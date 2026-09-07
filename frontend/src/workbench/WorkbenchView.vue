<script setup lang="ts">
/**
 * MiniMax Studio — 分镜工作台（V1 核心页面）。
 *
 * V1.1.1 起改为双视图：
 * - home（项目选择页）：最近项目列表 + 「导入 ComfyUI 工程」（读生成时自动快照）
 *   + 「新建项目」。正式用户流程，用户不碰任何 JSON。
 * - workbench（分镜工作台）：四区布局（左项目结构 / 中预览+时间线 / 右镜头设置 / 底任务中心）。
 *
 * 「导入 timeline_data 文件」降级为 home 底部「开发工具」调试入口（V1.1 反向导入通道仍保留）。
 * timeline_data 只是后端执行格式；用户项目格式 = ProjectModel（projectStore serialize/deserialize）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  useWorkbench,
  loadProject,
  clearProject,
  getCurrentEpisode,
  getCurrentScene,
  getCurrentShot,
  selectSceneSettings,
  selectShot,
  connectComfy,
  setRunning,
  setProgress,
  setPreview,
  setFinalVideo,
  clearFinalVideo,
  setOutputSize,
  applyResolutionCombo,
  setFrameRate,
  updateShotDuration,
  setLastPromptId,
  setError,
  addShot,
  removeShot,
  duplicateShot,
  moveShot,
  renameShot,
  moveShotToScene,
  reorderTimeline,
  addScene,
  removeScene,
  renameScene,
  renameProject,
  renameEpisode,
  patchScene,
  updateShotContent,
  updateShotNegative,
  updateShotSection,
  updateShotCameraTemplate,
  setShotCasts,
  updateShotLocation,
  updateShotContinuity,
  updateShotSmartTail,
  updateShotStateChange,
  addAsset,
  removeAsset,
  updateAsset,
  assetNameConflict,
  copyAssetToScene,
  renameAsset,
  addShotRefImage,
  removeShotRefImage,
  addShotRefVideo,
  removeShotRefVideo,
  addShotRefAudio,
  removeShotRefAudio,
  setGenScope,
  toggleShotSelected,
  setSegStatus,
  addTask,
  patchTask,
  startExportTask,
  recordParamHashes,
  addGeneration,
  setActiveGeneration,
  removeGeneration,
  genId,
  markSaved,
  adoptAiDraft,
  adoptAllAiDrafts,
  getVoiceCast,
  getStyleProfile,
  setDialogueOverride,
  type GenScope,
  type TaskRecord,
} from "@/stores/workbench";
import ImageUploadBox from "./ImageUploadBox.vue";
import TaskCenter from "./TaskCenter.vue";
import AssetConfirmDialog, { type MissingAssetRef } from "./AssetConfirmDialog.vue";
import WorkflowStudio from "./WorkflowStudio.vue";
import WorkflowParamsPanel from "./WorkflowParamsPanel.vue";
import BiblePanel from "./BiblePanel.vue";
import VoiceCastPanel from "./VoiceCastPanel.vue";
import VisualStylePanel from "./VisualStylePanel.vue";
import { comfyInputUrl, comfyOutputUrl } from "@/services/comfyApi";
import {
  findPlanShotDialogue,
  planDialogueForShot,
  buildCharacterVoiceOptions,
  buildTtsLinesPayload,
  withCustomVoiceOption,
  type DialogueLineView,
} from "@/core/voiceCast";
import type { TtsVoiceInfo } from "@/services/comfyApi";
import { shotFingerprint } from "./shotFingerprint";
import { loadSampleProject, loadSampleWorkflow } from "@/fixtures";
import {
  BUILTIN_WORKFLOW_ID,
  DEFAULT_DIRECTOR_NODE_ID,
  WORKFLOW_REGISTRY_KEY,
  buildBuiltinEntry,
  buildWorkflowEntry,
  loadWorkflowRegistry,
  parseWorkflowJson,
  resolveActiveWorkflow,
  type ActiveWorkflow,
  type WorkflowEntry,
} from "@/core/workflowRegistry";
import { findDirectorNode, readWorkflowSeed, rollRandomSeed } from "@/core/workflowStudio";
import { buildTimelineStructure, resolveFlattenedShots } from "@/core/directorCore";
import {
  ETA_HISTORY_KEY,
  CONF_LEVEL_DOT,
  CONF_LEVEL_LABEL,
  appendEtaHistory,
  buildGenMeta,
  computeRunEta,
  confidenceLevel,
  etaIntervalMs,
  formatEtaInterval,
  formatEtaMs,
  framesForDuration,
  lastMsForShot,
  loadEtaHistory,
  normalizeTaskKey,
  phaseStepLabel,
  projectTimeStats,
  type GenHistoryRecord,
  type GenMeta,
  type ProjectTimeStats,
} from "@/core/generationEta";
import {
  clearInFlight,
  loadInFlight,
  saveInFlight,
  type InFlightSnapshot,
  type InFlightSnapshotBase,
} from "@/core/inFlightSnapshot";
import { buildAssetIndex, resolveMentions } from "@/core/assetResolver";
import { buildShotPromptText } from "@/core/promptSections";
import {
  buildOverridesByShot,
  buildDurationsFromProject,
  buildBindingsFromProject,
  mapPlanPromptsToShots,
  rebuildPlanFromProject,
} from "@/core/rebuildPlan";
import { resolveFailedShotIds } from "@/core/failedShots";
import {
  parsePromptTokens,
  insertMediaTag,
  insertNumberedMediaTag,
  findAtTriggerStart,
  buildMediaTagCandidates,
  filterCandidates,
  type MediaTagCandidate,
  type MediaTagKind,
  type MediaTagSource,
} from "@/core/promptMediaTags";
import { resolveInheritance, shotCastIds, type InheritedAsset } from "@/core/inheritanceResolver";
import { shotReviewStatus, type ShotReviewStatus } from "@/core/shotReviewStatus";
import { findCameraTemplate, fillCameraTemplate } from "@/core/cameraTemplates";
import { BUILD_ID } from "@/core/buildId";
import { importProjectFromTimeline, serializeProject, deserializeProject } from "@/services/projectStore";
import type { AssetBinding, AssetScanResult, CameraTemplate, DirectorIntentDto, H3PromptItem, H3PromptResult, ProjectSummary, PromptDraftResult, ScriptImportResult, SnapshotSummary, StoryBibleJson, StoryImportResult } from "@/services/comfyApi";
import type { Asset, AssetKind, H3PromptSnapshot, Project, Scene, Shot, ShotGenRecord, VoiceTypeId } from "@/models/project";
import {
  planSummary,
  productionPlanToProject,
  appendPlanAsEpisode,
  type ConfirmedAssetMatch,
} from "@/core/productionPlanToProject";
import { RATIOS, findComboForSize } from "./resolutionPresets";
import { collectPlayableShotIds, type PlayScope } from "./playQueue";
import { pendingCastNames, sourceSummary } from "@/core/importFeedback";

const wb = useWorkbench();
const episode = computed(() => getCurrentEpisode());
const scene = computed(() => getCurrentScene());
const shot = computed(() => getCurrentShot());
const importInput = ref<HTMLInputElement | null>(null);

// ---------- V1.10-C 顶栏常驻横幅：1s 心跳 + 运行任务 ETA 快照 ----------
const nowTick = ref(Date.now());
const bannerOpen = ref(false);
let bannerTimer: ReturnType<typeof setInterval> | null = null;
/** 查看任务详情：+1 通知 TaskCenter 打开面板（信号量 prop）。 */
const taskCenterSignal = ref(0);

/** 顶栏常驻横幅数据：运行中生成任务 + ETA 快照 + 预测区间 + 可信度三级。 */
const runBanner = computed(() => {
  if (!wb.running) return null;
  const t = wb.tasks.find((x) => x.id === wb.currentTaskId && x.status === "running");
  if (!t) return null;
  const total = Math.max(t.shotCount, 1);
  const idx = Math.min(Math.max(t.currentIndex ?? 0, 0), total - 1);
  const progress = t.progress;
  const eta = t.genMeta ? computeRunEta(t.genMeta, progress, t.currentIndex, t.startedAt, nowTick.value) : null;
  const interval = eta ? etaIntervalMs(eta.remainingMs, eta.confidence) : null;
  const confLevel = eta ? confidenceLevel(eta.confidence) : 0;
  const elapsed = eta?.elapsedMs ?? (t.startedAt ? nowTick.value - t.startedAt : 0);
  const currentShotId = t.shotOrder?.[idx] ?? t.targetShotIds?.[idx] ?? "";
  return {
    task: t,
    idx,
    total,
    currentShotId,
    progress,
    eta,
    interval,
    confLevel,
    confDot: CONF_LEVEL_DOT[confLevel],
    elapsed,
    shotName: shotNameOf(currentShotId),
  };
});

/** 横幅进度条百分比（overall 段进度；无则用当前镜阶段进度估算）。 */
const bannerPct = computed(() => {
  const b = runBanner.value;
  if (!b) return 0;
  const p = b.progress;
  if (p && p.overallMax > 0) return Math.max(0, Math.min(100, (p.overallValue / p.overallMax) * 100));
  if (b.eta) return Math.round(b.eta.shotProgress * 100);
  return Math.round((b.idx / b.total) * 100);
});

/** 横幅阶段文案（真实 step：Sampling · Step 18/30，不伪造百分比）。 */
const bannerPhaseLabel = computed(() =>
  runBanner.value?.progress ? phaseStepLabel(runBanner.value.progress) : "准备中…",
);

/** 横幅「🎬 Shot 07/09」格式。 */
const bannerShotLabel = computed(() => {
  const b = runBanner.value;
  if (!b) return "";
  return `Shot ${String(b.idx + 1).padStart(2, "0")}/${String(b.total).padStart(2, "0")}`;
});

/** 横幅剩余区间文案（约剩 2–4 分钟）。 */
const bannerEtaText = computed(() => {
  const b = runBanner.value;
  if (!b?.interval) return "…";
  return formatEtaInterval(b.interval.lo, b.interval.hi);
});

/** 预计完成区间（HH:MM–HH:MM）。 */
const bannerFinishText = computed(() => {
  const b = runBanner.value;
  if (!b?.interval) return "—";
  const lo = new Date(nowTick.value + b.interval.lo);
  const hi = new Date(nowTick.value + b.interval.hi);
  const p = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${p(lo)}–${p(hi)}`;
});

/** 顶栏主标题 = 项目名（首页重命名后进入工作台立即同步显示，不再只显示「第一集」）。 */
const projectName = computed(() => wb.project?.name ?? episode.value?.title ?? "");

// ---------- V1.10-D 历史耗时：Shot 卡「⏱ 上次生成」+ 项目层统计 ----------

/** 镜头最近一次成功生成耗时（特征匹配：taskType+分辨率+帧数±2；无记录返回 null）。 */
function lastMsOfShot(s: Shot): number | null {
  const frames = framesForDuration(Math.max(0.1, s.durationSec), wb.frameRate);
  return lastMsForShot(readEtaHistory(), {
    taskType: normalizeTaskKey(s.generation?.taskType),
    frames,
    width: wb.outputSize.width,
    height: wb.outputSize.height,
    projectId: wb.project?.id ?? "",
  });
}

/** Shot 卡「⏱ 上次生成」文案（7m 18s / 45s / 无记录为空）。 */
function lastGenTextOf(s: Shot): string {
  const ms = lastMsOfShot(s);
  if (!ms) return "";
  const totalSec = Math.round(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return m > 0 ? `${m}m ${String(sec).padStart(2, "0")}s` : `${sec}s`;
}

/** 项目层耗时统计（平均/最快/最慢/已生成 N 镜）。 */
const projectStats = computed<ProjectTimeStats | null>(() =>
  projectTimeStats(readEtaHistory(), wb.project?.id ?? ""),
);

/** V1.10-D：TaskCenter 历史样本（每秒随 nowTick 刷新，供队列每镜实测耗时查询）。 */
const etaHistoryForTaskCenter = computed<GenHistoryRecord[]>(() => {
  void nowTick.value;
  return readEtaHistory();
});

/** 项目层统计耗时格式化（7m 18s / 45s）。 */
function fmtStats(ms: number): string {
  const totalSec = Math.max(1, Math.round(ms / 1000));
  const m = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return m > 0 ? `${m}m ${String(sec).padStart(2, "0")}s` : `${sec}s`;
}

/** 项目层「已生成 N/M 镜」：stats.count 算样本数，M=全项目镜头数。 */
const projectGeneratedText = computed(() => {
  const ep = episode.value;
  const total = ep?.scenes.reduce((a, s) => a + s.shots.length, 0) ?? 0;
  const n = projectStats.value?.count ?? 0;
  return total > 0 ? `已生成 ${n}/${total} 镜` : "";
});

// ---------- V1.3.1 播放队列（惰性加载，@ended 自动播下一镜）----------
const playList = ref<string[]>([]);
const playIdx = ref(0);
const playStatus = ref("");
let activeBlobUrl: string | null = null;

function revokeActiveBlob(): void {
  if (activeBlobUrl) {
    URL.revokeObjectURL(activeBlobUrl);
    activeBlobUrl = null;
  }
}

function shotNameOf(shotId: string): string {
  return flattenedShots().find((x) => x.id === shotId)?.name ?? shotId;
}

/** 加载并播放队列第 i 个镜头（fetch segment_mp4 → blob → 中央播放器）。
 *  #454：单镜加载失败不再中断整条队列——跳过该镜继续播下一镜，全部失败才报错。 */
async function loadPlayIndex(i: number): Promise<void> {
  const shotId = playList.value[i];
  if (!shotId) return;
  const idx = segIndexOf(shotId);
  if (idx < 0) return;
  revokeActiveBlob();
  try {
    const { blob, filename } = await wb.runService.api.segmentMp4(directorNodeId.value, idx, wb.frameRate);
    activeBlobUrl = URL.createObjectURL(blob);
    playIdx.value = i;
    playStatus.value = `播放 ${i + 1}/${playList.value.length} · ${shotNameOf(shotId)}`;
    setFinalVideo({ url: activeBlobUrl, filename });
  } catch {
    // #454：该镜视频缺失/加载失败——若队列还有后续镜头则跳到下一镜继续播；
    // 否则整条队列失败，给出明确提示。
    const next = i + 1;
    if (next < playList.value.length) {
      playStatus.value = `跳过 ${shotNameOf(shotId)}（视频不可用）· 继续播放`;
      await loadPlayIndex(next);
    } else {
      playStatus.value = "";
      setError("没有可播放的视频（已生成镜头对应的视频文件无法加载）");
    }
  }
}

/** 播放入口统一收集可播镜头并顺序播放。scope：shot=单镜 / scene=场景 / all=全片。 */
async function playVideo(scope: PlayScope, opts: { targetShotId?: string; sceneId?: string } = {}): Promise<void> {
  if (!episode.value) return;
  // 播放可用性只由「该镜是否有成功缓存」决定（collectPlayableShotIds 按 cached 集合过滤，
  // 正在生成且无缓存的镜头自然被排除）。生成中应允许播放其它已生成镜头——之前 `wb.running`
  // 一刀切禁播导致「镜头三生成完→点镜头一生成→镜头三播放按钮/入口被锁」（#83）。
  // 播放依赖已生成缓存：先刷新一次段状态，避免首次进入 segStatus 为空误报「无已生成镜头」。
  await refreshSegStatus();
  const shots = flattenedShots();
  const cached = new Set(wb.segStatus?.cached ?? []);
  const sceneShotIds = opts.sceneId
    ? (episode.value.scenes.find((s) => s.id === opts.sceneId)?.shots ?? []).map((s) => s.id)
    : undefined;
  const ids = collectPlayableShotIds(shots, cached, scope, { targetShotId: opts.targetShotId, sceneShotIds });
  if (!ids.length) {
    setError(scope === "shot" ? "该镜头尚未生成（无缓存）" : "没有已生成的镜头可播放（先在本节点生成）");
    return;
  }
  playList.value = ids;
  playStatus.value = "";
  await loadPlayIndex(0);
}

/** 播放器 @ended：播完当前镜自动播下一镜；播完清空队列。 */
function onPlayerEnded(): void {
  const next = playIdx.value + 1;
  if (next < playList.value.length) {
    void loadPlayIndex(next);
  } else {
    revokeActiveBlob();
    playList.value = [];
    playStatus.value = "";
  }
}

/** 生成开始时清掉回看队列（finalVideo 会被生成结果覆盖）。 */
function clearPlayback(): void {
  revokeActiveBlob();
  playList.value = [];
  playStatus.value = "";
}

/** 当前视图：项目选择页 / 分镜工作台。 */
const view = ref<"home" | "workbench">("home");
const projects = ref<ProjectSummary[]>([]);
const snapshots = ref<SnapshotSummary[]>([]);
const homeBusy = ref(false);
const homeError = ref("");
const savedTip = ref(false);

// ---------- V1.6-C 项目/集改名 inline 编辑 ----------
const renamingProjectId = ref<string | null>(null);
const renamingProjectInput = ref("");
const projectRenameInputRef = ref<HTMLInputElement | null>(null);
const renamingEpisode = ref(false);
const episodeTitleInput = ref("");
const episodeTitleInputRef = ref<HTMLInputElement | null>(null);

const ratioOptions = RATIOS;

// ---------- V1.2：场景/镜头编辑 + @资产识别 + 继承徽标 ----------

const sceneMode = computed(() => wb.currentPane === "scene" && !!scene.value);

/** 当前镜头实际所属场景（镜头面板用，兼容镜头跨场景）。 */
const shotScene = computed(() => {
  if (!shot.value || !episode.value) return scene.value;
  return episode.value.scenes.find((s) => s.id === shot.value!.sceneId) ?? scene.value;
});

const sceneCast = computed<Asset[]>(() => scene.value?.assets?.cast ?? []);
const sceneLocs = computed<Asset[]>(() => scene.value?.assets?.locations ?? []);
const sceneProps = computed<Asset[]>(() => scene.value?.assets?.props ?? []);
const sceneStyles = computed<Asset[]>(() => scene.value?.assets?.styles ?? []);
const globalCast = computed<Asset[]>(() => episode.value?.assets?.cast ?? []);
const globalLocs = computed<Asset[]>(() => episode.value?.assets?.locations ?? []);
const globalProps = computed<Asset[]>(() => episode.value?.assets?.props ?? []);
const globalStyles = computed<Asset[]>(() => episode.value?.assets?.styles ?? []);

/** 场景素材组（四类，用于场景面板渲染）。 */
const sceneAssetGroups = computed(() => [
  { kind: "cast" as const, label: "人物", items: sceneCast.value },
  { kind: "location" as const, label: "地点", items: sceneLocs.value },
  { kind: "prop" as const, label: "道具", items: sceneProps.value },
  { kind: "style" as const, label: "风格", items: sceneStyles.value },
]);

/** 全局资产库（四类，V1.5-P0-A 素材库全局池渲染）。 */
const globalAssetGroups = computed(() => [
  { kind: "cast" as const, label: "人物", items: globalCast.value },
  { kind: "location" as const, label: "地点", items: globalLocs.value },
  { kind: "prop" as const, label: "道具", items: globalProps.value },
  { kind: "style" as const, label: "风格", items: globalStyles.value },
]);

/** 全资产索引（全局 + 当前场景，四类，@ 解析用）。 */
const assetIndex = computed(() =>
  buildAssetIndex([
    ...globalCast.value,
    ...globalLocs.value,
    ...globalProps.value,
    ...globalStyles.value,
    ...sceneCast.value,
    ...sceneLocs.value,
    ...sceneProps.value,
    ...sceneStyles.value,
  ]),
);

/** @命中列表（基于当前镜头 Prompt）。 */
const mentionHits = computed(() =>
  shot.value ? resolveMentions(shot.value.content.visual ?? "", assetIndex.value) : [],
);

const mentionSet = computed(() => new Set(mentionHits.value.map((h) => h.assetId)));

/** 继承解析（当前镜头有效资产 + 来源链）。 */
const inheritance = computed(() => {
  if (!shot.value || !shotScene.value || !episode.value) return [];
  return resolveInheritance({
    shot: shot.value,
    scene: shotScene.value,
    episode: episode.value,
    mentionAssetIds: mentionSet.value,
  });
});

/** V1.6-B：cast 为集合（多角色），其余单选/集合沿用。 */
const castInherited = computed(() => inheritance.value.filter((i) => i.asset.kind === "cast"));
const locInherited = computed(() => inheritance.value.find((i) => i.asset.kind === "location"));
const propInherited = computed(() => inheritance.value.filter((i) => i.asset.kind === "prop"));
const styleInherited = computed(() => inheritance.value.filter((i) => i.asset.kind === "style"));

// ---- P0-2：场景素材组（候选池，非单镜默认注入）----

/** 场景素材组条目（四类展开，工序「📦 场景素材组 ▸」折叠区数据源）。
 *  P0-1 起这组资产不再整池注入「👤 资产」与生成 refs；只作场景级素材管理与
 *  <picture> 标签匹配候选。有图=有图/无图=b-scene-mat 徽标提示。 */
const sceneMaterialGroups = computed(() => {
  const sc = scene.value;
  const groups: { label: string; assetKind: AssetKind; items: Asset[] }[] = [
    { label: "人物", assetKind: "cast", items: sc?.assets?.cast ?? [] },
    { label: "地点", assetKind: "location", items: sc?.assets?.locations ?? [] },
    { label: "道具", assetKind: "prop", items: sc?.assets?.props ?? [] },
    { label: "风格", assetKind: "style", items: sc?.assets?.styles ?? [] },
  ];
  return groups.filter((g) => g.items.length);
});
/** 场景素材组折叠开关（默认收起：低频管理入口，不挤占单镜动线）。 */
const sceneMaterialOpen = ref(false);
/** 场景素材组总条目数（无资产时标题后显示「无素材」）。 */
const sceneMaterialCount = computed(() =>
  sceneMaterialGroups.value.reduce((n, g) => n + g.items.length, 0),
);

// ---- V1.7 Phase 4：AI 制作计划审核态（aiDraft → adopted，V17 §9.0）----

/** 待审核（未采纳）AI 草稿镜头。审核门槛数据源：AI 不直接开拍，逐镜采纳后才可生成。 */
const pendingReviewShots = computed(() =>
  flattenedShots().filter((s) => s.aiDraft && !s.adopted),
);
/** 审核中（有草稿且未全部采纳）→ 显示横幅。 */
const reviewActive = computed(() =>
  flattenedShots().some((s) => s.aiDraft && !s.adopted),
);

/** #370：剧本原文折叠区（含 visual_intent 备注 + 逐字原文/对白，供审核对照 AI 草稿是否忠实）。
 *  默认展开（导演审核参照高频）；无 description 的镜头不渲染该区。 */
const scriptOriginalOpen = ref(true);

/** 单镜审核就绪判定（复用 shotReviewStatus 纯函数）。
 *  对非当前选中镜，独立 resolveInheritance（各自 @命中 + 所属场景池），
 *  保证左侧卡片状态不受「当前选中镜」影响。 */
function reviewStatusOf(s: Shot): ShotReviewStatus {
  if (!episode.value) return { ok: false, issues: [] };
  const sc = episode.value.scenes.find((x) => x.id === s.sceneId);
  if (!sc) return { ok: false, issues: [] };
  const mentions = resolveMentions(s.content?.visual ?? "", assetIndex.value);
  const inherited = resolveInheritance({
    shot: s,
    scene: sc,
    episode: episode.value,
    mentionAssetIds: new Set(mentions.map((m) => m.assetId)),
  });
  return shotReviewStatus(s, {
    castIds: shotCastIds(s),
    assets: inherited.map((i) => i.asset),
  });
}

/** 采纳单镜（store 原子操作；aiDraft 保留为历史元数据）。 */
function adoptDraft(shotId: string): void {
  adoptAiDraft(shotId);
}

// ---- V1.7 Phase 4-C：补资产弹窗（⚠「资产未找到」→ AssetConfirmDialog 从共享资产库补选）----

/** 解析单镜 asset 类缺口（供 AssetConfirmDialog 展示与写回）。
 *  角色未绑定 → mode=cast；生效资产缺图 → mode=asset（含地点/道具/风格）。 */
function missingAssetRefs(s: Shot): MissingAssetRef[] {
  if (!episode.value) return [];
  const sc = episode.value.scenes.find((x) => x.id === s.sceneId);
  if (!sc) return [];
  const pool = [
    ...(episode.value.assets?.cast ?? []),
    ...(episode.value.assets?.locations ?? []),
    ...(episode.value.assets?.props ?? []),
    ...(episode.value.assets?.styles ?? []),
    ...(sc.assets?.cast ?? []),
    ...(sc.assets?.locations ?? []),
    ...(sc.assets?.props ?? []),
    ...(sc.assets?.styles ?? []),
  ];
  const out: MissingAssetRef[] = [];
  for (const issue of reviewStatusOf(s).issues) {
    if (issue.kind !== "asset") continue;
    const m = issue.text.match(/[「]([^」]+)[」]/);
    const nm = m?.[1] ?? "";
    if (!nm) continue;
    const pa = pool.find((a) => a.id === nm) ?? pool.find((a) => a.name === nm) ?? null;
    if (issue.text.startsWith("角色")) {
      out.push({
        mode: "cast",
        entityName: nm,
        assetId: pa?.id ?? "",
        poolAsset: pa,
        assetKind: "cast",
        text: issue.text,
      });
    } else {
      out.push({
        mode: "asset",
        entityName: nm,
        assetId: pa?.id ?? "",
        poolAsset: pa,
        assetKind: pa?.kind ?? "location",
        text: issue.text,
      });
    }
  }
  return out;
}

/** 补资产弹窗目标（当前被点开 ⚠ 徽标的镜头）。 */
const assetConfirmTarget = ref<Shot | null>(null);

/** 打开补资产弹窗（仅存在 asset 缺口时）。 */
function openAssetConfirm(s: Shot): void {
  if (wb.running) return;
  if (!missingAssetRefs(s).length) return;
  assetConfirmTarget.value = s;
}

const assetConfirmScene = computed(() =>
  assetConfirmTarget.value
    ? episode.value?.scenes.find((x) => x.id === assetConfirmTarget.value!.sceneId) ?? null
    : null,
);

/** 来源徽标文案。 */
function sourceBadge(src?: string): { label: string; cls: string } | null {
  switch (src) {
    case "shot":
      return { label: "⚡ 镜头指定", cls: "b-shot" };
    case "scene":
      return { label: "↑ 地点默认", cls: "b-scene" };
    case "project":
      return { label: "↑ 项目默认", cls: "b-project" };
    default:
      return null;
  }
}

/** 生效资产来源徽标：集合类（prop/style）地点池→📍地点素材、全局池→🌐全局素材；单选类沿用三态。 */
function sourceBadgeEx(i: InheritedAsset): { label: string; cls: string } | null {
  if (i.asset.kind === "prop" || i.asset.kind === "style") {
    if (i.source === "scene") return { label: "📍 地点素材", cls: "b-scene-mat" };
    if (i.source === "project") return { label: "🌐 全局素材", cls: "b-global-mat" };
    return null;
  }
  return sourceBadge(i.source);
}

/** 地点下拉显示值：手动锁定 → 显式 id；否则「自动继承」。 */
const locSelectValue = computed(() => (shot.value?.locationManual ? shot.value.locationId ?? "" : ""));

/** 角色名称判重键（trim + lowercase；空名回退 id）。与 inheritanceResolver.nameKey 规则一致。 */
function castNameKey(a: { name: string; id: string }): string {
  const n = a.name.trim().toLowerCase();
  return n || a.id;
}

/** V1.6-B 人物 chip：显式 castIds 解析成资产条目（供「人物 [名 ×]」渲染）。
 * 按 canonical 名称去重：场景池与全局池各有一个同名角色（不同 id）时，UI 只显示一次，
 * 从根上杜绝「重复添加同一个角色」。 */
const castSelectedItems = computed<Asset[]>(() => {
  if (!shot.value) return [];
  const pool = new Map(
    [...sceneCast.value, ...globalCast.value].map((a) => [a.id, a] as const),
  );
  const seen = new Set<string>();
  return shotCastIds(shot.value)
    .map((id) => pool.get(id))
    .filter((a): a is Asset => Boolean(a))
    .filter((a) => {
      const key = castNameKey(a);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
});

/** 「＋ 添加人物」可选池：场景+全局 cast，canonical 名称去重（场景池优先）且排除已显式选择的（防重复添加/重复展示）。 */
const castOptions = computed<Asset[]>(() => {
  const selected = new Set(castSelectedItems.value.map((a) => castNameKey(a)));
  const seen = new Set<string>();
  const out: Asset[] = [];
  for (const a of [...sceneCast.value, ...globalCast.value]) {
    const key = castNameKey(a);
    if (selected.has(key) || seen.has(key)) continue;
    seen.add(key);
    out.push(a);
  }
  return out;
});

/** 添加人物下拉开合状态（chip 行内 popover）。 */
const castPickerOpen = ref(false);

/** Prompt @ 高亮 + 媒体标签 chip 渲染（下层渲染，textarea 透明文字叠上层）。 */
/** 标签名 → 候选元数据（缩略图 URL，Prompt 框内 chip 用）。 */
const mediaChipMeta = computed(() => {
  const map = new Map<string, MediaTagCandidate>();
  for (const c of allTagCandidates()) {
    map.set(`${c.kind}::${c.name}`, c);
  }
  return map;
});

/** 官方编号标签 <Picture N> → refs.refImages[N-1]（1-based）；视频/音频无缩略图。 */
function numberedThumbUrl(kind: MediaTagKind, num: number): string {
  if (kind !== "picture") return "";
  const ref = shot.value?.refs?.refImages?.find((x) => x.index === num - 1);
  return ref?.imageFile ? comfyInputUrl(ref.imageFile) : "";
}

const promptHighlightHtml = computed(() => {
  const text = shot.value?.content.visual ?? "";
  if (!text) return "";
  const marks: { start: number; end: number; html: string }[] = [];
  // @ 资产命中：chip = 隐形 ghost（原文撑宽，保光标对齐）+ visual（缩略图 + 原文）
  for (const h of mentionHits.value) {
    const visual = `${assetThumbHtml(h.assetId, h.kind)}<span class="media-chip-label">${escapeHtml(text.slice(h.start, h.end))}</span>`;
    marks.push({
      start: h.start,
      end: h.end,
      html: chipHtml(text, h.start, h.end, `mc-asset m-${kindClass(h.kind)}`, visual),
    });
  }
  // 媒体标签 token：chip = 隐形 ghost（原文标签撑宽）+ visual（缩略图 + label）
  for (const t of parsePromptTokens(text)) {
    let imgUrl: string | undefined;
    if (t.numbered) {
      imgUrl = numberedThumbUrl(t.kind, t.num ?? 0);
    } else {
      imgUrl = mediaChipMeta.value.get(`${t.kind}::${t.name}`)?.imgUrl;
    }
    const icon = imgUrl
      ? `<img class="media-chip-thumb" src="${escapeHtml(imgUrl)}" alt="">`
      : `<span class="media-chip-icon">${kindIcon(t.kind)}</span>`;
    marks.push({
      start: t.start,
      end: t.end,
      html: chipHtml(text, t.start, t.end, `mc-${t.kind}`, `${icon}<span class="media-chip-label">${escapeHtml(t.label)}</span>`),
    });
  }
  marks.sort((a, b) => a.start - b.start);
  if (!marks.length) return escapeHtml(text);
  let html = "";
  let last = 0;
  for (const m of marks) {
    if (m.start < last) continue; // 与已渲染区间重叠则跳过
    if (m.start > last) html += escapeHtml(text.slice(last, m.start));
    html += m.html;
    last = m.end;
  }
  if (last < text.length) html += escapeHtml(text.slice(last));
  return html;
});

/** V2 分区编辑器：更多分区折叠开关（摄影/风格/声音/负面）。 */
const sectionsOpen = ref(false);

/** 生成用 prompt 预览：分区合并结果（画面+摄影+风格+声音，按输入语言拼接）。 */
const mergedPromptText = computed(() => (shot.value ? buildShotPromptText(shot.value) : ""));

/**
 * 渲染一个 chip：隐形 ghost 放**原始标签完整文本**（透明，只撑布局宽度，
 * 使高亮层断行点与 textarea 完全一致 → 光标不漂移）+ visual 层
 * （缩略图 + 文字，绝对定位覆盖，不占布局宽度，视觉上贴内容）。
 */
function chipHtml(text: string, start: number, end: number, cls: string, visual: string): string {
  const ghost = escapeHtml(text.slice(start, end));
  return `<span class="media-chip ${cls}"><span class="media-chip-ghost">${ghost}</span><span class="media-chip-visual">${visual}</span></span>`;
}

function kindClass(kind: AssetKind): string {
  switch (kind) {
    case "cast":
      return "cast";
    case "location":
      return "loc";
    case "prop":
      return "prop";
    case "style":
      return "style";
  }
}

/** 资产 kind → 无图时 chip 内占位 emoji（导演台 t-asset 风格）。 */
function assetKindIcon(kind: AssetKind): string {
  switch (kind) {
    case "cast":
      return "👤";
    case "location":
      return "📍";
    case "prop":
      return "🛠";
    case "style":
      return "🎨";
  }
}

/** @资产命中 chip 缩略图 html：从资产库取 imageFile → 15px 缩略图；无图 → emoji 占位。 */
function assetThumbHtml(assetId: string, kind: AssetKind): string {
  const asset = assetIndex.value.assets.find((a) => a.id === assetId);
  const url = asset?.imageFile ? comfyInputUrl(asset.imageFile) : "";
  if (url) return `<img class="media-chip-thumb" src="${escapeHtml(url)}" alt="">`;
  return `<span class="media-chip-icon">${assetKindIcon(kind)}</span>`;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------- V1.2.7：@ 素材自动补全 ----------
const promptInputRef = ref<HTMLTextAreaElement | null>(null);
const completeOpen = ref(false);
const completeCandidates = ref<MediaTagCandidate[]>([]);
const completeFilter = ref("");
const completeIndex = ref(0);
const completeAnchor = ref(-1); // @ 起点（替换用）

/**
 * 补全下拉归属：卡片 Prompt 框 or 放大弹窗 Prompt 框。
 * 模板里两份 .tag-complete 都用 v-if="completeOpen" 判断——必须加 target 区分，
 * 否则弹窗里输入 @ 时**两个下拉同时亮**，卡片那个显示在当前界面外。
 */
const completionTarget = ref<"card" | "modal">("card");

// ---------- Prompt 放大编辑弹窗（V1.2.8）：屏幕居中放大，方便看清缩略图 ----------
const promptModalOpen = ref(false);
const promptModalInputRef = ref<HTMLTextAreaElement | null>(null);
function openPromptModal() {
  promptModalOpen.value = true;
  completionTarget.value = "modal";
  nextTick(() => promptModalInputRef.value?.focus());
}
function closePromptModal() {
  promptModalOpen.value = false;
  closeCompletion();
}

/** 候选按类型分组（人物/地点/道具/风格/参考图/参考视频/参考音频），item 附带全局序号。 */
const completeGroups = computed(() => {
  const map = new Map<string, Array<MediaTagCandidate & { gIndex: number }>>();
  let gIndex = 0;
  for (const c of completeCandidates.value) {
    const arr = map.get(c.kindLabel) ?? [];
    arr.push({ ...c, gIndex });
    gIndex++;
    map.set(c.kindLabel, arr);
  }
  return Array.from(map.entries()).map(([label, items]) => ({ label, items }));
});

function allTagCandidates(): MediaTagCandidate[] {
  return buildMediaTagCandidates(assetIndex.value, shot.value, shotScene.value);
}

/** @ 起点存在 → 打开补全；候选为空时也打开并显示「无匹配素材」提示。 */
function openCompletion(atStart: number, caret: number, text: string) {
  const query = text.slice(atStart + 1, caret);
  const filtered = filterCandidates(allTagCandidates(), query);
  completeOpen.value = true;
  completeCandidates.value = filtered;
  completeFilter.value = query;
  completeAnchor.value = atStart;
  completeIndex.value = 0;
}

function closeCompletion() {
  completeOpen.value = false;
  completeCandidates.value = [];
  completeFilter.value = "";
  completeIndex.value = 0;
  completeAnchor.value = -1;
}

function onPromptInput(e: Event) {
  const el = e.target as HTMLTextAreaElement;
  // 记录补全归属：当前输入的是卡片框还是放大弹窗框
  completionTarget.value = el === promptModalInputRef.value ? "modal" : "card";
  const v = el.value;
  if (shot.value) updateShotContent(shot.value.id, v);
  const caret = el.selectionStart ?? v.length;
  const atStart = findAtTriggerStart(v, caret);
  if (atStart >= 0) openCompletion(atStart, caret, v);
  else closeCompletion();
}

/** 选中候选：资产 → <kind>名字</kind>；参考素材 → 官方编号标签 <Picture N>（不显示文件名）。 */
function pickCandidate(c: MediaTagCandidate) {
  if (!shot.value) return;
  const el = promptModalOpen.value && promptModalInputRef.value ? promptModalInputRef.value : promptInputRef.value;
  const text = shot.value.content.visual ?? "";
  const caret = el?.selectionStart ?? text.length;
  const r = c.source === "ref" && c.refIndex != null
    ? insertNumberedMediaTag(text, caret, c.kind, c.refIndex + 1)
    : insertMediaTag(text, caret, c.kind, c.name);
  updateShotContent(shot.value.id, r.text);
  closeCompletion();
  if (el) {
    nextTick(() => {
      el.focus();
      el.setSelectionRange(r.caret, r.caret);
    });
  }
}

function onPromptKeydown(e: KeyboardEvent) {
  // Esc：先关 @ 补全下拉，再关放大弹窗。
  if (e.key === "Escape") {
    if (completeOpen.value) {
      e.preventDefault();
      closeCompletion();
    } else if (promptModalOpen.value) {
      e.preventDefault();
      closePromptModal();
    }
    return;
  }
  if (!completeOpen.value) return;
  const len = completeCandidates.value.length;
  if (len <= 0) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    completeIndex.value = (completeIndex.value + 1) % len;
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    completeIndex.value = (completeIndex.value - 1 + len) % len;
  } else if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    pickCandidate(completeCandidates.value[completeIndex.value]);
  }
}

/** 切换镜头/场景时关闭补全和放大弹窗，避免残留脏状态。 */
watch([shot, scene], () => {
  closeCompletion();
  if (promptModalOpen.value) promptModalOpen.value = false;
  // V1.11：切镜头清空「正在查看的版本」→ 版本条高亮回落（无 active 时不显示快照）。
  viewedGenId.value = null;
  // #456：切镜头清空时长编辑草稿（避免把上一镜的草稿带入新镜头输入框）。
  durationDraft.value = null;
});

/** 补全下拉辅助：类型图标 / 来源标记 / 缩略图加载失败隐藏。 */
function kindIcon(kind: MediaTagKind): string {
  return kind === "picture" ? "🖼" : kind === "video" ? "🎬" : "🎵";
}

/** Prompt 框 chip 缩略图加载失败 → 替换成类型 emoji 占位（v-html 注入内容无法直接 @error）。 */
function onChipImgError(e: Event) {
  const img = e.target as HTMLImageElement;
  if (!(img instanceof HTMLImageElement) || !img.className.includes("media-chip-thumb")) return;
  const chip = img.closest(".media-chip") as HTMLElement | null;
  const kind: MediaTagKind = chip?.classList.contains("mc-video")
    ? "video"
    : chip?.classList.contains("mc-audio")
      ? "audio"
      : "picture";
  const fallback = document.createElement("span");
  fallback.className = "media-chip-icon";
  fallback.textContent = kindIcon(kind);
  img.replaceWith(fallback);
}

function srcLabel(source: MediaTagSource): string {
  return source === "scene" ? "📍地点" : source === "global" ? "🌐全局" : "参考";
}

function hideEl(e: Event) {
  (e.target as HTMLElement).style.display = "none";
}
/** 当前比例下拉值：直接取 store 显式状态（custom = 自定义手输宽高）。 */
const activeRatioId = computed(() => wb.ratioId);

/** MP 输入框显示值：组合选中时显示档位；否则按当前尺寸反推近似 MP。 */
const mpInputValue = computed<string>(() => {
  if (wb.ratioId === "custom") return "";
  if (wb.mpTier != null) return String(wb.mpTier);
  const hit = findComboForSize(wb.outputSize.width, wb.outputSize.height);
  if (hit) return String(hit.mp);
  return ((wb.outputSize.width * wb.outputSize.height) / 1_000_000).toFixed(1);
});

/** 解析 MP 输入：无效时反推当前尺寸档位，再兜底 0.9。 */
function parseMpInput(): number {
  const v = Number(mpInputValue.value);
  if (Number.isFinite(v) && v > 0) return v;
  const hit = findComboForSize(wb.outputSize.width, wb.outputSize.height);
  if (hit) return hit.mp;
  return 0.9;
}

function onRatioChange(e: Event) {
  const id = (e.target as HTMLSelectElement).value;
  if (id === "custom") {
    // 切自定义：保留当前宽高给用户手动改
    setOutputSize(wb.outputSize.width, wb.outputSize.height);
    return;
  }
  applyResolutionCombo(id, parseMpInput());
}

function onMpChange(e: Event) {
  const v = Number((e.target as HTMLInputElement).value);
  const ratio = activeRatioId.value === "custom" ? "16-9" : activeRatioId.value;
  if (!Number.isFinite(v) || v <= 0) {
    // 无效输入：恢复显示当前有效 MP。
    (e.target as HTMLInputElement).value = mpInputValue.value;
    return;
  }
  applyResolutionCombo(ratio, v);
}

function onFpsChange(e: Event) {
  const v = Number((e.target as HTMLInputElement).value);
  if (Number.isFinite(v)) setFrameRate(v);
}

/** 把工作台帧率同步进 workflow：Director 节点 frame_rate + CreateVideo 节点 fps。 */
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

function shotsOf(sceneId: string) {
  return episode.value?.scenes.find((s) => s.id === sceneId)?.shots ?? [];
}

// ---------- P0-7 Story Timeline（2026-08-14 用户拍板）----------
// 中间区 Storyboard 升级为「全片播放顺序」：按 episode.timeline 拍平（同一 Scene 可被
// 交叉引用多次），场景色条 + 场景名标签让「大堂→后院→大堂」交叉剪辑直观可见；
// 卡片可拖拽排序（HTML5 drag），落点=目标卡位置，写回 store.reorderTimeline 并置脏。

/** 场景配色板：按场景在 episode 中的顺序取色（稳定不闪烁）。 */
const TL_SCENE_COLORS = ["#4a7dff", "#3ecf6e", "#ffa94d", "#e0559c", "#a06bff", "#22c1c8", "#f2553b", "#c8b000"];

/** 全片拍平播放顺序（timeline 缺省=场景树顺序，与生成执行顺序一致）。 */
const timelineShots = computed<Shot[]>(() =>
  episode.value ? resolveFlattenedShots(episode.value) : [],
);

/** 场景名（时间线卡标签）。 */
function tlSceneName(sceneId: string): string {
  return episode.value?.scenes.find((s) => s.id === sceneId)?.name ?? "";
}

/** 场景色（按场景顺序稳定取色）。 */
function tlSceneColor(sceneId: string): string {
  const scenes = episode.value?.scenes ?? [];
  const i = scenes.findIndex((s) => s.id === sceneId);
  return i < 0 ? "#666666" : TL_SCENE_COLORS[i % TL_SCENE_COLORS.length];
}

const tlDragId = ref<string | null>(null);
const tlDropOverId = ref<string | null>(null);

function onTlDragStart(shotId: string, e: DragEvent): void {
  tlDragId.value = shotId;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", shotId);
  }
}

function onTlDragOver(targetId: string): void {
  tlDropOverId.value = targetId;
}

function onTlDrop(targetId: string): void {
  const from = tlDragId.value;
  tlDropOverId.value = null;
  if (!from || from === targetId) return;
  const idx = timelineShots.value.findIndex((s) => s.id === targetId);
  if (idx < 0) return;
  reorderTimeline(from, idx);
  tlDragId.value = null;
}

function onTlDragEnd(): void {
  tlDragId.value = null;
  tlDropOverId.value = null;
}

// ---------- P1-A-5（#525）：剧情概览 + 地点/人物筛选 ----------
// Timeline 上移到中间主区顶部成为主视图：顶部显示「N 镜 · N 地点 · N 人物 · 总时长」概览，
// 地点/人物下拉筛选时间线卡（筛选只影响显示；拖拽重排仍走完整 timeline 索引 onTlDrop）。
// 人物解析走继承链路（resolveInheritance cast），与 Shot 卡片「👤 资产」显示一致。

/** 筛选常量：未筛选（与下拉 option 的 value 对应）。 */
const TL_FILTER_ALL = "__all__";

const tlLocFilter = ref<string>(TL_FILTER_ALL);
const tlCastFilter = ref<string>(TL_FILTER_ALL);

/** 本镜有效 cast 名称（经继承解析，asset id → display name，去重去空）。 */
function tlCastNamesOf(shot: Shot): string[] {
  const sc = episode.value?.scenes.find((x) => x.id === shot.sceneId);
  if (!sc || !episode.value) return [];
  const inh = resolveInheritance({
    shot,
    scene: sc,
    episode: episode.value,
    mentionAssetIds: new Set(),
  });
  const names = new Set<string>();
  for (const i of inh) {
    if (i.asset?.kind === "cast" && i.asset.name.trim()) names.add(i.asset.name.trim());
  }
  return [...names];
}

/** #640（2026-08-18）：时间线卡片原文摘要（「原文：」段前 24 字，导入项目一眼可见新剧本内容）。 */
function tlSummaryOf(shot: Shot): string {
  return sourceSummary(shot, 24);
}

/** 一次计算内为时间线各镜缓存 cast 名称（避免同一 computed 多次全量继承解析）。 */
function tlCastNamesMap(shots: Shot[]): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const s of shots) m.set(s.id, tlCastNamesOf(s));
  return m;
}

/** 地点筛选选项：有镜头的场景（场景名即地点名，旧项目 sceneId 兼容映射 locationId）。 */
const tlLocOptions = computed<Scene[]>(() => {
  const scenes = episode.value?.scenes ?? [];
  return scenes.filter((sc) => (sc.shots?.length ?? 0) > 0);
});

/** 人物筛选选项：时间线各镜有效 cast 名称并集（保证每项至少命中一镜）。 */
const tlCastOptions = computed<string[]>(() => {
  const set = new Set<string>();
  for (const names of tlCastNamesMap(timelineShots.value).values()) {
    for (const n of names) set.add(n);
  }
  return [...set].sort((a, b) => a.localeCompare(b, "zh"));
});

/** 筛选后的时间线卡（显示用；onTlDrop 拖拽重排用完整 timelineShots 索引，筛选下不乱序）。 */
const filteredTimelineShots = computed<Shot[]>(() => {
  const loc = tlLocFilter.value;
  const cast = tlCastFilter.value;
  if (loc === TL_FILTER_ALL && cast === TL_FILTER_ALL) return timelineShots.value;
  const castMap = cast === TL_FILTER_ALL ? null : tlCastNamesMap(timelineShots.value);
  return timelineShots.value.filter((s) => {
    if (loc !== TL_FILTER_ALL && s.sceneId !== loc) return false;
    if (cast !== TL_FILTER_ALL && !castMap!.get(s.id)?.includes(cast)) return false;
    return true;
  });
});

/** 是否有筛选生效（决定「✕ 清除筛选」按钮显隐）。 */
const filterActive = computed<boolean>(() => tlLocFilter.value !== TL_FILTER_ALL || tlCastFilter.value !== TL_FILTER_ALL);

/** 地点数（时间线不同 sceneId 去重）。 */
const tlLocCount = computed<number>(() => new Set(timelineShots.value.map((s) => s.sceneId)).size);

/** 人物数（时间线各镜有效 cast 名称去重）。 */
const tlCastCount = computed<number>(() => {
  const set = new Set<string>();
  for (const names of tlCastNamesMap(timelineShots.value).values()) {
    for (const n of names) set.add(n);
  }
  return set.size;
});

/** 总时长（分:秒，空时返回 ""）。 */
const tlTotalDur = computed<string>(() => {
  const total = timelineShots.value.reduce((acc, s) => acc + (s.durationSec || 0), 0);
  if (!total) return "";
  const m = Math.floor(total / 60);
  const sec = Math.round(total % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
});

function clearTlFilters(): void {
  tlLocFilter.value = TL_FILTER_ALL;
  tlCastFilter.value = TL_FILTER_ALL;
}

async function onMount() {
  // 不再自动载入示例工程——先进「项目选择页」，连接 ComfyUI 拉最近项目与快照。
  // V1.8-2：恢复手动导入的工作流注册表 + 上次激活项。
  restoreWorkflowRegistry();
  await refreshHome();
}
onMounted(onMount);

// 进入工作台时拉一次段状态（显示已有缓存 + 状态灯）。
watch(view, (v) => {
  if (v === "workbench" && episode.value) {
    void refreshSegStatus();
  }
});

// ---------- 项目选择页（home）----------

// ---------- V1.4-P1 WS 连接指示器（三/四态圆点）----------

/** 圆点样式类：connected=常绿 / recovered=绿+脉冲 / reconnecting=橙+闪烁 / disconnected=灰。 */
function wsDotClass(s: typeof wb.wsStatus): string {
  if (s === "connected") return "on";
  if (s === "recovered") return "on pulse";
  if (s === "reconnecting") return "reconn";
  return "";
}

/** 指示器文案：home 页用长文案（含 ComfyUI），工作台用短文案。 */
function wsLabel(s: typeof wb.wsStatus, home: boolean): string {
  switch (s) {
    case "connected":
      return home ? "已连接 ComfyUI" : "已连接";
    case "reconnecting":
      return "连接断开 · 正在重连";
    case "recovered":
      return "已恢复";
    default:
      return "未连接";
  }
}

/** 指示器悬停提示（仅断线重连给说明，避免暴露技术细节）。 */
function wsHint(s: typeof wb.wsStatus): string {
  return s === "reconnecting" ? "与 ComfyUI 的连接已断开，正在自动重连…" : "";
}

/** 连接 ComfyUI + 拉最近项目/快照列表。 */
async function refreshHome() {
  homeBusy.value = true;
  homeError.value = "";
  try {
    try {
      await connectComfy();
    } catch {
      // 未连接也允许展示本地缓存；项目/快照接口失败单独降级为空数组。
    }
    const api = wb.runService.api;
    const [p, s] = await Promise.all([
      api.listProjects().catch(() => [] as ProjectSummary[]),
      api.listSnapshots().catch(() => [] as SnapshotSummary[]),
    ]);
    projects.value = p;
    snapshots.value = s;
  } catch (err) {
    homeError.value = err instanceof Error ? err.message : String(err);
  } finally {
    homeBusy.value = false;
  }
}

/** 打开已保存项目（GET ProjectModel → 反序列化 → 进工作台）。 */
async function openProject(pid: string) {
  homeBusy.value = true;
  homeError.value = "";
  try {
    const raw = await wb.runService.api.loadProject(pid);
    loadProject(deserializeProject(JSON.stringify(raw)));
    view.value = "workbench";
    // V1.10-B：项目打开后检查在途快照（浏览器刷新/关闭前生成中）→ 恢复「正在 ComfyUI 生成」态。
    restoreInFlight(pid);
  } catch (err) {
    homeError.value = `打开项目失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    homeBusy.value = false;
  }
}

/** 删除项目（确认后调后端，成功后刷新列表）。 */
async function removeProject(pid: string) {
  const p = projects.value.find((x) => x.id === pid);
  if (!confirm(`删除项目「${p?.name ?? pid}」？此操作不可恢复。`)) return;
  try {
    await wb.runService.api.deleteProject(pid);
    projects.value = projects.value.filter((x) => x.id !== pid);
  } catch (err) {
    homeError.value = `删除项目失败：${err instanceof Error ? err.message : String(err)}`;
  }
}

/** 删除快照（确认后调后端，成功后刷新列表）。 */
async function removeSnapshot(sid: string) {
  const s = snapshots.value.find((x) => x.id === sid);
  if (!confirm(`删除快照「${s?.name ?? sid}」？此操作不可恢复。`)) return;
  try {
    await wb.runService.api.deleteSnapshot(sid);
    snapshots.value = snapshots.value.filter((x) => x.id !== sid);
  } catch (err) {
    homeError.value = `删除快照失败：${err instanceof Error ? err.message : String(err)}`;
  }
}

/** 首页卡片进入改名态：✎ → inline input。 */
function startProjectRename(p: ProjectSummary) {
  renamingProjectId.value = p.id;
  renamingProjectInput.value = p.name;
  nextTick(() => {
    // 模板 ref 用在 v-for 里会被收集成数组，取首个元素 focus。
    const el = Array.isArray(projectRenameInputRef.value)
      ? projectRenameInputRef.value[0]
      : projectRenameInputRef.value;
    el?.focus();
  });
}

/**
 * 首页卡片确认改名：load→改 name→save→更新本地列表（纯前端往返，不改后端）。
 * Enter / ✓ / blur 都可能触发，用 renamingProjectId 守卫幂等。
 */
async function confirmProjectRename(pid: string) {
  if (renamingProjectId.value !== pid) return; // 幂等：enter+blur 双触发只跑一次
  const name = renamingProjectInput.value.trim();
  if (!name) {
    renamingProjectId.value = null;
    return;
  }
  try {
    const raw = await wb.runService.api.loadProject(pid);
    const project = JSON.parse(JSON.stringify(raw)) as Record<string, unknown>;
    project.name = name;
    await wb.runService.api.saveProject(pid, project);
    const target = projects.value.find((x) => x.id === pid);
    if (target) target.name = name;
    if (wb.project?.id === pid) renameProject(name); // 恰好是当前项目则同步 store
  } catch (err) {
    homeError.value = `改名失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    renamingProjectId.value = null;
  }
}

/** 取消首页卡片改名（Esc / 清空提交前取消）。 */
function cancelProjectRename() {
  renamingProjectId.value = null;
}

/** 工作台顶栏集标题进入改名态。 */
function startEpisodeRename() {
  episodeTitleInput.value = episode.value?.title ?? "";
  renamingEpisode.value = true;
  nextTick(() => episodeTitleInputRef.value?.focus());
}

/** 确认集标题改名（store 原子操作 + touch 置脏，走保存链路）。 */
function confirmEpisodeRename() {
  if (!renamingEpisode.value) return;
  renameEpisode(episodeTitleInput.value);
  renamingEpisode.value = false;
}

/** 取消集标题改名。 */
function cancelEpisodeRename() {
  renamingEpisode.value = false;
}

/** 新建项目（后端建空项目 → 打开）。 */
async function createNewProject() {
  homeBusy.value = true;
  homeError.value = "";
  try {
    const summary = await wb.runService.api.createProject("未命名项目");
    await openProject(summary.id);
  } catch (err) {
    homeError.value = `新建项目失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    homeBusy.value = false;
  }
}

/** 从 ComfyUI 生成快照导入：timeline_data → ProjectModel → 另存为正式项目 → 进工作台。 */
async function importSnapshot(sid: string) {
  homeBusy.value = true;
  homeError.value = "";
  try {
    const timeline = await wb.runService.api.loadSnapshot(sid);
    const { project, meta } = importProjectFromTimeline(timeline);
    project.id = `snap_${sid}`;
    project.name = sid;
    await wb.runService.api.saveProject(project.id, JSON.parse(serializeProject(project)) as Record<string, unknown>);
    loadProject(project);
    applyImportedSize(meta.output.width || meta.width, meta.output.height || meta.height);
    setFrameRate(meta.frameRate || 24);
    setError(null);
    view.value = "workbench";
  } catch (err) {
    homeError.value = `导入 ComfyUI 工程失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    homeBusy.value = false;
  }
}

/** 导入后按矩阵命中回填比例档位；未命中才落自定义。 */
function applyImportedSize(width: number, height: number): void {
  const combo = findComboForSize(width, height);
  if (combo) {
    applyResolutionCombo(combo.ratioId, combo.mp);
  } else {
    setOutputSize(width, height);
  }
}

// ---------- V1.7 Commit 3：剧本导入（规则拆 + Qwen 补全 → ProductionPlan 审核预览）----------

/** 剧本导入弹窗状态。 */
const scriptModalOpen = ref(false);
const scriptText = ref("");
const scriptFileName = ref("");
const scriptFileInput = ref<HTMLInputElement | null>(null);
const scriptBusy = ref(false);
const scriptError = ref("");
const scriptResult = ref<ScriptImportResult | null>(null);
/** #640（2026-08-18）：导入应用成功后的顶栏反馈横幅（「已应用《…》：N 镜 · 角色待绑定」）。 */
const importBanner = ref<{ title: string; shotCount: number; locCount: number; pendingCasts: string[] } | null>(null);
/** 审核摘要：场景数 / 镜头数 / 角色数 / 总时长。 */
const scriptSummaryText = computed(() =>
  scriptResult.value ? planSummary(scriptResult.value.plan) : null,
);

// ---------- P1-B-5（#534）：小说章节导入（自然语言零标记 → Beats 审核预览）----------

/** 导入弹窗页签（📜 剧本 / 📖 小说章节 并存，互不干扰状态）。 */
const importTab = ref<"script" | "story">("script");

/** 小说章节导入弹窗状态。 */
const storyText = ref("");
const storyFileName = ref("");
const storyFileInput = ref<HTMLInputElement | null>(null);
const storyBusy = ref(false);
const storyError = ref("");
const storyResult = ref<StoryImportResult | null>(null);

// ---------- P2-P5（#542）：导入目标项目选择（🆕 新建项目 / 已有项目追加新集）----------

/** "new" = 新建项目（默认，P1-B-5 向后兼容）；否则 = 已有项目 id（章节作为新 Episode 追加）。 */
const storyTarget = ref<"new" | string>("new");
/** 追加到已有项目时的目标集号（默认 = 下一集号；可手改）。 */
const storyEpisodeNumber = ref<number | null>(null);
/** 选中已有项目后拉取的 Bible（导入前「本次将注入的跨章上下文」预览来源）。 */
const storyBiblePreview = ref<StoryBibleJson | null>(null);
const storyBiblePreviewLoading = ref(false);

/** 目标已有项目（下拉选中态）。 */
const storyTargetProject = computed(() =>
  storyTarget.value === "new" ? null : projects.value.find((p) => p.id === storyTarget.value) ?? null,
);

/** 已有项目下一集号（集数 + 1；作为追加默认集号）。 */
const storyNextEpisodeNumber = computed(() =>
  storyTargetProject.value ? (storyTargetProject.value.episodes ?? 0) + 1 : 1,
);

/** 导入前上下文预览：目标项目 Bible 中「本次会注入」的已确认条目（身份+状态）。 */
const storyContextPreview = computed(() => {
  const b = storyBiblePreview.value;
  if (!b || !b.entries.length) return [];
  return b.entries
    .filter((e) => e.status === "confirmed")
    .map((e) => ({ name: e.name, current_status: e.current_status }));
});

/** 本次导入对 Bible 的变更列表（后端 bible_updates；仅选择已有项目时返回）。 */
const storyBibleUpdates = computed(() => storyResult.value?.bible_updates ?? []);

/** 选择已有项目 → 拉取 Bible 供上下文预览。 */
watch(storyTarget, async (t) => {
  if (t === "new") {
    storyBiblePreview.value = null;
    storyEpisodeNumber.value = null;
    return;
  }
  storyBiblePreviewLoading.value = true;
  storyEpisodeNumber.value = storyNextEpisodeNumber.value;
  try {
    const res = await wb.runService.api.getBible(t);
    storyBiblePreview.value = res.bible;
  } catch {
    storyBiblePreview.value = null;
  } finally {
    storyBiblePreviewLoading.value = false;
  }
});

/** 小说审核摘要（复用剧本 summary：地点/镜头/角色/时长）。 */
const storySummaryText = computed(() =>
  storyResult.value ? planSummary(storyResult.value.plan) : null,
);

/** 当前页签 busy（脚部按钮联动）。 */
const importBusy = computed(() => (importTab.value === "script" ? scriptBusy.value : storyBusy.value));

/** 当前页签可否「应用为项目」。 */
const importCanApply = computed(() =>
  importTab.value === "script" ? !!scriptResult.value : !!storyResult.value,
);

/** dramatic_function → 中文 chip 标签（审核预览只读展示）。 */
function dramaticFunctionLabel(fn: string): string {
  const map: Record<string, string> = {
    introduce_character: "人物登场",
    dialogue: "对白",
    plant_clue: "埋设伏笔",
    introduce_threat: "引入威胁",
    confrontation: "对峙",
    action: "动作",
    reveal: "揭示",
    emotional: "情绪",
    transition: "过渡",
  };
  return map[fn] ?? (fn || "未标注");
}

/** Beat 所属镜头数（entries 长度；空则 0）。 */
function beatShotCount(b: { entries?: string[] }): number {
  return b.entries?.length ?? 0;
}

/** P2-P5（#542）：BibleUpdate.kind → 中文 chip 标签（导入弹窗变更列表）。 */
function bibleUpdateLabel(kind: string): string {
  const map: Record<string, string> = {
    new_candidate: "新候选",
    reused: "复用",
    status_change: "状态变更",
    alias_merge: "别名合并",
  };
  return map[kind] ?? kind;
}

/** 小说正文里出现的全部地点（按 scenes 出现顺序，含 scene_id/location_name + 镜头数）。 */
const storyLocations = computed(() =>
  (storyResult.value?.plan.scenes ?? []).map((sc) => ({
    scene_id: sc.scene_id,
    name: sc.title || sc.location_name || sc.scene_id,
    time: sc.time,
    weather: sc.weather,
    shots: sc.shots.length,
  })),
);

/** 播放顺序摘要：timeline 逐条 → 可读标签（缺省按场景顺序拍平）。 */
const storyTimelineSummary = computed(() => {
  const plan = storyResult.value?.plan;
  if (!plan) return [];
  const shotById = new Map<string, string>();
  for (const sc of plan.scenes) {
    for (const sh of sc.shots) {
      shotById.set(`${sc.scene_id}:${sh.shot_id}`, `${sc.title || sc.scene_id} · ${sh.source_text.slice(0, 24)}`);
    }
  }
  const refs = plan.timeline?.length
    ? plan.timeline
    : plan.scenes.flatMap((sc) => sc.shots.map((sh) => `${sc.scene_id}:${sh.shot_id}`));
  return refs.map((ref, i) => ({ order: i + 1, ref, label: shotById.get(ref) ?? ref }));
});

// ---------- V1.7 Phase 2：资产自动匹配（规则扫描共享资产库，pending 人工确认）----------

/** 资产匹配结果（解析剧本成功后自动扫描共享资产库；失败不阻塞审核）。 */
const scriptMatch = ref<AssetScanResult | null>(null);
const scriptMatchBusy = ref(false);
const scriptMatchError = ref("");
/** pending 条目的用户确认：`${kind}:${name}` → 候选 index（选择后该实体确认绑定）。 */
/** pending 确认状态：候选索引（number）或「接受建议」（"accept"；⚠ 建议参考 的 [接受] 按钮）。 */
const scriptConfirm = ref<Record<string, number | "accept">>({});

/** V1.7 Phase 2-1（#135）：已落盘的人工确认（entity_key → binding）。重新导入同一剧本时
 *  后端 scan 已返回 match_kind="persisted"/status="auto"，这里仅用于 UI 回显「已确认」徽标。 */
const scriptBindings = ref<Record<string, AssetBinding>>({});

/** Phase 2-1：某条匹配是否「复用上次已落盘的人工确认」（persisted / binding_status=accepted）。 */
function scriptBindingAccepted(m: AssetScanResult["matches"][number]): boolean {
  return m.match_kind === "persisted" || m.binding_status === "accepted";
}

/** 统计：auto 自动 / pending 待确认 / none 未匹配。 */
const scriptMatchCounts = computed(() => {
  const c = { auto: 0, pending: 0, none: 0 };
  for (const m of scriptMatch.value?.matches ?? []) {
    if (m.status === "auto") c.auto += 1;
    else if (m.status === "pending") c.pending += 1;
    else c.none += 1;
  }
  return c;
});

function scriptMatchKey(m: { kind: string; name: string }): string {
  return `${m.kind}:${m.name}`;
}

/** V1.7 Phase 1.1：匹配结果分组（角色 → 地点 → 道具），供导入弹窗分组展示。 */
const SCRIPT_MATCH_GROUP_DEFS: { kind: string; label: string }[] = [
  { kind: "cast", label: "角色" },
  { kind: "location", label: "地点" },
  { kind: "prop", label: "道具" },
];

/** V1.7 Phase 1.1：matches 按 kind 分组（空组不显示）。 */
const scriptMatchGroups = computed(() =>
  SCRIPT_MATCH_GROUP_DEFS.map((g) => ({
    label: g.label,
    items: (scriptMatch.value?.matches ?? []).filter((m) => m.kind === g.kind),
  })).filter((g) => g.items.length > 0),
);

/** 实体抽取加固（#357）：实体类型 → 中文 chip 标签。 */
function scriptEntityTypeLabel(t: string): string {
  const map: Record<string, string> = {
    character: "角色",
    location: "地点",
    prop: "道具",
    costume: "服装",
    environment: "环境",
    effect: "特效",
    architecture: "建筑",
    vehicle: "载具",
    creature: "生物",
    unknown: "未知",
  };
  return map[t] ?? t;
}

/** 实体抽取加固（#357）+ Phase 1.1：漏斗文案「AI 发现 X → 丢弃 Y → 清洗 Z → 进入 W
 *  → a 自动 · p 待确认 · u 未匹配」（+ 无需资产匹配 v 视觉元素）。
 *  旧后端无 funnel 时回退到 scriptMatchCounts 简版。 */
const scriptFunnelText = computed(() => {
  const f = scriptMatch.value?.funnel;
  if (f) {
    const dropped = f.invalid_dropped + f.dedup_dropped;
    const chain = [`AI 发现 ${f.discovered}`];
    if (dropped > 0) chain.push(`丢弃 ${dropped}`);
    chain.push(`清洗 ${f.cleansed}`, `进入 ${f.entered}`);
    const tail = [`${f.auto} 自动`, `${f.pending} 待确认`, `${f.none} 未匹配`];
    if (f.visual_only > 0) tail.push(`视觉元素 ${f.visual_only}`);
    return `${chain.join(" → ")} → ${tail.join(" · ")}`;
  }
  const c = scriptMatchCounts.value;
  return `${c.auto} 自动 · ${c.pending} 待确认 · ${c.none} 未匹配`;
});

/** 实体抽取加固（#357）：进入匹配的实体置信度不足（0.60~0.85，can_auto=false）时的提示。
 *  后端会把这类精确命中也压到 pending，这里帮用户解释为什么。 */
function scriptEntityGateNote(m: AssetScanResult["matches"][number]): string {
  if (m.can_auto === false && m.entity_source === "script") {
    return `实体置信 ${Math.round(m.entity_confidence * 100)}%（<85%）→ 需确认`;
  }
  if (m.entity_source === "inferred") {
    return "AI 推断实体（不进匹配）";
  }
  return "";
}

/** pending 条目当前选中的候选（未选/已选「接受建议」返回 undefined）。 */
function scriptChosenCandidate(
  m: AssetScanResult["matches"][number],
): { name: string; image_file: string; score: number } | undefined {
  const ci = scriptConfirm.value[scriptMatchKey(m)];
  if (typeof ci !== "number") return undefined;
  return m.candidates[ci] ?? undefined;
}

/** pending 下拉选择候选 → 记入 scriptConfirm（响应式替换触发重渲染）。 */
function onConfirmAsset(m: AssetScanResult["matches"][number], e: Event) {
  const v = (e.target as HTMLSelectElement).value;
  if (v === "") return; // 空选 = 未确认（不绑定）
  scriptConfirm.value = { ...scriptConfirm.value, [scriptMatchKey(m)]: Number(v) };
}

/** ⚠ 建议参考 的 [接受] 按钮：绑定当前建议资产（asset_name/image_file，Phase 1.1）。 */
function onAcceptSuggest(m: AssetScanResult["matches"][number]) {
  if (!m.asset_name || !m.image_file) return;
  scriptConfirm.value = { ...scriptConfirm.value, [scriptMatchKey(m)]: "accept" as const };
}

/** ⚠ 建议参考 的 [重新选择] 按钮：清除接受态，退回候选下拉。 */
function onRechooseSuggest(m: AssetScanResult["matches"][number]) {
  const key = scriptMatchKey(m);
  const next = { ...scriptConfirm.value };
  delete next[key];
  scriptConfirm.value = next;
}

/** 该条目当前是「建议已接受」态（Phase 1.1：suggest + [接受]）。 */
function scriptSuggestAccepted(m: AssetScanResult["matches"][number]): boolean {
  return scriptConfirm.value[scriptMatchKey(m)] === "accept";
}

/** Phase 2：调 /assets/scan 匹配剧本实体（纯规则零显存；失败仅提示不阻塞审核）。 */
async function scanAssetsForPlan(plan: ScriptImportResult["plan"]) {
  scriptMatchBusy.value = true;
  scriptMatchError.value = "";
  scriptConfirm.value = {};
  try {
    scriptMatch.value = await wb.runService.api.scanAssets(plan);
    // Phase 2-1（#135）：拉取已落盘确认，供 UI 回显「已确认」徽标。持久化复用本身由
    // 后端 scan 完成（accepted binding → match_kind=persisted/status=auto），这里纯展示。
    try {
      const res = await wb.runService.api.fetchAssetBindings();
      scriptBindings.value = res.bindings ?? {};
    } catch {
      scriptBindings.value = {};
    }
  } catch (err) {
    scriptMatch.value = null;
    scriptMatchError.value = `资产匹配失败（不影响审核）：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    scriptMatchBusy.value = false;
  }
}

// ---------- V1.7 Phase 3（P0-C）：AI Draft 五区草稿（纯规则 + 模板 Registry，零显存）----------

/** 五区 AI 草稿（/prompt/draft 输出；解析成功后自动生成，供审核预览，应用后进工作台可编辑）。 */
const scriptDraft = ref<PromptDraftResult | null>(null);
const scriptDraftBusy = ref(false);
const scriptDraftError = ref("");

/** Phase 3：调 /prompt/draft 生成每镜五区草稿（纯规则零显存；失败仅提示不阻塞审核）。 */
async function scanPromptDrafts(plan: ScriptImportResult["plan"]) {
  scriptDraftBusy.value = true;
  scriptDraftError.value = "";
  try {
    scriptDraft.value = await wb.runService.api.buildPromptDrafts(plan);
  } catch (err) {
    scriptDraft.value = null;
    scriptDraftError.value = `Prompt 草稿生成失败（不影响审核）：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    scriptDraftBusy.value = false;
  }
}

/** 将五区草稿按 `scene_id:shot_id` 展平为 productionPlanToProject 的 drafts 入参。 */
function buildConfirmedDrafts(): PromptDraftResult["drafts"] {
  return scriptDraft.value?.drafts ?? [];
}

/** 五区展示标签（审核预览只读展示用）。 */
const SCRIPT_DRAFT_SECTIONS: { key: keyof PromptDraftResult["drafts"][number]["draft"]; label: string }[] = [
  { key: "visual", label: "画面" },
  { key: "camera", label: "运镜" },
  { key: "style", label: "风格" },
  { key: "sound", label: "声音" },
  { key: "negative", label: "负面" },
];

/** generation_mode 建议徽标标签。 */
function draftModeLabel(mode: string): string {
  return mode === "fl2v" ? "FL2V 强动作" : mode === "r2v" ? "R2V 续接" : "T2V 首镜";
}

// ---------- P0-①（#400）：H3 Prompt 三层可追溯（剧本原文 → AI 理解 → H3 Prompt）----------

/** H3 Prompt 快照（/prompt/h3 输出；解析成功后自动生成，审计预览用，应用后进 Shot.h3Prompt）。 */
const scriptH3 = ref<H3PromptResult | null>(null);
const scriptH3Busy = ref(false);
const scriptH3Error = ref("");
const h3Open = ref(false);

/** scriptDraft.intents（DirectorIntent 投影）按 `scene_id:shot_id` 展平，供 H3 三层回溯第二层用。 */
const scriptH3Intents = computed<Record<string, DirectorIntentDto>>(() => (scriptDraft.value?.intents ?? {}) as Record<string, DirectorIntentDto>);

/** 由当前已确认绑定 → bindings_by_shot（{scene_id:shot_id: {entity_key: {…}}}）。
 *  auto 匹配 + 上次落盘 persisted 复用算已确认；pending 未人工确认跳过（应用时以最终为准）。 */
function buildBindingsByShot(): Record<string, Record<string, { asset_id: string; image_file: string }>> {
  const map = new Map<string, { asset_id: string; image_file: string }>();
  for (const m of buildConfirmedMatches()) {
    if (m.entity_key && m.image_file) {
      map.set(m.entity_key, { asset_id: m.asset_id ?? "", image_file: m.image_file });
    }
  }
  const out: Record<string, Record<string, { asset_id: string; image_file: string }>> = {};
  for (const sc of scriptResult.value?.plan.scenes ?? []) {
    for (const sh of sc.shots ?? []) {
      out[`${sc.scene_id}:${sh.shot_id}`] = Object.fromEntries(map);
    }
  }
  return out;
}

/** 由 plan → duration_by_shot（{scene_id:shot_id: 秒}），进 H3 [0-Ns] 时间轴。 */
function buildDurationByShot(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const sc of scriptResult.value?.plan.scenes ?? []) {
    for (const sh of sc.shots ?? []) {
      const d = Number(sh.duration_sec);
      if (Number.isFinite(d) && d > 0) out[`${sc.scene_id}:${sh.shot_id}`] = d;
    }
  }
  return out;
}

/** H3 Prompt 快照 → 三层可追溯模型快照（订单与 plan 遍历一致：翎本场景 → 镜头原生顺序）。 */
function makeH3Snapshot(item: H3PromptItem): H3PromptSnapshot {
  const intents = scriptDraft.value?.intents ?? {};
  const inv = intents[`${item.scene_id}:${item.shot_id}`];
  return {
    userOriginalIntent: inv?.user_original_intent ?? "",
    intent: {
      retainedFacts: inv?.retained_facts ?? [],
      emotion: inv?.emotion ?? "",
      composition: inv?.composition ?? "",
      cameraDesc: inv?.continuity?.camera_desc ?? "",
      provenance: inv?.provenance ?? {},
    },
    prompt: {
      schema: item.schema,
      integratedDescription: item.integrated_multimodal_description ?? "",
      soundscape: item.overall_soundscape ?? "",
      music: item.non_diegetic_music ?? "",
      references: (item.references ?? []).map((r) => ({
        entityKey: r.entity_key,
        entityName: r.entity_name,
        imageFile: r.image_file,
      })),
      provenance: Object.fromEntries(
        Object.entries(item.provenance ?? {}).map(([k, v]) => [
          k,
          { userFacts: v.user_facts ?? [], aiSupplement: v.ai_supplement ?? [], ruleGenerated: v.rule_generated ?? [] },
        ]),
      ),
    },
  };
}

/** 调 /prompt/h3 生成每镜 H3 Prompt（纯规则零显存；失败仅提示不阻塞审核）。
 *  用当前已确认绑定预览；应用前会以最终绑定重新生成一次。 */
async function scanH3Prompts() {
  const plan = scriptResult.value?.plan;
  if (!plan) return;
  scriptH3Busy.value = true;
  scriptH3Error.value = "";
  try {
    scriptH3.value = await wb.runService.api.buildH3Prompts(plan, buildBindingsByShot(), buildDurationByShot(), undefined, getStyleProfile());
  } catch (err) {
    scriptH3.value = null;
    scriptH3Error.value = `H3 Prompt 生成失败（不影响审核）：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    scriptH3Busy.value = false;
  }
}

/** H3 段落来源徽标标签（provenance.per-key）。 */
function h3ProvLabel(key: string): string {
  return key === "integrated_multimodal_description"
    ? "画面描述"
    : key === "overall_soundscape"
      ? "声音音景"
      : "配乐";
}

/** Workbench 右侧「🧬 H3 三层」折叠开关（只读 shot.h3Prompt）。 */
const h3ShotOpen = ref(false);

/** shot.h3Prompt 已存角色事实（原文逐字层）——右侧折叠第三层徽标计数用。 */
const snapshotFacts = computed<string[]>(() => shot.value?.h3Prompt?.intent.retainedFacts ?? []);

/** shot.h3Prompt 段落级来源标记总条数摘要（如「原文3 · AI2 · 规则1」），供折叠头徽标。 */
const snapshotProvCount = computed(() => {
  const p = shot.value?.h3Prompt?.prompt.provenance;
  if (!p) return "";
  let u = 0;
  let a = 0;
  let r = 0;
  for (const e of Object.values(p)) {
    u += e.userFacts?.length ?? 0;
    a += e.aiSupplement?.length ?? 0;
    r += e.ruleGenerated?.length ?? 0;
  }
  const parts: string[] = [];
  if (u) parts.push(`原文${u}`);
  if (a) parts.push(`AI${a}`);
  if (r) parts.push(`规则${r}`);
  return parts.join(" · ");
});

/** Phase 2-1：由一条匹配结果 → 确认绑定（携带 entity_key/asset_id/sourceEntityId）。 */
function confirmedMatchFrom(
  m: AssetScanResult["matches"][number],
  asset_name: string,
  image_file: string,
  userConfirmed: boolean,
): ConfirmedAssetMatch {
  return {
    kind: m.kind,
    name: m.name,
    asset_name,
    image_file,
    entity_key: m.entity_key,
    asset_id: m.asset_id ?? null,
    sourceEntityId: m.entity_id,
    userConfirmed,
  };
}

/** 由 auto 结果 + 用户确认的 pending 候选 → 传给 productionPlanToProject 的确认绑定列表。 */
function buildConfirmedMatches(): ConfirmedAssetMatch[] {
  const out: ConfirmedAssetMatch[] = [];
  for (const m of scriptMatch.value?.matches ?? []) {
    if (m.status === "auto" && m.matched && m.asset_name && m.image_file) {
      // 复用上次落盘确认（persisted）算「已确认」；纯 auto 算系统自动。
      out.push(confirmedMatchFrom(m, m.asset_name, m.image_file, scriptBindingAccepted(m)));
    } else if (m.status === "pending") {
      // Phase 1.1：⚠ 建议参考 [接受] → 绑定当前建议资产；否则取候选下拉选择。
      if (scriptSuggestAccepted(m)) {
        if (m.asset_name && m.image_file) {
          out.push(confirmedMatchFrom(m, m.asset_name, m.image_file, true));
        }
        continue;
      }
      const cand = scriptChosenCandidate(m);
      if (cand) out.push(confirmedMatchFrom(m, cand.name, cand.image_file, true));
    }
  }
  return out;
}

/** V1.7 Phase 2-1（#135）：把进入项目的确认绑定落盘到 asset_registry.json。
 *  纯规则零显存；失败静默（用户仍可进工作台手动补图，不阻塞应用）。 */
async function persistConfirmedBindings(matches: ConfirmedAssetMatch[]): Promise<void> {
  const api = wb.runService.api;
  for (const m of matches) {
    if (!m.entity_key || !m.image_file) continue;
    try {
      await api.saveAssetBinding({
        entity_key: m.entity_key,
        ...(m.asset_id ? { asset_id: m.asset_id } : {}),
        image_file: m.image_file,
        entity_name: m.name,
        entity_type: m.kind === "cast" ? "character" : m.kind === "location" ? "location" : "prop",
        asset_name: m.asset_name,
        status: "accepted",
        source: m.userConfirmed ? "user" : "system",
      });
    } catch {
      // 落盘失败不阻塞应用
    }
  }
}

/** 打开导入弹窗（每次复位）。 */
function openScriptImport() {
  scriptText.value = "";
  scriptFileName.value = "";
  scriptError.value = "";
  scriptResult.value = null;
  scriptMatch.value = null;
  scriptMatchError.value = "";
  scriptConfirm.value = {};
  scriptDraft.value = null;
  scriptDraftError.value = "";
  scriptH3.value = null;
  scriptH3Error.value = "";
  scriptModalOpen.value = true;
}

/** 关闭导入弹窗。 */
function closeScriptImport() {
  scriptModalOpen.value = false;
}

/** 选择 .md/.txt 文件 → 读文本填入 textarea。 */
function onScriptFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  scriptFileName.value = file.name;
  scriptError.value = "";
  scriptResult.value = null;
  scriptMatch.value = null;
  scriptMatchError.value = "";
  scriptConfirm.value = {};
  scriptDraft.value = null;
  scriptDraftError.value = "";
  scriptH3.value = null;
  scriptH3Error.value = "";
  void file
    .text()
    .then((t) => {
      scriptText.value = t;
    })
    .catch(() => {
      scriptError.value = "读取文件失败。";
    });
}

/** 一步完成：调后端 /script/import（规则拆 + Qwen 补全；Qwen 失败自动降级为规则结果，不阻塞）。 */
async function runScriptImport() {
  const text = scriptText.value.trim();
  if (!text) {
    scriptError.value = "请先粘贴剧本内容，或选择 .md/.txt 文件。";
    return;
  }
  if (scriptBusy.value) return;
  scriptBusy.value = true;
  scriptError.value = "";
  scriptResult.value = null;
  scriptMatch.value = null;
  scriptMatchError.value = "";
  scriptConfirm.value = {};
  scriptDraft.value = null;
  scriptDraftError.value = "";
  try {
    const res = await wb.runService.api.importScript(text, {
      title: scriptFileName.value ? scriptFileName.value.replace(/\.(md|txt)$/i, "") : "",
      sourceFile: scriptFileName.value || "",
      analyze: true,
    });
    scriptResult.value = res;
    // Phase 2：解析成功后自动扫描共享资产库做规则匹配（纯规则零显存；失败不阻塞审核）。
    void scanAssetsForPlan(res.plan);
    // Phase 3：解析成功后自动生成每镜五区 AI 草稿（纯规则 + 模板 Registry，零显存；失败不阻塞审核）。
    void scanPromptDrafts(res.plan);
    // P0-①（#400）：解析成功后自动生成每镜 H3 Prompt 三层可追溯预览（纯规则零显存）。
    void scanH3Prompts();
  } catch (err) {
    scriptError.value = `剧本解析失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    scriptBusy.value = false;
  }
}

/** 审核通过 → 映射为 SPA 项目（带上 Phase 2 资产绑定 + Phase 3 五区草稿）→ 存后端工程文件层 → 进工作台。 */
async function applyScriptProject() {
  const res = scriptResult.value;
  if (!res || scriptBusy.value) return;
  scriptBusy.value = true;
  scriptError.value = "";
  try {
    const confirmed = buildConfirmedMatches();
    // Phase 2-1（#135）：先落盘人工确认（entity_key → asset_id，source=user/system）。
    // 失败静默不阻塞应用（用户仍可进工作台手动补图）。
    await persistConfirmedBindings(confirmed);
    const project = productionPlanToProject(res.plan, {
      matches: confirmed,
      drafts: buildConfirmedDrafts(),
      promptTemplateVersion: scriptDraft.value?.template_version,
    });
    // P0-A（#478）：把原始 ProductionPlan 持久化进项目（source_text 逐字权威源）。
    // 保存→关闭→重开→修改仍保留；生成提交时后端 /prompt/h3 用它重建三段式 H3 Prompt。
    project.plan = res.plan;
    // P0-①（#400）：用**最终绑定**（含人工确认，弹窗预览基于 auto）重新生成 H3 Prompt
    // 三层可追溯快照，zip 进每镜（遍历顺序 = plan 场景/镜头原生顺序，与 H3 prompts 一致）。
    // 失败只丢审计视图，不阻塞应用。
    try {
      const h3 = await wb.runService.api.buildH3Prompts(
        res.plan,
        buildBindingsByShot(),
        buildDurationByShot(),
        undefined,
        getStyleProfile(),
      );
      const snapshots = Object.values(h3.prompts).map((item) => makeH3Snapshot(item));
      let h3i = 0;
      for (const sc of project.episodes[0]?.scenes ?? []) {
        for (const sh of sc.shots) {
          if (snapshots[h3i]) sh.h3Prompt = snapshots[h3i];
          h3i++;
        }
      }
    } catch {
      // H3 快照失败静默（审计视图缺失，生成不受影响）
    }
    await wb.runService.api.saveProject(
      project.id,
      JSON.parse(serializeProject(project)) as Record<string, unknown>,
    );
    loadProject(project);
    setError(null);
    // #640（2026-08-18）：导入应用成功 → 顶栏横幅（镜数/地点数 + 待绑定参考图角色），
    // 让用户一眼确认「这是新剧本」，引导下一步绑定资产。
    importBanner.value = {
      title: res.plan.project.title?.trim() || project.name,
      shotCount: (res.plan.scenes ?? []).reduce((n, sc) => n + (sc.shots?.length ?? 0), 0),
      locCount: new Set((res.plan.scenes ?? []).map((sc) => sc.location_name?.trim()).filter(Boolean)).size,
      pendingCasts: pendingCastNames(res.plan, confirmed),
    };
    view.value = "workbench";
    closeScriptImport();
    void refreshHome();
  } catch (err) {
    scriptError.value = `应用项目失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    scriptBusy.value = false;
  }
}

// ---------- P1-B-5（#534）：小说章节导入方法（与剧本导入平行，状态互不干扰）----------

/** 打开小说导入：切到「📖 小说章节」页签 + 复位。 */
function openStoryImport() {
  importTab.value = "story";
  storyText.value = "";
  storyFileName.value = "";
  storyError.value = "";
  storyResult.value = null;
  // P2-P5（#542）：复位目标项目选择 + Bible 上下文预览。
  storyTarget.value = "new";
  storyEpisodeNumber.value = null;
  storyBiblePreview.value = null;
  scriptModalOpen.value = true;
}

/** 选择 .md/.txt 文件 → 读文本填入 story textarea。 */
function onStoryFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  storyFileName.value = file.name;
  storyError.value = "";
  storyResult.value = null;
  void file
    .text()
    .then((t) => {
      storyText.value = t;
    })
    .catch(() => {
      storyError.value = "读取文件失败。";
    });
}

/** 一步完成：调后端 /story/import（Qwen 整章理解 → Beats → 规则拆 Shot → timeline；
 *  Qwen 不可用自动降级规则（rule_only=true），不阻塞导入）。 */
async function runStoryImport() {
  const text = storyText.value.trim();
  if (!text) {
    storyError.value = "请先粘贴小说章节正文，或选择 .md/.txt 文件。";
    return;
  }
  if (storyBusy.value) return;
  storyBusy.value = true;
  storyError.value = "";
  storyResult.value = null;
  try {
    const res = await wb.runService.api.importStory(text, {
      title: storyFileName.value ? storyFileName.value.replace(/\.(md|txt)$/i, "") : "",
      sourceFile: storyFileName.value || "",
      analyze: true,
      // P2-P5（#542）：选中已有项目 → 后端对该项目 ensure_bible + 注入跨章上下文 +
      // 更新 Bible（响应含 bible_updates 变更列表；无 project_id 时与 P1-B 逐字一致）。
      ...(storyTarget.value !== "new" ? { projectId: storyTarget.value } : {}),
      ...(storyTarget.value !== "new" && storyEpisodeNumber.value
        ? { episodeNumber: storyEpisodeNumber.value }
        : {}),
    });
    storyResult.value = res;
  } catch (err) {
    storyError.value = `小说解析失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    storyBusy.value = false;
  }
}

/** 审核通过 → 应用为项目：
 *  - 目标「🆕 新建项目」（默认，P1-B-5 向后兼容）：映射为 SPA 项目（beats 天然随
 *    project.plan 持久化）→ 新建 id=`story-<ts>` → 存后端工程文件层 → 进工作台。
 *  - 目标「已有项目」（P2-P5 多集管理，一章一集）：loadProject → appendPlanAsEpisode
 *    追加为新一集 → saveProject（⛔ 只动 episodes + plan；Bible 已在后端随导入更新；
 *    不动 Asset Registry / 其它集）→ 进工作台。 */
async function applyStoryProject() {
  const res = storyResult.value;
  if (!res || storyBusy.value) return;
  storyBusy.value = true;
  storyError.value = "";
  try {
    let project: Project;
    if (storyTarget.value !== "new") {
      // P2-P5：追加为已有项目的指定集（默认下一集号）。
      const pid = storyTarget.value;
      const raw = await wb.runService.api.loadProject(pid);
      const base = deserializeProject(JSON.stringify(raw));
      const epNum = storyEpisodeNumber.value ?? storyNextEpisodeNumber.value;
      project = appendPlanAsEpisode(base, res.plan, epNum, res.plan.project.title);
      await wb.runService.api.saveProject(
        pid,
        JSON.parse(serializeProject(project)) as Record<string, unknown>,
      );
    } else {
      project = productionPlanToProject(res.plan, {});
      // P0-A（#478）：把原始 ProductionPlan（含 beats + timeline）持久化进项目。
      project.plan = res.plan;
      const id = `story-${Date.now()}`;
      project.id = id;
      project.name = res.plan.project.title || id;
      await wb.runService.api.saveProject(
        id,
        JSON.parse(serializeProject(project)) as Record<string, unknown>,
      );
    }
    loadProject(project);
    setError(null);
    view.value = "workbench";
    closeScriptImport();
    void refreshHome();
  } catch (err) {
    storyError.value = `应用项目失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    storyBusy.value = false;
  }
}

/**
 * 保存当前项目到后端工程文件层。成功返回 true，失败返回 false（不抛错，错误走 setError）。
 * silent=true 时不闪「已保存」成功提示（生成完成自动保存用，避免队列逐镜闪提示）。
 */
async function saveCurrentProject(silent = false): Promise<boolean> {
  const p = wb.project;
  if (!p) return false;
  try {
    await wb.runService.api.saveProject(p.id, JSON.parse(serializeProject(p)) as Record<string, unknown>);
    markSaved();
    if (!silent) {
      savedTip.value = true;
      setTimeout(() => (savedTip.value = false), 1600);
    }
    return true;
  } catch (err) {
    setError(`保存失败：${err instanceof Error ? err.message : String(err)}`);
    return false;
  }
}

// ---------- V1.4-P0-3 退出保护（最小版：仅 dirty 拦截，运行中任务不阻塞）----------

/** 「← 项目」离开确认框（三选一：保存并离开/不保存离开/取消）。 */
const leaveConfirmOpen = ref(false);
/** 「保存并离开」进行中，防重复点击。 */
const leaveBusy = ref(false);

/** 浏览器刷新/关闭拦截：仅 dirty 时触发原生 beforeunload 确认。 */
function onBeforeUnload(e: BeforeUnloadEvent) {
  if (wb.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
}

// ---------- V1.7 Phase 5（P1-D）运镜模板下拉：后端权威 + 前端拉取 ----------
const cameraTemplates = ref<CameraTemplate[]>([]);
const cameraTemplatesLoaded = ref(false);

/** 拉取运镜模板库（后端 /camera/templates，纯规则零显存）。失败静默降级为手编模式。 */
async function loadCameraTemplates(): Promise<void> {
  try {
    const res = await wb.runService.api.fetchCameraTemplates();
    cameraTemplates.value = res.templates ?? [];
  } catch {
    cameraTemplates.value = [];
  } finally {
    cameraTemplatesLoaded.value = true;
  }
}

/** 模板下拉选中：用 fillCameraTemplate 自动填 cameraText + 记录来源模板 id。 */
function onCameraTemplateSelect(shot: Shot, e: Event) {
  const id = (e.target as HTMLSelectElement).value;
  if (!id) {
    updateShotCameraTemplate(shot.id, "");
    return;
  }
  const tmpl = findCameraTemplate(cameraTemplates.value, id);
  const sc = scene.value;
  if (tmpl && sc) {
    updateShotSection(shot.id, "cameraText", fillCameraTemplate(tmpl, shot, sc));
    updateShotCameraTemplate(shot.id, tmpl.id);
  }
}

/** 摄影分区手编：清空来源模板标记（= 自定义），保住已有 cameraText。 */
function onCameraTextInput(shot: Shot, e: Event) {
  const v = (e.target as HTMLTextAreaElement).value;
  updateShotSection(shot.id, "cameraText", v);
  if (shot.cameraTemplate) updateShotCameraTemplate(shot.id, "");
}

// ---------- Phase 2（#575/#576）：TTS 音色列表（Voice Cast 面板 + 行级微调共享）----------
const ttsVoices = ref<TtsVoiceInfo[]>([]);
const ttsVoicesError = ref("");
const ttsVoicesLoading = ref(false);

/** 拉取引擎可用音色（后端 /tts/voices 静态表，纯规则零显存）。失败显示但不断言。 */
async function loadTtsVoices(): Promise<void> {
  ttsVoicesLoading.value = true;
  ttsVoicesError.value = "";
  try {
    const res = await wb.runService.api.listTtsVoices();
    if (res.ok && Array.isArray(res.voices)) ttsVoices.value = res.voices;
    else if (res.error) ttsVoicesError.value = res.error;
  } catch (err) {
    ttsVoicesError.value = err instanceof Error ? err.message : String(err);
  } finally {
    ttsVoicesLoading.value = false;
  }
}

// ---------- Phase 2-D（#576）：镜头对白行级微调（音色/情绪/语速）----------
const shotDialogueOpen = ref(false);

/** 常见情绪候选（Edge-TTS 无情感模型，Phase 0 仅记录；正式引擎接入后生效）。 */
const DIALOGUE_EMOTIONS = ["平静", "喜悦", "愤怒", "悲伤", "惊讶", "恐惧", "疑惑", "嘲讽", "温柔", "严厉"];
/** 常见语速/语气候选（delivery 含「快/慢」→ rate ±20%，含「电子」→ pitch +5Hz）。 */
const DIALOGUE_DELIVERIES = ["正常", "快", "慢", "电子", "轻柔", "激昂"];

/** 当前镜头对白解析视图（index 1-based + 已解析 voiceId + type）。 */
const shotDialogueLines = computed<DialogueLineView[]>(() => {
  const s = shot.value;
  if (!s?.planShotId) return [];
  return planDialogueForShot(wb.project?.plan, s.sceneId, s.planShotId, getVoiceCast());
});

/** 当前镜头对白原始行（读行级显式覆盖，空 = 自动/全局/规则）。 */
const shotDialogueRaw = computed(() => {
  const s = shot.value;
  if (!s?.planShotId) return null;
  return findPlanShotDialogue(wb.project?.plan, s.sceneId, s.planShotId);
});

/** 行级音色下拉选项：「自动」+ 语义 + 引擎原生。 */
const shotDialogueVoiceOptions = computed(() => buildCharacterVoiceOptions(ttsVoices.value));

function shotDialogueExplicit(index: number): string {
  return (shotDialogueRaw.value?.[index - 1]?.voice_id ?? "") || "";
}
function shotDialogueEmotion(index: number): string {
  return shotDialogueRaw.value?.[index - 1]?.emotion ?? "";
}
function shotDialogueDelivery(index: number): string {
  return shotDialogueRaw.value?.[index - 1]?.delivery ?? "";
}

function onDialogueVoice(index: number, e: Event): void {
  const s = shot.value;
  if (!s?.planShotId) return;
  setDialogueOverride(s.sceneId, s.planShotId, index, { voice_id: (e.target as HTMLSelectElement).value });
}
function onDialogueEmotion(index: number, e: Event): void {
  const s = shot.value;
  if (!s?.planShotId) return;
  setDialogueOverride(s.sceneId, s.planShotId, index, { emotion: (e.target as HTMLInputElement).value });
}
function onDialogueDelivery(index: number, e: Event): void {
  const s = shot.value;
  if (!s?.planShotId) return;
  setDialogueOverride(s.sceneId, s.planShotId, index, { delivery: (e.target as HTMLInputElement).value });
}

function dialogueTypeLabel(t: VoiceTypeId): string {
  switch (t) {
    case "narration": return "旁白";
    case "system_voice": return "系统";
    case "inner_monologue": return "内心";
    default: return "对白";
  }
}

onMounted(() => {
  window.addEventListener("beforeunload", onBeforeUnload);
  void loadCameraTemplates();
  void loadTtsVoices();
  // V1.10-C：顶栏横幅 ETA 心跳（已用/剩余/完成区间实时刷新）。
  bannerTimer = setInterval(() => {
    nowTick.value = Date.now();
  }, 1000);
});
onBeforeUnmount(() => {
  window.removeEventListener("beforeunload", onBeforeUnload);
  if (bannerTimer) {
    clearInterval(bannerTimer);
    bannerTimer = null;
  }
});

/** 实际离开（清项目 + 回项目选择页 + 刷新列表）。 */
function doLeave() {
  clearProject();
  view.value = "home";
  void refreshHome();
}

/** 返回项目选择页。dirty 时先弹三选一确认框。 */
function goHome() {
  if (wb.dirty) {
    leaveConfirmOpen.value = true;
    return;
  }
  doLeave();
}

/** 三选一：保存并离开 → 保存成功后才清理；失败留在当前页面。 */
async function onLeaveSave() {
  if (leaveBusy.value) return;
  leaveBusy.value = true;
  try {
    const ok = await saveCurrentProject();
    if (!ok) return; // 保存失败：不清理、不离开（saveCurrentProject 已 setError）。
    leaveConfirmOpen.value = false;
    doLeave();
  } finally {
    leaveBusy.value = false;
  }
}

/** 三选一：不保存离开 → 正常清理回项目页。 */
function onLeaveDiscard() {
  leaveConfirmOpen.value = false;
  doLeave();
}

/** 三选一：取消 → 什么都不做，留在工作台，dirty 状态不变。 */
function onLeaveCancel() {
  leaveConfirmOpen.value = false;
}

/** 开发工具：载入示例工程（调试用）。深拷贝防污染——否则编辑会写进模块级 JSON 对象。 */
function loadSample() {
  loadProject(JSON.parse(JSON.stringify(loadSampleProject())));
  view.value = "workbench";
}

/** 开发工具：导入 timeline_data 文件（V1.1 反向导入通道，调试入口）。 */
async function onImportTimeline(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const text = await file.text();
    const json = JSON.parse(text) as Record<string, unknown>;
    if (!Array.isArray(json.segments)) throw new Error("不是 timeline_data（缺 segments 数组）");
    const { project, meta } = importProjectFromTimeline(json);
    loadProject(project);
    // 同步 timeline 的分辨率/帧率到工作台会话态。
    applyImportedSize(meta.output.width || meta.width, meta.output.height || meta.height);
    setFrameRate(meta.frameRate || 24);
    setError(null);
    view.value = "workbench";
    // 允许下次选择同一文件时重新触发 change。
    if (importInput.value) importInput.value.value = "";
  } catch (err) {
    setError(`导入失败：${err instanceof Error ? err.message : String(err)}`);
  }
}

// ---------- 分镜工作台（workbench）----------

// ---------- V1.8-2：Workflow Registry（手动工作流选择） ----------

/** 内置默认工作流条目（懒构建缓存；workflow 深拷贝自 sample-workflow.json）。 */
let _builtinEntry: WorkflowEntry | null = null;
function builtinWorkflowEntry(): WorkflowEntry {
  if (!_builtinEntry) {
    const wf = JSON.parse(JSON.stringify(loadSampleWorkflow())) as Record<string, unknown>;
    _builtinEntry = buildBuiltinEntry(wf);
  }
  return _builtinEntry;
}

/** 手动导入的注册表条目（内置默认不在此列表，始终兜底）。 */
const workflowRegistry = ref<WorkflowEntry[]>([]);
/** 当前激活工作流 id（默认内置；持久化跨会话恢复）。 */
const activeWorkflowId = ref<string>(BUILTIN_WORKFLOW_ID);
/** 多节点选择弹窗。 */
const workflowPickOpen = ref(false);
/** 导入工作流弹窗。 */
const workflowImportOpen = ref(false);
const workflowImportText = ref("");
const workflowImportError = ref("");
const workflowImportName = ref("workflow_api");
const workflowImportInput = ref<HTMLInputElement | null>(null);

/** 激活条目（找不到回退内置默认）。 */
const activeEntry = computed<WorkflowEntry>(() => {
  const found = workflowRegistry.value.find((e) => e.id === activeWorkflowId.value);
  return found ?? builtinWorkflowEntry();
});
/** 激活工作流解析态（none/ok/multi → 生成前校验用）。 */
const activeWorkflow = computed<ActiveWorkflow>(() => resolveActiveWorkflow(activeEntry.value));
/** 当前可提交的 Director 节点 id（无效回退内置 "5"，保证缓存读取/导出不传 null）。 */
const directorNodeId = computed<string>(() => activeWorkflow.value.nodeId ?? DEFAULT_DIRECTOR_NODE_ID);

/** 工作流徽标（生成栏内节点状态提示）。 */
const workflowNodeBadge = computed<{ cls: string; text: string; title: string }>(() => {
  const aw = activeWorkflow.value;
  if (aw.status === "none") {
    return {
      cls: "wf-bad",
      text: "❌ 非 H3 工作流",
      title: "此工作流没有 MiniMaxH3Director 节点，不能用于生成",
    };
  }
  if (aw.status === "multi") {
    return {
      cls: "wf-warn",
      text: `⚠️ ${aw.nodeCount} 个节点`,
      title: "检测到多个 MiniMaxH3Director 节点，点击选择生成节点",
    };
  }
  return {
    cls: "wf-ok",
    text: `✓ 节点 ${directorNodeId.value}`,
    title: `生成节点：${directorNodeId.value}`,
  };
});

/** 持久化手动导入的注册表（内置默认不入库）。 */
function persistWorkflowRegistry(): void {
  const manual = workflowRegistry.value.filter((e) => e.source !== "builtin");
  try {
    localStorage.setItem(WORKFLOW_REGISTRY_KEY, JSON.stringify(manual));
  } catch {
    /* 持久化失败静默（隐私模式等） */
  }
}

/** 恢复手动导入注册表 + 上次激活的工作流。 */
function restoreWorkflowRegistry(): void {
  try {
    workflowRegistry.value = loadWorkflowRegistry(localStorage.getItem(WORKFLOW_REGISTRY_KEY));
    const last = localStorage.getItem(`${WORKFLOW_REGISTRY_KEY}.active`);
    if (last && workflowRegistry.value.some((e) => e.id === last)) {
      activeWorkflowId.value = last;
    }
  } catch {
    workflowRegistry.value = [];
  }
}

/** 持久化当前激活工作流 id（刷新后恢复）。 */
function persistActiveWorkflowId(): void {
  try {
    localStorage.setItem(`${WORKFLOW_REGISTRY_KEY}.active`, activeWorkflowId.value);
  } catch {
    /* 忽略（隐私模式等） */
  }
}

function onWorkflowChange(e: Event): void {
  const v = (e.target as HTMLSelectElement).value;
  if (!v) return;
  activeWorkflowId.value = v;
  persistActiveWorkflowId();
  if (resolveActiveWorkflow(activeEntry.value).status === "multi") {
    workflowPickOpen.value = true;
  }
  void refreshSegStatus(); // 切换工作流后按新节点 id 重新读缓存
}

/** 多节点弹窗选择生成节点。 */
function setWorkflowNode(nodeId: string): void {
  const entry = activeEntry.value;
  if (entry) entry.selectedNodeId = nodeId;
  workflowPickOpen.value = false;
  void refreshSegStatus();
}

/** 节点 id → 完整 class_type（多节点弹窗展示用）。 */
function nodeClassLabel(nodeId: string): string {
  const node = activeEntry.value.workflow[nodeId] as { class_type?: unknown } | undefined;
  return node && typeof node.class_type === "string" ? node.class_type : "MiniMaxH3Director";
}

/** 删除当前手动导入的工作流（内置不可删）。 */
function removeActiveWorkflow(): void {
  if (activeWorkflowId.value === BUILTIN_WORKFLOW_ID) return;
  workflowRegistry.value = workflowRegistry.value.filter((e) => e.id !== activeWorkflowId.value);
  activeWorkflowId.value = BUILTIN_WORKFLOW_ID;
  persistWorkflowRegistry();
  persistActiveWorkflowId();
  void refreshSegStatus();
}

function openWorkflowImport(): void {
  workflowImportText.value = "";
  workflowImportError.value = "";
  workflowImportName.value = "workflow_api";
  workflowImportOpen.value = true;
  void nextTick(() => workflowImportInput.value?.focus());
}

/** 选择 workflow_api.json 文件 → 读入文本（文件名为默认条目名）。 */
async function onWorkflowImportFile(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  workflowImportName.value = file.name.replace(/\.json$/i, "") || "workflow_api";
  try {
    workflowImportText.value = await file.text();
    workflowImportError.value = "";
  } catch {
    workflowImportError.value = "读取文件失败";
  }
}

/** 确认导入：解析 → 扫描节点 → 入注册表并激活（多节点自动弹选择）。 */
function confirmWorkflowImport(): void {
  const parsed = parseWorkflowJson(workflowImportText.value);
  if (!parsed.ok || !parsed.workflow) {
    workflowImportError.value = parsed.error ?? "解析失败";
    return;
  }
  const entry = buildWorkflowEntry(parsed.workflow, workflowImportName.value);
  workflowRegistry.value.push(entry);
  activeWorkflowId.value = entry.id;
  persistWorkflowRegistry();
  persistActiveWorkflowId();
  workflowImportOpen.value = false;
  if (entry.directorNodeIds.length > 1) workflowPickOpen.value = true;
  void refreshSegStatus();
}

// ---------- V1.9：Workflow Studio（节点图编辑器 + 快捷参数面板） ----------

/** 完整节点图编辑器弹窗。 */
const workflowStudioOpen = ref(false);
/** 快捷参数面板弹窗。 */
const workflowParamsOpen = ref(false);

/** 编辑器/面板保存：写回当前激活条目并持久化（内置工作流在组件内已自动转另存为）。 */
function onStudioSave(workflow: Record<string, unknown>): void {
  const entry = activeEntry.value;
  if (entry.source === "builtin") return;
  entry.workflow = workflow;
  persistWorkflowRegistry();
  setError(null);
}

/** 另存为：新增条目并激活（内置/手动工作流都能另存）。 */
function onStudioSaveAs(name: string, workflow: Record<string, unknown>): void {
  const entry = buildWorkflowEntry(workflow, name.trim() || "未命名工作流");
  workflowRegistry.value.push(entry);
  activeWorkflowId.value = entry.id;
  persistWorkflowRegistry();
  persistActiveWorkflowId();
  void refreshSegStatus();
}

/** 打开完整节点图编辑器（从快捷面板的「高级」入口进入）。 */
function openWorkflowStudioFromParams(): void {
  workflowParamsOpen.value = false;
  workflowStudioOpen.value = true;
}

/** 生成前把 seed=-1 替换成随机种子（每次生成都不同；0/正数保持固定）。 */
function randomizeSeedIfNeeded(workflow: Record<string, unknown>): void {
  const dir = findDirectorNode(workflow);
  if (!dir) return;
  if (dir.node.inputs.seed === -1) {
    dir.node.inputs.seed = rollRandomSeed();
  }
}

// ---------- V1.12 种子便捷化：全局种子控件（以 Director 节点 inputs.seed 为唯一真相） ----------

/** 当前工作流种子模式：seed=-1 → 随机（每次自动新种子）；否则固定。 */
const seedMode = computed<"random" | "fixed">(() => {
  const s = readWorkflowSeed(activeWorkflow.value.workflow);
  return s === null || s === -1 ? "random" : "fixed";
});

/** 固定模式当前种子值（随机模式显示 0，仅作输入框占位）。 */
const fixedSeed = computed<number>(() => {
  const s = readWorkflowSeed(activeWorkflow.value.workflow);
  return s === null || s === -1 ? 0 : s;
});

/** 写 Director 节点 seed 并持久化工作流（-1 = 随机模式）。 */
function writeSeed(v: number): void {
  const dir = findDirectorNode(activeWorkflow.value.workflow);
  if (!dir) return;
  dir.node.inputs.seed = v;
  persistWorkflowRegistry();
}

/** 🎲 掷一个新种子并固定（换种子重试；随机模式下点击 = 掷出并进入固定模式）。 */
function rollSeed(): void {
  writeSeed(rollRandomSeed());
}

/** 切换 随机/固定：随机→掷一个并固定；固定→改回随机（每次自动新种子）。 */
function toggleSeedMode(): void {
  if (seedMode.value === "random") {
    rollSeed();
  } else {
    writeSeed(-1);
  }
}

/** 固定模式输入框提交（校验：非负整数，越界回退当前值）。 */
function onSeedInput(e: Event): void {
  const el = e.target as HTMLInputElement;
  const v = Math.trunc(Number(el.value));
  if (!Number.isFinite(v) || v < 0) {
    el.value = String(fixedSeed.value);
    return;
  }
  writeSeed(v);
}

/** 「以此种子重放」：把某历史版本的种子写回工作流（固定模式）并重新生成此镜。 */
function replayWithSeed(shotId: string, seed: number): void {
  writeSeed(seed);
  void generateShot(shotId);
}

/** V1.12：当前查看版本的种子（详情面板「以此种子重放」按钮显隐用）。 */
const viewedSeed = computed<number | undefined>(() => viewedGen.value?.params.seed);

/** V1.12：以当前查看版本的种子重新生成本镜（内部守卫，模板不做类型窄化）。 */
function replayViewedSeed(): void {
  const g = viewedGen.value;
  const s = shot.value;
  if (!g || g.params.seed === undefined || !s) return;
  replayWithSeed(s.id, g.params.seed);
}

// ---------- V1.3 生成范围（6 态下拉）----------

/** 全片拍平镜头（段索引 = 该数组下标，与 structure.shots 同序）。 */
function flattenedShots() {
  if (!episode.value) return [];
  return episode.value.scenes.flatMap((sc) => sc.shots.slice().sort((a, b) => a.order - b.order));
}

/** 镜头 id → 段索引（-1 = 不在拍平列表）。 */
function segIndexOf(shotId: string): number {
  return flattenedShots().findIndex((s) => s.id === shotId);
}

/** 当前范围解析 → targetShotIds（null = 全部镜头；空数组 = 无命中）。 */
function resolveTargetShotIds(): string[] | null {
  const shots = flattenedShots();
  switch (wb.genScope) {
    case "all":
      return null;
    case "current":
      return shot.value ? [shot.value.id] : [];
    case "selected":
      return wb.selectedShotIds.filter((id) => shots.some((s) => s.id === id));
    case "scene":
      return scene.value ? scene.value.shots.map((s) => s.id) : [];
    case "pending": {
      const cached = wb.segStatus?.cached ?? [];
      return shots.filter((s) => !cached.includes(segIndexOf(s.id))).map((s) => s.id);
    }
    case "failed": {
      const states = wb.segStatus?.states ?? {};
      return shots.filter((s) => states[String(segIndexOf(s.id))] === "failed").map((s) => s.id);
    }
    // 从此镜继续：当前镜头 + 后续全部（拍平顺序）。走查续拍：修好失败镜后不重选范围直接续。
    case "fromShot": {
      const cur = shot.value;
      if (!cur) return [];
      const idx = shots.findIndex((s) => s.id === cur.id);
      if (idx < 0) return [];
      return shots.slice(idx).map((s) => s.id);
    }
    default:
      return null;
  }
}

/** 当前范围可生成数量（用于按钮计数；null=全部）。 */
const genScopeCount = computed(() => {
  const ids = resolveTargetShotIds();
  return ids === null ? flattenedShots().length : ids.length;
});

const GEN_SCOPE_LABEL: Record<GenScope, string> = {
  all: "全部镜头",
  current: "当前镜头",
  selected: "选中镜头",
  scene: "当前地点",
  pending: "未完成镜头",
  failed: "失败镜头",
  fromShot: "从此镜继续",
};

/** 主按钮禁用：运行中（含导出中），或范围内无镜头可生成。 */
const genScopeDisabled = computed(() => {
  if (wb.running || exportBusy.value || !episode.value) return true;
  const ids = resolveTargetShotIds();
  return Array.isArray(ids) && ids.length === 0;
});

/** V1.7 Phase 4：范围内含未采纳 AI 草稿 → 硬拦截（按钮警示可点，点击弹提示要求先采纳）。 */
const genBlockedByReview = computed(() => reviewScopeUnadoptedCount.value > 0);

/** 当前范围未采纳 AI 草稿数（生成按钮计数/拦截提示）。 */
const reviewScopeUnadoptedCount = computed(() => {
  if (!episode.value) return 0;
  const ids = resolveTargetShotIds();
  const scopeShots =
    ids === null ? flattenedShots() : flattenedShots().filter((s) => ids.includes(s.id));
  return scopeShots.filter((s) => s.aiDraft && !s.adopted).length;
});

/** 审核中（待审核）镜头里就绪（✓）的数量（横幅统计）。 */
const reviewReadyCount = computed(
  () => pendingReviewShots.value.filter((s) => reviewStatusOf(s).ok).length,
);

/** 刷新段状态（状态灯 + pending/failed 范围数据源）。失败静默降级为空。
 * 空闲时（wb.running=false）过滤掉后端残留的 running 状态 ——
 * 生成中断/崩溃后段循环可能没走到 failed 清理，蓝灯会永久闪；
 * 本过滤保证「没在生成就不显示生成中」。 */
async function refreshSegStatus(): Promise<void> {
  const shots = flattenedShots();
  try {
    // shotIds 镜头身份校验：排除旧项目/旧时间线残留缓存，防止播放全片加载旧视频。
    const shotIds = shots.map((s) => s.id);
    const st = await wb.runService.pollSegmentStatus(directorNodeId.value, shotIds);
    const states: Record<string, string> = {};
    for (const [k, v] of Object.entries(st.states ?? {})) {
      if (!wb.running && v === "running") continue;
      states[k] = v;
    }
    setSegStatus(shots.map((s) => s.id), st.cached, states);
  } catch {
    setSegStatus(shots.map((s) => s.id), [], {});
  }
}

/** WS 断线兜底：onPoll 节流用的上次刷新时间戳。 */
let lastSegPollAt = 0;

/** 镜头状态灯 class：cached→成功 / running→生成中 / failed→失败 / review→待检查 / stale→参数已变。 */
function statusClassOf(shotId: string): string {
  const st = wb.segStatus;
  if (!st) return "st-none";
  const idx = st.indexShotIds.indexOf(shotId);
  if (idx < 0) return "st-none";
  const s = st.states[String(idx)];
  if (s === "running") return "st-running";
  if (s === "failed") return "st-failed";
  if (s === "review") return "st-review";
  if (st.cached.includes(idx)) return isShotStale(shotId) ? "st-stale" : "st-done";
  return "st-none";
}

/** 镜头是否「参数已变需重生成」：有缓存、有生成时指纹、但当前参数 ≠ 生成时参数。 */
function isShotStale(shotId: string): boolean {
  const stored = wb.paramHashes[shotId];
  if (!stored) return false;
  const shot = flattenedShots().find((s) => s.id === shotId);
  if (!shot) return false;
  return shotFingerprint(shot, { outputSize: wb.outputSize, frameRate: wb.frameRate }) !== stored;
}

/** 镜头状态灯 tooltip。 */
function statusTitleOf(shotId: string): string {
  const st = wb.segStatus;
  if (!st) return "未生成";
  const idx = st.indexShotIds.indexOf(shotId);
  if (idx < 0) return "未生成";
  const s = st.states[String(idx)];
  if (s === "running") return "生成中";
  if (s === "failed") return "失败";
  if (s === "review") return "待检查";
  if (st.cached.includes(idx)) return isShotStale(shotId) ? "参数已变，需重新生成" : "成功";
  return "未生成";
}

function onGenScopeChange(e: Event) {
  setGenScope((e.target as HTMLSelectElement).value as GenScope);
}

/** 镜头是否已有成功缓存（下载按钮可用）。 */
function isShotCached(shotId: string): boolean {
  const st = wb.segStatus;
  if (!st) return false;
  const idx = st.indexShotIds.indexOf(shotId);
  return idx >= 0 && st.cached.includes(idx);
}

// ---------- V1.8-1：Shot 详情区（V1.11.1 UI 收敛后：纯文字信息卡；视频只在中央唯一播放器播放） ----------

/** 详情区展开/收起。 */
const shotDetailOpen = ref(true);

/** 生成参数速览（详情区底部 chips）。 */
const genInfoLines = computed<{ label: string; val: string }[]>(() => {
  const lines: { label: string; val: string }[] = [];
  const g = shot.value?.generation;
  if (g?.stateChange) lines.push({ label: "状态变化", val: g.stateChange });
  if (g?.smartTail) lines.push({ label: "智能尾帧", val: "开" });
  lines.push({ label: "分辨率", val: `${wb.outputSize.width}×${wb.outputSize.height}` });
  lines.push({ label: "帧率", val: `${wb.frameRate}fps` });
  return lines;
});

function taskTypeLabel(t: string): string {
  return ({ auto: "自动路由", r2v: "R2V 参考生视频", fl2v: "FL2V 首尾帧生视频" } as Record<string, string>)[t] ?? t;
}
function continuityLabel(c: string): string {
  return ({ auto: "自动续接", none: "独立镜头", ref2va: "参考续接", fl2va: "首尾帧续接" } as Record<string, string>)[c] ?? c;
}

// ---------- V1.11 生成历史（版本管理）----------

/** 中央播放器当前「正在查看」的版本 id（null=瞬态播放/预览，未查看具体版本）。
 *  ⭐ 当前成片 = shot.activeGenerationId；🆕 最新 = generations 末位。二者互相独立。 */
const viewedGenId = ref<string | null>(null);

/** 当前镜头的生成版本列表（按生成顺序，末位=最新）。 */
const shotGenerations = computed<ShotGenRecord[]>(() => shot.value?.generations ?? []);
/** 当前镜头「当前成片」版本 id（⭐）。 */
const activeGenId = computed(() => shot.value?.activeGenerationId ?? null);
/** 当前正在查看的版本对象（无则 null）。 */
const viewedGen = computed<ShotGenRecord | null>(() => {
  if (!viewedGenId.value) return null;
  return shotGenerations.value.find((g) => g.id === viewedGenId.value) ?? null;
});

/** 版本视频 URL：归档 input 路径优先（重启不丢），失败回退 output 临时文件。
 *  回退必须带 outputSubfolder（H3 输出在 output/video/ 子目录，缺了 404）。 */
function genVideoUrl(gen: ShotGenRecord): string {
  if (gen.videoFile) return comfyInputUrl(gen.videoFile);
  const q = new URLSearchParams({ type: "output", filename: gen.outputFilename });
  if (gen.outputSubfolder) q.set("subfolder", gen.outputSubfolder);
  return `/view?${q.toString()}`;
}

/** 是否最新生成（末位）。 */
function isNewestGen(gen: ShotGenRecord): boolean {
  const arr = shotGenerations.value;
  return arr.length > 0 && arr[arr.length - 1].id === gen.id;
}

/** 版本序号（1-based 显示）。 */
function genIndex(gen: ShotGenRecord): number {
  return shotGenerations.value.findIndex((g) => g.id === gen.id) + 1;
}

/** 版本时间（HH:MM 或 MM-DD HH:MM）。 */
function genCreatedAt(gen: ShotGenRecord): string {
  const d = new Date(gen.createdAt);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const today = new Date();
  if (
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  ) {
    return hm;
  }
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${hm}`;
}

/** 版本三态标签（🆕 最新 / ⭐ 当前成片 / ○ 历史）。 */
function genStateLabel(gen: ShotGenRecord): string {
  if (activeGenId.value === gen.id) return "⭐ 当前成片";
  if (isNewestGen(gen)) return "🆕 最新生成";
  return "○ 历史版本";
}

/** 点版本卡：主播放器切到该版本（唯一大播放器负责「看视频」，版本条只负责「选版本」）。 */
function viewGeneration(gen: ShotGenRecord): void {
  playerErr.value = "";
  playerRetriedUrl.value = "";
  viewedGenId.value = gen.id;
  setFinalVideo({ url: genVideoUrl(gen), filename: gen.videoFile || gen.outputFilename });
}

/** ⭐ 设为当前成片（不影响当前查看的版本；meta 由 playerMeta 推导）。 */
function setActive(shotId: string, generationId: string): void {
  setActiveGeneration(shotId, generationId);
}

/** 版本条/影院模式「设为当前成片」：把当前正在查看的版本采纳为当前成片。 */
function setActiveCurrent(): void {
  const g = viewedGen.value;
  const s = shot.value;
  if (!g || !s) return;
  setActive(s.id, g.id);
}

/** 主播放器下方 meta：优先当前查看的版本 → 播放队列状态 → 当前成片 → 文件回看。 */
const playerMeta = computed(() => {
  if (viewedGen.value) return `Generation #${genIndex(viewedGen.value)} · ${genStateLabel(viewedGen.value)}`;
  if (playStatus.value) return `🎬 ${playStatus.value}`;
  const active = shotGenerations.value.find((g) => g.id === activeGenId.value);
  if (active) return `Generation #${genIndex(active)} · ⭐ 当前成片`;
  if (wb.finalVideo) return `成片回看：${wb.finalVideo.filename}`;
  return "";
});

// ---------- V1.11.1 UI 收敛：Cinema Mode（唯一放大入口，主播放器右上角 [⛶]） ----------

const cinemaOpen = ref(false);

function openCinema(): void {
  if (!wb.finalVideo) return;
  cinemaOpen.value = true;
}

function closeCinema(): void {
  cinemaOpen.value = false;
}

function onCinemaKey(e: KeyboardEvent): void {
  if (e.key === "Escape") closeCinema();
}

watch(cinemaOpen, (v) => {
  if (v) window.addEventListener("keydown", onCinemaKey);
  else window.removeEventListener("keydown", onCinemaKey);
});

// ---------- #454 视频播放稳定性：播放器错误反馈 + 归档自动回退 ----------

/** 主播放器/Cinema 视频加载失败时的用户可见消息（空=正常）。 */
const playerErr = ref("");
/** 已对哪个 URL 做过「归档回退尝试」——同一 URL 重复失败只报错，不无限切。 */
const playerRetriedUrl = ref("");

/** <video> @error：URL 失效不再无声黑屏/灰卡。
 *  第一优先自动尝试回退到本镜的归档版本（input 目录，重启不丢）；
 *  回退也失败/无归档时给出明确提示（替代黑屏）。 */
function onPlayerVideoError(): void {
  const cur = wb.finalVideo?.url ?? "";
  if (playerRetriedUrl.value === cur) {
    playerErr.value =
      "⚠ 视频文件无法加载（可能已被清理或 ComfyUI 重启后 output 临时文件失效）。" +
      "请选择其它版本，或重新生成该镜。";
    return;
  }
  playerRetriedUrl.value = cur;
  // 找本镜带归档 videoFile 的版本（input 目录，重启不丢）——回退最稳定来源。
  const archived = shotGenerations.value.find((g) => g.videoFile);
  if (archived && genVideoUrl(archived) !== cur) {
    viewedGenId.value = archived.id;
    setFinalVideo({ url: genVideoUrl(archived), filename: archived.videoFile || archived.outputFilename });
    playerErr.value = "原视频不可用，已自动切换至归档版本";
    return;
  }
  playerErr.value =
    "⚠ 视频文件无法加载（可能已被清理或 ComfyUI 重启后 output 临时文件失效）。请重新生成该镜。";
}

/** <video> @loadeddata：加载成功清除错误横幅（并允许后续 URL 变化时再次回退）。 */
function onPlayerVideoLoaded(): void {
  playerErr.value = "";
  playerRetriedUrl.value = "";
}

/** 影院模式版本标签（与主播放器一致：优先当前查看版本）。 */
const cinemaGenLabel = computed(() => {
  const g = viewedGen.value;
  if (g) return `Generation #${genIndex(g)} · ${genStateLabel(g)}`;
  const active = shotGenerations.value.find((x) => x.id === activeGenId.value);
  if (active) return `Generation #${genIndex(active)} · ⭐ 当前成片`;
  return "—";
});

/** Shot 信息卡头部 Generation 徽标（优先当前查看版本，否则当前成片）。 */
const detailGenLabel = computed(() => {
  const g = viewedGen.value;
  if (g) return `Generation #${genIndex(g)} · ${genStateLabel(g)}`;
  const active = shotGenerations.value.find((x) => x.id === activeGenId.value);
  if (active) return `Generation #${genIndex(active)} · ⭐ 当前成片`;
  return "";
});

/** 生成完成后：归档视频 + 创建版本记录 + 播放器切到最新版（🆕）。
 *  首版自动采纳为当前成片；后续版本不自动切换 activeGenerationId。 */
async function createGeneration(
  shotId: string,
  video: { url: string; filename: string; subfolder?: string },
  opts: { t0: number; seed?: number },
): Promise<void> {
  const s = flattenedShots().find((x) => x.id === shotId);
  if (!s) return;
  const generationId = genId();
  let videoFile = "";
  try {
    const res = await wb.runService.api.archiveGeneration({
      projectId: wb.project?.id ?? "",
      shotId,
      generationId,
      filename: video.filename,
      subfolder: video.subfolder ?? "",
    });
    videoFile = res.video_file ?? "";
  } catch {
    videoFile = ""; // 归档失败降级：回退播放 output 临时文件
  }
  const gen: ShotGenRecord = {
    id: generationId,
    createdAt: Date.now(),
    videoFile,
    outputFilename: video.filename,
    outputSubfolder: video.subfolder || undefined,
    prompt: {
      visual: s.content.visual ?? "",
      negativePrompt: s.content.negativePrompt,
      cameraText: s.content.cameraText,
      style: s.content.style,
      soundText: s.content.soundText,
    },
    params: {
      taskType: s.generation?.taskType ?? "auto",
      continuityMode: s.generation?.continuityMode ?? "auto",
      width: wb.outputSize.width,
      height: wb.outputSize.height,
      frameRate: wb.frameRate,
      workflowId: activeWorkflowId.value,
      workflowName: activeWorkflow.value?.entry?.name ?? "",
      // V1.12：记录实际提交的种子（随机模式 = 本次掷出的具体值，可一键以此重放）。
      seed: opts.seed,
    },
    durationMs: Date.now() - opts.t0,
  };
  addGeneration(shotId, gen);
  // 播放器切到最新版（🆕 徽标）；activeGenerationId 不变（首版才自动采纳）。
  playerErr.value = "";
  playerRetriedUrl.value = "";
  viewedGenId.value = gen.id;
  setFinalVideo({ url: genVideoUrl(gen), filename: gen.videoFile || gen.outputFilename });
  void warmGenThumbs();
  // A2（2026-08-14）：生成成功后自动保存项目（版本记录 + Workbench 修改落盘），
  // 不再依赖用户手动点 💾；刷新/重开项目后生成记录仍在。失败仅透出错误不阻塞播放。
  void saveCurrentProject(true);
}

/**
 * #599（2026-08-17）：H3 提交 audioMode 与 Voice Cast 音频总开关联动。
 *  音频开启 → "auto"（H3 自带环境音/BGM 音轨保留）；
 *  音频暂停 → "mute"（后端跳过音频 VAE 解码，输出静音视频，还更快）。
 *  与 autoTtsForShot 同一开关驱动——「音频暂停」= 画面优先，不带任何声音。
 */
function resolveAudioMode(): "auto" | "mute" {
  return getVoiceCast().enabled ? "auto" : "mute";
}

/**
 * Phase 2-E（#577）：每镜 H3 生成完成后自动触发 TTS + 混音。
 *  链路：buildTtsLinesPayload（行级 > Voice Cast 全局 > 规则兜底，voice_id 已全解析）
 *        → POST /tts/synthesize（分层落盘 tts/line_NNN.wav + manifest.json）
 *        → POST /tts/mix（h3_tts；shot 目录缺 video.mp4 时后端用 fv 定位自动落位，幂等）
 *        → 播放器切到混音成片 final.mp4（/view?type=output）。
 *  无台词行（lines 为空）跳过——本镜直接播 H3 成片。
 *  任一环节失败降级：不标记生成失败、不阻塞，只 setError（TaskCenter ⚠ 提示），
 *  播放器保持 H3 成片。纯规则零 LLM 零显存。
 */
async function autoTtsForShot(
  sid: string,
  fv: { url: string; filename: string; subfolder?: string },
): Promise<void> {
  // Phase 2-G（2026-08-17）：音频总开关（Voice Cast 面板头部）。关 = 生成视频
  // 不自动配音/混音（Edge-TTS 不启动），只出画面；后期加音频时重开即可。
  if (!(getVoiceCast().enabled ?? false)) return;
  const s = flattenedShots().find((x) => x.id === sid);
  const projectId = wb.project?.id;
  if (!s?.planShotId || !projectId || !wb.project?.plan) return;
  const lines = buildTtsLinesPayload(wb.project.plan, s.sceneId, s.planShotId, getVoiceCast()).filter(
    (l) => (l.text || "").trim(),
  );
  if (!lines.length) return; // 本镜无台词 → 不触发自动配音
  const shotLabel = shotNameOf(sid);
  try {
    const synth = await wb.runService.api.synthesizeTts({
      project_name: projectId,
      shot_id: s.planShotId,
      scene_id: s.sceneId,
      lines,
    });
    if (!synth.ok) {
      setError(`🎙 ${shotLabel} 自动配音失败（H3 成片不受影响）：${synth.error || "合成失败"}`);
      return;
    }
    const mix = await wb.runService.api.mixTts({
      project_name: projectId,
      shot_id: s.planShotId,
      mode: "h3_tts",
      video_filename: fv.filename,
      video_subfolder: fv.subfolder,
    });
    if (!mix.ok) {
      setError(`🎙 ${shotLabel} 混音失败（H3 成片不受影响）：${mix.error || "混音失败"}`);
      return;
    }
    if (mix.final_rel) {
      const finalName = mix.final_rel.split("/").pop() || mix.final_rel;
      setFinalVideo({ url: comfyOutputUrl(mix.final_rel), filename: finalName });
      playStatus.value = `🎙 ${shotLabel}：自动配音 + 混音完成`;
    }
  } catch (err) {
    setError(
      `🎙 ${shotLabel} 自动配音失败（H3 成片不受影响）：${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

/** 版本快照参数速览（详情面板 chips）。 */
function genParamLines(gen: ShotGenRecord): { label: string; val: string }[] {
  const lines: { label: string; val: string }[] = [];
  lines.push({ label: "任务", val: taskTypeLabel(gen.params.taskType) });
  lines.push({ label: "续接", val: continuityLabel(gen.params.continuityMode) });
  lines.push({ label: "分辨率", val: `${gen.params.width}×${gen.params.height}` });
  lines.push({ label: "帧率", val: `${gen.params.frameRate}fps` });
  if (gen.params.seed !== undefined) lines.push({ label: "种子", val: String(gen.params.seed) });
  if (gen.params.workflowName) lines.push({ label: "工作流", val: gen.params.workflowName });
  if (gen.durationMs) lines.push({ label: "耗时", val: formatEtaMs(gen.durationMs) });
  return lines;
}

/** 复制版本 Prompt 快照到当前草稿（编辑态永远只针对当前草稿）。 */
function copySnapshotToDraft(): void {
  const g = viewedGen.value;
  const s = shot.value;
  if (!g || !s) return;
  updateShotContent(s.id, g.prompt.visual ?? "");
  updateShotNegative(s.id, g.prompt.negativePrompt ?? "");
  updateShotSection(s.id, "cameraText", g.prompt.cameraText ?? "");
  updateShotSection(s.id, "style", g.prompt.style ?? "");
  updateShotSection(s.id, "soundText", g.prompt.soundText ?? "");
  setError("已复制版本 Prompt 快照到当前草稿（可编辑）");
}

// ---------- V1.11.1：版本条图片缩略图 + 实际时长（video 首帧 → canvas → dataURL；懒加载 + 缓存 + 降级） ----------

const genThumbMap = new Map<string, string>();
const genDurMap = new Map<string, number>();

function genThumbOf(genId: string): string {
  return genThumbMap.get(genId) ?? "";
}

function genDurOf(genId: string): number | undefined {
  return genDurMap.get(genId);
}

/** 版本卡时长：优先视频元数据实际时长（如 5.2s），未知回退镜头设定时长。 */
function genDurText(gen: ShotGenRecord): string {
  const d = genDurOf(gen.id);
  if (d && Number.isFinite(d) && d > 0) return `${d.toFixed(1)}s`;
  return fmtSec(shot.value?.durationSec ?? 0);
}

/** 为单个版本提取首帧缩略图 + 实际时长（幂等；失败静默降级占位，测试环境无 canvas 直接跳过）。 */
async function ensureGenThumb(gen: ShotGenRecord): Promise<void> {
  if (genThumbMap.has(gen.id)) return;
  const url = genVideoUrl(gen);
  if (!url) return;
  const probe = document.createElement("canvas");
  if (!probe.getContext("2d")) return;
  let video: HTMLVideoElement | null = null;
  try {
    video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.crossOrigin = "anonymous";
    video.src = url;
    await new Promise<void>((res, rej) => {
      video!.onloadeddata = () => res();
      video!.onerror = () => rej(new Error("video load failed"));
    });
    if (Number.isFinite(video.duration) && video.duration > 0) genDurMap.set(gen.id, video.duration);
    if (video.readyState >= 1) video.currentTime = Math.min(0.1, video.duration || 0.1);
    await new Promise<void>((res) => {
      video!.onseeked = () => res();
      setTimeout(res, 800);
    });
    const canvas = document.createElement("canvas");
    canvas.width = 160;
    canvas.height = 90;
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.drawImage(video, 0, 0, 160, 90);
      genThumbMap.set(gen.id, canvas.toDataURL("image/jpeg", 0.7));
      thumbTick.value++;
    }
  } catch {
    // 首帧提取失败：保留占位
  } finally {
    if (video) setTimeout(() => URL.revokeObjectURL(url), 3000);
  }
}

/** 当前镜头全部版本预热缩略图（幂等；版本列表变化/切镜时调用）。 */
async function warmGenThumbs(): Promise<void> {
  await Promise.all(shotGenerations.value.map((g) => ensureGenThumb(g)));
}

// ---------- V1.11.1：Shot 信息卡内容源（优先当前查看的版本快照，否则当前草稿） ----------

/** 信息卡 Prompt 五区数据源：查看历史版本时显示该版快照，否则显示当前草稿。 */
const infoPrompt = computed(() => viewedGen.value?.prompt ?? null);

/** 信息卡参数 chips：查看历史版本 → 该版生成参数；否则当前镜头生成参数。 */
const infoGenParams = computed<{ label: string; val: string }[]>(() => {
  if (viewedGen.value) return genParamLines(viewedGen.value);
  return genInfoLines.value;
});

/** 信息卡 👤 资产行：当前镜头继承的四类资产（角色/地点/道具/风格）。 */
const infoAssetNames = computed(() =>
  inheritance.value.map((i) => i.asset.name).filter((n): n is string => !!n),
);

/** 删除某条版本记录（确认后）。 */
function removeGen(shotId: string, generationId: string): void {
  const g = shotGenerations.value.find((x) => x.id === generationId);
  if (!confirm(`删除 Generation #${g ? genIndex(g) : ""}？视频文件会保留在磁盘，仅从项目历史移除。`)) return;
  removeGeneration(shotId, generationId);
  if (viewedGenId.value === generationId) {
    viewedGenId.value = null;
    const s = flattenedShots().find((x) => x.id === shotId);
    const active = s?.generations?.find((x) => x.id === s.activeGenerationId);
    if (active) {
      viewedGenId.value = active.id;
      setFinalVideo({ url: genVideoUrl(active), filename: active.videoFile || active.outputFilename });
    }
  }
}

/** 点击底部 Storyboard 卡片：选中镜头 + 有缓存则主播放器直接播该镜（详情区随当前镜头联动）。 */
function selectShotAndPlay(shotId: string): void {
  selectShot(shotId);
  if (isShotCached(shotId) && !wb.running) void playVideo("shot", { targetShotId: shotId });
}

/** 详情区「编辑 Prompt」：跳到右面板 Prompt 编辑器并聚焦。 */
function scrollToPromptEditor(): void {
  if (!shot.value) return;
  if (promptInputRef.value) {
    promptInputRef.value.scrollIntoView({ behavior: "smooth", block: "center" });
    promptInputRef.value.focus();
  }
}

// ---------- V1.8-1：Storyboard 卡片缩略图（video 首帧 → canvas → dataURL，懒加载 + 缓存 + 降级） ----------

const thumbMap = new Map<string, string>();
/** 缩略图生成完成后 bump 触发重渲染。 */
const thumbTick = ref(0);

function thumbOf(shotId: string): string {
  return thumbMap.get(shotId) ?? "";
}

/** 为单个已缓存镜头提取首帧缩略图（幂等；失败静默降级占位）。 */
async function ensureThumb(shotId: string): Promise<void> {
  if (thumbMap.has(shotId)) return;
  if (!isShotCached(shotId)) return;
  const idx = segIndexOf(shotId);
  if (idx < 0) return;
  // 环境能力守卫：无 canvas 2d（happy-dom/jsdom 测试环境）直接跳过，避免 video 解码挂起
  const probe = document.createElement("canvas");
  if (!probe.getContext("2d")) return;
  let url = "";
  let video: HTMLVideoElement | null = null;
  try {
    const { blob } = await wb.runService.api.segmentMp4(directorNodeId.value, idx, wb.frameRate);
    url = URL.createObjectURL(blob);
    video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = url;
    await new Promise<void>((res, rej) => {
      video!.onloadeddata = () => res();
      video!.onerror = () => rej(new Error("video load failed"));
    });
    if (video.readyState >= 1) video.currentTime = Math.min(0.1, video.duration || 0.1);
    await new Promise<void>((res) => {
      video!.onseeked = () => res();
      setTimeout(res, 800); // 兜底：无 seeked 事件也放行
    });
    const canvas = document.createElement("canvas");
    canvas.width = 160;
    canvas.height = 90;
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.drawImage(video, 0, 0, 160, 90);
      thumbMap.set(shotId, canvas.toDataURL("image/jpeg", 0.7));
      thumbTick.value++;
    }
  } catch {
    // 首帧提取失败：保留占位
  } finally {
    // 立即 revoke 会导致解码中引用失效；延迟回收
    if (video) setTimeout(() => URL.revokeObjectURL(url), 3000);
  }
}

/** 当前场景全部已缓存镜头预热缩略图（幂等；首次进入/缓存变化时调用）。 */
async function warmThumbs(): Promise<void> {
  const sceneShots = shotsOf(scene.value?.id ?? "");
  await Promise.all(sceneShots.map((s) => ensureThumb(s.id)));
}

// 当前镜头切换 → 预热 Storyboard + 版本条缩略图；段状态刷新 → 补加载未生成镜的缩略图
watch(
  () => shot.value?.id,
  () => {
    void warmThumbs();
    void warmGenThumbs();
  },
);
watch(
  () => wb.segStatus,
  () => {
    void warmThumbs();
    void warmGenThumbs();
  },
);
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onCinemaKey);
  if (cinemaOpen.value) closeCinema();
});

// ---------- V1.6-A 成片导出（按钮三态 + 弹窗预检）----------

/** 导出弹窗打开状态 + 当前范围（movie=整部影片 / scene=当前场景）。 */
const exportOpen = ref(false);
const exportScope = ref<"movie" | "scene">("movie");

/** 导出预检：逐镜归类「可导出 / 未生成（缺失）/ 参数已变（stale）」。 */
interface ExportCheck {
  okIds: string[];
  missingIds: string[];
  staleIds: string[];
}
function exportCheckFor(shotIds: string[]): ExportCheck {
  const okIds: string[] = [];
  const missingIds: string[] = [];
  const staleIds: string[] = [];
  for (const id of shotIds) {
    if (!isShotCached(id)) missingIds.push(id);
    else if (isShotStale(id)) staleIds.push(id);
    else okIds.push(id);
  }
  return { okIds, missingIds, staleIds };
}

/** 当前弹窗范围要导出的镜头 id（拍平顺序）。 */
const exportTargetShots = computed<string[]>(() => {
  const shots = flattenedShots();
  if (exportScope.value === "scene") {
    const sc = scene.value;
    return sc ? sc.shots.slice().sort((a, b) => a.order - b.order).map((s) => s.id) : [];
  }
  return shots.map((s) => s.id);
});

/** 当前弹窗范围预检结果。 */
const exportCheck = computed<ExportCheck>(() => exportCheckFor(exportTargetShots.value));

/**
 * 导出按钮三态（V16_PLAN §2 ②）：
 * 🟢 全部可导出（全片所有镜头 cached 且未 stale）
 * 🟡 有缺失/参数过期镜头（可「仅导出已完成」）
 * 🔴 当前无可导出内容（一个可用镜头都没有）
 */
const exportState = computed<"green" | "yellow" | "red">(() => {
  const shots = flattenedShots();
  if (!shots.length) return "red";
  const c = exportCheckFor(shots.map((s) => s.id));
  if (!c.okIds.length) return "red";
  if (c.missingIds.length || c.staleIds.length) return "yellow";
  return "green";
});

/** 导出按钮 tooltip 文案。 */
const exportBtnTitle = computed(() => {
  const st = exportState.value;
  if (st === "green") return "全部镜头已生成且参数未变，可直接导出成片";
  if (st === "yellow") return "有镜头尚未生成或参数已修改，导出前请先处理（点击可查看明细）";
  return "当前没有可导出的镜头（请先生成镜头）";
});

/** 打开导出弹窗。 */
function openExportModal(): void {
  if (wb.running || exportBusy.value) return;
  exportScope.value = "movie";
  exportOpen.value = true;
}

function closeExportModal(): void {
  exportOpen.value = false;
}

/**
 * 发起导出任务（V1.6-A）：把任务推进任务中心（kind=export），进度「编码 Shot x/y → 合并成片」。
 * @param shotIds 实际导出的镜头 id（null=全部）
 * @param skipMissing true=跳过缺失只导出已有的（「仅导出已完成」）
 */
function beginExport(shotIds: string[] | null, skipMissing: boolean): void {
  if (wb.running || exportBusy.value) return;
  exportOpen.value = false;
  const allShots = flattenedShots();
  const targets = shotIds ?? exportTargetShots.value;
  if (!targets.length) return;
  const scope = exportScope.value;
  const label =
    scope === "scene"
      ? `导出当前地点（${targets.length} 镜）`
      : `导出整部影片（${targets.length} 镜）`;
  void startExportTask({
    scope,
    sceneId: scope === "scene" ? scene.value?.id ?? null : null,
    scopeLabel: label,
    shotOrder: allShots.map((s) => s.id),
    targetShotIds: targets,
    nodeId: directorNodeId.value,
    fps: wb.frameRate,
    skipMissing,
  });
}

/** 「去生成缺失镜头」：关弹窗，把缺失 + 参数已变的镜头一次性提交生成（走查模式逐镜串行）。 */
function goGenerateProblems(): void {
  if (wb.running || exportBusy.value) return;
  const problemIds = [...exportCheck.value.missingIds, ...exportCheck.value.staleIds];
  exportOpen.value = false;
  if (!problemIds.length) return;
  genMode.value = "walkthrough";
  void handleGenerate(problemIds);
}

/**
 * 是否有导出任务在跑（V1.6-A）。导出是单次阻塞 HTTP（后端逐镜编码+合并），
 * 期间 ComfyUI 事件循环被占，不能再发生成/再点导出 → 用此状态禁用相关入口。
 */
const exportBusy = computed(() => wb.tasks.some((t) => t.kind === "export" && t.status === "running"));

/** 用户手动取消标志：interruptTask 置位，onFinish 据此把任务标为 cancelled 而非 failed。 */
let cancelledByUser = false;

/**
 * 生成模式（V1.5 #104 升级为双模式顶层切换）：
 * - walkthrough 走查模式（默认）：多镜范围逐镜串行生成。每镜 exportMode=segments 独立出片，
 *   成功后自动提交下一镜（衔接自动走 r2v/fl2v 续接）；失败/取消即停。不合并整条时间线
 *   → 不触发 `_guard_merge_memory` 内存保护。逐镜用低内存安全路径，适合日常走查。
 * - final 成片模式：多镜范围整片合并输出（all），目标段新内容 + 其余段缓存填充拼成整片预览。
 *   需较高内存（全片合并），适合所有镜头逐镜验收后一次性出成片。
 * 单镜范围（current/单镜按钮）两模式都走 segments，不做合并。
 * UI-only 状态（同 genScope），不写入项目文件。
 */
const genMode = ref<"walkthrough" | "final">("walkthrough");

// ---------- V1.8-3 ETA：历史采样读写 + 任务 genMeta 构建 ----------

/** 读取历史生成采样（localStorage；坏数据返回空）。 */
function readEtaHistory(): GenHistoryRecord[] {
  return loadEtaHistory(localStorage.getItem(ETA_HISTORY_KEY));
}

/** 记录一组生成采样（新在前、容量封顶；只写成功样本）。 */
function recordEtaSamples(samples: Array<{ taskType: string; frames: number; ms: number }>) {
  if (!samples.length) return;
  const list = readEtaHistory();
  const { width, height } = wb.outputSize;
  const fps = wb.frameRate;
  const workflowId = activeWorkflowId.value;
  const projectId = wb.project?.id ?? "";
  const now = Date.now();
  let next = list;
  for (const s of samples) {
    if (!(s.frames > 0) || !(s.ms > 0)) continue;
    next = appendEtaHistory(next, {
      taskType: normalizeTaskKey(s.taskType),
      width,
      height,
      fps,
      frames: s.frames,
      ms: s.ms,
      finishedAt: now,
      workflowId,
      projectId: projectId || undefined,
    });
  }
  if (next.length) localStorage.setItem(ETA_HISTORY_KEY, JSON.stringify(next));
}

/** 构建任务 ETA 元数据（逐镜帧数/任务类型/预测耗时，基于当前历史快照）并写入任务记录。 */
function buildTaskGenMeta(taskId: string, shotIds: string[]): GenMeta {
  const { width, height } = wb.outputSize;
  const meta = buildGenMeta({
    width,
    height,
    fps: wb.frameRate,
    workflowId: activeWorkflowId.value,
    shots: shotIds.map((id) => {
      const s = flattenedShots().find((x) => x.id === id);
      return {
        id,
        durationSec: s?.durationSec ?? 5,
        taskType: s?.generation?.taskType ?? "r2v",
      };
    }),
    history: readEtaHistory(),
  });
  patchTask(taskId, { genMeta: meta });
  return meta;
}

/** P0-A（#479）：提交前实时重建三段式提交文本。
 *  生成链：原始剧本(project.plan 或重建) → 后端 DirectorIntent(build_plan_intents)
 *  → 用户五区 overrides → build_shot_intent → h3_prompt_builder → 三段式 H3 Prompt。
 *  返回 {shotId: 三段拼接文本}（描述/音景/配乐 \n 拼接）；失败返回 null
 *  （调用侧回退 buildShotPromptText 旧分区合并，向后兼容，不阻塞生成）。 */
async function rebuildSubmissionPrompts(): Promise<Record<string, string> | null> {
  const project = wb.project;
  if (!project) return null;
  try {
    const plan = project.plan ?? rebuildPlanFromProject(project);
    const bindings = buildBindingsFromProject(project, plan);
    const durations = buildDurationsFromProject(project, plan);
    const overrides = buildOverridesByShot(project, plan);
    const result = await wb.runService.api.buildH3Prompts(plan, bindings, durations, overrides, getStyleProfile());
    return mapPlanPromptsToShots(project, plan, result);
  } catch (err) {
    console.warn("[P0-A] /prompt/h3 实时重建失败，回退旧分区合并：", err);
    return null;
  }
}

/** 提交生成。targetShotIds：显式传入（单镜按钮）则用之；null=按当前范围解析。 */
async function handleGenerate(targetShotIds: string[] | null = null) {
  const ep = episode.value;
  if (!ep) return;
  // V1.8-2：工作流有效性校验（0 节点 / 多节点未选 → 阻止生成并提示）。
  const aw = activeWorkflow.value;
  if (aw.status === "none") {
    setError("❌ 此工作流不是 MiniMax H3 Director 工作流：没有 MiniMaxH3Director 节点");
    return;
  }
  if (aw.status === "multi" || !aw.nodeId) {
    setError(`⚠️ 检测到 ${aw.nodeCount} 个 MiniMaxH3Director 节点，请选择生成节点`);
    workflowPickOpen.value = true;
    return;
  }
  // V1.7 Phase 4 硬拦截：范围内含未采纳 AI 草稿 → 禁止开拍（产品底线：AI 不直接开拍）。
  // 未采纳时按钮呈警示态可点，点击到此提示「请先采纳 AI 草稿」。
  const gateIds = targetShotIds ?? resolveTargetShotIds();
  const gateShots =
    gateIds === null ? flattenedShots() : flattenedShots().filter((s) => gateIds.includes(s.id));
  const unadopted = gateShots.filter((s) => s.aiDraft && !s.adopted);
  if (unadopted.length > 0) {
    setError(`⚠ 请先采纳 AI 草稿：当前范围有 ${unadopted.length} 镜尚未采纳（${GEN_SCOPE_LABEL[wb.genScope]}）`);
    return;
  }
  cancelledByUser = false;
  setError(null);
  // 生成前刷新段状态：pending/failed 范围依赖最新缓存；状态灯同步。
  await refreshSegStatus();
  const scopeIds = targetShotIds ?? resolveTargetShotIds();
  if (scopeIds && scopeIds.length === 0) {
    setError("当前范围没有可生成的镜头");
    return;
  }
  const shotOrder = flattenedShots().map((s) => s.id);
  const scopeLabel = scopeIds ? `${GEN_SCOPE_LABEL[wb.genScope]}（${scopeIds.length}）` : `${GEN_SCOPE_LABEL[wb.genScope]}（${shotOrder.length}）`;
  const taskId = addTask({ scopeLabel, shotCount: scopeIds ? scopeIds.length : shotOrder.length, shotOrder, targetShotIds: scopeIds });
  // V1.8-3 ETA：生成前构建逐镜预测表（帧数/任务类型/预测耗时），任务中心据此渲染 ETA。
  const genMeta = buildTaskGenMeta(taskId, scopeIds ?? shotOrder);
  // 参数指纹：提交前记录目标镜头当前参数（生成成功后才写入 paramHashes）。
  const genParams = { outputSize: wb.outputSize, frameRate: wb.frameRate };
  const pendingHashes: Record<string, string> = {};
  for (const sid of scopeIds ?? shotOrder) {
    const shot = flattenedShots().find((s) => s.id === sid);
    if (shot) pendingHashes[sid] = shotFingerprint(shot, genParams);
  }
  setRunning(true);
  clearPlayback();
  clearFinalVideo();
  // 走查模式：多镜范围（含全部）逐镜串行生成，每镜独立 segments 出片、自动衔接下一镜。
  // 成片模式：多镜范围走 all 整片合并（内存较高，适合全部验收后一次性出成片）。
  // 单镜范围（1 镜）两模式都走 segments，不做合并。
  const genTotal = scopeIds ? scopeIds.length : shotOrder.length;
  if (genMode.value === "walkthrough" && genTotal > 1) {
    // V1.10-B：走查模式携带快照持久字段（每镜换片时写盘，刷新/重开可恢复续跑）。
    const snapBase: InFlightSnapshotBase = {
      version: 1,
      kind: "walkthrough",
      projectId: wb.project?.id ?? "",
      nodeId: directorNodeId.value,
      workflowId: activeWorkflowId.value,
      scopeLabel,
      shotCount: genTotal,
      shotOrder,
      targetShotIds: scopeIds,
      scopeIds: scopeIds ?? shotOrder,
      startedAt: Date.now(),
      outputSize: wb.outputSize,
      frameRate: wb.frameRate,
      genMeta,
      finalVideos: {},
    };
    await runSequentialGenerate(taskId, scopeIds, shotOrder, pendingHashes, { snapBase, genMeta });
    return;
  }
  try {
    const { width, height } = wb.outputSize;
    // P0-A（#479）：提交前实时重建三段式提交文本（导演链最终产物：原始剧本 →
    // DirectorIntent → 用户 overrides → h3_prompt_builder → 三段式）。失败回退旧分区合并。
    const promptOverride = await rebuildSubmissionPrompts();
    const structure = buildTimelineStructure({
      episode: ep,
      frameRate: wb.frameRate,
      width,
      height,
      refMaxSize: Math.max(width, height),
      output: {
        mode: "fixed",
        longEdge: Math.max(width, height),
        width,
        height,
        maxExportFrames: 0,
        // 单镜生成 → 分镜导出（segments）：后端只采样这一镜，SaveVideo 直接输出
        // 该镜单段视频，播放器看到的就是本次生成的这镜（不拼其余段缓存/passthrough）。
        // 多镜/全部 → 全片合并（all）：目标段新内容 + 其余段缓存填充拼成整片预览。
        exportMode: scopeIds && scopeIds.length === 1 ? "segments" : "all",
        continuityEnabled: false,
        continuityOverlapFrames: 9,
        audioMode: resolveAudioMode(),
        qwenVlEnabled: false,
        qwenVlLevel: 1,
      },
      targetShotIds: scopeIds,
      promptOverride: promptOverride ?? undefined,
    });
    const workflow = JSON.parse(JSON.stringify(activeWorkflow.value.workflow)) as Record<string, unknown>;
    // 同步帧率到 workflow 节点 widget（Director 帧率 + CreateVideo fps）。
    syncWorkflowFrameRate(workflow, wb.frameRate);
    // V1.9：工作流 seed=-1 → 每次生成随机种子。
    randomizeSeedIfNeeded(workflow);
    // V1.12：记录实际提交的种子（随机模式 = 本次掷出的具体值），写版本历史可一键以此重放。
    const actualSeed = readWorkflowSeed(workflow) ?? undefined;
    const t0 = Date.now(); // V1.8-3 ETA：整次提交墙钟起点（单镜精确 / 多镜按帧数分摊）。
    // V1.11：记录本次成片（onVideo 写），onFinish 成功时归档创建版本记录。
    let lastFv: { url: string; filename: string } | null = null;
    const promptId = await wb.runService.run(structure, workflow, directorNodeId.value, {
      onProgress: (p) => {
        if (p) {
          const fp = {
            segment: p.segment,
            segmentTotal: p.segmentTotal,
            phaseLabel: p.phaseLabel,
            overallValue: p.overallValue,
            overallMax: p.overallMax,
            framesLabel: p.framesLabel,
            // V1.10-E：真实阶段步进透传（Sampling · Step v/max）。
            phaseValue: p.phaseValue,
            phaseMax: p.phaseMax,
          };
          setProgress(fp);
          patchTask(taskId, { progress: fp });
        }
      },
      onPreview: (p) => {
        if (p) {
          const b64 = `data:image/png;base64,${p.imageB64}`;
          setPreview(b64);
          patchTask(taskId, { preview: b64 });
        }
      },
      onVideo: (video) => {
        const fv = { url: video.url, filename: video.ref.filename, subfolder: video.ref.subfolder };
        lastFv = fv;
        // 单镜生成后端 exportMode=segments 只输出这一镜 → 播放器直接播单段；
        // 多镜/全部仍是 all 输出整片拼接（目标段新 + 其余段缓存/passthrough）。
        // 任务记录保留同一视频供「打开成片」回看本次结果。
        if (scopeIds && scopeIds.length === 1) {
          playStatus.value = `本镜回看：${shotNameOf(scopeIds[0])}`;
        }
        patchTask(taskId, { finalVideo: fv });
        setFinalVideo(fv);
      },
      onFinish: async (pid, ok, info) => {
        setLastPromptId(pid);
        setRunning(false);
        clearInFlight();
        const cancelled = cancelledByUser;
        // #93：失败任务先刷新段状态（本次运行后的真实状态）再定位失败镜头。
        // 否则会用上一次运行残留的 failed 错标镜头（用户实测：s2 放坏图，
        // 因上轮取消把 s1 标成 failed，报错却显示 s1）。
        if (!ok && !cancelled) await refreshSegStatus();
        // V1.4-P0：失败任务带上完整异常/节点/失败镜头，供「查看详情」面板使用。
        const errorDetail = info?.errorDetail ?? null;
        const failedShotIds = !ok && !cancelled ? resolveFailedShotIds(shotOrder, errorDetail, wb.segStatus) : [];
        patchTask(taskId, {
          status: ok ? "done" : cancelled ? "cancelled" : "failed",
          promptId: pid,
          finishedAt: Date.now(),
          error: ok ? null : cancelled ? "已手动取消" : errorDetail || "生成失败",
          errorDetail,
          nodeId: info?.nodeId ?? null,
          failedShotIds,
        });
        // 成功才固化参数指纹；失败/取消不留（下次生成会重新记录）。
        if (ok) recordParamHashes(pendingHashes);
        // V1.11：单镜成功 → 归档创建版本记录（多镜合并成片不逐镜记录）。
        if (ok && lastFv && scopeIds && scopeIds.length === 1) {
          void createGeneration(scopeIds[0], lastFv, { t0, seed: actualSeed });
          // Phase 2-E（#577）：单镜完成 → 自动触发 TTS + 混音（有台词才触发，失败降级不阻塞）。
          void autoTtsForShot(scopeIds[0], lastFv);
        }
        // V1.8-3 ETA：成功记录历史采样（单镜精确耗时；多镜合并按帧数比例分摊）。
        if (ok && genMeta) {
          const totalMs = Date.now() - t0;
          const framesTotal = genMeta.shots.reduce((a, s) => a + s.frames, 0);
          recordEtaSamples(
            genMeta.shots.map((s) => ({
              taskType: s.taskType,
              frames: s.frames,
              ms:
                framesTotal > 0
                  ? Math.round((totalMs * s.frames) / framesTotal)
                  : Math.round(totalMs / genMeta.shots.length),
            })),
          );
        }
        if (!ok && !cancelled) setError(errorDetail || "生成失败");
        void refreshSegStatus();
      },
      // V1.4-P0：失败后异步取回后端 report（Qwen/VRAM/段连续诊断），best-effort。
      onReport: (pid, report) => {
        // 防陈旧：WS 重连后旧 error 事件可能晚到，只写 promptId 匹配的任务。
        const t = wb.tasks.find((x) => x.id === taskId);
        if (t && t.promptId === pid) patchTask(taskId, { report });
      },
      // WS 断线兜底：DirectorRunService 每 3s 轮询 /queue+/history；
      // 这里同步刷新段状态灯（节流 5s），保证断线时任务/缓存感知仍新鲜。
      onPoll: () => {
        const now = Date.now();
        if (now - lastSegPollAt >= 5000) {
          lastSegPollAt = now;
          void refreshSegStatus();
        }
      },
    });
    setLastPromptId(promptId);
    // V1.10-B：单次生成写恢复快照（刷新后可探测此 prompt 归宿，重建运行态）。
    saveInFlight({
      version: 1,
      kind: "single",
      projectId: wb.project?.id ?? "",
      nodeId: directorNodeId.value,
      workflowId: activeWorkflowId.value,
      scopeLabel,
      shotCount: genTotal,
      shotOrder,
      targetShotIds: scopeIds,
      scopeIds: scopeIds ?? shotOrder,
      startedAt: t0,
      outputSize: wb.outputSize,
      frameRate: wb.frameRate,
      genMeta,
      finalVideos: {},
      taskId,
      currentIndex: 0,
      currentShotId: scopeIds?.[0] ?? shotOrder[0] ?? "",
      currentPromptId: promptId,
      shotStates: {},
      pendingHashes,
      // V1.12：本次提交的实际种子（刷新恢复后该镜版本历史记同一种子）。
      seed: actualSeed,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    setError(msg);
    setRunning(false);
    clearInFlight();
    patchTask(taskId, {
      status: "failed",
      finishedAt: Date.now(),
      error: msg,
      errorDetail: msg,
      failedShotIds: [],
    });
  }
}

/** 逐镜串行单镜运行结果。 */
interface SequentialShotResult {
  ok: boolean;
  cancelled: boolean;
  pid: string | null;
  errorDetail: string | null;
  nodeId: string | null;
  /** V1.12：本镜实际提交的种子（恢复快照/历史记录用）。 */
  seed?: number;
}

/**
 * 逐镜串行生成（V1.5 #104 走查模式）：按范围顺序逐镜提交，每镜 exportMode=segments。
 * 一镜成功 → 固化该镜指纹 + 自动提交下一镜；失败/取消 → 停（失败镜头入 failedShotIds）。
 * 整批共用一条任务记录，progress.segment 显示「第 i+1/total 镜」，shotStates 记录每镜
 * 走查状态（pending/running/done/failed/cancelled）供任务中心渲染走查队列。
 *
 * V1.10-B：opts.snapBase 携带持久快照字段，每镜提交/完成时写盘（刷新可恢复续跑）；
 * opts.initialStates 提供续跑初始状态（done 镜头跳过，不重复提交）。
 */
interface SequentialGenerateOptions {
  snapBase: InFlightSnapshotBase;
  genMeta: GenMeta | null;
  initialStates?: Record<string, string>;
}

async function runSequentialGenerate(
  taskId: string,
  scopeIds: string[] | null,
  shotOrder: string[],
  pendingHashes: Record<string, string>,
  opts: SequentialGenerateOptions,
): Promise<void> {
  const targets = scopeIds ?? shotOrder;
  const total = targets.length;
  // P0-A（#479）：走查逐镜同样走导演链 —— 一次 /prompt/h3 重建全部目标镜的三段式
  // 提交文本（{shotId: 三段拼接}），逐镜透传；失败回退旧分区合并，不阻塞逐镜。
  const promptOverride = await rebuildSubmissionPrompts();
  const failedShotIds: string[] = [];
  const shotStates: Record<string, string> = opts.initialStates ? { ...opts.initialStates } : {};
  for (const sid of targets) if (!shotStates[sid]) shotStates[sid] = "pending";
  patchTask(taskId, { shotStates: { ...shotStates } });
  let cancelled = false;
  let lastPid: string | null = null;
  let lastError: string | null = null;
  let lastErrorDetail: string | null = null;
  let lastNodeId: string | null = null;
  // V1.10-B：已完成镜头 → 成片地址（刷新恢复后还原 Shot 卡）。
  const finalVideos = { ...opts.snapBase.finalVideos };
  const saveSnap = (index: number, shotId: string, pid: string, states: Record<string, string>, seed?: number) => {
    saveInFlight({
      ...opts.snapBase,
      finalVideos: { ...finalVideos },
      taskId,
      currentIndex: index,
      currentShotId: shotId,
      currentPromptId: pid,
      shotStates: { ...states },
      pendingHashes,
      // V1.12：本次提交的实际种子（刷新恢复后该镜版本历史记同一种子）。
      seed,
    });
  };

  for (let i = 0; i < total; i++) {
    if (cancelledByUser) {
      cancelled = true;
      break;
    }
    const sid = targets[i];
    // 续跑：已完成的镜头跳过（保留任务状态与段缓存，不重复提交）。
    if (shotStates[sid] === "done") {
      patchTask(taskId, { currentIndex: i });
      continue;
    }
    shotStates[sid] = "running";
    patchTask(taskId, { currentIndex: i, shotStates: { ...shotStates } });
    const r = await runOneSequentialShot(
      taskId,
      sid,
      i,
      total,
      pendingHashes,
      {
        // 提交成功（拿到 prompt_id）即写盘，保证换镜间隙刷新也能定位到运行中的这镜。
        onSubmitted: (pid, seed) => saveSnap(i, sid, pid, { ...shotStates }, seed),
        onShotVideo: (video) => {
          finalVideos[sid] = video;
        },
      },
      promptOverride,
    );
    lastPid = r.pid;
    if (r.cancelled) {
      shotStates[sid] = "cancelled";
      cancelled = true;
      break;
    }
    if (r.ok) {
      shotStates[sid] = "done";
      recordParamHashes({ [sid]: pendingHashes[sid] });
    } else {
      shotStates[sid] = "failed";
      failedShotIds.push(sid);
      lastError = r.errorDetail ?? "生成失败";
      lastErrorDetail = r.errorDetail;
      lastNodeId = r.nodeId;
      break;
    }
    patchTask(taskId, { shotStates: { ...shotStates } });
    saveSnap(i, sid, r.pid ?? "", { ...shotStates }, r.seed);
    // 逐镜间隙刷新段状态：状态灯随每镜完成即时更新（pending/failed 范围下次解析用最新缓存）。
    await refreshSegStatus();
  }

  setRunning(false);
  const status: TaskRecord["status"] = cancelled ? "cancelled" : failedShotIds.length ? "failed" : "done";
  patchTask(taskId, {
    status,
    promptId: lastPid,
    finishedAt: Date.now(),
    shotStates: { ...shotStates },
    error: status === "done" ? null : cancelled ? "已手动取消" : lastError,
    errorDetail: status === "failed" ? lastErrorDetail : null,
    nodeId: status === "failed" ? lastNodeId : null,
    failedShotIds,
  });
  // V1.10-B：任务收尾 → 清除恢复快照。
  clearInFlight();
  if (status === "failed") setError(lastError || "生成失败");
  // 批量收尾再刷一次：setRunning(false) 后 running 态过滤生效，状态灯归位。
  void refreshSegStatus();
}

/** V1.10-B：逐镜提交钩子（写恢复快照 / 收集已完成镜头成片）。 */
interface SequentialShotHooks {
  /** 提交成功拿到 prompt_id 后回调（该镜进入运行态的第一时间）；seed = 本次实际提交种子。 */
  onSubmitted?: (pid: string, seed?: number) => void;
  /** 本镜成片产出后回调（写入快照 finalVideos，供刷新后还原）。 */
  onShotVideo?: (video: { url: string; filename: string }) => void;
}

/** 逐镜单镜提交：构建 segments 结构 → runService.run → onFinish 决议本次结果。 */
function runOneSequentialShot(
  taskId: string,
  sid: string,
  idx: number,
  total: number,
  _pendingHashes: Record<string, string>,
  hooks?: SequentialShotHooks,
  promptOverride?: Record<string, string> | null,
): Promise<SequentialShotResult> {
  return new Promise((resolve) => {
    const ep = episode.value;
    if (!ep) {
      resolve({ ok: false, cancelled: false, pid: null, errorDetail: "项目为空", nodeId: null });
      return;
    }
    const { width, height } = wb.outputSize;
    let structure: ReturnType<typeof buildTimelineStructure>;
    try {
      structure = buildTimelineStructure({
        episode: ep,
        frameRate: wb.frameRate,
        width,
        height,
        refMaxSize: Math.max(width, height),
        output: {
          mode: "fixed",
          longEdge: Math.max(width, height),
          width,
          height,
          maxExportFrames: 0,
          // 逐镜：每镜独立分镜导出，后端只采样这一镜，SaveVideo 直接输出该镜单段视频。
          exportMode: "segments",
          continuityEnabled: false,
          continuityOverlapFrames: 9,
          audioMode: resolveAudioMode(),
          qwenVlEnabled: false,
          qwenVlLevel: 1,
        },
        targetShotIds: [sid],
        promptOverride: promptOverride ?? undefined,
      });
    } catch (err) {
      resolve({
        ok: false,
        cancelled: false,
        pid: null,
        errorDetail: err instanceof Error ? err.message : String(err),
        nodeId: null,
      });
      return;
    }
    const workflow = JSON.parse(JSON.stringify(activeWorkflow.value.workflow)) as Record<string, unknown>;
    syncWorkflowFrameRate(workflow, wb.frameRate);
    // V1.9：工作流 seed=-1 → 每次生成随机种子（走查逐镜也生效）。
    randomizeSeedIfNeeded(workflow);
    // V1.12：记录实际提交的种子（随机模式 = 本次掷出的具体值），写版本历史可一键以此重放。
    const actualSeed = readWorkflowSeed(workflow) ?? undefined;
    // V1.8-3 ETA：本镜生成参数（帧数/任务类型），成功时写入历史采样。
    const ts = structure.shots.find((x) => x.id === sid);
    const shotFrames = ts ? framesForDuration(ts.durationSec, structure.frameRate) : 0;
    const shotTaskType = ts ? normalizeTaskKey(ts.taskKey) : "r2v";
    // 提交前先写本镜进度：第 i+1/total 镜（后端进度事件覆盖 phaseLabel/framesLabel）。
    const pre = {
      segment: idx + 1,
      segmentTotal: total,
      phaseLabel: `第 ${idx + 1}/${total} 镜`,
      overallValue: idx,
      overallMax: total,
      framesLabel: "",
    };
    setProgress(pre);
    patchTask(taskId, { progress: pre });

    let expectedPid = "";
    let done = false;
    const finishOnce = (r: SequentialShotResult) => {
      if (done) return;
      done = true;
      resolve(r);
    };
    const t0 = Date.now(); // V1.8-3 ETA：本镜墙钟起点（成功时写入历史采样）。
    // V1.11：记录本镜成片（onVideo 写），onFinish 成功时归档创建版本记录。
    let lastFv: { url: string; filename: string } | null = null;
    wb.runService
      .run(structure, workflow, directorNodeId.value, {
        onProgress: (p) => {
          if (!p) return;
          // 逐镜进度：overallValue=已完成的镜数（本镜开始即 idx），overallMax=总镜数；
          // 阶段文案用后端真实阶段（采样中/编码…），镜头定位用「第 idx+1/total 镜」。
          const fp = {
            segment: idx + 1,
            segmentTotal: total,
            phaseLabel: p.phaseLabel,
            overallValue: idx,
            overallMax: total,
            framesLabel: p.framesLabel,
            // V1.10-E：真实阶段步进透传（Sampling · Step v/max）。
            phaseValue: p.phaseValue,
            phaseMax: p.phaseMax,
          };
          setProgress(fp);
          patchTask(taskId, { progress: fp });
        },
        onPreview: (p) => {
          if (p) {
            const b64 = `data:image/png;base64,${p.imageB64}`;
            setPreview(b64);
            patchTask(taskId, { preview: b64 });
          }
        },
        onVideo: (video) => {
          const fv = { url: video.url, filename: video.ref.filename, subfolder: video.ref.subfolder };
          lastFv = fv;
          // 逐镜 segments 只输出当前这一镜 → 播放器直接播单段，回看本镜。
          playStatus.value = `本镜回看：${shotNameOf(sid)}`;
          patchTask(taskId, { finalVideo: fv });
          setFinalVideo(fv);
          hooks?.onShotVideo?.(fv);
        },
        onFinish: (pid, ok, info) => {
          setLastPromptId(pid);
          // 陈旧事件保护：晚到的上一镜事件带旧 promptId，忽略（逐镜串行下防误收尾）。
          if (expectedPid && pid !== expectedPid) return;
          const cancelled = cancelledByUser;
          // V1.8-3 ETA：本镜成功 → 记录精确耗时采样（后续镜头/下次生成预测用）。
          if (ok && shotFrames > 0) {
            recordEtaSamples([{ taskType: shotTaskType, frames: shotFrames, ms: Date.now() - t0 }]);
          }
          // V1.11：本镜成功 → 归档创建版本记录。
          if (ok && lastFv) {
            void createGeneration(sid, lastFv, { t0, seed: actualSeed });
            // Phase 2-E（#577）：逐镜走查完成 → 自动触发 TTS + 混音（失败降级不阻塞走查）。
            void autoTtsForShot(sid, lastFv);
          }
          finishOnce({
            ok,
            cancelled,
            pid,
            errorDetail: ok ? null : cancelled ? null : (info?.errorDetail ?? null),
            nodeId: ok ? null : cancelled ? null : (info?.nodeId ?? null),
            seed: ok ? actualSeed : undefined,
          });
        },
        // V1.4-P0：失败后异步取回后端 report（best-effort），只写 promptId 匹配的任务。
        onReport: (pid, report) => {
          const t = wb.tasks.find((x) => x.id === taskId);
          if (t && t.promptId === pid) patchTask(taskId, { report });
        },
        // WS 断线兜底：每 5s 刷一次状态灯（与单次生成一致）。
        onPoll: () => {
          const now = Date.now();
          if (now - lastSegPollAt >= 5000) {
            lastSegPollAt = now;
            void refreshSegStatus();
          }
        },
      })
      .then((pid) => {
        expectedPid = pid;
        setLastPromptId(pid);
        hooks?.onSubmitted?.(pid, actualSeed);
      })
      .catch((err) => {
        if (done) return;
        const msg = err instanceof Error ? err.message : String(err);
        finishOnce({ ok: false, cancelled: cancelledByUser, pid: null, errorDetail: msg, nodeId: null });
      });
  });
}

/** 任务中心「重新生成」：复用原任务目标镜头 id 重新提交（null=全部）。 */
function onTaskRegen(targetShotIds: string[] | null) {
  void handleGenerate(targetShotIds);
}

/** 任务中心/顶部「取消」：中断 ComfyUI 当前生成，任务标为已取消。 */
async function onTaskCancel() {
  if (!wb.running) return;
  cancelledByUser = true;
  setError(null);
  clearInFlight();
  try {
    await wb.runService.interrupt();
  } catch (err) {
    setError(err instanceof Error ? err.message : String(err));
  }
}

// ---------- V1.10-B 生成中刷新/关闭恢复 ----------

/** 恢复当前项目是否有在途快照；有则重建「正在 ComfyUI 生成」态并挂回轮询。 */
function restoreInFlight(projectId: string): void {
  const snap = loadInFlight();
  if (!snap || snap.projectId !== projectId) {
    // 快照属于其他项目 / 不存在 → 清除（避免跨项目误恢复）。
    if (snap) clearInFlight();
    return;
  }
  if (!snap.currentPromptId) {
    // 无 prompt 定位信息 → 无法恢复，清快照（任务已结束/异常，不误报）。
    clearInFlight();
    return;
  }
  // 项目镜头顺序异动保护：与快照不一致 → 只恢复信息，不自动续跑（避免段索引错位）。
  const currentOrder = flattenedShots().map((s) => s.id);
  const orderMatches =
    snap.shotOrder.length === currentOrder.length &&
    snap.shotOrder.every((id, i) => id === currentOrder[i]);

  const taskId = addTask({
    scopeLabel: snap.scopeLabel,
    shotCount: snap.shotCount,
    shotOrder: snap.shotOrder,
    targetShotIds: snap.targetShotIds,
  });
  patchTask(taskId, {
    currentIndex: snap.currentIndex,
    shotStates: { ...snap.shotStates },
    promptId: snap.currentPromptId,
    startedAt: snap.startedAt,
    genMeta: snap.genMeta ?? undefined,
    finalVideo: snap.finalVideos[snap.currentShotId] ?? null,
    progress: {
      segment: snap.currentIndex + 1,
      segmentTotal: snap.shotCount,
      phaseLabel: "正在向 ComfyUI 恢复生成状态…",
      overallValue: snap.currentIndex,
      overallMax: snap.shotCount,
      framesLabel: "",
    },
  });

  cancelledByUser = false;
  setRunning(true);
  clearPlayback();
  clearFinalVideo();
  playStatus.value = `🎬 恢复中：${shotNameOf(snap.currentShotId)}`;
  wb.runService.resume(snap.currentPromptId, buildRestoreCallbacks(taskId, snap, orderMatches));
}

/** 为恢复任务重建回调：进度/预览/成片写回任务；当前镜收尾后走查自动续跑剩余镜头。 */
function buildRestoreCallbacks(taskId: string, snap: InFlightSnapshot, orderMatches: boolean) {
  const total = snap.shotCount;
  const idx = snap.currentIndex;
  const sid = snap.currentShotId;
  const remainingStates = { ...snap.shotStates, [sid]: "running" };
  patchTask(taskId, { shotStates: remainingStates });
  // V1.11：记录恢复本次成片（onVideo 写），成功时归档创建版本记录。
  let lastFv: { url: string; filename: string } | null = null;
  return {
    onProgress: (p: { phaseLabel?: string; framesLabel?: string; phaseValue?: number; phaseMax?: number } | null | undefined) => {
      if (!p) return;
      const fp = {
        segment: idx + 1,
        segmentTotal: total,
        phaseLabel: p.phaseLabel ?? "",
        overallValue: idx,
        overallMax: total,
        framesLabel: p.framesLabel ?? "",
        phaseValue: p.phaseValue,
        phaseMax: p.phaseMax,
      };
      setProgress(fp);
      patchTask(taskId, { progress: fp });
    },
    onPreview: (p: { imageB64: string } | null | undefined) => {
      if (p) {
        const b64 = `data:image/png;base64,${p.imageB64}`;
        setPreview(b64);
        patchTask(taskId, { preview: b64 });
      }
    },
    onVideo: (video: { url: string; ref: { filename: string; subfolder?: string } }) => {
      const fv = { url: video.url, filename: video.ref.filename, subfolder: video.ref.subfolder };
      lastFv = fv;
      playStatus.value = `💾 已从刷新恢复：${shotNameOf(sid)}`;
      patchTask(taskId, { finalVideo: fv });
      setFinalVideo(fv);
    },
    onFinish: async (pid: string, ok: boolean, info?: { errorDetail?: string; nodeId?: string }) => {
      setLastPromptId(pid);
      if (!ok) {
        setRunning(false);
        const cancelled = cancelledByUser;
        const errorDetail = info?.errorDetail ?? null;
        patchTask(taskId, {
          status: cancelled ? "cancelled" : "failed",
          promptId: pid,
          finishedAt: Date.now(),
          error: cancelled ? "已手动取消" : errorDetail || "生成失败（刷新后任务失联）",
          errorDetail,
          nodeId: info?.nodeId ?? null,
        });
        clearInFlight();
        if (!cancelled) setError(errorDetail || "生成失败（刷新后任务失联）");
        void refreshSegStatus();
        return;
      }
      // 本镜成功：写任务状态 + 走查续跑（顺序匹配时）。
      const newStates = { ...snap.shotStates, [sid]: "done" };
      patchTask(taskId, { currentIndex: idx, shotStates: newStates });
      recordParamHashes({ [sid]: snap.pendingHashes[sid] });
      // V1.11：恢复成功 → 归档创建版本记录（durationMs 用快照起点近似）。
      if (lastFv) {
        void createGeneration(sid, lastFv, { t0: snap.startedAt, seed: snap.seed });
        // Phase 2-E（#577）：刷新恢复本镜完成 → 自动触发 TTS + 混音（失败降级不阻塞续跑）。
        void autoTtsForShot(sid, lastFv);
      }
      const remaining = (snap.scopeIds ?? snap.shotOrder).filter((s) => newStates[s] !== "done");
      if (snap.kind === "walkthrough" && orderMatches && remaining.length > 0) {
        await runSequentialGenerate(taskId, snap.scopeIds, snap.shotOrder, snap.pendingHashes, {
          snapBase: snap,
          genMeta: snap.genMeta,
          initialStates: newStates,
        });
        return;
      }
      setRunning(false);
      patchTask(taskId, { status: "done", finishedAt: Date.now(), promptId: pid });
      clearInFlight();
      void refreshSegStatus();
    },
    onPoll: () => {
      const now = Date.now();
      if (now - lastSegPollAt >= 5000) {
        lastSegPollAt = now;
        void refreshSegStatus();
      }
    },
  };
}

/** 下载单个镜头 mp4（V1.3 任务中心/卡片「⬇ 下载」；无缓存或失败提示）。 */
async function downloadShotMp4(shotId: string) {
  const idx = segIndexOf(shotId);
  if (idx < 0) return;
  const st = wb.segStatus;
  if (!st?.cached.includes(idx)) {
    setError("该镜头尚未生成（无缓存）");
    return;
  }
  try {
    const { blob, filename } = await wb.runService.api.segmentMp4(directorNodeId.value, idx, wb.frameRate);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    setError(err instanceof Error ? err.message : String(err));
  }
}

function fmtSec(sec: number): string {
  return `${sec}s`;
}

function onWidthChange(e: Event) {
  const v = Number((e.target as HTMLInputElement).value);
  if (Number.isFinite(v)) setOutputSize(v, wb.outputSize.height);
}

function onHeightChange(e: Event) {
  const v = Number((e.target as HTMLInputElement).value);
  if (Number.isFinite(v)) setOutputSize(wb.outputSize.width, v);
}

/**
 * #456（P1-⑦）：镜头时长输入「编辑期草稿」。
 * 根因：`@change` 才写回 store，但输入过程中任一次响应式重渲染（如进度轮询/切镜 watch
 * 触发的 patch）都会把 `:value` 反写为旧 store 值，正在输入的 5/5.5/10 被吞掉/跳回。
 * 修复：输入期间用本地草稿顶住 DOM value（重渲染只对比草稿，不改用户输入），
 * @input 只更新草稿，@change（失焦/回车）才解析并写回 store，随后清空草稿。
 */
const durationDraft = ref<string | null>(null);

/** 时长输入框显示值：编辑中显示草稿，否则显示 store 镜头时长（格式化语义同存储）。 */
const durationInputValue = computed<string | number>(() => {
  if (durationDraft.value !== null) return durationDraft.value;
  return shot.value?.durationSec ?? 0;
});


/** @input：记录用户输入为草稿（不回写 store，防止输入过程中被重渲染打断）。 */
function onDurationDraftInput(e: Event) {
  durationDraft.value = (e.target as HTMLInputElement).value;
}

/** #456：@input 期间暂停「切镜/切场景清空草稿」之外的所有反写。@change 提交草稿。 */
function onDurationChange(e: Event) {
  if (!shot.value) return;
  const raw = (e.target as HTMLInputElement).value;
  durationDraft.value = null;
  const v = Number(raw);
  if (raw !== "" && Number.isFinite(v)) updateShotDuration(shot.value.id, v);
}

/** 生成当前选中镜头（右侧按钮 / 左侧卡片迷你按钮共用）。 */
function generateShot(shotId: string) {
  selectShot(shotId);
  handleGenerate([shotId]);
}

// ---------- V1.2 编辑交互 ----------

function findShotMeta(shotId: string) {
  return episode.value?.scenes.flatMap((s) => s.shots).find((x) => x.id === shotId);
}

/** 镜头卡 hover：浮层方向自适应。浮层向下弹但超出左栏可视区时改为向上弹，
 * 否则靠近列表底部的卡片（删除/上移等）浮层被滚动容器裁剪点不到。 */
function onShotCardEnter(e: MouseEvent): void {
  const card = e.currentTarget as HTMLElement;
  const ops = card.querySelector<HTMLElement>(".shot-ops");
  const list = card.closest<HTMLElement>(".wb-left");
  if (!ops || !list) return;
  const cardRect = card.getBoundingClientRect();
  const listRect = list.getBoundingClientRect();
  const opsH = ops.offsetHeight || 120;
  // 卡片下缘到容器下缘的空间不足放整个浮层 → 向上弹
  const spaceBelow = listRect.bottom - cardRect.bottom;
  ops.classList.toggle("ops-up", spaceBelow < opsH);
}

function addShotIn(sceneId: string) {
  addShot(sceneId);
}

function removeShotBy(shotId: string) {
  const s = findShotMeta(shotId);
  if (!confirm(`删除镜头「${s?.name ?? shotId}」？此操作不可恢复。`)) return;
  removeShot(shotId);
}

function dupShot(shotId: string) {
  duplicateShot(shotId);
}

function mvShot(shotId: string, dir: -1 | 1) {
  moveShot(shotId, dir);
}

function renameShotBy(shotId: string) {
  const s = findShotMeta(shotId);
  const name = prompt("镜头名称：", s?.name ?? "");
  if (name !== null) renameShot(shotId, name);
}

function addSceneNow() {
  addScene();
}

function removeSceneBy(sceneId: string) {
  const sc = episode.value?.scenes.find((x) => x.id === sceneId);
  if (!confirm(`删除地点「${sc?.name ?? sceneId}」及其全部镜头？此操作不可恢复。`)) return;
  removeScene(sceneId);
}

function renameSceneBy(sceneId: string) {
  const sc = episode.value?.scenes.find((x) => x.id === sceneId);
  const name = prompt("地点名称：", sc?.name ?? "");
  if (name !== null) renameScene(sceneId, name);
}

// 镜头字段写回（Prompt 输入走 onPromptInput：写回 + @ 补全检测）

/** @高亮下层随 textarea 滚动同步。 */
function syncPromptScroll(e: Event) {
  const el = e.target as HTMLTextAreaElement;
  const layer = el.parentElement?.querySelector(".prompt-layer") as HTMLElement | null;
  if (layer) {
    layer.scrollTop = el.scrollTop;
    layer.scrollLeft = el.scrollLeft;
  }
}

function onShotNegative(e: Event) {
  const v = (e.target as HTMLTextAreaElement).value;
  if (shot.value) updateShotNegative(shot.value.id, v);
}

/** V2 分区编辑器：摄影/风格/声音 自由文本写回。 */
function onShotSection(field: "cameraText" | "style" | "soundText", e: Event) {
  const v = (e.target as HTMLTextAreaElement).value;
  if (shot.value) updateShotSection(shot.value.id, field, v);
}

/** V1.6-B 添加人物：把选中资产并入显式 castIds（canonical 同名替换 → 绝不重复），关闭 picker。 */
function addCast(assetId: string) {
  if (!shot.value) return;
  setShotCasts(shot.value.id, mergeCastId(shot.value, assetId));
  castPickerOpen.value = false;
}

/** V1.6-B 移除人物：剔除该角色全部 canonical 同名条目（场景/全局副本一并移除；空集合 → 自动继承）。 */
function removeCast(assetId: string) {
  if (!shot.value) return;
  const target = assetIndex.value.assets.find((a) => a.id === assetId);
  const cur = shotCastIds(shot.value);
  if (target) {
    const key = castNameKey(target);
    setShotCasts(
      shot.value.id,
      cur.filter((id) => {
        const a = assetIndex.value.assets.find((x) => x.id === id);
        return !a || castNameKey(a) !== key;
      }),
    );
  } else {
    setShotCasts(shot.value.id, cur.filter((id) => id !== assetId));
  }
}

/**
 * V1.6-B 把 assetId 并入显式 castIds：canonical 同名已选 → 替换为本次资产（保持单一人选，
 * 场景/全局两个同名副本不会并存）；否则追加。供「＋ 添加人物」与 Prompt @cast chip 共用。
 */
function mergeCastId(shot: Shot, assetId: string): string[] {
  const target = assetIndex.value.assets.find((a) => a.id === assetId);
  const cur = shotCastIds(shot);
  if (target) {
    const key = castNameKey(target);
    const hit = cur
      .map((id) => assetIndex.value.assets.find((a) => a.id === id))
      .find((a): a is Asset => a !== undefined && castNameKey(a) === key);
    if (hit) return cur.map((id) => (id === hit.id ? assetId : id));
  }
  return [...cur, assetId];
}

function onShotLoc(e: Event) {
  const v = (e.target as HTMLSelectElement).value;
  if (shot.value) updateShotLocation(shot.value.id, v, !!v);
}

function onShotContinuity(e: Event) {
  const v = (e.target as HTMLSelectElement).value as "auto" | "none" | "ref2va" | "fl2va";
  if (shot.value) updateShotContinuity(shot.value.id, v);
}

function onShotSmartTail(e: Event) {
  const v = (e.target as HTMLInputElement).checked;
  if (shot.value) updateShotSmartTail(shot.value.id, v);
}

function onShotStateChange(e: Event) {
  const v = (e.target as HTMLInputElement).value;
  if (shot.value) updateShotStateChange(shot.value.id, v);
}

function onShotSceneMove(e: Event) {
  const v = (e.target as HTMLSelectElement).value;
  if (shot.value) moveShotToScene(shot.value.id, v);
}

// 场景字段写回
function onSceneText(e: Event, field: keyof Scene) {
  const v = (e.target as HTMLInputElement | HTMLTextAreaElement).value;
  if (scene.value) patchScene(scene.value.id, { [field]: v });
}

function onSceneDefault(kind: AssetKind, e: Event) {
  const v = (e.target as HTMLSelectElement).value;
  if (!scene.value) return;
  if (kind === "cast") patchScene(scene.value.id, { defaultCastId: v || undefined });
  else if (kind === "location") patchScene(scene.value.id, { defaultLocationId: v || undefined });
  else patchScene(scene.value.id, { defaultStyleId: v || undefined });
}

// ---------- V1.5-P0-A：素材库（两级池 + 复制 + 名称唯一） ----------

/** 素材库两级切换（scene=当前场景素材 / episode=全局资产）。 */
const libScope = ref<"scene" | "episode">("scene");
/** 正在内联编辑的资产（kind + id）；null=无。 */
const libEditing = ref<{ kind: AssetKind; id: string } | null>(null);
/** 内联编辑表单（编辑中资产的临时值）。 */
const libEditForm = ref<{ name: string; description: string; aliases: string }>({ name: "", description: "", aliases: "" });
/** 素材库操作反馈消息。 */
const libMsg = ref("");

/** 当前素材库展示的组（按 scope 切换）。 */
const libGroups = computed(() => (libScope.value === "scene" ? sceneAssetGroups.value : globalAssetGroups.value));

/** AssetKind → SceneAssets 键。 */
function assetKeyOf(kind: AssetKind): "cast" | "locations" | "props" | "styles" {
  return kind === "cast" ? "cast" : kind === "location" ? "locations" : kind === "prop" ? "props" : "styles";
}

/** AssetKind → 中文标签。 */
function libLabel(kind: AssetKind): string {
  return kind === "cast" ? "人物" : kind === "location" ? "地点" : kind === "prop" ? "道具" : "风格";
}

/** 当前素材库 scope 的目标池 sceneId（scene scope 传当前场景 id；episode 无）。 */
function libSceneId(): string | undefined {
  return libScope.value === "scene" ? scene.value?.id : undefined;
}

function setLibScope(scope: "scene" | "episode") {
  libScope.value = scope;
  libEditing.value = null;
}

/** 新建资产（落到当前素材库 scope）：名称 prompt + 名称唯一校验 + 自动展开编辑表单。 */
function addLibAsset(kind: AssetKind) {
  const label = libLabel(kind);
  const name = prompt(`新建${label}名称：`, "");
  if (!name || !name.trim()) return;
  const scope = libScope.value;
  if (assetNameConflict(kind, name.trim(), scope, libSceneId())) {
    libMsg.value = `「${name.trim()}」已存在于${scope === "scene" ? "本地点" : "全局"}素材库（${label}），请换名。`;
    return;
  }
  const a = addAsset(kind, name, { scope, sceneId: libSceneId() });
  if (a) {
    libMsg.value = "";
    libEditing.value = { kind, id: a.id };
    libEditForm.value = { name: a.name, description: a.description ?? "", aliases: (a.aliases ?? []).join(", ") };
  }
}

/** 打开资产内联编辑表单。 */
function startEditAsset(kind: AssetKind, assetId: string) {
  const pool = libScope.value === "episode"
    ? episode.value?.assets?.[assetKeyOf(kind)]
    : scene.value?.assets?.[assetKeyOf(kind)];
  const a = pool?.find((x) => x.id === assetId);
  if (!a) return;
  libEditing.value = { kind, id: assetId };
  libEditForm.value = { name: a.name, description: a.description ?? "", aliases: (a.aliases ?? []).join(", ") };
  libMsg.value = "";
}

function cancelEditAsset() {
  libEditing.value = null;
}

/** 保存资产编辑（改名/描述/别名；名称唯一校验；改名时全项目 @引用同步）。 */
function saveEditAsset() {
  if (!libEditing.value) return;
  const { kind, id } = libEditing.value;
  const scope = libScope.value;
  const name = libEditForm.value.name.trim();
  if (!name) {
    libMsg.value = "名称不能为空。";
    return;
  }
  if (assetNameConflict(kind, name, scope, libSceneId(), id)) {
    libMsg.value = `「${name}」已存在（${libLabel(kind)}），请换名。`;
    return;
  }
  const aliases = libEditForm.value.aliases.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  // V1.5-P0-B：改名走 renameAsset → 全项目镜头 Prompt 的 @旧名 批量替换（token 边界规则）。
  renameAsset(
    kind,
    id,
    { name, description: libEditForm.value.description.trim() || undefined, ...(aliases.length ? { aliases } : { aliases: undefined }) },
    scope,
    libSceneId(),
  );
  libEditing.value = null;
  libMsg.value = "";
}

/** 资产参考图上传 → 写回当前 scope 的 Asset.imageFile。 */
function onLibAssetImage(kind: AssetKind, assetId: string, relPath: string) {
  updateAsset(kind, assetId, { imageFile: relPath }, libScope.value, libSceneId());
}

/** 删除资产（当前 scope）。 */
function removeLibAsset(kind: AssetKind, assetId: string) {
  const key = assetKeyOf(kind);
  const pool = libScope.value === "episode"
    ? episode.value?.assets?.[key]
    : scene.value?.assets?.[key];
  const a = pool?.find((x) => x.id === assetId);
  if (!confirm(`删除素材「${a?.name ?? assetId}」？引用它的镜头会自动改回继承。`)) return;
  removeAsset(kind, assetId, libScope.value, libSceneId());
  if (libEditing.value?.id === assetId) libEditing.value = null;
}

/** 全局资产 → 复制到当前场景素材组（新 id 快照；同名冲突则跳过）。 */
function copyGlobalToScene(kind: AssetKind, assetId: string) {
  if (!scene.value) return;
  const src = episode.value?.assets?.[assetKeyOf(kind)]?.find((x) => x.id === assetId);
  if (!src) return;
  const copy = copyAssetToScene(kind, assetId, scene.value.id);
  if (copy) {
    libMsg.value = `已复制「${copy.name}」到本地点素材组。`;
    libScope.value = "scene";
  } else {
    libMsg.value = `本地点素材组已有同名资产「${src.name}」，未复制。`;
  }
}

/** 场景设置面板「默认人物/默认地点」旁的快速新建（落场景池 + 切到场景库 + 展开编辑）。 */
function addSceneAsset(kind: AssetKind) {
  if (!scene.value) return;
  const label = libLabel(kind);
  const name = prompt(`新建${label}名称：`, "");
  if (!name || !name.trim()) return;
  if (assetNameConflict(kind, name.trim(), "scene", scene.value.id)) {
    libMsg.value = `「${name.trim()}」已存在于本地点素材库（${label}），请换名。`;
    return;
  }
  const a = addAsset(kind, name, { sceneId: scene.value.id });
  if (a) {
    libScope.value = "scene";
    libEditing.value = { kind, id: a.id };
    libEditForm.value = { name: a.name, description: a.description ?? "", aliases: (a.aliases ?? []).join(", ") };
  }
}

// ---------- V1.2.5 参考图上传写回 ----------

/** 场景参考图上传成功 → 写回 scene.referenceImage（相对 input 路径）。 */
function onSceneRefImage(relPath: string) {
  if (scene.value) patchScene(scene.value.id, { referenceImage: relPath || undefined });
}

/** 镜头参考素材：新上传图追加进 refs.refImages。 */
function onShotAddRef(relPath: string) {
  if (shot.value && relPath) addShotRefImage(shot.value.id, relPath);
}

/** 删除镜头某张参考图。 */
function onShotRemoveRef(index: number) {
  if (shot.value) removeShotRefImage(shot.value.id, index);
}

/** 参考图缩略图加载失败 → 隐藏 img（避免破图图标）。 */
function onRefImgError(e: Event) {
  const el = e.target as HTMLImageElement;
  el.style.display = "none";
}

// ---------- V1.2.6 参考视频/音频写回 ----------

/** 镜头参考素材：新上传视频追加进 refs.refVideos。 */
function onShotAddRefVideo(relPath: string) {
  if (shot.value && relPath) addShotRefVideo(shot.value.id, relPath);
}

/** 删除镜头某条参考视频。 */
function onShotRemoveRefVideo(index: number) {
  if (shot.value) removeShotRefVideo(shot.value.id, index);
}

/** 镜头参考素材：新上传音频追加进 refs.refAudios。 */
function onShotAddRefAudio(relPath: string) {
  if (shot.value && relPath) addShotRefAudio(shot.value.id, relPath);
}

/** 删除镜头某条参考音频。 */
function onShotRemoveRefAudio(index: number) {
  if (shot.value) removeShotRefAudio(shot.value.id, index);
}

/** 参考视频/音频预览加载失败 → 隐藏元素。 */
function onRefMediaError(e: Event) {
  const el = e.target as HTMLElement;
  el.style.display = "none";
}

/** @命中资产 chip 点击 → 绑定到当前镜头（V1.6-B：cast 只加不减进集合——删除只走右侧人物 chip 的 ×，杜绝「点击高亮即删除」；地点显式单选）。 */
function bindMention(assetId: string, kind: AssetKind) {
  if (!shot.value) return;
  if (kind === "cast") setShotCasts(shot.value.id, mergeCastId(shot.value, assetId));
  else if (kind === "location") updateShotLocation(shot.value.id, assetId, true);
}
</script>

<template>
  <div class="wb">
    <!-- ═══════════ 项目选择页 ═══════════ -->
    <template v-if="view === 'home'">
      <div class="home">
        <header class="home-head">
          <div class="wb-brand">🎬 MiniMax Studio</div>
          <div class="home-conn" :title="wsHint(wb.wsStatus)">
            <span class="dot" :class="wsDotClass(wb.wsStatus)" />{{ wsLabel(wb.wsStatus, true) }}
          </div>
          <button class="btn" :disabled="homeBusy" @click="refreshHome">刷新</button>
        </header>

        <main class="home-body">
          <h2 class="home-title">最近项目</h2>
          <div v-if="projects.length" class="proj-list">
            <div v-for="p in projects" :key="p.id" class="proj-card" @click="openProject(p.id)">
              <span class="proj-ico">📁</span>
              <div class="proj-meta">
                <template v-if="renamingProjectId === p.id">
                  <div class="proj-rename">
                    <input
                      ref="projectRenameInputRef"
                      v-model="renamingProjectInput"
                      class="proj-rename-input"
                      :placeholder="p.name"
                      @click.stop
                      @keydown.enter="confirmProjectRename(p.id)"
                      @keydown.esc="cancelProjectRename"
                      @blur="confirmProjectRename(p.id)"
                    />
                    <button
                      class="rename-ok"
                      title="确认改名"
                      @mousedown.prevent
                      @click.stop="confirmProjectRename(p.id)"
                    >
                      ✓
                    </button>
                  </div>
                </template>
                <template v-else>
                  <div class="proj-name">
                    {{ p.name }}
                    <button class="rename-btn" title="重命名项目" @click.stop="startProjectRename(p)">✎</button>
                  </div>
                  <div class="proj-sub">{{ p.episodes }} 集 · {{ p.updatedAt }}</div>
                </template>
              </div>
              <button class="del-btn" title="删除项目" @click.stop="removeProject(p.id)">🗑</button>
              <span class="proj-arrow">›</span>
            </div>
          </div>
          <div v-else class="home-empty">还没有项目——从 ComfyUI 导入一个工程，或新建一个。</div>

          <div class="home-actions">
            <button class="btn big" :disabled="homeBusy" @click="refreshHome">＋ 导入 ComfyUI 工程</button>
            <button class="btn big" :disabled="homeBusy" @click="openScriptImport">📜 导入剧本</button>
            <button class="btn big" :disabled="homeBusy" @click="openStoryImport">📖 导入小说</button>
            <button class="btn big ghost" :disabled="homeBusy" @click="createNewProject">＋ 新建项目</button>
          </div>

          <!-- ComfyUI 生成时自动快照（用户只需在 ComfyUI 生成过一次） -->
          <div v-if="snapshots.length" class="snap-block">
            <h3 class="snap-title">ComfyUI 工程快照（最近生成过）</h3>
            <div v-for="s in snapshots" :key="s.id" class="snap-card" @click="importSnapshot(s.id)">
              <div class="snap-name">{{ s.name }}</div>
              <div class="snap-sub">{{ s.segments }} 镜 · {{ s.scenes }} 地点 · {{ s.time }}</div>
              <button class="del-btn" title="删除快照" @click.stop="removeSnapshot(s.id)">🗑</button>
            </div>
          </div>

          <div v-if="homeError" class="home-err">⚠ {{ homeError }}</div>

          <!-- 开发工具：V1.1 反向导入通道（调试入口，正式用户不碰） -->
          <details class="dev-tools">
            <summary>开发工具 ▾</summary>
            <div class="dev-row">
              <input
                ref="importInput"
                type="file"
                accept=".json,application/json"
                class="import-input"
                @change="onImportTimeline"
              />
              <button class="btn" @click="importInput?.click()">导入 timeline_data（调试）</button>
              <button class="btn" @click="loadSample">载入示例工程</button>
            </div>
          </details>
        </main>
      </div>
    </template>

    <!-- ═══════════ 分镜工作台 ═══════════ -->
    <template v-else>
      <!-- 顶栏 -->
      <header class="wb-head">
        <button class="btn nav" title="返回项目列表" @click="goHome">← 项目</button>
        <div class="wb-brand">🎬 MiniMax Studio</div>
        <div class="wb-project" v-if="episode">
          <template v-if="renamingEpisode">
            <input
              ref="episodeTitleInputRef"
              v-model="episodeTitleInput"
              class="ep-title-input"
              :placeholder="episode.title"
              @keydown.enter="confirmEpisodeRename"
              @keydown.esc="cancelEpisodeRename"
              @blur="confirmEpisodeRename"
            />
            <button class="rename-ok" title="确认改名" @mousedown.prevent @click="confirmEpisodeRename">✓</button>
          </template>
          <template v-else>
            <span class="wb-project-name" :title="`项目：${projectName}`">{{ projectName }}</span>
            <span class="wb-project-sep">/</span>
            <span class="wb-project-ep" :title="`集：${episode.title}`">{{ episode.title }}</span>
            <button class="rename-btn" title="重命名集" @click="startEpisodeRename">✎</button>
          </template>
        </div>
        <label class="preset-ctl" title="比例（含自定义）">
          <span class="ctl-label">比例</span>
          <select :value="activeRatioId" @change="onRatioChange">
            <option v-for="r in ratioOptions" :key="r.id" :value="r.id">
              {{ r.label }}{{ r.orientation ? ` ${r.orientation}` : "" }}
            </option>
          </select>
        </label>
        <label class="preset-ctl" title="百万像素档位（可手输任意数值）">
          <span class="ctl-label">MP</span>
          <input
            type="number"
            :value="mpInputValue"
            :disabled="activeRatioId === 'custom'"
            min="0.1" max="4.0" step="0.1"
            class="mp-input"
            @change="onMpChange"
          />
        </label>
        <label class="fps-ctl" title="输出帧率">
          <span class="ctl-label">帧率</span>
          <input type="number" :value="wb.frameRate" min="8" max="60" step="1" @change="onFpsChange" />
        </label>
        <label class="size-ctl" title="输出分辨率">
          <template v-if="activeRatioId === 'custom'">
            <input
              type="number"
              :value="wb.outputSize.width"
              min="256" max="2048" step="16"
              @change="onWidthChange"
            />
            <span class="size-x">×</span>
            <input
              type="number"
              :value="wb.outputSize.height"
              min="256" max="2048" step="16"
              @change="onHeightChange"
            />
          </template>
          <template v-else>
            <span class="size-ro">{{ wb.outputSize.width }} × {{ wb.outputSize.height }}</span>
          </template>
        </label>
        <button class="btn save" :class="{ dirty: wb.dirty }" :disabled="wb.running" @click="saveCurrentProject()">
          {{ savedTip ? "✓ 已保存" : wb.dirty ? "● 有未保存修改" : "💾 已保存" }}
        </button>
        <div class="wb-conn" :title="wsHint(wb.wsStatus)">
          <span class="dot" :class="wsDotClass(wb.wsStatus)" />{{ wsLabel(wb.wsStatus, false) }}
        </div>
        <div class="gen-ctl">
          <div class="wf-ctl" title="生成工作流（ComfyUI workflow_api）：默认内置 H3 r2v，可导入其他 workflow_api.json 手动切换">
            <label class="wf-pick">
              <span class="ctl-label">工作流</span>
              <select
                class="wf-select"
                :disabled="wb.running"
                :value="activeWorkflowId"
                @change="onWorkflowChange"
              >
                <option :value="BUILTIN_WORKFLOW_ID">默认 H3 r2v（内置）</option>
                <option v-for="e in workflowRegistry" :key="e.id" :value="e.id">{{ e.name }}</option>
              </select>
            </label>
            <button
              class="wf-badge"
              :class="workflowNodeBadge.cls"
              :title="workflowNodeBadge.title"
              :disabled="wb.running"
              @click="workflowPickOpen = true"
            >{{ workflowNodeBadge.text }}</button>
            <button
              v-if="activeWorkflowId !== BUILTIN_WORKFLOW_ID"
              class="btn ghost wf-del"
              title="删除当前工作流（回到默认内置）"
              :disabled="wb.running"
              @click="removeActiveWorkflow"
            >🗑</button>
            <button
              class="btn ghost wf-import"
              title="导入 ComfyUI 导出的 workflow_api.json"
              :disabled="wb.running"
              @click="openWorkflowImport"
            >＋ 导入</button>
            <button
              class="btn ghost wf-studio"
              title="工作流参数（快速编辑 steps/cfg/seed/LoRA）"
              :disabled="wb.running"
              @click="workflowParamsOpen = true"
            >⚙ 参数</button>
            <button
              class="btn ghost wf-studio"
              title="打开 Workflow Studio 可视化节点编辑器"
              :disabled="wb.running"
              @click="workflowStudioOpen = true"
            >◈ 编辑</button>
          </div>
          <div
            class="seed-ctl"
            :class="{ 'seed-fixed': seedMode === 'fixed' }"
            title="生成种子：🎲 随机 = 每次自动换新种子；🔒 固定 = 用同一种子复现画面。点 🎲 掷新种子并固定（换种子重试）"
          >
            <span class="ctl-label">种子</span>
            <button
              class="btn ghost seed-roll"
              title="掷一个新种子并固定（换种子重试）"
              :disabled="wb.running"
              @click="rollSeed"
            >🎲</button>
            <input
              v-if="seedMode === 'fixed'"
              class="seed-input"
              type="number"
              min="0" max="2147483647" step="1"
              :value="fixedSeed"
              :disabled="wb.running"
              @change="onSeedInput"
            />
            <span v-else class="seed-auto">自动随机</span>
            <button
              class="seed-pill"
              :class="seedMode === 'fixed' ? 'on' : ''"
              :title="seedMode === 'fixed' ? '当前固定种子，点击切换为随机（每次自动新种子）' : '当前随机种子，点击切换为固定（用当前值）'"
              :disabled="wb.running"
              @click="toggleSeedMode"
            >{{ seedMode === "fixed" ? "🔒 固定" : "🎲 随机" }}</button>
          </div>
          <button
            class="btn ghost"
            title="顺序播放全部已生成镜头"
            :disabled="wb.running"
            @click="playVideo('all')"
          >🎬 播放全片</button>
          <select
            class="gen-scope"
            :disabled="wb.running"
            :value="wb.genScope"
            title="生成范围"
            @change="onGenScopeChange"
          >
            <option value="all">全部镜头</option>
            <option value="current">当前镜头</option>
            <option value="selected">选中镜头{{ wb.selectedShotIds.length ? `（${wb.selectedShotIds.length}）` : "" }}</option>
            <option value="scene">当前地点</option>
            <option value="pending">未完成镜头</option>
            <option value="failed">失败镜头</option>
            <option value="fromShot">从此镜继续</option>
          </select>
          <div
            class="gen-mode"
            title="走查模式：多镜范围逐镜串行生成，每镜独立出片后自动衔接下一镜（不整片合并、低内存、失败即停，适合日常逐镜验收）。成片模式：多镜范围整片合并输出（需较高内存，适合全部镜头验收后一次性出成片）。"
          >
            <span class="gen-mode-label">模式</span>
            <label class="gen-mode-pill" :class="{ active: genMode === 'walkthrough' }">
              <input type="radio" name="genMode" value="walkthrough" v-model="genMode" :disabled="wb.running" />
              <span>走查</span>
            </label>
            <label class="gen-mode-pill" :class="{ active: genMode === 'final' }">
              <input type="radio" name="genMode" value="final" v-model="genMode" :disabled="wb.running" />
              <span>成片</span>
            </label>
          </div>
          <span
            v-if="genMode === 'final' && genScopeCount > 1"
            class="gen-final-hint"
            title="成片模式：多镜合并输出，可能需要较高内存（此前 6 镜 1475 帧全片导出曾触发内存保护崩溃）。建议所有镜头已逐镜走查验收后再切到此模式。"
          >⚠ 多镜合并</span>
          <button
            class="btn primary"
            :class="{ 'review-gate': genBlockedByReview && !genScopeDisabled }"
            :disabled="genScopeDisabled"
            :title="genBlockedByReview && !genScopeDisabled ? '⚠ 范围内含未采纳的 AI 草稿，请先在 AI 制作计划中采纳后再生成' : undefined"
            @click="handleGenerate()"
          >
            {{ wb.running ? "生成中…" : genBlockedByReview && !genScopeDisabled ? `⚠ 待采纳（${reviewScopeUnadoptedCount}）` : `▶ 生成 ${GEN_SCOPE_LABEL[wb.genScope]}（${genScopeCount}）` }}
          </button>
          <button
            class="btn export"
            :class="`export-${exportState}`"
            :disabled="wb.running || exportBusy"
            :title="exportBtnTitle"
            @click="openExportModal"
          >⭳ 导出</button>
          <button
            v-if="wb.running"
            class="btn danger"
            title="中断当前生成（正在跑的帧会被放弃，已生成的镜头保留）"
            @click="onTaskCancel"
          >
            ⏹ 取消
          </button>
        </div>
        <!-- P0-B（#482）：构建标识（v版本·b日期-时间，每次构建/启动唯一）——报 bug 对构建 -->
        <span
          class="wb-build-id"
          :title="'构建：' + BUILD_ID + '\n版本 ' + BUILD_ID.split('·')[0] + '｜本地时间戳 ' + BUILD_ID.split('·')[1]"
        >{{ BUILD_ID }}</span>
      </header>

      <!-- #640（2026-08-18）：剧本导入应用成功横幅（「已应用《…》：N 镜 · 角色待绑定」），
           消除「顶部的 shot 还是之前剧本缓存」的误判；点 ✕ 关闭。 -->
      <div v-if="importBanner" class="import-banner">
        <span class="ib-badge">✅ 已应用</span>
        <span class="ib-title">{{ importBanner.title }}</span>
        <span class="ib-meta">{{ importBanner.shotCount }} 镜 · {{ importBanner.locCount }} 地点</span>
        <template v-if="importBanner.pendingCasts.length">
          <span class="ib-warn">⚠ {{ importBanner.pendingCasts.length }} 位角色待绑定参考图：{{ importBanner.pendingCasts.join("、") }}</span>
        </template>
        <span class="ib-hint">点开任意镜头，在左侧「资产」区上传并绑定角色参考图后再生成</span>
        <button class="ib-close" title="关闭横幅" @click="importBanner = null">✕</button>
      </div>

      <!-- V1.10-C：生成中顶栏常驻横幅（🎬 Shot x/y + 进度条 + 预测区间 + 可信度；点击展开详情） -->
      <div v-if="runBanner" class="gen-banner" :class="{ open: bannerOpen }" @click="bannerOpen = !bannerOpen">
        <div class="gen-banner-main">
          <span class="gen-banner-badge">🎬 生成中</span>
          <span class="gen-banner-shot">{{ bannerShotLabel }}</span>
          <span class="gen-banner-name" :title="runBanner.shotName">{{ runBanner.shotName }}</span>
          <div class="gen-banner-bar">
            <div class="gen-banner-bar-fill" :style="{ width: bannerPct + '%' }" />
          </div>
          <span class="gen-banner-phase" :title="`当前阶段（真实步进，非伪造百分比）`">{{ bannerPhaseLabel }}</span>
          <span class="gen-banner-eta" :title="runBanner.eta ? CONF_LEVEL_LABEL[runBanner.confLevel] : ''">
            {{ runBanner.confDot }} 约剩 {{ bannerEtaText }}
          </span>
          <span class="gen-banner-time">{{ formatEtaMs(runBanner.elapsed) }}</span>
          <span class="gen-banner-caret">{{ bannerOpen ? "▾" : "▴" }}</span>
        </div>

        <div v-if="bannerOpen" class="gen-banner-detail">
          <div class="gen-banner-detail-grid">
            <div class="gbd-item"><em>当前阶段</em><b>{{ bannerPhaseLabel }}</b></div>
            <div class="gbd-item"><em>已用</em><b>{{ formatEtaMs(runBanner.elapsed) }}</b></div>
            <div class="gbd-item"><em>预计剩余区间</em><b>{{ bannerEtaText }}</b></div>
            <div class="gbd-item"><em>预计完成区间</em><b>{{ bannerFinishText }}</b></div>
            <div class="gbd-item"><em>本镜预计</em><b>{{ runBanner.eta ? formatEtaMs(runBanner.eta.currentShotRemainingMs) : "—" }}</b></div>
            <div class="gbd-item"><em>本项目预计</em><b>{{ runBanner.eta ? formatEtaMs(runBanner.eta.estimatedTotalMs) : "—" }}</b></div>
          </div>
          <div class="gen-banner-detail-foot">
            <span class="gbd-conf" :title="runBanner.eta ? CONF_LEVEL_LABEL[runBanner.confLevel] : ''">
              {{ runBanner.confDot }}
              {{ runBanner.eta
                ? (runBanner.confLevel === 2 ? "预测较稳定" : runBanner.confLevel === 1 ? "预测正在收敛" : "数据不足 · 经验估算")
                : "数据不足" }}
            </span>
            <span class="gbd-actions">
              <button class="btn ghost gbd-btn" title="打开任务中心查看详情" @click.stop="taskCenterSignal++">查看任务详情</button>
              <button class="btn danger gbd-btn" title="中断当前生成（正在跑的帧会被放弃，已生成的镜头保留）" @click.stop="onTaskCancel">⏹ 停止</button>
            </span>
          </div>
        </div>
      </div>

      <!-- V1.7 Phase 4：AI 制作计划审核横幅（N 镜待审核 + 就绪统计 + 采纳全部） -->
      <div v-if="reviewActive" class="review-banner">
        <div class="review-banner-info">
          <span class="review-banner-title">🤖 AI 制作计划</span>
          <span class="review-banner-count">{{ pendingReviewShots.length }} 镜待审核</span>
          <span class="review-banner-stat">{{ reviewReadyCount }}/{{ pendingReviewShots.length }} 镜就绪</span>
        </div>
        <div class="review-banner-actions">
          <button
            class="btn ghost review-adopt-all"
            title="一键采纳全部 AI 草稿（采纳后即可正常生成；草稿内容保留可继续手动修改）"
            :disabled="wb.running || pendingReviewShots.length === 0"
            @click="adoptAllAiDrafts()"
          >✓ 采纳全部</button>
        </div>
      </div>

      <div class="wb-body">
        <!-- 左：项目结构 -->
        <aside class="wb-left">
          <div class="wb-left-title">
            📍 地点库
            <button class="icon-btn add-scene" title="新增地点" @click="addSceneNow">＋ 地点</button>
          </div>
          <!-- V1.10-D：项目层历史耗时统计（平均/最快/最慢/已生成 N 镜） -->
          <div v-if="projectStats" class="proj-stats" title="本项目历史生成耗时统计（基于最近完成镜头采样）">
            <span class="ps-item">平均 <b>{{ fmtStats(projectStats.avgMs) }}</b></span>
            <span class="ps-item">最快 <b class="ps-fast">{{ fmtStats(projectStats.fastestMs) }}</b></span>
            <span class="ps-item">最慢 <b class="ps-slow">{{ fmtStats(projectStats.slowestMs) }}</b></span>
            <span v-if="projectGeneratedText" class="ps-item ps-done">{{ projectGeneratedText }}</span>
          </div>
          <div v-for="sc in episode?.scenes" :key="sc.id" class="scene-block">
            <div
              class="scene-name"
              :class="{ active: scene?.id === sc.id }"
              @click="selectSceneSettings(sc.id)"
            >
              <span class="scene-ico">📍</span>
              <span class="scene-name-text">{{ sc.name }}</span>
              <span class="scene-meta">{{ sc.shots.length }} 镜</span>
              <span class="scene-ops">
                <button
                  class="icon-btn scene-play"
                  title="播放该地点已生成镜头"
                  :disabled="wb.running"
                  @click.stop="playVideo('scene', { sceneId: sc.id })"
                >▶</button>
                <button class="icon-btn" title="新增镜头" @click.stop="addShotIn(sc.id)">＋</button>
                <button class="icon-btn" title="重命名地点" @click.stop="renameSceneBy(sc.id)">✎</button>
                <button class="icon-btn" title="删除地点" @click.stop="removeSceneBy(sc.id)">🗑</button>
              </span>
            </div>
            <div class="shot-list">
              <div
                v-for="s in shotsOf(sc.id)"
                :key="s.id"
                class="shot-card"
                :class="{
                  active: shot?.id === s.id,
                  sel: wb.selectedShotIds.includes(s.id),
                  'shot-draft': s.aiDraft && !s.adopted,
                  'shot-draft-ok': s.aiDraft && s.adopted,
                }"
                @click="selectShot(s.id)"
                @mouseenter="onShotCardEnter($event)"
              >
                <input
                  type="checkbox"
                  class="shot-sel"
                  :checked="wb.selectedShotIds.includes(s.id)"
                  title="多选（生成范围=选中镜头）"
                  @click.stop="toggleShotSelected(s.id)"
                />
                <span class="shot-thumb">🖼</span>
                <span class="shot-name">{{ s.name ?? s.id }}
                  <span v-if="(s.generations?.length ?? 0) > 0" class="shot-gen-badge" :title="`${s.generations!.length} 个生成版本${s.activeGenerationId ? '（⭐ 已选当前成片）' : ''}`">{{ s.generations!.length }}版{{ s.activeGenerationId ? "⭐" : "" }}</span>
                </span>
                <span class="shot-dur">{{ fmtSec(s.durationSec) }}</span>
                <span v-if="lastGenTextOf(s)" class="shot-lastgen" title="上次生成耗时（特征匹配：同任务类型/分辨率/帧数，取最近一次成功样本）">⏱ {{ lastGenTextOf(s) }}</span>
                <template v-if="s.aiDraft">
                  <span
                    v-if="!s.adopted"
                    class="shot-draft-badge"
                    :class="reviewStatusOf(s).ok ? 'ready' : 'notready'"
                    :title="
                      reviewStatusOf(s).ok
                        ? 'AI 草稿就绪（资产 / Prompt / 运镜三项齐全），采纳后即可生成'
                        : reviewStatusOf(s).issues.map((i) => i.text).join('；') + (reviewStatusOf(s).issues.some((i) => i.kind === 'asset') ? '（点此补资产）' : '')
                    "
                    :style="reviewStatusOf(s).ok ? undefined : { cursor: 'pointer' }"
                    @click.stop="openAssetConfirm(s)"
                  >🤖 {{ reviewStatusOf(s).ok ? "✓" : "⚠" }}</span>
                  <button
                    v-if="!s.adopted"
                    class="shot-draft-adopt"
                    title="采纳此镜 AI 草稿（采纳后即可生成；草稿内容保留可继续手动修改）"
                    :disabled="wb.running"
                    @click.stop="adoptDraft(s.id)"
                  >✓ 采纳</button>
                  <span
                    v-else
                    class="shot-draft-badge adopted"
                    title="此镜 AI 草稿已采纳"
                  >🤖 ✓ 已采纳</span>
                </template>
                <span class="shot-status" :class="statusClassOf(s.id)" :title="statusTitleOf(s.id)" />
                <span class="shot-ops">
                  <button class="icon-btn" title="上移镜头" @click.stop="mvShot(s.id, -1)">⇡ 上移</button>
                  <button class="icon-btn" title="下移镜头" @click.stop="mvShot(s.id, 1)">⇣ 下移</button>
                  <button class="icon-btn" title="复制镜头" @click.stop="dupShot(s.id)">⧉ 复制</button>
                  <button class="icon-btn" title="重命名镜头" @click.stop="renameShotBy(s.id)">✎ 重命名</button>
                  <button class="icon-btn" title="删除镜头" @click.stop="removeShotBy(s.id)">🗑 删除</button>
                </span>
                <button
                  class="shot-gen-mini shot-play"
                  title="播放此镜"
                  :disabled="!isShotCached(s.id)"
                  @click.stop="playVideo('shot', { targetShotId: s.id })"
                >🎬</button>
                <button
                  class="shot-gen-mini"
                  title="只生成此镜"
                  :disabled="wb.running"
                  @click.stop="generateShot(s.id)"
                >▶</button>
                <button
                  class="shot-gen-mini shot-dl"
                  title="下载该镜 mp4"
                  :disabled="wb.running || !isShotCached(s.id)"
                  @click.stop="downloadShotMp4(s.id)"
                >⬇</button>
              </div>
            </div>
          </div>
          <!-- P2-P5（#542）：Global Story Bible 面板（「📍 地点库」下方；跨章世界状态库，
               纯前端刷新即生效；/bible 三路由需重启 Comfy Desktop 后可用） -->
          <BiblePanel v-if="wb.project" :project-id="wb.project.id" />
          <!-- Phase 2-C（#575）：Voice Cast 全局音色面板（Bible 下方；写 project.voiceCast，
               纯前端刷新即生效；/tts/voices 数据源由本视图统一加载传入） -->
          <VoiceCastPanel
            v-if="wb.project"
            :project-id="wb.project.id"
            :voices="ttsVoices"
            :voices-error="ttsVoicesError"
            :voices-loading="ttsVoicesLoading"
            @refresh="loadTtsVoices"
          />
          <!-- v2.0 P3（#631+）：Visual Style 全剧画风面板（Voice Cast 下方；写 project.styleProfile，
               纯前端刷新即生效；/style/presets 数据源由面板自行拉取，需重启 Comfy Desktop 后可用） -->
          <VisualStylePanel v-if="wb.project" :project-id="wb.project.id" />
        </aside>

        <!-- 中：唯一大播放器 + 纯版本条 + Shot 信息卡（V1.11.1 UI 收敛）。P1-A-5（#525）：
             Timeline = 中间主区主视图/导航入口（全片播放顺序 + 剧情概览 + 地点/人物筛选）。 -->
        <main class="wb-center">
          <!-- P1-A-5（#525）：剧情概览 + 全片播放顺序时间线（顶部主视图）。
               Timeline 是播放顺序与主要导航的唯一权威源；地点只维护地点身份/资产/连续性状态。 -->
          <div class="tl-overview">
            <div class="tl-overview-main">
              <span class="tl-overview-title">🎞 全片播放顺序</span>
              <span class="tl-overview-stats">
                {{ timelineShots.length }} 镜 · {{ tlLocCount }} 地点 · {{ tlCastCount }} 人物
                <template v-if="tlTotalDur"> · 共 {{ tlTotalDur }}</template>
              </span>
              <span class="tl-head-hint">{{ episode?.timeline?.length ? "拖拽卡片调整交叉剪辑顺序" : "按地点顺序播放 · 拖拽卡片可自定义" }}</span>
            </div>
            <div class="tl-overview-filters">
              <select v-model="tlLocFilter" class="tl-filter-select" title="按地点筛选时间线">
                <option value="__all__">📍 全部地点</option>
                <option v-for="sc in tlLocOptions" :key="sc.id" :value="sc.id">{{ sc.name }}</option>
              </select>
              <select v-model="tlCastFilter" class="tl-filter-select" title="按人物筛选时间线" :disabled="!tlCastOptions.length">
                <option value="__all__">👤 全部人物</option>
                <option v-for="c in tlCastOptions" :key="c" :value="c">{{ c }}</option>
              </select>
              <button v-if="filterActive" class="tl-filter-clear" title="清除筛选，回到全片顺序" @click="clearTlFilters">✕ 清除筛选</button>
            </div>
          </div>
          <div class="timeline" @dragover.prevent @drop.prevent>
            <div
              v-for="(s, i) in filteredTimelineShots"
              :key="s.id"
              class="tl-card"
              :class="{
                active: shot?.id === s.id,
                'tl-dragging': tlDragId === s.id,
                'tl-drop-over': tlDropOverId === s.id,
              }"
              draggable="true"
              :title="`播放 #${i + 1} · ${tlSceneName(s.sceneId)}（拖拽调整顺序）`"
              @click="selectShotAndPlay(s.id)"
              @dragstart="onTlDragStart(s.id, $event)"
              @dragover.prevent="onTlDragOver(s.id)"
              @drop.prevent="onTlDrop(s.id)"
              @dragend="onTlDragEnd"
            >
              <div class="tl-thumb">
                <span class="tl-scene-bar" :style="{ background: tlSceneColor(s.sceneId) }" :title="tlSceneName(s.sceneId)" />
                <img v-if="thumbOf(s.id)" :src="thumbOf(s.id)" alt="" loading="lazy" />
                <span v-else class="tl-thumb-ph">🎬</span>
                <span class="tl-thumb-idx">{{ i + 1 }}</span>
              </div>
              <div class="tl-info">
                <span class="tl-scene-tag" :style="{ color: tlSceneColor(s.sceneId), borderColor: tlSceneColor(s.sceneId) }">{{ tlSceneName(s.sceneId) }}</span>
                <span class="tl-name">{{ s.name ?? s.id }}
                  <span v-if="(s.generations?.length ?? 0) > 0" class="tl-gen-badge">{{ s.generations!.length }}版</span>
                </span>
                <span class="tl-meta">{{ fmtSec(s.durationSec) }}</span>
                <span v-if="tlSummaryOf(s)" class="tl-summary">{{ tlSummaryOf(s) }}</span>
              </div>
              <span class="tl-status" :class="statusClassOf(s.id)" :title="statusTitleOf(s.id)" />
            </div>
            <div v-if="!filteredTimelineShots.length" class="tl-empty">
              {{ timelineShots.length ? "没有符合当前筛选的镜头 · 点「✕ 清除筛选」回到全片顺序" : "暂无镜头 · 在左侧地点库添加镜头，或导入剧本" }}
            </div>
          </div>

          <div class="player-wrap">
            <div class="player-head">
              <span class="ph-shot">🎬 {{ shot?.name ?? "未选择" }}</span>
              <span v-if="shot" class="ph-scene">{{ shotScene?.name ?? "" }}</span>
              <span v-if="shot" class="ph-dur">{{ fmtSec(shot.durationSec) }}</span>
              <span class="ph-spacer" />
              <button
                class="icon-btn ph-cinema"
                title="全屏预览（Cinema Mode）"
                :disabled="!wb.finalVideo"
                @click="openCinema"
              >⛶</button>
            </div>
            <div class="player">
              <template v-if="wb.finalVideo">
                <video
                  :src="wb.finalVideo.url"
                  class="player-video"
                  controls
                  autoplay
                  playsinline
                  @ended="onPlayerEnded"
                  @error="onPlayerVideoError"
                  @loadeddata="onPlayerVideoLoaded"
                />
                <div v-if="playerErr" class="player-err">{{ playerErr }}</div>
                <div class="player-video-meta">{{ playerMeta }}</div>
              </template>
              <template v-else-if="wb.preview">
                <img :src="wb.preview" class="player-img" />
              </template>
              <template v-else>
                <div class="player-empty">
                  <div class="player-placeholder">🎞 当前镜头 {{ shot?.name ?? "未选择" }}</div>
                  <div v-if="shot" class="player-prompt">{{ shot.content.visual }}</div>
                </div>
              </template>
            </div>
          </div>

          <!-- V1.11.1：纯版本条（图片缩略图，非播放器；点击切换中央唯一大播放器）。
               「选版本」唯一入口。卡只含 #N / 缩略图 / 时长 / 时间 / ⭐|🆕 徽标；Prompt 与参数在下方信息卡。 -->
          <div v-if="shot && shotGenerations.length" class="gen-strip">
            <div class="gen-strip-head">
              <span class="gen-strip-title">生成版本 · {{ shotGenerations.length }} 个</span>
              <span class="gen-strip-hint">点击缩略图在主播放器查看 · ⭐ 当前成片 · 🆕 最新</span>
            </div>
            <div class="gen-strip-list">
              <div
                v-for="g in shotGenerations"
                :key="g.id"
                class="gen-chip"
                :class="{ active: viewedGenId === g.id, adopted: activeGenId === g.id, newest: isNewestGen(g) }"
                :title="`Generation #${genIndex(g)} · ${genStateLabel(g)}`"
                @click="viewGeneration(g)"
              >
                <div class="gen-thumb">
                  <img v-if="genThumbOf(g.id)" :src="genThumbOf(g.id)" alt="" loading="lazy" />
                  <span v-else class="gen-thumb-ph">🎬</span>
                  <span class="gen-chip-idx">#{{ genIndex(g) }}</span>
                  <button
                    v-if="activeGenId !== g.id"
                    class="gen-del-corner"
                    title="删除该版本"
                    @click.stop="removeGen(shot.id, g.id)"
                  >🗑</button>
                </div>
                <div class="gen-meta">
                  <span class="gen-dur">{{ genDurText(g) }}</span>
                  <span class="gen-time">{{ genCreatedAt(g) }}</span>
                  <span class="gen-badges">
                    <span v-if="activeGenId === g.id" class="gb gb-adopted">⭐ 当前</span>
                    <span v-if="isNewestGen(g)" class="gb gb-newest">🆕 最新</span>
                  </span>
                </div>
              </div>
            </div>
            <div class="gen-strip-foot">
              <!-- P2-⑨：全屏放大入口唯一 = 主播放器右上角 [⛶]（ph-cinema）。
                   版本条 foot 只保留版本操作「⭐ 设为当前成片」，避免同屏双全屏按钮（信息只展示一次）。 -->
              <button
                class="btn primary gen-foot-btn gen-foot-adopt"
                title="把当前查看的版本设为当前成片"
                :disabled="!viewedGen || activeGenId === viewedGen.id"
                @click="setActiveCurrent"
              >⭐ 设为当前成片</button>
            </div>
          </div>

          <!-- V1.11.1：Shot 信息卡（纯文字：剧本原文 + Prompt 五区 + 资产 + 生成参数）。
               不再内嵌播放器——视频只在上方唯一大播放器播放；此卡回答「这个视频为什么是这样的」。 -->
          <div v-if="shot" class="shot-detail" :class="{ open: shotDetailOpen }">
            <div class="shot-detail-head">
              <button
                class="sd-collapse"
                :title="shotDetailOpen ? '收起镜头详情' : '展开镜头详情'"
                @click="shotDetailOpen = !shotDetailOpen"
              >{{ shotDetailOpen ? "▾" : "▸" }}</button>
              <span class="sd-title">🎬 {{ shot.name ?? shot.id }}</span>
              <span class="sd-scene">{{ shotScene?.name ?? "" }}</span>
              <span class="sd-chip">{{ fmtSec(shot.durationSec) }}</span>
              <span v-if="detailGenLabel" class="sd-chip sd-gen-chip" :class="{ 'sd-gen-active': activeGenId === viewedGenId }">{{ detailGenLabel }}</span>
              <span v-if="shot.generation?.taskType" class="sd-chip">{{ taskTypeLabel(shot.generation.taskType) }}</span>
              <span v-if="shot.generation?.continuityMode" class="sd-chip">{{ continuityLabel(shot.generation.continuityMode) }}</span>
              <span class="sd-status" :class="statusClassOf(shot.id)" :title="statusTitleOf(shot.id)">{{ statusTitleOf(shot.id) }}</span>
              <div class="sd-actions">
                <button class="btn ghost sd-btn" title="跳到右侧 Prompt 编辑器（当前草稿）" @click="scrollToPromptEditor">✎ 编辑</button>
                <button
                  class="btn ghost sd-btn"
                  title="只生成此镜"
                  :disabled="wb.running"
                  @click="generateShot(shot.id)"
                >🔄 重新生成</button>
                <button
                  v-if="viewedSeed !== undefined"
                  class="btn ghost sd-btn"
                  title="用此版本的种子重新生成（换一种子复现该画面）"
                  :disabled="wb.running"
                  @click="replayViewedSeed"
                >🎲 以此种子重放</button>
                <button
                  class="icon-btn sd-btn sd-dl"
                  title="下载该镜 mp4"
                  :disabled="wb.running || !isShotCached(shot.id)"
                  @click="downloadShotMp4(shot.id)"
                >⬇</button>
              </div>
            </div>
            <div v-if="shotDetailOpen" class="shot-detail-body">
              <details v-if="shot.description" class="sd-original" :open="true">
                <summary>📜 剧本原文</summary>
                <pre class="sd-original-text">{{ shot.description }}</pre>
              </details>
              <details v-if="shot.h3Prompt" class="sd-h3" :open="false">
                <summary>
                  🧬 H3 三层
                  <span v-if="snapshotProvCount" class="sd-h3-count">{{ snapshotProvCount }}</span>
                </summary>
                <div v-if="shot.h3Prompt.userOriginalIntent" class="sd-h3-row">
                  <span class="sd-h3-k">原文</span>
                  <span class="sd-h3-v">{{ shot.h3Prompt.userOriginalIntent }}</span>
                </div>
                <div v-if="snapshotFacts.length || shot.h3Prompt.intent.composition || shot.h3Prompt.intent.emotion" class="sd-h3-row">
                  <span class="sd-h3-k">AI 理解</span>
                  <span v-if="snapshotFacts.length" class="sd-h3-badge u" title="原文逐字事实">原文 {{ snapshotFacts.length }}</span>
                  <span v-if="shot.h3Prompt.intent.composition" class="sd-h3-badge ai" title="AI 补全（原文没有的画面意图）">AI 构图</span>
                  <span v-if="shot.h3Prompt.intent.emotion" class="sd-h3-badge ai" title="AI 推断情绪">AI 情绪</span>
                  <span v-if="shot.h3Prompt.intent.cameraDesc" class="sd-h3-badge rule" title="规则运镜状态机生成">规则运镜</span>
                </div>
                <div v-if="shot.h3Prompt.prompt.integratedDescription" class="sd-h3-row">
                  <span class="sd-h3-k">H3 画面</span>
                  <span class="sd-h3-v">{{ shot.h3Prompt.prompt.integratedDescription }}</span>
                </div>
                <div v-if="shot.h3Prompt.prompt.soundscape" class="sd-h3-row">
                  <span class="sd-h3-k">H3 声音</span>
                  <span class="sd-h3-v">{{ shot.h3Prompt.prompt.soundscape }}</span>
                </div>
                <div v-if="shot.h3Prompt.prompt.music" class="sd-h3-row">
                  <span class="sd-h3-k">H3 配乐</span>
                  <span class="sd-h3-v">{{ shot.h3Prompt.prompt.music }}</span>
                </div>
                <div v-if="shot.h3Prompt.prompt.references?.length" class="sd-h3-refs">
                  <img
                    v-for="r in shot.h3Prompt.prompt.references"
                    :key="r.entityKey"
                    :src="comfyInputUrl(r.imageFile)"
                    class="sd-h3-ref"
                    :title="`${r.entityName}（${r.imageFile}）`"
                    alt=""
                    @error="hideEl"
                  />
                </div>
              </details>
              <div v-if="infoPrompt" class="sd-copy-row">
                <span class="sd-copy-hint">当前显示 Generation #{{ genIndex(viewedGen!) }} 的历史快照</span>
                <button class="btn ghost sd-btn" title="把该版 Prompt 复制到当前草稿（可编辑）" @click="copySnapshotToDraft">📋 复制该版到当前草稿</button>
              </div>
              <div class="sd-prompt" :class="{ muted: !((infoPrompt?.visual ?? shot.content.visual) || '') }">
                <span class="sd-label">✨ 画面描述</span>
                <span class="sd-val">{{ (infoPrompt?.visual ?? shot.content.visual) || "（空）" }}</span>
              </div>
              <div v-if="(infoPrompt?.cameraText ?? shot.content.cameraText)" class="sd-prompt">
                <span class="sd-label">🎥 摄影</span>
                <span class="sd-val">{{ infoPrompt?.cameraText ?? shot.content.cameraText }}</span>
              </div>
              <div v-if="(infoPrompt?.style ?? shot.content.style)" class="sd-prompt">
                <span class="sd-label">🎨 风格</span>
                <span class="sd-val">{{ infoPrompt?.style ?? shot.content.style }}</span>
              </div>
              <div v-if="(infoPrompt?.soundText ?? shot.content.soundText)" class="sd-prompt">
                <span class="sd-label">🔊 声音</span>
                <span class="sd-val">{{ infoPrompt?.soundText ?? shot.content.soundText }}</span>
              </div>
              <div v-if="(infoPrompt?.negativePrompt ?? shot.content.negativePrompt)" class="sd-prompt">
                <span class="sd-label">❌ 负面</span>
                <span class="sd-val">{{ infoPrompt?.negativePrompt ?? shot.content.negativePrompt }}</span>
              </div>
              <div v-if="infoAssetNames.length" class="sd-prompt">
                <span class="sd-label">👤 资产</span>
                <span class="sd-val">{{ infoAssetNames.join("、") }}</span>
              </div>
              <div class="sd-gen">
                <span v-for="p in infoGenParams" :key="p.label" class="sd-gen-chip">{{ p.label }}：{{ p.val }}</span>
              </div>
            </div>
          </div>

          <!-- P1-A-5（#525）：时间线已上移为中间主区顶部主视图（见 wb-center 开头），此位置不再重复。 -->

          <!-- V1.11.1：Cinema Mode（全屏唯一放大入口）。大视频 + 版本圆点条 + 当前成片标签 + 设为主/关闭。 -->
          <Teleport to="body">
            <div v-if="cinemaOpen" class="cinema-mask" @click.self="closeCinema">
              <div class="cinema-top">
                <span class="cinema-title">🎬 {{ shot?.name ?? "未选择" }}{{ shotScene?.name ? ` · ${shotScene.name}` : "" }}</span>
                <button class="btn ghost cinema-close" title="关闭全屏（Esc）" @click="closeCinema">✕ 关闭</button>
              </div>
              <div class="cinema-stage">
                <video
                  v-if="wb.finalVideo"
                  :src="wb.finalVideo.url"
                  class="cinema-video"
                  controls
                  autoplay
                  playsinline
                  @ended="onPlayerEnded"
                  @error="onPlayerVideoError"
                  @loadeddata="onPlayerVideoLoaded"
                />
                <div v-if="wb.finalVideo && playerErr" class="cinema-err">{{ playerErr }}</div>
                <div v-else-if="!wb.finalVideo" class="cinema-empty">暂无视频</div>
              </div>
              <div class="cinema-bar">
                <span class="cinema-gen-label">{{ cinemaGenLabel }}</span>
                <div class="cinema-dots">
                  <button
                    v-for="g in shotGenerations"
                    :key="g.id"
                    class="cinema-dot"
                    :class="{ on: viewedGenId === g.id, star: activeGenId === g.id }"
                    :title="`Generation #${genIndex(g)} · ${genStateLabel(g)}`"
                    @click="viewGeneration(g)"
                  >{{ genIndex(g) }}</button>
                </div>
                <span class="cinema-spacer" />
                <button
                  class="btn primary cinema-adopt"
                  title="把当前查看的版本设为当前成片"
                  :disabled="!viewedGen || activeGenId === viewedGen.id"
                  @click="setActiveCurrent"
                >⭐ 设为当前成片</button>
              </div>
            </div>
          </Teleport>
        </main>

        <!-- 右：当前镜设置 / 场景设置 -->
        <aside class="wb-right">
          <!-- ══════ 场景设置 ══════ -->
          <template v-if="sceneMode && scene">
            <div class="wb-right-title">📍 地点设置 · {{ scene.name }}</div>
            <label class="fld">
              <span>名称</span>
              <input :value="scene.name" @change="(e) => onSceneText(e, 'name')" />
            </label>
            <label class="fld">
              <span>描述</span>
              <textarea :value="scene.description ?? ''" rows="3" @change="(e) => onSceneText(e, 'description')" />
            </label>
            <div class="fld-row">
              <label class="fld">
                <span>时间</span>
                <input :value="scene.time ?? ''" placeholder="夜晚" @change="(e) => onSceneText(e, 'time')" />
              </label>
              <label class="fld">
                <span>天气</span>
                <input :value="scene.weather ?? ''" placeholder="小雨" @change="(e) => onSceneText(e, 'weather')" />
              </label>
            </div>
            <label class="fld">
              <span>地点</span>
              <input :value="scene.location ?? ''" placeholder="天台" @change="(e) => onSceneText(e, 'location')" />
            </label>
            <label class="fld">
              <span>参考图（上传到 ComfyUI input）</span>
              <ImageUploadBox
                :api="wb.runService.api"
                :model-value="scene.referenceImage ?? ''"
                subfolder="minimax_studio/assets"
                placeholder="上传地点参考图"
                label="地点参考图"
                @update:model-value="onSceneRefImage"
              />
            </label>

            <div class="wb-right-sub">镜头默认值（新镜头自动继承）</div>
            <label class="fld">
              <span>默认人物</span>
              <div class="def-row">
                <select :value="scene.defaultCastId ?? ''" @change="(e) => onSceneDefault('cast', e)">
                  <option value="">（无）</option>
                  <option v-for="a in sceneCast" :key="a.id" :value="a.id">🎬 {{ a.name }}</option>
                  <option v-for="a in globalCast" :key="a.id" :value="a.id">🌐 {{ a.name }}</option>
                </select>
                <button class="icon-btn" title="新建人物" @click="addSceneAsset('cast')">＋</button>
              </div>
            </label>
            <label class="fld">
              <span>默认地点</span>
              <div class="def-row">
                <select :value="scene.defaultLocationId ?? ''" @change="(e) => onSceneDefault('location', e)">
                  <option value="">（无）</option>
                  <option v-for="a in sceneLocs" :key="a.id" :value="a.id">🎬 {{ a.name }}</option>
                  <option v-for="a in globalLocs" :key="a.id" :value="a.id">🌐 {{ a.name }}</option>
                </select>
                <button class="icon-btn" title="新建地点" @click="addSceneAsset('location')">＋</button>
              </div>
            </label>
            <label class="fld">
              <span>默认风格</span>
              <select :value="scene.defaultStyleId ?? ''" @change="(e) => onSceneDefault('style', e)">
                <option value="">（无）</option>
                <option v-for="a in sceneStyles" :key="a.id" :value="a.id">{{ a.name }}</option>
              </select>
            </label>

            <div class="wb-right-sub">素材库</div>
            <div class="lib-scope">
              <button class="lib-scope-btn" :class="{ on: libScope === 'scene' }" @click="setLibScope('scene')">📍 地点素材</button>
              <button class="lib-scope-btn" :class="{ on: libScope === 'episode' }" @click="setLibScope('episode')">🌐 全局资产</button>
            </div>
            <div v-if="libMsg" class="lib-msg">⚠ {{ libMsg }}</div>
            <div v-for="group in libGroups" :key="group.kind" class="asset-group">
              <div class="asset-group-head">
                <span>{{ group.label }}</span>
                <button class="icon-btn" title="新建{{ group.label }}" @click="addLibAsset(group.kind)">＋</button>
              </div>
              <div v-if="group.items.length" class="lib-grid">
                <div
                  v-for="a in group.items"
                  :key="a.id"
                  class="lib-card"
                  :class="{ editing: libEditing && libEditing.kind === group.kind && libEditing.id === a.id }"
                >
                  <template v-if="libEditing && libEditing.kind === group.kind && libEditing.id === a.id">
                    <div class="lib-edit">
                      <label class="fld"><span>名称</span><input v-model="libEditForm.name" /></label>
                      <label class="fld"><span>描述</span><textarea v-model="libEditForm.description" rows="2" /></label>
                      <label class="fld"><span>别名（逗号分隔，@ 匹配用）</span><input v-model="libEditForm.aliases" placeholder="如：林雪·成年, 雪儿" /></label>
                      <label class="fld">
                        <span>参考图</span>
                        <ImageUploadBox
                          :api="wb.runService.api"
                          :model-value="a.imageFile"
                          subfolder="minimax_studio/assets"
                          :label="a.name"
                          @update:model-value="(v) => onLibAssetImage(group.kind, a.id, v)"
                        />
                      </label>
                      <div class="lib-edit-actions">
                        <button class="btn small primary" @click="saveEditAsset">保存</button>
                        <button class="btn small ghost" @click="cancelEditAsset">取消</button>
                      </div>
                    </div>
                  </template>
                  <template v-else>
                    <div class="lib-card-top">
                      <ImageUploadBox
                        compact
                        :api="wb.runService.api"
                        :model-value="a.imageFile"
                        subfolder="minimax_studio/assets"
                        :label="a.name"
                        @update:model-value="(v) => onLibAssetImage(group.kind, a.id, v)"
                      />
                      <span class="lib-card-name" :title="a.name">{{ a.name }}</span>
                      <span class="lib-card-src" :title="libScope === 'episode' ? '全局资产' : '地点素材'">{{ libScope === 'episode' ? '🌐' : '📍' }}</span>
                    </div>
                    <div v-if="a.description" class="lib-card-desc">{{ a.description }}</div>
                    <div v-if="a.aliases?.length" class="lib-card-aliases">别名：{{ a.aliases.join('、') }}</div>
                    <div class="lib-card-ops">
                      <button class="icon-btn" title="编辑（改名/描述/别名/换图）" @click.stop="startEditAsset(group.kind, a.id)">✎</button>
                      <button v-if="libScope === 'episode'" class="icon-btn" title="复制到本地点" @click.stop="copyGlobalToScene(group.kind, a.id)">⧉</button>
                      <button class="icon-btn" title="删除" @click.stop="removeLibAsset(group.kind, a.id)">🗑</button>
                    </div>
                  </template>
                </div>
              </div>
              <div v-else class="asset-empty">空</div>
            </div>
          </template>

          <!-- ══════ 镜头设置 ══════ -->
          <template v-else-if="shot">
            <div class="wb-right-title">镜头设置</div>
            <button
              class="btn primary shot-gen"
              title="生成当前镜头（单镜走查，segments 安全路径）"
              :disabled="wb.running"
              @click="generateShot(shot.id)"
            >
              {{ wb.running ? "生成中…" : "▶ 生成此镜" }}
            </button>
            <label class="fld">
              <span>地点</span>
              <select :value="shot.sceneId" @change="onShotSceneMove">
                <option v-for="sc in episode?.scenes" :key="sc.id" :value="sc.id">{{ sc.name }}</option>
              </select>
            </label>
            <div class="fld">
              <span>人物</span>
              <div class="cast-chips">
                <template v-if="castSelectedItems.length">
                  <span v-for="c in castSelectedItems" :key="c.id" class="cast-chip">
                    <span class="cast-chip-thumb" v-html="assetThumbHtml(c.id, 'cast')" />
                    <span class="cast-chip-name">{{ c.name }}</span>
                    <button type="button" class="cast-chip-x" title="移除人物" @click.stop="removeCast(c.id)">×</button>
                  </span>
                </template>
                <span v-else class="cast-auto">✨ 自动继承</span>
                <div class="cast-add-wrap">
                  <button type="button" class="cast-add-btn" title="添加人物" @click="castPickerOpen = !castPickerOpen">＋ 添加人物</button>
                  <div v-if="castPickerOpen" class="cast-picker">
                    <div v-if="!castOptions.length" class="cast-picker-empty">无可用人物（先在素材库建人物）</div>
                    <button type="button" v-for="a in castOptions" :key="a.id" class="cast-picker-item" @click="addCast(a.id)">
                      <span class="cast-chip-thumb" v-html="assetThumbHtml(a.id, 'cast')" />
                      <span class="cast-picker-name">{{ a.name }}</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <label class="fld">
              <span>地点</span>
              <select :value="locSelectValue" @change="onShotLoc">
                <option value="">✨ 自动继承</option>
                <optgroup v-if="sceneLocs.length" label="地点素材">
                  <option v-for="a in sceneLocs" :key="a.id" :value="a.id">🎬 {{ a.name }}</option>
                </optgroup>
                <optgroup v-if="globalLocs.length" label="全局素材">
                  <option v-for="a in globalLocs" :key="a.id" :value="a.id">🌐 {{ a.name }}</option>
                </optgroup>
              </select>
            </label>
            <div class="wb-right-sub">生效资产</div>
            <div class="inherit-grid">
              <template v-for="ci in castInherited" :key="`c-${ci.assetId}`">
                <div class="inherit-row">
                  <span class="inherit-kind">人物</span>
                  <span class="inherit-thumb" v-html="assetThumbHtml(ci.assetId, 'cast')" />
                  <span class="inherit-asset">生效：{{ ci.asset.name }}</span>
                  <span v-if="sourceBadgeEx(ci)" class="inherit-badge" :class="sourceBadgeEx(ci)!.cls">
                    {{ sourceBadgeEx(ci)!.label }}
                  </span>
                </div>
              </template>
              <div v-if="locInherited" class="inherit-row">
                <span class="inherit-kind">地点</span>
                <span class="inherit-thumb" v-html="assetThumbHtml(locInherited.assetId, 'location')" />
                <span class="inherit-asset">生效：{{ locInherited.asset.name }}</span>
                <span v-if="sourceBadgeEx(locInherited)" class="inherit-badge" :class="sourceBadgeEx(locInherited)!.cls">
                  {{ sourceBadgeEx(locInherited)!.label }}
                </span>
              </div>
              <template v-for="pi in propInherited" :key="`p-${pi.assetId}`">
                <div class="inherit-row">
                  <span class="inherit-kind">道具</span>
                  <span class="inherit-thumb" v-html="assetThumbHtml(pi.assetId, 'prop')" />
                  <span class="inherit-asset">生效：{{ pi.asset.name }}</span>
                  <span v-if="sourceBadgeEx(pi)" class="inherit-badge" :class="sourceBadgeEx(pi)!.cls">
                    {{ sourceBadgeEx(pi)!.label }}
                  </span>
                </div>
              </template>
              <template v-for="si in styleInherited" :key="`s-${si.assetId}`">
                <div class="inherit-row">
                  <span class="inherit-kind">风格</span>
                  <span class="inherit-thumb" v-html="assetThumbHtml(si.assetId, 'style')" />
                  <span class="inherit-asset">生效：{{ si.asset.name }}</span>
                  <span v-if="sourceBadgeEx(si)" class="inherit-badge" :class="sourceBadgeEx(si)!.cls">
                    {{ sourceBadgeEx(si)!.label }}
                  </span>
                </div>
              </template>
              <div
                v-if="!castInherited.length && !locInherited && !propInherited.length && !styleInherited.length"
                class="inherit-empty"
              >暂无生效资产</div>
            </div>
            <!-- P0-2：地点素材组（候选池）折叠区。生效资产 = 本镜实际引用（castIds ⊔ propIds ⊔
                 @命中）；地点素材组是「地点级素材管理 + @ 标签候选」，绝不整池注入单镜生成。 -->
            <div v-if="sceneMaterialCount" class="wb-right-sub scenemat-head"
              :title="sceneMaterialOpen ? '收起地点素材组' : '展开地点素材组'"
              @click="sceneMaterialOpen = !sceneMaterialOpen">
              <span>📦 地点素材组</span>
              <span class="scenemat-count">{{ sceneMaterialCount }}</span>
              <span class="ps-toggle">{{ sceneMaterialOpen ? "收起 ▴" : "展开 ▾" }}</span>
            </div>
            <div v-if="sceneMaterialOpen && sceneMaterialCount" class="inherit-grid scenemat-grid">
              <template v-for="g in sceneMaterialGroups" :key="g.assetKind">
                <template v-for="a in g.items" :key="`${g.assetKind}-${a.id}`">
                  <div class="inherit-row">
                    <span class="inherit-kind">{{ g.label }}</span>
                    <span class="inherit-thumb" v-html="assetThumbHtml(a.id, g.assetKind)" />
                    <span class="inherit-asset">{{ a.name }}</span>
                    <span class="inherit-badge b-scene-mat">{{ a.imageFile ? "有图" : "无图" }}</span>
                  </div>
                </template>
              </template>
            </div>
            <label class="fld">
              <span>时长（秒）</span>
              <input
                class="wb-duration-input"
                :value="durationInputValue"
                type="number"
                min="1"
                step="0.1"
                @input="onDurationDraftInput"
                @change="onDurationChange"
              />
            </label>
            <!-- #370：剧本原文审核对照区（visual_intent 备注 + 逐字原文/对白；只读，默认展开） -->
            <div
              v-if="shot.description"
              class="script-original-head wb-right-sub"
              :title="scriptOriginalOpen ? '收起剧本原文' : '展开剧本原文'"
              @click="scriptOriginalOpen = !scriptOriginalOpen"
            >
              <span>📜 剧本原文</span>
              <span class="ps-toggle">{{ scriptOriginalOpen ? "收起 ▴" : "展开 ▾" }}</span>
            </div>
            <pre v-if="scriptOriginalOpen && shot.description" class="script-original">{{ shot.description }}</pre>
            <div
              v-if="shot.h3Prompt"
              class="h3-shot-head wb-right-sub"
              :title="h3ShotOpen ? '收起 H3 三层可追溯' : '展开 H3 三层可追溯'"
              @click="h3ShotOpen = !h3ShotOpen"
            >
              <span>🧬 H3 三层</span>
              <span v-if="snapshotProvCount" class="h3-shot-count">{{ snapshotProvCount }}</span>
              <span class="ps-toggle">{{ h3ShotOpen ? "收起 ▴" : "展开 ▾" }}</span>
            </div>
            <div v-if="h3ShotOpen && shot.h3Prompt" class="h3-shot">
              <div v-if="shot.h3Prompt.userOriginalIntent" class="h3-shot-row">
                <span class="h3-shot-k">原文</span>
                <span class="h3-shot-v">{{ shot.h3Prompt.userOriginalIntent }}</span>
              </div>
              <div v-if="snapshotFacts.length || shot.h3Prompt.intent.composition || shot.h3Prompt.intent.cameraDesc" class="h3-shot-row">
                <span class="h3-shot-k">AI 理解</span>
                <span v-if="snapshotFacts.length" class="h3-shot-badge b-user" title="原文逐字事实">原文 {{ snapshotFacts.length }}</span>
                <span v-if="shot.h3Prompt.intent.composition" class="h3-shot-badge b-ai" title="AI 补全（原文没有的画面意图）">AI 构图</span>
                <span v-if="shot.h3Prompt.intent.emotion" class="h3-shot-badge b-ai" title="AI 推断情绪">AI 情绪</span>
                <span v-if="shot.h3Prompt.intent.cameraDesc" class="h3-shot-badge b-rule" title="规则运镜状态机生成">规则运镜</span>
              </div>
              <div v-if="shot.h3Prompt.prompt.integratedDescription" class="h3-shot-row">
                <span class="h3-shot-k">H3 画面</span>
                <span class="h3-shot-v">{{ shot.h3Prompt.prompt.integratedDescription }}</span>
              </div>
              <div v-if="shot.h3Prompt.prompt.soundscape" class="h3-shot-row">
                <span class="h3-shot-k">H3 声音</span>
                <span class="h3-shot-v">{{ shot.h3Prompt.prompt.soundscape }}</span>
              </div>
              <div v-if="shot.h3Prompt.prompt.music" class="h3-shot-row">
                <span class="h3-shot-k">H3 配乐</span>
                <span class="h3-shot-v">{{ shot.h3Prompt.prompt.music }}</span>
              </div>
              <div v-if="shot.h3Prompt.prompt.references?.length" class="h3-shot-refs">
                <img
                  v-for="r in shot.h3Prompt.prompt.references"
                  :key="r.entityKey"
                  :src="comfyInputUrl(r.imageFile)"
                  class="h3-shot-ref"
                  :title="`${r.entityName}（${r.imageFile}）`"
                  alt=""
                  @error="hideEl"
                />
              </div>
            </div>
            <label class="fld">
              <div class="fld-head">
                <span>Prompt（@ 输入实时识别资产）</span>
                <button type="button" class="prompt-zoom-btn" title="放大编辑提示词（方便查看缩略图）" @click="openPromptModal">⛶ 放大</button>
              </div>
              <div class="prompt-box">
                <div class="prompt-layer" v-html="promptHighlightHtml" @error.capture="onChipImgError" />
                <textarea
                  ref="promptInputRef"
                  class="prompt-input"
                  :value="shot.content.visual"
                  style="color: transparent; background: transparent; caret-color: #e8e8ee; -webkit-text-fill-color: transparent;"
                  rows="5"
                  spellcheck="false"
                  @input="onPromptInput"
                  @keydown="onPromptKeydown"
                  @scroll="syncPromptScroll"
                />
                <div v-if="completeOpen && completionTarget === 'card'" class="tag-complete">
                  <div v-for="g in completeGroups" :key="g.label" class="tag-group">
                    <div class="tag-group-label">{{ g.label }}</div>
                    <button
                      v-for="c in g.items"
                      :key="c.name"
                      class="tag-item"
                      :class="{ sel: c.gIndex === completeIndex }"
                      @mousedown.prevent="pickCandidate(c)"
                    >
                      <img v-if="c.imgUrl" class="tag-thumb" :src="c.imgUrl" alt="" @error="hideEl" />
                      <span v-else class="tag-thumb tag-thumb-ph">{{ kindIcon(c.kind) }}</span>
                      <span class="tag-name">{{ c.name }}</span>
                      <span class="tag-src" :class="'ts-' + c.source">{{ srcLabel(c.source) }}</span>
                    </button>
                  </div>
                  <div v-if="!completeCandidates.length" class="tag-empty">无匹配素材</div>
                </div>
              </div>
              <div v-if="mentionHits.length" class="mention-bar">
                <span
                  v-for="(h, i) in mentionHits"
                  :key="i"
                  class="mention-chip"
                  :class="'m-' + kindClass(h.kind)"
                  :title="h.kind === 'cast' ? '点击绑定为人物' : h.kind === 'location' ? '点击绑定为地点' : '识别到资产'"
                  @click="bindMention(h.assetId, h.kind)"
                >
                  {{ h.name }}
                </span>
              </div>
            </label>
            <div class="fld prompt-sections">
              <div class="prompt-sections-head" @click="sectionsOpen = !sectionsOpen">
                <span class="ps-title">📑 更多分区</span>
                <span class="ps-hint">摄影 · 风格 · 声音 · 负面</span>
                <span class="ps-toggle">{{ sectionsOpen ? "收起 ▴" : "展开 ▾" }}</span>
              </div>
              <div v-if="sectionsOpen" class="prompt-sections-body">
                <label class="fld ps-sec">
                  <span>摄影</span>
                  <div class="ps-camera-row">
                    <select
                      :value="shot.cameraTemplate ?? ''"
                      class="ps-camera-select"
                      :disabled="!cameraTemplates.length"
                      @change="onCameraTemplateSelect(shot, $event)"
                    >
                      <option value="">{{ cameraTemplatesLoaded && !cameraTemplates.length ? "运镜模板不可用" : "🎥 运镜模板…" }}</option>
                      <option v-for="t in cameraTemplates" :key="t.id" :value="t.id">{{ t.name }}（{{ t.intent }}）</option>
                    </select>
                    <span v-if="shot.cameraTemplate" class="ps-camera-badge" title="模板来源，手编后自动清除">模板</span>
                  </div>
                  <textarea :value="shot.content.cameraText ?? ''" rows="2" placeholder="景别/运镜/速度/景深，如：近景，缓慢推近" @input="onCameraTextInput(shot, $event)" />
                </label>
                <label class="fld ps-sec">
                  <span>风格</span>
                  <textarea :value="shot.content.style ?? ''" rows="2" placeholder="光影/色调/美术风格，如：赛博朋克霓虹夜" @input="onShotSection('style', $event)" />
                </label>
                <label class="fld ps-sec">
                  <span>声音</span>
                  <textarea :value="shot.content.soundText ?? ''" rows="2" placeholder="环境音/音乐，如：雨声，低音电子" @input="onShotSection('soundText', $event)" />
                </label>
                <label class="fld ps-sec">
                  <span>负面 Prompt</span>
                  <textarea :value="shot.content.negativePrompt ?? ''" rows="2" @input="onShotNegative" />
                </label>
                <div class="ps-preview">
                  <div class="ps-preview-label">生成用 Prompt</div>
                  <div class="ps-preview-text">{{ mergedPromptText }}</div>
                </div>
              </div>
            </div>
            <label class="fld">
              <span>衔接</span>
              <select :value="shot.generation?.continuityMode ?? 'auto'" @change="onShotContinuity">
                <option value="auto">自动 · Ref2VA</option>
                <option value="none">独立镜头</option>
                <option value="ref2va">Ref2VA 续接</option>
                <option value="fl2va">FL2VA 续接</option>
              </select>
            </label>
            <label class="fld">
              <span>智能尾帧</span>
              <input type="checkbox" :checked="shot.generation?.smartTail ?? false" @change="onShotSmartTail" />
            </label>
            <label class="fld">
              <span>状态变化</span>
              <input :value="shot.generation?.stateChange ?? ''" placeholder="如：阿岚开始奔跑" @change="onShotStateChange" />
            </label>
            <!-- Phase 2-D（#576）：镜头对白行级微调（音色/情绪/语速；写回 plan 该镜该行，
                 空值恢复全局 Voice Cast / 规则推断；纯前端刷新即生效） -->
            <div
              class="wb-right-sub dialogue-head"
              :class="{ 'dialogue-has': shotDialogueLines.length }"
              :title="shotDialogueOpen ? '收起对白微调' : '展开对白微调'"
              @click="shotDialogueOpen = !shotDialogueOpen"
            >
              <span>🎙 对白（{{ shotDialogueLines.length }}）</span>
              <span v-if="!shotDialogueLines.length" class="dialogue-none">无 TTS 行</span>
              <span class="ps-toggle">{{ shotDialogueOpen ? "收起 ▴" : "展开 ▾" }}</span>
            </div>
            <div v-if="shotDialogueOpen" class="dialogue-list">
              <template v-if="shotDialogueLines.length">
                <div v-for="line in shotDialogueLines" :key="line.index" class="dialogue-row">
                  <div class="dialogue-row-head">
                    <span class="dialogue-idx">{{ line.index }}</span>
                    <span class="dialogue-speaker">{{ line.speaker || "（无说话人）" }}</span>
                    <span class="dialogue-type">{{ dialogueTypeLabel(line.type) }}</span>
                    <span class="dialogue-resolved" :title="'当前生效音色（行级显式 > Voice Cast 全局 > 规则推断）'">{{ line.voiceId }}</span>
                  </div>
                  <div class="dialogue-text" :title="line.text">{{ line.text }}</div>
                  <div class="dialogue-controls">
                    <label class="dialogue-ctl dialogue-ctl-voice">
                      <span>音色</span>
                      <select
                        class="vcast-select"
                        :value="shotDialogueExplicit(line.index)"
                        @change="onDialogueVoice(line.index, $event)"
                      >
                        <option
                          v-for="o in withCustomVoiceOption(shotDialogueVoiceOptions, shotDialogueExplicit(line.index))"
                          :key="o.value"
                          :value="o.value"
                        >{{ o.label }}</option>
                      </select>
                    </label>
                    <label class="dialogue-ctl">
                      <span>情绪</span>
                      <input class="dialogue-input" list="dlg-emotions" :value="shotDialogueEmotion(line.index)" placeholder="如：愤怒" @change="onDialogueEmotion(line.index, $event)" />
                    </label>
                    <label class="dialogue-ctl">
                      <span>语速</span>
                      <input class="dialogue-input" list="dlg-deliveries" :value="shotDialogueDelivery(line.index)" placeholder="快/慢/电子…" @change="onDialogueDelivery(line.index, $event)" />
                    </label>
                  </div>
                </div>
                <datalist id="dlg-emotions">
                  <option v-for="e in DIALOGUE_EMOTIONS" :key="e" :value="e" />
                </datalist>
                <datalist id="dlg-deliveries">
                  <option v-for="d in DIALOGUE_DELIVERIES" :key="d" :value="d" />
                </datalist>
                <div class="dialogue-hint">只影响此镜此行；「⚙ 自动」音色 = 走全局 Voice Cast / 规则推断。空情绪/语速 = 引擎默认。</div>
              </template>
              <div v-else class="dialogue-empty">
                本镜无 TTS 对白行（「📖 导入小说」建立制作计划后，台词自动出现在这里）。<br />
                旁白 / 内心独白 / 系统音也在本区微调。
              </div>
            </div>
            <label class="fld">
              <span>参考素材</span>
              <div class="ref-group">
                <div class="ref-group-title">🖼 参考图</div>
                <div class="ref-slot">
                  <div v-for="(r, i) in shot.refs?.refImages ?? []" :key="`img${i}`" class="ref-chip">
                    <img
                      v-if="r.imageFile"
                      class="ref-thumb"
                      :src="comfyInputUrl(r.imageFile)"
                      alt=""
                      @error="onRefImgError"
                    />
                    <span class="ref-name" :title="r.imageFile">{{ r.fileName ?? r.imageFile }}</span>
                    <button class="icon-btn" title="删除参考图" @click.stop="onShotRemoveRef(i)">🗑</button>
                  </div>
                  <span v-if="!(shot.refs?.refImages?.length)" class="ref-empty">未添加</span>
                </div>
                <ImageUploadBox
                  :api="wb.runService.api"
                  :model-value="''"
                  media-type="image"
                  subfolder="minimax_studio/assets"
                  placeholder="＋ 添加参考图"
                  label="镜头参考图"
                  @update:model-value="onShotAddRef"
                />
              </div>

              <div class="ref-group">
                <div class="ref-group-title">🎬 参考视频</div>
                <div class="ref-slot">
                  <div v-for="(r, i) in shot.refs?.refVideos ?? []" :key="`vid${i}`" class="ref-chip">
                    <video
                      v-if="r.videoFile"
                      class="ref-video-thumb"
                      :src="comfyInputUrl(r.videoFile)"
                      preload="metadata"
                      muted
                      @error="onRefMediaError"
                    />
                    <span class="ref-name" :title="r.videoFile">{{ r.fileName ?? r.videoFile }}</span>
                    <button class="icon-btn" title="删除参考视频" @click.stop="onShotRemoveRefVideo(i)">🗑</button>
                  </div>
                  <span v-if="!(shot.refs?.refVideos?.length)" class="ref-empty">未添加</span>
                </div>
                <ImageUploadBox
                  :api="wb.runService.api"
                  :model-value="''"
                  media-type="video"
                  subfolder="minimax_studio/assets"
                  placeholder="＋ 添加参考视频"
                  label="镜头参考视频"
                  @update:model-value="onShotAddRefVideo"
                />
              </div>

              <div class="ref-group">
                <div class="ref-group-title">🎵 参考音频</div>
                <div class="ref-slot">
                  <div v-for="(r, i) in shot.refs?.refAudios ?? []" :key="`aud${i}`" class="ref-chip">
                    <span v-if="r.audioFile" class="ref-audio-icon" :title="r.audioFile">🎵</span>
                    <span class="ref-name" :title="r.audioFile">{{ r.fileName ?? r.audioFile }}</span>
                    <button class="icon-btn" title="删除参考音频" @click.stop="onShotRemoveRefAudio(i)">🗑</button>
                  </div>
                  <span v-if="!(shot.refs?.refAudios?.length)" class="ref-empty">未添加</span>
                </div>
                <ImageUploadBox
                  :api="wb.runService.api"
                  :model-value="''"
                  media-type="audio"
                  subfolder="minimax_studio/assets"
                  placeholder="＋ 添加参考音频"
                  label="镜头参考音频"
                  @update:model-value="onShotAddRefAudio"
                />
              </div>
            </label>
          </template>

          <!-- 空态 -->
          <div v-else class="wb-right-empty">
            <div>🎞 分镜工作台</div>
            <div>点左侧地点进入「地点设置」<br />点镜头卡片进入「镜头设置」</div>
          </div>
        </aside>
      </div>

      <!-- 底：任务中心 -->
      <footer class="wb-foot">
        <TaskCenter :open-signal="taskCenterSignal" :history="etaHistoryForTaskCenter" @regen="onTaskRegen" @cancel="onTaskCancel" />
      </footer>
    </template>

    <!-- Prompt 放大编辑弹窗（Teleport 到 body，屏幕居中，方便查看缩略图） -->
    <Teleport to="body">
      <div v-if="promptModalOpen" class="prompt-modal-mask" @click.self="closePromptModal">
        <div class="prompt-modal" role="dialog" aria-label="提示词放大编辑">
          <div class="prompt-modal-head">
            <span class="prompt-modal-title">📝 提示词放大编辑{{ shot ? ` — 镜 ${shot.order + 1}` : "" }}</span>
            <button type="button" class="prompt-modal-close" title="关闭（Esc）" @click="closePromptModal">✕ 关闭</button>
          </div>
          <div class="prompt-box prompt-modal-box">
            <div class="prompt-layer" v-html="promptHighlightHtml" @error.capture="onChipImgError" />
            <textarea
              ref="promptModalInputRef"
              class="prompt-input"
              :value="shot?.content.visual ?? ''"
              style="color: transparent; background: transparent; caret-color: #e8e8ee; -webkit-text-fill-color: transparent;"
              rows="10"
              spellcheck="false"
              @input="onPromptInput"
              @keydown="onPromptKeydown"
              @scroll="syncPromptScroll"
            />
            <div v-if="completeOpen && completionTarget === 'modal'" class="tag-complete">
              <div v-for="g in completeGroups" :key="g.label" class="tag-group">
                <div class="tag-group-label">{{ g.label }}</div>
                <button
                  v-for="c in g.items"
                  :key="c.name"
                  class="tag-item"
                  :class="{ sel: c.gIndex === completeIndex }"
                  @mousedown.prevent="pickCandidate(c)"
                >
                  <img v-if="c.imgUrl" class="tag-thumb" :src="c.imgUrl" alt="" @error="hideEl" />
                  <span v-else class="tag-thumb tag-thumb-ph">{{ kindIcon(c.kind) }}</span>
                  <span class="tag-name">{{ c.name }}</span>
                  <span class="tag-src" :class="'ts-' + c.source">{{ srcLabel(c.source) }}</span>
                </button>
              </div>
              <div v-if="!completeCandidates.length" class="tag-empty">无匹配素材</div>
            </div>
          </div>
          <div v-if="mentionHits.length" class="mention-bar">
            <span
              v-for="(h, i) in mentionHits"
              :key="i"
              class="mention-chip"
              :class="'m-' + kindClass(h.kind)"
              :title="h.kind === 'cast' ? '点击绑定为人物' : h.kind === 'location' ? '点击绑定为地点' : '识别到资产'"
              @click="bindMention(h.assetId, h.kind)"
            >
              {{ h.name }}
            </span>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- V1.4-P0-3 「← 项目」退出保护确认框（仅 dirty 时弹出，三选一） -->
    <Teleport to="body">
      <div v-if="leaveConfirmOpen" class="leave-modal-mask" @click.self="onLeaveCancel">
        <div class="leave-modal" role="dialog" aria-label="有未保存的修改">
          <div class="leave-modal-title">⚠ 有未保存的修改</div>
          <div class="leave-modal-body">当前项目有尚未保存的修改，离开后这些修改将丢失。</div>
          <div class="leave-modal-actions">
            <button type="button" class="btn leave-save" :disabled="leaveBusy" @click="onLeaveSave">
              {{ leaveBusy ? "保存中…" : "保存并离开" }}
            </button>
            <button type="button" class="btn leave-discard" @click="onLeaveDiscard">不保存离开</button>
            <button type="button" class="btn leave-cancel" @click="onLeaveCancel">取消</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- V1.7 Commit 3：剧本导入弹窗（粘贴文本 / .md/.txt 文件 → 规则拆 + Qwen 补全 → 审核预览 → 应用为项目） -->
    <Teleport to="body">
      <div v-if="scriptModalOpen" class="script-modal-mask" @click.self="closeScriptImport">
        <div class="script-modal" role="dialog" aria-label="导入（剧本 / 小说章节）">
          <div class="script-modal-head">
            <span class="script-modal-title">📥 导入 → 制作计划</span>
            <div class="script-modal-tabs">
              <button
                type="button"
                class="script-modal-tab"
                :class="{ active: importTab === 'script' }"
                @click="importTab = 'script'"
              >📜 剧本</button>
              <button
                type="button"
                class="script-modal-tab"
                :class="{ active: importTab === 'story' }"
                @click="importTab = 'story'"
              >📖 小说章节</button>
            </div>
            <button type="button" class="script-modal-close" title="关闭" @click="closeScriptImport">✕</button>
          </div>

          <template v-if="importTab === 'script'">
          <!-- 输入区 -->
          <div class="script-input-zone">
            <textarea
              v-model="scriptText"
              class="script-input"
              rows="8"
              placeholder="粘贴剧本（Markdown：\n# 标题\n## 第X场：地点\n地点：…\n时间：…\n天气：…\n角色（身份）动作/对白…）"
            ></textarea>
            <div class="script-input-row">
              <input
                ref="scriptFileInput"
                type="file"
                accept=".md,.txt,text/markdown,text/plain"
                class="script-file-input"
                @change="onScriptFile"
              />
              <button class="btn ghost" @click="scriptFileInput?.click()">选择 .md/.txt 文件</button>
              <span v-if="scriptFileName" class="script-file-name">📄 {{ scriptFileName }}</span>
            </div>
            <div class="script-input-row">
              <button class="btn script-go" :disabled="scriptBusy" @click="runScriptImport">
                {{ scriptBusy ? "解析中…" : "解析剧本（规则拆镜 + Qwen 补全）" }}
              </button>
              <span class="script-hint">一步完成；Qwen 不可用时自动降级为纯规则结果，不阻塞。</span>
            </div>
          </div>

          <div v-if="scriptError" class="script-err">⚠ {{ scriptError }}</div>

          <!-- 审核预览 -->
          <div v-if="scriptResult && scriptSummaryText" class="script-preview">
            <div class="script-summary">
              <span>{{ scriptSummaryText.scenes }} 地点</span>
              <span class="sum-sep">·</span>
              <span>{{ scriptSummaryText.shots }} 镜头</span>
              <span class="sum-sep">·</span>
              <span>{{ scriptSummaryText.characters }} 角色</span>
              <span class="sum-sep">·</span>
              <span>约 {{ scriptSummaryText.durationSec }} 秒</span>
              <span v-if="scriptResult.rule_only" class="script-rule-only">（纯规则结果，无 Qwen 补全）</span>
            </div>

            <!-- V1.7 Phase 2 + Phase 1.1：资产自动匹配（规则扫描共享资产库；pending 人工确认；
                 #357 实体漏斗；#367 分组展示 + ⚠ 建议参考 [接受][重新选择]） -->
            <div v-if="scriptMatch" class="script-assets">
              <div class="script-assets-head">
                <span class="script-assets-title">🎨 资产自动匹配</span>
                <span class="script-assets-stat">{{ scriptFunnelText }}</span>
              </div>
              <div v-if="scriptMatchBusy" class="script-assets-tip">扫描共享资产库（minimax_studio/assets）…</div>
              <div v-if="scriptMatchError" class="script-warning">⚠ {{ scriptMatchError }}</div>

              <!-- Phase 1.1：按 角色 / 地点 / 道具 分组展示 -->
              <div v-if="scriptMatchGroups.length" class="script-assets-groups">
                <div v-for="g in scriptMatchGroups" :key="g.label" class="script-assets-group">
                  <div class="script-assets-group-title">
                    <span>{{ g.label }}</span>
                    <span class="script-assets-group-count">{{ g.items.length }}</span>
                  </div>
                  <div v-for="m in g.items" :key="scriptMatchKey(m)" class="script-asset-row">
                    <span class="script-asset-name" :title="m.name">{{ m.name }}</span>
                    <span class="script-asset-type" :class="`etype-${m.entity_type}`">
                      {{ scriptEntityTypeLabel(m.entity_type) }}
                    </span>
                    <span class="script-asset-conf">{{ Math.round(m.entity_confidence * 100) }}%</span>
                    <span v-if="scriptEntityGateNote(m)" class="script-asset-gate" :title="scriptEntityGateNote(m)">
                      ⚠ {{ scriptEntityGateNote(m) }}
                    </span>
                    <span class="script-asset-space"></span>

                    <!-- auto：自动匹配（Phase 2-1：persisted → 「已确认」复用上次落盘） -->
                    <template v-if="m.status === 'auto' && m.image_file">
                      <img :src="comfyInputUrl(m.image_file)" class="script-asset-thumb" alt="" />
                      <span v-if="scriptBindingAccepted(m)" class="script-asset-badge persisted">
                        ✓ 已确认<span v-if="scriptBindings[m.entity_key]?.asset_name" class="script-asset-badge-sub">
                          {{ scriptBindings[m.entity_key]?.asset_name }}</span
                        >
                      </span>
                      <span v-else class="script-asset-badge auto">✓ 匹配</span>
                    </template>

                    <!-- Phase 1.1：⚠ 建议参考（location_hierarchy / generic_reference）→ [接受][重新选择] -->
                    <template
                      v-else-if="m.status === 'pending' && m.suggest && m.asset_name && m.image_file"
                    >
                      <template v-if="scriptSuggestAccepted(m)">
                        <img :src="comfyInputUrl(m.image_file)" class="script-asset-thumb" alt="" />
                        <span class="script-asset-badge suggest">建议已接受 ✓</span>
                      </template>
                      <template v-else>
                        <span class="script-asset-suggest" :title="`${m.match_kind}：${m.name} 是 ${m.asset_name} 的子集`">
                          ⚠ 建议参考 {{ m.asset_name }} 相似度 {{ m.confidence }}
                        </span>
                        <button type="button" class="script-asset-btn accept" @click="onAcceptSuggest(m)">接受</button>
                        <button type="button" class="script-asset-btn rechoose" @click="onRechooseSuggest(m)">重新选择</button>
                      </template>
                    </template>

                    <!-- 普通 pending：候选下拉人工确认 -->
                    <template v-else-if="m.status === 'pending'">
                      <select
                        class="script-asset-select"
                        :value="scriptConfirm[scriptMatchKey(m)] ?? ''"
                        @change="onConfirmAsset(m, $event)"
                      >
                        <option value="" disabled>选择资产…</option>
                        <option v-for="(c, i) in m.candidates" :key="c.name" :value="i">
                          {{ c.name }}（置信 {{ c.score }}）
                        </option>
                      </select>
                      <img
                        v-if="scriptChosenCandidate(m)"
                        :src="comfyInputUrl(scriptChosenCandidate(m)!.image_file)"
                        class="script-asset-thumb"
                        alt=""
                      />
                    </template>

                    <span v-else class="script-asset-none">✗ 未找到 · 进工作台手动补图</span>
                  </div>
                </div>
              </div>
              <div v-else-if="!scriptMatchBusy" class="script-assets-tip">未从剧本提取到可匹配实体。</div>

              <!-- Phase 1.1：视觉元素（Task B，环境/氛围/光线/天气等；只进 Prompt，无需资产匹配） -->
              <div v-if="scriptMatch.visual_elements?.length" class="script-assets-visual">
                <span class="script-assets-visual-title">
                  🌫 视觉元素 · 进 Prompt
                  <em>（无需资产匹配 {{ scriptMatch.visual_elements.length }}）</em>
                </span>
                <div class="script-assets-visual-chips">
                  <span v-for="v in scriptMatch.visual_elements" :key="v.name" class="script-assets-visual-chip">
                    {{ v.name }}<em>{{ scriptEntityTypeLabel(v.type) }}</em>
                  </span>
                </div>
              </div>
            </div>

            <!-- V1.7 Phase 3（P0-C）：AI Draft 五区草稿（纯规则 + Prompt Template Registry，零显存；只读审核预览，应用后进工作台可编辑） -->
            <div v-if="scriptDraft" class="script-draft">
              <div class="script-draft-head">
                <span class="script-draft-title">🤖 AI Draft 制作计划草稿</span>
                <span class="script-draft-stat">
                  {{ scriptDraft.drafts.length }} 镜 · 模板 {{ scriptDraft.template_version }}
                </span>
              </div>
              <div v-if="scriptDraftBusy" class="script-draft-tip">生成每镜五区 Prompt 草稿（纯规则，零显存）…</div>
              <div v-if="scriptDraftError" class="script-warning">⚠ {{ scriptDraftError }}</div>
              <div v-if="scriptDraft.drafts.length" class="script-draft-list">
                <div v-for="d in scriptDraft.drafts" :key="`${d.scene_id}:${d.shot_id}`" class="script-draft-row">
                  <div class="script-draft-shot">
                    <span class="script-draft-id">{{ d.shot_id }}</span>
                    <span class="script-draft-mode" :class="`mode-${d.generation_mode}`">
                      {{ draftModeLabel(d.generation_mode) }}
                    </span>
                  </div>
                  <div class="script-draft-secs">
                    <div v-for="s in SCRIPT_DRAFT_SECTIONS" :key="s.key" class="script-draft-sec">
                      <span class="script-draft-sec-label">{{ s.label }}</span>
                      <span class="script-draft-sec-text">{{ d.draft[s.key] }}</span>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else-if="!scriptDraftBusy" class="script-draft-tip">未生成草稿（解析失败时跳过）。</div>
            </div>

            <!-- P0-①（#400）：H3 Prompt 三层可追溯（剧本原文 → AI 理解 → H3 Prompt；只读审计预览） -->
            <div v-if="scriptH3" class="script-h3">
              <div class="script-h3-head" @click="h3Open = !h3Open">
                <span class="script-h3-title">🧬 H3 Prompt（三层可追溯）</span>
                <span class="script-h3-stat">
                  {{ Object.keys(scriptH3.prompts).length }} 镜 · 地点实例 Schema {{ scriptH3.schema }}
                  <em>（只读追溯：原文 → AI 理解 → H3）</em>
                </span>
                <span class="script-h3-toggle">{{ h3Open ? "收起 ▴" : "展开 ▾" }}</span>
              </div>
              <div v-if="scriptH3Busy" class="script-h3-tip">生成每镜 H3 Prompt（纯规则，零显存）…</div>
              <div v-if="scriptH3Error" class="script-warning">⚠ {{ scriptH3Error }}</div>
              <div v-if="h3Open" class="script-h3-list">
                <div v-for="(item, key) in scriptH3.prompts" :key="key" class="script-h3-item">
                  <div class="script-h3-shot">
                    <span class="script-h3-id">{{ key }}</span>
                    <span v-if="item.duration_sec" class="script-h3-dur">{{ item.duration_sec }}s</span>
                  </div>

                  <!-- 第一层：剧本原文（逐字未覆盖） -->
                  <div class="script-h3-layer">
                    <span class="script-h3-layer-label layer-original">原文</span>
                    <span class="script-h3-layer-text">{{ scriptH3Intents[key]?.user_original_intent || "（无原文）" }}</span>
                  </div>

                  <!-- 第二层：AI 理解（规则抽取原文事实 + Qwen 推断补全，字段级来源标记） -->
                  <div v-if="scriptH3Intents[key]" class="script-h3-layer">
                    <span class="script-h3-layer-label layer-intent">AI 理解</span>
                    <div class="script-h3-intent">
                      <span
                        v-for="(f, i) in scriptH3Intents[key].retained_facts"
                        :key="i"
                        class="script-h3-fact"
                        title="来源：原文逐字事实"
                      >{{ f }}</span>
                      <span v-if="scriptH3Intents[key].composition" class="script-h3-fact ai"
                        title="来源：AI 补全（原文没有的画面意图）"
                      >AI 构图：{{ scriptH3Intents[key].composition }}</span>
                      <span v-if="scriptH3Intents[key].emotion" class="script-h3-fact ai"
                        title="来源：AI 推断情绪（原文没有时）"
                      >AI 情绪：{{ scriptH3Intents[key].emotion }}</span>
                      <span v-if="scriptH3Intents[key].continuity?.camera_desc" class="script-h3-fact rule"
                        title="来源：规则运镜状态机（渐近正反打/景别节奏）"
                      >{{ scriptH3Intents[key].continuity.camera_desc }}</span>
                    </div>
                  </div>

                  <!-- 第三层：H3 Prompt 三段 + 结构化 references + 段落级来源标记 -->
                  <div class="script-h3-layer">
                    <span class="script-h3-layer-label layer-h3">H3</span>
                    <div class="script-h3-prompt">
                      <div class="script-h3-sec">
                        <span class="script-h3-sec-label">{{ h3ProvLabel('integrated_multimodal_description') }}</span>
                        <span class="script-h3-sec-text">{{ item.integrated_multimodal_description }}</span>
                        <span v-if="item.provenance['integrated_multimodal_description']" class="script-h3-prov">
                          <em v-if="item.provenance['integrated_multimodal_description'].user_facts?.length"
                            class="prov prov-user" title="原文逐字事实">原文 {{ item.provenance['integrated_multimodal_description'].user_facts.length }}</em>
                          <em v-if="item.provenance['integrated_multimodal_description'].ai_supplement?.length"
                            class="prov prov-ai" title="AI 补全">AI {{ item.provenance['integrated_multimodal_description'].ai_supplement.length }}</em>
                          <em v-if="item.provenance['integrated_multimodal_description'].rule_generated?.length"
                            class="prov prov-rule" title="规则生成">规则 {{ item.provenance['integrated_multimodal_description'].rule_generated.length }}</em>
                        </span>
                      </div>
                      <div class="script-h3-sec">
                        <span class="script-h3-sec-label">{{ h3ProvLabel('overall_soundscape') }}</span>
                        <span class="script-h3-sec-text">{{ item.overall_soundscape }}</span>
                        <span v-if="item.provenance['overall_soundscape']" class="script-h3-prov">
                          <em v-if="item.provenance['overall_soundscape'].user_facts?.length"
                            class="prov prov-user" title="原文逐字事实">原文 {{ item.provenance['overall_soundscape'].user_facts.length }}</em>
                          <em v-if="item.provenance['overall_soundscape'].ai_supplement?.length"
                            class="prov prov-ai" title="AI 补全">AI {{ item.provenance['overall_soundscape'].ai_supplement.length }}</em>
                          <em v-if="item.provenance['overall_soundscape'].rule_generated?.length"
                            class="prov prov-rule" title="规则生成">规则 {{ item.provenance['overall_soundscape'].rule_generated.length }}</em>
                        </span>
                      </div>
                      <div class="script-h3-sec">
                        <span class="script-h3-sec-label">{{ h3ProvLabel('non_diegetic_music') }}</span>
                        <span class="script-h3-sec-text">{{ item.non_diegetic_music }}</span>
                        <span v-if="item.provenance['non_diegetic_music']" class="script-h3-prov">
                          <em v-if="item.provenance['non_diegetic_music'].user_facts?.length"
                            class="prov prov-user" title="原文逐字事实">原文 {{ item.provenance['non_diegetic_music'].user_facts.length }}</em>
                          <em v-if="item.provenance['non_diegetic_music'].ai_supplement?.length"
                            class="prov prov-ai" title="AI 补全">AI {{ item.provenance['non_diegetic_music'].ai_supplement.length }}</em>
                          <em v-if="item.provenance['non_diegetic_music'].rule_generated?.length"
                            class="prov prov-rule" title="规则生成">规则 {{ item.provenance['non_diegetic_music'].rule_generated.length }}</em>
                        </span>
                      </div>
                    </div>
                  </div>

                  <!-- 结构化 references（真实绑定，不依赖文本顺序） -->
                  <div v-if="item.references?.length" class="script-h3-refs">
                    <span class="script-h3-refs-label">📎 参考图绑定</span>
                    <span v-for="r in item.references" :key="r.entity_key" class="script-h3-ref">
                      <img v-if="r.image_file" :src="comfyInputUrl(r.image_file)" class="script-h3-ref-thumb" alt="" @error="hideEl" />
                      <span class="script-h3-ref-name">{{ r.entity_name }}</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="scriptResult.warnings.length" class="script-warnings">
              <div v-for="(w, i) in scriptResult.warnings" :key="i" class="script-warning">⚠ {{ w }}</div>
            </div>
            <div class="script-plan">
              <div v-for="sc in scriptResult.plan.scenes" :key="sc.scene_id" class="script-scene">
                <div class="script-scene-title">
                  {{ sc.title }}<em v-if="sc.location_name"> · {{ sc.location_name }}</em>
                  <span class="script-scene-sub">{{ sc.time }} · {{ sc.weather }}</span>
                </div>
                <div v-for="sh in sc.shots" :key="sh.shot_id" class="script-shot">
                  <span class="script-shot-id">{{ sh.shot_id }}</span>
                  <span class="script-shot-text">{{ sh.source_text }}</span>
                  <span v-if="sh.characters.length" class="script-shot-cast">
                    {{ sh.characters.map((c) => c.name).join("、") }}
                  </span>
                  <span v-if="sh.visual_intent" class="script-shot-viz">{{ sh.visual_intent }}</span>
                </div>
              </div>
            </div>
          </div>
          </template>

          <!-- P1-B-5（#534）：📖 小说章节 审核预览（Beats 分组卡片 + 地点列表 + 播放顺序 + 警告） -->
          <template v-else>
            <!-- P2-P5（#542）：目标项目选择（🆕 新建项目 / 已有项目追加新集 → Bible 跨章上下文）。
                 默认「新建项目」= P1-B-5 行为不变；选已有项目后，后端随导入对该项目更新 Bible。 -->
            <div class="script-input-zone story-target-zone">
              <div class="story-target-label">📖 导入到</div>
              <div class="story-target-options">
                <label class="story-target-opt" :class="{ active: storyTarget === 'new' }">
                  <input type="radio" value="new" v-model="storyTarget" />
                  🆕 新建项目
                </label>
                <label
                  v-for="p in projects"
                  :key="p.id"
                  class="story-target-opt"
                  :class="{ active: storyTarget === p.id }"
                  :title="`已有 ${p.episodes} 集 · 最近更新 ${p.updatedAt.slice(0, 10)}`"
                >
                  <input type="radio" :value="p.id" v-model="storyTarget" />
                  {{ p.name }}（{{ p.episodes }} 集）
                </label>
              </div>
              <!-- 已有项目：目标集号（一章一集）+ 本次将注入的跨章上下文预览 -->
              <div v-if="storyTargetProject" class="story-target-detail">
                <span class="story-target-hint">追加为第</span>
                <input v-model.number="storyEpisodeNumber" type="number" min="1" class="story-ep-input" />
                <span class="story-target-hint">集</span>
                <span v-if="storyBiblePreviewLoading" class="story-target-hint">· Bible 载入中…</span>
              </div>
              <div
                v-if="storyTargetProject && !storyBiblePreviewLoading"
                class="story-bible-context"
              >
                <div class="script-assets-head">
                  <span class="script-assets-title">📖 将注入的跨章上下文（Bible 已确认 {{ storyContextPreview.length }} 条）</span>
                </div>
                <div v-if="storyContextPreview.length" class="story-bible-context-list">
                  <div v-for="c in storyContextPreview" :key="c.name" class="story-bible-context-row">
                    <span class="story-bible-context-name">✓ {{ c.name }}</span>
                    <span v-if="c.current_status" class="story-bible-context-status">{{ c.current_status }}</span>
                  </div>
                </div>
                <div v-else class="story-bible-context-empty">
                  该项目暂无已确认条目 · 首次导入将自动建立 Bible（候选待面板确认）
                </div>
              </div>
            </div>
            <!-- 输入区：小说章节正文（自然语言，零标记） -->
            <div class="script-input-zone">
              <textarea
                v-model="storyText"
                class="script-input"
                rows="8"
                placeholder="粘贴小说章节正文（纯自然语言，无需「第一场/镜头一」标记）：\n例：沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。…"
              ></textarea>
              <div class="script-input-row">
                <input
                  ref="storyFileInput"
                  type="file"
                  accept=".md,.txt,text/markdown,text/plain"
                  class="script-file-input"
                  @change="onStoryFile"
                />
                <button class="btn ghost" @click="storyFileInput?.click()">选择 .md/.txt 文件</button>
                <span v-if="storyFileName" class="script-file-name">📄 {{ storyFileName }}</span>
              </div>
              <div class="script-input-row">
                <button class="btn script-go" :disabled="storyBusy" @click="runStoryImport">
                  {{ storyBusy ? "解析中…" : "解析章节（Qwen 整章理解 → Beats → 拆 Shot）" }}
                </button>
                <span class="script-hint">零标记自然语言；Qwen 不可用时自动降级为纯规则结果（Beats 标 source=rule），不阻塞。</span>
              </div>
            </div>

            <div v-if="storyError" class="script-err">⚠ {{ storyError }}</div>

            <div v-if="storyResult && storySummaryText" class="script-preview">
              <div class="script-summary">
                <span>{{ storySummaryText.scenes }} 地点</span>
                <span class="sum-sep">·</span>
                <span>{{ storySummaryText.shots }} 镜头</span>
                <span class="sum-sep">·</span>
                <span>{{ storySummaryText.characters }} 角色</span>
                <span class="sum-sep">·</span>
                <span>约 {{ storySummaryText.durationSec }} 秒</span>
                <span class="sum-sep">·</span>
                <span>{{ (storyResult.plan.beats ?? []).length }} 节拍</span>
                <span v-if="storyResult.rule_only" class="script-rule-only">（纯规则结果，无 Qwen 整章理解）</span>
              </div>

              <!-- P2-P5（#542）：本次导入对 Bible 的变更列表（选中已有项目时后端返回 bible_updates） -->
              <div v-if="storyBibleUpdates.length" class="story-bible-updates">
                <div class="script-assets-head">
                  <span class="script-assets-title">📖 本次 Bible 变更（{{ storyBibleUpdates.length }}）</span>
                  <span class="script-assets-stat">已写入该项目跨章世界状态库</span>
                </div>
                <div v-for="(u, i) in storyBibleUpdates" :key="i" class="story-bible-update-row">
                  <span class="story-bible-update-kind" :class="`k-${u.kind}`">{{ bibleUpdateLabel(u.kind) }}</span>
                  <span class="story-bible-update-name">{{ u.name }}</span>
                  <span class="story-bible-update-msg">{{ u.message }}</span>
                </div>
              </div>

              <!-- 📍 地点列表 -->
              <div v-if="storyLocations.length" class="story-locations">
                <div class="script-assets-head">
                  <span class="script-assets-title">📍 地点</span>
                  <span class="script-assets-stat">{{ storyLocations.length }} 个 · 进工作台地点库</span>
                </div>
                <div class="story-location-list">
                  <div v-for="loc in storyLocations" :key="loc.scene_id" class="story-location-row">
                    <span class="story-location-id">{{ loc.scene_id }}</span>
                    <span class="story-location-name">{{ loc.name }}</span>
                    <span v-if="loc.time || loc.weather" class="script-scene-sub">{{ loc.time }} · {{ loc.weather }}</span>
                    <span class="story-location-shots">{{ loc.shots }} 镜</span>
                  </div>
                </div>
              </div>

              <!-- 🎞 播放顺序摘要（timeline = 唯一播放顺序权威源） -->
              <div class="story-timeline">
                <div class="script-assets-head">
                  <span class="script-assets-title">🎞 播放顺序</span>
                  <span class="script-assets-stat">{{ storyTimelineSummary.length }} 镜 · 进时间线主视图</span>
                </div>
                <div class="story-timeline-list">
                  <div v-for="t in storyTimelineSummary" :key="t.ref" class="story-timeline-row">
                    <span class="story-timeline-order">{{ String(t.order).padStart(2, "0") }}</span>
                    <span class="story-timeline-label">{{ t.label }}</span>
                  </div>
                </div>
              </div>

              <!-- 🎬 Beats 分组卡片 -->
              <div class="story-beats">
                <div class="script-assets-head">
                  <span class="script-assets-title">🎬 剧情节拍（Story Beats）</span>
                  <span class="script-assets-stat">{{ (storyResult.plan.beats ?? []).length }} 个 · 拍板动态 8–16</span>
                </div>
                <div v-for="b in (storyResult.plan.beats ?? [])" :key="b.beat_id" class="story-beat-card">
                  <div class="story-beat-head">
                    <span class="story-beat-id">{{ b.beat_id }}</span>
                    <span class="story-beat-title">{{ b.title || b.summary || "（无标题）" }}</span>
                    <span class="story-beat-fn" :class="`fn-${b.dramatic_function}`">{{ dramaticFunctionLabel(b.dramatic_function) }}</span>
                    <span v-if="b.source === 'rule'" class="story-beat-rule" title="规则生成（Qwen 不可用），需人工检查">规则</span>
                  </div>
                  <div v-if="b.summary" class="story-beat-summary">{{ b.summary }}</div>
                  <div class="story-beat-meta">
                    <span v-if="b.scene_id" class="story-beat-meta-item">📍 {{ b.scene_id }}</span>
                    <span v-if="b.time" class="story-beat-meta-item">🕐 {{ b.time }}</span>
                    <span v-if="b.weather" class="story-beat-meta-item">🌦 {{ b.weather }}</span>
                    <span class="story-beat-meta-item">🎞 {{ beatShotCount(b) }} 镜</span>
                  </div>
                  <div v-if="b.transition_reason" class="story-beat-transition">↳ 过渡：{{ b.transition_reason }}</div>
                  <details class="story-beat-details">
                    <summary>原文片段（{{ b.text_segments?.length ?? 0 }}）</summary>
                    <div v-for="(seg, i) in (b.text_segments ?? [])" :key="i" class="story-beat-seg">{{ seg }}</div>
                  </details>
                </div>
              </div>

              <div v-if="storyResult.warnings.length" class="script-warnings">
                <div v-for="(w, i) in storyResult.warnings" :key="i" class="script-warning">⚠ {{ w }}</div>
              </div>
            </div>
          </template>

          <!-- 底部操作 -->
          <div class="script-modal-foot">
            <button type="button" class="btn" :disabled="importBusy" @click="closeScriptImport">取消</button>
            <button
              type="button"
              class="btn script-apply"
              :disabled="!importCanApply || importBusy"
              @click="importTab === 'script' ? applyScriptProject() : applyStoryProject()"
            >
              {{ importBusy ? "应用中…" : "应用为 SPA 项目（进工作台）" }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- V1.6-A 成片导出弹窗：范围 + 已生成镜头数 + 缺失/参数过期预检 + 三操作 -->
    <Teleport to="body">
      <div v-if="exportOpen" class="export-modal-mask" @click.self="closeExportModal">
        <div class="export-modal" role="dialog" aria-label="导出成片">
          <div class="export-modal-head">
            <span class="export-modal-title">⭳ 导出成片</span>
            <button type="button" class="export-modal-close" title="关闭（Esc）" @click="closeExportModal">✕</button>
          </div>

          <!-- 范围选择 -->
          <div class="export-scope">
            <label class="export-scope-pill" :class="{ active: exportScope === 'movie' }">
              <input type="radio" value="movie" v-model="exportScope" :disabled="wb.running || exportBusy" />
              <span>整部影片</span>
              <em>{{ flattenedShots().length }} 镜</em>
            </label>
            <label class="export-scope-pill" :class="{ active: exportScope === 'scene' }">
              <input type="radio" value="scene" v-model="exportScope" :disabled="wb.running || exportBusy" />
              <span>当前地点</span>
              <em>{{ scene?.shots.length ?? 0 }} 镜</em>
            </label>
          </div>

          <!-- 全绿：直接预计 + 开始导出 -->
          <template v-if="!exportCheck.missingIds.length && !exportCheck.staleIds.length">
            <div class="export-ok">
              <div class="export-ok-title">🟢 全部镜头已生成且参数未变</div>
              <div class="export-ok-sub">预计：{{ exportTargetShots.length }} 个已生成镜头（{{ exportScope === "scene" ? "当前地点" : "整部影片" }}）</div>
            </div>
            <div class="export-modal-foot">
              <button type="button" class="btn" :disabled="wb.running || exportBusy" @click="closeExportModal">取消</button>
              <button
                type="button"
                class="btn export-go"
                :disabled="wb.running || exportBusy || !exportTargetShots.length"
                @click="beginExport(null, false)"
              >开始导出</button>
            </div>
          </template>

          <!-- 有缺失/过期：列问题 + 三操作 -->
          <template v-else>
            <div class="export-warn">
              <div class="export-warn-title">
                ⚠ {{ exportCheck.missingIds.length + exportCheck.staleIds.length }} 个镜头需要处理
              </div>
              <div class="export-warn-list">
                <div v-for="id in exportCheck.missingIds" :key="'m' + id" class="export-warn-item">
                  <span class="ew-dot ew-missing" />{{ shotNameOf(id) }} · 尚未生成
                </div>
                <div v-for="id in exportCheck.staleIds" :key="'s' + id" class="export-warn-item">
                  <span class="ew-dot ew-stale" />{{ shotNameOf(id) }} · 参数已修改，需重新生成
                </div>
              </div>
            </div>
            <div class="export-modal-foot">
              <button type="button" class="btn" :disabled="wb.running || exportBusy" @click="closeExportModal">取消</button>
              <button
                v-if="exportCheck.okIds.length"
                type="button"
                class="btn export-go"
                :disabled="wb.running || exportBusy"
                title="跳过缺失/过期镜头，只导出已生成且参数未变的镜头"
                @click="beginExport(exportCheck.okIds, true)"
              >仅导出已完成（{{ exportCheck.okIds.length }}）</button>
              <button
                type="button"
                class="btn export-warn-go"
                :disabled="wb.running || exportBusy"
                title="按走查模式逐镜生成缺失/过期镜头"
                @click="goGenerateProblems"
              >去生成缺失镜头（{{ exportCheck.missingIds.length + exportCheck.staleIds.length }}）</button>
            </div>
          </template>
        </div>
      </div>
    </Teleport>

    <!-- V1.8-2：多节点选择弹窗（工作流含多个 MiniMaxH3Director 节点） -->
    <Teleport to="body">
      <div v-if="workflowPickOpen" class="wf-modal-mask" @click.self="workflowPickOpen = false">
        <div class="wf-modal" role="dialog" aria-label="选择生成节点">
          <div class="wf-modal-head">
            <span class="wf-modal-title">⚠️ 检测到 {{ activeWorkflow.nodeCount }} 个 MiniMaxH3Director 节点</span>
            <button type="button" class="wf-modal-close" title="关闭" @click="workflowPickOpen = false">✕</button>
          </div>
          <div class="wf-modal-body">
            <div class="wf-modal-hint">当前工作流有多个生成节点，请选择本次生成使用哪个节点：</div>
            <button
              v-for="nid in activeWorkflow.entry?.directorNodeIds ?? []"
              :key="nid"
              type="button"
              class="wf-node-opt"
              :class="{ sel: activeWorkflow.entry?.selectedNodeId === nid }"
              @click="setWorkflowNode(nid)"
            >节点 {{ nid }} · {{ nodeClassLabel(nid) }}</button>
          </div>
          <div class="wf-modal-foot">
            <button type="button" class="btn" @click="workflowPickOpen = false">取消</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- V1.8-2：导入 workflow_api.json 弹窗 -->
    <Teleport to="body">
      <div v-if="workflowImportOpen" class="wf-modal-mask" @click.self="workflowImportOpen = false">
        <div class="wf-modal" role="dialog" aria-label="导入工作流">
          <div class="wf-modal-head">
            <span class="wf-modal-title">＋ 导入工作流</span>
            <button type="button" class="wf-modal-close" title="关闭" @click="workflowImportOpen = false">✕</button>
          </div>
          <div class="wf-modal-body">
            <div class="wf-modal-hint">选择 ComfyUI 导出的 workflow_api.json（或直接粘贴 JSON），导入后自动扫描 MiniMaxH3Director 节点。</div>
            <input ref="workflowImportInput" type="file" accept=".json,application/json" class="wf-import-file" @change="onWorkflowImportFile" />
            <textarea
              v-model="workflowImportText"
              class="wf-import-text"
              rows="8"
              spellcheck="false"
              placeholder='{ "1": { "class_type": "UNETLoader", "inputs": { } } }'
            />
            <div v-if="workflowImportError" class="wf-import-err">❌ {{ workflowImportError }}</div>
            <div v-if="workflowImportName && workflowImportText.trim()" class="wf-import-name">条目名：{{ workflowImportName }}</div>
          </div>
          <div class="wf-modal-foot">
            <button type="button" class="btn" @click="workflowImportOpen = false">取消</button>
            <button
              type="button"
              class="btn wf-import-go"
              :disabled="!workflowImportText.trim()"
              @click="confirmWorkflowImport"
            >导入并切换</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- V1.7 Phase 4-C：补资产弹窗（审核态 ⚠「资产未找到」→ 从共享资产库补选并写回绑定） -->
    <AssetConfirmDialog
      v-if="assetConfirmTarget && assetConfirmScene && episode"
      :shot="assetConfirmTarget"
      :scene="assetConfirmScene"
      :episode="episode"
      :missing="missingAssetRefs(assetConfirmTarget)"
      @close="assetConfirmTarget = null"
      @confirmed="assetConfirmTarget = null"
    />

    <!-- V1.9：Workflow Studio 完整节点图编辑器 -->
    <WorkflowStudio
      v-if="workflowStudioOpen"
      :workflow="activeWorkflow.workflow"
      :name="activeEntry.name"
      :is-builtin="activeWorkflowId === BUILTIN_WORKFLOW_ID"
      @save="onStudioSave"
      @save-as="onStudioSaveAs"
      @close="workflowStudioOpen = false"
    />

    <!-- V1.9：工作流参数快捷面板 -->
    <WorkflowParamsPanel
      v-if="workflowParamsOpen"
      :workflow="activeWorkflow.workflow"
      :name="activeEntry.name"
      :is-builtin="activeWorkflowId === BUILTIN_WORKFLOW_ID"
      @save="onStudioSave"
      @save-as="onStudioSaveAs"
      @open-studio="openWorkflowStudioFromParams"
      @close="workflowParamsOpen = false"
    />
  </div>
</template>

<style scoped>
.wb { display: flex; flex-direction: column; height: 100vh; background: #101018; color: #e8e8ee; font-family: system-ui, sans-serif; }
.wb-head { display: flex; align-items: center; gap: 16px; padding: 8px 16px; background: #181824; border-bottom: 1px solid #26263a; }
.wb-brand { font-weight: 700; }
.wb-project { color: #9aa; flex: 1; min-width: 60px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 顶栏主标题 = 项目名（首页重命名后同步显示），副标题 = 集标题 */
.wb-project-name { font-weight: 600; color: #e8e8ee; }
.wb-project-sep { margin: 0 6px; color: #3a3a50; }
.wb-project-ep { color: #8a8a9a; font-size: 12px; }
/* V1.6-C inline 改名（顶栏集标题 + 首页项目卡片共用） */
.wb-project .rename-btn { display: none; }
.wb-project:hover .rename-btn { display: inline-flex; }
.rename-btn { background: transparent; border: none; color: #6a6a7a; cursor: pointer; font-size: 12px; padding: 0 4px; border-radius: 4px; line-height: 1; vertical-align: middle; }
.rename-btn:hover { color: #4a7dff; background: rgba(74, 125, 255, 0.12); }
.rename-ok { background: #4a7dff; border: none; color: #fff; cursor: pointer; font-size: 12px; width: 22px; height: 22px; border-radius: 4px; line-height: 1; flex: none; }
.rename-ok:hover { background: #3a6cf0; }
.proj-rename-input { background: #12121e; border: 1px solid #4a7dff; color: #e0e0f0; font-size: 14px; padding: 4px 8px; border-radius: 6px; width: 100%; outline: none; }
.ep-title-input { background: #12121e; border: 1px solid #4a7dff; color: #e0e0f0; font-size: 13px; padding: 3px 8px; border-radius: 6px; width: 220px; outline: none; }
.size-ctl { display: flex; align-items: center; gap: 4px; }
.preset-ctl, .fps-ctl { display: flex; align-items: center; gap: 4px; }
.ctl-label { font-size: 12px; color: #8a8a9a; white-space: nowrap; }
.preset-ctl select, .fps-ctl input, .mp-input { background: #181824; color: #e8e8ee; border: 1px solid #2a2a40; border-radius: 6px; padding: 4px 6px; }
.preset-ctl select:disabled, .mp-input:disabled { opacity: 0.5; }
.mp-input { width: 56px; text-align: center; }
.fps-ctl input { width: 52px; text-align: center; }
.size-ro { color: #c8c8d8; font-size: 13px; white-space: nowrap; }
.size-ctl input { width: 58px; background: #181824; color: #e8e8ee; border: 1px solid #2a2a40; border-radius: 6px; padding: 4px 6px; text-align: center; }
.size-x { color: #8a8a9a; font-size: 12px; }
.wb-conn { font-size: 12px; color: #8a8a9a; display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #555; flex: none; }
.dot.on { background: #3ecf6e; box-shadow: 0 0 6px #3ecf6e; }
.dot.reconn { background: #f5a623; box-shadow: 0 0 6px #f5a623; animation: dot-blink 1.1s ease-in-out infinite; }
.dot.pulse { animation: dot-pulse 0.9s ease-out; }
@keyframes dot-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
@keyframes dot-pulse { 0% { box-shadow: 0 0 0 0 rgba(62, 207, 110, 0.55); } 100% { box-shadow: 0 0 0 9px rgba(62, 207, 110, 0); } }
.gen-ctl { display: flex; align-items: center; gap: 6px; }
/* P0-B（#482）：顶栏右侧构建标识——极弱视觉（不抢操作区），唯一用途=对构建报 bug */
.wb-build-id {
  margin-left: auto;
  flex: none;
  font-size: 10px;
  color: #5c5c70;
  white-space: nowrap;
  user-select: none;
  line-height: 1;
  letter-spacing: 0.2px;
  cursor: help;
}
.wb-build-id:hover { color: #8a8a9a; }
/* #640（2026-08-18）：导入应用成功横幅（顶栏下方浮层，点击 ✕ 关闭） */
.import-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 14px;
  padding: 8px 14px;
  border: 1px solid #2e5a3a;
  border-radius: 10px;
  background: linear-gradient(90deg, rgba(62, 207, 110, 0.10), rgba(62, 207, 110, 0.04));
  font-size: 12px;
  color: #cfe8d8;
  flex-wrap: wrap;
}
.ib-badge { font-weight: 700; color: #3ecf6e; white-space: nowrap; }
.ib-title { font-weight: 600; color: #e8e8ee; white-space: nowrap; }
.ib-meta { color: #9aa9c8; white-space: nowrap; }
.ib-warn { color: #f5c65c; white-space: nowrap; }
.ib-hint { color: #8a8a9a; }
.ib-close {
  margin-left: auto;
  background: none;
  border: none;
  color: #8a8a9a;
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 6px;
}
.ib-close:hover { color: #fff; background: rgba(255, 255, 255, 0.08); }
/* V1.8-2：Workflow Registry 生成栏控件（工作流下拉 + 节点徽标 + 导入） */
.wf-ctl { display: flex; align-items: center; gap: 4px; padding: 2px 6px; border: 1px solid #26263a; border-radius: 8px; background: #12121e; }
.wf-pick { display: flex; align-items: center; gap: 4px; }
.wf-select { background: #181824; color: #e8e8ee; border: 1px solid #2a2a40; border-radius: 6px; padding: 3px 6px; font-size: 12px; max-width: 180px; }
.wf-select:disabled { opacity: 0.5; }
/* V1.12：全局种子控件（🎲 掷新种子 / 🔒 固定 与 自动随机 切换 / 固定种子输入） */
.seed-ctl { display: flex; align-items: center; gap: 5px; padding: 2px 6px; border: 1px solid #26263a; border-radius: 8px; background: #12121e; }
.seed-ctl .seed-roll { padding: 1px 6px; font-size: 13px; line-height: 1.2; }
.seed-ctl .seed-auto { font-size: 12px; color: #8a8a9a; white-space: nowrap; }
.seed-input {
  width: 92px; background: #181824; color: #e8e8ee; border: 1px solid #2a2a40;
  border-radius: 6px; padding: 3px 6px; font-size: 12px; font-variant-numeric: tabular-nums;
}
.seed-input:disabled { opacity: 0.5; }
.seed-pill {
  font-size: 11px; padding: 2px 8px; border-radius: 999px; cursor: pointer; white-space: nowrap;
  border: 1px solid #2a2a40; background: transparent; color: #a8a8b8;
}
.seed-pill.on { color: #ffd76a; border-color: #6b5a2a; background: rgba(107, 90, 42, 0.15); }
.seed-pill:disabled { opacity: 0.5; cursor: default; }
.seed-ctl.seed-fixed { border-color: #6b5a2a; }
.wf-badge { font-size: 11px; padding: 3px 8px; border-radius: 999px; border: 1px solid #2a2a40; background: transparent; cursor: pointer; white-space: nowrap; }
.wf-badge.wf-ok { color: #6ecf8a; border-color: #2f6b42; background: rgba(47, 107, 66, 0.15); }
.wf-badge.wf-warn { color: #ffcf7a; border-color: #6b5a2a; background: rgba(107, 90, 42, 0.15); }
.wf-badge.wf-bad { color: #ff8a8a; border-color: #6b3a3a; background: rgba(107, 58, 58, 0.15); }
.wf-badge:disabled { opacity: 0.5; cursor: default; }
.wf-del, .wf-import { padding: 3px 8px; font-size: 12px; }
/* V1.8-2：工作流弹窗（多节点选择 / 导入） */
.wf-modal-mask { position: fixed; inset: 0; background: rgba(6, 8, 16, 0.72); display: flex; align-items: center; justify-content: center; z-index: 120; }
.wf-modal { background: #181824; border: 1px solid #33334d; border-radius: 12px; width: min(520px, 92vw); max-height: 82vh; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); }
.wf-modal-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid #26263a; }
.wf-modal-title { font-weight: 600; color: #e8e8ee; }
.wf-modal-close { background: transparent; border: none; color: #8a8aa0; cursor: pointer; font-size: 13px; padding: 2px 6px; border-radius: 4px; }
.wf-modal-close:hover { color: #ffb0b0; background: rgba(107, 58, 58, 0.2); }
.wf-modal-body { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }
.wf-modal-hint { color: #9a9ab0; font-size: 13px; line-height: 1.5; }
.wf-node-opt { text-align: left; background: #12121e; border: 1px solid #2a2a40; color: #cfcfe0; padding: 8px 12px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.wf-node-opt:hover { border-color: #4a7dff; }
.wf-node-opt.sel { border-color: #4a7dff; background: #26324a; color: #9fc0ff; }
.wf-modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid #26263a; }
.wf-import-file { color: #9a9ab0; font-size: 12px; }
.wf-import-text { background: #12121e; border: 1px solid #2a2a40; color: #e0e0f0; border-radius: 8px; padding: 8px 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; resize: vertical; }
.wf-import-err { color: #ff8a8a; font-size: 12px; }
.wf-import-name { color: #8a8aa0; font-size: 12px; }
.wf-import-go { background: #4a7dff; color: #fff; }
.wf-import-go:disabled { opacity: 0.5; cursor: default; }
.gen-scope { background: #181824; color: #e8e8ee; border: 1px solid #2a2a40; border-radius: 6px; padding: 4px 6px; font-size: 13px; }
.gen-mode { display: flex; align-items: center; gap: 3px; }
.gen-mode-label { color: #8a8aa0; font-size: 11px; white-space: nowrap; margin-right: 2px; }
.gen-mode-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 3px 9px;
  border-radius: 999px;
  background: #181824;
  border: 1px solid #2a2a40;
  color: #8a8aa0;
  font-size: 12px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.gen-mode-pill input { position: absolute; opacity: 0; pointer-events: none; }
.gen-mode-pill span { pointer-events: none; }
.gen-mode-pill.active { background: #26324a; border-color: #4a7dff; color: #9fc0ff; }
.gen-mode-pill:not(.active):hover { color: #cfcfe0; border-color: #3a3a58; }
.gen-mode-pill:has(input:disabled) { opacity: 0.5; cursor: not-allowed; }
.gen-final-hint { color: #ff9f43; font-size: 11px; white-space: nowrap; }
.gen-scope:disabled { opacity: 0.5; }
.btn { padding: 6px 14px; border-radius: 6px; border: none; cursor: pointer; }
.btn.primary { background: #4a7dff; color: #fff; }
.btn.primary:disabled { opacity: 0.5; cursor: default; }
/* V1.7 Phase 4：范围内含未采纳 AI 草稿 → 生成按钮警示态（可点，点击弹「请先采纳」）。 */
.btn.primary.review-gate {
  background: #6b5a2a;
  color: #ffcf7a;
  border: 1px solid #8a742e;
  animation: review-gate-pulse 2s ease-in-out infinite;
}
@keyframes review-gate-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(255, 207, 122, 0); }
  50% { box-shadow: 0 0 8px 0 rgba(255, 207, 122, 0.45); }
}
.btn.danger { background: #6b2b2b; color: #ffb3b3; }
.btn.danger:hover { background: #7d3434; color: #ffd0d0; }
/* V1.6-A 导出按钮三态：🟢 全部可导出 / 🟡 有缺失或过期 / 🔴 无可导出 */
.btn.export { background: #1c1c2b; color: #c8c8d8; border: 1px solid #33334d; font-weight: 600; }
.btn.export:hover { border-color: #4a7dff; color: #e0e8ff; }
.btn.export.export-green { border-color: #2f6b42; color: #6ecf8a; }
.btn.export.export-green:hover { background: #1f3a28; color: #9fe8b4; }
.btn.export.export-yellow { border-color: #6b5a2a; color: #ffcf7a; }
.btn.export.export-yellow:hover { background: #3a2f1a; color: #ffe0a8; }
.btn.export.export-red { border-color: #6b3a3a; color: #ff8a8a; }
.btn.export.export-red:hover { background: #3a1f1f; color: #ffb0b0; }
.btn.export:disabled { opacity: 0.5; cursor: default; }
.btn.export:disabled:hover { background: #1c1c2b; border-color: #33334d; color: #c8c8d8; }
.btn.nav { background: #26263a; color: #c8c8d8; }
.btn.save { background: #2a2a40; color: #9ab8ff; }
.btn.save.dirty { background: #3a2f1a; color: #ffcf7a; border: 1px solid #6b5a2a; font-weight: 600; }
.btn.big { padding: 12px 20px; font-size: 15px; background: #4a7dff; color: #fff; }
.btn.big.ghost { background: transparent; border: 1px solid #3a3a55; color: #c8c8d8; }
.btn.big:disabled { opacity: 0.5; cursor: default; }
.import-input { display: none; }
/* V1.7 Phase 4：AI 制作计划审核横幅（顶栏下方通栏）。 */
.review-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 14px;
  padding: 8px 12px;
  border-radius: 8px;
  background: linear-gradient(90deg, #2a2540 0%, #1f2440 60%, #1a2038 100%);
  border: 1px solid #3a3560;
}
.review-banner-info { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.review-banner-title { font-weight: 700; color: #b9a8ff; }
.review-banner-count { font-size: 12px; color: #ffcf7a; }
.review-banner-stat { font-size: 12px; color: #8a8a9a; }
.review-banner-actions { display: flex; gap: 6px; }
.review-adopt-all { border: 1px solid #4a7dff; color: #9fc0ff; }
.review-adopt-all:hover { background: #26324a; }
/* V1.10-C：生成中顶栏常驻横幅（可点击展开详情）。 */
.gen-banner {
  margin: 8px 14px 0;
  border-radius: 10px;
  border: 1px solid rgba(74, 125, 255, 0.5);
  background: linear-gradient(90deg, rgba(74, 125, 255, 0.16), rgba(26, 30, 54, 0.6) 55%, rgba(46, 30, 60, 0.5));
  cursor: pointer;
  overflow: hidden;
}
.gen-banner:hover { border-color: #6c97ff; }
.gen-banner-main {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 14px;
  font-size: 13px;
}
.gen-banner-badge {
  flex-shrink: 0;
  font-weight: 700;
  color: #ffcf7a;
  letter-spacing: 0.03em;
}
.gen-banner-shot {
  flex-shrink: 0;
  font-weight: 700;
  color: #cfe0ff;
  font-variant-numeric: tabular-nums;
}
.gen-banner-name {
  flex-shrink: 0;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #cfcfe0;
  font-size: 12px;
}
.gen-banner-bar {
  flex: 1;
  min-width: 120px;
  height: 8px;
  background: rgba(10, 14, 30, 0.5);
  border-radius: 4px;
  overflow: hidden;
}
.gen-banner-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #4a7dff, #8ab2ff);
  border-radius: 4px;
  transition: width 0.3s;
}
.gen-banner-phase {
  flex-shrink: 0;
  color: #9ab8ff;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.gen-banner-eta {
  flex-shrink: 0;
  color: #6ecf8a;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.gen-banner-time {
  flex-shrink: 0;
  color: #8a8aa0;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.gen-banner-caret {
  flex-shrink: 0;
  color: #6a6a80;
  font-size: 12px;
}
.gen-banner.open { border-color: #6c97ff; }
.gen-banner-detail {
  border-top: 1px solid rgba(74, 125, 255, 0.28);
  padding: 12px 14px 14px;
  background: rgba(16, 18, 32, 0.5);
}
.gen-banner-detail-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 16px;
}
.gbd-item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.gbd-item em { font-style: normal; color: #6a6a80; font-size: 11px; }
.gbd-item b { color: #cfcfe0; font-size: 13px; font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gen-banner-detail-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  gap: 10px;
}
.gbd-conf { color: #6ecf8a; font-size: 12px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.gbd-actions { display: flex; gap: 8px; }
.gbd-btn { font-size: 12px; padding: 5px 12px; }
@media (max-width: 1000px) {
  .gen-banner-detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.wb-body { display: flex; flex: 1; min-height: 0; }
.wb-left { width: 270px; border-right: 1px solid #26263a; overflow-y: auto; padding: 8px; }
.wb-left-title, .wb-right-title { font-size: 12px; color: #8a8a9a; text-transform: uppercase; margin-bottom: 8px; }
.scene-block { margin-bottom: 8px; }
.scene-name { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-weight: 600; }
.scene-name.active { background: #2a2a40; }
.scene-ico { font-size: 13px; }
.scene-name-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scene-meta { font-size: 11px; color: #8a8a9a; font-weight: 400; flex-shrink: 0; }
.scene-ops { display: flex; gap: 2px; opacity: 0.25; transition: opacity 0.15s; flex-shrink: 0; }
.scene-name:hover .scene-ops { opacity: 1; }
.scene-ops .scene-play { color: #3ecf6e; font-size: 11px; padding: 1px 4px; }
.shot-list { margin-left: 10px; border-left: 1px solid #2a2a40; padding-left: 6px; padding-bottom: 160px; }
.shot-card { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border-radius: 6px; cursor: pointer; font-size: 13px; position: relative; min-width: 0; }
.shot-card.active { background: #33334d; }
/* V1.7 Phase 4：AI 草稿镜头卡片视觉（待审核/已采纳）。
   P2-⑧ 已采纳降权：已采纳是「完成态」，不再整卡高亮（绿底/绿虚线抢视觉重心），
   恢复与普通卡片一致的常态，只保留「🤖 ✓ 已采纳」小徽标；
   视觉重心留给「待审核」卡片（黄虚线）与「当前选中」卡片。 */
.shot-card.shot-draft { border: 1px dashed #6b5a2a; background: #26203a; }
.shot-card.shot-draft-ok { border-color: transparent; background: transparent; }
.shot-draft-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 16px;
  white-space: nowrap;
  cursor: help;
}
.shot-draft-badge.ready { background: #1f3a28; color: #6ecf8a; border: 1px solid #2f6b42; }
.shot-draft-badge.notready { background: #3a2f1a; color: #ffcf7a; border: 1px solid #6b5a2a; }
/* P2-⑧ 已采纳徽标降权：无边框、半透明小字，安静可识别，不作主要视觉元素。 */
.shot-draft-badge.adopted { background: transparent; color: #6f8a75; border-color: transparent; }
.shot-draft-adopt {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid #4a7dff;
  background: #26324a;
  color: #9fc0ff;
  font-size: 11px;
  line-height: 16px;
  cursor: pointer;
}
.shot-draft-adopt:hover:not(:disabled) { background: #33406a; color: #cfe0ff; }
.shot-draft-adopt:disabled { opacity: 0.5; cursor: default; }
/* 镜头操作浮层：hover 卡片时在卡片下方弹出，不参与 flex 布局 →
   不再挤压右侧常显的 播放/生成/下载 按钮（下载按钮始终可见可点）。 */
.shot-ops {
  position: absolute;
  top: calc(100% + 2px);
  right: 0;
  z-index: 50;
  display: none;
  flex-direction: column;
  gap: 2px;
  min-width: 104px;
  padding: 4px;
  background: #202036;
  border: 1px solid #2f2f4a;
  border-radius: 6px;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.45);
}
.shot-card:hover .shot-ops,
.shot-ops:hover {
  display: flex;
}
/* hover 桥：浮层从卡片下方弹出（top: calc(100% + 2px)），卡片与浮层之间有 2px
   间隙——鼠标离开卡片、还没进入浮层时 `.shot-card:hover` 与 `.shot-ops:hover`
   同时失效，浮层瞬间消失，导致所有镜头浮层里的操作按钮都点不到。
   伪元素把间隙 + 缓冲（10px）划进浮层自己的 hover 区，鼠标经过间隙时
   `.shot-ops:hover` 仍成立，浮层保持显示。 */
.shot-ops::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: -10px;
  height: 10px;
}
/* 浮层向上弹（onShotCardEnter 检测卡片接近列表底部时给 .shot-ops 加 .ops-up，
   否则浮层在滚动容器下缘被裁剪，靠底部的删除/上移等按钮点不到）。 */
.shot-ops.ops-up {
  top: auto;
  bottom: calc(100% + 2px);
}
/* 向上弹时 hover 桥移到浮层下方，覆盖卡片下缘到浮层之间的间隙。 */
.shot-ops.ops-up::before {
  top: auto;
  bottom: -10px;
}
.shot-ops .icon-btn {
  font-size: 11px;
  padding: 4px 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  text-align: left;
  border-radius: 4px;
}
.shot-ops .icon-btn:hover { background: #2f2f4a; color: #e8e8ee; }
.shot-ops .icon-btn:last-child:hover { background: rgba(255, 107, 107, 0.16); color: #ff9a9a; }
.icon-btn { background: transparent; border: none; color: #8a8a9a; cursor: pointer; font-size: 12px; padding: 1px 4px; border-radius: 4px; line-height: 1; flex-shrink: 0; }
.icon-btn:hover { color: #e8e8ee; background: #2a2a40; }
.wb-left-title { display: flex; align-items: center; justify-content: space-between; }
.add-scene { color: #9ab8ff; font-size: 12px; }
.add-scene:hover { color: #cfe0ff; background: #1c2a4a; }
.shot-thumb { width: 24px; height: 24px; border-radius: 4px; background: #26263a; display: flex; align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0; }
.shot-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shot-dur { color: #8a8a9a; font-size: 11px; flex-shrink: 0; }
/* V1.10-D：Shot 卡「⏱ 上次生成」耗时。 */
.shot-lastgen { color: #5a8a6a; font-size: 11px; flex-shrink: 0; font-variant-numeric: tabular-nums; white-space: nowrap; cursor: help; }
/* V1.10-D：项目层历史耗时统计（平均/最快/最慢/已生成 N 镜）。 */
.proj-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 6px 0 8px;
  padding: 6px 8px;
  border: 1px solid #2a2a40;
  border-radius: 6px;
  background: #16161f;
  font-size: 11px;
}
.ps-item { color: #8a8a9a; white-space: nowrap; }
.ps-item b { color: #cfcfe0; font-variant-numeric: tabular-nums; font-weight: 600; }
.ps-item b.ps-fast { color: #3ecf6e; }
.ps-item b.ps-slow { color: #ff9f43; }
.ps-item.ps-done { color: #9ab8ff; }
.shot-sel { margin: 0; accent-color: #4a7dff; cursor: pointer; flex-shrink: 0; }
.shot-card.sel { background: #1d2a4a; outline: 1px solid #4a7dff; }
.shot-status { width: 8px; height: 8px; border-radius: 50%; background: #444; flex-shrink: 0; }
.shot-status.st-running { background: #4a7dff; box-shadow: 0 0 6px #4a7dff; animation: st-breathe 1.2s ease-in-out infinite; }
.shot-status.st-failed { background: #ff6b6b; box-shadow: 0 0 6px #ff6b6b; }
.shot-status.st-review { background: #ffa94d; box-shadow: 0 0 6px #ffa94d; }
.shot-status.st-stale { background: #ffa94d; box-shadow: 0 0 6px #ffa94d; outline: 1px dashed #ffa94d; }
.shot-status.st-done { background: #3ecf6e; box-shadow: 0 0 6px #3ecf6e; }
@keyframes st-breathe { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
.shot-gen-mini { width: 20px; height: 20px; padding: 0; border: none; border-radius: 4px; background: #2a2a40; color: #9ab8ff; cursor: pointer; font-size: 11px; line-height: 1; flex-shrink: 0; }
.shot-gen-mini:hover:not(:disabled) { background: #4a7dff; color: #fff; }
.shot-gen-mini:disabled { opacity: 0.4; cursor: default; }
.shot-gen-mini.shot-dl { color: #9fe0b0; }
.shot-gen-mini.shot-dl:hover:not(:disabled) { background: #3ecf6e; color: #fff; }
.shot-gen-mini.shot-play { color: #ffd88a; }
.shot-gen-mini.shot-play:hover:not(:disabled) { background: #e8a94d; color: #fff; }
.wb-center { flex: 1; display: flex; flex-direction: column; min-width: 0; }
/* V1.11.1：唯一大播放器（含头部镜头信息 + ⛶ 唯一放大入口） */
.player-wrap { flex: 1; display: flex; flex-direction: column; min-height: 0; background: #0a0a10; }
.player-head { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 4px 12px; min-height: 30px; }
.ph-shot { font-size: 12px; font-weight: 600; color: #e8e8ee; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ph-scene { font-size: 11px; color: #9ab8ff; background: rgba(74, 125, 255, 0.12); border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
.ph-dur { font-size: 11px; color: #8a8a9a; white-space: nowrap; }
.ph-spacer { flex: 1; }
.ph-cinema { font-size: 14px; padding: 2px 8px; }
.player { flex: 1; display: flex; align-items: center; justify-content: center; min-height: 0; flex-direction: column; gap: 6px; position: relative; }
.player-img { max-width: 100%; max-height: 100%; }
.player-video { max-width: 100%; max-height: 100%; border-radius: 8px; box-shadow: 0 0 24px rgba(74, 125, 255, 0.25); }
.player-video-meta { font-size: 12px; color: #9ab8ff; }
.player-err { position: absolute; left: 0; right: 0; bottom: 0; padding: 8px 12px; font-size: 13px; color: #ffd8a8; background: rgba(120, 50, 20, 0.85); border-radius: 0 0 8px 8px; z-index: 2; text-align: center; }
.player-empty { text-align: center; color: #8a8a9a; padding: 20px; }
.player-placeholder { font-size: 20px; margin-bottom: 12px; }
.player-prompt { max-width: 480px; font-size: 13px; line-height: 1.6; }
/* V1.11.1：Shot 信息卡（纯文字：剧本原文 + Prompt 五区 + 资产 + 参数；视频不在此重复播放） */
.shot-detail { flex: 0 0 auto; border-top: 1px solid #26263a; background: #141420; }
.shot-detail-head { display: flex; align-items: center; gap: 8px; padding: 6px 12px; min-height: 34px; }
.sd-collapse { background: transparent; border: none; color: #8a8a9a; cursor: pointer; font-size: 12px; padding: 2px 6px; border-radius: 4px; line-height: 1; }
.sd-collapse:hover { color: #e8e8ee; background: #2a2a40; }
.sd-title { font-size: 13px; font-weight: 600; color: #e8e8ee; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 180px; }
.sd-scene { font-size: 11px; color: #9ab8ff; background: rgba(74, 125, 255, 0.12); border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
.sd-chip { font-size: 11px; color: #8a8a9a; background: #22223a; border: 1px solid #2e2e46; border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
.sd-chip.sd-gen-chip { color: #ffe0b0; background: rgba(232, 169, 77, 0.12); border-color: rgba(232, 169, 77, 0.35); }
.sd-chip.sd-gen-chip.sd-gen-active { color: #b9f0c8; background: rgba(62, 207, 110, 0.14); border-color: rgba(62, 207, 110, 0.4); }
.sd-status { font-size: 11px; color: #8a8a9a; white-space: nowrap; }
.sd-status.st-running { color: #7aa7ff; }
.sd-status.st-failed { color: #ff8f8f; }
.sd-status.st-review, .sd-status.st-stale { color: #ffc078; }
.sd-status.st-done { color: #7fe3a0; }
.sd-actions { margin-left: auto; display: flex; gap: 6px; }
.sd-btn { font-size: 12px; padding: 2px 10px; }
.sd-dl { font-size: 12px; color: #9fe0b0; background: transparent; border: 1px solid rgba(62, 207, 110, 0.4); border-radius: 4px; padding: 2px 8px; cursor: pointer; }
.sd-dl:hover:not(:disabled) { background: rgba(62, 207, 110, 0.16); color: #d9ffe4; }
.shot-detail-body { display: flex; flex-direction: column; gap: 4px; padding: 4px 12px 10px; min-height: 0; overflow-y: auto; max-height: 220px; }
.sd-copy-row { display: flex; align-items: center; gap: 8px; }
.sd-copy-hint { flex: 1; font-size: 11px; color: #8a8a9a; }
.sd-original { border: 1px solid #2e2e46; border-radius: 6px; background: #161625; padding: 4px 8px; }
.sd-original summary { font-size: 12px; color: #c4c4d2; cursor: pointer; user-select: none; }
.sd-original-text { font-size: 12px; color: #c4c4d2; line-height: 1.6; white-space: pre-wrap; word-break: break-word; max-height: 90px; overflow-y: auto; margin: 6px 0 2px; }
.sd-prompt { display: flex; gap: 8px; font-size: 12px; line-height: 1.5; }
.sd-prompt.muted .sd-val { color: #555; }
.sd-label { flex: 0 0 auto; color: #9ab8ff; font-size: 11px; min-width: 48px; }
.sd-val { color: #c8c8d8; word-break: break-word; white-space: pre-wrap; max-height: 60px; overflow-y: auto; }
.sd-gen { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
.sd-gen-chip { font-size: 10px; color: #9fe0b0; background: rgba(62, 207, 110, 0.10); border: 1px solid rgba(62, 207, 110, 0.28); border-radius: 4px; padding: 1px 6px; }

/* V1.11.1：纯版本条（图片缩略图；点击切中央播放器；Prompt/参数去下方信息卡） */
.gen-strip { flex: 0 0 auto; width: 100%; max-width: 760px; border-top: 1px solid #26263a; }
.gen-strip-head { display: flex; align-items: center; gap: 8px; padding: 6px 2px 2px; }
.gen-strip-title { font-size: 12px; font-weight: 600; color: #c8c8d8; }
.gen-strip-hint { font-size: 11px; color: #6a6a7a; flex: 1; }
.gen-strip-list { display: flex; gap: 8px; padding: 6px 0 4px; overflow-x: auto; }
.gen-chip { flex: 0 0 auto; width: 136px; background: #181824; border: 1px solid #2a2a40; border-radius: 8px; padding: 5px; cursor: pointer; display: flex; flex-direction: column; gap: 3px; transition: border-color 0.12s, background 0.12s; }
.gen-chip:hover { border-color: #3a4a6a; }
.gen-chip.active { border-color: #4a7dff; background: #1d2a4a; box-shadow: 0 0 8px rgba(74, 125, 255, 0.25); }
.gen-chip.adopted { border-color: #3ecf6e; }
.gen-chip.adopted.active { border-color: #3ecf6e; box-shadow: 0 0 8px rgba(62, 207, 110, 0.3); }
.gen-chip.newest { border-color: #e8a94d; }
.gen-chip.newest.active { border-color: #e8a94d; box-shadow: 0 0 8px rgba(232, 169, 77, 0.3); }
.gen-thumb { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #0a0a10; border-radius: 4px; overflow: hidden; display: flex; align-items: center; justify-content: center; }
.gen-thumb img { width: 100%; height: 100%; object-fit: cover; }
.gen-thumb-ph { font-size: 18px; opacity: 0.5; }
.gen-chip-idx { position: absolute; left: 3px; bottom: 3px; font-size: 10px; color: #0a0a10; background: rgba(255, 255, 255, 0.75); border-radius: 3px; padding: 0 4px; font-weight: 600; }
.gen-del-corner { position: absolute; right: 3px; top: 3px; font-size: 10px; line-height: 1; color: #ff8f8f; background: rgba(10, 10, 16, 0.7); border: 1px solid rgba(255, 107, 107, 0.5); border-radius: 4px; padding: 2px 4px; cursor: pointer; opacity: 0; transition: opacity 0.12s; }
.gen-chip:hover .gen-del-corner { opacity: 1; }
.gen-del-corner:hover { background: rgba(255, 107, 107, 0.2); }
.gen-meta { display: flex; align-items: center; gap: 6px; min-width: 0; }
.gen-dur { font-size: 11px; font-weight: 600; color: #e8e8ee; white-space: nowrap; }
.gen-time { font-size: 10px; color: #6a6a7a; white-space: nowrap; }
.gen-badges { display: flex; gap: 3px; margin-left: auto; }
.gb { font-size: 10px; border-radius: 3px; padding: 0 4px; white-space: nowrap; }
.gb-newest { color: #ffe0b0; background: rgba(232, 169, 77, 0.16); border: 1px solid rgba(232, 169, 77, 0.4); }
.gb-adopted { color: #b9f0c8; background: rgba(62, 207, 110, 0.16); border: 1px solid rgba(62, 207, 110, 0.4); }
.gen-strip-foot { display: flex; align-items: center; gap: 8px; padding: 2px 0 8px; }
.gen-foot-btn { font-size: 11px; padding: 2px 10px; }
/* P2-⑨：foot 现只保留「⭐ 设为当前成片」单按钮，不再右推。 */
.gen-foot-adopt { }
/* V1.11：Shot 卡 / 时间线卡「N版」徽标 */
.shot-gen-badge { font-size: 10px; color: #9ab8ff; background: rgba(74, 125, 255, 0.14); border: 1px solid rgba(74, 125, 255, 0.35); border-radius: 4px; padding: 0 5px; margin-left: 6px; white-space: nowrap; }
.shot-gen-badge .gb { padding: 0; }
.tl-gen-badge { font-size: 10px; color: #9ab8ff; background: rgba(74, 125, 255, 0.14); border: 1px solid rgba(74, 125, 255, 0.35); border-radius: 4px; padding: 0 5px; margin-left: 4px; white-space: nowrap; }

/* V1.11.1：Cinema Mode 全屏（版本圆点条 + 设为当前成片 + 关闭） */
.cinema-mask { position: fixed; inset: 0; z-index: 1000; background: rgba(4, 4, 10, 0.96); display: flex; flex-direction: column; }
.cinema-top { flex: 0 0 auto; display: flex; align-items: center; gap: 12px; padding: 10px 16px; }
.cinema-title { font-size: 14px; font-weight: 600; color: #e8e8ee; }
.cinema-close { margin-left: auto; }
.cinema-stage { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; }
.cinema-video { max-width: 100%; max-height: 100%; border-radius: 8px; box-shadow: 0 0 40px rgba(74, 125, 255, 0.3); }
.cinema-err { position: absolute; left: 50%; transform: translateX(-50%); top: 16px; max-width: 80%; padding: 8px 16px; font-size: 14px; color: #ffd8a8; background: rgba(120, 50, 20, 0.9); border-radius: 8px; z-index: 2; text-align: center; }
.cinema-empty { font-size: 16px; color: #6a6a7a; }
.cinema-bar { flex: 0 0 auto; display: flex; align-items: center; gap: 12px; padding: 12px 16px 16px; }
.cinema-gen-label { font-size: 12px; color: #c8c8d8; white-space: nowrap; }
.cinema-dots { display: flex; gap: 8px; }
.cinema-dot { min-width: 28px; height: 28px; border-radius: 50%; border: 1px solid #3a3a52; background: #181824; color: #8a8a9a; font-size: 12px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; padding: 0 6px; box-sizing: border-box; }
.cinema-dot:hover { border-color: #4a7dff; color: #cfe0ff; }
.cinema-dot.on { border-color: #4a7dff; background: #1d2a4a; color: #fff; box-shadow: 0 0 8px rgba(74, 125, 255, 0.4); }
.cinema-dot.star { border-color: #3ecf6e; color: #b9f0c8; }
.cinema-dot.on.star { border-color: #3ecf6e; background: rgba(62, 207, 110, 0.2); box-shadow: 0 0 8px rgba(62, 207, 110, 0.4); }
.cinema-spacer { flex: 1; }
.cinema-adopt { font-size: 12px; padding: 4px 14px; }

/* P1-A-5（#525）：全片播放顺序时间线 = 中间主区顶部主视图。
   剧情概览行（标题 + 统计 + 提示）+ 筛选行（地点/人物下拉 + 清除）+ 横向滚动卡片。 */
.tl-overview { flex: 0 0 auto; display: flex; flex-direction: column; gap: 6px; padding: 8px 12px 6px; border-bottom: 1px solid #26263a; }
.tl-overview-main { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.tl-overview-title { font-size: 12px; font-weight: 600; color: #c8c8d8; white-space: nowrap; }
.tl-overview-stats { font-size: 11px; color: #8a8a9a; white-space: nowrap; }
.tl-head-hint { font-size: 11px; color: #6a6a7a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-overview-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tl-filter-select { background: #181824; color: #e8e8ee; border: 1px solid #2a2a40; border-radius: 6px; padding: 3px 8px; font-size: 11px; max-width: 200px; }
.tl-filter-select:disabled { opacity: 0.5; cursor: not-allowed; }
.tl-filter-clear { font-size: 11px; color: #ffd88a; background: rgba(232, 169, 77, 0.12); border: 1px solid rgba(232, 169, 77, 0.4); border-radius: 4px; padding: 2px 8px; cursor: pointer; }
.tl-filter-clear:hover { background: rgba(232, 169, 77, 0.22); }
.timeline { display: flex; gap: 8px; padding: 4px 12px 10px; overflow-x: auto; }
.tl-empty { flex: 0 0 auto; padding: 12px 8px; font-size: 12px; color: #6a6a7a; }
.tl-card { flex: 0 0 auto; width: 156px; background: #181824; border: 1px solid #2a2a40; border-radius: 8px; padding: 6px; cursor: pointer; display: flex; flex-direction: column; gap: 4px; transition: border-color 0.12s, background 0.12s; }
.tl-card:hover { border-color: #3a4a6a; }
.tl-card.active { border-color: #4a7dff; background: #1d2a4a; box-shadow: 0 0 8px rgba(74, 125, 255, 0.25); }
.tl-card.tl-dragging { opacity: 0.45; }
.tl-card.tl-drop-over { border-color: #3ecf6e; box-shadow: 0 0 10px rgba(62, 207, 110, 0.45); }
.tl-thumb { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #0a0a10; border-radius: 4px; overflow: hidden; display: flex; align-items: center; justify-content: center; }
.tl-thumb img { width: 100%; height: 100%; object-fit: cover; }
.tl-thumb-ph { font-size: 18px; opacity: 0.5; }
.tl-thumb-idx { position: absolute; left: 3px; bottom: 3px; font-size: 10px; color: #0a0a10; background: rgba(255, 255, 255, 0.75); border-radius: 3px; padding: 0 4px; font-weight: 600; }
.tl-scene-bar { position: absolute; top: 0; bottom: 0; left: 0; width: 4px; }
.tl-info { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
.tl-scene-tag { flex-shrink: 0; font-size: 10px; border: 1px solid; border-radius: 4px; padding: 0 5px; max-width: 72px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: 0.95; }
.tl-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; color: #e8e8ee; }
.tl-meta { font-size: 11px; color: #8a8a9a; flex-shrink: 0; }
.tl-summary { flex-basis: 100%; font-size: 11px; color: #9aa9c8; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-status { width: 8px; height: 8px; border-radius: 50%; background: #444; flex-shrink: 0; align-self: center; }
.tl-status.st-running { background: #4a7dff; box-shadow: 0 0 6px #4a7dff; animation: st-breathe 1.2s ease-in-out infinite; }
.tl-status.st-failed { background: #ff6b6b; box-shadow: 0 0 6px #ff6b6b; }
.tl-status.st-review, .tl-status.st-stale { background: #ffa94d; box-shadow: 0 0 6px #ffa94d; }
.tl-status.st-done { background: #3ecf6e; box-shadow: 0 0 6px #3ecf6e; }
.wb-right { width: 300px; border-left: 1px solid #26263a; padding: 8px 12px; overflow-y: auto; }
.shot-gen { width: 100%; margin-bottom: 10px; }
.fld { display: block; margin-bottom: 10px; }
.fld span { display: block; font-size: 12px; color: #8a8a9a; margin-bottom: 4px; }
.fld-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.fld-head span { margin-bottom: 0; }
.prompt-zoom-btn { font-size: 11px; color: #9ab8ff; background: rgba(74, 125, 255, 0.12); border: 1px solid rgba(74, 125, 255, 0.35); border-radius: 6px; padding: 2px 8px; cursor: pointer; }
.prompt-zoom-btn:hover { background: rgba(74, 125, 255, 0.22); color: #cfe0ff; }
.fld select, .fld input, .fld textarea { width: 100%; box-sizing: border-box; background: #181824; color: #e8e8ee; border: 1px solid #2a2a40; border-radius: 6px; padding: 6px; }
/* V2 Prompt 分区编辑器：折叠分区（摄影/风格/声音/负面） */
.prompt-sections { border: 1px solid #2a2a40; border-radius: 6px; padding: 6px 8px; background: rgba(24, 24, 36, 0.5); }
.prompt-sections-head { display: flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; padding: 2px 0; }
.prompt-sections-head .ps-title { font-size: 12px; color: #c8c8d8; font-weight: 600; margin: 0; }
.prompt-sections-head .ps-hint { font-size: 11px; color: #6a6a7a; flex: 1; margin: 0; }
.prompt-sections-head .ps-toggle { font-size: 11px; color: #9ab8ff; margin: 0; }
.prompt-sections-head:hover .ps-toggle { color: #cfe0ff; }
.prompt-sections-body { margin-top: 8px; }
.ps-sec { margin-bottom: 8px; }
.ps-sec:last-of-type { margin-bottom: 4px; }
.ps-camera-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.ps-camera-select { flex: 1; }
.ps-camera-badge { font-size: 10px; color: #8fe3a0; background: rgba(74, 220, 120, 0.12); border: 1px solid rgba(74, 220, 120, 0.35); border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
.ps-preview { border: 1px dashed #2f3a55; background: rgba(74, 125, 255, 0.06); border-radius: 6px; padding: 6px 8px; margin-top: 6px; }
.ps-preview-label { font-size: 11px; color: #9ab8ff; margin-bottom: 4px; }
.ps-preview-text { font-size: 12px; color: #c8c8d8; line-height: 1.6; white-space: pre-wrap; word-break: break-word; max-height: 120px; overflow-y: auto; }
/* #370：剧本原文审核对照区（只读，默认展开；折叠头复用 wb-right-sub + ps-toggle 视觉） */
.script-original-head { display: flex; justify-content: space-between; align-items: center; cursor: pointer; user-select: none; }
.script-original-head:hover { color: #e8e8ee; }
.script-original { background: #14141f; border: 1px solid #2e2e46; border-radius: 6px; padding: 8px 10px; margin: 2px 0 10px; font-size: 12px; line-height: 1.6; color: #c4c4d2; white-space: pre-wrap; word-break: break-word; max-height: 160px; overflow-y: auto; }

/* P0-①（#400）：右侧「🧬 H3 三层」折叠（只读 shot.h3Prompt；三层=原文/AI 理解/H3 三段） */
.h3-shot-head { display: flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; }
.h3-shot-head:hover { color: #e8e8ee; }
.h3-shot-count { font-size: 10px; color: #8fb8e8; background: rgba(74, 125, 255, 0.12); border: 1px solid rgba(74, 125, 255, 0.35); border-radius: 4px; padding: 1px 6px; }

/* P0-2：右侧「📦 场景素材组」折叠（候选池，非单镜默认注入；徽标=有图/无图） */
.scenemat-head { display: flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; }
.scenemat-head:hover { color: #e8e8ee; }
.scenemat-count { font-size: 10px; color: #e0d0a0; background: rgba(220, 170, 60, 0.14); border: 1px solid rgba(220, 170, 60, 0.35); border-radius: 4px; padding: 1px 6px; }
.scenemat-grid { margin: 2px 0 10px; }
.h3-shot { display: flex; flex-direction: column; gap: 5px; background: #10182a; border: 1px solid #22324f; border-radius: 6px; padding: 8px 10px; margin: 2px 0 10px; }
.h3-shot-row { display: flex; align-items: flex-start; gap: 8px; font-size: 12px; line-height: 1.55; }
.h3-shot-k { flex: 0 0 auto; color: #7fa6e8; font-size: 11px; min-width: 52px; padding-top: 1px; }
.h3-shot-v { color: #c4d2e8; word-break: break-word; flex: 1; min-width: 0; }
.h3-shot-badge { font-size: 10px; border-radius: 4px; padding: 1px 7px; white-space: nowrap; }
.h3-shot-badge.b-user { color: #ffe0a0; background: rgba(255, 224, 160, 0.1); border: 1px solid rgba(255, 224, 160, 0.35); }
.h3-shot-badge.b-ai { color: #9ad1ff; background: rgba(90, 160, 255, 0.1); border: 1px solid rgba(90, 160, 255, 0.35); }
.h3-shot-badge.b-rule { color: #8fe0a8; background: rgba(74, 220, 120, 0.1); border: 1px solid rgba(74, 220, 120, 0.35); }
.h3-shot-refs { display: flex; flex-wrap: wrap; gap: 6px; padding-left: 60px; }
.h3-shot-ref { width: 34px; height: 34px; object-fit: cover; border-radius: 5px; border: 1px solid #2a3a5c; background: #0a0f1a; }

/* Shot 信息卡内「🧬 H3 三层」details（只读快照；较弱视觉，避免与编辑区重复） */
.sd-h3 { border: 1px solid #1e2a44; border-radius: 6px; background: #10182a; padding: 4px 8px; margin-top: 6px; }
.sd-h3 summary { font-size: 12px; color: #8fb8e8; cursor: pointer; user-select: none; }
.sd-h3-count { font-size: 10px; color: #8fb8e8; margin-left: 6px; }
.sd-h3-row { display: flex; align-items: flex-start; gap: 8px; font-size: 12px; line-height: 1.5; margin-top: 4px; }
.sd-h3-k { flex: 0 0 auto; color: #7fa6e8; font-size: 11px; min-width: 48px; }
.sd-h3-v { color: #c4d2e8; word-break: break-word; flex: 1; min-width: 0; max-height: 72px; overflow-y: auto; }
.sd-h3-badge { font-size: 10px; border-radius: 4px; padding: 0 6px; white-space: nowrap; margin-top: 2px; align-self: flex-end; }
.sd-h3-badge.u { color: #ffe0a0; background: rgba(255, 224, 160, 0.1); border: 1px solid rgba(255, 224, 160, 0.35); }
.sd-h3-badge.ai { color: #9ad1ff; background: rgba(90, 160, 255, 0.1); border: 1px solid rgba(90, 160, 255, 0.35); }
.sd-h3-badge.rule { color: #8fe0a8; background: rgba(74, 220, 120, 0.1); border: 1px solid rgba(74, 220, 120, 0.35); }
.sd-h3-refs { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 5px; }
.sd-h3-ref { width: 30px; height: 30px; object-fit: cover; border-radius: 4px; border: 1px solid #2a3a5c; background: #0a0f1a; }
.ref-slot { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.ref-chip { display: flex; align-items: center; gap: 4px; background: #26263a; padding: 3px 8px; border-radius: 4px; font-size: 12px; }
.ref-thumb { width: 22px; height: 22px; object-fit: cover; border-radius: 3px; background: #0a0a10; border: 1px solid #2a2a40; }
.ref-video-thumb { width: 34px; height: 22px; object-fit: cover; border-radius: 3px; background: #0a0a10; border: 1px solid #2a2a40; }
.ref-audio-icon { width: 22px; height: 22px; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.ref-name { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ref-empty { color: #666; font-size: 12px; }
.ref-group { border: 1px dashed #2a2a40; border-radius: 6px; padding: 6px; margin-bottom: 6px; }
.ref-group-title { font-size: 11px; color: #8a8a9a; margin-bottom: 4px; }
.wb-foot { border-top: 1px solid #26263a; padding: 6px 16px; }
.task-center { display: flex; align-items: center; gap: 12px; font-size: 13px; }
.task-spin { color: #4a7dff; }
.task-phase { color: #9aa; }
.task-bar { flex: 1; max-width: 400px; height: 6px; background: #26263a; border-radius: 3px; overflow: hidden; }
.task-bar-fill { height: 100%; background: #4a7dff; }
.task-id { color: #666; font-size: 12px; }
.task-err { color: #ff6b6b; }

/* 项目选择页 */
.home { display: flex; flex-direction: column; height: 100vh; }
.home-head { display: flex; align-items: center; gap: 16px; padding: 12px 20px; background: #181824; border-bottom: 1px solid #26263a; }
.home-conn { font-size: 12px; color: #8a8a9a; display: flex; align-items: center; gap: 6px; margin-left: auto; }
.home-body { flex: 1; overflow-y: auto; padding: 28px 32px; max-width: 680px; margin: 0 auto; width: 100%; box-sizing: border-box; }
.home-title { font-size: 18px; margin: 0 0 14px; color: #c8c8d8; }
.proj-list { display: flex; flex-direction: column; gap: 10px; margin-bottom: 22px; }
.proj-card { display: flex; align-items: center; gap: 12px; padding: 14px 16px; background: #181824; border: 1px solid #26263a; border-radius: 10px; cursor: pointer; }
.proj-card:hover { border-color: #4a7dff; background: #1c1c2c; }
.del-btn { background: transparent; border: none; color: #666; cursor: pointer; font-size: 14px; padding: 2px 6px; border-radius: 4px; line-height: 1; }
.del-btn:hover { color: #ff6b6b; background: rgba(255, 107, 107, 0.12); }
.proj-ico { font-size: 22px; }
.proj-meta { flex: 1; }
.proj-name { font-weight: 600; margin-bottom: 2px; }
.proj-rename { display: flex; align-items: center; gap: 6px; }
.proj-name .rename-btn { display: inline-flex; }
.proj-sub { font-size: 12px; color: #8a8a9a; }
.proj-arrow { color: #666; font-size: 20px; }
.home-empty { color: #666; padding: 12px 0 20px; font-size: 14px; }
.home-actions { display: flex; gap: 12px; margin: 6px 0 20px; }
.snap-block { margin: 10px 0 18px; }
.snap-title { font-size: 14px; color: #8a8a9a; margin: 0 0 10px; }
.snap-card { position: relative; padding: 12px 16px; background: #161625; border: 1px dashed #3a3a55; border-radius: 8px; cursor: pointer; margin-bottom: 8px; }
.snap-card:hover { border-color: #4a7dff; }
.snap-card .del-btn { position: absolute; top: 10px; right: 12px; }
.snap-name { font-weight: 600; font-size: 14px; padding-right: 24px; }
.snap-sub { font-size: 12px; color: #8a8a9a; margin-top: 2px; }
.home-err { color: #ff6b6b; font-size: 13px; margin: 10px 0; }
.dev-tools { margin-top: 28px; border-top: 1px solid #26263a; padding-top: 14px; }
.dev-tools summary { color: #666; font-size: 12px; cursor: pointer; user-select: none; }
.dev-row { display: flex; gap: 10px; margin-top: 10px; }

/* ─────────── V1.2 场景/镜头编辑面板 ─────────── */
.wb-right-sub { font-size: 11px; color: #6a6a7a; text-transform: uppercase; letter-spacing: 0.4px; margin: 14px 0 8px; border-top: 1px solid #26263a; padding-top: 10px; }
.fld-row { display: flex; gap: 8px; }
.fld-row .fld { flex: 1; }
.def-row { display: flex; gap: 6px; }
.def-row select { flex: 1; }
.wb-right-empty { color: #666; font-size: 13px; text-align: center; padding: 30px 10px; line-height: 1.8; }
.wb-right-empty div:first-child { font-size: 18px; margin-bottom: 10px; }

/* 场景素材组 */
.asset-group { margin-bottom: 8px; }
.asset-group-head { display: flex; align-items: center; justify-content: space-between; font-size: 12px; color: #9aa; padding: 2px 0; }
.asset-list { display: flex; flex-wrap: wrap; gap: 4px; }
.asset-chip { display: flex; align-items: center; gap: 4px; background: #26263a; padding: 3px 8px; border-radius: 4px; font-size: 12px; }
.asset-chip .icon-btn { font-size: 10px; }
.asset-empty { color: #555; font-size: 12px; padding: 2px 0; }
/* V1.5-P0-A 素材库：两级切换 + 资产卡片网格 */
.lib-scope { display: flex; gap: 6px; margin-bottom: 8px; }
.lib-scope-btn { flex: 1; background: #1a1a2a; color: #8a8a9a; border: 1px solid #2a2a40; border-radius: 6px; padding: 5px 0; font-size: 12px; cursor: pointer; }
.lib-scope-btn.on { background: rgba(74, 125, 255, 0.16); color: #cfe0ff; border-color: #4a7dff; }
.lib-msg { font-size: 11px; color: #ffcf7a; margin-bottom: 8px; line-height: 1.5; }
.lib-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.lib-card { background: #1d1d2e; border: 1px solid #2a2a40; border-radius: 6px; padding: 6px; font-size: 12px; }
.lib-card.editing { border-color: #4a7dff; background: #16223a; }
.lib-card-top { display: flex; align-items: center; gap: 5px; }
.lib-card-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #e8e8ee; }
.lib-card-src { font-size: 11px; opacity: 0.8; }
.lib-card-desc { margin-top: 4px; font-size: 11px; color: #8a8a9a; line-height: 1.4; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.lib-card-aliases { margin-top: 3px; font-size: 10px; color: #6a7; }
.lib-card-ops { display: flex; gap: 2px; margin-top: 4px; }
.lib-card-ops .icon-btn { font-size: 11px; }
.lib-edit { display: flex; flex-direction: column; gap: 6px; }
.lib-edit-actions { display: flex; gap: 6px; justify-content: flex-end; }
.btn.small { padding: 4px 12px; font-size: 12px; }
.btn.small.ghost { background: transparent; border: 1px solid #3a3a55; color: #c8c8d8; }
.btn.small.ghost:hover { border-color: #4a7dff; color: #cfe0ff; }

/* @ 资产识别高亮（textarea 透明文字 + 下层 mark 渲染）
   prompt-box 提供深色底，textarea 透明露出下层高亮层 */
.prompt-box { position: relative; font-family: system-ui, sans-serif; background: #181824; border: 1px solid #2a2a40; border-radius: 6px; }
.prompt-box:focus-within { border-color: #4a7dff; }
.prompt-layer, .prompt-input { font: inherit; font-size: 13px; line-height: 1.6; padding: 8px; border-radius: 6px; border: none; box-sizing: border-box; width: 100%; white-space: pre-wrap; word-break: break-word; }
.prompt-layer { position: absolute; inset: 0; color: #e8e8ee; pointer-events: none; overflow: hidden; }
.prompt-input { position: relative; background: transparent !important; color: transparent !important; -webkit-text-fill-color: transparent !important; caret-color: #e8e8ee; resize: vertical; overflow: auto; scrollbar-width: none; }
.prompt-input::-webkit-scrollbar { display: none; }
.prompt-input:focus { outline: none; }

/* 命中资产 chips */
.mention-bar { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.mention-chip { font-size: 12px; padding: 2px 8px; border-radius: 10px; cursor: pointer; }
.mention-chip:hover { filter: brightness(1.25); }
.mention-chip.m-cast { background: rgba(74, 125, 255, 0.25); color: #cfe0ff; }
.mention-chip.m-loc { background: rgba(62, 207, 110, 0.22); color: #c9f2d8; }
.mention-chip.m-prop { background: rgba(255, 176, 74, 0.22); color: #ffe3c0; }
.mention-chip.m-style { background: rgba(200, 130, 255, 0.22); color: #ecd9ff; }

/* V1.2.9 媒体标签 + @ 资产 chip：ghost + visual 双层架构（导演台风格，同 minimax_prompt_mentions.js）
   - .media-chip-ghost   = 隐形原文（透明色，撑出与 textarea 完全一致的宽度与断行 → 光标严格对齐）
   - .media-chip-visual  = 绝对定位覆盖层（缩略图 + label），不占布局宽度
   背景/边框/圆角全部放在 visual 上，ghost 零修饰保证宽度 == 原文宽度。
   不设 nowrap：ghost 按 textarea 同规则断行（pre-wrap + break-word），光标才不会漂。
   注意：.prompt-layer 内容经 v-html 注入无 scoped data 属性，必须用 :deep() 前缀才生效 */
.prompt-layer :deep(.media-chip) {
  position: relative;
  display: inline;
}
.prompt-layer :deep(.media-chip .media-chip-ghost) {
  color: transparent;
  background: transparent;
  border: none;
  padding: 0;
  margin: 0;
}
.prompt-layer :deep(.media-chip .media-chip-visual) {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  gap: 2px;
  white-space: nowrap;
  background: rgba(74, 125, 255, 0.14);
  border: 1px solid rgba(74, 125, 255, 0.4);
  border-radius: 4px;
  padding: 0 3px;
  font-weight: 600;
  pointer-events: none;
}
.prompt-layer :deep(.media-chip .media-chip-thumb) {
  display: inline-block;
  width: 13px;
  height: 13px;
  object-fit: cover;
  border-radius: 3px;
  flex: none;
}
.prompt-layer :deep(.media-chip .media-chip-icon) {
  display: inline-block;
  width: 13px;
  font-size: 10px;
  line-height: 1;
  text-align: center;
  flex: none;
}
.prompt-layer :deep(.media-chip .media-chip-label) { color: inherit; }
.prompt-layer :deep(.media-chip.mc-video .media-chip-visual) { background: rgba(255, 90, 140, 0.15); border-color: rgba(255, 90, 140, 0.4); }
.prompt-layer :deep(.media-chip.mc-audio .media-chip-visual) { background: rgba(62, 207, 110, 0.15); border-color: rgba(62, 207, 110, 0.4); }
.prompt-layer :deep(.media-chip.mc-asset.m-cast .media-chip-visual) { background: rgba(74, 125, 255, 0.15); border-color: rgba(74, 125, 255, 0.4); }
.prompt-layer :deep(.media-chip.mc-asset.m-loc .media-chip-visual) { background: rgba(62, 207, 110, 0.15); border-color: rgba(62, 207, 110, 0.4); }
.prompt-layer :deep(.media-chip.mc-asset.m-prop .media-chip-visual) { background: rgba(255, 176, 74, 0.15); border-color: rgba(255, 176, 74, 0.4); }
.prompt-layer :deep(.media-chip.mc-asset.m-style .media-chip-visual) { background: rgba(200, 130, 255, 0.15); border-color: rgba(200, 130, 255, 0.4); }
.prompt-layer :deep(.media-chip.mc-picture .media-chip-visual) { color: #cfe0ff; }

/* V1.2.7 @ 自动补全下拉 */
.tag-complete {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  z-index: 40;
  max-height: 260px;
  overflow-y: auto;
  background: #1c1c2e;
  border: 1px solid #2a2a40;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  padding: 6px;
}
.tag-group-label { font-size: 11px; color: #8b8ba3; padding: 6px 8px 3px; }
.tag-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #e8e8ee;
  text-align: left;
  cursor: pointer;
  font-size: 13px;
}
.tag-item.sel { background: rgba(74, 125, 255, 0.22); }
.tag-thumb {
  width: 26px;
  height: 26px;
  border-radius: 4px;
  object-fit: cover;
  background: #26263c;
  flex: none;
}
.tag-thumb-ph { display: flex; align-items: center; justify-content: center; font-size: 14px; }
.tag-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag-src { font-size: 11px; border-radius: 4px; padding: 1px 5px; flex: none; }
.ts-scene { background: rgba(62, 207, 110, 0.18); color: #b7ecc8; }
.ts-global { background: rgba(74, 125, 255, 0.18); color: #bcd3ff; }
.ts-ref { background: rgba(200, 130, 255, 0.18); color: #e5cfff; }
.tag-empty { padding: 10px 8px; font-size: 12px; color: #8b8ba3; }

/* 继承徽标 */
.inherit-line { display: flex; align-items: center; gap: 6px; margin-top: 4px; font-size: 12px; }
.inherit-asset { color: #9aa; }
.inherit-badge { padding: 1px 6px; border-radius: 8px; font-size: 11px; }
.b-shot { background: rgba(255, 107, 107, 0.15); color: #ff9a9a; }
.b-scene { background: rgba(74, 125, 255, 0.15); color: #9ab8ff; }
.b-project { background: rgba(62, 207, 110, 0.15); color: #7fe0a0; }

/* V1.5-P1 生效资产四类区块 */
.inherit-grid { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.inherit-row { display: flex; align-items: center; gap: 6px; font-size: 12px; min-height: 22px; }
.inherit-kind { flex: none; width: 28px; color: #6a6a7a; }
.inherit-thumb { flex: none; width: 20px; height: 20px; border-radius: 4px; overflow: hidden; display: inline-flex; align-items: center; justify-content: center; background: #26263c; }
.inherit-thumb img { width: 100%; height: 100%; object-fit: cover; }
.inherit-empty { font-size: 12px; color: #6a6a7a; padding: 4px 0; }
.b-scene-mat { background: rgba(62, 207, 110, 0.15); color: #7fe0a0; }
.b-global-mat { background: rgba(74, 125, 255, 0.15); color: #9ab8ff; }

/* Phase 2-D（#576）：镜头对白行级微调 */
.dialogue-head { cursor: pointer; user-select: none; display: flex; align-items: center; gap: 8px; }
.dialogue-head:hover { color: #9ab8ff; }
.dialogue-head.dialogue-has { color: #cfe0ff; }
.dialogue-none { color: #6a6a7a; font-size: 11px; text-transform: none; letter-spacing: 0; }
.dialogue-list { display: flex; flex-direction: column; gap: 8px; margin-top: 2px; }
.dialogue-row { background: #181824; border: 1px solid #2a2a40; border-radius: 8px; padding: 8px; }
.dialogue-row-head { display: flex; align-items: center; gap: 6px; font-size: 12px; margin-bottom: 4px; }
.dialogue-idx { flex: none; width: 18px; height: 18px; border-radius: 50%; background: #2a2a40; color: #9ab8ff; display: inline-flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; }
.dialogue-speaker { flex: none; max-width: 96px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #dfe7ff; font-weight: 600; }
.dialogue-type { flex: none; font-size: 10px; color: #7fe0a0; border: 1px solid rgba(62, 207, 110, 0.35); border-radius: 4px; padding: 0 4px; }
.dialogue-resolved { flex: 1; min-width: 0; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; color: rgba(255, 255, 255, 0.45); }
.dialogue-text { font-size: 12px; color: #cfcfe0; line-height: 1.5; margin-bottom: 6px; max-height: 3.6em; overflow: hidden; }
.dialogue-controls { display: flex; flex-wrap: wrap; gap: 6px; }
.dialogue-ctl { display: flex; flex-direction: column; gap: 2px; flex: 1 1 40%; min-width: 0; }
.dialogue-ctl > span { font-size: 10px; color: #6a6a7a; }
.dialogue-ctl select.vcast-select,
.dialogue-input {
  width: 100%;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  color: #e8e8ee;
  font-size: 12px;
  padding: 3px 6px;
}
.dialogue-ctl select.vcast-select:hover,
.dialogue-input:hover { border-color: rgba(122, 162, 247, 0.5); }
.dialogue-input::placeholder { color: rgba(255, 255, 255, 0.3); }
.dialogue-hint { font-size: 11px; color: rgba(255, 255, 255, 0.4); line-height: 1.5; border-top: 1px dashed rgba(255, 255, 255, 0.1); padding-top: 6px; }
.dialogue-empty { font-size: 12px; color: #6a6a7a; line-height: 1.6; padding: 6px 2px; }

/* V1.6-B 人物 chip 集合（[名 ×] + 添加人物 popover） */
.cast-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  background: #181824;
  border: 1px solid #2a2a40;
  border-radius: 6px;
  padding: 5px 6px;
  min-height: 30px;
}
.cast-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: #26263c;
  border: 1px solid #3a3a58;
  border-radius: 14px;
  padding: 2px 7px 2px 3px;
  font-size: 12px;
  color: #cfcfe0;
  line-height: 1;
}
.cast-chip-thumb {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #33334d;
}
.cast-chip-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cast-chip-name {
  max-width: 90px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cast-chip-x {
  background: transparent;
  border: none;
  color: #8a8a9a;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  padding: 0 1px;
  border-radius: 50%;
}
.cast-chip-x:hover {
  color: #ff8a8a;
  background: #3a2b2b;
}
.cast-auto {
  font-size: 12px;
  color: #6a6a7a;
}
.cast-add-wrap {
  position: relative;
  display: inline-flex;
}
.cast-add-btn {
  background: transparent;
  border: 1px dashed #3a3a58;
  color: #8a8a9a;
  border-radius: 14px;
  padding: 2px 9px;
  font-size: 12px;
  cursor: pointer;
  line-height: 1.4;
}
.cast-add-btn:hover {
  color: #cfcfe0;
  border-color: #4a4a6a;
  background: #22223a;
}
.cast-picker {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 30;
  min-width: 180px;
  max-width: 240px;
  max-height: 220px;
  overflow-y: auto;
  background: #1c1c2b;
  border: 1px solid #33334d;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cast-picker-empty {
  font-size: 12px;
  color: #6a6a7a;
  padding: 6px 8px;
}
.cast-picker-item {
  display: flex;
  align-items: center;
  gap: 7px;
  background: transparent;
  border: none;
  border-radius: 6px;
  padding: 4px 6px;
  font-size: 12px;
  color: #cfcfe0;
  cursor: pointer;
  text-align: left;
}
.cast-picker-item:hover {
  background: #26263c;
  color: #fff;
}
.cast-picker-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}


/* V1.2.8 Prompt 放大编辑弹窗（屏幕居中，方便查看缩略图） */
.prompt-modal-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(0, 0, 0, 0.68);
  display: flex;
  align-items: center;
  justify-content: center;
}
.prompt-modal {
  width: min(94vw, 920px);
  max-height: 88vh;
  background: #161622;
  border: 1px solid #3a3a55;
  border-radius: 12px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.55);
  display: flex;
  flex-direction: column;
  /* V1.2.9: overflow:visible 让 @ 补全下拉在弹窗内正常展开（不被弹窗边界裁剪） */
  overflow: visible;
}
.prompt-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid #2a2a40;
  flex: none;
}
.prompt-modal-title { font-size: 13px; color: #c8c8d8; font-weight: 600; }
.prompt-modal-close { font-size: 12px; color: #9ab8ff; background: rgba(74, 125, 255, 0.12); border: 1px solid rgba(74, 125, 255, 0.35); border-radius: 6px; padding: 3px 10px; cursor: pointer; }
.prompt-modal-close:hover { background: rgba(74, 125, 255, 0.22); color: #cfe0ff; }
.prompt-modal-box {
  flex: 1;
  margin: 12px;
  min-height: 320px;
}
.prompt-modal-box .prompt-input { font-size: 15px; line-height: 1.6; }
.prompt-modal-box .prompt-layer { font-size: 15px; line-height: 1.6; }
.prompt-modal-box :deep(.media-chip .media-chip-thumb) { width: 20px; height: 20px; }
.prompt-modal-box :deep(.media-chip .media-chip-icon) { width: 20px; font-size: 14px; }
.prompt-modal .mention-bar { padding: 0 12px 10px; flex: none; }

/* V1.4-P0-3 「← 项目」退出保护确认框（三选一） */
.leave-modal-mask {
  position: fixed;
  inset: 0;
  z-index: 3100;
  background: rgba(0, 0, 0, 0.68);
  display: flex;
  align-items: center;
  justify-content: center;
}
.leave-modal {
  width: min(90vw, 420px);
  background: #161622;
  border: 1px solid #3a3a55;
  border-radius: 12px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.55);
  padding: 18px 20px 16px;
}
.leave-modal-title { font-size: 14px; color: #ffcf7a; font-weight: 700; margin-bottom: 10px; }
.leave-modal-body { font-size: 13px; color: #c8c8d8; line-height: 1.6; margin-bottom: 16px; }
.leave-modal-actions { display: flex; gap: 8px; justify-content: flex-end; }
.leave-modal-actions .btn { font-size: 13px; padding: 6px 14px; border-radius: 6px; cursor: pointer; border: 1px solid transparent; }
.leave-save { background: rgba(74, 125, 255, 0.18); color: #9ab8ff; border-color: rgba(74, 125, 255, 0.4) !important; }
.leave-save:hover { background: rgba(74, 125, 255, 0.3); color: #cfe0ff; }
.leave-save:disabled { opacity: 0.55; cursor: default; }
.leave-discard { background: rgba(255, 107, 107, 0.12); color: #ff9a9a; border-color: rgba(255, 107, 107, 0.35) !important; }
.leave-discard:hover { background: rgba(255, 107, 107, 0.22); color: #ffc0c0; }
.leave-cancel { background: #22223a; color: #a0a0b8; border-color: #33334d !important; }
.leave-cancel:hover { background: #2a2a44; color: #d0d0e4; }

/* V1.6-A 成片导出弹窗 */
.export-modal-mask {
  position: fixed;
  inset: 0;
  z-index: 3200;
  background: rgba(0, 0, 0, 0.68);
  display: flex;
  align-items: center;
  justify-content: center;
}
.export-modal {
  width: min(92vw, 520px);
  background: #161622;
  border: 1px solid #3a3a55;
  border-radius: 12px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.55);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.export-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.export-modal-title { font-size: 15px; color: #e8e8ee; font-weight: 700; }
.export-modal-close {
  background: transparent;
  border: none;
  color: #8a8aa0;
  font-size: 14px;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 6px;
}
.export-modal-close:hover { color: #ff9a9a; background: rgba(255, 107, 107, 0.12); }
.export-scope {
  display: flex;
  gap: 8px;
}
.export-scope-pill {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid #2a2a40;
  border-radius: 8px;
  cursor: pointer;
  background: #12121e;
  color: #c8c8d8;
  font-size: 13px;
}
.export-scope-pill.active {
  border-color: #4a7dff;
  background: rgba(74, 125, 255, 0.1);
  color: #e0e8ff;
}
.export-scope-pill:has(input:disabled) { opacity: 0.55; cursor: default; }
.export-scope-pill em { margin-left: auto; font-style: normal; font-size: 12px; color: #8a8aa0; }
.export-ok-title { color: #6ecf8a; font-size: 14px; font-weight: 600; }
.export-ok-sub { color: #9a9ab0; font-size: 13px; margin-top: 6px; }
.export-warn-title { color: #ffcf7a; font-size: 14px; font-weight: 600; margin-bottom: 8px; }
.export-warn-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 180px;
  overflow-y: auto;
  background: #12121e;
  border: 1px solid #2a2a40;
  border-radius: 8px;
  padding: 8px 10px;
}
.export-warn-item {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #c8c8d8;
  font-size: 12.5px;
}
.ew-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.ew-missing { background: #ff9f43; }
.ew-stale { background: #ffcf7a; }
.export-modal-foot {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.export-modal-foot .btn {
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
}
.export-go { background: #4a7dff; color: #fff; }
.export-go:hover { background: #3a6cf0; }
.export-go:disabled { opacity: 0.5; cursor: default; }
.export-warn-go {
  background: rgba(255, 207, 122, 0.14);
  color: #ffcf7a;
  border-color: rgba(255, 207, 122, 0.35) !important;
}
.export-warn-go:hover { background: rgba(255, 207, 122, 0.24); color: #ffe0a8; }
.export-warn-go:disabled { opacity: 0.5; cursor: default; }

/* V1.7 Commit 3：剧本导入弹窗 */
.script-modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}
.script-modal {
  width: min(760px, 94vw);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: #161626;
  border: 1px solid #2a2a40;
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  color: #c8c8d8;
  font-size: 13px;
}
.script-modal-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid #26263a;
}
.script-modal-title { font-size: 14px; font-weight: 600; color: #e0e8ff; }
.script-modal-close {
  margin-left: auto;
  background: none;
  border: none;
  color: #8a8a9a;
  cursor: pointer;
  font-size: 13px;
  padding: 4px 8px;
  border-radius: 6px;
}
.script-modal-close:hover { background: #22223a; color: #fff; }
/* P1-B-5（#534）：导入弹窗页签（📜 剧本 / 📖 小说章节） */
.script-modal-tabs {
  display: flex;
  gap: 4px;
  margin-left: 4px;
  background: #10101c;
  border: 1px solid #2a2a40;
  border-radius: 8px;
  padding: 3px;
}
.script-modal-tab {
  background: none;
  border: none;
  color: #8a8a9a;
  cursor: pointer;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  transition: background 0.15s, color 0.15s;
}
.script-modal-tab:hover { color: #e0e8ff; }
.script-modal-tab.active { background: #2a3a6a; color: #fff; }
/* P2-P5（#542）：小说导入目标项目选择 + Bible 上下文预览 + Bible 变更列表 */
.story-target-zone {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(122, 162, 247, 0.07);
  border: 1px solid rgba(122, 162, 247, 0.22);
}
.story-target-label {
  font-size: 12.5px;
  font-weight: 700;
  color: #cfe0ff;
}
.story-target-options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.story-target-opt {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #c8c8d8;
  font-size: 12.5px;
  cursor: pointer;
  user-select: none;
}
.story-target-opt input { accent-color: #4a7dff; }
.story-target-opt.active {
  background: rgba(122, 162, 247, 0.2);
  border-color: #4a7dff;
  color: #fff;
}
.story-target-detail {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: #9ab8ff;
}
.story-ep-input {
  width: 64px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  color: #fff;
  padding: 3px 6px;
  font-size: 12.5px;
  text-align: center;
}
.story-target-hint { color: #8a8a9a; font-size: 12px; }
.story-bible-context {
  margin-top: 2px;
}
.story-bible-context-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 10px;
}
.story-bible-context-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12px;
}
.story-bible-context-name { color: #9fe8a6; white-space: nowrap; }
.story-bible-context-status { color: #c8c8d8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.story-bible-context-empty {
  color: #8a8a9a;
  font-size: 12px;
  padding: 4px 10px 6px;
}
.story-bible-updates {
  margin-top: 12px;
  border: 1px solid rgba(122, 162, 247, 0.25);
  background: rgba(122, 162, 247, 0.06);
  border-radius: 8px;
}
.story-bible-update-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  font-size: 12.5px;
  border-top: 1px dashed rgba(255, 255, 255, 0.08);
}
.story-bible-update-row:first-of-type { border-top: none; }
.story-bible-update-kind {
  border-radius: 4px;
  padding: 1px 7px;
  font-size: 11px;
  white-space: nowrap;
}
.story-bible-update-kind.k-new_candidate { background: rgba(220, 150, 50, 0.16); color: #ffd38a; }
.story-bible-update-kind.k-reused { background: rgba(122, 162, 247, 0.18); color: #a8c4ff; }
.story-bible-update-kind.k-status_change { background: rgba(70, 180, 90, 0.16); color: #9fe8a6; }
.story-bible-update-kind.k-alias_merge { background: rgba(200, 120, 200, 0.16); color: #d8a8ff; }
.story-bible-update-name { color: #e0e8ff; font-weight: 600; white-space: nowrap; }
.story-bible-update-msg { color: #9a9ab0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 📖 小说章节审核预览：地点 / 播放顺序 / Beats 卡片 */
.story-locations,
.story-timeline,
.story-beats {
  margin-top: 12px;
}
.story-location-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
}
.story-location-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
}
.story-location-id {
  color: #7f9bd8;
  font-family: ui-monospace, monospace;
  font-size: 11px;
  min-width: 78px;
}
.story-location-name { color: #e0e8ff; }
.story-location-shots {
  margin-left: auto;
  color: #8a8a9a;
  font-size: 11px;
  white-space: nowrap;
}
.story-timeline-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  max-height: 200px;
  overflow-y: auto;
}
.story-timeline-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
}
.story-timeline-order {
  color: #7f9bd8;
  font-family: ui-monospace, monospace;
  font-size: 11px;
  min-width: 22px;
}
.story-timeline-label {
  color: #c8c8d8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.story-beat-card {
  background: #131322;
  border: 1px solid #26263a;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 8px;
}
.story-beat-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.story-beat-id {
  color: #7f9bd8;
  font-family: ui-monospace, monospace;
  font-size: 11px;
  white-space: nowrap;
}
.story-beat-title { color: #e0e8ff; font-weight: 600; }
.story-beat-fn {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #22223a;
  color: #c8c8d8;
  white-space: nowrap;
}
.story-beat-fn.fn-introduce_character { background: #2a3a2a; color: #a8e6a8; }
.story-beat-fn.fn-dialogue { background: #2a2a3a; color: #b0b8e6; }
.story-beat-fn.fn-plant_clue { background: #3a332a; color: #e6cf9e; }
.story-beat-fn.fn-introduce_threat { background: #3a2a2a; color: #e6a0a0; }
.story-beat-fn.fn-confrontation { background: #3a2a33; color: #e6a8c8; }
.story-beat-fn.fn-action { background: #332a3a; color: #c8a0e6; }
.story-beat-fn.fn-reveal { background: #2a3a36; color: #9ee6cf; }
.story-beat-fn.fn-emotional { background: #3a3430; color: #e6c8a0; }
.story-beat-fn.fn-transition { background: #2a2a30; color: #9aa0b0; }
.story-beat-rule {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #4a3520;
  color: #ffd58a;
  white-space: nowrap;
}
.story-beat-summary { margin-top: 6px; color: #b0b0c0; font-size: 12.5px; }
.story-beat-meta {
  display: flex;
  gap: 12px;
  margin-top: 6px;
  color: #8a8a9a;
  font-size: 11.5px;
  flex-wrap: wrap;
}
.story-beat-transition { margin-top: 6px; color: #7f9bd8; font-size: 11.5px; }
.story-beat-details {
  margin-top: 6px;
  color: #8a8a9a;
  font-size: 12px;
}
.story-beat-details summary { cursor: pointer; color: #9aa0b0; }
.story-beat-seg {
  margin-top: 4px;
  padding: 6px 8px;
  background: #10101c;
  border-left: 2px solid #2a3a6a;
  border-radius: 4px;
  color: #b0b0c0;
  font-size: 12px;
  line-height: 1.6;
}
.script-input-zone {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid #26263a;
}
.script-input {
  width: 100%;
  box-sizing: border-box;
  background: #12121e;
  border: 1px solid #2a2a40;
  border-radius: 8px;
  color: #c8c8d8;
  font-size: 13px;
  padding: 10px 12px;
  resize: vertical;
  min-height: 120px;
  font-family: inherit;
}
.script-input:focus { outline: none; border-color: #4a7dff; }
.script-input-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.script-file-input { display: none; }
.script-file-name { color: #8a8ab0; font-size: 12.5px; }
.script-hint { color: #8a8a9a; font-size: 12px; }
.script-go { background: #4a7dff; color: #fff; }
.script-go:hover { background: #3a6cf0; }
.script-go:disabled { opacity: 0.5; cursor: default; }
.script-err { color: #ff6b6b; font-size: 13px; padding: 10px 16px; }
.script-preview {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}
.script-summary { display: flex; align-items: center; gap: 8px; color: #e0e8ff; font-weight: 600; }
.sum-sep { color: #55556a; font-weight: 400; }
.script-rule-only { color: #ffcf7a; font-weight: 400; font-size: 12px; }
.script-warnings { display: flex; flex-direction: column; gap: 4px; }
.script-warning { color: #ffcf7a; font-size: 12.5px; }
.script-plan { display: flex; flex-direction: column; gap: 10px; }
.script-scene {
  background: #12121e;
  border: 1px solid #2a2a40;
  border-radius: 8px;
  padding: 10px 12px;
}
.script-scene-title {
  font-weight: 600;
  color: #c8c8d8;
  font-size: 13px;
  margin-bottom: 4px;
}
.script-scene-title em { font-style: normal; color: #8a8ab0; font-weight: 400; }
.script-scene-sub { margin-left: 8px; color: #8a8a9a; font-size: 12px; font-weight: 400; }
.script-shot {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 10px;
  padding: 6px 0 0;
  border-top: 1px dashed #26263a;
  margin-top: 6px;
}
.script-shot-id { color: #4a7dff; font-size: 12px; font-family: ui-monospace, monospace; }
.script-shot-text { color: #c8c8d8; flex: 1; min-width: 200px; }
.script-shot-cast { color: #9ad1ff; font-size: 12px; white-space: nowrap; }
.script-shot-viz { color: #8a8ab0; font-size: 12px; font-style: italic; width: 100%; }

/* V1.7 Phase 2：剧本导入资产匹配区 */
.script-assets {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #12121e;
  border: 1px solid #26263a;
  border-radius: 8px;
  padding: 10px 12px;
}
.script-assets-head { display: flex; align-items: center; gap: 8px; }
.script-assets-title { font-weight: 600; color: #e0e8ff; font-size: 13px; }
.script-assets-stat { margin-left: auto; color: #8a8ab0; font-size: 12px; }
.script-assets-tip { color: #8a8a9a; font-size: 12px; }
/* V1.7 Phase 1.1：分组展示（角色/地点/道具） */
.script-assets-groups { display: flex; flex-direction: column; gap: 8px; }
.script-assets-group { display: flex; flex-direction: column; gap: 4px; }
.script-assets-group-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #aab0d0;
  font-size: 12px;
  font-weight: 600;
  padding: 0 2px;
}
.script-assets-group-count {
  font-size: 11px;
  font-weight: 400;
  color: #8a8ab0;
  background: #1c1c30;
  border: 1px solid #2a2a40;
  border-radius: 10px;
  padding: 0 7px;
  line-height: 16px;
}
.script-asset-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  background: #161626;
  border-radius: 6px;
}
.script-asset-space { margin-left: auto; }
.script-asset-name { color: #c8c8d8; font-weight: 500; min-width: 60px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* V1.7 实体抽取加固（#357）：实体类型 chip + 置信度 + 门控提示 */
.script-asset-type {
  font-size: 11px;
  color: #c9a6ff;
  border: 1px solid #4a3a6a;
  background: rgba(150, 106, 255, 0.1);
  border-radius: 4px;
  padding: 1px 5px;
  white-space: nowrap;
}
.script-asset-type.etype-character { color: #9ad1ff; border-color: #2a4a6a; background: rgba(90, 160, 255, 0.08); }
.script-asset-type.etype-location { color: #7ee0c0; border-color: #1f5a48; background: rgba(80, 210, 170, 0.08); }
.script-asset-type.etype-costume { color: #ffc98a; border-color: #6a4a1f; background: rgba(255, 190, 110, 0.08); }
.script-asset-type.etype-effect { color: #ff9ad1; border-color: #6a2a4a; background: rgba(255, 130, 200, 0.08); }
.script-asset-type.etype-environment,
.script-asset-type.etype-architecture { color: #a8d8a0; border-color: #2a5a2a; background: rgba(120, 200, 110, 0.08); }
.script-asset-conf { color: #9a9ab0; font-size: 11px; white-space: nowrap; }
.script-asset-gate { color: #ffc98a; font-size: 11px; white-space: nowrap; }
.script-asset-thumb {
  width: 34px;
  height: 34px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid #2a2a40;
  background: #0e0e18;
}
.script-asset-badge { font-size: 11px; padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
.script-asset-badge.auto { color: #7ee0a3; background: rgba(126, 224, 163, 0.12); }
.script-asset-badge.suggest { color: #ffcf7a; background: rgba(255, 207, 122, 0.14); }
/* Phase 2-1（#135）：重新导入复用了上次落盘的人工确认（persisted）。 */
.script-asset-badge.persisted { color: #8ab8ff; background: rgba(138, 184, 255, 0.14); }
.script-asset-badge-sub { margin-left: 4px; opacity: 0.75; }
.script-asset-suggest {
  color: #ffcf7a;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 240px;
}
.script-asset-btn {
  font-size: 12px;
  border: 1px solid #3a3a56;
  border-radius: 5px;
  padding: 2px 10px;
  cursor: pointer;
  color: #c8c8d8;
  background: #1c1c30;
  white-space: nowrap;
}
.script-asset-btn:hover { background: #26263e; }
.script-asset-btn.accept {
  color: #7ee0a3;
  border-color: #2a6a4a;
  background: rgba(80, 210, 170, 0.1);
}
.script-asset-btn.accept:hover { background: rgba(80, 210, 170, 0.2); }
.script-asset-btn.rechoose {
  color: #c9a6ff;
  border-color: #4a3a6a;
  background: rgba(150, 106, 255, 0.1);
}
.script-asset-btn.rechoose:hover { background: rgba(150, 106, 255, 0.2); }
.script-asset-select {
  background: #12121e;
  color: #c8c8d8;
  border: 1px solid #2a2a40;
  border-radius: 6px;
  font-size: 12px;
  padding: 3px 6px;
  max-width: 210px;
}
.script-asset-none { color: #8a8a9a; font-size: 12px; }

/* V1.7 Phase 1.1：视觉元素（Task B，只进 Prompt，无需资产匹配） */
.script-assets-visual {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-top: 1px dashed #26263a;
  padding-top: 6px;
}
.script-assets-visual-title { color: #aab0d0; font-size: 12px; font-weight: 600; }
.script-assets-visual-title em { font-style: normal; font-weight: 400; color: #8a8ab0; font-size: 11px; }
.script-assets-visual-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.script-assets-visual-chip {
  font-size: 11px;
  color: #a8d8a0;
  border: 1px solid #2a5a2a;
  background: rgba(120, 200, 110, 0.08);
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
}
.script-assets-visual-chip em { font-style: normal; color: #8a9a8a; margin-left: 4px; }

/* V1.7 Phase 3（P0-C）：AI Draft 五区草稿审核预览 */
.script-draft {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #12121e;
  border: 1px solid #26263a;
  border-radius: 8px;
  padding: 10px 12px;
}
.script-draft-head { display: flex; align-items: center; gap: 8px; }
.script-draft-title { font-weight: 600; color: #e0e8ff; font-size: 13px; }
.script-draft-stat { margin-left: auto; color: #8a8ab0; font-size: 12px; }
.script-draft-tip { color: #8a8a9a; font-size: 12px; }
.script-draft-list { display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow-y: auto; }
.script-draft-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
  background: #161626;
  border-radius: 6px;
}
.script-draft-shot { display: flex; align-items: center; gap: 8px; }
.script-draft-id { color: #9ad1ff; font-weight: 600; font-size: 12px; }
.script-draft-mode {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
  color: #9ad1ff;
  border: 1px solid #2a4a6a;
}
.script-draft-mode.mode-fl2v { color: #ff9db1; border-color: #6a2a3a; }
.script-draft-mode.mode-r2v { color: #7ee0a3; border-color: #2a6a4a; }
.script-draft-secs { display: flex; flex-direction: column; gap: 3px; }
.script-draft-sec { display: flex; align-items: flex-start; gap: 8px; }
.script-draft-sec-label {
  color: #8a8ab0;
  font-size: 11px;
  min-width: 30px;
  line-height: 1.5;
  flex-shrink: 0;
}
.script-draft-sec-text { color: #c8c8d8; font-size: 12px; line-height: 1.5; }

/* P0-①（#400）：H3 Prompt 三层可追溯（原文 → AI 理解 → H3） */
.script-h3 {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #0f1420;
  border: 1px solid #1e2a44;
  border-radius: 8px;
  padding: 10px 12px;
}
.script-h3-head {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}
.script-h3-title { font-weight: 600; color: #bcd4ff; font-size: 13px; }
.script-h3-stat { margin-left: auto; color: #7f94c0; font-size: 11px; }
.script-h3-stat em { font-style: normal; color: #5f7399; margin-left: 6px; }
.script-h3-toggle { color: #7f94c0; font-size: 11px; white-space: nowrap; }
.script-h3-tip { color: #7f94c0; font-size: 12px; }
.script-h3-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
}
.script-h3-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 8px 10px;
  background: #141b2c;
  border: 1px solid #223052;
  border-radius: 6px;
}
.script-h3-shot { display: flex; align-items: center; gap: 8px; }
.script-h3-id { color: #9ad1ff; font-weight: 600; font-size: 12px; }
.script-h3-dur { color: #7f94c0; font-size: 11px; }
.script-h3-layer { display: flex; align-items: flex-start; gap: 8px; }
.script-h3-layer-label {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
  margin-top: 2px;
  flex-shrink: 0;
}
.script-h3-layer-label.layer-original { color: #ffe0a0; border: 1px solid #7a5a20; background: #1f1a10; }
.script-h3-layer-label.layer-intent { color: #9ad1ff; border: 1px solid #2a4a6a; background: #101a26; }
.script-h3-layer-label.layer-h3 { color: #7ee0a3; border: 1px solid #2a6a4a; background: #101f18; }
.script-h3-layer-text { color: #c8d8f0; font-size: 12px; line-height: 1.55; flex: 1; }
.script-h3-intent { display: flex; flex-wrap: wrap; gap: 4px; flex: 1; }
.script-h3-fact {
  font-size: 11px;
  color: #c8d8f0;
  background: #182334;
  border: 1px solid #28385c;
  border-radius: 4px;
  padding: 1px 7px;
  line-height: 1.6;
}
.script-h3-fact.ai { color: #9ad1ff; border-color: #2a4a6a; }
.script-h3-fact.rule { color: #8fe0a8; border-color: #2a6a4a; }
.script-h3-prompt { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
.script-h3-sec { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px; }
.script-h3-sec-label { color: #6f86b4; font-size: 11px; flex-shrink: 0; }
.script-h3-sec-text {
  color: #b8c8e4;
  font-size: 12px;
  line-height: 1.55;
  flex: 1;
  min-width: 200px;
}
.script-h3-prov { display: inline-flex; gap: 4px; align-items: center; }
.script-h3-prov .prov { font-style: normal; font-size: 10px; padding: 0 5px; border-radius: 3px; }
.script-h3-prov .prov-user { color: #ffe0a0; background: #2a2110; }
.script-h3-prov .prov-ai { color: #9ad1ff; background: #101a26; }
.script-h3-prov .prov-rule { color: #8fe0a8; background: #101f18; }
.script-h3-refs { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding-left: 30px; }
.script-h3-refs-label { color: #7f94c0; font-size: 11px; }
.script-h3-ref {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: #171f31;
  border: 1px solid #2a3a5c;
  border-radius: 5px;
  padding: 2px 7px;
}
.script-h3-ref-thumb { width: 26px; height: 26px; object-fit: cover; border-radius: 3px; }
.script-h3-ref-name { color: #c8d8f0; font-size: 11px; }

.script-modal-foot {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  padding: 12px 16px;
  border-top: 1px solid #26263a;
}
.script-modal-foot .btn {
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
}
.script-apply { background: #4a7dff; color: #fff; }
.script-apply:hover { background: #3a6cf0; }
.script-apply:disabled { opacity: 0.5; cursor: default; }
</style>
