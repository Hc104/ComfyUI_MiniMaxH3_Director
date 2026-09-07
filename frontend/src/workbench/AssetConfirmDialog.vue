<!--
V1.7 Phase 4-C：审核态「补资产」弹窗（V17 §9.0 人工审核闭环）。
触发：Shot 卡片 🤖⚠ 徽标（存在 asset 缺口）→ 点开本弹窗。

数据：打开时用当前镜所在场景构造最小 ProductionPlan，调 /assets/scan
（纯规则，零显存）拿共享资产库列表 + 本镜实体的匹配候选（auto/pending/none）。
确认后写回：
  - cast（角色未绑定/缺图）：确保资产池有 cast 资产（复用已有 / 按选中候选补图 /
    从共享库注册新条目）→ setShotCasts 写入真实资产 id；
  - asset（地点/道具/风格生效资产缺图）：updateAsset 填 imageFile。
⛔ 只复用资产库已有文件、绝不创建「林雪_2」；纯规则零 Ollama/GPU。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  useWorkbench,
  setShotCasts,
  updateAsset,
  addAsset,
} from "@/stores/workbench";
import { shotCastIds } from "@/core/inheritanceResolver";
import { comfyInputUrl, type ProductionPlanJson } from "@/services/comfyApi";
import type { Asset, AssetKind, Episode, Scene, Shot } from "@/models/project";

/** 单镜资产缺口（父级用 reviewStatusOf + 资产池解析后传入）。 */
export interface MissingAssetRef {
  /** cast=角色需绑定资产；asset=生效资产缺图。 */
  mode: "cast" | "asset";
  /** 角色名（cast）或资产名（asset 缺图）。 */
  entityName: string;
  /** 生效/池中资产 id（asset 缺图时必有；cast 未绑定时可能为空）。 */
  assetId: string;
  /** 资产池中同名/同 id 资产（可能无 → 需从共享库注册）。 */
  poolAsset: Asset | null;
  assetKind: AssetKind;
  text: string;
}

interface Candidate {
  name: string;
  image_file: string;
  score: number;
}

const props = defineProps<{
  shot: Shot;
  scene: Scene;
  episode: Episode;
  missing: MissingAssetRef[];
}>();

const emit = defineEmits<{ close: []; confirmed: [] }>();

const wb = useWorkbench();

const scanBusy = ref(false);
const scanError = ref("");
/** 共享资产库平铺图片（scanAssets.assets）。 */
const library = ref<{ name: string; image_file: string }[]>([]);
/** `${kind}:${name}` → 实体匹配结果（scanAssets.matches）。 */
const matches = ref<Record<string, { kind: string; name: string; status: string; candidates: Candidate[] }>>({});
/** 每项选择：key=`${mode}:${entityName}` → 选中候选。 */
const choices = ref<Record<string, Candidate>>({});
/** 全库浏览搜索词（候选不足时手动挑图）。 */
const search = ref("");

function itemKey(item: MissingAssetRef): string {
  return `${item.mode}:${item.entityName}`;
}

function matchKeyOf(item: MissingAssetRef): string {
  // 后端 collect_plan_entities：location 从 scene.location_name、prop 从 shot.props、
  // cast 从 shot.characters；style 不参与匹配。
  return `${item.assetKind}:${item.entityName}`;
}

/** 构造本镜最小 ProductionPlan（只含缺失实体，供 /assets/scan 匹配）。 */
function buildMiniPlan(): ProductionPlanJson {
  const castNames = props.missing
    .filter((i) => i.mode === "cast")
    .map((i) => i.entityName);
  const locName =
    props.missing.find((i) => i.assetKind === "location")?.entityName ?? "";
  const propNames = props.missing
    .filter((i) => i.assetKind === "prop")
    .map((i) => i.entityName);
  return {
    project: { title: props.episode.title, source_file: "" },
    scenes: [
      {
        scene_id: props.scene.id,
        title: props.scene.name,
        location_name: locName,
        time: "",
        weather: "",
        shots: [
          {
            shot_id: props.shot.id,
            source_text: "",
            duration_sec: props.shot.durationSec ?? 5,
            characters: castNames.map((n) => ({ name: n, role: "" })),
            props: propNames.map((n) => ({ name: n })),
            actions: [],
            emotion: "",
            dialogue: [],
            visual_intent: "",
          },
        ],
      },
    ],
    validation: { status: "pending", errors: [], warnings: [] },
  };
}

async function loadLibrary(): Promise<void> {
  scanBusy.value = true;
  scanError.value = "";
  try {
    const res = await wb.runService.api.scanAssets(buildMiniPlan());
    library.value = res.assets;
    for (const m of res.matches) {
      matches.value[`${m.kind}:${m.name}`] = {
        kind: m.kind,
        name: m.name,
        status: m.status,
        candidates: m.candidates ?? [],
      };
    }
    // 默认选中：auto 匹配（有图）→ 否则池中候选第一个有图的。
    for (const item of props.missing) {
      const key = itemKey(item);
      if (choices.value[key]) continue;
      const m = matches.value[matchKeyOf(item)];
      const auto = m?.candidates.find((c) => c.image_file);
      if (auto) {
        choices.value[key] = auto;
        continue;
      }
      const poolPick = candidatesOf(item).find((c) => c.image_file);
      if (poolPick) choices.value[key] = poolPick;
    }
  } catch (err) {
    scanError.value = `共享资产库扫描失败：${err instanceof Error ? err.message : String(err)}`;
  } finally {
    scanBusy.value = false;
  }
}

