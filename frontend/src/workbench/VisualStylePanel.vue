<!--
v2.0 P3（#631+）：Visual Style 全剧画风面板（左侧栏「📍 地点库 / 📖 Bible / 🎙 Voice Cast」下方）。

数据源（方案 C 单一事实来源，面板自行拉取）：
  - GET /minimax/director/style/presets → 7 预设完整 dict（纯规则零显存）
  - presets 拉取失败/未加载时，下拉显示当前 profile 兜底项，保证「默认抖音半写实漫剧」恒成立

配置（写 project.styleProfile，经 serializeProject 持久化，全剧统一）：
  - 下拉选择 7 预设之一 → 完整 profile dict 落盘（不是 profile_id，后端 style_block
    需要完整字段渲染画风块）
  - 「恢复默认」→ douyin_semi_realistic
  - 生成提交时 WorkbenchView 用 getStyleProfile() 取归一化后完整 dict，经
    buildH3Prompts 第 5 参透传 /prompt/h3 的 style_profile（画风块注入每镜 H3 Prompt）

⛔ 纯前端刷新即生效；styleProfile 存于 project.json 无需后端改动。
后端 /style/presets 需重启 Comfy Desktop 后可用（P3 #631 已交付）。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useWorkbench, getStyleProfile, setStyleProfile, resetStyleProfile } from "@/stores/workbench";
import {
  stylePresetOptions,
  styleProfileSummary,
  perSceneLabels,
} from "@/core/visualStyle";
import type { VisualStyleProfileJson } from "@/services/comfyApi";

// 与 BiblePanel/VoiceCastPanel 保持同一挂载签名（project-id 已由 WorkbenchView 传入；
// 面板自身从 store 读取当前项目，不需额外消费该 prop）。
defineProps<{
  projectId: string;
}>();

const wb = useWorkbench();

const collapsed = ref(false);

/** 后端预设库（GET /style/presets；失败保持空，下拉退化为当前 profile 兜底项）。 */
const presets = ref<Record<string, VisualStyleProfileJson>>({});
const presetsError = ref("");
const presetsLoading = ref(false);

/** 拉取预设库。失败显示但不断言（面板仍可读当前 profile / 恢复默认）。 */
async function loadPresets(): Promise<void> {
  presetsLoading.value = true;
  presetsError.value = "";
  try {
    const res = await wb.runService.api.fetchStylePresets();
    if (res && res.presets && typeof res.presets === "object" && Object.keys(res.presets).length) {
      presets.value = res.presets;
    } else {
      presetsError.value = "预设库为空（后端未就绪？）";
    }
  } catch (err) {
    presetsError.value = err instanceof Error ? err.message : String(err);
  } finally {
    presetsLoading.value = false;
  }
}

/** 当前 profile（getStyleProfile 归一化：无/缺省 → 默认抖音半写实漫剧完整副本写回）。 */
const profile = computed(() => getStyleProfile());

/** 当前 profile id。 */
const currentId = computed(() => profile.value.profile_id);

/** 下拉选项：7 预设（presets 权威）；当前项不在其中时（未加载/自定义）手动补一项。 */
const options = computed(() => {
  const list = stylePresetOptions(presets.value);
  const ids = new Set(list.map((o) => o.value));
  if (!ids.has(currentId.value)) {
    list.unshift({ value: currentId.value, label: profile.value.name || currentId.value });
  }
  return list;
});

/** 当前 profile 预览行（名称 → 画风总纲 → 四组关键词 → 写实度·色调）。 */
const summary = computed(() => styleProfileSummary(profile.value));

/** 场景级微调命中的场景名（面板提示用）。 */
const sceneLabels = computed(() => perSceneLabels(profile.value));

/** 下拉选中 → 完整 dict 落盘 project.styleProfile（touch → 保存）。 */
function onSelect(e: Event): void {
  const id = (e.target as HTMLSelectElement).value;
  const p = presets.value[id];
  if (p) setStyleProfile(p);
}

/** 恢复默认（抖音半写实漫剧）。 */
function onReset(): void {
  resetStyleProfile();
}

onMounted(() => {
  void loadPresets();
});
</script>

