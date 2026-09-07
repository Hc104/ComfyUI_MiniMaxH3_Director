#!/usr/bin/env python3
"""TTS 引擎抽象层（Phase 0：Edge-TTS = 管线测试引擎，不是最终配音方案）。

用户 2026-08-16 拍板（TTS_VOICE_CAST_PLAN.md §4/§5/§6）：
- Edge-TTS 只是「管线测试引擎」：证明 Dialogue → TTS → WAV/MP3 → 时间轴 → FFmpeg → 成片
  整条链路成立即可，不做深度优化。
- 正式生产候选 = MiniMax Speech（Phase 1/2）；本地克隆增强 = CosyVoice 3 / GPT-SoVITS
  （Phase 3）。用户核心指标 = 音色稳定 + 情绪表现 + 中文自然度 + 批量生成 + API 自动化。
- 本模块提供统一 ``TtsEngine`` 接口，后续各后端可插拔，业务层不感知具体引擎。

结构：
    TtsEngine（抽象基类）
        ├── EdgeTtsBackend（Phase 0 实现：微软 Edge TTS，云端 API 无显存压力）
        ├── MiniMaxSpeechBackend（Phase 1/2 实现，正式生产候选，待接）
        ├── CosyVoice3Backend（Phase 3 实现：本地 RTX 5080，需过显存安全门）
        └── GPTSoVITSBackend（Phase 3 实现：音色克隆，待接）

用法：
    backend = create_default_tts_backend()          # Phase 0 默认 = EdgeTtsBackend
    backend.preflight()                             # 检查 edge-tts 依赖（缺则明确报错+安装命令）
    await backend.synthesize(text, voice_id, out_path, emotion=..., delivery=...)

⛔ 本模块不 import 深度学习 / LLM / HTTP 请求库；Edge-TTS 是云端 API 与 H3 生成不冲突
（本地 TTS 如 CosyVoice 3 才需要过显存安全门，Phase 3 另行接线）。
"""

from __future__ import annotations

import asyncio
import logging
import os

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.tts_engine")


# ---------------------------------------------------------------------------
# VoiceInfo：对外音色描述（list_voices 数据源，前端 Voice Cast 面板用）
# ---------------------------------------------------------------------------
@dataclass
class VoiceInfo:
    """一个可选音色的描述。voice_id 既可以是我们的语义 ID（voice_narrator），
    也可以是引擎原生音色名（zh-CN-XiaoxiaoNeural），面板显示 name 让用户选。"""

    voice_id: str = ""
    name: str = ""
    language: str = "zh-CN"
    gender: str = ""
    engine: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "name": self.name,
            "language": self.language,
            "gender": self.gender,
            "engine": self.engine,
        }


