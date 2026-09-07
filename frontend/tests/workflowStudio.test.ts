// @vitest-environment happy-dom
/**
 * V1.9 Workflow Studio：节点解析 / widget 提取 / LoRA 插入 / seed 随机 纯函数单测。
 */
import { describe, it, expect } from "vitest";
import {
  DIRECTOR_MANAGED_FIELDS,
  DIRECTOR_PROJECT_FIELDS,
  findDirectorNode,
  findLoraNode,
  insertLoraNode,
  nodeRole,
  readWorkflowSeed,
  removeLoraNode,
  resolveSeed,
  rollRandomSeed,
  scanDirectorNodeIds,
  scanWorkflowGraph,
  setNodeWidget,
  widgetsOf,
} from "@/core/workflowStudio";

/** 最小 H3 DAG：3 个 loader → Director → CreateVideo → SaveVideo。 */
function miniWorkflow(): Record<string, unknown> {
  return {
    "1": {
      class_type: "UNETLoader",
      inputs: { unet_name: "minimax_h3.safetensors", weight_dtype: "default" },
    },
    "2": { class_type: "CLIPLoader", inputs: { clip_name: "qwen3vl.safetensors", type: "minimax" } },
    "3": { class_type: "VAELoader", inputs: { vae_name: "minimax_h3_vae.safetensors" } },
    "5": {
      class_type: "MiniMaxH3Director",
      inputs: {
        model: ["1", 0],
        clip: ["2", 0],
        video_vae: ["3", 0],
        bd_grp_sample: "采样设置",
        steps: 25,
        seed: 42,
        cfg: 1.0,
        width: 864,
        height: 480,
        timeline_data: "",
      },
    },
    "6": { class_type: "CreateVideo", inputs: { images: ["5", 0], fps: 24.0 } },
    "7": { class_type: "SaveVideo", inputs: { video: ["6", 0], filename_prefix: "video/h3" } },
  };
}

describe("V1.9 workflowStudio：节点角色", () => {
  it("Director / 普通 / 系统三类判定", () => {
    expect(nodeRole("MiniMaxH3Director")).toBe("director");
    expect(nodeRole("MiniMaxH3Director v2")).toBe("director");
    expect(nodeRole("UNETLoader")).toBe("normal");
    expect(nodeRole("LoraLoaderModelOnly")).toBe("normal");
    expect(nodeRole("CreateVideo")).toBe("system");
    expect(nodeRole("SaveVideo")).toBe("system");
    expect(nodeRole("PreviewImage")).toBe("system");
    expect(nodeRole("")).toBe("normal");
  });

  it("scanDirectorNodeIds 只认 Director 节点", () => {
    expect(scanDirectorNodeIds(miniWorkflow())).toEqual(["5"]);
    expect(scanDirectorNodeIds({ "6": { class_type: "CreateVideo", inputs: {} } })).toEqual([]);
    expect(scanDirectorNodeIds(null)).toEqual([]);
  });
});

describe("V1.9 workflowStudio：图扫描", () => {
  it("扫描出全部节点 + 连接边 + 唯一 Director id", () => {
    const g = scanWorkflowGraph(miniWorkflow());
    expect(g.nodes.length).toBe(6);
    expect(g.directorNodeId).toBe("5");
    // 连接边：model/clip/video_vae（→5）、images（5→6）、video（6→7）。
    expect(g.edges.length).toBe(5);
    expect(g.edges.some((e) => e.inputKey === "model" && e.from === "1" && e.to === "5")).toBe(true);
    expect(g.edges.some((e) => e.inputKey === "video" && e.from === "6" && e.to === "7")).toBe(true);
  });

  it("坏输入返回空图", () => {
    expect(scanWorkflowGraph(null).nodes.length).toBe(0);
    expect(scanWorkflowGraph([]).nodes.length).toBe(0);
    expect(scanWorkflowGraph({ foo: "bar" }).nodes.length).toBe(0);
  });
});

