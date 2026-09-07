"""Parse MiniMax H3 Director timeline JSON and prepare per-segment edit plans."""

from __future__ import annotations

import base64
import copy
import io
import json
import logging
import os
from dataclasses import dataclass, field

import numpy as np
import torch
from PIL import Image

import folder_paths

from ..lib.audio_io import load_reference_audio
from ..lib.ref_audios import MAX_REFERENCE_AUDIOS, ref_audios_dict
from ..lib.ref_images import MAX_REFERENCE_IMAGES, REF_IMAGE_KEY_PREFIX
from ..lib.ref_videos import MAX_REFERENCE_VIDEOS, ref_videos_dict
from ..lib.image_prep import resolve_output_dimensions
from ..lib.task_prompts import get_task_prompt_spec, resolve_task_key
from ..lib.video_io import (
    load_reference_video_clip,
    logical_frame_count,
    logical_frame_map,
    load_timeline_segment,
    video_clips_from_timeline,
)
from .gen_timeline import (
    build_gen_director_plan,
    is_gen_timeline,
)

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director")

MIN_SEGMENT_FRAMES = 4
DEFAULT_CONTINUITY_OVERLAP = 9
MIN_CONTINUITY_OVERLAP = 1
MAX_CONTINUITY_OVERLAP = 81


@dataclass
class SegmentRef:
    index: int
    tensor: torch.Tensor


@dataclass
class GlobalAsset:
    """资产库条目（角色 cast / 场景 location / 道具 prop / 风格参考 style）。

    既用于全局资产库（timeline.assets，Phase B），也用于场景素材组
    （timeline.scenes[].assets，Scene Manager）。每个 r2v 段按 castId/locationId
    选择自动注入作为"世界锚点"，替代"依赖上一段传过参考图"的链式继承。
    """

    id: str
    name: str
    kind: str  # "cast" | "location" | "prop" | "style"
    tensor: torch.Tensor | None = None
    # 来源文件名（timeline 资产条目的 imageFile）。缓存指纹用：替换参考图后
    # 即使 id/name 不变，image_file 变化也会让该段缓存失效，避免「改图仍出旧画面」。
    image_file: str = ""


@dataclass
class SceneGroup:
    """场景组（Scene Manager 一级对象）。

    由前端 timeline.scenes 定义：手动创建、命名、排序。每个场景拥有独立素材组
    （角色/场景/道具/风格参考），是 Ref2VA 参考图、状态跟踪、Qwen 检测、镜头续接、
    导出的共同单位。段通过 SegmentPlan.scene_id 归属到场景。

    三级资产结构：Global Asset Library → Scene Asset Group → Shot Reference。
    本类即中间层「Scene Asset Group」：assets 为该场景的独立素材组，
    段（Shot）按 scene_id 归属后，优先使用本场景素材组（其次全局默认）。
    """

    id: str
    name: str = ""
    location: str = ""
    time: str = ""
    order: int = 0
    # 场景独立素材组：kind -> [GlobalAsset]。kind 复用 GlobalAsset 的
    # "cast"/"location"，并扩展 "prop"（道具）/ "style"（风格参考）。
    assets: dict[str, list[GlobalAsset]] = field(default_factory=dict)
    # 场景内默认资产引用（指向本场景 assets 里的资产 id），段未手动选择时兜底。
    default_cast_id: str = ""
    default_location_id: str = ""


@dataclass
class SceneGroupPlan:
    """一次场景分组的导出计划：段索引列表 + 显式 Scene 元数据（无则 None）。

    build_scene_groups 的返回单元。seg_indices 按时间顺序升序；
    scene 为 null 表示该组是 location 连续性兜底组（无显式 Scene）。
    """

    seg_indices: list[int]
    scene: SceneGroup | None = None


@dataclass
class SegmentRefAudio:
    """Standalone reference audio for MiniMax ``<Audio N>`` (index 0-based)."""

    index: int
    audio: dict  # ComfyUI AUDIO: {waveform, sample_rate}
    audio_file: str = ""


