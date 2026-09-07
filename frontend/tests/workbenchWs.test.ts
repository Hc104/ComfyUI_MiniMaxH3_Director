/**
 * V1.4-P1 WS 连接状态四态映射单测：
 *   disconnected（初始/未连接）→ connected（已连接）→ reconnecting（断开重连中）
 *   → recovered（刚恢复，短暂展示后回落 connected）。
 *
 * handleWsStatus 是 ComfyWsClient.onStatus 的桥接：open→true / close|disconnect→false。
 * recovered 回落用模块级定时器，测试用 fake timers 控制。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useWorkbench, handleWsStatus, setWsDisconnected } from "@/stores/workbench";

describe("V1.4-P1 WS 连接状态（三/四态圆点）", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // 每个用例从「未连接」基线开始，并清掉上一个用例残留的回落定时器。
    setWsDisconnected();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("初始基线：disconnected（未连接）", () => {
    expect(useWorkbench().wsStatus).toBe("disconnected");
  });

  it("首次连接：disconnected → handleWsStatus(true) → connected（不经过 recovered）", () => {
    handleWsStatus(true);
    expect(useWorkbench().wsStatus).toBe("connected");
  });

  it("断线：handleWsStatus(false) → reconnecting（自动重连中）", () => {
    handleWsStatus(true);
    handleWsStatus(false);
    expect(useWorkbench().wsStatus).toBe("reconnecting");
  });

  it("断线后恢复：reconnecting → handleWsStatus(true) → recovered，2.5s 后回落 connected", async () => {
    handleWsStatus(true);
    handleWsStatus(false);
    expect(useWorkbench().wsStatus).toBe("reconnecting");
    handleWsStatus(true);
    expect(useWorkbench().wsStatus).toBe("recovered");
    await vi.advanceTimersByTimeAsync(2500);
    expect(useWorkbench().wsStatus).toBe("connected");
  });

  it("recovered 展示期间再次断线 → 立即回 reconnecting，回落定时器被清不误改状态", async () => {
    handleWsStatus(true);
    handleWsStatus(false);
    handleWsStatus(true);
    expect(useWorkbench().wsStatus).toBe("recovered");
    handleWsStatus(false);
    expect(useWorkbench().wsStatus).toBe("reconnecting");
    // 残留的 recovered 回落定时器不应把 reconnecting 改回 connected。
    await vi.advanceTimersByTimeAsync(3000);
    expect(useWorkbench().wsStatus).toBe("reconnecting");
  });

  it("setWsDisconnected 归位未连接", () => {
    handleWsStatus(true);
    handleWsStatus(false);
    setWsDisconnected();
    expect(useWorkbench().wsStatus).toBe("disconnected");
  });

  it("connected 稳定时重复 onStatus(true) 保持 connected（不触发 recovered）", () => {
    handleWsStatus(true);
    handleWsStatus(true);
    handleWsStatus(true);
    expect(useWorkbench().wsStatus).toBe("connected");
  });
});
