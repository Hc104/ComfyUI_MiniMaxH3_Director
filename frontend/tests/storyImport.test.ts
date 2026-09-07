/**
 * P1-B-5（#534）：POST /minimax/director/story/import 前端 API + 映射单测。
 *
 * 覆盖：
 *  1. comfyApi.importStory —— POST /minimax/director/story/import，请求体含
 *     story_text/title/source_file/analyze，返回 {plan, rule_only, warnings}。
 *  2. importStory 空 title/sourceFile 时字段为 ""（analyze 默认 true）。
 *  3. productionPlanToProject —— plan.beats（P1-B StoryBeat 元数据层）+ timeline
 *     透传进前端 Project（Episode.timeline），beats 不参与结构校验不丢。
 *  4. 响应 type 形状：StoryImportResult.plan.beats 12 字段可读（dramatic_function）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ComfyApiClient } from "@/services/comfyApi";
import type {
  ProductionPlanJson,
  StoryImportResult,
  StoryBeatJson,
  StoryBibleJson,
  BibleEntryJson,
} from "@/services/comfyApi";
import { appendPlanAsEpisode, productionPlanToProject } from "@/core/productionPlanToProject";

function mockFetchOnce(payload: unknown, ok = true, status = 200): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async () => ({
    ok,
    status,
    statusText: ok ? "OK" : "Bad Request",
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function makeBeat(over: Partial<StoryBeatJson> = {}): StoryBeatJson {
  return {
    beat_id: "beat_01",
    title: "沈青崖进入破庙",
    summary: "风雪中推门入庙，发现断臂神像。",
    dramatic_function: "introduce_character",
    scene_id: "location_01",
    time: "雪夜",
    weather: "风雪",
    text_segments: ["沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。"],
    order: 1,
    entries: ["location_01:shot_01"],
    source: "ai",
    transition_reason: "主角登场，建立地点",
    ...over,
  };
}

function makePlan(over: Partial<ProductionPlanJson> = {}): ProductionPlanJson {
  return {
    project: { title: "山雨客栈", source_file: "" },
    scenes: [
      {
        scene_id: "location_01",
        title: "风雪山神庙",
        location_name: "风雪山神庙",
        time: "雪夜",
        weather: "风雪",
        shots: [
          {
            shot_id: "shot_01",
            source_text: "沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。",
            duration_sec: 5,
            characters: [],
            props: [],
            actions: [],
            emotion: "",
            dialogue: [],
            visual_intent: "镜头类型=establishing；景别=全景；运镜=缓摇",
          },
        ],
      },
    ],
    validation: { status: "pending", errors: [], warnings: [] },
    timeline: ["location_01:shot_01"],
    beats: [makeBeat()],
    ...over,
  };
}

const STORY = "沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。林雪提着灯笼从后殿转出来。";

describe("comfyApi.importStory（#534）", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("POST /minimax/director/story/import + 完整请求体（story_text/title/source_file/analyze）", async () => {
    const payload: StoryImportResult = { plan: makePlan(), rule_only: false, warnings: [] };
    const fetchFn = mockFetchOnce(payload);
    const api = new ComfyApiClient("http://test");
    const res = await api.importStory(STORY, {
      title: "第一章",
      sourceFile: "chapter_01.md",
      analyze: true,
    });
    expect(fetchFn).toHaveBeenCalledTimes(1);
    const [url, init] = fetchFn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://test/minimax/director/story/import");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body));
    expect(body.story_text).toBe(STORY);
    expect(body.title).toBe("第一章");
    expect(body.source_file).toBe("chapter_01.md");
    expect(body.analyze).toBe(true);
    // 返回结构稳定：plan / rule_only / warnings 三键。
    expect(res.rule_only).toBe(false);
    expect(res.warnings).toEqual([]);
    expect(res.plan.beats).toHaveLength(1);
    expect(res.plan.timeline).toEqual(["location_01:shot_01"]);
  });

  it("默认 opts：title/source_file='' 且 analyze 默认 true", async () => {
    const payload: StoryImportResult = { plan: makePlan(), rule_only: false, warnings: [] };
    const fetchFn = mockFetchOnce(payload);
    const api = new ComfyApiClient("http://test");
    await api.importStory(STORY);
    const [url, init] = fetchFn.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/story/import");
    const body = JSON.parse(String(init.body));
    expect(body.title).toBe("");
    expect(body.source_file).toBe("");
    expect(body.analyze).toBe(true);
  });

  it("rule_only=true（Qwen 失败降级）仍正常返回结构，beats.source=rule", async () => {
    const ruleBeat = makeBeat({ source: "rule", dramatic_function: "transition" });
    const payload: StoryImportResult = {
      plan: makePlan({ beats: [ruleBeat] }),
      rule_only: true,
      warnings: ["Qwen 不可用，结果未经 AI 剧情理解，需人工检查。"],
    };
    mockFetchOnce(payload);
    const api = new ComfyApiClient("http://test");
    const res = await api.importStory(STORY, { analyze: true });
    expect(res.rule_only).toBe(true);
    expect(res.plan.beats![0].source).toBe("rule");
    expect(res.warnings[0]).toContain("人工检查");
  });
});

describe("productionPlanToProject 透传 beats + timeline（#534）", () => {
  it("plan.beats + plan.timeline 映射进前端 Project 不丢失", () => {
    const project = productionPlanToProject(makePlan(), {});
    const ep = project.episodes[0];
    // plan.timeline 条目 `scene_id:shot_id` → 前端全局 Shot.id 透传（P0-5 语义）。
    expect(ep.timeline).toEqual(["shot_01"]);
    // beats 是元数据层：不参与结构映射（不出现在 scenes），但 plan 仍完整保留在后端契约里。
    expect(ep.scenes).toHaveLength(1);
    expect(ep.scenes[0].shots).toHaveLength(1);
  });

  it("plan.beats 12 字段 JSON 形状可被前端审核预览消费（dramatic_function 中文映射键齐备）", () => {
    const plan = makePlan();
    const b = plan.beats![0];
    expect(b.beat_id).toBe("beat_01");
    expect(b.dramatic_function).toBe("introduce_character");
    expect(Array.isArray(b.text_segments)).toBe(true);
    expect(b.text_segments[0]).toContain("沈青崖");
    expect(Array.isArray(b.entries)).toBe(true);
    expect(b.entries[0]).toBe("location_01:shot_01");
    // 9 个 dramatic_function 合法值集合（与后端 DRAMATIC_FUNCTIONS 对齐）。
    const valid = new Set([
      "introduce_character", "dialogue", "plant_clue", "introduce_threat",
      "confrontation", "action", "reveal", "emotional", "transition",
    ]);
    expect(valid.has(b.dramatic_function)).toBe(true);
  });

  it("plan 无 beats（旧剧本导入）向后兼容：timeline 仍透传", () => {
    const plan = makePlan();
    delete plan.beats;
    const project = productionPlanToProject(plan, {});
    expect(project.episodes[0].timeline).toEqual(["shot_01"]);
  });
});

// ---------------------------------------------------------------------------
// P2-P5（#542）：多集目标导入（project_id/episode_number）+ Bible 响应透传 +
// appendPlanAsEpisode 追加/替换集纯函数。
// ---------------------------------------------------------------------------

function makeBibleEntry(over: Partial<BibleEntryJson> = {}): BibleEntryJson {
  return {
    entity_id: "CHAR-001",
    entity_type: "character",
    name: "沈青崖",
    aliases: ["沈青山"],
    status: "confirmed",
    source: "ai",
    attributes: { 身份: "江湖刀客" },
    attribute_suggestions: [],
    current_status: "风雪中推开庙门",
    last_seen: "ep1",
    history: ["ep1：进入破庙"],
    first_seen: "ep1",
    props_held: [],
    relationships: [],
    asset_key: "character:沈青崖",
    ...over,
  };
}

function makeBible(over: Partial<StoryBibleJson> = {}): StoryBibleJson {
  return {
    bible_id: "bible_demo",
    project_id: "proj_demo",
    entries: [makeBibleEntry()],
    version: 2,
    updated_at: "2026-08-15T00:00:00.000Z",
    ...over,
  };
}

describe("comfyApi.importStory + Bible 多集目标（#542）", () => {
  it("带 projectId/episodeNumber → 请求体含 project_id/episode_number（追加为新一集）", async () => {
    const fetchFn = mockFetchOnce({ plan: makePlan(), rule_only: false, warnings: [] });
    const api = new ComfyApiClient("http://test");
    await api.importStory(STORY, { projectId: "proj_demo", episodeNumber: 3 });
    const [url, init] = fetchFn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://test/minimax/director/story/import");
    const body = JSON.parse(String(init.body));
    expect(body.project_id).toBe("proj_demo");
    expect(body.episode_number).toBe(3);
  });

  it("不带 projectId → 请求体不含 project_id（P1-B 向后兼容）", async () => {
    const fetchFn = mockFetchOnce({ plan: makePlan(), rule_only: false, warnings: [] });
    const api = new ComfyApiClient("http://test");
    await api.importStory(STORY);
    const body = JSON.parse(String(fetchFn.mock.calls[0][1].body));
    expect("project_id" in body).toBe(false);
    expect("episode_number" in body).toBe(false);
  });

  it("响应 bible + bible_updates 透传（前端上下文预览 / 变更列表数据源）", async () => {
    const payload: StoryImportResult = {
      plan: makePlan(),
      rule_only: false,
      warnings: [],
      bible: makeBible(),
      bible_updates: [
        { kind: "new_candidate", entity_id: "LOC-001", name: "风雪山神庙", message: "新建候选地点" },
        { kind: "reused", entity_id: "CHAR-001", name: "沈青崖", message: "复用已确认条目" },
      ],
    };
    mockFetchOnce(payload);
    const api = new ComfyApiClient("http://test");
    const res = await api.importStory(STORY, { projectId: "proj_demo", episodeNumber: 2 });
    expect(res.bible?.version).toBe(2);
    expect(res.bible?.entries[0].entity_id).toBe("CHAR-001");
    expect(res.bible?.entries[0].status).toBe("confirmed");
    expect(res.bible_updates).toHaveLength(2);
    expect(res.bible_updates![0].kind).toBe("new_candidate");
    expect(res.bible_updates![1].name).toBe("沈青崖");
  });
});

describe("appendPlanAsEpisode 多集追加/替换（#542）", () => {
  it("目标集不存在 → 追加为新一集（原集引用不动，plan 更新为最新导入）", () => {
    const base = productionPlanToProject(makePlan(), { projectName: "第一章" });
    expect(base.episodes).toHaveLength(1);
    const ch2 = makePlan();
    ch2.project.title = "第二章";
    ch2.scenes[0].title = "破庙内";
    ch2.scenes[0].location_name = "破庙内";
    const out = appendPlanAsEpisode(base, ch2, 2, "第二章");
    expect(out.episodes).toHaveLength(2);
    expect(out.episodes[0]).toBe(base.episodes[0]); // 原集引用不变
    const ep2 = out.episodes[1];
    expect(ep2.id).toBe("ep2");
    expect(ep2.episodeNumber).toBe(2);
    expect(ep2.title).toBe("第二章");
    expect(ep2.scenes[0].location).toBe("破庙内");
    expect(ep2.timeline).toEqual(["shot_01"]); // plan.timeline 透传
    expect(out.plan).toBe(ch2); // P0-A：plan 是当前活动集
    expect(base.episodes).toHaveLength(1); // 纯函数不 mutate 原项目
  });

  it("目标集已存在 → 整体替换该集（id 保持，scenes/timeline 换成新版）", () => {
    const base = productionPlanToProject(makePlan(), { projectName: "第一章" });
    const ch1v2 = makePlan();
    ch1v2.project.title = "第一章（修订）";
    ch1v2.scenes[0].title = "山神庙大殿";
    ch1v2.scenes[0].location_name = "山神庙大殿";
    const out = appendPlanAsEpisode(base, ch1v2, 1, "第一章（修订）");
    expect(out.episodes).toHaveLength(1);
    const ep = out.episodes[0];
    expect(ep.id).toBe(base.episodes[0].id);
    expect(ep.episodeNumber).toBe(1);
    expect(ep.title).toBe("第一章（修订）");
    expect(ep.scenes[0].location).toBe("山神庙大殿");
    expect(ep.timeline).toEqual(["shot_01"]);
    expect(out.plan).toBe(ch1v2);
  });

  it("空 plan（无 scenes）→ 不追加，原对象引用返回（不产生空集）", () => {
    const base = productionPlanToProject(makePlan(), { projectName: "第一章" });
    const empty = makePlan({ scenes: [] });
    const out = appendPlanAsEpisode(base, empty, 2, "空章");
    expect(out).toBe(base);
  });
});
