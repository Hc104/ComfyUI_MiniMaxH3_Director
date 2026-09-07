#!/usr/bin/env python3
"""单镜头单核心动作（v2.0 ①，用户 2026-08-17 拍板）。

命题：一个镜头 = 一个主要视觉事件。即使原文有多个动作，本镜只渲染核心那个，
其余降级为辅助提示（AI 补全层），不进「核心动作」句。

设计铁律（CAMERA_RULES_V2 §3.1）：
1. 动词优先级排序：强动作 > 表情动作 > 姿态 > 环境。
2. 折叠连续动作：同主语、语义连续的多个强动作合为一个核心事件（过程链）。
3. 镜头功能裁决：reaction → 取听者反应动作（表情类）；emotion → 情绪者动作；
   info / 其他 → 说话者 / 主动作。
4. 动作过程措辞：静态 / 结果性动词改写为动作过程（同一套过程化风格）。
5. ⛔ 纯规则零 LLM / 零显存；``retained_facts`` 原文逐字保底不被触碰
   （core_action 是**新增派生层**，绝不改写入 user_original_intent）。
6. 失败兜底：纯环境镜 / 无动作 → ("", "")，渲染层跳过该句（严格向后兼容）。

核心入口：
    extract_core_action(shot, role="") -> (core_action: str, core_subject: str)
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

# ---------- 动词优先级表 ----------

# 强动作词根（产生位移或物体交互）→ 核心事件候选（档位 0）。
_STRONG_VERBS: tuple[str, ...] = (
    "拿起", "捏起", "捡起", "甩落", "甩", "扔", "掷", "丢",
    "推门", "推开", "推", "开门", "关门", "打开",
    "拔剑", "拔刀", "抽出", "拔出",
    "追上", "奔跑", "狂奔", "冲", "闯", "逃", "跑", "走", "迈步", "踏",
    "跪", "跪下", "转身", "回头", "俯身", "弯腰", "蹲", "起身", "站起", "坐下",
    "抬手", "伸手", "收回", "抓住", "握住", "搂", "抱", "扇", "打", "掐",
    "挡", "躲", "闪", "退", "后退",
    "翻", "撕", "放下", "倒", "敲", "拍", "拍桌",
    "接过", "递给", "递", "塞", "塞进", "掏", "掏出", "摸", "拿出", "取出",
    "拉开", "拉", "扶", "踩", "踢", "踹", "撞", "按", "按到", "丢下",
    "甩手", "挥手", "举", "举起", "抬到", "抬起", "抬了", "握起", "捏着", "捻", "划", "点",
    "弹出", "扔回", "甩回", "放回", "收回",
)

# 表情动作词根（面部/手部微动作）→ 次选（档位 1，无强动作时作核心）。
_EXPRESSION_VERBS: tuple[str, ...] = (
    "抬眼", "抬眸", "垂眸", "垂眼", "低头", "抬头", "偏头", "侧头",
    "抿唇", "抿嘴", "皱眉", "蹙眉", "眉梢", "挑眉",
    "攥紧", "握拳", "握紧", "咬牙", "咬唇",
    "勾唇", "嘴角上扬", "嘴角", "撇嘴", "嗤笑", "轻笑", "冷笑", "干笑",
    "瞳孔微缩", "瞳孔一缩", "瞳孔", "眼神", "目光", "眼底",
    "神色", "表情", "脸色", "面部", "五官", "眉目",
    "呼吸", "喘", "怔住", "愣住", "僵住", "一顿", "屏息",
    "颤抖", "发抖", "颤", "落泪", "泛红", "泛白", "绷紧",
)

# 姿态词根（静态，档位 2）→ 不选为核心（交给场景 / 镜头承载）。
_STATIC_WORDS: tuple[str, ...] = (
    "站着", "站定", "坐在", "伫立", "静立", "沉默", "安静", "静静",
    "望着", "看着", "盯着", "注视", "凝视", "注视", "望向", "看向",
)

# 动作过程措辞表（静态/结果性动作 → 过程化描述；与 psych_visualize 同风格）。
_STATIC_PROCESS: tuple[tuple[str, str], ...] = (
    ("握拳", "垂在身侧的手缓缓收紧，指节逐渐绷紧"),
    ("握紧", "握紧的手缓缓收紧，指节逐渐泛白"),
    ("攥紧", "指尖逐渐收紧，指节泛白"),
    ("沉默", "一言不发，目光晦暗地垂下"),
    ("站着", "静静伫立，身形纹丝不动"),
    ("看着", "目光缓缓落在对方脸上"),
    ("盯着", "视线定定地锁在对方身上"),
)

# 兜底：角色名后常跟的助词/结构，折叠去主语时连带头部一并剥掉。
_SUBJECT_TRIM: tuple[str, ...] = ("的", "地", "从", "将", "把", "向", "对", "在", "抬头", "缓缓", "猛地")


def _names(shot: Any) -> List[str]:
    """角色名列表（SimpleNamespace / dataclass 均兼容）。"""
    names: List[str] = []
    for c in (getattr(shot, "characters", None) or []):
        n = str(getattr(c, "name", "") or "").strip()
        if n and n not in names:
            names.append(n)
    return names


def _classify(action: str) -> int:
    """动作 → 优先级档位：0=强动作 1=表情动作 2=静态 3=环境/未知。"""
    for v in _STRONG_VERBS:
        if v in action:
            return 0
    for v in _EXPRESSION_VERBS:
        if v in action:
            return 1
    for v in _STATIC_WORDS:
        if v in action:
            return 2
    return 3


def _subject_of(action: str, names: List[str]) -> str:
    """动作开头命中的角色名；无则空串。"""
    for n in names:
        if action.startswith(n):
            return n
    return ""


def _strip_subject(action: str, names: List[str]) -> str:
    """剥掉动作开头的角色名（含紧跟的助词结构），保留动词短语。"""
    for n in names:
        if action.startswith(n):
            rest = action[len(n):]
            for t in _SUBJECT_TRIM:
                if rest.startswith(t):
                    rest = rest[len(t):]
            return rest.strip(" ，,。")
    return action


def _processify(action: str) -> str:
    """静态/结果性动作 → 动作过程措辞（整句替换式，保留过程语义）。"""
    for verb, phrase in _STATIC_PROCESS:
        if verb in action:
            return phrase
    return action


def _fold(actions: List[str], best_idx: int, names: List[str]) -> str:
    """折叠连续强动作：同主语、紧邻的强动作并入核心，形成「过程链」。

    ⛔ 最多并 2 个后续动作；后续动作必须仍是强动作、且主语与核心相同
    （无主语动作视为延续核心主语），避免把另一个角色的动作卷进来。
    """
    parts: List[str] = [actions[best_idx]]
    subj = _subject_of(actions[best_idx], names)
    for a in actions[best_idx + 1:best_idx + 3]:
        if _classify(a) != 0:
            break
        a_subj = _subject_of(a, names)
        if subj and a_subj and a_subj != subj:
            break
        parts.append(_strip_subject(a, names))
    return "，".join(parts)


def extract_core_action(shot: Any, role: str = "") -> Tuple[str, str]:
    """单镜 → (核心动作, 核心主体)。

    - 无动作（纯环境镜）→ ("", "")（渲染层跳过，严格向后兼容）；
    - reaction 镜 → 优先表情类动作（拍听众，不给强动作不配台词）；
    - 其他 → 强动作 > 表情动作；姿态/环境不选为核心。
    """
    actions: List[str] = [
        str(a).strip() for a in (getattr(shot, "actions", None) or []) if str(a).strip()
    ]
    if not actions:
        return "", ""
    names = _names(shot)
    role = str(role or "")

    if role == "reaction":
        # 反应镜：观众该看听者的表情变化——优先表情动作，不选强动作。
        for a in actions:
            if _classify(a) == 1:
                return _processify(a), _subject_of(a, names)
        return "", ""

    best_idx: int = -1
    best_cls: int = 99
    for i, a in enumerate(actions):
        cls = _classify(a)
        if cls < best_cls:
            best_cls, best_idx = cls, i
        if cls == 0:
            break  # 动作序列按原文顺序 → 第一个强动作即核心
    if best_idx < 0 or best_cls >= 2:
        # 姿态（档位 2）/ 环境（档位 3）不选为核心——姿态交给场景/镜头承载。
        return "", ""
    core = actions[best_idx]
    if best_cls == 0:
        core = _fold(actions, best_idx, names)
    else:
        # 无强动作 → 表情动作为核心，静态姿态过程化。
        core = _processify(core)
    return core, _subject_of(core, names)


__all__ = [
    "extract_core_action",
    "_classify",
    "_fold",
    "_processify",
    "_names",
    "_subject_of",
]
