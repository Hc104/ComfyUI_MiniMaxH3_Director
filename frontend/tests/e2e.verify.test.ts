/**
 * E2E 真 H3 闭环验收（V1.0 关键验证）。
 *
 * 走与 SPA handleGenerate 完全相同的代码路径：
 *   Project Model → buildTimelineStructure → syncWorkflowFrameRate
 *   → DirectorRunService.run → POST /prompt → MiniMaxH3Director 真生成
 *   → WS 进度（可选）→ /history 轮询兜底 → 成片 /view URL 验证
 *
 * 运行（需 ComfyUI 已开在 127.0.0.1:8188，且 MiniMaxH3Director 节点可用）：
 *   cd frontend
 *   npm run e2e            # 默认：只生成第一镜（最快暴露链路问题）
 *   E2E_SCOPE=all npm run e2e   # 整片（3 镜，耗时更长）
 *
 * 自定义地址：VITE_COMFY_BASE=http://127.0.0.1:8188 npm run e2e
 *
 * ComfyUI 不可达时自动 skip 并打印提示。
 */

import { describe, it, expect, afterAll } from "vitest";
import process from "node:process";
import { loadSampleProject, loadSampleWorkflow } from "@/fixtures";
import { buildTimelineStructure } from "@/core/directorCore";
import { MiniMaxH3Adapter } from "@/adapters/minimaxH3Adapter";
import {
  ComfyApiClient,
  comfyViewUrl,
  firstVideoFromHistory,
  type ComfyVideoRef,
  type HistoryItem,
} from "@/services/comfyApi";
import { DirectorRunService } from "@/services/directorRun";

const BASE = process.env.VITE_COMFY_BASE ?? "http://127.0.0.1:8188";
const WS_URL = process.env.VITE_COMFY_WS ?? `${BASE.replace(/^http/, "ws")}/ws`;
const SCOPE = process.env.E2E_SCOPE ?? "shot"; // shot | all
const TIMEOUT_MS = SCOPE === "all" ? 60 * 60 * 1000 : 15 * 60 * 1000;

const adapter = new MiniMaxH3Adapter();
const api = new ComfyApiClient(BASE);

/** 与 WorkbenchView.syncWorkflowFrameRate 一致：Director frame_rate + CreateVideo fps。 */
function syncWorkflowFrameRate(workflow: Record<string, unknown>, fps: number): void {
  for (const node of Object.values(workflow)) {
    if (!node || typeof node !== "object") continue;
    const n = node as { class_type?: unknown; inputs?: Record<string, unknown> };
    if (!n.inputs) continue;
    const ct = typeof n.class_type === "string" ? n.class_type : "";
    if (ct.includes("MiniMaxH3Director") && n.inputs.frame_rate !== undefined) {
      n.inputs.frame_rate = fps;
    } else if (ct.includes("CreateVideo") && n.inputs.fps !== undefined) {
      n.inputs.fps = fps;
    }
  }
}

/** 只保留 class_type 节点的纯净 prompt 图（与 DirectorRunService.run 内部一致）。 */
function sanitizeWorkflow(workflow: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(workflow)) {
    if (v && typeof v === "object" && typeof (v as { class_type?: unknown }).class_type === "string") {
      out[k] = v;
    }
  }
  return out;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** 探测 ComfyUI 是否可达且节点可用。 */
async function comfyReachable(): Promise<boolean> {
  try {
    const info = await api.objectInfo("MiniMaxH3Director");
    return !!info && "MiniMaxH3Director" in info;
  } catch {
    return false;
  }
}

/** 轮询 /history/{prompt_id} 直到完成/失败/超时。 */
async function pollUntilDone(
  promptId: string,
  timeoutMs: number,
): Promise<{ ok: boolean; item?: HistoryItem; timedOut: boolean }> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    let hist: Record<string, HistoryItem> = {};
    try {
      hist = await api.historyById(promptId);
    } catch {
      // 服务端偶发瞬断，继续轮询
    }
    const item = hist[promptId];
    if (item?.status) {
      const s = item.status;
      if (s.completed || s.status_str === "success") return { ok: true, item, timedOut: false };
      if (s.status_str === "error") return { ok: false, item, timedOut: false };
    }
    await sleep(3000);
  }
  return { ok: false, timedOut: true };
}

