"""Qwen3-VL 导演反馈（待办⑤，里程碑 A：一级状态提取）。

设计决策（用户 2026-08-07）：
- **Qwen 是场记不是导演**：只负责「看画面 → 提取状态 → 更新状态库」，绝不直接决定生成。
- 一级状态提取除 角色/地点/时间/动作 外，**必须含 character_state（pose/direction/emotion）+
  camera_state（shot/angle）**——接缝问题的本质就是缺镜头状态，这两个字段供未来
  FL2VA/R2V 路由决策使用。
- 用户手填优先：executor 里用户填了 ``state_change`` 用用户的，没填才用 Qwen 提取的。
- 强 JSON 输出 + 容错解析（失败重试一次），防模型输出非 JSON。
"""

from __future__ import annotations

import json
import logging
import re

from typing import Any

from .vlm_backends import VisionBackend

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.qwen_vl_feedback")

# 一级状态提取的强 JSON 模板（中文，Qwen3-VL 双语模型可稳定输出）。
STATE_EXTRACT_PROMPT_TMPL = """你是视频分镜场记（continuity clerk），负责从单帧画面中记录镜头状态，供导演系统做下一镜头的接续与路由决策。

看图并参考上一镜头状态与镜头描述，严格输出 JSON（不要任何解释、不要 markdown 代码块）：

{{
  "character": "画面中的主要角色是谁（人名）",
  "location": "画面中的地点",
  "time": "时间段或光线氛围（如：黄昏/夜晚/白天）",
  "character_state": {{
    "pose": "人物姿态（如：站立/拔剑/奔跑/低头）",
    "direction": "人物朝向（如：朝向天碑/面朝镜头/背对镜头）",
    "emotion": "人物情绪（如：震惊/凝重/平静）"
  }},
  "camera_state": {{
    "shot": "景别（如：远景/全景/中景/近景/特写）",
    "angle": "机位角度（如：正面/侧面/俯拍/仰拍）"
  }},
  "next_shot_hint": "根据当前状态，下一镜头建议保持或切换什么（一句话）"
}}

上一镜头状态：{prev_state}
本镜头描述：{prompt}
只输出 JSON。"""

# 重试提示词：模型输出非 JSON 时追加。
RETRY_HINT = "\n注意：你上一次输出不是合法 JSON。请只输出一个 JSON 对象，不要包含任何其他文字。"


