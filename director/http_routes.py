"""HTTP routes for MiniMax H3 Director (chunked video upload + MiniMax Studio project layer)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass

import folder_paths
from aiohttp import web
from server import PromptServer

from .project_store import (
    create_project,
    delete_project,
    delete_snapshot,
    list_projects,
    list_snapshots,
    load_project,
    load_snapshot,
    save_project,
)
from .script_analyzer import analyze_script
from .script_parser import parse_script
from .story_analyzer import build_plan_from_beats, extract_beats
from .bible_ops import (
    confirm_bible_action,
    create_bible_entry,
    get_bible_payload,
    merge_plan_into_bible,
)
from .segment_status import get_segment_runtime_states
from .asset_matcher import match_plan
from .asset_registry import AssetRegistry, default_registry
from .camera_template import build_plan_cameras, list_camera_templates
from .prompt_builder_v17 import build_plan_drafts
from .h3_prompt_builder import SCHEMA_VERSION as H3_SCHEMA_VERSION
from .h3_prompt_builder import build_plan_h3_prompts
from .constraint_checker import build_constraint_check  # 生成约束层 v1.0（P1 接线：/prompt/h3 constraint_check）
from .production_plan import ProductionPlan, VoiceType
from .audio_intent import AudioIntent, VoiceLine
from .tts_engine import create_default_tts_backend
from .tts_worker import shot_output_dir, synthesize_shot
from .mixer import mix_shot
from .visual_style import presets_payload

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director")

CHUNK_ROOT = os.path.join(folder_paths.get_temp_directory(), "minimax_upload_chunks")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-()\u4e00-\u9fff]+")
_ROUTES_REGISTERED = False


def _safe_basename(name: str) -> str:
    base = os.path.basename(str(name or "video.mp4").replace("\\", "/"))
    base = _SAFE_NAME.sub("_", base).strip("._")
    return base or "video.mp4"


async def minimax_upload_video_chunk(request):
    try:
        post = await request.post()
    except Exception as exc:
        return web.Response(status=400, text=f"Invalid upload: {exc}")

    upload_id = str(post.get("upload_id") or "").strip()
    filename = _safe_basename(post.get("filename"))
    chunk_field = post.get("chunk")
    if not upload_id or chunk_field is None:
        return web.Response(status=400, text="Missing upload_id or chunk.")

    if ".." in upload_id or "/" in upload_id or "\\" in upload_id:
        return web.Response(status=400, text="Invalid upload_id.")

    try:
        chunk_index = int(post.get("chunk_index", 0))
        total_chunks = int(post.get("total_chunks", 1))
    except (TypeError, ValueError):
        return web.Response(status=400, text="Invalid chunk index.")

    if total_chunks < 1 or chunk_index < 0 or chunk_index >= total_chunks:
        return web.Response(status=400, text="Chunk index out of range.")

    session_dir = os.path.join(CHUNK_ROOT, upload_id)
    os.makedirs(session_dir, exist_ok=True)
    part_path = os.path.join(session_dir, f"{chunk_index:06d}.part")

    with open(part_path, "wb") as out:
        while True:
            block = chunk_field.file.read(1024 * 1024)
            if not block:
                break
            out.write(block)

    if chunk_index + 1 < total_chunks:
        return web.json_response({"status": "ok", "chunk_index": chunk_index})

    input_dir = folder_paths.get_input_directory()
    out_path = os.path.join(input_dir, filename)
    if os.path.exists(out_path):
        stem, ext = os.path.splitext(filename)
        for n in range(1, 1000):
            candidate = f"{stem}_{n}{ext}"
            candidate_path = os.path.join(input_dir, candidate)
            if not os.path.exists(candidate_path):
                out_path = candidate_path
                filename = candidate
                break

    with open(out_path, "wb") as out:
        for i in range(total_chunks):
            part = os.path.join(session_dir, f"{i:06d}.part")
            if not os.path.isfile(part):
                shutil.rmtree(session_dir, ignore_errors=True)
                return web.Response(status=400, text=f"Missing chunk {i}.")
            with open(part, "rb") as src:
                shutil.copyfileobj(src, out)

    shutil.rmtree(session_dir, ignore_errors=True)
    log.info("MiniMax H3 Director uploaded video to input/: %s", filename)
    return web.json_response({"name": filename, "subfolder": "", "type": "input"})


async def minimax_probe_video(request):
    try:
        if request.can_read_body and request.content_type == "application/json":
            body = await request.json()
        else:
            body = dict(request.query)
    except Exception as exc:
        return web.Response(status=400, text=f"Invalid request: {exc}")

    video_file = str(body.get("videoFile") or body.get("video_file") or "").strip()
    if not video_file:
        return web.Response(status=400, text="Missing videoFile.")

    from ..lib.video_io import probe_video_clip

    clip = {
        "videoFile": video_file,
        "fileName": os.path.basename(video_file),
        "subfolder": str(body.get("subfolder") or "").strip(),
        "type": str(body.get("type") or "input").strip() or "input",
    }
    try:
        info = probe_video_clip(clip)
    except Exception as exc:
        log.warning("MiniMax H3 Director video probe failed: %s", exc)
        return web.Response(status=400, text=str(exc))
    return web.json_response(info)


async def minimax_detect_shots(request):
    """Detect shot boundaries with PySceneDetect; return logical cut frames."""
    try:
        body = await request.json()
    except Exception as exc:
        return web.Response(status=400, text=f"Invalid JSON: {exc}")

    from ..lib.shot_detect import (
        detect_timeline_shot_cuts,
        scenedetect_available,
        scenedetect_install_hint,
    )

    if not scenedetect_available():
        return web.Response(
            status=400,
            text=(
                "PySceneDetect is not installed in ComfyUI's Python "
                f"({__import__('sys').executable}). "
                f"Run: {scenedetect_install_hint()}"
            ),
        )

    try:
        frame_rate = float(body.get("frameRate") or body.get("frame_rate") or 24)
    except (TypeError, ValueError):
        frame_rate = 24.0
    try:
        total_frames = int(body.get("totalFrames") or body.get("total_frames") or 0)
    except (TypeError, ValueError):
        return web.Response(status=400, text="Invalid totalFrames.")

    sensitivity = str(body.get("sensitivity") or "medium").strip().lower()
    try:
        min_shot_frames = int(body.get("minShotFrames") or body.get("min_shot_frames") or 12)
    except (TypeError, ValueError):
        min_shot_frames = 12

    clips_in = body.get("clips")
    clips: list[dict] = []
    if isinstance(clips_in, list) and clips_in:
        for item in clips_in:
            if not isinstance(item, dict):
                continue
            video_file = str(item.get("videoFile") or item.get("video_file") or "").strip()
            if not video_file:
                continue
            clips.append(
                {
                    "videoFile": video_file,
                    "fileName": os.path.basename(video_file),
                    "subfolder": str(item.get("subfolder") or "").strip(),
                    "type": str(item.get("type") or "input").strip() or "input",
                    "logicalStart": item.get("logicalStart", item.get("logical_start", 0)),
                    "logicalEnd": item.get("logicalEnd", item.get("logical_end", total_frames)),
                    "nativeFps": item.get("nativeFps", item.get("native_fps")),
                }
            )
    else:
        video_file = str(body.get("videoFile") or body.get("video_file") or "").strip()
        if not video_file:
            return web.Response(status=400, text="Missing clips[] or videoFile.")
        clips.append(
            {
                "videoFile": video_file,
                "fileName": os.path.basename(video_file),
                "subfolder": str(body.get("subfolder") or "").strip(),
                "type": str(body.get("type") or "input").strip() or "input",
                "logicalStart": 0,
                "logicalEnd": total_frames,
                "nativeFps": body.get("nativeFps", body.get("native_fps")),
            }
        )

    if total_frames <= 0:
        return web.Response(status=400, text="totalFrames must be > 0.")

    try:
        result = detect_timeline_shot_cuts(
            clips,
            frame_rate=frame_rate,
            total_frames=total_frames,
            sensitivity=sensitivity,
            min_shot_frames=min_shot_frames,
        )
    except ImportError as exc:
        return web.Response(status=400, text=str(exc))
    except Exception as exc:
        log.warning("MiniMax H3 Director shot detect failed: %s", exc)
        return web.Response(status=400, text=str(exc))

    return web.json_response(result)


def _register_route(routes, method: str, path: str, handler) -> None:
    if hasattr(routes, "add_route"):
        routes.add_route(method, path, handler)
    elif method == "POST" and hasattr(routes, "post"):
        routes.post(path)(handler)
    elif method == "GET" and hasattr(routes, "get"):
        routes.get(path)(handler)
    elif method == "DELETE" and hasattr(routes, "delete"):
        routes.delete(path)(handler)
    else:
        raise AttributeError("Unsupported ComfyUI route table API")


def _segment_cache_shot_id(root: str, idx: int) -> str | None:
    """读 seg_%04d.meta.json 里的镜头 id（#90 指纹含 id）；无/损坏返回 None。"""
    meta_path = os.path.join(root, f"seg_{idx:04d}.meta.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cid = data.get("id")
        return str(cid) if cid else None
    except Exception:
        return None


async def minimax_segment_cache_status(request):
    """Return indices of segments that already have a disk cache entry.

    Response: {"cached": [0, 3, 5]} — indices with seg_NNNN.pt present.
    可选 shot_ids：镜头身份校验（与 segment_status 一致），排除旧项目残留缓存。
    """
    node_id = str(request.query.get("node_id") or "").strip()
    if not node_id or not re.fullmatch(r"[A-Za-z0-9_\-]+", node_id):
        return web.json_response({"cached": []})

    shot_ids_raw = str(request.query.get("shot_ids") or "").strip()
    shot_ids: set[str] | None = None
    if shot_ids_raw:
        shot_ids = {x for x in shot_ids_raw.split(",") if x}

    root = os.path.join(folder_paths.get_output_directory(), "minimax_seg_cache", node_id)
    if not os.path.isdir(root):
        return web.json_response({"cached": []})

    indices: list[int] = []
    for name in os.listdir(root):
        m = re.fullmatch(r"seg_(\d{4,})\.pt", name)
        if m:
            idx = int(m.group(1))
            if shot_ids is not None:
                cid = _segment_cache_shot_id(root, idx)
                if not cid or cid not in shot_ids:
                    continue
            indices.append(idx)
    indices.sort()
    return web.json_response({"cached": indices})


async def minimax_segment_status(request):
    """Return per-segment status light data.

    Response:
      {"cached": [0, 2], "states": {"1": "running", "3": "review", "4": "failed"}}

    - cached:  indices with an on-disk cache entry (→ 成功, survives restarts).
    - states:  transient runtime states (→ 生成中/待检查/失败, in-memory only).

    可选参数 shot_ids（逗号分隔的当前项目镜头 id）：传入后做镜头身份校验，
    只有 meta.json 里 id 匹配的缓存才算 cached——防止旧项目/旧时间线残留缓存
    被误判为当前项目镜头已生成（用户实测：播放全片加载出几天前的旧视频）。
    不传则保持旧行为（只按文件存在性），兼容旧调用方。
    """
    node_id = str(request.query.get("node_id") or "").strip()
    if not node_id or not re.fullmatch(r"[A-Za-z0-9_\-]+", node_id):
        return web.json_response({"cached": [], "states": {}})

    shot_ids_raw = str(request.query.get("shot_ids") or "").strip()
    shot_ids: set[str] | None = None
    if shot_ids_raw:
        shot_ids = {x for x in shot_ids_raw.split(",") if x}

    cached: list[int] = []
    root = os.path.join(folder_paths.get_output_directory(), "minimax_seg_cache", node_id)
    if os.path.isdir(root):
        for name in os.listdir(root):
            m = re.fullmatch(r"seg_(\d{4,})\.pt", name)
            if not m:
                continue
            idx = int(m.group(1))
            if shot_ids is not None:
                cid = _segment_cache_shot_id(root, idx)
                if not cid or cid not in shot_ids:
                    continue
            cached.append(idx)
    cached.sort()

    return web.json_response({
        "cached": cached,
        "states": get_segment_runtime_states(node_id),
    })


def _load_segment_audio_for_download(root: str, index: int) -> dict | None:
    """读取单镜音频缓存 seg_%04d.aud.pt；没有或损坏返回 None。"""
    aud_path = os.path.join(root, f"seg_{index:04d}.aud.pt")
    if not os.path.isfile(aud_path):
        return None
    try:
        import torch
        data = torch.load(aud_path, map_location="cpu", weights_only=True)
        if (
            isinstance(data, dict)
            and isinstance(data.get("waveform"), torch.Tensor)
            and int(data["waveform"].numel()) > 0
        ):
            return {
                "waveform": data["waveform"].float().contiguous(),
                "sample_rate": int(data.get("sample_rate") or 44100) or 44100,
            }
    except Exception as exc:
        log.warning("Segment %d audio cache load failed: %s", index, exc)
    return None


async def minimax_segment_mp4(request):
    """下载单个镜头的 mp4（从磁盘缓存编码，缓存目录里已编好的 mp4 直接返回）。

    参数：node_id、index（0 起）、可选 fps（默认 24.0）。
    响应：video/mp4 附件流，文件名 ShotNN.mp4；无缓存返回 404 JSON。
    首次请求会把 seg_%04d.pt（含音频）编码成 seg_%04d.mp4 落盘，之后秒回。
    """
    node_id = str(request.query.get("node_id") or "").strip()
    index_str = str(request.query.get("index") or "").strip()
    if not node_id or not re.fullmatch(r"[A-Za-z0-9_\-]+", node_id):
        return web.json_response({"error": "invalid_node_id"}, status=400)
    if not index_str.isdigit():
        return web.json_response({"error": "invalid_index"}, status=400)
    index = int(index_str)
    if index < 0 or index > 9999:
        return web.json_response({"error": "index_out_of_range"}, status=400)

    try:
        fps = float(request.query.get("fps") or 24.0)
    except (TypeError, ValueError):
        fps = 24.0
    fps = max(0.1, fps)

    root = os.path.join(folder_paths.get_output_directory(), "minimax_seg_cache", node_id)
    pt_path = os.path.join(root, f"seg_{index:04d}.pt")
    mp4_path = os.path.join(root, f"seg_{index:04d}.mp4")
    if not os.path.isfile(pt_path):
        return web.json_response(
            {
                "error": "not_cached",
                "message": "该镜头尚未生成或缓存已失效，先在本节点生成该镜。",
                "index": index,
            },
            status=404,
        )

    try:
        import torch
        from .stream_export import save_segment_mp4

        # 已有编码产物且不比源缓存旧 → 直接返回（避免重复编码）。
        if os.path.isfile(mp4_path) and os.path.getmtime(mp4_path) >= os.path.getmtime(pt_path):
            return web.FileResponse(
                mp4_path,
                headers={"Content-Disposition": f'attachment; filename="Shot{index + 1:02d}.mp4"'},
            )

        tensor = torch.load(pt_path, map_location="cpu", weights_only=True)
        if not isinstance(tensor, torch.Tensor) or tensor.ndim != 4:
            return web.json_response(
                {"error": "invalid_cache", "message": "缓存帧不是有效视频张量。"},
                status=500,
            )
        audio = _load_segment_audio_for_download(root, index)
        # 用临时文件编码，成功后原子替换，避免并发请求写坏半截文件。
        tmp_path = mp4_path + f".tmp.{os.getpid()}"
        try:
            save_segment_mp4(tmp_path, tensor, audio, fps=fps)
            if os.path.exists(mp4_path):
                os.remove(mp4_path)
            os.replace(tmp_path, mp4_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return web.FileResponse(
            mp4_path,
            headers={"Content-Disposition": f'attachment; filename="Shot{index + 1:02d}.mp4"'},
        )
    except Exception as exc:
        log.warning("Segment %d mp4 encode failed: %s", index, exc)
        return web.json_response(
            {"error": "encode_failed", "message": f"编码失败：{exc}"},
            status=500,
        )


async def minimax_director_export(request):
    """V1.6-A：流式导出成片（scene/movie，ffmpeg 直拼低内存）。

    参数（JSON body）：
      node_id       必填。Director 节点 id，定位 ``minimax_seg_cache/<node_id>``。
      shot_ids      必填。string[]，要导出的镜头 id，**按时间线顺序**（前端拍平
                    顺序；scene 档只传该场景镜头）。导出合并顺序 = 此数组顺序。
      scope         "movie" | "scene"（默认 movie，仅用于命名/日志透传）。
      scene_id      可选。scene 档场景 id（前端已知，透传回响应便于关联）。
      fps           可选，默认 24.0。单镜编码帧率。
      prefix        可选，默认 "MiniMax_Studio_Movie" / "MiniMax_Studio_Scene"。
      skip_missing  可选，默认 false。true=跳过缺失镜头只导出已有的；
                    false=任一镜头缺失即报 not_cached（安全网，前端已前置检测）。

    响应：
      成功  {"path", "filename", "subfolder":"", "type":"output",
             "scope", "segments", "missing":[]}
      部分  skip_missing=true 且存在缺失时成功，但 missing 列出被跳过的镜头。
      失败  {"error":"not_cached","missing":[{shotId,order}]}  任一缺失（默认模式）
            {"error":"nothing_to_export"}                      无任何已生成镜头
            {"error":"ffmpeg_missing"}                         ffmpeg 不可用
            {"error":"invalid_cache","message"}                缓存帧不是有效视频张量

    设计要点：复用 stream_export 的 save_segment_mp4（显式 format="mp4"，#84）+
    merge_segment_mp4s（ffmpeg concat，先 -c copy 失败回退重编码），内存峰值 ≈
    1~2 段，长片不触发 #103「合并需约 N GB」保护。导出是「任务中心里的一种任务」，
    前端先做缺失/参数过期检测，本路由是执行兜底。
    """
    try:
        body = await request.json()
    except Exception as exc:
        return web.Response(status=400, text=f"Invalid JSON: {exc}")

    node_id = str(body.get("node_id") or "").strip()
    if not node_id or not re.fullmatch(r"[A-Za-z0-9_\-]+", node_id):
        return web.json_response({"error": "invalid_node_id", "message": "缺少合法 node_id。"}, status=400)

    raw_ids = body.get("shot_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return web.json_response(
            {"error": "bad_shot_ids", "message": "shot_ids 必须是镜头 id 数组且不能为空。"},
            status=400,
        )
    shot_ids = [str(x) for x in raw_ids if str(x).strip()]
    if not shot_ids:
        return web.json_response({"error": "bad_shot_ids", "message": "shot_ids 为空。"}, status=400)

    scope = str(body.get("scope") or "movie").strip().lower()
    if scope not in ("movie", "scene"):
        scope = "movie"
    scene_id = str(body.get("scene_id") or "").strip()
    try:
        fps = float(body.get("fps") or 24.0)
    except (TypeError, ValueError):
        fps = 24.0
    fps = max(0.1, fps)
    prefix = str(body.get("prefix") or "").strip() or (
        "MiniMax_Studio_Scene" if scope == "scene" else "MiniMax_Studio_Movie"
    )
    skip_missing = bool(body.get("skip_missing", False))

    from .stream_export import (
        ffmpeg_bin,
        make_stream_dir,
        merge_segment_mp4s,
        resolve_movie_output_path,
        save_segment_mp4,
    )

    root = os.path.join(folder_paths.get_output_directory(), "minimax_seg_cache", node_id)
    # 扫描缓存：shot id → (pt 路径, 段索引, mtime)。同 id 多索引取最新生成的，
    # 防时间线增删后旧索引残留被误导出（id 身份校验，与 segment_status 一致）。
    id_to_seg: dict[str, tuple[str, int, float]] = {}
    if os.path.isdir(root):
        for name in os.listdir(root):
            m = re.fullmatch(r"seg_(\d{4,})\.pt", name)
            if not m:
                continue
            idx = int(m.group(1))
            cid = _segment_cache_shot_id(root, idx)
            if not cid:
                continue
            pt_path = os.path.join(root, name)
            mt = os.path.getmtime(pt_path)
            prev = id_to_seg.get(cid)
            if prev is None or mt > prev[2]:
                id_to_seg[cid] = (pt_path, idx, mt)

    ordered: list[tuple[str, str, int]] = []  # (shot_id, pt_path, idx) 保持 shot_ids 顺序
    missing: list[dict] = []
    for order, sid in enumerate(shot_ids):
        hit = id_to_seg.get(sid)
        if hit is None:
            missing.append({"shotId": sid, "order": order})
        else:
            ordered.append((sid, hit[0], hit[1]))

    if missing and not skip_missing:
        return web.json_response(
            {
                "error": "not_cached",
                "missing": missing,
                "message": f"有 {len(missing)} 个镜头尚未生成。",
            },
            status=200,
        )
    if not ordered:
        return web.json_response(
            {"error": "nothing_to_export", "message": "没有可导出的已完成镜头。"},
            status=200,
        )

    if not ffmpeg_bin():
        return web.json_response(
            {
                "error": "ffmpeg_missing",
                "message": (
                    "ffmpeg 不可用，无法合并导出片段。请在 PATH 安装 FFmpeg 或 "
                    "`pip install imageio-ffmpeg`，再重新导出。"
                ),
            },
            status=500,
        )

    import torch

    def _load_tensor(pt_path: str):
        try:
            t = torch.load(pt_path, map_location="cpu", weights_only=True)
            if isinstance(t, torch.Tensor) and t.ndim == 4 and int(t.shape[0]) > 0:
                return t
        except Exception:
            return None
        return None

    first = _load_tensor(ordered[0][1])
    if first is None:
        return web.json_response(
            {"error": "invalid_cache", "message": "首段缓存帧不是有效视频张量。"},
            status=500,
        )
    width, height = int(first.shape[2]), int(first.shape[1])
    del first

    tmp_dir = make_stream_dir(node_id)
    seg_mp4s: list[str] = []
    for _sid, pt_path, idx in ordered:
        mp4 = os.path.join(tmp_dir, f"seg_{idx:04d}.mp4")
        # 复用本次运行已编码的临时产物（防重复编码）；临时目录每次导出清空。
        if os.path.isfile(mp4) and os.path.getmtime(mp4) >= os.path.getmtime(pt_path):
            seg_mp4s.append(mp4)
            continue
        tensor = _load_tensor(pt_path)
        if tensor is None:
            return web.json_response(
                {"error": "invalid_cache", "message": f"镜头段 {idx} 缓存帧不是有效视频张量。"},
                status=500,
            )
        audio = _load_segment_audio_for_download(root, idx)
        tmp = mp4 + f".tmp.{os.getpid()}"
        try:
            save_segment_mp4(tmp, tensor, audio, fps=fps)
            if os.path.exists(mp4):
                os.remove(mp4)
            os.replace(tmp, mp4)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        del tensor
        seg_mp4s.append(mp4)

    out_path = resolve_movie_output_path(width, height, prefix=prefix)
    merge_segment_mp4s(seg_mp4s, out_path)

    filename = os.path.basename(out_path)
    log.info(
        "MiniMax H3 Director export done: scope=%s segments=%d -> %s%s",
        scope,
        len(seg_mp4s),
        filename,
        f" (skipped {len(missing)} missing)" if missing else "",
    )
    return web.json_response({
        "path": out_path,
        "filename": filename,
        "subfolder": "",
        "type": "output",
        "scope": scope,
        "sceneId": scene_id,
        "segments": len(seg_mp4s),
        "missing": missing,
    })


async def minimax_director_projects(request):
    """GET 列表 / POST 新建。正式用户流程的「打开项目」「新建项目」。"""
    if request.method == "POST":
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)
        name = str(body.get("name") or "").strip()
        proj = create_project(name or "未命名项目")
        return web.json_response(proj)
    try:
        return web.json_response(list_projects())
    except Exception as exc:
        log.warning("list_projects failed: %s", exc)
        return web.json_response({"error": "list_failed", "message": f"{exc}"}, status=500)


async def minimax_director_project(request):
    """GET /project/{pid} 读 ProjectModel；POST /project/{pid} 保存 ProjectModel。"""
    pid = request.match_info.get("pid", "")
    if not pid:
        return web.json_response({"error": "bad_id", "message": "缺少项目 id。"}, status=400)
    if request.method == "POST":
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)
        ok = save_project(pid, body)
        return web.json_response({"ok": ok, "id": pid}, status=200 if ok else 500)
    data = load_project(pid)
    if data is None:
        return web.json_response({"error": "not_found", "message": f"项目 {pid} 不存在。"}, status=404)
    return web.json_response(data)


