#!/usr/bin/env python3
"""P2-P2（#539）：Global Story Bible 更新器单元测试。

覆盖（P2_GLOBAL_STORY_BIBLE_PLAN.md §6）：
1. extract_entities_from_plan：角色/道具/地点提取 + normalize 去重 + 跳过「未命名地点」；
2. update_bible 新建候选：status=candidate / source=rule / first_seen+last_seen / history 有剧情标题；
3. update_bible 命中已确认：复用 entity_id（Stable Entity Key）、history 追加、attributes 不动；
4. update_bible 命中候选：复用不新建（无重复条目）；
5. 别名命中已确认 → 复用且保持 confirmed；
6. 深拷贝：入参 bible 不被污染；
7. last_seen 按集序推进（ep12 > ep9，数字序非字典序）；
8. history 同集同实体不重复追加；
9. updates 形状稳定（new_candidate / reused）。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_bible_updater.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.bible import (  # noqa: E402
    BIBLE_ENTITY_CHARACTER,
    BIBLE_ENTITY_LOCATION,
    BIBLE_ENTITY_PROP,
    BIBLE_STATUS_CANDIDATE,
    BIBLE_STATUS_CONFIRMED,
    BibleEntry,
    StoryBible,
)
from director.bible_updater import (  # noqa: E402
    extract_entities_from_plan,
    update_bible,
)
from director.production_plan import (  # noqa: E402
    Character,
    ProductionPlan,
    Prop,
    Scene,
    Shot,
    StoryBeat,
)


def _mk_shot(source: str = "沈青崖背着一把旧剑匣走进山雨楼大堂。",
             chars: tuple = ("沈青崖",),
             props: tuple = ("剑匣",)) -> Shot:
    return Shot(
        shot_id="shot_01",
        source_text=source,
        characters=[Character(name=n) for n in chars],
        props=[Prop(name=p) for p in props],
    )


def _mk_plan(shots: tuple = (_mk_shot(),),
             loc: str = "山雨楼大堂",
             beats: tuple = ("沈青崖进入山雨楼",)) -> ProductionPlan:
    scene = Scene(
        scene_id="location_01",
        title=loc,
        location_name=loc,
        shots=list(shots),
    )
    plan = ProductionPlan(scenes=[scene], timeline=["location_01:shot_01"])
    plan.beats = [
        StoryBeat(
            beat_id=f"beat_{i + 1:02d}",
            title=t,
            text_segments=[shots[0].source_text],
        )
        for i, t in enumerate(beats)
    ]
    return plan


def test_extract_entities_basic() -> None:
    plan = _mk_plan()
    ents = extract_entities_from_plan(plan)
    by_name = {e["name"]: e["entity_type"] for e in ents}
    assert by_name.get("沈青崖") == BIBLE_ENTITY_CHARACTER
    assert by_name.get("剑匣") == BIBLE_ENTITY_PROP
    assert by_name.get("山雨楼大堂") == BIBLE_ENTITY_LOCATION
    assert len(ents) == 3


def test_extract_entities_dedup() -> None:
    # 两个 scene 同名地点 / 同一角色跨镜 → 去重
    shot_a = _mk_shot("沈青崖在柜台前。", chars=("沈青崖",), props=())
    shot_b = _mk_shot("林雪站在门口。", chars=("林雪",), props=())
    scene_a = Scene(scene_id="location_01", title="山雨楼大堂", location_name="山雨楼大堂", shots=[shot_a])
    scene_b = Scene(scene_id="location_02", title="山雨楼大堂", location_name="山雨楼大堂", shots=[shot_b])
    plan = ProductionPlan(scenes=[scene_a, scene_b])
    ents = extract_entities_from_plan(plan)
    names = [e["name"] for e in ents]
    assert names.count("山雨楼大堂") == 1
    assert names.count("沈青崖") == 1


def test_extract_skips_unnamed_location() -> None:
    scene = Scene(scene_id="location_00", title="未命名地点", location_name="未命名地点", shots=[_mk_shot()])
    plan = ProductionPlan(scenes=[scene])
    ents = extract_entities_from_plan(plan)
    assert all(e["entity_type"] != BIBLE_ENTITY_LOCATION for e in ents)


def test_update_new_candidate() -> None:
    bible = StoryBible(project_id="proj_demo", bible_id="proj_demo")
    plan = _mk_plan()
    new_bible, updates = update_bible(bible, extract_entities_from_plan(plan), plan, episode_id="ep3")
    assert len(new_bible.entries) == 3
    ch = next(e for e in new_bible.entries if e.name == "沈青崖")
    assert ch.entity_id == "CHAR-001"
    assert ch.status == BIBLE_STATUS_CANDIDATE
    assert ch.source == "rule"
    assert ch.first_seen == "ep3"
    assert ch.last_seen == "ep3"
    assert ch.history == ["ep3：沈青崖进入山雨楼"]
    assert ch.attributes == {}
    # updates 形状
    kinds = {u["kind"] for u in updates}
    assert kinds == {"new_candidate"}
    assert all("entity_id" in u and "name" in u and "message" in u for u in updates)


def test_update_reuse_confirmed_keeps_attributes() -> None:
    bible = StoryBible(project_id="proj_demo", bible_id="proj_demo", entries=[
        BibleEntry(
            entity_id="CHAR-001",
            entity_type=BIBLE_ENTITY_CHARACTER,
            name="沈青崖",
            status=BIBLE_STATUS_CONFIRMED,
            attributes={"appearance": "白衣剑客", "personality": "冷峻"},
            last_seen="ep1",
            history=["ep1：初入山雨楼"],
        )
    ])
    plan = _mk_plan()
    new_bible, updates = update_bible(bible, extract_entities_from_plan(plan), plan, episode_id="ep2")
    ch = next(e for e in new_bible.entries if e.name == "沈青崖")
    assert ch.entity_id == "CHAR-001", "Stable Entity Key：必须复用"
    assert ch.status == BIBLE_STATUS_CONFIRMED, "已确认不降级"
    assert ch.attributes == {"appearance": "白衣剑客", "personality": "冷峻"}, "静态属性绝不动"
    assert ch.last_seen == "ep2"
    assert ch.history == ["ep1：初入山雨楼", "ep2：沈青崖进入山雨楼"]
    # 沈青崖只复用不新建（剑匣/山雨楼大堂是新实体，按规则建候选）
    chars = [e for e in new_bible.entries if e.entity_type == BIBLE_ENTITY_CHARACTER]
    assert len(chars) == 1
    assert chars[0].entity_id == "CHAR-001"
    # 命中已确认实体的 updates 全部是 reused（不含该实体的 new_candidate）
    assert any(u["kind"] == "reused" and u["entity_id"] == "CHAR-001" for u in updates)
    assert not any(u["kind"] == "new_candidate" and u["entity_id"] == "CHAR-001" for u in updates)


def test_update_reuse_candidate_no_dup() -> None:
    bible = StoryBible(project_id="proj_demo", bible_id="proj_demo", entries=[
        BibleEntry(
            entity_id="CHAR-001",
            entity_type=BIBLE_ENTITY_CHARACTER,
            name="沈青崖",
            status=BIBLE_STATUS_CANDIDATE,
            source="rule",
        )
    ])
    plan = _mk_plan()
    new_bible, _ = update_bible(bible, extract_entities_from_plan(plan), plan, episode_id="ep2")
    chars = [e for e in new_bible.entries if e.entity_type == BIBLE_ENTITY_CHARACTER]
    assert len(chars) == 1, "候选命中也必须复用，不重复建条目"
    assert chars[0].entity_id == "CHAR-001"


def test_update_alias_hits_confirmed() -> None:
    bible = StoryBible(project_id="proj_demo", bible_id="proj_demo", entries=[
        BibleEntry(
            entity_id="CHAR-001",
            entity_type=BIBLE_ENTITY_CHARACTER,
            name="沈青崖",
            aliases=["青崖", "沈公子"],
            status=BIBLE_STATUS_CONFIRMED,
            attributes={"appearance": "白衣剑客"},
        )
    ])
    plan = _mk_plan(shots=(_mk_shot("青崖坐在窗边。", chars=("青崖",), props=()),))
    new_bible, updates = update_bible(bible, extract_entities_from_plan(plan), plan, episode_id="ep2")
    chars = [e for e in new_bible.entries if e.entity_type == BIBLE_ENTITY_CHARACTER]
    assert len(chars) == 1
    assert chars[0].entity_id == "CHAR-001"
    assert chars[0].status == BIBLE_STATUS_CONFIRMED
    assert any(u["kind"] == "reused" for u in updates)


def test_update_does_not_mutate_input() -> None:
    bible = StoryBible(project_id="proj_demo", bible_id="proj_demo", entries=[
        BibleEntry(
            entity_id="CHAR-001",
            entity_type=BIBLE_ENTITY_CHARACTER,
            name="沈青崖",
            status=BIBLE_STATUS_CONFIRMED,
            attributes={"appearance": "白衣剑客"},
            history=["ep1：初入山雨楼"],
            last_seen="ep1",
        )
    ])
    orig_history = list(bible.entries[0].history)
    orig_attr = dict(bible.entries[0].attributes)
    plan = _mk_plan()
    update_bible(bible, extract_entities_from_plan(plan), plan, episode_id="ep9")
    # 入参 bible 完全未被污染
    assert bible.entries[0].history == orig_history
    assert bible.entries[0].attributes == orig_attr
    assert bible.entries[0].last_seen == "ep1"
    assert len(bible.entries) == 1


def test_last_seen_numeric_order() -> None:
    """ep12 必须晚于 ep9（数字序，非字典序）。"""
    bible = StoryBible(project_id="proj_demo", bible_id="proj_demo", entries=[
        BibleEntry(
            entity_id="CHAR-001",
            entity_type=BIBLE_ENTITY_CHARACTER,
            name="沈青崖",
            status=BIBLE_STATUS_CONFIRMED,
            last_seen="ep9",
        )
    ])
    plan = _mk_plan()
    new_bible, _ = update_bible(bible, extract_entities_from_plan(plan), plan, episode_id="ep12")
    assert new_bible.entries[0].last_seen == "ep12"


def test_history_no_dup_same_episode() -> None:
    bible = StoryBible(project_id="proj_demo", bible_id="proj_demo", entries=[
        BibleEntry(
            entity_id="CHAR-001",
            entity_type=BIBLE_ENTITY_CHARACTER,
            name="沈青崖",
            status=BIBLE_STATUS_CONFIRMED,
        )
    ])
    plan = _mk_plan()
    ents = extract_entities_from_plan(plan)
    # 同一批实体连续两次 update 同一集 → history 不重复
    b1, _ = update_bible(bible, ents, plan, episode_id="ep3")
    b2, _ = update_bible(b1, ents, plan, episode_id="ep3")
    ch = next(e for e in b2.entries if e.name == "沈青崖")
    assert ch.history.count("ep3：沈青崖进入山雨楼") == 1
