#!/usr/bin/env python3
"""V1.7 Phase 1 · Commit 2 · script_analyzer（Qwen 语义理解补全，不决定镜头结构）。

职责（V17_PLAN §4 / P0-A，用户 2026-08-11 拍板）：
1. 对 Commit 1 规则拆好的 ProductionPlan（script_parser 产物）做**语义理解补全**：
   - characters[].role（规则只到名字，Qwen 补角色定位）
   - props[]（规则 Phase 1 不猜道具，Qwen 补）
   - actions[]（规则留空，Qwen 补具体动作）
   - emotion（规则留空，Qwen 补镜头主导情绪）
   - dialogue[].speaker 回填（规则对复杂句 speaker 留空，Qwen 确定性高才回填）
   - visual_intent（视觉意图草稿，**≠ content.visual**，见下）
2. **Qwen 不决定镜头数量/边界**（V17_PLAN §4 核心原则，全期不变）：
   镜头边界由 script_parser 规则锁死，Qwen 只补语义字段。shot_id / source_text /
   duration_sec / 镜头顺序 全部保持不变。
3. **视觉意图链路**（V17_PLAN §4 架构原则）：visual_intent ≠ SPA Shot.content.visual。
   visual_intent 是 Phase 1 草稿，必须经 Phase 3 Generation Planning + Official H3 Skill
   → H3 Prompt Builder → content.visual。中间任何一步未实现，visual_intent 不得直进 content.visual。
4. **Qwen 分析策略**（用户拍板⑦）：Scene 批量为主（上下文连贯），失败 fallback 到单 shot
   `analyze_shot()`；单一公共入口 `analyze_scene()`。
5. **⛔ 显存完全不动**（用户拍板⑧）：本模块**只 `with backend.session()`**，不启动/卸载 Ollama、
   不查 GPU registry、不改 keep_alive —— 全部交给 TextSession（text_backends.py §8/§8.5）。
   只做一次 `text_backend_active()` 守卫检查（进程内互斥锁状态，非 GPU 管理），
   确保 llm 来自 `with backend.session():` 内部，杜绝绕过显存安全门直接调 Qwen。
6. **强 JSON 兜底**：依赖 TextBackend.analyze_json 的容错解析 + RETRY_HINT 重试；
   JSON 字段类型非法 / 缺失 → 规范化后**保留规则值**（不覆盖、不崩溃）；
   Scene 批量失败 → 逐镜 analyze_shot；analyze_shot 也失败 → 保留规则 Shot。

设计原则：规则确定结构（Commit 1），Qwen 补语义（Commit 2），人工最终审核（SPA review）。
Qwen 输出只是「增强草稿」，任何时候失败都不影响 Commit 1 规则结果的完整性。

运行方式（同 text_backends / script_parser 测试，无第三方依赖）：
    python3 director/tests/test_script_analyzer.py
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .production_plan import (
    AssetRequirement,
    Character,
    Dialogue,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    Prop,
    Scene,
    Shot,
    VoiceType,
    VisualElement,
    asset_requirement_for_type,
    new_entity_id,
)
from .text_backends import (
    TextBackend,
    create_default_text_backend,
    text_backend_active,
)
from .entity_cleanse import is_invalid_entity_name

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.script_analyzer")

# 单镜 source_text 进 prompt 的截断上限（防长剧本超 token；不影响落盘的 source_text 原文）
_SOURCE_TEXT_MAX = 600
# Qwen 输出字段统一默认值
_EMPTY_STR = ""


# ---------------------------------------------------------------------------
# Prompt 模板（强 JSON，只补语义不改结构）
# ---------------------------------------------------------------------------

_SCENE_ANALYZE_TEMPLATE = """你是专业剧本分镜语义分析助手。下面是同一场景的 {n} 个镜头，已由规则切分，镜头边界固定、不可合并或拆分。

场景信息：
- 场景ID：{scene_id}
- 标题：{title}
- 地点：{location_name}
- 时间：{time}
- 天气：{weather}

镜头列表（每镜原始文本，编号即镜头序号）：
{shots_block}

请对每个镜头做语义理解，分两个明确的任务，只输出一个 JSON 对象，不要输出任何其他文字、代码块或注释。JSON 结构：
{{
  "shots": [
    {{
      "index": 1,
      "script_facts": {{
        "characters": [{{"name": "角色名", "role": "一句话角色定位"}}],
        "locations": [{{"name": "地点名"}}],
        "props": [{{"name": "关键道具名"}}],
        "costumes": [{{"name": "服装穿戴名"}}],
        "entities": [
          {{"name": "实体名", "type": "character", "confidence": 0.95}}
        ]
      }},
      "visual_interpretation": {{
        "visual_elements": [{{"name": "晨光", "type": "effect", "confidence": 0.8}}],
        "actions": ["动作1", "动作2"],
        "emotion": "镜头主导情绪"
      }},
      "dialogue": [{{"speaker": "说话者名", "text": "台词原文"}}],
      "visual_intent": "一句话视觉意图草稿（构图/机位/主体动作，中文）"
    }}
  ],
  "character_profiles": [
    {{"name": "角色名", "appearance": "该角色本场景外观描述"}}
  ]
}}

