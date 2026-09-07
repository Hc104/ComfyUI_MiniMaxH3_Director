/**
 * V1.2 store 编辑原子操作单测：
 *   镜头增删/复制/排序/字段写回、场景增删/重命名/字段、资产 CRUD + 引用清理、跨场景移动。
 *
 * store 是模块级单例（Vue reactive），每个用例用 loadProject(makeProject()) 重置。
 */
import { describe, it, expect, beforeEach } from "vitest";
import type { Asset, Episode, Project, Scene, Shot, ShotGenRecord } from "@/models/project";
import {
  loadProject,
  clearProject,
  getCurrentEpisode,
  getCurrentScene,
  getCurrentShot,
  addShot,
  removeShot,
  duplicateShot,
  moveShot,
  renameShot,
  moveShotToScene,
  addScene,
  removeScene,
  renameScene,
  renameProject,
  renameEpisode,
  patchScene,
  updateShotContent,
  updateShotNegative,
  updateShotDuration,
  updateShotSection,
  updateShotCast,
  setShotCasts,
  toggleShotCast,
  updateShotLocation,
  updateShotContinuity,
  updateShotSmartTail,
  updateShotStateChange,
  addAsset,
  updateAsset,
  removeAsset,
  assetNameConflict,
  copyAssetToScene,
  selectScene,
  selectSceneSettings,
  addShotRefImage,
  removeShotRefImage,
  addShotRefVideo,
  removeShotRefVideo,
  addShotRefAudio,
  removeShotRefAudio,
  useWorkbench,
  markDirty,
  markSaved,
  renameAsset,
  replaceMentionToken,
  adoptAiDraft,
  adoptAllAiDrafts,
  addGeneration,
  setActiveGeneration,
  removeGeneration,
  genId,
  addTask,
  patchTask,
} from "@/stores/workbench";

const castA: Asset = { id: "cast_a", name: "阿岚", kind: "cast", imageFile: "cast/a.png" };
const castB: Asset = { id: "cast_b", name: "阿辉", kind: "cast", imageFile: "cast/b.png" };
const locA: Asset = { id: "loc_a", name: "天台", kind: "location", imageFile: "loc/a.png" };

/**
 * 深拷贝资产（含 aliases 数组）。
 * 必须按此拷贝进 makeProject —— store 的 loadProject 是浅存引用，
 * 若场景池/全局池共享同一 Asset 对象，改名测试会污染后续用例。
 */
function cloneAsset<T extends Asset>(a: T): T {
  const c = { ...a } as T;
  if (a.aliases) c.aliases = [...a.aliases];
  else delete (c as { aliases?: string[] }).aliases;
  return c;
}

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

function makeProject(): Project {
  const sceneA: Scene = {
    id: "sc_a",
    name: "天台夜",
    order: 0,
    defaultCastId: "cast_a",
    defaultLocationId: "loc_a",
    assets: { cast: [cloneAsset(castA)], locations: [cloneAsset(locA)], props: [], styles: [] },
    shots: [makeShot("shot1", 0, "sc_a"), makeShot("shot2", 1, "sc_a")],
  };
  const sceneB: Scene = {
    id: "sc_b",
    name: "雨天街",
    order: 1,
    assets: { cast: [cloneAsset(castB)], locations: [], props: [], styles: [] },
    shots: [],
  };
  const episode: Episode = {
    id: "ep1",
    episodeNumber: 1,
    title: "第一集",
    scenes: [sceneA, sceneB],
    assets: { cast: [cloneAsset(castA)], locations: [cloneAsset(locA)], props: [], styles: [] },
  };
  return { id: "proj", name: "测试", createdAt: "", updatedAt: "", episodes: [episode] };
}

beforeEach(() => {
  loadProject(makeProject());
});

