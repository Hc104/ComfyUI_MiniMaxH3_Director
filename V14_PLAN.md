# V1.4 收尾规划（2026-08-10）

> 状态：**规划文档，供用户决策。尚未开始实施。**
> 前提：V1.3 人工验收基本通过（2026-08-10 用户确认），已封版。
> 目标：把「能稳定生产」的四块拼图补齐，不新增业务功能。

---

## 总览

| 项 | 优先级 | 核心问题 | 改动性质 |
|---|---|---|---|
| 错误定位 | P0 | 失败只显示「生成失败」，后端真实异常被丢弃 | 纯前端 |
| Dirty 状态 | P0 | 完全无「已保存/未保存」标记 | 纯前端 |
| 退出保护 | P0 | 关页/返回项目无任何确认，内存编辑直接丢 | 纯前端 |
| WS 状态视觉 | P1 | 连接圆点是「假绿点」，断线无感知 | 纯前端 |

全部纯前端改动，不涉及后端 → **刷新即生效，无需重启 Comfy Desktop**。

---

## P0-1 错误定位

### 现状

- 生成失败时任务中心只显示一个 `⚠` 图标（悬停才见 `t.error`），摘要行显示截断的 `shortErr`。
- `TaskRecord` 只有 `error: string|null`，失败时写死固定文案 `"生成失败"` —— 后端真实失败原因被丢弃。
- ComfyUI 标准 `execution_error` 事件携带 `node_id / node_type / exception_message / exception_type / traceback`，但前端 `directorRun.ts` 只取 `prompt_id`，其余全扔。
- 后端 report（Director 节点第 6 输出，含段连续/VRAM/Qwen 三级反馈诊断）存在 `/history/{prompt_id}` 里，前端已有 `api.historyById` 但**未消费**。
- `node_id` 前端硬编码 `"5"`；失败镜头 id 可由 `targetShotIds + shotOrder + 失败段索引` 还原。

### 目标行为

失败后用户能点开「查看详情」，看到：失败镜头（id/名称）、失败原因（exception_message）、prompt_id、node_id、后端 report（诊断全文），并可直接「重新生成」。

### 改动点

1. **`TaskRecord` 扩展字段**：`nodeId: string`、`report: string | null`、`errorDetail: string | null`（完整 exception_message）、`failedShotIds: string[]`。
2. **`directorRun.ts` 解析 execution_error**：从事件里取 `node_id` + `exception_message`，把完整异常写进 task（不再只 `settleRun(promptId, false)`）。
3. **失败时拉 report**：失败瞬间用 `api.historyById(promptId)` 取节点第 6 输出 `"report"`，存进 TaskRecord（补全诊断）。
4. **失败镜头还原**：任务结束时把 `segStatus.states` 里 `"failed"` 的段索引固化进 `failedShotIds`（避免依赖瞬态 segStatus）。
5. **UI「查看详情」**：任务中心失败任务点开 → 面板展示 失败镜头 / 失败原因 / prompt_id / node_id / 后端 report（等宽可复制），按钮「重新生成」（复用 V1.3.3-B 的 regen）。

### 涉及文件

`stores/workbench.ts`、`services/directorRun.ts`、`workbench/WorkbenchView.vue`（任务中心渲染 + 详情面板）、`services/comfyApi.ts`（若 historyById 需要扩展取 report 的方式，大概率不用改）。

### 验证

- vitest：解析 execution_error 单测（mock 事件 → 断言 nodeId/errorDetail 写入）；失败拉 report 单测（mock historyById → 断言 report 落 task）；失败镜头还原单测。
- 手动：造一次失败（如把镜头任务类型改成后端不认识的）→ 看详情面板是否完整展示。

---

## P0-2 Dirty 状态

### 现状

- store 无任何 dirty/saved 字段。
- 保存是纯手动：顶栏「保存项目」→ `POST /minimax/director/project/{id}`，成功后按钮文案变「已保存 ✓」1.6 秒即复位。
- 所有编辑操作（改 Prompt/时长/资产/镜头增删改…）直接改 reactive project 树，**不触发任何未保存标记**。

### 目标行为

顶栏常驻保存状态：`💾 已保存`（无修改）→ 编辑后 `● 有未保存修改`（可点保存）→ 保存成功 `✓ 已保存`。无修改时保存按钮禁用。

### 改动点

1. **store 加字段**：`dirty: boolean`、`lastSavedAt: number | null`，加 `markDirty()` / `markSaved()`。
2. **所有编辑原子操作统一置脏**：`updateShotContent / updateShotDuration / updateAsset / addShot / deleteShot / addScene / deleteScene / …` 末尾调 `markDirty()`。最省事：在 store 里加一个统一的内部包装，或逐个在 mutation 后置脏。
3. **打开/新建/导入/载入示例后重置** `dirty = false`（基线）。
4. **UI**：顶栏保存按钮区显示三态文案；`!dirty` 时禁用保存按钮；保存成功 `markSaved()`。

### 涉及文件

`stores/workbench.ts`（字段 + 各 update 函数）、`workbench/WorkbenchView.vue`（顶栏保存区 UI + 按钮禁用）。

### 验证

- vitest：改 Prompt → dirty=true；markSaved → false；打开项目 → false。
- 手动：改几处 → 看顶栏变「● 有未保存修改」→ 保存 → 变「✓ 已保存」。

---

## P0-3 退出保护

### 现状

