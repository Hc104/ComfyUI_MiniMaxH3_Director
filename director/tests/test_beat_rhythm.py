#!/usr/bin/env python3
"""情绪曲线 + 镜头节奏 + 反应镜硬规则（v2.0 ③/③′）单元测试（纯规则，mock，零显存）。

覆盖（CAMERA_RULES_V2 §3.3 + §3.4）：
1. plan_rhythm：基础张力（情绪/爆点/对白/无）；
2. 曲线模式：上升 build → 峰值 peak → 释放 release → 维持 hold；
3. ⛔ 峰值（3）定格语义：tension 2→3 判 peak（渲染层定格，非推镜）；
4. plan_reaction_shots：连续对白 ≥2 → 下一镜强制 reaction；
5. plan_reaction_shots：台词爆点 → 紧邻下一镜强制 reaction；
6. 无角色镜不强制 / 无对白不触发；
7. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_beat_rhythm.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.beat_rhythm as br  # noqa: E402

PASSED = 0
FAILED = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name} {detail}")


def _char(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _shot(shot_id: str, *, emotion: str = "", dialogue: tuple = (),
          characters: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(
        shot_id=shot_id,
        emotion=emotion,
        dialogue=[SimpleNamespace(text=t) for t in dialogue],
        characters=list(characters),
    )


def _plan(scenes: list, timeline: list) -> SimpleNamespace:
    return SimpleNamespace(scenes=scenes, timeline=timeline)


def _scene(scene_id: str, shots: list) -> SimpleNamespace:
    return SimpleNamespace(scene_id=scene_id, shots=shots)


# ---------- plan_rhythm：基础张力 ----------

def test_base_tension() -> None:
    print("_base_tension：")
    _check("无情绪无对白 → 0", br._base_tension(_shot("1")) == 0)
    _check("普通对白 → 1", br._base_tension(_shot("1", dialogue=("辛苦了。",))) == 1)
    _check("情绪紧张 → 2", br._base_tension(_shot("1", emotion="紧张")) == 2)
    _check("情绪愤怒 → 3", br._base_tension(_shot("1", emotion="愤怒")) == 3)
    _check("台词爆点 → 3", br._base_tension(_shot("1", dialogue=("凭什么？",))) == 3)


def test_rhythm_curve_build_peak_release() -> None:
    print("plan_rhythm · 上升→峰值→释放：")
    plan = _plan([
        _scene("s1", [
            _shot("1", dialogue=("这么大的雨，赶路辛苦了。",)),
            _shot("2", emotion="紧张", dialogue=("你到底想说什么？",)),
            _shot("3", emotion="愤怒", dialogue=("凭什么这么对我！",)),
            _shot("4", dialogue=("……",)),
        ]),
    ], ["s1:1", "s1:2", "s1:3", "s1:4"])
    r = br.plan_rhythm(plan)
    _check("镜1 (1, hold)", r["s1:1"] == (1, "hold"), str(r["s1:1"]))
    _check("镜2 上升 build", r["s1:2"][1] == "build", str(r["s1:2"]))
    _check("镜3 峰值 peak（2→3 定格）", r["s1:3"] == (3, "peak"), str(r["s1:3"]))
    _check("镜4 释放 release", r["s1:4"][1] == "release", str(r["s1:4"]))


def test_rhythm_flat_hold() -> None:
    print("plan_rhythm · 平缓维持：")
    plan = _plan([
        _scene("s1", [
            _shot("1", emotion="平静"),
            _shot("2", emotion="平静"),
        ]),
    ], ["s1:1", "s1:2"])
    r = br.plan_rhythm(plan)
    _check("镜1 hold", r["s1:1"] == (0, "hold"), str(r["s1:1"]))
    _check("镜2 hold", r["s1:2"] == (0, "hold"), str(r["s1:2"]))


def test_rhythm_peak_rule_not_more_push() -> None:
    print("⛔ 峰值（3）判定：tension 2→3 才是 peak（渲染层定格，不是继续推镜）：")
    plan = _plan([
        _scene("s1", [
            _shot("1", emotion="紧张"),
            _shot("2", emotion="愤怒"),
            _shot("3", emotion="愤怒"),   # 已稳定在 3 → hold
        ]),
    ], ["s1:1", "s1:2", "s1:3"])
    r = br.plan_rhythm(plan)
    _check("镜2 2→3 peak", r["s1:2"][1] == "peak", str(r["s1:2"]))
    _check("镜3 3→3 维持 hold", r["s1:3"][1] == "hold", str(r["s1:3"]))


def test_rhythm_no_timeline_backward_compat() -> None:
    print("plan_rhythm · 无 timeline 退化场景树顺序：")
    plan = SimpleNamespace(scenes=[_scene("s1", [_shot("1"), _shot("2")])], timeline=[])
    r = br.plan_rhythm(plan)
    _check("退化顺序仍出全链", set(r.keys()) == {"s1:1", "s1:2"}, str(set(r.keys())))


# ---------- plan_reaction_shots：硬规则 ----------

def test_reaction_after_dlg_streak() -> None:
    print("plan_reaction_shots · 连续对白 ≥2 → 下一镜强制 reaction：")
    plan = _plan([
        _scene("s1", [
            _shot("1", dialogue=("雨这么大，你先进来。",), characters=(_char("林薇薇"),)),
            _shot("2", dialogue=("我不用你管。",), characters=(_char("顾琰宸"),)),
            _shot("3", characters=(_char("林薇薇"),)),
            _shot("4", characters=(_char("顾琰宸"),)),
        ]),
    ], ["s1:1", "s1:2", "s1:3", "s1:4"])
    forced = br.plan_reaction_shots(plan)
    _check("镜3 被强制（对白连续后）", "s1:3" in forced, str(forced))
    _check("镜4 不被强制", "s1:4" not in forced, str(forced))


def test_reaction_after_bomb() -> None:
    print("plan_reaction_shots · 台词爆点 → 紧邻下一镜强制 reaction：")
    plan = _plan([
        _scene("s1", [
            _shot("1", dialogue=("就这？",), characters=(_char("林薇薇"),)),
            _shot("2", characters=(_char("顾琰宸"),)),
        ]),
    ], ["s1:1", "s1:2"])
    forced = br.plan_reaction_shots(plan)
    _check("镜2 被强制（爆点后）", "s1:2" in forced, str(forced))


def test_reaction_no_cast_skip() -> None:
    print("plan_reaction_shots · 无角色镜不强制：")
    plan = _plan([
        _scene("s1", [
            _shot("1", dialogue=("就这？",), characters=(_char("林薇薇"),)),
            _shot("2"),  # 无角色 → 不强制
        ]),
    ], ["s1:1", "s1:2"])
    forced = br.plan_reaction_shots(plan)
    _check("无角色镜跳过", "s1:2" not in forced, str(forced))


def test_reaction_no_dialogue() -> None:
    print("plan_reaction_shots · 无对白不触发：")
    plan = _plan([
        _scene("s1", [
            _shot("1", characters=(_char("林薇薇"),)),
            _shot("2", characters=(_char("顾琰宸"),)),
        ]),
    ], ["s1:1", "s1:2"])
    _check("无对白 → 空集合", br.plan_reaction_shots(plan) == set())


# ---------- 纯规则零显存 ----------

def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(br.__file__), "beat_rhythm.py"),
               encoding="utf-8").read()
    for bad in ("torch", "ollama", "urlopen", "requests", "aiohttp", "keep_alive",
                "gpu_models_loaded", "empty_cache", "openai"):
        _check(f"源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_beat_rhythm\n")
    test_base_tension()
    test_rhythm_curve_build_peak_release()
    test_rhythm_flat_hold()
    test_rhythm_peak_rule_not_more_push()
    test_rhythm_no_timeline_backward_compat()
    test_reaction_after_dlg_streak()
    test_reaction_after_bomb()
    test_reaction_no_cast_skip()
    test_reaction_no_dialogue()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
