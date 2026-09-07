<!--
  V1.3-C 任务中心：底部悬浮任务摘要 + 可展开面板。
  - 摘要行：运行中显示 范围/阶段/镜头进度/进度条；空闲显示 任务历史统计。
  - 面板：当前任务预览帧 + 最近任务列表（状态点/时间/范围/镜头数/prompt id/错误/打开成片）。
  - 「打开成片」把该任务的 finalVideo 喂给中央播放器（wb.finalVideo）。
-->
<template>
  <div class="task-center">
    <div class="tc-summary" :class="{ open: panelOpen }" @click="panelOpen = !panelOpen">
      <span v-if="activeTask" class="tc-spin">⚡</span>
      <span class="tc-title">
        {{ activeTask ? (activeTask.kind === "export" ? "导出中：" : "生成中：") + activeTask.scopeLabel : "任务中心" }}
      </span>
      <span v-if="activeTask?.progress || activeTask?.exportStage" class="tc-phase">
        <!-- V1.6-A 导出任务：编码 Shot x/y → 合并成片 -->
        <template v-if="activeTask.kind === 'export' && activeTask.exportStage">
          {{ exportStageLabel(activeTask) }}
        </template>
        <!-- 走查模式：currentIndex 非空 → 「🎬 走查队列 · 第 N/M 镜」；普通单次生成 → 「镜头 x/y」 -->
        <template v-else-if="activeTask.currentIndex != null">
          🎬 走查队列 · {{ activeTask.progress?.phaseLabel }} · 第 {{ activeTask.currentIndex + 1 }}/{{ activeTask.shotCount }} 镜
        </template>
        <template v-else>
          {{ activeTask.progress?.phaseLabel }} · 镜头 {{ activeTask.progress?.segment }}/{{ activeTask.progress?.segmentTotal }}
        </template>
      </span>
      <span v-else class="tc-idle">
        {{ tasks.length ? `共 ${tasks.length} 次 · 成功 ${doneCount} 次` : "空闲" }}
      </span>
      <div v-if="activeTask?.progress" class="tc-bar">
        <div class="tc-bar-fill" :style="{ width: barPct(activeTask.progress) + '%' }" />
      </div>
      <div v-else-if="activeTask?.kind === 'export' && activeTask.exportStage" class="tc-bar">
        <div class="tc-bar-fill" :style="{ width: exportPct(activeTask) + '%' }" />
      </div>
      <!-- V1.8-3 ETA：摘要行紧凑显示 已用 / 全片剩余（仅运行中且带预测表）。 -->
      <span v-if="eta" class="tc-eta" title="已用时间 · 全片预计剩余">
        ⏱ {{ fmtClock(eta.elapsedMs) }} · 剩 {{ fmtClock(eta.remainingMs) }}
      </span>
      <span v-if="wb.lastError" class="tc-err" title="最近错误">{{ shortErr }}</span>
      <span class="tc-caret">{{ panelOpen ? "▾" : "▴" }}</span>
    </div>

    <div v-if="panelOpen" class="tc-panel">
      <!-- V1.10-D 当前生成大卡：与下方走查队列/任务清单彻底分离（独立边框 + 大镜名 + 真实阶段步骤 + 预测依据）。 -->
      <div v-if="eta && activeTask?.genMeta" class="tc-eta-card">
        <div class="tc-eta-head">
          <span class="tc-eta-title">🎬 当前生成</span>
          <span class="tc-eta-total">预计总 {{ fmtClock(eta.estimatedTotalMs) }}</span>
        </div>
        <div class="tc-eta-rows">
          <div class="tc-eta-item"><em>已用</em><b>{{ fmtClock(eta.elapsedMs) }}</b></div>
          <div class="tc-eta-item"><em>本镜剩余</em><b>{{ fmtClock(eta.currentShotRemainingMs) }}</b></div>
          <div class="tc-eta-item"><em>全片剩余</em><b class="tc-eta-accent">{{ fmtClock(eta.remainingMs) }}</b></div>
        </div>
        <div class="tc-eta-shot">
          <div class="tc-eta-shot-head">
            <span class="tc-eta-shot-name">{{ currentShotLabel }}</span>
            <span v-if="confDot" class="tc-eta-conf-dot" :title="confLabel">{{ confDot }}</span>
            <span class="tc-eta-pct">{{ Math.round(eta.shotProgress * 100) }}%</span>
          </div>
          <div class="tc-eta-bar">
            <div class="tc-eta-bar-fill" :style="{ width: Math.round(eta.shotProgress * 100) + '%' }" />
          </div>
          <div class="tc-eta-shot-foot">
            <span>{{ phaseFor(activeTask) }}</span>
            <span v-if="eta.matchedTotal > 0" class="tc-eta-conf">
              {{ confDot }} 可信 {{ Math.round(eta.confidence * 100) }}% · 基于最近 {{ eta.matchedTotal }} 个已完成镜头
            </span>
            <span v-else class="tc-eta-conf tc-dim">○ 首次生成 · 经验估算</span>
          </div>
        </div>
      </div>
      <div v-if="activeTask?.preview" class="tc-preview">
        <img :src="activeTask.preview" alt="预览帧" />
        <span class="tc-preview-label">当前镜头预览帧</span>
      </div>
      <!-- V1.10-D 生成队列清单：与当前生成大卡分离；每镜带 耗时/预计剩余。 -->
      <div v-if="runningTask?.shotStates" class="tc-queue">
        <div class="tc-queue-title">
          <span>🎬 走查队列 · {{ runningTask.shotCount }} 镜</span>
          <span v-if="queueRemainingText" class="tc-queue-eta">{{ queueRemainingText }}</span>
        </div>
        <div class="tc-queue-list">
          <div v-for="(qst, sid) in runningTask.shotStates" :key="sid" class="tc-queue-item" :class="'tq-' + qst">
            <span class="tq-icon">{{ queueIcon(qst) }}</span>
            <span class="tq-name">{{ shotLabel(sid) }}</span>
            <span v-if="shotTimeText(qst, sid)" class="tq-time" :title="timeTitle(qst, sid)">{{ shotTimeText(qst, sid) }}</span>
            <span class="tq-state">{{ queueStateLabel(qst) }}</span>
          </div>
        </div>
      </div>
      <div class="tc-list">
        <div v-if="!tasks.length" class="tc-empty">
          还没有任务。<br />点顶部「▶ 生成」生成镜头，或「⭳ 导出」导出成片。
        </div>
        <div v-for="t in tasks" :key="t.id" class="tc-item" :class="'tc-' + t.status">
          <span class="tc-dot" :title="statusLabel(t.status)" />
          <span class="tc-kind" :class="'kind-' + t.kind">{{ t.kind === "export" ? "导出" : "生成" }}</span>
          <span class="tc-time">{{ fmtTime(t.startedAt) }}</span>
          <span class="tc-scope">{{ t.scopeLabel }}</span>
          <span class="tc-shots">{{ t.shotCount }} 镜</span>
          <span v-if="t.kind === 'export' && t.exportStage" class="tc-export-stage">{{ exportStageLabel(t) }}</span>
          <span v-if="t.promptId" class="tc-pid" :title="t.promptId">{{ shortPid(t.promptId) }}</span>
          <span v-if="t.error" class="tc-err" :title="t.error">⚠</span>
          <span class="tc-actions">
            <button
              v-if="t.status === 'running' && t.id === wb.currentTaskId && t.kind === 'generate'"
              class="tc-btn tc-cancel"
              title="中断当前生成（H3 正在跑的帧会被放弃，已生成的镜头保留）"
              @click.stop="cancel"
            >
              ⏹ 取消
            </button>
            <button
              v-if="t.status === 'failed'"
              class="tc-btn tc-detail"
              title="查看失败详情（原因 / 节点 / 后端 Report）"
              @click.stop="openDetail(t)"
            >
              🔍 详情
            </button>
            <button v-if="t.finalVideo" class="tc-btn" @click.stop="openVideo(t)">▶ 打开成片</button>
            <button
              v-if="t.kind === 'generate' && t.targetShotIds && t.targetShotIds.length"
              class="tc-btn"
              :disabled="wb.running"
              title="用当前参数重新生成该任务的镜头"
              @click.stop="regen(t)"
            >
              ↻ 重新生成
            </button>
          </span>
        </div>
      </div>
    </div>

    <!-- V1.4-P0 失败详情弹窗：失败镜头 / 失败原因 / 任务信息 / 后端 Report（折叠） -->
    <div v-if="detailTask" class="tc-modal" @click.self="detailTask = null">
      <div class="tc-modal-card">
        <div class="tc-modal-head">
          <span class="tc-modal-title">⚠ {{ detailTitle }}</span>
          <button class="tc-btn tc-modal-close" title="关闭" @click="detailTask = null">✕</button>
        </div>
        <div class="tc-modal-body">
          <div class="tc-sec">
            <div class="tc-sec-label">失败镜头</div>
            <div v-if="detailFailedShots.length" class="tc-sec-row">
              <span v-for="sid in detailFailedShots" :key="sid" class="tc-badge">{{ shotLabel(sid) }}</span>
            </div>
            <div v-else class="tc-sec-row tc-dim">未能定位到具体镜头（异常信息未携带段号）</div>
          </div>
          <div class="tc-sec">
            <div class="tc-sec-label">失败原因</div>
            <pre class="tc-reason">{{ detailTask.errorDetail || detailTask.error || "未知错误" }}</pre>
          </div>
          <div class="tc-sec">
            <div class="tc-sec-label">任务信息</div>
            <div class="tc-meta">
              <span class="tc-meta-item"><em>Prompt ID</em><code>{{ detailTask.promptId || "—" }}</code></span>
              <span class="tc-meta-item"><em>Node ID</em><code>{{ detailTask.nodeId || "—" }}</code></span>
              <span class="tc-meta-item"><em>开始时间</em><code>{{ fmtTime(detailTask.startedAt) }}</code></span>
            </div>
          </div>
          <div v-if="detailTask.report" class="tc-sec">
            <div class="tc-sec-label tc-sec-label-row">
              <span>后端诊断</span>
              <span class="tc-sec-actions">
                <button class="tc-btn" @click="reportOpen = !reportOpen">
                  {{ reportOpen ? "收起" : "▶ 查看完整 Report" }}
                </button>
                <button class="tc-btn" title="复制 Report 全文" @click="copyReport">⧉ 复制</button>
              </span>
            </div>
            <pre v-if="reportOpen" class="tc-report">{{ detailTask.report }}</pre>
          </div>
        </div>
        <div class="tc-modal-foot">
          <button class="tc-btn" @click="detailTask = null">关闭</button>
          <button
            v-if="detailTask.kind === 'generate' && detailTask.targetShotIds && detailTask.targetShotIds.length"
            class="tc-btn tc-btn-primary"
            :disabled="wb.running"
            title="用当前参数重新生成该任务的镜头"
            @click="regen(detailTask)"
          >
            ↻ 重新生成
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useWorkbench, type TaskRecord } from "@/stores/workbench";
import {
  CONF_LEVEL_DOT,
  CONF_LEVEL_LABEL,
  computeRunEta,
  confidenceLevel,
  formatEtaMs,
  lastMsForShot,
  phaseStepLabel,
  type GenHistoryRecord,
  type RunEtaSnapshot,
} from "@/core/generationEta";

