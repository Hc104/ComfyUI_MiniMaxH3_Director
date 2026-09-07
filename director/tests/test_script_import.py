#!/usr/bin/env python3
"""V1.7 Phase 1 · Commit 3：script/import 路由单元测试（mock，不连 Ollama、不碰 GPU）。

覆盖（V17_PLAN §13 / Phase 1 Commit 3 验收）：
1. POST /minimax/director/script/import 正常链路：规则拆 → analyze_script 补全 → {plan, rule_only, warnings}；
2. Qwen 补全失败 → 自动降级规则结果（rule_only=True），不阻塞导入；
3. analyze=false → 不调 analyze_script，只返回规则 plan；
4. 空剧本 / 非 JSON body → 400；
5. 返回结构稳定：plan/rule_only/warnings 三键；
6. 结构字段：shot_id/source_text/duration_sec 由规则决定（Qwen 不决定镜头结构）。

http_routes.py 依赖 ComfyUI 的 server/folder_paths，测试在 import 前注入假模块。
直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_script_import.py
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
from director.production_plan import Character  # noqa: E402

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


# 《山雨客栈》迷你剧本（规则拆 1 场景 2 镜）
_SIMPLE_SCRIPT = """# 山雨客栈

## 第一场：清晨 · 客栈大堂

地点：山雨客栈大堂
时间：清晨
天气：雨

林雪（青衫女侠）推开客栈大门，收伞，环视堂内。
陈默从柜台后抬眼看向林雪，轻声说话。
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


def _fake_analyze(plan):
    """模拟 Qwen 补全：填 visual_intent + 补 role，结构字段不动。"""
    for scene in plan.scenes:
        for shot in scene.shots:
            shot.visual_intent = "补全的视觉意图草稿"
            if not shot.characters:
                shot.characters.append(Character(name="林雪", role="青衫女侠"))
    plan.validate()
    return plan


def test_import_success_with_analyze() -> None:
    print("\n[1] 正常链路：规则拆 + analyze_script 补全 → {plan, rule_only, warnings}")
    orig = hr.analyze_script
    hr.analyze_script = _fake_analyze
    try:
        req = _FakeRequest({"script_text": _SIMPLE_SCRIPT, "title": "山雨客栈"})
        resp = asyncio.run(hr.minimax_director_script_import(req))
    finally:
        hr.analyze_script = orig

    _check("status 200", resp.status == 200, str(resp.status))
    data = _resp_data(resp)
    _check("返回三键 plan/rule_only/warnings",
           set(data.keys()) == {"plan", "rule_only", "warnings"}, str(data.keys()))
    _check("rule_only=False（Qwen 已补全）", data["rule_only"] is False)
    _check("场景存在", len(data["plan"]["scenes"]) == 1)
    _check("镜头存在", len(data["plan"]["scenes"][0]["shots"]) == 2)
    _check("visual_intent 已补全",
           all(s["visual_intent"] for s in data["plan"]["scenes"][0]["shots"]))
    _check("project.title 正确", data["plan"]["project"]["title"] == "山雨客栈")
    _check("validation.status 有效", data["plan"]["validation"]["status"] in ("valid", "invalid"))
    _check("结构字段由规则决定", all(
        s["shot_id"] and s["source_text"] and 2 <= s["duration_sec"] <= 8
        for s in data["plan"]["scenes"][0]["shots"]))


def test_import_analyze_fail_rule_only() -> None:
    print("\n[2] Qwen 补全失败 → 降级规则结果（rule_only=True），不阻塞导入")
    orig = hr.analyze_script

    def boom(plan):
        raise RuntimeError("Ollama 服务不可达")

    hr.analyze_script = boom
    try:
        req = _FakeRequest({"script_text": _SIMPLE_SCRIPT})
        resp = asyncio.run(hr.minimax_director_script_import(req))
    finally:
        hr.analyze_script = orig

    _check("status 200（不阻塞）", resp.status == 200, str(resp.status))
    data = _resp_data(resp)
    _check("rule_only=True", data["rule_only"] is True)
    _check("plan 仍是规则结构", len(data["plan"]["scenes"]) == 1
           and len(data["plan"]["scenes"][0]["shots"]) == 2)
    _check("visual_intent 空（无 Qwen 补全）",
           all(s["visual_intent"] == "" for s in data["plan"]["scenes"][0]["shots"]))
    _check("warnings 是列表", isinstance(data["warnings"], list))


def test_import_analyze_false_skips_qwen() -> None:
    print("\n[3] analyze=false → 不调 analyze_script，只返回规则 plan")
    orig = hr.analyze_script
    called = []

    def marker(plan):
        called.append(True)
        return plan

    hr.analyze_script = marker
    try:
        req = _FakeRequest({"script_text": _SIMPLE_SCRIPT, "analyze": False})
        resp = asyncio.run(hr.minimax_director_script_import(req))
    finally:
        hr.analyze_script = orig

    _check("status 200", resp.status == 200, str(resp.status))
    _check("analyze_script 未被调用", called == [])
    data = _resp_data(resp)
    _check("rule_only=False", data["rule_only"] is False)
    _check("规则结构完整", len(data["plan"]["scenes"][0]["shots"]) == 2)


def test_import_empty_script_400() -> None:
    print("\n[4] 空剧本 → 400")
    req = _FakeRequest({"script_text": "   \n  ", "title": "空"})
    resp = asyncio.run(hr.minimax_director_script_import(req))
    _check("status 400", resp.status == 400, str(resp.status))
    data = _resp_data(resp)
    _check("错误码 empty_script", data.get("error") == "empty_script", str(data))


def test_import_bad_json_400() -> None:
    print("\n[5] 非 JSON body → 400")
    req = _FakeRequest(json.JSONDecodeError("bad", "not json", 0))
    resp = asyncio.run(hr.minimax_director_script_import(req))
    _check("status 400", resp.status == 400, str(resp.status))
    data = _resp_data(resp)
    _check("错误码 bad_json", data.get("error") == "bad_json", str(data))


def test_import_structure_aligned_rule() -> None:
    print("\n[6] 结构由规则决定：source_text 逐字保留、duration 合法、无 asset 字段")
    orig = hr.analyze_script
    hr.analyze_script = _fake_analyze
    try:
        req = _FakeRequest({"script_text": _SIMPLE_SCRIPT})
        resp = asyncio.run(hr.minimax_director_script_import(req))
    finally:
        hr.analyze_script = orig
    data = _resp_data(resp)
    blob = json.dumps(data["plan"], ensure_ascii=False)
    forbidden = ("castIds", "locationId", "assetId", "generationMode", "h3Prompt", "camera", "refs")
    _check("无 Phase 2/3/4 字段", not any(k in blob for k in forbidden))
    shot = data["plan"]["scenes"][0]["shots"][0]
    _check("source_text 保留原文", "推开客栈大门" in shot["source_text"])
    _check("characters 有 name", all(c["name"] for c in shot["characters"]))


def test_register_route_included() -> None:
    print("\n[7] 路由已注册进 register_routes")
    _check("script/import 路由常量存在", hasattr(hr, "minimax_director_script_import"))
    src = open(os.path.join(_REPO_ROOT, "director", "http_routes.py"), "r", encoding="utf-8").read()
    _check("register_routes 内含 script/import", "/minimax/director/script/import" in src)


def main() -> None:
    test_import_success_with_analyze()
    test_import_analyze_fail_rule_only()
    test_import_analyze_false_skips_qwen()
    test_import_empty_script_400()
    test_import_bad_json_400()
    test_import_structure_aligned_rule()
    test_register_route_included()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
