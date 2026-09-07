/**
 * V1.9 Workflow Studio：把 ComfyUI workflow_api.json 从「黑盒模板」变成「可视化可编辑对象」。
 *
 * 职责（纯函数，可独立单测）：
 * - 扫描节点 → 分类角色（🔵 Director 核心 / 🟢 普通参数 / 🔴 系统连接锁定）
 * - 收集节点连接边（从 inputs 数组引用提取）→ 供 DAG 布局
 * - 提取可编辑 widget（标量参数 vs 节点连接自动区分；类型推断 int/float/str/bool/combo）
 * - Director 字段分组：managed（SPA 自动管理，隐藏）/ project-controlled（项目级，只读）/
 *   free（采样参数，可自由编辑）
 * - LoRA 节点检测 + 一键插入（受控连线操作，不做自由连线）
 * - seed=-1 随机语义
 *
 * 设计约束：本模块不改动原始 workflow 对象，所有「写」操作返回新对象（深拷贝），
 * 持久化/注册表读写由调用方负责。
 */

// ---------- 角色判定 ----------

/** 节点角色：director=Director 核心；system=输出/后处理（锁定）；normal=其余参数节点。 */
export type WorkflowNodeRole = "director" | "normal" | "system";

/** 判定为系统锁定节点的 class_type 关键字（输出/后处理，连线拆了会断生成链路）。 */
const SYSTEM_NODE_KEYWORDS = [
  "SaveVideo",
  "SaveImage",
  "CreateVideo",
  "PreviewImage",
  "VHS_VideoCombine",
  "ImageScale",
];

/** 判定节点角色。 */
export function nodeRole(classType: string): WorkflowNodeRole {
  if (!classType) return "normal";
  if (classType.includes("MiniMaxH3Director")) return "director";
  if (SYSTEM_NODE_KEYWORDS.some((k) => classType.includes(k))) return "system";
  return "normal";
}

// ---------- 枚举选项（第一版内置；后续可从 ComfyUI object_info 拉真实列表） ----------

/** MiniMax H3 任务类型（与 lib/task_prompts.py task_type_combo_options 一致）。 */
export const TASK_TYPE_OPTIONS: string[] = [
  "t2v — 文生视频(Text to Video)",
  "i2v — 图生视频(Image to Video)",
  "fl2v — 首尾帧生视频(First-Last Frame)",
  "r2v — 参考主体生视频(Reference to Video)",
  "v2v — 视频转视频(Video to Video)",
  "rv2v — 参考素材改视频(Reference Video Edit)",
];

/** ComfyUI KSampler.SAMPLERS 常用子集（H3 官方模板 res_multistep）。 */
export const SAMPLER_OPTIONS: string[] = [
  "res_multistep",
  "euler",
  "euler_ancestral",
  "heun",
  "dpmpp_2m",
  "dpmpp_2m_sde",
  "dpmpp_3m_sde",
  "dpmpp_sde",
  "ddim",
  "uni_pc",
  "uni_pc_bh2",
  "lms",
  "dpm_fast",
  "dpm_adaptive",
];

/** ComfyUI KSampler.SCHEDULERS 常用子集（H3 官方模板 simple）。 */
export const SCHEDULER_OPTIONS: string[] = [
  "normal",
  "karras",
  "exponential",
  "sgm_uniform",
  "simple",
  "ddim_uniform",
  "beta",
];

// ---------- Director 字段分组 ----------

/** SPA 自动管理、不展示给用户编辑的字段（生成前由 directorRun 注入）。 */
export const DIRECTOR_MANAGED_FIELDS: string[] = ["timeline_data", "timeline"];

/** 项目级控制、编辑了也不生效的字段（Workbench 生成时以项目/时间轴为准）。 */
export const DIRECTOR_PROJECT_FIELDS: string[] = [
  "width",
  "height",
  "ref_max_size",
  "total_frames",
  "frame_rate",
  "global_prompt",
];

/** ComfyUI 折叠分组标签 widget 前缀（BDGROUP，非真参数）。 */
const BDGROUP_PREFIX = "bd_grp";

// ---------- 图扫描 ----------

