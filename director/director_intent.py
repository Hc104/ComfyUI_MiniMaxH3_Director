#!/usr/bin/env python3
"""DirectorIntent 构建（P0-①，用户 2026-08-13 拍板）。

统一中间数据结构：ProductionPlan（规则 + Qwen 已补全字段）→ DirectorIntent。

设计铁律（用户拍板）：
1. ``user_original_intent`` = source_text 逐字保底（原文事实最高优先级，任何阶段不得覆盖）。
2. ``retained_facts`` 用规则从原文抽导演事实（按标点拆句 + 去镜头前缀），不依赖 AI 老实。
3. Qwen 只填「原文没有、导演需要」的推断字段（emotion/composition）；
   原文有的字段 Qwen 只分类不改写（actions/lighting/environment 内容是原文事实）。
4. 每个字段带 provenance 来源标注（USER/RULE/AI/MIX），供 UI 三层可追溯。
5. camera_position/movement 从运镜模板结构化字段（shot_size/movement）映射中文，
   与 prompt_builder_v17 同一 camera_template 状态机（场景内连贯 + 场景切换重置）。
6. ⛔ 本模块纯规则 + 已结构化数据投影，零 LLM 调用（Qwen 结果在 script_analyzer 已落 plan）。

用法：
    intents = build_plan_intents(plan)          # {scene_id:shot_id → DirectorIntent}
    intent = build_shot_intent(shot, scene, ...)  # 单镜
"""

from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Optional

from .production_plan import DirectorIntent, EntityType, IntentSource
from .shot_plan import build_shot_plan  # 生成约束层 v1.0（P1 接线）

# 运镜模板结构化字段 → 中文景别/运镜（与 camera_template.CAMERA_TEMPLATES 的
# shot_size/movement 枚举对齐；新增模板 id 未映射时回退空串，不臆造）。
_SHOT_SIZE_CN: Dict[str, str] = {
    "wide": "全景",
    "medium": "中景",
    "close_up": "近景",
    "extreme_close_up": "特写",
}
_MOVEMENT_CN: Dict[str, str] = {
    "pan": "缓摇",
    "push_in": "缓推",
    "tracking": "跟移",
    "handheld": "手持跟拍",
    "dolly": "横移",
    "pull_out": "拉远",
    "orbit": "环绕",
    "boom": "升降",
    "static": "固定机位",
}

# 光线/氛围关键词（lighting 字段：原文含这些词 → 原文事实；否则留空让 AI 补）
_LIGHTING_HINTS: tuple[str, ...] = (
    "光", "灯", "烛", "暖", "冷", "明月", "月色", "月光", "夕阳", "黄昏", "晨光",
    "反光", "映", "影", "暗", "亮", "夜",
)

# 镜头标点切分：保留原文事实（逐字），按停顿切短句
_FACT_SPLIT_RE = re.compile(r"[，。；、！？…\n]+")

# 引号内容（对白/引用）切分前先保护：内部标点不得当停顿切分点，
# 否则「剑，交出来。」会被切碎、join 后引号不闭合（P0-① 可读性修复）。
_QUOTED_RE = re.compile(r'("[^"]*"|「[^」]*」|『[^』]*』|《[^》]*》)')


def _strip_shot_marker(text: str) -> str:
    """去「镜头 1：」式前缀（轻微结构整理，内容逐字保留）。"""
    return re.sub(r"^\s*镜头\s*[一二三四五六七八九十百千\d]+\s*[：:]\s*", "", text).strip()


