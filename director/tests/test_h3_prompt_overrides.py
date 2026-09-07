#!/usr/bin/env python3
"""P0-A（#477）五区 overrides → DirectorIntent 覆盖 + H3 Prompt 重建测试。

覆盖（用户拍板「提交时实时重建三段式」）：
1. build_shot_intent(overrides=…) 字段映射：
   - visual     → composition（替换 AI 构图建议；provenance=USER）
   - cameraText → continuity.camera_desc（替换规则相机措辞，含首镜）
   - style      → style 字段（_build_description 追加「风格：」）
   - soundText  → audio.ambient（替换规则环境音；对白原文逐字保留）
2. ⛔ 原文不可覆盖铁律：user_original_intent / retained_facts 逐字不动、
   action 不动、对白 dialogue 逐字进描述区 <d> 块（#545 修复 D，绝不放 soundscape）
   —— 最终 H3 Prompt 可追溯回原始小说。
3. build_plan_intents(overrides_by_shot=…) 按 {scene_id:shot_id} 逐镜透传。
4. /prompt/h3 路由包装 _build_plan_h3_prompts_for_route：无 overrides 时行为不变
   （向后兼容）；带 overrides 时三段式反映编辑。
5. provenance：composition=USER 归「规则生成」桶（非 AI 补全）。

直接用系统 python 运行（纯规则零 LLM）：
    python3 director/tests/test_h3_prompt_overrides.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ---------- 依赖桩（仅解决 http_routes.py 顶层 ComfyUI 专属依赖无法在纯 Python 导入）----------
# 参考 test_generation_archive_route.py：在导入 http_routes 之前把 folder_paths /
# aiohttp.web / server.PromptServer / torch 桩进 sys.modules。不改任何生产代码。
import tempfile  # noqa: E402
import types  # noqa: E402

_FOLDER_PATHS = types.ModuleType("folder_paths")
_TEMP_DIR = tempfile.mkdtemp(prefix="minimax_h3_override_tmp_")
_FOLDER_PATHS.get_output_directory = lambda: tempfile.mkdtemp(prefix="minimax_h3_override_out_")
_FOLDER_PATHS.get_input_directory = lambda: tempfile.mkdtemp(prefix="minimax_h3_override_in_")
_FOLDER_PATHS.get_temp_directory = lambda: _TEMP_DIR  # http_routes 模块级 CHUNK_ROOT 需要
sys.modules.setdefault("folder_paths", _FOLDER_PATHS)


class _Resp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def json(self):
        return self.payload


def _json_response(data, status=200):
    return _Resp(data, status=status)


def _response(text, status=200):
    return _Resp(text, status=status)


_web = types.ModuleType("aiohttp.web")
_web.Response = _response
_web.json_response = _json_response
_aiohttp = types.ModuleType("aiohttp")
_aiohttp.web = _web
sys.modules.setdefault("aiohttp", _aiohttp)

# server 桩：http_routes 顶层 import PromptServer（不调用 register_routes）。
_server = types.ModuleType("server")
_server.PromptServer = types.SimpleNamespace(instance=None)
sys.modules.setdefault("server", _server)

# torch：优先用真实 torch（stream_export 模块级函数默认参数在 import 期求值
# torch.float32，空 stub 会 AttributeError——见 #597 排查）；仅当环境真无 torch
# 时才退化为带常用属性占位的最小 stub，避免 import 链崩溃。
try:  # noqa: E402
    import torch as _torch  # noqa: E402

    sys.modules["torch"] = _torch
except ImportError:  # pragma: no cover - 纯环境兜底
    _torch = types.ModuleType("torch")
    _torch.float32 = "torch.float32"
    _torch.float64 = "torch.float64"
    _torch.Tensor = object
    sys.modules.setdefault("torch", _torch)

from director import director_intent as di  # noqa: E402
from director import h3_prompt_builder as h3  # noqa: E402
from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    Entity,
    EntitySource,
    EntityType,
    IntentSource,
    ProductionPlan,
    ProjectInfo,
    Scene,
    Shot,
    Validation,
)

# 《山雨客栈》真实镜头原文（回归基准）
SHOT1_SRC = ("雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，"
             "湿漉漉的石阶映着暖光，门前老槐树滴着水珠。")
SHOT4_SRC = ("柳如烟放下茶碗，从柜台后绕过，端着一碗热茶走向堂中，语气平淡："
             "「客官，落座歇脚，茶先暖着。」")

PASSED = 0
FAILED = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name} {detail}")


def _plan() -> ProductionPlan:
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼外", location_name="山雨楼外",
            time="黄昏", weather="雨",
            shots=[
                Shot(shot_id="shot_01", source_text=SHOT1_SRC, duration_sec=5,
                     entities=[
                         Entity(name="山雨楼", type=EntityType.ARCHITECTURE,
                                source=EntitySource.SCRIPT, confidence=0.95),
                     ]),
                Shot(shot_id="shot_04", source_text=SHOT4_SRC, duration_sec=5,
                     characters=[Character(name="柳如烟")],
                     dialogue=[Dialogue(speaker="柳如烟", text="客官，落座歇脚，茶先暖着。")],
                     entities=[
                         Entity(name="柳如烟", type=EntityType.CHARACTER,
                                source=EntitySource.SCRIPT, confidence=0.95),
                     ]),
            ],
        )],
        validation=Validation(),
    )


# 用户编辑后的五区内容（模拟用户在 Workbench 编辑画面/摄影/风格/声音）
_OVERRIDES = {
    "visual": "山雨楼外雨雾未散，门前老槐树在暮色里垂着水珠，屋檐灯笼的暖光映在石阶上。",
    "cameraText": "全景俯拍缓缓推近，从檐角灯笼推向门前石阶。",
    "style": "写实水墨质感，冷蓝与暖黄冷暖对比。",
    "soundText": "雨滴从屋檐滴落的声音，远处林间雾气涌动的低响。",
}


def _intents_with_overrides():
    return di.build_plan_intents(_plan(), overrides_by_shot={
        "scene_01:shot_01": _OVERRIDES,
        # shot_04 不覆盖 → 验证向后兼容
    })


def test_shot_intent_overrides_mapping() -> None:
    print("\n[1] build_shot_intent overrides 字段映射")
    intents = _intents_with_overrides()
    it = intents["scene_01:shot_01"]
    _check("visual → composition 被替换", it.composition == _OVERRIDES["visual"],
           f"got={it.composition!r}")
    _check("composition provenance=USER",
           it.provenance.get("composition") == IntentSource.USER,
           str(it.provenance.get("composition")))
    _check("cameraText → continuity.camera_desc",
           it.continuity.get("camera_desc") == _OVERRIDES["cameraText"],
           str(it.continuity.get("camera_desc")))
    _check("style → style 字段", it.style == _OVERRIDES["style"], it.style)
    _check("soundText → audio.ambient", it.audio.get("ambient") == _OVERRIDES["soundText"],
           str(it.audio.get("ambient")))
    _check("audio provenance=USER", it.provenance.get("audio") == IntentSource.USER,
           str(it.provenance.get("audio")))


def test_original_never_overridden() -> None:
    print("\n[2] ⛔ 原文不可覆盖铁律")
    intents = _intents_with_overrides()
    it = intents["scene_01:shot_01"]
    _check("user_original_intent 逐字保底", it.user_original_intent == SHOT1_SRC,
           it.user_original_intent)
    _check("retained_facts 逐字不动", any("雨刚停" in f for f in it.retained_facts),
           str(it.retained_facts))
    _check("location 规则字段不变", it.location == "山雨楼外", it.location)
    # shot_04 无 overrides：composition 仍为 plan visual_intent（空/未覆盖）
    it4 = intents["scene_01:shot_04"]
    _check("未覆盖镜头 provenance 保持 AI", it4.provenance.get("composition") == IntentSource.AI,
           str(it4.provenance.get("composition")))


def test_plan_intents_threading_key() -> None:
    print("\n[3] build_plan_intents overrides_by_shot 逐镜透传 + 缺省向后兼容")
    intents = _intents_with_overrides()
    _check("shot_01 覆盖生效", intents["scene_01:shot_01"].style == _OVERRIDES["style"])
    it4 = intents["scene_01:shot_04"]
    _check("shot_04 无覆盖时 style 为空", it4.style == "", it4.style)
    _check("shot_04 无覆盖时 composition 非用户",
           it4.provenance.get("composition") != IntentSource.USER)
    # 无 overrides_by_shot → 与旧行为完全一致
    plain = di.build_plan_intents(_plan())
    _check("缺省调用 style 为空", plain["scene_01:shot_01"].style == "")
    _check("缺省调用 composition 为空",
           plain["scene_01:shot_01"].composition == "",
           plain["scene_01:shot_01"].composition)


def test_h3_prompt_reflects_overrides() -> None:
    print("\n[4] H3 Prompt 三段式反映编辑 + 原文仍可追溯")
    intents = _intents_with_overrides()
    out = h3.build_plan_h3_prompts(
        intents, plan=_plan(), duration_by_shot={"scene_01:shot_01": 5.0, "scene_01:shot_04": 5.0},
    )["scene_01:shot_01"]
    desc = out.integrated_multimodal_description
    _check("visual 覆盖文本进描述", _OVERRIDES["visual"] in desc, desc)
    _check("style 覆盖文本进描述（风格：）", "风格：" in desc and _OVERRIDES["style"] in desc, desc)
    _check("cameraText 覆盖进描述（镜头：）",
           "镜头：" in desc and "缓缓推近" in desc, desc)
    _check("原文事实仍在描述（可追溯）", any("雨刚停" in f for f in out.provenance[
        "integrated_multimodal_description"]["user_facts"]), desc)
    _check("soundText 覆盖进 soundscape", _OVERRIDES["soundText"] in out.overall_soundscape,
           out.overall_soundscape)
    _check("composition 覆盖归规则生成桶（非 AI）",
           any("山雨楼外雨雾未散" in s for s in out.provenance[
               "integrated_multimodal_description"]["rule_generated"]),
           str(out.provenance["integrated_multimodal_description"]["rule_generated"]))


def test_dialogue_verbatim_after_sound_override() -> None:
    print("\n[5] soundText 覆盖后对白原文逐字保留（#545 修复 D：进描述区 <d> 块，不进音景）")
    # 对 shot_04 只覆盖 soundText（无对白改动）→ 对白仍逐字进描述区 <d> 块，
    # soundscape 只含覆盖的环境音（官方格式：对白绝不放音景）。
    intents = di.build_plan_intents(_plan(), overrides_by_shot={
        "scene_01:shot_04": {"soundText": "茶碗轻轻放回柜台的瓷响。"},
    })
    out = h3.build_h3_prompt(intents["scene_01:shot_04"])
    desc = out.integrated_multimodal_description
    sound = out.overall_soundscape
    _check("对白原文逐字进 <d> 块",
           "<d>[Chinese] 客官，落座歇脚，茶先暖着。</d>" in desc, desc)
    _check("speaker 稳定 ID", "(S1)柳如烟说，" in desc, desc)
    _check("soundscape 无对白",
           "客官" not in sound and "柳如烟：「" not in sound, sound)
    _check("覆盖音效进 soundscape", "茶碗轻轻放回柜台的瓷响" in sound, sound)


def test_route_wrapper_backward_compat() -> None:
    print("\n[6] /prompt/h3 路由包装：无 overrides 行为不变")
    from director.http_routes import _build_plan_h3_prompts_for_route  # noqa: PLC0415
    # 与真实路由 minimax_director_prompt_h3 一致：body.plan(dict) 先 from_dict 转 dataclass
    # 再进 _build_plan_h3_prompts_for_route（build_shot_intent 按 dataclass 访问属性）。
    plan = ProductionPlan.from_dict(_plan().to_dict())
    base = _build_plan_h3_prompts_for_route(plan, None, None)
    _check("schema 正确", base["schema"] == "minimax-h3-project-v1")
    _check("无 overrides 时 prompts 键齐全", "scene_01:shot_01" in base["prompts"],
           str(list(base["prompts"].keys())))
    # 带 overrides → 三段式反映编辑
    ov = _build_plan_h3_prompts_for_route(
        plan, None, None, {"scene_01:shot_01": _OVERRIDES},
    )
    item = ov["prompts"]["scene_01:shot_01"]
    _check("visual 覆盖进集成描述", _OVERRIDES["visual"] in item["integrated_multimodal_description"],
           item["integrated_multimodal_description"][-100:])
    _check("style 覆盖进集成描述", "风格：" in item["integrated_multimodal_description"])


def main() -> None:
    test_shot_intent_overrides_mapping()
    test_original_never_overridden()
    test_plan_intents_threading_key()
    test_h3_prompt_reflects_overrides()
    test_dialogue_verbatim_after_sound_override()
    test_route_wrapper_backward_compat()
    print(f"\n合计：{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