【Task A · Script Fact Extraction —— 剧本事实，只允许剧本明确出现的实体】
script_facts.entities 是「需要资产参考图」的剧本实体清单，type 只能是：
character（角色）、location（地点/空间）、prop（可移动道具）、costume（服装穿戴）、
architecture（固定建筑构件）、vehicle（载具）、creature（生物）。
【铁律】只从镜头原文提取剧本明确出现的实体，绝不为了画面完整性自行创造：
- 「镜头 1」「镜头一」「第 1 镜」这类镜头编号不是实体；原文没写出的物品不算实体。
- 【实体名 = 纯名词】实体名必须是不含动作动词的纯名词短语——「柳如烟持灯退到
  柜台边」的实体是「灯」，不是「灯退到柜」；动作短语（退到/走向/扑来/拿起）不得
  拼进实体名，动作归入 actions，方位归入 visual_intent。
- 【资产抽取 ≠ 视觉描述】环境/氛围/光线/天气/烟尘类词（晨光、山雾、寒气、雨丝、
  暖黄灯火、尘埃、风、阴影、烛火、倒影、波纹、山色、暮色）一律【不得】放进
  script_facts.entities —— 它们是视觉描述，必须放入 visual_interpretation.visual_elements。

【Task B · Visual Interpretation —— 视觉解读，允许合理视觉推断】
visual_interpretation.visual_elements 是「只进 Prompt 的视觉元素」：环境/氛围/光线/
天气/烟尘/动态效果类（晨光、山雾、寒气、雨丝、暖黄灯火、尘埃、风、阴影、烛火、
倒影、波纹、衣袂摆动等），以及你为画面合理性推断出的补充视觉。visual_elements 的
type 只能是：environment（自然/大环境）、effect（光/雾/烟/动作特效）、unknown（其他）。
visual_interpretation.actions 用具体动作动词短语（2-5 条）；【每条只含一个动作】——
「捏起支票，缓缓抬到眼前」必须拆成「捏起支票」「缓缓抬到眼前」两条，一镜可拆成多个
动作序列；衣物/配饰的飘动、摆动等动作描述归入 actions，不作为 costume 实体。

要求：
- 只补全语义，不得改动镜头数量、顺序、台词原文。
- characters.name 用剧中规范人名；不确定角色定位时 role 留空字符串。
- 每个 script_facts.entities 必须给出 type（上面枚举之一）与 confidence（0~1，
  剧本明确出现建议 >=0.85）。
- 同一实体在不同镜头重复出现时 name 必须完全一致（规范化人名/物名）。
- dialogue 只列本镜头出现的台词；说话者不确定时 speaker 留空字符串。

【Task C · Character Appearance（可选顶层字段）】character_profiles 是**场景级**角色外观
档案，供「外观锁定」约束使用，只列本场景实际出场角色：
- appearance 用 1-2 句中文描述角色的**服装穿戴、体型、显著外观特征**；
- 【铁律】只从镜头原文 + 角色定位（role）做合理推断：原文明确写出的服装/配饰必须照写；
  原文未写、但可从身份/职业/时代合理推出的整体形象（如「剑客」「掌柜」）允许概括；
  **绝不自行编造原文没有的具体服装款式、颜色、花纹**（宁可留空，不得臆造）；
- 不确定或无法从原文推出时 appearance 留空字符串；脚本完全没提服装的角色也留空。"""

_SHOT_ANALYZE_TEMPLATE = """你是专业剧本分镜语义分析助手。下面是一个镜头（原始文本），镜头边界固定，不得改动。

场景信息：{scene_ctx}
镜头原始文本：
{source_text}

请对该镜头做语义理解，分两个明确的任务，只输出一个 JSON 对象（不要输出任何其他文字、代码块或注释）：
{{
  "script_facts": {{
    "characters": [{{"name": "角色名", "role": "一句话角色定位"}}],
    "locations": [{{"name": "地点名"}}],
    "props": [{{"name": "关键道具名"}}],
    "costumes": [{{"name": "服装穿戴名"}}],
    "entities": [
      {{"name": "实体名", "type": "character", "confidence": 0.95}}
    ]
  }},
  "visual_interpretation": {{
    "visual_elements": [{{"name": "晨光", "type": "effect", "confidence": 0.8}}],
    "actions": ["动作1", "动作2"],
    "emotion": "镜头主导情绪"
  }},
  "dialogue": [{{"speaker": "说话者名", "text": "台词原文"}}],
  "visual_intent": "一句话视觉意图草稿（构图/机位/主体动作，中文）"
}}

