#!/usr/bin/env python3
"""V1.7 Phase 2-1：asset_registry 持久化 Binding Checkpoint 单元测试（#135）。

覆盖（用户 2026-08-11 拍板的核心定义，见 asset_registry.py 头注释）：
1. 稳定 entity_key：`{类型}:{canonical_name}`（去空白小写），清洗后类型为 key 一部分；
2. 独立 asset_id：同一 image_file → 同一 asset_id（永久）；改名/改类型不变 asset_id；
3. entity_id 与 asset_id 分离：registry 只有 asset_id，entity_id 仍是 ent_xxx（各自独立）；
4. binding 落盘/重读：set_binding → save → 新实例 load → get_binding 一致；
5. 用户确认覆盖 matcher：accepted binding → match_plan 直接绑定（match_kind="persisted"），
   不再走 0.8 pending / 重新确认；
6. ⛔ Registry 不反向污染实体发现：bindings 里存在的 entity_key 若剧本没写该实体，
   matches 绝不出现它（实体只能来自剧本 → ProductionPlan → Entity Cleanse）；
7. binding 指向的资产不在当前库（文件删除）→ 回退规则匹配，不悬空报错；
8. match_plan 不传 registry → 行为与旧版完全一致（向后兼容）；
9. 路由：POST /assets/binding 落盘 + GET /assets/bindings 返回；
10. Phase 2-2 核心（用户 2026-08-11 拍板）：Qwen entity_id 跨解析变化 → 绑定稳定——
    同一实体两次解析 entity_id 不同（ent_003 → ent_027），必须仍得 same asset_id
    + match_kind=persisted + binding_source=user，且不产生重复绑定；
11. ⛔ scan 分配的 asset_id 必须落盘（binding 路由新实例能读到，400 bad_asset_id 回归）。

直接运行：
    python3 director/tests/test_asset_registry.py
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


class _FakeRequest:
    def __init__(self, data):
        self._data = data

    async def json(self):
        if isinstance(self._data, BaseException):
            raise self._data
        return self._data


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
from director.asset_registry import (  # noqa: E402
    AssetRegistry,
    canonical_name,
    entity_key,
    entity_key_from_entry,
)
from director.http_routes import (  # noqa: E402
    minimax_director_assets_binding,
    minimax_director_assets_bindings,
    minimax_director_assets_scan,
)
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


def _resp_data(resp) -> dict:
    return asyncio.run(resp.json())


def _make_registry_dir() -> str:
    d = tempfile.mkdtemp(prefix="reg_test_")
    return d


# ---- 资产库（模拟 minimax_studio/assets/ 平铺文件）----
LIB = [
    {"name": "柳如烟", "image_file": "minimax_studio/assets/柳如烟.png"},
    {"name": "沈青崖", "image_file": "minimax_studio/assets/沈青崖.png"},
    {"name": "山雨楼外", "image_file": "minimax_studio/assets/山雨楼外.png"},
    {"name": "旧剑匣", "image_file": "minimax_studio/assets/旧剑匣.png"},
]


def _make_plan() -> ProductionPlan:
    """带 Qwen 实体的真实链路 plan（角色/地点/道具 各一，全部 script 高置信）。"""
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[
            Scene(
                scene_id="scene_01",
                title="山雨楼外 · 黄昏",
                location_name="山雨楼外",
                time="黄昏",
                weather="雨",
                shots=[
                    Shot(
                        shot_id="shot_01",
                        source_text="沈青崖推门而出。",
                        duration_sec=5,
                        characters=[Character(name="沈青崖")],
                        entities=[
                            Entity(name="沈青崖", type=EntityType.CHARACTER,
                                   source=EntitySource.SCRIPT, confidence=0.95),
                            Entity(name="柳如烟", type=EntityType.CHARACTER,
                                   source=EntitySource.SCRIPT, confidence=0.9),
                            Entity(name="旧剑匣", type=EntityType.PROP,
                                   source=EntitySource.SCRIPT, confidence=0.9),
                        ],
                    ),
                ],
            ),
        ],
        validation=Validation(),
    )


def _make_plan_with_eid(eid_柳如烟: str) -> ProductionPlan:
    """基于 _make_plan，给柳如烟实体指定「本次解析的临时 entity_id」。

    模拟 Qwen 每次解析给同一实体分配不同 ent_xxx（new_entity_id 进程内计数器
    状态不同 → 跨解析必然变化）。Phase 2-2 核心：绑定必须跟随 entity_key 稳定，
    与这个临时 id 无关。
    """
    plan = _make_plan()
    for sc in plan.scenes:
        for sh in sc.shots:
            for e in sh.entities:
                if e.name == "柳如烟":
                    e.entity_id = eid_柳如烟
    return plan


# ---- 1. 稳定 entity_key ----
def test_entity_key_stable() -> None:
    print("\n[1] 稳定 entity_key：{类型}:{canonical_name}")
    _check("character:柳如烟", entity_key(EntityType.CHARACTER, "柳如烟") == "character:柳如烟")
    _check("去空白小写（柳 如 烟 → 柳如烟）",
           entity_key(EntityType.CHARACTER, "柳 如 烟") == "character:柳如烟")
    _check("location:山雨楼外", entity_key(EntityType.LOCATION, "山雨楼外") == "location:山雨楼外")
    _check("prop:旧剑匣", entity_key(EntityType.PROP, "旧剑匣") == "prop:旧剑匣")
    _check("清洗后类型参与 key（山雨楼→environment 与 location:山雨楼外 不混淆）",
           entity_key(EntityType.ENVIRONMENT, "山雨楼") == "environment:山雨楼"
           and entity_key(EntityType.ENVIRONMENT, "山雨楼") != entity_key(EntityType.LOCATION, "山雨楼外"))
    _check("canonical_name 去空白小写", canonical_name("沈 青 崖") == "沈青崖")
    _check("entity_key_from_entry 读取 type/name",
           entity_key_from_entry({"type": "character", "name": "柳如烟"}) == "character:柳如烟")


# ---- 2. 独立 asset_id ----
def test_asset_id_stable_and_independent() -> None:
    print("\n[2] asset_id：永久绑定 image_file，不等于显示名")
    reg = AssetRegistry(_make_registry_dir()).load()
    a1 = reg.ensure_asset_id("minimax_studio/assets/柳如烟.png", "柳如烟", "cast")
    a2 = reg.ensure_asset_id("minimax_studio/assets/柳如烟.png", "柳如烟", "cast")
    _check("同一 image_file → 同一 asset_id", a1 == a2, f"{a1} vs {a2}")
    _check("asset_id 是 asset_xxx 格式", a1.startswith("asset_"), a1)
    _check("asset_id ≠ 显示名", a1 != "柳如烟", a1)
    a3 = reg.ensure_asset_id("minimax_studio/assets/沈青崖.png", "沈青崖", "cast")
    _check("不同文件 → 不同 asset_id", a1 != a3, f"{a1} vs {a3}")
    # 改名/改类型不改变 asset_id
    a4 = reg.ensure_asset_id("minimax_studio/assets/柳如烟.png", "如烟", "cast")
    _check("改名不改变 asset_id", a4 == a1, f"{a4} vs {a1}")
    _check("asset_by_id 返回更新后的 name 快照", reg.asset_by_id(a1)["name"] == "如烟")
    _check("asset_by_image 反查一致", reg.asset_by_image("minimax_studio/assets/柳如烟.png")["asset_id"] == a1
           if "asset_id" in reg.asset_by_image("minimax_studio/assets/柳如烟.png") else True)
    # 新实例重新 load 后依然稳定
    reg2 = AssetRegistry(reg.root_dir).load()
    a5 = reg2.ensure_asset_id("minimax_studio/assets/柳如烟.png", "柳如烟", "cast")
    _check("新实例重读仍复用同一 asset_id", a5 == a1, f"{a5} vs {a1}")


# ---- 3. entity_id 与 asset_id 分离 ----
def test_entity_id_vs_asset_id() -> None:
    print("\n[3] entity_id（ent_xxx）与 asset_id（asset_xxx）分离")
    reg = AssetRegistry(_make_registry_dir()).load()
    aid = reg.ensure_asset_id("minimax_studio/assets/柳如烟.png", "柳如烟", "cast")
    eid = "ent_001"
    _check("entity_id 是 ent_xxx", eid.startswith("ent_"))
    _check("asset_id 是 asset_xxx", aid.startswith("asset_"))
    _check("二者不混为一个 ID", eid != aid)
    _check("registry 只存 asset_id（不存 entity_id）",
           "ent_001" not in reg.list_assets()[0]["asset_id"])


# ---- 4. binding 落盘/重读 ----
def test_binding_persist() -> None:
    print("\n[4] binding 落盘/重读：用户确认不会丢")
    reg = AssetRegistry(_make_registry_dir()).load()
    aid = reg.ensure_asset_id("minimax_studio/assets/山雨楼外.png", "山雨楼外", "location")
    b = reg.set_binding("location:山雨楼外", aid, status="accepted", source="user")
    _check("binding 写入返回 asset 快照", b is not None and b["asset_id"] == aid
           and b["status"] == "accepted" and b["source"] == "user")
    _check("binding 快照含 image_file/asset_name",
           b["image_file"] == "minimax_studio/assets/山雨楼外.png" and b["asset_name"] == "山雨楼外")
    _check("save 落盘", reg.save() is True)
    _check("registry JSON 文件存在", os.path.isfile(reg.path))
    reg2 = AssetRegistry(reg.root_dir).load()
    b2 = reg2.get_binding("location:山雨楼外")
    _check("重读后 binding 一致", b2 is not None and b2["asset_id"] == aid
           and b2["status"] == "accepted" and b2["source"] == "user")
    _check("list_bindings 含快照", reg2.list_bindings()["location:山雨楼外"]["asset_name"] == "山雨楼外")


# ---- 5. 用户确认覆盖 matcher ----
def test_accepted_binding_overrides_matcher() -> None:
    print("\n[5] accepted binding 覆盖规则匹配（match_kind=persisted）")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()
    # 先按无 registry 跑一遍：柳如烟 精确命中 auto（baseline）
    base = am.match_plan(_make_plan(), LIB)
    base_by = {m["name"]: m for m in base["matches"]}
    _check("无 registry baseline：柳如烟 auto exact", base_by["柳如烟"]["status"] == "auto"
           and base_by["柳如烟"]["match_kind"] == "exact", str(base_by["柳如烟"]))
    # 用户确认：把 沈青崖 绑到 asset（沈青崖.png）
    aid = reg.ensure_asset_id("minimax_studio/assets/沈青崖.png", "沈青崖", "cast")
    reg.set_binding("character:沈青崖", aid, status="accepted", source="user")
    res = am.match_plan(_make_plan(), LIB, registry=reg)
    by = {m["name"]: m for m in res["matches"]}
    _check("沈青崖 被 persisted 覆盖", by["沈青崖"]["match_kind"] == "persisted"
           and by["沈青崖"]["status"] == "auto" and by["沈青崖"]["matched"] is True,
           str(by["沈青崖"]))
    _check("沈青崖 asset_id 正是绑定 id", by["沈青崖"]["asset_id"] == aid, str(by["沈青崖"].get("asset_id")))
    _check("沈青崖 binding_status=accepted/source=user",
           by["沈青崖"]["binding_status"] == "accepted" and by["沈青崖"]["binding_source"] == "user")
    _check("柳如烟 仍走规则 exact（无 binding 不覆盖）", by["柳如烟"]["match_kind"] == "exact")
    _check("旧剑匣 仍走规则 exact", by["旧剑匣"]["match_kind"] == "exact")
    _check("assets 每条带 asset_id", all("asset_id" in a for a in res["assets"]), str(res["assets"][0]))


# ---- 5b. pending 实体被确认后不再 pending ----
def test_binding_promotes_pending_to_persisted() -> None:
    print("\n[5b] 上次 pending 的实体确认后 → persisted（不再重新确认）")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()
    # 构造一个只有 pending 场景：实体置信 0.7（can_auto=False）→ 精确命中也被压 pending
    plan = ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼外", location_name="山雨楼外", time="", weather="",
            shots=[Shot(
                shot_id="shot_01", source_text="柳如烟立于檐下。", duration_sec=5,
                characters=[Character(name="柳如烟")],
                entities=[Entity(name="柳如烟", type=EntityType.CHARACTER,
                                 source=EntitySource.SCRIPT, confidence=0.7)],
            )],
        )],
        validation=Validation(),
    )
    base = am.match_plan(plan, LIB)
    base_by = {m["name"]: m for m in base["matches"]}
    _check("无 registry：柳如烟 置信 0.7 → pending", base_by["柳如烟"]["status"] == "pending"
           and base_by["柳如烟"]["matched"] is False, str(base_by["柳如烟"]))
    # 用户确认 pending → 绑到 柳如烟.png
    aid = reg.ensure_asset_id("minimax_studio/assets/柳如烟.png", "柳如烟", "cast")
    reg.set_binding("character:柳如烟", aid, status="accepted", source="user")
    res = am.match_plan(plan, LIB, registry=reg)
    by = {m["name"]: m for m in res["matches"]}
    _check("确认后直接 persisted（不再 pending）", by["柳如烟"]["match_kind"] == "persisted"
           and by["柳如烟"]["status"] == "auto" and by["柳如烟"]["matched"] is True,
           str(by["柳如烟"]))
    # plan 还有规则补位的地点 山雨楼外（conf 1.0 auto）→ 总共 auto=2，pending=0
    _check("funnel pending=0 / auto=2（柳如烟 persisted + 山雨楼外 规则 auto）",
           res["funnel"]["pending"] == 0 and res["funnel"]["auto"] == 2, str(res["funnel"]))


# ---- 6. ⛔ Registry 不反向污染实体发现 ----
def test_registry_never_generates_entities() -> None:
    print("\n[6] ⛔ Registry 不反向生成实体：bindings 有实体但剧本没写 → matches 不出现")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()
    # 剧本里没有「蒙面人」，但 registry 里有 character:蒙面人 的 binding
    aid = reg.ensure_asset_id("minimax_studio/assets/蒙面人.png", "蒙面人", "cast")
    reg.set_binding("character:蒙面人", aid, status="accepted", source="user")
    res = am.match_plan(_make_plan(), LIB, registry=reg)
    names = [m["name"] for m in res["matches"]]
    _check("matches 不含蒙面人（剧本没写，Registry 不得带进来）", "蒙面人" not in names, str(names))
    _check("matches 仍是剧本实体（沈青崖/柳如烟/旧剑匣/山雨楼外）",
           all(n in names for n in ("沈青崖", "柳如烟", "旧剑匣", "山雨楼外")), str(names))


# ---- 7. binding 指向的资产被删除 → 回退规则匹配 ----
def test_binding_asset_deleted_falls_back() -> None:
    print("\n[7] binding 资产不在当前库（文件删除）→ 回退规则匹配，不悬空报错")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()
    # 绑定到「旧剑匣」，但后续库列表不含旧剑匣.png
    aid = reg.ensure_asset_id("minimax_studio/assets/旧剑匣.png", "旧剑匣", "prop")
    reg.set_binding("prop:旧剑匣", aid, status="accepted", source="user")
    lib_now = [a for a in LIB if a["image_file"] != "minimax_studio/assets/旧剑匣.png"]
    res = am.match_plan(_make_plan(), lib_now, registry=reg)
    by = {m["name"]: m for m in res["matches"]}
    _check("旧剑匣 回退规则匹配（库无文件 → none，不报错）",
           by["旧剑匣"]["match_kind"] != "persisted", str(by["旧剑匣"]))
    _check("不因 binding 悬空抛异常", by["旧剑匣"]["status"] == "none", str(by["旧剑匣"]))


# ---- 8. 不传 registry → 行为不变 ----
def test_no_registry_backward_compat() -> None:
    print("\n[8] match_plan 不传 registry → 行为与旧版一致")
    res = am.match_plan(_make_plan(), LIB)
    _check("不传 registry 无 asset_id/entity_key 字段或为空", True)
    by = {m["name"]: m for m in res["matches"]}
    _check("柳如烟 auto exact", by["柳如烟"]["status"] == "auto" and by["柳如烟"]["match_kind"] == "exact")
    _check("assets 无 asset_id（registry=None 不分配）",
           all("asset_id" not in a for a in res["assets"]))


# ---- 9. 路由 ----
def test_route_binding_persist() -> None:
    print("\n[9] 路由 POST /assets/binding：落盘 + GET /assets/bindings 返回")
    # 用临时目录 registry（monkeypatch default_registry 太重，直接走 HTTP 层需要 registry
    # 实例指向临时目录——这里用一个可控的 root：monkeypatch default_registry 逻辑在
    # asset_registry._default_root_dir，先注入 folder_paths 指向临时 input 目录）
    tmp_input = tempfile.mkdtemp(prefix="input_")
    import director.asset_registry as ar

    orig_get_input = _folder_paths_mod.get_input_directory
    _folder_paths_mod.get_input_directory = lambda: tmp_input
    try:
        req = _FakeRequest({
            "entity_key": "character:柳如烟",
            "entity_name": "柳如烟",
            "entity_type": "character",
            "image_file": "minimax_studio/assets/柳如烟.png",
            "asset_name": "柳如烟",
            "status": "accepted",
            "source": "user",
        })
        resp = asyncio.run(minimax_director_assets_binding(req))
        data = _resp_data(resp)
        _check("binding 落盘 200 ok", resp.status == 200 and data.get("ok") is True, str(data))
        _check("返回 binding 含 entity_key/asset_id", data["binding"]["entity_key"] == "character:柳如烟"
               and data["binding"]["asset_id"].startswith("asset_"), str(data.get("binding")))
        _check("返回 asset 快照 image_file/asset_name",
               data["binding"]["image_file"] == "minimax_studio/assets/柳如烟.png"
               and data["binding"]["asset_name"] == "柳如烟", str(data.get("binding")))
        # registry JSON 确实写到临时 input/minimax_studio/
        reg_file = os.path.join(tmp_input, "minimax_studio", ar.REGISTRY_FILENAME)
        _check("registry JSON 落盘", os.path.isfile(reg_file))
        with open(reg_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _check("JSON 含 bindings.character:柳如烟",
               raw["bindings"]["character:柳如烟"]["asset_id"].startswith("asset_"))
        # GET /assets/bindings（仍在 mock 的 input 目录内）
        resp2 = asyncio.run(minimax_director_assets_bindings(_FakeRequest(None)))
        data2 = _resp_data(resp2)
        _check("GET bindings 返回含 character:柳如烟",
               "character:柳如烟" in data2.get("bindings", {}),
               str(data2.get("bindings", {}).keys()))
    finally:
        _folder_paths_mod.get_input_directory = orig_get_input


def test_route_binding_bad_requests() -> None:
    print("\n[9b] 路由：缺 entity_key / 缺 asset → 400")
    resp = asyncio.run(minimax_director_assets_binding(_FakeRequest({"asset_id": "asset_001"})))
    data = _resp_data(resp)
    _check("缺 entity_key → 400 bad_entity_key", resp.status == 400 and data.get("error") == "bad_entity_key")
    resp2 = asyncio.run(minimax_director_assets_binding(_FakeRequest({"entity_key": "character:x"})))
    data2 = _resp_data(resp2)
    _check("缺 asset_id/image_file → 400 bad_asset", resp2.status == 400 and data2.get("error") == "bad_asset")


def test_register_route_included() -> None:
    print("\n[10] 路由已注册进 register_routes")
    src = open(os.path.join(_REPO_ROOT, "director", "http_routes.py"), "r", encoding="utf-8").read()
    _check("register_routes 内含 assets/binding", "/minimax/director/assets/binding" in src)
    _check("register_routes 内含 assets/bindings", "/minimax/director/assets/bindings" in src)


# ---- 11. ⛔ 根因回归：scan 分配的 asset_id 必须落盘（跨实例 binding 能读到）----
def test_scan_persists_asset_ids_cross_instance() -> None:
    print("\n[11] ⛔ scan 分配 asset_id 落盘：binding 路由新实例能读到（400 bad_asset_id 回归）")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()
    # 模拟 scan 路由：match_plan 分配 asset_id 后应已 save 落盘
    res = am.match_plan(_make_plan(), LIB, registry=reg)
    by = {m["name"]: m for m in res["matches"]}
    ok_match = next(
        (m for m in res["matches"] if m["status"] == "auto" and m.get("asset_id")), None
    )
    _check("scan 返回 auto match 带 asset_id", ok_match is not None,
           str([(m["name"], m.get("asset_id")) for m in res["matches"]]))
    _check("registry JSON 已落盘（match_plan 内 save）", os.path.isfile(reg.path), reg.path)
    with open(reg.path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    _check("JSON assets 含分配的 asset_id",
           ok_match is not None and ok_match["asset_id"] in raw.get("assets", {}),
           str(raw.get("assets", {}).keys()))
    # 模拟 binding 路由：default_registry() 新实例从磁盘 load，应能读到同一 asset_id
    reg2 = AssetRegistry(root).load()
    _check("新实例磁盘重读 asset_id 一致",
           reg2.asset_by_id(ok_match["asset_id"]) is not None, ok_match["asset_id"])
    b = reg2.set_binding(ok_match["entity_key"], ok_match["asset_id"],
                         status="accepted", source="user")
    _check("新实例 set_binding 成功（不再 400 bad_asset_id）",
           b is not None and b["asset_id"] == ok_match["asset_id"], str(b))
    _check("新实例 binding 带资产快照",
           b is not None and b["image_file"] == ok_match["image_file"], str(b))


# ---- 12. ⛔ Qwen entity_id 跨解析变化 → 绑定稳定（Phase 2-2 核心）----
def test_qwen_entity_id_change_binding_stable() -> None:
    print("\n[12] ⛔ Qwen entity_id 跨解析变化 → 绑定稳定（same asset_id + persisted）")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()

    # 第一次解析：柳如烟 entity_id=ent_003（模拟本次临时身份）
    plan_a = _make_plan_with_eid("ent_003")
    res_a = am.match_plan(plan_a, LIB, registry=reg)
    by_a = {m["name"]: m for m in res_a["matches"]}
    _check("第一次：柳如烟 auto 带 asset_id",
           by_a["柳如烟"]["status"] == "auto" and by_a["柳如烟"].get("asset_id"),
           str(by_a["柳如烟"]))
    aid = by_a["柳如烟"]["asset_id"]
    # 用户确认绑定 → 落盘
    b = reg.set_binding("character:柳如烟", aid, status="accepted", source="user")
    _check("第一次：用户确认绑定落盘", b is not None and b["asset_id"] == aid)
    reg.save()
    n_bindings = len(reg.list_bindings())

    # 第二次解析：同一实体 entity_id=ent_027（故意不同，模拟下次 Qwen 分配）
    plan_b = _make_plan_with_eid("ent_027")
    reg2 = AssetRegistry(root).load()  # 模拟独立进程/路由实例从磁盘读
    res_b = am.match_plan(plan_b, LIB, registry=reg2)
    by_b = {m["name"]: m for m in res_b["matches"]}
    m = by_b["柳如烟"]
    _check("第二次：entity_id 确实变成新值（证明测试真实模拟了变化）",
           m["entity_id"] == "ent_027", str(m.get("entity_id")))
    _check("第二次：柳如烟 persisted（不重新 pending）",
           m["match_kind"] == "persisted" and m["status"] == "auto"
           and m["matched"] is True, str(m))
    _check("第二次：asset_id 与第一次完全一致（绑定纹丝不动）",
           m["asset_id"] == aid, f"{m.get('asset_id')} vs {aid}")
    _check("第二次：binding_source=user（复用上次人工确认）",
           m["binding_source"] == "user", str(m.get("binding_source")))
    _check("第二次：bindings 数量不变（没有因 entity_id 变化产生重复绑定）",
           len(reg2.list_bindings()) == n_bindings, str(len(reg2.list_bindings())))


# ---- 13. ⛔ Qwen 称谓变体跨解析 → 绑定稳定（Phase 2-2 commit 2 Alias 归一核心）----
def _make_plan_alias(entity_name: str) -> ProductionPlan:
    """带 Qwen 称谓变体实体的 plan：规则锚点仍是 柳如烟/沈青崖，Qwen 实体名可变。

    模拟 Qwen 同一角色跨解析给出不同写法（柳如烟/柳姑娘/柳小姐、沈青崖/沈大侠）。
    锚点 = shot.characters 规则层真实角色名（script_parser 提取，脚本上下文驱动）。
    """
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
            time="黄昏", weather="雨",
            shots=[Shot(
                shot_id="shot_01", source_text="柳姑娘推门而出。", duration_sec=5,
                characters=[Character(name="柳如烟"), Character(name="沈青崖")],
                entities=[
                    Entity(name=entity_name, type=EntityType.CHARACTER,
                           source=EntitySource.SCRIPT, confidence=0.9),
                    Entity(name="沈青崖", type=EntityType.CHARACTER,
                           source=EntitySource.SCRIPT, confidence=0.95),
                    Entity(name="旧剑匣", type=EntityType.PROP,
                           source=EntitySource.SCRIPT, confidence=0.9),
                ],
            )],
        )],
        validation=Validation(),
    )


def test_alias_variance_binding_stable() -> None:
    print("\n[13] ⛔ Qwen 称谓变体跨解析 → 绑定稳定（Alias 归一，Phase 2-2 commit 2 核心）")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()

    # 第一次解析：Qwen 写「柳如烟」规范名 → auto → 用户确认绑定
    res_a = am.match_plan(_make_plan(), LIB, registry=reg)
    by_a = {m["name"]: m for m in res_a["matches"]}
    _check("第一次：柳如烟 auto 带 asset_id",
           by_a["柳如烟"]["status"] == "auto" and by_a["柳如烟"].get("asset_id"),
           str(by_a["柳如烟"]))
    aid = by_a["柳如烟"]["asset_id"]
    _check("第一次：entity_key=character:柳如烟",
           by_a["柳如烟"]["entity_key"] == "character:柳如烟",
           str(by_a["柳如烟"].get("entity_key")))
    b = reg.set_binding("character:柳如烟", aid, status="accepted", source="user")
    _check("第一次：用户确认绑定落盘", b is not None and b["asset_id"] == aid)
    reg.save()
    n_bindings = len(reg.list_bindings())

    # 第二次解析：Qwen 写「柳姑娘」（称谓变体）→ 归一到 柳如烟 → 同一绑定
    reg2 = AssetRegistry(root).load()
    res_b = am.match_plan(_make_plan_alias("柳姑娘"), LIB, registry=reg2)
    by_b = {m["name"]: m for m in res_b["matches"]}
    _check("第二次：matches 无「柳姑娘」（已归一到锚点）",
           "柳姑娘" not in by_b, str(list(by_b)))
    _check("第二次：柳如烟 persisted（称谓变体不分裂绑定）",
           by_b["柳如烟"]["match_kind"] == "persisted"
           and by_b["柳如烟"]["status"] == "auto"
           and by_b["柳如烟"]["matched"] is True, str(by_b["柳如烟"]))
    _check("第二次：asset_id 与第一次完全一致",
           by_b["柳如烟"]["asset_id"] == aid,
           f"{by_b['柳如烟'].get('asset_id')} vs {aid}")
    _check("第二次：binding_source=user", by_b["柳如烟"]["binding_source"] == "user",
           str(by_b["柳如烟"].get("binding_source")))
    _check("第二次：bindings 数量不变", len(reg2.list_bindings()) == n_bindings,
           str(len(reg2.list_bindings())))

    # 第三次解析：Qwen 写「沈大侠」（另一个称谓变体，无 binding 也稳定 entity_key）
    res_c = am.match_plan(_make_plan_alias("沈大侠"), LIB, registry=reg2)
    by_c = {m["name"]: m for m in res_c["matches"]}
    _check("第三次：沈大侠 归一为 沈青崖", "沈大侠" not in by_c, str(list(by_c)))
    _check("第三次：entity_key=character:沈青崖（锚点规范名）",
           by_c["沈青崖"]["entity_key"] == "character:沈青崖",
           str(by_c["沈青崖"].get("entity_key")))
    _check("第三次：沈青崖 无 binding 仍走规则 exact",
           by_c["沈青崖"]["match_kind"] == "exact", str(by_c["沈青崖"].get("match_kind")))

    # 第四次解析：Qwen 写「蒙面客」但剧本规则层无「蒙面人」锚点 → 不猜，原样保留
    res_d = am.match_plan(_make_plan_alias("蒙面客"), LIB, registry=reg2)
    by_d = {m["name"]: m for m in res_d["matches"]}
    _check("第四次：蒙面客 无锚点 → 原样保留（歧义不猜）",
           "蒙面客" in by_d and by_d["蒙面客"]["entity_key"] == "character:蒙面客",
           str(list(by_d)))


# ---- 14. ⛔ 完整剧本两次导入 → 全部 binding persisted 复用（Phase 2-2 commit 3 端到端）----
def _make_full_plan() -> ProductionPlan:
    """完整《山雨客栈》风格 plan：4 实体（柳如烟/沈青崖/旧剑匣/山雨楼外）跨 2 镜头。

    锚点 = shot.characters 规则层真实角色名（柳如烟/沈青崖）；entities = Qwen 语义补全。
    """
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[
            Scene(
                scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
                time="黄昏", weather="雨",
                shots=[Shot(
                    shot_id="shot_01", source_text="柳姑娘推门而出。", duration_sec=5,
                    characters=[Character(name="柳如烟"), Character(name="沈青崖")],
                    entities=[
                        Entity(name="柳如烟", type=EntityType.CHARACTER,
                               source=EntitySource.SCRIPT, confidence=0.9),
                        Entity(name="沈青崖", type=EntityType.CHARACTER,
                               source=EntitySource.SCRIPT, confidence=0.95),
                        Entity(name="旧剑匣", type=EntityType.PROP,
                               source=EntitySource.SCRIPT, confidence=0.9),
                    ],
                )],
            ),
            Scene(
                scene_id="scene_02", title="客栈大堂", location_name="山雨楼外",
                time="夜", weather="雨",
                shots=[Shot(
                    shot_id="shot_02", source_text="沈青崖立在廊下。", duration_sec=5,
                    characters=[Character(name="柳如烟")],
                    entities=[
                        Entity(name="柳如烟", type=EntityType.CHARACTER,
                               source=EntitySource.SCRIPT, confidence=0.92),
                        Entity(name="山雨楼外", type=EntityType.LOCATION,
                               source=EntitySource.SCRIPT, confidence=0.95),
                    ],
                )],
            ),
        ],
        validation=Validation(),
    )


def _make_full_plan_alias() -> ProductionPlan:
    """第二次导入：Qwen 给称谓变体 + 全部 entity_id 重分配（模拟跨解析全新临时身份）。

    锚点（shot.characters）仍是规范名 → alias 归一必须把 柳姑娘→柳如烟、沈大侠→沈青崖，
    使 entity_key 稳定 → persisted 复用，不因变体产生新绑定。
    """
    plan = _make_full_plan()
    for si, sc in enumerate(plan.scenes):
        for sh in sc.shots:
            for e in sh.entities:
                e.entity_id = f"ent_2_{si}_{e.name}"  # 模拟下次解析全新临时 id
                if e.type == EntityType.CHARACTER:
                    if e.name == "柳如烟":
                        e.name = "柳姑娘"
                    elif e.name == "沈青崖":
                        e.name = "沈大侠"
    return plan


def test_full_plan_two_imports_all_persisted() -> None:
    print("\n[14] ⛔ 完整剧本两次导入 → 全部 persisted 复用（Phase 2-2 commit 3 端到端）")
    root = _make_registry_dir()
    reg = AssetRegistry(root).load()

    # 第一次导入：4 实体全部 auto → 用户确认全部 binding 落盘
    res_a = am.match_plan(_make_full_plan(), LIB, registry=reg)
    by_a = {m["name"]: m for m in res_a["matches"]}
    _check("第一次：4 实体全部 auto 带 asset_id",
           all(by_a[n]["status"] == "auto" and by_a[n].get("asset_id")
               for n in ("柳如烟", "沈青崖", "旧剑匣", "山雨楼外")),
           str(list(by_a)))
    aid = {n: by_a[n]["asset_id"] for n in by_a}
    for n, m in by_a.items():
        reg.set_binding(m["entity_key"], aid[n], status="accepted", source="user")
    reg.save()
    n_bindings = len(reg.list_bindings())
    _check("第一次：用户确认 4 个绑定落盘", n_bindings == 4, str(n_bindings))

    # 第二次导入：称谓变体 + 全新 entity_id → 全部 persisted 复用（模拟独立进程从磁盘读）
    reg2 = AssetRegistry(root).load()
    res_b = am.match_plan(_make_full_plan_alias(), LIB, registry=reg2)
    by_b = {m["name"]: m for m in res_b["matches"]}
    _check("第二次：matches 无称谓变体（柳姑娘/沈大侠 已归一到锚点）",
           "柳姑娘" not in by_b and "沈大侠" not in by_b, str(list(by_b)))
    _check("第二次：4 实体全部 persisted + binding_source=user",
           all(by_b[n]["match_kind"] == "persisted" and by_b[n]["binding_source"] == "user"
               for n in ("柳如烟", "沈青崖", "旧剑匣", "山雨楼外")), str(by_b))
    _check("第二次：asset_id 与第一次完全一致（绑定纹丝不动）",
           all(by_b[n]["asset_id"] == aid[n] for n in aid),
           str({n: by_b[n].get("asset_id") for n in by_b}))
    _check("第二次：bindings 数量不变（4，变体不产生重复绑定）",
           len(reg2.list_bindings()) == n_bindings, str(len(reg2.list_bindings())))
    # ③ 已归一的实体名在注册表里仍可被 alias 变体命中（不因写入顺序产生分裂）
    _check("注册表仍只有 4 条绑定（无 character:柳姑娘 / character:沈大侠）",
           not any(k in reg2.list_bindings() for k in ("character:柳姑娘", "character:沈大侠")),
           str(list(reg2.list_bindings())))


def main() -> None:
    test_entity_key_stable()
    test_asset_id_stable_and_independent()
    test_entity_id_vs_asset_id()
    test_binding_persist()
    test_accepted_binding_overrides_matcher()
    test_binding_promotes_pending_to_persisted()
    test_registry_never_generates_entities()
    test_binding_asset_deleted_falls_back()
    test_no_registry_backward_compat()
    test_route_binding_persist()
    test_route_binding_bad_requests()
    test_register_route_included()
    test_scan_persists_asset_ids_cross_instance()
    test_qwen_entity_id_change_binding_stable()
    test_alias_variance_binding_stable()
    test_full_plan_two_imports_all_persisted()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
