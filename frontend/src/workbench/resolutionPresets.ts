/**
 * 输出分辨率配置（V1 工作台，节点工作流式交互）。
 *
 * 三个联动控件：
 *  1. 比例（含「自定义」）→ RATIOS
 *  2. 百万像素档位 → MP_TIERS
 *  3. 帧率（默认 24）→ DEFAULT_FPS
 *
 * 选「比例 + MP 档位」→ 查 RESOLUTION_MATRIX 得到标准分辨率（32 倍数）；
 * 选「自定义」比例 → 宽高手输，脱离档位联动。
 * 组合是纯 UI 辅助，不落项目模型；生成时最终走 outputSize → buildTimelineStructure → Adapter → timeline_data。
 *
 * ⚠️ H3 硬性要求：生成宽高必须是 **32 的倍数**（latent/16 后为偶数，
 * patch_size=(1,2,2)）。keyframe 首帧会精确按 width×height resize 再编码，
 * 奇数宽会在模型 patchify_video 崩溃。矩阵与公式全部对齐 32，杜绝该问题。
 */

export const DEFAULT_FPS = 24;
/** 帧率范围（H3 常见取值）。 */
export const FPS_MIN = 8;
export const FPS_MAX = 60;
/** 分辨率长边上限（H3 latent 限制）。 */
export const RES_MAX_DIM = 2048;
export const RES_MIN_DIM = 256;
/** MiniMax H3 要求宽高为 32 的倍数（= patch_size(1,2,2) 下 latent/16 偶数）。 */
export const H3_ALIGN = 32;

export interface RatioOption {
  id: string;
  label: string;
  /** 横竖屏描述（仅展示）。 */
  orientation: string;
  /** true = 自定义（手动输入宽高，脱离档位）。 */
  custom?: boolean;
}

export interface MpTier {
  /** 百万像素（近似档位，具体分辨率见 RESOLUTION_MATRIX）。 */
  mp: number;
  label: string;
}

export const RATIOS: RatioOption[] = [
  { id: "16-9", label: "16:9", orientation: "横屏" },
  { id: "9-16", label: "9:16", orientation: "竖屏" },
  { id: "1-1", label: "1:1", orientation: "方形" },
  { id: "4-3", label: "4:3", orientation: "横屏" },
  { id: "3-4", label: "3:4", orientation: "竖屏" },
  { id: "21-9", label: "21:9", orientation: "超宽" },
  { id: "custom", label: "自定义", orientation: "", custom: true },
];

export const MP_TIERS: MpTier[] = [
  { mp: 0.3, label: "0.3MP" },
  { mp: 0.5, label: "0.5MP" },
  { mp: 0.9, label: "0.9MP" },
  { mp: 1.5, label: "1.5MP" },
  { mp: 2.1, label: "2.1MP" },
  { mp: 3.7, label: "3.7MP" },
];

/** 把数值对齐到 H3 要求的 32 倍数（half-up，≥ 256）。与后端 snap_dimension 语义一致。 */
export function alignDim(v: number): number {
  const n = Math.round(v / H3_ALIGN) * H3_ALIGN;
  return Math.max(RES_MIN_DIM, n);
}

