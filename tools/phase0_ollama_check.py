#!/usr/bin/env python3
"""Phase 0 真实验收：Ollama + Qwen3:14B 连通性 / 进程级显存互斥（V17_PLAN.md §13）。

在真实 Ollama 服务上跑 Phase 0 独立验收第 1 项：
    ① Ollama ping（/api/tags）→ ② 真实 Qwen3:14B 分析（强 JSON）→
    ③ session 退出（keep_alive=0 卸载 + /api/ps 确认）→
    ④ text_backend_active() 互斥标志复位 → ⑤（ComfyUI 环境）GPU model registry empty。

本脚本不 mock：直连 localhost:11434 真实 Ollama。必须先在 Windows 本机启动 Ollama
（托盘运行即可），并确认 `ollama list` 里有 qwen3:14b。

两种运行方式：
  A. 系统 Python（只验 Ollama 链路，跳过 ComfyUI registry 检查）：
        python tools/phase0_ollama_check.py
  B. ComfyUI Desktop Python（能检查 ComfyUI GPU model registry）：
        "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" tools/phase0_ollama_check.py

退出码：0 = 全部 PASS；1 = 有 FAIL（缺 Ollama / 模型 / 强 JSON 失败 / 卸载未确认）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.text_backends import (
    create_default_text_backend,
    gpu_models_loaded,
    text_backend_active,
)

SAMPLE_SCRIPT = """清晨，山雨客栈外细雨蒙蒙。
林雪（青衫女侠）推开客栈大门，收伞，环视堂内。
陈默（黑衣剑客）坐在角落，抬眼看向林雪，轻声说："你也来了。"
林雪点头，走到他对面坐下："雨停之前，都走不了。"
桌上那壶茶还冒着热气。
"""

ANALYZE_PROMPT = f"""你是剧本结构化分析助手。请把下面的剧本片段解析成 JSON，包含字段：
- scene: 场景描述（简短）
- characters: 出场人物名数组
- locations: 地点名数组
- props: 关键道具数组
- actions: 主要动作描述数组（2-4 条）

只输出一个 JSON 对象，不要输出任何其他文字、代码块或注释。

