#!/usr/bin/env node
/**
 * E2E 真 H3 闭环验收运行器（跨平台：Windows cmd/PowerShell / macOS / Linux）。
 *
 *   npm run e2e          → E2E_SCOPE=shot（默认：只生成第一镜，最快暴露链路问题）
 *   npm run e2e:all      → E2E_SCOPE=all（整片 3 镜，更久）
 *   E2E_SCOPE=all npm run e2e   → Unix 语法在 Windows 不适用，请改用 npm run e2e:all
 *
 * 直接调用 node_modules/.bin/vitest，绕过 npm scripts 在 Windows 上无法设置
 * 环境变量的问题（`E2E_SCOPE=all npm run ...` 只在 bash 里有效）。
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scopeArg = process.argv[2];
if (scopeArg && scopeArg !== "shot" && scopeArg !== "all") {
  console.error(`[e2e] 未知 E2E_SCOPE: ${scopeArg}（仅 shot | all）`);
  process.exit(1);
}
if (scopeArg) process.env.E2E_SCOPE = scopeArg;
if (!process.env.E2E_SCOPE) process.env.E2E_SCOPE = "shot";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const vitestBin = path.join(
  root,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "vitest.cmd" : "vitest",
);

console.log(`[e2e] scope=${process.env.E2E_SCOPE}  base=${process.env.VITE_COMFY_BASE ?? "http://127.0.0.1:8188"}`);
const child = spawn(vitestBin, ["run", "tests/e2e.verify.test.ts", "--testTimeout=3600000"], {
  cwd: root,
  stdio: "inherit",
  shell: process.platform === "win32",
});
child.on("exit", (code) => process.exit(code ?? 1));
