"""Disk cache for MiniMax H3 Director segment decode outputs (partial re-run + merge).

Cache is best-effort: write failures (cloud RO mounts, same-name overwrite
blocks, full disks) must never abort the main generation run.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable

import torch

import folder_paths

from .plan import DirectorPlan, SegmentPlan

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.cache")


def _cache_root(node_id: str) -> Path | None:
    try:
        root = Path(folder_paths.get_output_directory()) / "minimax_seg_cache" / str(node_id)
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError as exc:
        log.warning("Segment cache dir unavailable (%s); cache disabled for this run.", exc)
        return None


def _asset_fingerprint(asset: Any | None) -> str:
    """注入资产图（角色/场景/道具/风格）的来源标识 → 缓存指纹。

    资产图经 executor 注入到 ref_image 槽位但不写回 seg.refs，若不进指纹，
    用户替换/更换参考图后指纹不变、会命中旧缓存出旧画面（#90 同类问题）。
    取 kind:id:image_file：id 变化（换资产）、image_file 变化（替换图片）都失效。
    """
    if asset is None:
        return ""
    kind = str(getattr(asset, "kind", "") or "")
    aid = str(getattr(asset, "id", "") or "")
    image_file = str(getattr(asset, "image_file", "") or "")
    return f"{kind}:{aid}:{image_file}"


def segment_cache_fingerprint(seg: SegmentPlan, plan: DirectorPlan) -> dict[str, Any]:
    """Stable identity for a segment —cache invalidates when edit params change."""
    ref_files = sorted(f"img{ref.index}" for ref in seg.refs)
    ref_audio_files = sorted(
        f"aud{getattr(a, 'index', i)}:{(getattr(a, 'audio_file', '') or '')}"
        for i, a in enumerate(getattr(seg, "ref_audios", None) or [])
    )
    ref_video_files = sorted(
        f"vid{getattr(v, 'index', i)}:{(getattr(v, 'video_file', '') or '')}"
        for i, v in enumerate(getattr(seg, "ref_videos", None) or [])
    )
    ref_video_file = (
        seg.reference_video_meta.get("videoFile")
        or seg.reference_video_meta.get("fileName")
        or ""
    ).strip()
    # V1.6-B：cast 集合逐一指纹（[林雪]→[林雪,陈默] 列表变化缓存必失效）。
    _cast_assets = getattr(seg, "cast_assets", None) or []
    _injected = [_asset_fingerprint(a) for a in _cast_assets]
    if not _cast_assets:
        _injected.append(_asset_fingerprint(getattr(seg, "cast_asset", None)))
    _injected.append(_asset_fingerprint(getattr(seg, "location_asset", None)))
    _injected += [_asset_fingerprint(a) for a in (getattr(seg, "extra_assets", None) or [])]
    _injected += [_asset_fingerprint(a) for a in (getattr(seg, "tag_assets", None) or [])]
    injected_assets = sorted(x for x in _injected if x)
    return {
        "id": getattr(seg, "id", "") or "",
        "index": seg.index,
        "start": seg.start_frame,
        "end": seg.end_frame,
        "prompt": seg.prompt,
        "negative": seg.negative_prompt,
        "task_key": seg.task_key,
        "width": plan.width,
        "height": plan.height,
        "output_mode": plan.output_mode,
        "ref_max": plan.ref_max_size,
        "refs": ref_files,
        "ref_audios": ref_audio_files,
        "ref_videos": ref_video_files,
        "ref_video": ref_video_file,
        "ref_video_start": seg.reference_video_start_frame,
        "assets": injected_assets,
        "continuity": plan.continuity_enabled,
        "continuity_overlap": plan.continuity_overlap_frames if plan.continuity_enabled else 0,
        # Bump when continuity sampling/handoff semantics change (invalidates stale segs).
        "continuity_pipeline": "minimax_h3_lastframe_v1",
    }


def _safe_unlink(path: Path) -> bool:
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
        return True
    except OSError:
        return False


def _atomic_publish(tmp: Path, dest: Path) -> None:
    """Move ``tmp`` →``dest``, tolerating clouds that block same-name overwrite."""
    try:
        os.replace(tmp, dest)
        return
    except OSError:
        pass
    # Some cloud mounts reject overwrite of an existing name —remove then rename.
    _safe_unlink(dest)
    try:
        os.replace(tmp, dest)
        return
    except OSError:
        pass
    try:
        tmp.rename(dest)
        return
    except OSError:
        # Last resort: keep the unique temp as the published file name is blocked.
        # Caller may still fail if even create-new is denied.
        raise


def _write_via_temp(dest: Path, write_fn: Callable[[Path], None]) -> None:
    """Write to a unique temp name in the same folder, then publish to ``dest``."""
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        write_fn(tmp)
        _atomic_publish(tmp, dest)
    finally:
        _safe_unlink(tmp)


def save_segment_cache(
    node_id: str | None,
    seg: SegmentPlan,
    plan: DirectorPlan,
    tensor: torch.Tensor,
    audio: dict[str, Any] | None = None,
) -> None:
    """Persist a segment tensor (optionally with its generated audio). Never raises —cache miss on next run is fine.

    audio 为 ``{"waveform": [1,C,T] float [-1,1], "sample_rate": int}``（ComfyUI
    AUDIO dict）。单独存成 ``seg_%04d.aud.pt``，便于恢复工具/后续合并复用带声音的单镜。
    旧缓存没有该文件时视为无音频（不报错）。
    """
    if not node_id:
        return
    root = _cache_root(node_id)
    if root is None:
        return
    fp = segment_cache_fingerprint(seg, plan)
    idx = seg.index
    pt_path = root / f"seg_{idx:04d}.pt"
    meta_path = root / f"seg_{idx:04d}.meta.json"
    aud_path = root / f"seg_{idx:04d}.aud.pt"
    try:
        payload = tensor.cpu().float().contiguous()
        _write_via_temp(pt_path, lambda p: torch.save(payload, p))
        text = json.dumps(fp, ensure_ascii=False, sort_keys=True)
        _write_via_temp(
            meta_path,
            lambda p: p.write_text(text, encoding="utf-8"),
        )
        if (
            isinstance(audio, dict)
            and isinstance(audio.get("waveform"), torch.Tensor)
            and int(audio["waveform"].numel()) > 0
        ):
            _write_via_temp(
                aud_path,
                lambda p: torch.save(
                    {
                        "waveform": audio["waveform"].detach().cpu().float().contiguous(),
                        "sample_rate": int(audio.get("sample_rate") or 44100) or 44100,
                    },
                    p,
                ),
            )
        else:
            _safe_unlink(aud_path)
        log.debug(
            "Cached segment %d for node %s (%d frames%s)",
            idx + 1,
            node_id,
            int(tensor.shape[0]),
            " + audio" if aud_path.is_file() else "",
        )
    except Exception as exc:
        # Xiangong / similar: RO mount or same-name write →skip cache, keep run alive.
        log.warning(
            "Segment %d cache write skipped (%s). Generation continues without disk cache.",
            idx + 1,
            exc,
        )
        for stray in root.glob(f".seg_{idx:04d}.*"):
            _safe_unlink(stray)


def load_segment_cache(
    node_id: str | None,
    seg: SegmentPlan,
    plan: DirectorPlan,
    *,
    allow_stale: bool = False,
) -> torch.Tensor | None:
    if not node_id:
        return None
    root = _cache_root(node_id)
    if root is None:
        return None
    idx = seg.index
    meta_path = root / f"seg_{idx:04d}.meta.json"
    tensor_path = root / f"seg_{idx:04d}.pt"
    if not meta_path.is_file() or not tensor_path.is_file():
        return None
    try:
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
        expected = segment_cache_fingerprint(seg, plan)
        if stored != expected:
            if allow_stale:
                # 续接锚点专用：段间连贯只需要上一段的尾帧/首帧做视觉锚点，内容即使
                # 因「导出模式/续接开关等全局字段」变化被判 stale，帧画面仍可作续接参考。
                # 打 warning 不拒绝，避免单镜重生成 r2v/fl2v 段时被「段间连贯」卡死（#82）。
                # 仅校验空间尺寸——参考帧下游会做 long-edge 缩放（_encode_single_reference_frame）。
                tensor = torch.load(tensor_path, map_location="cpu", weights_only=True)
                if (
                    isinstance(tensor, torch.Tensor)
                    and tensor.ndim == 4
                    and int(tensor.shape[1]) > 0
                    and int(tensor.shape[2]) > 0
                ):
                    log.warning(
                        "Segment %d cache is stale (timeline changed) but frames are usable as a "
                        "continuation anchor; using stale tail for segment #%d handoff.",
                        idx + 1,
                        idx + 2,
                    )
                    return tensor
                return None
            log.info(
                "Segment %d cache stale (timeline changed); re-run this segment to refresh.",
                idx + 1,
            )
            return None
        return torch.load(tensor_path, map_location="cpu", weights_only=True)
    except Exception as exc:
        log.warning("Failed to load segment %d cache: %s", idx + 1, exc)
        return None


def load_segment_cache_audio(
    node_id: str | None,
    seg: SegmentPlan,
    plan: DirectorPlan,
) -> dict[str, Any] | None:
    """载入与视频缓存配套的生成音频（``seg_%04d.aud.pt``）。没有则返回 None。

    返回值结构 ``{"waveform": [1,C,T] float [-1,1], "sample_rate": int}``，
    可直接用于 ``save_segment_mp4`` 的 audio 参数。旧缓存无该文件时静默返回 None。
    """
    if not node_id:
        return None
    root = _cache_root(node_id)
    if root is None:
        return None
    idx = seg.index
    meta_path = root / f"seg_{idx:04d}.meta.json"
    tensor_path = root / f"seg_{idx:04d}.pt"
    aud_path = root / f"seg_{idx:04d}.aud.pt"
    if not meta_path.is_file() or not tensor_path.is_file() or not aud_path.is_file():
        return None
    try:
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
        expected = segment_cache_fingerprint(seg, plan)
        if stored != expected:
            return None
        data = torch.load(aud_path, map_location="cpu", weights_only=True)
        if not isinstance(data, dict) or not isinstance(data.get("waveform"), torch.Tensor):
            return None
        return {
            "waveform": data["waveform"].float().contiguous(),
            "sample_rate": int(data.get("sample_rate") or 44100) or 44100,
        }
    except Exception as exc:
        log.debug("Segment %d audio cache load skipped: %s", idx + 1, exc)
        return None
