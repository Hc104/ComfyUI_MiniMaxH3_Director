#!/usr/bin/env python3
"""Phase 0 TTS 真机冒烟验证（Edge-TTS，管线测试引擎）。

前置条件：
  1. Comfy Desktop 已重启（http_routes.py 新路由已加载，⛔ 不重启会 404/405）。
  2. standalone-env 已安装 edge-tts。
用法（用户本机，完整可复制）：
    "D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe" tools/tts_smoke_check.py

验收：
  - 打印 engine=edge-tts + 音色列表数量；
  - POST /tts/synthesize 生成《吐槽在漫画里封神》shot_001 两句对白（角色+旁白）；
  - 展示分层落盘目录，播放 tts/line_001.wav 应能听清「完了，穿越了。我居然穿进了自己画的漫画里！」
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8188"
PROJECT = "吐槽在漫画里封神"
SHOT = "shot_001"


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
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 后端返回 aiohttp json_response → 读响应体拿到 error 字段（定位 500 根因）
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            detail = "<无法读取响应体>"
        print("  [HTTP %s] 响应体: %s" % (exc.code, detail))
        raise


def main():
    print("== 1) GET /minimax/director/tts/voices ==")
    try:
        voices = _get("/minimax/director/tts/voices")
    except Exception as exc:  # noqa: BLE001
        print("请求失败：确认 Comfy Desktop 已重启加载新路由。\n  ", exc)
        sys.exit(1)
    print("engine:", voices.get("engine"), "| voices:", len(voices.get("voices", [])))
    if not voices.get("ok"):
        print("FAIL:", voices)
        sys.exit(1)

    print("\n== 2) POST /minimax/director/tts/synthesize ==")
    body = {
        "project_name": PROJECT,
        "shot_id": SHOT,
        "scene_id": "scene_01",
        "ambient": "教室铃声渐弱，窗外蝉鸣",
        "music": "轻快校园配乐渐起",
        "lines": [
            {"text": "完了，穿越了。我居然穿进了自己画的漫画里！", "speaker": "林薇薇", "voice_id": "voice_林薇薇", "emotion": "震惊", "delivery": "语速偏快"},
            {"text": "林晓感觉胸口那股郁结之气缓缓散开。", "voice_type": "narration", "voice_id": "voice_narrator"},
        ],
    }
    try:
        resp = _post("/minimax/director/tts/synthesize", body)
    except Exception as exc:  # noqa: BLE001
        print("合成请求失败：\n  ", exc)
        sys.exit(1)
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    if not resp.get("ok"):
        print("FAIL:", resp)
        sys.exit(1)

    print("\n== 3) 分层落盘目录 ==")
    print("shot_dir  :", resp.get("shot_dir"))
    print("tts_dir   :", resp.get("tts_dir"))
    print("manifest  :", resp.get("manifest_file"))
    print("line_count:", resp.get("line_count"))
    print("\n验收：打开 shot_dir/tts/line_001.wav，应能听清")
    print('      「完了，穿越了。我居然穿进了自己画的漫画里！」')


if __name__ == "__main__":
    main()