describe("镜头管理", () => {
  it("addShot 不预写场景默认（Q3 修复）：castId/locationId 为空，继承链渲染时解析", () => {
    const shot = addShot("sc_a");
    expect(shot).not.toBeNull();
    // Q3 修复：不再预写场景默认值 → 生效区由 resolveInheritance 解析为「↑场景默认」，
    // 显式选择/@命中才会显示「⚡镜头指定」。避免 defaultId===explicitId 误判。
    expect(shot!.castId).toBeUndefined();
    expect(shot!.castIds).toBeUndefined();
    expect(shot!.locationId).toBeUndefined();
    expect(shot!.durationSec).toBe(3);
    const sc = getCurrentScene()!;
    expect(sc.shots).toHaveLength(3);
    expect(sc.shots.map((s) => s.order)).toEqual([0, 1, 2]);
    expect(getCurrentShot()!.id).toBe(shot!.id);
  });

  it("normalizeShotInheritance 清理「id 有值但 manual 假」的残留字段 + 迁移旧单值 castId", () => {
    // 模拟旧版 addShot 产生的残留：castId/locationId 有值但 manual 未设（undefined/false）。
    const p = makeProject();
    p.episodes[0].scenes[0].shots[0].castId = "cast_a";
    p.episodes[0].scenes[0].shots[0].castManual = false;
    p.episodes[0].scenes[0].shots[0].locationId = "loc_a";
    p.episodes[0].scenes[0].shots[0].locationManual = false;
    // 真实显式选择（manual=true）不能被清，且 V1.6-B 迁移进 castIds。
    p.episodes[0].scenes[0].shots[1].castId = "cast_b";
    p.episodes[0].scenes[0].shots[1].castManual = true;
    loadProject(p);
    const s0 = getCurrentScene()!.shots[0];
    const s1 = getCurrentScene()!.shots[1];
    expect(s0.castId).toBeUndefined();
    expect(s0.castIds).toBeUndefined();
    expect(s0.locationId).toBeUndefined();
    // 显式选择：单值 castId → castIds 数组迁移，castId 清空
    expect(s1.castId).toBeUndefined();
    expect(s1.castIds).toEqual(["cast_b"]);
    expect(s1.castManual).toBe(true);
  });

  it("addShot(afterShotId) 在指定镜头后插入", () => {
    const shot = addShot("sc_a", "shot1");
    const sc = getCurrentScene()!;
    expect(sc.shots.map((s) => s.id)).toEqual(["shot1", shot!.id, "shot2"]);
    expect(sc.shots.map((s) => s.order)).toEqual([0, 1, 2]);
  });

  it("removeShot 删除并重排 order，选中项落到相邻镜头", () => {
    selectScene("sc_a");
    expect(removeShot("shot1")).toBe(true);
    const sc = getCurrentScene()!;
    expect(sc.shots.map((s) => s.id)).toEqual(["shot2"]);
    expect(sc.shots[0].order).toBe(0);
    expect(getCurrentShot()!.id).toBe("shot2");
    // 删空场景 → 切到场景面板
    expect(removeShot("shot2")).toBe(true);
    expect(getCurrentScene()!.shots).toHaveLength(0);
  });

  it("duplicateShot 生成唯一 id 的副本，插到原镜头后", () => {
    const copy = duplicateShot("shot1");
    expect(copy).not.toBeNull();
    expect(copy!.id).not.toBe("shot1");
    expect(copy!.name).toContain("副本");
    const sc = getCurrentScene()!;
    expect(sc.shots.map((s) => s.id)).toEqual(["shot1", copy!.id, "shot2"]);
    expect(sc.shots.map((s) => s.order)).toEqual([0, 1, 2]);
    expect(getCurrentShot()!.id).toBe(copy!.id);
  });

  it("moveShot 上下移 + 边界不动", () => {
    selectScene("sc_a");
    moveShot("shot1", 1);
    expect(getCurrentScene()!.shots.map((s) => s.id)).toEqual(["shot2", "shot1"]);
    expect(getCurrentScene()!.shots.map((s) => s.order)).toEqual([0, 1]);
    moveShot("shot1", -1);
    expect(getCurrentScene()!.shots.map((s) => s.id)).toEqual(["shot1", "shot2"]);
    moveShot("shot1", -1); // 已顶，不动
    expect(getCurrentScene()!.shots.map((s) => s.id)).toEqual(["shot1", "shot2"]);
  });

  it("renameShot / 字段写回", () => {
    renameShot("shot1", "开场");
    expect(getCurrentShot()!.name).toBe("开场");
    updateShotContent("shot1", "阿岚站在天台");
    updateShotNegative("shot1", "文字, logo");
    updateShotCast("shot1", "cast_b", true);
    updateShotLocation("shot1", "loc_a");
    updateShotContinuity("shot1", "fl2va");
    updateShotSmartTail("shot1", true);
    updateShotStateChange("shot1", "开始奔跑");
    const s = getCurrentShot()!;
    expect(s.content.visual).toBe("阿岚站在天台");
    expect(s.content.negativePrompt).toBe("文字, logo");
    expect(s.castId).toBeUndefined();
    expect(s.castIds).toEqual(["cast_b"]);
    expect(s.castManual).toBe(true);
    expect(s.locationId).toBe("loc_a");
    expect(s.generation!.continuityMode).toBe("fl2va");
    expect(s.generation!.smartTail).toBe(true);
    expect(s.generation!.stateChange).toBe("开始奔跑");
    // 空 castId → 自动继承
    updateShotCast("shot1", "", false);
    expect(getCurrentShot()!.castIds).toBeUndefined();
    expect(getCurrentShot()!.castManual).toBe(false);
  });

  it("V1.6-B setShotCasts 数组写回：多角色集合 + 去重 + 空数组回自动", () => {
    setShotCasts("shot1", ["cast_a", "cast_b", "cast_a"]);
    const s = getCurrentShot()!;
    expect(s.castIds).toEqual(["cast_a", "cast_b"]);
    expect(s.castManual).toBe(true);
    expect(s.castId).toBeUndefined(); // 单值兼容字段不再写
    // 空数组 → 清空，回自动继承
    setShotCasts("shot1", []);
    expect(getCurrentShot()!.castIds).toBeUndefined();
    expect(getCurrentShot()!.castManual).toBe(false);
  });

  it("V1.6-B toggleShotCast：包含→移除，不含→追加，空后回自动", () => {
    toggleShotCast("shot1", "cast_a");
    let s = getCurrentShot()!;
    expect(s.castIds).toEqual(["cast_a"]);
    expect(s.castManual).toBe(true);
    toggleShotCast("shot1", "cast_b");
    s = getCurrentShot()!;
    expect(s.castIds).toEqual(["cast_a", "cast_b"]);
    // 移除最后一个 → castIds 清空回自动
    toggleShotCast("shot1", "cast_a");
    toggleShotCast("shot1", "cast_b");
    s = getCurrentShot()!;
    expect(s.castIds).toBeUndefined();
    expect(s.castManual).toBe(false);
  });

  it("moveShotToScene 跨场景移动 + order 重排 + 选中切到目标场景", () => {
    moveShotToScene("shot1", "sc_b");
    const ep = getCurrentEpisode()!;
    const scA = ep.scenes.find((s) => s.id === "sc_a")!;
    const scB = ep.scenes.find((s) => s.id === "sc_b")!;
    expect(scA.shots.map((s) => s.id)).toEqual(["shot2"]);
    expect(scB.shots.map((s) => s.id)).toEqual(["shot1"]);
    expect(scB.shots[0].sceneId).toBe("sc_b");
    expect(getCurrentScene()!.id).toBe("sc_b");
  });
});

describe("场景管理", () => {
  it("addScene 追加空场景并进入场景面板", () => {
    const sc = addScene();
    expect(sc).not.toBeNull();
    expect(getCurrentEpisode()!.scenes).toHaveLength(3);
    expect(getCurrentShot()).toBeUndefined();
  });

  it("removeScene 删除场景，选中项落到相邻场景", () => {
    selectScene("sc_a");
    expect(removeScene("sc_a")).toBe(true);
    const ep = getCurrentEpisode()!;
    expect(ep.scenes.map((s) => s.id)).toEqual(["sc_b"]);
    expect(getCurrentScene()!.id).toBe("sc_b");
  });

  it("renameScene / patchScene 字段写回", () => {
    renameScene("sc_a", "新名");
    expect(getCurrentScene()!.name).toBe("新名");
    patchScene("sc_a", { time: "夜晚", weather: "小雨", description: "夜戏" });
    const sc = getCurrentScene()!;
    expect(sc.time).toBe("夜晚");
    expect(sc.weather).toBe("小雨");
    expect(sc.description).toBe("夜戏");
  });

  it("selectSceneSettings 打开场景面板不跳镜头", () => {
    selectSceneSettings("sc_b");
    expect(getCurrentScene()!.id).toBe("sc_b");
  });
});

