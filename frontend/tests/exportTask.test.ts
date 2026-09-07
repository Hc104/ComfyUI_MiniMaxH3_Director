/**
 * V1.6-A 成片导出任务单测：
 *   TaskRecord.kind 默认 generate / 显式 export；startExportTask 成功（movie/scene）、
 *   业务错误（not_cached）、网络异常；导出进度 exportStage 客户端估算。
 *
 * store 是模块级单例，每个用例 loadProject + clearTasks 重置。
 * startExportTask 内部 setInterval 每 3s 推进 exportStage；用例内 mock API 立即 resolve，
 * 函数返回前 stopStage 已清定时器 → 无悬挂句柄。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import type { Episode, Project, Scene, Shot } from "@/models/project";
import {
  loadProject,
  clearTasks,
  addTask,
  startExportTask,
  useWorkbench,
} from "@/stores/workbench";

function makeShot(id: string, order: number, sceneId: string): Shot {
  return {
    id,
    sceneId,
    order,
    durationSec: 3,
    content: { visual: `镜头${id}的场面` },
    generation: { taskType: "auto", continuityMode: "auto", smartTail: false, stateChange: "", consistencyCheck: "auto" },
  };
}

function makeProject(): Project {
  const sceneA: Scene = {
    id: "sc_a",
    name: "天台夜",
    order: 0,
    assets: { cast: [], locations: [], props: [], styles: [] },
    shots: [makeShot("shot1", 0, "sc_a"), makeShot("shot2", 1, "sc_a")],
  };
  const episode: Episode = {
    id: "ep1",
    episodeNumber: 1,
    title: "第一集",
    scenes: [sceneA],
    assets: { cast: [], locations: [], props: [], styles: [] },
  };
  return { id: "proj", name: "测试", createdAt: "", updatedAt: "", episodes: [episode] };
}

const wb = useWorkbench();

beforeEach(() => {
  loadProject(makeProject());
  clearTasks();
});

describe("TaskRecord.kind（V1.6-A）", () => {
  it("addTask 默认 kind=generate", () => {
    const id = addTask({ scopeLabel: "全部镜头（1）", shotCount: 1, shotOrder: ["shot1"], targetShotIds: null });
    const t = wb.tasks.find((x) => x.id === id)!;
    expect(t.kind).toBe("generate");
    expect(t.exportStage).toBeNull();
    expect(t.exportFile).toBeNull();
  });

  it("addTask 显式 kind=export", () => {
    const id = addTask({
      scopeLabel: "导出整部影片（2 镜）",
      shotCount: 2,
      shotOrder: ["shot1", "shot2"],
      targetShotIds: null,
      kind: "export",
    });
    expect(wb.tasks.find((x) => x.id === id)!.kind).toBe("export");
  });
});

describe("startExportTask（V1.6-A）", () => {
  it("movie 成功：任务 done + exportFile/finalVideo 落账 + exportStage 置位", async () => {
    wb.runService.api.exportMovie = vi.fn(async () => ({
      filename: "MiniMax_Studio_Movie_00001_.mp4",
      scope: "movie" as const,
      segments: 2,
      missing: [],
    }));
    const taskId = await startExportTask({
      scope: "movie",
      sceneId: null,
      scopeLabel: "导出整部影片（2 镜）",
      shotOrder: ["shot1", "shot2"],
      targetShotIds: ["shot1", "shot2"],
      nodeId: "5",
    });
    const t = wb.tasks.find((x) => x.id === taskId)!;
    expect(t.kind).toBe("export");
    expect(t.status).toBe("done");
    expect(t.error).toBeNull();
    expect(t.exportFile).toBe("MiniMax_Studio_Movie_00001_.mp4");
    expect(t.finalVideo).toMatchObject({
      filename: "MiniMax_Studio_Movie_00001_.mp4",
    });
    expect(t.finalVideo!.url).toContain(encodeURIComponent("MiniMax_Studio_Movie_00001_.mp4"));
    // 进度始终有值（至少 1/total）。
    expect(t.exportStage).not.toBeNull();
    expect(t.exportStage!.total).toBe(2);
    expect(t.exportStage!.encoding).toBeGreaterThanOrEqual(1);
    expect(wb.runService.api.exportMovie).toHaveBeenCalledWith(
      "5",
      ["shot1", "shot2"],
      expect.objectContaining({ fps: undefined, skipMissing: undefined }),
    );
  });

  it("scene 成功：走 exportScene 且 sceneId 透传", async () => {
    const fn = vi.fn(async () => ({ filename: "MiniMax_Studio_Scene_00001_.mp4", scope: "scene" as const, sceneId: "sc_a", segments: 2, missing: [] }));
    wb.runService.api.exportScene = fn;
    await startExportTask({
      scope: "scene",
      sceneId: "sc_a",
      scopeLabel: "导出当前地点（2 镜）",
      shotOrder: ["shot1", "shot2"],
      targetShotIds: ["shot1", "shot2"],
      nodeId: "5",
      fps: 24,
      skipMissing: true,
    });
    expect(fn).toHaveBeenCalledWith("5", "sc_a", ["shot1", "shot2"], expect.objectContaining({ fps: 24, skipMissing: true }));
  });

  it("业务错误（not_cached）：任务 failed，error/message 落账", async () => {
    wb.runService.api.exportMovie = vi.fn(async () => ({
      error: "not_cached",
      message: "有 1 个镜头尚未生成。",
      missing: [{ shotId: "shot2", order: 1 }],
    }));
    const taskId = await startExportTask({
      scope: "movie",
      sceneId: null,
      scopeLabel: "导出整部影片（2 镜）",
      shotOrder: ["shot1", "shot2"],
      targetShotIds: ["shot1", "shot2"],
      nodeId: "5",
    });
    const t = wb.tasks.find((x) => x.id === taskId)!;
    expect(t.status).toBe("failed");
    expect(t.error).toBe("有 1 个镜头尚未生成。");
    expect(t.errorDetail).toBe("有 1 个镜头尚未生成。");
    expect(t.finalVideo).toBeNull();
  });

  it("网络异常：任务 failed，errorDetail 带异常消息", async () => {
    wb.runService.api.exportMovie = vi.fn(async () => {
      throw new Error("无法连接 ComfyUI（(当前源)）：fetch failed");
    });
    const taskId = await startExportTask({
      scope: "movie",
      sceneId: null,
      scopeLabel: "导出整部影片（2 镜）",
      shotOrder: ["shot1", "shot2"],
      targetShotIds: null,
      nodeId: "5",
    });
    const t = wb.tasks.find((x) => x.id === taskId)!;
    expect(t.status).toBe("failed");
    expect(t.error).toContain("无法连接 ComfyUI");
    expect(t.errorDetail).toContain("无法连接 ComfyUI");
    expect(t.exportFile).toBeNull();
  });

  it("targetShotIds=null 时导出全部 shotOrder", async () => {
    const fn = vi.fn(async () => ({ filename: "m.mp4", segments: 2, missing: [] }));
    wb.runService.api.exportMovie = fn;
    await startExportTask({
      scope: "movie",
      sceneId: null,
      scopeLabel: "导出整部影片（2 镜）",
      shotOrder: ["shot1", "shot2"],
      targetShotIds: null,
      nodeId: "5",
    });
    expect(fn).toHaveBeenCalledWith("5", ["shot1", "shot2"], expect.anything());
  });
});
