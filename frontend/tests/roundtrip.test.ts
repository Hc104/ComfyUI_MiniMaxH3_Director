/**
 * Round-trip 测试：Project Model → Director Core → MiniMaxH3Adapter → timeline_data
 * 应产出与 sample-r2v-timeline.json 一致的契约结果。
 *
 * 这是 V1.0 数据链路正确性的核心验证：
 * 前端项目模型 = 真相；Adapter 输出 = 后端 /prompt 消费的 JSON。
 */
import { describe, it, expect } from "vitest";
import { loadSampleProject } from "@/fixtures";
import { buildTimelineStructure } from "@/core/directorCore";
import { MiniMaxH3Adapter } from "@/adapters/minimaxH3Adapter";
import { comfyViewUrl, extractVideosFromUi, firstVideoFromHistory } from "@/services/comfyApi";

const adapter = new MiniMaxH3Adapter();

function buildSampleStructure() {
  const project = loadSampleProject();
  const episode = project.episodes[0];
  return buildTimelineStructure({
    episode,
    frameRate: 24,
    width: 864,
    height: 480,
    refMaxSize: 864,
    output: {
      mode: "fixed",
      longEdge: 864,
      width: 864,
      height: 480,
      maxExportFrames: 0,
      exportMode: "all",
      continuityEnabled: false,
      continuityOverlapFrames: 9,
      audioMode: "auto",
      qwenVlEnabled: false,
      qwenVlLevel: 1,
    },
    targetShotIds: null,
  });
}

