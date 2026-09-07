# State Manager（AI 导演状态管理器）设计方案

> 状态：2026-08-07 草稿（用户确认方向：叙事连续 > 像素连续，80% Ref2VA 状态驱动 + 20% FL2VA 特殊镜头）
> 关联问题：接缝跳（机位/朝向变）不是 bug——是 Ref2VA 的固有设计；真正该锁的是"同一个角色、同一个世界、同一段时间"。

## 1. 设计哲学

| | 像素连续（已放弃） | 叙事连续（目标） |
|---|---|---|
| 上一镜 → 下一镜 | 尾帧 = 首帧 | 角色/场景/时间状态一致 |
| 断点 | 跳帧 = 缺陷 | 切镜 = 镜头语言 |
| 承载工具 | FL2VA 首尾帧硬锁 | Ref2VA 参考图 + 状态描述 |
| 适用 | 连续动作（奔跑/打斗/变身/开门/跳跃） | 叙事推进镜头（80%） |

**关键结论**：AI 短剧系统应该保存"状态"，不是"帧"。每镜独立用 Ref2VA 生成，机位/景别/角度随便切（那是导演语言），但人、服装、世界、时间必须一致。

## 2. 核心概念

### 2.1 全局资产库（Assets）
与具体镜头解耦，全剧级配置：

```
角色资产 (cast)
  - id, 名字, 参考图（正脸/半身）
场景资产 (locations)
  - id, 名字, 参考图
```

每段自动注入：角色图 + 场景图 → `<Picture N>`，作为 Ref2VA 的"世界锚点"。
资产可在分镜级覆写（某镜换成其他角色/场景时）。

### 2.2 剧情状态（State）
每镜结束后维护一份"剧情进行到哪里"，传给下一镜：

```
currentState = {
  character: "小林",
  location: "圣城广场",
  time: "白天",
  action: "仰望天碑",   // 上一镜结束时的动作
  emotion: "肃穆"       // 上一镜结束时的情绪
}
```

### 2.3 镜头描述（Shot）
每个分镜只需写两件事：
- **本镜新动作/运镜**（"新镜头内容"）
- 可选：**状态变更**（如"走进酒馆"→ 更新 location；"脱下外套"→ 更新服装）

## 3. 数据流

```
剧本/分镜
  ↓ 镜头规划器
每镜 = { 角色资产图, 场景资产图, 上一镜状态描述, 本镜新动作 }
  ↓
Ref2VA 独立生成（每镜一张卡，机位随意切）
  ↓
生成视频 + 状态更新（本镜状态变更 → currentState）
  ↓ 循环
下一镜（继承新状态）
```

## 4. 提示词模板（自动生成）

```
[角色资产] <Picture 1> 是角色小林，保持其脸部、发型、服装与身材完全一致。
[场景资产] <Picture 2> 是圣城广场，保持建筑结构、布局与光线氛围一致。
[状态] 上一镜结束时：小林站在圣城广场中央，仰望天碑，神情肃穆，白天。
[新镜头] 人物缓缓低头凝视碑身纹路，镜头跟随目光轻微上摇，光线从云隙洒落。
```

对比现状：
- 旧：`<Picture 1>` = 尾帧 + "第一帧必须完全一致"（像素连续，已放弃）
- 新：`<Picture 1/2>` = 角色/场景资产图 + "状态继承，动作推进"（叙事连续）

尾帧可选保留为 `<Picture 3>`（帮模型感知上一镜构图），但提示词不再要求复刻。

## 5. 与现有代码的映射

| 现状 | 目标 |
|---|---|
| `inherit_ref_tensors` 继承上一段 refs | 全局 cast/locations 资产库自动注入（不依赖"上一段传过"） |
| `_r2v_handoff_prompt` 尾帧复刻语义 | 状态描述 + 新动作语义 |
| `prev_tail` 作为 `<Picture 1>` 硬锚点 | 降级为可选 `<Picture N>`（参考构图，不复刻） |
| 开关 `r2vAutoContinuity` | 升级为「状态驱动」模式开关 |

涉及文件：
- `director/plan.py` — `DirectorPlan` 加 `cast` / `locations` / `current_state`
- `director/gen_timeline.py` — 读 `timeline.assets` / `timeline.state`
- `director/executor_core.py` — 资产注入 + 状态前缀生成（替换 `_r2v_handoff_prompt`）
- `web/js/minimax_image_batch.js` — 全局资产库 UI + 每镜资产选择
- `web/js/minimax_i18n.js` — 新增 ZH 键（EN 无对应项）

## 6. 落地阶段