export interface WorkflowNodeMeta {
  nodeId: string;
  classType: string;
  role: WorkflowNodeRole;
}

/** 一条节点连接边（from 是上游 / to 是下游；fromSlot=上游输出槽，toSlot=下游输入槽）。 */
export interface WorkflowEdge {
  from: string;
  to: string;
  fromSlot: number;
  toSlot: number;
  inputKey: string;
}

export interface WorkflowGraph {
  nodes: WorkflowNodeMeta[];
  edges: WorkflowEdge[];
  /** 唯一 Director 节点 id（无则 null；多节点取第一个，UI 提示用 scanDirectorNodeIds）。 */
  directorNodeId: string | null;
}

/** 从 inputs 数组引用提取一条连接：[targetId, slot] 或 [targetId, slot, meta]。 */
function inputEdge(nodeId: string, inputKey: string, value: unknown): WorkflowEdge | null {
  if (!Array.isArray(value) || value.length < 2) return null;
  const target = value[0];
  const slot = value[1];
  if (typeof target !== "string" && typeof target !== "number") return null;
  if (typeof slot !== "number") return null;
  return { from: String(target), to: nodeId, fromSlot: slot, toSlot: 0, inputKey };
}

/** 扫描工作流 → 节点元信息 + 边（输入数据源，供布局/连线绘制）。 */
export function scanWorkflowGraph(workflow: unknown): WorkflowGraph {
  const graph: WorkflowGraph = { nodes: [], edges: [], directorNodeId: null };
  if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) return graph;

  for (const [nodeId, node] of Object.entries(workflow as Record<string, unknown>)) {
    if (!node || typeof node !== "object") continue;
    const n = node as { class_type?: unknown; inputs?: Record<string, unknown> };
    if (typeof n.class_type !== "string" || !n.class_type) continue;
    const role = nodeRole(n.class_type);
    graph.nodes.push({ nodeId, classType: n.class_type, role });
    if (role === "director" && graph.directorNodeId === null) {
      graph.directorNodeId = nodeId;
    }
    const inputs = n.inputs ?? {};
    for (const [key, value] of Object.entries(inputs)) {
      const edge = inputEdge(nodeId, key, value);
      if (edge) graph.edges.push(edge);
    }
  }
  return graph;
}

/** 找出所有 Director 节点 id（复用 workflowRegistry 的扫描；多节点时需用户选择）。 */
export function scanDirectorNodeIds(workflow: unknown): string[] {
  if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) return [];
  const ids: string[] = [];
  for (const [id, node] of Object.entries(workflow as Record<string, unknown>)) {
    if (!node || typeof node !== "object") continue;
    const n = node as { class_type?: unknown };
    if (typeof n.class_type === "string" && n.class_type.includes("MiniMaxH3Director")) {
      ids.push(id);
    }
  }
  return ids;
}

// ---------- widget 提取 ----------

export type WidgetKind = "int" | "float" | "str" | "bool" | "combo";

export interface WidgetField {
  key: string;
  value: unknown;
  kind: WidgetKind;
  /** combo 的可选项（kind === "combo" 时非空）。 */
  options?: string[];
  /** Director 字段分组：free=可自由编辑；project=项目级只读；managed=SPA 隐藏。 */
  group: "free" | "project" | "managed";
}

const COMBO_FIELDS: Record<string, string[]> = {
  task_type: TASK_TYPE_OPTIONS,
  sampler: SAMPLER_OPTIONS,
  scheduler: SCHEDULER_OPTIONS,
};

/** 语义上是 FLOAT 的字段（值可能恰为整数，如 cfg:1.0，但需要允许小数输入）。 */
const FLOAT_FIELDS = new Set([
  "cfg",
  "shift_video",
  "shift_audio",
  "frame_rate",
  "fps",
  "strength_model",
  "strength_clip",
  "denoise",
]);

/** 语义上是 INT 的字段（值可能含 .0，但应整型输入）。 */
const INT_FIELDS = new Set([
  "steps",
  "seed",
  "width",
  "height",
  "total_frames",
  "ref_max_size",
  "batch_size",
]);

