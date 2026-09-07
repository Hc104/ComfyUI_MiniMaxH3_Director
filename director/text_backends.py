"""文本后端抽象接口（V1.7 Phase 0：Ollama + TextBackend + 进程级显存互斥）。

设计决策（用户 2026-08-11 拍板，见 V17_PLAN.md）：
- V1.7 = 剧本 → AI 视频制作计划（Script-to-Plan）。剧本解析 / 资产语义匹配 / Prompt 草稿
  是纯文本任务，与现有视觉反馈（``VisionBackend``）不同，新增独立 ``TextBackend`` 抽象；
  业务层只依赖 ``TextBackend.analyze_text / analyze_json``，不感知具体后端。
- **Ollama API 是 V1.7 首先实现的后端**（用户推荐）：本机已有 ``qwen3:14b``（GGUF，Ollama 管理），
  14B Q4 做实体识别/别名归一/Scene-Shot 结构化/五区 Prompt 草稿足够；Ollama 与 ComfyUI
  Python 环境完全解耦，不折腾 GGUF→Transformers，也不装 llama-cpp-python。
- 结构：``TextBackend`` ├── ``OllamaBackend``（V1.7 首先实现）└── ``OpenAICompatibleBackend``（后续再做）。
  以后换 qwen3:30b / 其他 Ollama 模型 / 云端 API 都不用改剧本解析业务。
- ``LocalQwenBackend``（transformers fp16）保留为备用路径（非 Ollama 环境可用），不是 V1.7 首选。
- ⛔ 显存安全门（V17_PLAN.md §8 / §8.5）：Ollama 模型与 MiniMax H3 不允许同时常驻 GPU。
  **进程级互斥**：``with text_backend.session():``
    进入 → ping Ollama + 检查 ComfyUI GPU model registry 空（H3 未运行）
    退出 → 请求 Ollama 卸载（keep_alive=0）→ ``/api/ps`` 确认 qwen 不再运行
         → 释放互斥锁 → ``torch.cuda.empty_cache()``（清 ComfyUI 侧缓存）
  对 Ollama 不依赖 ``del model → gc → empty_cache``（那是 Transformers 语义），但保留 torch 侧清理。

用法：
    backend = create_default_text_backend()      # OllamaBackend(model="qwen3:14b")
    with backend.session() as llm:
        plan = llm.analyze_json("...")
    # 此处 Ollama 已卸载（/api/ps 为空），H3 可独占 GPU
"""

from __future__ import annotations

import gc
import json
import logging
import os
import re
import threading
import time
import urllib.request

from typing import Any

log = logging.getLogger("ComfyUI-MiniMaxH3-Director.director.text_backends")


