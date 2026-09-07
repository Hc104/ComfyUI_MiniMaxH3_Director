# V1.7 规划：剧本 → AI 视频制作计划（Script-to-Plan）

> 状态：**正式立项（2026-08-11 用户拍板）**。**Phase 0 已 PASS**（#123：Ollama + TextBackend + 进程级显存互斥，用户实测 5/5 + H3 生成链无残留）。**Phase 1 · Commit 1 已交付**（#124：ProductionPlan Schema v1 + 规则拆 Scene/Shot，58 测试全绿）。**Commit 2 已交付**（#125：script_analyzer Qwen 语义补全，74 测试全绿 + Commit 1 回归 58 全绿）。**Phase 1 真实验收 PASS（#126，用户实测 12/12）**。**Commit 3 已交付**（#127：剧本导入正式 UI 入口——后端 /script/import 路由 + 前端「导入剧本」弹窗 + ProductionPlan→SPA 映射层，后端 27 断言 + 前端 276 测试 + build 全绿）。**Phase 2 资产自动匹配已交付（#128：asset_matcher 纯规则四级匹配 + /assets/scan 路由 + 导入弹窗资产匹配确认区，后端 48 断言 + 前端 281 测试 + build 全绿）**。**Phase 3 Prompt Builder（P0-C）已交付（#129：prompt_builder_v17 纯规则五区草稿 + Prompt Template Registry v1 + /prompt/draft 路由 + 导入弹窗「🤖 AI Draft」折叠预览 + 前端 drafts 映射进 Shot.content，后端 68 断言 + 前端 287 测试 + build 全绿 + 双目录 md5 一致）**。**Phase 4 Workbench 审核（AI 制作计划 UI）已交付（#130：shotReviewStatus 三项就绪判定 + Workbench 审核态横幅/采纳/生成硬拦截 + AssetConfirmDialog 补资产闭环复用 /assets/scan，前端 311 测试 + build 全绿 + 双目录 md5 一致 + 纯前端刷新即生效）**。**Phase 5 自动运镜（P1-D）已交付（#133：camera_template.py 10 基础 + 5 补充模板库 + previousCameraState 跨镜状态机（场景内连贯 + 换场景重置）+ _apply_continuity 三规则（方向延续/对话反打/避免重复推近）+ pick_camera 注入 Prompt Builder + /camera/templates 路由 + 前端模板下拉 + 手编共存 + 模板 badge，后端 67+50 断言 + 前端 335 测试 + build 全绿 + 双目录 md5 一致）**。**实体抽取加固已交付（#134，用户实测「5 自动·2 待确认·23 未匹配」bug 驱动：Entity/EntityType/EntitySource Schema + Qwen Judge Prompt 升级（只提取原文禁止自创）+ entity_cleanse 纯规则清洗（镜头标记丢弃/类型纠正/去重/置信度门控 auto≥0.85·pending≥0.60）+ asset_matcher 接清洗注册表 + can_auto 门控 + 漏斗统计 + 前端漏斗文案/类型 chip + Golden Path 脚本实体卫生断言（无镜头标记/无 inferred 进匹配）14/14 PASS + dialogue 运镜模板补推近修复，后端全量 500+ 断言 + 前端 335 测试 + build 全绿）**。**Phase 1.1 语义准确性加固已交付（#361-#368，用户评审驱动拍板：ScriptEntity/VisualElement 两层实体 + asset_requirement 分流（required/recommended/none）+ Asset Matcher 类型硬约束（character→cast / location→location / prop类→prop）+ semantic incompatible→NONE（旧剑→旧剑匣 剔除）+ 地点层级建议参考（山雨楼→山雨楼外「⚠ 建议参考 [接受][重新选择]」）+ 泛化引用（石阶→后院石阶 generic_reference 不污染原文）+ Qwen Task A（Script Fact）/Task B（Visual Interpretation）拆分 + 前端分组展示（角色✓/地点✓?建议参考/道具✓?未找到/视觉元素·进Prompt/无需资产匹配 N 计数），后端 524 断言 + 前端 335 测试 + build 全绿 + golden_path --mock 14/14 PASS + 双目录 md5 一致）**。下一步：**Phase 2-1 Asset Identity + Persistent Binding 已交付（#379：稳定 entity_key + 永久 asset_id + 绑定落盘复用 + Registry 单向数据流，后端 asset_registry 52 + 前端 351 测试 + build 全绿）**。下一步：**用户本机验证（重启 Comfy Desktop → 重新导入《山雨客栈》核对已接受绑定复用不重新 pending + asset_registry.json 落盘）→ Phase 2 正式 Asset Registry 全量**。**Phase 2-2 Stable Binding 跨解析稳定已交付并最终验收 PASS（#383/#384/#385：Stable Entity Key + Alias 归一 + 收尾核验，完整剧本两次导入端到端 [14] + spa_acceptance ⑪；asset_registry 84 + spa_acceptance 真实 Ollama 48/48 + SPA 两次导入 7/7 persisted 复用；Phase 2-2 正式关闭，2026-08-11）**。下一步：**最终 Golden Path 剧本验收（导入《山雨客栈》→ 审核 → 逐镜生成 → 导出 → 重开项目复验）**。**Golden Path 全流程验收 PASS（#386，2026-08-11 用户实测）：导入《山雨客栈》→ 资产 7/7 persisted 复用 → AI Draft 9/9 五区无内部标记 → Workbench 9/9 采纳 → H3 逐镜生成 9/9 → 整片导出 48.9s → 重开项目复验保留 → V1.7 全阶段交付收官。**
> 核心哲学（用户原话）：**AI 负责把「剧本语言」转换成「生产语言」，你负责最后确认。**
>
> ## ⛔ V1.7 产品底线（放最顶部，勿偏航）
>
> **V1.7 不负责「替用户拍片」，只负责把剧本转换成一套经过人工审核即可生产的 AI 视频制作计划。**
>
> 以后功能越做越多，任何「AI 自动越权」的想法都要回到这句话来校准：AI 产出可审核的草稿，人做最终确认，**AI 不直接开拍**。
>
> 关联：`V1.6x_PRODUCTION_HARDENING.md`、`V16_PLAN.md`（V1.6 冻结）、`V1.5_MATERIAL_LIBRARY_PLAN.md`、`CHANGELOG.md`。

---

## 0. 拍板结论（用户 2026-08-11 正式立项）

| 决策点 | 结论 |
|---|---|
| **V1.7 主题** | **剧本 → AI 视频制作计划**（Script-to-Plan，不是「视频替换」） |
| **V1.7 版本定位** | V1.6：我有一个制作计划 → 稳定生产成片。**V1.7：我只有一个剧本 → 系统帮我生成一套可审核、可生产的制作计划** |
| **本次状态** | 🟢 **正式立项（2026-08-11 用户拍板）**；Phase 0（Ollama + TextBackend + 进程级互斥）已验收 PASS（#123）；**Phase 1 · Commit 1（Schema v1 + 规则拆 Scene/Shot）已交付（#124，58 测试全绿）**；**Commit 2（script_analyzer Qwen 语义补全）已交付（#125，74 测试全绿 + Commit 1 回归 58 全绿）**；**Phase 1 真实验收 PASS（#126，用户实测 12/12）**；**Commit 3（剧本导入正式 UI 入口）已交付（#127，后端路由 + 前端弹窗 + 映射层，测试/build/同步全绿）**；**Phase 2 资产自动匹配已交付（#128，asset_matcher 纯规则四级匹配 + /assets/scan 路由 + 导入弹窗匹配确认区，后端 48 断言 + 前端 281 测试 + build 全绿）**；**Phase 3 Prompt Builder（P0-C）已交付（#129，prompt_builder_v17 纯规则五区草稿 + 模板 Registry v1 + /prompt/draft 路由 + 导入弹窗「🤖 AI Draft」折叠预览 + drafts 映射进 Shot.content，后端 68 断言 + 前端 287 测试 + build 全绿）**；**Phase 4 Workbench 审核已交付（#130，shotReviewStatus 三项判定 + 审核态横幅/采纳/硬拦截 + AssetConfirmDialog 补资产闭环，前端 311 测试 + build 全绿 + 纯前端刷新即生效）**；**Phase 5 自动运镜已交付（#133，camera_template 15 模板 + 跨镜状态机 + 前端模板下拉，后端 117 断言 + 前端 335 测试 + build 全绿）**；**实体抽取加固已交付（#134，用户实测 bug 驱动分层加固：Entity Schema + Qwen 只提取不创造 + entity_cleanse 清洗/门控 + asset_matcher 接注册表 + 前端漏斗/类型 chip + Golden Path 脚本实体断言 14/14 + dialogue 运镜修复）**；**Phase 1.1 语义准确性加固已交付（#361-#368，用户评审驱动：ScriptEntity/VisualElement 两层实体 + asset_requirement 分流 + 类型硬约束（character→cast/location→location/prop类→prop）+ semantic incompatible→NONE + 地点层级建议参考 + 泛化引用不污染原文 + Qwen Task A/B 拆分 + 前端分组展示，后端 524 断言 + 前端 335 测试 + build 全绿 + --mock 14/14 PASS）**；**Phase 2-1 Asset Identity + Persistent Binding 已交付（#379：稳定 entity_key + 永久 asset_id + 绑定落盘复用 + Registry 单向数据流，后端 asset_registry 52 + 前端 351 测试 + build 全绿）**；下一步：**用户本机验证（重启 Comfy Desktop → 重新导入《山雨客栈》核对已接受绑定复用不重新 pending + asset_registry.json 落盘）→ Phase 2 正式 Asset Registry 全量** |
| **产品底线** | **V1.7 不负责「替用户拍片」，只负责把剧本转换成一套经过人工审核即可生产的 AI 视频制作计划**（见顶部 ⛔） |
| 产品路线 | V1.6 生产闭环（已完成）→ **V1.7 剧本→制作计划** → AI 短剧制作工具 |
| 自动化哲学 | AI 把「剧本语言」转「生产语言」；**人工审核是最后一道门，AI 不直接开拍** |
| 运镜策略 | **模板库选择 + 跨镜连贯**（保存 previousCameraState），不让 AI 每次自由发挥「摄影术语」 |
| **范围控制** | **只做四件事**：P0-A 剧本→Scene/Shot、P0-B 剧本→资产、P0-C 剧本→Prompt、P1-D 剧本→运镜；**明确不做清单见 §0.1** |
| 版本拆分 | **P0** = 剧本解析 + 资产自动匹配 + 结构化生产 Prompt 草稿 + **显存安全门**；**P1** = 自动运镜 |
| LLM 后端 | **Ollama API 优先**（`TextBackend` 首先实现 `OllamaBackend`，模型 `qwen3:14b`；后续补 `OpenAICompatibleBackend`；`LocalQwenBackend` 降为备用），业务层不关心谁解析 |
| **显存生命周期** | **硬性架构约束**：TextBackend（Qwen3:14B / Ollama）与 H3 不允许同时常驻 GPU；`with text_backend.session():` 用完请求 Ollama 卸载（keep_alive=0）+ `/api/ps` 确认 + `torch.cuda.empty_cache()`；运行时互斥（见 §8） |
| **GPU 状态检查** | **Qwen 分析完成 ≠ 立即开始 H3**：session 结束 → 请求 Ollama 卸载（keep_alive=0）→ `/api/ps` 确认 qwen3:14b 不再驻留 → **确认 GPU model registry = empty** → 才允许启动 H3；未释放则警告「正在清理显存」而非直接启动（见 §8.5） |
| H3 Prompt | 生成**结构化生产 Prompt 草稿**（Visual / Camera / Style / Sound / Negative），进 AI Draft 人工审核，**不直接作为 H3 黑盒 Prompt 生成器**；**模板版本化（Prompt Template Registry，见 §6.5）** |
| **剧本导入 UI** | **P0 就做完整 UI**（📄 导入剧本 → 选剧本文件 → 选 assets 文件夹 → AI 分析 → 资产匹配确认 → AI Draft → 进 Workbench），**不做命令导入**——V1.7 核心入口，避免体验仍是「技术工具」 |
| **运镜模板规模** | **先 10 条基础模板 + 补 4~6 条常用**（战斗 / 蒙太奇 / 空镜转场 / 环绕 / 升降），不宜一开始做几十种 |
| **aiDraft 留存** | **保留为历史元数据，不影响正常 Shot**（排查「AI 生成 vs 人工修改」有价值），不参与指纹/状态/导出逻辑 |
| **开发节奏** | **正式立项（2026-08-11）**；**Phase 0 先行**——先证明 Ollama/Qwen3:14B 能稳定分析、进程级卸载、卸载后 H3 生成链完全不受影响，再往剧本解析推进 |

