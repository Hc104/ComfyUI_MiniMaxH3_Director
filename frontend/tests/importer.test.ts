/**
 * 反向导入通道测试（V1.1）：
 *   timeline_data（后端契约） → TimelineImporter → Project/Episode
 *   然后 Project → buildTimelineStructure → Adapter → timeline_data
 *   round-trip 还原契约（关键字段一致）。
 */
import { describe, it, expect } from "vitest";
import { loadSampleTimeline } from "@/fixtures";
import { importProjectFromTimeline } from "@/services/projectStore";
import { buildTimelineStructure } from "@/core/directorCore";
import { MiniMaxH3Adapter } from "@/adapters/minimaxH3Adapter";

const adapter = new MiniMaxH3Adapter();

function importSample() {
  return importProjectFromTimeline(loadSampleTimeline());
}

/** 用导入的 meta 重新构建 structure（等价于导入后直接生成）。 */
function buildStructureFromImport(result: ReturnType<typeof importSample>) {
  const { project, meta } = result;
  return buildTimelineStructure({
    episode: project.episodes[0],
    frameRate: meta.frameRate,
    width: meta.output.width || meta.width,
    height: meta.output.height || meta.height,
    refMaxSize: meta.refMaxSize,
    output: {
      mode: meta.output.mode as "fixed" | "long_edge",
      longEdge: meta.output.longEdge,
      width: meta.output.width || meta.width,
      height: meta.output.height || meta.height,
      maxExportFrames: meta.output.maxExportFrames,
      exportMode: meta.output.exportMode as "segments" | "scene" | "movie" | "all",
      continuityEnabled: meta.output.continuityEnabled,
      continuityOverlapFrames: meta.output.continuityOverlapFrames,
      audioMode: meta.output.audioMode,
      qwenVlEnabled: meta.output.qwenVlEnabled,
      qwenVlLevel: meta.output.qwenVlLevel,
    },
    targetShotIds: null,
  });
}

describe("V1.1 反向导入通道 timeline_data → Project", () => {
  it("scenes/segments 按 sceneId 归组，时长恢复", () => {
    const { project, meta } = importSample();
    const ep = project.episodes[0];

    expect(ep.scenes).toHaveLength(2);
    expect(meta.frameRate).toBe(24);
    expect(meta.width).toBe(864);
    expect(meta.height).toBe(480);

    const sc1 = ep.scenes.find((s) => s.id === "sc1")!;
    const sc2 = ep.scenes.find((s) => s.id === "sc2")!;
    // s1/s2 归 sc1，s3 归 sc2，组内按 start 排序。
    expect(sc1.shots.map((s) => s.id)).toEqual(["s1", "s2"]);
    expect(sc2.shots.map((s) => s.id)).toEqual(["s3"]);

    // durationSec 从段恢复（真相源）。
    expect(sc1.shots[0].durationSec).toBe(5);
    expect(sc1.shots[1].durationSec).toBe(6);
    expect(sc2.shots[0].durationSec).toBe(6);

    // 镜头字段完整回填。
    const s1 = sc1.shots[0];
    expect(s1.content.visual).toContain("林雪");
    expect(s1.castId).toBe("cast_linxue");
    expect(s1.locationId).toBe("loc_neon");
    expect(s1.castManual).toBe(true);
    expect(s1.generation?.taskType).toBe("r2v");
    expect(s1.generation?.continuityMode).toBe("auto");
    expect(s1.generation?.stateChange).toBe("登场");
    expect(s1.refs?.refImages[0]?.imageFile).toBe("minimax_studio/cast/linxue.png");
  });

  it("全局资产 + 场景素材组导入（locations 复数兼容）", () => {
    const { project } = importSample();
    const ep = project.episodes[0];

    // 全局资产。
    expect(ep.assets?.cast.map((a) => a.name)).toEqual(["林雪"]);
    expect(ep.assets?.locations.map((a) => a.id).sort()).toEqual(["loc_neon", "loc_tower"]);

    // 场景素材组。
    const sc2 = ep.scenes.find((s) => s.id === "sc2")!;
    expect(sc2.assets?.locations.map((a) => a.id)).toEqual(["loc_tower"]);
    expect(sc2.defaultCastId).toBe("cast_linxue");
  });

  it("round-trip：导入后重新导出还原契约", () => {
    const result = importSample();
    const structure = buildStructureFromImport(result);
    const { timelineData } = adapter.buildTimelineData(structure);

    expect(timelineData.version).toBe(5);
    expect(timelineData.timelineMode).toBe("prompt_batch");
    expect(timelineData.totalFrames).toBe(406);
    expect(timelineData.frameRate).toBe(24);
    expect(timelineData.width).toBe(864);
    expect(timelineData.height).toBe(480);

    const segs = timelineData.segments as Array<Record<string, unknown>>;
    expect(segs).toHaveLength(3);
    expect(segs[0].start).toBe(0);
    expect(segs[1].start).toBe(124);
    expect(segs[2].start).toBe(265);
    expect(segs[0].durationSec).toBe(5);
    expect(segs[1].durationSec).toBe(6);
    expect(segs[2].durationSec).toBe(6);
    expect(segs[0].prompt).toContain("林雪");
    expect(segs[0].sceneId).toBe("sc1");
    expect(segs[2].sceneId).toBe("sc2");
    expect((segs[0] as { refs: Array<{ imageFile: string }> }).refs[0].imageFile).toBe(
      "minimax_studio/cast/linxue.png",
    );
  });

  it("孤儿段（sceneId 不在 scenes 块）→ 未归类场景兜底", () => {
    const timeline = loadSampleTimeline() as Record<string, unknown>;
    const segments = timeline.segments as Array<Record<string, unknown>>;
    segments[2] = { ...segments[2], sceneId: "ghost_scene" };
    const { project } = importProjectFromTimeline(timeline);

    const ep = project.episodes[0];
    const orphan = ep.scenes.find((s) => s.id === "scene_imported")!;
    expect(orphan).toBeDefined();
    expect(orphan.shots.map((s) => s.id)).toEqual(["s3"]);
  });

  it("snake_case 兼容读取", () => {
    const timeline = loadSampleTimeline() as Record<string, unknown>;
    const seg = (timeline.segments as Array<Record<string, unknown>>)[0];
    // 把关键字段改成 snake_case 再导入。
    (seg as Record<string, unknown>).duration_sec = 7;
    delete (seg as Record<string, unknown>).durationSec;
    (seg as Record<string, unknown>).cast_manual = true;
    (seg as Record<string, unknown>).task_type = "fl2v — 首尾帧生视频(First&Last to Video)";
    delete (seg as Record<string, unknown>).taskType;
    (seg as Record<string, unknown>).scene_id = "sc1";
    delete (seg as Record<string, unknown>).sceneId;

    const { project } = importProjectFromTimeline(timeline);
    const shot = project.episodes[0].scenes[0].shots[0];
    expect(shot.durationSec).toBe(7);
    expect(shot.generation?.taskType).toBe("fl2v");
    expect(shot.castManual).toBe(true);
  });
});
