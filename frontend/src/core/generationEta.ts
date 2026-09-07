/**
 * V1.8-3 生成预计时间（ETA）：基于历史生成数据预测当前镜 / 全片剩余时间。
 *
 * 数据：历史采样记录（resolution / fps / frames / workflow / task_type / generation_time）。
 * 模型：每帧耗时 msPerFrame = ms / frames，按特征（taskType + 分辨率 + fps）加权平均，
 *       再乘目标帧数得预测耗时；无匹配回退启发式常量（confidence=0，basis=fallback）。
 *
 * 设计约束：纯函数 + 可序列化数据结构（可直接 JSON 存 localStorage），
 * 持久化读写由调用方（WorkbenchView）负责；核心逻辑可独立单测（同 workflowRegistry）。
 */

import { deriveFrameCount } from "@/adapters/minimaxH3Adapter";

/** 历史采样：一次成功生成的实测墙钟耗时 + 生成参数特征。 */
export interface GenHistoryRecord {
  /** 任务类型：r2v / fl2v / t2v / i2v（"auto" 归一为实际生成模式 r2v）。 */
  taskType: string;
  width: number;
  height: number;
  fps: number;
  /** 段帧数（对齐 17k+5 网格后）。 */
  frames: number;
  /** 生成墙钟耗时（ms）。 */
  ms: number;
  /** 采样完成时间（近期加权用）。 */
  finishedAt: number;
  /** 工作流 id（内置/手动导入条目 id）。 */
  workflowId: string;
  /** 所属项目 id（V1.10 项目级耗时统计用；旧样本无此字段）。 */
  projectId?: string;
}

/** 历史采样持久化 key（WorkbenchView 读写）。 */
export const ETA_HISTORY_KEY = "minimax_studio.generationEta.v1";
/** 历史采样容量上限（超出丢最旧）。 */
export const ETA_HISTORY_CAP = 200;
/** 无历史匹配时的启发式每帧耗时（ms/帧，864×480 r2v 经验值）。 */
export const FALLBACK_MS_PER_FRAME = 600;

/** 单镜 ETA 预测入参。 */
export interface ShotEtaParams {
  taskType: string;
  width: number;
  height: number;
  fps: number;
  frames: number;
}

/** 单镜 ETA 预测结果。 */
export interface ShotEtaEstimate {
  /** 预测耗时（ms）。 */
  ms: number;
  /** 可信度 0~1（样本数 + 特征贴合度）。 */
  confidence: number;
  /** 命中历史样本数（0 = 回退启发式）。 */
  matchedCount: number;
  basis: "history" | "fallback";
}

/** 任务生成参数快照 + 逐镜预测（TaskRecord.genMeta，生成前算好供任务中心渲染）。 */
export interface GenMeta {
  width: number;
  height: number;
  fps: number;
  workflowId: string;
  /** 目标镜头（按生成顺序）的帧数 / 任务类型 / 预测耗时。 */
  shots: Array<{
    id: string;
    frames: number;
    taskType: string;
    predictedMs: number;
    confidence: number;
    /** 该镜命中的历史样本数。 */
    matchedCount: number;
  }>;
}

/** 进度事件最小形状（TaskCenter 从 TaskRecord.progress 传进来）。 */
export interface EtaProgressLike {
  phase?: string;
  phaseValue?: number;
  phaseMax?: number;
  segment?: number;
}

/** 运行中 ETA 快照（TaskCenter 每秒重算）。 */
export interface RunEtaSnapshot {
  /** 已用时间（ms）。 */
  elapsedMs: number;
  /** 当前镜序号（0-based，越界收敛到有效区间）。 */
  shotIndex: number;
  /** 当前镜阶段进度 0~1（后端 4 阶段：prepare/context_encode/sample/decode）。 */
  shotProgress: number;
  /** 当前镜剩余（ms）。 */
  currentShotRemainingMs: number;
  /** 全片剩余（ms，含当前镜剩余 + 后续镜预测）。 */
  remainingMs: number;
  /** 预计总时长（ms，所有目标镜预测和）。 */
  estimatedTotalMs: number;
  /** 可信度 0~1（所有镜平均）。 */
  confidence: number;
  /** 命中历史样本总数（0 = 全部回退启发式）。 */
  matchedTotal: number;
}

