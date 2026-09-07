/**
 * V1.4-P0 失败镜头还原（纯函数，可单测）。
 *
 * failedShotIds 采用双源并集并去重：
 * - segStatus.states === "failed" 的段索引 → indexShotIds[idx]（状态灯失败源）
 * - errorDetail 里解析出的段号 → shotOrder[N-1]（异常信息源，后端段号为 1-based）
 *
 * 双源原因：真实失败可能发生在 execution_error 时 segStatus 尚未同步；反之异常信息未必带段号。
 */

import type { SegStatus } from "@/stores/workbench";

/**
 * 从异常信息里提取段号（后端 1-based）。
 * 覆盖两种真实报错格式：英文 "Segment 3"（segment_cache.py）与中文 "片段 #3"（segment_continuity.py）。
 * 故意不匹配裸「段 #N」：中文句式「上一段 #1」是前驱引用而非失败镜头，误匹配会扩大 failedShotIds。
 */
const SEGMENT_RE = /(?:Segment|segment|片段)\s*#?\s*(\d+)/g;

export function resolveFailedShotIds(
  shotOrder: string[],
  errorDetail: string | null | undefined,
  segStatus?: SegStatus | null,
): string[] {
  const out = new Set<string>();
  if (segStatus) {
    for (const [idx, v] of Object.entries(segStatus.states)) {
      if (v === "failed") {
        const sid = segStatus.indexShotIds[Number(idx)];
        if (sid) out.add(sid);
      }
    }
  }
  if (errorDetail) {
    let m: RegExpExecArray | null;
    SEGMENT_RE.lastIndex = 0;
    while ((m = SEGMENT_RE.exec(errorDetail))) {
      const sid = shotOrder[Number(m[1]) - 1];
      if (sid) out.add(sid);
    }
  }
  return [...out];
}
