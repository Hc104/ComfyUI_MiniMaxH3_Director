#!/usr/bin/env python3
"""audio_export.py 单元测试（纯规则，mock，不碰网络/GPU）。

覆盖（#606/#607）：
1. mute 模式：AUDIO 输出=静音，但每个输出片段按实际帧数生成正确长度的
   立体声静音（torch.zeros(1, 2, n_samples)）。此前返回 0 样本空音频
   （torch.zeros(1, 1, 0)），下游 CreateVideo/SaveVideo 的 PyAV resampler
   对空 AudioFrame 报 [Errno 12] Cannot allocate memory（@graph.push）。
2. mute 模式合并导出（images_out 单条=合并全片）：按总帧数生成整段静音。
3. generate 模式（无生成音频）：r2v 走 source 提取回退静音，静音长度按帧数
   pad（非 mute 分支回归保护）。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_audio_export.py
"""

from __future__ import annotations

import os
import sys
import types
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包（与 ComfyUI 运行时一致）：director 是它的子包，
# audio_export 内部 `from ..lib.audio_io import ...` 才能正确解析。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

# folder_paths 是 ComfyUI 运行时模块（DEP 在 custom_nodes 内天然可用）。
# SRC 目录独立跑测试时不存在，而 audio_export 导入链（audio_export →
# lib/audio_io → lib/video_io）顶层 `import folder_paths`；它在被测路径中只在
# resolve_video_path() 函数体内被调用（本测试不触碰），导入期仅需模块可 import。
# 提供最小 stub，让测试在任何目录都能独立运行（ComfyUI 环境自动走真实模块）。
try:
    import folder_paths  # noqa: F401  # pragma: no cover - ComfyUI 运行时
except ImportError:  # pragma: no cover - 仅非 ComfyUI 环境走此分支
    _FP_STUB = types.ModuleType("folder_paths")
    _FP_STUB.get_input_directory = lambda: os.path.join(_REPO_ROOT, "input")
    _FP_STUB.get_output_directory = lambda: os.path.join(_REPO_ROOT, "output")
    _FP_STUB.get_temp_directory = lambda: os.path.join(_REPO_ROOT, "temp")
    sys.modules.setdefault("folder_paths", _FP_STUB)

# comfy.utils 同为 ComfyUI 运行时模块（image_prep 顶层 `from comfy.utils import
# common_upscale`）。common_upscale 只在 fit_long_edge/fit_canvas 函数体内被调用
# （本测试不触碰），导入期仅需模块可 import。提供最小 stub（被调用即抛清晰错误）。
try:
    from comfy.utils import common_upscale  # noqa: F401  # pragma: no cover - ComfyUI 运行时
except ImportError:  # pragma: no cover - 仅非 ComfyUI 环境走此分支
    _COMFY = types.ModuleType("comfy")
    _COMFY.__path__ = []
    sys.modules.setdefault("comfy", _COMFY)
    _COMFY_UTILS = types.ModuleType("comfy.utils")

    def _stub_common_upscale(*args, **kwargs):  # pragma: no cover
        raise NotImplementedError(
            "comfy.utils.common_upscale 仅 ComfyUI 运行时可用（本测试不调用它）"
        )

    _COMFY_UTILS.common_upscale = _stub_common_upscale
    sys.modules.setdefault("comfy.utils", _COMFY_UTILS)

try:
    import torch
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"需要 torch 才能跑本测试：{exc}") from exc

from ComfyUI_MiniMaxH3_Director.director.audio_export import (  # noqa: E402
    AUDIO_MODE_MUTE,
    build_director_audio_outputs,
    empty_audio_dict,
)

PASSED = 0
FAILED = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def _plan(audio_mode: str = "mute", fps: float = 24.0, task: str = "r2v") -> SimpleNamespace:
    return SimpleNamespace(
        frame_rate=fps,
        global_task_key=task,
        raw={"output": {"audioMode": audio_mode}},
        total_frames=124,
        segments=[],
        run_indices=None,
    )


def _frames(n: int) -> list:
    return [torch.zeros(n, 640, 1120, 3)]


