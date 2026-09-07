#!/usr/bin/env python3
"""Edge-TTS 音色连通性探针（诊断「No audio was received」根因，Phase 0 #562 复测）。

用法（用户本机，完整可复制；任意装了 edge-tts 的 python 均可）：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" tools/tts_voice_probe.py

它会依次把同一句原文（含「+100」的完整系统提示）用 7 种音色/参数组合合成到临时目录，
逐行报告成败。判定逻辑：
  - C 失败但 B/D 成功 → 「Yunxi + pitch」组合问题（改系统电子音方案）
  - C 失败且 D 也失败 → pitch 参数格式有问题（改 _delivery_params）
  - B 失败但 A 成功 → Yunxi 音色在你的网络不可用（换系统音色默认值）
  - A 就失败 → Edge-TTS 服务/网络整体不可用（重试或换网络）
  - 偶发失败、重跑即好 → 瞬时云端限流（引擎需要加重试）
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

TEXT = "系统提示：吐槽值 +100，请继续你的表演。"  # 与 smoke 完全一致的原文

# (标签, 音色名, 参数)
COMBOS = [
    ("A. Xiaoxiao 基线", "zh-CN-XiaoxiaoNeural", {}),
    ("B. Yunxi 基线", "zh-CN-YunxiNeural", {}),
    ("C. Yunxi + pitch+5Hz（当前系统电子音配置）", "zh-CN-YunxiNeural", {"pitch": "+5Hz"}),
    ("D. Xiaoxiao + pitch+5Hz", "zh-CN-XiaoxiaoNeural", {"pitch": "+5Hz"}),
    ("E. Yunxi + rate+20%", "zh-CN-YunxiNeural", {"rate": "+20%"}),
    ("F. Yunjian 基线（备选系统男声）", "zh-CN-YunjianNeural", {}),
    ("G. Yunyang 基线（旁白男声）", "zh-CN-YunyangNeural", {}),
]


async def _try_one(edge_tts, label: str, voice: str, params: dict, outdir: str) -> bool:
    path = os.path.join(outdir, label.split(".")[0].strip() + ".mp3")
    try:
        comm = edge_tts.Communicate(TEXT, voice, **params)
        await comm.save(path)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        print(f"  ✓ {label:<42} -> {os.path.basename(path)} ({size} bytes)")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ {label:<42} -> {type(exc).__name__}: {str(exc)[:150]}")
        return False


async def _run_all(edge_tts, outdir: str) -> list:
    results = []
    for label, voice, params in COMBOS:
        results.append(await _try_one(edge_tts, label, voice, params, outdir))
    return results


def main() -> None:
    try:
        import edge_tts  # noqa: PLC0415
    except ImportError:
        print("当前 python 没有 edge-tts，请换有 edge-tts 的 python 运行，或先安装：")
        print('  "D:\\Comfy-Desktop\\ComfyUI (1)\\standalone-env\\python.exe" -m pip install edge-tts')
        sys.exit(1)

    outdir = os.path.join(tempfile.gettempdir(), "tts_probe")
    os.makedirs(outdir, exist_ok=True)
    print(f"探针输出目录: {outdir}\n")
    results = asyncio.run(_run_all(edge_tts, outdir))
    ok = sum(1 for r in results if r)
    print(f"\n{ok}/{len(results)} 组合成功\n")
    if ok < len(results):
        print("判定参考：")
        print("  C 失败但 B/D 成功 → 问题在「Yunxi + pitch」组合（改系统电子音方案）")
        print("  C/D 都失败       → pitch 参数格式有问题（改 _delivery_params）")
        print("  B 失败但 A 成功   → Yunxi 音色在你的网络不可用（换系统音色默认值）")
        print("  A 就失败          → Edge-TTS 服务/网络整体不可用")
        print("  偶发失败、重跑即好 → 瞬时云端限流（引擎加重试）")


if __name__ == "__main__":
    main()
