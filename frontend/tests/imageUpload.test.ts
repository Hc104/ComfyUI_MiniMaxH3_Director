// @vitest-environment happy-dom
/**
 * V1.2.5 上传链路单测：
 *   uploadedRelPath / comfyInputUrl 路径工具 + ComfyApiClient.uploadImage 的 XHR 行为
 *   （multipart 字段、overwrite=false 冲突后缀语义、进度回调、失败抛 ComfyApiError）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ComfyApiClient, ComfyApiError, comfyInputUrl, comfyMediaUrl, uploadedRelPath } from "@/services/comfyApi";

class FakeXHR {
  static instances: FakeXHR[] = [];
  url = "";
  method = "";
  form: FormData | null = null;
  private _status = 200;
  private _resp: unknown = null;
  private _respText = "";
  private _onload: ((ev: ProgressEvent) => unknown) | null = null;
  private _onerror: ((ev: ProgressEvent) => unknown) | null = null;
  private _ontimeout: ((ev: ProgressEvent) => unknown) | null = null;
  responseType = "";
  timeout = 0;
  upload = { onprogress: null as ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };

  constructor() {
    FakeXHR.instances.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  send(form: FormData) {
    this.form = form;
  }
  set onload(fn: ((ev: ProgressEvent) => unknown) | null) {
    this._onload = fn;
  }
  get onload() {
    return this._onload;
  }
  set onerror(fn: ((ev: ProgressEvent) => unknown) | null) {
    this._onerror = fn;
  }
  get onerror() {
    return this._onerror;
  }
  set ontimeout(fn: ((ev: ProgressEvent) => unknown) | null) {
    this._ontimeout = fn;
  }
  get ontimeout() {
    return this._ontimeout;
  }
  get status() {
    return this._status;
  }
  get response() {
    return this._resp;
  }
  get responseText() {
    return this._respText;
  }
  respond(status: number, body: unknown) {
    this._status = status;
    this._resp = body;
    this._respText = typeof body === "string" ? body : "";
    this._onload?.(new ProgressEvent("load"));
  }
  fail() {
    this._onerror?.(new ProgressEvent("error"));
  }
}

const realXHR = globalThis.XMLHttpRequest;
beforeEach(() => {
  FakeXHR.instances = [];
  globalThis.XMLHttpRequest = FakeXHR as unknown as typeof XMLHttpRequest;
});
afterEach(() => {
  globalThis.XMLHttpRequest = realXHR;
});

describe("路径工具", () => {
  it("uploadedRelPath：有 subfolder 拼相对路径；无 subfolder 只有文件名", () => {
    expect(uploadedRelPath({ name: "a.png", subfolder: "minimax_studio/assets", type: "input" })).toBe(
      "minimax_studio/assets/a.png",
    );
    expect(uploadedRelPath({ name: "a.png", subfolder: "", type: "input" })).toBe("a.png");
  });

  it("comfyInputUrl：相对路径拆成 filename + subfolder + type=input 的 /view URL", () => {
    const url = comfyInputUrl("minimax_studio/assets/a.png");
    expect(url.startsWith("/view?")).toBe(true);
    expect(url).toContain("filename=a.png");
    expect(url).toContain("type=input");
    expect(comfyInputUrl("b.png")).toContain("filename=b.png");
    expect(comfyInputUrl("")).toBe("");
  });
});

describe("ComfyApiClient.uploadImage", () => {
  const api = new ComfyApiClient("");
  const file = new File([new Uint8Array([1, 2, 3])], "林雪.png", { type: "image/png" });

  it("POST /upload/image，multipart 带 image/overwrite=false/type=input/subfolder", async () => {
    const p = api.uploadImage(file, { subfolder: "minimax_studio/assets" });
    const xhr = FakeXHR.instances[0];
    expect(xhr.url).toBe("/upload/image");
    expect(xhr.method).toBe("POST");
    expect(xhr.form!.get("overwrite")).toBe("false");
    expect(xhr.form!.get("type")).toBe("input");
    expect(xhr.form!.get("subfolder")).toBe("minimax_studio/assets");
    expect(xhr.form!.get("image")).toBeInstanceOf(File);
    xhr.respond(200, { name: "林雪.png", subfolder: "minimax_studio/assets", type: "input" });
    await expect(p).resolves.toEqual({ name: "林雪.png", subfolder: "minimax_studio/assets", type: "input" });
  });

  it("进度回调透传 loaded/total", async () => {
    const onProgress = vi.fn();
    const p = api.uploadImage(file, { onProgress });
    const xhr = FakeXHR.instances[0];
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
    expect(onProgress).toHaveBeenCalledWith(50, 100);
    xhr.respond(200, { name: "a.png", subfolder: "", type: "input" });
    await p;
  });

  it("非 2xx → reject ComfyApiError（含 status）", async () => {
    const p1 = api.uploadImage(file);
    FakeXHR.instances[0].respond(500, "boom");
    await expect(p1).rejects.toBeInstanceOf(ComfyApiError);
    const p2 = api.uploadImage(file);
    FakeXHR.instances[1].respond(404, "nope");
    await expect(p2.catch((e: ComfyApiError) => e.status)).resolves.toBe(404);
  });

  it("网络错误 → reject ComfyApiError", async () => {
    const p = api.uploadImage(file);
    FakeXHR.instances[0].fail();
    await expect(p).rejects.toBeInstanceOf(ComfyApiError);
  });

  it("返回体缺 name → reject ComfyApiError", async () => {
    const p = api.uploadImage(file);
    FakeXHR.instances[0].respond(200, { subfolder: "x" });
    await expect(p).rejects.toBeInstanceOf(ComfyApiError);
  });
});

describe("ComfyApiClient.uploadFile（V1.2.6 媒体类型路由）", () => {
  const api = new ComfyApiClient("");

  it("kind=audio → POST /upload/audio，multipart field=audio", async () => {
    const audio = new File([new Uint8Array([1, 2, 3])], "对白.wav", { type: "audio/wav" });
    const p = api.uploadFile(audio, { kind: "audio", subfolder: "minimax_studio/assets" });
    const xhr = FakeXHR.instances[FakeXHR.instances.length - 1];
    expect(xhr.url).toBe("/upload/audio");
    expect(xhr.form!.get("audio")).toBeInstanceOf(File);
    expect(xhr.form!.get("overwrite")).toBe("false");
    expect(xhr.form!.get("type")).toBe("input");
    expect(xhr.form!.get("subfolder")).toBe("minimax_studio/assets");
    xhr.respond(200, { name: "对白.wav", subfolder: "minimax_studio/assets", type: "input" });
    await expect(p).resolves.toEqual({ name: "对白.wav", subfolder: "minimax_studio/assets", type: "input" });
  });

  it("kind=video → POST /upload/image，multipart field=image", async () => {
    const video = new File([new Uint8Array([1, 2, 3])], "动作参考.mp4", { type: "video/mp4" });
    const p = api.uploadFile(video, { kind: "video", subfolder: "minimax_studio/assets" });
    const xhr = FakeXHR.instances[FakeXHR.instances.length - 1];
    expect(xhr.url).toBe("/upload/image");
    expect(xhr.form!.get("image")).toBeInstanceOf(File);
    xhr.respond(200, { name: "动作参考.mp4", subfolder: "minimax_studio/assets", type: "input" });
    await expect(p).resolves.toEqual({ name: "动作参考.mp4", subfolder: "minimax_studio/assets", type: "input" });
  });

  it("kind=audio 错误提示带「音频」，uploadImage 兼容入口仍走 /upload/image", async () => {
    const audio = new File([new Uint8Array([1])], "a.wav", { type: "audio/wav" });
    const p = api.uploadFile(audio, { kind: "audio" });
    FakeXHR.instances[FakeXHR.instances.length - 1].fail();
    await expect(p.catch((e: Error) => e.message)).resolves.toContain("音频");

    const img = new File([new Uint8Array([1])], "a.png", { type: "image/png" });
    const p2 = api.uploadImage(img);
    expect(FakeXHR.instances[FakeXHR.instances.length - 1].url).toBe("/upload/image");
    FakeXHR.instances[FakeXHR.instances.length - 1].respond(200, { name: "a.png", subfolder: "", type: "input" });
    await expect(p2).resolves.toEqual({ name: "a.png", subfolder: "", type: "input" });
  });

  it("comfyMediaUrl 与 comfyInputUrl 等价（音频/视频同走 /view）", () => {
    const rel = "minimax_studio/assets/动作.mp4";
    expect(comfyMediaUrl(rel)).toBe(comfyInputUrl(rel));
  });
});
