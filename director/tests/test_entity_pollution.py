#!/usr/bin/env python3
"""P0-3 实体污染修复 · 专项测试（用户 2026-08-11 SPA 真实验收驱动）。

背景：SPA 真实页面显示「角色 11」（柳如烟/沈青崖 + 镜头一~九），地点误含「山道」。
后端 22/22 PASS 只验证了「正确角色被识别」，没验证「错误实体被排除」——这是测试设计漏洞。

本测试补上「错误实体必须被排除」的验收，覆盖三层：
1. 规则层源头（script_parser）：镜头标记行「镜头一：」不得被当角色/说话者。
2. script_analyzer._merge_characters：Qwen 幻觉的镜头标记/称谓不得进 shot.characters。
3. entity_cleanse.build_entity_registry：
   - shot.characters 已被污染（旧数据）→ 注册表角色兜底仍过滤（shot_marker/appellation）；
   - Qwen location 实体不对齐 Scene 正式地点（山道/檐角/门口）→ 降级 environment 不进匹配；
   - 精确对齐（P0-2）：仅 Scene 正式地点（山雨楼外）保留 location；山雨楼/石阶降级。
4. asset_matcher 端到端：matches 里不得出现「镜头一」「客官」「山道」。

直接用系统 python 运行（无第三方依赖，不连任何模型）：
    python3 director/tests/test_entity_pollution.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import entity_cleanse as ec  # noqa: E402
from director.asset_matcher import match_plan  # noqa: E402
from director.script_parser import parse_script  # noqa: E402
from director.script_analyzer import _merge_characters  # noqa: E402
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


def _ent(name, etype=EntityType.PROP, source=EntitySource.SCRIPT, conf=0.9):
    return Entity(name=name, type=etype, source=source, confidence=conf)


def _plan(scenes):
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=scenes,
        validation=Validation(),
    )


def _scene(shot, location_name="山雨楼外"):
    return Scene(scene_id="scene_01", title="第一场：山雨楼外",
                 location_name=location_name, time="黄昏", weather="雨",
                 shots=[shot])


def _shot(source_text, *, characters=None, entities=None, props=None):
    return Shot(shot_id="shot_01", source_text=source_text, duration_sec=5,
                characters=characters or [], entities=entities or [], props=props or [])


def test_rule_parser_shot_marker_not_character() -> None:
    print("\n[1] 规则层源头：镜头标记行「镜头一：」不是角色/对白")
    text = """# 第一场：山雨楼外

镜头一：远景，雨后的山雨楼矗立在山道尽头，檐角灯笼摇晃，暖黄灯火。
沈青崖负旧剑匣从山道走来，石阶停步。

