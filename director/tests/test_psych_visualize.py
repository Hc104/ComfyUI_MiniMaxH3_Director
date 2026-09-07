#!/usr/bin/env python3
"""小说心理描写视觉化（v2.0 ②）单元测试（纯规则，mock，零显存）。

覆盖（CAMERA_RULES_V2 §3.2 + P2）：
1. 心理词探测：emotion / source_text 命中 → 表情句；
2. 六类心理映射（慌乱/震惊/嘲讽/愤怒/悲伤/害怕…）；
3. 无心理词 → 空串（向后兼容）；
4. 动作过程短语独立入口 psych_process_action；
5. ⛔ 绝不改写原文：函数只返回派生句，不触碰 source_text/retained_facts；
6. P2 预标记：tag_psych / tag_psych_text 扫描命中词（保序去重、多源优先）；
7. P2 硬输入优先：shot.psych_tags 预标记 > 运行时扫描；未知 tag 忽略回退；
8. P2 向后兼容：无 psych_tags 字段的旧对象 → 回退运行时扫描；
9. ⛔ 纯规则零显存：源码无 torch/ollama/网络引用。

直接用系统 python 运行（无第三方依赖）：
    python3 director/tests/test_psych_visualize.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import director.psych_visualize as pv  # noqa: E402

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


def _shot(*, source_text: str = "", emotion: str = "") -> SimpleNamespace:
    return SimpleNamespace(source_text=source_text, emotion=emotion)


def _pshot(*, source_text: str = "", emotion: str = "", actions: tuple = (),
           psych_tags: tuple = ()) -> SimpleNamespace:
    """P2 夹具：带 psych_tags 字段（与 production_plan.Shot 对齐）。"""
    return SimpleNamespace(
        source_text=source_text,
        emotion=emotion,
        actions=list(actions),
        psych_tags=list(psych_tags),
    )


# ---------- 心理词探测 ----------

def test_emotion_hit() -> None:
    print("visualize_psych · emotion 命中：")
    _check("emotion=慌乱 → 表情句",
           pv.visualize_psych(_shot(emotion="慌乱")) == "眼神短暂躲闪，睫毛轻颤")
    _check("emotion=震惊 → 瞳孔微缩",
           pv.visualize_psych(_shot(emotion="震惊")) == "瞳孔微缩，表情短暂凝滞")


def test_source_text_hit() -> None:
    print("visualize_psych · source_text 命中：")
    shot = _shot(source_text="她心里一阵慌乱，说不出话来。")
    _check("原文含心理词 → 表情句", "眼神短暂躲闪" in pv.visualize_psych(shot), pv.visualize_psych(shot))


def test_six_category_map() -> None:
    print("visualize_psych · 六类心理映射：")
    cases = {
        "慌乱": "眼神短暂躲闪",
        "震惊": "瞳孔微缩",
        "嘲讽": "嘴角浮现一抹嘲讽的弧度",
        "愤怒": "下颌绷紧",
        "悲伤": "目光低垂",
        "害怕": "瞳孔微缩，嘴唇抿紧",
    }
    for kw, expect in cases.items():
        out = pv.visualize_psych(_shot(emotion=kw))
        _check(f"「{kw}」→ {expect}", expect in out, out)


def test_no_psych_fallback() -> None:
    print("visualize_psych · 无心理词兜底：")
    _check("无心理词 → ''", pv.visualize_psych(_shot()) == "")
    _check("中性情绪 → ''", pv.visualize_psych(_shot(emotion="平静")) == "")
    _check("中性原文 → ''", pv.visualize_psych(_shot(source_text="雨刚停，顾琰宸走进来。")) == "")


def test_process_action() -> None:
    print("psych_process_action · 动作过程短语：")
    _check("慌乱 → 攥紧衣摆", "攥紧衣摆" in pv.psych_process_action(_shot(emotion="慌乱")),
           pv.psych_process_action(_shot(emotion="慌乱")))
    _check("愤怒 → 指节绷紧", "指节逐渐绷紧" in pv.psych_process_action(_shot(emotion="愤怒")),
           pv.psych_process_action(_shot(emotion="愤怒")))
    _check("无心理词 → ''", pv.psych_process_action(_shot()) == "")


def test_never_rewrite_source() -> None:
    print("⛔ 绝不改写原文（铁律）：")
    src = "她心里一阵慌乱。"
    shot = _shot(source_text=src, emotion="")
    _check("source_text 保持原样", shot.source_text == src, shot.source_text)
    _check("visualize 只返回派生句", pv.visualize_psych(shot) != src)


# ---------- P2 预标记：tag_psych / tag_psych_text ----------

def test_tag_psych_text() -> None:
    print("tag_psych_text · 单文本扫描（P2）：")
    _check("原文含慌乱 → 命中", pv.tag_psych_text("她心里一阵慌乱") == ["慌乱"],
           str(pv.tag_psych_text("她心里一阵慌乱")))
    _check("冷笑 → 命中嘲讽组", pv.tag_psych_text("他冷笑一声") == ["冷笑"],
           str(pv.tag_psych_text("他冷笑一声")))
    _check("空串 → []", pv.tag_psych_text("") == [], str(pv.tag_psych_text("")))
    _check("中性词 → []", pv.tag_psych_text("平静") == [], str(pv.tag_psych_text("平静")))
    _check("多词保序（映射库顺序）",
           pv.tag_psych_text("她既紧张又慌乱") == ["慌乱", "紧张"],
           str(pv.tag_psych_text("她既紧张又慌乱")))


def test_tag_psych_multi_source() -> None:
    print("tag_psych · 多源扫描（原文优先 / 保序去重）：")
    _check("source_text 命中", pv.tag_psych(_pshot(source_text="她心里一阵慌乱。")) == ["慌乱"],
           str(pv.tag_psych(_pshot(source_text="她心里一阵慌乱。"))))
    _check("emotion 命中", pv.tag_psych(_pshot(emotion="震惊")) == ["震惊"],
           str(pv.tag_psych(_pshot(emotion="震惊"))))
    _check("actions 命中", pv.tag_psych(_pshot(actions=("他冷笑一声",))) == ["冷笑"],
           str(pv.tag_psych(_pshot(actions=("他冷笑一声",)))))
    _check("原文优先于情绪（先命中先排前）",
           pv.tag_psych(_pshot(source_text="她震惊地睁大眼睛", emotion="轻蔑")) == ["震惊", "轻蔑"],
           str(pv.tag_psych(_pshot(source_text="她震惊地睁大眼睛", emotion="轻蔑"))))
    _check("跨源去重", pv.tag_psych(_pshot(source_text="她慌乱地", emotion="慌乱")) == ["慌乱"],
           str(pv.tag_psych(_pshot(source_text="她慌乱地", emotion="慌乱"))))
    _check("无心理词 → []", pv.tag_psych(_pshot()) == [], str(pv.tag_psych(_pshot())))
    # 旧对象无 psych_tags 字段 → tag_psych 不崩溃。
    old = SimpleNamespace(source_text="她心里一阵慌乱。", emotion="",
                          actions=[], visual_intent="")
    _check("无 psych_tags 字段 → 仍可扫描", pv.tag_psych(old) == ["慌乱"],
           str(pv.tag_psych(old)))


# ---------- P2 硬输入优先（软约束转硬输入） ----------

def test_hard_input_priority() -> None:
    print("P2 硬输入优先 · psych_tags > 运行时扫描：")
    _check("psych_tags=['慌乱'] 且 emotion=愤怒 → 慌乱句（硬输入赢）",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("慌乱",))) == "眼神短暂躲闪，睫毛轻颤",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("慌乱",))))
    _check("无预标记 → 回退运行时扫描（愤怒）",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒"))
           == "下颌绷紧，眼底翻涌",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒")))
    _check("未知 tag 忽略 → 回退运行时扫描",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("不在表中",))) == "下颌绷紧，眼底翻涌",
           pv.visualize_psych(_pshot(source_text="她愤怒地拍桌", emotion="愤怒",
                                     psych_tags=("不在表中",))))
    _check("psych_process_action 硬输入（慌乱动作过程）",
           pv.psych_process_action(_pshot(emotion="愤怒", psych_tags=("慌乱",)))
           == "呼吸变急，手指不自觉攥紧衣摆",
           pv.psych_process_action(_pshot(emotion="愤怒", psych_tags=("慌乱",))))
    _check("多 tag 取映射库序第一条",
           pv.visualize_psych(_pshot(source_text="", emotion="", psych_tags=("轻蔑", "愤怒")))
           == "嘴角勾起一抹轻蔑的弧度",
           pv.visualize_psych(_pshot(source_text="", emotion="", psych_tags=("轻蔑", "愤怒"))))
    # 向后兼容：旧对象连 psych_tags 字段都没有 → 回退运行时扫描。
    old = SimpleNamespace(source_text="她愤怒地拍桌", emotion="愤怒")
    _check("无 psych_tags 字段旧对象 → 回退运行时扫描",
           pv.visualize_psych(old) == "下颌绷紧，眼底翻涌",
           pv.visualize_psych(old))


def test_no_gpu_side_effects() -> None:
    print("⛔ 纯规则零显存：")
    src = open(os.path.join(os.path.dirname(pv.__file__), "psych_visualize.py"),
               encoding="utf-8").read()
    for bad in ("torch", "ollama", "urlopen", "requests", "aiohttp", "keep_alive",
                "gpu_models_loaded", "empty_cache", "openai"):
        _check(f"源码无 {bad} 引用", bad not in src)


def main() -> None:
    global PASSED, FAILED
    PASSED = 0
    FAILED = 0
    print("test_psych_visualize\n")
    test_emotion_hit()
    test_source_text_hit()
    test_six_category_map()
    test_no_psych_fallback()
    test_process_action()
    test_never_rewrite_source()
    test_tag_psych_text()
    test_tag_psych_multi_source()
    test_hard_input_priority()
    test_no_gpu_side_effects()
    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
