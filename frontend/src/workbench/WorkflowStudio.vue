<!--
V1.9 Workflow Studio：工作流可视化节点编辑器。
把 ComfyUI workflow_api.json 从「黑盒模板」变成「可视化可编辑对象」：
  - 左侧节点图：真实读取 workflow JSON 的 DAG 连线（自绘 SVG，无第三方依赖）
  - 点击节点 → 右侧参数表单（widget 自动推断 int/float/str/bool/combo）
  - 节点三级视觉：🔵 Director 核心 / 🟢 普通参数 / 🔴 系统连接锁定
  - LoRA 区块：编辑已有 / 一键插入 / 移除
  - 保存 / 另存为：写回 Workflow Registry（内置工作流保存自动转另存为）
纯前端组件；不修改后端。
-->
<script setup lang="ts">
import { computed, ref } from "vue";
import {
  findLoraNode,
  insertLoraNode,
  nodeRole,
  removeLoraNode,
  scanWorkflowGraph,
  widgetsOf,
  type WidgetField,
} from "@/core/workflowStudio";
import { layoutWorkflow, NODE_H, NODE_W } from "@/core/workflowLayout";

const props = defineProps<{
  /** 当前编辑的工作流（父组件从 registry 取，深拷贝进入 draft）。 */
  workflow: Record<string, unknown>;
  /** 工作流展示名。 */
  name: string;
  /** 内置默认工作流（保存时自动转另存为）。 */
  isBuiltin: boolean;
}>();

const emit = defineEmits<{
  close: [];
  /** 保存：把当前 draft 深拷贝写回 registry。 */
  save: [workflow: Record<string, unknown>];
  /** 另存为：新建条目。 */
  saveAs: [name: string, workflow: Record<string, unknown>];
}>();

/** 编辑草稿（深拷贝自 props.workflow，保存时才写回 registry）。 */
const draft = ref<Record<string, unknown>>(
  JSON.parse(JSON.stringify(props.workflow)) as Record<string, unknown>,
);
const selectedNodeId = ref<string | null>(null);
const dirty = ref(false);

// ---- LoRA 编辑状态 ----
const addLoraOpen = ref(false);
const loraNameInput = ref("");
const loraStrengthInput = ref(1.0);
const loraError = ref("");

const graph = computed(() => scanWorkflowGraph(draft.value));
const layout = computed(() => layoutWorkflow(draft.value));
const lora = computed(() => findLoraNode(draft.value));

const posMap = computed(() => {
  const m = new Map<string, { x: number; y: number }>();
  for (const n of layout.value.nodes) m.set(n.nodeId, { x: n.x, y: n.y });
  return m;
});

const selectedNode = computed<{ class_type: string; inputs: Record<string, unknown> } | null>(() => {
  if (!selectedNodeId.value) return null;
  const node = draft.value[selectedNodeId.value] as
    | { class_type: string; inputs: Record<string, unknown> }
    | undefined;
  return node ?? null;
});

const selectedRole = computed(() => (selectedNode.value ? nodeRole(selectedNode.value.class_type) : null));
const selectedFields = computed<WidgetField[]>(() => {
  if (!selectedNode.value) return [];
  return widgetsOf(selectedNode.value);
});

/** 选中节点角色文案（模板用，避免非空断言）。 */
const selectedNodeRoleText = computed(() =>
  selectedNode.value ? roleLabelOf(selectedNodeId.value ?? "").text : "",
);

/** 读取当前节点 inputs 里的实时 widget 值（选中节点副本）。 */
function fieldValueOf(field: WidgetField): unknown {
  return selectedNode.value?.inputs?.[field.key];
}

/** class_type 简短名（去掉包名前缀）。 */
function shortClass(ct: string): string {
  const last = ct.split(".").pop() ?? ct;
  return last.length > 26 ? `${last.slice(0, 26)}…` : last;
}

function roleLabelOf(nodeId: string): { text: string; cls: string } {
  const node = draft.value[nodeId] as { class_type?: string } | undefined;
  const role = nodeRole(node?.class_type ?? "");
  if (role === "director") return { text: "🎬 Director", cls: "role-director" };
  if (role === "system") return { text: "🔒 系统节点", cls: "role-system" };
  return { text: "⚙ 参数节点", cls: "role-normal" };
}

function nodeSummary(nodeId: string): string {
  const node = draft.value[nodeId] as { class_type?: string; inputs?: Record<string, unknown> } | undefined;
  if (!node?.inputs) return "";
  const fields = widgetsOf(node).filter((f) => f.group === "free");
  return fields
    .slice(0, 2)
    .map((f) => `${f.key}=${String(f.value ?? "")}`)
    .join(" · ");
}

