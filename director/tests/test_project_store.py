#!/usr/bin/env python3
"""project_store 工程文件层单元测试（V1.1.1）。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_project_store.py

或经 pytest：
    pytest director/tests/test_project_store.py
（两者兼容：本文件以 assert 为主，pytest 可收集 test_* 函数。）
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types


# ---------- 路径与 folder_paths 桩（在导入 project_store 之前注入）----------
import os
import sys

# 让 `python3 director/tests/test_project_store.py` 能从仓库根导入 director 包
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_FOLDER_PATHS = types.ModuleType("folder_paths")
_INPUT_DIR: str = ""


def _get_input_directory() -> str:
    return _INPUT_DIR


_FOLDER_PATHS.get_input_directory = _get_input_directory
sys.modules.setdefault("folder_paths", _FOLDER_PATHS)

from director.project_store import (  # noqa: E402
    create_project,
    delete_project,
    delete_snapshot,
    list_projects,
    list_snapshots,
    load_project,
    load_snapshot,
    save_project,
    save_timeline_snapshot,
    snapshots_root,
    projects_root,
    _safe_project_id,
)


# ---------- 测试 ----------

def test_safe_project_id():
    assert _safe_project_id("hello") == "hello"
    assert _safe_project_id("我的项目") == "我的项目"
    # 路径穿越/危险字符：basename 剥目录 + 危险字符 → 下划线，结果不含分隔符/点点
    for evil in ("../etc/passwd", "a/b\\c d", "..\\..\\win.ini", "x; rm -rf /", " " * 3 + "a" + " " * 3):
        out = _safe_project_id(evil)
        assert out, f"{evil!r} → 空结果"
        assert "/" not in out and "\\" not in out and ".." not in out, f"{evil!r} → {out!r} 仍含危险片段"
    assert _safe_project_id("")  # 兜底非空


def test_create_and_list_project():
    proj = create_project("测试工程")
    assert proj["name"] == "测试工程"
    assert proj["episodes"] == 1
    # 磁盘上真的有 project.json 且带空 episode
    data = load_project(proj["id"])
    assert data is not None
    assert data["name"] == "测试工程"
    assert len(data["episodes"]) == 1
    assert data["episodes"][0]["scenes"] == []
    assert "cast" in data["episodes"][0]["assets"]
    assert "locations" in data["episodes"][0]["assets"]

    listing = list_projects()
    # 按 id 定位本项目，不断言 listing[0]——共享假输入根可能有跨运行残留项目，
    # 目录 mtime 排序不可靠（#541 验收实测 1 failed：listing[0] 命中残留的「往返工程」）。
    matches = [p for p in listing if p["id"] == proj["id"]]
    assert len(matches) == 1
    assert matches[0]["name"] == "测试工程"


def test_save_load_project_roundtrip():
    pid = _safe_project_id("roundtrip_工程")
    payload = {
        "id": pid,
        "name": "往返工程",
        "episodes": [
            {
                "id": "ep1",
                "title": "第一集",
                "scenes": [{"id": "s1", "name": "开场", "shots": []}],
                "assets": {"cast": [], "locations": [], "props": [], "styles": []},
            }
        ],
    }
    assert save_project(pid, payload) is True
    got = load_project(pid)
    assert got == payload
    assert got["episodes"][0]["scenes"][0]["name"] == "开场"


def test_save_timeline_snapshot_and_load():
    timeline = {
        "timelineMode": "prompt_batch",
        "frameRate": 24,
        "segments": [{"id": "seg1"}, {"id": "seg2"}],
        "scenes": [{"id": "s1"}],
    }
    name = save_timeline_snapshot(timeline)
    assert name is not None and name.endswith(".json")

    data = load_snapshot(name[:-5])
    assert data is not None
    assert data["frameRate"] == 24
    assert len(data["segments"]) == 2

    # 字符串版本（executor 传入 JSON 字符串）
    name2 = save_timeline_snapshot(json.dumps(timeline))
    assert name2 is not None
    data2 = load_snapshot(name2[:-5])
    assert data2["timelineMode"] == "prompt_batch"


def test_save_timeline_snapshot_rejects_nonjson():
    assert save_timeline_snapshot("not json {{") is None
    assert save_timeline_snapshot("") is None
    assert save_timeline_snapshot(None) is None


def test_list_snapshots_ordering():
    for i in range(3):
        save_timeline_snapshot({"i": i, "segments": [], "scenes": []})
    snaps = list_snapshots()
    assert len(snaps) >= 3
    # 新→旧（字母序倒序 = 时间倒序）
    times = [s["id"] for s in snaps]
    assert times == sorted(times, reverse=True)
    # 摘要字段齐全
    s0 = snaps[0]
    for key in ("id", "name", "time", "timelineMode", "segments", "scenes"):
        assert key in s0


def test_list_snapshots_projects_defensive():
    """#382b：坏文件/坏目录不得让 /snapshots、/projects 500（用户真实 500 驱动）。

    修复前：顶层 JSON 非 dict → data.get 抛 AttributeError → 路由 500；
    segments/scenes 非 list → len() 抛 TypeError → 500；_ensure_root 在 try 外
    → os.makedirs OSError 直接冒泡 500。
    """
    snap_dir = snapshots_root()
    os.makedirs(snap_dir, exist_ok=True)
    # 顶层非 dict（list）→ 跳过
    with open(os.path.join(snap_dir, "bad_array.json"), "w", encoding="utf-8") as f:
        json.dump(["not", "a", "dict"], f)
    # dict 但 segments/scenes 非 list → 不崩，计 0
    with open(os.path.join(snap_dir, "bad_segs.json"), "w", encoding="utf-8") as f:
        json.dump({"updatedAt": "2026-01-01", "segments": 42, "scenes": "x"}, f)

    snaps = list_snapshots()
    ids = [s["id"] for s in snaps]
    assert "bad_array" not in ids, "顶层非 dict 快照必须跳过"
    assert "bad_segs" in ids, "dict 快照字段非 list 也不得丢弃"
    for s in snaps:
        if s["id"] == "bad_segs":
            assert s["segments"] == 0 and s["scenes"] == 0, "segments/scenes 非 list 计 0"

    # 项目目录塞一个顶层非 dict 的 project.json → 跳过
    proj_dir = projects_root()
    os.makedirs(proj_dir, exist_ok=True)
    badp = os.path.join(proj_dir, "bad_proj")
    os.makedirs(badp, exist_ok=True)
    with open(os.path.join(badp, "project.json"), "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)
    projs = list_projects()
    assert all(p["id"] != "bad_proj" for p in projs), "顶层非 dict project.json 必须跳过"


def test_load_missing_returns_none():
    assert load_project("no_such_proj") is None
    assert load_snapshot("no_such_snap") is None
    # 路径穿越拒绝
    assert load_snapshot("../evil") is None


def test_delete_project_and_snapshot():
    proj = create_project("待删项目")
    pid = proj["id"]
    assert load_project(pid) is not None
    assert delete_project(pid) is True
    assert load_project(pid) is None
    # 二次删 / 不存在返回 False
    assert delete_project(pid) is False

    name = save_timeline_snapshot({"segments": [], "scenes": []})
    sid = name[:-5]
    assert load_snapshot(sid) is not None
    assert delete_snapshot(sid) is True
    assert load_snapshot(sid) is None
    assert delete_snapshot(sid) is False

    # 路径穿越拒绝
    assert delete_snapshot("../evil") is False
    assert delete_project("../evil") is False
    assert delete_project("") is False


def main() -> None:
    """无 pytest 时直接运行。"""
    global _INPUT_DIR
    tmp = tempfile.mkdtemp(prefix="minimax_studio_test_")
    _INPUT_DIR = tmp
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in funcs:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"共 {len(funcs)} 项全部通过（input_dir={tmp}）")
    # 清理
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
