/**
 * DirectorCore — Prompt Builder（模型无关）。
 *
 * 职责：结构化字段（content/camera/sound/assets）→ 模型无关的提示词结构。
 * 简洁模式（一句话）与高级模式（影视级三段）同源，由本模块派生。
 * Adapter 负责把高级模式的字段翻译成具体模型的 H3 命名
 * （integrated_multimodal_description / overall_soundscape / non_diegetic_music）。
 */

import type { Shot, ShotCamera } from "@/models/project";
import type { InheritedAsset } from "@/core/inheritanceResolver";

export interface ShotPrompt {
  /** 简洁模式：一句话（画面 + 主要运镜）。 */
  simple: string;
  /** 高级模式：影视级分段。 */
  advanced: {
    integratedMultimodalDescription: string;
    overallSoundscape: string;
    nonDiegeticMusic: string;
    negative: string;
  };
}

/** 组装镜头提示词。V1 最小实现：visual 为画面主体；camera/sound/继承资产有则并入。 */
export function buildShotPrompt(shot: Shot, inherited: InheritedAsset[]): ShotPrompt {
  const visual = (shot.content.visual ?? "").trim();
  const camDesc = describeCamera(shot.camera);
  const inheritedDesc = describeInherited(inherited);

  // 画面主体：visual → 继承资产引用 → 摄影语言。
  const parts = [visual];
  if (inheritedDesc) parts.push(inheritedDesc);
  if (camDesc) parts.push(camDesc);
  const integrated = parts.filter(Boolean).join("，");

  const sound = shot.sound;

  return {
    simple: visual || integrated,
    advanced: {
      integratedMultimodalDescription: integrated,
      overallSoundscape: sound?.ambient ?? "",
      nonDiegeticMusic: sound?.music ?? "",
      negative: shot.content.negativePrompt ?? "",
    },
  };
}

/** 摄影语言描述：景别 + 运镜 + 速度 + 景深。 */
function describeCamera(cam?: ShotCamera): string {
  if (!cam) return "";
  const parts: string[] = [];
  if (cam.shotSize) parts.push(cam.shotSize);
  if (cam.movement) parts.push(cam.movement);
  if (cam.speed) parts.push(cam.speed);
  if (cam.depthOfField) parts.push(cam.depthOfField);
  return parts.join("，");
}

/** 继承资产描述：把镜头实际生效的角色/地点名字并入（V1 只 cast/location）。 */
function describeInherited(inherited: InheritedAsset[]): string {
  if (inherited.length === 0) return "";
  const labels: string[] = [];
  for (const item of inherited) {
    if (item.asset.kind === "cast") labels.push(`角色：${item.asset.name}`);
    else if (item.asset.kind === "location") labels.push(`地点：${item.asset.name}`);
    // props/styles V1 不参与继承链，跳过
  }
  return labels.join("，");
}
