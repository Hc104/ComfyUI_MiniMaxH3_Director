#!/usr/bin/env python3
"""情绪曲线 + 镜头节奏（v2.0 ③）+ 反应镜硬规则（v2.0 ③′，用户 2026-08-17 拍板）。

命题：先理解整条情绪曲线（压迫→反击→反转→震惊→再压迫），再决定哪镜
特写 / 静止 / 留白 / 推镜——比「景别跟情绪走」更高一层的**镜头节奏导演**。

设计铁律（CAMERA_RULES_V2 §3.3 + §3.4）：
1. ``plan_rhythm``：按播放顺序（``resolve_timeline_order``）一次遍历全链，
   输出每镜 ``tension``(0-3) + ``rhythm``(build/peak/release/hold)。
2. ⛔ 关键修正（用户「峰值甚至不要动镜头」精确落地）：**不是张力越高越推**。
   **上升段（2→3）才推镜；峰值本身（3）定格静止**；释放段 pull-out 留白钩子。
3. ``plan_reaction_shots``（反应镜硬规则）：连续对白 ``dlg_streak>=2`` 或
   台词爆点后**强制下一镜 reaction**——不给听者安排台词，只给表情变化。
4. ⛔ 纯规则零 LLM / 零显存。

核心入口：
    plan_rhythm(plan) -> Dict[shot_id, Tuple[int, str]]
    plan_reaction_shots(plan, roles=None) -> Set[shot_id]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

# 对白爆发词（命中 → 台词爆点 → 下一镜强制 reaction）。
_BOMB_WORDS: Tuple[str, ...] = (
    "凭什么", "就这", "为什么", "竟然", "居然", "怎么敢", "难道",
    "你说什么", "你再说一遍", "滚", "闭嘴", "别过来", "不要",
    "我恨", "救命", "分手", "别走",
)

# 情绪关键词 → 基础张力档位（0-3）。
_TENSION_BY_EMOTION: Tuple[Tuple[str, int], ...] = (
    ("爆发", 3), ("暴怒", 3), ("愤怒", 3), ("震惊", 3), ("惊恐", 3), ("崩溃", 3),
    ("对峙", 3), ("决裂", 3), ("摊牌", 3),
    ("紧张", 2), ("压迫", 2), ("害怕", 2), ("恐惧", 2), ("焦躁", 2), ("凝重", 2),
    ("悲伤", 2), ("惊讶", 2), ("意外", 2), ("不安", 2),
    ("冷漠", 1), ("平淡", 1), ("轻蔑", 1), ("嘲讽", 1), ("得意", 1), ("从容", 1),
    ("平静", 0), ("温馨", 0), ("悠然", 0), ("日常", 0),
)

# 镜头功能 → 张力微调（establish/info 建立段压低，reaction 释放）。
_ROLE_TENSION_BOOST: Dict[str, int] = {
    "establish": 0,
    "ending": -1,
}


def _dialogue_bomb(shot: Any) -> bool:
    """对白含爆发标记（！？/质问词/情绪词）→ 台词爆点。"""
    for d in (shot.dialogue or []):
        t = str(getattr(d, "text", "") or "")
        if any(ch in t for ch in "！？!?"):
            return True
        if any(k in t for k in _BOMB_WORDS):
            return True
    return False


def _has_dialogue(shot: Any) -> bool:
    return bool([
        d for d in (shot.dialogue or [])
        if str(getattr(d, "text", "") or "").strip()
    ])


def _base_tension(shot: Any) -> int:
    """情绪关键词 → 0-3；台词爆点 → 3；普通对白 → 1；无 → 0。"""
    emo = str(getattr(shot, "emotion", "") or "").strip()
    for kw, t in _TENSION_BY_EMOTION:
        if kw in emo:
            return t
    if _dialogue_bomb(shot):
        return 3
    if _has_dialogue(shot):
        return 1
    return 0


def _role_adjusted_tension(shot: Any, t: int, role: str) -> int:
    """镜头功能微调张力档位（establish 建段压低 / ending 收束释放）。"""
    boost = _ROLE_TENSION_BOOST.get(role or "", 0)
    return max(0, min(3, t + boost))


def plan_rhythm(plan: Any, roles: Optional[Dict[str, str]] = None) -> Dict[str, Tuple[int, str]]:
    """全链张力曲线：{shot_id: (tension 0-3, rhythm)}，按播放顺序遍历一次。"""
    from .production_plan import resolve_timeline_order, shot_lookup  # noqa: PLC0415

    order = resolve_timeline_order(plan)
    lookup = shot_lookup(plan)
    out: Dict[str, Tuple[int, str]] = {}
    prev_t: Optional[int] = None
    prev_r = ""
    for sid, shid in order:
        key = f"{sid}:{shid}"
        item = lookup.get(key)
        if item is None:  # pragma: no cover - resolve_timeline_order 保证可定位
            continue
        _, shot = item
        t = _base_tension(shot)
        role = str((roles or {}).get(key, "") or "")
        t = _role_adjusted_tension(shot, t, role)
        if prev_t is None:
            r = "hold"
        elif t == 3 and prev_t == 2:
            r = "peak"          # 上升至峰值 → 定格静止
        elif t < prev_t:
            r = "release"       # 释放段 → pull-out 留白钩子
        elif t > prev_t:
            r = "build"         # 上升段 → 缓推
        else:
            r = "hold"
        out[key] = (t, r)
        prev_t, prev_r = t, r
    return out


def plan_reaction_shots(plan: Any, roles: Optional[Dict[str, str]] = None) -> Set[str]:
    """反应镜硬规则：{shot_id}——连续对白 ≥2 或台词爆点后，下一镜强制 reaction。

    - 硬触发 A：``dlg_streak >= 2`` 且当前镜有对白 → 下一镜强制 reaction；
    - 硬触发 B：当前镜是台词爆点 → 紧邻下一镜强制 reaction；
    - 被强制镜必须**有角色**（没人可拍就不强制）；
    - 返回被强制镜的 key 集合；调用方按需改写该镜 shot_role 为 reaction
      （P1 渲染：不给台词只给表情变化）。
    """
    from .production_plan import resolve_timeline_order, shot_lookup  # noqa: PLC0415

    order = resolve_timeline_order(plan)
    lookup = shot_lookup(plan)
    forced: Set[str] = set()
    dlg_streak = 0
    pending = False
    for sid, shid in order:
        key = f"{sid}:{shid}"
        item = lookup.get(key)
        if item is None:  # pragma: no cover
            continue
        _, shot = item
        if pending:
            has_cast = bool(getattr(shot, "characters", None) or [])
            if has_cast:
                forced.add(key)
        pending = False
        has_dlg = _has_dialogue(shot)
        is_bomb = _dialogue_bomb(shot)
        if has_dlg:
            dlg_streak += 1
            if dlg_streak >= 2 or is_bomb:
                pending = True
        else:
            dlg_streak = 0
    return forced


__all__ = [
    "plan_rhythm",
    "plan_reaction_shots",
    "_base_tension",
    "_dialogue_bomb",
    "_has_dialogue",
]