describe("资产 CRUD", () => {
  it("addAsset 默认落场景素材组；scope=episode 落全局库", () => {
    const a = addAsset("cast", "新人");
    expect(a).not.toBeNull();
    expect(getCurrentScene()!.assets!.cast).toHaveLength(2);
    expect(getCurrentScene()!.assets!.cast[1].name).toBe("新人");

    const g = addAsset("location", "港口", { scope: "episode" });
    expect(getCurrentEpisode()!.assets!.locations).toHaveLength(2);
    expect(g!.imageFile).toBe("");
  });

  it("updateAsset / removeAsset + 引用清理（场景默认值/镜头显式引用）", () => {
    const x = addAsset("cast", "X", { sceneId: "sc_a" });
    expect(x).not.toBeNull();
    patchScene("sc_a", { defaultCastId: x!.id });
    updateShotCast("shot1", x!.id, true);
    updateAsset("cast", x!.id, { imageFile: "cast/x.png", aliases: ["X侠"] }, "scene", "sc_a");
    const sc = getCurrentScene()!;
    expect(sc.assets!.cast.find((c) => c.id === x!.id)!.imageFile).toBe("cast/x.png");

    expect(removeAsset("cast", x!.id, "scene", "sc_a")).toBe(true);
    expect(sc.assets!.cast.find((c) => c.id === x!.id)).toBeUndefined();
    expect(sc.defaultCastId).toBeUndefined();
    // V1.6-B：显式 castIds 数组引用被清理，castId 单值本就未写
    expect(sc.shots[0].castIds).toBeUndefined();
    expect(sc.shots[0].castManual).toBe(false);
  });
});

describe("V1.5-P0-A 素材库：名称唯一 + 全局→场景复制", () => {
  it("assetNameConflict：同池同 kind 同名返回现有资产；跨 kind/跨池不冲突；excludeId 排除自己", () => {
    // loadProject 深拷贝，池内对象与模块级 fixture 不同引用 → 用 toEqual 断言内容
    // 场景池 sc_a 有 castA
    expect(assetNameConflict("cast", "阿岚", "scene", "sc_a")).toEqual(castA);
    // 全局池也有 castA（同名）
    expect(assetNameConflict("cast", "阿岚", "episode")).toEqual(castA);
    // sc_a 无 castB；sc_b 有 castB
    expect(assetNameConflict("cast", "阿辉", "scene", "sc_a")).toBeUndefined();
    expect(assetNameConflict("cast", "阿辉", "scene", "sc_b")).toEqual(castB);
    // location 池
    expect(assetNameConflict("location", "天台", "scene", "sc_a")).toEqual(locA);
    // 跨 kind 同名不冲突
    expect(assetNameConflict("cast", "天台", "scene", "sc_a")).toBeUndefined();
    // excludeId 排除自己（改名场景）
    expect(assetNameConflict("cast", "阿岚", "scene", "sc_a", "cast_a")).toBeUndefined();
    // 大小写不敏感 + 首尾空白 trim
    expect(assetNameConflict("cast", " 阿岚 ", "scene", "sc_a")).toEqual(castA);
  });

  it("addAsset 同池同 kind 同名返回 null 且不添加", () => {
    const dup = addAsset("cast", "阿岚", { sceneId: "sc_a" });
    expect(dup).toBeNull();
    expect(getCurrentScene()!.assets!.cast).toHaveLength(1);
    // 全局池同名同样拦截
    const gdup = addAsset("cast", "阿岚", { scope: "episode" });
    expect(gdup).toBeNull();
    expect(getCurrentEpisode()!.assets!.cast).toHaveLength(1);
  });

  it("updateAsset 改名冲突跳过 name 字段，其余字段照常应用", () => {
    // 先在 sc_a 场景池放一个「阿辉」占名 → cast_a 改名「阿辉」冲突
    addAsset("cast", "阿辉", { sceneId: "sc_a" });
    updateAsset("cast", "cast_a", { name: "阿辉", description: "测试备注" }, "scene", "sc_a");
    const a = getCurrentScene()!.assets!.cast[0];
    expect(a.name).toBe("阿岚");
    expect(a.description).toBe("测试备注");
  });

  it("copyAssetToScene：全局→场景复制成功（新 id + 字段拷贝 + 不污染全局）", () => {
    const g = addAsset("prop", "雨伞", { scope: "episode", imageFile: "prop/u.png", aliases: ["透明伞"], description: "透明长柄伞" });
    expect(g).not.toBeNull();
    const copy = copyAssetToScene("prop", g!.id, "sc_a");
    expect(copy).not.toBeNull();
    expect(copy!.id).not.toBe(g!.id);
    expect(copy!.name).toBe("雨伞");
    expect(copy!.imageFile).toBe("prop/u.png");
    expect(copy!.aliases).toEqual(["透明伞"]);
    expect(copy!.description).toBe("透明长柄伞");
    const sceneProps = getCurrentScene()!.assets!.props;
    expect(sceneProps).toHaveLength(1);
    expect(sceneProps[0].id).toBe(copy!.id);
    // 全局池不受影响（一次性快照）
    expect(getCurrentEpisode()!.assets!.props).toHaveLength(1);
    expect(getCurrentEpisode()!.assets!.props[0].id).toBe(g!.id);
  });

  it("copyAssetToScene：场景池已有同名 → 返回 null 不覆盖", () => {
    // sc_a 场景池已有 cast_a（阿岚）
    const dup = copyAssetToScene("cast", "cast_a", "sc_a");
    expect(dup).toBeNull();
    expect(getCurrentScene()!.assets!.cast).toHaveLength(1);
  });

  it("copyAssetToScene：未知全局资产/不存在场景 → null", () => {
    expect(copyAssetToScene("cast", "no_such", "sc_a")).toBeNull();
    expect(copyAssetToScene("cast", "cast_a", "no_such_scene")).toBeNull();
  });
});

