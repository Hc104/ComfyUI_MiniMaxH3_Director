#!/usr/bin/env python3
"""segment_cache 指纹「镜头身份」隔离回归测试（#90）。

背景（用户实测）：新建项目只建 1 场景 2 镜头，什么都不设置，点第 2 个镜头
「首尾帧生成视频」，却生成出旧项目里的人物。根因 = 缓存指纹不含段唯一 id
（timeline.segments[].id = SPA 镜头 uuid），缓存按「node_id + 段索引 + 参数」
存储；SPA 所有项目共用同一 node_id，于是新项目空 prompt 段命中旧项目同位置
空段的缓存，直接回放旧视频。

修复：① SegmentPlan 新增 id；② gen_timeline 解析段 id 传入；③ 指纹加 id +
注入资产图标识（kind:id:image_file）。本测试覆盖：id 不同→不互命中、id 相同
→正常命中、资产图替换→指纹变化、老缓存 stale 时 allow_stale 仍可作续接锚点。

以 `ComfyUI_MiniMaxH3_Director.director.*` 包路径导入（与 ComfyUI 运行时一致），
并桩掉 torch / folder_paths / comfy 依赖。

运行：
    python3 director/tests/test_segment_cache_identity.py
或
    pytest director/tests/test_segment_cache_identity.py
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
_OUTPUT_DIR = tempfile.mkdtemp(prefix="minimax_seg_cache_identity_test_")
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
    segment_cache_fingerprint,
)


def _fresh_node() -> str:
    """每个测试用独立 node_id，避免缓存跨用例污染。"""
    return f"test-identity-{uuid.uuid4().hex[:8]}"


def _make_plan(**over):
    base = dict(
        width=1280,
        height=736,
        output_mode="fixed",
        ref_max_size=1024,
        continuity_enabled=False,
        continuity_overlap_frames=0,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def _make_seg(index: int, **over):
    base = dict(
        id="",
        index=index,
        start_frame=0,
        end_frame=124,
        prompt="",
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


def _make_asset(**over):
    base = dict(id="asset-uuid-1", name="角色A", kind="cast", image_file="cast/a.png")
    base.update(over)
    return types.SimpleNamespace(**base)


def test_fingerprint_different_id_not_equal():
    """核心回归：同 index+同参数、不同段 id → 指纹不同（跨项目缓存隔离）。"""
    plan = _make_plan()
    a = _make_seg(1, id="shot-uuid-aaa")
    b = _make_seg(1, id="shot-uuid-bbb")
    assert segment_cache_fingerprint(a, plan) != segment_cache_fingerprint(b, plan)


def test_fingerprint_same_id_equal():
    """同一镜头重跑：id+参数都相同 → 指纹一致，正常命中缓存。"""
    plan = _make_plan()
    a = _make_seg(1, id="shot-uuid-aaa")
    a2 = _make_seg(1, id="shot-uuid-aaa")
    assert segment_cache_fingerprint(a, plan) == segment_cache_fingerprint(a2, plan)


def test_fingerprint_asset_image_change_invalidates():
    """替换资产图（image_file 变）→ 指纹变化，避免「改图仍出旧画面」。"""
    plan = _make_plan()
    seg = _make_seg(1, id="shot-uuid-aaa")
    seg.cast_assets = [_make_asset(image_file="cast/a.png")]
    seg2 = _make_seg(1, id="shot-uuid-aaa")
    seg2.cast_assets = [_make_asset(image_file="cast/b.png")]
    fp_a = segment_cache_fingerprint(seg, plan)
    fp_b = segment_cache_fingerprint(seg2, plan)
    assert fp_a != fp_b
    # assets 键确实参与了指纹
    assert fp_a.get("assets") == ["cast:asset-uuid-1:cast/a.png"]
    assert fp_b.get("assets") == ["cast:asset-uuid-1:cast/b.png"]


def test_fingerprint_cast_count_change_invalidates():
    """V1.6-B：cast 集合成员数变化（单角色→双角色）→ 指纹变化，缓存必失效。

    修复前指纹只取单值 cast_asset：castIds=[林雪] 与 [林雪,陈默] 指纹相同，
    多角色改动后仍命中旧单角色缓存，出成片身份不一致。
    """
    plan = _make_plan()
    seg1 = _make_seg(1, id="shot-uuid-aaa")
    seg1.cast_assets = [_make_asset(image_file="cast/linxue.png")]
    seg2 = _make_seg(1, id="shot-uuid-aaa")
    seg2.cast_assets = [
        _make_asset(image_file="cast/linxue.png"),
        _make_asset(image_file="cast/chenmo.png"),
    ]
    assert segment_cache_fingerprint(seg1, plan) != segment_cache_fingerprint(seg2, plan)


def test_save_load_same_id_hit():
    """保存后同 id 重跑 → 命中缓存。"""
    node = _fresh_node()
    plan = _make_plan()
    seg = _make_seg(1, id="shot-uuid-aaa")
    save_segment_cache(node, seg, plan, FakeTensor(10, 736, 1280))
    seg2 = _make_seg(1, id="shot-uuid-aaa")
    assert load_segment_cache(node, seg2, plan) is not None


def test_cross_project_different_id_no_hit():
    """跨项目：同 index、不同 id → 不命中（修复前会错误命中旧视频）。"""
    node = _fresh_node()
    plan = _make_plan()
    old = _make_seg(1, id="shot-uuid-aaa")
    save_segment_cache(node, old, plan, FakeTensor(10, 736, 1280))
    new = _make_seg(1, id="shot-uuid-bbb")  # 新项目的空镜头
    assert load_segment_cache(node, new, plan) is None


def test_stale_allow_stale_still_anchor():
    """老缓存 id 不匹配（stale）时：默认拒绝，但 allow_stale 仍可作续接锚点（#82 不破坏）。"""
    node = _fresh_node()
    plan = _make_plan()
    old = _make_seg(1, id="shot-uuid-aaa")
    save_segment_cache(node, old, plan, FakeTensor(10, 736, 1280))
    new = _make_seg(1, id="shot-uuid-bbb")
    assert load_segment_cache(node, new, plan) is None
    assert load_segment_cache(node, new, plan, allow_stale=True) is not None


def test_legacy_meta_without_id_stale():
    """老 meta（无 id 键）→ 默认 stale 拒绝；allow_stale 作锚点。"""
    import json

    node = _fresh_node()
    plan = _make_plan()
    seg = _make_seg(1, id="shot-uuid-aaa")
    save_segment_cache(node, seg, plan, FakeTensor(10, 736, 1280))

    # 手工改写 meta：删掉 id / assets 键，模拟修复前旧缓存。
    root = os.path.join(_OUTPUT_DIR, "minimax_seg_cache", node)
    meta_path = os.path.join(root, "seg_0001.meta.json")
    with open(meta_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.pop("id", None)
    data.pop("assets", None)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)

    seg_new = _make_seg(1, id="shot-uuid-aaa")
    assert load_segment_cache(node, seg_new, plan) is None  # 旧缓存不可再被「正式」命中
    assert load_segment_cache(node, seg_new, plan, allow_stale=True) is not None


def main():
    fns = [
        test_fingerprint_different_id_not_equal,
        test_fingerprint_same_id_equal,
        test_fingerprint_asset_image_change_invalidates,
        test_fingerprint_cast_count_change_invalidates,
        test_save_load_same_id_hit,
        test_cross_project_different_id_no_hit,
        test_stale_allow_stale_still_anchor,
        test_legacy_meta_without_id_stale,
    ]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    main()
