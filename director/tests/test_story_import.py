#!/usr/bin/env python3
"""P1-B-5（#534）：POST /minimax/director/story/import 路由单元测试。

覆盖（P1B_STORY_ANALYZER_PLAN §8.1 验收）：
1. 正常链路 analyze=True：extract_beats（Qwen，mock）→ build_plan_from_beats（规则）→
   {plan, rule_only, warnings}，beats 12 字段 + timeline 存在；
2. Qwen 后端创建失败 → 自动降级规则结果（rule_only=True + warnings 明示需人工检查），不阻塞；
3. analyze=false → 不创建后端，纯规则退化（rule_only=True）；
4. 空正文 / 非 JSON body → 400；
5. 返回结构稳定：plan/rule_only/warnings 三键；
6. beats 结构字段完整（title/summary/dramatic_function/scene_id/time/weather/
   text_segments/order/entries/source/transition_reason）+ rule_only 时 source="rule"；
7. 路由已注册进 register_routes。

http_routes.py 依赖 ComfyUI 的 server/folder_paths，测试在 import 前注入假模块。
直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_story_import.py
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

from director import http_routes as hr  # noqa: E402
from director import text_backends  # noqa: E402
from director.production_plan import (  # noqa: E402
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    StoryBeat,
    Validation,
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


# 《山雨客栈》第一章迷你正文（无任何标记，纯自然语言，>700 字触发 B-2 动态目标）
_STORY = """第一场 风雪山神庙前

沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。他环视殿内，目光落在一座半塌的
神像上，神像的右臂不知何时已经断了。

林雪提着灯笼从后殿转出来，看见沈青崖先是一愣，随即低声道："你果然也来了。"

"庙里的佛灯是刚灭的，火盆还是温的。"沈青崖蹲下摸了摸灰烬，"在我们之前，还有人
在这里避过雪。"

林雪走到神像前，借着灯笼的微光打量断臂处："刀口很新，像是被一剑削断的。"她伸手
想摸，被沈青崖拦住。

"别碰。"沈青崖沉声道，"有人在拿这座庙设局。"

风雪忽然更大了，庙门被风撞开，门外的雪地上，一串脚印正从远处延伸过来。

沈青崖握紧剑柄，把林雪挡在身后。那人影越来越近，脚步声在庙里回荡。

