#!/usr/bin/env python3
"""V1.7 Phase 2：asset_matcher 资产匹配单元测试（纯规则，mock 资产库，不碰网络/GPU）。

覆盖（V17_PLAN §13 / Phase 2 独立验收）：
1. scan_asset_library：临时目录图片筛选 + image_file 相对路径规约；
2. collect_plan_entities：角色/道具/地点三类实体提取 + 去重保序；
3. match_one 四级链路：精确 1.0 auto → 归一后精确 0.95 auto → 子串包含 0.8 pending → 无候选 none；
4. match_plan 全链路：assets + matches 结构、matched 只引用已有资产；
5. ⛔ AI 不创建「林雪_2」：matched 的 asset_name 只能来自资产库；
6. 路由 /minimax/director/assets/scan：bad plan / bad json → 400，正常 → {assets, matches}。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_asset_matcher.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
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

import director.asset_matcher as am  # noqa: E402
from director.http_routes import minimax_director_assets_scan  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    ProjectInfo,
    Prop,
    Scene,
    Shot,
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


# ---- 测试资产库（模拟 minimax_studio/assets/ 平铺文件）----
LIB = [
    {"name": "林雪", "image_file": "minimax_studio/assets/林雪.png"},
    {"name": "陈默", "image_file": "minimax_studio/assets/陈默.png"},
    {"name": "地铁站台", "image_file": "minimax_studio/assets/地铁站台.png"},
    {"name": "角色_XILNAR_主视角", "image_file": "minimax_studio/assets/角色_XILNAR_主视角.png"},
    {"name": "客栈大堂", "image_file": "minimax_studio/assets/客栈大堂.png"},
]


def _make_plan() -> ProductionPlan:
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[
            Scene(
                scene_id="scene_01",
                title="清晨 · 客栈大堂",
                location_name="客栈大堂",
                time="清晨",
                weather="雨",
                shots=[
                    Shot(
                        shot_id="shot_01",
                        source_text="林雪推开客栈大门。",
                        duration_sec=5,
                        characters=[Character(name="林雪", role="青衫女侠")],
                        props=[Prop(name="油纸伞")],
                    ),
                    Shot(
                        shot_id="shot_02",
                        source_text="陈默抬眼看向林雪。",
                        duration_sec=5,
                        characters=[Character(name="林雪"), Character(name="陈默")],
                        props=[],
                    ),
                ],
            ),
        ],
        validation=Validation(),
    )


def _qwen_plan() -> ProductionPlan:
    """带 Qwen 实体（含垃圾/推断/低置信）的 plan。"""
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
            time="黄昏", weather="雨",
            shots=[Shot(
                shot_id="shot_01", source_text="原文", duration_sec=5,
                characters=[Character(name="沈青崖")],
                entities=[
                    Entity(name="沈青崖", type=EntityType.CHARACTER,
                           source=EntitySource.SCRIPT, confidence=0.9),
                    Entity(name="镜头一", type=EntityType.CHARACTER,
                           source=EntitySource.SCRIPT, confidence=0.8),
                    Entity(name="青衫", type=EntityType.PROP,
                           source=EntitySource.SCRIPT, confidence=0.7),
                    Entity(name="山雨楼", type=EntityType.PROP,
                           source=EntitySource.SCRIPT, confidence=0.6),
                    Entity(name="雨雾", type=EntityType.EFFECT,
                           source=EntitySource.INFERRED, confidence=0.5),
                ],
            )],
        )],
        validation=Validation(),
    )


LIB2 = [
    {"name": "沈青崖", "image_file": "minimax_studio/assets/沈青崖.png"},
    {"name": "山雨楼外", "image_file": "minimax_studio/assets/山雨楼外.png"},
    {"name": "青衫", "image_file": "minimax_studio/assets/青衫.png"},
]


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _resp_data(resp):
    return asyncio.run(resp.json())


# ---- 1. 归一化 ----
def test_norm_asset_name() -> None:
    print("\n[1] _norm_asset_name 前后缀清洗")
    _check("角色_XILNAR_主视角 → XILNAR", am._norm_asset_name("角色_XILNAR_主视角") == "XILNAR")
    _check("设定卡_XILNAR → XILNAR", am._norm_asset_name("设定卡_XILNAR") == "XILNAR")
    _check("林雪图 → 林雪", am._norm_asset_name("林雪图") == "林雪")
    _check("无前后缀原样", am._norm_asset_name("林雪") == "林雪")
    _check("空串安全", am._norm_asset_name("  ") == "")


# ---- 2. 扫描 ----
def test_scan_asset_library() -> None:
    print("\n[2] scan_asset_library：图片筛选 + image_file 规约")
    with tempfile.TemporaryDirectory() as td:
        for fn in ("林雪.png", "地铁站台.jpg", "说明.txt", "剪影.mp4", "风景.webp"):
            with open(os.path.join(td, fn), "w", encoding="utf-8") as f:
                f.write("x")
        lib = am.scan_asset_library(td)
        names = sorted(a["name"] for a in lib)
        _check("只收图片（.png/.jpg/.webp），排除 .txt/.mp4", names == ["地铁站台", "林雪", "风景"])
        _check("image_file 是 input 相对路径", any(
            a["image_file"] == "minimax_studio/assets/林雪.png" for a in lib))
    _check("目录不存在返回空", am.scan_asset_library("/nonexistent/xyz") == [])


# ---- 3. 实体提取 ----
def test_collect_plan_entities() -> None:
    print("\n[3] collect_plan_entities：三类实体 + 去重保序")
    ents = am.collect_plan_entities(_make_plan())
    _check("4 个实体（林雪×2 去重 → 1）", len(ents) == 4, str(ents))
    kinds = [(e["kind"], e["name"]) for e in ents]
    _check("location 地点", ("location", "客栈大堂") in kinds)
    _check("cast 角色", ("cast", "林雪") in kinds and ("cast", "陈默") in kinds)
    _check("prop 道具", ("prop", "油纸伞") in kinds)
    _check("林雪只出现一次", kinds.count(("cast", "林雪")) == 1)


# ---- 4. 匹配四级链路 ----
def test_match_exact_auto() -> None:
    print("\n[4] match_one：精确全等 → auto 1.0")
    m = am.match_one("林雪", LIB)
    _check("status=auto", m["status"] == "auto", m["status"])
    _check("matched=True", m["matched"] is True)
    _check("confidence=1.0", m["confidence"] == 1.0, str(m["confidence"]))
    _check("asset_name=林雪", m["asset_name"] == "林雪")
    _check("image_file 正确", m["image_file"] == "minimax_studio/assets/林雪.png")


def test_match_normalized_exact_auto() -> None:
    print("\n[5] match_one：归一后精确 → auto 0.95（资产文件带设定卡前后缀也能命中）")
    m = am.match_one("XILNAR", LIB)
    _check("status=auto", m["status"] == "auto", m["status"])
    _check("asset_name 命中带前缀文件", m["asset_name"] == "角色_XILNAR_主视角")
    _check("confidence=0.95", m["confidence"] == 0.95, str(m["confidence"]))
    _check("matched=True", m["matched"] is True)


def test_match_substring_pending() -> None:
    print("\n[6] match_one：子串包含 → pending 0.8（人工确认候选）")
    m = am.match_one("站台", LIB)
    _check("status=pending（非 auto）", m["status"] == "pending", m["status"])
    _check("matched=False（待确认）", m["matched"] is False)
    _check("confidence=0.8", m["confidence"] == 0.8, str(m["confidence"]))
    _check("候选含地铁站台", any(c["name"] == "地铁站台" for c in m["candidates"]))
    _check("候选带 score", all("score" in c for c in m["candidates"]))


def test_match_none() -> None:
    print("\n[7] match_one：无候选 → none")
    m = am.match_one("不存在的角色", LIB)
    _check("status=none", m["status"] == "none", m["status"])
    _check("matched=False", m["matched"] is False)
    _check("asset_name=None", m["asset_name"] is None)
    _check("candidates 空", m["candidates"] == [])


# ---- 5. 全链路 + 不创建新资产 ----
def test_match_plan() -> None:
    print("\n[8] match_plan：assets + matches 全链路")
    res = am.match_plan(_make_plan(), LIB)
    _check("assets 是资产库列表", len(res["assets"]) == len(LIB))
    kinds = {m["kind"] for m in res["matches"]}
    _check("三类实体都在", kinds == {"cast", "prop", "location"}, str(kinds))
    by_name = {m["name"]: m for m in res["matches"]}
    _check("林雪 auto", by_name["林雪"]["status"] == "auto" and by_name["林雪"]["confidence"] == 1.0)
    _check("陈默 auto", by_name["陈默"]["status"] == "auto")
    _check("客栈大堂 auto（地点精确）", by_name["客栈大堂"]["status"] == "auto"
           and by_name["客栈大堂"]["image_file"] == "minimax_studio/assets/客栈大堂.png")
    _check("油纸伞 none（库中无）", by_name["油纸伞"]["status"] == "none")


def test_no_new_asset_created() -> None:
    print("\n[9] ⛔ 不创建「林雪_2」：matched 只引用资产库已有名")
    res = am.match_plan(_make_plan(), LIB)
    lib_names = {a["name"] for a in LIB}
    for m in res["matches"]:
        if m["matched"]:
            _check(f"matched 的 asset_name 在资产库内（{m['name']}→{m['asset_name']}）",
                   m["asset_name"] in lib_names, str(m))


# ---- 5b. 实体模式门控（2026-08-11 实体抽取加固核心 + P0-2 分类收紧）----
def test_match_plan_entity_mode_gates_garbage() -> None:
    print("\n[10] 实体模式：垃圾伪实体丢弃 + 推断实体不进 + 服装/未对齐地点只进 Prompt")
    res = am.match_plan(_qwen_plan(), LIB2)
    by = {m["name"]: m for m in res["matches"]}
    _check("matches 2 条（沈青崖/山雨楼外；青衫/山雨楼→none 不进匹配）",
           len(res["matches"]) == 2, str([m["name"] for m in res["matches"]]))
    _check("镜头一不进匹配（invalid 丢弃）", "镜头一" not in by)
    _check("雨雾不进匹配（inferred 排除）", "雨雾" not in by)
    _check("青衫不进匹配（costume→none 只进 Prompt）", "青衫" not in by)
    _check("山雨楼不进匹配（未精确对齐→environment）", "山雨楼" not in by)
    _check("沈青崖 cast auto（0.9≥0.85）", by["沈青崖"]["kind"] == "cast"
           and by["沈青崖"]["status"] == "auto" and by["沈青崖"]["can_auto"] is True)
    _check("山雨楼外 location auto（规则补位 1.0）", by["山雨楼外"]["kind"] == "location"
           and by["山雨楼外"]["status"] == "auto"
           and by["山雨楼外"]["entity_confidence"] == 1.0)
    _check("漏斗 invalid_dropped=1", res["funnel"]["invalid_dropped"] == 1, str(res["funnel"]))
    _check("漏斗 discovered=5 / entered=2 / visual_only=3 / excluded=0",
           res["funnel"]["discovered"] == 5 and res["funnel"]["entered"] == 2
           and res["funnel"]["visual_only"] == 3 and res["funnel"]["excluded"] == 0,
           str(res["funnel"]))
    _check("漏斗 auto=2 / pending=0 / none=0",
           res["funnel"]["auto"] == 2 and res["funnel"]["pending"] == 0
           and res["funnel"]["none"] == 0, str(res["funnel"]))
    ve_names = [v["name"] for v in res["visual_elements"]]
    _check("青衫/山雨楼/雨雾 进 visual_elements（只进 Prompt）",
           all(n in ve_names for n in ("青衫", "山雨楼", "雨雾")), str(ve_names))
    _check("正式地点山雨楼外不在 visual_elements（不重复）",
           "山雨楼外" not in ve_names, str(ve_names))


# ---- 6. 路由 ----
def test_route_bad_plan_400() -> None:
    print("\n[11] 路由：缺 scenes → 400 bad_plan")
    req = _FakeRequest({"plan": {"project": {"title": "x"}}})
    resp = asyncio.run(minimax_director_assets_scan(req))
    _check("status 400", resp.status == 400, str(resp.status))
    _check("错误码 bad_plan", _resp_data(resp).get("error") == "bad_plan")


def test_route_bad_json_400() -> None:
    print("\n[12] 路由：非 JSON → 400 bad_json")
    req = _FakeRequest(json.JSONDecodeError("bad", "x", 0))
    resp = asyncio.run(minimax_director_assets_scan(req))
    _check("status 400", resp.status == 400, str(resp.status))
    _check("错误码 bad_json", _resp_data(resp).get("error") == "bad_json")


def test_route_success() -> None:
    print("\n[13] 路由：正常 → {assets, matches}")
    plan_dict = _make_plan().to_dict()
    req = _FakeRequest({"plan": plan_dict})
    resp = asyncio.run(minimax_director_assets_scan(req))
    _check("status 200", resp.status == 200, str(resp.status))
    data = _resp_data(resp)
    _check("返回 assets+matches+visual_elements+funnel 四键",
           set(data.keys()) == {"assets", "matches", "visual_elements", "funnel"},
           str(data.keys()))
    _check("matches 含林雪", any(m["name"] == "林雪" and m["kind"] == "cast" for m in data["matches"]))
    _check("matches 透出 entity_type/entity_confidence",
           any(m["name"] == "林雪" and m.get("entity_type") == "character"
               and m.get("entity_confidence") == 0.95 for m in data["matches"]))
    _check("matches 透出 asset_requirement/match_kind",
           any(m["name"] == "林雪" and m.get("asset_requirement") == "required"
               and "match_kind" in m and "suggest" in m for m in data["matches"]))
    _check("funnel 结构齐全", set(data["funnel"].keys()) == {
        "discovered", "invalid_dropped", "boundary_dropped", "dedup_dropped",
        "seeded", "cleansed", "entered", "visual_only", "excluded",
        "auto", "pending", "none"},
        str(data["funnel"].keys()))


def test_register_route_included() -> None:
    print("\n[14] 路由已注册进 register_routes")
    src = open(os.path.join(_REPO_ROOT, "director", "http_routes.py"), "r", encoding="utf-8").read()
    _check("register_routes 内含 assets/scan", "/minimax/director/assets/scan" in src)


def main() -> None:
    test_norm_asset_name()
    test_scan_asset_library()
    test_collect_plan_entities()
    test_match_exact_auto()
    test_match_normalized_exact_auto()
    test_match_substring_pending()
    test_match_none()
    test_match_plan()
    test_no_new_asset_created()
    test_match_plan_entity_mode_gates_garbage()
    test_route_bad_plan_400()
    test_route_bad_json_400()
    test_route_success()
    test_register_route_included()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