def test_mute_segment_lengths() -> None:
    """mute 模式：每条输出静音长度=帧数对应样本数，通道=2，sample_rate=44100。"""
    print("\n[1] mute 模式：输出正确长度立体声静音（#607）")
    # 两条片段：3 帧 + 5 帧 @ 24fps → 期望样本数 = round(frames*44100/24)
    images_out = [torch.zeros(3, 640, 1120, 3), torch.zeros(5, 640, 1120, 3)]
    audio_out, fallback = build_director_audio_outputs(
        _plan(audio_mode="mute"), images_out, export_segments=True, audio_mode=AUDIO_MODE_MUTE
    )
    _check("mute 返回空列表? No → 长度=images 数", len(audio_out) == 2, str(len(audio_out)))
    _check("mute 无 source fallback", fallback is None, str(fallback))
    for i, (tensor, samples) in enumerate(zip(images_out, (3, 5))):
        wave = audio_out[i]["waveform"]
        want = int(round(samples * 44100 / 24))
        _check(
            f"片段#{i} 立体声 2ch", int(wave.shape[1]) == 2, f"got channels={wave.shape[1]}"
        )
        _check(
            f"片段#{i} 样本数={want}",
            int(wave.shape[-1]) == want,
            f"got samples={wave.shape[-1]}",
        )
        _check(f"片段#{i} 静音（全 0）", bool((wave == 0).all()))
        _check(f"片段#{i} sample_rate=44100", int(audio_out[i]["sample_rate"]) == 44100)
    # 回归：0 样本空音频不应再出现（此前触发 [Errno 12]）
    _check("mute 输出不再含 0 样本空音频", all(int(a["waveform"].shape[-1]) > 0 for a in audio_out))


def test_mute_merged_single() -> None:
    """mute 模式合并导出：images_out 单条=整片，静音长度=总帧数对应样本数。"""
    print("\n[2] mute 模式：合并导出单条整段静音（#607）")
    images_out = _frames(124)
    audio_out, _ = build_director_audio_outputs(
        _plan(audio_mode="mute"), images_out, export_segments=False, audio_mode=AUDIO_MODE_MUTE
    )
    _check("单条输出", len(audio_out) == 1, str(len(audio_out)))
    wave = audio_out[0]["waveform"]
    want = int(round(124 * 44100 / 24))
    _check("样本数=124帧对应", int(wave.shape[-1]) == want, f"got {wave.shape[-1]}, want {want}")
    _check("立体声 2ch", int(wave.shape[1]) == 2, str(wave.shape))
    _check("静音", bool((wave == 0).all()))


def test_generate_fallback_silent_padded() -> None:
    """generate 模式无生成音频：r2v source 提取失败回退静音，长度按帧数 pad（非 mute 回归）。"""
    print("\n[3] generate 模式：无生成音频回退静音（回归保护）")
    # r2v + 无 timeline 源音频 → extract 失败 → 静音按帧数 pad。
    # 必须带 segment：export_segments 分支按 plan.segments 取 seg_indices，
    # 无 segment 会直接返回空音频占位而不走「提取→pad」路径。
    seg = SimpleNamespace(
        index=0,
        start_frame=0,
        end_frame=3,
        frame_count=3,
    )
    plan = _plan(audio_mode="generate", task="r2v")
    plan.segments = [seg]
    images_out = _frames(3)
    audio_out, fallback = build_director_audio_outputs(
        plan, images_out, export_segments=True, audio_mode="generate"
    )
    # r2v 无源视频时 extract_timeline_audio 返回空 → _coerce → 静音
    wave = audio_out[0]["waveform"]
    _check("输出 1 条", len(audio_out) == 1, str(len(audio_out)))
    _check("样本数=3帧对应", int(wave.shape[-1]) == int(round(3 * 44100 / 24)), str(wave.shape[-1]))
    # 空音频占位符本身仍可用（返回类型契约不被破坏）
    empty = empty_audio_dict(44100)
    _check("empty_audio_dict 仍返回 0 样本占位", int(empty["waveform"].shape[-1]) == 0)


def main() -> None:
    test_mute_segment_lengths()
    test_mute_merged_single()
    test_generate_fallback_silent_padded()
    print(f"\n结果：{PASSED} 通过 / {FAILED} 失败")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
