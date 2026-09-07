"""MiniMax H3 Director —generation timeline (t2i / t2v / i2i / i2v) plan building."""

from __future__ import annotations

import logging

import torch

from ..lib.image_prep import fit_canvas, fit_video_long_edge, cat_frames_variable_size, resolve_output_dimensions
from ..lib.task_prompts import resolve_task_key

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.gen")

GEN_BLANK_KEYS = frozenset({"t2v", "r2v"})
GEN_IMAGE_KEYS = frozenset({"i2v"})
FL2V_KEYS = frozenset({"fl2v"})
GEN_TASK_KEYS = GEN_BLANK_KEYS | GEN_IMAGE_KEYS | FL2V_KEYS
PROMPT_BATCH_KEYS = frozenset({"t2v", "i2v", "r2v", "fl2v"})
VIDEO_BATCH_KEYS = frozenset({"t2v", "i2v", "r2v", "fl2v"})
IMAGE_BATCH_KEYS = frozenset()

MIN_GEN_FRAMES = 1
MIN_GEN_VIDEO_FRAMES = 4


def is_gen_task_key(task_key: str) -> bool:
    return task_key in GEN_TASK_KEYS


def is_gen_timeline(timeline: dict, task_key: str) -> bool:
    mode = str(timeline.get("timelineMode") or "").lower()
    if mode in ("gen_blank", "gen_image", "image_batch", "prompt_batch", "fl2v"):
        return True
    if mode == "video":
        return False
    return is_gen_task_key(task_key)


def is_prompt_batch_timeline(timeline: dict, task_key: str) -> bool:
    mode = str(timeline.get("timelineMode") or "").lower()
    if mode in ("image_batch", "prompt_batch"):
        return True
    # fl2v is a separate strip UI but still exports like a video prompt-batch.
    if mode == "fl2v" or task_key == "fl2v":
        return True
    return task_key in PROMPT_BATCH_KEYS


def is_image_batch_timeline(timeline: dict, task_key: str) -> bool:
    return is_prompt_batch_timeline(timeline, task_key)


def is_video_batch_task_key(task_key: str) -> bool:
    return task_key in VIDEO_BATCH_KEYS


def gen_submode(timeline: dict, task_key: str) -> str:
    mode = str(timeline.get("timelineMode") or "").lower()
    if mode == "gen_image" or task_key in GEN_IMAGE_KEYS:
        return "gen_image"
    if mode == "gen_blank" or task_key in GEN_BLANK_KEYS:
        return "gen_blank"
    return "gen_blank"


def _min_frames_for_task(task_key: str) -> int:
    if task_key in IMAGE_BATCH_KEYS or task_key in ("t2i", "i2i"):
        return MIN_GEN_FRAMES
    if task_key in ("t2v", "i2v", "r2v"):
        return MIN_GEN_VIDEO_FRAMES
    return MIN_GEN_VIDEO_FRAMES


def _segment_frame_count(raw: dict, *, default: int, task_key: str) -> int:
    fc = int(raw.get("frameCount") or raw.get("frame_count") or raw.get("length") or default)
    return max(_min_frames_for_task(task_key), fc)


def _match_asset_by_name(assets, text: str):
    """提示词命名自动匹配（Phase B 增强）：text 中出现资产名时返回该资产，否则 None。
    仅匹配名字长度 ≥2 的资产，避免单字误匹配。作为前端自动匹配的后端兜底。"""
    if not assets or not text:
        return None
    for asset in assets:
        name = (asset.name or "").strip()
        if len(name) < 2:
            continue
        if name in text:
            return asset
    return None


# ---------- V1.2.7：Prompt 媒体引用标签（<picture>/<video>/<audio>） ----------
# 前端 @ 补全插入的显式素材引用标签。语法：
#   <picture>素材名</picture> — 参考图（资产图 / 段 refImages）
#   <video>素材名</video>     — 参考视频（段 refVideos）
#   <audio>素材名</audio>     — 参考音频（段 refAudios）
# 标签是纯文本语法（随 Prompt 持久化），后端在此解析并匹配素材。

_MEDIA_TAG_RE = __import__("re").compile(r"<(picture|video|audio)>([^<>]+)</\1>")


def parse_media_tags(text: str) -> list[tuple[str, str, int, int]]:
    """解析文本中所有媒体引用标签，返回 [(kind, name, tag_start, tag_end)]。

    kind ∈ {"picture", "video", "audio"}；name 为标签内名字（去首尾空白）。
    开闭标签不匹配或名字为空的忽略。
    """
    out: list[tuple[str, str, int, int]] = []
    for m in _MEDIA_TAG_RE.finditer(text or ""):
        name = m.group(2).strip()
        if not name:
            continue
        out.append((m.group(1), name, m.start(), m.end()))
    return out


def strip_media_tags(text: str) -> str:
    """剥离媒体标签壳，保留内部名字文字：<picture>林雪</picture> → 林雪。

    这样提示词既无 `<picture>` 这类自定义语法干扰 H3，又保留素材名字的语义
    （供资产命名匹配与模型理解）。注意：只剥壳不改名，名字本身留在文本里。
    """
    if not text:
        return text
    return _MEDIA_TAG_RE.sub(lambda m: m.group(2).strip(), text)