/** 归一化任务类型：空 / auto → 实际生成模式 r2v（后端段级继承全局）。 */
export function normalizeTaskKey(t: string | null | undefined): string {
  return !t || t === "auto" ? "r2v" : t;
}

/** 从 localStorage raw 还原历史采样（防御性解析，坏数据返回空数组）。 */
export function loadEtaHistory(raw: string | null): GenHistoryRecord[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (r): r is GenHistoryRecord =>
        !!r &&
        typeof r === "object" &&
        typeof (r as GenHistoryRecord).taskType === "string" &&
        typeof (r as GenHistoryRecord).frames === "number" &&
        typeof (r as GenHistoryRecord).ms === "number",
    );
  } catch {
    return [];
  }
}

/** 追加一条采样（新在前），超容量丢最旧。 */
export function appendEtaHistory(list: GenHistoryRecord[], sample: GenHistoryRecord): GenHistoryRecord[] {
  return [sample, ...list].slice(0, ETA_HISTORY_CAP);
}

/**
 * 单镜预测：命中历史按特征加权平均每帧耗时 → 乘目标帧数。
 * 特征权重：taskType 不匹配直接淘汰；分辨率精确=1、不匹配=0.4（每帧耗时按像素比缩放）；
 *           fps 精确=1、±2 内=0.7、否则=0.3。
 * 无命中 → 启发式常量（confidence=0，basis=fallback）。
 */
export function estimateShotMs(params: ShotEtaParams, history: GenHistoryRecord[]): ShotEtaEstimate {
  const targetPixels = params.width * params.height;
  let wSum = 0;
  let matched = 0;
  let scoreSum = 0;

  for (const rec of history) {
    if (rec.taskType !== params.taskType) continue;
    const recPixels = rec.width * rec.height;
    // 分辨率不匹配：每帧耗时按像素比缩放（面积法近似），权重降为 0.4。
    const resExact = rec.width === params.width && rec.height === params.height;
    const resScore = resExact ? 1 : 0.4;
    const scale = resExact || recPixels <= 0 ? 1 : Math.max(0.4, Math.min(2.5, targetPixels / recPixels));
    const fpsDiff = Math.abs(rec.fps - params.fps);
    const fpsScore = fpsDiff === 0 ? 1 : fpsDiff <= 2 ? 0.7 : 0.3;
    const score = resScore * fpsScore;
    if (score <= 0) continue;
    const recMsPerFrame = rec.frames > 0 ? rec.ms / rec.frames : 0;
    wSum += recMsPerFrame * scale * score;
    scoreSum += score;
    matched++;
  }

  if (matched > 0 && scoreSum > 0) {
    const avgMsPerFrame = wSum / scoreSum;
    const ms = Math.round(avgMsPerFrame * params.frames);
    const confidence = Math.min(1, matched / 5) * Math.min(1, scoreSum / matched);
    return { ms: Math.max(0, ms), confidence, matchedCount: matched, basis: "history" };
  }

  return {
    ms: Math.round(FALLBACK_MS_PER_FRAME * params.frames),
    confidence: 0,
    matchedCount: 0,
    basis: "fallback",
  };
}

/** 由镜头时长推导帧数（复用 H3 17k+5 网格对齐）。 */
export function framesForDuration(durationSec: number, fps: number): number {
  return deriveFrameCount(durationSec, fps);
}

/**
 * 构建任务生成参数快照 + 逐镜预测（任务开始前调用一次）。
 * shots 按生成顺序传入；每镜预测用当前历史快照，运行中不随历史增长重算。
 */
export function buildGenMeta(input: {
  width: number;
  height: number;
  fps: number;
  workflowId: string;
  shots: Array<{ id: string; durationSec: number; taskType: string }>;
  history: GenHistoryRecord[];
}): GenMeta {
  const { width, height, fps, workflowId } = input;
  const shotMetas = input.shots.map((s) => {
    const frames = framesForDuration(Math.max(0.1, s.durationSec), fps);
    const taskType = normalizeTaskKey(s.taskType);
    const est = estimateShotMs({ taskType, width, height, fps, frames }, input.history);
    return {
      id: s.id,
      frames,
      taskType,
      predictedMs: est.ms,
      confidence: est.confidence,
      matchedCount: est.matchedCount,
    };
  });
  return { width, height, fps, workflowId, shots: shotMetas };
}

