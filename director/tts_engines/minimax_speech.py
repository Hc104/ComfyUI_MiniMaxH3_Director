#!/usr/bin/env python3
"""MiniMax Speech 后端（云端 T2A v2，Phase 1/2 正式生产候选引擎）。

接口事实（2026-08-16 官方文档核实）：
  - 同步合成：POST https://api.minimaxi.com/v1/t2a_v2
    鉴权：Authorization: Bearer {MINIMAX_API_KEY}（统一 JWT，无需 GroupId 头）
    请求：{model, text, stream:false,
           voice_setting:{voice_id, speed, vol, pitch, emotion},
           audio_setting:{sample_rate, format, channel}}
    响应：{data:{audio:<hex 编码>,status:2}, base_resp:{status_code,status_msg}}
    注意：data.audio 是 **hex**（非 base64），需 bytes.fromhex 还原。
  - 音色快速复刻（可选，需实名认证）：上传 ≥10s 音频（purpose=voice_clone，
    可选 <8s prompt audio）→ POST /v1/voice_clone → 拿自定义 voice_id
    （克隆音色 7 天未调用自动删除；Turbo 套餐赠 10 个快速克隆音色）。

特性：
  - 云端 API，不占本地 5080（与 H3 生成无显存冲突）。
  - 盲听测试默认用系统音色逐角色配声（stock voice，直接测成品级音质）；
    voice_map 可整体换成快速复刻的克隆 voice_id（见 clone_voice_sync）。
  - emotion → voice_setting.emotion；delivery「快/慢」→ speed，「电子」→
    voice_modify.sound_effects=robotic。
  - transport 注入：单测可注入假 HTTP 实现，免网络（测试环境无 requests 也能跑）。
  - ⛔ 本模块顶层不 import requests（preflight 延迟导入）。

资费参考（2026-08-16 官方）：Turbo 套餐 ¥360~¥400（200 万字符/月）、HD 套餐 ¥630~¥700。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from typing import Any, Dict, List, Optional, Tuple

from ..tts_engine import TtsEngine, VoiceInfo

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.tts_engines.minimax")

MINIMAX_T2A_URL = "https://api.minimaxi.com/v1/t2a_v2"
MINIMAX_UPLOAD_URL = "https://api.minimaxi.com/v1/files/upload"
MINIMAX_CLONE_URL = "https://api.minimaxi.com/v1/voice_clone"

# 默认模型（2026-08-16 官方文档枚举：speech-2.8-hd / 2.8-turbo / 2.6-hd / 2.6-turbo /
# 02-hd / 02-turbo / 01-hd / 01-turbo）。2.8-hd 最新，支持语气词标签 (laughs)/(sighs) 等。
DEFAULT_MODEL = "speech-2.8-hd"

# 盲听测试四角色 → 官方系统音色（语音包列表，2026-08-16 核实）：
#   林薇薇→少女音色 / 顾琰宸→霸道青年音色 / 旁白(林晓)→俏皮萌妹(吐槽感) / 系统→机械战甲
DEFAULT_STOCK_MAP: Dict[str, str] = {
    "voice_linweiwei": "female-shaonv",
    "voice_guyanchen": "male-qn-badao",
    "voice_narrator": "qiaopi_mengmei",
    "voice_system": "Robot_Armor",
}

# MiniMax 情绪枚举（voice_setting.emotion 合法值，来源官方 T2A 文档）
_EMOTION_SET = {
    "happy", "sad", "angry", "fearful", "disgusted",
    "surprised", "calm", "fluent", "whisper",
}

# 中文系统音色静态表（list_voices 数据源；盲听/配音面板选音色用，subset 够用即可）
_MINIMAX_ZH_VOICES: List[VoiceInfo] = [
    VoiceInfo("female-shaonv", "少女音色", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("female-yujie", "御姐音色", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("female-chengshu", "成熟女性音色", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("female-tianmei", "甜美女性音色", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("qiaopi_mengmei", "俏皮萌妹", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("wumei_yujie", "妩媚御姐", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("male-qn-badao", "霸道青年音色", "zh-CN", "Male", "minimax-speech"),
    VoiceInfo("male-qn-qingse", "青涩青年音色", "zh-CN", "Male", "minimax-speech"),
    VoiceInfo("male-qn-jingying", "精英青年音色", "zh-CN", "Male", "minimax-speech"),
    VoiceInfo("lengdan_xiongzhang", "冷淡学长", "zh-CN", "Male", "minimax-speech"),
    VoiceInfo("junlang_nanyou", "俊朗男友", "zh-CN", "Male", "minimax-speech"),
    VoiceInfo("Robot_Armor", "机械战甲（电子系统音）", "zh-CN", "Male", "minimax-speech"),
    VoiceInfo("Chinese (Mandarin)_News_Anchor", "新闻女声", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("Chinese (Mandarin)_Male_Announcer", "播报男声", "zh-CN", "Male", "minimax-speech"),
    VoiceInfo("Chinese (Mandarin)_Sweet_Lady", "甜美女声", "zh-CN", "Female", "minimax-speech"),
    VoiceInfo("Chinese (Mandarin)_Radio_Host", "电台男主播", "zh-CN", "Male", "minimax-speech"),
]

# transport 签名：(url, payload:dict, headers:dict, *, timeout:float) -> (status:int, content_type:str, body:bytes)
Transport = Any


async def _default_transport(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    *,
    timeout: float = 60.0,
) -> Tuple[int, str, bytes]:
    import requests  # noqa: PLC0415 - 延迟导入，缺依赖 preflight 已报错

    resp = await asyncio.to_thread(
        requests.post, url, json=payload, headers=headers, timeout=timeout
    )
    return resp.status_code, resp.headers.get("Content-Type", ""), resp.content


class MiniMaxSpeechBackend(TtsEngine):
    """MiniMax 云端 TTS 后端（T2A v2）。

    - ``api_key``：缺省读环境变量 ``MINIMAX_API_KEY``（与 H3 走同一平台，用户大概率已有）。
    - ``voice_map``：语义 voice_id → MiniMax 系统音色（默认见 DEFAULT_STOCK_MAP）。
    - ``model``：默认 speech-2.8-hd，可 --minimax-model 覆盖。
    - ``transport``：注入 async HTTP 实现（单测 mock 免网络）。
    """

    name = "minimax-speech"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        voice_map: Optional[Dict[str, str]] = None,
        sample_rate: int = 24000,
        transport: Transport = None,
    ):
        self.api_key = (api_key or os.environ.get("MINIMAX_API_KEY", "")).strip()
        self.model = model
        self.voice_map = dict(DEFAULT_STOCK_MAP)
        if voice_map:
            self.voice_map.update(voice_map)
        self.sample_rate = sample_rate
        self._transport = transport or _default_transport

    # -- 工具 ----------------------------------------------------------------
    def _require_requests(self) -> Any:
        import requests  # noqa: PLC0415 - 延迟导入

        return requests

    def resolve_voice(self, voice_id: str) -> str:
        """把语义 voice_id 解析成 MiniMax 系统音色名（或自定义克隆 voice_id）。"""
        vid = (voice_id or "").strip()
        if not vid:
            return self.voice_map.get("voice_narrator", "female-shaonv")
        return self.voice_map.get(vid, vid)

    # -- TtsEngine 接口 ------------------------------------------------------
    def preflight(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "未配置 MiniMax API Key。请设置环境变量 MINIMAX_API_KEY 后重试。\n"
                '  set MINIMAX_API_KEY=你的密钥\n'
                "密钥获取：https://platform.minimaxi.com/user-center/basic-information/interface-key\n"
                "（MiniMax Speech 与 H3 同一平台，如果你已跑过 H3 大概率已有 key。）"
            )
        if self._transport is _default_transport:
            try:
                self._require_requests()
            except ImportError as exc:  # pragma: no cover - 依赖缺失的清晰提示
                raise RuntimeError(
                    "缺 requests 库。请在 ComfyUI .venv 安装：\n"
                    '  "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" -m pip install requests'
                ) from exc
        log.info("MiniMax Speech 就绪（model=%s）", self.model)

    def list_voices(self) -> List[VoiceInfo]:
        return list(_MINIMAX_ZH_VOICES)

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path,
        *,
        emotion: str = "",
        delivery: str = "",
    ) -> str:
        self.preflight()
        voice = self.resolve_voice(voice_id)
        voice_setting: Dict[str, Any] = {
            "voice_id": voice,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        }
        if emotion in _EMOTION_SET:
            voice_setting["emotion"] = emotion
        audio_setting: Dict[str, Any] = {
            "sample_rate": self.sample_rate,
            "format": "wav",
            "channel": 1,
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": audio_setting,
        }
        d = delivery or ""
        if "快" in d:
            voice_setting["speed"] = 1.25
        elif "慢" in d:
            voice_setting["speed"] = 0.85
        if "电子" in d:
            # 机械电子感（仅 wav/mp3/flac 输出格式支持 sound_effects=robotic）
            payload["voice_modify"] = {"sound_effects": "robotic"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        status, _ctype, body = await self._transport(MINIMAX_T2A_URL, payload, headers)
        if status != 200:
            raise RuntimeError(
                f"MiniMax T2A HTTP {status}：{body[:300]!r}\n"
                "鉴权失败通常返回 1004（检查 MINIMAX_API_KEY），限流返回 1002（稍后重试）。"
            )
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise RuntimeError(f"MiniMax T2A 响应非 JSON：{body[:200]!r}") from exc
        br = data.get("base_resp") or {}
        if br.get("status_code", 0) != 0:
            raise RuntimeError(
                f"MiniMax T2A 业务错误 [{br.get('status_code')}]: {br.get('status_msg')}"
            )
        audio_hex = ((data.get("data") or {}).get("audio")) or ""
        if not audio_hex:
            raise RuntimeError("MiniMax T2A 响应缺少 data.audio（可能是配额/模型不可用）")
        try:
            raw = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise RuntimeError("MiniMax T2A data.audio 不是合法 hex") from exc

        out = str(output_path)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out, "wb") as f:
            f.write(raw)
        log.info("MiniMax T2A voice=%s text=%d字 → %s (%.1fKB)", voice, len(text), out, len(raw) / 1024)
        return out


# ---------------------------------------------------------------------------
# 音色快速复刻（可选，Phase B 阶段给主要角色做专属音色）
# ---------------------------------------------------------------------------
def clone_voice_sync(
    *,
    api_key: str,
    file_path: str,
    voice_id: str,
    prompt_audio: Optional[str] = None,
    prompt_text: str = "",
    model: str = DEFAULT_MODEL,
    audition_text: str = "你好，我是新角色。这是克隆音色的试听。",
    timeout: float = 120.0,
) -> str:
    """MiniMax 音色快速复刻（同步）：上传 ≥10s 音频 → 克隆 → 返回自定义 voice_id。

    限制（官方文档）：需实名认证；克隆音色 7 天未调用自动删除；Turbo 套餐赠 10 个。
    本函数只负责把克隆流程跑通；把返回的 voice_id 填进 backend.voice_map 即完成接入。

    用法示例（配合 blind_test_tts.py clone 子命令）：
        python tools/blind_test_tts.py clone --file D:/ref/林薇薇.wav --voice-id clone_linweiwei
    """
    import requests  # noqa: PLC0415 - 仅克隆路径才需要

    headers = {"Authorization": f"Bearer {api_key}"}

    # 1) 上传克隆音频（≥10s）
    with open(file_path, "rb") as f:
        r1 = requests.post(
            MINIMAX_UPLOAD_URL,
            headers=headers,
            data={"purpose": "voice_clone"},
            files={"file": (os.path.basename(file_path), f, "audio/wav")},
            timeout=timeout,
        )
    r1.raise_for_status()
    file_id = r1.json()["file"]["file_id"]

    # 2) 可选上传 prompt_audio（<8s 示例，指定角色说话风格）
    prompt_fid: Optional[str] = None
    if prompt_audio:
        with open(prompt_audio, "rb") as f:
            r2 = requests.post(
                MINIMAX_UPLOAD_URL,
                headers=headers,
                data={"purpose": "prompt_audio"},
                files={"file": (os.path.basename(prompt_audio), f, "audio/wav")},
                timeout=timeout,
            )
        r2.raise_for_status()
        prompt_fid = r2.json()["file"]["file_id"]

    # 3) 克隆
    payload: Dict[str, Any] = {
        "file_id": file_id,
        "voice_id": voice_id,
        "text": audition_text,
        "model": model,
    }
    if prompt_fid:
        payload["clone_prompt"] = {"prompt_audio": prompt_fid, "prompt_text": prompt_text}
    r3 = requests.post(
        MINIMAX_CLONE_URL,
        headers={**headers, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    r3.raise_for_status()
    resp = r3.json()
    br = resp.get("base_resp") or {}
    if br.get("status_code", 0) != 0:
        raise RuntimeError(
            f"MiniMax 克隆失败 [{br.get('status_code')}]: {br.get('status_msg')}"
        )
    log.info("MiniMax 克隆成功 voice_id=%s（7 天未调用自动删除）", voice_id)
    return voice_id
