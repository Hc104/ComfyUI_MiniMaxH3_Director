#!/usr/bin/env python3
"""V1.11.2：/minimax/director/generations/archive 生成版本归档路由单元测试。

回归背景（#398）：H3 视频实际落在 ComfyUI output/video/ 子目录，但归档路由只拼
basename（src = output/<filename>），导致 src 不存在 → 404 → 前端 videoFile="" →
版本条缩略图灰色、点开播放器也 404。修复为接受可选 subfolder 参数并在
src 里拼上子目录。

覆盖：带 subfolder 归档成功（关键回归）、无 subfolder 根目录归档、缺参数 400、
源文件不存在 404。

以 `ComfyUI_MiniMaxH3_Director.director.*` 包路径导入（与 ComfyUI 运行时一致），
桩掉 torch / folder_paths / aiohttp / server 依赖。

运行：
    python3 director/tests/test_generation_archive_route.py
或
    pytest director/tests/test_generation_archive_route.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types

# ---------- 依赖桩（在导入 http_routes 之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包：让 director 包以 ComfyUI_MiniMaxH3_Director.director 层级存在。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

_OUTPUT_DIR = tempfile.mkdtemp(prefix="minimax_archive_route_out_")
_INPUT_DIR = tempfile.mkdtemp(prefix="minimax_archive_route_in_")

# folder_paths 桩：output=input=临时目录（http_routes 模块级 CHUNK_ROOT 需要 get_temp_directory）。
_TEMP_DIR = tempfile.mkdtemp(prefix="minimax_archive_route_tmp_")
_FOLDER_PATHS = types.ModuleType("folder_paths")
_FOLDER_PATHS.get_output_directory = lambda: _OUTPUT_DIR
_FOLDER_PATHS.get_input_directory = lambda: _INPUT_DIR
_FOLDER_PATHS.get_temp_directory = lambda: _TEMP_DIR
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

# torch 桩（http_routes 或依赖链上可能 import torch）。
_torch = types.ModuleType("torch")
sys.modules.setdefault("torch", _torch)

# 现在导入被测试的路由模块。
from ComfyUI_MiniMaxH3_Director.director import http_routes  # noqa: E402

# 从模块里拿到真实 handler（供直接 await 调用）。
_archive_handler = None
for name in dir(http_routes):
    obj = getattr(http_routes, name)
    if name.startswith("minimax_generation_archive") or (
        callable(obj) and getattr(obj, "__name__", "") == "minimax_generation_archive"
    ):
        _archive_handler = obj
        break

if _archive_handler is None:
    # 兜底：名字已知，直接取。
    _archive_handler = getattr(http_routes, "minimax_generation_archive", None)


class _FakeRequest:
    """最小 request 桩：提供 await request.json()。"""

    def __init__(self, body: dict | None = None, raw: str | None = None):
        self._body = body
        self._raw = raw

    async def json(self):
        if self._raw is not None:
            return json.loads(self._raw)
        if self._body is not None:
            return self._body
        raise json.JSONDecodeError("no body", "", 0)


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"fake-mp4")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_archive_with_subfolder_success():
    """关键回归：H3 视频在 output/video/ 子目录，subfolder 必须生效。"""
    # 在 output/video/ 下放一个模拟 H3 输出文件。
    os.makedirs(os.path.join(_OUTPUT_DIR, "video"), exist_ok=True)
    fname = "MiniMaxH3_Director_r2v_00108_.mp4"
    _touch(os.path.join(_OUTPUT_DIR, "video", fname))

    req = _FakeRequest(
        {
            "project_id": "proj_cybercity",
            "shot_id": "shot_03",
            "generation_id": "gen_abc123",
            "filename": fname,
            "subfolder": "video",
        }
    )
    resp = _run(_archive_handler(req))
    assert resp.status == 200, f"expected 200, got {resp.status}: {resp.payload}"
    assert resp.payload["ok"] is True
    rel = resp.payload["video_file"]
    assert rel.startswith("minimax_studio/generations/proj_cybercity/shot_03/")
    assert rel.endswith(".mp4")
    # 归档副本真实存在（input 目录）。
    dst = os.path.join(_INPUT_DIR, *rel.split("/"))
    assert os.path.isfile(dst), f"archive copy missing: {dst}"
    # video_file 应是 input 相对路径（前端 comfyInputUrl 可直接播）。
    assert ".." not in rel and rel.startswith("minimax_studio/"), rel


def test_archive_without_subfolder_root_output():
    """无 subfolder：源文件直接在 output 根目录也应归档成功（兼容旧行为）。"""
    fname = "MiniMaxH3_Director_root.mp4"
    _touch(os.path.join(_OUTPUT_DIR, fname))

    req = _FakeRequest(
        {
            "project_id": "proj_a",
            "shot_id": "s1",
            "generation_id": "g1",
            "filename": fname,
            "subfolder": "",
        }
    )
    resp = _run(_archive_handler(req))
    assert resp.status == 200, resp.payload
    assert resp.payload["ok"] is True


def test_archive_missing_source_404():
    """源文件不存在（即使给了 subfolder）→ 404 not_found，前端降级回退。"""
    req = _FakeRequest(
        {
            "project_id": "proj_a",
            "shot_id": "s1",
            "generation_id": "g1",
            "filename": "does_not_exist.mp4",
            "subfolder": "video",
        }
    )
    resp = _run(_archive_handler(req))
    assert resp.status == 404
    assert resp.payload["error"] == "not_found"


def test_archive_bad_params_400():
    """缺 project_id → 400。"""
    req = _FakeRequest({"shot_id": "s1", "generation_id": "g1", "filename": "x.mp4", "subfolder": ""})
    resp = _run(_archive_handler(req))
    assert resp.status == 400
    assert resp.payload["error"] == "bad_params"


def test_archive_bad_json_400():
    """坏 JSON → 400。"""
    req = _FakeRequest(raw="{not valid json")
    resp = _run(_archive_handler(req))
    assert resp.status == 400


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
