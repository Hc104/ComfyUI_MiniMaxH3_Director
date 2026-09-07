/**
 * 输出分辨率配置测试：比例×MP 组合表、反查、等比缩放。
 */
import { describe, it, expect } from "vitest";
import {
  RATIOS,
  MP_TIERS,
  RESOLUTION_MATRIX,
  DEFAULT_FPS,
  resolutionFor,
  resolutionForMp,
  findComboForSize,
  scaleToFit,
} from "@/workbench/resolutionPresets";

describe("分辨率配置：比例 × 百万像素", () => {
  it("比例列表含自定义，且非自定义都有声明比例", () => {
    expect(RATIOS.some((r) => r.custom)).toBe(true);
    for (const r of RATIOS.filter((x) => !x.custom)) {
      expect(r.orientation.length).toBeGreaterThan(0);
    }
  });

  it("MP 档位单调递增", () => {
    for (let i = 1; i < MP_TIERS.length; i++) {
      expect(MP_TIERS[i].mp).toBeGreaterThan(MP_TIERS[i - 1].mp);
    }
  });

  it("默认帧率 24", () => {
    expect(DEFAULT_FPS).toBe(24);
  });

  it("矩阵每格都是 32 倍数且长边 ≤ 2048", () => {
    for (const ratioId of Object.keys(RESOLUTION_MATRIX)) {
      for (const mp of Object.keys(RESOLUTION_MATRIX[ratioId])) {
        const [w, h] = RESOLUTION_MATRIX[ratioId][Number(mp)];
        // H3 硬性要求：宽高为 32 倍数（latent/16 偶数，patch_size(1,2,2)）。
        expect(w % 32).toBe(0);
        expect(h % 32).toBe(0);
        expect(Math.max(w, h)).toBeLessThanOrEqual(2048);
      }
    }
  });

  it("16:9 高档 = 1080P 32 对齐（1920×1088），9:16 对称", () => {
    expect(resolutionFor("16-9", 2.1)).toEqual({ width: 1920, height: 1088 });
    expect(resolutionFor("9-16", 2.1)).toEqual({ width: 1088, height: 1920 });
  });

  it("自定义比例无档位分辨率", () => {
    expect(resolutionFor("custom", 0.9)).toBeNull();
  });

  it("任意 MP 数值走公式：16:9 @ 2.5MP → 近似 2048×1152，32 对齐且长边 ≤ 2048", () => {
    const dim = resolutionForMp("16-9", 2.5);
    expect(dim).not.toBeNull();
    expect(dim!.width % 32).toBe(0);
    expect(dim!.height % 32).toBe(0);
    expect(Math.max(dim!.width, dim!.height)).toBeLessThanOrEqual(2048);
    // 面积接近 2.5MP
    const area = dim!.width * dim!.height;
    expect(area / 1_000_000).toBeGreaterThan(2.2);
    expect(area / 1_000_000).toBeLessThan(2.8);
    // 宽高比接近 16:9
    expect(Math.abs(dim!.width / dim!.height - 16 / 9)).toBeLessThan(0.03);
  });

  it("公式档位与矩阵档位一致性：2.1MP 走公式应≈矩阵 1920×1088 面积", () => {
    const mat = resolutionFor("16-9", 2.1)!;
    const fm = resolutionForMp("16-9", 2.1)!;
    // 矩阵是标准 1920×1088（1080 对齐 32），公式是近似计算（32 对齐），面积应接近
    expect(Math.abs(fm.width * fm.height - mat.width * mat.height) / (mat.width * mat.height)).toBeLessThan(0.1);
  });

  it("未知比例/非法 MP 返回 null", () => {
    expect(resolutionForMp("bogus", 1)).toBeNull();
    expect(resolutionForMp("16-9", 0)).toBeNull();
    expect(resolutionForMp("16-9", -1)).toBeNull();
    expect(resolutionForMp("16-9", Number.NaN)).toBeNull();
  });

  it("findComboForSize 反查命中与未命中", () => {
    expect(findComboForSize(1920, 1088)).toEqual({ ratioId: "16-9", mp: 2.1 });
    expect(findComboForSize(1920, 1080)).toBeNull();
    expect(findComboForSize(864, 480)).toBeNull();
  });

  it("scaleToFit 等比缩放并保持在 clamp 范围 + 32 对齐", () => {
    const s = scaleToFit(3840, 2160);
    expect(Math.abs(s.width / s.height - 16 / 9)).toBeLessThan(0.02);
    expect(s.width).toBeLessThanOrEqual(2048);
    expect(s.width % 32).toBe(0);
    expect(s.height % 32).toBe(0);
    expect(scaleToFit(1280, 720)).toEqual({ width: 1280, height: 736 });
  });
});
