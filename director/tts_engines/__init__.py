"""TTS 引擎后端包（盲听测试 + Phase 3 候选引擎）。

统一 ``TtsEngine`` 抽象（见 director/tts_engine.py）的三套可插拔后端：

  - ``MiniMaxSpeechBackend``  : MiniMax 云端 T2A v2（不占本地 5080；需 MINIMAX_API_KEY）
  - ``GPTSoVITSBackend``      : 本地 GPT-SoVITS V4（zero-shot 音色克隆，需先部署 + 起 api.py）
  - ``CosyVoice3Backend``     : 本地 CosyVoice 3（zero-shot 音色克隆，经 tools/cosyvoice_server.py 走 HTTP）

设计原则（TTS_VOICE_CAST_PLAN.md §4/§5/§6，2026-08-16 用户拍板）：
  - 全部继承 ``director.tts_engine.TtsEngine``，业务层（tts_worker / 路由 / 前端 Voice Cast）
    只认 voice_id 字符串 → 换引擎 = 换一个 backend，全链路复用（AudioIntent + mixer.py 不动）。
  - 本地引擎全部通过 HTTP 调用（GPT-SoVITS 官方 api.py / CosyVoice 自托管
    tools/cosyvoice_server.py），重模型进程与 ComfyUI 主进程隔离，可独立启停以过显存安全门
    （5080 上 H3 生成与本地 TTS 互斥；云端 MiniMax 无此约束）。
  - 本包顶层不得 import requests / torch / transformers；缺依赖在 ``preflight()`` 才报错，
    错误信息带完整安装命令。

用法：
    from director.tts_engines import create_backend
    backend = create_backend("minimax")            # 云端，需 MINIMAX_API_KEY
    backend = create_backend("gpt-sovits", base_url="http://127.0.0.1:9880")
    backend = create_backend("cosyvoice3", base_url="http://127.0.0.1:8898")
    backend.preflight()
    await backend.synthesize(text, "voice_林薇薇", out_wav, emotion="", delivery="")
"""

from __future__ import annotations

from typing import Any, Dict, Type

from .cosyvoice3 import CosyVoice3Backend
from .gpt_sovits import GPTSoVITSBackend
from .minimax_speech import MiniMaxSpeechBackend

__all__ = [
    "MiniMaxSpeechBackend",
    "GPTSoVITSBackend",
    "CosyVoice3Backend",
    "create_backend",
]

_ENGINES: Dict[str, Type] = {
    "minimax": MiniMaxSpeechBackend,
    "minimax_speech": MiniMaxSpeechBackend,
    "gpt-sovits": GPTSoVITSBackend,
    "gpt_sovits": GPTSoVITSBackend,
    "cosyvoice3": CosyVoice3Backend,
    "cosyvoice": CosyVoice3Backend,
}


def create_backend(engine: str = "minimax", **kwargs: Any):
    """按名称构造 TTS 后端。

    engine 取值：``minimax``（云端）/ ``gpt-sovits`` / ``cosyvoice3``（本地，HTTP 调）。
    其余 kwargs 透传给对应后端构造函数（base_url / model / api_key / refs_file …）。
    """
    key = (engine or "").strip().lower()
    cls = _ENGINES.get(key)
    if cls is None:
        raise ValueError(
            f"未知 TTS 引擎：{engine!r}。可选：minimax / gpt-sovits / cosyvoice3"
        )
    return cls(**kwargs)
