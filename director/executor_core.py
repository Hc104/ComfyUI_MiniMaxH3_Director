"""Run MiniMax H3 Director segments through the official ComfyUI core pipeline."""

from __future__ import annotations

import logging
import os
from typing import Any

import torch

from ..lib.image_prep import fit_canvas, fit_video_long_edge
from ..lib.task_modes import SUPPORTED_TASK_KEYS
from ..nodes.conditioning import run_minimax_conditioning
from .core_sampling import sample_single_stage
from .frame_align import minimax_align_frame_count, pad_or_trim_frames
from .audio_export import (
    AUDIO_MODE_GENERATE,
    AUDIO_MODE_MUTE,
    AUDIO_MODE_SOURCE,
    empty_audio_dict,
    resolve_audio_mode,
)
from .segment_runtime import (
    frames_label,
    resolve_segment_raw_clip,
    segment_passthrough_chunk,
    tensor_frame_to_jpeg_b64,
)
from .plan import (
    build_scene_groups,
    DirectorPlan,
    SceneGroupPlan,
    plan_summary,
    prepare_segment_clip,
    ref_audios_to_dict,
    ref_videos_to_dict,
    reference_video_for_segment,
    refs_to_kwargs_for_context,
    reinforce_r2v_prompt,
    reinforce_rv2v_prompt,
    reinforce_v2v_prompt,
)
from .progress import report_director_finish, report_director_progress, report_director_segment_preview
from .segment_status import (
    clear_segment_runtime_state,
    set_segment_runtime_state,
)
from .vae_residency import ensure_vae_resident
from .segment_cache import (
    load_segment_cache,
    load_segment_cache_audio,
    save_segment_cache,
)
from .segment_continuity import (
    concat_continuous_chunks,
    is_continuity_active,
    resolve_prev_segment_output,
)
from .stream_export import (
    concat_audio_dicts,
    decode_video_tensor,
    estimated_merged_bytes,
    ffmpeg_bin,
    make_stream_dir,
    merge_segment_mp4s,
    resolve_movie_output_path,
    resolve_scene_run_paths,
    save_segment_mp4,
)
from .vram_cleanup import cleanup_segment_vram

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.core")


def _unpack_node_output(out):
    if hasattr(out, "args"):
        args = out.args
        if args:
            return args
    if isinstance(out, (tuple, list)):
        return out
    raise RuntimeError(f"Unexpected node output: {type(out)!r}")


def _sync_cuda() -> None:
    """#484 插桩用：等 GPU 队列排空，保证 [DECODE] 打印的耗时是真实墙钟时间。"""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass


def _env_flag(name: str, default: bool = True) -> bool:
    """#484 诊断开关：读取布尔环境变量，'0'/'false'/'no' 视为 False。"""
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _raise_if_interrupted() -> None:
    """ComfyUI /interrupt 支持（#91）：中断标志置位时抛 InterruptProcessingException。

    官方 KSampler 的中断依赖 comfy.ops 在模型前向里检查，但 H3 采样/编码阶段不一定命中；
    这里在段边界、采样前、场景合并等耗时点显式检查，保证「SPA 取消 → /interrupt」能停下生成。
    throw_exception_if_processing_interrupted 会先清标志再抛（BaseException 子类），
    测试环境无 comfy 时 ImportError/AttributeError 是 Exception，被吞掉不影响行为。
    """
    try:
        from comfy import model_management as mm

        mm.throw_exception_if_processing_interrupted()
    except Exception:
        pass


def _r2v_handoff_prompt(
    prompt: str,
    pic_roles: list[tuple[int, str, str]],
    *,
    continuing: bool = True,
) -> str:
    """r2v 提示词：状态继承方案（叙事连续 > 像素连续）。

    pic_roles: (1-based <Picture N>, 图片角色说明, 保持语义) 列表——
    由调用方按实际注入的 ref_image 槽位顺序生成，保证提示词与真实参考图一一对应。
    continuing=True：自动续接上段的「新镜头接续」框架（尾帧锚定）；
    continuing=False：首镜或仅资产注入时，只强调保持主体/场景一致。
    不再要求首帧像素级复刻——ref2va 无 first_frame 硬锁，且锁首帧会把
    下一镜焊死在上一镜的姿势/机位上，阻碍叙事动作推进。
    """
    text = (prompt or "").strip()
    if "<video" in text.lower() or "<video " in text.lower():
        return text  # 已含 <Video N> 标签，用户已在指挥参考视频
    if continuing:
        prefix = (
            "新镜头接续：这是同一角色、同一场景、同一时间的新镜头，但画面与动作都是新的——"
            "人物必须做出清晰可见的新动作，机位和景别可以自由切换，镜头重新取景。"
        )
    else:
        prefix = "本镜头保持以下参考图的画面主体与场景一致："
    for num, role, detail in pic_roles:
        prefix += f" <Picture {num}> 是{role}，{detail}。"
    if continuing:
        prefix += " 新镜头必须有清晰可见的新动作与情节推进，画面不要与上一镜头雷同。"
    if not text:
        return prefix
    return f"{prefix} 新镜头内容：{text}"


def _latent_meta(x):
    """稳健提取 latent 的 (shape, device, dtype)。

    ComfyUI 的 latent 标准格式是 dict（``{"samples": tensor, ...}``，
    ``LTXVSeparateAVLatent`` 输出的 video/audio latent 都是 dict）；个别路径
    也可能直接传裸 tensor。诊断打印统一走这里，避免 ``'dict' object has no
    attribute 'shape'``（#486 实测修复）。
    """
    samples = x.get("samples") if isinstance(x, dict) else x
    if not hasattr(samples, "shape"):
        return None, None, None
    return tuple(samples.shape), getattr(samples, "device", None), getattr(samples, "dtype", None)


def _decode_av_latent(samples, vae, audio_vae, *, decode_audio: bool = True):
    """#484 诊断插桩 + 修复：逐步打印视频/音频解码耗时与形状，定位「卡解码」卡点。

    日志形如 [DECODE] ...；用户复现后看最后一条 [DECODE] 行即知卡在哪一步。

    修复：aimdo 开启时 ComfyUI 把所有 VAE patcher 换成 ModelPatcherDynamic，
    视频 VAE 解码走动态按需换页 → 4-7 分钟「卡解码」。解码前把两个 VAE 都
    切到非动态全量驻留（vae_residency.ensure_vae_resident），让解码走
    force_full_load 普通路径（隔离脚本实测 14s/峰值 5.3GB）。
    """
    import time

    import torch

    from comfy_extras.nodes_lt import LTXVSeparateAVLatent
    from nodes import VAEDecode

    # #484 修复：先把两个 VAE 切到非动态全量驻留（幂等；aimdo 未开启时无操作）
    _t_swap = time.time()
    swapped_video = ensure_vae_resident(vae)
    swapped_audio = ensure_vae_resident(audio_vae) if audio_vae is not None else False
    if swapped_video or swapped_audio:
        print(
            f"[DECODE] VAE 非动态化完成 t={time.time() - _t_swap:.1f}s "
            f"video_swapped={swapped_video} audio_swapped={swapped_audio}"
        )

    _t_split = time.time()
    sep = LTXVSeparateAVLatent.execute(samples)
    video_latent, audio_latent = _unpack_node_output(sep)[:2]
    _sync_cuda()
    v_shape, v_dev, v_dtype = _latent_meta(video_latent)
    a_shape, _, _ = _latent_meta(audio_latent)
    print(
        f"[DECODE] split AV latent t={time.time() - _t_split:.1f}s "
        f"video={v_shape} audio={a_shape} "
        f"device={v_dev} dtype={v_dtype}"
    )

    _t_video = time.time()
    images, = VAEDecode().decode(vae, video_latent)
    _sync_cuda()
    print(
        f"[DECODE] video VAE decode done t={time.time() - _t_video:.1f}s "
        f"images={tuple(images.shape)} device={images.device}"
    )

    if not decode_audio or audio_vae is None:
        return images, empty_audio_dict()
    try:
        from comfy_extras.nodes_audio import VAEDecodeAudio
    except ImportError:
        from comfy_extras.nodes_lt import VAEDecodeAudio  # type: ignore

    _t_audio = time.time()
    audio_out = VAEDecodeAudio.execute(audio_vae, audio_latent)
    audio = _unpack_node_output(audio_out)[0]
    _sync_cuda()
    wave = audio.get("waveform") if isinstance(audio, dict) else None
    print(
        f"[DECODE] audio VAE decode done t={time.time() - _t_audio:.1f}s "
        f"waveform={tuple(wave.shape) if wave is not None else None} "
        f"sample_rate={audio.get('sample_rate') if isinstance(audio, dict) else None}"
    )
    return images, audio


def _ref_tensor_from_seg_refs(refs, index: int) -> torch.Tensor | None:
    for ref in refs or []:
        if int(getattr(ref, "index", -1)) == index and ref.tensor is not None:
            t = ref.tensor
            if t.shape[0] > 0:
                return t[:1]
    return None


def _gray_laplacian_var(gray: torch.Tensor) -> float:
    """灰度图 Laplacian 方差（锐度/清晰度度量）。gray: [H, W] float [0,1]."""
    import torch.nn.functional as F

    if gray.ndim == 2:
        gray = gray.unsqueeze(0).unsqueeze(0)  # [1,1,H,W]
    kernel = torch.tensor(
        [[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]],
        dtype=gray.dtype, device=gray.device,
    ).view(1, 1, 3, 3)
    lap = F.conv2d(gray, kernel, padding=1)
    return float(lap.var().item())