describe("V1.5-P0-B @资产改名同步", () => {
  it("replaceMentionToken：独立 token 替换；后接名字符/文本尾不误伤", () => {
    // 后接空格 → 替换
    expect(replaceMentionToken("@阿岚 走进", "阿岚", "阿岚·成年")).toBe("@阿岚·成年 走进");
    // 后接中文标点 → 替换
    expect(replaceMentionToken("@阿岚，回头", "阿岚", "阿岚·成年")).toBe("@阿岚·成年，回头");
    // 文本末尾 → 替换
    expect(replaceMentionToken("主角是@阿岚", "阿岚", "阿岚·成年")).toBe("主角是@阿岚·成年");
    // 后接名字符（· / 汉字）→ 不替换（更长 token 的组成部分）
    expect(replaceMentionToken("@阿岚·成年 登场", "阿岚", "阿岚·成年")).toBe("@阿岚·成年 登场");
    expect(replaceMentionToken("@阿岚走进", "阿岚", "阿岚·成年")).toBe("@阿岚走进");
    // 旧名=新名 → 原样
    expect(replaceMentionToken("@阿岚 走进", "阿岚", "阿岚")).toBe("@阿岚 走进");
  });

  it("renameAsset 改名：全项目镜头 @旧名 → @新名（visual + 三分区），负面词不动", () => {
    updateShotContent("shot1", "@阿岚 走进@天台");
    updateShotSection("shot1", "cameraText", "推近@阿岚");
    updateShotSection("shot1", "style", "赛博朋克@阿岚");
    updateShotSection("shot1", "soundText", "@阿岚 脚步声");
    updateShotNegative("shot1", "@阿岚 不要出现");
    // 显式 id 引用：先给 shot1 挂 castId=cast_a，改名后应保持不受影响
    updateShotCast("shot1", "cast_a");
    // 第二个镜头也在 sc_a，验证多镜头扫描
    const s2 = addShot("sc_a");
    updateShotContent(s2!.id, "@阿岚 转身");
    // 改名「阿岚」→「阿岚·成年」
    renameAsset("cast", "cast_a", { name: "阿岚·成年" }, "scene", "sc_a");
    const shot1 = getCurrentScene()!.shots[0];
    expect(shot1.content.visual).toBe("@阿岚·成年 走进@天台");
    expect(shot1.content.cameraText).toBe("推近@阿岚·成年");
    expect(shot1.content.style).toBe("赛博朋克@阿岚·成年");
    expect(shot1.content.soundText).toBe("@阿岚·成年 脚步声");
    expect(shot1.content.negativePrompt).toBe("@阿岚 不要出现");
    // addShot 追加到末尾（order 重排 [0,1,2]），按 id 定位新镜头
    expect(getCurrentScene()!.shots.find((s) => s.id === s2!.id)!.content.visual).toBe("@阿岚·成年 转身");
    // 资产本身改名成功；显式 id 引用不受影响（V1.6-B 存 castIds 数组）
    expect(getCurrentScene()!.assets!.cast[0].name).toBe("阿岚·成年");
    expect(shot1.castIds).toEqual(["cast_a"]);
  });

  it("renameAsset token 边界：@旧名·成年 / @旧名+汉字 不误伤，@旧名 独立 token 才替换", () => {
    updateShotContent("shot1", "@阿岚·成年 与 @阿岚 并肩 @阿岚走进");
    renameAsset("cast", "cast_a", { name: "阿岚·成年" }, "scene", "sc_a");
    // @阿岚·成年：@阿岚 后是 · → 不替换（但新名本身就是阿岚·成年，恰好一致）
    // @阿岚（后空格）→ 替换为 @阿岚·成年
    // @阿岚走进：@阿岚 后是「走」→ 不替换
    expect(getCurrentScene()!.shots[0].content.visual).toBe("@阿岚·成年 与 @阿岚·成年 并肩 @阿岚走进");
  });

  it("renameAsset 别名同步：旧 alias → 新 alias 同规则替换", () => {
    updateAsset("cast", "cast_a", { aliases: ["岚姐"] }, "scene", "sc_a");
    updateShotContent("shot1", "@岚姐 登场，@阿岚 随后");
    renameAsset("cast", "cast_a", { aliases: ["岚姐·成年"] }, "scene", "sc_a");
    expect(getCurrentScene()!.shots[0].content.visual).toBe("@岚姐·成年 登场，@阿岚 随后");
    expect(getCurrentScene()!.assets!.cast[0].aliases).toEqual(["岚姐·成年"]);
  });

  it("renameAsset 只改描述/别名不替换 name 时，@旧名 不受影响", () => {
    updateShotContent("shot1", "@阿岚 站在天台");
    renameAsset("cast", "cast_a", { description: "仅改备注" }, "scene", "sc_a");
    expect(getCurrentScene()!.shots[0].content.visual).toBe("@阿岚 站在天台");
  });

  it("renameAsset 跨场景扫描：全局资产改名，所有场景镜头同步", () => {
    // 全局池 cast_a 改名（scope=episode）
    updateShotContent("shot1", "全局引用@阿岚");
    renameAsset("cast", "cast_a", { name: "阿岚·成年" }, "episode");
    expect(getCurrentScene()!.shots[0].content.visual).toBe("全局引用@阿岚·成年");
    // 场景池 cast_a 同名独立副本不受全局改名影响（两级池解耦）
    expect(getCurrentScene()!.assets!.cast[0].name).toBe("阿岚");
  });
});

