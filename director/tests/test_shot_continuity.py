#!/usr/bin/env python3
"""P0-③ Shot-to-Shot 连贯性测试（H3_PIPELINE_AUDIT §五 P0-③，用户 2026-08-13 拍板）。

覆盖：
1. _transition_type 各分支（对切/反打/景别节奏/反应/延续/动作重叠/首镜空）；
2. _prev_shot_state 结构化：只投影身份类字段，不含 source_text 逐字、不含动作；
3. _must_keep_items 必须保持清单（角色外观/场景/光线），may_change 不渲染；
4. build_plan_intents 场景内逐镜传递 + **场景切换重置**（新场景首镜无 prev）；
5. build_h3_prompt 渲染：有上一镜才输出跨镜延续块（「保持…」），首镜不渲染；
6. 向后兼容：无 prev_shot_state 的 intent 渲染行为不变。

直接用系统 python 运行（无第三方依赖，纯规则零 LLM）：
    python3 director/tests/test_shot_continuity.py
"""

from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import director_intent as di  # noqa: E402
from director import h3_prompt_builder as hb  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    DirectorIntent,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
)

# 《山雨客栈》真实镜头原文（P0-① 回归基准；P0-③ 用同一批验证连续性）
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


def _mk(
    *,
    subject: str = "柳如烟",
    chars: list | None = None,
    location: str = "山雨楼外",
    tmpl: str = "dialogue",
    actions: list | None = None,
    lighting: str = "",
) -> DirectorIntent:
    """构造带 continuity.camera_template 的最小 DirectorIntent（纯函数单测用）。"""
    return DirectorIntent(
        subject=subject,
        characters=chars or [],
        location=location,
        action=actions or [],
        lighting=lighting,
        continuity={"camera_template": tmpl},
    )


def _plan() -> ProductionPlan:
    """1 场景 3 镜：shot_01 环境、shot_04 对白、shot_07 多人物对峙。"""
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
            time="黄昏", weather="雨",
            shots=[
                Shot(shot_id="shot_01", source_text=SHOT1_SRC, duration_sec=5),
                Shot(shot_id="shot_04", source_text=SHOT4_SRC, duration_sec=5,
                     characters=[Character(name="柳如烟")],
                     dialogue=[Dialogue(speaker="柳如烟", text="客官，落座歇脚，茶先暖着。")]),
                Shot(shot_id="shot_07", source_text=SHOT7_SRC, duration_sec=5,
                     characters=[Character(name="沈青崖"),
                                 Character(name="柳如烟"),
                                 Character(name="蒙面人")],
                     dialogue=[Dialogue(speaker="蒙面人", text="剑，交出来。")]),
            ],
        )],
        validation=Validation(),
    )


def _plan_two_scenes() -> ProductionPlan:
    """2 场景：scene_01（shot_01/04）+ scene_02（shot_02 环境首镜）。"""
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[
            Scene(scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
                  time="黄昏", weather="雨",
                  shots=[
                      Shot(shot_id="shot_01", source_text=SHOT1_SRC, duration_sec=5),
                      Shot(shot_id="shot_04", source_text=SHOT4_SRC, duration_sec=5,
                           characters=[Character(name="柳如烟")],
                           dialogue=[Dialogue(speaker="柳如烟",
                                              text="客官，落座歇脚，茶先暖着。")]),
                  ]),
            Scene(scene_id="scene_02", title="堂内", location_name="山雨楼堂内",
                  time="夜", weather="雨",
                  shots=[Shot(shot_id="shot_02", source_text="烛火昏黄，堂中空无一人。",
                              duration_sec=5)]),
        ],
        validation=Validation(),
    )


