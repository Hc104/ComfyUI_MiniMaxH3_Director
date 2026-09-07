/**
 * Workbench 状态（V1 最小集）：当前项目/场景/镜头 + 生成运行态。
 * 用 Vue reactive 管理，不引入 Pinia（V1 依赖少）。
 */
import { reactive } from "vue";
import type {
  Asset,
  AssetKind,
  Episode,
  Project,
  Scene,
  SceneAssets,
  Shot,
  ShotGeneration,
  ShotGenRecord,
  RefImage,
  RefAudio,
  RefVideo,
  VoiceCastConfig,
  VoiceTypeId,
} from "@/models/project";
import { MiniMaxH3Adapter } from "@/adapters/minimaxH3Adapter";
import type { DirectorAdapter } from "@/adapters/directorAdapter";
import { DirectorRunService } from "@/services/directorRun";
import type { ExportOptions, VisualStyleProfileJson } from "@/services/comfyApi";
import { FPS_MAX, FPS_MIN, alignDim, resolutionFor, resolutionForMp } from "@/workbench/resolutionPresets";
import { shotCastIds } from "@/core/inheritanceResolver";
import {
  normalizeStyleProfile as normalizeStyleProfileJson,
  DEFAULT_STYLE_PROFILE_JSON,
} from "@/core/visualStyle";
import type { GenMeta } from "@/core/generationEta";

interface WorkbenchState {
  project: Project | null;
  currentEpisodeId: string | null;
  currentSceneId: string | null;
  currentShotId: string | null;
  /** 右侧面板模式：shot=镜头设置 / scene=场景设置（V1.2）。 */
  currentPane: "shot" | "scene";
  adapter: DirectorAdapter;
  runService: DirectorRunService;
  /** WS 连接状态（V1.4-P1）：disconnected=未连接 / connected=已连接 / reconnecting=断开重连中 / recovered=刚恢复。 */
  wsStatus: WsStatus;
  /** 最近一次进度（转成 UI 友好形状）。 */
  progress: {
    segment: number;
    segmentTotal: number;
    phaseLabel: string;
    overallValue: number;
    overallMax: number;
    framesLabel: string;
    /** V1.10-E 真实阶段步进：后端 phase_value（Sampling step 等）。无则不伪造。 */
    phaseValue?: number;
    /** V1.10-E 真实阶段总步数（phase_max）。>1 才显示 Step v/max。 */
    phaseMax?: number;
  } | null;
  /** 最近预览帧（dataURL 或 b64 前缀）。 */
  preview: string | null;
  /** 成片回看：生成结束后 SaveVideo 输出的最终视频。 */
  finalVideo: { url: string; filename: string } | null;
  /** 输出分辨率（工作台会话级，V1 UI 可改）。 */
  outputSize: { width: number; height: number };
  /** 当前比例（'custom' = 自定义手动宽高）。 */
  ratioId: string;
  /** 当前百万像素档位（自定义比例时为 null）。 */
  mpTier: number | null;
  /** 输出帧率（默认 24）。 */
  frameRate: number;
  running: boolean;
  lastPromptId: string | null;
  lastError: string | null;
  /** 生成范围（V1.3 六态：全部/当前镜/选中镜/当前场景/未完成/失败）。 */
  genScope: GenScope;
  /** 多选镜头集合（「选中镜头」范围用）。 */
  selectedShotIds: string[];
  /** 最近一次 segment_status：镜头 id 顺序快照 + 缓存索引 + 瞬态状态。 */
  segStatus: SegStatus | null;
  /** 任务历史（V1.3 任务中心）：最新在前。 */
  tasks: TaskRecord[];
  /** 当前运行任务 id（无运行任务为 null）。 */
  currentTaskId: string | null;
  /** 各镜头「生成成功时的参数指纹」（shotId → fingerprint，V1.3-D 参数已变检测）。 */
  paramHashes: Record<string, string>;
  /** 有未保存修改（V1.4-P0-2 Dirty 状态）：任一编辑原子操作置 true，保存成功后置 false。 */
  dirty: boolean;
  /** 最近一次保存时间戳（未保存过为 null）。 */
  lastSavedAt: number | null;
}

/** 生成范围（顶栏下拉）。fromShot=从此镜继续（当前镜 + 后续全部，走查续拍用）。 */
export type GenScope = "all" | "current" | "selected" | "scene" | "pending" | "failed" | "fromShot";

/** WS 连接状态（V1.4-P1）：disconnected=未连接 / connected=已连接 / reconnecting=断开重连中 / recovered=刚恢复。 */
export type WsStatus = "disconnected" | "connected" | "reconnecting" | "recovered";

/** 段状态（POST /minimax/director/segment_status 的 SPA 侧镜像 + 镜头顺序快照）。 */
export interface SegStatus {
  /** 全片拍平后的镜头 id 顺序（段索引 = 该数组下标，与 structure.shots 同序）。 */
  indexShotIds: string[];
  /** 已有磁盘缓存（成功）的段索引。 */
  cached: number[];
  /** 瞬态状态 { 段索引: "running"|"review"|"failed" }。 */
  states: Record<string, string>;
}

/** 任务中心记录（V1.3-C）：一次生成 = 一条任务。 */
export interface TaskRecord {
  id: string;
  /** 范围摘要（如「全部镜头（3）」「当前镜头（1）」）。 */
  scopeLabel: string;
  /** 本任务目标镜头数。 */
  shotCount: number;
  /** 生成时刻全片拍平镜头 id 顺序（段索引 = 下标，固化防止项目增删后错位）。 */
  shotOrder: string[];
  /** 实际提交的镜头 id（null=全部）。 */
  targetShotIds: string[] | null;
  /** 逐镜串行生成：当前正在生成第几镜（0-based；null=非逐镜/单次全片合并）。 */
  currentIndex: number | null;
  /** 走查队列每镜状态（V1.5 #104 升级）：{ 镜头id: "pending"|"running"|"done"|"failed"|"cancelled" }；null=非走查模式。 */
  shotStates: Record<string, string> | null;
  promptId: string | null;
  status: "running" | "done" | "failed" | "cancelled";
  startedAt: number;
  finishedAt: number | null;
  error: string | null;
  /** 完整后端异常信息（execution_error.exception_message），详情面板直接展示（V1.4-P0）。 */
  errorDetail: string | null;
  /** ComfyUI 执行失败节点 id（execution_error.node_id），详情面板可查（V1.4-P0）。 */
  nodeId: string | null;
  /** 后端 Director report（节点第 6 输出，经 /history 取回），详情面板折叠展示（V1.4-P0）。 */
  report: string | null;
  /** 失败镜头 id 列表（segmentStatus failed ∪ 异常信息里解析出的段号 → shotOrder，去重）（V1.4-P0）。 */
  failedShotIds: string[];
  /** 阶段进度（与 workbench.progress 同构，任务内快照）。 */
  progress: WorkbenchState["progress"];
  /** 预览帧（dataURL）。 */
  preview: string | null;
  /** 成片回看（SaveVideo 输出）。 */
  finalVideo: { url: string; filename: string } | null;
  /** 任务类型（V1.6-A）：generate=镜头生成 / export=成片导出。 */
  kind: "generate" | "export";
  /** 导出任务进度（V1.6-A）：当前编码镜头号（1-based）+ 总镜头数；null=非导出任务/未开始。
   *   encoding 达到 total 后进入「合并成片」阶段（客户端估算，后端 /export 不流式回传进度）。 */
  exportStage: { encoding: number; total: number } | null;
  /** 导出产物文件名（成片 mp4，V1.6-A）。 */
  exportFile: string | null;
  /** V1.8-3 ETA：任务生成参数快照 + 逐镜预测（生成前算好，供任务中心渲染 ETA）。 */
  genMeta?: GenMeta;
}

const adapter = new MiniMaxH3Adapter();

