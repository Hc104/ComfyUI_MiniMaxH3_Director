#!/usr/bin/env python3
"""Phase 1：FFmpeg ducking 混音器（H3 原音保留 + TTS 人声叠加 + 自动 ducking）。

用户 2026-08-16 拍板（TTS_VOICE_CAST_PLAN.md §5.2，决策 B）：
  - 混音 = H3 原音轨保留 + TTS 叠加 + 自动 ducking（平时环境声正常 / 对白时 H3 自动降 /
    对白结束恢复），以「叠加」为底层机制。
  - 落盘 = `output/minimax_studio/projects/{项目}/shots/{shot}/` 分层：
        video.mp4      ← H3 生成视频（H3 侧写入）
        h3_audio.wav   ← H3 原音轨提取（本模块）
        tts/line_NNN.wav
        manifest.json  ← 每行补 start_sec/end_sec/duration_sec（本模块写回，重混音可复用）
        final.mp4      ← 混音成片（本模块）
  - 收益：重生成某一句只替换 tts/line_002.wav 再重混音，不需重生成整镜。

混音链（ffmpeg filter_complex）：
    h3 环境音 aformat 归一 → sidechain 触发源(TTS) 与 人声轨分离
    → sidechaincompress（TTS 人声触发 H3 压低，threshold/ratio/attack/release 可调）
    → [ducked 环境音] + [TTS 人声] amix → aout
    最后 mux 回 video.mp4（-c:v copy 不重编码）→ final.mp4

锚点：每行 start_sec 显式值优先（前端/剧本/AI 提供）；缺省按顺序自动排列——
    逐句 ffprobe 语义用纯 Python 读 WAV header（wav_duration）计算时长，句间 gap_sec。

⛔ 纯规则零 LLM 零显存：本模块不 import torch / ollama / requests；ffmpeg 是唯一外部
调用点（subprocess，-y 幂等重跑）。ffmpeg 解析 = PATH 优先，其次 imageio-ffmpeg。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import subprocess
from typing import Any, Dict, List, Optional

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.mixer")

# sidechaincompress 默认参数（可调）：threshold=0.03 略高于底噪，ratio=8 对白时明显压低
_DUCK_THRESHOLD = 0.03
_DUCK_RATIO = 8
_DUCK_ATTACK_MS = 20
_DUCK_RELEASE_MS = 300
_MIX_SAMPLE_RATE = 44100
_MIX_CHANNELS = "stereo"


# ---------------------------------------------------------------------------
# ffmpeg 解析
# ---------------------------------------------------------------------------
def ffmpeg_bin() -> Optional[str]:
    """解析 ffmpeg：PATH 优先，其次 imageio-ffmpeg 自带二进制（同 stream_export）。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        from imageio_ffmpeg import get_ffmpeg_exe  # noqa: PLC0415 - 懒加载，非本模块硬依赖

        return get_ffmpeg_exe()
    except Exception:  # pragma: no cover - 防御
        return None


def run_ffmpeg(ffmpeg: str, args: List[str]) -> None:
    """执行一条 ffmpeg 命令；非零退出 → RuntimeError（含 stderr 尾部）。"""
    cmd = [ffmpeg, "-y"] + list(args)
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = (
            proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        ).strip()
        raise RuntimeError(
            f"ffmpeg 失败（exit {proc.returncode}）：\n{err[-2000:]}"
        )


