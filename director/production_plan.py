#!/usr/bin/env python3
"""V1.7 Phase 1 · ProductionPlan Schema v1（AI 制作中间层，V17_PLAN §4/P0-A）。

用户 2026-08-11 拍板约定：
1. ProductionPlan = AI 制作中间层；SPA Shot = UI / 持久化层模型。两者不是同一个东西。
   数据流锁死：剧本 → ProductionPlan → Asset Registry → Generation Planning → H3 Prompt → SPA Shot。
   绝不「ProductionPlan 直接塞 SPA Shot」。
2. Phase 1 刻意不放的字段：castIds / locationId / assetId / generationMode / h3Prompt / camera / refs。
   castIds/locationId → Phase 2 Asset Registry；generationMode → Phase 3；h3Prompt → Phase 4（需官方 H3 Skill）。
3. duration_sec = 该镜头在最终成片时间轴上的剧情/成片时长（秒）。
   它 ≠ H3 generation duration；Phase 3 再把 duration_sec 映射到 H3 生成档位。
   SPA Shot.durationSec 最终与 ProductionPlan.duration_sec 对齐。
4. dialogue[].speaker → characters[].name（Phase 1 校验规则约束）。
   characters[].name → Phase 2 Asset Registry → castIds[]。Phase 1 绝不生成 Asset ID。
5. validation 只做机器侧结构校验，不代替人工审核；人工审核 = SPA render.status "review"。

刻意不放进来的东西（分别属于后面阶段）：
    castIds / locationId / assetId   → Phase 2 Asset Registry
    generationMode                   → Phase 3 Generation Mode 判定
    h3Prompt / camera / refs         → Phase 4 H3 Prompt Builder。
       （用户 2026-08-13 拍板：官方 base-en/ref-en 仓库不存在 → 建立**项目自己的**
        minimax-h3-project-v1 Schema，明确标注非官方；DirectorIntent 为派生数据，
        经 director/director_intent.py 重建，不落本 JSON。）

字段命名统一 snake_case；to_dict() 输出即 JSON 落盘格式（与 SPA 对接时再经 Adapter 映射）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 镜头时长规则硬上限（与 script_parser 共用，防 Qwen/规则失控）
MIN_SHOT_DURATION = 2
MAX_SHOT_DURATION = 8
MAX_SHOTS_PER_SCENE = 8

SCHEMA_VERSION = "v1"


class VoiceType:
    """对白声音类型（用户 2026-08-15 拍板 TTS/Voice Cast 声音导演层）。

    character_dialogue = 角色对白（林薇薇：「就这？」）
    narration         = 旁白（林晓感觉胸口那股郁结之气……）
    inner_monologue   = 内心独白（「完了。穿越了。」）
    system_voice      = 系统音（【吐槽值+50！】）
    """

    CHARACTER_DIALOGUE = "character_dialogue"
    NARRATION = "narration"
    INNER_MONOLOGUE = "inner_monologue"
    SYSTEM_VOICE = "system_voice"

    ALL = (CHARACTER_DIALOGUE, NARRATION, INNER_MONOLOGUE, SYSTEM_VOICE)

    @classmethod
    def normalize(cls, raw: Any) -> str:
        """非法/空值 → character_dialogue（向后兼容旧数据），合法值原样返回。"""
        return raw if raw in cls.ALL else cls.CHARACTER_DIALOGUE


@dataclass
class Dialogue:
    """对白。speaker 必须对应本场景 characters[].name（校验规则）。

    type / voice_id / emotion / delivery 为 TTS/Voice Cast 声音导演层字段
    （2026-08-15 扩展，全默认值向后兼容，旧数据 from_dict 自动补 character_dialogue）：
      - type:      四类声音之一（VoiceType）
      - voice_id:  角色→TTS 音色 ID（空则由 Voice Cast 规则解析）
      - emotion:   情绪提示（供 TTS 语气/情绪，可选）
      - delivery:  语速/语气提示（如「缓慢」「低语」「电子音」，可选）
    """

    speaker: str = ""
    text: str = ""
    type: str = VoiceType.CHARACTER_DIALOGUE
    voice_id: str = ""
    emotion: str = ""
    delivery: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "speaker": self.speaker,
            "text": self.text,
            "type": self.type,
            "voice_id": self.voice_id,
            "emotion": self.emotion,
            "delivery": self.delivery,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Dialogue":
        if not isinstance(data, dict):
            return cls()
        return cls(
            speaker=str(data.get("speaker", "")),
            text=str(data.get("text", "")),
            type=VoiceType.normalize(data.get("type", "")),
            voice_id=str(data.get("voice_id", "")),
            emotion=str(data.get("emotion", "")),
            delivery=str(data.get("delivery", "")),
        )


@dataclass
class Character:
    """语义角色（Phase 1 只到名字；Phase 2 才绑 Asset/castIds）。

    appearance（生成约束层 v1.0 P2，PROMPT_COMPILER_V1 §4-P2）：角色外观描述。
    来源=Qwen 导入产出（character_profiles）→ 规则兜底（costumes+role）→ 空。
    铁律=只从原文+合理外观推断，绝不自行创造服装；空串时约束块只锁名字+数量。
    """

    name: str = ""
    role: str = ""
    appearance: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "role": self.role, "appearance": self.appearance}

    @classmethod
    def from_dict(cls, data: Any) -> "Character":
        if not isinstance(data, dict):
            return cls()
        return cls(
            name=str(data.get("name", "")),
            role=str(data.get("role", "")),
            appearance=str(data.get("appearance", "")),
        )


@dataclass
class Prop:
    """语义道具（Phase 1 由 Qwen 补全，Phase 2 绑资产）。"""

    name: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name}

    @classmethod
    def from_dict(cls, data: Any) -> "Prop":
        if not isinstance(data, dict):
            return cls()
        return cls(name=str(data.get("name", "")))


class EntityType:
    """实体类型体系（用户 2026-08-11 实体抽取加固拍板）。

    character=角色 / location=地点 / prop=道具 / costume=服装 /
    environment=环境 / effect=效果 / architecture=建筑/室内构件 /
    vehicle=载具 / creature=生物 / unknown=未判定。
    """

    CHARACTER = "character"
    LOCATION = "location"
    PROP = "prop"
    COSTUME = "costume"
    ENVIRONMENT = "environment"
    EFFECT = "effect"
    ARCHITECTURE = "architecture"
    VEHICLE = "vehicle"
    CREATURE = "creature"
    UNKNOWN = "unknown"

    ALL = (
        CHARACTER, LOCATION, PROP, COSTUME, ENVIRONMENT,
        EFFECT, ARCHITECTURE, VEHICLE, CREATURE, UNKNOWN,
    )


class EntitySource:
    """实体来源：script=剧本原文明确存在（默认，进资产匹配）；
    inferred=AI 为画面合理性推断（不进资产匹配，仅供视觉规划）。"""

    SCRIPT = "script"
    INFERRED = "inferred"

    ALL = (SCRIPT, INFERRED)


class AssetRequirement:
    """资产需求等级（Phase 1.1 用户拍板：剧本实体 vs 视觉元素两层分流）。

    - required：必须匹配资产（角色/地点——它们绑定 castIds/locationId 的硬前提）
    - recommended：建议匹配（道具/服装/建筑/载具/生物——可给参考图增强一致性）
    - none：视觉元素，不进资产匹配（环境/效果/未知——只进 Prompt，交给 Prompt Builder）

    判定由 `asset_requirement_for_type()` 规则推导（纯规则零幻觉，Qwen 不输出该字段）。
    """

    REQUIRED = "required"
    RECOMMENDED = "recommended"
    NONE = "none"

    ALL = (REQUIRED, RECOMMENDED, NONE)


def asset_requirement_for_type(etype: str) -> str:
    """实体类型 → 资产需求等级（Phase 1.1 分流规则，纯规则）。

    character/location → required（必须匹配，资产绑定硬前提）
    prop/vehicle/creature → recommended（建议匹配）
    costume/architecture/environment/effect/unknown → none
        （P0-2 用户拍板 2026-08-11：服装(青衫)/空间描述(山道) 是画面风格元素，
        只进 Prompt，不进资产匹配——避免 Asset Registry 为一个服装/一个建筑
        创建不必要的资产卡）
    """
    if etype in (EntityType.CHARACTER, EntityType.LOCATION):
        return AssetRequirement.REQUIRED
    if etype in (
        EntityType.PROP, EntityType.VEHICLE, EntityType.CREATURE,
    ):
        return AssetRequirement.RECOMMENDED
    return AssetRequirement.NONE


_entity_seq = [0]


def new_entity_id(used: Optional[set] = None) -> str:
    """生成全局唯一实体 id（ent_001…），跳过 used 集合里已占用的 id。

    used：本 plan 内已存在的实体 id 集合（规则实体 / 从 JSON 载入的实体），
    保证新 id 不与既有实体撞车（否则前端 castIds 绑定会拿到重复 id）。
    """
    used = used or set()
    _entity_seq[0] += 1
    eid = f"ent_{_entity_seq[0]:03d}"
    while eid in used:
        _entity_seq[0] += 1
        eid = f"ent_{_entity_seq[0]:03d}"
    return eid


@dataclass
class Entity:
    """剧本语义实体（Entity Registry 的基本单位，V17 实体抽取加固 + Phase 1.1 两层分流）。

    实体 ≠ 资产：entity 是「剧本里出现的事物」，asset 是「资产库里已有的图」。
    资产匹配把实体匹配到资产文件；未匹配的实体留空，进工作台人工补图。

    - type：EntityType 之一（Qwen 判定 + 规则纠正）
    - source：EntitySource（script=剧本明确存在 / inferred=视觉推断）
    - confidence：0~1（≥0.85 进自动匹配 / 0.60~0.85 待确认 / <0.60 不进匹配）
    - asset_requirement：AssetRequirement 之一（Phase 1.1；由 type 规则推导；
      none=视觉元素，不进资产匹配，只进 Prompt）
    - aliases：同义词/别名（规范化名去重时合并）
    """

    entity_id: str = ""

    # ⛔ entity_id = 本次解析的临时身份（new_entity_id 进程内计数器分配）。
    #   跨解析/跨进程必然变化（Qwen 追加实体时尤其如此）。绝不作为跨解析主键：
    #   持久化绑定/复用一律走 entity_key（{清洗后最终类型}:{canonical_name}）。
    name: str = ""
    type: str = EntityType.UNKNOWN
    source: str = EntitySource.SCRIPT
    confidence: float = 0.0
    asset_requirement: str = AssetRequirement.REQUIRED
    aliases: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "type": self.type,
            "source": self.source,
            "confidence": round(float(self.confidence or 0.0), 3),
            "asset_requirement": self.asset_requirement,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Entity":
        if not isinstance(data, dict):
            return cls()
        return cls(
            entity_id=str(data.get("entity_id", "")),
            name=str(data.get("name", "")),
            type=str(data.get("type", EntityType.UNKNOWN)),
            source=str(data.get("source", EntitySource.SCRIPT)),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            asset_requirement=str(
                data.get("asset_requirement", AssetRequirement.REQUIRED)
            ),
            aliases=[str(a) for a in data.get("aliases", [])],
        )


@dataclass
class VisualElement:
    """视觉元素（Phase 1.1：与 ScriptEntity 两层分流；只进 Prompt，永不进资产匹配）。

    覆盖：环境/氛围/光线/天气类视觉词（晨光/山雾/寒气/雨丝/暖黄灯火/尘埃/风/阴影），
    以及 Qwen Task B 允许的合理视觉推断。Qwen 输出到 `visual_elements` 数组，
    Prompt Builder 消费它增强 Visual 区草稿；Asset Matcher 完全忽略。
    """

    name: str = ""
    type: str = EntityType.UNKNOWN
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "confidence": round(float(self.confidence or 0.0), 3),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VisualElement":
        if not isinstance(data, dict):
            return cls()
        return cls(
            name=str(data.get("name", "")),
            type=str(data.get("type", EntityType.UNKNOWN)),
            confidence=float(data.get("confidence", 0.0) or 0.0),
        )


@dataclass
class Shot:
    shot_id: str = ""
    source_text: str = ""  # 原剧本逐字保留（永久），AI 理解分离后的原始层
    duration_sec: int = 5  # 成片时间轴时长（秒），≠ H3 generation duration
    characters: List[Character] = field(default_factory=list)
    props: List[Prop] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    visual_elements: List[VisualElement] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    emotion: str = ""
    psych_tags: List[str] = field(default_factory=list)  # v2.0 P2 心理词预标记（软约束转硬输入）
    dialogue: List[Dialogue] = field(default_factory=list)
    visual_intent: str = ""  # 视觉意图草稿（Phase 1 由 Qwen 生成）；≠ content.visual（Phase 4 产物）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "source_text": self.source_text,
            "duration_sec": self.duration_sec,
            "characters": [c.to_dict() for c in self.characters],
            "props": [p.to_dict() for p in self.props],
            "entities": [e.to_dict() for e in self.entities],
            "visual_elements": [v.to_dict() for v in self.visual_elements],
            "actions": list(self.actions),
            "emotion": self.emotion,
            "psych_tags": list(self.psych_tags),
            "dialogue": [d.to_dict() for d in self.dialogue],
            "visual_intent": self.visual_intent,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Shot":
        if not isinstance(data, dict):
            return cls()
        return cls(
            shot_id=str(data.get("shot_id", "")),
            source_text=str(data.get("source_text", "")),
            duration_sec=int(data.get("duration_sec", 5) or 5),
            characters=[Character.from_dict(c) for c in data.get("characters", [])],
            props=[Prop.from_dict(p) for p in data.get("props", [])],
            entities=[Entity.from_dict(e) for e in data.get("entities", [])],
            visual_elements=[
                VisualElement.from_dict(v) for v in data.get("visual_elements", [])
            ],
            actions=[str(a) for a in data.get("actions", [])],
            emotion=str(data.get("emotion", "")),
            psych_tags=[str(t) for t in data.get("psych_tags", [])],
            dialogue=[Dialogue.from_dict(d) for d in data.get("dialogue", [])],
            visual_intent=str(data.get("visual_intent", "")),
        )


class IntentSource:
    """意图字段来源标注（用户 2026-08-13 P0-① 拍板：三类来源可追溯）。

    USER = 用户原文逐字保留（最高优先级，任何阶段不得被覆盖）
    RULE = 规则层确定性生成（场景字段 / 模板 / 状态机）
    AI   = Qwen 补全/推断（原文没有、但导演需要；只能补充不能覆盖原文）
    MIX  = 规则定结构 + AI 补语义
    """

    USER = "USER"
    RULE = "RULE"
    AI = "AI"
    MIX = "MIX"

    ALL = (USER, RULE, AI, MIX)


@dataclass
class DirectorIntent:
    """导演意图中间层（P0-①，用户 2026-08-13 拍板）。

    统一中间数据结构：``user_original_intent`` 逐字保底（原文事实最高优先级，
    双轨合并去重时原文优先、AI 只补充不覆盖）；每个字段带 ``provenance``
    来源标注（USER/RULE/AI/MIX），供 UI 三层可追溯（原始剧本 / AI 结构化理解 /
    最终 H3 Prompt）。

    ⛔ 本结构是**派生数据**，不落 ProductionPlan JSON（已验收的数据链路不动）：
    每次从 ProductionPlan 经 ``director_intent.build_shot_intent()`` 重建。
    """
    shot_id: str = ""
    scene_id: str = ""
    user_original_intent: str = ""                       # 🟢 = source_text 逐字（原文保底）
    retained_facts: List[str] = field(default_factory=list)  # 🟢 原文导演事实（雨刚停/从林间漫向/…）
    subject: str = ""                                    # 🟡 本镜主体（山雨楼 = 建筑）
    characters: List[str] = field(default_factory=list)  # 🟡 出场角色（本镜出现）
    location: str = ""                                   # 🔵 场景 location_name
    action: List[str] = field(default_factory=list)      # 🟡 动作序列（原文事实，保序）
    emotion: str = ""                                    # 🟣 AI 情绪基调
    composition: str = ""                                # 🟣 AI 构图建议
    camera_position: str = ""                            # 🔵 景别（establish → 全景）
    movement: str = ""                                   # 🔵 运镜（缓摇）
    lighting: str = ""                                   # 🟡 光线（原文昏黄灯笼/暖光 + AI 冷暖对比）
    environment: List[str] = field(default_factory=list) # 🟡 环境/氛围（visual_elements）
    style: str = ""                                    # 🟡 风格（用户五区 style 覆盖注入；无覆盖为空）
    # ---- v2.0 内容规则派生字段（CAMERA_RULES_V2 §4，P1 接线）----
    core_action: str = ""                              # ① 本镜唯一核心视觉事件（含动作过程措辞）
    expression: str = ""                               # ② 本镜核心表情变化（谁的脸怎么变）
    tension: int = 0                                   # ③ 张力档位 0-3（来自 beat_rhythm）
    rhythm: str = ""                                   # ③ build/peak/release/hold
    composition_note: str = ""                         # ⑤ 构图说明（P1 默认空，构图句默认关）
    continuity: Dict[str, Any] = field(default_factory=dict)  # 🔵 跨镜状态（P0-③ 扩展，首镜为空）
                                                       #   v2.0: continuity.reaction_forced=bool（§3.4 反应镜硬规则）
    audio: Dict[str, Any] = field(default_factory=dict)  # 🔵 规则音频（雨→雨声；无对白→舒缓音乐）
    entities: List[Dict[str, Any]] = field(default_factory=list)  # 🟡 剧本事实实体（已过边界检查）
    provenance: Dict[str, str] = field(default_factory=dict)  # 字段名 → IntentSource
    # ---- 生成约束层 v1.0（PROMPT_COMPILER_V1，P1 接线）----
    constraint: Optional[Dict[str, Any]] = None  # 🔵 ShotPlan.to_dict()（角色锁/空间锁/动作顺序/镜头锁/禁止）
                                               #   送 H3 时经 compile_constraint_block 渲染双语约束块

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "user_original_intent": self.user_original_intent,
            "retained_facts": list(self.retained_facts),
            "subject": self.subject,
            "characters": list(self.characters),
            "location": self.location,
            "action": list(self.action),
            "emotion": self.emotion,
            "composition": self.composition,
            "camera_position": self.camera_position,
            "movement": self.movement,
            "lighting": self.lighting,
            "environment": list(self.environment),
            "style": self.style,
            # v2.0 内容规则派生字段（P1 接线；未激活时为默认空值，不影响旧链路）
            "core_action": self.core_action,
            "expression": self.expression,
            "tension": self.tension,
            "rhythm": self.rhythm,
            "composition_note": self.composition_note,
            "continuity": dict(self.continuity),
            "audio": dict(self.audio),
            "entities": [dict(e) for e in self.entities],
            "provenance": dict(self.provenance),
            "constraint": dict(self.constraint) if self.constraint else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DirectorIntent":
        if not isinstance(data, dict):
            return cls()
        return cls(
            shot_id=str(data.get("shot_id", "")),
            scene_id=str(data.get("scene_id", "")),
            user_original_intent=str(data.get("user_original_intent", "")),
            retained_facts=[str(f) for f in data.get("retained_facts", [])],
            subject=str(data.get("subject", "")),
            characters=[str(c) for c in data.get("characters", [])],
            location=str(data.get("location", "")),
            action=[str(a) for a in data.get("action", [])],
            emotion=str(data.get("emotion", "")),
            composition=str(data.get("composition", "")),
            camera_position=str(data.get("camera_position", "")),
            movement=str(data.get("movement", "")),
            lighting=str(data.get("lighting", "")),
            environment=[str(e) for e in data.get("environment", [])],
            style=str(data.get("style", "")),
            core_action=str(data.get("core_action", "")),
            expression=str(data.get("expression", "")),
            tension=int(data.get("tension", 0) or 0),
            rhythm=str(data.get("rhythm", "")),
            composition_note=str(data.get("composition_note", "")),
            continuity=dict(data.get("continuity", {}) or {}),
            audio=dict(data.get("audio", {}) or {}),
            entities=[dict(e) for e in data.get("entities", [])],
            provenance=dict(data.get("provenance", {}) or {}),
            constraint=dict(data.get("constraint")) if data.get("constraint") else None,
        )


@dataclass
class Scene:
    scene_id: str = ""
    title: str = ""
    location_name: str = ""
    time: str = ""
    weather: str = ""
    shots: List[Shot] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "title": self.title,
            "location_name": self.location_name,
            "time": self.time,
            "weather": self.weather,
            "shots": [s.to_dict() for s in self.shots],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Scene":
        if not isinstance(data, dict):
            return cls()
        return cls(
            scene_id=str(data.get("scene_id", "")),
            title=str(data.get("title", "")),
            location_name=str(data.get("location_name", "")),
            time=str(data.get("time", "")),
            weather=str(data.get("weather", "")),
            shots=[Shot.from_dict(s) for s in data.get("shots", [])],
        )

    def character_names(self) -> List[str]:
        """本 Scene 全部镜头出现的角色名（去重保序）。"""
        seen: List[str] = []
        for shot in self.shots:
            for c in shot.characters:
                if c.name and c.name not in seen:
                    seen.append(c.name)
        return seen


@dataclass
class StoryBeat:
    """P1-B 剧情结构层（2026-08-14 用户拍板）：描述「这段剧情为什么存在、讲了什么」。

    四层不混（用户拍板核心原则）：
    - Location = 世界资产容器（Scene，scene_id 即 locationId 兼容）
    - Timeline = 播放顺序（ProductionPlan.timeline）
    - **Beat = 剧情结构**（本类）
    - Shot = 实际拍摄单位（Scene.shots）

    Beat 不决定播放顺序（那是 timeline），不决定镜头边界（那是 Shot Blueprint）。
    dramatic_function 是后续导演调度的输入（introduce_character/dialogue/…）。
    text_segments 逐字保存本 Beat 覆盖的小说原文（覆盖校验的源，见 P1B 设计文档 §6.5）。
    """
    beat_id: str = ""               # beat_01（规则层分配）
    title: str = ""                 # 节拍标题（沈青崖进入山雨楼）
    summary: str = ""               # 剧情摘要（Qwen）
    dramatic_function: str = ""     # 剧情功能（DRAMATIC_FUNCTIONS，story_analyzer 层校验）
    scene_id: str = ""              # 主发生 Location（scene_id 即 locationId 兼容）
    time: str = ""                  # 时间（黄昏）
    weather: str = ""               # 天气（雨）
    text_segments: List[str] = field(default_factory=list)  # 🟢 本 Beat 覆盖的小说原文片段（逐字）
    order: int = 0                  # 剧情顺序（规则）
    entries: List[str] = field(default_factory=list)  # ["scene_id:shot_id", ...] 本 Beat 镜头
    source: str = "ai"              # ai | rule（Qwen 失败降级为 rule）
    transition_reason: str = ""     # AI 候选：为什么切到这里（仅审核参考，不决定播放顺序）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "beat_id": self.beat_id,
            "title": self.title,
            "summary": self.summary,
            "dramatic_function": self.dramatic_function,
            "scene_id": self.scene_id,
            "time": self.time,
            "weather": self.weather,
            "text_segments": list(self.text_segments),
            "order": self.order,
            "entries": list(self.entries),
            "source": self.source,
            "transition_reason": self.transition_reason,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "StoryBeat":
        if not isinstance(data, dict):
            return cls()
        return cls(
            beat_id=str(data.get("beat_id", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            dramatic_function=str(data.get("dramatic_function", "")),
            scene_id=str(data.get("scene_id", "")),
            time=str(data.get("time", "")),
            weather=str(data.get("weather", "")),
            text_segments=[str(x) for x in (data.get("text_segments")
                                            if isinstance(data.get("text_segments"), list) else [])],
            order=_as_int_defensive(data.get("order")),
            entries=[str(x) for x in (data.get("entries")
                                      if isinstance(data.get("entries"), list) else [])],
            source=str(data.get("source", "ai")),
            transition_reason=str(data.get("transition_reason", "")),
        )


def _as_int_defensive(value: Any, default: int = 0) -> int:
    """把未知值安全转 int（bool/None/非数字串 → default，其余 int 化），from_dict 防御用。"""
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ProjectInfo:
    title: str = ""
    source_file: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"title": self.title, "source_file": self.source_file}

    @classmethod
    def from_dict(cls, data: Any) -> "ProjectInfo":
        if not isinstance(data, dict):
            return cls()
        return cls(title=str(data.get("title", "")), source_file=str(data.get("source_file", "")))


@dataclass
class Validation:
    status: str = "pending"  # pending | valid | invalid（机器侧结构校验；人工审核在 SPA render.status=review）
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "errors": list(self.errors), "warnings": list(self.warnings)}

    @classmethod
    def from_dict(cls, data: Any) -> "Validation":
        if not isinstance(data, dict):
            return cls()
        return cls(
            status=str(data.get("status", "pending")),
            errors=[str(e) for e in data.get("errors", [])],
            warnings=[str(w) for w in data.get("warnings", [])],
        )


@dataclass
class ProductionPlan:
    project: ProjectInfo = field(default_factory=ProjectInfo)
    scenes: List[Scene] = field(default_factory=list)
    validation: Validation = field(default_factory=Validation)
    # P0 Story Timeline（2026-08-14 用户拍板）：**播放顺序**数组，与场景树解耦。
    # 元素格式 `"{scene_id}:{shot_id}"`（无歧义；shot_id 场景内唯一、跨场景可重复）。
    # 同一 Scene 可在一章内多次被引用（交叉剪辑：大堂→后院→大堂）。
    # 空 / 缺省 = 场景树顺序（向后兼容，老项目不破坏）。
    timeline: List[str] = field(default_factory=list)
    # P1-B 剧情结构层（2026-08-14 用户拍板）：Story Beat 列表。
    # 元数据层，不参与结构校验；不决定播放顺序（timeline 才决定）。
    # 老数据缺省空列表，严格向后兼容。
    beats: List[StoryBeat] = field(default_factory=list)
    # 生成约束层 v1.0 P2（PROMPT_COMPILER_V1 §4-P2，2026-08-18 用户拍板）：
    # 跨镜角色外观档案 name→appearance。随 plan 流经全链路（导入→存盘→导演→编译）。
    # 来源=Qwen character_profiles 导入产出 → 规则兜底（costumes+role）→ 空串。
    # 铁律=只从原文+合理外观推断，绝不自行创造服装。
    character_profiles: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project.to_dict(),
            "scenes": [s.to_dict() for s in self.scenes],
            "validation": self.validation.to_dict(),
            "timeline": list(self.timeline),
            "beats": [b.to_dict() for b in self.beats],
            "character_profiles": dict(self.character_profiles),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ProductionPlan":
        if not isinstance(data, dict):
            return cls()
        return cls(
            project=ProjectInfo.from_dict(data.get("project")),
            scenes=[Scene.from_dict(s) for s in data.get("scenes", [])],
            validation=Validation.from_dict(data.get("validation")),
            timeline=[str(x) for x in (data.get("timeline") or [])],
            beats=[StoryBeat.from_dict(b) for b in (data.get("beats") or [])],
            character_profiles={
                str(k): str(v)
                for k, v in (data.get("character_profiles") or {}).items()
                if str(v).strip()
            },
        )

    # ---- 结构校验（机器侧；人工审核 = SPA render.status=review）----
    def validate(self) -> Validation:
        errors: List[str] = []
        warnings: List[str] = []

        if not self.scenes:
            errors.append("计划没有任何场景")

        for scene in self.scenes:
            label = scene.scene_id or scene.title or "（无名场景）"
            if not scene.scene_id:
                errors.append("场景缺少 scene_id")
            if not scene.shots:
                errors.append(f"场景 {label} 没有镜头（Scene 必有 Shot）")
                continue
            if len(scene.shots) > MAX_SHOTS_PER_SCENE:
                warnings.append(
                    f"场景 {label} 镜头数 {len(scene.shots)} 超过上限 {MAX_SHOTS_PER_SCENE}"
                )
            names = scene.character_names()
            seen_ids: set[str] = set()
            for shot in scene.shots:
                if not shot.shot_id:
                    errors.append(f"场景 {label} 存在缺少 shot_id 的镜头")
                else:
                    if shot.shot_id in seen_ids:
                        errors.append(f"场景 {label} 存在重复 shot_id：{shot.shot_id}")
                    seen_ids.add(shot.shot_id)
                if not shot.source_text.strip():
                    warnings.append(f"{shot.shot_id} 无原始文本（source_text 为空）")
                if shot.duration_sec < MIN_SHOT_DURATION or shot.duration_sec > MAX_SHOT_DURATION:
                    warnings.append(
                        f"{shot.shot_id} duration_sec={shot.duration_sec} "
                        f"超出 [{MIN_SHOT_DURATION}, {MAX_SHOT_DURATION}]"
                    )
                # dialogue.speaker → characters[].name（角色表非空时才校验）
                for d in shot.dialogue:
                    if d.speaker and names and d.speaker not in names:
                        warnings.append(
                            f"{shot.shot_id} 对白说话者「{d.speaker}」不在本场景角色表 {names} 中"
                        )

        # P0 Story Timeline：timeline 里引用的每个 {scene_id}:{shot_id} 必须能定位到镜头；
        # 找不到的条目 → warning（宽容，不 error——前端拖拽中间态允许）。
        all_keys = {
            f"{s.scene_id}:{sh.shot_id}"
            for s in self.scenes for sh in s.shots
        }
        seen_keys = set()
        for entry in self.timeline:
            if not isinstance(entry, str) or ":" not in entry:
                warnings.append(f"timeline 存在非法条目：{entry!r}")
                continue
            if entry not in all_keys:
                warnings.append(f"timeline 引用不存在的镜头：{entry}")
                continue
            if entry in seen_keys:
                warnings.append(f"timeline 重复引用镜头：{entry}")
                continue
            seen_keys.add(entry)

        self.validation = Validation(
            status="valid" if not errors else "invalid", errors=errors, warnings=warnings
        )
        return self.validation

    def total_shots(self) -> int:
        return sum(len(s.shots) for s in self.scenes)

    def total_duration_sec(self) -> int:
        return sum(shot.duration_sec for s in self.scenes for shot in s.shots)


def dump_json(plan: ProductionPlan, path: str) -> None:
    """落盘（UTF-8，ensure_ascii=False）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)


