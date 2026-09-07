#!/usr/bin/env python
"""#484 解码卡死隔离诊断脚本 v2（用户本机跑）。

v2 修复（重要）
--------------
v1 的输出把用户骗到了：隔离测试在视频解码处报 CUDA OOM（13.6 GiB allocated），
但那其实是脚本自身的假阳性 —— v1 没有像 ComfyUI 正式执行那样包 torch.inference_mode()，
导致 ViT3D 解码器 36 层 transformer 的全部中间激活被 autograd 图保留，
一层 ~450MB × 36 层 ≈ 13.6GB 直接打爆显存。

ComfyUI 正式链路（execution.py:751 `with torch.inference_mode():`）不建 autograd 图，
中间激活随层即算即丢。v2 保持一致：解码全程包 inference_mode()，并加显存记账
（allocated/reserved/peak），让输出反映真实解码成本。

用途
----
用户报告「视频生成不了，一直在解码」：SPA 生成跑到 decode 阶段后 4-7 分钟无输出。
本脚本把「采样/导演管线」从方程里剔除，只用合成零 latent 直连两个 VAE 解码，
逐步打印耗时与显存，定位解码阶段是否：
  1. 本身过慢 / OOM（即使完全驻留也跑不动）
  2. 音频 VAE 解码卡死

运行前提
--------
* 先完全退出 ComfyUI Desktop（避免显存占用与动态加载干扰），本脚本自会打印显存。
* 用 ComfyUI 的 venv python 运行，无需启动任何服务。

输出解读
--------
* 看到 [DIAG] video decode DONE → 视频解码在完全驻留下正常且够快，问题在 SPA 侧
  （aimdo 动态驻留/UNET 未释放），转查 SPA 路径。
* 视频解码在 inference_mode 下仍 OOM → 解码本身超显存，需降分辨率/关音频/缩小 tile。
* 卡在 [DIAG] video temporal chunk #N start 不动 → 时间块 N 内卡死。
* 卡在 [DIAG] audio decode start 不动 → 音频 VAE 解码卡死。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import traceback

# ---------------------------------------------------------------------------
# 路径推导（tools/  →  自定义节点根  →  ComfyUI 根）
# ---------------------------------------------------------------------------
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_CUSTOM_NODE_ROOT = os.path.dirname(_TOOLS_DIR)


def _resolve_comfyui_root() -> str:
    _candidates = [
        os.environ.get("COMFYUI_ROOT", ""),
        os.path.dirname(os.path.dirname(_CUSTOM_NODE_ROOT)),  # DEP 布局推导
        r"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI",              # 已知部署路径回退
    ]
    for _c in _candidates:
        if not _c:
            continue
        if os.path.isdir(os.path.join(_c, "comfy")) and os.path.isdir(os.path.join(_c, "models")):
            return _c
    return os.path.dirname(os.path.dirname(_CUSTOM_NODE_ROOT))


_COMFYUI_ROOT = _resolve_comfyui_root()
for _p in (_COMFYUI_ROOT, _CUSTOM_NODE_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import torch  # noqa: E402
import comfy.model_management  # noqa: E402
import comfy.sd  # noqa: E402
import comfy.utils  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def _mem_summary(label: str = "") -> None:
    """打印当前 CUDA 显存状态（allocated / reserved / peak）。"""
    if not torch.cuda.is_available():
        return
    try:
        alloc = torch.cuda.memory_allocated() / 2 ** 20
        reserved = torch.cuda.memory_reserved() / 2 ** 20
        peak = torch.cuda.max_memory_allocated() / 2 ** 20
        tag = f" [{label}]" if label else ""
        log(f"[DIAG]    MEM{tag}: allocated={alloc:.0f}MiB reserved={reserved:.0f}MiB peak={peak:.0f}MiB")
    except Exception:  # noqa: BLE001
        pass


def _load_vae(path: str, kind: str):
    if not os.path.exists(path):
        log(f"[DIAG] ERROR: {kind} VAE 文件不存在: {path}")
        sys.exit(1)
    log(f"[DIAG] 加载 {kind} VAE: {path}")
    t0 = time.time()
    sd, metadata = comfy.utils.load_torch_file(path, return_metadata=True)
    vae = comfy.sd.VAE(sd=sd, metadata=metadata)
    vae.throw_exception_if_invalid()
    n_params = sum(p.numel() * p.element_size() for p in vae.first_stage_model.parameters())
    log(f"[DIAG] 加载完成 t={time.time() - t0:.1f}s vae={type(vae).__name__} "
        f"first_stage={type(vae.first_stage_model).__name__} "
        f"device={vae.device} output_device={vae.output_device} vae_dtype={vae.vae_dtype}")
    log(f"[DIAG] VAE patcher: {type(vae.patcher).__name__} is_dynamic={vae.patcher.is_dynamic()} "
        f"参数体积≈{n_params / 2 ** 20:.0f}MiB")
    return vae


def _watchdog(stop_flag: threading.Event, label: str) -> None:
    """每 15s 打印一次心跳，用户据此判断「慢」还是「卡死」。"""
    t0 = time.time()
    while not stop_flag.wait(15.0):
        log(f"[DIAG] {label} 仍在运行 … 已等待 {time.time() - t0:.0f}s")
    log(f"[DIAG] {label} 观察线程结束（解码已返回）")


def _format_shape(shape) -> str:
    return "x".join(str(int(s)) for s in shape)


def _run_video_decode(vae, latent, args) -> bool:
    fs = vae.first_stage_model
    cls = fs.__class__

    log(f"[DIAG] video VAE 解码配置: tokens_chunk_size={getattr(fs, 'tokens_chunk_size', '?')} "
        f"token_drop={getattr(fs, 'token_drop', '?')} "
        f"tiling={getattr(fs, 'tiling', '?')} "
        f"tile_size={getattr(fs, 'tile_size', '?')} overlap={getattr(fs, 'overlap', '?')}")
    if args.no_tile:
        fs.tiling = False
        log("[DIAG] --no-tile 已生效：空间 tiling 关闭，走整块解码对照")

    orig_ad = cls._adaptive_decode
    orig_px = cls._decode_pixels
    counter = {"chunk": 0, "tile": 0}

    def _wrapped_adaptive_decode(self, z):
        counter["chunk"] += 1
        t0 = time.time()
        log(f"[DIAG]  ▶ temporal chunk #{counter['chunk']} start z={_format_shape(z.shape)}")
        out = orig_ad(self, z)
        torch.cuda.synchronize()
        dt = time.time() - t0
        _mem_summary(f"chunk #{counter['chunk']} done t={dt:.1f}s")
        log(f"[DIAG]  ✔ temporal chunk #{counter['chunk']} done t={dt:.1f}s out={_format_shape(out.shape)}")
        return out

    def _wrapped_decode_pixels(self, z):
        counter["tile"] += 1
        t0 = time.time()
        out = orig_px(self, z)
        torch.cuda.synchronize()
        dt = time.time() - t0
        if dt > 1.0:  # 只打印超过 1s 的慢 tile，避免刷屏
            _mem_summary(f"tile #{counter['tile']} t={dt:.1f}s")
            log(f"[DIAG]    ├ tile #{counter['tile']} done t={dt:.1f}s out={_format_shape(out.shape)}")
        return out

    cls._adaptive_decode = _wrapped_adaptive_decode
    cls._decode_pixels = _wrapped_decode_pixels

    stop = threading.Event()
    wd = threading.Thread(target=_watchdog, args=(stop, "video decode"), daemon=True)
    wd.start()

    try:
        t0 = time.time()
        log(f"[DIAG] video decode 开始 latent={_format_shape(latent.shape)} dtype={latent.dtype}")
        # v2 关键修复：与 ComfyUI 正式执行一致，全程 inference_mode（不建 autograd 图）
        with torch.inference_mode():
            images = vae.decode(latent)
        torch.cuda.synchronize()
        stop.set()
        _mem_summary("video decode DONE")
        log(f"[DIAG] ✅ video decode DONE t={time.time() - t0:.1f}s "
            f"images={_format_shape(images.shape)} device={images.device}")
        return True
    except KeyboardInterrupt:
        stop.set()
        log("[DIAG] 用户中断（Ctrl+C）。若卡在 chunk/tile start 后无 done → 该步卡死。")
        return False
    except Exception as e:  # noqa: BLE001
        stop.set()
        log(f"[DIAG] ❌ video decode ERROR: {e!r}")
        traceback.print_exc()
        return False
    finally:
        cls._adaptive_decode = orig_ad
        cls._decode_pixels = orig_px
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _run_audio_decode(vae, latent, args) -> None:
    stop = threading.Event()
    wd = threading.Thread(target=_watchdog, args=(stop, "audio decode"), daemon=True)
    wd.start()
    try:
        t0 = time.time()
        log(f"[DIAG] audio decode 开始 latent={_format_shape(latent.shape)} dtype={latent.dtype}")
        with torch.inference_mode():
            audio = vae.decode(latent).movedim(-1, 1)
        torch.cuda.synchronize()
        stop.set()
        _mem_summary("audio decode DONE")
        log(f"[DIAG] ✅ audio decode DONE t={time.time() - t0:.1f}s "
            f"audio={_format_shape(audio.shape)} device={audio.device}")
    except KeyboardInterrupt:
        stop.set()
        log("[DIAG] 用户中断（Ctrl+C）。若卡在 audio decode start 后无输出 → 音频解码卡死。")
    except Exception as e:  # noqa: BLE001
        stop.set()
        log(f"[DIAG] ❌ audio decode ERROR: {e!r}")
        traceback.print_exc()


def main() -> int:
    ap = argparse.ArgumentParser(description="#484 解码卡死隔离诊断 v2")
    ap.add_argument("--width", type=int, default=864, help="画面宽（32 对齐，默认 864）")
    ap.add_argument("--height", type=int, default=480, help="画面高（32 对齐，默认 480）")
    ap.add_argument("--length", type=int, default=124, help="帧数（默认 124 ≈ 5s，17k+5 网格取整）")
    ap.add_argument("--tiny", action="store_true",
                    help="小分辨率快速基线（256x256, 22帧，单时间块）——先验证解码器本身能跑")
    ap.add_argument("--video-vae", default=os.path.join(
        _COMFYUI_ROOT, "models", "vae", "minimax_h3_video_vae_fp16.safetensors"))
    ap.add_argument("--audio-vae", default=os.path.join(
        _COMFYUI_ROOT, "models", "vae", "minimax_h3_audio_vae_fp32.safetensors"))
    ap.add_argument("--no-audio", action="store_true",
                    help="跳过音频解码（等价 DIRECTOR_DECODE_AUDIO=0）")
    ap.add_argument("--no-tile", action="store_true",
                    help="关闭空间 tiling 走整块解码（对照实验，检验 tile 是否卡死）")
    ap.add_argument("--only-audio", action="store_true", help="只测音频解码")
    args = ap.parse_args()

    if args.tiny:
        args.width, args.height, args.length = 256, 256, 22

    # 与生成侧同一环境变量开关对齐
    if os.environ.get("DIRECTOR_DECODE_AUDIO", "").strip().lower() in {"0", "false", "no", "off"}:
        args.no_audio = True

    log(f"[DIAG] ==== #484 解码卡死隔离诊断 v2 ====")
    log(f"[DIAG] 脚本: {os.path.abspath(__file__)}")
    log(f"[DIAG] ComfyUI root: {_COMFYUI_ROOT}")
    log(f"[DIAG] Python: {sys.version.split()[0]}")
    log(f"[DIAG] 本次参数: {args.width}x{args.height} @ {args.length} 帧"
        f" no_audio={args.no_audio} no_tile={args.no_tile} tiny={args.tiny}")

    device = comfy.model_management.get_torch_device()
    try:
        free_mib = comfy.model_management.get_free_memory(device) / 2 ** 20
    except Exception:  # noqa: BLE001
        free_mib = -1.0
    log(f"[DIAG] device: {device} (free VRAM ≈ {free_mib:.0f} MiB)")
    if torch.cuda.is_available():
        log(f"[DIAG] GPU: {torch.cuda.get_device_name(0)} "
            f"/ {torch.cuda.get_device_properties(0).total_memory / 2 ** 30:.1f} GiB")
    else:
        log("[DIAG] ⚠ CUDA 不可用 —— 确认是否用 ComfyUI 的 venv python 运行！")

    # ---- 形状计算（与官方 nodes_minimax_h3.temporal_shape 完全一致）----
    fps, audio_latent_fps = 24, 40

    def _align_frame_count(n: int) -> int:
        while n % 17 != 5:
            n += 1
        return n

    def _video_latent_t(frame_count: int) -> int:
        return 2 if frame_count <= 5 else ((frame_count - 5) // 17) * 5 + 2

    frame_count = _align_frame_count(max(5, args.length))
    v_t = _video_latent_t(frame_count)
    a_t = round((frame_count / fps) * audio_latent_fps)
    v_shape = [1, 24, v_t, args.height // 16, args.width // 16]
    a_shape = [1, 32, 2, a_t]
    log(f"[DIAG] 目标: frames={frame_count} ({frame_count / fps:.2f}s)")
    log(f"[DIAG] video latent: {_format_shape(v_shape)}  (原始帧 {args.height}x{args.width})")
    log(f"[DIAG] audio latent: {_format_shape(a_shape)}")

    # ---- 加载 VAE ----
    video_vae = None if args.only_audio else _load_vae(args.video_vae, "video")
    _mem_summary("VAE 加载后")
    audio_vae = None if args.no_audio else _load_vae(args.audio_vae, "audio")
    _mem_summary("两 VAE 加载后")

    # ---- 合成零 latent（真实 pipeline 也是零 NestedTensor + 采样后同形状）----
    dev = comfy.model_management.intermediate_device()
    v_latent = torch.zeros(v_shape, device=dev, dtype=torch.bfloat16)
    a_latent = torch.zeros(a_shape, device=dev, dtype=torch.bfloat16)

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    # ---- 视频解码（插桩 + inference_mode）----
    video_ok = True
    if not args.only_audio:
        video_ok = _run_video_decode(video_vae, v_latent, args)

    # ---- 音频解码 ----
    if audio_vae is not None:
        _run_audio_decode(audio_vae, a_latent, args)

    if args.only_audio:
        log("[DIAG] ---- 仅音频测试结束 ----")
    elif video_ok:
        log("[DIAG] ---- 全部 DONE：完全驻留下解码未卡死，问题在 SPA 侧（aimdo/动态驻留/上游）----")
    else:
        log("[DIAG] ---- 视频解码未完成（见上方 error/hang）----")
    return 0


if __name__ == "__main__":
    sys.exit(main())
