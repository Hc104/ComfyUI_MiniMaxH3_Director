#!/usr/bin/env python3
"""text_backends Phase 0 mock 全链路单元测试（V1.7 · Ollama API 架构）。

覆盖（V17_PLAN.md Phase 0 独立验收）：
1. 强 JSON 输出：_extract_json_block 各种容错格式 + analyze_json 重试链；
2. OllamaBackend：HTTP API 请求 payload、ping、loaded_models、unload(keep_alive=0)、
   close 卸载+确认、analyze 强 JSON、session 全链路（进入 ping → 分析 → 卸载 → 锁释放）；
3. 进程级显存互斥（§8.5）：并发 session 拒绝 / GPU registry 非空（H3 占用）拒绝 /
   Ollama 服务不可达拒绝 / 退出后可重入 + torch.cuda.empty_cache 兜底；
4. LocalQwenBackend（备用路径）：假 transformers 加载、close 释放链、缺 config.json 报错；
5. 模型目录发现 + create_default_text_backend（Ollama 首选 + 环境变量覆盖）。

直接用系统 python 运行（无第三方依赖，全 mock）：
    python3 director/tests/test_text_backends.py
或经 pytest：
    pytest director/tests/test_text_backends.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import urllib.error
import urllib.request

# ---------- 路径与 folder_paths 桩（在导入 text_backends 之前注入）----------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_FOLDER_PATHS = types.ModuleType("folder_paths")
_FOLDER_PATHS.models_dir = ""  # 测试里动态设置
sys.modules.setdefault("folder_paths", _FOLDER_PATHS)

from director.text_backends import (  # noqa: E402
    TextBackend,
    LocalQwenBackend,
    OllamaBackend,
    _default_ollama_base,
    create_default_text_backend,
    default_text_dir,
    find_default_text_model_dir,
    gpu_models_loaded,
    text_backend_active,
    _acquire_text_active,
    _extract_json_block,
    _release_text_active,
)


# ---------- 假运行时（torch / comfy / transformers），保证自测确定性 ----------
_TORCH_STATE = {"available": True, "empty_cache_calls": 0}


class _FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return _TORCH_STATE["available"]

    @staticmethod
    def empty_cache() -> None:
        _TORCH_STATE["empty_cache_calls"] += 1


class _FakeTorch:
    float16 = "float16"
    cuda = _FakeCuda
    inference_mode = staticmethod(lambda: _null_ctx())


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _null_ctx() -> _NullCtx:
    return _NullCtx()


_MM_STATE = {"count": 0}


class _FakeModelManagement(types.ModuleType):
    @property
    def current_loaded_models(self) -> list[object]:
        return [object() for _ in range(_MM_STATE["count"])]

    @staticmethod
    def loaded_models() -> list[object]:
        return [object() for _ in range(_MM_STATE["count"])]


_FAKE_COMFY = types.ModuleType("comfy")
_FAKE_MM = _FakeModelManagement("comfy.model_management")


class _FakeIds:
    shape = (1, 5)


class _FakeInputs(dict):
    def __init__(self):
        super().__init__({"input_ids": _FakeIds()})

    def to(self, device: str) -> "_FakeInputs":
        return self


class _FakeOutput:
    def __getitem__(self, key):
        return None


class _FakeTokenizer:
    _last_text = ""

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True) -> str:
        # 原样回传用户 prompt（含 JSON），让 analyze_json 全链路可测
        if messages and isinstance(messages[0], dict):
            return str(messages[0].get("content", ""))
        return "fake prompt text"

    def __call__(self, text, return_tensors="pt") -> _FakeInputs:
        _FakeTokenizer._last_text = text
        return _FakeInputs()

    def batch_decode(self, out, skip_special_tokens=True) -> list[str]:
        return [_FakeTokenizer._last_text]


class _FakeAutoTokenizer:
    @classmethod
    def from_pretrained(cls, *args, **kwargs) -> _FakeTokenizer:
        return _FakeTokenizer()


class _FakeModel:
    device = "cuda"

    @classmethod
    def from_pretrained(cls, *args, **kwargs) -> "_FakeModel":
        return cls()

    def generate(self, **kwargs) -> _FakeOutput:
        return _FakeOutput()


class _FakeTransformers(types.ModuleType):
    AutoModelForCausalLM = _FakeModel
    AutoTokenizer = _FakeAutoTokenizer


_FAKE_TRANSFORMERS = _FakeTransformers("transformers")

# 强制注入（函数内 `import` 在运行时查 sys.modules，覆盖即可保证确定性）
sys.modules["torch"] = _FakeTorch
sys.modules["comfy"] = _FAKE_COMFY
sys.modules["comfy.model_management"] = _FAKE_MM
sys.modules["transformers"] = _FAKE_TRANSFORMERS


# ---------- 假 Ollama HTTP 服务（monkeypatch urllib.request.urlopen）----------
_OL_STATE = {
    "reachable": True,
    "ps_models": [],        # /api/ps 返回的驻留模型名
    "responses": [],        # /api/generate 的 response 队列（按序弹出）
    "calls": [],            # [(path, payload_or_None), ...]
    "unload_delay": 0,      # keep_alive=0 后还需几次 /api/ps 才真正消失（模拟异步卸载）
    "pending_unload": None, # 正在异步卸载的模型名
    "unload_remaining": 0,  # 剩余还需几次 /api/ps 才消失
}


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _fake_urlopen(req, timeout=None):
    path = req.full_url.split(":11434", 1)[-1].split("?")[0]
    payload = None
    if getattr(req, "data", None):
        payload = json.loads(req.data.decode("utf-8"))
    _OL_STATE["calls"].append((path, payload))
    if not _OL_STATE["reachable"]:
        raise urllib.error.URLError("Connection refused (fake Ollama down)")
    if path == "/api/tags":
        body = {"models": []}
    elif path == "/api/ps":
        if _OL_STATE["pending_unload"]:
            if _OL_STATE["unload_remaining"] <= 0:
                # 延迟结束：模型真正从 /api/ps 消失。
                _OL_STATE["ps_models"] = [
                    n for n in _OL_STATE["ps_models"]
                    if n != _OL_STATE["pending_unload"]
                ]
                _OL_STATE["pending_unload"] = None
            else:
                # 异步卸载未完成：仍驻留，计数递减。
                _OL_STATE["unload_remaining"] -= 1
        body = {"models": [{"name": n} for n in _OL_STATE["ps_models"]]}
    elif path == "/api/generate":
        if payload and payload.get("keep_alive") == 0:
            # 模拟 Ollama 异步卸载：keep_alive=0 后模型经 unload_delay 次
            # /api/ps 才真正消失（scheduler 释放 runner 需要时间）。
            model = payload.get("model")
            if model in _OL_STATE["ps_models"]:
                _OL_STATE["pending_unload"] = model
                _OL_STATE["unload_remaining"] = _OL_STATE["unload_delay"]
            body = {"response": "", "done": True}
        elif _OL_STATE["responses"]:
            body = {"response": _OL_STATE["responses"].pop(0)}
        else:
            body = {"response": "{}"}
    else:
        body = {"ok": True}
    return _FakeResp(json.dumps(body).encode("utf-8"))


_ORIG_URLOPEN = urllib.request.urlopen


def _install_fake_urlopen():
    urllib.request.urlopen = _fake_urlopen
    _OL_STATE["calls"].clear()
    _OL_STATE["unload_delay"] = 0
    _OL_STATE["pending_unload"] = None
    _OL_STATE["unload_remaining"] = 0


def _restore_urlopen():
    urllib.request.urlopen = _ORIG_URLOPEN


# ---------- 假后端 ----------
class FakeTextBackend(TextBackend):
    name = "fake"

    def __init__(self, responses: list[str] | None = None):
        self.calls: list[str] = []
        self.responses: list[str] = list(responses or [])
        self.closed = 0

    def analyze_text(self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.0) -> str:
        self.calls.append(prompt)
        if self.responses:
            return self.responses.pop(0)
        return '{"ok": true}'

    def close(self) -> None:
        self.closed += 1


# ---------- 强 JSON 输出 ----------
def test_extract_json_variants():
    assert _extract_json_block('{"a":1}') == {"a": 1}
    assert _extract_json_block('```json\n{"a":1}\n```') == {"a": 1}
    assert _extract_json_block('prefix {"a":1} suffix') == {"a": 1}
    assert _extract_json_block('{"a":1,}') == {"a": 1}  # 尾逗号
    assert _extract_json_block('{"a":1 // comment\n}') == {"a": 1}  # 行注释
    assert _extract_json_block('{"a":1 /* block */}') == {"a": 1}  # 块注释
    assert _extract_json_block("no json here") is None
    assert _extract_json_block("") is None
    assert _extract_json_block('"a"') is None  # 非对象


def test_analyze_json_success():
    b = FakeTextBackend(responses=['{"ok": true}'])
    assert b.analyze_json("x") == {"ok": True}
    assert len(b.calls) == 1


def test_analyze_json_retry_success():
    b = FakeTextBackend(responses=["not json", '{"ok": true}'])
    assert b.analyze_json("x") == {"ok": True}
    assert len(b.calls) == 2
    assert "不是合法 JSON" in b.calls[1]  # 重试带 RETRY_HINT


def test_analyze_json_retry_fail_returns_none():
    b = FakeTextBackend(responses=["not json", "still not"])
    assert b.analyze_json("x") is None
    assert len(b.calls) == 2


def test_analyze_json_no_retry():
    b = FakeTextBackend(responses=["not json"])
    assert b.analyze_json("x", retry=False) is None
    assert len(b.calls) == 1


# ---------- session 显存生命周期 + 互斥门 ----------
def test_session_lifecycle_release():
    b = FakeTextBackend()
    assert not text_backend_active()
    with b.session() as llm:
        assert isinstance(llm, FakeTextBackend)
        assert text_backend_active()
        assert llm.analyze_json("go") == {"ok": True}
    assert not text_backend_active()
    assert b.closed == 1  # 退出触发 close（卸载）


def test_session_second_enter_rejected():
    b1, b2 = FakeTextBackend(), FakeTextBackend()
    with b1.session():
        try:
            with b2.session():
                raise AssertionError("并发 session 应被互斥门拒绝")
        except RuntimeError:
            pass
        assert text_backend_active()  # 第一把锁仍持有
    assert not text_backend_active()
    assert b2.closed == 0  # 被拒 session 不触发 close


def test_session_rejected_when_gpu_busy():
    # 模拟 H3 正持有 GPU：current_loaded_models = 2
    _MM_STATE["count"] = 2
    b = FakeTextBackend()
    try:
        try:
            with b.session():
                raise AssertionError("GPU registry 非空时应拒绝启动 Qwen")
        except RuntimeError as exc:
            assert "GPU 模型" in str(exc)
        assert not text_backend_active()  # 拒绝后锁已释放
        assert b.closed == 0  # 未进入，不触发 close
    finally:
        _MM_STATE["count"] = 0


def test_session_reenter_after_exit():
    b = FakeTextBackend()
    with b.session():
        pass
    with b.session():  # 退出后可重入
        pass
    assert b.closed == 2


def test_gpu_models_loaded():
    _MM_STATE["count"] = 3
    try:
        assert gpu_models_loaded() == 3
    finally:
        _MM_STATE["count"] = 0
    assert gpu_models_loaded() == 0


# ---------- OllamaBackend：HTTP API ----------
def test_ollama_analyze_text_payload():
    _install_fake_urlopen()
    try:
        b = OllamaBackend(model="qwen3:14b")
        _OL_STATE["responses"] = ["你好，剧本已解析"]
        text = b.analyze_text("解析剧本", max_tokens=2048)
        assert text == "你好，剧本已解析"
        path, payload = _OL_STATE["calls"][-1]
        assert path == "/api/generate"
        assert payload["model"] == "qwen3:14b"
        assert payload["prompt"] == "解析剧本"
        assert payload["stream"] is False
        assert payload["keep_alive"] == "5m"
        assert payload["options"]["num_predict"] == 2048
        assert payload["options"]["temperature"] == 0.0
    finally:
        _restore_urlopen()


def test_ollama_analyze_text_temperature():
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        _OL_STATE["responses"] = ["x"]
        b.analyze_text("x", temperature=0.7)
        path, payload = _OL_STATE["calls"][-1]
        assert payload["options"]["temperature"] == 0.7
    finally:
        _restore_urlopen()


def test_ollama_analyze_json():
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        _OL_STATE["responses"] = ['{"scene": "start", "shots": 3}']
        out = b.analyze_json("x")
        assert out == {"scene": "start", "shots": 3}
    finally:
        _restore_urlopen()


def test_ollama_analyze_json_retry():
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        _OL_STATE["responses"] = ["不是 JSON", '{"ok": 1}']
        out = b.analyze_json("x")
        assert out == {"ok": 1}
        gen_calls = [c for c in _OL_STATE["calls"] if c[0] == "/api/generate"]
        assert len(gen_calls) == 2  # 重试触发第二次调用
    finally:
        _restore_urlopen()


def test_ollama_ping():
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        _OL_STATE["reachable"] = True
        assert b.ping() is True
        _OL_STATE["reachable"] = False
        try:
            assert b.ping() is False
        finally:
            _OL_STATE["reachable"] = True
    finally:
        _restore_urlopen()


def test_ollama_loaded_models():
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        _OL_STATE["ps_models"] = ["qwen3:14b", "shaw/dmeta-embedding-zh:latest"]
        assert b.loaded_models() == ["qwen3:14b", "shaw/dmeta-embedding-zh:latest"]
    finally:
        _restore_urlopen()


def test_ollama_unload_keep_alive_zero():
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        b.unload()
        path, payload = _OL_STATE["calls"][-1]
        assert path == "/api/generate"
        assert payload["model"] == "qwen3:14b"
        assert payload["keep_alive"] == 0
        assert payload["prompt"] == ""
    finally:
        _restore_urlopen()


def test_ollama_close_unload_and_confirm():
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        _OL_STATE["ps_models"] = ["qwen3:14b"]
        b.close()  # unload + /api/ps 确认（不应抛异常）
        paths = [c[0] for c in _OL_STATE["calls"]]
        assert "/api/ps" in paths
        unload_calls = [c for c in _OL_STATE["calls"]
                        if c[0] == "/api/generate" and c[1].get("keep_alive") == 0]
        assert unload_calls, "close 应发出 keep_alive=0 卸载请求"
    finally:
        _restore_urlopen()


def test_ollama_close_polls_until_unloaded():
    """close() 应轮询 /api/ps 等待异步卸载完成（用户实测 ⑬ FAIL 的竞态修复）。

    真实 Ollama 的 keep_alive=0 卸载是异步的：请求发出后 scheduler 释放
    runner 需要时间。本测试用 unload_delay=2 模拟「卸载请求后前两次
    /api/ps 仍驻留、第三次才消失」——close() 必须轮询等待（多次 /api/ps）
    而不是立即告警；且退出时模型已确认消失。
    """
    _install_fake_urlopen()
    try:
        b = OllamaBackend(unload_wait=5.0)
        _OL_STATE["ps_models"] = ["qwen3:14b"]
        _OL_STATE["unload_delay"] = 2
        b.close()  # 不应告警也不应抛异常；应等到模型消失才返回
        paths = [c[0] for c in _OL_STATE["calls"]]
        ps_after_unload = paths.count("/api/ps")
        assert ps_after_unload >= 2, (
            f"close 应轮询 /api/ps 多次等待异步卸载，实际只查了 {ps_after_unload} 次"
        )
        # 卸载后最终 /api/ps 应确认模型消失（close 退出即代表确认）。
        assert "qwen3:14b" not in _OL_STATE["ps_models"]
    finally:
        _restore_urlopen()
        _OL_STATE["unload_delay"] = 0


def test_ollama_session_full_chain():
    # Phase 0 核心验收：进入 ping → 分析（强 JSON）→ 退出卸载 keep_alive=0 → /api/ps 确认
    #                    → 锁释放 → torch.cuda.empty_cache 兜底
    _install_fake_urlopen()
    try:
        b = OllamaBackend()
        _OL_STATE["responses"] = ['{"shots": 3}']
        _OL_STATE["ps_models"] = []  # 进入时无驻留
        _TORCH_STATE["empty_cache_calls"] = 0
        with b.session() as llm:
            assert isinstance(llm, OllamaBackend)
            assert text_backend_active()
            assert llm.analyze_json("x") == {"shots": 3}
            # 进入时 preflight 应 ping 过
            assert any(c[0] == "/api/tags" for c in _OL_STATE["calls"])
        assert not text_backend_active()
        # 退出链路：keep_alive=0 卸载
        unload_calls = [c for c in _OL_STATE["calls"]
                        if c[0] == "/api/generate" and c[1].get("keep_alive") == 0]
        assert unload_calls, "session 退出应请求 Ollama 卸载"
        # /api/ps 确认卸载
        assert any(c[0] == "/api/ps" for c in _OL_STATE["calls"])
        # torch.cuda.empty_cache 兜底
        assert _TORCH_STATE["empty_cache_calls"] >= 1
    finally:
        _restore_urlopen()


def test_ollama_session_rejected_when_gpu_busy():
    _install_fake_urlopen()
    _MM_STATE["count"] = 2
    b = OllamaBackend()
    try:
        try:
            with b.session():
                raise AssertionError("GPU registry 非空应拒绝启动 Ollama 分析")
        except RuntimeError as exc:
            assert "GPU 模型" in str(exc)
        assert not text_backend_active()
        assert b.close  # 未进入，close 不触发（进入失败即释放锁）
    finally:
        _MM_STATE["count"] = 0
        _restore_urlopen()


def test_ollama_session_rejected_when_unreachable():
    _install_fake_urlopen()
    _OL_STATE["reachable"] = False
    b = OllamaBackend()
    try:
        try:
            with b.session():
                raise AssertionError("Ollama 不可达应拒绝进入 session")
        except RuntimeError as exc:
            assert "Ollama" in str(exc)
        assert not text_backend_active()  # preflight 失败后锁已释放
    finally:
        _OL_STATE["reachable"] = True
        _restore_urlopen()


def test_default_ollama_base():
    old = os.environ.pop("OLLAMA_HOST", None)
    try:
        assert _default_ollama_base() == "http://localhost:11434"
        os.environ["OLLAMA_HOST"] = "127.0.0.1:11435"
        assert _default_ollama_base() == "http://127.0.0.1:11435"
        os.environ["OLLAMA_HOST"] = "http://10.0.0.2:11434"
        assert _default_ollama_base() == "http://10.0.0.2:11434"
    finally:
        if old is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = old


# ---------- LocalQwenBackend（备用路径）----------
def _make_model_dir() -> str:
    tmp = tempfile.mkdtemp(prefix="qwen_model_")
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    return tmp


def test_local_qwen_load_analyze_close():
    d = _make_model_dir()
    b = LocalQwenBackend(model_dir=d)
    assert b.analyze_text("hi") == "hi"  # 触发 _load（假 transformers）
    _TORCH_STATE["empty_cache_calls"] = 0
    b.close()
    assert b._model is None  # 模型引用已释放
    assert b._tokenizer is None
    assert _TORCH_STATE["empty_cache_calls"] >= 1  # empty_cache 被调


def test_local_qwen_missing_config():
    tmp = tempfile.mkdtemp()
    b = LocalQwenBackend(model_dir=tmp)
    try:
        b.analyze_text("hi")
        raise AssertionError("缺 config.json 应抛 FileNotFoundError")
    except FileNotFoundError:
        pass


def test_local_qwen_session_full_chain():
    d = _make_model_dir()
    b = LocalQwenBackend(model_dir=d)
    _TORCH_STATE["empty_cache_calls"] = 0
    with b.session() as llm:
        assert isinstance(llm, LocalQwenBackend)
        assert text_backend_active()
        out = llm.analyze_json('{"scene": "start", "shots": 3}')
        assert out == {"scene": "start", "shots": 3}
    assert not text_backend_active()
    assert b._model is None  # 完整卸载
    assert _TORCH_STATE["empty_cache_calls"] >= 1  # torch.cuda.empty_cache()


# ---------- 模型目录发现 + 工厂 ----------
def test_find_default_text_model_dir():
    base = tempfile.mkdtemp()
    _FOLDER_PATHS.models_dir = base
    assert find_default_text_model_dir() is None  # 目录不存在
    sub = os.path.join(base, "text", "Qwen2.5-7B-Instruct")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "config.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    assert find_default_text_model_dir() == sub
    # default_text_dir 指向 models/text
    assert default_text_dir() == os.path.join(base, "text")


def test_create_default_text_backend_ollama():
    # V1.7 首选：OllamaBackend，默认模型 qwen3:14b
    b = create_default_text_backend()
    assert isinstance(b, OllamaBackend)
    assert b.model == "qwen3:14b"
    assert b.base_url == "http://localhost:11434"


def test_create_default_text_backend_env_override():
    old_model = os.environ.get("TEXT_LLM_MODEL")
    old_host = os.environ.get("OLLAMA_HOST")
    try:
        os.environ["TEXT_LLM_MODEL"] = "qwen3:30b"
        os.environ["OLLAMA_HOST"] = "127.0.0.1:11435"
        b = create_default_text_backend()
        assert isinstance(b, OllamaBackend)
        assert b.model == "qwen3:30b"
        assert b.base_url == "http://127.0.0.1:11435"
    finally:
        if old_model is None:
            os.environ.pop("TEXT_LLM_MODEL", None)
        else:
            os.environ["TEXT_LLM_MODEL"] = old_model
        if old_host is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = old_host


def main() -> None:
    _install_fake_urlopen()
    try:
        funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
        passed = 0
        for fn in funcs:
            fn()
            print(f"  ✓ {fn.__name__}")
            passed += 1
        print(f"共 {passed} 项全部通过（text_backends Phase 0 · Ollama 全链路 mock）")
    finally:
        _restore_urlopen()


if __name__ == "__main__":
    main()
