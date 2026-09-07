/**
 * TimelineStructure — Director Core 的中间产物，模型无关。
 *
 * 数据链路：ShotModel → TimelineStructure → Adapter → timeline_data
 * Core 输出本结构，Adapter 负责翻译成具体模型的 timeline JSON。
 *
 * 设计要点：
 * - `durationSec` 是唯一时长真相；start/length/frameCount 由 Adapter 派生。
 * - scenes/segments/assets 结构与后端 plan.py 对齐（scenes 带素材组，segments 按 sceneId 归属）。
 * - 不出现 H3 字段名；taskType/continuityMode 用语义枚举，由 Adapter 映射。
 */

import type { Asset, SceneAssets } from "@/models/project";

/** V1 只做 prompt_batch（r2v）；fl2v/video 后置。 */
export type TimelineMode = "prompt_batch" | "fl2v" | "video" | "gen_blank" | "gen_image";

export type TaskKey = "t2v" | "r2v" | "i2v" | "i2i" | "fl2v" | "";

export type ContinuityMode = "auto" | "none" | "ref2va" | "fl2va";

export interface TimelineScene {
  id: string;
  name: string;
  location: string;
  time: string;
  order: number;
  /** 场景素材组（Scene Asset Group）。 */
  assets: SceneAssets;
  defaultCastId: string;
  defaultLocationId: string;
}

export interface TimelineShot {
  /** 对应 ShotModel 的 id（跨链路保持稳定，前端据此映射回项目模型）。 */
  id: string;
  sceneId: string;
  durationSec: number;
  prompt: string;
  negativePrompt: string;
  taskKey: TaskKey;
  refs: Asset[]; // 参考图（图片资产）
  refAudios: Array<{ index: number; audioFile: string; fileName?: string }>;
  refVideos: Array<{ index: number; videoFile: string; fileName?: string }>;
  genImage: { imageFile: string; fileName?: string };
  /** 兼容单值（首元素，旧后端/快照读 castId）；V1.6-B 起主要写 castIds。 */
  castId: string;
  /** V1.6-B 多角色集合：本镜全部角色 id（显式 ∪ @命中，canonical 去重后）。 */
  castIds: string[];
  /** P0-1 per-shot 资产边界：本镜显式选中的道具/风格参考 id（非全场景扩散）。 */
  propIds: string[];
  styleIds: string[];
  locationId: string;
  castManual: boolean;
  locationManual: boolean;
  stateChange: string;
  smartTail: boolean;
  continuityMode: ContinuityMode;
  consistencyCheck: boolean | "auto";
}

export interface TimelineStructure {
  mode: TimelineMode;
  frameRate: number;
  width: number;
  height: number;
  refMaxSize: number;
  /** 全局资产：角色 + 场景（locations 复数）。V1 只这两类。 */
  assets: { cast: Asset[]; locations: Asset[] };
  scenes: TimelineScene[];
  shots: TimelineShot[];
  output: {
    mode: "fixed" | "long_edge";
    longEdge: number;
    width: number;
    height: number;
    maxExportFrames: number;
    exportMode: "segments" | "scene" | "movie" | "all";
    continuityEnabled: boolean;
    continuityOverlapFrames: number;
    audioMode: string;
    qwenVlEnabled: boolean;
    qwenVlLevel: number;
  };
  /** 生成范围：null=全部，非空数组=指定镜头 id 列表。 */
  runSelection: string[] | null;
}
