#!/usr/bin/env python3
"""V1.7 Phase 1 · script_analyzer 单元测试（Commit 2，mock 全链路）。

覆盖（V17_PLAN §4 / Phase 1 Commit 2 验收）：
1. Scene 批量强 JSON 补全：角色 role 回填 / 新角色追加 / 道具 / 动作 / 情绪 /
   visual_intent / 复杂句 speaker 回填；shot_id/source_text/duration_sec 结构不变；
2. 批量失败 fallback 逐镜 analyze_shot（prompt 形态区分 batch vs 单镜）；
3. analyze_script 单 session 遍历多场景；只 with session() 不碰显存
   （unload 零调用 / close 一次 / 退出后互斥锁释放）；
4. 显存安全守卫：analyze_scene 必须在 with backend.session(): 内部（否则 RuntimeError）；
5. analyze_json 容错：损坏 JSON → 自动重试一次 → 成功；
6. 规则优先合并：Qwen 只补空，不覆盖规则已有值；Qwen 追加新实体；
7. 兜底：超时/None/非 dict → 保留规则 Shot；backend 全失败 → plan 结构完整 + warnings；
8. Phase 1 刻意不放 asset 字段：补全后 JSON 无 castIds/assetId/generationMode/h3Prompt/camera/refs；
9. canonical 去重（角色名 trim/大小写）；字段类型非法降级；超长 source_text 截断。

直接用系统 python 运行（无第三方依赖，不连 Ollama、不碰 GPU）：
    python3 director/tests/test_script_analyzer.py
"""

from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import (  # noqa: E402
    Character,
    Dialogue,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    ProjectInfo,
    Prop,
    Scene,
    Shot,
    Validation,
)
from director.script_analyzer import (  # noqa: E402
    analyze_scene,
    analyze_script,
    analyze_shot,
    _bounded_text,
    _clean_character_profiles,
    _clean_entities,
    _merge_shot,
)
from director.text_backends import (  # noqa: E402
    TextBackend,
    text_backend_active,
)

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


# ---------------------------------------------------------------------------
# Fake 后端：继承 TextBackend，session() 走真实 TextSession（真实互斥锁 + GPU registry 查询）
# ---------------------------------------------------------------------------
class FakeTextBackend(TextBackend):
    """responder(prompt) -> str 决定输出；或用 queue 按调用顺序消费。

    用基类 session() → TextSession：进入走 _acquire_text_active + gpu_models_loaded
    （非 ComfyUI 环境返回 0）+ preflight（基类无操作）；退出走 close + 释放锁 + empty_cache。
    因此能真实验证「只 with session() 不碰显存管理」。
    """

    name = "fake"

    def __init__(self, responder=None, queue=None):
        self.responder = responder
        self.queue = list(queue or [])
        self.prompts: list[str] = []
        self.close_calls = 0
        self.unload_calls = 0

    def analyze_text(self, prompt, *, max_tokens=1024, temperature=0.0):
        self.prompts.append(prompt)
        if self.responder is not None:
            return self.responder(prompt)
        return self.queue.pop(0) if self.queue else ""

    def close(self):
        self.close_calls += 1

    def unload(self):
        self.unload_calls += 1


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------

def _shot(i: int, text: str = "") -> Shot:
    return Shot(
        shot_id=f"shot_{i:02d}",
        source_text=text or f"镜头 {i} 的原文文本",
        duration_sec=5,
        characters=[Character(name="林雪")],
        props=[],
        actions=[],
        emotion="",
        dialogue=[Dialogue(speaker="", text="雨停之前，都走不了。")],
        visual_intent="",
    )


def _scene(n: int = 2) -> Scene:
    return Scene(
        scene_id="scene_01",
        title="山雨客栈大堂",
        location_name="山雨客栈大堂",
        time="清晨",
        weather="雨",
        shots=[_shot(i) for i in range(1, n + 1)],
    )


def _plan(n_scenes: int = 2, shots_per: int = 2) -> ProductionPlan:
    return ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file="山雨客栈.md"),
        scenes=[_scene(shots_per) for _ in range(n_scenes)],
        validation=Validation(status="pending"),
    )