const state = reactive<WorkbenchState>({
  project: null,
  currentEpisodeId: null,
  currentSceneId: null,
  currentShotId: null,
  currentPane: "shot",
  adapter,
  runService: new DirectorRunService(adapter, undefined, undefined, handleWsStatus),
  wsStatus: "disconnected",
  progress: null,
  preview: null,
  finalVideo: null,
  // 默认沿用节点原生 864×480（0.4MP 16:9）；该尺寸不在预设矩阵里 → 以「自定义」呈现。
  outputSize: { width: 864, height: 480 },
  ratioId: "custom",
  mpTier: null,
  frameRate: 24,
  running: false,
  lastPromptId: null,
  lastError: null,
  genScope: "all",
  selectedShotIds: [],
  segStatus: null,
  tasks: [],
  currentTaskId: null,
  paramHashes: {},
  dirty: false,
  lastSavedAt: null,
});

/** 内部脏标记：任一编辑原子操作真实改到项目树后调用（V1.4-P0-2）。 */
function touch(): void {
  state.dirty = true;
}

/** 外部手动置脏（WorkbenchView 等直接改项目树又不在原子操作内的兜底）。 */
export function markDirty(): void {
  touch();
}

/** 保存成功后置干净，记录保存时间。 */
export function markSaved(): void {
  state.dirty = false;
  state.lastSavedAt = Date.now();
}

/**
 * Q3 修复（#98）+ V1.6-B 迁移：清理历史镜头里「id 有值但 manual 为假」的残留继承字段，
 * 并把旧单值 castId 迁移进 castIds 数组。
 *
 * 背景：旧版 addShot 直接把场景默认值预写进 castId/locationId 且不设 manual，
 * 导致 resolveOne 的 `defaultId === explicitId` 把「场景默认继承」误判成「⚡镜头指定」，
 * @命中分支永远走不到。现在 addShot 不再预写，这里兜底清理已保存项目中的旧数据。
 *
 * V1.6-B：cast 从单选升级为集合。旧项目 `castId: "x" + castManual:true` →
 * `castIds: ["x"]`（castId 清空）；「id 有值 + manual 假」残留 castIds 一并清掉。
 *
 * 安全边界：UI 原子操作里 `castIds 有值 ⟺ manual=true`（updateShotCast/setShotCasts），
 * 导入通道也设 castManual=true（importer 断言）。所以「id 有值 + manual 假」只可能
 * 来自旧 addShot 预写，可安全清掉；显式选择与导入数据不受影响。
 * 幂等：对已清理过的项目再跑无副作用。
 */
export function normalizeShotInheritance(project: Project): void {
  for (const ep of project.episodes) {
    for (const sc of ep.scenes) {
      for (const shot of sc.shots) {
        // V1.6-B 迁移：旧单值 castId → castIds 数组（显式选择带 manual=true 才迁移）。
        if (shot.castId && !shot.castIds?.length) {
          if (shot.castManual) {
            shot.castIds = [shot.castId];
          }
          shot.castId = undefined;
        }
        if (shot.castIds?.length && !shot.castManual) shot.castIds = undefined;
        if (shot.locationId && !shot.locationManual) shot.locationId = undefined;
      }
    }
  }
}

/**
 * V1.11 生成历史状态兜底（#453）：项目文件里 activeGenerationId 可能指向不存在的
 * 版本（外部编辑 / generations 被部分清理 / 旧数据损坏）→ 加载后 UI 信息卡会显示
 * 一个不存在的「⭐ 当前成片」。兜底规则：
 *   - 无 generations → 清空 activeGenerationId；
 *   - activeGenerationId 不在 generations 里 → 回退到最后一条生成（等价「最新」）；
 *   - 否则保留（用户选的版本就是当前成片）。
 * 幂等：对健康项目无副作用；不新增/删除 generations，不改变引用（#455 隔离契约不受影响）。
 */
export function normalizeGenerationState(project: Project): void {
  for (const ep of project.episodes) {
    for (const sc of ep.scenes) {
      for (const shot of sc.shots) {
        const arr = shot.generations;
        if (!arr?.length) {
          shot.activeGenerationId = undefined;
          continue;
        }
        if (shot.activeGenerationId && !arr.some((g) => g.id === shot.activeGenerationId)) {
          shot.activeGenerationId = arr[arr.length - 1].id;
        }
      }
    }
  }
}

/**
 * Phase 2（#573）：Voice Cast 全局音色映射（Project.voiceCast）兜底。
 * 缺省 → 默认配置；已有配置补全缺省字段；entries 防脏。
 * 幂等：对健康项目无副作用。载入项目时调用，保证面板总有可读写对象。
 */
export const DEFAULT_VOICE_CAST: VoiceCastConfig = {
  enabled: false,
  entries: {},
  narratorVoice: "voice_narrator",
  systemVoice: "voice_system",
  systemElectronic: "voice_system_electronic",
};

export function normalizeVoiceCast(project: Project): void {
  if (!project.voiceCast) {
    project.voiceCast = { ...DEFAULT_VOICE_CAST };
    return;
  }
  const vc = project.voiceCast;
  vc.enabled = vc.enabled ?? DEFAULT_VOICE_CAST.enabled;
  vc.narratorVoice = vc.narratorVoice || DEFAULT_VOICE_CAST.narratorVoice;
  vc.systemVoice = vc.systemVoice || DEFAULT_VOICE_CAST.systemVoice;
  vc.systemElectronic = vc.systemElectronic || DEFAULT_VOICE_CAST.systemElectronic;
  if (!vc.entries || typeof vc.entries !== "object") {
    vc.entries = {};
  }
}

/** 载入项目（V1：从 fixture 导入；真实项目文件存取 V1.1）。 */
export function loadProject(project: Project): void {
  normalizeShotInheritance(project);
  normalizeGenerationState(project);
  normalizeVoiceCast(project);
  normalizeStyleProfile(project);
  state.project = project;
  // V1.11.3：载入项目 = 全新会话。先无条件复位项目级瞬态：
  //   ① 清空上一个项目的生成任务/成片/预览/最近错误 → 底部任务中心不再残留旧镜头与旧任务；
  //   ② 复位选择游标（当前集/场景/镜头/面板）→ 空项目（无 scenes）也不会沿用旧项目的选中态。
  state.currentEpisodeId = null;
  state.currentSceneId = null;
  state.currentShotId = null;
  state.currentPane = "shot";
  state.finalVideo = null;
  state.preview = null;
  state.genScope = "all";
  state.selectedShotIds.splice(0);
  state.segStatus = null;
  state.tasks.splice(0);
  state.currentTaskId = null;
  state.lastError = null;
  // V1.3-D 参数指纹持久化：从项目文件恢复（重开项目后仍能判 st-stale）。
  state.paramHashes = { ...(project.fingerprints ?? {}) };
  // V1.4-P0-2：载入项目 = 与磁盘一致基线，未保存标记复位。
  state.dirty = false;
  state.lastSavedAt = null;
  const ep = project.episodes[0];
  if (!ep) return;
  state.currentEpisodeId = ep.id;
  const sc = ep.scenes[0];
  if (!sc) return;
  state.currentSceneId = sc.id;
  state.currentShotId = sc.shots[0]?.id ?? null;
  state.currentPane = state.currentShotId ? "shot" : "scene";
}

/** 清空当前项目（回项目选择页）。 */
export function clearProject(): void {
  state.project = null;
  state.currentEpisodeId = null;
  state.currentSceneId = null;
  state.currentShotId = null;
  state.currentPane = "shot";
  state.finalVideo = null;
  state.preview = null;
  state.genScope = "all";
  state.selectedShotIds.splice(0);
  state.segStatus = null;
  state.tasks.splice(0);
  state.currentTaskId = null;
  state.paramHashes = {};
  state.dirty = false;
  state.lastSavedAt = null;
}

/** V1.6-C：项目改名（首页卡片 inline）。trim 后写回 + touch 置脏。 */
export function renameProject(name: string): void {
  if (state.project && name.trim()) {
    state.project.name = name.trim();
    touch();
  }
}

/** V1.6-C：集标题改名（工作台顶栏 inline）。trim 后写回 + touch 置脏。 */
export function renameEpisode(title: string): void {
  const ep = getCurrentEpisode();
  if (ep && title.trim()) {
    ep.title = title.trim();
    touch();
  }
}

export function getCurrentEpisode(): ReturnType<() => Project["episodes"][number] | undefined> {
  return state.project?.episodes.find((e) => e.id === state.currentEpisodeId) ?? state.project?.episodes[0];
}