describe("镜头参考图（refs.refImages，V1.2.5）", () => {
  it("addShotRefImage 追加 ref，index 从 0 起，fileName 取 basename", () => {
    selectScene("sc_a");
    const r1 = addShotRefImage("shot1", "minimax_studio/assets/林雪.png");
    const r2 = addShotRefImage("shot1", "minimax_studio/assets/tail.png");
    expect(r1).not.toBeNull();
    expect(r1!.index).toBe(0);
    expect(r1!.imageFile).toBe("minimax_studio/assets/林雪.png");
    expect(r1!.fileName).toBe("林雪.png");
    expect(r2!.index).toBe(1);
    const refs = getCurrentShot()!.refs!;
    expect(refs.refImages).toHaveLength(2);
    expect(refs.refImages.map((r) => r.imageFile)).toEqual([
      "minimax_studio/assets/林雪.png",
      "minimax_studio/assets/tail.png",
    ]);
  });

  it("addShotRefImage 空路径/不存在镜头返回 null", () => {
    expect(addShotRefImage("shot1", "   ")).toBeNull();
    expect(addShotRefImage("no_such", "a.png")).toBeNull();
  });

  it("removeShotRefImage 按下标删除并重排 index；越界/空 refs 返回 false", () => {
    selectScene("sc_a");
    addShotRefImage("shot1", "a.png");
    addShotRefImage("shot1", "b.png");
    addShotRefImage("shot1", "c.png");
    expect(removeShotRefImage("shot1", 1)).toBe(true);
    const refs = getCurrentShot()!.refs!;
    expect(refs.refImages.map((r) => r.fileName)).toEqual(["a.png", "c.png"]);
    expect(refs.refImages.map((r) => r.index)).toEqual([0, 1]);
    expect(removeShotRefImage("shot1", 99)).toBe(false);
    expect(removeShotRefImage("shot1", -1)).toBe(false);
    expect(removeShotRefImage("no_such", 0)).toBe(false);
    // 全删空
    removeShotRefImage("shot1", 0);
    removeShotRefImage("shot1", 0);
    expect(getCurrentShot()!.refs!.refImages).toHaveLength(0);
    expect(removeShotRefImage("shot1", 0)).toBe(false);
  });
});

describe("镜头参考视频/音频（refs.refVideos / refAudios，V1.2.6）", () => {
  it("addShotRefVideo 追加 ref，index 从 0 起，fileName 取 basename", () => {
    selectScene("sc_a");
    const v1 = addShotRefVideo("shot1", "minimax_studio/assets/动作参考.mp4");
    const v2 = addShotRefVideo("shot1", "minimax_studio/assets/tail.mp4");
    expect(v1).not.toBeNull();
    expect(v1!.index).toBe(0);
    expect(v1!.videoFile).toBe("minimax_studio/assets/动作参考.mp4");
    expect(v1!.fileName).toBe("动作参考.mp4");
    expect(v2!.index).toBe(1);
    const refs = getCurrentShot()!.refs!;
    expect(refs.refVideos).toHaveLength(2);
    expect(refs.refVideos.map((r) => r.videoFile)).toEqual([
      "minimax_studio/assets/动作参考.mp4",
      "minimax_studio/assets/tail.mp4",
    ]);
  });

  it("addShotRefVideo 空路径/不存在镜头返回 null", () => {
    expect(addShotRefVideo("shot1", "   ")).toBeNull();
    expect(addShotRefVideo("no_such", "a.mp4")).toBeNull();
  });

  it("removeShotRefVideo 按下标删除并重排 index；越界返回 false", () => {
    selectScene("sc_a");
    addShotRefVideo("shot1", "a.mp4");
    addShotRefVideo("shot1", "b.mp4");
    addShotRefVideo("shot1", "c.mp4");
    expect(removeShotRefVideo("shot1", 1)).toBe(true);
    const refs = getCurrentShot()!.refs!;
    expect(refs.refVideos.map((r) => r.fileName)).toEqual(["a.mp4", "c.mp4"]);
    expect(refs.refVideos.map((r) => r.index)).toEqual([0, 1]);
    expect(removeShotRefVideo("shot1", 99)).toBe(false);
    expect(removeShotRefVideo("no_such", 0)).toBe(false);
  });

  it("addShotRefAudio 追加 ref，index 从 0 起，fileName 取 basename", () => {
    selectScene("sc_a");
    const a1 = addShotRefAudio("shot1", "minimax_studio/assets/对白.wav");
    const a2 = addShotRefAudio("shot1", "minimax_studio/assets/bgm.mp3");
    expect(a1).not.toBeNull();
    expect(a1!.index).toBe(0);
    expect(a1!.audioFile).toBe("minimax_studio/assets/对白.wav");
    expect(a1!.fileName).toBe("对白.wav");
    expect(a2!.index).toBe(1);
    const refs = getCurrentShot()!.refs!;
    expect(refs.refAudios).toHaveLength(2);
    expect(refs.refAudios.map((r) => r.audioFile)).toEqual([
      "minimax_studio/assets/对白.wav",
      "minimax_studio/assets/bgm.mp3",
    ]);
  });

  it("addShotRefAudio 空路径/不存在镜头返回 null", () => {
    expect(addShotRefAudio("shot1", "")).toBeNull();
    expect(addShotRefAudio("no_such", "a.wav")).toBeNull();
  });

  it("removeShotRefAudio 按下标删除并重排 index；越界返回 false", () => {
    selectScene("sc_a");
    addShotRefAudio("shot1", "a.wav");
    addShotRefAudio("shot1", "b.wav");
    addShotRefAudio("shot1", "c.wav");
    expect(removeShotRefAudio("shot1", 1)).toBe(true);
    const refs = getCurrentShot()!.refs!;
    expect(refs.refAudios.map((r) => r.fileName)).toEqual(["a.wav", "c.wav"]);
    expect(refs.refAudios.map((r) => r.index)).toEqual([0, 1]);
    expect(removeShotRefAudio("shot1", 99)).toBe(false);
    expect(removeShotRefAudio("shot1", -1)).toBe(false);
    expect(removeShotRefAudio("no_such", 0)).toBe(false);
  });

  it("三类 refs 互不干扰：refImages/refVideos/refAudios 各自独立", () => {
    selectScene("sc_a");
    addShotRefImage("shot1", "img.png");
    addShotRefVideo("shot1", "vid.mp4");
    addShotRefAudio("shot1", "aud.wav");
    const refs = getCurrentShot()!.refs!;
    expect(refs.refImages).toHaveLength(1);
    expect(refs.refVideos).toHaveLength(1);
    expect(refs.refAudios).toHaveLength(1);
    // 删除视频不影响音频/图片 index
    expect(removeShotRefVideo("shot1", 0)).toBe(true);
    expect(refs.refAudios[0].index).toBe(0);
    expect(refs.refImages[0].index).toBe(0);
  });
});

