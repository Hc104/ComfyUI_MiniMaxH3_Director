/**
 * Voice Cast 前端纯逻辑（Phase 2，#573）。
 *
 * 职责（纯函数、零副作用、可单测）：
 *   - 台词定位：plan.scenes[].shots[].dialogue 按 sceneId + planShotId 取行；
 *   - 类型推断：旁白/系统说话人强规则（与后端 audio_intent.infer_voice_type 对齐）；
 *   - voice_id 解析：行级显式 > Voice Cast 全局（旁白/系统/电子）> 规则兜底；
 *   - 合成 payload：buildTtsLinesPayload → POST /tts/synthesize 的 lines 数组；
 *   - 角色发现：collectSpeakers 供 Voice Cast 面板建角色行。
 *
 * ⛔ 纯规则零 LLM 零显存：本模块不 import 任何后端/网络模块。
 */

import type { ProductionPlanJson, TtsVoiceInfo } from "@/services/comfyApi";
import type { VoiceCastConfig, VoiceTypeId } from "@/models/project";

/** 行级配音视图：plan dialogue 行 + 已解析 voice_id（面板展示/行级微调用）。 */
export interface DialogueLineView {
  /** 行号（1-based），对应后端 tts/line_NNN.wav。 */
  index: number;
  speaker: string;
  text: string;
  type: VoiceTypeId;
  /** 已解析音色（行级显式 > Voice Cast 全局 > 规则兜底）。 */
  voiceId: string;
  emotion: string;
  delivery: string;
}

/** POST /tts/synthesize 的 lines 元素（后端直接消费 voice_id）。 */
export interface TtsLinePayload {
  text: string;
  speaker: string;
  voice_type: VoiceTypeId;
  voice_id: string;
  emotion: string;
  delivery: string;
}

const NARRATOR_SPEAKERS = ["旁白", "叙述", "叙述者", "旁白音", "画外音", "说书人"];
const SYSTEM_SPEAKERS = ["系统", "系统音", "提示音", "小助手", "AI", "人工智能"];

const VOICE_TYPE_IDS: VoiceTypeId[] = [
  "character_dialogue",
  "narration",
  "inner_monologue",
  "system_voice",
];

export function normalizeVoiceType(v: string | undefined): VoiceTypeId {
  return VOICE_TYPE_IDS.includes(v as VoiceTypeId)
    ? (v as VoiceTypeId)
    : "character_dialogue";
}

/** 特殊说话人强规则 → 声音类型（与后端 audio_intent.infer_voice_type 对齐）。 */
export function inferVoiceType(speaker: string): VoiceTypeId {
  const spk = (speaker || "").trim();
  if (NARRATOR_SPEAKERS.some((n) => spk.includes(n))) return "narration";
  if (SYSTEM_SPEAKERS.some((s) => spk.includes(s))) return "system_voice";
  return "character_dialogue";
}