### 阶段 A：改提示词语义（✅ 2026-08-07 已完成）
- `_r2v_handoff_prompt` 已从"第一帧必须与 <Picture 1> 完全一致"改为**状态继承语义**：
  只锁定"同一角色、同一场景、同一时间"，尾帧降级为环境/构图/光线参考（不必逐像素复刻），
  明确"机位和景别可以自由切换，随后按描述发生新的动作与运镜"。
- 目的：确认"叙事连续"方向下，段 2 不再追求像素锁，视觉上是否可接受。
  实测反馈：两遍解决、角度对、但段 2 提示词写了"抬头"而段 1 尾帧也是抬头 → 动作重复。
  提示词层面仍需用户配合：段 2 写清"新动作 + 新画面必须出现什么"。

### 阶段 B：全局资产库（State Manager 地基）✅ 已实现（Phase B）
- 后端：`DirectorPlan` 加 `global_assets_enabled`；`timeline.assets = { cast[], locations[], defaultCastId, defaultLocationId }`；
  `SegmentPlan` 加 `cast_asset` / `location_asset`；executor 每段把选中的资产图作为 `<Picture N>` 世界锚点注入
  （优先于继承 refs，`ref_image_N` 追加在尾帧锚点之后）
- 前端：image_batch 面板顶部加「角色库」「场景库」区（可上传多张、删图、设全局默认）；
  每镜卡片加「角色」「场景」下拉选择；「全局资产库」独立开关（默认开启）
- 注入规则：每段 1 角色 + 1 场景（建议数量）；角色图 detail=“保持脸部/发型/服装/身材完全一致”，
  场景图 detail=“保持建筑结构/布局/光线氛围完全一致”；开关独立于「自动续接上段」
- **提示词命名自动匹配**（Phase B 增强，2026-08-07）：上传资产时文件名=资产名（如"小林"），
  未手动选择过的段每次渲染按提示词里出现的资产名自动匹配注入（`autoMatchSegmentAssets` / `_match_asset_by_name`）；
  手动下拉选择（含选"无"）优先，不覆盖。名字 <2 字不参与匹配防误触。
- **提示词素材高亮卡片**（Phase B 增强，2026-08-07）：r2v 提示词文本框叠加只读高亮层
  （`attachPromptHighlight` / `renderPromptHighlight`），把 `<Picture N>`/`<Video N>`/`<Audio N>`/`@资产名`
  渲染成带缩略图的彩色卡片，与正文区分；底层仍是纯文本 textarea（保存/模型解析/自动匹配不受影响）。
- **资产改名**（2026-08-07）：点击资产缩略图下方名称就地编辑，回车/失焦保存；
  改名即改「提示词命名自动匹配」用的名字（`batch.assets.rename` 文案）。
- 目的：解决"角色/场景资产必须每段手动传，或依赖上一段"的问题；默认「全局设默认 + 段可覆盖」

### 阶段 C：状态跟踪（✅ 已实现，2026-08-07）
- 后端：维护 `state_holder.current`（executor 内跨段共享），每镜结束后从 `seg.state_change`
  （「状态变更」）更新累计状态；下一镜在 prompt 前自动拼接 `接续上一镜的状态：{cur}。`。
- 前端：r2v 卡片新增「状态变更」输入框（`batch.stateChange` / `batch.stateChangePh`），
  工具栏新增独立「状态跟踪」开关（存 `timeline.output.stateTrackingEnabled`，默认开启）。
- 开关持久化策略与「自动续接上段」「全局资产库」一致：字段缺失默认 ON，仅显式 false 关闭。
- 诊断：每镜报告追加 `状态跟踪: 状态前缀=「…」; 本镜状态变更=「…」`，便于核对拼接效果。
- 初版：状态由用户在分镜卡填写，系统拼接；不做自动提取
- 进阶：Qwen3-VL 自动校验一致性 + 提取状态（待办⑤）

### 待办④：智能尾帧选择（✅ 已实现，2026-08-07）
- 问题：参考图续接固定取上一段「真末帧」作 ref_image_0，若末帧恰好是运动模糊帧/黑帧/过曝帧，
  续接画面会糊或暗。
- 方案：仅参考图路径（r2v 图片锚点 ref_image_0、fl2v 参考图续接）从上一段末尾 window=5 帧中，
  按「灰度 Laplacian 方差（锐度）× 亮度惩罚（过暗/过曝 ×0.5）」挑最佳帧作锚点。
