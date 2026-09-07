<!--
V1.9 工作流参数快捷面板：面向普通用户的高频采样参数编辑。
放在 Workbench 右上角 ⚙，只露高频参数（steps/cfg/seed/sampler/scheduler + LoRA 强度），
「高级」入口跳 Workflow Studio 完整节点图。
与 WorkflowStudio.vue 共用同一份工作流草稿语义：修改不落盘，保存/另存为才写回 Registry。
-->
<script setup lang="ts">
import { computed, ref } from "vue";
import {
  findDirectorNode,
  findLoraNode,
  setNodeWidget,
  widgetsOf,
  type WidgetField,
} from "@/core/workflowStudio";

const props = defineProps<{
  workflow: Record<string, unknown>;
  name: string;
  isBuiltin: boolean;
}>();

const emit = defineEmits<{
  close: [];
  save: [workflow: Record<string, unknown>];
  saveAs: [name: string, workflow: Record<string, unknown>];
  /** 跳到完整节点编辑器。 */
  openStudio: [];
}>();

const draft = ref<Record<string, unknown>>(
  JSON.parse(JSON.stringify(props.workflow)) as Record<string, unknown>,
);
const dirty = ref(false);

const director = computed(() => findDirectorNode(draft.value));
const lora = computed(() => findLoraNode(draft.value));

/** 高频采样参数白名单（存在才显示）。 */
const QUICK_KEYS = ["steps", "cfg", "seed", "sampler", "scheduler", "shift_video", "shift_audio"];

const quickFields = computed<WidgetField[]>(() => {
  if (!director.value) return [];
  return widgetsOf(director.value.node).filter(
    (f) => f.group === "free" && QUICK_KEYS.includes(f.key),
  );
});

/** Director 节点基础信息（模板展示用）。 */
const directorMeta = computed(() => {
  if (!director.value) return null;
  return { nodeId: director.value.nodeId, className: director.value.node.class_type };
});

const FIELD_HINTS: Record<string, string> = {
  seed: "填 -1 = 每次随机",
};

function fieldValue(field: WidgetField): unknown {
  return director.value?.node.inputs?.[field.key];
}

function hintOf(field: WidgetField): string {
  return FIELD_HINTS[field.key] ?? "";
}

function onFieldChange(field: WidgetField, raw: unknown): void {
  if (!director.value) return;
  if ((field.kind === "int" || field.kind === "float") && raw === "") return;
  let value: unknown = raw;
  if (field.kind === "int" && typeof raw === "number" && !Number.isFinite(raw)) value = field.value;
  if (field.kind === "float" && typeof raw === "number" && !Number.isFinite(raw)) value = field.value;
  draft.value = setNodeWidget(draft.value, director.value.nodeId, field.key, value);
  dirty.value = true;
}

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