const wb = useWorkbench();
const panelOpen = ref(false);
/** V1.8-3 ETA：每秒心跳，驱动已用/剩余实时刷新。 */
const nowTick = ref(Date.now());
let etaTimer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  etaTimer = setInterval(() => {
    nowTick.value = Date.now();
  }, 1000);
});
onBeforeUnmount(() => {
  if (etaTimer) clearInterval(etaTimer);
  etaTimer = null;
});
/** V1.8-3 ETA：活动任务（running）的 ETA 快照；非运行/导出任务为 null。 */
const eta = computed<RunEtaSnapshot | null>(() => {
  const t = activeTask.value;
  if (!t?.genMeta) return null;
  return computeRunEta(t.genMeta, t.progress, t.currentIndex, t.startedAt, nowTick.value);
});
/** V1.8-3 ETA：当前镜展示名（场景 · 镜头名）。 */
const currentShotLabel = computed(() => {
  const t = activeTask.value;
  if (!t?.genMeta || !eta.value) return "";
  const s = t.genMeta.shots[eta.value.shotIndex];
  return s ? shotLabel(s.id) : "";
});
/** 时间格式化别名（mm:ss / h:mm:ss）。 */
const fmtClock = formatEtaMs;
/** V1.4-P0：失败详情弹窗当前展示的任务（null=关闭）。 */
const detailTask = ref<TaskRecord | null>(null);
/** Report 折叠状态（默认收起）。 */
const reportOpen = ref(false);
const emit = defineEmits<{
  /** 重新生成：携带原任务目标镜头 id（null=全部）。 */
  (e: "regen", targetShotIds: string[] | null): void;
  /** 取消当前生成（发 /interrupt 中断 ComfyUI 当前运行）。 */
  (e: "cancel"): void;
}>();
/** V1.10-C：顶栏横幅「查看任务详情」信号（+1 打开面板）。 */
/** V1.10-D：接收 ETA 历史样本（WorkbenchView 每秒刷新传入），供队列每镜实测耗时查询。 */
const props = defineProps<{ openSignal?: number; history?: GenHistoryRecord[] }>();
watch(
  () => props.openSignal ?? 0,
  (v, prev) => {
    if (v > (prev ?? 0)) panelOpen.value = true;
  },
);

