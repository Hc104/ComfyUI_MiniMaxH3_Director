/**
 * 播放队列（playQueue.ts）单测：把「已生成镜头」组织成顺序播放列表。
 */
import { describe, it, expect } from "vitest";
import { collectPlayableShotIds, type PlayScope } from "@/workbench/playQueue";

const shots = [
  { id: "s1" },
  { id: "s2" },
  { id: "s3" },
  { id: "s4" },
];

describe("collectPlayableShotIds", () => {
  it("shot 范围：只有目标镜头，且须已缓存", () => {
    const cached = new Set([0, 2]);
    expect(collectPlayableShotIds(shots, cached, "shot", { targetShotId: "s1" })).toEqual(["s1"]);
    // 未缓存的目标 → 空
    expect(collectPlayableShotIds(shots, cached, "shot", { targetShotId: "s2" })).toEqual([]);
  });

  it("scene 范围：只收该场景内已缓存镜头，保持拍平顺序", () => {
    const cached = new Set([0, 1, 3]); // s1, s2, s4 已缓存
    const sceneShotIds = ["s1", "s3", "s4"]; // 场景内镜头（按场景顺序）
    const ids = collectPlayableShotIds(shots, cached, "scene", { sceneShotIds });
    expect(ids).toEqual(["s1", "s4"]); // s3 未缓存跳过
  });

  it("scene 范围：未指定场景镜头 → 空", () => {
    const cached = new Set([0, 1, 2, 3]);
    expect(collectPlayableShotIds(shots, cached, "scene")).toEqual([]);
  });

  it("all 范围：全部已缓存镜头按拍平顺序", () => {
    const cached = new Set([1, 3]);
    expect(collectPlayableShotIds(shots, cached, "all")).toEqual(["s2", "s4"]);
  });

  it("去重：同一镜头 id 只出现一次", () => {
    const dupShots = [{ id: "s1" }, { id: "s1" }, { id: "s2" }];
    const cached = new Set([0, 1, 2]);
    expect(collectPlayableShotIds(dupShots, cached, "all")).toEqual(["s1", "s2"]);
  });

  it("空输入 / 无缓存 → 空数组", () => {
    expect(collectPlayableShotIds([], new Set(), "all")).toEqual([]);
    expect(collectPlayableShotIds(shots, new Set(), "all")).toEqual([]);
  });

  it("无目标 id 的 shot 范围 → 空", () => {
    const cached = new Set([0]);
    expect(collectPlayableShotIds(shots, cached, "shot" as PlayScope)).toEqual([]);
  });
});