**一句话价值**：V1.6 解决「我有 Prompt，怎么稳定生产视频」；V1.7 解决「我有剧本，怎么快速得到**可生产的 Prompt + 资产 + 运镜**」。两者接起来，SPA 才从 ComfyUI 视频工作台变成 **AI 短剧制作工具**。

---

## 0.1 范围控制（V1.7 只解决四件事）

**V1.7 只做四件事：**

| 编号 | 内容 | 一句话 |
|---|---|---|
| **P0-A** | 剧本 → Scene / Shot | 剧本解析成结构化分镜（人物/地点/道具/动作/情绪/台词） |
| **P0-B** | 剧本 → 资产 | 自动扫描 `assets/` 目录并匹配到 V1.5 Asset（低置信度人工确认） |
| **P0-C** | 剧本 → Prompt | 生成结构化五区 Prompt 草稿（Visual/Camera/Style/Sound/Negative） |
| **P1-D** | 剧本 → 运镜 | 模板库选择 + 跨镜连贯，注入 Shot |

**明确不在 V1.7 做（防止「AI 大杂烩」）：**
- ❌ 自动生成参考图
- ❌ 自动生成视频
- ❌ 自动剪辑
- ❌ 自动配乐
- ❌ 自动字幕
- ❌ AI 自动修改成片
- ❌ H3 高级参数自动优化
- ❌ 自动理解整部作品后无限发挥

> 原因（用户原话）：否则很容易从一个可控的生产工具变成「AI 大杂烩」。

---

## 0.2 V1.7 最终目标（用户视角）

用户最终只需要准备：

```
我的作品/
├── script.md
└── assets/
    ├── characters/
    ├── locations/
    ├── props/
    └── styles/
```

然后全流程：

```
📄 导入剧本 → 选择 assets 文件夹 → 🧠 Qwen 分析 → Scene/Shot 自动拆解
→ 资产自动匹配 → 结构化 Prompt 草稿 → 自动运镜 → AI Draft
→ 👤 人工审核 → ▶ 逐镜生成 → 🎬 成片
```

**关键原则：AI 负责生产计划，人负责最终确认，AI 不直接开拍。**

---

## 1. 现状 vs 目标工作流

### 现状（人工链路）
```
剧本 → 人工拆 Shot → 人工写 H3 Prompt → 人工找参考图 → 人工设置运镜 → 逐镜生成
```

### 目标（AI 草稿 + 人工审核）
```
剧本/作品 → 自动拆解 → 自动生成 H3 模板 → 自动识别资产
→ 自动从指定文件夹取参考图 → 自动设计运镜 → 人工审核 → 生成
```

**关键区别**：不是加一个「Prompt 优化」按钮，而是把**从剧本到可生产 Shot 的整条转换链**自动化；AI 产出的是「生产语言」，人做最后确认。

---

## 2. 输入规格

```
第七号站台/
├── script.md                    # 剧本（.md / .txt / .docx 均可）
└── assets/
    ├── characters/
    │   ├── 林雪.png
    │   └── 陈默.png
    ├── locations/
    │   ├── 七号站台.png
    │   └── 候车厅.png
    ├── props/
    │   ├── 手机.png
    │   └── 行李箱.png
    └── styles/
        └── 夜景电影感.png
```

- 剧本：自然语言（含场景/动作/对话/情绪），文件名 `script.md`（或 `.txt`；**.docx 推迟 Phase 1.5**，Phase 1 只读 .md/.txt）。
- 资产目录：四个子目录固定语义，**文件名 = 资产名**；同一目录内允许子文件夹分组。
- 参考图直接来自文件夹，**不用一个个上传**。

---

## 3. 自动化链总览（本规划核心）

```
剧本
 ├─ P0-A 剧本解析     LLM+规则 → Scene / Shot 结构化（人物/地点/道具/动作/对话/情绪/时间/空间）
 ├─ P0-B 资产自动匹配  扫描 assets/ → 名称匹配 + LLM 别名映射 → 自动建立 V1.5 Asset
 ├─ P0-C 结构化生产 Prompt 草稿  分区（Visual/Camera/Style/Sound/Negative）→ 规则组合 → AI Draft（**不进 H3 黑盒**）
 ├─ P1-D 自动运镜     运镜模板库（剧情意图→运镜）+ 跨镜连贯 → 注入 Shot
 └─ ⛔ 显存安全门     TextBackend 用完即卸载 + torch.cuda.empty_cache()，与 H3 运行时互斥（§8）
        ↓
进入现有 Workbench（AI 草稿状态）→ 人工逐镜审核/修改/采纳 → 逐镜生成
```

每一环的产出都直接落到现有 `ProjectModel`（Scene / Shot / Asset），**审核通过后就是普通项目**，生成/导出/续接全部复用现有能力。

---

## 4. P0-A：剧本解析

### 目标
把自然语言剧本转成结构化 Scene / Shot 列表，识别字段：

> Scene / Shot / **人物 / 地点 / 道具 / 动作 / 对话 / 情绪 / 时间 / 空间关系**

### 输出 Schema（对齐现有 ProjectModel）

> **正式拍板版（2026-08-11，Commit 1 #124 已落地）**：下方旧 Schema 仅是「对齐现有 ProjectModel」的**早期草案，不再作为解析目标**。
> **ProductionPlan Schema v1 = `director/production_plan.py`**（字段统一 snake_case，是 parser 的**契约**）。数据流锁死：
> `剧本 → ProductionPlan → Asset Registry → Generation Planning → H3 Prompt → SPA Shot`，**绝不 ProductionPlan 直接塞 SPA Shot**。
> Phase 1 字段定死见 §4.0；刻意不放：castIds / locationId / assetId / generationMode / h3Prompt / camera / refs（分属 Phase 2/3/4）。

```jsonc
{
  "episodes": [{
    "title": "第一集",
    "scenes": [{
      "name": "七号站台",            // 场景名（对齐 Scene.name / location）
      "time": "夜", "weather": "",   // 时间/天气
      "shots": [{
        "name": "镜头 1",
        "durationSec": 5,            // 规则估算，默认 5s，可调
        "content": { "visual": "..." },  // 画面描述（P0-C 再细化成分区）
        "cast": ["林雪"],             // 角色名（P0-B 映射到 Asset）
        "props": ["行李箱"],           // 道具名
        "location": "七号站台",        // 地点名
        "emotion": "紧张",            // 情绪（P1-D 用）
        "action": "快步走向站台",      // 动作（P1-D 用）
        "dialogue": "..."            // 台词
      }]
    }]
  }]
}
```

### 4.0 Phase 1 字段定死（snake_case，Commit 1 契约）
- project：{title, source_file}
- scenes[]：{scene_id, title, location_name, time, weather, shots[]}
- shots[]：{shot_id, source_text, duration_sec, characters[]{name, role}, props[]{name}, actions[], emotion, dialogue[]{speaker, text}, visual_intent}
- validation：{status: pending|valid|invalid, errors[], warnings[]}
- **刻意不放**：castIds / locationId / assetId / generationMode / h3Prompt / camera / refs（分属 Phase 2 / 3 / 4 产出）
- duration 只叫 `duration_sec`（成片时间轴秒数）；Phase 3 再映射 H3 generation duration；SPA Shot.durationSec 最终与它对齐。
- dialogue 是对象 {speaker, text}；speaker → characters[].name → Phase 2 Asset Registry → castIds[]；**Phase 1 绝不生成 Asset ID**。
- validation 只做**机器侧结构校验**；人工审核 = SPA render.status=review，不新增机制（§9.0）。
- 文件格式：Phase 1 只支持 **.md / .txt**；**.docx 推迟 Phase 1.5**（§15.2）。

