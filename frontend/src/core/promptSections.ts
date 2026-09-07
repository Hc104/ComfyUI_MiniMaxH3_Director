/**
 * DirectorCore — Prompt 分区合并（模型无关，V2 Prompt 分区编辑器）。
 *
 * 镜头 prompt 由多个分区组成，生成时合并成一段发给后端：
 *   画面(visual) → 摄影(cameraText) → 风格(style) → 声音(soundText)
 * 负面 Prompt 独立（negativePrompt）。
 *
 * 合并规则（用户拍板）：**不翻译、不套模板**——各分区原样拼接，分隔符按输入语言
 * 选择：含中日韩字符 → 中文逗号「，」，否则英文逗号+空格「, 」。
 * 保持向后兼容：camera/sound 的结构化旧字段（ShotCamera/ShotSound）有值且
 * 对应自由文本为空时，回退到结构化描述（describeCamera/describeSound）。
 */
import type { Shot, ShotCamera, ShotSound } from "@/models/project";

/** CJK 检测（中日韩统一表意文字 + 日文假名 + 韩文音节）。 */
const CJK_RE = /[一-鿿぀-ヿ가-힯]/;

/** 按内容语言选择分区连接符。 */
export function joinPromptParts(parts: string[]): string {
  const nonEmpty = parts.filter((p) => (p ?? "").trim().length > 0);
  if (nonEmpty.length === 0) return "";
  const text = nonEmpty.join("");
  return nonEmpty.join(CJK_RE.test(text) ? "，" : ", ");
}

/** 摄影分区：优先自由文本 cameraText，空则回退结构化 describeCamera。 */
export function shotCameraText(shot: Shot): string {
  const t = (shot.content.cameraText ?? "").trim();
  if (t) return t;
  return describeCamera(shot.camera);
}

/** 声音分区：优先自由文本 soundText，空则回退结构化 describeSound。 */
export function shotSoundText(shot: Shot): string {
  const t = (shot.content.soundText ?? "").trim();
  if (t) return t;
  return describeSound(shot.sound);
}

/** 合并后的完整正向 prompt（directorCore 用它替换 content.visual 直接透传）。 */
export function buildShotPromptText(shot: Shot): string {
  const parts: string[] = [];
  const visual = (shot.content.visual ?? "").trim();
  if (visual) parts.push(visual);
  const cam = shotCameraText(shot);
  if (cam) parts.push(cam);
  const style = (shot.content.style ?? "").trim();
  if (style) parts.push(style);
  const sound = shotSoundText(shot);
  if (sound) parts.push(sound);
  return joinPromptParts(parts);
}

/** 结构化摄影语言描述：景别 + 运镜 + 速度 + 景深。 */
function describeCamera(cam?: ShotCamera): string {
  if (!cam) return "";
  const parts: string[] = [];
  if (cam.shotSize) parts.push(cam.shotSize);
  if (cam.movement) parts.push(cam.movement);
  if (cam.speed) parts.push(cam.speed);
  if (cam.depthOfField) parts.push(cam.depthOfField);
  return parts.join("，");
}

/** 结构化声音描述：环境音 + 音乐。 */
function describeSound(s?: ShotSound): string {
  if (!s) return "";
  const parts: string[] = [];
  if (s.ambient) parts.push(s.ambient);
  if (s.music) parts.push(s.music);
  return parts.join("，");
}
