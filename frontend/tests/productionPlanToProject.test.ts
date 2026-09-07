/**
 * V1.7 Commit 3：productionPlanToProject（ProductionPlan → SPA Project）纯函数单测。
 *
 * 验证（V17_PLAN §4 数据流锁死）：
 * 1. scenes → Episode.scenes（sceneId/name/location/time/weather/order）；
 * 2. shots → Shot 骨架（id/sceneId/order/durationSec），content.visual 保持空（Phase 3/4 才生成）；
 * 3. visual_intent → shot.description 审核备注（不进 content.visual）；
 * 4. source_text 原文逐字并入 description；
 * 5. characters/props → Scene.assets 注册（按名去重），**不绑 castIds/locationId**；
 * 6. 输出绝无 Phase 2/3/4 字段（castIds/locationId/generation/h3Prompt/refs/camera）；
 * 7. planSummary：场景/镜头/角色去重/总时长；
 * 8. durationSec 合法钳制 2~8。
 */
import { describe, it, expect } from "vitest";
import { planSummary, productionPlanToProject, type ConfirmedAssetMatch } from "@/core/productionPlanToProject";
import type { ProductionPlanJson, PromptDraftItem } from "@/services/comfyApi";
// 真实后端输出 fixture（tools/spa_acceptance_shanyu.py 落盘，锁定后端真实形状）
import shanyuRealPlan from "../fixtures/shanyu_real_plan.json";
import shanyuRealMatches from "../fixtures/shanyu_real_matches.json";

const SAMPLE_PLAN: ProductionPlanJson = {
  project: { title: "山雨客栈", source_file: "" },
  scenes: [
    {
      scene_id: "scene_01",
      title: "清晨 · 客栈大堂",
      location_name: "山雨客栈大堂",
      time: "清晨",
      weather: "雨",
      shots: [
        {
          shot_id: "shot_01",
          source_text: "林雪推开客栈大门，收伞，环视堂内。",
          duration_sec: 5,
          characters: [{ name: "林雪", role: "青衫女侠" }],
          props: [{ name: "油纸伞" }],
          actions: ["推开大门", "收伞"],
          emotion: "平静",
          dialogue: [],
          visual_intent: "青衫女侠推开木门，雨水顺着伞沿滴落。",
        },
        {
          shot_id: "shot_02",
          source_text: "陈默从柜台后抬眼看向林雪，轻声说话。",
          duration_sec: 6,
          characters: [
            { name: "林雪", role: "青衫女侠" },
            { name: "陈默", role: "掌柜" },
          ],
          props: [],
          actions: ["抬眼", "说话"],
          emotion: "温和",
          dialogue: [{ speaker: "陈默", text: "这么大的雨，赶路辛苦了。" }],
          visual_intent: "",
        },
      ],
    },
  ],
  validation: { status: "valid", errors: [], warnings: [] },
};

describe("productionPlanToProject：项目骨架", () => {
  it("单集项目：name=剧本标题，episodes 长度 1", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    expect(p.id).toMatch(/^script-\d+$/);
    expect(p.name).toBe("山雨客栈");
    expect(p.episodes).toHaveLength(1);
    expect(p.episodes[0].episodeNumber).toBe(1);
    expect(p.episodes[0].title).toBe("山雨客栈");
  });

  it("title 为空 → fallback 名", () => {
    const p = productionPlanToProject(
      { ...SAMPLE_PLAN, project: { title: "", source_file: "" } },
      { projectName: "" },
    );
    expect(p.name).toBe("剧本导入项目");
  });

  it("无场景 → 空 episodes（应用时项目仍可保存）", () => {
    const p = productionPlanToProject({ ...SAMPLE_PLAN, scenes: [] });
    expect(p.episodes).toHaveLength(0);
  });
});

describe("productionPlanToProject：Scene 映射", () => {
  it("sceneId/name/location/time/weather/order 对齐", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const sc = p.episodes[0].scenes[0];
    expect(sc.id).toBe("scene_01");
    expect(sc.name).toBe("清晨 · 客栈大堂");
    expect(sc.location).toBe("山雨客栈大堂");
    expect(sc.time).toBe("清晨");
    expect(sc.weather).toBe("雨");
    expect(sc.order).toBe(1);
  });
});

describe("productionPlanToProject：Shot 映射", () => {
  it("骨架 + visual 空 + durationSec 对齐", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const sc = p.episodes[0].scenes[0];
    const s1 = sc.shots[0];
    const s2 = sc.shots[1];
    expect(s1.id).toBe("shot_01");
    expect(s1.sceneId).toBe("scene_01");
    expect(s1.order).toBe(1);
    expect(s1.durationSec).toBe(5);
    expect(s2.durationSec).toBe(6);
    // Phase 3/4 才生成 prompt：content.visual 保持空串
    expect(s1.content.visual).toBe("");
    expect(s2.content.visual).toBe("");
  });

  it("visual_intent → description（含原文），不进 content.visual", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const sc = p.episodes[0].scenes[0];
    // 有 visual_intent：备注 + 原文
    expect(sc.shots[0].description).toContain("青衫女侠推开木门");
    expect(sc.shots[0].description).toContain("原文：林雪推开客栈大门");
    // 无 visual_intent：只有原文
    expect(sc.shots[1].description).toBe("原文：陈默从柜台后抬眼看向林雪，轻声说话。");
  });

  it("duration 非法 → 钳制 2~8", () => {
    const plan: ProductionPlanJson = {
      ...SAMPLE_PLAN,
      scenes: [
        {
          ...SAMPLE_PLAN.scenes[0],
          shots: [
            {
              ...SAMPLE_PLAN.scenes[0].shots[0],
              duration_sec: 99,
            },
            {
              ...SAMPLE_PLAN.scenes[0].shots[1],
              duration_sec: 0,
            },
          ],
        },
      ],
    };
    const p = productionPlanToProject(plan);
    const shots = p.episodes[0].scenes[0].shots;
    expect(shots[0].durationSec).toBe(8);
    expect(shots[1].durationSec).toBe(2);
  });
});

