#!/usr/bin/env python3
"""Phase 0：TTS Engine 抽象 + Edge-TTS 后端单元测试（纯 mock，不碰网络）。

Edge-TTS 真实验收在用户本机：standalone-env 安装 edge-tts 后生成《吐槽在漫画里封神》
一句对白能听清 + 分层落盘目录正确（本文件只验证逻辑层）。

覆盖：
- resolve_voice：语义 ID → 音色名映射 / 原生 zh-CN-* 透传 / 未知 → default / 空 → default
- delivery → rate/pitch 参数推断（快/慢/电子）
- preflight：edge_tts 可 import 不抛；缺失 → RuntimeError 且报错带 pip install 命令
- synthesize：写文件 + voice 解析 + delivery 参数透传 + emotion 仅记录
- synthesize 输出格式：.wav 目标显式请求 RIFF PCM（云端默认 MP3 字节流，mixer 读不了）；
  .mp3 目标不传 output_format
- synthesize 降级重试：音色拒绝 prosody（No audio was received）→ 自动去 prosody 重试成功
  （output_format 保留）；非该类错误 → 直接抛不重试；成功时不额外重试
- list_voices：静态中文音色表（engine=edge-tts）
- 工厂 create_default_tts_backend → EdgeTtsBackend
- _sync_synthesize：sync 包装（asyncio.run）
- ⛔ 纯规则零 LLM 零显存：源码不 import torch / ollama / requests

运行：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" director/tests/test_tts_engine.py
或
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" -m pytest director/tests/test_tts_engine.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import types

# ---------- 依赖桩（在导入 tts_engine 之前注入 edge_tts 假模块）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包：让 director 包以 ComfyUI_MiniMaxH3_Director.director 层级存在。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)


class _FakeCommunicate:
    """记录参数并写一个假音频文件，不碰网络。"""

    LAST: dict = {}
    CALLS: int = 0  # 实例化次数（验证成功时不多重试）

    def __init__(self, text, voice, **kwargs):
        self.text = text
        self.voice = voice
        self.kwargs = kwargs
        _FakeCommunicate.CALLS += 1

    async def save(self, path):
        parent = os.path.dirname(str(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(str(path), "wb") as f:
            f.write(b"fake-tts")
        _FakeCommunicate.LAST = {
            "text": self.text,
            "voice": self.voice,
            "kwargs": dict(self.kwargs),
            "path": str(path),
        }


class _RejectProsodyCommunicate(_FakeCommunicate):
    """模拟 zh-CN-YunxiNeural 实测行为：带任何 prosody 参数（rate/pitch/volume）都抛
    「No audio was received」；只带 output_format（容器格式，非 prosody）正常合成。"""

    async def save(self, path):
        prosody = {k: v for k, v in self.kwargs.items() if k != "output_format"}
        if prosody:
            raise RuntimeError(
                "No audio was received. Please verify that your parameters are correct."
            )
        await super().save(path)


class _BoomCommunicate(_FakeCommunicate):
    """模拟与 prosody 无关的硬错误（如磁盘/网络）：直接抛，引擎不应重试。"""

    async def save(self, path):
        raise OSError("disk full")


class _LegacyCommunicate(_FakeCommunicate):
    """模拟旧版 edge-tts（6.x-）：Communicate.__init__ 显式列参、无 **kwargs、
    不含 output_format。引擎应探测到不支持 → 不传该参数也合成成功
    （落盘为 MP3 内容假 .wav，由 mixer 的 ffmpeg 时长兜底消化）。"""

    def __init__(self, text, voice, rate=None, volume=None, pitch=None, boundary=None):
        self.text = text
        self.voice = voice
        self.kwargs = {
            k: v
            for k, v in {
                "rate": rate,
                "volume": volume,
                "pitch": pitch,
                "boundary": boundary,
            }.items()
            if v is not None
        }
        _FakeCommunicate.CALLS += 1


_edge_tts = types.ModuleType("edge_tts")
_edge_tts.Communicate = _FakeCommunicate
sys.modules.setdefault("edge_tts", _edge_tts)

# 现在导入被测模块（桩已就位）。
from ComfyUI_MiniMaxH3_Director.director import tts_engine  # noqa: E402


def _module_path() -> str:
    return os.path.join(_REPO_ROOT, "director", "tts_engine.py")


def _has_import(src: str, bad: str) -> bool:
    """真实 import/from 语句扫描（行首正则，docstring 提及不误报）。"""
    return re.search(rf"(?m)^\s*(?:import|from)\s+{re.escape(bad)}\b", src) is not None


def test_resolve_voice_semantic_id_maps_to_edge_name():
    """voice_narrator → 云扬（新闻/旁白）；voice_system → 云希。"""
    backend = tts_engine.EdgeTtsBackend()
    assert backend.resolve_voice("voice_narrator") == "zh-CN-YunyangNeural"
    assert backend.resolve_voice("voice_system") == "zh-CN-YunxiNeural"
    assert backend.resolve_voice("voice_system_electronic") == "zh-CN-YunxiNeural"


def test_resolve_voice_custom_map_overrides_default():
    """显式 voice_map 覆盖默认映射。"""
    backend = tts_engine.EdgeTtsBackend(voice_map={"voice_柳如烟": "zh-CN-XiaoxiaoNeural"})
    assert backend.resolve_voice("voice_柳如烟") == "zh-CN-XiaoxiaoNeural"
    # 未配置的语义 ID → default
    assert backend.resolve_voice("voice_顾琰宸") == "zh-CN-XiaoxiaoNeural"


def test_resolve_voice_native_name_passthrough():
    """已是 zh-CN-* 原生音色名 → 直接透传（Voice Cast 面板可直接选原生音色）。"""
    backend = tts_engine.EdgeTtsBackend()
    assert backend.resolve_voice("zh-CN-YunjianNeural") == "zh-CN-YunjianNeural"


def test_resolve_voice_empty_falls_back_to_default():
    backend = tts_engine.EdgeTtsBackend(default_voice="zh-CN-XiaoyiNeural")
    assert backend.resolve_voice("") == "zh-CN-XiaoyiNeural"
    assert backend.resolve_voice(None) == "zh-CN-XiaoyiNeural"


def test_delivery_params_fast_slow_electronic():
    assert tts_engine._delivery_params("") == {}
    assert tts_engine._delivery_params("低语呢喃") == {}
    assert tts_engine._delivery_params("语速偏快") == {"rate": "+20%"}
    assert tts_engine._delivery_params("缓慢低沉") == {"rate": "-20%"}
    assert tts_engine._delivery_params("电子合成音") == {"pitch": "+5Hz"}
    p = tts_engine._delivery_params("又快又电子")
    assert p.get("rate") == "+20%"  # 快优先于慢
    assert p.get("pitch") == "+5Hz"


def test_synthesize_writes_file_with_resolved_voice():
    """synthesize：写文件 + 语义 ID 解析成音色 + delivery 参数透传 + emotion 不报错。"""
    backend = tts_engine.EdgeTtsBackend(voice_map={"voice_柳如烟": "zh-CN-XiaoxiaoNeural"})
    out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_synth_"), "line_001.wav")
    ret = asyncio.run(
        backend.synthesize(
            "完了，穿越了。", "voice_柳如烟", out, emotion="震惊", delivery="语速偏快"
        )
    )
    assert ret == out
    assert os.path.isfile(out), "输出文件未写入"
    last = _FakeCommunicate.LAST
    assert last["voice"] == "zh-CN-XiaoxiaoNeural"
    assert last["kwargs"].get("rate") == "+20%"
    assert last["text"] == "完了，穿越了。"


def test_synthesize_native_voice_and_no_delivery_params():
    backend = tts_engine.EdgeTtsBackend()
    out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_synth2_"), "sub", "line_002.wav")
    asyncio.run(backend.synthesize("旁白测试。", "zh-CN-YunyangNeural", out))
    last = _FakeCommunicate.LAST
    assert last["voice"] == "zh-CN-YunyangNeural"
    assert last["kwargs"].get("rate") is None  # 无 delivery → 无 prosody 参数
    assert last["kwargs"].get("output_format") == tts_engine._WAV_OUTPUT_FORMAT  # .wav → RIFF PCM


def test_synthesize_wav_requests_riff_pcm_output():
    """目标 .wav → 显式请求 RIFF PCM（云端默认是 MP3 字节流，mixer 读不了）。"""
    backend = tts_engine.EdgeTtsBackend()
    out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_wav_"), "line_006.wav")
    asyncio.run(backend.synthesize("测试。", "zh-CN-XiaoxiaoNeural", out))
    assert _FakeCommunicate.LAST["kwargs"].get("output_format") == tts_engine._WAV_OUTPUT_FORMAT


def test_synthesize_mp3_uses_default_output():
    """目标 .mp3 → 不传 output_format（云端默认 MP3）。"""
    backend = tts_engine.EdgeTtsBackend()
    out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_mp3_"), "line_007.mp3")
    asyncio.run(backend.synthesize("测试。", "zh-CN-XiaoxiaoNeural", out))
    assert "output_format" not in _FakeCommunicate.LAST["kwargs"]


def test_synthesize_legacy_edge_tts_skips_output_format():
    """旧版 edge-tts（Communicate 无 output_format 参数）→ 自动跳过容器格式参数，
    合成不中断（落盘为 MP3 假 .wav，由 mixer ffmpeg 时长兜底）。"""
    orig = _edge_tts.Communicate
    _edge_tts.Communicate = _LegacyCommunicate
    try:
        backend = tts_engine.EdgeTtsBackend()
        out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_legacy_"), "line_old.wav")
        ret = asyncio.run(backend.synthesize("测试。", "zh-CN-XiaoxiaoNeural", out))
    finally:
        _edge_tts.Communicate = orig
    assert ret == out
    assert os.path.isfile(out)
    assert "output_format" not in _FakeCommunicate.LAST["kwargs"], \
        "旧版引擎不应收到 output_format（否则 TypeError）"


def test_synthesize_success_no_unnecessary_retry():
    """音色接受 prosody（如 Xiaoxiao）→ 带参数一次成功，不额外重试。"""
    _FakeCommunicate.CALLS = 0
    backend = tts_engine.EdgeTtsBackend()
    out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_noretry_"), "line_004.wav")
    asyncio.run(backend.synthesize("测试。", "zh-CN-XiaoxiaoNeural", out, delivery="语速偏快"))
    assert _FakeCommunicate.CALLS == 1, "成功时不应有多余重试"
    assert _FakeCommunicate.LAST["kwargs"].get("rate") == "+20%"  # 参数原样透传
    assert _FakeCommunicate.LAST["kwargs"].get("output_format") == tts_engine._WAV_OUTPUT_FORMAT


def test_synthesize_degrades_when_voice_rejects_prosody():
    """音色拒绝 prosody（No audio was received）→ 引擎自动降级无 prosody 重试，管线不中断。

    对应实测根因：zh-CN-YunxiNeural 拒绝任何 rate/pitch（系统电子音 line_002 曾 500）。
    期望：带 pitch 的前两次尝试失败，最终以「仅 output_format」合成成功；LAST 记录最后一次
    （prosody 被剥离、容器格式保留）。
    """
    orig = _edge_tts.Communicate
    _edge_tts.Communicate = _RejectProsodyCommunicate
    try:
        backend = tts_engine.EdgeTtsBackend()
        out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_degrade_"), "line_sys.wav")
        ret = asyncio.run(
            backend.synthesize("系统提示：吐槽值 +100。", "voice_system_electronic", out, delivery="电子")
        )
    finally:
        _edge_tts.Communicate = orig
    assert ret == out
    assert os.path.isfile(out)
    last = _FakeCommunicate.LAST
    assert last["voice"] == "zh-CN-YunxiNeural"
    assert last["kwargs"] == {"output_format": tts_engine._WAV_OUTPUT_FORMAT}, \
        f"应以无 prosody 合成成功，实际 kwargs={last['kwargs']}"


def test_synthesize_raises_non_noaudio_errors():
    """非「No audio was received」错误 → 不重试直接抛（不掩盖真实错误）。"""
    orig = _edge_tts.Communicate
    _edge_tts.Communicate = _BoomCommunicate
    try:
        backend = tts_engine.EdgeTtsBackend()
        out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_boom_"), "line_005.wav")
        _FakeCommunicate.CALLS = 0
        try:
            asyncio.run(backend.synthesize("测试。", "zh-CN-XiaoxiaoNeural", out, delivery="语速偏快"))
            raise AssertionError("expected OSError")
        except OSError as exc:
            assert "disk full" in str(exc)
    finally:
        _edge_tts.Communicate = orig
    assert _FakeCommunicate.CALLS == 1, "非 NoAudioReceived 错误不应重试"


def test_preflight_ok_when_edge_tts_available():
    """edge_tts 桩在 sys.modules → preflight 不抛。"""
    backend = tts_engine.EdgeTtsBackend()
    backend.preflight()  # 不抛即通过


def test_preflight_missing_edge_tts_raises_with_install_cmd():
    """edge_tts 缺失 → RuntimeError 且带 pip install edge-tts 提示。"""
    orig = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = None  # None → import 抛 ImportError，模拟未安装
    try:
        backend = tts_engine.EdgeTtsBackend()
        try:
            backend.preflight()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            msg = str(exc)
            assert "pip install edge-tts" in msg
            assert "Edge-TTS" in msg  # 定位说明（管线测试引擎）
    finally:
        if orig is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = orig


def test_list_voices_static_zh_table():
    backend = tts_engine.EdgeTtsBackend()
    voices = backend.list_voices()
    assert len(voices) >= 5
    ids = {v.voice_id for v in voices}
    assert "zh-CN-XiaoxiaoNeural" in ids
    assert "zh-CN-YunyangNeural" in ids
    for v in voices:
        assert v.engine == "edge-tts"
        assert v.language == "zh-CN"
        assert v.voice_id.startswith("zh-CN-")


def test_create_default_tts_backend_returns_edge():
    backend = tts_engine.create_default_tts_backend()
    assert isinstance(backend, tts_engine.EdgeTtsBackend)
    assert backend.name == "edge-tts"


def test_sync_synthesize_wrapper():
    """_sync_synthesize：无事件循环线程里 asyncio.run 包装 async synthesize。"""
    backend = tts_engine.EdgeTtsBackend(voice_map={"voice_旁白": "zh-CN-YunyangNeural"})
    out = os.path.join(tempfile.mkdtemp(prefix="tts_engine_sync_"), "line_003.wav")
    ret = tts_engine._sync_synthesize(
        backend, "林晓感觉胸口那股郁结之气……", "voice_旁白", out, delivery="缓慢低沉"
    )
    assert ret == out
    assert os.path.isfile(out)
    last = _FakeCommunicate.LAST
    assert last["voice"] == "zh-CN-YunyangNeural"
    assert last["kwargs"].get("rate") == "-20%"


def test_no_llm_gpu_side_effects():
    """⛔ 纯规则零 LLM 零显存：源码不 import torch / ollama / requests。"""
    with open(_module_path(), "r", encoding="utf-8") as f:
        src = f.read()
    for bad in ("torch", "ollama", "requests"):
        assert not _has_import(src, bad), f"tts_engine.py 不应 import {bad}"


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
