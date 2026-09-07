#!/usr/bin/env python3
"""VM 测试环境补丁：stub ComfyUI 外部依赖（comfy / server / folder_paths）。

仅在 pytest 收集时生效（ComfyUI 运行时不会加载本文件）。
根包 __init__.py 会导入 nodes.director → `import comfy.samplers`，
VM 无 ComfyUI 环境，故 stub 空模块让测试可收集；被测逻辑（director/
entity_cleanse / asset_matcher / http_routes 等）不依赖这些符号。
"""

from __future__ import annotations

import os
import shutil
import sys
import types

# 假输入/临时/输出根（与下方 folder_paths 桩保持一致；pytest 会话开始清空，
# 杜绝跨运行残留项目目录污染 list_projects 排序——#541 验收实测 1 failed 根因）。
_FAKE_INPUT_DIR = "/tmp/fake_input"
_FAKE_TEMP_DIR = "/tmp/fake_temp"
_FAKE_OUTPUT_DIR = "/tmp/fake_output"


def pytest_sessionstart(session) -> None:
    """每次 pytest 会话启动时清掉上次运行的假根残留（Windows 上即 C:\\tmp\\fake_*）。"""
    for _dir in (_FAKE_INPUT_DIR, _FAKE_TEMP_DIR, _FAKE_OUTPUT_DIR):
        if os.path.exists(_dir):
            shutil.rmtree(_dir, ignore_errors=True)

if "comfy" not in sys.modules:
    _comfy = types.ModuleType("comfy")
    _samplers = types.ModuleType("comfy.samplers")
    _samplers.sample = None
    _utils = types.ModuleType("comfy.utils")
    _utils.common_upscale = lambda *a, **k: None
    _comfy.samplers = _samplers
    _comfy.utils = _utils
    sys.modules["comfy"] = _comfy
    sys.modules["comfy.samplers"] = _samplers
    sys.modules["comfy.utils"] = _utils

if "server" not in sys.modules:
    _server = types.ModuleType("server")
    _server.PromptServer = type("PromptServer", (), {"instance": None})
    sys.modules["server"] = _server

if "folder_paths" not in sys.modules:
    _fp = types.ModuleType("folder_paths")
    _fp.get_input_directory = lambda: _FAKE_INPUT_DIR
    _fp.get_temp_directory = lambda: _FAKE_TEMP_DIR
    _fp.get_output_directory = lambda: _FAKE_OUTPUT_DIR
    sys.modules["folder_paths"] = _fp