/**
 * V1.10-B 生成中「刷新/关闭恢复」快照：浏览器刷新/重开时，用一份持久化快照
 * 重建正在 ComfyUI 中执行的任务，并对当前 prompt_id 挂回轮询/WS 回调判定归宿。
 *
 * 设计约束：
 *  - 纯数据 + localStorage 序列化（可单测）；读写由 WorkbenchView 负责。
 *  - 快照只记录「最后一次提交/进行中的那一镜」的 prompt_id；恢复时按
 *    /history + /queue 判定该 prompt 是 running / completed / error / missing。
 *  - 断点续跑：走查模式恢复后，若当前镜已完成，自动继续剩余镜头（用
 *    shotStates + pendingHashes 重建续跑上下文）。
 */

import type { GenMeta } from "@/core/generationEta";

/** 持久化 key（版本化，坏数据 load 返回 null）。 */
export const IN_FLIGHT_KEY = "minimax_studio.inFlight.v1";

/** 单镜成片视频（还原已生成镜头）。 */
export interface InFlightShotVideo {
  url: string;
  filename: string;
  /** 输出文件在 ComfyUI output 目录下的子目录（如 H3 的 "video"），可空。 */
  subfolder?: string;
}

/** 快照中「在生成期间不随单镜推进变化」的持久字段。 */
export interface InFlightSnapshotBase {
  version: 1;
  /** walkthrough=逐镜串行（可断点续跑）；single=单次全片/单镜合并（只恢复信息）。 */
  kind: "walkthrough" | "single";
  /** 所属项目 id（恢复时须与当前打开项目一致，否则不自动续跑）。 */
  projectId: string;
  nodeId: string;
  workflowId: string;
  scopeLabel: string;
  shotCount: number;
  /** 生成时刻全片拍平镜头 id 顺序（固化段索引，防项目增删后错位）。 */
  shotOrder: string[];
  targetShotIds: string[] | null;
  /** 实际提交的镜头 id（恢复续跑用）。 */
  scopeIds: string[];
  startedAt: number;
  outputSize: { width: number; height: number };
  frameRate: number;
  /** ETA 预测表（恢复后继续渲染预计剩余）。 */
  genMeta: GenMeta | null;
  /** 已完成镜头 → 成片地址（刷新后还原 Shot 卡 / 播放器）。 */
  finalVideos: Record<string, InFlightShotVideo>;
  /** V1.12：本次提交的实际种子（刷新恢复后该镜版本历史记同一种子）。 */
  seed?: number;
}

/** 完整快照（含随单镜推进变化的字段）。 */
export interface InFlightSnapshot extends InFlightSnapshotBase {
  taskId: string;
  /** 当前镜序号（0-based，指向 currentShotId）。 */
  currentIndex: number;
  currentShotId: string;
  /** 最后一次提交/进行中的 prompt_id（恢复探测目标）。 */
  currentPromptId: string;
  /** 走查队列每镜状态（pending/running/done/failed/cancelled）。 */
  shotStates: Record<string, string>;
  pendingHashes: Record<string, string>;
}

/** 防御性解析 localStorage 快照；坏数据返回 null。 */
export function loadInFlight(): InFlightSnapshot | null {
  try {
    const raw = localStorage.getItem(IN_FLIGHT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const s = parsed as Partial<InFlightSnapshot>;
    if (s.version !== 1) return null;
    if (typeof s.taskId !== "string" || typeof s.currentPromptId !== "string") return null;
    if (typeof s.currentShotId !== "string" || typeof s.currentIndex !== "number") return null;
    if (!Array.isArray(s.shotOrder) || !Array.isArray(s.scopeIds)) return null;
    if (typeof s.shotStates !== "object" || s.shotStates === null) return null;
    if (typeof s.pendingHashes !== "object" || s.pendingHashes === null) return null;
    return s as InFlightSnapshot;
  } catch {
    return null;
  }
}

/** 写快照（SQLite/配额异常静默，不影响生成主流程）。 */
export function saveInFlight(s: InFlightSnapshot): void {
  try {
    localStorage.setItem(IN_FLIGHT_KEY, JSON.stringify(s));
  } catch {
    // 静默
  }
}

/** 清除快照（任务收尾/取消/恢复完成后调用）。 */
export function clearInFlight(): void {
  try {
    localStorage.removeItem(IN_FLIGHT_KEY);
  } catch {
    // 静默
  }
}