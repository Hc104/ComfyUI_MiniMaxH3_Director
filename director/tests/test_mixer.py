#!/usr/bin/env python3
"""Phase 1：mixer.py FFmpeg ducking 混音单元测试（mock ffmpeg，不真调）。

覆盖：
- wav_duration：纯 Python 读 WAV header 算时长 / 非 WAV 报错
- probe_line_durations：manifest 已有 duration_sec 直接复用
- plan_line_timing：顺序 gap 累加 / 显式 start_sec 优先 / #583 overlap 硬规则（clamp）
- build_mix_filter：多行/单行的 adelay + sidechaincompress + amix 链
- mix_shot：
    h3_tts 全链（提取 h3 音轨 → filter 混音 → mux final.mp4 + manifest 写回锚点）
    h3_only 纯拷贝 / 空台词行回退 h3_only / 缺 video/manifest/tts 报错 / ffmpeg 缺失报错
- ⛔ 纯规则零 LLM 零显存：源码不 import torch / ollama / requests

运行：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" director/tests/test_mixer.py
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import tempfile
import types
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

from ComfyUI_MiniMaxH3_Director.director import mixer  # noqa: E402


def _make_wav(path, sr=44100, ch=1, bits=16, dur=1.0):
    """写一个标准 PCM WAV（数据区静音）。"""
    block_align = ch * (bits // 8)
    data_bytes = int(sr * block_align * dur)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_bytes))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<HHIIHH", 1, ch, sr, sr * block_align, block_align, bits))
        f.write(b"data")
        f.write(struct.pack("<I", data_bytes))
        f.write(b"\x00" * data_bytes)


def _setup_shot(tmp, *, lines_count=2, video=True, manifest=True):
    """构造一镜分层目录：video.mp4 + manifest.json + tts/line_NNN.wav。"""
    shot_dir = os.path.join(tmp, "minimax_studio", "projects", "测试镜", "shots", "shot_001")
    os.makedirs(os.path.join(shot_dir, "tts"), exist_ok=True)
    if video:
        with open(os.path.join(shot_dir, "video.mp4"), "wb") as f:
            f.write(b"fake-mp4")
    if manifest:
        lines = []
        for i in range(1, lines_count + 1):
            lines.append({
                "index": i,
                "file": f"tts/line_{i:03d}.wav",
                "text": f"台词{i}",
                "speaker": "角色",
                "voice_id": "voice_角色",
                "voice_type": "character_dialogue",
                "emotion": "",
                "delivery": "",
                "duration_sec": None,
            })
        with open(os.path.join(shot_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"project": "测试镜", "shot_id": "shot_001", "lines": lines}, f, ensure_ascii=False, indent=2)
    for i in range(1, lines_count + 1):
        _make_wav(os.path.join(shot_dir, "tts", f"line_{i:03d}.wav"), dur=1.0 + i * 0.2)
    return shot_dir


# ---------------------------------------------------------------------------
# wav_duration
# ---------------------------------------------------------------------------
def test_wav_duration_parses_header():
    tmp = tempfile.mkdtemp(prefix="mixer_wav_")
    p = os.path.join(tmp, "a.wav")
    _make_wav(p, sr=16000, ch=1, bits=16, dur=2.0)
    assert abs(mixer.wav_duration(p) - 2.0) < 1e-6


def test_wav_duration_rejects_non_wav():
    tmp = tempfile.mkdtemp(prefix="mixer_wav_bad_")
    p = os.path.join(tmp, "bad.wav")
    with open(p, "wb") as f:
        f.write(b"NOTRIFFWAVE" + b"\x00" * 64)
    try:
        mixer.wav_duration(p)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# probe_line_durations
# ---------------------------------------------------------------------------
def test_probe_line_durations_reuses_manifest_duration():
    """#583：wav 缺失/探测失败时才复用 manifest duration_sec（兜底）。
    line_001.wav 不存在 → 复用 manifest 3.5；line_002.wav 存在（标准 WAV）→ 真实 0.75。"""
    tmp = tempfile.mkdtemp(prefix="mixer_probe_")
    lines = [
        {"index": 1, "file": "tts/line_001.wav", "duration_sec": 3.5},
        {"index": 2, "file": "tts/line_002.wav", "duration_sec": None},
    ]
    p = os.path.join(tmp, "tts", "line_002.wav")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    _make_wav(p, dur=0.75)
    durs = mixer.probe_line_durations(tmp, lines)
    assert durs[1] == 3.5
    assert abs(durs[2] - 0.75) < 1e-6


def test_probe_line_durations_ffmpeg_fallback_for_non_wav():
    """非标准 WAV（旧版 edge-tts 落盘的 MP3 假 .wav）→ wav_duration 失败时用
    ffmpeg 探测时长兜底，混音链不中断。"""
    tmp = tempfile.mkdtemp(prefix="mixer_probe_ff_")
    shot_dir = os.path.join(tmp, "minimax_studio", "projects", "X", "shots", "shot_001")
    os.makedirs(os.path.join(shot_dir, "tts"), exist_ok=True)
    bad = os.path.join(shot_dir, "tts", "line_001.wav")
    with open(bad, "wb") as f:  # 假 MP3 内容（ID3 头，非 RIFF/WAVE）
        f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)
    lines = [{"index": 1, "file": "tts/line_001.wav", "duration_sec": None}]

    class _Proc:  # ffmpeg -i 的 stderr 带 Duration
        returncode = 0
        stdout = b""
        stderr = b"  Duration: 00:00:01.80, start: 0.0, bitrate: 32 kb/s\n"

    with mock.patch.object(mixer.subprocess, "run", return_value=_Proc()):
        durs = mixer.probe_line_durations(shot_dir, lines, ffmpeg="fake-ffmpeg")
    assert abs(durs[1] - 1.8) < 1e-6


def test_probe_line_durations_non_wav_no_ffmpeg_raises():
    """非标准 WAV 且无 ffmpeg、无 manifest duration_sec → 原样抛错（不掩盖探测失败根因）。"""
    tmp = tempfile.mkdtemp(prefix="mixer_probe_nof_")
    shot_dir = os.path.join(tmp, "minimax_studio", "projects", "X", "shots", "shot_001")
    os.makedirs(os.path.join(shot_dir, "tts"), exist_ok=True)
    bad = os.path.join(shot_dir, "tts", "line_001.wav")
    with open(bad, "wb") as f:
        f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)
    lines = [{"index": 1, "file": "tts/line_001.wav", "duration_sec": None}]
    try:
        mixer.probe_line_durations(shot_dir, lines)
        raise AssertionError("expected ValueError")
    except RuntimeError as exc:
        assert "无法探测 TTS 台词真实时长" in str(exc)


def test_probe_line_durations_ignores_stale_manifest_duration():
    """#583：wav 存在且可探测 → 以真实音频时长为准，忽略 manifest 残留旧 duration_sec。
    这是「TTS 生成后以真实 duration 校正」的核心回归：line wav 被重新合成/替换后，
    旧 duration_sec 不得再排时间轴（否则后一句提前开始 → TTS-TTS 重叠）。"""
    tmp = tempfile.mkdtemp(prefix="mixer_stale_")
    shot_dir = os.path.join(tmp, "minimax_studio", "projects", "X", "shots", "shot_001")
    os.makedirs(os.path.join(shot_dir, "tts"), exist_ok=True)
    wav = os.path.join(shot_dir, "tts", "line_001.wav")
    _make_wav(wav, dur=2.5)  # 真实 2.5s（重新合成后的新音频）
    lines = [
        {"index": 1, "file": "tts/line_001.wav", "duration_sec": 1.2},  # 残留旧值
    ]
    durs = mixer.probe_line_durations(shot_dir, lines)
    assert abs(durs[1] - 2.5) < 1e-6  # 用真实时长，不用 1.2


def test_probe_line_durations_ffmpeg_fallback_failure_raises():
    """ffmpeg 也探测不出时长（无 Duration 行）→ RuntimeError 带台词定位。"""
    tmp = tempfile.mkdtemp(prefix="mixer_probe_bad_")
    shot_dir = os.path.join(tmp, "minimax_studio", "projects", "X", "shots", "shot_001")
    os.makedirs(os.path.join(shot_dir, "tts"), exist_ok=True)
    bad = os.path.join(shot_dir, "tts", "line_003.wav")
    with open(bad, "wb") as f:
        f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)
    lines = [{"index": 3, "file": "tts/line_003.wav", "duration_sec": None}]

    class _Proc:  # stderr 无 Duration → 探测失败
        returncode = 0
        stdout = b""
        stderr = b"some random ffmpeg output without duration\n"

    with mock.patch.object(mixer.subprocess, "run", return_value=_Proc()):
        try:
            mixer.probe_line_durations(shot_dir, lines, ffmpeg="fake-ffmpeg")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "line_003.wav" in str(exc)


# ---------------------------------------------------------------------------
# plan_line_timing
# ---------------------------------------------------------------------------
def test_plan_line_timing_sequential_gap():
    lines = [{"index": 1}, {"index": 2}, {"index": 3}]
    durs = {1: 1.0, 2: 0.8, 3: 1.2}
    t = mixer.plan_line_timing(lines, durs, gap_sec=0.3)
    assert t[0]["start_sec"] == 0.0 and t[0]["end_sec"] == 1.0 and t[0]["duration_sec"] == 1.0
    assert t[1]["start_sec"] == 1.3
    assert t[2]["start_sec"] == 2.4


def test_plan_line_timing_explicit_start_priority():
    lines = [{"index": 1, "start_sec": 5.0, "end_sec": 7.0}, {"index": 2}]
    t = mixer.plan_line_timing(lines, {1: 1.0, 2: 2.0}, gap_sec=0.5)
    assert t[0]["start_sec"] == 5.0
    assert t[0]["end_sec"] == 6.0  # #583：end 以真实 duration 为准，不信任显式 end_sec 7.0
    assert t[1]["start_sec"] == 6.0  # 顺序行不早于显式行播完（修复原「从 0 排」顺序错乱）


def test_plan_line_timing_clamps_overlap():
    """#583：显式 start_sec 与上一句真实播完时刻重叠 → 钳制到 prev_end，绝不撞车。
    用户示例：Line1「就这？」0.00→1.36 / Line2 请求 0.8 → 钳制到 1.36 串行。"""
    lines = [
        {"index": 1, "start_sec": 0.0},
        {"index": 2, "start_sec": 0.8},  # 与 line1 真实结束 1.36 重叠
    ]
    t = mixer.plan_line_timing(lines, {1: 1.36, 2: 2.84})
    assert t[0]["start_sec"] == 0.0 and t[0]["end_sec"] == 1.36
    assert t[0]["clamped"] is False
    assert t[1]["start_sec"] == 1.36  # 被钳制到 line1 播完
    assert t[1]["end_sec"] == 1.36 + 2.84
    assert t[1]["clamped"] is True


def test_plan_line_timing_no_clamp_sequential():
    """顺序分支（无显式 start_sec）天然串行，clamped 一律 False。"""
    lines = [{"index": 1}, {"index": 2}, {"index": 3}]
    t = mixer.plan_line_timing(lines, {1: 1.0, 2: 0.8, 3: 1.2}, gap_sec=0.3)
    assert [x["clamped"] for x in t] == [False, False, False]
    assert t[0]["start_sec"] == 0.0
    assert t[1]["start_sec"] == 1.3
    assert t[2]["start_sec"] == 2.4


# ---------------------------------------------------------------------------
# _ensure_h3_audio（#582：video 更新后丢弃旧环境音重新提取）
# ---------------------------------------------------------------------------
def test_ensure_h3_audio_reextract_when_video_newer():
    """重新生成视频后（video.mp4 mtime 更新）→ 旧 h3_audio.wav 作废，重新提取，
    避免混音用上一次生成音轨造成「画面/声音不匹配」（听感像声音重叠）。"""
    tmp = tempfile.mkdtemp(prefix="mixer_h3stale_")
    shot_dir = os.path.join(tmp, "minimax_studio", "projects", "X", "shots", "shot_001")
    os.makedirs(shot_dir, exist_ok=True)
    h3_wav = os.path.join(shot_dir, "h3_audio.wav")
    video = os.path.join(shot_dir, "video.mp4")
    with open(h3_wav, "wb") as f:
        f.write(b"old-h3")
    with open(video, "wb") as f:
        f.write(b"fake-mp4")
    old = 1_000_000.0
    os.utime(h3_wav, (old, old))
    os.utime(video, (old + 10, old + 10))  # video 更新

    with mock.patch.object(mixer, "extract_h3_audio", return_value=True) as m_extract:
        path = mixer._ensure_h3_audio("fake-ffmpeg", shot_dir, video)
    m_extract.assert_called_once()
    assert path == h3_wav


def test_ensure_h3_audio_reuses_when_video_old():
    """video 不比 h3_audio.wav 新 → 复用现有环境音（重混音幂等，不重复提取）。"""
    tmp = tempfile.mkdtemp(prefix="mixer_h3fresh_")
    shot_dir = os.path.join(tmp, "minimax_studio", "projects", "X", "shots", "shot_001")
    os.makedirs(shot_dir, exist_ok=True)
    h3_wav = os.path.join(shot_dir, "h3_audio.wav")
    video = os.path.join(shot_dir, "video.mp4")
    with open(h3_wav, "wb") as f:
        f.write(b"old-h3")
    with open(video, "wb") as f:
        f.write(b"fake-mp4")
    old = 1_000_000.0
    os.utime(video, (old, old))
    os.utime(h3_wav, (old + 10, old + 10))  # h3 更新

    with mock.patch.object(mixer, "extract_h3_audio", return_value=True) as m_extract:
        path = mixer._ensure_h3_audio("fake-ffmpeg", shot_dir, video)
    m_extract.assert_not_called()
    assert path == h3_wav


# ---------------------------------------------------------------------------
# build_mix_filter
# ---------------------------------------------------------------------------
def test_build_mix_filter_multi_line():
    timing = [
        {"index": 1, "start_sec": 0.0, "end_sec": 1.0, "duration_sec": 1.0},
        {"index": 2, "start_sec": 1.3, "end_sec": 2.1, "duration_sec": 0.8},
    ]
    fc = mixer.build_mix_filter(timing)
    assert "adelay=0:all=1" in fc
    assert "adelay=1300:all=1" in fc
    assert "sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300" in fc
    assert "amix=inputs=2" in fc
    assert "asplit=2[tts_sc][tts_voice]" in fc
    assert "[ducked][tts_voice]amix=inputs=2" in fc
    # #582：sidechain 触发源 apad（否则短台词截断 [ducked]）
    assert "[tts_sc]apad[tts_sc_pad]" in fc
    assert "[h3][tts_sc_pad]sidechaincompress=" in fc
    assert fc.index("apad") < fc.index("sidechaincompress")  # apad 在触发源处先补


def test_build_mix_filter_single_line():
    timing = [{"index": 1, "start_sec": 2.0, "end_sec": 3.0, "duration_sec": 1.0}]
    fc = mixer.build_mix_filter(timing)
    assert "[l1]asplit=2[tts_sc][tts_voice]" in fc
    assert "adelay=2000:all=1" in fc
    # #582：单行同样要 apad 触发源
    assert "[tts_sc]apad[tts_sc_pad]" in fc
    assert "[h3][tts_sc_pad]sidechaincompress=" in fc


def test_build_mix_filter_sidechain_apad_prevents_truncation():
    """#582 回归：sidechaincompress 输出时长=min(主输入, sidechain 输入)。
    5s 视频 + 2s 台词时若触发源不 pad，[ducked] 被截到 2s → mixed 2s → final 2s。
    修复后触发源 apad 无限静音，输出始终跟随主输入 h3 时长。"""
    timing = [
        {"index": 1, "start_sec": 0.0, "end_sec": 1.0, "duration_sec": 1.0},
    ]
    fc = mixer.build_mix_filter(timing)
    # 触发源与主输入之间的链必须是 apad（唯一一处 pad 语义）
    chain = fc.split("sidechaincompress")[0]
    assert "[tts_sc]apad[tts_sc_pad]" in chain
    assert "[h3]" in chain and "[tts_sc_pad]" in chain


def test_build_mix_filter_empty_raises():
    try:
        mixer.build_mix_filter([])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# mix_shot 端到端（mock ffmpeg）
# ---------------------------------------------------------------------------
def test_mix_shot_h3_tts_full_pipeline():
    tmp = tempfile.mkdtemp(prefix="mixer_shot_")
    shot_dir = _setup_shot(tmp, lines_count=2)
    calls: list[list[str]] = []
    with (
        mock.patch.object(mixer, "run_ffmpeg", side_effect=lambda ff, args: calls.append(list(args))),
        mock.patch.object(mixer, "_probe_video_audio", return_value=True),
        mock.patch.object(mixer, "_video_duration", return_value=None),
    ):
        result = mixer.mix_shot(shot_dir, ffmpeg="fake-ffmpeg")

    assert result["mode"] == "h3_tts"
    assert result["line_count"] == 2
    assert result["overlap_fixes"] == 0  # 顺序串行无显式 start_sec → 无钳制
    assert result["final_file"] == os.path.join(shot_dir, "final.mp4")
    assert result["h3_audio_file"] == os.path.join(shot_dir, "h3_audio.wav")

    # 调用序列：0=提取 h3 音轨，1=filter 混音，2=mux 回视频
    assert len(calls) == 3, f"expect 3 ffmpeg calls, got {len(calls)}"
    assert "-vn" in calls[0] and any("h3_audio.wav" in tok for tok in calls[0])
    assert "-filter_complex" in calls[1] and "sidechaincompress" in calls[1][calls[1].index("-filter_complex") + 1]
    assert calls[2][calls[2].index("-c:v") + 1] == "copy"
    assert calls[2][-1] == os.path.join(shot_dir, "final.mp4")

    # manifest 写回锚点（幂等复用）
    with open(os.path.join(shot_dir, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["lines"][0]["start_sec"] == 0.0
    assert manifest["lines"][1]["start_sec"] > 0.0
    assert manifest["lines"][0]["duration_sec"] == 1.2  # line_001 dur=1.0+1*0.2

    # 临时 _mixed.wav 已清理
    assert not os.path.exists(os.path.join(shot_dir, "tts", "._mixed.wav"))


def test_mix_shot_h3_only_copies():
    tmp = tempfile.mkdtemp(prefix="mixer_h3only_")
    shot_dir = _setup_shot(tmp, lines_count=0, manifest=False)
    calls: list[list[str]] = []
    with (
        mock.patch.object(mixer, "run_ffmpeg", side_effect=lambda ff, args: calls.append(list(args))),
        mock.patch.object(mixer, "_probe_video_audio", return_value=False),
    ):
        result = mixer.mix_shot(shot_dir, mode="h3_only", ffmpeg="fake-ffmpeg")

    assert result["mode"] == "h3_only"
    assert result["line_count"] == 0
    assert result["final_file"] == os.path.join(shot_dir, "final.mp4")
    # h3_only：video 原样拷贝（-c copy）；提取探测无音轨 → 不额外生成静音
    assert len(calls) == 1
    assert calls[0][calls[0].index("-c") + 1] == "copy"


def test_mix_shot_empty_lines_falls_back_h3_only():
    tmp = tempfile.mkdtemp(prefix="mixer_empty_")
    shot_dir = _setup_shot(tmp, lines_count=0, manifest=True)
    calls: list[list[str]] = []
    with (
        mock.patch.object(mixer, "run_ffmpeg", side_effect=lambda ff, args: calls.append(list(args))),
        mock.patch.object(mixer, "_probe_video_audio", return_value=False),
    ):
        result = mixer.mix_shot(shot_dir, ffmpeg="fake-ffmpeg")
    assert result["mode"] == "h3_only"
    assert result["line_count"] == 0


def test_mix_shot_missing_video_raises():
    tmp = tempfile.mkdtemp(prefix="mixer_novid_")
    shot_dir = _setup_shot(tmp, video=False)
    try:
        mixer.mix_shot(shot_dir, ffmpeg="fake-ffmpeg")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "缺少 H3 视频" in str(exc)


def test_mix_shot_missing_tts_file_raises():
    tmp = tempfile.mkdtemp(prefix="mixer_notts_")
    shot_dir = _setup_shot(tmp, lines_count=2)
    os.remove(os.path.join(shot_dir, "tts", "line_002.wav"))
    try:
        mixer.mix_shot(shot_dir, ffmpeg="fake-ffmpeg")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "缺少 TTS 台词文件" in str(exc)


def test_mix_shot_ffmpeg_missing_raises():
    tmp = tempfile.mkdtemp(prefix="mixer_noff_")
    shot_dir = _setup_shot(tmp, lines_count=1)
    with mock.patch.object(mixer, "ffmpeg_bin", return_value=None):
        try:
            mixer.mix_shot(shot_dir, ffmpeg=None)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "ffmpeg 不可用" in str(exc)


# ---------------------------------------------------------------------------
# 纯规则零 LLM 零显存
# ---------------------------------------------------------------------------
def _has_import(src: str, bad: str) -> bool:
    return re.search(rf"(?m)^\s*(?:import|from)\s+{re.escape(bad)}\b", src) is not None


def test_no_llm_gpu_side_effects():
    src_path = os.path.join(_REPO_ROOT, "director", "mixer.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    for bad in ("torch", "ollama", "requests"):
        assert not _has_import(src, bad), f"mixer.py 不应 import {bad}"


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
