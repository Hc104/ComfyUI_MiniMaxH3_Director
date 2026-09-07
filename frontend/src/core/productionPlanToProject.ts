/**
 * V1.7 Commit 3：ProductionPlan → SPA Project 映射（纯函数，可单测）。
 *
 * 数据流锁死（V17_PLAN §4）：剧本 → ProductionPlan → Asset Registry → Generation
 * Planning → H3 Prompt → SPA Shot。本文件是「ProductionPlan → SPA 项目」的桥接层，
 * 遵守以下原则：
 * - scenes → Episode.scenes、shots → Shot 骨架（durationSec 对齐）。
 * - characters/props → Scene.assets 注册（cast/props），**默认不自动绑 castIds**。
 * - **Phase 2（V1.7 P0-B 资产自动匹配）**：传 opts.matches（已确认的资产绑定，
 *   auto 自动 + pending 用户确认）时，才做高置信度绑定——
 *     cast → Scene.assets.cast 资产填 imageFile + 按 shot 角色绑定 castIds；
 *     prop → Scene.assets.props 资产填 imageFile；
 *     location → Scene.assets.locations 注册 + scene.defaultLocationId + shot.locationId。
 *   ⛔ 只复用资产库已有文件，绝不创建「林雪_2」；未匹配实体 imageFile 留空。
 * - visual_intent → shot.description 审核备注；**不进 content.visual**
 *   （visual_intent ≠ content.visual，Phase 3/4 才生成最终 prompt）。
 * - source_text 逐字保留在 shot.description 第一行，保留原文。
 * - **Phase 3（V1.7 P0-C）**：传 opts.drafts（/prompt/draft 输出的五区 AI 草稿）时，
 *   按 `scene_id:shot_id` 命中 → 五区填入 shot.content（visual/cameraText/style/soundText/negativePrompt）
 *   + shot.aiDraft=true + shot.promptTemplateVersion=opts.promptTemplateVersion。
 *   ⛔ 只读审核预览用；进工作台后五区可直接编辑。不传 drafts → 行为不变（content.visual 保持空）。
 * - **P0 Story Timeline（2026-08-14 用户拍板）**：场景树 + 播放顺序解耦。
 *     shot.planShotId 记录后端 `shot_id`（后端 key = `scene_id:shot_id`，见 production_plan.py）；
 *     episode.timeline 从 plan.timeline 透传（后端 `scene_id:shot_id` → 前端全局 Shot.id，
 *     语义对齐后端 resolve_timeline_order：显式条目优先 + 未覆盖补齐；plan.timeline 缺省 →
 *     不写 timeline 字段，前端退化场景树顺序，严格向后兼容）。
 */

import type { Asset, Episode, Project, Scene, SceneAssets, Shot } from "../models/project";
import type { ProductionPlanJson, PromptDraftItem } from "../services/comfyApi";

/** Phase 2 确认后的资产绑定（auto 自动 + pending 用户确认）。 */
export interface ConfirmedAssetMatch {
  kind: "cast" | "prop" | "location";
  /** 剧本实体名（Scene.assets 资产 id / castIds / locationId 用这个名字）。 */
  name: string;
  /** 资产库条目名（minimax_studio/assets 文件名）。 */
  asset_name: string;
  /** ComfyUI input 相对路径（Asset.imageFile）。 */
  image_file: string;
  /** V1.7 Phase 2-1（#135）：稳定实体键 `{清洗后类型}:{canonical_name}`；
   *  落盘 /assets/binding 的主键。 */
  entity_key?: string;
  /** V1.7 Phase 2-1（#135）：永久资产 id（asset_xxx；有 registry 时命中资产才有）。 */
  asset_id?: string | null;
  /** V1.7 Phase 2-1（#135）：来源实体 id（ent_xxx），随 SPA 资产 sourceEntityId 持久化。 */
  sourceEntityId?: string;
  /** V1.7 Phase 2-1（#135）：该确认是否来自「用户显式操作」（pending 选候选 / 接受建议）。
   *  落盘时 source=user；纯 auto 自动匹配为 false（source=system）。 */
  userConfirmed?: boolean;
}