async def minimax_director_snapshots(request):
    """GET 快照列表。「导入 ComfyUI 工程」用它列出最近生成过的工程。"""
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError:
        limit = 50
    try:
        return web.json_response(list_snapshots(limit=limit))
    except Exception as exc:
        log.warning("list_snapshots failed: %s", exc)
        return web.json_response({"error": "list_failed", "message": f"{exc}"}, status=500)


async def minimax_director_snapshot(request):
    """GET /snapshot/{sid} 读某次生成时的 timeline_data 原文。"""
    sid = request.match_info.get("sid", "")
    if not sid:
        return web.json_response({"error": "bad_id", "message": "缺少快照 id。"}, status=400)
    data = load_snapshot(sid)
    if data is None:
        return web.json_response({"error": "not_found", "message": f"快照 {sid} 不存在。"}, status=404)
    return web.json_response(data)


async def minimax_director_project_delete(request):
    """DELETE /project/{pid} 删除项目。"""
    pid = request.match_info.get("pid", "")
    if not pid:
        return web.json_response({"error": "bad_id", "message": "缺少项目 id。"}, status=400)
    ok = delete_project(pid)
    return web.json_response({"ok": ok, "id": pid}, status=200 if ok else 404)


async def minimax_director_snapshot_delete(request):
    """DELETE /snapshot/{sid} 删除快照。"""
    sid = request.match_info.get("sid", "")
    if not sid:
        return web.json_response({"error": "bad_id", "message": "缺少快照 id。"}, status=400)
    ok = delete_snapshot(sid)
    return web.json_response({"ok": ok, "id": sid}, status=200 if ok else 404)


