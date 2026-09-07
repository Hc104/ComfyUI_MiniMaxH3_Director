<!--
P2-P5（#542）：Global Story Bible 面板（左侧栏「📍 地点库」下方）。

数据：GET /minimax/director/bible?project_id=X → 项目级跨章世界状态库（后端 bible.json
权威）。仅展示 + 确认类操作，绝不写 project.json（四层不混：Bible=世界知识层）。

内容：人物 / 地点 / 道具 / 事件 四组 tab；每条目卡片 = 名称 + ✓已确认/◌候选 徽标 +
source 徽标（AI/规则/人工）+ 别名 chips + 当前状态 + 最后出现 + 静态属性（折叠，可编辑）+
历史（折叠）。

操作（全部走 POST /bible/confirm 写回 bible.json，返回全量 Bible 就地刷新）：
  - ✓ 确认候选      → action=confirm（candidate → confirmed，attributes 锁定）
  - ✓ 采纳属性建议   → action=accept_attribute（attributes 并入 + suggestion 移除）
  - ✎ 编辑静态属性   → 本地编辑 → action=edit_attributes（空值剔除，source=user）
  - ＋ 别名         → action=merge_alias（归一去重追加）
  - ＋ 新建条目      → POST /bible/entry（source=user，人工录入当前组实体）

⛔ 铁律：AI 只提候选，面板确认后才锁定；编辑只动本条目 attributes，不碰其它字段/其它条目。
纯前端刷新即生效；/bible 三路由需重启 Comfy Desktop 后可用（后端 P2-P4 #541）。
-->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useWorkbench } from "@/stores/workbench";
import type { BibleEntryJson, StoryBibleJson } from "@/services/comfyApi";

const props = defineProps<{ projectId: string }>();

const wb = useWorkbench();

const bible = ref<StoryBibleJson | null>(null);
const loading = ref(false);
const error = ref("");
const activeType = ref<BibleEntryJson["entity_type"]>("character");

/** 四组分类（与后端 BIBLE_ENTITY_TYPES 对齐）。 */
const TYPES: { key: BibleEntryJson["entity_type"]; label: string; icon: string }[] = [
  { key: "character", label: "人物", icon: "👤" },
  { key: "location", label: "地点", icon: "📍" },
  { key: "prop", label: "道具", icon: "🎭" },
  { key: "event", label: "事件", icon: "📜" },
];

const allEntries = computed(() => bible.value?.entries ?? []);
const activeEntries = computed(() => allEntries.value.filter((e) => e.entity_type === activeType.value));
const countOf = (t: BibleEntryJson["entity_type"]): number =>
  allEntries.value.filter((e) => e.entity_type === t).length;
const confirmedCount = computed(() => allEntries.value.filter((e) => e.status === "confirmed").length);
const candidateCount = computed(() => allEntries.value.filter((e) => e.status === "candidate").length);

const sourceLabel = (s: string): string => (s === "user" ? "人工" : s === "rule" ? "规则" : "AI");