【Task A · Script Fact Extraction】script_facts.entities 是「需要资产参考图」的剧本实体，
type 只能是：character、location、prop、costume、architecture、vehicle、creature。
【铁律】只从镜头原文提取剧本明确出现的实体，绝不自行创造；「镜头 1」「镜头一」不是实体；
【实体名 = 纯名词】实体名必须是不含动作动词的纯名词短语——「柳如烟持灯退到柜台边」的
实体是「灯」，不是「灯退到柜」；动作短语（退到/走向/扑来/拿起）不得拼进实体名，
动作归入 actions，方位归入 visual_intent；
【资产抽取 ≠ 视觉描述】环境/氛围/光线/天气/烟尘类词（晨光、山雾、寒气、雨丝、暖黄灯火、
尘埃、风、阴影、烛火、倒影、波纹）不得放进 entities，必须放入 visual_elements。

【Task B · Visual Interpretation】visual_interpretation.visual_elements 是「只进 Prompt 的
视觉元素」：环境/氛围/光线/天气/烟尘/动态效果类，允许合理视觉推断；type 只能是
environment、effect、unknown。actions 用具体动作动词短语（2-5 条）；【每条只含一个动作】——
「捏起支票，缓缓抬到眼前」必须拆成「捏起支票」「缓缓抬到眼前」两条；
衣物/配饰的飘动、摆动归入 actions，不作为 costume 实体。

