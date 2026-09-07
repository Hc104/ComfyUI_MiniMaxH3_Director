#!/usr/bin/env python3
"""声音导演层 AudioIntent 数据模型单元测试（纯规则，mock，不碰网络/GPU）。

覆盖（用户 2026-08-15 拍板「先设计 AudioIntent 数据模型」）：
1. Dialogue 扩展（type/voice_id/emotion/delivery）向后兼容：旧 dict → from_dict 自动补
   character_dialogue；非法 type 回退；显式值保留。
2. VoiceType.normalize 约束；infer_voice_type 特殊说话人识别（旁白/系统/角色）。
3. default_voice_id 稳定 ID（voice_角色名 / voice_narrator / voice_system / voice_unknown）。
4. VoiceCast 映射：显式 entries 优先 / 旁白系统固定 / 系统电子味变体；round-trip。
5. build_audio_intent 全链路：多说话人 + 特殊名 + 显式 type + 显式 voice_id +
   空文本跳过 + voice_cast 只收角色对白 + ambient/music 从 DirectorIntent.audio 透传。
6. AudioIntent round-trip（含 start_sec/end_sec None）。
7. script_analyzer._clean_dialogues 透传声音字段（Qwen 识别语义不丢失）。
8. ⛔ 纯规则零 LLM 零显存：audio_intent.py 源码无 torch/ollama/requests/unload。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_audio_intent.py
"""

from __future__ import annotations

import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.audio_intent import (  # noqa: E402
    AudioIntent,
    VoiceCast,
    VoiceLine,
    build_audio_intent,
    default_voice_id,
    infer_voice_type,
)
from director.production_plan import (  # noqa: E402
    Dialogue,
    Scene,
    Shot,
    VoiceType,
)
from director import script_analyzer  # noqa: E402

PASSED = 0
FAILED = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name} {detail}")


def _scene(weather: str = "雨", scene_id: str = "scene_01") -> Scene:
    return Scene(scene_id=scene_id, weather=weather)


def _shot(dialogues: list, emotion: str = "", shot_id: str = "shot_01") -> Shot:
    return Shot(shot_id=shot_id, emotion=emotion, dialogue=dialogues)


def test_dialogue_extension_backward_compat() -> None:
    print("Dialogue 扩展向后兼容：")
    d = Dialogue.from_dict({"speaker": "陈默", "text": "这么大的雨，赶路辛苦了。"})
    _check("旧 dict → type 默认 character_dialogue",
           d.type == VoiceType.CHARACTER_DIALOGUE, d.type)
    _check("旧 dict → voice_id/emotion/delivery 空",
           d.voice_id == "" and d.emotion == "" and d.delivery == "", repr(d))
    _check("speaker/text 保留", d.speaker == "陈默" and d.text == "这么大的雨，赶路辛苦了。", repr(d))
    d2 = Dialogue.from_dict({"speaker": "系统", "text": "x", "type": "system_voice"})
    _check("显式 type=system_voice 保留", d2.type == VoiceType.SYSTEM_VOICE, d2.type)
    d3 = Dialogue.from_dict({"speaker": "柳", "text": "x", "type": "bogus"})
    _check("非法 type → character_dialogue", d3.type == VoiceType.CHARACTER_DIALOGUE, d3.type)
    d4 = Dialogue.from_dict(None)
    _check("非 dict → 空 Dialogue", d4.speaker == "" and d4.text == "" and d4.type == VoiceType.CHARACTER_DIALOGUE)
    keys = set(Dialogue(speaker="林雪", text="谁？").to_dict().keys())
    _check("to_dict 6 键", keys == {"speaker", "text", "type", "voice_id", "emotion", "delivery"}, str(keys))


def test_voice_type_normalize() -> None:
    print("VoiceType.normalize：")
    for raw, want in [("narration", "narration"), ("inner_monologue", "inner_monologue"),
                      ("system_voice", "system_voice"), ("character_dialogue", "character_dialogue")]:
        _check(f"normalize({raw})", VoiceType.normalize(raw) == want, VoiceType.normalize(raw))
    for bad in ("", None, "bogus", 123):
        _check(f"normalize({bad!r}) → character_dialogue",
               VoiceType.normalize(bad) == VoiceType.CHARACTER_DIALOGUE, VoiceType.normalize(bad))
    _check("ALL 四类齐全", set(VoiceType.ALL) == {
        "character_dialogue", "narration", "inner_monologue", "system_voice"}, str(VoiceType.ALL))


