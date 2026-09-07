/**
 * Voice Cast 前端纯逻辑单测（Phase 2，#573/#576）。
 *   - 类型推断：inferVoiceType（旁白/系统强规则）
 *   - voice_id 解析：defaultVoiceId / resolveVoiceId（行级 > 全局 > 规则）
 *   - 台词定位：findPlanShotDialogue / planDialogueForShot / buildTtsLinesPayload
 *   - 角色发现：collectSpeakers；配音判断：hasTtsText
 *   - 共享下拉选项（#576）：nativeVoiceOptions / buildCharacterVoiceOptions /
 *     buildFixedVoiceOptions / withCustomVoiceOption / SEMANTIC_VOICE_OPTIONS
 *
 * ⛔ 纯规则零 LLM 零显存：仅 import core/voiceCast 与类型，无网络/后端依赖。
 */
import { describe, it, expect } from "vitest";
import type { ProductionPlanJson } from "@/services/comfyApi";
import type { VoiceCastConfig } from "@/models/project";
import {
  inferVoiceType,
  defaultVoiceId,
  resolveVoiceId,
  findPlanShotDialogue,
  planDialogueForShot,
  buildTtsLinesPayload,
  collectSpeakers,
  hasTtsText,
  SEMANTIC_VOICE_OPTIONS,
  nativeVoiceOptions,
  buildCharacterVoiceOptions,
  buildFixedVoiceOptions,
  withCustomVoiceOption,
  type TtsLinePayload,
} from "@/core/voiceCast";

const CAST: VoiceCastConfig = {
  entries: { 柳如烟: "voice_liu_ruyan", 阿岚: "" },
  narratorVoice: "voice_narrator",
  systemVoice: "voice_system",
  systemElectronic: "voice_system_electronic",
};

function makePlan(): ProductionPlanJson {
  return {
    project: { title: "测试", source_file: "t.md" },
    validation: { status: "ok", errors: [], warnings: [] },
    scenes: [
      {
        scene_id: "scene_01",
        title: "山雨楼外",
        location_name: "山雨楼外",
        time: "夜",
        weather: "雨",
        shots: [
          {
            shot_id: "shot_01",
            source_text: "柳如烟推门。",
            duration_sec: 5,
            characters: [{ name: "柳如烟", role: "老板娘" }],
            props: [],
            actions: ["推门"],
            emotion: "冷静",
            dialogue: [
              { speaker: "柳如烟", text: "客官，里面请。" },
              { speaker: "旁白", text: "雨打青檐，一夜未歇。" },
            ],
            visual_intent: "",
          },
          {
            shot_id: "shot_02",
            source_text: "系统提示。",
            duration_sec: 4,
            characters: [],
            props: [],
            actions: [],
            emotion: "",
            dialogue: [{ speaker: "系统", text: "危险区域，请勿靠近。", delivery: "电子" }],
            visual_intent: "",
          },
        ],
      },
    ],
  };
}

describe("inferVoiceType", () => {
  it("旁白/叙述系 → narration", () => {
    expect(inferVoiceType("旁白")).toBe("narration");
    expect(inferVoiceType("画外音")).toBe("narration");
  });
  it("系统/小助手系 → system_voice", () => {
    expect(inferVoiceType("系统")).toBe("system_voice");
    expect(inferVoiceType("AI")).toBe("system_voice");
  });
  it("普通角色 → character_dialogue", () => {
    expect(inferVoiceType("柳如烟")).toBe("character_dialogue");
    expect(inferVoiceType("")).toBe("character_dialogue");
  });
});

describe("defaultVoiceId", () => {
  it("旁白/系统固定语义 ID", () => {
    expect(defaultVoiceId("旁白", "narration")).toBe("voice_narrator");
    expect(defaultVoiceId("系统", "system_voice")).toBe("voice_system");
  });
  it("角色名净化 → voice_角色名", () => {
    expect(defaultVoiceId("柳如烟", "character_dialogue")).toBe("voice_柳如烟");
    expect(defaultVoiceId("阿岚", "character_dialogue")).toBe("voice_阿岚");
  });
  it("空说话人 → voice_unknown", () => {
    expect(defaultVoiceId("", "character_dialogue")).toBe("voice_unknown");
  });
});

describe("resolveVoiceId", () => {
  it("旁白 → narratorVoice（全局）", () => {
    expect(resolveVoiceId("旁白", "narration", "", CAST)).toBe("voice_narrator");
  });
  it("系统音 → systemVoice；含电子 → systemElectronic", () => {
    expect(resolveVoiceId("系统", "system_voice", "", CAST)).toBe("voice_system");
    expect(resolveVoiceId("系统", "system_voice", "电子", CAST)).toBe("voice_system_electronic");
  });
  it("角色显式 entries 优先", () => {
    expect(resolveVoiceId("柳如烟", "character_dialogue", "", CAST)).toBe("voice_liu_ruyan");
  });
  it("空 entries 条目 → 规则兜底 voice_角色名", () => {
    expect(resolveVoiceId("阿岚", "character_dialogue", "", CAST)).toBe("voice_阿岚");
  });
});

