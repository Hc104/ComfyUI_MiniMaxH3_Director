"""流式导出（Streaming Export）：逐段落盘 mp4 + ffmpeg 合并。

内存与段数解耦：每段生成完立即编码成独立 mp4，张量随即释放；全部完成后用
ffmpeg concat（流式、内存占用极小）合并成 movie.mp4。全程内存峰值 ≈ 1~2 段，
不再像「全部导出」那样把所有段的 float32 帧堆在 CPU 内存里一次性 torch.cat。

依赖：
- 编码/解码走 ComfyUI 自带 PyAV（``av``，``comfy_extras/nodes_video.py`` 依赖）。
- 合并走 ffmpeg（PATH 优先，其次 imageio-ffmpeg 自带二进制）。

注意事项：
- 每段以 h264+aac 编码，画布/帧率固定，保证 concat 可 `-c copy` 直拼。
- 本模块不承担段间像素级接缝处理（曝光修正/接缝桥）——那是「全部导出」
  ``concat_continuous_chunks`` 的事。流式导出按剪辑软件习惯做「直接拼接」，
  段间连续性已由生成时的续接机制（r2v 图片锚点 / fl2v 硬锁）保证。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from fractions import Fraction
from typing import Any

import torch

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.stream_export")


# ---------------------------------------------------------------------------
# 二进制解析
# ---------------------------------------------------------------------------

def _load_av():
    try:
        import av
    except ImportError as exc:  # pragma: no cover - ComfyUI always ships av
        raise RuntimeError(
            "「流式导出」需要 ComfyUI 自带的 PyAV（av）。当前环境未找到 av，"
            "请确认 ComfyUI 完整安装，或改用「分段导出」。"
        ) from exc
    return av


def ffmpeg_bin() -> str | None:
    """解析 ffmpeg：PATH 优先，其次 imageio-ffmpeg 自带二进制。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 目录 / 路径
# ---------------------------------------------------------------------------

def make_stream_dir(node_id: str | None) -> str:
    """创建本次运行的流式临时目录并清空旧文件。"""
    import folder_paths

    run_dir = os.path.join(
        folder_paths.get_temp_directory(),
        "minimax_director_stream",
        str(node_id or "run").replace("/", "_").replace("\\", "_"),
    )
    os.makedirs(run_dir, exist_ok=True)
    for name in os.listdir(run_dir):
        try:
            os.remove(os.path.join(run_dir, name))
        except OSError:
            pass
    return run_dir


def resolve_movie_output_path(width: int, height: int, *, prefix: str = "director_stream") -> str:
    """按 ComfyUI 命名规则生成 movie.mp4 的落盘路径（输出目录）。"""
    import folder_paths

    full_output_folder, filename, counter, _subfolder, _prefix = folder_paths.get_save_image_path(
        prefix, folder_paths.get_output_directory(), int(width), int(height)
    )
    return os.path.join(full_output_folder, f"{filename}_{counter:05}_.mp4")


def resolve_scene_run_paths(
    width: int, height: int, num_scenes: int, *,
    prefix: str = "Director_Scene", scene_names: list[str] | None = None,
) -> tuple[list[str], str]:
    """一次运行的场景 mp4 + Movie.mp4 落盘路径（输出目录，同一运行序号）。

    scene 模式：把每个场景编码成 ``*_Scene01.mp4``…（交付件）。
    movie 模式：场景 mp4 编码好后，ffmpeg 直拼成 ``*_Movie.mp4``。
    所有路径共用 ComfyUI 计数器，避免与历史输出重名。

    scene_names（Scene Manager 可选）：按场景命名，如 ``*_Scene01_水晶森林.mp4``。
    名字会做安全清洗（去重音/空白/非安全字符），空名回落 SceneNN 默认。
    """
    import folder_paths
    import re as _re

    full_out, filename, counter, _subfolder, _prefix = folder_paths.get_save_image_path(
        prefix, folder_paths.get_output_directory(), int(width), int(height)
    )
    base = os.path.join(full_out, f"{filename}_{counter:05}")
    names = list(scene_names or [])
    scene_paths: list[str] = []
    for gi in range(max(1, int(num_scenes))):
        seg_path = f"{base}_Scene{gi + 1:02d}.mp4"
        if gi < len(names):
            _clean = _re.sub(r"[^\w一-鿿-]+", "_", str(names[gi] or "")).strip("_")
            if _clean:
                seg_path = f"{base}_Scene{gi + 1:02d}_{_clean}.mp4"
        scene_paths.append(seg_path)
    return scene_paths, f"{base}_Movie.mp4"


