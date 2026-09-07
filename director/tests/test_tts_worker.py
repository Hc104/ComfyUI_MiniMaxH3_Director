#!/usr/bin/env python3
"""Phase 0：TTS 落盘 worker 单元测试（FakeBackend 直接写假文件，不碰网络）。

覆盖：
- shot_output_dir：分层目录结构 output/minimax_studio/projects/{项目}/shots/{shot}/ + tts/ 子目录
- build_manifest：纯函数字段 / line_NNN.wav 命名 / 空 lines
- synthesize_shot：逐行落盘 tts/line_NNN.wav + manifest.json 写盘 + 返回摘要
- 失败传播：某行合成失败 → RuntimeError 且带 line 定位
- ⛔ 纯规则零 LLM 零显存：worker 源码不 import torch / ollama / requests

运行：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" director/tests/test_tts_worker.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import sys
import tempfile
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

from ComfyUI_MiniMaxH3_Director.director import tts_worker  # noqa: E402
from ComfyUI_MiniMaxH3_Director.director.audio_intent import AudioIntent, VoiceLine  # noqa: E402
from ComfyUI_MiniMaxH3_Director.director.tts_engine import TtsEngine  # noqa: E402


class _FakeBackend(TtsEngine):
    """Fake TTS：直接写假 wav 文件，记录最后一次调用，不碰网络。"""

    name = "fake"

    def __init__(self):
        self.last: dict | None = None
        self.fail_on: str | None = None  # 包含该文本的行抛错（模拟失败）

    async def synthesize(
        self, text, voice_id, output_path, *, emotion="", delivery=""
    ):
        if self.fail_on and self.fail_on in text:
            raise RuntimeError(f"fake synth boom: {text}")
        parent = os.path.dirname(str(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(str(output_path), "wb") as f:
            f.write(b"fake-wav")
        self.last = {
            "text": text,
            "voice_id": voice_id,
            "output_path": str(output_path),
            "emotion": emotion,
            "delivery": delivery,
        }
        return str(output_path)


def _write_wav(path, sr=16000, dur=2.0):
    """写标准 PCM WAV（#583：真实时长探测用）。"""
    data_bytes = int(sr * 2 * dur)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_bytes))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", data_bytes))
        f.write(b"\x00" * data_bytes)


