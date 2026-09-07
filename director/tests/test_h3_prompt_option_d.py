#!/usr/bin/env python3
"""#545 根因修复 D（2026-08-15 用户拍板）回归测试。

对白位置最小改动：h3_prompt_builder 对白只进 integrated_multimodal_description
的 <d>[Language] 原文</d> 块（稳定说话人 ID (S1)/(S2)），绝不进 overall_soundscape。
覆盖：
1. 对白只进描述区 <d> 块（soundscape/music 零对白）；
2. 多说话人稳定 ID（S1/S2/S3，同说话人复用同 ID）；
3. 【】系统音引号（吐槽值+50！）随已知对白剥离；
4. 任何回显原文事实的字段（lighting/env/composition/style）不得泄漏对白正文
   （director_intent._lighting_from 会把含「冷」的对话事实整体当 lighting）；
5. 非对白引号（「山雨楼」强调词）不剥离（防误伤）；
6. 无对白镜头严格向后兼容（渲染与修复前一致：无 <d> 块、soundscape 兜底）。

直接用系统 python 运行（纯规则零 LLM）：
    python3 director/tests/test_h3_prompt_option_d.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import director_intent as di  # noqa: E402
from director import h3_prompt_builder as h3  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
)

SHOT1_SRC = ("雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，"
             "湿漉漉的石阶映着暖光，门前老槐树滴着水珠。")
SHOT4_SRC = ("柳如烟放下茶碗，从柜台后绕过，端着一碗热茶走向堂中，语气平淡："
             "「客官，落座歇脚，茶先暖着。」")
SHOT_C_SRC = ("林薇薇挑眉：「就这？」沈青崖冷笑：「呵。」系统音提示：【吐槽值+50！】")

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


def _plan() -> ProductionPlan:
    return ProductionPlan(
        project=ProjectInfo(title="Option D", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="教室", location_name="教室",
            time="白天", weather="",
            shots=[
                Shot(shot_id="shot_a", source_text=SHOT1_SRC, duration_sec=5,
                     entities=[Entity(name="山雨楼", type=EntityType.ARCHITECTURE,
                                      source=EntitySource.SCRIPT, confidence=0.95)]),
                Shot(shot_id="shot_b", source_text=SHOT4_SRC, duration_sec=5,
                     characters=[Character(name="柳如烟")],
                     dialogue=[Dialogue(speaker="柳如烟", text="客官，落座歇脚，茶先暖着。")]),
                Shot(shot_id="shot_c", source_text=SHOT_C_SRC, duration_sec=5,
                     characters=[Character(name="林薇薇"), Character(name="沈青崖")],
                     dialogue=[
                         Dialogue(speaker="林薇薇", text="就这？"),
                         Dialogue(speaker="沈青崖", text="呵。"),
                         Dialogue(speaker="系统", text="吐槽值+50！"),
                     ]),
            ],
        )],
        validation=Validation(),
    )


def _intents():
    return di.build_plan_intents(_plan())


def test_dialogue_only_in_d_block() -> None:
    print("\n[1] 对白只进描述区 <d> 块（soundscape/music 零对白）")
    out = h3.build_h3_prompt(_intents()["scene_01:shot_b"])
    desc, sound, mus = (out.integrated_multimodal_description,
                        out.overall_soundscape, out.non_diegetic_music)
    _check("<d> 块原文逐字", "<d>[Chinese] 客官，落座歇脚，茶先暖着。</d>" in desc, desc)
    _check("说话人 ID", "(S1)柳如烟说，" in desc, desc)
    _check("#608 <d> 块后定点仅声音说明",
           "</d>" + h3._DIALOGUE_AUDIO_ONLY_NOTE in desc, desc[-80:])
    _check("#608 尾部双语无字幕约束", h3.NO_SUBTITLE_RULE in desc, desc[-80:])
    _check("soundscape 零对白", "客官" not in sound and "<d>" not in sound, sound)
    _check("music 零对白", "客官" not in mus and "<d>" not in mus, mus)
    _check("描述正文无引号段", "「客官，落座歇脚，茶先暖着。」" not in desc, desc)
    _check("对白 provenance 在描述区",
           any("对白=" in s for s in out.provenance["integrated_multimodal_description"]["user_facts"]))


def test_multi_speaker_stable_ids() -> None:
    print("\n[2] 多说话人稳定 ID（S1/S2/S3）")
    out = h3.build_h3_prompt(_intents()["scene_01:shot_c"])
    desc = out.integrated_multimodal_description
    for sid, who, txt in [("(S1)", "林薇薇", "就这？"),
                          ("(S2)", "沈青崖", "呵。"),
                          ("(S3)", "系统", "吐槽值+50！")]:
        _check(f"{sid}{who} 块", f"{sid}{who}说，<d>[Chinese] {txt}</d>" in desc, desc)
    _check("同说话人 ID 唯一",
           desc.count("(S1)") == 1 and desc.count("(S2)") == 1 and desc.count("(S3)") == 1,
           desc)
    _check("对白正文仅 <d> 一处",
           desc.count("就这？") == 1 and desc.count("呵。") == 1 and desc.count("吐槽值+50！") == 1,
           desc)


def test_system_voice_brackets_stripped() -> None:
    print("\n[3] 【】系统音引号随已知对白剥离（仅匹配对白，强调词保留）")
    out = h3.build_h3_prompt(_intents()["scene_01:shot_c"])
    desc = out.integrated_multimodal_description
    body = desc.split("(S1)")[0]  # <d> 块之前 = 描述正文
    _check("描述正文无【吐槽值+50！】", "【吐槽值+50！】" not in body, body)
    _check("系统音动作保留", "系统音提示" in body, body)
    # 非对白强调引号（山雨楼）不得剥离
    out2 = h3.build_h3_prompt(_intents()["scene_01:shot_a"])
    _check("非对白「山雨楼」保留", "「山雨楼」" in out2.integrated_multimodal_description,
           out2.integrated_multimodal_description[:80])


def test_lighting_field_no_dialogue_leak() -> None:
    print("\n[4] lighting 字段（回显原文事实）不泄漏对白正文")
    # 复现 director_intent._lighting_from：含「冷」的对白事实被整体当 lighting
    intent = _intents()["scene_01:shot_c"]
    out = h3.build_h3_prompt(intent)
    desc = out.integrated_multimodal_description
    light = str(intent.lighting or "")
    _check("lighting 确为对白事实（复现前提）", "「" in light or "【" in light, light)
    if "光线：" in desc:
        light_line = desc.split("光线：")[1].split("氛围：")[0] if "氛围：" in desc else desc.split("光线：")[1]
        _check("光线行零对白正文",
               all(t not in light_line for t in ["就这", "呵。", "吐槽值+50", "「", "【"]),
               light_line)
    else:
        _check("光线行零对白正文（无光线行）", True)


def test_no_dialogue_backward_compat() -> None:
    print("\n[5] 无对白镜头严格向后兼容")
    out = h3.build_h3_prompt(_intents()["scene_01:shot_a"])
    desc, sound = out.integrated_multimodal_description, out.overall_soundscape
    _check("无 <d> 块", "<d>" not in desc, desc)
    _check("原文逐字保留（含山雨楼引号）", "「山雨楼」" in desc, desc)
    _check("soundscape 兜底环境音", sound.strip().endswith("环境音。") or "环境音" in sound, sound)


def test_fragment_strip_helper() -> None:
    print("\n[6] _strip_dialogue_quotes 各片段统一剥离（comp/env/style）")
    dlg = ["就这？", "呵。"]
    _check("「」匹配剥离", h3._strip_dialogue_quotes("林薇薇挑眉：「就这？」", dlg) == "林薇薇挑眉")
    _check("【】匹配剥离",
           h3._strip_dialogue_quotes("系统：【吐槽值+50！】", ["吐槽值+50！"]) == "系统")
    _check("非对白保留", h3._strip_dialogue_quotes("「山雨楼」檐角", dlg) == "「山雨楼」檐角")
    _check("孤冒号清理", "：" not in h3._strip_dialogue_quotes("林薇薇挑眉：「就这？」", dlg))
    _check("无对白列表原样", h3._strip_dialogue_quotes("林薇薇挑眉：「就这？」", []) == "林薇薇挑眉：「就这？」")


def main() -> None:
    test_dialogue_only_in_d_block()
    test_multi_speaker_stable_ids()
    test_system_voice_brackets_stripped()
    test_lighting_field_no_dialogue_leak()
    test_no_dialogue_backward_compat()
    test_fragment_strip_helper()
    print(f"\n合计：{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