describe("findPlanShotDialogue", () => {
  it("无 plan → null", () => {
    expect(findPlanShotDialogue(undefined, "scene_01", "shot_01")).toBeNull();
  });
  it("无该场景/镜头 → null", () => {
    expect(findPlanShotDialogue(makePlan(), "scene_99", "shot_01")).toBeNull();
    expect(findPlanShotDialogue(makePlan(), "scene_01", "shot_99")).toBeNull();
  });
  it("命中返回该镜 dialogue 行", () => {
    const rows = findPlanShotDialogue(makePlan(), "scene_01", "shot_01");
    expect(rows).toHaveLength(2);
    expect(rows![0]).toMatchObject({ speaker: "柳如烟", text: "客官，里面请。" });
  });
});

describe("planDialogueForShot", () => {
  it("类型推断：普通角色 character_dialogue，旁白 narration", () => {
    const lines = planDialogueForShot(makePlan(), "scene_01", "shot_01", CAST);
    expect(lines).toHaveLength(2);
    expect(lines[0].type).toBe("character_dialogue");
    expect(lines[0].voiceId).toBe("voice_liu_ruyan"); // 显式 entries
    expect(lines[1].type).toBe("narration");
    expect(lines[1].voiceId).toBe("voice_narrator");
  });
  it("系统 + delivery 电子 → system_electronic", () => {
    const lines = planDialogueForShot(makePlan(), "scene_01", "shot_02", CAST);
    expect(lines[0].type).toBe("system_voice");
    expect(lines[0].voiceId).toBe("voice_system_electronic");
  });
  it("行级显式 voice_id 最高优先", () => {
    const plan = makePlan();
    plan.scenes[0].shots[0].dialogue![0].voice_id = "zh-CN-YunxiNeural";
    const lines = planDialogueForShot(plan, "scene_01", "shot_01", CAST);
    expect(lines[0].voiceId).toBe("zh-CN-YunxiNeural");
  });
  it("无对白 → 空数组", () => {
    const plan = makePlan();
    // 运行时旧快照/导入的 JSON 可能缺 dialogue 字段（类型为必填数组，仅测试缺失路径）。
    plan.scenes[0].shots[0].dialogue = undefined as never;
    expect(planDialogueForShot(plan, "scene_01", "shot_01", CAST)).toHaveLength(0);
  });
});

describe("buildTtsLinesPayload", () => {
  it("输出后端可消费 lines（voice_id 全解析）", () => {
    const payload: TtsLinePayload[] = buildTtsLinesPayload(makePlan(), "scene_01", "shot_01", CAST);
    expect(payload).toHaveLength(2);
    expect(payload[0]).toMatchObject({
      text: "客官，里面请。",
      speaker: "柳如烟",
      voice_type: "character_dialogue",
      voice_id: "voice_liu_ruyan",
    });
    expect(payload[1]).toMatchObject({ voice_id: "voice_narrator" });
  });
});

describe("collectSpeakers", () => {
  it("全 plan 去重保序", () => {
    expect(collectSpeakers(makePlan())).toEqual(["柳如烟", "旁白", "系统"]);
  });
  it("无 plan → 空", () => {
    expect(collectSpeakers(undefined)).toEqual([]);
  });
});

describe("hasTtsText", () => {
  it("非空文本才有配音", () => {
    expect(hasTtsText({ text: "你好" })).toBe(true);
    expect(hasTtsText({ text: "  " })).toBe(false);
    expect(hasTtsText({})).toBe(false);
  });
});

describe("音色下拉共享选项（#576）", () => {
  const voices = [
    { voice_id: "zh-CN-XiaoxiaoNeural", name: "晓晓（女）", language: "zh-CN", gender: "Female", engine: "edge-tts" },
    { voice_id: "zh-CN-YunxiNeural", name: "云希（男）", language: "zh-CN", gender: "Male", engine: "edge-tts" },
  ];

  it("SEMANTIC_VOICE_OPTIONS 含旁白/系统/电子三语义 ID", () => {
    expect(SEMANTIC_VOICE_OPTIONS.map((o) => o.value)).toEqual([
      "voice_narrator",
      "voice_system",
      "voice_system_electronic",
    ]);
  });

  it("nativeVoiceOptions 按引擎音色映射", () => {
    expect(nativeVoiceOptions(voices)).toEqual([
      { value: "zh-CN-XiaoxiaoNeural", label: "晓晓（女）" },
      { value: "zh-CN-YunxiNeural", label: "云希（男）" },
    ]);
  });

  it("buildCharacterVoiceOptions = 自动 + 语义 + 原生", () => {
    const opts = buildCharacterVoiceOptions(voices);
    expect(opts[0]).toEqual({ value: "", label: "⚙ 自动（规则推断）" });
    expect(opts).toHaveLength(1 + 3 + 2);
    expect(opts[opts.length - 1].value).toBe("zh-CN-YunxiNeural");
  });

  it("buildFixedVoiceOptions = 语义 + 原生（无自动）", () => {
    const opts = buildFixedVoiceOptions(voices);
    expect(opts[0].value).toBe("voice_narrator");
    expect(opts).toHaveLength(3 + 2);
  });

  it("withCustomVoiceOption 自定义值补项；已有不重复", () => {
    const base = buildCharacterVoiceOptions(voices);
    const withCustom = withCustomVoiceOption(base, "voice_custom_aaa");
    expect(withCustom).toHaveLength(base.length + 1);
    expect(withCustom[withCustom.length - 1]).toEqual({ value: "voice_custom_aaa", label: "voice_custom_aaa（自定义）" });
    expect(withCustomVoiceOption(base, "voice_narrator")).toHaveLength(base.length);
    expect(withCustomVoiceOption(base, "")).toHaveLength(base.length);
  });
});
