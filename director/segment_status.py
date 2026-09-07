"""Runtime segment status for MiniMax H3 Director shot status lights.

UI 2.0 第五优先级（2026-08-08）：镜头胶囊条显示每镜状态灯——
未生成 / 生成中 / 成功 / 待检查 / 失败。

状态来源分两层：
- 「成功」由磁盘段缓存（seg_NNNN.pt）推导，跨重启保留（http_routes 扫目录）。
- 「生成中 / 待检查 / 失败」是运行时瞬态，存进程内存字典。

ComfyUI 本地单进程：PromptServer 与 worker 同进程，executor_core 写入、
http_routes 读取共享同一模块级字典。刻意不落盘——重启后 review/failed
标记丢失，镜头回退为「未生成」（无缓存）或「成功」（有缓存），最简单
且不产生陈旧状态；「待检查」本质是"上一次运行需人工确认"，用户重跑该
段即覆盖。

2026-08-10 #81：running 残留兜底。生成中断/崩溃时段循环可能走不到
「标 failed / 清 running」，进程内存里 running 会永久残留 → 前端蓝灯
一直闪（用户实测「没在生成蓝灯还闪」）。现在 set 时记时间戳，
get 时对超过 `_STALE_RUNNING_SECONDS` 的 running 直接忽略——
H3 单段生成不会超过 2 小时，超过视为残留。前端另有一道过滤
（WorkbenchView.refreshSegStatus 空闲时丢弃 running）。
"""

from __future__ import annotations

import time

# node_id -> { segment_index: (state, ts) }  state ∈ {"running", "review", "failed"}
_RUNTIME_STATES: dict[str, dict[int, tuple[str, float]]] = {}

# 单段 running 超过该秒数视为残留（H3 长段 + Qwen 三级重跑一般 ≤1h；给足余量）。
_STALE_RUNNING_SECONDS = 2 * 60 * 60


def set_segment_runtime_state(node_id: str | None, seg_index: int, state: str) -> None:
    """Set a transient runtime state for one segment (running/review/failed)."""
    if not node_id:
        return
    _RUNTIME_STATES.setdefault(str(node_id), {})[int(seg_index)] = (state, time.time())


def clear_segment_runtime_state(node_id: str | None, seg_index: int | None = None) -> None:
    """Clear runtime states for one segment (seg_index given) or the whole node."""
    if not node_id:
        return
    nid = str(node_id)
    if seg_index is None:
        _RUNTIME_STATES.pop(nid, None)
        return
    bucket = _RUNTIME_STATES.get(nid)
    if bucket is not None:
        bucket.pop(int(seg_index), None)
        if not bucket:
            _RUNTIME_STATES.pop(nid, None)


def get_segment_runtime_states(node_id: str | None) -> dict[str, str]:
    """Return {str(index): state} for all segments currently in a transient state.

    对超时的 running 做忽略（残留清理），review/failed 不设过期。
    """
    if not node_id:
        return {}
    bucket = _RUNTIME_STATES.get(str(node_id))
    if not bucket:
        return {}
    now = time.time()
    out: dict[str, str] = {}
    for k, (state, ts) in sorted(bucket.items()):
        if state == "running" and now - ts > _STALE_RUNNING_SECONDS:
            continue
        out[str(k)] = state
    return out
