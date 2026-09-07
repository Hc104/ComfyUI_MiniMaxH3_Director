/**
 * 播放队列（V1.3.1）：把「已生成镜头」组织成可顺序播放的列表。
 *
 * 三个播放入口共用：
 * - 单镜播放（scope="shot"）  → 只播目标镜头（有缓存才可播）
 * - 场景播放（scope="scene"） → 顺序播放该场景已缓存镜头
 * - 完整视频（scope="all"）   → 顺序播放全部已缓存镜头
 *
 * 镜头没有磁盘缓存（未生成/缓存失效）会被跳过；返回空数组表示无可播内容。
 * cached 为段索引集合（与 flattenedShots 同序），由 /segment_status 提供。
 */
export type PlayScope = "shot" | "scene" | "all";

export interface PlayableShot {
  /** 镜头 id（喂 segment_mp4 需要段索引，用外部 flattenedShots().indexOf(id)）。 */
  shotId: string;
}

export function collectPlayableShotIds(
  shots: Array<{ id: string }>,
  cached: Set<number>,
  scope: PlayScope,
  opts: { targetShotId?: string; sceneShotIds?: string[] } = {},
): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();
  for (let i = 0; i < shots.length; i++) {
    if (!cached.has(i)) continue;
    const s = shots[i];
    if (seen.has(s.id)) continue;
    let include = false;
    if (scope === "shot") {
      include = opts.targetShotId != null && s.id === opts.targetShotId;
    } else if (scope === "scene") {
      include = (opts.sceneShotIds ?? []).includes(s.id);
    } else {
      include = true;
    }
    if (include) {
      seen.add(s.id);
      ids.push(s.id);
    }
  }
  return ids;
}
