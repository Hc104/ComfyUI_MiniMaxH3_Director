#!/usr/bin/env python3
"""小说心理描写视觉化（v2.0 ②，用户 2026-08-17 拍板）。

命题：小说心理描写（「她心里一阵慌乱」）不能直接进 H3——H3 不知道拍什么；
先转换成「动作 / 表情的可视变化」（「眼神短暂躲闪，睫毛轻颤」）。

设计铁律（CAMERA_RULES_V2 §3.2 + P2）：
1. 心理词探测：``source_text`` / ``emotion`` 命中心理词表 → 激活可视化。
2. 动作 / 表情映射库：心理关键词 → (表情变化句, 动作过程短语)。
3. ⛔ 铁律：**绝不改写原文**。``retained_facts`` 逐字保底不被触碰；
   心理可视化是**新增的规则层**（进 ``ai_supplement`` / ``rule_generated`` provenance），
   追加在原文事实之后，与「AI 只补充不覆盖」一致。
4. 无心理词 → 输出空（渲染层跳过，严格向后兼容）。
5. ⛔ 纯规则零 LLM / 零显存。

P2（2026-08-18，「软约束转硬输入」）：
    ``tag_psych(shot)`` 把命中的心理词**预标记**到 ``shot.psych_tags``（script_analyzer
    分析阶段落盘），``visualize_psych`` / ``psych_process_action`` **优先消费硬输入**
    （``psych_tags``），无标记才回退运行时扫描 —— 心理词不依赖运行时原文仍在。

核心入口：
    visualize_psych(shot) -> str            # 表情变化句（expression），无心理词 → ""
    psych_process_action(shot) -> str       # 动作过程短语（可并入 core_action），无 → ""
    tag_psych(shot) -> List[str]            # 预标记：本镜全部命中的心理词（保序去重）
    tag_psych_text(text) -> List[str]       # 单文本扫描命中词
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# 心理关键词 → (表情变化 expression, 动作过程 action_process)。
# 顺序即优先级：前面的词先命中先返回。
PSYCH_MAP: Dict[str, Tuple[str, str]] = {
    # 慌乱 / 紧张 / 心慌
    "慌乱": ("眼神短暂躲闪，睫毛轻颤", "呼吸变急，手指不自觉攥紧衣摆"),
    "心慌": ("眼神短暂躲闪，睫毛轻颤", "呼吸变急，手指不自觉攥紧衣摆"),
    "惊慌": ("眼神短暂躲闪，睫毛轻颤", "呼吸变急，手指不自觉攥紧衣摆"),
    "紧张": ("眼神短暂躲闪，睫毛轻颤", "呼吸变急，手指不自觉攥紧衣摆"),
    # 震惊 / 惊愕 / 愣住
    "震惊": ("瞳孔微缩，表情短暂凝滞", "动作一顿，身体微微僵住"),
    "惊愕": ("瞳孔微缩，表情短暂凝滞", "动作一顿，身体微微僵住"),
    "愣住": ("瞳孔微缩，表情短暂凝滞", "动作一顿，身体微微僵住"),
    # 得意 / 从容 / 嘲讽
    "得意": ("嘴角浮现一抹嘲讽的弧度", "目光直视对方，缓缓抬眸"),
    "从容": ("嘴角浮现一抹嘲讽的弧度", "目光直视对方，缓缓抬眸"),
    "嘲讽": ("嘴角浮现一抹嘲讽的弧度", "目光直视对方，缓缓抬眸"),
    "冷笑": ("嘴角浮现一抹嘲讽的弧度", "目光直视对方，缓缓抬眸"),
    # 愤怒 / 压抑怒火
    "愤怒": ("下颌绷紧，眼底翻涌", "垂在身侧的手缓缓收紧，指节逐渐绷紧"),
    "怒火": ("下颌绷紧，眼底翻涌", "垂在身侧的手缓缓收紧，指节逐渐绷紧"),
    "压抑": ("下颌绷紧，眼底翻涌", "垂在身侧的手缓缓收紧，指节逐渐绷紧"),
    # 悲伤 / 落寞
    "悲伤": ("目光低垂，眼底微红", "指尖在杯沿无意识摩挲"),
    "落寞": ("目光低垂，眼底微红", "指尖在杯沿无意识摩挲"),
    # 害怕 / 恐惧
    "害怕": ("瞳孔微缩，嘴唇抿紧", "脚步下意识后退半步，肩线微微绷紧"),
    "恐惧": ("瞳孔微缩，嘴唇抿紧", "脚步下意识后退半步，肩线微微绷紧"),
    # 心动 / 羞涩
    "心动": ("眼神一颤，耳根泛红", "指尖无意识摩挲衣角"),
    "羞涩": ("眼神一颤，耳根泛红", "指尖无意识摩挲衣角"),
    # 轻蔑 / 不屑
    "轻蔑": ("嘴角勾起一抹轻蔑的弧度", "眼神居高临下地扫过"),
    "不屑": ("嘴角勾起一抹轻蔑的弧度", "眼神居高临下地扫过"),
}


def _tags_of(shot: Any) -> List[str]:
    """``shot.psych_tags``（P2 硬输入预标记）；缺字段/空 → []（向后兼容）。"""
    tags = getattr(shot, "psych_tags", None) or []
    return [str(t).strip() for t in tags if str(t).strip()]


def tag_psych_text(text: str) -> List[str]:
    """单文本 → 命中的心理词列表（PSYCH_MAP key，按映射库顺序，去重）。"""
    if not text:
        return []
    return [kw for kw in PSYCH_MAP if kw in text]


def tag_psych(shot: Any) -> List[str]:
    """本镜全部命中的心理词（预标记，P2 硬输入源）。

    扫描源：``source_text`` 优先 → ``emotion`` → ``actions`` → ``visual_intent``
    （原文心理描写永远优先于 AI 情绪标注；保序去重）。
    """
    src = str(getattr(shot, "source_text", "") or "").strip()
    emo = str(getattr(shot, "emotion", "") or "").strip()
    actions = [str(a) for a in (getattr(shot, "actions", None) or [])]
    vi = str(getattr(shot, "visual_intent", "") or "").strip()
    seen: set = set()
    out: List[str] = []
    for text in (src, emo, *actions, vi):
        for kw in tag_psych_text(text):
            if kw not in seen:
                seen.add(kw)
                out.append(kw)
    return out


def _psych_hit(shot: Any) -> Tuple[str, Tuple[str, str]]:
    """命中心理词 → (命中词, (expression, action_process))；无 → ("", None)。

    P2 硬输入优先：先读 ``shot.psych_tags``（script_analyzer 预标记），
    命中即返回；无预标记才回退运行时扫描 ``emotion`` + ``source_text``
    （旧行为逐字节保留，严格向后兼容）。
    """
    for tag in _tags_of(shot):
        if tag in PSYCH_MAP:
            return tag, PSYCH_MAP[tag]
    emo = str(getattr(shot, "emotion", "") or "").strip()
    src = str(getattr(shot, "source_text", "") or "").strip()
    text = f"{emo} {src}"
    for kw, pair in PSYCH_MAP.items():
        if kw in text:
            return kw, pair
    return "", None


def visualize_psych(shot: Any) -> str:
    """本镜心理 → 表情变化句（expression）。无心理词 → ""（渲染层跳过）。"""
    kw, pair = _psych_hit(shot)
    return pair[0] if pair else ""


def psych_process_action(shot: Any) -> str:
    """本镜心理 → 动作过程短语（可并入 core_action）。无 → ""。"""
    kw, pair = _psych_hit(shot)
    return pair[1] if pair else ""


__all__ = [
    "visualize_psych",
    "psych_process_action",
    "tag_psych",
    "tag_psych_text",
    "PSYCH_MAP",
]
