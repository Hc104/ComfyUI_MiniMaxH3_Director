"""MiniMax H3 conditioning — delegates to ComfyUI official MiniMaxH3 nodes."""

from __future__ import annotations

from ..lib.ref_images import MAX_REFERENCE_IMAGES, REF_IMAGE_KEY_PREFIX, flatten_reference_kwargs
from ..lib.task_modes import TASK_DESCRIPTIONS, infer_task

# MiniMax H3 DiT patch_size=(1,2,2)：latent 宽高必须为偶数（/16 后），即
# 生成宽高必须是 32 的倍数。官方节点 width/height 输入也是 step=32。
# 主 latent 有 pad_to_patch_size 兜底，但 keyframe（ImageToVideo first_frame）
# 与 reference latent 不做 padding —— 奇数宽直接崩。此处统一对齐，杜绝崩溃。
H3_ALIGN = 32


def _h3_align_dim(value: int) -> int:
    """Round *value* up/down to the nearest multiple of 32 (half-up, keep ≥32).

    与前端 JS ``Math.round(v / 32) * 32`` 保持一致（half-up），避免两端算出
    不同的对齐值导致 UI 显示与实际生成尺寸不一致。
    """
    v = int(value)
    if v <= 0:
        return H3_ALIGN
    return max(H3_ALIGN, (v + H3_ALIGN // 2) // H3_ALIGN * H3_ALIGN)


def _shared_optional_inputs() -> dict:
    return {
        "first_frame": (
            "IMAGE",
            {"tooltip": "Optional first keyframe (i2v / fl2v)."},
        ),
        "last_frame": (
            "IMAGE",
            {"tooltip": "Optional last keyframe (fl2v)."},
        ),
        **{
            f"{REF_IMAGE_KEY_PREFIX}{index}": (
                "IMAGE",
                {
                    "tooltip": (
                        f"Reference image for <Picture {index + 1}> in prompt (r2v). "
                        "Native aspect; H3 ref_image_size applies at encode time."
                    ),
                },
            )
            for index in range(MAX_REFERENCE_IMAGES)
        },
        "ref_image_size": (
            ["match", "max"],
            {
                "default": "match",
                "tooltip": "Reference image sizing for MiniMaxH3ReferenceToVideo.",
            },
        ),
    }


def _load_minimax_nodes():
    try:
        from comfy_extras.nodes_minimax_h3 import (
            MiniMaxH3ImageToVideo,
            MiniMaxH3ReferenceToVideo,
        )
    except ImportError as exc:
        raise RuntimeError(
            "MiniMaxH3Director requires ComfyUI official MiniMax H3 nodes "
            "(comfy_extras.nodes_minimax_h3). Upgrade to ComfyUI with PR #15224 merged."
        ) from exc
    return MiniMaxH3ImageToVideo, MiniMaxH3ReferenceToVideo


def _unpack_positive_latent(out):
    args = None
    if hasattr(out, "args"):
        args = out.args
    elif isinstance(out, (tuple, list)):
        args = out
    if args and len(args) >= 2:
        return args[0], args[1]
    raise RuntimeError(f"MiniMax H3 conditioning returned unexpected output: {type(out)!r}")


def _reference_images_dict_from_kwargs(kwargs: dict) -> dict | None:
    nested = kwargs.get("ref_images")
    if isinstance(nested, dict) and nested:
        out = {k: v for k, v in nested.items() if v is not None}
        return out or None

    refs = flatten_reference_kwargs(kwargs)
    out: dict[str, object] = {}
    for key, value in refs.items():
        if value is None:
            continue
        idx = key.removeprefix(REF_IMAGE_KEY_PREFIX)
        out[f"ref_image_{idx}"] = value
    return out or None


def _reference_videos_dict(ref_videos: dict | None) -> dict | None:
    if not ref_videos:
        return None
    out = {k: v for k, v in ref_videos.items() if v is not None}
    return out or None


def _task_hint(task_key: str, ref_images, ref_videos, first_frame=None, last_frame=None) -> str:
    ref_image_count = len(ref_images or {})
    ref_video_count = len(ref_videos or {})
    mode = infer_task(ref_image_count, ref_video_count)
    has_ff = first_frame is not None
    has_lf = last_frame is not None
    if has_ff or has_lf:
        if has_ff and has_lf:
            mode_label = "First+Last Frame (fl2v keyframe)"
        elif has_ff:
            mode_label = "First Frame (i2v keyframe)"
        else:
            mode_label = "Last Frame (keyframe)"
        hint = f"{task_key or mode.value} — {mode_label} (MiniMax H3)"
    else:
        hint = f"{task_key or mode.value} — {TASK_DESCRIPTIONS[mode]} (MiniMax H3)"
    if ref_image_count or ref_video_count:
        hint += f" (~{ref_image_count} ref image(s), {ref_video_count} ref video(s))"
    return hint


def run_minimax_conditioning(
    *,
    clip,
    vae,
    audio_vae,
    prompt: str,
    width: int,
    height: int,
    length: int,
    task_key: str,
    first_frame=None,
    last_frame=None,
    ref_images=None,
    ref_videos=None,
    ref_video_audios=None,
    ref_audios=None,
    ref_image_size: str = "match",
    **kwargs,
):
    """Build positive conditioning + AV latent via official MiniMax H3 nodes.

    MiniMax H3's first_frame / last_frame are passed straight through to
    ``MiniMaxH3ImageToVideo`` where they become ``minimax_keyframes`` — the
    official hard-lock (re-injected every step, never denoised). This is the
    correct seam mechanism; the AV latent itself is always zeros NestedTensor.
    """
    # H3 硬性要求：宽高为 32 的倍数。keyframe 路径会精确按 width×height
    # resize 首帧再编码，奇数宽会直接让 patchify_video reshape 崩溃。
    # 在进入官方节点前统一对齐（最终防线，无论前端/其它调用传什么都安全）。
    width = _h3_align_dim(width)
    height = _h3_align_dim(height)

    MiniMaxH3ImageToVideo, MiniMaxH3ReferenceToVideo = _load_minimax_nodes()

    ref_images = ref_images or _reference_images_dict_from_kwargs(kwargs)
    ref_videos = _reference_videos_dict(ref_videos)

    use_reference = (
        task_key in {"r2v", "v2v", "rv2v"}
        or ref_images
        or ref_videos
        or ref_audios
        or ref_video_audios
    )

    if use_reference:
        if audio_vae is None:
            raise ValueError("MiniMax H3 r2v/v2v/rv2v / reference conditioning requires audio_vae.")
        out = MiniMaxH3ReferenceToVideo.execute(
            clip,
            vae,
            audio_vae,
            prompt,
            width,
            height,
            length,
            ref_image_size,
            ref_images=ref_images,
            ref_videos=ref_videos,
            ref_video_audios=ref_video_audios,
            ref_audios=ref_audios,
        )
    else:
        out = MiniMaxH3ImageToVideo.execute(
            clip,
            vae,
            prompt,
            width,
            height,
            length,
            first_frame=first_frame,
            last_frame=last_frame,
        )

    positive, latent = _unpack_positive_latent(out)
    hint = _task_hint(task_key, ref_images, ref_videos, first_frame, last_frame)
    return positive, [], latent, hint


class MiniMaxH3DirectorConditioning:
    """Thin wrapper around official MiniMax H3 conditioning (positive + latent)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "width": ("INT", {"default": 864, "min": 32, "max": 8192, "step": 32}),
                "height": ("INT", {"default": 480, "min": 32, "max": 8192, "step": 32}),
                "length": ("INT", {"default": 124, "min": 5, "max": 3600, "step": 17}),
            },
            "optional": {
                "audio_vae": ("VAE", {"tooltip": "Required for r2v / v2v / rv2v / reference video+audio."}),
                **_shared_optional_inputs(),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT")
    RETURN_NAMES = ("positive", "latent")
    FUNCTION = "apply"
    CATEGORY = "MiniMaxH3"

    def apply(self, clip, vae, prompt, width, height, length, audio_vae=None, **kwargs):
        positive, _, latent, _ = run_minimax_conditioning(
            clip=clip,
            vae=vae,
            audio_vae=audio_vae,
            prompt=prompt,
            width=width,
            height=height,
            length=length,
            task_key="r2v" if kwargs.get("ref_images") or any(
                k.startswith(REF_IMAGE_KEY_PREFIX) for k in kwargs
            ) else "t2v",
            **kwargs,
        )
        return positive, latent


class MiniMaxH3DirectorPlannerConditioning:
    """Official MiniMax H3 conditioning plus task_mode string for planning UIs."""

    @classmethod
    def INPUT_TYPES(cls):
        base = MiniMaxH3DirectorConditioning.INPUT_TYPES()
        return base

    RETURN_TYPES = ("CONDITIONING", "LATENT", "STRING")
    RETURN_NAMES = ("positive", "latent", "task_mode")
    FUNCTION = "apply"
    CATEGORY = "MiniMaxH3"

    def apply(self, clip, vae, prompt, width, height, length, audio_vae=None, **kwargs):
        positive, _, latent, hint = run_minimax_conditioning(
            clip=clip,
            vae=vae,
            audio_vae=audio_vae,
            prompt=prompt,
            width=width,
            height=height,
            length=length,
            task_key="r2v" if kwargs.get("ref_images") or any(
                k.startswith(REF_IMAGE_KEY_PREFIX) for k in kwargs
            ) else "t2v",
            **kwargs,
        )
        return positive, latent, hint
