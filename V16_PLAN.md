# V1.6 规划（成片交付 + 多角色）— 已拍板定稿

> 状态：**已冻结（2026-08-10 Golden Path 验收通过 + 用户拍板）**。V1.6 不再追加功能，进入
> **V1.6.x Production Hardening · 生产观察期**（见 `V1.6x_PRODUCTION_HARDENING.md`）。
> 实施顺序 C → A → B → 冻结 → Golden Path 验收（已全部完成）。
> V1.6 定义：从「镜头生产工作台」正式变成**「可以交付成片的生产工作台」**。
> 版本边界（用户拍板）：**V1.4 = 稳定生产 / V1.5 = 资产一致性 / V1.6 = 成片交付 + 多角色 / V1.6.x = 生产观察期 / V1.7 = 视频资产/剪辑层（主题待定）**。
> 关联：`第七号站台_走查问题清单.md`、`V1.5_MATERIAL_LIBRARY_PLAN.md`（冻结）、`V14_PLAN.md`、`CHANGELOG.md`。

---

## 0. 拍板结论（用户 2026-08-10 评审）

| 方向 | 结论 | 优先级 |
|---|---|---|
| **C 项目/集命名** | 做，极小化（inline 编辑） | P1 |
| **A 导出中心** | **做，V1.6 核心**——不造独立页面，导出=任务中心一种任务 | P0 |
| **B 多角色 cast 集合** | 做，独立一期，做彻底（castId→castIds→cast_assets[]） | P1 |
| **D 视频替换** | **明确延后 V1.7**（牵涉视频来源/缓存/指纹/Shot 状态/导出/重生成/项目重开，不是小按钮） | P3 |

**实施顺序**：**C → A → B → V1.6 冻结 → 真实整片交付验收（Golden Path）**

---

## 1. V1.6-C：项目/集命名（极小化，纯前端）

**现状**：`WorkbenchView.vue:746` `createProject("未命名项目")` 硬编码；集标题硬编码「第一集」；无改名 UI（走查 Q1）。`Project.name` / `Episode.title` 已是模型字段，`saveCurrentProject` 序列化全量——**无需后端变更**。

**改动点**：
1. `stores/workbench.ts`：`renameProject(name)` / `renameEpisode(title)` 原子操作（trim + touch 置脏）。
2. `workbench/WorkbenchView.vue`：
   - 首页项目卡片 `proj-name` 旁加 ✎ → inline input（`[ 项目名____ ] ✓`），`@click.stop` 不触发打开。
   - 工作台顶栏 `.wb-project`（集标题）加 ✎ → inline input，确认写回。
3. `touch()` → dirty → 💾 保存，结束。**不新增**项目管理页/设置页/命名弹窗。

**测试**：store 2 例（改名写回 + touch 置脏）+ workbenchRender 2 例（home inline 改名 / 顶栏集改名）。

---

## 2. V1.6-A：成片导出（V1.6 核心）

### 核心思路（用户拍板）

导出**不是**新页面，是任务中心里的一种任务。顶部生成栏：`▶ 生成　⭳ 导出`。点击导出弹一个小对话框（整片/当前场景 + 已生成镜头数），确认后进任务中心，进度 `编码 Shot 08 → 合并成片`，产物「▶ 查看成片」。

### 关键设计（用户重点补的点）

**① 导出前必须做「缺失镜头 + 参数过期检测」，不能等后端报错**
- 复用现有 `segStatus.cached` + 参数指纹 `shotFingerprint`（st-stale 逻辑，V1.4 已建）。
- 导出弹窗内直接列出问题：
  - `⚠ 有 2 个镜头尚未生成 / Shot 07 未生成 / Shot 10 参数已修改，需重新生成`
  - 操作：`[仅导出已完成] [去生成缺失镜头] [取消]`
- **默认禁止导出旧缓存**：参数已变（st-stale）的镜头当前缓存对应旧参数，直接导出的成片会混入旧内容——必须提示 `Shot 03 参数已修改，需要重新生成 [重新生成 Shot 03]`。

