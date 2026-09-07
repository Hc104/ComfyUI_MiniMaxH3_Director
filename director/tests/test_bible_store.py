#!/usr/bin/env python3
"""P2-P1（#538）：Global Story Bible 持久化层单元测试。

覆盖（P2_GLOBAL_STORY_BIBLE_PLAN.md §5）：
1. ensure_bible：不存在 → 建空 Bible 并落盘（version=0）；
2. save → load round-trip：字段逐字一致；
3. load 缺失 → None；JSON 损坏 → None + warning（不抛）；
4. root 注入：显式 root 优先于默认 project_store 解析（不依赖 folder_paths）；
5. 原子写：bible.json 无 .tmp 残留；
6. project_id 安全化：路径穿越 / 特殊字符被清洗。

直接用系统 python 运行（无第三方依赖，不碰 GPU）：
    python3 director/tests/test_bible_store.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.bible import (  # noqa: E402
    BIBLE_ENTITY_CHARACTER,
    BibleEntry,
    StoryBible,
)
from director.bible_store import (  # noqa: E402
    bible_path,
    ensure_bible,
    load_bible,
    save_bible,
)


def _mk_bible(project_id: str = "proj_demo") -> StoryBible:
    return StoryBible(
        bible_id=project_id,
        project_id=project_id,
        entries=[
            BibleEntry(
                entity_id="CHAR-001",
                entity_type=BIBLE_ENTITY_CHARACTER,
                name="沈青崖",
                status="confirmed",
                attributes={"appearance": "白衣剑客"},
                history=["ep1：初入山雨楼"],
                last_seen="ep2",
            )
        ],
        version=1,
        updated_at="2026-08-15T12:00:00",
    )


def test_ensure_creates_empty_bible() -> None:
    with tempfile.TemporaryDirectory() as root:
        b = ensure_bible("proj_a", root=root)
        assert b.project_id == "proj_a"
        assert b.bible_id == "proj_a"
        assert b.entries == []
        assert b.version == 0
        # 已落盘，可再次加载
        b2 = load_bible("proj_a", root=root)
        assert b2 is not None and b2.project_id == "proj_a"


def test_save_then_load_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as root:
        bible = _mk_bible("proj_demo")
        assert save_bible(bible, root=root) is True
        loaded = load_bible("proj_demo", root=root)
        assert loaded is not None
        assert loaded == bible, "save→load 应逐字一致"
        # 落盘内容本身可读且含 ensure_ascii=False（中文原文）
        with open(bible_path("proj_demo", root=root), "r", encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["entries"][0]["name"] == "沈青崖"


def test_load_missing_returns_none() -> None:
    with tempfile.TemporaryDirectory() as root:
        assert load_bible("no_such", root=root) is None


def test_load_corrupt_returns_none() -> None:
    with tempfile.TemporaryDirectory() as root:
        path = bible_path("corrupt", root=root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ not valid json !!!")
        assert load_bible("corrupt", root=root) is None


def test_root_injection_overrides_default() -> None:
    """显式 root 生效——不触发 project_store（folder_paths）依赖。"""
    with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
        bible = _mk_bible("proj_split")
        assert save_bible(bible, root=root_a)
        # 写入 root_a，root_b 读不到
        assert load_bible("proj_split", root=root_b) is None
        assert load_bible("proj_split", root=root_a) is not None


def test_no_tmp_leftover() -> None:
    with tempfile.TemporaryDirectory() as root:
        assert save_bible(_mk_bible("proj_atomic"), root=root)
        d = os.path.dirname(bible_path("proj_atomic", root=root))
        names = os.listdir(d)
        assert "bible.json" in names
        assert not any(n.endswith(".tmp") for n in names), "原子写不应残留 .tmp"


def test_project_id_sanitized() -> None:
    with tempfile.TemporaryDirectory() as root:
        evil = "../evil/../escape"
        bible = StoryBible(project_id=evil, bible_id=evil)
        assert save_bible(bible, root=root) is True
        path = bible_path(evil, root=root)
        # 路径必须落在 root 内（无 ../ 逃逸）
        assert ".." not in path.split(os.sep)[-2:]
        assert os.path.isfile(path)


def test_load_fills_missing_ids() -> None:
    """旧数据缺 bible_id/project_id → 加载时补 project_id。"""
    with tempfile.TemporaryDirectory() as root:
        path = bible_path("proj_legacy", root=root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"entries": [], "version": 0}, f)
        b = load_bible("proj_legacy", root=root)
        assert b is not None
        assert b.project_id == "proj_legacy"
        assert b.bible_id == "proj_legacy"