async def minimax_director_script_import(request):
    """V1.7 Phase 1 · Commit 3：POST /minimax/director/script/import。

    剧本（.md/.txt 纯文本）→ 规则拆 Scene/Shot（script_parser）→ Qwen 语义补全
    （script_analyzer，真实 Ollama qwen3:14b 单 session；失败自动降级为规则结果 + warnings）。
    返回 ProductionPlan（to_dict）+ rule_only（是否只走了规则、无 Qwen 补全）+ warnings。
    正式用户流程入口，前端拿到 plan 做审核预览，「应用为 SPA 项目」后才落盘。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    script_text = str(body.get("script_text") or "").strip()
    if not script_text:
        return web.json_response(
            {"error": "empty_script", "message": "剧本内容为空（script_text 必填）。"}, status=400
        )
    title = str(body.get("title") or "").strip()
    source_file = str(body.get("source_file") or "").strip()
    analyze = bool(body.get("analyze", True))

    # 规则拆 Scene/Shot（纯本地，Qwen 前就有完整结构）
    try:
        plan = await asyncio.to_thread(parse_script, script_text, source_file, title)
    except Exception as exc:  # pragma: no cover - 规则解析不应抛
        log.exception("script_import parse failed")
        return web.json_response({"error": "parse_failed", "message": str(exc)}, status=500)

    rule_only = False
    if analyze and plan.scenes:
        # Qwen 语义补全：真实 Ollama 单 session（显存安全门内）。失败降级规则结果不阻塞导入。
        try:
            plan = await asyncio.to_thread(analyze_script, plan)
        except Exception as exc:
            log.warning("script_import analyze failed, keep rule result: %s", exc)
            rule_only = True

    return web.json_response({
        "plan": plan.to_dict(),
        "rule_only": rule_only,
        "warnings": list(plan.validation.warnings),
    })


async def minimax_director_story_import(request):
    """P1-B-5 + P2-P4（#541）：POST /minimax/director/story/import。

    小说章节正文（自然语言，零标记）→ Qwen 整章理解（extract_beats，真实 Ollama
    单 session；失败自动降级为规则结果 + warnings）→ 规则编排（build_plan_from_beats：
    Beat 数量约束/超限合并 → 覆盖补齐 → 地点规范化 → Shot Blueprint → Shot 独立
    Location → timeline + beats）。返回 ProductionPlan（to_dict，含 beats + timeline）
    + rule_only + warnings，与 /script/import 同构，前端复用审核预览骨架。

    正式用户流程入口：粘贴小说正文 → Qwen 剧情结构（Beats + dramatic_function）→
    规则拆 Shot（导演调度）→ 审核预览「📖 小说章节」→ 应用为 SPA 项目。

    P2-P4 扩展：body 可选 `project_id` + `episode_number`。
    有 project_id → ensure_bible → extract_beats 注入全量已确认上下文（P2-P3）→
    导入后 merge_plan_into_bible 更新 Bible（version+1 原子落盘）→ 响应追加
    bible + bible_updates。无 project_id → 行为与 P1-B 逐字一致（响应仅三键，
    extract_beats 调用签名亦不带 bible，回归锁死）。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    story_text = str(body.get("story_text") or "").strip()
    if not story_text:
        return web.json_response(
            {"error": "empty_story", "message": "小说正文为空（story_text 必填）。"}, status=400
        )
    title = str(body.get("title") or "").strip()
    source_file = str(body.get("source_file") or "").strip()
    analyze = bool(body.get("analyze", True))
    project_id = str(body.get("project_id") or "").strip()
    episode_number = body.get("episode_number")
    try:
        ep_num = int(episode_number) if episode_number not in (None, "") else 1
        if ep_num < 1:
            ep_num = 1
    except (TypeError, ValueError):
        ep_num = 1

    # Qwen 整章理解（B-1）：真实 Ollama 单 session（显存安全门内）。失败降级规则不阻塞。
    backend = None
    if analyze:
        try:
            from .text_backends import create_default_text_backend
            backend = create_default_text_backend()
        except Exception as exc:  # pragma: no cover - 后端创建失败走纯规则退化
            log.warning("story_import backend create failed, rule fallback: %s", exc)
            backend = None

    # P2-P4：有 project_id → 加载/新建项目 Bible（全量注入已确认上下文）
    bible = None
    if project_id:
        from .bible_store import ensure_bible

        bible = ensure_bible(project_id)

    try:
        if bible is not None:
            beats, rule_only, warnings = await asyncio.to_thread(
                extract_beats, story_text, backend, title=title, bible=bible
            )
        else:
            beats, rule_only, warnings = await asyncio.to_thread(
                extract_beats, story_text, backend, title=title
            )
        plan = await asyncio.to_thread(
            build_plan_from_beats, story_text, beats, warnings, title=title
        )
    except Exception as exc:  # pragma: no cover - 编排不应抛（内部已有退化/校验）
        log.exception("story_import failed")
        return web.json_response({"error": "story_import_failed", "message": str(exc)}, status=500)

    result = {
        "plan": plan.to_dict(),
        "rule_only": rule_only,
        "warnings": list(plan.validation.warnings),
    }
    # P2-P4：有 project_id → 并入 Bible（新增候选 + 动态状态累积），落盘后随响应返回
    if project_id:
        try:
            bible_payload, updates = await asyncio.to_thread(
                merge_plan_into_bible,
                project_id=project_id,
                plan=plan,
                episode_number=ep_num,
            )
            result["bible"] = bible_payload
            result["bible_updates"] = updates
        except Exception as exc:  # pragma: no cover - Bible 更新失败不阻塞导入主流程
            log.exception("story_import bible merge failed")
            return web.json_response({"error": "bible_merge_failed", "message": str(exc)}, status=500)

    return web.json_response(result)


