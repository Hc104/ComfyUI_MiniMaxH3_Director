<!--
Phase 2-C（#575）：Voice Cast 全局音色面板（左侧栏「📍 地点库 / 📖 Bible」下方）。

数据（只读数据源）：
  - GET /minimax/director/tts/voices → 引擎可用音色（Phase 0 = Edge-TTS 中文静态表）
  - project.plan（ProductionPlan）→ collectSpeakers 全 plan 角色名（建角色行）

配置（写 project.voiceCast，经 serializeProject 持久化，100 章自动继承）：
  - 三个固定音色行：旁白 / 系统音 / 系统音（电子）
  - 角色 → 音色映射行：每角色一个下拉；「自动」= 规则兜底（voice_净化角色名）
  - 行级微调（单句改音色/情绪/语速）在 Workbench 镜头对白列表（#576），不在此面板。

⛔ 纯前端刷新即生效；voiceCast 存于 project.json 无需后端改动。
后端 /tts/voices 需重启 Comfy Desktop 后可用（Phase 0 #556 已交付）。
-->
<script setup lang="ts">
import { computed, ref } from "vue";
import {
  useWorkbench,
  getVoiceCast,
  setVoiceCastEntry,
  setVoiceCastNarrator,
  setVoiceCastSystem,
  setVoiceCastSystemElectronic,
  setVoiceCastEnabled,
} from "@/stores/workbench";
import {
  collectSpeakers,
  buildCharacterVoiceOptions,
  buildFixedVoiceOptions,
  withCustomVoiceOption,
} from "@/core/voiceCast";
import type { TtsVoiceInfo } from "@/services/comfyApi";

const props = defineProps<{
  projectId: string;
  /** 可用音色（GET /tts/voices，由 WorkbenchView 统一加载传入）。 */
  voices?: TtsVoiceInfo[];
  voicesError?: string;
  voicesLoading?: boolean;
}>();

const emit = defineEmits<{ (e: "refresh"): void }>();

const wb = useWorkbench();

const collapsed = ref(false);

const nativeOptions = computed(() => props.voices ?? []);

/** 角色行下拉选项：「自动」+ 语义 ID + 引擎原生音色。 */
const characterOptions = computed(() => buildCharacterVoiceOptions(nativeOptions.value));

/** 固定音色行下拉选项：语义 ID + 引擎原生音色（无「自动」，默认即语义 ID）。 */
const fixedOptions = computed(() => buildFixedVoiceOptions(nativeOptions.value));

const cast = computed(() => getVoiceCast());

/** 音频总开关状态（Phase 2-G，2026-08-17）：false = 音频暂停（生成视频不自动配音）。 */
const castEnabled = computed<boolean>(() => cast.value?.enabled ?? false);

/** 头部开关按钮：翻转音频总开关。 */
function toggleEnabled(): void {
  setVoiceCastEnabled(!castEnabled.value);
}

/** 角色名列表（来自 plan 全部对话行，去重保序）。 */
const speakers = computed<string[]>(() => collectSpeakers(wb.project?.plan));

/** 当前角色条目已显式配置的数量（统计用）。 */
const mappedCount = computed<number>(() => {
  const vc = cast.value;
  return vc ? Object.keys(vc.entries).filter((k) => vc.entries[k]).length : 0;
});
</script>

