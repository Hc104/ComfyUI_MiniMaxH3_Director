#!/usr/bin/env python3
"""V1.6-A：/minimax/director/export 流式导出路由单元测试。

覆盖：movie 全片成功合并（顺序=shot_ids）、scene 场景子集、缺失镜头 not_cached、
skip_missing 跳过、无任何可导出 nothing_to_export、ffmpeg 缺失、参数校验。
stream_export 的 save_segment_mp4 / merge_segment_mp4s / make_stream_dir /
resolve_movie_output_path / ffmpeg_bin 全部打桩——真实 PyAV 编码/ffmpeg 合并
行为由 stream_export 自身（配合 ComfyUI 环境）保证，本测试只验证路由编排。

以 `ComfyUI_MiniMaxH3_Director.director.*` 包路径导入（与 ComfyUI 运行时一致），
桩掉 torch / folder_paths / aiohttp / server 依赖。

运行：
    python3 director/tests/test_export_route.py
或
    pytest director/tests/test_export_route.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import uuid

# ---------- 依赖桩（在导入 http_routes 之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包：让 director 包以 ComfyUI_MiniMaxH3_Director.director 层级存在。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

_OUTPUT_DIR = tempfile.mkdtemp(prefix="minimax_export_route_test_")
_TEMP_DIR = tempfile.mkdtemp(prefix="minimax_export_route_tmp_")

# folder_paths 桩：输出/临时目录指向临时路径。
_FOLDER_PATHS = types.ModuleType("folder_paths")
_FOLDER_PATHS.get_output_directory = lambda: _OUTPUT_DIR
_FOLDER_PATHS.get_temp_directory = lambda: _TEMP_DIR
_FOLDER_PATHS.get_input_directory = lambda: _OUTPUT_DIR
sys.modules.setdefault("folder_paths", _FOLDER_PATHS)

# aiohttp.web 桩：Response / json_response 返回可读结果对象。
class _Resp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def json(self):
        return self.payload


def _json_response(data, status=200):
    return _Resp(data, status=status)


def _response(text, status=200):
    return _Resp(text, status=status)


_web = types.ModuleType("aiohttp.web")
_web.Response = _response
_web.json_response = _json_response
_aiohttp = types.ModuleType("aiohttp")
_aiohttp.web = _web
sys.modules.setdefault("aiohttp", _aiohttp)

# server 桩：http_routes 顶层 import PromptServer（不调用 register_routes）。
_server = types.ModuleType("server")
_server.PromptServer = types.SimpleNamespace(instance=None)
sys.modules.setdefault("server", _server)


class FakeTensor:
    """极简「视频张量」桩：shape=(T,H,W,3)，够 route 取尺寸/判 ndim。"""

    def __init__(self, frames=10, h=736, w=1280):
        self.shape = (frames, h, w, 3)
        self.ndim = 4

    def cpu(self):
        return self

    def float(self):
        return self

    def contiguous(self):
        return self


_torch = types.ModuleType("torch")
_torch.Tensor = FakeTensor
_torch.float32 = "float32"
_torch.load = lambda _path, **kw: FakeTensor()
sys.modules.setdefault("torch", _torch)

# ---------- 被测模块（桩注入后导入）----------
from ComfyUI_MiniMaxH3_Director.director import http_routes  # noqa: E402
from ComfyUI_MiniMaxH3_Director.director import stream_export  # noqa: E402

# ---------- stream_export 打桩 ----------
MERGED_CALLS: list[tuple[list[str], str]] = []
STREAM_DIRS: list[str] = []


def _fake_ffmpeg_bin():
    return "/usr/bin/ffmpeg"


def _fake_make_stream_dir(node_id):
    d = os.path.join(_TEMP_DIR, "stream_" + str(node_id or "run").replace("/", "_"))
    os.makedirs(d, exist_ok=True)
    STREAM_DIRS.append(d)
    for name in os.listdir(d):
        try:
            os.remove(os.path.join(d, name))
        except OSError:
            pass
    return d


def _fake_save_segment_mp4(path, _frames, _audio, *, fps):
    with open(path, "wb") as fh:
        fh.write(b"FAKEMP4")
    return path


def _fake_merge_segment_mp4s(segment_paths, out_path):
    MERGED_CALLS.append((list(segment_paths), out_path))
    with open(out_path, "wb") as fh:
        fh.write(b"FAKEMOVIE")
    return out_path


def _fake_resolve_movie_output_path(_width, _height, *, prefix):
    return os.path.join(_OUTPUT_DIR, f"{prefix}_00001_.mp4")


def _patch_stream_export():
    stream_export.ffmpeg_bin = _fake_ffmpeg_bin
    stream_export.make_stream_dir = _fake_make_stream_dir
    stream_export.save_segment_mp4 = _fake_save_segment_mp4
    stream_export.merge_segment_mp4s = _fake_merge_segment_mp4s
    stream_export.resolve_movie_output_path = _fake_resolve_movie_output_path


# ---------- 缓存夹具 ----------
def _write_cache(node_id: str, shot_ids: list[str]) -> str:
    """在输出目录建 minimax_seg_cache/<node> 缓存，seg_%04d.pt + meta.json(id)。"""
    root = os.path.join(_OUTPUT_DIR, "minimax_seg_cache", node_id)
    os.makedirs(root, exist_ok=True)
    for idx, sid in enumerate(shot_ids):
        with open(os.path.join(root, f"seg_{idx:04d}.pt"), "wb") as fh:
            fh.write(b"FAKEPT")
        with open(os.path.join(root, f"seg_{idx:04d}.meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": sid}, fh, ensure_ascii=False)
    return root


def _req(**body):
    async def _json():
        return body

    return types.SimpleNamespace(json=_json)


def _run(coro):
    return asyncio.run(coro)


def _fresh_node() -> str:
    return f"export-{uuid.uuid4().hex[:8]}"


# ---------- 测试 ----------
def test_export_movie_success_preserves_order():
    _patch_stream_export()
    MERGED_CALLS.clear()
    node = _fresh_node()
    # 缓存索引顺序 s1/s2/s3，但请求顺序故意打乱 → 导出顺序必须按 shot_ids。
    _write_cache(node, ["s1", "s2", "s3"])
    resp = _run(http_routes.minimax_director_export(_req(
        node_id=node, scope="movie", shot_ids=["s3", "s1", "s2"], fps=24.0,
    )))
    assert resp.status == 200
    data = resp.json()
    assert "error" not in data, data
    assert data["segments"] == 3
    assert data["missing"] == []
    assert data["subfolder"] == "" and data["type"] == "output"
    assert data["filename"].endswith(".mp4") and data["scope"] == "movie"
    # 合并顺序 = shot_ids 顺序（s3 → s1 → s2），不是缓存索引顺序。
    assert len(MERGED_CALLS) == 1
    seg_paths, _out = MERGED_CALLS[0]
    assert len(seg_paths) == 3
    idxs = [int(os.path.basename(p).split("_")[1].split(".")[0]) for p in seg_paths]
    assert idxs == [2, 0, 1]  # s3(idx2), s1(idx0), s2(idx1)


def test_export_scene_subset():
    _patch_stream_export()
    MERGED_CALLS.clear()
    node = _fresh_node()
    _write_cache(node, ["s1", "s2", "s3"])
    resp = _run(http_routes.minimax_director_export(_req(
        node_id=node, scope="scene", scene_id="scene-a", shot_ids=["s2", "s3"],
    )))
    data = resp.json()
    assert "error" not in data, data
    assert data["segments"] == 2
    assert data["scope"] == "scene" and data["sceneId"] == "scene-a"
    assert data["filename"].startswith("MiniMax_Studio_Scene")
    idxs = [int(os.path.basename(p).split("_")[1].split(".")[0]) for p in MERGED_CALLS[0][0]]
    assert idxs == [1, 2]


def test_export_not_cached_lists_missing():
    _patch_stream_export()
    node = _fresh_node()
    _write_cache(node, ["s1", "s2"])
    resp = _run(http_routes.minimax_director_export(_req(
        node_id=node, scope="movie", shot_ids=["s1", "sX", "s2"],
    )))
    data = resp.json()
    assert data["error"] == "not_cached"
    assert data["missing"] == [{"shotId": "sX", "order": 1}]


def test_export_skip_missing_exports_available():
    _patch_stream_export()
    MERGED_CALLS.clear()
    node = _fresh_node()
    _write_cache(node, ["s1", "s2"])
    resp = _run(http_routes.minimax_director_export(_req(
        node_id=node, scope="movie", shot_ids=["s1", "sX", "s2"], skip_missing=True,
    )))
    data = resp.json()
    assert "error" not in data, data
    assert data["segments"] == 2
    assert data["missing"] == [{"shotId": "sX", "order": 1}]
    assert len(MERGED_CALLS) == 1


def test_export_nothing_to_export():
    _patch_stream_export()
    node = _fresh_node()
    # strict 模式（默认）全缺失 → not_cached 列全部缺失镜头；
    # skip_missing 且零可导出 → nothing_to_export。
    resp = _run(http_routes.minimax_director_export(_req(
        node_id=node, scope="movie", shot_ids=["sX", "sY"],
    )))
    assert resp.json()["error"] == "not_cached"
    resp = _run(http_routes.minimax_director_export(_req(
        node_id=node, scope="movie", shot_ids=["sX", "sY"], skip_missing=True,
    )))
    assert resp.json()["error"] == "nothing_to_export"


def test_export_ffmpeg_missing():
    _patch_stream_export()
    node = _fresh_node()
    _write_cache(node, ["s1"])
    stream_export.ffmpeg_bin = lambda: None
    try:
        resp = _run(http_routes.minimax_director_export(_req(
            node_id=node, scope="movie", shot_ids=["s1"],
        )))
    finally:
        stream_export.ffmpeg_bin = _fake_ffmpeg_bin
    data = resp.json()
    assert data["error"] == "ffmpeg_missing"


def test_export_bad_params():
    _patch_stream_export()
    resp = _run(http_routes.minimax_director_export(_req(shot_ids=["s1"])))
    assert resp.json()["error"] == "invalid_node_id"
    resp = _run(http_routes.minimax_director_export(_req(node_id="n1", shot_ids=[])))
    assert resp.json()["error"] == "bad_shot_ids"
    resp = _run(http_routes.minimax_director_export(_req(node_id="n1", shot_ids="s1")))
    assert resp.json()["error"] == "bad_shot_ids"


def test_export_old_cache_without_id_ignored():
    """老缓存 meta 无 id → 不参与导出（与 segment_status 口径一致）。"""
    _patch_stream_export()
    node = _fresh_node()
    root = os.path.join(_OUTPUT_DIR, "minimax_seg_cache", node)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "seg_0000.pt"), "wb") as fh:
        fh.write(b"FAKEPT")
    with open(os.path.join(root, "seg_0000.meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"prompt": "old"}, fh, ensure_ascii=False)  # 无 id 键
    resp = _run(http_routes.minimax_director_export(_req(
        node_id=node, scope="movie", shot_ids=["s1"],
    )))
    data = resp.json()
    assert data["error"] == "not_cached"
    assert data["missing"] == [{"shotId": "s1", "order": 0}]


def main():
    fns = [
        test_export_movie_success_preserves_order,
        test_export_scene_subset,
        test_export_not_cached_lists_missing,
        test_export_skip_missing_exports_available,
        test_export_nothing_to_export,
        test_export_ffmpeg_missing,
        test_export_bad_params,
        test_export_old_cache_without_id_ignored,
    ]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"共 {len(fns)} 项全部通过")


if __name__ == "__main__":
    main()
