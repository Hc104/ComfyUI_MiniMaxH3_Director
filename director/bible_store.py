#!/usr/bin/env python3
"""P2 · Global Story Bible 持久化层（bible_store.py，任务 #538）。

存储：`{ComfyUI input}/minimax_studio/projects/{project_id}/bible.json`
（与 project.json 同目录，**后端权威**；同 asset_registry.json 的「文件即状态」思路，
但属剧情层，独立文件独立演化）。

关键设计：
- **root 可注入**：`load_bible/save_bible/ensure_bible` 均接受可选 `root`
  （projects 根目录）。ComfyUI 运行环境缺省从 project_store.projects_root()
  解析；测试环境传临时目录即可，**不依赖 folder_paths**（延续「后端纯逻辑回归
  222 passed 不碰 ComfyUI」的纪律）。
- 原子写：临时文件 + os.replace，防中断半写（同 save_project 风格）。
- 防御加载：文件缺失 → None；JSON 损坏 → None + warning，绝不抛。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from .bible import StoryBible

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.bible_store")

BIBLE_FILE_NAME = "bible.json"

# 项目 id 安全字符（与 project_store._SAFE_ID 逐字一致；独立复制以避免
# 顶层 import project_store 触发 folder_paths 依赖，保持纯逻辑可测）
_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-一-鿿]+")


def _safe_project_id(raw: str) -> str:
    base = os.path.basename(str(raw or "").replace("\\", "/"))
    base = _SAFE_ID.sub("_", base).strip("._")
    return base or f"proj_{int(time.time() * 1000)}"


def _resolve_projects_root(root: Optional[str] = None) -> str:
    """解析 projects 根目录。显式 root 优先；缺省懒加载 project_store（ComfyUI 环境）。"""
    if root:
        return root
    from .project_store import projects_root  # noqa: PLC0415 - 懒加载避免测试环境 folder_paths 依赖

    return projects_root()


def bible_path(project_id: str, root: Optional[str] = None) -> str:
    """bible.json 绝对路径（不保证目录存在）。"""
    return os.path.join(
        _resolve_projects_root(root), _safe_project_id(project_id), BIBLE_FILE_NAME
    )


def load_bible(project_id: str, root: Optional[str] = None) -> Optional[StoryBible]:
    """读 Bible；文件缺失/损坏 → None + warning（绝不抛）。"""
    path = bible_path(project_id, root)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        bible = StoryBible.from_dict(data)
        if not bible.project_id:
            bible.project_id = _safe_project_id(project_id)
        if not bible.bible_id:
            bible.bible_id = bible.project_id
        return bible
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("加载 Bible %s 失败: %s", project_id, exc)
        return None


def save_bible(bible: StoryBible, root: Optional[str] = None) -> bool:
    """覆盖写 bible.json（原子：临时文件 + os.replace）。"""
    pid = _safe_project_id(bible.project_id or bible.bible_id)
    d = os.path.join(_resolve_projects_root(root), pid)
    path = os.path.join(d, BIBLE_FILE_NAME)
    try:
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bible.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log.error("保存 Bible %s 失败: %s", pid, exc)
        return False


def ensure_bible(project_id: str, root: Optional[str] = None) -> StoryBible:
    """加载 Bible；不存在则建空 Bible 并落盘（version=0）。"""
    bible = load_bible(project_id, root)
    if bible is not None:
        return bible
    pid = _safe_project_id(project_id)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    bible = StoryBible(
        bible_id=pid,
        project_id=pid,
        entries=[],
        version=0,
        updated_at=now,
    )
    save_bible(bible, root)
    return bible


__all__ = [
    "BIBLE_FILE_NAME",
    "bible_path",
    "load_bible",
    "save_bible",
    "ensure_bible",
]
