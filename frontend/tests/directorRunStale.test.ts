/**
 * DirectorRunService 跨镜竞态回归（V1.11.4 #506 批量卡死修复）。
 *
 * 背景：批量逐镜生成时共享 DirectorRunService 实例。上一镜的兜底轮询
 * （tickFallbackPolling await /history 挂起中）/ 晚到 WS 事件会在下一镜
 * run() 重置 settled=false 之后继续执行，污染共享 settled 标志，
 * 吞掉当前镜真实 execution_success → onFinish 永不触发 → 批量永久卡死。
 *
 * 覆盖：
 * 1. 上一镜在途兜底 tick 的 stale settle 不吞当前镜收尾（核心修复）。
 * 2. 上一镜晚到的 executed 不得污染当前镜 lastFv（onVideo 按 pid 过滤）。
 * 3. execution_success 先 await 回填再 settle：onFinish 触发时 onVideo 已就绪（不丢归档）。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { DirectorRunService } from "@/services/directorRun";
import { ComfyWsClient } from "@/services/comfyWs";
import type { ComfyApiClient } from "@/services/comfyApi";
import { MiniMaxH3Adapter } from "@/adapters/minimaxH3Adapter";
import type { TimelineStructure } from "@/models/timeline";

const POLL_INTERVAL_MS = 3000;

function makeStructure(): TimelineStructure {
  return {
    scenes: [{ id: "s1", name: "场景1", assets: { cast: [], locations: [], props: [], styles: [] } }],
    shots: [
      {
        id: "sh1",
        sceneId: "s1",
        name: "镜头1",
        order: 0,
        durationSec: 5,
        taskType: "",
        continuityMode: "none",
        content: { visual: "雨夜霓虹街道，女孩撑透明伞缓行", cameraText: "", style: "", soundText: "" },
        refs: [],
        refAudios: [],
        refVideos: [],
        cast: [],
        location: [],
      },
    ],
    totalFrames: 120,
    frameRate: 24,
    width: 864,
    height: 480,
    refMaxSize: 864,
    assets: { cast: [], locations: [] },
    output: {
      mode: "fixed",
      longEdge: 864,
      width: 864,
      height: 480,
      maxExportFrames: 0,
      exportMode: "all",
      continuityEnabled: false,
      continuityOverlapFrames: 9,
      audioMode: "auto",
      qwenVlEnabled: false,
      qwenVlLevel: 1,
    },
  } as unknown as TimelineStructure;
}

function makeWorkflow(): Record<string, unknown> {
  return {
    "5": { class_type: "MiniMaxH3Director", inputs: { timeline_data: "{}", timeline: "{}", width: 864, height: 480 } },
    "9": { class_type: "VAELoader", inputs: {} },
  };
}

/** Stub API：支持可定制 prompt_id + 内存 history/queue。 */
function makeStubApi() {
  let history: Record<
    string,
    {
      status?: { status_str?: string; completed?: boolean; messages?: unknown[] };
      outputs?: Record<string, unknown>;
    }
  > = {};
  let queue: {
    queue_running: Array<[string, number, unknown]>;
    queue_pending: Array<[string, number, unknown]>;
  } = { queue_running: [], queue_pending: [] };
  let nextPromptId = "pid-1";
  const calls = { historyById: 0, queue: 0, submit: 0 };

  const api = {
    historyById: vi.fn(async (pid: string) => {
      calls.historyById += 1;
      return history[pid] ? { [pid]: history[pid] } : {};
    }),
    queue: vi.fn(async () => {
      calls.queue += 1;
      return queue;
    }),
    submitPrompt: vi.fn(async () => {
      calls.submit += 1;
      return { prompt_id: nextPromptId };
    }),
    segmentStatus: vi.fn(async () => ({ cached: [], states: {}, indexShotIds: [] })),
    objectInfo: vi.fn(async () => ({})),
    assertDirectorNode: vi.fn(async () => {}),
    history: vi.fn(async () => ({})),
    interrupt: vi.fn(async () => {}),
    deletePending: vi.fn(async () => {}),
    segmentCacheStatus: vi.fn(async () => ({ cached: [], states: {}, indexShotIds: [] })),
    segmentMp4: vi.fn(async () => ({ blob: new Blob(), filename: "Shot01.mp4" })),
    listProjects: vi.fn(async () => []),
    listSnapshots: vi.fn(async () => []),
  } as unknown as ComfyApiClient;

  return {
    api,
    setHistory: (h: typeof history) => (history = h),
    setQueue: (q: typeof queue) => (queue = q),
    setNextPromptId: (id: string) => (nextPromptId = id),
    calls,
  };
}

/** Stub WS：不真实连接；手动触发 WS 事件。 */
function makeStubWs() {
  const handlers: Record<string, (data: unknown) => void> = {};
  const ws = {
    connect: vi.fn(),
    disconnect: vi.fn(),
    emit: (type: string, data: unknown) => handlers[type]?.(data),
    _setHandlers: (h: Record<string, (data: unknown) => void>) => Object.assign(handlers, h),
  } as unknown as ComfyWsClient;
  return ws;
}