# ---------------------------------------------------------------------------
# 单段编码
# ---------------------------------------------------------------------------

def save_segment_mp4(
    path: str,
    frames: torch.Tensor,
    audio: dict[str, Any] | None,
    *,
    fps: float,
) -> str:
    """把 [F,H,W,3] 帧（float [0,1]）+ 可选 AUDIO dict 编码成 mp4（h264+aac）。

    audio: ComfyUI AUDIO dict，``{"waveform": [1, C, T] float [-1,1], "sample_rate": int}``。
    空音频/无音频段照常出视频（静音）。
    """

    def _fps_to_avrate(value: float) -> Fraction | int:
        """PyAV ``add_stream`` 的 rate 只接受 int 或 Fraction，不接受 float。

        ``round(fps, 3)`` 返回的是 float，会触发 PyAV ``to_avrational`` 对
        ``frac.numerator`` 的 AttributeError。整帧率转 int，非整帧率转最简分数。
        """
        fps2 = max(0.01, float(value or 24.0))
        frac = Fraction(fps2).limit_denominator(1000000)
        return frac.numerator if frac.denominator == 1 else frac

    av = _load_av()
    frames = frames.detach().cpu()
    if frames.ndim != 4 or int(frames.shape[-1]) not in (3, 4):
        raise ValueError(f"Stream export: frames shape {tuple(frames.shape)} not [F,H,W,3/4]")
    h = int(frames.shape[1])
    w = int(frames.shape[2])
    n = int(frames.shape[0])
    if n <= 0 or h <= 0 or w <= 0:
        raise ValueError("Stream export: empty frames")
    fps_f = max(0.01, float(fps or 24.0))

    # uint8 转换（f16/f32 均可）
    u8 = torch.clamp(frames[..., :3] * 255.0, 0.0, 255.0).to(dtype=torch.uint8)

    # 显式指定容器格式，别依赖文件扩展名：minimax_segment_mp4 路由的临时编码
    # 文件是 `seg_XXXX.mp4.tmp.{pid}`（原子替换前不叫 .mp4），PyAV 若按扩展名
    # 推断会抛 "Could not determine output format"，导致下载/编码失败（#84）。
    container = av.open(path, mode="w", format="mp4")

    vstream = container.add_stream("h264", rate=_fps_to_avrate(fps_f))
    vstream.width = w
    vstream.height = h
    vstream.pix_fmt = "yuv420p"
    vstream.options = {"crf": "18", "preset": "medium"}

    wave = None
    sr = 44100
    astream = None
    if (
        isinstance(audio, dict)
        and isinstance(audio.get("waveform"), torch.Tensor)
        and int(audio["waveform"].numel()) > 0
    ):
        wave = audio["waveform"].detach().cpu()
        sr = int(audio.get("sample_rate") or 44100) or 44100
        if wave.ndim == 3:
            wave = wave.squeeze(0)  # [C, T]
        if wave.ndim != 2 or int(wave.shape[0]) <= 0:
            wave = None
        if wave is not None:
            astream = container.add_stream("aac", rate=sr)
            # PyAV 版本差异：AudioCodecContext 的部分属性在不同版本不可写/不存在。
            # - sample_fmt：部分版本无该可写属性（AttributeError）。
            # - channels：新版本为只读，由 layout 推导（stereo=2 / mono=1），不可写。
            # frame 侧已显式指定 format/layout，PyAV encode 会按 frame 自动对齐，
            # 因此这些设置失败静默跳过即可。
            try:
                astream.sample_fmt = "fltp"
            except (AttributeError, TypeError):
                pass
            try:
                astream.layout = "stereo" if int(wave.shape[0]) >= 2 else "mono"
            except (AttributeError, TypeError):
                pass

    # 视频帧
    for i in range(n):
        frame = av.VideoFrame.from_ndarray(u8[i].numpy(), format="rgb24")
        for packet in vstream.encode(frame):
            container.mux(packet)
    for packet in vstream.encode():
        container.mux(packet)

    # 音频帧
    if astream is not None and wave is not None:
        ch = int(wave.shape[0])
        if ch not in (1, 2):
            wave = wave[:2] if ch >= 2 else wave[:1]
        wave = wave.contiguous()
        planar = wave.numpy()  # [C, T] float32, value [-1,1]
        aframe = av.AudioFrame.from_ndarray(
            planar, format="fltp", layout="stereo" if int(wave.shape[0]) == 2 else "mono"
        )
        aframe.sample_rate = sr
        for packet in astream.encode(aframe):
            container.mux(packet)
    if astream is not None:
        for packet in astream.encode():
            container.mux(packet)

    container.close()
    return path


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------