/** 推断 widget 类型：boolean→bool；number→int/float（含已知字段表）；string→combo 或 str。 */
export function inferWidgetKind(key: string, value: unknown): WidgetKind {
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number") {
    if (FLOAT_FIELDS.has(key)) return "float";
    if (INT_FIELDS.has(key)) return "int";
    return Number.isInteger(value) ? "int" : "float";
  }
  if (typeof value === "string" && COMBO_FIELDS[key]) return "combo";
  return "str";
}

/** 从节点 inputs 提取可编辑 widget（跳过连接引用、BDGROUP 标签、managed 隐藏字段）。 */
export function widgetsOf(node: unknown): WidgetField[] {
  if (!node || typeof node !== "object") return [];
  const n = node as { class_type?: unknown; inputs?: Record<string, unknown> };
  if (typeof n.class_type !== "string" || !n.inputs) return [];
  const isDirector = n.class_type.includes("MiniMaxH3Director");
  const out: WidgetField[] = [];
  for (const [key, value] of Object.entries(n.inputs)) {
    if (Array.isArray(value)) continue; // 连接引用
    if (key.startsWith(BDGROUP_PREFIX)) continue; // 折叠分组标签
    if (typeof value === "object" && value !== null) continue; // 嵌套结构不当作 widget
    if (isDirector) {
      if (DIRECTOR_MANAGED_FIELDS.includes(key)) continue; // SPA 管理，隐藏
      const group = DIRECTOR_PROJECT_FIELDS.includes(key) ? "project" : "free";
      out.push({ key, value, kind: inferWidgetKind(key, value), group });
    } else {
      out.push({ key, value, kind: inferWidgetKind(key, value), group: "free" });
    }
  }
  return out;
}

// ---------- 写操作（深拷贝返回新 workflow，不污染原对象） ----------

function cloneWorkflow(workflow: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(workflow)) as Record<string, unknown>;
}

/** 深拷贝后写回一个 widget 值，返回新 workflow。 */
export function setNodeWidget(
  workflow: Record<string, unknown>,
  nodeId: string,
  key: string,
  value: unknown,
): Record<string, unknown> {
  const next = cloneWorkflow(workflow);
  const node = next[nodeId] as { inputs?: Record<string, unknown> } | undefined;
  if (!node?.inputs) return next;
  node.inputs[key] = value;
  return next;
}

// ---------- Director / LoRA 查找 ----------

export interface DirectorNodeInfo {
  nodeId: string;
  node: { class_type: string; inputs: Record<string, unknown> };
}

/** 找唯一 Director 节点（多节点取第一个）。 */
export function findDirectorNode(workflow: unknown): DirectorNodeInfo | null {
  const ids = scanDirectorNodeIds(workflow);
  if (ids.length === 0) return null;
  const id = ids[0];
  const node = (workflow as Record<string, unknown>)[id] as {
    class_type: string;
    inputs: Record<string, unknown>;
  };
  return { nodeId: id, node };
}

export interface LoraNodeInfo {
  nodeId: string;
  node: { class_type: string; inputs: Record<string, unknown> };
  loraName: string;
  strengthModel: number;
}

/** 检测工作流里已有的 LoRA 节点（LoraLoader / LoraLoaderModelOnly）。 */
export function findLoraNode(workflow: unknown): LoraNodeInfo | null {
  if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) return null;
  for (const [nodeId, node] of Object.entries(workflow as Record<string, unknown>)) {
    if (!node || typeof node !== "object") continue;
    const n = node as { class_type?: unknown; inputs?: Record<string, unknown> };
    const ct = typeof n.class_type === "string" ? n.class_type : "";
    if (!ct.includes("LoraLoader") && ct !== "LoraLoaderModelOnly") continue;
    const inputs = n.inputs ?? {};
    const loraName = typeof inputs.lora_name === "string" ? inputs.lora_name : "";
    const strength =
      typeof inputs.strength_model === "number" ? inputs.strength_model : 1.0;
    return { nodeId, node: { class_type: ct, inputs }, loraName, strengthModel: strength };
  }
  return null;
}

export interface InsertLoraResult {
  workflow: Record<string, unknown>;
  loraNodeId: string;
}

