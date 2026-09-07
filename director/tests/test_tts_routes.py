#!/usr/bin/env python3
"""Phase 0：/tts/voices + /tts/synthesize HTTP 路由单元测试（纯 mock，不碰网络）。

与 test_generation_archive_route.py 同款桩模式：folder_paths / aiohttp.web / server / torch
桩掉 + edge_tts 桩（Communicate 写假文件）。edge-tts 真实验收在用户本机。

覆盖：
- GET tts/voices：ok + engine=edge-tts + voices 列表
- POST tts/synthesize：正常落盘（output 分层目录 + tts/line_NNN.wav + manifest.json）+ 摘要
- 缺 project_name/shot_id → 400 bad_params
- 坏 JSON → 400 bad_json
- edge_tts 缺失 → 500（preflight 报错带安装命令）
- 空 lines → 200 ok + line_count 0 + manifest 落盘

运行：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" director/tests/test_tts_routes.py
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

_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

_OUTPUT_DIR = tempfile.mkdtemp(prefix="minimax_tts_route_out_")
_INPUT_DIR = tempfile.mkdtemp(prefix="minimax_tts_route_in_")
_TEMP_DIR = tempfile.mkdtemp(prefix="minimax_tts_route_tmp_")

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


class _FakeCommunicate:
    LAST: dict = {}

    def __init__(self, text, voice, **kwargs):
        self.text = text
        self.voice = voice
        self.kwargs = kwargs

    async def save(self, path):
        parent = os.path.dirname(str(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(str(path), "wb") as f:
            f.write(b"fake-tts-wav")
        _FakeCommunicate.LAST = {
            "text": self.text,
            "voice": self.voice,
            "kwargs": dict(self.kwargs),
            "path": str(path),
        }


_edge_tts = types.ModuleType("edge_tts")
_edge_tts.Communicate = _FakeCommunicate
sys.modules.setdefault("edge_tts", _edge_tts)

# 现在导入被测试的路由模块。
from ComfyUI_MiniMaxH3_Director.director import http_routes  # noqa: E402

_voices_handler = getattr(http_routes, "minimax_director_tts_voices", None)
_synth_handler = getattr(http_routes, "minimax_director_tts_synthesize", None)


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
    # Python 3.13：asyncio.get_event_loop() 不再隐式创建，统一 asyncio.run()（同 test_tts_engine）。
    return asyncio.run(coro)


def _shot_dir(project="吐槽在漫画里封神", shot="shot_001"):
    return os.path.join(_OUTPUT_DIR, "minimax_studio", "projects", project, "shots", shot)


def test_tts_voices_returns_list():
    resp = _run(_voices_handler(_FakeRequest({})))
    assert resp.status == 200
    assert resp.payload["ok"] is True
    assert resp.payload["engine"] == "edge-tts"
    voices = resp.payload["voices"]
    assert isinstance(voices, list) and len(voices) >= 5
    assert all(v["engine"] == "edge-tts" for v in voices)
    assert all(v["voice_id"].startswith("zh-CN-") for v in voices)


def test_tts_synthesize_success_writes_layered_files():
    body = {
        "project_name": "吐槽在漫画里封神",
        "shot_id": "shot_001",
        "scene_id": "scene_01",
        "ambient": "雨声淅沥",
        "music": "紧张氛围配乐渐起",
        "lines": [
            {"text": "完了，穿越了。", "speaker": "林薇薇", "voice_id": "voice_林薇薇", "emotion": "震惊", "delivery": "语速偏快"},
            {"text": "林晓感觉胸口那股郁结之气缓缓散开。", "voice_type": "narration", "voice_id": "voice_narrator"},
        ],
    }
    resp = _run(_synth_handler(_FakeRequest(body)))
    assert resp.status == 200, f"{resp.status}: {resp.payload}"
    assert resp.payload["ok"] is True
    assert resp.payload["line_count"] == 2

    shot_dir = resp.payload["shot_dir"]
    assert shot_dir == _shot_dir()
    assert os.path.isfile(os.path.join(shot_dir, "tts", "line_001.wav"))
    assert os.path.isfile(os.path.join(shot_dir, "tts", "line_002.wav"))

    with open(os.path.join(shot_dir, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["project"] == "吐槽在漫画里封神"
    assert manifest["shot_id"] == "shot_001"
    assert manifest["ambient"] == "雨声淅沥"
    assert manifest["engine"] == "edge-tts"
    assert len(manifest["lines"]) == 2
    # 第二行 type 应被 normalize（narration 保留）
    assert manifest["lines"][1]["voice_type"] == "narration"
    assert manifest["lines"][1]["file"] == "tts/line_002.wav"


def test_tts_synthesize_audio_intent_dict_form():
    """兼容 body 直接传 audio_intent dict（AudioIntent.to_dict 格式）。"""
    body = {
        "project_name": "兼容镜",
        "audio_intent": {
            "shot_id": "shot_002",
            "lines": [
                {"text": "系统提示：吐槽值+50！", "speaker": "系统", "voice_type": "system_voice", "voice_id": "voice_system_electronic", "delivery": "电子"},
            ],
        },
    }
    resp = _run(_synth_handler(_FakeRequest(body)))
    assert resp.status == 200, f"{resp.status}: {resp.payload}"
    assert resp.payload["line_count"] == 1
    shot_dir = resp.payload["shot_dir"]
    assert os.path.isfile(os.path.join(shot_dir, "tts", "line_001.wav"))
    last = _FakeCommunicate.LAST
    assert last["voice"] == "zh-CN-YunxiNeural"
    assert last["kwargs"].get("pitch") == "+5Hz"


def test_tts_synthesize_missing_params_400():
    resp = _run(_synth_handler(_FakeRequest({"shot_id": "shot_001"})))
    assert resp.status == 400
    assert resp.payload["error"] == "bad_params"
    resp2 = _run(_synth_handler(_FakeRequest({"project_name": "x"})))
    assert resp2.status == 400


def test_tts_synthesize_bad_json_400():
    resp = _run(_synth_handler(_FakeRequest(raw="{not json")))
    assert resp.status == 400
    assert resp.payload["error"] == "bad_json"


def test_tts_synthesize_empty_lines_ok_with_manifest():
    body = {"project_name": "空镜", "shot_id": "shot_003", "lines": []}
    resp = _run(_synth_handler(_FakeRequest(body)))
    assert resp.status == 200, f"{resp.status}: {resp.payload}"
    assert resp.payload["line_count"] == 0
    shot_dir = resp.payload["shot_dir"]
    with open(os.path.join(shot_dir, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["lines"] == []


def test_tts_synthesize_missing_edge_tts_500():
    """edge_tts 缺失 → preflight 失败 → 500（报错含安装命令）。"""
    orig = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = None
    try:
        body = {"project_name": "缺依赖", "shot_id": "shot_004", "lines": [{"text": "测试"}]}
        resp = _run(_synth_handler(_FakeRequest(body)))
        assert resp.status == 500
        assert "edge-tts" in resp.payload["error"]
    finally:
        if orig is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = orig


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