describe("V1.9 workflowStudio：widget 提取", () => {
  it("Director：连接引用被跳过、BDGROUP 被跳过、managed 字段隐藏、project 字段只读", () => {
    const fields = widgetsOf(miniWorkflow()["5"]);
    const keys = fields.map((f) => f.key);
    // 连接（model/clip/video_vae）与 BDGROUP 不进列表。
    expect(keys).not.toContain("model");
    expect(keys).not.toContain("bd_grp_sample");
    // managed 字段（timeline_data）隐藏。
    expect(keys).not.toContain("timeline_data");
    for (const f of DIRECTOR_MANAGED_FIELDS) expect(keys).not.toContain(f);
    // steps/seed/cfg → free；width/height → project。
    expect(fields.find((f) => f.key === "steps")).toMatchObject({ kind: "int", group: "free" });
    expect(fields.find((f) => f.key === "seed")).toMatchObject({ kind: "int", group: "free" });
    expect(fields.find((f) => f.key === "width")).toMatchObject({ group: "project" });
    expect(fields.find((f) => f.key === "height")).toMatchObject({ group: "project" });
    for (const f of DIRECTOR_PROJECT_FIELDS) {
      // 只断言节点里真实存在的 project 字段（width/height）。
      const hit = fields.find((x) => x.key === f);
      if (!hit) continue;
      expect(hit.group).toBe("project");
    }
  });

  it("普通节点：标量全量可编辑，连接引用跳过", () => {
    const fields = widgetsOf(miniWorkflow()["1"]);
    const keys = fields.map((f) => f.key);
    expect(keys).toContain("unet_name");
    expect(keys).toContain("weight_dtype");
    expect(keys).not.toContain("model");
    expect(fields.every((f) => f.group === "free")).toBe(true);
  });

  it("类型推断：int / float / str / bool / combo", () => {
    const node = {
      class_type: "MiniMaxH3Director",
      inputs: {
        steps: 25,
        cfg: 1.0,
        clear_vram_between_segments: true,
        sampler: "res_multistep",
        task_type: "r2v — 参考主体生视频(Reference to Video)",
        global_prompt: "hello",
      },
    };
    const fields = widgetsOf(node);
    expect(fields.find((f) => f.key === "steps")?.kind).toBe("int");
    expect(fields.find((f) => f.key === "cfg")?.kind).toBe("float");
    expect(fields.find((f) => f.key === "clear_vram_between_segments")?.kind).toBe("bool");
    expect(fields.find((f) => f.key === "sampler")?.kind).toBe("combo");
    expect(fields.find((f) => f.key === "task_type")?.kind).toBe("combo");
    expect(fields.find((f) => f.key === "global_prompt")?.kind).toBe("str");
  });

  it("widgetsOf 对坏输入返回空", () => {
    expect(widgetsOf(null)).toEqual([]);
    expect(widgetsOf({ class_type: "X" })).toEqual([]); // 无 inputs
  });
});

describe("V1.9 workflowStudio：写操作", () => {
  it("setNodeWidget 深拷贝写回，不污染原对象", () => {
    const wf = miniWorkflow();
    const before = JSON.stringify(wf);
    const next = setNodeWidget(wf, "5", "steps", 12);
    expect((next["5"] as { inputs: Record<string, unknown> }).inputs.steps).toBe(12);
    expect((wf["5"] as { inputs: Record<string, unknown> }).inputs.steps).toBe(25);
    expect(JSON.stringify(wf)).toBe(before);
  });

  it("setNodeWidget 对不存在节点安全返回", () => {
    const wf = miniWorkflow();
    const next = setNodeWidget(wf, "999", "x", 1);
    expect(JSON.stringify(next)).toBe(JSON.stringify(wf));
  });
});