要求：只补全语义；角色名用规范人名；不确定角色定位 role 留空；说话者不确定 speaker 留空；
不得改动台词原文。每个 script_facts.entities 给出 type（上述枚举）与 confidence
（0~1，剧本明确出现建议 >=0.85）。"""


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

def _bounded_text(text: str, max_len: int = _SOURCE_TEXT_MAX) -> str:
    """超长 source_text 截断（仅用于 prompt，不改变落盘原文）。"""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _scene_ctx_desc(scene: Scene) -> str:
    parts: List[str] = []
    if scene.title:
        parts.append(f"标题：{scene.title}")
    if scene.location_name:
        parts.append(f"地点：{scene.location_name}")
    if scene.time:
        parts.append(f"时间：{scene.time}")
    if scene.weather:
        parts.append(f"天气：{scene.weather}")
    if not parts:
        parts.append(f"场景ID：{scene.scene_id}")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# JSON 规范化（Qwen 输出 → 合法结构；非法一律降级，不覆盖规则值）
# ---------------------------------------------------------------------------

def _clean_str(v: Any, default: str = _EMPTY_STR) -> str:
    return v.strip() if isinstance(v, str) else default


def _clean_str_list(v: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(v, list):
        return out
    for item in v:
        if isinstance(item, str):
            s = item.strip()
        elif isinstance(item, dict):
            s = _clean_str(item.get("text") or item.get("name"))
        else:
            s = _clean_str(str(item))
        if s and s not in out:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# P2 动作拆分细化：Qwen 常把多动作塞进一条（「捏起支票，抬到眼前」），
# 一镜一核心动作（core_action）需要「一条一动作」的细粒度输入。
# 纯规则兜底：只拆不并，标点 + 连接词双信号；core_action._fold 再折叠成过程链。
# ---------------------------------------------------------------------------

# 动作连接词（命中且左右都有内容才拆）。
_ACTION_CONJUNCTIONS: tuple[str, ...] = ("然后", "接着", "而后", "随即", "并", "又")
# 动作标点分隔（全角/半角）。
_ACTION_SEP_RE = re.compile(r"[，,、；;。.!！?？]")
# 拆后丢弃的碎片最小长度（少于 2 字视为修饰/噪音）。
_ACTION_MIN_LEN = 2


def _split_conj(text: str) -> List[str]:
    """按连接词拆分（递归右半）；无连接词 → 原文一条。"""
    for conj in _ACTION_CONJUNCTIONS:
        if conj in text:
            left, _, right = text.partition(conj)
            if left.strip() and right.strip():
                return [left.strip()] + _split_conj(right.strip())
    return [text]


def _split_actions(actions: List[str]) -> List[str]:
    """把粗粒度动作条目拆成「一条一动作」（P2，纯规则零 LLM）。

    - 先按连接词（并/然后/接着/…）切，再按标点（，、；。！）切；
    - 空碎片 / 单字碎片 / 重复条目丢弃（保序去重）；
    - 已细粒度的条目幂等（Qwen 合规输出不受影响）。
    """
    out: List[str] = []
    seen = set()
    for a in actions:
        a = str(a).strip().strip(" ，,、；;。")
        if not a:
            continue
        for piece in _ACTION_SEP_RE.split(a):
            piece = piece.strip().strip(" ，,、；;。")
            if not piece:
                continue
            for conj_piece in _split_conj(piece):
                conj_piece = conj_piece.strip().strip(" ，,、；;。")
                if len(conj_piece) < _ACTION_MIN_LEN:
                    continue
                if conj_piece not in seen:
                    seen.add(conj_piece)
                    out.append(conj_piece)
    return out


def _clean_char_like(v: Any) -> List[Dict[str, str]]:
    """characters / props 共用：输出 [{name, role, appearance}]；props 只用 name。

    appearance 为 P2 角色外观档案扩展（Qwen 单镜角色可选携带，缺省空串）。
    """
    out: List[Dict[str, str]] = []
    if not isinstance(v, list):
        return out
    for item in v:
        if isinstance(item, dict):
            name = _clean_str(item.get("name"))
            role = _clean_str(item.get("role"))
            appearance = _clean_str(item.get("appearance"))
            if name and name not in [c["name"] for c in out]:
                out.append({"name": name, "role": role, "appearance": appearance})
        elif isinstance(item, str):
            name = item.strip()
            if name and name not in [c["name"] for c in out]:
                out.append({"name": name, "role": "", "appearance": ""})
    return out


def _clean_character_profiles(v: Any) -> Dict[str, str]:
    """Qwen 场景级角色外观档案规范化：{canonical_name: appearance}。

    P2（PROMPT_COMPILER_V1 §4-P2）：场景批量分析的顶层可选字段 character_profiles。
    规则：
    - name/appearance 任一为空 → 丢弃（只留确定档案，不臆造）；
    - 键 canonical 化（trim+lowercase）去重，先到先得；
    - 返回原始 name 作值不保留 —— 键恒为 canonical，供 _merge_characters 匹配。
    """
    out: Dict[str, str] = {}
    if not isinstance(v, list):
        return out
    for item in v:
        if not isinstance(item, dict):
            continue
        name = _clean_str(item.get("name"))
        app = _clean_str(item.get("appearance"))
        if not name or not app:
            continue
        key = name.strip().lower()
        if key not in out:
            out[key] = app
    return out


def _clean_dialogues(v: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(v, list):
        return out
    for item in v:
        if isinstance(item, dict):
            speaker = _clean_str(item.get("speaker"))
            text = _clean_str(item.get("text"))
            if text and text not in [d["text"] for d in out]:
                d: Dict[str, str] = {"speaker": speaker, "text": text}
                # 声音导演层字段（2026-08-15 扩展）：Qwen 识别到则透传，未识别不填。
                # type 必须过 VoiceType.normalize（非法/空 → character_dialogue）。
                if item.get("type") is not None:
                    d["type"] = VoiceType.normalize(_clean_str(item.get("type")))
                for key in ("voice_id", "emotion", "delivery"):
                    if item.get(key) is not None:
                        d[key] = _clean_str(item.get(key))
                out.append(d)
        elif isinstance(item, str):
            text = item.strip()
            if text and text not in [d["text"] for d in out]:
                out.append({"speaker": "", "text": text})
    return out


# ---------------------------------------------------------------------------
# 合并：Qwen 补全 → 规则 Shot（规则优先；Qwen 只补空、回填、追加新实体）
# ---------------------------------------------------------------------------

def _merge_characters(
    rule_chars: List[Character],
    qwen_chars: List[Dict[str, str]],
    appearance_map: Optional[Dict[str, str]] = None,
) -> List[Character]:
    """规则角色优先；Qwen 补 role/appearance + 追加新角色（canonical 去重：trim+lowercase）。

    P0-3（用户 2026-08-11 拍板）：规则层/Qwen 都可能把镜头标记「镜头一」、
    称谓「客官/老板娘」塞进 characters —— 这里统一过滤伪实体（shot_marker/appellation），
    保证 shot.characters 从源头干净（SPA 真实页面角色 11 的根治点之一）。

    P2（PROMPT_COMPILER_V1 §4-P2）：角色外观合并优先级
        规则 Character.appearance > Qwen 单镜 appearance > appearance_map（场景级 Qwen 档案）。
    只补空、绝不覆盖已有多源值；map 键按 canonical 名（trim+lower）匹配。
    """
    out: List[Character] = []
    seen = set()
    for c in rule_chars:
        nm = (c.name or "").strip()
        if not nm or is_invalid_entity_name(nm):
            continue
        out.append(Character(name=nm, role=c.role, appearance=c.appearance))
        seen.add(nm.strip().lower())
    for qc in qwen_chars:
        key = qc["name"].strip().lower()
        if not key or is_invalid_entity_name(qc["name"]):
            continue
        if key in seen:
            for c in out:
                if c.name.strip().lower() == key:
                    if not c.role and qc.get("role"):
                        c.role = qc["role"]
                    if not c.appearance and qc.get("appearance"):
                        c.appearance = qc["appearance"]
                    break
        else:
            seen.add(key)
            out.append(Character(name=qc["name"], role=qc.get("role", ""),
                                 appearance=qc.get("appearance", "")))
    # 场景级 Qwen 档案兜底：只补空，不覆盖
    if appearance_map:
        for c in out:
            if not c.appearance:
                cand = appearance_map.get(c.name.strip().lower())
                if not cand:
                    # map 键可能保留原始大小写，二次尝试精确键
                    cand = appearance_map.get(c.name)
                if cand:
                    c.appearance = cand
    return out


def _merge_dialogues(rule_dlg: List[Dialogue], qwen_dlg: List[Dict[str, str]]) -> List[Dialogue]:
    """规则对白优先保序；Qwen 相同台词回填 speaker、补充新台词（按 text 去重）。"""
    out: List[Dialogue] = [Dialogue(speaker=d.speaker, text=d.text) for d in rule_dlg]
    seen_text = {d.text for d in out}
    for qd in qwen_dlg:
        if qd["text"] in seen_text:
            for d in out:
                if d.text == qd["text"] and not d.speaker and qd.get("speaker"):
                    d.speaker = qd["speaker"]
                    break
        else:
            seen_text.add(qd["text"])
            out.append(Dialogue(speaker=qd.get("speaker", ""), text=qd["text"]))
    return out


def _merge_props(rule_props: List[Prop], qwen_props: List[Dict[str, str]]) -> List[Prop]:
    """规则道具优先保序；Qwen 追加新道具（按 name 去重，trim）。"""
    out: List[Prop] = [Prop(name=p.name) for p in rule_props]
    seen = {p.name for p in out}
    for qp in qwen_props:
        name = qp["name"]
        if name and name not in seen:
            seen.add(name)
            out.append(Prop(name=name))
    return out


def _clean_entities(v: Any) -> List[Dict[str, Any]]:
    """Qwen entities 规范化：{name, type, source, confidence}。

    非法 type/source 落默认值，confidence 钳制 0~1，shot 内按 (name,type) 去重。
    注意：这里只做「结构规范化」，不做剧本外实体过滤 —— 过滤交给 entity_cleanse 纯规则层。
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(v, list):
        return out
    seen = set()
    for item in v:
        if not isinstance(item, dict):
            continue
        name = _clean_str(item.get("name"))
        if not name:
            continue
        etype = _clean_str(item.get("type"), EntityType.UNKNOWN)
        if etype not in EntityType.ALL:
            etype = EntityType.UNKNOWN
        source = _clean_str(item.get("source"), EntitySource.SCRIPT)
        if source not in EntitySource.ALL:
            source = EntitySource.SCRIPT
        try:
            conf = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = min(1.0, max(0.0, conf))
        key = (name.strip().lower(), etype)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "type": etype, "source": source, "confidence": conf})
    return out


