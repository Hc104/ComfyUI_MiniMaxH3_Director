/**
 * MiniMaxH3Adapter v0 — MiniMax H3 模型适配器。
 *
 * 只做翻译：TimelineStructure → timeline_data JSON（r2v prompt_batch）。
 *
 * 与后端契约对齐（从现节点源码核实的权威白名单）：
 * - 顶层：version=5, timelineMode="prompt_batch", editMode="segment"
 * - 段白名单 22 字段：
 *   id, start, length, frameCount, durationSec, prompt, negativePrompt, taskType,
 *   refs, refAudios, refVideos, genImage, castId, locationId, castManual,
 *   locationManual, stateChange, smartTail, sceneId, continuityMode, consistencyCheck
 * - `durationSec` 是真相源；`start/length/frameCount` 由本适配器派生：
 *   frameCount = round(durationSec * fps) 后对齐 17k+5 网格（H3 采样网格），上限 512；
 *   start 为前段累计。
 * - 资产键统一 `locations`（复数）。
 */

import type { TimelineStructure, TimelineShot } from "@/models/timeline";
import type {
  BuildTimelineResult,
  BuildNodeWidgetsResult,
  DirectorAdapter,
  DirectorFinishEvent,
  DirectorPreviewEvent,
  DirectorProgressEvent,
} from "@/adapters/directorAdapter";

/** H3 采样帧数网格：17k+5（17, 39, 61, 83, …）。 */
export const MINIMAX_FRAME_GRID = (k: number): number => 17 * k + 5;
export const MINIMAX_MAX_FRAMES = 512;

/** 任务类型中文标签（后端 global.taskType 用完整中文）。 */
const TASK_LABELS: Record<string, string> = {
  r2v: "r2v — 参考主体生视频(Reference to Video)",
  t2v: "t2v — 文生视频(Text to Video)",
  i2v: "i2v — 图生视频(Image to Video)",
  fl2v: "fl2v — 首尾帧生视频(First&Last to Video)",
};

export class MiniMaxH3Adapter implements DirectorAdapter {
  readonly backend = "minimax-h3";
  readonly displayName = "MiniMax H3 (r2v prompt_batch)";

  buildTimelineData(structure: TimelineStructure): BuildTimelineResult {
    // ---- 派生段帧数：对齐 17k+5 网格 ----
    const shots = structure.shots.map((shot) => ({
      ...shot,
      frameCount: deriveFrameCount(shot.durationSec, structure.frameRate),
    }));

    // start 累计（后端按 start 排序分段；连续镜头相邻衔接）。
    let cursor = 0;
    const segs = shots.map((shot) => {
      const seg = toSegment(shot, structure, cursor);
      cursor += shot.frameCount;
      return seg;
    });

    const totalFrames = segs.reduce((acc, s) => acc + (s.frameCount ?? 0), 0);

    const timelineData: Record<string, unknown> = {
      version: 5,
      timelineMode: "prompt_batch",
      editMode: "segment",
      totalFrames,
      frameRate: structure.frameRate,
      width: structure.output.width,
      height: structure.output.height,
      refMaxSize: structure.refMaxSize,
      // scenes 块：场景素材组 + 默认资产。
      scenes: structure.scenes.map((s) => ({
        id: s.id,
        name: s.name,
        location: s.location,
        time: s.time,
        order: s.order,
        assets: {
          cast: s.assets.cast.map(toGlobalAsset),
          locations: s.assets.locations.map(toGlobalAsset),
          props: s.assets.props.map(toGlobalAsset),
          styles: s.assets.styles.map(toGlobalAsset),
        },
        defaultCastId: s.defaultCastId,
        defaultLocationId: s.defaultLocationId,
      })),
      // assets 块：全局角色 + 场景（locations 复数）。
      assets: {
        cast: structure.assets.cast.map(toGlobalAsset),
        locations: structure.assets.locations.map(toGlobalAsset),
      },
      global: {
        taskType: TASK_LABELS["r2v"] ?? "r2v",
        prompt: "", // 段级模式：global prompt 留空，逐段走 prompt。
        refs: [],
        refAudios: [],
        referenceVideo: {},
        continuousReference: false,
        genImage: { imageFile: "" },
      },
      output: {
        mode: structure.output.mode,
        longEdge: structure.output.longEdge,
        width: structure.output.width,
        height: structure.output.height,
        maxExportFrames: structure.output.maxExportFrames,
        exportMode: structure.output.exportMode,
        continuityEnabled: structure.output.continuityEnabled,
        continuityOverlapFrames: structure.output.continuityOverlapFrames,
        audioMode: structure.output.audioMode,
        qwenVlEnabled: structure.output.qwenVlEnabled,
        qwenVlLevel: structure.output.qwenVlLevel,
      },
      gen: { defaultFrameCount: 124 },
      segments: segs,
      runSelectEnabled: structure.runSelection != null,
      runSelection: structure.runSelection
        ? structure.runSelection
            .map((id) => structure.shots.findIndex((s) => s.id === id))
            .filter((i) => i >= 0)
        : [],
    };
    return { timelineData };
  }