onMounted(loadLibrary);

/** 本项候选 = 后端实体匹配候选 ∪ 资产池同类有图资产（去重 image_file）。 */
function candidatesOf(item: MissingAssetRef): Candidate[] {
  const out: Candidate[] = [];
  const seen = new Set<string>();
  const push = (c: Candidate) => {
    const k = c.image_file || c.name;
    if (!k || seen.has(k)) return;
    seen.add(k);
    out.push(c);
  };
  const m = matches.value[matchKeyOf(item)];
  for (const c of m?.candidates ?? []) push(c);
  for (const a of poolAssetsOf(item)) {
    if ((a.imageFile || "").trim()) push({ name: a.name, image_file: a.imageFile, score: 1 });
  }
  return out;
}

/** 资产池（全局 + 场景，本项 kind 同类）。 */
function poolAssetsOf(item: MissingAssetRef): Asset[] {
  const k = item.assetKind;
  const ep = (props.episode.assets as Record<AssetKind, Asset[]> | undefined)?.[k] ?? [];
  const sc = (props.scene.assets as Record<AssetKind, Asset[]> | undefined)?.[k] ?? [];
  return [...ep, ...sc];
}

/** 全库浏览候选（按搜索词过滤；未输入 → 空）。 */
const libraryFiltered = computed(() => {
  const q = search.value.trim().toLowerCase();
  if (!q) return library.value;
  return library.value.filter((a) => a.name.toLowerCase().includes(q));
});

function pick(item: MissingAssetRef, c: Candidate): void {
  choices.value = { ...choices.value, [itemKey(item)]: c };
}

/** 还有缺口未选 → 确认置灰。 */
const hasUnresolved = computed(() =>
  props.missing.some((i) => !choices.value[itemKey(i)]),
);

function close(): void {
  emit("close");
}

function apply(): void {
  for (const item of props.missing) {
    const choice = choices.value[itemKey(item)];
    if (!choice) continue;

    if (item.mode === "cast") {
      // 1) 确保目标 cast 资产存在且有图（复用池中同名 / 已选候选来自池 / 从共享库注册）。
      let target: Asset | null = item.poolAsset;
      if (!target) {
        const poolPick = poolAssetsOf(item).find((a) => a.name === choice.name);
        if (poolPick) target = poolPick;
      }
      if (!target) {
        const created = addAsset("cast", choice.name, {
          scope: "scene",
          sceneId: props.scene.id,
          imageFile: choice.image_file,
        });
        if (created) target = created;
      } else if (!(target.imageFile || "").trim()) {
        updateAsset("cast", target.id, { imageFile: choice.image_file }, "scene", props.scene.id);
      }
      if (!target) continue;

      // 2) castIds：剔除本实体旧引用（实体名/旧 id），并入目标资产 id（去重）。
      const cur = shotCastIds(props.shot).filter(
        (id) => id !== item.entityName && id !== item.assetId && id !== target!.id,
      );
      cur.push(target.id);
      setShotCasts(props.shot.id, cur);
    } else {
      // asset 缺图：直接补 imageFile（不新建资产、不覆盖已有图）。
      if (item.assetId && choice.image_file) {
        updateAsset(
          item.assetKind,
          item.assetId,
          { imageFile: choice.image_file },
          "scene",
          props.scene.id,
        );
      }
    }
  }
  emit("confirmed");
}
</script>

