/**
 * P0-A（#478/#479）：rebuildPlan（从 SPA Project 重建 ProductionPlan + overrides 提取）纯函数单测。
 *
 * 验证（用户拍板「#479 生成链：原始剧本 → DirectorIntent → 用户在 Workbench 修改 →
 * overrides_by_shot → build_shot_intent → h3_prompt_builder → 三段式 H3 Prompt」）：
 * 1. rebuildPlanFromProject（无 plan）：scene_id/shot_id 与前端遍历顺序严格对齐，
 *    source_text 从 description「原文：」段逐字提取；
 * 2. rebuildPlanFromProject（有 plan）：source_text 优先取原始 plan 对应镜头（权威原文逐字）；
 * 3. characters/props 从 castIds/propIds 投影，duration_sec 从 durationSec；
 * 4. buildOverridesByShot：键 = plan 的 scene_id:shot_id，顺序对齐，空分区不写；
 * 5. planShotKey：前端索引 → plan 键，越界返回空串。
 */
import { describe, it, expect } from "vitest";
import {
  extractSourceText,
  planShotKey,
  rebuildPlanFromProject,
  buildOverridesByShot,
  serializeH3Submission,
  mapPlanPromptsToShots,
  buildDurationsFromProject,
  buildBindingsFromProject,
} from "@/core/rebuildPlan";
import { productionPlanToProject } from "@/core/productionPlanToProject";
import type { H3PromptItem, H3PromptResult, ProductionPlanJson } from "@/services/comfyApi";
import type { Project } from "@/models/project";

/** 三段式测试条目构造（未填分区为空，验证空分区跳过）。 */
function makeItem(desc: string, snd: string, music = ""): H3PromptItem {
  return {
    schema: "minimax-h3-project-v1",
    shot_id: "shot_01",
    scene_id: "scene_01",
    duration_sec: 5,
    integrated_multimodal_description: desc,
    overall_soundscape: snd,
    non_diegetic_music: music,
    references: [],
    timeline: [],
    provenance: {},
  };
}

const SRC_1 = "雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，湿漉漉的石阶映着暖光。";
const SRC_2 = "沈青崖背着旧剑匣从山道走来，衣袂带风，走到客栈门前石阶停步，抬头望一眼檐角灯笼，低头推门。";

const PLAN: ProductionPlanJson = {
  project: { title: "山雨客栈", source_file: "山雨客栈.md" },
  scenes: [
    {
      scene_id: "scene_01",
      title: "第一场：山雨楼外",
      location_name: "山雨楼外",
      time: "黄昏",
      weather: "雨",
      shots: [
        {
          shot_id: "shot_01",
          source_text: SRC_1,
          duration_sec: 5,
          characters: [],
          props: [],
          actions: [],
          emotion: "宁静",
          dialogue: [],
          visual_intent: "远景建立：雨歇黄昏，山雾漫向客栈。",
        },
        {
          shot_id: "shot_02",
          source_text: SRC_2,
          duration_sec: 6,
          characters: [{ name: "沈青崖", role: "" }],
          props: [{ name: "旧剑匣" }],
          actions: ["走来", "停步"],
          emotion: "平静带戒备",
          dialogue: [],
          visual_intent: "沈青崖负旧剑匣从山道走来。",
        },
      ],
    },
  ],
  validation: { status: "valid", errors: [], warnings: [] },
};

/** 有 plan 的导入项目：先 productionPlanToProject 再补 plan 字段（applyScriptProject 实际路径）。 */
function importedProject(): Project {
  const p = productionPlanToProject(PLAN, {
    matches: [
      { kind: "cast", name: "沈青崖", asset_name: "沈青崖", image_file: "minimax_studio/assets/沈青崖.png" },
      { kind: "prop", name: "旧剑匣", asset_name: "旧剑匣", image_file: "minimax_studio/assets/旧剑匣.png" },
    ],
  });
  return { ...p, plan: PLAN };
}

