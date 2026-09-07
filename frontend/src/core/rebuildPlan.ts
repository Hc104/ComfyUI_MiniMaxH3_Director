/**
 * P0-A（#478/#479）：从 SPA Project 重建 ProductionPlan（后端 /prompt/h3 重建三段式的输入）。
 *
 * 数据流锁死：剧本 → ProductionPlan → DirectorIntent → H3Prompt。生成提交时后端
 * /prompt/h3 需要 ProductionPlan（source_text 逐字 + scene_id/shot_id）才能重建
 * 三段式 H3 Prompt。剧本导入项目把原始 plan 持久化在 `project.plan`（#478）；
 * 新建/手编项目没有 plan → 本模块从 Project 结构重建一个**结构对齐**的 ProductionPlan：
 *
 * - scene_id / shot_id 按前端遍历顺序生成（scene_id=sc.id，shot_id=shot_XX），
 *   与 overrides_by_shot 的键严格对齐（buildOverridesByShot 直接按索引取键）。
 * - source_text 优先取原始 plan 对应镜头（导入顺序一致，权威原文逐字），否则从
 *   `shot.description`「原文：」段提取（productionPlanToProject 逐字保留），再无 → 空串。
 * - characters/props 从前端 castIds/propIds 投影；duration_sec 从 durationSec。
 *   actions/dialogue/visual_intent 前端不存 → 空（DirectorIntent 规则层会兜底）。
 *
 * ⛔ 本模块纯函数、零网络、零后端依赖；输出严格满足 comfyApi.ProductionPlanJson 形状。
 */

import type { Project, Shot } from "@/models/project";
import type { H3PromptItem, H3PromptResult, ProductionPlanJson } from "@/services/comfyApi";

/** 「原文：」前缀（productionPlanToProject 把 source_text 逐字保留在 description）。 */
const ORIGINAL_RE = /(?:^|\n)原文：\s*([^\n]+)/;

/** 从 shot.description 提取逐字原文（无「原文：」段 → 空串）。 */
export function extractSourceText(shot: Shot): string {
  const m = ORIGINAL_RE.exec(shot.description ?? "");
  return m ? m[1].trim() : "";
}

/** 前端 scene/shot 遍历顺序 → plan 的 `scene_id:shot_id` 键（rebuild 后与前端严格对齐）。 */
export function planShotKey(plan: ProductionPlanJson, sceneIdx: number, shotIdx: number): string {
  const sc = plan.scenes?.[sceneIdx];
  const sh = sc?.shots?.[shotIdx];
  if (!sc || !sh) return "";
  return `${sc.scene_id}:${sh.shot_id}`;
}

/**
 * 从 Project 结构重建 ProductionPlan（scene_id/shot_id 与前端当前结构严格对齐）。
 *
 * project.plan（原始导入 plan）存在时：
 *   - source_text 按「scene_id 匹配 + 同 order 取 shot」从原始 plan 找回（权威原文逐字）；
 *   - 场景增删/改名后 scene_id 失配 → fallback description「原文：」提取。
 * 无 project.plan（新建/手编）：
 *   - source_text = description「原文：」段（导入项目才可能有），否则空串。
 */
export function rebuildPlanFromProject(project: Project): ProductionPlanJson {
  const src = project.plan;
  const scenes = project.episodes?.[0]?.scenes ?? [];
  return {
    project: {
      title: project.name || project.episodes?.[0]?.title || "",
      source_file: src?.project?.source_file ?? "",
    },
    scenes: scenes.map((sc) => {
      const srcScene = src?.scenes?.find((s) => s.scene_id === sc.id);
      const shots = [...sc.shots].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
      return {
        scene_id: sc.id || "scene_00",
        title: sc.name ?? "",
        location_name: sc.location ?? "",
        time: sc.time ?? "",
        weather: sc.weather ?? "",
        shots: shots.map((sh, j) => {
          const srcShot = srcScene?.shots?.[j];
          const source_text =
            srcShot?.source_text?.trim() || extractSourceText(sh) || "";
          // 原始 plan 镜头数据优先携带（dialogue/actions/emotion/visual_intent/entities/
          // visual_elements），保证 DirectorIntent 从原始剧本事实构建（对白逐字进
          // soundscape、实体边界、情绪等），绝不因重建丢失原文事实。
          return {
            shot_id: `shot_${String(j + 1).padStart(2, "0")}`,
            source_text,
            duration_sec: sh.durationSec,
            characters: (sh.castIds ?? []).map((n) => ({ name: n, role: "" })),
            props: (sh.propIds ?? []).map((n) => ({ name: n })),
            actions: srcShot?.actions ?? [],
            emotion: srcShot?.emotion ?? "",
            dialogue: srcShot?.dialogue ?? [],
            visual_intent: srcShot?.visual_intent ?? "",
            entities: srcShot?.entities ?? [],
            visual_elements: srcShot?.visual_elements ?? [],
          };
        }),
      };
    }),
    validation: { status: "ok", errors: [], warnings: [] },
  };
}