def _match_asset_exact(assets, name: str):
    """媒体标签的资产精确匹配：名字相等或相互包含（名长 ≥2）。"""
    name = (name or "").strip()
    if not name or len(name) < 2:
        return None
    for asset in assets:
        aname = (asset.name or "").strip()
        if not aname or len(aname) < 2:
            continue
        if name == aname or name in aname or aname in name:
            return asset
    return None


def _dedup_cast_ids(cast_pool, ids: list[str]) -> list[str]:
    """V1.6-B canonical identity 去重：按资产名 trim+lowercase 判重，保留池顺序。

    显式选中的 castIds 与 @ 命中的 cast 合并时，同一角色（同名）只进一次，
    避免「显式选中 + 提示词 @ 命中」重复注入。场景池优先（cast_pool 顺序）。
    id 在池中匹配不到的丢弃。
    """
    seen: set[str] = set()
    out: list[str] = []
    for _cid in ids:
        if not _cid:
            continue
        _a = next((a for a in cast_pool if a.id == _cid), None)
        if _a is None:
            continue
        _key = (_a.name or "").strip().lower()
        if not _key:
            _key = _cid.strip().lower()
        if _key in seen:
            continue
        seen.add(_key)
        out.append(_cid)
    return out


def _seg_id_list(seg_data: dict, *keys: str) -> list[str]:
    """P0-1：从段数据解析显式 ids 数组（per-shot 资产边界）。

    兼容前端字样 propIds/prop_ids（以及复数的 _ids），非 list/空值 → 空列表。
    只做「读取」，不做场景池行为——被某段显式列出的 id 才可能进入该段 refs。
    """
    for _k in keys:
        _raw = seg_data.get(_k)
        if isinstance(_raw, list):
            return [str(x).strip() for x in _raw if str(x).strip()]
        if isinstance(_raw, str) and _raw.strip():
            return [_raw.strip()]
    return []


def _load_scene_groups(scene_list: list[dict]) -> list["SceneGroup"]:
    """解析 timeline.scenes → 场景列表（Scene Manager 一级对象）。

    每个场景携带独立素材组（角色/场景/道具/风格参考），资产加载复用
    _load_global_assets（同 GlobalAsset 结构）。scene 无 id 的丢弃。

    scene schema:
        { id, name, location, time, order,
          assets: { cast:[{id,name,imageFile}], locations:[...], props:[...], styles:[...] },
          defaultCastId, defaultLocationId }
    """
    from .plan import SceneGroup, _load_global_assets

    scenes: list[SceneGroup] = []
    for i, sc in enumerate(scene_list or []):
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id") or "").strip()
        if not sid:
            continue
        assets_block = sc.get("assets") or {}
        assets: dict[str, list] = {}
        for kind_key, kind in (("cast", "cast"), ("locations", "location"),
                               ("props", "prop"), ("styles", "style")):
            # 资产键统一：locations 优先，旧数据 location（单数）回退。
            src = assets_block.get(kind_key)
            if src is None and kind_key == "locations":
                src = assets_block.get("location")
            loaded = _load_global_assets(src or [], kind=kind)
            if loaded:
                assets[kind_key] = loaded
        scenes.append(SceneGroup(
            id=sid,
            name=str(sc.get("name") or f"Scene {i + 1:02d}"),
            location=str(sc.get("location") or ""),
            time=str(sc.get("time") or ""),
            order=int(sc.get("order") or i),
            assets=assets,
            default_cast_id=str(sc.get("defaultCastId") or sc.get("default_cast_id") or "").strip(),
            default_location_id=str(sc.get("defaultLocationId") or sc.get("default_location_id") or "").strip(),
        ))
    return scenes


def _scene_asset_pool(scene: "SceneGroup | None", kind_key: str, global_assets: list) -> list:
    """场景素材组候选池：场景素材组优先，其次全局资产库（scene → global 合并去重）。

    Scene Manager 三级资产结构：Global Asset Library → Scene Asset Group → Shot Reference。
    段解析 cast/location 资产时，优先在本段归属场景的素材组里匹配，匹配不到回退全局。
    """
    if not scene:
        return list(global_assets or [])
    scene_pool = list((scene.assets.get(kind_key) or []) or [])
    seen = {a.id for a in scene_pool if a.id}
    for a in global_assets or []:
        if a.id not in seen:
            scene_pool.append(a)
            seen.add(a.id)
    return scene_pool


# 阶段 D 智能路由（FL2VA 强连续镜头关键词）。命中任一即判为"动作连续"，
# auto 模式下该 r2v 段改走 fl2v 首尾帧硬锁路径。用词尽量具体，避免误伤普通镜头。
_STRONG_CONTINUITY_KEYWORDS: tuple[str, ...] = (
    "开门", "关门", "开窗", "关门", "推门", "拉开", "拔出", "抽剑", "拔剑",
    "收剑", "挥剑", "斩下", "劈", "刺出", "挥舞",
    "变身", "变形", "进化", "觉醒", "合体", "分裂",
    "跳跃", "跃起", "起跳", "落地", "翻越", "跨过", "跳过", "攀爬", "爬升",
    "奔跑", "冲刺", "疾跑", "追逐", "逃跑", "追赶",
    "打斗", "搏斗", "交手", "格斗", "对打", "缠斗",
    "翻滚", "旋转", "翻身", "翻身跃起",
    "穿过", "横穿", "走到", "走过", "移向", "移动到", "走向",
    "从镜头外", "破门而入", "冲进",
)