/** V1.10-C：当前 ETA 可信度三级圆点/说明（●稳定 ◐收敛 ○数据不足）。 */
const confLevel = computed(() => (eta.value ? confidenceLevel(eta.value.confidence) : 0));
const confDot = computed(() => CONF_LEVEL_DOT[confLevel.value]);
const confLabel = computed(() => CONF_LEVEL_LABEL[confLevel.value]);

/** V1.10-D：活动任务真实阶段文案（后端 phase_max>1 时显示「phaseLabel · Step v/max」）。 */
function phaseFor(t: TaskRecord | null): string {
  return t?.progress ? phaseStepLabel(t.progress) : "准备中…";
}

/** V1.10-D：走查队列整体预计剩余（当前镜剩余 + 后续镜预测；无 ETA 时为空）。 */
const queueRemainingText = computed(() => {
  if (!runningTask.value?.shotStates || !eta.value) return "";
  return `预计剩余 ${formatEtaMs(eta.value.remainingMs)}`;
});

/** V1.10-D：按镜 id 查当前任务 genMeta 的帧数/任务类型（无则 null）。 */
function genMetaOf(sid: string): { frames: number; taskType: string } | null {
  const gm = activeTask.value?.genMeta;
  if (!gm) return null;
  const s = gm.shots.find((x) => x.id === sid);
  return s ? { frames: s.frames, taskType: s.taskType } : null;
}

