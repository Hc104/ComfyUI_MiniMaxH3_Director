// @vitest-environment happy-dom
/**
 * V1.8-2 Workflow Registry 纯函数单测：
 *   节点扫描 / JSON 解析 / 条目构建 / 激活态解析。
 * 覆盖 0 节点、单节点、多节点（未选/已选）四类判定。
 */
import { describe, it, expect } from "vitest";
import {
  BUILTIN_WORKFLOW_ID,
  DEFAULT_DIRECTOR_NODE_ID,
  WORKFLOW_REGISTRY_KEY,
  buildBuiltinEntry,
  buildWorkflowEntry,
  loadWorkflowRegistry,
  parseWorkflowJson,
  resolveActiveWorkflow,
  scanDirectorNodeIds,
} from "@/core/workflowRegistry";

/** 构造一个带指定 Director 节点数量的 workflow（含一个普通 Loader 节点）。 */
function makeWorkflow(directorCount: number, extraClass?: string): Record<string, unknown> {
  const wf: Record<string, unknown> = {
    "1": { class_type: "UNETLoader", inputs: {} },
  };
  for (let i = 0; i < directorCount; i++) {
    const id = String(5 + i);
    const cls = extraClass && i === 0 ? extraClass : "MiniMaxH3Director";
    wf[id] = { class_type: cls, inputs: { frame_rate: 24, timeline_data: [] } };
  }
  return wf;
}

describe("V1.8-2 workflowRegistry：节点扫描", () => {
  it("scanDirectorNodeIds 只识别 class_type 含 MiniMaxH3Director 的节点", () => {
    const wf = makeWorkflow(2);
    expect(scanDirectorNodeIds(wf)).toEqual(["5", "6"]);
  });

  it("scanDirectorNodeIds 对 0 节点 / 空 / 非法入参返回空数组", () => {
    expect(scanDirectorNodeIds({ "1": { class_type: "UNETLoader" } })).toEqual([]);
    expect(scanDirectorNodeIds({})).toEqual([]);
    expect(scanDirectorNodeIds(null)).toEqual([]);
    expect(scanDirectorNodeIds("nope")).toEqual([]);
    expect(scanDirectorNodeIds([1, 2, 3])).toEqual([]);
  });

  it("scanDirectorNodeIds 忽略无 class_type 的节点（顶层元数据 key）", () => {
    const wf = makeWorkflow(1);
    (wf as Record<string, unknown>)["version"] = "0.4.6"; // 字符串元数据
    (wf as Record<string, unknown>)["comfyUiApi"] = "0.1.3";
    expect(scanDirectorNodeIds(wf)).toEqual(["5"]);
  });
});

describe("V1.8-2 workflowRegistry：JSON 解析", () => {
  it("parseWorkflowJson 解析合法 workflow_api 文本", () => {
    const r = parseWorkflowJson(JSON.stringify(makeWorkflow(1)));
    expect(r.ok).toBe(true);
    expect(r.workflow?.["5"]).toEqual({ class_type: "MiniMaxH3Director", inputs: { frame_rate: 24, timeline_data: [] } });
  });

  it("parseWorkflowJson 容忍顶层元数据 key，只保留带 class_type 的节点", () => {
    const raw = { version: "0.4.6", last_node_id: 6, ...makeWorkflow(1) };
    const r = parseWorkflowJson(JSON.stringify(raw));
    expect(r.ok).toBe(true);
    expect(Object.keys(r.workflow!)).toEqual(["1", "5"]);
  });

  it("parseWorkflowJson 对坏 JSON 返回错误", () => {
    expect(parseWorkflowJson("{ not json")).toEqual({ ok: false, error: expect.stringContaining("JSON") });
  });

  it("parseWorkflowJson 对无 class_type 节点的结构返回错误", () => {
    const r = parseWorkflowJson(JSON.stringify({ version: "0.4.6", nodes: [] }));
    expect(r.ok).toBe(false);
    expect(r.error).toContain("class_type");
  });
});

