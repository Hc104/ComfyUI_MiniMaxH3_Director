#!/usr/bin/env python3
"""V1.7 Phase 5（P1-D）：camera_template 单元测试（纯规则，mock，不碰网络/GPU）。

覆盖（V17_PLAN §7 / Phase 5 独立验收）：
1. 模板库：list_camera_templates 15 条（10 基础 + 5 补充），含 id/name/intent/template；
2. pick_camera 意图命中：
   - 首镜无角色 → establish；对白 → dialogue；动作行走 → walk(tracking)；
   - 追逐 → chase(handheld)；战斗 → combat；特写 → emotion_close/see_object；
   - 强情绪 → emotion_close；尾镜 → ending；无特征 → default；
3. 跨镜状态机 previousCameraState：
   - 场景内方向延续（walk → 下镜 motion 前缀「承接上镜右向运动」）；
   - 上镜过肩 → 下镜对话前缀「承接上镜视线，反打对切」；
   - 上镜已推近特写 → 本镜又推近 → 「保持近景，微推」；
   - 场景切换重置：新场景首镜 prev_state 清空 → 无延续前缀；
4. build_plan_cameras 全链路：逐场景 prev_state 传递 + 切换重置 + 顺序对齐；
5. ⛔ 纯规则零显存：模块无 unload/gpu_models_loaded/keep_alive/Ollama/torch 引用。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_camera_template.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.camera_template as ct  # noqa: E402
from director.production_plan import ProductionPlan  # noqa: E402

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


def _assert_in(name: str, needle: str, haystack: str) -> None:
    _check(f"{name} 包含「{needle}」", needle in haystack, f"实际={haystack!r}")


# ---- 迷你《山雨客栈》ProductionPlan（to_dict 格式，与前端脚本导入一致） ----
PLAN = {
    "project": {"title": "山雨客栈", "source_file": ""},
    "scenes": [
        {
            "scene_id": "scene_01",
            "title": "清晨 · 客栈大堂",
            "location_name": "山雨客栈大堂",
            "time": "清晨",
            "weather": "雨",
            "shots": [
                {
                    "shot_id": "shot_01",
                    "source_text": "林雪推开客栈大门，收伞，环视堂内。",
                    "duration_sec": 5,
                    "characters": [{"name": "林雪", "role": "青衫女侠"}],
                    "props": [{"name": "油纸伞"}],
                    "actions": ["推开大门", "收伞"],
                    "emotion": "平静",
                    "dialogue": [],
                    "visual_intent": "青衫女侠推开木门，雨水顺着伞沿滴落。",
                },
                {
                    "shot_id": "shot_02",
                    "source_text": "陈默从柜台后抬眼看向林雪，轻声说话。",
                    "duration_sec": 6,
                    "characters": [{"name": "林雪", "role": "青衫女侠"}, {"name": "陈默", "role": "掌柜"}],
                    "props": [],
                    "actions": ["抬眼", "说话"],
                    "emotion": "温和",
                    "dialogue": [{"speaker": "陈默", "text": "这么大的雨，赶路辛苦了。"}],
                    "visual_intent": "",
                },
            ],
        },
        {
            "scene_id": "scene_02",
            "title": "午后 · 后院石阶",
            "location_name": "山雨客栈后院",
            "time": "午后",
            "weather": "晴",
            "shots": [
                {
                    "shot_id": "shot_03",
                    "source_text": "柳如烟独自坐在石阶上，望着远山发呆。",
                    "duration_sec": 5,
                    "characters": [{"name": "柳如烟", "role": "红衣女子"}],
                    "props": [],
                    "actions": ["坐", "望"],
                    "emotion": "悲伤",
                    "dialogue": [],
                    "visual_intent": "柳如烟独坐石阶，远山薄雾，眼神落寞。",
                },
            ],
        },
    ],
    "validation": {"status": "valid", "errors": [], "warnings": []},
}


def test_template_library() -> None:
    print("运镜模板库：")
    tmpls = ct.list_camera_templates()
    _check("共 15 条（10 基础 + 5 补充）", len(tmpls) == 15, str(len(tmpls)))
    ids = [t["id"] for t in tmpls]
    for expect in ("establish", "enter", "walk", "tense", "see_object", "dialogue",
                   "emotion_close", "chase", "memory", "ending",
                   "combat", "montage", "empty_transition", "orbit", "boom"):
        _check(f"含模板 {expect}", expect in ids, str(ids))
    _check("每条含 id/name/intent/template", all(
        all(k in t for k in ("id", "name", "intent", "template")) for t in tmpls
    ))
    _check("无重复 id", len(set(ids)) == len(ids), str(ids))


def _shot(**kw):
    base = {"shot_id": "sx", "source_text": "", "characters": [{"name": "林雪"}],
            "props": [], "actions": [], "emotion": "", "dialogue": [], "visual_intent": ""}
    base.update(kw)
    return base


SCENE = {"scene_id": "scene_01", "title": "大堂", "location_name": "客栈大堂",
         "time": "清晨", "weather": "雨"}


def test_pick_template_intents() -> None:
    print("pick_camera 意图命中：")
    d, intent, st = ct.pick_camera(_shot(characters=[], source_text="空荡的客栈大堂，空无一人。"),
                                   SCENE, None, is_last_shot=False)
    _check("首镜无角色 → establish", intent == "establish", intent)
    _assert_in("establish 措辞", "俯冲", d)

    d, intent, st = ct.pick_camera(_shot(dialogue=[{"speaker": "陈默", "text": "hi"}]),
                                   SCENE, None, is_last_shot=False)
    _check("对白 → dialogue", intent == "dialogue", intent)
    _assert_in("对白措辞", "正反打对切", d)

    d, intent, st = ct.pick_camera(_shot(source_text="林雪沿着山路行走。"),
                                   SCENE, None, is_last_shot=False)
    _check("行走 → walk(motion)", intent == "motion", intent)
    _assert_in("行走措辞", "跟移", d)

    d, intent, st = ct.pick_camera(_shot(source_text="黑衣人开始狂奔追逐。"),
                                   SCENE, None, is_last_shot=False)
    _check("追逐 → chase(motion)", intent == "motion", intent)
    _assert_in("追逐措辞", "手持", d)

    d, intent, st = ct.pick_camera(_shot(source_text="两人拔剑战斗。"),
                                   SCENE, None, is_last_shot=False)
    _check("战斗 → combat", intent == "combat", intent)
    _assert_in("战斗措辞", "甩镜", d)

    d, intent, st = ct.pick_camera(_shot(visual_intent="特写林雪的手握紧伞柄。"),
                                   SCENE, None, is_last_shot=False)
    _check("特写 → emotion_close", intent == "emotion", intent)
    _assert_in("特写措辞", "特写", d)

    d, intent, st = ct.pick_camera(_shot(visual_intent="特写林雪看见桌上的剑。"),
                                   SCENE, None, is_last_shot=False)
    _check("特写+看见 → see_object", intent == "close", intent)
    _assert_in("过肩措辞", "过肩", d)

    d, intent, st = ct.pick_camera(_shot(emotion="惊恐"), SCENE, None, is_last_shot=False)
    _check("强情绪 → emotion_close", intent == "emotion", intent)
    _assert_in("情绪措辞", "推近", d)

    d, intent, st = ct.pick_camera(_shot(source_text="路人静静站着。"),
                                   SCENE, None, is_last_shot=True)
    _check("无特征尾镜 → ending", intent == "ending", intent)
    _assert_in("尾镜措辞", "拉远", d)

    d, intent, st = ct.pick_camera(_shot(source_text="路人静静站着。"),
                                   SCENE, None, is_last_shot=False)
    _check("无特征非尾镜 → default", intent == "default", intent)
    _assert_in("default 措辞", "固定机位", d)


def test_continuity_direction() -> None:
    print("跨镜状态机 · 方向延续：")
    # 上镜 walk（右向）→ 下镜也是行走
    _, _, prev = ct.pick_camera(_shot(source_text="林雪向右行走。"),
                                SCENE, None, is_last_shot=False)
    _check("上镜为 walk（右向）", prev["movement_direction"] == "right", str(prev))
    d, intent, _ = ct.pick_camera(_shot(source_text="她继续向前走。"),
                                  SCENE, prev, is_last_shot=False)
    _assert_in("下镜承接上镜右向", "承接上镜右向运动", d)


def test_continuity_reverse() -> None:
    print("跨镜状态机 · 对话反打：")
    # 上镜 see_object（过肩）→ 下镜对话 → 反打对切
    _, _, prev = ct.pick_camera(_shot(visual_intent="特写林雪看见桌上的剑。"),
                                SCENE, None, is_last_shot=False)
    _check("上镜为 see_object（过肩）", prev["camera_position"] == "over_shoulder", str(prev))
    d, intent, _ = ct.pick_camera(_shot(dialogue=[{"speaker": "陈默", "text": "hi"}]),
                                  SCENE, prev, is_last_shot=False)
    _assert_in("下镜对话承接反打", "承接上镜视线，反打对切", d)


def test_continuity_pullback() -> None:
    print("跨镜状态机 · 避免重复推近：")
    # 上镜 emotion_close（推近特写）→ 下镜又是特写推近 → 保持景别微推
    _, _, prev = ct.pick_camera(_shot(emotion="惊恐"), SCENE, None, is_last_shot=False)
    _check("上镜为推近特写", prev["movement"] == "push_in" and prev["shot_size"] == "close_up",
           str(prev))
    d, intent, _ = ct.pick_camera(_shot(visual_intent="特写林雪的脸颊。"),
                                  SCENE, prev, is_last_shot=False)
    _assert_in("下镜避免重复推近", "保持特写景别，小幅推进", d)


def test_scene_reset() -> None:
    print("跨镜状态机 · 场景切换重置：")
    # 场景 1 末镜行走右向 → 场景 2 首镜（prev_state=None）→ 无延续前缀
    _, _, prev = ct.pick_camera(_shot(source_text="林雪向右行走。"),
                                SCENE, None, is_last_shot=True)
    _check("场景1末镜行走右向", prev["movement_direction"] == "right", str(prev))
    # 新场景首镜 prev_state=None → 无「承接」前缀
    d, intent, _ = ct.pick_camera(
        _shot(source_text="柳如烟独自坐在石阶上。"),
        {"scene_id": "scene_02", "title": "后院", "location_name": "客栈后院",
         "time": "午后", "weather": "晴"},
        None, is_last_shot=False,
    )
    _check("新场景首镜无延续前缀", "承接上镜" not in d, repr(d))


def test_build_plan_cameras() -> None:
    print("build_plan_cameras 全链路：")
    plan = ProductionPlan.from_dict(PLAN)
    out = ct.build_plan_cameras(plan)
    _check("template_version = camera-v2", out["template_version"] == "camera-v2",
           out["template_version"])
    cams = out["cameras"]
    _check("3 镜全部生成", len(cams) == 3, str(len(cams)))
    ids = [(c["scene_id"], c["shot_id"]) for c in cams]
    _check("顺序对齐 scene/shot",
           ids == [("scene_01", "shot_01"), ("scene_01", "shot_02"), ("scene_02", "shot_03")],
           str(ids))
    _check("每镜 camera 非空", all(bool(str(c["camera"]).strip()) for c in cams))
    # shot_01（推开/收伞，无对白）→ 动作镜；shot_02（对白）→ dialogue
    _check("shot_01 camera_intent = motion", cams[0]["camera_intent"] == "motion", cams[0]["camera_intent"])
    _check("shot_02 camera_intent = dialogue", cams[1]["camera_intent"] == "dialogue", cams[1]["camera_intent"])
    # 场景 2 首镜（prev 重置）无延续前缀
    _check("场景2首镜无延续前缀", "承接上镜" not in cams[2]["camera"], repr(cams[2]["camera"]))
    _check("shot_03 记录 camera_template", bool(cams[2]["camera_template"]), cams[2]["camera_template"])
    # 每镜带 camera_template 记录（进前端下拉回显）
    for c in cams:
        _check(f"{c['shot_id']} camera_template 非空", bool(c["camera_template"]), c["camera_template"])


def test_no_weak_camera_words() -> None:
    print("camera-v2 无弱指令（网上漫剧运镜强化）：")
    # 非 default 模板禁止「缓摇/缓慢推近/轻微/轻柔/轻推/固定机位」等弱运镜措辞
    #（#600 升级：每镜必须带明确强动态运镜 + 英文摄影术语辅助）。
    weak = ("缓摇", "缓慢", "缓推", "轻微", "轻柔", "轻推", "微推", "微升降", "慢慢")
    weak_tmpls = [
        t["id"] for t in ct.CAMERA_TEMPLATES if any(w in t["template"] for w in weak)
    ]
    _check("基础+补充模板无弱指令", not weak_tmpls, str(weak_tmpls))
    strong_kw = ("推近", "推前", "跟移", "环绕", "俯冲", "甩镜", "拉远", "横移", "升降",
                 "手持", "摇移", "特写", "摇动", "摇摄")
    for t in ct.CAMERA_TEMPLATES:
        _check(f"{t['id']} 措辞含明确运镜", any(k in t["template"] for k in strong_kw),
               t["template"])


def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(ct.__file__), "camera_template.py"),
               encoding="utf-8").read()
    for bad in ("unload", "gpu_models_loaded", "keep_alive", "empty_cache", "urlopen",
                "requests", "import torch", "from torch", "aiohttp", "ollama"):
        _check(f"源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_camera_template\n")
    test_template_library()
    test_pick_template_intents()
    test_continuity_direction()
    test_continuity_reverse()
    test_continuity_pullback()
    test_scene_reset()
    test_build_plan_cameras()
    test_no_weak_camera_words()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
