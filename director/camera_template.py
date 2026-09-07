#!/usr/bin/env python3
"""V1.7 Phase 5（P1-D）：运镜模板库 + 跨镜延续状态机（V17_PLAN §7）。

用户拍板（2026-08-11 Phase 5 三决策）：
1. **跨镜连贯边界 = 场景内连贯 + 场景切换重置**：previousCameraState 在场景内
   逐镜传递（方向延续 / 正反打反接 / 景别节奏），新场景首镜从 establish 重新开始。
2. **模板数据源 = 后端权威 + 前端拉取**：模板库在本模块（纯规则零显存），
   /minimax/director/camera/templates 路由返回模板列表供前端下拉；
   前端不维护第二份 JSON，避免双份漂移。
3. **前端交互 = 模板下拉 + 手编共存**：Workbench 摄影分区下拉选模板自动填
   cameraText，仍可手改；Shot 存 cameraTemplateVersion 进 Prompt。

模板库规模（V17_PLAN §7 用户拍板）：10 条基础 + 5 条常用补充 = 15 条。

⛔ 显存铁律：本模块纯规则、零 Ollama/GPU 依赖（与 asset_matcher / prompt_builder 同策略）。
   不加载任何深度学习模型、不访问网络、不做任何模型生命周期管理。

核心入口：
- `list_camera_templates()` → 模板库 JSON（路由/前端下拉用）
- `pick_camera(shot, scene, prev_state=None, *, is_last_shot=False)` →
    (camera_desc, intent, next_state)  场景内逐镜传 next_state 作下镜 prev_state
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# P0-4（2026-08-11 用户拍板）：shot_id/scene_id/镜头编号只作结构元数据，不进 camera 措辞。
from .prompt_sanitize import strip_internal_markers

# ---------- 运镜模板库（V17_PLAN §7 全量收录） ----------
#
# 字段说明：
#   id            模板标识（前端下拉 value；Shot.cameraTemplateVersion 记录）
#   name          中文名（下拉显示）
#   intent        粗分类（兼容 Phase 3 prompt_builder 的 camera_intent 语义；
#                 establish/dialogue/motion/emotion/close/ending/default 之外，
#                 补充战斗/蒙太奇/空镜/环绕/升降 5 类）
#   keywords      剧情意图关键词（pick_camera 判定用；命中优先级=模板出现顺序）
#   template      中文措辞模板（{location}/{time}/{subject}/{emotion}/{object} 占位）
#   shot_size     景别（跨镜状态机用）：wide / medium / close_up
#   movement      运镜（跨镜状态机用）：pan/tracking/dolly/orbit/boom/handheld/
#                 push_in/pull_out/static
#   camera_position 机位：front / over_shoulder / side / high / low / behind
#   movement_direction 运动方向：left / right / in / out / none（跨镜方向延续用）

CAMERA_TEMPLATES: List[Dict[str, Any]] = [
    # ---- 10 条基础模板（V17_PLAN §7） ----
    {
        "id": "establish",
        "name": "环境建立",
        "intent": "establish",
        "keywords": [],
        "template": "全景航拍俯冲推进（crane-down dive），掠过{location}上空，{time}氛围扑面而来。",
        "shot_size": "wide",
        "movement": "pan",
        "camera_position": "front",
        "movement_direction": "none",
    },
    {
        "id": "enter",
        "name": "人物登场",
        "intent": "enter",
        "keywords": ["登场", "走入", "走进", "现身", "出现", "踏入", "登场", "迈入"],
        "template": "全景跟拍{subject}登场，快速推近至中景（tracking push-in），人物入画醒目。",
        "shot_size": "medium",
        "movement": "push_in",
        "camera_position": "front",
        "movement_direction": "in",
    },
    {
        "id": "walk",
        "name": "人物行走",
        "intent": "motion",
        "keywords": ["行走", "走向", "走回", "走进", "走出", "走过", "迈步", "移步", "前行",
                     "向前走", "开门", "推门", "推开", "进门", "穿过", "进入", "离开",
                     "拿起", "放下", "收伞"],
        "template": "中景侧面跟移（lateral tracking），紧贴{subject}脚步节奏，背景匀速滑过。",
        "shot_size": "medium",
        "movement": "tracking",
        "camera_position": "side",
        "movement_direction": "right",
    },
    {
        "id": "tense",
        "name": "紧张发现",
        "intent": "emotion",
        "keywords": ["紧张", "发现", "警觉", "察觉", "震惊", "惊觉", "屏息", "僵住"],
        "template": "低角度快速推近（low-angle push-in），猛然逼近{subject}面部，压迫感骤升。",
        "shot_size": "close_up",
        "movement": "push_in",
        "camera_position": "front",
        "movement_direction": "in",
    },
    {
        "id": "see_object",
        "name": "看见重要物体",
        "intent": "close",
        "keywords": ["看见", "看到", "注视", "盯着", "望向", "看到物体", "发现物体"],
        "template": "过肩镜头（over-shoulder）锁向{object}，骤推特写揭示细节。",
        "shot_size": "medium",
        "movement": "push_in",
        "camera_position": "over_shoulder",
        "movement_direction": "in",
    },
    {
        "id": "dialogue",
        "name": "两人对话",
        "intent": "dialogue",
        "keywords": [],
        "template": "中景正反打对切（shot-reverse-shot），{subject}轮流入画，对白间小幅推近（subtle push-in），节奏紧凑。",
        "shot_size": "medium",
        "movement": "push_in",
        "camera_position": "front",
        "movement_direction": "in",
    },
    {
        "id": "emotion_close",
        "name": "情绪特写",
        "intent": "emotion",
        "keywords": [],
        "template": "特写快速推近（rapid push-in）{subject}面部，放大{emotion}的情绪张力。",
        "shot_size": "close_up",
        "movement": "push_in",
        "camera_position": "front",
        "movement_direction": "in",
    },
    {
        "id": "chase",
        "name": "追逐",
        "intent": "motion",
        "keywords": ["追逐", "追赶", "逃跑", "狂奔", "疾走", "飞奔", "追捕"],
        "template": "手持跟拍（handheld chase）高速追移{subject}，画面晃动急促，动感强烈。",
        "shot_size": "medium",
        "movement": "handheld",
        "camera_position": "behind",
        "movement_direction": "right",
    },
    {
        "id": "memory",
        "name": "回忆",
        "intent": "memory",
        "keywords": ["回忆", "回想", "闪回", "想起", "昔日", "曾经", "当年"],
        "template": "横移入画进入回忆氛围（dolly side-tracking），雾气朦胧，运镜悠远飘渺。",
        "shot_size": "wide",
        "movement": "dolly",
        "camera_position": "side",
        "movement_direction": "right",
    },
    {
        "id": "ending",
        "name": "结尾",
        "intent": "ending",
        "keywords": [],
        "template": "中景拉远（slow dolly-out），{subject}渐行渐远，镜头缓缓升起（crane-up），余韵收束。",
        "shot_size": "wide",
        "movement": "pull_out",
        "camera_position": "front",
        "movement_direction": "out",
    },
    # ---- 5 条常用补充模板（V17_PLAN §7 用户拍板补充） ----
    {
        "id": "combat",
        "name": "战斗打斗",
        "intent": "combat",
        "keywords": ["战斗", "打斗", "交手", "拼杀", "厮杀", "武斗", "挥剑", "格斗"],
        "template": "手持甩镜（whip pan）快速切换，急促推拉交错，打斗快剪感强烈。",
        "shot_size": "medium",
        "movement": "handheld",
        "camera_position": "side",
        "movement_direction": "right",
    },
    {
        "id": "montage",
        "name": "蒙太奇",
        "intent": "montage",
        "keywords": ["蒙太奇", "时间流逝", "日复一日", "过了几天", "岁月", "时光流转", "季节"],
        "template": "横移俯仰摇动（dolly + tilt），蒙太奇式时间流逝感，光影流转。",
        "shot_size": "wide",
        "movement": "dolly",
        "camera_position": "front",
        "movement_direction": "none",
    },
    {
        "id": "empty_transition",
        "name": "空镜转场",
        "intent": "empty_transition",
        "keywords": ["空镜", "转场", "空无一人", "无人", "空旷"],
        "template": "环境空镜快速摇移（pan sweep），掠过{location}，做场景过渡。",
        "shot_size": "wide",
        "movement": "pan",
        "camera_position": "front",
        "movement_direction": "right",
    },
    {
        "id": "orbit",
        "name": "环绕揭示",
        "intent": "orbit",
        "keywords": ["环绕", "绕", "一圈", "揭示全貌", "全景环绕"],
        "template": "环绕{subject}半周至一周（arc orbit），配合升降揭示全貌，空间立体感强。",
        "shot_size": "wide",
        "movement": "orbit",
        "camera_position": "side",
        "movement_direction": "right",
    },
    {
        "id": "boom",
        "name": "升降强化",
        "intent": "boom",
        "keywords": ["升起", "降下", "升腾", "俯瞰", "仰望", "上升", "下落", "升降"],
        "template": "升降运镜（crane up/down）强化空间关系与情绪，起幅落幅均带节奏。",
        "shot_size": "wide",
        "movement": "boom",
        "camera_position": "high",
        "movement_direction": "out",
    },
]

# 兜底模板（无任何关键词命中且非对话/非特写/非尾镜 → 中景固定）。
# #600→#601（2026-08-17 用户拍板「中性台词静止优先」）：v2 的「微推（subtle push-in）」
# 每镜必动造成过度；改为静止机位——该静止的近景更有力量，只有情绪爆点才推镜。
_DEFAULT_TEMPLATE: Dict[str, Any] = {
    "id": "default",
    "name": "中景固定",
    "intent": "default",
    "keywords": [],
    "template": "中景固定机位静止（static medium shot），聚焦{subject}状态，画面沉稳，情绪内敛。",
    "shot_size": "medium",
    "movement": "static",
    "camera_position": "front",
    "movement_direction": "none",
}

# 粗分类与模板 id 索引（后端路由 /camera/templates 返回；前端下拉用）。
_TEMPLATE_INDEX: Dict[str, Dict[str, Any]] = {
    t["id"]: t for t in CAMERA_TEMPLATES
}
_TEMPLATE_INDEX["default"] = _DEFAULT_TEMPLATE


def list_camera_templates() -> List[Dict[str, Any]]:
    """返回模板库 JSON（不含 keywords 内部词表，前端下拉只需 id/name/intent/template）。"""
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "intent": t["intent"],
            "template": t["template"],
        }
        for t in CAMERA_TEMPLATES
    ]


# ---------- 意图判定（对齐 Phase 3 prompt_builder 的优先级语义） ----------

# 特写意图关键词（visual_intent / source_text 命中 → close）。
_CLOSE_HINTS: Tuple[str, ...] = ("特写", "面部", "手部", "指尖")

# 强情绪关键词 → emotion 镜头。
_STRONG_EMOTIONS: Tuple[str, ...] = (
    "紧张", "愤怒", "惊恐", "害怕", "悲伤", "震惊", "惊愕", "凝重", "焦躁", "崩溃", "欣喜",
)


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


def _subject_text(shot: Any) -> str:
    names = [_get_name(c) for c in _get(shot, "characters", [])]
    names = [n for n in names if n]
    # P0-4：防御清洗（P0-3 已让角色注册表干净，这里兜底防脏数据进 camera 措辞）。
    names = [n for n in (strip_internal_markers(n) for n in names) if n]
    return "、".join(names) or "人物"


def _props_text(shot: Any) -> str:
    props = [_get_name(p) for p in _get(shot, "props", [])]
    props = [p for p in props if p]
    props = [p for p in (strip_internal_markers(p) for p in props) if p]
    return "、".join(props) or "目标"


def _pick_template(shot: Any, scene: Any, *, has_prev: bool, is_last_shot: bool) -> Dict[str, Any]:
    """按优先级命中模板。优先级见注释。"""
    text = _shot_text(shot)
    has_cast = bool(_get(shot, "characters", []))

    # 1. 环境建立：场景首镜且无角色（纯环境交代）。
    if not has_prev and not has_cast:
        return _TEMPLATE_INDEX["establish"]
    # 2. 特写意图：明确提到特写/面部/细节。
    if any(kw in text for kw in _CLOSE_HINTS):
        return _TEMPLATE_INDEX["see_object"] if any(
            kw in text for kw in ("看见", "看到", "注视", "盯着", "望向")
        ) else _TEMPLATE_INDEX["emotion_close"]
    # 3. 对白 → 正反打对切（优先于动作，避免「抬眼/说话」类对白镜被动作词误判）。
    if _get(shot, "dialogue", []):
        return _TEMPLATE_INDEX["dialogue"]
    # 4. 动作镜头（移动/开合/打斗）→ 按关键词命中细分模板（walk/chase/combat/enter…）。
    for t in CAMERA_TEMPLATES:
        if t["keywords"] and any(kw in text for kw in t["keywords"]):
            return t
    # 5. 强情绪 → 推近。
    if str(_get(shot, "emotion", "")).strip() and any(
        kw in str(_get(shot, "emotion", "")) for kw in _STRONG_EMOTIONS
    ):
        return _TEMPLATE_INDEX["emotion_close"]
    # 6. 场景尾镜 → 拉远收束。
    if is_last_shot:
        return _TEMPLATE_INDEX["ending"]
    return _DEFAULT_TEMPLATE


# ---------- 跨镜状态机：previousCameraState ----------

# 初始/重置状态（场景首镜 prev_state=None 或显式传 None 走此）。
DEFAULT_PREV_STATE: Dict[str, str] = {
    "movement_direction": "none",
    "shot_size": "wide",
    "camera_position": "front",
    "subject_direction": "front",
    "template_id": "establish",
}


def _apply_continuity(
    template: Dict[str, Any],
    prev_state: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], str]:
    """根据上一镜状态做运镜措辞微调，返回 (调整后的模板, 前缀修饰词)。

    规则（V17_PLAN §7 跨镜示例）：
    1. **方向延续**：上镜 movement_direction 为 left/right（横向跟移/追逐），
       本镜也是 tracking/handheld 类 → 前缀「承接上镜X向运动，」。
    2. **对话反打**：上镜机位是 over_shoulder（看见重要物体/过肩），本镜是
       对话正反打 → 前缀「承接上镜视线，反打对切，」。
    3. **景别衔接**：上镜特写、本镜全景/中景（或反之）→ 不加前缀（正常节奏）；
       仅当上镜与本镜 movement 同为 push_in 且上镜已是 close_up → 提示避免重复推近。
    """
    if not prev_state:
        return template, ""
    prev_dir = str(prev_state.get("movement_direction", "none"))
    prev_pos = str(prev_state.get("camera_position", "front"))
    prev_size = str(prev_state.get("shot_size", "wide"))
    prev_mov = str(prev_state.get("movement", ""))

    prefix = ""

    # 1. 方向延续：本镜有横向运动且上镜同向横向运动。
    cur_mov = template["movement"]
    if cur_mov in ("tracking", "handheld", "orbit", "dolly") and prev_dir in ("left", "right"):
        dir_cn = "左" if prev_dir == "left" else "右"
        prefix = f"承接上镜{dir_cn}向运动，"

    # 2. 对话反打：上镜过肩（看见物体），本镜正反打。
    if template["id"] == "dialogue" and prev_pos == "over_shoulder":
        prefix = "承接上镜视线，反打对切，"

    # 3. 避免重复推近：上镜已在推近特写，本镜又推近 → 改为「保持景别，微推」。
    if (
        prev_mov == "push_in"
        and prev_size == "close_up"
        and template["movement"] == "push_in"
    ):
        prefix = "保持特写景别，小幅推进，"

    return template, prefix


def pick_camera(
    shot: Any,
    scene: Any,
    prev_state: Optional[Dict[str, Any]] = None,
    *,
    is_last_shot: bool = False,
    scene_has_prev: Optional[bool] = None,
) -> Tuple[str, str, Dict[str, str]]:
    """单镜运镜决策。

    返回 (camera_desc, intent, next_state)：
    - camera_desc：最终 camera 措辞（含跨镜前缀，进 Prompt 分区编辑器，人工可改）。
    - intent：粗分类（camera_intent，兼容 Phase 3 语义）。
    - next_state：本镜摄影状态，作为下镜 prev_state 传入（按播放顺序传递；
      场景首镜显式传 scene_has_prev=False 走 establish 环境建立）。

    P0 Story Timeline（2026-08-14 用户拍板）：prev_state 按**播放顺序**跨场景传递
    （交叉剪辑的画面前驱），连续性衔接保留；scene_has_prev 显式区分「本场景是否已
    出现过」——场景首镜即使有画面前驱也按无 prev 处理（establish 环境建立），
    避免把切回原场景的第一镜误判成全新场景。
    """
    has_prev = prev_state is not None if scene_has_prev is None else scene_has_prev
    template = _pick_template(shot, scene, has_prev=has_prev, is_last_shot=is_last_shot)
    adjusted, prefix = _apply_continuity(template, prev_state)

    subject = _subject_text(shot)
    obj = _props_text(shot)
    location = str(_get(scene, "location_name", "")).strip() or "场景"
    time_parts = [str(_get(scene, "time", "")).strip(), str(_get(scene, "weather", "")).strip()]
    time = "、".join(x for x in time_parts if x) or "当下"
    # P0-4：emotion 清洗后再进 camera 措辞。
    emotion = strip_internal_markers(str(_get(shot, "emotion", "")).strip()) or "微妙"

    body = adjusted["template"].format(
        location=location,
        time=time,
        subject=subject,
        emotion=emotion,
        object=obj,
    )
    desc = (prefix + body) if prefix else body

    next_state: Dict[str, str] = {
        "movement_direction": adjusted["movement_direction"],
        "shot_size": adjusted["shot_size"],
        "camera_position": adjusted["camera_position"],
        "subject_direction": "front",
        "template_id": adjusted["id"],
        "movement": adjusted["movement"],
    }
    return desc, adjusted["intent"], next_state


def build_plan_cameras(
    plan: Any,
) -> Dict[str, Any]:
    """遍历 ProductionPlan（dict 或 dataclass）按**播放顺序**生成 camera 草稿。

    P0 Story Timeline（2026-08-14 用户拍板）：prev_state 按 resolve_timeline_order
    播放顺序跨场景传递（交叉剪辑的画面前驱），不再场景切换重置；场景首镜通过
    scene_has_prev=False 触发 establish 环境建立。无 timeline 的旧项目退化为场景树
    顺序（向后兼容）。

    返回 {template_version, cameras: [{scene_id, shot_id, camera, camera_intent, camera_template}]}。
    纯规则、零显存、无网络。
    """
    from collections import Counter  # noqa: PLC0415

    from .production_plan import resolve_timeline_order, shot_lookup  # noqa: PLC0415
    order = resolve_timeline_order(plan)
    lookup = shot_lookup(plan)
    scene_total = Counter(sid for sid, _ in order)
    scene_seen: Dict[str, int] = {}
    prev_state: Optional[Dict[str, str]] = None  # 按播放顺序跨场景传递
    cameras: List[Dict[str, Any]] = []
    for scene_id, shot_id in order:
        item = lookup.get(f"{scene_id}:{shot_id}")
        if item is None:  # pragma: no cover - resolve_timeline_order 保证可定位
            continue
        scene, shot = item
        seen = scene_seen.get(scene_id, 0) + 1
        scene_seen[scene_id] = seen
        is_scene_first = seen == 1
        # 「场景尾镜」= 该场景在播放顺序上最后一次出现（拉远收束）。
        is_last_shot = seen == scene_total[scene_id]
        desc, intent, next_state = pick_camera(
            shot, scene, prev_state,
            is_last_shot=is_last_shot,
            scene_has_prev=not is_scene_first,
        )
        cameras.append(
            {
                "scene_id": scene_id,
                "shot_id": shot_id,
                "camera": desc,
                "camera_intent": intent,
                "camera_template": next_state["template_id"],
            }
        )
        prev_state = next_state
    return {"template_version": "camera-v2", "cameras": cameras}