# ---------------------------------------------------------------------------
# JSON 容错解析
# ---------------------------------------------------------------------------
def _extract_json_block(text: str) -> dict[str, Any] | None:
    """从模型原始输出中尽力提取 JSON 对象。

    容忍：markdown 代码块包裹、前后杂文字、尾随逗号。解析失败返回 None。
    """
    if not text:
        return None
    # 先剥掉 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    # 找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    blob = text[start:end + 1]
    # 容错：去掉尾随逗号（如 {"a":1,}）
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        # 二次尝试：去掉行注释（//）与块注释（/* */），模型偶尔会输出注释
        blob = re.sub(r"//[^\n]*", "", blob)
        blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.S)
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def _coerce_str(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default


# ---------------------------------------------------------------------------
# 一级：状态提取
# ---------------------------------------------------------------------------
def extract_state_from_pil(
    backend: VisionBackend,
    images: list[Any],
    prompt: str,
    *,
    prev_state: str = "",
    max_tokens: int = 512,
) -> dict[str, Any]:
    """用 VLM 后端从画面中提取镜头状态，返回结构化 dict。

    返回字典始终含顶层键（character/location/time/character_state/camera_state），
    模型解析失败时对应值为空串/空 dict，由调用方决定兜底。
    """
    user_prompt = STATE_EXTRACT_PROMPT_TMPL.format(
        prev_state=prev_state or "（无）",
        prompt=prompt or "（无）",
    )
    text = backend.analyze(images=images, prompt=user_prompt, max_tokens=max_tokens)
    obj = _extract_json_block(text)
    if obj is None:
        # 失败重试一次：追加提示只输出 JSON
        log.warning("Qwen3-VL 状态提取输出非 JSON，重试一次。原文: %.200s", text)
        text2 = backend.analyze(
            images=images,
            prompt=user_prompt + RETRY_HINT,
            max_tokens=max_tokens,
        )
        obj = _extract_json_block(text2)
        if obj is None:
            log.warning("Qwen3-VL 状态提取重试仍非 JSON，返回空状态。原文: %.200s", text2)

    cs = obj.get("character_state") if obj and isinstance(obj.get("character_state"), dict) else {}
    cam = obj.get("camera_state") if obj and isinstance(obj.get("camera_state"), dict) else {}
    return {
        "character": _coerce_str(obj.get("character")) if obj else "",
        "location": _coerce_str(obj.get("location")) if obj else "",
        "time": _coerce_str(obj.get("time")) if obj else "",
        "character_state": {
            "pose": _coerce_str(cs.get("pose")),
            "direction": _coerce_str(cs.get("direction")),
            "emotion": _coerce_str(cs.get("emotion")),
        },
        "camera_state": {
            "shot": _coerce_str(cam.get("shot")),
            "angle": _coerce_str(cam.get("angle")),
        },
        "next_shot_hint": _coerce_str(obj.get("next_shot_hint")) if obj else "",
    }


def format_state_prefix(state: dict[str, Any]) -> str:
    """把提取的状态拼成状态跟踪的前缀文本（供 executor 拼接进下一镜 prompt）。

    格式：地点 + 时间，角色 + 姿态/朝向/情绪，镜头景别/角度。
    镜头状态（camera_state）必须入前缀——用户反馈②：接缝问题的本质就是缺镜头状态。
    """
    parts: list[str] = []
    if state.get("location"):
        parts.append(state["location"])
    if state.get("time"):
        parts.append(state["time"])
    if state.get("character"):
        parts.append(state["character"])
    cs = state.get("character_state") or {}
    csp = [cs.get("pose"), cs.get("direction"), cs.get("emotion")]
    csp = [s for s in csp if s]
    if csp:
        parts.append("，".join(csp))
    cam = state.get("camera_state") or {}
    cap = [cam.get("shot"), cam.get("angle")]
    cap = [s for s in cap if s]
    if cap:
        parts.append("镜头：" + "，".join(cap))
    if parts:
        return "；".join(parts)
    return ""


# ---------------------------------------------------------------------------
# 从 tensor 帧提取（executor 常用入口）
# ---------------------------------------------------------------------------
def extract_state_from_tensor(
    backend: VisionBackend,
    frames: Any,
    prompt: str,
    *,
    prev_state: str = "",
    max_tokens: int = 512,
) -> dict[str, Any]:
    """从 torch 帧张量 [N,H,W,C]（0~1 float）取最后一帧转 PIL 再提取状态。

    frames 为空或非 4D 时返回空状态（不抛错，保证反馈模块不影响主生成流程）。
    """
    if frames is None:
        return _empty_state()
    try:
        import torch

        if not isinstance(frames, torch.Tensor) or frames.ndim != 4 or int(frames.shape[0]) < 1:
            return _empty_state()
        from PIL import Image

        frame = frames[-1].detach().cpu()
        # H3 输出可能是 [H,W,C] 或 [C,H,W]；按 C==3 判方向
        if frame.ndim == 3 and int(frame.shape[0]) == 3 and int(frame.shape[2]) != 3:
            frame = frame.permute(1, 2, 0)
        arr = frame.clamp(0, 1).mul(255).to(torch.uint8).numpy()
        pil = Image.fromarray(arr)
        return extract_state_from_pil(
            backend, [pil], prompt, prev_state=prev_state, max_tokens=max_tokens
        )
    except Exception as exc:  # 反馈失败绝不阻断主流程
        log.warning("Qwen3-VL 从 tensor 提取状态失败: %s", exc)
        return _empty_state()


def _empty_state() -> dict[str, Any]:
    return {
        "character": "",
        "location": "",
        "time": "",
        "character_state": {"pose": "", "direction": "", "emotion": ""},
        "camera_state": {"shot": "", "angle": ""},
        "next_shot_hint": "",
    }


# ---------------------------------------------------------------------------
# 二级：一致性检测（里程碑 B，用户 2026-08-07 设计确认）
# ---------------------------------------------------------------------------
# 核心：把本镜生成帧 vs 全局角色/场景资产图对比，检验「生成的是不是设定里的
# 角色/场景」。输出结构化判断（match 布尔 + reason），不是 0~1 分数。
# - character_match: 生成帧主角 vs 角色资产图（面部/发型/身材）
# - scene_match: 生成帧环境 vs 场景资产图（建筑/布局/光线氛围）
# - clothing_match: 生成帧服装 vs 角色资产图（款式/颜色）
# - reason: 一句话说明哪些一致、哪些不一致
# 关键镜头才跑（有资产注入的镜头）；match=false 只报告提醒，不重跑（重跑留给三级）。

CONSISTENCY_PROMPT_TMPL = """你是视频分镜质检员（continuity checker）。对比「角色/场景参考图」与本镜生成帧，判断生成画面是否忠于设定。

图片说明：
- <Picture 1> 角色参考图：角色「{character_name}」的设定图（面部/发型/服装/身材）
- <Picture 2> 场景参考图：场景「{location_name}」的设定图（建筑/布局/光线氛围）
- <Picture 3> 本镜生成帧（待检画面）

严格输出 JSON（不要任何解释、不要 markdown 代码块）：
{{
  "character_match": true,
  "scene_match": true,
  "clothing_match": true,
  "reason": "一句话说明：哪些一致、哪些不一致（例如：人物面部发型一致，但服装颜色从青色变成蓝色）"
}}

只输出 JSON。"""

# 一致性检测重试提示词。
CONSISTENCY_RETRY_HINT = "\n注意：你上一次输出不是合法 JSON。请只输出一个 JSON 对象，不要包含任何其他文字。"


def _empty_consistency() -> dict[str, Any]:
    return {
        "character_match": None,
        "scene_match": None,
        "clothing_match": None,
        "reason": "",
    }


def check_consistency(
    backend: VisionBackend,
    gen_frame: Any,
    *,
    character_asset: Any = None,
    character_name: str = "",
    location_asset: Any = None,
    location_name: str = "",
    max_tokens: int = 512,
) -> dict[str, Any]:
    """二级一致性检测：本镜生成帧 vs 角色/场景资产图。

    Args:
        backend: VLM 后端。
        gen_frame: 生成帧（PIL Image 或 torch.Tensor [H,W,C] 0~1）。取首帧即可——首帧最能体现
            「生成的是不是设定角色/场景」（后续帧运动/表情变化会引入噪声）。
        character_asset: 角色资产图（PIL / tensor），None 则不检查角色维度。
        character_name: 角色名（用于提示词）。
        location_asset: 场景资产图，None 则不检查场景维度。
        location_name: 场景名。

    返回 dict：{character_match, scene_match, clothing_match, reason}。
    match 字段 True/False/None（None=该维度未检查）。失败返回空结果（不抛错）。
    """
    try:
        refs: list[tuple[str, Any]] = []
        if character_asset is not None:
            refs.append(("character", character_asset))
        if location_asset is not None:
            refs.append(("location", location_asset))
        if not refs:
            # 没有任何资产图可对比 → 无法检测
            return _empty_consistency()

        images: list[Any] = []
        for _kind, asset in refs:
            images.append(_to_pil(asset))
        images.append(_to_pil(gen_frame))

        _cn = character_name or "主角"
        _ln = location_name or "当前场景"
        user_prompt = CONSISTENCY_PROMPT_TMPL.format(
            character_name=_cn, location_name=_ln
        )
        text = backend.analyze(images=images, prompt=user_prompt, max_tokens=max_tokens)
        obj = _extract_json_block(text)
        if obj is None:
            log.warning("Qwen3-VL 一致性检测输出非 JSON，重试一次。原文: %.200s", text)
            text2 = backend.analyze(
                images=images,
                prompt=user_prompt + CONSISTENCY_RETRY_HINT,
                max_tokens=max_tokens,
            )
            obj = _extract_json_block(text2)
            if obj is None:
                log.warning("Qwen3-VL 一致性检测重试仍非 JSON。原文: %.200s", text2)
        if not isinstance(obj, dict):
            return _empty_consistency()

        def _match(key: str) -> bool | None:
            v = obj.get(key)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in {"true", "yes", "一致", "是"}
            return None

        return {
            "character_match": _match("character_match"),
            "scene_match": _match("scene_match"),
            "clothing_match": _match("clothing_match"),
            "reason": str(obj.get("reason") or "").strip(),
        }
    except Exception as exc:  # 反馈失败绝不阻断主流程
        log.warning("Qwen3-VL 一致性检测失败: %s", exc)
        return _empty_consistency()


def _to_pil(img: Any) -> "Any":
    """把 PIL / torch.Tensor / numpy 统一转成 PIL RGB（供 VLM 后端）。"""
    try:
        from PIL import Image
        import torch
        import numpy as np

        if isinstance(img, Image.Image):
            return img.convert("RGB")
        if isinstance(img, torch.Tensor):
            t = img.detach().cpu().float()
            if t.ndim == 4:
                t = t[0]
            if t.ndim == 3 and int(t.shape[0]) == 3 and int(t.shape[2]) != 3:
                t = t.permute(1, 2, 0)
            arr = t.clamp(0, 1).mul(255).to(torch.uint8).numpy()
            return Image.fromarray(arr).convert("RGB")
        if isinstance(img, np.ndarray):
            if img.ndim == 3 and img.shape[2] == 3:
                return Image.fromarray(img).convert("RGB")
        return Image.open(img).convert("RGB")
    except Exception as exc:
        log.warning("Qwen3-VL 图片转换失败: %s", exc)
        raise


def check_consistency_from_tensors(
    backend: VisionBackend,
    gen_frames: Any,
    *,
    character_asset: Any = None,
    character_name: str = "",
    location_asset: Any = None,
    location_name: str = "",
    max_tokens: int = 512,
) -> dict[str, Any]:
    """executor 入口：直接喂帧张量 + 资产张量，内部统一转 PIL 后调用 check_consistency。"""
    try:
        return check_consistency(
            backend,
            _to_pil(gen_frames),
            character_asset=character_asset,
            character_name=character_name,
            location_asset=location_asset,
            location_name=location_name,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        log.warning("Qwen3-VL 从 tensor 一致性检测失败: %s", exc)
        return _empty_consistency()


# ---------------------------------------------------------------------------
# 三级：失败重跑检测（里程碑 C，用户 2026-08-07 设计确认）
# ---------------------------------------------------------------------------
# Qwen 只负责"语义判断"——看生成帧是否存在严重质量问题（画面崩坏/多手/换脸等）。
# 是否真的重跑必须与 state_rule_check.py 的规则检测**双确认**：
#   Qwen 报严重问题 + 规则检测到客观像素异常 → 才重跑（换种子、限 1 次）。
# 防止 Qwen 单方面误判 → 无限生成。
# 三级默认关（成本最高，每段多一次 VLM 推理），用户显式开启才启用。

DETECT_ISSUE_PROMPT_TMPL = """你是视频分镜质检员（quality inspector）。看本镜生成画面，判断是否存在严重质量问题。

严重问题（观众一眼察觉、穿帮、需重拍）：
- 画面崩坏/生成伪影/画面撕裂
- 多手/多指/肢体畸形
- 换脸/面部崩坏/五官错位
- 场景崩坏/结构错乱
- 时间错乱/光线突变

轻微问题（可接受，不必重拍）：
- 服装颜色略有偏差
- 表情/姿态与预期略有出入
- 光线氛围略有差异

严格输出 JSON（不要任何解释、不要 markdown 代码块）：
{{
  "issues": ["疑似多手", "画面崩坏"],
  "severity": "high",
  "reason": "一句话说明"
}}

若无严重问题：{{"issues": [], "severity": "low", "reason": "画面正常"}}

本镜描述：{prompt}
只输出 JSON。"""

DETECT_ISSUE_RETRY_HINT = "\n注意：你上一次输出不是合法 JSON。请只输出一个 JSON 对象，不要包含任何其他文字。"

# 严重问题关键词（executor 过滤用）——Qwen 输出 issues 属于这些类别才进入重跑候选。
SEVERE_ISSUE_KEYWORDS = (
    "崩坏", "伪影", "撕裂", "多手", "多指", "肢体", "畸形", "换脸",
    "面部", "五官", "错位", "场景", "结构", "时间错乱", "突变",
    "变形", "扭曲", "融合", "残缺", "异常",
)


def _empty_issue() -> dict[str, Any]:
    return {"issues": [], "severity": "low", "reason": ""}


def detect_issue(
    backend: VisionBackend,
    frames: Any,
    *,
    prompt: str = "",
    max_tokens: int = 512,
    sample_frames: int = 3,
) -> dict[str, Any]:
    """三级：Qwen 语义级质量检测——本镜生成帧是否存在严重问题。

    Args:
        backend: VLM 后端。
        frames: 生成帧（torch.Tensor [N,H,W,C] 0~1 / PIL / list[PIL]）。
        prompt: 本镜提示词（给模型上下文）。
        sample_frames: 采样帧数（首/中/尾等距取，崩坏类问题看多帧更稳）。

    返回 {issues: list[str], severity: "high"/"low", reason: str}。
    解析失败/异常返回空结果（不抛错，不阻断主流程）。
    """
    try:
        imgs = _sample_to_pil(frames, sample_frames)
        if not imgs:
            return _empty_issue()
        user_prompt = DETECT_ISSUE_PROMPT_TMPL.format(prompt=prompt or "（无）")
        text = backend.analyze(images=imgs, prompt=user_prompt, max_tokens=max_tokens)
        obj = _extract_json_block(text)
        if obj is None:
            log.warning("Qwen3-VL 问题检测输出非 JSON，重试一次。原文: %.200s", text)
            text2 = backend.analyze(
                images=imgs,
                prompt=user_prompt + DETECT_ISSUE_RETRY_HINT,
                max_tokens=max_tokens,
            )
            obj = _extract_json_block(text2)
            if obj is None:
                log.warning("Qwen3-VL 问题检测重试仍非 JSON。原文: %.200s", text2)
        if not isinstance(obj, dict):
            return _empty_issue()

        raw_issues = obj.get("issues") or []
        issues: list[str] = []
        if isinstance(raw_issues, list):
            for s in raw_issues:
                s2 = str(s).strip()
                if s2 and s2 not in issues:
                    issues.append(s2)
        elif isinstance(raw_issues, str) and raw_issues.strip():
            issues.append(raw_issues.strip())

        sev = str(obj.get("severity") or "").strip().lower()
        if sev not in ("high", "low"):
            # 模型缺失/乱写 severity 时按 issues 内容兜底：命中严重关键词才判 high，
            # 否则即便有 issues 也只是轻微问题，不进入重跑候选（防止 Qwen 单方面误判）。
            sev = (
                "high"
                if any(k in s for s in issues for k in SEVERE_ISSUE_KEYWORDS)
                else "low"
            )
        return {
            "issues": issues,
            "severity": sev,
            "reason": str(obj.get("reason") or "").strip(),
        }
    except Exception as exc:  # 反馈失败绝不阻断主流程
        log.warning("Qwen3-VL 问题检测失败: %s", exc)
        return _empty_issue()


def detect_issue_from_tensors(
    backend: VisionBackend,
    gen_frames: Any,
    *,
    prompt: str = "",
    max_tokens: int = 512,
    sample_frames: int = 3,
) -> dict[str, Any]:
    """executor 入口：直接喂帧张量，内部采样转 PIL 后调用 detect_issue。"""
    try:
        return detect_issue(
            backend, gen_frames, prompt=prompt,
            max_tokens=max_tokens, sample_frames=sample_frames,
        )
    except Exception as exc:
        log.warning("Qwen3-VL 从 tensor 问题检测失败: %s", exc)
        return _empty_issue()


def _sample_to_pil(frames: Any, sample_frames: int) -> list[Any]:
    """把帧张量/列表等距采样最多 sample_frames 张转 PIL RGB。

    崩坏/多手等结构性问题是"整段都有"，首/中/尾采样即可覆盖，且省 VLM token。
    """
    import torch

    if frames is None:
        return []
    try:
        if isinstance(frames, torch.Tensor):
            n = int(frames.shape[0])
            if n < 1 or frames.ndim != 4:
                return []
            count = max(1, min(sample_frames, n))
            if count == 1:
                idxs = [0]
            else:
                step = n / count
                idxs = [min(n - 1, int(i * step)) for i in range(count)]
            return [_to_pil(frames[i]) for i in idxs]
        if isinstance(frames, (list, tuple)):
            return [_to_pil(f) for f in frames[:sample_frames]]
        return [_to_pil(frames)]
    except Exception as exc:
        log.warning("Qwen3-VL 帧采样失败: %s", exc)
        return []

