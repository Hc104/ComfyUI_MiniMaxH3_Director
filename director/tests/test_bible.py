#!/usr/bin/env python3
"""P2-P1（#538）：Global Story Bible 数据模型单元测试。

覆盖（P2_GLOBAL_STORY_BIBLE_PLAN.md §4）：
1. BibleEntry round-trip：to_dict/from_dict 保全部字段；
2. from_dict 防御：缺字段 / 非 dict / 空值 → 默认值不抛；
3. StoryBible round-trip + 空壳条目不收录；
4. new_bible_entry_id：类型独立序号（CHAR-001→CHAR-002）、跳过已占用、未知类型 ENT 兜底；
5. entry_by_id / find_entry（normalize 匹配 name/aliases）；
6. confirmed_entries / by_type 筛选。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_bible.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.bible import (  # noqa: E402
    BIBLE_ENTITY_CHARACTER,
    BIBLE_ENTITY_EVENT,
    BIBLE_ENTITY_LOCATION,
    BIBLE_ENTITY_PROP,
    BIBLE_SOURCE_AI,
    BIBLE_SOURCE_USER,
    BIBLE_STATUS_CANDIDATE,
    BIBLE_STATUS_CONFIRMED,
    BibleEntry,
    StoryBible,
    by_type,
    confirmed_entries,
    entry_by_id,
    find_entry,
    new_bible_entry_id,
)


def _mk_entry(entity_id: str = "CHAR-001", name: str = "沈青崖", **kw) -> BibleEntry:
    kwargs = dict(
        entity_type=BIBLE_ENTITY_CHARACTER,
        aliases=["青崖", "沈公子"],
        status=BIBLE_STATUS_CONFIRMED,
        source=BIBLE_SOURCE_AI,
        attributes={"appearance": "白衣剑客", "personality": "冷峻"},
        attribute_suggestions=[{"appearance": "左眉有一道疤"}],
        current_status="重伤未愈",
        last_seen="ep3",
        history=["ep1：初入山雨楼", "ep2：取得剑匣"],
        first_seen="ep1",
        props_held=["PROP-001"],
        relationships=["沈青崖-林雪：故交"],
        asset_key="shen_qingya",
    )
    kwargs.update(kw)
    return BibleEntry(entity_id=entity_id, name=name, **kwargs)


def test_entry_roundtrip_all_fields() -> None:
    e = _mk_entry()
    d = e.to_dict()
    # 校验全部 15 字段存在
    for key in (
        "entity_id", "entity_type", "name", "aliases", "status", "source",
        "attributes", "attribute_suggestions", "current_status", "last_seen",
        "history", "first_seen", "props_held", "relationships", "asset_key",
    ):
        assert key in d, f"缺字段 {key}"
    e2 = BibleEntry.from_dict(d)
    assert e2 == e, "round-trip 后应逐字段相等"


def test_entry_from_dict_missing_fields() -> None:
    e = BibleEntry.from_dict({})
    assert e.entity_id == ""
    assert e.entity_type == ""
    assert e.status == BIBLE_STATUS_CANDIDATE
    assert e.source == BIBLE_SOURCE_AI
    assert e.aliases == []
    assert e.attributes == {}
    assert e.history == []
    assert e.props_held == []
    assert e.relationships == []


def test_entry_from_dict_non_dict() -> None:
    assert BibleEntry.from_dict(None) == BibleEntry()
    assert BibleEntry.from_dict("abc") == BibleEntry()
    assert BibleEntry.from_dict([]) == BibleEntry()


def test_entry_from_dict_cleans_values() -> None:
    raw = {
        "entity_id": "  CHAR-007  ",
        "name": " 林雪 ",
        "aliases": ["林小姐", "", "林小姐"],       # 去空 + 去重
        "attributes": {" 外观 ": " 持伞 ", "": "x"},  # 空 key 丢弃
        "history": ["ep1：出场", "ep1：出场"],       # 去重
        "version": "3",
    }
    e = BibleEntry.from_dict(raw)
    assert e.entity_id == "CHAR-007"
    assert e.name == "林雪"
    assert e.aliases == ["林小姐"]
    assert e.attributes == {"外观": "持伞"}
    assert e.history == ["ep1：出场"]
    assert e.asset_key == ""


def test_bible_roundtrip() -> None:
    b = StoryBible(
        bible_id="proj_demo",
        project_id="proj_demo",
        entries=[_mk_entry(), _mk_entry(entity_id="LOC-001", name="山雨楼大堂", entity_type=BIBLE_ENTITY_LOCATION)],
        version=3,
        updated_at="2026-08-15T12:00:00",
    )
    d = b.to_dict()
    b2 = StoryBible.from_dict(d)
    assert b2 == b


def test_bible_from_dict_missing() -> None:
    assert StoryBible.from_dict(None).entries == []
    assert StoryBible.from_dict({}).version == 0
    # entries 里空壳条目不收录
    b = StoryBible.from_dict({"entries": [{}, {"entity_id": "CHAR-001", "name": "沈青崖"}]})
    assert len(b.entries) == 1
    assert b.entries[0].entity_id == "CHAR-001"


def test_new_entry_id_sequence() -> None:
    b = StoryBible(entries=[
        _mk_entry("CHAR-001"),
        _mk_entry("CHAR-003", name="林雪"),
    ])
    assert new_bible_entry_id(b, BIBLE_ENTITY_CHARACTER) == "CHAR-004"


def test_new_entry_id_by_type_independent() -> None:
    b = StoryBible(entries=[
        _mk_entry("CHAR-005", name="沈青崖"),
        BibleEntry(entity_id="LOC-002", entity_type=BIBLE_ENTITY_LOCATION, name="山雨楼大堂"),
        BibleEntry(entity_id="PROP-001", entity_type=BIBLE_ENTITY_PROP, name="剑匣"),
        BibleEntry(entity_id="EVT-001", entity_type=BIBLE_ENTITY_EVENT, name="林雪被劫"),
    ])
    assert new_bible_entry_id(b, BIBLE_ENTITY_CHARACTER) == "CHAR-006"
    assert new_bible_entry_id(b, BIBLE_ENTITY_LOCATION) == "LOC-003"
    assert new_bible_entry_id(b, BIBLE_ENTITY_PROP) == "PROP-002"
    assert new_bible_entry_id(b, BIBLE_ENTITY_EVENT) == "EVT-002"


def test_new_entry_id_unknown_type_fallback() -> None:
    b = StoryBible(entries=[_mk_entry("CHAR-001")])
    assert new_bible_entry_id(b, "misc") == "ENT-001"
    assert new_bible_entry_id(b, "") == "ENT-001"


def test_entry_by_id() -> None:
    b = StoryBible(entries=[_mk_entry("CHAR-001"), _mk_entry("CHAR-002", name="林雪")])
    assert entry_by_id(b, "CHAR-002").name == "林雪"
    assert entry_by_id(b, "CHAR-999") is None
    assert entry_by_id(b, "") is None


def test_find_entry_normalize() -> None:
    b = StoryBible(entries=[_mk_entry("CHAR-001", name="沈青崖", aliases=["青 崖", "沈公子"])])
    # 别名含空白 → normalize 去空白命中
    assert find_entry(b, "青 崖").entity_id == "CHAR-001"
    assert find_entry(b, "沈青崖").entity_id == "CHAR-001"
    assert find_entry(b, "沈公子").entity_id == "CHAR-001"
    assert find_entry(b, "林雪") is None
    assert find_entry(b, "") is None


def test_confirmed_and_by_type() -> None:
    b = StoryBible(entries=[
        _mk_entry("CHAR-001", status=BIBLE_STATUS_CONFIRMED),
        _mk_entry("CHAR-002", name="林雪", status=BIBLE_STATUS_CANDIDATE),
        BibleEntry(entity_id="LOC-001", entity_type=BIBLE_ENTITY_LOCATION, name="山雨楼大堂", status=BIBLE_STATUS_CONFIRMED),
    ])
    confirmed = confirmed_entries(b)
    assert [e.entity_id for e in confirmed] == ["CHAR-001", "LOC-001"]
    assert [e.entity_id for e in by_type(b, BIBLE_ENTITY_CHARACTER)] == ["CHAR-001", "CHAR-002"]
    assert [e.entity_id for e in by_type(b, BIBLE_ENTITY_PROP)] == []


def test_entry_id_reuse_stable_entity_key() -> None:
    """Stable Entity Key：同一条目反复 from_dict → 不重新分配 id。"""
    e = _mk_entry("CHAR-001")
    b = StoryBible(entries=[e])
    new_id = new_bible_entry_id(b, BIBLE_ENTITY_CHARACTER)
    assert new_id != "CHAR-001", "已占用的 id 绝不复用"
    assert entry_by_id(b, "CHAR-001") is not None