def _copy_entity(e: Entity) -> Entity:
    return Entity(
        entity_id=e.entity_id,
        name=e.name,
        type=e.type,
        source=e.source,
        confidence=e.confidence,
        asset_requirement=e.asset_requirement or AssetRequirement.REQUIRED,
        aliases=list(e.aliases),
    )


def _merge_entities(
    rule_entities: List[Entity], qwen_entities: List[Dict[str, Any]],
    used_ids: Optional[set] = None,
) -> List[Entity]:
    """规则实体优先保序（保留原 entity_id）；Qwen 追加新实体（新 id）。

    去重键 = (name trim+lower, type)。Qwen 重复/同键实体不再追加。
    used_ids：本 plan 内已占用的实体 id（跨镜去重），缺省取当前镜规则实体 id，
    保证新 id 不与既有实体撞车。
    """
    out: List[Entity] = [_copy_entity(e) for e in rule_entities]
    used: set = set(used_ids or []) | {
        e.entity_id for e in rule_entities if e.entity_id
    }
    seen = {(e.name.strip().lower(), e.type) for e in out if e.name}
    for qe in qwen_entities:
        key = (qe["name"].strip().lower(), qe["type"])
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(
            Entity(
                entity_id=new_entity_id(used),
                name=qe["name"],
                type=qe["type"],
                source=qe["source"],
                confidence=qe["confidence"],
                asset_requirement=asset_requirement_for_type(qe["type"]),
                aliases=[],
            )
        )
    return out


def _nested_or_flat(data: Dict[str, Any], nested_key: str, flat_key: str) -> Any:
    """Task A/B 嵌套结构优先（script_facts / visual_interpretation），回退旧扁平结构。

    兼容旧 prompt 输出与既有测试；新 prompt 输出嵌套结构时读嵌套，否则读扁平字段。
    """
    obj = data.get(nested_key)
    if isinstance(obj, dict) and flat_key in obj:
        return obj.get(flat_key)
    return data.get(flat_key)


