/**
 * 生成调度服务：TimelineStructure + Adapter → 提交 ComfyUI → 订阅 WS 进度。
 *
 * 这是 V1.0 要验证的核心链路：
 *   SPA → /prompt（携带 timeline_data）→ MiniMaxH3Director → H3 → 视频 → 进度回传
 *
 * 单槽问题缓解：保持唯一活跃 WS 连接；进度以 WS 为主，状态灯用 /segment_status 轮询兜底。
 */

import { ComfyApiClient, comfyViewUrl, extractVideosFromUi, firstVideoFromHistory, isVideoFile, type ComfyVideoRef, type HistoryItem } from "@/services/comfyApi";
import { ComfyWsClient, type WsEvent } from "@/services/comfyWs";
import type { DirectorAdapter } from "@/adapters/directorAdapter";
import type { TimelineStructure } from "@/models/timeline";

/** 失败诊断信息（V1.4-P0）：ComfyUI execution_error 携带的节点级错误。 */
export interface RunFinishInfo {
  nodeId?: string;
  errorDetail?: string;
  traceback?: string;
}

export interface RunCallbacks {
  onProgress?: (ev: ReturnType<DirectorAdapter["parseProgressEvent"]>) => void;
  onPreview?: (ev: ReturnType<DirectorAdapter["parsePreviewEvent"]>) => void;
  onFinish?: (promptId: string, ok: boolean, info?: RunFinishInfo) => void;
  /** 失败后异步取回的后端 report（Director 节点 STRING 输出，best-effort）。 */
  onReport?: (promptId: string, report: string) => void;
  /** 成片产出（SaveVideo 节点保存完成后）。url 可直接喂给 <video src>。 */
  onVideo?: (video: { url: string; ref: ComfyVideoRef }) => void;
  /** 兜底轮询每 tick 回调（WS 断开时保持 UI 状态灯/任务状态更新）。 */
  onPoll?: (info: { promptId: string; status: "queued" | "running" | "missing" }) => void;
}

/** 兜底轮询间隔（ms）。WS 正常时 execution_success/error 先到，轮询几乎不消耗。 */
const POLL_INTERVAL_MS = 3000;
/**
 * 最大轮询 tick。这是「任务彻底失联」的硬兜底上限，不是任务时长上限——
 * 达到上限后仍会先查 /history + /queue 确认任务是否还在跑，还在跑就重置继续等。
 * 成片模式（exportMode=all 整条时间线合并）可能远超 15 分钟，固定 tick 判失败会误杀长任务。
 * 2400 × 3s = 2 小时硬上限，防永久 running。
 */
const MAX_POLL_TICKS = 2400;

export class DirectorRunService {
  readonly api: ComfyApiClient;
  readonly ws: ComfyWsClient;
  private adapter: DirectorAdapter;
  private clientId = crypto.randomUUID();

  constructor(adapter: DirectorAdapter, api?: ComfyApiClient, wsUrl?: string, onWsStatus?: (connected: boolean) => void) {
    this.adapter = adapter;
    this.api = api ?? new ComfyApiClient();
    this.ws = new ComfyWsClient(wsUrl ?? "/ws", {
      onEvent: (ev) => this.handleWsEvent(ev),
      // V1.4-P1：WS 连接状态（open→true / close|disconnect→false）转发给上层（store 四态映射）。
      onStatus: onWsStatus ?? (() => undefined),
    });
  }

  setAdapter(adapter: DirectorAdapter): void {
    this.adapter = adapter;
  }

  async connect(): Promise<void> {
    await this.api.assertDirectorNode();
    this.ws.connect(this.clientId);
  }

  disconnect(): void {
    this.ws.disconnect();
  }

  /**
   * 提交一次生成。workflow 是完整 ComfyUI 图（含 Director 节点）；
   * timeline_data 由外部用 adapter.buildTimelineData() 得到后注入 node inputs。
   */
  async run(
    structure: TimelineStructure,
    workflow: Record<string, unknown>,
    nodeId: string,
    callbacks: RunCallbacks = {},
  ): Promise<string> {
    const { timelineData } = this.adapter.buildTimelineData(structure);

    // 顶层净化：ComfyUI 把 prompt 顶层每个键都当节点（值须为含 class_type 的对象）。
    // 丢弃 _comment 等注释/杂散键，否则 validate_prompt 会对字符串值调 .get 崩掉。
    const sanitized: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(workflow)) {
      if (v && typeof v === "object" && typeof (v as { class_type?: unknown }).class_type === "string") {
        sanitized[k] = v;
      }
    }