describe("productionPlanToProject：资产注册（注册不绑定）", () => {
  it("characters/props → Scene.assets，按名去重", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const sc = p.episodes[0].scenes[0];
    expect(sc.assets?.cast.map((c) => c.name)).toEqual(["林雪", "陈默"]);
    expect(sc.assets?.cast[0]).toMatchObject({ kind: "cast", imageFile: "", description: "青衫女侠" });
    expect(sc.assets?.props.map((x) => x.name)).toEqual(["油纸伞"]);
    // 地点/风格不注册（Phase 1 无 location 资产）
    expect(sc.assets?.locations).toHaveLength(0);
    expect(sc.assets?.styles).toHaveLength(0);
  });

  it("不绑 castIds/locationId/defaultCastId（Phase 2 才做 Asset Registry）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const sc = p.episodes[0].scenes[0];
    expect(sc.defaultCastId).toBeUndefined();
    expect(sc.defaultLocationId).toBeUndefined();
    for (const sh of sc.shots) {
      expect(sh.castIds).toBeUndefined();
      expect(sh.castId).toBeUndefined();
      expect(sh.locationId).toBeUndefined();
      expect(sh.generation).toBeUndefined();
      expect(sh.refs).toBeUndefined();
    }
  });

  it("序列化后无 Phase 2/3/4 字段", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const blob = JSON.stringify(p);
    for (const k of ["castIds", "locationId", "assetId", "generationMode", "h3Prompt", "camera", "refs"]) {
      expect(blob).not.toContain(k);
    }
  });
});

describe("planSummary", () => {
  it("场景/镜头/角色去重/总时长", () => {
    const s = planSummary(SAMPLE_PLAN);
    expect(s.scenes).toBe(1);
    expect(s.shots).toBe(2);
    expect(s.characters).toBe(2); // 林雪、陈默
    expect(s.durationSec).toBe(11);
  });
});

describe("productionPlanToProject：Phase 2 资产绑定（传 matches）", () => {
  it("auto cast → cast 资产填 imageFile + shot.castIds 按出现角色绑定", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        { kind: "cast", name: "林雪", asset_name: "林雪", image_file: "minimax_studio/assets/林雪.png" },
        { kind: "cast", name: "陈默", asset_name: "陈默", image_file: "minimax_studio/assets/陈默.png" },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    const cast = sc.assets?.cast ?? [];
    expect(cast.find((c) => c.name === "林雪")?.imageFile).toBe("minimax_studio/assets/林雪.png");
    expect(cast.find((c) => c.name === "陈默")?.imageFile).toBe("minimax_studio/assets/陈默.png");
    // shot_01 只有林雪；shot_02 有林雪+陈默
    expect(sc.shots[0].castIds).toEqual(["林雪"]);
    expect(sc.shots[1].castIds).toEqual(["林雪", "陈默"]);
  });

  it("auto location → locations 资产注册 + defaultLocationId + shot.locationId", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        {
          kind: "location",
          name: "山雨客栈大堂",
          asset_name: "客栈大堂",
          image_file: "minimax_studio/assets/客栈大堂.png",
        },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    expect(sc.assets?.locations.map((l) => l.name)).toEqual(["山雨客栈大堂"]);
    expect(sc.assets?.locations[0].imageFile).toBe("minimax_studio/assets/客栈大堂.png");
    expect(sc.defaultLocationId).toBe("山雨客栈大堂");
    for (const sh of sc.shots) expect(sh.locationId).toBe("山雨客栈大堂");
  });

  it("auto prop → prop 资产填 imageFile", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        { kind: "prop", name: "油纸伞", asset_name: "油纸伞", image_file: "minimax_studio/assets/油纸伞.png" },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    expect(sc.assets?.props.find((x) => x.name === "油纸伞")?.imageFile).toBe(
      "minimax_studio/assets/油纸伞.png",
    );
  });

  it("部分匹配：未匹配实体不绑（imageFile 留空，castIds 不出现）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [{ kind: "cast", name: "林雪", asset_name: "林雪", image_file: "minimax_studio/assets/林雪.png" }],
    });
    const sc = p.episodes[0].scenes[0];
    expect(sc.shots[0].castIds).toEqual(["林雪"]);
    // 陈默未匹配 → 不进 castIds、imageFile 留空
    expect(sc.shots[1].castIds).toEqual(["林雪"]);
    expect(sc.assets?.cast.find((c) => c.name === "陈默")?.imageFile).toBe("");
  });

  it("不传 matches → 行为不变（注册不绑定，无 Phase 2 字段）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const sc = p.episodes[0].scenes[0];
    expect(sc.defaultLocationId).toBeUndefined();
    expect(sc.assets?.locations).toHaveLength(0);
    expect(sc.shots[0].castIds).toBeUndefined();
    expect(sc.shots[0].locationId).toBeUndefined();
    expect(sc.shots[0].refs).toBeUndefined();
    // 序列化仍无 Phase 2/3/4 绑定字段
    const blob = JSON.stringify(p);
    for (const k of ["castIds", "locationId", "refs"]) expect(blob).not.toContain(k);
  });
});

describe("productionPlanToProject：Phase 2-1 资产身份（#135 entity_key/asset_id/sourceEntityId）", () => {
  it("cast 资产带 sourceEntityId（来源实体 ent_xxx），imageFile 正常填充", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        {
          kind: "cast",
          name: "林雪",
          asset_name: "林雪",
          image_file: "minimax_studio/assets/林雪.png",
          entity_key: "character:林雪",
          asset_id: "asset_001",
          sourceEntityId: "ent_001",
        },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    const lin = sc.assets?.cast.find((c) => c.name === "林雪");
    expect(lin?.imageFile).toBe("minimax_studio/assets/林雪.png");
    expect(lin?.sourceEntityId).toBe("ent_001");
    // castIds 仍按角色名绑定（不受 asset_id 影响）
    expect(sc.shots[0].castIds).toEqual(["林雪"]);
  });

  it("location 资产带 sourceEntityId + defaultLocationId/locationId 正常", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        {
          kind: "location",
          name: "山雨客栈大堂",
          asset_name: "客栈大堂",
          image_file: "minimax_studio/assets/客栈大堂.png",
          entity_key: "location:山雨客栈大堂",
          asset_id: "asset_002",
          sourceEntityId: "ent_002",
        },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    expect(sc.assets?.locations[0].sourceEntityId).toBe("ent_002");
    expect(sc.defaultLocationId).toBe("山雨客栈大堂");
    for (const sh of sc.shots) expect(sh.locationId).toBe("山雨客栈大堂");
  });

  it("prop 资产带 sourceEntityId", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        {
          kind: "prop",
          name: "油纸伞",
          asset_name: "油纸伞",
          image_file: "minimax_studio/assets/油纸伞.png",
          entity_key: "prop:油纸伞",
          asset_id: "asset_003",
          sourceEntityId: "ent_003",
        },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    expect(sc.assets?.props.find((x) => x.name === "油纸伞")?.sourceEntityId).toBe("ent_003");
  });

  it("entity_id 与 asset_id 分离：SPA 资产 id 仍是实体名，sourceEntityId 记录 ent_xxx", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        {
          kind: "cast",
          name: "林雪",
          asset_name: "林雪",
          image_file: "minimax_studio/assets/林雪.png",
          entity_key: "character:林雪",
          asset_id: "asset_001",
          sourceEntityId: "ent_001",
        },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    const lin = sc.assets?.cast.find((c) => c.name === "林雪")!;
    // SPA 资产 id = 实体名（既有约定不变）；永久 asset_id 由后端 registry 管，不进 SPA 资产 id
    expect(lin.id).toBe("林雪");
    expect(lin.sourceEntityId).toBe("ent_001");
    expect(JSON.stringify(sc.assets)).not.toContain("asset_001");
  });

  it("旧 matches（无 Phase 2-1 字段）→ 资产不写 sourceEntityId（向后兼容）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      matches: [
        { kind: "cast", name: "林雪", asset_name: "林雪", image_file: "minimax_studio/assets/林雪.png" },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    const lin = sc.assets?.cast.find((c) => c.name === "林雪");
    expect(lin?.imageFile).toBe("minimax_studio/assets/林雪.png");
    expect(lin?.sourceEntityId).toBeUndefined();
  });
});

