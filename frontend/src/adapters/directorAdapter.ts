/**
 * DirectorAdapter — 模型适配层接口（薄翻译）。
 *
 * 职责（v1.1 已拍板）：
 * - 把 TimelineStructure 翻译成具体模型的 timeline_data JSON（buildTimelineData）。
 * - 把模型参数输出为 ComfyUI 节点的原生 widget 值（buildNodeWidgets）。
 * - 把后端 WS 事件翻译成前端统一语义（parseProgressEvent / parsePreviewEvent / parseFinishEvent）。
 * - **不做** @资产解析、不做继承、不做 Prompt 内容组装（那是 Director Core）。
 *
 * V1 只实现 MiniMaxH3Adapter（r2v prompt_batch）。
 */

import type { TimelineStructure } from "@/models/timeline";

/** 后端生成的某段/镜头运行事件。 */
export interface DirectorProgressEvent {
  nodeId: string;
  /** 1 起：当前段序号（本次运行范围内）。 */
  segment: number;
  segmentTotal: number;
  /** 1 起：timeline 全局段序号（含未运行段）。 */
  timelineSegment: number;
  timelineSegmentTotal: number;
  /** 本次是否部分运行（只跑一部分镜头）。 */
  partialRun: boolean;
  phase: string; // planning / encode / sample / decode / export / finish …
  phaseLabel: string;
  phaseValue: number;
  phaseMax: number;
  overallValue: number;
  overallMax: number;
  remainingSegments: number;
  framesLabel: string;
  taskKey: string;
}

export interface DirectorPreviewEvent {
  nodeId: string;
  /** 0 起：段索引（本次运行范围内）。 */
  segmentIndex: number;
  imageB64: string;
  width: number;
  height: number;
  frames?: string[];
  fps?: number;
}

export interface DirectorFinishEvent {
  nodeId: string;
  segmentTotal: number;
}

export interface BuildTimelineResult {
  timelineData: Record<string, unknown>;
}

export interface BuildNodeWidgetsResult {
  /** ComfyUI 节点原生 widget 值（采样参数等，不在 timeline JSON 里）。 */
  widgets: Array<unknown>;
}

export interface DirectorAdapter {
  readonly backend: string; // "minimax-h3"
  readonly displayName: string;

  /** TimelineStructure → timeline_data JSON（含段白名单字段）。 */
  buildTimelineData(structure: TimelineStructure): BuildTimelineResult;

  /** 输出 ComfyUI 节点原生 widget 值（采样器/步数等）。 */
  buildNodeWidgets(structure: TimelineStructure): BuildNodeWidgetsResult;

  /** 后端 WS 事件 → 统一语义。返回 null 表示不是本适配器关注的事件。 */
  parseProgressEvent(payload: Record<string, unknown>): DirectorProgressEvent | null;
  parsePreviewEvent(payload: Record<string, unknown>): DirectorPreviewEvent | null;
  parseFinishEvent(payload: Record<string, unknown>): DirectorFinishEvent | null;
}