### LLM vs 规则分工
| 环节 | 谁做 | 原因 |
|---|---|---|
| Scene 粗切分（章节/场景标记） | **规则**（标题行、场景提示词 `## 场景` / 空行聚类） | 确定性高、便宜 |
| 角色/地点/道具实体识别 | **LLM**（剧本里的别称/指代 → 规范化实体名） | 语义理解 |
| 动作/情绪/对话提取 | **LLM** | 语义理解 |
| Shot 切分 + 时长估算 | **规则**（单段文本字数/动作密度 → 拆 1~N 镜；默认 5s） | 避免 LLM 幻觉镜数 |
| 结构校验（Scene 必有 Shot、Shot 必属 Scene） | **规则** | 复用现有模型约束 |

> **核心原则**：LLM 负责「理解」，规则负责「切分/校验/兜底」，避免 LLM 自由发挥破坏现有模型结构。

> **（正式立项补充，用户 2026-08-11）visual_intent ≠ content.visual（架构原则）**：
> `visual_intent` 是 Phase 1 的「视觉意图草稿」（Qwen 产出），**不是** SPA Shot 的 `content.visual`。
> 完整链路：`visual_intent → Generation Planning（Phase 3）→ Official H3 Skill → H3 Prompt Builder → content.visual`。
> 中间任何一步未实现，`visual_intent` 都**不得**直接进 `content.visual`。

> **（正式立项补充，用户 2026-08-11）Qwen 不决定镜头数量**：
> **Qwen 负责理解剧情动作 → 规则负责判断是否需要拆镜**。例如「走路 + 回头 + 停下 + 对话」——规则判断这是至少 3~4 个视觉动作，据此拆镜。
> 目的：避免「一段剧情 → Qwen 随便给你拆成 17 个 Shot」这种失控；镜头数量由规则（动作密度）约束，Qwen 只提供动作/情绪/对话的语义理解。

### 长剧本处理
- 按 Scene 分块送 LLM，每块独立解析后合并；保留块序号防乱序。
- 输出必须是**严格 JSON**（沿用 `QWEN3VL_FEEDBACK_PLAN.md` 的强 JSON 模板 + `repetition_penalty` 经验，防提示词污染）。

---

## 5. P0-B：资产自动匹配

### 目标
扫描指定 `assets/` 目录，**自动建立/匹配 V1.5 Asset**（两级池：全局 + 场景池），参考图自动进 `refImages`。

### 流程
```
扫描 assets/{characters,locations,props,styles}/
  → 文件名 = 资产名，建候选索引（name → imageFile 相对路径）
  → 对每个剧本实体：精确匹配（名称相等）→ 若失败走 LLM 别名映射（「小林」→ characters/林澈.png）
  → 命中 → 自动建全局 Asset（imageFile = minimax_studio/assets/<文件名>）+ 复制到场景池
  → 未命中 / 低置信度 → 前端弹候选下拉，人工确认（不阻断）
```

### 匹配示例
剧本出现「林雪走进七号站台」→ 自动 `@林雪` `@七号站台`，命中 `characters/林雪.png` `locations/七号站台.png`。

### 匹配链路（三级 + 置信度，正式立项补充）
```
剧本实体 → 精确名称匹配 → alias 匹配 → Qwen 语义匹配 → 置信度判断
```
例如剧本写「小林」：
```
候选：林雪 96% / 林澈 41% / 林薇 18%
UI：「"小林"可能对应"林雪"，是否使用？」
```
**低置信度 → 前端弹候选下拉人工确认，而不是让 AI 自己偷偷决定。**

### ⛔ AI 永远不能覆盖用户已经存在的资产（正式立项补充）
项目里已经有「林雪」，剧本再次出现「林雪」→ **必须复用现有 Asset**，不能 Qwen 又创建一个「林雪_2」。
这直接保护 V1.5 建好的资产一致性体系（canonical identity 去重、两级池、@引用同步都依赖资产名唯一）。

### 复用点
- 现有全局/场景两级池（V1.5）+ 继承链（`inheritanceResolver.ts`）。
- 现有 `@资产` 机制（`promptMediaTags.ts`）——生成的 Prompt 直接带 `@`，后端已支持解析注入。
- 资产图路径规约：复制到 `{ComfyUI input}/minimax_studio/assets/`（与现有 `imageFile` 路径一致）。

---

## 6. P0-C：结构化生产 Prompt 草稿

### 目标
**不直接让 AI 写一大段漂亮英文，也不让 AI 直接成为 H3 的「黑盒 Prompt 生成器」**。而是先生成工作台真正需要的结构化分区（AI Draft），再在审核通过后由**现有 H3 生成链**按规则组装成最终 Prompt。

> 用户拍板（2026-08-10）：P0-C 明确定位为 **「结构化生产 Prompt 草稿」**，不是「自动生成最终 H3 Prompt」。这样以后 H3 Prompt 模板换版本，不会把整个剧本解析系统一起推翻。

### 结构化分区（对应现有 Prompt 分区编辑器 `promptSections.ts`）
```
画面（visual）：
  林雪快步走向七号站台，手提黑色行李箱，霓虹灯牌在她脸上留下青紫光斑……
摄影（camera）：
  中景，摄影机平稳跟随林雪向前移动，雨丝在逆光中闪烁……
风格（style）：
  电影感，夜间车站，冷色调，湿地面反射……
声音（sound）：
  远处列车进站声，脚步声，雨声……
负面（negative）：
  …
```

### 组合规则
- 分区 → 按现有 `buildShotPrompt` 组装成 H3 最终 Prompt（visual 为主，camera/style/sound/negative 进对应字段）。
- 资产引用自动转 `@资产名` 标签（P0-B 已映射好），后端注入参考图。

### H3 Prompt 模板版本化 → **Prompt Template Registry（正式立项补充，用户 2026-08-11 强烈建议）**

**不要把 H3 Prompt 模板直接写死在代码里。** 建议：

```
prompt_templates/
├── v1.json
├── v2.json
└── ...
```

例如：
```json
{
  "version": "h3-v1",
  "visual": "...",
  "camera": "...",
  "style": "...",
  "sound": "...",
  "negative": "..."
}
```

Shot 保存：
```json
"promptTemplateVersion": "h3-v1"
```

**为什么这对本项目非常重要**：未来 H3 换模型或者 Prompt 策略变化——
- V1.7 → `h3-v1`
- 以后 V1.8 → `h3-v2`
- **老项目完全不需要重新生成 Prompt**（旧镜头用旧模板版本号可追溯）。

> Prompt 生成的正确形态（用户原话）：
> ```
> 不要：剧本 → Qwen → 一大段英文 Prompt
> 要：剧本 → 结构化理解 → Visual/Camera/Style/Sound/Negative → Workbench
> ```

---

## 7. P1-D：自动运镜

### 目标
根据**剧情动作 + 情绪 + 空间关系**，从**运镜模板库**中选择运镜，而不是让模型自由输出摄影术语。

### 运镜模板库（用户提供，全量收录）
| 剧情意图 | 自动运镜 |
|---|---|
| 人物登场 | Wide → Medium Push-in |
| 人物行走 | Tracking Shot |
| 紧张发现 | Slow Push-in |
| 看见重要物体 | Over-the-shoulder → Push-in |
| 两人对话 | 双人中景 / Shot-Reverse-Shot |
| 情绪特写 | Close-up + Slow Push |
| 环境建立 | Wide Establishing |
| 追逐 | Handheld Tracking |
| 回忆 | Slow Dolly + softer movement |
| 结尾 | Slow Pull-out |

> 用户拍板（2026-08-10）：**先 10 条基础模板，额外补 4~6 条常用模板**（战斗 / 蒙太奇 / 空镜转场 / 环绕 / 升降值得补），不宜一开始做几十种。

### 常用补充模板（4~6 条）
| 剧情意图 | 自动运镜 |
|---|---|
| 战斗 / 打斗 | Handheld 快速摇镜 + 急促推拉，节奏快剪感 |
| 蒙太奇 / 时间流逝 | 系列固定机位 + 轻微 Dolly，硬切过渡 |
| 空镜 / 转场 | 环境空镜 Slow Pan / Tilt（无人主体，做场景过渡） |
| 环绕揭示 | 环绕（Orbit）围绕主体 + 微升降揭示全貌 |
| 升降强化 | 升起（Boom Up）/ 降下（Boom Down），强化情绪或空间关系 |

### 跨镜连贯性（P1 重点）
**不是每个镜头独立随机运镜**，要考虑上一镜和下一镜：
```
Shot 03  林雪走向站台     → Tracking Right
Shot 04  林雪突然停下     → Continue Right + Slow Push-in   ← 承接 03 的方向
Shot 05  她看到陈默       → Over Shoulder                  ← 视线方向
Shot 06  陈默抬头         → Reverse Shot                   ← 对话正反打
```
- 模板选择 = 意图命中 + **方向延续性**（上一镜运动方向/机位）+ **景别衔接**（远景→中景→特写的节奏）。
- 每镜产出 `camera` 描述写入 Shot，进 Prompt 分区编辑器（人工可改）。

### 跨镜状态机：previousCameraState（正式立项补充，V1.7 最有价值点之一）

**运镜不能只看当前镜**。系统需要保存上一镜的摄影状态，至少包括：

```jsonc
"previousCameraState": {
  "movementDirection": "right",   // 上一镜运动方向
  "shotSize": "medium",           // 上一镜景别
  "cameraPosition": "front",      // 上一镜机位
  "subjectDirection": "right"     // 上一镜主体朝向
}
```

下一镜选择模板时参考上一镜：
```
Shot 03  林雪向右走     → Tracking Right
Shot 04  林雪停下来     → Continue Right + Slow Push   ← 承接 03 的方向
Shot 05  发现陈默       → Over Shoulder                ← 视线方向
Shot 06  陈默抬头       → Reverse Shot                 ← 对话正反打
```

这样才不是「AI 每一镜随机选一个酷炫运镜」，而是「**AI 在导演规则下安排镜头**」。

### 模板库可维护
- 模板库 = 前端 JSON 规则表（意图关键词 → 运镜序列 + 默认参数），UI 可增删改。
- 后端 fallback 用同一规则表；未来可加「自定义运镜」进库。

---

## 8. 显存安全门（V1.7 硬性架构约束 · 进程级互斥）

> 用户拍板（2026-08-10 + 2026-08-11 Ollama 架构调整）：TextBackend（Ollama / Qwen3:14B）与 MiniMax H3 **不允许同时常驻 GPU 显存**。这是 **V1.7 的硬性架构约束，不是可选优化**——用户已实际遇到 6.3GB 内存保护 / 视频生成资源问题，这次从架构上避免显存竞争，而不是等出问题再修。