def _batch_json(n: int, use_index: bool = True) -> dict:
    """合法 Scene 批量结果：每镜语义补全。"""
    return {
        "shots": [
            {
                "index": i + 1 if use_index else None,
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
            for i in range(n)
        ]
    }


# ---------------- 1. Scene 批量强 JSON 补全 ----------------

def test_batch_success() -> None:
    print("\n[1] Scene 批量强 JSON 补全（角色 role / 道具 / 动作 / 情绪 / speaker / 视觉意图）")
    scene = _scene(2)
    backend = FakeTextBackend(responder=lambda p: json.dumps(_batch_json(2), ensure_ascii=False))
    with backend.session():
        out = analyze_scene(backend, scene)

    _check("shot_id / source_text / duration_sec 结构不变",
           all(s.shot_id == r.shot_id and s.source_text == r.source_text
               and s.duration_sec == r.duration_sec
               for s, r in zip(out.shots, scene.shots)))
    s1 = out.shots[0]
    _check("已有角色 role 回填（林雪→青衫女侠）",
           any(c.name == "林雪" and c.role == "青衫女侠" for c in s1.characters))
    _check("新角色追加（陈默）",
           any(c.name == "陈默" and c.role == "黑衣剑客" for c in s1.characters))
    _check("道具补全（油纸伞 + 茶壶）",
           sorted(p.name for p in s1.props) == ["油纸伞", "茶壶"])
    _check("动作补全", "推开大门" in s1.actions and "收伞" in s1.actions)
    _check("情绪补全", s1.emotion == "沉着冷静")
    _check("复杂句 speaker 回填（林雪）",
           any(d.speaker == "林雪" and d.text == "雨停之前，都走不了。" for d in s1.dialogue))
    _check("visual_intent 补全", bool(s1.visual_intent) and "客栈门口" in s1.visual_intent)
    _check("批量调用 1 次、无 fallback",
           len(backend.prompts) == 1 and "镜头原始文本" not in backend.prompts[0])
    _check("退出 session 后互斥锁释放", text_backend_active() is False)
    _check("只 with session()，不直接 unload", backend.unload_calls == 0)
    _check("close 由 TextSession 退出时恰好一次", backend.close_calls == 1)


def test_batch_prompt_contains_all_shots() -> None:
    print("\n[2] Scene 批量 prompt 包含全部镜头原文")
    scene = _scene(2)
    seen = []
    def responder(p):
        seen.append(p)
        return json.dumps(_batch_json(2), ensure_ascii=False)
    backend = FakeTextBackend(responder=responder)
    with backend.session():
        analyze_scene(backend, scene)
    p = seen[0]
    _check("prompt 含 [1] 第一镜原文", f"[1] {scene.shots[0].source_text}" in p)
    _check("prompt 含 [2] 第二镜原文", f"[2] {scene.shots[1].source_text}" in p)
    _check("prompt 含场景信息", "scene_01" in p and "清晨" in p and "雨" in p)


# ---------------- 2. 批量失败 → 逐镜 fallback ----------------

def test_batch_fail_fallback_shot() -> None:
    print("\n[3] 批量返回非 JSON → 逐镜 analyze_shot 兜底")
    scene = _scene(2)
    calls = []

    def responder(p):
        calls.append(p)
        if "镜头原始文本" in p:  # 单镜模板 → 返回该镜语义
            return json.dumps({
                "characters": [{"name": "林雪", "role": "青衫女侠"}],
                "props": [{"name": "油纸伞"}],
                "actions": ["收伞"],
                "emotion": "沉着",
                "dialogue": [{"speaker": "林雪", "text": "雨停之前，都走不了。"}],
                "visual_intent": "单镜意图草稿。",
            }, ensure_ascii=False)
        return "抱歉，这不是 JSON"
    backend = FakeTextBackend(responder=responder)
    with backend.session():
        out = analyze_scene(backend, scene)

    _check("batch prompt 调用 2 次（含一次 retry）",
           sum("镜头原始文本" not in p for p in calls) == 2)
    _check("fallback 逐镜调用 n 次", sum("镜头原始文本" in p for p in calls) == 2)
    _check("fallback 补全生效",
           out.shots[0].visual_intent == "单镜意图草稿。" and out.shots[1].visual_intent == "单镜意图草稿。")
    _check("fallback role 回填", any(c.role == "青衫女侠" for c in out.shots[0].characters))
    _check("结构不变", [s.shot_id for s in out.shots] == ["shot_01", "shot_02"])


# ---------------- 3. analyze_script 单 session ----------------

def test_analyze_script_single_session() -> None:
    print("\n[4] analyze_script 单 session 遍历多场景")
    plan = _plan(n_scenes=2, shots_per=2)
    backend = FakeTextBackend(responder=lambda p: json.dumps(_batch_json(2), ensure_ascii=False))
    out = analyze_script(plan, backend)

    _check("close 恰好一次（单 session）", backend.close_calls == 1)
    _check("不直接 unload（显存全权交给 TextSession）", backend.unload_calls == 0)
    _check("退出后互斥锁释放", text_backend_active() is False)
    _check("两场景都补全", all(s.shots[0].visual_intent for s in out.scenes))
    _check("project 不变", out.project.title == "山雨客栈")
    _check("批量调用 2 次、无 fallback",
           len(backend.prompts) == 2 and all("镜头原始文本" not in p for p in backend.prompts))


def test_analyze_scene_guard_outside_session() -> None:
    print("\n[5] 显存安全守卫：analyze_scene 必须在 session 内")
    scene = _scene(1)
    backend = FakeTextBackend(responder=lambda p: json.dumps(_batch_json(1), ensure_ascii=False))
    try:
        analyze_scene(backend, scene)
        _check("不在 session 内调用 → RuntimeError", False, "未抛异常")
    except RuntimeError as exc:
        _check("不在 session 内调用 → RuntimeError", "session" in str(exc), str(exc))


# ---------------- 4. analyze_json 容错重试 ----------------

def test_corrupt_json_retry() -> None:
    print("\n[6] analyze_json 损坏 JSON → 自动重试一次 → 成功")
    scene = _scene(1)
    queue = ["这段不是 JSON 格式", json.dumps(_batch_json(1), ensure_ascii=False)]
    backend = FakeTextBackend(queue=queue)
    with backend.session():
        out = analyze_scene(backend, scene)
    _check("第一次损坏自动重试（analyze_text 调 2 次）", len(backend.prompts) == 2)
    _check("重试后补全成功", bool(out.shots[0].visual_intent))


def test_corrupt_json_all_fail_keeps_rule() -> None:
    print("\n[7] 批量与单镜都非 JSON → 保留规则 Shot + warning")
    scene = _scene(1)
    backend = FakeTextBackend(responder=lambda p: "完全不是 JSON")
    warnings: list = []
    with backend.session():
        out = analyze_scene(backend, scene, warnings=warnings)
    _check("镜头结构保留", out.shots[0].shot_id == "shot_01")
    _check("规则语义保留（visual_intent 空）", out.shots[0].visual_intent == "")
    _check("规则角色保留", any(c.name == "林雪" for c in out.shots[0].characters))
    _check("warnings 有记录（批量非 JSON 至少 1 条）",
           len(warnings) >= 1, str(warnings))


# ---------------- 5. 规则优先合并 ----------------

def test_merge_shot_rule_priority() -> None:
    print("\n[8] 规则优先：Qwen 只补空，不覆盖规则已有值")
    rule = Shot(
        shot_id="shot_01", source_text="原文", duration_sec=5,
        characters=[Character(name="林雪", role="青衫女侠")],
        props=[Prop(name="茶壶")],
        actions=["收伞"],
        emotion="沉着",
        dialogue=[Dialogue(speaker="林雪", text="雨停之前，都走不了。")],
        visual_intent="已填意图",
    )
    data = {
        "characters": [{"name": "林雪", "role": "青衫女侠"}, {"name": "陈默", "role": "黑衣剑客"}],
        "props": [{"name": "油纸伞"}],
        "actions": ["推门"],
        "emotion": "紧张",
        "dialogue": [{"speaker": "林雪", "text": "雨停之前，都走不了。"}],
        "visual_intent": "Qwen 意图",
    }
    merged = _merge_shot(rule, data)
    _check("规则角色保序不覆盖", merged.characters[0].name == "林雪"
           and merged.characters[0].role == "青衫女侠")
    _check("Qwen 追加新角色", any(c.name == "陈默" for c in merged.characters))
    _check("规则道具优先 + Qwen 追加", sorted(p.name for p in merged.props) == ["油纸伞", "茶壶"])
    _check("规则动作优先", merged.actions == ["收伞"])
    _check("规则情绪优先", merged.emotion == "沉着")
    _check("规则 speaker 不回填", merged.dialogue[0].speaker == "林雪")
    _check("规则 visual_intent 优先", merged.visual_intent == "已填意图")


def test_merge_shot_qwen_adds_new() -> None:
    print("\n[9] Qwen 追加新角色 / 新道具 / 新台词")
    rule = Shot(shot_id="shot_01", source_text="原文", duration_sec=5,
                characters=[Character(name="林雪")], props=[], actions=[], emotion="",
                dialogue=[], visual_intent="")
    data = {
        "characters": [{"name": "陈默", "role": "黑衣剑客"}],
        "props": [{"name": "长剑"}],
        "actions": ["拔剑", "挥剑"],
        "dialogue": [{"speaker": "", "text": "你来晚了。"}],
        "visual_intent": "客栈暗处，剑客起身拔剑。",
    }
    merged = _merge_shot(rule, data)
    _check("追加角色", [c.name for c in merged.characters] == ["林雪", "陈默"])
    _check("追加道具", [p.name for p in merged.props] == ["长剑"])
    _check("动作补全", merged.actions == ["拔剑", "挥剑"])
    _check("追加台词", merged.dialogue[0].text == "你来晚了。")
    _check("视觉意图", "拔剑" in merged.visual_intent)


def test_merge_characters_canonical_dedup() -> None:
    print("\n[10] canonical 去重：角色名 trim / 大小写")
    rule = [Character(name="林雪")]
    qwen = [
        {"name": " 林雪 ", "role": "女侠"},
        {"name": "林雪", "role": ""},
        {"name": "陈默", "role": "剑客"},
    ]
    from director.script_analyzer import _merge_characters
    merged = _merge_characters(rule, qwen)
    _check("林雪唯一且 role 回填",
           len([c for c in merged if c.name == "林雪"]) == 1
           and merged[0].role == "女侠")
    _check("陈默追加", any(c.name == "陈默" for c in merged))
    _check("去重后总数 2", len(merged) == 2)


# ---------------- 6. 兜底 ----------------

def test_analyze_shot_timeout_returns_rule() -> None:
    print("\n[11] analyze_shot 超时/异常 → 返回原 Shot")
    shot = _shot(1)
    backend = FakeTextBackend(responder=lambda p: (_ for _ in ()).throw(RuntimeError("超时")))
    out = analyze_shot(backend, shot, scene=None)
    _check("异常后返回原 Shot", out is shot or (out.shot_id == shot.shot_id
           and out.visual_intent == "" and out.actions == []))


def test_analyze_shot_none_returns_rule() -> None:
    print("\n[12] analyze_json 返回 None → 返回原 Shot")
    shot = _shot(1)
    backend = FakeTextBackend(responder=lambda p: "")
    out = analyze_shot(backend, shot, scene=_scene(1))
    _check("None 后保留规则", out.shot_id == shot.shot_id and out.visual_intent == "")


def test_analyze_script_all_fail_keeps_plan() -> None:
    print("\n[13] backend 全失败 → plan 结构完整 + warnings")
    plan = _plan(n_scenes=2, shots_per=1)
    backend = FakeTextBackend(responder=lambda p: (_ for _ in ()).throw(RuntimeError("服务不可达")))
    out = analyze_script(plan, backend)
    _check("场景数不变", len(out.scenes) == 2)
    _check("镜头结构保留", all(len(s.shots) == 1 for s in out.scenes))
    _check("规则语义保留", all(s.shots[0].visual_intent == "" for s in out.scenes))
    _check("warnings 追加", len(out.validation.warnings) >= 2, str(out.validation.warnings))
    _check("validation.status 仍是结构校验结果", out.validation.status in ("valid", "invalid"))


def test_empty_scene_returns_without_call() -> None:
    print("\n[14] 空镜头场景直接返回，不调 backend")
    scene = Scene(scene_id="scene_01", title="空", location_name="", time="", weather="",
                  shots=[])
    backend = FakeTextBackend(responder=lambda p: json.dumps(_batch_json(0), ensure_ascii=False))
    with backend.session():
        out = analyze_scene(backend, scene)
    _check("空场景原样返回", out.shots == [] and out.scene_id == "scene_01")
    _check("backend 未被调用", backend.prompts == [])


# ---------------- 7. 字段类型健壮性 ----------------

def test_clean_field_type_robustness() -> None:
    print("\n[15] Qwen 字段类型非法 → 规范化降级，不崩溃")
    rule = Shot(shot_id="shot_01", source_text="原文", duration_sec=5,
                characters=[Character(name="林雪")], props=[], actions=[], emotion="",
                dialogue=[], visual_intent="")
    data = {
        "characters": "林雪",            # 应为 list
        "props": ["油纸伞"],             # list of str → 合法
        "actions": {"a": "b"},          # 应为 list
        "emotion": 123,                 # 应为 str
        "dialogue": ["台词而已"],        # list of str → 合法
        "visual_intent": None,          # None → 空
    }
    merged = _merge_shot(rule, data)
    _check("characters 非法类型降级为规则", [c.name for c in merged.characters] == ["林雪"])
    _check("props list-of-str 正常提取", [p.name for p in merged.props] == ["油纸伞"])
    _check("actions 非法类型降级为空", merged.actions == [])
    _check("emotion 非法类型降级为空", merged.emotion == "")
    _check("dialogue list-of-str 正常提取", merged.dialogue[0].text == "台词而已")
    _check("visual_intent None 降级为空", merged.visual_intent == "")


def test_bounded_text_truncates() -> None:
    print("\n[16] 超长 source_text 截断（仅 prompt，不改原文）")
    long = "字" * 2000
    out = _bounded_text(long)
    _check("截断到上限 + 省略号", len(out) == 601 and out.endswith("…"))
    _check("短文本不截断", _bounded_text("短文本") == "短文本")
    _check("空白剥离", _bounded_text("  内容  ") == "内容")


# ---------------- 8. Phase 1 刻意不放 asset 字段 ----------------

def test_no_asset_fields() -> None:
    print("\n[17] Phase 1 补全后仍不放 castIds/assetId/generationMode/h3Prompt/camera/refs")
    plan = _plan(n_scenes=1, shots_per=2)
    backend = FakeTextBackend(responder=lambda p: json.dumps(_batch_json(2), ensure_ascii=False))
    out = analyze_script(plan, backend)
    blob = json.dumps(out.to_dict(), ensure_ascii=False)
    forbidden = ("castIds", "locationId", "assetId", "generationMode", "h3Prompt", "refs",
                 "camera", "content")
    _check("无 asset/H3/运镜字段", not any(k in blob for k in forbidden))
    _check("visual_intent 是字符串草稿", all(
        isinstance(s.shots[0].visual_intent, str) for s in out.scenes))


# ---------------- 9. index 匹配优先级 ----------------

def test_batch_partial_and_index_match() -> None:
    print("\n[18] 批量结果按 index 精确匹配；缺失镜保留规则")
    scene = _scene(3)
    # 故意乱序 + 缺第 2 镜 + 多一个 index=4 不在场景中
    data = {"shots": [
        {"index": 3, "visual_intent": "第三镜意图", "characters": [],
         "props": [], "actions": [], "emotion": "", "dialogue": []},
        {"index": 1, "visual_intent": "第一镜意图", "characters": [],
         "props": [], "actions": [], "emotion": "", "dialogue": []},
        {"index": 4, "visual_intent": "多余的第四镜", "characters": [],
         "props": [], "actions": [], "emotion": "", "dialogue": []},
    ]}
    backend = FakeTextBackend(responder=lambda p: json.dumps(data, ensure_ascii=False))
    with backend.session():
        out = analyze_scene(backend, scene)
    _check("index=1 匹配到第一镜", out.shots[0].visual_intent == "第一镜意图")
    _check("index=3 匹配到第三镜", out.shots[2].visual_intent == "第三镜意图")
    _check("缺 index=2 → 保留规则（空意图）", out.shots[1].visual_intent == "")
    _check("镜头顺序不变", [s.shot_id for s in out.shots] == ["shot_01", "shot_02", "shot_03"])


def test_batch_no_index_order_fallback() -> None:
    print("\n[19] 批量结果无 index 字段 → 按数组顺序兜底")
    scene = _scene(2)
    data = {"shots": [
        {"visual_intent": "第一镜（顺序位）", "characters": [],
         "props": [], "actions": [], "emotion": "", "dialogue": []},
        {"visual_intent": "第二镜（顺序位）", "characters": [],
         "props": [], "actions": [], "emotion": "", "dialogue": []},
    ]}
    backend = FakeTextBackend(responder=lambda p: json.dumps(data, ensure_ascii=False))
    with backend.session():
        out = analyze_scene(backend, scene)
    _check("shot_01 取顺序位 0", out.shots[0].visual_intent == "第一镜（顺序位）")
    _check("shot_02 取顺序位 1", out.shots[1].visual_intent == "第二镜（顺序位）")


# ---------------- 10. 实体抽取（#355 typed entities + script-only 约束） ----------------

def test_entities_merge() -> None:
    print("\n[20] entities 补全：规则实体保留 + Qwen 追加 + 类型/source/置信度透传")
    rule = Shot(shot_id="shot_01", source_text="原文", duration_sec=5,
                entities=[Entity(entity_id="ent_001", name="林雪", type=EntityType.CHARACTER,
                                 source=EntitySource.SCRIPT, confidence=0.9)])
    data = {
        "entities": [
            {"name": "林雪", "type": "character", "source": "script", "confidence": 0.8},
            {"name": "陈默", "type": "character", "source": "script", "confidence": 0.95},
            {"name": "油纸伞", "type": "prop", "source": "script", "confidence": 0.75},
            {"name": "雨雾", "type": "effect", "source": "inferred", "confidence": 0.5},
            {"name": "镜头一", "type": "character", "source": "script", "confidence": 0.8},
        ],
        "characters": [], "props": [], "actions": [], "emotion": "",
        "dialogue": [], "visual_intent": "",
    }
    merged = _merge_shot(rule, data)
    ents = {e.name: e for e in merged.entities}
    _check("规则实体保留（原 entity_id）", ents["林雪"].entity_id == "ent_001")
    _check("追加 4 个新实体", set(ents.keys()) == {"林雪", "陈默", "油纸伞", "雨雾", "镜头一"},
           str(list(ents)))
    _check("type/source/confidence 透传", ents["陈默"].type == "character"
           and ents["陈默"].source == "script" and ents["陈默"].confidence == 0.95)
    _check("inferred source 保留", ents["雨雾"].source == "inferred")
    _check("重复同名同 type 不追加", len([e for e in merged.entities if e.name == "林雪"]) == 1)
    _check("镜头一在此层保留（清洗层负责丢）", "镜头一" in ents)
    _check("实体带新 id", bool(ents["陈默"].entity_id) and ents["陈默"].entity_id != "ent_001")


def test_clean_entities_validation() -> None:
    print("\n[21] _clean_entities：非法 type/source/置信度规范化 + 去重")
    out = _clean_entities([
        {"name": " 林雪 ", "type": "character", "source": "script", "confidence": 0.9},
        {"name": "林雪", "type": "character", "source": "script", "confidence": 0.85},   # dup
        {"name": "油纸伞", "type": "bogus", "source": "script", "confidence": 2.0},      # type→unknown conf→1.0
        {"name": "雨雾", "type": "effect", "source": "bogus", "confidence": -0.5},       # source→script conf→0.0
        "not-a-dict",  # 忽略
    ])
    _check("林雪唯一", len([x for x in out if x["name"].strip() == "林雪"]) == 1)
    _check("非法 type→unknown", out[1]["type"] == "unknown", str(out[1]))
    _check("conf 钳制到 1.0", out[1]["confidence"] == 1.0, str(out[1]))
    _check("非法 source→script", out[2]["source"] == "script", str(out[2]))
    _check("conf 钳制到 0.0", out[2]["confidence"] == 0.0, str(out[2]))
    _check("非 dict 忽略，总 3 条", len(out) == 3, str(out))


def test_prompt_script_only_constraint() -> None:
    print("\n[22] Prompt 含 entities schema + script-only 铁律（杜绝创造实体）")
    from director.script_analyzer import (
        _SCENE_ANALYZE_TEMPLATE,
        _SHOT_ANALYZE_TEMPLATE,
    )
    for name, tpl in (("batch", _SCENE_ANALYZE_TEMPLATE), ("single", _SHOT_ANALYZE_TEMPLATE)):
        _check(f"{name} 含 entities 字段", '"entities"' in tpl)
        _check(f"{name} 含脚本原文铁律（绝不创造）", "绝不" in tpl and "实体" in tpl)
        _check(f"{name} 含实体类型枚举", "character" in tpl and "architecture" in tpl)
    _check("batch 不含单镜标记（模板可区分）", "镜头原始文本" not in _SCENE_ANALYZE_TEMPLATE)
    _check("single 含单镜标记", "镜头原始文本" in _SHOT_ANALYZE_TEMPLATE)


# ---------------- 主入口 ----------------

# ---------------- 11. P0-① 集成：Qwen 补全 → DirectorIntent（边界检查接入） ----------------

def test_analyze_script_feeds_director_intent() -> None:
    """Qwen 补全后的 ProductionPlan → build_plan_intents：幻觉实体丢弃、合法实体保留、
    AI 推断字段被 DirectorIntent 消费（P0-① 用户拍板 ①/② 链路验证）。"""
    from director.director_intent import build_plan_intents

    SHOT1_SRC = ("雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，"
                 "湿漉漉的石阶映着暖光，门前老槐树滴着水珠。")
    SHOT4_SRC = ("柳如烟放下茶碗，从柜台后绕过，端着一碗热茶走向堂中，语气平淡："
                 "「客官，落座歇脚，茶先暖着。」")
    SHOT7_SRC = ("蒙面人一步步走近，沉声：「剑，交出来。」沈青崖起身，横剑护在柳如烟身前；"
                 "柳如烟退后半步，手已按住柜台下的短刃。三人成三角对峙。")

    plan = ProductionPlan(
        project=ProjectInfo(title="山雨客栈", source_file=""),
        scenes=[Scene(
            scene_id="scene_01", title="山雨楼内", location_name="山雨楼内",
            time="黄昏", weather="雨",
            shots=[
                Shot(shot_id="shot_01", source_text=SHOT1_SRC, duration_sec=5),
                Shot(shot_id="shot_04", source_text=SHOT4_SRC, duration_sec=5),
                Shot(shot_id="shot_07", source_text=SHOT7_SRC, duration_sec=5),
            ],
        )],
        validation=Validation(),
    )

    # Qwen 批量输出：shot_01 混入跨镜幻觉「旧剑匣」+ marker「镜头二」；shot_04 含别名「柳姑娘」
    qwen = {"shots": [
        {"index": 1,
         "characters": [], "props": [],
         "entities": [
             {"name": "山雨楼", "type": "architecture", "source": "script", "confidence": 0.95},
             {"name": "旧剑匣", "type": "prop", "source": "inferred", "confidence": 0.9},
             {"name": "镜头二", "type": "character", "source": "script", "confidence": 0.8},
         ],
         "actions": ["漫向"], "emotion": "静谧",
         "dialogue": [], "visual_intent": "全景俯拍山雾笼罩的山雨楼，暖黄灯笼与湿石阶冷暖对比。"},
        {"index": 2,
         "characters": [{"name": "柳如烟", "role": "客栈老板娘"}], "props": [],
         "entities": [
             {"name": "柳如烟", "type": "character", "source": "script", "confidence": 0.95},
             {"name": "柳姑娘", "type": "character", "source": "inferred", "confidence": 0.8},
         ],
         "actions": ["放下", "绕过", "走向"], "emotion": "平静",
         "dialogue": [{"speaker": "柳如烟", "text": "客官，落座歇脚，茶先暖着。"}],
         "visual_intent": "中景跟拍柳如烟端茶走向堂中。"},
        {"index": 3,
         "characters": [], "props": [],
         "entities": [
             {"name": "沈青崖", "type": "character", "source": "script", "confidence": 0.95},
             {"name": "蒙面人", "type": "character", "source": "script", "confidence": 0.95},
             {"name": "柳如烟", "type": "character", "source": "script", "confidence": 0.9},
         ],
         "actions": ["走近", "起身", "按住"], "emotion": "紧张",
         "dialogue": [{"speaker": "蒙面人", "text": "剑，交出来。"}],
         "visual_intent": "三人对峙，蒙面人一步步逼近。"},
    ]}
    backend = FakeTextBackend(responder=lambda p: json.dumps(qwen, ensure_ascii=False))
    enriched = analyze_script(plan, backend)

    intents = build_plan_intents(enriched)
    _check("intents 覆盖 3 镜", set(intents) ==
           {"scene_01:shot_01", "scene_01:shot_04", "scene_01:shot_07"}, str(list(intents)))

    s1 = intents["scene_01:shot_01"]
    n1 = [e["name"] for e in s1.entities]
    _check("shot_01 山雨楼保留（原文实体）", "山雨楼" in n1, str(n1))
    _check("shot_01 旧剑匣丢弃（跨镜幻觉）", "旧剑匣" not in n1, str(n1))
    _check("shot_01 镜头二丢弃（marker 伪实体）", "镜头二" not in n1, str(n1))
    _check("shot_01 AI 情绪被消费", s1.emotion == "静谧", s1.emotion)
    _check("shot_01 AI 构图被消费为 composition", s1.composition == qwen["shots"][0]["visual_intent"],
           s1.composition)

    s4 = intents["scene_01:shot_04"]
    n4 = [e["name"] for e in s4.entities]
    _check("shot_04 柳如烟保留", "柳如烟" in n4, str(n4))
    _check("shot_04 柳姑娘别名归一保留", "柳姑娘" in n4, str(n4))
    _check("shot_04 对白原文进意图", any("客官，落座歇脚" in d["text"]
           for d in s4.audio["dialogue"]), str(s4.audio))

    s7 = intents["scene_01:shot_07"]
    n7 = [e["name"] for e in s7.entities]
    _check("shot_07 沈青崖/蒙面人/柳如烟 全保留", {"沈青崖", "蒙面人", "柳如烟"} <= set(n7), str(n7))
    _check("shot_07 retained_facts 以警示对白开头保留",
           any("剑，交出来" in f for f in s7.retained_facts), str(s7.retained_facts))


def test_no_qwen_director_intent_still_valid() -> None:
    """Qwen 全失败（规则兜底）→ build_plan_intents 仍构建：AI 字段空、原文事实/规则仍在（降级链路）。"""
    from director.director_intent import build_plan_intents

    plan = ProductionPlan(
        project=ProjectInfo(title="t", source_file=""),
        scenes=[Scene(scene_id="s1", title="t", location_name="山雨楼外", time="黄昏", weather="雨",
                      shots=[Shot(shot_id="s01",
                                  source_text="雨刚停，山雾从林间漫向山雨楼，门前老槐树滴着水珠。",
                                  duration_sec=5)])],
        validation=Validation(),
    )
    backend = FakeTextBackend(responder=lambda p: (_ for _ in ()).throw(RuntimeError("不可达")))
    enriched = analyze_script(plan, backend)
    intent = build_plan_intents(enriched)["s1:s01"]
    _check("无 Qwen 时 emotion 空", intent.emotion == "")
    _check("无 Qwen 时 composition 空", intent.composition == "")
    _check("原文事实仍保留（retained_facts 非空）", len(intent.retained_facts) >= 3,
           str(intent.retained_facts))
    _check("规则字段仍在（location/audio）", intent.location == "山雨楼外"
           and "雨" in intent.audio["ambient"])
    _check("规则运镜仍在（establish）", intent.continuity["camera_template"] == "establish",
           str(intent.continuity))


# ---------------- P2：Qwen 角色外观档案（PROMPT_COMPILER_V1 §4-P2） ----------------

def test_clean_character_profiles_normalizes() -> None:
    """_clean_character_profiles：{canonical_name: appearance}，空值丢弃 + canonical 去重。"""
    out = _clean_character_profiles([
        {"name": "顾琰宸", "appearance": "深色高定西装"},
        {"name": " 顾琰宸 ", "appearance": "另一套"},          # 同名（canonical 相同）→ 先到先得
        {"name": "林薇薇", "appearance": ""},                  # 空 appearance → 丢弃
        {"name": "", "appearance": "空名"},                    # 空 name → 丢弃
        {"name": "陈默", "appearance": " 黑衣剑客 "},          # trim
        "not-a-dict",                                          # 非 dict → 跳过
    ])
    _check("顾琰宸 取首条", out.get("顾琰宸") == "深色高定西装", str(out))
    _check("林薇薇 空 appearance 不入档案", "林薇薇" not in out, str(out))
    _check("陈默 trim 后保留", out.get("陈默") == "黑衣剑客", str(out))
    _check("None 防御", _clean_character_profiles(None) == {})
    _check("非 list 防御", _clean_character_profiles({"a": 1}) == {})


def test_merge_characters_appearance_merge() -> None:
    """_merge_characters 外观合并优先级：规则 > Qwen 单镜 > appearance_map（只补空）。"""
    from director.script_analyzer import _merge_characters

    # 规则已有 appearance → 不被 Qwen/map 覆盖
    rule = [Character(name="顾琰宸", role="商业精英", appearance="规则西装")]
    merged = _merge_characters(rule, [{"name": "顾琰宸", "role": "商业精英",
                                       "appearance": "Qwen西装"}],
                               appearance_map={"顾琰宸": "档案西装"})
    _check("规则 appearance 不被覆盖", merged[0].appearance == "规则西装",
           merged[0].appearance)

    # 规则空 + Qwen 单镜有 → 用 Qwen
    rule2 = [Character(name="林薇薇")]
    merged2 = _merge_characters(rule2, [{"name": "林薇薇", "appearance": "职业套裙"}])
    _check("Qwen 单镜 appearance 回填", merged2[0].appearance == "职业套裙",
           merged2[0].appearance)

    # 规则/Qwen 都空 + appearance_map 有 → 用 map（角色名 canonical 匹配）
    rule3 = [Character(name=" 陈默 ")]
    merged3 = _merge_characters(rule3, [], appearance_map={"陈默": "黑衣劲装"})
    _check("appearance_map canonical 匹配回填", merged3[0].appearance == "黑衣劲装",
           merged3[0].appearance)

    # 全空 → 保持空（不臆造）
    merged4 = _merge_characters([Character(name="顾长风")], [], appearance_map={})
    _check("全空 → appearance 留空", merged4[0].appearance == "",
           merged4[0].appearance)


def test_analyze_scene_character_profiles_batch() -> None:
    """analyze_scene 批量：顶层 character_profiles → shot.characters 外观回填。"""
    scene = _scene(1)
    scene.shots[0].characters = [Character(name="林雪"), Character(name="陈默")]
    payload = _batch_json(1, use_index=True)
    payload["character_profiles"] = [
        {"name": "林雪", "appearance": "青衫襦裙"},
        {"name": "陈默", "appearance": "黑衣劲装"},
    ]
    backend = FakeTextBackend(responder=lambda p: json.dumps(payload))
    with backend.session() as llm:
        out = analyze_scene(llm, scene)
    chars = {c.name: c.appearance for c in out.shots[0].characters}
    _check("林雪 外观回填", chars.get("林雪") == "青衫襦裙", str(chars))
    _check("陈默 外观回填", chars.get("陈默") == "黑衣劲装", str(chars))


def test_analyze_script_character_profiles_flow() -> None:
    """P2 全链：analyze_script 聚合外观档案 + 规则兜底 + timeline/beats 保留 + 约束接线。"""
    from director.director_intent import build_plan_intents

    shot_a = Shot(shot_id="s01",
                  source_text="顾琰宸走进豪华私人办公室。",
                  duration_sec=5,
                  characters=[Character(name="顾琰宸")],
                  entities=[Entity(name="顾琰宸", type=EntityType.CHARACTER,
                                   source=EntitySource.SCRIPT, confidence=0.95)])
    shot_b = Shot(shot_id="s02",
                  source_text="林薇薇独自站在窗边，背对门口。",
                  duration_sec=5,
                  characters=[Character(name="林薇薇")],
                  entities=[Entity(name="林薇薇", type=EntityType.CHARACTER,
                                   source=EntitySource.SCRIPT, confidence=0.95)])
    scene = Scene(scene_id="sc", title="办公室", location_name="豪华私人办公室",
                  shots=[shot_a, shot_b])
    plan = ProductionPlan(
        project=ProjectInfo(title="t", source_file=""),
        scenes=[scene],
        validation=Validation(),
        timeline=["sc:s01", "sc:s02"],
        beats=[],
    )

    def responder(prompt):
        return json.dumps({
            "shots": [
                {"index": 1, "characters": [{"name": "顾琰宸", "role": "商业精英"}]},
                {"index": 2, "characters": [{"name": "林薇薇", "role": "秘书"}]},
            ],
            "character_profiles": [
                {"name": "顾琰宸", "appearance": "深色高定西装，身姿挺拔"},
            ],
        })

    backend = FakeTextBackend(responder=responder)
    enriched = analyze_script(plan, backend)
    _check("顾琰宸 外观档案入库", enriched.character_profiles.get("顾琰宸") == "深色高定西装，身姿挺拔",
           str(enriched.character_profiles))
    _check("林薇薇 规则兜底（role）入库", "秘书" in enriched.character_profiles.get("林薇薇", ""),
           str(enriched.character_profiles))
    _check("timeline 保留", enriched.timeline == ["sc:s01", "sc:s02"], str(enriched.timeline))
    _check("shot.s01 角色外观回填", enriched.scenes[0].shots[0].characters[0].appearance
           == "深色高定西装，身姿挺拔", enriched.scenes[0].shots[0].characters[0].appearance)

    # 约束接线：build_plan_intents → CharacterLock.appearance
    intents = build_plan_intents(enriched)
    it = intents["sc:s01"]
    lock = next(l for l in it.constraint["character_locks"] if l["name"] == "顾琰宸")
    _check("CharacterLock.appearance=外观档案", lock["appearance"] == "深色高定西装，身姿挺拔",
           str(lock))


def test_analyze_script_preserves_existing_profiles() -> None:
    """既有 plan.character_profiles（持久化权威）不被 Qwen 覆盖。"""
    from director.script_analyzer import _merge_characters  # noqa: F401

    shot_a = Shot(shot_id="s01", source_text="顾琰宸走进办公室。", duration_sec=5,
                  characters=[Character(name="顾琰宸")])
    scene = Scene(scene_id="sc", title="办公室", location_name="办公室", shots=[shot_a])
    plan = ProductionPlan(
        project=ProjectInfo(title="t", source_file=""),
        scenes=[scene],
        validation=Validation(),
        character_profiles={"顾琰宸": "已审核外观"},
    )

    def responder(prompt):
        return json.dumps({
            "shots": [{"index": 1, "characters": [{"name": "顾琰宸"}]}],
            "character_profiles": [{"name": "顾琰宸", "appearance": "Qwen新外观"}],
        })

    enriched = analyze_script(plan, FakeTextBackend(responder=responder))
    _check("既有档案优先，Qwen 不覆盖", enriched.character_profiles.get("顾琰宸") == "已审核外观",
           str(enriched.character_profiles))


def main() -> None:
    test_batch_success()
    test_batch_prompt_contains_all_shots()
    test_batch_fail_fallback_shot()
    test_analyze_script_single_session()
    test_analyze_scene_guard_outside_session()
    test_corrupt_json_retry()
    test_corrupt_json_all_fail_keeps_rule()
    test_merge_shot_rule_priority()
    test_merge_shot_qwen_adds_new()
    test_merge_characters_canonical_dedup()
    test_analyze_shot_timeout_returns_rule()
    test_analyze_shot_none_returns_rule()
    test_analyze_script_all_fail_keeps_plan()
    test_empty_scene_returns_without_call()
    test_clean_field_type_robustness()
    test_bounded_text_truncates()
    test_no_asset_fields()
    test_batch_partial_and_index_match()
    test_batch_no_index_order_fallback()
    test_entities_merge()
    test_clean_entities_validation()
    test_prompt_script_only_constraint()
    test_analyze_script_feeds_director_intent()
    test_no_qwen_director_intent_still_valid()
    # P2：Qwen 角色外观档案
    test_clean_character_profiles_normalizes()
    test_merge_characters_appearance_merge()
    test_analyze_scene_character_profiles_batch()
    test_analyze_script_character_profiles_flow()
    test_analyze_script_preserves_existing_profiles()

    print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
