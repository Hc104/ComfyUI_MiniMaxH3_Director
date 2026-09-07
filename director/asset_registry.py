#!/usr/bin/env python3
"""V1.7 Phase 2-1：asset_registry.py（Asset Identity + Persistent Binding Checkpoint，#135）。

Phase 2-2 职责分离（用户 2026-08-11 拍板，commit 1「Stable Entity Key / Alias Binding」）：

    ProductionPlan
        │
        ├── entity_id       ← 本次解析临时身份，可变化（ent_001… 进程内计数器分配）
        │
        └── entity_key      ← 稳定业务身份（{清洗后最终类型}:{canonical_name}）
                 │
                 ▼
           Asset Registry   ← 只认 entity_key，绝不用 entity_id 查询/落盘
                 │
                 ▼
              asset_id      ← 永久绑定 image_file（跨解析不变）

⛔ entity_id 不是跨解析主键：Qwen 每次解析会因 new_entity_id 计数器状态不同
而给同一实体分配不同 ent_xxx（多进程/重启后更是必然不同）。绑定必须跟随
entity_key，与 entity_id 无关。前端绑定发 entity_key；sourceEntityId(entity_id)
仅作 SPA 资产追溯元数据，不参与匹配/复用。

用户 2026-08-11 拍板（Phase 2-1 核心定义）：
> 先建立真正稳定的 Asset ID…… asset_id = asset_001 …… 但必须永久绑定到实际图片资产，
> 不能等于显示名称。
> entity_id 和 asset_id 分开…… Entity(entity_id/name/type) / Asset(asset_id/name/path) /
> Binding(entity_key → asset_id)。
> 人工确认必须真正落盘…… 下一次重新导入：Qwen → ProductionPlan → Entity Cleanse →
> entity_key = location:山雨楼 → Asset Registry 查询 → 发现 user accepted → 直接绑定 asset_001。
> 但不要让 Registry 反向污染实体发现（写成测试）。

本模块只做三件事（⛔ 纯规则零显存零 Ollama，不加载任何模型）：

1. **稳定 entity_key**：`{最终类型}:{canonical_name}`（如 `character:柳如烟` / `location:山雨楼外`）。
   - 类型必须是清洗后最终类型（build_entity_registry 里 _rebuild_gates 之后），保证 key 稳定；
   - 名称用 canonical 归一（去空白 + 小写），跨解析不变；
   - `entity_key()` 与 `entity_key_from_entry()` 两个入口（直接传 etype/name 或注册表条目）。

2. **永久 asset_id**：`asset_{N:03d}` 顺序分配，但**永远绑定 image_file**（图片资产物理身份）。
   - 同一 image_file → 同一 asset_id（跨解析稳定）；
   - 资产改名/类型变化不改变 asset_id（asset_id ≠ 显示名）；
   - 资产库新增文件 → 追加分配；文件删除 → 该 asset_id 保留（binding 仍指向它，但
     当前库查不到时 match_plan 回退到规则匹配，不悬空报错）。

3. **持久化 Binding**：`entity_key → {asset_id, status, source, updated_at}` 落盘
   `minimax_studio/asset_registry.json`（资产库同级，扫描 assets 只认图片扩展名不会读到它）。
   - `status`: "accepted"（用户确认）/ "auto"（系统自动，可选记录）；
   - `source`: "user"（人工确认）/ "system"（自动匹配落盘）；
   - match_plan 消费 binding：`accepted` → **覆盖**规则匹配直接绑定该资产（match_kind="persisted"）；
   - 重新导入同一剧本时 entity_key 相同 → 直接复用上次确认，不再重新 pending。

⛔ 铁律（用户拍板，见 #135 与 V17_PLAN §5）：**Registry 绝不反向生成实体**。
本模块只有 `get/set/ensure` 能力，没有任何从资产库推导「角色/地点/道具」的逻辑；
实体发现只能来自 `剧本 → ProductionPlan → Entity Cleanse`（script_parser/script_analyzer/
entity_cleanse），本模块只是「记住用户确认过的 entity_key→asset_id」，见 test_asset_registry.py
的「不反向污染」专项测试。

存储格式（asset_registry.json）：
    {
      "version": 1,
      "assets": {
        "asset_001": {"image_file": "minimax_studio/assets/柳如烟.png",
                      "name": "柳如烟", "kind": "cast", "created_at": "..."}
      },
      "bindings": {
        "character:柳如烟": {"asset_id": "asset_001", "status": "accepted",
                             "source": "user", "updated_at": "..."}
      }
    }
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 稳定 entity_key：{类型}:{canonical_name}（清洗后最终类型 + 归一名称）
# ---------------------------------------------------------------------------

_CANON_RE = re.compile(r"\s+")


def canonical_name(name: str) -> str:
    """实体显示名 → canonical（去全部空白 + 小写；与 entity_cleanse.normalize_entity_name 一致）。"""
    return _CANON_RE.sub("", (name or "").strip().lower())


def entity_key(etype: str, name: str) -> str:
    """稳定实体键：`{类型}:{canonical_name}`。etype 为空/unknown 时用 `unknown`。

    ⛔ 类型必须传「清洗后最终类型」（character/location/prop 等），不是 Qwen 原始 type。
    例如「山雨楼」经 P0-2 降级为 environment → key=environment:山雨楼（不进匹配，
    也不与 location:山雨楼外 混淆）。「山雨楼外」是 Scene 正式地点 → key=location:山雨楼外。
    """
    t = (etype or "").strip().lower() or "unknown"
    return f"{t}:{canonical_name(name)}"


def entity_key_from_entry(entry: Dict[str, Any]) -> str:
    """注册表条目（entity_cleanse.build_entity_registry 的 entries 元素）→ entity_key。"""
    return entity_key(str(entry.get("type", "")), str(entry.get("name", "")))


# ---------------------------------------------------------------------------
# AssetRegistry：资产 id 分配 + 持久化 binding（纯文件 IO，零模型）
# ---------------------------------------------------------------------------

REGISTRY_VERSION = 1
REGISTRY_FILENAME = "asset_registry.json"


class AssetRegistry:
    """资产身份 + 人工确认持久化注册表。

    root_dir：minimax_studio 目录（registry JSON 落在 root_dir/asset_registry.json）。
    用法：
        reg = AssetRegistry(root_dir)
        reg.load()
        aid = reg.ensure_asset_id("minimax_studio/assets/柳如烟.png", "柳如烟", "cast")
        reg.set_binding("character:柳如烟", aid, status="accepted", source="user")
        reg.save()
    """

    def __init__(self, root_dir: Optional[str] = None):
        self.root_dir = root_dir or _default_root_dir()
        self.path = os.path.join(self.root_dir, REGISTRY_FILENAME)
        self._assets: Dict[str, Dict[str, Any]] = {}
        self._by_image: Dict[str, str] = {}  # image_file → asset_id
        self._bindings: Dict[str, Dict[str, Any]] = {}
        self._next_seq = 1
        self.dirty = False

    # ---- IO ----

    def load(self) -> "AssetRegistry":
        """读 registry JSON；不存在/损坏 → 空注册表（不崩溃）。损坏时备份 .bak。"""
        self._assets = {}
        self._by_image = {}
        self._bindings = {}
        self._next_seq = 1
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for aid, a in (data.get("assets") or {}).items():
                    if not isinstance(a, dict) or not a.get("image_file"):
                        continue
                    self._assets[aid] = _norm_asset_record(aid, a)
                    self._by_image[a["image_file"]] = aid
                for ekey, b in (data.get("bindings") or {}).items():
                    if isinstance(b, dict) and b.get("asset_id"):
                        self._bindings[str(ekey)] = dict(b)
                self._next_seq = self._max_asset_seq() + 1
            except Exception:
                # 损坏 → 备份后重建（避免下次 save 覆盖用户数据）
                try:
                    os.replace(self.path, f"{self.path}.bak")
                except OSError:
                    pass
        return self

    def save(self) -> bool:
        """写回 registry JSON（ensure_ascii=False 保留中文）；失败返回 False 不抛。"""
        if not self.dirty:
            return True
        try:
            os.makedirs(self.root_dir, exist_ok=True)
            payload = {
                "version": REGISTRY_VERSION,
                "assets": self._assets,
                "bindings": self._bindings,
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self.dirty = False
            return True
        except Exception:
            return False

    # ---- asset_id（永久绑定 image_file）----

    def ensure_asset_id(
        self, image_file: str, name: str = "", kind: str = "unknown"
    ) -> str:
        """给图片资产分配/复用 asset_id。

        - 同一 image_file → 返回同一 asset_id（永久稳定）；
        - 新文件 → 分配下一个 asset_{N:03d} 并记录（dirty）；
        - name/kind 仅作为展示快照更新（资产改名不改变 asset_id）。
        """
        if not image_file:
            return ""
        exist = self._by_image.get(image_file)
        if exist and exist in self._assets:
            rec = self._assets[exist]
            # 展示名/类型变化只更新快照，不动 asset_id
            changed = False
            if name and rec.get("name") != name:
                rec["name"] = name
                changed = True
            if kind and rec.get("kind") != kind:
                rec["kind"] = kind
                changed = True
            if changed:
                self.dirty = True
            return exist
        aid = f"asset_{self._next_seq:03d}"
        self._next_seq += 1
        self._assets[aid] = {
            "image_file": image_file,
            "name": name,
            "kind": kind,
            "created_at": _now(),
        }
        self._by_image[image_file] = aid
        self.dirty = True
        return aid

    def asset_by_id(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """asset_id → 资产记录；不存在 → None。"""
        if not asset_id:
            return None
        return self._assets.get(asset_id)

    def asset_by_image(self, image_file: str) -> Optional[Dict[str, Any]]:
        """image_file → 资产记录；不存在 → None。"""
        aid = self._by_image.get(image_file)
        if not aid:
            return None
        return self._assets.get(aid)

    def list_assets(self) -> List[Dict[str, Any]]:
        """全部资产记录（asset_id 字段内嵌，按 asset_id 排序）。"""
        out = []
        for aid in sorted(self._assets.keys()):
            rec = dict(self._assets[aid])
            rec["asset_id"] = aid
            out.append(rec)
        return out

    # ---- bindings（entity_key → asset_id 持久化确认）----

    def get_binding(self, ekey: str) -> Optional[Dict[str, Any]]:
        """entity_key → binding 记录；无 → None。"""
        if not ekey:
            return None
        return self._bindings.get(ekey)

    def set_binding(
        self,
        ekey: str,
        asset_id: str,
        *,
        status: str = "accepted",
        source: str = "user",
    ) -> Optional[Dict[str, Any]]:
        """写入/覆盖 entity_key → asset_id 绑定（dirty 置位，须调 save 落盘）。

        - ekey 非空、asset_id 非空才写（资产必须已 ensure_asset_id）；
        - status/source 记录确认来源（accepted=用户确认 / auto=系统自动）。
        返回写入后的 binding 记录（含 asset 快照 image_file/name），失败 None。
        """
        if not ekey or not asset_id:
            return None
        rec = self._assets.get(asset_id)
        if rec is None:
            return None
        binding = {
            "asset_id": asset_id,
            "status": status or "accepted",
            "source": source or "user",
            "updated_at": _now(),
        }
        self._bindings[ekey] = binding
        self.dirty = True
        return self.binding_with_asset(ekey, binding)

    def list_bindings(self) -> Dict[str, Dict[str, Any]]:
        """全部 bindings：entity_key → {asset_id, status, source, updated_at,
        image_file, asset_name}（附资产快照，供前端重新导入直接复用）。"""
        out: Dict[str, Dict[str, Any]] = {}
        for ekey, b in self._bindings.items():
            enriched = self.binding_with_asset(ekey, b)
            if enriched is not None:
                out[ekey] = enriched
        return out

    def binding_with_asset(
        self, ekey: str, binding: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """binding 记录 + 关联资产快照（image_file/name/kind + entity_key 回显）。"""
        rec = self._assets.get(binding.get("asset_id", ""))
        if rec is None:
            return None
        out = dict(binding)
        out["entity_key"] = ekey
        out["image_file"] = rec.get("image_file", "")
        out["asset_name"] = rec.get("name", "")
        out["asset_kind"] = rec.get("kind", "unknown")
        return out

    # ---- 内部 ----

    def _max_asset_seq(self) -> int:
        m = 0
        for aid in self._assets:
            mm = re.match(r"^asset_(\d+)$", aid)
            if mm:
                m = max(m, int(mm.group(1)))
        return m


def _norm_asset_record(aid: str, a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "image_file": str(a.get("image_file", "")),
        "name": str(a.get("name", "")),
        "kind": str(a.get("kind", "unknown")),
        "created_at": str(a.get("created_at", "")),
    }


def _now() -> str:
    # 精确到秒的 ISO 时间（本地无 tz 偏移，与项目其它落盘一致）
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _default_root_dir() -> str:
    """默认 root = {ComfyUI input}/minimax_studio（延迟导入 folder_paths 防纯单测环境炸）。"""
    try:
        import folder_paths  # noqa: PLC0415

        return os.path.join(folder_paths.get_input_directory(), "minimax_studio")
    except Exception:
        return "minimax_studio"


def default_registry() -> Optional[AssetRegistry]:
    """默认注册表实例（load 已调用）；环境不可用 → None（调用方照旧规则匹配）。"""
    try:
        return AssetRegistry().load()
    except Exception:
        return None


__all__ = [
    "canonical_name",
    "entity_key",
    "entity_key_from_entry",
    "AssetRegistry",
    "default_registry",
    "REGISTRY_VERSION",
    "REGISTRY_FILENAME",
]