# ---------------------------------------------------------------------------
# 强 JSON 输出（与 qwen_vl_feedback.py 相同的容错模式，独立实现避免反向依赖）
# ---------------------------------------------------------------------------
RETRY_HINT = "\n注意：你上一次输出不是合法 JSON。请只输出一个 JSON 对象，不要包含任何其他文字。"


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """从模型原始输出中尽力提取 JSON 对象。

    容忍：markdown 代码块包裹、前后杂文字、尾随逗号。解析失败返回 None。
    """
    if not text:
        return None
    # 先剥掉 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    # 找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    blob = text[start:end + 1]
    # 容错：去掉尾随逗号（如 {"a":1,}）
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        # 二次尝试：去掉行注释（//）与块注释（/* */），模型偶尔会输出注释
        blob = re.sub(r"//[^\n]*", "", blob)
        blob = re.sub(r"/\*.*?\*/", "", blob, flags=re.S)
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------------------
# GPU model registry（ComfyUI）互斥查询
# ---------------------------------------------------------------------------
def gpu_models_loaded() -> int:
    """返回 ComfyUI 当前已加载到 GPU 的模型数量（GPU model registry）。

    H3 等视频生成模型持有 GPU 时该值 > 0；用于「Qwen 分析前」拒绝启动，
    与「Ollama 卸载后」确认 ComfyUI 侧无残留（§8.5）。非 ComfyUI 环境（自测）返回 0。
    """
    try:
        import comfy.model_management as mm
    except Exception:
        return 0
    try:
        models = getattr(mm, "current_loaded_models", None)
        if models is None:  # 旧版 ComfyUI 接口兜底
            fn = getattr(mm, "loaded_models", None)
            models = fn() if callable(fn) else []
        return len(models) if models is not None else 0
    except Exception as exc:  # pragma: no cover - 查询失败保守返回 0
        log.warning("GPU model registry 查询失败，按 0 处理: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# 进程内互斥锁（TextBackend 与 H3 不允许同时持有 GPU 模型）
# ---------------------------------------------------------------------------
_text_lock = threading.Lock()
_text_active = False


def text_backend_active() -> bool:
    """TextBackend 是否正在运行（供 H3 启动前检查，V17_PLAN.md §8.5）。"""
    with _text_lock:
        return _text_active


def _acquire_text_active() -> bool:
    global _text_active
    with _text_lock:
        if _text_active:
            return False
        _text_active = True
        return True


def _release_text_active() -> None:
    global _text_active
    with _text_lock:
        _text_active = False


def _force_empty_cuda_cache() -> None:
    """确认释放 ComfyUI 侧 CUDA 缓存（Ollama 卸载后顺手清理，对 H3 更友好）。"""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # pragma: no cover - 非 CUDA 环境无害跳过
        pass


# ---------------------------------------------------------------------------
# TextBackend 抽象
# ---------------------------------------------------------------------------
class TextBackend:
    """文本后端抽象基类。所有 V1.7 剧本解析逻辑只依赖此接口。"""

    name: str = "base"

    def analyze_text(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        """喂提示词，返回模型原始文本输出。"""
        raise NotImplementedError

    def analyze_json(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        retry: bool = True,
    ) -> dict[str, Any] | None:
        """强 JSON 输出：分析 → 容错解析 → 失败重试一次（追加 RETRY_HINT）。

        返回 dict 或 None（重试仍非 JSON）。解析与重试逻辑对所有后端一致。
        """
        text = self.analyze_text(
            prompt=prompt, max_tokens=max_tokens, temperature=temperature
        )
        obj = _extract_json_block(text)
        if obj is None and retry:
            log.warning(
                "%s 输出非 JSON，重试一次。原文: %.200s", self.name, text
            )
            text2 = self.analyze_text(
                prompt=prompt + RETRY_HINT,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            obj = _extract_json_block(text2)
            if obj is None:
                log.warning(
                    "%s 重试仍非 JSON，返回 None。原文: %.200s", self.name, text2
                )
        return obj

    def preflight(self) -> None:
        """进入 session 前的可选检查（连通性 / 占用告警）。默认无操作。

        具体后端可覆写：Ollama 检查服务可达 + 其他驻留模型告警。
        """
        return None

    def close(self) -> None:
        """释放模型与显存。session 退出时必调，避免与 H3 生成抢显存。"""
        raise NotImplementedError

    def session(self) -> "TextSession":
        """返回显存安全上下文管理器（进入检查 → 分析 → 退出释放）。"""
        return TextSession(self)


# ---------------------------------------------------------------------------
# TextSession：显存生命周期上下文（V17_PLAN.md §8 / §8.5，进程级互斥）
# ---------------------------------------------------------------------------
class TextSession:
    """``with text_backend.session():`` 的显存安全上下文。

    进入：进程内互斥锁（拒绝并发 session）+ ``backend.preflight()``
      （Ollama：ping 服务可达 + ComfyUI GPU model registry 空，H3 占用则拒绝启动）
    退出：``backend.close()``（Ollama：keep_alive=0 卸载 + /api/ps 确认）
      → 释放互斥锁 → ``torch.cuda.empty_cache()``（清 ComfyUI 侧缓存）。
    """

    def __init__(self, backend: TextBackend) -> None:
        self._backend = backend
        self._entered = False

    def __enter__(self) -> TextBackend:
        if not _acquire_text_active():
            raise RuntimeError(
                "TextBackend 已在运行（AI 分析模型正被占用），"
                "请等待本次分析结束后再试。"
            )
        try:
            busy = gpu_models_loaded()
            if busy:
                raise RuntimeError(
                    f"检测到 {busy} 个 GPU 模型已加载（可能是 H3 生成占用），"
                    "AI 分析模型不能与视频生成同时驻留显存。"
                    "请等待生成结束或清理显存后再试。"
                )
            self._backend.preflight()
        except Exception:
            _release_text_active()
            raise
        self._entered = True
        return self._backend

    def __exit__(self, *exc: Any) -> None:
        if not self._entered:
            return
        self._entered = False
        try:
            self._backend.close()
        finally:
            _release_text_active()
            _force_empty_cuda_cache()


# ---------------------------------------------------------------------------
# OllamaBackend：Ollama API（V1.7 首选后端）
# ---------------------------------------------------------------------------
def _default_ollama_base() -> str:
    """Ollama 服务地址：优先 OLLAMA_HOST 环境变量，默认 localhost:11434。"""
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if host:
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        return host.rstrip("/")
    return "http://localhost:11434"


class OllamaBackend(TextBackend):
    """Ollama 本地 API 后端（V1.7 首先实现）。

    - 模型：``qwen3:14b``（Ollama 管理，GGUF Q4，~8.6GB 权重；运行时额外 KV cache/context，
      显存占用高于模型文件，必须走 §8 进程级互斥与 H3 错峰）。
    - 通过 HTTP API 调用（默认 localhost:11434），与 ComfyUI Python 环境完全解耦，
      不需要 llama-cpp-python / transformers，不折腾 GGUF 转格式。
    - ``close()`` 请求 Ollama ``keep_alive=0`` 立即卸载模型 + ``/api/ps`` 确认卸载，
      这才是 Ollama 侧的「显存安全门」。
    """

    name = "ollama"

    def __init__(
        self,
        model: str = "qwen3:14b",
        base_url: str | None = None,
        *,
        keep_alive: str = "5m",
        timeout: float = 600.0,
        unload_wait: float = 15.0,
    ):
        self.model = model
        self.base_url = base_url or _default_ollama_base()
        self.keep_alive = keep_alive
        self.timeout = timeout
        self.unload_wait = unload_wait

    # -- Ollama HTTP 基础 ---------------------------------------------------
    def _api(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        method: str = "POST",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """调 Ollama REST API，返回解析后的 JSON dict。纯标准库（urllib）。"""
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama 服务不可达（{self.base_url}{path}）：{exc}。"
                "请先启动 Ollama（ollama serve）。"
            ) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError:  # pragma: no cover - 容错
            return {"response": body}

    def ping(self, timeout: float = 10.0) -> bool:
        """Ollama 服务是否可达（/api/tags）。"""
        try:
            self._api("/api/tags", method="GET", timeout=timeout)
            return True
        except Exception:
            return False

    def loaded_models(self) -> list[str]:
        """当前 Ollama 驻留（加载到内存/显存）的模型名列表（/api/ps）。"""
        try:
            resp = self._api("/api/ps", method="GET", timeout=15.0)
            return [str(m.get("name", "")) for m in resp.get("models", [])]
        except Exception as exc:  # pragma: no cover
            log.warning("Ollama /api/ps 查询失败: %s", exc)
            return []

    def unload(self) -> None:
        """请求 Ollama 立即卸载本后端模型（keep_alive=0）。"""
        try:
            self._api(
                "/api/generate",
                {"model": self.model, "prompt": "", "stream": False, "keep_alive": 0},
                timeout=30.0,
            )
        except Exception as exc:
            log.warning("Ollama 卸载请求失败（%s）: %s", self.model, exc)

    # -- TextBackend 接口 ---------------------------------------------------
    def analyze_text(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        options: dict[str, Any] = {"num_predict": max_tokens}
        if temperature > 0:
            options["temperature"] = temperature
        else:
            options["temperature"] = 0.0
        resp = self._api(
            "/api/generate",
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": self.keep_alive,
                "options": options,
            },
        )
        return str(resp.get("response", "") or "").strip()

    def preflight(self) -> None:
        """进入 session 前：Ollama 可达 + 其他驻留模型告警。"""
        if not self.ping():
            raise RuntimeError(
                f"Ollama 服务不可达（{self.base_url}）。请先启动 Ollama（ollama serve）"
                f"并确认已拉取模型 {self.model}（ollama pull {self.model}）。"
            )
        loaded = self.loaded_models()
        others = [n for n in loaded if not n.startswith(self.model)]
        if others:
            log.warning(
                "Ollama 已驻留其他模型 %s，可能与 %s 争显存", others, self.model
            )
        if self.model in loaded:
            log.info("Ollama 模型 %s 已在驻留（复用），分析后统一卸载", self.model)

    def close(self) -> None:
        """卸载模型 + 确认（/api/ps），这是 Ollama 侧的显存安全门。

        Ollama 的 keep_alive=0 卸载是**异步**的：请求发出后 scheduler 释放
        runner 需要时间，立即查 /api/ps 会落在卸载完成前 → 误报「仍驻留」。
        因此这里**轮询等待**模型从 /api/ps 消失（默认 15s，0.5s 间隔），
        模型消失即返回；超时仍未消失才告警（显存互斥铁律不放宽为接受驻留）。
        """
        self.unload()
        deadline = time.monotonic() + self.unload_wait
        last_still: list[str] = []
        while True:
            try:
                last_still = [
                    n for n in self.loaded_models() if n.startswith(self.model)
                ]
            except Exception:  # pragma: no cover
                last_still = []
            if not last_still:
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        log.warning(
            "Ollama 模型仍驻留（/api/ps: %s，已等待 %.1fs 未释放），"
            "H3 前请确认显存已释放",
            last_still,
            self.unload_wait,
        )


# ---------------------------------------------------------------------------
# LocalQwenBackend：transformers fp16（备用路径，非 V1.7 首选）
# ---------------------------------------------------------------------------
class LocalQwenBackend(TextBackend):
    """transformers fp16 本地 Qwen 文本模型（备用，非 Ollama 环境可用）。

    V1.7 首选是 OllamaBackend；此实现保留以便未来不依赖 Ollama 时使用。
    fp16 加载 7B 级模型约 9GB 显存，``with session():`` 段间错峰卸载后与 H3 互斥。
    """

    name = "local-qwen"

    def __init__(
        self,
        model_dir: str,
        *,
        device: str = "cuda",
        max_new_tokens: int = 1024,
    ):
        self.model_dir = model_dir
        self.device = device
        self.max_new_tokens = max_new_tokens
        self._model: Any = None
        self._tokenizer: Any = None

    @property
    def model(self) -> Any:
        if self._model is None:
            self._model = self._load()
        return self._model

    def _load(self) -> Any:
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - 依赖缺失的清晰提示
            raise RuntimeError(
                "未安装 transformers。请在 ComfyUI 的 Python 环境执行：\n"
                '  pip install "transformers>=4.53" accelerate'
            ) from exc
        if not os.path.isdir(self.model_dir):
            raise FileNotFoundError(
                f"transformers 模型目录不存在: {self.model_dir}"
            )
        if not os.path.isfile(os.path.join(self.model_dir, "config.json")):
            raise FileNotFoundError(
                f"模型目录缺少 config.json，不是 transformers 格式: {self.model_dir}"
            )
        import torch

        log.info("加载本地 Qwen (transformers fp16): %s", self.model_dir)
        tokenizer = AutoTokenizer.from_pretrained(
            self.model_dir, trust_remote_code=True
        )
        model = AutoModelForCausalLM.from_pretrained(
            self.model_dir,
            torch_dtype=torch.float16,
            device_map="auto",
            trust_remote_code=True,
        )
        self._tokenizer = tokenizer
        self._model = model
        return model

    def analyze_text(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        model = self.model
        tokenizer = self._tokenizer
        import torch

        messages = [{"role": "user", "content": prompt}]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        do_sample = temperature > 0
        gen_kwargs: dict[str, Any] = {"max_new_tokens": max_tokens, "do_sample": do_sample}
        if do_sample:
            gen_kwargs["temperature"] = temperature
        with torch.inference_mode():
            out = model.generate(**inputs, **gen_kwargs)
        out = out[:, inputs["input_ids"].shape[1]:]
        return tokenizer.batch_decode(out, skip_special_tokens=True)[0].strip()

    def close(self) -> None:
        """释放模型与显存（Transformers 语义：del model → gc.collect() → empty_cache）。"""
        self._model = None
        self._tokenizer = None
        gc.collect()
        _force_empty_cuda_cache()


# ---------------------------------------------------------------------------
# 模型目录定位与工厂
# ---------------------------------------------------------------------------
def default_text_dir() -> str:
    """返回 ComfyUI 默认文本模型目录（models/text）。"""
    try:
        import folder_paths

        return os.path.join(folder_paths.models_dir, "text")
    except Exception:  # pragma: no cover - 非 ComfyUI 环境（自测脚本）回退
        return os.environ.get(
            "COMFY_TEXT_DIR", os.path.join(os.getcwd(), "models", "text")
        )


def find_default_text_model_dir() -> str | None:
    """在默认文本模型目录中查找含 config.json 的 Qwen 文本模型目录。"""
    d = default_text_dir()
    if not os.path.isdir(d):
        return None
    for fname in sorted(os.listdir(d)):
        cand = os.path.join(d, fname)
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "config.json")):
            return cand
    return None


def create_default_text_backend() -> TextBackend:
    """便捷工厂：V1.7 首选 Ollama 后端。

    模型名可用 ``TEXT_LLM_MODEL`` 环境变量覆盖（默认 qwen3:14b），
    服务地址走 ``OLLAMA_HOST`` 环境变量（默认 localhost:11434）。
    """
    return OllamaBackend(
        model=os.environ.get("TEXT_LLM_MODEL", "qwen3:14b"),
        base_url=_default_ollama_base(),
    )
