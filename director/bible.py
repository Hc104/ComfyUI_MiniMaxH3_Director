#!/usr/bin/env python3
"""P2 · Global Story Bible 数据模型层（bible.py，任务 #538）。

用户 2026-08-15 拍板（P2_GLOBAL_STORY_BIBLE_PLAN.md）：
- 独立 `bible.json`（项目级，后端权威，前端经路由读写）。
- 确认策略 = **静态锁定 + 动态累积**：`attributes` 确认后锁定（AI 永不覆盖）；
  动态 state（current_status/history/last_seen/props_held）自动累积。
- Stable Entity Key：AI 只提候选，不覆盖已确认条目；已确认 `CHAR-001` 跨章复用，
  绝不每章新建 CHAR-019/037/081。

本模块只定义数据结构 + 序列化 + 稳定 id 分配，**不做任何业务编排**：
实体提取/匹配在 bible_updater.py（P2-P2），持久化在 bible_store.py（本文件配套）。

铁律：
- 纯规则零显存零第三方依赖（可被 standalone-env 直接跑单测）。
- 与 production_plan.py 同风格**防御式 from_dict**（缺字段给默认，不抛）。
- 向后兼容：老数据缺任意字段 → 默认值；未知 entity_type/status 保留字符串宽松。

运行方式（无第三方依赖）：
    python3 director/tests/test_bible.py
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .entity_cleanse import normalize_entity_name

# ---------------------------------------------------------------------------
# 常量（宽松字符串，未知值不抛）
# ---------------------------------------------------------------------------
# 实体类型（与 script_parser / asset_registry 类型域一致）
BIBLE_ENTITY_CHARACTER = "character"
BIBLE_ENTITY_LOCATION = "location"
BIBLE_ENTITY_PROP = "prop"
BIBLE_ENTITY_EVENT = "event"
BIBLE_ENTITY_TYPES: tuple = (
    BIBLE_ENTITY_CHARACTER,
    BIBLE_ENTITY_LOCATION,
    BIBLE_ENTITY_PROP,
    BIBLE_ENTITY_EVENT,
)

# 条目状态
BIBLE_STATUS_CANDIDATE = "candidate"    # AI/规则提出，待人工确认
BIBLE_STATUS_CONFIRMED = "confirmed"    # 已确认，静态 attributes 锁定
BIBLE_STATUSES: tuple = (BIBLE_STATUS_CANDIDATE, BIBLE_STATUS_CONFIRMED)

# 来源
BIBLE_SOURCE_AI = "ai"
BIBLE_SOURCE_RULE = "rule"
BIBLE_SOURCE_USER = "user"
BIBLE_SOURCES: tuple = (BIBLE_SOURCE_AI, BIBLE_SOURCE_RULE, BIBLE_SOURCE_USER)

# entity_id 前缀（CHAR-001 / LOC-001 / PROP-001 / EVT-001），跨章稳定
_ENTITY_ID_PREFIX: Dict[str, str] = {
    BIBLE_ENTITY_CHARACTER: "CHAR",
    BIBLE_ENTITY_LOCATION: "LOC",
    BIBLE_ENTITY_PROP: "PROP",
    BIBLE_ENTITY_EVENT: "EVT",
}
_UNKNOWN_PREFIX = "ENT"


# ---------------------------------------------------------------------------
# 防御式转换工具（与 production_plan._as_int_defensive 同风格）
# ---------------------------------------------------------------------------

def _as_int_defensive(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for x in value:
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


def _as_str_dict(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in value.items():
        sk = str(k).strip()
        sv = str(v).strip()
        if sk and sv:
            out[sk] = sv
    return out


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for x in value:
        if isinstance(x, dict):
            out.append({str(k): v for k, v in x.items()})
    return out


# ---------------------------------------------------------------------------
# BibleEntry（一条世界状态记录）
# ---------------------------------------------------------------------------

@dataclass
class BibleEntry:
    """Global Story Bible 单条目。

    静态 facts（attributes）确认后锁定，AI 永不覆盖；动态 state
    （current_status/history/last_seen/props_held）自动累积供上下文注入。

    attribute_suggestions：AI 对已确认条目提出的新属性候选，面板逐条采纳/忽略，
    不直接改 attributes（Stable Entity Key 铁律）。
    """
    entity_id: str = ""                          # CHAR-001（跨章稳定）
    entity_type: str = ""                        # character | location | prop | event
    name: str = ""                               # 规范化名（沈青崖）
    aliases: List[str] = field(default_factory=list)   # 别名（青崖/沈公子）
    status: str = BIBLE_STATUS_CANDIDATE         # candidate | confirmed
    source: str = BIBLE_SOURCE_AI                # ai | rule | user
    # —— 静态 facts（confirmed 后锁定）——
    attributes: Dict[str, str] = field(default_factory=dict)       # 外观/性格/环境/归属…
    attribute_suggestions: List[Dict[str, Any]] = field(default_factory=list)
    # —— 动态 state（自动累积）——
    current_status: str = ""                     # 当前状态（重伤未愈 / 在林雪手中）
    last_seen: str = ""                          # 最后出现集（ep3）
    history: List[str] = field(default_factory=list)      # 已发生剧情（"ep2：沈青崖取得剑匣"）
    first_seen: str = ""                         # 首见集（ep1）
    props_held: List[str] = field(default_factory=list)   # character 持有道具 entity_id
    relationships: List[str] = field(default_factory=list)  # 人物关系候选（"沈青崖-林雪：故交"）
    # —— 关联 ——
    asset_key: str = ""                          # 关联 asset_registry entity_key（可空）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "name": self.name,
            "aliases": list(self.aliases),
            "status": self.status,
            "source": self.source,
            "attributes": dict(self.attributes),
            "attribute_suggestions": [dict(s) for s in self.attribute_suggestions],
            "current_status": self.current_status,
            "last_seen": self.last_seen,
            "history": list(self.history),
            "first_seen": self.first_seen,
            "props_held": list(self.props_held),
            "relationships": list(self.relationships),
            "asset_key": self.asset_key,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "BibleEntry":
        if not isinstance(data, dict):
            return cls()
        return cls(
            entity_id=_as_str(data.get("entity_id")),
            entity_type=_as_str(data.get("entity_type")),
            name=_as_str(data.get("name")),
            aliases=_as_str_list(data.get("aliases")),
            status=_as_str(data.get("status"), BIBLE_STATUS_CANDIDATE),
            source=_as_str(data.get("source"), BIBLE_SOURCE_AI),
            attributes=_as_str_dict(data.get("attributes")),
            attribute_suggestions=_as_dict_list(data.get("attribute_suggestions")),
            current_status=_as_str(data.get("current_status")),
            last_seen=_as_str(data.get("last_seen")),
            history=_as_str_list(data.get("history")),
            first_seen=_as_str(data.get("first_seen")),
            props_held=_as_str_list(data.get("props_held")),
            relationships=_as_str_list(data.get("relationships")),
            asset_key=_as_str(data.get("asset_key")),
        )


# ---------------------------------------------------------------------------
# StoryBible（项目级 Bible 容器）
# ---------------------------------------------------------------------------

@dataclass
class StoryBible:
    """项目级跨章世界状态库。entries 跨集共享；version 每次导入 +1。"""
    bible_id: str = ""                           # 与 project_id 同值
    project_id: str = ""
    entries: List[BibleEntry] = field(default_factory=list)
    version: int = 0                             # 每次导入 +1
    updated_at: str = ""                         # ISO 本地时间

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bible_id": self.bible_id,
            "project_id": self.project_id,
            "entries": [e.to_dict() for e in self.entries],
            "version": self.version,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "StoryBible":
        if not isinstance(data, dict):
            return cls()
        entries_raw = data.get("entries")
        entries: List[BibleEntry] = []
        if isinstance(entries_raw, list):
            for raw in entries_raw:
                e = BibleEntry.from_dict(raw)
                if e.name or e.entity_id:  # 空壳条目不收录
                    entries.append(e)
        return cls(
            bible_id=_as_str(data.get("bible_id")),
            project_id=_as_str(data.get("project_id")),
            entries=entries,
            version=_as_int_defensive(data.get("version")),
            updated_at=_as_str(data.get("updated_at")),
        )


# ---------------------------------------------------------------------------
# 稳定 id 分配 + 查询（纯函数，供 bible_updater / 路由复用）
# ---------------------------------------------------------------------------

def new_bible_entry_id(bible: StoryBible, entity_type: str) -> str:
    """在 bible 内生成稳定 entity_id：按类型取 max 序号 + 1（CHAR-001 → CHAR-002）。

    已确认条目跨章复用该 id，绝不重复分配（Stable Entity Key）。
    未知类型 → ENT-001…（宽松兜底）。
    """
    prefix = _ENTITY_ID_PREFIX.get(entity_type, _UNKNOWN_PREFIX)
    max_seq = 0
    for e in bible.entries:
        eid = e.entity_id or ""
        if eid.startswith(prefix + "-"):
            try:
                seq = int(eid[len(prefix) + 1:])
                if seq > max_seq:
                    max_seq = seq
            except (TypeError, ValueError):
                continue
    return f"{prefix}-{max_seq + 1:03d}"


def entry_by_id(bible: StoryBible, entity_id: str) -> Optional[BibleEntry]:
    """按 entity_id 精确查找（无则 None）。"""
    if not entity_id:
        return None
    for e in bible.entries:
        if e.entity_id == entity_id:
            return e
    return None


def find_entry(bible: StoryBible, name: str) -> Optional[BibleEntry]:
    """按规范化名匹配（name/aliases 均经 normalize_entity_name 归一；无则 None）。"""
    key = normalize_entity_name(name)
    if not key:
        return None
    for e in bible.entries:
        if normalize_entity_name(e.name) == key:
            return e
        if any(normalize_entity_name(a) == key for a in e.aliases):
            return e
    return None


def confirmed_entries(bible: StoryBible) -> List[BibleEntry]:
    """已确认条目（上下文注入只用已确认；候选仅供面板提示）。"""
    return [e for e in bible.entries if e.status == BIBLE_STATUS_CONFIRMED]


def by_type(bible: StoryBible, entity_type: str) -> List[BibleEntry]:
    """按类型筛选（保持 entries 顺序）。"""
    return [e for e in bible.entries if e.entity_type == entity_type]


__all__ = [
    "BIBLE_ENTITY_CHARACTER",
    "BIBLE_ENTITY_LOCATION",
    "BIBLE_ENTITY_PROP",
    "BIBLE_ENTITY_EVENT",
    "BIBLE_ENTITY_TYPES",
    "BIBLE_STATUS_CANDIDATE",
    "BIBLE_STATUS_CONFIRMED",
    "BIBLE_STATUSES",
    "BIBLE_SOURCE_AI",
    "BIBLE_SOURCE_RULE",
    "BIBLE_SOURCE_USER",
    "BIBLE_SOURCES",
    "BibleEntry",
    "StoryBible",
    "new_bible_entry_id",
    "entry_by_id",
    "find_entry",
    "confirmed_entries",
    "by_type",
    "normalize_entity_name",
]