  buildNodeWidgets(_structure: TimelineStructure): BuildNodeWidgetsResult {
    // V1：采样参数暂用节点默认（widgets_values 与示例工作流一致）。
    // 后续 V1.3 从 UI 读取原生 widget 值回填。
    // 顺序与 director.py INPUT_TYPES 的 widget 顺序对齐（含 bd_grp 分组标签）。
    return {
      widgets: [
        "r2v — 参考主体生视频(Reference to Video)", // taskType 下拉
        "", // global_prompt（段级模式留空）
        "采样设置", // bd_grp_sample 分组标签
        1.0, // cfg
        42, // seed
        "randomize", // control_after_generate
        24.0, // frame_rate
        864, // width
        480, // height
        864, // ref_max_size
        124, // total_frames（占位；真实值由 timeline 决定）
        "{}", // timeline_data 占位（真实值由调度层注入）
        "高级采样 Advanced", // bd_grp_advanced
        25, // steps
        "res_multistep", // sampler
        "simple", // scheduler
        12.0, // shift_video
        3.0, // shift_audio
        "性能 Performance", // bd_grp_perf
        true, // clear_vram_between_segments
        false, // export_source_images
      ],
    };
  }

  // ---- WS 事件翻译 ----

  parseProgressEvent(payload: Record<string, unknown>): DirectorProgressEvent | null {
    if (!payload || payload.node_id == null) return null;
    return {
      nodeId: String(payload.node_id),
      segment: Number(payload.segment ?? 1),
      segmentTotal: Number(payload.segment_total ?? 1),
      timelineSegment: Number(payload.timeline_segment ?? payload.segment ?? 1),
      timelineSegmentTotal: Number(payload.timeline_segment_total ?? payload.segment_total ?? 1),
      partialRun: Boolean(payload.partial_run),
      phase: String(payload.phase ?? ""),
      phaseLabel: String(payload.phase_label ?? payload.phase ?? ""),
      phaseValue: Number(payload.phase_value ?? 0),
      phaseMax: Number(payload.phase_max ?? 1),
      overallValue: Number(payload.overall_value ?? 0),
      overallMax: Number(payload.overall_max ?? 1),
      remainingSegments: Number(payload.remaining_segments ?? 0),
      framesLabel: String(payload.frames_label ?? ""),
      taskKey: String(payload.task_key ?? ""),
    };
  }

  parsePreviewEvent(payload: Record<string, unknown>): DirectorPreviewEvent | null {
    if (!payload || payload.node_id == null || payload.image_b64 == null) return null;
    return {
      nodeId: String(payload.node_id),
      segmentIndex: Number(payload.segment_index ?? 0),
      imageB64: String(payload.image_b64),
      width: Number(payload.width ?? 0),
      height: Number(payload.height ?? 0),
      frames: Array.isArray(payload.frames) ? (payload.frames as string[]) : undefined,
      fps: payload.fps != null ? Number(payload.fps) : undefined,
    };
  }