<template>
  <div class="vstyle-panel">
    <div class="vstyle-head" role="button" tabindex="0" @click="collapsed = !collapsed" @keydown.enter="collapsed = !collapsed">
      <span class="vstyle-title">🎨 Visual Style</span>
      <span
        class="vstyle-status"
        :class="{ off: presetsError }"
        :title="presetsLoading ? '加载预设库…' : presetsError ? '预设库加载失败（不影响当前画风与生成）' : `${Object.keys(presets).length} 预设`"
      >
        {{ presetsLoading ? "…" : presetsError ? "⚠ 离线" : `${Object.keys(presets).length} 预设` }}
      </span>
      <span class="vstyle-caret">{{ collapsed ? "▸" : "▾" }}</span>
      <button class="icon-btn vstyle-refresh" title="刷新预设库" :disabled="presetsLoading" @click.stop="loadPresets">🔄</button>
    </div>

    <template v-if="!collapsed">
      <div v-if="presetsError" class="vstyle-err">⚠ {{ presetsError }}（画风仍可读当前配置，恢复默认可用）</div>

      <!-- 预设下拉 -->
      <label class="vstyle-pick">
        <span class="vstyle-pick-label">预设</span>
        <select
          class="vstyle-select"
          :value="currentId"
          :disabled="presetsLoading"
          @change="onSelect"
        >
          <option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </label>

      <!-- 当前 profile 预览 -->
      <div class="vstyle-section-title">当前画风</div>
      <div class="vstyle-preview">
        <div v-for="(line, i) in summary" :key="i" class="vstyle-preview-line">{{ line }}</div>
      </div>
      <div v-if="sceneLabels.length" class="vstyle-scenes">
        场景级微调：<em>{{ sceneLabels.join(" · ") }}</em>
      </div>

      <div class="vstyle-actions">
        <button class="btn ghost" :disabled="currentId === 'douyin_semi_realistic' && !presetsError" @click="onReset">↺ 恢复默认</button>
      </div>

      <div class="vstyle-hint">
        全剧统一画风：所有镜头 H3 Prompt 自动注入本画风块；跨镜人物/光影/材质保持一致。改预设即换全剧画风。
      </div>
    </template>
  </div>
</template>

<style scoped>
.icon-btn {
  background: transparent;
  border: none;
  color: #8a8a9a;
  cursor: pointer;
  font-size: 12px;
  padding: 1px 4px;
  border-radius: 4px;
  line-height: 1;
  flex-shrink: 0;
}
.icon-btn:hover {
  color: #e8e8ee;
  background: #2a2a40;
}
.vstyle-panel {
  border-top: 1px dashed rgba(255, 255, 255, 0.14);
  margin-top: 10px;
  padding-top: 10px;
  font-size: 12px;
}
.vstyle-head {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
  margin-bottom: 6px;
}
.vstyle-title {
  font-weight: 700;
  color: #fff;
}
.vstyle-status {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  padding: 0 4px;
}
.vstyle-status.off {
  color: rgba(255, 160, 120, 0.75);
  border-color: rgba(255, 160, 70, 0.3);
}
.vstyle-caret {
  color: rgba(255, 255, 255, 0.45);
  font-size: 10px;
}
.vstyle-refresh {
  margin-left: auto;
}
.vstyle-err {
  color: #ff9d9d;
  background: rgba(255, 80, 80, 0.12);
  border: 1px solid rgba(255, 80, 80, 0.3);
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 8px;
  line-height: 1.4;
}
.vstyle-pick {
  display: flex;
  align-items: center;
  gap: 6px;
}
.vstyle-pick-label {
  flex: 0 0 34px;
  color: rgba(255, 255, 255, 0.6);
}
.vstyle-select {
  flex: 1;
  min-width: 0;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  color: #e8e8ee;
  font-size: 12px;
  padding: 3px 6px;
  box-sizing: border-box;
}
.vstyle-select:hover {
  border-color: rgba(122, 162, 247, 0.5);
}
.vstyle-section-title {
  color: rgba(255, 255, 255, 0.65);
  font-size: 11px;
  font-weight: 600;
  margin: 8px 0 4px;
}
.vstyle-preview {
  display: flex;
  flex-direction: column;
  gap: 3px;
  background: rgba(0, 0, 0, 0.22);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  padding: 6px 8px;
  max-height: 132px;
  overflow-y: auto;
}
.vstyle-preview-line {
  color: #dfe7ff;
  line-height: 1.5;
}
.vstyle-scenes {
  color: rgba(255, 255, 255, 0.55);
  font-size: 11px;
  line-height: 1.5;
  margin-top: 5px;
}
.vstyle-scenes em {
  font-style: normal;
  color: #ffd9a3;
}
.vstyle-actions {
  margin-top: 8px;
}
.btn {
  padding: 4px 12px;
  border-radius: 6px;
  border: none;
  cursor: pointer;
  background: #2a2a40;
  color: #c8c8d8;
  font-size: 12px;
}
.btn.ghost {
  background: transparent;
  border: 1px solid #3a3a55;
  color: #c8c8d8;
}
.btn.ghost:hover:not(:disabled) {
  border-color: #4a7dff;
  color: #cfe0ff;
}
.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.vstyle-hint {
  color: rgba(255, 255, 255, 0.4);
  font-size: 11px;
  line-height: 1.5;
  margin-top: 8px;
  border-top: 1px dashed rgba(255, 255, 255, 0.1);
  padding-top: 6px;
}
</style>
