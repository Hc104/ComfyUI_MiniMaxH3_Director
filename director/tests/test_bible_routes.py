#!/usr/bin/env python3
"""P2-P4（#541）：Global Story Bible 路由层测试。

分两层：
1. **bible_ops 纯逻辑**（直接 import，tempfile 临时 root，零假模块零 LLM 零 GPU）：
   merge_plan_into_bible（新建候选 / 复用已确认 Stable Entity Key / version+1 落盘）/
   get_bible_payload / confirm_bible_action（confirm / accept_attribute / merge_alias /
   edit_attributes + 错误） / create_bible_entry / 持久化 roundtrip。
2. **http_routes 薄壳**（注入假 server/folder_paths/aiohttp 后 import，同
   test_story_import.py 模式）：
   story/import 无 project_id → 仅三键 + extract_beats 不带 bible（向后兼容回归锁死）/
   有 project_id → extract_beats 收到 bible + 响应追加 bible + bible_updates /
   三条新路由注册 / GET·POST 400 校验。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_bible_routes.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import types

# ---- 在 import director.http_routes 前注入假 ComfyUI 模块（薄壳层需要）----
_server_mod = types.ModuleType("server")
_server_mod.PromptServer = type("PromptServer", (), {"instance": None})
sys.modules["server"] = _server_mod

_folder_paths_mod = types.ModuleType("folder_paths")
# 隔离假输入/临时根到本次进程专属临时目录：http_routes 薄壳层会经
# ensure_bible 在 input/minimax_studio/projects/{project_id}/ 落盘 bible.json，
# 若指向共享 /tmp/fake_input 会在用户机器残留 proj_demo 目录（#541 验收
# list_projects 扫到无 project.json 的项目目录 → warning）。模块级 tempdir 每次
# 运行全新，绝不污染共享测试根。
_ISOLATED_INPUT_DIR = tempfile.mkdtemp(prefix="minimax_studio_bible_routes_in_")
_ISOLATED_TEMP_DIR = tempfile.mkdtemp(prefix="minimax_studio_bible_routes_tmp_")
_folder_paths_mod.get_input_directory = lambda: _ISOLATED_INPUT_DIR
_folder_paths_mod.get_temp_directory = lambda: _ISOLATED_TEMP_DIR
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

from director.bible import (  # noqa: E402
    BIBLE_ENTITY_CHARACTER,
    BIBLE_ENTITY_LOCATION,
    BIBLE_ENTITY_PROP,
    BIBLE_STATUS_CANDIDATE,
    BIBLE_STATUS_CONFIRMED,
    BibleEntry,
    StoryBible,
)
from director.bible_ops import (  # noqa: E402
    confirm_bible_action,
    create_bible_entry,
    get_bible_payload,
    merge_plan_into_bible,
)
from director.bible_store import bible_path, load_bible, save_bible  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    ProductionPlan,
    ProjectInfo,
    Prop,
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


# ===========================================================================
# 通用 fixture
# ===========================================================================

def _mk_plan(chars: tuple = ("沈青崖",), props: tuple = ("剑匣",),
             loc: str = "山雨楼大堂") -> ProductionPlan:
    shot = Shot(
        shot_id="shot_01",
        source_text="沈青崖背着一把旧剑匣走进山雨楼大堂。",
        characters=[Character(name=n) for n in chars],
        props=[Prop(name=p) for p in props],
    )
    scene = Scene(scene_id="location_01", title=loc, location_name=loc, shots=[shot])
    plan = ProductionPlan(scenes=[scene], timeline=["location_01:shot_01"])
    plan.beats = [
        StoryBeat(beat_id="beat_01", title="沈青崖进入山雨楼",
                  text_segments=[shot.source_text])
    ]
    return plan


def _write_bible(root: str, project_id: str, entries, version: int = 0) -> None:
    save_bible(StoryBible(bible_id=project_id, project_id=project_id,
                          entries=entries, version=version), root=root)


def _mk_confirmed_char(project_id: str) -> StoryBible:
    return StoryBible(project_id=project_id, entries=[
        BibleEntry(
            entity_id="CHAR-001",
            entity_type=BIBLE_ENTITY_CHARACTER,
            name="沈青崖",
            status=BIBLE_STATUS_CONFIRMED,
            attributes={"appearance": "白衣剑客"},
            last_seen="ep1",
            history=["ep1：初入山雨楼"],
        )
    ])


# ===========================================================================
# bible_ops 纯逻辑
# ===========================================================================

def test_merge_plan_creates_candidates() -> None:
    print("\n[1] merge_plan_into_bible 空 Bible → 全新建候选 + version=1 + 落盘")
    with tempfile.TemporaryDirectory() as root:
        bible_dict, updates = merge_plan_into_bible(
            project_id="proj_demo", plan=_mk_plan(), episode_number=3, root=root
        )
        _check("version == 1", bible_dict["version"] == 1, str(bible_dict["version"]))
        names = {e["name"] for e in bible_dict["entries"]}
        _check("三实体入册", names == {"沈青崖", "剑匣", "山雨楼大堂"}, str(names))
        ch = next(e for e in bible_dict["entries"] if e["name"] == "沈青崖")
        _check("新建为候选", ch["status"] == BIBLE_STATUS_CANDIDATE, ch["status"])
        _check("来源 rule", ch["source"] == "rule", ch["source"])
        _check("首见 ep3", ch["first_seen"] == "ep3", ch["first_seen"])
        _check("history 带剧情标题", ch["history"] == ["ep3：沈青崖进入山雨楼"], str(ch["history"]))
        _check("updates 全 new_candidate",
               all(u["kind"] == "new_candidate" for u in updates), str(updates))
        _check("updates 形状稳定",
               all({"entity_id", "name", "message"} <= set(u) for u in updates))
        _check("bible.json 落盘", os.path.exists(bible_path("proj_demo", root=root)))
        _check("重载一致", load_bible("proj_demo", root=root).version == 1)


def test_merge_plan_reuses_confirmed() -> None:
    print("\n[2] merge_plan_into_bible 命中已确认 → 复用稳定 id，不新建")
    with tempfile.TemporaryDirectory() as root:
        _write_bible(root, "proj_demo", _mk_confirmed_char("proj_demo").entries, version=3)
        bible_dict, updates = merge_plan_into_bible(
            project_id="proj_demo", plan=_mk_plan(), episode_number=2, root=root
        )
        chars = [e for e in bible_dict["entries"] if e["entity_type"] == BIBLE_ENTITY_CHARACTER]
        _check("角色只 1 条（Stable Entity Key）", len(chars) == 1, str(len(chars)))
        _check("复用 CHAR-001", chars[0]["entity_id"] == "CHAR-001")
        _check("已确认不降级", chars[0]["status"] == BIBLE_STATUS_CONFIRMED)
        _check("静态属性不动", chars[0]["attributes"] == {"appearance": "白衣剑客"})
        _check("last_seen 推进 ep2", chars[0]["last_seen"] == "ep2")
        _check("history 追加不覆盖",
               chars[0]["history"] == ["ep1：初入山雨楼", "ep2：沈青崖进入山雨楼"],
               str(chars[0]["history"]))
        _check("updates 含 reused(CHAR-001)",
               any(u["kind"] == "reused" and u["entity_id"] == "CHAR-001" for u in updates))
        _check("剑匣为新建候选",
               any(u["kind"] == "new_candidate" and u["name"] == "剑匣" for u in updates))
        _check("version 3 → 4", bible_dict["version"] == 4, str(bible_dict["version"]))


def test_get_bible_payload() -> None:
    print("\n[3] get_bible_payload：无 Bible → None；有 → dict 含 entries")
    with tempfile.TemporaryDirectory() as root:
        _check("无 Bible → bible=None", get_bible_payload("nope", root=root)["bible"] is None)
        _write_bible(root, "p1", [BibleEntry(entity_id="CHAR-001", name="沈青崖")])
        payload = get_bible_payload("p1", root=root)
        _check("有 Bible → bible dict", isinstance(payload["bible"], dict))
        _check("entries 非空", len(payload["bible"]["entries"]) == 1)


def test_confirm_candidate() -> None:
    print("\n[4] confirm 候选 → confirmed + source=user + version+1")
    with tempfile.TemporaryDirectory() as root:
        _write_bible(root, "p1", [
            BibleEntry(entity_id="CHAR-001", entity_type=BIBLE_ENTITY_CHARACTER,
                       name="沈青崖", status=BIBLE_STATUS_CANDIDATE, source="rule"),
        ])
        out = confirm_bible_action(project_id="p1", entity_id="CHAR-001",
                                   action="confirm", root=root)
        e = out["bible"]["entries"][0]
        _check("status=confirmed", e["status"] == BIBLE_STATUS_CONFIRMED, e["status"])
        _check("source=user", e["source"] == "user", e["source"])
        _check("version=1", out["bible"]["version"] == 1)
        _check("落盘", load_bible("p1", root=root).entries[0].status == BIBLE_STATUS_CONFIRMED)


def test_confirm_accept_attribute() -> None:
    print("\n[5] accept_attribute 采纳属性建议 → attributes 并入 + suggestion 移除")
    with tempfile.TemporaryDirectory() as root:
        _write_bible(root, "p1", [
            BibleEntry(entity_id="CHAR-001", entity_type=BIBLE_ENTITY_CHARACTER,
                       name="沈青崖", status=BIBLE_STATUS_CONFIRMED,
                       attributes={"appearance": "白衣剑客"},
                       attribute_suggestions=[{"key": "personality", "value": "冷峻"}]),
        ])
        out = confirm_bible_action(project_id="p1", entity_id="CHAR-001",
                                   action="accept_attribute",
                                   payload={"key": "personality", "value": "冷峻"},
                                   root=root)
        e = out["bible"]["entries"][0]
        _check("attributes 并入", e["attributes"] == {"appearance": "白衣剑客", "personality": "冷峻"},
               str(e["attributes"]))
        _check("suggestion 移除", e["attribute_suggestions"] == [], str(e["attribute_suggestions"]))


def test_confirm_merge_alias() -> None:
    print("\n[6] merge_alias → aliases 追加去重")
    with tempfile.TemporaryDirectory() as root:
        _write_bible(root, "p1", [
            BibleEntry(entity_id="CHAR-001", entity_type=BIBLE_ENTITY_CHARACTER,
                       name="沈青崖", status=BIBLE_STATUS_CONFIRMED, aliases=["青崖"]),
        ])
        out = confirm_bible_action(project_id="p1", entity_id="CHAR-001",
                                   action="merge_alias", payload={"alias": "沈公子"},
                                   root=root)
        _check("aliases 追加", out["bible"]["entries"][0]["aliases"] == ["青崖", "沈公子"])
        out2 = confirm_bible_action(project_id="p1", entity_id="CHAR-001",
                                    action="merge_alias", payload={"alias": "青崖"},
                                    root=root)
        _check("重复别名不追加", out2["bible"]["entries"][0]["aliases"] == ["青崖", "沈公子"])


def test_confirm_edit_attributes() -> None:
    print("\n[7] edit_attributes 整体覆盖 + 空值剔除 + source=user")
    with tempfile.TemporaryDirectory() as root:
        _write_bible(root, "p1", [
            BibleEntry(entity_id="CHAR-001", entity_type=BIBLE_ENTITY_CHARACTER,
                       name="沈青崖", status=BIBLE_STATUS_CONFIRMED, source="rule",
                       attributes={"appearance": "白衣剑客", "personality": "冷峻"}),
        ])
        out = confirm_bible_action(project_id="p1", entity_id="CHAR-001",
                                   action="edit_attributes",
                                   payload={"attributes": {"appearance": "黑衣客", "personality": ""}},
                                   root=root)
        e = out["bible"]["entries"][0]
        _check("attributes 覆盖+空值删", e["attributes"] == {"appearance": "黑衣客"},
               str(e["attributes"]))
        _check("source=user", e["source"] == "user", e["source"])


def test_confirm_errors() -> None:
    print("\n[8] confirm 错误：无 Bible / 无条目 / 未知 action / 缺 payload")
    with tempfile.TemporaryDirectory() as root:
        try:
            confirm_bible_action(project_id="nope", entity_id="CHAR-001",
                                 action="confirm", root=root)
            _check("无 Bible → ValueError", False)
        except ValueError as exc:
            _check("无 Bible → ValueError", "尚无 Bible" in str(exc), str(exc))
        _write_bible(root, "p1", [
            BibleEntry(entity_id="CHAR-001", name="沈青崖"),
        ])
        for label, kw in [
            ("未知 action", dict(action="delete", entity_id="CHAR-001")),
            ("缺 entity", dict(action="confirm", entity_id="")),
            ("accept_attribute 缺 payload", dict(action="accept_attribute",
                                                  entity_id="CHAR-001",
                                                  payload={"key": "a"})),
            ("merge_alias 缺 alias", dict(action="merge_alias",
                                           entity_id="CHAR-001", payload={})),
            ("edit_attributes 非 dict", dict(action="edit_attributes",
                                              entity_id="CHAR-001",
                                              payload={"attributes": "x"})),
        ]:
            try:
                confirm_bible_action(project_id="p1", root=root, **kw)
                _check(label + " → ValueError", False)
            except ValueError:
                _check(label + " → ValueError", True)


def test_create_bible_entry() -> None:
    print("\n[9] create_bible_entry 人工新建（source=user），Bible 不存在自动建空")
    with tempfile.TemporaryDirectory() as root:
        out = create_bible_entry(project_id="p1", entity_type=BIBLE_ENTITY_CHARACTER,
                                 name="林雪", attributes={"appearance": "提灯女子"},
                                 aliases=["小雪"], root=root)
        created = out["created"]
        _check("新建 CHAR-001", created["entity_id"] == "CHAR-001", created["entity_id"])
        _check("source=user", created["source"] == "user")
        _check("attributes 保留", created["attributes"] == {"appearance": "提灯女子"})
        _check("aliases 保留", created["aliases"] == ["小雪"])
        _check("version=1", out["bible"]["version"] == 1)
        _check("bible 含新条目", len(out["bible"]["entries"]) == 1)


def test_persist_roundtrip() -> None:
    print("\n[10] confirm 写回后文件级持久化 roundtrip")
    with tempfile.TemporaryDirectory() as root:
        _write_bible(root, "p1", [
            BibleEntry(entity_id="LOC-001", entity_type=BIBLE_ENTITY_LOCATION,
                       name="山雨楼大堂", status=BIBLE_STATUS_CANDIDATE),
        ])
        confirm_bible_action(project_id="p1", entity_id="LOC-001", action="confirm", root=root)
        reloaded = load_bible("p1", root=root)
        _check("重载 status=confirmed", reloaded.entries[0].status == BIBLE_STATUS_CONFIRMED)
        _check("重载 version=1", reloaded.version == 1)
        _check("重载 source=user", reloaded.entries[0].source == "user")


# ===========================================================================
# http_routes 薄壳
# ===========================================================================

from director import http_routes as hr  # noqa: E402
from director import project_store as _ps  # noqa: E402

# 隔离 http_routes 薄壳层的项目落盘根。http_routes 内 ensure_bible（story/import
# 有 project_id 时真实执行）不传 root，默认经 bible_store._resolve_projects_root
# 懒加载 project_store.projects_root() 解析；而 project_store 的 folder_paths 绑定
# 取决于**最早导入它的测试文件**（全量收集时是 test_asset_matcher 的 /tmp/fake_input），
# 会把 proj_demo/bible.json 写进共享测试根、残留到用户机器（#541 验收 list_projects
# 扫到无 project.json 的项目目录 → warning）。
# 必须用 scoped 上下文管理器（不能模块级全局替换 projects_root/snapshots_root）：
# 模块级替换会让**早于本文件导入**的 test_project_store 的 snapshots_root() 函数引用
# （旧值）与 list_snapshots 内部模块全局查找（新值）脱节——写目录和扫描目录不一致，
# 导致 test_list_snapshots_projects_defensive 断言 bad_segs 丢失（#543 实测 1 failed）。
_PS_PROJECTS_ROOT = os.path.join(_ISOLATED_INPUT_DIR, "minimax_studio", "projects")
_PS_SNAPSHOTS_ROOT = os.path.join(_ISOLATED_INPUT_DIR, "minimax_studio", "snapshots")


@contextlib.contextmanager
def _isolated_project_roots():
    """仅包住会真实写盘的 http_routes 薄壳调用，用后恢复 project_store 的 root 函数。"""
    orig_pr, orig_sr = _ps.projects_root, _ps.snapshots_root
    _ps.projects_root = lambda: _PS_PROJECTS_ROOT
    _ps.snapshots_root = lambda: _PS_SNAPSHOTS_ROOT
    try:
        yield
    finally:
        _ps.projects_root, _ps.snapshots_root = orig_pr, orig_sr


class _FakeRequest:
    def __init__(self, body=None, method: str = "POST", query=None):
        self._body = body
        self.method = method
        self.query = query or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _resp_data(resp):
    return asyncio.run(resp.json())


def _make_plan(title: str = "山雨客栈") -> ProductionPlan:
    shot = Shot(shot_id="shot_01",
                source_text="沈青崖在漫天风雪中推开庙门，肩头的雪簌簌落下。")
    scene = Scene(scene_id="location_01", title="风雪山神庙",
                  location_name="风雪山神庙", shots=[shot])
    plan = ProductionPlan(project=ProjectInfo(title=title), scenes=[scene],
                          validation=Validation(status="pending"),
                          timeline=["location_01:shot_01"])
    plan.validate()
    return plan


_STORY = "沈青崖在漫天风雪中推开庙门。林雪提着灯笼从后殿转出来，低声道：你果然也来了。"


def test_story_import_no_project_backward_compat() -> None:
    print("\n[11] story/import 无 project_id → 仅三键 + extract_beats 不带 bible（向后兼容）")
    captured = {}

    def fake_extract(text, backend, **kw):
        captured["has_bible_kw"] = "bible" in kw
        return [], False, []

    def fake_build(text, beats, warnings=None, *, title=""):
        return _make_plan(title=title)

    orig_e, orig_b = hr.extract_beats, hr.build_plan_from_beats
    hr.extract_beats, hr.build_plan_from_beats = fake_extract, fake_build
    try:
        req = _FakeRequest({"story_text": _STORY, "title": "山雨客栈"})
        resp = asyncio.run(hr.minimax_director_story_import(req))
    finally:
        hr.extract_beats, hr.build_plan_from_beats = orig_e, orig_b

    data = _resp_data(resp)
    _check("status 200", resp.status == 200, str(resp.status))
    _check("仅三键（回归锁死）", set(data.keys()) == {"plan", "rule_only", "warnings"},
           str(sorted(data.keys())))
    _check("extract_beats 未收到 bible 参数", captured.get("has_bible_kw") is False)


def test_story_import_with_project_adds_bible() -> None:
    print("\n[12] story/import 带 project_id → extract_beats 收到 bible + 响应追加 bible/bible_updates")
    captured = {}

    def fake_extract(text, backend, **kw):
        captured["has_bible_kw"] = "bible" in kw
        return [], False, []

    def fake_build(text, beats, warnings=None, *, title=""):
        return _make_plan(title=title)

    def fake_merge(*, project_id, plan, episode_number=1):
        return ({"bible_id": project_id, "version": 1,
                 "entries": [{"entity_id": "CHAR-001", "name": "沈青崖"}]},
                [{"kind": "new_candidate", "entity_id": "CHAR-001",
                  "name": "沈青崖", "message": "新候选 沈青崖（CHAR-001，待确认）"}])

    orig_e, orig_b, orig_m = hr.extract_beats, hr.build_plan_from_beats, hr.merge_plan_into_bible
    hr.extract_beats, hr.build_plan_from_beats = fake_extract, fake_build
    hr.merge_plan_into_bible = fake_merge
    try:
        req = _FakeRequest({"story_text": _STORY, "project_id": "proj_demo",
                            "episode_number": 3})
        with _isolated_project_roots():
            # ensure_bible 在此真实落盘 → 临时钉到本模块专属根，绝不写共享测试根
            resp = asyncio.run(hr.minimax_director_story_import(req))
    finally:
        hr.extract_beats, hr.build_plan_from_beats = orig_e, orig_b
        hr.merge_plan_into_bible = orig_m

    data = _resp_data(resp)
    _check("status 200", resp.status == 200, str(resp.status))
    _check("extract_beats 收到 bible", captured.get("has_bible_kw") is True)
    _check("响应含 bible", isinstance(data.get("bible"), dict), str(data.get("bible")))
    _check("响应含 bible_updates", isinstance(data.get("bible_updates"), list))
    _check("bible_updates 形状", data["bible_updates"][0]["kind"] == "new_candidate")
    _check("五键", set(data.keys()) == {"plan", "rule_only", "warnings", "bible", "bible_updates"})


def test_bible_routes_registered() -> None:
    print("\n[13] 三条 Bible 路由注册进 register_routes")
    src = open(os.path.join(_REPO_ROOT, "director", "http_routes.py"), "r", encoding="utf-8").read()
    _check("GET /bible 注册", "/minimax/director/bible\"" in src.replace(",", "") or
           '"/minimax/director/bible"' in src, "")
    _check("POST /bible/confirm 注册", "/minimax/director/bible/confirm" in src)
    _check("POST /bible/entry 注册", "/minimax/director/bible/entry" in src)
    _check("handler 存在", hasattr(hr, "minimax_director_bible_get")
           and hasattr(hr, "minimax_director_bible_confirm")
           and hasattr(hr, "minimax_director_bible_entry"))


def test_bible_get_missing_project_400() -> None:
    print("\n[14] GET /bible 缺 project_id → 400 missing_project_id")
    resp = asyncio.run(hr.minimax_director_bible_get(_FakeRequest(method="GET", query={})))
    data = _resp_data(resp)
    _check("status 400", resp.status == 400, str(resp.status))
    _check("错误码 missing_project_id", data.get("error") == "missing_project_id", str(data))


def test_bible_confirm_missing_field_400() -> None:
    print("\n[15] POST /bible/confirm 缺字段 → 400 missing_field")
    req = _FakeRequest({"project_id": "p1", "action": "confirm"})
    resp = asyncio.run(hr.minimax_director_bible_confirm(req))
    data = _resp_data(resp)
    _check("status 400", resp.status == 400, str(resp.status))
    _check("错误码 missing_field", data.get("error") == "missing_field", str(data))


def test_bible_entry_missing_field_400() -> None:
    print("\n[16] POST /bible/entry 缺 name → 400 missing_field")
    req = _FakeRequest({"project_id": "p1", "entity_type": "character"})
    resp = asyncio.run(hr.minimax_director_bible_entry(req))
    data = _resp_data(resp)
    _check("status 400", resp.status == 400, str(resp.status))
    _check("错误码 missing_field", data.get("error") == "missing_field", str(data))


def main() -> None:
    test_merge_plan_creates_candidates()
    test_merge_plan_reuses_confirmed()
    test_get_bible_payload()
    test_confirm_candidate()
    test_confirm_accept_attribute()
    test_confirm_merge_alias()
    test_confirm_edit_attributes()
    test_confirm_errors()
    test_create_bible_entry()
    test_persist_roundtrip()
    test_story_import_no_project_backward_compat()
    test_story_import_with_project_adds_bible()
    test_bible_routes_registered()
    test_bible_get_missing_project_400()
    test_bible_confirm_missing_field_400()
    test_bible_entry_missing_field_400()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
