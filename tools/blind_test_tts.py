#!/usr/bin/env python3
"""三引擎盲听测试 runner（2026-08-16 用户拍板：先盲听再定最终 TTS 引擎）。

把同一段《吐槽成真》台词分别交给三套引擎合成，匿名成 A/B/C 供盲听打分，
打分后再揭晓映射。答案在 ``blind_test/mapping.key``，评分前不要打开。

子命令：
  prepare-refs              用 Edge-TTS 生成四角色参考音频 + refs.json
                            （GPT-SoVITS / CosyVoice 3 的 zero-shot 克隆目标）
  synth --engine <引擎>     用指定引擎合成整段台词 → blind_test/raw/<引擎>/
  arrange [--seed N]        三引擎结果洗牌成 blind_test/A|B|C（答案存 mapping.key）
  reveal                    揭晓 A/B/C 对应的引擎
  clone --file <wav> ...    MiniMax 音色快速复刻（可选，Phase B 专属音色）

引擎参数：
  minimax    云端 MiniMax Speech（需环境变量 MINIMAX_API_KEY；--minimax-model 可换模型）
  gpt-sovits 本地 GPT-SoVITS V4（需先起官方 api.py，默认 http://127.0.0.1:9880；--base-url 可覆盖）
  cosyvoice3 本地 CosyVoice 3（需先起 tools/cosyvoice_server.py，默认 http://127.0.0.1:8898；--base-url 可覆盖）

运行环境：ComfyUI .venv（已装 edge-tts / requests）：
  "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" tools\\blind_test_tts.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import random
import shutil
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from director.mixer import wav_duration  # noqa: E402
from director.tts_engines import create_backend  # noqa: E402

BLIND_DIR = os.path.join(_REPO, "blind_test")
SCRIPT_FILE = os.path.join(BLIND_DIR, "script.json")
REFS_DIR = os.path.join(BLIND_DIR, "refs")
REFS_FILE = os.path.join(REFS_DIR, "refs.json")
RAW_DIR = os.path.join(BLIND_DIR, "raw")
ANON_DIR = os.path.join(BLIND_DIR, "blind")
MAPPING_FILE = os.path.join(BLIND_DIR, "mapping.key")

ROLES = {
    "linweiwei": {"name": "林薇薇", "desc": "年轻女声"},
    "guyanchen": {"name": "顾琰宸", "desc": "低沉男声"},
    "narrator": {"name": "旁白·林晓", "desc": "轻松吐槽感"},
    "system": {"name": "系统", "desc": "电子音"},
}

# 角色参考音频（prepare-refs 用 Edge-TTS 生成；克隆引擎以这段音频为音色目标）。
REF_EDGE_VOICES = {
    "linweiwei": "zh-CN-XiaoxiaoNeural",
    "guyanchen": "zh-CN-YunjianNeural",
    "narrator": "zh-CN-XiaoyiNeural",
    "system": "zh-CN-YunxiNeural",
}
REF_TEXTS = {
    "linweiwei": "我是林薇薇。这部漫画里，我可是要活到最后的人。今晚这场戏，才刚刚开始。",
    "guyanchen": "我是顾琰宸。江海市的天，我说了算。你最好记住这一点。",
    "narrator": "林晓揉了揉眼睛，继续刷着评论。这个漫画的女主角，真是让人一言难尽啊。",
    "system": "系统启动。吐槽值收集程序运行中。请宿主继续保持吐槽。",
}


def _wav_sec(path: str) -> float:
    try:
        return round(wav_duration(path), 3)
    except (ValueError, OSError):
        return 0.0


# ---------------------------------------------------------------------------
# prepare-refs：用 Edge-TTS 生成四角色克隆参考音频
# ---------------------------------------------------------------------------
async def _prepare_refs(args: argparse.Namespace) -> int:
    os.makedirs(REFS_DIR, exist_ok=True)
    from director.tts_engine import EdgeTtsBackend  # noqa: PLC0415

    backend = EdgeTtsBackend()
    backend.preflight()
    print("生成角色参考音频（Edge-TTS）…")
    refs: dict = {}
    for role in sorted(REF_EDGE_VOICES):
        edge_voice = REF_EDGE_VOICES[role]
        text = REF_TEXTS[role]
        out = os.path.join(REFS_DIR, f"{role}.wav")
        await backend.synthesize(text, edge_voice, out)
        refs[role] = {
            "wav": f"{role}.wav",  # 相对 refs/ 目录
            "prompt_text": text,
            "prompt_language": "zh",
            "edge_voice": edge_voice,
            "duration_sec": _wav_sec(out),
        }
        print(f"  [{role}] {ROLES[role]['name']:<8} {edge_voice:<24} {round(refs[role]['duration_sec'], 1)}s")
    with open(REFS_FILE, "w", encoding="utf-8") as f:
        json.dump(refs, f, ensure_ascii=False, indent=2)
    print(f"\n参考音频清单已写：{os.path.relpath(REFS_FILE, _REPO)}")
    print("（想换真人参考：把 blind_test/refs/<角色>.wav 换成你录的音频，"
          "并同步改 refs.json 里的 prompt_text；克隆引擎会以你的音频为音色目标）")
    return 0


# ---------------------------------------------------------------------------
# synth：用指定引擎合成整段台词
# ---------------------------------------------------------------------------
async def _synth(args: argparse.Namespace) -> int:
    if not os.path.exists(SCRIPT_FILE):
        raise SystemExit(f"缺 {SCRIPT_FILE}（先建脚本）")
    with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
        script = json.load(f)

    kwargs: dict = {}
    if args.base_url:
        kwargs["base_url"] = args.base_url
    if args.refs_file:
        kwargs["refs_file"] = args.refs_file
    if args.engine == "minimax" and args.minimax_model:
        kwargs["model"] = args.minimax_model
    backend = create_backend(args.engine, **kwargs)
    backend.preflight()

    out_dir = os.path.join(RAW_DIR, args.engine)
    os.makedirs(out_dir, exist_ok=True)
    print(f"引擎 [{args.engine}] 合成 {script.get('scene', '')} 共 {len(script['lines'])} 句…")
    results = []
    total = 0.0
    for i, line in enumerate(script["lines"], start=1):
        speaker = str(line.get("speaker", ""))
        voice_id = f"voice_{speaker}" if speaker else ""
        text = str(line["text"])
        out = os.path.join(out_dir, f"{i:02d}_{speaker or 'unknown'}.wav")
        try:
            await backend.synthesize(
                text, voice_id, out,
                emotion=str(line.get("emotion", "")),
                delivery=str(line.get("delivery", "")),
            )
        except Exception as exc:  # noqa: BLE001 - 定位失败行
            raise SystemExit(
                f"合成失败 [{args.engine}] 第 {i} 句（{ROLES.get(speaker, {}).get('name', speaker)}）：\n  {exc}"
            )
        dur = _wav_sec(out)
        total += dur
        results.append({"index": i, "speaker": speaker, "file": out, "duration_sec": dur})
        print(f"  [{i:02d}] {ROLES.get(speaker, {}).get('name', speaker):<8} {dur:.2f}s  {os.path.relpath(out, BLIND_DIR)}")

    summary = {
        "engine": args.engine,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_sec": round(total, 2),
        "lines": results,
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n完成：{args.engine} {len(results)} 句，总时长约 {round(total, 1)}s → {os.path.relpath(out_dir, _REPO)}")
    return 0


# ---------------------------------------------------------------------------
# arrange：三引擎结果洗牌成 A/B/C 盲听目录（不打印映射，答案进 mapping.key）
# ---------------------------------------------------------------------------
def _arrange(args: argparse.Namespace) -> int:
    if not os.path.isdir(RAW_DIR):
        raise SystemExit("还没有 raw/ 目录，先跑 synth --engine …")
    engines = sorted(
        d for d in os.listdir(RAW_DIR)
        if os.path.isdir(os.path.join(RAW_DIR, d)) and os.path.exists(os.path.join(RAW_DIR, d, "summary.json"))
    )
    if len(engines) < 2:
        raise SystemExit(f"raw/ 下完成合成的引擎不足（{engines}），先跑 synth --engine … 至少 2 个引擎")
    labels = ["A", "B", "C", "D", "E"][: len(engines)]
    mapping = dict(zip(engines, labels))  # engine → label
    rng = random.Random(args.seed)
    mapping = {e: l for e, l in sorted(mapping.items(), key=lambda kv: rng.random())}

    if os.path.isdir(ANON_DIR):
        shutil.rmtree(ANON_DIR)
    for engine, label in mapping.items():
        src = os.path.join(RAW_DIR, engine)
        dst = os.path.join(ANON_DIR, label)
        os.makedirs(dst, exist_ok=True)
        # ⚠️ 只复制音频文件：summary.json 含 engine 字段会泄露答案，绝不进盲听目录。
        for fname in sorted(os.listdir(src)):
            if fname.lower().endswith((".wav", ".mp3", ".flac", ".m4a")):
                shutil.copy2(os.path.join(src, fname), os.path.join(dst, fname))
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump({"mapping": {e: l for e, l in mapping.items()}, "seed": args.seed}, f, ensure_ascii=False, indent=2)

    print("盲听目录已生成（blind_test/blind/）：")
    for label in sorted(mapping.values()):
        files = sorted(os.listdir(os.path.join(ANON_DIR, label)))
        print(f"  {label}/  {len(files)} 个文件")
    print(f"\n答案已加密存 {os.path.relpath(MAPPING_FILE, _REPO)}——打分完再运行 reveal 揭晓。")
    print("打分表：blind_test/打分表.md（打开即可打分）")
    return 0


# ---------------------------------------------------------------------------
# reveal：揭晓 A/B/C 对应引擎
# ---------------------------------------------------------------------------
def _reveal(args: argparse.Namespace) -> int:
    if not os.path.exists(MAPPING_FILE):
        raise SystemExit("还没有 mapping.key，先跑 arrange")
    with open(MAPPING_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    inv = {label: engine for engine, label in (data.get("mapping") or {}).items()}
    print("盲听答案揭晓：")
    for label in sorted(inv):
        print(f"  {label}/ = {inv[label]}")
    print("\n恭喜完成盲听！把打分表里的最终选择告诉 Claude，接下来就锁定正式引擎。")
    return 0


# ---------------------------------------------------------------------------
# clone：MiniMax 音色快速复刻（可选）
# ---------------------------------------------------------------------------
def _clone(args: argparse.Namespace) -> int:
    from director.tts_engines.minimax_speech import clone_voice_sync  # noqa: PLC0415

    api_key = args.api_key or os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise SystemExit("需 MINIMAX_API_KEY（--api-key 或环境变量）")
    vid = clone_voice_sync(
        api_key=api_key,
        file_path=args.file,
        voice_id=args.voice_id,
        prompt_audio=args.prompt_audio,
        prompt_text=args.prompt_text or "",
        model=args.model,
    )
    print(f"克隆成功：voice_id = {vid}")
    print("把它填进 Director 后端 voice_map 即完成接入（Phase B 再做）。")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", help="本地引擎服务地址（默认 minimax 云端 / gpt-sovits :9880 / cosyvoice3 :8898）")
    parser.add_argument("--refs-file", help="refs.json 路径（默认 blind_test/refs/refs.json）")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="blind_test_tts",
        description="三引擎 TTS 盲听测试（MiniMax Speech / GPT-SoVITS / CosyVoice 3）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare-refs", help="用 Edge-TTS 生成四角色克隆参考音频")
    p.set_defaults(func=_prepare_refs)

    p = sub.add_parser("synth", help="用指定引擎合成整段台词")
    p.add_argument("--engine", required=True, choices=["minimax", "gpt-sovits", "cosyvoice3"])
    p.add_argument("--minimax-model", default=None, help="MiniMax 模型（默认 speech-2.8-hd）")
    _add_common(p)
    p.set_defaults(func=_synth)

    p = sub.add_parser("arrange", help="三引擎结果洗牌成 A/B/C 盲听目录")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=_arrange)

    p = sub.add_parser("reveal", help="揭晓 A/B/C 对应引擎")
    p.set_defaults(func=_reveal)

    p = sub.add_parser("clone", help="MiniMax 音色快速复刻（可选，需实名认证）")
    p.add_argument("--file", required=True, help="克隆音频（≥10s 干音）")
    p.add_argument("--voice-id", required=True, help="自定义 voice_id（如 clone_linweiwei）")
    p.add_argument("--prompt-audio", default=None, help="可选：<8s 角色风格示例")
    p.add_argument("--prompt-text", default="", help="可选：示例音频转写文本")
    p.add_argument("--api-key", default=None, help="MiniMax API Key（缺省读环境变量）")
    p.add_argument("--model", default="speech-2.8-hd")
    p.set_defaults(func=_clone)

    args = parser.parse_args()
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    if asyncio.iscoroutinefunction(func):
        return asyncio.run(func(args))
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
