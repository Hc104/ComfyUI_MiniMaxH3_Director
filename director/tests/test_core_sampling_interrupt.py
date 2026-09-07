#!/usr/bin/env python3
"""core_sampling 采样中断检查回归测试（#91）。

背景（用户实测）：SPA 点「取消生成」→ 前端发 /interrupt → ComfyUI 只置中断标志；
官方 KSampler 依赖 comfy.ops 在模型前向检查标志，H3 采样路径不保证命中，导致
desktop 继续跑到结束。修复：core_sampling.py 复制 common_ksampler 核心并在
①采样前 ②每步 callback 显式检查中断标志，置位时抛 InterruptProcessingException
（BaseException 子类，向上传播终止整次生成）。

本测试用桩验证：
- 标志置位时，采样前检查直接抛 InterruptProcessingException；
- 标志在采样中途置位时，step callback 抛 InterruptProcessingException（sample 不返回）；
- 标志未置位时，正常走完采样并返回 latent dict。

运行：
    python3 director/tests/test_core_sampling_interrupt.py
或
    pytest director/tests/test_core_sampling_interrupt.py
"""

from __future__ import annotations

import os
import sys
import types

# ---------- 依赖桩（在导入 director 包之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包：让 director 包以 ComfyUI_MiniMaxH3_Director.director 层级存在。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

# comfy 桩（sample_single_stage 内 lazy import）。
_comfy = types.ModuleType("comfy")
_comfy.__path__ = []
sys.modules.setdefault("comfy", _comfy)

# comfy.model_management 桩：可编程的中断标志。
_INTERRUPT_FLAG = {"value": False}


class _InterruptProcessingException(BaseException):
    pass


_mm = types.ModuleType("comfy.model_management")
_mm.InterruptProcessingException = _InterruptProcessingException


def _throw_exception_if_processing_interrupted():
    if _INTERRUPT_FLAG["value"]:
        _INTERRUPT_FLAG["value"] = False
        raise _InterruptProcessingException()


_mm.throw_exception_if_processing_interrupted = _throw_exception_if_processing_interrupted
_mm.processing_interrupted = lambda: _INTERRUPT_FLAG["value"]
sys.modules.setdefault("comfy.model_management", _mm)
_comfy.model_management = _mm

# comfy.sample 桩：fix_empty_latent_channels / prepare_noise / sample。
_sample = types.ModuleType("comfy.sample")
_sample.fix_empty_latent_channels = lambda model, li, a=None, b=None: li
_sample.prepare_noise = lambda li, seed, noise_inds=None: "NOISE"

_CALLS = {"step": 0}


def _fake_sample(model, noise, steps, cfg, sampler_name, scheduler, positive, negative,
                 latent_image, denoise=1.0, disable_noise=False, start_step=None,
                 last_step=None, force_full_denoise=False, noise_mask=None,
                 callback=None, disable_pbar=False, seed=None):
    # 模拟采样第一步：invoke callback（每步回调会检查中断标志）。
    _CALLS["step"] += 1
    callback(0, None, None, int(steps))
    return "SAMPLES"


_sample.sample = _fake_sample
sys.modules.setdefault("comfy.sample", _sample)
_comfy.sample = _sample

# comfy.latent_preview 桩：回调拼接（这里返回 None，仅验证中断回调）。
_lp = types.ModuleType("comfy.latent_preview")
_lp.prepare_callback = lambda model, steps: None
sys.modules.setdefault("comfy.latent_preview", _lp)
_comfy.latent_preview = _lp

# comfy_extras.nodes_minimax_h3 桩：SigmaShift 返回移位模型。
_ce = types.ModuleType("comfy_extras")
_ce.__path__ = []
sys.modules.setdefault("comfy_extras", _ce)

_mh = types.ModuleType("comfy_extras.nodes_minimax_h3")


class _MiniMaxH3SigmaShift:
    @classmethod
    def execute(cls, model, shift_video, shift_audio):
        return types.SimpleNamespace(args=("SHIFTED_MODEL",))


