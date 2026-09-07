/**
 * V1.9 Workflow Studio：DAG 分层布局（无第三方依赖）。
 *
 * 从 workflow 的节点 + 连接边推导节点画布坐标：
 * - 分层：最长路径法（无入边节点 → 层 0；其余 → max(上游层) + 1）
 * - 同层按节点在 workflow 对象中的出现顺序横向排布
 * - 产出每节点坐标 + 画布尺寸（组件直接按绝对坐标渲染 SVG + 节点卡）
 *
 * 纯函数，可独立单测。
 */

import { scanWorkflowGraph, type WorkflowGraph } from "@/core/workflowStudio";

export interface LayoutNode {
  nodeId: string;
  /** 层号（0-based，纵向）。 */
  layer: number;
  /** 同层序号（横向）。 */
  index: number;
  x: number;
  y: number;
}

export interface WorkflowLayout {
  nodes: LayoutNode[];
  /** 画布宽高（px）。 */
  width: number;
  height: number;
  layerCount: number;
}

/** 节点卡尺寸 / 间距（与 WorkflowStudio.vue 样式保持一致的常量）。 */
export const NODE_W = 216;
export const NODE_H = 76;
export const GAP_X = 64;
export const GAP_Y = 30;
export const PAD_X = 28;
export const PAD_Y = 24;

interface LayoutOpts {
  nodeW?: number;
  nodeH?: number;
  gapX?: number;
  gapY?: number;
  padX?: number;
  padY?: number;
}

export function layoutWorkflow(workflow: unknown, opts: LayoutOpts = {}): WorkflowLayout {
  const {
    nodeW = NODE_W,
    nodeH = NODE_H,
    gapX = GAP_X,
    gapY = GAP_Y,
    padX = PAD_X,
    padY = PAD_Y,
  } = opts;

  const graph: WorkflowGraph = scanWorkflowGraph(workflow);
  const empty: WorkflowLayout = { nodes: [], width: 0, height: 0, layerCount: 0 };
  if (graph.nodes.length === 0) return empty;

  // 节点出现顺序（稳定排序依据）。
  const nodeIds = graph.nodes.map((n) => n.nodeId);
  const idIndex = new Map(nodeIds.map((id, i) => [id, i]));

  // 上游表：nodeId → Set<上游 nodeId>。
  const upstream = new Map<string, Set<string>>();
  for (const n of graph.nodes) upstream.set(n.nodeId, new Set());
  for (const e of graph.edges) {
    if (upstream.has(e.to)) upstream.get(e.to)!.add(e.from);
  }

  // 最长路径分层（迭代直到收敛）。
  const layerOf = new Map<string, number>();
  for (const id of nodeIds) layerOf.set(id, 0);
  let changed = true;
  let guard = 0;
  while (changed && guard < nodeIds.length + 1) {
    changed = false;
    guard += 1;
    for (const id of nodeIds) {
      let want = layerOf.get(id) ?? 0;
      for (const up of upstream.get(id) ?? []) {
        const upLayer = layerOf.get(up) ?? 0;
        if (upLayer + 1 > want) want = upLayer + 1;
      }
      if (want !== layerOf.get(id)) {
        layerOf.set(id, want);
        changed = true;
      }
    }
  }

  const layerCount = (Math.max(0, ...layerOf.values()) ?? 0) + 1;
  const byLayer = new Map<number, LayoutNode[]>();
  for (let i = 0; i < layerCount; i++) byLayer.set(i, []);

  const sorted = [...nodeIds].sort((a, b) => (idIndex.get(a) ?? 0) - (idIndex.get(b) ?? 0));
  for (const id of sorted) {
    const layer = layerOf.get(id) ?? 0;
    const list = byLayer.get(layer)!;
    list.push({
      nodeId: id,
      layer,
      index: list.length,
      x: 0,
      y: 0,
    });
  }

  // 坐标：层 → x，层内序号 → y（横向排布）。
  for (let layer = 0; layer < layerCount; layer++) {
    const list = byLayer.get(layer) ?? [];
    list.forEach((ln, i) => {
      ln.x = padX + layer * (nodeW + gapX);
      ln.y = padY + i * (nodeH + gapY);
    });
  }

  const layerSizes = Array.from(byLayer.values(), (l) => l.length);
  const maxNodesInLayer = Math.max(1, ...layerSizes);
  const width = Math.max(0, padX * 2 + layerCount * nodeW + (layerCount - 1) * gapX);
  const height = Math.max(0, padY * 2 + maxNodesInLayer * nodeH + (maxNodesInLayer - 1) * gapY);

  const nodes = [...byLayer.values()].flat();
  return { nodes, width, height, layerCount };
}