@dataclass
class SegmentRefVideo:
    """Standalone reference video for MiniMax ``<Video N>`` (index 0-based)."""

    index: int
    tensor: torch.Tensor
    video_file: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class SegmentPlan:
    index: int
    start_frame: int
    end_frame: int
    prompt: str
    task_type: str
    task_key: str
    use_global: bool
    refs: list[SegmentRef] = field(default_factory=list)
    ref_audios: list[SegmentRefAudio] = field(default_factory=list)
    ref_videos: list[SegmentRefVideo] = field(default_factory=list)
    reference_video_meta: dict = field(default_factory=dict)
    reference_video_start_frame: int = 0
    negative_prompt: str = ""
    # 段唯一标识（timeline.segments[].id，SPA 里即镜头 uuid）。缓存指纹用它做
    # 「镜头身份」隔离：不同项目/镜头即使 index+参数相同也不会互相命中旧缓存
    # （修复「新项目空镜头生成出旧项目人物」）。老 timeline 无 id 时为空串，
    # 指纹退化为按 index+参数匹配（向后兼容）。
    id: str = ""
    source_clip: torch.Tensor | None = None
    # 全局资产库（Phase B）：本段注入的角色/场景资产图（世界锚点）。
    # 由 gen_timeline 按 seg.castIds/castId（或全局默认）解析后挂载。
    # V1.6-B：多角色 cast 集合 → 列表（@命中 cast 并入去重）；旧单值 castId
    # 数据迁移为单元素列表。执行器逐一注入角色资产图（天然支持多角色同框）。
    cast_assets: list[GlobalAsset] = field(default_factory=list)
    location_asset: GlobalAsset | None = None
    # 场景归属（Scene Manager）：本段所属 SceneGroup.id（timeline.scenes 里）。
    # 空字符串 = 未显式归属，导出时按 location 连续性兜底分组。
    # 非空时：导出按 scene 分组；资产解析优先用该场景素材组（Scene Asset Group）。
    scene_id: str = ""
    # 场景素材组附加资产（Scene Asset Group 扩展）：本段所属场景素材组里的
    # 道具 prop / 风格参考 style（不占用 cast/location 槽位），executor 注入时
    # 作为额外 ref_image 追加（参考图槽位未满时）。优先级低于段手动 refs。
    extra_assets: list[GlobalAsset] = field(default_factory=list)
    # 状态跟踪（阶段 C）：本镜结束后应落地的「状态变更」描述（动作/地点/时间/情绪）。
    # 由用户在前端卡片填写（可选）；executor 用它更新 current_state，供下一镜拼接前缀。
    state_change: str = ""
    # 智能尾帧选择（待办④，段级覆盖）：三态——None=跟随全局开关、True=强制开启、
    # False=强制关闭。开启时参考图路径从上一段末尾几帧挑锐度最佳帧作续接锚点。
    smart_tail: bool | None = None
    # 阶段 D 镜头路由（智能导演）："auto"=按提示词关键词自动判定（Ref2VA/FL2VA）、
    # "ref2va"=强制走参考图状态驱动、"fl2va"=强制走首尾帧硬锁。auto 时由 gen_timeline
    # 用关键词路由函数解析成最终 task_key；手动覆盖优先。
    continuity_mode: str = "auto"
    # 里程碑 B：关键镜头标记（二级一致性检测）——三态覆盖（前端下拉）：
    # None/"auto"=有角色/场景资产注入即自动检测、True=强制检测、False=跳过。
    # 由 gen_timeline 解析 seg.consistencyCheck（undefined/"auto"/true/false）后挂载。
    consistency_check: bool | None = None
    # V1.2.7：Prompt 媒体引用标签（<picture>名</picture>）匹配到的资产图。
    # 用户在提示词里显式引用资产（角色/地点/道具/风格），gen_timeline 解析标签、
    # 在「场景素材组 + 全局资产」池里按名字匹配后挂载；executor 注入时作为
    # 额外 ref_image 追加（复用资产注入管线），并生成 <Picture N> 官方说明。
    # 已自动注入的 cast/location（cast_asset/location_asset）与场景素材组
    # extra_assets 会在 gen_timeline 去重，避免重复槽位。
    tag_assets: list[GlobalAsset] = field(default_factory=list)

    @property
    def cast_asset(self) -> GlobalAsset | None:
        """V1.6-B 兼容 getter：旧代码（缓存指纹/一致性检测）读单值时返回首元素。

        真相源是 ``cast_assets`` 列表；此属性只为向后兼容（旧读单值路径）
        与旧 SegmentPlan 对象（无 cast_assets 字段）提供统一的单值视图。
        """
        return self.cast_assets[0] if self.cast_assets else None

    @property
    def frame_count(self) -> int:
        return max(0, self.end_frame - self.start_frame)


@dataclass
class DirectorPlan:
    frame_rate: float
    total_frames: int
    width: int
    height: int
    ref_max_size: int
    output_mode: str
    source_width: int
    source_height: int
    global_task_type: str
    global_task_key: str
    global_prompt: str
    global_refs: list[SegmentRef]
    segments: list[SegmentPlan]
    source_video: torch.Tensor
    edit_mode: str
    raw: dict
    source_total_frames: int = 0
    export_max_frames: int = 0
    # Scene Manager（一级对象）：timeline.scenes 解析结果。
    # 每个场景拥有独立素材组（角色/场景/道具/风格参考），段按 scene_id 归属；
    # 导出/Ref2VA 参考图/状态跟踪/Qwen 检测/镜头续接都以场景为共同单位。
    scenes: list[SceneGroup] = field(default_factory=list)
    # 导出模式（Movie → Scene → Shot 三级）：
    #   "all"      = 全片导出（内存合并，旧行为，适合 ≤1~2 分钟短片）
    #   "segments" = 分镜导出（Shot）：每镜独立 clip，可单独重跑/调试
    #   "scene"    = 场景导出（Scene）★推荐：按场景流式合并（场景内接缝优化），
    #                输出 SceneNN.mp4；内存峰值 ≈ 单场景大小，与段数解耦
    #   "movie"    = 全片导出（Movie）：场景合并后 ffmpeg 直拼场景 → Movie.mp4，
    #                几乎不占合并内存
    # "stream"/"streaming" 是 "movie" 的旧别名，保持兼容。
    export_mode: str = "scene"
    run_indices: frozenset[int] | None = None  # None = run all segments
    continuity_enabled: bool = False
    continuity_overlap_frames: int = 0
    global_ref_audios: list[SegmentRefAudio] = field(default_factory=list)
    # fl2v 自动交接镜的续接方式：参考图续接 ("image") | 从视频续接 ("video")。
    # "image" → ref_image_0 = 上段尾帧；"video" → ref_video_0 = 上段整段视频。
    handoff_mode: str = "image"
    # r2v 自动续接（ref2va 模型专用）：开启后，r2v 段无显式参考媒体时，
    # 自动用上一段整段视频 (ref_video_0 = prev_tail) 续接，完整继承运动轨迹。
    r2v_auto_handoff: bool = False
    # 全局资产库总开关（Phase B，与「自动续接上段」独立）：字段缺失默认开启。
    # 开启后，r2v 段按其 cast_asset/location_asset 自动注入角色/场景资产图。
    global_assets_enabled: bool = True
    # 状态跟踪总开关（阶段 C）：字段缺失默认开启，与「自动续接上段」「全局资产库」独立。
    # 开启后，每镜 prompt 前自动拼接上一镜累计的 current_state 前缀。
    state_tracking_enabled: bool = True
    # 智能尾帧选择总开关（待办④）：字段缺失默认开启，与其它开关独立。
    # 开启后，参考图路径（r2v 图片锚点 / fl2v 参考图续接）自动挑末尾最佳帧；
    # 段级 seg.smart_tail 可单独覆盖本开关。
    smart_tail_enabled: bool = True
    # Qwen3-VL 视觉反馈总开关（待办⑤，AI 导演判断）：默认关闭。
    # 开启后，段生成完在「段间清理显存」窗口内跑 Qwen3-VL 反馈（状态提取等）。
    # 字段缺失默认关（与 r2v/资产等"缺失默认开"不同——Qwen3-VL 有额外显存/模型成本，
    # 必须用户显式开启）。
    qwen_vl_enabled: bool = False
    # Qwen3-VL 反馈级别：1=状态提取（含 character_state/camera_state）、
    # 2=+一致性检测（关键镜头）、3=+失败重跑（Qwen+规则双确认）。默认 1。
    qwen_vl_level: int = 1
    # 关键镜头标记总开关（二级一致性检测用）：开启后，主角首次出现/场景切换/
    # 重要剧情节点镜头才跑一致性检测（勿全量检查，成本高）。默认关闭。
    qwen_vl_key_segments: bool = False

    @property
    def segment_count(self) -> int:
        return len(self.segments)


