#!/usr/bin/env python3
"""CosyVoice 3 自托管 TTS 服务（在 CosyVoice 仓库的 conda 环境里运行）。

设计意图（TTS_VOICE_CAST_PLAN.md §6）：把 0.5B 重模型进程与 ComfyUI 主进程隔离——
本服务加载 Fun-CosyVoice3-0.5B，暴露 POST /tts，由
``director.tts_engines.cosyvoice3.CosyVoice3Backend`` 通过 HTTP 调用。
这样 CosyVoice 模型常驻在这个独立进程，ComfyUI 主进程零负担；需要过显存安全门时
（5080 上 H3 生成期间）可以单独停掉/重启本服务。

运行（在 CosyVoice 仓库根目录，conda 环境 cosyvoice）：
    conda activate cosyvoice
    cd /d D:\\...\\CosyVoice
    python D:\\夸克网盘\\ComfyUI_MiniMaxH3_Director\\tools\\cosyvoice_server.py ^
        --model_dir pretrained_models/Fun-CosyVoice3-0.5B --port 8898

接口：
    GET  /health  → {"ok": true, "model_dir": "...", "sample_rate": 24000}
    POST /tts     body={"text": "...", "prompt_text": "...", "prompt_wav": "C:/abs/ref.wav"}
        成功 → 200 audio/wav（24kHz mono，CosyVoice 3 采样率）
        失败 → 400/500 JSON {"message": "..."}

依赖（conda cosyvoice 环境内已具备）：
    fastapi uvicorn torch torchaudio cosyvoice（仓库源码）

Windows 注意：
    CosyVoice 官方 Windows 痛点 = ttsfrd wheel 仅 linux + pynini/WeTextProcessing 难装；
    官方已给 fallback：设环境变量 WETEXT_OFFLINE / 缺 ttsfrd 时走 wetext 文本前端。
    启动前请按官方 README 完成依赖安装（含 third_party/Matcha-TTS 子模块）。
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys

log = logging.getLogger("cosyvoice_server")


def _ensure_deps() -> None:
    """CosyVoice 官方要求 third_party/Matcha-TTS 在 sys.path（相对仓库根目录）。"""
    cwd = os.getcwd()
    matcha = os.path.join(cwd, "third_party", "Matcha-TTS")
    if os.path.isdir(matcha) and matcha not in sys.path:
        sys.path.insert(0, matcha)


def _main() -> int:
    parser = argparse.ArgumentParser(description="CosyVoice 3 自托管 TTS 服务")
    parser.add_argument(
        "--model_dir",
        default="pretrained_models/Fun-CosyVoice3-0.5B",
        help="Fun-CosyVoice3-0.5B 模型目录（相对/绝对路径）",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _ensure_deps()

    # --- 延迟导入：模块加载轻量，模型只加载一次，全部在请求路径里兜底 ---
    model = None
    sample_rate = 24000

    def _ensure_model():
        global model
        if model is None:
            from cosyvoice.cli.cosyvoice import AutoModel  # noqa: PLC0415

            log.info("加载 Fun-CosyVoice3-0.5B（首次请求触发，约需数秒…）")
            model = AutoModel(model_dir=args.model_dir)
            log.info("模型加载完成")
        return model

    try:
        import uvicorn  # noqa: PLC0415
        from fastapi import FastAPI, Request  # noqa: PLC0415
        from fastapi.responses import JSONResponse, Response  # noqa: PLC0415
        import torchaudio  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 依赖缺失的清晰提示
        print(
            f"[启动失败] 缺依赖：{exc}\n"
            "请在 CosyVoice 仓库的 conda 环境（cosyvoice）里安装：\n"
            "  conda activate cosyvoice\n"
            "  pip install fastapi uvicorn torchaudio\n"
            "并确保已按官方 README 装好 CosyVoice 本体依赖。",
            file=sys.stderr,
        )
        return 1

    app = FastAPI(title="CosyVoice 3 TTS Server")

    @app.get("/health")
    async def health():
        ok = model is not None
        return {
            "ok": ok,
            "model_dir": args.model_dir,
            "sample_rate": sample_rate if ok else None,
        }

    @app.post("/tts")
    async def tts(req: Request):
        data = await req.json()
        text = (data.get("text") or "").strip()
        prompt_text = (data.get("prompt_text") or "").strip()
        prompt_wav = str(data.get("prompt_wav") or "").strip()
        if not text:
            return JSONResponse({"message": "缺少 text"}, status_code=400)
        if not prompt_wav:
            return JSONResponse({"message": "缺少 prompt_wav（参考音频绝对路径）"}, status_code=400)
        if not os.path.exists(prompt_wav):
            return JSONResponse(
                {"message": f"参考音频不存在：{prompt_wav}（先运行 blind_test_tts.py prepare-refs）"},
                status_code=400,
            )
        try:
            m = _ensure_model()
            # inference_zero_shot(text, prompt_text, prompt_wav, stream=False) → dict 含 tts_speech tensor
            result = m.inference_zero_shot(text, prompt_text or "你好。", prompt_wav, stream=False)
            for chunk in result:
                tensor = chunk.get("tts_speech")
                if tensor is None:
                    continue
                buf = io.BytesIO()
                torchaudio.save(buf, tensor, m.sample_rate, format="wav")
                log.info(
                    "TTS 完成 prompt=%s text=%d字 → %d 字节",
                    os.path.basename(prompt_wav), len(text), buf.getbuffer().nbytes,
                )
                return Response(buf.getvalue(), media_type="audio/wav")
            return JSONResponse({"message": "inference_zero_shot 无输出"}, status_code=500)
        except Exception as exc:  # noqa: BLE001 - 合成失败返回给调用方定位
            log.exception("CosyVoice 合成异常")
            return JSONResponse({"message": f"{type(exc).__name__}: {exc}"}, status_code=500)

    log.info("CosyVoice 3 server 启动：http://%s:%d（model_dir=%s）", args.host, args.port, args.model_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