<template>
  <div class="vcast-panel">
    <div class="vcast-head" role="button" tabindex="0" @click="collapsed = !collapsed" @keydown.enter="collapsed = !collapsed">
      <span class="vcast-title">🎙 Voice Cast</span>
      <!-- Phase 2-G（#598）：音频总开关。关 = 生成视频不自动配音/混音（Edge-TTS 不启动）。 -->
      <button
        class="vcast-toggle"
        :class="{ on: castEnabled }"
        :title="castEnabled ? '音频已开启：每镜 H3 完成后自动配音 + ducking 混音' : '音频已暂停：生成视频不自动配音（点击开启）'"
        @click.stop="toggleEnabled"
      >
        {{ castEnabled ? "音频开" : "音频关" }}
      </button>
      <span
        class="vcast-engine"
        :class="{ off: !castEnabled }"
        :title="castEnabled ? (props.voicesLoading ? '加载音色…' : props.voicesError ? '音色列表加载失败（不影响配置，保存自动继承）' : '当前引擎') : '音频已暂停（Edge-TTS 不启动）'"
      >
        {{ castEnabled ? (props.voicesLoading ? "…" : props.voicesError ? "⚠ 离线" : "edge-tts") : "已关闭" }}
      </span>
      <span class="vcast-caret">{{ collapsed ? "▸" : "▾" }}</span>
      <button class="icon-btn vcast-refresh" title="刷新音色列表" :disabled="props.voicesLoading" @click.stop="emit('refresh')">🔄</button>
    </div>

    <template v-if="!collapsed">
      <div v-if="!castEnabled" class="vcast-paused">
        🛑 音频已暂停 · 生成视频时不自动配音/混音（Edge-TTS 不启动）。点头部「音频开」随时恢复；音色配置已保留。
      </div>
      <div v-if="props.voicesError" class="vcast-err">⚠ {{ props.voicesError }}（音色列表读取失败不影响已配置音色保存）</div>

      <!-- 固定音色：旁白 / 系统 / 系统电子 -->
      <div class="vcast-section-title">固定音色</div>
      <div class="vcast-fixed">
        <label class="vcast-fixed-row">
          <span class="vcast-fixed-name">旁白</span>
          <select
            class="vcast-select"
            :value="cast?.narratorVoice ?? 'voice_narrator'"
            @change="setVoiceCastNarrator(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="o in withCustomVoiceOption(fixedOptions, cast?.narratorVoice ?? 'voice_narrator')" :key="o.value" :value="o.value">
              {{ o.label }}
            </option>
          </select>
        </label>
        <label class="vcast-fixed-row">
          <span class="vcast-fixed-name">系统音</span>
          <select
            class="vcast-select"
            :value="cast?.systemVoice ?? 'voice_system'"
            @change="setVoiceCastSystem(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="o in withCustomVoiceOption(fixedOptions, cast?.systemVoice ?? 'voice_system')" :key="o.value" :value="o.value">
              {{ o.label }}
            </option>
          </select>
        </label>
        <label class="vcast-fixed-row">
          <span class="vcast-fixed-name">系统·电子</span>
          <select
            class="vcast-select"
            :value="cast?.systemElectronic ?? 'voice_system_electronic'"
            @change="setVoiceCastSystemElectronic(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="o in withCustomVoiceOption(fixedOptions, cast?.systemElectronic ?? 'voice_system_electronic')" :key="o.value" :value="o.value">
              {{ o.label }}
            </option>
          </select>
        </label>
      </div>

      <!-- 角色 → 音色映射 -->
      <div class="vcast-section-title">
        角色映射<em>{{ mappedCount }}/{{ speakers.length }}</em>
      </div>
      <div v-if="speakers.length" class="vcast-cast">
        <label v-for="spk in speakers" :key="spk" class="vcast-cast-row">
          <span class="vcast-cast-name" :title="`说话人「${spk}」`">{{ spk }}</span>
          <select
            class="vcast-select"
            :value="cast?.entries[spk] ?? ''"
            @change="setVoiceCastEntry(spk, ($event.target as HTMLSelectElement).value)"
          >
            <option v-for="o in withCustomVoiceOption(characterOptions, cast?.entries[spk] ?? '')" :key="o.value" :value="o.value">
              {{ o.label }}
            </option>
          </select>
        </label>
      </div>
      <div v-else class="vcast-empty">
        暂无角色 · 用「📖 导入小说」建立制作计划后，说话人自动出现在这里
      </div>

      <div class="vcast-hint">
        全局音色跨章继承（100 章声音稳定）；单句改音色/情绪/语速请在镜头对白列表微调。
      </div>
    </template>
  </div>
</template>

<style scoped>
.btn {
  padding: 6px 14px;
  border-radius: 6px;
  border: none;
  cursor: pointer;
  background: #2a2a40;
  color: #c8c8d8;
}
.btn.ghost {
  background: transparent;
  border: 1px solid #3a3a55;
  color: #c8c8d8;
}
.btn.ghost:hover {
  border-color: #4a7dff;
  color: #cfe0ff;
}
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
.vcast-toggle {
  font-size: 10px;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(255, 90, 90, 0.18);
  color: #ffb0b0;
  cursor: pointer;
  flex-shrink: 0;
}
.vcast-toggle:hover {
  border-color: rgba(255, 140, 140, 0.6);
}
.vcast-toggle.on {
  background: rgba(80, 220, 140, 0.16);
  color: #9fe8bd;
  border-color: rgba(120, 240, 170, 0.35);
}
.vcast-toggle.on:hover {
  border-color: rgba(150, 250, 190, 0.6);
}
.vcast-paused {
  color: #ffc9a3;
  background: rgba(255, 160, 70, 0.12);
  border: 1px solid rgba(255, 160, 70, 0.3);
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 8px;
  line-height: 1.5;
}
.vcast-panel {
  border-top: 1px dashed rgba(255, 255, 255, 0.14);
  margin-top: 10px;
  padding-top: 10px;
  font-size: 12px;
}
.vcast-head {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
  margin-bottom: 6px;
}
.vcast-title {
  font-weight: 700;
  color: #fff;
}
.vcast-engine {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  padding: 0 4px;
}
.vcast-engine.off {
  color: rgba(255, 160, 120, 0.75);
  border-color: rgba(255, 160, 70, 0.3);
}
.vcast-caret {
  color: rgba(255, 255, 255, 0.45);
  font-size: 10px;
}
.vcast-refresh {
  margin-left: auto;
}
.vcast-err {
  color: #ff9d9d;
  background: rgba(255, 80, 80, 0.12);
  border: 1px solid rgba(255, 80, 80, 0.3);
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 8px;
  line-height: 1.4;
}
.vcast-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: rgba(255, 255, 255, 0.65);
  font-size: 11px;
  font-weight: 600;
  margin: 8px 0 4px;
}
.vcast-section-title em {
  font-style: normal;
  font-weight: 400;
  color: rgba(255, 255, 255, 0.4);
}
.vcast-fixed,
.vcast-cast {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.vcast-fixed-row,
.vcast-cast-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.vcast-fixed-name,
.vcast-cast-name {
  color: #dfe7ff;
  min-width: 0;
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.vcast-fixed-name {
  flex: 0 0 64px;
}
.vcast-cast-name {
  flex: 0 0 72px;
  font-size: 12px;
}
.vcast-select {
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
.vcast-select:hover {
  border-color: rgba(122, 162, 247, 0.5);
}
.vcast-empty {
  color: rgba(255, 255, 255, 0.45);
  padding: 6px 2px;
  line-height: 1.5;
}
.vcast-hint {
  color: rgba(255, 255, 255, 0.4);
  font-size: 11px;
  line-height: 1.5;
  margin-top: 8px;
  border-top: 1px dashed rgba(255, 255, 255, 0.1);
  padding-top: 6px;
}
</style>