/** 比例 × MP 档位 → 标准分辨率（宽×高，均为 32 倍数，长边 ≤ 2048）。 */
export const RESOLUTION_MATRIX: Record<string, Record<number, readonly [number, number]>> = {
  "16-9": {
    0.3: [768, 448],
    0.5: [960, 544],
    0.9: [1280, 736],
    1.5: [1600, 896],
    2.1: [1920, 1088],
    3.7: [2048, 1152],
  },
  "9-16": {
    0.3: [448, 768],
    0.5: [544, 960],
    0.9: [736, 1280],
    1.5: [896, 1600],
    2.1: [1088, 1920],
    3.7: [1152, 2048],
  },
  "1-1": {
    0.3: [576, 576],
    0.5: [736, 736],
    0.9: [960, 960],
    1.5: [1216, 1216],
    2.1: [1440, 1440],
    3.7: [1920, 1920],
  },
  "4-3": {
    0.3: [640, 480],
    0.5: [832, 608],
    0.9: [1120, 832],
    1.5: [1440, 1088],
    2.1: [1664, 1248],
    3.7: [2048, 1536],
  },
  "3-4": {
    0.3: [480, 640],
    0.5: [608, 832],
    0.9: [832, 1120],
    1.5: [1088, 1440],
    2.1: [1248, 1664],
    3.7: [1536, 2048],
  },
  "21-9": {
    0.3: [1024, 448],
    0.5: [1280, 544],
    0.9: [1536, 672],
    1.5: [1792, 768],
    2.1: [2048, 864],
    3.7: [2048, 896],
  },
};

/** 查询组合对应的标准分辨率；自定义/未知组合返回 null。 */
export function resolutionFor(ratioId: string, mp: number): { width: number; height: number } | null {
  const byMp = RESOLUTION_MATRIX[ratioId];
  if (!byMp) return null;
  const dim = byMp[mp];
  if (!dim) return null;
  return { width: dim[0], height: dim[1] };
}

/** 反查：给定宽高，返回命中的比例+档位组合；不在矩阵内返回 null。 */
export function findComboForSize(width: number, height: number): { ratioId: string; mp: number } | null {
  for (const ratioId of Object.keys(RESOLUTION_MATRIX)) {
    const byMp = RESOLUTION_MATRIX[ratioId];
    for (const mp of Object.keys(byMp)) {
      const dim = byMp[Number(mp)];
      if (dim[0] === width && dim[1] === height) {
        return { ratioId, mp: Number(mp) };
      }
    }
  }
  return null;
}

/** 比例 → 宽高轴（用于任意 MP 计算）。 */
const RATIO_AXES: Record<string, [number, number]> = {
  "16-9": [16, 9],
  "9-16": [9, 16],
  "1-1": [1, 1],
  "4-3": [4, 3],
  "3-4": [3, 4],
  "21-9": [21, 9],
};

/**
 * 任意百万像素 → 标准分辨率（矩阵之外的手输数值走这里）。
 * 解 w/h = r 与 w*h = mp*1e6，clamp 长边 ≤ 2048 / 短边 ≥ 256，再对齐 32（half-up）。
 */
export function resolutionForMp(ratioId: string, mp: number): { width: number; height: number } | null {
  const axes = RATIO_AXES[ratioId];
  if (!axes || !Number.isFinite(mp) || mp <= 0) return null;
  const area = mp * 1_000_000;
  const r = axes[0] / axes[1];
  let w = Math.sqrt(area * r);
  let h = Math.sqrt(area / r);
  const longEdge = Math.max(w, h);
  if (longEdge > RES_MAX_DIM) {
    const s = RES_MAX_DIM / longEdge;
    w *= s;
    h *= s;
  }
  const shortEdge = Math.min(w, h);
  if (shortEdge < RES_MIN_DIM) {
    const s = RES_MIN_DIM / shortEdge;
    w *= s;
    h *= s;
  }
  return { width: alignDim(w), height: alignDim(h) };
}

/** 等比缩放到 clamp 范围（长边 ≤ 2048，短边 ≥ 256），不改宽高比，再对齐 32。 */
export function scaleToFit(width: number, height: number): { width: number; height: number } {
  const longEdge = Math.max(width, height);
  const scale = Math.min(RES_MAX_DIM / longEdge, 1);
  let w = Math.round(width * scale);
  let h = Math.round(height * scale);
  const shortEdge = Math.min(w, h);
  if (shortEdge < RES_MIN_DIM) {
    const up = RES_MIN_DIM / shortEdge;
    w = Math.round(w * up);
    h = Math.round(h * up);
  }
  return { width: alignDim(w), height: alignDim(h) };
}
