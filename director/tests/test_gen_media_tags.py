#!/usr/bin/env python3
"""gen_timeline 媒体引用标签解析单元测试（V1.2.7）。

覆盖 parse_media_tags / strip_media_tags / _match_asset_exact 三个纯函数：
标签解析、剥壳保留名字、资产精确匹配（含全局/场景素材池语义）。

以 `ComfyUI_MiniMaxH3_Director.director.gen_timeline` 包路径导入（与 ComfyUI
运行时一致），并桩掉 torch / comfy 依赖。

运行：
    python3 director/tests/test_gen_media_tags.py
或
    pytest director/tests/test_gen_media_tags.py
"""

from __future__ import annotations

import os
import sys
import types

# ---------- 依赖桩（在导入 gen_timeline 之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包：让 director 包以 ComfyUI_MiniMaxH3_Director.director 层级存在，
# gen_timeline 的 `from ..lib.image_prep` 相对导入才合法（与 ComfyUI 运行时一致）。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

# torch 桩。
_torch = types.ModuleType("torch")
_torch.full = lambda *a, **k: None
sys.modules.setdefault("torch", _torch)

# comfy.utils 桩（lib/image_prep 顶层 import 用）。
_comfy = types.ModuleType("comfy")
_comfy.__path__ = []
_utils = types.ModuleType("comfy.utils")
_utils.common_upscale = lambda *a, **k: None
_comfy.utils = _utils
sys.modules.setdefault("comfy", _comfy)
sys.modules.setdefault("comfy.utils", _utils)

from ComfyUI_MiniMaxH3_Director.director.gen_timeline import (  # noqa: E402
    _match_asset_exact,
    parse_media_tags,
    strip_media_tags,
)


def test_parse_media_tags_returns_kind_name_positions():
    refs = parse_media_tags("林雪走进<picture>林雪</picture>的街道")
    assert len(refs) == 1
    kind, name, start, end = refs[0]
    assert kind == "picture"
    assert name == "林雪"
    assert start == len("林雪走进")
    assert end == len("林雪走进<picture>林雪</picture>")


def test_parse_media_tags_mixed_video_audio():
    refs = parse_media_tags("a<video>动作.mp4</video>b<audio>对白.wav</audio>c")
    assert [(k, n) for k, n, _s, _e in refs] == [("video", "动作.mp4"), ("audio", "对白.wav")]


def test_parse_media_tags_ignores_mismatched_and_empty():
    assert parse_media_tags("<picture>林雪</video>") == []
    assert parse_media_tags("<picture>   </picture>") == []
    assert parse_media_tags("普通 <b>不是媒体</b>") == []
    assert parse_media_tags("") == []


def test_strip_media_tags_keeps_name():
    assert strip_media_tags("林雪走进<picture>林雪</picture>的街道") == "林雪走进林雪的街道"
    assert strip_media_tags("<video>动作.mp4</video>") == "动作.mp4"
    assert strip_media_tags("没有标签") == "没有标签"
    assert strip_media_tags("") == ""


def test_match_asset_exact_equal():
    asset = types.SimpleNamespace(id="a1", name="林雪")
    assert _match_asset_exact([asset], "林雪") is asset


def test_match_asset_exact_contained():
    asset = types.SimpleNamespace(id="a2", name="能量剑")
    assert _match_asset_exact([asset], "能量剑 蓝色") is asset
    assert _match_asset_exact([asset], "剑") is None  # 单字不匹配


def test_match_asset_exact_empty_short():
    assert _match_asset_exact([types.SimpleNamespace(id="a3", name="霓虹街区")], "  ") is None
    assert _match_asset_exact([types.SimpleNamespace(id="a4", name="街")], "街") is None  # 名长 <2


def test_match_asset_exact_pool_priority():
    scene_asset = types.SimpleNamespace(id="sc1", name="霓虹街区")
    global_asset = types.SimpleNamespace(id="gl1", name="霓虹街区")
    assert _match_asset_exact([scene_asset, global_asset], "霓虹街区") is scene_asset


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"\n{len(tests)} tests OK")


if __name__ == "__main__":
    _main()