// V1.7 Phase 3（P0-C）：/prompt/draft 输出的五区 AI 草稿 → shot.content 映射。
const SAMPLE_DRAFTS: PromptDraftItem[] = [
  {
    scene_id: "scene_01",
    shot_id: "shot_01",
    generation_mode: "fl2v",
    draft: {
      visual: "青衫女侠推开木门，雨水顺着伞沿滴落。",
      camera: "中景跟移，随林雪移动，清晨、雨光线变化。",
      style: "电影感，冷色调，湿地面反射，雨丝可见，画面干净通透，人物边缘清晰。",
      sound: "雨声淅沥，",
      negative: "画面模糊，肢体扭曲，多余肢体，面部变形，文字水印，字幕，低质量，构图杂乱。",
      camera_intent: "motion",
    },
  },
  {
    scene_id: "scene_01",
    shot_id: "shot_02",
    generation_mode: "r2v",
    draft: {
      visual: "林雪、陈默抬眼，说话，在山雨客栈大堂清晨、雨。",
      camera: "中景，正反打对切，林雪、陈默轮流入画。",
      style: "电影感，冷色调，湿地面反射，雨丝可见，画面干净通透，人物边缘清晰。",
      sound: "雨声淅沥，对白清晰，",
      negative: "画面模糊，肢体扭曲，多余肢体，面部变形，文字水印，字幕，低质量，构图杂乱。",
      camera_intent: "dialogue",
    },
  },
];

describe("productionPlanToProject：Phase 3 AI Draft 映射（传 drafts）", () => {
  it("命中 → 五区填入 content + aiDraft=true + promptTemplateVersion", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      drafts: SAMPLE_DRAFTS,
      promptTemplateVersion: "h3-v1",
    });
    const s1 = p.episodes[0].scenes[0].shots[0];
    const d1 = SAMPLE_DRAFTS[0].draft;
    expect(s1.content.visual).toBe(d1.visual);
    expect(s1.content.cameraText).toBe(d1.camera);
    expect(s1.content.style).toBe(d1.style);
    expect(s1.content.soundText).toBe(d1.sound);
    expect(s1.content.negativePrompt).toBe(d1.negative);
    expect(s1.aiDraft).toBe(true);
    expect(s1.promptTemplateVersion).toBe("h3-v1");
  });

  it("逐镜独立映射：shot_02 用其 own draft（不串区）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      drafts: SAMPLE_DRAFTS,
      promptTemplateVersion: "h3-v1",
    });
    const s2 = p.episodes[0].scenes[0].shots[1];
    const d2 = SAMPLE_DRAFTS[1].draft;
    expect(s2.content.visual).toBe(d2.visual);
    expect(s2.content.cameraText).toBe(d2.camera);
    expect(s2.aiDraft).toBe(true);
    // shot_02 是 dialogue 镜，cameraText 应含「正反打」而非 shot_01 的「跟移」
    expect(s2.content.cameraText).toContain("正反打对切");
    expect(s2.content.cameraText).not.toContain("跟移");
  });

  it("部分命中：无 draft 的镜 content.visual 保持空、无 aiDraft/promptTemplateVersion", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      drafts: [SAMPLE_DRAFTS[0]], // 只有 shot_01
      promptTemplateVersion: "h3-v1",
    });
    const shots = p.episodes[0].scenes[0].shots;
    expect(shots[0].aiDraft).toBe(true);
    expect(shots[1].content.visual).toBe("");
    expect(shots[1].content.cameraText).toBeUndefined();
    expect(shots[1].aiDraft).toBeUndefined();
    expect(shots[1].promptTemplateVersion).toBeUndefined();
  });

  it("不传 drafts → 无 aiDraft/promptTemplateVersion，content 保持空（行为不变）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN);
    const s1 = p.episodes[0].scenes[0].shots[0];
    expect(s1.content.visual).toBe("");
    expect(s1.content.cameraText).toBeUndefined();
    expect(s1.aiDraft).toBeUndefined();
    expect(s1.promptTemplateVersion).toBeUndefined();
    const blob = JSON.stringify(p);
    expect(blob).not.toContain("aiDraft");
    expect(blob).not.toContain("promptTemplateVersion");
  });

  it("空 drafts 数组 → 行为不变", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, { drafts: [] });
    const s1 = p.episodes[0].scenes[0].shots[0];
    expect(s1.content.visual).toBe("");
    expect(s1.aiDraft).toBeUndefined();
  });

  it("跨场景 shot_id 同名不串（scene_02 的 shot_01 不用 scene_01 的草稿）", () => {
    const plan: ProductionPlanJson = {
      ...SAMPLE_PLAN,
      scenes: [
        SAMPLE_PLAN.scenes[0],
        {
          scene_id: "scene_02",
          title: "午后 · 后院石阶",
          location_name: "山雨客栈后院",
          time: "午后",
          weather: "晴",
          shots: [
            {
              shot_id: "shot_01", // 与 scene_01.shot_01 同名
              source_text: "柳如烟独自坐在石阶上。",
              duration_sec: 5,
              characters: [{ name: "柳如烟", role: "红衣女子" }],
              props: [],
              actions: ["坐"],
              emotion: "悲伤",
              dialogue: [],
              visual_intent: "柳如烟独坐石阶，眼神落寞。",
            },
          ],
        },
      ],
    };
    const p = productionPlanToProject(plan, {
      drafts: SAMPLE_DRAFTS, // 只覆盖 scene_01
      promptTemplateVersion: "h3-v1",
    });
    const scene2Shot = p.episodes[0].scenes[1].shots[0];
    // scene_02.shot_01 无草稿 → 保持空
    expect(scene2Shot.content.visual).toBe("");
    expect(scene2Shot.aiDraft).toBeUndefined();
    // #132：前端 id 全局唯一，scene_02 的镜头不再叫 shot_01（避免 findShot 跨场景错配）
    expect(scene2Shot.id).toBe("shot_03");
  });
});

