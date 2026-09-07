/**
 * DirectorCore — Inheritance Resolver（模型无关）。
 *
 * 职责：继承解析。返回镜头最终使用的资产及每条资产的来源链（蓝图 §4.7/§5.1/§7.5）。
 *
 * 继承优先级（与后端 gen_timeline.py + 前端资产池语义一致）：
 *   cast：显式 castIds（∪ @命中，canonical 去重） > 场景默认 > 全局默认
 *   location：显式（shot.locationId + locationManual） > @匹配 > 场景默认 > 全局默认
 *
 * V1.5-P1 扩展到四类（规划 §3.4）：
 *   - location：单选链（显式 > @匹配 > 场景默认 > 项目默认）；
 *   - cast：V1.6-B 改集合（显式 ∪ @命中，按名称判重；无则场景默认/全局默认单选兜底）；
 *   - prop / style：集合级（场景池优先 ∪ 全局池补缺，按名称判重，UI 永不重复条目）。
 */

import type { Asset, Episode, Scene, Shot } from "@/models/project";

export type AssetSource = "shot" | "scene" | "project" | "none";
export type MatchMethod = "explicit" | "mention" | "scene-default" | "project-default";

export interface InheritedAsset {
  assetId: string;
  asset: Asset;
  source: AssetSource;
  matchedBy: MatchMethod;
}

export interface InheritanceCtx {
  shot: Shot;
  scene: Scene;
  episode: Episode;
  /** 提示词 @ 命中的资产 id 集合（由 Asset Resolver 产出，可为空）。 */
  mentionAssetIds: Set<string>;
}

/** 展平可用资产池（全局 + 场景素材组，四类），保持传入顺序。 */
function poolOf(ctx: InheritanceCtx): Asset[] {
  return [
    ...(ctx.episode.assets?.cast ?? []),
    ...(ctx.episode.assets?.locations ?? []),
    ...(ctx.episode.assets?.props ?? []),
    ...(ctx.episode.assets?.styles ?? []),
    ...(ctx.scene.assets?.cast ?? []),
    ...(ctx.scene.assets?.locations ?? []),
    ...(ctx.scene.assets?.props ?? []),
    ...(ctx.scene.assets?.styles ?? []),
  ];
}

/** 解析一个资产 id → 池中资产。 */
function findAsset(pool: Asset[], id: string | undefined): Asset | null {
  if (!id) return null;
  return pool.find((a) => a.id === id) ?? null;
}

/**
 * 读取镜头显式角色 id 集合（V1.6-B）：
 *   castIds 数组优先；无则回退旧单值 castId（兼容旧数据/未迁移场景）。
 * 返回去除空串的数组，不做名称判重（判重在 resolveCollection）。
 */
export function shotCastIds(shot: Shot): string[] {
  const arr = shot.castIds?.filter((id) => Boolean(id)) ?? [];
  if (arr.length) return arr;
  return shot.castId ? [shot.castId] : [];
}

/** location 走完整优先级链（V1.6-B 起 cast 改集合，不再走单选链）。 */
function resolveOne(
  kind: "location",
  ctx: InheritanceCtx,
): InheritedAsset | null {
  const pool = poolOf(ctx);
  const shot = ctx.shot;
  const explicitId = shot.locationId;
  const explicitManual = shot.locationManual;
  const defaultId = ctx.scene.defaultLocationId;

  // ① 显式：镜头显式指定（locationManual=true 强制覆盖）。
  // Q3 修复（#98）：去掉 `defaultId === explicitId` 子句——它是旧版 addShot 预写
  // 场景默认值（未设 manual）时被误判为「⚡镜头指定」的根源；正常显式选择必定
  // manual=true，该子句唯一作用就是制造误判。缺省时保留 explicitManual 与
  // 「场景无默认」兜底：无默认时只要 explicitId 有值即视为显式（兼容导入数据）。
  if (explicitId && (explicitManual || !defaultId)) {
    const a = findAsset(pool, explicitId);
    if (a) return { assetId: a.id, asset: a, source: "shot", matchedBy: "explicit" };
  }

  // ② @匹配：提示词命中的同 kind 资产（来源标记为 shot，但资产本体在全局/场景池）。
  for (const id of ctx.mentionAssetIds) {
    const a = findAsset(pool, id);
    if (a && a.kind === kind) {
      return { assetId: a.id, asset: a, source: "shot", matchedBy: "mention" };
    }
  }

  // ③ 场景默认。
  if (defaultId) {
    const a = findAsset(pool, defaultId);
    if (a && a.kind === kind) {
      return { assetId: a.id, asset: a, source: "scene", matchedBy: "scene-default" };
    }
  }

  // ④ 全局默认：episode 资产池中该 kind 第一个。
  const globalPool = ctx.episode.assets?.locations ?? [];
  const fallback = globalPool[0];
  if (fallback) {
    return { assetId: fallback.id, asset: fallback, source: "project", matchedBy: "project-default" };
  }

  return null;
}

