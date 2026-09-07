#!/usr/bin/env python3
"""segment_cache 缓存 stale 容错回归测试（#82）。

覆盖修复核心：段间连贯取上一段尾帧作续接锚点时，允许加载 stale 缓存
（`load_segment_cache(..., allow_stale=True)`），避免单镜重生成 r2v/fl2v 段时
因全局字段（如 continuity_enabled/width 等）变化触发指纹 mismatch 而被
「段间连贯」ValueError 卡死。

以 `ComfyUI_MiniMaxH3_Director.director.*` 包路径导入（与 ComfyUI 运行时一致），
并桩掉 torch / folder_paths / comfy 依赖。

运行：
    python3 director/tests/test_segment_cache_stale.py
或
    pytest director/tests/test_segment_cache_stale.py
"""

from __future__ import annotations

import os
import pickle
import sys
import tempfile
import types
import uuid

# ---------- 依赖桩（在导入 director 包之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包：让 director 包以 ComfyUI_MiniMaxH3_Director.director 层级存在。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)


class FakeTensor:
    """极简可 pickle 的「视频张量」桩：shape=(T,H,W,C)，提供 .cpu/.float/.contiguous。"""

    def __init__(self, frames: int, h: int, w: int):
        self.frames = frames
        self.h = h
        self.w = w
        self.c = 3

    @property
    def shape(self) -> tuple:
        return (self.frames, self.h, self.w, self.c)

    @property
    def ndim(self) -> int:
        return 4

    def cpu(self):  # noqa: D401
        return FakeTensor(self.frames, self.h, self.w)

    def float(self):  # noqa: D401
        return FakeTensor(self.frames, self.h, self.w)

    def contiguous(self):  # noqa: D401
        return FakeTensor(self.frames, self.h, self.w)


# torch 桩：save/load 用 pickle 直通。
_torch = types.ModuleType("torch")
_torch.full = lambda *a, **k: None
_torch.Tensor = FakeTensor


def _save(obj, f):
    if hasattr(f, "write"):
        pickle.dump(obj, f)
    else:  # Path 对象 → 自行打开
        with open(f, "wb") as fh:
            pickle.dump(obj, fh)


def _load(f, **kw):
    if hasattr(f, "read"):
        return pickle.load(f)
    with open(f, "rb") as fh:
        return pickle.load(fh)


_torch.save = _save
_torch.load = _load
sys.modules.setdefault("torch", _torch)

# comfy.utils 桩（lib/image_prep 顶层 import 用）。
_comfy = types.ModuleType("comfy")
_comfy.__path__ = []
_utils = types.ModuleType("comfy.utils")
_utils.common_upscale = lambda *a, **k: None
_comfy.utils = _utils
sys.modules.setdefault("comfy", _comfy)
sys.modules.setdefault("comfy.utils", _utils)

# folder_paths 桩：输出目录指向临时目录。
_OUTPUT_DIR = tempfile.mkdtemp(prefix="minimax_seg_cache_test_")
_FOLDER_PATHS = types.ModuleType("folder_paths")
_FOLDER_PATHS.get_output_directory = lambda: _OUTPUT_DIR
sys.modules.setdefault("folder_paths", _FOLDER_PATHS)

# numpy / PIL 桩（plan.py 顶层 import 用，测试不触碰真实张量运算）。
_np = types.ModuleType("numpy")
_np.ndarray = type("ndarray", (), {})
sys.modules.setdefault("numpy", _np)
_pil = types.ModuleType("PIL")
_pil.Image = type("Image", (), {})
sys.modules.setdefault("PIL", _pil)

from ComfyUI_MiniMaxH3_Director.director.segment_cache import (  # noqa: E402
    load_segment_cache,
    save_segment_cache,
)
from ComfyUI_MiniMaxH3_Director.director.segment_continuity import (  # noqa: E402
    resolve_prev_segment_output,
)

def _fresh_node() -> str:
    """每个测试用独立 node_id，避免缓存跨用例污染。"""
    return f"test-stale-{uuid.uuid4().hex[:8]}"


