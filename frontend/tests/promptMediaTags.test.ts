// @vitest-environment happy-dom
/**
 * V1.2.7 promptMediaTags 单测：
 *   parseMediaTags 标签解析、findAtTriggerStart @ 起点探测、insertMediaTag 插入/替换、
 *   buildMediaTagCandidates 候选构建（资产四类 + 镜头 refs 分组）、filterCandidates 过滤。
 */
import { describe, it, expect } from "vitest";
import {
  parseMediaTags,
  parsePromptTokens,
  findAtTriggerStart,
  insertMediaTag,
  insertNumberedMediaTag,
  buildMediaTagCandidates,
  filterCandidates,
  type MediaTagCandidate,
} from "@/core/promptMediaTags";
import { buildAssetIndex } from "@/core/assetResolver";
import type { Asset, Shot } from "@/models/project";

const castA: Asset = { id: "cast_a", name: "林雪", kind: "cast", imageFile: "minimax_studio/assets/林雪.png" };
const locA: Asset = { id: "loc_a", name: "霓虹街区", kind: "location", imageFile: "loc/neon.png" };
const propA: Asset = { id: "prop_a", name: "能量剑", kind: "prop", imageFile: "" };
const styleA: Asset = { id: "style_a", name: "赛博朋克", kind: "style", imageFile: "" };

function makeShot(): Shot {
  return {
    id: "s1",
    sceneId: "sc1",
    order: 0,
    durationSec: 3,
    content: { visual: "" },
    refs: {
      refImages: [
        { index: 0, imageFile: "minimax_studio/assets/tail.png", fileName: "tail.png" },
      ],
      refVideos: [{ index: 0, videoFile: "minimax_studio/assets/动作.mp4", fileName: "动作.mp4" }],
      refAudios: [{ index: 0, audioFile: "minimax_studio/assets/对白.wav", fileName: "对白.wav" }],
      genImage: { imageFile: "" },
    },
  };
}

const scene = {
  id: "sc1",
  assets: { cast: [castA], locations: [locA], props: [], styles: [] },
};

describe("parseMediaTags", () => {
  it("解析 <picture> 标签，返回位置与名字", () => {
    const refs = parseMediaTags("林雪走进<picture>林雪</picture>的街道");
    expect(refs).toHaveLength(1);
    expect(refs[0].kind).toBe("picture");
    expect(refs[0].name).toBe("林雪");
    expect(refs[0].start).toBe("林雪走进".length);
    expect(refs[0].end).toBe("林雪走进".length + "<picture>林雪</picture>".length);
    expect(refs[0].innerStart).toBe("林雪走进<picture>".length);
    expect(refs[0].innerEnd).toBe("林雪走进<picture>".length + "林雪".length);
  });

  it("解析混合视频/音频标签", () => {
    const refs = parseMediaTags("a<video>动作.mp4</video>b<audio>对白.wav</audio>c");
    expect(refs.map((r) => [r.kind, r.name])).toEqual([
      ["video", "动作.mp4"],
      ["audio", "对白.wav"],
    ]);
  });

  it("开闭不匹配 / 名字为空 / 无标签 → 忽略", () => {
    expect(parseMediaTags("<picture>林雪</video>")).toHaveLength(0);
    expect(parseMediaTags("<picture>   </picture>")).toHaveLength(0);
    expect(parseMediaTags("普通文本 <b>不是媒体</b>")).toHaveLength(0);
  });
});

describe("findAtTriggerStart", () => {
  it("找到 @ 与光标间无空白的触发起点", () => {
    expect(findAtTriggerStart("@林雪", 3)).toBe(0);
    expect(findAtTriggerStart("走入 @林雪", 6)).toBe(3);
  });
  it("@ 前有空白/找不到 → -1", () => {
    expect(findAtTriggerStart("林雪 @ 雪", 5)).toBe(-1); // @ 后空格
    expect(findAtTriggerStart("无触发", 3)).toBe(-1);
    expect(findAtTriggerStart("", 0)).toBe(-1);
  });
});

