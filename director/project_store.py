"""MiniMax Studio 工程文件层（V1.1.1）。

用户项目格式 = 前端 ProjectModel（projectStore.serializeProject 输出），
timeline_data 只是后端执行格式（ComfyUI Director 节点内部契约）。

存储根：{ComfyUI input}/minimax_studio/
├─ projects/{project_id}/project.json   # ProjectModel JSON（前端读写）
└─ snapshots/{timestamp}.json           # 原始 timeline_data 快照（executor 自动落盘）

「导入 ComfyUI 工程」= SPA 读 snapshots/ 里最近一次生成时的 timeline_data，
经前端 timelineImporter.migrate 成 ProjectModel，再另存为 projects/ 下正式项目。

薄接口层：只做文件 I/O + 扫描，不解析业务逻辑（迁移/序列化全在前端）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid

import folder_paths

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.project_store")

STUDIO_DIR_NAME = "minimax_studio"
PROJECTS_DIR_NAME = "projects"
SNAPSHOTS_DIR_NAME = "snapshots"

# 项目 id 安全字符（允许中文/数字/字母/横线/下划线）
_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-一-鿿]+")


def studio_root() -> str:
    return os.path.join(folder_paths.get_input_directory(), STUDIO_DIR_NAME)


def projects_root() -> str:
    return os.path.join(studio_root(), PROJECTS_DIR_NAME)


def snapshots_root() -> str:
    return os.path.join(studio_root(), SNAPSHOTS_DIR_NAME)


def _ensure_root() -> None:
    os.makedirs(projects_root(), exist_ok=True)
    os.makedirs(snapshots_root(), exist_ok=True)


def _safe_project_id(raw: str) -> str:
    base = os.path.basename(str(raw or "").replace("\\", "/"))
    base = _SAFE_ID.sub("_", base).strip("._")
    return base or f"proj_{int(time.time() * 1000)}"


def _project_dir(project_id: str) -> str:
    return os.path.join(projects_root(), _safe_project_id(project_id))


# ---------- 项目 ----------

def list_projects() -> list[dict]:
    """扫描 projects/*/project.json，返回 [{id,name,episodes,updatedAt}]（按 mtime 倒序）。

    ⛔ 防御性（#382b 生产硬化）：与 list_snapshots 同理，坏文件/坏目录不得 500——
    _ensure_root 挪进 try；顶层 JSON 非 dict 跳过；episodes 非 list 计 0。
    """
    out: list[dict] = []
    try:
        _ensure_root()
        entries = sorted(
            os.scandir(projects_root()),
            key=lambda e: e.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        log.warning("扫描项目目录失败: %s", exc)
        return out

    for ent in entries:
        if not ent.is_dir():
            continue
        meta_path = os.path.join(ent.path, "project.json")
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("读取项目 %s 失败: %s", ent.name, exc)
            continue
        if not isinstance(data, dict):
            continue  # 顶层非 dict 不当作项目
        eps = data.get("episodes") if isinstance(data.get("episodes"), list) else []
        out.append({
            "id": ent.name,
            "name": str(data.get("name") or ent.name),
            "episodes": len(eps),
            "updatedAt": str(data.get("updatedAt") or ""),
        })
    return out


def load_project(project_id: str) -> dict | None:
    """读 project.json 原文（前端 deserializeProject）。"""
    path = os.path.join(_project_dir(project_id), "project.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("加载项目 %s 失败: %s", project_id, exc)
        return None


def save_project(project_id: str, data: dict) -> bool:
    """覆盖写 project.json。data 必须是前端 serializeProject 输出的 ProjectModel。"""
    pid = _safe_project_id(project_id)
    d = _project_dir(pid)
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError as exc:
        log.error("保存项目 %s 失败: %s", pid, exc)
        return False


def create_project(name: str) -> dict:
    """新建空项目，返回 {id,name,episodes,updatedAt}。"""
    _ensure_root()
    pid = _safe_project_id(name) or f"proj_{int(time.time() * 1000)}"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    project = {
        "id": pid,
        "name": str(name or pid),
        "createdAt": now,
        "updatedAt": now,
        "episodes": [
            {
                "id": "ep1",
                "episodeNumber": 1,
                "title": "第一集",
                "scenes": [],
                "assets": {"cast": [], "locations": [], "props": [], "styles": []},
            }
        ],
    }
    save_project(pid, project)
    return {
        "id": pid,
        "name": project["name"],
        "episodes": 1,
        "updatedAt": now,
    }


def delete_project(project_id: str) -> bool:
    """删除整个项目目录（project.json + 任何子文件）。不存在返回 False。"""
    pid = _safe_project_id(project_id)
    d = _project_dir(pid)
    if not os.path.isdir(d):
        return False
    try:
        import shutil

        shutil.rmtree(d, ignore_errors=False)
        return not os.path.isdir(d)
    except OSError as exc:
        log.error("删除项目 %s 失败: %s", pid, exc)
        return False


# ---------- 快照（生成时自动落盘 timeline_data）----------

def save_timeline_snapshot(timeline_data: str | dict) -> str | None:
    """executor 每次收到 timeline_data 时调用，落盘一份快照。

    返回快照文件名（不含目录）；timeline_data 为空时返回 None。
    快照即后端契约原文，SPA「导入 ComfyUI 工程」读它再 migrate。
    """
    if timeline_data is None:
        return None
    if isinstance(timeline_data, str):
        raw = timeline_data.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 非 JSON 字符串（异常态）不落盘，避免污染快照目录
            log.warning("timeline_data 不是合法 JSON，跳过快照")
            return None
    else:
        data = timeline_data

    _ensure_root()
    ts = time.strftime("%Y%m%d_%H%M%S")
    # 毫秒时间戳 + 短随机片段：即使同毫秒内连续落盘（executor 连镜触发）也绝不互相覆盖。
    # 时间戳在前保证 list_snapshots 按名称倒序 = 时间倒序。
    name = f"{ts}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.json"
    path = os.path.join(snapshots_root(), name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return name
    except OSError as exc:
        log.error("写 timeline 快照失败: %s", exc)
        return None


def list_snapshots(limit: int = 50) -> list[dict]:
    """列出快照，返回 [{id,name,time,timelineMode,segments,scenes}]（新→旧）。

    ⛔ 防御性（#382b 生产硬化）：任何坏文件/坏目录都不得让 /snapshots 500——
    _ensure_root 挪进 try（os.makedirs 权限/磁盘满会 OSError）；顶层 JSON 非 dict
    跳过（json.load 只保证是 JSON，不保证是对象）；segments/scenes 非 list 计 0。
    """
    out: list[dict] = []
    try:
        _ensure_root()
        names = sorted(
            (e.name for e in os.scandir(snapshots_root()) if e.name.endswith(".json")),
            reverse=True,
        )[:limit]
    except OSError as exc:
        log.warning("扫描快照目录失败: %s", exc)
        return out

    for name in names:
        path = os.path.join(snapshots_root(), name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue  # 顶层非 dict（list/str/数字）不当作快照
        segs = data.get("segments") if isinstance(data.get("segments"), list) else []
        scns = data.get("scenes") if isinstance(data.get("scenes"), list) else []
        out.append({
            "id": name[:-5],  # 去 .json
            "name": name[:-5],
            "time": str(data.get("updatedAt") or name[:-5]),
            "timelineMode": str(data.get("timelineMode") or ""),
            "segments": len(segs),
            "scenes": len(scns),
        })
    return out


def load_snapshot(snapshot_id: str) -> dict | None:
    """读快照 timeline_data 原文。"""
    safe = os.path.basename(str(snapshot_id or "").replace("\\", "/"))
    if not safe or ".." in safe:
        return None
    path = os.path.join(snapshots_root(), f"{safe}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("加载快照 %s 失败: %s", snapshot_id, exc)
        return None


def delete_snapshot(snapshot_id: str) -> bool:
    """删除一份快照文件。不存在返回 False。"""
    safe = os.path.basename(str(snapshot_id or "").replace("\\", "/"))
    if not safe or ".." in safe or not safe.endswith(".json"):
        # 只接受不带 .json 的 id（load_snapshot 同款约定），拒绝路径穿越
        path = os.path.join(snapshots_root(), f"{safe}.json")
    else:
        path = os.path.join(snapshots_root(), safe)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
        return not os.path.isfile(path)
    except OSError as exc:
        log.error("删除快照 %s 失败: %s", snapshot_id, exc)
        return False