### 8.1 互斥原则
- **Qwen（Ollama 独立进程）与 H3 不允许同时占用 GPU**。
- H3 正在生成 → 禁止启动 Qwen 分析。
- Qwen（Ollama 驻留）正在分析 → 禁止启动 H3。

### 8.2 服务层生命周期
```python
with text_backend.session():          # 进入：ping Ollama + 检查 ComfyUI GPU registry 空
    plan = analyze_script()           # 解析 → 资产语义匹配 → Prompt 草稿 → JSON 校验
# session 结束 → 请求 Ollama 卸载（keep_alive=0）→ /api/ps 确认 → torch.cuda.empty_cache()
generate_h3(...)                      # 之后 H3 独占显存
```

### 8.3 运行阶段分离（产品内两种模式）
```
【AI 分析模式】Ollama/Qwen3:14B → Script → Plan → 资产匹配 → Prompt → Camera → AI Draft → 释放（keep_alive=0 卸载）
【视频生成模式】MiniMax H3 → Shot 01 / 02 / 03 …（与 V1.6「逐镜生成」完全契合）
```
用户无需关心显存：进 Workbench 审核时 Qwen 已卸载，逐镜生成时 H3 独占显存。

### 8.4 落地要点
- `text_backends.py` 提供 `session()` 上下文管理器 + **进程级卸载**（Ollama：keep_alive=0 + `/api/ps` 确认；LocalQwen 备用：del model → gc → empty_cache）。
- 服务层互斥门：分析 / 生成入口检查对方占用（Ollama `/api/ps` 驻留 + ComfyUI GPU model registry + 进程内互斥锁），占用则等待或明确报错（不静默）。
- Ollama 卸载后必须 `torch.cuda.empty_cache()` 清 ComfyUI 侧缓存，必要时同步 CPU 侧释放。

### 8.5 GPU 模型状态检查（Qwen → H3 的强制关卡 · 进程级互斥）

> 用户拍板（2026-08-10 + 2026-08-11 Ollama 架构调整）：**Qwen 分析完成 ≠ 立即开始 H3**，必须经过 GPU 模型状态检查。从架构上杜绝「理论上 Qwen 已经卸载，为什么 H3 又 OOM？」这类最难排查的问题。**Ollama 是独立进程，`del model / gc.collect()` 只对 Transformers 语义有效，对 Ollama 必须以「进程级卸载 + 驻留确认」为准。**

```
Ollama session 结束
  → 请求 Ollama 卸载（/api/generate keep_alive=0）
  → /api/ps 确认 qwen3:14b 不再驻留
  → 释放进程内互斥锁
  → torch.cuda.empty_cache()（清 ComfyUI 侧缓存）
  → 确认 ComfyUI GPU model registry = empty
  → 允许启动 H3
```

- 若检测到 Qwen 尚未完全释放（`/api/ps` 仍驻留 或 registry 非空）：⚠️ 提示「AI 分析模型尚未完全释放，正在清理显存……」——**而不是直接启动 H3**。
- 检查对象：Ollama `/api/ps` 驻留模型列表 + ComfyUI 已加载模型 registry（`comfy.model_management`）+ 进程内 TextBackend 互斥锁三重确认。
- 此检查进 P0 验收项：Qwen 分析→H3 生成必须显式经过该关卡，任一残留引用都会在此拦截。

---

## 9. 人工审核环节（不可跳过）

### 9.0 「AI 制作计划」预览阶段（正式立项补充，用户 2026-08-11）

**V1.7 的 UI 不要直接「剧本 → 自动生成 → Shot」，中间必须有一个「AI 制作计划」阶段：**

```
剧本导入
  ↓
┌─────────────────────────┐
│     AI 制作计划           │
├─────────────────────────┤
│ Scene 01                 │
│  ├ Shot 01 ✓             │
│  ├ Shot 02 ⚠             │
│  ├ Shot 03 ✓             │
│                          │
│ Scene 02                 │
│  ├ Shot 04 ✓             │
│  └ Shot 05 ⚠             │
└─────────────────────────┘
  ↓
确认资产 → 确认 Prompt → 确认运镜 → 进入正式 Workbench
```

**每个 AI Draft 显示：**
```
🤖 AI 草稿
人物    ✓ 林雪  ✓ 陈默
地点    ✓ 七号站台
道具    ✓ 行李箱
Prompt  ✓ 已生成
运镜    ✓ Tracking Right
⚠ 资产「手机」未找到
```

这样用户可以在生成视频之前把问题全部解决。

- 自动生成结果全部以 **AI 草稿** 状态进入现有 Workbench（Shot 加 `aiDraft: true` 标记）。
- 人工可逐镜：改 Prompt / 换资产 / 改运镜 / 一键「采纳全部」/「重新生成此镜」。
- **采纳后即为普通 Shot**，走现有生成/续接/导出链路，无任何特殊分支。
- **`aiDraft` 标记采纳后保留为历史元数据，不影响正常 Shot 语义**（用户拍板 2026-08-10）：以后排查「这一镜是 AI 自动生成还是人工修改」非常有价值；**不参与指纹 / 状态灯 / 导出 / 续接逻辑**。
- 审核门槛只在入口：**AI 不直接触发生成**，必须人工确认后逐镜生成。

> 这一点是用户强调的产品底线：自动化负责提效，最终判断权在人。

---

## 10. LLM vs 规则 vs 人工 分工矩阵（总）

| 环节 | LLM | 规则 | 人工确认 |
|---|---|---|---|
| Scene 粗切分 | — | ✅ 标题/场景标记 | — |
| 实体识别（人物/地点/道具） | ✅ 别名→规范名 | — | 低置信度时 |
| 动作/情绪/对话提取 | ✅ | — | 逐镜可改 |
| Shot 切分 + 时长 | — | ✅ 动作密度拆镜，默认 5s | 逐镜可改 |
| 资产匹配 | ✅ 别名映射 | ✅ 精确匹配 + 扫描 | 未命中弹候选 |
| 结构化 Prompt 草稿 | ✅ 画面/声音/风格 | ✅ 组合 + @转换 + 模板版本化 | 逐镜可改 |
| 运镜 | ✅ 意图分类 | ✅ 模板库 + 跨镜延续 | 逐镜可改 |
| **生成视频** | — | — | **必须人工确认** |

---

## 11. 技术选型（待评审，非承诺）

| 项 | 方案 | 理由 / 复用 |
|---|---|---|
| 文本 LLM 后端 | **新增 `TextBackend` 抽象**，**首先实现 `OllamaBackend`**（本机 Ollama 服务 + `qwen3:14b`），后续补 `OpenAICompatibleBackend`；`LocalQwenBackend`（transformers fp16）降为备用；**含 §8 进程级显存互斥**（`session()` 生命周期 + keep_alive=0 卸载 + `/api/ps` 确认 + H3 互斥） | 现有 `VisionBackend` 是视觉接口，剧本解析是纯文本，需独立抽象；Ollama 与 ComfyUI Python 环境解耦、14B Q4 足以胜任 Scene 切分/实体识别/别名/动作情绪/JSON 输出；不折腾 GGUF 转 Transformers、不装 llama-cpp-python |
| docx 解析 | **推迟 Phase 1.5**：Phase 1 只支持 .md/.txt（`read_script` 编码容错 utf-8-sig→utf-8→gb18030→gbk）；`.docx` 待 Phase 1.5 用 `python-docx` 解包后走同一 parser | 不给 ComfyUI Desktop Python 提前加 python-docx 依赖（用户 2026-08-11 拍板⑩） |
| 资产扫描 | 后端 `os.scandir` 递归扫描四子目录 → 返回候选索引 | 前端只做展示/确认 |
| 运镜模板库 | 前端 JSON 规则表 + 后端同表 fallback | 可维护、可版本化 |
| 剧本导入入口 | **P0 就做完整 UI**：工作台顶栏「📄 导入剧本」→ 选剧本文件 → 选 assets 文件夹 → AI 分析 → 资产匹配确认 → 生成 AI Draft → 进 Workbench（**不做命令导入**） | V1.7 核心入口（用户拍板）；正式流程原则（不走内部 JSON），避免体验仍是「技术工具」 |
| 持久化 | 生成结果直接落 ProjectModel → `project_store.save_project` | 复用现有保存链路 |

**LLM 后端开发顺序（用户拍板）**：**Ollama API 优先**——本机已有 `qwen3:14b`（Ollama 管理），Ollama 独立进程跑推理、与 ComfyUI Python 环境解耦，且「剧本 → 本地解析 → 本地资产 → 本地 H3 → 本地导出」全程不把剧本上传第三方；之后再补 `OpenAICompatibleBackend`。三者（OllamaBackend / OpenAICompatibleBackend / LocalQwenBackend 备用）都走同一 `TextBackend` 接口，业务层完全不关心谁解析。

---

## 12. 落地改动清单（文件级，P0/P1）

### 后端（`director/`）
| 文件 | 改动 |
|---|---|
| `production_plan.py`（新） | **ProductionPlan Schema v1（Commit 1 已交付 #124）**：dataclasses + to_dict/from_dict + 结构校验；MIN/MAX_SHOT_DURATION=[2,8]、MAX_SHOTS_PER_SCENE=8 |
| `script_parser.py`（新） | 剧本读取（**md/txt**，编码容错 UTF-8→GB18030；docx 推迟 Phase 1.5）+ Scene 粗切分规则 + Shot 切分规则（Commit 1 已交付 #124） |
| `text_backends.py`（新） | `TextBackend` 抽象 + `session()` 生命周期 + **进程级显存互斥**（§8：Ollama keep_alive=0 卸载 + `/api/ps` 确认 + `torch.cuda.empty_cache()` + 与 H3 互斥门）；首先实现 `OllamaBackend`（qwen3:14b），`LocalQwenBackend` 备用，后续 OpenAICompatibleBackend |
| `script_analyzer.py`（新） | 剧本→结构化 Scene/Shot JSON（LLM 强 JSON 模板 + 规则校验兜底） |
| `asset_scanner.py`（新） | `assets/` 四子目录扫描 → 候选索引 + 精确匹配 + LLM 别名映射 |
| `prompt_builder_v17.py`（新） | 结构化分区 → H3 组合（复用/对齐现有 `gen_timeline` 的 prompt 组装） |
| `camera_template.py`（新） | 运镜模板库 + 跨镜延续逻辑（意图→运镜序列） |
| `http_routes.py` | 新路由：`/minimax/director/script/analyze`、`/minimax/director/assets/scan`、`/minimax/director/camera/templates` |
| `project_store.py` | 可选：`scan_asset_dir()` 复用；Asset 复制到 assets/ 的辅助 |

