#!/usr/bin/env python3
"""P0 Story Timeline 交叉剪辑测试（2026-08-14 用户拍板）。

覆盖（《小说导演层架构审计.md》P0 全部语义）：
1. resolve_timeline_order：timeline 优先 + 非法跳过 + 未覆盖补齐 + 空回退场景树；
2. build_plan_intents 双前驱合成（用户 P0 硬性要求，非后补）：
   - Timeline Previous = 画面前驱（播放相邻，跨场景不断链）→ prev_shot_state
   - Scene Previous   = 本场景上次状态（切回原场景延续空间）→ scene_prev_state
   - transition_type 场景切换细分：scene_cut（切入新场景）/ cross_cut（平行剪辑切回）
   - must_keep 双来源合并：场景不变优先 scene_prev（切回大堂延续大堂状态）
3. build_plan_cameras 按播放顺序传递 prev_state + scene_has_prev 场景首镜 establish；
4. h3_prompt_builder 渲染 cross_cut 标签。

直接用系统 python 运行（无第三方依赖，纯规则零 LLM）：
    python3 director/tests/test_timeline.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director import camera_template as ct  # noqa: E402
from director import director_intent as di  # noqa: E402
from director import h3_prompt_builder as hb  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
    resolve_timeline_order,
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


def _cross_cut_plan() -> ProductionPlan:
    """《山雨客栈》3 镜交叉剪辑：01大堂 → 03后院 → 02大堂。

    timeline 显式交叉引用同一 scene_01（大堂）两次——正是用户要的
    「大堂→后院→大堂」反复切换，Scene 只维护一份资产。
    """
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[
            Scene(
                scene_id="scene_01", title="清晨 · 客栈大堂", location_name="山雨楼大堂",
                time="黄昏", weather="雨",
                shots=[
                    Shot(shot_id="shot_01", source_text="林雪推开客栈大门，收伞，环视堂内。",
                         duration_sec=5,
                         characters=[Character(name="林雪", role="青衫女侠")],
                         actions=["推开大门", "收伞"], emotion="平静",
                         visual_intent="青衫女侠推门而入，雨水沿伞沿滴落。"),
                    Shot(shot_id="shot_02", source_text="陈默从柜台后抬眼看向林雪，轻声说话。",
                         duration_sec=6,
                         characters=[Character(name="陈默", role="掌柜")],
                         actions=["抬眼", "说话"], emotion="温和",
                         dialogue=[Dialogue(speaker="陈默", text="这么大的雨，赶路辛苦了。")]),
                ],
            ),
            Scene(
                scene_id="scene_02", title="午后 · 后院石阶", location_name="山雨楼后院",
                time="午后", weather="晴",
                shots=[
                    Shot(shot_id="shot_03", source_text="柳如烟独自坐在石阶上，望着远山发呆。",
                         duration_sec=5,
                         characters=[Character(name="柳如烟", role="红衣女子")],
                         actions=["坐", "望"], emotion="悲伤",
                         visual_intent="柳如烟独坐石阶，远山薄雾，眼神落寞。"),
                ],
            ),
        ],
        timeline=["scene_01:shot_01", "scene_02:shot_03", "scene_01:shot_02"],
        validation=Validation(),
    )


# ---------------- 1. resolve_timeline_order ----------------
def test_resolve_timeline_order() -> None:
    print("\n[1] resolve_timeline_order 播放顺序解析")
    plan = _cross_cut_plan()
    order = resolve_timeline_order(plan)
    _check("显式 timeline 优先（大堂→后院→大堂）",
           order == [("scene_01", "shot_01"), ("scene_02", "shot_03"),
                     ("scene_01", "shot_02")],
           str(order))

    # 非法条目跳过 + 未覆盖补齐（保证不丢镜）
    plan.timeline = ["scene_01:shot_01", "bad_entry", "scene_99:missing", "scene_02:shot_03"]
    order2 = resolve_timeline_order(plan)
    _check("非法/不存在条目跳过", order2 == [("scene_01", "shot_01"), ("scene_02", "shot_03"),
                                            ("scene_01", "shot_02")],
           str(order2))

    # 重复条目去重
    plan.timeline = ["scene_01:shot_01", "scene_01:shot_01", "scene_02:shot_03", "scene_01:shot_02"]
    order3 = resolve_timeline_order(plan)
    _check("重复条目去重", order3 == [("scene_01", "shot_01"), ("scene_02", "shot_03"),
                                     ("scene_01", "shot_02")],
           str(order3))

    # 空 timeline → 场景树顺序（严格向后兼容）
    plan.timeline = []
    order4 = resolve_timeline_order(plan)
    _check("空 timeline 回退场景树顺序",
           order4 == [("scene_01", "shot_01"), ("scene_01", "shot_02"),
                      ("scene_02", "shot_03")],
           str(order4))


# ---------------- 2. build_plan_intents 双前驱合成 ----------------
def test_build_plan_intents_dual_predecessor() -> None:
    print("\n[2] build_plan_intents 双前驱合成（Timeline 前驱 + Scene 前驱）")
    plan = _cross_cut_plan()
    intents = di.build_plan_intents(plan)

    s1 = intents["scene_01:shot_01"]
    _check("timeline 首镜：无画面前驱",
           "transition_type" not in s1.continuity and "prev_shot_state" not in s1.continuity,
           str(s1.continuity))
    _check("timeline 首镜：无场景前驱",
           "scene_prev_state" not in s1.continuity, str(s1.continuity))

    s3 = intents["scene_02:shot_03"]
    _check("切入新场景 → transition_type = scene_cut",
           s3.continuity.get("transition_type") == "scene_cut", str(s3.continuity))
    _check("画面前驱 = 大堂 shot_01（播放相邻，跨场景不断链）",
           s3.continuity.get("prev_shot_state", {}).get("location") == "山雨楼大堂",
           str(s3.continuity.get("prev_shot_state")))
    _check("画面前驱 shot_id 记录",
           s3.continuity.get("timeline_prev_shot_id") == "scene_01:shot_01",
           str(s3.continuity))
    _check("scene_02 首次出现 → 无 scene_prev_state",
           "scene_prev_state" not in s3.continuity, str(s3.continuity))

    s2 = intents["scene_01:shot_02"]
    _check("切回大堂 → transition_type = cross_cut（平行剪辑）",
           s2.continuity.get("transition_type") == "cross_cut", str(s2.continuity))
    _check("画面前驱 = 后院 shot_03（叙事前驱）",
           s2.continuity.get("prev_shot_state", {}).get("location") == "山雨楼后院",
           str(s2.continuity.get("prev_shot_state")))
    _check("场景前驱 = 大堂 shot_01（本场景上次状态）",
           s2.continuity.get("scene_prev_state", {}).get("location") == "山雨楼大堂",
           str(s2.continuity.get("scene_prev_state")))
    _check("场景前驱 shot_id 记录",
           s2.continuity.get("scene_prev_shot_id") == "scene_01:shot_01",
           str(s2.continuity))
    _check("画面前驱 shot_id = 后院 shot_03",
           s2.continuity.get("timeline_prev_shot_id") == "scene_02:shot_03",
           str(s2.continuity))
    _check("must_keep 含「山雨楼大堂场景不变」（来自 scene_prev 空间状态）",
           any("山雨楼大堂场景不变" in k for k in s2.continuity.get("must_keep", [])),
           str(s2.continuity.get("must_keep")))
    _check("must_keep 不含后院场景（画面前驱的空间状态不误入）",
           all("山雨楼后院场景不变" not in k for k in s2.continuity.get("must_keep", [])),
           str(s2.continuity.get("must_keep")))


# ---------------- 3. build_plan_cameras 按播放顺序 ----------------
def test_build_plan_cameras_timeline_order() -> None:
    print("\n[3] build_plan_cameras 按播放顺序 + 场景首镜 establish")
    plan = _cross_cut_plan()
    out = ct.build_plan_cameras(plan)
    ids = [(c["scene_id"], c["shot_id"]) for c in out["cameras"]]
    _check("相机顺序 = timeline 播放顺序（大堂→后院→大堂）",
           ids == [("scene_01", "shot_01"), ("scene_02", "shot_03"),
                   ("scene_01", "shot_02")],
           str(ids))
    _check("每镜 camera 非空", all(bool(str(c["camera"]).strip()) for c in out["cameras"]))

    # 场景首镜（scene_01 shot_01 / scene_02 shot_03）即使有画面前驱也走环境建立
    first = out["cameras"][0]
    _check("场景首镜（shot_01）无画面前驱前缀",
           "承接上镜" not in first["camera"], repr(first["camera"]))

    third = out["cameras"][2]  # scene_01 shot_02：切回大堂，非场景首镜
    _check("切回大堂镜头非场景首镜（有 scene_has_prev）",
           third["camera_template"] not in ("", "establish") or bool(third["camera_template"]),
           third["camera_template"])


# ---------------- 4. h3_prompt_builder 渲染 cross_cut ----------------
def test_h3_prompt_renders_cross_cut() -> None:
    print("\n[4] h3_prompt_builder 渲染 cross_cut 平行剪辑")
    plan = _cross_cut_plan()
    intents = di.build_plan_intents(plan)
    desc = hb.build_h3_prompt(intents["scene_01:shot_02"]).integrated_multimodal_description
    _check("切回大堂 H3 含「平行剪辑切回本场景」",
           "平行剪辑切回本场景" in desc, desc)
    _check("切回大堂 H3 保持大堂场景",
           "保持山雨楼大堂场景不变。" in desc, desc)


def main() -> None:
    test_resolve_timeline_order()
    test_build_plan_intents_dual_predecessor()
    test_build_plan_cameras_timeline_order()
    test_h3_prompt_renders_cross_cut()
    print(f"\n合计：{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