def _clean_visual_elements(v: Any) -> List[Dict[str, Any]]:
    """Qwen visual_elements 规范化：{name, type, confidence}。

    type 非法落 UNKNOWN，confidence 钳制 0~1，shot 内按 name 去重。
    注意：visual_elements 是「只进 Prompt 的视觉元素」，永不进资产匹配。
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(v, list):
        return out
    seen = set()
    for item in v:
        if not isinstance(item, dict):
            continue
        name = _clean_str(item.get("name"))
        if not name:
            continue
        etype = _clean_str(item.get("type"), EntityType.UNKNOWN)
        if etype not in EntityType.ALL:
            etype = EntityType.UNKNOWN
        try:
            conf = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = min(1.0, max(0.0, conf))
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "type": etype, "confidence": conf})
    return out


def _merge_visual_elements(
    rule_ves: List[VisualElement], qwen_ves: List[Dict[str, Any]]
) -> List[VisualElement]:
    """规则视觉元素优先保序；Qwen 追加新视觉元素（按 name trim+lower 去重）。

    规则 Phase 1 不产 visual_elements（恒空），Qwen 补全为主。
    """
    out: List[VisualElement] = [
        VisualElement(name=v.name, type=v.type, confidence=v.confidence)
        for v in rule_ves
    ]
    seen = {v.name.strip().lower() for v in out if v.name}
    for qv in qwen_ves:
        key = qv["name"].strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            VisualElement(
                name=qv["name"], type=qv["type"], confidence=qv["confidence"]
            )
        )
    return out


def _merge_shot(
    shot: Shot,
    data: Optional[Dict[str, Any]],
    used_ids: Optional[set] = None,
    appearance_map: Optional[Dict[str, str]] = None,
) -> Shot:
    """把 Qwen 单镜语义补全合并进规则 Shot。data 为 None/非法时原样返回。

    appearance_map：P2 场景级角色外观档案 {canonical_name: appearance}，
    合并时只补空（规则 Character.appearance / Qwen 单镜 appearance 优先）。
    """
    if not isinstance(data, dict):
        return shot
    # Task A/B 嵌套优先（script_facts / visual_interpretation），回退旧扁平结构
    qwen_chars = _clean_char_like(_nested_or_flat(data, "script_facts", "characters"))
    qwen_props = _clean_char_like(_nested_or_flat(data, "script_facts", "props"))
    qwen_entities = _clean_entities(_nested_or_flat(data, "script_facts", "entities"))
    qwen_ves = _clean_visual_elements(
        _nested_or_flat(data, "visual_interpretation", "visual_elements")
    )
    qwen_actions = _clean_str_list(_nested_or_flat(data, "visual_interpretation", "actions"))
    qwen_emotion = _clean_str(_nested_or_flat(data, "visual_interpretation", "emotion"))
    qwen_dlg = _clean_dialogues(data.get("dialogue"))
    qwen_vi = _clean_str(data.get("visual_intent"))

    chars = _merge_characters(shot.characters, qwen_chars, appearance_map=appearance_map)
    props = _merge_props(shot.props, qwen_props)
    entities = _merge_entities(shot.entities, qwen_entities, used_ids=used_ids)
    visual_elements = _merge_visual_elements(shot.visual_elements, qwen_ves)
    # 规则优先：Qwen 只补空（规则 Phase 1 的 actions/emotion/visual_intent 恒为空，
    # 未来规则若填值则不被覆盖）
    # P2 动作拆分细化：规则/Qwen 动作统一过 _split_actions（一动作一条，纯规则兜底）。
    actions = _split_actions(list(shot.actions) or qwen_actions)
    emotion = shot.emotion or qwen_emotion
    dialogues = _merge_dialogues(shot.dialogue, qwen_dlg)
    visual_intent = shot.visual_intent or qwen_vi

    merged = Shot(
        shot_id=shot.shot_id,
        source_text=shot.source_text,
        duration_sec=shot.duration_sec,
        characters=chars,
        props=props,
        entities=entities,
        visual_elements=visual_elements,
        actions=actions,
        emotion=emotion,
        dialogue=dialogues,
        visual_intent=visual_intent,
    )
    # P2 心理词预标记（软约束转硬输入）：用合并后的最终字段扫描，命中词落盘
    # shot.psych_tags；build_plan_intents 的 visualize_psych 优先消费该硬输入。
    try:
        from .psych_visualize import tag_psych  # noqa: PLC0415
        merged.psych_tags = tag_psych(merged)
    except Exception:  # pragma: no cover - 预标记失败不影响主链路
        merged.psych_tags = []
    return merged


def _shot_data_for_index(data: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    """从 Scene 批量结果里取第 idx 镜（0-based）。

    策略：只要批量结果里出现任何 index 字段，就**只用 index 精确匹配**（缺失返回 None，
    该镜保留规则值）；只有整批都没有 index 字段时，才按数组顺序兜底（Qwen 严格遵守输出顺序）。
    防止「部分乱序」的批量结果把错位数据误配给别的镜头。
    """
    shots = data.get("shots")
    if not isinstance(shots, list):
        return None
    has_index = any(
        isinstance(s, dict) and isinstance(s.get("index"), int) for s in shots
    )
    if has_index:
        for s in shots:
            if isinstance(s, dict) and s.get("index") == idx + 1:
                return s
        return None
    if idx < len(shots) and isinstance(shots[idx], dict):
        return shots[idx]
    return None


# ---------------------------------------------------------------------------
# 单镜分析（fallback）
# ---------------------------------------------------------------------------

def analyze_shot(
    llm: TextBackend,
    shot: Shot,
    scene: Optional[Scene] = None,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    used_ids: Optional[set] = None,
    appearance_map: Optional[Dict[str, str]] = None,
) -> Shot:
    """单镜语义补全（Scene 批量失败时的 fallback）。失败返回原 Shot（规则兜底）。

    llm 必须是 ``with backend.session():`` 内部激活的后端（公共入口 analyze_scene 内部调用）。
    used_ids：本 plan 内已占用的实体 id 集合（跨镜唯一）。
    appearance_map：P2 场景级角色外观档案（fallback 时场景无顶层档案，通常为 None）。
    """
    scene_ctx = _scene_ctx_desc(scene) if scene is not None else ""
    prompt = _SHOT_ANALYZE_TEMPLATE.format(
        scene_ctx=scene_ctx,
        source_text=_bounded_text(shot.source_text),
    )
    try:
        data = llm.analyze_json(prompt, max_tokens=max_tokens, temperature=temperature)
    except Exception as exc:  # 超时/网络/后端异常 → 规则兜底
        log.warning("analyze_shot %s 调用失败，保留规则值: %s", shot.shot_id, exc)
        return shot
    if not isinstance(data, dict):
        return shot
    return _merge_shot(shot, data, used_ids=used_ids, appearance_map=appearance_map)


# ---------------------------------------------------------------------------
# 单一公共入口：Scene 批量分析（失败 fallback 单镜）
# ---------------------------------------------------------------------------

def analyze_scene(
    llm: TextBackend,
    scene: Scene,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    warnings: Optional[List[str]] = None,
    used_ids: Optional[set] = None,
) -> Scene:
    """Scene 批量语义补全（单一公共入口）。Qwen 不决定镜头结构。

    llm 必须是 ``with backend.session():`` 内部激活的后端（analyze_script 顶层提供；
    外部也可自己 ``with backend.session() as llm:`` 后传入）。

    策略：Scene 批量强 JSON（上下文连贯）→ 失败 fallback 逐镜 analyze_shot
    → 再失败保留规则 Shot。shot_id/source_text/duration_sec 全部不变。
    warnings 列表传入则追加分析失败记录（不改变 validation.status 语义）。
    used_ids：本 plan 内已占用的实体 id 集合（跨镜唯一）。
    """
    if warnings is None:
        warnings = []
    if not scene.shots:
        return scene

    # 显存安全守卫：只查进程内互斥锁状态（非 GPU 管理），确保 llm 在 session 内
    if not text_backend_active():
        raise RuntimeError(
            "script_analyzer.analyze_scene 必须在 with backend.session(): 内部调用，"
            "否则会绕过显存安全门（V17_PLAN §8）。请用 analyze_script() 或自行包 session。"
        )

    shots_block = "\n".join(
        f"[{i + 1}] {_bounded_text(s.source_text)}" for i, s in enumerate(scene.shots)
    )
    prompt = _SCENE_ANALYZE_TEMPLATE.format(
        n=len(scene.shots),
        scene_id=scene.scene_id,
        title=scene.title,
        location_name=scene.location_name,
        time=scene.time,
        weather=scene.weather,
        shots_block=shots_block,
    )

    new_shots: List[Shot] = []
    batch_ok = False
    try:
        data = llm.analyze_json(prompt, max_tokens=max_tokens, temperature=temperature)
    except Exception as exc:
        data = None
        warnings.append(f"场景 {scene.scene_id} Qwen 批量分析调用失败: {exc}")

    # P2：场景级可选顶层字段 character_profiles → {canonical_name: appearance}
    appearance_map: Dict[str, str] = {}
    if isinstance(data, dict):
        appearance_map = _clean_character_profiles(data.get("character_profiles"))

    if isinstance(data, dict) and isinstance(data.get("shots"), list):
        batch_ok = True
        for i, shot in enumerate(scene.shots):
            new_shots.append(_merge_shot(shot, _shot_data_for_index(data, i),
                                         used_ids=used_ids,
                                         appearance_map=appearance_map))
    else:
        warnings.append(f"场景 {scene.scene_id} Qwen 批量分析未返回合法 JSON，逐镜兜底")

    if not batch_ok:
        # 逐镜兜底：每次调用都独立 try 捕获，任何失败保留规则 Shot
        fallback_tokens = max(256, max_tokens // 2)
        for shot in scene.shots:
            new_shots.append(
                analyze_shot(llm, shot, scene, max_tokens=fallback_tokens,
                             temperature=temperature, used_ids=used_ids,
                             appearance_map=appearance_map)
            )

    out = Scene(
        scene_id=scene.scene_id,
        title=scene.title,
        location_name=scene.location_name,
        time=scene.time,
        weather=scene.weather,
        shots=new_shots,
    )
    return out


# ---------------------------------------------------------------------------
# 顶层入口：整本剧本（一个 session 遍历全部 Scene）
# ---------------------------------------------------------------------------

def _collect_entity_ids(plan: ProductionPlan) -> set:
    """收集 plan 内全部已占用实体 id（跨镜去重；用于 new_entity_id 防撞车）。"""
    used: set = set()
    for sc in plan.scenes or []:
        for sh in sc.shots or []:
            for e in sh.entities or []:
                if e.entity_id:
                    used.add(e.entity_id)
    return used


def _fallback_appearance(c: Character, shot: Shot) -> str:
    """P2 规则兜底：Qwen 未提供外观时用 costumes + role 拼装（PROMPT_COMPILER_V1 §4-P2）。

    铁律=只从原文+合理外观推断，绝不自行创造服装：costumes 是剧本明确实体（EntityType
    .COSTUME），role 是剧本角色定位。只在「单角色镜头」时才用该镜 costumes 配对（避免
    多角色时服装张冠李戴）；两者皆空 → 返回空串（约束块只锁名字+数量，不臆造外观）。
    """
    role = (c.role or "").strip()
    shot_chars = [x for x in shot.characters if (x.name or "").strip()]
    costume_names = [
        e.name.strip() for e in shot.entities
        if e.type == EntityType.COSTUME and (e.name or "").strip()
    ]
    parts: List[str] = []
    if costume_names and len(shot_chars) == 1:
        parts.append("身着" + "、".join(costume_names))
    if role:
        parts.append(role)
    return "，".join(parts)


def analyze_script(
    plan: ProductionPlan,
    backend: Optional[TextBackend] = None,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> ProductionPlan:
    """整本 ProductionPlan 语义补全。只开一个 TextSession，遍历全部 Scene。

    backend 缺省用 ``create_default_text_backend()``（Ollama qwen3:14b）。
    返回新 ProductionPlan：project/结构不变，Scene/Shot 语义字段被 Qwen 补全；
    分析失败记录追加到 validation.warnings（不改变 status 结构校验语义）。
    """
    if backend is None:
        backend = create_default_text_backend()
    warnings: List[str] = []
    new_scenes: List[Scene] = []
    used_ids = _collect_entity_ids(plan)

    with backend.session() as llm:
        for scene in plan.scenes:
            try:
                new_scenes.append(analyze_scene(llm, scene, max_tokens=max_tokens,
                                                temperature=temperature,
                                                warnings=warnings, used_ids=used_ids))
            except Exception as exc:  # session 级异常：保留规则 Scene，记录 warning
                warnings.append(f"场景 {scene.scene_id} 分析失败，保留规则结果: {exc}")
                new_scenes.append(scene)

    # P2：跨镜聚合角色外观档案（PROMPT_COMPILER_V1 §4-P2）。
    # 优先级：既有 plan.character_profiles（持久化权威）> Qwen 场景档案（setdefault）>
    # 规则兜底（costumes+role 拼装，无歧义才配对）。只补空、绝不覆盖。
    profiles: Dict[str, str] = {}
    for k, v in (plan.character_profiles or {}).items():
        if str(v).strip():
            profiles[str(k)] = str(v).strip()
    for scene in new_scenes:
        for shot in scene.shots:
            for c in shot.characters:
                nm = (c.name or "").strip()
                if not nm:
                    continue
                app = (c.appearance or "").strip()
                if app:
                    profiles.setdefault(nm, app)
    for scene in new_scenes:
        for shot in scene.shots:
            for c in shot.characters:
                nm = (c.name or "").strip()
                if not nm or nm in profiles:
                    continue
                fallback = _fallback_appearance(c, shot)
                if fallback:
                    profiles[nm] = fallback
                    c.appearance = fallback  # 回填 shot 角色，链路上游一致

    out = ProductionPlan(
        project=plan.project,
        scenes=new_scenes,
        validation=plan.validation,
        timeline=list(plan.timeline or []),
        beats=list(plan.beats or []),
        character_profiles=profiles,
    )
    out.validate()  # 机器侧结构校验（status 由结构决定，与 Qwen 成败无关）
    if warnings:
        out.validation.warnings = warnings + out.validation.warnings
    return out


__all__ = [
    "analyze_script",
    "analyze_scene",
    "analyze_shot",
    "_merge_shot",
    "_bounded_text",
    "_split_actions",
]