export function getCurrentScene(): Scene | undefined {
  const ep = getCurrentEpisode();
  return ep?.scenes.find((s) => s.id === state.currentSceneId) ?? ep?.scenes[0];
}

export function getCurrentShot(): Shot | undefined {
  const sc = getCurrentScene();
  return sc?.shots.find((s) => s.id === state.currentShotId) ?? sc?.shots[0];
}

export function selectScene(sceneId: string): void {
  state.currentSceneId = sceneId;
  const sc = getCurrentScene();
  state.currentShotId = sc?.shots[0]?.id ?? null;
  state.currentPane = "shot";
}

/** 选中场景并打开「场景设置」面板（不跳到镜头）。 */
export function selectSceneSettings(sceneId: string): void {
  state.currentSceneId = sceneId;
  state.currentPane = "scene";
}

export function selectShot(shotId: string): void {
  // P0-7（#519）：点击全片时间线上其它场景的镜头时，同步 currentSceneId 到该镜所在场景。
  // 否则 getCurrentShot 只在「当前场景」内 find 该镜，找不到 → 回退当前场景首镜，
  // 详情区/主播放器显示错镜（旧时间线只展示当前场景镜头，无此问题）。
  const ep = getCurrentEpisode();
  if (ep) {
    const sc = ep.scenes.find((s) => s.id === state.currentSceneId) ?? ep.scenes[0];
    if (sc && !sc.shots.some((s) => s.id === shotId)) {
      const owner = ep.scenes.find((s) => s.shots.some((sh) => sh.id === shotId));
      if (owner) state.currentSceneId = owner.id;
    }
  }
  state.currentShotId = shotId;
  if (shotId) state.currentPane = "shot";
}

// ---------- V1.4-P1 WS 连接状态（三/四态圆点）----------

/** 「已恢复」展示时长（ms）：重连成功后短暂显示「已恢复」，随后回落「已连接」。 */
const RECOVERED_DISPLAY_MS = 2500;
let recoveredTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * ComfyWsClient.onStatus → 四态映射。
 * true：若正处于「断开重连」→ 置「已恢复」（短暂停留后回落「已连接」）；否则 → 已连接。
 * false：→ 断开重连（ComfyWsClient 内部自动重连）。
 */
export function handleWsStatus(connected: boolean): void {
  if (connected) {
    if (state.wsStatus === "reconnecting") {
      state.wsStatus = "recovered";
      if (recoveredTimer) clearTimeout(recoveredTimer);
      recoveredTimer = setTimeout(() => {
        if (state.wsStatus === "recovered") state.wsStatus = "connected";
      }, RECOVERED_DISPLAY_MS);
    } else {
      state.wsStatus = "connected";
    }
  } else {
    if (recoveredTimer) {
      clearTimeout(recoveredTimer);
      recoveredTimer = null;
    }
    state.wsStatus = "reconnecting";
  }
}

/** 显式置为「未连接」（初始基线 / 主动断开兜底）。 */
export function setWsDisconnected(): void {
  if (recoveredTimer) {
    clearTimeout(recoveredTimer);
    recoveredTimer = null;
  }
  state.wsStatus = "disconnected";
}

export async function connectComfy(): Promise<void> {
  await state.runService.connect();
  // 连接状态由 runService.ws.onStatus → handleWsStatus 驱动（V1.4-P1）：
  // onopen 到来时自动置「已连接」，这里不手动置位。
}

export function setProgress(p: WorkbenchState["progress"]): void {
  state.progress = p;
}

export function setPreview(b64: string | null): void {
  state.preview = b64;
}

export function setFinalVideo(video: WorkbenchState["finalVideo"]): void {
  state.finalVideo = video;
}

/** 新一轮生成开始时清空上次成片（保留预览帧）。 */
export function clearFinalVideo(): void {
  state.finalVideo = null;
}

/** 设置输出分辨率（clamp 到 H3 常见范围，并对齐 32 倍数）。手动修改 → 比例切到自定义。 */
export function setOutputSize(width: number, height: number): void {
  state.outputSize = {
    width: alignDim(clampDim(width, state.outputSize.width)),
    height: alignDim(clampDim(height, state.outputSize.height)),
  };
  state.ratioId = "custom";
  state.mpTier = null;
}

/** 应用「比例 + 百万像素」组合：矩阵档位优先，任意数值走公式 → 写 outputSize。 */
export function applyResolutionCombo(ratioId: string, mp: number): void {
  const dim = resolutionFor(ratioId, mp) ?? resolutionForMp(ratioId, mp);
  if (!dim) return;
  state.outputSize = { width: dim.width, height: dim.height };
  state.ratioId = ratioId;
  state.mpTier = mp;
}

/** 设置输出帧率（clamp 到 FPS 范围，默认 24）。 */
export function setFrameRate(v: number): void {
  if (!Number.isFinite(v)) return;
  state.frameRate = Math.min(FPS_MAX, Math.max(FPS_MIN, Math.round(v)));
}

/** 修改某镜头时长（durationSec 是唯一真相源，写回项目模型）。 */
export function updateShotDuration(shotId: string, durationSec: number): void {
  if (!state.project) return;
  const d = clampDur(durationSec);
  for (const ep of state.project.episodes) {
    for (const sc of ep.scenes) {
      const shot = sc.shots.find((s) => s.id === shotId);
      if (shot) {
        shot.durationSec = d;
        touch();
        return;
      }
    }
  }
}

// ---------- V1.2 编辑原子操作 ----------
// 镜头/场景/资产的可写操作。全部直接改 reactive project 树，Vue 自动刷新。

