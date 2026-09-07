/**
 * DirectorCore — Prompt 媒体引用标签工具（模型无关，V1.2.7）。
 *
 * 在提示词文本里用显式标签引用素材：
 *   <picture>素材名</picture> — 参考图（资产图 / 镜头 refImages）
 *   <video>素材名</video>     — 参考视频（镜头 refVideos）
 *   <audio>素材名</audio>     — 参考音频（镜头 refAudios）
 *
 * 前端：@ 自动补全选中后插入标签，高亮层渲染成带缩略图的 chip。
 * 后端：gen_timeline 解析标签 → 把引用素材注入 refs，并映射 H3 官方
 *       <Picture N>/<Video N>/<Audio N> 标签（见 director/gen_timeline.py）。
 *
 * 标签是纯文本的一部分（随 Prompt 持久化），不新增加独立数据字段。
 */
import type { Asset, AssetKind, Shot } from "@/models/project";
import type { AssetIndex } from "@/core/assetResolver";

export type MediaTagKind = "picture" | "video" | "audio";

/** 一次标签命中：标签在文本中的位置 + 内部名字。 */
export interface MediaTagRef {
  kind: MediaTagKind;
  /** 标签内名字（资产 name 或 ref 文件名，不含 <>）。 */
  name: string;
  /** 标签起点（含 <picture>）。 */
  start: number;
  /** 标签终点（含 </picture>）。 */
  end: number;
  /** 名字起点。 */
  innerStart: number;
  /** 名字终点。 */
  innerEnd: number;
}

/** 标签正则：`<picture>名</picture>` / `<video>名</video>` / `<audio>名</audio>`。 */
const MEDIA_TAG_RE = /<(picture|video|audio)>([^<>]+)<\/(picture|video|audio)>/gu;

// ---------- 提示词 token 解析（高亮层，V1.2.7） ----------

/**
 * 同时识别两种引用语法，供 Prompt 高亮层渲染成带缩略图的小图标：
 *   - 官方编号标签：`<Picture 1>` / `<Video 1>` / `<Audio 1>`（对应段 refs 的 index，1-based）
 *   - 自定义名字标签：`<picture>林雪</picture>` / `<video>动作.mp4</video>` / `<audio>对白.wav</audio>`
 */
export type PromptTokenKind = "picture" | "video" | "audio";

export interface PromptToken {
  kind: PromptTokenKind;
  /** 标签类型：官方编号（numbered）或自定义名字（named）。 */
  numbered: boolean;
  /** 官方编号（1-based，仅 numbered 有）。 */
  num?: number;
  /** 自定义名字（仅 named 有）。 */
  name?: string;
  /** 高亮层显示文本（`Picture 1` / `林雪`）。 */
  label: string;
  start: number;
  end: number;
}

const PROMPT_TOKEN_RE =
  /<(picture|video|audio)\s*(\d+)>|<(picture|video|audio)>([^<>]+)<\/(picture|video|audio)>/giu;

/** 解析提示词里的媒体引用 token（官方编号 + 自定义名字，两种并存）。 */
export function parsePromptTokens(text: string): PromptToken[] {
  if (!text) return [];
  const out: PromptToken[] = [];
  let m: RegExpExecArray | null;
  PROMPT_TOKEN_RE.lastIndex = 0;
  while ((m = PROMPT_TOKEN_RE.exec(text)) !== null) {
    if (m[1]) {
      // 官方编号：<Picture 1> → kind=picture, num=1
      const kind = m[1].toLowerCase() as PromptTokenKind;
      const num = Number(m[2]);
      out.push({
        kind,
        numbered: true,
        num,
        label: `${capKind(kind)} ${num}`,
        start: m.index,
        end: m.index + m[0].length,
      });
    } else if (m[3] && m[3].toLowerCase() === m[5].toLowerCase()) {
      // 自定义名字：<picture>林雪</picture>
      const kind = m[3].toLowerCase() as PromptTokenKind;
      const name = m[4].trim();
      if (!name) continue;
      out.push({
        kind,
        numbered: false,
        name,
        label: name,
        start: m.index,
        end: m.index + m[0].length,
      });
    }
  }
  return out;
}