**② 导出按钮三态**（工作台顶栏）
- 🟢 全部可导出（所有镜头 cached 且未 stale）
- 🟡 有缺失/参数过期镜头
- 🔴 当前无可导出内容

**③ 缺失段处理**：后端 `not_cached` 并列缺失段号（已有设计保留），但 UX 前置到前端弹窗。

### 改动点（文件级）

**后端**（`director/http_routes.py` + 复用 `director/stream_export.py`）：
1. 新增 `POST /minimax/director/export`，参数：`node_id`、`scope=movie|scene`、`scene_id`（scene 档，可选，缺省整片）、可选 `fps`。
2. 逻辑：遍历缓存段序 → 无 mp4 先 `save_segment_mp4` 原子编码 → 收集 mp4 路径 → `merge_segment_mp4s` ffmpeg 直拼 → 返回 `{path, filename}`。
3. 边界：无缓存段报 `not_cached` 并列缺失段号；ffmpeg 缺失走 `ffmpeg_bin()` 报错。**需重启 Comfy Desktop**。

**前端**：
1. `services/comfyApi.ts`：`exportMovie(nodeId, opts?)` / `exportScene(nodeId, sceneId, opts?)`。
2. `stores/workbench.ts`：TaskRecord 加 `kind: "generate" | "export"`；`startExportTask(scope, label)` + 导出轮询（复用 poll 模式，超时先查状态不误判 #105 经验）。
3. `workbench/WorkbenchView.vue`：生成栏加 `⭳ 导出` 按钮 + 三态；导出弹窗（范围 + 缺失/过期检测 + 三操作）；导出任务进任务中心。
4. `workbench/TaskCenter.vue`：导出任务渲染（编码段 x/y + 合并进度 + 查看成片）。

**导出前检测**（纯前端，复用现有能力）：`flattenedShots` + `segStatus.cached` + 指纹 stale → 分类「可导出 / 缺失 / 参数过期」，驱动弹窗和三态按钮。

---

## 3. V1.6-B：多角色 cast 集合（独立一期，做彻底）

### 核心思路（用户拍板）

- **做彻底，不做半吊子**：`castId → castIds → cast_assets[]`。原因：`@林雪 和 @陈默` 若只 `castId: "林雪"`，H3 实际只拿林雪参考图——这是**生成质量问题**（身份一致性/多人同框/第二角色服装/脸部特征），不只是 UI 问题。
- **兼容旧数据**：保留 `castId`，新增 `castIds: []`，最终统一成 `cast_assets[]`。

### 关键规则（用户重点补的点）

**① 去重层（canonical identity）**：显式选中的角色和 `@` 命中的角色**不能重复注入**。
```
显式：林雪、陈默    Prompt：@林雪 和 @陈默
最终：cast_assets = [林雪, 陈默]（去重，不是 4 条）
```
现有 prop/style 已按名称去重，cast 这一轮把规则统一掉（`trim+lowercase` 名称判重，场景池优先）。

**② UI 用 chip，不用多选下拉**（用户克制建议）：
```
人物
[林雪 ×] [陈默 ×]  + 添加人物
生效：林雪 ⚡镜头指定 / 陈默 ⚡镜头指定
```
扩到 3~4 角色也不难用，符合素材库设计。

### 改动点（文件级）

**前端**：
1. `models/project.ts`：Shot 加 `castIds?: string[]`（保留 `castId?` 兼容，读旧数据单值→数组迁移）。
2. `core/inheritanceResolver.ts`：新增 `resolveCollection("cast")`（复用 prop/style 集合级：场景池优先 + 全局补缺 + 名称去重）；`resolveInheritance` cast 改集合；canonical identity 去重（显式 ∪ @命中，去重）。
3. `stores/workbench.ts`：`updateShotCast` 改数组写回；`addShot` 不预写默认（保持 #98）。
4. `workbench/WorkbenchView.vue`：人物区域改 chip 集合（[名 ×] + 添加人物），生效区多徽标。

