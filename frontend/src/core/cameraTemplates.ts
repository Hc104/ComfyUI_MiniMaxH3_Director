/**
 * V1.7 Phase 5（P1-D）：运镜模板占位符填充（模型无关，可单测）。
 *
 * 后端 camera_template.list_camera_templates 返回的 template 含
 * {location}/{time}/{subject}/{emotion}/{object} 占位符（与 camera_template.py
 * pick_camera 的 .format 一致）。前端下拉选模板时用本模块把占位符替换成
 * 本镜/本场景的实际值，自动填进 cameraText（模板下拉 + 手编共存）。
 *
 * 不翻译、不套额外模板：填充结果就是最终 cameraText，用户可直接手改。
 * 占位符填充语义与后端 pick_camera 保持一致，避免同一模板两端措辞漂移。
 */
import type { CameraTemplate } from "@/services/comfyApi";
import type { Scene, Shot } from "@/models/project";

/** 场景地点名（location_name → scene.location → scene.name → "地点"）。 */
export function sceneLocationName(scene: Scene): string {
  return (scene.location ?? "").trim() || scene.name?.trim() || "地点";
}

/** 场景时间/天气提示（"清晨、雨"；空 → "当下"，与后端 time_hint 一致）。 */
export function sceneTimeHint(scene: Scene): string {
  const parts = [(scene.time ?? "").trim(), (scene.weather ?? "").trim()].filter(Boolean);
  return parts.join("、") || "当下";
}

/** 本镜角色名（"林雪" / "林雪、陈默"；空 → "人物"）。 */
export function shotSubject(shot: Shot): string {
  const names = (shot.castIds ?? []).map((n) => n.trim()).filter(Boolean);
  return names.join("、") || "人物";
}

/** 本镜道具名（props 取首个；空 → "目标"）。 */
export function shotObject(shot: Shot): string {
  const p = shot.castIds ?? [];
  void p;
  // 道具不在 castIds；从场景资产 props 或镜头描述里取——暂无结构化 props 字段，
  // 兜底用「目标」，保持与后端 _props_text 的空值语义一致。
  return "目标";
}

/** 本镜情绪（空 → "微妙"，与后端 emotion 兜底一致）。 */
export function shotEmotion(_shot: Shot): string {
  // Shot 无顶层 emotion 字段；从 description/visual 兜底取不到则用「微妙」。
  return "微妙";
}

/** 模板 id → 模板条目（无则 null）。 */
export function findCameraTemplate(templates: CameraTemplate[], id?: string): CameraTemplate | null {
  if (!id) return null;
  return templates.find((t) => t.id === id) ?? null;
}

/** 把模板占位符替换成本镜/场景实际值，返回可直接进 cameraText 的文案。 */
export function fillCameraTemplate(tmpl: CameraTemplate, shot: Shot, scene: Scene): string {
  return tmpl.template
    .replace(/\{location\}/g, sceneLocationName(scene))
    .replace(/\{time\}/g, sceneTimeHint(scene))
    .replace(/\{subject\}/g, shotSubject(shot))
    .replace(/\{emotion\}/g, shotEmotion(shot))
    .replace(/\{object\}/g, shotObject(shot));
}
