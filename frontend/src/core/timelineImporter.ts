/**
 * DirectorCore — 反向导入通道（timeline_data → Project）。
 *
 * V1.1 核心：先支持「加载现节点导出的 timeline_data」转成前端项目模型（导入通道）。
 *
 * 数据流（反向）：
 *   timeline_data（后端契约 JSON） → TimelineImporter.parseTimelineData → Project + ImportMeta
 *
 * 与正向链路（Project → buildTimelineStructure → Adapter → timeline_data）互为逆运算：
 *   导入后重新导出应还原等价契约（round-trip 测试验证）。
 *
 * 读兼容（蓝图 §5.5）：camelCase 为主，兼容 snake_case（assets.location 单数 → locations）。
 */

import type { Asset, Episode, Project, Scene, SceneAssets, Shot, ShotGeneration } from "@/models/project";

export interface ImportMeta {
  frameRate: number;
  width: number;
  height: number;
  refMaxSize: number;
  output: {
    mode: string;
    longEdge: number;
    width: number;
    height: number;
    maxExportFrames: number;
    exportMode: string;
    continuityEnabled: boolean;
    continuityOverlapFrames: number;
    audioMode: string;
    qwenVlEnabled: boolean;
    qwenVlLevel: number;
  };
}

export interface ImportResult {
  project: Project;
  meta: ImportMeta;
}

/** timeline_data → Project + meta。 */
export function parseTimelineData(raw: Record<string, unknown>): ImportResult {
  const frameRate = num(raw, "frameRate", "frame_rate", 24);
  const width = num(raw, "width", undefined, 864);
  const height = num(raw, "height", undefined, 480);
  const refMaxSize = num(raw, "refMaxSize", "ref_max_size", Math.max(width, height));

  // ---- 全局资产（兼容 location/locations 单复数）----
  const assetsRaw = obj(raw, "assets");
  const globalCast = toAssets(assetsRaw, "cast", "cast");
  const globalLocations = toAssets(assetsRaw, "locations", "location");
  const episodeAssets: SceneAssets = {
    cast: globalCast,
    locations: globalLocations,
    props: [],
    styles: [],
  };

  // ---- 场景 ----
  const scenesRaw = arr(raw, "scenes");
  const scenes: Scene[] = scenesRaw.map((s, i) => {
    const sObj = obj(s);
    const sAssetsRaw = obj(sObj, "assets");
    const sceneAssets: SceneAssets = {
      cast: toAssets(sAssetsRaw, "cast", "cast"),
      locations: toAssets(sAssetsRaw, "locations", "location"),
      props: toAssets(sAssetsRaw, "props", "props"),
      styles: toAssets(sAssetsRaw, "styles", "styles"),
    };
    return {
      id: str(sObj, "id", `scene_${i + 1}`),
      name: str(sObj, "name", `地点 ${i + 1}`),
      order: num(sObj, "order", undefined, i),
      description: strOrUndef(sObj, "description"),
      location: strOrUndef(sObj, "location"),
      time: strOrUndef(sObj, "time"),
      weather: strOrUndef(sObj, "weather"),
      referenceImage: strOrUndef(sObj, "referenceImage", "reference_image"),
      assets: sceneAssets,
      defaultCastId: strOrUndef(sObj, "defaultCastId", "default_cast_id"),
      defaultLocationId: strOrUndef(sObj, "defaultLocationId", "default_location_id"),
      defaultStyleId: strOrUndef(sObj, "defaultStyleId", "default_style_id"),
      shots: [],
    };
  });

  // ---- 段 → 镜头（按 sceneId 归组，组内按 start 排序）----
  const segmentsRaw = arr(raw, "segments");
  const shotByScene = new Map<string, Shot[]>();
  const fallbackSceneId = "scene_imported";

  for (const seg of segmentsRaw) {
    const s = obj(seg);
    const sceneId = str(s, "sceneId", "scene_id", fallbackSceneId);
    const shot = segmentToShot(s, sceneId, frameRate);
    const list = shotByScene.get(sceneId) ?? [];
    list.push(shot);
    shotByScene.set(sceneId, list);
  }

  for (const scene of scenes) {
    const list = shotByScene.get(scene.id) ?? [];
    list.sort((a, b) => (a.generation?.order ?? 0) - (b.generation?.order ?? 0));
    scene.shots = list.map((shot, i) => ({ ...shot, order: i }));
    shotByScene.delete(scene.id);
  }

  // 孤儿段（sceneId 不在 scenes 块）→ 兜底「未归类」场景。
  if (shotByScene.size > 0) {
    const orphanShots: Shot[] = [];
    for (const list of shotByScene.values()) orphanShots.push(...list);
    orphanShots.sort((a, b) => (a.generation?.order ?? 0) - (b.generation?.order ?? 0));
    scenes.push({
      id: fallbackSceneId,
      name: "未归类",
      order: scenes.length,
      shots: orphanShots.map((s, i) => ({ ...s, order: i, sceneId: fallbackSceneId })),
    });
  }

  const episodeId = str(raw, "episodeId", "episode_id", "ep1");
  const episode: Episode = {
    id: episodeId,
    episodeNumber: num(raw, "episodeNumber", "episode_number", 1),
    title: str(raw, "episodeTitle", "episode_title", "导入工程"),
    description: strOrUndef(raw, "episodeDescription", "episode_description"),
    scenes,
    assets: episodeAssets,
  };

  const project: Project = {
    id: str(raw, "projectId", "project_id", `proj_imported_${Date.now()}`),
    name: str(raw, "projectName", "project_name", "导入工程"),
    createdAt: str(raw, "createdAt", "created_at", new Date().toISOString()),
    updatedAt: str(raw, "updatedAt", "updated_at", new Date().toISOString()),
    episodes: [episode],
  };

  const outputRaw = obj(raw, "output");
  const meta: ImportMeta = {
    frameRate,
    width,
    height,
    refMaxSize,
    output: {
      mode: str(outputRaw, "mode", "fixed"),
      longEdge: num(outputRaw, "longEdge", "long_edge", Math.max(width, height)),
      width: num(outputRaw, "width", undefined, width),
      height: num(outputRaw, "height", undefined, height),
      maxExportFrames: num(outputRaw, "maxExportFrames", "max_export_frames", 0),
      exportMode: str(outputRaw, "exportMode", "export_mode", "all"),
      continuityEnabled: bool(outputRaw, "continuityEnabled", "continuity_enabled", false),
      continuityOverlapFrames: num(outputRaw, "continuityOverlapFrames", "continuity_overlap_frames", 9),
      audioMode: str(outputRaw, "audioMode", "audio_mode", "auto"),
      qwenVlEnabled: bool(outputRaw, "qwenVlEnabled", "qwen_vl_enabled", false),
      qwenVlLevel: num(outputRaw, "qwenVlLevel", "qwen_vl_level", 1),
    },
  };

  return { project, meta };
}

