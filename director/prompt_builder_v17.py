#!/usr/bin/env python3
"""V1.7 Phase 3：结构化生产 Prompt 草稿生成器（P0-C，V17_PLAN §6）。

用户拍板（2026-08-11 Phase 3）：
1. 定位 = 「结构化生产 Prompt 草稿」，不是「自动生成最终 H3 Prompt」。
   Visual/Camera/Style/Sound/Negative 五区 → 进 AI Draft 人工审核 →
   Phase 4 才按官方 base-en/ref-en 组装最终 H3 Prompt。
2. 生成方式 = 纯规则 + 模板组合：从 ProductionPlan 已结构化字段
   （Qwen 已补全的 visual_intent / emotion / actions / dialogue +
   场景时间/地点/天气）按 Prompt Template Registry 模板规则组合，
   零显存、结果可复现、导入快（与 Phase 2「纯规则先行」一致）。
3. 语言 = 中文草稿（与剧本语言一致，人工审核友好）；英文转换留 Phase 4。
4. Prompt Template Registry：prompt_templates/v1.json。改模板措辞不动解析逻辑；
   Shot 存 promptTemplateVersion，未来 h3-v2 老项目不重新生成。
5. generation_mode 判定（V17-P0-EXT 允许 Phase 3 做）：首镜 t2v / 续镜 r2v /
   含强连续动作 fl2v——作为「制作计划建议」进草稿输出，不改 SPA taskType(auto)。
6. V1.7 Phase 5（P1-D）：camera 措辞升级为运镜模板库 + 跨镜延续状态机
   （camera_template.pick_camera，场景内连贯 + 场景切换重置），草稿透出
   camera_template 模板 id 供前端下拉回显；build_shot_draft（单镜）保持
   Phase 3 intent 语义，build_plan_drafts（正式链路）默认用状态机。

⛔ 显存铁律：本模块纯规则、零 Ollama/GPU 依赖（与 asset_matcher 相同策略）。
   路由用 asyncio.to_thread 包裹只是避免阻塞事件循环。
"""

from __future__ import annotations

import difflib
import json
import os
from typing import Any, Dict, List, Optional

# V1.7 Phase 5（P1-D）：运镜模板库 + 跨镜延续状态机（场景内连贯 + 场景切换重置）。
# 纯规则零显存，与 Phase 2/3 同策略；P0-① 起相机决策统一走 director_intent
# build_plan_intents（同一 pick_camera 状态机），本模块不再直接调用。

# P0-4（2026-08-11 用户拍板）：shot_id/scene_id/镜头编号只作结构元数据，不进五区措辞。
# visual_intent/emotion/actions/subject 等文本字段一律过 strip_internal_markers 防御清洗。
from .prompt_sanitize import strip_internal_markers

# 模板版本约定：version "h3-v1" → 文件 prompt_templates/v1.json；h3-v2 → v2.json。
DEFAULT_TEMPLATE_VERSION = "h3-v1"
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_templates")

# generation_mode 建议值（Phase 3 判定；SPA taskType 保持 auto 由现有链路路由）。
MODE_T2V = "t2v"
MODE_R2V = "r2v"
MODE_FL2V = "fl2v"

# 强连续动作关键词（用于 generation_mode fl2v 判定，对齐 gen_timeline 阶段 D 智能路由
# 的用词思路，独立维护防耦合。保留精准：一次性位移/开合/打斗类，避免普通镜头误判）。
_STRONG_ACTIONS: tuple[str, ...] = (
    "开门", "推门", "关门", "开窗", "拉开", "拔出", "抽剑", "拔剑", "收剑", "挥剑",
    "斩下", "劈", "刺出", "挥舞", "跳跃", "跃起", "起跳", "落地", "翻越", "跨过",
    "跳过", "攀爬", "奔跑", "疾走", "追赶", "冲进", "冲出", "转身", "跌倒", "摔倒",
    "打斗", "战斗", "变身", "变形", "觉醒", "合体", "分裂", "推倒", "掀翻",
    "走过", "穿过", "走回", "离开", "进入",
    "推开", "收伞", "进门", "走出", "坐下", "起身", "拿起", "放下", "走进",
)

# 动作镜头意图判定用词（比 fl2v 判定稍泛：含微表情/朝向类，只影响 camera 措辞）。
_MOTION_HINTS: tuple[str, ...] = _STRONG_ACTIONS + (
    "环视", "抬头", "低头", "看向", "行走", "移步", "迈步",
)

# 强情绪关键词 → emotion 镜头（推近特写）。
_STRONG_EMOTIONS: tuple[str, ...] = (
    "紧张", "愤怒", "惊恐", "害怕", "悲伤", "震惊", "惊愕", "凝重", "焦躁", "崩溃", "欣喜",
)