def _is_strong_continuity_prompt(text: str) -> bool:
    """阶段 D 路由判定：提示词是否含强连续动作关键词（→ FL2VA）。"""
    if not text:
        return False
    for kw in _STRONG_CONTINUITY_KEYWORDS:
        if kw in text:
            return True
    return False


def _gen_segment_ranges(
    segments: list[dict],
    *,
    default_frame_count: int,
    task_key: str,
) -> list[tuple[int, int, dict]]:
    ranges: list[tuple[int, int, dict]] = []
    start = 0
    for raw in segments:
        fc = _segment_frame_count(raw, default=default_frame_count, task_key=task_key)
        ranges.append((start, start + fc, raw))
        start += fc
    if not ranges:
        fc = max(_min_frames_for_task(task_key), default_frame_count)
        ranges.append((0, fc, {}))
    return ranges


def _resolve_gen_image_ref(
    seg_data: dict,
    *,
    edit_mode: str,
    global_block: dict,
) -> dict | None:
    if edit_mode == "segment":
        img = seg_data.get("genImage") or {}
        if img.get("imageFile") or img.get("imageB64"):
            return img
        if seg_data.get("imageFile"):
            return {"imageFile": seg_data["imageFile"]}
        return None
    img = global_block.get("genImage") or {}
    if img.get("imageFile") or img.get("imageB64"):
        return img
    if global_block.get("imageFile"):
        return {"imageFile": global_block["imageFile"]}
    return None


def _load_gen_image_tensor(ref: dict) -> torch.Tensor:
    from .plan import load_reference_tensor

    tensor = load_reference_tensor(ref)
    if tensor is None:
        raise ValueError("Generation segment image could not be loaded.")
    return tensor


def _build_i2v_source_clip(
    img: torch.Tensor,
    _frame_count: int,
    *,
    width: int,
    height: int,
    output_mode: str,
    ref_max_size: int,
) -> torch.Tensor:
    """Use the source image as a one-frame source-video context."""
    if img.ndim == 3:
        img = img.unsqueeze(0)
    if output_mode == "fixed":
        return fit_canvas(img, width, height)
    return fit_video_long_edge(img, ref_max_size)


def _resolve_gen_image_source_dims(
    segment_ranges: list[tuple[int, int, dict]],
    global_block: dict,
    output_block: dict,
) -> tuple[int, int]:
    sw = int(global_block.get("sourceWidth") or output_block.get("sourceWidth") or 0)
    sh = int(global_block.get("sourceHeight") or output_block.get("sourceHeight") or 0)
    if sw > 0 and sh > 0:
        return sw, sh
    for _start, _end, seg_data in segment_ranges:
        gi = seg_data.get("genImage") or {}
        sw = int(gi.get("width") or 0)
        sh = int(gi.get("height") or 0)
        if sw > 0 and sh > 0:
            return sw, sh
    return 0, 0


def _build_gen_source_clips(
    ranges: list[tuple[int, int, dict]],
    *,
    task_key: str,
    submode: str,
    edit_mode: str,
    global_block: dict,
    height: int,
    width: int,
    output_mode: str,
    ref_max_size: int,
) -> list[torch.Tensor]:
    chunks: list[torch.Tensor] = []
    for _start, end, seg_data in ranges:
        frame_count = end - _start
        if frame_count <= 0:
            continue
        if submode == "gen_blank":
            clip = torch.full((frame_count, height, width, 3), 0.5, dtype=torch.float32)
        else:
            ref = _resolve_gen_image_ref(seg_data, edit_mode=edit_mode, global_block=global_block)
            if ref is None:
                seg_idx = len(chunks) + 1
                raise ValueError(
                    f"Segment #{seg_idx} has no source image. "
                    "Upload an image in the generation timeline (global or per-segment)."
                )
            img = _load_gen_image_tensor(ref)
            if task_key == "i2v":
                clip = _build_i2v_source_clip(
                    img,
                    frame_count,
                    width=width,
                    height=height,
                    output_mode=output_mode,
                    ref_max_size=ref_max_size,
                )
            else:
                clip = img.repeat(frame_count, 1, 1, 1)
                if output_mode == "fixed":
                    clip = fit_canvas(clip, width, height)
                else:
                    clip = fit_video_long_edge(clip, ref_max_size)
        chunks.append(clip)
    if not chunks:
        raise ValueError("Generation timeline has no frames.")
    return chunks


def _build_gen_source_video(
    ranges: list[tuple[int, int, dict]],
    *,
    task_key: str,
    submode: str,
    edit_mode: str,
    global_block: dict,
    height: int,
    width: int,
    output_mode: str,
    ref_max_size: int,
) -> torch.Tensor:
    return cat_frames_variable_size(
        _build_gen_source_clips(
            ranges,
            task_key=task_key,
            submode=submode,
            edit_mode=edit_mode,
            global_block=global_block,
            height=height,
            width=width,
            output_mode=output_mode,
            ref_max_size=ref_max_size,
        )
    )


