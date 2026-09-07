#!/usr/bin/env python3
"""V1.7 P0-4：Prompt Builder 内部标记泄漏修复 · 专项测试（2026-08-11 用户拍板）。

用户拍板：「shot_id/scene_id/镜头编号只能作结构元数据，不进 visual/camera/style/sound」。

背景：SPA 真实验收《山雨客栈》发现 AI Draft「中景，缓慢推近镜头二、沈青崖……」
内部标记泄漏——Qwen 生成的 visual_intent 等文本混入「镜头二」。

覆盖：
1. prompt_sanitize.strip_internal_markers 各模式（镜头+N / 第N镜 / shot_N / scene_N /
   括号包裹 / scene:shot 组合），且「远景镜头」等正文含义不误删。
2. prompt_builder build_shot_draft 集成：visual_intent 含「镜头二」→ visual 区不含；
   emotion 含「镜头七」→ camera 区不含；fallback subject 脏字符过滤。
3. camera_template pick_camera 集成：characters 含「镜头一」脏数据 → camera 措辞不含
   「镜头一」但保留真实角色「沈青崖」。

直接用系统 python 运行（纯规则，无第三方依赖）：
    python3 director/tests/test_prompt_sanitize.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.prompt_sanitize import strip_internal_markers  # noqa: E402
from director.prompt_builder_v17 import (  # noqa: E402
    build_shot_draft,
    load_template_registry,
)
from director.camera_template import pick_camera  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
)

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


def _plan(shot, scene):
    return ProductionPlan(
        project=ProjectInfo(title="t", source_file=""),
        scenes=[scene],
        validation=Validation(),
    )


def _scene(shot):
    return Scene(scene_id="scene_01", title="第一场", location_name="山雨楼外",
                 time="黄昏", weather="雨", shots=[shot])


def _shot(**kw):
    base = dict(shot_id="shot_01", source_text="原文", duration_sec=5,
                characters=[], entities=[], props=[], actions=[], emotion="",
                dialogue=[], visual_intent="")
    base.update(kw)
    return Shot(**base)


def test_sanitize_patterns() -> None:
    print("\n[1] strip_internal_markers 各模式")
    cases = [
        ("镜头二", ""),
        ("镜头一：远景", "远景"),
        ("镜头 1", ""),
        ("镜头1，", ""),
        ("第2镜", ""),
        ("第二个镜头", ""),
        ("第3幕", ""),
        ("shot_1", ""),
        ("shot-2", ""),
        ("Shot 3", ""),
        ("SHOT4，", ""),
        ("scene_01", ""),
        ("scene-1，", ""),
        ("scene_02:shot_07", ""),
        ("【镜头一】", ""),
        ("（第2镜）", ""),
        ("(shot_1)", ""),
        ("中景，缓慢推近镜头二、沈青崖", "中景，缓慢推近沈青崖"),
    ]
    for raw, expect in cases:
        got = strip_internal_markers(raw)
        _check(f"清洗「{raw}」→「{expect}」", got == expect, f"got「{got}」")

    no_touch = ["远景镜头", "镜头切换", "山道", "沈青崖", "客栈大堂", "中景固定机位"]
    for raw in no_touch:
        got = strip_internal_markers(raw)
        _check(f"正文不误删「{raw}」", got == raw, f"got「{got}」")


def test_sanitize_integrated_visual() -> None:
    print("\n[2] build_shot_draft：visual_intent 含「镜头二」→ visual 区不含")
    tmpl = load_template_registry()
    sh = _shot(visual_intent="中景，缓慢推近镜头二、沈青崖，雨幕中凝望")
    draft = build_shot_draft(sh, _scene(sh), tmpl, has_prev=True, is_last_shot=False)
    visual = draft["visual"]
    _check("visual 区不含「镜头二」", "镜头二" not in visual, visual)
    _check("visual 区保留正文（沈青崖）", "沈青崖" in visual, visual)
    _check("visual 区无残留孤立顿号头", not visual.startswith("、"), visual)


def test_sanitize_integrated_emotion() -> None:
    print("\n[3] build_shot_draft：emotion 含「镜头七」→ camera/sound 区不含")
    tmpl = load_template_registry()
    sh = _shot(emotion="镜头七，紧张", visual_intent="")
    draft = build_shot_draft(sh, _scene(sh), tmpl, has_prev=True, is_last_shot=False)
    _check("camera 区不含「镜头七」", "镜头七" not in draft["camera"], draft["camera"])
    _check("sound 区不含「镜头七」", "镜头七" not in draft["sound"], draft["sound"])


def test_sanitize_fallback_subject() -> None:
    print("\n[4] fallback subject：characters 脏字符过滤")
    tmpl = load_template_registry()
    sh = _shot(characters=[Character(name="沈青崖"), Character(name="镜头一")],
               visual_intent="")
    draft = build_shot_draft(sh, _scene(sh), tmpl, has_prev=True, is_last_shot=False)
    joined = draft["visual"] + draft["camera"]
    _check("措辞不含「镜头一」", "镜头一" not in joined, joined)
    _check("措辞保留「沈青崖」", "沈青崖" in joined, joined)


def test_camera_template_integration() -> None:
    print("\n[5] pick_camera：脏字符不进 camera 措辞")
    sh = _shot(characters=[Character(name="沈青崖"), Character(name="镜头一")],
               emotion="镜头七，紧张")
    desc, intent, state = pick_camera(sh, _scene(sh), None, is_last_shot=False)
    _check("camera 不含「镜头一」", "镜头一" not in desc, desc)
    _check("camera 不含「镜头七」", "镜头七" not in desc, desc)
    _check("camera 保留「沈青崖」", "沈青崖" in desc, desc)


def main() -> int:
    print("P0-4 Prompt 内部标记泄漏修复 · 专项验收")
    test_sanitize_patterns()
    test_sanitize_integrated_visual()
    test_sanitize_integrated_emotion()
    test_sanitize_fallback_subject()
    test_camera_template_integration()
    print("\n" + "=" * 60)
    print(f"结果: {PASSED} PASS / {FAILED} FAIL")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