# ---------------------------------------------------------------------------
# 时长探测（纯 Python，读 WAV header，零 ffmpeg 依赖）
# ---------------------------------------------------------------------------
def wav_duration(path: str) -> float:
    """解析标准 PCM WAV 的 data chunk 字节数 → 时长（秒）。Edge-TTS 输出即标准 WAV。

    失败（非 RIFF/WAVE 或缺少关键 chunk）→ ValueError。
    """
    with open(path, "rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[:4] != b"RIFF" or hdr[8:12] != b"WAVE":
            raise ValueError(f"不是标准 WAV 文件：{path}")
        sample_rate: Optional[int] = None
        block_align: Optional[int] = None
        data_bytes: Optional[int] = None
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                break
            cid = chunk[:4]
            (csize,) = struct.unpack("<I", chunk[4:8])
            if cid == b"fmt ":
                payload = f.read(min(csize, 4096))
                if len(payload) >= 16:
                    sample_rate = struct.unpack("<I", payload[4:8])[0]
                    block_align = struct.unpack("<H", payload[12:14])[0]
                if csize > len(payload):  # 跳过 fmt 剩余
                    f.seek(csize - len(payload), os.SEEK_CUR)
            elif cid == b"data":
                data_bytes = csize
                f.seek(csize, os.SEEK_CUR)  # data 之后一般无内容；跳过以保持指针一致
            else:
                f.seek(csize, os.SEEK_CUR)
            if csize % 2 == 1:  # chunk 对齐到偶数
                f.seek(1, os.SEEK_CUR)
    if sample_rate and block_align and data_bytes is not None:
        if sample_rate <= 0 or block_align <= 0:
            raise ValueError(f"WAV 参数非法：{path}（rate={sample_rate}, align={block_align}）")
        return data_bytes / float(sample_rate * block_align)
    raise ValueError(f"WAV 缺少 fmt/data chunk：{path}")


def _ffmpeg_media_duration(ffmpeg: str, path: str) -> Optional[float]:
    """ffmpeg 探测媒体时长（对非标准 WAV / MP3 内容的假 .wav 兜底）。

    旧版 edge-tts 不支持 output_format 时落盘的 .wav 实际是 MP3 字节流，
    纯 Python 读 WAV header 失败 → 走 ffmpeg ``-i`` 解析 Duration。失败返回 None。
    """
    try:
        proc = subprocess.run([ffmpeg, "-i", path], capture_output=True)
    except OSError:  # pragma: no cover - ffmpeg 二进制缺失防御
        return None
    merged = (proc.stderr or b"").decode("utf-8", "replace")
    for line in merged.splitlines():
        marker = "Duration:"
        if marker in line:
            seg = line.split(marker, 1)[1].split(",", 1)[0].strip()
            parts = seg.split(":")
            if len(parts) == 3:
                try:
                    return (
                        int(parts[0]) * 3600
                        + int(parts[1]) * 60
                        + float(parts[2])
                    )
                except ValueError:
                    return None
    return None


def probe_line_durations(
    shot_dir: str,
    lines: List[Dict[str, Any]],
    *,
    ffmpeg: Optional[str] = None,
) -> Dict[int, float]:
    """逐行探测 tts/line_NNN.wav 的**真实音频时长**——混音时间轴的唯一事实源。

    ⛔ #583：TTS 生成后以真实音频 duration 为准，不再直接信任 manifest 里残留的
    duration_sec（行级重合成/替换 wav 后旧时长会导致时间轴错位 → TTS-TTS 重叠）。
    探测顺序：
      1. 标准 WAV → 纯 Python 读 header（wav_duration，毫秒级零依赖）；
      2. 非标准 WAV（旧版 edge-tts 落盘的 MP3 假 .wav）→ ffmpeg 探测兜底；
      3. 以上全失败 → 复用 manifest duration_sec（仅兜底，保留重混音不中断语义）。
    行文件存在性已由 mix_shot 前置校验，此处不再重复。
    """
    durations: Dict[int, float] = {}
    for ln in lines:
        idx = int(ln.get("index", 0))
        rel = str(ln.get("file") or f"tts/line_{idx:03d}.wav")
        path = os.path.join(shot_dir, rel)
        dur: Optional[float] = None
        try:
            dur = wav_duration(path)
        except (ValueError, OSError):
            if ffmpeg:
                dur = _ffmpeg_media_duration(ffmpeg, path)
        if dur is None or dur <= 0:
            # 探测全失败 → 复用 manifest duration_sec（兜底；不抛错中断重混音）
            existing = ln.get("duration_sec")
            if isinstance(existing, (int, float)) and existing > 0:
                durations[idx] = float(existing)
                continue
            raise RuntimeError(
                f"无法探测 TTS 台词真实时长：{rel}"
                + ("" if ffmpeg else "（未提供 ffmpeg 兜底）")
            )
        durations[idx] = dur
    return durations


# ---------------------------------------------------------------------------
# 时间轴锚点规划（纯函数）
# ---------------------------------------------------------------------------
def plan_line_timing(
    lines: List[Dict[str, Any]],
    durations: Dict[int, float],
    *,
    gap_sec: float = 0.3,
) -> List[Dict[str, Any]]:
    """为每一行分配 start_sec/end_sec（台词时间轴调度器）。

    优先级：显式 start_sec（前端/剧本/AI 提供）> 顺序自动排列（句间 gap_sec）。

    ⛔ #583 TTS-TTS 重叠硬规则（V1 全角色对白默认串行）：无论 start_sec 从哪来，
    下一句 start 一律 `max(requested, prev_end)` 钳制——prev_end = 上一句
    start + **真实音频 duration**。上游传进错误/重叠的时间也不会撞车。
    end_sec 一律 = start + 真实 duration，不信任显式 end_sec（显式 end 可能是按
    字数估算，与 TTS 实际时长不符）。

    返回 [{index, start_sec, end_sec, duration_sec, clamped}]，
    clamped=True 表示显式 start_sec 被重叠钳制过（上游时间轴错误被自动修复）。
    """
    cursor = 0.0
    prev_end = 0.0
    out: List[Dict[str, Any]] = []
    for ln in lines:
        idx = int(ln.get("index", 0))
        dur = durations.get(idx, 0.0)
        explicit = ln.get("start_sec")
        clamped = False
        if explicit is not None:
            requested = max(0.0, float(explicit))
            start = max(requested, prev_end)  # ⛔ overlap 硬规则：不早于上一句播完
            if start > requested + 1e-6:
                clamped = True
            end = start + dur
        else:
            start = max(cursor, prev_end)
            end = start + dur
            cursor = end + gap_sec
        prev_end = end
        out.append(
            {
                "index": idx,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(dur, 3),
                "clamped": clamped,
            }
        )
    return out


# ---------------------------------------------------------------------------
# ffmpeg filter_complex 构建（纯函数，可测）
# ---------------------------------------------------------------------------
def build_mix_filter(timing: List[Dict[str, Any]]) -> str:
    """生成 ducking 混音 filter_complex。

    输入约定：``[0:a]`` = H3 环境音轨；``[i:a]``（i 从 1 起）= 第 i 句 TTS wav。
    链：aformat 归一 → adelay 摆位 → amix 成人声轨 → asplit → sidechaincompress
    （人声触发 H3 压低）→ [ducked]+[人声] amix → aout。
    """
    sr = _MIX_SAMPLE_RATE
    cl = _MIX_CHANNELS
    parts: List[str] = []
    parts.append(f"[0:a]aformat=sample_rates={sr}:channel_layouts={cl}[h3]")
    for i, t in enumerate(timing, start=1):
        ms = max(0, int(round(float(t["start_sec"]) * 1000)))
        parts.append(
            f"[{i}:a]aformat=sample_rates={sr}:channel_layouts={cl},"
            f"adelay={ms}:all=1[l{i}]"
        )
    n = len(timing)
    if n == 1:
        parts.append("[l1]asplit=2[tts_sc][tts_voice]")
    elif n > 1:
        ins = "".join(f"[l{i}]" for i in range(1, n + 1))
        parts.append(
            f"{ins}amix=inputs={n}:duration=longest:normalize=0[tts_all]"
        )
        parts.append("[tts_all]asplit=2[tts_sc][tts_voice]")
    else:
        raise ValueError("build_mix_filter: 无台词行，无需混音")
    # ⛔ #582：sidechaincompress 的输出时长 = min(主输入, sidechain 输入)，
    #   而不是跟随主输入（ffmpeg 4.4.2 实测）。5s 视频 + 2s 台词时，触发源只有
    #   2s → [ducked] 被截到 2s → mixed_wav 2s → mux `-shortest` 把 final.mp4
    #   截成 2s（「5 秒视频只生成 2 秒」）。
    #   修复：sidechain 触发源 apad 无限静音 → sidechaincompress 输出始终跟随
    #   主输入 h3 时长（完整覆盖视频画面）。
    parts.append("[tts_sc]apad[tts_sc_pad]")
    parts.append(
        f"[h3][tts_sc_pad]sidechaincompress="
        f"threshold={_DUCK_THRESHOLD}:ratio={_DUCK_RATIO}:"
        f"attack={_DUCK_ATTACK_MS}:release={_DUCK_RELEASE_MS}[ducked]"
    )
    parts.append(
        "[ducked][tts_voice]amix=inputs=2:duration=longest:normalize=0[aout]"
    )
    return ";\n".join(parts)


# ---------------------------------------------------------------------------
# H3 音轨提取 / 静音兜底
# ---------------------------------------------------------------------------
def _probe_video_audio(ffmpeg: str, video_path: str) -> bool:
    """探测视频是否有音轨（读 ffmpeg -i 的 stderr，不真正解码）。"""
    proc = subprocess.run(
        [ffmpeg, "-i", video_path], capture_output=True
    )
    merged = (proc.stderr or b"").decode("utf-8", "replace")
    return "Audio:" in merged


def extract_h3_audio(ffmpeg: str, video_path: str, out_wav: str) -> bool:
    """从 H3 视频提取原音轨 → h3_audio.wav（pcm_s16le/44100/stereo）。

    视频无音轨 → 返回 False（调用方以静音兜底）。
    """
    if not _probe_video_audio(ffmpeg, video_path):
        return False
    try:
        run_ffmpeg(
            ffmpeg,
            ["-i", video_path, "-vn", "-c:a", "pcm_s16le",
             "-ar", str(_MIX_SAMPLE_RATE), "-ac", "2", out_wav],
        )
        return True
    except RuntimeError as exc:
        log.warning("H3 音轨提取失败，以静音兜底：%s", exc)
        return False


def _silence_wav(ffmpeg: str, out_wav: str, duration: float) -> None:
    """生成静音环境音轨（视频无音轨时兜底，保证 ducking 链输入存在）。"""
    dur = max(1.0, float(duration))
    run_ffmpeg(
        ffmpeg,
        ["-f", "lavfi", "-i", f"anullsrc=r={_MIX_SAMPLE_RATE}:cl=stereo",
         "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", out_wav],
    )


def _ensure_h3_audio(ffmpeg: str, shot_dir: str, video_path: str) -> str:
    """确保 h3_audio.wav 存在且与当前 video.mp4 匹配（提取成功或静音兜底）。

    ⛔ #582：video.mp4 比 h3_audio.wav 新（重新生成视频后）→ 丢弃旧环境音重新
    提取，避免混音用上一次生成的音轨造成「画面/声音不匹配」（听感像声音重叠）。
    """
    h3_wav = os.path.join(shot_dir, "h3_audio.wav")
    if os.path.isfile(h3_wav):
        try:
            if os.path.getmtime(video_path) <= os.path.getmtime(h3_wav):
                return h3_wav
        except OSError:  # pragma: no cover - 探测失败保守复用，不阻塞混音
            return h3_wav
        log.info("video.mp4 已更新，重新提取 h3_audio.wav（丢弃旧环境音）")
    if not extract_h3_audio(ffmpeg, video_path, h3_wav):
        # 无音轨 → 静音兜底（时长取视频时长，探测失败则 5s）
        duration = _video_duration(ffmpeg, video_path) or 5.0
        _silence_wav(ffmpeg, h3_wav, duration)
    return h3_wav


def _video_duration(ffmpeg: str, video_path: str) -> Optional[float]:
    """从 ffmpeg -i stderr 解析 Duration（秒）。失败返回 None（委托通用媒体探测）。"""
    return _ffmpeg_media_duration(ffmpeg, video_path)


# ---------------------------------------------------------------------------
# 混音主入口
# ---------------------------------------------------------------------------
def _render_h3_only(
    shot_dir: str, video_path: str, ffmpeg: str
) -> Dict[str, Any]:
    """纯 H3 导出：video.mp4 原样拷贝 → final.mp4（-c copy 不重编码）。

    顺带提取 h3_audio.wav（分层完整性；无音轨则跳过）。
    """
    h3_wav = os.path.join(shot_dir, "h3_audio.wav")
    if not os.path.isfile(h3_wav):
        try:
            extract_h3_audio(ffmpeg, video_path, h3_wav)
        except RuntimeError:  # pragma: no cover - 提取失败不影响纯 H3 拷贝
            log.warning("纯 H3 导出：音轨提取失败，跳过 h3_audio.wav")
    final = os.path.join(shot_dir, "final.mp4")
    run_ffmpeg(ffmpeg, ["-i", video_path, "-map", "0", "-c", "copy", final])
    return {
        "mode": "h3_only",
        "shot_dir": shot_dir,
        "video_file": video_path,
        "h3_audio_file": h3_wav if os.path.isfile(h3_wav) else "",
        "final_file": final,
        "line_count": 0,
        "lines": [],
    }


def mix_shot(
    shot_dir: str,
    *,
    mode: str = "h3_tts",
    ffmpeg: Optional[str] = None,
    gap_sec: float = 0.3,
) -> Dict[str, Any]:
    """混音一镜：video.mp4 + manifest.json + tts/*.wav → final.mp4。

    mode:
      h3_only 纯 H3 导出（无 TTS / 不想加人声）
      h3_tts  H3 原音保留 + TTS 人声叠加 + ducking（默认）

    流程：读 manifest → 探测行时长 → 规划锚点 → 提取 H3 音轨（或静音兜底）→
    filter_complex 混音 → mux 回视频 → 写回 manifest 锚点（重混音幂等复用）。
    """
    ff = ffmpeg or ffmpeg_bin()
    if not ff:
        raise RuntimeError(
            "ffmpeg 不可用：无法混音。请在 PATH 安装 FFmpeg 或 "
            "`pip install imageio-ffmpeg`，再重新混音。"
        )

    video_path = os.path.join(shot_dir, "video.mp4")
    if not os.path.isfile(video_path):
        raise RuntimeError(f"缺少 H3 视频：{video_path}（先生成视频再混音）")

    # 纯 H3 导出：不需要 manifest/TTS，视频原样拷贝即可
    if mode == "h3_only":
        return _render_h3_only(shot_dir, video_path, ff)

    if mode not in ("h3_tts",):
        raise ValueError(f"未知混音模式：{mode}（支持 h3_tts / h3_only）")

    manifest_path = os.path.join(shot_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise RuntimeError(f"缺少 manifest.json：{manifest_path}（先 TTS 再混音）")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    lines: List[Dict[str, Any]] = manifest.get("lines") or []

    # 无台词行 → 退回纯 H3 导出
    if not lines:
        return _render_h3_only(shot_dir, video_path, ff)

    # 1) 行文件齐全性检查
    tts_dir = os.path.join(shot_dir, "tts")
    for ln in lines:
        idx = int(ln.get("index", 0))
        rel = str(ln.get("file") or f"tts/line_{idx:03d}.wav")
        if not os.path.isfile(os.path.join(shot_dir, rel)):
            raise RuntimeError(
                f"缺少 TTS 台词文件：{rel}（shot={os.path.basename(shot_dir)}，"
                f"先合成再混音）"
            )

    # 2) 时长探测 + 锚点规划（ffmpeg 兜底：旧版 edge-tts 落盘的 MP3 假 .wav）
    durations = probe_line_durations(shot_dir, lines, ffmpeg=ff)
    timing = plan_line_timing(lines, durations, gap_sec=gap_sec)

    # 3) H3 环境音轨（提取 / 静音兜底）
    h3_wav = _ensure_h3_audio(ff, shot_dir, video_path)

    # 4) 混音 → 临时混合轨
    filter_cx = build_mix_filter(timing)
    mixed_wav = os.path.join(tts_dir, "._mixed.wav")
    mix_cmd: List[str] = ["-i", h3_wav]
    for t in timing:
        idx = t["index"]
        rel = f"tts/line_{idx:03d}.wav"
        mix_cmd += ["-i", os.path.join(shot_dir, rel)]
    mix_cmd += [
        "-filter_complex", filter_cx,
        "-map", "[aout]",
        "-ar", str(_MIX_SAMPLE_RATE), "-ac", "2",
        mixed_wav,
    ]
    run_ffmpeg(ff, mix_cmd)

    # 5) mux 回视频（不重编码视频流）
    final_path = os.path.join(shot_dir, "final.mp4")
    try:
        run_ffmpeg(
            ff,
            ["-i", video_path, "-i", mixed_wav,
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", final_path],
        )
    finally:
        if os.path.isfile(mixed_wav):
            try:
                os.remove(mixed_wav)
            except OSError:  # pragma: no cover - 清理失败不影响结果
                pass

    # 6) 写回 manifest 锚点（重混音幂等复用；前端展示对白时间轴）
    for t in timing:
        for ln in lines:
            if int(ln.get("index", 0)) == t["index"]:
                ln["start_sec"] = t["start_sec"]
                ln["end_sec"] = t["end_sec"]
                ln["duration_sec"] = t["duration_sec"]
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    log.info(
        "混音完成 shot=%s mode=%s lines=%d → final.mp4",
        os.path.basename(shot_dir), mode, len(timing),
    )
    return {
        "mode": mode,
        "shot_dir": shot_dir,
        "video_file": video_path,
        "h3_audio_file": h3_wav,
        "final_file": final_path,
        "line_count": len(timing),
        "overlap_fixes": sum(1 for t in timing if t.get("clamped")),
        "lines": timing,
    }
