/**
 * fixtures 加载器 — 真实数据驱动的开发基础。
 * 让 Core/Adapter/UI 始终针对真实契约数据开发，而不是凭空假设。
 */
import type { Project } from "@/models/project";

import sampleProjectJson from "../fixtures/h3/sample-project.json";
import sampleTimelineJson from "../fixtures/h3/sample-r2v-timeline.json";
import sampleWorkflowJson from "../fixtures/h3/sample-workflow.json";

/** 加载样例项目模型（前端语义模型）。深拷贝返回，避免调用方编辑写进模块级 JSON 对象。 */
export function loadSampleProject(): Project {
  return JSON.parse(JSON.stringify(sampleProjectJson)) as unknown as Project;
}

/** 加载样例 timeline_data（后端契约目标值，用于 round-trip 校验）。 */
export function loadSampleTimeline(): Record<string, unknown> {
  return sampleTimelineJson as unknown as Record<string, unknown>;
}

/** 加载样例工作流（prompt API 格式，提交 /prompt 用）。深拷贝返回（生成流程会改写 widget）。 */
export function loadSampleWorkflow(): Record<string, unknown> {
  return JSON.parse(JSON.stringify(sampleWorkflowJson)) as unknown as Record<string, unknown>;
}
