#!/usr/bin/env python3
"""#484 VAE 非动态化修复单元测试（vae_residency.ensure_vae_resident）。

验证：
  1. 幂等：VAE patcher 已非动态 → 返回 False，什么都不改。
  2. 动态 + cached_patcher_init 工厂 → 走 get_non_dynamic_delegate()，替换
     vae.patcher 并设 vae.disable_offload=True。
  3. 缓存复用：同 key 二次调用不再重建（get_non_dynamic_delegate 只调一次）。
  4. 动态但工厂缺失/抛错 → 回退 vae.get_sd() 重建 + 基类 ModelPatcher。
  5. 重建也失败 → 返回 False（不破坏原状态）。

运行：
    python3 director/tests/test_vae_residency.py
或
    pytest director/tests/test_vae_residency.py
"""

from __future__ import annotations

import os
import sys
import types
import unittest

# ---------- 依赖桩（vae_residency 模块级无重依赖，可直接导入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

from director import vae_residency  # noqa: E402


# ---------- 桩 ----------
class _FakeDynamicPatcher:
    """模拟 ModelPatcherDynamic：is_dynamic()=True + get_non_dynamic_delegate()。

    与真实实现一致：cached_patcher_init 缺失时 get_non_dynamic_delegate() 抛
    RuntimeError（真实代码在 model_patcher.py clone() 里这样处理）。
    """

    def __init__(self, delegate=None, delegate_error: Exception | None = None, cached_patcher_init=None):
        self._delegate = delegate or _FakeNonDynamicPatcher()
        self._delegate_error = delegate_error
        self.cached_patcher_init = cached_patcher_init
        self.patches = {"a": [1, 2]}
        self.patches_uuid = "uuid-1"
        self.load_device = "cuda:0"
        self.offload_device = "cpu"
        self._size = 5120

    def is_dynamic(self):
        return True

    def model_size(self):
        return self._size

    def get_non_dynamic_delegate(self):
        if self.cached_patcher_init is None:
            raise RuntimeError("Cannot create non-dynamic delegate: cached_patcher_init is not initialized.")
        if self._delegate_error is not None:
            raise self._delegate_error
        return self._delegate


class _FakeNonDynamicPatcher:
    """非动态 patcher：持有 .model（对应 vae.first_stage_model 应同步指向的对象）。"""

    def __init__(self, model=None):
        self.model = model if model is not None else types.SimpleNamespace(
            __class__=type("DelegateModel", (), {})
        )

    def is_dynamic(self):
        return False


class _FakeVae:
    def __init__(self, patcher):
        self.patcher = patcher
        self.disable_offload = False
        self.first_stage_model = types.SimpleNamespace(__class__=type("MiniMaxH3VideoVAE", (), {}))

    def get_sd(self):
        return {"fake.weight": 1}

    def throw_exception_if_invalid(self):
        if getattr(self, "first_stage_model", None) is None:
            raise RuntimeError("VAE is invalid")


def _install_comfy_stubs(sd_module, mp_module):
    """注入 comfy 包 + comfy.sd + comfy.model_patcher 桩；返回恢复函数。"""
    comfy = types.ModuleType("comfy")
    comfy.__path__ = []
    comfy.sd = sd_module
    comfy.model_patcher = mp_module
    old = {
        "comfy": sys.modules.get("comfy"),
        "comfy.sd": sys.modules.get("comfy.sd"),
        "comfy.model_patcher": sys.modules.get("comfy.model_patcher"),
    }
    sys.modules["comfy"] = comfy
    sys.modules["comfy.sd"] = sd_module
    sys.modules["comfy.model_patcher"] = mp_module

    def restore():
        for name, mod in old.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    return restore


