#!/usr/bin/env python3
"""P1-B-4 · Shot → DirectorIntent（端到端接线回归，复用已验收，不新架构）。

P1B_STORY_ANALYZER_PLAN.md §8（用户 2026-08-14 拍板 + 锁死实现细节）：
    P1-B-4 只接线 + 回归，不改 build_plan_intents 架构：
    build_plan_from_beats（B-3 已验收）→ build_plan_intents（P0 已验收）→ {key: DirectorIntent}

本文件锁死 5 条接线（全部**零 LLM 零 GPU**，直接用系统 python 跑）：
1. 贯通点：B-3 写入 shot.visual_intent → DirectorIntent.composition（build_shot_intent 逐字投影）；
2. 原文逐字：user_original_intent == shot.source_text（USER 层最高优先级，任何阶段不得覆盖）；
3. 运镜接线：pick_camera 状态机按 timeline 传 prev_state → camera_position/movement/camera_desc
   全部非空（B-3 shot 无 cast 也能吃，establish 兜底）；
4. 双前驱连续性：timeline 画面前驱 timeline_prev_shot_id + 场景上次状态
   scene_prev_state/scene_prev_shot_id；切新场景 scene_cut、切回旧场景 cross_cut；
5. 实体边界：B-3 shot 不产 entities → intent.entities 为空（char_anchors 过滤安全）。

另覆盖：overrides_by_shot（P0-A 五区覆盖仍工作，原文事实不可覆盖）、
退化路径全链路（extract_beats(None)→…→intents）、不 mutate plan。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_story_analyzer_intents.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import director_intent as di  # noqa: E402
from director.production_plan import StoryBeat  # noqa: E402
from director.story_analyzer import (  # noqa: E402
    _core,
    build_plan_from_beats,
    extract_beats,
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


def _beat(bid: str, order: int, func: str, scene: str = "",
          segments: list[str] | None = None, summary: str = "",
          time: str = "", weather: str = "") -> StoryBeat:
    return StoryBeat(
        beat_id=bid,
        title=f"节拍{bid}",
        summary=summary,
        dramatic_function=func,
        scene_id=scene,
        time=time,
        weather=weather,
        text_segments=segments or [f"原文{bid}"],
        order=order,
        source="ai",
    )


def _all_shots(plan) -> list:
    return [sh for s in plan.scenes for sh in s.shots]


def _shot_by_id(plan, shot_id: str):
    for sh in _all_shots(plan):
        if sh.shot_id == shot_id:
            return sh
    return None


def _shot_flat(plan) -> str:
    by_id = {sh.shot_id: sh for s in plan.scenes for sh in s.shots}
    return "".join(_core(by_id[e.split(":", 1)[1]].source_text) for e in plan.timeline)


# ---------------- 1. 贯通点：visual_intent → composition ----------------

def test_wiring_visual_intent_to_composition() -> None:
    print("\n[1] 贯通点：B-3 visual_intent → DirectorIntent.composition（逐字投影）")
    text = ("柳如烟轻声问道：「客官从何处来？」沈青崖答道：「一路往北。」她又问：「可要住店？」"
            "他摇头：「先找个人。」她指着楼上：「那人就在楼上等你。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
                   segments=[text], summary="对话", weather="雨")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    intents = di.build_plan_intents(plan)
    _check("intent 数 == timeline 数", len(intents) == len(plan.timeline) == 3,
           f"{len(intents)} vs {len(plan.timeline)}")
    _check("intent 键 == timeline 条目", set(intents.keys()) == set(plan.timeline),
           str(set(intents.keys())))
    for e in plan.timeline:
        sh = _shot_by_id(plan, e.split(":", 1)[1])
        it = intents[e]
        _check(f"{e} 原文逐字 user_original_intent", it.user_original_intent == sh.source_text,
               f"{it.user_original_intent!r} vs {sh.source_text!r}")
        _check(f"{e} composition == visual_intent", it.composition == sh.visual_intent,
               f"{it.composition!r} vs {sh.visual_intent!r}")
        _check(f"{e} composition 含 B-3 景别/运镜",
               ("景别=" in it.composition) and ("运镜=" in it.composition),
               it.composition)
    first = plan.timeline[0]
    _check("首镜 composition 含「景别=中景」（dialogue two_shot）",
           "景别=中景" in intents[first].composition, intents[first].composition)
    _check("audio.ambient=雨声淅沥（weather 贯通）",
           intents[first].audio.get("ambient") == "雨声淅沥",
           str(intents[first].audio.get("ambient")))


# ---------------- 2. 运镜接线：pick_camera 状态机 ----------------

def test_wiring_camera_state_machine() -> None:
    print("\n[2] 运镜接线：pick_camera 按 timeline 传 prev_state，camera 三字段非空")
    text = ("柳如烟轻声问道：「客官从何处来？」沈青崖答道：「一路往北。」她又问：「可要住店？」"
            "他摇头：「先找个人。」她指着楼上：「那人就在楼上等你。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
                   segments=[text], summary="对话", time="黄昏", weather="雨")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    intents = di.build_plan_intents(plan)
    for i, e in enumerate(plan.timeline):
        it = intents[e]
        _check(f"{e} camera_position 非空", bool(it.camera_position), it.camera_position)
        _check(f"{e} movement 非空", bool(it.movement), it.movement)
        _check(f"{e} continuity.camera_desc 非空", bool(it.continuity.get("camera_desc")),
               str(it.continuity.get("camera_desc")))
        _check(f"{e} continuity.camera_template 非空", bool(it.continuity.get("camera_template")),
               str(it.continuity.get("camera_template")))
        if i == 0:
            _check("首镜 has_prev=False", it.continuity.get("has_prev") is False,
                   str(it.continuity.get("has_prev")))
        else:
            _check(f"{e} has_prev=True", it.continuity.get("has_prev") is True,
                   str(it.continuity.get("has_prev")))
    _check("尾镜 is_last_shot=True（场景尾镜收束）",
           intents[plan.timeline[-1]].continuity.get("is_last_shot") is True,
           str(intents[plan.timeline[-1]].continuity.get("is_last_shot")))


def test_wiring_transition_blueprint_establish() -> None:
    print("\n[3] 无 cast 纯环境镜：pick_camera establish 兜底 + subject 回退场景名")
    text = "雨停了，山雾漫向山雨楼，檐角灯笼昏黄。"
    beats = [_beat("beat_01", 1, "transition", scene="山雨楼外",
                   segments=[text], summary="", weather="雨")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    intents = di.build_plan_intents(plan)
    e = plan.timeline[0]
    it = intents[e]
    _check("1 镜", len(plan.timeline) == 1, str(plan.timeline))
    _check("camera_position=全景（establish wide）", it.camera_position == "全景", it.camera_position)
    _check("movement=缓摇（establish pan）", it.movement == "缓摇", it.movement)
    _check("composition 含「景别=远景」（B-3 establishing 模板）",
           "景别=远景" in it.composition and "运镜=拉远" in it.composition, it.composition)
    _check("subject 回退场景名（无角色无实体）", it.subject == "山雨楼外", it.subject)
    _check("characters 为空（无对白说话者）", it.characters == [], str(it.characters))


# ---------------- 3. 双前驱连续性：跨地点交叉剪辑 ----------------

def test_wiring_cross_location_dual_prev() -> None:
    print("\n[4] 双前驱连续性：大堂→后院→大堂 交叉剪辑（scene_cut / cross_cut）")
    text = ("柳如烟在柜台后问道：「客官，打尖还是住店？」\n\n"
            "黑影一路退入后院，一闪没入柴房。\n\n"
            "柳如烟追回山雨楼大堂，喘着气道：「你把人怎么了？」")
    beats = [
        _beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
              segments=["柳如烟在柜台后问道：「客官，打尖还是住店？」"], summary="大堂问话"),
        _beat("beat_02", 2, "action", scene="后院",
              segments=["黑影一路退入后院，一闪没入柴房。"], summary="退入后院"),
        _beat("beat_03", 3, "dialogue", scene="山雨楼大堂",
              segments=["柳如烟追回山雨楼大堂，喘着气道：「你把人怎么了？」"], summary="回到大堂"),
    ]
    plan = build_plan_from_beats(text, beats, [], title="t")
    intents = di.build_plan_intents(plan)
    sc = {s.scene_id: s.location_name for s in plan.scenes}
    hall_id = [k for k, v in sc.items() if v == "山雨楼大堂"][0]
    yard_id = [k for k, v in sc.items() if v == "后院"][0]
    seq = [e.split(":")[0] for e in plan.timeline]
    _check("timeline 跨地点（大堂→后院→大堂）", seq[0] == hall_id and
           seq[-1] == hall_id and yard_id in seq, str(plan.timeline))
    # 找到后院首镜 + 其后切回大堂的镜
    first_yard = next(i for i, sid in enumerate(seq) if sid == yard_id)
    return_hall = next(i for i, sid in enumerate(seq[first_yard + 1:]) if sid == hall_id) + first_yard + 1
    first_key = plan.timeline[0]
    yard_key = plan.timeline[first_yard]
    hall_return_key = plan.timeline[return_hall]

    it_yard = intents[yard_key]
    _check("后院首镜 transition_type=scene_cut（切入新场景）",
           it_yard.continuity.get("transition_type") == "scene_cut",
           str(it_yard.continuity.get("transition_type")))
    _check("后院首镜 timeline_prev_shot_id == 大堂前驱",
           it_yard.continuity.get("timeline_prev_shot_id") == first_key,
           str(it_yard.continuity.get("timeline_prev_shot_id")))
    _check("后院首镜无 scene_prev_state（该场景首次出现）",
           "scene_prev_state" not in it_yard.continuity, str(it_yard.continuity))

    it_back = intents[hall_return_key]
    _check("切回大堂 transition_type=cross_cut（平行剪辑切回）",
           it_back.continuity.get("transition_type") == "cross_cut",
           str(it_back.continuity.get("transition_type")))
    _check("切回大堂 timeline_prev_shot_id == 后院前驱",
           it_back.continuity.get("timeline_prev_shot_id") == yard_key,
           str(it_back.continuity.get("timeline_prev_shot_id")))
    _check("切回大堂 scene_prev_shot_id == 大堂首镜（场景上次状态）",
           it_back.continuity.get("scene_prev_shot_id") == first_key,
           str(it_back.continuity.get("scene_prev_shot_id")))
    _check("切回大堂 scene_prev_state 空间状态延续",
           (it_back.continuity.get("scene_prev_state") or {}).get("location") == "山雨楼大堂",
           str(it_back.continuity.get("scene_prev_state")))

    it_first = intents[first_key]
    _check("timeline 首镜无 transition_type（无画面前驱）",
           "transition_type" not in it_first.continuity, str(it_first.continuity))


# ---------------- 4. 退化路径全链路 + 原文逐字 ----------------

def test_wiring_fallback_chain_verbatim() -> None:
    print("\n[5] 退化路径全链路：extract_beats(None) → plan → intents（原文逐字贯穿）")
    text = ("雨刚停，山雾漫向山雨楼。\n\n"
            "沈青崖走进客栈，环视堂内。\n\n"
            "柳如烟问道：「客官，打尖还是住店？」\n\n"
            "沈青崖低声道：「找人。」")
    beats, rule_only, warnings = extract_beats(text, None, title="t")
    _check("rule_only=True（无 AI 不阻塞）", rule_only is True)
    plan = build_plan_from_beats(text, beats, warnings, title="t")
    intents = di.build_plan_intents(plan)
    _check("intent 数 == timeline 数", len(intents) == len(plan.timeline) >= 1,
           f"{len(intents)} vs {len(plan.timeline)}")
    joined = "".join(_core(intents[e].user_original_intent) for e in plan.timeline)
    _check("user_original_intent 按 timeline 拼接 == 原文（逐字贯穿 B-4）",
           joined == _core(text), f"{joined} vs {_core(text)}")
    _check("intent 键都可定位", all(e in intents for e in plan.timeline),
           str(plan.timeline))


# ---------------- 5. 实体边界 + overrides + 不 mutate ----------------

def test_wiring_entity_boundary_empty() -> None:
    print("\n[6] 实体边界：B-3 shot 不产 entities → intent.entities 为空（char_anchors 过滤安全）")
    text = ("柳如烟轻声问道：「客官从何处来？」沈青崖答道：「一路往北。」她又问：「可要住店？」"
            "他摇头：「先找个人。」她指着楼上：「那人就在楼上等你。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
                   segments=[text], summary="对话", weather="雨")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    intents = di.build_plan_intents(plan)
    for e in plan.timeline:
        _check(f"{e} entities 为空", intents[e].entities == [], str(intents[e].entities))
        _check(f"{e} subject 非空（场景/角色回退）", bool(intents[e].subject), intents[e].subject)


def test_wiring_overrides_by_shot() -> None:
    print("\n[7] P0-A 五区 overrides 仍工作：composition/camera 可覆盖、原文事实不可覆盖")
    text = ("柳如烟轻声问道：「客官从何处来？」沈青崖答道：「一路往北。」她又问：「可要住店？」"
            "他摇头：「先找个人。」她指着楼上：「那人就在楼上等你。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
                   segments=[text], summary="对话", weather="雨")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    first_key = plan.timeline[0]
    second_key = plan.timeline[1]
    overrides = {
        first_key: {"visual": "雨夜俯拍全景，檐角灯笼为视觉锚点",
                   "cameraText": "缓慢拉升"},
    }
    intents = di.build_plan_intents(plan, overrides_by_shot=overrides)
    sh_first = _shot_by_id(plan, first_key.split(":", 1)[1])
    it = intents[first_key]
    _check("composition 被用户 visual 覆盖",
           it.composition == "雨夜俯拍全景，檐角灯笼为视觉锚点", it.composition)
    _check("provenance.composition=USER", it.provenance.get("composition") == "USER",
           str(it.provenance.get("composition")))
    _check("continuity.camera_desc 被 cameraText 覆盖",
           it.continuity.get("camera_desc") == "缓慢拉升",
           str(it.continuity.get("camera_desc")))
    _check("原文事实不可覆盖（user_original_intent 不变）",
           it.user_original_intent == sh_first.source_text, it.user_original_intent)
    it2 = intents[second_key]
    sh2 = _shot_by_id(plan, second_key.split(":", 1)[1])
    _check("未覆盖镜不受影响（composition 仍 == visual_intent）",
           it2.composition == sh2.visual_intent, it2.composition)


def test_wiring_no_mutate_plan() -> None:
    print("\n[8] build_plan_intents 不 mutate plan")
    text = ("柳如烟轻声问道：「客官从何处来？」沈青崖答道：「一路往北。」她又问：「可要住店？」"
            "他摇头：「先找个人。」她指着楼上：「那人就在楼上等你。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
                   segments=[text], summary="对话", weather="雨")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    before_src = [sh.source_text for sh in _all_shots(plan)]
    before_tl = list(plan.timeline)
    di.build_plan_intents(plan)
    _check("shot.source_text 未变", [sh.source_text for sh in _all_shots(plan)] == before_src)
    _check("timeline 未变", list(plan.timeline) == before_tl)
    _check("覆盖链仍完整（shot 拼接 == 原文）", _shot_flat(plan) == _core(text))


def main() -> None:
    test_wiring_visual_intent_to_composition()
    test_wiring_camera_state_machine()
    test_wiring_transition_blueprint_establish()
    test_wiring_cross_location_dual_prev()
    test_wiring_fallback_chain_verbatim()
    test_wiring_entity_boundary_empty()
    test_wiring_overrides_by_shot()
    test_wiring_no_mutate_plan()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