- 保守策略：默认仍取末帧（行为与旧版一致）；仅当末帧锐度显著低于窗口内最佳帧（>35%）才回退选帧。
- **不用于 first_frame 硬锁**：硬锁要求本段首帧=上一段真正末帧（像素连续），选别的帧会断链。
- **开关（2026-08-07 用户要求）**：工具栏新增「智能尾帧」总开关（存 `output.smartTailEnabled`，
  字段缺失默认开启）+ 每段三态下拉「跟随全局 / 开启 / 关闭」（存 `seg.smartTail`，
  缺失=跟随全局、true/false=强制覆盖）。后端 `plan.smart_tail_enabled` 全局 + `seg.smart_tail` 段级。
- 诊断：参考图续接/图片锚点续接的 first_frame 来源会显示「末帧」或「末5帧→第3帧(末帧较模糊)」；
  开关关闭时追加「(智能选帧已关)」。

### 阶段 D：FL2VA 智能分配（✅ 已实现，2026-08-07）
- **核心概念（用户 2026-08-07 澄清）**：FL2VA 不是「高级/大场面镜头」，而是
  **「状态变化镜头」**——前后状态必须精确连接时用 FL2VA。例如：开门、拔剑、变装、
  坐下、拿东西、跳跃落地。大场面（如"城市全景展示"）反而不用 FL2VA。
- **任务拆分（按用户建议 D1-D5，一次不做大）**：
  - D1 镜头任务类型字段：`seg.task_key`（r2v/fl2v），手动 override 优先
  - D2 关键词路由器：`_is_strong_continuity_prompt()` 检测 拔剑/变身/打斗/跳跃/开门/转身/奔跑/爆炸
    等动作连续关键词 → `task_key="fl2v"`
  - D3 手动覆盖 UI：每段卡片「衔接模式」三态下拉（auto/ref2va/fl2va）
  - D4 FL2VA 输入链：上一段输出 → Extract Last Frame → FL2VA First Frame；
    用户上传 `uploaded_first_frame` / `uploaded_last_frame` 优先
  - D5 handoff 状态复用：不另造系统，统一 `handoff_state`（current_frame/character_state/scene_state）；
    R2V 读状态，FL2V 读状态 + 首尾约束
- **路由规则**：每段 `continuity_mode` 三态——auto=按关键词自动判定、ref2va=强制状态驱动、
  fl2va=强制首尾帧硬锁。手动覆盖优先；auto 模式仅对 r2v 段生效（r2v 批里混入 fl2v 段由此而来）。
  后端 `gen_timeline.py` 解析后：`fl2va`→`task_key=fl2v`；`auto`+r2v+关键词命中→`task_key=fl2v`。
- **FL2VA 段输入语义**（`web/js/minimax_image_batch.js`）：卡片「参考图」区切换为「首帧/结束帧」
  两槽——slot 0 首帧（可选，留空自动取上一段真末帧）、slot 1 结束帧（可选，留空则仅首帧硬锁）。
  参考图九宫格/视频/音频槽位对 fl2v 路径无意义，FL2VA 模式下隐藏避免混淆。
- **executor 行为**（`director/executor_core.py`）：r2v 批里路由出的 fl2v 段，无显式首帧时
  走 `fl2v_handoff`（`resolve_prev_segment_output(..., allow_without_continuity=True)`）取上段尾帧
  作首帧硬锁；`_director_fl2v` 强制 `allow_reference=False`，不退化参考图续接；智能尾帧选帧
  不用于硬锁（硬锁必须用真末帧保持像素连续）。
- **refs 保留修复**（`director/gen_timeline.py`）：fl2v 在 `CONTEXT_REFERENCE_EXCLUDED_KEYS` 中，
  `segment_refs_for_context` 会把 fl2v 的 refs 清空——手动标 FL2VA 的段保留 seg_refs
  （首帧 index 0 / 结束帧 index 1），避免用户上传的首尾帧丢失、退化回"仅首帧硬锁"。
  关键词自动路由（auto）仍走清理：无 refs 时由 executor 自动取上段末帧作首帧。
- **目的**：实现"80% Ref2VA + 20% FL2VA"的分配；未来 Qwen3-VL 可直接替代关键词路由器，
  升级为 AI 导演判断（见待办⑤）。

## 7. 前端 UI（示意）

```
┌─────────────────────────────────────────────┐
│ [参考主体生视频]  r2v    [✔自动续接上段]  [✔全局资产库] │
├─────────────────────────────────────────────┤
│ 全局资产                                        │
│  角色库: [小林图] [+ 添加]                      │
│  场景库: [圣城广场图] [+ 添加]                  │
├─────────────────────────────────────────────┤
│ 镜头1 [角色:小林] [场景:广场] 动作:仰望天碑       │
│ 镜头2 [角色:小林] [场景:广场] 动作:低头凝视碑身    │
│   ↑ 状态变更(可选): 动作→凝视碑身               │
└─────────────────────────────────────────────┘
```