def test_infer_voice_type() -> None:
    print("infer_voice_type：")
    for spk, want in [("旁白", VoiceType.NARRATION), ("画外音", VoiceType.NARRATION),
                      ("系统", VoiceType.SYSTEM_VOICE), ("AI", VoiceType.SYSTEM_VOICE),
                      ("柳如烟", VoiceType.CHARACTER_DIALOGUE), ("", VoiceType.CHARACTER_DIALOGUE)]:
        got = infer_voice_type(spk)
        _check(f"speaker={spk!r} → {want}", got == want, got)
    _check("旁白不误判角色", infer_voice_type("旁白音") == VoiceType.NARRATION)
    _check("系统不误判角色", infer_voice_type("提示音") == VoiceType.SYSTEM_VOICE)


def test_default_voice_id() -> None:
    print("default_voice_id：")
    _check("普通角色 → voice_角色名", default_voice_id("柳如烟", VoiceType.CHARACTER_DIALOGUE) == "voice_柳如烟")
    _check("净化标点", default_voice_id("林 薇 薇", VoiceType.CHARACTER_DIALOGUE) == "voice_林薇薇")
    _check("空 speaker → voice_unknown", default_voice_id("", VoiceType.CHARACTER_DIALOGUE) == "voice_unknown")
    _check("旁白 → voice_narrator", default_voice_id("旁白", VoiceType.NARRATION) == "voice_narrator")
    _check("系统 → voice_system", default_voice_id("系统", VoiceType.SYSTEM_VOICE) == "voice_system")


def test_voice_cast_resolve() -> None:
    print("VoiceCast.resolve：")
    vc = VoiceCast(entries={"柳如烟": "voice_liu_ruyan"})
    _check("显式 entries 优先", vc.resolve("柳如烟", VoiceType.CHARACTER_DIALOGUE) == "voice_liu_ruyan")
    _check("无 entries → 规则兜底", vc.resolve("林薇薇", VoiceType.CHARACTER_DIALOGUE) == "voice_林薇薇")
    _check("旁白固定", vc.resolve("旁白", VoiceType.NARRATION) == "voice_narrator")
    _check("系统固定", vc.resolve("系统", VoiceType.SYSTEM_VOICE) == "voice_system")
    _check("系统电子味", vc.resolve("系统", VoiceType.SYSTEM_VOICE, "电子音") == "voice_system_electronic")
    _check("系统普通 delivery 不误判电子", vc.resolve("系统", VoiceType.SYSTEM_VOICE, "冰冷") == "voice_system")
    vc2 = VoiceCast()
    _check("空 cast 兜底 narrator", vc2.resolve("旁白", VoiceType.NARRATION) == "voice_narrator")


def test_voice_cast_roundtrip() -> None:
    print("VoiceCast round-trip：")
    vc = VoiceCast(entries={"柳如烟": "voice_liu_ruyan", "顾琰宸": "voice_gu_yanchen"})
    vc2 = VoiceCast.from_dict(vc.to_dict())
    _check("entries 保留", vc2.entries == vc.entries, str(vc2.entries))
    _check("固定 ID 保留", vc2.narrator_voice == "voice_narrator" and vc2.system_voice == "voice_system"
           and vc2.system_electronic == "voice_system_electronic")
    vc3 = VoiceCast.from_dict(None)
    _check("非法 → 空 VoiceCast", vc3.entries == {} and vc3.narrator_voice == "voice_narrator")