镜头二：
柳如烟：客官，落座歇脚，茶先暖着。
"""
    plan = parse_script(text)
    scene = plan.scenes[0]
    _check("拆出 2 镜", len(scene.shots) == 2, str(len(scene.shots)))
    all_chars = []
    for sh in scene.shots:
        for c in sh.characters:
            all_chars.append(c.name)
    _check("任何 shot.characters 不含「镜头一」", "镜头一" not in all_chars, str(all_chars))
    _check("对白 speaker = 柳如烟（镜头标记不被当说话者）",
           any(d.speaker == "柳如烟" for sh in scene.shots for d in sh.dialogue),
           str([(d.speaker, d.text) for sh in scene.shots for d in sh.dialogue]))


def test_merge_characters_filters_qwen_hallucination() -> None:
    print("\n[2] script_analyzer._merge_characters：Qwen 幻觉不进 characters")
    rule = [Character(name="沈青崖"), Character(name="柳如烟")]
    qwen = [
        {"name": "沈青崖", "role": "剑客"},
        {"name": "镜头一", "role": "远景人物"},      # shot_marker
        {"name": "客官", "role": "称呼"},            # appellation
        {"name": "老板娘", "role": "掌柜"},           # appellation
        {"name": "蒙面人", "role": "不速之客"},
    ]
    out = _merge_characters(rule, qwen)
    names = [c.name for c in out]
    _check("保留真实角色（沈青崖/柳如烟/蒙面人）", names == ["沈青崖", "柳如烟", "蒙面人"],
           str(names))
    for bad in ("镜头一", "客官", "老板娘"):
        _check(f"过滤 {bad}", bad not in names)


def test_registry_seed_filters_polluted_characters() -> None:
    print("\n[3] entity_cleanse 兜底：shot.characters 已被污染（旧数据）→ 注册表仍干净")
    sh = _shot("原文", characters=[
        Character(name="沈青崖"), Character(name="镜头一"), Character(name="镜头二"),
        Character(name="客官"),
    ])
    plan = _plan([_scene(sh)])
    reg = ec.build_entity_registry(plan)
    chars = [e["name"] for e in reg["entries"] if e["type"] == EntityType.CHARACTER]
    _check("角色注册表只含沈青崖", chars == ["沈青崖"], str(chars))
    for bad in ("镜头一", "镜头二", "客官"):
        _check(f"注册表无 {bad}", bad not in [e["name"] for e in reg["entries"]])


def test_registry_qwen_entities_invalid_dropped() -> None:
    print("\n[4] Qwen entities 路径：镜头标记/称谓实体 → invalid_dropped 不进匹配")
    sh = _shot("原文", entities=[
        _ent("沈青崖", EntityType.CHARACTER, conf=0.95),
        _ent("镜头一", EntityType.CHARACTER, conf=0.95),
        _ent("镜头七", EntityType.CHARACTER, conf=0.95),
        _ent("客官", EntityType.CHARACTER, conf=0.8),
    ])
    plan = _plan([_scene(sh)])
    reg = ec.build_entity_registry(plan)
    names = [e["name"] for e in reg["entries"] if e["entered"]]
    _check("invalid_dropped >= 3（镜头一/镜头七/客官）", reg["funnel"]["invalid_dropped"] >= 3,
           str(reg["funnel"]))
    # entered = 沈青崖 + Scene 正式地点「山雨楼外」（合法 Location 资产）；
    # 镜头标记/称谓一律不得进入匹配。
    _check("entered 无镜头一/镜头七/客官",
           all(bad not in names for bad in ("镜头一", "镜头七", "客官")), str(names))
    _check("entered 含沈青崖 + 山雨楼外",
           all(n in names for n in ("沈青崖", "山雨楼外")), str(names))


def test_location_hierarchy_degradation() -> None:
    print("\n[5] Location 精确对齐（P0-2）：山雨楼/山道/檐角 均不对齐 → 降级 environment")
    sh = _shot("雨后的山雨楼矗立在山道尽头", entities=[
        _ent("山雨楼", EntityType.LOCATION, conf=0.95),   # ⊂山雨楼外 但非精确 → 降级
        _ent("山道", EntityType.LOCATION, conf=0.95),     # 不对齐 → 降级 environment
        _ent("檐角", EntityType.LOCATION, conf=0.85),     # 不对齐 → 降级 environment
    ])
    plan = _plan([_scene(sh, location_name="山雨楼外")])
    reg = ec.build_entity_registry(plan)
    by = {e["name"]: e for e in reg["entries"]}
    for nm in ("山雨楼", "山道", "檐角"):
        _check(f"{nm} 降级 environment（非精确对齐）",
               by.get(nm, {}).get("type") == EntityType.ENVIRONMENT, str(by.get(nm)))
        _check(f"{nm} 不进匹配", by.get(nm, {}).get("entered") is False)
    _check("山雨楼外保留 location（正式 Scene 地点）",
           by.get("山雨楼外", {}).get("type") == EntityType.LOCATION
           and by.get("山雨楼外", {}).get("entered") is True)
    ve_names = [v["name"] for v in reg["visual_elements"]]
    _check("山雨楼/山道/檐角 进 visual_elements（留给 Prompt）",
           all(n in ve_names for n in ("山雨楼", "山道", "檐角")), str(ve_names))
    _check("正式地点山雨楼外不在 visual_elements（不重复）", "山雨楼外" not in ve_names,
           str(ve_names))


def test_qwen_drop_keeps_rule_character() -> None:
    print("\n[7] P0-2 双轨制：Qwen 漏返回（蒙面人）→ 规则兜底仍保留角色")
    sh = _shot("沈青崖推门，柳如烟退后，蒙面人拔刀。",
               characters=[Character(name="沈青崖"), Character(name="柳如烟"),
                           Character(name="蒙面人")],
               entities=[_ent("沈青崖", EntityType.CHARACTER, conf=0.95)])
    plan = _plan([_scene(sh)])
    reg = ec.build_entity_registry(plan)
    chars = [e["name"] for e in reg["entries"] if e["type"] == EntityType.CHARACTER]
    for nm in ("沈青崖", "柳如烟", "蒙面人"):
        _check(f"{nm} 在角色注册表（Qwen 未返回也被规则保留）", nm in chars, str(chars))
    _check("沈青崖置信度 = Qwen 的 0.95（规则不覆盖 Qwen）",
           next(e["confidence"] for e in reg["entries"] if e["name"] == "沈青崖") == 0.95)
    _check("蒙面人置信度 = 规则兜底 0.95",
           next(e["confidence"] for e in reg["entries"] if e["name"] == "蒙面人")
           == ec.CONF_RULE_CHAR)


def test_match_plan_end_to_end_clean() -> None:
    print("\n[6] asset_matcher 端到端：matches 无镜头标记/称谓/未对齐地点")
    sh = _shot("原文", characters=[Character(name="沈青崖"), Character(name="柳如烟")],
               entities=[
                   _ent("沈青崖", EntityType.CHARACTER, conf=0.95),
                   _ent("柳如烟", EntityType.CHARACTER, conf=0.95),
                   _ent("镜头一", EntityType.CHARACTER, conf=0.95),
                   _ent("客官", EntityType.CHARACTER, conf=0.8),
                   _ent("山道", EntityType.LOCATION, conf=0.95),
                   _ent("旧剑匣", EntityType.PROP, conf=0.9),
               ])
    plan = _plan([_scene(sh)])
    res = match_plan(plan, library=[])
    names = [m["name"] for m in res["matches"]]
    _check("matches 无镜头一", "镜头一" not in names, str(names))
    _check("matches 无客官", "客官" not in names)
    _check("matches 无山道", "山道" not in names)
    _check("matches 含沈青崖/柳如烟/旧剑匣",
           all(n in names for n in ("沈青崖", "柳如烟", "旧剑匣")), str(names))
    _check("山道在 visual_elements", any(v["name"] == "山道" for v in res["visual_elements"]))


def main() -> int:
    print("P0-3 实体污染修复 · 专项验收")
    test_rule_parser_shot_marker_not_character()
    test_merge_characters_filters_qwen_hallucination()
    test_registry_seed_filters_polluted_characters()
    test_registry_qwen_entities_invalid_dropped()
    test_location_hierarchy_degradation()
    test_qwen_drop_keeps_rule_character()
    test_match_plan_end_to_end_clean()
    print("\n" + "=" * 60)
    print(f"结果: {PASSED} PASS / {FAILED} FAIL")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
