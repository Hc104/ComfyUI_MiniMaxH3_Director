/**
 * V1.4-P0 错误定位单测。
 *
 * 覆盖：
 * 1. WS execution_error → onFinish(false, { nodeId, errorDetail, traceback })（真实异常不再丢弃）。
 * 2. WS 断开 + history error → 从 status.messages 提取异常 → onFinish(false, info)。
 * 3. 失败后 report 回填 → onReport(promptId, report)（/history outputs STRING 输出）。
 * 4. resolveFailedShotIds：段状态灯 failed ∪ 异常段号（1-based）双源并集去重。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { DirectorRunService } from "@/services/directorRun";
import { ComfyWsClient } from "@/services/comfyWs";
import type { ComfyApiClient } from "@/services/comfyApi";
import { MiniMaxH3Adapter } from "@/adapters/minimaxH3Adapter";
import type { TimelineStructure } from "@/models/timeline";
import { resolveFailedShotIds } from "@/core/failedShots";

const POLL_INTERVAL_MS = 3000;

function makeStructure(): TimelineStructure {
  return {
    scenes: [{ id: "s1", name: "场景1", assets: { cast: [], locations: [], props: [], styles: [] } }],
    shots: [
      {
        id: "sh1", sceneId: "s1", name: "镜头1", order: 0, durationSec: 5, taskType: "",
        continuityMode: "none",
        content: { visual: "雨夜霓虹街道，女孩撑透明伞缓行", cameraText: "", style: "", soundText: "" },
        refs: [], refAudios: [], refVideos: [], cast: [], location: [],
      },
    ],
    totalFrames: 120, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
    assets: { cast: [], locations: [] },
    output: {
      mode: "fixed", longEdge: 864, width: 864, height: 480, maxExportFrames: 0, exportMode: "all",
      continuityEnabled: false, continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
    },
  } as unknown as TimelineStructure;
}

function makeWorkflow(): Record<string, unknown> {
  return {
    "5": { class_type: "MiniMaxH3Director", inputs: { timeline_data: "{}", timeline: "{}", width: 864, height: 480 } },
    "9": { class_type: "VAELoader", inputs: {} },
  };
}

/** Stub API：内存 history + queue（沿用 directorRunFallback.test 的形态）。 */
function makeStubApi() {
  let history: Record<
    string,
    { status?: { status_str?: string; completed?: boolean; messages?: Array<[string, Record<string, unknown>]> }; outputs?: Record<string, unknown> }
  > = {};
  let queue: { queue_running: Array<[string, number, unknown]>; queue_pending: Array<[string, number, unknown]> } = {
    queue_running: [],
    queue_pending: [],
  };

  const api = {
    historyById: vi.fn(async (pid: string) => (history[pid] ? { [pid]: history[pid] } : {})),
    queue: vi.fn(async () => queue),
    submitPrompt: vi.fn(async () => ({ prompt_id: "test-prompt-1" })),
    segmentStatus: vi.fn(async () => ({ cached: [], states: {}, indexShotIds: [] })),
    objectInfo: vi.fn(async () => ({})),
    assertDirectorNode: vi.fn(async () => {}),
    history: vi.fn(async () => ({})),
    interrupt: vi.fn(async () => {}),
    deletePending: vi.fn(async () => {}),
    segmentCacheStatus: vi.fn(async () => ({ cached: [], states: {}, indexShotIds: [] })),
    segmentMp4: vi.fn(async () => ({ blob: new Blob(), filename: "Shot01.mp4" })),
    listProjects: vi.fn(async () => []),
    listSnapshots: vi.fn(async () => []),
  } as unknown as ComfyApiClient;

  return { api, setHistory: (h: typeof history) => (history = h), setQueue: (q: typeof queue) => (queue = q) };
}

/** Stub WS：手动派发事件（沿用 directorRunFallback.test 的形态）。 */
function makeStubWs() {
  const handlers: Record<string, (data: unknown) => void> = {};
  const ws = {
    connect: vi.fn(),
    disconnect: vi.fn(),
    _setHandlers: (h: Record<string, (data: unknown) => void>) => Object.assign(handlers, h),
  } as unknown as ComfyWsClient;
  return ws;
}

function newRun(api: ComfyApiClient, ws: ComfyWsClient) {
  const svc = new DirectorRunService(new MiniMaxH3Adapter(), api, "ws://stub");
  (svc as unknown as { ws: ComfyWsClient }).ws = ws;
  return svc;
}

/** 取 svc 内部事件派发口（箭头包装保 this 绑定，同 directorRunFallback.test 的写法）。 */
function evSink(svc: DirectorRunService): (ev: { type: string; data: Record<string, unknown> }) => void {
  const anySvc = svc as unknown as { handleWsEvent(ev: { type: string; data: Record<string, unknown> }): void };
  return (ev) => anySvc.handleWsEvent(ev);
}

