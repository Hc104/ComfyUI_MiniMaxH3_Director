/**
 * DirectorCore — 模型无关的导演核心（v1.1 分层）。
 *
 * 职责：把 Project Model（ShotModel）转成 TimelineStructure，把渲染范围算成 RenderPlan。
 * 它**不知道** H3 字段（integrated_multimodal_description / prompt_batch / fl2v…），
 * 只输出语义结构，具体翻译交给 Adapter。
 *
 * V1 只实现「纯函数组装」：Scene/Shot → TimelineScene/TimelineShot（字段搬运 + 继承），
 * 资产解析/@匹配/继承可视化 等完整逻辑 V1.5。
 */

import type { Episode, Scene, Shot, ShotGeneration } from "@/models/project";
import type { TimelineStructure, TimelineScene, TimelineShot, TaskKey } from "@/models/timeline";
import type { RenderPlan } from "@/models/render";
import { buildShotPromptText } from "@/core/promptSections";
import { shotCastIds } from "@/core/inheritanceResolver";

export interface BuildStructureInput {
  episode: Episode;
  frameRate: number;
  width: number;
  height: number;
  refMaxSize: number;
  output: TimelineStructure["output"];
  /** 生成范围：null=全部，否则镜头 id 列表。 */
  targetShotIds: string[] | null;
  /** P0-A（#479）：{shotId: 三段式提交文本}。有值镜头用该文本替代 buildShotPromptText
   *  （后端 /prompt/h3 实时重建的三段式），缺省镜头回退分区合并。 */
  promptOverride?: Record<string, string>;
}

/** 从集级模型构建 TimelineStructure（不含任何 H3 字段）。 */
export function buildTimelineStructure(input: BuildStructureInput): TimelineStructure {
  const { episode, frameRate, width, height, refMaxSize, output, targetShotIds, promptOverride } = input;

  const scenes: TimelineScene[] = episode.scenes.map((s) => ({
    id: s.id,
    name: s.name,
    location: s.location ?? "",
    time: s.time ?? "",
    order: s.order,
    assets: s.assets ?? { cast: [], locations: [], props: [], styles: [] },
    defaultCastId: s.defaultCastId ?? "",
    defaultLocationId: s.defaultLocationId ?? "",
  }));

  // P0 Story Timeline（2026-08-14 用户拍板）：镜头按 episode.timeline（播放顺序）展开，
  // 同一 Scene 可被交叉引用多次（场景树只是空间/资产容器，不决定播放顺序）。
  const sceneById = new Map(episode.scenes.map((s) => [s.id, s]));
  const shots: TimelineShot[] = resolveFlattenedShots(episode).map((shot) =>
    toTimelineShot(
      shot,
      sceneById.get(shot.sceneId) ?? episode.scenes[0],
      episode,
      promptOverride,
    ),
  );

  return {
    mode: "prompt_batch",
    frameRate,
    width,
    height,
    refMaxSize,
    assets: {
      cast: episode.assets?.cast ?? [],
      locations: episode.assets?.locations ?? [],
    },
    scenes,
    shots,
    output,
    runSelection: targetShotIds,
  };
}

/** 渲染范围解析 → RenderPlan（范围语义由 UI 层定 targetShotIds）。 */
export function buildRenderPlan(
  structure: TimelineStructure,
  targetShotIds: string[] | null,
): RenderPlan {
  return {
    mode: structure.output.mode,
    width: structure.output.width,
    height: structure.output.height,
    refMaxSize: structure.refMaxSize,
    maxExportFrames: structure.output.maxExportFrames,
    exportMode: structure.output.exportMode,
    continuityEnabled: structure.output.continuityEnabled,
    continuityOverlapFrames: structure.output.continuityOverlapFrames,
    targetShotIds,
  };
}

// ---------- 内部：Shot → TimelineShot ----------

/**
 * P0 Story Timeline（2026-08-14 用户拍板）：Episode → 拍平镜头列表（播放顺序）。
 *  - episode.timeline 存在 → 按 timeline 显式顺序展开（同一 Scene 可被交叉引用多次），
 *    未覆盖镜头按场景树顺序补齐（镜头增删后 timeline 可能缺条目，保证不丢镜）；
 *  - episode.timeline 缺省/空 → 场景树顺序（scene.order 升序 → shot.order 升序，
 *    严格向后兼容：旧项目/手编项目行为不变）。
 *
 * 导出供 Workbench 时间线 UI（P0-7）复用：Storyboard 板块按同一拍平顺序渲染，
 * 保证 UI 显示与生成执行顺序一致。
 */