describe("productionPlanToProject：Phase 5 运镜模板标记（camera_template）", () => {
  const DRAFTS_WITH_CAM: PromptDraftItem[] = [
    { ...SAMPLE_DRAFTS[0], camera_template: "walk" },
    { ...SAMPLE_DRAFTS[1], camera_template: "dialogue" },
  ];

  it("命中 camera_template → shot.cameraTemplate 记录（下拉回显）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      drafts: DRAFTS_WITH_CAM,
      promptTemplateVersion: "h3-v1",
    });
    const shots = p.episodes[0].scenes[0].shots;
    expect(shots[0].cameraTemplate).toBe("walk");
    expect(shots[1].cameraTemplate).toBe("dialogue");
  });

  it("无 camera_template 的草稿 → 不写 cameraTemplate（手编 = 自定义）", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, { drafts: SAMPLE_DRAFTS });
    const s1 = p.episodes[0].scenes[0].shots[0];
    expect(s1.cameraTemplate).toBeUndefined();
    const blob = JSON.stringify(p);
    expect(blob).not.toContain("cameraTemplate");
  });

  it("空字符串 camera_template → 不写 cameraTemplate", () => {
    const p = productionPlanToProject(SAMPLE_PLAN, {
      drafts: [{ ...SAMPLE_DRAFTS[0], camera_template: "" }],
    });
    const s1 = p.episodes[0].scenes[0].shots[0];
    expect(s1.cameraTemplate).toBeUndefined();
  });
});

// #132：后端 ProductionPlan 的 shot_id 是场景内序号（每场景从 shot_01 重置），
// 前端必须生成全局唯一 Shot.id，否则 findShot 全局首个匹配 + adoptAiDraft 幂等
// 会导致「采纳 N 个后剩余采纳按钮点击无效」（命中其它场景已采纳的同名镜头）。
describe("productionPlanToProject：跨场景 shot_id 碰撞 → 前端 id 全局唯一（#132）", () => {
  const MULTI_SCENE_PLAN: ProductionPlanJson = {
    ...SAMPLE_PLAN,
    scenes: [
      SAMPLE_PLAN.scenes[0], // scene_01：shot_01、shot_02
      {
        scene_id: "scene_02",
        title: "午后 · 后院石阶",
        location_name: "山雨客栈后院",
        time: "午后",
        weather: "晴",
        shots: [
          {
            shot_id: "shot_01", // 与 scene_01.shot_01 同名
            source_text: "柳如烟独自坐在石阶上。",
            duration_sec: 5,
            characters: [{ name: "柳如烟", role: "红衣女子" }],
            props: [],
            actions: ["坐"],
            emotion: "悲伤",
            dialogue: [],
            visual_intent: "",
          },
        ],
      },
    ],
  };

  it("前端 Shot.id 全局连续递增、无重复", () => {
    const p = productionPlanToProject(MULTI_SCENE_PLAN);
    const allIds = p.episodes[0].scenes.flatMap((sc) => sc.shots.map((s) => s.id));
    expect(allIds).toEqual(["shot_01", "shot_02", "shot_03"]);
    expect(new Set(allIds).size).toBe(allIds.length);
  });

  it("跨场景同后端 shot_id 的镜头前端 id 不同（findShot 不会错配）", () => {
    const p = productionPlanToProject(MULTI_SCENE_PLAN);
    const [sc1, sc2] = p.episodes[0].scenes;
    expect(sc1.shots[0].id).toBe("shot_01");
    expect(sc2.shots[0].id).toBe("shot_03");
    expect(sc1.shots[0].id).not.toBe(sc2.shots[0].id);
    expect(sc2.shots[0].sceneId).toBe("scene_02");
  });

  it("order 仍为场景内序号（生成/导出依赖场景内顺序）", () => {
    const p = productionPlanToProject(MULTI_SCENE_PLAN);
    const [sc1, sc2] = p.episodes[0].scenes;
    expect(sc1.shots.map((s) => s.order)).toEqual([1, 2]);
    expect(sc2.shots.map((s) => s.order)).toEqual([1]);
  });

  it("三场景回归：scene_02(7 镜)+scene_03(2 镜) 全局 id 连续不碰撞", () => {
    const scene02: ProductionPlanJson["scenes"][number] = {
      scene_id: "scene_02",
      title: "客栈大堂",
      location_name: "大堂",
      time: "",
      weather: "",
      shots: Array.from({ length: 7 }, (_, i) => ({
        shot_id: `shot_0${i + 1}`, // 与 scene_01 的 shot_01~03 全部重名
        source_text: `大堂镜头 ${i + 1}`,
        duration_sec: 4,
        characters: [],
        props: [],
        actions: [],
        emotion: "",
        dialogue: [],
        visual_intent: "",
      })),
    };
    const scene03: ProductionPlanJson["scenes"][number] = {
      scene_id: "scene_03",
      title: "后院石阶",
      location_name: "后院",
      time: "",
      weather: "",
      shots: Array.from({ length: 2 }, (_, i) => ({
        shot_id: `shot_0${i + 1}`,
        source_text: `后院镜头 ${i + 1}`,
        duration_sec: 4,
        characters: [],
        props: [],
        actions: [],
        emotion: "",
        dialogue: [],
        visual_intent: "",
      })),
    };
    const p = productionPlanToProject({ ...SAMPLE_PLAN, scenes: [SAMPLE_PLAN.scenes[0], scene02, scene03] });
    const allIds = p.episodes[0].scenes.flatMap((sc) => sc.shots.map((s) => s.id));
    // 2 + 7 + 2 = 11 镜，全局 id 唯一
    expect(allIds).toEqual(Array.from({ length: 11 }, (_, i) => `shot_${String(i + 1).padStart(2, "0")}`));
    expect(new Set(allIds).size).toBe(11);
  });
});