class VaeResidencyTest(unittest.TestCase):
    def tearDown(self):
        vae_residency._ND_DELEGATE_CACHE.clear()

    # 1. 幂等
    def test_non_dynamic_is_noop(self):
        vae = _FakeVae(_FakeNonDynamicPatcher())
        self.assertFalse(vae_residency.ensure_vae_resident(vae))
        self.assertIsInstance(vae.patcher, _FakeNonDynamicPatcher)
        self.assertFalse(vae.disable_offload)

    # 2. 动态 + 工厂
    def test_dynamic_uses_official_delegate(self):
        nd = _FakeNonDynamicPatcher()
        dyn = _FakeDynamicPatcher(delegate=nd, cached_patcher_init=("f", ("D:/vae.safetensors", None, None)))
        vae = _FakeVae(dyn)
        self.assertTrue(vae_residency.ensure_vae_resident(vae))
        self.assertIs(vae.patcher, nd)
        # #486 核心断言：forward 用 first_stage_model、load 用 patcher.model，
        # 两者必须同一对象，否则音频 VAE conv1d 报 Input/weight device 不匹配。
        self.assertIs(vae.first_stage_model, nd.model)
        self.assertTrue(vae.disable_offload)

    # 3. 缓存复用（同 key 不重建）
    def test_cache_reuse_same_key(self):
        calls = {"n": 0}

        class CountingDelegate(_FakeNonDynamicPatcher):
            pass

        class CountingDynamic(_FakeDynamicPatcher):
            def get_non_dynamic_delegate(self):
                calls["n"] += 1
                return CountingDelegate()

        vae1 = _FakeVae(CountingDynamic(cached_patcher_init=("f", ("D:/vae.safetensors", None, None))))
        vae2 = _FakeVae(CountingDynamic(cached_patcher_init=("f", ("D:/vae.safetensors", None, None))))
        self.assertTrue(vae_residency.ensure_vae_resident(vae1))
        self.assertTrue(vae_residency.ensure_vae_resident(vae2))
        self.assertEqual(calls["n"], 1, "同 vae 路径只应重建一次（缓存复用）")
        # 两个 vae 共享缓存 patcher → first_stage_model 也必须指向同一 delegate.model
        self.assertIs(vae1.first_stage_model, vae1.patcher.model)
        self.assertIs(vae2.first_stage_model, vae2.patcher.model)
        self.assertIs(vae1.first_stage_model, vae2.first_stage_model)

    # 4a. 工厂缺失 → 回退 get_sd 重建
    def test_fallback_when_factory_missing(self):
        dyn = _FakeDynamicPatcher(cached_patcher_init=None)

        class FakeSD(types.ModuleType):
            def VAE(self, sd=None, metadata=None):
                vae = _FakeVae(_FakeNonDynamicPatcher())
                vae.first_stage_model = types.SimpleNamespace(__class__=type("FreshModel", (), {}))
                return vae

        class FakeMP(types.ModuleType):
            class ModelPatcher:
                def __init__(self, model, load_device=None, offload_device=None, size=0, weight_inplace_update=False):
                    self.model = model
                    self.is_dynamic_flag = False
                    self.patches = {}
                    self.patches_uuid = None

                def is_dynamic(self):
                    return self.is_dynamic_flag

        restore = _install_comfy_stubs(FakeSD("comfy.sd"), FakeMP("comfy.model_patcher"))
        try:
            vae = _FakeVae(dyn)
            self.assertTrue(vae_residency.ensure_vae_resident(vae))
            self.assertIsInstance(vae.patcher, FakeMP.ModelPatcher)
            # 回退路径：first_stage_model 必须指向新重建模型的同一对象
            self.assertIs(vae.first_stage_model, vae.patcher.model)
            self.assertTrue(vae.disable_offload)
        finally:
            restore()

    # 4b. 工厂抛错 → 回退 get_sd 重建
    def test_fallback_when_delegate_raises(self):
        dyn = _FakeDynamicPatcher(
            delegate_error=RuntimeError("cached_patcher_init not initialized"),
            cached_patcher_init=("f", ("D:/vae.safetensors", None, None)),
        )

        class FakeSD(types.ModuleType):
            def VAE(self, sd=None, metadata=None):
                return _FakeVae(_FakeNonDynamicPatcher())

        class FakeMP(types.ModuleType):
            class ModelPatcher:
                def __init__(self, model, load_device=None, offload_device=None, size=0, weight_inplace_update=False):
                    self.model = model
                    self.is_dynamic_flag = False
                    self.patches = {}
                    self.patches_uuid = None

                def is_dynamic(self):
                    return self.is_dynamic_flag

        restore = _install_comfy_stubs(FakeSD("comfy.sd"), FakeMP("comfy.model_patcher"))
        try:
            vae = _FakeVae(dyn)
            self.assertTrue(vae_residency.ensure_vae_resident(vae))
            # 回退路径：first_stage_model 同步到新重建模型
            self.assertIs(vae.first_stage_model, vae.patcher.model)
            self.assertTrue(vae.disable_offload)
        finally:
            restore()

    # 5. 重建失败 → 返回 False 不破坏原状态
    def test_build_failure_is_noop(self):
        dyn = _FakeDynamicPatcher(cached_patcher_init=None)

        class BadSD(types.ModuleType):
            def VAE(self, sd=None, metadata=None):
                raise RuntimeError("boom")

        class FakeMP(types.ModuleType):
            class ModelPatcher:
                def __init__(self, *args, **kwargs):
                    pass

        restore = _install_comfy_stubs(BadSD("comfy.sd"), FakeMP("comfy.model_patcher"))
        try:
            vae = _FakeVae(dyn)
            orig_model = vae.first_stage_model
            self.assertFalse(vae_residency.ensure_vae_resident(vae))
            self.assertIs(vae.patcher, dyn)  # 原 patcher 不被改动
            self.assertIs(vae.first_stage_model, orig_model)  # first_stage_model 也不被动
            self.assertFalse(vae.disable_offload)
        finally:
            restore()

    # 6. vae 无 patcher → False
    def test_no_patcher(self):
        vae = types.SimpleNamespace()
        self.assertFalse(vae_residency.ensure_vae_resident(vae))


if __name__ == "__main__":
    unittest.main(verbosity=2)
