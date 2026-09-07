#!/usr/bin/env python3
"""#486 _latent_meta 稳健提取回归测试（dict latent / 裸 tensor / 空 dict）。

背景：executor_core._decode_av_latent 的诊断打印曾直接 `video_latent.shape`，
而 ComfyUI 0.32.0 的 LTXVSeparateAVLatent.execute 返回 latent dict
（`{"samples": tensor, ...}`）→ `'dict' object has no attribute 'shape'` 崩。
修复后统一走 `_latent_meta`（dict 取 samples；裸 tensor 直接用；无 shape 返回 None）。

运行：
    python3 director/tests/test_latent_meta.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

# ---------- 依赖桩（与 test_asset_shot_boundary.py 同款，保证 executor_core 可导入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

_comfy = types.ModuleType("comfy")
_comfy.__path__ = []
_utils = types.ModuleType("comfy.utils")
_utils.common_upscale = lambda *a, **k: None
_comfy.utils = _utils
sys.modules.setdefault("comfy", _comfy)
sys.modules.setdefault("comfy.utils", _utils)

_TMP_IN = tempfile.mkdtemp(prefix="h3in_")
_fp = types.ModuleType("folder_paths")
_fp.get_input_directory = lambda: _TMP_IN
_fp.get_output_directory = lambda: tempfile.mkdtemp(prefix="h3out_")
_fp.get_temp_directory = lambda: tempfile.mkdtemp(prefix="h3tmp_")
_fp.models_dir = ""
_fp.get_annotated_filepath = lambda path, *a, **k: path
sys.modules.setdefault("folder_paths", _fp)

import torch  # noqa: E402
import unittest  # noqa: E402

from ComfyUI_MiniMaxH3_Director.director.executor_core import _latent_meta  # noqa: E402


class LatentMetaTest(unittest.TestCase):
    def test_dict_latent(self):
        # ComfyUI 标准 latent dict
        samples = torch.zeros(1, 24, 7, 30, 54, dtype=torch.bfloat16)
        shape, device, dtype = _latent_meta({"samples": samples})
        self.assertEqual(shape, (1, 24, 7, 30, 54))
        self.assertEqual(dtype, torch.bfloat16)

    def test_raw_tensor(self):
        t = torch.zeros(1, 24, 7, 30, 54)
        shape, _, _ = _latent_meta(t)
        self.assertEqual(shape, (1, 24, 7, 30, 54))

    def test_empty_dict(self):
        self.assertEqual(_latent_meta({}), (None, None, None))

    def test_dict_without_samples(self):
        self.assertEqual(_latent_meta({"foo": 1}), (None, None, None))

    def test_none(self):
        self.assertEqual(_latent_meta(None), (None, None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