/** 手编项目（无 plan）：直接从 Project 结构构造。 */
function handBuiltProject(): Project {
  return {
    id: "manual-001",
    name: "手编短片",
    createdAt: "2026-08-13T00:00:00Z",
    updatedAt: "2026-08-13T00:00:00Z",
    episodes: [
      {
        id: "ep_01",
        episodeNumber: 1,
        title: "手编短片",
        scenes: [
          {
            id: "scene_01",
            name: "第一场：山雨楼外",
            order: 1,
            location: "山雨楼外",
            time: "黄昏",
            weather: "雨",
            shots: [
              {
                id: "shot_01",
                sceneId: "scene_01",
                order: 1,
                description: "远景建立。\n原文：雨刚停，山雾从林间漫向山间客栈「山雨楼」。",
                durationSec: 5,
                content: { visual: "檐角灯笼暖光映在湿石阶上。" },
              },
              {
                id: "shot_02",
                sceneId: "scene_01",
                order: 2,
                durationSec: 6,
                castIds: ["沈青崖"],
                propIds: ["旧剑匣"],
                content: { visual: "", cameraText: "全景跟移" },
              },
            ],
          },
        ],
      },
    ],
  };
}

describe("extractSourceText：description「原文：」段", () => {
  it("有「原文：」段 → 逐字提取", () => {
    expect(extractSourceText(handBuiltProject().episodes[0].scenes[0].shots[0])).toBe(
      "雨刚停，山雾从林间漫向山间客栈「山雨楼」。",
    );
  });

  it("无「原文：」段 → 空串", () => {
    const shot = handBuiltProject().episodes[0].scenes[0].shots[1];
    expect(extractSourceText(shot)).toBe("");
  });
});

describe("planShotKey：前端索引 → plan 键", () => {
  it("scene_01:shot_01 / scene_01:shot_02", () => {
    const plan = rebuildPlanFromProject(handBuiltProject());
    expect(planShotKey(plan, 0, 0)).toBe("scene_01:shot_01");
    expect(planShotKey(plan, 0, 1)).toBe("scene_01:shot_02");
  });

  it("越界 → 空串（不生成悬空键）", () => {
    const plan = rebuildPlanFromProject(handBuiltProject());
    expect(planShotKey(plan, 5, 0)).toBe("");
    expect(planShotKey(plan, 0, 9)).toBe("");
  });
});

describe("rebuildPlanFromProject：无 plan（手编）重建结构对齐", () => {
  it("scene_id=sc.id、shot_id 场景内 shot_XX、顺序对齐", () => {
    const plan = rebuildPlanFromProject(handBuiltProject());
    expect(plan.scenes).toHaveLength(1);
    expect(plan.scenes[0].scene_id).toBe("scene_01");
    expect(plan.scenes[0].shots.map((s) => s.shot_id)).toEqual(["shot_01", "shot_02"]);
  });

  it("source_text 从 description「原文：」逐字提取；无原文 → 空串", () => {
    const plan = rebuildPlanFromProject(handBuiltProject());
    expect(plan.scenes[0].shots[0].source_text).toBe(
      "雨刚停，山雾从林间漫向山间客栈「山雨楼」。",
    );
    expect(plan.scenes[0].shots[1].source_text).toBe("");
  });

  it("characters/props 从 castIds/propIds 投影，duration_sec 对齐", () => {
    const plan = rebuildPlanFromProject(handBuiltProject());
    const s2 = plan.scenes[0].shots[1];
    expect(s2.characters).toEqual([{ name: "沈青崖", role: "" }]);
    expect(s2.props).toEqual([{ name: "旧剑匣" }]);
    expect(s2.duration_sec).toBe(6);
    expect(plan.scenes[0].shots[0].duration_sec).toBe(5);
  });

  it("无场景 → scenes 空数组（空项目不崩）", () => {
    const empty: Project = { ...handBuiltProject(), episodes: [] };
    const plan = rebuildPlanFromProject(empty);
    expect(plan.scenes).toEqual([]);
  });
});

