#!/usr/bin/env python3
"""Shot Planning 层（生成约束层 v1.0，PROMPT_COMPILER_V1 §3.1，用户 2026-08-18 拍板）。

分镜描述 ≠ 视频生成 Prompt。本模块把 DirectorIntent（剧情意图）编译成 ShotPlan
（画面执行指令的约束结构），交给 prompt_compiler 出双语约束块、constraint_checker 出检查清单。

解决用户实测三大病灶（办公室多出第三人 / 角色复制同框 / 一镜自己切景别）：
- Character Lock + Character Count：锁「画面里只有这 N 人，每人只出现一次」。
- Spatial Lock：复用 v1.0 spatial_continuity 的双语站位块（180° 轴线）。
- Action Timeline：动作编号序列（核心动作标记来自 core_action）。
- Camera Lock：一镜一摄影机状态，禁止景别切换。
- Performance Lock：原文否定句（「没有伸手去拿」）→ 禁止动作。
- Forbidden：生成禁止事项清单。

⛔ 纯规则、零 LLM、零显存、零网络（与 core_action/spatial_continuity 同策略）。
⛔ 本模块只**派生**，绝不改写 source_text / retained_facts（原文事实最高优先级铁律）。

核心入口：
- `build_shot_plan(intent, appearance_map=…)` → ShotPlan（可 to_dict 存 DirectorIntent.constraint）
- `extract_shot_sizes(text)` → 景别词去重保序（多景别冲突检测用）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 景别词表（与 director_intent._SHOT_SIZE_CN / camera_template 枚举对齐；按语义长度降序
# 保证「中近景」先于「中景/近景」命中、「大远景」先于「远景」）。
_SHOT_SIZE_KEYWORDS: tuple[str, ...] = (
    "大远景", "大特写", "中近景", "远景", "全景", "中景", "近景", "特写",
)
# 长词优先交替正则：逐位匹配命中即消费该段，杜绝「中近景」同时误报「近景/中景」。
_SHOT_SIZE_RE = re.compile("|".join(re.escape(k) for k in _SHOT_SIZE_KEYWORDS))

# 否定句标记（Performance Lock 抽取）。「不」单独处理需动词白名单防误伤（「冷漠」等）。
_NEG_MARKERS: tuple[str, ...] = ("没有", "未曾", "并未", "不曾", "并未", "未", "不")
# 「不 + 动作动词」白名单（仅明确动作/行为动词才抽，防心理/状态词误伤）。
_NO_ACTION_VERBS: tuple[str, ...] = (
    "伸手", "去拿", "拿起", "去接", "接", "拿", "碰", "捡", "翻", "动",
    "回身", "起身", "抬头", "低头", "说话", "开口", "回答", "理睬",
)
# 保持静止类（Performance Lock 第二通道）。
_STILL_HINTS: tuple[str, ...] = ("保持静止", "静止不动", "纹丝不动", "一动不动", "站在原地")

_NEG_RE = re.compile(r"(?P<marker>没有|未曾|并未|不曾|未|不)\s*(?P<verb>去?[一-鿿]{2,8})")


@dataclass
class CharacterLock:
    """单角色锁定（Character Lock）。"""
    name: str = ""
    appearance: str = ""   # Qwen 导入产出（P2）/ 规则提取 / 空串（只锁名字+数量）
    role: str = ""         # 剧本角色定位（P2 character_profiles 可带）


@dataclass
class ActionItem:
    """动作时间轴单条（Action Timeline）。"""
    text: str = ""
    subject: str = ""          # 动作主体（谁做；缺省空）
    is_primary: bool = False   # 核心动作标记（来自 core_action）


@dataclass
class ShotPlan:
    """单镜约束计划（可序列化进 DirectorIntent.constraint）。"""
    shot_id: str = ""
    scene_id: str = ""
    location: str = ""                              # 本镜场景（scene_locked info 用）
    character_count: int = 0
    character_locks: List[CharacterLock] = field(default_factory=list)
    spatial_lock: str = ""                          # 复用 spatial_continuity block()（双语）
    action_timeline: List[ActionItem] = field(default_factory=list)
    camera_lock: Dict[str, str] = field(default_factory=dict)  # {shot_size, movement, cn}
    performance_lock: List[str] = field(default_factory=list)  # 禁止动作（原文否定句）
    forbidden: List[str] = field(default_factory=list)         # 生成禁止事项
    shot_sizes_seen: List[str] = field(default_factory=list)   # 检测到的景别（≥2 冲突）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "location": self.location,
            "character_count": self.character_count,
            "character_locks": [
                {"name": c.name, "appearance": c.appearance, "role": c.role}
                for c in self.character_locks
            ],
            "spatial_lock": self.spatial_lock,
            "action_timeline": [
                {"text": a.text, "subject": a.subject, "is_primary": a.is_primary}
                for a in self.action_timeline
            ],
            "camera_lock": dict(self.camera_lock),
            "performance_lock": list(self.performance_lock),
            "forbidden": list(self.forbidden),
            "shot_sizes_seen": list(self.shot_sizes_seen),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ShotPlan":
        if not isinstance(data, dict):
            return cls()
        locks = [
            CharacterLock(
                name=str(c.get("name", "") or ""),
                appearance=str(c.get("appearance", "") or ""),
                role=str(c.get("role", "") or ""),
            )
            for c in (data.get("character_locks") or []) if isinstance(c, dict)
        ]
        timeline = [
            ActionItem(
                text=str(a.get("text", "") or ""),
                subject=str(a.get("subject", "") or ""),
                is_primary=bool(a.get("is_primary", False)),
            )
            for a in (data.get("action_timeline") or []) if isinstance(a, dict)
        ]
        cam = data.get("camera_lock")
        return cls(
            shot_id=str(data.get("shot_id", "") or ""),
            scene_id=str(data.get("scene_id", "") or ""),
            location=str(data.get("location", "") or ""),
            character_count=int(data.get("character_count", 0) or 0),
            character_locks=locks,
            spatial_lock=str(data.get("spatial_lock", "") or ""),
            action_timeline=timeline,
            camera_lock=dict(cam) if isinstance(cam, dict) else {},
            performance_lock=[str(x) for x in (data.get("performance_lock") or [])],
            forbidden=[str(x) for x in (data.get("forbidden") or [])],
            shot_sizes_seen=[str(x) for x in (data.get("shot_sizes_seen") or [])],
        )


# ---------------------------------------------------------------------------
# 工具：景别 / 禁止动作抽取
# ---------------------------------------------------------------------------

def extract_shot_sizes(text: str) -> List[str]:
    """扫描文本中的景别词，去重保序（按出现顺序）。

    长词优先交替正则在原文逐位匹配、命中即消费该段 →
    「中近景」不会同时误报「近景/中景」；「中景…近景」独立出现则各报一次。
    """
    if not text:
        return []
    out: List[str] = []
    seen: set = set()
    for m in _SHOT_SIZE_RE.finditer(text):
        kw = m.group(0)
        if kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


def extract_performance_lock(text: str, characters: List[str]) -> List[str]:
    """原文否定句 → 禁止动作（Performance Lock）。

    抽取规则（纯规则，零 LLM）：
    - 「X 没有/未/不曾/未曾/并未 + 动作」→「X不得{动作}」（否定完整动作句）。
    - 「X 不 + 动作白名单动词」→「X不得{动作}」（防「冷漠」类状态词误伤）。
    - 「X 保持静止/一动不动…」→「X保持静止，不得移动」。
    - 动作主体 = 否定标记/静止短语 **前最近** 出现的本镜出场角色（同句多角色时
      不会张冠李戴，如「顾琰宸…林薇薇没有伸手去拿」只锁林薇薇）。

    返回去重保序列表；无命中 → 空列表。
    """
    if not text or not characters:
        return []
    chars: List[str] = [c for c in characters if c]
    out: List[str] = []
    seen: set = set()
    for seg in re.split(r"[。；；\n]", text):
        if not seg.strip():
            continue
        # ---- 否定动作（nearest-preceding subject）----
        for m in _NEG_RE.finditer(seg):
            marker = m.group("marker")
            verb = m.group("verb").strip()
            if not verb:
                continue
            if marker == "不" and not verb.startswith(_NO_ACTION_VERBS):
                continue
            subject = _nearest_preceding_char(seg, m.start(), chars)
            if not subject:
                continue
            item = f"{subject}不得{verb}"
            if item not in seen:
                seen.add(item)
                out.append(item)
        # ---- 保持静止类（nearest-preceding subject）----
        for hint in _STILL_HINTS:
            idx = seg.find(hint)
            while idx != -1:
                subject = _nearest_preceding_char(seg, idx, chars)
                if subject:
                    item = f"{subject}保持{_still_cn(hint)}，不得移动"
                    if item not in seen:
                        seen.add(item)
                        out.append(item)
                idx = seg.find(hint, idx + 1)
    return out


def _nearest_preceding_char(seg: str, pos: int, chars: List[str]) -> str:
    """返回 seg 中 pos 之前最近出现的角色名（含跨标点）；无则空串。"""
    head = seg[:pos]
    best: str = ""
    best_pos = -1
    for ch in chars:
        i = head.rfind(ch)
        if i >= 0 and i > best_pos:
            best_pos = i
            best = ch
    return best


def _still_cn(hint: str) -> str:
    """保持静止类短语 → 动作节选（「保持静止」→「静止」，其余原样）。"""
    return hint[2:] if hint.startswith("保持") else hint


# ---------------------------------------------------------------------------
# ShotPlan 构建
# ---------------------------------------------------------------------------

def build_shot_plan(
    intent: Any,
    *,
    appearance_map: Optional[Dict[str, str]] = None,
    role_map: Optional[Dict[str, str]] = None,
) -> ShotPlan:
    """DirectorIntent（dataclass 或 dict）→ ShotPlan（纯规则派生）。

    appearance_map：{角色名: 外观描述}（P2 Qwen 角色外观档案 / 规则兜底）。
    role_map：{角色名: 角色定位}（可选）。

    ⛔ 只读 intent 字段，绝不改写；source_text/retained_facts 保持逐字。
    """
    d = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent or {})
    shot_id = str(d.get("shot_id", "") or "")
    scene_id = str(d.get("scene_id", "") or "")
    location = str(d.get("location", "") or "").strip()

    # ---- Character Lock + Character Count ----
    raw_chars = [str(c).strip() for c in (d.get("characters") or []) if str(c).strip()]
    chars: List[str] = []
    for c in raw_chars:
        if c and c not in chars:
            chars.append(c)
    appearance_map = appearance_map or {}
    role_map = role_map or {}
    locks = [
        CharacterLock(
            name=c,
            appearance=str(appearance_map.get(c, "") or "").strip(),
            role=str(role_map.get(c, "") or "").strip(),
        )
        for c in chars
    ]

    # ---- Spatial Lock（复用 v1.0 spatial_continuity 双语块）----
    spatial_lock = ""
    cont = d.get("continuity") or {}
    if isinstance(cont, dict):
        sp = cont.get("spatial") or {}
        if isinstance(sp, dict):
            spatial_lock = str(sp.get("block", "") or "").strip()

    # ---- Action Timeline（intent.action 保序编号；core_action 标 primary）----
    actions = [str(a).strip() for a in (d.get("action") or []) if str(a).strip()]
    core_action = str(d.get("core_action", "") or "").strip()
    timeline: List[ActionItem] = []
    for a in actions:
        is_primary = bool(
            core_action and (a == core_action or core_action in a or a in core_action)
        )
        timeline.append(ActionItem(text=a, is_primary=is_primary))
    if not timeline and core_action:
        timeline.append(ActionItem(text=core_action, is_primary=True))
    # 防多镜核心动作并列：只保留第一条 primary 为真（其余降级），
    # 交由 constraint_checker 报告 multi_primary_action。
    primary_seen = False
    for item in timeline:
        if item.is_primary:
            if primary_seen:
                item.is_primary = False
            else:
                primary_seen = True

    # ---- Camera Lock（一镜一摄影机状态）----
    cam_pos = str(d.get("camera_position", "") or "").strip()
    move = str(d.get("movement", "") or "").strip()
    camera_lock: Dict[str, str] = {
        "shot_size": cam_pos,
        "movement": move,
        "cn": (cam_pos + move) if (cam_pos or move) else "",
    }

    # ---- Performance Lock + Forbidden ----
    source = " ".join(filter(None, [
        str(d.get("user_original_intent", "") or "").strip(),
        str(d.get("composition", "") or "").strip(),
    ]))
    perf = extract_performance_lock(source, chars)
    forbidden = [
        "画面中不得出现未列出的角色",
        "每个角色不得重复出现",
        "不得中途切换景别或机位",
        "场景不得改变",
    ]
    forbidden.extend(perf)

    # ---- 景别冲突检测 ----
    shot_sizes = extract_shot_sizes(source)

    return ShotPlan(
        shot_id=shot_id,
        scene_id=scene_id,
        location=location,
        character_count=len(chars),
        character_locks=locks,
        spatial_lock=spatial_lock,
        action_timeline=timeline,
        camera_lock=camera_lock,
        performance_lock=list(perf),
        forbidden=forbidden,
        shot_sizes_seen=shot_sizes,
    )


__all__ = [
    "ShotPlan",
    "CharacterLock",
    "ActionItem",
    "build_shot_plan",
    "extract_shot_sizes",
    "extract_performance_lock",
]
