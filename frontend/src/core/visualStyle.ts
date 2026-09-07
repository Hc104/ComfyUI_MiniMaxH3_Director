/**
 * Visual Style 前端纯逻辑（v2.0 P3，#631+）。
 *
 * 职责（纯函数、零副作用、可单测）：
 *   - 默认兜底：项目无 styleProfile 时归一为「抖音半写实漫剧」（P3 默认要求）；
 *   - 预览文案：当前 profile 的摘要（面板「当前 profile 预览」区）；
 *   - 下拉选项：7 预设 → 选项（选中后完整 dict 落盘 project.styleProfile）。
 *
 * ⛔ 纯规则零 LLM 零显存：本模块不 import 任何后端/网络模块。
 * ⚠ 双份维护唯一例外：DEFAULT_STYLE_PROFILE_JSON 与后端
 *    director/visual_style.py 的 douyin_semi_realistic 预设逐字段一致
 *    （presets 拉取失败/未加载时的离线兜底；面板加载后以后端
 *    GET /style/presets 权威数据覆盖下拉选项）。
 */

import type { VisualStyleProfileJson } from "@/services/comfyApi";

/** 推荐预设 id（P3：默认「抖音半写实漫剧」）。 */
export const DEFAULT_STYLE_PROFILE_ID = "douyin_semi_realistic";

/** 兜底默认：抖音半写实漫剧（与后端 visual_style.py douyin_semi_realistic 逐字段同步）。 */
export const DEFAULT_STYLE_PROFILE_JSON: VisualStyleProfileJson = {
  profile_id: "douyin_semi_realistic",
  name: "抖音半写实漫剧（Semi-Realistic Cinematic Anime）",
  art_direction:
    "现代国漫写实风。Modern Chinese semi-realistic anime. Ultra detailed character. Realistic facial " +
    "proportions. Delicate skin shading. Natural makeup. Detailed hair strands. Cinematic " +
    "photography. Movie lighting. Warm daylight. Shallow depth of field. 85mm lens. Soft " +
    "bokeh. Photorealistic materials. Anime rendering. High-end Chinese animation style. " +
    "Consistent character appearance. Natural facial expressions. Ultra HD.",
  style_keywords: {
    人物: "半写实动漫人物，真实人体比例，细腻五官，自然皮肤材质",
    光影: "电影级光影，真实环境反射，柔和轮廓光",
    材质: "真实布料，真实头发，真实皮肤，真实金属",
    整体: "高精度国漫，电影感，写实人物，轻动漫化",
  },
  fidelity: "semi_realistic",
  color_tone: "warm",
  negative_rules: [
    "人物五官、发型、服饰全程保持不变，人体结构正常，无畸形崩坏，画风统一。",
    "Keep consistent facial features, hairstyle and costume. Proper anatomy. No distortion.",
  ],
  per_scene: { 雨夜: "冷调，光线清冷克制", 回忆: "暖黄褪色，柔光" },
};

/**
 * 归一化 styleProfile：缺省/缺字段 → 兜底默认（douyin 完整副本），
 * 已有 profile 补全 8 字段默认值（缺字段不崩溃）。
 * 幂等：对健康 profile 无副作用。面板读取 / 生成透传前调用。
 */
export function normalizeStyleProfile(
  profile: VisualStyleProfileJson | undefined,
): VisualStyleProfileJson {
  if (!profile || typeof profile !== "object" || !profile.profile_id) {
    return { ...DEFAULT_STYLE_PROFILE_JSON };
  }
  return {
    profile_id: profile.profile_id || DEFAULT_STYLE_PROFILE_ID,
    name: profile.name || profile.profile_id,
    art_direction: profile.art_direction || "",
    style_keywords: profile.style_keywords || {},
    fidelity: profile.fidelity || "semi_realistic",
    color_tone: profile.color_tone || "warm",
    negative_rules: profile.negative_rules || [],
    per_scene: profile.per_scene || {},
  };
}

/** 预设下拉选项（面板下拉数据源；presets 来自后端 GET /style/presets）。 */
export interface StylePresetOption {
  value: string;
  label: string;
}

export function stylePresetOptions(
  presets: Record<string, VisualStyleProfileJson>,
): StylePresetOption[] {
  return Object.entries(presets).map(([id, p]) => ({
    value: id,
    label: p.name || id,
  }));
}

/** 写实度中文标签。 */
export function fidelityLabel(fidelity: string): string {
  const map: Record<string, string> = {
    full_realistic: "全写实",
    semi_realistic: "半写实",
    stylized: "风格化",
  };
  return map[fidelity] || fidelity || "—";
}

/** 色调中文标签。 */
export function colorToneLabel(colorTone: string): string {
  const map: Record<string, string> = {
    cold: "冷调",
    warm: "暖调",
    dark: "暗调",
    daylight: "自然光",
  };
  return map[colorTone] || colorTone || "—";
}

/**
 * 当前 profile 预览摘要（面板「当前 profile 预览」区渲染行数组）。
 * 顺序：名称 → 画风总纲（art_direction）→ 四组关键词（人物/光影/材质/整体）
 * → 写实度 · 色调。空 profile → 归一化后仍非空（默认兜底）。
 */
export function styleProfileSummary(
  profile: VisualStyleProfileJson | undefined,
): string[] {
  const p = normalizeStyleProfile(profile);
  const lines: string[] = [];
  if (p.name) lines.push(p.name);
  if (p.art_direction) lines.push(p.art_direction);
  for (const key of ["人物", "光影", "材质", "整体"]) {
    const v = p.style_keywords[key];
    if (v && v.trim()) lines.push(v.trim());
  }
  lines.push(`写实度 ${fidelityLabel(p.fidelity)} · 色调 ${colorToneLabel(p.color_tone)}`);
  return lines;
}

/** 该 profile 是否命中某个场景级微调（per_scene 有键；面板提示用）。 */
export function perSceneLabels(profile: VisualStyleProfileJson | undefined): string[] {
  const p = normalizeStyleProfile(profile);
  return Object.keys(p.per_scene || {});
}
