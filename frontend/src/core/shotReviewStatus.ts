/**
 * V1.7 Phase 4：AI 制作计划审核就绪检查（纯函数，可单测）。
 *
 * 每镜 ✓/⚠ 判定（用户 2026-08-11 拍板三项）：
 *  ① 资产绑定：本镜应有角色（castIds）在生效资产池中有资产且资产已上传参考图
 *     （imageFile 非空）；生效资产里 imageFile 为空的（地点/道具等）→ ⚠「缺参考图」。
 *  ② 五区 Prompt：content.visual 非空。
 *  ③ 运镜：content.cameraText 非空。
 *
 * 任何缺失 → ok=false + issues 逐条列出（人类可读，直接可展示）。
 * ⚠ 只用于「AI 制作计划」审核展示与审核门槛，不参与指纹/状态灯/导出/续接逻辑
 * （与 aiDraft 同准则，V17 §9.0）。
 */

import type { Asset, Shot } from "../models/project";

export interface ShotReviewIssue {
  kind: "asset" | "prompt" | "camera";
  text: string;
}

export interface ShotReviewStatus {
  ok: boolean;
  issues: ShotReviewIssue[];
}

/** 审核就绪输入：本镜应有角色 id + 本镜生效资产（由调用方用 inheritanceResolver 解析）。 */
export interface ShotReviewInput {
  /** 本镜应有角色 id（shotCastIds(shot) 解析结果；空镜可为空）。 */
  castIds?: string[];
  /** 本镜生效资产（resolveInheritance 输出的资产实体；含场景默认/@命中/显式绑定）。 */
  assets?: Asset[];
}

export function shotReviewStatus(
  shot: Shot,
  input: ShotReviewInput = {},
): ShotReviewStatus {
  const issues: ShotReviewIssue[] = [];
  const castIds = input.castIds ?? [];
  const assets = input.assets ?? [];
  const byId = new Map(assets.map((a) => [a.id, a]));

  // ① 资产：应有角色必须在生效资产池且已上传参考图。
  for (const cid of castIds) {
    const a = byId.get(cid);
    if (!a) {
      issues.push({ kind: "asset", text: `角色「${cid}」未绑定资产` });
    } else if (!(a.imageFile || "").trim()) {
      issues.push({ kind: "asset", text: `角色「${cid}」参考图缺失` });
    }
  }
  // ① 资产：生效资产里其它类型（地点/道具/风格）缺参考图。
  for (const a of assets) {
    if ((a.imageFile || "").trim()) continue;
    if (castIds.includes(a.id)) continue; // 角色已在上面判定，避免重复报。
    issues.push({ kind: "asset", text: `资产「${a.name}」未上传参考图` });
  }

  // ② 五区 Prompt：画面描述必须非空。
  if (!(shot.content?.visual ?? "").trim()) {
    issues.push({ kind: "prompt", text: "Prompt 画面描述为空" });
  }

  // ③ 运镜：摄影分区必须非空。
  if (!(shot.content?.cameraText ?? "").trim()) {
    issues.push({ kind: "camera", text: "运镜未设置" });
  }

  return { ok: issues.length === 0, issues };
}