describe("V1.8-2 workflowRegistry：条目构建", () => {
  it("buildWorkflowEntry 单节点自动选中", () => {
    const e = buildWorkflowEntry(makeWorkflow(1), "my_flow");
    expect(e.source).toBe("manual");
    expect(e.name).toBe("my_flow");
    expect(e.directorNodeIds).toEqual(["5"]);
    expect(e.selectedNodeId).toBe("5");
  });

  it("buildWorkflowEntry 多节点不预选（强制用户选择）", () => {
    const e = buildWorkflowEntry(makeWorkflow(3), "multi");
    expect(e.directorNodeIds).toEqual(["5", "6", "7"]);
    expect(e.selectedNodeId).toBeNull();
  });

  it("buildBuiltinEntry 固定 id 且单节点自动选中", () => {
    const e = buildBuiltinEntry(makeWorkflow(1));
    expect(e.id).toBe(BUILTIN_WORKFLOW_ID);
    expect(e.source).toBe("builtin");
    expect(e.selectedNodeId).toBe("5");
  });
});

describe("V1.8-2 workflowRegistry：激活态解析", () => {
  it("无条目 → invalid（调用方回退内置）", () => {
    const aw = resolveActiveWorkflow(null);
    expect(aw.status).toBe("invalid");
    expect(aw.nodeId).toBeNull();
  });

  it("0 节点 → none：非 H3 工作流（生成被阻止）", () => {
    const e = buildWorkflowEntry({ "1": { class_type: "UNETLoader" } }, "bad");
    const aw = resolveActiveWorkflow(e);
    expect(aw.status).toBe("none");
    expect(aw.nodeCount).toBe(0);
    expect(aw.nodeId).toBeNull();
  });

  it("单节点 → ok，nodeId 自动", () => {
    const e = buildWorkflowEntry(makeWorkflow(1), "one");
    const aw = resolveActiveWorkflow(e);
    expect(aw.status).toBe("ok");
    expect(aw.nodeId).toBe("5");
  });

  it("多节点未选 → multi：需选择生成节点（生成被阻止）", () => {
    const e = buildWorkflowEntry(makeWorkflow(2), "two");
    const aw = resolveActiveWorkflow(e);
    expect(aw.status).toBe("multi");
    expect(aw.nodeCount).toBe(2);
    expect(aw.nodeId).toBeNull();
  });

  it("多节点已选 → ok，nodeId = 所选节点", () => {
    const e = buildWorkflowEntry(makeWorkflow(2), "two");
    e.selectedNodeId = "6";
    const aw = resolveActiveWorkflow(e);
    expect(aw.status).toBe("ok");
    expect(aw.nodeId).toBe("6");
  });
});

describe("V1.8-2 workflowRegistry：持久化还原", () => {
  it("loadWorkflowRegistry 从 raw JSON 还原条目", () => {
    const raw = JSON.stringify([buildWorkflowEntry(makeWorkflow(1), "saved")]);
    const list = loadWorkflowRegistry(raw);
    expect(list.length).toBe(1);
    expect(list[0].name).toBe("saved");
    expect(list[0].directorNodeIds).toEqual(["5"]);
  });

  it("loadWorkflowRegistry 对坏数据返回空数组", () => {
    expect(loadWorkflowRegistry(null)).toEqual([]);
    expect(loadWorkflowRegistry("not-json")).toEqual([]);
    expect(loadWorkflowRegistry(JSON.stringify({ a: 1 }))).toEqual([]);
    expect(loadWorkflowRegistry(JSON.stringify([{ id: 1 }]))).toEqual([]);
  });

  it("导出 key 约定稳定（跨会话持久化依赖）", () => {
    expect(WORKFLOW_REGISTRY_KEY).toMatch(/^minimax_studio\./);
    expect(DEFAULT_DIRECTOR_NODE_ID).toBe("5");
  });
});
