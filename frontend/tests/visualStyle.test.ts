/**
 * Visual Style 前端纯逻辑单测（v2.0 P3，#631+）。
 *   - 默认兜底：normalizeStyleProfile 缺省 → 抖音半写实漫剧完整副本
 *   - 下拉选项：stylePresetOptions（7 预设 → 选项）
 *   - 预览文案：styleProfileSummary（行序：名称→总纲→四组关键词→写实度·色调）
 *   - 标签：fidelityLabel / colorToneLabel；场景级微调：perSceneLabels
 *
 * ⛔ 纯规则零 LLM 零显存：仅 import core/visualStyle 与类型，无网络/后端依赖。
 * 注意：DEFAULT_STYLE_PROFILE_JSON 与后端 director/visual_style.py douyin 预设
 * 逐字段同步（双份维护唯一例外，本测试守住结构完整性）。
 */
import { describe, it, expect } from "vitest";
import type { VisualStyleProfileJson } from "@/services/comfyApi";
import {
  DEFAULT_STYLE_PROFILE_ID,
  DEFAULT_STYLE_PROFILE_JSON,
  normalizeStyleProfile,
  stylePresetOptions,
  fidelityLabel,
  colorToneLabel,
  styleProfileSummary,
  perSceneLabels,
} from "@/core/visualStyle";

const PROFILE_KEYS = [
  "profile_id",
  "name",
  "art_direction",
  "style_keywords",
  "fidelity",
  "color_tone",
  "negative_rules",
  "per_scene",
];

/** 与后端 presets_payload 同构的 7 预设样表（测试用最小化预设，非完整画风）。 */
const SEVEN_PRESETS: Record<string, VisualStyleProfileJson> = {
  douyin_semi_realistic: {
    profile_id: "douyin_semi_realistic",
    name: "抖音半写实漫剧",
    art_direction: "国漫写实",
    style_keywords: { 人物: "半写实人物" },
    fidelity: "semi_realistic",
    color_tone: "warm",
    negative_rules: ["画风统一"],
    per_scene: { 雨夜: "冷调" },
  },
  japan_cel: {
    profile_id: "japan_cel",
    name: "日系赛璐璐",
    art_direction: "cel 平涂",
    style_keywords: {},
    fidelity: "stylized",
    color_tone: "daylight",
    negative_rules: [],
    per_scene: {},
  },
  cn_3d: {
    profile_id: "cn_3d",
    name: "国漫 3D",
    art_direction: "3D 渲染",
    style_keywords: {},
    fidelity: "stylized",
    color_tone: "warm",
    negative_rules: [],
    per_scene: {},
  },
  pixar: {
    profile_id: "pixar",
    name: "皮克斯",
    art_direction: "3D 卡通",
    style_keywords: {},
    fidelity: "stylized",
    color_tone: "warm",
    negative_rules: [],
    per_scene: {},
  },
  ink_guofeng: {
    profile_id: "ink_guofeng",
    name: "水墨国风",
    art_direction: "水墨",
    style_keywords: {},
    fidelity: "stylized",
    color_tone: "dark",
    negative_rules: [],
    per_scene: {},
  },
  korean_thick: {
    profile_id: "korean_thick",
    name: "韩漫厚涂",
    art_direction: "厚涂",
    style_keywords: {},
    fidelity: "semi_realistic",
    color_tone: "cold",
    negative_rules: [],
    per_scene: {},
  },
  cinematic_real: {
    profile_id: "cinematic_real",
    name: "院线写实",
    art_direction: "电影写实",
    style_keywords: {},
    fidelity: "full_realistic",
    color_tone: "dark",
    negative_rules: [],
    per_scene: {},
  },
};

describe("DEFAULT_STYLE_PROFILE_JSON（兜底默认，与后端 douyin 同步）", () => {
  it("profile_id = douyin_semi_realistic", () => {
    expect(DEFAULT_STYLE_PROFILE_JSON.profile_id).toBe(DEFAULT_STYLE_PROFILE_ID);
  });
  it("8 字段齐全", () => {
    for (const k of PROFILE_KEYS) {
      expect(DEFAULT_STYLE_PROFILE_JSON).toHaveProperty(k);
    }
  });
  it("name 含中文与英文串（与后端逐字段同步）", () => {
    expect(DEFAULT_STYLE_PROFILE_JSON.name).toContain("抖音半写实漫剧");
    expect(DEFAULT_STYLE_PROFILE_JSON.name).toContain("Semi-Realistic Cinematic Anime");
  });
  it("art_direction 含用户英文风格串（85mm/浅景深/电影级）", () => {
    const ad = DEFAULT_STYLE_PROFILE_JSON.art_direction;
    expect(ad).toContain("Modern Chinese semi-realistic anime");
    expect(ad).toContain("85mm lens");
    expect(ad).toContain("Shallow depth of field");
    expect(ad).toContain("Movie lighting");
  });
  it("style_keywords 四组齐全（人物/光影/材质/整体）", () => {
    for (const k of ["人物", "光影", "材质", "整体"]) {
      expect(DEFAULT_STYLE_PROFILE_JSON.style_keywords[k]).toBeTruthy();
    }
  });
  it("negative_rules 双语约束非空", () => {
    expect(DEFAULT_STYLE_PROFILE_JSON.negative_rules.length).toBeGreaterThan(0);
    expect(DEFAULT_STYLE_PROFILE_JSON.negative_rules.join("")).toContain("画风统一");
  });
});