剧本：
{SAMPLE_SCRIPT}"""

PASS_MSG = "  ✓ PASS"
FAIL_MSG = "  ✗ FAIL"


def _check_ollama_reachable(backend):
    """① Ollama ping（/api/tags）。"""
    print("\n[①] Ollama ping（/api/tags）")
    if backend.ping():
        print(f"{PASS_MSG} Ollama 服务可达: {backend.base_url}")
        return True
    print(
        f"{FAIL_MSG} Ollama 不可达: {backend.base_url}\n"
        "      请先在 Windows 本机启动 Ollama（托盘运行），再重试。"
    )
    return False


def _check_model_installed(backend, model):
    """② 确认 qwen3:14b 已拉取（/api/tags 列表）。"""
    print(f"\n[②] 确认模型 {model} 已拉取")
    try:
        models = backend._api("/api/tags", method="GET", timeout=15.0)
        names = [m.get("name", "") for m in models.get("models", [])]
    except Exception as exc:
        print(f"{FAIL_MSG} 无法读取模型列表: {exc}")
        return False
    exact = model in names
    prefix = [n for n in names if n.startswith(model)]
    if exact or prefix:
        print(f"{PASS_MSG} 找到模型: {exact and model or prefix[0]} （共 {len(names)} 个已拉取模型）")
        return True
    print(
        f"{FAIL_MSG} 模型 {model} 未拉取。可用模型: {names}\n"
        f"      请执行: ollama pull {model}"
    )
    return False


def _check_real_analysis(backend):
    """③ 真实 Qwen3:14B 分析：进 session → 分析剧本 → 强 JSON 校验。"""
    print("\n[③] 真实分析（with text_backend.session():）")
    try:
        with backend.session() as llm:
            print("      session 已进入（互斥锁 + preflight ping + GPU registry 检查通过）")
            if not text_backend_active():
                print(f"{FAIL_MSG} session 内 text_backend_active() 应为 True")
                return False
            out = llm.analyze_json(ANALYZE_PROMPT, max_tokens=2048, temperature=0.0)
    except Exception as exc:
        print(f"{FAIL_MSG} session 异常: {exc}")
        return False

    if not isinstance(out, dict):
        print(f"{FAIL_MSG} Qwen 未返回合法 JSON（实际: {out!r:.100}）")
        return False
    required = {"scene", "characters", "locations", "props", "actions"}
    missing = required - set(out.keys())
    if missing:
        print(f"{FAIL_MSG} JSON 缺字段: {sorted(missing)}")
        return False
    print(f"{PASS_MSG} Qwen 强 JSON 输出:")
    print("      " + json.dumps(out, ensure_ascii=False)[:300])
    return True


def _check_unload_and_lock(backend, model):
    """④ 退出后：text_backend_active() 复位 + /api/ps 确认 qwen3:14b 不再驻留。"""
    print("\n[④] 退出后卸载确认（keep_alive=0 + /api/ps）")
    ok = True
    if text_backend_active():
        print(f"{FAIL_MSG} text_backend_active() 退出后仍为 True（互斥锁未释放）")
        ok = False
    else:
        print(f"{PASS_MSG} text_backend_active() 已复位 False")
    try:
        loaded = backend.loaded_models()
        still = [n for n in loaded if n.startswith(model)]
    except Exception as exc:
        print(f"{FAIL_MSG} /api/ps 查询失败: {exc}")
        return False
    if still:
        print(f"{FAIL_MSG} /api/ps 仍驻留: {still}（H3 前显存可能被占用）")
        ok = False
    else:
        print(f"{PASS_MSG} /api/ps 已确认 {model} 不再驻留（loaded={loaded}）")
    return ok


def _check_gpu_registry(backend):
    """⑤（仅 ComfyUI 环境）GPU model registry 应为空。"""
    print("\n[⑤] ComfyUI GPU model registry")
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


def _check_mutex_gate(backend):
    """⑥ 互斥门反向验证：session 激活时 H3 侧互斥门应拦截。"""
    print("\n[⑥] 互斥门反向验证（session 激活时 H3 被拦截）")
    from director.text_backends import _acquire_text_active, _release_text_active

    if not _acquire_text_active():
        print(f"{FAIL_MSG} 无法获取互斥锁（其他 session 正在运行）")
        return False
    try:
        if not text_backend_active():
            print(f"{FAIL_MSG} 互斥锁获取后 text_backend_active() 应为 True")
            return False
        # 模拟 H3 侧检查：引用 executor_core 里相同的互斥门语义
        from director.text_backends import text_backend_active as _tba

        if _tba():
            print(f"{PASS_MSG} 互斥门检测到 TextBackend 激活（H3 将拒绝启动）")
            return True
        print(f"{FAIL_MSG} 互斥门未检测到激活")
        return False
    finally:
        _release_text_active()
        if text_backend_active():
            print(f"{FAIL_MSG} 释放后 text_backend_active() 仍为 True")
            return False
        print(f"{PASS_MSG} 互斥锁已释放，text_backend_active() 复位 False")


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 0 Ollama 真实验收")
    ap.add_argument("--model", default=os.environ.get("TEXT_LLM_MODEL", "qwen3:14b"))
    ap.add_argument("--host", default=None, help="Ollama base_url，默认读 OLLAMA_HOST")
    args = ap.parse_args()

    backend = create_default_text_backend()
    if args.host:
        from director.text_backends import OllamaBackend

        backend = OllamaBackend(model=args.model, base_url=args.host)

    print("=" * 64)
    print(f"Phase 0 真实验收 · {backend.name} · model={backend.model} · {backend.base_url}")
    print("=" * 64)

    checks = [
        _check_ollama_reachable(backend),
        _check_model_installed(backend, backend.model),
        _check_real_analysis(backend),
        _check_unload_and_lock(backend, backend.model),
    ]
    # ⑤ 只在 ComfyUI 环境（能 import comfy）时执行
    try:
        import comfy  # noqa: F401

        checks.append(_check_gpu_registry(backend))
    except Exception:
        print("\n[⑤] 跳过 ComfyUI GPU registry（非 ComfyUI Python 环境）")

    checks.append(_check_mutex_gate(backend))

    passed = sum(1 for c in checks if c)
    print("\n" + "=" * 64)
    print(f"验收结果: {passed}/{len(checks)} PASS")
    if passed == len(checks):
        print("Phase 0 连通性/显存互斥验收通过 ✅")
        print("下一步：启动 ComfyUI Desktop → 正常生成一镜 H3，确认互斥门放行、显存无残留。")
        return 0
    print("存在 FAIL，请按上方提示修复后重跑。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
