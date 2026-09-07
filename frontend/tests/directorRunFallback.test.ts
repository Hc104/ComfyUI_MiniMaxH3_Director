/**
 * DirectorRunService 兜底轮询单测（V1.3.3-A）。
 *
 * 场景覆盖：
 * 1. WS 正常 execution_success → onFinish 恰好一次（settle 去重）。
 * 2. WS 断开（无事件）→ /history 兜底判定 completed → onFinish(true)。
 * 3. WS 断开 + /history 报 error → onFinish(false)。
 * 4. /queue 判断 running/queued/missing → onPoll 上报。
 *
 * 关键：ws 用 stub（不真实连接），api 用 stub（内存 history/queue）。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { DirectorRunService, type RunCallbacks } from "@/services/directorRun";
import { ComfyWsClient } from "@/services/comfyWs";
import type { ComfyApiClient } from "@/services/comfyApi";
import { MiniMaxH3Adapter } from "@/adapters/minimaxH3Adapter";
import type { TimelineStructure } from "@/models/timeline";

const POLL_INTERVAL_MS = 3000;

/** 最小可跑 buildTimelineData 的 structure（复用 adapter 契约）。 */
function makeStructure(): TimelineStructure {
  // 直接借用 adapter：buildTimelineData 只读 structure.shots/scenes 等。
  // 这里给出最小合法结构，靠 adapter 的容错走完（若抛错，测试会快速失败暴露契约变化）。
  return {
    scenes: [
      { id: "s1", name: "场景1", assets: { cast: [], locations: [], props: [], styles: [] } },
    ],
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

/** Stub API：内存 history + queue，记录调用。 */
function makeStubApi() {
  let history: Record<
    string,
    {
      status?: {
        status_str?: string;
        completed?: boolean;
        messages?: Array<[string, { node_id?: string; exception_message?: string; traceback?: string }]>;
      };
      outputs?: Record<string, unknown>;
    }
  > = {};
  // ComfyUI /queue 元组：第一个元素是 prompt_id（string）。
  let queue: {
    queue_running: Array<[string, number, unknown]>;
    queue_pending: Array<[string, number, unknown]>;
  } = { queue_running: [], queue_pending: [] };
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
      return { prompt_id: "test-prompt-1" };
    }),
    segmentStatus: vi.fn(async () => ({ cached: [], states: {}, indexShotIds: [] })),
    // 内部仅用到上面这些；其余置 no-op。
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

  return { api, setHistory: (h: typeof history) => (history = h), setQueue: (q: typeof queue) => (queue = q), calls };
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

describe("DirectorRunService 兜底轮询", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("WS execution_success → onFinish 恰好一次（settle 去重 + 轮询不再触发）", async () => {
    const { api } = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(api, ws);
    // 捕获 svc 实例用于手动派发 WS。
    const svcAny = svc as unknown as { handleWsEvent(ev: { type: string; data: unknown }): void };

    const finish = vi.fn();
    const cb: RunCallbacks = { onFinish: finish };
    await svc.run(makeStructure(), makeWorkflow(), "5", cb);

    // WS 正常路径：派发 execution_success。
    svcAny.handleWsEvent({ type: "execution_success", data: { prompt_id: "test-prompt-1" } });
    // V1.11.4 #506：execution_success 先 await 回填成片再 settle（异步收尾），flush 微任务后断言。
    await vi.advanceTimersByTimeAsync(0);
    expect(finish).toHaveBeenCalledTimes(1);
    // V1.4-P0：info 现为对象（{ nodeId }，无节点信息时为 undefined 字段）。
    expect(finish).toHaveBeenCalledWith("test-prompt-1", true, { nodeId: undefined });

    // 轮询 tick 多次也不重复触发。
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    expect(finish).toHaveBeenCalledTimes(1);
  });

  it("WS 断开（无事件）→ /history completed 兜底 → onFinish(true)", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();

    stub.setHistory({
      "test-prompt-1": { status: { status_str: "success", completed: true }, outputs: {} },
    });

    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 1.5);

    expect(stub.calls.historyById).toBeGreaterThan(0);
    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("test-prompt-1", true, undefined);
  });

  it("WS 断开 + /history error → onFinish(false)", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();

    stub.setHistory({ "test-prompt-1": { status: { status_str: "error" } } });

    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 1.5);

    expect(finish).toHaveBeenCalledTimes(1);
    // V1.4-P0：history 无 messages → info 为空对象。
    expect(finish).toHaveBeenCalledWith("test-prompt-1", false, {});
  });

  it("/queue 兜底：running / queued / missing 上报 onPoll", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const poll = vi.fn();

    // 第一次 tick：history 无记录 → queue_running 有该 prompt。
    stub.setQueue({
      queue_running: [["test-prompt-1", 0, {}]],
      queue_pending: [],
    });
    await svc.run(makeStructure(), makeWorkflow(), "5", { onPoll: poll });

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 1.1);
    expect(poll).toHaveBeenLastCalledWith({ promptId: "test-prompt-1", status: "running" });

    // 第二次 tick：转 queued。
    stub.setQueue({ queue_running: [], queue_pending: [["test-prompt-1", 0, {}]] });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(poll).toHaveBeenLastCalledWith({ promptId: "test-prompt-1", status: "queued" });

    // 第三次 tick：消失 → missing。
    stub.setQueue({ queue_running: [], queue_pending: [] });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    expect(poll).toHaveBeenLastCalledWith({ promptId: "test-prompt-1", status: "missing" });
  });

  it("settle 后轮询停止（onPoll 不再上报）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();
    const poll = vi.fn();

    stub.setHistory({ "test-prompt-1": { status: { status_str: "success", completed: true } } });
    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish, onPoll: poll });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 1.5);
    expect(finish).toHaveBeenCalledTimes(1);

    const queueCallsBefore = stub.calls.queue;
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    expect(stub.calls.queue).toBe(queueCallsBefore);
    expect(poll).not.toHaveBeenCalled();
  });

  it("interrupt() → deletePending + /interrupt + onFinish(false) 恰好一次（取消链路）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const svcAny = svc as unknown as { handleWsEvent(ev: { type: string; data: unknown }): void };
    const finish = vi.fn();

    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });
    expect(stub.calls.submit).toBe(1);

    await svc.interrupt();

    // ① 排队项被移除（若还在 pending）；② /interrupt 发出；③ 主动收尾。
    expect(stub.api.deletePending).toHaveBeenCalledWith("test-prompt-1");
    expect(stub.api.interrupt).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("test-prompt-1", false, undefined);

    // ④ 随后的 WS execution_error 被 settled 挡住，不重复触发。
    svcAny.handleWsEvent({ type: "execution_error", data: { prompt_id: "test-prompt-1" } });
    expect(finish).toHaveBeenCalledTimes(1);

    // ⑤ 轮询已停止，不再有新 tick。
    const queueCallsBefore = stub.calls.queue;
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    expect(stub.calls.queue).toBe(queueCallsBefore);
  });

  it("成片长任务超过轮询上限但仍在 /queue → 不误判失败，history completed 后成功收尾（#105）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();

    // 任务全程在跑：history 无记录，/queue 一直 running（模拟 11 镜 exportMode=all 长任务）。
    stub.setQueue({ queue_running: [["test-prompt-1", 0, {}]], queue_pending: [] });
    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });

    // 推进远超 MAX_POLL_TICKS（2400×3s≈2h）——fake timers 快速跳过。
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2500);

    // 任务仍在 /queue running → 不应触发 onFinish(false)（#105 修复点）。
    expect(finish).not.toHaveBeenCalled();

    // 任务真正完成：/queue 清空 + /history completed。
    stub.setQueue({ queue_running: [], queue_pending: [] });
    stub.setHistory({
      "test-prompt-1": { status: { status_str: "success", completed: true }, outputs: {} },
    });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 1.5);

    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("test-prompt-1", true, undefined);
  });

  it("任务彻底失联（不在 /history 也不在 /queue）且超上限 → 按失败收尾（防永久 running）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();

    // history 无记录、/queue 空：任务彻底消失。
    stub.setQueue({ queue_running: [], queue_pending: [] });
    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2500);

    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("test-prompt-1", false, undefined);
  });

  it("超上限时 /history error → 按失败收尾（错误信息完整传递）", async () => {
    const stub = makeStubApi();
    const ws = makeStubWs();
    const svc = newRun(stub.api, ws);
    const finish = vi.fn();

    // history 记录 error（带 messages），/queue 已清空——超时后走 error 分支。
    stub.setHistory({
      "test-prompt-1": {
        status: {
          status_str: "error",
          messages: [["execution_error", { node_id: "5", exception_message: "boom", traceback: "tb" }]],
        },
      },
    });
    stub.setQueue({ queue_running: [], queue_pending: [] });
    await svc.run(makeStructure(), makeWorkflow(), "5", { onFinish: finish });

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2500);

    expect(finish).toHaveBeenCalledTimes(1);
    expect(finish).toHaveBeenCalledWith("test-prompt-1", false, {
      nodeId: "5",
      errorDetail: "boom",
      traceback: "tb",
    });
  });
});