export interface ProductionPlanToProjectOptions {
  projectId?: string;
  projectName?: string;
  /** Phase 2：已确认的资产绑定（auto 自动 + pending 用户确认）；缺省 = 注册不绑定。 */
  matches?: ConfirmedAssetMatch[];
  /** Phase 3（P0-C）：/prompt/draft 输出的五区 AI 草稿（按 scene_id:shot_id 命中）；
   *  缺省 = 不进 content（行为不变，content.visual 保持空）。 */
  drafts?: PromptDraftItem[];
  /** Phase 3：草稿的 Prompt Template Registry 版本（h3-v1），随 Shot 持久化。 */
  promptTemplateVersion?: string;
}

/** ProductionPlan → SPA Project（新建项目骨架，用于导入审核后「应用为项目」）。 */
export function productionPlanToProject(
  plan: ProductionPlanJson,
  opts: ProductionPlanToProjectOptions = {},
): Project {
  const now = new Date().toISOString();
  const name = opts.projectName?.trim() || plan.project.title?.trim() || "剧本导入项目";
  const matchesByKey = buildMatchIndex(opts.matches);
  const draftsByKey = buildDraftIndex(opts.drafts);

  // 跨场景递增的全局镜头序号：后端 ProductionPlan 的 shot_id 是「场景内」序号
  // （每场景从 shot_01 重新计数，见 #132），直接用作前端 Shot.id 会跨场景碰撞，
  // findShot 全局首个匹配会命中其它场景已采纳的同名镜头。这里生成全局唯一 id。
  const shotCounter = { n: 0 };
  // P0 Story Timeline（2026-08-14 用户拍板）：记录后端 `scene_id:shot_id` → 前端全局
  // Shot.id 映射（后端 plan.timeline 条目 = `scene_id:shot_id`，见 production_plan.py；
  // 前端 Shot.id 跨场景唯一，见 #132）。sceneFromPlan 创建 Shot 时填入。
  const shotKeyMap = new Map<string, string>();
  const scenes = plan.scenes.map((ps, si) =>
    sceneFromPlan(ps, si, matchesByKey, draftsByKey, shotCounter, opts.promptTemplateVersion, shotKeyMap),
  );
  const timeline = buildEpisodeTimeline(plan, shotKeyMap);
  const episodes: Episode[] = scenes.length
    ? [
        {
          id: "ep1",
          episodeNumber: 1,
          title: name,
          scenes,
          ...(timeline && timeline.length ? { timeline } : {}),
        },
      ]
    : [];

  return {
    id: opts.projectId || `script-${now.replace(/\D/g, "").slice(0, 14)}`,
    name,
    createdAt: now,
    updatedAt: now,
    episodes,
  };
}

/** `${kind}:${name}` → 匹配条目索引（过滤掉无 imageFile 的无效条目）。 */
function buildMatchIndex(matches?: ConfirmedAssetMatch[]): Map<string, ConfirmedAssetMatch> {
  const map = new Map<string, ConfirmedAssetMatch>();
  for (const m of matches ?? []) {
    if (!m?.name || !m.image_file) continue;
    map.set(`${m.kind}:${m.name.trim()}`, m);
  }
  return map;
}

/** `${scene_id}:${shot_id}` → AI 草稿索引（Phase 3，/prompt/draft 输出）。 */
function buildDraftIndex(drafts?: PromptDraftItem[]): Map<string, PromptDraftItem> {
  const map = new Map<string, PromptDraftItem>();
  for (const d of drafts ?? []) {
    if (!d?.shot_id || !d.draft) continue;
    map.set(`${d.scene_id || ""}:${d.shot_id}`, d);
  }
  return map;
}

