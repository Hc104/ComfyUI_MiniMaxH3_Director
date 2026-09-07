#!/usr/bin/env python3
"""P0-1 Shot 级资产边界硬测试（#473，cesi Shot 01 跨镜资产污染审计修复验收）。

验证的是**最终 generation refs**（不是只看 project.json）——两层断言：
  1. plan 层（gen_timeline.build_gen_director_plan）：SegmentPlan.extra_assets
     只含该段显式 propIds/styleIds 声明的道具/风格；未声明 propIds 的段
     （空镜/旧项目）绝不整池扩散场景素材组（铁律①：一个 Shot 拿不到其他
     Shot 的资产）。
  2. executor 层（executor_core.build_shot_asset_items）：cast/location/extra/tag
     折叠为最终资产注入列表，名字与「角色→场景→道具→风格→tag」注入顺序正确，
     并逐一断言禁止项（forbidden）绝不出现在该镜 refs。

Matrix（忠实《山雨客栈》语义的 synthetic fixtures）：
  Shot 01（空镜远景·山雨楼外·灯笼）：allowed 山雨楼外、灯笼 | forbidden 旧剑匣、伞、沈青崖
  Shot 02（沈青崖 草堆寻剑匣 + 打伞离店）：allowed 山雨楼外、沈青崖、旧剑匣、伞 | forbidden 柳如烟、蒙面人
  Shot 03（客栈大堂·柳如烟奉茶）：allowed 客栈大堂、柳如烟、茶碗/热茶 | forbidden 旧剑匣、伞、蒙面人

另覆盖：
  - backward-compat：无 propIds 字段的旧段 → extra_assets 为空（不扩散场景池）
  - V1.2.7 回归：<picture>显式标签仍能拉场景池资产进 tag_assets（已注入的 id 去重）
  - P0-4：script_parser 场景标题兜底 location 候选（「第X场：山雨楼外」→山雨楼外）

运行：
    python3 director/tests/test_asset_shot_boundary.py
或
    pytest director/tests/test_asset_shot_boundary.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

# ---------- 依赖桩（在导入 gen_timeline / executor_core 之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 顶层命名空间包（与 ComfyUI 运行时一致）。
_PKG = types.ModuleType("ComfyUI_MiniMaxH3_Director")
_PKG.__path__ = [_REPO_ROOT]
sys.modules.setdefault("ComfyUI_MiniMaxH3_Director", _PKG)

# comfy.utils 桩（lib/image_prep 顶层 import 用）。
_comfy = types.ModuleType("comfy")
_comfy.__path__ = []
_utils = types.ModuleType("comfy.utils")
_utils.common_upscale = lambda *a, **k: None
_comfy.utils = _utils
sys.modules.setdefault("comfy", _comfy)
sys.modules.setdefault("comfy.utils", _utils)

# folder_paths 桩（lib/video_io.py 顶层 import 用）：资产/参考图解析需要
# get_input_directory 可调用；测试资产不带真实图片文件（tensor 为 None 不崩）。
_TMP_IN = tempfile.mkdtemp(prefix="h3in_")
_fp = types.ModuleType("folder_paths")
_fp.get_input_directory = lambda: _TMP_IN
_fp.get_output_directory = lambda: tempfile.mkdtemp(prefix="h3out_")
_fp.get_temp_directory = lambda: tempfile.mkdtemp(prefix="h3tmp_")
_fp.models_dir = ""
_fp.get_annotated_filepath = lambda path, *a, **k: path
sys.modules.setdefault("folder_paths", _fp)

# 真实 torch（VM 有 torch 2.13.0+cpu）——build_gen_director_plan 会做真实张量运算。

import torch  # noqa: E402

from ComfyUI_MiniMaxH3_Director.director.executor_core import (  # noqa: E402
    build_shot_asset_items,
)
from ComfyUI_MiniMaxH3_Director.director.gen_timeline import (  # noqa: E402
    build_gen_director_plan,
)
from ComfyUI_MiniMaxH3_Director.director.plan import GlobalAsset  # noqa: E402
from ComfyUI_MiniMaxH3_Director.director.script_parser import (  # noqa: E402
    _location_candidate_from_title,
)

# r2v 完整中文标签（前端适配器 minimaxH3Adapter TASK_LABELS 透传到后端的形态）。
R2V_LABEL = "r2v — 参考主体生视频(Reference to Video)"


def _ga(aid: str, name: str, kind: str, tensor: bool = False) -> dict:
    """GlobalAsset fixture（dict 形态，给 timeline/scenes；tensor 可携带供 executor 直调）。"""
    return {"id": aid, "name": name, "kind": kind, "imageFile": f"{aid}.png"}


def _ga_obj(aid: str, name: str, kind: str) -> GlobalAsset:
    """GlobalAsset dataclass（executor 层直调用）。"""
    return GlobalAsset(
        id=aid,
        name=name,
        kind=kind,
        tensor=torch.full((1, 8, 8, 3), 0.5, dtype=torch.float32),
        image_file=f"{aid}.png",
    )


def _timeline() -> dict:
    """《山雨客栈》三镜 timeline（prompt_batch / r2v），忠实审计矩阵的 synthetic fixtures。"""
    return {
        "version": 5,
        "timelineMode": "prompt_batch",
        "editMode": "segment",
        "width": 144,
        "height": 80,
        "frameRate": 24.0,
        "assets": {
            "cast": [],
            "locations": [],
        },
        # 两个场景：山雨楼外（旧剑匣/伞/灯笼 道具 + 沈青崖）、客栈大堂（茶碗 + 柳如烟）。
        "scenes": [
            {
                "id": "scn_out",
                "name": "山雨楼外",
                "assets": {
                    "cast": [_ga("cast_shen", "沈青崖", "cast")],
                    "locations": [_ga("loc_outer", "山雨楼外", "location")],
                    "props": [
                        _ga("prop_sword", "旧剑匣", "prop"),
                        _ga("prop_umbrella", "伞", "prop"),
                        _ga("prop_lantern", "灯笼", "prop"),
                    ],
                    "styles": [],
                },
            },
            {
                "id": "scn_lobby",
                "name": "客栈大堂",
                "assets": {
                    "cast": [_ga("cast_liu", "柳如烟", "cast")],
                    "locations": [_ga("loc_lobby", "客栈大堂", "location")],
                    "props": [_ga("prop_tea", "茶碗/热茶", "prop")],
                    "styles": [],
                },
            },
        ],
        "global": {
            "taskType": R2V_LABEL,
            "prompt": "",
            "refs": [],
        },
        "output": {"mode": "fixed", "width": 144, "height": 80},
        "gen": {"defaultFrameCount": 124},
        "segments": [
            {
                # Shot 01：空镜远景。只显式绑定灯笼；绝不拿 Shot 03 才出现的旧剑匣/伞。
                "id": "shot01",
                "sceneId": "scn_out",
                "durationSec": 5,
                "frameCount": 124,
                "prompt": "空镜远景：山雨楼外，夜色雨幕，屋檐下一盏灯笼微微摇晃",
                "propIds": ["prop_lantern"],
            },
            {
                # Shot 02：沈青崖 草堆旁拾起旧剑匣、撑伞走出。显式绑定旧剑匣+伞。
                "id": "shot02",
                "sceneId": "scn_out",
                "durationSec": 5,
                "frameCount": 124,
                "prompt": "沈青崖拨开草堆捧起<picture>旧剑匣</picture>，撑开黑伞走入雨幕",
                "castIds": ["cast_shen"],
                "locationId": "loc_outer",
                "propIds": ["prop_sword", "prop_umbrella"],
            },
            {
                # Shot 03：客栈大堂，柳如烟 斟茶。茶碗/热茶入，旧剑匣/伞绝不出现在本镜。
                "id": "shot03",
                "sceneId": "scn_lobby",
                "durationSec": 5,
                "frameCount": 124,
                "prompt": "客栈大堂，柳如烟 端来<picture>热茶</picture>，雾气袅袅",
                "castIds": ["cast_liu"],
                "locationId": "loc_lobby",
                "propIds": ["prop_tea"],
            },
        ],
    }


def _build(timeline: dict):
    """调 build_gen_director_plan → 按 segments 顺序返回 SegmentPlan 列表。"""
    plan = build_gen_director_plan(
        timeline=timeline,
        global_task_type=R2V_LABEL,
        global_prompt="",
        total_frames=512,
        frame_rate=24.0,
        width=864,
        height=480,
        ref_max_size=864,
    )
    return plan.segments


def _ref_names(assets) -> list[str]:
    return [a.name for a in assets]


def _tensorized(assets) -> list[GlobalAsset]:
    """把 plan 层解析出的资产（tensor 可能为 None）换算成带真实 tensor 的
    GlobalAsset，供 executor 层 build_shot_asset_items 直调。名字/kind 沿用
    plan 层解析结果 → 断言的就是「该镜最终要注入 refs 的资产」。"""
    out: list[GlobalAsset] = []
    for a in assets or []:
        out.append(GlobalAsset(
            id=a.id,
            name=a.name,
            kind=a.kind,
            tensor=torch.full((1, 8, 8, 3), 0.5, dtype=torch.float32),
            image_file=a.image_file or "",
        ))
    return out


def _executor_items(cast, loc, extra, tag):
    """plan 层资产集合 → executor 注入列表（tensorized）。"""
    return build_shot_asset_items(
        _tensorized(cast),
        _tensorized([loc])[0] if loc is not None else None,
        _tensorized(extra),
        _tensorized(tag),
    )


def test_shot01_extra_only_explicit_lantern_no_pool_spread():
    segs = _build(_timeline())
    s1 = segs[0]
    # plan 层：extra_assets 只含显式 propIds 声明的「灯笼」→ allowed；旧剑匣/伞 绝不在。
    assert _ref_names(s1.extra_assets) == ["灯笼"]
    # 场景池其它道具（旧剑匣/伞）绝不整池扩散。
    forbidden = {a.name for a in s1.extra_assets} & {"旧剑匣", "伞"}
    assert forbidden == set()

    # executor 层：cast 无（空镜未出场角色），location=山雨楼外，extra=灯笼。
    items = _executor_items(s1.cast_assets, s1.location_asset, s1.extra_assets, s1.tag_assets)
    names = [n for _lab, n, _t in items]
    assert names == ["山雨楼外", "灯笼"], names
    # forbidden 命名检查（铁律①：Shot 01 绝不出现 旧剑匣/伞/沈青崖）。
    assert "旧剑匣" not in names and "伞" not in names and "沈青崖" not in names


def test_shot02_gets_own_cast_sword_umbrella():
    segs = _build(_timeline())
    s2 = segs[1]
    # plan 层：cast=[沈青崖]，location=山雨楼外，extra=[旧剑匣, 伞]（按 propIds 声明顺序）。
    assert _ref_names(s2.cast_assets) == ["沈青崖"]
    assert s2.location_asset is not None and s2.location_asset.name == "山雨楼外"
    assert _ref_names(s2.extra_assets) == ["旧剑匣", "伞"]

    # executor 层：角色→场景→道具→tag。
    items = _executor_items(s2.cast_assets, s2.location_asset, s2.extra_assets, s2.tag_assets)
    names = [n for _lab, n, _t in items]
    assert names == ["沈青崖", "山雨楼外", "旧剑匣", "伞"], names
    # forbidden：柳如烟（另一场景角色）、蒙面人 绝不出现。
    assert "柳如烟" not in names and "蒙面人" not in names


def test_shot03_gets_tea_only_not_sword_umbrella():
    segs = _build(_timeline())
    s3 = segs[2]
    assert _ref_names(s3.cast_assets) == ["柳如烟"]
    assert s3.location_asset is not None and s3.location_asset.name == "客栈大堂"
    assert _ref_names(s3.extra_assets) == ["茶碗/热茶"]

    items = _executor_items(s3.cast_assets, s3.location_asset, s3.extra_assets, s3.tag_assets)
    names = [n for _lab, n, _t in items]
    assert names == ["柳如烟", "客栈大堂", "茶碗/热茶"], names
    assert "旧剑匣" not in names and "伞" not in names and "蒙面人" not in names


def test_backward_compat_no_prop_ids_no_pool_spread():
    """旧 timeline 段字段没有 propIds → 绝不回退「场景池全量注入」（审计根因行为）。"""
    tl = _timeline()
    # 清掉所有 propIds（模拟旧项目/手写 timeline）。
    for seg in tl["segments"]:
        seg.pop("propIds", None)
    segs = _build(tl)
    for s in segs:
        assert s.extra_assets == [], f"segment {s.id} leaked scene pool: {_ref_names(s.extra_assets)}"


def test_media_tag_still_pulls_scene_pool_asset():
    """V1.2.7 回归：<picture> 显式标签仍能拉场景池资产进 tag_assets（不被 P0-1 破坏）。

    Shot 02 的 <picture>旧剑匣</picture> 已通过 propIds 显式注入 → id 去重不进 tag；
    且 cast 已是自动注入。这里独立验证：未显式 propIds 的「灯笼」标签在各段工作。
    """
    tl = _timeline()
    # Shot 01：无 propIds，但在 prompt 显式 <picture>灯笼</picture> → tag_assets 补入灯笼。
    tl["segments"][0] = {
        "id": "shot01b",
        "sceneId": "scn_out",
        "frameCount": 124,
        "prompt": "空镜远景：山雨楼外，屋檐下<picture>灯笼</picture>微微摇晃",
    }
    segs = _build(tl)
    s1 = segs[0]
    # extra_assets（无显式 propIds）= []（P0-1 边界生效），灯笼走 tag 注入。
    assert s1.extra_assets == []
    assert _ref_names(s1.tag_assets) == ["灯笼"]
    items = _executor_items(s1.cast_assets, s1.location_asset, s1.extra_assets, s1.tag_assets)
    names = [n for _lab, n, _t in items]
    assert names == ["山雨楼外", "灯笼"], names


def test_location_title_fallback_parser_p04():
    """P0-4：场景标题兜底 location 候选。「第一场：山雨楼外」→ 山雨楼外。"""
    assert _location_candidate_from_title("第一场：山雨楼外") == "山雨楼外"
    assert _location_candidate_from_title("第2场 山雨楼外") == "山雨楼外"
    # 纯编号标题 → 不猜测地点。
    assert _location_candidate_from_title("尾声") == ""
    assert _location_candidate_from_title("第一场") == ""
    # 时间/天气提示剔除后仍回地点（「清晨的站台」→「站台」）。
    assert _location_candidate_from_title("第四场：清晨的站台") == "站台"


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"\n{len(tests)} tests OK")


if __name__ == "__main__":
    _main()