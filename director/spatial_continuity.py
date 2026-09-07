#!/usr/bin/env python3
"""V1.0 运镜规则层·第一层硬约束：空间连续性（180° 轴线 + 左右位置 + 视线轴）。

用户拍板（2026-08-17《AI 漫剧导演运镜规则 v1.0》四决策）：
1. 初始左右位置 = **剧本原文方位词优先** → 出场顺序兜底（首角色左、次角色右）；
2. H3 渲染 = **中文 + 英文双语**（英文保 H3 执行稳，中文保可读）；
3. 中性台词静止优先（见 director_rules.py）；
4. 范围 = 只做规则层（一镜 = 一次 H3 生成，不做自动扩镜）。

防晕硬约束（《吐槽在漫画里封神》双人对白 A 左 B 右一旦跳轴观众直接晕）：
- 同场景内角色屏幕左右位置**一经建立永不翻转**（跳轴是硬错误）；
- 初始位置优先级：①剧本原文「X 在 Y 的左边/右边/对面/一侧」→ ②出场顺序；
- 过肩镜：前景主体在听者对侧（camera_on_axis 保持机位在 A–B 轴线）；
- 视线方向：A 看 B（B 在右）→ A 视线朝右；反打时 B 视线朝左；
- 场景切换 → 空间状态重置（新场景重新建立）；
- 交叉剪辑切回原场景 → 恢复该场景上次空间状态（与 scene_prev_state 同机制，
  由 build_plan_intents 按 scene_id 存取本模块状态）。

⛔ 显存铁律：纯规则、零 LLM、零 GPU、零网络（与 camera_template 同策略）。

核心入口：
- `SpatialState.from_shot(shot)` → 新场景首镜建立（剧本方位词 / 出场顺序）
- `state.update(chars)` → 新角色补位（已有角色绝不移位）
- `state.block()` → 双语空间连续性块（进 DirectorIntent.continuity.spatial.block，
  H3 渲染用）
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_SCREEN_LEFT = "screen_left"
_SCREEN_RIGHT = "screen_right"

# 剧本方位词关系表（X <verb> Y 的? <suffix> → X=a_side, Y=b_side）。
# 支持「X 在 Y 的左边/右侧/对面/一侧/身侧」等口语表述。
# 动词组固定用最短表意（在/位于/站在/坐在/蹲在/立在）；
# 名字不写死字符类——由出场名单动态构造（见 _parse_script_positions），
# 避免「坐在」首字被吞进名字（顾琰宸坐）导致方位词失效。
_VERBS = r"(?:在|位于|站在|坐在|蹲在|立在)"
_RELATIONS: List[Dict[str, Any]] = [
    {
        "suffix": r"左边|左侧|左方|左手边",
        "a_side": _SCREEN_LEFT,
        "b_side": _SCREEN_RIGHT,
    },
    {
        "suffix": r"右边|右侧|右方|右手边",
        "a_side": _SCREEN_RIGHT,
        "b_side": _SCREEN_LEFT,
    },
    {
        "suffix": r"对面|对侧|另一侧|一旁|身侧|跟前",
        "a_side": _SCREEN_LEFT,
        "b_side": _SCREEN_RIGHT,
    },
]


class SpatialState:
    """场景级空间状态：角色 → 屏幕左右位置 + 视线轴（纯规则）。

    同一 SpatialState 实例全程不翻转已有位置；新角色只补空位。
    """

    def __init__(
        self,
        axis: Optional[Dict[str, str]] = None,
        eye_line: str = "",
        camera_on_axis: bool = True,
    ) -> None:
        self.axis: Dict[str, str] = dict(axis or {})  # char -> screen_left/right
        self.eye_line: str = eye_line or ""            # "a->b"（a 看向 b）
        self.camera_on_axis: bool = camera_on_axis

    # ---------- 建立（新场景首镜） ----------

    @classmethod
    def from_shot(cls, shot: Any) -> "SpatialState":
        """新场景首镜建立：剧本方位词优先，出场顺序兜底。"""
        chars = [str(c.name).strip() for c in (shot.characters or []) if str(c.name).strip()]
        text = " ".join(
            str(x).strip() for x in (
                getattr(shot, "source_text", "") or "",
                getattr(shot, "visual_intent", "") or "",
            ) if str(x).strip()
        )
        st = cls()
        _parse_script_positions(st, text, chars)
        if len(st.axis) < 2 and len(chars) >= 2:
            # 出场顺序兜底：首角色左、次角色右（仅当两者均未定位）。
            if st.axis.get(chars[0]) is None and st.axis.get(chars[1]) is None:
                st.axis[chars[0]] = _SCREEN_LEFT
                st.axis[chars[1]] = _SCREEN_RIGHT
        st._finalize(chars)
        return st

    # ---------- 更新（后续镜补位 / 永不翻转） ----------

    def update(self, shot: Any) -> None:
        """后续镜：只补新角色到空位，已定位角色绝不移位（跳轴硬错误）。"""
        chars = [str(c.name).strip() for c in (shot.characters or []) if str(c.name).strip()]
        for ch in chars:
            if ch in self.axis:
                continue  # 已有位置 → 保持（不翻转）
        # 新角色补位：与已定位角色关系未知 → 依出场顺序补到空侧。
        if len(chars) >= 2:
            if self.axis.get(chars[0]) is None and self.axis.get(chars[1]) is None:
                # 两位都是新角色（如新角色登场对话）
                sides = [s for s in (_SCREEN_LEFT, _SCREEN_RIGHT)
                         if s not in self.axis.values()]
                if len(sides) >= 2:
                    self.axis[chars[0]] = sides[0]
                    self.axis[chars[1]] = sides[1]
        self._finalize(chars)

    def _finalize(self, chars: List[str]) -> None:
        """补视线轴：轴上有且仅有两个已定位角色 → a 左 b 右 → eye_line a->b。"""
        present = [c for c in chars if self.axis.get(c)]
        if len(present) >= 2:
            left = next((c for c in present if self.axis.get(c) == _SCREEN_LEFT), "")
            right = next((c for c in present if self.axis.get(c) == _SCREEN_RIGHT), "")
            if left and right:
                self.eye_line = f"{left}->{right}"
                self.camera_on_axis = True

    # ---------- 输出 ----------

    def block(self) -> str:
        """双语空间连续性块（≥2 已定位角色才输出；否则空串）。"""
        left = [c for c, s in self.axis.items() if s == _SCREEN_LEFT]
        right = [c for c, s in self.axis.items() if s == _SCREEN_RIGHT]
        if not left or not right:
            return ""
        l = left[0]
        r = right[0]
        line_cn = f"{l}在屏幕左侧，{r}在屏幕右侧"
        if self.eye_line:
            line_cn += f"，视线轴固定（{self.eye_line.replace('->', '→')}）"
        line_en = f"{l}: screen left. {r}: screen right. Eye-line axis: fixed"
        if self.eye_line:
            a, b = self.eye_line.split("->")
            line_en += f" ({a}–{b})"
        line_en += ". Camera remains on the axis."
        return f"空间连续性：{line_cn}。Spatial Continuity: {line_en}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": dict(self.axis),
            "eye_line": self.eye_line,
            "camera_on_axis": self.camera_on_axis,
            "block": self.block(),
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"SpatialState(axis={self.axis!r}, eye_line={self.eye_line!r})"


def _parse_script_positions(
    st: SpatialState,
    text: str,
    chars: List[str],
) -> None:
    """剧本方位词解析（只在两位角色都在出场名单内时落地）。

    ⛔ 关键：名字不写死「任意汉字」字符类，而是用出场名单动态构造 alternation——
    否则「顾琰宸坐在林薇薇右侧」会把动词首字「坐」吞进名字（顾琰宸坐），
    或因 B 贪婪吞「的」（顾琰宸的）导致方位词失效、落入出场顺序兜底。
    名字按长度降序排列（长名优先），避免「顾琰」被「顾琰宸」吞掉。
    """
    if len(chars) < 2:
        return
    name_pat = "|".join(re.escape(c) for c in sorted(chars, key=len, reverse=True))
    for rel in _RELATIONS:
        pat = re.compile(
            rf"(?P<a>{name_pat}){_VERBS}(?P<b>{name_pat})的?(?:{rel['suffix']})"
        )
        for m in pat.finditer(text):
            a, b = m.group("a"), m.group("b")
            if a not in chars or b not in chars:
                continue
            if st.axis.get(a) is not None or st.axis.get(b) is not None:
                continue  # 已被更高优先级定位
            st.axis[a] = rel["a_side"]
            st.axis[b] = rel["b_side"]
            return  # 只取第一处有效方位，避免多镜文本互相打架
