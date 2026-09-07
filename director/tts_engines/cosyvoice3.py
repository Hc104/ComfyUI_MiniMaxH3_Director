#!/usr/bin/env python3
"""CosyVoice 3 后端（本地 zero-shot 音色克隆，经自托管 HTTP server，Phase 3 候选引擎）。

模型：Fun-CosyVoice3-0.5B-2512（0.5B、约 4GB 显存、Apache-2.0、2025/12 发布，
9 语言 + 18 中文方言，zero-shot 克隆 + instruct 支持情绪/语速）。

架构决策（TTS_VOICE_CAST_PLAN.md §6）：重模型进程与 ComfyUI 主进程隔离——
在 CosyVoice 仓库 conda 环境里跑 ``tools/cosyvoice_server.py``（FastAPI），
本后端只对它发 HTTP，可独立启停以过显存安全门（5080 上 H3 与本地 TTS 互斥）。

接口约定（见 tools/cosyvoice_server.py）：
  - 服务：python tools/cosyvoice_server.py --model_dir pretrained_models/Fun-CosyVoice3-0.5B --port 8898
  - 合成：POST http://127.0.0.1:8898/tts
      {text, prompt_text, prompt_wav}   → 200 audio/wav（24kHz mono）
      失败 → 400/500 JSON {message: "..."}
  - 健康：GET /health → {ok: true, model, sample_rate}

⛔ 本模块顶层不 import requests / torch / transformers；transport 注入可 mock。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from typing import Any, Dict, Optional, Tuple

from ..tts_engine import TtsEngine, VoiceInfo

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.tts_engines.cosyvoice3")

DEFAULT_BASE_URL = "http://127.0.0.1:8898"
DEFAULT_SAMPLE_RATE = 24000  # CosyVoice 3 采样率


def _default_refs_file(refs_dir: Optional[str] = None) -> str:
    if refs_dir:
        return os.path.join(refs_dir, "refs.json")
    env = os.environ.get("BLIND_TEST_REFS")
    if env:
        return env
    return os.path.join(os.getcwd(), "blind_test", "refs", "refs.json")


async def _default_transport(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    *,
    timeout: float = 180.0,
) -> Tuple[int, str, bytes]:
    import requests  # noqa: PLC0415 - 延迟导入

    resp = await asyncio.to_thread(
        requests.post, url, json=payload, headers=headers, timeout=timeout
    )
    return resp.status_code, resp.headers.get("Content-Type", ""), resp.content


class CosyVoice3Backend(TtsEngine):
    """本地 CosyVoice 3 后端（HTTP 调 tools/cosyvoice_server.py，zero-shot 克隆）。

    - ``base_url``：cosyvoice_server 服务地址，默认 http://127.0.0.1:8898。
    - ``refs_file``：refs.json（role → {wav, prompt_text, prompt_language}）。
    - ``transport``：注入 async HTTP 实现（单测 mock 免网络）。
    """

    name = "cosyvoice3"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        refs_file: Optional[str] = None,
        refs_dir: Optional[str] = None,
        transport: Any = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._refs_file = refs_file or _default_refs_file(refs_dir)
        self._transport = transport or _default_transport
        self._refs: Optional[Dict[str, Any]] = None

    # -- 参考音频（与 GPT-SoVITS 同款 refs.json 结构，prompt_language 仅保留字段） ----
    def _load_refs(self) -> Dict[str, Any]:
        if self._refs is not None:
            return self._refs
        if not os.path.exists(self._refs_file):
            raise RuntimeError(
                f"找不到参考音频清单 {self._refs_file}。\n"
                "请先运行：\n"
                '  "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" tools\\blind_test_tts.py prepare-refs'
            )
        with open(self._refs_file, "r", encoding="utf-8") as f:
            self._refs = json.load(f)
        return self._refs

    def resolve_ref(self, voice_id: str) -> Dict[str, str]:
        refs = self._load_refs()
        role = (voice_id or "").strip()
        if role.startswith("voice_"):
            role = role[len("voice_"):]
        if not role or role not in refs:
            raise RuntimeError(
                f"角色 {role!r} 无参考音频（可用角色：{sorted(refs)}）"
            )
        entry = refs[role]
        wav = str(entry.get("wav") or entry.get("wav_path") or "")
        if not os.path.isabs(wav):
            wav = os.path.join(os.path.dirname(self._refs_file), wav)
        if not os.path.exists(wav):
            raise RuntimeError(f"参考音频不存在：{wav}（先运行 prepare-refs 生成）")
        return {
            "wav": wav,
            "prompt_text": str(entry.get("prompt_text", "")),
            "prompt_language": str(entry.get("prompt_language", "zh")),
        }

    # -- TtsEngine 接口 ------------------------------------------------------
    def preflight(self) -> None:
        self.resolve_ref("voice_system")
        log.info("CosyVoice 3 后端就绪（base_url=%s）", self.base_url)

    def list_voices(self) -> list[VoiceInfo]:
        roles = sorted(self._load_refs())
        return [
            VoiceInfo(f"voice_{r}", f"参考音色·{r}", "zh-CN", "", self.name)
            for r in roles
        ]

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
        ref = self.resolve_ref(voice_id)
        payload: Dict[str, Any] = {
            "text": text,
            "prompt_text": ref["prompt_text"],
            "prompt_wav": ref["wav"],
        }
        try:
            status, ctype, body = await self._transport(
                self.base_url + "/tts", payload, {"Content-Type": "application/json"}
            )
        except Exception as exc:  # noqa: BLE001 - 连接失败给出启动命令
            raise RuntimeError(
                f"CosyVoice 3 连接失败（{self.base_url}）：{exc}\n"
                "请先在 CosyVoice 仓库的 conda 环境启动服务：\n"
                "  conda activate cosyvoice\n"
                '  python D:\\夸克网盘\\ComfyUI_MiniMaxH3_Director\\tools\\cosyvoice_server.py --model_dir pretrained_models/Fun-CosyVoice3-0.5B'
            ) from exc

        if status != 200 or not ctype.lower().startswith("audio/"):
            msg = ""
            try:
                msg = json.loads(body.decode("utf-8", "replace")).get("message") or ""
            except ValueError:
                msg = body[:300].decode("utf-8", "replace")
            raise RuntimeError(f"CosyVoice 3 合成失败 [{status}]: {msg}")

        out = str(output_path)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out, "wb") as f:
            f.write(body)
        log.info("CosyVoice 3 ref=%s text=%d字 → %s (%.1fKB)", ref["wav"], len(text), out, len(body) / 1024)
        return out