### 前端（`frontend/src/`）
| 文件 | 改动 |
|---|---|
| `models/project.ts` | Shot 加 `aiDraft?: boolean`、`emotion?`、`action?`、`dialogue?`（解析元数据，审核后可留可删） |
| `services/comfyApi.ts` | `analyzeScript()` / `scanAssets()` / `getCameraTemplates()` |
| `stores/workbench.ts` | `importScriptPlan(plan)` → 批量建 Scene/Shot/Asset + `aiDraft` 标记 + touch 置脏 |
| `workbench/WorkbenchView.vue` | 顶栏「📄 剧本导入」入口 + 导入流程（选目录/确认资产/预览草稿） |
| `workbench/AssetConfirmDialog.vue`（新） | 资产匹配候选确认（低置信度 → 下拉选择） |
| `workbench/ShotReviewBadge.vue`（新） | AI 草稿角标 + 「采纳/重新生成」 |
| `core/promptSections.ts` | 运镜模板下拉 + `cameraTemplateVersion` 落 Prompt |

### 测试与验收
- 后端：`script_parser` 切分单测、`text_backends` mock 自测、`asset_scanner` 匹配用例、`camera_template` 跨镜延续用例。
- 前端：`importScriptPlan` store 测试、剧本导入 E2E、AI 草稿 → 采纳 → 生成链路回归。
- 全量回归 + vue-tsc + vite build + py_compile + 双目录同步。

---

## 13. 里程碑与验收（Phase 0-5 开发顺序，每阶段独立验收）

> **开发顺序拍板（用户 2026-08-11）**：Phase 0 → 1 → 2 → 3 → 4 → 5，**每阶段独立验收，PASS 才继续**；不过关就修，绝不带病推进。**第一提交只做 Phase 0。**

### Phase 0：基础设施（TextBackend + OllamaBackend/Qwen3:14B + 进程级显存互斥）——【第一提交】
**不碰 UI，纯后端基建。** 目标：先证明「Ollama 稳定分析 → 强 JSON 输出 → 进程级卸载 → H3 生成链完全不受影响」。**Phase 0 第一项 = OllamaBackend + Qwen3:14B 连通性/显存互斥验收**。
- 交付物：`director/text_backends.py`（`TextBackend` 抽象 + `OllamaBackend`（HTTP API + keep_alive 控制，首选）+ `LocalQwenBackend` transformers fp16（备用）+ `session()` 上下文管理器 + 强 JSON 输出复用 `_extract_json_block`/`RETRY_HINT`）；进程级显存互斥门 + GPU 状态检查（§8.5，Ollama `/api/ps` + `comfy.model_management` registry + 进程内互斥锁三重确认）；mock 测试全链路。
- **独立验收（PASS 才进入 Phase 1）**：
  1. Ollama ping → 分析（mock 强 JSON）→ 卸载（keep_alive=0）→ `/api/ps` 确认 qwen3:14b 不再驻留 → `torch.cuda.empty_cache()` → **ComfyUI GPU model registry = empty**；
  2. 卸载后 H3 正常加载生成，**不受任何残留影响**；
  3. Qwen 分析中发起 H3 被互斥门拦截；H3 生成中发起 Qwen 分析同样拦截（明确报错/排队，不静默）；
  4. 全程不出现 Qwen + H3 同时驻留显存。
- **✅ 验收结果（2026-08-11 用户实测 PASS）**：`tools/phase0_ollama_check.py` 真实直连 Ollama 全过（①ping 可达 ②qwen3:14b 已拉取 ③session 内真实 Qwen3:14B 分析《山雨客栈》剧本 → 强 JSON 五字段全对 ④退出 keep_alive=0 卸载 → /api/ps 确认空 + 锁复位 ⑤（ComfyUI 环境跳过，独立进程不可见主进程 registry，该点由 mock 测试 + H3 实测兜底）⑥互斥门反向验证 session 激活拦截 → 释放复位）；**重启 Comfy Desktop 后用户实测正常生成一镜 H3，互斥门放行、显存无残留**。四项验收全部满足，**Phase 0 PASS，进入 Phase 1**。

### Phase 1：剧本解析（P0-A）
- **Commit 1 已交付（#124）**：`production_plan.py`（ProductionPlan Schema v1 = parser 契约）+ `script_parser.py`（md/txt 读取 + Scene 粗切分 + 规则拆镜 + 时长估算 + 轻量实体提取）+ 单元测试 **58 PASS / 0 FAIL**；《山雨客栈》真实样例：2 场景 5 镜，场景角色表正确（林雪 / 柳如烟·沈青崖）。
- **Commit 2 已交付（#125）**：`script_analyzer.py`（Qwen 语义理解补全：`analyze_scene` Scene 批量强 JSON 单一公共入口 + `analyze_shot` fallback + `analyze_script` 单 session 遍历 + 超时/重试/JSON 校验/规则兜底 + **只 `with backend.session()`，不碰 GPU 管理**——模块内无 unload/gpu_models_loaded/keep_alive 操作，入口有 `text_backend_active()` 守卫防绕过显存安全门）+ 单元测试 **74 PASS / 0 FAIL**（mock 全链路，session() 走真实 TextSession 验证显存零操作）。Commit 1 回归 **58 PASS / 0 FAIL**；py_compile 4 文件全绿。
- **独立验收**：`tools/phase1_script_test.py` 喂《山雨客栈》式剧本 → 自动得到结构化 Scene/Shot（人物/地点/道具/动作/情绪/对话），镜头数量由规则约束不失控；解析全程走 Phase 0 显存安全门（仅 session 上下文，不主动管理 GPU）。

### Phase 2：资产扫描匹配（P0-B）✅ 已交付（#128，2026-08-11）
- 交付物：`asset_matcher.py`（**纯规则先行**——精确全等 conf 1.0 auto → 归一后精确 conf 0.95 auto → 子串包含 conf 0.8 pending → 无候选 none；**Qwen 语义匹配（「小林」→ 林雪 96%）留 Phase 4**）+ 固定共享资产库 `{ComfyUI input}/minimax_studio/assets/` 扫描 + `POST /minimax/director/assets/scan` 路由（`asyncio.to_thread` 包裹，零显存）。
- 前端：`productionPlanToProject` 传 `opts.matches` 做高置信度绑定（cast→castIds / location→locationId / 资产填 imageFile）；导入弹窗新增「🎨 资产自动匹配」确认区（auto 缩略图 + pending 下拉候选人工确认 + none 提示手动补图）。
- **独立验收 ✅**：剧本实体 → 精确匹配优先 → 归一/子串兜底；**AI 不创建「林雪_2」**（matched 只引用资产库已有文件）；低置信度返回候选供人工确认。后端 48 断言 + 前端 281 测试 + build 全绿。

### Phase 2-1：Asset Identity + Persistent Binding（#379，2026-08-11 用户拍板）
- 交付物：**稳定 Asset IDs + 持久绑定 Checkpoint**。`asset_registry.py`（纯规则零显存零 Ollama）落盘 `minimax_studio/asset_registry.json`——稳定 **entity_key = `{清洗后最终类型}:{canonical_name}`**（`character:柳如烟`/`location:山雨楼外`）；永久 **asset_id = `asset_{N:03d}`** 顺序分配永远绑定 image_file；**Binding(entity_key → asset_id)**；**人工确认真正落盘**（重新导入复用已接受的确认，不再重新 pending）；`accepted(source=user)` 覆盖 matcher（`match_kind="persisted"`）。
- **数据流单向锁死**：剧本 → ProductionPlan → Entity Registry → Cleanse → Asset Matcher → Persistent Binding；**Registry 绝不反向污染实体发现**（只持久化绑定结果，不产生新实体）。
- 后端 `POST /assets/binding` 保存 + 读取路由；前端 `comfyApi.ts` AssetBinding/AssetBindingPayload + saveAssetBinding/fetchAssetBindings、`Asset.sourceEntityId`、WorkbenchView 确认落盘（user/system）+ `persisted` 徽标 + 重新导入复用。
- **独立验收 ✅**：后端 asset_registry 52 断言 + 全量回归全绿；前端 351/351 测试 + build 全绿；双目录 md5 全一致。**下一步 = 用户本机验证**（重启 Comfy Desktop → 重新导入《山雨客栈》核对已接受绑定复用不重新 pending）。

### Phase 2-2：Stable Binding 跨解析稳定（#383/#384/#385，2026-08-11 用户拍板）

- **核心问题**：Qwen 每次解析的 `entity_id` 必然变化（临时身份），且同一角色可能给出不同写法（柳如烟/柳姑娘、沈青崖/沈大侠、蒙面人/蒙面客）——都会导致 entity_key 分裂、绑定被写成多条无法复用。
- **commit 1（#383）Stable Entity Key / Alias Binding**：职责分离固化（`entity_id`=临时身份 / `entity_key`=`{类型}:{canonical_name}` 稳定身份 / `asset_id`=永久绑定）；绑定只认 entity_key；硬测试 [12] 锁死 entity_id 变化（ent_003→ent_027）→ same asset_id + persisted + bindings 数量不变。65 PASS。
- **commit 2（#384）Alias 归一**：角色称谓变体归一到规则层锚点（`entity_cleanse.py`）：`_APPELLATION_SUFFIXES` 称谓后缀词表 + `_character_anchor_map`（锚点 = shot.characters 规则层真实角色名，脚本上下文驱动不硬编码）+ `resolve_character_alias`（剥后缀→主干→唯一前缀/后缀命中锚点→归一；⛔ 歧义不猜）。**只对 character 类型做**（location/prop 语义不同物由 P0-2 精确对齐 + semantic_incompatible 锁死）。硬测试 [13] 锁死称谓变体跨解析（柳如烟→柳姑娘）→ persisted + same asset_id + bindings 数量不变；无锚点变体（蒙面客）→ 原样保留。entity_cleanse 115 PASS + asset_registry 77 PASS + 全量回归 + golden_path --mock 14/14 + spa_acceptance --mock 43/43 + 双目录 md5 一致。
- **commit 3（#385）收尾核验**：补齐两块验收缺口——`test_asset_registry.py` **[14] 完整剧本两次导入端到端**（2 镜头 4 实体：auto 确认落盘 → 第二次 Qwen 称谓变体「柳姑娘/沈大侠」+ 全 entity_id 重分配 → 4/4 persisted + same asset_id + binding_source=user + bindings 数量不变 + 无 `character:柳姑娘`）+ `tools/spa_acceptance_shanyu.py` **⑪ Alias 归一 + Persisted 复用段**（真实 qwen_plan 两次导入：第一次确认落盘 7 binding → 第二次 7/7 persisted 复用、matches 无变体名、bindings 不变；浏览器核对清单加第 9 条）。asset_registry **84 PASS**（+7）+ spa_acceptance --mock **47 PASS**（+4）+ golden_path --mock **14/14** + 全量后端回归全绿 + 双目录 md5 一致。**硬测试 [6] 注册表不是实体来源 / [12] entity_id 稳定 / [13] alias 归一 / [14] 两次导入端到端 全部锁定——Phase 2-2 三件套闭环。**
- **⛔ 后端需重启 Comfy Desktop**（commit 1+2 Python 代码改动）。**✅ 最终真实验收 PASS（2026-08-11 用户实测）**：`tools/spa_acceptance_shanyu.py`（真实 Ollama）→ **48 PASS / 0 FAIL**（⑪ 段：第一次导入确认落盘 7 binding → 第二次导入 7/7 persisted 复用、matches 无称谓变体「柳姑娘/沈大侠」、bindings 数量不变 7→7）；SPA 重新导入《山雨客栈》→ 7 条「✓ 已确认」persisted 复用、0 待确认、无变体名重复绑定。**Phase 2-2 正式关闭。**

