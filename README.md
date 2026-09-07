# AI 漫剧导演系统 · ComfyUI MiniMax H3 Director（二次开发版）

> 从"小说 / 剧本文本"到"带配音成片"的 **AI 漫剧生产线** —— 在 AIMixer 的 MiniMax H3 Director 节点（Apache-2.0）之上二次开发的上层导演系统。
> 一个人 + 本地显卡 + 开源模型，把文字变成一集有镜头语言、有人物一致性、有配音的 AI 漫剧。

**English** → [README_EN.md](README_EN.md)

![MiniMaxH3Director 工作流截图](docs/screenshot.png)

---

## ⚠️ 来源与致谢（Credit / Provenance）

本仓库是 **fork（二次开发仓库）**，不是从零自研：

- **基座**：fork 自 [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)（Apache-2.0），完整保留其**全部上游提交历史与 LICENSE**。原版「多段音视频导演台节点」能力（多段时间轴 / t2v / i2v / fl2v / r2v / v2v / rv2v / 原生立体声）见下文「基座功能（原版继承）」。
- **二次开发**：在基座之上新增「**AI 漫剧导演层**」——剧本理解、分镜调度、运镜规则、H3 提示词编译、资产生成一致性、TTS 配音、SPA 分镜工作台。新增内容以一个提交集中提交（`10664e7`，约 +9.5 万行）。
- **原作者的 QQ / QQ 群 / B 站 / Comfyit 等联系方式均属 [AIMixer](https://github.com/AIMixer) 所有**，请通过上游仓库联系原作者；本 fork 为个人学习与求职展示用途。
- **若只需原版节点**，请直接使用上游仓库：[github.com/AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)。

---

## 一、这个仓库是什么

一句话：**基座负责"生成一镜"，导演层负责"像导演一样把整个故事排成镜头并盯到成片"。**

```
小说 / 剧本
   │  story_analyzer · script_parser（本地 Qwen 整章理解 + 纯规则兜底）
   ▼
Story Timeline + 分镜（ProductionPlan：Chapter → Beat → Shot → DirectorIntent）
   │  shot_plan · 双前驱连续性（时间线前驱 + 场景末态）· Global Story Bible
   ▼
每镜「导演意图」DirectorIntent（内容 / 动作 / 心理 / 运镜 / 画风 / 音频 / 实体）
   │  camera_template · core_action · psych_visualize · beat_rhythm · visual_style
   ▼
H3 提示词编译（三段式 + 双语约束 + 无字幕强约束）
   │  h3_prompt_builder · prompt_compiler · constraint_checker
   ▼
MiniMax H3 生成（基座节点执行 t2v/fl2v/r2v/v2v/rv2v）
   │  qwen_vl_feedback（Qwen3-VL 抽帧校验 → 三级重跑）· vae_residency 解码修复
   ▼
TTS 配音（Voice Cast）→ FFmpeg ducking 混音 → 成片
   │  tts_engine（多引擎可插拔）· tts_worker · mixer
   ▼
SPA 分镜工作台（Vue3 + TS）：项目 / 时间线 / 镜头卡片 / 任务中心 / 成片归档
```

**设计原则**：内容层（Story Bible / 分镜）、导演规则层（运镜 / 画风 / 节奏）、提示词编译层、执行层、声音层、UI 层分层解耦；凡是能写成纯规则的绝不用 LLM（可测试、可复现、零显存）。

---

## 二、本 fork 新增的「AI 漫剧导演层」

### 1. 剧本 / 小说导入与故事理解
- `story_analyzer.py` / `script_parser.py` / `script_analyzer.py` / `entity_cleanse.py`：**自然语言整章理解**（本地 Qwen，Ollama/transformers 可切）产出结构化剧情，纯规则层负责拆段、清洗、兜底，实体分类带置信度门控。
- `production_plan.py`：核心数据模型。剧情按 **Chapter → Beat → Shot → DirectorIntent** 逐层精化；`timeline` 字段是**播放顺序的唯一权威源**（不按场景字面顺序）。
- `bible.py` / `bible_store.py` / `bible_updater.py`：**Global Story Bible**，跨镜世界观 / 人物 / 地点一致，只注入已确认事实，防止幻觉污染。

### 2. 分镜与导演意图
- `shot_plan.py`：9 类分镜模板（Shot Blueprint），按戏剧功能把 Beat 拆成有镜头语言的 Shot。
- `director_intent.py`：统一的「导演意图」中间表示——一镜的动作、人物、心理、运镜、画风、音频、实体边界全在这里，是后面所有生成提示词的唯一输入。
- 双前驱连续性：每镜同时继承「时间线前一镜」与「该场景上一次出现」的状态，解决交叉剪辑跳戏。

### 3. 导演规则层（运镜 / 画风 / 节奏）——「AI 漫剧导演规则 v1/v2」
- `camera_template.py`：跨镜运镜状态机（推 / 拉 / 摇 / 移 / 环绕 / 升降），上一镜收尾镜头自动接下一镜开场镜头。
- `spatial_continuity.py`：空间连续性（人物 / 物体在画面中的方位关系）。
- `core_action.py`：**一镜一动作**，把一句话里的多个动作拆到不同镜头。
- `psych_visualize.py`：心理活动 → 可视化意象（如"心如刀绞"→ 视觉隐喻）。
- `beat_rhythm.py`：情绪曲线 → 运镜强度（情绪上升推镜、峰值定格）。
- `visual_style.py`：全剧级**画风预设库**（7 个预设），镜头不再各自为政。
- `director_rules.py`：反应镜等硬规则。

### 4. H3 提示词编译与约束层
- `h3_prompt_builder.py`：把 DirectorIntent 编译成 MiniMax H3 官方三段式 Prompt；处理对白（`<d>` 块）与画外音。
- `prompt_compiler.py` / `constraint_checker.py`：**生成约束层**——8 层约束注入（人物外观 / 群像数量 / 空间关系 / 无字幕等），用「强约束 + 提示词」双通道压制模型自由发挥。
- `prompt_sanitize.py` / `prompt_enhance_media.py`：去重、去污染、素材引用重写。
- `shot_plan` → `DirectorIntent` → `h3_prompt_builder`：全程**可追溯**（前端三层展示：AI 理解 / 导演意图 / 最终 H3 Prompt）。

### 5. 资产生成与人物一致性
- 资产三级继承：**全局资产库 → Scene 资产 → Shot 资产**（`asset_matcher.py` 自动匹配，前端逐项确认）。
- `asset_registry.py`：实体持久绑定 Checkpoint（跨集 / 跨导入复用，别名归一）。
- Qwen 角色外观档案：规则优先、LLM 补空、绝不臆造服装。
- `qwen_vl_feedback.py` + `state_rule_check.py` + `vlm_backends.py`：**视觉反馈闭环**——生成后抽帧让 Qwen3-VL 检查画面与脚本是否一致，异常触发三级重跑策略。

### 6. 生成执行层的工程修复与增强（在基座执行链路之上）
- `executor_core.py` / `gen_timeline.py` / `plan.py` / `segment_*` / `stream_export.py`：段级计划、缓存、流式导出。
- `vae_residency.py`：修复 aimdo 动态 VAE patcher 导致解码卡死的问题（解码前切非动态全量驻留，解码 ~15s）。
- `frame_align.py` / `vram_cleanup.py` / `core_text_encode.py`：帧对齐、显存回收、文本编码隔离。
- 显存安全门：提交前检查 `/api/ps`，LLM 与视频生成互斥，防止 OOM 崩溃。

### 7. 声音导演层（Voice Cast / TTS）
- `audio_intent.py` + Dialogue 台词提取：从剧本提取对白（引号对白 + 【】系统提示）。
- `tts_engine.py` + `director/tts_engines/`：**TTS 引擎抽象层**，Edge-TTS / GPT-SoVITS / CosyVoice 3 / MiniMax Speech 可插拔，统一接口与降级重试。
- `tts_worker.py`：合成落盘 + 真实时长回写；`mixer.py`：**FFmpeg sidechaincompress ducking 混音**，解决台词重叠、被视频截短问题。
- 架构决策：**H3 只负责画面 + 环境音，对白 / 旁白走 TTS 演员，最后 ducking 混音成片**。

### 8. 前端：分镜工作台 SPA（`frontend/`，Vue3 + TypeScript + Vite）
- 项目 / 集管理、项目持久化与快照、打开 / 新建 / 导入。
- **时间线主视图**（播放顺序唯一权威，拖拽排序）+ 地点库 + 镜头卡片。
- Prompt 分区编辑器、`@` 资产引用补全与 chip 缩略图、三级资产继承徽标。
- 任务中心：队列分离、真实阶段显示、ETA、种子（固定 / 随机 / 以某次种子重放）、取消重试。
- 生成版本归档：每镜历史版本、Prompt 只读快照、「以此版本重放」。
- Voice Cast 全局面板 + Dialogue 行级音色 / 情绪 / 语速。
- `frontend/src` 50 个源文件 + 32 个测试文件（`frontend/tests`）。

### 9. 工程质量
- 后端 pytest **60 个测试文件**（`director/tests`）+ 前端 vitest；全链路回归、双目录（开发 / 部署）md5 同步校验。
- `CHANGELOG.md`：全量改动索引（已到 #650+）；`OPERATION_GUIDE.md`：可发给任何 AI 助手的完整操作手册。
- 设计文档沉淀了关键架构决策（运镜规则 / 生成约束层 / 声音导演层 / SPA 蓝图），导航见「七、文档导航」。

---

## 三、验证情况

本项目在**真实剧本**上做过多种形态的端到端验证：4 镜级 A/B（导演规则 v2 与生成约束层的实机对比）、11 镜级 UI 全流程走查、**12 镜级全片 Golden Path**（剧本导入 → 规则拆镜 → 实体匹配 → 逐镜生成 → 配音成片）。逐镜验收过程与走查清单为本地开发记录，未随仓库公开。

> 演示视频正在制作中，完成后将在此外链 B 站。（仓库不放视频文件）

---

## 四、仓库结构速览

```
├─ nodes/                 基座节点定义（MiniMaxH3Director，含导演层扩展入口）
├─ lib/                   基座底层库（音频/视频/条件编码等，含少量扩展）
├─ web/js/                基座节点内置 UI（minimax_*.js，导演台面板，含大量扩展）
├─ director/              ★ 新增导演层（Python 后端，55 个模块）
│  ├─ production_plan.py / director_intent.py   数据模型与导演意图
│  ├─ story_analyzer / script_analyzer / bible_* 故事理解与世界观
│  ├─ camera_template / visual_style / core_action / psych_visualize / beat_rhythm / director_rules / spatial_continuity
│  ├─ h3_prompt_builder / prompt_compiler / constraint_checker / shot_plan
│  ├─ asset_matcher / asset_registry / entity_cleanse / qwen_vl_feedback / state_rule_check
│  ├─ tts_engine / tts_worker / mixer / audio_intent / tts_engines/
│  ├─ executor_core / gen_timeline / plan / segment_* / stream_export / vae_residency / vram_cleanup
│  ├─ http_routes.py / project_store.py          HTTP 路由与工程文件层
│  ├─ prompt_templates/                          Prompt 模板注册表
│  └─ tests/                                     60 个 pytest 文件
├─ frontend/               ★ 新增独立 SPA 分镜工作台（Vue3 + TS + Vite）
│  ├─ src/ (50 文件)   ├─ tests/ (32 文件)   └─ scripts/（e2e）
├─ tools/                 新增工具脚本（验收 / 盲听测试 / 诊断）
├─ example_workflows/     基座示例工作流
└─ docs/ + *.md           设计文档（NOVEL_TO_MANGA_GUIDE / CAMERA_RULES_V1/V2 / PROMPT_COMPILER_V1 / TTS_VOICE_CAST_PLAN / …）
```

---

## 五、安装（本 fork）

前置条件与基座一致：**ComfyUI ≥ v0.30.0**（含官方 MiniMax H3 节点）+ MiniMax H3 模型权重（fl2va / ref2va UNET、Qwen3-VL CLIP、双 VAE）。

导演层的剧本理解需要本地文本模型（Ollama 跑 Qwen 即可），视觉反馈需 Qwen3-VL 权重（可关）。

### 方式一：手动安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Hc104/ComfyUI_MiniMaxH3_Director.git
cd ComfyUI_MiniMaxH3_Director
pip install -r requirements.txt
```

重启 ComfyUI。

### 方式二：ComfyUI Manager

1. 打开 **ComfyUI Manager** → **Install via Git URL**
2. 填入 `https://github.com/Hc104/ComfyUI_MiniMaxH3_Director.git`
3. 重启 ComfyUI

### 前端 SPA（可选）

```bash
cd frontend
npm install
npm run build      # 生产构建
# npm run dev      # 开发模式（热更新）
npm test           # vitest
```

### 后端测试

```bash
python -m pytest director/tests -q
```

> 完整操作步骤、界面说明与排错见 **[OPERATION_GUIDE.md](OPERATION_GUIDE.md)**。

---

## 六、基座功能（原版继承，作者 AIMixer）

> 以下能力来自上游 [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)，本 fork 完整保留并在此基础上扩展。

**MiniMaxH3Director** 是面向长视频、多段生成的 MiniMax H3 导演台节点，把分段计划、条件编码、采样解码和导出整合在一个节点里。底层走官方 `MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo` + `MiniMaxH3SigmaShift` + `KSampler` + AV 分离解码链路，原生输出立体声音频。

| 功能 | 说明 |
|------|------|
| **多段时间轴** | 节点内上传视频，支持切分、均分、智能分镜分割（PySceneDetect）、追加；分割点可选中删除；可视化时间轴预览每段范围与缩略图 |
| **多任务模式** | `task_type`：`t2v`（文生视频）、`i2v`（图生视频）、`fl2v`（首尾帧生视频）、`r2v`（参考主体生视频 / 素材组）、`v2v`（视频转视频）、`rv2v`（参考素材改视频） |
| **首尾帧 (fl2v)** | 独立首尾帧时间轴：多组关键帧、「添加一组」上传首帧（必传）与尾帧（可选）；拖缘调时长；提示词写中间运动；支持「选择运行」只跑部分组 |
| **参考素材组 (r2v)** | fl2v 式分组 UI：每组图片1–9 / 音频1–3 / 视频1–3；提示词用 `<Picture N>` / `<Video K>` / `<Audio J>`（或 `@` 引用） |
| **源视频编辑 (v2v / rv2v)** | 源视频时间轴；每段源画面自动绑定 `<Video 1>`；`rv2v` 另可挂参考图与参考音频 |
| **选择运行** | 开启后只采样勾选的片段 / 素材组；未勾选段可用缓存或源画面填充 |
| **原生立体声音频** | 与画面同次采样生成；`v2v` / `rv2v` 可选生成声音 / 使用原声 / 静音 |
| **运行报告** | `report` 口输出分段计划、每段任务摘要 |

**输入：** `model` → `video_vae` → `audio_vae` → `clip`　**输出：** `images` → `audio` → `fps` → `frame_count` → `source_images` → `report`

> CLIP Loader 的 **type 必须选 `minimax`**（Qwen3-VL）。`t2v` / `i2v` / `fl2v` 用 **fl2va** UNET；`r2v` / `v2v` / `rv2v` 用 **ref2va** UNET。

### 默认采样参数

- 画布默认 **0.4MP 16:9（864×480）**，**5 秒 / 124 帧 @ 24 fps**（17k+5 网格）
- **25** steps，`res_multistep` + `simple`，CFG **1.0**；Sigma shift：video **12** / audio **3**

### 推荐模型文件

| 用途 | 文件名 | 目录 |
|------|--------|------|
| UNET (t2v / i2v / fl2v) | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` |
| UNET (r2v / v2v / rv2v) | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` |
| CLIP | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `models/text_encoders/` |
| Video VAE | `minimax_h3_video_vae_fp16.safetensors` | `models/vae/` |
| Audio VAE | `minimax_h3_audio_vae_fp32.safetensors` | `models/vae/` |

模型权重与示例工作流下载、官方文档与上游生态，见 **[上游仓库 README](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)**。

---

## 七、文档导航

| 文档 | 用途 |
|---|---|
| [OPERATION_GUIDE.md](OPERATION_GUIDE.md) | **操作使用指南**：界面布局、字段含义、完整流程、常见排查 |
| [CHANGELOG.md](CHANGELOG.md) | **修改记录总索引**：全部改动时间线 + 涉及文件 + 已知坑 |
| [NOVEL_TO_MANGA_GUIDE.md](NOVEL_TO_MANGA_GUIDE.md) | 小说 → 漫剧全流程指南 |
| [CAMERA_RULES_V1.md](CAMERA_RULES_V1.md) / [CAMERA_RULES_V2.md](CAMERA_RULES_V2.md) | AI 导演运镜规则 v1 / v2 设计文档 |
| [PROMPT_COMPILER_V1.md](PROMPT_COMPILER_V1.md) | 生成约束层（8 层约束）设计文档 |
| [TTS_VOICE_CAST_PLAN.md](TTS_VOICE_CAST_PLAN.md) | 声音导演层架构（H3 + TTS + ducking） |
| [FRONTEND_SPA_BLUEPRINT.md](FRONTEND_SPA_BLUEPRINT.md) | 前端 SPA 分镜工作台设计蓝图 |

---

## 八、致谢与许可

- 基座：[AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)（Apache-2.0）
- [Comfy-Org / ComfyUI](https://github.com/Comfy-Org/ComfyUI) — 官方 MiniMax H3 支持；[MiniMax-AI](https://github.com/MiniMax-AI) — MiniMax H3 模型
- [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) — 权重与文档
- 本仓库新增部分亦以 **Apache-2.0** 许可发布；LICENSE 沿用上游原样。
