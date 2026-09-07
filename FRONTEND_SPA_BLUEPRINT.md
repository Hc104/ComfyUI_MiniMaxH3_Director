# MiniMax Studio — 独立前端大框架蓝图

> 版本：v1.1（2026-08-09） · 状态：设计蓝图，未动工
> 定位：把 `MiniMaxH3Director` 当作「后端引擎」固定下来，独立开发 Vue 3 前端围绕其数据模型做「项目 → 场景 → 镜头 → 素材 → Prompt」的层级系统。
> 本文件是动代码前的需求 + 现有节点全量梳理，也是后续所有实现的分工依据。
> **v1.1 变更**：交互层重定义为「AI 视频导演工作台」——单分镜工作台为核心 + Scene 世界观一致性管理器 + 结构化 Prompt + @资产 + 继承可视化 + 任务中心；架构层新增 Director Core；V1 范围收缩为单页。详见 §0。

---

## 目录

1. [目标与定位](#1-目标与定位)
2. [现有节点能力盘点（设计依据）](#2-现有节点能力盘点)
3. [目标架构](#3-目标架构)
4. [项目文件 Schema](#4-项目文件-schema)
5. [Adapter 层设计](#5-adapter-层设计)
6. [通信层设计](#6-通信层设计)
7. [交互设计：分镜工作台（v1.1 重写）](#7-交互设计分镜工作台v11-重写)
8. [分阶段实施计划（V1 单页优先）](#8-分阶段实施计划v1-单页优先)
9. [风险与决策点](#9-风险与决策点)
10. [附录：字段契约权威清单](#10-附录字段契约权威清单)

---

## 0. v1.1 UI 评审修订（2026-08-09）

> 用户在完整评审后拍板的方向调整。**架构层（Project Model / Adapter / 数据流 / 通信）维持 v1 不变**，交互层重新定义：
>
> 1. **产品定位从「管理后台」改为「AI 视频导演工作台」**——用户 70% 时间停留在**一个**分镜工作台页面（看到故事 → 找镜头 → 改镜头 → 生成 → 看结果 → 继续拍），不跳页。
> 2. **Scene 提升为一级核心概念**：它是「世界观/场景一致性管理器」而非普通分组。场景设置 = 参考图 + 地点/时间/天气/默认人物/默认风格/默认道具 + 场景描述，所有镜头自动继承。
> 3. **Shot 是视觉单位**：左侧树里 Scene 用故事单位（🌧 霓虹街区），Shot 用**缩略图卡片**（🖼 + 序号 + 名称 + 时长 + 状态灯），不是文字 `shot_001`。
> 4. **Prompt 结构化**：画面/摄影/风格/声音/负面词分区 + 简洁/高级双模式，Adapter 后台合成 H3 完整格式。机器 Prompt（`integrated_multimodal_description`）永远不是主编辑框。
> 5. **@资产识别成为核心交互**：输入 `@林雪走进@霓虹街区` → 实时高亮 + 右侧资产面板 + 一致性绑定状态（人物✓/场景✓/道具✓/风格✓）。
> 6. **继承关系可视化**：资产条目显示来源（↑来自 Scene 01 / ↑来自 Project / ⚡ Shot 覆盖），把后端继承链（显式 > @匹配 > 场景默认 > 全局默认）变成用户看得见的 UI。
> 7. **镜头时间线而非剪辑时间线**：只有镜头胶囊 + 状态灯 + 时长刻度，不做 Premiere 式多轨。
> 8. **生成不跳页**：原地预览 + 右下角常驻任务中心（运行中/排队/已完成），成片页只管批量查看/导出/合并。
> 9. **架构加 Director Core 层**：Asset Resolver / Inheritance Resolver / Prompt Builder / Timeline Builder / Render Planner 独立成层，**不塞进 Adapter**（否则变 2000 行 `MiniMaxH3Adapter.ts`）。Adapter 只做薄翻译。
> 10. **ShotModel 去 H3 化**：`smartTail/continuityMode/castManual` 等收进 `generation` 子对象，前端模型面向内容创作而非模型参数。
> 11. **V1 范围收缩**：只做一个核心分镜工作台页（项目→分镜→生成→视频）。素材库/@资产/继承 → V1.5，Prompt 导演系统 → V2，多模型 → V3。

**v1.1 改动的章节**：§1（定位）、§3（架构加 Director Core）、§4（ShotModel 重构 + 状态）、§5（Core/Adapter 分层）、§7（交互层重写）、§8（V1 单页计划）、§9（决策点更新）。**未改**：§2（节点盘点，硬事实）、§6（通信层，仍有效）、§10（字段契约，仍权威）。

### 0.1 已拍板原则（用户 2026-08-09 确认，实现硬约束）

> 以下 11 条为**已定原则**，后续任何方案/代码不得违背。实现时以本清单为最高优先级，蓝图其余文字描述若与原则冲突，以本清单为准。

1. **V1 核心只有「分镜工作台」**，不再回到五页面设计。
2. **Director Core 与 Adapter 严格分层**：Core 不知道 H3 字段，Adapter 只做翻译。
3. **ShotModel → TimelineStructure → Adapter → timeline_data 是唯一数据链路**，不允许前端直接拼 H3 数据。
4. **`durationSec` 是项目模型真相**，`frameCount/length` 由 Adapter 派生（对齐 17k+5 网格，上限 512）。
5. **Scene 是一级故事/一致性单位**，但完整素材库、@资产、继承放到 V1.5。
6. **V1 先支持导入现有 timeline_data**，优先跑通真实生成闭环。
7. **V1 只做 MiniMax H3 r2v prompt_batch**，fl2v/video/Wan/LTX 后置。
8. **SPA 与现节点 UI 并行共存**，成熟后冻结旧 UI。
9. **生成不跳页**，播放器、时间线、镜头设置、任务中心形成完整闭环。
10. **WS 采用主动连接 + 轮询兜底**，暂不急着改后端。
11. **不做**工作流编辑器、逐帧实时预览、多用户、云渲染。

---

## 1. 目标与定位

### 1.1 一句话定位

**MiniMax Studio = AI 视频导演工作台。** 核心是一个「分镜工作台」页面：看到故事 → 找到镜头 → 改镜头 → 生成 → 看结果 → 继续拍下一镜，全程不跳页。ComfyUI 只当无头渲染后端。

> 不是「项目管理后台」，不是「提示词编辑器」，不是「剪映模仿」。Scene 是故事单位，Shot 是视觉单位，Asset 是世界观资产，继承是场景一致性引擎。

用户操作流程（目标态）：

```
① 创建项目《赛博都市》
② 建素材库：拖入 林雪.png / 霓虹街区.png / 手机.png → AI 建立资产
③ 建 Scene 01：设默认素材（林雪 + 霓虹街区 + 夜晚 + 下雨）
④ 连加镜头：01 城市全景 / 02 穿过高楼 / 03 数据塔……
⑤ 每镜只写自己独有的内容，系统自动继承人物/场景/天气/风格/声音
⑥ 点「生成」→ 前端组装 timeline_data → ComfyUI 队列 → MiniMax H3 → 视频
⑦ 成片页：预览 / 重新生成 / 超分 / 合并 / 导出
```

### 1.2 为什么现在可行（技术落点）

现有 `MiniMaxH3Director` 的 timeline JSON 已经是完整的「项目文件」——`scenes`/`segments`/`assets.cast/locations/props/styles`/`continuityMode`/`smartTail`/`prompt_batch`/`output` 全在。缺的只是一个人能直接操作的壳（SPA 前端）。后端生成管线、段缓存、进度事件、单镜下载全部现成，**无需重写生成逻辑**。

### 1.3 三大架构决策（已定）

| 决策点 | 结论 | 理由 |
|---|---|---|
| 前端形态 | **独立 SPA**（Vue 3 + TypeScript + Vite） | 大量状态（项目/场景/镜头/素材/队列/预览），管理型 UI 适合 Vue；纯 JS 难维护；浏览器可关，ComfyUI 服务必须跑 |
| 项目存储 | **真正项目层**，独立于 workflow widget | timeline 塞在 widget 里没有"项目"概念；素材库不应和 workflow 绑定 |
| 数据流 | **前端项目模型 → Director Core → Adapter → timeline_data → ComfyUI** | 前端不依赖 timeline_data 内部结构；换模型（Wan/LTX）只换 Adapter |
| 交互形态 | **单「分镜工作台」为核心**，其余是辅助页 | 视频创作的动线是"故事→镜头→生成→结果"一条链，跳页即打断创作；用户 70% 时间停留在工作台 |

---

## 2. 现有节点能力盘点

> 本节是四份代码盘点的综合结论（已逐文件核实），是 Adapter 层与 UI 层的**唯一权威依据**。

### 2.1 timeline JSON 顶层结构（前端序列化载体）

载体：节点 `timeline_data` widget（STRING multiline，`JSON.stringify(buildTimelinePayload())`）。**JSON 没有 version 门控**（grep 确认无 `version` 强校验），靠 `timelineMode` + `taskType` 路由。

```
timeline_data = {
  version: 5 | 4,
  timelineMode: "prompt_batch" | "fl2v" | "video" | "gen_blank" | "gen_image" ...,
  editMode: "global" | "segment",
  totalFrames, frameRate,
  video: {...}, videoClips: [...],        // 仅 v2v/rv2v 源视频路径
  global: {...},                          // 全局提示词/refs/参考音视频
  output: {...},                          // 生成/导出/连续性/智能参数
  gen: { defaultFrameCount },             // 仅 gen 模式
  scenes: [...],                          // 场景块
  segments: [...],                        // 镜头块
  assets: { cast, locations, props, styles },  // 全局素材库
  runSelectEnabled, runSelection,         // 选择运行
}
```

- `version`：fl2v/prompt_batch/gen → **5**；video → **4**。读入不做严格门控，只做字段归一化。
- `timelineMode`：`"image_batch"` 读入时归一化为 `"prompt_batch"`。
- **采样/生成参数不在 JSON 里**：`seed/steps/sampler/scheduler/shift_video/shift_audio/clear_vram_between_segments/export_source_images` 是节点原生 widget（高级面板折叠），独立前端必须把它们建模为**节点参数**而非 timeline 字段。

### 2.2 global 块字段

| 字段 | 类型 | 说明 |
|---|---|---|
| taskType | string | 全局任务类型（"t2v — 文生视频" 等，来自 `lib/task_prompts.py`） |
| prompt | string | 全局提示词 |
| refs | [{index, imageFile, fileName, type, subfolder}] | 全局参考图 |
| refAudios | [{index, audioFile, fileName, type, subfolder, durationSec}] | 全局参考音频 |
| referenceVideo | {videoFile, fileName, type, subfolder} | 参考视频（旧顶层迁移到此） |
| continuousReference | bool | ads2v 连续参考 |
| genImage | {imageFile} | gen 模式源图 |
| sourceWidth/sourceHeight | int | i2i/i2v 源图尺寸 |

### 2.3 output 块字段（全局生成配置）

| 字段 | 默认 | 说明 |
|---|---|---|
| mode | "long_edge" | "fixed"/"long_edge" |
| aspectRatio | "16:9 (宽屏)" | "Custom" 时存 custom |
| megapixels | 0.4 | 画质档 |
| multiple | 32 | 画布对齐系数 |
| longEdge | 848 | 长边 |
| width / height | 848 / 480 | 固定尺寸 |
| maxExportFrames | 0 | 导出帧上限 |
| exportMode | "scene" | scene/movie/segments/all |
| audioMode | "generate" | generate/source/mute |
| continuityEnabled | false | 仅 fl2v 有效（非 fl2v 强制清零） |
| continuityOverlapFrames | 9 | 1–81 |
| qwenVlEnabled / qwenVlLevel | false / 1 | Qwen 视觉反馈 |
| r2vAutoContinuity | true | r2v 自动续接（仅显式 false 关） |
| globalAssetsEnabled | true | 全局资产库 |
| stateTrackingEnabled | true | 状态跟踪 |
| smartTailEnabled | true | 智能尾帧 |
| handoffMode | "image" | fl2v 交接 "image"/"video" |

### 2.4 scenes 块字段

```
{ id: "sc...", name: "Scene 01", location: "霓虹街区", time: "夜晚",
  order: 0, assets: { cast: [], locations: [], props: [], styles: [] },
  defaultCastId: "", defaultLocationId: "" }
```

注意：场景**没有** `defaultProps`、`description` 字段（设计文档里猜的字段不存在）。props/styles 没有默认引用，直接整组作为 extra_assets 注入。

### 2.5 segments 块字段（prompt_batch 显式白名单，22 项，前后端契约）

```
id, start, length, frameCount, durationSec, prompt, negativePrompt, taskType,
refs, refAudios, refVideos, genImage, castId, locationId, castManual,
locationManual, stateChange, smartTail, sceneId, continuityMode, consistencyCheck
```

- `durationSec` 是**真相源**；`frameCount`/`length` 是它对齐 17k+5 网格后的镜像。
- `taskType: "auto"` 时前端删除该字段。
- `smartTail`/`consistencyCheck` 三态：`undefined/"auto"`→跟随全局；`true`→强制；`false`→关闭。
- `continuityMode`：`auto`/`none`/`ref2va`/`fl2va`。
- `castId/locationId`：显式 `""` = 用户选「无」（不走继承）；`undefined` = 走后端匹配。
- 每段参考素材上限：**9 图 / 3 音频 / 3 视频**。
- fl2v 段额外：`isStartFrame, isEndFrame, shotIndex, endImage` + 顶层 `shots`/`keyframes` 镜像。

### 2.6 assets 块字段（全局素材库）

```
{ cast: [...], locations: [...], props: [...], styles: [...],
  defaultCastId: "", defaultLocationId: "" }
```

- **`locations`（复数）是规范键**；旧 `location` 单数自动迁移回退。
- 资产对象：`{ id, name, imageFile }`。
- 三级层级：段候选池 = **场景素材组优先 → 全局兜底**（id 去重）。

### 2.7 后端解析规则（Adapter 必须遵守）

- **命名约定**：几乎每个字段 camelCase 主 + snake_case 兜底，读取顺序 camel 优先。Adapter 写死 camelCase，但要兼容旧 snake_case 数据。
- **资产命名自动匹配**：仅段未显式给 castId/locationId 时启用；在 prompt 里**纯子串包含**（`name in text`），只匹配名字长度 ≥2 的资产。优先级链：`显式 castId/locationId > 提示词 @匹配 > 场景默认 > 全局默认`。仅 r2v 段生效。
- **关键词路由**：约 40 个中文强连续动作词（开门/拔剑/奔跑/穿过/走向/冲进…）。`continuity_mode=="auto"` 且 r2v 段 prompt 命中 → task_key 改写 `fl2v`；`"fl2va"` 直接强制 fl2v；`"none"` 不参与。
- **三态解析**：缺失/"auto"/""→None；"on"/"true"/"1"→True；"off"/"false"/"0"→False。
- **开关默认值不对称**：`r2vAutoContinuity/globalAssetsEnabled/stateTrackingEnabled/smartTailEnabled` 缺省即开；`qwenVlEnabled/qwenVlKeySegments` 缺省即关。
- **导出模式归一**：`segments`(segment/per_segment/by_segment/shot/shots)、`scene`(scenes/by_scene/per_scene)、`movie`(stream/streaming/mp4/files/per_file/video_files/full/full_movie)、其余归 `all`。
- **选择运行**：`runSelectEnabled` + `runSelection` 索引列表；开启但没勾任何段 → 后端 ValueError；勾全部 → 等价全部运行。
- **最小段帧数**：导出裁剪不足 4 帧的段并入前段（`MIN_SEGMENT_FRAMES=4`）。

### 2.8 节点 API 面

**输入 widget**（required）：`model/video_vae/audio_vae/clip`（模型连接口）+ `task_type/global_prompt/cfg/seed/frame_rate/width/height/ref_max_size/total_frames/timeline_data`。
**输入 widget**（optional，全部折叠）：`steps/sampler/scheduler/shift_video/shift_audio/clear_vram_between_segments/export_source_images`。
**hidden**：`unique_id`（= node_id，贯穿缓存目录/进度/状态灯）。

**输出**：`images(list IMAGE) / audio(list AUDIO) / fps(FLOAT) / frame_count(INT) / source_images(list IMAGE) / report(STRING)`。

**执行链**：`execute()` → `prepare_director_plan`（发 plan 事件）→ `build_director_plan`（`json.loads(timeline_data)`，plan.py:684）→ `execute_director_plan_core(plan, node_id=unique_id, ...)` → `finalize_director_outputs`。

### 2.9 HTTP 路由（独立 SPA 可直接调，无鉴权）

| 路由 | 说明 |
|---|---|
| `POST /minimax/director/upload_chunk` | 分片上传源视频，落盘 input 目录 |
| `GET|POST /minimax/director/probe_video` | 视频探测 |
| `POST /minimax/director/detect_shots` | PySceneDetect 镜头切分 |
| `GET /minimax/director/segment_cache_status?node_id=` | 已缓存段索引 |
| `GET /minimax/director/segment_status?node_id=` | 状态灯（cached + transient states） |
| `GET /minimax/director/segment_mp4?node_id=&index=&fps=` | 单镜 mp4 下载（无缓存 404） |

**关键缺口**：没有「读取/写回 timeline_data」的 HTTP 接口。独立 SPA 要么自己维护项目文件（本蓝图采用），要么从 `/api/prompts` 解析节点 widget。

### 2.10 WS 事件（进度/预览）

- `minimax_director_progress`：`node_id, segment(1基), segment_total, timeline_segment, timeline_segment_total, partial_run, phase(prepare/context_encode/sample/decode + plan/finish), phase_label, phase_value, phase_max, overall_value, overall_max, remaining_segments, frames_label, task_key`。
- `minimax_director_preview`：`node_id, segment_index(0基), image_b64, width, height, frames[], fps`。
- `minimax_director_enhanced`：AI 增强提示词回调。

**⚠️ 关键坑**：`send_sync(event, payload, srv.client_id)` 只发给**最后连接的客户端**。外部 SPA 与 ComfyUI 自带前端会互相挤掉 WS 槽位（只有最后一个能收事件）。通信层必须处理（见 §6.2）。

### 2.11 后端执行管线要点

- **单进程串行**：一个 plan 执行期间 worker 被阻塞，无取消原语，`sample_single_stage` 中途不可中断。前端应轮询/订阅而非实时等待，长任务排队。
- **段缓存**：`{ComfyUI output}/minimax_seg_cache/{node_id}/seg_%04d.pt`（`[F,H,W,3] float`）+ `.meta.json`（fingerprint）+ `.aud.pt`（可选）。指纹不匹配 → 缓存失效 → 重生成。写缓存 best-effort（失败只 warn）。
- **导出 4 模式**：`segments`（不合并）/`all`（内存合并）/`scene`（SceneNN.mp4）/`movie`（场景 mp4 + ffmpeg 直拼 Movie.mp4）。每镜 mp4 `Director_SceneNN_ShotMM.mp4` 必落盘（三层内存防线兜底）。
- **进度阶段**：每段 `prepare → context_encode → sample → decode`，Qwen 三级反馈（L1 状态提取 / L2 一致性只报告 / L3 高严重度 + 规则双确认才重跑）。
- **运行时状态不落盘**：`running/review/failed` 进程内存，重启即丢；「成功」由磁盘缓存推导。
- **显存**：`clear_vram_between_segments=True` 时每段 context_encode 后卸载模型（多段）→ 换速度省显存；Qwen 前错峰清理。

### 2.12 前端已有 UI 能力（可平移/借鉴）

| 能力 | 现状 | 对 SPA 的意义 |
|---|---|---|
| Scene+Shot 导航 | 场景胶囊栏 + 镜头胶囊条 | 验证了 Scene/Shot 分层方向 |
| 状态灯 | 未生成/生成中/成功/待检查/失败五态 | 成片页直接复用逻辑 |
| 单镜下载 | r2v/fl2v 卡片「下载本镜」 | 成片页复用 |
| 三级资产层级 | 场景素材组优先 → 全局兜底 | 素材库/镜头设置复用 |
| 场景默认素材继承 | `defaultCastId/LocationId` | 「场景默认素材」已实现 |
| Prompt+衔接视觉中心 | 衔接徽章 + Prompt 分栏 | 结构化 Prompt 编辑器起点 |
| 生成操作下拉 | 6 范围（全部/当前镜/选中/当前场景/未完成/失败） | 任务队列范围语义复用 |

---

## 3. 目标架构

### 3.1 总架构图（v1.1 加 Director Core）

```
                         MiniMax Studio
┌────────────────────────────────────────────────────┐
│              创作层 Creative Layer                 │
│                                                    │
│  Project / Episode / Scene / Shot / Asset          │
│  （面向用户的数据模型，无 H3 概念）                  │
└──────────────────────┬─────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────┐
│                Director Core  ★v1.1 新增★         │
│                                                    │
│  Prompt Builder       （结构化 → 完整 H3 Prompt）   │
│  Asset Resolver       （@资产识别/命中/绑定）       │
│  Inheritance Resolver （显式>@匹配>场景默认>全局）   │
│  Timeline Builder     （Shot → timeline_data 结构） │
│  Render Planner       （范围选择 → runSelection）   │
└──────────────────────┬─────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────┐
│                    Adapter（薄翻译）                │
│                                                    │
│  MiniMax H3       Wan       LTX       Other        │
└─────────┬─────────────┬──────────────┬─────────────┘
          ↓             ↓              ↓
┌────────────────────────────────────────────────────┐
│                  ComfyUI API                       │
└──────────────────────┬─────────────────────────────┘
                       ↓
                     GPU
```

**为什么必须有 Director Core**：创作逻辑（@资产解析、继承链、Prompt 组装、镜头→timeline 映射）是**模型无关**的。如果全塞进 `MiniMaxH3Adapter`，换 Wan/LTX 时要么复制一堆逻辑、要么变成一个 2000 行的怪物。Core 层持有业务规则，Adapter 只把 Core 的输出翻译成具体模型的 ComfyUI prompt 结构。

### 3.2 四大原则

1. **项目模型独立**：前端持有自己的 Project/Episode/Scene/Shot/Asset 数据模型，**绝不直接把 timeline_data 当数据库**。timeline_data 只是 Adapter 的输入输出格式。
2. **Director Core 承载业务逻辑**：@资产解析、继承链、Prompt 组装、镜头→timeline 映射全部进 Core 层，模型无关。
3. **Adapter 薄翻译**：`MiniMaxAdapter`（H3）只做「Core 输出 → ComfyUI prompt 结构」的翻译；将来 Wan/LTX 各写一个 Adapter，前端与 Core 零改动。
4. **SPA 形态 + 单工作台**：静态托管（Vite build 产物），ComfyUI 只当无头渲染后端。浏览器可关，服务端必须跑。交互以分镜工作台为核心，其余页面辅助。

---

## 4. 项目文件 Schema

### 4.1 目录结构

```
Projects/
└── CyberCity/                        # 项目根（project.json 所在）
    ├── project.json                  # 项目元数据 + 全局设置
    ├── assets/
    │   ├── cast/                     # 人物资产（每资产一个 JSON + 图片）
    │   │   ├── CHAR_001.json
    │   │   ├── CHAR_001.png
    │   │   └── ...
    │   ├── locations/                # 场景资产
    │   ├── props/                    # 道具资产
    │   └── styles/                   # 风格资产
    ├── episodes/
    │   ├── episode_01/
    │   │   ├── episode.json          # 集内 scenes + shots + 引用
    │   │   ├── shots/                # 每镜独立 JSON（便于 git diff / 并行编辑）
    │   │   │   ├── shot_001.json
    │   │   │   └── ...
    │   │   └── output/               # 本集成片输出（mp4/图片/报告）
    │   └── episode_02/
    └── renders/                      # 全片合并输出
```

### 4.2 project.json

```jsonc
{
  "schema": "minimax-studio/project",
  "schemaVersion": 1,
  "name": "赛博都市",
  "createdAt": "2026-08-09T12:00:00+08:00",
  "updatedAt": "2026-08-09T12:00:00+08:00",
  "defaults": {
    "frameRate": 24,
    "width": 864,
    "height": 480,
    "longEdge": 848,
    "mode": "long_edge",
    "megapixels": 0.4,
    "aspectRatio": "16:9 (宽屏)",
    "exportMode": "scene",
    "audioMode": "generate"
  },
  "engine": {
    "backend": "minimax-h3",          // Adapter 选择
    "nodeType": "MiniMaxH3Director",  // 提交给 ComfyUI 的节点类名
    "workflow": { }                    // 预留：任意 workflow 模板引用
  },
  "episodes": ["episode_01", "episode_02"]
}
```

### 4.3 episode.json（v1.1：Scene 强化 + ShotModel 去 H3 化）

```jsonc
{
  "id": "episode_01",
  "name": "第一集",
  "order": 1,
  "scenes": [
    {
      "id": "sc01",
      "name": "霓虹街区",
      "order": 0,
      // --- 世界观/场景一致性核心 ---
      "location": "霓虹街区",
      "time": "夜晚",
      "weather": "下雨",
      "description": "狭窄的霓虹街道，两侧高楼林立，全息广告牌闪烁。",
      "referenceImage": "assets/locations/LOC_001.png",
      "defaultCastId": "CHAR_001",
      "defaultLocationId": "LOC_001",
      "defaultProps": ["PROP_001", "PROP_002"],
      "defaultStyleId": "STYLE_cyberpunk",
      "assetGroup": { "cast": [], "locations": [], "props": [], "styles": [] }
    }
  ],
  "shots": [
    {
      "id": "shot_001",
      "sceneId": "sc01",
      "order": 1,
      // --- 内容（创作层，用户直接写） ---
      "content": {
        "prompt": "城市全景，雨夜霓虹。",
        "negativePrompt": "",
        "durationSec": 9
      },
      // --- 视觉资产（引用，不存 H3 字段名） ---
      "assets": {
        "castId": "CHAR_001",
        "locationId": "LOC_001",
        "props": ["PROP_001"],
        "styleId": "STYLE_cyberpunk",
        "manual": { "castId": false, "locationId": false }  // true=用户覆盖（对应后端 castManual/locationManual）
      },
      // --- 摄影（Prompt Builder 输入） ---
      "camera": { "shotSize": "中景", "movement": "跟拍", "speed": "缓慢", "depthOfField": "浅景深" },
      // --- 声音（Prompt Builder 输入） ---
      "sound": { "ambience": "雨声", "music": "低沉电子" },
      // --- 参考素材（per-shot refs） ---
      "refs": { "images": [], "audios": [], "videos": [] },
      // --- 生成参数（★所有 H3 特有概念收进这里，换模型只改这层） ---
      "generation": {
        "taskType": "auto",
        "continuity": { "mode": "auto" },
        "smartTail": "auto",
        "consistencyCheck": "auto",
        "stateChange": ""
      },
      // --- 渲染状态（持久层只存结果摘要，运行时态不落盘） ---
      "render": {
        "lastPromptHash": "",
        "lastRenderAt": null,
        "output": "",
        "status": "draft"
      }
    }
  ]
}
```

**v1.1 重构要点**：
- Scene 增加 `weather/description/referenceImage/defaultProps/defaultStyleId`——它是「世界观/场景一致性管理器」，不是普通分组。
- Shot 的 `smartTail/continuityMode/castManual/locationManual/stateChange` 等 H3 概念**全部收进 `generation` 子对象**，`assets.manual` 对应后端 `castManual/locationManual`。前端模型面向内容创作，不面向模型参数。
- `camera/sound` 是新增的结构化字段，喂给 Prompt Builder 合成 H3 完整格式。

**设计取舍**：shots 平铺在 episode.json（vs 每镜一文件）。v1 用单文件（简单、加载快）；当镜头 >50 时再拆 `shots/shot_NNN.json`（迁移成本低，Adapter 不在乎存储形态）。

### 4.4 资产对象

```jsonc
{
  "id": "CHAR_001",
  "kind": "cast",                    // cast/location/prop/style
  "name": "林雪",
  "aliases": ["女主", "小雪"],       // @匹配用
  "imageFile": "assets/cast/CHAR_001.png",
  "description": "年轻女性，黑色长发，白色外套",
  "createdAt": "...",
  "usageCount": 17,                  // 统计：被哪些镜引用（只读，派生存档）
  "usedInShots": ["ep01:shot_001", "ep01:shot_002"]
}
```

### 4.5 存储层选型（决策点）

| 方案 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| **文件系统 JSON**（上述） | 透明可 git、零依赖、离线 | 无索引、并发写要自己管 | ✅ v1 采用 |
| SQLite（better-sqlite3） | 查询强、原子事务 | 项目文件不可读、需迁移 | 镜头 >200 再上 |
| IndexedDB | 浏览器本地 | 不可备份/不可 git | 不用 |

文件系统 JSON 落盘位置：**ComfyUI input 目录旁边**的独立目录（如 `{ComfyUI}/input/minimax_studio/Projects/`），或用户指定任意目录。v1 建议放 `{ComfyUI}/input/minimax_studio/`——与上传资产同盘，备份即拷整个目录。

### 4.6 Shot 状态模型（v1.1 新增）

Shot 状态分两层，**严格分离**：

```
运行时状态（不进项目 JSON，内存/磁盘缓存推导，重启即丢）
  draft → queued → generating → success
                        └→ review / failed

持久状态（进 project/episode JSON 的 shot.render，只存结果摘要）
  {
    "lastPromptHash": "...",   // 指纹：改参数即失效 → UI 提示需重生成
    "lastRenderAt": "ISO...",
    "output": "ShotNN.mp4 | scene 路径",
    "status": "success"        // 只存终态：draft/success/failed/review
  }
```

- **「成功」的唯一权威 = 磁盘段缓存 `seg_%04d.pt` 存在**（跨重启保留），UI 打开项目时从 `GET /segment_status` 推导，不读 JSON。
- `queued/generating/72%/phase=sample` 这类**永远不进 JSON**——是运行时任务中心的状态。
- `lastPromptHash` 用途：镜头参数改了但没重新生成时，状态灯显示「⚠ 参数已变需重生成」，防止用户以为旧成果仍然有效。

### 4.7 @资产与继承（v1.1 核心交互）

**@资产识别**（Prompt Builder 的前端部分）：
```
用户输入：@林雪走进@霓虹街区，手里拿着@手机
  ↓ 实时扫描 prompt，命中资产库 name/aliases
  ↓ 高亮 + 右侧「本镜头使用资产」面板：
      👩 林雪   → CHAR_001.png
      🌆 霓虹街区 → LOC_001.png
      📱 手机   → PROP_001.png
  ↓ 确认后写 shot.assets.castId/locationId/props
```

**继承可视化**（Inheritance Resolver 的前端部分）：镜头资产条目显示来源链，把后端逻辑变用户可见：
```
Shot 03 人物：👩 林雪   ↑ 来自 Scene 01     （场景默认）
Shot 03 地点：🌆 霓虹街区 ↑ 来自 Scene 01
Shot 03 风格：🎨 Cyberpunk ↑ 来自 Project    （全局默认）
Shot 05 人物：👩 林雪   ⚡ Shot 覆盖          （用户手动改过 → manual=true）
```
后端继承链（显式 castId/locationId > @匹配 > 场景默认 > 全局默认）在 UI 上以「来源徽标」形式呈现，用户永远知道**为什么这个镜头用了这个人**。

---

## 5. Director Core + Adapter 双层设计（v1.1 拆分）

> v1 把业务逻辑全放 Adapter。v1.1 拆成两层：**Director Core**（模型无关的业务规则） + **Adapter**（模型相关的薄翻译）。前端只依赖 Core 的接口。

### 5.1 Director Core（模型无关，前端唯一依赖）

```ts
// ── 创作层数据模型（§4，无 H3 概念） ──
interface ProjectModel { ... }
interface EpisodeModel { scenes: SceneModel[]; shots: ShotModel[]; ... }
interface SceneModel { ... }   // 世界观/一致性管理器
interface ShotModel { ... }    // content/assets/camera/sound/refs/generation/render
interface AssetModel { ... }

// ── Director Core：业务规则全在这，换模型不碰 ──
interface DirectorCore {
  // @资产解析：扫描 prompt 里的 @名字，命中资产库 name/aliases → 资产引用
  resolveMentions(text: string, assets: AssetIndex): MentionHit[];

  // 继承解析：返回每个资产条目的最终来源链
  //   显式 shot.assets > @匹配 > 场景默认 > 全局默认
  resolveInheritance(shot: ShotModel, scene: SceneModel, projectDefaults): InheritedAsset[];

  // 继承可视化：来源徽标（↑Scene / ↑Project / ⚡Shot覆盖）
  inheritedSource(assetId: string, ctx: InheritanceCtx): "shot" | "scene" | "project" | "none";

  // Prompt 组装：结构化字段（camera/sound/content/assets）→ 完整提示词
  buildShotPrompt(shot: ShotModel, inherited: InheritedAsset[]): ShotPrompt;
  //  → { integratedMultimodalDescription, overallSoundscape, nonDiegeticMusic, negative }
  //  简洁模式：只给一句话；高级模式：完整影视级三段。两者同源，由 buildShotPrompt 派生。

  // 镜头→timeline 结构：ShotModel 列表 → timeline 所需中间结构（段/场景/资产块）
  buildTimelineStructure(ep: EpisodeModel): TimelineStructure;

  // 渲染规划：范围选择 → runSelection 索引
  planRenderScope(ep: EpisodeModel, scope: RenderScope): RenderPlan;
}

interface MiniMaxH3Adapter implements DirectorAdapter { ... }  // 见 5.2
interface WanAdapter implements DirectorAdapter { ... }
interface LTXAdapter implements DirectorAdapter { ... }
```

**Core 与 Adapter 的分工边界**：
- Core 产出 `TimelineStructure`（镜头/场景/资产块的**语义结构**）——不含任何 H3 字段名。
- Adapter 把 `TimelineStructure` 翻译成具体模型的 `timeline_data` JSON（含 `prompt_batch` 22 字段白名单、`continuityMode/smartTail` 等 H3 命名）。
- Core 的 `buildShotPrompt` 产出**模型无关的提示词结构**（画面/摄影/风格/声音/负面），Adapter 负责拼成 H3 的 `integrated_multimodal_description/overall_soundscape/non_diegetic_music` 格式。

### 5.2 Adapter（薄翻译）

```ts
interface DirectorAdapter {
  readonly backend: string;                 // "minimax-h3" | "wan" | "ltx"
  readonly nodeType: string;                // ComfyUI 节点类名

  // Core 的 TimelineStructure → 具体模型 timeline_data 字符串
  buildTimelineData(ctx: AdapterContext, structure: TimelineStructure): string;

  // 节点原生 widget 参数（采样等不在 timeline JSON 里的）
  buildNodeWidgets(ctx: AdapterContext, ep: EpisodeModel): Record<string, unknown>;

  // 事件 → 前端运行时状态
  parseProgressEvent(detail: unknown): RenderProgress;
  parsePreviewEvent(detail: unknown): RenderPreview;
}
```

Adapter 职责**只有**：字段命名映射（camelCase、`locations` 复数、`durationSec`→`frameCount` 网格化、三态序列化）、ComfyUI prompt 结构组装、事件解析。**不做** @资产解析、不做继承、不做 Prompt 内容组装。

### 5.3 Core 输出 → Adapter 翻译映射规则

**前端从不直接拼 timeline JSON。** 数据流：`ShotModel → DirectorCore.buildTimelineStructure → TimelineStructure → Adapter.buildTimelineData → timeline_data`。

映射要点（翻译职责在 Adapter，规则由 §2 盘点而来）：

1. **顶层**：`version=5`（prompt_batch/fl2v）或 `4`（video）；`timelineMode` 由集内任务混合决定（有 fl2v 段 → "fl2v"，否则 "prompt_batch"）；`editMode="segment"`（SPA 都是段级编辑）。
2. **global**：`taskType` = 集默认任务；`prompt` = 集全局提示词（可选）；`refs/refAudios` 从集级参考素材映射。
3. **output**：project.json 的 `defaults` + 集内覆盖（exportMode/audioMode/连续性/智能尾帧/Qwen 开关）。
4. **scenes**：episode.json 的 scenes → timeline.scenes，`assets` 键统一 `locations`（复数）。
5. **segments**：ShotModel → timeline.segments，逐字段映射（§2.5 白名单），时长统一 `durationSec` + 由 Adapter 算 `frameCount/length`（对齐 17k+5 网格，上限 512）。
6. **assets**：episode 引用的资产 → timeline.assets（cast + locations；props/styles 前端持有、通过 per-shot refs 注入，因后端只解析 cast/locations 全局 + 场景四类）。
7. **runSelectEnabled/runSelection**：由 `planRenderScope` 的输出生成。

### 5.4 原生 widget 参数建模（Adapter 的隐藏职责）

采样/显存参数不在 timeline JSON，是节点原生 widget。Adapter 必须同时输出：

```
buildTimelineData()  → timeline_data widget value
buildNodeWidgets()   → { cfg, seed, steps, sampler, scheduler, shift_video, shift_audio,
                         clear_vram_between_segments, export_source_images,
                         frame_rate, width, height, ref_max_size, total_frames }
```

独立 SPA 提交任务时，这两个一起组成节点 prompt 的 widget 输入（§6.3）。

### 5.5 命名与迁移坑（Adapter 内部消化，UI 无感）

| 坑 | 处理 |
|---|---|
| camelCase vs snake_case | 写只写 camel；读兼容 snake（`getField(obj, "r2vAutoContinuity", "r2v_auto_continuity")`） |
| assets.location(s) | 统一写复数 `locations` |
| ref 槽位 index vs slot | 统一 index |
| durationSec vs frameCount/length | durationSec 是真相，frameCount 由 Adapter 派生 |
| continuityEnabled 仅 fl2v | 非 fl2v 集自动清零 |
| taskType "auto" | 序列化时删除该字段（让后端路由） |
| smartTail/consistencyCheck 三态 | 序列化时 undefined 删除、true/false 保留 |
| 关键词路由 | 交给后端（段 continuityMode=auto 即可） |
| 老数据迁移 | `migrate(timelineData)` 工具：旧 version4/video 数据 → 新模型，读盘时做 |

---

## 6. 通信层设计

### 6.1 ComfyUI API 对接清单

| 用途 | ComfyUI 端点 | 说明 |
|---|---|---|
| 列出已连模型/节点 | `GET /object_info` | 校验 Adapter.nodeType 存在、拿到 task_type COMBO 选项 |
| 提交任务 | `POST /prompt` | 构造 `{prompt: {node_id: {class_type, inputs}}, client_id}` |
| 排队状态 | `GET /queue` | 当前队列 |
| 历史/结果 | `GET /history/{prompt_id}` | 取 report、images、frame_count |
| 断线重连 | `GET /ws?clientId=` | WS 进度/预览 |
| 资产上传 | 复用 `POST /minimax/director/upload_chunk` | 源视频分片 |
| 单镜下载 | `GET /minimax/director/segment_mp4` | 已缓存镜直接出片 |
| 状态灯 | `GET /minimax/director/segment_status` | 轮询兜底 |

### 6.2 WS 单槽问题（必须解决）

**问题**：`send_sync(..., srv.client_id)` 只把 Director 进度事件发给**最后连接**的 WS 客户端。SPA 打开后，ComfyUI 自带前端收不到 Director 进度；反过来如果用户又开了 ComfyUI 前端，SPA 就收不到。

**方案（推荐组合）**：
1. **SPA 作为唯一活跃 WS 客户端**：打开 SPA 即连 `/ws?clientId=<uuid>`，保持活跃。用户可关掉 ComfyUI 浏览器（本就符合"无头"定位）。
2. **轮询兜底**：SPA 用 `GET /queue` + `GET /minimax/director/segment_status` 每 2-5s 轮询，即使 WS 被挤掉也能显示进度/状态灯。预览帧（`minimax_director_preview`）丢失时回退「生成中」动画。
3. **(可选) 后端补强**：把 Director 进度事件也写到 `segment_status.py` 那样的进程内存，加一个 `GET /minimax/director/run_progress?node_id=` 路由返回最近 progress payload——彻底摆脱 WS 槽位竞争。

### 6.3 任务提交流（SPA 侧）

```
用户点「生成 Shot 07」
  → 组装 ProjectModel（project + episode + shot 全量）
  → MiniMaxH3Adapter.buildTimelineData() + buildNodeWidgets()
  → 构造 ComfyUI prompt：
      {
        "<node_id>": {
          "class_type": "MiniMaxH3Director",
          "inputs": {
            "model": ["4", 0], "video_vae": ["5", 0], "audio_vae": ["6", 0], "clip": ["7", 0],
            "task_type": "...", "global_prompt": "...", ...原生 widget,
            "timeline_data": "<adapter 输出 JSON 字符串>"
          }
        }
      }
  → POST /prompt，拿 prompt_id
  → 订阅 WS 进度 + 轮询 segment_status
  → 完成后：单镜 mp4 走 segment_mp4；成片走 /history 拿 images/report
```

**关键点**：`<node_id>` 是工作流里 Director 节点的数字 id。SPA 需要一个「工作流模板」概念——内置一个默认工作流（Director + 上游模型节点），节点 id 固定或可配置。这是**项目层 engine.workflow 字段**的作用。

### 6.4 任务队列与渲染调度

- **ComfyUI 队列串行**，SPA 的任务队列只是「愿望列表」：多个镜头依次排队提交，逐个跟踪 `prompt_id`。
- **范围语义**复用现有 6 范围（全部/当前镜/选中/当前场景/未完成/失败）——每次提交前由 Adapter 计算 `runSelection`。
- **缓存感知**：提交前查 `segment_status`，已成功且指纹未变的镜头**自动跳过**（除非用户选「重新生成」），避免浪费。
- **中断**：ComfyUI 无取消原语。SPA 可提供「停止后不再提交后续」，已在队列中的无法中断（提示用户）。

---

## 7. 交互设计：一个「分镜工作台」为核心（v1.1 重写）

> v1 是五页面导航。v1.1 改为**单工作台**：用户 70% 时间停留在分镜工作台，其余页面是辅助入口（导航保留，但重要创作功能不进设置/成片）。

### 7.1 分镜工作台（核心页面，第一版只做这个）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ MiniMax Studio   赛博都市 / 第一集                         💾已保存  ▶生成 │
├────────────┬──────────────────────────────────────────────┬─────────────────┤
│            │                                              │  当前镜头       │
│ 项目结构    │             当前镜头播放器                   │                 │
│            │                                              │  Shot 03        │
│ 📁 第一集   │             ┌──────────────────────┐          │  00:09          │
│  ├ Scene01 │             │                      │          │                 │
│  │  ├ 01   │             │       VIDEO          │          │ 场景            │
│  │  ├ 02   │             │                      │          │ [霓虹街区 ▼]    │
│  │  └ 03 ● │             └──────────────────────┘          │                 │
│  ├ Scene02 │                                              │ 人物            │
│  │  ├ 04   │            ▶  00:04 / 00:09                  │ [林雪 ▼]        │
│  │  └ 05   │                                              │                 │
│            │                                              │ 地点            │
│ 🧩 素材     │                                              │ [霓虹街区 ▼]    │
│  👤 人物    ├──────────────────────────────────────────────┤                 │
│  🌆 场景    │  Scene 01                                    │ 风格            │
│  📦 道具    │  [01✓][02✓][03▶][04○][05○][+]               │ [赛博朋克 ▼]    │
│  🎨 风格    │                                              │                 │
│            │  0s     9s     18s     27s     36s             │ Prompt          │
│            │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ [结构化编辑区]  │
│            │                                              │ [简洁|高级]     │
│            │                                              │                 │
│            │                                              │ 衔接 [自动 ▼]   │
│            │                                              │ 参考 [🖼][🖼][+]  │
├────────────┴──────────────────────────────────────────────┴─────────────────┤
│ ⚡ 任务中心  Shot 03 生成中 ████████████░░ 72%  Sample 156/226   Shot 04 排队 │
└─────────────────────────────────────────────────────────────────────────────┘
```

**四区职责**：
- **左·项目结构**：故事树（Scene 是故事单位）+ 素材库快捷入口。Scene 下是 **Shot 缩略图卡片**（🖼/▶ + 序号 + 名称 + 时长 + 状态灯），不是文字列表。
- **中·预览 + 时间线**：当前镜播放器（可播放已生成视频，未生成显占位）+ **镜头时间线**（镜头胶囊 + 状态灯 + 时长刻度，不是剪辑多轨）。
- **右·镜头设置**：场景/人物/地点/风格下拉（常显）+ 结构化 Prompt + 衔接 + 参考素材。**所有东西属于当前选中的 Shot**。
- **底·任务中心**：常驻，运行中/排队/已完成。

**交互动线**（核心目标）：看到故事 → 点缩略图选中镜头 → 改 Prompt/素材 → 点生成 → 原地看预览 → 下一个镜头。**全程不离开工作台。**

### 7.2 场景设置（世界观/一致性管理器）

Scene 是**一级核心概念**，点场景名打开场景面板：

```
SCENE 01  霓虹街区
┌──────────────────────────┐
│      场景参考图 🖼        │
├──────────────────────────┤
│ 地点    霓虹街区          │
│ 时间    夜晚              │
│ 天气    🌧 下雨           │
│ 默认人物  👩 林雪         │
│ 默认风格  🎨 Cyberpunk    │
│ 默认道具  📱 手机 / 🚕 出租车│
│ 场景描述  狭窄的霓虹街道…  │
└──────────────────────────┘
```

场景下所有镜头**自动继承**这些默认值（继承链：显式 > @匹配 > 场景默认 > 全局默认）。新镜头继承场景参考图 → 场景一致性成为产品能力，而非逐镜手填。

### 7.3 镜头设置：结构化 Prompt（v1.1 重点）

用户看到的编辑区（**不是** `integrated_multimodal_description` 大文本框）：

```
┌────────────────────────────────────┐
│ 🎬 画面                             │
│   林雪穿过雨夜的霓虹街区，镜头跟随她 │
│   向前移动。                       │
├────────────────────────────────────┤
│ 🎥 摄影                             │
│   景别[中景 ▼] 运动[跟拍 ▼]         │
│   速度[缓慢 ▼] 景深[浅景深 ▼]      │
├────────────────────────────────────┤
│ 🎨 风格                             │
│   [赛博朋克] [电影感] [浅景深]      │
├────────────────────────────────────┤
│ 🔊 声音                             │
│   环境[雨声] 音乐[低沉电子]         │
├────────────────────────────────────┤
│ 🚫 负面词                           │
│   text, logo, watermark            │
└────────────────────────────────────┘
        [ 简洁模式 ] [ 高级模式 ]
```

- **简洁模式**：一句人话（`女主走进雨夜霓虹街区，镜头缓慢推进`），可点「✨ AI 扩写」由 AI 生成完整描述。
- **高级模式**：显示 H3 完整三段（`integrated_multimodal_description / overall_soundscape / non_diegetic_music`），给精细控制。
- 两模式**同源**：`DirectorCore.buildShotPrompt` 从结构化字段派生，Adapter 负责拼成 H3 格式。

### 7.4 @资产识别 + 一致性绑定（核心交互）

```
输入：@林雪走进@霓虹街区，手里拿着@手机
  ↓ 实时扫描 + 高亮
本镜头使用资产         一致性
👩 林雪      CHAR_001  ✓ 人物 已绑定
🌆 霓虹街区  LOC_001   ✓ 场景 已绑定
📱 手机      PROP_001  ✓ 道具 已绑定
                       ✓ 风格 已继承
              [查看资产]
```

- 命中即写 `shot.assets.castId/locationId/props`，无需手动选下拉。
- 一致性面板显示**绑定状态**（人物/场景/道具/风格），继承来的标「已继承」，手动的标「本镜头覆盖」。

### 7.5 继承可视化（用户永远知道"为什么是这个"）

```
Shot 03 人物：👩 林雪   ↑ 来自 Scene 01      （场景默认）
Shot 03 地点：🌆 霓虹街区 ↑ 来自 Scene 01
Shot 03 风格：🎨 Cyberpunk ↑ 来自 Project     （全局默认）
Shot 05 人物：👩 林雪   ⚡ Shot 覆盖           （manual=true）
```

### 7.6 生成交互

- 主按钮 `▶ 生成` + 下拉范围：当前镜头 / 当前场景 / 选中镜头 / 所有未生成 / 所有失败 / 全部重新生成。
- **生成不跳页**：原地预览 + 进度（阶段 + 百分比 + 剩余帧）。完成后：`[重新生成] [下载] [替换]`。
- 右下角任务中心：运行中 1 / 排队 2 / 已完成列表（可点开看阶段明细）。长任务时用户继续编辑，任务中心实时更新。

### 7.7 辅助页（非核心，V1 之后做）

| 页 | 职责 | 级别 |
|---|---|---|
| 🎬 项目 | 项目列表 / 新建 / 集管理 / **剧本式概览**（场景卡片 + 人物地点统计 + 进度） | V1.5 |
| 🧩 素材库 | 四类资产 CRUD / 图片上传 / 描述别名 / 使用统计 | V1.5 |
| 🎥 成片 | 镜头结果网格 / 批量导出 / 合并 / 下载 | V1.5 |
| ⚙ 设置 | ComfyUI 地址 / 工作流模板 / 模型参数默认值 / 显存 / 输出目录 | V1.5 |

**成片页不承担创作**：批量查看/导出/合并走成片，单镜预览/下载/重新生成在工作台内完成。

---

## 8. 分阶段实施计划（v1.1 收缩为单页优先）

> v1.1 核心变化：**第一版只做分镜工作台一个核心页面**，做到极其好用，再谈扩展。
>
> 路线：**V1 项目→分镜→生成→视频** → **V1.5 素材库→@资产→继承** → **V2 Prompt 导演系统** → **V3 多模型 Adapter**。

### V1：分镜工作台（核心闭环，约 2-3 周）

**目标**：一个页面完成"选项目 → 看/改镜头 → 生成 → 原地看结果"。其余页面只给占位入口（路由 404 页写"V1.5 提供"）。

- [ ] **V1.0 脚手架 + 通信层验证（约 1 天）**
  - Vite + Vue3 + TS 脚手架；单页路由（工作台 `/workbench`）+ 占位入口。
  - ComfyUI 连接服务（API client）：`/object_info` 校验节点、`/prompt` 提交、`/queue`、`/history`。
  - WS 客户端：收 `minimax_director_progress`/`preview`；验证「SPA 最后连接即收事件」（§6.2 单槽问题）。
  - 用**现成 timeline_data**（从现节点导出）手动提交一次，跑通"SAP → ComfyUI → H3 → 视频 → 回传"。
  - **验收**：SPA 能提交一个现有工程并拿到成片。

- [ ] **V1.1 Director Core 骨架 + 项目文件层（2 天）**
  - `DirectorCore` 五模块接口（Asset Resolver / Inheritance Resolver / Prompt Builder / Timeline Builder / Render Planner）定签名 + 最小实现。
  - Project/Episode/Scene/Shot/Asset TS 类型 + JSON 存取服务（Projects/ 目录）。
  - 先支持**加载现节点导出的 timeline_data** 转成前端模型（导入通道），暂不做完整项目新建。
  - **验收**：能打开一个现有工程，Scene/Shot/Asset 分层渲染。

- [ ] **V1.2 工作台四区布局 + 镜头时间线（3-4 天）**
  - 左·项目结构树：Scene 故事单位 + Shot 缩略图卡片（🖼/▶ + 序号 + 名称 + 时长 + 状态灯）。
  - 中·当前镜播放器 + 镜头时间线（胶囊 + 状态灯 + 时长刻度）。
  - 右·镜头设置：场景/人物/地点/风格下拉（常显）+ 衔接/尾帧 + 参考素材（高级折叠沉底）。
  - 场景设置面板：参考图 + 地点/时间/天气/默认人物/默认风格/默认道具 + 场景描述。
  - **验收**：点缩略图切换镜头不丢状态；场景默认值能看得到。

- [ ] **V1.3 生成闭环 + 任务中心（3-4 天）**
  - **MiniMaxH3Adapter v1**：`buildTimelineData` + `buildNodeWidgets`（先只支持 r2v prompt_batch，fl2v/video 后置）。
  - 生成下拉 6 范围（当前镜/选中/当前场景/未完成/失败/全部重生成）+ 主按钮。
  - 原地预览：生成完成自动刷新当前镜；`[重新生成][下载][替换]` 就地操作。
  - 右下角任务中心：运行中/排队/已完成 + 阶段进度 + 预览帧。
  - 缓存感知：提交前查 `segment_status` 跳过已成功镜头；状态灯「参数已变需重生成」。
  - **验收**：建 3 场景 × 2 镜 → 一键生成 → 进度实时 → 原地看片 → 下载 mp4。

- [ ] **V1.4 收尾打磨（1-2 天）**
  - 保存/加载（episode.json 读写）、已保存指示、退出确认。
  - WS 断线轮询兜底；错误 toast（后端报错可见化）。
  - **验收**：核心动线无跳页跑通，30 秒内理解"Scene/Shot/Asset/生成"关系。

> **V1 交付后冻结**：老节点内嵌 UI 继续可用（并行共存），SPA 成熟后老 UI 冻结。

### V1.5：素材库 → @资产 → 继承（约 1-2 周）

- [ ] 素材库页：四类资产 CRUD + 图片上传 + 描述/别名 + 使用统计。
- [ ] `@资产名` 实时解析：高亮 + 命中列表 + 自动填 castId/locationId + 右侧一致性绑定面板。
- [ ] 继承可视化：资产条目显示来源徽标（↑来自 Scene 01 / ↑来自 Project / ⚡ Shot 覆盖）。
- [ ] 场景参考图自动注入该场景镜头；手动覆盖标记 manual。
- **验收**：写 `@林雪走进@霓虹街区` → 自动关联参考图 + 继承链可见，生成人物/场景正确。

### V2：Prompt 导演系统（约 2 周）

- [ ] 结构化 Prompt 编辑器：画面/摄影/风格/声音/负面词 分区（简洁/高级双模式）。
- [ ] `✨ AI 扩写`：简洁模式一句人话 → AI 生成 H3 完整描述。
- [ ] 风格预设库（赛博朋克/皮克斯/写实…）+ 风格一致性锁。
- [ ] 项目首页剧本式概览（场景卡片 + 人物地点统计 + 进度）。
- **验收**：完全不懂提示词的用户能产出风格统一的连续剧。

### V3：多模型 Adapter（按需）

- [ ] `DirectorAdapter` 正式化（接口文档 + 注册表 + `engine.backend` 切换）。
- [ ] fl2v/video 模式映射（第 2 版 Adapter）。
- [ ] Wan/LTX Adapter 实验。
- [ ] 超分/字幕/多集管理/封面图。
- **验收**：换后端只改 Adapter 注册表，前端零改动。

---

## 9. 风险与决策点（v1.1 更新）

### 9.1 需用户拍板的决策

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| D1 | 项目目录位置 | (a) `{ComfyUI}/input/minimax_studio/` (b) 用户任意目录 | (a) 同盘好备份；v1 用 (a)，设置页留自定义 |
| D2 | 工作流模板策略 | (a) SPA 内置默认 Director 工作流，节点 id 可配 (b) 用户从 ComfyUI 导出 workflow 上传 | v1 用 (a)，设置页放模板编辑器 |
| D3 | WS 补强 | (a) 纯前端轮询兜底 (b) 后端加 `GET /run_progress` 路由 | 先 (a)，V1.4 视体验加 (b) |
| D4 | shots 存储 | (a) 单文件 episode.json (b) 每镜一文件 | v1 用 (a)，>50 镜再拆 |
| D5 | 新前端与现节点 UI 关系 | (a) 并行共存（现节点继续用） (b) 逐步替代 | v1 并行共存，SPA 成熟后老 UI 冻结 |
| D6 | **V1 是否含素材库/@资产/继承** | (a) V1 只做工作台闭环 (b) 素材库提前进 V1 | **按用户拍板：V1 只做工作台**，素材库/@资产/继承 → V1.5 |
| D7 | **V1 的导入通道** | (a) 先支持导入现节点 timeline_data (b) V1 就做完整项目新建 | (a) 最快验证闭环；新建项目在 V1.4 收尾补 |

### 9.2 技术风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| WS 单槽（§6.2） | 进度丢 | SPA 保持唯一活跃客户端 + 轮询兜底 |
| 无取消原语 | 长任务不可中断 | UI 明确"停止后续提交"，排队中提示 |
| 提交 payload 大 | timeline JSON + refs 图片引用 | refs 只传 imageFile（input 目录相对路径），不上传图片本体 |
| 缓存指纹失效 | 改参数即重跑 | 前端缓存感知 + 状态灯提示"参数已变需重生成" |
| 现节点字段双命名 | Adapter 错误 | 统一走 Adapter 内部 `getField` 兼容层 |
| Vite SPA 与 ComfyUI 跨域 | CORS | ComfyUI `--enable-cors-header` 或 SPA 走同源代理（vite dev proxy / 部署到 ComfyUI 静态目录） |
| **Director Core 越写越厚** | Core 变上帝类 | 五模块职责单一 + 纯函数式（输入模型 → 输出模型），Adapter 只做机械映射；测试锁行为 |
| **Workbench 布局在窄屏溢出** | 创作被打断 | 最小宽度约束 + 四区可折叠（左侧树/右侧设置可收起），以中间预览为锚 |

### 9.3 明确不做

**V1（第一版）不做**：

- 不做素材库完整页（占位入口），`@资产` 识别留 V1.5。
- 不做结构化 Prompt 分区编辑器（V1 用简洁模式 + 直接编辑合成 Prompt），V2 做完整导演系统。
- 不做成片页（批量导出/合并）；V1 单镜下载/重新生成在工作台内完成。
- 不做 fl2v/video 生成模式（V1 只走 r2v prompt_batch）。
- 不做完整项目新建向导（V1 支持导入现工程 + 基础保存，新建项目 V1.4 收尾补）。

**所有版本都不做**：

- 不做 ComfyUI 工作流编辑器（SPA 只管 Director 一个节点的数据）。
- 不做实时逐帧预览（H3 生成中无逐帧流，只有段首预览帧）。
- 不做多用户/协作。
- 不做云端渲染。

---

## 10. 附录：字段契约权威清单

> 前端 Project Model 与后端 timeline_data 的唯一权威映射。实现时以本附录 + 现节点源码为准。

### 10.1 集/全局 → timeline

| Project Model | timeline 位置 | 后端读取 |
|---|---|---|
| episode.defaultTaskKey | global.taskType | plan.global_task_key |
| episode.globalPrompt | global.prompt | plan.global_prompt |
| episode.globalRefs | global.refs | plan.global_refs |
| episode.globalRefAudios | global.refAudios | plan.global_ref_audios |
| project.defaults.* | output.* | 见 §2.3 |
| project.engine.backend | —（路由 Adapter） | — |

### 10.2 场景 → timeline.scenes[]

| Project Model | timeline | 说明 |
|---|---|---|
| scene.id | scenes[].id | 后端丢弃无 id 场景 |
| scene.name | scenes[].name | 缺省回退 "Scene NN" |
| scene.location / time | scenes[].location / .time | 文案 |
| scene.order | scenes[].order | 排序 |
| scene.assetGroup | scenes[].assets | key 用复数 locations |
| scene.defaultCastId | scenes[].defaultCastId | 继承链 |
| scene.defaultLocationId | scenes[].defaultLocationId | 继承链 |

### 10.3 镜头 → timeline.segments[]（prompt_batch 白名单 22 项）

`id, start, length, frameCount, durationSec, prompt, negativePrompt, taskType, refs, refAudios, refVideos, genImage, castId, locationId, castManual, locationManual, stateChange, smartTail, sceneId, continuityMode, consistencyCheck`

- `start/length/frameCount` 由 Adapter 算（累计 + 网格对齐），**不存项目模型**。
- `castManual/locationManual` 表示用户显式选择过（继承逻辑不覆盖）。

### 10.4 原生 widget 参数（Adapter.buildNodeWidgets 输出）

`task_type, global_prompt, cfg, seed, frame_rate, width, height, ref_max_size, total_frames, steps, sampler, scheduler, shift_video, shift_audio, clear_vram_between_segments, export_source_images` + 模型连接 `model/video_vae/audio_vae/clip`。

### 10.5 响应/事件

- 进度：`minimax_director_progress`（§2.10）。
- 预览：`minimax_director_preview`（§2.10）。
- 节点输出：`images(list) / audio(list) / fps / frame_count / source_images(list) / report(string)`。
- 状态灯：`GET /minimax/director/segment_status` → `{cached:[...], states:{}}`。
- 单镜 mp4：`GET /minimax/director/segment_mp4?node_id&index&fps`。

---

*本蓝图基于 2026-08-09 对现节点源码的四份全量盘点（plan.py/gen_timeline.py、nodes/director.py+http_routes.py+progress.py、web/js 前端、executor_core.py+segment_cache.py+stream_export.py）。任何字段以现源码为准。*