describe("rebuildPlanFromProject：有 plan（导入）source_text 权威原文", () => {
  it("source_text 逐字取原始 plan（不受 description 改动影响）", () => {
    const proj = importedProject();
    // 模拟用户在 Workbench 改过 description（审核备注变了）→ rebuild 仍取 plan 原文
    proj.episodes[0].scenes[0].shots[0].description = "用户改过的备注";
    const plan = rebuildPlanFromProject(proj);
    expect(plan.scenes[0].shots[0].source_text).toBe(SRC_1);
    expect(plan.scenes[0].shots[1].source_text).toBe(SRC_2);
  });

  it("scene_id 匹配 + 同 order 取 shot（场景增删/顺序不变）", () => {
    const plan = rebuildPlanFromProject(importedProject());
    expect(plan.scenes[0].scene_id).toBe("scene_01");
    expect(plan.scenes[0].shots.map((s) => s.shot_id)).toEqual(["shot_01", "shot_02"]);
  });

  it("scene_id 失配（场景改名）→ fallback description「原文：」", () => {
    const proj = importedProject();
    proj.episodes[0].scenes[0].id = "scene_99"; // 改名 → 原始 plan 匹配不到
    const plan = rebuildPlanFromProject(proj);
    // 该场景 shot description 由 productionPlanToProject 写入「原文：…」→ fallback 命中
    expect(plan.scenes[0].scene_id).toBe("scene_99");
    expect(plan.scenes[0].shots[0].source_text).toContain("雨刚停");
  });
});

describe("buildOverridesByShot：五区编辑 → overrides 键", () => {
  it("只提交有内容的分区；空分区不写（保留 AI/规则默认）", () => {
    const proj = handBuiltProject();
    const plan = rebuildPlanFromProject(proj);
    const ov = buildOverridesByShot(proj, plan);
    expect(ov["scene_01:shot_01"]).toEqual({ visual: "檐角灯笼暖光映在湿石阶上。" });
    expect(ov["scene_01:shot_02"]).toEqual({ cameraText: "全景跟移" }); // visual 空不写
  });

  it("导入项目 + plan：键与 plan 对齐，用户五区编辑可追溯", () => {
    const proj = importedProject();
    // 模拟 Workbench 编辑 shot_01 visual + shot_02 camera
    proj.episodes[0].scenes[0].shots[0].content.visual = "低机位缓慢推进，突出檐角灯笼和湿润石阶。";
    proj.episodes[0].scenes[0].shots[1].content.cameraText = "全景俯拍缓缓推近";
    const plan = rebuildPlanFromProject(proj);
    const ov = buildOverridesByShot(proj, plan);
    expect(ov["scene_01:shot_01"].visual).toContain("低机位缓慢推进");
    expect(ov["scene_01:shot_02"].cameraText).toBe("全景俯拍缓缓推近");
  });

  it("全部分区空 → 空对象（不提交任何 overrides，向后兼容）", () => {
    const proj = handBuiltProject();
    proj.episodes[0].scenes[0].shots[0].content.visual = "";
    proj.episodes[0].scenes[0].shots[1].content.cameraText = "";
    const plan = rebuildPlanFromProject(proj);
    expect(buildOverridesByShot(proj, plan)).toEqual({});
  });
});

describe("rebuildPlan 输出形状：满足 ProductionPlanJson（后端 /prompt/h3 可消费）", () => {
  it("校验通过：project/scenes/validation 字段齐全", () => {
    const plan = rebuildPlanFromProject(importedProject());
    expect(plan.project.title).toBe("山雨客栈");
    expect(plan.project.source_file).toBe("山雨客栈.md");
    expect(plan.validation.status).toBe("ok");
    expect(plan.scenes[0]).toMatchObject({
      scene_id: "scene_01",
      title: "第一场：山雨楼外",
      location_name: "山雨楼外",
      time: "黄昏",
      weather: "雨",
    });
  });
});

