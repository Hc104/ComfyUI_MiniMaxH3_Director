#!/usr/bin/env python3
"""P1-B-1 · story_analyzer（整章理解 → StoryBeat）单元测试。

覆盖（P1B_STORY_ANALYZER_PLAN.md §2 / §7，用户 2026-08-14 拍板）：
1. AI 正常路径：Qwen beats JSON → StoryBeat（source="ai"、beat_id、dramatic_function、
   order、text_segments 逐字、无覆盖缺口）；
2. dramatic_function 归一：非法值 → transition + warning；
3. backend=None 退化：纯规则按段拆（source="rule"），rule_only=True，warning 明示
   「未经过 AI 剧情理解，需人工检查」（拍板②）；
4. backend 抛异常退化：不阻塞，warning 记录调用失败；
5. analyze_json 返回 None / 非法 → 退化；
6. 覆盖缺口：text_segments 未覆盖全部原文 → warning（P1-B-2 规则补齐，本阶段不越界）；
7. 空正文 → 空 beats + warning；
8. _coverage_gap / _rule_dramatic_function 纯函数单测；
9. 显存安全门：session 进入/退出一次，close 恰 1 次。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_story_analyzer.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.story_analyzer import (  # noqa: E402
    _coverage_gap,
    _rule_dramatic_function,
    extract_beats,
)
from director.text_backends import TextBackend  # noqa: E402

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


_CHAPTER = (
    "雨刚停，山雾从林间漫向山雨楼，门前老槐树滴着水珠。\n\n"
    "沈青崖走进客栈，抖落斗笠上的雨水，环视堂内。\n"
    "柜台后的柳如烟抬起头来，眼中闪过一丝警惕。\n\n"
    "「客官，打尖还是住店？」柳如烟问道。\n"
    "沈青崖低声道：「找人。」他目光落在柜台旁那把蒙尘的剑匣上。"
)

_FULL_SEGMENTS = [
    "雨刚停，山雾从林间漫向山雨楼，门前老槐树滴着水珠。",
    "沈青崖走进客栈，抖落斗笠上的雨水，环视堂内。",
    "柜台后的柳如烟抬起头来，眼中闪过一丝警惕。",
    "「客官，打尖还是住店？」柳如烟问道。",
    "沈青崖低声道：「找人。」他目光落在柜台旁那把蒙尘的剑匣上。",
]


def _valid_payload() -> dict:
    """text_segments 完整覆盖 _CHAPTER（无缺口）的合法 beats JSON。"""
    return {
        "beats": [
            {
                "title": "沈青崖进入山雨楼",
                "summary": "雨夜，年轻剑客踏进客栈。",
                "dramatic_function": "introduce_character",
                "location": "山雨楼外",
                "time": "黄昏",
                "weather": "雨",
                "text_segments": [_FULL_SEGMENTS[0], _FULL_SEGMENTS[1]],
                "characters": ["沈青崖"],
                "props": ["斗笠", "雨水"],
                "dialogue": [],
                "emotion": "警惕",
                "transition_reason": "新地点建立",
            },
            {
                "title": "柳如烟与沈青崖对话",
                "summary": "柳如烟问话，沈青崖答。",
                "dramatic_function": "dialogue",
                "location": "山雨楼大堂",
                "time": "黄昏",
                "weather": "雨",
                "text_segments": [_FULL_SEGMENTS[2], _FULL_SEGMENTS[3], _FULL_SEGMENTS[4]],
                "characters": ["沈青崖", "柳如烟"],
                "props": [],
                "dialogue": ["客官，打尖还是住店？", "找人。"],
                "emotion": "平静",
                "transition_reason": "对话开始",
            },
        ]
    }


# ---------------------------------------------------------------------------
# Fake 后端：继承 TextBackend，session() 走真实 TextSession（互斥锁 + close 统计）
# ---------------------------------------------------------------------------
class _FakeBackend(TextBackend):
    """responder(prompt) -> dict | None | raise。覆写 analyze_json 直达 dict。"""

    name = "fake"

    def __init__(self, responder):
        self.responder = responder
        self.prompts: list[str] = []
        self.close_calls = 0

    def analyze_json(self, prompt, *, max_tokens=1024, temperature=0.0, retry=True):
        self.prompts.append(prompt)
        return self.responder(prompt)

    def close(self):
        self.close_calls += 1


# ---------------- 1. AI 正常路径 ----------------

def test_ai_path_normal() -> None:
    bk = _FakeBackend(responder=lambda p: _valid_payload())
    beats, rule_only, warnings = extract_beats(_CHAPTER, bk, title="山雨客栈·第一章")
    _check("非 rule_only", rule_only is False)
    _check("2 个 Beat", len(beats) == 2, str(len(beats)))
    _check("source=ai", all(b.source == "ai" for b in beats))
    _check("beat_id 连续", [b.beat_id for b in beats] == ["beat_01", "beat_02"])
    _check("dramatic_function 保留",
           [b.dramatic_function for b in beats] == ["introduce_character", "dialogue"])
    _check("order 连续", [b.order for b in beats] == [1, 2])
    _check("scene_id 取 location", beats[1].scene_id == "山雨楼大堂")
    _check("text_segments 逐字", beats[0].text_segments == _FULL_SEGMENTS[:2],
           str(beats[0].text_segments))
    _check("无覆盖缺口警告", not any("覆盖缺口" in w for w in warnings), str(warnings))
    _check("prompt 含正文与标题",
           "沈青崖走进客栈" in bk.prompts[0] and "山雨客栈·第一章" in bk.prompts[0])
    _check("session close 恰 1 次", bk.close_calls == 1, str(bk.close_calls))


# ---------------- 2. dramatic_function 归一 ----------------

def test_dramatic_function_normalize() -> None:
    payload = _valid_payload()
    payload["beats"][1]["dramatic_function"] = "climax_unknown"
    bk = _FakeBackend(responder=lambda p: payload)
    beats, rule_only, warnings = extract_beats(_CHAPTER, bk)
    _check("仍非 rule_only", rule_only is False)
    _check("非法值归一为 transition", beats[1].dramatic_function == "transition",
           beats[1].dramatic_function)
    _check("warning 记录归一", any("归一为 transition" in w for w in warnings), str(warnings))


# ---------------- 3/4/5. 退化路径 ----------------

def test_backend_none_rule_fallback() -> None:
    beats, rule_only, warnings = extract_beats(_CHAPTER, None, title="t")
    _check("rule_only=True", rule_only is True)
    _check("source=rule", all(b.source == "rule" for b in beats))
    _check("按段拆出 Beat（≥2）", len(beats) >= 2, str(len(beats)))
    _check("text_segments 保留原文段", beats[0].text_segments[0] in _CHAPTER)
    _check("warning 明示未经 AI", any("未经过 AI 剧情理解" in w for w in warnings), str(warnings))


def test_backend_exception_rule_fallback() -> None:
    def boom(p):
        raise RuntimeError("模型不可达")
    bk = _FakeBackend(responder=boom)
    beats, rule_only, warnings = extract_beats(_CHAPTER, bk)
    _check("rule_only=True", rule_only is True)
    _check("source=rule", all(b.source == "rule" for b in beats))
    _check("warning 记录调用失败", any("调用失败" in w for w in warnings), str(warnings))
    _check("close 仍执行（session 退出）", bk.close_calls == 1, str(bk.close_calls))


def test_analyze_json_none_rule_fallback() -> None:
    bk = _FakeBackend(responder=lambda p: None)
    beats, rule_only, warnings = extract_beats(_CHAPTER, bk)
    _check("rule_only=True", rule_only is True)
    _check("source=rule", all(b.source == "rule" for b in beats))
    _check("warning 记录非 JSON 对象", any("不是 JSON 对象" in w for w in warnings), str(warnings))


def test_ai_empty_beats_rule_fallback() -> None:
    bk = _FakeBackend(responder=lambda p: {"beats": []})
    beats, rule_only, warnings = extract_beats(_CHAPTER, bk)
    _check("空 beats → 退化", rule_only is True and len(beats) > 0)


# ---------------- 6. 覆盖缺口 ----------------

def test_coverage_gap_warning() -> None:
    payload = _valid_payload()
    # 只覆盖第一段（缺失后 3 句）
    payload["beats"] = payload["beats"][:1]
    payload["beats"][0]["text_segments"] = [_FULL_SEGMENTS[0], _FULL_SEGMENTS[1]]
    bk = _FakeBackend(responder=lambda p: payload)
    beats, rule_only, warnings = extract_beats(_CHAPTER, bk)
    _check("非 rule_only", rule_only is False)
    _check("warning 记录覆盖缺口", any("覆盖缺口" in w for w in warnings), str(warnings))


# ---------------- 7. 空正文 ----------------

def test_empty_text() -> None:
    bk = _FakeBackend(responder=lambda p: _valid_payload())
    beats, rule_only, warnings = extract_beats("   \n ", bk, title="t")
    _check("空正文 beats 空", beats == [])
    _check("空正文 rule_only", rule_only is True)
    _check("空正文 warning", any("正文为空" in w for w in warnings), str(warnings))
    _check("空正文不调 AI（无 prompt）", bk.prompts == [], str(bk.prompts))


# ---------------- 8. 纯函数单测 ----------------

def test_coverage_gap_unit() -> None:
    text = "雨刚停山雾漫向山雨楼沈青崖进门"
    _check("全覆盖 → 空", _coverage_gap(text, ["雨刚停", "山雾漫向山雨楼", "沈青崖进门"]) == "")
    gap = _coverage_gap(text, ["雨刚停", "沈青崖进门"])
    _check("缺中段 → 非空缺口", len(gap) > 0, gap)
    _check("空原文 → 空", _coverage_gap("", ["x"]) == "")
    _check("乱序 → 缺口", _coverage_gap("甲乙", ["乙", "甲"]) != "")


def test_rule_dramatic_function_unit() -> None:
    _check("拔剑 → confrontation", _rule_dramatic_function("他猛然拔剑，怒视对方") == "confrontation")
    _check("问道 → dialogue", _rule_dramatic_function("柳如烟问道：「客官，打尖？」") == "dialogue")
    _check("剑匣 → plant_clue", _rule_dramatic_function("他目光落在蒙尘的剑匣上") == "plant_clue")
    _check("普通叙述 → transition", _rule_dramatic_function("山雾缓缓漫过林间") == "transition")


def main() -> None:
    test_ai_path_normal()
    test_dramatic_function_normalize()
    test_backend_none_rule_fallback()
    test_backend_exception_rule_fallback()
    test_analyze_json_none_rule_fallback()
    test_ai_empty_beats_rule_fallback()
    test_coverage_gap_warning()
    test_empty_text()
    test_coverage_gap_unit()
    test_rule_dramatic_function_unit()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