- 无 vue-router，页面切换是 WorkbenchView 内部 `view = ref<"home"|"workbench">` 的 v-if/v-else。
- 顶栏「← 项目」→ `goHome()` → `clearProject()`（清空 project/segStatus/tasks/paramHashes/preview/finalVideo）→ 回项目选择页，**无任何确认**。
- 全代码无 `beforeunload` / `onBeforeRouteLeave` / `onUnmounted`。
- 关标签页：内存编辑全丢，无提示；运行中关页，WS 断开、任务可能继续跑、前端无记录。

### 目标行为

- **关页/刷新**：有未保存修改时弹浏览器确认「有未保存的修改，确定离开？」。
- **「← 项目」**：有未保存修改时弹确认对话框（保存并离开 / 不保存离开 / 取消）；即使已保存过，也提示「本次会话还有 N 个任务/镜头未保存？」，避免 `clearProject` 把会话态一并丢掉（此项可配置为「仅 dirty 时提示」先做最小版）。
- 运行中离开：同样提示（已有任务在跑）。

### 改动点

1. **`beforeunload`**：组件挂载时注册，`dirty` 时 `e.preventDefault() + returnValue` 触发浏览器原生确认。
2. **`goHome` 改造**：dirty 时先弹站内确认对话框（三选一），非 dirty 直接走。注意把 `clearProject` 的语义从「无条件清空」改为「确认后清空」。
3. **确认对话框组件**：站内 modal（保存并离开 → 先 `saveCurrentProject()` 再 `goHome`；不保存离开 → 直接 `goHome`；取消 → 原地不动）。
4. **可选**：`onUnmounted` 时调 `runService.disconnect()`，WS 生命周期收尾（顺带补 P1 缺口）。

### 涉及文件

`workbench/WorkbenchView.vue`（beforeunload 注册 + goHome 改造 + 确认 modal）、`stores/workbench.ts`（clearProject 语义、dirty 读取）。

### 验证

- vitest：dirty 时 beforeunload 触发 preventDefault；goHome 在 dirty 时打开 modal、确认后清空。
- 手动：改 Prompt 不保存 → 点「← 项目」→ 弹确认 → 取消原地 / 保存并离开 / 放弃离开；改后直接刷新 → 浏览器提示。

---

## P1 WS 状态视觉

### 现状

- 首页/工作台顶部都有圆点 `.dot` +「已连接 ComfyUI」文案。
- 但 `DirectorRunService` 构造时把 `onStatus` 写死为 `() => undefined`，**回调根本没接到 store** → 圆点只反映「首次连接成功」，WS 断开后仍是绿点（假绿点）。
- 底层自动重连（2s）+ 3s 轮询兜底都在，只缺可见状态。

### 目标行为

圆点三态：`● 绿=已连接` / `○ 灰=连接断开·正在重连` / `● 恢复绿=已重连成功`。断线时任务照常（轮询兜底已在跑），视觉同步。

### 改动点

1. **`directorRun.ts`**：把 `onStatus` 从空实现接到 store（`setWsConnected(boolean)`）；或在 connect 成功后轮询 `ws.connected`。
2. **store 加字段**：`wsConnected: boolean`（或升级为 `wsState: "connected" | "disconnected"`），替换现有 `connected` 的更新路径。
3. **`WorkbenchView.vue`**：圆点 class 按状态切换；断线时文案「连接断开·正在重连」，恢复后回「已连接」。
4. **组件卸载时 `disconnect()`**（与退出保护共用）。

### 涉及文件

`services/directorRun.ts`、`services/comfyWs.ts`（小改：确保 onStatus 覆盖重连成功/失败）、`stores/workbench.ts`、`workbench/WorkbenchView.vue`。

### 验证

- vitest：mock ws onStatus → 断言 store 状态切换；断线 → 圆点 class 变化。
- 手动：开着 SPA 把 ComfyUI Desktop 停掉 → 圆点变灰；重启 ComfyUI → 变回绿。

---

## 实施顺序与依赖

依赖关系：**P0-2 Dirty 是 P0-3 退出保护的前置**（退出保护要判断 dirty）。P0-1 错误定位独立。P1 独立。

建议顺序：

1. **P0-1 错误定位**（最影响生产使用，先补）
2. **P0-2 Dirty 状态**（纯增量，快）
3. **P0-3 退出保护**（依赖 2）
4. **P1 WS 状态视觉**（独立，收尾）

每项完成后：vitest 加回归 + `vue-tsc && vite build` + 双目录同步 + CHANGELOG + memory。

## 风险与说明

- 退出保护的「← 项目」确认，需要把 `clearProject` 从「无条件清空」改为「确认后清空」——这是行为变更，用户点「← 项目」会多一步确认，属于预期。
- 错误定位里 report 全文可能很长（Qwen 三级反馈诊断），详情面板要可滚动/可复制，不塞进摘要行。
- 四项都纯前端，无后端依赖，不重启 Comfy Desktop；但**需重新 `npm run dev`（或 vite 热更新自动生效）**。
- 本轮不动 V1.5（素材库/@ 资产深化）。

## 待用户决策点

1. 实施顺序是否按上面建议？（或指定先做某一项）
2. 退出保护的「← 项目」提示，最小版（仅 dirty 时提示）还是完整版（连任务会话态也提示）？
3. 错误定位的「查看详情」，report 全文展示是否会太长？要不要只展示 exception_message + report 前 N 行，report 全文折叠？