describe("V1.9 workflowStudio：LoRA", () => {
  it("无 LoRA 节点时 findLoraNode 返回 null", () => {
    expect(findLoraNode(miniWorkflow())).toBeNull();
  });

  it("识别已有 LoraLoaderModelOnly", () => {
    const wf = miniWorkflow();
    (wf["8"] as Record<string, unknown>) = {
      class_type: "LoraLoaderModelOnly",
      inputs: { lora_name: "h3_accel.safetensors", strength_model: 0.7, model: ["1", 0] },
    };
    const lora = findLoraNode(wf);
    expect(lora?.loraName).toBe("h3_accel.safetensors");
    expect(lora?.strengthModel).toBe(0.7);
  });

  it("insertLoraNode：插入节点并把 Director.model 接到新节点", () => {
    const wf = miniWorkflow();
    const res = insertLoraNode(wf, "h3_accel.safetensors", 0.7);
    expect("workflow" in res).toBe(true);
    if ("error" in res) throw new Error(`unexpected error: ${res.message}`);
    const director = findDirectorNode(res.workflow)!;
    expect(director.node.inputs.model).toEqual([res.loraNodeId, 0]);
    const lora = res.workflow[res.loraNodeId] as { class_type: string; inputs: Record<string, unknown> };
    expect(lora.class_type).toBe("LoraLoaderModelOnly");
    expect(lora.inputs.lora_name).toBe("h3_accel.safetensors");
    expect(lora.inputs.strength_model).toBe(0.7);
    expect(lora.inputs.model).toEqual(["1", 0]);
    // 原对象不被污染。
    expect((wf["5"] as { inputs: Record<string, unknown> }).inputs.model).toEqual(["1", 0]);
  });

  it("insertLoraNode：已有 LoRA 时报错", () => {
    const wf = miniWorkflow();
    (wf["8"] as Record<string, unknown>) = {
      class_type: "LoraLoaderModelOnly",
      inputs: { lora_name: "a.safetensors", strength_model: 1, model: ["1", 0] },
    };
    const res = insertLoraNode(wf, "b.safetensors", 1);
    expect("error" in res && res.error).toBe("already-lora");
  });

  it("insertLoraNode：Director model 未连接时报错", () => {
    const wf = miniWorkflow();
    delete (wf["5"] as { inputs: Record<string, unknown> }).inputs.model;
    const res = insertLoraNode(wf, "a.safetensors", 1);
    expect("error" in res && res.error).toBe("model-not-connected");
  });

  it("insertLoraNode：无 Director 时报错", () => {
    const res = insertLoraNode({ "6": { class_type: "CreateVideo", inputs: {} } }, "a.safetensors", 1);
    expect("error" in res && res.error).toBe("no-director");
  });

  it("removeLoraNode：移除节点并把 Director.model 恢复到上游", () => {
    const wf = miniWorkflow();
    const inserted = insertLoraNode(wf, "h3_accel.safetensors", 0.7);
    if ("error" in inserted) throw new Error("insert failed");
    const removed = removeLoraNode(inserted.workflow);
    if ("error" in removed) throw new Error(`remove failed: ${removed.message}`);
    const director = findDirectorNode(removed.workflow)!;
    expect(director.node.inputs.model).toEqual(["1", 0]);
    expect(removed.workflow[inserted.loraNodeId]).toBeUndefined();
    // 原对象不污染。
    expect(findLoraNode(inserted.workflow)).not.toBeNull();
  });

  it("removeLoraNode：无 LoRA 时报错", () => {
    const res = removeLoraNode(miniWorkflow());
    expect("error" in res && res.error).toBe("no-lora");
  });
});

describe("V1.9 workflowStudio：seed 随机", () => {
  it("-1 → 每次随机（在合法范围且非 -1）", () => {
    const a = resolveSeed(-1);
    const b = resolveSeed(-1);
    expect(a).toBeGreaterThanOrEqual(0);
    expect(a).toBeLessThan(2 ** 31);
    expect(b).toBeGreaterThanOrEqual(0);
    expect(b).toBeLessThan(2 ** 31);
    expect(a).not.toBe(-1);
  });

  it("固定值原样返回（整数化）", () => {
    expect(resolveSeed(42)).toBe(42);
    expect(resolveSeed(3.7)).toBe(3);
    expect(resolveSeed("42")).toBe(0); // 非 number → 兜底 0
  });
});

describe("V1.12 workflowStudio：种子便捷化（rollRandomSeed / readWorkflowSeed）", () => {
  it("rollRandomSeed 掷出 32 位合法种子（0 ≤ s < 2^31，两次不同）", () => {
    const a = rollRandomSeed();
    const b = rollRandomSeed();
    expect(a).toBeGreaterThanOrEqual(0);
    expect(a).toBeLessThan(2 ** 31);
    expect(Number.isInteger(a)).toBe(true);
    expect(b).toBeGreaterThanOrEqual(0);
    expect(b).toBeLessThan(2 ** 31);
    expect(a).not.toBe(b);
  });

  it("readWorkflowSeed 读 Director 节点 seed（固定值原样整数化）", () => {
    const wf = miniWorkflow(); // seed: 42
    expect(readWorkflowSeed(wf)).toBe(42);
  });

  it("readWorkflowSeed 读 -1（随机模式）", () => {
    const wf = miniWorkflow();
    (wf["5"] as { inputs: Record<string, unknown> }).inputs.seed = -1;
    expect(readWorkflowSeed(wf)).toBe(-1);
  });

  it("readWorkflowSeed 对 3.7 整数化", () => {
    const wf = miniWorkflow();
    (wf["5"] as { inputs: Record<string, unknown> }).inputs.seed = 3.7;
    expect(readWorkflowSeed(wf)).toBe(3);
  });

  it("readWorkflowSeed 无 Director 节点返回 null", () => {
    expect(readWorkflowSeed({ "6": { class_type: "CreateVideo", inputs: {} } })).toBeNull();
  });

  it("readWorkflowSeed 非法类型返回 null", () => {
    const wf = miniWorkflow();
    (wf["5"] as { inputs: Record<string, unknown> }).inputs.seed = "abc";
    expect(readWorkflowSeed(wf)).toBeNull();
  });
});