/** JSON 字符串版本。 */
export function importTimelineJson(json: string): ImportResult {
  return parseTimelineData(JSON.parse(json) as Record<string, unknown>);
}

// ---------- 段 → Shot ----------

const TASK_KEYS = ["r2v", "t2v", "i2v", "fl2v"] as const;

function segmentToShot(s: Record<string, unknown>, sceneId: string, frameRate: number): Shot {
  const durationSec =
    num(s, "durationSec", "duration_sec", 0) || Math.round(num(s, "frameCount", "frame_count", 0) / frameRate);
  const taskLabel = str(s, "taskType", "task_type", "r2v");
  const taskType = reverseTask(taskLabel);
  const continuityMode = str(s, "continuityMode", "continuity_mode", "auto");
  const smartTail = bool(s, "smartTail", "smart_tail", false);
  const stateChange = str(s, "stateChange", "state_change", "");
  const consistency = s.consistencyCheck ?? s.consistency_check ?? "auto";

  const refsRaw = arr(s, "refs");
  const refAudiosRaw = arr(s, "refAudios", "ref_audios");
  const refVideosRaw = arr(s, "refVideos", "ref_videos");
  const genImageRaw = obj(s, "genImage", "gen_image");

  const generation: ShotGeneration & { order: number } = {
    taskType: taskType as ShotGeneration["taskType"],
    continuityMode: (["auto", "none", "ref2va", "fl2va"].includes(continuityMode)
      ? continuityMode
      : "auto") as ShotGeneration["continuityMode"],
    smartTail,
    stateChange,
    consistencyCheck: consistency === true || consistency === false ? consistency : "auto",
    order: num(s, "start", undefined, 0),
  };

  return {
    id: str(s, "id", `shot_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`),
    sceneId,
    order: 0, // 归组后由场景重排
    name: strOrUndef(s, "name"),
    durationSec,
    content: {
      visual: str(s, "prompt", ""),
      negativePrompt: strOrUndef(s, "negativePrompt", "negative_prompt"),
    },
    refs: {
      refImages: refsRaw.map((r) => ({
        index: num(obj(r), "index", undefined, 0),
        imageFile: str(obj(r), "imageFile", "image_file", ""),
        fileName: strOrUndef(obj(r), "fileName", "file_name"),
      })),
      refAudios: refAudiosRaw.map((r, i) => ({
        index: num(obj(r), "index", undefined, i),
        audioFile: str(obj(r), "audioFile", "audio_file", ""),
        fileName: strOrUndef(obj(r), "fileName", "file_name"),
      })),
      refVideos: refVideosRaw.map((r, i) => ({
        index: num(obj(r), "index", undefined, i),
        videoFile: str(obj(r), "videoFile", "video_file", ""),
        fileName: strOrUndef(obj(r), "fileName", "file_name"),
      })),
      genImage: { imageFile: str(genImageRaw, "imageFile", "image_file", "") },
    },
    castId: strOrUndef(s, "castId", "cast_id"),
    castIds: strArr(s, "castIds", "cast_ids"),
    locationId: strOrUndef(s, "locationId", "location_id"),
    castManual: bool(s, "castManual", "cast_manual", false),
    locationManual: bool(s, "locationManual", "location_manual", false),
    generation,
  };
}