/**
 * 一键插入 LoraLoaderModelOnly 到 Director 的 model 输入前（受控连线操作）。
 * 要求：Director 存在、model 已连接非 LoRA 源、且工作流还没有 LoRA 节点。
 */
export function insertLoraNode(
  workflow: Record<string, unknown>,
  loraName: string,
  strengthModel: number,
): InsertLoraResult | InsertLoraError {
  const director = findDirectorNode(workflow);
  if (!director) {
    return { error: "no-director", message: "工作流里没有 MiniMaxH3Director 节点" };
  }
  if (findLoraNode(workflow)) {
    return { error: "already-lora", message: "工作流已存在 LoRA 节点，直接编辑即可" };
  }
  const modelInput = director.node.inputs.model;
  if (!Array.isArray(modelInput) || modelInput.length < 2) {
    return { error: "model-not-connected", message: "Director 节点的 model 未连接，无法插入 LoRA" };
  }
  const [srcId, slot] = modelInput;
  const loraNodeId = nextNodeId(workflow);
  const next = cloneWorkflow(workflow);
  next[loraNodeId] = {
    class_type: "LoraLoaderModelOnly",
    inputs: {
      lora_name: loraName || "lora.safetensors",
      strength_model: typeof strengthModel === "number" && isFinite(strengthModel) ? strengthModel : 1.0,
      model: [String(srcId), typeof slot === "number" ? slot : 0],
    },
  };
  (next[director.nodeId] as { inputs: Record<string, unknown> }).inputs.model = [loraNodeId, 0];
  return { workflow: next, loraNodeId };
}

export type RemoveLoraError = { error: "no-lora" | "no-director"; message: string };

/**
 * 移除 LoRA 节点并把 Director.model 恢复到 LoRA 的上游源。
 * 返回新 workflow（深拷贝），不污染原对象。
 */
export function removeLoraNode(
  workflow: Record<string, unknown>,
): { workflow: Record<string, unknown> } | RemoveLoraError {
  const lora = findLoraNode(workflow);
  if (!lora) return { error: "no-lora", message: "工作流里没有 LoRA 节点" };
  const director = findDirectorNode(workflow);
  if (!director) return { error: "no-director", message: "工作流里没有 Director 节点" };
  const next = cloneWorkflow(workflow);
  const src = lora.node.inputs.model;
  if (Array.isArray(src) && src.length >= 2) {
    (next[director.nodeId] as { inputs: Record<string, unknown> }).inputs.model = [
      String(src[0]),
      typeof src[1] === "number" ? src[1] : 0,
    ];
  }
  delete next[lora.nodeId];
  return { workflow: next };
}

/** 找出下一个未占用的数字节点 id（max 数字 key + 1；无数字 key 时从 "100" 开始）。 */
function nextNodeId(workflow: Record<string, unknown>): string {
  let max = 99;
  for (const id of Object.keys(workflow)) {
    const num = Number(id);
    if (Number.isInteger(num) && num >= 0 && num > max) max = num;
  }
  let candidate = String(max + 1);
  while (candidate in workflow) candidate = String(Number(candidate) + 1);
  return candidate;
}

export interface InsertLoraError {
  error: "no-director" | "model-not-connected" | "already-lora";
  message: string;
}

// ---------- seed 随机语义 ----------

/** seed === -1 → 每次生成随机；否则原样返回（整数化）。 */
export function resolveSeed(seed: unknown): number {
  if (typeof seed !== "number" || !isFinite(seed)) return 0;
  const v = Math.trunc(seed);
  if (v === -1) return Math.floor(Math.random() * 2 ** 31);
  return v;
}

/** 掷一个 32 位随机种子（0 ≤ seed < 2^31，与 resolveSeed/-1 语义一致）。 */
export function rollRandomSeed(): number {
  return Math.floor(Math.random() * 2 ** 31);
}

/** 读 Director 节点当前 seed 值（-1 = 随机模式；无 Director 节点返回 null）。 */
export function readWorkflowSeed(workflow: unknown): number | null {
  const dir = findDirectorNode(workflow);
  if (!dir) return null;
  const s = dir.node.inputs.seed;
  return typeof s === "number" && isFinite(s) ? Math.trunc(s) : null;
}