function capKind(kind: PromptTokenKind): string {
  return kind === "picture" ? "Picture" : kind === "video" ? "Video" : "Audio";
}

/** 解析文本中所有媒体引用标签。名字去首尾空白。 */
export function parseMediaTags(text: string): MediaTagRef[] {
  if (!text) return [];
  const out: MediaTagRef[] = [];
  let m: RegExpExecArray | null;
  MEDIA_TAG_RE.lastIndex = 0;
  while ((m = MEDIA_TAG_RE.exec(text)) !== null) {
    if (m[1] !== m[3]) continue; // 开闭标签不匹配
    const name = m[2].trim();
    if (!name) continue;
    const innerStart = m.index + m[0].indexOf(m[2]);
    const innerEnd = innerStart + m[2].length;
    out.push({
      kind: m[1] as MediaTagKind,
      name,
      start: m.index,
      end: m.index + m[0].length,
      innerStart,
      innerEnd,
    });
  }
  return out;
}

/** 从 caret 位置往前找最近的 @ 触发起点（@ 与 caret 之间无空白）。无则 -1。 */
export function findAtTriggerStart(text: string, caret: number): number {
  let i = caret - 1;
  while (i >= 0) {
    const ch = text[i];
    if (ch === "@") return i;
    if (/\s/.test(ch)) return -1;
    i--;
  }
  return -1;
}

export interface TagInsertResult {
  text: string;
  caret: number;
}

/**
 * 在光标处插入媒体标签。若光标前紧邻 @（正在补全），把 @及其后已输入部分
 * 整体替换成标签（避免残留 @）；否则纯插入。
 */
export function insertMediaTag(
  text: string,
  caret: number,
  kind: MediaTagKind,
  name: string,
): TagInsertResult {
  const atStart = findAtTriggerStart(text, caret);
  const start = atStart >= 0 ? atStart : caret;
  const end = atStart >= 0 ? caret : caret;
  const tag = `<${kind}>${name}</${kind}>`;
  return { text: text.slice(0, start) + tag + text.slice(end), caret: start + tag.length };
}

/**
 * 在光标处插入**官方编号**媒体标签：`<Picture N>` / `<Video N>` / `<Audio N>`。
 * 用于 @ 补全里选择**参考素材**（ref 源）——编号标签不显示文件名（如 a.png），
 * 且后端走官方 refs 注入管线（按段 refs 数组 index 注入，1-based）。
 */
export function insertNumberedMediaTag(
  text: string,
  caret: number,
  kind: MediaTagKind,
  num: number,
): TagInsertResult {
  const atStart = findAtTriggerStart(text, caret);
  const start = atStart >= 0 ? atStart : caret;
  const end = atStart >= 0 ? caret : caret;
  const tag = `<${capKind(kind)} ${num}>`;
  return { text: text.slice(0, start) + tag + text.slice(end), caret: start + tag.length };
}

// ---------- 补全候选 ----------

/** 候选来源标记。 */
export type MediaTagSource = "scene" | "global" | "ref";

/** 一个补全候选（资产 / 参考媒体）。 */
export interface MediaTagCandidate {
  kind: MediaTagKind;
  name: string;
  /** 来源：🎬 场景素材 / 🌐 全局资产 / ref 参考素材。 */
  source: MediaTagSource;
  /** 类型标签（人物/地点/道具/风格/参考图…）。 */
  kindLabel: string;
  /** 缩略图 /view URL（无图则为空）。 */
  imgUrl?: string;
  /** 用于匹配的下文字（name 小写）。 */
  searchKey: string;
  /**
   * 参考素材在段 refs 数组里的 0-based 下标（仅 source=ref 有）。
   * 选中后插入官方编号标签 `<Picture N>`（N=index+1），避免显示文件名。
   */
  refIndex?: number;
}

const KIND_LABEL: Record<AssetKind, string> = {
  cast: "人物",
  location: "地点",
  prop: "道具",
  style: "风格",
};

