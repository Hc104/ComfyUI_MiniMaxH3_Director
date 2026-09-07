#!/usr/bin/env python3
"""#580 P1-B 台词提取缺口修复：小说正文引号对白 + 【】系统提示（方案 A）。

根因：P1-B 小说导入只认剧本格式「X说：」，不识别小说引号对白（“…”）与
【】系统提示 → plan.shot.dialogue 全空 → autoTtsForShot 短路 → 用户听到 H3
原声（只有环境音+配乐），台词一句都没有。

本测试覆盖（2026-08-16 用户拍板方案 A，场景取自《我靠吐槽在漫画里封神》）：
1. 引号对白提取：`“…”` + 说话人归属（引号后动作主语 / 拆分对白窗口 / 跨镜头继承）
2. 【】系统提示 → Dialogue(speaker=系统, type=system_voice)
3. 强调/引用引号排除（引用描述「…的“…”的…」不当作对白）
4. 候选名垃圾过滤（你知/那个 → 不进角色表）
5. quote-aware 句子切分（引号内句号/问号不切，对白不跨镜头）
6. _extract_dialogues 集成（剧本格式 + 引号 + 【】 + 说话人继承）
7. build_plan_from_beats 端到端：dialogue 非空 + 覆盖链完好

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_prose_dialogue.py
或 pytest：
    pytest director/tests/test_prose_dialogue.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import StoryBeat, VoiceType  # noqa: E402
from director.script_parser import (  # noqa: E402
    _collect_candidate_names,
    _extract_dialogues,
    _extract_dialogues_last,
    _extract_prose_dialogues,
    _is_valid_name_candidate,
)
from director.story_analyzer import (  # noqa: E402
    _split_sentences_quote_aware,
    build_plan_from_beats,
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


# ---------------- 真实场景样例（《我靠吐槽在漫画里封神》beat_02/beat_03） ----------------

SEG_QUOTE_1 = (
    "“不是，姐，”林晓对着屏幕里那个把钻石项链扔进垃圾桶、眼角带泪却咬着嘴唇一言不发的女主角敲下评论，"
    "“你倒是说句话啊！那是你男朋友送你的生日礼物，你看见它出现在你闺蜜脖子上，你有点正常人的反应行不行？"
    "哪怕上去薅她头发呢？憋着这口气你是要修仙飞升吗？”"
)

SEG_QUOTE_2 = (
    "“还有你，林薇薇——对，跟我一个姓我都嫌晦气——你笑什么笑？你以为你赢了吗？"
    "你知道原著里你活不过三章吗大姐！三章！你连下周的漫画更新都撑不到！"
    "你站那么高喝红酒，是方便一会儿霸总推你的时候摔得响亮点吗？”"
)

SEG_SYSTEM = (
    "【吐槽值+10。当前能量：10/1000。】\n紧接着，第二行字也浮现出来：\n"
    "【检测到宿主对当前情境吐槽欲望强烈，是否激活“吐槽成真”功能？Y/N】"
)

SEG_EMPHASIS = (
    "画面的角落，一个长发及腰、妆容精致的女人正端着红酒杯，露出一个标准的、"
    "让读者恨不得冲进去扇她两巴掌的“恶毒女配微笑”。"
)

SEG_QUOTE_REF = "弹幕里飘过一条写着“慕了”的评论。"


# ---------------- 1. 引号对白提取 ----------------

def test_quote_dialogue_extraction() -> None:
    print("\n[1] 引号对白提取（说话人归属）")
    ds, last = _extract_prose_dialogues(SEG_QUOTE_1, prev_speaker="", candidates=["林晓"])
    _check("2 条对白", len(ds) == 2, str([d.to_dict() for d in ds]))
    _check("第 1 条 speaker=林晓（引号后动作主语）",
           len(ds) >= 1 and ds[0].speaker == "林晓", str([d.to_dict() for d in ds]))
    _check("第 1 条 text=不是，姐，",
           len(ds) >= 1 and ds[0].text == "不是，姐，", str([d.to_dict() for d in ds]))
    _check("第 2 条 speaker=林晓（窗口分析/继承）",
           len(ds) >= 2 and ds[1].speaker == "林晓", str([d.to_dict() for d in ds]))
    _check("第 2 条 text 含完整台词",
           len(ds) >= 2 and "你倒是说句话啊" in ds[1].text, str([d.to_dict() for d in ds]))
    _check("type=character_dialogue",
           all(d.type == VoiceType.CHARACTER_DIALOGUE for d in ds))
    _check("末位说话人=林晓", last == "林晓", repr(last))


def test_quote_speaker_inherit() -> None:
    print("\n[1b] 无署名引号 → 跨镜头说话人继承")
    ds, last = _extract_prose_dialogues(SEG_QUOTE_2, prev_speaker="林晓", candidates=["林晓"])
    _check("1 条对白", len(ds) == 1, str([d.to_dict() for d in ds]))
    _check("继承 prev=林晓", ds[0].speaker == "林晓", str(ds[0].to_dict()))
    _check("完整台词保留", ds[0].text.startswith("还有你，林薇薇"), str(ds[0].text)[:40])
    _check("末位说话人=林晓", last == "林晓", repr(last))


# ---------------- 2. 【】系统提示 ----------------

def test_system_prompt_extraction() -> None:
    print("\n[2] 【】系统提示 → system_voice")
    ds, last = _extract_prose_dialogues(SEG_SYSTEM)
    _check("2 条系统提示", len(ds) == 2, str([d.to_dict() for d in ds]))
    _check("speaker=系统", all(d.speaker == "系统" for d in ds))
    _check("type=system_voice", all(d.type == VoiceType.SYSTEM_VOICE for d in ds))
    _check("正文为【】内文字（原子块，内层引号不再扫）",
           ds[0].text == "吐槽值+10。当前能量：10/1000。", str(ds[0].text))
    _check("系统不占说话人（last 不被系统污染）", last == "", repr(last))


# ---------------- 3. 强调/引用引号排除 ----------------

def test_emphasis_quote_excluded() -> None:
    print("\n[3] 强调/引用引号排除（非对白）")
    ds, _ = _extract_prose_dialogues(SEG_EMPHASIS)
    _check("强调名词「恶毒女配微笑」不进对白", len(ds) == 0, str([d.to_dict() for d in ds]))
    ds2, _ = _extract_prose_dialogues(SEG_QUOTE_REF)
    _check("引用描述「写着“慕了”的评论」不进对白（R0 的+名词）",
           len(ds2) == 0, str([d.to_dict() for d in ds2]))


# ---------------- 4. 候选名垃圾过滤 ----------------

def test_candidate_name_filter() -> None:
    print("\n[4] 候选名垃圾过滤")
    _check("你知非人名（末字动词）", not _is_valid_name_candidate("你知"))
    _check("那个非人名（停用词）", not _is_valid_name_candidate("那个"))
    _check("林晓是人名候选", _is_valid_name_candidate("林晓"))
    lines = [SEG_QUOTE_1, "你知不知道读者给你起的外号叫“抠门大王”。"]
    cands = _collect_candidate_names(lines)
    _check("林晓进候选（引号后动作主语）", "林晓" in cands, str(cands))
    _check("无垃圾候选", all(c not in cands for c in ("你知", "那个", "他", "这")), str(cands))


# ---------------- 5. quote-aware 切分 ----------------

def test_quote_aware_split() -> None:
    print("\n[5] quote-aware 句子切分（引号内标点不切）")
    text = "“不是，姐，”林晓敲下评论，“你倒是说句话啊！”\n“还有你，林薇薇——你笑什么笑？”"
    parts = _split_sentences_quote_aware(text)
    _check("引号内 ！？ 不当作切分点 → 1 段",
           len(parts) == 1, str(parts))
    _check("对白整体保留（不残引号）", parts[0] == text, str(parts[0])[:60])
    text2 = "林晓揉了揉眼睛。他继续刷评论。"
    parts2 = _split_sentences_quote_aware(text2)
    _check("普通句子正常切分", len(parts2) == 2, str(parts2))


# ---------------- 6. _extract_dialogues 集成 ----------------

def test_extract_dialogues_integration() -> None:
    print("\n[6] _extract_dialogues 集成（剧本格式 + 引号 + 【】 + 继承）")
    lines = [
        "林晓说：“女主你能有点反应吗？”",
        SEG_SYSTEM,
        "“还有你，林薇薇——你笑什么笑？”",
    ]
    ds = _extract_dialogues(lines, candidates=["林晓"], prev_speaker="")
    _check("4 条对白（剧本+系统×2+引号）", len(ds) == 4, str([d.to_dict() for d in ds]))
    _check("剧本格式 speaker=林晓", ds[0].speaker == "林晓", str(ds[0].to_dict()))
    _check("系统提示 type=system_voice",
           ds[1].speaker == "系统" and ds[1].type == VoiceType.SYSTEM_VOICE
           and ds[2].speaker == "系统" and ds[2].type == VoiceType.SYSTEM_VOICE,
           str([d.to_dict() for d in ds]))
    _check("无署名引号继承林晓（跳过系统说话人）", ds[3].speaker == "林晓", str(ds[3].to_dict()))


def test_dialogues_last() -> None:
    print("\n[6b] 末位说话人（跨镜头继承；系统不污染）")
    last = _extract_dialogues_last([SEG_QUOTE_1], prev_speaker="")
    _check("末位说话人=林晓", last == "林晓", repr(last))
    last2 = _extract_dialogues_last([SEG_SYSTEM], prev_speaker="")
    _check("纯系统块不改变说话人", last2 == "", repr(last2))
    last3 = _extract_dialogues_last([SEG_QUOTE_2], prev_speaker="林晓")
    _check("无署名引号继承 prev", last3 == "林晓", repr(last3))


# ---------------- 7. build_plan_from_beats 端到端 ----------------

def test_build_plan_from_beats_e2e() -> None:
    print("\n[7] build_plan_from_beats 端到端：dialogue 非空 + 覆盖链完好")
    beats = [
        StoryBeat(
            beat_id="beat_01",
            title="对漫画角色的激烈吐槽",
            summary="林晓对女主角与林薇薇的剧情激烈吐槽",
            dramatic_function="dialogue",
            scene_id="location_01",
            time="凌晨三点十七分",
            weather="深夜寂静",
            text_segments=[SEG_QUOTE_1, SEG_QUOTE_2],
            order=0,
        )
    ]
    plan = build_plan_from_beats("", beats, title="测试")
    all_dialogue = [d for s in plan.scenes for sh in s.shots for d in sh.dialogue]
    _check("有台词进 plan", len(all_dialogue) >= 2, str([d.to_dict() for d in all_dialogue]))
    _check("所有台词 speaker 非空", all(d.speaker for d in all_dialogue),
           str([d.to_dict() for d in all_dialogue]))
    _check("引号内问号不切镜（原文对白在同一镜内）",
           all(sh.dialogue for s in plan.scenes for sh in s.shots),
           "对白为空即引号被切开")
    _check("覆盖链无缺口", shot_coverage_check(plan) == [], str(shot_coverage_check(plan)))


def main() -> int:
    print("=" * 64)
    print("#580 小说正文台词提取（引号对白 + 【】系统提示）单元测试")
    print("=" * 64)
    test_quote_dialogue_extraction()
    test_quote_speaker_inherit()
    test_system_prompt_extraction()
    test_emphasis_quote_excluded()
    test_candidate_name_filter()
    test_quote_aware_split()
    test_extract_dialogues_integration()
    test_dialogues_last()
    test_build_plan_from_beats_e2e()
    print("\n" + "=" * 64)
    print(f"结果: {PASSED} PASS / {FAILED} FAIL")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