def build_gen_director_plan(
    timeline: dict,
    *,
    global_task_type: str,
    global_prompt: str,
    total_frames: int,
    frame_rate: float,
    width: int,
    height: int,
    ref_max_size: int,
):
    """Build DirectorPlan for generation timeline modes (lazy import avoids cycles)."""
    from .plan import (
        DirectorPlan,
        SceneGroup,
        SegmentPlan,
        _load_global_assets,
        _load_ref_audios,
        _load_ref_videos,
        _load_refs,
        _parse_run_selection,
        _resolve_export_mode,
        segment_ref_audios_for_context,
        segment_refs_for_context,
    )

    global_block = timeline.get("global") or {}
    edit_mode = timeline.get("editMode") or timeline.get("edit_mode") or "global"
    if is_prompt_batch_timeline(timeline, resolve_task_key(global_block.get("taskType") or global_task_type or "")):
        edit_mode = "segment"
    elif edit_mode not in ("global", "segment"):
        edit_mode = "global"

    task_type = global_block.get("taskType") or global_task_type or "t2v —鏂囩敓瑙嗛(Text to Video)"
    task_key = resolve_task_key(task_type)
    if not is_gen_task_key(task_key):
        raise ValueError(f"Task {task_key} is not supported on the generation timeline.")

    submode = gen_submode(timeline, task_key)
    prompt = global_block.get("prompt") or global_prompt or ""
    global_refs = _load_refs(global_block.get("refs") or [])

    # 全局资产库（Phase B）：角色库 / 场景库 + 全局默认选择。
    # timeline.assets = { cast: [{id,name,imageFile}], locations: [...],
    #                     defaultCastId, defaultLocationId }
    assets_block = timeline.get("assets") or {}
    cast_assets = _load_global_assets(assets_block.get("cast") or [], kind="cast")
    # 资产键统一：只保留 locations（复数），旧数据 location（单数）回退兼容。
    location_assets = _load_global_assets(
        assets_block.get("locations") or assets_block.get("location") or [], kind="location"
    )
    default_cast_id = str(assets_block.get("defaultCastId") or assets_block.get("default_cast_id") or "").strip()
    default_location_id = str(
        assets_block.get("defaultLocationId") or assets_block.get("default_location_id") or ""
    ).strip()

    # Scene Manager（一级对象）：timeline.scenes → 场景列表。
    # 每个场景拥有独立素材组（角色/场景/道具/风格参考），段按 sceneId 归属。
    scenes = _load_scene_groups(timeline.get("scenes") or [])
    scenes_by_id = {s.id: s for s in scenes}

    output_block = timeline.get("output") or {}
    gen_block = timeline.get("gen") or {}
    default_fc = int(gen_block.get("defaultFrameCount") or total_frames or 81)

    segment_ranges = _gen_segment_ranges(
        timeline.get("segments") or [],
        default_frame_count=default_fc,
        task_key=task_key,
    )

    if submode == "gen_blank":
        out_mode = "fixed"
        fw = int(output_block.get("width") or timeline.get("width") or width or 0)
        fh = int(output_block.get("height") or timeline.get("height") or height or 0)
        if fw < 16 or fh < 16:
            raise ValueError(
                "t2i / t2v / r2i / r2v require fixed output width and height (鈮?6, multiples of 16). "
                "Set width and height in the generation timeline output panel."
            )
        out_w, out_h, ref_max, _ = resolve_output_dimensions(
            fw,
            fh,
            mode="fixed",
            long_edge=ref_max_size,
            fixed_width=fw,
            fixed_height=fh,
        )
    else:
        out_mode = str(output_block.get("mode") or "long_edge").lower()
        if out_mode not in ("fixed", "long_edge"):
            out_mode = "long_edge"
        src_w, src_h = _resolve_gen_image_source_dims(segment_ranges, global_block, output_block)
        out_w, out_h, ref_max, out_mode = resolve_output_dimensions(
            src_w or int(width or 832),
            src_h or int(height or 480),
            mode=out_mode,
            long_edge=int(output_block.get("longEdge") or output_block.get("long_edge") or ref_max_size or 848),
            fixed_width=int(output_block.get("width") or timeline.get("width") or width),
            fixed_height=int(output_block.get("height") or timeline.get("height") or height),
        )

    export_mode = _resolve_export_mode(output_block)
    # Image prompt-batch (t2i/i2i/r2i) always merges to images list; video batch (t2v/i2v/r2v) respects export mode.
    if is_prompt_batch_timeline(timeline, task_key) and not is_video_batch_task_key(task_key):
        export_mode = "all"

    source_clips = _build_gen_source_clips(
        segment_ranges,
        task_key=task_key,
        submode=submode,
        edit_mode=edit_mode,
        global_block=global_block,
        height=out_h,
        width=out_w,
        output_mode=out_mode,
        ref_max_size=ref_max,
    )
    attach_source_clips = is_prompt_batch_timeline(timeline, task_key) and task_key in ("i2i", "i2v")
    if attach_source_clips:
        # Placeholder timeline index only —spatial data comes from each segment's source_clip.
        source_video = torch.full((len(source_clips), 16, 16, 3), 0.5, dtype=torch.float32)
    else:
        source_video = cat_frames_variable_size(source_clips)

    segments: list[SegmentPlan] = []
    for idx, (start, end, seg_data) in enumerate(segment_ranges):
        # 段唯一标识（timeline.segments[].id，SPA 镜头 uuid）。缓存指纹用它隔离
        # 跨项目/跨镜头缓存（#90：新项目空镜头生成出旧人物）。老 timeline 无 id → 空串。
        seg_id = str(seg_data.get("id") or seg_data.get("segmentId") or "").strip()
        if edit_mode == "global":
            seg_prompt = prompt
            seg_task = task_type
            seg_refs = list(global_refs)
            use_global = True
            seg_negative = ""
        else:
            use_global = False
            seg_prompt = (seg_data.get("prompt") or "").strip() or prompt
            seg_task = seg_data.get("taskType") or seg_data.get("task_type") or task_type
            # Segment / batch mode: only this group's refs —never inherit global.refs.
            try:
                seg_refs = _load_refs(seg_data.get("refs") or [])
            except Exception as _ref_exc:
                # #93：plan 阶段参考图加载失败（坏图/缺文件）带段号抛错。
                # 前端 failedShotIds 正则 (Segment N) 据此定位真实失败镜头，
                # 而不是落到上一次运行残留的 failed 状态上（s2 坏图错标 s1）。
                raise ValueError(
                    f"Segment {idx + 1} reference image could not be loaded: {_ref_exc}"
                ) from _ref_exc
            seg_negative = (
                (seg_data.get("negativePrompt") or seg_data.get("negative_prompt") or "").strip()
            )

        # V1.2.7：Prompt 媒体引用标签（<picture>/<video>/<audio>）解析。
        # 剥壳（<picture>林雪</picture> → 林雪），保留名字文字供后续资产命名匹配
        # 与 H3 理解。必须在资产命名匹配（_match_asset_by_name）之前剥壳，使标签内
        # 名字同样参与自动匹配。r2v 段的显式图片资产注入在后面池子构建后进行。
        seg_media_tags = parse_media_tags(seg_prompt)
        if seg_media_tags:
            seg_prompt = strip_media_tags(seg_prompt)

        seg_task_key = resolve_task_key(seg_task)
        # 阶段 D 智能路由（AI 导演）：每段「衔接模式」三态——auto=按关键词自动判定、
        # ref2va=强制状态驱动、fl2va=强制首尾帧硬锁。手动覆盖优先。
        # 注意：关键词路由仅对 r2v 段生效（r2v 批里混入 fl2v 段即由此而来）。
        # 架构固底④：本镜生成模式（R2V/FL2V）由 per-segment taskType 独立决定（③），
        # continuityMode 只描述「本镜怎么接上一镜」——自动续接与生成模式解耦：
        #   none   = 独立镜头（不自动续接上段；也跳过 strong-continuity 关键词路由）
        #   auto   = 自动续接（默认；关键词命中强连续动作则按 auto 路由判 fl2v，向后兼容）
        #   ref2va = 强制 Ref2VA 状态驱动续接（向后兼容）
        #   fl2va  = 强制 FL2VA 首尾帧硬锁续接（向后兼容；显式 taskType 优先）
        seg_continuity_mode = str(
            seg_data.get("continuityMode") or seg_data.get("continuity_mode") or "auto"
        ).strip().lower()
        if seg_continuity_mode not in ("auto", "none", "ref2va", "fl2va"):
            seg_continuity_mode = "auto"
        if seg_continuity_mode == "none":
            pass  # 独立镜头：不覆盖 task_key、不参与 strong-prompt 路由
        elif seg_continuity_mode == "fl2va" and seg_task_key != "fl2v":
            seg_task_key = "fl2v"
        elif seg_continuity_mode == "auto" and seg_task_key == "r2v" and _is_strong_continuity_prompt(seg_prompt):
            seg_task_key = "fl2v"
        # 状态跟踪（阶段 C）：本镜状态变更（用户填写，可选）。
        # 在 edit_mode=="global" 时读段自身（若 image_batch 段也走这里）；否则读 seg_data。
        seg_state_change = str(seg_data.get("stateChange") or seg_data.get("state_change") or "").strip()
        # 智能尾帧选择（待办④，段级三态覆盖）：缺失/"auto"/"" → None（跟随全局开关）；
        # "on"/"true"/"1" → True（强制开启）；"off"/"false"/"0" → False（强制关闭）。
        seg_smart_tail = None
        if "smartTail" in seg_data and seg_data.get("smartTail") not in (None, ""):
            _sv = str(seg_data.get("smartTail")).strip().lower()
            if _sv not in ("auto", "follow", "global", "default"):
                seg_smart_tail = _sv not in ("off", "false", "0", "no")
        # 里程碑 B：关键镜头标记（二级一致性检测，段级三态覆盖）——语义与 smartTail 一致：
        # 缺失/"auto"/"" → None（跟随自动：有资产注入即检测）；"on"/"true"/"1" → True（强制）；
        # "off"/"false"/"0" → False（跳过）。前端 dropdown 存 undefined/"auto"/true/false。
        seg_consistency_check = None
        if "consistencyCheck" in seg_data and seg_data.get("consistencyCheck") not in (None, ""):
            _cv = str(seg_data.get("consistencyCheck")).strip().lower()
            if _cv not in ("auto", "follow", "global", "default"):
                seg_consistency_check = _cv not in ("off", "false", "0", "no")
        # Scene Manager 归属：本段所属场景（timeline.scenes[].id）。
        seg_scene_id = str(seg_data.get("sceneId") or seg_data.get("scene_id") or "").strip()
        seg_scene = scenes_by_id.get(seg_scene_id) if seg_scene_id else None
        # 全局资产库（Scene Manager 三级结构）：
        # Global Asset Library → Scene Asset Group → Shot Reference。
        # 资产池合并：场景素材组优先（Scene Asset Group），其次全局默认（Global Library）。
        # 段手动选择（seg.castId/locationId）始终最高优先；其次场景默认；最后全局默认。
        seg_cast_assets: list = []
        location_asset = None
        extra_assets: list = []
        if seg_task_key == "r2v" and (cast_assets or location_assets or (seg_scene and seg_scene.assets)):
            # 候选池：场景素材组 + 全局（场景优先，全局兜底）。
            cast_pool = _scene_asset_pool(seg_scene, "cast", cast_assets)
            location_pool = _scene_asset_pool(seg_scene, "locations", location_assets)
            # 场景默认优先于全局默认。
            scene_default_cast = (seg_scene.default_cast_id or "") if seg_scene else ""
            scene_default_location = (seg_scene.default_location_id or "") if seg_scene else ""
            # V1.6-B：多角色 cast 集合。显式 castIds 数组优先；旧单值 castId 迁移
            # 为单元素；@ 命中的 cast（<picture> 标签匹配角色池）并入，canonical
            # identity 去重——显式选中与 @ 命中不重复注入，同一角色只进一次。
            seg_cast_ids: list[str] = []
            _raw_cast_ids = seg_data.get("castIds", seg_data.get("cast_ids"))
            if isinstance(_raw_cast_ids, list):
                seg_cast_ids = [str(x).strip() for x in _raw_cast_ids if str(x).strip()]
            elif "castId" in seg_data or "cast_id" in seg_data:
                _cid = str(seg_data.get("castId") or seg_data.get("cast_id") or "").strip()
                if _cid:
                    seg_cast_ids = [_cid]
            # @ 命中 cast：<picture> 标签匹配角色池（canonical identity：名称判重）。
            _mention_cast_ids: list[str] = []
            for _tk, _name, _ts, _te in seg_media_tags:
                if _tk != "picture":
                    continue
                _mt = _match_asset_exact(cast_pool, _name)
                if _mt is not None:
                    _mention_cast_ids.append(_mt.id)
            # 显式 ∪ @命中 → 名称去重（保留池顺序，场景优先）。
            _cast_id_order = _dedup_cast_ids(cast_pool, seg_cast_ids + _mention_cast_ids)
            if not _cast_id_order:
                # 回退（保持旧行为）：提示词命名匹配 → 场景默认 → 全局默认。
                _m = _match_asset_by_name(cast_pool, seg_prompt)
                if _m is not None:
                    _cast_id_order = [_m.id]
                else:
                    _fb = scene_default_cast or default_cast_id
                    if _fb:
                        _cast_id_order = [_fb]
            # 逐个匹配 cast_pool → 段级 cast_assets（按数组顺序，同名兜底去重）。
            for _cid in _cast_id_order:
                _a = next((a for a in cast_pool if a.id == _cid), None)
                if _a is None:
                    continue
                if any((x.name or "").strip().lower() == (_a.name or "").strip().lower()
                       for x in seg_cast_assets):
                    continue
                seg_cast_assets.append(_a)
            if "locationId" in seg_data or "location_id" in seg_data:
                seg_location_id = str(
                    seg_data.get("locationId") or seg_data.get("location_id") or ""
                ).strip()
            else:
                seg_location_id = ""
                _m = _match_asset_by_name(location_pool, seg_prompt)
                if _m is not None:
                    seg_location_id = _m.id
                else:
                    seg_location_id = scene_default_location or default_location_id
            if seg_location_id:
                location_asset = next((a for a in location_pool if a.id == seg_location_id), None)
            # ★ P0-1 per-shot 资产边界（铁律①）：场景素材组是「候选池」，不是
            #   「默认注入集」。只把本段显式声明的 props/styles id（前端 shot.propIds /
            #   styleIds，旧数据兼容 prop_ids）从场景池内按 id 检出注入 executor 的
            #   extra_assets；没写 propIds 的段（旧项目/手写 timeline）不再全量扩散
            #   场景池 —— Shot 01（空镜）绝不拿到 Shot 03 才出现的旧剑匣。
            #   场景级素材仍供 Workbench 管理与 <picture> 标签匹配，但不作默认 ref。
            seg_prop_ids = _seg_id_list(seg_data, "propIds", "prop_ids")
            seg_style_ids = _seg_id_list(seg_data, "styleIds", "style_ids")
            if seg_scene:
                for _k, _ids in (("props", seg_prop_ids), ("styles", seg_style_ids)):
                    if not _ids:
                        continue
                    scene_pool = seg_scene.assets.get(_k) or []
                    by_id = {a.id: a for a in scene_pool}
                    for _pid in _ids:
                        _a = by_id.get(_pid)
                        if _a is None:
                            _a = _match_asset_by_name(scene_pool, _pid)
                        if _a is not None:
                            extra_assets.append(_a)
            # V1.2.7：<picture> 标签显式引用的资产图注入。
            # 在「场景素材组 + 全局资产」池（含场景 props/styles）里按名字匹配标签；
            # 匹配结果挂 SegmentPlan.tag_assets，executor 作为额外 ref_image 注入。
            # 与已自动注入的 cast/location/extra_assets 按 id 去重，避免重复槽位。
            tag_assets: list = []
            if seg_media_tags:
                tag_asset_pool = list(cast_pool or []) + list(location_pool or [])
                if seg_scene:
                    tag_asset_pool += (seg_scene.assets.get("props") or []) + (
                        seg_scene.assets.get("styles") or []
                    )
                _injected = {
                    a.id for a in (*seg_cast_assets, location_asset, *extra_assets) if a is not None and a.id
                }
                for _k, _name, _s, _e in seg_media_tags:
                    if _k != "picture":
                        continue  # video/audio 标签引用段 refs，走既有注入管线
                    _m = _match_asset_exact(tag_asset_pool, _name)
                    if _m is None:
                        log.warning(
                            "media tag <picture>%s</picture> on segment #%d didn't match "
                            "any asset (scene+global); ignored",
                            _name,
                            idx + 1,
                        )
                        continue
                    if _m.id in _injected:
                        continue  # 已通过自动注入（cast/location/extra_assets）
                    tag_assets.append(_m)
                    _injected.add(_m.id)
        else:
            tag_assets = []
        if seg_task_key == "i2v" and seg_refs:
            log.info(
                "i2v segment #%d: ignoring %d reference image(s); using source video context only",
                idx + 1,
                len(seg_refs),
            )
        # 阶段 D：r2v 批里手动标为 FL2VA 的段，其 refs 即首帧(index 0)/结束帧(index 1)，
        # 必须保留给 fl2v 硬锁路径使用；否则 segment_refs_for_context 会把 fl2v 的 refs 清空，
        # 导致用户上传的首尾帧丢失、退化回"仅首帧硬锁"。
        # 关键词自动路由（continuity_mode=="auto"）仍走清理：fl2v 段不带 refs 时，
        # executor 的 fl2v_handoff 分支会自动取上段末帧作首帧。
        if seg_task_key == "fl2v" and seg_continuity_mode == "fl2va":
            pass  # 保留 seg_refs（首帧 index 0 / 结束帧 index 1）
        else:
            seg_refs = segment_refs_for_context(seg_task_key, seg_refs)
        seg_ref_audios = []
        seg_ref_videos = []
        if edit_mode == "global":
            seg_ref_audios = segment_ref_audios_for_context(
                seg_task_key,
                _load_ref_audios(global_block.get("refAudios") or global_block.get("ref_audios") or []),
            )
        else:
            seg_ref_audios = segment_ref_audios_for_context(
                seg_task_key,
                _load_ref_audios(seg_data.get("refAudios") or seg_data.get("ref_audios") or []),
            )
            if seg_task_key == "r2v":
                seg_len = max(5, int(end) - int(start))
                raw_vids = seg_data.get("refVideos") or seg_data.get("ref_videos") or []
                # Backward compat: single referenceVideo → slot 0
                legacy = seg_data.get("referenceVideo") or seg_data.get("reference_video") or {}
                if isinstance(legacy, dict) and (legacy.get("videoFile") or legacy.get("fileName")):
                    if not any(int(v.get("index", v.get("slot", -1))) == 0 for v in raw_vids if isinstance(v, dict)):
                        raw_vids = [{"index": 0, **legacy}, *list(raw_vids or [])]
                seg_ref_videos = _load_ref_videos(raw_vids, timeline, seg_len)
        if seg_task_key in ("r2v", "r2i") and not seg_refs and not seg_ref_videos and not seg_ref_audios:
            log.warning(
                "gen segment #%d task=%s has no reference media — will behave like "
                "t2v/t2i. Upload 图片/音频/视频 on this material card.",
                idx + 1,
                seg_task_key,
            )
        seg_source = source_clips[idx].clone() if idx < len(source_clips) else None

        segments.append(
            SegmentPlan(
                id=seg_id,
                index=idx,
                start_frame=start,
                end_frame=end,
                prompt=seg_prompt,
                task_type=seg_task,
                task_key=seg_task_key,
                use_global=use_global,
                refs=seg_refs,
                ref_audios=seg_ref_audios,
                ref_videos=seg_ref_videos,
                negative_prompt=seg_negative,
                source_clip=seg_source,
                cast_assets=seg_cast_assets,
                location_asset=location_asset,
                scene_id=seg_scene_id,
                extra_assets=extra_assets,
                tag_assets=tag_assets,
                state_change=seg_state_change,
                smart_tail=seg_smart_tail,
                continuity_mode=seg_continuity_mode,
                consistency_check=seg_consistency_check,
            )
        )

    total = int(segment_ranges[-1][1]) if segment_ranges else int(source_video.shape[0])
    if is_prompt_batch_timeline(timeline, task_key):
        timeline_mode = "prompt_batch"
    else:
        timeline_mode = "gen_image" if submode == "gen_image" else "gen_blank"

    raw = dict(timeline)
    raw["timelineMode"] = timeline_mode
    src_w, src_h = _resolve_gen_image_source_dims(segment_ranges, global_block, output_block)

    # r2v 自动续接（ref2va 模型专用）：r2v 段无显式参考媒体时自动用上一段尾帧+继承
    # 角色/场景参考图续接（图片锚点方案）。**默认开启**——r2v 任务本质是"参考主体生视频"，
    # 第 N 段（N>1）无参考媒体时续接上一段是合理默认，只有用户显式关闭才禁用。
    # 注意独立于 continuityEnabled（前端对非 fl2v 模式会强制清零该字段）。
    # 持久化坑：r2vAutoContinuity 字段重启后可能不保留（旧工作流 JSON 无此字段），
    # 因此字段缺失一律视为 True；只有显式 false / "false" 才关闭。
    if task_key == "r2v":
        _raw = output_block.get("r2vAutoContinuity", output_block.get("r2v_auto_continuity", True))
        r2v_auto_handoff = not (
            _raw is False
            or (isinstance(_raw, str) and _raw.strip().lower() in ("false", "0", "off", "no"))
        )
    else:
        r2v_auto_handoff = False

    # 全局资产库总开关（Phase B，独立于「自动续接上段」）。持久化策略同
    # r2vAutoContinuity：字段缺失默认开启，只有显式 false / "false" 才关闭。
    _ga = output_block.get("globalAssetsEnabled", output_block.get("global_assets_enabled", True))
    global_assets_enabled = not (
        _ga is False
        or (isinstance(_ga, str) and _ga.strip().lower() in ("false", "0", "off", "no"))
    )

    # 状态跟踪总开关（阶段 C，独立开关）。持久化策略同上：字段缺失默认开启。
    _st = output_block.get("stateTrackingEnabled", output_block.get("state_tracking_enabled", True))
    state_tracking_enabled = not (
        _st is False
        or (isinstance(_st, str) and _st.strip().lower() in ("false", "0", "off", "no"))
    )

    # 智能尾帧选择总开关（待办④，独立开关）。持久化策略同上：字段缺失默认开启。
    _sa = output_block.get("smartTailEnabled", output_block.get("smart_tail_enabled", True))
    smart_tail_enabled = not (
        _sa is False
        or (isinstance(_sa, str) and _sa.strip().lower() in ("false", "0", "off", "no"))
    )

    # Qwen3-VL 视觉反馈（待办⑤）：默认关闭（有额外显存/模型成本，必须显式开启）。
    # 开启后段间跑状态提取；级别 1/2/3（默认 1）；关键镜头标记默认关。
    _qv = output_block.get("qwenVlEnabled", output_block.get("qwen_vl_enabled", False))
    qwen_vl_enabled = not (
        _qv is False
        or (isinstance(_qv, str) and _qv.strip().lower() in ("false", "0", "off", "no"))
    ) and bool(_qv)
    _qv_level = output_block.get("qwenVlLevel", output_block.get("qwen_vl_level", 1))
    try:
        qwen_vl_level = int(_qv_level)
    except (TypeError, ValueError):
        qwen_vl_level = 1
    if qwen_vl_level not in (1, 2, 3):
        qwen_vl_level = 1
    _qvk = output_block.get("qwenVlKeySegments", output_block.get("qwen_vl_key_segments", False))
    qwen_vl_key_segments = not (
        _qvk is False
        or (isinstance(_qvk, str) and _qvk.strip().lower() in ("false", "0", "off", "no"))
    ) and bool(_qvk)

    return DirectorPlan(
        frame_rate=float(timeline.get("frameRate") or frame_rate or 24),
        total_frames=total,
        width=out_w,
        height=out_h,
        ref_max_size=ref_max,
        output_mode=out_mode,
        source_width=int(src_w or out_w),
        source_height=int(src_h or out_h),
        global_task_type=task_type,
        global_task_key=task_key,
        global_prompt=prompt,
        global_refs=global_refs,
        source_video=source_video,
        segments=segments,
        edit_mode=edit_mode,
        raw=raw,
        scenes=scenes,
        export_mode=export_mode,
        run_indices=_parse_run_selection(timeline, len(segments)),
        r2v_auto_handoff=r2v_auto_handoff,
        global_assets_enabled=global_assets_enabled,
        state_tracking_enabled=state_tracking_enabled,
        smart_tail_enabled=smart_tail_enabled,
        qwen_vl_enabled=qwen_vl_enabled,
        qwen_vl_level=qwen_vl_level,
        qwen_vl_key_segments=qwen_vl_key_segments,
    )