### Phase 3：Prompt Builder（P0-C）——P0 Golden Path 达成
- 交付物：`prompt_builder_v17.py`（结构化分区 → H3 组合）+ **Prompt Template Registry**（`prompt_templates/v1.json`，Shot 存 `promptTemplateVersion`）。
- **独立验收**：结构化生产 Prompt 草稿（Visual/Camera/Style/Sound/Negative）直接可用；模板版本化生效（改模板不动解析逻辑）。

### Phase 4：Workbench 审核（「AI 制作计划」UI）——**已交付（#130，2026-08-11）**
- 交付物：剧本导入完整 UI（📄 导入剧本 → 选剧本 → 选 assets 文件夹 → AI 分析 → 资产匹配确认 → AI Draft → 进 Workbench）+ `AssetConfirmDialog` / `ShotReviewBadge`「🤖 AI 草稿」/ 批量采纳（§9.0）。
- **落地（#130）**：`core/shotReviewStatus.ts` 三项就绪判定（资产绑定 / 五区 Prompt / 运镜）纯函数 + Workbench 审核态（横幅统计、逐镜 ✓/⚠ 徽标、逐镜/一键采纳、生成硬拦截「请先采纳 AI 草稿」）+ `workbench/AssetConfirmDialog.vue` 补资产闭环（复用 `/assets/scan`，cast 未绑定→注册/绑定真实资产 id，asset 缺图→updateAsset 补图，⛔ 绝不创建「林雪_2」）。纯前端，刷新即生效。
- **独立验收**：AI 制作计划阶段每镜 ✓/⚠ 状态；采纳后即普通 Shot 走现有链路；`aiDraft` 保留为历史元数据不参与指纹/状态/导出。

### 实体抽取加固（#354-#359，2026-08-11 用户实测驱动）

- **背景**：用户实测《山雨客栈》「📄 导入剧本」真实流水线后，资产匹配输出极差——「**5 自动 · 2 待确认 · 23 未匹配**」；Qwen 输出垃圾实体：「角色 镜头一」~「镜头九」（镜头标记被当角色）、「道具 青衫」「道具 客栈大堂」「道具 檐角」「道具 雾气」等。用户定性：**不是 Qwen3:14B 差，而是「实体抽取 Prompt + Schema + 后处理 + 资产分类设计不够严格」**。
- **拍板**：分层加固（增量，不重写完整 Entity Registry）；流水线 = **Rule Extractor → Qwen Entity Judge → Entity Registry（entity_cleanse 纯规则）→ Asset Matcher**。
- **落地**：
  - `production_plan.py`：`Entity`/`EntityType`（character/location/prop/costume/environment/effect/architecture/vehicle/creature/unknown）+ `EntitySource`（script=进匹配 / inferred=视觉补充**永不进匹配**）+ `Shot.entities` + `new_entity_id(used)` 防撞车（跨镜收集已占用 id）。
  - `script_analyzer.py`：Prompt 升级「只从镜头原文提取实体，绝不自行创造」+ typed entities（name/type/source/confidence）+ 跨镜 used_ids 线程化。
  - `entity_cleanse.py`（纯规则零显存）：`INVALID_ENTITY_PATTERNS`（`^镜头[一二三四五六七八九十\d]+` 等丢弃）+ 类型强纠正（青衫→costume / 檐角→architecture / 雾气→effect）+ canonical 去重 + **置信度门控**（≥0.85 auto / 0.60~0.85 pending / <0.60 不进匹配）+ 双模式注册表（无实体 plan → legacy 回退保旧测试）。
  - `asset_matcher.py`：消费清洗后注册表；`can_auto=False` 精确命中也被 `_cap_pending` 限制为 pending；每匹配透出 `entity_type/entity_source/entity_confidence/entity_id/can_auto` + **漏斗统计**（discovered→invalid_dropped→dedup_dropped→seeded→cleansed→entered→excluded + auto/pending/none）。
  - 前端导入弹窗：资产匹配确认区展示漏斗文案「AI 发现 N → 丢弃 M → 清洗 K → 进入 J → a 自动 · p 待确认 · u 未匹配」+ 每行类型 chip（角色/地点/道具/服装/…）+ 置信度 % + 门控提示（<85% 需确认 / AI 推断实体不进匹配）。
- **⛔ 纪律**：实体模式**不回填 shot.props**（杜绝「道具 青衫」）；AI 永不创建新资产只复用文件；纯规则层零显存零 Ollama（源码无 unload/keep_alive/torch/ollama）。
- **验收**：后端全量回归（script_analyzer 95 / script_parser 58 / asset_matcher 60 / entity_cleanse 66 / text_backends 28 / script_import 27 / prompt_builder 68 / camera_plan 50 / camera_template 67 全 PASS）；前端 **335 测试 + build 全绿**；双目录 md5 全一致；`tools/golden_path_script_test.py` 增实体断言（Fake 注入「镜头N」垃圾 + inferred「烛火」→ 验证丢弃/排除；漏斗统计打印）→ **--mock 离线全链路 14/14 PASS**。
- **附带修复（#133 遗留）**：对话运镜模板 `dialogue` 原本 `movement=static`（「中景，正反打对切，{subject}轮流入画。」无运镜词，违反用户「每镜必须带明确运镜设计」硬规则且 golden_path ⑩ 必挂）→ 改为「…轮流入画，**随对话节奏轻微推近**」+ `movement=push_in`；对白类镜头因此也带推近运镜（substring 断言 `"正反打对切"` 不受影响）。

### Phase 1.1 语义准确性加固（#361-#368，2026-08-11 用户评审驱动拍板）

- **背景**：Golden Path 14/14 PASS 只证明「管线完整性」不证明「语义准确性」。用户明确指示 **「不要继续 Phase 2，先做一个非常小的 Phase 1.1」**，只改三件事：① Entity 分成两层 **ScriptEntity / VisualElement**；② ScriptEntity 增加 **type / asset_requirement / source / confidence**；③ Asset Matcher 加 **硬约束**（character→cast / location→location / prop→prop）并增加 **semantic incompatible → NONE**。
- **附加要求（用户逐条给出）**：地点层级关系（山雨楼→山雨楼外，UI 显示「⚠ 建议参考 山雨楼外 相似度 0.80 [接受][重新选择]」）；泛化匹配（石阶→后院石阶 标记 generic_reference，原文实体不被资产匹配结果反向污染）；Qwen 拆 **Task A（Script Fact Extraction，只允许剧本明确出现实体）/ Task B（Visual Interpretation，允许合理视觉推断）**；明确 **Asset extraction ≠ Visual description**；结果展示分组（角色✓ / 地点✓?建议参考 / 道具✓?未找到 / 视觉元素·进Prompt / 无需资产匹配 N 计数）。
- **落地（分层增量，纯规则零显存零 Ollama）**：
  - `production_plan.py`：`AssetRequirement`（required/recommended/none）+ `asset_requirement_for_type`（character/location→required；prop/costume/architecture/vehicle/creature→recommended；environment/effect/unknown→none）+ `Entity.asset_requirement` + `VisualElement`（name/type/confidence）+ `Shot.visual_elements`。
  - `script_analyzer.py`：Prompt 拆 **Task A `script_facts`**（characters/locations/props/costumes/entities）+ **Task B `visual_interpretation`**（visual_elements/actions/emotion）；铁律「资产抽取 ≠ 视觉描述」（晨光/山雾/寒气/雨丝/暖黄灯火/尘埃/风/阴影/烛火/倒影/波纹等一律不得进 script_facts.entities）；`_nested_or_flat` 兼容旧扁平结构。
  - `entity_cleanse.py`：`entered` 判定加 `requirement != none`；`_rebuild_gates` 按最终 type 重算；funnel 新增 `visual_only`；`visual_elements` 输出 = shot.visual_elements + visual_only 旧数据兜底。
  - `asset_matcher.py`：`_type_compatible` 硬约束 + `_judge_relation` 语义判定（semantic_incompatible 剔除 / location_hierarchy / generic_reference / contains）+ `match_kind`/`suggest` 透出 + 资产 kind 双通道推断 + 返回契约 `{assets, matches, visual_elements, funnel}`。
  - 前端导入弹窗：**分组展示**（角色/地点/道具）+ 建议参考「⚠ 建议参考 … [接受][重新选择]」+ 视觉元素区「🌫 视觉元素 · 进 Prompt（无需资产匹配 N）」。