async def minimax_director_bible_get(request):
    """P2-P4：GET /minimax/director/bible?project_id=X → {bible}。

    读项目 Bible（bible.json 后端权威）；尚未创建 → {bible: None}（前端空面板）。
    """
    project_id = str(request.query.get("project_id") or "").strip()
    if not project_id:
        return web.json_response({"error": "missing_project_id", "message": "project_id 必填。"}, status=400)
    try:
        payload = await asyncio.to_thread(get_bible_payload, project_id)
    except Exception as exc:  # pragma: no cover - 读取不应抛（bible_store 防御加载）
        log.exception("bible get failed")
        return web.json_response({"error": "bible_get_failed", "message": str(exc)}, status=500)
    return web.json_response(payload)


async def minimax_director_bible_confirm(request):
    """P2-P4：POST /minimax/director/bible/confirm → {bible}。

    body: {project_id, entity_id, action, payload}。
    action ∈ confirm / accept_attribute / merge_alias / edit_attributes
    （bible_ops.confirm_bible_action）。写回 bible.json（version+1）。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    project_id = str(body.get("project_id") or "").strip()
    entity_id = str(body.get("entity_id") or "").strip()
    action = str(body.get("action") or "").strip()
    if not project_id or not entity_id or not action:
        return web.json_response(
            {"error": "missing_field", "message": "project_id/entity_id/action 必填。"}, status=400
        )
    try:
        payload = await asyncio.to_thread(
            confirm_bible_action,
            project_id=project_id,
            entity_id=entity_id,
            action=action,
            payload=body.get("payload") or {},
        )
    except ValueError as exc:
        return web.json_response({"error": "invalid_request", "message": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover
        log.exception("bible confirm failed")
        return web.json_response({"error": "bible_confirm_failed", "message": str(exc)}, status=500)
    return web.json_response(payload)


async def minimax_director_bible_entry(request):
    """P2-P4：POST /minimax/director/bible/entry → {bible, created}。

    body: {project_id, entity_type, name, status?, attributes?, aliases?}。
    人工新建条目（source=user）；Bible 不存在自动建空。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    project_id = str(body.get("project_id") or "").strip()
    entity_type = str(body.get("entity_type") or "").strip()
    name = str(body.get("name") or "").strip()
    if not project_id or not entity_type or not name:
        return web.json_response(
            {"error": "missing_field", "message": "project_id/entity_type/name 必填。"}, status=400
        )
    try:
        payload = await asyncio.to_thread(
            create_bible_entry,
            project_id=project_id,
            entity_type=entity_type,
            name=name,
            status=str(body.get("status") or "candidate"),
            attributes=body.get("attributes") or {},
            aliases=body.get("aliases") or [],
        )
    except ValueError as exc:
        return web.json_response({"error": "invalid_request", "message": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover
        log.exception("bible entry failed")
        return web.json_response({"error": "bible_entry_failed", "message": str(exc)}, status=500)
    return web.json_response(payload)


async def minimax_director_assets_scan(request):
    """V1.7 Phase 2：POST /minimax/director/assets/scan。

    对 ProductionPlan 的剧本实体（角色/道具/地点）做资产库匹配（纯规则：
    精确 → 归一后精确 → 子串包含 → 置信度）。返回资产库列表 + 每实体匹配结果，
    前端做人工确认（低置信度弹候选）。⛔ 只匹配已有资产，绝不创建「林雪_2」。
    Phase 2-1（#135）：匹配前加载 asset_registry——用户已确认的 entity_key
    （accepted binding）直接覆盖规则匹配（match_kind="persisted"），assets 每条
    带永久 asset_id。registry 只记住确认，绝不反向生成实体。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    plan_data = body.get("plan")
    if not isinstance(plan_data, dict) or not isinstance(plan_data.get("scenes"), list):
        return web.json_response(
            {"error": "bad_plan", "message": "缺少 plan（ProductionPlan JSON，需含 scenes）。"}, status=400
        )
    try:
        plan = ProductionPlan.from_dict(plan_data)
    except Exception as exc:  # pragma: no cover - 结构非法
        return web.json_response({"error": "bad_plan", "message": str(exc)}, status=400)

    try:
        registry = default_registry()  # 不可用 → None（行为与旧版一致）
        result = await asyncio.to_thread(match_plan, plan, None, registry)
    except Exception as exc:  # pragma: no cover - 扫描/匹配不应抛
        log.exception("assets_scan failed")
        return web.json_response({"error": "scan_failed", "message": str(exc)}, status=500)
    return web.json_response(result)


async def minimax_director_assets_binding(request):
    """V1.7 Phase 2-1：POST /minimax/director/assets/binding。

    人工确认持久化（#135）：用户接受建议/选择候选后，把
    `entity_key → asset_id`（status=accepted, source=user）写入 asset_registry.json。
    下一次重新解析同一剧本时，scan 直接复用该确认，不再重新 pending。

    body:
      entity_key:  稳定实体键（character:柳如烟 / location:山雨楼外 / prop:旧剑匣）
      entity_name: 实体显示名（可选，用于新建资产记录快照）
      entity_type: 实体类型（可选，character/location/prop）
      asset_id:    永久资产 id（来自 scan 的 matches[i].asset_id；有则直接写）
      image_file:  资产相对路径（asset_id 缺失时按此分配/复用）
      asset_name:  资产名（可选，资产记录快照）
      status:      "accepted"（默认）/ "auto"
      source:      "user"（默认）/ "system"
    返回 {ok: true, binding: {entity_key, asset_id, status, source, updated_at,
    image_file, asset_name, asset_kind}}。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    ekey = str(body.get("entity_key") or "").strip()
    if not ekey:
        return web.json_response({"error": "bad_entity_key", "message": "缺少 entity_key。"}, status=400)
    asset_id = str(body.get("asset_id") or "").strip()
    image_file = str(body.get("image_file") or "").strip()
    if not asset_id and not image_file:
        return web.json_response(
            {"error": "bad_asset", "message": "缺少 asset_id 或 image_file。"}, status=400
        )
    status = str(body.get("status") or "accepted").strip() or "accepted"
    source = str(body.get("source") or "user").strip() or "user"
    try:
        registry = default_registry()
        if registry is None:
            return web.json_response(
                {"error": "registry_unavailable", "message": "资产注册表不可用。"}, status=500
            )
        # asset_id 缺失 → 按 image_file 分配/复用永久 id
        if not asset_id:
            asset_id = registry.ensure_asset_id(
                image_file,
                str(body.get("asset_name") or "").strip(),
                str(body.get("asset_kind") or body.get("entity_type") or "unknown").strip(),
            )
        binding = registry.set_binding(
            ekey, asset_id, status=status, source=source
        )
        if binding is None:
            return web.json_response(
                {"error": "bad_asset_id", "message": f"asset_id 未注册：{asset_id}"}, status=400
            )
        registry.save()
        return web.json_response({"ok": True, "binding": binding})
    except Exception as exc:  # pragma: no cover - 写盘失败不应崩
        log.exception("assets_binding failed")
        return web.json_response({"error": "binding_failed", "message": str(exc)}, status=500)


async def minimax_director_assets_bindings(request):
    """V1.7 Phase 2-1：GET /minimax/director/assets/bindings。

    返回全部已持久化确认：{bindings: {entity_key: {asset_id, status, source,
    updated_at, image_file, asset_name, asset_kind}}}。前端重新导入剧本时拉取，
    把已确认实体直接标为「已确认」复用，不重新 pending（#135）。
    """
    try:
        registry = default_registry()
        if registry is None:
            return web.json_response({"bindings": {}})
        return web.json_response({"bindings": registry.list_bindings()})
    except Exception as exc:  # pragma: no cover
        log.exception("assets_bindings failed")
        return web.json_response({"error": "bindings_failed", "message": str(exc)}, status=500)


async def minimax_director_prompt_draft(request):
    """V1.7 Phase 3：POST /minimax/director/prompt/draft。

    对 ProductionPlan 每镜生成结构化五区 Prompt 草稿（Visual/Camera/Style/Sound/Negative，
    中文，纯规则 + Prompt Template Registry 模板组合，零显存）。返回
    {template_version, drafts:[{scene_id, shot_id, generation_mode, draft:{...}}]}。
    generation_mode 是「制作计划建议」（首镜 t2v / 续镜 r2v / 强连续动作 fl2v），
    不改 SPA taskType(auto)。模板版本化：改 prompt_templates/v1.json 措辞不动解析逻辑。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    plan_data = body.get("plan")
    if not isinstance(plan_data, dict) or not isinstance(plan_data.get("scenes"), list):
        return web.json_response(
            {"error": "bad_plan", "message": "缺少 plan（ProductionPlan JSON，需含 scenes）。"}, status=400
        )
    try:
        plan = ProductionPlan.from_dict(plan_data)
    except Exception as exc:  # pragma: no cover - 结构非法
        return web.json_response({"error": "bad_plan", "message": str(exc)}, status=400)

    try:
        result = await asyncio.to_thread(build_plan_drafts, plan)
    except Exception as exc:  # pragma: no cover - 草稿生成不应抛
        log.exception("prompt_draft failed")
        return web.json_response({"error": "draft_failed", "message": str(exc)}, status=500)
    return web.json_response(result)


async def minimax_director_prompt_h3(request):
    """P0-①：POST /minimax/director/prompt/h3。

    body {plan, bindings_by_shot?, duration_by_shot?, overrides_by_shot?, style_profile?} →
    {schema: minimax-h3-project-v1, prompts: {scene_id:shot_id: H3Prompt dict}}。
    DirectorIntent → 项目 H3 Prompt Schema（三段：integrated_multimodal_description /
    overall_soundscape / non_diegetic_music + 时间轴 + [REF] 可读标签 + 结构化 references
    + provenance 三类来源标记）。纯规则零显存（与 /prompt/draft 同源之 DirectorIntent）。

    bindings_by_shot：{scene_id:shot_id: {entity_key: {asset_id, image_file, ref_image}}}，
    缺省不注入参考图（references 空、无 [REF] 行）。前端三层可追溯 + 生成链路复用。

    overrides_by_shot（P0-A #477）：{scene_id:shot_id: {visual/cameraText/style/soundText}}，
    五区编辑 → 用户最终意图覆盖层。仅覆盖 AI/规则字段（composition/camera/audio/style），
    原文事实（retained_facts/对白）绝不被覆盖 —— 保证最终 H3 Prompt 可追溯回原始小说。
    生成提交前实时重建三段式（前端把当前编辑后五区内容透传，后端重建返回）。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    plan_data = body.get("plan")
    if not isinstance(plan_data, dict) or not isinstance(plan_data.get("scenes"), list):
        return web.json_response(
            {"error": "bad_plan", "message": "缺少 plan（ProductionPlan JSON，需含 scenes）。"}, status=400
        )
    try:
        plan = ProductionPlan.from_dict(plan_data)
    except Exception as exc:  # pragma: no cover - 结构非法
        return web.json_response({"error": "bad_plan", "message": str(exc)}, status=400)

    bindings = body.get("bindings_by_shot")
    if bindings is not None and not isinstance(bindings, dict):
        return web.json_response(
            {"error": "bad_bindings", "message": "bindings_by_shot 必须是 {scene_id:shot_id: {entity_key: {...}}}。"},
            status=400,
        )
    durations = body.get("duration_by_shot")
    if durations is not None and not isinstance(durations, dict):
        return web.json_response(
            {"error": "bad_durations", "message": "duration_by_shot 必须是 {scene_id:shot_id: 秒数}。"},
            status=400,
        )
    overrides = body.get("overrides_by_shot")
    if overrides is not None and not isinstance(overrides, dict):
        return web.json_response(
            {"error": "bad_overrides", "message": "overrides_by_shot 必须是 {scene_id:shot_id: {visual/cameraText/style/soundText}}。"},
            status=400,
        )
    style_profile = body.get("style_profile")
    if style_profile is not None and not isinstance(style_profile, dict):
        return web.json_response(
            {"error": "bad_style_profile", "message": "style_profile 必须是 dict（VisualStyleProfile）。"},
            status=400,
        )

    try:
        result = await asyncio.to_thread(
            _build_plan_h3_prompts_for_route, plan, bindings, durations, overrides, style_profile
        )
    except Exception as exc:  # pragma: no cover - H3 组装不应抛
        log.exception("prompt_h3 failed")
        return web.json_response({"error": "h3_failed", "message": str(exc)}, status=500)
    return web.json_response(result)


def _build_plan_h3_prompts_for_route(plan, bindings_by_shot, duration_by_shot, overrides_by_shot=None,
                                     style_profile=None) -> dict:
    """路由线程包装：DirectorIntent → H3Prompt 字典（纯规则，避免 to_thread 传参限制）。

    style_profile（v2.0 VisualStyleProfile dict / None）：非 None 时注入全剧级画风块 +
    防崩坏双语约束（_ANTI_BREAK_RULE）；None → 旧链路输出不变（P3 前端预设选择后启用）。
    """
    from .director_intent import build_plan_intents  # noqa: PLC0415 - 延迟导入防顶层环

    intents = build_plan_intents(plan, overrides_by_shot=overrides_by_shot)
    profile = None
    if style_profile:
        from .visual_style import VisualStyleProfile  # noqa: PLC0415
        profile = VisualStyleProfile.from_dict(style_profile)
    prompts = build_plan_h3_prompts(
        intents,
        plan=plan,
        bindings_by_shot=bindings_by_shot,
        duration_by_shot=duration_by_shot,
        style_profile=profile,
    )
    # 生成约束层 v1.0（PROMPT_COMPILER_V1 §3.3）：送 H3 前的镜头硬约束检查报告。
    # 每镜 {scene_id:shot_id → [ConstraintIssue dict]}；纯规则零显存零 LLM。
    return {
        "schema": H3_SCHEMA_VERSION,
        "prompts": {k: v.to_dict() for k, v in prompts.items()},
        "constraint_check": build_constraint_check(intents),
    }


async def minimax_director_camera_templates(request):
    """V1.7 Phase 5：GET /minimax/director/camera/templates。

    返回运镜模板库（15 条：10 基础 + 5 补充，id/name/intent/template）。
    前端 Workbench 摄影分区下拉用（模板下拉 + 手编共存，Phase 5 决策 3）。
    纯规则零显存，无需 plan 参数。
    """
    try:
        result = await asyncio.to_thread(list_camera_templates)
    except Exception as exc:  # pragma: no cover - 模板库不应抛
        log.exception("camera_templates failed")
        return web.json_response({"error": "templates_failed", "message": str(exc)}, status=500)
    return web.json_response({"templates": result, "template_version": "camera-v2"})


async def minimax_director_style_presets(request):
    """v2.0 P3：GET /minimax/director/style/presets。

    返回 Visual Style 预设库（7 预设完整 dict，含 douyin_semi_realistic 默认）。
    前端 Visual Style 面板下拉数据源（方案 C：单一事实来源，前端不硬编码副本），
    选中后完整 dict 存 project.styleProfile，生成时透传 /prompt/h3 的 style_profile。
    纯规则零显存，无需 plan 参数。
    """
    try:
        result = await asyncio.to_thread(presets_payload)
    except Exception as exc:  # pragma: no cover - 预设库不应抛
        log.exception("style_presets failed")
        return web.json_response({"error": "presets_failed", "message": str(exc)}, status=500)
    return web.json_response({"presets": result, "preset_version": "visual-style-v1"})


async def minimax_director_camera_plan(request):
    """V1.7 Phase 5：POST /minimax/director/camera/plan。

    对 ProductionPlan 每镜生成运镜决策（camera 措辞 + camera_intent + camera_template），
    跨镜延续状态机：场景内逐镜传递、**场景切换重置**。返回
    {template_version, cameras:[{scene_id, shot_id, camera, camera_intent, camera_template}]}。
    纯规则零显存。前端「重新生成运镜」/验收可复用；正式导入流程走 /prompt/draft
    （其 camera 措辞与 camera_template 已由同一状态机生成）。
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    plan_data = body.get("plan")
    if not isinstance(plan_data, dict) or not isinstance(plan_data.get("scenes"), list):
        return web.json_response(
            {"error": "bad_plan", "message": "缺少 plan（ProductionPlan JSON，需含 scenes）。"}, status=400
        )
    try:
        plan = ProductionPlan.from_dict(plan_data)
    except Exception as exc:  # pragma: no cover - 结构非法
        return web.json_response({"error": "bad_plan", "message": str(exc)}, status=400)

    try:
        result = await asyncio.to_thread(build_plan_cameras, plan)
    except Exception as exc:  # pragma: no cover - 运镜决策不应抛
        log.exception("camera_plan failed")
        return web.json_response({"error": "plan_failed", "message": str(exc)}, status=500)
    return web.json_response(result)


async def minimax_generation_archive(request):
    """V1.11：生成完成后把当次输出 mp4 归档到项目 generations 目录（生成历史持久化）。

    把 ComfyUI output 目录里的成片（filename）复制一份到
    ``{input_dir}/minimax_studio/generations/{project_id}/{shot_id}/{generation_id}.mp4``。
    input 目录经 ComfyUI ``/view?type=input`` 同源可播、重启不丢，是 SPA 生成历史的
    持久视频源（output 目录是临时区，刷新/重启后可能被清理）。

    body:
      project_id      项目 id（sanitize 后作目录名，缺省字段不落盘）
      shot_id         镜头 id（sanitize）
      generation_id   生成版本 id（sanitize）
      filename        当次输出 mp4 文件名（ComfyUI output 目录 basename）
      subfolder       输出文件在 output 目录下的子目录（如 H3 的 "video"），可空
    返回 {ok, video_file}（video_file = input 相对路径，前端 comfyInputUrl 播放）。
    """
    try:
        body = await request.json()
    except (Exception, json.JSONDecodeError):
        return web.json_response({"error": "bad_json", "message": "请求体不是合法 JSON。"}, status=400)

    # 先校验原始必填字段：_safe_basename 对空串会回退 "video.mp4"，若先 sanitize 再判空
    # 守卫永远是死代码，缺参会被静默归档到伪目录（V1.11.2 #398 顺手修正）。
    raw_project_id = str(body.get("project_id") or "").strip()
    raw_shot_id = str(body.get("shot_id") or "").strip()
    raw_generation_id = str(body.get("generation_id") or "").strip()
    raw_filename = str(body.get("filename") or "").strip()
    if not raw_project_id or not raw_shot_id or not raw_generation_id or not raw_filename:
        return web.json_response(
            {"error": "bad_params", "message": "缺少 project_id/shot_id/generation_id/filename。"},
            status=400,
        )
    project_id = _safe_basename(raw_project_id)
    shot_id = _safe_basename(raw_shot_id)
    generation_id = _safe_basename(raw_generation_id)
    filename = _safe_basename(raw_filename)
    subfolder = str(body.get("subfolder") or "").strip().replace("\\", "/").strip("/")

    output_dir = folder_paths.get_output_directory()
    src = os.path.join(output_dir, subfolder, filename) if subfolder else os.path.join(output_dir, filename)
    if not os.path.isfile(src):
        return web.json_response(
            {"error": "not_found", "message": f"输出文件不存在：{filename} (subfolder={subfolder})"}, status=404
        )

    rel = os.path.join("minimax_studio", "generations", project_id, shot_id, f"{generation_id}.mp4")
    dst = os.path.join(folder_paths.get_input_directory(), rel)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    except Exception as exc:
        log.warning("MiniMax H3 Director generation archive failed: %s", exc)
        return web.json_response({"error": "archive_failed", "message": str(exc)}, status=500)
    log.info("MiniMax H3 Director generation archived: %s", rel.replace(os.sep, "/"))
    return web.json_response({"ok": True, "video_file": rel.replace(os.sep, "/")})


# ---------------------------------------------------------------------------
# Phase 0 TTS（Edge-TTS 管线测试引擎）：音色列表 + 单镜 TTS 合成落盘
# ---------------------------------------------------------------------------
async def minimax_director_tts_voices(request):
    """GET /minimax/director/tts/voices → 可用音色列表（前端 Voice Cast 面板数据源）。"""
    try:
        backend = create_default_tts_backend()
        voices = [v.to_dict() for v in backend.list_voices()]
        return web.json_response({"ok": True, "engine": backend.name, "voices": voices})
    except Exception as exc:  # pragma: no cover - 防御
        log.warning("TTS voices 查询失败: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def minimax_director_tts_synthesize(request):
    """POST /minimax/director/tts/synthesize → 单镜 TTS 落盘（分层目录 + manifest.json）。

    body:
        project_name: 项目名（目录名，含中文保留）
        shot_id:      镜头 id
        scene_id / ambient / music: 可选（manifest 透传，供 Phase 1 混音参考）
        lines: [{text, speaker?, voice_type?, voice_id?, emotion?, delivery?}, ...]
        或 audio_intent: {shot_id, scene_id, lines, ...}（AudioIntent.to_dict 格式）
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad_json"}, status=400)
    project_name = str(body.get("project_name") or body.get("project") or "").strip()
    # audio_intent dict 形式（AudioIntent.to_dict）：shot_id 顶层优先，否则从 dict 兜底
    intent_dict = body.get("audio_intent") or {}
    shot_id = str(body.get("shot_id") or intent_dict.get("shot_id") or "").strip()
    if not project_name or not shot_id:
        return web.json_response({"ok": False, "error": "bad_params"}, status=400)

    lines_data = body.get("lines")
    if lines_data is None:
        lines_data = intent_dict.get("lines", [])
    lines: list[VoiceLine] = []
    for item in (lines_data or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        lines.append(
            VoiceLine(
                text=text,
                speaker=str(item.get("speaker", "")).strip(),
                voice_type=VoiceType.normalize(item.get("voice_type", "")),
                voice_id=str(item.get("voice_id", "") or "").strip(),
                emotion=str(item.get("emotion", "") or "").strip(),
                delivery=str(item.get("delivery", "") or "").strip(),
            )
        )
    intent = AudioIntent(
        shot_id=shot_id,
        scene_id=str(body.get("scene_id", "") or intent_dict.get("scene_id", "") or ""),
        ambient=str(body.get("ambient", "") or intent_dict.get("ambient", "") or ""),
        music=str(body.get("music", "") or intent_dict.get("music", "") or ""),
        lines=lines,
    )
    backend = create_default_tts_backend()
    try:
        backend.preflight()
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    try:
        summary = await synthesize_shot(intent, project_name, backend=backend)
    except Exception as exc:
        log.warning("TTS 合成失败: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    return web.json_response({"ok": True, **summary})


async def minimax_director_tts_mix(request):
    """POST /minimax/director/tts/mix → FFmpeg ducking 混音成片。

    输入（分层目录已就绪）：
        shot_dir = output/minimax_studio/projects/{project_name}/shots/{shot_id}/
            video.mp4      H3 生成视频（必须已有）
            manifest.json  TTS 落盘时写出（必须已有；本路由读它拿台词清单）
            tts/line_NNN.wav  每行台词（必须已有，缺失报错定位）

    body:
        project_name: 项目名（目录名，含中文保留）
        shot_id:      镜头 id
        mode:         h3_tts（默认，H3 原音+TTS 叠加+ducking）| h3_only（纯 H3 原样拷贝）
        video_filename / video_subfolder: 可选。Phase 2-E（#577）自动链：H3 生成完成后
            前端把成片定位信息随 mix 提交；若 shot 分层目录缺 video.mp4，则先从 ComfyUI
            output 目录拷入（H3 侧产物），再混音。已存在则忽略（幂等重混音）。

    成功 → {ok, mode, shot_dir, video_file, h3_audio_file, final_file,
            final_rel（output 根相对路径，前端 /view?type=output 直接播放）,
            line_count, lines[]}
    失败 → 400 bad_json/bad_params | 500（ffmpeg 缺失 / 缺 video/manifest/tts 文件 / ffmpeg 执行失败）
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad_json"}, status=400)
    project_name = str(body.get("project_name") or body.get("project") or "").strip()
    shot_id = str(body.get("shot_id") or "").strip()
    if not project_name or not shot_id:
        return web.json_response({"ok": False, "error": "bad_params"}, status=400)
    mode = str(body.get("mode") or "h3_tts").strip()
    if mode not in ("h3_tts", "h3_only"):
        return web.json_response(
            {"ok": False, "error": "bad_params",
             "message": f"mode 仅支持 h3_tts / h3_only，收到：{mode}"},
            status=400,
        )
    shot_dir = shot_output_dir(project_name, shot_id)
    # Phase 2-E（#577）：分层目录缺 video.mp4 时，从前端给的 H3 成片定位自动落位（幂等）。
    video_path = os.path.join(shot_dir, "video.mp4")
    if not os.path.isfile(video_path):
        video_filename = str(body.get("video_filename") or "").strip()
        video_subfolder = str(
            (body.get("video_subfolder") or "").strip().replace("\\", "/").strip("/")
        )
        if video_filename:
            src = (
                os.path.join(
                    folder_paths.get_output_directory(), video_subfolder, video_filename
                )
                if video_subfolder
                else os.path.join(folder_paths.get_output_directory(), video_filename)
            )
            if os.path.isfile(src):
                try:
                    os.makedirs(shot_dir, exist_ok=True)
                    shutil.copy2(src, video_path)
                except OSError as exc:
                    log.warning("TTS 成片落位失败（继续尝试混音）: %s", exc)
    try:
        result = mix_shot(shot_dir, mode=mode)
    except Exception as exc:
        log.warning("TTS 混音失败: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    # Phase 2-E（#577）：附加 output 根相对路径，前端 /view?type=output 直接播放 final.mp4。
    try:
        final_file = str(result.get("final_file") or "")
        if final_file:
            out_root = folder_paths.get_output_directory()
            rel = os.path.relpath(final_file, out_root).replace("\\", "/")
            if not rel.startswith(".."):
                result["final_rel"] = rel
    except Exception:  # pragma: no cover - 相对路径附加失败不影响混音结果
        pass
    return web.json_response({"ok": True, **result})


def register_routes() -> bool:
    """Register MiniMax H3 Director HTTP routes on the ComfyUI PromptServer."""
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return True

    server = PromptServer.instance
    if server is None:
        log.warning("MiniMax H3 Director: PromptServer not ready, HTTP routes not registered")
        return False

    routes = server.routes
    _register_route(routes, "POST", "/minimax/director/upload_chunk", minimax_upload_video_chunk)
    _register_route(routes, "POST", "/minimax/director/probe_video", minimax_probe_video)
    _register_route(routes, "GET", "/minimax/director/probe_video", minimax_probe_video)
    _register_route(routes, "POST", "/minimax/director/detect_shots", minimax_detect_shots)
    _register_route(routes, "GET", "/minimax/director/segment_cache_status", minimax_segment_cache_status)
    _register_route(routes, "GET", "/minimax/director/segment_status", minimax_segment_status)
    _register_route(routes, "GET", "/minimax/director/segment_mp4", minimax_segment_mp4)
    # V1.6-A 成片导出（流式 scene/movie 合并）
    _register_route(routes, "POST", "/minimax/director/export", minimax_director_export)
    # MiniMax Studio 工程文件层（V1.1.1）
    _register_route(routes, "GET", "/minimax/director/projects", minimax_director_projects)
    _register_route(routes, "POST", "/minimax/director/projects", minimax_director_projects)
    _register_route(routes, "GET", "/minimax/director/project/{pid}", minimax_director_project)
    _register_route(routes, "POST", "/minimax/director/project/{pid}", minimax_director_project)
    _register_route(routes, "DELETE", "/minimax/director/project/{pid}", minimax_director_project_delete)
    _register_route(routes, "GET", "/minimax/director/snapshots", minimax_director_snapshots)
    _register_route(routes, "GET", "/minimax/director/snapshot/{sid}", minimax_director_snapshot)
    _register_route(routes, "DELETE", "/minimax/director/snapshot/{sid}", minimax_director_snapshot_delete)
    # V1.7 Phase 1 · Commit 3 剧本导入（规则拆 + Qwen 语义补全 → ProductionPlan 审核预览）
    _register_route(routes, "POST", "/minimax/director/script/import", minimax_director_script_import)
    # P1-B-5 小说章节导入（Qwen 整章理解 → Beats → Timeline → Shots → 审核预览）
    _register_route(routes, "POST", "/minimax/director/story/import", minimax_director_story_import)
    # P2-P4 Global Story Bible：跨章世界状态库读写（Bible 面板）
    _register_route(routes, "GET", "/minimax/director/bible", minimax_director_bible_get)
    _register_route(routes, "POST", "/minimax/director/bible/confirm", minimax_director_bible_confirm)
    _register_route(routes, "POST", "/minimax/director/bible/entry", minimax_director_bible_entry)
    _register_route(routes, "POST", "/minimax/director/assets/scan", minimax_director_assets_scan)
    # V1.7 Phase 2-1 人工确认持久化（Asset Identity + Persistent Binding Checkpoint，#135）
    _register_route(routes, "POST", "/minimax/director/assets/binding", minimax_director_assets_binding)
    _register_route(routes, "GET", "/minimax/director/assets/bindings", minimax_director_assets_bindings)
    # V1.7 Phase 3 结构化生产 Prompt 草稿（五区中文草稿 + generation_mode 建议 + 模板版本化）
    _register_route(routes, "POST", "/minimax/director/prompt/draft", minimax_director_prompt_draft)
    # P0-① 项目 H3 Prompt Schema（DirectorIntent → 三段 H3 + 结构化 references + provenance）
    _register_route(routes, "POST", "/minimax/director/prompt/h3", minimax_director_prompt_h3)
    # V1.7 Phase 5 运镜模板库 + 跨镜延续状态机（模板下拉数据源 + 全计划运镜决策）
    _register_route(routes, "GET", "/minimax/director/camera/templates", minimax_director_camera_templates)
    _register_route(routes, "POST", "/minimax/director/camera/plan", minimax_director_camera_plan)
    # v2.0 P3 Visual Style 预设库（前端 Visual Style 面板下拉数据源，单一事实来源）
    _register_route(routes, "GET", "/minimax/director/style/presets", minimax_director_style_presets)
    # V1.11 生成历史：生成完成后归档当次 mp4 到项目 generations 目录（持久化回看）
    _register_route(routes, "POST", "/minimax/director/generations/archive", minimax_generation_archive)
    # Phase 0 TTS（Edge-TTS 管线测试引擎）：音色列表 + 单镜 TTS 合成落盘（分层目录 + manifest）
    _register_route(routes, "GET", "/minimax/director/tts/voices", minimax_director_tts_voices)
    _register_route(routes, "POST", "/minimax/director/tts/synthesize", minimax_director_tts_synthesize)
    # Phase 1 混音（FFmpeg ducking：H3 原音 + TTS 人声叠加 + sidechaincompress）
    _register_route(routes, "POST", "/minimax/director/tts/mix", minimax_director_tts_mix)
    _ROUTES_REGISTERED = True
    log.info("MiniMax H3 Director HTTP routes registered")
    return True
