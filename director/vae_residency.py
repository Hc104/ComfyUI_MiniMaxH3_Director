"""#484 修复：aimdo 开启时把视频/音频 VAE 解码切到非动态全量驻留。

根因
----
ComfyUI 启动时（main.py）一旦检测到 comfy-aimdo，会执行::

    comfy.model_patcher.CoreModelPatcher = comfy.model_patcher.ModelPatcherDynamic

因此服务端**所有**通过 ``comfy.sd.VAE()`` 构造的 VAE patcher 都是
``ModelPatcherDynamic``（动态）。动态 patcher 把权重 staged 在 host buffer，
解码时每个算子按需换页：

* MiniMax H3 视频 VAE 是 36 层 ViT3D 解码器；
* 解码走时间分块（7 个 chunk）+ 空间 tiling（~48 tile）；
* 每块每 tile 都要经过 36 层 transformer → 数千次权重换页 → 4-7 分钟「卡解码」。

隔离诊断脚本 ``tools/diag_decode_hang.py`` **不跑 main.py**，所以
``CoreModelPatcher`` 还是基类 ``ModelPatcher``，视频 VAE 非动态 → 完全驻留下
解码仅 14s / 峰值 5.3GB。这与 SPA 的「一直在解码」完全吻合：问题在 SPA 侧
（aimdo 动态驻留），不在解码计算本身。

修复
----
解码前把 VAE patcher 换成**非动态代理**，并设 ``vae.disable_offload = True``
让 ``VAE.decode`` 走 ``force_full_load=True`` 全量驻留路径：

1. 优先用 ComfyUI 官方 ``ModelPatcherDynamic.get_non_dynamic_delegate()``
   （VAELoader 会给 patcher 注册 ``cached_patcher_init`` 工厂，从 vae 文件重建
   一个 ops 干净的非动态模型）；
2. 缺工厂时回退：用 ``vae.get_sd()`` 重建 VAE，再强制换成基类
   ``comfy.model_patcher.ModelPatcher``（绕开 main.py 对 CoreModelPatcher 的
   aimdo 替换）。

按 vae 文件路径做模块级缓存，避免每次解码都重读 5GB checkpoint。
换后解码走普通全量加载：权重一次进 VRAM，之后由 ComfyUI 内存管理器正常卸载。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 非动态 VAE patcher 缓存：键 = vae 文件路径。首个非动态代理会重建一次模型，
# 之后同文件复用，避免每次生成都从磁盘重读 5GB。
_ND_DELEGATE_CACHE: dict[str, Any] = {}


def _vae_disk_key(vae: Any) -> str | None:
    """从 ``VAE.patcher.cached_patcher_init`` 提取 vae 文件路径，用作缓存键。"""
    patcher = getattr(vae, "patcher", None)
    ci = getattr(patcher, "cached_patcher_init", None)
    if not ci:
        return None
    try:
        args = ci[1]
        path = args[0] if args else None
        return path if isinstance(path, str) else None
    except Exception:  # noqa: BLE001
        return None


def _build_non_dynamic_patcher(vae: Any) -> Any:
    """从动态 patcher 生成一个非动态代理 patcher（权重干净、ops 非 aimdo）。

    返回的 patcher 包装一个**全新**模型实例（从磁盘或 state_dict 重建），
    避免与动态 patcher 共享模型后仍被 aimdo 的 op 挂钩污染。
    """
    patcher = vae.patcher

    # 主路径：ComfyUI 官方非动态代理（需要 cached_patcher_init 工厂）
    try:
        nd = patcher.get_non_dynamic_delegate()
        if nd is not None and not nd.is_dynamic():
            return nd
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VAE-RESIDENCY] get_non_dynamic_delegate() 不可用：%r；回退 get_sd() 重建", exc)

    # 回退：vae.get_sd() 重建 VAE，再强制基类 ModelPatcher
    try:
        import comfy.model_patcher
        import comfy.sd

        sd = vae.get_sd()
        fresh = comfy.sd.VAE(sd=sd, metadata=getattr(vae, "metadata", None))
        fresh.throw_exception_if_invalid()
        base = comfy.model_patcher.ModelPatcher(
            fresh.first_stage_model,
            load_device=patcher.load_device,
            offload_device=patcher.offload_device,
            size=patcher.model_size(),
            weight_inplace_update=getattr(patcher, "weight_inplace_update", False),
        )
        # 携带已有的重量级补丁（LoRA 等），避免解码结果与动态 patcher 不一致
        for key, val in (getattr(patcher, "patches", {}) or {}).items():
            base.patches[key] = val[:]
        base.patches_uuid = getattr(patcher, "patches_uuid", None)
        return base
    except Exception as exc:  # noqa: BLE001
        logger.error("[VAE-RESIDENCY] 非动态化失败：%r", exc)
        return None


def ensure_vae_resident(vae: Any) -> bool:
    """确保 VAE 解码走非动态全量驻留路径（#484 修复）。

    幂等：VAE patcher 已是非动态时直接返回 False（什么都不做）。
    返回 True 表示本次把动态 patcher 换成了非动态代理。
    """
    patcher = getattr(vae, "patcher", None)
    if patcher is None or not patcher.is_dynamic():
        return False

    key = _vae_disk_key(vae)
    nd = _ND_DELEGATE_CACHE.get(key) if key else None
    if nd is None:
        nd = _build_non_dynamic_patcher(vae)
        if nd is None:
            return False
        if key:
            _ND_DELEGATE_CACHE[key] = nd

    vae.patcher = nd
    # 关键（#486 实测）：comfy.sd.VAE.decode 的 forward 走 vae.first_stage_model，
    # 而 load_models_gpu 加载的是 vae.patcher.model——两者必须是**同一模型对象**。
    # 只换 patcher 不换 first_stage_model 会导致权重加载到新模型（cuda）、
    # forward 仍用旧模型（cpu 权重）→ `Input type (torch.cuda.FloatTensor) and
    # weight type (torch.FloatTensor) should be the same`（音频 VAE conv1d 实测崩）。
    vae.first_stage_model = nd.model
    # 让 VAE.decode 走 force_full_load=True，权重一次进 VRAM
    vae.disable_offload = True
    model_name = getattr(getattr(vae, "first_stage_model", None), "__class__", type(vae)).__name__
    logger.info(
        "[VAE-RESIDENCY] %s 动态 patcher 已切换为非动态（disable_offload=True, key=%s）",
        model_name,
        key or "<none>",
    )
    return True