function reverseTask(label: string): string {
  const lower = label.toLowerCase();
  for (const k of TASK_KEYS) {
    if (lower.includes(k)) return k;
  }
  return "r2v";
}

// ---------- 读辅助 ----------

function pick(obj: Record<string, unknown>, keys: string[]): unknown {
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

function str(o: Record<string, unknown>, camel: string, snake?: string, fallback = ""): string {
  const v = pick(o, snake ? [camel, snake] : [camel]);
  return v == null ? fallback : String(v);
}

function strOrUndef(o: Record<string, unknown>, camel: string, snake?: string): string | undefined {
  const v = pick(o, snake ? [camel, snake] : [camel]);
  return v == null || v === "" ? undefined : String(v);
}

function num(o: Record<string, unknown>, camel: string, snake?: string, fallback = 0): number {
  const v = pick(o, snake ? [camel, snake] : [camel]);
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function bool(o: Record<string, unknown>, camel: string, snake?: string, fallback = false): boolean {
  const v = pick(o, snake ? [camel, snake] : [camel]);
  if (v === true || v === false) return v;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "string") return v === "true" || v === "1";
  return fallback;
}

function obj(o: Record<string, unknown>, camel?: string, snake?: string): Record<string, unknown> {
  let v: unknown = o;
  if (camel) v = o[camel] ?? (snake ? o[snake] : undefined);
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

function arr(o: Record<string, unknown>, camel: string, snake?: string): Array<Record<string, unknown>> {  const v = pick(o, snake ? [camel, snake] : [camel]);
  return Array.isArray(v) ? (v as Array<Record<string, unknown>>) : [];
}

/** 字符串数组读取（V1.6-B castIds；空/非数组返回 undefined）。 */
function strArr(o: Record<string, unknown>, camel: string, snake?: string): string[] | undefined {
  const v = pick(o, snake ? [camel, snake] : [camel]);
  if (!Array.isArray(v)) return undefined;
  const ids = v.map((x) => String(x)).filter((x) => x);
  return ids.length ? ids : undefined;
}

/** 从 assets 块取一类资产（兼容 name/kind/imageFile 字段）。 */
function toAssets(assets: Record<string, unknown>, camel: string, snake: string): Asset[] {
  const list = arr(assets, camel, snake);
  return list.map((a, i) => ({
    id: str(a, "id", undefined, `asset_${camel}_${i}`),
    name: str(a, "name", undefined, `未命名${camel}_${i}`),
    kind: (str(a, "kind", undefined, camel) as Asset["kind"]) ?? camel,
    imageFile: str(a, "imageFile", "image_file", ""),
    description: strOrUndef(a, "description"),
    aliases: Array.isArray(a.aliases) ? (a.aliases as string[]) : undefined,
  }));
}