- **验收**：后端全量回归（asset_matcher 63 / entity_cleanse 68 / script_analyzer 95 / script_parser 58 / prompt_builder 68 / camera_plan 50 / camera_template 67 / script_import 27 / text_backends 28 / segment_cache_identity 8 全 PASS）；前端 335 测试 + build 全绿；`tools/golden_path_script_test.py` ⑨ 加 **Phase 1.1 契约断言**（visual_elements list + funnel.visual_only + 每条 match 透出 asset_requirement/match_kind/suggest + suggest 与 match_kind 一致 + 视觉元素不混入匹配）→ **--mock 14/14 PASS**（AI 发现 43 → 丢弃 34 → 清洗 12 → 进入 11 → auto 7 / 待确认 0 / 未匹配 4 / 视觉元素 1）；双目录 md5 全一致。
- **⛔ 纪律（延续）**：实体模式不回填 shot.props；AI 永不创建新资产只复用文件；纯规则零显存；相对导入纪律。**前端刷新即生效；后端改动需重启一次 Comfy Desktop 生效**。

### Phase 5：自动运镜（P1-D）
- 交付物：`camera_template.py`（10 基础 + 4~6 常用模板 + `previousCameraState` 跨镜状态机）。
- **独立验收**：同一剧本 → 每镜自动带运镜（模板命中 + 跨镜方向延续）；前后镜衔接合理；模板库 UI 可增改规则。

### 最终 Golden Path 验收（全部 Phase 过关后）
```
📄 导入剧本
 → 选择剧本文件
 → 选择 assets 文件夹
 → AI 分析（Ollama/Qwen 独占 → 强 JSON → 进程级卸载 → 显存门通过）
 → 资产匹配确认（低置信度弹候选）
 → 生成 AI Draft（AI 制作计划预览：每镜 ✓/⚠）
 → 进入 Workbench
 → 人工审核（改 Prompt/换资产/改运镜/采纳）
 → 逐镜生成
 → 走查/成片 → 导出 → 重开项目复验
```

---

## 14. 与 V1.6.x 观察期的衔接

- **V1.7 主题已定为「剧本 → 制作计划」**（本规划记录，更新 `V1.6x_PRODUCTION_HARDENING.md` §5 backlog）。
- **开发节奏（用户 2026-08-11 正式立项拍板）**：~~先观察期 2~3 个项目再开发~~ → **改为「正式立项，Phase 0 先行」**：观察期（V1.6.x Production Hardening）仍继续跑真实项目回归验证 V1.6 生产链，但**不再阻塞 V1.7**；V1.7 从 **Phase 0（Ollama API + TextBackend + 进程级显存互斥）** 起独立推进，Phase 0 验收 PASS 后再进 Phase 1 剧本解析。
- **两条线并行**：V1.6.x 只修 Bug 保生产链稳定（观察期项目照常）；V1.7 Phase 0 走基建（不碰 UI、不动 V1.6 生成链，风险隔离）。
- 观察期内若暴露「剧本/资产导入」相关痛点（如长项目资产匹配、多角色命名），直接增强本规划。

---

## 15. 风险与边界

| 风险 | 对策 |
|---|---|
| LLM 解析幻觉（角色/地点误识别） | 规则强校验 + 低置信度人工确认 + 结构化 JSON 模板 |
| 资产名称匹配（中英文/别名/同音） | 精确匹配优先 + LLM 别名映射 + 候选下拉兜底 |
| 与现有两级池/继承链冲突 | 走 V1.5 已有模型，Asset 自动建库即复用现有解析 |
| 长剧本 token 超限 | 按 Scene 分块解析 + 合并，块号防乱序 |
| H3 Prompt 质量不稳定 | 分区模板化 + 版本化，升级模板不改解析逻辑 |
| 显存/内存（LLM + H3 同时存在） | **硬性约束（§8 进程级显存互斥）**：TextBackend 与 H3 不允许同时占用 GPU，`with session()` 用完即请求 Ollama 卸载（keep_alive=0 + `/api/ps` 确认）+ `torch.cuda.empty_cache()`，运行时互斥门拦截 |
| 「全自动」失控 | AI 只产出草稿，生成必经人工确认（产品底线） |

### 15.1 P0 外部依赖追踪（BLOCKED / NOT FOUND）

> **V17-P0-EXT-H3-SKILL-REF：MiniMax H3 Skill 官方参考文件 `references/base-en.txt` / `ref-en.txt`**
> - **状态：BLOCKED / NOT FOUND**（2026-08-11 登记；用户实查 MiniMax 官方公开仓库无这两个文件的公开副本）
> - **用途**：Phase 4 H3 Prompt Builder 必须遵循官方 base-en.txt（base/keyframe 模式结构）与 ref-en.txt（Ref2VA 六段改写格式）的字段名 / 段序 / 标签 / 时值标注；**不拿网上「类似模板」替代**
> - **查找顺序（固定）**：MiniMax 官方 Skill / 仓库 → 官方文档 → 当前 H3 Skill 的来源仓库 / 安装包 → 追溯 Skill 发布来源版本
> - **约束**：官方依据找到前——**Phase 3 可做 Generation Mode 判定；Phase 4 不实现最终 H3 Prompt Builder**
> - **不阻塞**：Phase 1 / 2 正常推进，不等待此依赖；找到后回补 Phase 4

### 15.2 docx 支持（推迟 Phase 1.5）
> Phase 1 只支持 **.md / .txt**（`read_script` 编码容错：utf-8-sig → utf-8 → gb18030 → gbk）。
> `.docx` 待 Phase 1.5 用 `python-docx` 解包为纯文本再进同一 parser；**不给 ComfyUI Desktop Python 提前加依赖**（用户 2026-08-11 拍板⑩）。

---

## 16. 决策点拍板记录（2026-08-10 用户已全部拍板）

1. ~~文本 LLM 后端选型~~ → **已拍板（2026-08-11 调整）：Ollama API 优先**——`TextBackend` 首先实现 `OllamaBackend`（模型 `qwen3:14b`），后续补 `OpenAICompatibleBackend`；`LocalQwenBackend`（transformers）降为备用。业务层走同一接口。**不折腾 GGUF 转 Transformers，也不装 llama-cpp-python。**
2. ~~P0 是否含「剧本导入 UI」~~ → **已拍板：包含，P0 就做完整 UI**（📄 导入剧本 → 选剧本 → 选 assets 文件夹 → AI 分析 → 资产匹配确认 → AI Draft → 进 Workbench），**不做命令导入**——V1.7 核心入口，避免实际体验仍是「技术工具」。
3. ~~运镜模板库初始规模~~ → **已拍板：先 10 条基础模板 + 补 4~6 条常用**（战斗 / 蒙太奇 / 空镜转场 / 环绕 / 升降），不宜一开始做几十种。
4. ~~aiDraft 留存~~ → **已拍板：保留为历史元数据，不影响正常 Shot**（以后排查「AI 生成 vs 人工修改」有价值），不参与指纹/状态/导出逻辑。
5. ~~开发节奏~~ → **已拍板：先观察期跑 2~3 个真实项目，再正式开 V1.7**（V1.6 刚冻结，先确认生产链稳定）。

> **补充拍板（用户 2026-08-10 + 2026-08-11 Ollama 架构调整）**：**GPU 模型状态检查**——Qwen 分析完成 ≠ 立即开始 H3。Ollama 是独立进程，**必须走进程级互斥**：请求 Ollama 卸载（keep_alive=0）→ `/api/ps` 确认 qwen3:14b 不再驻留 → `torch.cuda.empty_cache()` → **确认 GPU model registry = empty** → 才允许启动 H3；未释放则警告「正在清理显存」而非直接启动（见 §8.5）。

> **正式立项拍板（用户 2026-08-11）**：
> 6. ~~开发节奏~~ → **已拍板：正式立项，Phase 0 先行**。观察期不再阻塞 V1.7；第一提交只做 **Phase 0（Ollama API + TextBackend + 进程级显存互斥）**，**Phase 0 第一项 = OllamaBackend + Qwen3:14B 连通性/显存互斥验收**，先证明 Ollama 稳定分析 → 强 JSON → 进程级卸载 → H3 不受影响，再推进剧本解析。
> 7. **Qwen 不决定镜头数量** → 已拍板：Qwen 负责理解剧情动作，**规则负责拆镜**（动作密度），避免「一段剧情拆成 17 镜」。
> 8. **AI 不能覆盖已有资产** → 已拍板：项目已有「林雪」必须复用，禁止 Qwen 创建「林雪_2」；保护 V1.5 资产一致性体系。
> 9. **Prompt Template Registry** → 已拍板：`prompt_templates/v1.json`，Shot 存 `promptTemplateVersion`；未来 V1.8→h3-v2，老项目不重新生成。
> 10. **previousCameraState 跨镜状态机** → 已拍板：保存 movementDirection/shotSize/cameraPosition/subjectDirection，下一镜参考上一镜，「AI 在导演规则下安排镜头」而非随机酷炫运镜。
> 11. **「AI 制作计划」UI 阶段** → 已拍板：剧本导入后先出「AI 制作计划」预览（每镜 ✓/⚠ + AssetConfirmDialog + ShotReviewBadge），确认后才进正式 Workbench。
> 12. **开发顺序 Phase 0-5** → 已拍板：Phase 0 基础设施 → 1 剧本解析 → 2 资产扫描 → 3 Prompt Builder → 4 Workbench 审核 → 5 自动运镜；**每阶段独立验收 PASS 才继续**。
> 13. **Ollama API 首选（2026-08-11）** → 已拍板：文本后端不写死 `LocalQwenBackend`，架构 = `TextBackend → OllamaBackend（首先实现，qwen3:14b）→ OpenAICompatibleBackend（后续）`；显存安全门改进程级互斥（session 结束 → 请求 Ollama 卸载 keep_alive=0 → `/api/ps` 确认不再驻留 → GPU 状态检查 → 允许 H3）；Qwen3:14B 够用（实体识别/别名归一/Scene-Shot 结构化/五区草稿/资产匹配）；Qwen 只输出结构化分区（visual/camera/style/sound/negative），最终 H3 Prompt 由 Prompt Builder 组装；不折腾 GGUF 转 Transformers，也不装 llama-cpp-python。

