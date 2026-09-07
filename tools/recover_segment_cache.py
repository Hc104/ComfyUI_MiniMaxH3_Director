#!/usr/bin/env python
"""Recover MiniMax H3 Director segment cache → 可播放的 Shot mp4（可一键合并成整片）。

用途：当「全片导出 / 场景导出」在最后的场景合并阶段因内存不足失败时，之前 4 小时
逐镜生成的视频其实已缓存在磁盘（``output/minimax_seg_cache/<node_id>/seg_*.pt``），
本工具把它们直接解码/编码成 mp4，立刻找回成果，无需重新生成。

缓存位置自动探测：ComfyUI 共享输出目录（``ComfyUI-Shared\\output``）、安装目录
output、ComfyUI Desktop 用户数据目录（``%APPDATA%``）、用户目录等；也可用
``--cache-dir`` 手动指定。

用法（在 ComfyUI 根目录下，用 ComfyUI 自带的 python 运行）：

    .venv\\Scripts\\python.exe custom_nodes\\ComfyUI_MiniMaxH3_Director\\tools\\recover_segment_cache.py

可选参数：
    --cache-dir <路径>   手动指定缓存目录（默认自动探测）
    --out-dir  <路径>    输出 mp4 的目录（默认输出目录 Recovered_...）
    --no-concat          只导每镜 mp4，不做整片拼接
    --fps <数值>         输出帧率（默认 24.0）
    --node <node_id>     只恢复指定节点
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch

# 让脚本能 import 到 director 包（脚本位于 custom_nodes/<name>/tools/ 下）
_HERE = Path(__file__).resolve().parent           # .../tools
_CUSTOM_NODE_ROOT = _HERE.parent                  # .../ComfyUI_MiniMaxH3_Director
_COMFYUI_ROOT = _CUSTOM_NODE_ROOT.parent.parent    # custom_nodes 的上上级 = ComfyUI 根
for _p in (str(_CUSTOM_NODE_ROOT), str(_COMFYUI_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _candidate_cache_roots() -> list[Path]:
    """按可能性列出所有 minimax_seg_cache 候选位置。"""
    roots: list[Path] = []

    def _add(p: Path | None) -> None:
        if p is not None:
            roots.append(p)

    # 1) 本工具推断的 ComfyUI 根 → 共享输出目录（用户实际输出在
    #    D:\Comfy-Desktop\ComfyUI-Shared\output）
    _add(_COMFYUI_ROOT.parent.parent / "ComfyUI-Shared" / "output" / "minimax_seg_cache")

    # 2) 运行时 folder_paths 的输出目录（import 成功时）
    try:
        import folder_paths
        _add(Path(folder_paths.get_output_directory()) / "minimax_seg_cache")
    except Exception:
        pass

    # 3) 安装目录自身 output（非 Desktop 重定向时）
    _add(_COMFYUI_ROOT / "output" / "minimax_seg_cache")

    # 4) ComfyUI Desktop 用户数据目录（%APPDATA% / %LOCALAPPDATA%）
    for env_key in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env_key)
        if base:
            _add(Path(base) / "ComfyUI" / "output" / "minimax_seg_cache")

    # 5) 用户目录 / 文档下的 ComfyUI
    home = Path.home()
    _add(home / "ComfyUI" / "output" / "minimax_seg_cache")
    _add(home / "Documents" / "ComfyUI" / "output" / "minimax_seg_cache")

    # 去重，保留顺序
    seen: set[str] = set()
    out: list[Path] = []
    for p in roots:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _default_cache_root() -> Path | None:
    """定位 minimax_seg_cache：返回第一个真实存在的候选目录。"""
    for root in _candidate_cache_roots():
        if root.is_dir():
            return root
    return None


def _load_audio(aud_path: Path) -> dict | None:
    try:
        if not aud_path.is_file():
            return None
        data = torch.load(aud_path, map_location="cpu", weights_only=True)
        if isinstance(data, dict) and isinstance(data.get("waveform"), torch.Tensor) \
                and int(data["waveform"].numel()) > 0:
            return {
                "waveform": data["waveform"].float().contiguous(),
                "sample_rate": int(data.get("sample_rate") or 44100) or 44100,
            }
    except Exception as exc:
        print(f"    [warn] 音频缓存读取失败，本镜为无声: {exc}")
    return None


def _recover_node(node_dir: Path, out_dir: Path, *, fps: float, concat: bool) -> None:
    from director.stream_export import merge_segment_mp4s, save_segment_mp4

    seg_files = sorted(
        (p for p in node_dir.glob("seg_*.pt")),
        key=lambda p: int(p.stem.split("_")[1]),
    )
    if not seg_files:
        print(f"  {node_dir.name}: 没有可恢复的 seg_*.pt，跳过")
        return

    node_id = node_dir.name
    out_node = out_dir / f"Recovered_{node_id}"
    out_node.mkdir(parents=True, exist_ok=True)

    shot_paths: list[str] = []
    for i, pt in enumerate(seg_files):
        idx = int(pt.stem.split("_")[1])
        aud_path = node_dir / f"seg_{idx:04d}.aud.pt"
        try:
            tensor = torch.load(pt, map_location="cpu", weights_only=True)
        except Exception as exc:
            print(f"  [error] 镜 {idx + 1} 读取失败，跳过: {exc}")
            continue
        if not isinstance(tensor, torch.Tensor) or tensor.ndim != 4:
            print(f"  [error] 镜 {idx + 1} 不是有效帧张量，跳过")
            continue
        audio = _load_audio(aud_path)
        out_mp4 = out_node / f"Shot{idx + 1:02d}.mp4"
        save_segment_mp4(str(out_mp4), tensor, audio, fps=fps)
        shot_paths.append(str(out_mp4))
        print(f"  Shot{idx + 1:02d} <- {pt.name} ({tensor.shape[0]} frames"
              f"{' + 音频' if audio else '（无音频缓存）'})")

    if not shot_paths:
        print(f"  {node_id}: 没有任何可恢复镜头")
        return

    print(f"  完成 {len(shot_paths)} 个单镜 → {out_node}")

    if concat and len(shot_paths) >= 1:
        try:
            from director.stream_export import ffmpeg_bin
            if ffmpeg_bin() is None:
                print("  [warn] 找不到 ffmpeg，跳过整片拼接（单镜 mp4 已可用）")
                return
            movie = out_dir / f"Recovered_{node_id}.mp4"
            merge_segment_mp4s(shot_paths, str(movie))
            print(f"  整片拼接 → {movie}")
        except Exception as exc:
            print(f"  [error] 整片拼接失败（单镜 mp4 仍可用）: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Recover Director segment cache to mp4")
    ap.add_argument("--cache-dir", default=None, help="缓存目录（默认自动定位）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认 ComfyUI 输出目录）")
    ap.add_argument("--no-concat", action="store_true", help="不拼接整片")
    ap.add_argument("--fps", type=float, default=24.0, help="输出帧率")
    ap.add_argument("--node", default=None, help="只恢复指定 node_id")
    args = ap.parse_args()

    cache_root = Path(args.cache_dir) if args.cache_dir else _default_cache_root()
    if cache_root is None or not cache_root.is_dir():
        print(f"[error] 未找到缓存目录: {cache_root}")
        print("已搜索以下候选位置（均无 minimax_seg_cache）：")
        for i, cand in enumerate(_candidate_cache_roots(), 1):
            mark = "  ✓" if cand.is_dir() else ""
            print(f"  {i}. {cand}{mark}")
        print("请确认：1) 你曾用本节点生成过镜头（缓存写入成功）；2) 输出目录实际位置。")
        print("可用 --cache-dir 手动指定，例如：")
        print('  --cache-dir "D:\\Comfy-Desktop\\ComfyUI-Shared\\output\\minimax_seg_cache"')
        return 1

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        try:
            import folder_paths
            out_dir = Path(folder_paths.get_output_directory())
        except Exception:
            out_dir = _COMFYUI_ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"缓存目录: {cache_root}")
    print(f"输出目录: {out_dir}")

    node_dirs = sorted(
        (p for p in cache_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    if args.node:
        node_dirs = [cache_root / args.node]
    if not node_dirs:
        print("缓存目录为空。若你有旧版本生成的缓存，可能它们写入了其它位置。")
        return 0

    for nd in node_dirs:
        print(f"== 节点 {nd.name} ==")
        _recover_node(nd, out_dir, fps=args.fps, concat=not args.no_concat)

    print("\n全部完成。单镜文件在 Recovered_<节点>_<ShotNN>.mp4，整片在 Recovered_<节点>.mp4。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