## 8. 待确认的决策点

1. ~~**资产图数量**：~~ ✅ 已定：每段 1 角色 + 1 场景（2 张），尾帧可选第 3 张。
2. ~~**状态来源**：~~ ✅ 已定（阶段 C 初版）：状态由用户在分镜卡「状态变更」填写，系统维护累计状态；自动提取已完成（待办⑤ 里程碑 A，见第 9 节）。
3. ~~**开关粒度**：~~ ✅ 已定：「全局资产库」与「自动续接上段」为两个独立开关（默认均开启）。

## 9. 待办⑤ 里程碑 A：Qwen3-VL 一级状态提取 ✅（2026-08-07）

### 9.1 设计决策（用户 6 点反馈，全部纳入）

1. **技术选型不写死**：抽象 `VisionBackend` 接口（`analyze`/`close`），llama.cpp+GGUF 为主实现（`QwenLlamaBackend`），transformers 为备选（`QwenTransformersBackend`）；换模型/推理库不改导演逻辑。
2. **一级状态必须含角色态+镜头态**：提取 `character_state`（pose/direction/emotion）+ `camera_state`（shot/angle）——接缝问题的本质就是缺镜头状态，两字段供未来 FL2VA/R2V 路由使用。
3. **Qwen 是场记不是导演**：只更新状态库，绝不直接决定生成；用户手填 `state_change` 优先，Qwen 提取兜底。
4. **二级一致性（已定稿，见 9.5）**：结构化判断（match booleans + reason），不是 0-1 分数；未来用 CLIP/DINO 做 embedding。
5. **三级重跑（已实现，见 9.6）**：Qwen + 规则检测双重确认，限重跑 1 次。
6. **前端独立面板**：「AI 导演设置」面板不属于 R2V，独立承载五组（自动续接/状态跟踪/FL2VA路由/Qwen视觉反馈/质量控制），里程碑 A 只实现 Qwen 组。

### 9.2 硬件与部署

- 16GB 显存 + 24GB RAM。模型 int4（Qwen3VL-4B-Instruct-Q4_K_M.gguf ≈ 2.5GB + mmproj Q8 ≈ 0.4GB）。
- 模型放 `models/vlm/`；需在 ComfyUI Python 环境装 llama-cpp-python（CUDA wheel，见 QWEN3VL_FEEDBACK_PLAN.md）。
- 段间错峰：H3 生成 → `cleanup_segment_vram` → 懒加载 Qwen3-VL → 推理 → `backend.close()` + `torch.cuda.empty_cache()` → 继续下一段。

### 9.3 新增/修改文件

| 文件 | 类型 | 内容 |
|---|---|---|
| `director/vlm_backends.py` | 新增 | VisionBackend 抽象 + QwenLlamaBackend + QwenTransformersBackend + 默认目录/文件扫描/工厂 |
| `director/qwen_vl_feedback.py` | 新增 | 状态提取强 JSON 模板 + 容错解析 + 重试一次 + 状态前缀格式化 |
| `director/plan.py` | 修改 | DirectorPlan 加 qwen_vl_enabled / qwen_vl_level / qwen_vl_key_segments |
| `director/gen_timeline.py` | 修改 | 从 timeline.output 解析 qwen 开关（缺失默认关/1级） |
| `director/executor_core.py` | 修改 | 段间清理后挂 Qwen 状态提取回调（不阻断主流程） |
| `web/js/minimax_director_panel.js` | 新增 | AI 导演设置面板（Qwen 开关+级别，持久化 timeline.output） |
| `web/js/minimax_image_batch.js` | 修改 | 挂载点 + 面板样式 + batchUi.directorPanel |
| `web/js/minimax_timeline.js` | 修改 | editor 接管面板 + output 加载路径保留 qwen 字段 |
| `web/js/minimax_i18n.js` | 修改 | ZH/EN 键（panel.director.* + tooltip.qwenVl） |

### 9.4 里程碑 A 验证标准

- [x] 前端「AI 导演设置」面板出现，Qwen 开关默认关（缺失字段=默认关，需显式开启）。
- [x] 开关持久化到 timeline.output，跨保存/重载保留（加载路径白名单已补）。
- [x] 段间清理显存后 Qwen3-VL 懒加载，提取尾帧状态，用户未填 state_change 时更新状态跟踪前缀。
- [x] 报告段出现「反馈: Qwen3-VL 状态=…」，或依赖缺失时「反馈不可用: xxx」不阻断生成。
- [x] mock 自测通过：JSON 容错解析（代码块/尾逗号/行注释/块注释）、重试一次、优雅降级空状态、空输入兜底。

