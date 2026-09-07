#!/usr/bin/env python3
"""P2-P3（#540）：Global Story Bible 上下文注入单元测试。

覆盖（P2_GLOBAL_STORY_BIBLE_PLAN.md §7）：
1. _bible_context_block：None / 空 Bible / 仅候选 → ""（无可注入）；
2. 已确认条目渲染：人物/地点/道具分组 + 稳定 id + ✓ + attributes + 当前/最近；
3. 候选条目绝不出现在上下文块（AI 只提候选不覆盖已确认）；
4. history 注入 cap（最近 20 条，防超 token）；
5. _build_story_analyze_prompt：bible=None → 与 P1-B 模板逐字一致（向后兼容）；
6. bible 有已确认 → 上下文块插在「小说正文：」之前，正文仍在；
7. extract_beats 端到端：bible 注入生效 / 无 bible 向后兼容 / 退化路径不受影响。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_bible_context.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.bible import (  # noqa: E402
    BIBLE_STATUS_CONFIRMED,
    BibleEntry,
    StoryBible,
)
from director.story_analyzer import (  # noqa: E402
    _STORY_ANALYZE_TEMPLATE,
    _BIBLE_HISTORY_CAP,
    _bible_context_block,
    _build_story_analyze_prompt,
    extract_beats,
)

_SAMPLE_TEXT = "沈青崖背着一把旧剑匣走进山雨楼大堂，林雪在角落看着他。"


def _mk_confirmed_bible() -> StoryBible:
    return StoryBible(
        bible_id="proj_demo",
        project_id="proj_demo",
        version=3,
        entries=[
            BibleEntry(
                entity_id="CHAR-001",
                entity_type="character",
                name="沈青崖",
                status=BIBLE_STATUS_CONFIRMED,
                attributes={"appearance": "白衣剑客", "personality": "冷峻"},
                current_status="重伤未愈",
                last_seen="ep2",
                history=["ep2：沈青崖取得剑匣"],
            ),
            # 候选条目：绝不该出现在上下文块
            BibleEntry(
                entity_id="CHAR-002",
                entity_type="character",
                name="林雪",
                status="candidate",
                last_seen="ep1",
            ),
            BibleEntry(
                entity_id="LOC-001",
                entity_type="location",
                name="山雨楼大堂",
                status=BIBLE_STATUS_CONFIRMED,
                attributes={"environment": "雨夜烛火"},
                last_seen="ep3",
            ),
            BibleEntry(
                entity_id="PROP-001",
                entity_type="prop",
                name="剑匣",
                status=BIBLE_STATUS_CONFIRMED,
                attributes={"kind": "旧剑匣"},
                current_status="在林雪手中",
                history=["ep2：沈青崖取得剑匣"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# fake TextBackend（捕获 prompt，返回合法 beats，不碰 session 互斥/torch）
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self) -> None:
        self.captured_prompts: list = []

    def analyze_json(self, prompt, *, max_tokens=1024, temperature=0.0, retry=True):
        self.captured_prompts.append(prompt)
        return {
            "beats": [
                {
                    "title": "沈青崖进入山雨楼",
                    "summary": "沈青崖带着旧剑匣走进山雨楼大堂，林雪在角落看着他。",
                    "dramatic_function": "introduce_character",
                    "location": "山雨楼大堂",
                    "time": "夜晚",
                    "weather": "雨",
                    "text_segments": [_SAMPLE_TEXT],
                    "characters": ["沈青崖", "林雪"],
                    "props": ["剑匣"],
                    "dialogue": [],
                    "emotion": "凝重",
                    "transition_reason": "开场建立",
                }
            ]
        }


class _FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.llm = _FakeLLM()

    def session(self):
        return _FakeSession(self.llm)

    def close(self) -> None:
        pass


class _FakeSession:
    def __init__(self, llm) -> None:
        self._llm = llm

    def __enter__(self):
        return self._llm

    def __exit__(self, *exc) -> bool:
        return False


# ---------------------------------------------------------------------------
# _bible_context_block
# ---------------------------------------------------------------------------

def test_context_block_none_is_empty() -> None:
    assert _bible_context_block(None) == ""


def test_context_block_empty_bible_is_empty() -> None:
    assert _bible_context_block(StoryBible(project_id="x")) == ""


def test_context_block_only_candidates_is_empty() -> None:
    bible = StoryBible(project_id="x", entries=[
        BibleEntry(entity_id="CHAR-001", entity_type="character", name="沈青崖", status="candidate"),
    ])
    assert _bible_context_block(bible) == ""


def test_context_block_renders_confirmed() -> None:
    block = _bible_context_block(_mk_confirmed_bible())
    assert "【跨章知识（Global Story Bible）】" in block
    assert "人物：" in block
    assert "地点：" in block
    assert "道具：" in block
    assert "已发生剧情：" in block
    # 稳定 id + ✓ + attributes + 动态 state
    assert "沈青崖 (CHAR-001) ✓" in block
    assert "appearance=白衣剑客" in block
    assert "当前：重伤未愈" in block
    assert "最近：ep2：沈青崖取得剑匣" in block
    assert "山雨楼大堂 (LOC-001) ✓" in block
    assert "剑匣 (PROP-001) ✓" in block
    # 已发生剧情行
    assert "- ep2：沈青崖取得剑匣" in block
    # 规则提示
    assert "用其稳定名，不另造新名" in block


def test_context_block_excludes_candidates() -> None:
    block = _bible_context_block(_mk_confirmed_bible())
    # 候选条目的「名 (CHAR-002) ✓/◌」结构绝不该出现；
    # 注意 PROP-001 的 current_status「在林雪手中」合法含「林雪」，不能按子串断言。
    assert "林雪 (CHAR-002)" not in block
    assert "CHAR-002" not in block


def test_context_block_history_cap() -> None:
    entries = [
        BibleEntry(
            entity_id="CHAR-001",
            entity_type="character",
            name="沈青崖",
            status=BIBLE_STATUS_CONFIRMED,
            history=[f"ep{i}：事件{i}" for i in range(1, _BIBLE_HISTORY_CAP + 6)],  # 26 条
        )
    ]
    block = _bible_context_block(StoryBible(project_id="x", entries=entries))
    # 只保留最近 _BIBLE_HISTORY_CAP 条（ep6 起）
    assert f"ep1：事件1" not in block
    assert f"ep5：事件5" not in block
    assert f"ep6：事件6" in block
    assert f"ep{_BIBLE_HISTORY_CAP + 5}：事件{_BIBLE_HISTORY_CAP + 5}" in block
    # 计数：块内「- ep」行 = cap
    ep_lines = [l for l in block.splitlines() if l.startswith("- ep")]
    assert len(ep_lines) == _BIBLE_HISTORY_CAP


# ---------------------------------------------------------------------------
# _build_story_analyze_prompt
# ---------------------------------------------------------------------------

def test_build_prompt_backward_compat() -> None:
    title, story = "第二章", "正文……"
    prompt = _build_story_analyze_prompt(title, story, None)
    expected = _STORY_ANALYZE_TEMPLATE.format(title=title, story_text=story)
    assert prompt == expected, "bible=None 必须与 P1-B 模板逐字一致"


def test_build_prompt_no_confirmed_backward_compat() -> None:
    title, story = "第二章", "正文……"
    bible = StoryBible(project_id="x", entries=[
        BibleEntry(entity_id="CHAR-001", entity_type="character", name="沈青崖", status="candidate"),
    ])
    prompt = _build_story_analyze_prompt(title, story, bible)
    expected = _STORY_ANALYZE_TEMPLATE.format(title=title, story_text=story)
    assert prompt == expected


def test_build_prompt_injects_before_story() -> None:
    title, story = "第二章", "正文……"
    prompt = _build_story_analyze_prompt(title, story, _mk_confirmed_bible())
    assert "【跨章知识（Global Story Bible）】" in prompt
    marker = "小说正文："
    assert prompt.index("【跨章知识（Global Story Bible）】") < prompt.index(marker)
    # 正文仍在模板里
    assert f"{marker}\n{story}" in prompt
    assert prompt.count(marker) == 1


# ---------------------------------------------------------------------------
# extract_beats 端到端
# ---------------------------------------------------------------------------

def test_extract_beats_with_bible_injects() -> None:
    backend = _FakeBackend()
    beats, rule_only, _ = extract_beats(_SAMPLE_TEXT, backend, title="第三章", bible=_mk_confirmed_bible())
    assert rule_only is False
    assert beats, "合法 beats 应返回 ai 结果"
    prompt = backend.llm.captured_prompts[0]
    assert "【跨章知识（Global Story Bible）】" in prompt
    assert "沈青崖 (CHAR-001) ✓" in prompt
    assert "小说正文：" in prompt


def test_extract_beats_no_bible_backward_compat() -> None:
    backend = _FakeBackend()
    beats, rule_only, _ = extract_beats(_SAMPLE_TEXT, backend, title="第三章")
    assert rule_only is False
    prompt = backend.llm.captured_prompts[0]
    assert "【跨章知识（Global Story Bible）】" not in prompt
    expected = _STORY_ANALYZE_TEMPLATE.format(title="第三章", story_text=_SAMPLE_TEXT)
    assert prompt == expected, "无 bible 时 prompt 与 P1-B 逐字一致"


def test_extract_beats_rule_fallback_with_bible() -> None:
    beats, rule_only, warnings = extract_beats(_SAMPLE_TEXT, None, bible=_mk_confirmed_bible())
    assert rule_only is True
    assert any("需人工检查" in w for w in warnings)
    assert beats, "退化路径仍产出规则 beats"
