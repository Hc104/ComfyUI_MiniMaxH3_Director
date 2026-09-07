/**
 * ComfyUI HTTP API 客户端。
 *
 * 只封装标准 ComfyUI REST API + 本节点的 /minimax 扩展路由。
 * V1 走 Vite 同源代理（见 vite.config.ts），生产部署到 ComfyUI 静态目录则无需 CORS。
 */

export interface ComfyNodeInput {
  class_type: string;
  inputs: Record<string, unknown>;
}

/** /prompt 提交体。 */
export interface PromptRequest {
  prompt: Record<string, ComfyNodeInput>;
  client_id?: string;
  extra_data?: Record<string, unknown>;
}

export interface QueueItem {
  [nodeId: string]: ComfyNodeInput;
}

export interface QueueResponse {
  queue_running: Array<[number, number, QueueItem]>;
  queue_pending: Array<[number, number, QueueItem]>;
}

export interface HistoryItem {
  prompt: Array<[number, QueueItem]>;
  outputs: Record<string, Record<string, unknown>>;
  status?: { status_str?: string; completed?: boolean; messages?: unknown[] };
}

/** ComfyUI 输出的视频引用（SaveVideo/PreviewVideo 节点 ui 输出）。 */
export interface ComfyVideoRef {
  filename: string;
  subfolder: string;
  type: string;
}

/** 从节点 ui 输出里提取视频列表。适配两处来源：
 * - WS `executed` 事件的 data.output（SaveVideo 节点 ui）
 * - /history/{prompt_id} 的 outputs[nodeId]
 * 结构均为 { images: [{filename, subfolder, type}], animated: [true] }。
 */
export function extractVideosFromUi(ui: unknown): ComfyVideoRef[] {
  if (!ui || typeof ui !== "object") return [];
  const images = (ui as Record<string, unknown>).images;
  if (!Array.isArray(images)) return [];
  const out: ComfyVideoRef[] = [];
  for (const img of images) {
    if (!img || typeof img !== "object") continue;
    const r = img as Record<string, unknown>;
    if (typeof r.filename === "string" && r.filename.trim()) {
      out.push({
        filename: r.filename,
        subfolder: typeof r.subfolder === "string" ? r.subfolder : "",
        type: typeof r.type === "string" ? r.type : "output",
      });
    }
  }
  return out;
}

/** 构造可播放的 /view URL（走 Vite 代理同源，生产部署到 ComfyUI 静态目录也同源）。 */
export function comfyViewUrl(v: ComfyVideoRef): string {
  const q = new URLSearchParams({ filename: v.filename, type: v.type });
  if (v.subfolder) q.set("subfolder", v.subfolder);
  return `/view?${q.toString()}`;
}

/** 从 history item 的所有节点输出里收集第一个含视频的输出。 */
export function firstVideoFromHistory(item: HistoryItem | undefined): ComfyVideoRef | null {
  if (!item) return null;
  // 先取视频扩展名的输出（SaveVideo 的 mp4）；退回任意 images（预览图）。
  const all: ComfyVideoRef[] = [];
  for (const nodeId of Object.keys(item.outputs)) {
    all.push(...extractVideosFromUi(item.outputs[nodeId]));
  }
  if (!all.length) return null;
  return all.find((v) => isVideoFile(v.filename)) ?? all[0];
}

const VIDEO_EXT_RE = /\.(mp4|webm|mov|mkv|avi|gif)$/i;
export function isVideoFile(filename: string): boolean {
  return VIDEO_EXT_RE.test(filename);
}

export interface SegmentStatusResponse {
  cached: number[];
  states: Record<string, string>;
}

export class ComfyApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly body?: string,
  ) {
    super(message);
    this.name = "ComfyApiError";
  }
}

