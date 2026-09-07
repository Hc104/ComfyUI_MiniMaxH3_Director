#!/usr/bin/env python3
"""V1.7 Phase 1 · script_parser + production_plan 单元测试（Commit 1）。

覆盖（V17_PLAN §4 / Phase 1 独立验收的子集）：
1. production_plan Schema：to_dict/from_dict round-trip、validate（Scene 必有 Shot、
   重复 shot_id、duration 越界 warning、dialogue.speaker→characters 校验）；
2. script_parser 规则拆 Scene：无标题兜底 / markdown 标题 / 「第X场」中文序号 / slugline / 标签行；
3. script_parser 规则拆 Shot：显式镜头标记 / 时间变化 / 人物行为变化 / 对白跟随 / 默认合并；
4. 硬上限：超 MAX_SHOTS_PER_SCENE 合并 + warning；时长 clamp [2,8]；
5. 原始文本保留 source_text；对白 speaker（括号候选名 + 行首主语）；角色提取；
6. read_script 编码容错（UTF-8 / GB18030）。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_script_parser.py
或 pytest：
    pytest director/tests/test_script_parser.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import (  # noqa: E402
    MAX_SHOTS_PER_SCENE,
    MAX_SHOT_DURATION,
    MIN_SHOT_DURATION,
    Character,
    Dialogue,
    ProductionPlan,
    Scene,
    Shot,
    Validation,
)
from director.script_parser import (  # noqa: E402
    _extract_props_in_line,
    estimate_duration_sec,
    parse_script,
    parse_script_file,
    read_script,
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


# ---------------- 剧本样例 ----------------

SHAN_YU_MINI = """清晨，山雨客栈外细雨蒙蒙。
林雪（青衫女侠）推开客栈大门，收伞，环视堂内。
陈默（黑衣剑客）坐在角落，抬眼看向林雪，轻声说："你也来了。"
林雪点头，走到他对面坐下："雨停之前，都走不了。"
桌上那壶茶还冒着热气。
"""

SHAN_YU_SCENES = """# 山雨客栈

## 第一场：清晨 · 客栈大堂

地点：山雨客栈大堂
时间：清晨
天气：雨

清晨，山雨客栈外细雨蒙蒙。
林雪（青衫女侠）推开客栈大门，收伞，环视堂内。

## 第二场：午后 · 后院石阶

地点：后院石阶
时间：午后
天气：阴

柳如烟（白衣医女）蹲在石阶边，查看沈青崖（灰衣刀客）的伤口。
沈青崖皱眉："这点小伤，不碍事。"
柳如烟摇头："伤口已见骨，必须处理。"
"""

SHOT_MARKS = """## 场景一

镜头 1：林雪推门进入客栈。
镜头 2：陈默抬眼看向她。
"""

TIME_CHANGE = """## 场景一