/** V1.10-D：队列每镜耗时——完成镜显示最近一次实测；待生成/生成中显示预测值。 */
function shotTimeText(qst: string, sid: string): string {
  const gm = genMetaOf(sid);
  const meta = activeTask.value?.genMeta;
  if (!gm || !meta) return "";
  if (qst === "done") {
    const ms = lastMsForShot(props.history ?? [], {
      taskType: gm.taskType,
      frames: gm.frames,
      width: meta.width,
      height: meta.height,
      projectId: wb.project?.id ?? "",
    });
    return ms != null ? `⏱ ${formatEtaMs(ms)}` : "";
  }
  const s = meta.shots.find((x) => x.id === sid);
  return s ? `预计 ${formatEtaMs(s.predictedMs)}` : "";
}

/** V1.10-D：队列每镜耗时悬停说明（实测 vs 预测依据）。 */
function timeTitle(qst: string, sid: string): string {
  const gm = genMetaOf(sid);
  if (!gm) return "";
  if (qst === "done") return "最近一次成功生成实测耗时";
  const s = activeTask.value?.genMeta?.shots.find((x) => x.id === sid);
  return s ? `按预计生成（可信 ${Math.round(s.confidence * 100)}% · ${s.matchedCount} 条样本）` : "预计生成耗时";
}

