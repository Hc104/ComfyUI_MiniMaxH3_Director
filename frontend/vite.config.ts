import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";
import { readFileSync } from "node:fs";

// MiniMax Studio — Vite 配置
// 开发时默认走 ComfyUI 同源代理（部署到 ComfyUI 静态目录时无跨域）。
// ComfyUI 默认端口 8188；可在 env 里改：VITE_COMFY_BASE 或 --host。
export default defineConfig(() => {
  const comfyBase = process.env.VITE_COMFY_BASE || "http://127.0.0.1:8188";
  // ComfyUI 默认开启 origin_only_middleware：校验 Host 与 Origin 必须同域（防外部网站 POST 127.0.0.1）。
  // dev 代理时浏览器发 Origin: localhost:5173，转发的 Host 是 ComfyUI 地址 → 403。
  // 这里把 Origin 改写成 ComfyUI 同源，绕过校验（只影响转发头，浏览器视角仍是同源 5173）。
  const comfyOrigin = new URL(comfyBase).origin;

  // P0-B（#482）：构建标识 —— 每次 build / dev server 启动唯一（版本 + 本地时间戳）。
  // 注入 `__BUILD_ID__` 全局常量（见 src/env.d.ts 声明），顶栏右侧小字展示，报 bug 可精确对构建。
  const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8"));
  const _now = new Date();
  const _pad = (n: number) => String(n).padStart(2, "0");
  const BUILD_ID = `v${pkg.version ?? "0.0.0"}·b${String(_now.getFullYear()).slice(2)}${_pad(_now.getMonth() + 1)}${_pad(_now.getDate())}-${_pad(_now.getHours())}${_pad(_now.getMinutes())}`;

  return {
    plugins: [vue()],
    define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        // 开发期把 ComfyUI 的 API/WS 走同源代理，规避 CORS + origin 校验。
        // #92：必须显式代理 /interrupt —— 之前缺失导致 SPA「取消生成」的 POST /interrupt
        // 打到 vite dev server 自身（404 被前端吞掉），ComfyUI 后端从未收到中断信号，
        // 生成继续跑完。同源部署（ComfyUI 静态目录）无此问题，但 dev 模式依赖此规则。
        "/object_info": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/prompt": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/queue": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/history": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/interrupt": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/free": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/ws": { target: comfyBase, changeOrigin: true, ws: true, rewriteWsOrigin: true },
        "/view": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/upload": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/api": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
        "/minimax": { target: comfyBase, changeOrigin: true, headers: { Origin: comfyOrigin } },
      },
    },
    test: {
      globals: true,
      environment: "node",
      // e2e 真闭环验收独立于单测：文件名不带 .test 后缀，仅 `npm run e2e` 显式运行时执行。
    },
  };
});