/** 净化角色名 → 稳定语义 voice_id（与后端 audio_intent.default_voice_id 对齐）。 */
export function defaultVoiceId(speaker: string, voiceType: VoiceTypeId): string {
  if (voiceType === "narration") return "voice_narrator";
  if (voiceType === "system_voice") return "voice_system";
  const spk = (speaker || "").trim();
  if (!spk) return "voice_unknown";
  const clean = spk.replace(
    /[\s（）()「」『』【】[\]《》"'“”‘’：:，,。.、;；？！?！…—-]+/g,
    "",
  );
  return `voice_${clean}`;
}

/**
 * 解析 voice_id：旁白/系统固定 ID（含「电子」变体）→ Voice Cast 显式 entries →
 * 规则兜底。与后端 VoiceCast.resolve 对齐。
 */
export function resolveVoiceId(
  speaker: string,
  voiceType: VoiceTypeId,
  delivery: string,
  cast: VoiceCastConfig,
): string {
  const spk = (speaker || "").trim();
  if (voiceType === "narration") {
    return cast.narratorVoice || "voice_narrator";
  }
  if (voiceType === "system_voice") {
    if ((delivery || "").includes("电子")) {
      return cast.systemElectronic || "voice_system_electronic";
    }
    return cast.systemVoice || "voice_system";
  }
  const explicit = cast.entries[spk];
  if (explicit) return explicit;
  return defaultVoiceId(spk, voiceType);
}

/** 定位 plan 中某镜的 dialogue 行数组；无 plan/场景/镜头 → null。 */
export function findPlanShotDialogue(
  plan: ProductionPlanJson | undefined,
  sceneId: string,
  planShotId: string,
): NonNullable<ProductionPlanJson["scenes"][number]["shots"][number]["dialogue"]> | null {
  if (!plan) return null;
  const sc = plan.scenes.find((s) => s.scene_id === sceneId);
  if (!sc) return null;
  const sh = sc.shots.find((s) => s.shot_id === planShotId);
  if (!sh) return null;
  return sh.dialogue || null;
}

/**
 * 取某镜的配音行视图（面板展示）。
 * 类型解析优先级（与后端 build_audio_intent 一致）：
 *   ① 特殊说话人名强规则（旁白/系统）→ ② 行级显式 type → ③ character_dialogue。
 * voice_id 优先级：行级显式 → 全局 Voice Cast → 规则兜底。
 */
export function planDialogueForShot(
  plan: ProductionPlanJson | undefined,
  sceneId: string,
  planShotId: string,
  cast: VoiceCastConfig,
): DialogueLineView[] {
  const rows = findPlanShotDialogue(plan, sceneId, planShotId);
  if (!rows) return [];
  return rows.map((d, i) => {
    const speaker = d.speaker || "";
    const delivery = d.delivery || "";
    let type = inferVoiceType(speaker);
    if (type === "character_dialogue") {
      type = normalizeVoiceType(d.type);
    }
    const voiceId = (d.voice_id && d.voice_id.trim())
      ? d.voice_id.trim()
      : resolveVoiceId(speaker, type, delivery, cast);
    return {
      index: i + 1,
      speaker,
      text: d.text || "",
      type,
      voiceId,
      emotion: d.emotion || "",
      delivery,
    };
  });
}

/** 构建 POST /tts/synthesize 的 lines payload（voice_id 已全部解析）。 */
export function buildTtsLinesPayload(
  plan: ProductionPlanJson | undefined,
  sceneId: string,
  planShotId: string,
  cast: VoiceCastConfig,
): TtsLinePayload[] {
  return planDialogueForShot(plan, sceneId, planShotId, cast).map((v) => ({
    text: v.text,
    speaker: v.speaker,
    voice_type: v.type,
    voice_id: v.voiceId,
    emotion: v.emotion,
    delivery: v.delivery,
  }));
}

/** 全 plan 涉及的角色名（去重，保序）——Voice Cast 面板建角色行用。 */
export function collectSpeakers(plan: ProductionPlanJson | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  if (!plan) return out;
  for (const sc of plan.scenes) {
    for (const sh of sc.shots) {
      for (const d of sh.dialogue || []) {
        const spk = (d.speaker || "").trim();
        if (spk && !seen.has(spk)) {
          seen.add(spk);
          out.push(spk);
        }
      }
    }
  }
  return out;
}

/** 判断一行是否有「可配音」文本（非空文本才进 TTS）。 */
export function hasTtsText(d: { text?: string }): boolean {
  return Boolean((d.text || "").trim());
}

// ---------- 音色下拉选项（Voice Cast 面板 + 镜头对白行级微调共用）----------

export interface VoiceOption {
  value: string;
  label: string;
}

/** 语义 ID 固定候选（与后端 tts_engine._DEFAULT_EDGE_MAP 对齐）。 */
export const SEMANTIC_VOICE_OPTIONS: VoiceOption[] = [
  { value: "voice_narrator", label: "旁白（云扬·沉稳男声）" },
  { value: "voice_system", label: "系统音（云希·少年）" },
  { value: "voice_system_electronic", label: "系统音·电子味（云希·pitch 上调）" },
];

/** 引擎原生音色（GET /tts/voices 返回）→ 选项。 */
export function nativeVoiceOptions(voices: TtsVoiceInfo[]): VoiceOption[] {
  return voices.map((v) => ({ value: v.voice_id, label: v.name || v.voice_id }));
}

/** 角色行下拉选项：「自动（规则推断）」+ 语义 + 引擎原生。 */
export function buildCharacterVoiceOptions(voices: TtsVoiceInfo[]): VoiceOption[] {
  return [
    { value: "", label: "⚙ 自动（规则推断）" },
    ...SEMANTIC_VOICE_OPTIONS,
    ...nativeVoiceOptions(voices),
  ];
}

/** 固定音色行（旁白/系统/系统电子）下拉选项：语义 + 引擎原生（无「自动」）。 */
export function buildFixedVoiceOptions(voices: TtsVoiceInfo[]): VoiceOption[] {
  return [...SEMANTIC_VOICE_OPTIONS, ...nativeVoiceOptions(voices)];
}

/** 当前值不在候选列表时（自定义 voice_id），补一项避免下拉失焦。 */
export function withCustomVoiceOption(options: VoiceOption[], value: string): VoiceOption[] {
  if (!value) return options;
  return options.some((o) => o.value === value)
    ? options
    : [...options, { value, label: `${value}（自定义）` }];
}