def load_json(path: str) -> ProductionPlan:
    """读盘。"""
    with open(path, "r", encoding="utf-8") as f:
        return ProductionPlan.from_dict(json.load(f))


def ensure_plan_file(plan: ProductionPlan, dirpath: str, name: str = "production_plan.json") -> str:
    """把 plan 写进指定目录（原子：先写临时再改名），返回路径。"""
    os.makedirs(dirpath, exist_ok=True)
    out = os.path.join(dirpath, name)
    tmp = out + ".tmp"
    dump_json(plan, tmp)
    os.replace(tmp, out)
    return out


# ---------- P0 Story Timeline 纯函数（2026-08-14 用户拍板） ----------
#
# Scene 是「空间/资产容器」，timeline 是「播放顺序」。同一 Scene 可被多次引用，
# 不同 Scene 按剧情交叉剪辑。timeline 元素格式 `"{scene_id}:{shot_id}"`：
#   - shot_id 场景内唯一、跨场景可重复，必须带 scene_id 前缀才能无歧义定位；
#   - 与 DirectorIntent 返回键 {scene_id:shot_id} 一致，overrides_by_shot 同源。


def _iter_scenes(plan: Any) -> List[Any]:
    """ProductionPlan dataclass 或 dict 统一取 scenes 列表。"""
    if hasattr(plan, "scenes"):
        return list(plan.scenes)
    if isinstance(plan, dict):
        return list(plan.get("scenes", []))
    return []


