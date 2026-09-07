#!/usr/bin/env python3
"""Phase 1 混音真机冒烟验证（FFmpeg ducking：H3 原音 + TTS 人声 + sidechaincompress）。

流程（一条命令跑完，不依赖已生成的 H3 视频，也无需本机装 folder_paths）：
  1. POST /tts/synthesize 生成《吐槽在漫画里封神》shot_002 两句对白（角色+旁白），
     从响应拿 shot_dir（后端 folder_paths 计算的落盘目录，standalone-env 不必知道路径）；
  2. 用本机 ffmpeg 在 shot_dir 里造一个 8 秒「测试 H3 视频」
     （testsrc 画面 + 440Hz 正弦环境音）当 video.mp4；
  3. POST /tts/mix（mode=h3_tts）混音成 final.mp4；
  4. 打印落盘路径。

验收：
  - final.mp4 画面=测试画面，音轨=「440Hz 环境音 + 两句对白」，且对白期间 440Hz 被压低
    （ducking 生效），对白结束恢复 —— 打开 final.mp4 试听即知。
  - manifest.json 每行写回 start_sec/end_sec/duration_sec（重混音幂等复用锚点）。

前置：
  1. Comfy Desktop 已重启（http_routes.py 已加载 /tts/mix，⛔ 不重启会 404/405）。
  2. 后端 .venv 已装 edge-tts（Phase 0 已验收）；本机 PATH 有 ffmpeg（造测试视频用）。

用法（用户本机，完整可复制）：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" tools/tts_mix_smoke_check.py
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8188"
PROJECT = "吐槽在漫画里封神"
SHOT = "shot_002"  # 独立镜头，不碰 shot_001 已有产物
DURATION = 8


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            detail = "<无法读取响应体>"
        print("  [HTTP %s] 响应体: %s" % (exc.code, detail))
        raise


def main():
    ff = shutil.which("ffmpeg")
    if not ff:
        print("本机 PATH 无 ffmpeg，无法造测试视频。请安装 FFmpeg 后重试。")
        sys.exit(1)

    print("\n== 1) POST /tts/synthesize（生成两句对白）==")
    synth = _post(
        "/minimax/director/tts/synthesize",
        {
            "project_name": PROJECT,
            "shot_id": SHOT,
            "scene_id": "scene_01",
            "ambient": "教室铃声渐弱，窗外蝉鸣",
            "music": "轻快校园配乐渐起",
            "lines": [
                {"text": "完了，穿越了。我居然穿进了自己画的漫画里！", "speaker": "林薇薇", "voice_id": "voice_林薇薇", "emotion": "震惊", "delivery": "语速偏快"},
                {"text": "系统提示：吐槽值 +100，请继续你的表演。", "voice_type": "system_voice", "voice_id": "voice_system_electronic", "delivery": "电子"},
            ],
        },
    )
    print(json.dumps(synth, ensure_ascii=False, indent=2))
    if not synth.get("ok"):
        print("FAIL: TTS 合成失败")
        sys.exit(1)

    # 落盘路径由后端 folder_paths 计算，客户端直接从 synthesize 响应拿（避免猜路径）
    shot_dir = synth.get("shot_dir")
    if not shot_dir:
        print("FAIL: TTS 合成响应缺 shot_dir")
        sys.exit(1)

    print("\n== 2) 造测试 H3 视频（testsrc + 440Hz 环境音）==")
    video_path = os.path.join(shot_dir, "video.mp4")
    if not os.path.isfile(video_path):
        subprocess.run(
            [
                ff, "-y",
                "-f", "lavfi", "-i", f"testsrc=duration={DURATION}:size=1280x720:rate=24",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", video_path,
            ],
            check=True,
        )
        print("  video.mp4 ->", video_path)
    else:
        print("  video.mp4 已存在，跳过造视频：", video_path)

    print("\n== 3) POST /tts/mix（H3 原音 + TTS 叠加 + ducking）==")
    mix = _post(
        "/minimax/director/tts/mix",
        {"project_name": PROJECT, "shot_id": SHOT, "mode": "h3_tts"},
    )
    print(json.dumps(mix, ensure_ascii=False, indent=2))
    if not mix.get("ok"):
        print("FAIL: 混音失败")
        sys.exit(1)

    print("\n== 4) 验收 ==")
    print("final.mp4 :", mix.get("final_file"))
    print("h3_audio  :", mix.get("h3_audio_file"))
    print("line_count:", mix.get("line_count"))
    for ln in mix.get("lines", []):
        print("  line_%03d  start=%ss end=%ss dur=%ss" % (
            ln["index"], ln["start_sec"], ln["end_sec"], ln["duration_sec"]))
    print("\n打开 final.mp4：画面=测试图案，音轨=440Hz 环境音 + 两句对白，")
    print("对白期间环境音被压低（ducking 生效），对白结束恢复。")


if __name__ == "__main__":
    main()