**后端**：
1. `director/plan.py`：`SegmentPlan.cast_asset` → `cast_assets: list[GlobalAsset]`（兼容 getter）。
2. `director/gen_timeline.py`：段解析 `castId`/`castIds` → 数组逐个匹配 → `cast_assets` 列表；`@` 命中 cast（tag_assets）并入去重。
3. `director/executor_core.py`：角色资产图循环扩为 `cast_assets` 列表逐一 append `("角色资产图", name, tensor)`（现有管线天然支持多图）。
4. 快照兼容：旧 timeline `castId` 单值 → `cast_assets` 单元素。

---

## 4. V1.6-D：视频替换 → 明确延后 V1.7

用户明确：V1.6 千万别碰。牵涉视频来源/缓存/参数指纹/Shot 状态/导出/重新生成/项目重开/下载转码，不是小按钮。V1.7 归入「视频资产/剪辑层」。

---

## 5. Golden Path 生产验收剧本（用户建议，每大版本保留）

**主线（V1.6 完成后全量走一遍）**：
```
新建项目 → 命名「第七号站台」→ 创建 Scene → 建立人物/地点/道具/风格
→ 生成 8~11 个 Shot → 修改其中一个 Prompt → 确认橙色「需重新生成」
→ 重新生成 → 全部缓存完成 → 导出当前 Scene → 播放
→ 导出整片 → 下载 MP4 → 重新打开项目 → 再次播放/导出
```

**多角色专项**：
```
Shot 05 林雪+陈默 → castIds=[林雪, 陈默] → 两个参考图进 timeline → H3 生成 → 检查两人身份一致性
```

这两条跑通，V1.6 才是真正意义上的「生产闭环完成」。

---

## 6. 实施清单（每项完成 = vitest 回归 + vue-tsc + vite build + 后端 py_compile + 双目录同步 + CHANGELOG + memory）

- [x] V1.6-C：项目/集命名（inline，纯前端）
- [x] V1.6-A-后端：`/minimax/director/export` 路由（scene/movie 流式合并）
- [x] V1.6-A-前端：comfyApi 导出方法 + TaskRecord.kind + 导出任务
- [x] V1.6-A-前端：导出按钮三态 + 导出弹窗（缺失/过期检测）+ 任务中心导出渲染
- [x] V1.6-B-前端：castIds + resolveCollection("cast") + chip UI（259 测试 + vue-tsc + vite build 全绿）
- [x] V1.6-B-后端：plan/gen_timeline/executor cast_assets 列表（cast_asset 单值→cast_assets 列表迁移 + @命中 cast 并入 canonical 去重 + executor 逐一注入 + 缓存指纹按列表失效；py_compile 全绿；test_segment_cache_identity 8 passed）
- [x] **V1.6 冻结 + Golden Path 验收（含多角色专项）——2026-08-10 用户确认全部通过**（CHANGELOG #112）

> **Golden Path 验收结果（2026-08-10 用户实测确认）**：
> - **主线**：新建项目 → 命名 → 建 Scene → 建人物/地点/道具/风格 → 生成 8~11 Shot → **修改 Prompt → 确认橙色「需重新生成」**（#111 指纹持久化修复后已验证）→ 重新生成 → 全部缓存 → 导出 Scene → 播放 → 导出整片 → 下载 MP4 → 重开项目 → 再次播放/导出 ✅
> - **多角色专项**：Shot 05 林雪+陈默 → castIds=[林雪,陈默] → 两个参考图进 timeline → H3 生成 → 两人身份一致性 ✅
> - V1.6 全部完成，正式冻结。

## 7. 涉及文件汇总

| 方向 | 后端 | 前端 |
|---|---|---|
| C | 无 | `workbench.ts`、`WorkbenchView.vue` |
| A | `http_routes.py`（新导出路由）、复用 `stream_export.py` | `comfyApi.ts`、`workbench.ts`、`WorkbenchView.vue`、`TaskCenter.vue` |
| B | `plan.py`、`gen_timeline.py`、`executor_core.py` | `project.ts`、`inheritanceResolver.ts`、`workbench.ts`、`WorkbenchView.vue` |