describe("normalizeStyleProfile（归一化）", () => {
  it("undefined → 默认完整副本", () => {
    const p = normalizeStyleProfile(undefined);
    expect(p.profile_id).toBe(DEFAULT_STYLE_PROFILE_ID);
    for (const k of PROFILE_KEYS) expect(p).toHaveProperty(k);
  });
  it("缺 profile_id → 默认副本", () => {
    const p = normalizeStyleProfile({} as VisualStyleProfileJson);
    expect(p.profile_id).toBe(DEFAULT_STYLE_PROFILE_ID);
  });
  it("已有 profile 补全 8 字段（缺失字段不崩溃）", () => {
    const p = normalizeStyleProfile({ profile_id: "pixar", name: "皮克斯" } as VisualStyleProfileJson);
    expect(p.profile_id).toBe("pixar");
    expect(p.name).toBe("皮克斯");
    expect(p.art_direction).toBe("");
    expect(p.fidelity).toBe("semi_realistic");
    expect(p.color_tone).toBe("warm");
    expect(p.negative_rules).toEqual([]);
    expect(p.per_scene).toEqual({});
  });
  it("健康 profile 幂等（不静默改写）", () => {
    const src: VisualStyleProfileJson = {
      profile_id: "cinematic_real",
      name: "院线写实",
      art_direction: "电影写实",
      style_keywords: { 整体: "电影感" },
      fidelity: "full_realistic",
      color_tone: "dark",
      negative_rules: ["写实"],
      per_scene: { 回忆: "暖黄" },
    };
    expect(normalizeStyleProfile(src)).toEqual(src);
  });
});

describe("stylePresetOptions（下拉选项）", () => {
  it("7 预设 → 7 选项", () => {
    const opts = stylePresetOptions(SEVEN_PRESETS);
    expect(opts).toHaveLength(7);
    expect(opts[0].value).toBe("douyin_semi_realistic");
    expect(opts[0].label).toBe("抖音半写实漫剧");
  });
  it("label 缺省回退 id", () => {
    const opts = stylePresetOptions({
      foo: { profile_id: "foo", name: "" } as unknown as VisualStyleProfileJson,
    });
    expect(opts[0].label).toBe("foo");
  });
  it("空预设表 → 空选项（面板自行补当前项兜底）", () => {
    expect(stylePresetOptions({})).toEqual([]);
  });
});

describe("fidelityLabel / colorToneLabel（中文标签）", () => {
  it("写实度映射", () => {
    expect(fidelityLabel("full_realistic")).toBe("全写实");
    expect(fidelityLabel("semi_realistic")).toBe("半写实");
    expect(fidelityLabel("stylized")).toBe("风格化");
  });
  it("未知值原样回退", () => {
    expect(fidelityLabel("weird")).toBe("weird");
    expect(fidelityLabel("")).toBe("—");
  });
  it("色调映射", () => {
    expect(colorToneLabel("cold")).toBe("冷调");
    expect(colorToneLabel("warm")).toBe("暖调");
    expect(colorToneLabel("dark")).toBe("暗调");
    expect(colorToneLabel("daylight")).toBe("自然光");
  });
});

describe("styleProfileSummary（当前 profile 预览）", () => {
  it("undefined → 默认兜底（名称行非空）", () => {
    const lines = styleProfileSummary(undefined);
    expect(lines.length).toBeGreaterThan(0);
    expect(lines[0]).toContain("抖音半写实漫剧");
  });
  it("行序：名称 → 总纲 → 四组关键词 → 写实度·色调", () => {
    const lines = styleProfileSummary(SEVEN_PRESETS.douyin_semi_realistic);
    expect(lines[0]).toBe("抖音半写实漫剧");
    expect(lines[1]).toBe("国漫写实");
    expect(lines).toContain("半写实人物");
    expect(lines[lines.length - 1]).toBe("写实度 半写实 · 色调 暖调");
  });
  it("空关键词不产生空行", () => {
    const lines = styleProfileSummary(SEVEN_PRESETS.japan_cel);
    expect(lines).toContain("写实度 风格化 · 色调 自然光");
    expect(lines.length).toBe(3); // 名称 + 总纲 + 写实度·色调
  });
});

describe("perSceneLabels（场景级微调提示）", () => {
  it("返回 per_scene 键名", () => {
    expect(perSceneLabels(SEVEN_PRESETS.douyin_semi_realistic)).toEqual(["雨夜"]);
  });
  it("无 per_scene → 空数组", () => {
    expect(perSceneLabels(SEVEN_PRESETS.pixar)).toEqual([]);
  });
  it("undefined → 默认兜底（douyin 有 per_scene 雨夜/回忆）", () => {
    expect(perSceneLabels(undefined)).toEqual(["雨夜", "回忆"]);
  });
});
