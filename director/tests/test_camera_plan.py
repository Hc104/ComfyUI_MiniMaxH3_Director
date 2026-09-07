#!/usr/bin/env python3
"""V1.7 Phase 5（P1-D）：运镜模板库路由 + prompt_builder 接入测试（纯规则，mock）。

覆盖（V17_PLAN §7 / Phase 5 独立验收）：
1. GET /minimax/director/camera/templates：15 条（10 基础 + 5 补充）+ template_version=camera-v2；
2. POST /minimax/director/camera/plan：正常 plan → {template_version, cameras}；
   bad plan / bad json → 400；
3. build_plan_drafts 接入跨镜状态机：
   - camera 措辞由状态机生成（含场景内延续前缀）；
   - 每镜透出 camera_template（模板 id）；
   - 场景切换重置（场景 2 首镜无「承接上镜」前缀）；
   - generation_mode 建议不变 [fl2v, r2v, t2v]；
   - use_camera_planner=False 回退 Phase 3 intent 措辞（无 camera_template）；
4. 路由已注册进 register_routes。

http_routes.py 依赖 ComfyUI 的 server/folder_paths，测试在 import 前注入假模块。
直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_camera_plan.py
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

# ---- 假 aiohttp（普通 python 环境可能未装；handler 只用 web.json_response）----
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
from director import http_routes as hr  # noqa: E402
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


class _FakeRequest:
    def __init__(self, body, method: str = "POST"):
        self._body = body
        self.method = method

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _resp_data(resp):
    """web.json_response 返回 web.Response：status 属性 + async json()。"""
    return asyncio.run(resp.json())


def test_templates_route() -> None:
    print("[1] GET /camera/templates：")
    resp = asyncio.run(hr.minimax_director_camera_templates(_FakeRequest(None, method="GET")))
    _check("status 200", resp.status == 200, str(resp.status))
    data = _resp_data(resp)
    _check("template_version = camera-v2", data.get("template_version") == "camera-v2",
           str(data.get("template_version")))
    tmpls = data.get("templates", [])
    _check("共 15 条（10 基础 + 5 补充）", len(tmpls) == 15, str(len(tmpls)))
    _check("每条含 id/name/intent/template", all(
        all(k in t for k in ("id", "name", "intent", "template")) for t in tmpls
    ), str(tmpls[:1]))
    ids = [t["id"] for t in tmpls]
    _check("无重复 id", len(set(ids)) == len(ids), str(ids))
    _check("含 establish / walk / dialogue / combat / empty_transition",
           all(k in ids for k in ("establish", "walk", "dialogue", "combat", "empty_transition")), str(ids))


def test_camera_plan_route() -> None:
    print("[2] POST /camera/plan：")
    async def run():
        r1 = await hr.minimax_director_camera_plan(_FakeRequest({"foo": 1}))
        _check("bad plan → 400", r1.status == 400, str(r1.status))
        r2 = await hr.minimax_director_camera_plan(
            _FakeRequest({"plan": ProductionPlan.from_dict(PLAN).to_dict()}))
        _check("正常 → 200", r2.status == 200, str(r2.status))
        data = r2._data
        _check("template_version = camera-v2", data["template_version"] == "camera-v2",
               str(data.get("template_version")))
        cams = data.get("cameras", [])
        _check("3 镜全部生成", len(cams) == 3, str(len(cams)))
        ids = [(c["scene_id"], c["shot_id"]) for c in cams]
        _check("顺序对齐 scene/shot",
               ids == [("scene_01", "shot_01"), ("scene_01", "shot_02"), ("scene_02", "shot_03")],
               str(ids))
        _check("每镜 camera/camera_intent/camera_template 非空", all(
            bool(str(c["camera"]).strip()) and bool(c["camera_intent"]) and bool(c["camera_template"])
            for c in cams
        ), str(cams))
        _check("场景2首镜无延续前缀（切换重置）", "承接上镜" not in cams[2]["camera"],
               repr(cams[2]["camera"]))
    asyncio.run(run())


def test_build_plan_drafts_state_machine() -> None:
    print("[3] build_plan_drafts 接入跨镜状态机：")
    plan = ProductionPlan.from_dict(PLAN)
    out = pb.build_plan_drafts(plan)
    drafts = out["drafts"]
    _check("template_version = h3-v1", out["template_version"] == "h3-v1", out["template_version"])
    _check("3 镜全部生成", len(drafts) == 3, str(len(drafts)))
    # shot_01：推开/收伞 → walk 模板（状态机）
    _check("shot_01 camera_template = walk", drafts[0]["camera_template"] == "walk",
           drafts[0]["camera_template"])
    _check("shot_01 camera 非空", bool(str(drafts[0]["draft"]["camera"]).strip()),
           repr(drafts[0]["draft"]["camera"]))
    # shot_02：对白 → dialogue 模板
    _check("shot_02 camera_template = dialogue", drafts[1]["camera_template"] == "dialogue",
           drafts[1]["camera_template"])
    _check("shot_02 camera 含正反打", "正反打对切" in drafts[1]["draft"]["camera"],
           repr(drafts[1]["draft"]["camera"]))
    # shot_03：场景 2 首镜（prev 重置）→ 无延续前缀
    _check("shot_03 无延续前缀", "承接上镜" not in drafts[2]["draft"]["camera"],
           repr(drafts[2]["draft"]["camera"]))
    _check("shot_03 camera_template 非空", bool(drafts[2]["camera_template"]),
           drafts[2]["camera_template"])
    # generation_mode 建议不变
    _check("generation_mode 建议 = [fl2v, r2v, t2v]",
           [d["generation_mode"] for d in drafts] == ["fl2v", "r2v", "t2v"],
           str([d["generation_mode"] for d in drafts]))
    # 五区齐全
    for d in drafts:
        for sec in ("visual", "camera", "style", "sound", "negative"):
            _check(f"{d['shot_id']}.{sec} 非空", bool(str(d["draft"][sec]).strip()),
                   repr(d["draft"][sec]))


def test_build_plan_drafts_fallback() -> None:
    print("[4] use_camera_planner=False 回退 Phase 3 intent 措辞：")
    plan = ProductionPlan.from_dict(PLAN)
    out = pb.build_plan_drafts(plan, use_camera_planner=False)
    drafts = out["drafts"]
    _check("无 camera_template 字段（回退）", all("camera_template" not in d or not d["camera_template"]
                                                for d in drafts), str(drafts[0].keys()))
    # 回退措辞仍用 v1.json intent 模板（跟移/正反打/推近）
    _check("shot_01 camera 含跟移（Phase 3 motion）", "跟移" in drafts[0]["draft"]["camera"],
           repr(drafts[0]["draft"]["camera"]))
    _check("shot_02 camera_intent = dialogue", drafts[1]["draft"]["camera_intent"] == "dialogue",
           drafts[1]["draft"]["camera_intent"])


def test_route_registered() -> None:
    print("[5] 路由已注册进 register_routes：")
    src = open(os.path.join(_REPO_ROOT, "director", "http_routes.py"), "r", encoding="utf-8").read()
    _check("register_routes 内含 camera/templates", "/minimax/director/camera/templates" in src)
    _check("register_routes 内含 camera/plan", "/minimax/director/camera/plan" in src)
    _check("handler 函数存在",
           hasattr(hr, "minimax_director_camera_templates") and hasattr(hr, "minimax_director_camera_plan"))


def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存（prompt_builder + camera_plan 链路）：")
    src = open(os.path.join(os.path.dirname(pb.__file__), "prompt_builder_v17.py"), encoding="utf-8").read()
    for bad in ("unload", "gpu_models_loaded", "keep_alive", "empty_cache", "urlopen", "requests", "torch"):
        _check(f"prompt_builder_v17 源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_camera_plan\n")
    test_templates_route()
    test_camera_plan_route()
    test_build_plan_drafts_state_machine()
    test_build_plan_drafts_fallback()
    test_route_registered()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
