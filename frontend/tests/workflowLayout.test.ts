// @vitest-environment happy-dom
/**
 * V1.9 Workflow Studio：DAG 分层布局纯函数单测。
 */
import { describe, it, expect } from "vitest";
import { layoutWorkflow, NODE_H, NODE_W } from "@/core/workflowLayout";

/** 最小 H3 DAG（与 workflowStudio 测试共用结构）。 */
function miniWorkflow(): Record<string, unknown> {
  return {
    "1": { class_type: "UNETLoader", inputs: { unet_name: "a.safetensors" } },
    "2": { class_type: "CLIPLoader", inputs: { clip_name: "b.safetensors" } },
    "3": { class_type: "VAELoader", inputs: { vae_name: "c.safetensors" } },
    "5": {
      class_type: "MiniMaxH3Director",
      inputs: { model: ["1", 0], clip: ["2", 0], video_vae: ["3", 0], steps: 25, seed: 42 },
    },
    "6": { class_type: "CreateVideo", inputs: { images: ["5", 0], fps: 24.0 } },
    "7": { class_type: "SaveVideo", inputs: { video: ["6", 0] } },
  };
}

describe("V1.9 workflowLayout：分层", () => {
  it("3 loader → Director → CreateVideo → SaveVideo 共 4 层", () => {
    const layout = layoutWorkflow(miniWorkflow());
    expect(layout.layerCount).toBe(4);
    expect(layout.nodes.length).toBe(6);
    const layerOf = (id: string) => layout.nodes.find((n) => n.nodeId === id)?.layer;
    expect(layerOf("1")).toBe(0);
    expect(layerOf("5")).toBe(1);
    expect(layerOf("6")).toBe(2);
    expect(layerOf("7")).toBe(3);
  });

  it("同层节点横向排布（y 递增），层间纵向（x 递增）", () => {
    const layout = layoutWorkflow(miniWorkflow());
    const n1 = layout.nodes.find((n) => n.nodeId === "1")!;
    const n5 = layout.nodes.find((n) => n.nodeId === "5")!;
    const n7 = layout.nodes.find((n) => n.nodeId === "7")!;
    // 层 1 的 x > 层 0 的 x（从左到右）。
    expect(n5.x).toBeGreaterThan(n1.x);
    // 最后层 x 最大。
    expect(n7.x).toBeGreaterThan(n5.x);
    // 画布宽高足够容纳节点。
    expect(layout.width).toBeGreaterThan(n7.x + NODE_W);
    expect(layout.height).toBeGreaterThanOrEqual(NODE_H);
  });

  it("同层出现顺序稳定（对象 key 顺序）", () => {
    const wf = miniWorkflow();
    // 两个同层节点按 key 顺序排。
    const layout = layoutWorkflow(wf);
    const l0 = layout.nodes.filter((n) => n.layer === 0);
    expect(l0.length).toBe(3);
    expect(l0[0].nodeId).toBe("1");
    expect(l0[1].nodeId).toBe("2");
    expect(l0[2].nodeId).toBe("3");
    expect(l0[0].y).toBeLessThan(l0[1].y);
  });

  it("空工作流返回空布局", () => {
    const layout = layoutWorkflow(null);
    expect(layout.nodes.length).toBe(0);
    expect(layout.width).toBe(0);
    expect(layout.height).toBe(0);
    expect(layout.layerCount).toBe(0);
  });

  it("链式（每个节点只有一个上游）也能正确分层", () => {
    const wf: Record<string, unknown> = {
      "1": { class_type: "A", inputs: { p: "x" } },
      "2": { class_type: "B", inputs: { in: ["1", 0] } },
      "3": { class_type: "C", inputs: { in: ["2", 0] } },
    };
    const layout = layoutWorkflow(wf);
    expect(layout.layerCount).toBe(3);
    expect(layout.nodes.find((n) => n.nodeId === "1")?.layer).toBe(0);
    expect(layout.nodes.find((n) => n.nodeId === "2")?.layer).toBe(1);
    expect(layout.nodes.find((n) => n.nodeId === "3")?.layer).toBe(2);
  });
});