  parseFinishEvent(payload: Record<string, unknown>): DirectorFinishEvent | null {
    if (!payload || payload.node_id == null) return null;
    return {
      nodeId: String(payload.node_id),
      segmentTotal: Number(payload.segment_total ?? 0),
    };
  }
}

// ---------- 内部工具 ----------

/** 由镜头时长推导帧数（对齐 17k+5 网格，封顶 512）。导出供 ETA 预测复用。 */
export function deriveFrameCount(durationSec: number, fps: number): number {
  const raw = Math.max(1, Math.round(durationSec * fps));
  // 对齐 17k+5 网格：选最近的一个网格值，封顶 512。
  let best = 17 + 5; // k=1
  let bestDist = Infinity;
  for (let k = 1; ; k++) {
    const grid = MINIMAX_FRAME_GRID(k);
    const dist = Math.abs(grid - raw);
    if (dist < bestDist) {
      bestDist = dist;
      best = grid;
    } else if (grid > raw) {
      break;
    }
    if (grid >= MINIMAX_MAX_FRAMES) break;
  }
  return Math.min(best, MINIMAX_MAX_FRAMES);
}

function toSegment(shot: TimelineShot & { frameCount: number }, _structure: TimelineStructure, start: number) {
  return {
    id: shot.id,
    start,
    length: shot.frameCount,
    frameCount: shot.frameCount,
    durationSec: shot.durationSec,
    prompt: shot.prompt || "",
    negativePrompt: shot.negativePrompt || "",
    // taskType：空串（auto 归一化）→ 不写字段，后端段级继承全局 taskType（r2v）；
    // 显式模式 → 中文标签（后端 resolve_task_key 按前缀解析）。禁止写 "auto"（后端不识别）。
    ...(shot.taskKey
      ? { taskType: TASK_LABELS[shot.taskKey] ?? shot.taskKey }
      : {}),
    // refs 槽位 index 必须与前端 @ 补全的 refIndex（refImages 数组下标）一致：
    // @ 参考图 i → <Picture {i+1}> → 后端 ref_image_{i}。硬编码 0 会让多张参考图
    // 的 dict 键互相覆盖，只剩最后一张生效（#85 @ 引用参考图不出现）。
    refs: shot.refs.map((r, i) => ({ index: i, imageFile: r.imageFile, fileName: r.name })),
    refAudios: shot.refAudios.map((a, i) => ({ index: i, audioFile: a.audioFile, fileName: a.fileName ?? "" })),
    refVideos: shot.refVideos.map((v, i) => ({ index: i, videoFile: v.videoFile, fileName: v.fileName ?? "" })),
    genImage: shot.genImage,
    // V1.6-B：cast 集合走 castIds 数组；castId 保留首元素（旧后端/快照读单值）。
    // 防御性读取：旧 TimelineShot 结构可能没有 castIds 字段（castIds ?? []）。
    castId: (shot.castIds ?? [])[0] ?? shot.castId ?? "",
    castIds: shot.castIds ?? [],
    // P0-1 per-shot 资产边界：propIds/styleIds 透传（后端 _seg_id_list 解析）。
    // 旧 TimelineShot 无此字段时回退 []（不触发场景池扩散）。
    propIds: shot.propIds ?? [],
    styleIds: shot.styleIds ?? [],
    locationId: shot.locationId,
    castManual: shot.castManual,
    locationManual: shot.locationManual,
    stateChange: shot.stateChange,
    smartTail: shot.smartTail,
    sceneId: shot.sceneId,
    continuityMode: shot.continuityMode,
    consistencyCheck: shot.consistencyCheck,
  };
}

function toGlobalAsset(a: { id: string; name: string; kind: string; imageFile: string; description?: string; aliases?: string[] }) {
  return {
    id: a.id,
    name: a.name,
    kind: a.kind,
    imageFile: a.imageFile,
    ...(a.description ? { description: a.description } : {}),
    ...(a.aliases?.length ? { aliases: a.aliases } : {}),
  };
}
