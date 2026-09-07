/**
 * P0 Story Timeline（2026-08-14 用户拍板）前端单测：
 *  1. directorCore.resolveFlattenedShots —— 拍平顺序（timeline 显式优先 + 未覆盖补齐 + 无 timeline 退化）。
 *  2. productionPlanToProject —— plan.timeline（后端 `scene_id:shot_id`）→ Episode.timeline 透传。
 *  3. workbench store reorderTimeline —— 拖拽排序 + ensureTimeline 懒初始化 + 场景树操作集合一致性。
 *
 * 背景：Scene 是「空间/资产容器」，Timeline 是「播放顺序」；同一 Scene 可在一章内多次引用，
 * 不同 Scene 按剧情交叉剪辑（P0 用户核心需求）。timeline 是播放顺序唯一权威源。
 */
import { describe, it, expect, beforeEach } from "vitest";
import type { Episode, Project, Scene, Shot } from "@/models/project";
import { resolveFlattenedShots, buildTimelineStructure } from "@/core/directorCore";
import { productionPlanToProject } from "@/core/productionPlanToProject";
import {
  loadProject,
  getCurrentEpisode,
  reorderTimeline,
  addShot,
  removeShot,
  duplicateShot,
  removeScene,
  useWorkbench,
} from "@/stores/workbench";
import type { ProductionPlanJson } from "@/services/comfyApi";

function makeShot(id: string, order: number, sceneId: string): Shot {
  return {
    id,
    sceneId,
    order,
    durationSec: 3,
    content: { visual: `镜头${id}的场面` },
    generation: { taskType: "auto", continuityMode: "auto", smartTail: false, stateChange: "", consistencyCheck: "auto" },
  };
}

function makeEpisode(opts: { timeline?: string[] } = {}): Episode {
  const sceneA: Scene = {
    id: "sc_a",
    name: "大堂",
    order: 0,
    assets: { cast: [], locations: [], props: [], styles: [] },
    shots: [makeShot("s1", 0, "sc_a"), makeShot("s2", 1, "sc_a")],
  };
  const sceneB: Scene = {
    id: "sc_b",
    name: "后院",
    order: 1,
    assets: { cast: [], locations: [], props: [], styles: [] },
    shots: [makeShot("s3", 0, "sc_b"), makeShot("s4", 1, "sc_b")],
  };
  const ep: Episode = {
    id: "ep1",
    episodeNumber: 1,
    title: "第一集",
    scenes: [sceneA, sceneB],
    assets: { cast: [], locations: [], props: [], styles: [] },
  };
  if (opts.timeline) ep.timeline = [...opts.timeline];
  return ep;
}

function makeProject(ep?: Episode): Project {
  return { id: "proj", name: "测试", createdAt: "", updatedAt: "", episodes: [ep ?? makeEpisode()] };
}

function tlIds(ep: Episode | undefined): string[] {
  return ep?.timeline ?? [];
}

describe("resolveFlattenedShots：拍平顺序（P0-6）", () => {
  it("无 timeline → 场景树顺序（scene.order 升序 → shot.order 升序）", () => {
    const ep = makeEpisode();
    expect(resolveFlattenedShots(ep).map((s) => s.id)).toEqual(["s1", "s2", "s3", "s4"]);
  });

  it("空 timeline 数组 → 同样退化场景树顺序", () => {
    const ep = makeEpisode({ timeline: [] });
    expect(resolveFlattenedShots(ep).map((s) => s.id)).toEqual(["s1", "s2", "s3", "s4"]);
  });

  it("有 timeline → 显式交叉剪辑顺序优先（大堂→后院→大堂）", () => {
    const ep = makeEpisode({ timeline: ["s1", "s3", "s2"] });
    expect(resolveFlattenedShots(ep).map((s) => s.id)).toEqual(["s1", "s3", "s2", "s4"]);
  });

  it("timeline 含未知 id → 跳过；未覆盖镜头按场景树顺序补齐（不丢镜）", () => {
    const ep = makeEpisode({ timeline: ["s1", "ghost", "s3"] });
    expect(resolveFlattenedShots(ep).map((s) => s.id)).toEqual(["s1", "s3", "s2", "s4"]);
  });

  it("timeline 含重复 id → 去重（播放顺序不重复）", () => {
    const ep = makeEpisode({ timeline: ["s1", "s3", "s1", "s3"] });
    expect(resolveFlattenedShots(ep).map((s) => s.id)).toEqual(["s1", "s3", "s2", "s4"]);
  });

  it("buildTimelineStructure 按拍平顺序产出 TimelineShot（与 UI 显示同序）", () => {
    const ep = makeEpisode({ timeline: ["s3", "s1", "s4", "s2"] });
    const structure = buildTimelineStructure({
      episode: ep,
      frameRate: 24,
      width: 864,
      height: 480,
      refMaxSize: 1024,
      output: {
        mode: "fixed",
        longEdge: 864,
        width: 864,
        height: 480,
        maxExportFrames: 0,
        exportMode: "segments",
        continuityEnabled: false,
        continuityOverlapFrames: 0,
        audioMode: "",
        qwenVlEnabled: false,
        qwenVlLevel: 0,
      },
      targetShotIds: null,
    });
    expect(structure.shots.map((s) => s.id)).toEqual(["s3", "s1", "s4", "s2"]);
  });
});

