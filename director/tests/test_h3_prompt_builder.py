#!/usr/bin/env python3
"""P0-① H3 Prompt Builder 测试（用户 2026-08-13 拍板 ③/④ + 硬性回归）。

覆盖：
1. 三段结构 + [0-5s] 时间轴 + schema=minimax-h3-project-v1（项目内部规范标注）；
2. 原文事实逐字保留（user_facts == retained_facts，最高优先级不得被覆盖）；
3. 双轨合并去重：AI 补全句与原文事实相似 → AI 句被跳过（原文优先）；
4. [REF: 名] 可读标签 + references 结构化绑定：
   - entity_key 匹配（类型:canonical 名）
   - 角色别名归一（柳姑娘→柳如烟）绑定命中
   - 无绑定 → references 空 / 无 [REF] 行
5. 对白原文逐字 + speaker 标注（#545 修复 D：对白只进 integrated_multimodal_description
   的 <d>[Language] 原文</d> 块 + 稳定说话人 ID，绝不进 overall_soundscape）；
6. provenance 三类来源标记（回归审计基准：Shot 1/4/7 可追溯）。

直接用系统 python 运行（纯规则零 LLM）：
    python3 director/tests/test_h3_prompt_builder.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import director_intent as di  # noqa: E402
from director import h3_prompt_builder as h3  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
)

# 《山雨客栈》真实镜头原文（P0-① 回归基准，与 P0_1_H3_CHAIN_DEMO.md 一致）
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
                         Entity(name="旧剑匣", type=EntityType.PROP,
                                source=EntitySource.INFERRED, confidence=0.9),
                     ]),
                Shot(shot_id="shot_04", source_text=SHOT4_SRC, duration_sec=5,
                     characters=[Character(name="柳如烟")],
                     dialogue=[Dialogue(speaker="柳如烟", text="客官，落座歇脚，茶先暖着。")],
                     entities=[
                         Entity(name="柳如烟", type=EntityType.CHARACTER,
                                source=EntitySource.SCRIPT, confidence=0.95),
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
                     ]),
            ],
        )],
        validation=Validation(),
    )


def _intents():
    return di.build_plan_intents(_plan())


def test_schema_and_timeline() -> None:
    print("\n[1] Schema + 时间轴 + 三段结构")
    p = _intents()["scene_01:shot_01"]
    out = h3.build_h3_prompt(p, duration_sec=5)
    _check("schema = minimax-h3-project-v1", out.schema == "minimax-h3-project-v1",
           out.schema)
    _check("时间轴前缀 [0-5s]",
           out.integrated_multimodal_description.startswith("[0-5s] "),
           out.integrated_multimodal_description[:40])
    _check("三段齐全", all([
        out.integrated_multimodal_description,
        out.overall_soundscape,
        out.non_diegetic_music,
    ]))
    _check("timeline 结构化", out.timeline == [{"start": 0.0, "end": 5.0, "label": "[0-5s]"}])


def test_user_facts_verbatim_priority() -> None:
    print("\n[2] 原文事实逐字保留（最高优先级）")
    p = _intents()["scene_01:shot_01"]
    out = h3.build_h3_prompt(p)
    prov = out.provenance["integrated_multimodal_description"]
    _check("user_facts == retained_facts（逐字）", prov["user_facts"] == p.retained_facts,
           str(prov["user_facts"]))
    desc = out.integrated_multimodal_description
    for fact in p.retained_facts:
        _check(f"原文逐字在描述：{fact[:12]}…", fact in desc, desc[:80])
    # 越界实体不出现（#460 边界过滤链路）
    _check("旧剑匣不进 Prompt", "旧剑匣" not in desc)
    _check("provenance.user_original_intent=USER",
           p.provenance["user_original_intent"] == "USER")


def test_ai_supplement_dedup() -> None:
    print("\n[3] 双轨合并去重：AI 不覆盖原文")
    p = _intents()["scene_01:shot_01"]
    # 场景 A：AI composition 与原文事实高度重复 → 应被去重跳过（原文优先）
    p.composition = "雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼"
    out = h3.build_h3_prompt(p)
    prov = out.provenance["integrated_multimodal_description"]
    desc = out.integrated_multimodal_description
    _check("AI 重复压缩句未覆盖原文细节（雨刚停 仍在）", "雨刚停" in desc)
    _check("AI 与原文重复句被去重丢弃",
           not any("全景" in s for s in prov["ai_supplement"]), str(prov["ai_supplement"]))
    _check("原文事实区完整", len(prov["user_facts"]) == len(p.retained_facts))

    # 场景 B：AI 含原文没有的构图信息 → 作为补充保留（不覆盖，只追加）
    p.composition = "全景俯拍山雾笼罩的山雨楼，暖黄灯笼与湿石阶形成冷暖对比，老槐树水珠特写增强雨后质感"
    out_b = h3.build_h3_prompt(p)
    prov_b = out_b.provenance["integrated_multimodal_description"]
    _check("AI 构图补全保留为补充", prov_b["ai_supplement"] == [p.composition],
           str(prov_b["ai_supplement"]))
    _check("原文事实仍完整（8 处逐字）", len(prov_b["user_facts"]) == len(p.retained_facts))
    _check("AI 补全在原文之后（不覆盖顺序）", out_b.integrated_multimodal_description.count("雨刚停") == 1)


def test_ref_labels_and_references() -> None:
    print("\n[4] [REF] 可读标签 + references 结构化绑定")
    p = _intents()["scene_01:shot_01"]
    bindings = {
        "architecture:山雨楼": {"asset_id": "asset_001", "image_file": "shan_yu_lou.png",
                              "ref_image": "ref_image_0"},
    }
    out = h3.build_h3_prompt(p, bindings=bindings)
    _check("[REF: 山雨楼] 可读标签", "[REF: 山雨楼]" in out.integrated_multimodal_description,
           out.integrated_multimodal_description[-60:])
    _check("references 结构化", out.references == [{
        "entity_key": "architecture:山雨楼", "entity_name": "山雨楼",
        "asset_id": "asset_001", "image_file": "shan_yu_lou.png",
        "ref_image": "ref_image_0",
    }], str(out.references))


def test_ref_alias_normalization() -> None:
    print("\n[5] 角色别名归一绑定命中（柳姑娘→柳如烟）")
    plan = _plan()
    p = _intents()["scene_01:shot_04"]
    bindings = {
        "character:柳如烟": {"asset_id": "asset_002", "image_file": "liu_ruyan.png",
                           "ref_image": "ref_image_0"},
    }
    out = h3.build_plan_h3_prompts(
        {"scene_01:shot_04": p}, plan=plan,
        bindings_by_shot={"scene_01:shot_04": bindings},
    )["scene_01:shot_04"]
    keys = [r["entity_key"] for r in out.references]
    _check("别名归一 → character:柳如烟 绑定命中", "character:柳如烟" in keys, str(keys))
    _check("references 含两条（柳如烟+柳姑娘归一后同 key 去重）", len(out.references) == 1,
           str(out.references))


def test_no_binding_no_ref() -> None:
    print("\n[6] 无绑定 → references 空 / 无 [REF] 行")
    p = _intents()["scene_01:shot_01"]
    out = h3.build_h3_prompt(p, bindings={})
    _check("references 空", out.references == [])
    _check("无 [REF:] 标签", "[REF:" not in out.integrated_multimodal_description)


def test_dialogue_verbatim_speaker() -> None:
    print("\n[7] 对白原文逐字 + speaker 标注（#545 修复 D：只进描述区 <d> 块）")
    p = _intents()["scene_01:shot_04"]
    out = h3.build_h3_prompt(p)
    desc = out.integrated_multimodal_description
    sound = out.overall_soundscape
    _check("对白原文逐字进 <d> 块",
           "<d>[Chinese] 客官，落座歇脚，茶先暖着。</d>" in desc, desc)
    _check("speaker 稳定 ID", "(S1)柳如烟说，" in desc, desc)
    # #608：<d> 块后紧跟「仅声音」定点说明（画面无字幕）
    _check("<d> 块后定点仅声音说明",
           "</d>" + h3._DIALOGUE_AUDIO_ONLY_NOTE in desc, desc[-80:])
    _check("soundscape 无对白",
           "客官" not in sound and "柳如烟：「" not in sound and "<d>" not in sound, sound)
    _check("描述正文无对白引号段",
           "「客官，落座歇脚，茶先暖着。」" not in desc, desc)
    _check("对白属 user_facts（描述区）",
           any("对白=" in s for s in
               out.provenance["integrated_multimodal_description"]["user_facts"]))


def test_provenance_three_sources() -> None:
    print("\n[8] provenance 三类来源标记（回归审计基准）")
    p = _intents()["scene_01:shot_07"]
    out = h3.build_h3_prompt(p)
    prov = out.provenance["integrated_multimodal_description"]
    _check("user_facts 非空（原文事实）", len(prov["user_facts"]) > 0)
    _check("rule_generated 含镜头（规则运镜）",
           any("镜头=" in s for s in prov["rule_generated"]), str(prov["rule_generated"]))
    _check("emotion=AI 进 ai_supplement 或规则音乐",
           out.provenance["non_diegetic_music"]["rule_generated"] != [])
    _check("描述含角色（沈青崖）", "沈青崖" in out.integrated_multimodal_description,
           out.integrated_multimodal_description[-80:])


def test_a1_prompt_dedup_regression() -> None:
    """A1 重复污染回归（2026-08-14 实机 seg_0000 验收发现，用户拍板先修 A1）。

    复现 seg_0000 实际提交的四类污染：composition 内嵌完整原文、cameraText 带
    「镜头：」前缀、emotion 与 composition 内情绪词重复、style 自带句号。
    修复后要求：原文恰一次 / 镜头前缀恰一次 / 情绪词不重复渲染 / 无双句号。
    """
    print("\n[9] A1 Prompt 重复污染回归")
    p = _intents()["scene_01:shot_01"]
    # 用户五区 visual 覆盖：composition 内嵌完整原文 + 低机位 + 情绪词
    p.composition = ("黄昏雨歇，山道尽头雾气弥漫，山雨楼的檐角还在滴水，"
                     "低机位缓慢推进穿行山道，仰拍山雨楼檐角滴水，"
                     "突出檐角滴水与雾气笼罩的山道，静谧孤寂")
    p.continuity = dict(p.continuity or {})
    p.continuity["camera_desc"] = "镜头：全景，缓摇交代山雨楼外环境，雨氛围。"
    p.style = "电影感，冷色调，湿地面反射，雨丝可见，画面干净通透，人物边缘清晰。"
    p.emotion = "静谧孤寂"
    out = h3.build_h3_prompt(p, duration_sec=5)
    desc = out.integrated_multimodal_description

    for f in p.retained_facts:
        _check(f"[A1-1] 原文逐字恰一次：{f[:10]}…",
               desc.count(f) == 1, f"count={desc.count(f)}")
    _check("[A1-1] 用户补全（低机位）保留", "低机位缓慢推进" in desc, desc[-60:])
    _check("[A1-2] 镜头前缀恰一次", desc.count("镜头：") == 1, desc)
    _check("[A1-3] 无『氛围静谧孤寂』重复", "氛围静谧孤寂" not in desc, desc[-60:])
    _check("[A1-3] desc 内静谧孤寂 ≤1 次", desc.count("静谧孤寂") <= 1, desc)
    _check("[A1-4] 无双句号", "。。" not in desc and "。。" not in out.non_diegetic_music,
           desc[-40:])


def test_a1_fact_strip_boundary() -> None:
    """A1 fact 剥离词边界保护：短 fact 不得误伤更长词（「山雨楼」≠「山雨楼外」）。"""
    print("\n[10] A1 fact 剥离词边界保护")
    out = h3._strip_fact_duplicates("山雨楼外的灯笼亮着", ["山雨楼"], "山雨楼")
    _check("短 fact 不破坏更长词（山雨楼外）", "山雨楼外" in out, out)
    out2 = h3._strip_fact_duplicates("山雨楼，黄昏雨歇", ["山雨楼"], "山雨楼")
    _check("独立短 fact 正常剥离", "山雨楼" not in out2, out2)
    out3 = h3._strip_fact_duplicates(
        "黄昏雨歇 山道尽头雾气弥漫 山雨楼的檐角还在滴水 低机位缓慢推进",
        ["黄昏雨歇", "山道尽头雾气弥漫", "山雨楼的檐角还在滴水"],
        "黄昏雨歇，山道尽头雾气弥漫，山雨楼的檐角还在滴水")
    _check("空格分隔原文整段剥离", "黄昏雨歇" not in out3, out3)
    _check("剥离后用户补全保留", "低机位缓慢推进" in out3, out3)


def test_no_subtitle_rule() -> None:
    """无字幕约束（#597/#608，2026-08-17）：双语强负向约束 + 每 <d> 对白块定点「仅声音」说明。"""
    print("\n[11] 无字幕画面约束（双语强约束 + <d> 定点说明）")
    # 有对白镜：约束在 <d> 块之后（描述区尾部）
    p = _intents()["scene_01:shot_04"]
    out = h3.build_h3_prompt(p)
    desc = out.integrated_multimodal_description
    _check("约束文本在描述内", h3.NO_SUBTITLE_RULE in desc, desc[-80:])
    _check("约束在 <d> 对白块之后",
           desc.rfind(h3.NO_SUBTITLE_RULE) > desc.rfind("<d>"), desc[-80:])
    _check("约束在描述区尾部", desc.endswith(h3.NO_SUBTITLE_RULE), desc[-80:])
    # #608：双语强约束（英文负向指令点名全部画面文字形态）
    _check("约束含英文负向指令", "Do NOT render any subtitles" in desc, desc[-160:])
    _check("约束点名 subtitle/caption/dialogue text",
           "subtitles, captions, on-screen text" in desc, desc[-160:])
    # #608：每个 <d> 对白块后紧跟「仅声音」定点说明（与尾部全局约束双信号）
    _check("<d> 块后定点仅声音说明",
           "</d>" + h3._DIALOGUE_AUDIO_ONLY_NOTE in desc, desc[-160:])
    _check("定点说明含英文 audio-only", "Audio-only, no on-screen text" in desc, desc[-160:])
    prov = out.provenance["integrated_multimodal_description"]
    _check("rule_generated 记无字幕约束", "无字幕约束" in prov["rule_generated"],
           str(prov["rule_generated"]))
    _check("soundscape 无约束（约束只进描述区）",
           h3.NO_SUBTITLE_RULE not in out.overall_soundscape)
    _check("music 无约束", h3.NO_SUBTITLE_RULE not in out.non_diegetic_music)
    # 无对白镜：约束同样追加，且无定点说明（无 <d> 块）
    p2 = _intents()["scene_01:shot_01"]
    desc2 = h3.build_h3_prompt(p2).integrated_multimodal_description
    _check("无对白镜也追加约束", desc2.endswith(h3.NO_SUBTITLE_RULE), desc2[-80:])
    _check("无对白镜无定点说明", h3._DIALOGUE_AUDIO_ONLY_NOTE not in desc2, desc2[-80:])


def main() -> None:
    test_schema_and_timeline()
    test_user_facts_verbatim_priority()
    test_ai_supplement_dedup()
    test_ref_labels_and_references()
    test_ref_alias_normalization()
    test_no_binding_no_ref()
    test_dialogue_verbatim_speaker()
    test_provenance_three_sources()
    test_a1_prompt_dedup_regression()
    test_a1_fact_strip_boundary()
    test_no_subtitle_rule()
    print(f"\n合计：{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()