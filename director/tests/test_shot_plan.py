#!/usr/bin/env python3
"""Shot Planning 层（生成约束层 v1.0）单元测试（纯规则，mock，零显存）。

覆盖（PROMPT_COMPILER_V1 §3.1）：
1. extract_shot_sizes —— 多景别检测（去重保序 / 中近景长词优先 / 无景别空）；
2. extract_performance_lock —— 原文否定句→禁止动作（没有伸手去拿 / 保持静止 /
   「冷漠」状态词不误伤 / 不+白名单动词 / 跨镜角色不抽）；
3. build_shot_plan —— 角色计数去重 / appearance_map 注入 / spatial 块复用 /
   action_timeline primary 标记（core_action 命中）/ camera_lock / forbidden /
   shot_sizes_seen；
4. 用户失败案例回放（办公室三人群像：锁 2 人 + 双景别 + 不伸手 + 多核心动作）；
5. to_dict/from_dict 序列化 roundtrip（可存 DirectorIntent.constraint）；
6. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.shot_plan as sp  # noqa: E402
from director.director_intent import DirectorIntent  # noqa: E402


def _intent(**over) -> DirectorIntent:
    base: dict = {
        "shot_id": "scene_01:shot_01",
        "scene_id": "scene_01",
        "user_original_intent": "顾琰宸从西装内袋掏出支票，将支票从手中甩落到林薇薇面前的桌面，支票落桌后轻微滑动，林薇薇没有伸手去拿，中景拍摄顾琰宸甩出支票，特写支票滑动至桌面，林薇薇保持静止，冷漠。",
        "retained_facts": [
            "顾琰宸从西装内袋掏出支票",
            "将支票从手中甩落到林薇薇面前的桌面",
            "支票落桌后轻微滑动",
            "林薇薇没有伸手去拿",
            "林薇薇保持静止，冷漠",
        ],
        "subject": "顾琰宸、林薇薇",
        "characters": ["顾琰宸", "林薇薇"],
        "location": "豪华私人办公室",
        "action": ["掏出支票", "甩落桌面", "林薇薇没有伸手去拿"],
        "emotion": "冷漠",
        "composition": "中景拍摄顾琰宸甩出支票，特写支票滑动至桌面",
        "camera_position": "中景",
        "movement": "固定机位",
        "core_action": "甩落桌面",
        "expression": "林薇薇嘴角浮现一抹嘲讽",
        "continuity": {
            "spatial": {"block": "空间连续性：顾琰宸在屏幕左侧，林薇薇在屏幕右侧，视线轴固定（顾琰宸→林薇薇）。Spatial Continuity: 顾琰宸: screen left. 林薇薇: screen right."},
        },
    }
    base.update(over)
    return DirectorIntent(**base)


# ---------------------------------------------------------------------------
# extract_shot_sizes
# ---------------------------------------------------------------------------

def test_extract_shot_sizes_multi() -> None:
    sizes = sp.extract_shot_sizes("中景拍摄顾琰宸甩出支票，特写支票滑动至桌面")
    assert sizes == ["中景", "特写"]


def test_extract_shot_sizes_dedup_order() -> None:
    sizes = sp.extract_shot_sizes("特写，再切中景，又切特写，最后全景")
    assert sizes == ["特写", "中景", "全景"]


def test_extract_shot_sizes_long_word_first() -> None:
    sizes = sp.extract_shot_sizes("中近景推进")
    assert sizes == ["中近景"]


def test_extract_shot_sizes_empty() -> None:
    assert sp.extract_shot_sizes("") == []
    assert sp.extract_shot_sizes("顾琰宸掏出支票") == []


# ---------------------------------------------------------------------------
# extract_performance_lock
# ---------------------------------------------------------------------------

def test_perf_negative_action() -> None:
    out = sp.extract_performance_lock("林薇薇没有伸手去拿。", ["林薇薇"])
    assert out == ["林薇薇不得伸手去拿"]


def test_perf_still_hint() -> None:
    out = sp.extract_performance_lock("林薇薇保持静止，冷漠。", ["林薇薇"])
    assert out == ["林薇薇保持静止，不得移动"]


def test_perf_not_mistake_state_word() -> None:
    # 「冷漠」不是动作，不应抽成「不得…」；「不」+动作白名单才抽。
    out = sp.extract_performance_lock("林薇薇冷漠地站着。", ["林薇薇"])
    assert out == []
    out2 = sp.extract_performance_lock("林薇薇不伸手去拿。", ["林薇薇"])
    assert out2 == ["林薇薇不得伸手去拿"]


def test_perf_ignore_out_of_scene_char() -> None:
    out = sp.extract_performance_lock("路人没有伸手。", ["林薇薇", "顾琰宸"])
    assert out == []


def test_perf_empty() -> None:
    assert sp.extract_performance_lock("", ["林薇薇"]) == []
    assert sp.extract_performance_lock("顾琰宸掏出支票。", ["顾琰宸"]) == []


# ---------------------------------------------------------------------------
# build_shot_plan
# ---------------------------------------------------------------------------

def test_build_plan_character_lock() -> None:
    plan = sp.build_shot_plan(_intent())
    assert plan.character_count == 2
    assert [c.name for c in plan.character_locks] == ["顾琰宸", "林薇薇"]


def test_build_plan_character_dedup() -> None:
    it = _intent()
    it.characters = ["顾琰宸", "林薇薇", "顾琰宸"]
    plan = sp.build_shot_plan(it)
    assert plan.character_count == 2
    assert [c.name for c in plan.character_locks] == ["顾琰宸", "林薇薇"]


def test_build_plan_appearance_map() -> None:
    plan = sp.build_shot_plan(
        _intent(),
        appearance_map={"顾琰宸": "白色西装", "林薇薇": "酒红丝绒长裙"},
    )
    by_name = {c.name: c.appearance for c in plan.character_locks}
    assert by_name["顾琰宸"] == "白色西装"
    assert by_name["林薇薇"] == "酒红丝绒长裙"


def test_build_plan_spatial_reuse() -> None:
    plan = sp.build_shot_plan(_intent())
    assert "顾琰宸在屏幕左侧" in plan.spatial_lock


def test_build_plan_action_timeline_primary() -> None:
    plan = sp.build_shot_plan(_intent())
    texts = [a.text for a in plan.action_timeline]
    assert texts == ["掏出支票", "甩落桌面", "林薇薇没有伸手去拿"]
    primaries = [a.text for a in plan.action_timeline if a.is_primary]
    # core_action=甩落桌面 命中第二条；「林薇薇没有伸手去拿」是禁止动作，非核心。
    assert primaries == ["甩落桌面"]


def test_build_plan_camera_lock() -> None:
    plan = sp.build_shot_plan(_intent())
    assert plan.camera_lock["shot_size"] == "中景"
    assert plan.camera_lock["movement"] == "固定机位"
    assert plan.camera_lock["cn"] == "中景固定机位"


def test_build_plan_forbidden_contains_perf() -> None:
    plan = sp.build_shot_plan(_intent())
    assert "画面中不得出现未列出的角色" in plan.forbidden
    assert "林薇薇不得伸手去拿" in plan.forbidden


def test_build_plan_shot_sizes_seen() -> None:
    plan = sp.build_shot_plan(_intent())
    assert plan.shot_sizes_seen == ["中景", "特写"]


def test_build_plan_no_camera_ok() -> None:
    it = _intent()
    it.camera_position = ""
    it.movement = ""
    plan = sp.build_shot_plan(it)
    assert plan.camera_lock["cn"] == ""


# ---------------------------------------------------------------------------
# 用户失败案例回放（PROMPT_COMPILER_V1 §6）
# ---------------------------------------------------------------------------

def test_failure_case_replay() -> None:
    it = _intent()
    plan = sp.build_shot_plan(it)
    assert plan.character_count == 2
    assert plan.shot_sizes_seen == ["中景", "特写"]
    assert "林薇薇不得伸手去拿" in plan.forbidden
    assert "林薇薇保持静止，不得移动" in plan.performance_lock


# ---------------------------------------------------------------------------
# 序列化 roundtrip
# ---------------------------------------------------------------------------

def test_plan_roundtrip() -> None:
    plan = sp.build_shot_plan(_intent(), appearance_map={"林薇薇": "酒红丝绒长裙"})
    restored = sp.ShotPlan.from_dict(plan.to_dict())
    assert restored.character_count == plan.character_count
    assert restored.location == plan.location == "豪华私人办公室"
    assert [c.name for c in restored.character_locks] == [c.name for c in plan.character_locks]
    assert [c.appearance for c in restored.character_locks] == [c.appearance for c in plan.character_locks]
    assert restored.camera_lock == plan.camera_lock
    assert restored.action_timeline[0].text == plan.action_timeline[0].text
    assert restored.action_timeline[1].is_primary is True
    assert restored.shot_sizes_seen == plan.shot_sizes_seen


def test_plan_from_dict_none() -> None:
    restored = sp.ShotPlan.from_dict(None)
    assert restored.character_count == 0
    assert restored.forbidden == []


# ---------------------------------------------------------------------------
# 零 GPU 副作用
# ---------------------------------------------------------------------------

def test_no_gpu_side_effects() -> None:
    import inspect
    src = inspect.getsource(sp)
    for banned in ("torch", "ollama", "requests", "http://", "comfy.samplers"):
        assert banned not in src, f"shot_plan.py 引用了禁止依赖 {banned}"


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