// ---------- plan.timeline → Episode.timeline 透传（P0-5） ----------

function planWithTimeline(): ProductionPlanJson {
  return {
    project: { title: "交叉剪辑", source_file: "" },
    timeline: ["scene_01:shot_02", "scene_02:shot_01", "scene_01:shot_01"],
    scenes: [
      {
        scene_id: "scene_01",
        title: "大堂",
        location_name: "山雨客栈大堂",
        time: "夜",
        weather: "雨",
        shots: [
          { shot_id: "shot_01", source_text: "A", duration_sec: 5, characters: [], props: [], actions: [], emotion: "", dialogue: [], visual_intent: "" },
          { shot_id: "shot_02", source_text: "B", duration_sec: 5, characters: [], props: [], actions: [], emotion: "", dialogue: [], visual_intent: "" },
        ],
      },
      {
        scene_id: "scene_02",
        title: "后院",
        location_name: "后院",
        time: "夜",
        weather: "雨",
        shots: [
          { shot_id: "shot_01", source_text: "C", duration_sec: 5, characters: [], props: [], actions: [], emotion: "", dialogue: [], visual_intent: "" },
          { shot_id: "shot_02", source_text: "D", duration_sec: 5, characters: [], props: [], actions: [], emotion: "", dialogue: [], visual_intent: "" },
        ],
      },
    ],
    validation: { status: "valid", errors: [], warnings: [] },
  };
}

describe("productionPlanToProject：plan.timeline → Episode.timeline（P0-5）", () => {
  it("plan.timeline 交叉剪辑条目 → 前端全局 Shot.id 顺序透传（显式优先 + 未覆盖补齐）", () => {
    const p = productionPlanToProject(planWithTimeline());
    const ep = p.episodes[0];
    // 显式条目优先：shot_02(scene01) → shot_03(scene02) → shot_01(scene01)
    expect(tlIds(ep)).toEqual(["shot_02", "shot_03", "shot_01", "shot_04"]);
    // shot_04（scene_02:shot_02）未在 plan.timeline → 导入时按场景树顺序补齐，timeline 完整不悬空
    expect(ep!.scenes.flatMap((s) => s.shots).map((s) => s.id)).toEqual(["shot_01", "shot_02", "shot_03", "shot_04"]);
    expect(resolveFlattenedShots(ep!).map((s) => s.id)).toEqual(["shot_02", "shot_03", "shot_01", "shot_04"]);
  });

  it("plan.timeline 含未知条目 → 跳过（容错），未覆盖镜头补齐", () => {
    const plan = planWithTimeline();
    plan.timeline = ["scene_09:shot_99", "scene_01:shot_02", "scene_02:shot_01"];
    const p = productionPlanToProject(plan);
    const ep = p.episodes[0];
    expect(tlIds(ep)).toEqual(["shot_02", "shot_03", "shot_01", "shot_04"]);
  });

  it("plan.timeline 缺省 → Episode 不写 timeline 字段（严格向后兼容）", () => {
    const plan = planWithTimeline();
    delete (plan as { timeline?: string[] }).timeline;
    const p = productionPlanToProject(plan);
    const ep = p.episodes[0];
    expect("timeline" in ep!).toBe(false);
    expect(resolveFlattenedShots(ep!).map((s) => s.id)).toEqual(["shot_01", "shot_02", "shot_03", "shot_04"]);
  });

  it("plan.timeline 空数组 → 不写 timeline 字段", () => {
    const plan = planWithTimeline();
    plan.timeline = [];
    const p = productionPlanToProject(plan);
    expect("timeline" in p.episodes[0]).toBe(false);
  });

  it("Shot.planShotId 记录后端场景内 shot_id", () => {
    const p = productionPlanToProject(planWithTimeline());
    const shots = p.episodes[0].scenes.flatMap((s) => s.shots);
    expect(shots.map((s) => s.planShotId)).toEqual(["shot_01", "shot_02", "shot_01", "shot_02"]);
  });
});