export class ComfyApiClient {
  constructor(
    /** 根地址，如 http://127.0.0.1:8188（走代理时留相对路径如 ""）。 */
    readonly baseUrl: string = "",
    readonly defaultClientId?: string,
  ) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${this.baseUrl}${path}`, init);
    } catch (err) {
      throw new ComfyApiError(
        `无法连接 ComfyUI（${this.baseUrl || "(当前源)"}）：${err instanceof Error ? err.message : String(err)}`,
      );
    }
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new ComfyApiError(`ComfyUI ${path} 请求失败：${res.status} ${res.statusText}`, res.status, body);
    }
    return (await res.json()) as T;
  }

  /** 校验节点存在（返回 object_info 中该 class 的输入定义）。 */
  async objectInfo(classType?: string): Promise<Record<string, unknown>> {
    const suffix = classType ? `?${new URLSearchParams({ class_type: classType })}` : "";
    const info = await this.request<Record<string, unknown>>(`/object_info${suffix}`);
    return info;
  }

  /** 校验 MiniMaxH3Director 节点可用。 */
  async assertDirectorNode(): Promise<void> {
    const info = await this.objectInfo("MiniMaxH3Director");
    if (!info || !("MiniMaxH3Director" in info)) {
      throw new ComfyApiError("MiniMaxH3Director 节点不存在，请确认 custom_nodes 已安装且已刷新。");
    }
  }

  /** 提交 prompt，返回 prompt_id。 */
  async submitPrompt(req: PromptRequest): Promise<{ prompt_id: string }> {
    const body: PromptRequest = { ...req, client_id: req.client_id ?? this.defaultClientId };
    const res = await fetch(`${this.baseUrl}/prompt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ComfyApiError(`提交 prompt 失败：${res.status} ${text.slice(0, 500)}`, res.status, text);
    }
    return (await res.json()) as { prompt_id: string };
  }

  /** 当前队列（运行中 + 排队）。 */
  async queue(): Promise<QueueResponse> {
    return this.request<QueueResponse>("/queue");
  }

  /** 历史（最近 runs）。 */
  async history(maxItems = 20): Promise<Record<string, HistoryItem>> {
    return this.request<Record<string, HistoryItem>>(`/history?max_items=${maxItems}`);
  }

  /** 按 prompt_id 取单条历史。 */
  async historyById(promptId: string): Promise<Record<string, HistoryItem>> {
    return this.request<Record<string, HistoryItem>>(`/history/${promptId}`);
  }

  /** 取消排队（running 会请求中断）。 */
  async interrupt(): Promise<void> {
    await this.request<Record<string, never>>("/interrupt", { method: "POST" });
  }

  async deletePending(promptId: string): Promise<void> {
    await this.request<Record<string, never>>(`/queue/${promptId}`, { method: "DELETE" });
  }

  // ---- MiniMaxH3Director 扩展路由 ----

  /** 段缓存/运行状态灯。shotIds 可选：传当前项目镜头 id 列表后，后端只返回
   * 缓存镜头身份匹配的段（排除旧项目/旧时间线残留缓存，防止播放全片加载旧视频）。 */
  async segmentStatus(nodeId: string, shotIds?: string[]): Promise<SegmentStatusResponse> {
    const q = new URLSearchParams({ node_id: nodeId });
    if (shotIds && shotIds.length) q.set("shot_ids", shotIds.join(","));
    return this.request<SegmentStatusResponse>(`/minimax/director/segment_status?${q.toString()}`);
  }

  /** 段缓存状态（旧路由，仍可用；同样支持 shotIds 镜头身份校验）。 */
  async segmentCacheStatus(nodeId: string, shotIds?: string[]): Promise<SegmentStatusResponse> {
    const q = new URLSearchParams({ node_id: nodeId });
    if (shotIds && shotIds.length) q.set("shot_ids", shotIds.join(","));
    return this.request<SegmentStatusResponse>(`/minimax/director/segment_cache_status?${q.toString()}`);
  }

  /**
   * 下载单个镜头 mp4（GET /minimax/director/segment_mp4，V1.3 任务中心「下载 mp4」）。
   * 后端从磁盘段缓存编码（或直接返回已编码 mp4）；无缓存返回 404。
   * 返回 blob + 附件文件名（ShotNN.mp4），由调用方触发保存。
   */
  async segmentMp4(nodeId: string, index: number, fps?: number): Promise<{ blob: Blob; filename: string }> {
    const q = new URLSearchParams({ node_id: nodeId, index: String(index) });
    if (fps && Number.isFinite(fps)) q.set("fps", String(fps));
    const res = await fetch(`${this.baseUrl}/minimax/director/segment_mp4?${q.toString()}`);
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new ComfyApiError(
        `下载镜头 ${index + 1} mp4 失败：${res.status} ${body.slice(0, 200)}`,
        res.status,
        body,
      );
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") ?? "";
    const m = /filename="([^"]+)"/.exec(cd);
    const filename = m?.[1] ?? `Shot${String(index + 1).padStart(2, "0")}.mp4`;
    return { blob, filename };
  }

  // ---- V1.6-A 成片导出（POST /minimax/director/export，流式 scene/movie 合并）----

  /**
   * 导出整部影片（scope=movie）。shotIds = 全片拍平镜头 id（时间线顺序），
   * 后端按此顺序 ffmpeg 直拼各段缓存 mp4（低内存，不触发 #103 内存保护）。
   */
  async exportMovie(nodeId: string, shotIds: string[], opts: ExportOptions = {}): Promise<ExportResponse> {
    return this.request<ExportResponse>("/minimax/director/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        node_id: nodeId,
        scope: "movie",
        shot_ids: shotIds,
        fps: opts.fps,
        prefix: opts.prefix,
        skip_missing: opts.skipMissing,
      }),
    });
  }

  /** 导出当前场景（scope=scene）。shotIds = 该场景镜头 id（时间线顺序）。 */
  async exportScene(
    nodeId: string,
    sceneId: string,
    shotIds: string[],
    opts: ExportOptions = {},
  ): Promise<ExportResponse> {
    return this.request<ExportResponse>("/minimax/director/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        node_id: nodeId,
        scope: "scene",
        scene_id: sceneId,
        shot_ids: shotIds,
        fps: opts.fps,
        prefix: opts.prefix,
        skip_missing: opts.skipMissing,
      }),
    });
  }

  // ---------- MiniMax Studio 工程文件层（V1.1.1）----------

  /** 列出已保存项目 [{id,name,episodes,updatedAt}]。 */
  async listProjects(): Promise<ProjectSummary[]> {
    return this.request<ProjectSummary[]>("/minimax/director/projects");
  }

  /** 新建项目，返回项目摘要。 */
  async createProject(name: string): Promise<ProjectSummary> {
    return this.request<ProjectSummary>("/minimax/director/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  }

  /** 读 ProjectModel 原文。 */
  async loadProject(projectId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(`/minimax/director/project/${encodeURIComponent(projectId)}`);
  }

  /** 保存 ProjectModel。 */
  async saveProject(projectId: string, project: Record<string, unknown>): Promise<{ ok: boolean }> {
    return this.request<{ ok: boolean }>(`/minimax/director/project/${encodeURIComponent(projectId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(project),
    });
  }

  /** V1.7 Commit 3：剧本导入（规则拆 Scene/Shot + Qwen 语义补全 → ProductionPlan 审核预览）。 */
  async importScript(
    scriptText: string,
    opts: { title?: string; sourceFile?: string; analyze?: boolean } = {},
  ): Promise<ScriptImportResult> {
    return this.request<ScriptImportResult>("/minimax/director/script/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_text: scriptText,
        title: opts.title ?? "",
        source_file: opts.sourceFile ?? "",
        analyze: opts.analyze ?? true,
      }),
    });
  }

  /** P1-B-5（#534）：小说章节导入（自然语言零标记 → Qwen 整章理解 Beats → 规则拆 Shot →
   *  ProductionPlan(beats+timeline) 审核预览）。Qwen 失败自动降级规则（rule_only=true），不阻塞。
   *  P2-P4（#541）：opts.projectId + opts.episodeNumber（均可选）→ 后端对目标项目 ensure_bible +
   *  注入跨章上下文 + 更新 Bible，响应追加 bible + bible_updates（无 project_id 时与 P1-B 逐字一致）。 */
  async importStory(
    storyText: string,
    opts: {
      title?: string;
      sourceFile?: string;
      analyze?: boolean;
      projectId?: string;
      episodeNumber?: number;
    } = {},
  ): Promise<StoryImportResult> {
    return this.request<StoryImportResult>("/minimax/director/story/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        story_text: storyText,
        title: opts.title ?? "",
        source_file: opts.sourceFile ?? "",
        analyze: opts.analyze ?? true,
        ...(opts.projectId ? { project_id: opts.projectId } : {}),
        ...(opts.episodeNumber ? { episode_number: opts.episodeNumber } : {}),
      }),
    });
  }

  /** P2-P4（#541）：GET /minimax/director/bible?project_id=X → 项目级 Global Story Bible
   *  （不存在返回 bible=null）。前端 Bible 面板加载入口。 */
  async getBible(projectId: string): Promise<{ bible: StoryBibleJson | null }> {
    return this.request<{ bible: StoryBibleJson | null }>(
      `/minimax/director/bible?project_id=${encodeURIComponent(projectId)}`,
    );
  }

  /** P2-P4（#541）：POST /minimax/director/bible/confirm → 确认候选 / 采纳属性建议 /
   *  合并别名 / 编辑静态属性。写回 bible.json，返回更新后的全量 Bible。 */
  async bibleConfirm(
    projectId: string,
    entityId: string,
    action: "confirm" | "accept_attribute" | "merge_alias" | "edit_attributes",
    payload?: Record<string, unknown>,
  ): Promise<{ bible: StoryBibleJson }> {
    return this.request<{ bible: StoryBibleJson }>("/minimax/director/bible/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
        entity_id: entityId,
        action,
        ...(payload ? { payload } : {}),
      }),
    });
  }

  /** P2-P4（#541）：POST /minimax/director/bible/entry → 人工新建条目（source=user）。 */
  async bibleEntry(
    projectId: string,
    entityType: BibleEntryJson["entity_type"],
    name: string,
    opts: { attributes?: Record<string, string>; aliases?: string[] } = {},
  ): Promise<{ created: BibleEntryJson; bible: StoryBibleJson }> {
    return this.request<{ created: BibleEntryJson; bible: StoryBibleJson }>(
      "/minimax/director/bible/entry",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          entity_type: entityType,
          name,
          ...(opts.attributes && Object.keys(opts.attributes).length
            ? { attributes: opts.attributes }
            : {}),
          ...(opts.aliases?.length ? { aliases: opts.aliases } : {}),
        }),
      },
    );
  }

  /** V1.7 Phase 2：剧本实体 → 共享资产库匹配（纯规则，零显存/GPU；POST /minimax/director/assets/scan）。 */
  async scanAssets(plan: ProductionPlanJson): Promise<AssetScanResult> {
    return this.request<AssetScanResult>("/minimax/director/assets/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
  }

  /** V1.7 Phase 3（P0-C）：ProductionPlan → 结构化生产 Prompt 草稿（五区 + generation_mode 建议）。
   *  纯规则 + 模板 Registry，零显存/GPU（POST /minimax/director/prompt/draft）。 */
  async buildPromptDrafts(plan: ProductionPlanJson): Promise<PromptDraftResult> {
    return this.request<PromptDraftResult>("/minimax/director/prompt/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan }),
    });
  }

  /** P0-①（#400）：ProductionPlan → H3 Prompt（项目 Schema minimax-h3-project-v1）。
   *  三段 + 结构化 references + 段落级来源标记（POST /minimax/director/prompt/h3，纯规则零显存）。
   *  bindingsByShot = {scene_id:shot_id: {entity_key: {asset_id, image_file}}}，refs 绑定以结构化数组为准。
   *  durationByShot = {scene_id:shot_id: 秒}，进 [0-Ns] 时间轴前缀；缺省后端按 5s。
   *  overridesByShot（P0-A #479）= {scene_id:shot_id: {visual?, cameraText?, style?, soundText?}}：
   *  Workbench 五区编辑 → DirectorIntent 覆盖（visual→composition / cameraText→camera_desc /
   *  style→style / soundText→audio.ambient），原文事实不可覆盖；空分区不写。
   *  styleProfile（v2.0 P3）：Visual Style 预设完整 dict（Project.styleProfile）；非空才传，
   *  后端 _build_description 注入画风块；undefined/空 → 旧链路逐字节不变。 */
  async buildH3Prompts(
    plan: ProductionPlanJson,
    bindingsByShot?: Record<string, Record<string, { asset_id: string; image_file: string }>>,
    durationByShot?: Record<string, number>,
    overridesByShot?: Record<string, { visual?: string; cameraText?: string; style?: string; soundText?: string }>,
    styleProfile?: VisualStyleProfileJson,
  ): Promise<H3PromptResult> {
    return this.request<H3PromptResult>("/minimax/director/prompt/h3", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plan,
        ...(bindingsByShot ? { bindings_by_shot: bindingsByShot } : {}),
        ...(durationByShot ? { duration_by_shot: durationByShot } : {}),
        ...(overridesByShot ? { overrides_by_shot: overridesByShot } : {}),
        ...(styleProfile ? { style_profile: styleProfile } : {}),
      }),
    });
  }

  /** v2.0 P3（#631+）：拉取 Visual Style 预设库（GET /minimax/director/style/presets）。
   *  Visual Style 面板下拉数据源（方案 C 单一事实来源，前端不硬编码 7 预设副本）；
   *  选中完整 dict 存 project.styleProfile，生成时透传 buildH3Prompts 第 5 参。纯规则零显存。 */
  async fetchStylePresets(): Promise<StylePresetsResult> {
    return this.request<StylePresetsResult>("/minimax/director/style/presets");
  }

  /** V1.7 Phase 5（P1-D）：拉取运镜模板库（GET /minimax/director/camera/templates）。
   *  Workbench 摄影分区下拉用；纯规则零显存。 */
  async fetchCameraTemplates(): Promise<CameraTemplateList> {
    return this.request<CameraTemplateList>("/minimax/director/camera/templates");
  }

  /** Phase 0（#574）：可用 TTS 音色列表（GET /minimax/director/tts/voices）。
   *  Voice Cast 面板数据源；失败抛 ComfyApiError。 */
  async listTtsVoices(): Promise<TtsVoiceListResult> {
    return this.request<TtsVoiceListResult>("/minimax/director/tts/voices");
  }

  /** Phase 0（#574）：单镜 TTS 合成落盘（POST /minimax/director/tts/synthesize）。
   *  失败（400/500）抛 ComfyApiError，body 含后端 error（edge_tts 缺失/合成失败等）。 */
  async synthesizeTts(payload: TtsSynthesizePayload): Promise<TtsSynthesizeResult> {
    return this.request<TtsSynthesizeResult>("/minimax/director/tts/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  /** Phase 1（#574）：FFmpeg ducking 混音成片（POST /minimax/director/tts/mix）。 */
  async mixTts(payload: TtsMixPayload): Promise<TtsMixResult> {
    return this.request<TtsMixResult>("/minimax/director/tts/mix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  /** V1.7 Phase 2-1（#135）：人工确认落盘（POST /minimax/director/assets/binding）。
   *  把 `entity_key → asset_id`（status=accepted, source=user）写入 asset_registry.json。
   *  下一次重新解析同一剧本时 scan 直接复用，不再重新 pending。纯规则零显存。 */
  async saveAssetBinding(
    payload: AssetBindingPayload,
  ): Promise<{ ok: boolean; binding: AssetBinding }> {
    return this.request<{ ok: boolean; binding: AssetBinding }>(
      "/minimax/director/assets/binding",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  /** V1.7 Phase 2-1（#135）：拉取全部已持久化确认（GET /minimax/director/assets/bindings）。
   *  重新导入剧本时调用，把已确认实体直接回显「已确认」复用，不重新 pending。 */
  async fetchAssetBindings(): Promise<{ bindings: Record<string, AssetBinding> }> {
    return this.request<{ bindings: Record<string, AssetBinding> }>(
      "/minimax/director/assets/bindings",
    );
  }

  /** 删除项目。 */
  async deleteProject(projectId: string): Promise<{ ok: boolean }> {
    return this.request<{ ok: boolean }>(`/minimax/director/project/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    });
  }

  /** 删除快照。 */
  async deleteSnapshot(snapshotId: string): Promise<{ ok: boolean }> {
    return this.request<{ ok: boolean }>(`/minimax/director/snapshot/${encodeURIComponent(snapshotId)}`, {
      method: "DELETE",
    });
  }

  /** V1.11：生成完成后把当次输出 mp4 归档到项目 generations 目录。
   *  目录结构：{input}/minimax_studio/generations/{projectId}/{shotId}/{generationId}.mp4。
   *  subfolder：输出文件在 output 目录下的子目录（如 H3 的 "video"），可空。
   *  返回 video_file（input 相对路径），前端经 comfyInputUrl 播放（重启不丢）。 */
  async archiveGeneration(payload: {
    projectId: string;
    shotId: string;
    generationId: string;
    filename: string;
    subfolder?: string;
  }): Promise<{ ok: boolean; video_file: string }> {
    return this.request<{ ok: boolean; video_file: string }>("/minimax/director/generations/archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: payload.projectId,
        shot_id: payload.shotId,
        generation_id: payload.generationId,
        filename: payload.filename,
        subfolder: payload.subfolder ?? "",
      }),
    });
  }

  /** 列出生成时自动快照（「导入 ComfyUI 工程」数据源）。 */
  async listSnapshots(limit = 50): Promise<SnapshotSummary[]> {
    return this.request<SnapshotSummary[]>(`/minimax/director/snapshots?limit=${limit}`);
  }

  /** 读某次快照的 timeline_data 原文。 */
  async loadSnapshot(snapshotId: string): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>(`/minimax/director/snapshot/${encodeURIComponent(snapshotId)}`);
  }

  /**
   * 上传一个参考媒体到 ComfyUI input 目录（V1.2.5 图片 / V1.2.6 音频+视频）。
   *
   * 端点：kind=audio → POST /upload/audio（multipart field: audio）；
   *      kind=image|video → POST /upload/image（multipart field: image）。
   * ComfyUI 自带同名冲突处理：overwrite=false 时若文件名已存在，自动在 stem 后追加 _1/_2/…（不覆盖）。
   * 用 XMLHttpRequest 以便拿到上传进度（fetch 无 upload 进度事件）。
   */
  uploadFile(file: File, opts: UploadMediaOptions = {}): Promise<UploadedMedia> {
    const kind = opts.kind ?? "image";
    const isAudio = kind === "audio";
    const endpoint = isAudio ? "/upload/audio" : "/upload/image";
    const field = isAudio ? "audio" : "image";
    const label = kind === "image" ? "图片" : kind === "audio" ? "音频" : "视频";
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append(field, file);
      form.append("overwrite", opts.overwrite ? "true" : "false");
      form.append("type", "input");
      if (opts.subfolder) form.append("subfolder", opts.subfolder);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${this.baseUrl}${endpoint}`);
      xhr.responseType = "json";
      xhr.timeout = opts.timeoutMs ?? (isAudio ? 120_000 : 600_000);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) opts.onProgress?.(e.loaded, e.total);
      };
      xhr.onerror = () =>
        reject(new ComfyApiError(`无法连接 ComfyUI（${this.baseUrl || "(当前源)"}）：上传${label}失败`));
      xhr.ontimeout = () => reject(new ComfyApiError(`上传${label}超时`));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const data = xhr.response as UploadedMedia | null;
          if (data && typeof data.name === "string" && data.name) {
            resolve(data);
          } else {
            reject(new ComfyApiError(`ComfyUI ${endpoint} 返回格式异常`));
          }
        } else {
          let body = "";
          try {
            body = xhr.responseText ?? "";
          } catch {
            /* responseType=json 下部分浏览器读 responseText 会抛 */
          }
          reject(new ComfyApiError(`ComfyUI ${endpoint} 请求失败：${xhr.status} ${xhr.statusText}`, xhr.status, body));
        }
      };
      xhr.send(form);
    });
  }

  /** 上传参考图（V1.2.5 兼容入口，等价 uploadFile(kind=image)）。 */
  uploadImage(file: File, opts: UploadImageOptions = {}): Promise<UploadedImage> {
    return this.uploadFile(file, { ...opts, kind: "image" });
  }
}

/** 上传选项：subfolder=input 内子目录；overwrite=false 同名自动加后缀。 */
export interface UploadImageOptions {
  subfolder?: string;
  /** true=同名覆盖；false=同名自动加 _1/_2 后缀（默认，不丢旧文件）。 */
  overwrite?: boolean;
  onProgress?: (loaded: number, total: number) => void;
  timeoutMs?: number;
}

/** 通用上传选项：kind 决定端点与 multipart 字段。 */
export interface UploadMediaOptions extends UploadImageOptions {
  /** image → /upload/image（field image）；audio → /upload/audio（field audio）；video → /upload/image（field image）。 */
  kind?: "image" | "audio" | "video";
}

/** 上传成功返回体（/upload/image 与 /upload/audio 同构）。 */
export interface UploadedMedia {
  name: string;
  subfolder: string;
  type: "input" | "temp";
}

/** 图片上传返回体（兼容别名）。 */
export type UploadedImage = UploadedMedia;

/** 上传结果 → ComfyUI input 目录相对路径（存进 Asset.imageFile / refs 的真相格式）。 */
export function uploadedRelPath(u: UploadedMedia): string {
  return u.subfolder ? `${u.subfolder}/${u.name}` : u.name;
}

/**
 * input 目录相对路径 → 浏览器 /view 预览 URL（图片/视频/音频通用）。
 * relPath 形如 "minimax_studio/assets/xxx.png" 或 "xxx.png"。
 * ComfyUI /view 的 filename 需要拆出 name，子目录单独放 subfolder 参数。
 */
export function comfyInputUrl(relPath: string): string {
  if (!relPath) return "";
  const norm = relPath.replace(/\\/g, "/");
  const idx = norm.lastIndexOf("/");
  const filename = idx >= 0 ? norm.slice(idx + 1) : norm;
  const subfolder = idx >= 0 ? norm.slice(0, idx) : "";
  const q = new URLSearchParams({ filename, subfolder, type: "input" });
  return `/view?${q.toString()}`;
}

/** 参考媒体预览 URL（语义别名，底层同 comfyInputUrl）。 */
export function comfyMediaUrl(relPath: string): string {
  return comfyInputUrl(relPath);
}

/** output 目录相对路径 → 浏览器 /view 预览 URL（type=output）。
 *  Phase 2-E（#577）：混音成片 final.mp4 落在 output/minimax_studio/projects/... 下，
 *  播放器需要 type=output 才能取到。 */
export function comfyOutputUrl(relPath: string): string {
  if (!relPath) return "";
  const norm = relPath.replace(/\\/g, "/");
  const idx = norm.lastIndexOf("/");
  const filename = idx >= 0 ? norm.slice(idx + 1) : norm;
  const subfolder = idx >= 0 ? norm.slice(0, idx) : "";
  const q = new URLSearchParams({ filename, subfolder, type: "output" });
  return `/view?${q.toString()}`;
}

/** 项目列表项（后端 project_store.list_projects 输出）。 */
export interface ProjectSummary {
  id: string;
  name: string;
  episodes: number;
  updatedAt: string;
}

/** V1.7 Commit 3：POST /minimax/director/script/import 响应。 */
export interface ScriptImportResult {
  plan: ProductionPlanJson;
  /** true = 只走了规则、无 Qwen 补全（Qwen 失败自动降级，不阻塞导入）。 */
  rule_only: boolean;
  warnings: string[];
}

/** P1-B-5（#534）：POST /minimax/director/story/import 响应。 */
export interface StoryImportResult {
  plan: ProductionPlanJson;
  /** true = 只走了规则、无 Qwen 整章理解（Qwen 不可用/失败自动降级，不阻塞导入）。
   *  此时 beats[].source="rule"，warnings 明示「需人工检查」。 */
  rule_only: boolean;
  warnings: string[];
  /** P2-P4（#541）：带 project_id 导入时追加——更新后的项目级全量 Bible
   *  （前端 Bible 面板 / 上下文预览用）。无 project_id 时缺省（与 P1-B 一致）。 */
  bible?: StoryBibleJson | null;
  /** P2-P4（#541）：本次导入对 Bible 的变更列表（新增候选/复用/状态变化），
   *  供导入弹窗展示「本次导入对 Bible 的变更」。 */
  bible_updates?: BibleUpdate[];
}

/** P2-P1（#538）：BibleEntry.to_dict 形状（character | location | prop | event）。 */
export interface BibleEntryJson {
  entity_id: string;
  entity_type: "character" | "location" | "prop" | "event";
  name: string;
  aliases: string[];
  /** candidate=候选（待人工确认）/ confirmed=已确认（静态属性锁定）。 */
  status: "candidate" | "confirmed";
  /** ai=AI 提出 / rule=规则提取 / user=人工录入或编辑。 */
  source: "ai" | "rule" | "user";
  /** 静态 facts：确认后锁定，AI 永不覆盖。character:{appearance,personality}
   *  location:{environment,style,props} prop:{owner,purpose,appearance}。 */
  attributes: Record<string, string>;
  /** AI 对已确认条目提出的新属性候选（面板逐条采纳/忽略）。 */
  attribute_suggestions: { key: string; value: string }[];
  /** 当前状态（沈青崖：重伤未愈；剑匣：在林雪手中）。 */
  current_status: string;
  /** 最后出现集（ep3）。 */
  last_seen: string;
  /** 已发生剧情（"ep2：沈青崖取得剑匣"），尾部追加。 */
  history: string[];
  /** 首见集（ep1）。 */
  first_seen: string;
  /** character 当前持有的道具 entity_id（动态）。 */
  props_held: string[];
  /** 人物关系候选（"沈青崖-林雪：故交"），面板可确认。 */
  relationships: string[];
  /** 关联 asset_registry 的 entity_key（可空）。 */
  asset_key: string;
}

/** P2-P1（#538）：StoryBible.to_dict 形状（项目级跨章世界状态库）。 */
export interface StoryBibleJson {
  bible_id: string;
  project_id: string;
  entries: BibleEntryJson[];
  /** 每次导入 +1（前端判断「有新变更」）。 */
  version: number;
  updated_at: string;
}

/** P2-P2（#539）：本次导入对 Bible 的变更列表项。 */
export interface BibleUpdate {
  kind: "new_candidate" | "reused" | "status_change" | "alias_merge";
  entity_id: string;
  name: string;
  message: string;
}

/** V1.7 Phase 2：POST /minimax/director/assets/scan 响应（剧本实体 → 共享资产库匹配）。 */
export interface AssetScanResult {
  /** 共享资产库列表（minimax_studio/assets/ 平铺图片，供前端缩略图）。 */
  assets: AssetLibraryItem[];
  /** 剧本实体匹配结果（按剧本出现顺序）。 */
  matches: AssetMatch[];
  /** V1.7 Phase 1.1：Task B 视觉元素（环境/氛围/光线/天气等，只进 Prompt，永不进资产匹配）。 */
  visual_elements: VisualElement[];
  /** 实体抽取加固漏斗（#357）：AI 发现 → 清洗 → 进入匹配 → auto/pending/none。 */
  funnel: {
    /** Qwen 提取到的实体总数。 */
    discovered: number;
    /** 伪实体丢弃（镜头一 / 第 3 镜 等）。 */
    invalid_dropped: number;
    /** canonical 去重丢弃数。 */
    dedup_dropped: number;
    /** 规则补位种子数（场景地点/角色兜底）。 */
    seeded: number;
    /** 清洗后注册表实体数。 */
    cleansed: number;
    /** 进入资产匹配的实体数（source=script 且置信度达标）。 */
    entered: number;
    /** 无需资产匹配数（asset_requirement=none：环境/特效，只进 Prompt）。 */
    visual_only: number;
    /** 未进入匹配数（inferred / 低置信）。 */
    excluded: number;
    /** 匹配结果：自动绑定数。 */
    auto: number;
    /** 匹配结果：待人工确认数。 */
    pending: number;
    /** 匹配结果：无候选数。 */
    none: number;
  };
}

/** V1.7 Phase 1.1：Task B 视觉元素（name/type/confidence；只进 Prompt 不进资产匹配）。 */
export interface VisualElement {
  name: string;
  type: string;
  confidence: number;
}

/** V1.7 Phase 3（P0-C）：POST /minimax/director/prompt/draft 响应（结构化生产 Prompt 草稿）。 */
export interface PromptDraftResult {
  /** Prompt Template Registry 版本（h3-v1）。 */
  template_version: string;
  /** 每镜一份草稿（按剧本出现顺序）。 */
  drafts: PromptDraftItem[];
  /** P0-①（#400）：每镜 DirectorIntent（「AI 理解」层；字段级 provenance 来源标记）。
   *  key = `${scene_id}:${shot_id}`，与 drafts 同序遍历。 */
  intents?: Record<string, DirectorIntentDto>;
}

/** P0-①（#400）：单镜 DirectorIntent 投影（后端 DirectorIntent.to_dict）。
 *  provenance 值 ∈ user/rule/ai/mix（IntentSource）。三层可追溯的「AI 理解」层。 */
export interface DirectorIntentDto {
  shot_id: string;
  scene_id: string;
  /** 剧本原文逐字保底（任何阶段不得覆盖）。 */
  user_original_intent: string;
  /** 规则从原文抽的导演事实（逐字保序）。 */
  retained_facts: string[];
  subject: string;
  characters: string[];
  location: string;
  action: string[];
  emotion: string;
  composition: string;
  camera_position: string;
  movement: string;
  lighting: string;
  environment: string[];
  continuity: {
    has_prev: boolean;
    is_last_shot: boolean;
    camera_intent: string;
    camera_template: string;
    camera_desc: string;
  };
  audio: {
    ambient: string;
    dialogue: { speaker: string; text: string }[];
    music: string;
    provenance: Record<string, string>;
  };
  entities: { name: string; type: string; confidence: number; source: string }[];
  /** 字段级来源标记（user_original_intent=user / camera_position=rule / emotion=ai / subject=mix …）。 */
  provenance: Record<string, string>;
}

/** V1.7 Phase 5（P1-D）：运镜模板库条目（GET /minimax/director/camera/templates 返回项）。
 *  template 含 {location}/{time}/{subject}/{emotion}/{object} 占位符，前端填充后进 cameraText。 */
export interface CameraTemplate {
  id: string;
  name: string;
  intent: string;
  template: string;
}

/** 运镜模板库响应（后端 list_camera_templates + template_version=camera-v1）。 */
export interface CameraTemplateList {
  templates: CameraTemplate[];
  template_version: string;
}

/** 单镜 AI Draft：五区中文草稿 + generation_mode 制作计划建议。 */
export interface PromptDraftItem {
  scene_id: string;
  shot_id: string;
  /** 制作计划建议（t2v/r2v/fl2v）；仅建议，SPA Shot.taskType 保持 auto。 */
  generation_mode: "t2v" | "r2v" | "fl2v";
  /** V1.7 Phase 5：命中该镜的运镜模板 id（walk/dialogue/…；纯规则状态机选出）。
   *  Workbench 摄影分区下拉回显；空 = 未走模板（use_camera_planner=False 降级）。 */
  camera_template?: string;
  /** 五区中文草稿（与剧本语言一致，供人工审核后编辑）。 */
  draft: {
    visual: string;
    camera: string;
    style: string;
    sound: string;
    negative: string;
    camera_intent: string;
  };
}

/** P0-①（#400）：结构化参考图绑定（entity_key → asset_id → image_file）。
 *  与 Prompt 中 [REF: 名] 可读标签一一对应，但绑定以本数组为准（不依赖文本顺序）。 */
export interface H3PromptReference {
  entity_key: string;
  entity_name: string;
  asset_id: string;
  image_file: string;
  ref_image: string;
}

/** P0-①（#400）：单镜 H3 Prompt（项目 Schema minimax-h3-project-v1，POST /prompt/h3 响应项）。 */
export interface H3PromptItem {
  schema: string;
  shot_id: string;
  scene_id: string;
  duration_sec: number;
  integrated_multimodal_description: string;
  overall_soundscape: string;
  non_diegetic_music: string;
  references: H3PromptReference[];
  timeline: { start: number; end: number; label: string }[];
  /** 段落级来源标记：{integrated_multimodal_description/…: {user_facts, ai_supplement, rule_generated}}。 */
  provenance: Record<
    string,
    { user_facts: string[]; ai_supplement: string[]; rule_generated: string[] }
  >;
}

/** P0-①（#400）：POST /prompt/h3 响应。prompts key = `${scene_id}:${shot_id}`。 */
export interface H3PromptResult {
  schema: string;
  prompts: Record<string, H3PromptItem>;
}

/** v2.0 P3（#631+）：Visual Style Profile（全剧级视觉基底，与后端
 *  VisualStyleProfile.to_dict 八字段对齐）。存于 Project.styleProfile；
 *  后端 style_block 需要完整字段渲染画风块，前端不硬编码 7 预设副本
 *  （GET /style/presets 单一事实来源拉取 → 选中完整 dict 落盘）。 */
export interface VisualStyleProfileJson {
  profile_id: string;
  name: string;
  art_direction: string;
  style_keywords: Record<string, string>;
  fidelity: string;
  color_tone: string;
  negative_rules: string[];
  per_scene: Record<string, string>;
}

/** v2.0 P3（#631+）：GET /minimax/director/style/presets 响应（7 预设完整 dict）。 */
export interface StylePresetsResult {
  presets: Record<string, VisualStyleProfileJson>;
  preset_version: string;
}

/** 共享资产库条目（{ComfyUI input}/minimax_studio/assets/ 平铺图片）。 */
export interface AssetLibraryItem {
  name: string;
  image_file: string;
  /** Phase 2-1（#135）：永久资产 id（asset_xxx，同一 image_file 稳定不变）；无 registry 时缺失。 */
  asset_id?: string;
  /** 资产 kind（cast/location/prop/unknown；前缀推断 + 实体名逆向锚定补齐）。 */
  kind?: string;
}

/** 实体 → 资产匹配结果。kind ∈ cast/prop/location；status ∈ auto/pending/none。 */
export interface AssetMatch {
  kind: "cast" | "prop" | "location";
  /** 剧本实体名。 */
  name: string;
  /** auto=高置信度自动绑 / pending=低置信度待人工确认 / none=无候选。 */
  status: "auto" | "pending" | "none";
  /** true 仅 auto（pending 由前端确认后置 true）。 */
  matched: boolean;
  asset_name: string | null;
  image_file: string | null;
  confidence: number;
  /** 低置信度候选（最多 5 条），供人工确认。 */
  candidates: { name: string; image_file: string; score: number }[];
  /** 实体抽取加固（#357）：实体类型（character/location/prop/costume/effect/…）。 */
  entity_type: string;
  /** 实体来源（script=剧本明确 / inferred=视觉推断，inferred 不进匹配）。 */
  entity_source: "script" | "inferred";
  /** 实体置信度（0~1；<0.60 不进匹配，0.60~0.85 只能 pending）。 */
  entity_confidence: number;
  /** 实体唯一 id（ent_xxx，跨镜头去重后稳定）。 */
  entity_id: string;
  /** 实体是否允许自动绑定（置信度 >=0.85 才 true；false 时精确命中也被压到 pending）。 */
  can_auto: boolean;
  /** V1.7 Phase 1.1：资产需求分级（required=角色/地点必须匹配 / recommended=道具建议匹配 / none=只进 Prompt）。 */
  asset_requirement: "required" | "recommended" | "none";
  /** V1.7 Phase 1.1：子串匹配的语义判定（exact/normalized/contains/location_hierarchy/generic_reference/semantic_incompatible/none）。 */
  match_kind: string;
  /** V1.7 Phase 1.1：match_kind ∈ {location_hierarchy, generic_reference} → true，前端显示「⚠ 建议参考 [接受][重新选择]」。 */
  suggest: boolean;
  /** V1.7 Phase 2-1（#135）：稳定实体键 `{清洗后类型}:{canonical_name}`（如 character:柳如烟），
   *  人工确认落盘 / 重新导入复用 的主键。 */
  entity_key: string;
  /** V1.7 Phase 2-1（#135）：永久资产 id（asset_xxx，命中资产才有；未匹配/无 registry 为 null）。 */
  asset_id: string | null;
  /** V1.7 Phase 2-1（#135）：该实体是否有已落盘 binding（accepted=用户确认过 → 本次 match_kind=persisted）。 */
  binding_status?: "accepted" | "auto" | null;
  /** V1.7 Phase 2-1（#135）：binding 来源（user=人工确认 / system=自动匹配落盘）。 */
  binding_source?: "user" | "system" | null;
}

/** V1.7 Phase 2-1（#135）：已持久化的人工确认（GET /assets/bindings 返回项 / POST 落盘结果）。 */
export interface AssetBinding {
  /** 稳定实体键（character:柳如烟 / location:山雨楼外 / prop:旧剑匣）。 */
  entity_key: string;
  /** 永久资产 id（asset_xxx，绑定 image_file）。 */
  asset_id: string;
  /** accepted=用户确认 / auto=系统自动。 */
  status: "accepted" | "auto";
  /** user=人工确认 / system=自动。 */
  source: "user" | "system";
  updated_at?: string;
  /** 资产快照（binding_with_asset 透出）。 */
  image_file?: string;
  asset_name?: string;
  asset_kind?: string;
}

/** V1.7 Phase 2-1（#135）：POST /assets/binding 请求体。 */
export interface AssetBindingPayload {
  /** 稳定实体键（必填）。 */
  entity_key: string;
  /** 永久资产 id（来自 scan 的 matches[i].asset_id；有则直接写）。 */
  asset_id?: string;
  /** 资产相对路径（asset_id 缺失时按此分配/复用）。 */
  image_file?: string;
  /** 实体显示名（可选，新建资产记录快照用）。 */
  entity_name?: string;
  /** 实体类型（可选，character/location/prop）。 */
  entity_type?: string;
  /** 资产名（可选，资产记录快照）。 */
  asset_name?: string;
  /** accepted（默认）/ auto。 */
  status?: "accepted" | "auto";
  /** user（默认）/ system。 */
  source?: "user" | "system";
}

/** ProductionPlan JSON 形状（后端 director/production_plan.py to_dict）。 */
export interface ProductionPlanJson {
  project: { title: string; source_file: string };
  scenes: {
    scene_id: string;
    title: string;
    location_name: string;
    time: string;
    weather: string;
    shots: {
      shot_id: string;
      source_text: string;
      duration_sec: number;
      characters: { name: string; role: string }[];
      props: { name: string }[];
      actions: string[];
      emotion: string;
      /** Phase 2（#573）：Dialogue 扩展声音字段（后端 Dialogue dataclass 6 字段）。
       *  speaker/text 为剧本原文；type/voice_id/emotion/delivery 为 TTS 配音层
       *  （前端行级微调写回，空值不写）。voice_id 显式 > Voice Cast 全局 > 规则兜底。 */
      dialogue: {
        speaker: string;
        text: string;
        type?: "character_dialogue" | "narration" | "inner_monologue" | "system_voice";
        voice_id?: string;
        emotion?: string;
        delivery?: string;
      }[];
      visual_intent: string;
      /** V1.7 Phase 1.1 实体抽取：typed + confidence + asset_requirement（后端真实 to_dict 输出）。
       *  仅进导入弹窗漏斗/分组展示；不消费于 castIds 绑定（绑定走 characters 列表）。 */
      entities?: {
        entity_id: string;
        name: string;
        type: string;
        source: "script" | "inferred";
        confidence: number;
        asset_requirement: "required" | "recommended" | "none";
        aliases: string[];
      }[];
      /** V1.7 Phase 1.1 视觉推断元素（环境/特效等，asset_requirement=none，只进 Prompt）。 */
      visual_elements?: { name: string; type: string; confidence: number }[];
    }[];
  }[];
  validation: { status: string; errors: string[]; warnings: string[] };
  /** P0 Story Timeline（2026-08-14 用户拍板）：播放顺序数组，元素 `"{scene_id}:{shot_id}"`，
   *  与场景树解耦（同一 Scene 可被多次交叉引用）。缺省 = 场景树顺序（向后兼容）。 */
  timeline?: string[];
  /** P1-B 剧情结构层（2026-08-14 用户拍板）：Story Beat 列表（自然语言小说导入时产出）。
   *  元数据层，不参与结构校验，不决定播放顺序（timeline 才决定）。 */
  beats?: StoryBeatJson[];
}

/** P1-B StoryBeat（与后端 production_plan.StoryBeat.to_dict 对齐，12 字段）。
 *  Beat 描述「这段剧情为什么存在、讲了什么」；dramatic_function 是导演调度输入。 */
export interface StoryBeatJson {
  beat_id: string;
  title: string;
  summary: string;
  dramatic_function: string;
  scene_id: string;
  time: string;
  weather: string;
  /** 本 Beat 覆盖的小说原文片段（逐字，覆盖校验的源）。 */
  text_segments: string[];
  order: number;
  /** 本 Beat 对应的镜头（"{scene_id}:{shot_id}"）。 */
  entries: string[];
  /** ai | rule（Qwen 失败降级为 rule）。 */
  source: "ai" | "rule";
  /** AI 候选：为什么切到这里（仅审核参考）。 */
  transition_reason: string;
}

/** 快照列表项（后端 project_store.list_snapshots 输出）。 */
export interface SnapshotSummary {
  id: string;
  name: string;
  time: string;
  timelineMode: string;
  segments: number;
  scenes: number;
}

/** 导出请求选项（V1.6-A，POST /minimax/director/export）。 */
export interface ExportOptions {
  /** 单镜编码帧率，默认 24。 */
  fps?: number;
  /** 输出文件名前缀，默认 MiniMax_Studio_Movie / MiniMax_Studio_Scene。 */
  prefix?: string;
  /** true=跳过缺失镜头只导出已有的；false=任一缺失即报 not_cached（默认）。 */
  skipMissing?: boolean;
}

/** 缺失镜头条目（后端 not_cached 返回）。 */
export interface ExportMissingShot {
  shotId: string;
  order: number;
}

// ---------- Phase 0/1 TTS（#574）----------

/** 可用音色描述（后端 tts_engine.VoiceInfo.to_dict）。
 *  voice_id 为语义 ID（voice_narrator / voice_柳如烟）或引擎原生名（zh-CN-XiaoxiaoNeural）。 */
export interface TtsVoiceInfo {
  voice_id: string;
  name: string;
  language: string;
  gender: string;
  engine: string;
}

/** GET /minimax/director/tts/voices 响应。 */
export interface TtsVoiceListResult {
  ok: boolean;
  engine?: string;
  voices?: TtsVoiceInfo[];
  error?: string;
}

/** POST /minimax/director/tts/synthesize 请求体。 */
export interface TtsSynthesizePayload {
  /** 项目名（目录名，含中文保留）。 */
  project_name: string;
  /** 镜头 id（如 shot_001）。 */
  shot_id: string;
  scene_id?: string;
  ambient?: string;
  music?: string;
  /** 逐行台词（voice_id 已由前端按 Voice Cast 解析）。 */
  lines?: TtsLineInput[];
}

/** TTS 台词行（后端 synthesize 直接消费）。 */
export interface TtsLineInput {
  text: string;
  speaker?: string;
  voice_type?: string;
  voice_id?: string;
  emotion?: string;
  delivery?: string;
}

/** POST /minimax/director/tts/synthesize 成功响应（失败抛 ComfyApiError，body 为 error）。 */
export interface TtsSynthesizeResult {
  ok: boolean;
  shot_dir?: string;
  tts_dir?: string;
  manifest_file?: string;
  line_count?: number;
  error?: string;
}

/** POST /minimax/director/tts/mix 请求体。 */
export interface TtsMixPayload {
  project_name: string;
  shot_id: string;
  mode?: "h3_tts" | "h3_only";
  /** Phase 2-E（#577）：H3 生成成片的定位信息（onVideo 的 ref）。
   *  shot 分层目录缺 video.mp4 时，后端先从 output 拷入再混音（幂等）。 */
  video_filename?: string;
  video_subfolder?: string;
}

/** POST /minimax/director/tts/mix 成功响应（失败抛 ComfyApiError，body 为 error）。 */
export interface TtsMixResult {
  ok: boolean;
  mode?: string;
  shot_dir?: string;
  video_file?: string;
  h3_audio_file?: string;
  final_file?: string;
  /** Phase 2-E（#577）：final.mp4 相对 output 根的路径（如 minimax_studio/projects/.../final.mp4），
   *  播放器经 comfyOutputUrl 转 /view?type=output 直接播放。 */
  final_rel?: string;
  line_count?: number;
  lines?: { index: number; start_sec?: number; end_sec?: number; duration_sec?: number }[];
  error?: string;
}

/**
 * 导出响应（后端 /minimax/director/export 输出）。
 * 成功：path/filename/subfolder/type/scope/segments/missing；
 * 业务失败：error 字段（not_cached | nothing_to_export | ffmpeg_missing | invalid_cache）。
 */
export interface ExportResponse {
  path?: string;
  filename?: string;
  subfolder?: string;
  type?: string;
  scope?: "movie" | "scene";
  sceneId?: string;
  segments?: number;
  missing?: ExportMissingShot[];
  error?: string;
  message?: string;
}