def extract_retained_facts(source_text: str) -> List[str]:
    """从 source_text 抽原文导演事实（逐字保序，去镜头前缀/空段/纯标点）。

    规则层确定性抽取，不依赖 AI：把原文按停顿标点拆成事实片段，
    每个片段 >= 2 字才保留（防「雨，」「的」类碎片）。片段本身逐字不改写。

    P0-① 对白保护：引号内容（「…」等）整体保留，内部标点不拆，对白
    片段以闭合引号结尾（join 后引号完整闭合，H3 描述可读）。
    """
    text = _strip_shot_marker(source_text or "")
    if not text:
        return []
    placeholders: List[str] = []

    def _hold(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00{len(placeholders) - 1}\x00"

    held = _QUOTED_RE.sub(_hold, text)
    facts: List[str] = []
    for part in _FACT_SPLIT_RE.split(held):
        p = part.strip()
        if not p:
            continue
        for i, quoted in enumerate(placeholders):
            p = p.replace(f"\x00{i}\x00", quoted)
        if len(p.strip("「」『』\"“”")) >= 2:
            facts.append(p)
    return facts


def _main_entity_name(shot: Any) -> str:
    """无角色镜的主体：优先建筑/道具/生物主实体（按置信度），否则场景名。"""
    best: str = ""
    best_conf = -1.0
    for e in getattr(shot, "entities", []) or []:
        if e.type in (EntityType.ARCHITECTURE, EntityType.PROP, EntityType.CREATURE,
                      EntityType.VEHICLE):
            conf = float(getattr(e, "confidence", 0.0) or 0.0)
            if conf > best_conf:
                best_conf = conf
                best = e.name or ""
    return best


def _camera_cn(template_id: str) -> tuple[str, str]:
    """模板 id → (中文景别, 中文运镜)。模板未收录 → ("", "")。"""
    try:
        from .camera_template import _TEMPLATE_INDEX  # noqa: PLC0415 - 延迟导入避免环
    except Exception:  # pragma: no cover - 模板库加载失败回退空
        return "", ""
    t = _TEMPLATE_INDEX.get(template_id or "")
    if not t:
        return "", ""
    return (
        _SHOT_SIZE_CN.get(t.get("shot_size", ""), ""),
        _MOVEMENT_CN.get(t.get("movement", ""), ""),
    )


def _lighting_from(shot: Any, facts: List[str]) -> str:
    """光线字段：原文片段含光线词 → 原文事实（逐字）；否则空（AI 补）。"""
    for f in facts:
        if any(k in f for k in _LIGHTING_HINTS):
            return f
    return shot.emotion or ""


def _audio_block(shot: Any, scene: Any) -> Dict[str, Any]:
    """规则音频：天气→环境音；对白→对白原文；情绪强词→音乐类型。纯规则。"""
    weather = str(getattr(scene, "weather", "") or "").strip()
    ambient = ""
    if weather:
        ambient = {
            "雨": "雨声淅沥",
            "雪": "风雪声",
            "风": "风声",
            "晴": "清淡环境音",
            "阴": "低沉环境音",
        }.get(weather[:1], "环境音")
    dialogues = [
        {"speaker": d.speaker, "text": d.text} for d in (shot.dialogue or [])
        if (d.text or "").strip()
    ]
    emotion = str(getattr(shot, "emotion", "") or "").strip()
    music = "舒缓氛围配乐" if not emotion else (
        "紧张氛围配乐渐起" if any(k in emotion for k in ("紧张", "愤怒", "惊恐", "害怕",
                                                          "悲伤", "震惊", "凝重", "焦躁"))
        else "舒缓氛围配乐"
    )
    return {
        "ambient": ambient,
        "dialogue": dialogues,
        "music": music,
        "provenance": {
            "ambient": IntentSource.RULE,
            "dialogue": IntentSource.USER,
            "music": IntentSource.RULE,
        },
    }


# ---------- P0-③ Shot-to-Shot 连贯性（纯规则推导，零 LLM） ----------
#
# H3_PIPELINE_AUDIT §五 P0-③ 三要素（用户 2026-08-13 拍板）：
#   1. Previous Shot State 结构化：只投影**身份类**字段（主体/角色/地点/光线/情绪/
#      运镜模板），动作/表情由本镜自主推进（may_change 语义），绝不带上一镜
#      source_text 逐字 —— 遵守「不将原始文本强制注入 H3 Prompt」。
#   2. 镜头关系 transition_type：规则推导（对切/反打/景别节奏/反应/延续）。
#   3. 必须保持 vs 允许变化分离：must_keep 注入 H3 保护一致性（角色外观/场景/
#      光线），may_change 为设计语义只结构化记录、不注入。

# may_change 设计语义（不注入 H3，仅结构化记录）。
_MAY_CHANGE_ITEMS: tuple[str, ...] = (
    "镜头景别/机位/运镜变化",
    "动作推进",
    "情绪与表情变化",
)


def _prev_shot_state(prev: "DirectorIntent") -> Dict[str, Any]:
    """上一镜结构化状态（身份类字段投影，动作/表情不投影）。

    返回 dict 而不是原文片段：subject/characters/location/lighting/emotion/
    camera_template 都是已结构化的导演字段，不含 source_text 逐字。
    """
    return {
        "subject": str(prev.subject or ""),
        "characters": [c for c in (prev.characters or []) if c][:3],
        "location": str(prev.location or ""),
        "lighting": str(prev.lighting or ""),
        "emotion": str(prev.emotion or ""),
        "camera_template": str((prev.continuity or {}).get("camera_template", "") or ""),
    }


def _transition_type(
    prev: Optional["DirectorIntent"],
    *,
    cur_template_id: str,
    cur_location: str,
    cur_subject: str,
    cur_characters: List[str],
    cur_actions: Optional[List[str]] = None,
) -> str:
    """镜头关系推导（纯规则，零 LLM）。空串 = timeline 首镜（无画面前驱）。

    规则（与 camera_template._apply_continuity 的衔接语义对齐，只补镜头关系维度；
    reverse/cut 的措辞已由 camera_desc 前缀覆盖，不重复渲染）：
      - 无上一镜（timeline 首镜）                   → ""
      - 上镜对白 + 本镜对白（正反打对切）            → "cut"
      - 上镜过肩 + 本镜对白（反打）                → "reverse"
      - 上镜特写 + 本镜全景（景别拉开）            → "close_to_wide"
      - 上镜全景 + 本镜特写（视线收紧）            → "wide_to_close"
      - 上镜特写 + 本镜特写（同景别反应衔接）      → "reaction"
      - 同地点且同主体/共享角色，且动作重叠        → "match_action"
      - 同地点且同主体/共享角色（叙事延续）        → "continuation"
      - 换地点（跨场景切换）                       → "scene_cut"
        （P0 Story Timeline：交叉剪辑切回本场景时由 build_shot_intent
          依据 scene_prev 存在性升级为 "cross_cut" 平行剪辑）
      - 默认普通切换                              → "cut"
    """
    if prev is None:
        return ""
    try:
        from .camera_template import _TEMPLATE_INDEX  # noqa: PLC0415 - 延迟导入避免环
    except Exception:  # pragma: no cover - 模板库加载失败按同地点延续回退
        if prev.location and prev.location == cur_location:
            return "continuation"
        return "scene_cut"
    prev_cont = prev.continuity or {}
    prev_tmpl = _TEMPLATE_INDEX.get(str(prev_cont.get("camera_template", "") or ""))
    cur_tmpl = _TEMPLATE_INDEX.get(str(cur_template_id or ""))
    if not prev_tmpl or not cur_tmpl:
        if prev.location and prev.location == cur_location:
            return "continuation"
        return "scene_cut"
    prev_id = str(prev_tmpl.get("id", ""))
    cur_id = str(cur_tmpl.get("id", ""))
    prev_size = str(prev_tmpl.get("shot_size", ""))
    cur_size = str(cur_tmpl.get("shot_size", ""))
    prev_pos = str(prev_tmpl.get("camera_position", ""))
    if prev_id == "dialogue" and cur_id == "dialogue":
        return "cut"
    if prev_pos == "over_shoulder" and cur_id == "dialogue":
        return "reverse"
    if prev_size == "close_up" and cur_size == "wide":
        return "close_to_wide"
    if prev_size == "wide" and cur_size == "close_up":
        return "wide_to_close"
    if prev_size == "close_up" and cur_size == "close_up":
        return "reaction"
    same_people = bool(set(prev.characters or []) & set(cur_characters or []))
    if prev.location == cur_location and (prev.subject == cur_subject or same_people):
        prev_actions = set(a.strip() for a in (prev.action or []) if str(a).strip())
        cur_actions = set(a.strip() for a in (cur_actions or []) if str(a).strip())
        if prev_actions and cur_actions and prev_actions & cur_actions:
            return "match_action"
        return "continuation"
    if prev.location and prev.location != cur_location:
        return "scene_cut"
    return "cut"


def _must_keep_items(
    prev: Optional["DirectorIntent"],
    *,
    cur_characters: List[str],
    cur_location: str,
    cur_lighting: str,
    scene_prev: Optional["DirectorIntent"] = None,
) -> List[str]:
    """「必须保持」清单（中文短语，供 H3 渲染直接拼接）。

    只保持**身份类**一致性：共享角色外观一致、相同地点场景不变、相同光线延续。
    动作/镜头/情绪是设计语义 → 进 may_change，不在此列。

    P0 Story Timeline 双来源（用户拍板 2026-08-14，硬性要求）：
      - 角色外观一致：合并 **timeline 画面前驱**（播放相邻镜里共享角色）
        与 **本场景上次状态**（切回原场景时保持该场景在场角色外观），去重保序；
      - 场景不变 / 光线延续：优先 **本场景上次状态**（scene_prev），
        无 scene_prev（本镜是该场景首次出现）时回退 timeline 前驱。
    """
    if prev is None and scene_prev is None:
        return []
    keep: List[str] = []
    # 角色外观一致：双来源合并，去重保序。
    seen_names: List[str] = []
    for src in (prev, scene_prev):
        if src is None:
            continue
        shared = [
            c for c in (src.characters or [])
            if c in cur_characters and c not in seen_names
        ]
        if shared:
            seen_names.extend(shared)
    if seen_names:
        keep.append("、".join(seen_names) + "外观一致")
    # 场景/光线：优先本场景上次状态；无则回退画面前驱。
    sp = scene_prev or prev
    if sp is not None:
        if sp.location and sp.location == cur_location:
            keep.append(f"{sp.location}场景不变")
        if sp.lighting and sp.lighting == cur_lighting:
            keep.append("光线氛围延续")
    return keep


def build_shot_intent(
    shot: Any,
    scene: Any,
    *,
    camera_desc: str = "",
    camera_intent: str = "",
    camera_template_id: str = "",
    has_prev: bool = False,
    is_last_shot: bool = False,
    char_anchors: Optional[Dict[str, str]] = None,
    prev_intent: Optional["DirectorIntent"] = None,
    scene_prev_intent: Optional["DirectorIntent"] = None,
    overrides: Optional[Dict[str, str]] = None,
    shot_role: str = "",
    spatial: Optional[Dict[str, Any]] = None,
    # ---- v2.0 内容规则派生字段（CAMERA_RULES_V2 §4，P1 接线；默认空/0 即不注入）----
    core_action: str = "",
    expression: str = "",
    tension: int = 0,
    rhythm: str = "",
    reaction_forced: bool = False,
    # synthetic 模板兜底：peak_hold/rising_push/reaction 不在 _TEMPLATE_INDEX，
    # 由 build_plan_intents 传入 next_state 的结构化 shot_size/movement 直接映射中文。
    camera_shot_size: str = "",
    camera_movement: str = "",
    # 生成约束层 v1.0 P2（PROMPT_COMPILER_V1 §4-P2）：角色外观档案 {角色名: 外观}。
    # 传给 build_shot_plan → CharacterLock.appearance → 约束块「外观锁定」。
    appearance_map: Optional[Dict[str, str]] = None,
) -> DirectorIntent:
    """单镜 → DirectorIntent（规则 + 已结构化 Qwen 产物投影，零 LLM）。

    各字段来源：
      USER  = user_original_intent / retained_facts（source_text 逐字）
      RULE  = location / camera_position / movement / continuity / audio.ambient+music
      MIX   = subject / characters / action / lighting / environment / entities
              （规则定结构，内容为原文事实，AI 只分类/补全）
      AI    = emotion / composition（原文没有、导演需要）

    char_anchors 非 None 时对 entities 做**边界过滤**（复用 entity_cleanse.entity_in_shot_scope
    同一实现）：Qwen 跨镜越界实体（名字/别名不在本镜 source_text 或规则锚点）直接丢弃，
    保证 DirectorIntent.entities 与资产匹配链路收到的实体一致（用户拍板 ②）。

    prev_intent / scene_prev_intent（P0 Story Timeline 双前驱，用户拍板 2026-08-14）：
      - prev_intent      = **timeline 画面前驱**（播放顺序上一镜，无论属哪个场景）
      - scene_prev_intent= **本场景上次状态**（该场景在 timeline 上此前最后一次出现）
      continuity 由两个来源合成：prev_shot_state 投影画面前驱（向后兼容）、
      scene_prev_state 投影场景空间状态、must_keep 双来源合并、transition_type
      场景切换细分（scene_cut 切入新场景 / cross_cut 平行剪辑切回）。

    overrides（五区编辑 → 用户最终意图覆盖层，P0-A #477）：
      visual     → composition（替换 AI 构图建议；原文 retained_facts 仍最高优先级）
      cameraText → continuity.camera_desc（替换规则相机措辞）
      style      → style 字段（追加「风格：」描述）
      soundText  → audio.ambient（替换规则环境音；对白原文逐字保留）
    覆盖标记 provenance=USER（供三层可追溯「用户最终意图」层）。
    """
    source = str(getattr(shot, "source_text", "") or "").strip()
    facts = extract_retained_facts(source)

    chars = [str(c.name).strip() for c in (shot.characters or []) if str(c.name).strip()]
    entities: List[Dict[str, Any]] = []
    for e in (shot.entities or []):
        if not e.name:
            continue
        if char_anchors is not None:
            from .entity_cleanse import entity_in_shot_scope  # noqa: PLC0415
            if not entity_in_shot_scope(e, source, char_anchors):
                continue
        entities.append(
            {
                "name": e.name,
                "type": e.type,
                "confidence": round(float(e.confidence or 0.0), 3),
                "source": e.source,
            }
        )
    subject = "、".join(chars) or _main_entity_name(shot) or \
        str(getattr(scene, "location_name", "") or "").strip() or "场景"

    actions = [str(a).strip() for a in (shot.actions or []) if str(a).strip()]
    environment = [
        str(v.name).strip() for v in (shot.visual_elements or [])
        if str(v.name).strip()
    ]
    cam_pos, move = _camera_cn(camera_template_id)
    if not cam_pos and camera_shot_size:
        # synthetic 模板（peak_hold/rising_push/reaction）不在 _TEMPLATE_INDEX →
        # 用 next_state 结构化 shot_size 枚举直接映射中文景别。
        cam_pos = _SHOT_SIZE_CN.get(camera_shot_size, "")
    if not move and camera_movement:
        move = _MOVEMENT_CN.get(camera_movement, "")
    if not move and camera_desc:
        # 无模板映射（手编/旧路径）→ 用运镜决策措辞整体作 movement 兜底
        move = str(camera_desc).strip()

    lighting = _lighting_from(shot, facts) if facts else ""

    # P0 Story Timeline 双前驱连续性（有画面前驱时才注入）。
    # prev_intent = timeline 画面前驱（播放相邻），scene_prev_intent = 本场景上次状态。
    # transition_type/prev_shot_state/must_keep/may_change 都是**纯规则**推导，
    # 零 LLM；may_change 为设计语义只结构化记录、不注入 H3。
    if prev_intent is not None:
        cur_location = str(getattr(scene, "location_name", "") or "").strip()
        prev_location = str(prev_intent.location or "").strip()
        scene_switch = bool(prev_location and cur_location and prev_location != cur_location)
        # P0 Story Timeline：场景切换细分——切回本场景（有 scene 前驱）→ 平行剪辑；
        # 切入全新场景 → scene_cut。同场景沿用线性镜头关系推导。
        if scene_switch:
            transition = "cross_cut" if scene_prev_intent is not None else "scene_cut"
        else:
            transition = _transition_type(
                prev_intent,
                cur_template_id=camera_template_id,
                cur_location=cur_location,
                cur_subject=subject,
                cur_characters=chars,
                cur_actions=actions,
            )
        continuity: Dict[str, Any] = {
            "has_prev": has_prev,
            "is_last_shot": is_last_shot,
            "camera_intent": camera_intent or "",
            "camera_template": camera_template_id or "",
            # P0-①：pick_camera 完整运镜措辞（含跨镜前缀/正反打/景别），
            # H3 与五区草稿 camera 渲染优先用它，结构化字段语义不丢。
            "camera_desc": camera_desc or "",
            "transition_type": transition,
            "prev_shot_state": _prev_shot_state(prev_intent),
            "timeline_prev_shot_id": (
                f"{prev_intent.scene_id}:{prev_intent.shot_id}"
                if prev_intent.scene_id else prev_intent.shot_id
            ),
            "must_keep": _must_keep_items(
                prev_intent,
                cur_characters=chars,
                cur_location=cur_location,
                cur_lighting=lighting,
                scene_prev=scene_prev_intent,
            ),
            "may_change": list(_MAY_CHANGE_ITEMS),
        }
        # V1.0 运镜规则层（#601 用户拍板）：镜头功能 + 空间连续性（180° 轴线）。
        if shot_role:
            continuity["shot_role"] = shot_role
        if spatial:
            continuity["spatial"] = spatial
        if scene_prev_intent is not None:
            # 本场景上次空间状态（切回原场景时延续该场景的灯光/布局/在场角色）。
            continuity["scene_prev_state"] = _prev_shot_state(scene_prev_intent)
            continuity["scene_prev_shot_id"] = (
                f"{scene_prev_intent.scene_id}:{scene_prev_intent.shot_id}"
                if scene_prev_intent.scene_id else scene_prev_intent.shot_id
            )
    else:
        continuity = {
            "has_prev": has_prev,
            "is_last_shot": is_last_shot,
            "camera_intent": camera_intent or "",
            "camera_template": camera_template_id or "",
            # P0-①：pick_camera 完整运镜措辞（含跨镜前缀/正反打/景别），
            # H3 与五区草稿 camera 渲染优先用它，结构化字段语义不丢。
            "camera_desc": camera_desc or "",
        }
        # V1.0 运镜规则层（#601 用户拍板）：镜头功能 + 空间连续性（180° 轴线）。
        if shot_role:
            continuity["shot_role"] = shot_role
        if spatial:
            continuity["spatial"] = spatial

    # v2.0 反应镜硬规则（§3.4）：仅 forced 时写键，保证旧链路 continuity 不膨胀。
    if reaction_forced:
        continuity["reaction_forced"] = True

    provenance: Dict[str, str] = {
        "user_original_intent": IntentSource.USER,
        "retained_facts": IntentSource.USER,
        "subject": IntentSource.MIX,
        "characters": IntentSource.MIX,
        "location": IntentSource.RULE,
        "action": IntentSource.MIX,
        "emotion": IntentSource.AI,
        "composition": IntentSource.AI,
        "camera_position": IntentSource.RULE,
        "movement": IntentSource.RULE,
        "lighting": IntentSource.MIX,
        "environment": IntentSource.MIX,
        # v2.0 内容规则派生字段 = RULE 来源（纯规则，非 AI 推断）。
        "core_action": IntentSource.RULE,
        "expression": IntentSource.RULE,
        "tension": IntentSource.RULE,
        "rhythm": IntentSource.RULE,
        "continuity": IntentSource.RULE,
        "audio": IntentSource.RULE,
        "entities": IntentSource.MIX,
    }
    audio = _audio_block(shot, scene)
    if dialogue := audio.get("dialogue"):
        # 对白原文逐字 = USER 来源
        provenance["audio"] = IntentSource.MIX

    intent = DirectorIntent(
        shot_id=str(getattr(shot, "shot_id", "") or ""),
        scene_id=str(getattr(scene, "scene_id", "") or ""),
        user_original_intent=source,
        retained_facts=facts,
        subject=subject,
        characters=chars,
        location=str(getattr(scene, "location_name", "") or "").strip(),
        action=actions,
        emotion=str(getattr(shot, "emotion", "") or "").strip(),
        composition=str(getattr(shot, "visual_intent", "") or "").strip(),
        camera_position=cam_pos,
        movement=move,
        lighting=lighting,
        environment=environment,
        # v2.0 内容规则派生字段（P1 接线；未激活时为默认空/0，旧链路不变）。
        core_action=core_action,
        expression=expression,
        tension=tension,
        rhythm=rhythm,
        continuity=continuity,
        audio=audio,
        entities=entities,
        provenance=provenance,
    )
    if overrides:
        _apply_user_overrides(intent, overrides)

    # 生成约束层 v1.0（PROMPT_COMPILER_V1 §3.1）：ShotPlan 纯规则派生。
    # 放在用户覆盖之后构建 → 用户最终意图（composition/camera 覆盖）进入约束；
    # provenance=RULE（纯规则，非 AI）；原文事实铁律不变。
    # P2：appearance_map（Qwen 角色外观档案）→ CharacterLock.appearance → 外观锁定。
    intent.constraint = build_shot_plan(intent, appearance_map=appearance_map).to_dict()
    intent.provenance["constraint"] = IntentSource.RULE
    return intent


def _apply_user_overrides(intent: DirectorIntent, overrides: Dict[str, str]) -> None:
    """五区编辑 → DirectorIntent 覆盖（用户最终意图层，P0-A #477）。

    ⛔ 铁律：只覆盖 AI/规则生成的字段（composition/camera/audio/style），
    **绝不触碰原文事实**（user_original_intent/retained_facts/action/dialogue），
    保证「最终 H3 Prompt 可追溯回原始小说」。

    映射：
      visual     → composition（替换 AI 构图建议）
      cameraText → continuity.camera_desc（替换规则相机措辞，含首镜）
      style      → intent.style（_build_description 追加「风格：」）
      soundText  → audio.ambient（替换规则环境音；对白 dialogue 逐字保留）
    """
    visual = str(overrides.get("visual", "") or "").strip()
    if visual:
        intent.composition = visual
        intent.provenance["composition"] = IntentSource.USER
    cam = str(overrides.get("cameraText", "") or "").strip()
    if cam:
        continuity = dict(intent.continuity)
        continuity["camera_desc"] = cam
        intent.continuity = continuity
        intent.provenance["camera_desc"] = IntentSource.USER
    style = str(overrides.get("style", "") or "").strip()
    if style:
        intent.style = style
        intent.provenance["style"] = IntentSource.USER
    sound = str(overrides.get("soundText", "") or "").strip()
    if sound:
        audio = dict(intent.audio)
        audio["ambient"] = sound
        intent.audio = audio
        intent.provenance["audio"] = IntentSource.USER


def build_plan_intents(
    plan: Any,
    *,
    use_camera_planner: bool = True,
    overrides_by_shot: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, DirectorIntent]:
    """遍历 ProductionPlan（dict 或 dataclass）按**播放顺序**为每镜构建 DirectorIntent。

    P0 Story Timeline（2026-08-14 用户拍板）：遍历顺序 = resolve_timeline_order(plan)
    （优先 plan.timeline 交叉剪辑顺序；无 timeline 退化为场景树顺序，向后兼容）。
    - prev_intent       = timeline **画面前驱**（上一播放镜，跨场景不断链）
    - scene_last[场景]  = 本场景**上次状态**（切回原场景时延续空间状态）
    两者合成为 build_shot_intent 的双前驱 continuity（用户硬性要求，非后补）。

    相机决策与 prompt_builder_v17 同源（camera_template.pick_camera 状态机按播放顺序
    传递 prev_state），保证「Prompt Sections 草稿」与「DirectorIntent」相机一致。

    返回 {f"{scene_id}:{shot_id}": DirectorIntent}。
    纯规则、零显存、无网络。

    entities 边界过滤：与 build_entity_registry（资产匹配唯一入口）同一套
    entity_in_shot_scope 检查 + 同一 _character_anchor_map 锚点表，Qwen 跨镜越界
    实体在 DirectorIntent 层同步丢弃（用户拍板 ②），保证两边实体集合一致。

    overrides_by_shot（P0-A #477）：{scene_id:shot_id: {visual/cameraText/style/soundText}}，
    五区编辑覆盖层，逐镜透传 build_shot_intent(overrides=…)；仅覆盖 AI/规则字段，
    原文事实（retained_facts/对白）绝不被覆盖。
    """
    from collections import Counter  # noqa: PLC0415

    from .production_plan import resolve_timeline_order, shot_lookup  # noqa: PLC0415
    char_anchors: Optional[Dict[str, str]] = None
    try:
        from .entity_cleanse import _character_anchor_map  # noqa: PLC0415
        char_anchors = _character_anchor_map(plan)
    except Exception:  # pragma: no cover - 锚点表失败则跳过过滤（保底不误杀）
        char_anchors = None
    # P2：跨镜角色外观档案 {canonical_name: appearance}（PROMPT_COMPILER_V1 §4-P2）。
    # 来源=ProductionPlan.character_profiles（Qwen 导入产出/规则兜底）；dict 与
    # dataclass 都支持；解析失败退化为空（旧链路不变）。键 canonical=trim 原名。
    appearance_map: Dict[str, str] = {}
    try:
        raw_cp = getattr(plan, "character_profiles", None)
        if raw_cp is None and isinstance(plan, dict):
            raw_cp = plan.get("character_profiles") or {}
        if isinstance(raw_cp, dict):
            appearance_map = {
                str(k).strip(): str(v).strip()
                for k, v in raw_cp.items()
                if str(k).strip() and str(v).strip()
            }
    except Exception:  # pragma: no cover - 档案解析失败则跳过（保底不误杀）
        appearance_map = {}
    out: Dict[str, DirectorIntent] = {}
    # v2.0 内容规则（CAMERA_RULES_V2 §3.3/§3.4，P1 接线）：
    # 全链情绪曲线 tension/rhythm + 反应镜硬规则集合。纯规则，失败退化为空（旧行为不变）。
    rhythm_map: Dict[str, Any] = {}
    forced_reactions: set = set()
    try:
        from .beat_rhythm import plan_rhythm, plan_reaction_shots  # noqa: PLC0415
        rhythm_map = plan_rhythm(plan)
        forced_reactions = plan_reaction_shots(plan)
    except Exception:  # pragma: no cover - 规则层失败退化为不注入（向后兼容）
        rhythm_map = {}
        forced_reactions = set()
    order = resolve_timeline_order(plan)
    lookup = shot_lookup(plan)
    # 每场景在播放顺序上出现的次数 → 「场景尾镜」判定（该场景最后一次出现收束）。
    scene_total = Counter(sid for sid, _ in order)
    scene_seen: Dict[str, int] = {}
    scene_last: Dict[str, DirectorIntent] = {}  # 本场景上次出现的 intent（Scene Last State）
    scene_spatial: Dict[str, Any] = {}  # V1.0 空间连续性（按场景维护，交叉剪辑切回恢复）
    prev_state = None
    prev_intent: Optional[DirectorIntent] = None  # timeline 画面前驱（跨场景不断链）
    for scene_id, shot_id in order:
        item = lookup.get(f"{scene_id}:{shot_id}")
        if item is None:  # pragma: no cover - resolve_timeline_order 保证可定位
            continue
        scene, shot = item
        seen = scene_seen.get(scene_id, 0) + 1
        scene_seen[scene_id] = seen
        is_scene_first = seen == 1
        is_last_shot = seen == scene_total[scene_id]
        has_prev = prev_intent is not None
        key = f"{scene_id}:{shot_id}"
        tension, rhythm = rhythm_map.get(key, (0, "")) if key in rhythm_map else (0, "")
        reaction_forced = key in forced_reactions
        core_action = ""
        expression = ""
        cam_desc = ""
        cam_intent = ""
        tmpl_id = ""
        role = ""
        next_state: Optional[Dict[str, str]] = None  # 未规划/双回退失败 → None（字段兜底空）
        if use_camera_planner:
            try:
                # V1.0 规则层（#601 用户拍板）：镜头功能 + 四维决策，情绪 → 意图 → 镜头选择。
                from .director_rules import assign_shot_role, decide_camera  # noqa: PLC0415
                role = assign_shot_role(
                    shot, scene,
                    is_scene_first=is_scene_first,
                    is_last_shot=is_last_shot,
                    prev_intent=prev_intent,
                )
                # v2.0 反应镜硬规则（§3.4）：强制 reaction 镜，不给台词只给表情变化。
                if reaction_forced:
                    role = "reaction"
                # v2.0 内容规则（§3.1/§3.2）：一镜一核心动作 + 心理可视化（纯规则零 LLM）。
                try:
                    from .core_action import extract_core_action  # noqa: PLC0415
                    core_action, _subj = extract_core_action(shot, role=role)
                    core_action = core_action or ""
                    from .psych_visualize import psych_process_action, visualize_psych  # noqa: PLC0415
                    expression = visualize_psych(shot) or ""
                    if not core_action:
                        # 静态/无强动作镜 → 用动作过程措辞补位（processify）。
                        core_action = psych_process_action(shot) or ""
                except Exception:  # pragma: no cover - 内容规则失败退化为不注入（向后兼容）
                    core_action = ""
                    expression = ""
                cam_desc, cam_intent, next_state = decide_camera(
                    shot, scene, role, prev_state,
                    is_last_shot=is_last_shot,
                    scene_has_prev=not is_scene_first,
                    tension=tension,
                    reaction_forced=reaction_forced,
                )
                tmpl_id = next_state.get("template_id", "")
                prev_state = next_state
            except Exception:  # pragma: no cover - 规则层失败回退 v2 模板状态机
                try:
                    from .camera_template import pick_camera  # noqa: PLC0415
                    cam_desc, cam_intent, next_state = pick_camera(
                        shot, scene, prev_state,
                        is_last_shot=is_last_shot,
                        scene_has_prev=not is_scene_first,
                    )
                    tmpl_id = next_state.get("template_id", "")
                    prev_state = next_state
                except Exception:  # pragma: no cover - 双回退仍失败 → 无相机
                    cam_desc = cam_intent = tmpl_id = ""
        # V1.0 空间连续性：场景首镜建立（剧本方位词/出场顺序），后续镜补位、绝不翻转。
        spatial: Optional[Dict[str, Any]] = None
        try:
            from .spatial_continuity import SpatialState  # noqa: PLC0415
            sp = scene_spatial.get(scene_id)
            if sp is None:
                sp = SpatialState.from_shot(shot)
                scene_spatial[scene_id] = sp
            else:
                sp.update(shot)
            spatial = sp.to_dict()
        except Exception:  # pragma: no cover - 空间状态异常不阻断生成
            spatial = None
        intent = build_shot_intent(
            shot, scene,
            camera_desc=cam_desc,
            camera_intent=cam_intent,
            camera_template_id=tmpl_id,
            has_prev=has_prev,
            is_last_shot=is_last_shot,
            char_anchors=char_anchors,
            prev_intent=prev_intent,
            scene_prev_intent=scene_last.get(scene_id),
            overrides=(overrides_by_shot or {}).get(f"{scene_id}:{shot_id}"),
            shot_role=role,
            spatial=spatial,
            core_action=core_action,
            expression=expression,
            tension=tension,
            rhythm=rhythm,
            reaction_forced=reaction_forced,
            # synthetic 模板兜底：peak_hold/rising_push/reaction 不在 _TEMPLATE_INDEX，
            # 用 next_state 结构化枚举映射中文景别/运镜（tension=0 旧模板也幂等）。
            camera_shot_size=str(next_state.get("shot_size", "")) if next_state else "",
            camera_movement=str(next_state.get("movement", "")) if next_state else "",
            # P2：跨镜角色外观档案 → CharacterLock.appearance → 约束块「外观锁定」。
            appearance_map=appearance_map or None,
        )
        out[f"{scene_id}:{shot_id}"] = intent
        prev_intent = intent          # 下镜的「画面前驱」
        scene_last[scene_id] = intent  # 本场景下次出现时的「空间状态前驱」
    return out


__all__ = [
    "build_shot_intent",
    "build_plan_intents",
    "extract_retained_facts",
    "DirectorIntent",
    "IntentSource",
]