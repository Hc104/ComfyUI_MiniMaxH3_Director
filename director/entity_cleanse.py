#!/usr/bin/env python3
"""V1.7 实体抽取加固 · entity_cleanse.py（纯规则清洗层，#356）。

用户 2026-08-11 拍板的三层架构中间层：
    Rule Extractor (script_parser) → Qwen Entity Judge (script_analyzer)
    → Entity Registry (本模块 entity_cleanse) → Asset Matcher (asset_matcher)

职责（与用户评审结论一一对应）：
1. **INVALID_ENTITY_PATTERNS**：丢弃「镜头一」「镜头 1」「shot_1」「scene_2」
   等镜头/场景编号伪实体（Qwen 常见幻觉，把镜头标记当角色/道具）。
2. **类型纠正**：已知角色名 / 场景地点名优先；其余按后缀启发式纠正
   （青衫→costume、檐角→architecture、雾气→effect、木桌→prop）。
3. **canonical 去重**：跨镜头同名实体合并（去空白/小写），aliases 累积原样名。
4. **置信度门控**：source=script 且 confidence>=CONF_ENTER 才进入资产匹配；
   inferred（视觉补充）不进匹配；confidence>=CONF_AUTO_ENTITY 才允许自动绑定。
5. **漏斗统计**：funnel_counts 透出「AI 发现→清洗→进入→auto/pending/none」，
   前端导入弹窗据此展示漏斗。
6. **规则补位**：场景 location_name 若 Qwen 实体遗漏则补位（source=script, conf=1.0），
   保证地点类资产永远进匹配（地点是 Phase 2 绑定 locationId 的关键）。

⛔ 纯规则零显存：本模块不涉及任何模型加载/卸载、GPU 管理、文本后端调用；
   只做字符串/正则/dataclass 操作。与 asset_matcher 同策略。

设计边界：
- 本模块不修改 ProductionPlan（不改 shot.entities），只产出派生注册表；
  修改由 script_analyzer 负责，匹配由 asset_matcher 负责。
- 「实体 ≠ 资产」：实体是剧本里的事物，资产是资产库里的图；本模块只负责
  「哪些实体值得拿去匹配」，不负责「匹配到哪张图」（那是 asset_matcher）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .production_plan import (
    AssetRequirement,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    asset_requirement_for_type,
)
# P0-3b：复用 script_parser 全量动作动词表（公开别名 ACTION_VERBS）与道具动词表
# （PROP_OBJECT_VERBS），保证「动作短语阻断」与规则层道具提取用同一份动词表，
# 不漂移。
from .script_parser import ACTION_VERBS as _ACTION_VERBS
from .script_parser import PROP_OBJECT_VERBS as _PROP_OBJECT_VERBS

# 实体级置信度门控（与 asset_matcher 的资产匹配分不同：这里是「进不进匹配」）
CONF_ENTER = 0.60       # 实体置信度 < 0.60 不进资产匹配
CONF_AUTO_ENTITY = 0.85  # 实体置信度 >= 0.85 才允许自动绑定（否则最高 pending）
CONF_SEED = 1.0          # 规则补位实体置信度
CONF_RULE_CHAR = 0.95    # 规则角色兜底置信度


# ---------------------------------------------------------------------------
# 镜头/场景编号伪实体（Qwen 常见幻觉 + 规则层对白误判），命中即丢弃
# ---------------------------------------------------------------------------
# reason 统一归类（用户 2026-08-11 P0-3 拍板）：
#   - shot_marker：镜头/场景编号（镜头一/shot_1/第一镜…）—— 结构元数据不是实体
#   - appellation：称谓/泛指（老板娘/客官/一个人/有人…）—— 不独立成角色
#   - empty：空白
INVALID_ENTITY_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^镜头\s*[一二三四五六七八九十百\d]+", re.I),   # 镜头一 / 镜头1 / 镜头十
    re.compile(r"^镜头\s*$", re.I),                              # 镜头（裸）
    re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*[镜景场]", re.I),  # 第 3 镜 / 第二场 / 第一镜
    re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*个\s*镜头", re.I),  # 第二个镜头
    re.compile(r"^shot[\s_\-]*\d+", re.I),                       # shot_1 / shot-02
    re.compile(r"^scene[\s_\-]*\d+", re.I),                      # scene_2
    re.compile(r"^景[\s_\-]*\d+", re.I),                         # 景1
    re.compile(r"^\d+[\s]?[镜景场]$", re.I),                     # 1镜 / 2 场
    re.compile(r"^\d+$"),                                        # 纯数字
)

# 称谓/泛指词：Qwen 常见幻觉（把称呼/泛指当独立角色）。命中即不作为角色实体。
# 保守判定：仅当该名未出现在任何 Scene 的规则角色兜底（对白 speaker / 括号人物）时才丢弃；
# 若剧本明确以该称谓作对白 speaker（「老板娘：…」且非纯称呼语境），由规则层保留。
_APPELLATION_NAMES: frozenset = frozenset({
    "老板娘", "老板", "掌柜", "小二", "伙计", "客人", "顾客", "客官",
    "这位客官", "诸位客官", "各位客官", "众客官",
    "一个人", "有人", "某人", "众人", "人群", "大伙", "大家", "诸位",
    "男子", "女子", "少年", "少女", "孩童", "小孩", "老者", "老翁",
    "老婆婆", "老妇", "老伯", "公子", "姑娘", "小姐", "少爷", "夫人",
    "大侠", "侠客", "剑客", "好汉", "汉子", "路人", "过客", "来客",
    "这位", "那位", "那人", "此女", "此人", "对方", "两人", "三人",
})

# 谓词性句首（「有/见/只见/望见 一人/有人」等泛指引入），命中且短名 → 不作为角色。
_AMBIGUOUS_SHORT_NAMES: frozenset = frozenset({
    "一人", "二人", "三人", "俩人", "几人", "数人", "路人", "过客",
    "来客", "陌生人", "黑衣人", "白衣人", "人影", "身影",
})

# Phase 2-2 commit 2（用户 2026-08-11 拍板「Alias 归一」）：角色称谓后缀。
# Qwen 每次解析可能把「柳如烟」写成「柳姑娘 / 柳小姐」，把「沈青崖」写成「沈大侠 /
# 沈公子」，把「蒙面人」写成「蒙面客」——剥掉后缀得主干（柳/沈/蒙面），再向剧本规则层
# 锚点（shot.characters 真实角色名）做唯一前缀/后缀命中 → 归一到锚点显示名。
# 这样跨解析名称写法变化时 entity_key（character:柳如烟）保持稳定，绑定不分裂。
# ⛔ 刻意不做大规模 Alias 库（用户拍板）：词表只有「称谓后缀」这一小撮通用称呼，
# 不含具体人物名。具体人名锚点全部来自剧本自身（脚本上下文驱动，不硬编码人物表）。
_APPELLATION_SUFFIXES: Tuple[str, ...] = (
    "老板娘", "大姑娘", "小姑娘", "姑娘", "小姐", "公子", "大侠", "女侠",
    "少侠", "侠客", "先生", "前辈", "夫人", "客官", "客", "兄", "姐", "妹",
    "娘", "郎",
)


def invalid_entity_reason(name: str) -> Optional[str]:
    """返回伪实体归因（"shot_marker"/"appellation"/"empty"）；合法实体 → None。

    统一归类（用户 P0-3 拍板）：镜头编号/称谓/空白 都不该进 Character/Location/Prop。
    规则层对白误判（「镜头一：」当说话者）与 Qwen 幻觉都走这里。
    """
    nm = (name or "").strip()
    if not nm:
        return "empty"
    if any(p.match(nm) for p in INVALID_ENTITY_PATTERNS):
        return "shot_marker"
    if nm in _APPELLATION_NAMES or nm in _AMBIGUOUS_SHORT_NAMES:
        return "appellation"
    return None


def is_invalid_entity_name(name: str) -> bool:
    """名称命中伪实体规则 → True（丢弃）。"""
    return invalid_entity_reason(name) is not None


def normalize_entity_name(name: str) -> str:
    """canonical 名：去全部空白 + 小写（用于跨镜头合并去重）。"""
    return re.sub(r"\s+", "", (name or "").strip().lower())


# ---------------------------------------------------------------------------
# P0-3b 动作/空间短语实体名清洗（用户 2026-08-11 拍板「动作/空间短语不能进实体名」）
# ---------------------------------------------------------------------------
# 背景：真实剧本「柳如烟持灯退到柜台边」被实体抽取/清洗切成「灯退到柜」——
# 与已修复的「镜头一/镜头七/客官」（内部标记/称谓）同类的实体污染，属「动作短语
# 切分污染」。铁律：不写黑名单（`if name=="灯退到柜": drop`），建立通用规则。
# 通用规则 = 实体名（纯名词）不得含动作动词；含则裁剪到动词前/后，只留名词核心。
#   持灯退到柜台边 → 灯        （动词「退」在中间 → 裁到动词前）
#   端茶走到桌边   → 茶        （「走」在中间 → 裁到动词前）
#   拔刀扑来       → 刀        （「拔」在首位 → 裁动词留名词）
#   背着旧剑匣走来 → 旧剑匣    （纯名词，不含动词 → 原样）
#   拿起茶碗放在桌上 → 茶碗    （「拿起」首位 → 裁动词；「放在」中间 → 裁到「茶碗」）
# 纯名词实体（柳如烟/客栈大堂/木桌/檐角）不含动词，原样返回。
# P0-3b 补：裁剪动词后残留的助词/桥接（「背着」裁「背」→「着旧剑匣」、
# 「正提着」裁「提」→「正着…」），剥掉才是纯名词核心。按长度降序匹配。
_ACTION_BRIDGE_PREFIXES = (
    "正在", "着", "了", "过", "地", "得", "又", "也", "已", "便", "就", "只", "再",
)


def _cleanse_action_phrase_name(name: str) -> Optional[str]:
    """实体名动作短语粘连清洗；返回清洗后名词，None = 整名都是动作（丢弃）。"""
    nm = (name or "").strip()
    if not nm:
        return None
    if len(nm) < 2:
        return nm
    changed = True
    while changed and nm:
        changed = False
        # P0-3b：同时扫描 动作动词表 + 道具动词表——「持灯/端茶/背剑匣」的
        # 前导道具动词（持/端/背/挎…）也要裁剪，否则「持灯」原样保留为实体名。
        best_idx = -1
        best_len = 0
        for v in _ACTION_VERBS + _PROP_OBJECT_VERBS:
            idx = nm.find(v)
            if idx < 0:
                continue
            # 动词在末尾（如「日出」的「出」、「灯退」的「退」）：不视为粘连，不动
            if idx == len(nm) - len(v):
                continue
            if best_idx < 0 or idx < best_idx or (idx == best_idx and len(v) > best_len):
                best_idx = idx
                best_len = len(v)
        if best_idx < 0:
            break
        # 首位动词：裁掉动词留名词（持灯→灯）；中间动词：裁到动词前（灯退到柜→灯）
        nm = nm[best_len:] if best_idx == 0 else nm[:best_idx]
        changed = True
        # 剥掉残留助词/桥接（背着→着；正提着→正着→着）
        while nm:
            hit = False
            for b in _ACTION_BRIDGE_PREFIXES:
                if nm.startswith(b):
                    nm = nm[len(b):]
                    hit = True
                    break
            if not hit:
                break
    if not nm:
        return None
    if not all("一" <= ch <= "鿿" for ch in nm):
        return None
    return nm


# ---------------------------------------------------------------------------
# 类型纠正启发式（已知优先，其次后缀）
# ---------------------------------------------------------------------------

# 后缀 → 类型（更具体的排前面，避免「门」之类被 location 抢先）
_TYPE_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("costume", ("斗篷", "披风", "发簪", "玉佩", "腰带", "靴", "衫", "袍", "裙",
                 "裤", "帽", "鞋", "甲", "衣")),
    ("architecture", ("斗拱", "檐", "梁", "柱", "瓦", "窗", "墙", "梯", "门")),
    ("effect", ("波纹", "倒影", "飞尘", "光", "雾", "烟", "火", "雨", "雪", "影", "尘", "气")),
    ("environment", ("山色", "夜色", "暮色", "晨光", "天", "云", "风", "林间", "水面")),
    ("location", ("客栈", "大堂", "楼", "堂", "台", "店", "馆", "院", "房", "厅",
                  "阁", "庙", "殿", "塔", "山", "林", "河", "湖", "桥", "园",
                  "街", "巷", "村", "城", "口", "阶")),
    ("prop", ("剑匣", "剑", "刀", "碗", "杯", "壶", "灯", "烛", "香", "书", "信",
              "酒", "坛", "桌", "凳", "椅", "柜", "匾", "帘")),
    ("character", ("人", "客", "者", "侠", "士", "爷", "娘", "生", "女",
                   "兄", "弟", "姐", "妹")),
)

# 强纠正后缀：Qwen 已判 type 但明显误判时（如「道具 青衫」「道具 檐角」「道具 雾气」），
# 命中这些无歧义后缀就纠正。刻意排除 台/门/口/梁/柱 等（「烛台」「大门」「桥梁」仍是道具）。
_STRONG_SUFFIXES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("costume", ("斗篷", "披风", "发簪", "玉佩", "腰带", "衫", "袍", "裙")),
    ("architecture", ("斗拱", "檐", "瓦")),
    ("effect", ("波纹", "倒影", "飞尘", "雾", "烟")),
)


def hint_type(name: str) -> str:
    """后缀启发式：先走强纠正（substring，如「檐角」含「檐」），再走通用 endswith。"""
    nm = (name or "").strip()
    if not nm:
        return EntityType.UNKNOWN
    strong = _strong_hint_type(nm)
    if strong != EntityType.UNKNOWN:
        return strong
    for etype, suffixes in _TYPE_HINTS:
        for s in suffixes:
            if nm.endswith(s) or nm == s:
                return etype
    return EntityType.UNKNOWN


def _strong_hint_type(name: str) -> str:
    """强纠正后缀：只在 prop 误判纠正时用。匹配 endswith 或包含（「檐角」含「檐」）。"""
    nm = (name or "").strip()
    if not nm:
        return EntityType.UNKNOWN
    for etype, suffixes in _STRONG_SUFFIXES:
        for s in suffixes:
            if nm.endswith(s) or s in nm:
                return etype
    if nm.endswith("火"):  # 烛火/灯火 → effect（火单独，防「怒火」误判）
        return EntityType.EFFECT
    return EntityType.UNKNOWN


def _known_names(plan: ProductionPlan) -> Tuple[set, set]:
    """plan → (角色 canonical 集合, 地点 canonical 集合)。"""
    chars: set = set()
    locs: set = set()
    for sc in plan.scenes or []:
        if sc.location_name:
            locs.add(normalize_entity_name(sc.location_name))
        for sh in sc.shots or []:
            for c in sh.characters or []:
                if c.name:
                    chars.add(normalize_entity_name(c.name))
    return chars, locs


def correct_type(plan: ProductionPlan, name: str, cur_type: str) -> str:
    """类型纠正：已知角色/场景地点最高优先；prop 误判用强后缀纠正；未知才全启发式。

    - 角色名命中 → character（覆盖 Qwen 误判）
    - 名称与某个场景 location_name 归一后相等/互含 → location（覆盖「道具 客栈大堂」）
    - 其余：当前类型是 prop 且强后缀命中（衫/檐/雾…）→ 纠正为 costume/architecture/effect
    - 当前类型 UNKNOWN → 全后缀启发式；否则保留 Qwen 已判类型（不强改）
    """
    chars, locs = _known_names(plan)
    nm = normalize_entity_name(name)
    if nm in chars:
        return EntityType.CHARACTER
    for loc in locs:
        if len(nm) >= 2 and len(loc) >= 2 and (nm == loc or nm in loc or loc in nm):
            return EntityType.LOCATION
    if cur_type not in EntityType.ALL:
        cur_type = EntityType.UNKNOWN  # Qwen 非法 type → 当未判定处理
    if cur_type not in (EntityType.UNKNOWN, ""):
        if cur_type == EntityType.PROP:
            strong = _strong_hint_type(name)
            if strong != EntityType.UNKNOWN and strong != EntityType.PROP:
                return strong
        return cur_type
    hinted = hint_type(name)
    return hinted if hinted != EntityType.UNKNOWN else EntityType.UNKNOWN


# ---------------------------------------------------------------------------
# 注册表构建（去重 + 类型纠正 + 置信度门控 + 漏斗）
# ---------------------------------------------------------------------------

def _to_entry(e: Entity, plan: ProductionPlan) -> Optional[Dict[str, Any]]:
    """Entity → 注册表条目（动作短语清洗 + 类型纠正 + 门控标注，不改原始 Entity）。

    P0-3b：先做动作短语实体名清洗（「灯退到柜」→「灯」）。清洗后无名词核心
    （整名都是动作短语）返回 None → 调用方按丢弃处理（计入 invalid_dropped）。
    asset_requirement 按纠正后 type 规则推导（character/location→required；
    prop/costume/architecture/vehicle/creature→recommended；
    environment/effect/unknown→none 不进匹配）。Qwen 不输出该字段（纯规则零幻觉）。
    """
    nm = _cleanse_action_phrase_name(e.name)
    if nm is None:
        return None
    etype = correct_type(plan, nm, e.type or EntityType.UNKNOWN)
    source = e.source if e.source in EntitySource.ALL else EntitySource.SCRIPT
    conf = max(0.0, min(1.0, float(e.confidence or 0.0)))
    requirement = asset_requirement_for_type(etype)
    entered = (
        source == EntitySource.SCRIPT
        and conf >= CONF_ENTER
        and requirement != AssetRequirement.NONE
    )
    can_auto = entered and conf >= CONF_AUTO_ENTITY
    aliases = [str(a) for a in (e.aliases or [])]
    raw = (e.name or "").strip()
    if raw and raw != nm and raw not in aliases:
        aliases = [raw] + aliases
    return {
        "entity_id": e.entity_id or "",
        "name": nm,
        "type": etype,
        "source": source,
        "confidence": round(conf, 3),
        "asset_requirement": requirement,
        "aliases": aliases,
        "entered": entered,
        "can_auto": can_auto,
    }


def _merge_entry(base: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    """同名条目合并：置信度取高者；类型优先非 UNKNOWN；aliases 累积原样名。

    asset_requirement 只是中间值（取 keep 的），合并后由 build_entity_registry
    按最终 type 统一重算（保证与类型纠正结果一致）。
    """
    keep = base if base["confidence"] >= other["confidence"] else other
    merged: Dict[str, Any] = {
        "entity_id": keep["entity_id"] or base.get("entity_id") or other.get("entity_id"),
        "name": keep["name"] or base["name"] or other["name"],
        "type": keep["type"] if keep["type"] != EntityType.UNKNOWN else base["type"],
        "source": keep["source"],
        "confidence": round(max(base["confidence"], other["confidence"]), 3),
        "asset_requirement": keep.get(
            "asset_requirement", AssetRequirement.REQUIRED
        ),
        "aliases": list(base["aliases"]) + [a for a in other["aliases"] if a not in base["aliases"]],
        "entered": base["entered"] or other["entered"],
        "can_auto": base["can_auto"] or other["can_auto"],
    }
    nm = merged["name"]
    for src in (base, other):
        for a in [src["name"], *src["aliases"]]:
            if a and a != nm and a not in merged["aliases"]:
                merged["aliases"].append(a)
    return merged


def collect_entities(plan: ProductionPlan) -> List[Entity]:
    """plan → 全部 shot.entities（Qwen 产物），保序。"""
    out: List[Entity] = []
    for sc in plan.scenes or []:
        for sh in sc.shots or []:
            for e in sh.entities or []:
                out.append(e)
    return out


# ---------------------------------------------------------------------------
# P0-① 实体边界检查（用户 2026-08-13 拍板 ②：Qwen 越界实体在 Asset Matcher
# 之前丢弃，不允许 AI 幻觉实体进入资产注册/匹配链路）
# ---------------------------------------------------------------------------
# 边界检查生效前提：source_text 是「足够真实的剧本原文」（真实镜头描述远超此值；
# 测试/占位短文本视为无边界信息，跳过检查避免误杀）。旧剑匣混进镜头 1 的真实
# source_text 53 字符 >> 此值 → 严格检查 → 丢弃。
_SHOT_SCOPE_MIN_LEN = 20


def entity_in_shot_scope(
    entity: Entity,
    source_text: str,
    char_anchors: Optional[Dict[str, str]] = None,
    *,
    min_len: int = _SHOT_SCOPE_MIN_LEN,
) -> bool:
    """实体是否在本镜 source_text 边界内（per-shot 判定）。

    规则（与用户拍板「原文实体 → 规则实体 → Qwen补充 → 边界检查 → 合法实体」一致）：
      1. 实体名或其别名**逐字**出现在本镜 source_text → 合法（剧本明确事实）；
      2. 角色类型的别名变体（柳姑娘→柳如烟）经 resolve_character_alias 归一到
         规则层锚点后，锚点名出现在原文 → 合法；
      3. 否则 = Qwen 跨镜越界幻觉（如「旧剑匣」混进镜头 1）→ 越界丢弃。
    source_text 短于 min_len（占位/测试）→ 无边界信息，返回 True 不误杀。
    """
    text = (source_text or "").strip()
    if len(text) < min_len:
        return True
    if entity is None:
        return False
    names = [entity.name or ""] + [str(a) for a in (entity.aliases or [])]
    for nm in names:
        if nm and nm in text:
            return True
    if char_anchors and getattr(entity, "type", "") == EntityType.CHARACTER:
        canon = resolve_character_alias(entity.name or "", char_anchors)
        if canon and canon != (entity.name or "") and canon in text:
            return True
    return False


def collect_entities_scoped(plan: ProductionPlan) -> List[Tuple[Entity, str]]:
    """plan → [(Entity, 所属镜头 source_text)]，保留 per-shot 上下文供边界检查。"""
    out: List[Tuple[Entity, str]] = []
    for sc in plan.scenes or []:
        for sh in sc.shots or []:
            src = sh.source_text or ""
            for e in sh.entities or []:
                out.append((e, src))
    return out


def _collect_rule_character_names(plan: ProductionPlan) -> List[str]:
    """shot.characters 里的角色名（去重保序，兜底用）。

    P0-3（用户 2026-08-11 拍板）：规则层也可能把镜头标记行「镜头一：」当说话者，
    Qwen 也可能把称谓塞进 characters。这里统一过滤伪实体（shot_marker/appellation），
    保证注册表永不被「镜头一」「客官」污染 —— 这是 SPA 真实页面角色 11 的根治点。
    """
    seen: List[str] = []
    for sc in plan.scenes or []:
        for sh in sc.shots or []:
            for c in sh.characters or []:
                nm = (c.name or "").strip()
                if not nm or invalid_entity_reason(nm) is not None:
                    continue
                if nm not in seen:
                    seen.append(nm)
    return seen


def _character_anchor_map(plan: ProductionPlan) -> Dict[str, str]:
    """规则层角色名 → {canonical: display} 锚点表（Phase 2-2 commit 2 Alias 归一）。

    锚点 = 剧本规则层 shot.characters 里的真实角色名（script_parser 提取，脚本上下文
    驱动，不硬编码任何人物表）。Qwen 实体名的称谓变体（柳姑娘/沈大侠/蒙面客）向这里
    归一到规范名，从而 entity_key 跨解析稳定（character:柳如烟 不会因写法变化分裂）。
    """
    out: Dict[str, str] = {}
    for nm in _collect_rule_character_names(plan):
        key = normalize_entity_name(nm)
        out.setdefault(key, nm)
    return out


def resolve_character_alias(name: str, anchors: Dict[str, str]) -> str:
    """角色实体名 → 归一到规则层锚点显示名；无法唯一确定 → 原样返回。

    anchors：{canonical: display}，来自 _character_anchor_map（规则层真实角色名）。
    规则（⛔ 保守，歧义不猜，绝不硬猜）：
      1. 名已精确命中锚点（canonical 相等）→ 原样返回（已是规范名）；
      2. 剥称谓后缀（姑娘/大侠/客…，最长优先）得主干；
      3. 主干精确命中唯一锚点 → 该锚点显示名；
      4. 主干是唯一锚点的前缀（柳 → 柳如烟；蒙面 → 蒙面人）→ 该锚点显示名；
      5. 主干（≥2 字）是唯一锚点的后缀（如烟 → 柳如烟）→ 该锚点显示名；
      6. 其余（无命中 / 多命中 / 主干过短）→ 原样返回。
    """
    nm = (name or "").strip()
    if not nm or not anchors:
        return nm
    key = normalize_entity_name(nm)
    if key in anchors:
        return nm  # 已是规范名
    stem = nm
    for suf in sorted(_APPELLATION_SUFFIXES, key=len, reverse=True):
        if nm.endswith(suf) and len(nm) > len(suf):
            stem = nm[: -len(suf)]
            break
    skey = normalize_entity_name(stem)
    if not skey or skey == key:
        return nm
    if skey in anchors:
        return anchors[skey]
    # 前缀（柳 → 柳如烟；len≥1 允许单字姓）
    pre = [a for a in anchors if a.startswith(skey) and a != skey]
    if len(pre) == 1:
        return anchors[pre[0]]
    # 后缀（如烟 → 柳如烟；len≥2 防过度归并）
    if len(skey) >= 2:
        suf = [a for a in anchors if a.endswith(skey) and a != skey]
        if len(suf) == 1:
            return anchors[suf[0]]
    return nm


def _collect_rule_prop_names(plan: ProductionPlan) -> List[str]:
    """shot.props 里的道具名（去重保序，仅纯规则 plan 兜底用）。"""
    seen: List[str] = []
    for sc in plan.scenes or []:
        for sh in sc.shots or []:
            for p in sh.props or []:
                nm = (p.name or "").strip()
                if not nm or invalid_entity_reason(nm) is not None:
                    continue
                if nm not in seen:
                    seen.append(nm)
    return seen


def plan_has_entities(plan: ProductionPlan) -> bool:
    """plan 是否含 Qwen 实体（shot.entities 非空）。"""
    for sc in plan.scenes or []:
        for sh in sc.shots or []:
            if sh.entities:
                return True
    return False


def _legacy_targets(plan: ProductionPlan) -> List[Dict[str, Any]]:
    """纯规则 plan（无 Qwen 实体）回退：地点/角色/道具全进注册表（高置信）。

    覆盖没有跑 analyze_script 的旧 plan / 测试场景；真实 Qwen 链路走实体模式。
    """
    out: List[Dict[str, Any]] = []
    for sc in plan.scenes or []:
        if (sc.location_name or "").strip():
            entry = _to_entry(
                Entity(name=sc.location_name, type=EntityType.LOCATION,
                       source=EntitySource.SCRIPT, confidence=CONF_SEED),
                plan,
            )
            if entry is not None:
                out.append(entry)
    for nm in _collect_rule_character_names(plan):
        entry = _to_entry(
            Entity(name=nm, type=EntityType.CHARACTER,
                   source=EntitySource.SCRIPT, confidence=CONF_RULE_CHAR),
            plan,
        )
        if entry is not None:
            out.append(entry)
    for nm in _collect_rule_prop_names(plan):
        entry = _to_entry(
            Entity(name=nm, type=EntityType.PROP,
                   source=EntitySource.SCRIPT, confidence=0.9),
            plan,
        )
        if entry is not None:
            out.append(entry)
    return out


def _collect_visual_elements(plan: ProductionPlan) -> List[Dict[str, Any]]:
    """plan → 全部 shot.visual_elements（Task B 产物，去重保序，供前端/Prompt 展示）。

    P0-2（用户 2026-08-11 拍板「正式 Location 已进入 location_name 的，不再进入
    visual_elements」）：跳过 Scene 正式地点（客栈大堂/后院石阶）——它们已经是正式
    Location 资产，视觉元素清单里再出现就是重复。
    P0-3b：跳过判定剥中文类型后缀（后院石阶环境/客栈大堂环境/山雨楼建筑），
    并允许层级互含（山雨楼 ⊂ 山雨楼外）——带后缀的正式地点变体一并跳过。
    """
    scene_locs = _scene_location_set(plan)
    out: List[Dict[str, Any]] = []
    seen = set()
    for sc in plan.scenes or []:
        for sh in sc.shots or []:
            for v in sh.visual_elements or []:
                nm = (v.name or "").strip()
                if not nm:
                    continue
                key = normalize_entity_name(nm)
                if _ve_is_scene_location(nm, scene_locs):
                    continue
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "name": v.name,
                        "type": v.type if v.type in EntityType.ALL else EntityType.UNKNOWN,
                        "confidence": round(float(v.confidence or 0.0), 3),
                    }
                )
    return out


def _scene_location_set(plan: ProductionPlan) -> set:
    """Scene 级正式地点集合（scene.location_name，canonical 化）。

    P0-3（用户 2026-08-11 拍板）：Scene Location 必须是场景结构字段
    （第一场→山雨楼外 / 第二场→客栈大堂 / 第三场→后院石阶），
    Shot 里的空间描述（山道/檐角/门口/柜台/桌边）不得升级成同等级 Location 资产。
    """
    return {
        normalize_entity_name(sc.location_name)
        for sc in plan.scenes or []
        if sc.location_name
    }


# P0-3b（用户 2026-08-11 拍板「正式地点不得再进 visual_elements」）：
# Qwen Task B 视觉元素名常带中文类型后缀（「后院石阶环境」「客栈大堂环境」
# 「山雨楼建筑」「雨歇特效」「青衫服装」）——剥掉后缀后的名字才是真正名词。
# 若剥后缀后命中某 Scene 正式地点（exact 或层级互含：山雨楼 ⊂ 山雨楼外），
# 说明该 ve 是正式地点的带后缀变体 → 跳过（正式地点已是 Location 资产）。
_VE_TYPE_SUFFIXES = (
    "环境", "特效", "建筑", "服装", "氛围", "光线", "天气", "光影",
)


def _strip_ve_type_suffix(name: str) -> str:
    """剥掉视觉元素名的中文类型后缀（环境/特效/建筑/服装…）。"""
    for suf in _VE_TYPE_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _ve_is_scene_location(name: str, scene_locs: set) -> bool:
    """视觉元素名（剥类型后缀后）是否对应某 Scene 正式地点（exact 或层级互含）。"""
    key = normalize_entity_name(_strip_ve_type_suffix(name))
    if not key:
        return False
    for loc in scene_locs:
        if key == loc:
            return True
        if len(key) >= 2 and len(loc) >= 2 and (key in loc or loc in key):
            return True
    return False


def _location_aligned(name: str, scene_locs: set) -> bool:
    """location 实体是否与某 Scene 正式地点精确对齐（P0-2：只允许精确相等）。

    P0-2（用户 2026-08-11 拍板「地点 matches 恰好 3 正式地点」）：
    之前用「互含」判定，导致 山雨楼（⊂山雨楼外）与 石阶（⊂后院石阶）都通过，
    SPA 真实验收 地点 5 个（后院石阶/客栈大堂/山雨楼/山雨楼外/石阶）。
    现在改为只允许 canonical 精确相等：正式 Location 资产 = Scene 的 location_name
    （山雨楼外 / 客栈大堂 / 后院石阶）。山雨楼/石阶 → 降级 environment 进视觉元素。
    """
    key = normalize_entity_name(name)
    if not key:
        return False
    return key in scene_locs


def _degrade_unaligned_locations(
    entries: List[Dict[str, Any]], scene_locs: set
) -> None:
    """未对齐 Scene 正式地点的 location 实体 → 降级 environment（只进 Prompt）。

    就地修改 entries：type → environment（requirement=none，entered=False，进 visual_elements）。
    Qwen 幻觉的「山道」「檐角」「门口」不会升级成 Location 资产，但保留在视觉元素里
    供 Prompt Builder 使用 —— 这正是用户要的「visual_context / environment」而非「location entity」。
    """
    for e in entries:
        if e["type"] == EntityType.LOCATION and not _location_aligned(
            e["name"], scene_locs
        ):
            e["type"] = EntityType.ENVIRONMENT


def _rebuild_gates(entry: Dict[str, Any]) -> Dict[str, Any]:
    """按最终 type 重算 asset_requirement / entered / can_auto（合并后统一调用）。

    保证 asset_requirement 永远与纠正后类型一致（Qwen 旧数据/类型纠正后都可能漂移）。
    """
    req = asset_requirement_for_type(entry["type"])
    entry["asset_requirement"] = req
    entry["entered"] = (
        entry["source"] == EntitySource.SCRIPT
        and entry["confidence"] >= CONF_ENTER
        and req != AssetRequirement.NONE
    )
    entry["can_auto"] = entry["entered"] and entry["confidence"] >= CONF_AUTO_ENTITY
    return entry


def build_entity_registry(plan: ProductionPlan) -> Dict[str, Any]:
    """ProductionPlan → 清洗后的实体注册表 + 漏斗统计 + 视觉元素清单。

    返回：
      entries: [{entity_id, name, type, source, confidence, asset_requirement,
                 aliases, entered, can_auto}]  —— 去重保序，type 已纠正，
                 asset_requirement 按类型规则推导（required/recommended/none）
      visual_elements: [{name, type, confidence}] —— Task B 视觉元素 + 实体里
                 requirement=none 的旧数据（只进 Prompt，永不进资产匹配）
      funnel:  {discovered, invalid_dropped, dedup_dropped, seeded,
                cleansed, entered, visual_only, excluded}
      （auto/pending/none 由 asset_matcher 匹配后回填到 funnel）

    双模式：
      - plan 含 Qwen 实体（真实链路）：实体来自 shot.entities，经 INVALID 过滤 +
        类型纠正 + canonical 去重；**不**把 shot.props 回流（杜绝「道具 青衫」垃圾）。
        补位 = 场景 location_name + 规则角色兜底（Qwen 实体里没有的角色名）。
      - plan 无实体（纯规则/测试）：回退 legacy 提取（地点/角色/道具），保证
        Phase 2 对未跑 Qwen 的 plan 也能匹配（覆盖旧行为与既有测试）。
    """
    qwen = collect_entities_scoped(plan)
    discovered = len(qwen)
    has_entities = discovered > 0

    # P0-①（用户 2026-08-13 拍板 ②）：实体边界检查在 Asset Matcher 之前。
    # 角色锚点提前计算（供别名变体判定：柳姑娘→柳如烟），Qwen 跨镜越界实体
    # （如「旧剑匣」混进镜头 1）在此丢弃，绝不让 AI 幻觉实体进资产注册/匹配。
    char_anchors = _character_anchor_map(plan)

    invalid_dropped = 0
    boundary_dropped = 0
    kept: List[Dict[str, Any]] = []
    for e, src_text in qwen:
        if is_invalid_entity_name(e.name):
            invalid_dropped += 1
            continue
        if not entity_in_shot_scope(e, src_text, char_anchors):
            boundary_dropped += 1
            continue
        entry = _to_entry(e, plan)
        if entry is None:
            # P0-3b：动作短语粘连且无名词核心（整名都是动作短语）→ 丢弃
            invalid_dropped += 1
            continue
        kept.append(entry)

    # P0-3（用户 2026-08-11 拍板）：Qwen 幻觉的 location 实体（山道/檐角/门口/柜台/桌边）
    # 不对齐任何 Scene 正式地点（scene.location_name）→ 降级 environment（只进 Prompt，
    # 不升级成 Scene Location 资产）。
    # P0-2（用户 2026-08-11 拍板「地点 matches 恰好 3 正式地点」）：对齐判定改为
    # 只允许精确相等 —— 山雨楼（⊂山雨楼外）/ 石阶（⊂后院石阶）不再视为对齐，
    # 一并降级 environment（SPA 真实验收 地点 5 → 3）。
    _degrade_unaligned_locations(kept, _scene_location_set(plan))

    # Phase 2-2 commit 2（用户 2026-08-11 拍板「Alias 归一」）：角色别名归一到规则层锚点。
    # Qwen 同一角色跨解析可能给出不同写法（柳如烟/柳姑娘/沈青崖/沈大侠/蒙面人/蒙面客）。
    # 只对 character 类型做（location/prop 刻意不做：山雨楼≠山雨楼外、旧剑≠旧剑匣
    # 由 P0-2 精确对齐与 semantic_incompatible 处理，语义不同物不得归并）。
    # 归一后 entry.name = 锚点规范名，原样名进 aliases → entity_key 跨解析稳定。
    char_anchors = _character_anchor_map(plan)
    if char_anchors:
        for entry in kept:
            if entry["type"] != EntityType.CHARACTER:
                continue
            canon = resolve_character_alias(entry["name"], char_anchors)
            if canon != entry["name"]:
                raw = entry["name"]
                entry["name"] = canon
                if raw and raw not in entry["aliases"]:
                    entry["aliases"].append(raw)

    # 规则补位种子（去重前的原始候选）
    if has_entities:
        seeds = _legacy_targets(plan)
        # P0-2（用户 2026-08-11 拍板「原文兜底」）：规则层负责「不能漏」——
        # script_parser 已把剧本原文直接出现的核心实体写入 shot.characters / shot.props
        # （沈青崖/柳如烟/蒙面人/旧剑匣/青瓷茶碗…）。Qwen 漏返回的在此补位；
        # Qwen 已返回的（同 canonical 名）不补（避免覆盖 Qwen 的置信度）。
        # 例：Qwen 漏了「蒙面人」→ 规则角色补位 0.95；Qwen 漏了「旧剑匣」→ 规则道具补位 0.9。
        qwen_norms = {
            normalize_entity_name(x["name"])
            for x in kept
        }
        seeds = [
            s for s in seeds
            if normalize_entity_name(s["name"]) not in qwen_norms
        ]
    else:
        seeds = _legacy_targets(plan)

    # canonical 去重合并（种子优先，保证规则名/类型不被 Qwen 同义条目覆盖）
    merged: Dict[str, Dict[str, Any]] = {}
    for entry in seeds + kept:
        key = normalize_entity_name(entry["name"])
        if not key:
            continue
        if key in merged:
            merged[key] = _merge_entry(merged[key], entry)
        else:
            merged[key] = entry

    entries = list(merged.values())
    # 统一重算需求/门控（按合并后最终 type）
    for entry in entries:
        _rebuild_gates(entry)
    # 稳定排序：character/location 在前（关键资产），其余按置信度降序
    def _sort_key(x: Dict[str, Any]) -> Tuple[int, float, str]:
        rank = 0 if x["type"] == EntityType.CHARACTER else (
            1 if x["type"] == EntityType.LOCATION else 2
        )
        return (rank, -x["confidence"], x["name"])

    entries.sort(key=_sort_key)

    entered_list = [x for x in entries if x["entered"]]
    visual_only_list = [x for x in entries if x["asset_requirement"] == AssetRequirement.NONE]
    seeded = len(seeds)
    cleansed = len(entries)

    # Task B 视觉元素清单（shot.visual_elements 为主 + 实体里 requirement=none 的旧数据兜底）
    visual_elements = _collect_visual_elements(plan)
    seen_ve = {normalize_entity_name(v["name"]) for v in visual_elements}
    for e in visual_only_list:
        key = normalize_entity_name(e["name"])
        if key and key not in seen_ve:
            seen_ve.add(key)
            visual_elements.append(
                {
                    "name": e["name"],
                    "type": e["type"],
                    "confidence": e["confidence"],
                }
            )

    funnel: Dict[str, Any] = {
        "discovered": discovered,
        "invalid_dropped": invalid_dropped,
        "boundary_dropped": boundary_dropped,
        "dedup_dropped": max(0, (len(kept) + seeded) - cleansed),
        "seeded": seeded,
        "cleansed": cleansed,
        "entered": len(entered_list),
        "visual_only": len(visual_only_list),
        "excluded": max(0, cleansed - len(entered_list) - len(visual_only_list)),
    }

    return {
        "entries": entries,
        "visual_elements": visual_elements,
        "funnel": funnel,
    }


def entered_entities(plan: ProductionPlan) -> List[Dict[str, Any]]:
    """只取进入资产匹配的实体（asset_matcher 消费入口）。"""
    reg = build_entity_registry(plan)
    return [e for e in reg["entries"] if e["entered"]]


# kind 映射：实体类型 → 资产 kind（与 asset_matcher 契约一致）
def kind_for_type(etype: str) -> str:
    """实体类型 → 资产匹配 kind：character→cast / location→location / 其余→prop。"""
    if etype == EntityType.CHARACTER:
        return "cast"
    if etype == EntityType.LOCATION:
        return "location"
    return "prop"


__all__ = [
    "CONF_ENTER",
    "CONF_AUTO_ENTITY",
    "INVALID_ENTITY_PATTERNS",
    "invalid_entity_reason",
    "is_invalid_entity_name",
    "normalize_entity_name",
    "hint_type",
    "correct_type",
    "collect_entities",
    "build_entity_registry",
    "entered_entities",
    "kind_for_type",
    "resolve_character_alias",
]