/** 名称判重键（trim + lowercase；空名回退 id，避免同 id 重复注入）。 */
function nameKey(a: Asset): string {
  const n = a.name.trim().toLowerCase();
  return n || a.id;
}

/**
 * prop / style 集合级解析（P0-1/P0-2 per-shot 资产边界）：
 *   最终生效集合 = 本镜显式 propIds/styleIds ∪ 提示词 @ 命中（按名称判重）。
 *   场景素材组恢复「候选池」语义——条目只进 Workbench「场景素材组 ▸」折叠区，
 *   不再整池注入 Workbench「👤 资产」与生成 refs（铁律①：一个 Shot 不能拿到
 *   其他 Shot 的资产；无 propIds 的镜 = 无道具参考，而不是场景池全量）。
 *
 * cast 集合（V1.6-B，canonical identity）：
 *   最终生效集合 = 显式 castIds（shotCastIds） ∪ @命中 cast，按名称判重；
 *   无显式无 @命中 → 场景默认（单选）→ 全局默认（单选）兜底。
 */
function resolveCollection(
  kind: "prop" | "style" | "cast",
  ctx: InheritanceCtx,
): InheritedAsset[] {
  if (kind === "cast") {
    const pool = poolOf(ctx);
    const out: InheritedAsset[] = [];
    const seen = new Set<string>();

    // ① 显式角色集合（canonical identity 主体）。
    for (const id of shotCastIds(ctx.shot)) {
      const a = findAsset(pool, id);
      if (!a) continue;
      const key = nameKey(a);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ assetId: a.id, asset: a, source: "shot", matchedBy: "explicit" });
    }

    // ② @命中 cast 并入（去重：显式已收录的同名不再注入）。
    for (const id of ctx.mentionAssetIds) {
      const a = findAsset(pool, id);
      if (!a || a.kind !== "cast") continue;
      const key = nameKey(a);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ assetId: a.id, asset: a, source: "shot", matchedBy: "mention" });
    }

    // ③ 无显式无 @命中 → 场景默认 → 全局默认（单选兜底，保持与旧单选链一致）。
    if (!out.length) {
      if (ctx.scene.defaultCastId) {
        const a = findAsset(pool, ctx.scene.defaultCastId);
        if (a && a.kind === "cast") {
          out.push({ assetId: a.id, asset: a, source: "scene", matchedBy: "scene-default" });
        }
      } else {
        const fallback = ctx.episode.assets?.cast?.[0];
        if (fallback) {
          out.push({ assetId: fallback.id, asset: fallback, source: "project", matchedBy: "project-default" });
        }
      }
    }
    return out;
  }

  // prop / style：P0-1 per-shot 显式 ∪ @命中（无场景池全量注入）。
  const explicitIds = kind === "prop" ? ctx.shot.propIds ?? [] : ctx.shot.styleIds ?? [];
  const pool = poolOf(ctx);
  const out: InheritedAsset[] = [];
  const seen = new Set<string>();

  for (const id of explicitIds) {
    const a = findAsset(pool, id);
    if (!a || a.kind !== kind) continue;
    const key = nameKey(a);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ assetId: a.id, asset: a, source: "shot", matchedBy: "explicit" });
  }

  for (const id of ctx.mentionAssetIds) {
    const a = findAsset(pool, id);
    if (!a || a.kind !== kind) continue;
    const key = nameKey(a);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ assetId: a.id, asset: a, source: "shot", matchedBy: "mention" });
  }

  return out;
}

/** 继承解析：返回镜头有效 cast 集合 + location + prop 集合 + style 集合资产及来源。 */
export function resolveInheritance(ctx: InheritanceCtx): InheritedAsset[] {
  const out: InheritedAsset[] = [];
  out.push(...resolveCollection("cast", ctx));
  const location = resolveOne("location", ctx);
  if (location) out.push(location);
  out.push(...resolveCollection("prop", ctx));
  out.push(...resolveCollection("style", ctx));
  return out;
}

/** 继承来源查询：单条资产在给定上下文中属于哪一层。 */
export function inheritedSource(assetId: string, ctx: InheritanceCtx): AssetSource {
  return resolveInheritance(ctx).find((i) => i.assetId === assetId)?.source ?? "none";
}
