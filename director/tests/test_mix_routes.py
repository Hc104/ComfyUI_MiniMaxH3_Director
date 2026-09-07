#!/usr/bin/env python3
"""Phase 1：/tts/mix HTTP 路由单元测试（mock mix_shot，不真调 ffmpeg）。

与 test_tts_routes.py 同款桩模式（folder_paths / aiohttp.web / server / torch 桩）。
mix_shot 直接 patch（http_routes 命名空间绑定），ffmpeg 执行不触发。

覆盖：
- POST tts/mix：h3_tts 成功 200 + payload（final_file/line_count/lines）
- mode=h3_only 透传
- 缺 project_name/shot_id → 400 bad_params
- 坏 JSON → 400 bad_json
- mode 非法 → 400 bad_params
- 缺 video/manifest/tts / ffmpeg 失败 → 500（error 含定位）

运行：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" director/tests/test_mix_routes.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
from unittest import mock

# ---------- 依赖桩（在导入 http_routes 之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

_OUTPUT_DIR = tempfile.mkdtemp(prefix="minimax_mix_route_out_")
_INPUT_DIR = tempfile.mkdtemp(prefix="minimax_mix_route_in_")
_TEMP_DIR = tempfile.mkdtemp(prefix="minimax_mix_route_tmp_")

_FOLDER_PATHS = types.ModuleType("folder_paths")
_FOLDER_PATHS.get_output_directory = lambda: _OUTPUT_DIR
_FOLDER_PATHS.get_input_directory = lambda: _INPUT_DIR
_FOLDER_PATHS.get_temp_directory = lambda: _TEMP_DIR
sys.modules.setdefault("folder_paths", _FOLDER_PATHS)


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

_server = types.ModuleType("server")
_server.PromptServer = types.SimpleNamespace(instance=None)
sys.modules.setdefault("server", _server)

_torch = types.ModuleType("torch")
sys.modules.setdefault("torch", _torch)

from ComfyUI_MiniMaxH3_Director.director import http_routes  # noqa: E402

_mix_handler = getattr(http_routes, "minimax_director_tts_mix", None)
assert _mix_handler is not None, "http_routes 未找到 minimax_director_tts_mix"


class _FakeRequest:
    def __init__(self, body: dict | None = None, raw: str | None = None):
        self._body = body
        self._raw = raw

    async def json(self):
        if self._raw is not None:
            return json.loads(self._raw)
        if self._body is not None:
            return self._body
        raise json.JSONDecodeError("no body", "", 0)


def _run(coro):
    return asyncio.run(coro)


def _shot_dir(project="吐槽在漫画里封神", shot="shot_001"):
    return os.path.join(_OUTPUT_DIR, "minimax_studio", "projects", project, "shots", shot)


def _fake_result(mode="h3_tts"):
    shot_dir = _shot_dir()
    return {
        "mode": mode,
        "shot_dir": shot_dir,
        "video_file": os.path.join(shot_dir, "video.mp4"),
        "h3_audio_file": os.path.join(shot_dir, "h3_audio.wav"),
        "final_file": os.path.join(shot_dir, "final.mp4"),
        "line_count": 2,
        "lines": [
            {"index": 1, "start_sec": 0.0, "end_sec": 1.2, "duration_sec": 1.2},
            {"index": 2, "start_sec": 1.5, "end_sec": 2.1, "duration_sec": 0.6},
        ],
    }


def test_mix_success_h3_tts():
    body = {"project_name": "吐槽在漫画里封神", "shot_id": "shot_001"}
    with mock.patch.object(http_routes, "mix_shot", return_value=_fake_result("h3_tts")) as m:
        resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 200, f"{resp.status}: {resp.payload}"
    assert resp.payload["ok"] is True
    assert resp.payload["mode"] == "h3_tts"
    assert resp.payload["final_file"].endswith("final.mp4")
    assert resp.payload["line_count"] == 2
    assert len(resp.payload["lines"]) == 2
    m.assert_called_once_with(_shot_dir(), mode="h3_tts")


def test_mix_h3_only_mode_passthrough():
    body = {"project_name": "吐槽在漫画里封神", "shot_id": "shot_001", "mode": "h3_only"}
    with mock.patch.object(http_routes, "mix_shot", return_value=_fake_result("h3_only")) as m:
        resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 200
    assert resp.payload["ok"] is True
    assert resp.payload["mode"] == "h3_only"
    m.assert_called_once_with(_shot_dir(), mode="h3_only")


def test_mix_missing_params_400():
    resp = _run(_mix_handler(_FakeRequest({"shot_id": "shot_001"})))
    assert resp.status == 400
    assert resp.payload["error"] == "bad_params"
    resp2 = _run(_mix_handler(_FakeRequest({"project_name": "x"})))
    assert resp2.status == 400


def test_mix_bad_json_400():
    resp = _run(_mix_handler(_FakeRequest(raw="{not json")))
    assert resp.status == 400
    assert resp.payload["error"] == "bad_json"


def test_mix_bad_mode_400():
    body = {"project_name": "p", "shot_id": "s", "mode": "surround"}
    resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 400
    assert resp.payload["error"] == "bad_params"


def test_mix_failure_500_with_locator():
    """mix_shot 抛 RuntimeError（缺视频/manifest/tts/ffmpeg 失败）→ 500 + error 定位。"""
    body = {"project_name": "吐槽在漫画里封神", "shot_id": "shot_001"}
    with mock.patch.object(
        http_routes, "mix_shot", side_effect=RuntimeError("缺少 H3 视频：.../video.mp4")
    ):
        resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 500
    assert resp.payload["ok"] is False
    assert "缺少 H3 视频" in resp.payload["error"]


def test_mix_success_includes_final_rel():
    """Phase 2-E（#577）：成功响应带 final_rel（output 根相对路径，前端 /view 直接播放）。"""
    body = {"project_name": "吐槽在漫画里封神", "shot_id": "shot_001"}
    with mock.patch.object(http_routes, "mix_shot", return_value=_fake_result("h3_tts")):
        resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 200
    rel = resp.payload.get("final_rel", "")
    assert rel
    assert rel == os.path.join("minimax_studio", "projects", "吐槽在漫画里封神", "shots", "shot_001", "final.mp4").replace(os.sep, "/")
    assert not rel.startswith("..")