/**
 * P0 Story Timeline（2026-08-14 用户拍板）：plan.timeline（后端 `scene_id:shot_id`
 * 播放顺序）→ 前端 Episode.timeline（前端全局 Shot.id）。语义对齐后端
 * production_plan.resolve_timeline_order：
 *  - 显式条目优先（映射失败/重复跳过——后端 validate 只 warning 不 error，这里同样容错）；
 *  - 未覆盖镜头按场景树顺序补齐（保证不丢镜）；
 *  - plan.timeline 空/缺省 → undefined（前端 directorCore 退化场景树顺序，严格向后兼容）。
 */
function buildEpisodeTimeline(
  plan: ProductionPlanJson,
  keyMap: Map<string, string>,
): string[] | undefined {
  const raw = plan.timeline ?? [];
  if (!raw.length) return undefined;
  const out: string[] = [];
  const seen = new Set<string>();
  for (const entry of raw) {
    const id = keyMap.get(entry);
    if (id && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  for (const s of plan.scenes) {
    for (const sh of s.shots) {
      const id = keyMap.get(`${s.scene_id}:${sh.shot_id}`);
      if (id && !seen.has(id)) {
        seen.add(id);
        out.push(id);
      }
    }
  }
  return out;
}

function sceneFromPlan(
  p: ProductionPlanJson["scenes"][number],
  si: number,
  matchesByKey: Map<string, ConfirmedAssetMatch>,
  draftsByKey: Map<string, PromptDraftItem>,
  shotCounter: { n: number },
  promptTemplateVersion?: string,
  shotKeyMap?: Map<string, string>,
): Scene {
  const sceneId = p.scene_id || `scene_${String(si + 1).padStart(2, "0")}`;
  const locName = (p.location_name || "").trim();
  const locMatch = locName ? matchesByKey.get(`location:${locName}`) : undefined;
  const shots: Shot[] = p.shots.map((sh, j) => {
    // 全局唯一 id：跨场景递增（shot_01 → shot_02 → …）。后端 shot_id 每场景内重置，
    // 跨场景会重复，不能直接作前端 Shot.id（见 #132：findShot 全局首个匹配 + adoptAiDraft 幂等）。
    shotCounter.n += 1;
    const shotId = `shot_${String(shotCounter.n).padStart(2, "0")}`;
    // P0 Story Timeline（2026-08-14 用户拍板）：记录后端 `scene_id:shot_id` → 前端全局
    // Shot.id（plan.timeline 条目 = `scene_id:shot_id`，透传时反查前端 id）。
    // scene_id 兜底：后端 to_dict 一定有 scene_id；缺省时用 sceneId（场景树顺序推导值）。
    shotKeyMap?.set(`${p.scene_id || sceneId}:${sh.shot_id}`, shotId);
    // source_text 原文并入 description（visual_intent 备注 + 原文，逐字保留供审核）。
    const descParts: string[] = [];
    if (sh.visual_intent?.trim()) descParts.push(sh.visual_intent.trim());
    if (sh.source_text?.trim()) descParts.push(`原文：${sh.source_text.trim()}`);
    // Phase 2：auto/已确认 cast → 按本镜出现角色绑定 castIds（资产 id = 实体名）。
    const castIds = sh.characters
      ?.map((c) => (c.name || "").trim())
      .filter((n) => n && matchesByKey.has(`cast:${n}`));
    // P0-1 per-shot 资产边界：本镜实际出现的道具 props + 实体表里 type=prop 的条目
    // → propIds（资产 id = 实体名，与场景池 props 的 id 一致）。不按 matches 过滤：
    // 无图的 prop 仍绑定（进了 propIds 才有 chance 进生成 refs）；场景池只作候选，
    // 默认注入集严格 = 本镜 propIds。
    const _propSeen = new Set<string>();
    const propIds: string[] = [];
    const _collectProp = (nm?: string) => {
      const n = (nm || "").trim();
      if (!n || _propSeen.has(n)) return;
      _propSeen.add(n);
      propIds.push(n);
    };
    for (const pp of sh.props ?? []) _collectProp(pp.name);
    for (const shEnt of sh.entities ?? []) {
      if (String(shEnt.type || "").toLowerCase() === "prop") _collectProp(shEnt.name);
    }
    // Phase 3：命中 AI 草稿 → 五区填入 content（只读审核预览进工作台后可直接编辑）。
    const draft = draftsByKey.get(`${p.scene_id || ""}:${sh.shot_id || shotId}`);
    const content: Shot["content"] = { visual: "" };
    let aiDraft: boolean | undefined;
    if (draft?.draft) {
      content.visual = draft.draft.visual ?? "";
      if (draft.draft.camera) content.cameraText = draft.draft.camera;
      if (draft.draft.style) content.style = draft.draft.style;
      if (draft.draft.sound) content.soundText = draft.draft.sound;
      if (draft.draft.negative) content.negativePrompt = draft.draft.negative;
      aiDraft = true;
    }
    // Phase 5（P1-D）：记录本镜 cameraText 来源的运镜模板 id（下拉回显；手编后清空）。
    const cameraTemplate = draft?.camera_template?.trim() || undefined;
    return {
      id: shotId,
      sceneId,
      order: j + 1,
      name: shotId,
      // P0 Story Timeline：后端场景内 shot_id（`scene_id:shot_id` 的后半段）。
      // 手编项目/旧导入无此字段 → undefined；timeline 拍平时按后端 key 寻址。
      planShotId: sh.shot_id,
      description: descParts.length ? descParts.join("\n") : undefined,
      durationSec: clampDuration(sh.duration_sec),
      // Phase 3/4 才生成最终 prompt；视觉意图只留在 description。
      content,
      ...(aiDraft ? { aiDraft } : {}),
      ...(aiDraft && promptTemplateVersion ? { promptTemplateVersion } : {}),
      ...(cameraTemplate ? { cameraTemplate } : {}),
      ...(castIds?.length ? { castIds } : {}),
      ...(propIds.length ? { propIds } : {}),
      ...(locMatch ? { locationId: locName } : {}),
    };
  });

  return {
    id: sceneId,
    name: p.title || sceneId,
    order: si + 1,
    description: locName || undefined,
    location: locName || undefined,
    time: p.time?.trim() || undefined,
    weather: p.weather?.trim() || undefined,
    assets: collectSceneAssets(p, matchesByKey),
    ...(locMatch ? { defaultLocationId: locName } : {}),
    shots,
  };
}

/** 收集场景角色/道具/地点为资产。Phase 2 起：auto/已确认匹配会填 imageFile 并绑定。 */
function collectSceneAssets(
  p: ProductionPlanJson["scenes"][number],
  matchesByKey: Map<string, ConfirmedAssetMatch>,
): SceneAssets {
  const castSeen = new Set<string>();
  const propsSeen = new Set<string>();
  const cast: Asset[] = [];
  const props: Asset[] = [];
  const locations: Asset[] = [];

  for (const shot of p.shots) {
    for (const c of shot.characters || []) {
      const nm = (c.name || "").trim();
      if (!nm || castSeen.has(nm)) continue;
      castSeen.add(nm);
      const m = matchesByKey.get(`cast:${nm}`);
      cast.push({
        id: nm,
        name: nm,
        kind: "cast",
        imageFile: m?.image_file ?? "",
        // Phase 2-1（#135）：来源实体 id（ent_xxx），重新导入时经 entity_key 复用确认。
        sourceEntityId: m?.sourceEntityId,
        description: (c.role || "").trim() || undefined,
      });
    }
    for (const pr of shot.props || []) {
      const nm = (pr.name || "").trim();
      if (!nm || propsSeen.has(nm)) continue;
      propsSeen.add(nm);
      const m = matchesByKey.get(`prop:${nm}`);
      props.push({
        id: nm,
        name: nm,
        kind: "prop",
        imageFile: m?.image_file ?? "",
        sourceEntityId: m?.sourceEntityId,
      });
    }
    // Phase 1.1 实体层：Qwen 补的道具进 shot.entities（type=prop），规则层 shot.props
    // 保持空（#361-#368 后真实后端输出）。合并收集，避免道具资产在 SPA 映射中丢失。
    for (const e of shot.entities || []) {
      if (e.type !== "prop") continue;
      const nm = (e.name || "").trim();
      if (!nm || propsSeen.has(nm)) continue;
      propsSeen.add(nm);
      const m = matchesByKey.get(`prop:${nm}`);
      props.push({
        id: nm,
        name: nm,
        kind: "prop",
        imageFile: m?.image_file ?? "",
        sourceEntityId: m?.sourceEntityId,
      });
    }
  }

  const locName = (p.location_name || "").trim();
  const locMatch = locName ? matchesByKey.get(`location:${locName}`) : undefined;
  if (locName && locMatch) {
    locations.push({
      id: locName,
      name: locName,
      kind: "location",
      imageFile: locMatch.image_file,
      sourceEntityId: locMatch.sourceEntityId,
    });
  }

  return { cast, locations, props, styles: [] };
}

function clampDuration(sec: number): number {
  const n = Number.isFinite(sec) ? Math.round(sec) : 5;
  return Math.min(8, Math.max(2, n));
}

/** 审核预览摘要：场景数 / 镜头数 / 角色数 / 总时长秒。 */
export function planSummary(plan: ProductionPlanJson): {
  scenes: number;
  shots: number;
  characters: number;
  durationSec: number;
} {
  const charSet = new Set<string>();
  let shots = 0;
  let durationSec = 0;
  for (const s of plan.scenes) {
    shots += s.shots.length;
    for (const sh of s.shots) {
      durationSec += sh.duration_sec || 0;
      for (const c of sh.characters || []) {
        if ((c.name || "").trim()) charSet.add(c.name.trim());
      }
    }
  }
  return { scenes: plan.scenes.length, shots, characters: charSet.size, durationSec };
}

/**
 * P2-P5（#542）：把已解析的章节 ProductionPlan 追加/替换为指定集（多集管理 P2 §9）。
 *
 * 「一章一集」（P1-B 拍板）：每章导入 = 一个 Episode。目标集不存在 → 新建；存在 →
 * 整体替换该集 scenes/timeline。复用 productionPlanToProject 的映射（scene→地点库、
 * shot→镜头、timeline 透传），不新建映射逻辑。
 *
 * ⛔ 只动 episodes 集合 + project.plan（P0-A：最新导入 plan 供生成提交重建三段式）；
 * 不动 Asset Registry / Shot 生成版本 / 其它集。
 */
export function appendPlanAsEpisode(
  project: Project,
  plan: ProductionPlanJson,
  episodeNumber: number,
  episodeTitle?: string,
): Project {
  const mapped = productionPlanToProject(plan, { projectName: episodeTitle });
  const src = mapped.episodes[0];
  if (!src) return project; // 空 plan（无 scenes）不追加
  const now = new Date().toISOString();
  const target = project.episodes.find((e) => e.episodeNumber === episodeNumber);
  const newEp: Episode = {
    id: target?.id ?? `ep${episodeNumber}`,
    episodeNumber,
    title: episodeTitle?.trim() || target?.title || `第 ${episodeNumber} 章`,
    scenes: src.scenes,
    ...(src.timeline?.length ? { timeline: src.timeline } : {}),
  };
  const episodes = target
    ? project.episodes.map((e) => (e.episodeNumber === episodeNumber ? newEp : e))
    : [...project.episodes, newEp];
  return {
    ...project,
    updatedAt: now,
    episodes,
    // P0-A（#478）：plan 是「当前活动集」的原始 ProductionPlan（生成提交重建三段式用）。
    plan,
  };
}
