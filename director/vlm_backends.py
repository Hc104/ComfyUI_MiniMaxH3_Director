"""VLM 视觉后端抽象接口（待办⑤，里程碑 A）。

设计决策（用户 2026-08-07）：
- 技术选型不写死单一方案，用抽象接口解耦——所有导演逻辑只依赖 ``VisionBackend.analyze``，
  不感知具体后端；以后换模型 / 推理库（llama.cpp → transformers → vLLM…）不改导演代码。
- 场景不是聊天：单张/少量关键帧理解 + 结构化 JSON 输出，因此模型不常驻，
  段间懒加载、用完立即释放，不与 MiniMax H3 视频生成抢显存（16GB 显存 + 24GB 内存）。

用法：
    backend = QwenLlamaBackend(
        model_path=...,
        mmproj_path=...,
        n_gpu_layers=-1,
        n_ctx=4096,
    )
    text = backend.analyze(images=[pil_image], prompt="...")
    backend.close()
"""

from __future__ import annotations

import base64
import io
import logging
import os

from typing import Any

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.vlm_backends")


def _pil_to_b64_url(image: "Any", max_side: int = 512) -> str:
    """PIL 图片 → base64 data URL（llama.cpp / transformers 视觉输入通用格式）。

    VLM 只需理解画面语义，无需原分辨率；缩到 max_side 内可显著降低显存与 token。
    """
    try:
        from PIL import Image

        if not isinstance(image, Image.Image):
            image = Image.fromarray(image) if hasattr(image, "shape") else Image.open(io.BytesIO(image))
        img = image.convert("RGB")
        w, h = img.size
        scale = min(1.0, float(max_side) / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:  # pragma: no cover - 图片预处理失败不应阻断主流程
        log.warning("PIL 转 base64 失败: %s", exc)
        raise


class VisionBackend:
    """视觉后端抽象基类。所有导演反馈逻辑只依赖此接口。"""

    name: str = "base"

    def analyze(
        self,
        images: list[Any],
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """喂一组图片 + 提示词，返回模型原始文本输出。

        Args:
            images: PIL.Image 列表（1~N 张关键帧 / 参考图）。
            prompt: 用户提示词（导演反馈模块负责构造强 JSON 模板）。
            max_tokens: 输出最大 token。
            temperature: 采样温度（0 = 确定性，适合 JSON 结构化输出）。
        """
        raise NotImplementedError

    def close(self) -> None:
        """释放模型与显存。段间必须调用，避免与 H3 生成抢显存。"""
        raise NotImplementedError

    def __enter__(self) -> "VisionBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class QwenLlamaBackend(VisionBackend):
    """llama.cpp + Qwen3-VL GGUF 实现（推荐，int4 显存最小）。

    依赖：``pip install llama-cpp-python``（CUDA 版，见 QWEN3VL_FEEDBACK_PLAN.md）。
    llama.cpp 通过 mmproj 视觉投影文件支持 Qwen3-VL（picture 输入走多模态路径）。
    """

    name = "llama.cpp"

    def __init__(
        self,
        model_path: str,
        mmproj_path: str | None = None,
        *,
        n_gpu_layers: int = -1,
        n_ctx: int = 4096,
        n_threads: int | None = None,
        verbose: bool = False,
    ):
        self.model_path = model_path
        self.mmproj_path = mmproj_path
        self.n_gpu_layers = n_gpu_layers
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.verbose = verbose
        self._llm: Any = None

    # -- 懒加载 -----------------------------------------------------------
    @property
    def llm(self) -> Any:
        if self._llm is None:
            self._llm = self._load()
        return self._llm

    def _load(self) -> Any:
        try:
            from llama_cpp import Llama
        except ImportError as exc:  # pragma: no cover - 依赖缺失的清晰提示
            raise RuntimeError(
                "未安装 llama-cpp-python。请在 ComfyUI 的 Python 环境执行：\n"
                "  pip install llama-cpp-python --extra-index-url "
                "https://abetlen.github.io/llama-cpp-python/whl/cu124\n"
                "（或按你的 CUDA 版本选择预编译 wheel）"
            ) from exc
        if not self.model_path or not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Qwen3-VL 主模型不存在: {self.model_path}")
        if self.mmproj_path and not os.path.exists(self.mmproj_path):
            raise FileNotFoundError(f"Qwen3-VL 视觉投影(mmproj)不存在: {self.mmproj_path}")
        log.info(
            "加载 Qwen3-VL (llama.cpp): model=%s mmproj=%s gpu_layers=%s ctx=%s",
            os.path.basename(self.model_path),
            os.path.basename(self.mmproj_path) if self.mmproj_path else None,
            self.n_gpu_layers,
            self.n_ctx,
        )
        kwargs: dict[str, Any] = {
            "model_path": self.model_path,
            "n_ctx": self.n_ctx,
            "n_gpu_layers": self.n_gpu_layers,
            "verbose": self.verbose,
        }
        if self.mmproj_path:
            kwargs["mmproj"] = self.mmproj_path
        if self.n_threads is not None:
            kwargs["n_threads"] = self.n_threads
        return Llama(**kwargs)

    # -- VisionBackend 接口 ----------------------------------------------
    def analyze(
        self,
        images: list[Any],
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        if not images:
            raise ValueError("QwenLlamaBackend.analyze 需要至少一张图片。")
        llm = self.llm
        content: list[dict[str, Any]] = []
        for img in images:
            content.append(
                {"type": "image_url", "image_url": {"url": _pil_to_b64_url(img)}}
            )
        content.append({"type": "text", "text": prompt})
        resp = llm.create_chat_completion(
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            return str(resp["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover
            raise RuntimeError(f"llama.cpp 返回格式异常: {resp}") from exc

    def close(self) -> None:
        if self._llm is not None:
            try:
                self._llm.close()
            except Exception as exc:  # pragma: no cover - 释放失败不阻断
                log.warning("llama.cpp close 失败: %s", exc)
            self._llm = None
            # llama.cpp 使用自己的显存分配，不经过 torch；但 ComfyUI 的 torch cache
            # 可能被 H3 占用，这里统一清一次避免残留占用。
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # pragma: no cover
                pass


class QwenTransformersBackend(VisionBackend):
    """transformers + qwen-vl-utils 实现（用户 2026-08-07 选定为主路径）。

    背景：ComfyUI Desktop 的 Python 是 3.13，官方 llama-cpp-python 预编译 index
    （cu121/cu124）与 PyPI 均无 cp313 Windows wheel → GGUF 路径需 MSVC 源码编译，
    用户选择 transformers 后端（免编译器）。此后端零编译、纯 pip 依赖。

    需要：``pip install "transformers>=4.53" qwen-vl-utils accelerate``
    模型目录：``models/vlm/Qwen3-VL-4B-Instruct``（transformers 原版目录，含 config.json）。
    默认 fp16（Windows 下 bitsandbytes 4bit 兼容性不稳，先求稳；显存约 9GB，
    段间错峰 cleanup 后 16GB 足够）。
    """

    name = "transformers"

    def __init__(
        self,
        model_dir: str,
        *,
        device: str = "cuda",
        load_in_4bit: bool = False,
        max_new_tokens: int = 512,
    ):
        self.model_dir = model_dir
        self.device = device
        self.load_in_4bit = load_in_4bit
        self.max_new_tokens = max_new_tokens
        self._pipe: Any = None
        self._processor: Any = None

    @property
    def pipe(self) -> Any:
        if self._pipe is None:
            self._pipe = self._load()
        return self._pipe

    def _load(self) -> Any:
        try:
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "未安装 transformers。请执行：\n"
                "  pip install \"transformers>=4.53\" qwen-vl-utils accelerate"
            ) from exc
        if not os.path.isdir(self.model_dir):
            raise FileNotFoundError(f"transformers 模型目录不存在: {self.model_dir}")
        if not os.path.isfile(os.path.join(self.model_dir, "config.json")):
            raise FileNotFoundError(f"模型目录缺少 config.json，不是 transformers 格式: {self.model_dir}")
        kwargs: dict[str, Any] = {}
        if self.load_in_4bit:
            # 4bit 需要 bitsandbytes；Windows 下兼容性不稳，默认关闭（fp16）。
            try:
                from transformers import BitsAndBytesConfig

                kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True)
            except ImportError:  # pragma: no cover
                log.warning("load_in_4bit=True 但未装 bitsandbytes，回退 fp16")
                kwargs.pop("quantization_config", None)
        log.info(
            "加载 Qwen3-VL (transformers): %s 4bit=%s fp16",
            self.model_dir,
            self.load_in_4bit,
        )
        processor = AutoProcessor.from_pretrained(self.model_dir, trust_remote_code=True)
        model = AutoModelForImageTextToText.from_pretrained(
            self.model_dir, device_map="auto", trust_remote_code=True, **kwargs
        )
        self._processor = processor
        return model

    def analyze(
        self,
        images: list[Any],
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        model = self.pipe
        processor = self._processor
        try:
            from qwen_vl_utils import process_vision_info  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("transformers 后端需要 qwen-vl-utils 包。pip install qwen-vl-utils") from exc

        import torch

        messages = [
            {
                "role": "user",
                "content": [
                    *[{"type": "image", "image": img} for img in images],
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, _ = process_vision_info(messages)
        inputs = processor(
            text=[text], images=image_inputs, padding=True, return_tensors="pt"
        ).to(model.device)
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=(temperature > 0))
        out = out[:, inputs["input_ids"].shape[1]:]
        return processor.batch_decode(out, skip_special_tokens=True)[0].strip()

    def close(self) -> None:
        self._pipe = None
        self._processor = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # pragma: no cover
            pass


def default_vlm_dir() -> str:
    """返回 ComfyUI 默认 vlm 模型目录（models/vlm）。"""
    try:
        import folder_paths

        return os.path.join(folder_paths.models_dir, "vlm")
    except Exception:  # pragma: no cover - 非 ComfyUI 环境（自测脚本）回退环境变量/相对路径
        return os.environ.get("COMFY_VLM_DIR", os.path.join(os.getcwd(), "models", "vlm"))


def find_default_qwen_files() -> tuple[str | None, str | None]:
    """在默认 vlm 目录中查找 Qwen3-VL 主模型 + mmproj。

    返回 (model_path, mmproj_path)；找不到对应文件返回 None。
    """
    vlm_dir = default_vlm_dir()
    if not os.path.isdir(vlm_dir):
        return None, None
    model_path = None
    mmproj_path = None
    for fname in sorted(os.listdir(vlm_dir)):
        if fname.lower().endswith(".gguf"):
            low = fname.lower()
            if "mmproj" in low:
                if mmproj_path is None:
                    mmproj_path = os.path.join(vlm_dir, fname)
            elif model_path is None:
                model_path = os.path.join(vlm_dir, fname)
    return model_path, mmproj_path


def find_default_transformers_dir() -> str | None:
    """在默认 vlm 目录中查找 transformers 格式的 Qwen3-VL 模型目录。

    判定：vlm 下的子目录含 config.json（transformers 模型标志）。返回目录路径或 None。
    """
    vlm_dir = default_vlm_dir()
    if not os.path.isdir(vlm_dir):
        return None
    for fname in sorted(os.listdir(vlm_dir)):
        cand = os.path.join(vlm_dir, fname)
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "config.json")):
            return cand
    return None


def create_default_backend() -> VisionBackend:
    """便捷工厂：优先 transformers 模型目录，其次 GGUF（llama.cpp）。

    - transformers 后端（用户 2026-08-07 选定）：零编译，Python 3.13 可用，
      vlm 目录下有含 config.json 的子目录即走此路。
    - GGUF 路径需要 llama-cpp-python（cp313 无预编译 wheel，需 MSVC 源码编译），
      仅在 transformers 目录缺失时尝试。
    找不到模型或依赖缺失时抛错，由调用方（executor 回调）捕获并降级为「无反馈」。
    """
    tdir = find_default_transformers_dir()
    if tdir:
        return QwenTransformersBackend(model_dir=tdir, load_in_4bit=False)
    model_path, mmproj_path = find_default_qwen_files()
    if not model_path:
        raise FileNotFoundError(
            f"未在 {default_vlm_dir()} 找到可用的 Qwen3-VL 模型。\n"
            "两种任选其一：\n"
            "  ① transformers 目录（推荐）：放含 config.json 的 Qwen3-VL-4B-Instruct 目录到该路径；\n"
            "  ② GGUF 文件：Qwen3VL-4B-Instruct-Q4_K_M.gguf 与 mmproj（需 llama-cpp-python）。"
        )
    return QwenLlamaBackend(model_path=model_path, mmproj_path=mmproj_path)
