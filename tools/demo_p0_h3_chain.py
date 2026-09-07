#!/usr/bin/env python3
"""P0-① 演示数据采集：真实《山雨客栈》完整转换链路（规则层 + Qwen 层 + 五区草稿）。

用途：为「用户中文意图 → DirectorIntent → H3 Prompt」演示收集**真实**中间产物。
本脚本不 mock，直连本机 Ollama（qwen3:14b），输出《山雨客栈》9 镜的：

  ① 规则层（script_parser 纯规则）：source_text 原文 / characters / props / entities
  ② Qwen 层（script_analyzer 语义补全）：characters+role / props / entities+type+confidence /
     visual_elements / actions / emotion / dialogue / visual_intent
  ③ 五区草稿（prompt_builder_v17 纯规则模板组合，Qwen 补全后）
  ④ generation_mode / camera_template（规则判定）

输出：JSON 文件（demo_h3_chain_output.json，落脚本同目录）+ 终端摘要。

前置条件：
  1. Windows 本机 Ollama 正在运行（托盘即可），`ollama list` 有 qwen3:14b。
  2. 运行期间不要同时跑 H3 视频生成（显存安全门：Qwen 与 H3 互斥）。

运行方式（完整指令，任选其一）：
  A. 系统 Python：
        cd /d D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\custom_nodes\ComfyUI_MiniMaxH3_Director
        "D:\Comfy-Desktop\ComfyUI (1)\standalone-env\python.exe" tools\demo_p0_h3_chain.py
  B. 指定剧本：
        "D:\Comfy-Desktop\ComfyUI (1)\standalone-env\python.exe" tools\demo_p0_h3_chain.py --script "D:\Comfy-Desktop\ComfyUI-Shared\input\minimax_studio\scripts\山雨客栈.md"

退出码：0 = 成功；1 = 缺 Ollama / 模型 / 剧本文件找不到。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.script_parser import parse_script_file
from director.script_analyzer import analyze_script
from director.prompt_builder_v17 import build_plan_drafts
from director.text_backends import create_default_text_backend

# 剧本候选路径（Windows 本机常见位置）
_CANDIDATE_PATHS = [
    r"D:\Comfy-Desktop\ComfyUI-Shared\input\minimax_studio\scripts\山雨客栈.md",
    os.path.join(_REPO_ROOT, "..", "input", "minimax_studio", "scripts", "山雨客栈.md"),
    os.path.join(_REPO_ROOT, "input", "minimax_studio", "scripts", "山雨客栈.md"),
    os.path.join(_REPO_ROOT, "scripts", "山雨客栈.md"),
]

OUTPUT_NAME = "demo_h3_chain_output.json"


def _find_script(explicit: str | None) -> str:
    if explicit and os.path.isfile(explicit):
        return explicit
    for cand in _CANDIDATE_PATHS:
        if os.path.isfile(cand):
            return cand
    raise FileNotFoundError(
        "找不到《山雨客栈.md》。请用 --script 显式指定剧本路径。"
    )


def _shot_to_dict(shot, scene) -> dict:
    """规则 + Qwen 合并后的 Shot 全字段快照（Qwen 补全后 = 真实链路进入 SPA 的数据）。"""
    return {
        "shot_id": shot.shot_id,
        "source_text": shot.source_text,
        "duration_sec": shot.duration_sec,
        "characters": [{"name": c.name, "role": c.role} for c in shot.characters],
        "props": [p.name for p in shot.props],
        "entities": [
            {"name": e.name, "type": e.type, "confidence": e.confidence,
             "source": e.source}
            for e in shot.entities
        ],
        "visual_elements": [
            {"name": v.name, "type": v.type, "confidence": v.confidence}
            for v in shot.visual_elements
        ],
        "actions": list(shot.actions),
        "emotion": shot.emotion,
        "dialogue": [{"speaker": d.speaker, "text": d.text} for d in shot.dialogue],
        "visual_intent": shot.visual_intent,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="P0-① 演示数据采集")
    ap.add_argument("--script", default=None, help="剧本 .md 路径（缺省自动探测）")
    ap.add_argument("--scenes", default="all",
                    help="分析范围：all 或场景号（1/2/3）")
    ap.add_argument("--tokens", type=int, default=4096,
                    help="Qwen 每场景批量 max_tokens（默认 4096）")
    args = ap.parse_args()

    script_path = _find_script(args.script)
    print(f"[1/4] 规则层拆镜（script_parser）: {script_path}")
    rule_plan = parse_script_file(script_path)
    print(f"      场景 {len(rule_plan.scenes)} 个 / 镜头 "
          f"{sum(len(s.shots) for s in rule_plan.scenes)} 个")

    print("[2/4] Qwen 语义补全（script_analyzer，Ollama qwen3:14b）…")
    print("      分析 3 个场景预计 3-8 分钟，期间请勿跑 H3 生成。")
    plan = analyze_script(rule_plan, max_tokens=args.tokens)
    for w in plan.validation.warnings:
        print(f"      ⚠ {w}")

    print("[3/4] 五区草稿（prompt_builder_v17，Qwen 补全后）")
    drafts_result = build_plan_drafts(plan)
    drafts_by_key = {
        f"{d['scene_id']}:{d['shot_id']}": d for d in drafts_result["drafts"]
    }

    print("[4/4] 汇总输出…")
    scenes_out = []
    for si, scene in enumerate(plan.scenes, start=1):
        if args.scenes != "all" and str(si) not in args.scenes.split(","):
            continue
        shots_out = []
        for shot in scene.shots:
            d = drafts_by_key.get(f"{scene.scene_id}:{shot.shot_id}")
            shots_out.append({
                "shot_id": shot.shot_id,
                "scene_id": scene.scene_id,
                "scene_title": scene.title,
                "shot": _shot_to_dict(shot, scene),
                "draft": d["draft"] if d else {},
                "generation_mode": d["generation_mode"] if d else "",
                "camera_template": d["camera_template"] if d else "",
            })
        scenes_out.append({
            "scene_id": scene.scene_id,
            "title": scene.title,
            "location_name": scene.location_name,
            "time": scene.time,
            "weather": scene.weather,
            "shots": shots_out,
        })

    out = {
        "script_file": script_path,
        "template_version": drafts_result["template_version"],
        "scenes": scenes_out,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUTPUT_NAME)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n✅ 输出已保存: {out_path}")

    # 终端摘要：第一场全镜 + 情绪/意图
    for sc in scenes_out:
        print(f"\n=== {sc['title']} | {sc['location_name']} | {sc['time']} | {sc['weather']}")
        for s in sc["shots"]:
            sh = s["shot"]
            print(f"  [{s['shot_id']}] emotions={sh['emotion']!r} char={[c['name'] for c in sh['characters']]}")
            print(f"      visual_intent: {sh['visual_intent'][:60] if sh['visual_intent'] else '(空)'}")
            print(f"      draft.visual: {s['draft'].get('visual','')[:60] if s.get('draft') else '(空)'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    except Exception as exc:  # 顶层兜底：给用户清晰错误
        print(f"❌ 脚本失败: {exc}")
        sys.exit(1)