清晨，山雨客栈外细雨蒙蒙。
午后，雨停了，林雪在院子里练剑。
"""

SLUGLINE = """内 客栈大堂 夜
林雪推门进来。
"""

MANY_SHOTS = "\n".join(f"镜头 {i}：林雪做了第 {i} 个动作。" for i in range(1, 11))


# ---------------- 1. production_plan Schema ----------------

def test_schema_roundtrip() -> None:
    print("\n[1] ProductionPlan to_dict / from_dict round-trip")
    plan = parse_script(SHAN_YU_MINI, source_file="山雨客栈.txt", title="山雨客栈")
    data = plan.to_dict()
    # P0 Story Timeline：顶层新增 timeline（播放顺序，缺省=场景树顺序，向后兼容）
    # P1-B 剧情结构层：顶层新增 beats（缺省空列表，老数据向后兼容，见 test_plan_beats.py）
    _check("顶层字段齐全", set(data.keys()) == {"project", "scenes", "validation", "timeline", "beats"})
    _check("缺省 timeline = 场景树顺序", data["timeline"] == [
        f"{s['scene_id']}:{sh['shot_id']}"
        for s in data["scenes"] for sh in s["shots"]
    ])
    _check("project 字段", data["project"] == {"title": "山雨客栈", "source_file": "山雨客栈.txt"})
    _check("刻意不放 castIds/generationMode/h3Prompt/camera/refs",
           not any(k in json.dumps(data, ensure_ascii=False) for k in
                   ("castIds", "locationId", "assetId", "generationMode", "h3Prompt", "refs")))
    plan2 = ProductionPlan.from_dict(data)
    _check("round-trip 后 to_dict 一致", plan2.to_dict() == data)
    _check("scene_id 命名 scene_01", plan2.scenes[0].scene_id == "scene_01")
    _check("shot_id 命名 shot_01", plan2.scenes[0].shots[0].shot_id == "shot_01")


def test_schema_validate() -> None:
    print("\n[2] validate：结构校验规则")
    # 合法：迷你剧本
    plan = parse_script(SHAN_YU_MINI)
    v = plan.validate()
    _check("迷你剧本 valid", v.status == "valid" and not v.errors, str(v))

    # 空剧本 → invalid
    empty = parse_script("   \n  ")
    _check("空剧本 invalid（无场景）", empty.validation.status == "invalid"
           and "没有" in "".join(empty.validation.errors), str(empty.validation.errors))

    # Scene 无 Shot → error
    bad = ProductionPlan(scenes=[Scene(scene_id="scene_01", shots=[])])
    bad_v = bad.validate()
    _check("Scene 无 Shot → error", bad_v.status == "invalid"
           and any("没有镜头" in e for e in bad_v.errors), str(bad_v.errors))

    # duration 越界 → warning
    w = ProductionPlan(scenes=[Scene(scene_id="scene_01", shots=[
        Shot(shot_id="shot_01", source_text="x", duration_sec=1)]),
        Scene(scene_id="scene_02", shots=[
            Shot(shot_id="shot_01", source_text="x", duration_sec=99)])])
    w_v = w.validate()
    _check("duration 越界 → warning", w_v.status == "valid"
           and len(w_v.warnings) == 2, str(w_v.warnings))

    # dialogue.speaker 不在 characters → warning
    d = ProductionPlan(scenes=[Scene(scene_id="scene_01", shots=[
        Shot(shot_id="shot_01", source_text="x",
             characters=[Character(name="林雪")],
             dialogue=[Dialogue(speaker="路人甲", text="谁？")])])])
    d_v = d.validate()
    _check("speaker 不在角色表 → warning",
           any("路人甲" in x and "角色表" in x for x in d_v.warnings), str(d_v.warnings))

    # 重复 shot_id → error
    dup = ProductionPlan(scenes=[Scene(scene_id="scene_01", shots=[
        Shot(shot_id="shot_01", source_text="a"),
        Shot(shot_id="shot_01", source_text="b")])])
    dup_v = dup.validate()
    _check("重复 shot_id → error", dup_v.status == "invalid"
           and any("重复 shot_id" in e for e in dup_v.errors), str(dup_v.errors))


# ---------------- 2. script_parser：Scene 切分 ----------------

def test_parse_no_title_scene() -> None:
    print("\n[3] 无标题剧本 → 兜底单场景")
    plan = parse_script(SHAN_YU_MINI, title="山雨客栈")
    _check("1 个场景", len(plan.scenes) == 1, str(len(plan.scenes)))
    scene = plan.scenes[0]
    _check("场景标题为（无标题场景）", scene.title == "（无标题场景）")
    _check("无标题场景仍生成 scene_id", scene.scene_id == "scene_01")
    _check("拆出 4 镜（环境/林雪/陈默/对坐+茶壶）", len(scene.shots) == 4, str(len(scene.shots)))


def test_parse_markdown_scenes() -> None:
    print("\n[4] markdown 标题 → 多场景 + 标签行填充")
    plan = parse_script(SHAN_YU_SCENES)
    _check("2 个场景", len(plan.scenes) == 2, str(len(plan.scenes)))
    s1, s2 = plan.scenes
    _check("scene1 标题", s1.title == "第一场：清晨 · 客栈大堂")
    _check("scene1 地点（标签行）", s1.location_name == "山雨客栈大堂")
    _check("scene1 时间（标签行）", s1.time == "清晨")
    _check("scene1 天气（标签行）", s1.weather == "雨")
    _check("scene1 拆 2 镜（环境 / 林雪推门）", len(s1.shots) == 2, str(len(s1.shots)))
    _check("scene2 标题", s2.title == "第二场：午后 · 后院石阶")
    _check("scene2 地点", s2.location_name == "后院石阶")
    _check("scene2 时间", s2.time == "午后")
    _check("scene2 拆 3 镜（柳如烟/沈青崖/柳如烟）", len(s2.shots) == 3, str(len(s2.shots)))


def test_parse_scene_index_and_slugline() -> None:
    print("\n[5] 「第X场」中文序号 + slugline")
    plan = parse_script("第一场：客栈大堂\n林雪推门进来。\n")
    _check("第X场切场景", len(plan.scenes) == 1 and plan.scenes[0].title == "客栈大堂",
           str(plan.scenes[0].title))

    plan2 = parse_script(SLUGLINE)
    s = plan2.scenes[0]
    _check("slugline 切场景", len(plan2.scenes) == 1)
    _check("slugline 地点提取", s.location_name == "客栈大堂", repr(s.location_name))
    _check("slugline 时间提取", s.time == "夜", repr(s.time))


# ---------------- 3. script_parser：Shot 规则拆分 ----------------

def test_parse_shot_marks() -> None:
    print("\n[6] 显式镜头标记硬切")
    plan = parse_script(SHOT_MARKS)
    scene = plan.scenes[0]
    _check("镜头标记拆 2 镜", len(scene.shots) == 2, str(len(scene.shots)))
    _check("镜头1 source_text 保留", "林雪推门进入客栈" in scene.shots[0].source_text)
    _check("镜头2 source_text 保留", "陈默抬眼看向她" in scene.shots[1].source_text)


def test_parse_time_change() -> None:
    print("\n[7] 时间变化拆镜")
    plan = parse_script(TIME_CHANGE)
    scene = plan.scenes[0]
    _check("时间变化拆 2 镜", len(scene.shots) == 2, str(len(scene.shots)))
    _check("清晨镜含环境", "细雨" in scene.shots[0].source_text)
    _check("午后镜含练剑", "练剑" in scene.shots[1].source_text)


def test_parse_overlimit_merge() -> None:
    print("\n[8] 超 MAX_SHOTS_PER_SCENE 合并 + warning")
    plan = parse_script(MANY_SHOTS)
    scene = plan.scenes[0]
    _check(f"10 个显式镜头合并到 {MAX_SHOTS_PER_SCENE}", len(scene.shots) == MAX_SHOTS_PER_SCENE,
           str(len(scene.shots)))
    _check("warning 记录合并", any("超过上限" in w for w in plan.validation.warnings),
           str(plan.validation.warnings))


def test_parse_default_merge() -> None:
    print("\n[9] 默认合并（无信号整段一镜）")
    plan = parse_script("山雨客栈的雨下了整整一夜。\n屋檐下滴答的水声没有停过。\n")
    scene = plan.scenes[0]
    _check("两行环境描写合并 1 镜", len(scene.shots) == 1, str(len(scene.shots)))
    _check("source_text 拼接保留", "雨下了整整一夜" in scene.shots[0].source_text
           and "滴答" in scene.shots[0].source_text)


# ---------------- 4. 时长 / 对白 / 角色 / 原文 ----------------

def test_duration_estimate() -> None:
    print("\n[10] duration 规则估算 + clamp")
    _check("短句默认 5s", estimate_duration_sec("好。") == 5)
    _check("动作密集 +1s", estimate_duration_sec("他推门走进来，收伞，环视四周。") == 6)
    long_text = "。".join(
        ["他推门走进来，收伞，环视四周，放下行李，坐在桌前，拿起茶杯，倒了一碗热茶，递给对面的人，转身又走回去，看了一眼窗外的雨"] * 3
    )
    _check("超长文本 clamp 到 MAX", estimate_duration_sec(long_text) == MAX_SHOT_DURATION,
           str(estimate_duration_sec(long_text)))
    _check("极短文本默认 5s（非 MIN）", estimate_duration_sec("。") == 5)


def test_dialogue_extraction() -> None:
    print("\n[11] 对白提取（括号候选名 + 行首主语）")
    plan = parse_script(SHAN_YU_MINI)
    shots = plan.scenes[0].shots
    # 第 3 镜：陈默
    d3 = shots[2].dialogue
    _check("陈默对白 1 条", len(d3) == 1, str([x.to_dict() for x in d3]))
    _check("陈默 speaker 归属", len(d3) == 1 and d3[0].speaker == "陈默", str([x.to_dict() for x in d3]))
    _check("陈默台词保留", len(d3) == 1 and "你也来了" in d3[0].text, str([x.to_dict() for x in d3]))
    # 第 4 镜：林雪
    d4 = shots[3].dialogue
    _check("林雪对白 1 条", len(d4) == 1 and d4[0].speaker == "林雪", str([x.to_dict() for x in d4]))


def test_character_extraction() -> None:
    print("\n[12] 角色提取（括号备注 + 对白说话者）")
    plan = parse_script(SHAN_YU_MINI)
    names = plan.scenes[0].character_names()
    _check("角色表含林雪/陈默", "林雪" in names and "陈默" in names, str(names))
    plan2 = parse_script(SHAN_YU_SCENES)
    names2 = plan2.scenes[1].character_names()
    _check("场景2 角色含柳如烟/沈青崖", "柳如烟" in names2 and "沈青崖" in names2, str(names2))


def test_source_text_preserved() -> None:
    print("\n[13] 原始文本逐字保留")
    plan = parse_script(SHAN_YU_MINI)
    joined = "\n".join(s.source_text for s in plan.scenes[0].shots)
    _check("拼接后等于原文（含标点）", joined == SHAN_YU_MINI.strip(), repr(joined))


def test_parse_file_and_encoding() -> None:
    print("\n[14] 文件读取 + 编码容错")
    with tempfile.TemporaryDirectory() as td:
        # UTF-8
        p_utf8 = os.path.join(td, "s.txt")
        with open(p_utf8, "w", encoding="utf-8") as f:
            f.write(SHAN_YU_MINI)
        plan = parse_script_file(p_utf8)
        _check("UTF-8 解析", len(plan.scenes) == 1 and plan.project.title == "s")
        # GB18030
        p_gbk = os.path.join(td, "g.txt")
        with open(p_gbk, "w", encoding="gb18030") as f:
            f.write(SHAN_YU_MINI)
        _check("GB18030 read_script", read_script(p_gbk) == SHAN_YU_MINI)
        # 缺文件
        try:
            read_script(os.path.join(td, "missing.txt"))
            _check("缺文件抛异常", False)
        except FileNotFoundError:
            _check("缺文件抛异常", True)


def test_visual_intent_empty() -> None:
    print("\n[15] Phase 1 不产出 visual_intent / actions / emotion（props 由 P0-2 规则兜底）")
    plan = parse_script(SHAN_YU_MINI)
    shot = plan.scenes[0].shots[1]
    _check("visual_intent 空", shot.visual_intent == "")
    _check("actions 空", shot.actions == [])
    _check("emotion 空", shot.emotion == "")
    # P0-3b：单字道具放宽后，「收伞」应提取出「伞」（纯名词道具），不再是空。
    _check("shots[1] 收伞 → 道具伞", [p.name for p in shot.props] == ["伞"],
           str([p.name for p in shot.props]))
    _check("shots[2] 无动作宾语道具", plan.scenes[0].shots[2].props == [])


def test_p02_dual_track_backstop() -> None:
    print("\n[16] P0-2 双轨制：动作主语角色（蒙面人）规则兜底 + 动作宾语道具")
    text = """## 第一场：山雨楼外