describe("auto taskType 归一化（V1.3 修复 #73）", () => {
  it("auto → 不写段 taskType（继承全局）；r2v/fl2v → 中文标签", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    const gen0 = episode.scenes[0].shots[0].generation;
    const gen1 = episode.scenes[0].shots[1].generation;
    if (gen0) gen0.taskType = "auto"; // 新镜头默认
    if (gen1) gen1.taskType = "fl2v"; // 显式首尾帧
    const structure = buildTimelineStructure({
      episode, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
      output: {
        mode: "fixed", longEdge: 864, width: 864, height: 480,
        maxExportFrames: 0, exportMode: "all", continuityEnabled: false,
        continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: null,
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    const segs = timelineData.segments as Array<Record<string, unknown>>;
    // 第 1 镜 auto → 不写 taskType 字段，后端段级继承全局 r2v。
    expect(segs[0]).not.toHaveProperty("taskType");
    // 第 2 镜 fl2v → 写中文标签（后端 resolve_task_key 按前缀解析）。
    expect(segs[1].taskType).toBe("fl2v — 首尾帧生视频(First&Last to Video)");
    // 第 3 镜（场景 2）保持 r2v。
    expect(segs[2].taskType).toBe("r2v — 参考主体生视频(Reference to Video)");
  });
});

describe("Director Core → Adapter round-trip", () => {
  it("builds timeline_data matching the sample contract", () => {
    const structure = buildSampleStructure();
    const { timelineData } = adapter.buildTimelineData(structure);

    // 顶层关键字段一致
    expect(timelineData.version).toBe(5);
    expect(timelineData.timelineMode).toBe("prompt_batch");
    expect(timelineData.editMode).toBe("segment");
    expect(timelineData.frameRate).toBe(24);
    expect(timelineData.totalFrames).toBe(406);
    expect(timelineData.width).toBe(864);
    expect(timelineData.height).toBe(480);

    // 段白名单：3 段，字段齐全
    const segs = timelineData.segments as Array<Record<string, unknown>>;
    expect(segs).toHaveLength(3);
    const SEG_KEYS = [
      "id", "start", "length", "frameCount", "durationSec", "prompt", "negativePrompt",
      "taskType", "refs", "refAudios", "refVideos", "genImage", "castId", "castIds",
      "locationId",
      "castManual", "locationManual", "stateChange", "smartTail", "sceneId",
      "continuityMode", "consistencyCheck",
    ];
    for (const seg of segs) {
      for (const k of SEG_KEYS) {
        expect(seg).toHaveProperty(k);
      }
    }

    // start 累计 + frameCount 对齐 17k+5 网格
    expect(segs[0].start).toBe(0);
    expect(segs[1].start).toBe(124);
    expect(segs[2].start).toBe(265);
    expect(segs[0].frameCount).toBe(124); // 5s * 24 = 120 → 124
    expect(segs[1].frameCount).toBe(141); // 6s * 24 = 144 → 141
    expect(segs[2].frameCount).toBe(141);
  });

  it("durationSec is the truth; frameCount derived by adapter (not in project model)", () => {
    const project = loadSampleProject();
    const ep = project.episodes[0];
    const flat = ep.scenes.flatMap((s) => s.shots);
    // 项目模型不存 frameCount
    for (const shot of flat) {
      expect(shot).not.toHaveProperty("frameCount");
      expect(shot).not.toHaveProperty("length");
      expect(shot.durationSec).toBeGreaterThan(0);
    }
  });

  it("scene assets and global assets serialized with locations plural", () => {
    const structure = buildSampleStructure();
    const { timelineData } = adapter.buildTimelineData(structure);
    const assets = timelineData.assets as Record<string, unknown>;
    expect(assets).toHaveProperty("cast");
    expect(assets).toHaveProperty("locations"); // 复数规范
    expect(assets).not.toHaveProperty("location");

    const scenes = timelineData.scenes as Array<Record<string, unknown>>;
    expect(scenes).toHaveLength(2);
    const sc1 = scenes[0] as { assets: Record<string, unknown> };
    expect(sc1.assets).toHaveProperty("locations");
  });

  it("derives start from cumulative frames", () => {
    const structure = buildSampleStructure();
    const { timelineData } = adapter.buildTimelineData(structure);
    const segs = timelineData.segments as Array<{ start: number; frameCount: number }>;
    for (let i = 1; i < segs.length; i++) {
      expect(segs[i].start).toBe(segs[i - 1].start + segs[i - 1].frameCount);
    }
  });

  it("V1.6-B 多角色 castIds 贯穿：castIds 完整、castId=首元素（旧后端/快照读单值）", () => {
    const project = loadSampleProject();
    const ep = project.episodes[0];
    const shot = ep.scenes[0].shots[0];
    shot.castIds = ["cast_linxue", "cast_chenmo"];
    shot.castManual = true;
    const structure = buildTimelineStructure({
      episode: ep,
      frameRate: 24,
      width: 864,
      height: 480,
      refMaxSize: 864,
      output: {
        mode: "fixed", longEdge: 864, width: 864, height: 480, maxExportFrames: 0,
        exportMode: "all", continuityEnabled: false, continuityOverlapFrames: 9,
        audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: null,
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    const segs = timelineData.segments as Array<Record<string, unknown>>;
    expect(segs[0].castIds).toEqual(["cast_linxue", "cast_chenmo"]);
    expect(segs[0].castId).toBe("cast_linxue");
    expect(segs[0].castManual).toBe(true);
  });
});

describe("MiniMaxH3Adapter WS 事件翻译", () => {
  it("parseProgressEvent", () => {
    const ev = adapter.parseProgressEvent({
      node_id: "5",
      segment: 2,
      segment_total: 3,
      phase: "sample",
      phase_label: "采样",
      overall_value: 5,
      overall_max: 12,
    });
    expect(ev?.nodeId).toBe("5");
    expect(ev?.segment).toBe(2);
    expect(ev?.phaseLabel).toBe("采样");
    expect(ev?.overallMax).toBe(12);
  });

  it("parsePreviewEvent", () => {
    const ev = adapter.parsePreviewEvent({
      node_id: "5",
      segment_index: 0,
      image_b64: "AAAA",
      width: 864,
      height: 480,
    });
    expect(ev?.imageB64).toBe("AAAA");
    expect(ev?.segmentIndex).toBe(0);
  });

  it("returns null for unrelated events", () => {
    expect(adapter.parseProgressEvent({})).toBeNull();
    expect(adapter.parsePreviewEvent({})).toBeNull();
    expect(adapter.parseFinishEvent({})).toBeNull();
  });
});

describe("成片回看：SaveVideo 输出 → /view URL", () => {
  it("extracts videos from executed event output (images + animated)", () => {
    // ComfyUI SaveVideo 节点的 ui 输出结构。
    const output = {
      images: [{ filename: "MiniMaxH3_Director_r2v_00001_.mp4", subfolder: "video", type: "output" }],
      animated: [true],
    };
    const videos = extractVideosFromUi(output);
    expect(videos).toHaveLength(1);
    expect(videos[0].filename).toBe("MiniMaxH3_Director_r2v_00001_.mp4");
    expect(videos[0].subfolder).toBe("video");
    expect(videos[0].type).toBe("output");
  });

  it("builds a playable /view URL", () => {
    const url = comfyViewUrl({ filename: "MiniMaxH3_Director_r2v_00001_.mp4", subfolder: "video", type: "output" });
    expect(url).toContain("/view?");
    expect(url).toContain("filename=MiniMaxH3_Director_r2v_00001_.mp4");
    expect(url).toContain("subfolder=video");
    expect(url).toContain("type=output");
  });

  it("ignores non-video outputs and empty ui", () => {
    expect(extractVideosFromUi(null)).toHaveLength(0);
    expect(extractVideosFromUi({ images: [] })).toHaveLength(0);
    expect(extractVideosFromUi({ images: [{ filename: "" }] })).toHaveLength(0);
  });

  it("firstVideoFromHistory picks the video file across node outputs", () => {
    const historyItem = {
      prompt: [] as never,
      outputs: {
        "5": { images: [{ filename: "seg_0000.png", subfolder: "", type: "output" }] },
        "7": { images: [{ filename: "MiniMaxH3_Director_r2v_00001_.mp4", subfolder: "video", type: "output" }] },
      },
      status: { completed: true },
    };
    const ref = firstVideoFromHistory(historyItem);
    expect(ref?.filename).toBe("MiniMaxH3_Director_r2v_00001_.mp4"); // 优先视频扩展名，跳过 png
    const ref2 = firstVideoFromHistory({ prompt: [] as never, outputs: {}, status: {} });
    expect(ref2).toBeNull();
  });
});

describe("工作台可编辑：时长 / 分辨率 / 单镜生成", () => {
  it("single-shot targetShotIds maps to runSelection indices", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    const structure = buildTimelineStructure({
      episode,
      frameRate: 24,
      width: 864,
      height: 480,
      refMaxSize: 864,
      output: {
        mode: "fixed", longEdge: 864, width: 864, height: 480,
        maxExportFrames: 0, exportMode: "all", continuityEnabled: false,
        continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: ["s2"],
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    // s2 是结构里第 2 个镜头（index 1），runSelection 应映射为 [1]。
    expect(timelineData.runSelectEnabled).toBe(true);
    expect(timelineData.runSelection).toEqual([1]);
  });

  it("full-run targetShotIds=null disables runSelection", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    const structure = buildTimelineStructure({
      episode, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
      output: {
        mode: "fixed", longEdge: 864, width: 864, height: 480,
        maxExportFrames: 0, exportMode: "all", continuityEnabled: false,
        continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: null,
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    expect(timelineData.runSelectEnabled).toBe(false);
    expect(timelineData.runSelection).toEqual([]);
  });

  it("editing shot.durationSec flows into derived frameCount", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    // 把第 1 镜 5s → 8s：8*24=192 帧，17k+5 网格 → 189? 192 最近 = 17*11+5=192 → 192。
    episode.scenes[0].shots[0].durationSec = 8;
    const structure = buildTimelineStructure({
      episode, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
      output: {
        mode: "fixed", longEdge: 864, width: 864, height: 480,
        maxExportFrames: 0, exportMode: "all", continuityEnabled: false,
        continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: null,
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    const segs = timelineData.segments as Array<{ frameCount: number; start: number }>;
    expect(segs[0].frameCount).toBe(192); // 8s → 192 帧（网格 17*11+5=192）
    expect(segs[1].start).toBe(192); // 第二镜 start 跟随变化
    expect(timelineData.totalFrames).toBe(192 + 141 + 141);
  });

  it("output resolution flows into timelineData", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    const structure = buildTimelineStructure({
      episode, frameRate: 24, width: 1280, height: 720, refMaxSize: 1280,
      output: {
        mode: "fixed", longEdge: 1280, width: 1280, height: 720,
        maxExportFrames: 0, exportMode: "all", continuityEnabled: false,
        continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: null,
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    expect(timelineData.width).toBe(1280);
    expect(timelineData.height).toBe(720);
    expect(timelineData.refMaxSize).toBe(1280);
  });

  it("frameRate flows into timelineData and drives frameCount", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    // 5s @ 30fps = 150 帧 → 17k+5 网格：141 (k=8, 差9) / 158 (k=9, 差8) → 取 158。
    episode.scenes[0].shots[0].durationSec = 5;
    const structure = buildTimelineStructure({
      episode, frameRate: 30, width: 864, height: 480, refMaxSize: 864,
      output: {
        mode: "fixed", longEdge: 864, width: 864, height: 480,
        maxExportFrames: 0, exportMode: "all", continuityEnabled: false,
        continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: null,
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    expect(timelineData.frameRate).toBe(30);
    const segs = timelineData.segments as Array<{ frameCount: number }>;
    expect(segs[0].frameCount).toBe(158);
  });
});

describe("@ 引用参考图槽位（V1.3 #85 修复）", () => {
  it("段 refs 的 index 与 refImages 数组下标一致（多图不互相覆盖）", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    // 参考素材区 2 张图：@ 第 1 张 → <Picture 1> ↔ ref_image_0；@ 第 2 张 → <Picture 2> ↔ ref_image_1。
    // 若 adapter 把 index 硬编码为 0，后端 refs_to_kwargs 的 dict 键会互相覆盖，只剩最后一张生效。
    const shot = episode.scenes[0].shots[0];
    shot.refs = {
      refImages: [
        { index: 0, imageFile: "minimax_studio/assets/hero.png", fileName: "hero" },
        { index: 1, imageFile: "minimax_studio/assets/sword.png", fileName: "sword" },
      ],
      refAudios: shot.refs?.refAudios ?? [],
      refVideos: shot.refs?.refVideos ?? [],
      genImage: shot.refs?.genImage ?? { imageFile: "" },
    };
    const structure = buildTimelineStructure({
      episode, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
      output: {
        mode: "fixed", longEdge: 864, width: 864, height: 480,
        maxExportFrames: 0, exportMode: "all", continuityEnabled: false,
        continuityOverlapFrames: 9, audioMode: "auto", qwenVlEnabled: false, qwenVlLevel: 1,
      },
      targetShotIds: null,
    });
    const { timelineData } = adapter.buildTimelineData(structure);
    const segs = timelineData.segments as Array<{ refs: Array<{ index: number; imageFile: string; fileName: string }> }>;
    expect(segs[0].refs).toEqual([
      { index: 0, imageFile: "minimax_studio/assets/hero.png", fileName: "hero" },
      { index: 1, imageFile: "minimax_studio/assets/sword.png", fileName: "sword" },
    ]);
  });
});

describe("P0-A（#479）：promptOverride 三段式提交文本接入 buildTimelineStructure", () => {
  const OUTPUT = {
    mode: "fixed" as const,
    longEdge: 864,
    width: 864,
    height: 480,
    maxExportFrames: 0,
    exportMode: "all" as const,
    continuityEnabled: false,
    continuityOverlapFrames: 9,
    audioMode: "auto" as const,
    qwenVlEnabled: false,
    qwenVlLevel: 1,
  };

  it("有 override 的镜头 → prompt=三段式提交文本（不用旧五区合并）；无 override 镜头 → 回退 buildShotPromptText", () => {
    const project = loadSampleProject();
    const episode = project.episodes[0];
    const shotId = episode.scenes[0].shots[0].id;
    const overrideText = "[0-5s] 低机位缓慢推进，突出檐角灯笼和湿润石阶。\n雨声渐止，檐角灯笼轻响";
    const base = buildTimelineStructure({
      episode, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
      output: OUTPUT, targetShotIds: null,
    });
    const withOverride = buildTimelineStructure({
      episode, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
      output: OUTPUT, targetShotIds: null,
      promptOverride: { [shotId]: overrideText },
    });
    const i = withOverride.shots.findIndex((s) => s.id === shotId);
    expect(withOverride.shots[i].prompt).toBe(overrideText);
    // 关键验收：override 替代的是旧五区合并结果（二者不同源）
    expect(base.shots[i].prompt).not.toBe(overrideText);
    // 未覆盖镜头（第 2 镜）→ 与 base 完全一致（回退旧分区合并，向后兼容）
    const j = withOverride.shots.findIndex((s) => s.id !== shotId);
    expect(withOverride.shots[j].prompt).toBe(base.shots[j].prompt);
    // 无 override 输入 → 结构与 base 逐镜 prompt 一致（promptOverride 缺省不改变行为）
    const noOverride = buildTimelineStructure({
      episode, frameRate: 24, width: 864, height: 480, refMaxSize: 864,
      output: OUTPUT, targetShotIds: null,
    });
    expect(noOverride.shots.map((s) => s.prompt)).toEqual(base.shots.map((s) => s.prompt));
  });
});

