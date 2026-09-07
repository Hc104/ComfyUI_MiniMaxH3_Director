/**
 * 剧本导入反馈（#640，2026-08-18）：导入应用后「看起来像旧缓存」UX 修复。
 *
 * 背景：导入剧本→应用为项目→进入工作台后，时间线统计「4 镜 · 1 地点 · 0 人物」、
 * 卡片名称 shot_01~04、无缩略图——视觉上与旧项目几乎无法区分，用户误判为「旧剧本缓存」。
 * 实际导入链路无 bug（统计数字精确匹配新剧本）。本模块提供两处反馈数据，让导入结果可感知：
 *
 * ① `sourceSummary(shot, maxLen)`：时间线卡片原文摘要（从 shot.description「原文：」段
 *    提取前 maxLen 字），导入项目一眼可见新剧本内容。
 * ② `planCastNames` / `pendingCastNames`：剧本角色名 + 尚未绑定资产的角色名，
 *    顶栏横幅「已应用《…》：N 镜 · M 位角色待绑定参考图」引导用户下一步。
 *
 * ⛔ 纯函数、零网络、零后端依赖、零副作用；可单测。
 */

import type { Shot } from "@/models/project";
import type { ProductionPlanJson } from "@/services/comfyApi";
import { extractSourceText } from "@/core/rebuildPlan";
import type { ConfirmedAssetMatch } from "@/core/productionPlanToProject";

/** 剧本所有角色名（遍历 scenes→shots→characters，去重保序）。无角色 → []。 */
export function planCastNames(plan: ProductionPlanJson): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const sc of plan.scenes ?? []) {
    for (const sh of sc.shots ?? []) {
      for (const c of sh.characters ?? []) {
        const n = (c?.name ?? "").trim();
        if (!n || seen.has(n)) continue;
        seen.add(n);
        out.push(n);
      }
    }
  }
  return out;
}

/** 尚未绑定资产的角色名：剧本角色 − 已确认匹配（kind=cast 且 image_file 非空）。
 *  与 productionPlanToProject.buildMatchIndex 的过滤规则一致（无图绑定不成立）。 */
export function pendingCastNames(
  plan: ProductionPlanJson,
  confirmed: ConfirmedAssetMatch[],
): string[] {
  const bound = new Set<string>();
  for (const m of confirmed ?? []) {
    if (m.kind === "cast" && m.image_file) bound.add(m.name.trim());
  }
  return planCastNames(plan).filter((n) => !bound.has(n));
}

/** 时间线卡片摘要：原文前 maxLen 字（超长截断加省略号），无原文 → 空串（不渲染该行）。 */
export function sourceSummary(shot: Shot, maxLen = 20): string {
  const raw = extractSourceText(shot);
  if (!raw) return "";
  return raw.length > maxLen ? `${raw.slice(0, maxLen)}…` : raw;
}