export function resolveFlattenedShots(episode: Episode): Shot[] {
  const timeline = episode.timeline ?? [];
  if (timeline.length) {
    const byId = new Map<string, Shot>();
    for (const scene of episode.scenes) {
      for (const shot of scene.shots) byId.set(shot.id, shot);
    }
    const out: Shot[] = [];
    const seen = new Set<string>();
    for (const id of timeline) {
      const shot = byId.get(id);
      if (shot && !seen.has(id)) {
        seen.add(id);
        out.push(shot);
      }
    }
    for (const scene of episode.scenes) {
      for (const shot of scene.shots.slice().sort((a, b) => a.order - b.order)) {
        if (!seen.has(shot.id)) {
          seen.add(shot.id);
          out.push(shot);
        }
      }
    }
    return out;
  }
  return episode.scenes.flatMap((scene) =>
    scene.shots.slice().sort((a, b) => a.order - b.order),
  );
}

/**
 * 本镜角色 id 集合（V1.6-B）：显式 castIds → 场景默认 → 全局首个。
 * 与 resolveCollection("cast") 语义一致；@命中由后端 gen_timeline 并入去重。
 */
function resolveShotCastIds(shot: Shot, scene: Scene, episode: Episode): string[] {
  const explicit = shotCastIds(shot);
  if (explicit.length) return explicit;
  if (scene.defaultCastId) return [scene.defaultCastId];
  const first = episode.assets?.cast?.[0];
  return first ? [first.id] : [];
}

function toTimelineShot(shot: Shot, scene: Scene, episode: Episode, promptOverride?: Record<string, string>): TimelineShot {
  const gen: ShotGeneration = shot.generation ?? {
    taskType: "r2v",
    continuityMode: "auto",
    smartTail: false,
    stateChange: "",
    consistencyCheck: "auto",
  };
  const refs = shot.refs?.refImages ?? [];
  const castIds = resolveShotCastIds(shot, scene, episode);
  return {
    id: shot.id,
    sceneId: scene.id,
    durationSec: shot.durationSec,
    // prompt = 提交文本。P0-A（#479）：有实时重建的三段式 → 用它（导演链最终产物）；
    // 否则回退分区合并结果（画面 + 摄影 + 风格 + 声音，按输入语言拼接）。
    prompt: promptOverride?.[shot.id] ?? buildShotPromptText(shot),
    negativePrompt: shot.content.negativePrompt ?? "",
    // 生成模式：auto=跟随全局（后端段级继承全局 taskType，通常 r2v）。
    // 直接写 "auto" 后端不识别（SUPPORTED_TASK_KEYS 不含 auto），故归一化为空串；
    // Adapter 对空串不写段 taskType 字段。对齐原生前端 `if (sel.value === "auto") delete seg.taskType`。
    taskKey: (gen.taskType === "auto" ? "" : gen.taskType) as TaskKey,
    refs: refs.map((r, i) => ({
      id: String(i),
      name: r.fileName ?? r.imageFile,
      kind: "cast",
      imageFile: r.imageFile,
    })),
    refAudios: shot.refs?.refAudios ?? [],
    refVideos: shot.refs?.refVideos ?? [],
    genImage: shot.refs?.genImage ?? { imageFile: "" },
    castId: castIds[0] ?? "",
    castIds,
    // P0-1 per-shot 资产边界：本镜显式道具/风格引用 id 透传（后端按 id 从场景池检出，
    // 不再全场景扩散）。
    propIds: shot.propIds ?? [],
    styleIds: shot.styleIds ?? [],
    locationId: shot.locationId ?? scene.defaultLocationId ?? episode.assets?.locations[0]?.id ?? "",
    castManual: shot.castManual ?? false,
    locationManual: shot.locationManual ?? false,
    stateChange: gen.stateChange,
    smartTail: gen.smartTail,
    continuityMode: gen.continuityMode as TimelineShot["continuityMode"],
    consistencyCheck: gen.consistencyCheck,
  };
}