function newRun(api: ComfyApiClient, ws: ComfyWsClient) {
  const svc = new DirectorRunService(new MiniMaxH3Adapter(), api, "ws://stub");
  // 偷换 ws 实例：用 stub 替换内部 ws（constructor 已创建真实 ws 但未 connect）。
  (svc as unknown as { ws: ComfyWsClient }).ws = ws;
  return svc;
}

describe("DirectorRunService 跨镜竞态（V1.11.4 #506）", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("跨镜核心：上一镜在途兜底 tick 的 stale settle 不吞掉当前镜收尾", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const svcAny = svc as unknown as { handleWsEvent(ev: { type: string; data: unknown }): void };
    const finish = vi.fn();

    // 受控 historyById：第一次调用（第一镜在途 tick）挂起，直到显式释放。
    let releaseFirst!: (v: unknown) => void;
    const gate = new Promise((r) => {
      releaseFirst = r;
    });
    let firstCall = true;
    stub.api.historyById = vi.fn(async (pid: string) => {
      if (firstCall) {
        firstCall = false;
        await gate;
      }
      return { [pid]: { status: { status_str: "success", completed: true }, outputs: {} } };
    }) as unknown as typeof stub.api.historyById;

    // 第一镜提交（pid-A）。
    stub.setNextPromptId("pid-A");
    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });

    // 触发第一镜兜底 tick：进入 await historyById(pid-A)，挂起中。
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

    // 第二镜提交（pid-B）：settled=false、pendingPromptId=pid-B。
    stub.setNextPromptId("pid-B");
    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });

    // 释放第一镜在途 tick 的 await → 它查到 pid-A completed → 尝试 settleRun("pid-A")。
    // 守卫必须挡住（pendingPromptId 已是 pid-B），不得触发任何 onFinish、不得把 B 槽位标记 settled。
    releaseFirst({});
    await vi.advanceTimersByTimeAsync(0);

    expect(finish).not.toHaveBeenCalled();

    // 第二镜真实完成：execution_success(B) → onFinish(B, true) 恰好一次。
    svcAny.handleWsEvent({ type: "execution_success", data: { prompt_id: "pid-B" } });
    await vi.advanceTimersByTimeAsync(0);

    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("pid-B", true, expect.anything());
  });

  it("跨镜：上一镜晚到的 executed 不得污染当前镜 lastFv（onVideo 按 pid 过滤）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const svcAny = svc as unknown as { handleWsEvent(ev: { type: string; data: unknown }): void };
    const onVideo = vi.fn();

    stub.setNextPromptId("pid-A");
    await svc.run(makeStructure(), makeWorkflow(), "5", { onVideo });

    stub.setNextPromptId("pid-B");
    await svc.run(makeStructure(), makeWorkflow(), "5", { onVideo });

    // 上一镜（pid-A）的 executed 晚到——不得触发当前镜 onVideo。
    svcAny.handleWsEvent({
      type: "executed",
      data: {
        prompt_id: "pid-A",
        output: { images: [{ filename: "old.mp4", subfolder: "video", type: "output" }] },
      },
    });
    expect(onVideo).not.toHaveBeenCalled();

    // 当前镜（pid-B）的 executed → onVideo 触发。
    svcAny.handleWsEvent({
      type: "executed",
      data: {
        prompt_id: "pid-B",
        output: { images: [{ filename: "new.mp4", subfolder: "video", type: "output" }] },
      },
    });
    expect(onVideo).toHaveBeenCalledTimes(1);
    expect(onVideo.mock.calls[0][0].ref.filename).toBe("new.mp4");
  });

  it("execution_success 先 await 回填再 settle：onFinish 触发前 onVideo 已就绪（不丢归档）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const svcAny = svc as unknown as { handleWsEvent(ev: { type: string; data: unknown }): void };
    const onVideo = vi.fn();
    const finish = vi.fn();

    // history 里 pid-A 已 completed 且带成片输出（模拟 executed 事件丢失，但 /history 兜底可取到）。
    stub.setHistory({
      "pid-A": {
        status: { status_str: "success", completed: true },
        outputs: { "5": { images: [{ filename: "a.mp4", subfolder: "video", type: "output" }] } },
      },
    });
    stub.setNextPromptId("pid-A");
    await svc.run(makeStructure(), makeWorkflow(), "5", { onVideo, onFinish: finish });

    // 只有 execution_success 到达（executed 事件丢失）。
    svcAny.handleWsEvent({ type: "execution_success", data: { prompt_id: "pid-A" } });
    await vi.advanceTimersByTimeAsync(0);

    // 修复前：settle 先于 backfill → onFinish 触发时 lastFv 仍为 null（丢归档）。
    // 修复后：先 await backfill 再 settle → onFinish 触发时 onVideo 已就绪。
    expect(onVideo).toHaveBeenCalledTimes(1);
    expect(onVideo.mock.calls[0][0].ref.filename).toBe("a.mp4");
    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("pid-A", true, expect.anything());

    // 调用顺序：onVideo 先于 onFinish。
    expect(onVideo.mock.invocationCallOrder[0]).toBeLessThan(finish.mock.invocationCallOrder[0]);
  });
});