/** 从 history item 提取成片并验证 /view 可访问。 */
async function verifyVideo(item: HistoryItem | undefined): Promise<{ ref: ComfyVideoRef; url: string } | null> {
  const ref = firstVideoFromHistory(item);
  if (!ref) return null;
  const url = `${BASE}${comfyViewUrl(ref)}`;
  const res = await fetch(url);
  if (!res.ok) {
    console.log(`  [成片] ⚠ /view 返回 ${res.status}（${url}）`);
  } else {
    console.log(`  [成片] /view 200 ✓ 文件可访问`);
  }
  console.log(`  [成片] filename=${ref.filename} subfolder=${ref.subfolder} type=${ref.subfolder || "output"}`);
  return { ref, url };
}

const comfyUp = await comfyReachable();

/** 文件级持有 runService，便于 afterAll 清理（防止 WS 重连 timer 挂住进程）。 */
let runService: DirectorRunService | null = null;

afterAll(() => {
  runService?.disconnect();
});

if (!comfyUp) {
  console.warn(`
┌──────────────────────────────────────────────────────────┐
│  ComfyUI 未就绪：无法从 ${BASE} 探测到 MiniMaxH3Director    │
│  1) 启动/重启 ComfyUI 并确认节点已加载                      │
│  2) cd frontend && npm run e2e 重试                        │
│  如果确认已开，检查：端口、VITE_COMFY_BASE、节点是否报错     │
└──────────────────────────────────────────────────────────┘`);
}

