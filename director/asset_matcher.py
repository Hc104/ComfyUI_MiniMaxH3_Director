"""V1.7 Phase 2：资产自动匹配（P0-B 剧本 → 资产）。

用户拍板（2026-08-11）：
- 资产来源：固定共享资产库 `{ComfyUI input}/minimax_studio/assets/`（当前所有资产图都在此）。
- 匹配引擎：纯规则先行（精确 → 归一后精确 → 子串包含 → 置信度打分），**不跑 Qwen 语义**。
  Qwen 语义匹配（「小林」→ 林雪 96%）留 Phase 4 或需要时再加。
- 绑定深度：高置信度（>=0.9）自动绑；低置信度（0.5~0.89）弹候选人工确认；无候选不匹配。

⛔ AI 永远不能覆盖用户已经存在的资产：本项目只做「匹配已有资产文件」，绝不创建
「林雪_2」之类的新资产。未匹配就留空（进工作台后用户可手工补参考图）。

匹配链路（V17_PLAN §5 三级，Phase 2 实现前两级规则层 + 2026-08-11 实体抽取加固）：
  剧本实体 → 实体清洗（entity_cleanse 纯规则：伪实体剔除/类型纠正/canonical 去重/
  置信度门控）→ ① 精确名称匹配 → ② 归一后精确 → ③ 子串包含 → 置信度
  - 实体置信度 < 0.60：不进匹配（funnel.excluded）
  - 实体置信度 0.60~0.85：进入匹配但最高 pending（can_auto=False，即使资产精确命中）
  - 实体置信度 >= 0.85：按资产匹配分走 auto/pending/none
  - 资产匹配分 >= 0.9 → status="auto"；0.5~0.89 或有候选 → "pending"；无候选 → "none"

输出契约（`match_plan`）：
  { assets: [{name, image_file}],        // 资产库去重列表（供前端缩略图）
    matches: [{kind, name, status, matched, asset_name?, image_file?, confidence,
               candidates: [{name, image_file, score}],
               entity_type, entity_source, entity_confidence, entity_id, can_auto}] }
  - kind ∈ {"cast", "prop", "location"}：由 entity_type 映射（character→cast / location→location / 其余→prop）
  - status ∈ {"auto", "pending", "none"}；matched=true 仅 auto（pending 由前端确认后置 true）
  - funnel: {discovered, invalid_dropped, dedup_dropped, seeded, cleansed, entered,
             excluded, auto, pending, none}  —— 导入弹窗漏斗展示
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from .entity_cleanse import build_entity_registry, kind_for_type
from .production_plan import AssetRequirement, EntityType
from .asset_registry import entity_key_from_entry

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# ── 置信度规则表（Phase 2-1 顺手集中；实体级阈值见 entity_cleanse.CONF_*）──
# 资产匹配分（match_one）：精确全等 1.0 → 归一后精确 0.95 → 子串包含 0.8 → 无候选 0.0
#   status = auto（>= CONF_AUTO） / pending（>= CONF_PENDING 且有候选） / none（无候选）
# 实体级门控（entity_cleanse）：< CONF_ENTER 不进匹配；< CONF_AUTO_ENTITY 最高 pending
# 持久化覆盖（asset_registry）：用户已确认 binding → match_kind="persisted"，无条件覆盖
CONF_AUTO = 0.9  # >= 自动绑定
CONF_PENDING = 0.5  # >= 低置信度（有候选，需人工确认）

ASSETS_SUBDIR = os.path.join("minimax_studio", "assets")

# 资产文件名里的通用前后缀（导演输出中间帧/设定卡等），归一化时剥掉
_NAME_PREFIXES = ("角色_", "场景_", "设定卡_", "素材_", "assets_", "pic_", "img_")
_NAME_SUFFIXES = (
    "_主视觉", "_主视角", "_侧视角", "_近景图", "_中近距离图", "_微距图",
    "_设定卡", "_主图", "_参考", "_图", "_v1", "_v2", "_01", "_02",
    " 主视觉", " 主视角", " 侧视角", " 角色", " 设定卡", " 图", "图",
)

# 资产 kind 前缀推断（角色_→cast / 场景_→location / 设定卡_素材_→prop）
_ASSET_KIND_PREFIX: tuple[tuple[str, str], ...] = (
    ("角色_", "cast"),
    ("场景_", "location"),
    ("设定卡_", "prop"),
    ("素材_", "prop"),
)

# ---- Phase 1.1 语义判定词表（纯规则，用户 2026-08-11 拍板）----
# 语义不兼容后缀：实体名 ⊂ 资产名 且剩余部分命中 → 语义不同物（旧剑→旧剑匣），
# 该候选直接剔除（即使字符串相似，也不能当同一资产）。
_SEMANTIC_INCOMPATIBLE_SUFFIX: tuple[str, ...] = (
    "匣", "柄", "鞘", "尖", "盖", "套", "锁", "盒", "袋", "箱",
    "绳", "环", "钮", "扣", "夹", "把", "带", "面",
)

# 地点层级词：location 实体 ⊂ location 资产 且剩余命中 → 建议参考（山雨楼→山雨楼外）
_LOCATION_HIERARCHY_SUFFIX: tuple[str, ...] = (
    "外", "内", "堂", "厅", "楼", "台", "殿", "阁", "口", "门",
    "前", "后", "东", "西", "南", "北", "旁", "侧", "附近", "门口",
)
_LOCATION_HIERARCHY_PREFIX: tuple[str, ...] = (
    "后院", "客栈", "酒楼", "门口", "门前", "屋内", "堂前", "檐下",
    "阶前", "山间", "林中", "店内", "窗边", "河畔", "桥上", "树下",
    "楼前", "楼后", "楼上", "楼下", "山前", "山后",
)

# 泛化修饰前缀：实体 ⊂ 资产 且剩余前缀命中 → 泛化引用（石阶→后院石阶），
# 建议参考但**原文实体名不被反向污染**（generic_reference）。
_GENERIC_MODIFIERS_PREFIX: tuple[str, ...] = (
    "后院", "客栈", "酒楼", "门口", "门前", "屋内", "堂前", "檐下",
    "阶前", "山间", "林中", "店内", "窗边", "河畔", "桥上", "树下",
    "楼前", "楼后", "楼上", "楼下", "山前", "山后",
    "旧", "破", "小", "大", "老", "新",
)


def _hit(part: str, words: tuple[str, ...]) -> bool:
    """part 命中词表：等值或前缀命中（「外间」命中「外」）。"""
    if not part:
        return False
    for w in words:
        if part == w or part.startswith(w):
            return True
    return False


def _asset_kind_by_prefix(name: str) -> str:
    """文件名前缀 → 资产 kind（角色_→cast 等）；无前缀 → unknown。"""
    for pfx, kind in _ASSET_KIND_PREFIX:
        if (name or "").startswith(pfx):
            return kind
    return "unknown"


# ---- Phase 1.1 硬约束：实体类型 ↔ 资产 kind（character→cast / location→location / prop类→prop）----
_ASSET_KIND_BY_TYPE: Dict[str, str] = {
    EntityType.CHARACTER: "cast",
    EntityType.LOCATION: "location",
}


def _type_compatible(etype: Any, asset_kind: str) -> bool:
    """实体类型 ↔ 资产 kind 硬约束。etype 为 None/unknown 时放行（保守）。

    - character → 只匹配 cast；location → 只匹配 location；
      prop/costume/architecture/vehicle/creature → 只匹配 prop。
    - 资产 kind 未知（unknown）→ 放行（无法判定时宁可给候选，不让用户丢匹配）。
    - 语义不兼容交给 _judge_relation 的子串级判定（旧剑→旧剑匣 是 prop↔prop，
      类型硬约束拦不住，必须靠词表）。
    """
    if etype is None or etype == EntityType.UNKNOWN:
        return True
    if asset_kind == "unknown":
        return True
    want = _ASSET_KIND_BY_TYPE.get(etype)
    if want is not None:
        return asset_kind == want
    # prop 类实体（prop/costume/architecture/vehicle/creature）→ 只匹配 prop
    return asset_kind == "prop"


def _judge_relation(etype: Any, ekey: str, akey: str) -> str:
    """子串关系的语义判定（Phase 1.1 核心，纯规则词表）。

    返回 match_kind 之一：
      "semantic_incompatible" — 实体名 ⊂ 资产名 且剩余部分是事物部位/容器后缀
                                （旧剑→旧剑匣）→ 语义不同物，候选剔除
      "location_hierarchy"    — location 实体 ⊂ location 资产 且剩余是地点层级词
                                （山雨楼→山雨楼外）→ 建议参考
      "generic_reference"     — 实体 ⊂ 资产 且剩余是泛化修饰前缀
                                （石阶→后院石阶）→ 建议参考，不污染原文
      "contains"              — 普通子串包含（候选保留）
    """
    if ekey == akey:
        return "contains"
    if not (len(ekey) >= 2 and len(akey) >= 2):
        return "contains"
    is_location = etype == EntityType.LOCATION
    if ekey in akey:
        if akey.startswith(ekey):
            suffix = akey[len(ekey):]
            if is_location and _hit(suffix, _LOCATION_HIERARCHY_SUFFIX):
                return "location_hierarchy"
            if _hit(suffix, _SEMANTIC_INCOMPATIBLE_SUFFIX):
                return "semantic_incompatible"
            return "contains"
        if akey.endswith(ekey):
            prefix = akey[:-len(ekey)]
            if is_location and _hit(prefix, _LOCATION_HIERARCHY_PREFIX):
                return "location_hierarchy"
            if _hit(prefix, _GENERIC_MODIFIERS_PREFIX):
                return "generic_reference"
            return "contains"
        # 实体名在资产名中间（如 旧剑 ⊂ 一把旧剑匣）→ 保守按 contains
        return "contains"
    return "contains"


def _key(s: str) -> str:
    """去空白 + 小写（用于精确比较）。"""
    return re.sub(r"\s+", "", (s or "").strip().lower())


def _norm_asset_name(name: str) -> str:
    """剥掉资产文件名的通用前后缀，得到语义名（「角色_XILNAR_主视角」→「XILNAR」）。"""
    n = (name or "").strip()
    for pfx in _NAME_PREFIXES:
        if n.startswith(pfx):
            n = n[len(pfx):]
            break
    for sfx in _NAME_SUFFIXES:
        if n.endswith(sfx):
            n = n[: -len(sfx)]
            break
    return n.strip()


def scan_asset_library(root: str | None = None) -> List[Dict[str, str]]:
    """扫描共享资产库 `minimax_studio/assets/`，返回 [{name, image_file, kind}]（按文件名排序）。

    image_file 是 ComfyUI input 相对路径（与现有 Asset.imageFile 规约一致，
    前端经 comfyInputUrl() 拼 /view?type=input 预览）。
    kind ∈ {"cast","location","prop","unknown"}：前缀推断（角色_→cast 等），
    无前缀先标 unknown；match_plan 里再用实体名逆向锚定补齐。
    """
    base = root
    if base is None:
        import folder_paths  # noqa: PLC0415 - 延迟导入：纯单测环境可传 root 不依赖 comfy

        base = os.path.join(folder_paths.get_input_directory(), ASSETS_SUBDIR)

    out: List[Dict[str, str]] = []
    if not os.path.isdir(base):
        return out
    for fn in sorted(os.listdir(base)):
        ext = os.path.splitext(fn)[1].lower()
        if ext not in IMAGE_EXTS:
            continue
        name = os.path.splitext(fn)[0].strip()
        if not name:
            continue
        out.append({
            "name": name,
            "image_file": f"{ASSETS_SUBDIR.replace(os.sep, '/')}/{fn}",
            "kind": _asset_kind_by_prefix(name),
        })
    return out


def _infer_asset_kinds(
    library: List[Dict[str, Any]], entries: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """资产 kind 补齐：前缀标注优先；unknown 资产用 entered 实体名归一全等逆向锚定。

    例：资产「沈青崖.png」无前缀，实体表里有沈青崖(character)→ 资产 kind=cast。
    这样类型硬约束才能对无前缀的既有资产库生效（资产库里多数图无类型前缀）。
    """
    kind_by_norm: Dict[str, str] = {}
    for e in entries:
        if not e.get("entered"):
            continue
        kind_by_norm.setdefault(_key(e["name"]), kind_for_type(e["type"]))
    out: List[Dict[str, Any]] = []
    for a in library:
        kind = a.get("kind", "unknown")
        if kind == "unknown":
            an = _key(_norm_asset_name(a["name"]))
            if an and an in kind_by_norm:
                kind = kind_by_norm[an]
        out.append({**a, "kind": kind})
    return out


def collect_plan_entities(plan: Any) -> List[Dict[str, str]]:
    """从 ProductionPlan 提取待匹配实体（角色 / 道具 / 地点），去重保序。

    ⚠ 兼容保留：2026-08-11 实体抽取加固后，`match_plan` 已改走 entity_cleanse
    注册表（清洗+类型+置信度门控）。此函数保留供旧测试/纯规则 plan 使用。
    """
    entities: List[Dict[str, str]] = []
    seen = set()

    def add(kind: str, name: str) -> None:
        nm = (name or "").strip()
        if not nm:
            return
        k = (kind, _key(nm))
        if k in seen:
            return
        seen.add(k)
        entities.append({"kind": kind, "name": nm})

    for sc in plan.scenes or []:
        add("location", sc.location_name)
        for sh in sc.shots or []:
            for c in sh.characters or []:
                add("cast", c.name)
            for p in sh.props or []:
                add("prop", p.name)
    return entities


def _candidate(asset: Dict[str, str], score: float) -> Dict[str, Any]:
    return {"name": asset["name"], "image_file": asset["image_file"], "score": round(score, 2)}


def _match_out(
    status: str,
    *,
    matched: bool,
    asset_name: Any,
    image_file: Any,
    confidence: float,
    candidates: List[Dict[str, Any]],
    match_kind: str,
) -> Dict[str, Any]:
    return {
        "status": status,
        "matched": matched,
        "asset_name": asset_name,
        "image_file": image_file,
        "confidence": confidence,
        "candidates": candidates,
        "match_kind": match_kind,
        "suggest": match_kind in ("location_hierarchy", "generic_reference"),
    }


def match_one(
    entity_name: str, library: List[Dict[str, Any]], etype: Any = None
) -> Dict[str, Any]:
    """单实体 → 资产库匹配（Phase 1.1：类型硬约束 + 语义判定 + match_kind/suggest）。

    etype：实体类型（EntityType 之一）。传 None 时不做类型硬约束
    （兼容直接调 match_one 的旧测试/旧调用；match_plan 一定传真实类型）。
    匹配分：① 精确全等 1.0 auto → ② 归一后精确 0.95 auto →
    ③ 子串包含 0.8 pending（语义判定分流：semantic_incompatible 剔除 /
    location_hierarchy / generic_reference）→ 无候选 none。
    """
    key = _key(entity_name)

    # ① 精确全等（「林雪」→「林雪」）
    for a in library:
        if _key(a["name"]) == key and _type_compatible(etype, a.get("kind", "unknown")):
            return _match_out(
                "auto", matched=True, asset_name=a["name"], image_file=a["image_file"],
                confidence=1.0, candidates=[_candidate(a, 1.0)], match_kind="exact",
            )

    # ② 归一后精确（「角色_XILNAR_主视角」→ 实体「XILNAR」；「林雪图」→「林雪」）
    en = _norm_asset_name(entity_name)
    ekey = _key(en)
    if ekey:
        for a in library:
            if not _type_compatible(etype, a.get("kind", "unknown")):
                continue
            akey = _key(_norm_asset_name(a["name"]))
            if akey and akey == ekey:
                return _match_out(
                    "auto", matched=True, asset_name=a["name"], image_file=a["image_file"],
                    confidence=0.95, candidates=[_candidate(a, 0.95)], match_kind="normalized",
                )

    # ③ 子串包含（双向，名长 >= 2；「站台」⊂「地铁站台」→ 0.8 低置信度候选）
    scored: List[Dict[str, Any]] = []
    for a in library:
        if not _type_compatible(etype, a.get("kind", "unknown")):
            continue
        akey = _key(_norm_asset_name(a["name"]))
        if not akey or not ekey:
            continue
        if len(ekey) >= 2 and len(akey) >= 2 and (ekey in akey or akey in ekey):
            rel = _judge_relation(etype, ekey, akey)
            if rel == "semantic_incompatible":
                continue  # 语义不同物（旧剑→旧剑匣）：候选剔除，不进入 candidates
            scored.append((a, 0.8, rel))

    if scored:
        scored.sort(key=lambda x: (-x[1], x[0]["name"]))
        top = scored[0]
        auto = top[1] >= CONF_AUTO
        top_kind = top[2] if top[2] != "contains" else "contains"
        return _match_out(
            "auto" if auto else "pending",
            matched=auto,
            asset_name=top[0]["name"],
            image_file=top[0]["image_file"],
            confidence=top[1],
            candidates=[_candidate(a[0], a[1]) for a in scored[:5]],
            match_kind=top_kind,
        )

    return _match_out(
        "none", matched=False, asset_name=None, image_file=None,
        confidence=0.0, candidates=[], match_kind="none",
    )


def _cap_pending(m: Dict[str, Any]) -> Dict[str, Any]:
    """实体置信度不足（can_auto=False）→ 资产匹配结果最高 pending（绝不自动绑定）。"""
    if m.get("status") == "auto":
        return {**m, "status": "pending", "matched": False}
    return m


def match_plan(
    plan: Any,
    library: List[Dict[str, Any]] | None = None,
    registry: Any = None,
) -> Dict[str, Any]:
    """ProductionPlan → {assets, matches, visual_elements, funnel}。

    实体来源 = entity_cleanse 注册表（清洗 + 类型纠正 + 置信度门控 +
    asset_requirement 分流），不再直接消费 shot.characters/props。
    matches 每条透出 entity_type/entity_source/entity_confidence/asset_requirement/
    can_auto/match_kind/suggest（Phase 1.1 前端分组展示 + 建议参考），以及
    Phase 2-1 新增 entity_key/asset_id/binding_status/binding_source
    （asset_registry 持久化确认；见 #135）。
    visual_elements = Task B 视觉元素（只进 Prompt，不进匹配）。
    funnel 统计随匹配结果回填 auto/pending/none。

    ⛔ Phase 2-1（用户拍板）：registry 只「记住确认」，绝不反向生成实体——
    实体仍全部来自 build_entity_registry；registry 仅在有 accepted binding 时
    覆盖该实体的匹配结果（match_kind="persisted"）。registry=None → 行为不变。
    """
    library = library if library is not None else scan_asset_library()
    reg = build_entity_registry(plan)
    # 资产 kind 补齐（前缀 + entered 实体名逆向锚定）
    library = _infer_asset_kinds(library, reg["entries"])
    by_image: Dict[str, Dict[str, Any]] = {}
    if registry is not None:
        # 资产库每条分配/复用永久 asset_id（同一 image_file → 同一 asset_id）
        for a in library:
            a["asset_id"] = registry.ensure_asset_id(
                a.get("image_file", ""), a.get("name", ""), a.get("kind", "unknown")
            )
        by_image = {a.get("image_file"): a for a in library if a.get("image_file")}
        # Phase 2-1 根因修复（#381c）：asset_id 必须立即落盘。binding 路由用
        # default_registry() 新建实例从磁盘 load；scan 若只 ensure_asset_id 不 save，
        # asset_id 仅存活于本实例内存 → set_binding 在新实例里找不到 → 400 bad_asset_id。
        # save() 在 dirty=False 时幂等（无新分配/无快照变更 → 不写盘）。
        registry.save()
    funnel: Dict[str, Any] = dict(reg["funnel"])
    matches: List[Dict[str, Any]] = []
    for e in reg["entries"]:
        if not e["entered"]:
            continue
        ekey = entity_key_from_entry(e)
        binding = registry.get_binding(ekey) if registry is not None else None
        m = None
        if binding and binding.get("status") == "accepted" and binding.get("asset_id"):
            # 用户已确认 → 覆盖规则匹配：直接绑定该资产（match_kind="persisted"）
            basset = registry.asset_by_id(binding["asset_id"])
            if basset:
                target = by_image.get(basset.get("image_file"))
                if target:
                    m = _match_out(
                        "auto", matched=True, asset_name=target["name"],
                        image_file=target["image_file"], confidence=1.0,
                        candidates=[_candidate(target, 1.0)], match_kind="persisted",
                    )
        if m is None:
            m = match_one(e["name"], library, etype=e["type"])
            if not e["can_auto"]:
                m = _cap_pending(m)
        # asset_id：命中的资产记录里的永久 id（auto/persisted 有；pending 建议候选也有）
        asset_id = None
        if m.get("image_file") and by_image:
            a0 = by_image.get(m["image_file"])
            if a0:
                asset_id = a0.get("asset_id")
        matches.append(
            {
                "kind": kind_for_type(e["type"]),
                "name": e["name"],
                "entity_id": e.get("entity_id", ""),
                "entity_key": ekey,
                "entity_type": e["type"],
                "entity_source": e["source"],
                "entity_confidence": e["confidence"],
                "asset_requirement": e.get(
                    "asset_requirement", AssetRequirement.REQUIRED
                ),
                "can_auto": e["can_auto"],
                "asset_id": asset_id,
                "binding_status": (binding or {}).get("status"),
                "binding_source": (binding or {}).get("source"),
                **m,
            }
        )
    funnel["auto"] = sum(1 for m in matches if m["status"] == "auto")
    funnel["pending"] = sum(1 for m in matches if m["status"] == "pending")
    funnel["none"] = sum(1 for m in matches if m["status"] == "none")
    return {
        "assets": library,
        "matches": matches,
        "visual_elements": reg.get("visual_elements", []),
        "funnel": funnel,
    }
