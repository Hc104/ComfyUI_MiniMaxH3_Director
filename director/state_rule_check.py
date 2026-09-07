"""三级失败重跑：非 VLM 规则检测（state_rule_check，里程碑 C）。

设计决策（用户 2026-08-07）：**不让 Qwen 一个模型决定重跑**，否则误判 → 无限生成。
流程：Qwen 发现问题（"疑似多手"）→ 规则检测确认（黑帧/过曝/纯色/模糊/分辨率异常）
→ **双确认成立才重跑**（换种子、限 1 次）。

本模块只做**纯像素/统计规则检测，不依赖 VLM**——与 Qwen 独立形成交叉验证：
Qwen 负责语义判断（画面崩坏/多手/换脸），规则负责客观验证（像素层面是否有真实异常）。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.state_rule_check")

# 规则阈值（经验值，基于 0~1 归一化灰度帧 / [N,H,W,C] 0~1 帧张量）
LUMA_TOO_DARK = 0.03             # 帧平均亮度低于此 → 过暗
LUMA_TOO_BRIGHT = 0.97           # 帧平均亮度高于此 → 过曝
STD_TOO_LOW = 0.012              # 灰度标准差低于此 → 纯色/无内容
LAPLACIAN_TOO_BLURRY = 0.00035   # Laplacian 方差低于此 → 严重模糊
MIN_WIDTH = 320                  # 分辨率下限（H3 通常 864×480）
MIN_HEIGHT = 240
# 各类异常帧占比超过阈值才触发（容忍单帧噪声）
DARK_RATIO = 0.15
BRIGHT_RATIO = 0.15
SOLID_RATIO = 0.15
BLUR_RATIO = 0.25
SAMPLE_LIMIT = 32                # 最多等距采样 N 帧统计（省计算）


def _to_gray_chw(frame: Any) -> "Any":
    """把单帧 [H,W,C] 或 [C,H,W] 转成 [H,W] float32 灰度。frame 须已是 torch.Tensor。"""
    import torch

    t = frame.detach().cpu().float()
    if t.ndim == 3 and int(t.shape[0]) == 3 and int(t.shape[2]) != 3:
        t = t.permute(1, 2, 0)  # [C,H,W] → [H,W,C]
    if t.ndim != 3:
        raise ValueError(f"帧维度异常: {t.shape}")
    # 简单亮度加权转灰度
    if int(t.shape[2]) >= 3:
        gray = 0.299 * t[..., 0] + 0.587 * t[..., 1] + 0.114 * t[..., 2]
    else:
        gray = t[..., 0]
    return gray.clamp(0, 1)


def _laplacian_var(gray: Any) -> float:
    """灰度图 Laplacian 方差（锐度/清晰度）。gray: [H,W] float [0,1]."""
    import torch.nn.functional as F

    g = gray.unsqueeze(0).unsqueeze(0)  # [1,1,H,W]
    kernel = torch.tensor(
        [[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]],
        dtype=gray.dtype, device=gray.device,
    ).view(1, 1, 3, 3)
    lap = F.conv2d(g, kernel, padding=1)
    return float(lap.var().item())


def _frame_stats(frame: Any) -> dict[str, float]:
    """单帧统计：{mean(亮度), std(灰度标准差), lap(Laplacian 方差)}."""
    gray = _to_gray_chw(frame)
    mean = float(gray.mean().item())
    std = float(gray.std().item())
    lap = _laplacian_var(gray)
    return {"mean": mean, "std": std, "lap": lap}


def analyze_frames(frames: Any) -> dict[str, Any]:
    """整段帧的统计汇总（供规则判定与诊断展示）。

    Args:
        frames: torch.Tensor [N,H,W,C]（或 [N,C,H,W]）0~1 float，或可被 torch.as_tensor 的序列。

    返回 dict（不含判定，只含统计）：
    {
        "frame_count": N,
        "width": W, "height": H,
        "dark_ratio": 过暗帧占比,
        "bright_ratio": 过曝帧占比,
        "solid_ratio": 纯色帧占比,
        "blur_ratio": 模糊帧占比,
        "min_lap": 最小 Laplacian 方差,
        "sampled": 实际采样帧数,
    }
    任何异常返回空 dict（不抛错，保证不阻断主流程）。
    """
    try:
        import torch

        if frames is None:
            return {}
        if not isinstance(frames, torch.Tensor):
            frames = torch.as_tensor(frames)
        if frames.ndim != 4 or int(frames.shape[0]) < 1:
            return {}

        n = int(frames.shape[0])
        h, w = int(frames.shape[1]), int(frames.shape[2])
        # 采样：等距取最多 SAMPLE_LIMIT 帧
        if n <= SAMPLE_LIMIT:
            idxs = list(range(n))
        else:
            step = n / SAMPLE_LIMIT
            idxs = [min(n - 1, int(i * step)) for i in range(SAMPLE_LIMIT)]

        dark = bright = solid = blur = 0
        laps: list[float] = []
        for i in idxs:
            try:
                st = _frame_stats(frames[i])
            except Exception:
                continue
            if st["mean"] < LUMA_TOO_DARK:
                dark += 1
            if st["mean"] > LUMA_TOO_BRIGHT:
                bright += 1
            if st["std"] < STD_TOO_LOW:
                solid += 1
            if st["lap"] < LAPLACIAN_TOO_BLURRY:
                blur += 1
            laps.append(st["lap"])

        sampled = len(laps)
        if sampled == 0:
            return {"frame_count": n, "width": w, "height": h, "sampled": 0}

        def _ratio(cnt: int) -> float:
            return round(cnt / sampled, 3)

        return {
            "frame_count": n,
            "width": w,
            "height": h,
            "dark_ratio": _ratio(dark),
            "bright_ratio": _ratio(bright),
            "solid_ratio": _ratio(solid),
            "blur_ratio": _ratio(blur),
            "min_lap": round(min(laps), 6),
            "sampled": sampled,
        }
    except Exception as exc:  # 规则检测失败绝不阻断主流程
        log.warning("规则检测统计失败: %s", exc)
        return {}


def rule_check_frames(frames: Any, *, expected_size: tuple[int, int] | None = None) -> dict[str, Any]:
    """规则检测（二级交叉验证）：客观像素异常判定。

    Args:
        frames: 整段帧张量（见 analyze_frames）。
        expected_size: (width, height) 期望分辨率；None 则不查分辨率。

    返回：
    {
        "abnormal": bool,        # 是否检测到客观异常（触发重跑候选）
        "issues": list[str],     # 命中的规则问题（中文描述）
        "detail": str,           # 一句话诊断（供报告）
    }
    无法检测时 abnormal=False 且 issues 为空（不误伤）。
    """
    try:
        stats = analyze_frames(frames)
        if not stats or not stats.get("sampled"):
            return {"abnormal": False, "issues": [], "detail": "规则检测不可用"}

        issues: list[str] = []
        if stats["dark_ratio"] >= DARK_RATIO:
            issues.append(f"过暗帧 {int(stats['dark_ratio'] * 100)}%")
        if stats["bright_ratio"] >= BRIGHT_RATIO:
            issues.append(f"过曝帧 {int(stats['bright_ratio'] * 100)}%")
        if stats["solid_ratio"] >= SOLID_RATIO:
            issues.append(f"纯色帧 {int(stats['solid_ratio'] * 100)}%")
        if stats["blur_ratio"] >= BLUR_RATIO:
            issues.append(f"模糊帧 {int(stats['blur_ratio'] * 100)}%")
        if expected_size is not None:
            _w, _h = int(expected_size[0]), int(expected_size[1])
            if stats["width"] < MIN_WIDTH or stats["height"] < MIN_HEIGHT:
                issues.append(f"分辨率异常 {stats['width']}×{stats['height']}")

        abnormal = bool(issues)
        detail = "; ".join(issues) if issues else "无明显像素异常"
        return {
            "abnormal": abnormal,
            "issues": issues,
            "detail": detail,
        }
    except Exception as exc:  # 规则检测失败绝不阻断主流程
        log.warning("规则检测失败: %s", exc)
        return {"abnormal": False, "issues": [], "detail": "规则检测失败"}


def confirm_rerun(qwen_issues: list[str] | None, rule_result: dict[str, Any] | None) -> tuple[bool, str]:
    """Qwen + 规则双确认：只有 Qwen 报严重问题 AND 规则检测到客观异常才重跑。

    Args:
        qwen_issues: Qwen 检测到的严重问题列表（已过滤为"结构性/严重"类别），空/None 表示无。
        rule_result: rule_check_frames 的返回。

    返回 (rerun: bool, reason: str)。reason 供报告展示。
    """
    q = [s for s in (qwen_issues or []) if s]
    r = rule_result or {}
    if not q:
        return False, "Qwen 未发现严重问题"
    if not r.get("abnormal"):
        # Qwen 单方面误判 → 规则不确认 → 不重跑（防无限生成铁律）
        return False, f"Qwen 报「{'、'.join(q)}」但规则无客观异常，不重跑"
    return True, f"Qwen「{'、'.join(q)}」+ 规则「{r.get('detail')}」双确认 → 重跑"
