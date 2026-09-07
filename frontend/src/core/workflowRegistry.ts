/**
 * V1.8-2 Workflow Registry：手动工作流选择。
 *
 * 职责：
 * - 扫描 workflow_api.json（ComfyUI 导出格式）里的 MiniMaxH3Director 节点
 * - 判定工作流有效性：0 个节点 = 不是 H3 工作流；多节点 = 需用户选择生成节点
 * - 构建/解析注册表条目（内置默认 + 手动导入）
 *
 * 设计约束：本模块保持纯函数 + 可序列化数据结构（workflow 对象可直接 JSON 存取），
 * 持久化读写由调用方（WorkbenchView）负责；这样核心逻辑可独立单测。
 */

export interface WorkflowEntry {
  /** 稳定 id：内置 = "builtin:h3-default"；手动导入 = "manual:..." */
  id: string;
  /** 展示名（导入时取文件名）。 */
  name: string;
  source: "builtin" | "manual";
  /** workflow_api.json 的节点对象（{ nodeId: { class_type, inputs, ... } }）。 */
  workflow: Record<string, unknown>;
  /** 扫描出的 MiniMaxH3Director 节点 id 列表（按对象 key 顺序）。 */
  directorNodeIds: string[];
  /** 多节点时用户选择的生成节点；单节点自动取唯一；未选为 null。 */
  selectedNodeId: string | null;
  importedAt: number;
}

export type WorkflowStatus = "invalid" | "none" | "ok" | "multi";

export interface ActiveWorkflow {
  entry: WorkflowEntry | null;
  workflow: Record<string, unknown>;
  /** 当前可提交的 Director 节点 id（无效/未选时为 null）。 */
  nodeId: string | null;
  status: WorkflowStatus;
  nodeCount: number;
}

export const BUILTIN_WORKFLOW_ID = "builtin:h3-default";
export const DEFAULT_DIRECTOR_NODE_ID = "5";

/** 扫描 workflow 中 class_type 含 MiniMaxH3Director 的节点 id。 */
export function scanDirectorNodeIds(workflow: unknown): string[] {
  if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) return [];
  const ids: string[] = [];
  for (const [id, node] of Object.entries(workflow)) {
    if (!node || typeof node !== "object") continue;
    const n = node as { class_type?: unknown };
    if (typeof n.class_type === "string" && n.class_type.includes("MiniMaxH3Director")) {
      ids.push(id);
    }
  }
  return ids;
}

export interface ParseWorkflowResult {
  ok: boolean;
  workflow?: Record<string, unknown>;
  error?: string;
}

/**
 * 解析 workflow_api.json 文本为节点对象。
 * ComfyUI 导出的 workflow_api.json 顶层含非节点元数据（comfyUiApi/version 等 string 值），
 * 这里只保留带 class_type 字符串的节点；过滤掉裸元数据 key。
 */
export function parseWorkflowJson(text: string): ParseWorkflowResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { ok: false, error: "JSON 解析失败：不是有效的 JSON 文件" };
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, error: "工作流结构无效：应为节点对象 { 节点id: { class_type, inputs } }" };
  }
  const workflow: Record<string, unknown> = {};
  for (const [id, node] of Object.entries(raw)) {
    if (!node || typeof node !== "object" || Array.isArray(node)) continue;
    const n = node as { class_type?: unknown };
    if (typeof n.class_type !== "string" || !n.class_type) continue;
    workflow[id] = node;
  }
  if (Object.keys(workflow).length === 0) {
    return { ok: false, error: "工作流无效：没有找到带 class_type 的节点" };
  }
  return { ok: true, workflow };
}

/** 基于解析出的节点对象构建注册表条目（手动导入）。 */
export function buildWorkflowEntry(
  workflow: Record<string, unknown>,
  name: string,
  id?: string,
): WorkflowEntry {
  const directorNodeIds = scanDirectorNodeIds(workflow);
  return {
    id: id ?? `manual:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
    name: name || "手动导入工作流",
    source: "manual",
    workflow,
    directorNodeIds,
    // 单节点自动选中；多节点强制用户选择（selectedNodeId 置 null）。
    selectedNodeId: directorNodeIds.length === 1 ? directorNodeIds[0] : null,
    importedAt: Date.now(),
  };
}

/** 构建内置默认工作流条目（深拷贝 sample-workflow，节点 id 固定 "5"）。 */
export function buildBuiltinEntry(workflow: Record<string, unknown>): WorkflowEntry {
  const directorNodeIds = scanDirectorNodeIds(workflow);
  return {
    id: BUILTIN_WORKFLOW_ID,
    name: "默认 H3 r2v（内置）",
    source: "builtin",
    workflow,
    directorNodeIds,
    selectedNodeId: directorNodeIds.length === 1 ? directorNodeIds[0] : null,
    importedAt: 0,
  };
}

/**
 * 解析激活工作流的可提交状态。
 * - invalid：无条目（调用方应回退内置）
 * - none：0 个 Director 节点 → 提示「不是 MiniMax H3 Director 工作流」
 * - multi：多节点且未选 → 提示「请选择生成节点」
 * - ok：单节点自动 / 多节点已选 → nodeId 可提交
 */
export function resolveActiveWorkflow(entry: WorkflowEntry | null): ActiveWorkflow {
  if (!entry) {
    return { entry: null, workflow: {}, nodeId: null, status: "invalid", nodeCount: 0 };
  }
  const count = entry.directorNodeIds.length;
  if (count === 0) {
    return { entry, workflow: entry.workflow, nodeId: null, status: "none", nodeCount: 0 };
  }
  if (count === 1) {
    return { entry, workflow: entry.workflow, nodeId: entry.directorNodeIds[0], status: "ok", nodeCount: 1 };
  }
  const selected =
    entry.selectedNodeId !== null && entry.directorNodeIds.includes(entry.selectedNodeId);
  return {
    entry,
    workflow: entry.workflow,
    nodeId: selected ? entry.selectedNodeId : null,
    status: selected ? "ok" : "multi",
    nodeCount: count,
  };
}

/** 持久化 key（WorkbenchView 读写）。 */
export const WORKFLOW_REGISTRY_KEY = "minimax_studio.workflowRegistry.v1";

/** 从 localStorage 还原手动导入的注册表（防御性解析，坏数据返回空列表）。 */
export function loadWorkflowRegistry(raw: string | null): WorkflowEntry[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is WorkflowEntry =>
        !!e &&
        typeof e === "object" &&
        typeof (e as WorkflowEntry).id === "string" &&
        typeof (e as WorkflowEntry).workflow === "object" &&
        Array.isArray((e as WorkflowEntry).directorNodeIds),
    );
  } catch {
    return [];
  }
}
