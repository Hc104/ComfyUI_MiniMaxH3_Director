/**
 * DirectorCore 五模块单测（V1.1）：
 *   Asset Resolver（resolveMentions）/ Inheritance Resolver（resolveInheritance / inheritedSource）
 *   / Prompt Builder（buildShotPrompt）。
 */
import { describe, it, expect } from "vitest";
import type { Asset, Episode, Scene, Shot } from "@/models/project";
import { buildAssetIndex, resolveMentions } from "@/core/assetResolver";
import { resolveInheritance, inheritedSource, type InheritanceCtx } from "@/core/inheritanceResolver";
import { buildShotPrompt } from "@/core/promptBuilder";

const assets: Asset[] = [
  { id: "cast_linxue", name: "林雪", kind: "cast", imageFile: "cast/linxue.png" },
  { id: "loc_neon", name: "霓虹街区", kind: "location", imageFile: "loc/neon.png", aliases: ["霓虹"] },
];

describe("Asset Resolver: resolveMentions", () => {
  const idx = buildAssetIndex(assets);

  it("命中 @名字（含别名子串匹配）", () => {
    const hits = resolveMentions("@林雪走进@霓虹街区", idx);
    expect(hits.map((h) => h.assetId)).toEqual(["cast_linxue", "loc_neon"]);
    expect(hits[0]).toMatchObject({ name: "林雪", kind: "cast", start: 1, end: 3 });
    // name（霓虹街区）比 alias（霓虹）更长，保留较长匹配 end=10。
    expect(hits[1]).toMatchObject({ name: "霓虹街区", kind: "location", start: 6, end: 10 });
  });

  it("无 @ 前缀不命中；空文本无命中", () => {
    expect(resolveMentions("林雪走进霓虹街区", idx)).toHaveLength(0);
    expect(resolveMentions("", idx)).toHaveLength(0);
  });

  it("未知名字不命中", () => {
    expect(resolveMentions("@路人甲路过", idx)).toHaveLength(0);
  });

  it("同资产同区间去重（name + alias 同资产）", () => {
    const idx2 = buildAssetIndex([
      { id: "loc_neon", name: "霓虹街区", kind: "location", imageFile: "x.png", aliases: ["霓虹街区"] },
    ]);
    const hits = resolveMentions("@霓虹街区", idx2);
    expect(hits).toHaveLength(1);
    expect(hits[0].assetId).toBe("loc_neon");
  });
});

