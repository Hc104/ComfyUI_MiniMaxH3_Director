#!/usr/bin/env python3
"""Prompt Compiler 层（生成约束层 v1.0）单元测试（纯规则，mock，零显存）。

覆盖（PROMPT_COMPILER_V1 §3.2）：
1. 失败案例回放：双语句双语约束块（角色锁定/动作顺序/镜头锁定/禁止/空间复用）；
2. 空 plan / 无角色 → 空串（调用方跳过，向后兼容）；
3. 外观锁定：有 appearance 才注入；空则绝不虚造；
4. 无机位 / 无动作 → 对应段落跳过；
5. 英文骨架行存在（exactly N / single camera / no third character…）；
6. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.prompt_compiler as pc  # noqa: E402
from director.shot_plan import build_shot_plan  # noqa: E402
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


def _plan(**kw):
    return build_shot_plan(_intent(), **kw)


# ---------------------------------------------------------------------------
# 失败案例回放（§6 验收）
# ---------------------------------------------------------------------------

def test_block_failure_case() -> None:
    block = pc.compile_constraint_block(_plan())
    assert "角色锁定：画面中只有 2 名角色——顾琰宸、林薇薇" in block
    assert "每名角色全程只出现一次，不得复制" in block
    assert "Character lock: exactly 2 characters — 顾琰宸, 林薇薇" in block
    assert "动作顺序：1. 掏出支票 2. 甩落桌面 3. 林薇薇没有伸手去拿（核心动作：甩落桌面）" in block
    assert "镜头锁定：单机位 中景 固定机位，全镜不变，禁止中途切换景别或机位" in block
    assert "Camera lock: single camera state (中景 固定机位) throughout" in block
    assert "禁止：画面中不得出现未列出的角色；每个角色不得重复出现；不得中途切换景别或机位；场景不得改变；林薇薇不得伸手去拿；林薇薇保持静止，不得移动" in block
    assert "Forbidden: no character outside the listed cast. no duplicate characters. no shot-size or camera change mid-shot. no scene change mid-shot." in block
    assert "follow Chinese restrictions strictly" in block
    # 空间块复用（自带「空间连续性/Spatial Continuity」双语标签，不重复加前缀）
    assert "空间连续性：顾琰宸在屏幕左侧" in block
    assert "Spatial Continuity" in block


# ---------------------------------------------------------------------------
# 空 plan / 无角色（向后兼容）
# ---------------------------------------------------------------------------

def test_block_empty() -> None:
    from director.shot_plan import ShotPlan
    assert pc.compile_constraint_block(None) == ""
    assert pc.compile_constraint_block(ShotPlan(character_count=0)) == ""


def test_block_no_characters() -> None:
    it = _intent()
    it.characters = []
    block = pc.compile_constraint_block(build_shot_plan(it))
    assert block == ""


# ---------------------------------------------------------------------------
# 外观锁定（P2：有才注入，绝不虚造）
# ---------------------------------------------------------------------------

def test_block_appearance() -> None:
    block = pc.compile_constraint_block(
        _plan(appearance_map={"顾琰宸": "白色西装", "林薇薇": "酒红丝绒长裙"})
    )
    assert "外观锁定：顾琰宸=白色西装，全程不变" in block
    assert "外观锁定：林薇薇=酒红丝绒长裙，全程不变" in block
    assert "Appearance lock: 顾琰宸 — 白色西装. Unchanged throughout." in block


def test_block_no_appearance() -> None:
    block = pc.compile_constraint_block(_plan())
    assert "外观锁定" not in block
    assert "Appearance lock" not in block


# ---------------------------------------------------------------------------
# 无相机 / 无动作 → 段落跳过
# ---------------------------------------------------------------------------

def test_block_no_camera() -> None:
    it = _intent()
    it.camera_position = ""
    it.movement = ""
    block = pc.compile_constraint_block(build_shot_plan(it))
    assert "镜头锁定" not in block
    assert "Camera lock" not in block
    assert "角色锁定" in block  # 其余段落仍在


def test_block_no_action() -> None:
    it = _intent()
    it.action = []
    it.core_action = ""
    block = pc.compile_constraint_block(build_shot_plan(it))
    assert "动作顺序" not in block
    assert "Action sequence" not in block


# ---------------------------------------------------------------------------
# 零 GPU 副作用
# ---------------------------------------------------------------------------

def test_no_gpu_side_effects() -> None:
    import inspect
    src = inspect.getsource(pc)
    for banned in ("torch", "ollama", "requests", "http://", "comfy.samplers"):
        assert banned not in src, f"prompt_compiler.py 引用了禁止依赖 {banned}"


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