// #352 契约对齐：后端真实 to_dict 形状（含 Phase 1.1 entities/visual_elements）进映射层不丢字段。
// 后端 director/production_plan.py Shot.to_dict 实际输出这两组字段（TS 已补可选声明）。
// 铁律：映射层只消费 characters/props/visual_intent/source_text；entities/visual_elements
// 仅进导入弹窗漏斗展示（/assets/scan 走 entity 注册表，不走这里）——输出绝不含这两组。
describe("productionPlanToProject：真实后端形状（含 entities/visual_elements）", () => {
  const REAL_SHAPE_PLAN: ProductionPlanJson = {
    project: { title: "山雨客栈", source_file: "山雨客栈.md" },
    scenes: [
      {
        scene_id: "scene_01",
        title: "清晨 · 客栈大堂",
        location_name: "山雨客栈大堂",
        time: "清晨",
        weather: "雨",
        shots: [
          {
            shot_id: "shot_01",
            source_text: "林雪推开客栈大门，收伞，环视堂内。",
            duration_sec: 5,
            characters: [{ name: "林雪", role: "青衫女侠" }],
            props: [],
            actions: ["推开大门", "收伞"],
            emotion: "平静",
            dialogue: [],
            visual_intent: "青衫女侠推开木门，雨水顺着伞沿滴落。",
            // 后端真实输出的 Phase 1.1 字段（本测试模拟 to_dict 形状）
            entities: [
              {
                entity_id: "ent_001",
                name: "林雪",
                type: "character",
                source: "script",
                confidence: 0.98,
                asset_requirement: "required",
                aliases: [],
              },
              {
                entity_id: "ent_002",
                name: "油纸伞",
                type: "prop",
                source: "script",
                confidence: 0.92,
                asset_requirement: "recommended",
                aliases: ["油伞"],
              },
            ],
            visual_elements: [
              { name: "雨丝", type: "effect", confidence: 0.85 },
              { name: "湿地面反光", type: "environment", confidence: 0.7 },
            ],
          },
        ],
      },
    ],
    validation: { status: "valid", errors: [], warnings: [] },
  };

  it("真实形状走映射不抛错：Scene/Shot 骨架 + description（visual_intent + 原文）", () => {
    const p = productionPlanToProject(REAL_SHAPE_PLAN);
    const sc = p.episodes[0].scenes[0];
    const s1 = sc.shots[0];
    expect(sc.id).toBe("scene_01");
    expect(s1.id).toBe("shot_01");
    expect(s1.durationSec).toBe(5);
    expect(s1.description).toContain("青衫女侠推开木门"); // visual_intent
    expect(s1.description).toContain("原文：林雪推开客栈大门"); // source_text 逐字
  });

  it("castIds 绑定仍走 characters（entities 不参与绑定），输出不含 entities/visual_elements", () => {
    const p = productionPlanToProject(REAL_SHAPE_PLAN, {
      matches: [
        { kind: "cast", name: "林雪", asset_name: "林雪", image_file: "minimax_studio/assets/林雪.png" },
        { kind: "prop", name: "油纸伞", asset_name: "油纸伞", image_file: "minimax_studio/assets/油纸伞.png" },
      ],
    });
    const sc = p.episodes[0].scenes[0];
    // 绑定走 characters → castIds 正确（entities 里的同名实体不重复干扰）
    expect(sc.shots[0].castIds).toEqual(["林雪"]);
    // 序列化输出绝不含 Phase 1.1 两组字段（AI 不创建资产，只复用文件）
    const blob = JSON.stringify(p);
    expect(blob).not.toContain("entity_id");
    expect(blob).not.toContain("visual_elements");
  });

  it("planSummary 不受 entities 影响（角色去重只看 characters）", () => {
    const s = planSummary(REAL_SHAPE_PLAN);
    expect(s.scenes).toBe(1);
    expect(s.shots).toBe(1);
    expect(s.characters).toBe(1); // 仅林雪（characters 表）
    expect(s.durationSec).toBe(5);
  });
});

// ===========================================================================
// ③ SPA 真实验收《山雨客栈》：真实 ProductionPlan（3 场景 9 镜，含 Phase 1.1
// entities/visual_elements）+ 真实 auto 匹配结果 → productionPlanToProject。
// 验收点（用户 2026-08-11 拍板）：Scene 顺序=山雨楼外/客栈大堂/后院石阶、
// 2+6+1=9 镜、shot_id/source_text/duration_sec 不变、角色→castIds、
// 地点→locationId、旧剑匣→已匹配 / 旧剑→未匹配、视觉元素不进资产列表。
// ===========================================================================
const SHANYU_SCENE_SRC = {
  scene_01: [
    "雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，湿漉漉的石阶映着暖光，门前老槐树滴着水珠。",
    "沈青崖背着旧剑匣从山道走来，衣袂带风，走到客栈门前石阶停步，抬头望一眼檐角灯笼，低头推门。",
  ],
  scene_02: [
    "沈青崖推门走进大堂，暖黄灯火扑面而来。柜台后柳如烟正低头擦拭青瓷碗，闻声抬眼，二人目光相接，大堂安静片刻。",
    "柳如烟放下茶碗，从柜台后绕过，端着一碗热茶走向堂中，语气平淡：「客官，落座歇脚，茶先暖着。」",
    "沈青崖落座，接过茶碗却不喝，指节轻敲桌面，目光落在柜台上方的空剑架，低声：「这里…可曾收过一把旧剑？」",
    "客栈大门「砰」地被推开，夜风灌入，蒙面人逆光立在门口，黑纱蒙面只露双眼，腰悬窄刀，寒气漫进大堂，烛火乱晃。",
    "蒙面人一步步走近，沉声：「剑，交出来。」沈青崖起身，横剑护在柳如烟身前；柳如烟退后半步，手已按住柜台下的短刃。三人成三角对峙。",
    "蒙面人拔刀跃起，沈青崖侧身避过，旧剑出鞘寒光一闪，两人短兵相接，烛台被劲风打翻，酒坛碎裂；柳如烟退到柜台后按下短刃，观战戒备。",
  ],
  scene_03: [
    "天明雨停，后院石阶上沈青崖坐着拭剑，旧剑匣放在膝边；柳如烟端来一碗热茶放在他身侧石阶上，没多言语，转身回屋。远处山雾散开，晨光洒落。",
  ],
} as const;

/** 模拟真实后端 to_dict：rule 拆镜 + Qwen 语义补全（characters/props/visual_intent/
 *  entities/visual_elements 已合并）。source_text 与剧本逐字一致。 */