def _pick_handoff_anchor(
    frames: torch.Tensor,
    *,
    window: int = 5,
    min_luma: float = 0.04,
    max_luma: float = 0.97,
) -> tuple[torch.Tensor, str]:
    """智能尾帧选择（待办④）：从上一段末尾 window 帧中挑最适合作续接锚点的一帧。

    仅用于「参考图」路径（r2v 图片锚点 ref_image_0、fl2v 参考图续接），
    **不用于 first_frame 硬锁**——硬锁要求本段首帧=上一段真正末帧（像素连续），
    选别的帧会断链。

    保守策略：默认仍取最后一帧（保持既有已验证行为）；仅当末帧质量明显较差
    ——锐度显著低于窗口内最佳帧（>35%），或过暗/过曝——才回退到窗口内最佳帧。
    评分 = 灰度 Laplacian 方差（锐度）× 亮度惩罚（0.5，过暗/过曝）。
    frames: [N, H, W, C] float [0,1]。返回 (选中的单帧 [1,H,W,C], 选择说明)。
    """
    if frames is None:
        return frames, ""
    n = int(frames.shape[0])
    if n <= 1:
        return frames[-1:], ""
    start = max(0, n - max(1, int(window)))
    cand = frames[start:].float()
    best_i = int(cand.shape[0]) - 1
    best_score = -1.0
    scores: list[float] = []
    for i in range(int(cand.shape[0])):
        f = cand[i]
        gray = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
        lap_var = _gray_laplacian_var(gray)
        mean = float(gray.mean().item())
        if mean < min_luma or mean > max_luma:
            lap_var *= 0.5
        scores.append(lap_var)
        if lap_var > best_score:
            best_score = lap_var
            best_i = i
    last_score = scores[-1]
    # 保守：仅当末帧质量明显低于窗口内最佳帧（>35%）才回退选帧。
    # 默认取末帧（行为与旧版一致），此时说明为空，诊断不额外提示。
    if best_score > last_score * 1.35:
        rel = int(best_i) + 1
        return (
            cand[best_i:best_i + 1].clone(),
            f"; 智能选帧: 末{len(scores)}帧→第{rel}帧(末帧较模糊)",
        )
    return cand[-1:].clone(), ""


def build_shot_asset_items(
    cast_assets,
    location_asset,
    extra_assets,
    tag_assets,
):
    """组装一段 Shot 的资产图注入列表（P0-1 提取为纯函数，可单测）。

    铁律①：一个 Shot 只能拿到自己显式/本镜 @ 引用的资产，不能全场景扩散。
    本函数只做「过滤 + 角色映射」，per-shot 边界由 gen_timeline 的
    SegmentPlan.extra_assets 保证（见 _seg_prop_ids / _seg_style_ids）。

    返回 [(role, name, tensor), ...]，tensor 为 None 的条目自动跳过
    （沿用旧 executor 行为：无图资产不进 ref）。role 取值与旧 inline 完全一致：
      角色资产图 / 场景资产图 / 道具资产图 / 风格参考图。
    """
    asset_items: list[tuple[str, str, torch.Tensor]] = []
    for _ca in cast_assets or []:
        if _ca.tensor is not None:
            asset_items.append(("角色资产图", _ca.name or "角色", _ca.tensor))
    _la = location_asset
    if _la is not None and _la.tensor is not None:
        asset_items.append(("场景资产图", _la.name or "场景", _la.tensor))
    for _ea in extra_assets or []:
        if _ea.tensor is None:
            continue
        if _ea.kind == "prop":
            asset_items.append(("道具资产图", _ea.name or "道具", _ea.tensor))
        elif _ea.kind == "style":
            asset_items.append(("风格参考图", _ea.name or "风格", _ea.tensor))
        else:
            asset_items.append(("场景资产图", _ea.name or "附加", _ea.tensor))
    for _ta in tag_assets or []:
        if _ta.tensor is None:
            continue
        if _ta.kind == "cast":
            asset_items.append(("角色资产图", _ta.name or "角色", _ta.tensor))
        elif _ta.kind == "prop":
            asset_items.append(("道具资产图", _ta.name or "道具", _ta.tensor))
        elif _ta.kind == "style":
            asset_items.append(("风格参考图", _ta.name or "风格", _ta.tensor))
        else:
            asset_items.append(("场景资产图", _ta.name or "场景", _ta.tensor))
    return asset_items


def _build_minimax_inputs(
    plan: DirectorPlan,
    seg,
    *,
    clip_frames: torch.Tensor | None,
    ctx_w: int,
    ctx_h: int,
    prev_tail: torch.Tensor | None,
    auto_handoff: bool = False,
    allow_reference: bool = False,
    handoff_mode: str = "image",
    r2v_auto_handoff: bool = False,
    inherit_ref_tensors: list[torch.Tensor] | None = None,
    asset_ref_tensors: list[torch.Tensor] | None = None,
    anchor_frame: torch.Tensor | None = None,
):
    """Map segment task + refs to MiniMax ImageToVideo / ReferenceToVideo inputs."""
    task_key = seg.task_key
    first_frame = None
    last_frame = None
    ref_images = None
    ref_videos = None
    ref_audios = None
    # 智能尾帧选择（待办④）：参考图路径用 anchor_frame（上段末尾最佳帧）替代固定取末帧；
    # first_frame 硬锁路径（fl2v/i2v）仍用真正末帧保证像素连续，不受影响。
    _tail_anchor = anchor_frame

    if task_key == "fl2v":
        explicit_start = _ref_tensor_from_seg_refs(seg.refs, 0)
        explicit_end = _ref_tensor_from_seg_refs(seg.refs, 1)
        if explicit_start is not None:
            # 手动上传首帧的镜头：使用 first_frame 硬锁定 + last_frame 可选
            first_frame = explicit_start
            if explicit_end is not None:
                last_frame = explicit_end
            elif clip_frames is not None and clip_frames.shape[0] >= 2:
                last_frame = clip_frames[-1:].clone()
        else:
            # 自动交接镜头（无显式首帧）：按 handoff_mode 选择续接方式
            # - "video"：从视频续接 —— ref_video_0 = 上一段整段视频，
            #   模型能看到完整运动轨迹，运动延续最强（更吃显存）
            # - "image"（默认）：参考图续接 —— ref_image_0 = 上段尾帧，
            #   延续场景+运动趋势，比 first_frame 硬锁更连贯
            # - 二者皆不可用时回退 first_frame 硬锁（保留 last_frame 支持）
            if allow_reference and explicit_end is None and handoff_mode == "video":
                if prev_tail is not None and prev_tail.shape[0] >= 5:
                    ref_videos = {"ref_video_0": prev_tail.clone()}
                elif clip_frames is not None and clip_frames.shape[0] >= 5:
                    ref_videos = {"ref_video_0": clip_frames.clone()}
            elif allow_reference and explicit_end is None:
                if prev_tail is not None and prev_tail.shape[0] > 0:
                    _f = _tail_anchor if _tail_anchor is not None else prev_tail[-1:]
                    ref_images = {"ref_image_0": _f.clone()}
                elif clip_frames is not None and clip_frames.shape[0] >= 1:
                    ref_images = {"ref_image_0": clip_frames[-1:].clone()}
            else:
                if prev_tail is not None and prev_tail.shape[0] > 0:
                    first_frame = prev_tail[-1:].clone()
                elif clip_frames is not None and clip_frames.shape[0] >= 1:
                    first_frame = clip_frames[-1:].clone()
                if explicit_end is not None:
                    last_frame = explicit_end
            # ref_videos / ref_images 非 None 时走 ReferenceToVideo；
            # 否则 ref_images 保持 None 走 ImageToVideo 路径（有 first_frame 参数）。
        if first_frame is None:
            first_frame = _ref_tensor_from_seg_refs(seg.refs, 0)
        if last_frame is None:
            last_frame = _ref_tensor_from_seg_refs(seg.refs, 1)
    elif task_key == "i2v":
        if prev_tail is not None and prev_tail.shape[0] > 0:
            first_frame = prev_tail[-1:].clone()
        elif clip_frames is not None and clip_frames.shape[0] > 0:
            first_frame = clip_frames[:1]
        else:
            first_frame = _ref_tensor_from_seg_refs(seg.refs, 0)
    elif task_key == "r2v":
        ref_kwargs = refs_to_kwargs_for_context(task_key, seg.refs)
        ref_images = {}
        for key, tensor in ref_kwargs.items():
            if tensor is None:
                continue
            idx = key.removeprefix("reference_image_")
            ref_images[f"ref_image_{idx}"] = tensor[:1] if tensor.ndim == 4 else tensor
        if not ref_images:
            ref_images = None
        # Prefer multi-slot ref_videos (r2v batch cards); fall back to legacy single meta.
        ref_videos = ref_videos_to_dict(getattr(seg, "ref_videos", None) or [])
        if not ref_videos:
            nframes = max(5, int(getattr(seg, "frame_count", 0) or plan.total_frames or 124))
            ref_video = reference_video_for_segment(plan, seg, num_frames=nframes)
            if ref_video is not None and ref_video.shape[0] > 0:
                ref_videos = {"ref_video_0": ref_video}
        ref_audios = ref_audios_to_dict(getattr(seg, "ref_audios", None) or [])
        # r2v 自动续接（ref2va 模型专用）：该段无显式参考媒体 + 上一段已生成 →
        # 图片锚点方案（不再注入 ref_video，避免模型把上一段视频"再演一遍"）：
        #   ref_image_0 = 上一段尾帧（<Picture 1>，新镜头起点/接缝锚定）
        #   ref_image_1..N = 上一段继承的角色/场景参考图（<Picture 2>…，保持主体一致）
        if r2v_auto_handoff and not ref_images and not ref_videos and not ref_audios:
            if prev_tail is not None and prev_tail.shape[0] >= 5:
                _f = _tail_anchor if _tail_anchor is not None else prev_tail[-1:]
                ref_images = {"ref_image_0": _f.clone()}
                for i, t in enumerate(inherit_ref_tensors or []):
                    ref_images[f"ref_image_{i + 1}"] = t
        # 全局资产注入（Phase B）：角色/场景资产图追加到段自身参考图
        # （与自动续接尾帧）之后，作为人物/场景一致性的"世界锚点"。
        asset_tensors = list(asset_ref_tensors or [])
        if asset_tensors:
            if ref_images is None:
                ref_images = {}
            occupied: set[int] = set()
            for k in ref_images.keys():
                if k.startswith("ref_image_"):
                    try:
                        occupied.add(int(k.rsplit("_", 1)[1]))
                    except ValueError:
                        pass
            next_idx = 0
            while next_idx in occupied:
                next_idx += 1
            for t in asset_tensors:
                ref_images[f"ref_image_{next_idx}"] = t
                next_idx += 1
    elif task_key in {"v2v", "rv2v"}:
        # Bernini-style video edit: each timeline segment's source clip → <Video 1>.
        # rv2v additionally injects 图片1–9 / 音频1–3 as <Picture N> / <Audio J>.
        if clip_frames is None or clip_frames.shape[0] <= 0:
            raise ValueError(
                f"{task_key} segment #{seg.index + 1} has no source frames. "
                "Upload a video in the Director timeline before running."
            )
        ref_videos = {"ref_video_0": clip_frames}
        if task_key == "rv2v":
            # Refs are optional per segment: with refs → <Video 1>+<Picture N>;
            # without refs → same as v2v (source edit only).
            ref_kwargs = refs_to_kwargs_for_context(task_key, seg.refs)
            ref_images = {}
            for key, tensor in ref_kwargs.items():
                if tensor is None:
                    continue
                idx = key.removeprefix("reference_image_")
                ref_images[f"ref_image_{idx}"] = tensor[:1] if tensor.ndim == 4 else tensor
            if not ref_images:
                ref_images = None
            ref_audios = ref_audios_to_dict(getattr(seg, "ref_audios", None) or [])

    return first_frame, last_frame, ref_images, ref_videos, ref_audios