/** H3 后端 4 阶段（与 director/progress.py DIRECTOR_PHASES 对齐）。 */
const SHOT_PHASES = ["prepare", "context_encode", "sample", "decode"];

/** 当前镜阶段进度 0~1：prepare/context_encode/sample/decode 四段 + 段内 phaseValue 占比。 */
export function currentShotFraction(p: EtaProgressLike | null | undefined): number {
  if (!p) return 0;
  if (p.phase === "finish") return 1;
  const i = SHOT_PHASES.indexOf(p.phase ?? "");
  if (i < 0) return 0;
  const max = Math.max(1, p.phaseMax ?? 1);
  const frac = Math.max(0, Math.min(1, (p.phaseValue ?? 0) / max));
  return Math.min(1, (i + frac) / SHOT_PHASES.length);
}

/** 运行中 ETA 快照：已用 / 当前镜进度 / 本镜剩余 / 全片剩余 / 总时长 / 可信度。 */
export function computeRunEta(
  meta: GenMeta,
  progress: EtaProgressLike | null | undefined,
  currentIndex: number | null,
  startedAt: number,
  nowMs: number,
): RunEtaSnapshot {
  const shots = meta.shots;
  const elapsedMs = Math.max(0, nowMs - startedAt);

  // 当前镜定位：走查模式用 currentIndex（后端 overall 只反映全片段进度）；
  // 单次模式用 progress.segment（后端段序号 1-based）。
  let shotIndex = 0;
  if (shots.length === 0) {
    return {
      elapsedMs,
      shotIndex: 0,
      shotProgress: 0,
      currentShotRemainingMs: 0,
      remainingMs: 0,
      estimatedTotalMs: 0,
      confidence: 0,
      matchedTotal: 0,
    };
  }
  if (currentIndex != null && currentIndex >= 0) {
    shotIndex = Math.min(currentIndex, shots.length - 1);
  } else if (progress && progress.segment != null && progress.segment > 0) {
    shotIndex = Math.min(Math.max(0, progress.segment - 1), shots.length - 1);
  }

  const shotProgress = currentShotFraction(progress);
  const current = shots[shotIndex];
  const currentRemaining = current.predictedMs * (1 - shotProgress);
  let remaining = currentRemaining;
  let matchedTotal = current.matchedCount;
  for (let i = shotIndex + 1; i < shots.length; i++) {
    remaining += shots[i].predictedMs;
    matchedTotal += shots[i].matchedCount;
  }
  const estimatedTotalMs = shots.reduce((a, s) => a + s.predictedMs, 0);
  const confidence = shots.length ? shots.reduce((a, s) => a + s.confidence, 0) / shots.length : 0;
  return {
    elapsedMs,
    shotIndex,
    shotProgress,
    currentShotRemainingMs: Math.max(0, currentRemaining),
    remainingMs: Math.max(0, remaining),
    estimatedTotalMs,
    confidence,
    matchedTotal,
  };
}

