#!/usr/bin/env python3
"""P1-B-2 · Beat → Timeline（纯规则）单元测试。

覆盖（P1B_STORY_ANALYZER_PLAN.md §5 / §7 / §4④，用户 2026-08-14 拍板 + 锁死实现细节 1）：
1. target_beat_count：推荐目标区间 clamp(round(len/700), 8, 16)，空文本 0；
2. _merge_beats_to_target 超限合并不截断：
   - len(beats) <= target → 不凑数、不硬拆（拍板①）；
   - 超限 → 优先合并 transition/低信息相邻对；同地点相邻对次之；
   - 合并后 dramatic_function 取更具体（优先级表 confrontation > dialogue > … > transition）；
   - text_segments 拼接顺序无丢失（覆盖底线不破）；合并记 warning；
   - 合并后仍超 target → 继续合并；
3. _fill_coverage_gaps 覆盖补齐：
   - 完整覆盖 → 不补、无 warning；
   - 缺尾段 → 追加到最近 Beat；缺中段 → 追加到缺口前最近 Beat；
   - 空 beats → 原样返回；
4. _normalize_location_ids 地点规范化：
   - 同名（归一后）合并同一 scene_id（location_XX）；归一去空白；
   - 不同地点不同 id；loc_map: scene_id -> 规范地名；无地点 → ""；
5. beat_timeline：Beat 顺序权威（order 升序）、条目 "{scene_id}:{beat_id}"、同地点前缀；
6. 退化路径集成：extract_beats(None) → process_beats 全链路覆盖完整 + 数量不硬凑。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_story_analyzer_timeline.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import StoryBeat  # noqa: E402
from director.story_analyzer import (  # noqa: E402
    BEAT_TARGET_MAX,
    BEAT_TARGET_MIN,
    _core,
    _dramatic_priority,
    _fill_coverage_gaps,
    _merge_beats_to_target,
    _normalize_location_ids,
    beat_timeline,
    extract_beats,
    process_beats,
    target_beat_count,
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


def _beat(bid: str, order: int, func: str, scene: str = "",
          segments: list[str] | None = None, summary: str = "") -> StoryBeat:
    return StoryBeat(
        beat_id=bid,
        title=f"节拍{bid}",
        summary=summary,
        dramatic_function=func,
        scene_id=scene,
        text_segments=segments or [f"原文{bid}"],
        order=order,
        source="ai",
    )


def _flat(beats: list[StoryBeat]) -> str:
    return "".join(_core(s) for b in beats for s in b.text_segments)


# ---------------- 1. target_beat_count ----------------

def test_target_beat_count() -> None:
    print("\n[1] target_beat_count（推荐目标区间 clamp(round(len/700), 8, 16)）")
    _check("空文本 → 0", target_beat_count("") == 0)
    _check("空白文本 → 0", target_beat_count("   \n ") == 0)
    _check("1000 字 → 8（clamp 下限）", target_beat_count("字" * 1000) == 8)
    _check("5000 字 → 8", target_beat_count("字" * 5000) == 8)
    _check("7000 字 → 10", target_beat_count("字" * 7000) == 10)
    _check("7700 字 → 11", target_beat_count("字" * 7700) == 11)
    _check("11000 字 → 16", target_beat_count("字" * 11000) == 16)
    _check("12000 字 → 16（clamp 上限）", target_beat_count("字" * 12000) == 16)
    _check("15000 字 → 16（封顶）", target_beat_count("字" * 15000) == 16)
    _check("常量 8 ≤ 16", BEAT_TARGET_MIN <= BEAT_TARGET_MAX)


# ---------------- 2. 超限合并不截断 ----------------

def test_merge_no_padding() -> None:
    print("\n[2] 超限合并：不凑数、不硬拆（拍板①）")
    beats = [_beat(f"beat_{i:02d}", i, "transition") for i in range(1, 4)]
    out = _merge_beats_to_target(beats, 8)
    _check("3 个 Beat、target=8 → 原样（不凑 8）", len(out) == 3, str(len(out)))
    _check("无合并 warning（未超限）",
           len(_merge_beats_to_target(beats, 8, [])) == 3)


def test_merge_overlimit_priority_transition() -> None:
    print("\n[3] 超限合并：优先合并 transition/低信息对")
    beats = [
        _beat("beat_01", 1, "introduce_character", scene="山雨楼外"),
        _beat("beat_02", 2, "transition", scene="山雨楼外", summary=""),
        _beat("beat_03", 3, "dialogue", scene="山雨楼大堂"),
        _beat("beat_04", 4, "confrontation", scene="山雨楼大堂"),
        _beat("beat_05", 5, "action", scene="后院"),
        _beat("beat_06", 6, "transition", scene="后院", summary=""),
        _beat("beat_07", 7, "reveal", scene="后院"),
        _beat("beat_08", 8, "emotional", scene="大堂"),
        _beat("beat_09", 9, "action", scene="大堂"),
    ]
    warnings: list[str] = []
    out = _merge_beats_to_target(beats, 8, warnings)
    _check("9 个合并到 8", len(out) == 8, str(len(out)))
    _check("text_segments 拼接无丢失（顺序保留）", _flat(out) == _flat(beats))
    _check("合并记 warning", len(warnings) >= 1, str(warnings))
    _check("warning 含 beat id", any("Beat beat_" in w for w in warnings), str(warnings))


def test_merge_dramatic_more_specific() -> None:
    print("\n[4] 合并取更具体的 dramatic_function（优先级表）")
    # dialogue(3) + confrontation(1) → confrontation
    m = _merge_beats_to_target(
        [_beat("a", 1, "dialogue", segments=["甲"]), _beat("b", 2, "confrontation", segments=["乙"])],
        1, [],
    )
    _check("dialogue+confrontation → confrontation", m[0].dramatic_function == "confrontation",
           m[0].dramatic_function)
    _check("text_segments 拼接保序", m[0].text_segments == ["甲", "乙"], str(m[0].text_segments))
    # transition(9) + action(3) → action
    m2 = _merge_beats_to_target(
        [_beat("a", 1, "transition", segments=["甲"]), _beat("b", 2, "action", segments=["乙"])],
        1, [],
    )
    _check("transition+action → action", m2[0].dramatic_function == "action",
           m2[0].dramatic_function)
    # 优先级表单测
    _check("confrontation 优先级 < dialogue",
           _dramatic_priority("confrontation") < _dramatic_priority("dialogue"))
    _check("dialogue 优先级 < action",
           _dramatic_priority("dialogue") < _dramatic_priority("action"))
    _check("transition 优先级最高（最不具体）",
           _dramatic_priority("transition") > _dramatic_priority("plant_clue"))
    _check("未知值 → 末尾", _dramatic_priority("zzz") > _dramatic_priority("transition"))


def test_merge_continue_until_target() -> None:
    print("\n[5] 合并后仍超 target → 继续合并，直到不超")
    beats = [_beat(f"beat_{i:02d}", i, "dialogue", scene="大堂") for i in range(1, 13)]
    out = _merge_beats_to_target(beats, 8, [])
    _check("12 个合并到 ≤8", len(out) <= 8, str(len(out)))
    _check("12 个合并后不 <2（不会过度合并）", len(out) >= 2, str(len(out)))
    _check("覆盖完整（无丢失）", _flat(out) == _flat(beats))


def test_merge_does_not_mutate_input() -> None:
    print("\n[6] 合并不改入参 beats")
    beats = [_beat(f"beat_{i:02d}", i, "transition") for i in range(1, 11)]
    before = [b.beat_id for b in beats]
    _merge_beats_to_target(beats, 8, [])
    _check("入参未被修改", [b.beat_id for b in beats] == before)


# ---------------- 3. 覆盖补齐 ----------------

def test_fill_gap_complete() -> None:
    print("\n[7] 覆盖补齐：完整覆盖 → 不补、无 warning")
    text = "第一段。\n\n第二段。\n\n第三段。"
    beats = [_beat("beat_01", 1, "dialogue", segments=["第一段。"]),
             _beat("beat_02", 2, "action", segments=["第二段。", "第三段。"])]
    warnings: list[str] = []
    out = _fill_coverage_gaps(text, beats, warnings)
    _check("段数不变", len(out) == 2)
    _check("无缺口 warning", warnings == [], str(warnings))


def test_fill_gap_tail() -> None:
    print("\n[8] 覆盖补齐：缺尾段 → 追加到最近 Beat")
    text = "第一段。\n\n第二段。\n\n第三段。"
    beats = [_beat("beat_01", 1, "dialogue", segments=["第一段。"]),
             _beat("beat_02", 2, "action", segments=["第二段。"])]
    warnings: list[str] = []
    out = _fill_coverage_gaps(text, beats, warnings)
    _check("补到尾段到 beat_02", out[1].text_segments == ["第二段。", "第三段。"],
           str(out[1].text_segments))
    _check("缺口 warning", any("缺口" in w for w in warnings), str(warnings))
    _check("补齐后拼接覆盖全文", _flat(out) == _flat([_beat("", 0, "", segments=["第一段。"]),
                                                    _beat("", 0, "", segments=["第二段。", "第三段。"])]))
    _check("入参 beats 未被污染（深拷贝修复）", beats[1].text_segments == ["第二段。"],
           str(beats[1].text_segments))


def test_fill_gap_middle() -> None:
    print("\n[9] 覆盖补齐：缺中段 → 追加到缺口前最近 Beat")
    text = "第一段。\n\n第二段。\n\n第三段。"
    beats = [_beat("beat_01", 1, "dialogue", segments=["第一段。"]),
             _beat("beat_02", 2, "action", segments=["第三段。"])]
    out = _fill_coverage_gaps(text, beats, [])
    _check("缺中段补到 beat_01（缺口前最近）", out[0].text_segments == ["第一段。", "第二段。"],
           str(out[0].text_segments))
    _check("补齐后拼接覆盖全文", _flat(out) == "第一段。第二段。第三段。", _flat(out))


def test_fill_gap_empty_beats() -> None:
    print("\n[10] 覆盖补齐：空 beats → 原样返回")
    out = _fill_coverage_gaps("任何正文。", [], [])
    _check("空 beats 不变", out == [])


# ---------------- 4. 地点规范化 ----------------

def test_normalize_location_same_name_merge() -> None:
    print("\n[11] 地点规范化：同名合并同一 scene_id")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂"),
             _beat("beat_02", 2, "action", scene="山雨楼大堂"),
             _beat("beat_03", 3, "action", scene="后院")]
    out, loc_map = _normalize_location_ids(beats)
    _check("同名地点同一 scene_id", out[0].scene_id == out[1].scene_id,
           f"{out[0].scene_id} vs {out[1].scene_id}")
    _check("不同地点不同 scene_id", out[1].scene_id != out[2].scene_id)
    _check("scene_id 前缀 location_", out[0].scene_id.startswith("location_"),
           out[0].scene_id)
    _check("loc_map 含地名", loc_map[out[0].scene_id] == "山雨楼大堂", str(loc_map))
    _check("loc_map 2 个地点", len(loc_map) == 2, str(loc_map))


def test_normalize_location_whitespace() -> None:
    print("\n[12] 地点规范化：归一（去空白）合并")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼 大堂"),
             _beat("beat_02", 2, "action", scene="山雨楼大堂")]
    out, _ = _normalize_location_ids(beats)
    _check("去空白后同名 → 同一 scene_id", out[0].scene_id == out[1].scene_id,
           f"{out[0].scene_id} vs {out[1].scene_id}")


def test_normalize_location_empty() -> None:
    print("\n[13] 地点规范化：无地点名 → 空 scene_id")
    beats = [_beat("beat_01", 1, "dialogue")]
    out, loc_map = _normalize_location_ids(beats)
    _check("空地点 → 空 scene_id", out[0].scene_id == "")
    _check("空地点不进 loc_map", loc_map == {})


# ---------------- 5. beat_timeline ----------------

def test_beat_timeline_order_authority() -> None:
    print("\n[14] beat_timeline：Beat 顺序权威（order 升序）")
    beats = [
        _beat("beat_03", 3, "action", scene="location_02"),
        _beat("beat_01", 1, "dialogue", scene="location_01"),
        _beat("beat_02", 2, "action", scene="location_01"),
    ]
    tl = beat_timeline(beats)
    _check("按 order 升序", tl == ["location_01:beat_01", "location_01:beat_02", "location_02:beat_03"],
           str(tl))
    _check("同地点前缀（交叉剪辑可引用同 Scene）",
           tl[0].split(":")[0] == tl[1].split(":")[0])
    _check("条目格式 scene_id:beat_id", all(":" in x and x.split(":")[1].startswith("beat_") for x in tl))


# ---------------- 6. 退化路径集成 ----------------

def test_process_beats_fallback_integration() -> None:
    print("\n[15] 退化路径集成：extract_beats(None) → process_beats 全链路")
    text = ("雨刚停，山雾漫向山雨楼。\n\n"
            "沈青崖走进客栈，环视堂内。\n\n"
            "柳如烟问道：「客官，打尖还是住店？」\n\n"
            "沈青崖低声道：「找人。」")
    beats, rule_only, warnings = extract_beats(text, None, title="t")
    _check("rule_only=True（未经过 AI）", rule_only is True)
    out, loc_map = process_beats(text, beats, warnings)
    _check("仍有 Beat", len(out) >= 1, str(len(out)))
    _check("target 未硬凑（4 段 ≤ 8 → 4 个）", len(out) == 4, str(len(out)))
    _check("覆盖完整（拼接含全部原文）", all(_core(s) in _flat(out) for s in text.split("\n\n")))
    _check("scene_id 已规范化", all(b.scene_id == "" or b.scene_id.startswith("location_") for b in out),
           str([b.scene_id for b in out]))
    _check("warnings 明示未经 AI", any("未经过 AI 剧情理解" in w for w in warnings), str(warnings))


def test_process_beats_ai_payload_full() -> None:
    print("\n[16] AI 路径集成：beats_meta 直接走 process_beats（合并→补齐→规范化）")
    text = "第一段。\n\n第二段。\n\n第三段。"
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂", segments=["第一段。"]),
             _beat("beat_02", 2, "transition", scene="山雨楼大堂", segments=["第二段。"], summary=""),
             _beat("beat_03", 3, "action", scene="后院", segments=["第三段。"])]
    out, loc_map = process_beats(text, beats, [])
    _check("3 个 Beat 不超 8 → 不合并", len(out) == 3, str(len(out)))
    _check("同名地点合并", out[0].scene_id == out[1].scene_id)
    _check("覆盖完整", _flat(out) == "第一段。第二段。第三段。", _flat(out))
    _check("loc_map 2 个地点", len(loc_map) == 2, str(loc_map))


def main() -> None:
    test_target_beat_count()
    test_merge_no_padding()
    test_merge_overlimit_priority_transition()
    test_merge_dramatic_more_specific()
    test_merge_continue_until_target()
    test_merge_does_not_mutate_input()
    test_fill_gap_complete()
    test_fill_gap_tail()
    test_fill_gap_middle()
    test_fill_gap_empty_beats()
    test_normalize_location_same_name_merge()
    test_normalize_location_whitespace()
    test_normalize_location_empty()
    test_beat_timeline_order_authority()
    test_process_beats_fallback_integration()
    test_process_beats_ai_payload_full()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
