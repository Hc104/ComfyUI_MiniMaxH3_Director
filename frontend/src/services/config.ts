/**
 * 连接配置（V1：默认走 Vite 同源代理，ComfyUI 在 127.0.0.1:8188）。
 * 生产部署到 ComfyUI 静态目录时 baseUrl 留空即可。
 */
export const COMFY_BASE_URL = import.meta.env.VITE_COMFY_BASE ?? "";

/** WS 端点：走代理时用相对路径，直连时拼 ws://host:port/ws。 */
export const COMFY_WS_URL = import.meta.env.VITE_COMFY_WS ?? `${wsProto(COMFY_BASE_URL)}/ws`;

function wsProto(base: string): string {
  if (!base) return "/ws";
  return base.replace(/^http/, "ws");
}
