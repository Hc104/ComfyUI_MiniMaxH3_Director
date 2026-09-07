/**
 * Prompt 分区编辑器单测（V2）：分区合并逻辑 + 结构化回退 + 语言分隔符。
 */
import { describe, it, expect } from "vitest";
import type { Shot } from "@/models/project";
import { buildShotPromptText, joinPromptParts, shotCameraText, shotSoundText } from "@/core/promptSections";

function mkShot(partial: Partial<Shot["content"]> = {}, extra: Partial<Shot> = {}): Shot {
  return {
    id: "s1",
    sceneId: "sc1",
    order: 0,
    durationSec: 5,
    content: { visual: "", ...partial },
    ...extra,
  };
}

describe("joinPromptParts", () => {
  it("中文内容用中文逗号", () => {
    expect(joinPromptParts(["林雪走过", "近景"])).toBe("林雪走过，近景");
  });
  it("英文内容用英文逗号+空格", () => {
    expect(joinPromptParts(["A girl walks", "close-up"])).toBe("A girl walks, close-up");
  });
  it("空分区跳过；全空返回空串", () => {
    expect(joinPromptParts(["林雪", "", "  "])).toBe("林雪");
    expect(joinPromptParts([""])).toBe("");
  });
});

describe("buildShotPromptText", () => {
  it("只有 visual：合并结果 == visual（向后兼容）", () => {
    const shot = mkShot({ visual: "林雪走过霓虹街区" });
    expect(buildShotPromptText(shot)).toBe("林雪走过霓虹街区");
  });

  it("中文全分区按 画面→摄影→风格→声音 拼接", () => {
    const shot = mkShot({
      visual: "林雪走过霓虹街区",
      cameraText: "近景，缓慢推近",
      style: "赛博朋克夜色",
      soundText: "雨声，低音电子",
    });
    expect(buildShotPromptText(shot)).toBe("林雪走过霓虹街区，近景，缓慢推近，赛博朋克夜色，雨声，低音电子");
  });

  it("英文全分区用英文分隔符", () => {
    const shot = mkShot({
      visual: "A girl walks through the neon street",
      cameraText: "close-up, slow push-in",
      style: "cyberpunk night",
      soundText: "rain, low electronic beat",
    });
    expect(buildShotPromptText(shot)).toBe(
      "A girl walks through the neon street, close-up, slow push-in, cyberpunk night, rain, low electronic beat",
    );
  });

  it("部分分区为空：跳过空分区", () => {
    const shot = mkShot({ visual: "林雪回头", style: "浅景深" });
    expect(buildShotPromptText(shot)).toBe("林雪回头，浅景深");
  });

  it("自由文本优先：cameraText 空时回退结构化 camera", () => {
    const shot = mkShot({ visual: "林雪回头" }, {
      camera: { shotSize: "近景", movement: "缓慢推进", speed: "平稳", depthOfField: "浅景深" },
    });
    expect(shotCameraText(shot)).toBe("近景，缓慢推进，平稳，浅景深");
    expect(buildShotPromptText(shot)).toBe("林雪回头，近景，缓慢推进，平稳，浅景深");
  });

  it("自由文本优先：cameraText 有值时忽略结构化 camera", () => {
    const shot = mkShot({ visual: "林雪回头", cameraText: "低角度环绕" }, {
      camera: { shotSize: "近景", movement: "缓慢推进", speed: "平稳", depthOfField: "浅景深" },
    });
    expect(shotCameraText(shot)).toBe("低角度环绕");
    expect(buildShotPromptText(shot)).toContain("林雪回头，低角度环绕");
    expect(buildShotPromptText(shot)).not.toContain("近景");
  });

  it("声音回退结构化 sound（环境音+音乐）", () => {
    const shot = mkShot({ visual: "林雪回头" }, {
      sound: { ambient: "雨声", music: "低音电子" },
    });
    expect(shotSoundText(shot)).toBe("雨声，低音电子");
    expect(buildShotPromptText(shot)).toBe("林雪回头，雨声，低音电子");
  });

  it("全部为空：返回空串", () => {
    expect(buildShotPromptText(mkShot())).toBe("");
  });
});