def merge_segment_mp4s(segment_paths: list[str], out_path: str) -> str:
    """ffmpeg concat demuxer 合并多段 mp4 → movie.mp4。

    优先 `-c copy`（不重编码，快）；若 copy 失败（编码参数不齐）回退
    libx264+aac 重编码。全程流式，内存占用极小。
    """
    if not segment_paths:
        raise ValueError("merge_segment_mp4s: no segments")
    ff = ffmpeg_bin()
    if not ff:
        raise RuntimeError(
            "ffmpeg 不可用：无法合并流式导出片段。请在 PATH 安装 FFmpeg 或"
            "`pip install imageio-ffmpeg`，或改用「分段导出」。"
        )

    concat_txt = out_path + ".concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{p.replace(os.sep, '/')}'\n")

    base = [ff, "-y", "-f", "concat", "-safe", "0", "-i", concat_txt]
    copy_cmd = base + ["-c", "copy", "-movflags", "+faststart", out_path]
    encode_cmd = base + [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", out_path,
    ]

    try:
        subprocess.run(copy_cmd, check=True, capture_output=True, timeout=3600)
        log.info("Streaming export: merged %d segment(s) with -c copy", len(segment_paths))
    except subprocess.CalledProcessError:
        try:
            subprocess.run(encode_cmd, check=True, capture_output=True, timeout=7200)
            log.info("Streaming export: merged %d segment(s) with re-encode", len(segment_paths))
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "ffmpeg 合并失败（流式导出）：\n"
                + (exc.stderr.decode("utf-8", "replace") if exc.stderr else str(exc))
            ) from exc
    finally:
        try:
            os.remove(concat_txt)
        except OSError:
            pass
    return out_path


# ---------------------------------------------------------------------------
# 解码回张量（节点 images 输出用）
# ---------------------------------------------------------------------------

def decode_video_tensor(path: str, *, dtype: torch.dtype = torch.float32) -> torch.Tensor:
    """把 mp4 解码回 [F,H,W,3] 张量（float [0,1]，dtype 可指定 f32/f16）。"""
    av = _load_av()
    container = av.open(path)
    frames: list[torch.Tensor] = []
    try:
        for frame in container.decode(video=0):
            arr = frame.to_ndarray(format="rgb24")  # [H,W,3] uint8
            t = torch.from_numpy(arr).to(dtype=dtype) / 255.0
            frames.append(t)
    finally:
        container.close()
    if not frames:
        raise ValueError(f"decode_video_tensor: no frames decoded from {path}")
    return torch.stack(frames, dim=0)


def concat_audio_dicts(audio_list: list[dict[str, Any]]) -> dict[str, Any] | None:
    """把多个 ComfyUI AUDIO dict（waveform=[1,C,T] float [-1,1]）按时间拼成一段。

    场景导出里多个镜头共用同一声景，需要把它们的生成音频拼成一条再编码进
    SceneNN.mp4。采样率取第一段；声道数取最大并补零对齐；空输入返回 None（纯视频）。
    """
    audios = [
        a for a in audio_list
        if isinstance(a, dict) and isinstance(a.get("waveform"), torch.Tensor)
        and int(a["waveform"].numel()) > 0
    ]
    if not audios:
        return None
    sr = int(audios[0].get("sample_rate") or 44100) or 44100
    waves: list[torch.Tensor] = []
    max_ch = 1
    for a in audios:
        w = a["waveform"].detach().cpu()
        if w.ndim == 3:
            w = w.squeeze(0)  # [C,T]
        if w.ndim != 2 or int(w.shape[0]) <= 0 or int(w.shape[1]) <= 0:
            continue
        max_ch = max(max_ch, int(w.shape[0]))
        waves.append(w)
    if not waves:
        return None
    padded: list[torch.Tensor] = []
    for w in waves:
        ch, t = int(w.shape[0]), int(w.shape[1])
        if ch < max_ch:
            w = torch.cat([w, torch.zeros(max_ch - ch, t, dtype=w.dtype)], dim=0)
        padded.append(w.unsqueeze(0))  # [1,C,T]
    return {"waveform": torch.cat(padded, dim=2), "sample_rate": sr}


def estimated_merged_bytes(total_frames: int, width: int, height: int, elem: int) -> int:
    """估算合并视频张量字节数（解码回节点输出用）。"""
    return (
        max(1, int(total_frames))
        * max(1, int(width))
        * max(1, int(height))
        * 3
        * int(elem)
    )