async function load() {
  if (!props.projectId) return;
  loading.value = true;
  error.value = "";
  try {
    const res = await wb.runService.api.getBible(props.projectId);
    bible.value = res.bible;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => props.projectId, load);

/** 统一操作包装：调 /bible/confirm 或 /bible/entry → 用返回全量 Bible 就地刷新。 */
async function runAction(fn: () => Promise<{ bible: StoryBibleJson }>) {
  error.value = "";
  try {
    const res = await fn();
    bible.value = res.bible;
    return true;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
    return false;
  }
}

/** ✓ 确认候选：status candidate → confirmed（静态属性锁定，AI 永不覆盖）。 */
function confirmEntry(entry: BibleEntryJson) {
  return runAction(() => wb.runService.api.bibleConfirm(props.projectId, entry.entity_id, "confirm"));
}

/** ✓ 采纳属性建议：attributes 并入 + suggestion 移除。 */
function acceptSuggestion(entry: BibleEntryJson, sugg: { key: string; value: string }) {
  return runAction(() =>
    wb.runService.api.bibleConfirm(props.projectId, entry.entity_id, "accept_attribute", {
      key: sugg.key,
      value: sugg.value,
    }),
  );
}

/** ＋ 别名：prompt 输入 → merge_alias（后端归一去重）。 */
function mergeAlias(entry: BibleEntryJson) {
  const alias = window.prompt(`为「${entry.name}」添加别名：`, "");
  if (!alias || !alias.trim()) return;
  void runAction(() =>
    wb.runService.api.bibleConfirm(props.projectId, entry.entity_id, "merge_alias", {
      alias: alias.trim(),
    }),
  );
}

// ---------- 编辑静态属性（本地编辑 → edit_attributes 保存，空值剔除）----------

const editingId = ref<string | null>(null);
const editRows = ref<{ key: string; value: string }[]>([]);

function startEdit(entry: BibleEntryJson) {
  editingId.value = entry.entity_id;
  editRows.value = Object.entries(entry.attributes || {}).map(([key, value]) => ({ key, value }));
  if (!editRows.value.length) editRows.value.push({ key: "", value: "" });
}

function addAttrRow() {
  editRows.value.push({ key: "", value: "" });
}

function removeAttrRow(i: number) {
  editRows.value.splice(i, 1);
}

function cancelEdit() {
  editingId.value = null;
}

async function saveAttrs(entry: BibleEntryJson) {
  const clean: Record<string, string> = {};
  for (const row of editRows.value) {
    const key = row.key.trim();
    const value = row.value.trim();
    if (key && value) clean[key] = value;
  }
  const ok = await runAction(() =>
    wb.runService.api.bibleConfirm(props.projectId, entry.entity_id, "edit_attributes", {
      attributes: clean,
    }),
  );
  if (ok) editingId.value = null;
}

// ---------- ＋ 新建条目（POST /bible/entry，source=user）----------

const newOpen = ref(false);
const newForm = ref({ name: "", aliases: "" });

async function createEntry() {
  const name = newForm.value.name.trim();
  if (!name) return;
  try {
    await wb.runService.api.bibleEntry(props.projectId, activeType.value, name, {
      aliases: newForm.value.aliases
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
    });
    newOpen.value = false;
    newForm.value = { name: "", aliases: "" };
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
}
</script>

<template>
  <div class="bible-panel">
    <div class="bible-head">
      <span class="bible-title">📖 Bible 世界状态库</span>
      <span class="bible-ops">
        <button class="icon-btn" title="刷新" :disabled="loading" @click="load">🔄</button>
        <button class="icon-btn" title="新建条目（人工录入）" @click="newOpen = !newOpen">＋</button>
      </span>
    </div>

    <div v-if="bible" class="bible-meta" title="每次导入章节 version +1（有新版说明有新沉淀）">
      <span class="bible-meta-item">{{ allEntries.length }} 条</span>
      <span class="bible-meta-item">✓ {{ confirmedCount }} 已确认</span>
      <span class="bible-meta-item">◌ {{ candidateCount }} 候选</span>
      <span class="bible-meta-item">v{{ bible.version }}</span>
    </div>

    <div v-if="error" class="bible-err">⚠ {{ error }}</div>
    <div v-if="loading && !bible" class="bible-empty">加载 Bible…</div>

    <!-- 新建条目表单 -->
    <div v-if="newOpen" class="bible-new">
      <input v-model="newForm.name" class="bible-new-input" placeholder="实体名称（如：林雪）" @keydown.enter="createEntry" />
      <input v-model="newForm.aliases" class="bible-new-input" placeholder="别名（逗号分隔，可空）" @keydown.enter="createEntry" />
      <div class="bible-new-ops">
        <button class="btn ghost" @click="newOpen = false">取消</button>
        <button class="btn script-go" :disabled="!newForm.name.trim()" @click="createEntry">✓ 新建</button>
      </div>
    </div>

    <!-- 四组 tab -->
    <div v-if="bible && allEntries.length" class="bible-tabs">
      <button
        v-for="t in TYPES"
        :key="t.key"
        type="button"
        class="bible-tab"
        :class="{ active: activeType === t.key }"
        @click="activeType = t.key"
      >{{ t.icon }} {{ t.label }}<em>{{ countOf(t.key) }}</em></button>
    </div>

    <div v-if="bible && !allEntries.length" class="bible-empty">
      暂无 Bible 数据 · 用「📖 导入小说」对已有项目导入章节后自动建立
    </div>

    <div v-if="bible && allEntries.length" class="bible-list">
      <div v-for="e in activeEntries" :key="e.entity_id" class="bible-card">
        <div class="bible-card-head">
          <span class="bible-id">{{ e.entity_id }}</span>
          <span class="bible-name">{{ e.name }}</span>
          <span class="bible-badge" :class="e.status === 'confirmed' ? 'confirmed' : 'candidate'">
            {{ e.status === "confirmed" ? "✓ 已确认" : "◌ 候选" }}
          </span>
          <span class="bible-source">{{ sourceLabel(e.source) }}</span>
        </div>

        <!-- 别名 -->
        <div v-if="e.aliases?.length" class="bible-row">
          <span class="bible-row-label">别名</span>
          <span class="bible-aliases">
            <span v-for="a in e.aliases" :key="a" class="bible-alias">{{ a }}</span>
          </span>
          <button class="icon-btn bible-alias-add" title="添加别名" @click="mergeAlias(e)">＋</button>
        </div>
        <button v-else class="bible-alias-add-alone" title="添加别名" @click="mergeAlias(e)">＋ 别名</button>

        <!-- 当前状态 + 最后出现 -->
        <div v-if="e.current_status || e.last_seen" class="bible-row bible-state-row">
          <span v-if="e.current_status" class="bible-state" :title="`当前状态（动态，随导入自动累积）`">📌 {{ e.current_status }}</span>
          <span v-if="e.last_seen" class="bible-seen" :title="`最后出现集（动态）`">📍 {{ e.last_seen }}</span>
        </div>

        <!-- 静态属性（折叠；已确认可编辑） -->
        <details v-if="Object.keys(e.attributes || {}).length || e.status === 'confirmed'" class="bible-details">
          <summary>静态属性（{{ Object.keys(e.attributes || {}).length }}）</summary>
          <!-- 编辑态 -->
          <template v-if="editingId === e.entity_id">
            <div v-for="(row, i) in editRows" :key="i" class="bible-edit-row">
              <input v-model="row.key" class="bible-edit-key" placeholder="键" />
              <input v-model="row.value" class="bible-edit-val" placeholder="值" />
              <button class="icon-btn" title="删除此行" @click="removeAttrRow(i)">✕</button>
            </div>
            <div class="bible-edit-ops">
              <button class="btn ghost" @click="addAttrRow">＋ 行</button>
              <button class="btn ghost" @click="cancelEdit">取消</button>
              <button class="btn script-go" @click="saveAttrs(e)">✓ 保存</button>
            </div>
          </template>
          <!-- 只读态 -->
          <template v-else>
            <div v-for="(v, k) in e.attributes" :key="k" class="bible-attr-row">
              <span class="bible-attr-key">{{ k }}</span>
              <span class="bible-attr-val">{{ v }}</span>
            </div>
            <button v-if="e.status === 'confirmed'" class="btn ghost bible-edit-btn" @click="startEdit(e)">✎ 编辑</button>
          </template>
        </details>

        <!-- AI 属性建议（只对已确认条目；逐条采纳/忽略） -->
        <div v-if="e.attribute_suggestions?.length" class="bible-sug">
          <div v-for="(s, i) in e.attribute_suggestions" :key="i" class="bible-sug-row">
            <span class="bible-sug-text">🤖 {{ s.key }}：{{ s.value }}</span>
            <button class="bible-sug-accept" title="采纳此建议（写入静态属性并移除建议）" @click="acceptSuggestion(e, s)">✓ 采纳</button>
          </div>
        </div>

        <!-- 历史（折叠） -->
        <details v-if="e.history?.length" class="bible-details">
          <summary>已发生剧情（{{ e.history.length }}）</summary>
          <div v-for="(h, i) in e.history" :key="i" class="bible-history-row">↳ {{ h }}</div>
        </details>

        <div class="bible-card-foot">
          <button v-if="e.status === 'candidate'" class="bible-confirm" title="确认后静态属性锁定，AI 不再覆盖" @click="confirmEntry(e)">✓ 确认</button>
          <span v-if="e.first_seen" class="bible-firstseen" :title="`首见集`">首见 {{ e.first_seen }}</span>
        </div>
      </div>

      <div v-if="!activeEntries.length" class="bible-empty">本组暂无条目</div>
    </div>
  </div>
</template>

<style scoped>
/* 子组件自给基础按钮（父组件 scoped 样式不继承；与 WorkbenchView .btn/.icon-btn 视觉一致） */
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
.btn.script-go {
  background: #4a7dff;
  color: #fff;
}
.btn.script-go:disabled {
  opacity: 0.5;
  cursor: default;
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
.bible-panel {
  border-top: 1px dashed rgba(255, 255, 255, 0.14);
  margin-top: 10px;
  padding-top: 10px;
  font-size: 12px;
}
.bible-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.bible-title {
  font-weight: 700;
  color: #fff;
}
.bible-ops {
  display: flex;
  gap: 4px;
}
.bible-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  color: rgba(255, 255, 255, 0.55);
  margin-bottom: 8px;
}
.bible-meta-item {
  font-size: 11px;
}
.bible-err {
  color: #ff9d9d;
  background: rgba(255, 80, 80, 0.12);
  border: 1px solid rgba(255, 80, 80, 0.3);
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 8px;
}
.bible-empty {
  color: rgba(255, 255, 255, 0.45);
  padding: 10px 4px;
  line-height: 1.5;
}
.bible-new {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.bible-new-input {
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  color: #fff;
  padding: 4px 6px;
  font-size: 12px;
  width: 100%;
  box-sizing: border-box;
}
.bible-new-ops {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}
.bible-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.bible-tab {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  color: rgba(255, 255, 255, 0.75);
  padding: 4px 8px;
  font-size: 12px;
  cursor: pointer;
}
.bible-tab em {
  font-style: normal;
  margin-left: 4px;
  color: rgba(255, 255, 255, 0.45);
  font-size: 11px;
}
.bible-tab.active {
  background: rgba(122, 162, 247, 0.22);
  border-color: rgba(122, 162, 247, 0.6);
  color: #fff;
}
.bible-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bible-card {
  background: rgba(255, 255, 255, 0.045);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 8px;
}
.bible-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.bible-id {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.4);
  font-family: monospace;
}
.bible-name {
  font-weight: 700;
  color: #fff;
}
.bible-badge {
  font-size: 11px;
  border-radius: 4px;
  padding: 1px 6px;
}
.bible-badge.confirmed {
  color: #9fe8a6;
  background: rgba(70, 180, 90, 0.16);
  border: 1px solid rgba(70, 180, 90, 0.4);
}
.bible-badge.candidate {
  color: #ffd38a;
  background: rgba(220, 150, 50, 0.14);
  border: 1px solid rgba(220, 150, 50, 0.4);
}
.bible-source {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  padding: 0 4px;
}
.bible-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.bible-row-label {
  color: rgba(255, 255, 255, 0.45);
  font-size: 11px;
}
.bible-aliases {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.bible-alias {
  background: rgba(122, 162, 247, 0.16);
  border: 1px solid rgba(122, 162, 247, 0.4);
  color: #a8c4ff;
  border-radius: 4px;
  padding: 0 6px;
  font-size: 11px;
}
.bible-alias-add {
  margin-left: auto;
}
.bible-alias-add-alone {
  margin-top: 6px;
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.5);
  font-size: 11px;
  cursor: pointer;
  padding: 0;
}
.bible-state-row {
  gap: 8px;
}
.bible-state {
  color: #ffd38a;
}
.bible-seen {
  color: rgba(255, 255, 255, 0.5);
}
.bible-details {
  margin-top: 6px;
}
.bible-details summary {
  cursor: pointer;
  color: rgba(255, 255, 255, 0.55);
  font-size: 11px;
  user-select: none;
}
.bible-attr-row {
  display: flex;
  gap: 6px;
  padding: 2px 0;
  font-size: 11px;
}
.bible-attr-key {
  color: rgba(255, 255, 255, 0.5);
  min-width: 72px;
}
.bible-attr-val {
  color: #dfe7ff;
}
.bible-edit-btn {
  margin-top: 4px;
}
.bible-edit-row {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}
.bible-edit-key,
.bible-edit-val {
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  color: #fff;
  padding: 2px 6px;
  font-size: 11px;
  width: 0;
  flex: 1;
}
.bible-edit-ops {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  justify-content: flex-end;
}
.bible-sug {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bible-sug-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.bible-sug-text {
  color: #c9b8ff;
  font-size: 11px;
  flex: 1;
}
.bible-sug-accept {
  background: rgba(122, 162, 247, 0.2);
  border: 1px solid rgba(122, 162, 247, 0.5);
  color: #a8c4ff;
  border-radius: 4px;
  font-size: 11px;
  padding: 1px 8px;
  cursor: pointer;
}
.bible-history-row {
  color: rgba(255, 255, 255, 0.6);
  font-size: 11px;
  padding: 1px 0;
}
.bible-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.bible-confirm {
  background: rgba(70, 180, 90, 0.2);
  border: 1px solid rgba(70, 180, 90, 0.5);
  color: #9fe8a6;
  border-radius: 4px;
  font-size: 11px;
  padding: 2px 10px;
  cursor: pointer;
}
.bible-firstseen {
  color: rgba(255, 255, 255, 0.35);
  font-size: 10px;
}
</style>
