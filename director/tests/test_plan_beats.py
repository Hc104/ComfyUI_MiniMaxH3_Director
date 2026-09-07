#!/usr/bin/env python3
"""P1-B-1 · StoryBeat / ProductionPlan.beats 序列化单元测试。

覆盖（P1B_STORY_ANALYZER_PLAN.md §3.1/§3.2，用户 2026-08-14 拍板）：
1. StoryBeat to_dict/from_dict 往返（全部字段，含 text_segments / entries / dramatic_function）；
2. ProductionPlan.beats 往返（to_dict 带 "beats"；from_dict 恢复）；
3. 老项目兼容：旧数据（无 beats 键 / beats 缺省）→ 严格空列表，不破坏既有字段；
4. text_segments 逐字保留（覆盖校验的源，P1B §6.5）；
5. from_dict 防御：beats 里非 dict / 非 list / 缺字段 → 不崩，默认值兜底；
6. dramatic_function 校验（story_analyzer.validate_dramatic_function）：
   9 个合法值通过；未知值 → ("transition", False)。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_plan_beats.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import (  # noqa: E402
    ProductionPlan,
    ProjectInfo,
    StoryBeat,
    Validation,
)
from director.story_analyzer import (  # noqa: E402
    DRAMATIC_FUNCTIONS,
    validate_dramatic_function,
)

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


def _sample_beat(beat_id: str = "beat_01") -> StoryBeat:
    return StoryBeat(
        beat_id=beat_id,
        title="沈青崖进入山雨楼",
        summary="雨夜，年轻剑客踏进客栈，环视堂内。",
        dramatic_function="introduce_character",
        scene_id="山雨楼大堂",
        time="黄昏",
        weather="雨",
        text_segments=["雨刚停，山雾从林间漫向山雨楼。", "门前老槐树滴着水珠。"],
        order=1,
        entries=["scene_01:shot_01", "scene_01:shot_02"],
        source="ai",
        transition_reason="新地点建立，人物登场",
    )


# ---------------- 1. StoryBeat 序列化 ----------------

def test_storybeat_roundtrip() -> None:
    b = _sample_beat()
    d = b.to_dict()
    _check("to_dict 含全部 12 字段",
           set(d.keys()) == {
               "beat_id", "title", "summary", "dramatic_function", "scene_id",
               "time", "weather", "text_segments", "order", "entries",
               "source", "transition_reason",
           }, str(sorted(d.keys())))
    b2 = StoryBeat.from_dict(d)
    _check("beat_id 往返", b2.beat_id == "beat_01")
    _check("dramatic_function 往返", b2.dramatic_function == "introduce_character")
    _check("scene_id 往返", b2.scene_id == "山雨楼大堂")
    _check("text_segments 逐字往返", b2.text_segments == b.text_segments,
           str(b2.text_segments))
    _check("entries 往返", b2.entries == ["scene_01:shot_01", "scene_01:shot_02"])
    _check("source 往返", b2.source == "ai")
    _check("transition_reason 往返", b2.transition_reason == b.transition_reason)


def test_storybeat_defaults() -> None:
    b = StoryBeat()
    d = b.to_dict()
    _check("默认 beat_id 空", d["beat_id"] == "")
    _check("默认 text_segments 空列表", d["text_segments"] == [])
    _check("默认 source 为 ai", d["source"] == "ai")
    _check("默认 order 0", d["order"] == 0)
    b2 = StoryBeat.from_dict(d)
    _check("默认往返不崩", b2.beat_id == "" and b2.text_segments == [])


def test_storybeat_from_dict_defensive() -> None:
    _check("非 dict → 默认", StoryBeat.from_dict("x").beat_id == "")
    _check("None → 默认", StoryBeat.from_dict(None).beat_id == "")
    _check("缺字段 → 默认", StoryBeat.from_dict({}).text_segments == [])
    _check("text_segments 非 list → 空",
           StoryBeat.from_dict({"text_segments": "oops"}).text_segments == [])
    _check("entries 非 list → 空",
           StoryBeat.from_dict({"entries": 42}).entries == [])
    _check("order 非法 → 0",
           StoryBeat.from_dict({"order": "abc"}).order == 0)
    _check("source 缺省 → ai",
           StoryBeat.from_dict({"beat_id": "b1"}).source == "ai")


# ---------------- 2. ProductionPlan.beats ----------------

def test_plan_beats_roundtrip() -> None:
    plan = ProductionPlan(
        project=ProjectInfo(title="山雨客栈·第一章", source_file="chapter1.txt"),
        validation=Validation(status="pending"),
        timeline=["scene_01:shot_01"],
        beats=[_sample_beat("beat_01"), _sample_beat("beat_02")],
    )
    d = plan.to_dict()
    _check("to_dict 带 beats 键", "beats" in d)
    _check("beats 数量 2", len(d["beats"]) == 2)
    _check("timeline 保留", d["timeline"] == ["scene_01:shot_01"])
    plan2 = ProductionPlan.from_dict(d)
    _check("beats 恢复 2", len(plan2.beats) == 2)
    _check("beat 字段恢复", plan2.beats[1].dramatic_function == "introduce_character")
    _check("text_segments 逐字恢复",
           plan2.beats[0].text_segments == ["雨刚停，山雾从林间漫向山雨楼。", "门前老槐树滴着水珠。"])
    _check("project.title 保留", plan2.project.title == "山雨客栈·第一章")


def test_plan_beats_old_data_compat() -> None:
    """老项目数据（无 beats 键）→ beats 严格空列表，其余字段不破坏。"""
    old = {
        "project": {"title": "山雨客栈", "source_file": "山雨客栈.md"},
        "scenes": [],
        "validation": {"status": "valid", "errors": [], "warnings": []},
        "timeline": [],
        # 无 "beats" 键（老版本 ProductionPlan.to_dict 输出）
    }
    plan = ProductionPlan.from_dict(old)
    _check("老数据 beats 为空列表", plan.beats == [])
    _check("老数据 project 保留", plan.project.title == "山雨客栈")
    _check("老数据 validation 保留", plan.validation.status == "valid")

    old2 = dict(old)
    old2["beats"] = None  # 显式 None（部分老实现）
    plan2 = ProductionPlan.from_dict(old2)
    _check("beats=None → 空列表", plan2.beats == [])


def test_plan_beats_from_dict_defensive() -> None:
    d = {
        "project": {"title": "t"},
        "scenes": [],
        "validation": {},
        "timeline": [],
        "beats": ["not-a-dict", None, {}],
    }
    plan = ProductionPlan.from_dict(d)
    _check("beats 非 dict 项不崩", len(plan.beats) == 3)
    _check("非法项走默认", plan.beats[0].beat_id == "" and plan.beats[0].source == "ai")
    _check("空 dict 项走默认", plan.beats[2].text_segments == [])


# ---------------- 3. dramatic_function 校验 ----------------

def test_dramatic_functions_set() -> None:
    expect = {
        "introduce_character", "dialogue", "plant_clue", "introduce_threat",
        "confrontation", "action", "reveal", "emotional", "transition",
    }
    _check("DRAMATIC_FUNCTIONS 含全部 9 个合法值", expect == set(DRAMATIC_FUNCTIONS),
           str(set(DRAMATIC_FUNCTIONS) ^ expect))


def test_validate_dramatic_function() -> None:
    ok, good = validate_dramatic_function("confrontation")
    _check("合法值 confrontation 通过", good and ok == "confrontation")
    _check("合法值 plant_clue 通过",
           validate_dramatic_function("plant_clue")[0] == "plant_clue")
    norm, bad = validate_dramatic_function("climax_unknown")
    _check("未知值 → transition", norm == "transition")
    _check("未知值标记非法", bad is False)
    _check("空值 → transition", validate_dramatic_function("")[0] == "transition")
    _check("None → transition", validate_dramatic_function(None)[0] == "transition")
    _check("空白串 → transition", validate_dramatic_function("  ")[0] == "transition")


def main() -> None:
    test_storybeat_roundtrip()
    test_storybeat_defaults()
    test_storybeat_from_dict_defensive()
    test_plan_beats_roundtrip()
    test_plan_beats_old_data_compat()
    test_plan_beats_from_dict_defensive()
    test_dramatic_functions_set()
    test_validate_dramatic_function()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