/**
 * #479：从当前五区编辑提取 `overrides_by_shot`（键 = plan 的 scene_id:shot_id，顺序对齐）。
 *
 * 只提交「有内容」的分区；空分区不写（后端保持 AI/规则默认，绝不覆盖原文事实）。
 * visual→composition / cameraText→camera_desc / style→style / soundText→audio.ambient
 * 的映射由后端 _apply_user_overrides 完成。
 */
export function buildOverridesByShot(
  project: Project,
  plan: ProductionPlanJson,
): Record<string, { visual?: string; cameraText?: string; style?: string; soundText?: string }> {
  const out: Record<string, { visual?: string; cameraText?: string; style?: string; soundText?: string }> = {};
  (project.episodes?.[0]?.scenes ?? []).forEach((sc, si) => {
    const shots = [...sc.shots].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    shots.forEach((sh, j) => {
      const key = planShotKey(plan, si, j);
      if (!key) return;
      const ov: { visual?: string; cameraText?: string; style?: string; soundText?: string } = {};
      const v = (sh.content.visual ?? "").trim();
      if (v) ov.visual = v;
      const c = (sh.content.cameraText ?? "").trim();
      if (c) ov.cameraText = c;
      const s = (sh.content.style ?? "").trim();
      if (s) ov.style = s;
      const sd = (sh.content.soundText ?? "").trim();
      if (sd) ov.soundText = sd;
      if (Object.keys(ov).length) out[key] = ov;
    });
  });
  return out;
}

// ---------- P0-A（#479）：提交时实时重建三段式 → 提交文本 ----------

/** 三段式 H3 Prompt → 提交单文本（描述/音景/配乐按 \n 拼接，空前缀跳过）。
 *  integrated_multimodal_description 自带 [0-Ns] 时间轴前缀（自标识），不加人工标签。 */
export function serializeH3Submission(item: H3PromptItem): string {
  const parts: string[] = [];
  const desc = (item.integrated_multimodal_description ?? "").trim();
  if (desc) parts.push(desc);
  const snd = (item.overall_soundscape ?? "").trim();
  if (snd) parts.push(snd);
  const mus = (item.non_diegetic_music ?? "").trim();
  if (mus) parts.push(mus);
  return parts.join("\n");
}

/** 遍历项目当前结构（场景 → order 排序镜头），回调 (shot, planKey)。
 *  planKey 空（结构失配/越界）→ 跳过，不产生悬空键。 */
function forEachShotKey(
  project: Project,
  plan: ProductionPlanJson,
  cb: (sh: Shot, key: string) => void,
): void {
  (project.episodes?.[0]?.scenes ?? []).forEach((sc, si) => {
    const shots = [...sc.shots].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    shots.forEach((sh, j) => {
      const key = planShotKey(plan, si, j);
      if (key) cb(sh, key);
    });
  });
}

/** /prompt/h3 结果 → {shotId: 三段拼接提交文本}。
 *  只收录结果里有的镜头；结果缺失的镜头（重建后新增/顺序失配）不覆盖
 *  → 调用侧回退 buildShotPromptText 旧分区合并（向后兼容）。 */
export function mapPlanPromptsToShots(
  project: Project,
  plan: ProductionPlanJson,
  result: H3PromptResult,
): Record<string, string> {
  const out: Record<string, string> = {};
  forEachShotKey(project, plan, (sh, key) => {
    const item = result.prompts?.[key];
    if (!item) return;
    const txt = serializeH3Submission(item);
    if (txt) out[sh.id] = txt;
  });
  return out;
}

