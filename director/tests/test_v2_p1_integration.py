#!/usr/bin/env python3
"""v2.0 P1 综合集成测试（CAMERA_RULES_V2 §4/§6/§7 接线，纯规则 mock，零显存）。

覆盖（P1-A ~ P1-D，用户 2026-08-17「验证通过开始P1」）：
1. decide_camera 张力决策（P1-A）：
   - tension=0 → 输出与 v1.0 逐字节一致（一键可关，严格向后兼容）；
   - reaction_forced=True → 景别锁特写 / 运动锁静止（反应镜硬规则）；
   - tension>=3 峰值定格：普通模板替换为特写+静止；已特写+静止 → 原样不重复措辞；
   - tension==2 上升缓推：静止镜改缓推，景别保留；establish/ending 不缓推。
2. build_plan_intents 四模块接线（P1-B）：
   - tension/rhythm/core_action/expression/reaction_forced 字段填充；
   - 连续对白/台词爆点 → 下一镜强制 reaction（shot_role=reaction + continuity.reaction_forced）；
   - 纯环境镜 → core_action/expression 空（严格向后兼容）。
3. _build_description 渲染升级（P1-C）：
   - style_profile=None → 画风块/防崩坏句全部跳过（旧链路不变）；
   - style_profile 非 None → 画风块在顶部（location 之后）、防崩坏双语在尾部
     （NO_SUBTITLE_RULE 之前）+ negative_rules 合并；
   - 核心动作/表情句：非空且与原文不逐字/不相似才渲染；
   - provenance rule_generated 记录画风块/防崩坏约束/核心动作/表情。
4. _merge_negative_rules：默认负面（命中 _ANTI_BREAK_KEYS）跳过，自定义负面保留。
5. build_plan_h3_prompts style_profile 透传（P1-C 路由层）。
6. ⛔ 纯规则零显存：5 个改动文件源码无 torch/ollama/网络引用。

直接用系统 python3 运行（无第三方依赖）：
    python3 director/tests/test_v2_p1_integration.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.beat_rhythm as br  # noqa: E402
import director.core_action as ca  # noqa: E402
import director.director_intent as di  # noqa: E402
import director.director_rules as dr  # noqa: E402
import director.h3_prompt_builder as h3  # noqa: E402
import director.psych_visualize as pv  # noqa: E402
import director.visual_style as vs  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    ProjectInfo,
    ProductionPlan,
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


# ---------- SimpleNamespace 夹具（decide_camera 直接测） ----------

def _char(name: str, role: str = "") -> SimpleNamespace:
    return SimpleNamespace(name=name, role=role)


def _shot(*, source_text: str = "", visual_intent: str = "", emotion: str = "",
          dialogue: tuple = (), characters: tuple = (), actions: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(
        source_text=source_text,
        visual_intent=visual_intent,
        emotion=emotion,
        dialogue=[SimpleNamespace(text=t) for t in dialogue],
        characters=list(characters),
        actions=list(actions),
        props=[],
    )


def _scene(location: str = "山雨楼大堂", time: str = "夜", weather: str = "雨") -> SimpleNamespace:
    return SimpleNamespace(location_name=location, time=time, weather=weather)


# ---------- ProductionPlan 真实链路夹具（build_plan_intents 全链） ----------
#
# 计划：shot_01 中性对白（info，张力 1）→ shot_02 台词爆点+愤怒（emotion，张力 3 build）
#       → shot_03 无对白有角色（爆点后 → plan_reaction_shots 强制 reaction）。
# 预期（beat_rhythm 纯规则推导）：
#   rhythm：shot_01 (1, hold)；shot_02 (3, build)；shot_03 (0, release)
#   forced：shot_03 被强制 reaction

def _plan() -> ProductionPlan:
    return ProductionPlan(
        project=ProjectInfo(title="P1综合", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼大堂", location_name="山雨楼大堂",
            time="夜", weather="雨",
            shots=[
                Shot(shot_id="shot_01",
                     source_text="林薇薇垂眸，轻声说，这么大的雨，赶路辛苦了。",
                     characters=[Character(name="林薇薇")],
                     dialogue=[Dialogue(speaker="林薇薇", text="这么大的雨，赶路辛苦了。")],
                     actions=[]),
                Shot(shot_id="shot_02",
                     source_text="林薇薇攥紧衣袖，愤怒道，凭什么这么对我！",
                     characters=[Character(name="林薇薇"), Character(name="沈青崖")],
                     dialogue=[Dialogue(speaker="林薇薇", text="凭什么这么对我！")],
                     emotion="愤怒",
                     actions=["林薇薇攥紧衣袖"]),
                Shot(shot_id="shot_03",
                     source_text="沈青崖愣在原地，说不出话。",
                     characters=[Character(name="沈青崖")],
                     emotion="愣住",
                     actions=["沈青崖愣在原地"]),
            ],
        )],
        validation=Validation(),
        timeline=["scene_01:shot_01", "scene_01:shot_02", "scene_01:shot_03"],
    )


# ---------- 1. decide_camera 张力决策（P1-A） ----------

def test_decide_tension0_backward_compat() -> None:
    print("\n[P1-A] decide_camera · tension=0 与 v1.0 逐字节一致：")
    shot = _shot(emotion="悲伤", characters=(_char("林薇薇"),))
    d0, i0, ns0 = dr.decide_camera(shot, _scene(), "emotion", None,
                                   is_last_shot=False, scene_has_prev=False)
    d1, i1, ns1 = dr.decide_camera(shot, _scene(), "emotion", None,
                                   is_last_shot=False, scene_has_prev=False, tension=0)
    _check("tension=0 措辞 == 不带参数", d0 == d1, f"{d0!r} != {d1!r}")
    _check("tension=0 意图一致", i0 == i1, f"{i0!r} != {i1!r}")
    _check("tension=0 next_state 一致", ns0 == ns1, f"{ns0!r} != {ns1!r}")


def test_decide_reaction_forced() -> None:
    print("[P1-A] decide_camera · reaction_forced 锁特写静止（覆盖 info）：")
    shot = _shot(dialogue=("赶路辛苦了。",), characters=(_char("林薇薇"), _char("顾琰宸")))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "info", None,
                                        is_last_shot=False, scene_has_prev=False,
                                        reaction_forced=True)
    _check("景别锁 close_up", ns.get("shot_size") == "close_up", str(ns))
    _check("运动锁 static", ns.get("movement") == "static", str(ns))
    _check("措辞含「反应特写」", "反应特写" in desc, desc)
    _check("template_id=reaction", ns.get("template_id") == "reaction", str(ns))


def test_decide_tension3_peak_hold() -> None:
    print("[P1-A] decide_camera · tension=3 峰值定格：")
    # emotion=愤怒 → tense 模板（close_up + push_in）→ 非特写静止 → 替换为峰值定格。
    shot = _shot(emotion="愤怒", characters=(_char("林薇薇"),))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "emotion", None,
                                        is_last_shot=False, scene_has_prev=False, tension=3)
    _check("峰值定格 特写", ns.get("shot_size") == "close_up", str(ns))
    _check("峰值定格 静止", ns.get("movement") == "static", str(ns))
    _check("措辞含「定格」", "定格" in desc, desc)
    _check("template_id=peak_hold", ns.get("template_id") == "peak_hold", str(ns))
    # reaction 镜原生即 close_up+static → tension=3 原样，不替换不重复措辞。
    shot2 = _shot(characters=(_char("林薇薇"),))
    desc2, _, ns2 = dr.decide_camera(shot2, _scene(), "reaction", None,
                                     is_last_shot=False, scene_has_prev=False, tension=3)
    _check("已特写静止 → 原样保留", ns2.get("template_id") == "reaction", str(ns2))
    _check("不替换为峰值定格", "峰值定格" not in desc2 and "凝住" not in desc2, desc2)
    _check("措辞仍为反应特写", "反应特写" in desc2, desc2)


def test_decide_tension2_rising_push() -> None:
    print("[P1-A] decide_camera · tension=2 上升缓推（保留景别）：")
    shot = _shot(dialogue=("赶路辛苦了。",), characters=(_char("林薇薇"), _char("顾琰宸")))
    desc, _, ns = dr.decide_camera(shot, _scene(), "info", None,
                                   is_last_shot=False, scene_has_prev=False, tension=2)
    _check("缓推 保留中景", ns.get("shot_size") == "medium", str(ns))
    _check("缓推 movement=push_in", ns.get("movement") == "push_in", str(ns))
    _check("措辞含「缓推」", "缓推" in desc, desc)
    _check("template_id=rising_push", ns.get("template_id") == "rising_push", str(ns))
    # ending 收束镜不被缓推（保留拉远）。
    shot2 = _shot(characters=(_char("林薇薇"),))
    _, _, ns2 = dr.decide_camera(shot2, _scene(), "ending", None,
                                 is_last_shot=True, scene_has_prev=False, tension=2)
    _check("ending 不缓推（保留拉远）", ns2.get("movement") == "pull_out", str(ns2))


# ---------- 2. build_plan_intents 四模块接线（P1-B） ----------

def test_plan_intents_v2_fields() -> None:
    print("\n[P1-B] build_plan_intents · v2.0 内容规则字段：")
    intents = di.build_plan_intents(_plan())
    s1 = intents["scene_01:shot_01"]
    s2 = intents["scene_01:shot_02"]
    s3 = intents["scene_01:shot_03"]
    _check("shot_02 tension=3", s2.tension == 3, str(s2.tension))
    _check("shot_02 rhythm=build", s2.rhythm == "build", s2.rhythm)
    _check("shot_02 core_action=指尖收紧（攥紧→过程化）",
           s2.core_action == "指尖逐渐收紧，指节泛白", s2.core_action)
    _check("shot_02 expression=下颌绷紧（愤怒可视化）",
           s2.expression == "下颌绷紧，眼底翻涌", s2.expression)
    _check("shot_01 tension=1（中性对白）", s1.tension == 1, str(s1.tension))
    _check("shot_01 rhythm=hold", s1.rhythm == "hold", s1.rhythm)
    _check("shot_01 无动作 → core_action 空", s1.core_action == "", s1.core_action)
    _check("shot_01 无心理词 → expression 空", s1.expression == "", s1.expression)


def test_plan_intents_reaction_forced() -> None:
    print("[P1-B] build_plan_intents · 反应镜硬规则（连续对白/爆点 → 下一镜强制）：")
    intents = di.build_plan_intents(_plan())
    s3 = intents["scene_01:shot_03"]
    _check("continuity.reaction_forced=True", s3.continuity.get("reaction_forced") is True,
           str(s3.continuity))
    _check("shot_role=reaction", s3.continuity.get("shot_role") == "reaction",
           str(s3.continuity.get("shot_role")))
    _check("reaction 镜相机措辞=反应特写", "反应特写" in s3.continuity.get("camera_desc", ""),
           s3.continuity.get("camera_desc", ""))
    _check("core_action 补位（心理动作过程）",
           s3.core_action == "动作一顿，身体微微僵住", s3.core_action)
    _check("expression=瞳孔微缩（愣住可视化）",
           s3.expression == "瞳孔微缩，表情短暂凝滞", s3.expression)
    # 非 forced 镜不写 reaction_forced 键（continuity 不膨胀）。
    s2 = intents["scene_01:shot_02"]
    _check("shot_02 无 reaction_forced 键", "reaction_forced" not in s2.continuity,
           str(s2.continuity))
    # synthetic 模板（reaction/peak_hold 不在 _TEMPLATE_INDEX）→ next_state 结构化枚举兜底：
    # camera_position/movement 必须映射出中文（近景/固定机位），不得为空（修复 #626）。
    _check("shot_03 camera_position=近景（reaction 特写兜底）",
           s3.camera_position == "近景", s3.camera_position)
    _check("shot_03 movement=固定机位", s3.movement == "固定机位", s3.movement)
    _check("shot_02 camera_position=近景（peak_hold 特写兜底）",
           s2.camera_position == "近景", s2.camera_position)
    _check("shot_02 movement=固定机位", s2.movement == "固定机位", s2.movement)


def test_plan_intents_provenance_rule() -> None:
    print("[P1-B] build_plan_intents · 派生字段 provenance=RULE：")
    intents = di.build_plan_intents(_plan())
    prov = intents["scene_01:shot_02"].provenance
    _check("core_action=RULE", prov.get("core_action") == di.IntentSource.RULE, str(prov))
    _check("expression=RULE", prov.get("expression") == di.IntentSource.RULE, str(prov))
    _check("tension=RULE", prov.get("tension") == di.IntentSource.RULE, str(prov))
    _check("rhythm=RULE", prov.get("rhythm") == di.IntentSource.RULE, str(prov))


def test_plan_intents_to_from_dict_roundtrip() -> None:
    print("[P1-B] DirectorIntent to_dict/from_dict 新字段往返：")
    intents = di.build_plan_intents(_plan())
    s2 = intents["scene_01:shot_02"]
    d = s2.to_dict()
    _check("to_dict 含 core_action", d.get("core_action") == s2.core_action, str(d))
    _check("to_dict 含 tension=3", d.get("tension") == 3, str(d))
    _check("to_dict 含 rhythm", d.get("rhythm") == "build", str(d))
    r = di.DirectorIntent.from_dict(d)
    _check("from_dict core_action 恢复", r.core_action == s2.core_action, r.core_action)
    _check("from_dict tension 恢复", r.tension == 3, str(r.tension))
    _check("from_dict rhythm 恢复", r.rhythm == "build", r.rhythm)
    _check("from_dict expression 恢复", r.expression == s2.expression, r.expression)
    # 缺省向后兼容：旧数据无新字段 → 默认空/0。
    old = {k: v for k, v in d.items() if k not in (
        "core_action", "expression", "tension", "rhythm", "composition_note")}
    old2 = di.DirectorIntent.from_dict(old)
    _check("旧数据 core_action 缺省空", old2.core_action == "", old2.core_action)
    _check("旧数据 tension 缺省 0", old2.tension == 0, str(old2.tension))


def test_plan_intents_no_plan_modules_break() -> None:
    print("[P1-B] 规则模块单独可用（零集成依赖）：")
    plan = _plan()
    rm = br.plan_rhythm(plan)
    _check("plan_rhythm 完整", rm == {
        "scene_01:shot_01": (1, "hold"),
        "scene_01:shot_02": (3, "build"),
        "scene_01:shot_03": (0, "release"),
    }, str(rm))
    forced = br.plan_reaction_shots(plan)
    _check("plan_reaction_shots 强制 shot_03", forced == {"scene_01:shot_03"}, str(forced))
    ca_out, subj = ca.extract_core_action(plan.scenes[0].shots[1], role="emotion")
    _check("core_action 直接调用", ca_out == "指尖逐渐收紧，指节泛白", ca_out)
    _check("psych_visualize 直接调用",
           pv.visualize_psych(plan.scenes[0].shots[1]) == "下颌绷紧，眼底翻涌",
           pv.visualize_psych(plan.scenes[0].shots[1]))


# ---------- 3. _build_description 渲染升级（P1-C） ----------

def _intent_dict(**overrides) -> dict:
    d = {
        "shot_id": "shot_02", "scene_id": "scene_01",
        "location": "山雨楼大堂",
        "subject": "林薇薇",
        "characters": ["林薇薇", "沈青崖"],
        "retained_facts": ["林薇薇攥紧衣袖", "愤怒道"],
        "core_action": "",
        "expression": "",
        "composition": "",
        "emotion": "愤怒",
        "camera_position": "近景",
        "movement": "缓推",
        "lighting": "",
        "environment": [],
        "style": "",
        "continuity": {"camera_desc": "特写快速推近林薇薇面部，放大愤怒的情绪张力。"},
        "audio": {"ambient": "雨声", "dialogue": [], "music": "紧张氛围配乐渐起"},
        "provenance": {},
        "entities": [],
    }
    d.update(overrides)
    return d


def _style_profile() -> vs.VisualStyleProfile:
    return vs.VisualStyleProfile.from_dict({
        "profile_id": "test",
        "art_direction": "测试画风（Test style）",
        "style_keywords": {"人物": "测试人物", "光影": "测试光影"},
        "negative_rules": [
            "自定义负面：无文字水印",
            "人物五官、发型、服饰全程保持不变，人体结构正常，无畸形崩坏，画风统一。",
            "Keep consistent facial features, hairstyle and costume. Proper anatomy.",
        ],
    })


def test_build_desc_style_profile_none() -> None:
    print("\n[P1-C] _build_description · style_profile=None 旧链路不变：")
    desc, prov = h3._build_description(
        _intent_dict(), duration_sec=5.0, ref_line="")
    _check("无画风块", "画风：" not in desc, desc)
    _check("无防崩坏约束", "防崩坏约束" not in prov["rule_generated"], str(prov))
    _check("无 _ANTI_BREAK_RULE 文本", "Do NOT change the character" not in desc, desc)
    _check("provenance 无画风块标记", "画风块" not in prov["rule_generated"], str(prov))
    _check("provenance 无防崩坏标记", "防崩坏约束" not in prov["rule_generated"], str(prov))


def test_build_desc_style_profile_inject() -> None:
    print("[P1-C] _build_description · style_profile 注入画风块+防崩坏：")
    desc, prov = h3._build_description(
        _intent_dict(), duration_sec=5.0, ref_line="", style_profile=_style_profile(),
        scene_name="山雨楼大堂")
    # 画风块在 location 之后（顶部）。
    _check("画风块在描述区顶部", "画风：测试画风" in desc, desc)
    _check("画风块在 location 之后", desc.index("画风：测试画风") > desc.index("山雨楼大堂"),
           desc)
    _check("画风块关键词注入", "人物：测试人物" in desc, desc)
    # 防崩坏句在尾部、无字幕约束之前。
    _check("防崩坏固定句注入", "人物五官、发型、服饰全程保持不变" in desc, desc)
    _check("防崩坏英文句注入", "Do NOT change the character's facial features" in desc, desc)
    _check("自定义负面保留", "自定义负面：无文字水印" in desc, desc)
    _check("默认负面去重（固定句只 1 次）",
           desc.count("人物五官、发型、服饰全程保持不变") == 1, desc)
    _check("防崩坏在无字幕约束之前",
           desc.index("Do NOT change the character's") < desc.index("Do NOT render any subtitles"),
           desc)
    _check("rule_generated 含画风块", "画风块" in prov["rule_generated"], str(prov))
    _check("rule_generated 含防崩坏约束", "防崩坏约束" in prov["rule_generated"], str(prov))


def test_build_desc_core_action_expression() -> None:
    print("[P1-C] _build_description · 核心动作/表情句渲染：")
    desc, prov = h3._build_description(
        _intent_dict(core_action="指尖逐渐收紧，指节泛白", expression="下颌绷紧，眼底翻涌"),
        duration_sec=5.0, ref_line="")
    _check("核心动作句", "核心动作：指尖逐渐收紧，指节泛白" in desc, desc)
    _check("表情句", "表情：下颌绷紧，眼底翻涌" in desc, desc)
    _check("rule_generated 含核心动作", "核心动作=指尖逐渐收紧，指节泛白" in prov["rule_generated"],
           str(prov))
    _check("rule_generated 含表情", "表情=下颌绷紧，眼底翻涌" in prov["rule_generated"], str(prov))
    # 空值跳过。
    desc2, prov2 = h3._build_description(_intent_dict(), duration_sec=5.0, ref_line="")
    _check("空 core_action → 无核心动作句", "核心动作：" not in desc2, desc2)
    _check("空 expression → 无表情句", "表情：" not in desc2, desc2)


def test_build_desc_core_action_skip_verbatim() -> None:
    print("[P1-C] _build_description · 与原文逐字/相似重复 → 跳过规则句：")
    # core_action 逐字出现在原文事实 → 跳过（原文事实区已覆盖，不重复）。
    desc, _ = h3._build_description(
        _intent_dict(core_action="林薇薇攥紧衣袖"), duration_sec=5.0, ref_line="")
    _check("逐字重复 → 不渲染「核心动作：」", "核心动作：林薇薇攥紧衣袖" not in desc, desc)
    _check("原文事实仍在（未误删）", "林薇薇攥紧衣袖" in desc, desc)


# ---------- 4. _merge_negative_rules 去重 ----------

def test_merge_negative_rules() -> None:
    print("\n[P1-C] _merge_negative_rules · 默认负面跳过/自定义保留：")
    _check("空 profile → 空串", h3._merge_negative_rules(vs.VisualStyleProfile()) == "")
    default_prof = vs.VisualStyleProfile(negative_rules=list(vs._COMMON_NEGATIVE))
    _check("默认负面（主题命中）→ 全跳过", h3._merge_negative_rules(default_prof) == "",
           h3._merge_negative_rules(default_prof))
    custom = vs.VisualStyleProfile(negative_rules=["无文字水印", "保持眼神清澈"])
    merged = h3._merge_negative_rules(custom)
    _check("自定义负面保留", merged == "无文字水印保持眼神清澈", merged)
    mixed = vs.VisualStyleProfile(negative_rules=[
        "无文字水印", "人物五官、发型、服饰全程保持不变，人体结构正常，无畸形崩坏，画风统一。"])
    _check("混合：自定义保留/默认跳过", h3._merge_negative_rules(mixed) == "无文字水印",
           h3._merge_negative_rules(mixed))
    _check("dict 形式兼容",
           h3._merge_negative_rules({"negative_rules": ["只保留我"]}) == "只保留我")


# ---------- 5. build_plan_h3_prompts style_profile 透传（P1-C 路由层） ----------

def test_build_plan_h3_prompts_style_profile() -> None:
    print("\n[P1-C] build_plan_h3_prompts · style_profile 透传：")
    intents = di.build_plan_intents(_plan())
    out_none = h3.build_plan_h3_prompts(intents, plan=_plan())
    _check("style_profile=None 无画风块",
           all("画风：" not in p.integrated_multimodal_description
               for p in out_none.values()))
    prof = vs.VisualStyleProfile.from_dict({
        "profile_id": "test", "art_direction": "透传画风",
        "style_keywords": {"人物": "透传人物"},
    })
    out = h3.build_plan_h3_prompts(intents, plan=_plan(), style_profile=prof)
    d = out["scene_01:shot_02"].integrated_multimodal_description
    _check("style_profile 非 None 注入画风块", "画风：透传画风" in d, d)
    _check("scene_name=intent.location 透传",
           "山雨楼大堂" in d, d)


# ---------- 6. 纯规则零显存 ----------

def test_no_gpu_side_effects() -> None:
    print("\n[P1] ⛔ 纯规则零显存：")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = (
        "director_rules.py", "director_intent.py", "h3_prompt_builder.py",
        "visual_style.py", "core_action.py", "psych_visualize.py", "beat_rhythm.py",
    )
    bad = ("torch", "ollama", "urlopen", "requests", "aiohttp", "keep_alive",
           "gpu_models_loaded", "empty_cache")
    for f in files:
        src = open(os.path.join(base, f), encoding="utf-8").read()
        for b in bad:
            _check(f"{f} 无 {b} 引用", b not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_v2_p1_integration")
    test_decide_tension0_backward_compat()
    test_decide_reaction_forced()
    test_decide_tension3_peak_hold()
    test_decide_tension2_rising_push()
    test_plan_intents_v2_fields()
    test_plan_intents_reaction_forced()
    test_plan_intents_provenance_rule()
    test_plan_intents_to_from_dict_roundtrip()
    test_plan_intents_no_plan_modules_break()
    test_build_desc_style_profile_none()
    test_build_desc_style_profile_inject()
    test_build_desc_core_action_expression()
    test_build_desc_core_action_skip_verbatim()
    test_merge_negative_rules()
    test_build_plan_h3_prompts_style_profile()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
