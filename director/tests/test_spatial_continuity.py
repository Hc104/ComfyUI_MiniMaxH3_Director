#!/usr/bin/env python3
"""V1.0 运镜规则层·第一层硬约束：空间连续性单元测试（纯规则，mock，零显存）。

覆盖（《AI 漫剧导演运镜规则 v1.0》§2 第一层，用户 2026-08-17 拍板）：
1. 初始位置：剧本方位词优先（左边/右边/对面）→ 出场顺序兜底（首角色左、次角色右）；
2. 永不翻转：同场景后续镜已定位角色绝不移位（跳轴硬错误）；
3. 新角色补位：只补空侧，不影响已定位；
4. 场景切换重置：新场景 from_shot 重新建立；
5. 视线轴：轴上有且仅有两个已定位角色 → eye_line a->b；
6. block() 双语输出：中文可读 + 英文保 H3 执行稳；<2 角色 → 空串。
7. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_spatial_continuity.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.spatial_continuity as sc  # noqa: E402

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


def _shot(source_text: str = "", *names: str) -> SimpleNamespace:
    return SimpleNamespace(
        source_text=source_text,
        visual_intent="",
        characters=[SimpleNamespace(name=n, role="") for n in names],
    )


def test_entry_order_fallback() -> None:
    print("初始位置 · 出场顺序兜底：")
    st = sc.SpatialState.from_shot(_shot("", "林薇薇", "顾琰宸"))
    _check("首角色=screen_left", st.axis.get("林薇薇") == "screen_left", str(st.axis))
    _check("次角色=screen_right", st.axis.get("顾琰宸") == "screen_right", str(st.axis))
    _check("视线轴 林薇薇->顾琰宸", st.eye_line == "林薇薇->顾琰宸", st.eye_line)
    _check("camera_on_axis", st.camera_on_axis is True, str(st.camera_on_axis))


def test_script_position_left() -> None:
    print("初始位置 · 剧本方位词「X 在 Y 左边」：")
    st = sc.SpatialState.from_shot(
        _shot("林薇薇站在顾琰宸的左边，嘲讽地笑。", "林薇薇", "顾琰宸"))
    _check("林薇薇=screen_left", st.axis.get("林薇薇") == "screen_left", str(st.axis))
    _check("顾琰宸=screen_right", st.axis.get("顾琰宸") == "screen_right", str(st.axis))


def test_script_position_right() -> None:
    print("初始位置 · 剧本方位词「X 在 Y 右边」：")
    st = sc.SpatialState.from_shot(
        _shot("顾琰宸坐在林薇薇右侧，垂着眼。", "顾琰宸", "林薇薇"))
    _check("顾琰宸=screen_right", st.axis.get("顾琰宸") == "screen_right", str(st.axis))
    _check("林薇薇=screen_left", st.axis.get("林薇薇") == "screen_left", str(st.axis))


def test_script_position_opposite() -> None:
    print("初始位置 · 剧本方位词「X 在 Y 对面」：")
    st = sc.SpatialState.from_shot(
        _shot("林薇薇站在顾琰宸对面。", "林薇薇", "顾琰宸"))
    _check("对面→首角色左", st.axis.get("林薇薇") == "screen_left", str(st.axis))
    _check("对面→次角色右", st.axis.get("顾琰宸") == "screen_right", str(st.axis))


def test_never_flip() -> None:
    print("永不翻转（跳轴硬错误）：")
    st = sc.SpatialState.from_shot(_shot("", "林薇薇", "顾琰宸"))
    st.update(_shot("", "林薇薇", "顾琰宸"))
    st.update(_shot("", "林薇薇", "顾琰宸"))
    _check("林薇薇仍=screen_left", st.axis.get("林薇薇") == "screen_left", str(st.axis))
    _check("顾琰宸仍=screen_right", st.axis.get("顾琰宸") == "screen_right", str(st.axis))


def test_new_character_placement() -> None:
    print("新角色补位（不影响已定位）：")
    st = sc.SpatialState.from_shot(_shot("", "林薇薇", "顾琰宸"))
    st.update(_shot("", "林薇薇", "顾琰宸", "沈青崖"))
    _check("林薇薇位置不变", st.axis.get("林薇薇") == "screen_left", str(st.axis))
    _check("顾琰宸位置不变", st.axis.get("顾琰宸") == "screen_right", str(st.axis))


def test_scene_reset() -> None:
    print("场景切换重置：")
    st1 = sc.SpatialState.from_shot(_shot("", "林薇薇", "顾琰宸"))
    st2 = sc.SpatialState.from_shot(_shot("", "柳如烟", "陈默"))
    _check("新场景重新建立", st1.axis.get("林薇薇") == "screen_left"
           and st2.axis.get("柳如烟") == "screen_left", str(st2.axis))


def test_block_bilingual() -> None:
    print("block() 双语输出：")
    st = sc.SpatialState.from_shot(_shot("", "林薇薇", "顾琰宸"))
    b = st.block()
    _check("含中文 屏幕左侧", "屏幕左侧" in b, b)
    _check("含英文 screen left", "screen left" in b, b)
    _check("含视线轴", "视线轴固定" in b, b)
    _check("含 eye-line", "Eye-line axis" in b, b)
    _check("含机位在轴线", "Camera remains on the axis" in b, b)


def test_block_empty_for_single() -> None:
    print("block() <2 角色为空：")
    st = sc.SpatialState.from_shot(_shot("", "林薇薇"))
    _check("单角色 block 为空", st.block() == "", repr(st.block()))


def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(sc.__file__), "spatial_continuity.py"),
               encoding="utf-8").read()
    for bad in ("torch", "ollama", "urlopen", "requests", "aiohttp", "keep_alive",
                "gpu_models_loaded", "empty_cache"):
        _check(f"源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_spatial_continuity\n")
    test_entry_order_fallback()
    test_script_position_left()
    test_script_position_right()
    test_script_position_opposite()
    test_never_flip()
    test_new_character_placement()
    test_scene_reset()
    test_block_bilingual()
    test_block_empty_for_single()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
