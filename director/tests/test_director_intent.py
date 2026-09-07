#!/usr/bin/env python3
"""P0-① DirectorIntent 构建 + 实体边界过滤测试（用户 2026-08-13 拍板 ①/②）。

覆盖：
1. build_shot_intent 各字段来源标注（USER/RULE/AI/MIX）+ source_text 逐字保底；
2. char_anchors=None → entities 原样投影（向后兼容，不破坏旧调用）；
3. char_anchors 传入 → Qwen 跨镜越界实体（旧剑匣混进镜头 1）丢弃、原文实体保留；
4. build_plan_intents 全链路：真实长度 source_text + 跨镜越界实体 → 丢弃；
   角色别名变体（柳姑娘→柳如烟）经锚点归一后合法保留；
5. 占位/测试短文本（<20 字符）不误杀（min_len 门槛）。

直接用系统 python 运行（无第三方依赖，纯规则零 LLM）：
    python3 director/tests/test_director_intent.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import director_intent as di  # noqa: E402
from director import entity_cleanse as ec  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Entity,
    EntitySource,
    EntityType,
    IntentSource,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
)

# 《山雨客栈》真实镜头原文（P0-① 回归基准）
SHOT1_SRC = ("雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，"
             "湿漉漉的石阶映着暖光，门前老槐树滴着水珠。")
SHOT4_SRC = ("柳如烟放下茶碗，从柜台后绕过，端着一碗热茶走向堂中，语气平淡："
             "「客官，落座歇脚，茶先暖着。」")
SHOT7_SRC = ("蒙面人一步步走近，沉声：「剑，交出来。」沈青崖起身，横剑护在柳如烟身前；"
             "柳如烟退后半步，手已按住柜台下的短刃。三人成三角对峙。")

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


def _plan() -> ProductionPlan:
    """1 场景 3 镜：shot_01 环境（含 Qwen 越界「旧剑匣」）、shot_04 对白人物、
    shot_07 多人物动作。"""
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
            time="黄昏", weather="雨",
            shots=[
                Shot(shot_id="shot_01", source_text=SHOT1_SRC, duration_sec=5,
                     entities=[
                         Entity(name="山雨楼", type=EntityType.ARCHITECTURE,
                                source=EntitySource.SCRIPT, confidence=0.95),
                         # Qwen 跨镜幻觉：旧剑匣是镜头 2 道具，混进镜头 1 → 应丢弃
                         Entity(name="旧剑匣", type=EntityType.PROP,
                                source=EntitySource.INFERRED, confidence=0.9),
                     ]),
                Shot(shot_id="shot_04", source_text=SHOT4_SRC, duration_sec=5,
                     characters=[Character(name="柳如烟")],
                     entities=[
                         Entity(name="柳如烟", type=EntityType.CHARACTER,
                                source=EntitySource.SCRIPT, confidence=0.95),
                         # 称谓变体：柳姑娘 → 锚点归一（柳如烟）→ 本镜原文含锚点 → 合法
                         Entity(name="柳姑娘", type=EntityType.CHARACTER,
                                source=EntitySource.INFERRED, confidence=0.8),
                     ]),
                Shot(shot_id="shot_07", source_text=SHOT7_SRC, duration_sec=5,
                     characters=[Character(name="沈青崖"),
                                 Character(name="柳如烟"),
                                 Character(name="蒙面人")],
                     entities=[
                         Entity(name="沈青崖", type=EntityType.CHARACTER,
                                source=EntitySource.SCRIPT, confidence=0.95),
                         Entity(name="旧剑匣", type=EntityType.PROP,
                                source=EntitySource.INFERRED, confidence=0.9),
                     ]),
            ],
        )],
        validation=Validation(),
    )


# ---------------- 1. 字段来源标注 + 原文逐字保底 ----------------
def test_shot_intent_basics() -> None:
    print("\n[1] build_shot_intent 字段来源标注 + source_text 逐字保底")
    plan = _plan()
    shot = plan.scenes[0].shots[0]
    scene = plan.scenes[0]
    intent = di.build_shot_intent(shot, scene)

    _check("user_original_intent = source_text 逐字",
           intent.user_original_intent == SHOT1_SRC)
    _check("retained_facts 以「雨刚停」开头", intent.retained_facts
           and intent.retained_facts[0].startswith("雨刚停"))
    _check("provenance.user_original_intent = USER",
           intent.provenance["user_original_intent"] == IntentSource.USER)
    _check("provenance.emotion = AI", intent.provenance["emotion"] == IntentSource.AI)
    _check("provenance.camera_position = RULE",
           intent.provenance["camera_position"] == IntentSource.RULE)
    _check("provenance.action = MIX", intent.provenance["action"] == IntentSource.MIX)
    _check("subject 含「山雨楼」", "山雨楼" in intent.subject)
    _check("location = 山雨楼外", intent.location == "山雨楼外")
    _check("audio.ambient 含雨声（weather=雨→规则）", "雨" in intent.audio["ambient"])


# ---------------- 2. char_anchors=None 向后兼容 ----------------
def test_no_filter_backward_compat() -> None:
    print("\n[2] char_anchors=None → entities 原样投影（向后兼容）")
    plan = _plan()
    shot = plan.scenes[0].shots[0]
    scene = plan.scenes[0]
    intent = di.build_shot_intent(shot, scene)  # 不带 char_anchors
    names = [e["name"] for e in intent.entities]
    _check("旧剑匣保留（不过滤）", "旧剑匣" in names, str(names))
    _check("山雨楼保留", "山雨楼" in names, str(names))


# ---------------- 3. 边界过滤：越界实体丢弃 ----------------
def test_entities_boundary_filter() -> None:
    print("\n[3] char_anchors 传入 → 越界实体丢弃、原文实体保留")
    plan = _plan()
    anchors = ec._character_anchor_map(plan)  # noqa: SLF001 - 测试直取规则锚点表
    shot = plan.scenes[0].shots[0]
    scene = plan.scenes[0]
    intent = di.build_shot_intent(shot, scene, char_anchors=anchors)
    names = [e["name"] for e in intent.entities]
    _check("山雨楼保留（本镜原文事实）", "山雨楼" in names, str(names))
    _check("旧剑匣丢弃（跨镜越界幻觉）", "旧剑匣" not in names, str(names))


# ---------------- 4. build_plan_intents 全链路 ----------------
def test_build_plan_intents_full_chain() -> None:
    print("\n[4] build_plan_intents 全链路：越界丢弃 + 别名归一合法")
    intents = di.build_plan_intents(_plan())

    s1 = intents["scene_01:shot_01"]
    _check("shot_01 entities 无旧剑匣",
           all(e["name"] != "旧剑匣" for e in s1.entities),
           str([e["name"] for e in s1.entities]))
    _check("shot_01 entities 含山雨楼",
           any(e["name"] == "山雨楼" for e in s1.entities))

    s4 = intents["scene_01:shot_04"]
    n4 = [e["name"] for e in s4.entities]
    _check("shot_04 含柳如烟（原文）", "柳如烟" in n4, str(n4))
    _check("shot_04 含柳姑娘（别名归一合法保留）", "柳姑娘" in n4, str(n4))

    s7 = intents["scene_01:shot_07"]
    _check("shot_07 无旧剑匣（本镜原文无此物）",
           all(e["name"] != "旧剑匣" for e in s7.entities),
           str([e["name"] for e in s7.entities]))
    _check("shot_07 含沈青崖", any(e["name"] == "沈青崖" for e in s7.entities))

    # 相机决策与 prompt_builder_v17 同源：首镜 establish
    _check("shot_01 continuity.camera_template = establish",
           s1.continuity["camera_template"] == "establish",
           str(s1.continuity))


# ---------------- 5. 占位短文本不误杀 ----------------
def test_placeholder_short_text_not_killed() -> None:
    print("\n[5] 占位/测试短文本（<20 字符）不误杀")
    plan = ProductionPlan(
        project=ProjectInfo(title="t", source_file=""),
        scenes=[Scene(scene_id="s1", title="t", location_name="x", time="", weather="",
                      shots=[Shot(shot_id="s01", source_text="原文", duration_sec=5,
                                  entities=[Entity(name="任意实体", type=EntityType.PROP,
                                                   source=EntitySource.INFERRED,
                                                   confidence=0.9)])])],
        validation=Validation(),
    )
    intents = di.build_plan_intents(plan)
    names = [e["name"] for e in intents["s1:s01"].entities]
    _check("短原文跳过边界检查（不误杀）", "任意实体" in names, str(names))


def main() -> None:
    test_shot_intent_basics()
    test_no_filter_backward_compat()
    test_entities_boundary_filter()
    test_build_plan_intents_full_chain()
    test_placeholder_short_text_not_killed()
    print(f"\n合计：{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()