def test_build_audio_intent_basic() -> None:
    print("build_audio_intent 全链路：")
    shot = _shot(
        [
            Dialogue(speaker="柳如烟", text="客官，落座歇脚，茶先暖着。"),
            Dialogue(speaker="系统", text="【吐槽值+50！】", delivery="电子音"),
            Dialogue(speaker="旁白", text="林晓感觉胸口那股郁结之气……"),
            Dialogue(speaker="林薇薇", text="完了。穿越了。", type=VoiceType.INNER_MONOLOGUE),
            Dialogue(speaker="沈青崖", text="呵。", voice_id="voice_shen_qingya",
                     emotion="冷", delivery="低语"),
            Dialogue(speaker="路人", text="   "),  # 空文本跳过
        ],
        emotion="紧张",
    )
    intent = build_audio_intent(shot, _scene())
    _check("5 行有效台词", len(intent.lines) == 5, str(len(intent.lines)))
    ln0 = intent.lines[0]
    _check("柳如烟 → character_dialogue", ln0.voice_type == VoiceType.CHARACTER_DIALOGUE, ln0.voice_type)
    _check("柳如烟 → voice_柳如烟", ln0.voice_id == "voice_柳如烟", ln0.voice_id)
    _check("柳如烟 shot/scene 归属", ln0.shot_id == "shot_01" and ln0.scene_id == "scene_01")
    ln1 = intent.lines[1]
    _check("系统特殊名 → system_voice", ln1.voice_type == VoiceType.SYSTEM_VOICE, ln1.voice_type)
    _check("系统电子味 → voice_system_electronic", ln1.voice_id == "voice_system_electronic", ln1.voice_id)
    ln2 = intent.lines[2]
    _check("旁白 → narration", ln2.voice_type == VoiceType.NARRATION, ln2.voice_type)
    _check("旁白 → voice_narrator", ln2.voice_id == "voice_narrator", ln2.voice_id)
    ln3 = intent.lines[3]
    _check("显式 inner_monologue 生效", ln3.voice_type == VoiceType.INNER_MONOLOGUE, ln3.voice_type)
    _check("inner_monologue voice_id 规则兜底", ln3.voice_id == "voice_林薇薇", ln3.voice_id)
    ln4 = intent.lines[4]
    _check("显式 voice_id 优先", ln4.voice_id == "voice_shen_qingya", ln4.voice_id)
    _check("emotion/delivery 透传", ln4.emotion == "冷" and ln4.delivery == "低语", repr(ln4))
    _check("voice_cast 只收角色对白", intent.voice_cast == {
        "柳如烟": "voice_柳如烟", "沈青崖": "voice_shen_qingya"}, str(intent.voice_cast))
    _check("ambient 透传（雨）", intent.ambient == "雨声淅沥", intent.ambient)
    _check("music 透传（紧张）", "紧张" in intent.music, intent.music)
    _check("has_voice True", intent.has_voice())


def test_build_audio_intent_no_dialogue() -> None:
    print("build_audio_intent 无对白：")
    intent = build_audio_intent(_shot([], emotion="悲伤"), _scene(weather="雪"))
    _check("无对白 → 空 lines", intent.lines == [], str(intent.lines))
    _check("无对白 → has_voice False", not intent.has_voice())
    _check("ambient 仍透传（雪）", intent.ambient == "风雪声", intent.ambient)
    _check("music 仍透传（悲伤→紧张氛围）", intent.music == "紧张氛围配乐渐起", intent.music)


def test_build_audio_intent_cast_override() -> None:
    print("build_audio_intent 显式 VoiceCast：")
    vc = VoiceCast(entries={"柳如烟": "voice_lin_xiao"})
    shot = _shot([Dialogue(speaker="柳如烟", text="客官，落座歇脚。")])
    intent = build_audio_intent(shot, _scene(), cast=vc)
    _check("显式 cast voice_id 生效", intent.lines[0].voice_id == "voice_lin_xiao", intent.lines[0].voice_id)
    _check("voice_cast 反映映射", intent.voice_cast == {"柳如烟": "voice_lin_xiao"}, str(intent.voice_cast))