describe("insertMediaTag", () => {
  it("无 @ 时在光标处纯插入标签", () => {
    const r = insertMediaTag("林雪走进街道", 4, "picture", "林雪");
    expect(r.text).toBe("林雪走进<picture>林雪</picture>街道");
    expect(r.caret).toBe(4 + "<picture>林雪</picture>".length);
  });

  it("光标前是 @（含已输入部分）→ 替换为标签，不残留 @", () => {
    const r = insertMediaTag("走入 @林雪，然后", 6, "picture", "林雪");
    expect(r.text).toBe("走入 <picture>林雪</picture>，然后");
    expect(r.text).not.toContain("@");
    expect(r.caret).toBe(3 + "<picture>林雪</picture>".length);
  });

  it("视频/音频标签同样插入", () => {
    expect(insertMediaTag("", 0, "video", "动作.mp4").text).toBe("<video>动作.mp4</video>");
    expect(insertMediaTag("", 0, "audio", "对白.wav").text).toBe("<audio>对白.wav</audio>");
  });
});

describe("insertNumberedMediaTag", () => {
  it("光标处插入官方编号标签 <Picture N>（@ 前缀替换，不残留 @）", () => {
    const r = insertNumberedMediaTag("走入 @tail.png，然后", 12, "picture", 1);
    expect(r.text).toBe("走入 <Picture 1>，然后");
    expect(r.text).not.toContain("@");
    expect(r.text).not.toContain("tail.png"); // 不显示文件名
    expect(r.caret).toBe(3 + "<Picture 1>".length);
  });

  it("无 @ 时纯插入官方编号标签", () => {
    const r = insertNumberedMediaTag("林雪走进", 4, "picture", 2);
    expect(r.text).toBe("林雪走进<Picture 2>");
    expect(r.caret).toBe(4 + "<Picture 2>".length);
  });

  it("视频/音频官方编号标签同样插入", () => {
    expect(insertNumberedMediaTag("", 0, "video", 1).text).toBe("<Video 1>");
    expect(insertNumberedMediaTag("", 0, "audio", 3).text).toBe("<Audio 3>");
  });
});

describe("buildMediaTagCandidates", () => {
  const index = buildAssetIndex([castA, locA, propA, styleA]);

  it("资产四类进 picture 候选，场景素材标 🎬/全局 🌐", () => {
    const cs = buildMediaTagCandidates(index, undefined, scene);
    const pics = cs.filter((c) => c.kind === "picture");
    expect(pics).toHaveLength(4);
    const cast = pics.find((c) => c.name === "林雪")!;
    expect(cast.source).toBe("scene");
    expect(cast.imgUrl).toContain("filename=" + encodeURIComponent("林雪.png"));
    const prop = pics.find((c) => c.name === "能量剑")!;
    expect(prop.source).toBe("global");
    expect(prop.kindLabel).toBe("道具");
  });

  it("镜头 refs 进对应 kind（图 picture/视频 video/音频 audio）", () => {
    const cs = buildMediaTagCandidates(index, makeShot(), scene);
    expect(cs.find((c) => c.name === "tail.png")?.kind).toBe("picture");
    expect(cs.find((c) => c.name === "动作.mp4")?.kind).toBe("video");
    expect(cs.find((c) => c.name === "对白.wav")?.kind).toBe("audio");
    const tail = cs.find((c) => c.name === "tail.png")!;
    expect(tail.source).toBe("ref");
    expect(tail.imgUrl).toContain("filename=" + encodeURIComponent("tail.png"));
  });

  it("ref 候选带 refIndex（段 refs 数组 0-based 下标，供插入官方编号标签）", () => {
    const shot = makeShot();
    shot.refs!.refImages = [
      { index: 0, imageFile: "minimax_studio/assets/tail.png", fileName: "tail.png" },
      { index: 1, imageFile: "minimax_studio/assets/pose.png", fileName: "pose.png" },
    ];
    const cs = buildMediaTagCandidates(index, shot, scene);
    const refs = cs.filter((c) => c.source === "ref" && c.kind === "picture");
    expect(refs.map((c) => [c.name, c.refIndex])).toEqual([
      ["tail.png", 0],
      ["pose.png", 1],
    ]);
    // 资产/场景候选无 refIndex
    const asset = cs.find((c) => c.name === "林雪")!;
    expect(asset.refIndex).toBeUndefined();
  });

  it("地点参考图 scene.referenceImage 进候选（kind picture / source scene / 缩略图）", () => {
    const sceneRef = { ...scene, referenceImage: "minimax_studio/scenes/ref.png" };
    const cs = buildMediaTagCandidates(index, undefined, sceneRef);
    const ref = cs.find((c) => c.kindLabel === "地点参考图")!;
    expect(ref).toBeDefined();
    expect(ref.kind).toBe("picture");
    expect(ref.source).toBe("scene");
    expect(ref.imgUrl).toContain("filename=" + encodeURIComponent("ref.png"));
  });

  it("无 refs / 无 scene → 资产全 global、refs 组为空", () => {
    const shot: Shot = { id: "s1", sceneId: "sc1", order: 0, durationSec: 3, content: { visual: "" } };
    const cs = buildMediaTagCandidates(index, shot, undefined);
    expect(cs.every((c) => c.source === "global")).toBe(true);
    expect(cs.filter((c) => c.source === "ref")).toHaveLength(0);
  });
});