describe("V1.4-P0 错误定位", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("WS execution_error → onFinish(false, { nodeId, errorDetail, traceback })", async () => {
    const { api } = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(api, ws);
    const finish = vi.fn();

    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });

    evSink(svc)({
      type: "execution_error",
      data: {
        prompt_id: "test-prompt-1",
        node_id: 5,
        exception_type: "ValueError",
        exception_message: "段间连贯：片段 #2 需要上一段 #1 的生成结果。",
        traceback: "Traceback (most recent call last):\n...",
      },
    });

    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("test-prompt-1", false, {
      nodeId: "5",
      errorDetail: "段间连贯：片段 #2 需要上一段 #1 的生成结果。",
      traceback: "Traceback (most recent call last):\n...",
    });
  });

  it("WS 断开 + history status.messages 含 execution_error → onFinish(false, info)", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();

    stub.setHistory({
      "test-prompt-1": {
        status: {
          status_str: "error",
          messages: [
            ["execution_error", { node_id: 5, exception_message: "CUDA out of memory", traceback: "tb" }],
          ],
        },
      },
    });

    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 1.5);

    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("test-prompt-1", false, {
      nodeId: "5",
      errorDetail: "CUDA out of memory",
      traceback: "tb",
    });
  });

  it("失败后 report 从 /history outputs STRING 输出回填 → onReport(promptId, report)", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();
    const report = vi.fn();

    stub.setHistory({
      "test-prompt-1": {
        status: { status_str: "error" },
        outputs: { "5": { text: ["导演诊断：Qwen 未检测到异常；VRAM 峰值 8.1GB。"] } },
      },
    });

    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish, onReport: report });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 1.5);

    expect(finish).toHaveBeenCalledTimes(1);
    // backfillReportFromHistory 是异步 fire-and-forget，flush 微任务。
    await vi.runAllTimersAsync();
    expect(report).toHaveBeenCalledWith("test-prompt-1", "导演诊断：Qwen 未检测到异常；VRAM 峰值 8.1GB。");
  });

  it("陈旧 report 事件不污染新任务（promptId 不匹配则忽略）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const report = vi.fn();

    // history 只有旧 prompt（与当前 pending "test-prompt-1" 不同）。
    stub.setHistory({
      "old-prompt": { outputs: { "5": { text: ["旧诊断"] } } },
    });

    await svc.run(makeStructure(), makeWorkflow(), "5", { onReport: report });
    await vi.runAllTimersAsync();
    expect(report).not.toHaveBeenCalled();
  });

  describe("resolveFailedShotIds 双源并集", () => {
    const shotOrder = ["sh1", "sh2", "sh3"];

    it("段状态灯 failed → 索引映射 shotOrder", () => {
      const ids = resolveFailedShotIds(
        shotOrder,
        null,
        { indexShotIds: shotOrder, cached: [], states: { "0": "failed" } },
      );
      expect(ids).toEqual(["sh1"]);
    });

    it("异常信息段号（1-based）→ shotOrder[N-1]", () => {
      const ids = resolveFailedShotIds(shotOrder, "段间连贯：片段 #2 需要上一段 #1 的生成结果。", null);
      expect(ids).toEqual(["sh2"]);
      // Segment 英文形式
      expect(resolveFailedShotIds(shotOrder, "Segment 3 cache stale; re-run this segment.", null)).toEqual(["sh3"]);
    });

    it("#93 回归：plan 坏图错误带段号 → 定位真实镜头（s2 坏图不再错标 s1）", () => {
      // gen_timeline/plan 段循环包 try/except 后抛出的错误信息格式。
      const err = "Segment 2 reference image could not be loaded: cannot identify image file '...新建 文本文档.png'";
      // 无残留 segStatus（新 run 已清空 runtime states）→ 仅 errorDetail 源。
      expect(resolveFailedShotIds(shotOrder, err, null)).toEqual(["sh2"]);
      expect(
        resolveFailedShotIds(shotOrder, err, { indexShotIds: shotOrder, cached: [], states: {} }),
      ).toEqual(["sh2"]);
      // 错误里出现「上一段 #1」这类前驱引用时不应误报成 s1（只匹配失败段号 2）。
      const errWithPrev = "Segment 2 reference image could not be loaded; 上一段 #1 需先生成。";
      expect(resolveFailedShotIds(shotOrder, errWithPrev, null)).toEqual(["sh2"]);
    });

    it("双源并集去重", () => {
      const ids = resolveFailedShotIds(
        shotOrder,
        "片段 #2 需要上一段 #1 的生成结果。",
        { indexShotIds: shotOrder, cached: [], states: { "0": "failed", "1": "failed" } },
      );
      expect(ids).toEqual(["sh1", "sh2"]);
    });

    it("无任何信息 → 空数组", () => {
      expect(resolveFailedShotIds(shotOrder, null, null)).toEqual([]);
      expect(resolveFailedShotIds(shotOrder, undefined, undefined)).toEqual([]);
    });
  });
});