/** 毫秒 → mm:ss 或 h:mm:ss。 */
export function formatEtaMs(ms: number): string {
  if (!isFinite(ms) || ms < 0) return "--:--";
  const totalSec = Math.round(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// ---------- V1.10 生成体验：可信度分级 / 预测区间 / 历史耗时 / 项目统计 ----------

/**
 * ETA 可信度三级（V1.10-C）：2=预测较稳定（≥0.5）/ 1=预测正在收敛（0.2~0.5）/
 * 0=数据不足（<0.2 或回退启发式）。映射 ● / ◐ / ○。
 */
export function confidenceLevel(confidence: number): 0 | 1 | 2 {
  if (confidence >= 0.5) return 2;
  if (confidence >= 0.2) return 1;
  return 0;
}

/** 可信度三级圆点（● 稳定 / ◐ 收敛 / ○ 数据不足）。 */
export const CONF_LEVEL_DOT: Record<0 | 1 | 2, string> = { 0: "○", 1: "◐", 2: "●" };

/** 可信度三级说明（悬停用）。 */
export const CONF_LEVEL_LABEL: Record<0 | 1 | 2, string> = {
  0: "数据不足 · 经验估算",
  1: "预测正在收敛 · 请以实际进度为准",
  2: "预测较稳定 · 基于近期生成样本",
};

/**
 * 预测区间（V1.10-C）：把点预测放宽成区间，可信度越高区间越窄。
 * 稳定 ±20% / 收敛 ±40% / 数据不足 ±60%。
 */
export function etaIntervalMs(ms: number, confidence: number): { lo: number; hi: number } {
  const tier = confidenceLevel(confidence);
  const spread = tier === 2 ? 0.2 : tier === 1 ? 0.4 : 0.6;
  return { lo: Math.max(0, Math.round(ms * (1 - spread))), hi: Math.round(ms * (1 + spread)) };
}

/** 区间文案：约剩 X–Y 分钟（<1 分钟显示「约 1 分钟内」；同值显示单个）。 */
export function formatEtaInterval(lo: number, hi: number): string {
  const loMin = Math.max(1, Math.ceil(lo / 60000));
  const hiMin = Math.max(1, Math.ceil(hi / 60000));
  if (hiMin <= 1) return "约 1 分钟内";
  if (loMin === hiMin) return `约剩 ${loMin} 分钟`;
  return `约剩 ${loMin}–${hiMin} 分钟`;
}

/**
 * 镜头最近一次成功生成耗时（V1.10-D Shot 卡「⏱ 上次生成」）。
 * 匹配：taskType 精确 + 分辨率精确 + 帧数 ±2 内；取 finishedAt 最近的样本。无匹配返回 null。
 */
export function lastMsForShot(
  history: GenHistoryRecord[],
  params: { taskType: string; frames: number; width: number; height: number; projectId?: string },
): number | null {
  let best: GenHistoryRecord | null = null;
  for (const r of history) {
    if (r.taskType !== normalizeTaskKey(params.taskType)) continue;
    if (params.projectId && r.projectId && r.projectId !== params.projectId) continue;
    if (r.width !== params.width || r.height !== params.height) continue;
    if (Math.abs(r.frames - params.frames) > 2) continue;
    if (r.ms <= 0) continue;
    if (!best || r.finishedAt > best.finishedAt) best = r;
  }
  return best ? best.ms : null;
}

/** 项目级耗时统计（V1.10-D）：平均/最快/最慢/样本数。无有效样本返回 null。 */
export interface ProjectTimeStats {
  count: number;
  avgMs: number;
  fastestMs: number;
  slowestMs: number;
}

/** 按 projectId 过滤历史样本（无 projectId 的旧样本计入全部项目）。 */
export function projectTimeStats(history: GenHistoryRecord[], projectId?: string): ProjectTimeStats | null {
  const recs = projectId ? history.filter((r) => !r.projectId || r.projectId === projectId) : history;
  const valid = recs.filter((r) => r.ms > 0);
  if (!valid.length) return null;
  let sum = 0;
  let fastest = Infinity;
  let slowest = 0;
  for (const r of valid) {
    sum += r.ms;
    if (r.ms < fastest) fastest = r.ms;
    if (r.ms > slowest) slowest = r.ms;
  }
  return { count: valid.length, avgMs: Math.round(sum / valid.length), fastestMs: fastest, slowestMs: slowest };
}

/**
 * 真实阶段 step 文案（V1.10-E）：后端 phase_value/phase_max 存在且 phase_max>1 时
 * 显示「phaseLabel · Step v/max」（真实步进，非伪造）；否则只显示 phaseLabel。
 */
export function phaseStepLabel(
  p: { phaseLabel?: string; phaseValue?: number; phaseMax?: number } | null | undefined,
): string {
  if (!p?.phaseLabel) return "准备中…";
  if (p.phaseValue != null && p.phaseMax != null && p.phaseMax > 1) {
    return `${p.phaseLabel} · Step ${p.phaseValue}/${p.phaseMax}`;
  }
  return p.phaseLabel;
}