# 特写意图关键词（visual_intent / source_text 命中 → close 镜头）。
# 只留明确摄影意图词，避免「眼神落寞」类情绪描写误触发特写。
_CLOSE_HINTS: tuple[str, ...] = ("特写", "面部", "手部", "指尖")


def _get(obj: Any, key: str, default: Any = "") -> Any:
    """dict 或 dataclass 统一取值（路由传入 dict，测试可用 dataclass）。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_name(obj: Any) -> str:
    """取实体名（Character/Prop dict 或 dataclass 通用）。"""
    return str(_get(obj, "name", "")).strip()


def _shot_text(shot: Any) -> str:
    """该镜用于关键词判定的文本（visual_intent + source_text + actions 拼接）。"""
    parts = [
        str(_get(shot, "visual_intent", "")),
        str(_get(shot, "source_text", "")),
    ]
    for a in _get(shot, "actions", []):
        parts.append(str(a))
    return " ".join(p for p in parts if p)


def load_template_registry(version: str = DEFAULT_TEMPLATE_VERSION) -> Dict[str, Any]:
    """读 Prompt Template Registry。缺省 h3-v1；找不到该版本抛 ValueError（防静默回退到旧措辞）。

    版本约定：version "h3-v1" → 文件 prompt_templates/v1.json。
    """
    file_name = version.replace("h3-", "") + ".json"
    path = os.path.join(_TEMPLATES_DIR, file_name)
    if not os.path.isfile(path):
        raise ValueError(f"Prompt 模板版本不存在：{version}（期望文件 {path}）")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("version"):
        raise ValueError(f"Prompt 模板文件格式非法：{path}")
    return data


def judge_generation_mode(
    shot: Any,
    *,
    has_prev: bool,
) -> str:
    """Generation Mode 建议（V17-P0-EXT：Phase 3 可做判定）。

    规则：命中强连续动作 → fl2v；场景首镜（无前镜）→ t2v；否则 → r2v（续接前镜）。
    注意：这只是「制作计划建议」，SPA Shot.taskType 保持 auto 由现有链路真实路由。
    """
    if any(kw in _shot_text(shot) for kw in _STRONG_ACTIONS):
        return MODE_FL2V
    return MODE_R2V if has_prev else MODE_T2V


# ---------- 五区草稿（规则 + 模板组合，中文） ----------


def _subject_text(shot: Any) -> str:
    names = [_get_name(c) for c in _get(shot, "characters", [])]
    names = [n for n in names if n]
    # P0-4：防御清洗（P0-3 已让角色注册表干净，这里兜底防脏数据进措辞）。
    names = [n for n in (strip_internal_markers(n) for n in names) if n]
    return "、".join(names) or "人物"


def _actions_hint(shot: Any) -> str:
    acts = [str(a).strip() for a in _get(shot, "actions", []) if str(a).strip()]
    acts = [a for a in (strip_internal_markers(a) for a in acts) if a]
    return "，".join(acts)


def _props_hint(shot: Any) -> str:
    props = [_get_name(p) for p in _get(shot, "props", [])]
    props = [p for p in props if p]
    props = [p for p in (strip_internal_markers(p) for p in props) if p]
    return ("，手持" + "、".join(props)) if props else ""


def _time_hint(scene: Any) -> str:
    t = str(_get(scene, "time", "")).strip()
    w = str(_get(scene, "weather", "")).strip()
    return "、".join(x for x in (t, w) if x)


def _pick_camera_intent(shot: Any, scene: Any, *, has_prev: bool, is_last_shot: bool) -> str:
    """运镜意图选择（规则在代码，措辞在模板）。优先级见注释。"""
    text = _shot_text(shot)
    has_cast = bool(_get(shot, "characters", []))

    # 1. 环境建立：场景首镜且无角色（纯环境交代）。
    if not has_prev and not has_cast:
        return "establish"
    # 2. 特写意图：明确提到特写/面部/细节。
    if any(kw in text for kw in _CLOSE_HINTS):
        return "close"
    # 3. 对白 → 正反打对切（优先于动作，避免「抬眼/说话」类对白镜被动作词误判）。
    if _get(shot, "dialogue", []):
        return "dialogue"
    # 4. 动作镜头（移动/开合/打斗）→ 跟移。
    if any(kw in text for kw in _MOTION_HINTS):
        return "motion"
    # 5. 强情绪 → 推近。
    if str(_get(shot, "emotion", "")).strip() and any(
        kw in str(_get(shot, "emotion", "")) for kw in _STRONG_EMOTIONS
    ):
        return "emotion"
    # 6. 场景尾镜 → 拉远收束。
    if is_last_shot:
        return "ending"
    return "default"


def _build_visual(tmpl: Dict[str, Any], shot: Any, scene: Any) -> str:
    sec = tmpl["sections"]["visual"]
    # P0-4：visual_intent 是 Qwen 原文，可能混入「镜头二」等内部标记 → 清洗后再进措辞。
    intent = strip_internal_markers(str(_get(shot, "visual_intent", "")).strip())
    if intent:
        return sec["template"].format(visual_intent=intent)
    fb = sec["fallback"]
    return fb.format(
        subject=_subject_text(shot),
        actions_hint=_actions_hint(shot),
        props_hint=_props_hint(shot),
        location_hint=str(_get(scene, "location_name", "")).strip() or "场景",
        time_hint=_time_hint(scene),
    )


def _build_camera(tmpl: Dict[str, Any], shot: Any, scene: Any, intent: str) -> str:
    intents = tmpl["sections"]["camera"]["intents"]
    cam = intents.get(intent) or tmpl["sections"]["camera"]["default"]
    # P0-4：emotion 同样可能含内部标记，清洗后再进措辞。
    emotion = strip_internal_markers(str(_get(shot, "emotion", "")).strip()) or "微妙"
    return cam.format(
        location=str(_get(scene, "location_name", "")).strip() or "场景",
        time=_time_hint(scene) or "当下",
        subject=_subject_text(shot),
        emotion=emotion,
    )


def _build_style(tmpl: Dict[str, Any], scene: Any) -> str:
    weather = str(_get(scene, "weather", "")).strip()
    palettes = tmpl.get("palettes", {})
    palette = palettes.get(weather) or palettes.get("default") or "自然色调，柔和漫射光"
    return tmpl["sections"]["style"]["template"].format(palette=palette)


def _build_sound(tmpl: Dict[str, Any], shot: Any, scene: Any) -> str:
    weather = str(_get(scene, "weather", "")).strip()
    ambients = tmpl.get("ambients", {})
    ambient = ambients.get(weather) or ambients.get("default") or ""
    hints = tmpl.get("sound_hints", {})
    dialogue_hint = hints.get("dialogue", "") if _get(shot, "dialogue", []) else ""
    # P0-4：emotion 清洗后再用于音乐提示判定与措辞。
    emotion = strip_internal_markers(str(_get(shot, "emotion", "")).strip())
    if emotion and any(kw in emotion for kw in _STRONG_EMOTIONS):
        music_hint = hints.get("music_tense", "")
    elif emotion:
        music_hint = hints.get("music_calm", "")
    else:
        music_hint = ""
    return tmpl["sections"]["sound"]["template"].format(
        ambient=ambient,
        dialogue_hint=dialogue_hint,
        music_hint=music_hint,
    )


def _build_negative(tmpl: Dict[str, Any]) -> str:
    return tmpl["sections"]["negative"]["template"]


def build_shot_draft(
    shot: Any,
    scene: Any,
    tmpl: Dict[str, Any],
    *,
    has_prev: bool,
    is_last_shot: bool,
) -> Dict[str, str]:
    """单镜五区中文草稿。"""
    intent = _pick_camera_intent(shot, scene, has_prev=has_prev, is_last_shot=is_last_shot)
    return {
        "visual": _build_visual(tmpl, shot, scene),
        "camera": _build_camera(tmpl, shot, scene, intent),
        "style": _build_style(tmpl, scene),
        "sound": _build_sound(tmpl, shot, scene),
        "negative": _build_negative(tmpl),
        "camera_intent": intent,
    }


def _intent_facts_sim(facts: List[str], text: str) -> bool:
    """原文事实与 AI 补全句句子级去重（ratio>=0.7 → AI 句跳过，原文优先）。"""
    return any(difflib.SequenceMatcher(None, f, text).ratio() >= 0.7 for f in facts)


def build_draft_from_intent(
    intent: Any,
    scene: Any,
    tmpl: Dict[str, Any],
    *,
    fallback: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """DirectorIntent → 五区中文草稿（P0-① 用户拍板：视觉原文事实优先）。

    - visual：user_original_intent 的 retained_facts **逐字优先**（AI 压缩不得覆盖），
      composition/emotion 作为补充句（句子级去重，保护原文）。
    - camera：camera_position/movement 与 h3_prompt_builder 同源（同一
      camera_template.pick_camera 状态机产物），不再重复跑相机决策。
    - style/sound/negative：保持 Prompt Template Registry 模板措辞（fallback 体系）。
    intent 为 None / 相机为空（use_camera_planner=False 降级）→ 回退 fallback。
    """
    d = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent or {})
    fb = fallback or {}

    facts = [f.strip() for f in (d.get("retained_facts") or []) if f.strip()]
    ai_parts: List[str] = []
    for field in ("composition", "emotion"):
        f = str(d.get(field, "") or "").strip()
        if f and not _intent_facts_sim(facts, f):
            ai_parts.append(f)
    visual = "，".join(facts)
    if ai_parts:
        visual = f"{visual}，{'，'.join(ai_parts)}" if visual else "，".join(ai_parts)

    camera = ""
    # P0-①：pick_camera 完整运镜措辞（含跨镜前缀/正反打/景别）优先；
    # 结构化景别+运镜措辞兜底（完整措辞已含地点/时间时不再重复拼接）。
    cam_desc = str(d.get("continuity", {}).get("camera_desc", "") or "").strip()
    cam_pos = str(d.get("camera_position", "") or "").strip()
    move = str(d.get("movement", "") or "").strip()
    if cam_desc:
        camera = f"镜头：{cam_desc}"
    elif cam_pos or move:
        loc = str(_get(scene, "location_name", "")).strip() or "场景"
        time_hint = _time_hint(scene)
        camera = f"镜头{cam_pos}{move}，交代{loc}{'，' + time_hint if time_hint else ''}。"
    camera_intent = str(d.get("continuity", {}).get("camera_intent", "") or "").strip() \
        or fb.get("camera_intent", "")

    return {
        "visual": visual or fb.get("visual", ""),
        "camera": camera or fb.get("camera", ""),
        "style": fb.get("style", ""),
        "sound": fb.get("sound", ""),
        "negative": fb.get("negative", ""),
        "camera_intent": camera_intent,
    }


def _as_intent_dict(intent: Any) -> Dict[str, Any]:
    """DirectorIntent → dict（供返回结构序列化；无 → 空）。"""
    if intent is None:
        return {}
    return intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)


def build_plan_drafts(
    plan: Any,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    *,
    use_camera_planner: bool = True,
) -> Dict[str, Any]:
    """遍历 ProductionPlan（dict 或 dataclass）为每镜生成五区草稿。

    Phase 3（P0-C）起：visual/style/sound/negative 由 Prompt Template Registry 组合；
    Phase 5（P1-D）起：camera 改用运镜模板库 + 跨镜延续状态机（camera_template.pick_camera）
    生成，措辞带场景内方向延续 / 对话反打 / 景别节奏，并透出 `camera_template`（模板 id，
    前端下拉回显用）。跨镜连贯 = 场景内逐镜传递 prev_state，**场景切换重置**。
    use_camera_planner=False 可回退 Phase 3 的 intent 措辞（测试/降级用）。

    P0-① 起（用户 2026-08-13 拍板）：正式链路先构建 DirectorIntent（build_plan_intents，
    相机决策同源），五区草稿由 DirectorIntent 派生：
      - visual = 原文事实逐字优先（AI 压缩不得覆盖）；
      - camera = intent.camera_position/movement（与 h3_prompt_builder 同一状态机）；
      - style/sound/negative 保持模板措辞；
    返回新增 `intents`（每个 DirectorIntent 的 dict，供前端三层可追溯）。

    返回 {template_version, drafts: [{scene_id, shot_id, generation_mode,
             camera_template, draft: {...}}], intents: {scene_id:shot_id: {...}}}。
    纯规则、零显存、无网络。
    """
    from .director_intent import build_plan_intents  # noqa: PLC0415 - 防顶层环

    tmpl = load_template_registry(template_version)
    scenes = _get(plan, "scenes", [])
    intents = build_plan_intents(plan, use_camera_planner=use_camera_planner)
    drafts: List[Dict[str, Any]] = []
    for scene in scenes:
        shots = _get(scene, "shots", [])
        n = len(shots)
        for i, shot in enumerate(shots):
            has_prev = i > 0
            is_last_shot = i == n - 1
            key = f"{str(_get(scene, 'scene_id', ''))}:{str(_get(shot, 'shot_id', ''))}"
            intent = intents.get(key)
            fallback = build_shot_draft(
                shot, scene, tmpl, has_prev=has_prev, is_last_shot=is_last_shot
            )
            draft = build_draft_from_intent(intent, scene, tmpl, fallback=fallback)
            camera_template_id = ""
            if intent is not None:
                camera_template_id = str(
                    (intent.to_dict() if hasattr(intent, "to_dict") else intent)
                    .get("continuity", {})
                    .get("camera_template", "")
                    or ""
                )
            mode = judge_generation_mode(shot, has_prev=has_prev)
            drafts.append(
                {
                    "scene_id": str(_get(scene, "scene_id", "")),
                    "shot_id": str(_get(shot, "shot_id", "")),
                    "generation_mode": mode,
                    "camera_template": camera_template_id,
                    "draft": draft,
                }
            )
    return {
        "template_version": tmpl["version"],
        "drafts": drafts,
        "intents": {k: _as_intent_dict(v) for k, v in intents.items()},
    }
