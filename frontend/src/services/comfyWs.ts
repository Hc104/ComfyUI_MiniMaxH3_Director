/**
 * ComfyUI WebSocket 客户端（进度/预览）。
 *
 * 单槽问题（蓝图 §6.2）：后端 send_sync 只发给最后连接的 WS 客户端。
 * V1 策略：SPA 保持唯一活跃 WS 连接；断线时轮询 /history + /minimax/director/segment_status 兜底。
 */

export type WsEventType =
  | "status"
  | "execution_start"
  | "execution_success"
  | "execution_error"
  | "execution_cached"
  | "executed"
  | "progress"
  | "executing"
  | "minimax_director_progress"
  | "minimax_director_preview"
  | "minimax_director_enhanced";

export interface WsEvent {
  type: WsEventType;
  data: Record<string, unknown>;
}

export interface WsOptions {
  /** 连接生命周期回调。 */
  onEvent: (ev: WsEvent) => void;
  onStatus?: (connected: boolean) => void;
  /** 自动重连间隔 ms（默认 2000）。 */
  reconnectMs?: number;
}

export class ComfyWsClient {
  private ws: WebSocket | null = null;
  private closedByUser = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private url: string;
  private clientId: string | null = null;

  constructor(
    /** WS 端点，如 "ws://127.0.0.1:8188/ws"；走代理时可填 "/ws"。 */
    baseWsUrl: string,
    private readonly opts: WsOptions,
  ) {
    this.url = baseWsUrl;
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  connect(clientId?: string): void {
    this.closedByUser = false;
    this.clientId = clientId ?? this.clientId;
    this.open();
  }

  disconnect(): void {
    this.closedByUser = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.ws?.close();
    this.ws = null;
    this.opts.onStatus?.(false);
  }

  private open(): void {
    if (this.closedByUser) return;
    let url = this.url;
    if (this.clientId) {
      url += url.includes("?") ? `&clientId=${encodeURIComponent(this.clientId)}` : `?clientId=${encodeURIComponent(this.clientId)}`;
    }
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => this.opts.onStatus?.(true);

    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(String(msg.data)) as { type?: string; data?: Record<string, unknown> };
        if (!parsed.type) return;
        this.opts.onEvent({ type: parsed.type as WsEventType, data: parsed.data ?? {} });
      } catch {
        // 非 JSON 帧（如二进制预览帧）忽略；预览走 minimax_director_preview JSON。
      }
    };

    ws.onclose = () => {
      this.opts.onStatus?.(false);
      if (!this.closedByUser) {
        this.reconnectTimer = setTimeout(() => this.open(), this.opts.reconnectMs ?? 2000);
      }
    };

    ws.onerror = () => {
      // onclose 会随后触发并调度重连。
    };
  }
}