// ---------- workbench store reorderTimeline（P0-7） ----------

describe("reorderTimeline：拖拽排序（P0-7）", () => {
  beforeEach(() => {
    loadProject(makeProject());
  });

  it("无 timeline → 首次拖拽 ensureTimeline 按场景树顺序初始化", () => {
    const ep = getCurrentEpisode()!;
    expect(tlIds(ep)).toEqual([]);
    const changed = reorderTimeline("s1", 2); // s1 → 第 3 位
    expect(changed).toBe(true);
    expect(tlIds(ep)).toEqual(["s2", "s3", "s1", "s4"]);
  });

  it("有 timeline → 拖到目标镜头位置（落点=目标卡位置）", () => {
    loadProject(makeProject(makeEpisode({ timeline: ["s1", "s3", "s2", "s4"] })));
    const ep = getCurrentEpisode()!;
    // 把 s4 拖到 s2 的位置（index 1）
    const changed = reorderTimeline("s4", 1);
    expect(changed).toBe(true);
    expect(tlIds(ep)).toEqual(["s1", "s4", "s3", "s2"]);
  });

  it("目标索引越界 → 钳制到边界", () => {
    loadProject(makeProject(makeEpisode({ timeline: ["s1", "s3", "s2", "s4"] })));
    const ep = getCurrentEpisode()!;
    reorderTimeline("s1", 99);
    expect(tlIds(ep)).toEqual(["s3", "s2", "s4", "s1"]);
    reorderTimeline("s4", -5);
    expect(tlIds(ep)).toEqual(["s4", "s3", "s2", "s1"]);
  });

  it("同一位置 → 返回 false（不触发 touch）", () => {
    loadProject(makeProject(makeEpisode({ timeline: ["s1", "s3", "s2", "s4"] })));
    const ep = getCurrentEpisode()!;
    const before = useWorkbench().dirty;
    const changed = reorderTimeline("s3", 1);
    expect(changed).toBe(false);
    expect(tlIds(ep)).toEqual(["s1", "s3", "s2", "s4"]);
    expect(useWorkbench().dirty).toBe(before);
  });

  it("timeline 中没有的镜头 → 先补进尾部再移动（部分 timeline 的缺口由拍平补齐）", () => {
    loadProject(makeProject(makeEpisode({ timeline: ["s1", "s3"] })));
    const ep = getCurrentEpisode()!;
    const changed = reorderTimeline("s2", 0);
    expect(changed).toBe(true);
    expect(tlIds(ep)).toEqual(["s2", "s1", "s3"]);
    // s4 不在 timeline 中：拍平时按场景树顺序补齐（timeline 是播放顺序权威源，缺口可拍平）
    expect(resolveFlattenedShots(ep!).map((s) => s.id)).toEqual(["s2", "s1", "s3", "s4"]);
  });
});

describe("场景树操作与 timeline 集合一致性（P0-7）", () => {
  beforeEach(() => {
    loadProject(makeProject());
  });

  it("addShot → 新镜插入 timeline（afterShotId 后）；未拖拽过 → 懒初始化", () => {
    const ep = getCurrentEpisode()!;
    // 先拖一次激活 timeline
    reorderTimeline("s1", 0);
    const shot = addShot("sc_a", "s1")!;
    expect(ep.timeline).toContain(shot.id);
    const idx = ep.timeline!.indexOf(shot.id);
    const s1Idx = ep.timeline!.indexOf("s1");
    expect(idx).toBe(s1Idx + 1);
  });

  it("removeShot → 从 timeline 移除（不悬空）", () => {
    loadProject(makeProject(makeEpisode({ timeline: ["s1", "s2", "s3", "s4"] })));
    const ep = getCurrentEpisode()!;
    removeShot("s3");
    expect(tlIds(ep)).toEqual(["s1", "s2", "s4"]);
  });

  it("duplicateShot → 副本紧跟原镜后", () => {
    loadProject(makeProject(makeEpisode({ timeline: ["s1", "s2", "s3", "s4"] })));
    const ep = getCurrentEpisode()!;
    const copy = duplicateShot("s2")!;
    expect(ep.timeline).toEqual(["s1", "s2", copy.id, "s3", "s4"]);
  });

  it("removeScene → 场景内全部镜头从 timeline 移除", () => {
    loadProject(makeProject(makeEpisode({ timeline: ["s1", "s3", "s2", "s4"] })));
    const ep = getCurrentEpisode()!;
    removeScene("sc_b");
    expect(tlIds(ep)).toEqual(["s1", "s2"]);
    expect(resolveFlattenedShots(ep!).map((s) => s.id)).toEqual(["s1", "s2"]);
  });
});