    // Director 节点的 timeline_data 输入（字符串 JSON）。
    const directorNode = sanitized[nodeId] as { class_type?: string; inputs?: Record<string, unknown> } | undefined;
    if (!directorNode || !directorNode.inputs) {
      throw new Error(`workflow 中缺少节点 ${nodeId}（MiniMaxH3Director）`);
    }
    directorNode.inputs.timeline_data = JSON.stringify(timelineData);
    directorNode.inputs.timeline = JSON.stringify(timelineData); // 兼容旧字段名（节点忽略未知输入）

    this.pendingCallbacks = callbacks;
    this.pendingPromptId = "";
    this.settled = false;
    const { prompt_id } = await this.api.submitPrompt({
      prompt: sanitized as never,
      client_id: this.clientId,
    });
    // 提交成功后启动兜底轮询：WS 断开/丢事件时，用 /history + /queue 判定任务归宿。
    this.pendingPromptId = prompt_id;
    this.startFallbackPolling(prompt_id);
    return prompt_id;
  }

  /** 轮询兜底：查段缓存状态。shotIds 可选：镜头身份校验（防旧项目缓存污染）。 */
  async pollSegmentStatus(nodeId: string, shotIds?: string[]) {
    return this.api.segmentStatus(nodeId, shotIds);
  }

  /**
   * V1.10-B 刷新/重开恢复：为「已在 ComfyUI 执行中的 prompt」重新挂回调 + 兜底轮询。
   * 不重新提交。tickFallbackPolling 会按 /history + /queue 判定该 prompt 归宿：
   * running/queued → 继续等；completed → 成功收尾（含成片回填）；error → 失败收尾；
   * 两者都查不到 → missing 失联收尾。WS 连接重连后进度事件也会流向新回调。
   */
  resume(promptId: string, callbacks: RunCallbacks = {}): void {
    this.pendingCallbacks = callbacks;
    this.pendingPromptId = promptId;
    this.settled = false;
    this.startFallbackPolling(promptId);
  }

  /**
   * 中断当前生成（V1.3.4 取消按钮）。
   * 1) 若任务仍在排队 → deletePending 移出队列；
   * 2) 若正在执行 → ComfyUI /interrupt 中断；
   * 3) 主动收尾（settleRun）触发 onFinish(false)，让任务中心立即反映「已取消」；
   *    随后 WS 抛的 execution_error 会被 settled 挡住，不会重复回调。
   * 注意：/interrupt 会中断 ComfyUI 当前所有 running 任务（本 SPA 单用户场景可接受）。
   */
  async interrupt(): Promise<void> {
    const pid = this.pendingPromptId;
    if (pid) {
      try {
        await this.api.deletePending(pid);
      } catch {
        // 任务已不在排队（正在执行或已结束）→ 忽略 404
      }
    }
    try {
      await this.api.interrupt();
    } catch {
      // 无任务在跑时 /interrupt 可能非 2xx → 忽略
    }
    if (pid && !this.settled) {
      this.settleRun(pid, false);
    }
  }

  private pendingCallbacks: RunCallbacks = {};
  private pendingPromptId = "";
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private pollTicks = 0;
  private settled = false;

  private startFallbackPolling(promptId: string): void {
    this.stopFallbackPolling();
    this.pollTicks = 0;
    this.pollTimer = setInterval(() => {
      void this.tickFallbackPolling(promptId);
    }, POLL_INTERVAL_MS);
  }

  private stopFallbackPolling(): void {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private async tickFallbackPolling(promptId: string): Promise<void> {
    if (this.settled) return;
    this.pollTicks += 1;
    const cb = this.pendingCallbacks;
    if (this.pollTicks > MAX_POLL_TICKS) {
      // 超时兜底：不能只看 tick 数就判失败——成片模式（exportMode=all）整条时间线合并
      // 可能远超 15 分钟，仍在跑的任务会被误杀（后端成功但前端弹「生成失败」）。
      // 超时后先查 /history + /queue 确认任务是否真的消失：
      //  ① /history 有 completed → 成功收尾；② /history error → 失败收尾；
      //  ③ /queue 还在 running/queued → 任务仍在路上，重置 tick 继续等；
      //  ④ 两者都查不到 → 任务彻底失联，按失败收尾（防永久 running）。
      try {
        const history = await this.api.historyById(promptId);
        // 竞态守卫（V1.11.4 #506）：await 期间可能已被 WS execution_success 收尾，
        // 或被下一镜 run() 覆盖；在途 tick 不得再碰 shared 状态（settleRun 守卫也会再挡）。
        if (this.settled || promptId !== this.pendingPromptId) return;
        const item = history?.[promptId];
        if (item?.status) {
          const completed = item.status.completed === true;
          const statusStr = item.status.status_str;
          if (completed) {
            this.backfillVideoFromHistory(promptId);
            this.settleRun(promptId, true);
            return;
          }
          if (statusStr === "error") {
            const info = this.errorInfoFromHistory(item);
            this.settleRun(promptId, false, info);
            void this.backfillReportFromHistory(promptId);
            return;
          }
        }
        const q = await this.api.queue();
        const inRunning = q.queue_running.some(([pid]) => String(pid) === promptId);
        const inPending = q.queue_pending.some(([pid]) => String(pid) === promptId);
        if (inRunning || inPending) {
          // 任务还在队列（长任务超 15 分钟属正常）→ 重置 tick 继续等，不误判失败。
          this.pollTicks = 0;
          cb.onPoll?.({ promptId, status: inRunning ? "running" : "queued" });
          return;
        }
      } catch {
        // 查询失败静默；落到下面按失败收尾，避免任务永久 running。
      }
      this.settleRun(promptId, false);
      return;
    }
    try {
      const history = await this.api.historyById(promptId);
      // 竞态守卫（V1.11.4 #506）：同上，在途 tick 不得在 await 后继续收尾/上报旧状态。
      if (this.settled || promptId !== this.pendingPromptId) return;
      const item = history?.[promptId];
      if (item?.status) {
        const completed = item.status.completed === true;
        const statusStr = item.status.status_str;
        if (completed) {
          // 成片事件（executed）可能因单槽问题丢；用 /history 兜底取一次成片 URL。
          this.backfillVideoFromHistory(promptId);
          this.settleRun(promptId, true);
          return;
        }
        if (statusStr === "error") {
          // WS 断开时错误信息从 history.status.messages 里提取（含 execution_error 载荷）。
          const info = this.errorInfoFromHistory(item);
          this.settleRun(promptId, false, info);
          void this.backfillReportFromHistory(promptId);
          return;
        }
      }
      // history 尚无该 prompt（可能仍在排队/运行）→ 查 /queue 判断是否还在路上。
      // ComfyUI /queue 元组为 [prompt_id, steps, prompt]，prompt_id 在第一个元素。
      const q = await this.api.queue();
      const inRunning = q.queue_running.some(([pid]) => String(pid) === promptId);
      const inPending = q.queue_pending.some(([pid]) => String(pid) === promptId);
      cb.onPoll?.({
        promptId,
        status: inRunning ? "running" : inPending ? "queued" : "missing",
      });
    } catch {
      // 查询失败静默；下一 tick 重试。
    }
  }

  /** 结果落地（WS 事件与兜底轮询共用的唯一出口，防止 onFinish 重复触发）。 */
  private settleRun(promptId: string, ok: boolean, info?: RunFinishInfo): void {
    // 关键守卫（V1.11.4 #506 批量卡死根因）：只允许「当前 pending prompt」收尾。
    // 跨镜场景中，上一镜的在途兜底轮询（await /history 挂起中）/晚到 WS 事件会在
    // 下一镜 run() 重置 settled=false 之后继续执行；若它们能置共享 settled 标志，
    // 下一镜真实的 execution_success 会被 `if (this.settled) return` 吞掉 →
    // onFinish 永不触发 → runOneSequentialShot 的 Promise 永不 resolve → 批量永久卡死。
    if (promptId !== this.pendingPromptId) return;
    if (this.settled) return;
    this.settled = true;
    this.stopFallbackPolling();
    const cb = this.pendingCallbacks;
    cb.onFinish?.(promptId, ok, info);
  }

  /**
   * execution_success 专用收尾（V1.11.4 #506 伴生修复）：先回填成片 URL 再 settle。
   * executed WS 事件可能因单槽问题丢失；若不 await 回填就直接 settle，
   * onFinish 触发时 lastFv 仍为 null → `ok && lastFv` 为假 → createGeneration 不归档（丢版本）。
   */
  private async settleWithVideoBackfill(promptId: string, nodeId?: string): Promise<void> {
    if (promptId !== this.pendingPromptId) return;
    await this.backfillVideoFromHistory(promptId);
    // await 期间可能已被中断/新 run 覆盖；重新校验再 settle（settleRun 内部也会再挡一次）。
    if (promptId !== this.pendingPromptId) return;
    this.settleRun(promptId, true, { nodeId });
  }

  private handleWsEvent(ev: WsEvent): void {
    const cb = this.pendingCallbacks;
    switch (ev.type) {
      case "minimax_director_progress": {
        const parsed = this.adapter.parseProgressEvent(ev.data);
        if (parsed) cb.onProgress?.(parsed);
        break;
      }
      case "minimax_director_preview": {
        const parsed = this.adapter.parsePreviewEvent(ev.data);
        if (parsed) cb.onPreview?.(parsed);
        break;
      }
      case "executed": {
        // 跨镜污染守卫（V1.11.4 #506）：上一镜的 executed 可能晚到下一镜 run() 之后；
        // 若不按 prompt_id 过滤，会污染当前镜的 lastFv（错误地拿旧成片去归档当前镜）。
        const epid = ev.data?.prompt_id != null ? String(ev.data.prompt_id) : "";
        if (epid && epid !== this.pendingPromptId) break;
        // SaveVideo 等输出节点保存完成后：data.output = { images: [{filename,subfolder,type}], animated: [true] }
        const videos = extractVideosFromUi(ev.data.output);
        const ref = videos.find((v) => isVideoFile(v.filename));
        if (ref) {
          cb.onVideo?.({ url: comfyViewUrl(ref), ref });
        }
        break;
      }
      case "execution_success": {
        const promptId = String(ev.data.prompt_id ?? this.pendingPromptId ?? "");
        const nodeId = this.nodeIdOfPrompt(ev.data);
        // V1.11.4 #506：先 await 回填成片（executed 可能丢失）再 settle，
        // 保证 onFinish 触发时 lastFv 已就绪 → 归档不丢版本。
        void this.settleWithVideoBackfill(promptId, nodeId);
        break;
      }
      case "execution_error": {
        const promptId = String(ev.data.prompt_id ?? this.pendingPromptId ?? "");
        // V1.4-P0：完整解析失败节点与异常信息，不再只丢「生成失败」。
        const info: RunFinishInfo = {
          nodeId: ev.data.node_id != null ? String(ev.data.node_id) : undefined,
          errorDetail: ev.data.exception_message != null ? String(ev.data.exception_message) : undefined,
          traceback: ev.data.traceback != null ? String(ev.data.traceback) : undefined,
        };
        this.settleRun(promptId, false, info);
        void this.backfillReportFromHistory(promptId);
        break;
      }
      default:
        break;
    }
  }

  /** history 兜底：从最近一次运行的输出里提取成片 URL。 */
  private async backfillVideoFromHistory(promptId: string): Promise<void> {
    // 跨镜污染守卫（V1.11.4 #506）：上一镜在途回填若在下一镜 run() 之后落地，
    // 会把旧成片 URL 喂给当前镜的 onVideo → 当前镜 lastFv 污染、错误归档。
    if (promptId !== this.pendingPromptId) return;
    try {
      const history = await this.api.historyById(promptId);
      // await 期间可能已被新 run 覆盖；落地前再校验一次。
      if (promptId !== this.pendingPromptId) return;
      const item = history[promptId];
      const ref = firstVideoFromHistory(item);
      if (ref) {
        this.pendingCallbacks.onVideo?.({ url: comfyViewUrl(ref), ref });
      }
    } catch {
      // 兜底失败静默；WS executed 已覆盖多数场景。
    }
  }

  /** V1.4-P0：从 history.status.messages 提取 execution_error 载荷（WS 断开时的错误来源）。 */
  private errorInfoFromHistory(item: HistoryItem | undefined): RunFinishInfo {
    const msgs = (item?.status?.messages ?? []) as Array<[string, Record<string, unknown>]>;
    for (const [, data] of msgs) {
      if (data && typeof data === "object" && data.exception_message != null) {
        return {
          nodeId: data.node_id != null ? String(data.node_id) : undefined,
          errorDetail: String(data.exception_message),
          traceback: data.traceback != null ? String(data.traceback) : undefined,
        };
      }
    }
    return {};
  }

  /** V1.4-P0：失败后从 /history 取回 Director 节点 report（第 6 输出 STRING，best-effort）。 */
  private async backfillReportFromHistory(promptId: string): Promise<void> {
    // 陈旧事件保护：error 事件可能晚到，只认当前 pending 的 prompt。
    if (promptId !== this.pendingPromptId) return;
    try {
      const history = await this.api.historyById(promptId);
      const report = this.reportFromHistory(history[promptId]);
      if (report) {
        this.pendingCallbacks.onReport?.(promptId, report);
      }
    } catch {
      // 拉取失败静默；详情面板不因 report 缺失而不可用。
    }
  }

  /** 从 history item 的 outputs 里扫出第一个 STRING 输出（ComfyUI 存为 { text: [...] }）。 */
  private reportFromHistory(item: HistoryItem | undefined): string | undefined {
    if (!item) return undefined;
    for (const out of Object.values(item.outputs ?? {})) {
      const raw = out?.text;
      if (raw != null) {
        const txt = Array.isArray(raw) ? raw[0] : raw;
        if (typeof txt === "string" && txt.trim().length > 0) return txt;
      }
    }
    return undefined;
  }

  private nodeIdOfPrompt(data: Record<string, unknown>): string | undefined {
    // 从 execution_success 的 node map 里找 Director 节点（尽力而为）。
    const nodes = data.nodes as Array<{ node_id?: number; class_type?: string }> | undefined;
    const director = nodes?.find((n) => String(n.class_type ?? "").includes("MiniMaxH3Director"));
    return director ? String(director.node_id) : undefined;
  }
}