function onSave(): void {
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

const saveButtonLabel = computed(() => (props.isBuiltin ? "另存为" : dirty.value ? "● 保存" : "保存"));
</script>

<template>
  <Teleport to="body">
    <div class="wpp-mask" @click.self="onClose">
      <div class="wpp-panel" role="dialog" aria-label="工作流参数">
        <header class="wpp-head">
          <div class="wpp-title">
            <span class="wpp-title-name" :title="name">⚙ {{ name }}</span>
            <span v-if="dirty" class="wpp-badge">● 未保存</span>
            <span v-if="isBuiltin" class="wpp-badge wpp-builtin">内置</span>
          </div>
          <div class="wpp-actions">
            <button type="button" class="btn" :class="{ active: dirty }" @click="onSave">{{ saveButtonLabel }}</button>
            <button type="button" class="btn ghost" @click="onClose">关闭</button>
          </div>
        </header>

        <div class="wpp-body">
          <div v-if="!directorMeta" class="wpp-empty">当前工作流里没有 Director 节点，无法编辑采样参数。</div>
          <template v-else>
            <div class="wpp-meta">
              <span class="wpp-meta-id">#{{ directorMeta.nodeId }}</span>
              <span class="wpp-meta-class">{{ directorMeta.className }}</span>
            </div>

            <div v-if="quickFields.length === 0" class="wpp-hint">
              此工作流的 Director 节点没有可编辑的采样参数。
            </div>
            <div v-for="f in quickFields" :key="f.key" class="wpp-field">
              <span class="wpp-label">{{ f.key }}</span>
              <select
                v-if="f.kind === 'combo'"
                class="wpp-input"
                :value="String(fieldValue(f) ?? '')"
                @change="onFieldChange(f, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="opt in f.options" :key="opt" :value="opt">{{ opt }}</option>
              </select>
              <input
                v-else
                class="wpp-input"
                type="number"
                :step="f.kind === 'int' ? 1 : 0.01"
                :value="fieldValue(f)"
                @input="onFieldChange(f, Number(($event.target as HTMLInputElement).value))"
              />
              <div v-if="hintOf(f)" class="wpp-hint">{{ hintOf(f) }}</div>
            </div>

            <div v-if="lora" class="wpp-lora">
              <div class="wpp-lora-head">
                <span class="wpp-lora-name" :title="lora.loraName">{{ lora.loraName }}</span>
                <span class="wpp-lora-badge">LoRA</span>
              </div>
              <label class="wpp-field">
                <span class="wpp-label">强度 Strength</span>
                <input
                  class="wpp-input"
                  type="number"
                  step="0.05"
                  :value="lora.strengthModel"
                  @input="onLoraStrength($event)"
                />
              </label>
            </div>
            <div v-else class="wpp-hint">当前工作流未挂载 LoRA（可在高级编辑器里一键插入）。</div>

            <button type="button" class="btn wpp-advanced" @click="emit('openStudio')">
              高级节点编辑器 →
            </button>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.wpp-mask {
  position: fixed;
  inset: 0;
  background: rgba(5, 6, 12, 0.55);
  z-index: 2900;
  display: flex;
  justify-content: flex-end;
  padding: 12px;
  color-scheme: dark;
}
.wpp-panel {
  width: 360px;
  max-width: 94vw;
  height: fit-content;
  max-height: 92vh;
  background: #12121e;
  border: 1px solid #33334d;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
}
.wpp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #2a2a40;
  background: #181824;
}
.wpp-title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.wpp-title-name {
  color: #eef; font-weight: 600; font-size: 13px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wpp-badge {
  font-size: 10px; color: #ffb020; background: rgba(255, 176, 32, 0.14);
  border-radius: 999px; padding: 1px 7px; flex: none;
}
.wpp-builtin { color: #7da6ff; background: rgba(74, 125, 255, 0.16); }
.wpp-actions { display: flex; gap: 6px; flex: none; }
.wpp-body { padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.wpp-meta { display: flex; align-items: center; gap: 6px; }
.wpp-meta-id { font-size: 11px; color: #8a8aa0; background: #26263a; border-radius: 4px; padding: 1px 6px; }
.wpp-meta-class { font-size: 11px; color: #9a9ab0; }
.wpp-field { display: flex; flex-direction: column; gap: 4px; }
.wpp-label { font-size: 11px; color: #9a9ab0; }
.wpp-input {
  background: #12121e; border: 1px solid #2a2a40; color: #e0e0f0;
  border-radius: 6px; padding: 6px 8px; font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.wpp-hint { font-size: 10.5px; color: #6f6f88; line-height: 1.4; }
.wpp-lora {
  background: #181824; border: 1px solid rgba(122, 162, 247, 0.3);
  border-radius: 8px; padding: 8px; display: flex; flex-direction: column; gap: 6px;
}
.wpp-lora-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.wpp-lora-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px; color: #dbe4ff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wpp-lora-badge {
  font-size: 10px; color: #7da6ff; background: rgba(74, 125, 255, 0.14);
  border-radius: 999px; padding: 1px 7px; flex: none;
}
.wpp-advanced { margin-top: 4px; width: 100%; }
.wpp-empty { font-size: 12px; color: #ffb0b0; padding: 12px 0; }
.btn { border: none; border-radius: 8px; padding: 6px 12px; font-size: 12px; cursor: pointer; background: #2a2a40; color: #e0e0f0; }
.btn:hover { background: #34344e; }
.btn.active { background: #4a7dff; color: #fff; }
.btn.ghost { background: transparent; border: 1px solid #33334d; color: #b8b8cc; }
.btn.ghost:hover { border-color: #4a5568; }
</style>