describe("Inheritance Resolver: resolveInheritance", () => {
  function ctxOf(over: Partial<Shot> = {}, sceneOver: Partial<Scene> = {}): InheritanceCtx {
    const scene: Scene = {
      id: "sc1",
      name: "霓虹街区",
      order: 0,
      defaultCastId: "cast_linxue",
      defaultLocationId: "loc_neon",
      assets: { cast: [], locations: [], props: [], styles: [] },
      shots: [],
      ...sceneOver,
    };
    const episode: Episode = {
      id: "ep1",
      episodeNumber: 1,
      title: "t",
      assets: {
        cast: [{ id: "cast_linxue", name: "林雪", kind: "cast", imageFile: "c.png" }],
        locations: [{ id: "loc_neon", name: "霓虹街区", kind: "location", imageFile: "l.png" }],
        props: [],
        styles: [],
      },
      scenes: [scene],
    };
    const shot: Shot = {
      id: "s1",
      sceneId: "sc1",
      order: 0,
      durationSec: 5,
      content: { visual: "…" },
      ...over,
    };
    return { shot, scene, episode, mentionAssetIds: new Set() };
  }

  it("优先级：显式 > 场景默认 > 全局默认", () => {
    // 显式（castManual=true）。
    const explicit = resolveInheritance(ctxOf({ castId: "cast_linxue", castManual: true }));
    expect(explicit.find((i) => i.asset.kind === "cast")).toMatchObject({
      source: "shot",
      matchedBy: "explicit",
    });

    // 无显式 → 场景默认。
    const sceneDefault = resolveInheritance(ctxOf({}));
    expect(sceneDefault.find((i) => i.asset.kind === "cast")).toMatchObject({
      source: "scene",
      matchedBy: "scene-default",
    });

    // 场景无默认 → 全局默认。
    const globalDefault = resolveInheritance(
      ctxOf({}, { defaultCastId: undefined, defaultLocationId: undefined }),
    );
    expect(globalDefault.find((i) => i.asset.kind === "cast")).toMatchObject({
      source: "project",
      matchedBy: "project-default",
    });
  });

  it("@匹配命中 → source=shot, matchedBy=mention", () => {
    const ctx = ctxOf({});
    ctx.mentionAssetIds = new Set(["cast_linxue"]);
    const inherited = resolveInheritance(ctx);
    expect(inherited.find((i) => i.asset.kind === "cast")).toMatchObject({
      source: "shot",
      matchedBy: "mention",
    });
  });

  it("V1.6-B 多角色集合：显式 castIds 逐个注入，全部 source=shot/explicit", () => {
    const sceneCast: Asset[] = [
      { id: "cast_linxue", name: "林雪", kind: "cast", imageFile: "c1.png" },
      { id: "cast_chenmo", name: "陈默", kind: "cast", imageFile: "c2.png" },
    ];
    const ctx = ctxOf(
      { castIds: ["cast_linxue", "cast_chenmo"], castManual: true },
      { assets: { cast: sceneCast, locations: [], props: [], styles: [] } },
    );
    const inherited = resolveInheritance(ctx);
    const casts = inherited.filter((i) => i.asset.kind === "cast");
    expect(casts).toHaveLength(2);
    expect(casts.map((c) => c.asset.name)).toEqual(["林雪", "陈默"]);
    for (const c of casts) expect(c).toMatchObject({ source: "shot", matchedBy: "explicit" });
  });

  it("V1.6-B canonical identity 去重：显式 ∪ @命中 按名称去重，同名不同 id 只注入一条", () => {
    const sceneCast: Asset[] = [
      { id: "cast_linxue", name: "林雪", kind: "cast", imageFile: "c1.png" },
      // 与全局 cast_linxue 同名但不同 id（复制副本），显式引用全局 id 时不应重复注入
      { id: "cast_linxue_copy", name: "林雪", kind: "cast", imageFile: "copy.png" },
      { id: "cast_chenmo", name: "陈默", kind: "cast", imageFile: "c2.png" },
    ];
    const ctx = ctxOf(
      { castIds: ["cast_linxue"], castManual: true },
      { assets: { cast: sceneCast, locations: [], props: [], styles: [] } },
    );
    // @命中陈默 + @命中林雪（显式已收录同名 → 去重不重复注入）
    ctx.mentionAssetIds = new Set(["cast_chenmo", "cast_linxue"]);
    const inherited = resolveInheritance(ctx);
    const casts = inherited.filter((i) => i.asset.kind === "cast");
    expect(casts).toHaveLength(2);
    expect(casts.map((c) => c.asset.id)).toEqual(["cast_linxue", "cast_chenmo"]);
    expect(casts.map((c) => c.matchedBy)).toEqual(["explicit", "mention"]);
  });

  it("V1.6-B @命中 cast 并入：显式未列角色由 @ 补足", () => {
    const sceneCast: Asset[] = [
      { id: "cast_linxue", name: "林雪", kind: "cast", imageFile: "c1.png" },
      { id: "cast_chenmo", name: "陈默", kind: "cast", imageFile: "c2.png" },
    ];
    const ctx = ctxOf(
      { castIds: ["cast_linxue"], castManual: true },
      { assets: { cast: sceneCast, locations: [], props: [], styles: [] } },
    );
    ctx.mentionAssetIds = new Set(["cast_chenmo"]);
    const casts = resolveInheritance(ctx).filter((i) => i.asset.kind === "cast");
    expect(casts).toHaveLength(2);
    expect(casts.map((c) => c.asset.name)).toEqual(["林雪", "陈默"]);
  });

  it("V1.6-B 旧单值兼容：castId + castManual 显式 → 集合单元素", () => {
    const ctx = ctxOf({ castId: "cast_linxue", castManual: true });
    const casts = resolveInheritance(ctx).filter((i) => i.asset.kind === "cast");
    expect(casts).toHaveLength(1);
    expect(casts[0]).toMatchObject({ source: "shot", matchedBy: "explicit" });
  });

  it("P0-1 prop/style per-shot 边界：仅 shot.propIds ∪ @命中，无场景池全量", () => {
    const sceneProps: Asset[] = [
      { id: "prop_umbrella", name: "黑伞", kind: "prop", imageFile: "p1.png" },
      { id: "prop_sword", name: "旧剑匣", kind: "prop", imageFile: "p2.png" },
    ];
    const globalProps: Asset[] = [
      { id: "prop_global_umbrella", name: "黑伞", kind: "prop", imageFile: "g1.png" },
      { id: "prop_bike", name: "摩托", kind: "prop", imageFile: "g2.png" },
    ];
    const globalStyles: Asset[] = [{ id: "style_neon", name: "霓虹赛博", kind: "style", imageFile: "s1.png" }];
    const ctx = ctxOf(
      // 空镜：本镜只显式绑定「黑伞」，场景池的旧剑匣绝不注入
      { propIds: ["prop_umbrella"], styleIds: ["style_neon"] },
      { assets: { cast: [], locations: [], props: sceneProps, styles: [] } },
    );
    ctx.episode.assets = { cast: [], locations: [], props: globalProps, styles: globalStyles };
    const inherited = resolveInheritance(ctx);
    const props = inherited.filter((i) => i.asset.kind === "prop");
    const styles = inherited.filter((i) => i.asset.kind === "style");
    // 道具：只绑定黑伞（场景池旧剑匣不进；全局摩托不补缺）
    expect(props).toHaveLength(1);
    expect(props[0]).toMatchObject({
      assetId: "prop_umbrella",
      source: "shot",
      matchedBy: "explicit",
    });
    // 风格：显式 styleIds 命中全局池
    expect(styles).toHaveLength(1);
    expect(styles[0]).toMatchObject({
      assetId: "style_neon",
      source: "shot",
      matchedBy: "explicit",
    });
  });

  it("P0-1 空镜无 propIds → prop 集合为空（场景池不再默认注入）", () => {
    const sceneProps: Asset[] = [
      // Shot 01（空镜远景）场景池里有别的镜的道具，本镜未绑定 → 不显示
      { id: "prop_sword", name: "旧剑匣", kind: "prop", imageFile: "p2.png" },
    ];
    const ctx = ctxOf(
      {},
      { assets: { cast: [], locations: [], props: sceneProps, styles: [] } },
    );
    const props = resolveInheritance(ctx).filter((i) => i.asset.kind === "prop");
    expect(props).toHaveLength(0);
  });

  it("P0-1 @命中 prop 并入（本镜提示词显式引用 → source=shot/mention）", () => {
    const sceneProps: Asset[] = [
      { id: "prop_sword", name: "旧剑匣", kind: "prop", imageFile: "p2.png" },
    ];
    const ctx = ctxOf(
      {},
      { assets: { cast: [], locations: [], props: sceneProps, styles: [] } },
    );
    ctx.mentionAssetIds = new Set(["prop_sword"]);
    const props = resolveInheritance(ctx).filter((i) => i.asset.kind === "prop");
    expect(props).toHaveLength(1);
    expect(props[0]).toMatchObject({ source: "shot", matchedBy: "mention" });
  });

  it("P0-1 显式 + @命中同名去重：同名不同 id 只注入一条", () => {
    const sceneProps: Asset[] = [
      { id: "prop_umbrella", name: "黑伞", kind: "prop", imageFile: "p1.png" },
      { id: "prop_umbrella_copy", name: "黑伞", kind: "prop", imageFile: "copy.png" },
    ];
    const ctx = ctxOf(
      { propIds: ["prop_umbrella"] },
      { assets: { cast: [], locations: [], props: sceneProps, styles: [] } },
    );
    ctx.mentionAssetIds = new Set(["prop_umbrella_copy"]);
    const props = resolveInheritance(ctx).filter((i) => i.asset.kind === "prop");
    expect(props).toHaveLength(1);
    expect(props[0]).toMatchObject({ assetId: "prop_umbrella", matchedBy: "explicit" });
  });

  it("inheritedSource 返回 none（资产不存在或未绑定）", () => {
    const ctx = ctxOf({});
    expect(inheritedSource("nope", ctx)).toBe("none");
    // 场景池有道具但本镜未绑定 → inheritedSource 也返回 none（不再回退 scene-default）
    ctx.scene.assets = {
      cast: [],
      locations: [],
      props: [{ id: "prop_sword", name: "旧剑匣", kind: "prop", imageFile: "p2.png" }],
      styles: [],
    };
    expect(inheritedSource("prop_sword", ctx)).toBe("none");
  });
});