def test_mix_stages_h3_video_from_output_when_missing():
    """Phase 2-E（#577）：shot 目录缺 video.mp4 + 前端传 video_filename/subfolder →
    先从 ComfyUI output 拷入，再调 mix_shot。"""
    import shutil as _shutil

    shot_dir = _shot_dir()
    os.makedirs(shot_dir, exist_ok=True)
    # output 根下造一个假的 H3 成片
    src = os.path.join(_OUTPUT_DIR, "minimax_studio", "videos", "h3_shot_001.mp4")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w", encoding="utf-8") as f:
        f.write("fake-mp4")
    video_path = os.path.join(shot_dir, "video.mp4")
    if os.path.isfile(video_path):
        os.remove(video_path)
    body = {
        "project_name": "吐槽在漫画里封神",
        "shot_id": "shot_001",
        "video_filename": "h3_shot_001.mp4",
        "video_subfolder": "minimax_studio/videos",
    }
    with mock.patch.object(http_routes, "mix_shot", return_value=_fake_result("h3_tts")) as m:
        resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 200
    assert os.path.isfile(video_path), "video.mp4 应从 output 自动落位到 shot 分层目录"
    with open(video_path, "r", encoding="utf-8") as f:
        assert f.read() == "fake-mp4"
    m.assert_called_once_with(shot_dir, mode="h3_tts")


def test_mix_does_not_overwrite_existing_video():
    """video.mp4 已存在 → 不重拷（幂等重混音）；即使传了 video_filename 也跳过落位。"""
    shot_dir = _shot_dir()
    os.makedirs(shot_dir, exist_ok=True)
    video_path = os.path.join(shot_dir, "video.mp4")
    with open(video_path, "w", encoding="utf-8") as f:
        f.write("original-mp4")
    body = {
        "project_name": "吐槽在漫画里封神",
        "shot_id": "shot_001",
        "video_filename": "h3_shot_001.mp4",
        "video_subfolder": "minimax_studio/videos",
    }
    with mock.patch.object(http_routes, "mix_shot", return_value=_fake_result("h3_tts")):
        resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 200
    with open(video_path, "r", encoding="utf-8") as f:
        assert f.read() == "original-mp4"


def test_mix_missing_video_and_no_filename_still_calls_mix():
    """视频缺且前端没给定位 → 不落位但也不阻塞（mix_shot 自己会 500 缺视频定位）。"""
    shot_dir = _shot_dir()
    os.makedirs(shot_dir, exist_ok=True)
    video_path = os.path.join(shot_dir, "video.mp4")
    if os.path.isfile(video_path):
        os.remove(video_path)
    body = {"project_name": "吐槽在漫画里封神", "shot_id": "shot_001"}
    with mock.patch.object(http_routes, "mix_shot", return_value=_fake_result("h3_tts")) as m:
        resp = _run(_mix_handler(_FakeRequest(body)))
    assert resp.status == 200
    assert not os.path.isfile(video_path)
    m.assert_called_once_with(shot_dir, mode="h3_tts")


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
