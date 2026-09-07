/**
 * 剧本导入反馈（#640，2026-08-18）：importFeedback 纯函数单测。
 *
 * 覆盖：
 *  1. planCastNames —— 剧本所有角色名（跨镜去重保序）；无角色 → []。
 *  2. pendingCastNames —— 已确认绑定（kind=cast 且 image_file 非空）的角色从待绑定中剔除；
 *     无图绑定不成立（与 productionPlanToProject.buildMatchIndex 过滤规则一致）。
 *  3. sourceSummary —— shot.description「原文：」段前 maxLen 字截断 + 省略号；
 *     无原文 → 空串（时间线不渲染该行）。
 */
import { describe, it, expect } from "vitest";
import type { ProductionPlanJson } from "@/services/comfyApi";
import type { Shot } from "@/models/project";
import type { ConfirmedAssetMatch } from "@/core/productionPlanToProject";
import { planCastNames, pendingCastNames, sourceSummary } from "@/core/importFeedback";

function makePlan(over: Partial<ProductionPlanJson> = {}): ProductionPlanJson {
  return {
    project: { title: "《吐槽成真》4 镜 A/B 实机剧本", source_file: "吐槽成真_4镜剧本.md" },
    scenes: [
      {
        scene_id: "scene_01",
        title: "第一场：豪华私人办公室",
        location_name: "豪华私人办公室",
        time: "午后",
        weather: "晴",
        shots: [
          {
            shot_id: "shot_01",
            source_text: "镜头 1：顾琰宸从西装内袋掏出支票，将支票从手中甩落到林薇薇面前的桌面。",
            duration_sec: 5,
            characters: [{ name: "顾琰宸", role: "" }],
            props: [],
            actions: [],
            emotion: "",
            dialogue: [],
            visual_intent: "",
          },
          {
            shot_id: "shot_03",
            source_text: "镜头 3：林薇薇用支票轻轻敲击桌面，目光直视顾琰宸。",
            duration_sec: 5,
            characters: [{ name: "林薇薇", role: "" }],
            props: [{ name: "支票" }],
            actions: [],
            emotion: "轻蔑",
            dialogue: [{ speaker: "林薇薇", text: "就这？" }],
            visual_intent: "",
          },
          {
            shot_id: "shot_04",
            source_text: "镜头 4：顾琰宸原本冷漠的表情突然凝固。",
            duration_sec: 5,
            characters: [{ name: "顾琰宸", role: "" }, { name: "林薇薇", role: "" }],
            props: [],
            actions: [],
            emotion: "",
            dialogue: [],
            visual_intent: "",
          },
        ],
      },
    ],
    validation: { status: "ok", errors: [], warnings: [] },
    timeline: ["scene_01:shot_01", "scene_01:shot_03", "scene_01:shot_04"],
    ...over,
  };
}

describe("planCastNames", () => {
  it("跨镜去重保序收集角色名", () => {
    expect(planCastNames(makePlan())).toEqual(["顾琰宸", "林薇薇"]);
  });

  it("无 scenes/shots/characters → 空数组", () => {
    expect(planCastNames({ ...makePlan(), scenes: [] })).toEqual([]);
  });

  it("空白角色名过滤", () => {
    const plan = makePlan();
    plan.scenes![0].shots[0].characters = [{ name: "  ", role: "" }];
    expect(planCastNames(plan)).toEqual(["林薇薇", "顾琰宸"]);
  });
});

describe("pendingCastNames", () => {
  const hasImage: ConfirmedAssetMatch = {
    kind: "cast",
    name: "顾琰宸",
    asset_name: "顾琰宸.png",
    image_file: "assets/顾琰宸.png",
    userConfirmed: true,
  };

  it("未确认绑定 → 全部角色待绑定", () => {
    expect(pendingCastNames(makePlan(), [])).toEqual(["顾琰宸", "林薇薇"]);
  });

  it("已确认绑定角色从待绑定剔除", () => {
    expect(pendingCastNames(makePlan(), [hasImage])).toEqual(["林薇薇"]);
  });

  it("无 image_file 的确认不成立（仍待绑定）", () => {
    const noImage: ConfirmedAssetMatch = { ...hasImage, image_file: "" };
    expect(pendingCastNames(makePlan(), [noImage])).toEqual(["顾琰宸", "林薇薇"]);
  });

  it("非 cast 类确认不影响角色待绑定", () => {
    const propMatch: ConfirmedAssetMatch = {
      kind: "prop",
      name: "支票",
      asset_name: "支票.png",
      image_file: "assets/支票.png",
    };
    expect(pendingCastNames(makePlan(), [propMatch])).toEqual(["顾琰宸", "林薇薇"]);
  });
});

describe("sourceSummary", () => {
  function shotOf(description: string | undefined): Shot {
    return { id: "shot_01", sceneId: "scene_01", order: 1, name: "shot_01", description } as Shot;
  }

  it("提取「原文：」段并截断前 10 字加省略号", () => {
    const s = shotOf("视觉备注\n原文：顾琰宸从西装内袋掏出支票，将支票从手中甩落到林薇薇面前的桌面，支票落桌后轻微滑动。");
    expect(sourceSummary(s, 10)).toBe("顾琰宸从西装内袋掏出…");
  });

  it("原文不超过 maxLen 不截断不加省略号", () => {
    const s = shotOf("原文：顾琰宸掏出支票。");
    expect(sourceSummary(s)).toBe("顾琰宸掏出支票。");
  });

  it("无 description / 无「原文：」段 → 空串", () => {
    expect(sourceSummary(shotOf(undefined))).toBe("");
    expect(sourceSummary(shotOf("只有视觉备注"))).toBe("");
  });

  it("自定义 maxLen=6 截前 6 字", () => {
    const s = shotOf("原文：顾琰宸从西装内袋掏出支票，将支票甩落桌面。");
    expect(sourceSummary(s, 6)).toBe("顾琰宸从西装…");
  });
});