const tasks = computed(() => wb.tasks);
/** 当前任务（currentTaskId 指向的最新任务；结束后仍指向该任务，供回看队列/预览）。 */
const runningTask = computed(() =>
  wb.currentTaskId ? wb.tasks.find((t) => t.id === wb.currentTaskId) ?? null : null,
);
/** 活动任务（正在 running 的任务；无运行任务为 null —— 摘要行「生成中」/进度条只认这个）。 */
const activeTask = computed(() =>
  wb.currentTaskId ? wb.tasks.find((t) => t.id === wb.currentTaskId && t.status === "running") ?? null : null,
);
const doneCount = computed(() => wb.tasks.filter((t) => t.status === "done").length);
const shortErr = computed(() => (wb.lastError ? `⚠ ${wb.lastError.slice(0, 24)}${wb.lastError.length > 24 ? "…" : ""}` : ""));

/** 走查队列状态图标。 */
function queueIcon(s: string): string {
  return s === "done" ? "✓" : s === "running" ? "▶" : s === "failed" ? "✗" : s === "cancelled" ? "–" : "○";
}

/** 走查队列状态文案。 */
function queueStateLabel(s: string): string {
  return s === "done" ? "完成" : s === "running" ? "生成中" : s === "failed" ? "失败" : s === "cancelled" ? "已取消" : "待生成";
}

/** V1.4-P0：弹窗内失败镜头 id 列表。 */
const detailFailedShots = computed(() => detailTask.value?.failedShotIds ?? []);
/** 弹窗标题：有失败镜头就带名字，否则泛化。 */
const detailTitle = computed(() => {
  const t = detailTask.value;
  if (!t) return "";
  const names = t.failedShotIds.map(shotLabel).filter(Boolean);
  return names.length ? `生成失败：${names.join("、")}` : "生成失败";
});

/** 按镜头 id 在当前项目里查名字（找不到返回 null）。 */
function shotInfo(id: string): { name: string; sceneName: string } | null {
  const ep = wb.project?.episodes[0];
  for (const sc of ep?.scenes ?? []) {
    const s = sc.shots.find((x) => x.id === id);
    if (s) return { name: s.name ?? s.id, sceneName: sc.name ?? sc.id };
  }
  return null;
}

/** 镜头标签：场景 · 镜头名；查不到就用原始 id。 */
function shotLabel(id: string): string {
  const info = shotInfo(id);
  return info ? `${info.sceneName} · ${info.name}` : id;
}

function openDetail(t: TaskRecord) {
  detailTask.value = t;
  reportOpen.value = false;
}

/** 复制 Report 全文（Clipboard API 不可用时降级 textarea 法）。 */
async function copyReport() {
  const text = detailTask.value?.report;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
}

function barPct(p: NonNullable<TaskRecord["progress"]>): number {
  const max = p.overallMax > 0 ? p.overallMax : 1;
  return Math.max(0, Math.min(100, (p.overallValue / max) * 100));
}

/** V1.6-A 导出任务阶段文案：编码 Shot x/y → 合并成片。 */
function exportStageLabel(t: TaskRecord): string {
  const st = t.exportStage;
  if (!st) return "";
  return st.encoding >= st.total ? "合并成片" : `编码 Shot ${String(st.encoding).padStart(2, "0")}/${st.total}`;
}

