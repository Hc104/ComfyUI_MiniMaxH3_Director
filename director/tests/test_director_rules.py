#!/usr/bin/env python3
"""V1.0 运镜规则层·第二/三层（导演规则 + 镜头功能）单元测试（纯规则，mock，零显存）。

覆盖（《AI 漫剧导演运镜规则 v1.0》§2 第二/三层，用户 2026-08-17 拍板）：
1. 镜头功能 assign_shot_role：establish / emotion / ending / info / reaction / action；
2. 对白爆发检测：！？/质问词（还是说/凭什么/为什么）→ emotion，中性台词 → 非；
3. 四维决策 decide_camera：景别（情绪→特写/信息→中景）、运动（情绪→推近/中性→静止）、
   机位（强者→低/弱小→高）、构图（正反打对切/反应特写）；
4. 跨镜前缀：承接上镜运动方向；
5. next_state 携带 shot_role / camera_angle / template_id / movement / shot_size；
6. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_director_rules.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.director_rules as dr  # noqa: E402

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


def _scene(location: str = "山雨客栈", time: str = "夜", weather: str = "雨") -> SimpleNamespace:
    return SimpleNamespace(location_name=location, time=time, weather=weather)


# ---------- 镜头功能 assign_shot_role ----------

def test_assign_role_establish() -> None:
    print("assign_shot_role · establish：")
    shot = _shot(source_text="大雨笼罩山雨客栈。")
    _check("场景首镜无角色 → establish",
           dr.assign_shot_role(shot, _scene(), is_scene_first=True, is_last_shot=False) == "establish")


def test_assign_role_emotion_bomb() -> None:
    print("assign_shot_role · 对白爆发 → emotion：")
    for bomb in ("凭什么", "还是说", "为什么", "你再说一遍", "救命"):
        shot = _shot(dialogue=(f"你说什么？{bomb}",), characters=(_char("林薇薇"),))
        _check(f"「{bomb}」→ emotion",
               dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False) == "emotion")
    shot = _shot(dialogue=("怎么可能……！",), characters=(_char("林薇薇"),))
    _check("「！」→ emotion",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False) == "emotion")
    shot = _shot(emotion="惊恐", characters=(_char("林薇薇"),))
    _check("镜头情绪「惊恐」→ emotion",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False) == "emotion")


def test_assign_role_ending_info_reaction_action() -> None:
    print("assign_shot_role · ending/info/reaction/action：")
    shot = _shot(characters=(_char("林薇薇"),))
    _check("场景尾镜无对白 → ending",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=True) == "ending")
    shot = _shot(dialogue=("这么大的雨，赶路辛苦了。",), characters=(_char("林薇薇"), _char("顾琰宸")))
    _check("中性对白 → info",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False) == "info")
    prev_intent = SimpleNamespace(dialogue=[SimpleNamespace(text="辛苦了。")])
    shot = _shot(characters=(_char("林薇薇"),))
    _check("无对白+上镜有对白 → reaction",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False,
                               prev_intent=prev_intent) == "reaction")
    shot = _shot(characters=(_char("顾琰宸"),))
    _check("无对白无上镜 → action",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False) == "action")


def test_prev_has_dialogue_director_intent_shape() -> None:
    print("_prev_has_dialogue · DirectorIntent 形态（audio.dialogue）：")
    prev = SimpleNamespace(dialogue=None,
                           audio={"ambient": "雨声", "dialogue": [{"speaker": "林薇薇", "text": "辛苦了。"}]})
    shot = _shot(characters=(_char("林薇薇"),))
    _check("audio.dialogue → reaction",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False,
                               prev_intent=prev) == "reaction")
    prev_no = SimpleNamespace(dialogue=None, audio={"ambient": "雨声", "dialogue": []})
    _check("audio.dialogue 空 → 非 reaction",
           dr.assign_shot_role(shot, _scene(), is_scene_first=False, is_last_shot=False,
                               prev_intent=prev_no) == "action")
    _check("prev_intent=None → False",
           dr._prev_has_dialogue(None) is False)


def test_dialogue_has_bomb() -> None:
    print("_dialogue_has_bomb：")
    _check("质问词 → True", dr._dialogue_has_bomb(_shot(dialogue=("凭什么？",))) is True)
    _check("感叹号 → True", dr._dialogue_has_bomb(_shot(dialogue=("怎么可能！",))) is True)
    _check("中性句 → False", dr._dialogue_has_bomb(_shot(dialogue=("辛苦了。",))) is False)
    _check("空对白 → False", dr._dialogue_has_bomb(_shot()) is False)


# ---------- 第二层四维决策 decide_camera ----------

def test_decide_emotion_push_in() -> None:
    print("decide_camera · 情绪镜 → 推近特写：")
    shot = _shot(emotion="悲伤", characters=(_char("林薇薇"),))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "emotion", None,
                                        is_last_shot=False, scene_has_prev=False)
    _check("意图=emotion", intent == "emotion", intent)
    _check("措辞含推近", "推近" in desc, desc)
    _check("next_state.shot_role=emotion", ns.get("shot_role") == "emotion", str(ns))
    _check("next_state.shot_size=close_up", ns.get("shot_size") == "close_up", str(ns))
    _check("next_state.movement=push_in", ns.get("movement") == "push_in", str(ns))


def test_decide_reaction_static() -> None:
    print("decide_camera · 反应镜 → 静止特写：")
    shot = _shot(characters=(_char("林薇薇"),))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "reaction", None,
                                        is_last_shot=False, scene_has_prev=False)
    _check("意图=reaction", intent == "reaction", intent)
    _check("措辞含反应特写", "反应特写" in desc, desc)
    _check("措辞含静止", "静止" in desc, desc)
    _check("next_state.movement=static", ns.get("movement") == "static", str(ns))
    _check("next_state.shot_size=close_up", ns.get("shot_size") == "close_up", str(ns))


def test_decide_info_static_reverse() -> None:
    print("decide_camera · 信息镜 → 静止正反打：")
    shot = _shot(dialogue=("赶路辛苦了。",), characters=(_char("林薇薇"), _char("顾琰宸")))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "info", None,
                                        is_last_shot=False, scene_has_prev=False)
    _check("意图=dialogue", intent == "dialogue", intent)
    _check("措辞含正反打对切", "正反打对切" in desc, desc)
    _check("措辞含沉稳", "沉稳" in desc, desc)
    _check("next_state.movement=static（静止优先）", ns.get("movement") == "static", str(ns))
    _check("next_state.shot_size=medium", ns.get("shot_size") == "medium", str(ns))

    shot1 = _shot(dialogue=("赶路辛苦了。",), characters=(_char("林薇薇"),))
    desc1, _, _ = dr.decide_camera(shot1, _scene(), "info", None,
                                   is_last_shot=False, scene_has_prev=False)
    _check("单人信息镜 → 静止机位", "静止机位" in desc1, desc1)


def test_decide_ending_pull_out() -> None:
    print("decide_camera · 收尾镜 → 拉远：")
    shot = _shot(characters=(_char("林薇薇"),))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "ending", None,
                                        is_last_shot=True, scene_has_prev=False)
    _check("意图=ending", intent == "ending", intent)
    _check("措辞含拉远", "拉远" in desc, desc)
    _check("next_state.movement=pull_out", ns.get("movement") == "pull_out", str(ns))
    _check("next_state.shot_size=wide", ns.get("shot_size") == "wide", str(ns))


def test_decide_action_keyword() -> None:
    print("decide_camera · 动作镜 → 跟移：")
    shot = _shot(source_text="顾琰宸在雨中行走。", characters=(_char("顾琰宸"),))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "action", None,
                                        is_last_shot=False, scene_has_prev=False)
    _check("意图=motion", intent == "motion", intent)
    _check("措辞含跟移", "跟移" in desc, desc)
    _check("next_state.movement=tracking", ns.get("movement") == "tracking", str(ns))


def test_decide_enter_beat() -> None:
    print("decide_camera · 登场镜（走进）→ enter：")
    shot = _shot(source_text="顾琰宸推门走进来。", characters=(_char("顾琰宸"),))
    desc, intent, ns = dr.decide_camera(shot, _scene(), "action", None,
                                        is_last_shot=False, scene_has_prev=False)
    _check("意图=enter", intent == "enter", intent)
    _check("措辞含登场", "登场" in desc, desc)
    _check("next_state.template_id=enter", ns.get("template_id") == "enter", str(ns))


def test_decide_angle_power() -> None:
    print("decide_camera · 机位权力关系：")
    shot = _shot(source_text="推门走进来。", characters=(_char("顾琰宸", "反派"),))
    desc, _, _ = dr.decide_camera(shot, _scene(), "action", None,
                                  is_last_shot=False, scene_has_prev=False)
    _check("反派 → 低机位", "低机位" in desc, desc)
    shot = _shot(emotion="悲伤", characters=(_char("柳如烟", "受害者"),))
    desc2, _, ns2 = dr.decide_camera(shot, _scene(), "emotion", None,
                                     is_last_shot=False, scene_has_prev=False)
    _check("受害者/悲伤 → 高机位", "高机位" in desc2, desc2)
    _check("next_state.camera_angle=high", ns2.get("camera_angle") == "high", str(ns2))


def test_camera_angle_direct() -> None:
    print("_camera_angle 直接判定：")
    _check("角色 反派 → low", dr._camera_angle(_shot(characters=(_char("a", "反派"),)), _scene()) == "low")
    _check("text 弱小 → high", dr._camera_angle(_shot(source_text="无助弱小"), _scene()) == "high")
    _check("emotion 无助 → high", dr._camera_angle(_shot(emotion="无助"), _scene()) == "high")
    _check("中性 → eye_level", dr._camera_angle(_shot(), _scene()) == "eye_level")


def test_continuity_prefix() -> None:
    print("decide_camera · 跨镜前缀（承接运动方向）：")
    prev = {"movement_direction": "right", "shot_size": "medium",
            "camera_position": "side", "template_id": "walk", "movement": "tracking"}
    shot = _shot(source_text="顾琰宸继续前行。", characters=(_char("顾琰宸"),))
    desc, _, _ = dr.decide_camera(shot, _scene(), "action", prev,
                                  is_last_shot=False, scene_has_prev=True)
    _check("承接上镜右向运动", "承接上镜右向运动" in desc, desc)


def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(dr.__file__), "director_rules.py"),
               encoding="utf-8").read()
    for bad in ("torch", "ollama", "urlopen", "requests", "aiohttp", "keep_alive",
                "gpu_models_loaded", "empty_cache"):
        _check(f"源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_director_rules\n")
    test_assign_role_establish()
    test_assign_role_emotion_bomb()
    test_assign_role_ending_info_reaction_action()
    test_prev_has_dialogue_director_intent_shape()
    test_dialogue_has_bomb()
    test_decide_emotion_push_in()
    test_decide_reaction_static()
    test_decide_info_static_reverse()
    test_decide_ending_pull_out()
    test_decide_action_keyword()
    test_decide_enter_beat()
    test_decide_angle_power()
    test_camera_angle_direct()
    test_continuity_prefix()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
