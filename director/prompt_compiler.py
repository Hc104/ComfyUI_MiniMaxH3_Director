#!/usr/bin/env python3
"""Prompt Compiler 层（生成约束层 v1.0，PROMPT_COMPILER_V1 §3.2，用户 2026-08-18 拍板）。

ShotPlan（画面执行指令的约束结构）→ **双语约束块**，注入 integrated_multimodal_description。
注入位置：`_build_description` 中「空间连续性块」之后、「光线/氛围」之前
（约束是画面硬边界，优先于装饰性描述）。

双语铁律（沿用 v1.0 spatial/防崩坏双语策略）：
- 中文行 = 可读 + 精确内容（角色/动作/镜头/禁止清单）；
- 英文行 = 结构化语义骨架（exactly N / single camera / no third character…），
  保 H3 执行稳；动作与禁止短语为原文中文（纯规则零 LLM 不虚译，混合行可接受）。

⛔ 纯规则零 LLM 零显存零网络（与 shot_plan/spatial_continuity 同策略）。
⛔ 只读 ShotPlan，绝不改写 source_text / retained_facts（原文事实最高优先级铁律）。

核心入口：
- `compile_constraint_block(plan)` → 多行字符串；空 plan / character_count==0 → 空串。
"""

from __future__ import annotations

from typing import List

from .shot_plan import ShotPlan

# 基础禁止清单的英文直译（结构化，H3 保执行稳）。
_BASE_FORBIDDEN_CN = (
    "画面中不得出现未列出的角色",
    "每个角色不得重复出现",
    "不得中途切换景别或机位",
    "场景不得改变",
)
_BASE_FORBIDDEN_EN = (
    "no character outside the listed cast",
    "no duplicate characters",
    "no shot-size or camera change mid-shot",
    "no scene change mid-shot",
)


# ---------------------------------------------------------------------------
# 单段渲染
# ---------------------------------------------------------------------------

def _char_names_cn(plan: ShotPlan) -> str:
    return "、".join(c.name for c in plan.character_locks if c.name)


def _char_names_en(plan: ShotPlan) -> str:
    return ", ".join(c.name for c in plan.character_locks if c.name)


def _action_timeline_cn(plan: ShotPlan) -> str:
    """动作顺序中文行：编号时间轴 + 核心动作标注。"""
    items = [a for a in plan.action_timeline if a.text]
    if not items:
        return ""
    parts = [f"{i}. {a.text}" for i, a in enumerate(items, 1)]
    primary = next((a.text for a in items if a.is_primary), "")
    head = f"动作顺序：{' '.join(parts)}"
    if primary:
        head += f"（核心动作：{primary}）"
    return head + "。"


def _action_timeline_en(plan: ShotPlan) -> str:
    """动作顺序英文行：strict order 骨架 + 原文动作（不虚译）。"""
    items = [a for a in plan.action_timeline if a.text]
    if not items:
        return ""
    parts = [f"{i}) {a.text}" for i, a in enumerate(items, 1)]
    primary = next((a.text for a in items if a.is_primary), "")
    out = f"Action sequence in strict order: {' → '.join(parts)}"
    if primary:
        out += f" Primary action: {primary}."
    return out


def _camera_state(plan: ShotPlan) -> str:
    cam = plan.camera_lock or {}
    shot_size = str(cam.get("shot_size", "") or "").strip()
    move = str(cam.get("movement", "") or "").strip()
    return " ".join(x for x in (shot_size, move) if x)


def _camera_cn(plan: ShotPlan) -> str:
    state = _camera_state(plan)
    if not state:
        return ""
    return f"镜头锁定：单机位 {state}，全镜不变，禁止中途切换景别或机位。"


def _camera_en(plan: ShotPlan) -> str:
    state = _camera_state(plan)
    if not state:
        return ""
    return f"Camera lock: single camera state ({state}) throughout. No shot-size or camera change."


def _forbidden_cn(plan: ShotPlan) -> str:
    items = plan.forbidden or []
    if not items:
        return ""
    return f"禁止：{'；'.join(items)}。"


def _forbidden_en(plan: ShotPlan) -> str:
    """英文禁止行：基础三条结构化直译 + 追加项（performance_lock 等）以中文原样附录。"""
    items = plan.forbidden or []
    if not items:
        return ""
    base_en = list(_BASE_FORBIDDEN_EN)
    extra = [x for x in items if x not in _BASE_FORBIDDEN_CN]
    parts = list(base_en)
    if extra:
        parts.append("follow Chinese restrictions strictly: " + "；".join(extra))
    return "Forbidden: " + ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# 总装配
# ---------------------------------------------------------------------------

def compile_constraint_block(plan: ShotPlan) -> str:
    """ShotPlan → 双语约束块（多行字符串；空 plan / 无角色 → 空串，调用方跳过）。

    段落顺序（与设计文档 §3.2 对齐）：
      角色锁定（含外观锁定，P2 有外观才注入）→ 空间锁定（复用双语块）→
      动作顺序 → 镜头锁定 → 禁止。
    """
    if plan is None or plan.character_count <= 0:
        return ""
    lines: List[str] = []

    # ---- Character Lock + Character Count ----
    names_cn = _char_names_cn(plan)
    names_en = _char_names_en(plan)
    if names_cn:
        lines.append(
            f"角色锁定：画面中只有 {plan.character_count} 名角色——{names_cn}；"
            "每名角色全程只出现一次，不得复制。"
        )
        lines.append(
            f"Character lock: exactly {plan.character_count} characters — {names_en}. "
            "Each appears exactly once, no duplicates."
        )

    # ---- 外观锁定（P2：Qwen 外观档案；空则不注入，绝不虚造外观）----
    for c in plan.character_locks:
        if c.appearance:
            lines.append(f"外观锁定：{c.name}={c.appearance}，全程不变。")
            lines.append(f"Appearance lock: {c.name} — {c.appearance}. Unchanged throughout.")

    # ---- Spatial Lock（复用 spatial_continuity 双语块，自带「空间连续性/Spatial」标签）----
    if plan.spatial_lock:
        lines.append(plan.spatial_lock)

    # ---- Action Timeline ----
    act_cn = _action_timeline_cn(plan)
    act_en = _action_timeline_en(plan)
    if act_cn:
        lines.append(act_cn)
        lines.append(act_en)

    # ---- Camera Lock ----
    cam_cn = _camera_cn(plan)
    cam_en = _camera_en(plan)
    if cam_cn:
        lines.append(cam_cn)
        lines.append(cam_en)

    # ---- Forbidden ----
    forb_cn = _forbidden_cn(plan)
    forb_en = _forbidden_en(plan)
    if forb_cn:
        lines.append(forb_cn)
        lines.append(forb_en)

    return "\n".join(lines)


__all__ = ["compile_constraint_block"]
