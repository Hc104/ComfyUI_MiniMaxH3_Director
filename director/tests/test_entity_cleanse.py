#!/usr/bin/env python3
"""V1.7 实体抽取加固 · entity_cleanse 单元测试（纯规则，#356）。

覆盖（用户 2026-08-11 实体抽取加固评审结论）：
1. INVALID_ENTITY_PATTERNS：镜头一/镜头 1/shot_1/scene_2/第 3 镜 → 丢弃；
2. 类型纠正：已知角色/场景地点优先；强后缀纠正（青衫→costume、檐角→architecture、
   雾气→effect、烛火→effect）；烛台不误判；
3. canonical 去重：跨镜头同名实体合并，aliases 累积；
4. 置信度门控：inferred 不进匹配、conf<0.60 不进匹配、conf 0.60~0.85 只能 pending
   （can_auto=False）、>=0.85 允许自动；
5. 双模式：无实体 plan 回退 legacy（地点/角色/道具全进）；有实体 plan 只走注册表；
6. 漏斗统计：discovered/invalid_dropped/dedup_dropped/seeded/cleansed/entered/excluded。

直接用系统 python 运行（无第三方依赖，不连任何模型）：
    python3 director/tests/test_entity_cleanse.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import entity_cleanse as ec  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    ProjectInfo,
    Prop,
    Scene,
    Shot,
    Validation,
    VisualElement,
)

PASSED = 0
FAILED = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name} {detail}")


def _ent(name, etype=EntityType.PROP, source=EntitySource.SCRIPT, conf=0.9,
         eid=""):
    return Entity(entity_id=eid, name=name, type=etype, source=source, confidence=conf)


def _qwen_plan(entities):
    """带 Qwen 实体的 plan（1 场景 1 镜）。"""
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
            time="黄昏", weather="雨",
            shots=[Shot(shot_id="shot_01", source_text="原文", duration_sec=5,
                        characters=[Character(name="沈青崖")], entities=entities)],
        )],
        validation=Validation(),
    )


# ---------------- 1. 伪实体规则 ----------------
def test_invalid_patterns() -> None:
    print("\n[1] INVALID_ENTITY_PATTERNS：镜头编号伪实体丢弃")
    for bad in ("镜头一", "镜头 1", "镜头1", "镜头", "第 3 镜", "第二场", "shot_1",
                "shot-2", "scene_3", "景4", "5镜", "2 场", "123"):
        _check(f"丢弃 {bad}", ec.is_invalid_entity_name(bad), bad)
    for good in ("沈青崖", "山雨楼", "旧剑匣", "青衫", "檐角"):
        _check(f"保留 {good}", not ec.is_invalid_entity_name(good), good)
    _check("空串丢弃", ec.is_invalid_entity_name("  "))


# ---------------- 2. 类型纠正 ----------------
def test_hint_type() -> None:
    print("\n[2] hint_type：UNKNOWN 后缀启发式")
    _check("青衫→costume", ec.hint_type("青衫") == EntityType.COSTUME)
    _check("檐角→architecture", ec.hint_type("檐角") == EntityType.ARCHITECTURE)
    _check("雾气→effect", ec.hint_type("雾气") == EntityType.EFFECT)
    _check("木桌→prop", ec.hint_type("木桌") == EntityType.PROP)
    _check("无命中→unknown", ec.hint_type("奇奇怪怪") == EntityType.UNKNOWN)


def test_correct_type_priority() -> None:
    print("\n[3] correct_type：角色/地点优先 + 强后缀纠正 + 不误判")
    plan = _qwen_plan([])
    # 已知角色覆盖
    _check("沈青崖→character", ec.correct_type(plan, "沈青崖", EntityType.PROP)
           == EntityType.CHARACTER)
    # 场景地点名覆盖（含互含：山雨楼 ⊂ 山雨楼外）
    _check("山雨楼→location（含于场景地点）", ec.correct_type(plan, "山雨楼", EntityType.PROP)
           == EntityType.LOCATION)
    _check("山雨楼外→location（精确）", ec.correct_type(plan, "山雨楼外", EntityType.PROP)
           == EntityType.LOCATION)
    # 强后缀纠正 prop 误判
    _check("青衫 prop→costume", ec.correct_type(plan, "青衫", EntityType.PROP)
           == EntityType.COSTUME)
    _check("檐角 prop→architecture", ec.correct_type(plan, "檐角", EntityType.PROP)
           == EntityType.ARCHITECTURE)
    _check("烛火 prop→effect", ec.correct_type(plan, "烛火", EntityType.PROP)
           == EntityType.EFFECT)
    # 台/门 不误判
    _check("烛台保持 prop", ec.correct_type(plan, "烛台", EntityType.PROP) == EntityType.PROP)
    _check("大门保持 prop", ec.correct_type(plan, "大门", EntityType.PROP) == EntityType.PROP)
    # Qwen 已判 character 不强行改
    _check("柳如烟 character 保持", ec.correct_type(plan, "柳如烟", EntityType.CHARACTER)
           == EntityType.CHARACTER)
    # 非法 type → 当 unknown
    _check("非法 type 当 unknown → 启发式", ec.correct_type(plan, "青衫", "bogus")
           == EntityType.COSTUME)


# ---------------- 3. 双模式注册表 ----------------
def test_registry_legacy_mode() -> None:
    print("\n[4] 无 Qwen 实体 plan → legacy 回退（地点/角色/道具全进注册表）")
    plan = ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", location_name="客栈大堂", time="黄昏", weather="雨",
            shots=[
                Shot(shot_id="shot_01", source_text="x", duration_sec=5,
                     characters=[Character(name="林雪")], props=[Prop(name="油纸伞")]),
                Shot(shot_id="shot_02", source_text="y", duration_sec=5,
                     characters=[Character(name="林雪"), Character(name="陈默")], props=[]),
            ],
        )],
        validation=Validation(),
    )
    reg = ec.build_entity_registry(plan)
    _check("discovered=0（无 Qwen 实体）", reg["funnel"]["discovered"] == 0)
    _check("cleansed=4（林雪/陈默/客栈大堂/油纸伞）", reg["funnel"]["cleansed"] == 4,
           str([e["name"] for e in reg["entries"]]))
    _check("全部 entered", reg["funnel"]["entered"] == 4)
    names = sorted(e["name"] for e in reg["entries"])
    _check("地点/角色/道具齐全", names == ["客栈大堂", "林雪", "油纸伞", "陈默"], str(names))
    _check("油纸伞 kind→prop", ec.kind_for_type(
        next(e["type"] for e in reg["entries"] if e["name"] == "油纸伞")) == "prop")


def test_registry_qwen_mode() -> None:
    print("\n[5] 有 Qwen 实体 plan → 清洗/去重/门控/漏斗")
    plan = _qwen_plan([
        _ent("沈青崖", EntityType.CHARACTER, conf=0.9, eid="ent_001"),
        _ent("镜头一", EntityType.CHARACTER, conf=0.8),   # invalid → 丢弃
        _ent("青衫", EntityType.PROP, conf=0.7),          # → costume, can_auto False
        _ent("山雨楼", EntityType.PROP, conf=0.6),        # → location, can_auto False
        _ent("雾气", EntityType.EFFECT, EntitySource.INFERRED, 0.5),  # inferred 不进
        _ent("旧剑匣", EntityType.PROP, conf=0.95),       # entered + can_auto
        _ent("沈青崖", EntityType.CHARACTER, conf=0.85),  # 重复 → dedup
    ])
    reg = ec.build_entity_registry(plan)
    f = reg["funnel"]
    _check("discovered=7", f["discovered"] == 7, str(f))
    _check("invalid_dropped=1（镜头一）", f["invalid_dropped"] == 1, str(f))
    _check("dedup_dropped=1（沈青崖重复）", f["dedup_dropped"] == 1, str(f))
    _check("seeded=1（山雨楼外地点补位；沈青崖 Qwen 已返回不补）", f["seeded"] == 1, str(f))
    _check("cleansed=6", f["cleansed"] == 6, str(f["cleansed"]))
    _check("entered=3（山雨楼外/沈青崖/旧剑匣；青衫/山雨楼/雾气→视觉元素）",
           f["entered"] == 3, str(f))
    _check("visual_only=3（青衫 costume/山雨楼 env/雾气 effect→none）",
           f["visual_only"] == 3, str(f))
    _check("excluded=0", f["excluded"] == 0, str(f))

    by = {e["name"]: e for e in reg["entries"]}
    _check("镜头一不在注册表", "镜头一" not in by)
    _check("沈青崖 character + can_auto", by["沈青崖"]["type"] == EntityType.CHARACTER
           and by["沈青崖"]["can_auto"] is True)
    _check("青衫 costume + none 不进匹配", by["青衫"]["type"] == EntityType.COSTUME
           and by["青衫"]["entered"] is False
           and by["青衫"]["asset_requirement"] == "none")
    _check("山雨楼→environment（精确对齐，山雨楼⊄山雨楼外）",
           by["山雨楼"]["type"] == EntityType.ENVIRONMENT
           and by["山雨楼"]["entered"] is False)
    _check("雾气 inferred + none 不进匹配", by["雾气"]["entered"] is False
           and by["雾气"]["asset_requirement"] == "none")
    ve_names = [v["name"] for v in reg["visual_elements"]]
    _check("青衫/山雨楼/雾气 进 visual_elements 清单",
           all(n in ve_names for n in ("青衫", "山雨楼", "雾气")), str(ve_names))
    _check("山雨楼外 location 补位", by["山雨楼外"]["type"] == EntityType.LOCATION
           and by["山雨楼外"]["confidence"] == 1.0)
    _check("旧剑匣 entered + can_auto", by["旧剑匣"]["entered"] and by["旧剑匣"]["can_auto"])


def test_registry_dedup_aliases() -> None:
    print("\n[6] 同名合并累积 aliases + 地点精确对齐降级")
    plan = _qwen_plan([
        _ent("山雨楼", EntityType.LOCATION, conf=0.6),
        _ent("山雨楼", EntityType.PROP, conf=0.9),  # 同名不同 type → 合并取高置信
    ])
    reg = ec.build_entity_registry(plan)
    by = {e["name"]: e for e in reg["entries"]}
    _check("山雨楼唯一", sum(1 for e in reg["entries"] if e["name"] == "山雨楼") == 1)
    _check("取高置信 0.9", by["山雨楼"]["confidence"] == 0.9, str(by["山雨楼"]))
    _check("山雨楼→environment（精确对齐，山雨楼⊄山雨楼外）",
           by["山雨楼"]["type"] == EntityType.ENVIRONMENT
           and by["山雨楼"]["entered"] is False)
    _check("山雨楼进 visual_elements", any(
        v["name"] == "山雨楼" for v in reg["visual_elements"]))


def test_visual_elements_skip_scene_locations() -> None:
    print("\n[8] P0-2：正式 Scene 地点不再进 visual_elements（去重）")
    plan = ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", location_name="客栈大堂", time="黄昏", weather="雨",
            shots=[Shot(shot_id="shot_01", source_text="x", duration_sec=5,
                        visual_elements=[
                            VisualElement(name="客栈大堂", type=EntityType.LOCATION,
                                          confidence=0.9),
                            VisualElement(name="山道", type=EntityType.ENVIRONMENT,
                                          confidence=0.8),
                        ])],
        )],
        validation=Validation(),
    )
    reg = ec.build_entity_registry(plan)
    ve_names = [v["name"] for v in reg["visual_elements"]]
    _check("客栈大堂（正式地点）不在 visual_elements", "客栈大堂" not in ve_names, str(ve_names))
    _check("山道保留在 visual_elements", "山道" in ve_names, str(ve_names))


def test_p03b_action_phrase_cleanse() -> None:
    """P0-3b（用户 2026-08-11 拍板）：动作/空间短语不得进入实体名（纯名词规则）。

    用户 5 个回归用例 + 单字道具 + 纯名词实体原样保留 + 整名动作短语丢弃。
    """
    print("\n[9] P0-3b 动作短语→实体名清洗（纯名词规则，无黑名单）")
    cases = [
        ("持灯退到柜台边", "灯"),
        ("端茶走到桌边", "茶"),
        ("拔刀扑来", "刀"),
        ("背着旧剑匣走来", "旧剑匣"),
        ("拿起茶碗放在桌上", "茶碗"),
        ("灯退到柜", "灯"),
        ("剑走向门", "剑"),
        ("持灯", "灯"),
        ("端茶", "茶"),
    ]
    for src, want in cases:
        got = ec._cleanse_action_phrase_name(src)
        _check(f"cleanse({src!r})={want!r}", got == want, f"got={got!r}")
    # 纯名词实体（不含动作动词）原样保留
    for src in ("柳如烟", "客栈大堂", "山雨楼", "木桌", "长凳", "灯笼",
                "旧剑匣", "后院石阶", "檐角", "青瓷茶碗", "沈青崖", "蒙面人"):
        got = ec._cleanse_action_phrase_name(src)
        _check(f"cleanse({src!r}) 原样保留", got == src, f"got={got!r}")
    # 整名都是动作短语 → None（丢弃，计入 invalid_dropped）
    _check("整名动作短语丢弃（拿着→None）",
           ec._cleanse_action_phrase_name("拿着") is None)


def test_p03b_visual_elements_skip_suffixed_locations() -> None:
    """P0-3b：正式 Scene 地点的带中文类型后缀变体（后院石阶环境/客栈大堂环境/
    山雨楼建筑）不得进 visual_elements；非正式地点（山道环境/檐角建筑/雾气环境）保留。
    """
    print("\n[10] P0-3b：正式地点带后缀变体不进 visual_elements")
    plan = ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[
            Scene(scene_id="scene_01", location_name="山雨楼外", time="黄昏", weather="雨",
                  shots=[Shot(shot_id="shot_01", source_text="x", duration_sec=5,
                              visual_elements=[
                                  VisualElement(name="山雨楼建筑", type=EntityType.ARCHITECTURE,
                                                confidence=0.9),
                                  VisualElement(name="山道环境", type=EntityType.ENVIRONMENT,
                                                confidence=0.8),
                              ])]),
            Scene(scene_id="scene_02", location_name="客栈大堂", time="夜", weather="雨",
                  shots=[Shot(shot_id="shot_02", source_text="x", duration_sec=5,
                              visual_elements=[
                                  VisualElement(name="客栈大堂环境", type=EntityType.ENVIRONMENT,
                                                confidence=0.9),
                                  VisualElement(name="檐角建筑", type=EntityType.ARCHITECTURE,
                                                confidence=0.7),
                              ])]),
            Scene(scene_id="scene_03", location_name="后院石阶", time="清晨", weather="雨",
                  shots=[Shot(shot_id="shot_03", source_text="x", duration_sec=5,
                              visual_elements=[
                                  VisualElement(name="后院石阶环境", type=EntityType.ENVIRONMENT,
                                                confidence=0.9),
                                  VisualElement(name="雾气环境", type=EntityType.EFFECT,
                                                confidence=0.6),
                              ])]),
        ],
        validation=Validation(),
    )
    reg = ec.build_entity_registry(plan)
    ve_names = [v["name"] for v in reg["visual_elements"]]
    for skip in ("山雨楼建筑", "客栈大堂环境", "后院石阶环境"):
        _check(f"{skip}（正式地点后缀变体）不在 visual_elements",
               skip not in ve_names, str(ve_names))
    for keep in ("山道环境", "檐角建筑", "雾气环境"):
        _check(f"{keep}（非正式地点）保留在 visual_elements",
               keep in ve_names, str(ve_names))


def test_character_alias_normalization() -> None:
    """Phase 2-2 commit 2（用户 2026-08-11 拍板「Alias 归一」）：角色称谓变体归一到规则层锚点。

    Qwen 同一角色跨解析可能给出不同写法：柳如烟/柳姑娘、沈青崖/沈大侠、蒙面人/蒙面客。
    锚点 = 剧本规则层 shot.characters（脚本上下文驱动，不做大规模 Alias 库）。
    归一到锚点显示名，原样名进 aliases → entity_key 跨解析稳定，绑定不分裂。
    歧义（多个锚点同前缀/同后缀）不猜，保持原样。
    """
    print("\n[11] Phase 2-2 Alias 归一：角色称谓变体 → 规则层锚点")

    # 直接测 resolve_character_alias（纯函数）
    anchors = {
        "柳如烟": "柳如烟",
        "沈青崖": "沈青崖",
        "蒙面人": "蒙面人",
    }
    cases = [
        ("柳姑娘", "柳如烟"),   # 前缀：柳 → 柳如烟
        ("柳小姐", "柳如烟"),   # 前缀：柳 → 柳如烟
        ("沈大侠", "沈青崖"),   # 前缀：沈 → 沈青崖
        ("沈公子", "沈青崖"),   # 前缀：沈 → 沈青崖
        ("蒙面客", "蒙面人"),   # 剥 客 → 蒙面 → 蒙面人
        ("柳如烟", "柳如烟"),   # 已规范名不动
        ("老板娘", "老板娘"),   # 无锚点命中 → 原样
        ("青衫", "青衫"),       # 非角色名 → 原样
    ]
    for src, want in cases:
        got = ec.resolve_character_alias(src, anchors)
        _check(f"resolve({src!r}) = {want!r}", got == want, f"got={got!r}")

    # 歧义：两个柳姓锚点 → 柳姑娘 不猜
    amb = {"柳如烟": "柳如烟", "柳无痕": "柳无痕"}
    _check("歧义（柳如烟/柳无痕）→ 柳姑娘 不猜",
           ec.resolve_character_alias("柳姑娘", amb) == "柳姑娘")
    # 空锚点 → 原样
    _check("空锚点表 → 原样", ec.resolve_character_alias("柳姑娘", {}) == "柳姑娘")

    # 注册表级：Qwen 给出变体，规则层锚点有规范名 → 合并成一条规范条目
    plan = ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", location_name="山雨楼外", time="黄昏", weather="雨",
            shots=[Shot(shot_id="shot_01", source_text="x", duration_sec=5,
                        characters=[Character(name="柳如烟"), Character(name="沈青崖"),
                                    Character(name="蒙面人")],
                        entities=[
                            _ent("柳姑娘", EntityType.CHARACTER, conf=0.9),
                            _ent("沈大侠", EntityType.CHARACTER, conf=0.85),
                            _ent("蒙面客", EntityType.CHARACTER, conf=0.8),
                        ])],
        )],
        validation=Validation(),
    )
    reg = ec.build_entity_registry(plan)
    names = [e["name"] for e in reg["entries"]]
    _check("柳姑娘 归一为 柳如烟", "柳如烟" in names and "柳姑娘" not in names, str(names))
    _check("沈大侠 归一为 沈青崖", "沈青崖" in names and "沈大侠" not in names, str(names))
    _check("蒙面客 归一为 蒙面人", "蒙面人" in names and "蒙面客" not in names, str(names))
    lry = next(e for e in reg["entries"] if e["name"] == "柳如烟")
    _check("柳如烟 aliases 含 柳姑娘", "柳姑娘" in lry["aliases"], str(lry["aliases"]))
    sqy = next(e for e in reg["entries"] if e["name"] == "沈青崖")
    _check("沈青崖 aliases 含 沈大侠", "沈大侠" in sqy["aliases"], str(sqy["aliases"]))
    mmr = next(e for e in reg["entries"] if e["name"] == "蒙面人")
    _check("蒙面人 aliases 含 蒙面客", "蒙面客" in mmr["aliases"], str(mmr["aliases"]))


# ---------------- 4. kind 映射 ----------------
def test_kind_mapping() -> None:
    print("\n[7] kind_for_type：character→cast / location→location / 其余→prop")
    _check("character→cast", ec.kind_for_type(EntityType.CHARACTER) == "cast")
    _check("location→location", ec.kind_for_type(EntityType.LOCATION) == "location")
    for t in (EntityType.PROP, EntityType.COSTUME, EntityType.EFFECT,
              EntityType.ENVIRONMENT, EntityType.ARCHITECTURE,
              EntityType.VEHICLE, EntityType.CREATURE, EntityType.UNKNOWN):
        _check(f"{t}→prop", ec.kind_for_type(t) == "prop")


# ---------------- 主入口 ----------------
def main() -> None:
    test_invalid_patterns()
    test_hint_type()
    test_correct_type_priority()
    test_registry_legacy_mode()
    test_registry_qwen_mode()
    test_registry_dedup_aliases()
    test_kind_mapping()
    test_visual_elements_skip_scene_locations()
    test_p03b_action_phrase_cleanse()
    test_p03b_visual_elements_skip_suffixed_locations()
    test_character_alias_normalization()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