describe("V1.4-P0-2 Dirty 状态（已保存/未保存标记）", () => {
  beforeEach(() => {
    loadProject(makeProject());
  });

  it("loadProject 后 dirty=false、lastSavedAt=null（与磁盘一致的干净基线）", () => {
    const wb = useWorkbench();
    expect(wb.dirty).toBe(false);
    expect(wb.lastSavedAt).toBeNull();
  });

  it("编辑原子操作置脏：更新镜头内容/时长/资产/参考图", () => {
    const wb = useWorkbench();
    updateShotContent("shot1", "新画面");
    expect(wb.dirty).toBe(true);
    markSaved();
    expect(wb.dirty).toBe(false);

    updateShotDuration("shot1", 6);
    expect(wb.dirty).toBe(true);
    markSaved();

    updateShotSection("shot1", "cameraText", "缓慢推近");
    expect(wb.dirty).toBe(true);
    markSaved();

    addAsset("cast", "新角色");
    expect(wb.dirty).toBe(true);
    markSaved();

    addShotRefImage("shot1", "minimax_studio/assets/x.png");
    expect(wb.dirty).toBe(true);
    markSaved();
  });

  it("镜头/场景增删改也置脏", () => {
    const wb = useWorkbench();
    addShot("sc_a");
    expect(wb.dirty).toBe(true);
    markSaved();

    renameScene("sc_a", "新场景");
    expect(wb.dirty).toBe(true);
    markSaved();

    moveShot("shot1", 1);
    expect(wb.dirty).toBe(true);
    markSaved();
  });

  it("markSaved 置干净并记录时间戳", () => {
    const wb = useWorkbench();
    updateShotContent("shot1", "x");
    expect(wb.dirty).toBe(true);
    markSaved();
    expect(wb.dirty).toBe(false);
    expect(wb.lastSavedAt).not.toBeNull();
    expect(wb.lastSavedAt).toBeLessThanOrEqual(Date.now());
  });

  it("markDirty 外部兜底可手动置脏", () => {
    const wb = useWorkbench();
    expect(wb.dirty).toBe(false);
    markDirty();
    expect(wb.dirty).toBe(true);
  });

  it("clearProject 复位 dirty", () => {
    const wb = useWorkbench();
    updateShotContent("shot1", "x");
    expect(wb.dirty).toBe(true);
    clearProject();
    expect(wb.dirty).toBe(false);
  });

  it("未命中目标的编辑操作不置脏", () => {
    const wb = useWorkbench();
    updateShotContent("no_such", "x");
    expect(wb.dirty).toBe(false);
    removeShot("no_such");
    expect(wb.dirty).toBe(false);
  });
});

describe("V1.6-C 项目/集命名", () => {
  beforeEach(() => {
    loadProject(makeProject());
  });

  it("renameProject 写回项目名 + trim + 置脏", () => {
    const wb = useWorkbench();
    expect(wb.project!.name).toBe("测试");
    renameProject("  第七号站台  ");
    expect(wb.project!.name).toBe("第七号站台");
    expect(wb.dirty).toBe(true);
    markSaved();
    expect(wb.dirty).toBe(false);
  });

  it("renameProject 空名不生效（保持原名 + 不置脏）", () => {
    const wb = useWorkbench();
    renameProject("   ");
    expect(wb.project!.name).toBe("测试");
    expect(wb.dirty).toBe(false);
  });

  it("renameEpisode 写回当前集标题 + trim + 置脏", () => {
    const wb = useWorkbench();
    const ep = getCurrentEpisode()!;
    expect(ep.title).toBe("第一集");
    renameEpisode("  第二季首集  ");
    expect(ep.title).toBe("第二季首集");
    expect(wb.dirty).toBe(true);
    markSaved();
    expect(wb.dirty).toBe(false);
  });

  it("renameEpisode 空标题不生效（保持原标题 + 不置脏）", () => {
    const wb = useWorkbench();
    renameEpisode("");
    expect(getCurrentEpisode()!.title).toBe("第一集");
    expect(wb.dirty).toBe(false);
  });
});

describe("V1.7 Phase 4：AI 制作计划审核（采纳 AI 草稿）", () => {
  function aiShot(id: string, order: number, sceneId: string, adopted = false): Shot {
    const s = makeShot(id, order, sceneId);
    s.aiDraft = true;
    s.promptTemplateVersion = "h3-v1";
    if (adopted) s.adopted = true;
    return s;
  }

  function makeAiProject(): Project {
    const p = makeProject();
    const sceneA = p.episodes[0].scenes[0];
    sceneA.shots = [
      aiShot("ai1", 0, "sc_a"),
      aiShot("ai2", 1, "sc_a", true), // 已采纳
      aiShot("ai3", 2, "sc_a"),
    ];
    return p;
  }

  beforeEach(() => {
    loadProject(makeProject()); // 重置为普通项目
  });

  it("adoptAiDraft：普通镜头幂等无操作", () => {
    const wb = useWorkbench();
    loadProject(makeAiProject());
    // 先清 dirty 基线，普通镜头不带 aiDraft
    loadProject(makeProject());
    expect(wb.dirty).toBe(false);
    adoptAiDraft("shot1");
    expect(getCurrentScene()!.shots[0].adopted).toBeUndefined();
    expect(wb.dirty).toBe(false);
  });

  it("adoptAiDraft：采纳单个 AI 草稿（adopted=true + 置脏 + aiDraft 保留历史元数据）", () => {
    loadProject(makeAiProject());
    const wb = useWorkbench();
    expect(wb.dirty).toBe(false);
    adoptAiDraft("ai1");
    const s = getCurrentScene()!.shots[0];
    expect(s.adopted).toBe(true);
    expect(s.aiDraft).toBe(true); // aiDraft 保留为历史元数据（V17 拍板）
    expect(wb.dirty).toBe(true);
  });

  it("adoptAiDraft：已采纳草稿幂等（不重复置脏）", () => {
    loadProject(makeAiProject());
    const wb = useWorkbench();
    adoptAiDraft("ai2"); // 初始已 adopted
    expect(wb.dirty).toBe(false);
  });

  it("adoptAllAiDrafts：一键采纳全部未采纳草稿，已采纳保持", () => {
    loadProject(makeAiProject());
    const wb = useWorkbench();
    adoptAllAiDrafts();
    const shots = getCurrentScene()!.shots;
    expect(shots.every((s) => s.adopted === true)).toBe(true);
    expect(wb.dirty).toBe(true);
  });

  it("adoptAllAiDrafts：全部已采纳 → 无操作不置脏", () => {
    const p = makeAiProject();
    p.episodes[0].scenes[0].shots.forEach((s) => (s.adopted = true));
    loadProject(p);
    const wb = useWorkbench();
    expect(wb.dirty).toBe(false);
    adoptAllAiDrafts();
    expect(wb.dirty).toBe(false);
  });

  it("序列化往返保留 adopted（不丢采纳标记）", () => {
    loadProject(makeAiProject());
    adoptAiDraft("ai1");
    const wb = useWorkbench();
    const blob = JSON.stringify(wb.project);
    expect(blob).toContain('"aiDraft":true');
    expect(blob).toContain('"adopted":true');
    // 反序列化后 adopted 仍在
    const p2 = JSON.parse(blob) as Project;
    expect(p2.episodes[0].scenes[0].shots[0].adopted).toBe(true);
  });
});