> **Phase 1 拍板（用户 2026-08-11，Commit 1 #124 已按此交付）**：
> 14. **Commit 1 = Schema + parser 先行**：顺序 Schema → parser → tests；**ProductionPlan Schema v1 是 parser 的契约，不是 Commit 3**。Commit 1 目标三件套：Schema 先定 / 规则拆 Scene-Shot / .md/.txt 单测通过。
> 15. **ProductionPlan ≠ SPA Shot**：数据流 `剧本 → ProductionPlan → Asset Registry → Generation Planning → H3 Prompt → SPA Shot`，绝不直接塞。
> 16. **字段定死 + 刻意不放**（见 §4.0）：duration 只叫 `duration_sec`；dialogue 对象化 {speaker, text}；speaker→characters[].name→Phase 2 castIds[]；**Phase 1 绝不生成 Asset ID**。
> 17. **Qwen 分析策略**：Scene 批量为主（上下文连贯），失败 fallback 单 shot `analyze_shot()`；单一公共入口 `analyze_scene()`。
> 18. **显存完全不动**：script_analyzer 只 `with backend.session()`，不启动/卸载 Ollama、不查 GPU、不改 keep_alive（全权交给 TextSession）。
> 19. **validation 只做机器侧结构校验**；人工审核 = SPA render.status=review，不新增机制。
> 20. **docx 推迟 Phase 1.5**；Phase 1 只支持 .md/.txt。

---

## 17. 实施清单（V1.7 拍板后按项打勾）

> **第一提交范围（用户 2026-08-11 拍板）：只做 Phase 0。**

- [x] **Phase 0：text_backends.py（TextBackend 抽象 + OllamaBackend 首选（qwen3:14b）+ LocalQwenBackend 备用 + session() 生命周期 + 强 JSON 输出）**（#120 交付 + #121 Ollama 架构调整）
- [x] **Phase 0：进程级显存互斥门 + GPU 状态检查（§8.5：keep_alive=0 卸载 → /api/ps 确认 → empty_cache() → registry empty → 才允许 H3）**（#121）
- [x] **Phase 0：mock 测试全链路（Ollama ping→分析→强 JSON→keep_alive=0 卸载→/api/ps 确认→registry empty；H3 不受影响；互斥门拦截）**（#121，28 项 mock 全绿）
- [x] **Phase 0：真实验收脚本 `tools/phase0_ollama_check.py` + 用户实测 PASS（2026-08-11）**（#122 脚本不 mock 直连真实 Ollama：ping→模型确认→session 内真实 Qwen3:14B 强 JSON→卸载→/api/ps 确认→互斥门反向验证；#123 用户跑 5/5 PASS + 重启 Comfy Desktop 后正常生成一镜 H3 → **Phase 0 验收通过，进入 Phase 1**）
- [x] **Phase 1 · Commit 1：ProductionPlan Schema v1（production_plan.py，parser 契约）+ 纯规则拆 Scene/Shot（script_parser.py：镜头标记/时间变化/动作单元/对白跟随/上限合并/时长估算/实体提取）+ .md/.txt 编码容错 + 单元测试 58 PASS / 0 FAIL**（#124，2026-08-11）
- [x] **Phase 1 · Commit 2：script_analyzer.py（Qwen 语义理解补全：`analyze_scene` Scene 批量强 JSON 单一公共入口 + `analyze_shot` fallback + `analyze_script` 单 session 遍历 + 超时/重试/JSON 校验/规则兜底；**只 with session() 不碰显存**——入口 text_backend_active() 守卫防绕过显存安全门）+ 单元测试 74 PASS / 0 FAIL + Commit 1 回归 58 PASS / 0 FAIL + py_compile 4 文件全绿 + 双目录 md5 一致**（#125，2026-08-11）
- [x] **Phase 1 真实验收：tools/phase1_script_test.py 跑《山雨客栈》12/12 PASS（2026-08-11 用户实测）**（①Ollama ping ②qwen3:14b 已拉取 ③规则拆 2 场景 ④镜头边界对齐 5 镜 ⑤analyze_script 单 session 全链路 ⑥Qwen 补全 2/5 镜完整 ⑦结构字段零改动 ⑧speaker 2/2 回填 ⑨validate=valid ⑩无 Phase 2/3/4 字段 ⑪退出卸载+锁复位 ⑫GPU registry（venv Python 非 ComfyUI 运行时自动跳过，实质已在 #123 验证）⑬互斥门反向验证；退出码 0）→ **Phase 1 全链路验收通过，进入 Commit 3**）
- [x] **Phase 1 · Commit 3：剧本导入正式 UI 入口**（#127，2026-08-11）：后端 `POST /minimax/director/script/import`（规则拆 Scene/Shot → analyze_script Qwen 补全 → `{plan, rule_only, warnings}`；**一步完成**：Qwen 失败自动降级为规则结果 + 警告，不阻塞导入；空剧本/非 JSON 400）+ 前端「📜 导入剧本」入口 + 弹窗（粘贴文本 / .md/.txt 文件 → **ProductionPlan 审核预览**：场景/镜头/角色/总时长 + rule_only 警告 + 逐镜 visual_intent 草稿 → **应用为 SPA 项目**进工作台）+ 映射层 `productionPlanToProject.ts`（scenes→Episode.scenes、shots→Shot 骨架、characters/props→Scene.assets 注册**不绑定**、**visual_intent→Shot.description 审核备注**、content.visual 保持空、绝无 Phase 2/3/4 字段）+ `Shot.description` 模型字段；后端 test_script_import 27 断言 PASS + 回归 58+74+28+27 全绿；前端 productionPlanToProject 单测 11 PASS + 全量 276 PASS + vue-tsc/vite build 全绿 + 双目录 md5 一致；**需重启 Comfy Desktop 生效新路由**）
- [x] **Phase 2：资产自动匹配**（#128，2026-08-11）：`asset_matcher.py`（**纯规则先行**：精确全等 conf 1.0 auto → 归一后精确 0.95 auto → 子串包含 0.8 pending → none；`scan_asset_library` 固定扫 `{ComfyUI input}/minimax_studio/assets/`，延迟 import folder_paths）+ `POST /minimax/director/assets/scan` 路由（`asyncio.to_thread(match_plan)` 零显存，bad plan/json 400）+ 前端 `scanAssets()` API + `productionPlanToProject({matches})` 高置信度绑定（cast→castIds / prop 填 imageFile / location→locations 注册 + defaultLocationId + locationId，**不传 matches 行为不变**）+ 导入弹窗「🎨 资产自动匹配」确认区（auto 缩略图「自动 ✓」/ pending 下拉候选人工确认/ none 提示进工作台手动补图）；后端 test_asset_matcher **48 断言 PASS** + py_compile 双绿；前端 Phase 2 绑定单测 5 新增 + 全量 **281 PASS** + vue-tsc/vite build 全绿 + 双目录 md5 一致；**⛔ AI 绝不创建「林雪_2」**（matched 只复用已有资产，未匹配留空）；**需重启 Comfy Desktop 生效新路由**）
- [x] **Phase 3：Prompt Builder**（#129，2026-08-11）：`prompt_builder_v17.py`（**纯规则 + Prompt Template Registry**：visual_intent 优先/空则 fallback 组合、camera 6 意图优先级 establish→close→dialogue→motion→emotion→ending→default、style 天气配色、sound 环境+对白+情绪音乐、negative 模板；`judge_generation_mode` 强动作→fl2v/续镜→r2v/首镜→t2v 仅「制作计划建议」不改 SPA taskType）+ `prompt_templates/v1.json`（h3-v1 版本化，改措辞不动解析）+ `POST /minimax/director/prompt/draft` 路由（`asyncio.to_thread` 零显存）+ 前端 `buildPromptDrafts()` API + 导入弹窗「🤖 AI Draft」折叠预览（每镜五区只读 + generation_mode 徽标 + 模板版本）+ `productionPlanToProject({drafts, promptTemplateVersion})` 五区映射进 Shot.content + `Shot.aiDraft/promptTemplateVersion` 元数据；后端 test_prompt_builder **68 断言 PASS** + 回归 48+27 全绿；前端 Phase 3 映射单测 6 新增 + 全量 **287 PASS** + vue-tsc/vite build 全绿 + 双目录 md5 一致；**⛔ 纯规则零显存**（模块无 unload/gpu/keep_alive/empty_cache/torch）+ **需重启 Comfy Desktop 生效新路由**）
- [x] Phase 4：剧本导入完整 UI + AI 制作计划审核（📄 导入 → AI 分析 → 资产确认 → AI Draft → 进 Workbench）——**已交付（#130，311 测试全绿）**
- [x] **Phase 5：自动运镜（P1-D）**（#133，2026-08-11）：`camera_template.py`（**10 基础 + 5 补充模板库** + `previousCameraState` 跨镜状态机：**场景内连贯 + 换场景重置** + `_apply_continuity` 三规则=方向延续/对话反打/避免重复推近）+ `pick_camera(shot, scene, prev_state, is_last_shot)` → (camera_desc, intent, next_state) 接入 Prompt Builder 的 camera 意图区（**纯规则零显存**）+ `GET /minimax/director/camera/templates` 路由（`asyncio.to_thread`）+ 前端 `fetchCameraTemplates()` API + `CameraTemplate` 模型字段 + `cameraTemplates.ts` 纯函数（sceneLocationName/sceneTimeHint/shotSubject/shotObject/shotEmotion/findCameraTemplate/fillCameraTemplate 占位符替换）+ `updateShotCameraTemplate` store action + Workbench 摄影区**模板下拉 + 手编共存**（下拉选模板自动填 cameraText + 模板 badge + 手编清标记）+ `productionPlanToProject` 持久化 `shot.cameraTemplate`；后端 test_camera_template **67 断言 + 回归 50 全绿**；前端 335 PASS（cameraTemplates 17 新增 + Phase 5 映射 3 新增 + 回归）+ vue-tsc/vite build 全绿 + 双目录 md5 一致；**⛔ 纯规则零显存**（模块无 unload/gpu/keep_alive/empty_cache/torch）；**需重启 Comfy Desktop 生效新路由，前端刷新即生效**）
- [x] **Golden Path 剧本验收**（#386，2026-08-11 用户实测）：导入《山雨客栈》→ 3 场景 / 9 镜头 / 3 角色 → 资产 **7/7 persisted 复用、0 待确认、Qwen 称谓变体 0** → 场景顺序正确 + 原文逐字保留 → **AI Draft 9/9（五区齐全、内部标记泄漏 0）** → Workbench 逐镜审核 **9/9 采纳** → H3 逐镜生成 **9/9 mp4（r2v 续接 00096-00104）** → 导出成片 **MiniMax_Studio_Movie_00002_.mp4（48.9s）** → 重开项目复验保留 → **V1.7 全阶段交付收官**
