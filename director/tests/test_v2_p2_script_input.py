#!/usr/bin/env python3
"""v2.0 P2 输入质量提升综合测试（CAMERA_RULES_V2 §7 P2 行，纯规则 mock，零显存）。

P2 范围（用户 2026-08-18「开始P2」）：
① ``script_analyzer`` 升级：``actions`` 拆分更细——Qwen 常把多动作塞一条
   （「捏起支票，缓缓抬到眼前」），P2 用纯规则 ``_split_actions`` 拆成
   「一条一动作」+ prompt 升级（batch/single 模板都带拆分指令）。
② 心理词预标记（软约束转硬输入）：``tag_psych(shot)`` 在分析阶段把命中
   心理词落盘到 ``shot.psych_tags``，``psych_visualize._psych_hit`` 优先
   消费硬输入，无标记才回退运行时扫描。

覆盖：
1. ``_split_actions`` 单测：标点拆分 / 连词拆分 / 去重 / 最小长度 / 空串 / 幂等；
2. ``tag_psych`` / ``tag_psych_text`` 单测：保序去重、多源扫描（原文优先于情绪）；
3. script_analyzer 集成：FakeTextBackend 返回多动作 JSON → ``_merge_shot``
   actions 拆成一动作一条 + psych_tags 预标记落盘；
4. 硬输入优先：psych_tags=["慌乱"] 且 emotion=愤怒 → expression 取慌乱句
   （硬输入 > 运行时扫描）；未知 tag 忽略回退运行时扫描；
5. Shot to_dict/from_dict 往返带 psych_tags；旧数据缺字段默认 []（向后兼容）；
6. story_analyzer._build_shot 小说导入路径预标记（psych_tags 落盘）；
7. prompt 拆分指令断言：analyze_scene（batch）与 analyze_shot（single）
   模板都含「每条只含一个动作」；
8. 《吐槽成真》4 镜全链：script 语义补全（拆分+预标记）→ build_plan_intents
   → 4 字段（core_action/expression/tension/rhythm）+ 反应镜强制；
9. ⛔ 纯规则零显存：4 个改动文件源码无 torch/ollama/网络引用。

直接用系统 python3 运行（无第三方依赖）：
    python3 director/tests/test_v2_p2_script_input.py
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.psych_visualize as pv  # noqa: E402
from director.director_intent import build_plan_intents  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
)
from director.script_analyzer import (  # noqa: E402
    analyze_scene,
    analyze_shot,
    _merge_shot,
    _split_actions,
)
from director.story_analyzer import _build_shot  # noqa: E402
from director.text_backends import TextBackend  # noqa: E402

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


# ---------------------------------------------------------------------------
# Fake 后端（与 test_script_analyzer 同款：继承 TextBackend 真 session）
# ---------------------------------------------------------------------------
class FakeTextBackend(TextBackend):
    name = "fake"

    def __init__(self, responder=None):
        self.responder = responder
        self.prompts: list[str] = []

    def analyze_text(self, prompt, *, max_tokens=1024, temperature=0.0):
        self.prompts.append(prompt)
        if self.responder is not None:
            return self.responder(prompt)
        return ""

    def close(self):
        pass

    def unload(self):
        pass


def _pshot(*, source_text: str = "", emotion: str = "", actions: tuple = (),
           visual_intent: str = "", psych_tags: tuple = ()) -> SimpleNamespace:
    """psych_visualize 直接测试夹具（SimpleNamespace，兼容无 psych_tags 旧对象）。"""
    return SimpleNamespace(
        source_text=source_text,
        emotion=emotion,
        actions=list(actions),
        visual_intent=visual_intent,
        psych_tags=list(psych_tags),
    )


# ---------- 1. _split_actions 单测 ----------

def test_split_actions() -> None:
    print("\n[1] _split_actions · 一动作一条（标点/连词拆分）：")
    _check("标点拆分：捏起支票，缓缓抬到眼前 → 两条",
           _split_actions(["捏起支票，缓缓抬到眼前"]) == ["捏起支票", "缓缓抬到眼前"],
           str(_split_actions(["捏起支票，缓缓抬到眼前"])))
    _check("连词拆分：他推开门然后走进来 → 两条",
           _split_actions(["他推开门然后走进来"]) == ["他推开门", "走进来"],
           str(_split_actions(["他推开门然后走进来"])))
    _check("连词拆分：他点头并微笑 → 两条",
           _split_actions(["他点头并微笑"]) == ["他点头", "微笑"],
           str(_split_actions(["他点头并微笑"])))
    _check("单动作幂等：收伞 → 收伞",
           _split_actions(["收伞"]) == ["收伞"], str(_split_actions(["收伞"])))
    _check("多动作序列幂等：拔剑，挥剑 → 原样两条",
           _split_actions(["拔剑", "挥剑"]) == ["拔剑", "挥剑"],
           str(_split_actions(["拔剑", "挥剑"])))
    _check("去重：收伞，收伞 → 一条",
           _split_actions(["收伞", "收伞"]) == ["收伞"], str(_split_actions(["收伞", "收伞"])))
    _check("空白/标点剥离：' 收伞。' → 收伞",
           _split_actions([" 收伞。"]) == ["收伞"], str(_split_actions([" 收伞。"])))
    _check("空列表 → []", _split_actions([]) == [], str(_split_actions([])))
    _check("空串条目跳过", _split_actions(["", "收伞"]) == ["收伞"],
           str(_split_actions(["", "收伞"])))
    _check("最短长度守卫：'走' → 丢弃",
           _split_actions(["走"]) == [], str(_split_actions(["走"])))
    _check("连续标点不产生空条目",
           _split_actions(["收伞，，环视堂内"]) == ["收伞", "环视堂内"],
           str(_split_actions(["收伞，，环视堂内"])))


# ---------- 2. tag_psych / tag_psych_text 单测 ----------

def test_tag_psych_text() -> None:
    print("\n[2] tag_psych_text · 单文本扫描：")
    _check("原文含慌乱 → 命中", pv.tag_psych_text("她心里一阵慌乱") == ["慌乱"],
           str(pv.tag_psych_text("她心里一阵慌乱")))
    _check("冷笑 → 命中嘲讽组", pv.tag_psych_text("他冷笑一声") == ["冷笑"],
           str(pv.tag_psych_text("他冷笑一声")))
    _check("空串 → []", pv.tag_psych_text("") == [], str(pv.tag_psych_text("")))
    _check("中性词 → []", pv.tag_psych_text("平静") == [],
           str(pv.tag_psych_text("平静")))
    _check("多词命中保序（映射库顺序）",
           pv.tag_psych_text("她既紧张又慌乱") == ["慌乱", "紧张"],
           str(pv.tag_psych_text("她既紧张又慌乱")))


def test_tag_psych_multi_source() -> None:
    print("[2] tag_psych · 多源扫描（原文优先 / 保序去重）：")
    _check("source_text 命中", pv.tag_psych(_pshot(source_text="她心里一阵慌乱。")) == ["慌乱"],
           str(pv.tag_psych(_pshot(source_text="她心里一阵慌乱。"))))
    _check("emotion 命中", pv.tag_psych(_pshot(emotion="震惊")) == ["震惊"],
           str(pv.tag_psych(_pshot(emotion="震惊"))))
    _check("actions 命中", pv.tag_psych(_pshot(actions=("他冷笑一声",))) == ["冷笑"],
           str(pv.tag_psych(_pshot(actions=("他冷笑一声",)))))
    _check("visual_intent 命中",
           pv.tag_psych(_pshot(visual_intent="眼神轻蔑地扫过")) == ["轻蔑"],
           str(pv.tag_psych(_pshot(visual_intent="眼神轻蔑地扫过"))))
    _check("原文优先于情绪（source 先命中先排前）",
           pv.tag_psych(_pshot(source_text="她震惊地睁大眼睛", emotion="轻蔑")) == ["震惊", "轻蔑"],
           str(pv.tag_psych(_pshot(source_text="她震惊地睁大眼睛", emotion="轻蔑"))))
    _check("跨源去重", pv.tag_psych(_pshot(source_text="她慌乱地", emotion="慌乱")) == ["慌乱"],
           str(pv.tag_psych(_pshot(source_text="她慌乱地", emotion="慌乱"))))
    _check("无心理词 → []", pv.tag_psych(_pshot()) == [], str(pv.tag_psych(_pshot())))


# ---------- 3. 硬输入优先（软约束转硬输入） ----------

def test_hard_input_priority() -> None:
    print("\n[3] psych_visualize · 硬输入优先（psych_tags > 运行时扫描）：")
    _check("psych_tags=['慌乱'] 且 emotion=愤怒 → 慌乱句（硬输入赢）",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("慌乱",))) == "眼神短暂躲闪，睫毛轻颤",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("慌乱",))))
    _check("psych_tags 无 → 回退运行时扫描（愤怒）",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒")) == "下颌绷紧，眼底翻涌",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒")))
    _check("未知 tag 忽略 → 回退运行时扫描",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("不在表中",))) == "下颌绷紧，眼底翻涌",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("不在表中",))))
    _check("psych_process_action 硬输入（慌乱动作过程）",
           pv.psych_process_action(_pshot(emotion="愤怒", psych_tags=("慌乱",)))
           == "呼吸变急，手指不自觉攥紧衣摆",
           pv.psych_process_action(_pshot(emotion="愤怒", psych_tags=("慌乱",))))
    _check("多 tag 取映射库序第一条",
           pv.visualize_psych(_pshot(source_text="", emotion="", psych_tags=("轻蔑", "愤怒")))
           == "嘴角勾起一抹轻蔑的弧度",
           pv.visualize_psych(_pshot(source_text="", emotion="", psych_tags=("轻蔑", "愤怒"))))


# ---------- 4. script_analyzer 合并：拆分 + 预标记 ----------

def test_merge_shot_split_and_pretag() -> None:
    print("\n[4] _merge_shot · 多动作拆分 + psych_tags 预标记：")
    shot = Shot(shot_id="shot_01",
                source_text="她心里一阵慌乱，捏起支票，缓缓抬到眼前。")
    data = {
        "visual_interpretation": {
            "actions": ["捏起支票，缓缓抬到眼前"],
            "emotion": "慌乱",
        }
    }
    merged = _merge_shot(shot, data)
    _check("多动作拆成一条一动作", merged.actions == ["捏起支票", "缓缓抬到眼前"],
           str(merged.actions))
    _check("psych_tags 预标记慌乱", "慌乱" in merged.psych_tags, str(merged.psych_tags))
    _check("source_text 逐字不变（铁律）",
           merged.source_text == "她心里一阵慌乱，捏起支票，缓缓抬到眼前。",
           merged.source_text)
    _check("emotion 填充慌乱", merged.emotion == "慌乱", merged.emotion)
    _check("psych_tags 去重", merged.psych_tags == ["慌乱"], str(merged.psych_tags))
    # 规则动作优先 + 仍过拆分（向后兼容：既有单动作断言不变）。
    shot2 = Shot(shot_id="shot_02", source_text="沈青崖拔剑，挥剑。",
                 actions=["拔剑", "挥剑"])
    merged2 = _merge_shot(shot2, {"visual_interpretation": {"actions": ["收伞"]}})
    _check("规则动作优先不被 Qwen 覆盖", merged2.actions == ["拔剑", "挥剑"],
           str(merged2.actions))
    _check("data=None 原样返回", _merge_shot(shot, None).actions == shot.actions)


# ---------- 5. Shot 持久化往返（向后兼容） ----------

def test_shot_psych_tags_roundtrip() -> None:
    print("\n[5] Shot · psych_tags 持久化往返：")
    s = Shot(shot_id="shot_01", source_text="她心里一阵慌乱。", psych_tags=["慌乱"])
    d = s.to_dict()
    _check("to_dict 含 psych_tags", d.get("psych_tags") == ["慌乱"], str(d))
    r = Shot.from_dict(d)
    _check("from_dict 恢复 psych_tags", r.psych_tags == ["慌乱"], str(r.psych_tags))
    old = {k: v for k, v in d.items() if k != "psych_tags"}
    old_s = Shot.from_dict(old)
    _check("旧数据缺 psych_tags → 默认 []（向后兼容）", old_s.psych_tags == [],
           str(old_s.psych_tags))
    _check("from_dict(None) → 全缺省", Shot.from_dict(None).psych_tags == [])


# ---------- 6. 小说导入路径预标记（story_analyzer._build_shot） ----------

def test_build_shot_pretag() -> None:
    print("\n[6] story_analyzer._build_shot · 小说导入路径心理词预标记：")
    template = {"shot_type": "closeup", "camera_position": "近景",
                "movement": "固定", "intent": "情绪特写"}
    shot = _build_shot("她心里一阵慌乱，说不出话来。", template, 1, [])
    _check("psych_tags 含慌乱", "慌乱" in shot.psych_tags, str(shot.psych_tags))
    _check("psych_tags 保序", shot.psych_tags == ["慌乱"], str(shot.psych_tags))
    _check("无心理词 chunk → []",
           _build_shot("雨刚停，沈青崖走进客栈。", template, 2, []).psych_tags == [],
           str(_build_shot("雨刚停，沈青崖走进客栈。", template, 2, []).psych_tags))


# ---------- 7. prompt 拆分指令断言 ----------

def test_prompt_split_instruction() -> None:
    print("\n[7] prompt · batch/single 模板拆分指令：")
    # batch（analyze_scene）
    scene = Scene(scene_id="scene_01", title="山雨楼大堂", location_name="山雨楼大堂",
                  time="夜", weather="雨",
                  shots=[Shot(shot_id="shot_01", source_text="林雪推开门走进来。")])
    backend = FakeTextBackend(responder=lambda p: json.dumps(
        {"shots": [{"index": 1, "visual_interpretation": {"actions": ["收伞"], "emotion": ""}}]},
        ensure_ascii=False))
    with backend.session():
        analyze_scene(backend, scene)
    _check("batch prompt 含「每条只含一个动作」",
           any("每条只含一个动作" in p for p in backend.prompts),
           backend.prompts[0][:120] if backend.prompts else "")
    # single（analyze_shot fallback）
    backend2 = FakeTextBackend(responder=lambda p: "")
    with backend2.session():
        analyze_shot(backend2, scene.shots[0], scene)
    _check("single prompt 含「每条只含一个动作」",
           any("每条只含一个动作" in p for p in backend2.prompts),
           backend2.prompts[0][:120] if backend2.prompts else "")


# ---------- 8. 《吐槽成真》4 镜全链集成 ----------

def _tucao_batch() -> dict:
    return {
        "shots": [
            {"index": 1, "visual_interpretation": {"actions": [], "emotion": ""}},
            {"index": 2, "visual_interpretation": {"actions": ["林雪推开店门", "环视堂内"],
                                                   "emotion": "惊讶"}},
            {"index": 3, "visual_interpretation": {"actions": ["林雪冷笑，拿起菜单"],
                                                   "emotion": "轻蔑"}},
            {"index": 4, "visual_interpretation": {"actions": ["林雪瞳孔微缩", "后退半步"],
                                                   "emotion": "震惊"}},
        ]
    }


def _tucao_rule_plan() -> ProductionPlan:
    """规则骨架（script_parser 产物样式）：actions/emotion 空，由 Qwen 语义补全。"""
    return ProductionPlan(
        project=ProjectInfo(title="吐槽成真", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="网红餐厅门口", location_name="网红餐厅门口",
            time="午", weather="晴",
            shots=[
                Shot(shot_id="shot_01",
                     source_text="林雪站在餐厅门口，说，这家店评分五颗星，咱们来对了。",
                     characters=[Character(name="林雪")],
                     dialogue=[Dialogue(speaker="林雪", text="这家店评分五颗星，咱们来对了。")],
                     actions=[], emotion=""),
                Shot(shot_id="shot_02",
                     source_text="林雪推开店门，惊讶地环视堂内，宾客满座。",
                     characters=[Character(name="林雪")],
                     actions=[], emotion=""),
                Shot(shot_id="shot_03",
                     source_text="林雪冷笑，拿起菜单，就这？就这？就这点水平也敢开店？",
                     characters=[Character(name="林雪")],
                     dialogue=[Dialogue(speaker="林雪", text="就这？就这？就这点水平也敢开店？")],
                     actions=[], emotion=""),
                Shot(shot_id="shot_04",
                     source_text="林雪瞳孔微缩，震惊得后退半步，说不出话。",
                     characters=[Character(name="林雪"), Character(name="沈青崖")],
                     actions=[], emotion=""),
            ],
        )],
        validation=Validation(),
        timeline=["scene_01:shot_01", "scene_01:shot_02", "scene_01:shot_03", "scene_01:shot_04"],
    )


def test_tucao_full_chain() -> None:
    print("\n[8] 《吐槽成真》4 镜全链 · 语义补全（拆分+预标记）→ build_plan_intents：")
    plan = _tucao_rule_plan()
    backend = FakeTextBackend(responder=lambda p: json.dumps(_tucao_batch(), ensure_ascii=False))
    with backend.session():
        merged_scene = analyze_scene(backend, plan.scenes[0])
    merged_plan = ProductionPlan(
        project=plan.project, scenes=[merged_scene],
        validation=Validation(), timeline=plan.timeline,
    )
    s = merged_scene.shots
    # —— 拆分 + 预标记（script_analyzer 侧） ——
    _check("shot_01 actions 空", s[0].actions == [], str(s[0].actions))
    _check("shot_02 actions 两条", s[1].actions == ["林雪推开店门", "环视堂内"], str(s[1].actions))
    _check("shot_03 多动作拆成两条（冷笑 / 拿起菜单）",
           s[2].actions == ["林雪冷笑", "拿起菜单"], str(s[2].actions))
    _check("shot_04 actions 两条", s[3].actions == ["林雪瞳孔微缩", "后退半步"], str(s[3].actions))
    _check("shot_03 psych_tags 预标记（冷笑）", "冷笑" in s[2].psych_tags, str(s[2].psych_tags))
    _check("shot_04 psych_tags 预标记（震惊）", "震惊" in s[3].psych_tags, str(s[3].psych_tags))
    _check("shot_01 无心理词 → psych_tags 空", s[0].psych_tags == [], str(s[0].psych_tags))
    # —— DirectorIntent 4 字段派生 ——
    intents = build_plan_intents(merged_plan)
    i1 = intents["scene_01:shot_01"]
    i2 = intents["scene_01:shot_02"]
    i3 = intents["scene_01:shot_03"]
    i4 = intents["scene_01:shot_04"]
    _check("shot_01 tension=1（对白存在）", i1.tension == 1, str(i1.tension))
    _check("shot_01 rhythm=hold", i1.rhythm == "hold", i1.rhythm)
    _check("shot_01 无动作 → core_action 空", i1.core_action == "", i1.core_action)
    _check("shot_01 无心理词 → expression 空", i1.expression == "", i1.expression)
    _check("shot_02 tension=2（惊讶）", i2.tension == 2, str(i2.tension))
    _check("shot_02 rhythm=build", i2.rhythm == "build", i2.rhythm)
    # 注：shot_02 无对白 + 上镜有对白 → v1.0 assign_shot_role 判为 reaction（听比说重要，
    # test_director_rules 锁定行为）；reaction 镜只挑表情动作，而 actions=[林雪推开店门,环视堂内]
    # 均非表情动词 → core_action 空；惊讶 不在 PSYCH_MAP → 无动作过程补位。强动作路径
    # 由 shot_03（拿起菜单）与 test_bomb_peak_path shot_01（action 镜）覆盖。
    _check("shot_02 reaction 镜（无表情动作）→ core_action 空",
           i2.core_action == "", i2.core_action)
    _check("shot_02 惊讶非映射心理词 → expression 空",
           i2.expression == "", i2.expression)
    # 注：镜3 emotion='轻蔑' → beat_rhythm._base_tension 情绪关键词优先（轻蔑→1）
    # 先于爆点词（就这）；故 tension=1、rhythm=release（release 由 2→1 触发）。
    _check("shot_03 tension=1（轻蔑关键词优先于爆点）", i3.tension == 1, str(i3.tension))
    _check("shot_03 rhythm=release（2→1 释放段）", i3.rhythm == "release", i3.rhythm)
    _check("shot_03 core_action=拿起菜单（首个强动作）", i3.core_action == "拿起菜单",
           i3.core_action)
    _check("shot_03 expression=冷笑心理词（硬输入）",
           i3.expression == "嘴角浮现一抹嘲讽的弧度", i3.expression)
    _check("shot_04 reaction_forced=True", i4.continuity.get("reaction_forced") is True,
           str(i4.continuity))
    _check("shot_04 shot_role=reaction", i4.continuity.get("shot_role") == "reaction",
           str(i4.continuity.get("shot_role")))
    _check("shot_04 tension=3（震惊）", i4.tension == 3, str(i4.tension))
    _check("shot_04 rhythm=build（1→3 上升段）", i4.rhythm == "build", i4.rhythm)
    _check("shot_04 core_action=林雪瞳孔微缩（反应镜表情优先）",
           i4.core_action == "林雪瞳孔微缩", i4.core_action)
    _check("shot_04 expression=瞳孔微缩表情凝滞（震惊硬输入）",
           i4.expression == "瞳孔微缩，表情短暂凝滞", i4.expression)
    _check("shot_04 camera_position=近景（reaction 兜底）",
           i4.camera_position == "近景", i4.camera_position)
    _check("shot_04 movement=固定机位", i4.movement == "固定机位", i4.movement)


def test_bomb_peak_path() -> None:
    """独立验证「台词爆点 → 峰值」路径（设计意图补测，防止 轻蔑 关键词掩盖）。"""
    print("[8] 台词爆点 → tension3 peak（情绪词留空，让爆点词驱动）：")
    plan = ProductionPlan(
        project=ProjectInfo(title="爆点", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="餐厅", location_name="餐厅",
            time="午", weather="晴",
            shots=[
                Shot(shot_id="shot_01",
                     source_text="林雪推开店门，惊讶地环视堂内。",
                     characters=[Character(name="林雪")],
                     emotion="惊讶", actions=["林雪推开店门"]),
                Shot(shot_id="shot_02",
                     source_text="林雪冷笑，就这？就这？就这点水平也敢开店？",
                     characters=[Character(name="林雪")],
                     dialogue=[Dialogue(speaker="林雪", text="就这？就这？就这点水平也敢开店？")],
                     emotion="", actions=["林雪冷笑"]),
            ],
        )],
        validation=Validation(),
        timeline=["scene_01:shot_01", "scene_01:shot_02"],
    )
    intents = build_plan_intents(plan)
    i1 = intents["scene_01:shot_01"]
    i2 = intents["scene_01:shot_02"]
    _check("shot_01 tension=2（惊讶）", i1.tension == 2, str(i1.tension))
    # 首镜无上镜对白 → v1.0 assign_shot_role 判为 action → 强动作路径（首个强动作）。
    _check("shot_01 core_action=林雪推开店门（action 镜首个强动作）",
           i1.core_action == "林雪推开店门", i1.core_action)
    _check("shot_02 tension=3（爆点词 就这 驱动）", i2.tension == 3, str(i2.tension))
    _check("shot_02 rhythm=peak（2→3 峰值定格）", i2.rhythm == "peak", i2.rhythm)
    _check("shot_02 expression=冷笑心理词", i2.expression == "嘴角浮现一抹嘲讽的弧度",
           i2.expression)


# ---------- 9. 纯规则零显存 ----------

def test_no_gpu_side_effects() -> None:
    print("\n[P2] ⛔ 纯规则零显存：")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = ("script_analyzer.py", "psych_visualize.py", "story_analyzer.py",
             "production_plan.py")
    bad = ("torch", "ollama", "urlopen", "requests", "aiohttp",
           "gpu_models_loaded", "empty_cache", "openai")
    for f in files:
        src = open(os.path.join(base, f), encoding="utf-8").read()
        for b in bad:
            _check(f"{f} 无 {b} 引用", b not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_v2_p2_script_input")
    test_split_actions()
    test_tag_psych_text()
    test_tag_psych_multi_source()
    test_hard_input_priority()
    test_merge_shot_split_and_pretag()
    test_shot_psych_tags_roundtrip()
    test_build_shot_pretag()
    test_prompt_split_instruction()
    test_tucao_full_chain()
    test_bomb_peak_path()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
