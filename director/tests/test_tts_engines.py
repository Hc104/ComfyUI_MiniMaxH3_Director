#!/usr/bin/env python3
"""TTS 引擎后端单元测试（盲听测试三引擎：MiniMax Speech / GPT-SoVITS / CosyVoice 3）。

设计：
  - 全部依赖 transport 注入 mock HTTP，免网络（测试环境无 requests 也能跑）。
  - MiniMax：验证 payload 构造（voice_id 解析 / 快慢 speed / 电子 sound_effects）、
    hex 解码落盘、业务错误（base_resp）与缺 audio 错误。
  - GPT-SoVITS / CosyVoice3：验证 refs.json 解析（相对路径绝对化）、合成落盘、
    HTTP 错误 JSON 透传、缺 refs 的清晰报错。
  - 工厂：create_backend 路由与未知引擎报错。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_tts_engines.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.tts_engines import create_backend  # noqa: E402
from director.tts_engines.cosyvoice3 import CosyVoice3Backend  # noqa: E402
from director.tts_engines.gpt_sovits import GPTSoVITSBackend  # noqa: E402
from director.tts_engines.minimax_speech import (  # noqa: E402
    DEFAULT_STOCK_MAP,
    MINIMAX_T2A_URL,
    MiniMaxSpeechBackend,
)

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


def _fake_transport(status: int = 200, ctype: str = "application/json", body: bytes = b"{}", records: list | None = None):
    """构造假 transport：(url, payload, headers) -> (status, ctype, body)。records 记录调用。"""

    async def transport(url, payload, headers, **kw):  # noqa: ARG001
        if records is not None:
            records.append((url, payload, headers))
        return status, ctype, body

    return transport


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def test_factory() -> None:
    print("\n[1] create_backend 工厂")
    _check("minimax → MiniMaxSpeechBackend", isinstance(create_backend("minimax"), MiniMaxSpeechBackend))
    _check("gpt-sovits → GPTSoVITSBackend", isinstance(create_backend("gpt-sovits"), GPTSoVITSBackend))
    _check("cosyvoice3 → CosyVoice3Backend", isinstance(create_backend("cosyvoice3"), CosyVoice3Backend))
    try:
        create_backend("unknown-engine")
        _check("未知引擎抛 ValueError", False)
    except ValueError:
        _check("未知引擎抛 ValueError", True)


# ---------------------------------------------------------------------------
# MiniMax Speech
# ---------------------------------------------------------------------------
def test_minimax_resolve_voice() -> None:
    print("\n[2] MiniMax resolve_voice")
    b = MiniMaxSpeechBackend(api_key="k", transport=_fake_transport())
    _check("voice_linweiwei → female-shaonv", b.resolve_voice("voice_linweiwei") == "female-shaonv")
    _check("voice_guyanchen → male-qn-badao", b.resolve_voice("voice_guyanchen") == "male-qn-badao")
    _check("voice_narrator → qiaopi_mengmei", b.resolve_voice("voice_narrator") == "qiaopi_mengmei")
    _check("voice_system → Robot_Armor", b.resolve_voice("voice_system") == "Robot_Armor")
    _check("自定义 voice_id 透传", b.resolve_voice("clone_linweiwei") == "clone_linweiwei")
    _check("空 voice_id → 旁白音色", b.resolve_voice("") == DEFAULT_STOCK_MAP["voice_narrator"])


def test_minimax_preflight() -> None:
    print("\n[3] MiniMax preflight")
    b = MiniMaxSpeechBackend(api_key="", transport=_fake_transport())
    try:
        b.preflight()
        _check("缺 key 抛 RuntimeError", False)
    except RuntimeError as exc:
        _check("缺 key 抛 RuntimeError（带 MINIMAX_API_KEY 提示）", "MINIMAX_API_KEY" in str(exc))
    b2 = MiniMaxSpeechBackend(api_key="k", transport=_fake_transport())
    b2.preflight()
    _check("有 key 通过", True)


def test_minimax_synthesize_ok() -> None:
    print("\n[4] MiniMax synthesize 成功（hex 解码落盘）")
    audio_hex = "504b0304140000000800"  # 任意 hex 串（模拟 data.audio）
    body = json.dumps({"base_resp": {"status_code": 0, "status_msg": "ok"}, "data": {"audio": audio_hex, "status": 2}}).encode()
    records = []
    b = MiniMaxSpeechBackend(api_key="test-key", transport=_fake_transport(body=body, records=records))
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.wav")
        ret = _run(b.synthesize("你好", "voice_linweiwei", out, emotion="", delivery=""))
        _check("返回落盘路径", ret == out and os.path.exists(out))
        with open(out, "rb") as f:
            _check("内容 = bytes.fromhex(audio_hex)", f.read() == bytes.fromhex(audio_hex))
        _check("请求打到 t2a_v2", records and records[0][0] == MINIMAX_T2A_URL)
        payload = records[0][1]
        _check("Authorization Bearer", records[0][2]["Authorization"] == "Bearer test-key")
        _check("model 默认 speech-2.8-hd", payload["model"] == "speech-2.8-hd")
        _check("stream=false", payload["stream"] is False)
        _check("voice_setting.voice_id=female-shaonv", payload["voice_setting"]["voice_id"] == "female-shaonv")
        _check("audio_setting wav", payload["audio_setting"]["format"] == "wav")


def test_minimax_synthesize_delivery() -> None:
    print("\n[5] MiniMax delivery 映射（快/慢/电子）")
    records = []
    b = MiniMaxSpeechBackend(api_key="test-key", transport=_fake_transport(
        body=json.dumps({"base_resp": {"status_code": 0}, "data": {"audio": "00"}}).encode(), records=records))
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "o.wav")
        _run(b.synthesize("快一点", "voice_narrator", out, delivery="快"))
        _check("快 → speed 1.25", records[0][1]["voice_setting"]["speed"] == 1.25)
        records.clear()
        _run(b.synthesize("慢一点", "voice_narrator", out, delivery="慢"))
        _check("慢 → speed 0.85", records[0][1]["voice_setting"]["speed"] == 0.85)
        records.clear()
        _run(b.synthesize("电子", "voice_system", out, delivery="电子"))
        _check("电子 → voice_modify robotic", records[0][1].get("voice_modify", {}).get("sound_effects") == "robotic")


def test_minimax_synthesize_errors() -> None:
    print("\n[6] MiniMax 业务错误")
    b = MiniMaxSpeechBackend(api_key="test-key", transport=_fake_transport(
        body=json.dumps({"base_resp": {"status_code": 1004, "status_msg": "auth failed"}}).encode()))
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _run(b.synthesize("x", "voice_narrator", os.path.join(tmp, "o.wav")))
            _check("base_resp≠0 抛 RuntimeError", False)
        except RuntimeError as exc:
            _check("base_resp≠0 抛 RuntimeError（含 1004）", "1004" in str(exc))
    b2 = MiniMaxSpeechBackend(api_key="test-key", transport=_fake_transport(
        body=json.dumps({"base_resp": {"status_code": 0}, "data": {}}).encode()))
    try:
        _run(b2.synthesize("x", "voice_narrator", os.path.join(tempfile.mkdtemp(), "o.wav")))
        _check("缺 data.audio 抛 RuntimeError", False)
    except RuntimeError as exc:
        _check("缺 data.audio 抛 RuntimeError", "audio" in str(exc))
    b3 = MiniMaxSpeechBackend(api_key="test-key", transport=_fake_transport(status=401, body=b"unauthorized"))
    try:
        _run(b3.synthesize("x", "voice_narrator", os.path.join(tempfile.mkdtemp(), "o.wav")))
        _check("HTTP 401 抛 RuntimeError", False)
    except RuntimeError as exc:
        _check("HTTP 401 抛 RuntimeError", "401" in str(exc))


# ---------------------------------------------------------------------------
# 本地引擎共享 fixture：临时 refs.json + 假 wav
# ---------------------------------------------------------------------------
def _make_refs(tmp: str, roles: tuple[str, ...] = ("linweiwei", "guyanchen", "narrator", "system")) -> str:
    refs = {}
    for role in roles:
        wav = os.path.join(tmp, f"{role}.wav")
        with open(wav, "wb") as f:
            f.write(b"RIFF\x00\x00\x00\x00WAVE")
        refs[role] = {"wav": f"{role}.wav", "prompt_text": f"{role} 的参考文本", "prompt_language": "zh"}
    refs_file = os.path.join(tmp, "refs.json")
    with open(refs_file, "w", encoding="utf-8") as f:
        json.dump(refs, f, ensure_ascii=False, indent=2)
    return refs_file


# ---------------------------------------------------------------------------
# GPT-SoVITS
# ---------------------------------------------------------------------------
def test_gpt_sovits_synthesize() -> None:
    print("\n[7] GPT-SoVITS synthesize 成功")
    with tempfile.TemporaryDirectory() as tmp:
        refs_file = _make_refs(tmp)
        records = []
        b = GPTSoVITSBackend(refs_file=refs_file, transport=_fake_transport(status=200, ctype="audio/wav", body=b"RIFFWAVEDATA", records=records))
        out = os.path.join(tmp, "out.wav")
        ret = _run(b.synthesize("你好", "voice_guyanchen", out))
        _check("返回路径 + 落盘", ret == out and os.path.exists(out))
        with open(out, "rb") as f:
            _check("音频字节透传", f.read() == b"RIFFWAVEDATA")
        _check("POST 到 base_url/", records[0][0].endswith("/"))
        payload = records[0][1]
        _check("refer_wav_path 绝对化", os.path.isabs(payload["refer_wav_path"]))
        _check("refer_wav_path = guyanchen.wav", payload["refer_wav_path"].endswith("guyanchen.wav"))
        _check("prompt_text 读 refs", payload["prompt_text"] == "guyanchen 的参考文本")
        _check("text_language=zh", payload["text_language"] == "zh")
        _check("推理参数直传", payload["top_k"] == 15 and payload["speed"] == 1.0)


def test_gpt_sovits_errors() -> None:
    print("\n[8] GPT-SoVITS 错误处理")
    with tempfile.TemporaryDirectory() as tmp:
        refs_file = _make_refs(tmp)
        b = GPTSoVITSBackend(refs_file=refs_file, transport=_fake_transport(status=400, ctype="application/json", body=b'{"message":"no such refer"}'))
        try:
            _run(b.synthesize("x", "voice_narrator", os.path.join(tmp, "o.wav")))
            _check("HTTP 400 → RuntimeError 含 message", False)
        except RuntimeError as exc:
            _check("HTTP 400 → RuntimeError 含 message", "no such refer" in str(exc))
        try:
            b.resolve_ref("voice_ghost")
            _check("未知角色报错", False)
        except RuntimeError as exc:
            _check("未知角色报错（含可用角色）", "linweiwei" in str(exc))
    b2 = GPTSoVITSBackend(refs_file=os.path.join(tempfile.mkdtemp(), "nope.json"))
    try:
        b2.preflight()
        _check("缺 refs.json 报错", False)
    except RuntimeError as exc:
        _check("缺 refs.json 报错（含 prepare-refs 提示）", "prepare-refs" in str(exc))


# ---------------------------------------------------------------------------
# CosyVoice 3
# ---------------------------------------------------------------------------
def test_cosyvoice3_synthesize() -> None:
    print("\n[9] CosyVoice 3 synthesize 成功")
    with tempfile.TemporaryDirectory() as tmp:
        refs_file = _make_refs(tmp)
        records = []
        b = CosyVoice3Backend(refs_file=refs_file, transport=_fake_transport(status=200, ctype="audio/wav", body=b"RIFFCV3", records=records))
        out = os.path.join(tmp, "out.wav")
        ret = _run(b.synthesize("你好", "voice_linweiwei", out))
        _check("返回路径 + 落盘", ret == out and os.path.exists(out))
        _check("POST 到 /tts", records[0][0].endswith("/tts"))
        payload = records[0][1]
        _check("prompt_wav 绝对化", os.path.isabs(payload["prompt_wav"]))
        _check("prompt_wav = linweiwei.wav", payload["prompt_wav"].endswith("linweiwei.wav"))
        _check("prompt_text 读 refs", payload["prompt_text"] == "linweiwei 的参考文本")
        _check("text 直传", payload["text"] == "你好")


def test_cosyvoice3_errors() -> None:
    print("\n[10] CosyVoice 3 错误处理")
    with tempfile.TemporaryDirectory() as tmp:
        refs_file = _make_refs(tmp)
        b = CosyVoice3Backend(refs_file=refs_file, transport=_fake_transport(status=500, ctype="application/json", body=b'{"message":"cuda oom"}'))
        try:
            _run(b.synthesize("x", "voice_narrator", os.path.join(tmp, "o.wav")))
            _check("HTTP 500 → RuntimeError 含 message", False)
        except RuntimeError as exc:
            _check("HTTP 500 → RuntimeError 含 message", "cuda oom" in str(exc))


def test_local_backend_list_voices() -> None:
    print("\n[11] 本地引擎 list_voices（读 refs 角色）")
    with tempfile.TemporaryDirectory() as tmp:
        refs_file = _make_refs(tmp)
        b = GPTSoVITSBackend(refs_file=refs_file, transport=_fake_transport(status=200, ctype="audio/wav", body=b"x"))
        vids = [v.voice_id for v in b.list_voices()]
        _check("voice_linweiwei 在列", "voice_linweiwei" in vids and "voice_system" in vids)
        b2 = CosyVoice3Backend(refs_file=refs_file, transport=_fake_transport(status=200, ctype="audio/wav", body=b"x"))
        _check("CosyVoice list_voices 同源", len(b2.list_voices()) == 4)


def main() -> int:
    print("=" * 64)
    print("TTS 引擎后端单元测试（MiniMax / GPT-SoVITS / CosyVoice 3）")
    print("=" * 64)
    test_factory()
    test_minimax_resolve_voice()
    test_minimax_preflight()
    test_minimax_synthesize_ok()
    test_minimax_synthesize_delivery()
    test_minimax_synthesize_errors()
    test_gpt_sovits_synthesize()
    test_gpt_sovits_errors()
    test_cosyvoice3_synthesize()
    test_cosyvoice3_errors()
    test_local_backend_list_voices()
    print("\n" + "=" * 64)
    print(f"结果: {PASSED} PASS / {FAILED} FAIL")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