function nodeSelectedClass(nodeId: string): string {
  return selectedNodeId.value === nodeId ? "selected" : "";
}

/** 节点 class_type 原文（模板辅助，避免在模板里写类型断言）。 */
function nodeClassType(nodeId: string): string {
  const node = draft.value[nodeId] as { class_type?: string } | undefined;
  return node?.class_type ?? "";
}

/** 节点角色 CSS 类名（ws-node-director / system / normal）。 */
function nodeRoleClass(nodeId: string): string {
  return roleLabelOf(nodeId).cls.replace("role-", "");
}

function selectNode(nodeId: string): void {
  selectedNodeId.value = nodeId;
}

function edgePath(from: string, to: string): string {
  const a = posMap.value.get(from);
  const b = posMap.value.get(to);
  if (!a || !b) return "";
  const fx = a.x + NODE_W;
  const fy = a.y + NODE_H / 2;
  const tx = b.x;
  const ty = b.y + NODE_H / 2;
  const dx = Math.max(24, (tx - fx) / 2);
  return `M ${fx} ${fy} C ${fx + dx} ${fy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
}

/** 通用字段说明（第一版内置）。 */
const FIELD_HINTS: Record<string, string> = {
  seed: "填 -1 = 每次随机",
  sampler: "采样器（H3 官方 res_multistep）",
  scheduler: "调度器（H3 官方 simple）",
};

function fieldHint(field: WidgetField): string {
  return FIELD_HINTS[field.key] ?? "";
}

function onWidgetChange(field: WidgetField, raw: unknown): void {
  if (!selectedNodeId.value || !selectedNode.value) return;
  // 清空数字输入框时跳过（避免把字段误写成 0）。
  if ((field.kind === "int" || field.kind === "float") && raw === "") return;
  let value: unknown = raw;
  if (field.kind === "int" && typeof raw === "number" && !Number.isFinite(raw)) value = field.value;
  if (field.kind === "float" && typeof raw === "number" && !Number.isFinite(raw)) value = field.value;
  selectedNode.value.inputs[field.key] = value;
  dirty.value = true;
}

/** LoRA 强度滑杆直接写回工作流里的 LoRA 节点。 */
function onLoraStrength(ev: Event): void {
  if (!lora.value) return;
  const raw = (ev.target as HTMLInputElement).value;
  if (raw === "") return;
  const v = Number(raw);
  if (!Number.isFinite(v)) return;
  const node = draft.value[lora.value.nodeId] as { inputs: Record<string, unknown> } | undefined;
  if (!node) return;
  node.inputs.strength_model = v;
  dirty.value = true;
}

function onAddLora(): void {
  if (!draft.value) return;
  const res = insertLoraNode(draft.value, loraNameInput.value.trim(), Number(loraStrengthInput.value));
  if ("error" in res) {
    loraError.value = res.message;
    return;
  }
  draft.value = res.workflow;
  dirty.value = true;
  addLoraOpen.value = false;
  loraError.value = "";
  selectedNodeId.value = res.loraNodeId;
}

function onRemoveLora(): void {
  const res = removeLoraNode(draft.value);
  if ("error" in res) {
    loraError.value = res.message;
    return;
  }
  draft.value = res.workflow;
  dirty.value = true;
  loraError.value = "";
}

function openAddLora(): void {
  addLoraOpen.value = true;
  loraError.value = "";
  loraNameInput.value = "";
  loraStrengthInput.value = 1.0;
}

function onSave(): void {
  // 内置默认工作流不允许覆盖，点「保存」自动转另存为。
  if (props.isBuiltin) {
    onSaveAs();
    return;
  }
  emit("save", JSON.parse(JSON.stringify(draft.value)) as Record<string, unknown>);
  dirty.value = false;
}

function onSaveAs(): void {
  const suggested = `${props.name || "工作流"} 副本`;
  const name = window.prompt("另存为新工作流名称：", suggested);
  if (!name) return;
  emit("saveAs", name.trim(), JSON.parse(JSON.stringify(draft.value)) as Record<string, unknown>);
  dirty.value = false;
}

function onClose(): void {
  if (dirty.value && !window.confirm("工作流有未保存的修改，确定关闭？")) return;
  emit("close");
}

/** 保存按钮文案：内置工作流保存 = 另存为。 */
const saveButtonLabel = computed(() => (props.isBuiltin ? "另存为" : dirty.value ? "● 保存" : "保存"));

/** 字段是否只读：system 节点全部锁定 / project 组由项目控制。 */
function fieldReadonly(field: WidgetField): boolean {
  if (selectedRole.value === "system") return true;
  return field.group === "project";
}

function fieldDisabledHint(field: WidgetField): string {
  if (selectedRole.value === "system") return "🔒 系统连接节点锁定，只读";
  if (field.group === "project") return "⚙ 由项目（分辨率/帧率/时间轴）控制";
  return "";
}
</script>

<template>
  <Teleport to="body">
    <div class="ws-mask" @click.self="onClose">
      <div class="ws-panel" role="dialog" aria-label="Workflow Studio">
        <header class="ws-head">
          <div class="ws-title">
            <span class="ws-title-name" :title="name">{{ name }}</span>
            <span class="ws-badge" :class="dirty ? 'badge-dirty' : 'badge-ok'">
              {{ dirty ? "● 未保存修改" : "✓ 已保存" }}
            </span>
            <span v-if="isBuiltin" class="ws-badge badge-builtin">内置</span>
          </div>
          <div class="ws-actions">
            <button type="button" class="btn ws-btn-save" :class="{ active: dirty }" @click="onSave">
              {{ saveButtonLabel }}
            </button>
            <button type="button" class="btn ghost" @click="onClose">关闭</button>
          </div>
        </header>

        <div class="ws-body">
          <!-- 左侧：节点图 -->
          <div class="ws-graph">
            <div v-if="graph.nodes.length === 0" class="ws-empty">
              <div class="ws-empty-title">没有可显示的节点</div>
              <div class="ws-empty-hint">此工作流没有带 class_type 的节点</div>
            </div>
            <div v-else class="ws-canvas" :style="{ width: `${layout.width}px`, height: `${layout.height}px` }">
              <svg class="ws-svg" :width="layout.width" :height="layout.height" :viewBox="`0 0 ${layout.width} ${layout.height}`">
                <path
                  v-for="e in graph.edges"
                  :key="`${e.from}-${e.to}-${e.inputKey}`"
                  class="ws-edge"
                  :d="edgePath(e.from, e.to)"
                />
              </svg>
              <div
                v-for="n in layout.nodes"
                :key="n.nodeId"
                class="ws-node"
                :class="[nodeSelectedClass(n.nodeId), `ws-node-${nodeRoleClass(n.nodeId)}`]"
                :style="{ left: `${n.x}px`, top: `${n.y}px`, width: `${NODE_W}px` }"
                @click="selectNode(n.nodeId)"
              >
                <div class="ws-node-title">
                  <span class="ws-node-id">#{{ n.nodeId }}</span>
                  <span class="ws-node-class">{{ shortClass(nodeClassType(n.nodeId)) }}</span>
                </div>
                <div class="ws-node-role">{{ roleLabelOf(n.nodeId).text }}</div>
                <div v-if="nodeSummary(n.nodeId)" class="ws-node-summary">{{ nodeSummary(n.nodeId) }}</div>
              </div>
            </div>
          </div>

          <!-- 右侧：检查器 -->
          <aside class="ws-inspector">
            <!-- LoRA 区块 -->
            <section class="ws-section ws-lora">
              <div class="ws-section-title">LoRA 加速</div>
              <div v-if="lora" class="ws-lora-body">
                <div class="ws-lora-name" :title="lora.loraName">{{ lora.loraName }}</div>
                <label class="ws-field">
                  <span class="ws-field-label">强度 Strength</span>
                  <input
                    class="ws-input"
                    type="number"
                    step="0.05"
                    :value="lora.strengthModel"
                    @input="onLoraStrength($event)"
                  />
                </label>
                <div class="ws-lora-actions">
                  <button type="button" class="btn ghost danger" @click="onRemoveLora">移除 LoRA</button>
                </div>
              </div>
              <div v-else-if="addLoraOpen" class="ws-lora-body">
                <label class="ws-field">
                  <span class="ws-field-label">LoRA 文件名</span>
                  <input v-model="loraNameInput" class="ws-input" type="text" placeholder="如 h3_accel.safetensors" />
                </label>
                <label class="ws-field">
                  <span class="ws-field-label">强度</span>
                  <input v-model="loraStrengthInput" class="ws-input" type="number" step="0.05" />
                </label>
                <div v-if="loraError" class="ws-err">❌ {{ loraError }}</div>
                <div class="ws-lora-actions">
                  <button type="button" class="btn" :disabled="!loraNameInput.trim()" @click="onAddLora">插入节点</button>
                  <button type="button" class="btn ghost" @click="addLoraOpen = false">取消</button>
                </div>
                <div class="ws-hint">插入 LoraLoaderModelOnly 到 Director 的 model 输入前</div>
              </div>
              <div v-else class="ws-lora-empty">
                <div class="ws-hint">工作流未使用 LoRA</div>
                <button type="button" class="btn ghost" @click="openAddLora">＋ 添加 LoRA 节点</button>
              </div>
            </section>

            <!-- 选中节点参数 -->
            <section class="ws-section ws-fields">
              <div class="ws-section-title">节点参数</div>
              <div v-if="!selectedNode" class="ws-empty-sm">点击左侧节点查看/编辑参数</div>
              <template v-else>
                <div class="ws-node-head">
                  <span class="ws-node-head-id">#{{ selectedNodeId }}</span>
                  <span class="ws-node-head-class">{{ shortClass(selectedNode.class_type) }}</span>
                  <span class="ws-node-head-role">{{ selectedNodeRoleText }}</span>
                </div>
                <div v-if="selectedRole === 'system'" class="ws-lock-banner">🔒 系统连接节点（输出/后处理）默认锁定，防止拆断生成链路</div>
                <div v-if="selectedFields.length === 0" class="ws-empty-sm">该节点没有可编辑参数（全是节点连接）</div>
                <div v-for="f in selectedFields" :key="f.key" class="ws-field">
                  <span class="ws-field-label">
                    {{ f.key }}
                    <span v-if="fieldReadonly(f)" class="ws-field-lock" :title="fieldDisabledHint(f)">🔒</span>
                  </span>
                  <select
                    v-if="f.kind === 'combo'"
                    class="ws-input"
                    :disabled="fieldReadonly(f)"
                    :value="String(fieldValueOf(f) ?? '')"
                    @change="onWidgetChange(f, ($event.target as HTMLSelectElement).value)"
                  >
                    <option v-for="opt in f.options" :key="opt" :value="opt">{{ opt }}</option>
                  </select>
                  <input
                    v-else-if="f.kind === 'bool'"
                    class="ws-check"
                    type="checkbox"
                    :disabled="fieldReadonly(f)"
                    :checked="!!fieldValueOf(f)"
                    @change="onWidgetChange(f, ($event.target as HTMLInputElement).checked)"
                  />
                  <input
                    v-else-if="f.kind === 'int' || f.kind === 'float'"
                    class="ws-input"
                    type="number"
                    :step="f.kind === 'int' ? 1 : 0.01"
                    :disabled="fieldReadonly(f)"
                    :value="fieldValueOf(f) as number | undefined"
                    @input="onWidgetChange(f, Number(($event.target as HTMLInputElement).value))"
                  />
                  <input
                    v-else
                    class="ws-input"
                    type="text"
                    :disabled="fieldReadonly(f)"
                    :value="String(fieldValueOf(f) ?? '')"
                    @input="onWidgetChange(f, ($event.target as HTMLInputElement).value)"
                  />
                  <div v-if="fieldHint(f)" class="ws-hint">{{ fieldHint(f) }}</div>
                  <div v-if="fieldReadonly(f)" class="ws-hint">{{ fieldDisabledHint(f) }}</div>
                </div>
              </template>
            </section>
          </aside>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.ws-mask {
  position: fixed;
  inset: 0;
  background: rgba(5, 6, 12, 0.78);
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  color-scheme: dark;
}
.ws-panel {
  width: min(1200px, 96vw);
  height: min(760px, 92vh);
  background: #12121e;
  border: 1px solid #33334d;
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.6);
}
.ws-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid #2a2a40;
  background: #181824;
}
.ws-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.ws-title-name {
  color: #eef; font-weight: 600; font-size: 14px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 380px;
}
.ws-badge {
  font-size: 11px; padding: 2px 8px; border-radius: 999px; flex: none;
}
.badge-dirty { background: rgba(255, 176, 32, 0.16); color: #ffb020; }
.badge-ok { background: rgba(80, 200, 120, 0.14); color: #7ee2a0; }
.badge-builtin { background: rgba(74, 125, 255, 0.16); color: #7da6ff; }
.ws-actions { display: flex; align-items: center; gap: 8px; flex: none; }
.ws-btn-save.active { background: #4a7dff; color: #fff; }

.ws-body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.ws-graph {
  flex: 1;
  overflow: auto;
  padding: 12px;
  position: relative;
  background: #0e0e18;
}
.ws-canvas {
  position: relative;
}
.ws-svg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.ws-edge {
  fill: none;
  stroke: #3a3a58;
  stroke-width: 1.6;
  stroke-linecap: round;
  opacity: 0.9;
}
.ws-node {
  position: absolute;
  height: 76px;
  background: #1b1b2a;
  border: 1px solid #33334d;
  border-radius: 10px;
  padding: 7px 10px;
  box-sizing: border-box;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 3px;
  transition: border-color 0.12s, box-shadow 0.12s, background 0.12s;
  z-index: 1;
}
.ws-node:hover { border-color: #4a5568; }
.ws-node.selected {
  border-color: #4a7dff;
  box-shadow: 0 0 0 2px rgba(74, 125, 255, 0.32);
  background: #1d2130;
}
.ws-node-director { border-color: rgba(74, 125, 255, 0.55); }
.ws-node-system { border-color: rgba(224, 108, 108, 0.5); }
.ws-node-title {
  display: flex; align-items: center; gap: 6px; min-width: 0;
}
.ws-node-id { font-size: 10px; color: #8a8aa0; background: #26263a; border-radius: 4px; padding: 1px 5px; flex: none; }
.ws-node-class {
  font-size: 11px; font-weight: 600; color: #e0e0f0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ws-node-role { font-size: 10px; color: #9a9ab0; }
.ws-node-summary {
  font-size: 10px; color: #6f6f88;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.ws-inspector {
  width: 320px;
  flex: none;
  border-left: 1px solid #2a2a40;
  background: #16161f;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ws-section {
  background: #181824;
  border: 1px solid #2a2a40;
  border-radius: 10px;
  padding: 10px;
}
.ws-section-title {
  font-size: 11px; font-weight: 700; color: #8a8aa0; text-transform: uppercase;
  letter-spacing: 0.06em; margin-bottom: 8px;
}
.ws-lora-body, .ws-lora-empty { display: flex; flex-direction: column; gap: 8px; }
.ws-lora-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px; color: #e0e0f0; background: #12121e;
  border: 1px solid #2a2a40; border-radius: 6px; padding: 6px 8px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ws-lora-actions { display: flex; gap: 8px; }
.ws-hint { font-size: 10.5px; color: #6f6f88; line-height: 1.4; }
.ws-err { font-size: 11px; color: #ff8a8a; }

.ws-node-head {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 8px;
}
.ws-node-head-id { font-size: 11px; color: #8a8aa0; background: #26263a; border-radius: 4px; padding: 1px 6px; }
.ws-node-head-class { font-size: 12px; font-weight: 600; color: #eef; }
.ws-node-head-role { font-size: 10px; color: #9a9ab0; }
.ws-lock-banner {
  font-size: 11px; color: #e0a0a0; background: rgba(224, 108, 108, 0.1);
  border: 1px solid rgba(224, 108, 108, 0.25); border-radius: 6px; padding: 6px 8px; margin-bottom: 8px;
}
.ws-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; }
.ws-field-label { font-size: 11px; color: #9a9ab0; display: flex; align-items: center; gap: 4px; }
.ws-field-lock { font-size: 10px; }
.ws-input {
  background: #12121e; border: 1px solid #2a2a40; color: #e0e0f0;
  border-radius: 6px; padding: 6px 8px; font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.ws-input:disabled { opacity: 0.55; cursor: not-allowed; }
.ws-check { accent-color: #4a7dff; width: 16px; height: 16px; }
.ws-empty {
  height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: #6f6f88; gap: 6px;
}
.ws-empty-title { font-size: 14px; font-weight: 600; }
.ws-empty-hint { font-size: 12px; }
.ws-empty-sm { font-size: 12px; color: #6f6f88; padding: 12px 0; }
.btn { border: none; border-radius: 8px; padding: 6px 12px; font-size: 12px; cursor: pointer; background: #2a2a40; color: #e0e0f0; }
.btn:hover { background: #34344e; }
.btn.ghost { background: transparent; border: 1px solid #33334d; color: #b8b8cc; }
.btn.ghost:hover { border-color: #4a5568; }
.btn.ghost.danger { color: #e08a8a; border-color: rgba(224, 108, 108, 0.4); }
.btn.ghost.danger:hover { border-color: #e06c6c; background: rgba(224, 108, 108, 0.08); }
.btn:disabled { opacity: 0.5; cursor: default; }
</style>
