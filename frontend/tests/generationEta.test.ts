// @vitest-environment happy-dom
/**
 * V1.8-3 生成 ETA 纯函数单测：
 *   历史采样读写 / 特征加权预测 / genMeta 构建 / 运行中快照 / 时间格式化。
 */
import { describe, it, expect } from "vitest";
import {
  ETA_HISTORY_CAP,
  ETA_HISTORY_KEY,
  FALLBACK_MS_PER_FRAME,
  appendEtaHistory,
  buildGenMeta,
  computeRunEta,
  currentShotFraction,
  estimateShotMs,
  formatEtaMs,
  framesForDuration,
  loadEtaHistory,
  normalizeTaskKey,
  type GenHistoryRecord,
} from "@/core/generationEta";

/** 构造一条历史采样。 */
function rec(partial: Partial<GenHistoryRecord> & { taskType: string; frames: number; ms: number }): GenHistoryRecord {
  return {
    width: 864,
    height: 480,
    fps: 24,
    finishedAt: 0,
    workflowId: "builtin:h3-default",
    ...partial,
  };
}

describe("V1.8-3 generationEta：历史采样持久化", () => {
  it("loadEtaHistory 还原合法 raw JSON", () => {
    const raw = JSON.stringify([
      rec({ taskType: "r2v", frames: 124, ms: 60000, finishedAt: 1000 }),
      rec({ taskType: "fl2v", frames: 124, ms: 75000, finishedAt: 2000 }),
    ]);
    const list = loadEtaHistory(raw);
    expect(list.length).toBe(2);
    expect(list[0].taskType).toBe("r2v");
    expect(list[0].ms).toBe(60000);
  });

  it("loadEtaHistory 对坏数据返回空数组", () => {
    expect(loadEtaHistory(null)).toEqual([]);
    expect(loadEtaHistory("not-json")).toEqual([]);
    expect(loadEtaHistory(JSON.stringify({ a: 1 }))).toEqual([]);
    expect(loadEtaHistory(JSON.stringify([{ id: 1 }]))).toEqual([]);
    expect(loadEtaHistory(JSON.stringify([{ taskType: "r2v", ms: 1 }]))).toEqual([]); // 缺 frames
  });

  it("appendEtaHistory 新在前且容量封顶", () => {
    const base: GenHistoryRecord[] = [];
    let list = base;
    for (let i = 0; i < ETA_HISTORY_CAP + 5; i++) {
      list = appendEtaHistory(list, rec({ taskType: "r2v", frames: 124, ms: i * 1000, finishedAt: i }));
    }
    expect(list.length).toBe(ETA_HISTORY_CAP);
    expect(list[0].ms).toBe((ETA_HISTORY_CAP + 4) * 1000); // 最新在最前
    expect(list[list.length - 1].ms).toBe(5 * 1000); // 最旧被挤出（丢了 0..4000 五条）
  });

  it("key 约定稳定（跨会话持久化依赖）", () => {
    expect(ETA_HISTORY_KEY).toMatch(/^minimax_studio\./);
  });
});

describe("V1.8-3 generationEta：任务类型归一", () => {
  it("空 / auto 归一为 r2v，显式类型保持", () => {
    expect(normalizeTaskKey("")).toBe("r2v");
    expect(normalizeTaskKey("auto")).toBe("r2v");
    expect(normalizeTaskKey(null)).toBe("r2v");
    expect(normalizeTaskKey(undefined)).toBe("r2v");
    expect(normalizeTaskKey("r2v")).toBe("r2v");
    expect(normalizeTaskKey("fl2v")).toBe("fl2v");
    expect(normalizeTaskKey("t2v")).toBe("t2v");
  });
});

describe("V1.8-3 generationEta：帧数推导（17k+5 网格）", () => {
  it("5s@24 → 120 帧 → 对齐 124", () => {
    expect(framesForDuration(5, 24)).toBe(124);
  });
  it("极短镜头不归零（取网格最小值 22）", () => {
    expect(framesForDuration(0.2, 24)).toBe(22);
  });
  it("上限 512", () => {
    expect(framesForDuration(600, 24)).toBeLessThanOrEqual(512);
  });
});