/** V1.6-A 导出任务进度条：编码 N 镜 + 合并 = N+1 步（客户端估算）。 */
function exportPct(t: TaskRecord): number {
  const st = t.exportStage;
  if (!st) return 0;
  const steps = st.total + 1;
  return Math.max(0, Math.min(100, Math.round((st.encoding / steps) * 100)));
}

function statusLabel(s: TaskRecord["status"]): string {
  return s === "done" ? "完成" : s === "failed" ? "失败" : s === "cancelled" ? "已取消" : "运行中";
}

function fmtTime(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function shortPid(pid: string): string {
  return pid.length > 12 ? `${pid.slice(0, 12)}…` : pid;
}

/** 打开成片：把任务 finalVideo 喂给中央播放器，并收起面板。 */
function openVideo(t: TaskRecord) {
  if (t.finalVideo) wb.finalVideo = { url: t.finalVideo.url, filename: t.finalVideo.filename };
  panelOpen.value = false;
}

/** 取消当前生成：交给上层（WorkbenchView.onTaskCancel → runService.interrupt）。 */
function cancel() {
  emit("cancel");
  panelOpen.value = false;
}

/** 重新生成：把原任务目标镜头 id 交给上层（WorkbenchView.handleGenerate）。 */
function regen(t: TaskRecord) {
  detailTask.value = null;
  emit("regen", t.targetShotIds);
  panelOpen.value = false;
}
</script>

<style scoped>
.task-center {
  position: relative;
  flex: 1;
  min-width: 0;
}
.tc-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  padding: 4px 2px;
  font-size: 12px;
  color: #9a9ab0;
  user-select: none;
}
.tc-summary:hover {
  color: #cfcfe0;
}
.tc-spin {
  animation: tc-spin 1.6s linear infinite;
  display: inline-block;
}
@keyframes tc-spin {
  to {
    transform: rotate(360deg);
  }
}
.tc-title {
  color: #e8e8ee;
  font-weight: 600;
  white-space: nowrap;
}
.tc-phase {
  color: #4a7dff;
  white-space: nowrap;
}
.tc-idle {
  color: #8a8aa0;
  white-space: nowrap;
}
.tc-bar {
  flex: 1;
  max-width: 240px;
  height: 6px;
  background: #2a2a3e;
  border-radius: 3px;
  overflow: hidden;
}
.tc-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #4a7dff, #7aa2ff);
  border-radius: 3px;
  transition: width 0.2s;
}
.tc-caret {
  color: #6a6a80;
}
/* V1.8-3 ETA：摘要行紧凑时钟 */
.tc-eta {
  color: #7aa2ff;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.tc-eta-card {
  margin-bottom: 8px;
  border: 1px solid rgba(74, 125, 255, 0.35);
  background: linear-gradient(180deg, rgba(74, 125, 255, 0.1), rgba(26, 26, 40, 0.4));
  border-radius: 8px;
  padding: 8px 10px;
}
.tc-eta-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.tc-eta-title {
  color: #9ab8ff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.tc-eta-total {
  color: #8a8aa0;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.tc-eta-rows {
  display: flex;
  gap: 14px;
  margin-bottom: 8px;
}
.tc-eta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tc-eta-item em {
  font-style: normal;
  color: #6a6a80;
  font-size: 10px;
}
.tc-eta-item b {
  color: #cfcfe0;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.tc-eta-item b.tc-eta-accent {
  color: #7aa2ff;
}
.tc-eta-shot-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.tc-eta-shot-name {
  color: #cfcfe0;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}
/* V1.10-C：可信度三级圆点（●稳定 ◐收敛 ○数据不足）。 */
.tc-eta-conf-dot {
  color: #7aa2ff;
  font-size: 12px;
  flex-shrink: 0;
  cursor: help;
}
.tc-eta-pct {
  color: #7aa2ff;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.tc-eta-bar {
  height: 6px;
  background: #2a2a3e;
  border-radius: 3px;
  overflow: hidden;
}
.tc-eta-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #4a7dff, #8ab2ff);
  border-radius: 3px;
  transition: width 0.3s;
}
.tc-eta-shot-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 5px;
  color: #8a8aa0;
  font-size: 11px;
}
.tc-eta-conf {
  color: #6ecf8a;
  font-variant-numeric: tabular-nums;
}
.tc-eta-conf.tc-dim {
  color: #6a6a80;
}
.tc-panel {
  position: absolute;
  right: 0;
  bottom: calc(100% + 10px);
  width: 430px;
  max-width: calc(100vw - 32px);
  background: #1c1c2b;
  border: 1px solid #26263a;
  border-radius: 10px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5);
  padding: 10px;
  z-index: 40;
}
.tc-preview {
  position: relative;
  margin-bottom: 8px;
  border-radius: 6px;
  overflow: hidden;
}
.tc-preview img {
  width: 100%;
  max-height: 150px;
  object-fit: cover;
  display: block;
}
.tc-preview-label {
  position: absolute;
  left: 6px;
  bottom: 6px;
  background: rgba(0, 0, 0, 0.6);
  color: #cfcfe0;
  font-size: 11px;
  border-radius: 4px;
  padding: 2px 6px;
}
.tc-list {
  max-height: 280px;
  overflow-y: auto;
}
/* V1.5 #104：走查队列每镜状态清单 */
.tc-queue {
  margin-bottom: 8px;
  border: 1px solid #2a2a40;
  border-radius: 8px;
  background: #16161f;
  padding: 6px 8px;
}
.tc-queue-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #7aa2ff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}
.tc-queue-eta {
  color: #8a8aa0;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}
.tc-queue-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 200px;
  overflow-y: auto;
}
.tc-queue-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 6px;
  border-radius: 5px;
  font-size: 12px;
}
.tc-queue-item.tq-running { background: rgba(74, 125, 255, 0.12); }
.tc-queue-item.tq-failed { background: rgba(255, 107, 107, 0.1); }
.tq-icon { width: 16px; text-align: center; flex-shrink: 0; font-size: 11px; }
.tq-done .tq-icon { color: #3ecf6e; }
.tq-running .tq-icon { color: #4a7dff; animation: tc-pulse 1s infinite; }
.tq-failed .tq-icon { color: #ff6b6b; }
.tq-cancelled .tq-icon { color: #9a9ab0; }
.tq-pending .tq-icon { color: #6a6a80; }
.tq-name { color: #cfcfe0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* V1.10-D：队列每镜耗时/预计（tabular-nums 对齐，弱化色避免与状态抢眼）。 */
.tq-time {
  margin-left: auto;
  color: #6a6a80;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  flex-shrink: 0;
  cursor: help;
}
.tq-done .tq-time { color: #5a8a6a; }
.tq-running .tq-time { color: #7aa2ff; }
.tq-state { color: #8a8aa0; font-size: 11px; flex-shrink: 0; }
.tq-done .tq-state { color: #3ecf6e; }
.tq-running .tq-state { color: #7aa2ff; }
.tq-failed .tq-state { color: #ff8a8a; }
.tc-empty {
  color: #6a6a80;
  font-size: 12px;
  padding: 8px 4px;
  line-height: 1.6;
}
.tc-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 4px;
  border-bottom: 1px solid #23233a;
  font-size: 12px;
}
.tc-item:last-child {
  border-bottom: none;
}
.tc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.tc-done .tc-dot {
  background: #3ecf6e;
}
.tc-failed .tc-dot {
  background: #ff6b6b;
}
.tc-cancelled .tc-dot {
  background: #9a9ab0;
}
.tc-running .tc-dot {
  background: #4a7dff;
  animation: tc-pulse 1s infinite;
}
@keyframes tc-pulse {
  50% {
    opacity: 0.4;
  }
}
.tc-time {
  color: #6a6a80;
  font-variant-numeric: tabular-nums;
}
.tc-scope {
  color: #cfcfe0;
}
.tc-shots {
  color: #8a8aa0;
}
/* V1.6-A 任务类型徽标（生成/导出） */
.tc-kind {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}
.tc-kind.kind-generate {
  background: rgba(74, 125, 255, 0.14);
  color: #9ab8ff;
  border: 1px solid rgba(74, 125, 255, 0.3);
}
.tc-kind.kind-export {
  background: rgba(62, 207, 110, 0.12);
  color: #6ecf8a;
  border: 1px solid rgba(62, 207, 110, 0.3);
}
.tc-export-stage {
  color: #7aa2ff;
  font-size: 11px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.tc-pid {
  color: #5a5a75;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px;
}
.tc-err {
  color: #ff9f43;
}
.tc-actions {
  margin-left: auto;
}
.tc-btn {
  background: #26263a;
  border: 1px solid #33334d;
  color: #cfcfe0;
  border-radius: 5px;
  padding: 2px 8px;
  font-size: 11px;
  cursor: pointer;
}
.tc-btn:hover {
  background: #33334d;
  color: #fff;
}
.tc-btn.tc-cancel {
  border-color: #6b3a3a;
  color: #ff8a8a;
}
.tc-btn.tc-cancel:hover {
  background: #4a2b2b;
  color: #ffb3b3;
}
.tc-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.tc-btn:disabled:hover {
  background: #26263a;
  color: #cfcfe0;
}
.tc-btn.tc-detail {
  border-color: #6b5a2a;
  color: #ffcf7a;
}
.tc-btn.tc-detail:hover {
  background: #4a3f2b;
  color: #ffe0a8;
}
.tc-btn.tc-btn-primary {
  border-color: #3a5a8a;
  color: #9fc0ff;
}
.tc-btn.tc-btn-primary:hover {
  background: #2f3f6b;
  color: #cfe0ff;
}

/* V1.4-P0 失败详情弹窗 */
.tc-modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.tc-modal-card {
  width: 640px;
  max-width: calc(100vw - 48px);
  max-height: min(80vh, 720px);
  background: #1c1c2b;
  border: 1px solid #33334d;
  border-radius: 12px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.tc-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #26263a;
}
.tc-modal-title {
  color: #ff9f9f;
  font-size: 14px;
  font-weight: 700;
}
.tc-modal-close {
  border: none;
  background: transparent;
  font-size: 14px;
  padding: 2px 8px;
}
.tc-modal-body {
  padding: 12px 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.tc-sec {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tc-sec-label {
  color: #8a8aa0;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.tc-sec-label-row {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
}
.tc-sec-actions {
  display: flex;
  gap: 6px;
}
.tc-sec-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tc-dim {
  color: #6a6a80;
  font-size: 12px;
}
.tc-badge {
  background: #2b2b40;
  border: 1px solid #3a3a58;
  color: #cfcfe0;
  border-radius: 5px;
  padding: 2px 8px;
  font-size: 12px;
  white-space: nowrap;
}
.tc-reason {
  margin: 0;
  background: #16161f;
  border: 1px solid #2b2b40;
  border-radius: 6px;
  padding: 10px 12px;
  color: #ffb3a8;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow-y: auto;
}
.tc-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 20px;
}
.tc-meta-item {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #8a8aa0;
  font-size: 12px;
}
.tc-meta-item em {
  font-style: normal;
  color: #6a6a80;
}
.tc-meta-item code {
  background: #16161f;
  border: 1px solid #2b2b40;
  border-radius: 4px;
  padding: 1px 6px;
  color: #cfcfe0;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px;
}
.tc-report {
  margin: 0;
  background: #16161f;
  border: 1px solid #2b2b40;
  border-radius: 6px;
  padding: 10px 12px;
  color: #b8b8cc;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}
.tc-modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid #26263a;
}
</style>
