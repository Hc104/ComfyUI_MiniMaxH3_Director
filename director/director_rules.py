#!/usr/bin/env python3
"""V1.0 运镜规则层·第二层导演规则 + 第三层镜头功能（纯规则，零 LLM/零显存）。

用户拍板（2026-08-17《AI 漫剧导演运镜规则 v1.0》四决策）：
- 只做规则层（一镜 = 一次 H3 生成，不做自动扩镜）；
- 剧本方位词优先 → 出场顺序兜底（见 spatial_continuity.py）；
- 空间连续性块双语渲染（见 spatial_continuity.py）；
- **中性台词静止优先**：情绪爆点才推镜，该静止的近景更有力量（推翻 v2 每镜必动）。

核心心法：运镜是「情绪放大器」不是「炫技工具」；
规则不是「情绪 → 固定镜头」，而是「情绪 → 导演意图 → 镜头选择」。

三层规则：
- 第一层（硬约束）→ spatial_continuity.py（180° 轴线/左右位置/视线轴）
- 第二层（导演规则）→ 本模块 decide_camera：景别/机位/运动/构图四维决策
- 第三层（镜头功能）→ 本模块 assign_shot_role：establish/info/reaction/emotion/ending/action
  对话场景默认节奏 = 建立 → 信息 → 反应 → 情绪 → 收尾（每镜落位对应功能）

关键规则（用户原话落地）：
1. **景别跟着情绪走**：情绪镜→特写；信息镜→中景；反应镜→特写（给听众）；建立/收尾→全景。
2. **机位表达权力**：强者/反派/威严→低机位（压迫）；弱小/无助→高机位（弱小）；平等→平视。
3. **运动跟着情绪变化走**：关键台词情绪爆点才推镜；中性台词**静止近景**；
   走路/追逐/战斗→跟移/手持/甩镜（动态由动作驱动，不由对白驱动）。
4. **反应 > 说话**：观众该看谁的反应——无对白 + 上镜有对白 → reaction 特写给听者。
5. **构图守 180° 轴线**：对话守正反打；关系双人镜；过肩前景在听者对侧（配合 spatial）。

⛔ 显存铁律：纯规则、零 LLM、零 GPU、零网络。import 只引标准库 + camera_template。

核心入口：
- `assign_shot_role(shot, scene, *, is_scene_first, is_last_shot, prev_intent)` → 镜头功能
- `decide_camera(shot, scene, role, prev_state, *, is_last_shot, scene_has_prev)` →
   (camera_desc, camera_intent, next_state)，语义与 camera_template.pick_camera 对齐
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .camera_template import (  # noqa: E402 - 短语/状态机复用，避免双份漂移
    _apply_continuity,
    _pick_template,
    _props_text,
    _STRONG_EMOTIONS,
    _subject_text,
)

# ---------- 镜头功能（第三层：Shot Macro-Role） ----------

SHOT_ROLES: Tuple[str, ...] = (
    "establish", "info", "reaction", "emotion", "ending", "action",
)

# 对白爆发词（命中 → emotion 镜；关键台词情绪变化才运动）。
_DIALOGUE_BOMB: Tuple[str, ...] = (
    "还是说", "凭什么", "就这", "为什么", "竟然", "居然", "你竟然", "怎么敢",
    "难道", "你说什么", "你再说一遍", "滚", "闭嘴", "别过来", "不要",
    "我恨", "我喜欢你", "我爱你", "分手", "别走", "求求", "救命",
)

# 机位权力关系（第二层：从哪看）。
_POWER_LOW_ROLE: Tuple[str, ...] = (
    "强者", "反派", "威严", "家主", "掌门", "宗主", "皇帝", "首领", "上司",
    "大人", "修罗", "魔王", "帝王", "将军", "师尊", "长老", "尊上", "总裁", "老板",
)
_POWER_HIGH_ROLE: Tuple[str, ...] = (
    "弱小", "无助", "受害者", "伤员", "孩童", "昏迷", "被困", "落败",
)
_LOW_HINT: Tuple[str, ...] = ("压迫", "居高临下", "威严", "施压", "震慑", "强大", "霸道", "强势")
_HIGH_HINT: Tuple[str, ...] = ("弱小", "无助", "卑微", "落寞", "受伤", "跪下", "跪着", "俯身", "俯首", "哀求")

# 情绪 → 机位倾向（镜头情绪本身也能驱动机位）。
_EMO_LOW: Tuple[str, ...] = ("紧张", "愤怒", "惊恐", "震惊", "压迫")
_EMO_HIGH: Tuple[str, ...] = ("无助", "害怕", "悲伤", "崩溃", "落寞", "卑微")


# ---------- 角色专用措辞（不进 CAMERA_TEMPLATES 的 15 条，保路由契约不变） ----------

def _synthetic_template(
    tid: str, name: str, intent: str, template: str,
    shot_size: str, movement: str, camera_position: str, movement_direction: str,
) -> Dict[str, Any]:
    return {
        "id": tid, "name": name, "intent": intent, "template": template,
        "shot_size": shot_size, "movement": movement,
        "camera_position": camera_position, "movement_direction": movement_direction,
    }


def _reaction_template(shot: Any) -> Dict[str, Any]:
    """反应镜（第三层核心：观众该看谁的反应）——静止特写给听者。"""
    return _synthetic_template(
        "reaction", "反应特写", "reaction",
        "反应特写（reaction close-up），对准{subject}面部，捕捉瞬间情绪变化，画面静止克制。",
        "close_up", "static", "front", "none",
    )


def _info_dialogue_template(shot: Any) -> Dict[str, Any]:
    """信息镜（中性说事）——静止正反打（不每镜必动，推翻 v2「小幅推近」）。"""
    n = len([c for c in (shot.characters or []) if str(getattr(c, "name", "") or "").strip()])
    if n >= 2:
        phrase = "中近景正反打对切（shot-reverse-shot），{subject}轮流入画，画面沉稳克制。"
    else:
        phrase = "中近景静止机位（static medium shot），聚焦{subject}，画面沉稳，情绪内敛。"
    return _synthetic_template(
        "dialogue", "两人对话", "dialogue", phrase,
        "medium", "static", "front", "none",
    )


# ---------- 第三层：assign_shot_role（镜头功能） ----------

def _dialogue_has_bomb(shot: Any) -> bool:
    """对白行含爆发标记（！？/质问词/情绪词）→ 情绪镜（关键台词情绪变化才运动）。"""
    for d in (shot.dialogue or []):
        t = str(getattr(d, "text", "") or "")
        if any(ch in t for ch in "！？!?"):
            return True
        if any(k in t for k in _DIALOGUE_BOMB):
            return True
        if any(k in t for k in _STRONG_EMOTIONS):
            return True
    return False


def assign_shot_role(
    shot: Any,
    scene: Any,
    *,
    is_scene_first: bool = False,
    is_last_shot: bool = False,
    prev_intent: Any = None,
) -> str:
    """镜头功能判定（对话场景默认节奏 建立→信息→反应→情绪→收尾）。

    优先级：
      1. establish：场景首镜且无角色（纯环境交代）；
      2. emotion：对白含爆发标记 或 镜头强情绪；
      3. ending：场景尾镜且无对白进行中；
      4. info：有对白（中性说事）；
      5. reaction：无对白但有角色，且上镜有对白（听比说重要）；
      6. action：动作/状态镜；
      7. 兜底：establish / action。
    """
    has_cast = bool(shot.characters)
    has_dlg = bool(shot.dialogue)

    if is_scene_first and not has_cast:
        return "establish"
    if _dialogue_has_bomb(shot) or _shot_has_strong_emotion(shot):
        return "emotion"
    if is_last_shot and not has_dlg:
        return "ending"
    if has_dlg:
        return "info"
    if has_cast and _prev_has_dialogue(prev_intent):
        return "reaction"
    if has_cast:
        return "action"
    return "establish" if is_scene_first else "action"


def _prev_has_dialogue(prev_intent: Any) -> bool:
    """上镜是否有对白（观众该看谁的反应）。

    真实链路 prev_intent 是 DirectorIntent——对白原文在 `audio.dialogue`
    （见 director_intent._audio_block）；兼容测试/旧路径的 `.dialogue` 属性。
    """
    if prev_intent is None:
        return False
    if getattr(prev_intent, "dialogue", None):
        return True
    audio = getattr(prev_intent, "audio", None)
    if isinstance(audio, dict):
        return bool(audio.get("dialogue"))
    return False


def _shot_has_strong_emotion(shot: Any) -> bool:
    emo = str(getattr(shot, "emotion", "") or "").strip()
    return bool(emo and any(k in emo for k in _STRONG_EMOTIONS))


# ---------- 第二层：四维决策 + 相机渲染 ----------

def _camera_angle(shot: Any, scene: Any) -> str:
    """机位（从哪看）：权力关系 + 镜头情绪 → low / high / eye_level。"""
    roles = [str(getattr(c, "role", "") or "") for c in (shot.characters or [])]
    text = " ".join(str(x).strip() for x in (
        getattr(shot, "source_text", "") or "",
        getattr(shot, "visual_intent", "") or "",
    ) if str(x).strip())
    if any(any(k in (r or "") for k in _POWER_LOW_ROLE) for r in roles):
        return "low"
    if any(k in text for k in _LOW_HINT):
        return "low"
    if any(any(k in (r or "") for k in _POWER_HIGH_ROLE) for r in roles):
        return "high"
    if any(k in text for k in _HIGH_HINT):
        return "high"
    emo = str(getattr(shot, "emotion", "") or "").strip()
    if any(k in emo for k in _EMO_LOW):
        return "low"
    if any(k in emo for k in _EMO_HIGH):
        return "high"
    return "eye_level"


def _select_role_template(
    role: str,
    shot: Any,
    base_tid: str,
) -> Dict[str, Any]:
    """镜头功能 → 模板（复用 camera_template 15 条短语 + 角色专用措辞）。"""
    from .camera_template import _TEMPLATE_INDEX  # noqa: PLC0415 - 防顶层环

    if role == "establish":
        return _TEMPLATE_INDEX["establish"]
    if role == "emotion":
        emo = str(getattr(shot, "emotion", "") or "").strip()
        # 紧张/压迫 → 低角度逼近；否则情绪特写推镜。
        if any(k in emo for k in ("紧张", "压迫", "愤怒", "惊恐", "对峙")):
            return _TEMPLATE_INDEX["tense"]
        return _TEMPLATE_INDEX["emotion_close"]
    if role == "reaction":
        return _reaction_template(shot)
    if role == "info":
        if getattr(shot, "dialogue", None):
            return _info_dialogue_template(shot)
        # 无对白的信息/状态 → 人物登场跟拍（保持人物入画）。
        return _TEMPLATE_INDEX["enter"]
    if role == "ending":
        return _TEMPLATE_INDEX["ending"]
    # role == "action"：由关键词命中基础模板（walk/chase/combat/enter/see_object…）。
    return _TEMPLATE_INDEX.get(base_tid) or _TEMPLATE_INDEX["default"]


_ANGLE_PREFIX: Dict[str, str] = {
    "low": "低机位",
    "high": "高机位",
    "eye_level": "",
}
# 模板措辞可能已含机位（tense 的「低角度」等）→ 不重复前缀。
_ANGLE_ALREADY: Tuple[str, ...] = ("低角", "低机位", "高机位", "高角", "俯拍", "仰拍")


def decide_camera(
    shot: Any,
    scene: Any,
    role: str,
    prev_state: Optional[Dict[str, Any]] = None,
    *,
    is_last_shot: bool = False,
    scene_has_prev: Optional[bool] = None,
    tension: int = 0,
    reaction_forced: bool = False,
) -> Tuple[str, str, Dict[str, str]]:
    """单镜相机决策（四维：景别/机位/运动/构图，情绪 → 意图 → 镜头选择）。

    返回 (camera_desc, camera_intent, next_state)，语义与 camera_template.pick_camera
    对齐（director_intent.build_shot_intent 直接消费）：
    - camera_desc：完整运镜措辞（含跨镜前缀 / 机位前缀 / 强动态短语），进 H3「镜头：」；
    - camera_intent：粗分类（establish/dialogue/motion/emotion/close/ending/reaction…）；
    - next_state：跨镜状态（下镜 prev_state，含 template_id/movement/shot_size…）。

    运动决策（第二层核心）：
    - 情绪镜（爆点）→ 推镜（emotion_close/tense）；
    - 反应镜 → 静止特写（该静止的近景更有力量）；
    - 信息镜 → 静止正反打（不每镜必动）；
    - 动作镜 → 关键词动态（walk 跟移 / chase 手持 / combat 甩镜）；
    - 尾镜 → 拉远收束。

    v2.0 张力节奏（CAMERA_RULES_V2 §3.3，P1 接线，tension=0 即 v1.0 行为）：
    - reaction_forced=True（§3.4 反应镜硬规则）→ 景别锁特写 / 运动锁静止，覆盖任何 role；
    - tension>=3 峰值（peak）→ 特写定格（若已是特写+静止则原样，不重复措辞）；
    - tension==2 上升段（build）→ 静止镜改缓推（情绪升起，镜头随情绪小幅推近）。
    """
    has_prev = prev_state is not None if scene_has_prev is None else scene_has_prev
    base_tid = _pick_template(
        shot, scene, has_prev=has_prev, is_last_shot=is_last_shot,
    )["id"]
    template = _select_role_template(role, shot, base_tid)

    # ---- v2.0 P1：张力节奏 + 反应镜硬规则（纯规则，tension=0 全跳过） ----
    if reaction_forced:
        # 硬规则：景别锁特写 / 运动锁静止（reaction 措辞，观众该看谁的反应）。
        template = _reaction_template(shot)
    elif tension >= 3:
        # 峰值（3）定格：情绪在峰值处凝住——特写 + 静止（「第四镜不要动镜头」精确落地）。
        if not (template.get("shot_size") == "close_up"
                and template.get("movement") == "static"):
            template = _synthetic_template(
                "peak_hold", "峰值定格", "emotion",
                "特写定格（static close-up），{subject}的情绪在峰值处凝住，画面静止克制。",
                "close_up", "static",
                str(template.get("camera_position", "front") or "front"), "none",
            )
    elif tension == 2:
        # 上升段（2）：静止镜 → 缓推（情绪升起，镜头随情绪小幅推近；景别保留原模板）。
        if (template.get("movement") == "static"
                and role not in ("establish", "ending")):
            size = str(template.get("shot_size", "medium") or "medium")
            size_cn = "特写" if size == "close_up" else ("全景" if size == "wide" else "中景")
            template = _synthetic_template(
                "rising_push", "上升缓推", "motion",
                f"{size_cn}缓推（slow push-in），{{subject}}的情绪正悄然升起，镜头缓慢推近。",
                size, "push_in",
                str(template.get("camera_position", "front") or "front"), "forward",
            )

    adjusted, prefix = _apply_continuity(template, prev_state)

    subject = _subject_text(shot)
    obj = _props_text(shot)
    location = str(getattr(scene, "location_name", "") or "").strip() or "场景"
    time_parts = [str(getattr(scene, "time", "") or "").strip(),
                  str(getattr(scene, "weather", "") or "").strip()]
    time = "、".join(x for x in time_parts if x) or "当下"
    emotion = str(getattr(shot, "emotion", "") or "").strip() or "微妙"

    body = adjusted["template"].format(
        location=location, time=time, subject=subject,
        emotion=emotion, object=obj,
    )
    # 机位前缀（低/高机位；措辞已含机位则不重复）。
    angle = _camera_angle(shot, scene)
    angle_cn = _ANGLE_PREFIX.get(angle, "")
    if angle_cn and not any(k in body for k in _ANGLE_ALREADY):
        body = angle_cn + body
    desc = (prefix + body) if prefix else body

    next_state: Dict[str, str] = {
        "movement_direction": str(adjusted.get("movement_direction", "none")),
        "shot_size": str(adjusted.get("shot_size", "medium")),
        "camera_position": str(adjusted.get("camera_position", "front")),
        "subject_direction": "front",
        "template_id": str(adjusted.get("id", "")),
        "movement": str(adjusted.get("movement", "static")),
        "shot_role": role,
        "camera_angle": angle,
    }
    return desc, str(adjusted.get("intent", "")), next_state