# ---------------- 1. transition_type 推导各分支 ----------------
def test_transition_type_branches() -> None:
    print("\n[1] _transition_type 各分支（纯规则）")
    prev_dialogue = _mk(subject="柳如烟", chars=["柳如烟"], tmpl="dialogue")
    prev_os = _mk(subject="柳如烟", chars=["柳如烟"], tmpl="see_object")
    prev_close = _mk(subject="柳如烟", chars=["柳如烟"], tmpl="tense")
    prev_wide = _mk(subject="柳如烟", chars=["柳如烟"], tmpl="establish")
    prev_walk = _mk(subject="柳如烟", chars=["柳如烟"], tmpl="walk",
                    actions=["走过柜台"])

    _check("首镜（无 prev）→ 空串",
           di._transition_type(None, cur_template_id="establish",
                               cur_location="山雨楼外", cur_subject="山雨楼",
                               cur_characters=[]) == "",
           "应返回 ''")
    _check("上镜对白+本镜对白 → cut（正反打对切）",
           di._transition_type(prev_dialogue, cur_template_id="dialogue",
                               cur_location="山雨楼外", cur_subject="沈青崖",
                               cur_characters=["沈青崖"]) == "cut")
    _check("上镜过肩+本镜对白 → reverse（反打）",
           di._transition_type(prev_os, cur_template_id="dialogue",
                               cur_location="山雨楼外", cur_subject="沈青崖",
                               cur_characters=["沈青崖"]) == "reverse")
    _check("上镜特写+本镜全景 → close_to_wide",
           di._transition_type(prev_close, cur_template_id="establish",
                               cur_location="山雨楼外", cur_subject="柳如烟",
                               cur_characters=["柳如烟"]) == "close_to_wide")
    _check("上镜全景+本镜特写 → wide_to_close",
           di._transition_type(prev_wide, cur_template_id="tense",
                               cur_location="山雨楼外", cur_subject="柳如烟",
                               cur_characters=["柳如烟"]) == "wide_to_close")
    _check("上镜特写+本镜特写 → reaction",
           di._transition_type(prev_close, cur_template_id="emotion_close",
                               cur_location="山雨楼外", cur_subject="柳如烟",
                               cur_characters=["柳如烟"]) == "reaction")
    _check("同地点同主体无动作重叠 → continuation",
           di._transition_type(prev_walk, cur_template_id="walk",
                               cur_location="山雨楼外", cur_subject="柳如烟",
                               cur_characters=["柳如烟"]) == "continuation")
    _check("同地点同主体动作重叠 → match_action",
           di._transition_type(prev_walk, cur_template_id="walk",
                               cur_location="山雨楼外", cur_subject="柳如烟",
                               cur_characters=["柳如烟"],
                               cur_actions=["走过柜台"]) == "match_action")
    _check("换地点 → scene_cut（切入新场景）",
           di._transition_type(prev_walk, cur_template_id="walk",
                               cur_location="山雨楼堂内", cur_subject="柳如烟",
                               cur_characters=["柳如烟"]) == "scene_cut")


# ---------------- 2. prev_shot_state 结构化 ----------------
def test_prev_shot_state_structured() -> None:
    print("\n[2] _prev_shot_state 结构化投影（身份字段，无 source_text/动作）")
    prev = _mk(subject="柳如烟", chars=["柳如烟", "沈青崖"],
               location="山雨楼外", tmpl="dialogue", actions=["放下茶碗"])
    state = di._prev_shot_state(prev)
    _check("subject 投影", state["subject"] == "柳如烟", str(state))
    _check("characters 投影（保序）", state["characters"] == ["柳如烟", "沈青崖"], str(state))
    _check("location 投影", state["location"] == "山雨楼外")
    _check("camera_template 投影", state["camera_template"] == "dialogue")
    _check("无 source_text 逐字字段",
           all("source_text" not in k and "原文" not in str(v)
               for k, v in state.items()),
           str(state))
    _check("无动作字段（动作是 may_change 语义）",
           "action" not in state,
           str(state))


# ---------------- 3. must_keep 清单 ----------------
def test_must_keep_items() -> None:
    print("\n[3] _must_keep_items 必须保持清单")
    prev = _mk(subject="柳如烟", chars=["柳如烟"], location="山雨楼外",
               tmpl="dialogue", lighting="暖光")
    keep = di._must_keep_items(prev, cur_characters=["柳如烟", "沈青崖"],
                               cur_location="山雨楼外", cur_lighting="暖光")
    _check("共享角色外观一致", "柳如烟外观一致" in keep, str(keep))
    _check("相同地点场景不变", "山雨楼外场景不变" in keep, str(keep))
    _check("相同光线延续", "光线氛围延续" in keep, str(keep))

    keep2 = di._must_keep_items(prev, cur_characters=["沈青崖"],
                                cur_location="山雨楼堂内", cur_lighting="")
    _check("角色不共享→无外观一致", all("外观一致" not in k for k in keep2), str(keep2))
    _check("换地点→无场景不变", all("场景不变" not in k for k in keep2), str(keep2))
    _check("光线不同→无光线延续", all("光线" not in k for k in keep2), str(keep2))
    _check("may_change 设计语义不进 must_keep",
           all("镜头" not in k and "动作" not in k and "表情" not in k for k in keep2),
           str(keep2))