class _RealWavBackend(TtsEngine):
    """#583：写真 WAV 的 Fake TTS（合成后能探测真实时长）。"""

    name = "fake-real-wav"

    async def synthesize(
        self, text, voice_id, output_path, *, emotion="", delivery=""
    ):
        parent = os.path.dirname(str(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        _write_wav(str(output_path), dur=2.0)
        return str(output_path)


def _run(coro):
    # Python 3.13：asyncio.get_event_loop() 不再隐式创建，统一 asyncio.run()（同 test_tts_engine）。
    return asyncio.run(coro)


def _make_intent(*, shot_id="shot_001", scene_id="scene_01"):
    return AudioIntent(
        shot_id=shot_id,
        scene_id=scene_id,
        lines=[
            VoiceLine(
                text="完了，穿越了。",
                speaker="林薇薇",
                voice_id="voice_林薇薇",
                voice_type="character_dialogue",
                emotion="震惊",
                delivery="语速偏快",
            ),
            VoiceLine(
                text="林晓感觉胸口那股郁结之气缓缓散开。",
                speaker="",
                voice_id="voice_narrator",
                voice_type="narration",
            ),
        ],
        ambient="雨声淅沥",
        music="紧张氛围配乐渐起",
    )


def _module_path() -> str:
    return os.path.join(_REPO_ROOT, "director", "tts_worker.py")


def _has_import(src: str, bad: str) -> bool:
    return re.search(rf"(?m)^\s*(?:import|from)\s+{re.escape(bad)}\b", src) is not None


def test_shot_output_dir_structure():
    base = tempfile.mkdtemp(prefix="tts_worker_dir_")
    d = tts_worker.shot_output_dir("吐槽在漫画里封神", "shot_001", base_dir=base)
    rel = os.path.relpath(d, base).replace("\\", "/")
    assert rel == "minimax_studio/projects/吐槽在漫画里封神/shots/shot_001"
    assert os.path.isdir(d)
    assert os.path.isdir(os.path.join(d, "tts"))


def test_shot_output_dir_sanitizes_bad_name():
    base = tempfile.mkdtemp(prefix="tts_worker_dir2_")
    d = tts_worker.shot_output_dir("a/b:c", "shot?1", base_dir=base)
    rel = os.path.relpath(d, base).replace("\\", "/")
    assert "/" not in rel.split("projects/")[1].split("/shots/")[0]  # 项目名不含 /
    assert "minimax_studio/projects/" in rel


def test_build_manifest_pure():
    intent = _make_intent()
    manifest = tts_worker.build_manifest(intent, "吐槽在漫画里封神", engine="fake")
    assert manifest["project"] == "吐槽在漫画里封神"
    assert manifest["shot_id"] == "shot_001"
    assert manifest["scene_id"] == "scene_01"
    assert manifest["ambient"] == "雨声淅沥"
    assert manifest["music"] == "紧张氛围配乐渐起"
    assert manifest["engine"] == "fake"
    assert len(manifest["lines"]) == 2
    l1 = manifest["lines"][0]
    assert l1["index"] == 1
    assert l1["file"] == "tts/line_001.wav"
    assert l1["text"] == "完了，穿越了。"
    assert l1["speaker"] == "林薇薇"
    assert l1["voice_id"] == "voice_林薇薇"
    assert l1["emotion"] == "震惊"
    assert l1["delivery"] == "语速偏快"
    assert l1["duration_sec"] is None
    assert manifest["lines"][1]["file"] == "tts/line_002.wav"


def test_build_manifest_empty_lines():
    intent = AudioIntent(shot_id="shot_02")
    manifest = tts_worker.build_manifest(intent, "空镜", engine="fake")
    assert manifest["lines"] == []
    assert manifest["shot_id"] == "shot_02"


def test_synthesize_shot_writes_lines_and_manifest():
    base = tempfile.mkdtemp(prefix="tts_worker_synth_")
    backend = _FakeBackend()
    intent = _make_intent()
    summary = _run(tts_worker.synthesize_shot(intent, "吐槽在漫画里封神", backend=backend, base_dir=base))

    shot_dir = summary["shot_dir"]
    assert os.path.isfile(os.path.join(shot_dir, "tts", "line_001.wav"))
    assert os.path.isfile(os.path.join(shot_dir, "tts", "line_002.wav"))
    assert summary["line_count"] == 2

    # manifest.json 落盘且内容正确
    with open(os.path.join(shot_dir, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["project"] == "吐槽在漫画里封神"
    assert manifest["shot_id"] == "shot_001"
    assert manifest["engine"] == "fake"
    assert len(manifest["lines"]) == 2
    assert manifest["lines"][0]["file"] == "tts/line_001.wav"

    # backend 最后一次调用参数透传正确（第二行旁白）
    assert backend.last["text"] == "林晓感觉胸口那股郁结之气缓缓散开。"
    assert backend.last["voice_id"] == "voice_narrator"
    assert backend.last["emotion"] == ""
    assert backend.last["delivery"] == ""
    # Windows 路径分隔符为反斜杠，统一转正斜杠再断言（平台无关，Linux/Windows 都能跑）
    out_norm = backend.last["output_path"].replace("\\", "/")
    assert out_norm.endswith("tts/line_002.wav")


def test_synthesize_shot_empty_lines_still_writes_manifest():
    base = tempfile.mkdtemp(prefix="tts_worker_empty_")
    backend = _FakeBackend()
    intent = AudioIntent(shot_id="shot_03", ambient="风声")
    summary = _run(tts_worker.synthesize_shot(intent, "无台词镜", backend=backend, base_dir=base))
    assert summary["line_count"] == 0
    with open(os.path.join(summary["shot_dir"], "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["lines"] == []


def test_synthesize_shot_failure_propagates_with_line_locator():
    base = tempfile.mkdtemp(prefix="tts_worker_fail_")
    backend = _FakeBackend()
    backend.fail_on = "林晓感觉"  # 第二行失败
    intent = _make_intent()
    try:
        _run(tts_worker.synthesize_shot(intent, "失败镜", backend=backend, base_dir=base))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        msg = str(exc)
        assert "line_002" in msg
        assert "shot_001" in msg
        assert "voice_narrator" in msg


def test_synthesize_shot_records_real_duration():
    """#583：TTS 合成后立即探测真实音频时长写入 manifest（时间轴唯一事实源）。
    Edge-TTS riff-pcm 落盘为标准 WAV → duration_sec = 真实值（非估算）。"""
    base = tempfile.mkdtemp(prefix="tts_worker_dur_")
    backend = _RealWavBackend()
    intent = _make_intent()
    summary = _run(
        tts_worker.synthesize_shot(intent, "时长镜", backend=backend, base_dir=base)
    )
    with open(os.path.join(summary["shot_dir"], "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    for ln in manifest["lines"]:
        assert ln["duration_sec"] is not None
        assert abs(ln["duration_sec"] - 2.0) < 1e-3  # _write_wav dur=2.0


def test_no_llm_gpu_side_effects():
    with open(_module_path(), "r", encoding="utf-8") as f:
        src = f.read()
    for bad in ("torch", "ollama", "requests"):
        assert not _has_import(src, bad), f"tts_worker.py 不应 import {bad}"


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