describe("serializeH3Submission：三段式 H3 Prompt → 提交单文本", () => {
  it("三段齐全：描述/音景/配乐按 \\n 拼接（描述自带 [0-Ns] 时间轴前缀）", () => {
    const txt = serializeH3Submission(
      makeItem(
        "[0-5s] 低机位缓慢推进，湿漉漉的石阶反着暖光，檐角灯笼轻晃。",
        "雨水滴落声，檐角灯笼在风里轻响",
        "低沉弦乐，节奏舒缓",
      ),
    );
    expect(txt).toBe(
      "[0-5s] 低机位缓慢推进，湿漉漉的石阶反着暖光，檐角灯笼轻晃。\n雨水滴落声，檐角灯笼在风里轻响\n低沉弦乐，节奏舒缓",
    );
  });

  it("空分区跳过（不产生空行污染），整体 trim 生效", () => {
    const item = makeItem("  描述内容  ", "");
    expect(serializeH3Submission(item)).toBe("描述内容");
    const item2 = makeItem("", "", "");
    expect(serializeH3Submission(item2)).toBe("");
  });

  it("全部为空 → 空串（调用侧据此跳过覆盖，回退旧分区合并）", () => {
    expect(serializeH3Submission({} as H3PromptItem)).toBe("");
  });
});

describe("mapPlanPromptsToShots：/prompt/h3 结果 → {shotId: 三段提交文本}", () => {
  it("按 plan 键映射到前端 shot id；只收录结果里有的镜头", () => {
    const proj = importedProject();
    const plan = rebuildPlanFromProject(proj);
    const result: H3PromptResult = {
      schema: "minimax-h3-project-v1",
      prompts: {
        "scene_01:shot_01": makeItem("[0-5s] 雨歇黄昏，山雾漫向客栈。", "雨声渐止"),
        "scene_01:shot_02": makeItem("[0-6s] 沈青崖负剑匣走来。", "衣袂带风"),
      },
    };
    const map = mapPlanPromptsToShots(proj, plan, result);
    expect(map).toEqual({
      shot_01: "[0-5s] 雨歇黄昏，山雾漫向客栈。\n雨声渐止",
      shot_02: "[0-6s] 沈青崖负剑匣走来。\n衣袂带风",
    });
  });

  it("结果缺失镜头 / 三段序列化后空文本 → 不覆盖（回退 buildShotPromptText）", () => {
    const proj = importedProject();
    const plan = rebuildPlanFromProject(proj);
    const result: H3PromptResult = {
      schema: "minimax-h3-project-v1",
      prompts: {
        "scene_01:shot_01": makeItem("", ""), // 空三段
        // scene_01:shot_02 缺失
      },
    };
    expect(mapPlanPromptsToShots(proj, plan, result)).toEqual({});
  });

  it("plan 键与前端结构失配（多出镜头）→ 只映射能对齐的", () => {
    const proj = importedProject();
    // 前端多插一个镜头（plan 重建后也只有 2 镜 → 第三个镜头键为空被跳过）
    proj.episodes[0].scenes[0].shots.push({
      id: "shot_03",
      sceneId: "scene_01",
      order: 3,
      durationSec: 4,
      content: { visual: "" },
    });
    const plan = rebuildPlanFromProject(proj);
    const result: H3PromptResult = {
      schema: "minimax-h3-project-v1",
      prompts: { "scene_01:shot_01": makeItem("[0-5s] 描述", "音") },
    };
    const map = mapPlanPromptsToShots(proj, plan, result);
    expect(map).toEqual({ shot_01: "[0-5s] 描述\n音" });
    expect(map.shot_02).toBeUndefined();
    expect(map.shot_03).toBeUndefined();
  });
});