const SHANYU_PLAN: ProductionPlanJson = {
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
          source_text: SHANYU_SCENE_SRC.scene_01[0],
          duration_sec: 5,
          characters: [],
          props: [],
          actions: [],
          emotion: "宁静",
          dialogue: [],
          visual_intent: "远景建立：雨歇黄昏，山雾漫向客栈，檐角昏黄灯笼，湿石阶映暖光，老槐树滴水珠。",
          entities: [{ entity_id: "ent_001", name: "山雨楼外", type: "location", source: "script", confidence: 0.97, asset_requirement: "required", aliases: [] }],
          visual_elements: [
            { name: "山雾", type: "effect", confidence: 0.9 },
            { name: "暖黄灯火", type: "effect", confidence: 0.85 },
            { name: "湿石阶", type: "effect", confidence: 0.7 },
          ],
        },
        {
          shot_id: "shot_02",
          source_text: SHANYU_SCENE_SRC.scene_01[1],
          duration_sec: 6,
          characters: [{ name: "沈青崖", role: "" }],
          props: [{ name: "旧剑匣" }],
          actions: ["走来", "停步", "望", "推门"],
          emotion: "平静带戒备",
          dialogue: [],
          visual_intent: "沈青崖负旧剑匣从山道走来，石阶停步望檐角灯笼，低头推门。",
          entities: [
            { entity_id: "ent_002", name: "沈青崖", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_003", name: "山雨楼外", type: "location", source: "script", confidence: 0.95, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_004", name: "旧剑匣", type: "prop", source: "script", confidence: 0.92, asset_requirement: "recommended", aliases: [] },
          ],
          visual_elements: [{ name: "山雾", type: "effect", confidence: 0.65 }],
        },
      ],
    },
    {
      scene_id: "scene_02",
      title: "第二场：客栈大堂",
      location_name: "客栈大堂",
      time: "黄昏",
      weather: "雨",
      shots: [
        {
          shot_id: "shot_01",
          source_text: SHANYU_SCENE_SRC.scene_02[0],
          duration_sec: 6,
          characters: [{ name: "沈青崖", role: "" }, { name: "柳如烟", role: "" }],
          props: [{ name: "青瓷碗" }],
          actions: ["推门走进", "低头擦拭", "抬眼"],
          emotion: "目光相接，安静片刻",
          dialogue: [],
          visual_intent: "沈青崖推门进大堂，暖黄灯火扑面，柜台后柳如烟擦碗抬眼，二人目光相接。",
          entities: [
            { entity_id: "ent_005", name: "沈青崖", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_006", name: "柳如烟", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_007", name: "客栈大堂", type: "location", source: "script", confidence: 0.95, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_008", name: "青瓷碗", type: "prop", source: "script", confidence: 0.8, asset_requirement: "recommended", aliases: [] },
          ],
          visual_elements: [{ name: "暖黄灯火", type: "effect", confidence: 0.9 }],
        },
        {
          shot_id: "shot_02",
          source_text: SHANYU_SCENE_SRC.scene_02[1],
          duration_sec: 5,
          characters: [{ name: "柳如烟", role: "" }],
          props: [],
          actions: ["放下茶碗", "绕过柜台", "端茶", "走向堂中"],
          emotion: "平淡",
          dialogue: [{ speaker: "柳如烟", text: "客官，落座歇脚，茶先暖着。" }],
          visual_intent: "柳如烟放碗绕过柜台，端热茶走向堂中，语气平淡招呼。",
          entities: [{ entity_id: "ent_009", name: "柳如烟", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] }],
          visual_elements: [{ name: "暖黄灯火", type: "effect", confidence: 0.85 }],
        },
        {
          shot_id: "shot_03",
          source_text: SHANYU_SCENE_SRC.scene_02[2],
          duration_sec: 5,
          characters: [{ name: "沈青崖", role: "" }],
          props: [{ name: "旧剑" }],
          actions: ["落座", "接过茶碗", "敲桌面", "目光落在"],
          emotion: "压抑试探",
          dialogue: [{ speaker: "沈青崖", text: "这里…可曾收过一把旧剑？" }],
          visual_intent: "沈青崖落座接碗不喝，指节轻敲桌面，目光落在空剑架，低声询问。",
          entities: [
            { entity_id: "ent_010", name: "沈青崖", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_011", name: "旧剑", type: "prop", source: "script", confidence: 0.85, asset_requirement: "recommended", aliases: [] },
          ],
          visual_elements: [],
        },
        {
          shot_id: "shot_04",
          source_text: SHANYU_SCENE_SRC.scene_02[3],
          duration_sec: 5,
          characters: [{ name: "蒙面人", role: "" }],
          props: [],
          actions: ["推开", "灌入", "立在门口"],
          emotion: "压迫",
          dialogue: [],
          visual_intent: "客栈大门砰然被推开，夜风灌入，蒙面人逆光立门口，黑纱蒙面腰悬窄刀，烛火乱晃。",
          entities: [
            { entity_id: "ent_012", name: "蒙面人", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_013", name: "客栈大堂", type: "location", source: "script", confidence: 0.9, asset_requirement: "required", aliases: [] },
          ],
          visual_elements: [
            { name: "夜风", type: "effect", confidence: 0.8 },
            { name: "寒气", type: "effect", confidence: 0.75 },
            { name: "烛火", type: "effect", confidence: 0.7 },
          ],
        },
        {
          shot_id: "shot_05",
          source_text: SHANYU_SCENE_SRC.scene_02[4],
          duration_sec: 6,
          characters: [{ name: "蒙面人", role: "" }, { name: "沈青崖", role: "" }, { name: "柳如烟", role: "" }],
          props: [],
          actions: ["走近", "起身", "横剑", "退后半步", "按住"],
          emotion: "对峙",
          dialogue: [{ speaker: "蒙面人", text: "剑，交出来。" }],
          visual_intent: "蒙面人步步走近沉声要剑，沈青崖起身横剑护柳如烟，柳如烟退步按柜台下短刃，三人三角对峙。",
          entities: [
            { entity_id: "ent_014", name: "蒙面人", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_015", name: "沈青崖", type: "character", source: "script", confidence: 0.97, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_016", name: "柳如烟", type: "character", source: "script", confidence: 0.97, asset_requirement: "required", aliases: [] },
          ],
          visual_elements: [{ name: "烛火", type: "effect", confidence: 0.6 }],
        },
        {
          shot_id: "shot_06",
          source_text: SHANYU_SCENE_SRC.scene_02[5],
          duration_sec: 5,
          characters: [{ name: "蒙面人", role: "" }, { name: "沈青崖", role: "" }],
          props: [{ name: "旧剑" }, { name: "烛台" }, { name: "酒坛" }],
          actions: ["拔刀跃起", "侧身避过", "出鞘", "短兵相接"],
          emotion: "激烈打斗",
          dialogue: [],
          visual_intent: "蒙面人拔刀跃起，沈青崖侧身避过旧剑出鞘寒光一闪，短兵相接，烛台打翻酒坛碎裂，柳如烟退后按刃戒备。",
          entities: [
            { entity_id: "ent_017", name: "蒙面人", type: "character", source: "script", confidence: 0.97, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_018", name: "沈青崖", type: "character", source: "script", confidence: 0.97, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_019", name: "旧剑", type: "prop", source: "script", confidence: 0.85, asset_requirement: "recommended", aliases: [] },
            { entity_id: "ent_020", name: "烛台", type: "prop", source: "script", confidence: 0.75, asset_requirement: "recommended", aliases: [] },
            { entity_id: "ent_021", name: "酒坛", type: "prop", source: "script", confidence: 0.7, asset_requirement: "recommended", aliases: [] },
          ],
          visual_elements: [{ name: "寒光", type: "effect", confidence: 0.8 }],
        },
      ],
    },
    {
      scene_id: "scene_03",
      title: "第三场：后院石阶",
      location_name: "后院石阶",
      time: "清晨",
      weather: "晴",
      shots: [
        {
          shot_id: "shot_01",
          source_text: SHANYU_SCENE_SRC.scene_03[0],
          duration_sec: 5,
          characters: [{ name: "沈青崖", role: "" }, { name: "柳如烟", role: "" }],
          props: [{ name: "旧剑匣" }, { name: "旧剑" }],
          actions: ["坐着拭剑", "放在膝边", "端来", "放在身侧", "转身回屋"],
          emotion: "无言",
          dialogue: [],
          visual_intent: "天明雨停，后院石阶沈青崖拭剑旧剑匣放膝边，柳如烟端茶放身侧无言转身回屋，晨光洒落。",
          entities: [
            { entity_id: "ent_022", name: "沈青崖", type: "character", source: "script", confidence: 0.98, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_023", name: "柳如烟", type: "character", source: "script", confidence: 0.97, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_024", name: "后院石阶", type: "location", source: "script", confidence: 0.95, asset_requirement: "required", aliases: [] },
            { entity_id: "ent_025", name: "旧剑匣", type: "prop", source: "script", confidence: 0.9, asset_requirement: "recommended", aliases: [] },
            { entity_id: "ent_026", name: "旧剑", type: "prop", source: "script", confidence: 0.8, asset_requirement: "recommended", aliases: [] },
          ],
          visual_elements: [
            { name: "晨光", type: "effect", confidence: 0.9 },
            { name: "山雾", type: "effect", confidence: 0.6 },
          ],
        },
      ],
    },
  ],
  validation: { status: "valid", errors: [], warnings: [] },
};

