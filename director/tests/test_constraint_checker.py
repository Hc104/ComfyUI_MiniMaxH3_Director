#!/usr/bin/env python3
"""镜头硬约束检查器（生成约束层 v1.0）单元测试（纯规则，mock，零显存）。

覆盖（PROMPT_COMPILER_V1 §3.3 + §6 验收）：
1. 失败案例回放：multi_shot_size / character_count / multi_primary_action /
   scene_locked / performance 全量命中；
2. 缺失场景：missing_character_count / missing_primary_action / empty_camera；
3. character_duplication 防御检查（from_dict 带重名 locks 场景）；
4. check_intent（路由入口）从 DirectorIntent 重建检查；
5. None 防御；
6. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.constraint_checker as cc  # noqa: E402
from director.shot_plan import build_shot_plan, ShotPlan, CharacterLock  # noqa: E402
from director.director_intent import DirectorIntent  # noqa: E402


def _intent() -> DirectorIntent:
    return DirectorIntent(
        shot_id="scene_01:shot_01",
        scene_id="scene_01",
        user_original_intent="顾琰宸从西装内袋掏出支票，将支票从手中甩落到林薇薇面前的桌面，支票落桌后轻微滑动，林薇薇没有伸手去拿，中景拍摄顾琰宸甩出支票，特写支票滑动至桌面，林薇薇保持静止，冷漠。",
        retained_facts=[
            "顾琰宸从西装内袋掏出支票",
            "将支票从手中甩落到林薇薇面前的桌面",
            "支票落桌后轻微滑动",
            "林薇薇没有伸手去拿",
            "林薇薇保持静止，冷漠",
        ],
        subject="顾琰宸、林薇薇",
        characters=["顾琰宸", "林薇薇"],
        location="豪华私人办公室",
        action=["掏出支票", "甩落桌面", "林薇薇没有伸手去拿"],
        emotion="冷漠",
        composition="中景拍摄顾琰宸甩出支票，特写支票滑动至桌面",
        camera_position="中景",
        movement="固定机位",
        core_action="甩落桌面",
        continuity={
            "spatial": {"block": "空间连续性：顾琰宸在屏幕左侧，林薇薇在屏幕右侧，视线轴固定（顾琰宸→林薇薇）。Spatial Continuity: 顾琰宸: screen left. 林薇薇: screen right."},
        },
    )


def _codes(plan: ShotPlan) -> set:
    return {i.code for i in cc.check_shot_plan(plan)}


# ---------------------------------------------------------------------------
# 失败案例回放（§6 验收）
# ---------------------------------------------------------------------------

def test_failure_case_codes() -> None:
    codes = _codes(build_shot_plan(_intent()))
    assert "multi_shot_size" in codes
    assert "character_count" in codes
    assert "multi_primary_action" in codes
    assert "scene_locked" in codes
    assert "performance" in codes


def test_failure_case_messages() -> None:
    issues = cc.check_shot_plan(build_shot_plan(_intent()))
    msgs = {i.message for i in issues}
    assert any("中景 + 特写" in m and "拆成两个 Shot" in m for m in msgs)
    assert any("角色数量=2" in m and "Exactly 2 characters" in m for m in msgs)
    assert any("核心=甩落桌面" in m for m in msgs)
    assert any("本镜场景锁定：豪华私人办公室" in m for m in msgs)
    assert any("林薇薇不得伸手去拿" in m and "原文否定句" in m for m in msgs)


def test_failure_case_all_warning_has_fix() -> None:
    issues = cc.check_shot_plan(build_shot_plan(_intent()))
    warns = [i for i in issues if i.severity == "warning"]
    assert warns, "失败案例应至少有一条 warning"
    for w in warns:
        assert w.fix, f"warning {w.code} 缺建议动作 fix"


# ---------------------------------------------------------------------------
# 缺失场景
# ---------------------------------------------------------------------------

def test_missing_character_count() -> None:
    it = _intent()
    it.characters = []
    codes = _codes(build_shot_plan(it))
    assert "missing_character_count" in codes
    assert "character_count" not in codes


def test_missing_primary_action() -> None:
    it = _intent()
    it.action = []
    it.core_action = ""
    codes = _codes(build_shot_plan(it))
    assert "missing_primary_action" in codes
    assert "multi_primary_action" not in codes


def test_empty_camera() -> None:
    it = _intent()
    it.camera_position = ""
    it.movement = ""
    codes = _codes(build_shot_plan(it))
    assert "empty_camera" in codes


def test_no_scene_no_scene_locked() -> None:
    it = _intent()
    it.location = ""
    codes = _codes(build_shot_plan(it))
    assert "scene_locked" not in codes


# ---------------------------------------------------------------------------
# character_duplication（防御 from_dict 带重名 locks）
# ---------------------------------------------------------------------------

def test_character_duplication_defensive() -> None:
    plan = ShotPlan(
        character_count=2,
        character_locks=[CharacterLock(name="A"), CharacterLock(name="A")],
    )
    assert "character_duplication" in _codes(plan)


# ---------------------------------------------------------------------------
# 路由入口 check_intent / None 防御
# ---------------------------------------------------------------------------

def test_check_intent_from_intent() -> None:
    issues = cc.check_intent(_intent())
    assert issues
    assert "multi_shot_size" in {i.code for i in issues}


def test_none_defense() -> None:
    assert cc.check_shot_plan(None) == []
    assert cc.check_intent(None) == []


# ---------------------------------------------------------------------------
# 零 GPU 副作用
# ---------------------------------------------------------------------------

def test_no_gpu_side_effects() -> None:
    import inspect
    src = inspect.getsource(cc)
    for banned in ("torch", "ollama", "requests", "http://", "comfy.samplers"):
        assert banned not in src, f"constraint_checker.py 引用了禁止依赖 {banned}"


if __name__ == "__main__":
    import traceback
    passed = 0
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"  ✓ {name}")
            except Exception:
                failed += 1
                print(f"  ✗ {name}")
                traceback.print_exc()
    print(f"\n结果：{passed} PASS / {failed} FAIL")
    sys.exit(1 if failed else 0)
