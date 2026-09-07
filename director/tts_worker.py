#!/usr/bin/env python3
"""TTS 落盘 worker（Phase 0：AudioIntent → 逐行 TTS → 分层目录 + manifest.json）。

用户 2026-08-16 拍板（TTS_VOICE_CAST_PLAN.md §5.4）：TTS 落盘 = ComfyUI output 分层：

    output/minimax_studio/projects/{项目名}/shots/{shot_id}/
        ├── video.mp4        ← H3 生成视频（Phase 1 后 H3 侧写入）
        ├── h3_audio.wav     ← H3 原音轨提取（Phase 1 混音用）
        ├── tts/
        │   ├── line_001.wav
        │   └── line_002.wav
        ├── manifest.json    ← line → text/voice_id/emotion/delivery/时长
        └── final.mp4        ← Phase 1 混音成片

收益：重生成某一句只替换 tts/line_002.wav 再重混音，不需重生成整镜。

本模块职责：
- ``synthesize_shot(intent, project_name, *, backend, base_dir)``：async，逐行调
  ``TtsEngine.synthesize`` 落盘 tts/line_NNN.wav + 写 manifest.json，返回落盘摘要。
- ``build_manifest(intent, project_name, engine)``：纯函数，生成 manifest dict（可预览）。
- ``shot_output_dir(project_name, shot_id, *, base_dir)``：路径解析 + 建目录。

⛔ 本模块不 import torch / ollama / requests；TTS 合成是唯一外部调用点（由传入 backend 承担）。
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re

from typing import Any, Dict, List, Optional

from .audio_intent import AudioIntent
from .mixer import wav_duration
from .tts_engine import TtsEngine

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.tts_worker")

# 文件名净化：项目名可能含中文（保留），但剥离路径分隔符与非法字符。
_INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_name(name: str) -> str:
    return _INVALID_PATH_CHARS.sub("_", (name or "").strip()) or "untitled"


def default_output_dir() -> str:
    """ComfyUI output 根目录（folder_paths.get_output_directory）。非 ComfyUI 环境回退。"""
    try:
        import folder_paths  # noqa: PLC0415 - ComfyUI 运行时才有

        return str(folder_paths.get_output_directory())
    except Exception:  # pragma: no cover - 非 ComfyUI 环境（自测脚本）回退
        return os.environ.get(
            "COMFY_OUTPUT_DIR", os.path.join(os.getcwd(), "output")
        )


def shot_output_dir(
    project_name: str,
    shot_id: str,
    *,
    base_dir: Optional[str] = None,
) -> str:
    """返回一镜的输出根目录（并创建目录结构）。

    结构：<base_dir>/minimax_studio/projects/{project}/shots/{shot}/
    base_dir 缺省 = ComfyUI output 根（folder_paths.get_output_directory）。
    """
    root = base_dir or default_output_dir()
    d = os.path.join(
        root,
        "minimax_studio",
        "projects",
        _sanitize_name(project_name),
        "shots",
        _sanitize_name(shot_id),
    )
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "tts"), exist_ok=True)
    return d


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def build_manifest(
    intent: AudioIntent,
    project_name: str,
    *,
    engine: str = "unknown",
) -> Dict[str, Any]:
    """纯函数：把 AudioIntent 转成 manifest dict（不落盘，路由预览用）。

    lines 条目 = index/file/text/speaker/voice_type/voice_id/emotion/delivery/duration_sec。
    duration_sec 合成后由 synthesize_shot 用真实音频时长填充（时间轴唯一事实源，
    见 #583）；本纯函数先留 None（合成前无法得知真实时长）。
    """
    lines: List[Dict[str, Any]] = []
    for i, ln in enumerate(intent.lines, start=1):
        lines.append(
            {
                "index": i,
                "file": f"tts/line_{i:03d}.wav",
                "text": ln.text,
                "speaker": ln.speaker,
                "voice_type": ln.voice_type,
                "voice_id": ln.voice_id,
                "emotion": ln.emotion,
                "delivery": ln.delivery,
                "duration_sec": None,
            }
        )
    return {
        "project": project_name,
        "shot_id": intent.shot_id,
        "scene_id": intent.scene_id,
        "ambient": intent.ambient,
        "music": intent.music,
        "engine": engine,
        "generated_at": _now_iso(),
        "lines": lines,
    }


async def synthesize_shot(
    intent: AudioIntent,
    project_name: str,
    *,
    backend: TtsEngine,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """把 AudioIntent 逐行合成到 shot 分层目录，返回落盘摘要。

    - 每行 → tts/line_NNN.wav（Edge-TTS save 按 .wav 扩展名输出 wav 编码）
    - 全部成功 → 写 manifest.json（含逐行材料清单，供 Phase 1 混音定位）
    - 中途某行失败 → 抛 RuntimeError（调用方决定中断还是继续，Phase 0 先中断）
    """
    shot_dir = shot_output_dir(project_name, intent.shot_id, base_dir=base_dir)
    tts_dir = os.path.join(shot_dir, "tts")
    manifest = build_manifest(intent, project_name, engine=getattr(backend, "name", "unknown"))

    for entry in manifest["lines"]:
        idx = entry["index"]
        out = os.path.join(tts_dir, f"line_{idx:03d}.wav")
        text = entry["text"]
        try:
            await backend.synthesize(
                text,
                entry["voice_id"],
                out,
                emotion=entry["emotion"],
                delivery=entry["delivery"],
            )
        except Exception as exc:  # noqa: BLE001 - 逐行失败给出定位
            raise RuntimeError(
                f"TTS 合成失败 shot={intent.shot_id} line_{idx:03d} "
                f"(voice_id={entry['voice_id']})：{exc}"
            ) from exc
        # #583：合成后立即探测真实音频时长写入 manifest（时间轴唯一事实源）。
        #   标准 WAV（Edge-TTS riff-pcm）→ 纯 Python 读 header；旧版 edge-tts
        #   落盘的 MP3 假 .wav 探测失败 → 保持 None，mix 时 ffmpeg 兜底。
        try:
            entry["duration_sec"] = round(wav_duration(out), 3)
        except (ValueError, OSError):
            entry["duration_sec"] = None
        log.info("TTS 落盘 shot=%s line_%03d.wav (%.0f 字, %.2fs)", intent.shot_id, idx, len(text), entry["duration_sec"] or 0.0)

    manifest_path = os.path.join(shot_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {
        "shot_dir": shot_dir,
        "tts_dir": tts_dir,
        "manifest": manifest,
        "manifest_file": manifest_path,
        "line_count": len(manifest["lines"]),
    }