describe("V1.8-3 generationEta：单镜预测", () => {
  const params = { taskType: "r2v", width: 864, height: 480, fps: 24, frames: 124 };

  it("无历史 → 回退启发式（confidence=0）", () => {
    const est = estimateShotMs(params, []);
    expect(est.basis).toBe("fallback");
    expect(est.matchedCount).toBe(0);
    expect(est.confidence).toBe(0);
    expect(est.ms).toBe(FALLBACK_MS_PER_FRAME * 124);
  });

  it("任务类型不匹配 → 不采纳，回退启发式", () => {
    const est = estimateShotMs(params, [rec({ taskType: "fl2v", frames: 124, ms: 60000 })]);
    expect(est.basis).toBe("fallback");
    expect(est.matchedCount).toBe(0);
  });

  it("精确匹配 → 用历史每帧耗时（60000ms/124帧 ≈ 483ms/帧 → 124 帧 ≈ 60000ms）", () => {
    const est = estimateShotMs(params, [rec({ taskType: "r2v", frames: 124, ms: 60000 })]);
    expect(est.basis).toBe("history");
    expect(est.matchedCount).toBe(1);
    expect(est.ms).toBe(60000);
  });

  it("多样本加权：不同帧数同 taskType 归一为每帧耗时后平均", () => {
    const history = [
      rec({ taskType: "r2v", frames: 124, ms: 60000 }), // ~483.9 ms/帧
      rec({ taskType: "r2v", frames: 248, ms: 130000 }), // ~524.2 ms/帧
    ];
    const est = estimateShotMs(params, history);
    expect(est.basis).toBe("history");
    expect(est.matchedCount).toBe(2);
    // 期望 ≈ ((60000/124) + (130000/248)) / 2 × 124
    const expectMs = Math.round(((60000 / 124 + 130000 / 248) / 2) * 124);
    expect(est.ms).toBe(expectMs);
  });

  it("分辨率不匹配 → 每帧耗时按像素比缩放且权重降档", () => {
    const bigger = estimateShotMs(
      { ...params, width: 1728, height: 960 }, // 4 倍像素
      [rec({ taskType: "r2v", frames: 124, ms: 60000 })],
    );
    expect(bigger.basis).toBe("history");
    // 像素比 4 → scale 封顶 2.5：60000ms × 2.5 ≈ 150000ms
    expect(bigger.ms).toBe(Math.round((60000 / 124) * 2.5 * 124));
  });

  it("fps 差 1 → 权重 0.7；差 5 → 权重 0.3", () => {
    const near = estimateShotMs({ ...params, fps: 25 }, [rec({ taskType: "r2v", frames: 124, ms: 60000 })]);
    expect(near.matchedCount).toBe(1);
    const far = estimateShotMs({ ...params, fps: 30 }, [rec({ taskType: "r2v", frames: 124, ms: 60000 })]);
    expect(far.matchedCount).toBe(1);
    // 都命中历史（权重不影响 matchedCount），只影响 confidence 与均值
    expect(near.confidence).toBeGreaterThan(far.confidence);
  });
});

describe("V1.8-3 generationEta：genMeta 构建", () => {
  const history = [rec({ taskType: "r2v", frames: 124, ms: 60000 })];

  it("每镜帧数 + 任务类型归一 + 预测写入", () => {
    const meta = buildGenMeta({
      width: 864,
      height: 480,
      fps: 24,
      workflowId: "builtin:h3-default",
      shots: [
        { id: "s1", durationSec: 5, taskType: "auto" },
        { id: "s2", durationSec: 3, taskType: "fl2v" },
      ],
      history,
    });
    expect(meta.shots.length).toBe(2);
    expect(meta.shots[0]).toMatchObject({ id: "s1", frames: 124, taskType: "r2v" });
    expect(meta.shots[0].predictedMs).toBe(60000);
    expect(meta.shots[0].matchedCount).toBe(1);
    // s2 fl2v 无匹配 → fallback（confidence=0，matchedCount=0）
    expect(meta.shots[1].taskType).toBe("fl2v");
    expect(meta.shots[1].confidence).toBe(0);
    expect(meta.shots[1].matchedCount).toBe(0);
    expect(meta.shots[1].predictedMs).toBe(FALLBACK_MS_PER_FRAME * framesForDuration(3, 24));
  });
});