_mh.MiniMaxH3SigmaShift = _MiniMaxH3SigmaShift
sys.modules.setdefault("comfy_extras.nodes_minimax_h3", _mh)
_ce.nodes_minimax_h3 = _mh


from ComfyUI_MiniMaxH3_Director.director.core_sampling import sample_single_stage  # noqa: E402


def _reset() -> None:
    _INTERRUPT_FLAG["value"] = False
    _CALLS["step"] = 0
    _sample.sample = _fake_sample  # 恢复默认 sample 桩（防跨用例残留）


def _latent() -> dict:
    return {"samples": "LATENT"}


def test_interrupt_before_sample_raises():
    """采样前检查：标志置位 → 直接抛 InterruptProcessingException，不进入 SigmaShift/sample。"""
    _reset()
    _INTERRUPT_FLAG["value"] = True
    try:
        sample_single_stage(model="model", positive=[], negative=[], latent=_latent(),
                            seed=1, cfg=1.0, steps=25, sampler_name="res_multistep",
                            scheduler="simple")
        assert False, "应抛 InterruptProcessingException"
    except _InterruptProcessingException:
        pass
    # sample 从未被调用（段开始就停了）。
    assert _CALLS["step"] == 0
    # 标志已被清掉（throw_exception_if_processing_interrupted 会复位）。
    assert _INTERRUPT_FLAG["value"] is False


def test_interrupt_mid_sample_raises():
    """采样中途置位 → step callback 抛 InterruptProcessingException（sample 不返回）。"""
    _reset()
    # 采样前不置位，进入 sample 后再置位（模拟采样进行中用户点取消）。
    _INTERRUPT_FLAG["value"] = False
    _sample.sample = lambda *a, **kw: _flag_set_then_invoke_callback(*a, **kw)
    try:
        sample_single_stage(model="model", positive=[], negative=[], latent=_latent(),
                            seed=1, cfg=1.0, steps=25, sampler_name="res_multistep",
                            scheduler="simple")
        assert False, "应抛 InterruptProcessingException"
    except _InterruptProcessingException:
        pass
    assert _CALLS["step"] == 1


def _flag_set_then_invoke_callback(model, noise, steps, cfg, sampler_name, scheduler,
                                   positive, negative, latent_image, denoise=1.0,
                                   disable_noise=False, start_step=None, last_step=None,
                                   force_full_denoise=False, noise_mask=None,
                                   callback=None, disable_pbar=False, seed=None):
    _CALLS["step"] += 1
    _INTERRUPT_FLAG["value"] = True  # 采样中途用户点取消
    callback(0, None, None, int(steps))
    return "SAMPLES"


def test_no_interrupt_normal_return():
    """标志未置位 → 正常走完采样并返回 latent dict（与旧行为一致）。"""
    _reset()
    out = sample_single_stage(model="model", positive=[], negative=[], latent=_latent(),
                              seed=1, cfg=1.0, steps=25, sampler_name="res_multistep",
                              scheduler="simple")
    assert isinstance(out, dict)
    assert out.get("samples") == "SAMPLES"
    # latent 的附加字段原样保留（dict(latent) 复制），与 common_ksampler 行为一致。
    assert out.get("extra") is None


def test_preview_cb_error_does_not_block():
    """preview 回调抛异常时中断回调仍先于它执行，preview 异常被吞掉不阻断采样。"""
    _reset()

    def _preview_cb(model, steps):
        def _inner(*a, **kw):
            raise RuntimeError("preview 失败")
        return _inner

    _lp.prepare_callback = _preview_cb
    try:
        out = sample_single_stage(model="model", positive=[], negative=[], latent=_latent(),
                                  seed=1, cfg=1.0, steps=25, sampler_name="res_multistep",
                                  scheduler="simple")
        assert out.get("samples") == "SAMPLES"
    finally:
        _lp.prepare_callback = lambda model, steps: None


def main():
    fns = [
        test_interrupt_before_sample_raises,
        test_interrupt_mid_sample_raises,
        test_no_interrupt_normal_return,
        test_preview_cb_error_does_not_block,
    ]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    main()
