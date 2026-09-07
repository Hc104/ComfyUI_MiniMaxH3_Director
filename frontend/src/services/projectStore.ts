/**
 * Project 文件层（V1.1 最小）。
 *
 * 职责：项目模型的导入 / 序列化 / 反序列化。
 * V1.1 聚焦「导入现节点导出的 timeline_data」→ Project（蓝图 §8 V1.1）。
 * 完整 Projects/ 目录持久化（episode.json 读写）留到 V1.4。
 */

import type { Project } from "@/models/project";
import { parseTimelineData, importTimelineJson, type ImportMeta } from "@/core/timelineImporter";

export interface ImportProjectResult {
  project: Project;
  meta: ImportMeta;
}

/** timeline_data（对象）→ Project + meta。 */
export function importProjectFromTimeline(timelineData: Record<string, unknown>): ImportProjectResult {
  return parseTimelineData(timelineData);
}

/** timeline_data（JSON 字符串）→ Project + meta。 */
export function importProjectFromTimelineJson(json: string): ImportProjectResult {
  return importTimelineJson(json);
}

/** Project → 规范化 JSON 字符串（供保存/导出）。 */
export function serializeProject(project: Project): string {
  return JSON.stringify(project, null, 2);
}

/** JSON 字符串 → Project。 */
export function deserializeProject(json: string): Project {
  return JSON.parse(json) as Project;
}
