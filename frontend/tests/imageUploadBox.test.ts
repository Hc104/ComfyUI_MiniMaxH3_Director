// @vitest-environment happy-dom
/**
 * V1.2.5 ImageUploadBox 渲染冒烟（V1.2.6 扩展 mediaType=image/video/audio）：
 *   空态/有图态、点击选文件、上传成功 emit relPath、上传失败提示、替换/删除、
 *   进度显示、compact 模式、模型值反斜杠规范化、媒体类型路由与校验。
 *
 * api 用最小 MediaUploadApi mock（只暴露 uploadFile），不绑定 ComfyApiClient。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import ImageUploadBox, { type MediaUploadApi } from "@/workbench/ImageUploadBox.vue";
import type { Mock } from "vitest";

const ASSET_DIR = "minimax_studio/assets";

function makeApi(): MediaUploadApi & { uploadFile: Mock } {
  const api = {
    // 回显上传文件名（subfolder 固定），模拟 ComfyUI /upload 返回体
    uploadFile: vi.fn(async (_f: File) => ({ name: _f.name, subfolder: ASSET_DIR, type: "input" as const })),
  };
  return api as MediaUploadApi & { uploadFile: Mock };
}

function setFile(wrapper: VueWrapper, file: File) {
  const input = wrapper.find('input[type="file"]');
  Object.defineProperty(input.element, "files", { value: [file], configurable: true });
  return input.trigger("change");
}

function imgFile(name = "林雪.png"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/png" });
}

function videoFile(name = "动作参考.mp4"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "video/mp4" });
}

function audioFile(name = "对白.wav"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "audio/wav" });
}

describe("ImageUploadBox", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("空态显示上传入口，点击触发隐藏 file input", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: "", api, subfolder: ASSET_DIR, placeholder: "上传参考图" },
    });
    expect(wrapper.find(".img-empty").exists()).toBe(true);
    expect(wrapper.text()).toContain("上传参考图");
    await wrapper.find(".img-empty").trigger("click");
    // click 之后没有文件选择对话框（happy-dom），不报错即可
  });

  it("选择图片 → api.uploadFile(kind:image, subfolder, overwrite:false) → emit update:modelValue=相对路径", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: "", api, subfolder: ASSET_DIR },
    });
    await setFile(wrapper, imgFile());
    await flushPromises();
    expect(api.uploadFile).toHaveBeenCalledTimes(1);
    const [, opts] = api.uploadFile.mock.calls[0] as [File, { kind?: string; subfolder?: string; overwrite?: boolean }];
    expect(opts?.kind).toBe("image");
    expect(opts?.subfolder).toBe(ASSET_DIR);
    expect(opts?.overwrite).toBe(false);
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([`${ASSET_DIR}/林雪.png`]);
    expect(wrapper.emitted("uploaded")?.at(-1)).toEqual([`${ASSET_DIR}/林雪.png`]);
  });

  it("有图态显示缩略图（/view?filename=…&subfolder=…&type=input），可替换/删除", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: `${ASSET_DIR}/林雪.png`, api, subfolder: ASSET_DIR },
    });
    const img = wrapper.find(".img-thumb");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toContain("/view?");
    expect(img.attributes("src")).toContain("filename=" + encodeURIComponent("林雪.png"));
    expect(img.attributes("src")).toContain("type=input");
    // 替换 → 再走一次选择
    await wrapper.find(".img-btn").trigger("click"); // 无对话框，不报错
    // 删除 → emit update:modelValue("")
    await wrapper.find(".img-btn.danger").trigger("click");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([""]);
  });

  it("上传失败 → 显示错误信息 + emit error", async () => {
    const api = makeApi();
    api.uploadFile.mockRejectedValueOnce(new Error("上传图片超时"));
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: "", api, subfolder: ASSET_DIR },
    });
    await setFile(wrapper, imgFile());
    await flushPromises();
    expect(wrapper.find(".img-err").exists()).toBe(true);
    expect(wrapper.text()).toContain("上传图片超时");
    expect(wrapper.emitted("error")?.at(-1)).toEqual(["上传图片超时"]);
  });

  it("非图片/超 20MB 文件被拒，不上传", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, { props: { modelValue: "", api, subfolder: ASSET_DIR } });
    const txt = new File(["hi"], "a.txt", { type: "text/plain" });
    await setFile(wrapper, txt);
    await flushPromises();
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("只支持图片文件");

    const big = new File([new Uint8Array(21 * 1024 * 1024)], "b.png", { type: "image/png" });
    await setFile(wrapper, big);
    await flushPromises();
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("图片超过 20MB");
  });

  it("上传中显示进度百分比，完成后回写", async () => {
    const api = makeApi();
    let captured: { onProgress?: (l: number, t: number) => void; resolve: (v: unknown) => void } | null = null;
    api.uploadFile.mockImplementationOnce(
      (_f, opts) =>
        new Promise((resolve) => {
          captured = { onProgress: opts?.onProgress, resolve };
        }),
    );
    const wrapper = mount(ImageUploadBox, { props: { modelValue: "", api, subfolder: ASSET_DIR } });
    await setFile(wrapper, imgFile());
    await flushPromises();
    expect(wrapper.text()).toContain("0%");
    captured!.onProgress?.(50, 100);
    await flushPromises();
    expect(wrapper.text()).toContain("50%");
    captured!.resolve({ name: "林雪.png", subfolder: ASSET_DIR, type: "input" });
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([`${ASSET_DIR}/林雪.png`]);
  });

  it("compact 模式：有图小缩略图 + 删除；无图 ＋ 按钮", async () => {
    const api = makeApi();
    const withImg = mount(ImageUploadBox, {
      props: { modelValue: `${ASSET_DIR}/a.png`, api, compact: true },
    });
    expect(withImg.find(".c-thumb").exists()).toBe(true);
    await withImg.find(".c-del").trigger("click");
    expect(withImg.emitted("update:modelValue")?.at(-1)).toEqual([""]);

    const empty = mount(ImageUploadBox, { props: { modelValue: "", api, compact: true } });
    expect(empty.find(".c-add").exists()).toBe(true);
    expect(empty.find(".c-add").text()).toBe("＋");
  });

  it("modelValue 带反斜杠时缩略图 URL 也规范化（\\ 转 /）", () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: `minimax_studio\\assets\\a.png`, api },
    });
    const src = wrapper.find(".img-thumb").attributes("src") ?? "";
    expect(src).toContain("filename=a.png");
    expect(src).not.toContain("\\");
  });

  // ---- V1.2.6 媒体类型：视频 / 音频 ----

  it("mediaType=video：上传走 kind=video，有值渲染 <video> 预览", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: "", api, subfolder: ASSET_DIR, mediaType: "video" },
    });
    await setFile(wrapper, videoFile());
    await flushPromises();
    const [, opts] = api.uploadFile.mock.calls[0] as [File, { kind?: string }];
    expect(opts?.kind).toBe("video");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([`${ASSET_DIR}/动作参考.mp4`]);

    const withVal = mount(ImageUploadBox, {
      props: { modelValue: `${ASSET_DIR}/动作参考.mp4`, api, mediaType: "video" },
    });
    const video = withVal.find("video");
    expect(video.exists()).toBe(true);
    expect(video.attributes("controls")).toBeDefined();
    expect(video.attributes("src")).toContain("filename=" + encodeURIComponent("动作参考.mp4"));
  });

  it("mediaType=audio：上传走 kind=audio，有值渲染 <audio> 预览", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: "", api, subfolder: ASSET_DIR, mediaType: "audio" },
    });
    await setFile(wrapper, audioFile());
    await flushPromises();
    const [, opts] = api.uploadFile.mock.calls[0] as [File, { kind?: string }];
    expect(opts?.kind).toBe("audio");
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual([`${ASSET_DIR}/对白.wav`]);

    const withVal = mount(ImageUploadBox, {
      props: { modelValue: `${ASSET_DIR}/对白.wav`, api, mediaType: "audio" },
    });
    const audio = withVal.find("audio");
    expect(audio.exists()).toBe(true);
    expect(audio.attributes("controls")).toBeDefined();
  });

  it("mediaType=video 拒绝图片/超 500MB，报「只支持视频文件」「视频超过 500MB」", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: "", api, subfolder: ASSET_DIR, mediaType: "video" },
    });
    await setFile(wrapper, imgFile());
    await flushPromises();
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("只支持视频文件");

    const big = new File([new Uint8Array(501 * 1024 * 1024)], "b.mp4", { type: "video/mp4" });
    await setFile(wrapper, big);
    await flushPromises();
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("视频超过 500MB");
  });

  it("mediaType=audio 拒绝视频/超 100MB，报「只支持音频文件」「音频超过 100MB」", async () => {
    const api = makeApi();
    const wrapper = mount(ImageUploadBox, {
      props: { modelValue: "", api, subfolder: ASSET_DIR, mediaType: "audio" },
    });
    await setFile(wrapper, videoFile());
    await flushPromises();
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("只支持音频文件");

    const big = new File([new Uint8Array(101 * 1024 * 1024)], "b.wav", { type: "audio/wav" });
    await setFile(wrapper, big);
    await flushPromises();
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("音频超过 100MB");
  });

  it("compact 模式 mediaType=video/audio：有值显示 🎬/🎵 图标", () => {
    const api = makeApi();
    const vid = mount(ImageUploadBox, {
      props: { modelValue: `${ASSET_DIR}/动作.mp4`, api, compact: true, mediaType: "video" },
    });
    expect(vid.find(".c-media").exists()).toBe(true);
    expect(vid.find(".c-media").text()).toBe("🎬");

    const aud = mount(ImageUploadBox, {
      props: { modelValue: `${ASSET_DIR}/对白.wav`, api, compact: true, mediaType: "audio" },
    });
    expect(aud.find(".c-media").exists()).toBe(true);
    expect(aud.find(".c-media").text()).toBe("🎵");
  });
});
