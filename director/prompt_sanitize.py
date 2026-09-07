#!/usr/bin/env python3
"""V1.7 P0-4：Prompt 内部标记清洗（2026-08-11 用户拍板）。

用户拍板：「shot_id/scene_id/镜头编号只能作结构元数据，不进 visual/camera/style/sound」。

背景：SPA 真实验收《山雨客栈》发现 AI Draft 出现「中景，缓慢推近镜头二、沈青崖……」
内部标记泄漏——Qwen 生成的 visual_intent 等文本字段里混入了「镜头二」这类镜头编号。

本模块提供统一清洗入口 `strip_internal_markers`，供 prompt_builder_v17 / camera_template
对**所有进入五区措辞**的文本字段（visual_intent / emotion / actions / subject / props）
做防御性清洗。设计原则：
- 只删「镜头编号」类内部标记（镜头+N / 第N镜 / shot_N / scene_N），保留正文含义；
  「远景镜头」这类「镜头」不带编号 → 不误删。
- 括号包裹的标记（【镜头一】/（第2镜）/(shot_1)）整体删除。
- 清洗后做残留标点清理（孤立逗号/顿号/冒号），避免「中景，缓慢推近、沈青崖」式残渣。

⛔ 显存铁律：纯规则、零 Ollama/GPU 依赖（与 asset_matcher / prompt_builder 同策略）。
"""

from __future__ import annotations

import re
from typing import Pattern, Sequence

__all__ = ["strip_internal_markers", "INTERNAL_MARKER_PATTERNS"]


def _p(*ps: str) -> Pattern[str]:
    return re.compile("|".join(ps), re.IGNORECASE)


# 编号用词（中文数字 + 阿拉伯数字）。
_NUM = r"[一二三四五六七八九十百\d]+"

# 括号包裹的内部标记：整体删除（【镜头一】/（第2镜）/（shot_1）/ [scene_01]）。
_PAREN_PATTERNS: Sequence[Pattern[str]] = [
    _p(
        r"[（(【\[]\s*(?:第\s*" + _NUM + r"\s*(?:个?\s*镜头|镜|幕)|镜头\s*[:：]?\s*" + _NUM + r"|shot\s*[-_]?\s*\d+|scene\s*[-_]?\s*\d+)\s*[）)】\]]"
    ),
]

# 裸露的内部标记（先删完整 scene:shot 组合，再删单段）。
_NAKED_PATTERNS: Sequence[Pattern[str]] = [
    # scene_01:shot_02 / scene_1：shot_2（组合，优先）
    _p(r"scene\s*[-_]?\s*\d+\s*[:：]\s*shot\s*[-_]?\s*\d+"),
    # 第X镜 / 第二个镜头 / 第3幕
    _p(r"第\s*" + _NUM + r"\s*(?:个?\s*镜头|镜|幕)"),
    # 镜头一 / 镜头1 / 镜头 二 / 镜头：2（含尾随标点）
    _p(r"镜头\s*[:：]?\s*" + _NUM + r"\s*[:：，,、]?"),
    # shot_1 / shot-1 / Shot 2 / SHOT3（含尾随标点）
    _p(r"shot\s*[-_]?\s*\d+\s*[:：，,、]?"),
    # scene_01 / scene-1 / Scene 2（含尾随标点）
    _p(r"scene\s*[-_]?\s*\d+\s*[:：，,、]?"),
]

INTERNAL_MARKER_PATTERNS: Sequence[Pattern[str]] = list(_PAREN_PATTERNS) + list(_NAKED_PATTERNS)

# 清洗后残留标点清理：多个顿号/逗号合一、首尾孤立标点剔除。
_JOIN_SEP = re.compile(r"\s*[、，,；;：:]+\s*")
_LEAD_PUNCT = re.compile(r"^\s*[、，,；;：:]+")
_TAIL_PUNCT = re.compile(r"[、，,；;：:]+\s*$")
_DUP_SEP = re.compile(r"([、，,；;：:])\1+")


def _clean_leftovers(text: str) -> str:
    """清洗后的标点残渣清理：避免「中景，缓慢推近、沈青崖」式孤立顿号/双逗号。"""
    out = text
    out = _JOIN_SEP.sub("，", out)
    out = _DUP_SEP.sub(r"\1", out)
    out = _LEAD_PUNCT.sub("", out)
    out = _TAIL_PUNCT.sub("", out)
    return out.strip()


def strip_internal_markers(text: str) -> str:
    """删除文本中的镜头编号 / 第X镜 / shot_id / scene_id 等内部标记。

    返回清洗后文本（保留正文）。空/非字符串原样返回。
    """
    if not text:
        return text
    out = str(text)
    for pat in INTERNAL_MARKER_PATTERNS:
        out = pat.sub("", out)
    cleaned = _clean_leftovers(out)
    # 清洗后可能只剩空白/孤立标点 → 视为空（不输出「，」之类空壳）。
    if cleaned in ("，", "、", "：", ":", ""):
        return ""
    return cleaned
