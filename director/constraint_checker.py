#!/usr/bin/env python3
"""镜头硬约束检查器（生成约束层 v1.0，PROMPT_COMPILER_V1 §3.3，用户 2026-08-18 拍板）。

送 H3 前对 ShotPlan 做硬约束检查：**报告 + 建议**，不改原文（与 psych_visualize
同铁律）。自动补全（Exactly N / 拆主次）只影响编译输出，绝不动 source_text / retained_facts。

severity：warning（可处理）| info（确认）；当前阶段无阻断式 error。
路由入口：`check_intent(intent)`（从 DirectorIntent 重建检查）；
纯函数入口：`check_shot_plan(plan)`。

⛔ 纯规则零 LLM 零显存零网络（与 shot_plan/prompt_compiler 同策略）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .shot_plan import ShotPlan, build_shot_plan


@dataclass
class ConstraintIssue:
    severity: str = "warning"   # warning | info
    code: str = ""              # multi_shot_size / missing_character_count / ...
    message: str = ""           # 中文提示（用户可见）
    fix: str = ""               # 建议动作（可为空）


def check_shot_plan(plan: ShotPlan) -> List[ConstraintIssue]:
    """纯规则检查：ShotPlan → 检查清单。空 plan → 空列表。"""
    if plan is None:
        return []
    issues: List[ConstraintIssue] = []

    # ---- multi_shot_size（一镜多景别 → 拆镜建议；用户拍板 Q3：警告+提示拆镜，不阻断）----
    if len(plan.shot_sizes_seen) >= 2:
        sizes = " + ".join(plan.shot_sizes_seen)
        issues.append(ConstraintIssue(
            severity="warning",
            code="multi_shot_size",
            message=f"⚠️ 当前镜头包含 {sizes}，请拆成两个 Shot",
            fix="把本镜按景别拆成两个独立 Shot，每个 Shot 只保留一个景别",
        ))

    # ---- character_count（缺失 → warning + 自动补；有值 → info 确认）----
    if plan.character_count <= 0:
        issues.append(ConstraintIssue(
            severity="warning",
            code="missing_character_count",
            message="角色数量未明确，送 H3 前自动补「Exactly N characters」",
            fix="补充出场角色名单；无角色时按主体补 1 名",
        ))
    else:
        issues.append(ConstraintIssue(
            severity="info",
            code="character_count",
            message=f"角色数量={plan.character_count}，送 H3 自动补 Exactly {plan.character_count} characters",
        ))

    # ---- character_duplication（去重提示；build_shot_plan 已去重，防御 from_dict 场景）----
    names = [c.name for c in plan.character_locks if c.name]
    if len(names) != len(set(names)):
        issues.append(ConstraintIssue(
            severity="info",
            code="character_duplication",
            message="出场角色名单含重复项，已去重",
        ))

    # ---- multi_primary_action / missing_primary_action ----
    actions = [a.text for a in plan.action_timeline if a.text]
    if len(actions) >= 2:
        primary = next(
            (a.text for a in plan.action_timeline if a.is_primary and a.text),
            actions[0],
        )
        issues.append(ConstraintIssue(
            severity="warning",
            code="multi_primary_action",
            message=f"本镜有多个核心动作候选，已自动选主/次（核心={primary}）",
            fix="保留一个核心动作，其余降为次级动作",
        ))
    elif not actions:
        issues.append(ConstraintIssue(
            severity="warning",
            code="missing_primary_action",
            message="本镜缺少核心动作，请补充明确动作",
            fix="补充本镜最核心的视觉事件",
        ))

    # ---- empty_camera（景别/运镜皆空 → H3 可能自行切景别）----
    cam = plan.camera_lock or {}
    if not (cam.get("shot_size") or cam.get("movement") or cam.get("cn")):
        issues.append(ConstraintIssue(
            severity="warning",
            code="empty_camera",
            message="本镜缺少机位设置，H3 可能自行切景别",
            fix="设置景别与运镜（一镜一机位）",
        ))

    # ---- scene_locked（info 确认；location 为空则跳过）----
    if plan.location:
        issues.append(ConstraintIssue(
            severity="info",
            code="scene_locked",
            message=f"本镜场景锁定：{plan.location}",
        ))

    # ---- performance（原文否定句 → 禁止动作确认，每条一条 info）----
    for item in plan.performance_lock:
        issues.append(ConstraintIssue(
            severity="info",
            code="performance",
            message=f"{item}（原文否定句）",
        ))

    return issues


def check_intent(intent: Any) -> List[ConstraintIssue]:
    """从 DirectorIntent（dataclass 或 dict）重建 ShotPlan 并检查（路由用）。"""
    if intent is None:
        return []
    return check_shot_plan(build_shot_plan(intent))


def build_constraint_check(intents: Any) -> Dict[str, List[Dict[str, Any]]]:
    """送 H3 前的镜头硬约束检查报告（PROMPT_COMPILER_V1 §3.3，路由用纯函数）。

    入参：{shot_key: DirectorIntent}（build_plan_intents 输出）；
    出参：{shot_key: [ConstraintIssue dict]}，仅记录有 issue 的镜头。
    纯规则零显存零 LLM；None/空 → 空 dict。
    """
    report: Dict[str, List[Dict[str, Any]]] = {}
    if not intents:
        return report
    for key, intent in intents.items():
        issues = check_intent(intent)
        if issues:
            report[key] = [asdict(i) for i in issues]
    return report


__all__ = ["ConstraintIssue", "check_shot_plan", "check_intent", "build_constraint_check"]
