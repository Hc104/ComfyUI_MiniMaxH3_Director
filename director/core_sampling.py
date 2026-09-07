"""Single-stage sampling for MiniMax H3 (SigmaShift + KSampler).

#91：ComfyUI 的 /interrupt 只是把 ``model_management.interrupt_processing`` 标志置位，
真正中断依赖运行中的采样循环去检查该标志。官方 KSampler 依赖 ``comfy.ops`` 在模型
前向里检查（``run_every_op``），但 H3 采样路径是否命中取决于模型/量化包装，不能保证；
这里显式复制 ``common_ksampler`` 的核心逻辑，并在采样前 + 每步 callback 检查中断标志，
保证「SPA 取消 → /interrupt」真正停下 H3 生成。
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.core_sampling")

PhaseCallback = Callable[[str, float, float], None]


def _unpack_node_output(out):
    if hasattr(out, "args"):
        args = out.args
        if args:
            return args
    if isinstance(out, (tuple, list)):
        return out
    raise RuntimeError(f"Unexpected node output type: {type(out)!r}")


def _raise_if_interrupted() -> None:
    """ComfyUI /interrupt 支持：中断标志置位时抛 InterruptProcessingException。

    测试环境无 comfy → 忽略（ImportError/AttributeError 都是 Exception，被吞掉）；
    生成路径一定有 comfy，此时若标志置位会抛 BaseException 子类，向上传播终止整次生成。
    """
    try:
        from comfy import model_management as mm

        mm.throw_exception_if_processing_interrupted()
    except Exception:
        pass


def sample_single_stage(
    *,
    model,
    positive,
    negative,
    latent,
    seed: int,
    cfg: float,
    steps: int,
    sampler_name: str,
    scheduler: str,
    shift_video: float = 12.0,
    shift_audio: float = 3.0,
    on_phase: PhaseCallback | None = None,
):
    from comfy import model_management as mm
    from comfy import sample as comfy_sample
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3SigmaShift

    def notify(phase: str, value: float, max_value: float = 1) -> None:
        if on_phase:
            on_phase(phase, value, max_value)

    # 采样前先检查一次中断：让取消在进入昂贵的 SigmaShift / 采样前就能生效。
    _raise_if_interrupted()

    notify("sample", 0)
    shifted = MiniMaxH3SigmaShift.execute(model, float(shift_video), float(shift_audio))
    model_shifted = _unpack_node_output(shifted)[0]

    neg = negative if negative else []

    # NOTE: MiniMax H3 的 latent 永远是 zeros NestedTensor（见官方 _empty_av_latent）。
    # 真正的首帧硬锁定靠 first_frame → minimax_keyframes 条件（re-injected every step,
    # never denoised）。不要在此注入内容 latent —— 官方源码证明这条路是死路。
    #
    # 复制 nodes.common_ksampler 的核心（fix_empty_latent_channels + prepare_noise +
    # comfy.sample.sample + callback），差异只在于 callback 里显式检查中断标志：
    # KSampler 节点本版不暴露 callback 参数，直接改 call 无法加步进回调。
    latent_image = latent["samples"]
    latent_image = comfy_sample.fix_empty_latent_channels(
        model_shifted,
        latent_image,
        latent.get("downscale_ratio_spacial", None),
        latent.get("downscale_ratio_temporal", None),
    )
    batch_inds = latent["batch_index"] if "batch_index" in latent else None
    noise = comfy_sample.prepare_noise(latent_image, seed, batch_inds)
    noise_mask = latent.get("noise_mask")

    try:
        from comfy import latent_preview

        preview_cb = latent_preview.prepare_callback(model_shifted, int(steps))
    except Exception:
        preview_cb = None

    def _step_callback(step, x0, x, total_steps) -> None:
        # V1.10-E 真实采样步进：向导演进度上报当前 step / total_steps（前端显示
        # 「Sampling · Step 18/30」）。在中断检查前上报，取消时也能看到最后一步。
        notify("sample", step + 1, max(total_steps, 1))
        # 每步检查：ComfyUI /interrupt 置位后立刻抛 InterruptProcessingException 终止采样。
        # throw_exception_if_processing_interrupted 会先清标志再抛，避免标志残留影响下一次生成。
        mm.throw_exception_if_processing_interrupted()
        if preview_cb is not None:
            try:
                preview_cb(step, x0, x, total_steps)
            except Exception:
                pass

    samples = comfy_sample.sample(
        model_shifted,
        noise,
        int(steps),
        float(cfg),
        sampler_name,
        scheduler,
        positive,
        neg,
        latent_image,
        denoise=1.0,
        noise_mask=noise_mask,
        callback=_step_callback,
        disable_pbar=True,
        seed=seed,
    )
    out = dict(latent)
    out.pop("downscale_ratio_spacial", None)
    out.pop("downscale_ratio_temporal", None)
    out["samples"] = samples
    notify("sample", 1)
    return out