地点：山雨楼外

镜头 1：沈青崖推门走进大堂，背着旧剑匣从山道走来。
镜头 2：柳如烟放下茶碗。
镜头 3：沈青崖落座，环视堂内。
镜头 4：蒙面人一步步走近。
镜头 5：蒙面人拔刀，柳如烟退后一步。
镜头 6：暖黄灯火扑面而来，衣袂带风走进门。
"""
    plan = parse_script(text)
    scene = plan.scenes[0]
    cands = [c.name for sh in scene.shots for c in sh.characters]
    for nm in ("沈青崖", "柳如烟", "蒙面人"):
        _check(f"{nm} 进角色（规则兜底，即使无对白无括号）", nm in cands, str(cands))
    props = [p.name for sh in scene.shots for p in sh.props]
    _check("旧剑匣进 props（贪婪吞字后仍提取：背着旧剑匣从山道）",
           "旧剑匣" in props, str(props))
    _check("茶碗进 props", "茶碗" in props, str(props))
    _check("镜头标记不进角色", all(n not in cands for n in ("镜头一", "镜头1")), str(cands))
    _check("环境名词不进角色（暖黄灯火/衣袂带风：后缀+频次过滤）",
           all(n not in cands for n in ("暖黄灯火", "衣袂带风", "大堂")), str(cands))


def test_p03b_action_phrase_props() -> None:
    """P0-3b（用户 2026-08-11 拍板）：动作/空间短语不得进入实体名（纯名词规则）。

    用户给出的 5 个回归用例 + 单字道具放宽（持灯/端茶）：
      持灯退到柜台边→灯，端茶走到桌边→茶，拔刀扑来→刀，
      背着旧剑匣走来→旧剑匣，拿起茶碗放在桌上→茶碗。
    """
    print("\n[17] P0-3b 动作短语→实体名粘连修复（纯名词规则，无黑名单）")
    cases = [
        ("柳如烟持灯退到柜台边", ["灯"]),
        ("沈青崖端茶走到桌边", ["茶"]),
        ("蒙面人拔刀扑来", ["刀"]),
        ("沈青崖背着旧剑匣走来", ["旧剑匣"]),
        ("柳如烟拿起茶碗放在桌上", ["茶碗"]),
    ]
    for text, want in cases:
        got = _extract_props_in_line(text)
        _check(f"props({text!r})={want}", got == want, str(got))
    # 非道具宾语不被误提取（正向规则：动词表+后缀表都命中才返回）
    _check("放牛不是道具宾语", _extract_props_in_line("他放牛") == [])
    _check("扫地不是道具宾语", _extract_props_in_line("她扫地") == [])
    _check("说笑不是道具宾语", _extract_props_in_line("两人说笑") == [])


def main() -> int:
    print("=" * 64)
    print("V1.7 Phase 1 · Commit 1 单元测试（script_parser + production_plan）")
    print("=" * 64)
    test_schema_roundtrip()
    test_schema_validate()
    test_parse_no_title_scene()
    test_parse_markdown_scenes()
    test_parse_scene_index_and_slugline()
    test_parse_shot_marks()
    test_parse_time_change()
    test_parse_overlimit_merge()
    test_parse_default_merge()
    test_duration_estimate()
    test_dialogue_extraction()
    test_character_extraction()
    test_source_text_preserved()
    test_parse_file_and_encoding()
    test_visual_intent_empty()
    test_p02_dual_track_backstop()
    test_p03b_action_phrase_props()
    print("\n" + "=" * 64)
    print(f"结果: {PASSED} PASS / {FAILED} FAIL")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
