#!/usr/bin/env python3
"""生成约束层 v1.0 P1 接线测试（PROMPT_COMPILER_V1 §3.1-3.3，用户 2026-08-18 拍板）。

覆盖真实生成链路的四段接线（P1）：
1. build_shot_intent / build_plan_intents → DirectorIntent.constraint 填充
   （ShotPlan.to_dict()，provenance["constraint"]=RULE）；
2. to_dict/from_dict → constraint 字段持久化往返（生成提交/快照不丢约束）；
3. h3_prompt_builder._build_description → 双语约束块注入（角色锁定/动作顺序/
   镜头锁定/禁止）+ rule_generated 记「约束块」；无 constraint → 向后兼容不注入；
4. constraint_checker.build_constraint_check → /prompt/h3 的 constraint_check
   报告（用户失败案例：多景别 warning 必现）。

直接用系统 python 运行（纯规则零 LLM 零显存）：
    python3 director/tests/test_constraint_wiring.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import constraint_checker as cc  # noqa: E402
from director import director_intent as di  # noqa: E402
from director import h3_prompt_builder as h3  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
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

# 《山雨客栈》镜头原文（P0-① 回归基准，复用既有 _plan 数据）
SHOT1_SRC = ("雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，"
             "湿漉漉的石阶映着暖光，门前老槐树滴着水珠。")
SHOT4_SRC = ("柳如烟放下茶碗，从柜台后绕过，端着一碗热茶走向堂中，语气平淡："
             "「客官，落座歇脚，茶先暖着。」")
SHOT7_SRC = ("蒙面人一步步走近，沉声：「剑，交出来。」沈青崖起身，横剑护在柳如烟身前；"
             "柳如烟退后半步，手已按住柜台下的短刃。三人成三角对峙。")

# 用户失败案例回放（办公室三人群像 → 锁 2 人 + 双景别 + 不伸手 + 多核心动作）
FAILURE_SRC = ("顾琰宸从西装内袋掏出支票，将支票从手中甩落到林薇薇面前的桌面，"
               "支票落桌后轻微滑动，林薇薇没有伸手去拿，中景拍摄顾琰宸甩出支票，"
               "特写支票滑动至桌面，林薇薇保持静止，冷漠。")

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
    """1 场景 3 镜：shot_01 环境（无角色）、shot_04 单角色、shot_07 三角色。"""
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
                     ]),
                Shot(shot_id="shot_04", source_text=SHOT4_SRC, duration_sec=5,
                     characters=[Character(name="柳如烟")],
                     dialogue=[Dialogue(speaker="柳如烟", text="客官，落座歇脚，茶先暖着。")],
                     entities=[
                         Entity(name="柳如烟", type=EntityType.CHARACTER,
                                source=EntitySource.SCRIPT, confidence=0.95),
                     ]),
                Shot(shot_id="shot_07", source_text=SHOT7_SRC, duration_sec=5,
                     characters=[Character(name="沈青崖"),
                                 Character(name="柳如烟"),
                                 Character(name="蒙面人")],
                     actions=["蒙面人一步步走近", "沈青崖起身", "横剑护在柳如烟身前",
                              "柳如烟退后半步"],
                     entities=[
                         Entity(name="沈青崖", type=EntityType.CHARACTER,
                                source=EntitySource.SCRIPT, confidence=0.95),
                     ]),
            ],
        )],
        validation=Validation(),
    )


def _failure_plan() -> ProductionPlan:
    """失败案例回放单镜 plan：带 actions 走 build_plan_intents 真实接线。"""
    return ProductionPlan(
        project=ProjectInfo(title="失败案例回放", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="豪华私人办公室", location_name="豪华私人办公室",
            shots=[Shot(
                shot_id="shot_01", source_text=FAILURE_SRC, duration_sec=5,
                characters=[Character(name="顾琰宸"), Character(name="林薇薇")],
                actions=["掏出支票", "甩落桌面", "林薇薇没有伸手去拿"],
                emotion="冷漠",
                visual_intent="中景拍摄顾琰宸甩出支票，特写支票滑动至桌面",
            )],
        )],
        validation=Validation(),
    )


def _failure_intent() -> "di.DirectorIntent":
    """手动构造失败案例 intent（composition 含双景别；用于 constraint=None 向后兼容与检查报告）。"""
    return di.DirectorIntent(
        shot_id="scene_01:shot_01",
        scene_id="scene_01",
        user_original_intent=FAILURE_SRC,
        retained_facts=["顾琰宸从西装内袋掏出支票", "将支票从手中甩落到林薇薇面前的桌面",
                        "支票落桌后轻微滑动", "林薇薇没有伸手去拿", "林薇薇保持静止，冷漠"],
        subject="顾琰宸、林薇薇",
        characters=["顾琰宸", "林薇薇"],
        location="豪华私人办公室",
        action=["掏出支票", "甩落桌面", "林薇薇没有伸手去拿"],
        emotion="冷漠",
        composition="中景拍摄顾琰宸甩出支票，特写支票滑动至桌面",
        camera_position="中景",
        movement="固定机位",
        core_action="甩落桌面",
        expression="林薇薇嘴角浮现一抹嘲讽",
        continuity={
            "spatial": {"block": "空间连续性：顾琰宸在屏幕左侧，林薇薇在屏幕右侧，"
                                  "视线轴固定（顾琰宸→林薇薇）。Spatial Continuity: "
                                  "顾琰宸: screen left. 林薇薇: screen right."},
        },
    )


# ---------------- 1. build_shot_intent / build_plan_intents 填充 constraint ----------------

def test_build_plan_intents_populates_constraint() -> None:
    print("\n[1] build_plan_intents → intent.constraint 填充")
    intents = di.build_plan_intents(_plan())
    # shot_04 单角色 → character_count=1
    it04 = intents["scene_01:shot_04"]
    _check("shot_04 constraint 非空 dict", isinstance(it04.constraint, dict)
           and it04.constraint is not None, str(type(it04.constraint)))
    _check("shot_04 character_count=1", it04.constraint["character_count"] == 1,
           str(it04.constraint.get("character_count")))
    _check("shot_04 provenance.constraint=RULE",
           it04.provenance.get("constraint") == IntentSource.RULE,
           str(it04.provenance.get("constraint")))
    # shot_07 三角色 → character_count=3
    it07 = intents["scene_01:shot_07"]
    _check("shot_07 character_count=3", it07.constraint["character_count"] == 3,
           str(it07.constraint.get("character_count")))
    _check("shot_07 角色锁定名单", len(it07.constraint.get("character_locks", [])) == 3,
           str(it07.constraint.get("character_locks")))
    # shot_01 无角色 → character_count=0（约束块跳过，向后兼容）
    it01 = intents["scene_01:shot_01"]
    _check("shot_01 character_count=0", it01.constraint.get("character_count") == 0,
           str(it01.constraint.get("character_count")))


# ---------------- 2. 序列化往返不丢约束 ----------------

def test_constraint_roundtrip_persisted() -> None:
    print("\n[2] to_dict/from_dict → constraint 持久化往返")
    intents = di.build_plan_intents(_plan())
    it07 = intents["scene_01:shot_07"]
    d = it07.to_dict()
    _check("to_dict 含 constraint key", "constraint" in d and d["constraint"] is not None,
           str(d.get("constraint")))
    restored = di.DirectorIntent.from_dict(d)
    _check("from_dict 恢复 constraint", isinstance(restored.constraint, dict)
           and restored.constraint["character_count"] == 3,
           str(restored.constraint))
    _check("from_dict 恢复 provenance.constraint",
           restored.provenance.get("constraint") == IntentSource.RULE,
           str(restored.provenance.get("constraint")))


# ---------------- 3. _build_description 注入双语约束块 ----------------

def test_h3_description_contains_constraint_block() -> None:
    print("\n[3] _build_description → 双语约束块注入（失败案例真实接线）")
    intents = di.build_plan_intents(_failure_plan())
    it = intents["scene_01:shot_01"]
    _check("接线后 constraint 非空", isinstance(it.constraint, dict)
           and it.constraint["character_count"] == 2, str(it.constraint))
    out = h3.build_h3_prompt(it, duration_sec=5)
    desc = out.integrated_multimodal_description
    for marker in ("角色锁定：画面中只有", "动作顺序：", "镜头锁定：", "禁止：",
                   "no character outside the listed cast"):
        _check(f"约束块含 {marker[:16]}…", marker in desc, desc[-200:])
    prov = out.provenance["integrated_multimodal_description"]
    _check("rule_generated 记「约束块」", "约束块" in prov.get("rule_generated", []),
           str(prov.get("rule_generated")))
    # 动作顺序按原文顺序编号，主/次核心动作已标注
    _check("动作顺序含核心动作「甩落桌面」", "甩落桌面" in desc,
           [a["text"] for a in it.constraint.get("action_timeline", [])])


def test_h3_no_constraint_backward_compat() -> None:
    print("\n[3b] 无 constraint → 不注入（旧链路向后兼容）")
    it = _failure_intent()
    it.constraint = None  # 模拟旧数据/手动构造无约束 intent
    it.provenance.pop("constraint", None)
    out = h3.build_h3_prompt(it, duration_sec=5)
    desc = out.integrated_multimodal_description
    _check("无「角色锁定：」注入", "角色锁定：画面中只有" not in desc)
    prov = out.provenance["integrated_multimodal_description"]
    _check("rule_generated 无「约束块」", "约束块" not in prov.get("rule_generated", []),
           str(prov.get("rule_generated")))


# ---------------- 3c. P2：外观锁定（PROMPT_COMPILER_V1 §4） ----------------

def test_h3_constraint_block_appearance_lock() -> None:
    print("\n[3c] P2 外观档案 → CharacterLock.appearance → 约束块「外观锁定」双语")
    plan = _plan()
    plan.character_profiles = {"柳如烟": "青衫襦裙，发髻簪钗"}
    intents = di.build_plan_intents(plan)
    it04 = intents["scene_01:shot_04"]
    lock = next(l for l in it04.constraint["character_locks"] if l["name"] == "柳如烟")
    _check("CharacterLock.appearance=档案外观", lock["appearance"] == "青衫襦裙，发髻簪钗",
           str(lock))
    out = h3.build_h3_prompt(it04, duration_sec=5)
    desc = out.integrated_multimodal_description
    _check("中文外观锁定行", "外观锁定：柳如烟=青衫襦裙，发髻簪钗，全程不变。" in desc,
           [l for l in desc.splitlines() if "外观锁定" in l])
    _check("英文外观锁定行", "Appearance lock: 柳如烟 — 青衫襦裙，发髻簪钗" in desc,
           [l for l in desc.splitlines() if "Appearance lock" in l])
    # 无外观档案角色 → 不渲染外观锁定行（但角色锁定仍在）
    it07 = intents["scene_01:shot_07"]
    lock7 = next(l for l in it07.constraint["character_locks"] if l["name"] == "沈青崖")
    _check("无档案角色 appearance 留空", lock7["appearance"] == "", str(lock7))
    desc7 = h3.build_h3_prompt(it07, duration_sec=5).integrated_multimodal_description
    _check("无档案 → 不渲染该角色外观锁定", "外观锁定：沈青崖" not in desc7,
           [l for l in desc7.splitlines() if "外观锁定" in l])


# ---------------- 4. build_constraint_check（/prompt/h3 constraint_check 报告） ----------------

def test_build_constraint_check_reports_failure_case() -> None:
    print("\n[4] build_constraint_check → constraint_check 报告（用户失败案例）")
    it = _failure_intent()
    report = cc.build_constraint_check({"scene_01:shot_01": it})
    _check("报告含该镜 key", "scene_01:shot_01" in report, str(report.keys()))
    issues = report["scene_01:shot_01"]
    codes = [i["code"] for i in issues]
    _check("多景别 warning 必现", "multi_shot_size" in codes, str(codes))
    _check("角色数量 info 必现", "character_count" in codes, str(codes))
    _check("场景锁定 info 必现", "scene_locked" in codes, str(codes))
    multi = next(i for i in issues if i["code"] == "multi_shot_size")
    _check("多景别消息提示拆镜", "请拆成两个 Shot" in multi["message"], multi["message"])
    _check("多景别 severity=warning", multi["severity"] == "warning", multi["severity"])
    _check("issue 已 dict 化（JSON 可序列化）",
           isinstance(multi["fix"], str) and isinstance(multi["message"], str),
           str(type(multi.get("fix"))))


def test_build_constraint_check_empty_defense() -> None:
    print("\n[4b] build_constraint_check 空/None 防御")
    _check("None → {}", cc.build_constraint_check(None) == {})
    _check("空 dict → {}", cc.build_constraint_check({}) == {})
    _check("含 None intent 跳过", cc.build_constraint_check({"k": None}) == {})


def test_build_plan_intents_check_pipeline() -> None:
    print("\n[4c] 真实全链路：build_plan_intents → build_constraint_check")
    intents = di.build_plan_intents(_plan())
    report = cc.build_constraint_check(intents)
    # shot_01 无角色 → missing_character_count warning 必现
    issues01 = report["scene_01:shot_01"]
    codes01 = [i["code"] for i in issues01]
    _check("shot_01 missing_character_count", "missing_character_count" in codes01,
           str(codes01))
    # shot_07 三角色 → character_count info + scene_locked info
    issues07 = report["scene_01:shot_07"]
    codes07 = [i["code"] for i in issues07]
    _check("shot_07 无 missing_character_count", "missing_character_count" not in codes07,
           str(codes07))
    _check("shot_07 有 character_count info", "character_count" in codes07, str(codes07))


# ---------------- 5. 零 GPU 副作用 ----------------

def test_wiring_no_gpu_side_effects() -> None:
    print("\n[5] 接线文件零显存零网络")
    import inspect
    for mod in (cc, h3):
        src = inspect.getsource(mod)
        for banned in ("torch.float", "ollama", "requests.get", "http://"):
            assert banned not in src, f"{mod.__name__} 引用了禁止依赖 {banned}"


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                FAILED += 1
                print(f"  ✗ {name}")
                traceback.print_exc()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)