<template>
  <div class="ac-mask" role="dialog" aria-label="补资产" @click.self="close">
    <div class="ac-panel">
      <div class="ac-head">
        <span class="ac-title">🤖 补资产 · {{ shot.name ?? shot.id }}</span>
        <span class="ac-hint">从共享资产库匹配参考图，确认后写回本镜绑定</span>
        <button type="button" class="ac-close" title="关闭" @click="close">✕</button>
      </div>

      <div v-if="scanBusy" class="ac-tip">扫描共享资产库（minimax_studio/assets）…</div>
      <div v-if="scanError" class="ac-err">⚠ {{ scanError }}</div>

      <div v-if="!scanBusy" class="ac-body">
        <div v-for="item in missing" :key="itemKey(item)" class="ac-item">
          <div class="ac-item-head">
            <span class="ac-item-kind" :class="`k-${item.assetKind}`">
              {{ item.assetKind === "cast" ? "人物" : item.assetKind === "location" ? "地点" : item.assetKind === "prop" ? "道具" : "风格" }}
            </span>
            <span class="ac-item-name">{{ item.entityName }}</span>
            <span class="ac-item-why">{{ item.text }}</span>
          </div>

          <div v-if="candidatesOf(item).length" class="ac-cands">
            <button
              v-for="c in candidatesOf(item)"
              :key="`${c.name}:${c.image_file}`"
              type="button"
              class="ac-cand"
              :class="{ sel: choices[itemKey(item)]?.image_file === c.image_file }"
              :title="`${c.name}（置信 ${c.score}）`"
              @click="pick(item, c)"
            >
              <img v-if="c.image_file" :src="comfyInputUrl(c.image_file)" class="ac-thumb" alt="" />
              <span class="ac-thumb-ph" v-else>🖼</span>
              <span class="ac-cand-name">{{ c.name }}</span>
              <span v-if="c.score >= 0.9" class="ac-cand-auto">自动 ✓</span>
            </button>
          </div>

          <div class="ac-browse">
            <input
              v-model="search"
              class="ac-search"
              placeholder="共享资产库搜索（输入名字浏览全部图片）…"
            />
            <div v-if="libraryFiltered.length" class="ac-lib">
              <button
                v-for="a in libraryFiltered"
                :key="a.image_file"
                type="button"
                class="ac-cand"
                :class="{ sel: choices[itemKey(item)]?.image_file === a.image_file }"
                :title="a.name"
                @click="pick(item, { name: a.name, image_file: a.image_file, score: 0.5 })"
              >
                <img :src="comfyInputUrl(a.image_file)" class="ac-thumb" alt="" />
                <span class="ac-cand-name">{{ a.name }}</span>
              </button>
            </div>
          </div>
        </div>

        <div v-if="!missing.length" class="ac-empty">没有资产缺口，此镜审核已就绪。</div>
      </div>

      <div class="ac-foot">
        <button type="button" class="btn ghost" @click="close">取消</button>
        <button
          type="button"
          class="btn primary"
          :disabled="scanBusy || hasUnresolved || !missing.length"
          @click="apply"
        >
          ✓ 确认补上
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ac-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1100;
  padding: 20px;
}
.ac-panel {
  width: min(720px, 94vw);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: #161626;
  border: 1px solid #2a2a40;
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  color: #c8c8d8;
  font-size: 13px;
}
.ac-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid #26263a;
}
.ac-title { font-size: 14px; font-weight: 600; color: #e0e8ff; }
.ac-hint { color: #8a8ab0; font-size: 12px; }
.ac-close {
  margin-left: auto;
  background: none;
  border: none;
  color: #8a8a9a;
  cursor: pointer;
  font-size: 13px;
  padding: 4px 8px;
  border-radius: 6px;
}
.ac-close:hover { background: #22223a; color: #fff; }
.ac-tip { color: #8a8a9a; font-size: 12px; padding: 14px 16px; }
.ac-err { color: #ff9a9a; font-size: 12px; padding: 10px 16px; background: rgba(255, 107, 107, 0.08); }
.ac-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.ac-item {
  border: 1px solid #26263a;
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #1a1a2c;
}
.ac-item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ac-item-kind {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 4px;
  white-space: nowrap;
}
.ac-item-kind.k-cast { color: #7ee0a3; background: rgba(126, 224, 163, 0.12); }
.ac-item-kind.k-location { color: #8ab8ff; background: rgba(138, 184, 255, 0.12); }
.ac-item-kind.k-prop { color: #ffcf7a; background: rgba(255, 207, 122, 0.12); }
.ac-item-kind.k-style { color: #c9a2ff; background: rgba(201, 162, 255, 0.12); }
.ac-item-name { font-weight: 600; color: #e8e8ee; }
.ac-item-why { color: #ff9a9a; font-size: 12px; }
.ac-cands { display: flex; flex-wrap: wrap; gap: 8px; }
.ac-browse { display: flex; flex-direction: column; gap: 6px; }
.ac-search {
  background: #12121f;
  border: 1px solid #2a2a40;
  color: #d0d0e0;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 12px;
}
.ac-lib { display: flex; flex-wrap: wrap; gap: 8px; }
.ac-cand {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #12121f;
  border: 1px solid #26263a;
  border-radius: 8px;
  padding: 4px 8px 4px 4px;
  cursor: pointer;
  color: #c8c8d8;
  font-size: 12px;
}
.ac-cand:hover { border-color: #4a4a6a; }
.ac-cand.sel { border-color: #7ee0a3; background: rgba(126, 224, 163, 0.1); }
.ac-thumb { width: 34px; height: 34px; object-fit: cover; border-radius: 6px; background: #22223a; }
.ac-thumb-ph { width: 34px; height: 34px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; background: #22223a; font-size: 14px; }
.ac-cand-name { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ac-cand-auto { color: #7ee0a3; font-size: 11px; }
.ac-empty { color: #8a8a9a; font-size: 12px; padding: 10px 0; }
.ac-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #26263a;
}
.ac-foot .btn { font-size: 13px; padding: 6px 16px; border-radius: 6px; cursor: pointer; border: 1px solid transparent; }
.ac-foot .btn.primary { background: #3b5bd6; color: #fff; }
.ac-foot .btn.primary:disabled { opacity: 0.45; cursor: not-allowed; }
.ac-foot .btn.ghost { background: none; color: #9a9ab0; border-color: #2a2a40; }
.ac-foot .btn.ghost:hover { color: #fff; }
</style>
