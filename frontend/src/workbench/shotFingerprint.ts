/**
 * 镜头参数指纹（V1.3-D）：生成参数的稳定摘要。
 * 用于「参数已变需重生成」：生成成功后记录该镜头当时的指纹，
 * 若当前镜头参数与生成时不同 → 状态灯提示需重生成（即使有缓存）。
 */
import type { Shot, ShotGeneration } from "@/models/project";
import { shotCastIds } from "@/core/inheritanceResolver";

export interface ShotGenParams {
  outputSize: { width: number; height: number };
  frameRate: number;
}

const SEP = "";

/** 计算镜头生成参数指纹（字符串拼接；字段内不可能出现 分隔符）。 */
export function shotFingerprint(shot: Shot, params: ShotGenParams): string {
  const g: ShotGeneration = shot.generation ?? ({} as ShotGeneration);
  const refs = shot.refs ?? { refImages: [], refAudios: [], refVideos: [], genImage: { imageFile: "" } };
  const parts: unknown[] = [
    shot.content.visual ?? "",
    shot.content.cameraText ?? "",
    shot.content.style ?? "",
    shot.content.soundText ?? "",
    shot.content.negativePrompt ?? "",
    shot.durationSec,
    shotCastIds(shot).join("|"),
    shot.locationId ?? "",
    g.taskType ?? "",
    g.continuityMode ?? "",
    g.smartTail ?? false,
    g.stateChange ?? "",
    refs.refImages.map((r) => r.imageFile).join("|"),
    refs.refAudios.map((r) => r.audioFile).join("|"),
    refs.refVideos.map((r) => r.videoFile).join("|"),
    params.outputSize.width,
    params.outputSize.height,
    params.frameRate,
  ];
  return parts.join(SEP);
}

/** 指纹转短显示（调试/提示用）。 */
export function shortFingerprint(hash: string): string {
  let h = 0;
  for (let i = 0; i < hash.length; i++) {
    h = (h * 31 + hash.charCodeAt(i)) | 0;
  }
  return `#${(h >>> 0).toString(16).slice(0, 6)}`;
}