/** 生成唯一 id（crypto.randomUUID 优先，旧浏览器兜底）。 */
export function genId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `id_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

/** 默认生成参数（V1 r2v 最小集）。 */
export function defaultGeneration(): ShotGeneration {
  return { taskType: "auto", continuityMode: "auto", smartTail: false, stateChange: "", consistencyCheck: "auto" };
}

/** 默认镜头 refs 结构。 */
export function defaultRefs(): Shot["refs"] {
  return { refImages: [], refAudios: [], refVideos: [], genImage: { imageFile: "" } };
}

function findShot(shotId: string): { shot: Shot; scene: Scene; episode: Episode } | null {
  if (!state.project) return null;
  for (const ep of state.project.episodes) {
    for (const sc of ep.scenes) {
      const shot = sc.shots.find((s) => s.id === shotId);
      if (shot) return { shot, scene: sc, episode: ep };
    }
  }
  return null;
}

function findScene(sceneId: string): Scene | null {
  if (!state.project) return null;
  for (const ep of state.project.episodes) {
    const sc = ep.scenes.find((s) => s.id === sceneId);
    if (sc) return sc;
  }
  return null;
}

/** 场景内镜头按数组序重置 order。 */
function reorder(scene: Scene): void {
  scene.shots.forEach((s, i) => (s.order = i));
}

// ---- 镜头字段 ----

export function updateShotContent(shotId: string, visual: string): void {
  const f = findShot(shotId);
  if (f) {
    f.shot.content.visual = visual;
    touch();
  }
}

export function updateShotNegative(shotId: string, text: string): void {
  const f = findShot(shotId);
  if (f) {
    f.shot.content.negativePrompt = text;
    touch();
  }
}

/** 镜头 content 分区字段（V2 Prompt 分区编辑器）。 */
export type ShotContentField = "visual" | "negativePrompt" | "cameraText" | "style" | "soundText";

/** 写回镜头 content 分区字段（摄影/风格/声音/负面等自由文本）。 */
export function updateShotSection(shotId: string, field: ShotContentField, text: string): void {
  const f = findShot(shotId);
  if (!f) return;
  (f.shot.content as unknown as Record<ShotContentField, string>)[field] = text;
  touch();
}

/** V1.7 Phase 5（P1-D）：记录本镜 cameraText 来源的运镜模板 id（下拉回显）。
 *  空串 = 自定义/手编（cameraTemplate 清空）。仅元数据，不参与指纹/生成。 */
export function updateShotCameraTemplate(shotId: string, templateId: string): void {
  const f = findShot(shotId);
  if (!f) return;
  f.shot.cameraTemplate = templateId || undefined;
  touch();
}

/**
 * 设置镜头人物集合（V1.6-B）。
 * 空数组 = 自动继承（场景默认 → 全局默认）；非空 → castManual=true。
 * 数组写 castIds；旧单值 castId 字段清空（读取时由 normalizeShotInheritance 迁移）。
 */
export function setShotCasts(shotId: string, castIds: string[]): void {
  const f = findShot(shotId);
  if (!f) return;
  const ids = [...new Set(castIds.filter((id) => Boolean(id)))];
  f.shot.castIds = ids.length ? ids : undefined;
  f.shot.castId = undefined;
  f.shot.castManual = ids.length > 0;
  touch();
}

/** 指定镜头人物（单值兼容入口）：非空 castId → castIds=[castId]，空串 → 自动继承。 */
export function updateShotCast(shotId: string, castId: string, _manual = true): void {
  const f = findShot(shotId);
  if (!f) return;
  const ids = castId ? [castId] : [];
  f.shot.castIds = ids.length ? ids : undefined;
  f.shot.castId = undefined;
  f.shot.castManual = ids.length > 0;
  touch();
}

/** 切换镜头人物：已在集合 → 移除；不在 → 追加（chip × / @命中绑定用）。 */
export function toggleShotCast(shotId: string, castId: string): void {
  const f = findShot(shotId);
  if (!f) return;
  const cur = shotCastIds(f.shot);
  const next = cur.includes(castId) ? cur.filter((id) => id !== castId) : [...cur, castId];
  setShotCasts(shotId, next);
}

export function updateShotLocation(shotId: string, locationId: string, manual = true): void {
  const f = findShot(shotId);
  if (!f) return;
  f.shot.locationId = locationId || undefined;
  f.shot.locationManual = manual;
  touch();
}

export function updateShotContinuity(shotId: string, mode: ShotGeneration["continuityMode"]): void {
  const f = findShot(shotId);
  if (!f) return;
  f.shot.generation = { ...(f.shot.generation ?? defaultGeneration()), continuityMode: mode };
  touch();
}

export function updateShotSmartTail(shotId: string, v: boolean): void {
  const f = findShot(shotId);
  if (!f) return;
  f.shot.generation = { ...(f.shot.generation ?? defaultGeneration()), smartTail: v };
  touch();
}

export function updateShotStateChange(shotId: string, text: string): void {
  const f = findShot(shotId);
  if (!f) return;
  f.shot.generation = { ...(f.shot.generation ?? defaultGeneration()), stateChange: text };
  touch();
}

// ---- V1.7 Phase 4：AI 制作计划审核（采纳 AI 草稿） ----

/**
 * 采纳单个 AI 草稿镜头（adopted=true，过审核门槛）。
 * 非 AI 草稿 / 已采纳 → 幂等无操作。aiDraft 保留为历史元数据（V17 拍板）。
 */
export function adoptAiDraft(shotId: string): void {
  const f = findShot(shotId);
  if (!f) return;
  if (!f.shot.aiDraft || f.shot.adopted) return;
  f.shot.adopted = true;
  touch();
}

/** 采纳全部 AI 草稿镜头（一键批量，V17 §9.0「采纳全部」）。无未采纳草稿 → 无操作。 */
export function adoptAllAiDrafts(): void {
  if (!state.project) return;
  let changed = false;
  for (const ep of state.project.episodes) {
    for (const sc of ep.scenes) {
      for (const s of sc.shots) {
        if (s.aiDraft && !s.adopted) {
          s.adopted = true;
          changed = true;
        }
      }
    }
  }
  if (changed) touch();
}

// ---- 镜头管理 ----

/**
 * 在场景中追加/插入新镜头。
 *
 * Q3 修复（#98）：不再把场景默认值预写进 castId/locationId。
 * 原因：预写 + 未设 manual 会让 resolveOne 的 `defaultId === explicitId` 把
 * 「场景默认继承」误判成「⚡镜头指定」，@ 命中分支永远走不到；
 * 且 directorCore 生成路径已有 `shot.castId ?? scene.defaultCastId ?? 全局` 兜底，
 * 预写纯属多余。保持 undefined，让继承链在渲染时动态解析。
 */
export function addShot(sceneId: string, afterShotId?: string): Shot | null {
  const scene = findScene(sceneId);
  if (!scene) return null;
  const shot: Shot = {
    id: genId(),
    sceneId,
    order: scene.shots.length,
    name: `镜头 ${scene.shots.length + 1}`,
    durationSec: 3,
    content: { visual: "" },
    castId: undefined,
    castIds: undefined,
    locationId: undefined,
    generation: defaultGeneration(),
    refs: defaultRefs(),
  };
  if (afterShotId) {
    const idx = scene.shots.findIndex((s) => s.id === afterShotId);
    scene.shots.splice(idx + 1, 0, shot);
  } else {
    scene.shots.push(shot);
  }
  reorder(scene);
  // P0 Story Timeline：timeline 存在时同步新镜头（插到参照镜头之后，缺省末尾；
  // 播放顺序不因场景树增改而改变，只在时间线拖拽时显式调整）。
  const ep = getCurrentEpisode();
  if (ep) insertShotIdAfterInTimeline(ep, shot.id, afterShotId);
  state.currentSceneId = sceneId;
  state.currentShotId = shot.id;
  state.currentPane = "shot";
  touch();
  return shot;
}

export function removeShot(shotId: string): boolean {
  const f = findShot(shotId);
  if (!f) return false;
  const idx = f.scene.shots.findIndex((s) => s.id === shotId);
  f.scene.shots.splice(idx, 1);
  reorder(f.scene);
  // P0 Story Timeline：timeline 存在时移除该镜头 id（保持集合一致，不悬空）。
  dropShotIdFromTimeline(f.episode, shotId);
  if (state.currentShotId === shotId) {
    const next = f.scene.shots[Math.min(idx, f.scene.shots.length - 1)];
    state.currentShotId = next?.id ?? null;
    if (!state.currentShotId) state.currentPane = "scene";
  }
  touch();
  return true;
}

export function duplicateShot(shotId: string): Shot | null {
  const f = findShot(shotId);
  if (!f) return null;
  const idx = f.scene.shots.findIndex((s) => s.id === shotId);
  const copy = JSON.parse(JSON.stringify(f.shot)) as Shot;
  copy.id = genId();
  copy.order = idx + 1;
  copy.name = `${f.shot.name ?? `镜头 ${idx + 1}`} 副本`;
  copy.render = undefined;
  f.scene.shots.splice(idx + 1, 0, copy);
  reorder(f.scene);
  // P0 Story Timeline：副本插到原镜之后（保持邻接；仅当 timeline 已存在）。
  insertShotIdAfterInTimeline(f.episode, copy.id, shotId);
  state.currentShotId = copy.id;
  state.currentPane = "shot";
  touch();
  return copy;
}

/** 上移/下移一个镜头（同场景内）。 */
export function moveShot(shotId: string, dir: -1 | 1): void {
  const f = findShot(shotId);
  if (!f) return;
  const idx = f.scene.shots.findIndex((s) => s.id === shotId);
  const target = idx + dir;
  if (target < 0 || target >= f.scene.shots.length) return;
  const [moved] = f.scene.shots.splice(idx, 1);
  f.scene.shots.splice(target, 0, moved);
  reorder(f.scene);
  touch();
}

export function renameShot(shotId: string, name: string): void {
  const f = findShot(shotId);
  if (f && name.trim()) {
    f.shot.name = name.trim();
    touch();
  }
}

/** 镜头跨场景移动（目标场景尾部追加）。 */
export function moveShotToScene(shotId: string, newSceneId: string): void {
  const f = findShot(shotId);
  const dst = findScene(newSceneId);
  if (!f || !dst || f.scene.id === newSceneId) return;
  const idx = f.scene.shots.findIndex((s) => s.id === shotId);
  const [moved] = f.scene.shots.splice(idx, 1);
  moved.sceneId = newSceneId;
  dst.shots.push(moved);
  reorder(f.scene);
  reorder(dst);
  state.currentSceneId = newSceneId;
  state.currentPane = "shot";
  touch();
}

// ---- 场景管理 ----

export function addScene(): Scene | null {
  const ep = getCurrentEpisode();
  if (!ep) return null;
  const scene: Scene = {
    id: genId(),
    name: `地点 ${ep.scenes.length + 1}`,
    order: ep.scenes.length,
    assets: { cast: [], locations: [], props: [], styles: [] },
    shots: [],
  };
  ep.scenes.push(scene);
  state.currentSceneId = scene.id;
  state.currentShotId = null;
  state.currentPane = "scene";
  touch();
  return scene;
}

/** 删除场景（连带其镜头）。 */
export function removeScene(sceneId: string): boolean {
  const ep = getCurrentEpisode();
  if (!ep) return false;
  const idx = ep.scenes.findIndex((s) => s.id === sceneId);
  if (idx < 0) return false;
  const removed = ep.scenes[idx];
  ep.scenes.splice(idx, 1);
  // P0 Story Timeline：场景删除连带其全部镜头 id 移出播放顺序（不悬空）。
  if (ep.timeline?.length && removed.shots.length) {
    const gone = new Set(removed.shots.map((s) => s.id));
    ep.timeline = ep.timeline.filter((id) => !gone.has(id));
  }
  if (state.currentSceneId === sceneId) {
    if (ep.scenes.length) {
      const next = ep.scenes[Math.min(idx, ep.scenes.length - 1)];
      state.currentSceneId = next.id;
      state.currentShotId = next.shots[0]?.id ?? null;
      state.currentPane = state.currentShotId ? "shot" : "scene";
    } else {
      state.currentSceneId = null;
      state.currentShotId = null;
      state.currentPane = "scene";
    }
  }
  touch();
  return true;
}

// ---- P0 Story Timeline（2026-08-14 用户拍板）----
// Episode.timeline = 本集播放顺序（前端全局 Shot.id，跨场景唯一）。Scene 是空间/
// 资产容器，timeline 是播放顺序，两者解耦。缺省/空 = 场景树顺序（严格向后兼容）。
// 原则：timeline 是播放顺序唯一权威源，场景树操作只维护「集合一致」（不悬空、不丢镜），
// 不改其序；只有时间线 UI 拖拽（reorderTimeline）才改播放顺序。

/** 懒初始化：timeline 缺省/空时按场景树顺序生成（scene.order → shot.order）。 */
function ensureTimeline(ep: Episode): Episode {
  if (!ep.timeline || !ep.timeline.length) {
    ep.timeline = collectEpisodeShotIds(ep);
  }
  return ep;
}

/** 场景树顺序收集全部镜头 id（scene.order 升序 → shot.order 升序）。 */
function collectEpisodeShotIds(ep: Episode): string[] {
  const out: string[] = [];
  for (const sc of ep.scenes.slice().sort((a, b) => a.order - b.order)) {
    for (const sh of sc.shots.slice().sort((a, b) => a.order - b.order)) {
      out.push(sh.id);
    }
  }
  return out;
}

/** timeline 存在时，把镜头 id 插到参照镜头之后（无参照/不在列 → 末尾）。 */
function insertShotIdAfterInTimeline(ep: Episode, shotId: string, afterShotId?: string): void {
  const tl = ep.timeline;
  if (!tl) return;
  if (afterShotId) {
    const i = tl.indexOf(afterShotId);
    if (i >= 0) {
      tl.splice(i + 1, 0, shotId);
      return;
    }
  }
  tl.push(shotId);
}

/** timeline 存在时，从其中移除某镜头 id（场景树删除的一致性同步）。 */
function dropShotIdFromTimeline(ep: Episode, shotId: string): void {
  const tl = ep.timeline;
  if (!tl) return;
  const i = tl.indexOf(shotId);
  if (i >= 0) tl.splice(i, 1);
}

/**
 * P0-7 Timeline UI：把镜头移动到播放顺序的 targetIndex（时间线拖拽落点）。
 *  - timeline 缺省/空 → 懒初始化场景树顺序（此后显式播放顺序，与场景树解耦）；
 *  - 悬空镜头（timeline 建后被场景树增补未同步）先补到末尾再移动；
 *  - 目标越界钳制、原位置相同 → 无操作。
 * @returns 是否发生移动（调用方据此更新 dirty）。
 */
export function reorderTimeline(shotId: string, targetIndex: number): boolean {
  const ep = getCurrentEpisode();
  if (!ep) return false;
  ensureTimeline(ep);
  const tl = ep.timeline!;
  let from = tl.indexOf(shotId);
  if (from < 0) {
    tl.push(shotId);
    from = tl.length - 1;
  }
  const max = tl.length - 1;
  if (targetIndex < 0) targetIndex = 0;
  if (targetIndex > max) targetIndex = max;
  if (from === targetIndex) return false;
  const [moved] = tl.splice(from, 1);
  tl.splice(targetIndex, 0, moved);
  touch();
  return true;
}

export function renameScene(sceneId: string, name: string): void {
  const sc = findScene(sceneId);
  if (sc && name.trim()) {
    sc.name = name.trim();
    touch();
  }
}

/** 场景通用字段写回（description/location/time/weather/referenceImage/defaultCastId…）。 */
export function patchScene(sceneId: string, patch: Partial<Scene>): void {
  const sc = findScene(sceneId);
  if (!sc) return;
  Object.assign(sc, patch);
  touch();
}

// ---- 资产 CRUD（scope: episode=全局库 / scene=场景素材组）----

/** AssetKind（单数，模型层）→ SceneAssets 键（复数，库容器）。 */
const ASSET_KIND_KEY: Record<AssetKind, keyof SceneAssets> = {
  cast: "cast",
  location: "locations",
  prop: "props",
  style: "styles",
};

function assetPool(kind: AssetKind, scope: "episode" | "scene", sceneId?: string): Asset[] | null {
  const ep = getCurrentEpisode();
  if (!ep) return null;
  const key = ASSET_KIND_KEY[kind];
  if (scope === "episode") {
    if (!ep.assets) ep.assets = { cast: [], locations: [], props: [], styles: [] };
    return ep.assets[key];
  }
  const sc = findScene(sceneId ?? state.currentSceneId ?? "");
  if (!sc) return null;
  if (!sc.assets) sc.assets = { cast: [], locations: [], props: [], styles: [] };
  return sc.assets[key];
}

/**
 * V1.5-P0-A：同池内同 kind 名称唯一性检查（防 @ 名字匹配歧义）。
 * 返回占用该名称的现有资产（不含 excludeId）；无冲突返回 undefined。
 * name 大小写不敏感（trim 后比较）。
 */
export function assetNameConflict(
  kind: AssetKind,
  name: string,
  scope: "episode" | "scene",
  sceneId?: string,
  excludeId?: string,
): Asset | undefined {
  const pool = assetPool(kind, scope, sceneId);
  if (!pool || !name.trim()) return undefined;
  const n = name.trim().toLowerCase();
  return pool.find((a) => a.id !== excludeId && a.name.trim().toLowerCase() === n);
}

/** 新建资产。默认落到场景素材组；scope="episode" 落全局库。同池内同 kind 名称冲突 → null。 */
export function addAsset(
  kind: AssetKind,
  name: string,
  opts: { scope?: "episode" | "scene"; sceneId?: string; imageFile?: string; aliases?: string[]; description?: string } = {},
): Asset | null {
  const pool = assetPool(kind, opts.scope ?? "scene", opts.sceneId);
  if (!pool || !name.trim()) return null;
  // V1.5-P0-A：同池内同 kind 名称唯一。冲突返回 null（调用方用 assetNameConflict 提前提示）。
  if (assetNameConflict(kind, name.trim(), opts.scope ?? "scene", opts.sceneId)) return null;
  const asset: Asset = {
    id: genId(),
    name: name.trim(),
    kind,
    imageFile: opts.imageFile ?? "",
    ...(opts.description ? { description: opts.description } : {}),
    ...(opts.aliases?.length ? { aliases: opts.aliases } : {}),
  };
  pool.push(asset);
  touch();
  return asset;
}

/** 更新资产。改名时校验同池内同 kind 唯一；冲突则跳过 name 字段（其余照常应用）。 */
export function updateAsset(
  kind: AssetKind,
  assetId: string,
  patch: Partial<Asset>,
  scope: "episode" | "scene" = "scene",
  sceneId?: string,
): void {
  const pool = assetPool(kind, scope, sceneId);
  const a = pool?.find((x) => x.id === assetId);
  if (a) {
    if (patch.name !== undefined && assetNameConflict(kind, patch.name, scope, sceneId, assetId)) {
      const { name: _skipName, ...rest } = patch;
      Object.assign(a, rest);
    } else {
      Object.assign(a, patch);
    }
    touch();
  }
}

/**
 * V1.5-P0-A：把全局库资产复制到当前场景素材组。
 * 新 id 一次性快照（复制后与全局解耦）；场景池已有同 kind 同名 → 返回 null（不覆盖）。
 */
export function copyAssetToScene(kind: AssetKind, assetId: string, sceneId: string): Asset | null {
  const src = assetPool(kind, "episode");
  const a = src?.find((x) => x.id === assetId);
  if (!a) return null;
  const sc = findScene(sceneId);
  if (!sc) return null;
  if (!sc.assets) sc.assets = { cast: [], locations: [], props: [], styles: [] };
  const pool = sc.assets[ASSET_KIND_KEY[kind]];
  if (!pool) return null;
  if (assetNameConflict(kind, a.name, "scene", sceneId)) return null;
  const copy: Asset = {
    id: genId(),
    name: a.name,
    kind: a.kind,
    imageFile: a.imageFile,
    ...(a.description ? { description: a.description } : {}),
    ...(a.aliases?.length ? { aliases: [...a.aliases] } : {}),
  };
  pool.push(copy);
  touch();
  return copy;
}

/** V1.5-P0-B：@资产引用批量替换覆盖的 Prompt 字段（规划 §3.5：visual + 摄影/风格/声音三分区）。 */
const MENTION_TEXT_FIELDS = ["visual", "cameraText", "style", "soundText"] as const;

/** @token 后不允许跟的「资产名字符」（与 assetResolver 名字符集一致）：字母/数字/下划线/连字符/括号/·。 */
const ASSET_NAME_CHAR = String.raw`\p{L}\p{N}_\-（）()·\.·`;

/**
 * V1.5-P0-B：把文本中 `@旧名` token 替换为 `@新名`（§3.5 token 边界规则）。
 * 仅当 `@旧名` **后一字符不是资产名字符**（或已到文本末尾）时替换 ——
 * 防止 `@林雪` 误伤 `@林雪·成年` / `@林雪走进`（更长 token 的组成部分）。
 */
export function replaceMentionToken(text: string, oldName: string, newName: string): string {
  if (!text || !oldName || oldName === newName) return text;
  const esc = oldName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`@${esc}(?![${ASSET_NAME_CHAR}])`, "gu");
  return text.replace(re, `@${newName}`);
}

/**
 * V1.5-P0-B：@资产改名同步。
 * 改 name/aliases 后，扫描项目全部镜头 Prompt（visual + 三分区文本），
 * 把 `@旧名` token 批量替换为 `@新名`（旧 aliases 同理）。全程 touch()。
 *
 * 规则（§3.5）：
 * - 多匹配名（name + aliases）按**旧名长度降序**替换，长名先替换后不会残留可被短名二次命中的片段；
 * - token 边界：`@旧名` 后一字符非资产名字符才替换；
 * - 显式 id 引用（shot.castId/locationId、scene 默认值）不受影响（id 稳定）。
 */
export function renameAsset(
  kind: AssetKind,
  assetId: string,
  patch: Partial<Asset>,
  scope: "episode" | "scene" = "scene",
  sceneId?: string,
): void {
  const pool = assetPool(kind, scope, sceneId);
  const a = pool?.find((x) => x.id === assetId);
  if (!a || !pool) return;
  const oldName = a.name;
  const oldAliases = a.aliases ?? [];

  updateAsset(kind, assetId, patch, scope, sceneId);

  // 应用 patch 后读回新值（name 冲突时 updateAsset 跳过 name → newName 仍是旧名，映射跳过）。
  const after = pool.find((x) => x.id === assetId);
  if (!after) return;
  const newName = after.name;
  const newAliases = after.aliases ?? [];

  const mapping: [string, string][] = [];
  if (newName !== oldName) mapping.push([oldName, newName]);
  oldAliases.forEach((al, i) => {
    const nw = newAliases[i];
    if (nw && nw !== al) mapping.push([al, nw]);
  });
  if (!mapping.length) return; // updateAsset 已 touch()

  // 按旧名长度降序：先替换最长名，避免 `@林雪` 把 `@林雪·成年` 拦腰截断。
  mapping.sort((a, b) => b[0].length - a[0].length);

  const ep = getCurrentEpisode();
  if (!ep) return;
  for (const sc of ep.scenes) {
    for (const shot of sc.shots) {
      const c = shot.content;
      for (const field of MENTION_TEXT_FIELDS) {
        const v = c[field];
        if (typeof v !== "string" || !v) continue;
        let out = v;
        for (const [oldN, newN] of mapping) out = replaceMentionToken(out, oldN, newN);
        if (out !== v) c[field] = out;
      }
    }
  }
  touch();
}

export function removeAsset(
  kind: AssetKind,
  assetId: string,
  scope: "episode" | "scene" = "scene",
  sceneId?: string,
): boolean {
  const pool = assetPool(kind, scope, sceneId);
  if (!pool) return false;
  const idx = pool.findIndex((a) => a.id === assetId);
  if (idx < 0) return false;
  pool.splice(idx, 1);
  cleanupAssetRefs(kind, assetId);
  touch();
  return true;
}

/** 资产删除后清掉依赖它的场景默认值/镜头显式引用。 */
function cleanupAssetRefs(kind: AssetKind, assetId: string): void {
  const ep = getCurrentEpisode();
  if (!ep) return;
  for (const sc of ep.scenes) {
    if (kind === "cast" && sc.defaultCastId === assetId) sc.defaultCastId = undefined;
    if (kind === "location" && sc.defaultLocationId === assetId) sc.defaultLocationId = undefined;
    if (kind === "style" && sc.defaultStyleId === assetId) sc.defaultStyleId = undefined;
    for (const shot of sc.shots) {
      if (kind === "cast" && shot.castIds?.includes(assetId)) {
        shot.castIds = shot.castIds.filter((id) => id !== assetId);
        if (!shot.castIds.length) shot.castIds = undefined;
      }
      if (kind === "cast" && shot.castId === assetId) shot.castId = undefined;
      if (kind === "cast" && !shotCastIds(shot).length) shot.castManual = false;
      if (kind === "location" && shot.locationId === assetId) {
        shot.locationId = undefined;
        shot.locationManual = false;
      }
    }
  }
}

// ---- 镜头参考图（refs.refImages，V1.2.5 上传闭环）----

function basename(relPath: string): string {
  const norm = relPath.replace(/\\/g, "/");
  return norm.slice(norm.lastIndexOf("/") + 1);
}

/** 给镜头追加一张参考图（refImages）。relPath 是 ComfyUI input 相对路径。 */
export function addShotRefImage(shotId: string, relPath: string): RefImage | null {
  const f = findShot(shotId);
  if (!f || !relPath.trim()) return null;
  if (!f.shot.refs) f.shot.refs = defaultRefs();
  const refs = f.shot.refs as NonNullable<Shot["refs"]>;
  const ref: RefImage = {
    index: refs.refImages.length,
    imageFile: relPath.trim(),
    fileName: basename(relPath),
  };
  refs.refImages.push(ref);
  touch();
  return ref;
}

/** 删除镜头某张参考图（按数组下标），后续 refs 重新编号。 */
export function removeShotRefImage(shotId: string, index: number): boolean {
  const f = findShot(shotId);
  if (!f) return false;
  const arr = f.shot.refs?.refImages;
  if (!arr || index < 0 || index >= arr.length) return false;
  arr.splice(index, 1);
  arr.forEach((r, i) => (r.index = i));
  touch();
  return true;
}

// ---- 镜头参考视频/音频（refs.refVideos / refAudios，V1.2.6）----

/** 给镜头追加一条参考视频（refVideos）。relPath 是 ComfyUI input 相对路径。 */
export function addShotRefVideo(shotId: string, relPath: string): RefVideo | null {
  const f = findShot(shotId);
  if (!f || !relPath.trim()) return null;
  if (!f.shot.refs) f.shot.refs = defaultRefs();
  const refs = f.shot.refs as NonNullable<Shot["refs"]>;
  const ref: RefVideo = {
    index: refs.refVideos.length,
    videoFile: relPath.trim(),
    fileName: basename(relPath),
  };
  refs.refVideos.push(ref);
  touch();
  return ref;
}

/** 删除镜头某条参考视频（按数组下标），后续 refs 重新编号。 */
export function removeShotRefVideo(shotId: string, index: number): boolean {
  const f = findShot(shotId);
  if (!f) return false;
  const arr = f.shot.refs?.refVideos;
  if (!arr || index < 0 || index >= arr.length) return false;
  arr.splice(index, 1);
  arr.forEach((r, i) => (r.index = i));
  touch();
  return true;
}

/** 给镜头追加一条参考音频（refAudios）。relPath 是 ComfyUI input 相对路径。 */
export function addShotRefAudio(shotId: string, relPath: string): RefAudio | null {
  const f = findShot(shotId);
  if (!f || !relPath.trim()) return null;
  if (!f.shot.refs) f.shot.refs = defaultRefs();
  const refs = f.shot.refs as NonNullable<Shot["refs"]>;
  const ref: RefAudio = {
    index: refs.refAudios.length,
    audioFile: relPath.trim(),
    fileName: basename(relPath),
  };
  refs.refAudios.push(ref);
  touch();
  return ref;
}

/** 删除镜头某条参考音频（按数组下标），后续 refs 重新编号。 */
export function removeShotRefAudio(shotId: string, index: number): boolean {
  const f = findShot(shotId);
  if (!f) return false;
  const arr = f.shot.refs?.refAudios;
  if (!arr || index < 0 || index >= arr.length) return false;
  arr.splice(index, 1);
  arr.forEach((r, i) => (r.index = i));
  touch();
  return true;
}

function clampDim(v: number, fallback: number): number {
  if (!Number.isFinite(v)) return fallback;
  return alignDim(Math.min(2048, Math.max(256, Math.round(v))));
}

function clampDur(v: number): number {
  if (!Number.isFinite(v)) return 1;
  // 保留 1 位小数：允许小数秒（H3 按帧换算，deriveFrameCount 会 snap 17k+5 网格）。
  return Math.max(1, Math.round(v * 10) / 10);
}

export function setRunning(running: boolean): void {
  state.running = running;
}

export function setLastPromptId(id: string | null): void {
  state.lastPromptId = id;
}

export function setError(err: string | null): void {
  state.lastError = err;
}

// ---------- V1.3 生成范围 / 多选 / 段状态 ----------

export function setGenScope(scope: GenScope): void {
  state.genScope = scope;
}

/** 多选切换：已选则移除，未选则加入。 */
export function toggleShotSelected(shotId: string): void {
  const i = state.selectedShotIds.indexOf(shotId);
  if (i >= 0) state.selectedShotIds.splice(i, 1);
  else state.selectedShotIds.push(shotId);
}

export function clearSelectedShots(): void {
  state.selectedShotIds.splice(0);
}

/** 存最近一次 segment_status（段索引 → 镜头 id 由调用方按拍平顺序传 indexShotIds）。 */
export function setSegStatus(indexShotIds: string[], cached: number[], states: Record<string, string>): void {
  state.segStatus = { indexShotIds, cached, states };
}

// ---------- V1.3-C 任务中心 ----------

/**
 * 新建任务记录（最新在前），返回 taskId。
 * shotOrder：生成时刻全片拍平镜头 id 顺序，任务下载/定位用它固化段索引。
 * kind：generate=镜头生成（默认）/ export=成片导出（V1.6-A）。
 */
export function addTask(input: {
  scopeLabel: string;
  shotCount: number;
  shotOrder: string[];
  targetShotIds: string[] | null;
  kind?: "generate" | "export";
}): string {
  const id = genId();
  const t: TaskRecord = {
    id,
    kind: input.kind ?? "generate",
    scopeLabel: input.scopeLabel,
    shotCount: input.shotCount,
    shotOrder: input.shotOrder,
    targetShotIds: input.targetShotIds,
    currentIndex: null,
    shotStates: null,
    promptId: null,
    status: "running",
    startedAt: Date.now(),
    finishedAt: null,
    error: null,
    errorDetail: null,
    nodeId: null,
    report: null,
    failedShotIds: [],
    progress: null,
    preview: null,
    finalVideo: null,
    exportStage: null,
    exportFile: null,
  };
  state.tasks.unshift(t);
  state.currentTaskId = id;
  return id;
}

/** 局部更新任务（进度/预览/成片/结束态）。 */
export function patchTask(taskId: string, patch: Partial<TaskRecord>): void {
  const t = state.tasks.find((x) => x.id === taskId);
  if (t) Object.assign(t, patch);
}

// ---------- V1.6-A 成片导出任务 ----------

/** 导出任务输入（V1.6-A）。scope=movie 整片 / scene 当前场景。 */
export interface ExportTaskInput {
  scope: "movie" | "scene";
  /** scene 档必填：当前场景 id。 */
  sceneId: string | null;
  /** 任务范围摘要（如「整部影片（8 镜）」「当前场景（3 镜）」）。 */
  scopeLabel: string;
  /** 生成时刻全片拍平镜头 id 顺序（固化段索引）。 */
  shotOrder: string[];
  /** 实际要导出的镜头 id（null=全部）。 */
  targetShotIds: string[] | null;
  /** Director 节点 id。 */
  nodeId: string;
  /** 单镜编码帧率（默认由后端 24）。 */
  fps?: number;
  /** true=跳过缺失镜头只导出已有的。 */
  skipMissing?: boolean;
}

/**
 * 发起一次成片导出（V1.6-A）：
 * 1) 建导出任务（kind=export）进任务中心；
 * 2) 调后端 POST /minimax/director/export（单次阻塞 HTTP，内部逐镜编码 + ffmpeg 直拼）；
 * 3) 期间用客户端估算进度驱动「编码 Shot x/y → 合并成片」文案（后端不流式回传进度）；
 * 4) 完成写入成片产物（▶ 查看成片）；业务错误（not_cached/ffmpeg_missing…）按失败落账。
 * 返回 taskId（调用方可观察状态）。
 */
export async function startExportTask(input: ExportTaskInput): Promise<string> {
  const shotIds = input.targetShotIds ?? input.shotOrder;
  const taskId = addTask({
    scopeLabel: input.scopeLabel,
    shotCount: shotIds.length,
    shotOrder: input.shotOrder,
    targetShotIds: input.targetShotIds,
    kind: "export",
  });
  const api = state.runService.api;
  const total = Math.max(1, shotIds.length);
  // 客户端估算进度：按镜头数均摊「编码」阶段，编码完成后停在 total 表示进入「合并成片」。
  let encoding = 0;
  let stageTimer: ReturnType<typeof setInterval> | null = null;
  const stopStage = () => {
    if (stageTimer) {
      clearInterval(stageTimer);
      stageTimer = null;
    }
  };
  const stageTick = () => {
    if (encoding < total) encoding += 1;
    patchTask(taskId, { exportStage: { encoding, total } });
  };
  patchTask(taskId, { exportStage: { encoding: 1, total } });
  encoding = 1;
  stageTimer = setInterval(stageTick, 3000);
  try {
    const opts: ExportOptions = { fps: input.fps, skipMissing: input.skipMissing };
    const resp =
      input.scope === "scene" && input.sceneId
        ? await api.exportScene(input.nodeId, input.sceneId, shotIds, opts)
        : await api.exportMovie(input.nodeId, shotIds, opts);
    stopStage();
    if (resp.error) {
      patchTask(taskId, {
        status: "failed",
        error: resp.message ?? `导出失败（${resp.error}）`,
        errorDetail: resp.message ?? null,
        finishedAt: Date.now(),
      });
    } else {
      const filename = resp.filename ?? "";
      patchTask(taskId, {
        status: "done",
        finishedAt: Date.now(),
        exportFile: filename || null,
        finalVideo: filename
          ? { url: `/view?filename=${encodeURIComponent(filename)}&type=output`, filename }
          : null,
      });
    }
  } catch (err) {
    stopStage();
    const msg = err instanceof Error ? err.message : String(err);
    patchTask(taskId, {
      status: "failed",
      error: msg,
      errorDetail: msg,
      finishedAt: Date.now(),
    });
  }
  return taskId;
}

/** 清空任务历史。 */
export function clearTasks(): void {
  state.tasks.splice(0);
  state.currentTaskId = null;
}

// ---------- V1.3-D 参数指纹 ----------

/** 记录「生成成功」时各镜头参数指纹（增量合并，同时写回项目模型持久化）。 */
export function recordParamHashes(entries: Record<string, string>): void {
  Object.assign(state.paramHashes, entries);
  if (state.project) {
    state.project.fingerprints = { ...(state.project.fingerprints ?? {}), ...entries };
  }
}

// ---------- V1.11 生成历史（版本管理）----------

/**
 * 生成完成追加一条版本记录（写 project 树 + touch 置脏）。
 * 首版自动采纳为当前成片（activeGenerationId）；后续版本不自动切换——
 * 用户点「⭐ 设为当前成片」才更新（最新生成 ≠ 当前成片）。
 */
export function addGeneration(shotId: string, gen: ShotGenRecord): void {
  const f = findShot(shotId);
  if (!f) return;
  if (!f.shot.generations) f.shot.generations = [];
  f.shot.generations.push(gen);
  if (!f.shot.activeGenerationId) f.shot.activeGenerationId = gen.id;
  touch();
}

/** ⭐ 设为当前成片：activeGenerationId 指向该版本（版本必须已存在才生效）。 */
export function setActiveGeneration(shotId: string, generationId: string): void {
  const f = findShot(shotId);
  if (!f || !f.shot.generations) return;
  if (!f.shot.generations.some((g) => g.id === generationId)) return;
  f.shot.activeGenerationId = generationId;
  touch();
}

/** 删除某条生成版本（历史清理；若删的是当前成片则回落到最后一条/清空）。 */
export function removeGeneration(shotId: string, generationId: string): void {
  const f = findShot(shotId);
  if (!f || !f.shot.generations) return;
  const arr = f.shot.generations;
  const idx = arr.findIndex((g) => g.id === generationId);
  if (idx < 0) return;
  arr.splice(idx, 1);
  if (f.shot.activeGenerationId === generationId) {
    f.shot.activeGenerationId = arr.length ? arr[arr.length - 1].id : undefined;
  }
  touch();
}

/** 清空参数指纹（切项目时）。 */
export function clearParamHashes(): void {
  state.paramHashes = {};
}

// ---------- Phase 2 Voice Cast（#573）----------

/** 当前项目 Voice Cast（无项目/未归一 → 默认副本）。 */
export function getVoiceCast(): VoiceCastConfig {
  if (!state.project) return { ...DEFAULT_VOICE_CAST };
  normalizeVoiceCast(state.project);
  return state.project.voiceCast!;
}

/** 设置角色 → 音色全局映射（Voice Cast 面板）。空 voiceId = 删除该角色条目（回退规则兜底）。 */
export function setVoiceCastEntry(speaker: string, voiceId: string): void {
  if (!state.project) return;
  normalizeVoiceCast(state.project);
  const vc = state.project.voiceCast!;
  const spk = speaker.trim();
  if (!spk) return;
  const v = (voiceId || "").trim();
  if (v) vc.entries[spk] = v;
  else delete vc.entries[spk];
  touch();
}

/** 旁白固定音色（空串 → 回默认 voice_narrator）。 */
export function setVoiceCastNarrator(voiceId: string): void {
  if (!state.project) return;
  normalizeVoiceCast(state.project);
  state.project.voiceCast!.narratorVoice = (voiceId || "").trim() || DEFAULT_VOICE_CAST.narratorVoice;
  touch();
}

/** 系统音固定音色（空串 → 回默认 voice_system）。 */
export function setVoiceCastSystem(voiceId: string): void {
  if (!state.project) return;
  normalizeVoiceCast(state.project);
  state.project.voiceCast!.systemVoice = (voiceId || "").trim() || DEFAULT_VOICE_CAST.systemVoice;
  touch();
}

/** 系统音电子味变体（delivery 含「电子」时启用；空串 → 回默认）。 */
export function setVoiceCastSystemElectronic(voiceId: string): void {
  if (!state.project) return;
  normalizeVoiceCast(state.project);
  state.project.voiceCast!.systemElectronic = (voiceId || "").trim() || DEFAULT_VOICE_CAST.systemElectronic;
  touch();
}

/** 音频总开关（Phase 2-G，2026-08-17）：关 = 生成视频不自动配音/混音（Edge-TTS 不启动）。
 *  Voice Cast 面板头部按钮；配置保留，随时可重开。 */
export function setVoiceCastEnabled(enabled: boolean): void {
  if (!state.project) return;
  normalizeVoiceCast(state.project);
  state.project.voiceCast!.enabled = !!enabled;
  touch();
}

// ---------- v2.0 P3 Visual Style（#631+）----------

/** 当前项目 Visual Style profile（无项目/未归一 → 默认「抖音半写实漫剧」完整副本）。
 *  读取前归一化写回 project.styleProfile（与 getVoiceCast 同模式），
 *  保证「默认抖音半写实漫剧」在产品语义上无条件成立。 */
export function getStyleProfile(): VisualStyleProfileJson {
  if (!state.project) return { ...DEFAULT_STYLE_PROFILE_JSON };
  normalizeStyleProfile(state.project);
  return state.project.styleProfile!;
}

/** 设置全局 Visual Style profile（Visual Style 面板下拉选择；完整 dict 落盘）。
 *  生成提交时经 buildH3Prompts 第 5 参透传 /prompt/h3 的 style_profile（画风块注入）。 */
export function setStyleProfile(profile: VisualStyleProfileJson): void {
  if (!state.project) return;
  state.project.styleProfile = normalizeStyleProfileJson(profile);
  touch();
}

/** 恢复默认（抖音半写实漫剧）。 */
export function resetStyleProfile(): void {
  if (!state.project) return;
  state.project.styleProfile = { ...DEFAULT_STYLE_PROFILE_JSON };
  touch();
}

/** 项目级归一（loadProject 调用；仿 normalizeVoiceCast）。
 *  缺省 → 默认「抖音半写实漫剧」；已有 profile 补全 8 字段；幂等。 */
export function normalizeStyleProfile(project: Project): void {
  if (!project.styleProfile || typeof project.styleProfile !== "object" || !project.styleProfile.profile_id) {
    project.styleProfile = { ...DEFAULT_STYLE_PROFILE_JSON };
    return;
  }
  project.styleProfile = normalizeStyleProfileJson(project.styleProfile);
}

/** Dialogue 行级覆盖字段（写回 plan 某镜某行；空串删除该字段回退全局/规则）。 */
export interface DialogueOverride {
  type?: VoiceTypeId;
  voice_id?: string;
  emotion?: string;
  delivery?: string;
}

/**
 * 写回 plan 某镜某行对白的配音字段（行级微调，#576）。
 * 定位 = sceneId + planShotId（前端镜头场景内 shot_id）→ lineIndex（1-based）。
 * 只写非 undefined 字段；空串删除该键（恢复全局/规则推断）。
 */
export function setDialogueOverride(
  sceneId: string,
  planShotId: string,
  lineIndex: number,
  fields: DialogueOverride,
): void {
  const plan = state.project?.plan;
  if (!plan) return;
  const sc = plan.scenes.find((s) => s.scene_id === sceneId);
  if (!sc) return;
  const sh = sc.shots.find((s) => s.shot_id === planShotId);
  if (!sh?.dialogue) return;
  const row = sh.dialogue[lineIndex - 1];
  if (!row) return;
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined) continue;
    const rowRec = row as unknown as Record<string, string>;
    if (v === "") delete rowRec[k];
    else rowRec[k] = v;
  }
  touch();
}

export function useWorkbench() {
  return state;
}
