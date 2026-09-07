#!/usr/bin/env python3
"""P2-P4（#541）：Global Story Bible 路由业务层（bible_ops.py）。

把 /story/import 的 Bible 接线 + GET/POST /bible 的业务逻辑抽成**纯逻辑函数**
（root 可注入，不 import ComfyUI 的 server/folder_paths），http_routes.py 只做
HTTP 薄壳。延续「后端纯逻辑回归不碰 ComfyUI/torch」的纪律——测试直接 import
本模块 + tempfile 临时 root，无需假模块。

设计（P2_GLOBAL_STORY_BIBLE_PLAN.md §8/§11）：
- `merge_plan_into_bible`：/story/import 的 Bible 更新段（ensure_bible →
  extract_entities_from_plan + update_bible（Stable Entity Key 复用/新建候选/
  动态累积）→ version+1 → save_bible）。http_routes 在 extract_beats /
  build_plan_from_beats 之后调用它，把本集实体并入项目 Bible。
- `get_bible_payload` / `confirm_bible_action` / `create_bible_entry`：
  Bible 面板读写（GET /bible / POST /bible/confirm / POST /bible/entry）。

铁律：
- 纯规则零显存零第三方依赖；不 import project_store（避免 folder_paths）。
- 确认策略 = 静态锁定 + 动态累积：confirm/accept_attribute/edit_attributes 只动
  attributes/aliases/source/status，history/last_seen 由导入自动累积。
- 任何写回前 version+1 + updated_at（文件即状态，内容变更即演化）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .bible import (
    BIBLE_SOURCE_USER,
    BIBLE_STATUS_CANDIDATE,
    BIBLE_STATUS_CONFIRMED,
    BibleEntry,
    StoryBible,
    entry_by_id,
    new_bible_entry_id,
)
from .bible_store import ensure_bible, load_bible, save_bible
from .bible_updater import extract_entities_from_plan, update_bible
from .production_plan import ProductionPlan


def _bump_version(bible: StoryBible) -> None:
    """任何写回前 version+1 + 时间戳（文件即状态，内容变更即演化）。"""
    bible.version = int(bible.version or 0) + 1
    bible.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# POST /story/import 的 Bible 更新段
# ---------------------------------------------------------------------------

def merge_plan_into_bible(
    *,
    project_id: str,
    plan: ProductionPlan,
    episode_number: int = 1,
    root: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """把本集 ProductionPlan 的实体并入项目 Bible。

    Args:
        project_id: 目标项目（bible.json 与 project.json 同目录）。
        plan: P1-B build_plan_from_beats 产物（含 scenes→shots 实体 + beats 剧情标题）。
        episode_number: 本集编号（1-99），history/last_seen 用 ep{N} 记录。
        root: projects 根目录（测试注入；None = ComfyUI 实际环境）。

    Returns:
        (bible_dict, updates)
        bible_dict = 更新后 Bible 的 to_dict()（version 已 +1，已落盘）。
        updates = update_bible 的变更列表（new_candidate / reused）。
    """
    bible = ensure_bible(project_id, root=root)
    ep = f"ep{max(1, int(episode_number))}"
    entities = extract_entities_from_plan(plan)
    new_bible, updates = update_bible(bible, entities, plan, episode_id=ep)
    _bump_version(new_bible)
    save_bible(new_bible, root=root)
    return new_bible.to_dict(), updates


# ---------------------------------------------------------------------------
# GET /bible
# ---------------------------------------------------------------------------

def get_bible_payload(project_id: str, root: Optional[str] = None) -> Dict[str, Any]:
    """读项目 Bible；尚未创建 → bible=None（前端显示空面板，不 404）。"""
    bible = load_bible(project_id, root=root)
    return {"bible": bible.to_dict() if bible is not None else None}


# ---------------------------------------------------------------------------
# POST /bible/confirm（确认候选 / 采纳属性建议 / 合并别名 / 人工改静态属性）
# ---------------------------------------------------------------------------

def confirm_bible_action(
    *,
    project_id: str,
    entity_id: str,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    root: Optional[str] = None,
) -> Dict[str, Any]:
    """action ∈ confirm | accept_attribute | merge_alias | edit_attributes。

    - confirm：candidate → confirmed，source=user（静态 attributes 锁定）。
    - accept_attribute：采纳一条属性建议（payload.key + payload.value）→ 并入
      attributes，并从 attribute_suggestions 移除匹配项。
    - merge_alias：payload.alias 加入 aliases（去重）。
    - edit_attributes：payload.attributes（dict）整体覆盖静态 attributes，
      空值表示删除该键；source=user。

    非法输入/未知 action → ValueError（http_routes 转 400）。
    """
    if not project_id or not entity_id or not action:
        raise ValueError("project_id/entity_id/action 必填")
    bible = load_bible(project_id, root=root)
    if bible is None:
        raise ValueError(f"项目 {project_id} 尚无 Bible（请先导入章节）")
    entry = entry_by_id(bible, entity_id)
    if entry is None:
        raise ValueError(f"Bible 无条目 {entity_id}")
    payload = payload or {}

    if action == "confirm":
        entry.status = BIBLE_STATUS_CONFIRMED
        entry.source = BIBLE_SOURCE_USER
    elif action == "accept_attribute":
        key = str(payload.get("key") or "").strip()
        value = str(payload.get("value") or "").strip()
        if not key or not value:
            raise ValueError("accept_attribute 需要 payload.key + payload.value")
        entry.attributes[key] = value
        entry.attribute_suggestions = [
            s for s in (entry.attribute_suggestions or [])
            if not (str(s.get("key", "")) == key and str(s.get("value", "")) == value)
        ]
    elif action == "merge_alias":
        alias = str(payload.get("alias") or "").strip()
        if not alias:
            raise ValueError("merge_alias 需要 payload.alias")
        if alias not in entry.aliases:
            entry.aliases.append(alias)
    elif action == "edit_attributes":
        attrs = payload.get("attributes")
        if not isinstance(attrs, dict):
            raise ValueError("edit_attributes 需要 payload.attributes（dict）")
        entry.attributes = {
            str(k): str(v) for k, v in attrs.items() if str(v).strip()
        }
        entry.source = BIBLE_SOURCE_USER
    else:
        raise ValueError(f"未知 action：{action}")

    _bump_version(bible)
    save_bible(bible, root=root)
    return {"bible": bible.to_dict()}


# ---------------------------------------------------------------------------
# POST /bible/entry（人工新建条目）
# ---------------------------------------------------------------------------

def create_bible_entry(
    *,
    project_id: str,
    entity_type: str,
    name: str,
    status: str = BIBLE_STATUS_CANDIDATE,
    attributes: Optional[Dict[str, str]] = None,
    aliases: Optional[List[str]] = None,
    root: Optional[str] = None,
) -> Dict[str, Any]:
    """人工新建条目（source=user）。Bible 不存在则自动建空 Bible。

    返回 {"bible": 更新后全量, "created": 新条目 to_dict()}。
    """
    if not project_id or not entity_type or not name.strip():
        raise ValueError("project_id/entity_type/name 必填")
    if status not in (BIBLE_STATUS_CANDIDATE, BIBLE_STATUS_CONFIRMED):
        status = BIBLE_STATUS_CANDIDATE
    bible = ensure_bible(project_id, root=root)
    eid = new_bible_entry_id(bible, entity_type)
    entry = BibleEntry(
        entity_id=eid,
        entity_type=entity_type,
        name=name.strip(),
        status=status,
        source=BIBLE_SOURCE_USER,
        aliases=[str(a).strip() for a in (aliases or []) if str(a).strip()],
        attributes={
            str(k): str(v) for k, v in (attributes or {}).items() if str(v).strip()
        },
    )
    bible.entries.append(entry)
    _bump_version(bible)
    save_bible(bible, root=root)
    return {"bible": bible.to_dict(), "created": entry.to_dict()}


__all__ = [
    "merge_plan_into_bible",
    "get_bible_payload",
    "confirm_bible_action",
    "create_bible_entry",
    "_bump_version",
]
