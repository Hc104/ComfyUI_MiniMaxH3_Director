#!/usr/bin/env python3
"""Phase 1 真实验收：剧本 → AI 制作计划（Qwen 语义补全）全链路（V17_PLAN.md §13）。

在真实 Ollama 服务上跑 Phase 1 独立验收（13 项）：
    Commit 1 规则拆 Scene/Shot → Commit 2 Qwen 语义补全（analyze_script 单 session）
    → 结构对齐 / 字段齐全 / 结构校验 / Phase 1 不放 asset 字段
    → 退出卸载（keep_alive=0 + /api/ps）→ 互斥锁复位 → 互斥门反向验证。

本脚本不 mock：直连 localhost:11434 真实 Ollama。必须先在 Windows 本机启动 Ollama
（托盘运行即可），并确认 `ollama list` 里有 qwen3:14b。

两种运行方式：
  A. 系统 Python（只验 Ollama 链路，跳过 ComfyUI registry 检查）：
        python tools/phase1_script_test.py
  B. ComfyUI Desktop Python（能检查 ComfyUI GPU model registry）：
        "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" tools/phase1_script_test.py

可选 --mock：用内置 FakeTextBackend 走一遍验收逻辑（不连真实 Ollama），用于离线自测脚本本身。

退出码：0 = 全部 PASS；1 = 有 FAIL。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    ProductionPlan,
    Prop,
    Scene,
    Shot,
)
from director.script_parser import parse_script  # noqa: E402
from director.script_analyzer import analyze_script  # noqa: E402
from director.text_backends import (  # noqa: E402
    TextBackend,
    create_default_text_backend,
    gpu_models_loaded,
    text_backend_active,
)

# ---------------------------------------------------------------------------
# 《山雨客栈》两场景剧本（Commit 1 已验证：2 场景 5 镜）
# ---------------------------------------------------------------------------
SHAN_YU_SCRIPT = """# 山雨客栈

## 第一场：清晨 · 客栈大堂

地点：山雨客栈大堂
时间：清晨
天气：雨

清晨，山雨客栈外细雨蒙蒙。
林雪（青衫女侠）推开客栈大门，收伞，环视堂内。

## 第二场：午后 · 后院石阶

地点：后院石阶
时间：午后
天气：阴