describe("buildDurationsFromProject：时长 → duration_by_shot（H3 [0-Ns] 时间轴）", () => {
  it("每镜正有限时长进 {scene_id:shot_id: 秒}", () => {
    const proj = handBuiltProject();
    const plan = rebuildPlanFromProject(proj);
    expect(buildDurationsFromProject(proj, plan)).toEqual({
      "scene_01:shot_01": 5,
      "scene_01:shot_02": 6,
    });
  });

  it("非法/非正时长跳过（0、NaN、缺省）", () => {
    const proj = handBuiltProject();
    proj.episodes[0].scenes[0].shots[0].durationSec = 0;
    proj.episodes[0].scenes[0].shots[1].durationSec = Number.NaN;
    const plan = rebuildPlanFromProject(proj);
    expect(buildDurationsFromProject(proj, plan)).toEqual({});
  });
});

describe("buildBindingsFromProject：本镜显式引用 → bindings_by_shot", () => {
  it("导入项目：本镜 castIds/propIds 显式资产 → entity_key（回退资产名），无引用镜头不注入", () => {
    const proj = importedProject();
    const plan = rebuildPlanFromProject(proj);
    const b = buildBindingsFromProject(proj, plan);
    // shot_01 无 castIds/propIds/locationId → 无 binding（与 P0-1 per-shot 资产边界一致）
    expect(b["scene_01:shot_01"]).toBeUndefined();
    expect(b["scene_01:shot_02"]).toEqual({
      "character:沈青崖": { asset_id: "", image_file: "minimax_studio/assets/沈青崖.png" },
      "prop:旧剑匣": { asset_id: "", image_file: "minimax_studio/assets/旧剑匣.png" },
    });
  });

  it("asset.sourceEntityId 命中 plan 实体 → entity_key 用 plan 实体键 + asset_id=实体 id", () => {
    const proj = handBuiltProject();
    proj.episodes[0].scenes[0].shots[1].castIds = ["沈青崖"];
    proj.episodes[0].scenes[0].assets = {
      cast: [{ id: "沈青崖", name: "沈青崖", kind: "cast", imageFile: "assets/青崖.png", sourceEntityId: "ent_1" }],
      locations: [],
      props: [],
      styles: [],
    };
    const plan = rebuildPlanFromProject(proj);
    // 重建 plan 注入实体表（entity_id ↔ name，后端 plan_entity_key 同款）
    plan.scenes[0].shots[1].entities = [
      {
        entity_id: "ent_1",
        name: "沈青崖",
        type: "character",
        source: "script",
        confidence: 0.95,
        asset_requirement: "required",
        aliases: [],
      },
    ];
    const b = buildBindingsFromProject(proj, plan);
    expect(b["scene_01:shot_02"]).toEqual({
      "character:沈青崖": { asset_id: "ent_1", image_file: "assets/青崖.png" },
    });
  });

  it("有 locationId 但无 location 资产 + scene.referenceImage → 兜底 location:场景名", () => {
    const proj = handBuiltProject();
    proj.episodes[0].scenes[0].shots[0].locationId = "scene_loc";
    proj.episodes[0].scenes[0].referenceImage = "minimax_studio/assets/山雨楼外.png";
    proj.episodes[0].scenes[0].assets = { cast: [], locations: [], props: [], styles: [] };
    const plan = rebuildPlanFromProject(proj);
    const b = buildBindingsFromProject(proj, plan);
    expect(b["scene_01:shot_01"]).toEqual({
      "location:山雨楼外": { asset_id: "", image_file: "minimax_studio/assets/山雨楼外.png" },
    });
  });

  it("手编项目（无 plan 实体 + 无资产池）→ 空 bindings（生成 refs 空，正常）", () => {
    const proj = handBuiltProject();
    const plan = rebuildPlanFromProject(proj);
    expect(buildBindingsFromProject(proj, plan)).toEqual({});
  });
});