def _make_plan(**over):
    base = dict(
        width=1280,
        height=736,
        output_mode="fixed",
        ref_max_size=1024,
        continuity_enabled=True,
        continuity_overlap_frames=9,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def _make_seg(index: int, **over):
    base = dict(
        index=index,
        start_frame=0,
        end_frame=124,
        prompt="test prompt",
        negative_prompt="",
        task_key="r2v",
        refs=[],
        ref_audios=[],
        ref_videos=[],
        reference_video_meta={},
        reference_video_start_frame=0,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def _fresh_plan_segs():
    plan = _make_plan()
    seg0 = _make_seg(0)
    seg1 = _make_seg(1)
    return plan, seg0, seg1


def test_roundtrip_match():
    plan, seg, _ = _fresh_plan_segs()
    node = _fresh_node()
    save_segment_cache(node, seg, plan, FakeTensor(10, 736, 1280))
    out = load_segment_cache(node, seg, plan)
    assert out is not None and out.shape[0] == 10


def test_stale_rejected_by_default():
    plan, seg, _ = _fresh_plan_segs()
    node = _fresh_node()
    save_segment_cache(node, seg, plan, FakeTensor(10, 736, 1280))
    # 全局字段变化 → 指纹 mismatch → 默认拒绝（合并/透传路径必须严格）。
    plan.continuity_enabled = False
    assert load_segment_cache(node, seg, plan) is None


def test_stale_allowed_for_continuation():
    plan, seg, _ = _fresh_plan_segs()
    node = _fresh_node()
    save_segment_cache(node, seg, plan, FakeTensor(10, 736, 1280))
    plan.continuity_enabled = False
    # allow_stale=True：续接锚点专用，容忍 stale 仍返回帧（打 warning 不拒绝）。
    out = load_segment_cache(node, seg, plan, allow_stale=True)
    assert out is not None and out.shape[0] == 10


def test_stale_allowed_requires_valid_4d_tensor():
    plan, seg, _ = _fresh_plan_segs()
    node = _fresh_node()
    save_segment_cache(node, seg, plan, FakeTensor(10, 736, 1280))
    plan.width = 640  # 空间尺寸变化，stale 帧下游缩放可处理，仍放行
    assert load_segment_cache(node, seg, plan, allow_stale=True) is not None


def test_missing_cache_returns_none():
    plan, _, seg1 = _fresh_plan_segs()
    node = _fresh_node()
    assert load_segment_cache(node, seg1, plan) is None
    assert load_segment_cache(node, seg1, plan, allow_stale=True) is None


def test_resolve_prev_segment_output_uses_stale_tail():
    """单镜重生成 r2v 段 #2 时，#1 缓存 stale 也应取到尾帧作续接锚点（#82）。"""
    plan, seg0, seg1 = _fresh_plan_segs()
    # 先按「全片/续接开」生成 #1 并落盘。
    node = _fresh_node()
    save_segment_cache(node, seg0, _make_plan(), FakeTensor(12, 736, 1280))
    # 单镜生成 #2 时前端对非 fl2v 清零 continuityEnabled → 全局字段变化 → #1 缓存 stale。
    plan.continuity_enabled = False
    plan.continuity_overlap_frames = 0
    prev = resolve_prev_segment_output(
        plan, [seg0, seg1], 1, {}, node, allow_without_continuity=True,
    )
    assert prev is not None and prev.shape[0] == 12


def test_resolve_prev_with_no_cache_still_raises():
    """前驱段从未生成且无任何缓存 → 仍报「段间连贯」，行为不变。"""
    plan, _, seg1 = _fresh_plan_segs()
    plan.continuity_enabled = False
    try:
        resolve_prev_segment_output(
            plan, [_make_seg(0), seg1], 1, {}, _fresh_node(), allow_without_continuity=True,
        )
        raise AssertionError("应当抛出段间连贯 ValueError")
    except ValueError as exc:
        assert "段间连贯" in str(exc)


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"\n{len(tests)} tests OK")


if __name__ == "__main__":
    _main()
