#!/usr/bin/env python3
"""P2 · Global Story Bible 更新器（bible_updater.py，任务 #539）。

用户 2026-08-15 拍板（P2_GLOBAL_STORY_BIBLE_PLAN.md §6）：纯规则零 LLM 零 GPU。
把本集（一章）导入产出的实体并入项目 Bible：

- **命中**已确认/候选条目（normalize 匹配 name/aliases）→ 复用稳定 entity_id
  （Stable Entity Key），**只更新动态 state**（last_seen / history），静态
  attributes 绝不动。
- **未命中** → 新建 candidate 条目（source="rule"，status="candidate"，待人工确认）。
- 返回 `(updated_bible, updates)`：updates 供前端展示「本次导入对 Bible 的变更」。

输入选择：接受 `ProductionPlan`（P1-B 导入产物），从 scenes→shots 提取
characters / props / location_name——**复用现有脚本实体抽取结果，不重复解析原文**。

铁律：
- 深拷贝，绝不污染入参（P1-B 已锁死的测试纪律）。
- version 由路由层在保存前 +1（本模块不管持久化时机）。
- 已确认条目被别名命中 → 仍保持 confirmed，不降级。
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, List, Optional, Tuple

from .bible import (
    BIBLE_ENTITY_CHARACTER,
    BIBLE_ENTITY_LOCATION,
    BIBLE_ENTITY_PROP,
    BIBLE_SOURCE_RULE,
    BIBLE_STATUS_CANDIDATE,
    BibleEntry,
    StoryBible,
    find_entry,
    new_bible_entry_id,
    normalize_entity_name,
)
from .production_plan import ProductionPlan

# 「未命名地点」兜底（P1-B-3 §6.3 的 location_00）无信息量，不收入 Bible
_UNNAMED_LOCATION = "未命名地点"


# ---------------------------------------------------------------------------
# 实体提取（复用 P1-B 脚本实体抽取结果）
# ---------------------------------------------------------------------------

def extract_entities_from_plan(plan: ProductionPlan) -> List[Dict[str, str]]:
    """从 ProductionPlan 提取去重实体列表：`[{entity_type, name}]`。

    - 地点 = scene.title / location_name（跳过「未命名地点」兜底）；
    - 角色 = shot.characters[].name；
    - 道具 = shot.props[].name。
    全部经 normalize_entity_name 去重（跨 scene 同名合并）。
    """
    out: List[Dict[str, str]] = []
    seen: set = set()
    for scene in plan.scenes or []:
        loc = (scene.location_name or scene.title or "").strip()
        if loc and loc != _UNNAMED_LOCATION and normalize_entity_name(loc) not in seen:
            seen.add(normalize_entity_name(loc))
            out.append({"entity_type": BIBLE_ENTITY_LOCATION, "name": loc})
        for shot in scene.shots or []:
            for ch in shot.characters or []:
                name = (ch.name or "").strip()
                if name and normalize_entity_name(name) not in seen:
                    seen.add(normalize_entity_name(name))
                    out.append({"entity_type": BIBLE_ENTITY_CHARACTER, "name": name})
            for p in shot.props or []:
                name = (p.name or "").strip()
                if name and normalize_entity_name(name) not in seen:
                    seen.add(normalize_entity_name(name))
                    out.append({"entity_type": BIBLE_ENTITY_PROP, "name": name})
    return out


# ---------------------------------------------------------------------------
# 历史行 / 集序比较
# ---------------------------------------------------------------------------

def _first_beat_title(plan: ProductionPlan, entity_name: str) -> str:
    """在 plan.beats 里找第一条包含该实体名的 Beat 标题（history 用剧情进展）。"""
    key = normalize_entity_name(entity_name)
    if not key:
        return ""
    for b in plan.beats or []:
        for seg in b.text_segments or []:
            if key in normalize_entity_name(seg):
                return (b.title or "").strip()
        if key in normalize_entity_name(b.title):
            return (b.title or "").strip()
    return ""


def _history_line(plan: Optional[ProductionPlan], entity_name: str, episode_id: str) -> str:
    if plan is None:
        return ""
    title = _first_beat_title(plan, entity_name)
    if not title:
        return ""
    return f"{episode_id}：{title}"


def _ep_num(ep: str) -> int:
    m = re.search(r"(\d+)", ep or "")
    return int(m.group(1)) if m else 0


def _ep_gt(a: str, b: str) -> bool:
    """a 集是否晚于 b 集（ep1 < ep2 < … < ep12，按数字不按字典序）。"""
    return _ep_num(a) > _ep_num(b)


# ---------------------------------------------------------------------------
# 主入口：把本集实体并入 Bible
# ---------------------------------------------------------------------------

def update_bible(
    bible: StoryBible,
    entities: List[Dict[str, str]],
    plan: Optional[ProductionPlan] = None,
    *,
    episode_id: str = "ep1",
) -> Tuple[StoryBible, List[Dict[str, Any]]]:
    """把本集实体并入 Bible。

    Args:
        bible: 现有项目 Bible（**深拷贝后返回，不污染入参**）。
        entities: `extract_entities_from_plan` 输出（[{entity_type, name}]）。
        plan: 本集 ProductionPlan（用于从 beats 取剧情标题写 history；可 None）。
        episode_id: 本集编号（ep1 / ep3 / …）。

    Returns:
        (updated_bible, updates)
        updates: [{kind: "new_candidate"|"reused", entity_id, name, message}]
    """
    # ---- 深拷贝（P1-B 纪律：绝不污染入参）----
    entries: List[BibleEntry] = []
    for e in bible.entries:
        entries.append(
            dataclasses.replace(
                e,
                aliases=list(e.aliases),
                attributes=dict(e.attributes),
                attribute_suggestions=[dict(s) for s in e.attribute_suggestions],
                history=list(e.history),
                props_held=list(e.props_held),
                relationships=list(e.relationships),
            )
        )
    new_bible = StoryBible(
        bible_id=bible.bible_id or bible.project_id,
        project_id=bible.project_id,
        entries=entries,
        version=bible.version,
        updated_at=bible.updated_at,
    )

    updates: List[Dict[str, Any]] = []
    # (entity_id, history_line) 本集已记录 → 防重复追加
    recorded_history: set = set()

    for ent in entities:
        etype = ent.get("entity_type", "")
        name = (ent.get("name") or "").strip()
        if not name:
            continue

        existing = find_entry(new_bible, name)
        if existing is not None:
            # ---- 复用稳定 id：只更新动态 state，静态 attributes 绝不动 ----
            entity_id = existing.entity_id
            line = _history_line(plan, name, episode_id)
            if line and line not in existing.history:
                key = (entity_id, line)
                if key not in recorded_history:
                    existing.history.append(line)
                    recorded_history.add(key)
            if not existing.last_seen or _ep_gt(episode_id, existing.last_seen):
                existing.last_seen = episode_id
            updates.append({
                "kind": "reused",
                "entity_id": entity_id,
                "name": existing.name,
                "message": f"复用 {existing.name}（{entity_id}）",
            })
        else:
            # ---- 新建候选（AI 只提候选，不覆盖已确认）----
            eid = new_bible_entry_id(new_bible, etype)
            new_entry = BibleEntry(
                entity_id=eid,
                entity_type=etype,
                name=name,
                status=BIBLE_STATUS_CANDIDATE,
                source=BIBLE_SOURCE_RULE,
                first_seen=episode_id,
                last_seen=episode_id,
            )
            line = _history_line(plan, name, episode_id)
            if line:
                new_entry.history.append(line)
            new_bible.entries.append(new_entry)
            updates.append({
                "kind": "new_candidate",
                "entity_id": eid,
                "name": name,
                "message": f"新候选 {name}（{eid}，待确认）",
            })

    return new_bible, updates


__all__ = [
    "extract_entities_from_plan",
    "update_bible",
    "_first_beat_title",
    "_history_line",
]