describe("Prompt Builder: buildShotPrompt", () => {
  it("无 camera/sound：simple 取 visual，advanced 分段", () => {
    const shot: Shot = {
      id: "s1",
      sceneId: "sc1",
      order: 0,
      durationSec: 5,
      content: { visual: "林雪走过霓虹街区", negativePrompt: "text, logo" },
    };
    const p = buildShotPrompt(shot, []);
    expect(p.simple).toBe("林雪走过霓虹街区");
    expect(p.advanced.integratedMultimodalDescription).toBe("林雪走过霓虹街区");
    expect(p.advanced.negative).toBe("text, logo");
    expect(p.advanced.overallSoundscape).toBe("");
    expect(p.advanced.nonDiegeticMusic).toBe("");
  });

  it("camera + sound + 继承资产并入高级段", () => {
    const shot: Shot = {
      id: "s1",
      sceneId: "sc1",
      order: 0,
      durationSec: 5,
      content: { visual: "林雪回头", negativePrompt: "" },
      camera: { shotSize: "近景", movement: "缓慢推进", speed: "平稳", depthOfField: "浅景深" },
      sound: { ambient: "雨声", music: "低音电子" },
    };
    const inherited = [
      { assetId: "cast_linxue", asset: assets[0], source: "scene" as const, matchedBy: "scene-default" as const },
      { assetId: "loc_neon", asset: assets[1], source: "scene" as const, matchedBy: "scene-default" as const },
    ];
    const p = buildShotPrompt(shot, inherited);
    expect(p.advanced.integratedMultimodalDescription).toContain("林雪回头");
    expect(p.advanced.integratedMultimodalDescription).toContain("角色：林雪");
    expect(p.advanced.integratedMultimodalDescription).toContain("地点：霓虹街区");
    expect(p.advanced.integratedMultimodalDescription).toContain("近景");
    expect(p.advanced.overallSoundscape).toBe("雨声");
    expect(p.advanced.nonDiegeticMusic).toBe("低音电子");
  });
});
