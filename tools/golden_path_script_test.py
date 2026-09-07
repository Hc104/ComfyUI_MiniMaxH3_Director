#!/usr/bin/env python3
"""Golden Path 真实验收：剧本 → AI 制作计划（V1.7 全链路）。

《山雨客栈》原始剧本 → 规则拆 Scene/Shot（3 场景 9 镜）→ Ollama Qwen 语义补全
（analyze_script 单 session）→ 资产自动匹配（对照 minimax_studio/assets 山雨客栈 8 图）
→ Prompt Builder 五区草稿（Phase 3 模板 + Phase 5 运镜模板 + 跨镜连贯状态机）
→ ProductionPlan 校验 → 退出卸载（keep_alive=0 + /api/ps）+ 互斥门复位。

用法（Windows 本机，Ollama 需已启动且 `ollama list` 里有 qwen3:14b）：
  A. ComfyUI Desktop Python（推荐，含 GPU registry 检查）：
     "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" tools\\golden_path_script_test.py
  B. 系统 Python（跳过 ComfyUI registry 检查）：
     python tools\\golden_path_script_test.py
  离线自测（不连 Ollama，用内置 FakeTextBackend 走全链路）：
     python tools\\golden_path_script_test.py --mock \
        --script "<山雨客栈.md 路径>" --assets-root "<minimax_studio/assets 目录>"

退出码 0 = 全部 PASS；1 = 有 FAIL。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import ProductionPlan
from director.script_parser import parse_script_file
from director.script_analyzer import analyze_script
from director.asset_matcher import match_plan, scan_asset_library
from director.prompt_builder_v17 import build_plan_drafts
from director.camera_template import build_plan_cameras, list_camera_templates
from director.text_backends import (
    TextBackend,
    create_default_text_backend,
    gpu_models_loaded,
    text_backend_active,
)

# ---------------------------------------------------------------------------
# 验收预期（《山雨客栈》设计稿 V1.6x_PROJECT2：3 场景 9 镜）
# ---------------------------------------------------------------------------
EXPECTED_SCENES = 3
EXPECTED_SHOTS_PER_SCENE = {1: 2, 2: 6, 3: 1}
EXPECTED_TOTAL_SHOTS = 9

# 山雨客栈 8 张资产图（本次验收应能在共享资产库扫到）
KEY_ASSETS = (
    "沈青崖", "柳如烟", "蒙面人",          # 角色
    "山雨楼外", "客栈大堂", "后院石阶",    # 场景
    "旧剑匣", "古风水墨",                  # 道具 + 风格
)

# 运镜措辞应含的运动词（Phase 5 模板库保证至少命中一个）
CAMERA_MOVEMENT_WORDS = (
    "推", "拉", "摇", "移", "升降", "环绕", "俯", "仰",
    "跟", "转", "弧线", "推进", "拉远", "运动", "特写",
)
# 场景切换重置判定：场景首镜 prev_state=None → 前缀必为空
CONTINUITY_PREFIXES = ("承接上镜", "保持近景", "反打对切")

PASS_MSG = "  ✓ PASS"
FAIL_MSG = "  ✗ FAIL"

STUDIO_SUBDIR = os.path.join("minimax_studio", "scripts")
ASSETS_SUBDIR = os.path.join("minimax_studio", "assets")

# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def _resolve_script_path(explicit: str) -> str:
    """剧本文件路径：--script / SHAN_YU_SCRIPT env / ComfyUI input / 本机候选。"""
    cands = [explicit, os.environ.get("SHAN_YU_SCRIPT", "")]
    try:
        import folder_paths  # noqa: PLC0415 - ComfyUI 环境

        cands.append(os.path.join(
            folder_paths.get_input_directory(), STUDIO_SUBDIR, "山雨客栈.md"))
    except Exception:
        pass
    # 本机已知候选（用户实际部署目录）
    cands += [
        r"D:\Comfy-Desktop\ComfyUI-Shared\input\minimax_studio\scripts\山雨客栈.md",
        os.path.join(_REPO_ROOT, "..", "ComfyUI-Shared", "input", "minimax_studio",
                     "scripts", "山雨客栈.md"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "找不到《山雨客栈》剧本，请用 --script 指定，或设 SHAN_YU_SCRIPT 环境变量。"
        "候选: " + " | ".join(repr(c) for c in cands if c)
    )


def _resolve_assets_root(explicit: str) -> str:
    """资产库目录：--assets-root / MINIMAX_STUDIO_ASSETS env / ComfyUI input。"""
    cands = [explicit, os.environ.get("MINIMAX_STUDIO_ASSETS", "")]
    try:
        import folder_paths  # noqa: PLC0415

        cands.append(os.path.join(folder_paths.get_input_directory(), ASSETS_SUBDIR))
    except Exception:
        pass
    cands += [
        r"D:\Comfy-Desktop\ComfyUI-Shared\input\minimax_studio\assets",
        os.path.join(_REPO_ROOT, "..", "ComfyUI-Shared", "input", "minimax_studio",
                     "assets"),
    ]
    for c in cands:
        if c and os.path.isdir(c):
            return c
    raise FileNotFoundError(
        "找不到共享资产库，请用 --assets-root 指定，或设 MINIMAX_STUDIO_ASSETS 环境变量。"
    )


# ---------------------------------------------------------------------------
# 内置 Fake（--mock 离线自测用）：按《山雨客栈》内容补全，验证脚本本身正确
# ---------------------------------------------------------------------------
_FAKE_CHARS = (("沈青崖", "白衣剑客"), ("柳如烟", "客栈老板娘"), ("蒙面人", "神秘刀客"))
_FAKE_PROPS = ("旧剑匣", "窄刀", "短刃", "茶碗", "灯笼")


class FakeTextBackend(TextBackend):
    name = "fake"

    def __init__(self) -> None:
        self.close_calls = 0
        self.base_url = "mock://fake"
        self.model = "fake"

    def ping(self, timeout: float = 10.0) -> bool:
        return True

    def loaded_models(self) -> list[str]:
        return []  # /api/ps：已卸载

    def _api(self, path, payload=None, *, method="POST", timeout=None):
        return {"models": [{"name": "qwen3:14b"}]}  # /api/tags：已拉取

    @staticmethod
    def _shot_json(index: int, text: str) -> dict:
        chars = [{"name": n, "role": r} for n, r in _FAKE_CHARS if n in text]
        if not chars:  # 兜底（纯环境镜）
            chars = [{"name": "沈青崖", "role": "白衣剑客"}]
        props = [{"name": p} for p in _FAKE_PROPS if p in text]
        if not props:
            props = [{"name": "旧剑匣"}]
        dm = re.search(r"「([^」]+)」", text)
        dialogue = [{"speaker": chars[0]["name"], "text": dm.group(1)}] if dm else []
        # 实体抽取加固（#356/#357）：角色/道具（script 0.9）+ 镜头标记垃圾
        # （script 0.8，应被 INVALID_ENTITY_PATTERNS 丢弃）+ 推断实体
        # （inferred 0.5，应被 excluded，绝不进匹配）。
        cns = "一二三四五六七八九"
        entities = (
            [{"name": c["name"], "type": "character", "source": "script", "confidence": 0.9}
             for c in chars]
            + [{"name": p["name"], "type": "prop", "source": "script", "confidence": 0.9}
               for p in props]
            + [{"name": f"镜头{cns[index - 1] if 1 <= index <= len(cns) else index}",
                "type": "character", "source": "script", "confidence": 0.8}]
            + [{"name": "烛火", "type": "effect", "source": "inferred", "confidence": 0.5}]
        )
        return {
            "index": index,
            "characters": chars,
            "props": props,
            "entities": entities,
            "actions": ["入场", "观察环境", "落座"],
            "emotion": "沉稳",
            "dialogue": dialogue,
            "visual_intent": "固定镜头交代环境，主体居中，给足信息量。",
        }

    def analyze_text(self, prompt, *, max_tokens=1024, temperature=0.0) -> str:
        # 批量模板：镜头列表行形如「[1] xxx」
        shots = []
        for line in prompt.splitlines():
            m = re.match(r"^\[\s*(\d+)\s*\]\s*(.*)$", line)
            if m:
                shots.append(self._shot_json(int(m.group(1)), m.group(2)))
        if shots:
            return json.dumps({"shots": shots}, ensure_ascii=False)
        # 单镜模板（fallback）
        return json.dumps(self._shot_json(1, prompt), ensure_ascii=False)

    def close(self) -> None:
        self.close_calls += 1


# ---------------------------------------------------------------------------
# 验收项
# ---------------------------------------------------------------------------


def _check_ollama_reachable(backend) -> bool:
    """① Ollama ping。"""
    print("\n[①] Ollama ping（/api/tags）")
    if backend.ping():
        print(f"{PASS_MSG} Ollama 服务可达: {backend.base_url}")
        return True
    print(f"{FAIL_MSG} Ollama 不可达: {backend.base_url}\n"
          "      请先在 Windows 本机启动 Ollama（托盘运行），再重试。")
    return False


def _check_model_installed(backend, model) -> bool:
    """② 确认 qwen3:14b 已拉取。"""
    print(f"\n[②] 确认模型 {model} 已拉取")
    try:
        models = backend._api("/api/tags", method="GET", timeout=15.0)
        names = [m.get("name", "") for m in models.get("models", [])]
    except Exception as exc:
        print(f"{FAIL_MSG} 无法读取模型列表: {exc}")
        return False
    if model in names or any(n.startswith(model) for n in names):
        print(f"{PASS_MSG} 找到模型（共 {len(names)} 个已拉取模型）")
        return True
    print(f"{FAIL_MSG} 模型 {model} 未拉取。可用: {names}\n      ollama pull {model}")
    return False


def _check_rule_parse(script_path: str):
    """③ 规则拆 Scene/Shot：产出 3 场景。"""
    print("\n[③] 规则拆 Scene/Shot（parse_script_file）")
    try:
        plan = parse_script_file(script_path)
    except Exception as exc:
        print(f"{FAIL_MSG} parse_script_file 异常: {exc}")
        return None
    if not plan.scenes:
        print(f"{FAIL_MSG} 无场景产出")
        return None
    print(f"{PASS_MSG} 规则拆出 {len(plan.scenes)} 场景，标题: "
          + " / ".join(f"「{s.title}」" for s in plan.scenes))
    for s in plan.scenes:
        print(f"       {s.scene_id} {s.title} {len(s.shots)} 镜")
    return plan


def _check_rule_structure(plan) -> bool:
    """④ 规则镜头边界与预期对齐：scene_01=2 / scene_02=6 / scene_03=1 / 总 9。"""
    print("\n[④] 规则镜头边界与预期对齐")
    if len(plan.scenes) != EXPECTED_SCENES:
        print(f"{FAIL_MSG} 场景数 {len(plan.scenes)} ≠ 预期 {EXPECTED_SCENES}")
        return False
    ok = True
    for i, sc in enumerate(plan.scenes, start=1):
        exp = EXPECTED_SHOTS_PER_SCENE.get(i)
        if exp is None or len(sc.shots) != exp:
            print(f"{FAIL_MSG} {sc.scene_id} 镜头 {len(sc.shots)} ≠ 预期 {exp}")
            ok = False
    total = sum(len(s.shots) for s in plan.scenes)
    if total != EXPECTED_TOTAL_SHOTS:
        print(f"{FAIL_MSG} 总镜头 {total} ≠ 预期 {EXPECTED_TOTAL_SHOTS}")
        ok = False
    if ok:
        print(f"{PASS_MSG} 3 场景 / {total} 镜对齐（{EXPECTED_SHOTS_PER_SCENE}）")
    return ok


def _check_analyze_full(plan, backend) -> bool:
    """⑤ analyze_script 单 session 全链路 + session 正确开合。"""
    print("\n[⑤] analyze_script 全链路（单 session Qwen 补全）")
    if text_backend_active():
        print(f"{FAIL_MSG} 调用前互斥锁应为 False")
        return False
    try:
        out = analyze_script(plan, backend)
    except Exception as exc:
        print(f"{FAIL_MSG} analyze_script 异常: {exc}")
        return False
    if text_backend_active():
        print(f"{FAIL_MSG} 调用后互斥锁应为 False（session 未正确释放）")
        return False
    if len(out.scenes) != len(plan.scenes):
        print(f"{FAIL_MSG} 场景数变化 {len(plan.scenes)} → {len(out.scenes)}")
        return False
    print(f"{PASS_MSG} 补全完成（{len(out.scenes)} 场景），session 正确开合")
    return True


def _check_structure_aligned(rule_plan, out) -> bool:
    """⑥ 补全前后结构对齐：逐镜 shot_id/source_text/duration_sec 零改动。"""
    print("\n[⑥] 补全前后结构对齐（shot_id / source_text / duration_sec）")
    for rs, os_ in zip(rule_plan.scenes, out.scenes):
        if len(rs.shots) != len(os_.shots):
            print(f"{FAIL_MSG} {rs.scene_id} 镜头数变化 {len(rs.shots)} → {len(os_.shots)}")
            return False
        for r, o in zip(rs.shots, os_.shots):
            if (r.shot_id != o.shot_id or r.source_text != o.source_text
                    or r.duration_sec != o.duration_sec):
                print(f"{FAIL_MSG} {rs.scene_id}/{r.shot_id} 结构字段被改动")
                return False
    print(f"{PASS_MSG} 全部 {EXPECTED_TOTAL_SHOTS} 镜结构字段零改动（Qwen 不决定镜头结构）")
    return True


def _check_fields_filled(out) -> bool:
    """⑦ Qwen 补全生效：至少 1 镜 role+props+actions+emotion+visual_intent 全非空。"""
    print("\n[⑦] Qwen 语义补全生效（role/props/actions/emotion/visual_intent）")
    filled = 0
    for scene in out.scenes:
        for shot in scene.shots:
            has_role = any(c.role for c in shot.characters)
            if (has_role and shot.props and shot.actions and shot.emotion
                    and shot.visual_intent):
                filled += 1
    if filled == 0:
        print(f"{FAIL_MSG} 没有任何镜头被完整补全（role+props+actions+emotion+visual_intent）")
        return False
    print(f"{PASS_MSG} {filled}/{sum(len(s.shots) for s in out.scenes)} 镜完整补全")
    return True


def _check_speaker_filled(out) -> bool:
    """⑧ 对白 speaker 回填：至少 1 条非空。"""
    print("\n[⑧] 对白 speaker 回填")
    n_filled = sum(1 for s in out.scenes for sh in s.shots
                   for d in sh.dialogue if d.speaker)
    n_total = sum(1 for s in out.scenes for sh in s.shots for _ in sh.dialogue)
    if n_filled == 0 and n_total:
        print(f"{FAIL_MSG} 对白存在但没有 speaker 被回填（{n_total} 条全空）")
        return False
    print(f"{PASS_MSG} {n_filled}/{n_total} 条对白有 speaker")
    return True


def _check_asset_match(out, assets_root: str) -> bool:
    """⑨ 资产自动匹配：8 图在库 + 3 地点 auto + ≥1 cast auto + ≥1 prop 非 none。"""
    print("\n[⑨] 资产自动匹配（match_plan 对照 minimax_studio/assets）")
    try:
        library = scan_asset_library(assets_root)
    except Exception as exc:
        print(f"{FAIL_MSG} scan_asset_library 异常: {exc}")
        return False
    lib_names = {a["name"] for a in library}
    missing = [k for k in KEY_ASSETS if k not in lib_names]
    if missing:
        print(f"{FAIL_MSG} 资产库缺图: {missing}（扫描到 {len(library)} 张）")
        return False
    print(f"{PASS_MSG} 资产库 {len(library)} 张，山雨客栈 8 图全部在库")

    result = match_plan(out, library)
    matches = result["matches"]
    auto = [m for m in matches if m["status"] == "auto"]
    print(f"       实体 {len(matches)} 个 / auto 绑定 {len(auto)} 个")
    for m in matches:
        img = m.get("image_file") or "-"
        et = m.get("entity_type") or m.get("kind")
        conf = m.get("confidence")
        print(f"       [{m['kind']}|{et}] {m['name']} → {m['status']} {img}"
              + (f" ({conf})" if conf else ""))

    # 结构合法性
    for m in matches:
        if m["matched"] != (m["status"] == "auto"):
            print(f"{FAIL_MSG} matched({m['matched']}) 与 status({m['status']}) 不一致")
            return False

    # 实体抽取加固（#356/#357）：漏斗统计 + 无镜头标记伪实体 + 无 inferred 进匹配
    funnel = result.get("funnel") or {}
    if not isinstance(funnel, dict) or "entered" not in funnel:
        print(f"{FAIL_MSG} match_plan 未返回 funnel（实体抽取加固未生效）")
        return False
    for m in matches:
        if m.get("entity_source") == "inferred":
            print(f"{FAIL_MSG} inferred 实体进匹配（应被 excluded）: {m['name']}")
            return False
        if re.match(r"^镜头", m.get("name", "")):
            print(f"{FAIL_MSG} 镜头标记伪实体进匹配（应被 INVALID 丢弃）: {m['name']}")
            return False
    print(f"{PASS_MSG} 实体清洗生效：无镜头标记 / 无 inferred 进匹配")

    # Phase 1.1 契约：visual_elements + asset_requirement/match_kind/suggest 透出
    ves = result.get("visual_elements")
    if not isinstance(ves, list):
        print(f"{FAIL_MSG} match_plan 未返回 visual_elements 列表（Phase 1.1 契约）")
        return False
    if "visual_only" not in funnel:
        print(f"{FAIL_MSG} funnel 缺 visual_only（无需资产匹配计数，Phase 1.1 契约）")
        return False
    for m in matches:
        for f in ("asset_requirement", "match_kind", "suggest"):
            if f not in m:
                print(f"{FAIL_MSG} match 缺 Phase 1.1 字段 {f}: {m['name']}")
                return False
        if m["suggest"] and m.get("match_kind") not in ("location_hierarchy", "generic_reference"):
            print(f"{FAIL_MSG} suggest=True 但 match_kind 非法: {m['name']} {m.get('match_kind')}")
            return False
    vis_names = {v.get("name") for v in ves if isinstance(v, dict)}
    ent_names = {m["name"] for m in matches}
    overlap = vis_names & ent_names
    if overlap:
        print(f"{FAIL_MSG} 视觉元素不应参与资产匹配: {overlap}")
        return False
    print(f"{PASS_MSG} Phase 1.1 契约齐全：visual_elements {len(ves)} 个（只进 Prompt）"
          f" + 字段透出 + 无视觉元素混入匹配")
    print(f"       漏斗: AI 发现 {funnel.get('discovered')} → 丢弃 "
          f"{funnel.get('invalid_dropped', 0) + funnel.get('dedup_dropped', 0)} → 清洗 "
          f"{funnel.get('cleansed')} → 进入 {funnel.get('entered')} → "
          f"auto {funnel.get('auto', 0)} / 待确认 {funnel.get('pending', 0)} / "
          f"未匹配 {funnel.get('none', 0)} / "
          f"视觉元素(无需资产匹配) {funnel.get('visual_only', 0)}")
    if funnel.get("invalid_dropped", 0) == 0:
        print("       提示: 本次未产出镜头标记垃圾（invalid_dropped=0），符合预期")

    # 3 个地点（规则保证）必须 auto
    locs = {m["name"] for m in matches if m["kind"] == "location" and m["matched"]}
    exp_locs = {"山雨楼外", "客栈大堂", "后院石阶"}
    if not exp_locs <= locs:
        print(f"{FAIL_MSG} 地点未全部 auto 绑定: {exp_locs - locs}")
        return False
    print(f"{PASS_MSG} 3 个场景地点全部 auto 绑定")

    # ≥1 角色 auto
    cast_auto = {m["name"] for m in matches if m["kind"] == "cast" and m["matched"]}
    if not cast_auto:
        print(f"{FAIL_MSG} 没有任何角色被 auto 绑定（Qwen 应识别 沈青崖/柳如烟/蒙面人）")
        return False
    print(f"{PASS_MSG} 角色 auto 绑定: {sorted(cast_auto)}")

    # ≥1 道具命中（auto 或待确认都算：Qwen 道具名与库名可能不完全一致）
    prop_hit = [m for m in matches if m["kind"] == "prop" and m["status"] != "none"]
    if not prop_hit:
        print(f"{FAIL_MSG} 没有任何道具被匹配（应有 旧剑匣 等）")
        return False
    print(f"{PASS_MSG} 道具匹配 {len(prop_hit)} 个: "
          + ", ".join(f"{m['name']}({m['status']})" for m in prop_hit))
    return True


def _check_drafts(out, assets_root: str) -> bool:
    """⑩ Prompt Builder 五区草稿 + Phase 5 运镜接入。"""
    print("\n[⑩] Prompt Builder 五区草稿（Phase 3 + Phase 5 运镜）")
    try:
        res = build_plan_drafts(out)
    except Exception as exc:
        print(f"{FAIL_MSG} build_plan_drafts 异常: {exc}")
        return False
    drafts = res["drafts"]
    if len(drafts) != EXPECTED_TOTAL_SHOTS:
        print(f"{FAIL_MSG} 草稿数 {len(drafts)} ≠ {EXPECTED_TOTAL_SHOTS}")
        return False
    zones = ("visual", "camera", "style", "sound", "negative")
    modes_ok, tmpl_ok, cam_ok = True, True, True
    for d in drafts:
        draft = d.get("draft", {})
        empty = [z for z in zones if not str(draft.get(z, "")).strip()]
        if empty:
            print(f"{FAIL_MSG} {d['scene_id']}/{d['shot_id']} 缺分区 {empty}")
            modes_ok = False
        if d.get("generation_mode") not in ("t2v", "fl2v", "r2v"):
            print(f"{FAIL_MSG} {d['shot_id']} generation_mode 非法: {d.get('generation_mode')}")
            modes_ok = False
        if not d.get("camera_template"):
            print(f"{FAIL_MSG} {d['shot_id']} 无 Phase 5 camera_template（运镜模板未接入）")
            tmpl_ok = False
        cam = str(draft.get("camera", ""))
        if not any(w in cam for w in CAMERA_MOVEMENT_WORDS):
            print(f"{FAIL_MSG} {d['shot_id']} 运镜措辞不含运动词: {cam[:50]}")
            cam_ok = False
    if not (modes_ok and tmpl_ok and cam_ok):
        return False
    print(f"{PASS_MSG} {len(drafts)} 镜五区草稿齐全（visual/camera/style/sound/negative）")
    print(f"       generation_mode 建议: "
          + ", ".join(f"{d['shot_id']}={d['generation_mode']}" for d in drafts))
    tmpl_ids = sorted({d["camera_template"] for d in drafts})
    print(f"       Phase 5 运镜模板命中 {len(tmpl_ids)} 个: {tmpl_ids}")
    return True


def _check_cameras(out, assets_root: str) -> bool:
    """⑪ 运镜跨镜连贯：9 镜 camera + 场景首镜无延续前缀（场景切换重置）。"""
    print("\n[⑪] 运镜跨镜连贯（camera_template 状态机 + 场景切换重置）")
    try:
        camres = build_plan_cameras(out)
        templates = list_camera_templates()
    except Exception as exc:
        print(f"{FAIL_MSG} camera 模块异常: {exc}")
        return False
    cams = camres["cameras"]
    if len(cams) != EXPECTED_TOTAL_SHOTS:
        print(f"{FAIL_MSG} 运镜数 {len(cams)} ≠ {EXPECTED_TOTAL_SHOTS}")
        return False
    tids = {t["id"] for t in templates}
    ok = True
    # 每场景首镜索引（1-based shot 序号 → cams 顺序）
    scene_first = {}
    for i, c in enumerate(cams):
        if c["scene_id"] not in scene_first:
            scene_first[c["scene_id"]] = i
    for c in cams:
        if not c["camera"].strip():
            print(f"{FAIL_MSG} {c['shot_id']} camera 为空")
            ok = False
        if not c["camera_template"] or c["camera_template"] not in tids:
            print(f"{FAIL_MSG} {c['shot_id']} camera_template 无效: {c['camera_template']}")
            ok = False
        if not c["camera_intent"]:
            print(f"{FAIL_MSG} {c['shot_id']} camera_intent 为空")
            ok = False
    # 场景首镜（prev_state=None）不得带跨镜延续前缀
    for sc_id, idx in scene_first.items():
        cam = cams[idx]["camera"]
        for pfx in CONTINUITY_PREFIXES:
            if cam.startswith(pfx):
                print(f"{FAIL_MSG} 场景首镜 {cams[idx]['shot_id']} 出现延续前缀: {cam[:40]}")
                ok = False
    # 场景内相邻运镜不应完全相同（状态机应产生变化）
    for sc_id in {c["scene_id"] for c in cams}:
        sc_cams = [c for c in cams if c["scene_id"] == sc_id]
        same = sum(1 for a, b in zip(sc_cams, sc_cams[1:]) if a["camera"] == b["camera"])
        if len(sc_cams) > 1 and same == len(sc_cams) - 1:
            print(f"{FAIL_MSG} {sc_id} 场景内运镜完全相同，状态机未生效")
            ok = False
    if not ok:
        return False
    print(f"{PASS_MSG} {len(cams)} 镜运镜齐全，模板 id 全部有效")
    print("       场景首镜（无延续前缀）: "
          + ", ".join(cams[i]["shot_id"] for i in scene_first.values()))
    return True


def _check_validation(out) -> bool:
    """⑫ 结构校验：validation.status == valid。"""
    print("\n[⑫] 结构校验（plan.validate()）")
    v = out.validation
    if v.status != "valid":
        print(f"{FAIL_MSG} status={v.status} errors={v.errors}")
        return False
    if v.warnings:
        print(f"{PASS_MSG} status=valid（warning 仅提示: {len(v.warnings)} 条）")
    else:
        print(f"{PASS_MSG} status=valid（无 warning）")
    return True


def _check_unload_and_lock(backend, model, *, wait: float = 15.0) -> bool:
    """⑬ 退出后：互斥锁复位 + /api/ps 确认模型不再驻留。

    Ollama 的 keep_alive=0 卸载是异步的（scheduler 释放 runner 需要时间），
    因此先轮询 /api/ps 等待模型消失（0.5s 间隔，默认 15s 超时）再判定——
    避免「立即查询落在卸载完成前」的误报。超时仍驻留才判 FAIL。
    """
    print("\n[⑬] 退出后卸载确认（keep_alive=0 + /api/ps 轮询等待）")
    ok = True
    if text_backend_active():
        print(f"{FAIL_MSG} text_backend_active() 仍为 True（互斥锁未释放）")
        ok = False
    else:
        print(f"{PASS_MSG} text_backend_active() 已复位 False")
    # 轮询 /api/ps 等待模型消失。
    deadline = time.monotonic() + wait
    loaded: list[str] = []
    while True:
        try:
            loaded = backend.loaded_models()
        except Exception as exc:
            print(f"{FAIL_MSG} /api/ps 查询失败: {exc}")
            return False
        still = [n for n in loaded if n.startswith(model)]
        if not still:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    if still:
        print(
            f"{FAIL_MSG} /api/ps 仍驻留: {still}（等待 {wait:.0f}s 未释放，"
            f"H3 前显存可能被占用）"
        )
        ok = False
    else:
        print(f"{PASS_MSG} /api/ps 已确认 {model} 不再驻留（loaded={loaded}）")
    return ok


def _check_gpu_registry() -> bool:
    """⑭（仅 ComfyUI 环境）GPU model registry 应为空。"""
    print("\n[⑭] ComfyUI GPU model registry")
    try:
        n = gpu_models_loaded()
    except Exception as exc:
        print(f"{FAIL_MSG} registry 查询异常: {exc}")
        return False
    if n:
        print(f"{FAIL_MSG} ComfyUI 仍有 {n} 个模型驻留 GPU（可能是 H3 未释放）")
        return False
    print(f"{PASS_MSG} GPU model registry = empty（{n} 个）")
    return True


def _check_mutex_gate() -> bool:
    """⑮ 互斥门反向验证：session 激活时 H3 侧互斥门应拦截。"""
    print("\n[⑮] 互斥门反向验证（session 激活时 H3 被拦截）")
    from director.text_backends import _acquire_text_active, _release_text_active

    if not _acquire_text_active():
        print(f"{FAIL_MSG} 无法获取互斥锁（其他 session 正在运行）")
        return False
    try:
        if not text_backend_active():
            print(f"{FAIL_MSG} 互斥锁获取后 text_backend_active() 应为 True")
            return False
        print(f"{PASS_MSG} 互斥门检测到 TextBackend 激活（H3 将拒绝启动）")
        return True
    finally:
        _release_text_active()
        if text_backend_active():
            print(f"{FAIL_MSG} 释放后 text_backend_active() 仍为 True")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_checks(backend, model: str, script_path: str, assets_root: str) -> int:
    checks: list[bool] = []

    # ①② 服务与模型（真实 Ollama 专用；Fake 同样走通）
    checks.append(_check_ollama_reachable(backend))
    checks.append(_check_model_installed(backend, model))

    # ③④ 规则层：读取剧本 → 拆 Scene/Shot
    plan = _check_rule_parse(script_path)
    checks.append(plan is not None)
    checks.append(_check_rule_structure(plan) if plan is not None else False)

    # ⑤-⑧ Commit 2 全链路（analyze_script 内部开 session）
    out = None
    if plan is not None:
        out = analyze_script(plan, backend)
        checks.append(_check_analyze_full(plan, backend))
        checks.append(_check_structure_aligned(plan, out))
        checks.append(_check_fields_filled(out))
        checks.append(_check_speaker_filled(out))
    else:
        checks += [False] * 4

    # ⑨-⑫ Phase 2 资产 / Phase 3+5 草稿 / 运镜连贯 / 校验
    if out is not None:
        checks.append(_check_asset_match(out, assets_root))
        checks.append(_check_drafts(out, assets_root))
        checks.append(_check_cameras(out, assets_root))
        checks.append(_check_validation(out))
    else:
        checks += [False] * 4

    # ⑬ 卸载 + 锁复位；⑭（ComfyUI 环境）registry；⑮ 互斥门
    checks.append(_check_unload_and_lock(backend, model))
    try:
        import comfy  # noqa: F401
        checks.append(_check_gpu_registry())
    except Exception:
        print("\n[⑭] 跳过 ComfyUI GPU registry（非 ComfyUI Python 环境）")
    checks.append(_check_mutex_gate())

    passed = sum(1 for c in checks if c)
    print("\n" + "=" * 64)
    print(f"验收结果: {passed}/{len(checks)} PASS")
    if passed == len(checks):
        print("Golden Path：剧本 → AI 制作计划 全链路验收通过 ✅")
        print("下一步：SPA 前端「导入剧本 → 审核」链路核对。")
        return 0
    print("存在 FAIL，请按上方提示修复后重跑。")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Golden Path 真实验收：剧本 → AI 制作计划（V1.7 全链路）")
    ap.add_argument("--model", default=os.environ.get("TEXT_LLM_MODEL", "qwen3:14b"))
    ap.add_argument("--host", default=None, help="Ollama base_url，默认读 OLLAMA_HOST")
    ap.add_argument("--script", default="", help="《山雨客栈》剧本 .md 路径")
    ap.add_argument("--assets-root", default="", help="minimax_studio/assets 目录")
    ap.add_argument("--mock", action="store_true",
                    help="离线自测：用内置 FakeTextBackend 走全链路（不连 Ollama）")
    args = ap.parse_args()

    try:
        script_path = _resolve_script_path(args.script)
        assets_root = _resolve_assets_root(args.assets_root)
    except FileNotFoundError as exc:
        print(f"{FAIL_MSG} {exc}")
        return 1

    if args.mock:
        backend = FakeTextBackend()
        print("=" * 64)
        print("Golden Path 真实验收 · 离线自测（--mock，FakeTextBackend）")
        print(f"  剧本: {script_path}")
        print(f"  资产: {assets_root}")
        print("=" * 64)
        return run_checks(backend, args.model, script_path, assets_root)

    backend = create_default_text_backend()
    if args.host:
        from director.text_backends import OllamaBackend
        backend = OllamaBackend(model=args.model, base_url=args.host)

    print("=" * 64)
    print(f"Golden Path 真实验收 · {backend.name} · model={backend.model} · {backend.base_url}")
    print(f"  剧本: {script_path}")
    print(f"  资产: {assets_root}")
    print("=" * 64)
    return run_checks(backend, backend.model, script_path, assets_root)


if __name__ == "__main__":
    sys.exit(main())
