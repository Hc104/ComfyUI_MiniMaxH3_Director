#!/usr/bin/env python3
"""GPT-SoVITS 后端（本地 V4，zero-shot 音色克隆，Phase 3 候选引擎）。

接口事实（官方 api.py，2026-08-16 源码核实）：
  - 服务启动：``python api.py -dr 参考音频.wav -dt 参考文本 -dl zh``（默认端口 9880）
    （-dr/-dt 只设全局默认参考；本后端每次合成请求带该角色的参考音频，不依赖全局默认）
  - 合成：POST http://127.0.0.1:9880/
      {refer_wav_path, prompt_text, prompt_language, text, text_language,
       top_k, top_p, temperature, speed}
      成功 → 200，Content-Type: audio/wav，body = wav 字节流（V4 原生 48kHz）
      失败 → 400，Content-Type: application/json，{code, message}
  - 参考音频：官方建议 3~10 秒单人干音 + 对应转写文本（prompt_text），
    用它做 zero-shot 音色克隆。V4 显存需求约 4~6GB（5080 可用）。

盲听测试用法：``blind_test_tts.py prepare-refs`` 生成 blind_test/refs/*.wav +
refs.json（四角色参考音频+文本），本后端按 voice_id 取对应参考 → 逐次请求克隆合成。

⛔ 本模块顶层不 import requests；transport 注入可 mock（免网络单测）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from typing import Any, Dict, Optional, Tuple

from ..tts_engine import TtsEngine, VoiceInfo

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.tts_engines.gpt_sovits")

DEFAULT_BASE_URL = "http://127.0.0.1:9880"
DEFAULT_SAMPLE_RATE = 48000  # GPT-SoVITS V4 原生输出


def _default_refs_file(refs_dir: Optional[str] = None) -> str:
    """默认参考音频清单路径（blind_test/refs/refs.json，可被环境变量覆盖）。"""
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
    timeout: float = 120.0,
) -> Tuple[int, str, bytes]:
    import requests  # noqa: PLC0415 - 延迟导入

    resp = await asyncio.to_thread(
        requests.post, url, json=payload, headers=headers, timeout=timeout
    )
    return resp.status_code, resp.headers.get("Content-Type", ""), resp.content


class GPTSoVITSBackend(TtsEngine):
    """本地 GPT-SoVITS V4 后端（HTTP 调官方 api.py，zero-shot 克隆）。

    - ``base_url``：api.py 服务地址，默认 http://127.0.0.1:9880。
    - ``refs_file``：refs.json（role → {wav, prompt_text, prompt_language}）。
    - ``top_k / top_p / temperature / speed``：GPT-SoVITS 推理参数（API 直传）。
    - ``transport``：注入 async HTTP 实现（单测 mock 免网络）。
    """

    name = "gpt-sovits"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        refs_file: Optional[str] = None,
        refs_dir: Optional[str] = None,
        top_k: int = 15,
        top_p: float = 0.6,
        temperature: float = 0.6,
        speed: float = 1.0,
        transport: Any = None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._refs_file = refs_file or _default_refs_file(refs_dir)
        self.top_k = top_k
        self.top_p = top_p
        self.temperature = temperature
        self.speed = speed
        self._transport = transport or _default_transport
        self._refs: Optional[Dict[str, Any]] = None

    # -- 参考音频 --------------------------------------------------------------
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
        """voice_id → {wav, prompt_text, prompt_language}（voice_ 前缀可省略）。"""
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
        # 至少确认参考音频就绪；服务连通性在 synthesize 内探测（给出清晰启动命令）。
        self.resolve_ref("voice_system")
        log.info("GPT-SoVITS 后端就绪（base_url=%s）", self.base_url)

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
            "refer_wav_path": ref["wav"],
            "prompt_text": ref["prompt_text"],
            "prompt_language": ref["prompt_language"],
            "text": text,
            "text_language": "zh",
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "speed": self.speed,
        }
        try:
            status, ctype, body = await self._transport(
                self.base_url + "/", payload, {"Content-Type": "application/json"}
            )
        except Exception as exc:  # noqa: BLE001 - 连接失败给出启动命令
            raise RuntimeError(
                f"GPT-SoVITS 连接失败（{self.base_url}）：{exc}\n"
                "请先在 GPT-SoVITS 目录启动服务，例如：\n"
                '  python api.py -dr blind_test/refs/guyanchen.wav -dt "我是顾琰宸。江海市的天，我说了算。" -dl zh'
            ) from exc

        if status != 200 or not ctype.lower().startswith("audio/"):
            msg = ""
            try:
                msg = json.loads(body.decode("utf-8", "replace")).get("message") or ""
            except ValueError:
                msg = body[:300].decode("utf-8", "replace")
            raise RuntimeError(f"GPT-SoVITS 合成失败 [{status}]: {msg}")

        out = str(output_path)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out, "wb") as f:
            f.write(body)
        log.info("GPT-SoVITS ref=%s text=%d字 → %s (%.1fKB)", ref["wav"], len(text), out, len(body) / 1024)
        return out