### 9.5 里程碑 B：二级一致性检测 ✅（2026-08-07，用户设计：生成帧 vs 资产图）

**B=二级一致性检测（已定稿，用户实测通过）**：本镜生成帧 vs 全局角色/场景资产图（检验「生成的是不是设定里的角色/场景」），输出结构化判断（match booleans + reason 一句话），非 0~1 分数。

- **关键镜头三态覆盖（用户设计：自动+手动可加减）**：每段 r2v 卡片「关键镜头」下拉（`seg.consistencyCheck`）——
  `undefined/"auto"`=有角色/场景资产注入即自动检测（有对照物才算关键镜）、`true`=强制检测（无资产时底层降级「未查」）、
  `false`=跳过（即使有资产注入）。后端 `SegmentPlan.consistency_check` + `gen_timeline` 解析 + executor 三态路由。
- **触发条件**：`qwen_vl_enabled` + `qwen_vl_level>=2` + 三态放行。match=false 只报告提醒，不重跑（重跑留给三级）。
- **报告**：` 一致性: 角色=…; 场景=…; 服装=… (reason)`。
- 新增文件/改动：`qwen_vl_feedback.py`（check_consistency + _to_pil + tensor 入口）、`executor_core.py`（二级块挂载）、
  `plan.py`/`gen_timeline.py`（consistency_check 字段 + 解析）、`minimax_image_batch.js`（下拉）、`minimax_timeline.js`（白名单）、`minimax_i18n.js`（键）。
- **验证标准 B ✅ 已通过**：关键镜头（段1 r2v 有资产注入）报告出现 `一致性: 角色=True; 场景=True; 服装=True (人物面部发型与服装细节与参考图一致…)`；fl2v 硬锁段（无资产注入）不跑。

### 9.6 里程碑 C：三级失败重跑 ✅（2026-08-07，已实现，待用户实测）

**C=三级失败重跑（Qwen + 规则双确认，用户设计：不让 Qwen 一个模型决定重跑，否则误判→无限生成）**：

- **Qwen 语义判断**：`qwen_vl_feedback.detect_issue` 采样首/中/尾帧喂 VLM，输出 `{issues[], severity, reason}`；强 JSON + 失败重试一次；
  severity 缺失/乱写时按 issues 命中 `SEVERE_ISSUE_KEYWORDS`（崩坏/多手/换脸/畸形…）兜底判 high/low（修复：原先缺失直接判 low 的缺陷）。
- **规则客观确认**：`state_rule_check.rule_check_frames` 纯像素检测（平均亮度 <0.03 过暗 / >0.97 过曝、标准差 <0.012 纯色、
  Laplacian 方差 <0.00035 模糊、分辨率 <320×240），全 torch 张量运算，无 VLM 依赖，最多采样 32 帧。
- **confirm_rerun 双确认**：Qwen issues 非空 + severity=high **且** 规则 abnormal → 才重跑；任一侧不成立不重跑。
- **重跑**：`seed=(seed+1) & 0xFFFFFFFF` 重新采样，替换 decoded/chunk/audio 输出 + 写段缓存；重跑失败保留原输出；
  报告追加 `重跑: 已重跑(原因; seed=…→…)` / `不重跑: …` / `轻微问题: …`。
- **触发条件**：`qwen_vl_enabled` + `qwen_vl_level>=3`。
- 新增/改动文件：`state_rule_check.py`（新增）、`qwen_vl_feedback.py`（detect_issue + SEVERE_ISSUE_KEYWORDS）、
  `executor_core.py`（三级块挂载 + qwen_rerun_note）、`minimax_i18n.js`（级别说明更新）。
- **验证标准 C ✅ 已通过（防误判，用户 2026-08-07 三镜实测）**：段1 报告出现 `不重跑: Qwen 报「多手、肢体畸形、面部崩坏、画面崩坏」但规则无客观异常，不重跑`——detect_issue 命中严重关键词→severity=high→进重跑候选→规则无异常→双确认不成立不重跑；三段链路 + 报告行完整。多手类语义崩坏（画面清晰但画错）按设计不重跑；用户 AskUserQuestion 确认保持现状（不加目标检测、不降像素阈值）。
- mock 自测已通过：JSON 容错 / detect_issue（含 severity 兜底）/ confirm_rerun 双确认 / 规则阈值（正常帧零触发、全黑/全白/纯灰/模糊正确触发）。