# ---------------- 4. build_plan_intents 场景内传递 + 场景切换重置 ----------------
def test_plan_intents_continuity_chain() -> None:
    print("\n[4] build_plan_intents 场景内传递 + 场景切换重置")
    plan = _plan_two_scenes()
    intents = di.build_plan_intents(plan)

    s1 = intents["scene_01:shot_01"]
    _check("场景首镜 continuity 无 transition_type",
           "transition_type" not in s1.continuity, str(s1.continuity))
    _check("场景首镜 continuity 无 prev_shot_state",
           "prev_shot_state" not in s1.continuity, str(s1.continuity))

    s4 = intents["scene_01:shot_04"]
    _check("scene_01 第 2 镜有 transition_type",
           s4.continuity.get("transition_type") in ("cut", "continuation"),
           str(s4.continuity))
    _check("scene_01 第 2 镜有 prev_shot_state",
           s4.continuity.get("prev_shot_state", {}).get("subject") == "山雨楼外",
           str(s4.continuity.get("prev_shot_state")))
    _check("scene_01 第 2 镜有 must_keep（山雨楼外场景不变）",
           any("山雨楼外场景不变" in k for k in s4.continuity.get("must_keep", [])),
           str(s4.continuity.get("must_keep")))
    _check("scene_01 第 2 镜有 may_change（设计语义不渲染）",
           bool(s4.continuity.get("may_change")), str(s4.continuity))

    s2 = intents["scene_02:shot_02"]
    # P0 Story Timeline：无 timeline → 退化场景树顺序；场景切换不再重置 prev，
    # 而是保留 timeline 画面前驱 → 切到新场景 = scene_cut（切入新场景）。
    _check("场景切换：新场景首镜 transition_type = scene_cut",
           s2.continuity.get("transition_type") == "scene_cut", str(s2.continuity))
    _check("场景切换：保留 timeline 画面前驱 prev_shot_state",
           s2.continuity.get("prev_shot_state", {}).get("location") == "山雨楼外",
           str(s2.continuity.get("prev_shot_state")))
    _check("场景切换：无 scene_prev_state（本场景首次出现）",
           "scene_prev_state" not in s2.continuity, str(s2.continuity))
    _check("场景切换：timeline_prev_shot_id 记录画面前驱",
           s2.continuity.get("timeline_prev_shot_id") == "scene_01:shot_04",
           str(s2.continuity))


# ---------------- 5. build_h3_prompt 跨镜延续渲染 ----------------
def test_h3_prompt_continuation_block() -> None:
    print("\n[5] build_h3_prompt 跨镜延续块（有 prev 渲染 / 首镜不渲染）")
    intents = di.build_plan_intents(_plan())

    desc1 = hb.build_h3_prompt(intents["scene_01:shot_01"]).integrated_multimodal_description
    _check("首镜 description 无「延续上一镜」", "延续上一镜" not in desc1, desc1)
    _check("首镜 description 无跨镜保持块", "跨镜延续" not in desc1, desc1)

    desc4 = hb.build_h3_prompt(intents["scene_01:shot_04"]).integrated_multimodal_description
    _check("第 2 镜含「保持山雨楼外场景不变」",
           "保持山雨楼外场景不变。" in desc4, desc4)
    _check("第 2 镜无「延续上一镜」延续句（无共享角色且主体不同）",
           "延续上一镜" not in desc4, desc4)

    desc7 = hb.build_h3_prompt(intents["scene_01:shot_07"]).integrated_multimodal_description
    _check("第 3 镜含「保持柳如烟外观一致」", "保持柳如烟外观一致" in desc7, desc7)
    _check("第 3 镜含「山雨楼外场景不变」", "山雨楼外场景不变" in desc7, desc7)
    _check("第 3 镜不逐字注入上一镜 source_text 碎片",
           all(frag not in desc7 for frag in ("放下茶碗", "绕过", "端着一碗热茶")),
           desc7)

    prov = hb.build_h3_prompt(intents["scene_01:shot_07"]).provenance
    rg = prov.get("integrated_multimodal_description", {}).get("rule_generated", [])
    _check("延续块 provenance 标 rule_generated",
           any("跨镜延续" in str(x) for x in rg), str(rg))


# ---------------- 6. 向后兼容：无 prev_shot_state 渲染不变 ----------------
def test_backward_compat_no_prev() -> None:
    print("\n[6] 无 prev_shot_state → _continuation_block 为空（向后兼容）")
    intent = _mk(subject="柳如烟", chars=["柳如烟"], location="山雨楼外", tmpl="dialogue")
    d = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)
    block = hb._continuation_block(d, "柳如烟为主体。")
    _check("无 prev_shot_state → 延续块空串", block == "", repr(block))

    # 手动构造带 prev 且类型不全的数据，确保不抛异常
    d2 = dict(d)
    d2["continuity"] = {
        "prev_shot_state": {"subject": "山雨楼", "characters": ["柳如烟"],
                            "location": "山雨楼外"},
        "transition_type": "continuation",
        "must_keep": ["柳如烟外观一致", "山雨楼外场景不变"],
    }
    block2 = hb._continuation_block(d2, "柳如烟为主体。")
    _check("有 prev_shot_state → 延续块非空", bool(block2), repr(block2))
    _check("延续块含镜头关系句", "延续上一镜的叙事" in block2, repr(block2))
    _check("延续块含必须保持清单",
           "保持柳如烟外观一致、山雨楼外场景不变。" in block2, repr(block2))


def main() -> None:
    test_transition_type_branches()
    test_prev_shot_state_structured()
    test_must_keep_items()
    test_plan_intents_continuity_chain()
    test_h3_prompt_continuation_block()
    test_backward_compat_no_prev()
    print(f"\n合计：{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