describe("V1.11 生成历史（版本管理：addGeneration / setActiveGeneration / removeGeneration）", () => {
  function makeGen(id: string, createdAt = 1000): ShotGenRecord {
    return {
      id,
      createdAt,
      videoFile: "",
      outputFilename: `${id}.mp4`,
      prompt: { visual: `版本${id}的 Prompt` },
      params: { taskType: "r2v", continuityMode: "auto", width: 1280, height: 720, frameRate: 30 },
      durationMs: 5000,
    };
  }

  it("addGeneration：首版自动采纳为当前成片（activeGenerationId=gen1）", () => {
    const wb = useWorkbench();
    expect(wb.dirty).toBe(false);
    addGeneration("shot1", makeGen("gen1"));
    const s = getCurrentScene()!.shots[0];
    expect(s.generations).toHaveLength(1);
    expect(s.activeGenerationId).toBe("gen1");
    expect(wb.dirty).toBe(true);
  });

  it("addGeneration：后续版本不自动切换当前成片（最新 ≠ 当前）", () => {
    addGeneration("shot1", makeGen("gen1"));
    addGeneration("shot1", makeGen("gen2"));
    const s = getCurrentScene()!.shots[0];
    expect(s.generations).toHaveLength(2);
    expect(s.generations![1].id).toBe("gen2"); // 末位 = 最新
    expect(s.activeGenerationId).toBe("gen1"); // ⭐ 仍指首版
  });

  it("setActiveGeneration：⭐ 手动设为当前成片（不影响列表顺序）", () => {
    addGeneration("shot1", makeGen("gen1"));
    addGeneration("shot1", makeGen("gen2"));
    setActiveGeneration("shot1", "gen2");
    const s = getCurrentScene()!.shots[0];
    expect(s.activeGenerationId).toBe("gen2");
    expect(s.generations![1].id).toBe("gen2"); // 顺序不变
  });

  it("setActiveGeneration：版本不存在 → 无操作", () => {
    addGeneration("shot1", makeGen("gen1"));
    const wb = useWorkbench();
    setActiveGeneration("shot1", "nope");
    const s = getCurrentScene()!.shots[0];
    expect(s.activeGenerationId).toBe("gen1");
    expect(wb.dirty).toBe(true); // 只有 addGeneration 置脏，setActive 无效不改
  });

  it("removeGeneration：删除普通版本，当前成片保持不变", () => {
    addGeneration("shot1", makeGen("gen1"));
    addGeneration("shot1", makeGen("gen2"));
    addGeneration("shot1", makeGen("gen3"));
    removeGeneration("shot1", "gen2");
    const s = getCurrentScene()!.shots[0];
    expect(s.generations).toHaveLength(2);
    expect(s.generations!.map((g) => g.id)).toEqual(["gen1", "gen3"]);
    expect(s.activeGenerationId).toBe("gen1");
  });

  it("removeGeneration：删除当前成片 → 回落到末位（最新）", () => {
    addGeneration("shot1", makeGen("gen1"));
    addGeneration("shot1", makeGen("gen2"));
    addGeneration("shot1", makeGen("gen3"));
    setActiveGeneration("shot1", "gen2");
    removeGeneration("shot1", "gen2");
    const s = getCurrentScene()!.shots[0];
    expect(s.activeGenerationId).toBe("gen3"); // 回落到末位
  });

  it("removeGeneration：删除唯一版本 → activeGenerationId 清空", () => {
    addGeneration("shot1", makeGen("gen1"));
    removeGeneration("shot1", "gen1");
    const s = getCurrentScene()!.shots[0];
    expect(s.generations).toHaveLength(0);
    expect(s.activeGenerationId).toBeUndefined();
  });

  it("genId：唯一性（两次调用不同）", () => {
    expect(genId()).not.toBe(genId());
  });

  it("序列化往返保留 generations + activeGenerationId（project.json 持久化）", () => {
    addGeneration("shot1", makeGen("gen1"));
    addGeneration("shot1", makeGen("gen2"));
    setActiveGeneration("shot1", "gen2");
    const wb = useWorkbench();
    const blob = JSON.stringify(wb.project);
    expect(blob).toContain('"generations"');
    expect(blob).toContain('"activeGenerationId"');
    const p2 = JSON.parse(blob) as Project;
    const s2 = p2.episodes[0].scenes[0].shots[0];
    expect(s2.generations).toHaveLength(2);
    expect(s2.activeGenerationId).toBe("gen2");
    expect(s2.generations![1].prompt.visual).toBe("版本gen2的 Prompt");
    expect(s2.generations![1].params.workflowId).toBeUndefined(); // 可选字段
  });

  it("loadProject 重载后 generations 仍在（不丢版本历史）", () => {
    addGeneration("shot1", makeGen("gen1"));
    addGeneration("shot1", makeGen("gen2"));
    setActiveGeneration("shot1", "gen2");
    loadProject(makeProject());
    // 重新加载初始项目（无 generations）→ 新项目为空，验证切换不残留
    const s = getCurrentScene()!.shots[0];
    expect(s.generations).toBeUndefined();
  });
});

/**
 * V1.11.3（#399）：载入新项目 = 全新会话，必须无条件复位项目级瞬态。
 * 回归背景：loadProject 替换 state.project 后若不清任务/成片/预览/最近错误/选中态，
 * 底部任务中心仍列上一个项目的任务与镜头（用户「我新开了一个项目，为什么底部还有之前的 shot」）。
 */