/** 当前项目时长 → duration_by_shot（{scene_id:shot_id: 秒}），进 H3 [0-Ns] 时间轴。 */
export function buildDurationsFromProject(
  project: Project,
  plan: ProductionPlanJson,
): Record<string, number> {
  const out: Record<string, number> = {};
  forEachShotKey(project, plan, (sh, key) => {
    const d = Number(sh.durationSec);
    if (Number.isFinite(d) && d > 0) out[key] = d;
  });
  return out;
}

/** 资产名 → entity_key canonical 段（与后端 asset_registry._canon 一致：去空白 + 小写）。 */
function canonName(name: string): string {
  return (name ?? "").replace(/\s+/g, "").toLowerCase();
}

/**
 * 当前项目资产 → bindings_by_shot（{scene_id:shot_id: {entity_key: {asset_id, image_file}}}）。
 *
 * 只注入本镜显式引用（castIds/propIds/locationId/styleIds → 场景池检出），与 P0-1
 * per-shot 资产边界一致（场景素材组是候选池，不是默认注入集）。entity_key 优先从
 * plan 实体（entity_id ↔ asset.sourceEntityId 匹配）取 {类型}:{canonical 名}，与后端
 * plan_entity_key 对齐；无对应实体（手编项目）回退资产名构造。无显式引用 → 该镜无
 * binding（后端 references 空，正常）。
 */
export function buildBindingsFromProject(
  project: Project,
  plan: ProductionPlanJson,
): Record<string, Record<string, { asset_id: string; image_file: string }>> {
  // Pre-pass：plan 全部镜头实体 entity_id → entity_key（{类型}:{canonical 名}）。
  const entityKeyByEntityId = new Map<string, string>();
  for (const ps of plan.scenes ?? []) {
    for (const psh of ps.shots ?? []) {
      for (const e of psh.entities ?? []) {
        const id = e?.entity_id;
        if (id && e?.name) entityKeyByEntityId.set(id, `${e.type}:${canonName(e.name)}`);
      }
    }
  }

  const out: Record<string, Record<string, { asset_id: string; image_file: string }>> = {};
  const scenes = project.episodes?.[0]?.scenes ?? [];
  scenes.forEach((sc, si) => {
    const pool = sc.assets ?? { cast: [], locations: [], props: [], styles: [] };
    const shots = [...sc.shots].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    shots.forEach((sh, j) => {
      const key = planShotKey(plan, si, j);
      if (!key) return;
      const bindings: Record<string, { asset_id: string; image_file: string }> = {};
      const addAsset = (a: { sourceEntityId?: string; name: string; imageFile: string }, etype: string) => {
        if (!a.imageFile) return;
        let ekey = a.sourceEntityId ? entityKeyByEntityId.get(a.sourceEntityId) : undefined;
        if (!ekey) ekey = `${etype}:${canonName(a.name)}`;
        if (!bindings[ekey]) {
          bindings[ekey] = { asset_id: a.sourceEntityId ?? "", image_file: a.imageFile };
        }
      };
      for (const cid of sh.castIds ?? []) {
        const a = pool.cast.find((x) => x.id === cid || x.name === cid);
        if (a) addAsset(a, "character");
      }
      const locId = sh.locationId ?? sc.defaultLocationId ?? "";
      if (locId) {
        const a = pool.locations.find((x) => x.id === locId);
        if (a) addAsset(a, "location");
        else if (sc.referenceImage) {
          const ekey = `location:${canonName(sc.location ?? "")}`;
          if (!bindings[ekey]) bindings[ekey] = { asset_id: "", image_file: sc.referenceImage };
        }
      }
      for (const pid of sh.propIds ?? []) {
        const a = pool.props.find((x) => x.id === pid || x.name === pid);
        if (a) addAsset(a, "prop");
      }
      for (const sid of sh.styleIds ?? []) {
        const a = pool.styles.find((x) => x.id === sid);
        if (a) addAsset(a, "style");
      }
      if (Object.keys(bindings).length) out[key] = bindings;
    });
  });
  return out;
}
