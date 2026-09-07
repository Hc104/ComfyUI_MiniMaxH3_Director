#!/usr/bin/env python3
"""P1-B-3 · Beat → Shot（Shot Blueprint 导演规则）单元测试。

覆盖（P1B_STORY_ANALYZER_PLAN.md §6/§7，用户 2026-08-14 拍板 + 锁死实现细节 2/3）：
1. §6.1 各 dramatic_function 模板表：dialogue=two_shot+正反打、action=wide+medium+fast_zoom、
   transition=establishing、plant_clue=establishing+insert；
2. §6.2 特征启用：短 Beat 只取 1-2 模板、dialogue 无对白降级、has_prop_clue 追加 insert、
   location_change 前置 establishing、emotion_shift 追加 closeup 慢推；
3. §6.3 Shot 独立 Location 三步兜底：shot 文本首个已知地点 > 继承上一 shot > beat.scene_id；
4. §6.5 覆盖链（核心锁死）：所有 Shot.source_text 拼接（保序去重）→ 完整覆盖
   Beat.text_segments 拼接 → 完整覆盖小说正文，无遗漏、无重复、原文逐字；
5. §7 编排：跨地点交叉剪辑（大堂→后院→大堂）、同名地点合并同一 scene_id、
   timeline 逐 Beat 逐 shot、beats.entries 记录、不流水账（6 句对话仍 3 镜非 6 镜）、
   退化路径集成、无地点兜底「未命名地点」。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_story_analyzer_shots.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import StoryBeat  # noqa: E402
from director.story_analyzer import (  # noqa: E402
    SHOT_BLUEPRINTS,
    _core,
    _location_scan_table,
    _select_template_subsets,
    _shot_features,
    _split_segments_into_chunks,
    build_plan_from_beats,
    extract_beats,
    resolve_shot_location,
    shot_coverage_check,
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
          segments: list[str] | None = None, summary: str = "") -> StoryBeat:
    return StoryBeat(
        beat_id=bid,
        title=f"节拍{bid}",
        summary=summary,
        dramatic_function=func,
        scene_id=scene,
        text_segments=segments or [f"原文{bid}"],
        order=order,
        source="ai",
    )


def _plan_beats(plan) -> list[StoryBeat]:
    return list(plan.beats)


def _shot_flat(plan) -> str:
    # 按 timeline 播放顺序展平（timeline=顺序权威，scene 分组仅存储）；跨地点交叉剪辑安全
    by_id = {sh.shot_id: sh for s in plan.scenes for sh in s.shots}
    return "".join(_core(by_id[e.split(":", 1)[1]].source_text) for e in plan.timeline)


def _beat_flat(plan) -> str:
    return "".join(_core(seg) for b in plan.beats for seg in b.text_segments)


def _all_shots(plan) -> list:
    return [sh for s in plan.scenes for sh in s.shots]


# ---------------- 1. §6.1 各 dramatic_function 模板表 ----------------

def test_blueprint_dialogue_default() -> None:
    print("\n[1] dialogue Blueprint：two_shot + 正反打（3 模板，长文本无干扰特征）")
    text = ("柳如烟问道：「客官，打尖还是住店？」沈青崖低声道：「找人。」"
            "柳如烟又问：「可要住店？」沈青崖答道：「先上一壶茶。」"
            "柳如烟点点头，转身去柜台张罗，顺手把檐下的灯笼挑亮了些。")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂", segments=[text],
                   summary="客栈对话")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    shots = _all_shots(plan)
    _check("3 镜（two_shot+正反打，无 optional insert）", len(shots) == 3, str(len(shots)))
    _check("镜1 景别=中景 运镜=固定", "景别=中景" in shots[0].visual_intent and "运镜=固定" in shots[0].visual_intent,
           shots[0].visual_intent)
    _check("镜2 近景 正打", "景别=近景" in shots[1].visual_intent and "正打" in shots[1].visual_intent,
           shots[1].visual_intent)
    _check("镜3 近景 反打", "景别=近景" in shots[2].visual_intent and "反打" in shots[2].visual_intent,
           shots[2].visual_intent)
    _check("对白逐字进入 source_text", "找人" in shots[0].source_text or "找人" in shots[1].source_text)


def test_blueprint_action() -> None:
    print("\n[2] action Blueprint：wide + medium + fast_zoom（3 镜）")
    text = ("沈青崖拔剑追向蒙面人，破门冲入后院。黑影反手挥刀，刀光擦着他耳边掠过，"
            "沈青崖侧身闪过。他趁机一剑刺向对方肩头，逼得黑影连连后退。")
    beats = [_beat("beat_01", 1, "action", scene="后院", segments=[text], summary="后院搏斗")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    shots = _all_shots(plan)
    _check("3 镜（wide/medium/fast_zoom）", len(shots) == 3, str(len(shots)))
    _check("镜1 全景 甩镜", "景别=全景" in shots[0].visual_intent and "甩镜" in shots[0].visual_intent,
           shots[0].visual_intent)
    _check("镜2 中景 跟拍", "景别=中景" in shots[1].visual_intent and "跟拍" in shots[1].visual_intent,
           shots[1].visual_intent)
    _check("镜3 特写 推进", "景别=特写" in shots[2].visual_intent and "推进" in shots[2].visual_intent,
           shots[2].visual_intent)


def test_blueprint_transition_short() -> None:
    print("\n[3] transition Blueprint：establishing 远景拉远（短文本 → 1 镜）")
    text = "夜色渐深，山雨楼檐下的灯笼一盏盏熄灭。"
    beats = [_beat("beat_01", 1, "transition", scene="山雨楼外", segments=[text], summary="")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    shots = _all_shots(plan)
    _check("1 镜", len(shots) == 1, str(len(shots)))
    _check("远景 拉远", "景别=远景" in shots[0].visual_intent and "拉远" in shots[0].visual_intent,
           shots[0].visual_intent)


def test_blueprint_plant_clue() -> None:
    print("\n[4] plant_clue Blueprint：establishing + insert（2 模板）")
    features = {"func": "plant_clue", "len": 30, "short": True,
                "has_dialogue": False, "has_action": False, "has_prop_clue": False,
                "location_change": False, "emotion_shift": False}
    subs = _select_template_subsets(SHOT_BLUEPRINTS["plant_clue"], features)
    _check("短文本只取前 1 个", len(subs) == 1, str([t["shot_type"] for t in subs]))
    features2 = dict(features, short=False)
    subs2 = _select_template_subsets(SHOT_BLUEPRINTS["plant_clue"], features2)
    _check("非短文本 2 模板（establishing+insert）",
           [t["shot_type"] for t in subs2] == ["establishing", "insert"],
           str([t["shot_type"] for t in subs2]))


# ---------------- 2. §6.2 特征启用 ----------------

def test_feature_short_beat() -> None:
    print("\n[5] 短 Beat：<40 字只取 1 模板；40-59 字取 2 模板")
    f1 = {"func": "dialogue", "len": 20, "short": True, "has_dialogue": True,
          "has_action": False, "has_prop_clue": False, "location_change": False, "emotion_shift": False}
    _check("20 字 dialogue → 1 模板", len(_select_template_subsets(SHOT_BLUEPRINTS["dialogue"], f1)) == 1)
    f2 = dict(f1, len=50)
    _check("50 字 dialogue → 2 模板", len(_select_template_subsets(SHOT_BLUEPRINTS["dialogue"], f2)) == 2)


def test_feature_dialogue_no_speech() -> None:
    print("\n[6] dialogue 无对白 → 降级 establishing + reaction（不硬套正反打）")
    text = ("沈青崖坐在大堂角落，目光扫过堂内每个人的脸，迟迟没有说话。"
            "柳如烟站在柜台后擦拭茶盏，偶尔抬眼打量他。")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂", segments=[text], summary="堂内气氛")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    shots = _all_shots(plan)
    vi = " | ".join(sh.visual_intent for sh in shots)
    _check("不出现 two_shot", "two_shot" not in vi and "正打" not in vi and "反打" not in vi, vi)
    _check("出现 establishing（远景）", "establishing" in vi or "景别=远景" in vi, vi)
    _check("出现 reaction（拉远）", "reaction" in vi or "拉远" in vi, vi)


def test_feature_prop_clue_insert() -> None:
    print("\n[7] has_prop_clue → dialogue 追加 insert（optional 保留）")
    features = {"func": "dialogue", "len": 80, "short": False, "has_dialogue": True,
                "has_action": False, "has_prop_clue": True, "location_change": False, "emotion_shift": False}
    subs = _select_template_subsets(SHOT_BLUEPRINTS["dialogue"], features)
    _check("4 模板含 insert",
           [t["shot_type"] for t in subs] == ["two_shot", "closeup", "closeup", "insert"],
           str([t["shot_type"] for t in subs]))


def test_feature_location_change_establishing() -> None:
    print("\n[8] location_change（文本提 ≥2 已知地点）→ 前置 establishing")
    text = ("柳如烟在柜台后问道：「客官，打尖还是住店？」沈青崖低声道：「找人。」\n\n"
            "沈青崖从山雨楼大堂拔剑追出。黑影一路退入后院，一闪没入柴房。")
    beats = [
        _beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
              segments=["柳如烟在柜台后问道：「客官，打尖还是住店？」沈青崖低声道：「找人。」"],
              summary="大堂问话"),
        _beat("beat_02", 2, "action", scene="后院",
              segments=["沈青崖从山雨楼大堂拔剑追出。黑影一路退入后院，一闪没入柴房。"],
              summary="跨地点追逐"),
    ]
    plan = build_plan_from_beats(text, beats, [], title="t")
    shots = _all_shots(plan)
    action_first = [sh for sh in shots if "拔剑追出" in sh.source_text][0]
    _check("跨地点 Beat 首镜 establishing（转入新地点）", "establishing" in action_first.visual_intent,
           action_first.visual_intent)
    sc = {s.scene_id: s.location_name for s in plan.scenes}
    _check("2 个 Scene（大堂/后院）", len(sc) == 2, str(sc))
    tl_first = plan.timeline[0].split(":")[0]
    tl_last = plan.timeline[-1].split(":")[0]
    _check("timeline 跨地点（前大堂后后院）", tl_first != tl_last, str(plan.timeline))
    _check("覆盖链完整", _shot_flat(plan) == _core(text), _shot_flat(plan))


def test_feature_emotion_shift() -> None:
    print("\n[9] emotion_shift → 追加 closeup 慢推（缺特写时）")
    features = {"func": "action", "len": 80, "short": False, "has_dialogue": False,
                "has_action": True, "has_prop_clue": False, "location_change": False, "emotion_shift": True}
    subs = _select_template_subsets(SHOT_BLUEPRINTS["action"], features)
    _check("action+情绪转折 → 4 模板含特写慢推",
           [t["shot_type"] for t in subs] == ["wide", "medium", "fast_zoom", "closeup"],
           str([t["shot_type"] for t in subs]))


# ---------------- 3. §6.3 Shot 独立 Location 三步兜底 ----------------

def test_shot_location_three_step() -> None:
    print("\n[10] resolve_shot_location 三步兜底")
    beat = _beat("beat_01", 1, "action", scene="location_01",
                 segments=["他追进后院。"], summary="追")
    loc = _location_scan_table({"location_01": "山雨楼大堂", "location_02": "后院"})
    _check("1) shot 文本含「后院」→ location_02",
           resolve_shot_location("他追进后院，四下张望。", beat, loc, None) == "location_02")
    _check("2) 无地点词 → 继承上一 shot（location_02）",
           resolve_shot_location("他握紧剑柄。", beat, loc, "location_02") == "location_02")
    _check("3) 首镜无地点词 → beat.scene_id（location_01）",
           resolve_shot_location("他握紧剑柄。", beat, loc, None) == "location_01")
    _check("长名优先：'山雨楼大堂' 命中", resolve_shot_location("山雨楼大堂内，烛火摇曳。", beat, loc, None) == "location_01")


def test_split_chunks_cover_original() -> None:
    print("\n[11] _split_segments_into_chunks：拼接还原原文")
    segs = ["第一段。第二段。第三段。", "第四段。第五段。"]
    chunks = _split_segments_into_chunks(segs, 3)
    _check("3 个非空 chunk", len(chunks) == 3, str(chunks))
    _check("拼接 == 原文（去空白）", _core("".join(chunks)) == _core("".join(segs)), "".join(chunks))
    _check("无空 chunk", all(c.strip() for c in chunks))
    _check("n=1 → 整体", _split_segments_into_chunks(segs, 1) == ["第一段。第二段。第三段。第四段。第五段。"])
    _check("句子数 < n → 不硬凑", len(_split_segments_into_chunks(["只有一句。"], 4)) == 1)


# ---------------- 4. §6.5 覆盖链（核心锁死） ----------------

def test_coverage_chain_single_beat() -> None:
    print("\n[12] 覆盖链：单 Beat 拆 3 镜，拼接完整覆盖原文（无遗漏无重复）")
    text = ("柳如烟轻声问道：「客官从何处来？」沈青崖答道：「一路往北。」她又问：「可要住店？」"
            "他摇头：「先找个人。」她指着楼上：「那人就在楼上等你。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
                   segments=[text], summary="对话")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    _check("3 镜", len(_all_shots(plan)) == 3, str(len(_all_shots(plan))))
    _check("shot 拼接 == beat 拼接", _shot_flat(plan) == _beat_flat(plan),
           f"{_shot_flat(plan)} vs {_beat_flat(plan)}")
    _check("shot 拼接 == 原文", _shot_flat(plan) == _core(text), _shot_flat(plan))
    _check("覆盖链无 warning", shot_coverage_check(plan) == [], str(shot_coverage_check(plan)))
    # 保序去重：逐镜 source_text 顺序拼接（不重复）
    seq = "".join(_core(sh.source_text) for sh in _all_shots(plan))
    _check("保序（句首在最前，句尾在最后）",
           seq.startswith("柳如烟轻声问道") and seq.endswith("楼上等你。」"), seq)


def test_coverage_chain_multi_beat() -> None:
    print("\n[13] 覆盖链：多 Beat 多镜，拼接完整覆盖多段正文")
    text = ("柳如烟在大堂柜台后问道：「客官，打尖还是住店？」沈青崖低声道：「找人。」\n\n"
            "沈青崖从大堂一路追到后院，黑影一闪没入柴房。\n\n"
            "柳如烟追回大堂，喘着气道：「你把后院那人怎么了？」")
    beats = [
        _beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
              segments=["柳如烟在大堂柜台后问道：「客官，打尖还是住店？」沈青崖低声道：「找人。」"],
              summary="客栈问话"),
        _beat("beat_02", 2, "action", scene="后院",
              segments=["沈青崖从大堂一路追到后院，黑影一闪没入柴房。"], summary="追入后院"),
        _beat("beat_03", 3, "dialogue", scene="山雨楼大堂",
              segments=["柳如烟追回大堂，喘着气道：「你把后院那人怎么了？」"], summary="追问"),
    ]
    plan = build_plan_from_beats(text, beats, [], title="t")
    _check("覆盖链：shot 拼接 == 原文", _shot_flat(plan) == _core(text),
           f"{_shot_flat(plan)} vs {_core(text)}")
    _check("覆盖链无 warning", shot_coverage_check(plan) == [], str(shot_coverage_check(plan)))
    _check("timeline 每条可定位", all(
        any(s.scene_id == e.split(":")[0] and any(sh.shot_id == e.split(":")[1] for sh in s.shots)
            for s in plan.scenes) for e in plan.timeline))


def test_coverage_chain_duplicate_free() -> None:
    print("\n[14] 覆盖链：镜头边界不产生原文重复计算")
    text = ("柳如烟问道：「客官，打尖还是住店？」沈青崖道：「找人。」柳如烟又问：「找什么人？」"
            "沈青崖道：「一个姓陆的。」柳如烟摇头道：「这里没有姓陆的客人。」沈青崖道：「替我留一间上房。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="大堂",
                   segments=[text], summary="多句对话")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    _check("6 句 → 3 镜（不逐句切镜）", len(_all_shots(plan)) == 3, str(len(_all_shots(plan))))
    _check("拼接不重复（「住店」1 次、「上房」1 次）",
           _shot_flat(plan).count("住店") == 1 and _shot_flat(plan).count("上房") == 1, _shot_flat(plan))


# ---------------- 5. §7 编排：跨地点 / 同名合并 / 不流水账 / 集成 ----------------

def test_cross_location_same_scene_merge() -> None:
    print("\n[15] 跨地点交叉剪辑 + 同名地点合并同一 scene_id")
    text = ("柳如烟在大堂问道：「客官，打尖还是住店？」沈青崖低声道：「找人。」\n\n"
            "沈青崖从山雨楼大堂拔剑追出。黑影退入后院，没入柴房。\n\n"
            "柳如烟追回山雨楼大堂，喘着气道：「你把人怎么了？」")
    beats = [
        _beat("beat_01", 1, "dialogue", scene="山雨楼大堂",
              segments=["柳如烟在大堂问道：「客官，打尖还是住店？」沈青崖低声道：「找人。」"],
              summary="大堂问话"),
        _beat("beat_02", 2, "action", scene="后院",
              segments=["沈青崖从山雨楼大堂拔剑追出。黑影退入后院，没入柴房。"], summary="追入后院"),
        _beat("beat_03", 3, "dialogue", scene="山雨楼大堂",
              segments=["柳如烟追回山雨楼大堂，喘着气道：「你把人怎么了？」"], summary="回到大堂"),
    ]
    plan = build_plan_from_beats(text, beats, [], title="t")
    _check("覆盖链：shot 拼接 == 原文（timeline 序）", _shot_flat(plan) == _core(text),
           f"{_shot_flat(plan)} vs {_core(text)}")
    sc = {s.scene_id: s.location_name for s in plan.scenes}
    _check("同名「山雨楼大堂」合并为 1 个 Scene", list(sc.values()).count("山雨楼大堂") == 1, str(sc))
    _check("2 个 Scene", len(sc) == 2, str(sc))
    loc1 = [k for k, v in sc.items() if v == "山雨楼大堂"][0]
    loc2 = [k for k, v in sc.items() if v == "后院"][0]
    _check("timeline 首镜在大堂", plan.timeline[0].split(":")[0] == loc1, str(plan.timeline))
    _check("timeline 跨到后院再回大堂（交叉剪辑）",
           plan.timeline[-1].split(":")[0] == loc1 and
           any(e.split(":")[0] == loc2 for e in plan.timeline), str(plan.timeline))
    # beat.entries 记录
    for b in _plan_beats(plan):
        _check(f"{b.beat_id} entries 非空", len(b.entries) >= 1, str(b.entries))
        _check(f"{b.beat_id} entries 可定位", all(
            any(s.scene_id == e.split(":")[0] and any(sh.shot_id == e.split(":")[1] for sh in s.shots)
                for s in plan.scenes) for e in b.entries))


def test_no_word_by_word() -> None:
    print("\n[16] 不流水账：6 句对话 → 3 镜（不是 6 镜逐句切）")
    text = ("柳如烟问道：「客官，打尖还是住店？」沈青崖道：「找人。」柳如烟又问：「找人？找什么人？」"
            "沈青崖道：「一个姓陆的。」柳如烟皱眉道：「姓陆的？这里没有姓陆的客人。」"
            "沈青崖道：「那就从明天起，替我留一间上房。」")
    beats = [_beat("beat_01", 1, "dialogue", scene="大堂", segments=[text], summary="大堂多轮对话")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    shots = _all_shots(plan)
    _check("3 镜（two_shot + 正反打）", len(shots) == 3, str(len(shots)))
    _check("覆盖完整", _shot_flat(plan) == _core(text), _shot_flat(plan))


def test_build_plan_integration_fallback() -> None:
    print("\n[17] 退化路径集成：extract_beats(None) → build_plan_from_beats 全链路")
    text = ("雨刚停，山雾漫向山雨楼。\n\n"
            "沈青崖走进客栈，环视堂内。\n\n"
            "柳如烟问道：「客官，打尖还是住店？」\n\n"
            "沈青崖低声道：「找人。」")
    beats, rule_only, warnings = extract_beats(text, None, title="t")
    _check("rule_only=True", rule_only is True)
    plan = build_plan_from_beats(text, beats, warnings, title="t")
    _check("有 Scene", len(plan.scenes) >= 1, str([s.scene_id for s in plan.scenes]))
    _check("有 Shot", len(_all_shots(plan)) >= 1, str(len(_all_shots(plan))))
    _check("timeline 非空", len(plan.timeline) >= 1, str(plan.timeline))
    _check("覆盖链：shot 拼接 == 原文", _shot_flat(plan) == _core(text),
           f"{_shot_flat(plan)} vs {_core(text)}")
    _check("无覆盖链 warning", not any("覆盖链" in w for w in plan.validation.warnings),
           str(plan.validation.warnings))
    _check("结构校验 valid", plan.validation.status == "valid", str(plan.validation.status))


def test_fallback_unnamed_location() -> None:
    print("\n[18] 无地点 Beat → 兜底「未命名地点」scene（不污染已确认 Location）")
    text = "沈青崖拔剑追向黑影，破门冲入后院。"
    beats = [_beat("beat_01", 1, "action", scene="", segments=[text], summary="追")]
    plan = build_plan_from_beats(text, beats, [], title="t")
    _check("1 个 Scene", len(plan.scenes) == 1, str([s.scene_id for s in plan.scenes]))
    _check("scene_id = location_00", plan.scenes[0].scene_id == "location_00", plan.scenes[0].scene_id)
    _check("scene 名 = 未命名地点", plan.scenes[0].location_name == "未命名地点",
           plan.scenes[0].location_name)
    _check("覆盖链完整", _shot_flat(plan) == _core(text), _shot_flat(plan))


def test_no_mutate_input_beats() -> None:
    print("\n[19] build_plan_from_beats 不改入参 beats")
    text = "第一段。第二段。第三段。"
    beats = [_beat("beat_01", 1, "dialogue", scene="大堂", segments=["第一段。第二段。第三段。"], summary="对话")]
    before = [b.beat_id for b in beats]
    build_plan_from_beats(text, beats, [], title="t")
    _check("入参未改", [b.beat_id for b in beats] == before)
    _check("入参 text_segments 未被污染", beats[0].text_segments == ["第一段。第二段。第三段。"],
           str(beats[0].text_segments))


def main() -> None:
    test_blueprint_dialogue_default()
    test_blueprint_action()
    test_blueprint_transition_short()
    test_blueprint_plant_clue()
    test_feature_short_beat()
    test_feature_dialogue_no_speech()
    test_feature_prop_clue_insert()
    test_feature_location_change_establishing()
    test_feature_emotion_shift()
    test_shot_location_three_step()
    test_split_chunks_cover_original()
    test_coverage_chain_single_beat()
    test_coverage_chain_multi_beat()
    test_coverage_chain_duplicate_free()
    test_cross_location_same_scene_merge()
    test_no_word_by_word()
    test_build_plan_integration_fallback()
    test_fallback_unnamed_location()
    test_no_mutate_input_beats()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