柳如烟（白衣医女）蹲在石阶边，查看沈青崖（灰衣刀客）的伤口。
沈青崖皱眉："这点小伤，不碍事。"
柳如烟摇头："伤口已见骨，必须处理。"
"""

# 规则拆镜预期（Commit 1 已验证）：scene_01 2 镜 / scene_02 3 镜 / 总 5 镜
EXPECTED_SCENES = 2
EXPECTED_SHOTS_PER_SCENE = {1: 2, 2: 3}
EXPECTED_TOTAL_SHOTS = 5

PASS_MSG = "  ✓ PASS"
FAIL_MSG = "  ✗ FAIL"

# Phase 1 刻意不放的字段（分属 Phase 2/3/4）
_FORBIDDEN_KEYS = ("castIds", "locationId", "assetId", "generationMode", "h3Prompt",
                   "camera", "refs")


# ---------------------------------------------------------------------------
# 内置 Fake（--mock 自测用）：session() 走真实 TextSession（真实互斥锁 + GPU registry）
# ---------------------------------------------------------------------------
class FakeTextBackend(TextBackend):
    name = "fake"

    def __init__(self) -> None:
        self.close_calls = 0
        self.base_url = "mock://fake"
        self.model = "fake"

    def ping(self, timeout: float = 10.0) -> bool:
        return True

    def loaded_models(self) -> list[str]:
        return []

    def _api(self, path, payload=None, *, method="POST", timeout=None):
        # /api/tags 视为已拉取 qwen3:14b；/api/ps 视为已卸载（loaded_models 返回空）
        return {"models": [{"name": "qwen3:14b"}]}

    def analyze_text(self, prompt, *, max_tokens=1024, temperature=0.0) -> str:
        # 单镜模板（含「镜头原始文本」）与批量模板（含「镜头列表」）都返回合法补全
        return json.dumps({
            "shots": [
                {
                    "index": 1,
                    "characters": [
                        {"name": "林雪", "role": "青衫女侠"},
                        {"name": "陈默", "role": "黑衣剑客"},
                    ],
                    "props": [{"name": "油纸伞"}, {"name": "茶壶"}],
                    "actions": ["推开大门", "收伞", "环视堂内"],
                    "emotion": "沉着冷静",
                    "dialogue": [{"speaker": "林雪", "text": "雨停之前，都走不了。"}],
                    "visual_intent": "客栈门口，青衫女子收伞入画，全景交代环境。",
                }
            ]
        }, ensure_ascii=False)

    def close(self) -> None:
        self.close_calls += 1


# ---------------------------------------------------------------------------
# 13 项验收
# ---------------------------------------------------------------------------

def _check_ollama_reachable(backend) -> bool:
    """① Ollama ping（/api/tags）。"""
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


def _check_rule_parse() -> bool:
    """③ Commit 1 规则解析成功：parse_script 产出 2 场景。"""
    print("\n[③] Commit 1 规则拆 Scene/Shot（parse_script）")
    try:
        plan = parse_script(SHAN_YU_SCRIPT, source_file="山雨客栈.md", title="山雨客栈")
    except Exception as exc:
        print(f"{FAIL_MSG} parse_script 异常: {exc}")
        return False
    if not plan.scenes:
        print(f"{FAIL_MSG} 无场景产出")
        return False
    print(f"{PASS_MSG} 规则拆出 {len(plan.scenes)} 场景")
    for s in plan.scenes:
        print(f"       {s.scene_id}「{s.title}」 {len(s.shots)} 镜")
    return True


def _check_rule_structure(plan) -> bool:
    """④ 规则镜头边界与预期对齐：scene_01=2 / scene_02=3 / 总 5。"""
    print("\n[④] 规则镜头边界与预期对齐")
    if len(plan.scenes) != EXPECTED_SCENES:
        print(f"{FAIL_MSG} 场景数 {len(plan.scenes)} ≠ 预期 {EXPECTED_SCENES}")
        return False
    total = 0
    ok = True
    for i, scene in enumerate(plan.scenes, start=1):
        n = len(scene.shots)
        total += n
        exp = EXPECTED_SHOTS_PER_SCENE.get(i)
        if exp is not None and n != exp:
            print(f"{FAIL_MSG} {scene.scene_id} {n} 镜 ≠ 预期 {exp}")
            ok = False
    if total != EXPECTED_TOTAL_SHOTS:
        print(f"{FAIL_MSG} 总镜头 {total} ≠ 预期 {EXPECTED_TOTAL_SHOTS}")
        ok = False
    if ok:
        print(f"{PASS_MSG} 镜头边界对齐（{total} 镜，受规则约束未失控）")
    return ok


def _check_analyze_full(plan, backend) -> bool:
    """⑤ analyze_script 单 session 跑通 + session 正确开合。"""
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


def _check_fields_filled(out) -> bool:
    """⑥ Qwen 补全生效：至少 1 镜 role+props+actions+emotion+visual_intent 全非空。"""
    print("\n[⑥] Qwen 语义补全生效（role/props/actions/emotion/visual_intent）")
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


def _check_structure_aligned(rule_plan, out) -> bool:
    """⑦ 结构对齐：补全前后逐镜 shot_id/source_text/duration_sec 完全一致。"""
    print("\n[⑦] 补全前后结构对齐（shot_id / source_text / duration_sec）")
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


def _check_speaker_filled(out) -> bool:
    """⑧ dialogue.speaker 回填：至少 1 条非空。"""
    print("\n[⑧] 对白 speaker 回填")
    n_filled = sum(1 for s in out.scenes for sh in s.shots
                   for d in sh.dialogue if d.speaker)
    n_total = sum(1 for s in out.scenes for sh in s.shots for _ in sh.dialogue)
    if n_filled == 0 and n_total:
        print(f"{FAIL_MSG} 对白存在但没有 speaker 被回填（{n_total} 条全空）")
        return False
    print(f"{PASS_MSG} {n_filled}/{n_total} 条对白有 speaker")
    return True


def _check_validation(out) -> bool:
    """⑨ 结构校验：validation.status == valid。"""
    print("\n[⑨] 结构校验（plan.validate()）")
    v = out.validation
    if v.status != "valid":
        print(f"{FAIL_MSG} status={v.status} errors={v.errors}")
        return False
    if v.warnings:
        print(f"{PASS_MSG} status=valid（warning 仅提示: {len(v.warnings)} 条）")
    else:
        print(f"{PASS_MSG} status=valid（无 warning）")
    return True


def _check_no_asset_fields(out) -> bool:
    """⑩ Phase 1 刻意不放 asset/H3/运镜字段。"""
    print("\n[⑩] Phase 1 不放 castIds/locationId/assetId/generationMode/h3Prompt/camera/refs")
    blob = json.dumps(out.to_dict(), ensure_ascii=False)
    hit = [k for k in _FORBIDDEN_KEYS if k in blob]
    if hit:
        print(f"{FAIL_MSG} 意外出现字段: {hit}")
        return False
    print(f"{PASS_MSG} 输出 JSON 无 Phase 2/3/4 字段")
    return True


def _check_unload_and_lock(backend, model) -> bool:
    """⑪ 退出后：互斥锁复位 + /api/ps 确认模型不再驻留。"""
    print("\n[⑪] 退出后卸载确认（keep_alive=0 + /api/ps）")
    ok = True
    if text_backend_active():
        print(f"{FAIL_MSG} text_backend_active() 仍为 True（互斥锁未释放）")
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


def _check_gpu_registry() -> bool:
    """⑫（仅 ComfyUI 环境）GPU model registry 应为空。"""
    print("\n[⑫] ComfyUI GPU model registry")
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
    """⑬ 互斥门反向验证：session 激活时 H3 侧互斥门应拦截。"""
    print("\n[⑬] 互斥门反向验证（session 激活时 H3 被拦截）")
    from director.text_backends import _acquire_text_active, _release_text_active

    if not _acquire_text_active():
        print(f"{FAIL_MSG} 无法获取互斥锁（其他 session 正在运行）")
        return False
    try:
        if not text_backend_active():
            print(f"{FAIL_MSG} 互斥锁获取后 text_backend_active() 应为 True")
            return False
        if text_backend_active():
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


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_checks(backend, model: str) -> int:
    checks: list[bool] = []

    # ①② 服务与模型（真实 Ollama 专用）
    checks.append(_check_ollama_reachable(backend))
    checks.append(_check_model_installed(backend, model))

    # ③④ Commit 1 规则层（与后端无关，先跑）
    try:
        plan = parse_script(SHAN_YU_SCRIPT, source_file="山雨客栈.md", title="山雨客栈")
    except Exception as exc:  # pragma: no cover - 规则解析不应抛
        print(f"[③] FAIL parse_script 异常: {exc}")
        plan = None
    checks.append(_check_rule_parse())
    checks.append(_check_rule_structure(plan) if plan is not None else False)

    # ⑤-⑩ Commit 2 全链路（analyze_script 内部开 session）
    out = None
    if plan is not None:
        out = analyze_script(plan, backend)  # 默认创建真实 session
        checks.append(_check_analyze_full(plan, backend))
        checks.append(_check_fields_filled(out))
        checks.append(_check_structure_aligned(plan, out))
        checks.append(_check_speaker_filled(out))
        checks.append(_check_validation(out))
        checks.append(_check_no_asset_fields(out))
    else:
        checks += [False] * 6

    # ⑪ 卸载 + 锁复位；⑫（ComfyUI 环境）registry；⑬ 互斥门
    checks.append(_check_unload_and_lock(backend, model))
    try:
        import comfy  # noqa: F401
        checks.append(_check_gpu_registry())
    except Exception:
        print("\n[⑫] 跳过 ComfyUI GPU registry（非 ComfyUI Python 环境）")
    checks.append(_check_mutex_gate())

    passed = sum(1 for c in checks if c)
    print("\n" + "=" * 64)
    print(f"验收结果: {passed}/{len(checks)} PASS")
    if passed == len(checks):
        print("Phase 1 剧本→AI 制作计划全链路验收通过 ✅")
        print("下一步：Commit 3 = 剧本导入正式 UI 入口。")
        return 0
    print("存在 FAIL，请按上方提示修复后重跑。")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 真实验收（真实 Ollama）")
    ap.add_argument("--model", default=os.environ.get("TEXT_LLM_MODEL", "qwen3:14b"))
    ap.add_argument("--host", default=None, help="Ollama base_url，默认读 OLLAMA_HOST")
    ap.add_argument("--mock", action="store_true",
                    help="离线自测：用内置 FakeTextBackend 走一遍验收逻辑（不连 Ollama）")
    args = ap.parse_args()

    if args.mock:
        backend = FakeTextBackend()
        print("=" * 64)
        print("Phase 1 真实验收 · 离线自测（--mock，FakeTextBackend）")
        print("=" * 64)
        return run_checks(backend, args.model)

    backend = create_default_text_backend()
    if args.host:
        from director.text_backends import OllamaBackend
        backend = OllamaBackend(model=args.model, base_url=args.host)

    print("=" * 64)
    print(f"Phase 1 真实验收 · {backend.name} · model={backend.model} · {backend.base_url}")
    print("=" * 64)
    return run_checks(backend, backend.model)


if __name__ == "__main__":
    sys.exit(main())