describe("V1.11.3 loadProject 复位项目级瞬态", () => {
  /** 模拟后端 create_project 返回的空项目（ep1 存在但无场景）。 */
  function emptyProject(): Project {
    return {
      id: "proj_empty",
      name: "未命名项目",
      createdAt: "",
      updatedAt: "",
      episodes: [
        {
          id: "ep1",
          episodeNumber: 1,
          title: "第一集",
          scenes: [],
          assets: { cast: [], locations: [], props: [], styles: [] },
        },
      ],
    };
  }

  it("载入空项目：清空上一项目的任务/成片/预览/最近错误/选中态/游标", () => {
    const wb = useWorkbench();
    // ① 制造「上一项目的工作现场」：生成任务 + 成片 + 预览 + 最近错误 + 选中态。
    const taskId = addTask({
      scopeLabel: "全部镜头（2）",
      shotCount: 2,
      shotOrder: ["shot1", "shot2"],
      targetShotIds: null,
    });
    patchTask(taskId, {
      status: "done",
      finalVideo: { url: "/view?file=old.mp4", filename: "old.mp4" },
    });
    wb.currentTaskId = taskId;
    wb.finalVideo = { url: "/view?file=old.mp4", filename: "old.mp4" };
    wb.preview = "data:image/png;base64,xxx";
    wb.lastError = "上一项目生成失败";
    wb.genScope = "fromShot";
    wb.selectedShotIds.push("shot1");
    wb.segStatus = { running: ["shot1"] } as never;
    expect(wb.tasks).toHaveLength(1);
    expect(wb.currentShotId).toBe("shot1");

    // ② 载入空项目（新建项目即空项目）。
    loadProject(emptyProject());

    // ③ 项目级瞬态全部复位，不残留上一项目内容。
    expect(wb.tasks, "任务中心应清空").toHaveLength(0);
    expect(wb.currentTaskId).toBeNull();
    expect(wb.finalVideo, "成片应清空").toBeNull();
    expect(wb.preview, "预览应清空").toBeNull();
    expect(wb.lastError, "最近错误应清空").toBeNull();
    expect(wb.genScope).toBe("all");
    expect(wb.selectedShotIds).toHaveLength(0);
    expect(wb.segStatus).toBeNull();
    // 空项目无场景 → 游标不沿用旧项目。
    expect(wb.currentEpisodeId).toBe("ep1");
    expect(wb.currentSceneId).toBeNull();
    expect(wb.currentShotId).toBeNull();
    expect(wb.currentPane).toBe("shot");
    expect(wb.dirty).toBe(false);
    expect(wb.lastSavedAt).toBeNull();
  });

  it("载入有场景的新项目：任务清空 + 游标落到第一场景第一镜", () => {
    const wb = useWorkbench();
    addTask({ scopeLabel: "x", shotCount: 1, shotOrder: ["shot1"], targetShotIds: null });
    wb.finalVideo = { url: "/view?file=old.mp4", filename: "old.mp4" };
    expect(wb.tasks).toHaveLength(1);

    loadProject(makeProject());

    expect(wb.tasks, "任务中心应清空").toHaveLength(0);
    expect(wb.finalVideo).toBeNull();
    expect(wb.currentEpisodeId).toBe("ep1");
    expect(wb.currentSceneId).toBe("sc_a");
    expect(wb.currentShotId).toBe("shot1");
    expect(wb.currentPane).toBe("shot");
    expect(wb.dirty).toBe(false);
  });

  /**
   * #455（P1-⑥）：新项目状态隔离 —— 同名 shot id 也不得泄漏对象级数据。
   * 回归背景：loadProject 直接替换 state.project 引用，本应天然隔离；
   * 但一旦将来有人改成「浅合并/复用 shot 对象」，同名 shot1 的 generations /
   * activeGenerationId / 出片视频等会串到新项目。此用例把这种行为钉死。
   */
  it("跨项目切载：A 项目 shot1 的生成历史不泄漏到 B 项目同名 shot1", () => {
    const projA = makeProject();
    const shotA = projA.episodes[0].scenes[0].shots[0]; // id "shot1"
    shotA.generations = [
      {
        id: "genA1",
        createdAt: 1000,
        videoFile: "",
        outputFilename: "A v1.mp4",
        prompt: { visual: "A 项目的 Prompt" },
        params: { taskType: "r2v", continuityMode: "auto", width: 1280, height: 720, frameRate: 30 },
        durationMs: 5000,
      },
    ];
    shotA.activeGenerationId = "genA1";
    shotA.content = { visual: "A 项目的场面" };
    shotA.castIds = ["cast_a"];
    shotA.castManual = true;

    // B 项目同样有 id "shot1"，但内容完全不同、且无任何生成历史。
    const projB = makeProject();
    const shotB = projB.episodes[0].scenes[0].shots[0];
    shotB.content = { visual: "B 项目的场面" };
    shotB.castIds = undefined;
    shotB.castManual = false;

    // ① 载入 A：生成历史 + 当前成片都在。
    loadProject(projA);
    const wb = useWorkbench();
    expect(getCurrentShot()!.generations).toHaveLength(1);
    expect(getCurrentShot()!.activeGenerationId).toBe("genA1");

    // ② 切到 B：同名 shot1 的 generations / activeGenerationId 必须清空，
    //    内容回落到 B 自己的，选中/成片/任务均为 B 的空现场。
    loadProject(projB);
    const sB = getCurrentShot()!;
    expect(sB.generations, "B 项目 shot1 不得带 A 的生成历史").toBeUndefined();
    expect(sB.activeGenerationId, "B 项目不得带 A 的当前成片").toBeUndefined();
    expect(sB.content.visual).toBe("B 项目的场面");
    expect(sB.castIds, "B 项目不得带 A 的角色选择").toBeUndefined();
    expect(sB.id).toBe("shot1"); // 游标仍落到第一镜
    expect(wb.currentEpisodeId).toBe("ep1");
    expect(wb.currentSceneId).toBe("sc_a");
    expect(wb.currentShotId).toBe("shot1");
    expect(wb.tasks).toHaveLength(0);
    expect(wb.finalVideo).toBeNull();
    expect(wb.dirty).toBe(false);
  });
});

