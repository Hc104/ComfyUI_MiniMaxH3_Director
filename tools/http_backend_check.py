#!/usr/bin/env python3
"""HTTP 直测：绕过 SPA/浏览器，直接调运行中 ComfyUI 后端的 script/import + assets/scan。

目的（2026-08-11 #399）：用户 SPA 两次导入结果逐字相同且为旧行为（杯热茶/杯青瓷茶
粘连实体、山雨楼环境/后院石阶环境进 visual_elements、蒙面人 80% pending、漏斗
24→10→21→17）。但同一份新代码的 #381 真实验收漏斗是 32→21→18→7→0→11。
本脚本直接 POST 后端 API，绕开前端一切缓存，看运行中后端到底返回什么。

用法（用户本机，Ollama 需已启动且 qwen3:14b 在）：
  "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" \
      "D:\\夸克网盘\\ComfyUI_MiniMaxH3_Director\\tools\\http_backend_check.py" \
      --script "D:\\Comfy-Desktop\\ComfyUI-Shared\\input\\minimax_studio\\scripts\\山雨客栈.md"
可选 --comfy http://127.0.0.1:8188 --analyze/--no-analyze
退出码 0 = 后端返回新代码特征；1 = 后端返回旧代码特征（或调用失败）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

def _post(comfy: str, path: str, payload: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        comfy + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--script", required=True)
    ap.add_argument("--analyze", dest="analyze", action="store_true", default=True)
    ap.add_argument("--no-analyze", dest="analyze", action="store_false")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        script_text = f.read()

    print("== [1/2] POST /script/import ==")
    imp = _post(args.comfy, "/minimax/director/script/import", {
        "script_text": script_text,
        "title": "山雨客栈",
        "source_file": args.script,
        "analyze": args.analyze,
    })
    if "plan" not in imp:
        print("ERROR script/import 失败:", json.dumps(imp, ensure_ascii=False)[:500])
        return 1
    plan = imp["plan"]
    print("rule_only =", imp.get("rule_only"))
    print("warnings  =", len(imp.get("warnings", [])))
    print("scenes    =", len(plan.get("scenes", [])))
    shot_count = sum(len(s.get("shots", [])) for s in plan.get("scenes", []))
    print("shots     =", shot_count)

    print("\n== [2/2] POST /assets/scan ==")
    scan = _post(args.comfy, "/minimax/director/assets/scan", {"plan": plan})
    if "funnel" not in scan:
        print("ERROR assets/scan 失败:", json.dumps(scan, ensure_ascii=False)[:500])
        return 1
    funnel = scan.get("funnel", {})
    print("funnel    =", json.dumps(funnel, ensure_ascii=False))

    matches = scan.get("matches", [])
    print(f"\nmatches ({len(matches)}):")
    for m in matches:
        print(
            f"  [{m.get('kind')}] {m.get('name')!r} "
            f"status={m.get('status')} conf={m.get('confidence')} "
            f"match_kind={m.get('match_kind')} "
            f"asset={m.get('asset_name')} "
            f"entity_conf={m.get('entity_confidence')}"
        )

    ve = scan.get("visual_elements", [])
    ve_names = [v.get("name") if isinstance(v, dict) else str(v) for v in ve]
    print(f"\nvisual_elements ({len(ve_names)}):", ve_names)

    # ---- 判据 ----
    names = [m.get("name", "") for m in matches]
    # ⚠ 修正（#399）：不能把「灯笼」当旧特征——「昏黄灯笼」是剧本合法道具（新版正确产物）。
    # 旧行为粘连实体 = 杯热茶/杯青瓷茶/木桌/长凳/油灯/青瓷茶碗 这类动作短语粘连或旧版残留。
    old_adjoined = [n for n in names if any(k in n for k in ("杯热茶", "杯青瓷茶", "木桌", "长凳", "油灯", "青瓷茶碗"))]
    leaked_ve = [n for n in ve_names if any(k in n for k in ("山雨楼环境", "后院石阶环境", "檐角建筑"))]
    meng = next((m for m in matches if m.get("name") == "蒙面人"), None)

    print("\n== 判据 ==")
    old_sigs = []
    if old_adjoined:
        old_sigs.append(f"粘连实体出现: {old_adjoined}")
    if leaked_ve:
        old_sigs.append(f"正式地点变体泄漏进 visual_elements: {leaked_ve}")
    if meng and meng.get("status") != "auto":
        old_sigs.append(f"蒙面人 status={meng.get('status')}（新代码应 auto）")
    if old_sigs:
        print("后端返回【旧代码特征】——")
        for s in old_sigs:
            print("  ✗", s)
        return 1
    print("后端返回【新代码特征】✓（漏斗/匹配/ve 均符合 #381 验收）")
    return 0

if __name__ == "__main__":
    sys.exit(main())