/** 真实 auto 匹配结果（S2 管线输出，未匹配项不传）。 */
const SHANYU_MATCHES = [
  { kind: "cast" as const, name: "沈青崖", asset_name: "沈青崖", image_file: "minimax_studio/assets/沈青崖.png" },
  { kind: "cast" as const, name: "柳如烟", asset_name: "柳如烟", image_file: "minimax_studio/assets/柳如烟.png" },
  { kind: "cast" as const, name: "蒙面人", asset_name: "蒙面人", image_file: "minimax_studio/assets/蒙面人.png" },
  { kind: "location" as const, name: "山雨楼外", asset_name: "山雨楼外", image_file: "minimax_studio/assets/山雨楼外.png" },
  { kind: "location" as const, name: "客栈大堂", asset_name: "客栈大堂", image_file: "minimax_studio/assets/客栈大堂.png" },
  { kind: "location" as const, name: "后院石阶", asset_name: "后院石阶", image_file: "minimax_studio/assets/后院石阶.png" },
  { kind: "prop" as const, name: "旧剑匣", asset_name: "旧剑匣", image_file: "minimax_studio/assets/旧剑匣.png" },
];

describe("③ SPA 真实验收《山雨客栈》：Backend ProductionPlan → SPA Model", () => {
  const p = productionPlanToProject(SHANYU_PLAN, { matches: SHANYU_MATCHES });

  it("Scene 顺序 = 山雨楼外 → 客栈大堂 → 后院石阶，2+6+1=9 镜", () => {
    const scenes = p.episodes[0].scenes;
    expect(scenes.map((s) => s.name)).toEqual(["第一场：山雨楼外", "第二场：客栈大堂", "第三场：后院石阶"]);
    expect(scenes.map((s) => s.location)).toEqual(["山雨楼外", "客栈大堂", "后院石阶"]);
    expect(scenes.map((s) => s.shots.length)).toEqual([2, 6, 1]);
    const all = scenes.flatMap((s) => s.shots);
    expect(all).toHaveLength(9);
  });

  it("前端 Shot.id 全局唯一连续（shot_01..shot_09），order 场景内保留", () => {
    const scenes = p.episodes[0].scenes;
    const allIds = scenes.flatMap((s) => s.shots.map((x) => x.id));
    expect(allIds).toEqual(["shot_01", "shot_02", "shot_03", "shot_04", "shot_05", "shot_06", "shot_07", "shot_08", "shot_09"]);
    expect(new Set(allIds).size).toBe(9);
    expect(scenes[0].shots.map((x) => x.order)).toEqual([1, 2]);
    expect(scenes[1].shots.map((x) => x.order)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(scenes[2].shots.map((x) => x.order)).toEqual([1]);
  });

  it("shot_id / source_text / duration_sec 不变（逐字对照剧本）", () => {
    const shots = p.episodes[0].scenes.flatMap((s) => s.shots);
    const src = shots.map((x) => x.description?.split("\n").find((l) => l.startsWith("原文："))?.slice(3));
    expect(src).toEqual([...SHANYU_SCENE_SRC.scene_01, ...SHANYU_SCENE_SRC.scene_02, ...SHANYU_SCENE_SRC.scene_03]);
    expect(shots.map((x) => x.durationSec)).toEqual([5, 6, 6, 5, 5, 5, 6, 5, 5]);
    // 后端 shot_id 场景内重置，但前端 id 已全局唯一；description 首段 = visual_intent
    expect(shots[1].description).toContain("原文：沈青崖背着旧剑匣从山道走来，衣袂带风");
    expect(shots[1].description).toContain("沈青崖负旧剑匣从山道走来");
  });

  it("角色 → castIds（按镜头出现），不塞进 Shot 其它字段", () => {
    const shots = p.episodes[0].scenes.flatMap((s) => s.shots);
    expect(shots[0].castIds).toBeUndefined(); // 空镜
    expect(shots[1].castIds).toEqual(["沈青崖"]);
    expect(shots[2].castIds).toEqual(["沈青崖", "柳如烟"]);
    expect(shots[3].castIds).toEqual(["柳如烟"]);
    expect(shots[4].castIds).toEqual(["沈青崖"]);
    expect(shots[5].castIds).toEqual(["蒙面人"]);
    expect(shots[6].castIds).toEqual(["蒙面人", "沈青崖", "柳如烟"]);
    expect(shots[7].castIds).toEqual(["蒙面人", "沈青崖"]);
    expect(shots[8].castIds).toEqual(["沈青崖", "柳如烟"]);
    // cast 资产按场景独立注册（SceneAssets per scene），场景内去重
    const scenes = p.episodes[0].scenes;
    expect(scenes[0].assets!.cast.map((c) => c.name)).toEqual(["沈青崖"]);
    expect(scenes[1].assets!.cast.map((c) => c.name).sort()).toEqual(["沈青崖", "柳如烟", "蒙面人"].sort());
    expect(scenes[2].assets!.cast.map((c) => c.name).sort()).toEqual(["沈青崖", "柳如烟"].sort());
    // 有匹配的角色 imageFile 都指向资产库文件
    const castAll = scenes.flatMap((s) => s.assets!.cast);
    expect(castAll.every((c) => c.imageFile.endsWith(".png"))).toBe(true);
  });

  it("地点 → locationId + defaultLocationId（每场景全镜）", () => {
    const scenes = p.episodes[0].scenes;
    expect(scenes[0].defaultLocationId).toBe("山雨楼外");
    expect(scenes[1].defaultLocationId).toBe("客栈大堂");
    expect(scenes[2].defaultLocationId).toBe("后院石阶");
    for (const sc of scenes) {
      for (const sh of sc.shots) expect(sh.locationId).toBe(sc.location);
    }
    expect(scenes[0].assets!.locations.map((l) => l.name)).toEqual(["山雨楼外"]);
    expect(scenes[1].assets!.locations.map((l) => l.name)).toEqual(["客栈大堂"]);
    expect(scenes[2].assets!.locations.map((l) => l.name)).toEqual(["后院石阶"]);
  });

  it("旧剑匣 → 已匹配（imageFile 填充）；旧剑 → 未匹配（imageFile 留空）", () => {
    const props = p.episodes[0].scenes.flatMap((s) => s.assets!.props);
    const jianxia = props.find((x) => x.name === "旧剑匣");
    const jian = props.find((x) => x.name === "旧剑");
    expect(jianxia).toBeDefined();
    expect(jianxia?.imageFile).toBe("minimax_studio/assets/旧剑匣.png");
    expect(jian).toBeDefined();
    expect(jian?.imageFile).toBe(""); // 未匹配：复用文件绝不创建
  });

  it("视觉元素（山雾/晨光/灯火/寒气/烛火…）不进资产列表，输出无 Phase 1.1 字段", () => {
    const allAssetNames = p.episodes[0].scenes.flatMap((s) => [
      ...s.assets!.cast.map((x) => x.name),
      ...s.assets!.props.map((x) => x.name),
      ...s.assets!.locations.map((x) => x.name),
    ]);
    for (const v of ["山雾", "晨光", "暖黄灯火", "夜风", "寒气", "烛火", "寒光", "湿石阶"]) {
      expect(allAssetNames).not.toContain(v);
    }
    const blob = JSON.stringify(p);
    expect(blob).not.toContain("entity_id");
    expect(blob).not.toContain("visual_elements");
    expect(blob).not.toContain("asset_requirement");
  });

  it("真实 fixture（shanyu_real_plan/matches.json，后端 to_dict 落盘）走映射全链验收", () => {
    const pReal = productionPlanToProject(shanyuRealPlan as unknown as ProductionPlanJson, {
      matches: shanyuRealMatches as unknown as ConfirmedAssetMatch[],
    });
    const scenes = pReal.episodes[0].scenes;
    // ① Scene 顺序 + 2+6+1=9
    expect(scenes.map((s) => s.name)).toEqual(["第一场：山雨楼外", "第二场：客栈大堂", "第三场：后院石阶"]);
    expect(scenes.map((s) => s.location)).toEqual(["山雨楼外", "客栈大堂", "后院石阶"]);
    expect(scenes.map((s) => s.shots.length)).toEqual([2, 6, 1]);
    const shots = scenes.flatMap((s) => s.shots);
    expect(shots).toHaveLength(9);
    // ② 前端 Shot.id 全局唯一连续
    expect(shots.map((x) => x.id)).toEqual(["shot_01", "shot_02", "shot_03", "shot_04", "shot_05", "shot_06", "shot_07", "shot_08", "shot_09"]);
    // ③ description 含逐字原文
    expect(shots[1].description).toContain("原文：");
    expect(shots[1].description).toContain("沈青崖");
    // ④ 角色 → castIds（空镜/单角色/多人）
    expect(shots[0].castIds).toBeUndefined();
    expect(shots[1].castIds).toEqual(["沈青崖"]);
    expect(shots[5].castIds).toEqual(["蒙面人"]);
    expect(shots[6].castIds).toEqual(["蒙面人", "沈青崖", "柳如烟"]);
    // ⑤ 地点 → locationId
    for (const sc of scenes) {
      for (const sh of sc.shots) expect(sh.locationId).toBe(sc.location);
    }
    // ⑥ 旧剑匣已匹配 / 旧剑未匹配
    const props = scenes.flatMap((s) => s.assets!.props);
    const jianxia = props.find((x) => x.name === "旧剑匣");
    const jian = props.find((x) => x.name === "旧剑");
    expect(jianxia?.imageFile).toBe("minimax_studio/assets/旧剑匣.png");
    expect(jian?.imageFile).toBe("");
    // ⑦ 视觉元素不进资产列表 + 输出无 Phase 1.1 字段
    const allAssetNames = scenes.flatMap((s) => [
      ...s.assets!.cast.map((x) => x.name),
      ...s.assets!.props.map((x) => x.name),
      ...s.assets!.locations.map((x) => x.name),
    ]);
    for (const v of ["山雾", "晨光", "暖黄灯火", "夜风", "寒气", "烛火", "寒光", "湿石阶"]) {
      expect(allAssetNames).not.toContain(v);
    }
    const blob = JSON.stringify(pReal);
    expect(blob).not.toContain("entity_id");
    expect(blob).not.toContain("visual_elements");
    expect(blob).not.toContain("asset_requirement");
  });
});