def _ref_video_has_file(ref_block: dict | None) -> bool:
    if not ref_block:
        return False
    return bool((ref_block.get("videoFile") or ref_block.get("fileName") or "").strip())


def _continuous_reference_enabled(timeline: dict, edit_mode: str, task_key: str) -> bool:
    """Global ads2v only: align reference video timeline offset with each segment start."""
    if edit_mode != "global" or task_key != "ads2v":
        return False
    global_block = timeline.get("global") or {}
    return bool(
        global_block.get("continuousReference")
        or global_block.get("continuous_reference")
        or timeline.get("continuousReference")
        or timeline.get("continuous_reference")
    )


def _resolve_global_reference_video(timeline: dict) -> dict:
    global_block = timeline.get("global") or {}
    ref = global_block.get("referenceVideo") or global_block.get("reference_video") or {}
    if _ref_video_has_file(ref):
        return dict(ref)
    legacy = timeline.get("referenceVideo") or timeline.get("reference_video") or {}
    return dict(legacy) if isinstance(legacy, dict) else {}


from .frame_align import minimax_align_frame_count as wan_align_frame_count


def _decode_image_b64(b64_str: str) -> torch.Tensor:
    if not b64_str:
        raise ValueError("Empty image data.")
    if b64_str.startswith("/view?"):
        raise ValueError("Remote view URLs are not supported; upload images in the Director node.")
    payload = b64_str.split(",", 1)[1] if "," in b64_str else b64_str
    img_bytes = base64.b64decode(payload)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    arr = np.array(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


def load_reference_tensor(ref: dict) -> torch.Tensor | None:
    if ref.get("imageFile"):
        rel = str(ref["imageFile"]).replace("\\", "/")
        file_path = os.path.join(folder_paths.get_input_directory(), rel.replace("/", os.sep))
        if os.path.exists(file_path):
            img = Image.open(file_path).convert("RGB")
            arr = np.array(img, dtype=np.float32) / 255.0
            return torch.from_numpy(arr).unsqueeze(0)

    b64_str = ref.get("imageB64", "")
    if not b64_str:
        return None
    try:
        return _decode_image_b64(b64_str)
    except Exception as exc:
        log.warning("Failed to decode reference image: %s", exc)
        return None


def load_source_video_from_timeline(timeline: dict) -> torch.Tensor:
    """Load all logical frames (legacy). Prefer load_timeline_segment for long videos."""
    total = logical_frame_count(timeline)
    if total <= 0:
        video = timeline.get("video") or {}
        if not (video.get("frames") or []):
            raise ValueError("No frames in MiniMax H3 Director timeline.")
    return load_timeline_segment(timeline, 0, max(1, total))


def _load_refs(ref_list: list[dict]) -> list[SegmentRef]:
    refs: list[SegmentRef] = []
    for item in ref_list or []:
        index = int(item.get("index", item.get("slot", len(refs))))
        if index < 0 or index >= MAX_REFERENCE_IMAGES:
            continue
        tensor = load_reference_tensor(item)
        if tensor is not None:
            refs.append(SegmentRef(index=index, tensor=tensor))
    return sorted(refs, key=lambda r: r.index)


_BAD_ASSET_NAME = "[object File]"


def _clean_asset_name(item: dict) -> str:
    """清洗资产名称：拒绝前端误存的 "[object File]" 占位，空名回退文件名。"""
    name = str(item.get("name") or "").strip()
    if name and name != _BAD_ASSET_NAME:
        return name
    img = str(item.get("imageFile") or item.get("image") or "").replace("\\", "/").split("/")[-1]
    return img or ""


def _load_global_assets(asset_list: list[dict], kind: str) -> list[GlobalAsset]:
    """Load global asset library entries (cast / location) from timeline.assets."""
    assets: list[GlobalAsset] = []
    for item in asset_list or []:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id") or "").strip()
        if not aid:
            continue
        assets.append(
            GlobalAsset(
                id=aid,
                name=_clean_asset_name(item),
                kind=kind,
                tensor=load_reference_tensor(item),
                image_file=str(item.get("imageFile") or item.get("image") or "").replace("\\", "/"),
            )
        )
    return assets


def load_reference_audio_item(item: dict) -> dict | None:
    """Load one timeline refAudios entry into a ComfyUI AUDIO dict."""
    rel = str(
        item.get("audioFile")
        or item.get("audio_file")
        or item.get("fileName")
        or item.get("file_name")
        or ""
    ).replace("\\", "/").strip()
    if not rel:
        return None
    sub = str(item.get("subfolder") or "").replace("\\", "/").strip().strip("/")
    if sub and not rel.startswith(sub + "/"):
        rel = f"{sub}/{rel}"
    file_path = os.path.join(folder_paths.get_input_directory(), rel.replace("/", os.sep))
    if not os.path.isfile(file_path):
        log.warning("Reference audio missing: %s", file_path)
        return None
    audio = load_reference_audio(file_path)
    if audio is None:
        log.warning("Failed to decode reference audio: %s", file_path)
    return audio


def _load_ref_audios(audio_list: list[dict]) -> list[SegmentRefAudio]:
    out: list[SegmentRefAudio] = []
    for item in audio_list or []:
        if not isinstance(item, dict):
            continue
        index = int(item.get("index", item.get("slot", len(out))))
        if index < 0 or index >= MAX_REFERENCE_AUDIOS:
            continue
        audio = load_reference_audio_item(item)
        if audio is None:
            continue
        rel = str(item.get("audioFile") or item.get("audio_file") or item.get("fileName") or "").strip()
        out.append(SegmentRefAudio(index=index, audio=audio, audio_file=rel))
    return sorted(out, key=lambda a: a.index)


def segment_ref_audios_for_context(task_key: str, audios: list[SegmentRefAudio]) -> list[SegmentRefAudio]:
    """Standalone ref audios apply to r2v / rv2v (official ReferenceToVideo)."""
    if task_key not in {"r2v", "rv2v"}:
        return []
    return audios


def ref_audios_to_dict(audios: list[SegmentRefAudio]) -> dict | None:
    return ref_audios_dict([(a.index, a.audio) for a in audios])


def _ref_video_entry_has_file(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    return bool((item.get("videoFile") or item.get("fileName") or "").strip())


def _load_ref_videos(
    video_list: list[dict],
    timeline: dict,
    num_frames: int,
) -> list[SegmentRefVideo]:
    """Load up to 3 standalone reference videos for r2v / ReferenceToVideo."""
    out: list[SegmentRefVideo] = []
    for item in video_list or []:
        if not isinstance(item, dict) or not _ref_video_entry_has_file(item):
            continue
        index = int(item.get("index", item.get("slot", len(out))))
        if index < 0 or index >= MAX_REFERENCE_VIDEOS:
            continue
        try:
            tensor = load_reference_video_clip(item, timeline, num_frames, start_frame=0)
        except Exception as exc:
            log.warning("Failed to load reference video slot %s: %s", index, exc)
            continue
        if tensor is None or tensor.numel() <= 0:
            continue
        rel = str(item.get("videoFile") or item.get("fileName") or "").strip()
        out.append(SegmentRefVideo(index=index, tensor=tensor, video_file=rel, meta=dict(item)))
    return sorted(out, key=lambda v: v.index)


def ref_videos_to_dict(videos: list[SegmentRefVideo]) -> dict | None:
    return ref_videos_dict([(v.index, v.tensor) for v in videos])


def reinforce_r2v_prompt(
    prompt: str,
    *,
    ref_indices: list[int] | None = None,
    video_indices: list[int] | None = None,
    audio_indices: list[int] | None = None,
) -> str:
    """Remind <Picture N> / <Video K> / <Audio J> when tags are missing (r2v batch)."""
    text = (prompt or "").strip() or "Generate a cinematic scene."
    pic_indices = sorted({int(i) for i in (ref_indices or []) if int(i) >= 0})
    vid_indices = sorted({int(i) for i in (video_indices or []) if int(i) >= 0})
    aud_indices = sorted({int(i) for i in (audio_indices or []) if int(i) >= 0})
    prefix_parts: list[str] = []
    if pic_indices and "<Picture" not in text and "<picture" not in text:
        prefix_parts.append(" ".join(f"<Picture {i + 1}>" for i in pic_indices))
    if vid_indices and "<Video" not in text and "<video" not in text:
        prefix_parts.append(" ".join(f"<Video {i + 1}>" for i in vid_indices))
    if aud_indices and "<Audio" not in text and "<audio" not in text:
        prefix_parts.append(" ".join(f"<Audio {i + 1}>" for i in aud_indices))
    if not prefix_parts:
        return text
    return f"{' '.join(prefix_parts)} {text}"


def _segment_ranges_from_timeline(timeline: dict, total: int) -> list[tuple[int, int, dict]]:
    segments = timeline.get("segments") or []
    if segments and ("length" in segments[0] or "end" in segments[0]):
        ranges: list[tuple[int, int, dict]] = []
        for raw in sorted(segments, key=lambda s: int(s.get("start", 0))):
            start = int(raw.get("start", 0))
            if "end" in raw:
                end = int(raw["end"])
            else:
                end = start + int(raw.get("length", 0))
            start = max(0, min(start, total))
            end = max(start, min(end, total))
            if end - start >= MIN_SEGMENT_FRAMES or not ranges:
                ranges.append((start, end, raw))
        if ranges:
            return ranges

    split_points = timeline.get("splitPoints") or timeline.get("split_points") or []
    auto_count = int(timeline.get("autoSegmentCount") or timeline.get("auto_segment_count") or 0)
    if auto_count > 1:
        points = [int(round(total * i / auto_count)) for i in range(1, auto_count)]
    else:
        points = sorted({int(p) for p in split_points if 0 < int(p) < total})

    edges = [0] + points + [total]
    ranges = []
    for i in range(len(edges) - 1):
        start, end = edges[i], edges[i + 1]
        if end <= start:
            continue
        raw = segments[i] if i < len(segments) else {}
        ranges.append((start, end, raw))
    return ranges or [(0, total, {})]


def _resolve_export_total(timeline: dict, source_total: int) -> int:
    output_block = timeline.get("output") or {}
    max_export = int(output_block.get("maxExportFrames") or output_block.get("max_export_frames") or 0)
    if max_export <= 0 or source_total <= 0:
        return source_total
    return min(source_total, max_export)


def _resolve_export_mode(output_block: dict) -> str:
    mode = str(output_block.get("exportMode") or output_block.get("export_mode") or "scene").lower()
    if mode in ("segments", "segment", "per_segment", "by_segment", "shot", "shots"):
        return "segments"
    if mode in ("scene", "scenes", "by_scene", "per_scene"):
        return "scene"
    if mode in ("stream", "streaming", "mp4", "files", "per_file", "video_files",
                "movie", "full", "full_movie"):
        return "movie"
    return "all"


def build_scene_groups(
    segments: list["SegmentPlan"],
    scenes: list["SceneGroup"] | None = None,
) -> list[SceneGroupPlan]:
    """把连续段按「场景」分组（Scene Manager 一级对象）。

    分组规则（sceneId 显式优先 + location 连续性兜底）：
      1. 段带 scene_id 且能在 scenes 里找到对应 SceneGroup → 按显式场景分组。
         同一显式场景的段即使跨地点/跨时间也归同组，组序按时间线出现顺序。
      2. 未显式归属场景的段 → 相邻段 location_asset 相同归为同一场景组。
      3. 完全无 location 信息的段并为一组。
    保持时间顺序；返回 SceneGroupPlan 列表（seg_indices + 对应 scene 元数据，
    location 兜底组的 scene 为 None）。

    场景是导演系统的一等公民（Movie → Scene → Shot）。显式场景由 Scene Manager
    手动创建（timeline.scenes）；location 兜底用于旧时间线 / 未归属段，等价旧的
    「同地点连续拍摄归一场戏」行为。
    """
    scenes_by_id = {s.id: s for s in (scenes or [])}

    def _key(seg: "SegmentPlan") -> tuple[str, str]:
        sid = (seg.scene_id or "").strip()
        if sid and sid in scenes_by_id:
            return ("scene", sid)
        loc = getattr(seg, "location_asset", None)
        loc_name = (getattr(loc, "name", "") or "").strip()
        return ("loc", loc_name)

    def _close_group(indices: list[int], key: tuple[str, str] | None) -> SceneGroupPlan:
        scene = scenes_by_id.get(key[1]) if key and key[0] == "scene" else None
        return SceneGroupPlan(seg_indices=list(indices), scene=scene)

    groups: list[SceneGroupPlan] = []
    cur_indices: list[int] = []
    cur_key: tuple[str, str] | None = None
    for seg in segments:
        key = _key(seg)
        if cur_key is not None and key != cur_key:
            groups.append(_close_group(cur_indices, cur_key))
            cur_indices = []
        cur_indices.append(seg.index)
        cur_key = key
    if cur_indices:
        groups.append(_close_group(cur_indices, cur_key))
    if not groups:
        groups.append(SceneGroupPlan(seg_indices=[s.index for s in segments]))
    return groups


def _clip_segment_ranges(
    ranges: list[tuple[int, int, dict]], export_total: int
) -> list[tuple[int, int, dict]]:
    if export_total <= 0:
        return ranges
    clipped: list[tuple[int, int, dict]] = []
    for start, end, data in ranges:
        if start >= export_total:
            break
        end = min(end, export_total)
        if end <= start:
            continue
        if end - start < MIN_SEGMENT_FRAMES and clipped:
            ps, _, pd = clipped[-1]
            clipped[-1] = (ps, end, pd)
        else:
            clipped.append((start, end, data))
    if not clipped and export_total > 0:
        data = ranges[0][2] if ranges else {}
        clipped.append((0, export_total, data))
    return clipped


def _trim_timeline_for_export(timeline: dict, export_total: int) -> dict:
    t = copy.deepcopy(timeline)
    video = dict(t.get("video") or {})
    frames_b64 = video.get("frames") or []
    if frames_b64 and export_total < len(frames_b64):
        video["frames"] = frames_b64[:export_total]
    frame_map = video.get("frameMap") or []
    if frame_map and export_total < len(frame_map):
        video["frameMap"] = frame_map[:export_total]
    t["video"] = video
    t["totalFrames"] = export_total
    return t


def _parse_run_selection(timeline: dict, segment_count: int) -> frozenset[int] | None:
    """Return selected segment indices, or None when all segments should run."""
    enabled = bool(timeline.get("runSelectEnabled") or timeline.get("run_select_enabled"))
    if not enabled:
        return None
    raw = timeline.get("runSelection")
    if raw is None:
        raw = timeline.get("run_selection")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    indices = {int(i) for i in raw if 0 <= int(i) < segment_count}
    if not indices:
        raise ValueError(
            "MiniMax H3 Director: 「选择运行」已开启但未勾选任何片段/提示词组。请至少勾选一组再执行。"
        )
    if len(indices) >= segment_count:
        return None
    return frozenset(indices)


def count_all_timeline_segments(timeline_data: str) -> int:
    """Total segment count on the timeline (ignores run selection)."""
    if not timeline_data or not str(timeline_data).strip():
        return 1
    try:
        timeline = json.loads(timeline_data)
    except json.JSONDecodeError:
        return 1

    segments = timeline.get("segments") or []
    global_task = (timeline.get("global") or {}).get("taskType") or ""
    task_key = resolve_task_key(global_task) if global_task else ""
    if task_key == "fl2v" or str(timeline.get("timelineMode") or "").lower() == "fl2v":
        from .fl2v_timeline import count_fl2v_runnable_shots

        return count_fl2v_runnable_shots(timeline)
    if is_gen_timeline(timeline, task_key):
        return max(1, len(segments) or 1)

    source_total = logical_frame_count(timeline) or int(timeline.get("totalFrames") or 0)
    export_total = _resolve_export_total(timeline, source_total)
    plan_total = export_total or source_total or 1
    ranges = _segment_ranges_from_timeline(timeline, source_total or plan_total)
    return max(1, len(_clip_segment_ranges(ranges, plan_total)))


def count_timeline_segments(timeline_data: str) -> int:
    """Segments that will run (respects run selection when enabled)."""
    if not timeline_data or not str(timeline_data).strip():
        return 1
    try:
        timeline = json.loads(timeline_data)
    except json.JSONDecodeError:
        return 1

    seg_count = count_all_timeline_segments(timeline_data)
    run_sel = _parse_run_selection(timeline, seg_count)
    return len(run_sel) if run_sel is not None else seg_count


def build_director_plan(
    timeline_data: str,
    *,
    global_task_type: str,
    global_prompt: str,
    total_frames: int,
    frame_rate: float,
    width: int,
    height: int,
    ref_max_size: int,
) -> DirectorPlan:
    timeline: dict = {}
    if timeline_data and timeline_data.strip():
        try:
            timeline = json.loads(timeline_data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid timeline_data JSON: {exc}") from exc

    global_block = timeline.get("global") or {}
    edit_mode = timeline.get("editMode") or timeline.get("edit_mode") or "global"
    if edit_mode not in ("global", "segment"):
        edit_mode = "global"

    task_type = global_block.get("taskType") or global_task_type or "v2v — 视频转视频(Video to Video)"
    prompt = global_block.get("prompt") or global_prompt or ""
    global_refs = _load_refs(global_block.get("refs") or [])
    global_ref_audios = _load_ref_audios(
        global_block.get("refAudios") or global_block.get("ref_audios") or []
    )
    global_ref_video = _resolve_global_reference_video(timeline)

    task_key_early = resolve_task_key(task_type)
    if task_key_early == "fl2v" or str(timeline.get("timelineMode") or "").lower() == "fl2v":
        from .fl2v_timeline import build_fl2v_director_plan

        return build_fl2v_director_plan(
            timeline,
            global_task_type=task_type,
            global_prompt=prompt,
            total_frames=total_frames,
            frame_rate=frame_rate,
            width=width,
            height=height,
            ref_max_size=ref_max_size,
        )
    if is_gen_timeline(timeline, task_key_early):
        return build_gen_director_plan(
            timeline,
            global_task_type=task_type,
            global_prompt=prompt,
            total_frames=total_frames,
            frame_rate=frame_rate,
            width=width,
            height=height,
            ref_max_size=ref_max_size,
        )

    frame_map = logical_frame_map(timeline)
    source_total = logical_frame_count(timeline) or int(timeline.get("totalFrames") or total_frames or 0)
    export_max = int(
        (timeline.get("output") or {}).get("maxExportFrames")
        or (timeline.get("output") or {}).get("max_export_frames")
        or 0
    )
    export_total = _resolve_export_total(timeline, source_total)

    load_timeline = _trim_timeline_for_export(timeline, export_total) if export_total < source_total else timeline

    clips = video_clips_from_timeline(load_timeline)
    if not clips and not (load_timeline.get("video") or {}).get("frames"):
        raise ValueError(
            "No source video in MiniMax H3 Director. Upload a video inside the node timeline UI before running."
        )

    try:
        probe = load_timeline_segment(load_timeline, 0, 1)
        loaded_h = int(probe.shape[1])
        loaded_w = int(probe.shape[2])
    except Exception as exc:
        log.warning("Could not probe source video frame: %s", exc)
        video_meta = load_timeline.get("video") or {}
        loaded_w = int(video_meta.get("width") or width)
        loaded_h = int(video_meta.get("height") or height)

    source_video = torch.zeros(0, max(1, loaded_h), max(1, loaded_w), 3)
    video_meta = timeline.get("video") or {}
    meta_w = int(video_meta.get("width") or 0)
    meta_h = int(video_meta.get("height") or 0)

    output_block = timeline.get("output") or {}
    export_mode = _resolve_export_mode(output_block)
    out_w, out_h, ref_max, output_mode = resolve_output_dimensions(
        loaded_w or meta_w or int(width),
        loaded_h or meta_h or int(height),
        mode=str(output_block.get("mode") or "long_edge"),
        long_edge=int(output_block.get("longEdge") or output_block.get("long_edge") or ref_max_size or 848),
        fixed_width=int(output_block.get("width") or timeline.get("width") or width),
        fixed_height=int(output_block.get("height") or timeline.get("height") or height),
    )

    total = int(load_timeline.get("totalFrames") or export_total or total_frames or 0)
    if total <= 0:
        total = source_total

    segment_ranges = _segment_ranges_from_timeline(timeline, source_total or total)
    segment_ranges = _clip_segment_ranges(segment_ranges, total)
    segments: list[SegmentPlan] = []
    continuous_ref = _continuous_reference_enabled(timeline, edit_mode, resolve_task_key(task_type))

    for idx, (start, end, seg_data) in enumerate(segment_ranges):
        if edit_mode == "global":
            seg_prompt = prompt
            seg_task = task_type
            seg_refs = list(global_refs)
            seg_ref_audios = list(global_ref_audios)
            seg_ref_video = dict(global_ref_video)
            use_global = True
        else:
            use_global = False
            seg_prompt = (seg_data.get("prompt") or "").strip() or prompt
            seg_task = seg_data.get("taskType") or seg_data.get("task_type") or task_type
            # Segment mode: only this segment's refs — never inherit global.refs / refAudios.
            try:
                seg_refs = _load_refs(seg_data.get("refs") or [])
            except Exception as _ref_exc:
                # #93：plan 阶段参考图加载失败（坏图/缺文件）带段号抛错。
                # 前端 failedShotIds 正则 (Segment N) 据此定位真实失败镜头，
                # 而不是落到上一次运行残留的 failed 状态上（s2 坏图错标 s1）。
                raise ValueError(
                    f"Segment {idx + 1} reference image could not be loaded: {_ref_exc}"
                ) from _ref_exc
            seg_ref_audios = _load_ref_audios(
                seg_data.get("refAudios") or seg_data.get("ref_audios") or []
            )
            seg_ref_video = dict(seg_data.get("referenceVideo") or seg_data.get("reference_video") or {})

        seg_task_key = resolve_task_key(seg_task)
        seg_refs = segment_refs_for_context(seg_task_key, seg_refs)
        seg_ref_audios = segment_ref_audios_for_context(seg_task_key, seg_ref_audios)
        ref_start = start if continuous_ref and seg_task_key == "ads2v" else 0

        segments.append(
            SegmentPlan(
                index=idx,
                start_frame=start,
                end_frame=end,
                prompt=seg_prompt,
                task_type=seg_task,
                task_key=seg_task_key,
                use_global=use_global,
                refs=seg_refs,
                ref_audios=seg_ref_audios,
                reference_video_meta=seg_ref_video,
                reference_video_start_frame=ref_start,
            )
        )

    for seg in segments:
        if seg.task_key != "ads2v":
            continue
        if _ref_video_has_file(seg.reference_video_meta):
            continue
        raise ValueError(
            f"ads2v (广告植入) segment #{seg.index + 1} requires a reference video. "
            "Upload the content-to-insert clip for this segment in the Director node UI."
        )

    from .segment_continuity import resolve_continuity_settings

    continuity_enabled, continuity_overlap = resolve_continuity_settings(
        timeline, segment_count=len(segments)
    )

    return DirectorPlan(
        frame_rate=float(timeline.get("frameRate") or frame_rate or 24),
        total_frames=total,
        width=out_w,
        height=out_h,
        ref_max_size=ref_max,
        output_mode=output_mode,
        source_width=int(meta_w or loaded_w),
        source_height=int(meta_h or loaded_h),
        global_task_type=task_type,
        global_task_key=resolve_task_key(task_type),
        global_prompt=prompt,
        global_refs=global_refs,
        segments=segments,
        source_video=source_video,
        edit_mode=edit_mode,
        raw=load_timeline,
        source_total_frames=source_total or total,
        export_max_frames=export_max,
        export_mode=export_mode,
        run_indices=_parse_run_selection(timeline, len(segments)),
        continuity_enabled=continuity_enabled,
        continuity_overlap_frames=continuity_overlap,
        global_ref_audios=global_ref_audios,
    )


def slice_video_frames(source: torch.Tensor, start: int, end: int) -> torch.Tensor:
    end = min(end, source.shape[0])
    start = max(0, min(start, end))
    return source[start:end].clone()


def prepare_segment_clip(clip: torch.Tensor, target_frames: int) -> tuple[torch.Tensor, int]:
    """Trim source toward MiniMax 17k+5 length. Do **not** pad with last-frame copies.

    Fabricating freeze frames in the source makes Bernini/Wan reproduce visible
    stutter / duplicate frames. Official BerniniConditioning simply encodes
    ``source[:length]`` even when the clip is shorter than ``length``.
    """
    actual = clip.shape[0]
    if actual <= 0:
        raise ValueError("Segment has no frames.")
    num_frames = wan_align_frame_count(max(actual, target_frames))
    if actual > num_frames:
        clip = clip[:num_frames]
    return clip, num_frames


# i2v/fl2v use keyframes; v2v uses source clip as <Video 1>; r2v uses ref_images.
CONTEXT_REFERENCE_EXCLUDED_KEYS = frozenset({"i2v", "fl2v", "t2v", "v2v"})


def segment_refs_for_context(task_key: str, refs: list[SegmentRef]) -> list[SegmentRef]:
    if task_key in CONTEXT_REFERENCE_EXCLUDED_KEYS:
        return []
    return refs


def refs_to_kwargs(refs: list[SegmentRef]) -> dict[str, torch.Tensor]:
    return {f"{REF_IMAGE_KEY_PREFIX}{ref.index}": ref.tensor for ref in refs}


def reinforce_v2v_prompt(prompt: str) -> str:
    """Ensure MiniMax ReferenceToVideo sees an explicit <Video 1> tag for source edit."""
    text = (prompt or "").strip()
    if not text:
        return "Edit <Video 1>."
    if "<Video" in text or "<video" in text:
        return text
    return f"<Video 1> {text}"


def reinforce_rv2v_prompt(
    prompt: str,
    *,
    ref_indices: list[int] | None = None,
    audio_indices: list[int] | None = None,
) -> str:
    """Source <Video 1> + remind <Picture N> / <Audio J> when tags are missing."""
    text = reinforce_v2v_prompt(prompt)
    pic_indices = sorted({int(i) for i in (ref_indices or []) if int(i) >= 0})
    aud_indices = sorted({int(i) for i in (audio_indices or []) if int(i) >= 0})
    prefix_parts: list[str] = []
    if pic_indices and "<Picture" not in text and "<picture" not in text:
        prefix_parts.append(" ".join(f"<Picture {i + 1}>" for i in pic_indices))
    if aud_indices and "<Audio" not in text and "<audio" not in text:
        prefix_parts.append(" ".join(f"<Audio {i + 1}>" for i in aud_indices))
    if not prefix_parts:
        return text
    return f"{' '.join(prefix_parts)} {text}"


def reference_video_for_segment(plan: DirectorPlan, seg: SegmentPlan, num_frames: int) -> torch.Tensor | None:
    """Optional separate reference video for r2v (not used by v2v — source clip is the ref)."""
    if seg.task_key != "r2v":
        return None
    if not _ref_video_has_file(seg.reference_video_meta):
        return None
    return load_reference_video_clip(
        seg.reference_video_meta,
        plan.raw,
        num_frames,
        start_frame=seg.reference_video_start_frame,
    )


def refs_to_kwargs_for_context(task_key: str, refs: list[SegmentRef]) -> dict[str, torch.Tensor]:
    return refs_to_kwargs(segment_refs_for_context(task_key, refs))


def plan_summary(plan: DirectorPlan) -> str:
    mode = str(plan.raw.get("timelineMode") or "")
    if mode in ("gen_blank", "gen_image", "prompt_batch", "image_batch", "fl2v"):
        if mode == "fl2v":
            mode_label = "首尾帧 (fl2v)"
        elif mode in ("prompt_batch", "image_batch"):
            mode_label = f"批量生成 ({plan.global_task_key})"
        else:
            mode_label = "空白画布" if mode == "gen_blank" else "图片生成"
        lines = [
            f"MiniMax H3 Director [{mode_label}] ({plan.edit_mode}): "
            f"{plan.segment_count} segment(s), {plan.total_frames} frames @ {plan.frame_rate:.2f} fps",
            f"Output: {plan.width}×{plan.height} ({plan.output_mode})",
            f"Global task: {get_task_prompt_spec(plan.global_task_type).label}",
        ]
        for seg in plan.segments:
            lines.append(
                f"  #{seg.index + 1} [{seg.start_frame}:{seg.end_frame}] "
                f"{seg.frame_count}f — {seg.task_key} — {seg.prompt[:60]}{'…' if len(seg.prompt) > 60 else ''}"
            )
        return "\n".join(lines)

    mode_label = (
        f"视频编辑 ({plan.global_task_key})"
        if plan.global_task_key in {"v2v", "rv2v"}
        else "源视频时间轴"
    )
    lines = [
        f"MiniMax H3 Director [{mode_label}] ({plan.edit_mode}): {plan.segment_count} segment(s), "
        f"{plan.total_frames} frames @ {plan.frame_rate:.2f} fps",
    ]
    if plan.export_max_frames > 0 and plan.source_total_frames > plan.total_frames:
        lines.append(
            f"Export cap: {plan.total_frames}/{plan.source_total_frames} frames "
            f"(max {plan.export_max_frames})"
        )
    export_label = {
        "all": "全片导出（内存合并）",
        "segments": "分镜导出（Shot）",
        "scene": "场景导出（Scene）",
        "movie": "全片导出（Movie，流式）",
    }.get(plan.export_mode, plan.export_mode)
    lines.append(f"Export mode: {export_label}")
    if plan.continuity_enabled:
        from .segment_continuity import resolve_continuity_lock_pixels

        lock_px = resolve_continuity_lock_pixels(plan.continuity_overlap_frames)
        lines.append(
            f"Segment continuity: ON (overlap {plan.continuity_overlap_frames} "
            f"→ SCAIL lock {lock_px}f + appearance refs)"
        )
    else:
        lines.append("Segment continuity: OFF (official Studio / per-segment path)")
    if plan.run_indices is not None:
        selected = sorted(plan.run_indices)
        skipped = [i + 1 for i in range(plan.segment_count) if i not in plan.run_indices]
        lines.append(
            f"Run selection: {len(selected)}/{plan.segment_count} segment(s) "
            f"(#{', #'.join(str(i + 1) for i in selected)}; skipped #{', #'.join(map(str, skipped)) or 'none'})"
        )
    lines.append(f"Global task: {get_task_prompt_spec(plan.global_task_type).label}")
    for seg in plan.segments:
        lines.append(
            f"  #{seg.index + 1} [{seg.start_frame}:{seg.end_frame}] "
            f"{seg.frame_count}f — {seg.task_key} — {seg.prompt[:60]}{'…' if len(seg.prompt) > 60 else ''}"
        )
    return "\n".join(lines)