describe("V1.8-3 generationEta：当前镜进度 + 运行中快照", () => {
  it("currentShotFraction 四阶段 + finish", () => {
    expect(currentShotFraction(null)).toBe(0);
    expect(currentShotFraction({ phase: "prepare" })).toBe(0);
    expect(currentShotFraction({ phase: "prepare", phaseValue: 1, phaseMax: 1 })).toBe(0.25);
    expect(currentShotFraction({ phase: "context_encode", phaseValue: 1, phaseMax: 1 })).toBe(0.5);
    expect(currentShotFraction({ phase: "sample", phaseValue: 1, phaseMax: 1 })).toBe(0.75);
    expect(currentShotFraction({ phase: "decode", phaseValue: 1, phaseMax: 1 })).toBe(1);
    expect(currentShotFraction({ phase: "finish" })).toBe(1);
    expect(currentShotFraction({ phase: "unknown" })).toBe(0);
  });

  it("走查模式：currentIndex 定位当前镜，剩余 = 本镜剩余 + 后续镜预测", () => {
    const meta: ReturnType<typeof buildGenMeta> = {
      width: 864,
      height: 480,
      fps: 24,
      workflowId: "x",
      shots: [
        { id: "s1", frames: 124, taskType: "r2v", predictedMs: 60000, confidence: 1, matchedCount: 1 },
        { id: "s2", frames: 124, taskType: "r2v", predictedMs: 60000, confidence: 1, matchedCount: 1 },
      ],
    };
    const snap = computeRunEta(meta, { phase: "sample", phaseValue: 1, phaseMax: 1 }, 0, 1_000_000, 1_031_000);
    expect(snap.shotIndex).toBe(0);
    expect(snap.shotProgress).toBe(0.75);
    // 本镜剩余 60000×(1-0.75) = 15000 + s2 60000 = 75000
    expect(snap.currentShotRemainingMs).toBe(15000);
    expect(snap.remainingMs).toBe(75000);
    expect(snap.estimatedTotalMs).toBe(120000);
    expect(snap.elapsedMs).toBe(31000);
    expect(snap.confidence).toBe(1);
    expect(snap.matchedTotal).toBe(2);
  });

  it("单次模式：currentIndex=null 用 progress.segment 定位", () => {
    const meta: ReturnType<typeof buildGenMeta> = {
      width: 864,
      height: 480,
      fps: 24,
      workflowId: "x",
      shots: [
        { id: "s1", frames: 124, taskType: "r2v", predictedMs: 60000, confidence: 0, matchedCount: 0 },
        { id: "s2", frames: 124, taskType: "r2v", predictedMs: 30000, confidence: 0, matchedCount: 0 },
      ],
    };
    const snap = computeRunEta(meta, { segment: 2, phase: "sample", phaseValue: 0, phaseMax: 1 }, null, 0, 90_000);
    expect(snap.shotIndex).toBe(1);
    // s2 在 0.5 进度：剩余 30000×0.5 = 15000，无后续
    expect(snap.currentShotRemainingMs).toBe(15000);
    expect(snap.remainingMs).toBe(15000);
  });

  it("空镜头表 → 全零快照", () => {
    const snap = computeRunEta(
      { width: 864, height: 480, fps: 24, workflowId: "x", shots: [] },
      null,
      null,
      0,
      1000,
    );
    expect(snap.remainingMs).toBe(0);
    expect(snap.estimatedTotalMs).toBe(0);
  });
});

describe("V1.8-3 generationEta：时间格式化", () => {
  it("mm:ss / h:mm:ss / 边界", () => {
    expect(formatEtaMs(0)).toBe("00:00");
    expect(formatEtaMs(83000)).toBe("01:23");
    expect(formatEtaMs(3725000)).toBe("1:02:05");
    expect(formatEtaMs(-1)).toBe("--:--");
    expect(formatEtaMs(Number.NaN)).toBe("--:--");
  });
});