def test_audio_intent_roundtrip() -> None:
    print("AudioIntent round-trip：")
    shot = _shot([
        Dialogue(speaker="柳如烟", text="客官。", type=VoiceType.CHARACTER_DIALOGUE, emotion="温和"),
        Dialogue(speaker="系统", text="【+50】", delivery="电子音"),
    ])
    intent = build_audio_intent(shot, _scene())
    intent.lines[0].start_sec = 0.5
    intent.lines[0].end_sec = 2.0
    intent2 = AudioIntent.from_dict(intent.to_dict())
    _check("shot/scene 保留", intent2.shot_id == "shot_01" and intent2.scene_id == "scene_01")
    _check("lines 数量保留", len(intent2.lines) == len(intent.lines) == 2)
    _check("voice_type 保留", intent2.lines[0].voice_type == VoiceType.CHARACTER_DIALOGUE)
    _check("voice_id 保留", intent2.lines[0].voice_id == intent.lines[0].voice_id)
    _check("emotion/delivery 保留", intent2.lines[0].emotion == "温和"
           and intent2.lines[1].delivery == "电子音")
    _check("start/end_sec 保留", intent2.lines[0].start_sec == 0.5 and intent2.lines[0].end_sec == 2.0)
    _check("未填 start_sec 为 None", intent2.lines[1].start_sec is None)
    _check("voice_cast 保留", intent2.voice_cast == intent.voice_cast)
    _check("ambient/music 保留", intent2.ambient == intent.ambient and intent2.music == intent.music)
    empty = AudioIntent.from_dict(None)
    _check("非法 → 空 AudioIntent", empty.lines == [] and not empty.has_voice())


def test_clean_dialogues_pass_through() -> None:
    print("script_analyzer._clean_dialogues 透传：")
    out = script_analyzer._clean_dialogues([
        {"speaker": "林薇薇", "text": "完了。穿越了。", "type": "inner_monologue"},
        {"speaker": "系统", "text": "【吐槽值+50！】", "delivery": "电子音"},
        {"speaker": "陈默", "text": "这么大的雨，赶路辛苦了。"},  # 无声音字段
        {"speaker": "路人", "text": "hi", "type": "bogus"},  # 非法 type → character_dialogue
    ])
    _check("4 行全部保留（非空文本）", len(out) == 4, str(len(out)))
    _check("显式 inner_monologue 透传", out[0]["type"] == "inner_monologue", str(out[0]))
    _check("delivery 透传", out[1]["delivery"] == "电子音", str(out[1]))
    _check("无字段不凭空补", "type" not in out[2] and "voice_id" not in out[2], str(out[2]))
    _check("非法 type 归一", out[3]["type"] == "character_dialogue", str(out[3]))
    out2 = script_analyzer._clean_dialogues(["台词而已", 123])
    _check("list-of-str 兼容", len(out2) == 1 and out2[0]["text"] == "台词而已", str(out2))


def _has_import(src: str, bad: str) -> bool:
    """源码是否 import 了 bad 库（只匹配行首 import/from 语句，docstring 提到不算）。"""
    return re.search(rf"(?m)^\s*(?:import|from)\s+{re.escape(bad)}\b", src) is not None


def test_no_llm_gpu_side_effects() -> None:
    print("⛔ 纯规则零 LLM 零显存：")
    src = open(os.path.join(os.path.dirname(__file__), "..", "audio_intent.py"),
               encoding="utf-8").read()
    for bad in ("torch", "ollama", "requests", "urlopen", "unload", "empty_cache", "keep_alive"):
        _check(f"audio_intent.py 无 {bad} import",
               not _has_import(src, bad), _has_import(src, bad))
    src_pp = open(os.path.join(os.path.dirname(__file__), "..", "production_plan.py"),
                  encoding="utf-8").read()
    for bad in ("torch", "ollama", "requests", "urlopen"):
        _check(f"production_plan.py 无 {bad} import",
               not _has_import(src_pp, bad), _has_import(src_pp, bad))


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_audio_intent\n")
    test_dialogue_extension_backward_compat()
    test_voice_type_normalize()
    test_infer_voice_type()
    test_default_voice_id()
    test_voice_cast_resolve()
    test_voice_cast_roundtrip()
    test_build_audio_intent_basic()
    test_build_audio_intent_no_dialogue()
    test_build_audio_intent_cast_override()
    test_audio_intent_roundtrip()
    test_clean_dialogues_pass_through()
    test_no_llm_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