def execute_director_plan_core(
    plan: DirectorPlan,
    *,
    node_id: str | None = None,
    model,
    vae,
    audio_vae,
    clip,
    cfg: float = 1.0,
    seed: int = 0,
    steps: int = 25,
    sampler: str = "res_multistep",
    scheduler: str = "simple",
    shift_video: float = 12.0,
    shift_audio: float = 3.0,
    clear_vram_between_segments: bool = True,
) -> tuple[torch.Tensor, list[torch.Tensor], list[dict[str, Any]], str]:
    """Process every segment with MiniMax H3 conditioning + single-stage sampling."""
    # ⛔ 显存互斥门（V17_PLAN.md §8.5）：TextBackend（Qwen）分析中禁止启动 H3。
    # 用函数内轻量导入：互斥检查失败时只告警不阻断 H3 生产链（Phase 0 原则：
    # Qwen 卸载后 H3 完全不受影响）。
    try:
        from .text_backends import text_backend_active

        if text_backend_active():
            raise RuntimeError(
                "AI 分析模型（Qwen/TextBackend）正在占用 GPU，"
                "请等待分析结束并确认显存释放后再生成视频。"
            )
    except RuntimeError:
        raise
    except Exception:
        log.warning("TextBackend 互斥检查不可用，跳过（不阻断 H3 生成）")

    audio_mode = resolve_audio_mode(plan)
    decode_audio = audio_mode == AUDIO_MODE_GENERATE
    # #484 诊断：环境变量 DIRECTOR_DECODE_AUDIO=0 强制跳过音频 VAE 解码，
    # 用于隔离「音频解码是否卡死」+ 临时绕过（保留显存安全门逻辑不受影响）。
    if _env_flag("DIRECTOR_DECODE_AUDIO", default=True):
        pass
    else:
        decode_audio = False
        audio_mode = AUDIO_MODE_MUTE

    all_segments = plan.segments
    # Strictly honor「选择运行」— never force-sample unselected segments.
    run_indices = plan.run_indices if plan.run_indices is not None else frozenset(range(len(all_segments)))

    run_list = sorted(run_indices)
    seg_total = len(run_list)
    progress_pos = {idx: pos for pos, idx in enumerate(run_list)}
    passthrough_indices: list[int] = []

    output_chunks: list[torch.Tensor] = []
    segment_outputs: list[torch.Tensor] = []
    segment_audios: list[dict[str, Any]] = []
    reports: list[str] = [plan_summary(plan), "", "Execution path: ComfyUI official MiniMax H3"]
    if clear_vram_between_segments:
        reports.append("VRAM: 段间清理显存已开启。")
    if audio_mode == AUDIO_MODE_MUTE:
        reports.append("Audio: muted — skip audio VAE decode, silent AUDIO output.")
    elif audio_mode == AUDIO_MODE_SOURCE:
        reports.append("Audio: source — skip audio VAE decode, use original timeline audio.")
    else:
        reports.append("Audio: generate — decode MiniMax H3 AV latent audio.")
    if plan.run_indices is not None:
        skipped = [i + 1 for i in range(len(all_segments)) if i not in run_indices]
        reports.append(
            f"Run selection: {len(run_list)}/{len(all_segments)} segment(s) "
            f"(indices {[i + 1 for i in run_list]}; skipped {skipped or 'none'})"
        )

    if plan.continuity_enabled:
        reports.append(
            "Segment continuity: ON — 自动交接镜头用 first_frame（官方 minimax_keyframes "
            "硬锁定，每步重注入、永不去噪）衔接上一段尾帧。"
        )
    else:
        reports.append("Segment continuity: OFF — per-segment generation only.")

    completed_outputs: dict[int, torch.Tensor] = {}
    # 状态跟踪（阶段 C）：跨段共享的可变容器。current_state 在本段结束后用
    # seg.state_change 更新，供下一镜拼接提示词前缀。初值取第一段的 state_change
    # 之前为空；用户不填状态时保持空串，行为与旧版一致。
    state_holder: dict[str, str] = {"current": ""}

    # 全部导出时，合并视频在 CPU 内存里组装。输出太大时会裸报
    # DefaultCPUAllocator OOM，没有可操作的提示。这里在生成前用计划数据估算
    # 输出大小：超阈值就改用 float16 累积/合并（内存减半）；float16 也放不下
    # 就立刻抛清晰的中文报错，而不是跑到一半崩掉。
    _merge_f16 = False
    _avail_ram = 0.0
    try:
        import psutil
        _avail_ram = float(psutil.virtual_memory().available)
    except Exception:
        _avail_ram = 0.0
    if plan.export_mode == "all" and _avail_ram > 0:
        _est_f32 = max(1, int(plan.total_frames)) * max(1, int(plan.width)) * max(1, int(plan.height)) * 3 * 4
        _est_f16 = _est_f32 / 2.0
        if _est_f16 > _avail_ram * 0.9:
            raise RuntimeError(
                f"「全部导出」合并视频预计需要约 {_est_f32 / (1024 ** 3):.1f} GB（float32）"
                f" / {_est_f16 / (1024 ** 3):.1f} GB（float16），"
                f"当前可用内存约 {_avail_ram / (1024 ** 3):.1f} GB，放不下。\n"
                "解决办法（任选）：① 导出模式改成「场景导出」（Scene，推荐，按场景流式合并）；"
                "② 减少每段时长（H3 建议 5–10 秒，勿超 15 秒）；"
                "③ 降低画布分辨率；④ 用「选择运行」分批生成再手动拼接。"
            )
        _merge_f16 = _est_f32 > max(8 * (1 << 30), _avail_ram * 0.3)

    # 场景/全片（流式）导出（Movie → Scene → Shot）：
    #   - scene/movie 模式下，按 location 连续性把镜头分成「场景组」；
    #     场景内做曝光/接缝优化后立即编码落盘 SceneNN.mp4（内存峰值 ≈ 单场景），
    #     不再像「全部导出」那样把全片 float32 帧堆在 CPU 内存里一次 torch.cat。
    #   - movie 模式额外用 ffmpeg concat 直拼各场景 mp4 → Movie.mp4（几乎不占内存）。
    _scene_groups: list[SceneGroupPlan] = []
    _scene_paths: list[str] = []
    _movie_path: str | None = None
    _decode_f16 = False
    if plan.export_mode in ("scene", "movie"):
        _scene_groups = build_scene_groups(all_segments, plan.scenes)
        reports.append(
            f"Scene groups: {len(_scene_groups)} scene(s) "
            f"({'/'.join(str(len(g.seg_indices)) for g in _scene_groups) or '—'} shot(s) each)"
        )
        if plan.export_mode == "movie":
            if ffmpeg_bin() is None:
                raise RuntimeError(
                    "「全片导出（Movie）」需要 ffmpeg 合并场景片段，但当前环境找不到 ffmpeg。\n"
                    "解决办法（任选）：① 在系统 PATH 安装 FFmpeg；② `pip install imageio-ffmpeg`；"
                    "③ 改用「场景导出」或「全片导出（内存合并）」。"
                )
        # 场景/全片模式同样按需启用 float16 累积/合并（与「全部导出」同口径）：
        # 场景内合并峰值 ≈ 输入张量 + 输出张量，f16 各减半，避免长场景合并 OOM。
        if _avail_ram > 0:
            _est_f32 = max(1, int(plan.total_frames)) * max(1, int(plan.width)) * max(1, int(plan.height)) * 3 * 4
            if _est_f32 > max(8 * (1 << 30), _avail_ram * 0.3):
                _merge_f16 = True
            # 场景内合并放不下时，流式降级后仍需把 SceneNN.mp4 解码回 tensor 供节点
            # 输出；大场景用 f16 解码，尽可能装下（装不下再占位）。
            _decode_f16 = _est_f32 > max(8 * (1 << 30), _avail_ram * 0.3)
        # 场景 mp4 / Movie.mp4 的落盘路径（输出目录，共用运行序号）。
        # Scene Manager：显式场景用其名字命名文件（Director_Scene01_水晶森林.mp4）。
        _scene_paths, _movie_path = resolve_scene_run_paths(
            plan.width, plan.height, len(_scene_groups),
            scene_names=[g.scene.name if (g.scene and g.scene.name) else "" for g in _scene_groups],
        )
        if plan.export_mode == "movie" and _avail_ram > 0:
            _est_f16 = _est_f32 / 2.0
            if _est_f16 > _avail_ram * 0.9:
                reports.append(
                    f"Movie: 合并后解码回 tensor 输出预计需 {_est_f32 / (1024 ** 3):.1f} GB"
                    f"（f32）/ {_est_f16 / (1024 ** 3):.1f} GB（f16），内存紧张；"
                    "Movie.mp4 会照常落盘，若解码失败将降级为占位输出。"
                )

    def _coerce_chunk(t: torch.Tensor) -> torch.Tensor:
        """统一把解码帧转成 CPU 上的合并 dtype（float32 / 自适应 float16）。"""
        t = t.detach().cpu()
        return t.half() if _merge_f16 else t.float()

    def _run_one_segment(seg, *, progress_index: int) -> tuple[torch.Tensor, dict[str, Any] | None]:
        # 段开始先检查中断：取消已点、标志置位时直接终止，不再进入本段昂贵的采样。
        _raise_if_interrupted()
        if seg.task_key not in SUPPORTED_TASK_KEYS:
            raise ValueError(
                f"Task '{seg.task_key}' is not supported on MiniMax H3 Director. "
                f"Supported: {', '.join(sorted(SUPPORTED_TASK_KEYS))}."
            )

        meta = {
            "frames_label": frames_label(seg),
            "task_key": seg.task_key,
            "timeline_segment_index": seg.index,
            "timeline_segment_total": len(all_segments),
        }
        # 镜头状态灯（UI 2.0 第五优先级）：段开始标记「生成中」。
        set_segment_runtime_state(node_id, seg.index, "running")

        report_director_progress(
            node_id, segment_index=progress_index, segment_total=seg_total,
            phase="prepare", phase_value=0, phase_max=1, **meta,
        )

        target_len = max(1, int(seg.frame_count or plan.total_frames or 124))
        raw_clip = resolve_segment_raw_clip(plan, seg)

        if seg.source_clip is not None:
            body_raw = seg.source_clip
            target_len = max(target_len, int(body_raw.shape[0]))
        else:
            body_raw = raw_clip[:target_len] if int(raw_clip.shape[0]) > target_len else raw_clip

        if body_raw is not None and body_raw.shape[0] > 0:
            if plan.output_mode == "fixed":
                clip_frames = fit_canvas(body_raw, plan.width, plan.height)
            else:
                clip_frames = fit_video_long_edge(body_raw, plan.ref_max_size)
        else:
            clip_frames = None

        num_frames = minimax_align_frame_count(target_len)
        if clip_frames is not None:
            clip_frames, _ = prepare_segment_clip(clip_frames, num_frames)

        prev_tail = None
        # 架构固底④：continuity_mode=="none" → 本镜独立，不自动续接上段（per-shot 覆盖全局开关）。
        _seg_cont = str(getattr(seg, "continuity_mode", "") or "").strip().lower()
        if _seg_cont not in ("auto", "none", "ref2va", "fl2va"):
            _seg_cont = "auto"
        # r2v 自动续接（ref2va 模型专用）：段无显式参考媒体时，用上一段尾帧 + 继承
        # 上一段角色/场景参考图续接（图片锚点方案，不注入整段视频）。
        # 独立于 continuityEnabled（前端对非 fl2v 模式会清零该字段）。
        _auto_cont = (
            bool(getattr(plan, "r2v_auto_handoff", False))
            and seg.index > 0
            and _seg_cont != "none"
        )
        r2v_handoff = seg.task_key == "r2v" and _auto_cont
        # 阶段 D：fl2v 段混入 r2v 批时，同样按「自动续接上段」取上段尾帧作首帧（首帧硬锁）。
        # 有显式首帧的 fl2v 段除外（用户上传首帧优先）。
        fl2v_handoff = (
            seg.task_key == "fl2v" and _auto_cont
            and _ref_tensor_from_seg_refs(seg.refs, 0) is None
        )
        inherit_ref_tensors: list[torch.Tensor] = []
        # 全局资产（Phase B）：本段注入的角色/场景资产图，形如 (角色说明, 资产名, tensor)
        asset_items: list[tuple[str, str, torch.Tensor]] = []
        if r2v_handoff:
            prev_tail = resolve_prev_segment_output(
                plan, all_segments, seg.index, completed_outputs, node_id,
                allow_without_continuity=True,
            )
            # 继承上一段 r2v 段的参考图（角色/场景资产）作为稳定锚点。
            prev_seg = all_segments[seg.index - 1] if seg.index > 0 else None
            if prev_seg is not None and prev_seg.task_key == "r2v":
                try:
                    prev_kwargs = refs_to_kwargs_for_context("r2v", prev_seg.refs)
                    for k in sorted(prev_kwargs.keys()):
                        t = prev_kwargs[k]
                        if t is None:
                            continue
                        inherit_ref_tensors.append(t[:1] if t.ndim == 4 else t)
                except Exception as exc:  # 继承失败不应阻断续接
                    log.warning("r2v 段 #%d 继承上一段参考图失败: %s", seg.index + 1, exc)
        elif fl2v_handoff:
            prev_tail = resolve_prev_segment_output(
                plan, all_segments, seg.index, completed_outputs, node_id,
                allow_without_continuity=True,
            )
        elif is_continuity_active(plan, seg) and _seg_cont != "none":
            # fl2v auto-handoff: only derive the first frame from the previous tail when
            # the shot has no explicit start image. Shots with a manual start use their
            # own start even when continuity is on. Other tasks (i2v) keep prior behavior.
            if seg.task_key == "fl2v" and _ref_tensor_from_seg_refs(seg.refs, 0) is not None:
                prev_tail = None
            else:
                prev_tail = resolve_prev_segment_output(
                    plan, all_segments, seg.index, completed_outputs, node_id
                )

        ctx_w = plan.width
        ctx_h = plan.height
        if clip_frames is not None and clip_frames.shape[0] > 0:
            ctx_h, ctx_w = int(clip_frames.shape[1]), int(clip_frames.shape[2])

        report_director_progress(
            node_id, segment_index=progress_index, segment_total=seg_total,
            phase="prepare", phase_value=1, phase_max=1, **meta,
        )

        positive_prompt = seg.prompt
        handoff_reference = False
        handoff_video = False

        # 状态跟踪（阶段 C）：上一镜累计的 current_state 拼到本镜 prompt 前，
        # 让模型知道"故事走到哪了、人物处于什么状态"，再据此发生新动作。
        # 开关独立于「自动续接上段」「全局资产库」，字段缺失默认开启。
        # 注意：这里在段开始时捕获本镜前缀快照，供段末诊断使用——段末 state_holder
        # 已被本镜 state_change 更新，直接读会显示成"本镜自己的状态"而非"上一镜传下来的前缀"。
        _seg_start_state = (state_holder.get("current") or "").strip()
        if getattr(plan, "state_tracking_enabled", True):
            if _seg_start_state:
                positive_prompt = (
                    f"接续上一镜的状态：{_seg_start_state}。{positive_prompt}"
                )

        if seg.task_key == "fl2v":
            from .fl2v_timeline import reinforce_fl2v_prompt

            has_end = any(getattr(r, "index", None) == 1 for r in (seg.refs or []))
            if not has_end and seg.refs:
                has_end = len(seg.refs) >= 2
            # 自动交接镜头（无显式首帧）：
            # - 从视频续接模式：无显式尾帧 + 有 audio_vae + handoff_mode=="video" 时，
            #   用 ref_video_0=上段整段视频 走参考视频生视频，继承完整运动轨迹
            # - 参考图续接模式：同上但 handoff_mode=="image"，用 ref_image_0=上段尾帧
            # - 否则回退 first_frame 硬锁（ImageToVideo 路径）
            is_auto_handoff = _ref_tensor_from_seg_refs(seg.refs, 0) is None
            _handoff_mode = getattr(plan, "handoff_mode", "image")
            # 阶段 D 路由的 fl2v 段（r2v 批里标为 FL2VA 的段）：无显式尾帧时
            # 强制走首帧硬锁（首帧=上段尾帧），不退化为参考图续接——符合
            # "传了结束帧→完整首尾硬锁；没传→首帧硬锁"的预期。
            _director_fl2v = fl2v_handoff or str(getattr(seg, "continuity_mode", "")) == "fl2va"
            # 架构固底④：continuity_mode=="none"（独立镜头）时禁止自动参考续接，退化为
            # 无首帧 fl2v（提示词驱动）；若用户显式上传了首帧则仍用用户首帧。
            handoff_video = (not _director_fl2v) and is_auto_handoff and (_seg_cont != "none") and (audio_vae is not None) and (not has_end) and (_handoff_mode == "video")
            handoff_reference = (not _director_fl2v) and is_auto_handoff and (_seg_cont != "none") and (audio_vae is not None) and (not has_end) and (not handoff_video)
            positive_prompt = reinforce_fl2v_prompt(
                positive_prompt, has_end_frame=has_end, auto_handoff=is_auto_handoff,
                reference_mode=handoff_reference, video_mode=handoff_video,
            )
        elif seg.task_key == "r2v":
            ref_idxs = [int(getattr(r, "index", 0)) for r in (seg.refs or []) if r is not None]
            vid_idxs = [int(getattr(v, "index", 0)) for v in (getattr(seg, "ref_videos", None) or []) if v is not None]
            audio_idxs = [int(getattr(a, "index", 0)) for a in (seg.ref_audios or []) if a is not None]
            positive_prompt = reinforce_r2v_prompt(
                positive_prompt,
                ref_indices=ref_idxs,
                video_indices=vid_idxs,
                audio_indices=audio_idxs,
            )
            # r2v 自动续接（ref2va 模型专用）：无显式参考媒体 + 上一段已生成 →
            # 图片锚点：尾帧 + 继承角色/场景图，提示词动态补 <Picture N> 说明。
            # 全局资产库（Phase B）：每个 r2v 段按 cast/location 选择自动注入
            # 角色/场景资产图（世界锚点）。两个独立开关：
            # 「全局资产库」= plan.global_assets_enabled（注入资产图）
            # 「自动续接上段」= r2v_handoff（注入上一段尾帧）
            assets_enabled = bool(getattr(plan, "global_assets_enabled", True))
            # V1.6-B：多角色 cast 集合逐一注入（现有管线天然支持多角色资产图，
            # 多张「角色资产图」依次占参考图槽位，<Picture N> 说明一一对应）。
            cast_assets = getattr(seg, "cast_assets", None) or []
            location_asset = getattr(seg, "location_asset", None)
            # P0-1：per-shot 资产边界 → 纯函数组装（角色/场景/道具/风格 一一映射），
            # tensor 缺失自动跳过；不在此处做任何场景池扩散（那段逻辑已从 gen_timeline 移除）。
            asset_items = (
                build_shot_asset_items(
                    cast_assets,
                    location_asset,
                    getattr(seg, "extra_assets", None) or [],
                    getattr(seg, "tag_assets", None) or [],
                )
                if assets_enabled
                else []
            )
            # 使用全局资产时跳过「继承上一段 refs」——资产库就是人物/场景一致性的来源
            if asset_items:
                inherit_ref_tensors = []
            # 图片角色表：按实际注入顺序生成 <Picture N> 说明（与 ref_image 槽位一一对应）
            if r2v_handoff or asset_items:
                pic_roles: list[tuple[int, str, str]] = []
                occupied: set[int] = {int(getattr(r, "index", 0)) for r in (seg.refs or [])}
                for r in sorted((seg.refs or []), key=lambda x: int(getattr(x, "index", 0))):
                    pic_roles.append(
                        (int(getattr(r, "index", 0)) + 1, "本镜参考图", "保持其画面主体与风格一致")
                    )
                if r2v_handoff:
                    pic_roles.append(
                        (1, "上一镜头结束时的画面", "仅作为环境、构图与光线参考，不要复刻它的姿势与画面")
                    )
                    occupied.add(0)
                next_idx = 0
                while next_idx in occupied:
                    next_idx += 1
                for role, name, _t in asset_items:
                    if role == "角色资产图":
                        detail = f"保持角色{name}的脸部、发型、服装与身材完全一致"
                    elif role == "道具资产图":
                        detail = f"保持道具{name}的外观、材质、大小与所处位置完全一致"
                    elif role == "风格参考图":
                        detail = f"整体艺术风格、光影氛围与色彩倾向参考{name}，保持一致"
                    else:
                        detail = f"保持场景{name}的建筑结构、布局与光线氛围完全一致"
                    pic_roles.append((next_idx + 1, role, detail))
                    next_idx += 1
                positive_prompt = _r2v_handoff_prompt(
                    positive_prompt, pic_roles, continuing=r2v_handoff
                )
        elif seg.task_key == "v2v":
            positive_prompt = reinforce_v2v_prompt(positive_prompt)
        elif seg.task_key == "rv2v":
            ref_idxs = [int(getattr(r, "index", 0)) for r in (seg.refs or []) if r is not None]
            audio_idxs = [int(getattr(a, "index", 0)) for a in (seg.ref_audios or []) if a is not None]
            positive_prompt = reinforce_rv2v_prompt(
                positive_prompt, ref_indices=ref_idxs, audio_indices=audio_idxs,
            )

        report_director_progress(
            node_id, segment_index=progress_index, segment_total=seg_total,
            phase="context_encode", phase_value=0, phase_max=1, **meta,
        )

        # 判断是否为自动交接镜头
        auto_handoff = (seg.task_key == "fl2v") and (_ref_tensor_from_seg_refs(seg.refs, 0) is None)

        # 智能尾帧选择（待办④）：仅参考图路径（r2v 图片锚点 / fl2v 参考图续接）使用——
        # 从上一段末尾 window 帧中挑锐度最佳且未过暗/过曝的一帧作续接锚点，
        # 避免恰好取到运动模糊帧/黑帧。first_frame 硬锁路径不使用（须取真末帧保像素连续）。
        # 总开关 plan.smart_tail_enabled（缺失默认开启）→ 段级 seg.smart_tail 三态覆盖：
        # True=强制开启、False=强制关闭、None=跟随全局。
        anchor_frame = None
        anchor_desc = ""
        smart_tail_on = getattr(plan, "smart_tail_enabled", True)
        _seg_smart = getattr(seg, "smart_tail", None)
        if _seg_smart is not None:
            smart_tail_on = bool(_seg_smart)
        if (
            smart_tail_on
            and prev_tail is not None and prev_tail.shape[0] > 0
            and (r2v_handoff or handoff_reference)
        ):
            anchor_frame, anchor_desc = _pick_handoff_anchor(prev_tail)

        first_frame, last_frame, ref_images, ref_videos, ref_audios = _build_minimax_inputs(
            plan, seg, clip_frames=clip_frames, ctx_w=ctx_w, ctx_h=ctx_h, prev_tail=prev_tail,
            auto_handoff=auto_handoff,
            # 允许参考续接 = 参考图续接 或 从视频续接 任一被激活。具体走哪条由 handoff_mode 决定
            # （"image" → ref_image_0=上段尾帧；"video" → ref_video_0=上段整段视频）。
            # 旧写法只传 handoff_reference：从视频续接时 handoff_reference=False，
            # 导致 allow_reference=False 而误回退 first_frame 硬锁（2026-08-07 修复）。
            allow_reference=(handoff_reference or handoff_video),
            handoff_mode=getattr(plan, "handoff_mode", "image"),
            r2v_auto_handoff=r2v_handoff,
            inherit_ref_tensors=inherit_ref_tensors,
            asset_ref_tensors=[t for (_r, _n, t) in asset_items],
            anchor_frame=anchor_frame,
        )

        # 诊断：首帧/尾帧来源（用于确认 keyframe 硬锁定 / 参考图 / 图片锚点续接是否真正生效）
        if r2v_handoff and ref_images:
            _n_inh = len(inherit_ref_tensors)
            _inh_txt = f" + 继承{_n_inh}张角色/场景参考图" if _n_inh else ""
            _n_asset = len(asset_items)
            _asset_txt = f" + 全局资产{_n_asset}张" if _n_asset else ""
            ff_src = f"图片锚点续接(ref_image_0=上段尾帧{anchor_desc or '→取末帧'}{_inh_txt}{_asset_txt})"
            if not smart_tail_on:
                ff_src += "(智能选帧已关)"
        elif ref_images and asset_items:
            _n_asset = len(asset_items)
            ff_src = f"全局资产注入(角色/场景{_n_asset}张)"
        elif ref_videos and ref_images:
            src_desc = (
                f"上段整段视频({int(prev_tail.shape[0])}帧)"
                if prev_tail is not None
                else "源clip整段"
            )
            ff_src = f"复合续接(ref_image_0=上段尾帧; ref_video_0={src_desc})"
        elif ref_videos:
            src_desc = (
                f"上段整段视频({int(prev_tail.shape[0])}帧)"
                if prev_tail is not None
                else "源clip整段"
            )
            ff_src = f"从视频续接(ref_video_0={src_desc})"
        elif ref_images:
            src_desc = (
                f"上段尾帧({int(prev_tail.shape[0])}帧{anchor_desc or '→取末帧'})"
                if prev_tail is not None
                else "源clip尾帧"
            )
            ff_src = f"参考图续接(ref_image_0={src_desc})"
            if not smart_tail_on:
                ff_src += "(智能选帧已关)"
        elif first_frame is None:
            ff_src = "无 first_frame"
        elif auto_handoff and prev_tail is not None:
            ff_src = f"上一段尾帧({int(prev_tail.shape[0])}帧→取末帧)"
        elif auto_handoff:
            ff_src = "源clip尾帧"
        else:
            ff_src = "用户上传首帧"
        lf_src = "用户上传尾帧" if last_frame is not None else "无 last_frame"
        handoff_note = f"first_frame={ff_src}; last_frame={lf_src}"
        if auto_handoff and not ref_videos and not ref_images:
            # 阶段 D：fl2v_handoff（r2v 批路由出的 fl2v 段）或手动 FL2VA 段，无显式首帧时
            # 强制走首帧硬锁（首帧=上段真末帧）是设计行为，不是"回退"，无需告警；
            # 反而打正面标记便于确认路由生效。仅普通 fl2v 自动交接段回退硬锁才提示原因。
            if fl2v_handoff or str(getattr(seg, "continuity_mode", "")) == "fl2va":
                handoff_note += "; FL2VA 强制首帧硬锁"
            elif audio_vae is None:
                handoff_note += "; 注意: audio_vae 未连接，参考续接不可用，已回退 first_frame 硬锁"
            elif _handoff_mode != "video":
                handoff_note += "; 注意: 续接方式未选择从视频续接(handoff_mode=image)"
        # r2v 段无参考媒体且自动续接未开启 → 静默退化成 t2v 是常见坑，明确提示。
        if (
            seg.task_key == "r2v"
            and seg.index > 0
            and not ref_images and not ref_videos and not ref_audios
            and not r2v_handoff
        ):
            handoff_note += (
                "; 注意: 该 r2v 段无参考媒体且已关闭「自动续接上段」，已退化为文生视频(t2v)。"
                "取消关闭 r2v 工具栏「自动续接上段」开关后会自动用上一段尾帧+继承角色/场景图续接"
            )
        log.info("Segment %d/%d handoff: %s", seg.index + 1, len(all_segments), handoff_note)

        if seg.task_key in {"r2v", "v2v", "rv2v"} and (ref_images or ref_videos or ref_audios) and audio_vae is None:
            raise ValueError("r2v/v2v/rv2v / reference conditioning requires audio_vae input.")

        positive, negative, latent, task_hint = run_minimax_conditioning(
            clip=clip,
            vae=vae,
            audio_vae=audio_vae,
            prompt=positive_prompt,
            width=ctx_w,
            height=ctx_h,
            length=num_frames,
            task_key=seg.task_key,
            first_frame=first_frame,
            last_frame=last_frame,
            ref_images=ref_images,
            ref_videos=ref_videos,
            ref_audios=ref_audios,
        )

        report_director_progress(
            node_id, segment_index=progress_index, segment_total=seg_total,
            phase="context_encode", phase_value=1, phase_max=1, **meta,
        )

        if clear_vram_between_segments:
            cleanup_segment_vram(enabled=True, unload_models=seg_total > 1)

        def _report_sample_phase(phase: str, value: float, max_value: float = 1) -> None:
            report_director_progress(
                node_id, segment_index=progress_index, segment_total=seg_total,
                phase=phase, phase_value=value, phase_max=max_value, **meta,
            )

        samples = sample_single_stage(
            model=model,
            positive=positive,
            negative=negative,
            latent=latent,
            seed=seed,
            cfg=cfg,
            steps=steps,
            sampler_name=sampler,
            scheduler=scheduler,
            shift_video=shift_video,
            shift_audio=shift_audio,
            on_phase=_report_sample_phase,
        )

        report_director_progress(
            node_id, segment_index=progress_index, segment_total=seg_total,
            phase="decode", phase_value=0, phase_max=1, **meta,
        )
        decoded, audio_dict = _decode_av_latent(
            samples, vae, audio_vae, decode_audio=decode_audio,
        )
        report_director_progress(
            node_id, segment_index=progress_index, segment_total=seg_total,
            phase="decode", phase_value=1, phase_max=1, **meta,
        )

        if decoded.shape[0] > target_len:
            decoded = decoded[:target_len]

        chunk = _coerce_chunk(decoded)
        save_segment_cache(node_id, seg, plan, chunk, audio=audio_dict)
        completed_outputs[seg.index] = chunk

        # 状态跟踪（阶段 C）：本镜结束，用 seg.state_change 更新累计状态，供下一镜拼接。
        if getattr(plan, "state_tracking_enabled", True):
            _sc = (seg.state_change or "").strip()
            if _sc:
                state_holder["current"] = _sc

        if seg.task_key in {"t2v", "i2v", "r2v", "fl2v", "v2v", "rv2v"} and decoded.shape[0] >= 1:
            try:
                frames_b64 = [
                    tensor_frame_to_jpeg_b64(decoded[i])
                    for i in range(int(decoded.shape[0]))
                ]
                h, w = int(decoded.shape[1]), int(decoded.shape[2])
                report_director_segment_preview(
                    node_id,
                    segment_index=seg.index,
                    image_b64=frames_b64[0],
                    width=w,
                    height=h,
                    frames=frames_b64,
                    fps=float(plan.frame_rate or 24),
                )
            except Exception as exc:
                log.debug("Segment video preview skipped: %s", exc)

        if clear_vram_between_segments:
            cleanup_segment_vram(enabled=True)

        # Qwen3-VL 反馈（待办⑤）：
        # 在「段间清理显存」之后加载 VLM（错峰，不与 H3 抢显存），
        # 里程碑 A：一级状态提取——更新 state_holder 供下一镜状态前缀使用（用户手填优先）。
        # 里程碑 B：二级一致性检测——关键镜头（有角色/场景资产注入）对比生成帧 vs 资产图，
        #   检验「生成的是不是设定角色/场景」，报告显示 match 结果 + reason（不重跑）。
        # 任何异常都不阻断主生成流程（降级为无反馈）。
        qwen_note = ""
        qwen_consistency_note = ""
        qwen_rerun_note = ""
        _qv_level = int(getattr(plan, "qwen_vl_level", 1) or 1)
        if getattr(plan, "qwen_vl_enabled", False) and decoded.shape[0] >= 1:
            try:
                from .vlm_backends import create_default_backend
                from .qwen_vl_feedback import (
                    extract_state_from_tensor,
                    format_state_prefix,
                    check_consistency_from_tensors,
                    detect_issue_from_tensors,
                )
                from .state_rule_check import rule_check_frames, confirm_rerun

                backend = create_default_backend()
                try:
                    # --- 一级：状态提取（里程碑 A）---
                    _prev = (state_holder.get("current") or "").strip()
                    _qstate = extract_state_from_tensor(
                        backend, decoded, seg.prompt, prev_state=_prev,
                    )
                    _fmt = format_state_prefix(_qstate)
                    if _fmt:
                        # 用户手填优先：填了 state_change 用用户的；没填才用 Qwen 提取的。
                        if not (seg.state_change or "").strip():
                            state_holder["current"] = _fmt
                        qwen_note = (
                            f"Qwen3-VL 状态=「{_qstate.get('character') or '?'}"
                            f"@{_qstate.get('location') or '?'} {_qstate.get('time') or '?'}"
                            f"; 姿态={(_qstate.get('character_state') or {}).get('pose') or '?'}"
                            f"; 朝向={(_qstate.get('character_state') or {}).get('direction') or '?'}"
                            f"; 情绪={(_qstate.get('character_state') or {}).get('emotion') or '?'}"
                            f"; 景别={(_qstate.get('camera_state') or {}).get('shot') or '?'}"
                            f"; 机位={(_qstate.get('camera_state') or {}).get('angle') or '?'}」"
                        )

                    # --- 二级：一致性检测（里程碑 B）---
                    # 关键镜头判定（三态覆盖，用户 2026-08-07 设计：自动+手动可加减）：
                    #   seg.consistency_check True  = 强制检测（即使无资产，底层优雅降级为未查）
                    #   seg.consistency_check False = 跳过（即使有资产注入）
                    #   None/"auto"                 = 有角色/场景资产注入即自动检测（有对照物才算关键镜）
                    # 且 level>=2 才跑；match=false 只报告提醒，不重跑（重跑留给三级）。
                    if _qv_level >= 2:
                        _cast = getattr(seg, "cast_asset", None)
                        _loc = getattr(seg, "location_asset", None)
                        _cast_t = _cast.tensor if (_cast is not None and _cast.tensor is not None) else None
                        _loc_t = _loc.tensor if (_loc is not None and _loc.tensor is not None) else None
                        _cc = getattr(seg, "consistency_check", None)
                        if _cc is False:
                            _run_consistency = False  # 手动减：跳过
                        elif _cc is True:
                            _run_consistency = True  # 手动加：强制
                        else:
                            _run_consistency = _cast_t is not None or _loc_t is not None  # 自动
                        if _run_consistency:
                            _cons = check_consistency_from_tensors(
                                backend, decoded,
                                character_asset=_cast_t,
                                character_name=(
                                    _cast.name if (_cast is not None and _cast.name) else "主角"
                                ),
                                location_asset=_loc_t,
                                location_name=(
                                    _loc.name if (_loc is not None and _loc.name) else "当前场景"
                                ),
                            )
                            _cm = _cons.get("character_match")
                            _sm = _cons.get("scene_match")
                            _cl = _cons.get("clothing_match")
                            _r = (_cons.get("reason") or "").strip()
                            if _cm is not None or _sm is not None or _cl is not None:
                                _seg = []
                                _seg.append(f"角色={_cm if _cm is not None else '未查'}")
                                _seg.append(f"场景={_sm if _sm is not None else '未查'}")
                                _seg.append(f"服装={_cl if _cl is not None else '未查'}")
                                _parts = "; ".join(_seg)
                                if _r:
                                    _parts += f" ({_r})"
                                qwen_consistency_note = f"一致性: {_parts}"

                    # --- 三级：失败重跑（里程碑 C）---
                    # 用户设计（2026-08-07）：不让 Qwen 一个模型决定重跑，否则误判→无限生成。
                    # Qwen 语义判断（画面崩坏/多手/换脸等严重问题）→ 规则检测确认
                    # （黑帧/过曝/纯色/模糊/分辨率等客观像素异常）→ 双确认成立才重跑。
                    # 换种子重跑 1 次，结果替换输出；三级默认关（成本最高），level=3 才启用。
                    if _qv_level >= 3:
                        _issue = detect_issue_from_tensors(
                            backend, decoded, prompt=seg.prompt,
                        )
                        _issues = _issue.get("issues") or []
                        _sev = str(_issue.get("severity") or "low").strip().lower()
                        if _issues and _sev == "high":
                            _rule = rule_check_frames(decoded)
                            _rerun, _why = confirm_rerun(_issues, _rule)
                            if _rerun:
                                _new_seed = (seed + 1) & 0xFFFFFFFF
                                try:
                                    _samples2 = sample_single_stage(
                                        model=model,
                                        positive=positive,
                                        negative=negative,
                                        latent=latent,
                                        seed=_new_seed,
                                        cfg=cfg,
                                        steps=steps,
                                        sampler_name=sampler,
                                        scheduler=scheduler,
                                        shift_video=shift_video,
                                        shift_audio=shift_audio,
                                        on_phase=_report_sample_phase,
                                    )
                                    _dec2, _aud2 = _decode_av_latent(
                                        _samples2, vae, audio_vae, decode_audio=decode_audio,
                                    )
                                    if _dec2.shape[0] > target_len:
                                        _dec2 = _dec2[:target_len]
                                    _chunk2 = _coerce_chunk(_dec2)
                                    # 替换输出：后续报告 / 下一段续接 / 导出 都看新结果
                                    decoded = _dec2
                                    chunk = _chunk2
                                    audio_dict = _aud2
                                    completed_outputs[seg.index] = _chunk2
                                    save_segment_cache(node_id, seg, plan, _chunk2, audio=_aud2)
                                    qwen_rerun_note = (
                                        f"已重跑({_why}; seed={seed}→{_new_seed})"
                                    )
                                except Exception as _rex:
                                    # 重跑失败保留原输出，不阻断主流程
                                    log.warning("Qwen3-VL 三级重跑失败（保留原输出）: %s", _rex)
                                    qwen_rerun_note = f"重跑失败(保留原输出): {type(_rex).__name__}"
                            else:
                                # Qwen 单方面误判，规则不确认 → 不重跑（防无限生成铁律）
                                qwen_rerun_note = f"不重跑: {_why}"
                        elif _issues:
                            qwen_rerun_note = f"轻微问题: {'、'.join(_issues)}"
                finally:
                    backend.close()
            except Exception as exc:
                log.warning("Qwen3-VL 反馈跳过（不影响生成）: %s", exc)
                qwen_note = f"Qwen3-VL 反馈不可用: {type(exc).__name__}"

        # 镜头状态灯（UI 2.0 第五优先级）：按 Qwen 反馈判定「待检查」。
        # 轻微问题 / 一致性不通过 / Qwen 报疑似但规则不确认 / 重跑失败 →
        # 橙灯提示人工查看；否则清除运行时状态 → 前端按磁盘缓存显示「成功」。
        _seg_review = False
        if qwen_rerun_note.startswith("轻微问题"):
            _seg_review = True
        elif qwen_rerun_note.startswith("不重跑"):
            _seg_review = True
        elif qwen_rerun_note.startswith("重跑失败"):
            _seg_review = True
        elif qwen_consistency_note and "False" in qwen_consistency_note:
            _seg_review = True
        if _seg_review:
            set_segment_runtime_state(node_id, seg.index, "review")
        else:
            clear_segment_runtime_state(node_id, seg.index)

        reports.append(
            f"Segment {seg.index + 1}/{len(all_segments)}: {task_hint} "
            f"({target_len} frames, seed={seed})"
        )
        _dur = target_len / float(plan.frame_rate or 24.0)
        if _dur > 15.0:
            reports.append(
                f"  时长提示: 本镜 {_dur:.1f}s 超过 15s，建议拆分为两个镜头"
                "（镜头时长规范：特写 5–8s / 中景 8–10s / 全景 10–12s / "
                "建立镜头 12–15s；FL2VA 连续动作 ≤15s）。"
            )
        if handoff_note:
            reports.append(
                f"  衔接: {handoff_note}"
            )
        # 状态跟踪（阶段 C）诊断：本镜开始时的累计状态前缀（快照）+ 本镜填写/更新的状态变更。
        if getattr(plan, "state_tracking_enabled", True):
            _pre = _seg_start_state
            _sc = (seg.state_change or "").strip()
            _parts = []
            if _pre:
                _parts.append(f"状态前缀=「{_pre}」")
            else:
                _parts.append("状态前缀=无")
            if _sc:
                _parts.append(f"本镜状态变更=「{_sc}」")
            reports.append(f"  状态跟踪: " + "; ".join(_parts))
        # Qwen3-VL 反馈诊断（待办⑤）：显示提取的状态或不可用原因。
        if qwen_note:
            reports.append(f"  反馈: {qwen_note}")
        # 里程碑 B：二级一致性检测结果（关键镜头，有资产注入才跑）。
        if qwen_consistency_note:
            reports.append(f"  {qwen_consistency_note}")
        # 里程碑 C：三级失败重跑结果（Qwen+规则双确认；只有高严重度且规则确认才重跑）。
        if qwen_rerun_note:
            reports.append(f"  重跑: {qwen_rerun_note}")
        log.info(
            "MiniMax H3 Director segment %d/%d done (%d frames, task=%s)",
            seg.index + 1, len(all_segments), target_len, seg.task_key,
        )
        return chunk, audio_dict

    # ---- 场景/全片流式导出（scene / movie）：按场景分组执行 ----
    # 场景内合并（曝光/接缝优化）→ 编码 SceneNN.mp4 → 释放张量；内存峰值 ≈ 单场景。
    # movie 模式最后用 ffmpeg 直拼场景 mp4 → Movie.mp4（几乎不占合并内存）。
    scene_results: list[torch.Tensor] = []
    scene_audios_list: list[dict[str, Any]] = []

    def _shot_path_for(gi: int, shot_index: int) -> str:
        scene_path = _scene_paths[gi] if gi < len(_scene_paths) else f"Scene{gi + 1:02d}.mp4"
        base, ext = os.path.splitext(scene_path)
        return f"{base}_Shot{int(shot_index) + 1:02d}{ext or '.mp4'}"

    def _chunks_est_gb(chunks: list[torch.Tensor]) -> float:
        try:
            total = sum(int(c.shape[0]) for c in chunks)
            h, w = int(chunks[0].shape[1]), int(chunks[0].shape[2])
            return total * h * w * 3 * int(chunks[0].element_size()) / (1024 ** 3)
        except Exception:
            return 0.0

    def _scene_merge_fits(chunks: list[torch.Tensor]) -> bool:
        """估算场景内内存合并的峰值能否放下；放不下就降级为流式直拼。

        峰值 ≈ 输入张量 + 合并输出张量 + 接缝处理的临时副本，按 1.8 倍估算；
        与 _guard_merge_memory（out > avail*0.7 报错）同一口径、更保守。
        """
        if not chunks or _avail_ram <= 0:
            return True
        try:
            total = sum(int(c.shape[0]) for c in chunks)
            h, w = int(chunks[0].shape[1]), int(chunks[0].shape[2])
            elem = int(chunks[0].element_size())
        except Exception:
            return True
        if total <= 0:
            return True
        out = total * h * w * 3 * elem
        return out * 1.8 <= _avail_ram * 0.7

    def _write_shot_mp4(gi: int, idx: int, chunk: torch.Tensor,
                        audio: dict[str, Any] | None) -> str:
        path = _shot_path_for(gi, idx)
        try:
            save_segment_mp4(path, chunk, audio or {}, fps=float(plan.frame_rate or 24.0))
        except Exception as exc:
            log.warning("Shot %d mp4 write skipped (%s); streaming fallback re-encodes.", idx + 1, exc)
        return path

    def _stream_concat_scene(
        gi: int, group: list[int], chunks: list[torch.Tensor],
        scene_path: str, *, label: str, reason: str,
        shot_paths: list[str] | None = None,
        shot_audios: dict[int, dict[str, Any] | None] | None = None,
    ) -> torch.Tensor | None:
        """流式降级：单镜 mp4 已在循环里落盘，这里补齐后用 ffmpeg 直拼 SceneNN.mp4。

        几乎零内存，无像素级接缝优化（段间连续性由生成时续接机制保证）。
        scene 模式的 images 输出需要 tensor：尽量解码回来，放不下则占位。
        """
        _raise_if_interrupted()  # #91：取消时不再做昂贵的合并/编码
        fps = float(plan.frame_rate or 24.0)
        paths = list(shot_paths or [])
        if len(paths) != len(chunks):
            paths = [_shot_path_for(gi, group[i]) for i in range(len(chunks))]
        for i, chunk in enumerate(chunks):
            if not os.path.exists(paths[i]):
                save_segment_mp4(paths[i], chunk, (shot_audios or {}).get(group[i]) or {}, fps=fps)
        merge_segment_mp4s(paths, scene_path)
        reports.append(
            f"Scene {gi + 1}{label}: {reason}"
            f"（内存合并需约 {_chunks_est_gb(chunks):.1f} GB / 可用"
            f" {_avail_ram / (1024 ** 3):.1f} GB），降级为流式直拼"
            f" {len(chunks)} 个单镜 → {os.path.basename(scene_path)}"
        )
        if plan.export_mode != "scene":
            return None
        try:
            merged = decode_video_tensor(
                scene_path,
                dtype=torch.float16 if _decode_f16 else torch.float32,
            )
        except Exception as exc:
            log.warning("Scene %d decode back to tensor failed: %s", gi + 1, exc)
            merged = None
        if merged is None:
            merged = torch.full(
                (max(1, int(plan.total_frames)), max(1, int(plan.height)),
                 max(1, int(plan.width)), 3),
                0.5, dtype=torch.float32,
            )
            reports.append(
                f"Scene {gi + 1}{label}: tensor 输出降级为占位（内存放不下）；"
                "SceneNN.mp4 已落盘。"
            )
        return merged

    def _encode_scene(gi: int, group: list[int], chunks: list[torch.Tensor],
                      scene_audio: dict[str, Any] | None,
                      scene_label: str = "",
                      shot_paths: list[str] | None = None,
                      shot_audios: dict[int, dict[str, Any] | None] | None = None) -> torch.Tensor | None:
        _raise_if_interrupted()  # #91：取消时不再做昂贵的场景合并
        scene_segs = [all_segments[i] for i in group]
        scene_path = _scene_paths[gi] if gi < len(_scene_paths) else f"Scene{gi + 1:02d}.mp4"
        label = f"「{scene_label}」" if scene_label else ""
        fps = float(plan.frame_rate or 24.0)

        if not _scene_merge_fits(chunks):
            return _stream_concat_scene(
                gi, group, chunks, scene_path, label=label,
                reason="内存合并放不下", shot_paths=shot_paths, shot_audios=shot_audios,
            )

        try:
            merged = concat_continuous_chunks(chunks, scene_segs, plan)
        except Exception as exc:
            # 估算与实际有出入（例如合并瞬间 RAM 被抢占）：单镜 mp4 已落盘，
            # 降级为流式直拼，绝不因导出阶段内存不足丢掉整段成果。
            log.warning("Scene %d in-memory merge failed (%s); stream-concat fallback.", gi + 1, exc)
            return _stream_concat_scene(
                gi, group, chunks, scene_path, label=label,
                reason=f"内存合并失败({type(exc).__name__})",
                shot_paths=shot_paths, shot_audios=shot_audios,
            )
        save_segment_mp4(scene_path, merged, scene_audio, fps=fps)
        reports.append(
            f"Scene {gi + 1}{label}: merged {len(chunks)} shot(s), "
            f"{merged.shape[0]} frames → {os.path.basename(scene_path)}"
        )
        return merged

    if plan.export_mode in ("scene", "movie"):
        # 只在需要时把上一场景片段从 segment_outputs 用于「段间清显存」判断
        for gi, group_plan in enumerate(_scene_groups):
            group = group_plan.seg_indices
            scene_label = (group_plan.scene.name if group_plan.scene else "") or ""
            group_chunks: list[torch.Tensor] = []
            group_audios: list[dict[str, Any]] = []
            shot_paths: list[str] = []
            shot_audios: dict[int, dict[str, Any] | None] = {}
            for idx in group:
                seg = all_segments[idx]
                # 段间中断检查：多段运行时取消要能立刻停下，而不是等当前段跑完。
                _raise_if_interrupted()
                if idx in run_indices:
                    if clear_vram_between_segments and (group_chunks or segment_outputs):
                        cleanup_segment_vram(enabled=True)
                    try:
                        chunk, audio_dict = _run_one_segment(seg, progress_index=progress_pos[seg.index])
                    except BaseException:
                        set_segment_runtime_state(node_id, seg.index, "failed")
                        raise
                    chunk = _coerce_chunk(chunk)
                    group_chunks.append(chunk)
                    if audio_dict:
                        group_audios.append(audio_dict)
                    shot_audios[idx] = audio_dict
                    shot_paths.append(_write_shot_mp4(gi, idx, chunk, audio_dict))
                    del chunk, audio_dict
                    continue

                cached = load_segment_cache(node_id, seg, plan)
                if cached is not None:
                    cached = _coerce_chunk(cached)
                    cached_audio = load_segment_cache_audio(node_id, seg, plan)
                    completed_outputs[seg.index] = cached
                    reports.append(
                        f"Segment {seg.index + 1}/{len(all_segments)}: loaded from cache "
                        f"({cached.shape[0]} frames)"
                    )
                    group_chunks.append(cached)
                    if cached_audio:
                        group_audios.append(cached_audio)
                    shot_audios[idx] = cached_audio
                    shot_paths.append(_write_shot_mp4(gi, idx, cached, cached_audio))
                    continue

                fill = segment_passthrough_chunk(plan, seg)
                if fill is None:
                    raise ValueError(
                        f"Segment {seg.index + 1} is not selected and has no valid cache or source "
                        "frames to passthrough. Include it in「选择运行」, or switch export to「分镜导出」."
                    )
                fill = _coerce_chunk(fill)
                completed_outputs[seg.index] = fill
                passthrough_indices.append(seg.index)
                reports.append(
                    f"Segment {seg.index + 1}/{len(all_segments)}: source passthrough "
                    f"({fill.shape[0]} frames, not sampled — outside run selection)"
                )
                group_chunks.append(fill)
                shot_audios[idx] = None
                shot_paths.append(_write_shot_mp4(gi, idx, fill, None))

            if not group_chunks:
                raise ValueError(
                    f"Scene {gi + 1} produced no frames. Include its shot(s) in「选择运行」."
                )
            scene_audio = concat_audio_dicts(group_audios) or {}
            merged = _encode_scene(
                gi, group, group_chunks, scene_audio,
                scene_label=scene_label, shot_paths=shot_paths, shot_audios=shot_audios,
            )
            scene_audios_list.append(scene_audio)
            if plan.export_mode == "scene":
                if merged is None:
                    # 防御兜底：流式降级对 scene 模式总会返回 tensor。
                    merged = torch.full(
                        (max(1, int(plan.total_frames)), max(1, int(plan.height)),
                         max(1, int(plan.width)), 3),
                        0.5, dtype=torch.float32,
                    )
                scene_results.append(merged)
            del merged, group_chunks, group_audios, shot_paths, shot_audios

        if passthrough_indices:
            reports.append(
                "Passthrough (not sampled) segment(s) "
                f"{[i + 1 for i in passthrough_indices]} — run selection is honored; "
                "unselected gaps filled from cache/source before scene merge."
            )

        report_director_finish(node_id, seg_total)

        if plan.export_mode == "movie":
            if not _scene_paths:
                raise ValueError("Movie export produced no scene clips.")
            merge_segment_mp4s(_scene_paths, _movie_path)
            reports.append(
                f"Movie: merged {len(_scene_paths)} scene(s) → {os.path.basename(_movie_path)}"
            )
            try:
                combined = decode_video_tensor(
                    _movie_path,
                    dtype=torch.float16 if _decode_f16 else torch.float32,
                )
                reports.append(
                    f"Movie: decoded back to tensor ({combined.shape[0]} frames, "
                    f"{'float16' if _decode_f16 else 'float32'}) for node output."
                )
            except Exception as exc:
                log.warning("Movie decode back to tensor failed: %s", exc)
                combined = torch.full(
                    (max(1, int(plan.total_frames)), max(1, int(plan.height)),
                     max(1, int(plan.width)), 3),
                    0.5, dtype=torch.float32,
                )
                reports.append(
                    f"Movie: decode back to tensor failed（{exc}）；Movie.mp4 已落盘，"
                    "tensor 输出降级为占位帧。"
                )
            return combined, scene_results, scene_audios_list, "\n".join(reports)

        return None, scene_results, scene_audios_list, "\n".join(reports)

    # ---- 全片导出（内存合并，all）/ 分镜导出（segments）：保持既有行为 ----
    for seg in all_segments:
        # 段间中断检查（#91）：取消要能立刻停下，而不是等当前段跑完。
        _raise_if_interrupted()
        if seg.index in run_indices:
            if clear_vram_between_segments and segment_outputs:
                cleanup_segment_vram(enabled=True)
            try:
                chunk, audio_dict = _run_one_segment(seg, progress_index=progress_pos[seg.index])
            except BaseException:
                set_segment_runtime_state(node_id, seg.index, "failed")
                raise
            # 已跑段写回内存 completed_outputs：下一段 _run_one_segment 里
            # resolve_prev_segment_output（r2v/fl2v 自动续接取上段尾帧）优先查内存，
            # 缺失才落回磁盘缓存。不写回会导致「单镜/选中运行」场景下前驱段明明刚跑完，
            # 却查不到内存、再查磁盘缓存又因指纹 stale 而误报「段间连贯」错误（#82）。
            chunk = _coerce_chunk(chunk)
            completed_outputs[seg.index] = chunk
            segment_outputs.append(chunk)
            segment_audios.append(audio_dict or {})
            if plan.export_mode == "all":
                output_chunks.append(chunk)
            continue

        if plan.export_mode != "all":
            continue

        cached = load_segment_cache(node_id, seg, plan)
        if cached is not None:
            cached = _coerce_chunk(cached)
            completed_outputs[seg.index] = cached
            reports.append(
                f"Segment {seg.index + 1}/{len(all_segments)}: loaded from cache ({cached.shape[0]} frames)"
            )
            output_chunks.append(cached)
            continue

        # Not selected + no cache: keep full-timeline export by passthrough (do NOT sample).
        fill = segment_passthrough_chunk(plan, seg)
        if fill is None:
            raise ValueError(
                f"Segment {seg.index + 1} is not selected and has no valid cache or source "
                "frames to passthrough. Include it in「选择运行」, or switch export to「分镜导出」."
            )
        fill = _coerce_chunk(fill)
        completed_outputs[seg.index] = fill
        passthrough_indices.append(seg.index)
        reports.append(
            f"Segment {seg.index + 1}/{len(all_segments)}: source passthrough "
            f"({fill.shape[0]} frames, not sampled — outside run selection)"
        )
        output_chunks.append(fill)

    if passthrough_indices:
        reports.append(
            "Passthrough (not sampled) segment(s) "
            f"{[i + 1 for i in passthrough_indices]} — run selection is honored; "
            "unselected gaps filled from cache/source for「全部导出」."
        )

    if not output_chunks and not segment_outputs:
        raise ValueError("Director plan produced no segments.")

    report_director_finish(node_id, seg_total)
    export_chunks = output_chunks if output_chunks else segment_outputs
    export_segments = all_segments if output_chunks else [all_segments[i] for i in sorted(run_indices)]
    combined = concat_continuous_chunks(export_chunks, export_segments, plan)
    return combined, segment_outputs, segment_audios, "\n".join(reports)