function comfyView(relPath: string | undefined): string | undefined {
  if (!relPath) return undefined;
  // 复用 comfyInputUrl 的同构逻辑（避免这里再依赖 services 层导致循环引用）。
  const norm = relPath.replace(/\\/g, "/");
  const idx = norm.lastIndexOf("/");
  const filename = idx >= 0 ? norm.slice(idx + 1) : norm;
  const subfolder = idx >= 0 ? norm.slice(0, idx) : "";
  const q = new URLSearchParams({ filename, subfolder, type: "input" });
  return `/view?${q.toString()}`;
}

/** 场景素材组里的资产 id 集合（用于来源标记）。 */
function sceneAssetIds(scene: { assets?: { cast: Asset[]; locations: Asset[]; props: Asset[]; styles: Asset[] } } | undefined): Set<string> {
  const ids = new Set<string>();
  const assets = scene?.assets;
  if (!assets) return ids;
  for (const arr of [assets.cast, assets.locations, assets.props, assets.styles]) {
    for (const a of arr ?? []) ids.add(a.id);
  }
  return ids;
}

/** buildMediaTagCandidates 的 scene 参数形态。 */
export interface MediaTagSceneShape {
  id: string;
  assets?: { cast: Asset[]; locations: Asset[]; props: Asset[]; styles: Asset[] };
  /** 场景参考图（ComfyUI input 相对路径，V1.1.1 场景设置面板上传）。 */
  referenceImage?: string;
}

/**
 * 构建 @ 补全候选：全局资产 + 当前场景资产（图片 → <picture>），
 * + 场景参考图（scene.referenceImage → <picture>）
 * + 当前镜头 refs（refImages → <picture> / refVideos → <video> / refAudios → <audio>）。
 * scene 可选：无 scene 时所有资产标记为 global。
 */
export function buildMediaTagCandidates(
  index: AssetIndex,
  shot: Shot | undefined,
  scene: MediaTagSceneShape | undefined,
): MediaTagCandidate[] {
  const out: MediaTagCandidate[] = [];
  const sceneIds = sceneAssetIds(scene);
  for (const a of index.assets) {
    out.push({
      kind: "picture",
      name: a.name,
      source: sceneIds.has(a.id) ? "scene" : "global",
      kindLabel: KIND_LABEL[a.kind],
      imgUrl: comfyView(a.imageFile),
      searchKey: a.name.toLowerCase(),
    });
  }
  if (scene?.referenceImage) {
    const fileName = basenameOf(scene.referenceImage);
    out.push({
      kind: "picture",
      name: fileName,
      source: "scene",
      kindLabel: "地点参考图",
      imgUrl: comfyView(scene.referenceImage),
      searchKey: fileName.toLowerCase(),
    });
  }
  const refs = shot?.refs;
  if (refs) {
    (refs.refImages ?? []).forEach((r, i) => {
      const fileName = r.fileName || basenameOf(r.imageFile);
      out.push({
        kind: "picture",
        name: fileName,
        source: "ref",
        kindLabel: "参考图",
        imgUrl: comfyView(r.imageFile),
        searchKey: fileName.toLowerCase(),
        refIndex: i,
      });
    });
    (refs.refVideos ?? []).forEach((v, i) => {
      const fileName = v.fileName || basenameOf(v.videoFile);
      out.push({
        kind: "video",
        name: fileName,
        source: "ref",
        kindLabel: "参考视频",
        imgUrl: comfyView(v.videoFile),
        searchKey: fileName.toLowerCase(),
        refIndex: i,
      });
    });
    (refs.refAudios ?? []).forEach((a, i) => {
      const fileName = a.fileName || basenameOf(a.audioFile);
      out.push({
        kind: "audio",
        name: fileName,
        source: "ref",
        kindLabel: "参考音频",
        imgUrl: comfyView(a.audioFile),
        searchKey: fileName.toLowerCase(),
        refIndex: i,
      });
    });
  }
  return out;
}

function basenameOf(relPath: string): string {
  const norm = relPath.replace(/\\/g, "/");
  return norm.slice(norm.lastIndexOf("/") + 1);
}

/** 按用户已输入的关键字过滤候选（子串包含，大小写不敏感）。 */
export function filterCandidates(candidates: MediaTagCandidate[], query: string): MediaTagCandidate[] {
  const q = query.trim().toLowerCase();
  if (!q) return candidates;
  return candidates.filter((c) => c.searchKey.includes(q));
}