describe("parsePromptTokens", () => {
  it("识别官方编号标签 <Picture N>/<Video N>/<Audio N>（大小写不敏感）", () => {
    const ts = parsePromptTokens("a<Picture 1>b<video 2>c<AUDIO 3>d");
    expect(ts.map((t) => [t.kind, t.numbered, t.num, t.label])).toEqual([
      ["picture", true, 1, "Picture 1"],
      ["video", true, 2, "Video 2"],
      ["audio", true, 3, "Audio 3"],
    ]);
    expect(ts[0].start).toBe(1);
    expect(ts[0].end).toBe(1 + "<Picture 1>".length);
  });

  it("无空格编号 <Picture1>/<picture1> 也识别（用户手输习惯）", () => {
    const ts = parsePromptTokens("<Picture1><picture1>");
    expect(ts.map((t) => [t.kind, t.numbered, t.num])).toEqual([
      ["picture", true, 1],
      ["picture", true, 1],
    ]);
  });

  it("识别自定义名字标签 <picture>名</picture>", () => {
    const ts = parsePromptTokens("林雪<picture>林雪</picture>街道");
    expect(ts).toHaveLength(1);
    expect(ts[0].kind).toBe("picture");
    expect(ts[0].numbered).toBe(false);
    expect(ts[0].name).toBe("林雪");
    expect(ts[0].label).toBe("林雪");
  });

  it("官方编号 + 自定义名字混合解析", () => {
    const ts = parsePromptTokens("<Picture 1><video>动作.mp4</video><Audio 2>");
    expect(ts.map((t) => [t.kind, t.numbered])).toEqual([
      ["picture", true],
      ["video", false],
      ["audio", true],
    ]);
  });

  it("忽略空名/无标签", () => {
    expect(parsePromptTokens("")).toEqual([]);
    expect(parsePromptTokens("普通文本")).toEqual([]);
    expect(parsePromptTokens("<picture>  </picture>")).toEqual([]);
  });
});

describe("filterCandidates", () => {
  it("子串过滤，大小写不敏感", () => {
    const cs: MediaTagCandidate[] = [
      { kind: "picture", name: "林雪", source: "scene", kindLabel: "人物", searchKey: "林雪" },
      { kind: "picture", name: "Snow", source: "global", kindLabel: "人物", searchKey: "snow" },
      { kind: "audio", name: "对白.wav", source: "ref", kindLabel: "参考音频", searchKey: "对白.wav" },
    ];
    expect(filterCandidates(cs, "雪")).toHaveLength(1);
    expect(filterCandidates(cs, "SNOW")).toHaveLength(1);
    expect(filterCandidates(cs, "")).toHaveLength(3);
    expect(filterCandidates(cs, "不存在")).toHaveLength(0);
  });
});