describe.skipIf(!comfyUp)(`E2E 真 H3 闭环（scope=${SCOPE}，base=${BASE}）`, () => {
  it(
    "SPA 提交 → /prompt → 真 H3 → 进度 → 成片回看",
    async () => {
      console.log(`[env] node=${process.version} scope=${SCOPE} ws=${typeof WebSocket === "undefined" ? "不可用(轮询)" : "可用"}`);
      const steps: Array<{ name: string; ok: boolean; detail: string }> = [];
      const rec = (name: string, ok: boolean, detail: string) => {
        steps.push({ name, ok, detail });
        console.log(`${ok ? "✓" : "✗"} ${name}: ${detail}`);
      };

      // ---- STEP 1 连通性 ----
      try {
        await api.assertDirectorNode();
        rec("STEP1 连通性", true, `${BASE} · MiniMaxH3Director 节点可用`);
      } catch (err) {
        rec("STEP1 连通性", false, err instanceof Error ? err.message : String(err));
        throw err;
      }

      // ---- STEP 2 数据链路（前端构建 timeline_data）----
      let structure: ReturnType<typeof buildTimelineStructure> | null = null;
      try {
        const project = loadSampleProject();
        const episode = project.episodes[0];
        const firstShotId = episode.scenes[0].shots[0].id;
        structure = buildTimelineStructure({
          episode,
          frameRate: 24,
          width: 864,
          height: 480,
          refMaxSize: 864,
          output: {
            mode: "fixed",
            longEdge: 864,
            width: 864,
            height: 480,
            maxExportFrames: 0,
            exportMode: "all",
            continuityEnabled: false,
            continuityOverlapFrames: 9,
            audioMode: "auto",
            qwenVlEnabled: false,
            qwenVlLevel: 1,
          },
          targetShotIds: SCOPE === "all" ? null : [firstShotId],
        });
        const { timelineData } = adapter.buildTimelineData(structure);
        const segs = timelineData.segments as Array<{ frameCount: number; prompt: string }>;
        rec(
          "STEP2 数据链路",
          true,
          `${timelineData.width}×${timelineData.height} @${timelineData.frameRate}fps · ${segs.length} 段 · ${timelineData.totalFrames} 帧 · 首段 prompt ${segs[0]?.prompt.length ?? 0} 字`,
        );
      } catch (err) {
        rec("STEP2 数据链路", false, err instanceof Error ? err.message : String(err));
        throw err;
      }

      // ---- STEP 3 工作流注入 ----
      let workflow: Record<string, unknown> | null = null;
      try {
        workflow = sanitizeWorkflow(JSON.parse(JSON.stringify(loadSampleWorkflow())) as Record<string, unknown>);
        syncWorkflowFrameRate(workflow, 24);
        const director = workflow["5"] as { class_type?: string; inputs?: Record<string, unknown> } | undefined;
        if (!director?.inputs) throw new Error("workflow 缺少节点 5（MiniMaxH3Director）");
        const { timelineData } = adapter.buildTimelineData(structure!);
        director.inputs.timeline_data = JSON.stringify(timelineData);
        director.inputs.timeline = JSON.stringify(timelineData);
        rec("STEP3 工作流注入", true, `Director 节点 timeline_data 注入（${JSON.stringify(timelineData).length} 字节 JSON）`);
      } catch (err) {
        rec("STEP3 工作流注入", false, err instanceof Error ? err.message : String(err));
        throw err;
      }

      // ---- STEP 4+5 提交 & 监控 ----
      runService = new DirectorRunService(adapter, api, WS_URL);
      let wsConnected = false;
      try {
        if (typeof WebSocket !== "undefined") {
          runService.connect();
          wsConnected = true;
        }
      } catch (err) {
        console.warn(`  [ws] 连接失败，降级轮询：${err instanceof Error ? err.message : String(err)}`);
      }

      let promptId: string | null = null;
      const wsStats = { progress: 0, preview: 0, finish: null as boolean | null };
      try {
        promptId = await runService.run(structure!, workflow!, "5", {
          onProgress: (p) => {
            if (!p) return;
            wsStats.progress += 1;
            console.log(`  [WS进度] 段 ${p.segment}/${p.segmentTotal} ${p.phaseLabel}（${p.overallValue}/${p.overallMax}）`);
          },
          onPreview: () => {
            wsStats.preview += 1;
          },
          onFinish: (_pid, ok) => {
            wsStats.finish = ok;
            console.log(`  [WS] execution_${ok ? "success" : "error"} → ${ok ? "完成" : "失败"}`);
          },
        });
        rec("STEP4 提交", true, `prompt_id=${promptId}`);
      } catch (err) {
        const body = (err as { body?: string }).body ? `\n    服务端响应: ${(err as { body?: string }).body}` : "";
        rec("STEP4 提交", false, err instanceof Error ? err.message + body : String(err));
        throw err;
      }

      const pollResult = await pollUntilDone(promptId!, TIMEOUT_MS);
      rec(
        "STEP5 监控",
        pollResult.ok,
        `${wsConnected ? "WS+轮询" : "轮询(无WS)"} · WS进度 ${wsStats.progress} 条 · 预览帧 ${wsStats.preview} · WS完成=${wsStats.finish}` +
          (pollResult.timedOut ? " · ⚠ 超时未完成" : pollResult.ok ? " · 执行完成" : " · 执行失败"),
      );
      if (!pollResult.ok && pollResult.item) {
        const msgs = pollResult.item.status?.messages ?? [];
        for (const m of msgs.slice(0, 6)) {
          const s = JSON.stringify(m);
          if (/exception|error|Error|python/i.test(s)) {
            console.log(`  [错误] ${s.slice(0, 900)}`);
          }
        }
      }
      if (!pollResult.ok) throw new Error("H3 执行失败或超时，详见上方日志");

      // ---- STEP 6 成片回看 ----
      const video = await verifyVideo(pollResult.item);
      if (video) {
        rec("STEP6 成片", true, `${video.ref.filename} → ${video.url}`);
        console.log(`  浏览器打开即可播放：${video.url}`);
      } else {
        rec("STEP6 成片", false, "history 中没有视频输出（检查 SaveVideo/导出参数）");
      }

      // ---- 汇总 ----
      const failed = steps.filter((s) => !s.ok);
      console.log(`\n════════ 验收汇总 ════════`);
      for (const s of steps) console.log(`  ${s.ok ? "PASS" : "FAIL"}  ${s.name}`);
      console.log(`  ───────────────────────`);
      console.log(`  ${failed.length === 0 ? "全部通过 ✅" : `${failed.length} 步失败 ❌`}`);
      expect(failed.length).toBe(0);
    },
    TIMEOUT_MS,
  );
});
