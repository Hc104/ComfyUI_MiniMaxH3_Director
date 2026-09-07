/**
 * V1.7 Phase 5（P1-D）：前端运镜模板占位符填充（cameraTemplates）单测。
 *
 * 纯函数（模型无关）：后端模板 {location}/{time}/{subject}/{emotion}/{object}
 * 占位符 → 本镜/本场景实际值，自动填进 cameraText。模板下拉 + 手编共存，
 * 手编后 cameraTemplate 清空（= 自定义）。
 */
import { describe, it, expect } from "vitest";
import {
  sceneLocationName,
  sceneTimeHint,
  shotSubject,
  shotObject,
  shotEmotion,
  findCameraTemplate,
  fillCameraTemplate,
} from "@/core/cameraTemplates";
import type { CameraTemplate } from "@/services/comfyApi";
import type { Scene, Shot } from "@/models/project";

function shot(overrides: Partial<Shot> = {}): Shot {
  return {
    id: "shot_01",
    sceneId: "scene_01",
    order: 1,
    durationSec: 5,
    content: { visual: "" },
    ...overrides,
  };
}

function scene(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "scene_01",
    name: "山雨楼外",
    order: 1,
    shots: [],
    ...overrides,
  };
}

const walk: CameraTemplate = {
  id: "walk",
  name: "跟随行走",
  intent: "行走",
  template: "中景，{subject}在{location}中行走，{time}，情绪{emotion}。",
};

const seeObject: CameraTemplate = {
  id: "see_object",
  name: "看见目标",
  intent: "特写",
  template: "近景，{subject}注视{object}，{time}。",
};

describe("sceneLocationName：场景地点兜底", () => {
  it("有 location → location 名", () => {
    expect(sceneLocationName(scene({ location: "山雨客栈门前" }))).toBe("山雨客栈门前");
  });

  it("无 location → 场景名", () => {
    expect(sceneLocationName(scene({ name: "第二场" }))).toBe("第二场");
  });

  it("都空 → 「地点」兜底", () => {
    expect(sceneLocationName(scene({ name: "  ", location: "" }))).toBe("地点");
  });
});

describe("sceneTimeHint：时间/天气提示", () => {
  it("time+weather 组合（顿号）", () => {
    expect(sceneTimeHint(scene({ time: "清晨", weather: "雨" }))).toBe("清晨、雨");
  });

  it("只有 time", () => {
    expect(sceneTimeHint(scene({ time: "深夜" }))).toBe("深夜");
  });

  it("都空 → 「当下」兜底（与后端 time_hint 一致）", () => {
    expect(sceneTimeHint(scene({}))).toBe("当下");
  });
});

describe("shotSubject：本镜角色", () => {
  it("多角色顿号连接", () => {
    expect(shotSubject(shot({ castIds: ["林雪", "陈默"] }))).toBe("林雪、陈默");
  });

  it("单角色", () => {
    expect(shotSubject(shot({ castIds: ["林雪"] }))).toBe("林雪");
  });

  it("空 → 「人物」兜底", () => {
    expect(shotSubject(shot({ castIds: [] }))).toBe("人物");
  });
});

describe("shotObject / shotEmotion：兜底占位", () => {
  it("无结构化道具 → 「目标」", () => {
    expect(shotObject(shot({}))).toBe("目标");
  });

  it("无情绪字段 → 「微妙」", () => {
    expect(shotEmotion(shot({}))).toBe("微妙");
  });
});

describe("findCameraTemplate：id 查找", () => {
  it("命中", () => {
    expect(findCameraTemplate([walk], "walk")?.id).toBe("walk");
  });

  it("未命中 → null", () => {
    expect(findCameraTemplate([walk], "none")).toBeNull();
  });

  it("空 id → null", () => {
    expect(findCameraTemplate([walk], undefined)).toBeNull();
  });
});

describe("fillCameraTemplate：占位符替换", () => {
  it("替换全部占位符（subject/location/time/emotion）", () => {
    const s = shot({ castIds: ["林雪"] });
    const sc = scene({ location: "山雨楼外", time: "清晨", weather: "雨" });
    expect(fillCameraTemplate(walk, s, sc)).toBe(
      "中景，林雪在山雨楼外中行走，清晨、雨，情绪微妙。",
    );
  });

  it("object 占位符替换为「目标」", () => {
    const s = shot({});
    const sc = scene({ location: "客栈大堂", time: "夜晚" });
    expect(fillCameraTemplate(seeObject, s, sc)).toBe("近景，人物注视目标，夜晚。");
  });

  it("同一占位符多次出现全部替换（全局替换）", () => {
    const tmpl: CameraTemplate = {
      id: "dup",
      name: "重复",
      intent: "测试",
      template: "{subject}看向{subject}，{location}的{location}。",
    };
    const s = shot({ castIds: ["陈默"] });
    const sc = scene({ location: "屋顶" });
    expect(fillCameraTemplate(tmpl, s, sc)).toBe("陈默看向陈默，屋顶的屋顶。");
  });
});
