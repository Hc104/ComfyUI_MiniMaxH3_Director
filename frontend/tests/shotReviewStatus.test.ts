/**
 * V1.7 Phase 4：AI 制作计划审核就绪检查（shotReviewStatus）单测。
 *
 * 用户 2026-08-11 拍板三项判定：①资产绑定（角色在生效资产池且有参考图、
 *   生效资产 imageFile 非空）②五区 Prompt（visual 非空）③运镜（cameraText 非空）。
 * 任何缺失 → ok=false + issues 逐条。⚠ 不参与指纹/状态灯/导出/续接。
 */
import { describe, it, expect } from "vitest";
import { shotReviewStatus } from "@/core/shotReviewStatus";
import type { Asset, Shot } from "@/models/project";

function shot(overrides: Partial<Shot> = {}): Shot {
  return {
    id: "s1",
    sceneId: "scene_01",
    order: 1,
    durationSec: 5,
    content: {
      visual: "青衫女侠推开木门，雨水顺着伞沿滴落。",
      cameraText: "中景跟移，随林雪移动，清晨、雨光线变化。",
      style: "电影感，冷色调。",
      soundText: "雨声淅沥。",
      negativePrompt: "画面模糊，肢体扭曲。",
    },
    ...overrides,
  };
}

function asset(id: string, imageFile = "", name = id): Asset {
  return { id, name, kind: "cast", imageFile };
}

describe("shotReviewStatus：三项判定", () => {
  it("全就绪 → ok=true，无 issues", () => {
    const s = shot();
    const st = shotReviewStatus(s, {
      castIds: ["林雪"],
      assets: [asset("林雪", "minimax_studio/assets/林雪.png", "林雪")],
    });
    expect(st.ok).toBe(true);
    expect(st.issues).toHaveLength(0);
  });

  it("角色未绑定资产 → ⚠ 资产项", () => {
    const s = shot();
    const st = shotReviewStatus(s, { castIds: ["林雪"], assets: [] });
    expect(st.ok).toBe(false);
    expect(st.issues).toContainEqual({ kind: "asset", text: "角色「林雪」未绑定资产" });
  });

  it("角色资产已注册但参考图缺失 → ⚠ 资产项", () => {
    const s = shot();
    const st = shotReviewStatus(s, {
      castIds: ["林雪"],
      assets: [asset("林雪", "", "林雪")],
    });
    expect(st.ok).toBe(false);
    expect(st.issues).toContainEqual({ kind: "asset", text: "角色「林雪」参考图缺失" });
  });

  it("生效资产里地点/道具缺参考图 → ⚠ 资产项（角色不重复报）", () => {
    const s = shot();
    const st = shotReviewStatus(s, {
      castIds: ["林雪"],
      assets: [
        asset("林雪", "minimax_studio/assets/林雪.png", "林雪"),
        asset("山雨客栈", "", "山雨客栈"), // 地点资产无图
      ],
    });
    expect(st.issues).toHaveLength(1);
    expect(st.issues[0]).toEqual({ kind: "asset", text: "资产「山雨客栈」未上传参考图" });
  });

  it("Prompt 画面描述为空 → ⚠ prompt 项", () => {
    const s = shot({ content: { ...shot().content, visual: "  " } });
    const st = shotReviewStatus(s, { castIds: [], assets: [] });
    expect(st.issues).toContainEqual({ kind: "prompt", text: "Prompt 画面描述为空" });
  });

  it("运镜未设置 → ⚠ camera 项", () => {
    const s = shot({ content: { ...shot().content, cameraText: undefined } });
    const st = shotReviewStatus(s, { castIds: [], assets: [] });
    expect(st.issues).toContainEqual({ kind: "camera", text: "运镜未设置" });
  });

  it("空镜（无角色无资产）只有 prompt/camera 判定，不报资产", () => {
    const s = shot({ content: { visual: "" } });
    const st = shotReviewStatus(s, { castIds: [], assets: [] });
    expect(st.ok).toBe(false);
    const kinds = st.issues.map((i) => i.kind);
    expect(kinds).not.toContain("asset");
    expect(kinds).toContain("prompt");
    expect(kinds).toContain("camera");
  });

  it("多问题组合逐条列出（角色缺 + prompt 空 + 运镜空）", () => {
    const s = shot({ content: { visual: "" } });
    const st = shotReviewStatus(s, { castIds: ["陈默"], assets: [] });
    expect(st.issues).toEqual([
      { kind: "asset", text: "角色「陈默」未绑定资产" },
      { kind: "prompt", text: "Prompt 画面描述为空" },
      { kind: "camera", text: "运镜未设置" },
    ]);
  });
});