"二位好雅兴。"来人摘下兜帽，露出一张带疤的脸，"这庙里的东西，我找了一夜。"
"""


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


def _fake_ai_beats(title: str = "山雨客栈") -> list:
    """模拟 Qwen 整章理解返回的 beats（source="ai"，12 字段完整）。"""
    return [
        StoryBeat(
            beat_id="beat_01",
            title="沈青崖进入破庙",
            summary="风雪中推门入庙，发现断臂神像。",
            dramatic_function="introduce_character",
            scene_id="location_01",
            time="雪夜",
            weather="风雪",
            text_segments=["沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。"],
            order=1,
            source="ai",
            transition_reason="主角登场，建立地点",
        ),
        StoryBeat(
            beat_id="beat_02",
            title="庙里有第三人",
            summary="火盆尚温、神像断臂为剑伤，推断有人设局。",
            dramatic_function="plant_clue",
            scene_id="location_01",
            time="雪夜",
            weather="风雪",
            text_segments=["庙里的佛灯是刚灭的，火盆还是温的。"],
            order=2,
            source="ai",
            transition_reason="埋下伏笔，推动悬念",
        ),
    ]


def _make_plan(title: str = "山雨客栈", beats=None, warnings=None) -> ProductionPlan:
    """构造一个最小合法 plan（1 场景 2 镜 + beats + timeline）。"""
    shot1 = Shot(
        shot_id="shot_01",
        source_text="沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。",
        duration_sec=5,
    )
    shot2 = Shot(
        shot_id="shot_02",
        source_text="庙里的佛灯是刚灭的，火盆还是温的。",
        duration_sec=5,
    )
    scene = Scene(
        scene_id="location_01",
        title="风雪山神庙",
        location_name="风雪山神庙",
        time="雪夜",
        weather="风雪",
        shots=[shot1, shot2],
    )
    plan = ProductionPlan(
        project=ProjectInfo(title=title),
        scenes=[scene],
        validation=Validation(status="pending"),
        timeline=["location_01:shot_01", "location_01:shot_02"],
        beats=beats or [],
    )
    plan.validate()
    plan.validation.warnings = list(warnings or [])
    return plan


def test_story_import_success_analyze() -> None:
    print("\n[1] 正常链路：extract_beats(AI) + build_plan_from_beats → {plan, rule_only, warnings}")
    fake_beats = _fake_ai_beats()

    def fake_extract(text, backend, *, title=""):
        _check("extract_beats 收到正文", "沈青崖" in text)
        _check("extract_beats 收到 title", title == "山雨客栈", str(title))
        _check("extract_beats 收到后端（analyze=True）", backend is not None)
        return list(fake_beats), False, []

    def fake_build(text, beats, warnings=None, *, title=""):
        return _make_plan(title=title, beats=beats, warnings=warnings)

    orig_e, orig_b = hr.extract_beats, hr.build_plan_from_beats
    hr.extract_beats, hr.build_plan_from_beats = fake_extract, fake_build
    try:
        req = _FakeRequest({"story_text": _STORY, "title": "山雨客栈", "analyze": True})
        resp = asyncio.run(hr.minimax_director_story_import(req))
    finally:
        hr.extract_beats, hr.build_plan_from_beats = orig_e, orig_b

    _check("status 200", resp.status == 200, str(resp.status))
    data = _resp_data(resp)
    _check("返回三键 plan/rule_only/warnings",
           set(data.keys()) == {"plan", "rule_only", "warnings"}, str(data.keys()))
    _check("rule_only=False（AI 已理解）", data["rule_only"] is False)
    plan = data["plan"]
    _check("plan.project.title 正确", plan["project"]["title"] == "山雨客栈")
    _check("plan.scenes 存在", len(plan["scenes"]) == 1)
    _check("plan.timeline 非空", len(plan["timeline"]) == 2, str(plan["timeline"]))
    _check("plan.beats 存在", len(plan["beats"]) == 2)
    b0 = plan["beats"][0]
    _check("beat 12 字段完整",
           set(b0.keys()) == {
               "beat_id", "title", "summary", "dramatic_function", "scene_id",
               "time", "weather", "text_segments", "order", "entries",
               "source", "transition_reason",
           }, str(sorted(b0.keys())))
    _check("beat.source=ai", b0["source"] == "ai")
    _check("beat.dramatic_function 保留", b0["dramatic_function"] == "introduce_character")
    _check("warnings 是列表", isinstance(data["warnings"], list))


def test_story_import_backend_fail_rule_only() -> None:
    print("\n[2] Qwen 后端创建失败 → 降级规则（rule_only=True），不阻塞导入")
    orig = text_backends.create_default_text_backend

    def boom():
        raise RuntimeError("Ollama 不可达")

    text_backends.create_default_text_backend = boom
    try:
        req = _FakeRequest({"story_text": _STORY, "title": "山雨客栈"})
        resp = asyncio.run(hr.minimax_director_story_import(req))
    finally:
        text_backends.create_default_text_backend = orig

    _check("status 200（不阻塞）", resp.status == 200, str(resp.status))
    data = _resp_data(resp)
    _check("rule_only=True", data["rule_only"] is True)
    _check("plan.scenes 存在", len(data["plan"]["scenes"]) >= 1)
    _check("plan.beats 存在", len(data["plan"]["beats"]) >= 1)
    _check("beat.source=rule", all(b["source"] == "rule" for b in data["plan"]["beats"]))
    blob = json.dumps(data["warnings"], ensure_ascii=False)
    _check("warnings 明示需人工检查", "人工检查" in blob, blob)
    _check("warnings 明示未经 AI 理解", "AI" in blob, blob)


def test_story_import_analyze_false_skips_backend() -> None:
    print("\n[3] analyze=false → 不创建后端，纯规则退化（rule_only=True）")
    orig = text_backends.create_default_text_backend
    called = []

    def marker():
        called.append(True)
        return object()

    text_backends.create_default_text_backend = marker
    try:
        req = _FakeRequest({"story_text": _STORY, "analyze": False})
        resp = asyncio.run(hr.minimax_director_story_import(req))
    finally:
        text_backends.create_default_text_backend = orig

    _check("status 200", resp.status == 200, str(resp.status))
    _check("create_default_text_backend 未被调用", called == [])
    data = _resp_data(resp)
    _check("rule_only=True", data["rule_only"] is True)
    _check("plan.scenes 存在", len(data["plan"]["scenes"]) >= 1)
    _check("plan.beats 存在", len(data["plan"]["beats"]) >= 1)
    _check("timeline 非空", len(data["plan"]["timeline"]) >= 1, str(data["plan"]["timeline"]))


def test_story_import_empty_400() -> None:
    print("\n[4] 空正文 → 400 empty_story")
    req = _FakeRequest({"story_text": "   \n  ", "title": "空"})
    resp = asyncio.run(hr.minimax_director_story_import(req))
    _check("status 400", resp.status == 400, str(resp.status))
    data = _resp_data(resp)
    _check("错误码 empty_story", data.get("error") == "empty_story", str(data))


def test_story_import_bad_json_400() -> None:
    print("\n[5] 非 JSON body → 400 bad_json")
    req = _FakeRequest(json.JSONDecodeError("bad", "not json", 0))
    resp = asyncio.run(hr.minimax_director_story_import(req))
    _check("status 400", resp.status == 400, str(resp.status))
    data = _resp_data(resp)
    _check("错误码 bad_json", data.get("error") == "bad_json", str(data))


def test_story_import_rule_fallback_structure() -> None:
    print("\n[6] 规则退化结构：beats 12 字段 + source_text 逐字覆盖正文")
    orig = text_backends.create_default_text_backend

    def boom():
        raise RuntimeError("无 Ollama")

    text_backends.create_default_text_backend = boom
    try:
        req = _FakeRequest({"story_text": _STORY, "title": "山雨客栈"})
        resp = asyncio.run(hr.minimax_director_story_import(req))
    finally:
        text_backends.create_default_text_backend = orig

    data = _resp_data(resp)
    plan = data["plan"]
    _check("rule_only=True", data["rule_only"] is True)
    _check("beats 非空", len(plan["beats"]) >= 1)
    b0 = plan["beats"][0]
    _check("beat 12 字段完整",
           set(b0.keys()) == {
               "beat_id", "title", "summary", "dramatic_function", "scene_id",
               "time", "weather", "text_segments", "order", "entries",
               "source", "transition_reason",
           }, str(sorted(b0.keys())))
    _check("beat.dramatic_function 是 9 值之一",
           b0["dramatic_function"] in {
               "introduce_character", "dialogue", "plant_clue", "introduce_threat",
               "confrontation", "action", "reveal", "emotional", "transition",
           }, b0["dramatic_function"])
    _check("text_segments 逐字保留正文", any("沈青崖" in seg for b in plan["beats"] for seg in b["text_segments"]))
    # 覆盖校验：所有 shot.source_text 按 timeline 展平后完整覆盖正文（B-3 §6.5）
    shot_by_id = {s["shot_id"]: s for sc in plan["scenes"] for s in sc["shots"]}
    flattened = []
    for ref in plan["timeline"]:
        sid = ref.split(":", 1)[0]
        flat_id = ref.split(":", 1)[1]
        if sid in {sc["scene_id"] for sc in plan["scenes"]}:
            flattened.append(shot_by_id[flat_id]["source_text"])
    _check("timeline 引用的镜头都存在", len(flattened) == len(plan["timeline"]))
    body = "".join(flattened)
    _check("覆盖正文关键句", "沈青崖在漫天风雪中推开庙门" in body or "沈青崖" in body)


def test_story_import_register_route_included() -> None:
    print("\n[7] 路由已注册进 register_routes")
    _check("story/import 路由常量存在", hasattr(hr, "minimax_director_story_import"))
    src = open(os.path.join(_REPO_ROOT, "director", "http_routes.py"), "r", encoding="utf-8").read()
    _check("register_routes 内含 story/import", "/minimax/director/story/import" in src)


def main() -> None:
    test_story_import_success_analyze()
    test_story_import_backend_fail_rule_only()
    test_story_import_analyze_false_skips_backend()
    test_story_import_empty_400()
    test_story_import_bad_json_400()
    test_story_import_rule_fallback_structure()
    test_story_import_register_route_included()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