# ---------------------------------------------------------------------------
# TtsEngine 抽象基类
# ---------------------------------------------------------------------------
class TtsEngine:
    """TTS 后端抽象。所有配音业务只依赖此接口（与 text_backends.TextBackend 同款风格）。

    ``synthesize`` 是 async：Edge-TTS 走 async API；路由层（aiohttp）直接 await；
    命令行/测试用 ``asyncio.run()`` 包装（见 tts_worker）。
    """

    name: str = "abstract"

    def preflight(self) -> None:
        """进入使用前的可选检查（依赖 / 连通性 / key）。缺依赖时 raise RuntimeError，
        错误信息带完整安装命令。默认无操作。"""
        return None

    def list_voices(self) -> List[VoiceInfo]:
        """可选音色列表（前端 Voice Cast 面板数据源）。默认空。"""
        return []

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: os.PathLike[str] | str,
        *,
        emotion: str = "",
        delivery: str = "",
    ) -> str:
        """把一行台词合成到 ``output_path``，返回实际写出的文件路径字符串。

        - text:      台词原文（不含说话人标签，worker 层已剥离）
        - voice_id:  语义 voice_id（voice_narrator / voice_柳如烟）或引擎原生音色名
        - emotion:   情绪词（各引擎映射能力不同，Edge-TTS 无情感模型，Phase 0 仅记录）
        - delivery:  语气/语速（「快/慢/电子…」可映射到引擎参数）
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Edge-TTS 后端（Phase 0 管线测试引擎）
# ---------------------------------------------------------------------------
# voice_id → Edge-TTS 中文音色名 默认映射（Phase 0 测试用，正式配音由 MiniMax Speech 承担）。
_DEFAULT_EDGE_MAP: Dict[str, str] = {
    "voice_narrator": "zh-CN-YunyangNeural",            # 云扬：新闻/旁白（沉稳男声）
    "voice_system": "zh-CN-YunxiNeural",                # 云希：系统音（青春男声）
    "voice_system_electronic": "zh-CN-YunxiNeural",     # 电子味变体（delivery 映射 pitch 调整）
}

# 常用中文音色静态表（list_voices 数据源；Phase 0 静态表避免网络依赖，够面板选音色）。
_EDGE_ZH_VOICES: List[VoiceInfo] = [
    VoiceInfo("zh-CN-XiaoxiaoNeural", "晓晓（女·自然·推荐）", "zh-CN", "Female", "edge-tts"),
    VoiceInfo("zh-CN-XiaoyiNeural", "晓伊（女·活泼）", "zh-CN", "Female", "edge-tts"),
    VoiceInfo("zh-CN-YunxiNeural", "云希（男·少年/电子）", "zh-CN", "Male", "edge-tts"),
    VoiceInfo("zh-CN-YunjianNeural", "云健（男·播音）", "zh-CN", "Male", "edge-tts"),
    VoiceInfo("zh-CN-YunyangNeural", "云扬（男·新闻/旁白）", "zh-CN", "Male", "edge-tts"),
    VoiceInfo("zh-CN-YunxiaNeural", "云夏（男·少年）", "zh-CN", "Male", "edge-tts"),
    VoiceInfo("zh-CN-YunfengNeural", "云枫（男·成熟）", "zh-CN", "Male", "edge-tts"),
]

# Edge-TTS 云端默认输出是 MP3 字节流（audio-24khz-48kbitrate-mono-mp3），即使文件名是 .wav
# 内容仍是 MP3——mixer.wav_duration 纯 Python 读标准 WAV header 会报「不是标准 WAV 文件」。
# 目标文件为 .wav 时显式请求 RIFF PCM（24kHz/16bit/mono），让落盘是真 WAV（零 ffmpeg 读时长成立）。
_WAV_OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"


def _delivery_params(delivery: str) -> Dict[str, str]:
    """从 delivery 文本推断 Edge-TTS 参数（rate / pitch）。

    规则（Phase 0 够用即可，不追求精细化）：
      - 含「快」→ rate +20%；含「慢」→ rate -20%
      - 含「电子」→ pitch +5Hz（电子机械感）
    """
    params: Dict[str, str] = {}
    d = delivery or ""
    if "快" in d:
        params["rate"] = "+20%"
    elif "慢" in d:
        params["rate"] = "-20%"
    if "电子" in d:
        params["pitch"] = "+5Hz"
    return params


class EdgeTtsBackend(TtsEngine):
    """微软 Edge TTS 后端（云端 API，免费，中文音色多，无显存压力）。

    - ``synthesize`` 内部调 ``edge_tts.Communicate(text, voice, **params).save(path)``。
    - ``voice_id`` 解析：已是 ``zh-CN-*`` 原生名 → 直接透传；否则查 ``voice_map``
      （voice_narrator / voice_system … → 具体音色名）；未命中 → ``default_voice``。
    - Phase 0 定位：管线测试引擎，验证链路成立即可，不做深度优化。
    """

    name = "edge-tts"

    def __init__(
        self,
        *,
        voice_map: Optional[Dict[str, str]] = None,
        default_voice: str = "zh-CN-XiaoxiaoNeural",
    ):
        self.voice_map = dict(_DEFAULT_EDGE_MAP)
        if voice_map:
            self.voice_map.update(voice_map)
        self.default_voice = default_voice

    # -- 工具 ----------------------------------------------------------------
    def _require_edge_tts(self) -> Any:
        """确认 edge_tts 库可用；缺失时给出清晰安装命令。"""
        try:
            import edge_tts  # noqa: PLC0415 - 延迟导入，缺失时才报错

            return edge_tts
        except ImportError as exc:  # pragma: no cover - 依赖缺失的清晰提示
            raise RuntimeError(
                "未安装 edge-tts。请在本机 ComfyUI 的 .venv 环境执行：\n"
                '  "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" -m pip install edge-tts\n'
                "（#3551 铁律：后端依赖必须装 .venv，不要装进 standalone-env）\n"
                "Edge-TTS 是 Phase 0 管线测试引擎（微软免费中文 TTS）；"
                "正式生产配音将切换 MiniMax Speech（Phase 1/2）。"
            ) from exc

    def resolve_voice(self, voice_id: str) -> str:
        """把语义 voice_id 解析成 Edge-TTS 音色名。"""
        vid = (voice_id or "").strip()
        if not vid:
            return self.default_voice
        if vid.startswith("zh-CN-"):
            return vid
        return self.voice_map.get(vid, self.default_voice)

    # -- TtsEngine 接口 ------------------------------------------------------
    def preflight(self) -> None:
        self._require_edge_tts()
        log.info("Edge-TTS 可用（管线测试引擎，正式配音将切换 MiniMax Speech）")

    def list_voices(self) -> List[VoiceInfo]:
        return list(_EDGE_ZH_VOICES)

    @staticmethod
    def _supports_output_format(edge_tts: Any) -> bool:
        """edge-tts 版本探测：新版（7.x+）Communicate 接受 output_format 参数。

        旧版（6.x-）签名不含该参数（也没有 ``**kwargs`` 兜底），传了会抛
        TypeError「got an unexpected keyword argument」。显式参数名或 ``**kwargs``
        任一命中即视为支持；探测失败（无签名）保守视为不支持。
        """
        try:
            import inspect  # noqa: PLC0415 - 轻量标准库，按需引入

            params = inspect.signature(edge_tts.Communicate.__init__).parameters
            if "output_format" in params:
                return True
            return any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):  # pragma: no cover - 无签名环境防御
            return False

    @staticmethod
    async def _save_once(edge_tts: Any, text: str, voice: str, out: str, params: Dict[str, str]) -> None:
        """调一次 Edge-TTS 合成到文件（可重入，供降级重试用）。

        目标扩展名为 .wav 时显式请求 RIFF PCM 输出（默认是 MP3 字节流，非标准 WAV），
        保证 mixer.wav_duration 纯 Python 读 WAV header 成立；.mp3 等其他扩展不传
        output_format（用云端默认 MP3）。旧版 edge-tts 不支持 output_format 时自动
        跳过该参数（落盘为 MP3 内容假 .wav，由 mixer 的 ffmpeg 时长兜底消化）。
        """
        kwargs: Dict[str, str] = dict(params)
        if out.lower().endswith(".wav") and EdgeTtsBackend._supports_output_format(edge_tts):
            kwargs["output_format"] = _WAV_OUTPUT_FORMAT
        comm = edge_tts.Communicate(text, voice, **kwargs)
        await comm.save(out)

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: os.PathLike[str] | str,
        *,
        emotion: str = "",
        delivery: str = "",
    ) -> str:
        edge_tts = self._require_edge_tts()
        voice = self.resolve_voice(voice_id)
        params = _delivery_params(delivery)
        out = str(output_path)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if emotion:
            # Edge-TTS 无情感模型，Phase 0 仅记录（正式引擎在 Phase 1/2 映射情感）。
            log.info("Edge-TTS synthesize(voice=%s, emotion=%s, delivery=%s)", voice, emotion, delivery)
        # 部分 Edge-TTS 音色（实测 zh-CN-YunxiNeural）拒绝 <prosody>（rate/pitch）——
        # 云端返回「No audio was received」。策略：带参数失败先同参数重试一次（瞬时限流），
        # 仍失败则降级为无参数再试一次（保证管线不中断；电子味由正式引擎/后期处理承担）。
        # 只有「No audio was received」类错误才降级；其他错误（缺文件/网络中断）直接抛，
        # 不掩盖真实错误。
        attempts: List[Dict[str, str]] = [params]
        if params:
            attempts += [params, {}]
        else:
            attempts += [{}]
        last_exc: Optional[Exception] = None
        for attempt_params in attempts:
            try:
                await self._save_once(edge_tts, text, voice, out, attempt_params)
                if attempt_params != params:
                    log.warning(
                        "Edge-TTS voice=%s 参数 %s 失败后以 %s 合成成功（该音色可能拒绝 prosody）",
                        voice, params, attempt_params,
                    )
                return out
            except Exception as exc:  # noqa: BLE001 - 需统一捕获以判断是否降级
                last_exc = exc
                if "No audio was received" not in str(exc):
                    raise  # 非云端拒绝/限流类错误 → 直接抛出，不掩盖真实错误
        assert last_exc is not None  # attempts 非空，必至少尝试一次
        raise last_exc


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def create_default_tts_backend(
    *,
    voice_map: Optional[Dict[str, str]] = None,
    default_voice: str = "zh-CN-XiaoxiaoNeural",
    engine: str = "edge-tts",
    **kwargs: Any,
) -> TtsEngine:
    """默认后端工厂。engine 可选：

    - ``edge-tts``（默认）：Phase 0 管线测试引擎，不做深度优化。
    - ``minimax`` / ``gpt-sovits`` / ``cosyvoice3``：盲听测试/生产候选引擎
      （实现于 director/tts_engines/，懒加载避免导入 requests 等重依赖）。
      额外参数（api_key / base_url / refs_file …）透传给对应后端。

    切换方式：TTS_BACKEND 环境变量或调用方配置选 engine；业务层只认
    ``TtsEngine`` 接口与 voice_id，不感知具体引擎。
    """
    key = (engine or "edge-tts").strip().lower()
    if key in ("edge-tts", "edge", "edgetts"):
        return EdgeTtsBackend(voice_map=voice_map, default_voice=default_voice)
    # 懒加载 tts_engines 包：避免默认路径 import requests（ComfyUI 后端主进程轻启动）。
    from .tts_engines import create_backend  # noqa: PLC0415

    return create_backend(key, **kwargs)


def _sync_synthesize(
    backend: TtsEngine,
    text: str,
    voice_id: str,
    output_path: os.PathLike[str] | str,
    *,
    emotion: str = "",
    delivery: str = "",
) -> str:
    """sync 包装：在无事件循环的线程/脚本里用 asyncio.run 调 async synthesize。

    在已有事件循环（aiohttp 路由）里请直接 ``await backend.synthesize(...)``，
    不要调用本函数（asyncio.run 不能在运行中的循环里调用）。
    """
    return asyncio.run(
        backend.synthesize(
            text, voice_id, output_path, emotion=emotion, delivery=delivery
        )
    )