def _iter_shots(scene: Any) -> List[Any]:
    if hasattr(scene, "shots"):
        return list(scene.shots)
    if isinstance(scene, dict):
        return list(scene.get("shots", []))
    return []


def _scene_id(scene: Any) -> str:
    return str(scene.scene_id if hasattr(scene, "scene_id") else scene.get("scene_id", ""))


def _shot_id(shot: Any) -> str:
    return str(shot.shot_id if hasattr(shot, "shot_id") else shot.get("shot_id", ""))


def _plan_timeline(plan: Any) -> List[str]:
    if hasattr(plan, "timeline"):
        return [str(x) for x in (plan.timeline or [])]
    if isinstance(plan, dict):
        return [str(x) for x in (plan.get("timeline") or [])]
    return []


def default_timeline(plan: Any) -> List[str]:
    """场景树顺序的播放顺序（向后兼容老数据）。"""
    return [f"{_scene_id(s)}:{_shot_id(sh)}" for s in _iter_scenes(plan) for sh in _iter_shots(s)]


def shot_lookup(plan: Any) -> Dict[str, Tuple[Any, Any]]:
    """`"{scene_id}:{shot_id}"` → (scene, shot)。scene/shot 保持 plan 里的原始对象。"""
    out: Dict[str, Tuple[Any, Any]] = {}
    for s in _iter_scenes(plan):
        for sh in _iter_shots(s):
            out[f"{_scene_id(s)}:{_shot_id(sh)}"] = (s, sh)
    return out


def resolve_timeline_order(plan: Any) -> List[Tuple[str, str]]:
    """返回播放顺序 [(scene_id, shot_id), ...]。

    优先 plan.timeline（`scene_id:shot_id`），非法/无法定位的条目跳过；
    最后**补齐**场景树顺序里未被 timeline 引用的镜头（保证不丢镜）。
    timeline 为空 / 全部解析失败 → 纯场景树顺序（严格向后兼容）。
    """
    all_keys: List[Tuple[str, str]] = [
        (str(_scene_id(s)), str(_shot_id(sh)))
        for s in _iter_scenes(plan) for sh in _iter_shots(s)
    ]
    raw = _plan_timeline(plan)
    if not raw:
        return all_keys
    all_set = set(all_keys)
    seen: set[Tuple[str, str]] = set()
    out: List[Tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, str) or ":" not in entry:
            continue
        sid, _, shid = entry.partition(":")
        key = (sid, shid)
        if key in all_set and key not in seen:
            seen.add(key)
            out.append(key)
    for key in all_keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out
