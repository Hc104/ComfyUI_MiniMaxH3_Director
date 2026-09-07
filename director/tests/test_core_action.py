#!/usr/bin/env python3
"""单镜头单核心动作（v2.0 ①）单元测试（纯规则，mock，零显存）。

覆盖（CAMERA_RULES_V2 §3.1）：
1. 单强动作 → 原样选中；
2. 多动作 → 取第一个强动作 + 折叠同主语连续强动作（过程链）；
3. 无强动作 → 取表情动作；
4. 姿态/环境 → 不选为核心；
5. reaction 镜 → 优先表情动作（拍听众）；
6. 静态动作 → 过程化措辞；
7. 无动作（纯环境）→ ("", "") 兜底；
8. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_core_action.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.core_action as ca  # noqa: E402

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


def _shot(*, actions: tuple = (), characters: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(actions=list(actions), characters=list(characters))


# ---------- 核心动作提取 ----------

def test_single_strong_action() -> None:
    print("extract_core_action · 单强动作：")
    shot = _shot(actions=("顾琰宸将支票从手中甩落到桌面",), characters=(_char("顾琰宸"),))
    core, subj = ca.extract_core_action(shot)
    _check("选中原句", core == "顾琰宸将支票从手中甩落到桌面", core)
    _check("主体=顾琰宸", subj == "顾琰宸", subj)


def test_fold_same_subject_actions() -> None:
    print("extract_core_action · 折叠同主语连续强动作：")
    shot = _shot(
        actions=("林薇薇捏起桌上的支票", "缓缓抬到眼前", "看向顾琰宸"),
        characters=(_char("林薇薇"), _char("顾琰宸")),
    )
    core, _ = ca.extract_core_action(shot)
    _check("强动作折叠成过程链（含视线动作被省略）",
           core == "林薇薇捏起桌上的支票，缓缓抬到眼前", core)
    # 折叠只并同主语：不同主语强动作不卷入。
    shot2 = _shot(
        actions=("顾琰宸甩落支票", "林薇薇捏起支票"),
        characters=(_char("顾琰宸"), _char("林薇薇")),
    )
    core2, _ = ca.extract_core_action(shot2)
    _check("不同主语不折叠", core2 == "顾琰宸甩落支票", core2)


def test_strong_beats_expression() -> None:
    print("extract_core_action · 强动作优先于表情动作：")
    shot = _shot(
        actions=("林薇薇抬眼", "捏起桌上的支票"),
        characters=(_char("林薇薇"),),
    )
    core, _ = ca.extract_core_action(shot)
    _check("取强动作（捏起）", "捏起" in core, core)


def test_expression_only() -> None:
    print("extract_core_action · 无强动作 → 表情动作：")
    shot = _shot(actions=("林薇薇瞳孔微缩",), characters=(_char("林薇薇"),))
    core, _ = ca.extract_core_action(shot)
    _check("取表情动作", core == "林薇薇瞳孔微缩", core)


def test_static_not_core() -> None:
    print("extract_core_action · 姿态不选为核心：")
    shot = _shot(actions=("顾琰宸静静地站着",), characters=(_char("顾琰宸"),))
    core, _ = ca.extract_core_action(shot)
    _check("纯姿态 → 空", core == "", core)


def test_processify_static_verb() -> None:
    print("extract_core_action · 静态动作过程化：")
    shot = _shot(actions=("顾琰宸双手握拳",), characters=(_char("顾琰宸"),))
    core, _ = ca.extract_core_action(shot)
    _check("握拳 → 过程短语", "指节逐渐绷紧" in core, core)


def test_reaction_prefers_expression() -> None:
    print("extract_core_action · reaction 镜：")
    shot = _shot(
        actions=("顾琰宸抬手接支票", "瞳孔微缩，表情第一次明显错愕"),
        characters=(_char("顾琰宸"),),
    )
    core, _ = ca.extract_core_action(shot, role="reaction")
    _check("reaction 不选强动作", "抬手" not in core, core)
    _check("reaction 选表情动作", "瞳孔微缩" in core, core)


def test_no_action_env_fallback() -> None:
    print("extract_core_action · 纯环境镜兜底：")
    _check("无动作 → ('', '')", ca.extract_core_action(_shot()) == ("", ""))


def test_reaction_no_expression_fallback() -> None:
    print("extract_core_action · reaction 无表情 → 空：")
    shot = _shot(actions=("顾琰宸甩落支票",), characters=(_char("顾琰宸"),))
    _check("reaction 无表情动作 → ('', '')",
           ca.extract_core_action(shot, role="reaction") == ("", ""))


# ---------- 纯规则零显存 ----------

def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(ca.__file__), "core_action.py"),
               encoding="utf-8").read()
    for bad in ("torch", "ollama", "urlopen", "requests", "aiohttp", "keep_alive",
                "gpu_models_loaded", "empty_cache", "openai"):
        _check(f"源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_core_action\n")
    test_single_strong_action()
    test_fold_same_subject_actions()
    test_strong_beats_expression()
    test_expression_only()
    test_static_not_core()
    test_processify_static_verb()
    test_reaction_prefers_expression()
    test_no_action_env_fallback()
    test_reaction_no_expression_fallback()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
