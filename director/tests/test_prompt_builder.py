#!/usr/bin/env python3
"""V1.7 Phase 3：prompt_builder_v17 单元测试（纯规则，mock，不碰网络/GPU）。

覆盖（V17_PLAN §13 / Phase 3 独立验收）：
1. Prompt Template Registry：load h3-v1 成功（版本 + 五区 sections + palettes + sound_hints）；未知版本 ValueError；
2. generation_mode 判定：首镜 t2v / 续镜 r2v / 强连续动作 fl2v（V17-P0-EXT 允许 Phase 3 做）；
3. 五区草稿：visual（visual_intent 优先 / 空时 fallback 组合）、camera（establish/close/dialogue/motion/emotion/ending/default）、
   style（天气配色）、sound（环境音 + 对白 + 情绪音乐）、negative（模板）；
4. build_plan_drafts 全链路：每镜五区非空 + template_version + generation_mode 建议 + 顺序对齐；
5. ⛔ 纯规则零显存：模块无 unload/gpu_models_loaded/keep_alive/Ollama 调用；
6. 路由 /minimax/director/prompt/draft：bad plan / bad json → 400，正常 → {template_version, drafts}。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_prompt_builder.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types

# ---- 在 import director.http_routes 前注入假 ComfyUI 模块 ----
_server_mod = types.ModuleType("server")
_server_mod.PromptServer = type("PromptServer", (), {"instance": None})
sys.modules["server"] = _server_mod

_folder_paths_mod = types.ModuleType("folder_paths")
_folder_paths_mod.get_input_directory = lambda: "/tmp/fake_input"
_folder_paths_mod.get_temp_directory = lambda: "/tmp/fake_temp"
sys.modules["folder_paths"] = _folder_paths_mod


class _FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    async def json(self):
        return self._data


_aiohttp = types.ModuleType("aiohttp")
_aiohttp_web = types.ModuleType("aiohttp.web")


def _fake_json_response(data, status=200, **kw):
    return _FakeResponse(data, status)


def _fake_response(status=200, text=""):
    return _FakeResponse({"error": "http_error", "message": text}, status)


_aiohttp_web.json_response = _fake_json_response
_aiohttp_web.Response = _fake_response
_aiohttp.web = _aiohttp_web
sys.modules["aiohttp"] = _aiohttp
sys.modules["aiohttp.web"] = _aiohttp_web

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.prompt_builder_v17 as pb  # noqa: E402
from director.http_routes import (  # noqa: E402
    minimax_director_prompt_draft,
    minimax_director_prompt_h3,
)
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
                    "entities": [
                        {"name": "山雨客栈", "type": "architecture",
                         "confidence": 0.95, "source": "qwen"},
                    ],
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


def test_template_registry() -> None:
    print("模板 Registry：")
    tmpl = pb.load_template_registry()
    _check("缺省版本 = h3-v1", tmpl["version"] == "h3-v1", str(tmpl.get("version")))
    _check("五区 sections 齐全", all(
        k in tmpl["sections"] for k in ("visual", "camera", "style", "sound", "negative")
    ), str(tmpl.get("sections", {}).keys()))
    _check("camera 顶层有 default",
           bool(str(tmpl["sections"]["camera"].get("default", "")).strip()))
    _check("camera.intents 含 6 个意图",
           set(("establish", "close", "dialogue", "motion", "emotion", "ending"))
           <= set(tmpl["sections"]["camera"]["intents"]),
           str(tmpl["sections"]["camera"]["intents"].keys()))
    _check("palettes 含雨/夜/黄昏/雪/清晨/default",
           all(k in tmpl["palettes"] for k in ("雨", "夜", "黄昏", "雪", "清晨", "default")))
    _check("sound_hints 含 dialogue/music_tense/music_calm",
           all(k in tmpl["sound_hints"] for k in ("dialogue", "music_tense", "music_calm")))
    try:
        pb.load_template_registry("h3-unknown")
        _check("未知版本抛 ValueError", False, "未抛异常")
    except ValueError:
        _check("未知版本抛 ValueError", True)


def test_generation_mode() -> None:
    print("generation_mode 判定：")
    s1 = PLAN["scenes"][0]["shots"][0]  # 推开/收伞 → fl2v
    s2 = PLAN["scenes"][0]["shots"][1]  # 无强动作 → 续镜 r2v
    s3 = PLAN["scenes"][1]["shots"][0]  # 无强动作 → 首镜 t2v
    _check("首镜 + 强连续动作 → fl2v", pb.judge_generation_mode(s1, has_prev=False) == "fl2v")
    _check("续镜 + 无强动作 → r2v", pb.judge_generation_mode(s2, has_prev=True) == "r2v")
    _check("首镜 + 无强动作 → t2v", pb.judge_generation_mode(s3, has_prev=False) == "t2v")
    _check("续镜 + 无强动作且无对白 → r2v", pb.judge_generation_mode(s3, has_prev=True) == "r2v")


def test_visual() -> None:
    print("visual 草稿：")
    tmpl = pb.load_template_registry()
    scene1 = PLAN["scenes"][0]
    s1 = scene1["shots"][0]
    s2 = scene1["shots"][1]
    v1 = pb._build_visual(tmpl, s1, scene1)
    _check("visual_intent 优先", v1 == "青衫女侠推开木门，雨水顺着伞沿滴落。", repr(v1))
    v2 = pb._build_visual(tmpl, s2, scene1)
    _check("空 visual_intent → fallback 组合", "林雪、陈默" in v2 and "抬眼，说话" in v2, repr(v2))
    _assert_in("fallback 含地点", "山雨客栈大堂", v2)
    _assert_in("fallback 含时间天气", "清晨、雨", v2)


def test_camera_intents() -> None:
    print("camera 意图：")
    tmpl = pb.load_template_registry()
    scene1 = PLAN["scenes"][0]
    scene2 = PLAN["scenes"][1]
    s1 = scene1["shots"][0]  # 推开/收伞 → motion
    s2 = scene1["shots"][1]  # 对白 → dialogue
    s3 = scene2["shots"][0]  # 悲伤 → emotion（尾镜）
    # 环境建立：首镜无角色
    env = {"shot_id": "sx", "characters": [], "actions": [], "dialogue": [], "emotion": "",
           "visual_intent": "空荡的客栈大堂。", "source_text": "清晨的客栈大堂空无一人。"}
    # 特写意图
    cl = {"shot_id": "sc", "characters": [{"name": "林雪"}], "actions": [], "dialogue": [], "emotion": "",
          "visual_intent": "特写林雪的手握紧伞柄。", "source_text": "林雪握紧伞柄。"}
    d = pb.build_shot_draft(s1, scene1, tmpl, has_prev=False, is_last_shot=False)
    _check("动作镜 → motion（跟移）", d["camera_intent"] == "motion", d["camera_intent"])
    _assert_in("跟移措辞", "跟移", d["camera"])
    d = pb.build_shot_draft(s2, scene1, tmpl, has_prev=True, is_last_shot=True)
    _check("对白镜 → dialogue（正反打）", d["camera_intent"] == "dialogue", d["camera_intent"])
    _assert_in("对切措辞", "正反打对切", d["camera"])
    d = pb.build_shot_draft(s3, scene2, tmpl, has_prev=False, is_last_shot=True)
    _check("强情绪 → emotion（推近）", d["camera_intent"] == "emotion", d["camera_intent"])
    _assert_in("推近措辞", "缓慢推近", d["camera"])
    d = pb.build_shot_draft(env, scene1, tmpl, has_prev=False, is_last_shot=False)
    _check("首镜无角色 → establish（摇摄）", d["camera_intent"] == "establish", d["camera_intent"])
    _assert_in("摇摄措辞", "缓摇", d["camera"])
    d = pb.build_shot_draft(cl, scene1, tmpl, has_prev=False, is_last_shot=False)
    _check("特写意图 → close", d["camera_intent"] == "close", d["camera_intent"])
    _assert_in("特写措辞", "特写", d["camera"])
    d = pb.build_shot_draft(s3, scene2, tmpl, has_prev=True, is_last_shot=True)
    _check("强情绪优先于尾镜 → emotion", d["camera_intent"] == "emotion", d["camera_intent"])
    d = pb.build_shot_draft(
        {"shot_id": "sx2", "characters": [{"name": "路人"}], "actions": ["站立"], "dialogue": [],
         "emotion": "", "visual_intent": "", "source_text": "路人静静站着。"},
        scene1, tmpl, has_prev=True, is_last_shot=True,
    )
    _check("无特征尾镜 → ending（拉远）", d["camera_intent"] == "ending", d["camera_intent"])
    _assert_in("拉远措辞", "拉远", d["camera"])
    d = pb.build_shot_draft(
        {"shot_id": "sx3", "characters": [{"name": "路人"}], "actions": ["站立"], "dialogue": [],
         "emotion": "", "visual_intent": "", "source_text": "路人静静站着。"},
        scene1, tmpl, has_prev=True, is_last_shot=False,
    )
    _check("无特征非尾镜 → default", d["camera_intent"] == "default", d["camera_intent"])


def test_style_sound_negative() -> None:
    print("style / sound / negative：")
    tmpl = pb.load_template_registry()
    scene1 = PLAN["scenes"][0]  # 雨
    scene2 = PLAN["scenes"][1]  # 晴
    s1 = scene1["shots"][0]
    s2 = scene1["shots"][1]
    s3 = scene2["shots"][0]
    st = pb._build_style(tmpl, scene1)
    _assert_in("雨景配色", "冷色调", st)
    _assert_in("雨景配色细节", "湿地面反射", st)
    st2 = pb._build_style(tmpl, scene2)
    _assert_in("晴天默认配色", "自然色调", st2)
    sd1 = pb._build_sound(tmpl, s1, scene1)
    _assert_in("雨环境音", "雨声淅沥", sd1)
    _check("无对白 → 无对白提示", "对白清晰" not in sd1, repr(sd1))
    sd2 = pb._build_sound(tmpl, s2, scene1)
    _assert_in("对白 → 对白清晰提示", "对白清晰", sd2)
    sd3 = pb._build_sound(tmpl, s3, scene2)
    _assert_in("强情绪 → 紧张配乐提示", "配乐", sd3)
    neg = pb._build_negative(tmpl)
    _check("negative 非空且含负面词", bool(neg.strip()) and "模糊" in neg, repr(neg))


def test_build_plan_drafts() -> None:
    print("build_plan_drafts 全链路：")
    plan = ProductionPlan.from_dict(PLAN)
    out = pb.build_plan_drafts(plan)
    _check("template_version = h3-v1", out["template_version"] == "h3-v1", out["template_version"])
    drafts = out["drafts"]
    _check("3 镜全部生成", len(drafts) == 3, str(len(drafts)))
    ids = [(d["scene_id"], d["shot_id"]) for d in drafts]
    _check("顺序对齐 scene/shot",
           ids == [("scene_01", "shot_01"), ("scene_01", "shot_02"), ("scene_02", "shot_03")], str(ids))
    _check("generation_mode 建议 = [fl2v, r2v, t2v]",
           [d["generation_mode"] for d in drafts] == ["fl2v", "r2v", "t2v"],
           str([d["generation_mode"] for d in drafts]))
    for d in drafts:
        for sec in ("visual", "camera", "style", "sound", "negative"):
            _check(f"{d['shot_id']}.{sec} 非空", bool(str(d["draft"][sec]).strip()), repr(d["draft"][sec]))


def test_route_h3() -> None:
    print("路由 /minimax/director/prompt/h3：")

    async def run():
        r1 = await minimax_director_prompt_h3(_FakeResponse({"foo": 1}))
        _check("bad plan → 400", r1.status == 400, str(r1.status))

        plan = ProductionPlan.from_dict(PLAN)
        r2 = await minimax_director_prompt_h3(_FakeResponse({"plan": plan.to_dict()}))
        _check("无绑定 → 200", r2.status == 200, str(r2.status))
        data = r2._data
        _check("schema = minimax-h3-project-v1", data["schema"] == "minimax-h3-project-v1",
               str(data.get("schema")))
        prompts = data["prompts"]
        _check("prompts 覆盖 3 镜",
               set(prompts) == {"scene_01:shot_01", "scene_01:shot_02", "scene_02:shot_03"},
               str(list(prompts)))
        p1 = prompts["scene_01:shot_01"]
        _check("三段齐全", all(bool(p1[k]) for k in (
            "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")))
        _check("时间轴前缀 [0-5s]", p1["integrated_multimodal_description"].startswith("[0-5s] "))
        _check("无绑定 → references 空 / 无 [REF]", p1["references"] == []
               and "[REF:" not in p1["integrated_multimodal_description"])
        _check("provenance 三类来源键存在", set(p1["provenance"]["integrated_multimodal_description"])
               == {"user_facts", "ai_supplement", "rule_generated"})

        r3 = await minimax_director_prompt_h3(_FakeResponse({
            "plan": plan.to_dict(),
            "bindings_by_shot": {
                "scene_01:shot_01": {
                    "architecture:山雨客栈": {"asset_id": "a1", "image_file": "shan.png",
                                              "ref_image": "ref_img_0"},
                },
            },
            "duration_by_shot": {"scene_01:shot_01": 6.0},
        }))
        _check("带绑定 → 200", r3.status == 200, str(r3.status))
        pd = r3._data["prompts"]["scene_01:shot_01"]
        _check("绑定 → references 非空 + [REF] 标签",
               pd["references"] and "[REF: " in pd["integrated_multimodal_description"],
               str(pd["references"]))
        _check("时长 6s → 时间轴 [0-6s]", pd["integrated_multimodal_description"].startswith("[0-6s] "),
               pd["integrated_multimodal_description"][:20])

        r4 = await minimax_director_prompt_h3(_FakeResponse({
            "plan": plan.to_dict(), "bindings_by_shot": "not-a-dict"}))
        _check("非法 bindings → 400", r4.status == 400, str(r4.status))

    asyncio.run(run())


def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(pb.__file__), "prompt_builder_v17.py"), encoding="utf-8").read()
    for bad in ("unload", "gpu_models_loaded", "keep_alive", "empty_cache", "urlopen", "requests", "torch"):
        _check(f"源码无 {bad} 引用", bad not in src)


def test_route() -> None:
    print("路由 /minimax/director/prompt/draft：")

    async def run():
        r1 = await minimax_director_prompt_draft(_FakeResponse({"foo": 1}))
        _check("bad plan → 400", r1.status == 400, str(r1.status))
        r2 = await minimax_director_prompt_draft(_FakeResponse({"plan": {"scenes": []}}))
        _check("空 scenes plan → 200（合法空计划）", r2.status == 200, str(r2.status))
        r3 = await minimax_director_prompt_draft(_FakeResponse({"plan": ProductionPlan.from_dict(PLAN).to_dict()}))
        _check("正常 → 200", r3.status == 200, str(r3.status))
        data = r3._data
        _check("返回 template_version", data["template_version"] == "h3-v1", str(data.get("template_version")))
        _check("返回 drafts 长度 3", len(data["drafts"]) == 3, str(len(data.get("drafts", []))))

    asyncio.run(run())


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_prompt_builder\n")
    test_template_registry()
    test_generation_mode()
    test_visual()
    test_camera_intents()
    test_style_sound_negative()
    test_build_plan_drafts()
    test_no_gpu_side_effects()
    test_route()
    test_route_h3()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
