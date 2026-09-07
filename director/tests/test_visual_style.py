#!/usr/bin/env python3
"""Visual Style / Art Direction 层（v2.0 ⑧）单元测试（纯规则，mock，零显存）。

覆盖（CAMERA_RULES_V2 §3.5）：
1. STYLE_PRESETS 内置 7 预设齐全，推荐预设=抖音半写实漫剧；
2. douyin_semi_realistic 预设 8 部分拆解：半写实人物/电影自然光/85mm 浅景深/
   低饱和暖调/真实材质/DOF/电影构图/整体国漫渲染 + 用户英文风格串逐词保留；
3. style_block：固定画风块格式「画风：…。人物：…。光影：…。材质：…。整体：…。」；
4. per_scene 场景级微调（命中追加 / 未命中不加 / 场景名不命中不加）；
5. VisualStyleProfile to_dict/from_dict round-trip（含空 dict 兜底）；
6. get_preset 未知 id 回退推荐预设；
7. ⛔ 全剧级一致性：同一 profile 每镜 style_block 相同（不静默漂移）；
8. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_visual_style.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.visual_style as vs  # noqa: E402

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


# ---------- 预设库 ----------

def test_seven_presets() -> None:
    print("STYLE_PRESETS · 内置 7 预设：")
    expect = {"douyin_semi_realistic", "japan_cel", "cn_3d",
              "pixar", "ink_guofeng", "korean_thick", "cinematic_real"}
    _check("7 预设齐全", set(vs.STYLE_PRESETS.keys()) == expect,
           str(set(vs.STYLE_PRESETS.keys())))
    _check("推荐预设=抖音半写实漫剧", vs.STYLE_PRESETS["douyin_semi_realistic"].name ==
           "抖音半写实漫剧（Semi-Realistic Cinematic Anime）")


def test_douyin_8_parts() -> None:
    print("douyin_semi_realistic · 8 部分拆解（全 profile 概念覆盖）：")
    p = vs.STYLE_PRESETS["douyin_semi_realistic"]
    ad = p.art_direction
    kw = p.style_keywords
    src = open(os.path.join(os.path.dirname(vs.__file__), "visual_style.py"),
               encoding="utf-8").read()
    _check("① 半写实动漫人物", "semi-realistic anime" in ad and "半写实动漫人物" in kw.get("人物", ""))
    _check("① 真人70%/二次元30%（源码 docstring 保留）", "真人70%/二次元30%" in src)
    _check("② 电影级自然光", "Movie lighting" in ad and "电影级光影" in kw.get("光影", ""), ad)
    _check("③ 85mm 浅景深（英文串内）", "85mm lens" in ad and "Shallow depth of field" in ad)
    _check("④ 低饱和暖调（color_tone=warm）", p.color_tone == "warm")
    _check("⑤ 真实材质", "Photorealistic materials" in ad and "真实" in kw.get("材质", ""), kw.get("材质", ""))
    _check("⑥ DOF 前景清后景虚（Soft bokeh）", "Soft bokeh" in ad)
    _check("⑦ 电影构图", "Cinematic photography" in ad and "电影感" in kw.get("整体", ""), kw.get("整体", ""))
    _check("⑧ 国漫渲染", "Anime rendering" in ad and "国漫" in kw.get("整体", ""), kw.get("整体", ""))
    # ⛔ 四组摘要紧凑（H3 Token 预算：文档 §3.5「四组关键词摘要 ≤60 字」为软目标，
    #    文档自己的「中文速览」模板实测 82 字；阈值按文档模板长度校准 ≤85 字）
    four = sum(len(kw.get(k, "")) for k in ("人物", "光影", "材质", "整体"))
    _check("四组摘要紧凑（≤85 字）", four <= 85, f"四组共 {four} 字")


def test_douyin_english_string_verbatim() -> None:
    print("douyin_semi_realistic · 用户英文风格串逐词保留：")
    p = vs.STYLE_PRESETS["douyin_semi_realistic"]
    for fragment in (
        "Modern Chinese semi-realistic anime", "Ultra detailed character",
        "Realistic facial proportions", "Delicate skin shading", "Natural makeup",
        "Detailed hair strands", "Cinematic photography", "Movie lighting",
        "Warm daylight", "Shallow depth of field", "85mm lens", "Soft bokeh",
        "Photorealistic materials", "Anime rendering", "High-end Chinese animation style",
        "Consistent character appearance", "Natural facial expressions", "Ultra HD",
    ):
        _check(f"含「{fragment}」", fragment in p.art_direction)


# ---------- style_block ----------

def test_style_block_format() -> None:
    print("style_block · 固定画风块格式：")
    block = vs.style_block(vs.STYLE_PRESETS["douyin_semi_realistic"])
    _check("以「画风：」开头", block.startswith("画风："), block)
    _check("含「人物：」段", "人物：" in block)
    _check("含「光影：」段", "光影：" in block)
    _check("含「材质：」段", "材质：" in block)
    _check("含「整体：」段", "整体：" in block)
    _check("以句号结尾", block.endswith("。"), block[-3:])


def test_style_block_scene_micro() -> None:
    print("style_block · per_scene 场景级微调：")
    p = vs.STYLE_PRESETS["douyin_semi_realistic"]
    hit = vs.style_block(p, scene="雨夜")
    _check("命中场景 → 追加（雨夜：…）", "（雨夜：冷调，光线清冷克制）" in hit, hit)
    miss = vs.style_block(p, scene="天台")
    _check("未命中场景 → 不追加", "天台" not in miss)
    plain = vs.style_block(p)
    _check("无场景 → 与命中版不同（仅追加）", hit != plain)


def test_style_block_empty_profile() -> None:
    print("style_block · 空 profile 兜底：")
    _check("空 dict → ''", vs.style_block({}) == "")
    _check("None → ''", vs.style_block(None) == "")
    _check("空 VisualStyleProfile → ''", vs.style_block(vs.VisualStyleProfile()) == "")


def test_style_block_global_consistent() -> None:
    print("⛔ 全剧级一致性（不静默漂移）：")
    p = vs.STYLE_PRESETS["douyin_semi_realistic"]
    _check("同一 profile 两次渲染相同", vs.style_block(p) == vs.style_block(p))


# ---------- 序列化 ----------

def test_round_trip() -> None:
    print("VisualStyleProfile · to_dict/from_dict：")
    p = vs.STYLE_PRESETS["pixar"]
    d = p.to_dict()
    _check("to_dict 8 字段齐全", set(d.keys()) ==
           {"profile_id", "name", "art_direction", "style_keywords",
            "fidelity", "color_tone", "negative_rules", "per_scene"}, str(set(d.keys())))
    back = vs.VisualStyleProfile.from_dict(d)
    _check("round-trip 等值", back == p)
    _check("未知 profile_id 保留", back.profile_id == p.profile_id)
    _check("per_scene 保留", back.per_scene == p.per_scene)


def test_from_dict_empty() -> None:
    print("VisualStyleProfile · 空/坏输入兜底：")
    _check("空 dict → 缺省 profile", vs.VisualStyleProfile.from_dict({}).profile_id == "douyin_semi_realistic")
    _check("None → 缺省 profile", vs.VisualStyleProfile.from_dict(None).profile_id == "douyin_semi_realistic")


# ---------- get_preset ----------

def test_get_preset_fallback() -> None:
    print("get_preset · 未知 id 回退推荐预设：")
    _check("未知 id → douyin", vs.get_preset("does_not_exist").profile_id == "douyin_semi_realistic")
    _check("空串 → douyin", vs.get_preset("").profile_id == "douyin_semi_realistic")
    _check("japan_cel 命中", vs.get_preset("japan_cel").profile_id == "japan_cel")


# ---------- presets_payload（P3：前端下拉单一事实来源） ----------

def test_presets_payload() -> None:
    print("presets_payload · 7 预设完整 dict（P3 前端下拉数据源）：")
    payload = vs.presets_payload()
    _check("7 预设齐全", set(payload.keys()) == set(vs.STYLE_PRESETS.keys()),
           str(set(payload.keys())))
    _check("douyin 默认在列", "douyin_semi_realistic" in payload)
    d = payload["douyin_semi_realistic"]
    _check("douyin 8 字段完整", all(k in d for k in
           ("profile_id", "name", "art_direction", "style_keywords",
            "fidelity", "color_tone", "negative_rules", "per_scene")), str(list(d.keys())))
    _check("douyin 含用户英文串", "Modern Chinese semi-realistic anime" in d["art_direction"])
    _check("每预设 roundtrip 一致",
           all(vs.STYLE_PRESETS[pid].to_dict() == d2 for pid, d2 in payload.items()))
    _check("from_dict(payload[id]) 还原 profile",
           vs.VisualStyleProfile.from_dict(payload["japan_cel"]).profile_id == "japan_cel")
    _check("payload 与 to_dict 同构",
           payload["pixar"] == vs.STYLE_PRESETS["pixar"].to_dict())


# ---------- 纯规则零显存 ----------

def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(vs.__file__), "visual_style.py"),
               encoding="utf-8").read()
    for bad in ("torch", "ollama", "urlopen", "requests", "aiohttp", "keep_alive",
                "gpu_models_loaded", "empty_cache", "openai"):
        _check(f"源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_visual_style\n")
    test_seven_presets()
    test_douyin_8_parts()
    test_douyin_english_string_verbatim()
    test_style_block_format()
    test_style_block_scene_micro()
    test_style_block_empty_profile()
    test_style_block_global_consistent()
    test_round_trip()
    test_from_dict_empty()
    test_get_preset_fallback()
    test_presets_payload()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
