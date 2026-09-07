# ComfyUI_MiniMaxH3_Director 修改记录（CHANGELOG）

> 用途：**下次修改/优化的总索引**。本文件汇总 2026-08-07 起本节点的所有修改，
> 每条包含：涉及文件、改动要点、设计决策（Why）、验证标准、已知坑（How to apply）。
> 开发时先看本文件 → 再按需跳转对应设计文档。

## 文档地图

| 文档 | 用途 |
|---|---|
| **CHANGELOG.md**（本文件） | 修改记录总索引，所有改动的时间线 + 坑速查 |
| **QWEN3VL_FEEDBACK_PLAN.md** | 待办⑤ Qwen3-VL 三级反馈实施计划（A/B/C 全部定稿） |
| **STATE_MANAGER_DESIGN.md** | State Manager AI 导演状态管理器设计方案（含 9.x 里程碑 A/B/C 详情） |
| **LATENT_CHAINING_FIX.md** | MiniMax H3 无缝衔接正确机制（官方源码版，init_latent 方案已废弃） |
| **README.md** | 项目说明（中文默认） |
| **README_EN.md** | 项目说明（英文） |

## 环境与硬约束（所有改动的前提）

- **用户硬件**：16GB 显存 + 24GB RAM。H3 生成峰值约 10-14GB 显存；Qwen3-VL 必须「段间错峰 + 强制卸载」，不与 H3 抢显存。
- **ComfyUI Desktop Python 3.13.12**（`D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe`）。
  **没有 llama-cpp-python cp313 预编译 wheel → 只能走 transformers 后端**（QwenTransformersBackend，fp16，约 9GB）。
  **别推荐 pip 装 llama-cpp-python**（cp313 必踩 MSVC 编译坑）。
- **视频生成只用 MiniMaxH3**（LTX/Wan 等已淘汰，勿再推荐）。
- **i18n 只加 ZH 键**（EN 无对应项）——核心路由字段例外（continuityMode 那批加了 EN）。
- **测试故事设定**：角色 = **陆玄**，场景 = **圣城广场**。
- **双目录同步**：工作区源 `D:\夸克网盘\ComfyUI_MiniMaxH3_Director` = 部署 `D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\custom_nodes\ComfyUI_MiniMaxH3_Director`。
  改文件必须两端同步。bash VM 卡死时用文件工具直接改部署目录（逐文件对应）。

---

## 2026-08-07 修改记录（按时间线）

### 1. fl2v「自动续首帧」开关（用户需求，早期）

- **功能**：勾选后第 2 镜起首帧自动用上一镜尾帧（`prev_tail[-1]`），无需逐镜上传；显式首帧优先。
- **涉及**：`director/fl2v_timeline.py`（`_normalize_shots(auto_handoff=...)` 保留无首帧镜）、
  `director/executor_core.py`（fl2v 分支 `first_frame=prev_tail[-1:]`）、`web/js/minimax_fl2v.js`（`.bd-fl2v-auto` 开关）、
  `web/js/minimax_timeline.js`（`!isFl2vMode()` 守卫）、`web/js/minimax_i18n.js`（只加 ZH 键）。
- **后端字段**：timeline schema v5 `output.continuityEnabled` / `continuityOverlapFrames`。
- **坑**：`updateSegmentContinuityUI` 对非 fl2v 模式会强制清零 `continuityEnabled` → 后续加 r2v 自动续接必须用独立字段。

### 2. 参考图续接模式（fl2v，二修）

- **触发**：fl2v + 无显式首帧 + 无显式尾帧 + `audio_vae` 已连（ReferenceToVideo 依赖 audio_vae）。
- **实现**：`_build_minimax_inputs(allow_reference=...)` → `ref_images={"ref_image_0": prev_tail[-1:]}`，走 `MiniMaxH3ReferenceToVideo`。
- **提示词**：`fl2v_timeline.py` `REF_CONT_PROMPT_PREFIX/SUFFIX`（"延续参考图 <Picture 1>…"），`reinforce_fl2v_prompt(reference_mode=True)`。
- **回退**：有显式尾帧 / audio_vae 未连 → 回退 first_frame 硬锁。
- **验证**：2 段 fl2v 报告第 2 段 `fl2v — Reference-to AV (~1 ref image(s))` + 衔接行确认。

### 3. 从视频续接模式（fl2v，三修）

- **新增** `DirectorPlan.handoff_mode`（"image"/"video"），`output.handoffMode`。
- **实现**：`handoff_mode=="video"` 且 `prev_tail>=5 帧` → `ref_videos={"ref_video_0": prev_tail.clone()}`（整段视频，继承运动轨迹）。
- **提示词**：`REF_VIDEO_CONT_PROMPT_PREFIX/SUFFIX`（"延续参考视频 <Video 1>…继承其运动轨迹"）。
- **前端**：`minimax_fl2v.js` `.bd-fl2v-handoff-sel` 下拉（image/video）。i18n 只加 ZH。
- **注意**：整段 ref_video 更吃显存。

### 4. allow_reference bug 修复（四修）

- **症状**：选「从视频续接」仍回退 first_frame 硬锁。
- **根因**：`_run_one_segment` 里 `allow_reference=handoff_reference`（不含 video）→ video 模式被排除。
- **修复**：`allow_reference=(handoff_reference or handoff_video)`，具体走哪条由 `handoff_mode` 决定。

### 5. r2v 自动续接（ref2va，五修）

- **背景**：fl2v 用 fl2va checkpoint、r2v 用 ref2va checkpoint，**两个模型不一样**——fl2va 对参考视频不敏感，
  「从视频续接」应移植到 r2v/ref2va 工作流。
- **前端**：`minimax_image_batch.js` 工具栏「自动续接上段」开关（`.bd-batch-r2v-auto`），存 `timeline.output.r2vAutoContinuity`。
  **必须独立字段**（见 #1 的坑）。
- **后端**：`plan.py` `DirectorPlan.r2v_auto_handoff`；`gen_timeline.py` 读（仅 task_key=="r2v" 生效）；
  `executor_core.py` 段 task=r2v 且 index>0 且开启时 `resolve_prev_segment_output(..., allow_without_continuity=True)` 取 prev_tail。
- **关键坑（已修）**：`resolve_prev_segment_output` 原来 `if not plan.continuity_enabled: return None` → 加了 `allow_without_continuity: bool=False` 参数。
- **前提**：需 audio_vae 已连；上一段须已生成。

### 6. 复合续接 + "一段视频放两遍" 修复（六修）

- **症状**：纯 ref_video 续接 + 提示词全"延续/保持" → ref2va 把参考视频再放一遍。
- **修复（复合锚点）**：同时注入 `ref_image_0 = prev_tail[-1:]`（`<Picture 1>` 像素锚定）+ `ref_video_0 = prev_tail`（`<Video 1>` 运动上下文）。
- **提示词**：前缀改为「新镜头接续：<Picture 1> 是上一镜头最后一帧，新镜头从这一帧开始；<Video 1> 是上一镜头完整视频，提供上下文。从参考画面末尾自然衔接，随后发生新的动作与运镜，不要复刻或重放参考视频」；动作描述用「新镜头内容：」。

### 7. r2vAutoContinuity 持久化坑 + 退化诊断（七修 + 十修默认开启）

- **症状**：重启后 r2v 段退化成 t2v（"不重复了"是假象，接缝彻底断）。
- **根因**：`timeline.output.r2vAutoContinuity` **不会持久化到已保存的工作流 JSON**，重启后字段丢失 → 开关回到默认关。
- **诊断**：executor 报告里 r2v 段 index>0 且无参考媒体且开关未开时，衔接行追加「注意: 该 r2v 段无参考媒体且未开启自动续接上段，已退化为文生视频(t2v)」。
- **十修（默认开启）**：`gen_timeline.py` 解析改为字段缺失一律视为 True（仅显式 false/"false"/"0" 关闭）；前端读缺失字段默认勾选。
- **How to apply**：**以后看到报告 "Text-to AV" 第一反应是开关没勾/字段丢失，不是代码 bug。**

### 8. 图片锚点方案（去视频，八修）—— r2v 最终方案

- **背景**：复合续接（尾帧图+整段视频）实测仍被 ref2va 重放。
- **最终机制**：只注入 `ref_image_0 = prev_tail[-1:]`（`<Picture 1>` 尾帧）+ **继承上一段 r2v 段的角色/场景参考图**
  `ref_image_1..N`（`<Picture 2>…`）。`executor_core.inherit_ref_tensors` 用 `refs_to_kwargs_for_context("r2v", prev_seg.refs)` 收集，每张取 `t[:1]`。
- **提示词**：`_r2v_handoff_prompt(prompt, ref_image_count=...)` 动态前缀——尾帧 + 角色图 + 场景图，
  收尾「保持人物与环境一致，随后发生新的动作与运镜，不要复刻或重放上一镜头」。旧 `_R2V_HANDOFF_PREFIX` 常量已删。
- **诊断**：衔接行 `first_frame=图片锚点续接(ref_image_0=上段尾帧 + 继承N张角色/场景参考图)`。
- **验证**：段 2 报告应为 `~1+ 张 ref image`（**无 ref video**）+ `图片锚点续接` 诊断。

### 9. 接缝跳：ref2va 无 first_frame 硬锁（九修→十一修演进）

- **官方事实**：`MiniMaxH3ReferenceToVideo` 的 ref_images 全进 `minimax_refs`（条件参考），**没有 first_frame 硬锁**；
  首帧机位/朝向/景别可自由发挥。官方两节点互斥——**无法在 ref2va 段上同时"首帧硬锁+参考图"**。
- **九修（首帧强约束）**："第一帧必须与 <Picture 1> 完全一致、不得改机位"——实测"角度对了但动作被焊死，天碑没进画面"。
- **十一修（状态继承语义）**：只锁定「同一角色、同一场景、同一时间」，`<Picture 1>` 尾帧降级为「环境/构图/光线参考，不必逐像素复刻」，
  明确「机位和景别可以自由切换，随后按描述发生新的动作与运镜」。
- **Why**：AI 导演系统应走「叙事连续 > 像素连续」，机位/景别/姿势应可自由切换。像素级无缝只在少数连续动作镜头（FL2VA 硬锁路径）需要。

### 10. 段 2 基本复刻段 1（十二修）

- **症状**：段 2 基本复刻段 1（动作没推进）。
- **根因**：ref2va 对图片锚点遵循太强，弱约束前缀压不住。
- **修复**：`_r2v_handoff_prompt` 强化新动作语义——「同一角色、同一场景、同一时间的**新镜头，但画面与动作都是新的**」、
  「人物必须做出清晰可见的新动作，机位和景别可以自由切换，镜头重新取景」、「<Picture 1> 仅作为环境/构图/光线参考，不要复刻姿势与画面」、
  收尾「新镜头必须有清晰可见的新动作与情节推进，画面不要与上一镜头雷同」。
- **用户提示词也要配合**：段 2 必须写**具体、可执行的新动作**（"低头俯视 + 伸手轻抚碑文 + 镜头拉近特写"），不能只写弱动作。
- **How to apply**：段 2 画面雷同段 1 → ①确认前缀是十二修版本（部署同步+重启）②段 2 提示词写足具体动作动词 + 新机位/景别。

### 11. 全局资产库 Phase B（任务 #19-24）

- **设计决策（AskUserQuestion 确认）**：每段注入 **1 角色 + 1 场景（2 张）**，尾帧可选第 3 张；
  「全局资产库」(globalAssetsEnabled) 与「自动续接上段」(r2vAutoContinuity) **两个独立开关**，默认均开启；
  「全局设默认 + 段可覆盖」——资产面板可设全局默认，新建段自动回填，每段下拉可单独覆盖。
- **timeline schema**：`timeline.assets = { cast:[{id,name,imageFile}], locations:[...], defaultCastId, defaultLocationId }`。
- **后端**：`plan.py` `GlobalAsset` dataclass + `SegmentPlan.cast_asset/location_asset` + `DirectorPlan.global_assets_enabled`；
  `gen_timeline.py` 解析 + 每段按 `seg.castId/locationId`（缺省回填默认）挂载；**仅 r2v 段注入**；总开关字段缺失默认开启。
- **executor**：资产图作为 `ref_image_N` 追加在尾帧锚点后（跳过已占用槽位）；动态 pic_roles 提示词（角色图/场景图各一句"保持…完全一致"）；
  诊断行「全局资产注入(角色/场景N张)」或「图片锚点续接(ref_image_0=上段尾帧 + 全局资产N张)」。
- **前端**：`minimax_image_batch.js` 工具栏「全局资产库」开关 + 资产面板（角色库/场景库、传图/删图/设全局默认）+ 每段「角色」「场景」下拉。
- **i18n**：只加 ZH 键（`panel.batch.globalAssets` 等 12 个）。

### 12. 提示词命名自动匹配资产（Phase B 增强）

- **机制**：上传资产时文件名 = 资产名。前端 `autoMatchSegmentAssets(seg, assets)` 扫每段 `seg.prompt`，
  出现资产名（≥2 字）即返回该资产 id；后端 `gen_timeline.py` `_match_asset_by_name` 兜底。
- **优先级**：手动下拉选择（含选「无」）> 提示词命名自动匹配 > 全局默认。用 `seg.castManual/locationManual` 标记手动选择。
- **已修 bug**：原「选无无效」——下拉选「无」时后端把空串当"未设置"回退全局默认。改为区分字段缺失（回退）vs 显式空串（不注入）。
- **How to apply**：换角色/场景，把资产文件名起成提示词里用的名字即可自动匹配；个别段特殊想强制某资产就手动下拉（会被记住）。

### 13. 提示词素材高亮卡片 + 资产改名（Phase B 增强）

- **资产改名**：`renderAssetItem` 点击资产名就地变 input，回车/失焦保存。改名即改「命名自动匹配」用的名字。
- **提示词素材高亮层**：`minimax_prompt_mentions.js` 新增 `attachPromptHighlight`——r2v 提示词 textarea 外包 `.bd-phl-wrap`，
  叠只读高亮 div（`bd-phl-hl`），把 `<Picture N>`/`<Video N>`/`<Audio N>`/`@资产名` 渲染成带缩略图的彩色卡片；
  **底层仍是纯文本 textarea**（保存/模型解析/命名匹配不受影响）。
- **对齐关键（已修 bug）**：textarea 有竖向滚动条（~8px）→ 高亮层全宽 → 换行点不同 → 光标错位。
  修复：textarea 隐藏滚动条（`scrollbar-width:none` + `::-webkit-scrollbar{display:none}`）+ 关键对齐属性 `!important` 压过更高特异性。
- **滚动溢出 bug（2026-08-07 二修）**：用户反馈"鼠标向上划，文本全部超出边界"。根因：`.bd-phl-hl` 用 `inset:0` 把高度**锁死为容器高度**，
  滚动时 `translateY(-scrollTop)` 平移的是一个"视口大小的块"，内容超出部分被自身 `overflow:hidden` 裁掉、随平移整体移出边界（滚动到底必露白）。
  修复：`.bd-phl-hl` 去掉 `bottom:0`（`top:0;left:0;right:0`）让高度**由内容撑开**（= textarea scrollHeight），`.bd-phl-wrap` 加 `overflow:hidden` 承担裁剪，
  textarea 加 `overflow-anchor:none` 防浏览器滚动锚点干扰。数学验证：任意 scrollTop 下 textarea 可视窗口 `[st, st+clientHeight]` 与 hl 平移后可见内容区间一致，
  底部始终被覆盖不露白。**How to apply**：以后高亮层"滚动时内容跑到边界外/露白"，第一反应检查 hl 是否 `inset:0` 锁死高度——必须由内容撑开 + wrap 裁剪。
- **How to apply**：以后高亮层光标/文字错位，先查滚动条宽度或特异性覆盖。

### 14. 状态跟踪 Stage C：current_state + 状态前缀（任务 #27-30）

- **机制**：executor 跨段共享 `state_holder = {"current": ""}`；每镜 prompt 前自动拼接「接续上一镜的状态：{cur}。」；
  本镜结束后用 `seg.state_change`（「状态变更」输入框）更新 current_state。叙事状态跨镜传递。
- **后端**：`plan.py` `SegmentPlan.state_change` + `DirectorPlan.state_tracking_enabled`；
  `gen_timeline.py` 解析 `seg.stateChange`/`output.stateTrackingEnabled`；`executor_core.py` 状态前缀 + 更新 + 诊断行。
- **前端**：`minimax_image_batch.js` 工具栏「状态跟踪」开关（`.bd-batch-r2v-state`）+ 每镜「状态变更」输入框（`stateRow`）。
- **开关持久化**：字段缺失默认 ON，仅显式 false/"false"/"0"/"off"/"no" 关闭。
- **与自动续接的区别**：自动续接=参考画面保证像素/主体连续；状态跟踪=文本前缀保证叙事状态连续。两者独立可同开。
- **测试设定**：状态跟踪测试分镜正式用 **陆玄 / 圣城广场**。

### 15. 秒数框"自己变"修复（任务 #28）

- **根因**：`applySec` 防抖回调无条件回写显示值（停顿 >200ms 覆盖半截输入）；`flushBatchDurationInputs` 提交正在聚焦的半截值。
- **修复**：`applySec` 只在 `document.activeElement !== secInput` 时回写；flush 开头 `if (input === document.activeElement) continue;`。
- **How to apply**：以后数字输入"输入途中值自己跳"，先查防抖回调是否无条件回写显示值 + flush 是否跳过 activeElement。

### 16. image batch payload 白名单丢字段（任务 #29）—— ⚠️ 一号嫌疑点

- **症状**：前端填了状态变更，但报告三镜全是 `状态前缀=无` 且无 `本镜状态变更`。
- **根因**：`minimax_timeline.js` `buildTimelinePayload` 的 image batch（r2v）分支用白名单重建 segments，
  新 per-segment 字段（`stateChange`/`castId`/`locationId`/`castManual`/`locationManual`）**全被剥掉**。
  全局资产"看起来生效"是因为 gen_timeline 有命名自动匹配兜底；stateChange 无兜底 → 彻底失效。
- **修复**：白名单补 `castId/locationId/castManual/locationManual/stateChange`。
- **How to apply（重要）**：以后给 r2v 段加任何新 per-segment 字段（前端 `seg.xxx`），**必须同时检查白名单有没有带上**——"前端填了但后端收不到"的一号嫌疑点。

### 17. 状态跟踪诊断时序 bug（任务 #30）

- **症状**：镜 1 显示 `状态前缀=「陆玄仰望天碑…」`（第一镜开始前应为"无"）；镜 2 显示的是镜 2 自己的 state_change 而不是镜 1 传的。
- **根因**：诊断代码在段**结束后**读 `state_holder.get("current")`，而状态更新也在段末、且在诊断之前 → 读到的是本镜刚更新的状态。
  实际拼接是正确的（镜 2 提示词确实前缀了镜 1 状态），只是报告显示错。
- **修复**：段开始时捕获快照 `_seg_start_state`，前缀拼接和诊断都用快照。
- **How to apply（通用）**：诊断若声称"某段开始时的值"，必须在段开始时捕获快照，不能到段末再读共享可变状态。

### 18. 智能尾帧选择（待办④，任务 #31-37）

- **范围**：仅参考图路径——r2v 图片锚点 `ref_image_0`、fl2v 参考图续接 `ref_image_0`。**不用于 first_frame 硬锁**。
- **算法**：`executor_core._pick_handoff_anchor(frames, window=5, min_luma=0.04, max_luma=0.97)`——取末尾 5 帧，
  算灰度 Laplacian 方差（锐度），过暗（<0.04）/过曝（>0.97）分数 ×0.5；默认仍取末帧，仅当末帧锐度显著低于窗口内最佳帧（>35%）才回退选帧。
- **开关**：工具栏「智能尾帧」总开关（`output.smartTailEnabled`，默认开启，仅 r2v 显示）+ 每段三态下拉「跟随全局/开启/关闭」（`seg.smartTail`）。
- **实测结论（用户确认）**：段 2 关掉智能选帧后复刻段 1；改回跟随全局后正常。**智能选帧默认开启是稳妥的，不要建议用户关。**
- **How to apply**：以后看到「某段关闭智能选帧后复刻上一段」，第一反应怀疑这个，而不是接缝机制。段级三态语义：
  缺失/"auto"→None（跟随全局）、"on"/true→True、"off"/false→False。

### 19. 阶段 D：FL2VA 智能分配（任务 #38-43）

- **核心概念澄清（用户明确）**：FL2VA 不是"高级/大场面镜头"，而是**「状态变化镜头」**——前后状态必须精确连接时用
  （开门/拔剑/变装/坐下/拿东西/跳跃落地）；大场面（如城市全景）反而不用 FL2VA。
- **D1** `seg.task_key`（r2v/fl2v），手动 override 优先。
- **D2 关键词路由器**：`gen_timeline.py` `_is_strong_continuity_prompt()` + `_STRONG_CONTINUITY_KEYWORDS`（开门/拔剑/变身/打斗/跳跃/奔跑/爆炸等）
  → auto 模式下 r2v 段命中即转 fl2v。
- **D3 手动覆盖 UI**：每段卡片「衔接模式」三态下拉（auto/ref2va/fl2va，`normalizeContinuityMode`），存 `seg.continuityMode`；
  fl2va 时卡片切换为「首帧/结束帧」两槽（slot0 首帧可选、slot1 结束帧可选，参考图/视频/音频槽隐藏）。
- **D4 FL2VA 输入链**：executor fl2v 分支 `explicit_start=refs[0]`、`explicit_end=refs[1]`；无显式首帧时 `fl2v_handoff` 走
  `resolve_prev_segment_output(allow_without_continuity=True)` 取上段真末帧作首帧硬锁；无显式尾帧则仅首帧硬锁。
- **D5 handoff 状态复用**：沿用 r2v_handoff + allow_without_continuity 方向；`_director_fl2v = fl2v_handoff or continuity_mode=="fl2va"`
  强制 `allow_reference=False`（不退化参考图续接）；智能尾帧选帧不用于硬锁（必须用真末帧像素连续）。
- **refs 保留修复（关键坑）**：fl2v 在 `CONTEXT_REFERENCE_EXCLUDED_KEYS`，`segment_refs_for_context("fl2v", ...)` 返回 `[]`。
  手动标 FL2VA 的段在 gen_timeline 保留 seg_refs（首帧 index0/结束帧 index1），否则用户上传的首尾帧丢失退化"仅首帧硬锁"。
- **字段**：`SegmentPlan.continuity_mode`（"auto"/"ref2va"/"fl2va"）+ `seg.continuityMode` payload 白名单。
- **误导诊断修复**：`if auto_handoff and not ref_videos and not ref_images:` 先判断 `fl2v_handoff or continuity_mode=="fl2va"`
  → 显示正面标记 `FL2VA 强制首帧硬锁`；仅普通 fl2v 自动交接段回退硬锁才提示原因。
- **验证（用户三镜实测通过）**：段1 r2v + 资产注入；段2 提示词含"拔剑"→ 自动路由 fl2v + `first_frame=上一段尾帧`；
  段3 手动 FL2VA → fl2v 硬锁。**阶段 D 硬锁路径与 fl2v 面板参考图/从视频续接路径互不干扰**（r2v_auto_handoff 默认 False，仅 r2v 批路由出的 fl2v 段走硬锁）。

### 20. 待办⑤ 里程碑 A：Qwen3-VL 一级状态提取（任务 #44-49）

- **设计决策（用户 6 点反馈，全部纳入）**：技术选型不写死（`VisionBackend` 接口）；一级状态必须含
  `character_state`（pose/direction/emotion）+ `camera_state`（shot/angle）——**接缝问题的本质就是缺镜头状态**；
  **Qwen 是场记不是导演**（只更新状态库，绝不直接决定生成）；用户手填 `state_change` 优先，Qwen 提取兜底；前端独立「AI 导演设置」面板。
- **新增** `director/vlm_backends.py`：`VisionBackend` 抽象（`analyze`/`close`）+ `QwenLlamaBackend` + `QwenTransformersBackend` + 工厂。
- **新增** `director/qwen_vl_feedback.py`：状态提取强 JSON 模板 + `_extract_json_block` 容错解析（代码块/尾逗号/行注释/块注释）+ 失败重试一次 + `format_state_prefix`。
- **后端**：`plan.py` `qwen_vl_enabled/qwen_vl_level/qwen_vl_key_segments`；`gen_timeline.py` 解析（缺失默认关/1级）。
- **executor**：段间清理显存后懒加载 Qwen → 提取尾帧状态 → `backend.close()` + `torch.cuda.empty_cache()`；不阻断主流程。
- **前端**：`minimax_director_panel.js`「AI 导演设置」面板（Qwen 开关 + 级别）。
- **实测 bug 修复**：① JSON 块注释解析 ② `obj=None` 时 AttributeError ③ `format_state_prefix` 未拼 camera_state。
- **A5 加载路径坑**：`normalizeOutputContinuity` 显式字段列表要补 `qwenVlEnabled`/`qwenVlLevel`，否则跨保存/重载丢失开关。
- **实测定稿**：报告每镜出现 `反馈: Qwen3-VL 状态=「…」`；character_state+camera_state 全字段齐；用户手填优先原则生效。

### 21. 环境约束：Python 3.13 无 llama-cpp-python → transformers 后端（任务 #50）

- **硬约束**：ComfyUI Desktop Python 3.13.12；官方 abetlen index（cu121/cu124）只有 cp39-cp312 Windows wheel，没有 cp313；
  pip 装会退源码包需 MSVC+CUDA 编译 → 失败。
- **用户选定**：transformers 后端。`QwenTransformersBackend` 默认 `load_in_4bit=False`（fp16，4B 约 9GB，段间错峰下 16GB 够）。
- **新增** `find_default_transformers_dir()`（扫 `models/vlm/` 下含 config.json 的子目录）；`create_default_backend()` 优先级：
  transformers 目录 → GGUF(llama.cpp) → 报错。
- **用户需执行**：模型 clone 到 `models/vlm/Qwen3-VL-4B-Instruct/`（含 config.json）；装 `transformers>=4.53 qwen-vl-utils accelerate`；重启。
- **✅ 已就位**：模型 8.9GB + transformers 5.14.1 + qwen-vl-utils 0.0.14 + accelerate 1.14.0。实测成功，里程碑 A 定稿。

### 22. 待办⑤ 里程碑 B：二级一致性检测（任务 #51-55）

- **设计（用户）**：把本镜生成帧 vs 全局角色/场景资产图，检验「生成的是不是设定里的角色/场景」；结构化判断（match booleans + reason），
  非 0~1 分数；**自动标 + 手动可加减**。
- **新增** `check_consistency(backend, gen_frame, character_asset, character_name, location_asset, location_name)` +
  `check_consistency_from_tensors` + `_to_pil`（PIL/tensor/numpy 统一转 PIL RGB）。输出 `{character_match, scene_match, clothing_match, reason}`。
  **取首帧对比**（后续帧运动/表情是噪声）。
- **executor**：`_qv_level>=2` 才跑；同一 backend session 内先后做状态提取 + 一致性检测（不重复加载模型）。报告行 ` 一致性: 角色=…; 场景=…; 服装=… (reason)`。
- **三态覆盖**：每段「关键镜头」下拉 `seg.consistencyCheck`——`undefined/"auto"`=有资产注入即自动检测、`true`=强制检测（无资产降级"未查"）、`false`=跳过。
- **match=false 只报告提醒，不重跑**（重跑留给三级）。
- **实测定稿**：段1 r2v（有资产注入）报告 `一致性: 角色=True; 场景=True; 服装=True (人物面部发型与服装细节与参考图一致…)`；
  fl2v 硬锁段（无资产注入）不跑——符合"自动=有资产注入即检测"语义。

### 23. 待办⑤ 里程碑 C：三级失败重跑（任务 #56-60）

- **设计（用户）**：**不让 Qwen 一个模型决定重跑，否则误判→无限生成**。Qwen 语义判断 + 规则客观确认，双确认才重跑，限 1 次。
- **新增** `director/state_rule_check.py`：纯像素规则，无 VLM 依赖。阈值——平均亮度 <0.03 过暗 / >0.97 过曝、标准差 <0.012 纯色、
  Laplacian 方差 <0.00035 模糊、分辨率 <320×240；比例——过暗/过曝/纯色 ≥15% 帧、模糊 ≥25% 帧；最多等距采样 32 帧。
  `analyze_frames` → `rule_check_frames(frames, expected_size=None) -> {abnormal, issues, detail}`；
  `confirm_rerun(qwen_issues, rule_result) -> (rerun, reason)`——**仅当 Qwen issues 非空 AND 规则 abnormal 才重跑**。
- **qwen_vl_feedback.py**：`detect_issue(backend, frames, prompt, max_tokens=512, sample_frames=3)` 采样首/中/尾帧；
  `SEVERE_ISSUE_KEYWORDS`（崩坏/伪影/撕裂/多手/多指/肢体/畸形/换脸/面部/五官/错位/变形/扭曲/融合/残缺/异常）；
  强 JSON + 失败重试一次。
- **executor**：`qwen_rerun_note=""` + 三级块（`_qv_level>=3`）——Qwen 报 issues 且 severity=high → 规则检测 → `confirm_rerun` 成立才重跑：
  `seed=(seed+1)&0xFFFFFFFF` 重新采样，替换 decoded/chunk/audio 输出 + 写段缓存；异常保留原输出。
  报告行 ` 重跑: 已重跑(原因; seed=…→…)` / `不重跑: …` / `轻微问题: …`。
- **i18n**：级别说明更新为 1/2/3 三级。
- **✅ self-test 发现并修真 bug**：severity 兜底缺陷——`str(obj.get("severity") or "low")` 导致缺失时**直接判 low**，即使 issues 命中严重关键词
  也不进重跑候选，且 `SEVERE_ISSUE_KEYWORDS` 是死代码。修复：缺失/乱写 severity 时按 issues 命中关键词兜底判 high/low（显式 low 仍尊重）。
- **✅ 实测定稿（防误判验证）**：段1 Qwen 报「多手、肢体畸形、面部崩坏、画面崩坏」severity=high → 规则无客观异常 →
  报告 `不重跑: Qwen 报「…」但规则无客观异常，不重跑`——**双确认防误判生效，正是"不让 Qwen 一个模型决定重跑"的实证**。
  **用户决策：保持现状**——不加手/脸目标检测、不降像素阈值。多手类语义错误不重跑，靠提示词/人工排查。
- **How to apply**：用户报"画面崩了但没重跑"→ 看报告该行是「不重跑: Qwen 报…但规则无客观异常」还是压根没重跑行。
  多手/换脸类**清晰但画错**的画面按设计不重跑，不要当 bug 修；要触发重跑必须画面同时有像素异常，提示词写足"剧烈运动模糊/强光过曝"才有机会。

### 24. 全部导出 OOM 内存保护（DefaultCPUAllocator 修复）

- **症状**：`RuntimeError: [enforce fail at alloc_cpu.cpp:117] data. DefaultCPUAllocator: not enough memory: you tried to allocate 48939171840 bytes`（≈45.6GB，Node 5 MiniMaxH3Director）。
- **根因**：「全部导出」把 9 段解码后的 float32 帧（每段 ≈492 帧 ≈20s @24fps）在 **CPU 内存**里 `torch.cat` 合并成整段视频。48.9GB ≈ 4425 帧 × 1280×720×3×4。**每段时长太长是主因**——9 段 × 默认 5s(124f) 只需 ≈12.3GB，不会爆。
- **修复（三层）**：
  1. `executor_core.py` 生成前**预检**：`plan.export_mode=="all"` 时按 `total_frames×宽×高×3×4` 估 float32 大小；float16 仍超 90% 可用 RAM → 直接抛**清晰中文报错**（提示改分段导出/缩短每段/降分辨率/选择运行），不再裸报 allocator OOM。
  2. `executor_core.py` 新增 `_coerce_chunk`：预计输出 > `max(8GB, 30% 可用 RAM)` 时 CPU 帧转 **float16** 累积/合并（内存减半），否则维持 float32 输出不变。4 个调用点（主解码 / 三级重跑 / 段缓存加载 / 未选段 passthrough）全部走它。
  3. `segment_continuity.py` `concat_continuous_chunks` 合并前 `_guard_merge_memory`：按真实 chunk 形状算输出大小，超 70% 可用 RAM 抛可操作中文报错。`director_common.py` `finalize_director_outputs` 去掉强制 `.float()`，让 float16 省下的内存在节点输出处保持住。
- **How to apply**：以后「全部导出」报内存不足，**第一反应是每段时长/总帧数**——H3 建议每段 5–10s（勿超 15s），9 段全 20s 必然 45GB+。改不改代码都建议「分段导出」+ 每段短时长。

### 25. 三级导出架构：Movie → Scene → Shot（任务 #66-71，本版核心）

- **用户架构需求（本版转向点）**：导出层级从 Movie→Shot 两级升级为 **Movie→Scene→Shot 三级**，Scene（场景）成为导演系统一等公民。
  Scene 负责场景/时间/地点/人物状态（自动合并本场景镜头 + 曝光/颜色统一 + 接缝优化）；Shot 负责运镜/动作/构图；Movie 只做场景 mp4 直拼。
- **四个导出档位**（`output.exportMode`，下拉顺序 = 推荐度）：
  - `scene`（**默认/推荐**）：按地点连续段归场景 → 每场景 `concat_continuous_chunks`（曝光/接缝优化）→ PyAV h264+aac 编码 `{prefix}_NNNNN_Scene01.mp4`… → 释放张量 → 节点输出每场景一段。
  - `movie`：场景内处理同上，最后 `merge_segment_mp4s`（ffmpeg concat，`-c copy` 优先重编码兜底）→ `{prefix}_NNNNN_Movie.mp4`，**几乎零合并内存**；再 decode 回 tensor 输出（f16 自适应）。
  - `segments`：旧「分镜导出」——每镜单独输出，不合并。
  - `all`：旧「全片导出（内存合并）」——仅适合短片，保留向后兼容。
- **场景边界算法** `plan.py build_scene_groups`：`location_asset.name` 连续段相同归一组；无 location 信息则全片一组（等价旧全片导出）。
- **内存策略**：场景内合并（最多≈1 场景的 float16 帧）→ 立即编码落盘 → 释放；movie 模式 ffmpeg 直拼不堆张量；解码回 tensor 时按 f16 预估防 OOM。
- **新增** `stream_export.py`：`resolve_scene_run_paths`（场景 mp4 + Movie.mp4 共用 ComfyUI 计数器命名）、`concat_audio_dicts`（多镜头 AUDIO dict 按采样率/声道补零拼接进场景 mp4）、复用 `save_segment_mp4`/`merge_segment_mp4s`/`decode_video_tensor`/`ffmpeg_bin`。
- **executor_core.py**：预检块 scene/movie 分支（场景分组报告 + ffmpeg 可用性 + 落盘路径 + movie 解码 f16 预估）；场景执行主循环（`_encode_scene` 场景内合并→编码→落盘→释放）；movie 模式 ffmpeg 合并 + 解码回 tensor（失败降级占位帧 + 报告注明 Movie.mp4 已落盘）；**旧 all/segments 路径完整保留**。
- **前端**：`minimax_timeline.js` 导出方式下拉 4 档（scene 默认第一）+ 所有默认值 `"all"`→`"scene"`（加载回退同样 `?? "scene"`）；`minimax_i18n.js` ZH/EN 同步（tooltip 描述四级）。
- **director_common.py**：`export_segments = export_mode in ("segments","scene")`（scene 也算"多条 clip"输出）；`split_label = "Scene"/"shot"`；movie 模式 images 输出加报告行；默认 timeline JSON `"exportMode":"scene"`。
- **兼容性**：image-batch（t2i/i2i）仍强制 `"all"`（gen_timeline/前端守卫）；fl2v 面板独立不受影响。
- **How to apply**：想导出"一个地点的两三分钟连续剧情（3-4 个分镜）"→ 默认 scene 档即可，自动合并+接缝优化；想要整片 → movie 档（ffmpeg 直拼，不吃内存）；旧工作流加载后 exportMode 回退 `"scene"`（不再默认 `"all"`）。

### 26. 导演系统一级对象：Scene（场景管理，任务 #72-77，本版核心）

- **背景**：项目结构从 `Project → Segment 001/002…` 升级为 `Project → Scene（素材组：角色/场景/道具/风格参考；镜头：Shot 001/002/003）`。用户 2026-08-07 AskUserQuestion 三项确认：① 顶部导航 tab 入口；② `timeline.scenes` 新块数据模型；③ 完整落地（面板 + 段归属 + 每场景导出 + 全片拼接）。
- **数据模型**：`timeline.scenes: []` 新块，每条 `{id,name,location,time,order,assets{cast,locations,props,styles},defaultCastId,defaultLocationId}`；segment 新增 `sceneId`（归属场景）。三级资产结构：**Global Asset Library → Scene Asset Group → Shot Reference**。
- **后端**（`director/plan.py`）：新增 `SceneGroup`/`SceneGroupPlan` dataclass、`build_scene_groups(segments, scenes)`（`sceneId` 显式优先 + location 兜底）、`_load_global_assets(asset_list, kind)`（cast/location/prop/style 四类）。
- **后端**（`director/gen_timeline.py`）：`_load_scene_groups(scene_list)` 解析 scenes 块；`_scene_asset_pool(scene, kind_key, global_assets)`（场景素材组优先 + 全局合并去重）；segment 循环读 `seg_data.get("sceneId")`，构造 `cast_asset/location_asset/extra_assets/scene_id`。
- **后端**（`director/executor_core.py`）：`asset_items` 四角色（角色资产图/场景资产图/道具资产图/风格参考图）；`pic_roles` 生成 `<Picture N>` 说明；`_encode_scene(gi, group, chunks, scene_audio, scene_label)` 场景内合并→编码→落盘，报告带 `「场景名」` 标签。
- **前端**（新文件 `web/js/minimax_scene_manager.js`）：Scene Manager 面板（`mountSceneManagerPanel` 同 mountDirectorPanel 范式 `{el, syncFromWidgets, attachEditor, selectScene}`）。场景卡片列表（新建/重命名/删除/上移下移）、场景详情（名称/地点/时间/素材组/镜头列表）、每场景导出（`exportScene`：设 `exportMode="scene"` + `runSelectEnabled` + `runSelection=本场景镜头` → `window.app.queuePrompt()`）。
- **前端**（新文件 `web/js/minimax_export_center.js`）：`mountAssetLibraryPanel`（素材库总览：全局资产四类 + 场景素材组）+ `mountExportCenterPanel`（导出中心：每场景导出按钮 + 全片导出 `exportMode="movie"`，复用 `exportScene`）。
- **前端**（`web/js/minimax_image_batch.js`）：全局资产库升级——`getAssetsBlock` 初始化 `props/styles` 数组、`addGlobalAsset`/`removeGlobalAsset`/`renderGlobalAssetsPanel` 支持四类、mountImageBatchPanel 加「道具库」「风格参考库」两行 + add 按钮绑定；每张段卡片新增「所属场景」下拉（`makeSegmentSceneSelect`，写 `seg.sceneId`，r2v 与普通卡都有）。
- **前端**（`web/js/minimax_timeline.js`）：顶部导航 4 tab（时间线/场景管理/素材库/导出中心）；`scenesPane`/`assetsPane`/`exportPane` 各自挂面板；`setActiveNavTab` 切换时 `syncFromWidgets`；payload 白名单补 `sceneId`；batchUi refs 补 `assetsProp/assetsStyle/assetsPropAdd/assetsStyleAdd`。
- **前端**（`web/js/minimax_i18n.js`）：ZH+EN 补 `scene.*`、`assets.library.*`、`export.*`、`panel.batch.propLabel/styleLabel` 等键。
- **导出语义**：每场景导出 → `{prefix}_{counter}_SceneNN.mp4`（场景内接缝/曝光统一）；全片导出 → 各场景 mp4 用 ffmpeg 直拼 `Movie.mp4`（几乎零内存）。未归属任何场景的镜头自动按 location 兜底分组。
- **坑 / How to apply**：① 段归属场景字段是 `seg.sceneId`（不是 `scene_id`），gen_timeline 两者都读但前端只写 camelCase；② 新增 per-segment 字段必须同步进 payload 白名单（同坑 #16）；③ 删场景会清空该场景下段的 `sceneId`（不删段）；④ 全片导出依赖 ffmpeg（PATH 或 imageio-ffmpeg）；⑤ 场景排序用 `order` 字段，不是数组下标。

### 27. PyAV 帧率 float → Fraction 修复（scene/movie 导出编码报错）

- **症状**：真实生成跑到导出编码阶段报 `AttributeError: 'float' object has no attribute 'numerator'`，traceback 指向 `stream_export.py save_segment_mp4`：
  `vstream = container.add_stream("h264", rate=round(fps_f, 3))` → `av/utils.py to_avrational` → `input.num = frac.numerator`。
- **根因**：`round(fps_f, 3)` 返回的是 **Python float**（round 带 ndigits 永远返回 float），而 PyAV `add_stream` 的 `rate` 只接受 **int 或 `fractions.Fraction`**（float 会被直接当对象取 `.numerator` 属性 → AttributeError）。H3 默认 24fps 也踩（`round(24.0, 3)` = `24.0` 仍为 float）。
- **修复**：`stream_export.py` 顶部 `from fractions import Fraction`；`save_segment_mp4` 内新增 `_fps_to_avrate(value)`——`Fraction(max(0.01, float(value or 24.0))).limit_denominator(1000000)`，分母为 1 返回 int、否则返回 Fraction；`add_stream` 改传 `_fps_to_avrate(fps_f)`。音频流 `rate=sr` 本来就是 int，不受影响。
- **同步范围**：source（`D:\夸克网盘\...`）与部署（`D:\Comfy-Desktop\...`）两端 `director/stream_export.py` 均已改；用户需**重启 Comfy Desktop** 生效后重新生成。
- **How to apply**：以后 PyAV `add_stream("h264", rate=...)` 一律传 int 或 Fraction，**绝不传 float**；非整帧率（如 23.976）用 `Fraction(fps).limit_denominator(...)` 化简成可编码比率。

### 28. PyAV 音频流 sample_fmt 属性缺失（scene 导出带音频编码报错）

- **症状**：生成跑到 `_encode_scene` → `save_segment_mp4` 音频分支报 `AttributeError: 'av.audio.codeccontext.AudioCodecContext' object has no attribute 'sample_fmt' and no __dict__ for setting new attributes. Did you mean: 'sample_rate'?`，traceback 指向 `stream_export.py` line 188 `astream.sample_fmt = "fltp"`。
- **根因**：该 PyAV 版本的 `AudioCodecContext` **不暴露可写的 `sample_fmt` 属性**（`astream.sample_fmt=...` 走 `Stream.__setattr__` → `setattr(codec_context, "sample_fmt", ...)` → 属性不存在且无 `__dict__`）。此音频编码分支此前未实际跑到（scene 导出无音频段时不进此分支）。
- **修复**：把 `astream.sample_fmt = "fltp"` 包进 `try/except (AttributeError, TypeError): pass` 静默跳过。**安全依据**：FFmpeg AAC 编码器默认 sample_fmt 就是 `fltp`，与下方 `AudioFrame.from_ndarray(..., format="fltp")` 一致；且 PyAV `encode` 阶段会按 codec 参数自动做音频格式/布局转换，不显式设置也能正确编码。`astream.layout`/`astream.channels` 是 PyAV 长期存在的属性，保持设置。
- **同步范围**：source + 部署两端 `director/stream_export.py` 已改；用户需重启 Comfy Desktop。
- **How to apply**：PyAV 流参数（`sample_fmt`/`pix_fmt`/`layout` 等）在不同版本上可能不存在或只读，设置时一律 try/except 兜底；frame 侧显式指定 `format="fltp"` 是最可靠的锚点，让 encode 自动对齐。

### 29. PyAV 音频流 channels 只读（scene 导出带音频编码报错 #3）

- **症状**：继续跑又报 `AttributeError: attribute 'channels' of 'av.audio.codeccontext.AudioCodecContext' objects is not writable`，traceback 指向 `stream_export.py` `astream.channels = 2 if ...`。
- **根因**：该 PyAV 版本的 `AudioCodecContext.channels` 是**只读属性**（由 `layout` 推导：stereo=2 / mono=1），显式赋值报不可写。这是 PyAV 音频编码分支第 3 次版本兼容问题（#27 视频 rate float、#28 sample_fmt 缺失、#29 channels 只读）。
- **修复**：删除 `astream.channels = ...` 显式赋值——设置 `astream.layout = "stereo"/"mono"` 即会推导 channels；`layout` 也包进 `try/except (AttributeError, TypeError): pass` 兜底。frame 侧 `AudioFrame.from_ndarray(..., format="fltp", layout=...)` 已显式指定格式，PyAV `encode` 会按 frame 自动对齐，即使 stream 侧全部设置失败也能正确编码。
- **同步范围**：source + 部署两端 `director/stream_export.py` 已改；用户需重启 Comfy Desktop。
- **How to apply**：PyAV 音频流设置 `sample_fmt`/`channels`/`layout` 都可能在当前版本不可写/不存在——**统一 try/except 兜底，不要显式设 channels**（layout 推导），frame 侧显式 `format`/`layout` 是唯一可靠锚点。连续 3 个编码报错全部是 PyAV 版本兼容问题，改代码时记住这个规律。

---

## 2026-08-08 修改记录（按时间线）

### 30. UI 2.0 第一优先级：Scene+Shot 导航重构（任务 #82-83）

- **背景（Why）**：用户定的 UI 2.0 重构哲学——不再主要加"功能"，而是把已有功能围绕 **Scene → Shot → Asset → State → Generate → Export** 主线重新组织；技术复杂度藏进系统、操作复杂度不暴露给用户。第一优先级 = **场景栏 → 镜头缩略图 → 当前镜头** 导航。用户明确要求"一次只改第一优先级，不要一次改十几个东西"。
- **功能**：时间线页顶部新增两条导航条（纯增量视图，不改任何数据）：
  - **场景胶囊栏** `bd-scene-nav`：`全部 N` + 每个场景胶囊（`名称 · 镜头数`，hover 显示 location/time，点击切换激活场景）+ `未归类 N`（有孤儿镜头且当前非"全部"时出现）+ `+ 场景`（调 `addScene` 新建并激活）。
  - **镜头胶囊条** `bd-shot-nav`：当前激活场景的镜头胶囊（`NN 时长`，点击选中镜头，`selectedIndex` 与 canvas/卡片天然同步）；已生成镜头（磁盘缓存命中）圆点变绿（`.done .bd-shot-dot`，数据来自新 HTTP 路由 `segment_cache_status`）。
- **涉及**：
  - `web/js/minimax_timeline.js`：CSS（.bd-scene-nav/chip/.bd-shot-nav/chip/dot，行 564-581）；`buildDOM` 在 mainBody 后、stage 前插两个容器；构造器加 `this._activeSceneId` + `_initActiveSceneId()`；新方法 `activeSceneId/_initActiveSceneId/setActiveScene/_shotsInScene/renderSceneNavBar/_sceneChip/renderShotNavBar/refreshSegmentCacheStatus`；`updateSelectionUI` 尾部 + `commit()` 里刷镜头条/场景栏；`getScenesBlock` 已在 scene_manager 导出。
  - `web/js/minimax_scene_manager.js`：`getScenesBlock` 由内部函数改为 `export function`。
  - `web/js/minimax_i18n.js`：新增 ZH+EN 各 5 键（`sceneNav.all/orphan/addScene/noShots/noPrompt`）。
  - `director/http_routes.py`：新增 `GET /minimax/director/segment_cache_status`，扫 `output/minimax_seg_cache/{node_id}/seg_NNNN.pt` 返回 `{"cached":[索引]}`。
  - `director/gen_timeline.py`：仅修一处乱码字符（`\xee\x95\xb6`），使源目录与部署字节一致。
- **设计决策（Why）**：
  - 导航条放时间线页**顶部**、stage 之前（用户确认）；镜头条是**胶囊按钮**（用户确认）。
  - **纯增量**：不写 `timeline.scenes`，只读；`selectedIndex` 为唯一选中源，避免双份状态。
  - 激活场景初始化逻辑：优先当前选中镜头所属场景 → 否则第一个场景 → 否则 `""`（全部）。
  - `refreshSegmentCacheStatus` 用 `Set(data.cached.map(Number))`，非致命（网络/解析失败静默忽略）。
- **已知坑（How to apply）**：
  - **场景栏/镜头条刷新时机**：场景增删、段归属变化走 `commit()`（已挂刷新）；镜头选中走 `updateSelectionUI` 尾部（只刷镜头条，场景栏由 commit 刷）。新增"改场景归属"入口时记得在 commit 链路里刷，别只刷镜头条。
  - **`seg.sceneId` 是 camelCase**（同坑 #14），读取 `_shotsInScene` 用的就是它。
  - 孤儿镜头判定：`_shotsInScene("").filter(i => !segments[i].sceneId)`（sceneId 为空串/undefined 都算）。
  - 新 HTTP 路由注册后**必须重启 Comfy Desktop**（PromptServer 路由在启动时注册）。
  - 场景胶囊 label 兜底：`sc.name || \`Scene ${order+1}\``（`order` 可能缺省 → `?? 0`）。
- **验证标准**：node --check + py_compile 双目录通过；重启后时间线页顶部出现场景胶囊栏+镜头胶囊条；点场景胶囊 → 镜头条切换为该场景镜头；点镜头胶囊 → 卡片/canvas 选中同步；已生成镜头圆点绿；`+ 场景` 可新建并自动激活。**用户需重启 Comfy Desktop 验证**。

### 31. UI 2.0 第二优先级：Prompt/续接/素材模块化——r2v 卡片「镜头设置」折叠模块（任务 #85-86）

- **背景（Why）**：第一优先级导航重构后，第二优先级 = **把 Prompt/续接/素材变成清晰的模块**。探索发现 r2v 段卡片根级平铺 5 个控制行（资产选择/状态变更/智能尾帧/关键镜头/衔接模式），彼此无容器、无标题、无折叠，视觉雷同，用户难区分（尤其「状态变更/智能尾帧/关键镜头/衔接模式」四个同款蓝行）。用户哲学——技术复杂度藏进系统、操作复杂度不暴露给用户。
- **功能**：r2v 段卡片重组——
  - **资产选择（角色/场景/所属场景）保留在卡片头正下方始终可见**（高频操作）。
  - 其余 4 个低频控制行收进**「镜头设置」折叠模块**（`.bd-batch-cfg`）：默认折叠，点击折叠头展开/收起；折叠头右侧摘要实时显示当前「衔接模式」（`衔接: 自动/Ref2VA/FL2VA`），不展开也能看到关键路由状态。
  - 展开后按语义分两组（`.bd-batch-cfg-group` + 组标题）：**「续接」组** = 衔接模式 + 状态变更；**「生成」组** = 智能尾帧 + 关键镜头。
  - 折叠状态存 `editor._batchCfgOpen`（全局共享，所有卡片同步），重渲染不丢。
- **涉及**：
  - `web/js/minimax_image_batch.js`：新增 `.bd-batch-cfg*` CSS（约 15 条规则，借用 director_panel 折叠模式但独立命名）；`renderImageBatchGroups` 在 asset-sel 后创建 cfgWrap/cfgHead/cfgBody + 续接/生成两个 group；4 个控制行的 `card.appendChild` 改为挂到对应 group；`cfgGroupCont/cfgGroupGen/cfgSummary` 用 `let` 提升到 forEach 回调作用域（**避免块级作用域 ReferenceError**）。
  - `web/js/minimax_i18n.js`：新增 ZH+EN 各 5 键（`batch.cfg/cfgToggleTitle/cfgContinuity/cfgGenerate/cfgSummary`）。
- **设计决策（Why）**：
  - 折叠头用 `<button>`，onclick `stopPropagation` + 卡片 onclick 的 `closest("button,…")` 兜底 → 点折叠头不会误选卡片。
  - `editor._batchCfgOpen` 是纯 UI 状态，不落 timeline（折叠偏好没必要持久化）。
  - 非 r2v 卡片（plain/refs/source）无此折叠模块，行为完全不变。
- **已知坑（How to apply）**：
  - **块级作用域**：在 `if (isR2v)` 内用 `const` 声明的容器，后续独立 `if (isR2v)` 块引用会 ReferenceError——必须在 forEach 回调顶部 `let` 声明再赋值。
  - 折叠头摘要只在 continuity 块（r2v）填；非 r2v 卡片 `cfgSummary` 为 null，代码用 `if (cfgSummary)` 保护。
  - 切 FL2VA 时 `renderImageBatchGroups` 重渲染，`_batchCfgOpen` 保留折叠状态；素材区（参考图→首帧/结束帧）仍直接变化，摘要同步显示「衔接: FL2VA」。
  - fl2v 面板尚未模块化（「本镜提示词」与镜头卡片分离问题）——留作第二优先级后续子任务，r2v 验证通过后再做。
- **验证标准**：node --check 双目录通过；重启后 r2v 卡片资产选择下方出现「▸ 镜头设置 衔接: 自动」折叠条；点开显示「续接」组（衔接模式+状态变更）和「生成」组（智能尾帧+关键镜头）；折叠状态跨卡片同步；非 r2v 卡片不受影响。**用户需重启 Comfy Desktop 验证**。

### 32. UI 2.0 第二优先级收尾：fl2v 面板模块化——镜头卡片提示词摘要 + 详情区标题（任务 #88）

- **背景（Why）**：r2v 卡片模块化完成后，fl2v 面板仍存在「本镜提示词」与镜头卡片分离问题——fl2v 用**共享 prompt textarea**（`ui.prompt` 映射到 `editor.selectedIndex`），而卡片列表不显示当前镜写了什么，切镜后容易丢失"哪一镜写了什么"的心智模型。
- **设计决策（Why）**：**不在 220px 宽卡片里放 textarea**（太窄没法写），而是保留共享 textarea + 加两层桥接——
  1. **卡片级提示词摘要行**（`.bd-fl2v-shot-prompt`）：✎ 图标 + 文本，42 字符截断加 `…`；无提示词时灰显「（未写提示词）」（EN `(no prompt)`）；点摘要行 = 切到该镜 + 聚焦共享 textarea（`scrollIntoView({block:"nearest"})`）。
  2. **详情区标题**（`.bd-fl2v-detail-title`）：「镜 N · 本镜提示词」，每镜切选中时更新，明确 textarea 当前编辑的是第几镜。
- **涉及**：
  - `web/js/minimax_fl2v.js`：`FL2V_STYLES` 新增 7 条 CSS；`mountFl2vPanel` 详情区加 `<b data-r="fl2v-detail-shot">` + 返回对象加 `detailShot` 引用；`updateFl2vDetailUI` 更新标题；`renderFl2vShotCards` 卡片模板加摘要行 + 点击聚焦逻辑。
  - `web/js/minimax_i18n.js`：新增 `fl2v.promptEmpty`（ZH+EN 双语，与 batch 折叠模块一致）、`panel.fl2v.shotPrompt/shotN`（ZH+EN）。
- **已知坑（How to apply）**：摘要行 click 需 `stopPropagation`，否则会先触发卡片选中逻辑（倒也无害，但要保持 focus 行为清晰）；`promptEmpty` 键 ZH/EN 都要给，否则 EN 环境显示 `(no prompt)` 时引用缺失键返回 key 名。
- **验证标准**：node --check 双目录通过；重启后 fl2v 卡片出现提示词摘要行，空镜显示「（未写提示词）」，写后显示前 42 字；点摘要行跳到对应镜并聚焦 textarea；详情区标题显示「镜 N」。**用户需重启 Comfy Desktop 验证**。

### 33. UI 2.0 第三优先级：顶部固定生成栏 + 场景级生成（任务 #89-90）

- **背景（Why）**：第一优先级场景导航、第二优先级模块化之后，第三优先级 = **固定生成栏 + 场景级生成/导出**。探索发现：执行完全靠 ComfyUI 底部 Queue Prompt 按钮，节点内无生成入口；运行进度在节点底部 run-status，长内容时看不见；场景级导出已有（Scene Manager/导出中心），但场景级生成无入口。用户确认方案：**顶部固定栏**（非真 sticky，避免内部滚动冲突）+ **范围三选一**。
- **功能**：节点顶部（导航 tab 与工具栏之间）新增 `.bd-genbar` 固定生成栏——
  - **范围三选一**（`.bd-genbar-scope`）：「全部」/「当前场景」/「选中」。「当前场景」= 当前场景胶囊下的所有镜头；「选中」= 复用「选择运行」勾选的镜头，自动打开选择运行模式。「当前场景」按钮在无场景时禁用；不支持选择运行的任务（单镜头/部分 batch）范围自动降级为「全部」。
  - **「生成」主按钮**（`.bd-genbar-run`）：按当前范围设置 `runSelectEnabled/runSelection` → `commit` 同步 → `app.queuePrompt(1)`（经过 setup 里 patch 的 flushDirectors，payload 最新）。
  - **运行状态移入**：原底部 run-status（标题/详情/选择摘要/双进度条）整体迁到生成栏右侧（flex:1），生成控制 + 进度统一固定节点顶部。
  - **范围摘要**（`.bd-genbar-summary`）：实时显示「将运行全部 N 镜 / 将运行当前场景（N 镜）/ 将运行选中的 N 镜」，空时红色警示。
- **涉及**：
  - `web/js/minimax_timeline.js`：CSS（`.bd-genbar*` 约 20 条，`.bd-run-status` 改 flex:1 无边框）；`buildDOM` navBar 后插 genbarWrap（run-status 从 root 底部移除）；constructor 加 `_genScope`；新增 `setGenScope/updateGenScopeUI/_sceneRunIndices/runDirectorGeneration`；`bindEvents` 加 4 个按钮；刷新链路——`commit()`（场景导航刷新块后）、`setActiveScene()`、`applyLocale()`（语言切换重取摘要）；`toggleRunSelectMode` 关闭选择运行时 `_genScope` 回退 all；`_clearLiveRunSelection` 模式切换时 scope 重置 all。
  - `web/js/minimax_i18n.js`：新增 `genbar.*` 10 键 + `tooltip.genScope*` 3 键（ZH+EN 双语）。
- **设计决策（Why）**：
  - `_genScope` 是纯 UI 状态（不落 timeline），重启默认「全部」；「场景/选中」的范围在**点生成时**才写入 `runSelection`，切换范围只更新摘要不污染用户勾选。
  - 场景级生成复用现有 run-selection 机制（`runSelectEnabled + runSelection` + payload 白名单），**后端零改动**；fl2v 模式自动过滤只保留起始帧段。
  - 不引入节点内部滚动（真 sticky 需 max-height+overflow，会与 ComfyUI 画布滚轮冲突），生成栏固定放节点顶部 = 最易达位置。
  - 场景级导出维持 Scene Manager/导出中心现状（已有每场景导出），不在生成栏放重复按钮。
- **已知坑（How to apply）**：
  - `supportsRunSelect()` 为 false 的任务：`setGenScope` 强制回退 all，`updateGenScopeUI` 摘要也降级 all，避免 UI 说「当前场景」实际跑全部。
  - fl2v 场景范围必须 `_sceneRunIndices()` 过滤 startFrame，否则非起始帧段被选入 runSelection 会被 `normalizeRunSelection` 清掉导致空集。
  - 生成按钮依赖 `app.queuePrompt` 的 patch（flushDirectors），改 setup() 里 patch 逻辑时别破坏透传。
- **验证标准**：node --check 双目录通过；重启后节点顶部出现「范围 [全部|当前场景|选中] [生成] 运行状态…」栏，底部旧 run-status 消失；切场景胶囊摘要联动；点「当前场景」+「生成」只跑该场景镜头（r2v 卡片勾选/进度条验证）；「选中」自动开选择运行模式。**用户需重启 Comfy Desktop 验证**。

### 34. UI 2.0 第四优先级：高级设置折叠面板（任务 #91-92）

- **背景（Why）**：前三优先级（场景导航 / 模块化 / 固定生成栏）后，节点底部仍散落 4 组原生控件（采样组 cfg/seed、高级组 steps/sampler/scheduler/shift_video/shift_audio、性能组 clear_vram/export_source_images，外加各自 BDGROUP 组头），batch 里还有独立「AI 导演设置」面板（Qwen3-VL 开关/级别）。用户确认方案：**DOM 折叠面板**，位置在**时间线底部，所有模式可见**，统一收敛为「高级设置」四组。
- **功能**：时间线底部新增 `.bd-adv-panel` 折叠面板（prompt_batch / fl2v / video 三模式通用）——
  - **Qwen3-VL 视觉反馈**（原 batch「AI 导演设置」面板迁入，独立面板移除）：启用复选框 + 反馈级别下拉（1 状态提取 / 2 +一致性检测 / 3 +失败重跑），状态仍存 `timeline.output.qwenVlEnabled/qwenVlLevel`，与旧面板同一存储，旧工作流数据无缝沿用。
  - **采样设置**：CFG + 种子（含 🎲 随机种子按钮，32-bit 避免超 JS 安全整数 2^53）+ 生成前后定制（control_after_generate）。
  - **高级采样**：采样步数 + 采样器 + 调度器 + 视频偏移 + 音频偏移。
  - **性能**：段间清理显存 + 输出原片对比。
  - 默认折叠（仅头栏 ≈46px），点头部展开/收起；展开高度按四组固定行高累加，Qwen 级别行随启用状态增减。
- **涉及**：
  - `web/js/minimax_advanced_panel.js`（**新文件**）：导出 `ADVANCED_PANEL_STYLES` / `getAdvancedPanelUiHeight(editor)` / `mountAdvancedPanel(parentEl, editor)`；`mountAdvancedPanel` 返回 `{el, syncFromWidgets, attachEditor}`；`commitWidget` 写回原生 `widget.value`（后端 `execute()` 读同一对象），`commitOutput` 写 `timeline.output` + `scheduleTimelineSync`；Qwen 状态经 `readDirectorQwenFields(editor)`（import 自 minimax_director_panel.js）。
  - `web/js/minimax_timeline.js`：import 新模块；STYLES 注入 `${ADVANCED_PANEL_STYLES}`；`HIDDEN_WIDGETS` 扩到 15 项（cfg/seed/control_after_generate/control after generate/steps/sampler/scheduler/shift_video/shift_audio/clear_vram_between_segments/export_source_images + 三组 BDGROUP 组头 bd_grp_sample/advanced/perf）；新增 `applyDirectorWidgetVisibility(node)`（精确名 + control 正则兜底）；`getDirectorUiHeight` 所有分支累加 `getAdvancedPanelUiHeight`；`buildDOM` 用 `mountAdvancedPanel(this.mainBody, this)` 替换原 batch director 面板挂载；`commit()`/`applyLocale()` 尾部调 `advancedPanel.syncFromWidgets()`。
  - `web/js/minimax_image_batch.js`：移除 `mountDirectorPanel` import、`.bd-director-*` CSS、`bd-director-mount` 容器及相关 return 项（AI 导演设置面板整体迁出）。
  - `web/js/minimax_i18n.js`：新增 ZH+EN 键（`widget.cfg/steps/sampler/scheduler/shiftVideo/shiftAudio/controlFixed/controlIncrement/controlDecrement/controlRandomize/seedDice`、`panel.adv.title/toggleTitle`、`tooltip.advPanel`）。
  - `web/js/minimax_director_panel.js`：**不删**——`minimax_advanced_panel.js` 仍 import 它的 `readDirectorQwenFields`；`mountDirectorPanel` 已无调用点。
- **设计决策（Why）**：
  - **原生 widget 必须保留真实对象**：后端 `execute()` 读 widget 值，面板只做「镜像读写」——读 `widget.value` 填 DOM，改 DOM 写回同一 widget 对象；原生控件全部隐藏（HIDDEN_WIDGETS + hideWidget）。
  - 折叠形态选 **DOM 面板**（非 BDGROUP 原生折叠）：BDGROUP 只藏一个值、无子控件渲染能力，且三个组头要分别隐藏；DOM 面板可自由排版 + 统一折叠。
  - 位置选**时间线底部所有模式可见**（用户确认）：prompt_batch / fl2v / video 共享一份高级设置，避免每种模式一套。
  - Qwen 控制从 batch 独立面板迁入高级设置 = 收敛入口，操作复杂度不暴露（用户哲学）；存储字段不变。
  - `control_after_generate` 伴随控件名有变体（ComfyUI 可能命名 `control after generate`）：HIDDEN_WIDGETS 两者都列 + `applyDirectorWidgetVisibility` 用 `/control[_\s]?after[_\s]?generate/` 正则兜底隐藏。
  - 种子随机用 `Math.floor(Math.random()*0xFFFFFFFF)`（32-bit），避免超 2^53 安全整数丢精度。
- **已知坑（How to apply）**：
  - 高度估算：折叠 = `ADV_HEAD_H+8`（46px）；展开 = 头栏 + body padding + 四组 `advancedGroupH(rowHeights)` 累加。新增行/组时**必须**同步改 `getAdvancedPanelUiHeight`，否则节点高度与实际 DOM 不符会裁切。
  - `minimax_director_panel.js` 不能删：`mountDirectorPanel` 已无调用点，但 `readDirectorQwenFields` 仍被 advanced_panel import；删文件会破坏 import。
  - 挂载点在 `buildDOM` 的 `mainBody` 底部、bindFl2vEvents 之后；`commit()` 与 `applyLocale()` 里调 `syncFromWidgets()`；locale 切换后 combo 选项 label 用 `controlLabels()` 现取现翻译（**不要 const 缓存**，否则语言切换后 label 陈旧）。
  - 折叠状态存 `editor._advPanelCollapsed`（纯 UI 状态，不落 timeline，重启默认折叠）。
- **验证标准**：node --check 双目录通过；重启后节点底部出现「高级设置 ▸」折叠栏（prompt_batch / fl2v / video 都可见）；点头部展开四组（Qwen/采样/高级采样/性能）；原生 cfg/seed/steps/sampler/…/BDGROUP 组头全部消失；展开时改 cfg/seed/steps 等 → 再折叠展开回读值一致；Qwen 开关 → 级别行显示/隐藏 + 高度联动；🎲 生成 0~2^32-1 随机种子；语言切换后下拉 label 刷新。**用户需重启 Comfy Desktop 验证**。


---

### 35. UI 2.0 第五优先级：镜头状态灯（任务 #93-94）

- **背景（Why）**：镜头胶囊条此前只区分「已生成 / 未生成」（磁盘段缓存存在性），生成中 / 待检查 / 失败无视觉反馈；用户重跑某段失败或 Qwen 报问题时无法从 UI 一眼看出。目标：每镜一状态灯，五态——**未生成 / 生成中 / 成功 / 待检查 / 失败**。
- **状态来源（两层）**：
  - **成功** = 磁盘段缓存 `output/minimax_seg_cache/{node_id}/seg_NNNN.pt` 存在（跨重启保留，扫目录推导）。
  - **生成中 / 待检查 / 失败** = 进程内存瞬态（`director/segment_status.py` 模块级 dict）。ComfyUI 单进程：executor_core（worker）写入、http_routes（PromptServer）读取共享同一字典。刻意不落盘——重启后瞬态清空，镜头回退「未生成」（无缓存）或「成功」（有缓存），无陈旧状态。
- **待检查判定**：复用 Qwen 反馈字符串——`qwen_rerun_note` 前缀命中「轻微问题 / 不重跑 / 重跑失败」或 `qwen_consistency_note` 含 `False` → 标 `review`；否则清空该段瞬态。
- **失败判定**：`_run_one_segment` 抛异常 → `try/except` 捕获标 `failed` 后 re-raise（原报错链路不变）。
- **涉及**：
  - `director/segment_status.py`（**新文件**）：`set_segment_runtime_state(node_id, seg_index, state)` / `clear_segment_runtime_state(node_id, seg_index=None)` / `get_segment_runtime_states(node_id)`。
  - `director/executor_core.py`：import + 4 处插桩——`_run_one_segment` 开头标 `running`；段报告前做 review 判定（命中则 `review`，否则清瞬态）；两处段调用点（scene/movie、all/segments）包 `try/except` 标 `failed` 后 re-raise。
  - `director/http_routes.py`：新路由 `GET /minimax/director/segment_status?node_id=` → `{"cached": [idx], "states": {"seg": state}}`；`node_id` 白名单校验。旧 `/segment_cache_status` 路由保留（向后兼容，前端已不再调用）。
  - `web/js/minimax_timeline.js`：`_segCacheStatus` 由 `Set` 改 `Map`；`refreshSegmentCacheStatus` 改打新路由并合并 cached→success + states；`renderShotNavBar` 按状态加 CSS 类 + `dot.title` 用 `t(\`shotStatus.${st}\`)`；`setRunProgress` 活动阶段把当前段标 `running` 实时刷新、`finish` 阶段拉最终状态；`setRunError` 拉最终状态；初始 load 后 `refreshSegmentCacheStatus`。
  - `web/js/minimax_i18n.js`：ZH+EN `shotStatus.running/success/review/failed`。
- **CSS**：`.bd-shot-dot.st-running`（蓝 #4da3ff + 呼吸动画）、`.st-review`（橙 #ffa94d）、`.st-failed`（红 #ff6b6b）；成功沿用 `.bd-shot-chip.done` 绿点。
- **已知坑（How to apply）**：
  - `node_id` 必须白名单校验（`[A-Za-z0-9_\-]+`），否则任意字符串可触发扫目录。
  - 瞬态状态不落盘：重启后 review/failed 消失属**预期**（无陈旧状态）；「待检查」= 上一次运行需人工确认，用户重跑该段即覆盖。
  - `running` 是前端按 WebSocket 进度事件**乐观标记**：`timeline_segment_index` 到段即设 `running`；若后端实际失败，`setRunError`/`finish` 的刷新会用真实状态覆盖。
  - `Set→Map` 改动后所有 `.has(i)` 判断必须改 `.get(i)`（本节点内已全部替换）。
- **验证标准**：node --check / py_compile 双目录通过；重启后：有缓存的镜头胶囊显示绿点（done），其余无点；生成时当前段胶囊蓝点呼吸；生成完拉最终状态（成功绿 / Qwen 命中橙 / 异常红）。**用户需重启 Comfy Desktop 验证**。

### 36. UI 2.1 P1：场景优先一体化控制台（任务 #95-100）

- **背景（Why）**：用户评审 UI 2.0（「导演台雏形，方向对」）后给出 P1 最高优先级——Scene/Shot 层级视觉强化：顶部从「4 tab 导航栏」升级为**场景优先控制台**，Scene 成为核心入口单位；「素材组 N」卡片改 `🎬 Shot N`；新增**继续拍摄**按钮（当前场景末尾自动建镜 + 继承场景资产/上一镜状态/自动续接），是本系统区别于普通 ComfyUI 工作流的标志性操作。用户明确「不大改后端，先做 UI/交互层重构」，故全部改动为纯前端。
- **场景优先控制台头部**（替换原 4 tab 导航栏）：
  - 行1：场景身份 `🎬 Scene 01 · 水晶森林 · 黄昏`（场景名 + 地点 + 时间）+ 元信息 `4 镜头 · 43 秒 · 已完成 3/4`；右侧动作按钮 `[继续拍摄][生成场景][导出场景]` + 分隔 + 次要入口 `[时间线][场景管理][素材库][导出中心]`（ghost 小按钮）。
  - 行2：场景胶囊栏（原 sceneNavWrap 从 mainBody 移入控制台）。
  - `renderConsoleHead()` 刷新场景名/元信息/按钮可用性，挂进 renderSceneNavBar / renderShotNavBar / refreshSegmentCacheStatus；「生成场景」「导出场景」要求当前激活具体场景（非「全部镜头」视图），「继续拍摄」要求 r2v 批且有场景。
- **Shot 卡片重塑**（renderImageBatchGroups）：
  - 标题 `🎬 Shot 01`（`shot.title` 键，`String(index+1).padStart(2,"0")`），右侧加**状态灯**（复用 `.bd-shot-dot` + `st-success/running/review/failed`，数据来自 `_segCacheStatus`）。
  - meta 区加**场景/角色标签**（`🎬 场景名` / `👤 角色名`，一眼看出素材层级归属）。
  - meta 区加**生成本镜**按钮：`runSelection=[index]` + commit + queuePrompt（等价 genbar「选中」范围跑单镜）。
- **继续拍摄 continueShooting()**（纯前端）：
  - 取当前激活场景（无则第一个场景）；前序镜头 = 场景内最后一镜（无场景归属取全片最后一镜）；`newBatchSegment` 建新镜，继承 `sceneId/castId/locationId/stateChange/continuityMode="auto"/smartTail=true`，`durationSec=defaultDurationSec(taskKey)`；`splice` 插入场景镜头末尾 → `normalizeImageBatchSegments` → 选中新镜 → render + commit + 高度刷新 → 滚动并聚焦新镜头 prompt 输入。
  - 尾帧锚点不复制 refs：r2v 自动续接引擎已用上一镜尾帧作图片锚点 + 注入场景/角色资产，避免重复参考。
- **高度**：`getDirectorUiHeight` 各分支加 `CONSOLE_H=66`（场景行 + 场景胶囊行）。
- **涉及**：
  - `web/js/minimax_timeline.js`：buildDOM 导航栏替换为 `.bd-console`；`sceneNavWrap` 挂到 `[data-r="console-scenes"]`；`setActiveNavTab` 选择器 `.bd-nav-tab` → `.bd-console-nav`；新增 `renderConsoleHead()` / `continueShooting()`；bind `console-shoot/gen-scene/export-scene`；import `exportScene`（scene_manager）、`defaultDurationSec`（gen_timeline）；STYLES 加 `.bd-console*` 系列。
  - `web/js/minimax_image_batch.js`：卡片标题/状态灯/场景角色标签/生成本镜按钮；CSS 加 `.bd-batch-tags/.bd-batch-tag/.bd-batch-shot-run/.bd-batch-head .bd-shot-dot.st-*`。
  - `web/js/minimax_i18n.js`：ZH+EN `console.continueShooting/continueShootingTitle/genScene/genSceneTitle/exportScene/exportSceneTitle/timeline/scenes/assets/export/allShots/meta`、`shot.title/tagScene/tagCast/generate`。
- **已知坑（How to apply）**：
  - `setActiveNavTab` 仍用 `this.navBar`（现指向 consoleHead）；只改选择器类名，pane 切换逻辑零改动。
  - 旧 `nav.*` i18n 键保留不删（`applyI18nDom` 对缺失键无害），新按钮用 `console.*`。
  - 继续拍摄**只对 r2v 批**开放（`isR2vBatch` 判定），fl2v/video 模式按钮禁用。
  - 「生成场景」「导出场景」按钮在「全部镜头」视图禁用（避免无场景时误跑全片）；先点场景胶囊再操作。
- **验证标准**：node --check 双目录通过；重启后顶部出现场景优先控制台（场景名+元信息+3 动作按钮+4 次要入口+场景胶囊栏）；点场景胶囊→头部场景名/元信息/按钮可用性联动；卡片标题 `🎬 Shot N` + 状态灯 + 场景/角色标签 + 生成本镜按钮；点「继续拍摄」→ 场景末尾新增一镜（继承字段正确）并聚焦 prompt；「生成本镜」只跑该镜。**用户需重启 Comfy Desktop 验证**。

### 37. UI 2.1 P3：生成操作下拉 + 场景级操作（任务 #101-104）

- **背景（Why）**：P1 场景优先控制台落地后，生成栏还是「全部/当前场景/选中」三个平铺按钮 + 场景切换靠点胶囊；用户要求把生成操作收敛成一个下拉（生成当前镜头/选中/当前场景/所有未完成/重新生成失败镜头），并把「全部5 / +场景」位置升级为场景控制（`[Scene 01 ▼] [+ 新建场景]` + 过滤 全部/未生成/失败/选中）。纯前端，后端零改动。
- **生成操作下拉**（`.bd-genbar-dd`，替换原 3 个平铺 scope 按钮）：
  - 下拉按钮显示当前范围：`全部镜头 ▾`；菜单 6 项 `全部镜头 / 生成当前镜头 / 生成选中镜头 / 生成当前场景 / 生成所有未完成 / 重新生成失败镜头`，当前项高亮（`.active`），不可用项禁用（`.disabled`，如无失败镜头时「重新生成失败镜头」置灰）。
  - `_genScope` 状态机扩充为 6 值：`all/current/select/scene/pending/failed`；`supportsRunSelect()` false 时非 all 全部降级为 all（延续 #33 语义）。
  - 新增范围计数/禁用辅助：`_genScopeCount(scope)` / `_genActDisabled(scope)` / `_pendingRunIndices()`（状态灯非 success 的可运行段）/ `_failedRunIndices()`（状态灯 failed）/ `_currentRunIndices()`（**fl2v 下把选中段映射到其包含的起始帧段**，否则非起始段不可独立运行）/ `_runnableIndices()`（fl2v 只取起始帧段）/ `_sceneRunIndices()`（当前场景内可运行段，fl2v 过滤 startFrame——延续 #19/#33 坑）。
  - `toggleGenMenu()` 外部点击关闭：close 处理器对落在 `.bd-genbar-dd` 内的点击直接返回（否则 capture 阶段提前关菜单，菜单项 handler 没机会跑）。
  - `runDirectorGeneration()` 按 6 范围写 `runSelection`：all→关 runSelect；current→`_currentRunIndices()`；select→`normalizeRunSelection()`；scene→`_sceneRunIndices()`；pending/failed→对应索引；然后 commit+queuePrompt。
- **场景控制行**（renderSceneNavBar 重写）：
  - `[场景下拉 ▼]`（`data-r="scene-sel"`：全部镜头 + 每场景 `名 · N镜`，title 带地点/时间）→ change 走 `setActiveScene`；`[+ 新建场景]`（`data-a="scene-add2"`）→ `addScene` + 设为激活。
  - `[分隔] 过滤 [全部/未生成/失败/选中]`（`data-r="shot-filter-sel"`）→ `setShotFilter(filter)` 只影响镜头导航条显示；未生成=状态灯非 success，失败=状态灯 failed，选中=在 runSelection 内；过滤后为空显示 `filterEmpty`。
- **摘要刷新链路**：`updateGenScopeUI()` 挂进 `setRunProgress` finish 分支 + `setRunError`（状态灯刷新后重算 pending/failed 计数），保证一次生成后「将运行未完成的 N 组」等摘要立即更新；`setGenScope/setActiveScene/commit` 也各自刷新。
- **涉及**：`web/js/minimax_timeline.js`（genbar DOM+事件+状态机+辅助函数+renderSceneNavBar 重写+CSS `.bd-genbar-dd*`/`.bd-scene-sel`/`.bd-filter-*`，移除旧 `.bd-genbar-scope*` 与 `_sceneChip`）、`web/js/minimax_i18n.js`（ZH+EN `genbar.act.*`/`genbar.summary{Pending,Failed,Current}{,Empty}`、`sceneNav.selectSceneTitle/filterLabel/filterTitle/filterAll/filterPending/filterFailed/filterSelected/filterEmpty`）。
- **已知坑（How to apply）**：
  - fl2v「生成当前镜头」在选中非起始段时自动落到其起始帧段（否则单独跑非起始段是空集）。
  - pending/failed 计数依赖 `_segCacheStatus`（磁盘缓存 + segment_status 路由），首次进入时未刷新会显示 0——生成后即刷新。
  - 旧 `genbar.scopeSelect` / `genbar.scopeCurrent` 等键与 `.bd-genbar-scope` DOM 已彻底移除，别再引用。
- **验证标准**：node --check 双目录通过；重启后生成栏出现 6 项下拉 + 范围摘要；r2v 批（n≥2）下六项均可用/禁用正确；点「生成当前场景」只跑该场景段；有失败镜头时「重新生成失败镜头」显示失败数并只重跑失败段；场景下拉切换 + 过滤联动镜头胶囊。**用户需重启 Comfy Desktop 验证**。

### 38. UI 2.1 P4：当前场景素材 / 全局资产层级区分（任务 #105-108）

- **背景（Why）**：用户评审 P4——**三级视觉层级 🌐全局资产 → 🎬当前场景资产 → 🎥当前镜头引用**，一眼看出素材属于哪级。后端 `gen_timeline.py::_scene_asset_pool` 早就实现了「场景素材组优先、全局资产兜底」的注入顺序，但前端镜头卡片的下拉把候选渲染成**扁平的全局-only 列表**：场景素材组里配的角色/场景既显示不出归属层级、也根本选不到（隐性功能缺口）。P4 = 纯前端把 UI 对齐后端解析顺序，让场景级资产真正可选且层级一眼可辨。
- **`resolveSegmentAssetPool(editor, seg, kind)` 辅助函数**（minimax_image_batch.js）：
  - 语义镜像后端 `_scene_asset_pool`：按 `seg.sceneId` 找场景 → 候选池 = 场景素材组（scene-first）→ 追加全局资产（按 `asset.id` 去重，全局里已在场景出现的不重复进池）。
  - 返回 `{ scene, sceneAssets, globalAssets, pool }`，供下拉分组 + 标签来源标记 + 自动匹配三处共用。
  - 段无场景归属时 `scene=null`、`sceneAssets=[]`，`pool` 即全局资产。
- **角色/场景下拉 optgroup 分组**（`makeSegmentAssetSelect` 重写）：
  - 🎬 组 = 场景素材组（`🎬 ${scene.name}`），🌐 组 = 全局资产（含与场景去重后的剩余项）；有场景归属时下拉 `title` 提示「场景素材组优先，其次全局资产库」，无归属提示「使用全局资产库」。
  - onchange 不变：写 `seg.castId/locationId` + `castManual/locationManual` → commit + scheduleRender。
- **段卡片角色标签来源标记**（renderImageBatchGroups）：
  - 命中场景素材组 → `🎬 角色名` + CSS 类 `from-scene`（绿底），title「来源：本镜所属场景「{scene}」的素材组」；命中全局 → `🌐 角色名` + `from-global`（蓝底），title「来源：全局资产库」。
  - CSS：`.bd-batch-tag.from-scene/.from-global` + optgroup 样式（说明文字置灰、选项浅色）。
- **自动匹配场景默认优先**（isR2v 块）：`castDefault = scene.defaultCastId || global.defaultCastId`（locDefault 同理）；`autoMatchSegmentAssets` 用合并后的 `pool` 匹配——场景素材组里的资产现在能自动命中（此前全局-only 池匹配不到场景资产）。
- **素材库总览标题层级化**（minimax_export_center.js + i18n）：「🌐 全局资产库」/「🎬 场景素材组」两区块标题直接带层级前缀，面板资产面板标题同步为「🌐 全局资产（角色 / 场景 / 道具 / 风格）」。
- **涉及**：
  - `web/js/minimax_image_batch.js`：新增 `resolveSegmentAssetPool`；重写 `makeSegmentAssetSelect`（optgroup）；渲染标签来源标记 + 自动匹配默认；CSS。
  - `web/js/minimax_export_center.js`：素材库两区块标题带 🌐/🎬 前缀。
  - `web/js/minimax_i18n.js`：ZH+EN `assets.library.globalTitle/sceneTitle`、`assets.pool.global/scene`、`panel.batch.assetsTitle`、`shot.tagCastScene/tagCastGlobal`、`tooltip.assetPoolScene/assetPoolGlobal`。
- **已知坑（How to apply）**：
  - 段候选池**场景优先**，全局去重只补缺——下拉里 🎬 组在前、🌐 组在后，同一素材不会出现在两组（按 id 去重）；「🎬 组在 🎬 名下找不到某素材」先查该素材是否其实配在全局。
  - 段无 `sceneId`（未归属任何场景）时下拉无 🎬 组、直接 🌐 全局——这是「无场景归属 → 走全局」的既定语义，不是 bug。
  - 新增 i18n 键 `assets.pool.*` / `tooltip.assetPool*` 均 ZH+EN 双语（下拉 title 与组名两种语言下都会显示，缺 EN 键会直接显示 key 名）。
  - 本次是纯前端；若后续后端改 `_scene_asset_pool` 的解析顺序（如加 props/styles 之外的层级），前端 `resolveSegmentAssetPool` 必须同步改，两边才能一致。
- **验证标准**：node --check 双目录通过；重启后：①有场景归属的 r2v 段，角色/场景下拉按「🎬 场景名 / 🌐 全局资产」分组，场景素材组里的角色在 🎬 组可选；②段卡片角色标签命中场景素材组 → 绿底 `🎬 角色名`，命中全局 → 蓝底 `🌐 角色名`，悬停 title 说明来源；③无场景归属段只有 🌐 全局组；④场景素材组配置的默认角色在自动匹配时优先。**用户需重启 Comfy Desktop 验证**。

### 39. UI 2.1 P5：Prompt 和镜头衔接成为视觉中心（任务 #109-112）

- **背景（Why）**：P4 把素材层级整清楚后，用户 P5 要求——**Shot 卡片里 Prompt 和镜头衔接（自动 · Ref2VA / 尾帧→Ref2VA）是主体**。改动前 r2v 卡片顺序是「标题 → 资产选择行 → 镜头设置折叠 → body[参考素材 | Prompt+预览]」：衔接模式藏在折叠里（不展开看不到怎么接上一镜），Prompt 虽然大但排在卡片中部右侧，被参考素材和设置抢了视觉焦点。P5 = 纯前端把「拍什么（Prompt）」和「怎么接（衔接）」提到卡片顶部成为主体。
- **衔接徽章行 `.bd-batch-cont`**（新增 `buildContinuityStrip(editor, seg)`）：
  - `⛓ 衔接` 标签 + 三态下拉（**自动 · Ref2VA / Ref2VA 状态驱动 / FL2VA 首尾帧硬锁**），样式做成醒目胶囊（深蓝渐变底 + 亮青文字）。
  - 直接在徽章下拉切换 `seg.continuityMode`（写字段/删字段 + commit + 重渲染，FL2VA 切换时参考图区变首尾帧槽位），**不再需要展开「镜头设置」**。
  - 折叠区里原来的衔接 select（cmRow）已移除——单一数据源，避免两处控件不同步。
- **Prompt 视觉主体**（新增 `createShotPromptBlock(editor, seg, isR2v)` 提取原内联块）：
  - r2v 下 Prompt 提到卡片顶部**紧跟衔接徽章**、全宽大输入（`bd-batch-prompts-center`：min-height 180px、textarea 120px+，标签放大、聚焦高亮），成为卡片第一主体。
  - 事件绑定与原来完全一致：oninput 写 `seg.prompt`、素材命名高亮（`wirePromptImageMentions`）+ 提示词引用高亮（`attachPromptHighlight`）。
  - 非 r2v 卡片（t2v/i2v/plain/source/refs）布局不变（grid 位置保持），只走提取后的同一函数。
- **卡片顺序重排**：`标题 → ⛓衔接徽章 → Prompt → body[参考素材 | 预览] → 资产选择行 → 镜头设置折叠`。
  - 实现方式：`assetSelRow`/`cfgWrap` 改为**先建后挂**（`let assetSelRow/cfgWrap = null` 作用域提升，body 之后再 `card.appendChild`），保证 DOM 顺序即视觉顺序。
  - 折叠头 summary 保留（仍显示 `衔接: 自动 · Ref2VA`），作为折叠态的第二提示点。
- **涉及**：
  - `web/js/minimax_image_batch.js`：新增 `buildContinuityStrip`/`createShotPromptBlock`；`renderImageBatchGroups` 卡片顺序重排 + 延迟挂载资产行/折叠；移除 cmRow 块；CSS `.bd-batch-cont*` / `.bd-batch-prompts-center`。
  - `web/js/minimax_i18n.js`：ZH+EN `cont.badge/badgeTitle`、`cont.mode.auto/ref2va/fl2va`。
- **已知坑（How to apply）**：
  - 衔接模式现在**只有一个控件**（顶部徽章下拉）；折叠区里的 cmRow 已删，别再往 `cfgGroupCont` 里加第二个衔接 select，否则两份数据源不同步。
  - r2v 下 Prompt 必须**先建后挂**在 body 之前（`createShotPromptBlock` 返回后再 `card.appendChild(prompts)`）；非 r2v 仍在 body/media 之后挂载，grid 列布局依赖 append 顺序，别统一提前。
  - `assetSelRow`/`cfgWrap` 是延迟挂载：它们的**子元素创建/挂载**（`makeSegmentAssetSelect`、state/st/ck 行进折叠组）仍在原位置，只是 `card.appendChild` 移到 body 之后；改顺序时注意别把子元素挂到还没进 DOM 的容器导致丢失。
  - 旧 `batch.continuityMode/continuityAuto/Ref2va/Fl2va` i18n 键与 `.bd-batch-r2v-continuity` CSS 已无 DOM 引用，保留无害（勿复用给新控件，新控件用 `cont.*`）。
  - 折叠「续接」组现在只剩状态变更输入；衔接信息通过顶部徽章 + 折叠头 summary 双提示。
- **验证标准**：node --check 双目录通过；重启后 r2v 批卡片从上到下依次是：`🎬 Shot N` 标题行 → `⛓ 衔接` 徽章（下拉三态可选、显示当前模式）→ Prompt 大输入区 → 参考素材/预览 → 角色/场景下拉 → 镜头设置折叠；顶部徽章切 FL2VA 后参考图区变首帧/结束帧槽位；非 r2v 卡片布局与原来一致。**用户需重启 Comfy Desktop 验证**。

> **P5 布局修正（用户反馈「不方便」，2026-08-08）**：第一版把 Prompt 硬提到卡片最顶（全宽）、参考素材/角色场景下拉沉底，打断了「先传参考图 / 选素材 → 再写提示词」的操作动线，且违背 P2「资产选择常显靠上」原则。用户指出后修正为：
> - **角色/场景/所属场景下拉回到标题下方**（常显，先选素材再写词）。
> - **body 左右布局恢复**：左列 = 参考素材（图/视频/音频），右列 = `⛓ 衔接徽章 → Prompt 大输入（视觉主体）→ 预览`。参考图与 Prompt 左右并排，「左看图右写词」动线自然。
> - 衔接徽章 + Prompt 仍是卡片视觉中心（P5 诉求保留），但不再打断素材→内容的顺序。
> - 镜头设置折叠保持沉底。
> - 改动：`renderImageBatchGroups` 中衔接徽章/Prompt 从卡片顶部移入 `r2vMain` 右列、`assetSelRow` 提前挂到标题后、底部只延迟挂 `cfgWrap`；CSS `.bd-batch-prompts-center` 改 `flex:1 1 auto`（右列内撑满）。**用户需重启 Comfy Desktop 验证**。

### 40. UI 2.1 P6：高级参数继续折叠——导出 / 音频 收进高级设置（任务 #113-116）

- **背景（Why）**：P6 目标是把剩余的技术参数全部收进底部「高级设置」折叠面板。第四优先级已收 Seed/CFG/Steps/Sampler/Scheduler/Shift/VRAM；P6 收编剩下的**导出参数**（导出方式 / 最大帧数 / 段间引导）和**音频参数**（声音模式）。头部输出栏只保留创作高频项（分辨率/比例、帧率、输出预览），低频输出项收进高级面板——符合 P1「普通用户只看时长/比例/模式/参考素材，技术参数藏进高级设置」。
- **高级面板新增两组**（`minimax_advanced_panel.js`）：
  - **导出设置**：导出方式 `exportMode`（场景/全片/分镜/内存合并）+ 最大帧数 `maxExportFrames`（0 = 不限）+ 段间引导 `continuityEnabled` + 参考帧数 `continuityOverlapFrames`。
  - **音频**：声音模式 `audioMode`（生成声音 / 使用原声 / 静音）。
  - 全部走 `_editor.onOutputField(...)` 镜像读写 `timeline.output`（复用归一化 + commit + flush 链路，与头部原控件同一数据源）；`syncFromWidgets` 读 `timeline.output` 回填。
  - `getAdvancedPanelUiHeight` 加入导出设置（4 行）+ 音频（1 行）两组高度累加。
- **头部输出栏精简**（`minimax_timeline.js`）：导出方式 / 最大帧数 / 声音三个快捷控件**强制 hidden**（保留 DOM 元素，防 `querySelector` 引用失效），统一由高级面板控制；删除已无引用处的 `showBatchExport` 局部变量（`isVideoBatchTask` 其余调用点不受影响）。
- **涉及**：
  - `web/js/minimax_advanced_panel.js`：新增「导出设置」「音频」两组 DOM + 事件绑定 + sync + 高度计算。
  - `web/js/minimax_timeline.js`：头部显示逻辑三处强制 hidden；删 `showBatchExport` 死变量。
  - `web/js/minimax_i18n.js`：ZH+EN `panel.adv.exportGroup/audioGroup`、`output.maxFramesHint/segmentContinuityHint`（组内既有 `output.exportMode.*`/`output.audio.*`/`output.maxFrames`/`output.segmentContinuity`/`output.continuityOverlap` 复用）。
- **已知坑（How to apply）**：
  - 导出方式 / 声音切换入口已移到高级面板（默认折叠）。头部输出栏不再显示这三个控件，但 DOM 仍保留——**别恢复它们的 `.hidden` toggle**，否则与高级面板重复控制。
  - `showBatchExport` 变量已删，勿再引用；`out-export-mode`/`out-max-frames`/`out-audio-wrap` 的 onchange 绑定保留（元素在 DOM 中），改高级面板时只需操作 `timeline.output`。
  - 高级面板新增行/组必须同步 `getAdvancedPanelUiHeight`（见坑 #21），否则节点高度裁切。
- **验证标准**：node --check 双目录通过；重启后头部输出栏只显示宽/高、帧率、输出预览；展开底部高级设置可见「导出设置」「音频」两组，切换导出方式/声音/最大帧数/段间引导后生成/导出行为与原先头部控件一致。**用户需重启 Comfy Desktop 验证**。

### 41. 架构固底 ①-④：Shot 归属 Scene / 资产继承 / per-shot 模式独立 / 续接方式独立（任务 #117-120）

- **背景（Why）**：用户 2026-08-08 架构评审：后端/数据结构方向已对，但 5 个底层关系必须固定（不要大改 UI，先把底层关系钉死）。已落地前 4 个（⑤ 尾帧输入/⑥ 整体生成/⑦ 整体导出见 #121 回归）：
  1. **① Shot 必须属于 Scene**：新增强制归一化 `ensureShotsInScene`——`normalizeImageBatchSegments` 开头把 `sceneId` 为空/指向不存在场景的段归入第一个场景（按 order 排序）；`deleteScene` 改为把被删场景的段移交第一个剩余场景，不再清空。
  2. **② Scene 资产自动继承**：新增 `inheritSceneAssetsToShots`——段未手动指定 castId/locationId 时自动继承所属场景 `defaultCastId/defaultLocationId`（手动选择过的尊重手动值，`castManual/locationManual` 标记优先）。
  3. **③ Shot 独立决定 R2V/FL2V（per-segment taskType）**：新增 `makeSegmentModeSelect`（assetSelRow 第 4 项「模式」下拉 auto/R2V/FL2V），写 `seg.taskType`；后端 `seg_task = taskType || task_type` 已有解析，此下拉让每镜可独立覆盖全局任务类型。
  4. **④ 自动续接 vs 智能尾帧彻底分离 + Shot 独立决定续接方式**：
     - `continuityMode` 语义重定义为「本镜怎么接上一镜」四态：**auto**（自动续接，默认）/ **none**（独立镜头，不续接）/ **ref2va**（强制状态驱动）/ **fl2va**（强制首尾帧硬锁）；不再决定生成模式——`task_key` 由 taskType 独立决定（`fl2va` 仅在 taskType 非 fl2v 时才强制为 fl2v，向后兼容）。
     - 后端 `executor_core`：`continuity_mode=="none"` 段级覆盖全局 `r2v_auto_handoff`（r2v_handoff / fl2v_handoff / auto-handoff / handoff_video / handoff_reference 全部关闭）→ 该镜真正独立生成。
     - UI：智能尾帧从「生成」组迁入「续接设置」组，成为独立第二个控件（🎯 与 🔗 徽章、模式下拉完全独立）；「续接设置」组 = 🎯 智能尾帧 + 状态变更；「生成设置」组只剩关键镜头；折叠头摘要显示「衔接:xxx · 尾帧:yyy」双状态。
- **涉及**：
  - `web/js/minimax_image_batch.js`：`ensureShotsInScene`/`inheritSceneAssetsToShots`（normalize 开头）+ `makeSegmentModeSelect`（assetSelRow）+ `normalizeContinuityMode` 支持 none + `buildContinuityStrip` 四态 + 智能尾帧迁入 `cfgGroupCont` + 折叠摘要双状态。
  - `web/js/minimax_scene_manager.js`：`deleteScene` 段移交剩余场景（不回退空）。
  - `director/gen_timeline.py`：`continuityMode` 合法值加 `none`；`fl2va` 覆盖 task_key 加 `!= "fl2v"` 条件；`none` 跳过 strong-prompt 路由。
  - `director/executor_core.py`：`_auto_cont` 段级 `none` 覆盖；`is_continuity_active`/`handoff_video`/`handoff_reference` 排除 `none`。
  - `web/js/minimax_i18n.js`：`cont.mode.none`（独立镜头）、组标题 `🔗 续接设置`/`⚙ 生成设置`、`batch.cfgSummaryCont/Tail`；**补 EN 缺失的 batch.stateChange / batch.smartTail 系列 / panel.batch.smartTail / tooltip.smartTail**（历史遗漏，本次补全）。
- **已知坑（How to apply）**：
  - `continuityMode` 是「续接方式」不是「生成模式」；要改生成模式改「模式」下拉（taskType），两者解耦后勿再让徽章覆盖 taskType。
  - `none` 段仍受全局 `r2vAutoContinuity` 约束吗？**不受**——`none` 段级覆盖（只关本镜）；全局开关仍控制非 none 段的默认续接。
  - payload 白名单（`sanitizeSegmentForPayload`）走 rest 保留，taskType/continuityMode/smartTail/sceneId 自动透传，无需额外加键。
- **验证标准**：node --check 双目录通过；py_compile 通过；架构④语义 selfcheck 9+5 用例全过（none 不续接且不被 strong 劫持、fl2va 仍强制 fl2v、auto+strong 仍路由 fl2v）。**用户需重启 Comfy Desktop 验证**：①新建场景删场景后所有段必有归属；②场景默认资产自动落到段上；③每镜可独立选 R2V/FL2V；④折叠「续接设置」组可见 🎯 智能尾帧，徽章选「独立镜头」后该镜不接上一镜。

### 42. 架构⑤⑥⑦ 回归确认 + 场景下拉「无场景」空选项修复（任务 #121-122）

- **架构⑤⑥⑦ 回归（无回归，全部通过）**：
  - **⑤ 上一镜尾帧 → 本镜输入**：executor 三路续接（`r2v_handoff` / `fl2v_handoff` / `is_continuity_active`）均取上段尾帧（`resolve_prev_segment_output`），`_seg_cont != "none"` 只对独立镜头关闭，auto/ref2va/fl2va 行为完全不变；`continueShooting` 新建段继承 prev 的 cast/location/sceneId + 置 `continuityMode:"auto"`、`smartTail:true`，与 ①-④ 兼容。
  - **⑥ Scene 整体生成**：`_sceneRunIndices` 按 sceneId 过滤（fl2v 映射 startFrame），段级 taskType/continuityMode 保留在 seg 上透传后端，互不干扰。
  - **⑦ Scene 整体导出**：`exportScene` = 预设 exportMode=scene + runSelection=[本场景镜头] → 触发运行，不涉及本次改动路径。
- **makeSegmentSceneSelect 修复（架构①固底补全）**：回归检查发现 r2v 卡片场景下拉仍保留「无场景」空选项，与架构①「每个 Shot 必须属于 Scene」冲突。现改为：**有场景时不显示空选项**，段未归属时自动落入第一个场景（order 最小，与 `ensureShotsInScene` 一致）；仅场景列表为空时才显示「未建场景」占位。`sel.onchange` 兜底 `sorted[0].id`。
- **涉及**：`web/js/minimax_image_batch.js`（`makeSegmentSceneSelect` 分支重构）。
- **验证**：node --check 源/部署双目录通过；py_compile（gen_timeline/executor_core/plan）通过；双目录全量 cmp 一致（plan.py 仅行尾符 CRLF/LF 差异，内容相同）。**用户需重启 Comfy Desktop** 验证：场景下拉不再出现「无场景」，新建段自动归属第一场景。

### 43. 紧急修复：image_batch.js 孤立注释 SyntaxError → 节点白板（任务 #123）

- **事故**：用户重启 Comfy Desktop 后 Director 节点变白板（"东西全没了"）。
- **根因**：本次 #42 编辑 `makeSegmentSceneSelect` 时，其上方注释的 `/**` 开头行被覆盖丢失，残留成孤立 ` *  写入...` + ` */` 片段（`/*` 开头没了）。该片段在 ESM 解析时是 `Unexpected token '*'` → 整个 `minimax_image_batch.js` 模块**语法错误** → 浏览器 import 失败 → 依赖它的 timeline.js 等全部加载失败 → 节点 UI 白板。**node --check 未检出**（detect-module 模式漏检），真实 `import()` 才暴露。
- **修复**：952 行孤立 ` * ` 恢复为 `/** `，与 953 行 ` */` 正常闭合。
- **验证新标准（How to apply）**：
  - `node --check` 对含 import 的 ESM 文件不可靠，**必须用 `node --input-type=module --check < file.js`**（真实 ESM 语法检查）。
  - 或 `node -e "import('file:///...').catch(e=>console.log(e.constructor.name))"` 真加载——SyntaxError 即白板根因；`Cannot find module api.js` / ReferenceError 属预期（ComfyUI 全局依赖，沙箱缺失）。
  - 本次已对源+部署全部 11 个 JS 文件做 ESM check 全过；双目录 cmp 一致。
- **用户操作**：重启 Comfy Desktop（或浏览器强刷 Ctrl+Shift+R）后节点应恢复。

### 44. 四项数据模型修复：资产命名 / location 字段 / 资产继承 / segments-vs-shots 单一数据源（任务 #124-127）

- **① 素材名称 `[object File]`（#124）**：
  - 根因：`fileBaseName(File)` 用 `String(File)` → 得到 `"[object File]"` 存进资产 `name`。
  - 修复：`minimax_image_batch.js` / `minimax_scene_manager.js` 的 `fileBaseName` 对对象优先取 `.name` 并守卫字面量 `"[object File]"` → 返回 `""`；新增 `sanitizeAssetName`（资产加载时兜底：名字无效则回退图片文件名）+ `migrateLocationAssetKey`；后端 `plan.py` 加 `_BAD_ASSET_NAME` / `_clean_asset_name`，`_load_global_assets` 存 `name=_clean_asset_name(item)`。
  - 验证：6 例函数测试全过，任何输入不再泄漏 `[object File]`。

- **② 统一 `assets.location` → `locations`（#125）**：
  - 规范：资产块只保留复数键 `locations`（全局 `timeline.assets` 与场景 `scenes[].assets` 一致）。
  - 修复：前端 `migrateLocationAssetKey`（把旧单数数组 `location` 迁移到 `locations`、按 id 去重、删除单数键）接入 `getAssetsBlock`（image_batch/export_center）与 `getScenesBlock`（scene_manager）及 `parseTimeline`；后端 `gen_timeline.py` 全局与场景组解析都加了 `locations or location` 兼容回退。
  - 注意：`scenes[].location`（场景地点元信息，字符串）是**兄弟字段**，不是资产块，不参与迁移。

- **③ Scene→Shot→Scene Assets→Global Assets 继承链（#126）**：
  - 优先级（高→低）：段手动 `castId/locationId`（含显式「无」= 空串 + `castManual/locationManual=true`，禁止被覆盖）→ 场景默认 `defaultCastId/defaultLocationId` → `@名字` 同名匹配（场景素材组优先）→ 全局默认。
  - 修复：`inheritSceneAssetsToShots` 尊重手动标志（`!seg.castId && !seg.castManual && sc.defaultCastId` 才继承），不再覆盖显式「无」；段卡下拉 `🎬 场景素材组 / 🌐 全局资产库` optgroup 分组，每段可额外覆盖/添加。`SCENE_PROMPT_GUIDE.md` 第 4 节更新为完整继承链说明。
  - 验证：5 例函数测试全过（继承默认 / 显式无保留 / 手动保留 / 位置继承而角色手动 / 无默认 noop）。

- **④ segments 与 shots 双重数据源一致性（#127）**：
  - **结论**：不是双重数据源——**fl2v 模式以 `timeline.shots` 为唯一正式数据**，`segments`/`keyframes` 由 `syncFl2vFromShots` 派生（兼容层，1:1）；**r2v/image_batch 模式以 `timeline.segments` 为正式数据**，`shots`/`keyframes` 是 fl2v 工作区（stash/restore 隔离）。两种模式互不串用。
  - **计数不一致修复**：后端 `fl2v_timeline.py` `run_count` 从 `len(timeline.shots)`/`len(keyframes)` 改为 `len(shots)`（`_normalize_shots` 归一化/`_expand_shots` 展开后的**实际生成组数**）——无首帧 shot 会被 `_normalize_shots` 跳过，`len(keyframes)` 因 end 帧成对虚高，两者都会让「选择运行」范围大于实际生成数；前端 `minimax_fl2v.js` `fl2vStartIndices` 重写为**镜像后端 `_normalize_shots`**：无首帧跳过，除非 `output.continuityEnabled`（自动续首帧）开启且前面已有带首帧镜头形成续接链，非对象条目硬跳过；`minimax_timeline.js` `_currentRunIndices` 去掉「无首帧镜头回退上一可运行镜头」的旧 keyframes 兜底（shots 世界每镜独立，不可运行即返回空 → 生成下拉禁用）。
  - 验证：前端 8 例 + 后端镜像参考 7 例全过，重叠场景输出一致（全带首帧 / 中间缺首帧 off-on / 首镜缺首帧 / 空 / 非对象 / 旧别名 `continuity_enabled` 视为 off）。进度计数 `count_fl2v_runnable_shots` 与前端 `fl2vStartIndices().length` 已对齐。
  - **How to apply**：后续加 fl2v 计数/进度相关逻辑，一律以「归一化后的 shots 数量」为唯一口径；不要用 `len(timeline.shots)` 或 `len(keyframes)` 当生成数。

### 45. 导出内存崩溃修复：全片导出不再因合并放不下而丢成果（任务 #128-130）

- **用户事故（Why）**：2026-08-08 夜，用户「全片导出」6 段 r2v 共 1475 帧 @1280×736 跑了 3h38m，最后在场景合并阶段报 `合并视频需要约 15.5 GB 内存（当前剩余约 12.7 GB）`。根因：**`movie` 模式的场景组把全部 6 镜归进 1 个场景**，`_encode_scene` → `concat_continuous_chunks` → `_guard_merge_memory` 用 float32 估算 15.5GB 超限直接抛错；且 `_merge_f16` 只对 `export_mode=="all"` 开启，scene/movie 的 chunk 一律转 float32。**「全片导出」≠「全片导出（内存合并）」**：前者是 movie（流式，先编码场景 mp4 再 ffmpeg 直拼），本不该在内存里合并全部 6 镜。
- **修复（三层防线）**：
  1. **scene/movie 按需 f16 合并**：`executor_core.py` 场景分组块把 `_merge_f16` 判定扩到 scene/movie（估算超 `max(8GB, avail*0.3)` 就 f16），并新增 `_decode_f16`（大场景流式降级后解码回 tensor 用 f16，防止 scene 模式 fallback 再 OOM）。
  2. **流式降级 `_stream_concat_scene`**：`_encode_scene` 里内存合并前用 `_scene_merge_fits`（`out*1.8 <= avail*0.7`，比 `_guard_merge_memory` 更保守）预判，放不下就**ffmpeg 直拼各单镜 mp4 → SceneNN.mp4（几乎零内存）**；`concat_continuous_chunks` 若仍抛错（估算与实际有出入）也 catch 降级。段间连续性由生成时续接保证，流式直拼只少像素级接缝优化。
  3. **每镜 mp4 必落盘**：场景循环里每段（生成/缓存/透传）都 `_write_shot_mp4` → `Director_SceneNN_ShotMM.mp4` 写进输出目录，**即使最终合并失败也不会丢掉任何已生成镜头**；`save_segment_cache` 追加 `audio=` 参数，把生成音频另存 `seg_%04d.aud.pt`（Qwen 重跑分支同步传音频）。
- **新增恢复工具 `tools/recover_segment_cache.py`**：把 `output/minimax_seg_cache/<node_id>/seg_*.pt` 解码成可播放 mp4（`save_segment_mp4` 复用 h264+aac），单镜 `Recovered_<node_id>/ShotNN.mp4` + 可选 ffmpeg 直拼整片 `Recovered_<node_id>.mp4`。**找回用户那 4 小时的 6 个镜头用这个**。
- **涉及**：`director/executor_core.py`、`director/segment_cache.py`（+`load_segment_cache_audio`）、`tools/recover_segment_cache.py`（新）。
- **验证**：py_compile 三文件过；双目录 cmp 一致；内存数学复核（1475 帧 f32=15.53GiB / f16=7.76GiB，avail 12.7GiB → `_scene_merge_fits` f16 也判放不下 → 走流式；f16 解码 7.76GiB ≤ 12.7*0.9 放得下）。**用户需重启 Comfy Desktop；旧失败运行的镜头用恢复工具找回（见下文操作指引）**。

### 46. 单镜 mp4 下载按钮：r2v/fl2v 卡片直接下载单个镜头（任务 #131-134）

- **需求（Why）**：导出崩溃恢复后用户想要「在 UI 上直接下载某一镜的 mp4」，而不是整场/整片导出后去输出目录翻 `Director_SceneNN_ShotMM.mp4`。已有 `output/minimax_seg_cache/<node_id>/seg_*.pt`（帧张量）+ `seg_*.aud.pt`（音频）缓存，逐镜下载无需重新生成。
- **后端路由 `GET /minimax/director/segment_mp4`（`director/http_routes.py`）**：
  - 参数：`node_id`（节点 id）、`index`（0 起镜头序号）、可选 `fps`（默认 24.0）。
  - 逻辑：先查 `seg_%04d.pt` 是否存在，不存在返回 404 JSON `not_cached`（前端禁用态已防，兜底报错）；存在则走 `save_segment_mp4`（h264+aac）编码 `seg_%04d.mp4` 落盘缓存，**临时文件编码 + 原子 `os.replace` 替换**，防并发请求写坏半截；已有 mp4 且不比源缓存旧 → 直接 `web.FileResponse` 秒回；音频从 `seg_%04d.aud.pt` 用 `_load_segment_audio_for_download` 读取（无/损坏 → 静音视频）。
  - 响应头：`Content-Disposition: attachment; filename="ShotNN.mp4"`。
- **前端（`minimax_image_batch.js` / `minimax_fl2v.js`）**：
  - 新增导出函数 `downloadSegmentMp4(editor, index)`：`api.fetchApi` → blob → 临时 `<a download>` 触发浏览器下载，文件名 `ShotNN.mp4`；失败 console 提示。
  - r2v 卡片 `meta` 区「生成本镜」后加「下载本镜」按钮（`.bd-batch-shot-dl`，蓝绿色系区分生成钮）；fl2v 卡片头 `.bd-fl2v-shot-head` 右侧加同款按钮（`.bd-fl2v-shot-dl`，`margin-left:auto` 右对齐）。
  - **按钮可用性绑定状态灯**：`editor._segCacheStatus?.get?.(index) === "success"` 才可点；未生成时禁用 + tooltip 提示「先生成本镜再下载」。点击 `stopPropagation` 防误触卡片选中/拖拽。
- **i18n（`minimax_i18n.js`）**：`shot.download`（下载本镜 / Download）、`tooltip.shotDownload`、`tooltip.shotDownloadPending`——**ZH+EN 双语**（按钮在两种语言下都可见，缺 EN 键会显示 key 名）。
- **涉及**：`director/http_routes.py`（+segment_mp4 路由）、`web/js/minimax_image_batch.js`（+downloadSegmentMp4 + 按钮）、`web/js/minimax_fl2v.js`（+按钮）、`web/js/minimax_i18n.js`（+3 键双语）。
- **验证**：三前端文件 `node --input-type=module --check` 过（ESM 必须用该方式，`node --check` 漏检 import）；双目录 diff 一致；py_compile http_routes 过；`save_segment_mp4` 签名确认（`(path, frames, audio, *, fps)`）。**用户需重启 Comfy Desktop**（新路由启动时注册，热更新无效）。

### 47. 修复「全片导出成功但单镜下载显示尚未生成」（任务 #135）

- **症状**：用户全片导出（movie）成功、`seg_*.pt` 缓存落盘，但 r2v/fl2v 卡片「下载本镜」按钮仍禁用，hover 显示「该镜尚未生成」。
- **根因（Why）**：`refreshSegmentCacheStatus()` 是异步 fetch `/segment_status`，取回 `_segCacheStatus` 后只重绘了 `renderShotNavBar()` + `renderConsoleHead()`，**没有重绘卡片本体**（`renderImageBatchGroups()` / `updateFl2vDetailUI()`）。而运行结束的 finish 分支里 `refreshSegmentCacheStatus()` **未 await**，紧接着的 `renderImageBatchGroups()` 用的是**旧 map**——里面的镜头仍是进度事件标记的 `"running"`（永远不是 `"success"`），下载按钮于是停留在禁用态；等异步 fetch 把 `"success"` 写进 map 时，卡片已经不会重绘了。
- **修复（`web/js/minimax_timeline.js`）**：
  1. `refreshSegmentCacheStatus()` 更新 map 后追加卡片重绘：image batch → `renderImageBatchGroups()`；fl2v → `updateFl2vDetailUI(this)`。从此「状态灯」与「下载按钮」在任何刷新路径（初始化/运行结束/报错）都会跟着落到最新状态。
  2. `setRunProgress` 改为 `async`，finish 分支 `await this.refreshSegmentCacheStatus()` 再重绘卡片——避免先用旧 map 画一帧「运行中」再被异步覆盖。
- **下载失败可见化（`minimax_image_batch.js`）**：`downloadSegmentMp4` catch 里原来只有 `console.warn`（用户看不到），补 `window.alert` 显示后端错误消息（如 404 `not_cached`），排查节点 id 对不上缓存目录等场景。
- **验证**：三前端文件 `node --input-type=module --check` 过；双目录 diff 一致。**用户需重启 Comfy Desktop**（前端 js 热更新可生效，保险起见重启）。若仍有「尚未生成」，到浏览器 DevTools 执行 `fetch('/minimax/director/segment_status?node_id=5').then(r=>r.json())` 核对缓存节点 id 是否等于该节点 id（`app.graph.nodes.find(n=>n.type.includes('Director')).id`）。

### 48. MiniMax Studio 前端 V1.0 脚手架 + 数据链路 round-trip 验证（任务 #142-147）

- **背景**：蓝图 v1.1（FRONTEND_SPA_BLUEPRINT.md §8 V1 计划）动工。独立 Vue3+TS+Vite SPA，把 Director 节点当后端引擎。**已拍板 11 条硬原则**：单分镜工作台、Director Core/Adapter 严格分层、ShotModel→TimelineStructure→Adapter→timeline_data 唯一链路、durationSec 为唯一真相、Scene 一级单位、V1 只做 r2v prompt_batch、WS 主动连接+轮询兜底等。
- **新增 `frontend/` 完整工程**：
  - 脚手架：`package.json`（vue3.5 / vite6 / vitest / vue-tsc / @types/node）、`vite.config.ts`（dev 代理 /object_info|/prompt|/queue|/history|/ws(ws:true)|/view|/api|/minimax → 127.0.0.1:8188）、tsconfig 三件套。
  - 模型层 `src/models/`：`project.ts`（Project/Episode/Scene/Shot/Asset/ShotGeneration/ShotRefs，**无 frameCount/length**）、`timeline.ts`（TimelineStructure/TimelineScene/TimelineShot，资产 locations 复数）、`render.ts`（RenderPlan）。
  - 核心 `src/core/directorCore.ts`：`buildTimelineStructure()`/`buildRenderPlan()`，**不出现任何 H3 字段名**；继承 castId/locationId = shot → scene 默认 → episode 全局。
  - 适配层 `src/adapters/`：`directorAdapter.ts` 接口（buildTimelineData/buildNodeWidgets/parseProgress|Preview|FinishEvent）+ `minimaxH3Adapter.ts` v0（**17k+5 帧网格** `MINIMAX_FRAME_GRID(k)=17k+5`，上限 512；durationSec→frameCount 派生；start 累计；22 字段段白名单全量序列化；locations 复数）。
  - 服务层 `src/services/`：`comfyApi.ts`（object_info/prompt/queue/history/interrupt/segment_status/segment_cache_status）、`comfyWs.ts`（clientId+自动重连+事件分发）、`directorRun.ts`（提交时注入 `timeline_data`+`timeline` 兼容字段，WS 进度/预览/finish 翻译）。
  - 状态 `src/stores/workbench.ts`（Vue reactive，不引 Pinia）+ 最小工作台 `src/workbench/WorkbenchView.vue`（四区：左分镜树/中播放器+时间线/右镜头设置/底任务中心，暗色主题）。
  - Fixtures `frontend/fixtures/h3/`：`sample-r2v-timeline.json`（真实契约：version5/prompt_batch/2 场景 3 段/124-141-141 帧）、`sample-project.json`（语义模型，5s/6s/6s）、`sample-workflow.json`（prompt API 格式节点 1-7）。
- **round-trip 测试（`tests/roundtrip.test.ts`，7 个用例全绿）**：验证 Project Model → Core → Adapter 产出与 sample 契约**逐字段一致**——version5、totalFrames 406、frameCount 124/141/141、start 累计 0/124/265、22 字段齐、locations 复数、WS 事件翻译。
- **修复 build 期 17 个 TS 错误**：`generation` 缺省值用完整 `ShotGeneration` 而非 `{}`；refs 映射用 `String(i)`+`fileName`（RefImage 无 id/name）；`toSegment` 未用参数改 `_structure`；`useWorkbench()` 去掉显式返回类型（`reactive` 解包丢掉类私有成员导致类型不兼容）；sample-workflow.json 改走 `loadSampleWorkflow()`（原相对路径 `../fixtures/` 在 src/workbench/ 下解析错位）；删未用变量；vite.config 去 `mode`；补 `@types/node`。`npm run build` 通过（24 模块，85.7kB JS），`npm test` 7/7 绿。
- **同步**：`frontend/` 双目录 rsync 一致（排除 node_modules/dist），部署目录新增 `frontend/` 源码。
- **验收前提**：V1.0 通信层真实闭环（SPA 提交现有工程 → /prompt → 真 H3 → 视频 → 进度回传）需 ComfyUI 运行。启动方式：`cd frontend && npm run dev`（dev 代理 127.0.0.1:8188）。

### 49. 修复 Vite dev 代理 POST /prompt 403（ComfyUI origin_only_middleware 校验）

- **症状**：SPA 在 dev 模式连接 ComfyUI 成功（GET /object_info 通），但点「生成」提交 /prompt 返回 **403**。
- **根因（Why）**：ComfyUI `server.py` 默认（未设 `--enable-cors-header` 时）无条件挂载 `create_origin_only_middleware()`：请求带 `Host`+`Origin` 且 Host 是 loopback 时，**Host 域名与 Origin 域名必须一致，否则 403**（防外部网站 POST 127.0.0.1 的 CSRF 防护）。Vite dev 代理下浏览器发 `Origin: http://localhost:5173`，`changeOrigin:true` 只改写 `Host` → `127.0.0.1:8188`，**Origin 原样保留** → 域名不匹配 → 403。GET 不带 Origin 故不触发，所以「连接成功」但「提交 403」。
- **修复（`frontend/vite.config.ts`）**：给每个 HTTP 代理目标加 `headers: { Origin: comfyOrigin }`（`comfyOrigin = new URL(comfyBase).origin`，跟随 VITE_COMFY_BASE）；WS 代理加 `rewriteWsOrigin: true`。浏览器视角仍是同源 5173，ComfyUI 视角 Origin=Host 匹配，校验通过。**无需改后端/ComfyUI 源码**。
- **How to apply（后续若遇 dev 代理 403）**：先查 `Host` vs `Origin`；任何走 Vite 代理访问 ComfyUI 的请求都要改写 Origin，别直接 fetch 8188（跨源还过不了 CORS）。
- **验证**：配置同步部署目录 diff 一致；`node -e "new URL('http://127.0.0.1:8188').origin"` 输出正确。**用户重启 `npm run dev` 后生效**。

### 50. 修复提交 /prompt 500（validate_prompt 撞上顶层 `_comment` 字符串键）

- **症状**：403 修复后再点生成 → `[ERROR] Error handling request from 127.0.0.1`，`execution.py validate_prompt` 抛 `AttributeError: 'str' object has no attribute 'get'`（`node_data.get('_meta', {}).get('title')`）。
- **根因（Why）**：ComfyUI `validate_prompt` 把 prompt **顶层每个键都当节点**：`for x in prompt:`，对没有 `class_type` 的键执行 `prompt[x].get('_meta')`。我们的 `sample-workflow.json` 顶层带了个说明字段 `_comment`（值是字符串）→ `'class_type' in "样例工作流…"` 为 False → 进分支对字符串调 `.get` → 崩。
- **修复**：
  1. `frontend/fixtures/h3/sample-workflow.json`：删掉顶层 `_comment` 键。
  2. `frontend/src/services/directorRun.ts` `run()`：提交前**顶层净化**——只保留值是「含 `class_type` 字符串的对象」的键，丢弃 `_comment` 等杂散键（防御任何导入工作流带注释/图格式残留键）。
- **附注**：`validate_inputs` 只遍历节点声明的输入，**多余输入键会被忽略**——所以 run() 注入的 `timeline`（Director 节点没有的输入名）无害，不需要删。
- **验证**：build 过（24 模块 85.5kB JS）、round-trip 测试 7/7 绿；双目录 diff 一致。**用户重启 `npm run dev` 后生效**。

### 51. node_modules 平台混用问题与清理（沙箱 Linux vs 用户 Windows）

- **现象**：我在沙箱 Linux 里 `npm install` 生成的 `node_modules`（esbuild 等原生二进制为 ELF/Linux 版）被同步到用户 D 盘，Windows 上直接跑会崩。
- **处理**：源目录 `frontend/node_modules` 已删除；用户在 Windows 上 `npm install` 会装回 win32 版。残留 `node_modules.old`（两个 win32 二进制被夸克网盘同步锁住无法经挂载删除）无害，可手动删或无视。
- **How to apply**：**沙箱里给前端装依赖只用于 build/typecheck/test 验证，装完删掉 `node_modules`，不要把 Linux 版同步到用户目录**；用户机器的依赖由用户自己 `npm install`（Windows 原生平台）。

### 52. 修复提交 /prompt 400（Director 节点 required 的 `bd_grp_sample` 缺失）

- **症状**：400 `prompt_outputs_failed_validation`，`node_errors[5]` 报 `required_input_missing: bd_grp_sample`（MiniMaxH3Director）。
- **根因（Why）**：`nodes/director_common.py` 的 `director_timeline_required_inputs()` 把 `bd_grp_sample: ("BDGROUP", {"default": "采样设置"})` 放进 **required**（UI 分组标签，但被校验器当必填输入处理）；`bd_grp_advanced`/`bd_grp_perf` 在 optional。我们的手写 API workflow 漏了这三个分组键 → validate 报 missing。
- **修复（`frontend/fixtures/h3/sample-workflow.json`）**：节点 5 inputs 补 `bd_grp_sample:"采样设置"`、`bd_grp_advanced:"高级采样 Advanced"`、`bd_grp_perf:"性能 Performance"`（值与 example_workflows/minimax_h3_director_r2v.json 的 widgets_values 对齐）。节点 execute() 用 `**kwargs` 吸收这些分组键，运行不受影响。
- **顺带**：`buildNodeWidgets` 补上漏掉的 `total_frames`（widgets 21 项与 example 完全对齐，并加注释标注每个 widget 对应 INPUT_TYPES 字段）。
- **How to apply**：改 Director 节点 API workflow 时必须对照 `nodes/director.py` / `director_common.py` 的 INPUT_TYPES 填齐 required（含 BDGROUP 分组标签）；手工拼 workflow 前先 `python -c "json.load(...)"` 校验 required 键齐。
- **验证**：JSON 合法、required 键齐全、双目录 diff 一致。**用户重启 `npm run dev` 后生效**。

### 53. 前端成片回看：生成完能播视频（executed 事件 → /view URL → 播放器）

- **用户反馈**："视频生成了也看不了"——播放器只显示 WS 预览帧（静态图），没有成片回看。这是 V1.0 工作台最大的 UX 缺口，本期补齐**「生成 → 成片回看」闭环**。
- **契约确认（Why）**：`SaveVideo` 是 ComfyUI 内建输出节点（`comfy_extras/nodes_video.py`），执行完返回 `ui.PreviewVideo([ui.SavedResult(file, subfolder, FolderType.output)])`，序列化成 `{"images":[{filename,subfolder,type}],"animated":(True,)}`。ComfyUI 每完成一个输出节点就发 WS `executed` 事件（`execution.py:578`，payload `{node, display_node, output, prompt_id}`）。`/history/{prompt_id}` 的 `outputs[nodeId]` 也是同构。取 `output.images` 里视频扩展名的条目，拼 `/view?filename=…&subfolder=…&type=output` 即可播放（Vite dev 代理已配 `/view`，同源无 CORS）。
- **实现（前端，全 `frontend/`）**：
  1. `src/services/comfyApi.ts`：加 `ComfyVideoRef` 类型、`extractVideosFromUi()`（从节点 ui 输出提取视频列表，兼容 executed + history 两种来源）、`comfyViewUrl()`（构造 /view URL）、`isVideoFile()`（mp4/webm/mov/mkv/avi/gif）、`firstVideoFromHistory()`（跨节点找第一个视频，**优先视频扩展名**，跳过 Director 预览 PNG）。
  2. `src/services/comfyWs.ts`：`WsEventType` 加 `"executed"`。
  3. `src/services/directorRun.ts`：`RunCallbacks` 加 `onVideo`；WS 处理 `executed`（从 `ev.data.output` 提取视频 ref → onVideo）；`execution_success` 时调 `backfillVideoFromHistory()` 用 `/history` 兜底一次（WS 单槽问题下可能丢 executed）。
  4. `src/stores/workbench.ts`：加 `finalVideo {url, filename}` + `setFinalVideo()`/`clearFinalVideo()`（新一轮生成开始清空上次成片）。
  5. `src/workbench/WorkbenchView.vue`：播放器三态——有成片渲染 `<video controls autoplay playsinline>`（下方显示文件名）、有预览帧显示静态图、都没有显示占位；handleGenerate 挂 `onVideo` 回调写 store。
- **测试**：`tests/roundtrip.test.ts` 加"成片回看"测试组 4 个用例（executed 输出提取 / /view URL 构造 / 空与杂散输出忽略 / firstVideoFromHistory 优先 mp4），**共 11 个用例全绿**；`vue-tsc` typecheck + `vite build` 通过（修复 2 个 unused TS 错误：adapter `buildNodeWidgets` 参数加 `_` 前缀、directorRun 删无用的 `lastPromptId`）。
- **同步**：`frontend/` 双目录 rsync 一致（排除 node_modules/dist），DIFF_OK。
- **验收**：用户重启 `npm run dev` → 点生成 → 等 H3 跑完 → SaveVideo 保存完成 → 播放器自动切到成片 mp4 可播。**注意**：`npm run dev` 需用户在 Windows 上已 `npm install`（win32 平台 node_modules）。

### 54. 工作台可编辑：时长 / 分辨率 / 单镜生成

- **用户反馈**："生成的时间以及分辨率无法修改，也不能选择生成单个镜头"。V1 工作台三个可用性缺口，纯前端补齐。
- **改动（全 `frontend/`）**：
  1. `src/stores/workbench.ts`：加 `outputSize {width,height}`（默认 864×480）；`setOutputSize()`（clamp 256-2048）、`updateShotDuration(shotId, sec)`（写回项目模型 `shot.durationSec`，clamp ≥1，reactive 自动刷新）。
  2. `src/workbench/WorkbenchView.vue`：
     - **顶栏分辨率**：`宽 × 高` 两个 number input，`@change` → `setOutputSize`；生成按钮改「▶ 生成全部」。
     - **时长可编辑**：右侧「时长（秒）」input 加 `@change` → `updateShotDuration`。
     - **单镜生成**：左侧每张镜头卡片加迷你「▶」按钮 + 右侧镜头设置顶部「▶ 生成此镜」按钮，都走 `generateShot(shotId)` = `selectShot` + `handleGenerate([shotId])`。
  3. `handleGenerate(targetShotIds)`：默认 `null`=整片；单镜传 `[shotId]`。分辨率/refMaxSize 从 `wb.outputSize` 读（refMaxSize = 长边），不再硬编码 864。
- **数据链路（Why）**：`buildTimelineStructure` 早已支持 `targetShotIds` → `TimelineStructure.runSelection` → Adapter `buildTimelineData` 映射成 segments 下标数组（`runSelectEnabled` + `runSelection`）。后端 Director 节点按 `runSelection` 只跑选定镜头。时长修改走 `durationSec` 唯一真相源 → Adapter 17k+5 网格派生 frameCount → start/start 累计/总帧数自动跟随。
- **测试**：`tests/roundtrip.test.ts` 加"工作台可编辑"测试组 4 用例——单镜 `targetShotIds:["s2"]` → `runSelection=[1]`；整片 `null` → `runSelectEnabled=false`；`durationSec 5→8` → `frameCount 192`（网格 17*11+5）+ 后续 start 跟随 + totalFrames 474；分辨率 1280×720 → `width/height/refMaxSize` 透传。**共 15 用例全绿**；typecheck + build 通过。
- **同步**：`frontend/` 双目录 rsync 一致，FRONTEND_DIFF_OK。
- **验收**：重启 `npm run dev` → 顶栏改分辨率 → 右侧改镜头时长（可逐镜不同）→ 点某镜头卡片 ▶ 或右侧「生成此镜」只跑该镜。

### 55. 输出分辨率交互重构：比例（含自定义）+ 百万像素档位 + 帧率（默认 24）

- **用户二次反馈（否决第一版）**：首版做成单下拉两组预设（平台比例组 + 百万像素档位组）。用户明确"这个分辨率的设置不是我想要的"——要的是**节点工作流式三控件布局**：① 比例（含自定义）② 百万像素自行选择 ③ 帧率默认 24。按此重构。
- **重写 `frontend/src/workbench/resolutionPresets.ts`**（三控件 + 组合矩阵）：
  - `RATIOS`：16:9 横屏 / 9:16 竖屏 / 1:1 方形 / 4:3 横屏 / 3:4 竖屏 / 21:9 超宽 / **自定义**（手输宽高脱离档位）。
  - `MP_TIERS`：0.3 / 0.5 / 0.9 / 1.5 / 2.1 / 3.7 六档（与 H3 常见 latent 尺寸对齐）。
  - `RESOLUTION_MATRIX`：比例 × MP → 标准分辨率（长边 ≤ 2048，均为 8 倍数，如 16:9 2.1MP → 1920×1080、9:16 对称、4:3 3.7MP → 2048×1536）。
  - `resolutionFor(ratioId, mp)` / `findComboForSize(w, h)` / `scaleToFit()`；`DEFAULT_FPS = 24`，帧率 clamp 8–60。
- **改动**：
  - `src/stores/workbench.ts`：`ratioId`（默认 `"custom"`）+ `mpTier`（默认 `null`）+ `frameRate`（默认 `24`）三状态；`setOutputSize()` 手动改尺寸 → 比例切自定义；`applyResolutionCombo(ratioId, mp)` → 查矩阵写 `outputSize` + 记组合；`setFrameRate()` clamp。
  - `src/workbench/WorkbenchView.vue`：顶栏「比例」select →「MP」select（自定义比例时禁用）→「帧率」number（默认 24）→ 尺寸显示（组合命中只读 `W × H`，自定义时两个可编辑输入 step=16）；`onRatioChange` 切自定义保留当前尺寸；`syncWorkflowFrameRate()` 提交前把帧率写进 workflow——MiniMaxH3Director 节点 `frame_rate` + CreateVideo 节点 `fps`（sample-workflow 的 widget 不再各自为政）。
- **设计决策（Why）**：① 组合矩阵是纯 UI 辅助，不落项目模型/不写 timeline——最终仍走 `outputSize` → `buildTimelineStructure` → Adapter → timeline_data 唯一链路（#54 已打通）；② 16 对齐（如 1080 → 1088）交给后端 `snap_dimension`，前端保留 1920×1080 这类标准友好值；③ 帧率默认 24 且写回 workflow widget，保证 CreateVideo 与 Director 帧率一致，`timeline_data.frameRate` 同步驱动 `frameCount`（17k+5 网格派生）。
- **测试**：`tests/resolutionPresets.test.ts` 重写为 8 用例（比例含自定义、MP 递增、DEFAULT_FPS=24、矩阵 8 倍数且长边 ≤2048、16:9 2.1→1920×1080 与 9:16 对称、自定义无档位、findComboForSize 命中/未命中、scaleToFit 等比）；`tests/roundtrip.test.ts` 新增 `frameRate flows into timelineData and drives frameCount`（30fps 5s → 150 帧 → 网格取 158）。**共 24 用例全绿**；typecheck + build 通过。
- **同步**：`frontend/` 双目录 rsync 一致，FRONTEND_DIFF_OK；CHANGELOG 已同步，CHANGELOG_DIFF_OK。
- **验收**：重启 `npm run dev` → 顶栏「比例」选 9:16 →「MP」选 2.1 → 尺寸区只读显示 1080 × 1920 →「帧率」默认 24，改 30 后点「▶ 生成全部」按 30fps/竖屏提交；选「自定义」→ 宽高变可编辑，改任一项 MP 下拉置灰。
- **二次修正（用户实测反馈，2026-08-09）**：
  - **① 自定义比例点了没反应**：根因 `activeRatioId` 做了 `findComboForSize` 反查——选「自定义」后当前宽高若恰命中矩阵（如 1280×720 → 16:9/0.9MP），下拉又被映射回「16:9 横屏」，看起来像没点中；刷新后默认 864×480 不在矩阵才正常显示。修：`activeRatioId` 直接取 store 显式 `wb.ratioId`，**尊重用户显式选择，去掉反查**。
  - **② MP 要能手输任意合理数值**：MP 从 select 六档改成 **number 输入框**（0.1–4.0 step 0.1，自定义比例时禁用）。新增 `resolutionForMp(ratioId, mp)` 公式：按 `w/h = 比例` 与 `w*h = mp×1e6` 解算，clamp 长边 ≤2048 / 短边 ≥256，floor 对齐 16（不超上限）；`applyResolutionCombo` 矩阵档位优先，手输任意值走公式兜底（16:9 @ 2.5MP → ~2000×1125）。输入框显示逻辑：组合选中显示档位，否则按当前尺寸反推近似 MP。
  - **测试**：`resolutionPresets.test.ts` 扩到 **11 用例**（新增 任意 MP 公式 16 对齐/面积≈档位/比例吻合、公式与矩阵 2.1MP 面积一致性、未知比例/非法 MP 返回 null）。**共 27 用例全绿**；typecheck + build 通过。
  - **同步**：双目录 rsync 一致，FRONTEND_DIFF_OK。

### 56. E2E 真 H3 闭环验收脚本（任务 #156，#157 前置）

- **用户需求**："进行下一步真 H3 闭环验收——SPA 提交工程 → `/prompt` → 真跑 H3 → 进度 → 成片回看，全链路真实过一遍。目前 27 个用例全是 round-trip 单测，数据链路只验证了「模型→JSON 一致」，还没和真后端碰过。"
- **新增 `frontend/tests/e2e.verify.test.ts`**（E2E 真闭环验收脚本，**复用 SPA 生产代码路径**，非另写一套）：
  - 6 步验收：**STEP1 连通性**（`assertDirectorNode`，探测 ComfyUI + 节点可用）→ **STEP2 数据链路**（`loadSampleProject` → `buildTimelineStructure` → `MiniMaxH3Adapter.buildTimelineData`，与 handleGenerate 完全同一条链）→ **STEP3 工作流注入**（`sanitizeWorkflow` + `syncWorkflowFrameRate` + 注入 `timeline_data`）→ **STEP4 提交**（`DirectorRunService.run` → POST `/prompt`，返回真实 `prompt_id`）→ **STEP5 监控**（`ComfyWsClient` WS 进度 + `/history/{prompt_id}` 轮询兜底，3s 间隔直到 success/error/超时）→ **STEP6 成片**（`firstVideoFromHistory` 提取输出 → `/view` 验证可访问 + 打印播放 URL）。
  - 顶层环境变量：`VITE_COMFY_BASE`（默认 127.0.0.1:8188）、`E2E_SCOPE`（`shot`=只生成第一镜最快暴露链路问题 / `all`=整片）、超时 shot 15min / all 60min。
  - ComfyUI 不可达时 `describe.skipIf(!comfyUp)` 自动 skip + 打印排查提示（不会让 `npm test` 挂掉）。
- **改动**：
  - `frontend/package.json`：`"test": "vitest run --exclude tests/e2e.verify.test.ts"`（27 单测不受影响）；`"e2e": "vitest run tests/e2e.verify.test.ts --testTimeout=3600000"`。
  - `frontend/vite.config.ts`：test 配置保持干净（globals + node），注释说明 e2e 用 `.test.ts` 后缀 + `--exclude` 处理；保留 ComfyUI 同源代理（`/object_info|/prompt|/queue|/history|/ws|/view|/api|/minimax`，带 Origin rewrite 头绕过 origin_only_middleware）。
- **设计决策（Why）**：① e2e 脚本直接 import 生产模块（`directorCore`/`adapter`/`directorRun`/`comfyApi`），保证验收的就是 SPA 点「生成」跑的代码，不是测试专用复刻；② WS 连接失败自动降级轮询——真实 ComfyUI 也可能没开 WS 调试事件，轮询 `/history` 是唯一可靠兜底；③ scope=shot 优先交付，跑一镜 ≈ 最快 2-4 分钟暴露链路/模型/提示词问题。
- **沙箱验证**：VM 无法访问主机 ComfyUI（隔离，tap0 172.16.10.3/24），用临时 `outputs/mock-comfy.mjs`（mock `/object_info`、`/prompt`、`/history/{id}`、`/view`，第二次查 history 返回 completed）在 VM 内跑通脚本全流程：**6 步全 PASS**（提交收到带 `timeline_data` 的 `/prompt`、轮询兜底完成、`/view` 200）。验证脚本逻辑本身无 bug，可放心在主机跑真 H3。
- **验收（主机上做，需 ComfyUI 运行）**：`cd frontend && npm run e2e`（默认生成第一镜）或 `E2E_SCOPE=all npm run e2e`（整片 3 镜）。输出 6 行 PASS/FAIL + 汇总；STEP6 打印成片 `/view` URL 浏览器可播放。**ComfyUI 不可达会自动 skip**并提示：启动/重启 ComfyUI、确认节点加载、检查端口与 `VITE_COMFY_BASE`。
- **同步**：`frontend/` 双目录 rsync 一致，FRONTEND_DIFF_OK；CHANGELOG 已同步，CHANGELOG_DIFF_OK。
- **运行环境追加（用户实测报错，2026-08-09）**：
  - ① `'vitest' 不是内部或外部命令`——源码 `node_modules` 被此前 VM 侧 `rm -rf node_modules` 清理时在挂载目录上留下残缺状态（I/O error，部分删除）。修：Windows 上完整重装 `rmdir /s /q node_modules && del package-lock.json && npm install`。
  - ② `E2E_SCOPE=all npm run e2e` 在 Windows cmd 不认 Unix 语法。修：新增跨平台运行器 `frontend/scripts/e2e-run.mjs`（spawn `node_modules/.bin/vitest`，在进程内设 `E2E_SCOPE`），`package.json` 改 `"e2e": "node scripts/e2e-run.mjs"`、`"e2e:all": "node scripts/e2e-run.mjs all"`——**Windows 整片用 `npm run e2e:all`，不要再写 `E2E_SCOPE=all npm run e2e`**。
- **✅ 真机验收结果（2026-08-09，用户主机实测）**：重启 ComfyUI + 重装 node_modules 后 `npm run e2e`（scope=shot）**6 步全 PASS**，耗时 233s（约 4 分钟）：
  - `prompt_id=6cf6b31f-4694-4c1b-8a58-60fe985c53f1` 真实提交；WS 进度 9 条完整推进（准备片段→H3 条件编码→采样→AV 解码→全部完成），`execution_success` 收到。
  - 成片 `MiniMaxH3_Director_r2v_00043_.mp4`（864×480 @24fps），`/view` 200 浏览器可播。
  - **V1.0 通信层全链路真实打通**：Project Model → buildTimelineStructure → Adapter → timeline_data → /prompt → 真 H3 → WS 进度 → 成片 /view。任务 #155/#156/#157 全部完成。下一步 V1.1 Director Core + Project 文件层（导入现有 timeline_data 优先）→ V1.2 分镜工作台。

### 57. V1.1 Director Core 五模块 + 反向导入通道 + Project 文件层（任务 #158-163）

- **用户需求**："直接进入 V1.1"。V1.1 范围（蓝图 §8）：DirectorCore 五模块接口定签名 + 最小实现；Project/Episode/Scene/Shot/Asset TS 类型 + JSON 存取服务（Projects/ 目录）；**优先支持加载现节点导出的 timeline_data 转成前端模型（导入通道）**；验收 = 能打开现有工程，Scene/Shot/Asset 分层渲染。
- **新增五个核心模块（`frontend/src/core/`）**：
  - `assetResolver.ts`：**Asset Resolver**。`buildAssetIndex` 建 name/aliases 索引（名长 ≥ 2）；`resolveMentions` 扫 `@名字` 片段做**子串包含匹配**（与后端 `gen_timeline.py` 命名规则对齐）、同片段同资产去重、保留较长匹配（name 优先于 alias）。
  - `inheritanceResolver.ts`：**Inheritance Resolver**。`resolveInheritance` 按 `①显式(castManual/locationManual) → ②@匹配 → ③场景默认 → ④全局默认` 优先级回填每镜 cast/location，产出 `InheritedAsset{assetId, asset, source, matchedBy}`；`inheritedSource` 给资产打来源标记。
  - `promptBuilder.ts`：**Prompt Builder**。`buildShotPrompt` 生成 `simple`（visual）与 `advanced`（integratedMultimodalDescription 把 visual + 继承资产描述「角色：X，地点：Y」+ camera 三段并 join；overallSoundscape / nonDiegeticMusic 取自 shot.sound；negative 透传）。
  - `timelineImporter.ts`：**反向导入通道**。`parseTimelineData` 把后端 timeline_data 契约 JSON 转成 `Project + ImportMeta`——scenes→Scene（含场景素材组 assets）、segments→按 sceneId 归组 Shot、组内按 start 排序、durationSec 从段恢复（真相源）、孤儿段兜底「未归类」场景；读辅助 `str/num/bool/obj/arr` 全部 camel+snake 兼容（`assets.location` 单数 → `locations` 复数）。
  - `directorCore.ts`（已有）+ `frontend/src/services/projectStore.ts`：`importProjectFromTimeline(JsOn/string)` / `serializeProject` / `deserializeProject` 的 Project 文件层。
- **UI 导入入口**：`WorkbenchView.vue` 顶栏新增「导入 timeline」按钮（隐藏 file input），读 JSON → 校验 `segments` 数组 → `importProjectFromTimeline` → `loadProject` + 把 timeline 的分辨率/帧率写进工作台会话（`setOutputSize`/`setFrameRate`）。
- **设计决策（Why）**：① 导入通道优先是因为用户手上全是现节点导出的 timeline_data，先打通「打开旧工程」才有 V1.2 分镜工作台的地基；② `durationSec` 是唯一真相源，`frameCount/length` 仍由 Adapter 派生（17k+5 网格对齐），导入只恢复 durationSec 不落 frameCount，保证重新导出契约等价；③ 继承优先级与后端 `_scene_asset_pool` / `inheritSceneAssetsToShots` 一致，前端五模块的产出就是后端行为的可预测镜像。
- **测试**：新增 `tests/importer.test.ts`（**5 用例**：sceneId 归组+时长恢复、全局资产+场景素材组 locations 复数、**round-trip**——导入后重新导出还原契约 406 帧/start 0/124/265、孤儿段 fallback、snake_case 兼容读取）+ `tests/coreModules.test.ts`（**9 用例**：resolveMentions 命中/无@/未知/去重、resolveInheritance 三优先级/@匹配/inheritedSource none、buildShotPrompt simple/advanced）。**共 41 用例全绿**（roundtrip 16 + resolutionPresets 11 + importer 5 + coreModules 9）。
- **build 阶段修复**（`npm run build` 即 `vue-tsc -b` 比 `vue-tsc --noEmit` 更严，直接暴露类型错误）：
  - `num(o, camel, 数字)` 三参误用——第三参是 snake 名（string），fallback 必须走第四参 `num(o, camel, undefined, 数字)`（timelineImporter 全部修正）。
  - `round(...)` 未定义 → `Math.round(...)`。
  - `ShotGeneration` 缺 `order`——导入时把段 `start` 存 `generation.order`（排序用），在 `models/project.ts` 补 `order?: number` 字段。
  - `typecheck` 脚本（`vue-tsc --noEmit`）因 tsconfig project references 配置**实际没检查到**这些错误，build 才报——后续改代码以 `npm run build` 为准。
- **同步**：`frontend/` 双目录 rsync 一致，FRONTEND_DIFF_OK；CHANGELOG 已同步。
- **验收**：`npm run dev` 起 SPA → 顶栏「导入 timeline」选一个现节点导出的 timeline_data JSON（如带 scenes/segments 的导出档）→ 左栏 Scene/Shot 分层出现、中栏当前镜预览、右栏镜头设置回填（场景/人物/地点/时长/Prompt/衔接/参考素材）→「▶ 生成全部」按导入工程的分辨率与帧率提交。V1.1 里程碑达成，下一步 V1.2 分镜工作台（编辑增删镜头、资产引用高亮、继承徽标）。

### 58. V1.1.1 项目选择页 + 工程文件层（任务 #164-170，用户设计修正）

- **用户设计修正（关键方向，推翻 #57 的导入入口）**：`timeline_data` 是**后端执行格式**，不应成为用户项目格式；SPA 不应要求用户去 ComfyUI 找工作流 JSON 再抠 `timeline_data`。「导入 timeline JSON 文件」只是**开发阶段验证通道**，不是正式用户流程。正式 UI 只保留：**打开项目 / 新建项目 / 导入 ComfyUI 工程**；「导入 timeline」降级到项目选择页底部「开发工具」折叠区。
- **数据流拍板**：`ProjectModel → DirectorCore → TimelineStructure → MiniMaxH3Adapter → timeline_data → MiniMaxH3Director → 视频`。反向通道只在「导入 ComfyUI 工程」时把生成时的自动快照 `migrate()` 回 ProjectModel。
- **后端工程文件层 `director/project_store.py`（新建，任务 #164）**：存储根 `{ComfyUI input}/minimax_studio/`，`projects/{id}/project.json`（前端 ProjectModel JSON）+ `snapshots/{ts}_{ms}_{uuid6}.json`（原始 timeline_data）。函数：`studio_root/projects_root/snapshots_root`、`_safe_project_id`（basename 剥目录 + 危险字符→下划线，防穿越）、`list_projects`（按 mtime 倒序 `[{id,name,episodes,updatedAt}]`）、`load_project`/`save_project`、`create_project`（空 episode 带 `assets{cast,locations,props,styles}`）、`save_timeline_snapshot`（str/dict 双收，非 JSON 跳过快照）、`list_snapshots`/`load_snapshot`。
- **后端路由 `director/http_routes.py`（任务 #165）**：注册 6 个 `/minimax/director/projects|project/{pid}|snapshots|snapshot/{sid}` 路由（GET 列表/读 + POST 新建/保存）。`nodes/director.py`（任务 #166）：`execute()` 开头 `save_timeline_snapshot(timeline_data)`——**每次生成前自动快照**，「导入 ComfyUI 工程」据此恢复 ProjectModel，用户无需导出/抠 JSON。
- **前端 `services/comfyApi.ts`（任务 #167）**：新增 `ProjectSummary`/`SnapshotSummary` 接口 + `listProjects/createProject/loadProject/saveProject/listSnapshots/loadSnapshot` 六方法。
- **`WorkbenchView.vue` 双视图重写（任务 #168+#169）**：
  - **home 项目选择页**（`v-if="view==='home'"`）：品牌 + 连接态 + 刷新；「最近项目」卡片列表（`listProjects` → 点击 `openProject` = `loadProject` → `deserializeProject`）；「＋ 导入 ComfyUI 工程」（`refreshHome` 拉 `listSnapshots` → 点击快照卡 `importSnapshot` = `loadSnapshot` → `importProjectFromTimeline` → 另存正式项目 → 进工作台）；「＋ 新建项目」（`createProject` → `openProject`）；底部 `<details>开发工具▾</details>` 折叠区 = 导入 timeline_data 文件（调试入口）+ 载入示例工程。
  - **workbench 分镜工作台**（`v-else`）：原有四区布局 + 顶栏新增 `← 项目`（`goHome` = `clearProject` + 回 home + `refreshHome`）与 **`保存项目`** 按钮（`serializeProject` + `api.saveProject`，保存成功打「已保存 ✓」瞬态）。「导入 timeline」文件输入从顶栏移入 home 开发工具区。
  - 导入后分辨率回填 `applyImportedSize`：`findComboForSize` 命中预设 → `applyResolutionCombo`（保留比例档位），未命中才 `setOutputSize` 落自定义；帧率 `setFrameRate(meta.frameRate||24)`。
- **快照文件名碰撞修复（实测发现）**：`{ts}_{int(time.time()*1000)%100000}` 在同一毫秒内连落 5 份会互相覆盖（沙箱时钟分辨率约 1ms，executor 连镜瞬时触发）。改为 `{ts}_{ms}_{uuid.uuid4().hex[:6]}`——毫秒时间戳在前保 `list_snapshots` 按名倒序=时间倒序，短随机片段兜底唯一。
- **测试**：新增 `director/tests/test_project_store.py`（**7 用例**：safe_id 防穿越、create/list、save/load roundtrip、快照 str/dict 双收、非 JSON 拒绝、列表排序+摘要字段、缺省返回 None；`sys.modules` 注入 mock `folder_paths`，`python3 director/tests/test_project_store.py` 直接跑）。前端 **41 用例全绿** + `npm run build` 通过。
- **同步**：双目录 rsync（源 → 部署，部署此前缺 project_store.py，现已补齐）。**新路由需重启 Comfy Desktop 生效**（坑 #16）。
- **验收**：起 SPA → home 出现「最近项目/导入 ComfyUI 工程/新建项目」→ 点「导入 ComfyUI 工程」列出最近生成快照 → 选一条自动进工作台（无需碰任何 JSON）→ 顶栏「保存项目」落盘 → 「← 项目」回 home 出现刚保存的项目。V1.1.1 达成，下一步 V1.2 分镜工作台（编辑增删镜头、资产引用高亮、继承徽标）。

### 59. V1.1.1 项目/快照删除按钮（任务 #171-173，用户需求）

- **用户需求**：「项目和快照都可以弄一个删除按钮」。
- **后端 `director/project_store.py`**：新增 `delete_project(project_id)`（`shutil.rmtree` 删整个项目目录，不存在返回 False）与 `delete_snapshot(snapshot_id)`（删 `snapshots/{sid}.json`，路径穿越/无 .json 后缀拒绝，不存在返回 False）。
- **后端 `director/http_routes.py`**：`_register_route` 补 `DELETE` 分支（`routes.delete(path)(handler)`）；新增 `minimax_director_project_delete` / `minimax_director_snapshot_delete` 两个 handler（不存在返回 404 `{ok:false}`）；注册 `DELETE /minimax/director/project/{pid}` 与 `DELETE /minimax/director/snapshot/{sid}`。
- **前端 `comfyApi.ts`**：新增 `deleteProject(projectId)` / `deleteSnapshot(snapshotId)`（`method: "DELETE"`）。
- **前端 `WorkbenchView.vue` 项目选择页**：项目卡片与快照卡片各加 🗑 删除按钮——`@click.stop` 不触发卡片的打开/导入动作；`confirm()` 二次确认；删除成功后本地 `filter` 即时移除（不整页刷新）。快照卡片按钮绝对定位右上角，`.snap-name` 留右 padding 防重叠。
- **测试**：后端 `test_project_store.py` 新增 `test_delete_project_and_snapshot`（删/二次删 False/路径穿越拒绝/空 id），扩到 **8 用例**；前端 **41 用例全绿** + `npm run build` 通过。
- **同步**：双目录 rsync 一致。**新 DELETE 路由需重启 Comfy Desktop 生效**（坑 #16）。
- **验收**：home 页点任意项目/快照卡片上的 🗑 → confirm → 条目从列表消失；刷新后仍不在（后端真删）。

### 60. V1.2 分镜工作台（镜头/场景增删改 + @资产识别高亮 + 继承徽标 + 场景设置面板）（任务 #175-179，用户拍板范围=完整三件套+场景面板）

- **用户拍板范围**（AskUserQuestion）：V1.2 =「完整三件套」——镜头/场景增删改 + @资产高亮 + 继承徽标；并确认**包含场景设置面板**（默认资产/时间/天气/素材组）。
- **store 编辑原子操作 `frontend/src/stores/workbench.ts`**（V1.2-A，#175）：
  - 新状态 `currentPane: "shot" | "scene"`（右面板模式）+ `selectSceneSettings(sceneId)`（选中场景并打开场景设置，不跳镜头）。
  - 镜头管理：`addShot(sceneId, afterShotId?)`（默认继承场景默认资产、自动重排 order、选中新镜头）、`removeShot`（删空场景自动切场景面板）、`duplicateShot`（副本名「XXX 副本」）、`moveShot`（上下移+边界）、`renameShot`、`moveShotToScene`（跨场景 splice + 重排 + 选中目标场景）；字段写回 `updateShotContent/Negative/Cast/Location/Continuity/SmartTail/StateChange`（cast/location 空串=自动继承，`manual` 标记显式锁定）。
  - 场景管理：`addScene`（空场景+进场景面板）、`removeScene`（连带删其镜头）、`renameScene`、`patchScene`（name/description/location/time/weather/referenceImage/defaultCastId…）。
  - 资产 CRUD：`addAsset/updateAsset/removeAsset`；`ASSET_KIND_KEY` 映射 AssetKind 单数→SceneAssets 复数键（cast→cast / location→locations / prop→props / style→styles）；`removeAsset` 自动清引用（场景默认值 + 镜头显式 cast/location 回退继承）。
- **WorkbenchView.vue UI 改造**（V1.2-B/C/D，#176-178）：
  - **左分镜树**：标题行「＋ 场景」按钮；场景行 hover 操作（＋镜头/✎重命名/🗑删除）；镜头卡片 hover 操作（⇡上移/⇣下移/⧉复制/✎重命名/🗑删除）+ ▶ 单镜生成保留。
  - **右面板双模式**：**场景设置**（名称/描述/时间/天气/地点/参考图 + 「镜头默认值」默认人物/地点/风格 select（🎬场景/🌐全局 optgroup）+ ＋新建 + 「场景素材组」四类 add/remove chip）与 **镜头设置**（场景移动 select、人物/地点带「✨自动继承」选项 + 继承生效行 + 来源徽标、@高亮 Prompt 盒、负面词、衔接、智能尾帧、状态变化、参考素材）。
  - **@资产识别高亮**（C）：`buildAssetIndex` + `resolveMentions`（@名字子串包含、名长≥2、同片段去重、较长匹配优先）；Prompt 盒子**双层渲染**——下层 `v-html` mark 高亮（`.m-cast` 蓝 / `.m-loc` 绿 / `.m-prop` 橙 / `.m-style` 紫）、上层透明 textarea 文字（caret 可见，`@scroll` 下层同步）；命中资产出彩色 chip，**点击 chip 直接绑定为镜头人物/地点**（`bindMention`）。
  - **继承徽标**（D）：`resolveInheritance`（显式>@匹配>场景默认>全局默认）→ 人物/地点「生效：X」+ 来源徽标（⚡镜头指定 / ↑场景默认 / ↑项目默认）。
- **测试**：
  - `frontend/tests/workbenchEdit.test.ts`（新增 **13 用例**）：store 原子操作全链路（镜头增删/复制/移动/重命名/字段写回/跨场景移动；场景增删/改名/patch；资产 CRUD + 引用清理）。
  - `frontend/tests/workbenchRender.test.ts`（新增 **10 用例**，`@vue/test-utils` + happy-dom 渲染冒烟）：真实挂载 WorkbenchView——home→工作台切换、场景树/镜头卡片渲染、场景设置面板、镜头设置 + 继承徽标（人物=⚡镜头指定 / 地点=↑场景默认）、@输入实时高亮 + 点击 chip 绑定字段、场景行＋镜头继承默认人物、＋场景、场景面板新建素材、场景字段写回。`runService` 替换假实现不碰网络/WS。
  - 全量 **64 用例全绿**（roundtrip 16 + workbenchEdit 13 + coreModules 9 + importer 5 + resolutionPresets 11 + workbenchRender 10）+ `npm run build` 通过（vue-tsc 模板类型检查）。
- **依赖**：新增 devDeps `@vue/test-utils` + `happy-dom`（组件渲染测试环境）。
- **同步**：双目录 rsync 一致。
- **验收**：打开/新建/导入工程进工作台 → 左树点场景名进「场景设置」（改默认人物/天气/加素材组，新镜头自动继承）→ 点镜头卡片改 Prompt 输入 `@林雪走进@霓虹街区` → 实时高亮 + 彩色 chip 点击绑定 → 场景行＋镜头 / ＋场景 / hover 删除即时生效 → 顶栏「保存项目」落盘。

### 61. V1.2.5 参考素材真实上传闭环（SPA 选图 → ComfyUI input → 相对路径引用 → 缩略图 → 复用）（任务 #180-185）

- **范围（用户拍板）**：只做「图片进入 ComfyUI input → 项目引用 → H3 可用」的闭环，明确**不扩大范围**（Prompt 分区编辑器推迟到 V1.3 之后）；做完马上进 V1.3 生成闭环。
- **链路**：SPA 选择图片 → 上传到 ComfyUI `/upload/image`（标准端点）→ 获得 ComfyUI 可用文件名（`overwrite=false` 同名自动 `_1/_2` 后缀，不覆盖旧图）→ Asset.imageFile / Scene.referenceImage / Shot refs 保存 **input 相对路径**（`minimax_studio/assets/<name>`）→ 工作台 `comfyInputUrl` 拆 filename/subfolder 走 `/view?type=input` 显示缩略图 → 重新打开项目仍显示（相对路径持久化）→ 生成时 Adapter 直接使用该路径（后端 `load_reference_tensor` 本来就从 `get_input_directory()` 相对读取，无需新后端路由）。
- **comfyApi.ts**（V1.2.5-A，#181）：
  - `uploadImage(file, opts)`：XHR POST `/upload/image`（fetch 没有上传进度事件，XHR 才带 `upload.onprogress`）；multipart `image/overwrite/type=input/subfolder`；`onProgress(loaded,total)` 进度回调、120s 超时、非 2xx/网络错误/缺 name 抛 `ComfyApiError`。
  - 新导出 `UploadImageOptions` / `UploadedImage` / `uploadedRelPath`（subfolder/name 拼相对路径）/ `comfyInputUrl`（相对路径 → `/view?filename=…&subfolder=…&type=input`，反斜杠规范化为 `/`）。
- **vite.config.ts**：dev 代理加 `/upload`（同 `/view` 一样 rewrite Origin，绕 ComfyUI origin_only_middleware 403）。
- **ImageUploadBox.vue**（V1.2.5-B，#182，新建可复用上传组件）：
  - 能力：点击上传、拖拽上传（dragover/drop）、缩略图预览（`comfyInputUrl`）、上传进度条、替换/删除、失败提示、文件校验（仅 image/*、≤20MB）、compact 模式（资产 chip / ref 列表 26px 小缩略图 + ×删除 + ＋添加）。
  - **最小接口 `ImageUploadApi`（只暴露 uploadImage）**：刻意不绑定 ComfyApiClient——它有 private `request` 成员导致结构类型 mock 不兼容（TS2741），解耦组件也方便测试。
  - 数据流：选择文件 → api.uploadImage → `uploadedRelPath` → `emit update:modelValue(relPath)`，父组件写回 Asset / Scene / Shot refs。
- **workbench.ts store**（V1.2.5-C，#183）：`addShotRefImage(shotId, relPath)`（append + index 从 0 起 + fileName=basename）+ `removeShotRefImage(shotId, index)`（splice + 后续 refs 重编号）；空路径/越界安全返回。
- **WorkbenchView.vue 三入口接入**（V1.2.5-D，#184）：场景设置「参考图」→ `patchScene referenceImage`；资产 chip 内嵌 compact 上传盒 → `updateAsset imageFile`；镜头「参考素材」重设计为 ref-chip 行（缩略图+文件名+🗑）+ 底部「＋ 添加参考图」上传盒 → `addShotRefImage / removeShotRefImage`。
- **测试**：
  - `frontend/tests/imageUpload.test.ts`（新增 **7 用例**）：`uploadedRelPath`/`comfyInputUrl` 路径工具 + FakeXHR mock 验证 uploadImage 的 multipart 字段（image/overwrite=false/type=input/subfolder）、进度回调透传、非 2xx/网络错误/缺 name 抛 `ComfyApiError`。
  - `frontend/tests/imageUploadBox.test.ts`（新增 **8 用例**，happy-dom 渲染冒烟）：空态/有图态、选图 → uploadImage(subfolder, overwrite:false) → emit relPath、失败提示 + emit error、非图/超 20MB 拒传、进度百分比显示、compact 模式、反斜杠路径规范化。
  - `frontend/tests/workbenchEdit.test.ts` 追加 **3 用例**：addShotRefImage 追加+index+basename、空路径/不存在镜头 null、removeShotRefImage 删除+重编号+越界安全。
  - 全量 **82 用例全绿**（此前 64 + 新增 18）+ `npm run build` 通过（vue-tsc 严格检查）。
- **同步**：双目录 rsync 一致；纯前端改动，刷新浏览器即生效（/upload 是 ComfyUI 标准端点，无需重启 Comfy Desktop）。
- **验收**：工作台场景设置/资产 chip/镜头参考素材三处上传图片 → 缩略图即时出现 → 刷新或重开项目缩略图仍在 → 项目文件里 Asset.imageFile / refs 存的是 `minimax_studio/assets/<文件名>` 相对路径 → 生成时该路径直接进 timeline 供后端读取。

### 62. V1.2.6 音频+视频参考素材上传闭环（用户提问「音频和视频没办法参考吗」→ 拍板加 V1.2.6，任务 #186-191）

- **背景**：后端本已支持视频/音频参考（`ref_video_0` → ReferenceToVideo 运动迁移 + 视频续接；`ref_audios` → L2VA 音频驱动视频），只缺前端 UI。V1.2.6 纯前端改造，**无新后端路由**。
- **comfyApi.ts**（V1.2.6-A，#187）：`uploadImage` 泛化为 **`uploadFile(file, opts)`**（`kind: "image"|"video"|"audio"`）——`kind=audio` → POST `/upload/audio`（multipart field `audio`）；`kind=image|video` → POST `/upload/image`（field `image`）；超时音频 120s / 其余 600s；错误文案按类型（上传图片/视频/音频）。`uploadImage` 保留为兼容入口（等价 `kind=image`）。新增 `UploadMediaOptions`（extends UploadImageOptions + kind）/ `UploadedMedia` / `comfyMediaUrl`（语义别名 = `comfyInputUrl`）。
- **ImageUploadBox.vue**（V1.2.6-B，#188，泛化 mediaType）：
  - 新增 `mediaType?: "image"|"video"|"audio"`（默认 image）；`MEDIA_LABEL`/`MEDIA_MAX_MB`（图 20/视频 500/音频 100）/`MEDIA_ACCEPT`（扩展名兜底 mp4/webm/mov/avi/mkv；wav/mp3/ogg/flac/m4a/aac/aiff）。
  - 接口改 **`MediaUploadApi { uploadFile }`**（原 `ImageUploadApi.uploadImage`），组件只依赖 `props.api.uploadFile(file, { kind, subfolder, overwrite:false, onProgress })`。
  - 预览：常规模式视频 `<video controls preload="metadata">`（96×56）、音频 `<audio controls>`（160×32）；compact 模式非图显示 🎬/🎵 图标。`mediaBroken` 图片失败回退 🖼。
- **workbench.ts store**（V1.2.6-C，#189）：`addShotRefVideo/removeShotRefVideo`（refs.refVideos，`videoFile`）+ `addShotRefAudio/removeShotRefAudio`（refs.refAudios，`audioFile`），语义与 refImages 一致（append + index 从 0 起 + fileName=basename + splice 重编号 + 空路径/越界安全）。
- **WorkbenchView.vue**（V1.2.6-D，#190）：镜头「参考素材」拆成三组 `.ref-group`——🖼 参考图 / 🎬 参考视频（`<video class="ref-video-thumb" muted preload="metadata">`）/ 🎵 参考音频（🎵 图标 chip）；每组各自挂 ImageUploadBox（`media-type="image|video|audio"`）+ 删除按钮；`@error` 媒体加载失败隐藏。
- **测试**（V1.2.6-E，#191）：
  - `imageUpload.test.ts` +**4 用例**：`uploadFile` kind=audio → `/upload/audio` field=audio；kind=video → `/upload/image` field=image；音频网络失败文案带「音频」+ uploadImage 兼容入口仍走 `/upload/image`；`comfyMediaUrl === comfyInputUrl`。
  - `imageUploadBox.test.ts` 改造 +**5 用例**（共 13）：既有用例全转 `api.uploadFile`；新增 mediaType=video 上传 kind=video + `<video controls>` 预览、mediaType=audio 上传 kind=audio + `<audio controls>` 预览、video 拒图/超 500MB（「只支持视频文件」「视频超过 500MB」）、audio 拒视频/超 100MB（「只支持音频文件」「音频超过 100MB」）、compact 模式 🎬/🎵 图标。
  - `workbenchEdit.test.ts` +**7 用例**（共 23）：refVideos/refAudios 追加+index+basename、空路径/不存在镜头 null、删除+重编号+越界、三类 refs 互不干扰。
  - 全量 **98 用例全绿**（此前 82 + 16）+ `npm run build` 通过（vue-tsc 严格检查）。
- **同步**：双目录 rsync 一致；纯前端改动，刷新浏览器即生效（/upload/audio 是 ComfyUI 标准端点，无需重启 Comfy Desktop）。
- **验收**：镜头「参考素材」下 🎬 参考视频/🎵 参考音频各传一个文件 → 视频出 96×56 播放预览、音频出播放条 → 重开项目仍在 → 项目文件里 refs.refVideos[].videoFile / refs.refAudios[].audioFile 存 `minimax_studio/assets/<文件名>` 相对路径 → 生成时进入 timeline（r2v 段 ref_video_0 / ref_audios 直接可用）。
- **下一步**：V1.3 生成闭环（镜头一键生成 → Adapter 组装 timeline_data → 提交 /prompt → 进度/状态灯 → 成片回看），V1.2.5 遗留的 Prompt 分区编辑器也排进 V1.3 之后。

### 63. V1.2.7 Prompt 素材引用标签 + @ 自动补全（用户需求「输入 @ 弹出可选择的素材 + 可以用 <picture> 等调用」，任务 #192-197）

- **背景**：V1.2.5/1.2.6 已能把图片/视频/音频传进 ComfyUI input 并挂到镜头 refs，但用户要在**提示词输入框里**直接引用素材——输入 `@` 弹出候选、插入 `<picture>素材名</picture>` 等标签，且标签引用的素材要真正被当作参考媒体进视频生成（「识别这个图片相当于参考图生视频」）。AskUserQuestion 拍板：语法 `<picture>` + 小图标、**音频和视频也支持**（`<video>`/`<audio>`）、候选范围=「场景+全局按类型分组（推荐）」（🎬/🌐 来源标记）。
- **设计**：标签是**纯文本语法**（随 Prompt 持久化，不新增独立数据字段）。后端 gen_timeline 解析 `<picture>/<video>/<audio>` → **剥壳保留名字**（`<picture>林雪</picture>` → `林雪`，避免自定义语法干扰 H3，名字文字仍参与资产命名自动匹配与模型理解）→ 匹配素材注入。音频/视频标签引用段 refAudios/refVideos（天然已注入，executor 的 `reinforce_r2v_prompt` 会生成官方 `<Audio N>`/`<Video N>` 标签）；图片标签匹配资产走资产注入管线（复用 `<Picture N>` 说明）。
- **promptMediaTags.ts**（V1.2.7-A，#193，前端媒体标签工具）：
  - `parseMediaTags(text)`：正则 `<picture|video|audio>名</...>` → `MediaTagRef[]`（kind/name/start/end/innerStart/innerEnd），开闭不匹配/空名忽略。
  - `findAtTriggerStart(text, caret)`：从光标往前找无空白间隔的 `@` 起点（无则 -1）。
  - `insertMediaTag(text, caret, kind, name)`：光标前紧邻 `@` → 把 `@` 及已输入部分整体替换成标签（不残留 `@`）；否则纯插入；返回新文本+新光标。
  - `buildMediaTagCandidates(index, shot, scene)`：全局 cast/locations + 场景四类资产 → picture 候选（source=🎬scene/🌐global）；镜头 refImages/refVideos/refAudios → picture/video/audio 候选（source=ref，kindLabel=参考图/参考视频/参考音频）；缩略图复用 comfyInputUrl 同构逻辑（本地 comfyView 避免循环依赖）。
  - `filterCandidates(candidates, query)`：子串包含、大小写不敏感。
- **WorkbenchView.vue**（V1.2.7-B/C，#194/#195）：
  - **@ 自动补全**：textarea `@input` 时 `findAtTriggerStart` 探测 → 打开下拉（`buildMediaTagCandidates` + `filterCandidates`），按类型分组（人物/地点/道具/风格/参考图/参考视频/参考音频），每项=缩略图（无图 🖼/🎬/🎵 占位）+ 名字 + 🎬场景/🌐全局/参考 来源标记；↑↓ 移动 / Enter/Tab 插入 / Esc 关闭；`@mousedown.prevent` 点击选中；选中后 `insertMediaTag` 写回 store + 恢复焦点与光标。切换镜头/场景 watch 关闭补全。
  - **标签 chip 渲染**：`promptHighlightHtml` 双层渲染叠加——`parseMediaTags` 把 `<picture>` 等渲染成 `.media-chip`（🖼/🎬/🎵 + 名字，蓝/粉/绿），与既有 `@` mark 高亮共存，重叠区间跳过；补全下拉样式 `.tag-complete`/`.tag-item`/`.tag-src`。
- **plan.py**（V1.2.7-D，#196）：SegmentPlan 新增 `tag_assets: list[GlobalAsset]`——Prompt `<picture>` 标签匹配到的资产图（图片标签引用段 refs 无需新字段，refs 本已注入）。
- **gen_timeline.py**（V1.2.7-D，#196）：
  - 新增 `parse_media_tags` / `strip_media_tags` / `_match_asset_exact`（名字相等或相互包含，名长 ≥2）。
  - 段循环：`seg_prompt` 确定后立即剥壳（在资产命名匹配之前）；r2v 段在「场景素材组 + 全局资产」池（cast_pool + location_pool + 场景 props/styles）里按名字匹配 `<picture>` 标签 → 挂 `SegmentPlan.tag_assets`，与 cast_asset/location_asset/extra_assets 按 id 去重；匹配不到记 warning；非 r2v 段只剥壳不注入。
- **executor_core.py**（V1.2.7-D，#196）：r2v 段资产注入循环追加 `seg.tag_assets`——按 kind 转成 asset_items（角色资产图/道具资产图/风格参考图/场景资产图），复用 `_r2v_handoff_prompt` 的 `<Picture N>` 角色说明生成（保持角色脸型/道具外观/风格色彩/场景布局一致）。
- **测试**：
  - 前端 `promptMediaTags.test.ts` **12 用例**（parse 3 / findAt 2 / insert 3 / build 3 / filter 1，含缩略图 URL、🎬/🌐/ref 来源标记、无 refs 回退 global）→ 全量 **110 用例全绿**（98 + 12）+ `npm run build` 通过。
  - 后端 `director/tests/test_gen_media_tags.py` **8 用例**（parse 剥壳保留名字、标签解析位置、开闭不匹配忽略、_match_asset_exact 相等/包含/单字拒绝/池优先）——用 `ComfyUI_MiniMaxH3_Director.director.gen_timeline` 包路径导入 + torch/comfy 桩；`test_project_store.py` 8 用例回归通过。
- **同步**：双目录 rsync 一致（frontend/src + director/plan.py + gen_timeline.py + executor_core.py + director/tests）。**后端改动需重启 Comfy Desktop 生效**（纯前端部分刷新浏览器即可）。
- **验收**：提示词框输入 `@` → 弹出按类型分组的素材候选（🎬/🌐/参考 标记 + 缩略图）→ ↑↓+Enter 或点击插入 `<picture>林雪</picture>` → 高亮层出 chip → r2v 镜头生成时该资产图作为参考图注入（资产注入管线 → `<Picture N>` 说明）→ 视频主体保持一致；`<video>动作.mp4</video>`/`<audio>对白.wav</audio>` 对应段 ref 视频/音频注入（`<Video 1>`/`<Audio 1>`）。
- **下一步**：V1.3 生成闭环（镜头一键生成 → Adapter 组装 timeline_data → 提交 /prompt → 进度/状态灯 → 成片回看），V1.2.5 遗留的 Prompt 分区编辑器排 V1.3 之后。

### 64. V1.2.7 修复：@ 补全候选为空不弹出 + 场景参考图进候选（用户实测反馈「SPA 输入 @ 没反应，期望看到带缩略图的候选」）

- **背景**：用户实测反馈——Comfy Desktop（旧节点 UI）里 @ 素材引用可用，但新 SPA 里输入 `@` 「实现不了」：期望弹出候选、候选里能看到缩放的小图（缩略图），实际什么都没显示。
- **根因 ①（候选为空 → 下拉直接关闭）**：`openCompletion` 里 `if (!filtered.length) { closeCompletion(); return; }`——候选为空时下拉直接关闭，用户输入 `@` 没有任何视觉反馈，误以为功能没实现。修复：`@` 触发总是打开下拉，候选为空时显示「无匹配素材」提示（模板已有 `.tag-empty`）。
- **根因 ②（场景参考图不在候选）**：`buildMediaTagCandidates` 只取「全局/场景资产四类 + 镜头 refs」，**漏了场景设置面板上传的 `scene.referenceImage`**——用户把参考图传在场景上（最常用入口），`@` 候选里却没有它。修复：`buildMediaTagCandidates` 增加场景参考图候选（kind=picture、source=🎬scene、kindLabel=「场景参考图」、缩略图 `comfyView(scene.referenceImage)`）。
- **顺手加固**：`onPromptKeydown` 候选为空时 guard（避免 ↑↓ 对 `len=0` 取模得 NaN），仅 Esc 关闭。
- **测试**：`promptMediaTags.test.ts` 新增「场景参考图进候选」用例（kind/source/缩略图 URL）→ 全量 **111 用例全绿** + `npm run build` 通过；三个改动文件双目录 cmp 一致。
- **生效**：纯前端改动，刷新浏览器即生效（dev server 下 Vite HMR 自动热更新）。

### 65. V1.2.7 修复：Prompt 框内媒体标签 chip 显示真正的小缩略图（用户澄清「要的不是带缩略图的候选，是要在 Prompt 框里显示小图标」）

- **背景**：上一条 #64 修的是「@ 候选下拉」——用户澄清理解错了：他要的不是候选下拉里带缩略图，而是 **Prompt 框里**（提示词高亮层）把 `<picture>` 标签渲染成**真正的缩略图小图标**（上传素材的图片），而不是当时的 emoji 占位。
- **改动（WorkbenchView.vue，纯前端）**：
  - `promptHighlightHtml` 渲染 chip 时按 `kind::name` 从 `mediaChipMeta`（基于 `allTagCandidates()` 的标签名→候选映射）查缩略图：命中且有 `imgUrl` → `<img class="media-chip-thumb">`（18px 圆角缩略图，走 `/view?type=input`）；无图/未匹配 → `<span class="media-chip-icon">` emoji 占位。
  - `.prompt-layer` 挂 `@error.capture` → `onChipImgError`：缩略图加载失败（文件被删/路径失效）时 `replaceWith` 成对应类型 emoji（🖼/🎬/🎵），避免破图。
  - 样式：`.media-chip` 改 `inline-flex` 横向居中，`.media-chip-thumb` 18×18 圆角 cover，`.media-chip-icon` 单行小图标。
- **测试**：`workbenchRender.test.ts` 新增「媒体标签 chip 渲染缩略图小图标」用例（有图资产 → `media-chip-thumb` + filename=linxue.png；无图/未匹配资产 → `media-chip-icon` 且无 thumb）→ 全量 **112 用例全绿** + `npm run build` 通过；3 个改动文件双目录 cmp 一致。
- **生效**：纯前端改动，刷新浏览器即生效。

### 66. V1.2.7 修复：支持官方编号标签 `<Picture N>`/`<Video N>`/`<Audio N>` 渲染缩略图（用户参照导演节点：只输入 `<Picture 1>` 就变图标，@ 也可用）

- **背景**：#65 修完自定义名字标签 `<picture>林雪</picture>` 在 Prompt 框渲染缩略图后，用户仍不满意——「你看看导演节点里是怎么实现的：只输入了 `<Picture 1>` 就变成小图标了，并且 @ 也可以实现」。即 SPA 高亮层漏了 **H3 官方编号标签**（`<Picture N>` 1-based，对应段 refs 的 index）。
- **改动（纯前端，两个文件）**：
  - `src/core/promptMediaTags.ts`：新增 `parsePromptTokens(text)` + `PromptToken` 接口 + `PROMPT_TOKEN_RE`（`/<(picture|video|audio)\s+(\d+)>|<(picture|video|audio)>([^<>]+)<\/(picture|video|audio)>/giu`），**两种语法并存**统一解析：官方编号（`numbered:true, num`，label=`Picture 1`）与自定义名字（`numbered:false, name`）；`parseMediaTags` 保留仍导出（仅测试用）。
  - `src/workbench/WorkbenchView.vue`：
    - `promptHighlightHtml` 改用 `parsePromptTokens`：编号 token → `numberedThumbUrl(kind,num)` 取 `refs.refImages[num-1].imageFile`（视频/音频同理取 refVideos/refAudios）→ `/view?type=input` 缩略图；名字 token → `mediaChipMeta.get(\`${kind}::${name}\`)?.imgUrl`；label 显示 `Picture 1` 或名字；chip 带 `mc-picture/mc-video/mc-audio` 配色 + 缩略图 `<img class="media-chip-thumb">`，加载失败 `@error.capture → onChipImgError` 换 emoji。
    - 删除不再使用的 `parseMediaTags` import。
- **测试**：`promptMediaTags.test.ts` 新增 **4 用例**（编号标签大小写不敏感/自定义名字/混合/空名忽略）→ 全量 **116 用例全绿** + `npm run build` 通过；3 个改动文件双目录 cmp 一致。
- **生效**：纯前端改动，刷新浏览器即生效。@ 补全仍插入 `<picture>名字</picture>`，但手输官方 `<Picture 1>` 同样变缩略图。

### 67. V1.2.7 修复：Prompt 输入框文字不透明盖住高亮层（用户实测「还是纯文本没变化」的根因）+ 编号标签支持无空格写法

- **诊断**：用户多次反馈「还是不行」，浏览器 Console 注入诊断脚本拿到 4 行关键信息：① `.prompt-layer.innerHTML` 里 **已经有 chip**（`media-chip mc-picture` + 缩略图 `<img>`），说明 #63-#66 的渲染逻辑**一直在工作**；② textarea 的 `color` 是 `rgb(232,232,238)`（**不透明**）、`background` 是 `rgb(24,24,36)`（**不透明**）——高亮层在 textarea 下面，但 textarea 文字不透明把它**完全盖住**，所以用户只能看到纯文本。
- **根因**：`.prompt-input { color: transparent; background: transparent; }` 这条样式规则没有真正压过 `.prompt-layer, .prompt-input` 共享规则里的 `color`/`background`（文本叠加层方案依赖 textarea 透明才能露出下层高亮）。另外用户手输 `<picture1>`（**无空格**）也不被 `\s+` 正则识别。
- **改动（纯前端，两个文件）**：
  - `WorkbenchView.vue`：textarea 加**内联样式** `color: transparent; background: transparent; caret-color: #e8e8ee; -webkit-text-fill-color: transparent;`（内联最高优先级，无条件透明）；`.prompt-input` 样式补 `!important` + `-webkit-text-fill-color: transparent !important` 双保险。
  - `promptMediaTags.ts`：`PROMPT_TOKEN_RE` 编号分支 `\s+` → `\s*`，`<Picture 1>` 与 `<picture1>`（无空格手输习惯）都能识别。
- **测试**：`promptMediaTags.test.ts` 新增「无空格编号 `<Picture1>` 也识别」+ `workbenchRender.test.ts` 新增「官方编号 `<Picture 1>` 在 Prompt 框渲染 chip」→ 全量 **118 用例全绿** + `npm run build` 通过；4 个改动文件双目录 cmp 一致。
- **生效**：纯前端改动，刷新浏览器即生效（dev server 需重启加载新样式）。

### 68. V1.2.7 修复：Prompt 媒体 chip 卡片过大撑破文本框 + 光标位置不对（chip 宽度改变换行 → 与 textarea 错位）

- **背景**：#67 透明叠层修好后，用户反馈「卡片太大了文本框都放不下，以及光标位置不对」。**根因**：#65/#66 的 chip 是 `display:inline-flex` + 18px 缩略图 + 额外 label 文本（文件名/`Picture 1`），渲染宽度**大于** textarea 里原始标签文本 → 高亮层换行位置与 textarea 不一致 → 光标错位 + chip 撑出文本框。
- **改动（WorkbenchView.vue，纯前端）**：
  - chip 改「**透明文本占位**」方案：`.media-chip` 改 `display:inline; position:relative`，内部 `.media-chip-text` 放**原文完整标签文本**（`<picture>林雪</picture>` 或 `<Picture 1>`）且 `color:transparent` → 高亮层渲染宽度与 textarea 完全一致，换行/光标严格对齐；缩略图 `.media-chip-thumb`/图标 `.media-chip-icon` 改**绝对定位覆盖**（`left:0; top:50%; transform:translateY(-50%)`，13px 小图）不占布局宽度。
  - @ 命中 mark 改回只渲染原文文字（高亮背景，不改变宽度）。
  - `.prompt-input` 加 `scrollbar-width:none` + `::-webkit-scrollbar{display:none}`（隐藏滚动条，防 textarea/高亮层滚动条宽度差导致错位）。
- **测试**：`workbenchRender.test.ts` 新增「媒体 chip 保留原文占位（透明文本）→ 换行与 textarea 一致、光标对齐」用例（断言 `.media-chip-text` 内含 `&lt;picture&gt;林雪&lt;/picture&gt;` 原文）→ 全量 **119 用例全绿** + `npm run build` 通过；2 个改动文件双目录 cmp 一致。
- **生效**：纯前端改动，dev server 重启 + 浏览器硬刷新（Ctrl+Shift+R）加载新样式。

### 69. V1.2.7 修复：媒体 chip 样式从未生效的真根因（`<style scoped>` 不作用于 `v-html` 内容）——缩略图一直以原始大图显示

- **背景**：#68 透明文本占位方案改完后用户反馈「还是非常大的图标」。排查发现**根因根本不是 chip 宽度**：#68 的 `.media-chip { display:inline }`、`.media-chip-thumb { width:13px }`、`.media-chip-text { color:transparent }` 以及更早 #65/#66 的 mark 高亮、18px 缩略图样式**在浏览器里从未生效过**。
- **根因**：这些 DOM 都是 `promptHighlightHtml` 生成的 HTML 字符串，经 `.prompt-layer` 的 `v-html` 注入。Vue `<style scoped>` 编译会给**模板中的元素**加 `data-v-hash` 属性，选择器改写为 `.selector[data-v-hash]`；但 `v-html` 注入的内容**没有 data 属性** → `.media-chip-thumb` 等规则全部失配 → `<img>` 按**原始图片尺寸**渲染（成百上千像素的巨图），`<picture>…</picture>` 原文标签文字也正常显示 → 用户看到"非常大的图标"。
- **改动（WorkbenchView.vue，纯前端 CSS）**：所有针对 v-html 内容的规则加 `:deep()` 前缀，编译为 `.prompt-layer[data-v-hash] .media-chip` 等（`.prompt-layer` 是模板元素带 data-v，其后代 v-html 内容可匹配）：
  - `.prompt-layer mark` → `.prompt-layer :deep(mark)`；mark 高亮色 `.m-cast` 等 → `.prompt-layer :deep(mark.m-cast)` 等（**不**改 `.mention-chip.m-cast`，mention-chip 是模板元素 scoped 正常）
  - `.media-chip` 全套（display:inline/背景配色）→ `.prompt-layer :deep(.media-chip)`
  - `.media-chip-text`（透明占位）→ `.prompt-layer :deep(.media-chip-text)`
  - `.media-chip-thumb`（13px 绝对定位）→ `.prompt-layer :deep(.media-chip-thumb)`
  - `.media-chip-icon`（11px emoji）→ `.prompt-layer :deep(.media-chip-icon)`
- **验证**：`npm run build` 后 grep dist CSS 确认编译产物为 `.prompt-layer[data-v-39493724] .media-chip-thumb`（带 data-v 前缀）——规则现在真正匹配 v-html 内容。全量 **119 用例全绿** + build 通过；1 个改动文件双目录 cmp 一致。
- **生效**：纯前端改动，dev server 重启 + 浏览器硬刷新（Ctrl+Shift+R）。**注意：CSS scoped 是 Vue SFC 的隐藏坑——凡 `v-html` 注入内容的样式必须用 `:deep()`，否则看起来"改了但没效果"。**

### 70. V1.2.8 Prompt 框放大编辑弹窗 + 媒体/@ chip 改导演台形式（用户需求「小图片看不见」→ 弹窗放大 + chip 缩略图+文字）

- **背景**：用户提三点：①Prompt 输入框要一个便捷方式**放到屏幕中间并单独放大**（否则小缩略图看不清）；②小图片改成**和导演台一样的形态**——前面缩放图片、后面跟 `Picture 1` 等文字；③`@` 命中资产也改成同样形态（不然 `@名字` 太长）。
- **改动（WorkbenchView.vue + workbenchRender.test.ts，纯前端）**：
  - **放大弹窗**：`.fld-head` 加 `⛶ 放大` 按钮（`.prompt-zoom-btn`）→ `openPromptModal()` 打开 `<Teleport to="body">` 的 `.prompt-modal-mask`（遮罩点空白关闭）+ `.prompt-modal`（`min(94vw,920px)`、88vh、居中）。弹窗内**复用同一套** `.prompt-box`/`.prompt-layer`/`.prompt-input`/`.tag-complete`/`.mention-bar` → 高亮渲染、`@` 补全、Esc/方向键、滚动同步全部同卡片行为一致；`pickCandidate` 自动切换到弹窗 textarea（`promptModalInputRef`）。Esc 先关补全再关弹窗；切镜头/场景自动关弹窗。
  - **chip 导演台化**：`@` 命中（`mc-asset m-cast` 等）和媒体标签（`mc-picture/video/audio`）统一渲染为 `<span class="media-chip">缩略图 + <span class="media-chip-label">文字</span></span>`——有图用 15px 缩略图、无图用类型 emoji 占位；label 保留原文（`@林雪` 显示 `@林雪`，`<Picture 1>` 显示 `Picture 1`）。与导演节点 `minimax_prompt_mentions.js` `.bd-phl-token`（inline + nowrap + 底色描边）一致。
  - **光标/文本对齐修正**（用户提醒「别忘了光标和文本保持一致」）：透明 textarea 与高亮层必须**完全同字体度量**。缩略图 `vertical-align` 从 `-3px` 改 `-2px`、基础 `line-height` 从 1.5 提到 **1.6**，保证 15px 缩略图在行盒内不撑高行高——否则含 chip 的行会让高亮层与 textarea 逐行错位、光标漂移；弹窗内放大（15px 字 + 20px 缩略图，line-height 1.6）同样经几何验证不撑高。
- **测试坑（Teleport）**：弹窗内容经 `<Teleport to="body">` **不在组件根 DOM 内**，`wrapper.find(".prompt-modal")` 永远找不到 → 必须 `document.body.querySelector`。且 Teleport 锚点不会随组件根卸载清理，跨用例污染会报 `insertBefore null` → 加 `enableAutoUnmount(afterEach)`（@vue/test-utils 官方 API）+ afterEach 清 `document.body.innerHTML`。新增「Prompt 放大弹窗」用例（开/输入同步写回/chip 渲染/关）覆盖。
- **验证**：全量 **120 用例全绿**（119→120）+ `npm run build` 通过；2 个改动文件双目录 cmp 一致。
- **生效**：dev server 重启 + 浏览器硬刷新（Ctrl+Shift+R）。

### 71. V1.2.9 Prompt 框@补全四项修正（用户需求「@选参考默认 picture 不显示文件名 + 光标位置还是不对 + 放大弹窗内下拉应在当前界面」）

- **背景**：V1.2.8 放大弹窗上线后用户提四点：①`@` 选**参考素材**要**默认插 picture**（不是按文件名）；②插入后**不要显示文件名**（如 `a.png`），应插**官方编号标签** `<Picture N>`；③**光标位置还是不对**——之前 #68 的「透明文本占位」方案方向对但实现有漏：`.media-chip-text` 只占了一部分（`.media-chip` 是 inline 内部 text 在换行时宽度未与 textarea 原文严格一致）；④**放大弹窗里 `@` 之后出现的选项要在当前界面显示**——之前卡片 + 弹窗两份 `.tag-complete` 都用 `v-if="completeOpen"` 判断，弹窗里输入时**两个下拉同时亮**，卡片那个显示在当前界面外。
- **改动（前端，纯 SPA：WorkbenchView.vue + promptMediaTags.ts + 两个测试文件）**：
  - **①@ 选参考默认 picture + ②不显示文件名**：`promptMediaTags.ts` 新增 `insertNumberedMediaTag(text, caret, kind, num)`（插 `<Picture N>`/`<Video N>`/`<Audio N>`，N=refs 数组下标+1）；`MediaTagCandidate` 加 `refIndex?`（ref 源候选带段 refs 数组 0-based 下标）；`buildMediaTagCandidates` 给 ref 候选填 `refIndex`；`WorkbenchView.pickCandidate` 分支——`source==="ref" && refIndex!=null` 走 `insertNumberedMediaTag`，否则（资产/场景参考图）仍走 `insertMediaTag`（自定义名字标签）。
  - **③光标对齐彻底修**：chip 改「**ghost 占位 + visual 覆盖**」双 span——`.media-chip-ghost` 放**原始标签完整文本**（`&lt;picture&gt;林雪&lt;/picture&gt;` 或 `<Picture 2>`）且 `color:transparent; background:transparent; padding:0; border:none` → **布局宽度与 textarea 原文完全一致**，换行点/光标严格对齐；`.media-chip-visual` `position:absolute; left:0; top:50%; transform:translateY(-50%)` 绝对定位覆盖（缩略图 13px + label），**不占布局宽度**；`chipHtml()` 同时输出两者。
  - **④弹窗下拉在当前界面**：新增 `completionTarget = ref<"card"|"modal">` 状态——`openPromptModal()` 置 `"modal"`，`onPromptInput` 按 `e.target === promptModalInputRef.value` 置归属；卡片下拉 `v-if="completeOpen && completionTarget === 'card'"`、弹窗下拉 `v-if="completeOpen && completionTarget === 'modal'"` → 永远只亮一个。`.prompt-modal` 从 `overflow:hidden` 改 `overflow:visible` 保证下拉不被裁剪在当前界面内。
- **测试**：`promptMediaTags.test.ts` 新增 `insertNumberedMediaTag` 三用例（@ 前缀替换/纯插入/视频音频）+ ref 候选 `refIndex` 用例；`workbenchRender.test.ts` 修正/新增——「放大弹窗内 @ 输入：下拉只在弹窗显示」（`.tag-complete` 实际在 `.wb-right .prompt-box` 里**不在 `.shot-card`**，卡片下拉用 `wrapper.find(".wb-right .prompt-box .tag-complete")`、弹窗下拉用 `document.body.querySelector(".prompt-modal .tag-complete")`，补全归属由 completionTarget 分离）、「@ 选参考素材 → 插入官方编号标签」（断言 `groups.map(text)` 含「参考图」分组、`refItem` 按 `tail.png` 找、插入 `<Picture N>` N=添加前 refImages 数+1、不含文件名、高亮层渲染 chip）。全量 **126 用例全绿** + `npm run build` 通过；4 个改动文件双目录 cmp 一致。
- **生效**：纯前端改动，dev server 重启 + 浏览器硬刷新（Ctrl+Shift+R）。**注意测试选择器坑：`.tag-complete` 在 `.wb-right .prompt-box` 内，不在 `.shot-card` 里——`wrapper.find(".shot-card .tag-complete")` 永远 false 即使下拉已渲染。**

## 2026-08-09 修改记录（按时间线）

### 72. V1.3 生成闭环 + 任务中心（SPA：生成下拉 6 范围 + 任务中心 + 缓存感知/参数指纹）

- **背景**：MiniMax Studio 分镜工作台（Vue3+TS+Vitest）完成 V1.2 编辑后进入 V1.3「生成闭环」——把顶栏单一生成按钮升级为**六范围生成**、底栏升级为**任务中心**（运行中/已完成 + 阶段进度 + 预览帧 + 打开成片）、镜头卡片带**缓存状态灯 + 单镜下载**、参数变更后**状态灯提示需重生成**。验收：建 3 场景 × 2 镜 → 一键生成 → 进度实时 → 原地看片 → 下载 mp4。
- **改动（纯前端 SPA：`src/stores/workbench.ts` + `src/workbench/WorkbenchView.vue` + 新 `src/workbench/TaskCenter.vue` + 新 `src/workbench/shotFingerprint.ts` + `src/services/comfyApi.ts` + `src/fixtures.ts` + `tests/workbenchRender.test.ts`）**：
  - **① 生成下拉 6 范围（B）**：`GenScope = "all"|"current"|"selected"|"scene"|"pending"|"failed"`；`resolveTargetShotIds()` 把范围解析成镜头 id 集合（`all`→null=全部；`current`→当前镜；`selected`→`selectedShotIds` 多选；`scene`→当前场景；`pending`→`segStatus.cached` 未包含；`failed`→`segStatus.states`=="failed"）；主按钮显示 `▶ 生成 {范围}（{计数}）`，范围内空集禁用；镜头卡片加 checkbox 多选 + `.shot-card.sel` 高亮 + 状态灯 `.shot-status`（st-none/running/failed/review/done + 呼吸动画）。
  - **② 任务中心（C）**：store 加 `tasks: TaskRecord[]` + `currentTaskId` + `addTask/patchTask/clearTasks`；`handleGenerate` 每次提交建一条任务（scopeLabel/shotCount/shotOrder 固化拍平顺序/targetShotIds），`onProgress`→进度快照、`onPreview`→预览帧、`onVideo`→finalVideo、`onFinish`→done/failed+promptId+时间；新组件 `TaskCenter.vue` 替换原 footer 极简进度条——摘要行（运行中 ⚡范围+阶段+镜头 x/y+进度条 / 空闲 任务统计+最近错误）+ 可展开面板（当前预览帧 + 最近任务列表：状态点/时间/范围/镜头数/prompt id/错误/「▶ 打开成片」喂中央播放器）。`loadProject` 保留会话级任务历史，`clearProject` 清空。
  - **③ 单镜下载 mp4（C）**：`comfyApi.segmentMp4(nodeId, index, fps)`（GET `segment_mp4` 附件流，Content-Disposition 解析 filename，无缓存 404）；镜头卡片每镜加 `⬇` 下载按钮（`.shot-dl`），`isShotCached(shotId)` 有缓存才可点，`downloadShotMp4` 用 `URL.createObjectURL + a.download` 触发保存。
  - **④ 缓存感知 + 参数指纹（D）**：提交前 `refreshSegStatus()` 拉后端 `segment_status`（pending/failed 范围数据源）；store 加 `paramHashes: Record<shotId, hash>` + `recordParamHashes`；新 `shotFingerprint.ts` 对镜头生成参数（prompt/负prompt/时长/cast/location/taskType/continuityMode/smartTail/stateChange/refs 三组/outputSize/frameRate）算稳定指纹；生成**成功**才 `recordParamHashes(pendingHashes)`（失败不留）；状态灯优先级 cached 且指纹不匹配 → **`.st-stale` 橙色虚线「参数已变，需重新生成」**（有缓存但参数动过不再算 up-to-date），无指纹记录的旧缓存仍显示 st-done 不误报。
  - **⑤ fixtures 深拷贝修隐患**：`loadSampleProject`/`loadSampleWorkflow` 返回 `JSON.parse(JSON.stringify(...))` 深拷贝，修复 V1.2 起「addScene/addShot 把镜头写进模块级样例 JSON → 后续用例断言 3 镜实际 4 镜」的 store 单例污染。
- **设计决策**：状态灯 6 态（含 stale）是**前端瞬态派生**，后端只报 cached/states——参数指纹放前端会话态（不落盘），切项目 `clearProject` 清空；「全部」范围用 `runSelection=null` 走后端全量，其余范围传显式 `targetShotIds`；任务历史只存摘要不存原始 timeline，防泄漏 + 轻量。
- **验证**：`npm test` **139 用例全绿**（新增 B 5 例 + C 5 例 + D 2 例：6 选项计数/当前镜 runSelection/多选选中/状态灯 cached/未完成排除失败范围/任务 running→done/失败任务/打开成片/下载按钮缓存开关/参数已变 stale/无指纹不误报）+ `npm run build` 通过（vue-tsc + vite，index.js 157 kB）；改动文件双目录 cmp 一致；后端 `segment_status`/`segment_mp4` 路由在 http_routes.py 已就绪无需改。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。**注意**：runService mock 现默认立即触发 `onFinish(ok)`（测试完整生命周期），真实后端回调由 executor 按进度触发，行为一致。

### 73. SPA 生成失败修复：Task 'auto' is not supported（SPA：directorCore + adapter）

- **背景**：V1.3 生成闭环用户实测报错 `ValueError: Task 'auto' is not supported on MiniMax H3 Director. Supported: fl2v, i2v, r2v, rv2v, t2v, v2v.`（executor_core.py `_run_one_segment` 校验）。已跑 227s 后失败。
- **根因链**：SPA `project.ts` 里 `ShotGeneration.taskType: "auto"|"r2v"|"fl2v"`（新镜头默认 `"auto"`，`workbench.ts:280`）→ `directorCore.ts` `toTimelineShot` 把 `gen.taskType`（= `"auto"`）`as TaskKey` 直接透传 → `minimaxH3Adapter.ts` `toSegment` 写 `taskType: TASK_LABELS[shot.taskKey] ?? shot.taskKey` → `"auto"` 不在 TASK_LABELS → 原样写进 timeline 段 → 后端 `gen_timeline.py` `resolve_task_key("auto")` 返回 `"auto"` → `SegmentPlan.task_key="auto"` → executor 抛错。
- **原生前端语义对照**：Director 节点自己的 UI 对 auto 的处理是 **不写段 taskType**（`minimax_image_batch.js` `if (sel.value === "auto") delete seg.taskType;`），让段继承全局 taskType（通常 r2v）；**auto 智能路由在 continuityMode 层面**（阶段 D：`continuityMode=="auto"` 时按强连续动作关键词把 r2v 路由成 fl2v）。SPA 的 continuityMode 已独立透传，只需把 taskType 的 auto 归一化掉。
- **改动（纯前端 SPA 2 文件 + 测试）**：
  - `src/core/directorCore.ts`：`taskKey: gen.taskType as TaskKey` → `taskKey: (gen.taskType === "auto" ? "" : gen.taskType) as TaskKey`（auto/空串→空串，表示「跟随全局」）。
  - `src/adapters/minimaxH3Adapter.ts` `toSegment`：`taskType: TASK_LABELS[...] ?? ...` → `...(shot.taskKey ? { taskType: TASK_LABELS[shot.taskKey] ?? shot.taskKey } : {})`（空串**不写 taskType 字段**，后端段级继承全局 r2v；显式 r2v/fl2v 仍写中文标签）。
  - `tests/roundtrip.test.ts`：新增「auto taskType 归一化」用例——第 1 镜改 auto 断言段**无 taskType 属性**、第 2 镜 fl2v 断言标签 `fl2v — 首尾帧生视频(First&Last to Video)`、第 3 镜 r2v 断言原标签。
- **验证**：全量 `npm test` **140 用例全绿**（139→140）+ `npm run build` 通过；3 文件双目录 cmp 一致。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。已生成的失败任务重跑即可（后端无需重启）。

### 74. SPA 镜头/场景/全片独立播放按钮 + 镜头名过长遮挡操作按钮（用户需求「每个镜头/场景/以及完整视频弄一个单独播放的按钮 + 镜头名字过长会导致后面的重命名等按钮点击不到」）

- **背景**：V1.3 生成闭环后，用户要在工作台直接回看单镜/场景/全片，而不必等「全片导出」；同时镜头名一长，卡片上的重命名/删除等操作按钮被顶到视口外点不到。
- **改动（纯前端 SPA：新 `src/workbench/playQueue.ts` + `WorkbenchView.vue` + 测试）**：
  - **① 三档播放入口**：顶部生成栏新增 `🎬 播放全片`（scope="all"，顺序播全部已缓存镜头）；场景标题栏新增 `▶`（scope="scene"，只播该场景已缓存镜头）；镜头卡片新增 `🎬`（scope="shot"，只播此镜，无缓存禁用）。播放前统一 `refreshSegStatus()` 拉最新缓存集。
  - **② 播放队列（惰性加载）**：新 `playQueue.ts` `collectPlayableShotIds()` 纯函数——按拍平顺序收集已缓存镜头 id（`cached` 为段索引集合，与 `flattenedShots` 同序），shot/scene/all 三种范围 + 同 id 去重 + 空集短路；`WorkbenchView.vue` 里 `playList/playIdx/playStatus` 三态 + `loadPlayIndex(i)` 用 `segment_mp4` blob 喂中央播放器 + `URL.revokeObjectURL` 释放旧 blob（防内存堆积），`onPlayerEnded` 自动播下一镜，播完/`clearPlayback` 清空。
  - **③ 生成时清理**：`handleGenerate` 开头 `clearPlayback(); clearFinalVideo();`（开始新生成时收掉旧播放占用）。
  - **④ 长名修复（CSS）**：`.shot-name` 改 `flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap`；`.scene-meta`/`.scene-ops`/`.shot-ops`/`.icon-btn`/`.shot-thumb`/`.shot-dur`/`.shot-gen-mini` 全部 `flex-shrink:0`；`.shot-card` 加 `min-width:0`。长镜头名显示省略号，操作按钮不再被挤出卡片。
  - **⑤ 测试**：新 `tests/playQueue.test.ts` 7 例（shot/scene/all 范围、去重、空输入、未缓存目标）；`tests/workbenchRender.test.ts` 场景行测试选择器改 `title === "新增镜头"`（场景标题栏新增播放按钮后 `.scene-name .icon-btn[0]` 顺序变化）。
- **设计决策**：场景/全片**不做后端新路由**——scene/movie 档的 mp4 落盘在 ComfyUI 输出目录但不经 SaveVideo WS 输出，无法复用 `onVideo`；纯前端把已缓存单镜 `segment_mp4` blob 顺序拼接播放，零后端改动、零内存合并、复用已编码缓存（首帧即播）。播放是**预览语义**：跳过未生成镜头（而不是报错）；无任何缓存时给「先在本节点生成」提示。
- **验证**：全量 `npm test` **147 用例全绿**（140→147）+ `npm run build` 通过（vue-tsc + vite，index.js 159 kB）；4 文件双目录 cmp 一致。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新（播放不经过后端，无需重启 Comfy Desktop）。

### 75. SPA Prompt 分区编辑器：画面 + 摄影/风格/声音/负面 折叠分区，语言自适应合并（用户拍板「Prompt 分区编辑器」+「折叠自由文本框」+「输入中文就中文合并，英文就英文合并」）

- **背景**：V1.3 生成闭环后，用户想让镜头提示词按 画面/摄影/风格/声音/负面 分区组织，而不是全塞进一个大 Prompt 框；生成时按输入语言原样拼接（不翻译、不套模板）。三个决策（AskUserQuestion 拍板）：①做 Prompt 分区编辑器；②输入形态 = 折叠自由文本框（画面框保持原样 @补全/chip/放大，下方加「📑 更多分区」折叠块）；③合并输出 = 中文内容用中文逗号拼接、英文内容用英文逗号拼接，输入什么语言就什么语言。
- **数据模型（`frontend/src/models/project.ts`）**：`Shot.content` 新增可选字段 `cameraText?/style?/soundText?`（画面 visual 与负面 negativePrompt 原有；旧 timeline 导入不受影响，新字段纯可选）。
- **核心纯函数（新 `frontend/src/core/promptSections.ts`）**：
  - `joinPromptParts(parts)`：跳过空分区；用 CJK 正则 `/[一-鿿぀-ヿ가-힯]/` 检测文本语言——中文内容用「，」、英文内容用 ", "（分隔符由**内容语言**决定，不是输入框语言）。
  - `shotCameraText` / `shotSoundText`：**自由文本优先**，cameraText/soundText 为空时回退结构化 `shot.camera`（景别/运镜/速度/景深）/ `shot.sound`（环境音/音乐）——旧结构化字段不浪费。
  - `buildShotPromptText(shot)`：按 画面→摄影→风格→声音 顺序合并；**只有 visual 的旧数据合并结果 == visual，向后兼容**；全空返回空串。
- **生成链路（`frontend/src/core/directorCore.ts`）**：`toTimelineShot` 的 `prompt` 从 `shot.content.visual` 改为 `buildShotPromptText(shot)`。后端 `seg.prompt` 本来就是最终正向 prompt（自由文本），无分区字段解析 → **分区是纯前端合并语义，后端零改动**。
- **store（`frontend/src/stores/workbench.ts`）**：新增 `updateShotSection(shotId, field, text)` 原子写回（field ∈ visual/negativePrompt/cameraText/style/soundText）。
- **参数指纹（`frontend/src/workbench/shotFingerprint.ts`）**：指纹 parts 加入 cameraText/style/soundText/negativePrompt → 改任意分区，已缓存镜头状态灯变 `st-stale`「参数已变需重生成」。
- **UI（`frontend/src/workbench/WorkbenchView.vue`）**：原独立「负面 Prompt」label 替换为折叠块「📑 更多分区」（摄影 · 风格 · 声音 · 负面），头部点击展开/收起；4 个自由 textarea（placeholder 给出中文示例如「景别/运镜/速度/景深，如：近景，缓慢推近」）；底部「生成用 Prompt」实时预览（`.ps-preview-text` 显示 buildShotPromptText 合并结果，虚线框内滚动）。画面 Prompt 框保持原样（@补全/chip/放大弹窗/高亮层全部不变）。CSS `.prompt-sections` 系列（头/正文/ps-preview，暗色卡片风格对齐现有 .fld）。
- **测试**：新 `tests/promptSections.test.ts` **11 例**（joinPromptParts 中文/英文/空分区；buildShotPromptText 只有 visual 向后兼容/中文全分区/英文全分区/部分空/自由文本 vs 结构化回退 camera/sound/全空）；`tests/workbenchRender.test.ts` 新增 **3 例**（默认折叠仅见头 / 展开 4 分区编辑写回 store + 合并预览实时中文逗号更新 / 负面 Prompt 迁入折叠区仍可编辑写回）。
- **验证**：全量 `npm test` **161 用例全绿**（147→161）+ `npm run build` 通过（vue-tsc + vite，index.js 161.71 kB）；8 文件双目录 cmp 一致。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。

### 76. 左侧分镜交互优化：下载按钮点击不到（用户反馈「左侧分镜交互做的不是很好，点击不到下载的按钮」）

- **根因（布局级）**：`.shot-card` 是 flex 行；`.shot-ops`（⇡⇣⧉✎🗑 5 个按钮）`display:none`，hover 卡片时才变 `display:flex` 插入 flex 流。在 220px 宽的 `.wb-left` 里，hover 后 5 按钮（≈124px）+ 常显的 checkbox/缩略图/时长/状态灯/播放🎬/生成▶/下载⬇ 总宽 ≈300px 超出容器 → **最右侧的下载按钮被 `overflow-y:auto` 的 `.wb-left` 横向裁剪，物理上点不到**。
- **修复（纯前端 WorkbenchView.vue）**：
  - **① `.shot-ops` 改绝对定位纵向浮层**：`position:absolute; top:calc(100%+2px); right:0; z-index:50; flex-direction:column`，hover 卡片（或 hover 浮层本身）在卡片下方弹出，**不再参与 flex 布局** → hover 时不再挤压任何常显按钮，播放🎬/生成▶/下载⬇ 位置恒定、下载按钮始终可见可点。
  - **② 左栏加宽 220→270px**：给常显按钮 + 镜头名更多空间，镜头名不再被挤成几像素。
  - **③ 浮层按钮加文字标签**：`⇡ 上移 / ⇣ 下移 / ⧉ 复制 / ✎ 重命名 / 🗑 删除`（原来是纯图标），点击目标变大、语义更清楚；最后一项「删除」hover 红色警示。
  - **④ `.shot-list` 底部 padding-bottom:160px**：避免最后一张卡片的浮层被 `.wb-left` 底部裁剪（边缘镜头浮层仍可完整弹出）。
- **测试**：`tests/workbenchRender.test.ts` +2 例（下载按钮是卡片直接子元素且独立于 `.shot-ops` 浮层 + 浮层按钮带文字标签；点「删除」触发 confirm stub 后镜头数 -1）。全量 **163 测试全绿**（161→163）+ build 通过（index.js 161.76 kB），2 文件双目录 cmp 一致。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。

### 77. V1.3.3 真实生成闭环硬化：WS 断线 /queue 兜底轮询 + 任务中心「重新生成」（用户拍板「直接进 V1.3 生成闭环，选 Shot→改 Prompt→点生成→进 ComfyUI→H3 出片→SPA 拿回→当前 Shot 播放」）

- **背景**：V1.3 生成闭环本体已在 #72 落地（生成下拉 6 范围 + 任务中心 + 缓存感知/参数指纹），#73 修掉 auto taskType。本轮用户拍板 V1.3.3「真实生成闭环重点一次打通」，验收标准唯一 = 从 SPA 选一个 Shot → 改 Prompt/参考图 → 点生成 → 真正进入 ComfyUI → MiniMax H3 出视频 → SPA 自动拿回 → 当前 Shot 直接播放。盘点后确认链路 90% 已就绪，真正缺口只有两处，本轮补齐 + 单测覆盖 + 双目录同步。
- **缺口 A：WS 断线/丢事件 → /queue + /history 兜底轮询（`frontend/src/services/directorRun.ts` + `WorkbenchView.vue`）**：
  - 之前 `execution_success/error` 完全依赖 WS 事件；WS 断开或单槽丢事件时 `onFinish` 永远不触发 → 任务永久 running、状态灯不刷新。
  - 现在 `run()` 提交成功后启动兜底定时器（`POLL_INTERVAL_MS=3000`，`MAX_POLL_TICKS=300` ≈ 15 分钟覆盖超长 H3 生成）：每 tick 查 `/history/{prompt_id}` —— `completed`→`settleRun(true)`、`status_str=error`→`settleRun(false)`；history 尚无 → 查 `/queue` 判 `running/queued/missing` 并回调 `onPoll`。
  - **`settleRun()` 是 WS 事件与兜底轮询共用的唯一出口**（`settled` 标志 + `stopFallbackPolling()`），防止 onFinish 重复触发（WS 正常时 execution_success 先到即停，轮询几乎零消耗）。
  - `execution_success/error` 的 promptId 现优先取事件值、缺省回退 `this.pendingPromptId`（防丢）。
  - **WorkbenchView**：`handleGenerate` 加 `onPoll` 回调，节流 5s（`lastSegPollAt`）刷新 `refreshSegStatus()` → WS 断线期间状态灯仍随段缓存新鲜。
- **缺口 B：任务中心「重新生成」（`TaskCenter.vue` + `WorkbenchView.vue`）**：任务条目新增 `↻ 重新生成` 按钮（有 `targetShotIds` 才显示，running 时 disabled），`emit("regen", t.targetShotIds)` → WorkbenchView `onTaskRegen` → `handleGenerate(targetShotIds)` 复用原任务目标镜头 id 重新提交。`.tc-btn:disabled` 样式。
- **测试**：新 `tests/directorRunFallback.test.ts` **5 例**（WS execution_success → onFinish 恰好一次 + settle 去重；WS 断开 → /history completed 兜底 → onFinish(true)；/history error → onFinish(false)；/queue 兜底 running/queued/missing 上报 onPoll；settle 后轮询停止不再上报）。全量 **168 测试全绿**（163→168）+ build 通过（index.js 163.41 kB），4 文件双目录 cmp 一致。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。真实 E2E（`npm run e2e`，ComfyUI 运行在 127.0.0.1:8188 时）走通「选 Shot→生成→H3 出片→SPA 回看」闭环。

## 2026-08-10 修改记录（按时间线）

### 78. H3 分辨率 32 对齐崩溃修复（验收步骤① blocker：fl2v 首帧路径奇数宽 latent）

- **背景**：V1.3 冻结后真实人工 UI 验收步骤①「单镜生成 Shot 03」后端秒败：
  `shape '[1, 24, 1, 1, 10, 2, 18, 2]' is invalid for input of size 17760`，
  发生在 `comfy/ldm/minimax/model.py:47 patchify_video`（Prompt executed in 9.74 秒）。
- **根因链**：
  - Shot 03 prompt 含「走向」→ 命中强连续动作关键词（`_STRONG_CONTINUITY_KEYWORDS`）
    → 阶段 D 把 `task_key=r2v` 自动路由成 `fl2v`（首尾帧硬锁）→ 走 `MiniMaxH3ImageToVideo`
    → 官方节点把 first_frame 精确 resize 到生成尺寸 592×320 → latent 宽 37（592/16）**奇数**。
  - H3 DiT patch_size=(1,2,2)：latent 宽高必须为偶数。主 latent 有 `pad_to_patch_size` 兜底，
    但 `cond_video_latents`（keyframe/ref，`model_base.py extra_conds`）**不做 padding**，
    `patchify_video` 对奇数维 floor reshape 直接崩。
  - 尺寸来源：前端 `resolutionPresets` 矩阵只对齐 16（768×432、1280×720…）+ 后端
    `resolve_output_dimensions` stride=16 → 产生 592×320 这种非 32 倍数尺寸。
  - 附带隐患：前端 JS `Math.round`（half-up）与 Python `round`（银行家舍入）对 592/32=18.5
    会算出不同对齐值（前端 608 vs 后端 576）→ UI 显示与实际生成尺寸脱节。
- **分层修复（5 文件 + 测试）**：
  - `frontend/src/workbench/resolutionPresets.ts`：新增 `H3_ALIGN=32` + `alignDim()`；
    **RESOLUTION_MATRIX 全量改 32 倍数**（16:9 高档 1920×1088、1280×736 等）；`resolutionForMp`/`scaleToFit` 末尾 `alignDim`。
  - `frontend/src/stores/workbench.ts`：`setOutputSize`/`clampDim` 对齐 32（手动输入宽高也落 32 网格）。
  - `frontend/tests/resolutionPresets.test.ts`：矩阵校验 `%8`→`%32`，2.1MP 断言 1920×1088，`findComboForSize(1920,1080)`→null，`scaleToFit(1280,720)`→1280×736。
  - `lib/image_prep.py`：`resolve_output_dimensions` stride 默认 16→32；`snap_dimension` 改 **half-up**
    （`(v + stride//2) // stride * stride`）与前端 `Math.round` 一致。
  - `nodes/conditioning.py`：`H3_ALIGN=32` + `_h3_align_dim()`，`run_minimax_conditioning` 入口
    对 width/height 统一对齐 —— **最终防线**：无论前端/其它调用传什么尺寸，进官方节点前必为 32 倍数。
- **验证**：⚠️ VM（bash）连续 6 次 "guest is not connected" wedged，**未能跑 `npm test`/`npm run build`/`py_compile`**；
  改动已用文件工具逐文件同步到部署目录（5 文件双目录一致）。VM 恢复后需补跑前端测试/构建 + 后端语法校验。
- **生效**：后端 2 文件（conditioning.py / image_prep.py）需**重启 Comfy Desktop**；前端 3 文件 dev server 重启 + 浏览器硬刷新。
- **待办**：VM 恢复后补验证；用户重启后重跑验收步骤①（Shot 03 含「走向」会再走 fl2v，正好验证 32 对齐）。

### 79. 镜头时长支持小数秒（用户实测反馈「时间不能选择小数，正常应该可以选择的」）

- **根因**：两处强制整数 —— ① `WorkbenchView.vue` 时长输入框 `step="1"`（HTML number 只允许整数步进）；
  ② store `clampDur` 里 `Math.round(v)` 把任何小数四舍五入成整数。
- **修复（纯前端 2 文件）**：
  - `WorkbenchView.vue`：`<input ... step="1">` → `step="0.1"`（0.1 秒粒度步进）。
  - `stores/workbench.ts` `clampDur`：`Math.round(v)` → `Math.round(v * 10) / 10`（保留 1 位小数，≥1s）。
- **链路无影响**：`deriveFrameCount` 本来就是 `Math.round(durationSec * fps)` 再 snap 17k+5 网格
  （如 3.5s×24fps=84 帧→snap 90 帧≈3.75s）；`fmtSec` 显示 `${sec}s` 原样支持小数。后端零改动。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。VM wedged 未跑测试/构建，恢复后补跑。

### 80. 生成任务「取消/中断」按钮（用户实测反馈「点击生成之后没有取消按钮」）

- **背景**：验收期 H3 单镜生成 3-6 分钟，任务中心只有「打开成片/重新生成」，无中途取消入口。
  `ComfyApiClient.interrupt()`（POST /interrupt）早已存在但前端从未暴露给用户。
- **修复（4 文件 + 测试）**：
  - `services/directorRun.ts`：新增 `DirectorRunService.interrupt()` —— ①若任务还在排队 `deletePending(promptId)` 移出队列；
    ②`api.interrupt()` 中断 ComfyUI 正在执行的任务；③主动 `settleRun(pid, false)` 触发 `onFinish(false)` 让任务中心立即收尾
    （随后 WS 抛的 `execution_error` 会被 `settled` 挡住，不重复回调）。注释注明 /interrupt 会中断当前所有 running 任务（单用户可接受）。
  - `workbench/TaskCenter.vue`：运行中且为当前任务（`t.status==='running' && t.id===wb.currentTaskId`）的任务行加「⏹ 取消」按钮
    （`@click.stop="cancel"` → emit "cancel"）；新增 `cancelled` 状态灰点 `.tc-cancelled`；`statusLabel` 支持「已取消」。
  - `workbench/WorkbenchView.vue`：新增 `onTaskCancel()`（`if (!wb.running) return; cancelledByUser=true; await wb.runService.interrupt()`）；
    模块级 `cancelledByUser` 标志在 `handleGenerate` 开头重置；`onFinish` 据此把任务标为 `status:"cancelled"` + error「已手动取消」
    （不弹「生成失败」错误）；顶部生成栏 `wb.running` 时显示独立的「⏹ 取消」按钮（`btn.danger`）。
  - `stores/workbench.ts`：`TaskRecord.status` 联合类型加 `"cancelled"`。
  - `tests/directorRunFallback.test.ts`：新增 interrupt 用例 —— deletePending+interrupt 调用、`onFinish(false)` 恰好一次、
    后续 WS execution_error 被 settled 挡住、轮询已停止。
- **验证**：`npm test` 全量 **169 passed | 1 skipped**（新增 1 例）；`npm run build` 通过（vue-tsc 严查未用参数 `t` 已修）；
  部署目录 5 文件 diff 一致。**#78 的补验证也已在本轮完成**：`py_compile` conditioning.py/image_prep.py 源+部署均 OK。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。取消后 H3 正在跑的帧被放弃，已生成的镜头缓存保留（状态灯绿不丢）。

### 81. 单镜生成却播整段全片（用户实测「我只点了生成此段，为什么是一整段视频」）

- **根因**：`WorkbenchView.handleGenerate` 固定 `exportMode:"all"`，后端 `executor_core.py`
  export_mode=all 分支对未选段用**缓存/源帧 passthrough 填充**，最终 SaveVideo 输出的是
  「目标段新内容 + 其余段旧缓存/源帧」拼成的**整段视频**；前端 `onVideo` 又直接把全片
  `setFinalVideo` → 播放器显示整片。用户只点「生成此镜」却看到一整段，且旧缓存段占大头
  → 观感像「旧提示词换了个种子」。
- **初版修法（不彻底，已弃）**：onVideo 单镜时拉 `segment_mp4` blob 播放，失败回退全片。
  实测用户仍看到整片——因该段磁盘缓存可能未落盘/索引错位导致 `segmentMp4` 失败走回退。
- **根治（纯前端 2 文件）**：
  - `WorkbenchView.vue` `handleGenerate`：`exportMode` 动态化——**单镜范围
    （`scopeIds.length===1`）→ `"segments"`**（后端只采样这一镜，SaveVideo 直接输出该镜
    单段视频，从源头杜绝拼接）；多镜/全部仍 `"all"`（目标段新 + 其余段缓存拼成整片预览）。
  - `WorkbenchView.vue` `onVideo`：去掉 segmentMp4/回退逻辑，直接 `setFinalVideo(video)`；
    单镜时 `playStatus` 显示「本镜回看：镜头名」，任务记录 finalVideo 同一单段。
  - `tests/workbenchRender.test.ts`：单镜用例改为断言——`structure.output.exportMode ===
    "segments"`、播放器直接播 onVideo 单段、`segmentMp4` 未被调用。
    坑：`wrapper.find(".shot-gen-mini")` 命中第一个「播放此镜」`shot-play`，须用
    `button.shot-gen-mini[title="只生成此镜"]`。
- **验证**：`npm test` 全量 **170 passed | 1 skipped**；`npm run build` 通过；部署目录 2 文件 diff 一致。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。
- **关联坑**：segments 模式只跑选中段——若该镜是 r2v 且依赖上一段尾帧、而上一段无缓存，
  可能取不到锚点（**已修复 #82**：内存前驱写回 + 续接锚点容忍 stale 缓存）；生成单镜后想看成片，
  点顶部「生成全部/场景」即可。

---

### #82 单镜重生成 r2v/fl2v 段「段间连贯」报错根治（2026-08-10）

- **用户事故**：「生成单个镜头之后没有办法再生成一遍」——单镜生成 r2v 段 #3 时报
  `ValueError: 段间连贯：片段 #3 需要上一段 #2 的生成结果`，且日志有
  `Segment 2 cache stale (timeline changed)`。
- **根因（三重叠加）**：
  1. `executor_core.py` run 循环**已跑段不写回 `completed_outputs`**（对比未选段会写
     `completed_outputs[seg.index] = cached/fill`）。导致下一段 `_run_one_segment` 里
     `resolve_prev_segment_output`（r2v_handoff/fl2v_handoff 取上段尾帧）查内存 `completed`
     找不到前驱，只能落回磁盘缓存。
  2. **缓存指纹含全局字段**（`output_mode`/`continuity`/`continuity_overlap`/
     `continuity_pipeline`/`width`/`height` 等）——单镜生成 r2v 段时前端对非 fl2v 清零
     `continuityEnabled`，与之前「全片/续接开」生成时落盘的 #2 指纹不同 →
     `load_segment_cache` 判 stale → 返回 None。
  3. segments 模式只跑选中段 #3，#2 不在 run_indices，既无内存也无有效缓存。
- **修复**：
  - `director/executor_core.py`：run 循环已跑段 `chunk = _coerce_chunk(chunk)` 后
    `completed_outputs[seg.index] = chunk`——同一次运行内，前驱段的输出内存中即可取尾帧。
  - `director/segment_cache.py`：`load_segment_cache` 加 `allow_stale=False` 参数；
    stale 且 `allow_stale=True` 时打 warning 仍返回帧（仅校验 4 维 + 空间尺寸>0，
    参考帧下游做 long-edge 缩放，尺寸差可容忍）。
  - `director/segment_continuity.py`：`resolve_prev_segment_output` 加载前驱缓存改为
    `load_segment_cache(..., allow_stale=True)`——续接锚点只取尾帧/首帧做视觉参考，
    全局字段变化不影响锚点可用性。
  - 合并/透传路径（executor `load_segment_cache(node_id, seg, plan)`）**保持严格**，
    只有续接取锚点走容错。
- **验证**：新增 `director/tests/test_segment_cache_stale.py`（7 用例：roundtrip 匹配 /
  默认拒 stale / 续接容 stale / 4 维校验 / 缺失返回 None / resolve 用 stale 尾帧 /
  无缓存仍报段间连贯）。全量后端测试 **8+8+7 = 23 passed** + `py_compile` 通过。
  双目录已同步。**需重启 Comfy Desktop 生效**。
- **已知边界**：前驱段**从未生成且无任何缓存**（不是 stale 而是缺失）→ 仍报「段间连贯」
  （行为不变，错误信息已足够明确）；此时应先生成前驱段或「生成全部/场景」。
- **涉及**：`director/executor_core.py`、`director/segment_cache.py`、
  `director/segment_continuity.py`、`director/tests/test_segment_cache_stale.py`（新）。

### #83 生成中「播放此镜」被锁：已生成镜头点不了（2026-08-10）

- **用户事故**：「镜头三生成完之后点击镜头一生成，左侧的镜头三播放此镜就点击不了了。
  但是底部右侧的打开成片可以看」——生成中想回看已完成的镜头，播放入口被禁用，
  但任务中心「打开成片」可用，两个入口行为不一致。
- **根因**：前端播放可用性被 `wb.running` 一刀切——
  1. `playVideo()` 入口守卫 `if (wb.running || !episode.value) return`：只要有任何镜头
     在生成，所有播放入口全部短路。
  2. 播放按钮 `:disabled="wb.running || !isShotCached(s.id)"`：生成中即使该镜已成功
     缓存也禁用。
  3. 任务中心「打开成片」不查 `wb.running`，只认缓存/任务结果 → 所以可用，造成不一致。
- **修复**：播放可用性只由「该镜是否有成功缓存」决定——
  - `playVideo()` 去掉 `wb.running` 守卫；正在生成且无缓存的镜头本来就不会进入播放队列
    （`collectPlayableShotIds` 按 cached 集合过滤），无需全局禁播。
  - 播放按钮 `:disabled` 改为 `!isShotCached(s.id)`：其它已生成（缓存成功）的镜头播放正常；
    该镜自身生成中无缓存则仍禁用。
- **验证**：新增「生成中可播放已生成镜头（#83 回归）」2 用例（① 生成中已缓存镜头按钮
  可用/无缓存禁用 ② 生成中点已缓存镜头 → `segment_mp4` 被调用、播放器显示该镜 mp4）。
  `workbenchRender` **37/37** + `vue-tsc --noEmit` + `vite build`（临时目录）全过。
  双目录已同步。**纯前端改动，刷新即生效，无需重启 Comfy Desktop**。
- **涉及**：`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/workbenchRender.test.ts`。

### #84 单镜下载 mp4 编码失败：`Could not determine output format`（2026-08-10）

- **用户事故**：「点击还是显示下载镜头mp4失败」——SPA 卡片「⬇ 下载该镜 mp4」点下去
  报 `下载镜头 N mp4 失败：500 {"error":"encode_failed","message":"编码失败：Could not
  determine output format"}`。
- **根因**：`segment_mp4` 下载路由为原子替换，把临时编码文件命名为
  `seg_XXXX.mp4.tmp.{pid}`（**不以 `.mp4` 结尾**）。`save_segment_mp4` 里
  `av.open(path, mode="w")` 靠**文件扩展名推断容器格式**，`.tmp.{pid}` 无法识别 →
  PyAV 抛 `Could not determine output format` → 路由 500。该错误此前在 #82 用户日志里
  就以 `Segment 2 mp4 encode failed` 出现过，当时被段间连贯主案掩盖、未单独修。
  executor 的 `_shot_path_for` 产出 `..._ShotNN.mp4` 以 `.mp4` 结尾，不受影响；
  只有下载路由的临时文件名踩中。
- **修复**：`director/stream_export.py` `save_segment_mp4` 的
  `av.open(path, mode="w")` 显式加 **`format="mp4"`**（PyAV 文档：`format` 参数
  「Specific format to use. Defaults to autodect.」）——容器格式不再依赖扩展名，
  临时文件 `.tmp.{pid}` 也能正确写出 mp4。一处修复同时覆盖所有 `save_segment_mp4`
  调用点（下载路由 + executor 单镜/场景落盘），已编好的 `seg_XXXX.mp4` 秒回不受影响。
- **验证**：双目录已同步 + `py_compile` 通过 + 后端三测试文件全过
  （test_segment_cache_stale 7 / test_gen_media_tags 8 / test_project_store 8）。
  **需重启 Comfy Desktop 生效**（http 路由 + stream_export 改动）。
- **涉及**：`director/stream_export.py`。

### #85 @ 引用参考图不生效：refs 槽位 index 硬编码 0（2026-08-10）

- **用户事故**：「我在参考素材里添加了一张角色图，使用@引用时为什么没出现在画面里」——
  生成画面里看不到上传的参考角色。
- **根因**：前端 @ 参考素材用的是**数组下标**（`buildMediaTagCandidates` 里
  `refImages.forEach((r, i) => … refIndex: i)` → `pickCandidate` 插
  `<Picture {refIndex+1}>`），而后端注入槽位靠段 `refs[].index`
  （`_load_refs` → `refs_to_kwargs` → `reference_image_{index}` → 官方节点
  `MiniMaxH3ReferenceToVideo` 的 `ref_images` dict）。两处必须对齐。
  但 `MiniMaxH3Adapter.toSegment` 把每个 ref 的 `index` **硬编码为 0**
  （refAudios/refVideos 却正确用了 `i`）。多张参考图时后端
  `refs_to_kwargs` 的 dict 键全挤到 `reference_image_0` **互相覆盖**，只剩
  **最后一张**生效——用户 @ 引用的那张（非最后一张）根本没注入模型。
- **修复**：`toSegment` 里 `refs` 改为 `index: i`（map 数组下标，与前端 @ 的
  `refIndex` / store `addShotRefImage` 维护的 index 天然一致）。单张图行为不变，
  多张图后 `@ 第 N 张 → <Picture N> ↔ ref_image_{N-1}` 一一对应。
- **第二层说明**（非 bug，需用户知悉）：若某镜头实际跑 **fl2v**（首尾帧硬锁），
  `segment_refs_for_context` 会把它的 refs 清空（`CONTEXT_REFERENCE_EXCLUDED_KEYS`
  含 fl2v），上传的参考图不会注入——fl2v 的语义是首帧/结束帧，不是参考图；
  要带参考图生成请让该镜走 r2v（模式=自动/R2V）。另外 H3 的 `<Picture N>`
  参考图是「参考主体/风格」，prompt 里最好顺带描述角色外貌特征，别只依赖标签。
- **验证**：新增回归用例「段 refs 的 index 与 refImages 数组下标一致（多图不互相
  覆盖）」——`roundtrip.test.ts` **18/18** 通过；全量 `vitest` 171 passed（1 个
  `imageUploadBox` 超时是 VM 里 501MB 大文件分配的环境敏感用例，与本次改动无关）；
  `vue-tsc -b && vite build` 全过。**纯前端改动，双目录已同步，刷新即生效，
  无需重启 Comfy Desktop**。
- **涉及**：`frontend/src/adapters/minimaxH3Adapter.ts`、`frontend/tests/roundtrip.test.ts`。

### #86 V1.4-P0 错误定位（2026-08-10）：任务失败不再只显示「生成失败」

- **背景**：V1.4 四项收尾第一项（P0-1）。此前失败任务只显示「⚠ 生成失败」，用户看不到真实后端异常、
  失败的是哪个镜头、Prompt ID / Node ID，也没有快速重新生成入口。
- **改动**（纯前端）：
  - **`TaskRecord` 扩展**（`stores/workbench.ts`）：新增 `errorDetail` / `nodeId` / `report` /
    `failedShotIds` 四字段，`addTask` 统一初始化。
  - **`DirectorRunService` 完整错误解析**（`services/directorRun.ts`）：
    - WS `execution_error` → 解析 `node_id` / `exception_message` / `traceback`，不再丢弃，走
      `onFinish(promptId, false, { nodeId, errorDetail, traceback })`；
    - WS 断开兜底轮询的 `status_str === "error"` 分支 → `errorInfoFromHistory` 从
      `status.messages` 提取同样载荷；
    - 新增 `onReport` 回调：失败后 `backfillReportFromHistory` 从 `/history` 的
      `outputs[nodeId].text`（STRING 输出）取回后端导演诊断，`promptId` 不匹配的陈旧事件直接忽略。
  - **失败镜头双源并集**（`core/failedShots.ts` 纯函数 `resolveFailedShotIds`）：
    `segStatus.states === "failed"` 的段索引（状态灯失败源）∪ errorDetail 里解析出的段号
    （1-based，正则匹配 `Segment N` / `片段 #N`；**故意不匹配裸「段 #N」**，避免把
    「上一段 #1」前驱引用误判成失败镜头）→ `shotOrder[N-1]` 还原镜头 id，去重。
  - **任务中心失败详情弹窗**（`workbench/TaskCenter.vue`）：失败任务新增「🔍 详情」按钮；
    弹窗展示 失败镜头（场景·镜头名 badges）/ 失败原因（`exception_message` 直接展示，不折叠）/
    任务信息（Prompt ID / Node ID / 开始时间）/ 后端诊断 Report（**默认折叠** + 「⧉ 复制」）/
    底部「↻ 重新生成」（复用原任务 targetShotIds）。
- **设计决策**：
  - `exception_message` **直接展示**（用户拍板），Report 折叠成「▶ 查看完整 Report」——失败原因
    一眼可见，深层诊断按需展开。
  - `failedShotIds = segStatus failed ∪ errorDetail 段号` 并集去重（用户拍板）：真实失败可能发生在
    `execution_error` 时 segStatus 尚未同步，反之异常信息未必带段号，双源互补。
- **验证**：新增 `directorRunError.test.ts` **8 个用例**（WS error 解析 / history 错误提取 /
  report 回填 / 陈旧 report 防护 / 双源并集 4 例）；`directorRunFallback.test.ts` 更新 3 个断言
  （onFinish 新签名）；`roundtrip.test.ts` 修 `as` 语法 + 显式构造完整 `ShotRefs`。
  全量 `vitest` **181 passed | 1 skipped**，`vue-tsc --noEmit` 干净，`vite build` 成功
  （169.65 kB JS / 59.20 kB gzip）。**纯前端改动，双目录已同步，刷新即生效，无需重启
  Comfy Desktop**。
- **涉及**：`frontend/src/stores/workbench.ts`、`frontend/src/services/directorRun.ts`、
  `frontend/src/core/failedShots.ts`（新）、`frontend/src/workbench/WorkbenchView.vue`、
  `frontend/src/workbench/TaskCenter.vue`、`frontend/tests/directorRunError.test.ts`（新）、
  `frontend/tests/directorRunFallback.test.ts`、`frontend/tests/roundtrip.test.ts`。

### #87 V1.4-P0-2 Dirty 状态（2026-08-10）：保存按钮三态「💾 已保存 → ● 有未保存修改 → ✓ 已保存」

- **背景**：V1.4 四项收尾第二项（P0-2）。此前工作台编辑后没有任何「未保存」提示，用户不确定
  当前项目树是否已落盘，刷新/切走可能丢改动。
- **改动**（纯前端）：
  - **`WorkbenchState` 新增 `dirty: boolean` + `lastSavedAt: number | null`**（`stores/workbench.ts`）。
  - **store 内部 `touch()`**：在全部 26 个编辑原子操作「真实改到项目树后」调用置脏——
    镜头字段（content/negative/duration/section/cast/location/continuity/smartTail/stateChange）、
    镜头增删/复制/排序/重命名/跨场景移动、场景增删/重命名/patch、资产 add/update/remove、
    refs 图片/视频/音频 add/remove。
  - **`markSaved()`**（保存成功后清脏 + 记 `lastSavedAt`）、**`markDirty()`**（外部兜底手动置脏）。
  - **`loadProject` / `clearProject` 复位 dirty=false、lastSavedAt=null**：载入项目 = 与磁盘一致基线。
  - **保存按钮三态**（`WorkbenchView.vue`）：`💾 已保存`（灰蓝）→ 编辑后 `● 有未保存修改`（琥珀高亮，
    `btn.save.dirty` 样式）→ 点击保存成功 `✓ 已保存`（1.6s 后回落）。运行中禁用保存。
  - **故意不置脏**：`setOutputSize` / `applyResolutionCombo` / `setFrameRate`——输出分辨率/帧率是
    会话级状态，不在 ProjectModel 序列化内，不该触发「未保存」。
- **验证**：`workbenchEdit.test.ts` 新增 dirty describe 块 **7 个用例**（干净基线 / 编辑置脏 /
  增删改置脏 / markSaved 时间戳 / markDirty 兜底 / clearProject 复位 / 未命中不置脏）。
  全量 `vitest` **188 passed | 1 skipped**，`vue-tsc -b && vite build` 成功
  （170.03 kB JS / 59.33 kB gzip）。**纯前端改动，双目录已同步，刷新即生效，无需重启
  Comfy Desktop**。
- **涉及**：`frontend/src/stores/workbench.ts`、`frontend/src/workbench/WorkbenchView.vue`、
  `frontend/tests/workbenchEdit.test.ts`。

### #88 V1.4-P0-3 退出保护（2026-08-10）：仅 dirty 拦截，最小版三出口

- **背景**：V1.4 四项收尾第三项（P0-3）。此前工作台有未保存修改时，点「← 项目」/刷新/关页直接
  离开，改动静默丢失。用户拍板**最小版**：只拦 `dirty===true`，不拦运行中任务本身/任务队列状态，
  浏览器关闭用原生 `beforeunload` 不用自定义 Modal。
- **改动**（纯前端，`WorkbenchView.vue`）：
  - **「← 项目」三选一确认框**（`leaveConfirmOpen`/`leaveBusy` 状态 + Teleport 到 body 的
    `.leave-modal`）：`dirty` 时弹出「⚠ 有未保存的修改 / 当前项目有尚未保存的修改，离开后这些
    修改将丢失。」固定三选一：
    - **保存并离开** → `saveCurrentProject()`（**改为返回 `Promise<boolean>`**）→ 成功才
      `clearProject()` + 回项目页；**失败留在当前页面**（不清理、不离开，错误走 setError）。
    - **不保存离开** → `clearProject()` + 回项目页。
    - **取消** → 什么都不做，`dirty` 状态完全不变。
  - **浏览器刷新/关闭**：`onMounted` 注册 `beforeunload`、`onBeforeUnmount` 移除；handler 仅
    `dirty` 时 `e.preventDefault()` + `e.returnValue = ""` 触发浏览器原生「离开此网站？」确认，
    clean 时正常离开。**不做自定义 Modal 拦截浏览器关闭**（现代浏览器限制 beforeunload 展示内容）。
  - **不拦截**：`dirty===false`（直接离开）、运行中任务本身、任务队列状态。
- **验证**：`workbenchRender.test.ts` 新增 P0-3 describe **8 个用例**（dirty→beforeunload
  preventDefault / clean→不拦截 / dirty=false 直接回项目页 / dirty=true 弹三选一且内容三按钮正确 /
  保存并离开→saveProject 调用+成功后才清理 / 保存失败→留在当前页面 / 不保存离开→正常清理 /
  取消→状态完全不变）。全量 `vitest` **196 passed | 1 skipped**，`vue-tsc -b && vite build` 成功
  （171.25 kB JS / 59.76 kB gzip）。**纯前端改动，双目录已同步，刷新即生效，无需重启
  Comfy Desktop**。
- **涉及**：`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/workbenchRender.test.ts`。

### #89 V1.4-P1 WS 状态视觉（2026-08-10）：连接指示器三/四态圆点

- **背景**：V1.4 四项收尾最后一项（P1）。此前连接指示器只有「已连接/未连接」两态 boolean
  （`state.connected`），WS 断线自动重连过程没有视觉反馈。用户拍板三态：●已连接 / ○断开重连 /
  ●已恢复，不暴露过多技术细节。
- **状态模型**（`frontend/src/stores/workbench.ts`）：
  - `connected: boolean` → **`wsStatus: WsStatus`**，四态枚举
    `"disconnected" | "connected" | "reconnecting" | "recovered"`（初始 `disconnected`）。
  - 新增 `handleWsStatus(connected: boolean)`（`ComfyWsClient.onStatus` 桥接）：
    - `true`：若正处于 `reconnecting` → 置 `recovered`，**短暂展示 2.5s 后回落 `connected`**
      （模块级 `recoveredTimer`，期间再次断线会清定时器不误改状态）；否则 → `connected`。
    - `false`：→ `reconnecting`（ComfyWsClient 内部自动重连每 2s，无需前端干预）。
  - 新增 `setWsDisconnected()`：显式归位「未连接」（初始基线/主动断开兜底）。
  - `connectComfy()` 不再手动置位，连接状态完全由 onopen 事件驱动（更真实）。
- **Service 层**（`frontend/src/services/directorRun.ts`）：构造函数新增第 4 参数
  `onWsStatus?: (connected: boolean) => void`，透传给 `ComfyWsClient` 的 `onStatus`。
  `workbench.ts` 创建 `DirectorRunService` 时注入 `handleWsStatus`。
- **UI**（`frontend/src/workbench/WorkbenchView.vue`）：
  - 两处指示器（home 页 `.home-conn` + 工作台顶栏 `.wb-conn`）改为
    `wsDotClass(wb.wsStatus)` + `wsLabel(wb.wsStatus, home)` 渲染：
    - `disconnected`：灰点「未连接」
    - `connected`：绿点「已连接 / 已连接 ComfyUI」
    - `reconnecting`：橙点（闪烁动画 `dot-blink`）「连接断开 · 正在重连」，hover 给简短提示
    - `recovered`：绿点 + 脉冲动画（`dot-pulse`）「已恢复」
  - CSS：`.dot.reconn`（橙+闪烁）、`.dot.pulse`（脉冲）、`.dot` 加 `flex:none` 防挤压。
- **验证**：`workbenchWs.test.ts` 新增 **7 个用例**（四态映射 + recovered 回落 + 断线期间回落
  定时器被清不误改 + `setWsDisconnected` 归位 + connected 稳定不触发 recovered）；
  `workbenchRender.test.ts` 新增 P1 describe **6 个用例**（home 四态圆点 class + 文案、工作台
  顶栏两态透传）。全量 `vitest` **209 passed | 1 skipped**，`vue-tsc -b && vite build` 成功
  （171.97 kB JS / 59.98 kB gzip）。**纯前端改动，双目录已同步，刷新即生效，无需重启
  Comfy Desktop**。
- **涉及**：`frontend/src/services/directorRun.ts`、`frontend/src/stores/workbench.ts`、
  `frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/workbenchWs.test.ts`（新）、
  `frontend/tests/workbenchRender.test.ts`。

| 文件 | 职责 | 改它时要一起改的 |
|---|---|---|
| `director/plan.py` | 数据模型（DirectorPlan/SegmentPlan/GlobalAsset），所有字段定义 | 加字段时同步 gen_timeline.py |
| `director/gen_timeline.py` | timeline JSON → plan 解析（含命名自动匹配、关键词路由、三态解析） | plan.py 字段、payload 白名单 |
| `director/executor_core.py` | 核心执行：H3 生成、续接路由、资产注入、状态跟踪、Qwen 三级反馈、报告 | 报告格式、诊断行 |
| `director/state_rule_check.py` | 三级规则检测（纯像素，无 VLM） | qwen_vl_feedback.py、executor_core.py |
| `director/qwen_vl_feedback.py` | Qwen3-VL 三级反馈：状态提取/一致性/问题检测 + JSON 容错 | executor_core.py、vlm_backends.py |
| `director/vlm_backends.py` | VLM 后端抽象（llama.cpp / transformers）+ 工厂 | qwen_vl_feedback.py |
| `director/fl2v_timeline.py` | fl2v 面板 plan 构建、fl2v 提示词模板 | executor_core.py |
| `director/stream_export.py` | 流式/场景导出：SceneNN.mp4 编码、ffmpeg 合并、音频拼接、解码回张量 | executor_core.py、director_common.py |
| `director/segment_cache.py` | 段缓存读写（视频 `seg_%04d.pt` + 音频 `seg_%04d.aud.pt`） | executor_core.py（save_segment_cache）、tools/recover_segment_cache.py |
| `tools/recover_segment_cache.py` | **段缓存恢复工具**：缓存丢失/导出崩溃后把 `seg_*.pt` 解码成可播放单镜 mp4 + ffmpeg 直拼整片 | segment_cache.py、stream_export.py |
| `director/vram_cleanup.py` | 段间显存清理 | executor_core.py |
| `web/js/minimax_image_batch.js` | r2v 批 UI：工具栏开关、每段卡片、资产面板、高亮、**镜头设置折叠模块**、**三级资产层级**（resolveSegmentAssetPool 场景优先池 + optgroup 分组 + 🎬/🌐 来源标签）、**P5 Prompt+衔接视觉中心**（衔接徽章 + Prompt 放 body 右列、参考素材左列、资产选择行标题下常显、折叠沉底） | minimax_timeline.js、minimax_i18n.js |
| `web/js/minimax_fl2v.js` | fl2v 面板 UI：镜头卡片、共享 prompt、**提示词摘要行 + 详情区标题** | minimax_timeline.js |
| `web/js/minimax_director_panel.js` | 导出 `readDirectorQwenFields`（Qwen 状态读取；原 mountDirectorPanel 已无调用点） | minimax_advanced_panel.js |
| `web/js/minimax_advanced_panel.js` | 高级设置折叠面板（Qwen/采样/高级采样/性能/**导出设置**/**音频** 六组，导出+音频走 onOutputField 镜像 timeline.output） | minimax_timeline.js |
| `web/js/minimax_prompt_mentions.js` | 提示词素材高亮层 | minimax_image_batch.js |
| `web/js/minimax_timeline.js` | timeline 构建/加载/持久化；**payload 白名单**（坑 #16）；**顶部固定生成栏**（生成操作下拉 6 范围 + 运行状态）；**场景控制行**（场景下拉/新建场景/镜头过滤） | image_batch.js、i18n |
| `web/js/minimax_scene_manager.js` | Scene Manager 面板（场景 CRUD/素材组/每场景导出） | timeline.js、i18n |
| `web/js/minimax_export_center.js` | 素材库总览 + 导出中心（每场景导出/全片导出） | scene_manager.js、timeline.js、i18n |
| `web/js/minimax_i18n.js` | 中英文案 | 所有 UI 改动（只加 ZH 除非核心字段） |
| `director/http_routes.py` | HTTP 路由：upload_chunk/probe_video/detect_shots/**segment_cache_status**/**segment_status**/**segment_mp4** | 加路由必须重启 Comfy Desktop |

### #90 段缓存指纹补「镜头身份」：修复新项目空镜头生成出旧项目人物（2026-08-10）

- **背景（用户实测）**：V1.4-P0-1 错误定位验收时，新建项目只建 1 场景 2 镜头、
  什么都不设置，点第 2 个镜头「首尾帧生成视频」，**生成成功却回放旧项目里的人物**。
- **根因**：`segment_cache.py::segment_cache_fingerprint` 指纹**不含段唯一 id**
  （timeline.segments[].id = SPA 镜头 uuid），缓存按
  **`node_id / seg_%04d.pt`（段索引）+ 参数指纹** 存储；SPA 所有项目共用同一
  `DIRECTOR_NODE_ID` → 新项目空 prompt 段（默认分辨率/帧数/无 refs）与旧项目同位置
  空段的指纹**完全一致** → 命中旧缓存直接回放旧视频，H3 根本没跑。
- **修复**：
  1. `plan.py::SegmentPlan` 新增 `id: str = ""`（放在带默认值区；dataclass 不允许
     带默认字段排在无默认字段前）；`GlobalAsset` 新增 `image_file: str = ""`
     （来源文件名，缓存指纹用）。
  2. `gen_timeline.py` 段解析读取 `seg_data.id`（兼容 `segmentId`）传入 SegmentPlan；
     `_load_global_assets` 构造时带上 `imageFile`。
  3. `segment_cache.py` 指纹新增两键：
     - `"id"` = 段唯一 id → **跨项目/跨镜头缓存隔离**（核心修复）。
     - `"assets"` = 注入资产图标识列表（`kind:id:image_file`，含 cast/location/
       extra/tag 四类，过滤空项排序）→ **替换/更换参考图也会让缓存失效**，
       避免「改角色图仍出旧画面」（同类隐患，资产图注入 ref_image 但不写回
       seg.refs，原指纹感知不到）。
- **影响与兼容**：
  - 旧缓存 meta 无 `id`/`assets` 键 → `stored != expected` → **全部旧缓存失效**
    （重新生成一次即可），但 `allow_stale=True` 的续接锚点路径（#82）不受影响，
    仍可拿旧帧作视觉锚点。
  - 老 timeline 无段 id → `seg.id == ""` → 指纹退化为「按 index+参数」匹配，
    向后兼容。
- **验证**：新增 `director/tests/test_segment_cache_identity.py` **7 用例**
  （不同 id 不互命中 / 同 id 正常命中 / 资产图替换失效 / 保存-同 id 加载命中 /
  跨项目不同 id 不命中 / stale+allow_stale 仍作锚点 / 老 meta 无 id 键→默认拒
  allow_stale 可锚）。`py_compile` 三个改动文件通过；后端测试
  identity 7 + stale 7 + gen_media_tags 8 + project_store 8 **全部通过**。
- **涉及**：`director/plan.py`、`director/gen_timeline.py`、`director/segment_cache.py`、
  `director/tests/test_segment_cache_identity.py`（新）。**改后必须重启 Comfy Desktop**。

### #91 修复「SPA 点取消生成，desktop 还在跑」（2026-08-10）

- **背景（用户实测）**：空镜头 bug（#90）修复后重跑，生成中在 SPA 点「取消生成」，
  ComfyUI Desktop 里任务**仍在跑**，直到整段生成完。
- **根因**：ComfyUI 的 `/interrupt` 只把 `comfy.model_management.interrupt_processing`
  标志置位，真正中断要**运行中的采样循环去检查该标志**。官方 KSampler 靠
  `comfy.ops::run_every_op()` 在模型前向里检查，但本节点 executor 直接调
  `KSampler.sample()`（`core_sampling.py`），H3 采样是否命中 comfy.ops 前向检查
  取决于模型/量化包装，**不能保证** → 标志置了也没人理，跑到结束。
  前端链路本身正确（`interrupt()` → deletePending → POST /interrupt → settleRun(false)）。
- **修复**（后端，双层检查）：
  1. `core_sampling.py` 改为**忠实复制 `nodes.common_ksampler` 核心**（
     `fix_empty_latent_channels` + `prepare_noise` + `comfy.sample.sample` +
     latent dict 返回），并在 **①采样前** 和 **②每步 callback** 显式调用
     `model_management.throw_exception_if_processing_interrupted()`——
     置位即抛 `InterruptProcessingException`（BaseException 子类，先清标志再抛），
     向上传播终止整次生成；保留 `latent_preview.prepare_callback` 预览回调，
     预览异常吞掉不阻断采样。
  2. `executor_core.py` 新增模块级 `_raise_if_interrupted()`，插桩在
     **段开始（`_run_one_segment`）**、**两条段循环的段间**、**场景合并
     （`_encode_scene` / `_stream_concat_scene` 入口）**——多段/导出阶段取消
     也能立刻停下。
  3. 段循环的 `except Exception` 改为 `except BaseException`：取消/中断传播时
     同样把当前段状态灯标 `failed`（不再停留 `running`），再原样上抛。
- **验证**：新增 `director/tests/test_core_sampling_interrupt.py` **4 用例**
  （采样前置位即抛 / 采样中途置位 step callback 抛且 sample 不返回 /
  未置位正常返回 latent dict / preview 回调异常不阻断）。后端全套测试
  interrupt 4 + identity 7 + stale 7 + gen_media_tags 8 + project_store 8
  **全部通过**；py_compile 通过；双目录已同步。
- **涉及**：`director/core_sampling.py`、`director/executor_core.py`、
  `director/tests/test_core_sampling_interrupt.py`（新）。**改后必须重启 Comfy Desktop**。

### #92 修复「SPA 取消仍不停」真根因：vite proxy 缺 /interrupt（2026-08-10）

- **背景（用户实测）**：#91 后端修复部署 + desktop 重启后，SPA 点「⏹ 取消」
  （刚点生成不久就取消），**生成仍正常跑完**。前端任务标「已取消」，desktop 不停。
- **真根因**：SPA 通过 **vite dev server（localhost:5173）** 访问（部署目录没有
  `frontend/dist`，未做静态部署）。`comfyApi.interrupt()` 发 `POST /interrupt`
  （相对路径）→ 打到 vite dev server → **vite.config.ts proxy 规则列表里没有
  `/interrupt`**（只有 object_info/prompt/queue/history/ws/view/upload/api/minimax）
  → 请求被 vite 自己吃掉（404/HTML），`request()` 抛错被 `directorRun.interrupt()`
  的 try/catch 吞掉 → **ComfyUI 后端从未收到 /interrupt，标志从未置位**，生成继续跑完。
  #91 的后端检查本身正确，但「请求传输」第一层就断了，后端再检查也收不到信号。
- **修复**：`frontend/vite.config.ts` proxy 补上
  `"/interrupt"` 和 `"/free"`（ComfyUI 顶层 API，同样会漏）两条规则，其余配置一致
  （changeOrigin + Origin 改写绕过 origin_only_middleware）。同源部署（ComfyUI 静态目录）
  下 /interrupt 天然可达，此修复只影响 dev 模式——而 dev 模式正是用户实际的使用方式。
- **验证**：前端全套测试 209 passed | 1 skipped 全绿；部署目录 vite.config.ts 已同步
  （diff SAME，proxy 规则含 /interrupt）。**改 vite.config.ts 需重启 vite dev server 生效**，
  ComfyUI Desktop 后端无需重启。
- **涉及**：`frontend/vite.config.ts`。教训：**SPA 新增对 ComfyUI 顶层 API 的调用时，
  必须同步检查 vite proxy 白名单**——缺规则时请求被 dev server 吞掉且前端无感知
  （try/catch 静默），极难排查。

### #93 修复 P0-1 失败定位：s2 坏图错标 s1（2026-08-10）

- **背景（用户实测）**：P0-1 验收时故意把坏图（文本文件改名 .png）放进 **s2** 的参考素材，
  生成失败弹窗却显示 **「霓虹街区 · s1」**（上一轮取消过的镜头）。快照 + comfyui.log 双线取证确认。
- **真根因**（两个问题叠加）：
  1. **plan 阶段坏图失败不带段号**：`build_gen_director_plan`（gen_timeline.py 段循环）里
     `_load_refs()` 直接在循环中抛 `UnidentifiedImageError`，ComfyUI execution_error 只有
     traceback 没有段号 → 前端 `resolveFailedShotIds` 的「异常段号」源为空。
  2. **上一轮取消残留的 failed 状态**：#91 用 `except BaseException` 兜底中断，用户取消时把
     当时在跑的段标成 `failed`；而 `_RUNTIME_STATES` 的 failed **永不过期**，且新 run 开始
     不清空 → 下一次 plan 失败时 `segStatus.states` 里躺着上一轮的「s1=failed」，
     前端双源并集只匹配到它 → 错标 s1。
- **修复**（三层）：
  1. `gen_timeline.py` + `plan.py` 段循环的 `_load_refs()` 包 `try/except`，失败时
     `raise ValueError(f"Segment {idx+1} reference image could not be loaded: {exc}")` ——
     错误信息带段号（1-based），前端正则 `(Segment|segment|片段)\s*#?\s*(\d+)` 直接定位真实镜头。
  2. `nodes/director.py` 每次 `execute()` 开头（`prepare_director_plan` 前）调用
     `clear_segment_runtime_state(unique_id)` 清空本节点全部 runtime states —— 新 run 开始
     即丢弃上一轮取消/中断标记的残留 failed/running。
  3. 前端 `WorkbenchView.onFinish` 失败分支先 `await refreshSegStatus()` 再
     `resolveFailedShotIds(...)` —— 定位基于本次运行后的真实状态，不读旧缓存。
- **验证**：前端 210 测试（+1 回归用例「#93 plan 坏图错误带段号 → 定位真实镜头」）+ build 全绿；
  后端 py_compile 通过。双目录已同步 + 清 __pycache__。
- **涉及**：`director/gen_timeline.py`、`director/plan.py`、`nodes/director.py`、
  `frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/directorRunError.test.ts`。
  **后端改动需重启 Comfy Desktop**；前端 vite dev 刷新即生效。
- **坑**：①段号用 `idx+1`（1-based），与前端 `shotOrder[N-1]` 对齐；②**取消一个正在跑的段也会
  把它标 failed**（#91 的 BaseException 兜底），新 run 清空是必要的——否则取消行为会污染下一次
  任务的失败定位；③全局资产 `_load_global_assets` 里的坏图仍不带段号（不属于任何镜头），
  定位时显示「未能定位到具体镜头」，属预期。

### #94 V1.5-P0-A 素材库 UI + 两级池 + 复制（2026-08-10）

- **背景**：V1.5 目标是「素材库 → 复制 → @引用 → 改名同步 → 继承展示 → refs → H3」数据链。
  P0-A 先做**素材库管理 UI + 全局/场景两级池 + 复制快捷**（规划文档 `V1.5_MATERIAL_LIBRARY_PLAN.md` §5）。
  纯前端改动，不碰后端（`gen_timeline.py`/`plan.py` 本轮零动）。
- **功能**：
  1. 场景设置面板「素材库」区：四类资产（人物/地点/道具/风格）卡片网格，每卡 = 缩略图 + 名称 +
     来源标记（🎬 场景 / 🌐 全局）+ 描述 + 别名 + 操作（✎ 编辑 / ⧉ 复制到本场景 / 🗑 删除）。
  2. 两级切换按钮「🎬 场景素材 ↔ 🌐 全局资产」，切换即换池，无独立 tab（用户拍板 §8.2）。
  3. 新建资产（＋）→ 名称 prompt + 名称唯一校验 + **自动展开内联编辑表单**（名称/描述/别名/参考图，
     保存/取消）；编辑复用同一表单。
  4. 全局资产卡「⧉ 复制到本场景」→ `copyAssetToScene` 生成**新 id 一次性快照**，复制后与全局解耦
     （用户拍板 §8.4），成功后自动切回场景素材 scope。
  5. 同池内同 kind 名称唯一（用户拍板 §3.2）：`addAsset`/`updateAsset` 冲突拦截/跳过 name，
     UI 用 `libMsg` 提示换名。
- **store 层**（`frontend/src/stores/workbench.ts`）：
  - `assetNameConflict(kind, name, scope, sceneId?, excludeId?)` —— 返回占用名资产或 undefined。
  - `addAsset` 补 `description` 字段支持（之前只有 imageFile/aliases，描述只能走 updateAsset）。
  - `updateAsset` 改名冲突时跳过 name 字段、其余照常应用。
  - `copyAssetToScene(kind, assetId, sceneId)` —— 场景池同名冲突返回 null，否则新 id 快照。
- **验证**：前端 218 测试（+9：workbenchEdit 6 个 store 用例 + workbenchRender 3 个 UI 用例）+ build 全绿。
- **涉及**：`frontend/src/stores/workbench.ts`、`frontend/src/workbench/WorkbenchView.vue`、
  `frontend/tests/workbenchEdit.test.ts`、`frontend/tests/workbenchRender.test.ts`。
  **纯前端，vite dev 刷新即生效，无需重启 Comfy Desktop**。
- **坑**：①`loadProject` 深拷贝 → 测试里资产引用比较必须用 `toEqual` 不能 `toBe`；
  ②updateAsset 的冲突检查只在**传入的 scope+sceneId 池内**做，跨池同名不拦（阿辉在 sc_b、
  改 sc_a 的 cast_a 为阿辉不冲突）；③新建后**自动进入编辑表单**，保存才落卡片，渲染测试须先点保存
  再断言卡片文本（`.lib-edit-actions .btn.small.primary`）；④`.lib-card-name` 列表是断言两级池
  切换/去重最稳的方式（场景树/镜头卡同名文本会污染 `wrapper.text()` 断言）。

### #95 V1.5-P0-B @资产改名同步（2026-08-10）

- **背景**：P0-A 素材库改名只是改资产本身，镜头 Prompt 里的 `@旧名` 纯文本引用会变「断链」。
  P0-B 让改名**批量同步全项目 Prompt 引用**（规划 §3.5，用户拍板：只替换真正的 `@token`）。
  纯前端改动，不碰后端。
- **功能**：
  1. `replaceMentionToken(text, oldName, newName)` —— 只替换 `@旧名` **后一字符不是资产名字符**
     的 token（`ASSET_NAME_CHAR = [\p{L}\p{N}_\-（）()·\.·]`，与 assetResolver 名字符集一致），
     防 `@林雪` 误伤 `@林雪·成年` / `@林雪走进`；文本末尾的 `@旧名` 也替换（无后字符）。
  2. `renameAsset(kind, assetId, patch, scope, sceneId)` —— 改 name/aliases 后扫描**项目全部镜头**
     的 `visual` + 摄影/风格/声音三分区（`MENTION_TEXT_FIELDS`），多匹配名按**旧名长度降序**替换
     （长名先替换，避免 `@林雪` 把 `@林雪·成年` 拦腰截断）；负面词（negativePrompt）不动；
     显式 id 引用（shot.castId/locationId、scene 默认值）不受影响（id 稳定）。
  3. `WorkbenchView.saveEditAsset` 从 `updateAsset` 改走 `renameAsset` —— 素材库编辑改名即触发全项目同步。
- **验证**：前端 224 测试（+6：replaceMentionToken token 边界 1 + renameAsset 改名/边界/别名/描述/
  跨场景 5）+ build 全绿。
- **涉及**：`frontend/src/stores/workbench.ts`、`frontend/src/workbench/WorkbenchView.vue`、
  `frontend/tests/workbenchEdit.test.ts`。**纯前端，vite dev 刷新即生效，无需重启 Comfy Desktop**。
- **坑**：①**测试 fixture 共享引用污染**——`loadProject` 是浅存引用（`state.project = project`），
  测试里 makeProject 若让场景池/全局池共享同一 `Asset` 常量，改名会跨用例污染（改名后用例
  `oldName===newName` → mapping 空 → 不替换文本）。workbenchEdit.test.ts 已加 `cloneAsset`
  深拷贝资产进 fixture；**新增测试文件若复用 Asset 常量必须 cloneAsset**。
  ②`renameAsset` 里 `pool` 可能 null（TS18047），`if (!a || !pool) return;` 双判空。
  ③addShot 是**追加到数组末尾**（order 重排 [0,1,2]），定位新镜头用 `find(id)` 不能硬编 index。

### #96 V1.5-P1 四类继承可视化（2026-08-10）

- **背景**：P0-A/P0-B 完成后，镜头面板只能看到人物/地点两条继承来源（单选链），
  道具/风格（集合类，走 extra_assets 整组注入）完全不可见。P1 在**展示层**把该镜最终生效的
  四类资产 + 每条来源画出来（规划 §3.4/§5-P1；用户拍板不碰后端 @prop/@style，后端零改动）。
- **功能**：
  1. `inheritanceResolver.ts`：`poolOf` 扩展四类（episode+scene 的 cast/locations/props/styles）；
     新增 `resolveCollection(kind: prop|style)` —— **集合级解析** = 场景池（优先，source=scene）
     ∪ 全局池中「场景池没有的同名资产」（补缺，source=project），**按名称判重（trim+lowercase）**，
     场景池优先，UI 永不出现重复条目；`resolveInheritance` 返回 cast + location + prop 集合 + style 集合。
  2. `WorkbenchView.vue`：`assetIndex` 补 `globalProps/globalStyles`（@ 识别/缩略图对全局道具/风格一致）；
     新增 `propInherited`/`styleInherited` computed；`sourceBadgeEx` 集合类徽标 = 🎬场景素材/🌐全局素材
     （单选类沿用 ⚡镜头指定/↑场景默认/↑项目默认三态）；镜头设置面板新增「生效资产」区块——
     四类分块（人物/地点单选 + 道具/风格集合），每条 = 缩略图 + `生效：名` + 来源徽标，
     无任何生效资产时显示「暂无生效资产」。
- **验证**：前端 228 测试（+4：coreModules 集合级去重/空池+inheritedSource 2 + workbenchRender
  四类区块渲染 5 行/无道具风格只列人物地点 2）+ build 全绿。
- **涉及**：`frontend/src/core/inheritanceResolver.ts`、`frontend/src/workbench/WorkbenchView.vue`、
  `frontend/tests/coreModules.test.ts`、`frontend/tests/workbenchRender.test.ts`。
  **纯前端，vite dev 刷新即生效，无需重启 Comfy Desktop**。
- **坑**：集合类去重是**按名称**（非按 id）——场景池「黑伞」与全局复制副本「黑伞」同名不同 id，
  展示时只保留场景池那条；同名判重用 `name.trim().toLowerCase()`，与 P0-A 名称唯一校验口径一致。

### #97 V1.5 正式冻结 CLOSED / FROZEN ✅（2026-08-10）

- **背景**：P0-A（素材库）+ P0-B（@资产改名同步）+ P1（四类继承可视化）三阶段全部收官，
  用户拍板**不再往 V1.5 追加功能**，正式冻结。V1.5 规划文档头部状态已改为
  `✅ CLOSED / FROZEN`，§5 三阶段 checkboxes 全部勾选。
- **冻结范围（保持不动）**：
  - ❌ H3 `integrated_multimodal_description` 高级结构化输入
  - ❌ AI 自动拆 Prompt / 自动 Prompt 优化
  - ❌ AI 自动生成 / 自动理解资产
  - ❌ 后端 `@prop/@style` 扩展
  - ❌ 资产 ID 绑定 Prompt（`@` 保持纯文本）
  - ❌ 全局资产与场景资产实时同步（复制是一次性快照，解耦）
- **下一阶段方向（用户拍板）**：**不先开 V1.6 开发**，而是拿一个真实的、稍微复杂的短片项目
  完整走一遍，专门验证 V1.4（稳定生产闭环）+ V1.5（素材库/@资产/继承）合在一起后的
  **生产效率与资产一致性**。真实项目走查中暴露的问题优先于新 UI 功能。
- **版本边界**：
  ```
  V1.2 分镜工作台 → V1.3 Prompt 分区 → V1.4 稳定生产闭环 → V1.5 素材库/@资产/继承
  项目 ├─ 场景 ├─ 素材 └─ Shot └─ Prompt/@资产/继承/refs/H3生成 + 全局素材库
  ```
- **涉及**：`V1.5_MATERIAL_LIBRARY_PLAN.md`（状态+清单打勾）、`CHANGELOG.md`（本条）、记忆。
  无代码改动，双目录已同步。

### #98 Q3 修复：addShot 预写场景默认值 → 继承徽标误判「⚡镜头指定」（2026-08-10）

- **背景**：走查《第七号站台》镜 4 时发现——地点下拉「没动」，但「生效资产」区地点显示
  「地铁站台 ⚡镜头指定」（应为 ↑场景默认）。定位根因是 `addShot`（workbench.ts）新建镜头
  直接 `castId: scene.defaultCastId`、`locationId: scene.defaultLocationId` 写入，**不设
  castManual/locationManual**；而 `resolveOne`（inheritanceResolver.ts）显式分支
  `defaultId === explicitId` 把「addShot 预填的场景默认」判定为「explicit 镜头指定」。
- **影响（连锁）**：①新建镜头生效区误显示 ⚡；②**镜 2「@命中验证」是假阳性**——镜 2 人物/地点
  ⚡ 实际是 addShot 预写默认值被误判为显式，@ 命中分支根本没执行；③污染后续镜 6/7/8 @ 验证。
- **修复（三层）**：
  ① `addShot` 不再预写场景默认值（`castId/locationId` 保持 `undefined`，继承链渲染时动态解析）；
  ② 新增 `normalizeShotInheritance(project)`，在 `loadProject` 开头清理已保存项目中
     「id 有值但 manual 假」的残留字段（安全边界：UI 原子操作里 `castId 有值 ⟺ manual=true`，
     导入通道也设 manual=true，残留只可能来自旧 addShot，可安全清；幂等）；
  ③ `resolveOne` 显式分支去掉 `defaultId === explicitId` 子句（正常显式选择必定 manual=true，
     该子句唯一作用就是制造误判）。
- **生成路径安全**：`directorCore.ts` 已有 `shot.castId ?? scene.defaultCastId ?? 全局` 兜底，
  移除预写不改变生成行为（后端 gen_timeline.py 也按 castId 显式 → @名字 → 场景默认解析）。
- **涉及**：`frontend/src/stores/workbench.ts`（addShot + normalizeShotInheritance + loadProject）、
  `frontend/src/core/inheritanceResolver.ts`（resolveOne）、
  `frontend/tests/workbenchEdit.test.ts`（addShot 测试改语义 + normalize 新测试）、
  `frontend/tests/workbenchRender.test.ts`（「新增镜头」测试改为验证不预写 + 渲染层走场景默认）。
- **验证**：229 测试全绿 + `npm run build` 通过，双目录已同步。纯前端改动，刷新 SPA 即生效；
  已保存项目需重新打开一次触发 normalize 清理残留。

### #99 Q4 修复：项目自动保存三层防线（防「刷新丢全部数据」，2026-08-10）

- **背景（生产事故）**：走查《第七号站台》返工时发现——项目结构数据（3 场景/4 镜头/两级资产池复制关系）
  因**从未手动保存**，刷新 SPA 后全部丢失；磁盘 `minimax_studio/projects/` 只剩空项目。根因=项目只存
  内存（Vue store），无 localStorage 缓存；snapshots 只在生成视频时自动落盘（本项目未生成过）。
  资产参考图不受影响（上传直接写文件）。V1.4-P0-2 有 Dirty 状态 + P0-3 beforeunload 拦截，但
  **提醒机制不够主动**，用户刷新/关页仍丢数据。
- **修复（三层，纯前端）**：
  ① **防抖自动保存**：`WorkbenchView.vue` 编辑后 3s 停顿自动落盘（`AUTO_SAVE_DELAY=3000` +
    `scheduleAutoSave`/`autoSave`，静默调 `saveCurrentProject(true)`，不弹「✓ 已保存」tip）。
  ② **切后台立即保存**：`visibilitychange→hidden` 同步 `autoSave()`（关页/切走前兜底）。
  ③ **beforeunload 保留**：仍拦截 dirty 确认（手动「保存并离开」路径不变）。
- **关键实现决策（Why）**：
  - **watch `wb.dirty` → watch `wb.revision`**：连续编辑时 dirty 始终 `true` 不触发 watcher，
    防抖不会重置；新增 `revision` 计数器**每次 touch() 递增**，每次编辑都触发 watcher 重置防抖计时。
  - `revision` 加入 `WorkbenchState` 接口 + `loadProject`/`clearProject` 复位为 0（与 dirty 对齐磁盘基线）。
  - 生成中（`wb.running`）跳过自动保存（避免保存被生成快照状态污染）；生成结束 watch running 补一次防抖。
- **涉及**：`frontend/src/stores/workbench.ts`（revision 计数器 + touch 递增 + 接口 + load/clear 复位）、
  `frontend/src/workbench/WorkbenchView.vue`（三层自动保存 + watch 改 revision）、
  `frontend/tests/workbenchRender.test.ts`（新增 Q4 describe 4 用例：
  停顿 3s 落盘 / 连续编辑重置防抖 / 切后台立即落盘 / 生成中跳过+结束补存）。
- **测试坑**：Q4 用例依赖确定性的「非运行态」，前序 V1.3 生成用例可能残留 `wb.running=true`
  → autoSave 因 `wb.running` 跳过 → 用例误失败；Q4 describe 内加 `beforeEach(() => setRunning(false))` 复位。
- **验证**：233 测试全绿（新增 4 例）+ `npm run build` 通过（vue-tsc 严查 revision 接口字段），
  双目录已同步。**纯前端改动，刷新 SPA 即生效**（dev server HMR 自动热更）。

### #100 回滚 #99 自动保存（用户决定：改回手动保存版，2026-08-10）

- **背景**：用户实测发现自动保存未能阻止「退出后项目数据丢失」——磁盘 `未命名项目/project.json`
  仍是 16:55:29 的空壳（新建项目落盘后编辑的数据从未写入）。用户拍板**回滚自动保存改动**，
  恢复成 #99 之前的**手动保存**版本，不再追求自动落盘。
- **回滚范围**（3 文件，纯前端）：
  ① `frontend/src/stores/workbench.ts`：删 `revision` 字段（接口/init/touch 递增/load/clear 复位全移除）。
  ② `frontend/src/workbench/WorkbenchView.vue`：删三层自动保存（`AUTO_SAVE_DELAY`/`scheduleAutoSave`/
    `autoSave`/`onVisibilityChange`/watch revision/watch running）；`saveCurrentProject(silent)` 恢复无参
    版本（保存后始终弹「✓ 已保存」tip）；onMounted/onBeforeUnmount 只保留 beforeunload 拦截。
  ③ `frontend/tests/workbenchRender.test.ts`：删 Q4 describe 4 用例 + mockHiddenState 辅助。
- **保留**：#99 之前的全部功能（Dirty 状态 💾→●→✓、退出保护三选一、beforeunload 拦截）原样不变。
- **验证**：229 测试全绿 + `vue-tsc --noEmit` 通过 + `npm run build` 通过，双目录已同步。
  纯前端改动，刷新 SPA 即生效。**走查教训**：编辑关键结构后仍需手动点保存（💾 按钮），
  或依赖生成时快照；自动保存方案待重新设计（如 localStorage 缓存）后再考虑。

### #101 播放全片加载「几天前生成的旧视频」根治：段缓存加镜头身份校验（2026-08-10）

- **用户症状**：走查《第七号站台》点「播放全片」，播放器把几天前其它项目/旧时间线生成的视频也加载了出来。
- **根因**：`/minimax/director/segment_status` 的 `cached` 只按「文件存在」判定
  （`minimax_seg_cache/<node_id>/seg_NNNN.pt` 存在即算已生成），**不做镜头身份校验**。
  ComfyUI 输出目录里残留着旧项目缓存（本机实测：节点 `135` 8 文件、节点 `5` 34 文件，
  seg_0003+ 的 meta.json 无 id 字段、prompt 含「跳舞/Cinematic」等旧内容），
  只要 node_id 撞上（同目录下 Director 节点/工作流复用），旧缓存就被当成当前项目镜头。
- **修复**（后端 + 前端两处）：
  ① `director/http_routes.py`：`segment_status`/`segment_cache_status` 新增可选 `shot_ids`
    （逗号分隔当前项目镜头 id 列表）。传入后只把 `meta.json` 里 `id` 命中集合的段算作 `cached`；
    无 `shot_ids` 保持旧行为兼容旧调用方。新增 `_segment_cache_shot_id(root, idx)` 读
    `seg_%04d.meta.json` 的 `id`（#90 指纹已含 id；无/损坏返回 None）。
  ② 前端：`comfyApi.ts` 的 `segmentStatus`/`segmentCacheStatus` 加 `shotIds?` 参数拼进 URL；
    `directorRun.ts` `pollSegmentStatus` 透传；`WorkbenchView.refreshSegStatus()` 传
    `flattenedShots().map(s => s.id)`，播放全片/场景/单镜前刷新段状态即拿到身份校验后的 cached。
- **Why**：播放可用性完全由 `cached` 集合驱动（`collectPlayableShotIds` 按 cached 过滤），
  修对数据源即可同时修好状态灯 + 播放队列 + 「播放此镜」按钮，不用动播放器。
- **How to apply**：旧格式缓存（meta 无 id）现在一律不算 cached——这是预期行为，
  用户看到的「几天前旧视频」正是这些。当前项目后续新生成的缓存都有 id，不受影响。
- **验证**：后端 `py_compile` + AST 通过；前端 228/229 测试通过（1 例 flaky 超时，
  单跑即绿）+ `npm run build` 通过；双目录已同步。**需重启 Comfy Desktop** 生效。

### #102 左侧镜头删除按钮点不到：浮层自适应向上弹（2026-08-10）

- **用户症状**：左侧镜头列表里，靠近底部的镜头「🗑 删除」等按钮点不到。
- **根因**：`.shot-ops` 是卡片 hover 时在**下方**弹出的绝对定位浮层
  （`top: calc(100% + 2px)`）。`.wb-left` 是 `overflow-y: auto` 滚动容器：
  ① 靠近列表底部的卡片，浮层超出容器下缘被裁剪；② `+2px` 间隙让鼠标从卡片移到浮层的
  hover 链断裂，浮层瞬间消失，根本点不到。
- **修复**（纯前端）：
  ① 模板 `.shot-card` 加 `@mouseenter="onShotCardEnter($event)"`。
  ② 新函数 `onShotCardEnter`：算卡片下缘到 `.wb-left` 可视区下缘的剩余空间 `spaceBelow`，
    不足放下整个浮层（`ops.offsetHeight || 120`）就给 `.shot-ops` 加 `.ops-up`。
  ③ CSS `.shot-ops.ops-up`：`top: auto; bottom: calc(100% + 2px);` 向上弹。
- **Why**：用「距容器底部空间」判断而不是硬编码「最后 N 张卡片」，镜头数量/高度任意变化都自适应；
  浮层仍绝对定位不参与 flex 布局（#76 修复的「下载按钮被挤出」不回归）。
- **验证**：229 测试全绿 + `npm run build` 通过；双目录已同步。纯前端，刷新 vite dev 即生效。

### #103 走查发现：只生成 3 镜却报「合并视频需约 6.3GB 内存」（2026-08-10，使用指引非代码）

- **用户疑问**：「我才生成三个镜头为什么会满？」
- **根因**：SPA 生成范围选「当前场景 / 所有未完成 / 全部」时，`exportMode` 固定走 `"all"`
  （`WorkbenchView.vue`：`scopeIds.length === 1 ? "segments" : "all"`）。`"all"` 模式后端按
  **整条时间线**合并（`executor_core.py`：未选中的段用 `segment_passthrough_chunk` 填充进
  `output_chunks`，然后全量 concat），内存按全片总帧数估算——本项目 11 镜 ≈ 60s，
  按 1280×736 估算就超过剩余 RAM（H3 生成中 + 中间帧已吃掉一大半，剩 3.7GB）。
- **应对**：走查期间**逐镜生成**（生成范围选「生成当前镜头」，单镜 → `exportMode:"segments"`
  不合并、不触发内存保护）。全片/场景导出另用「导出」功能（scene/movie 走流式 + ffmpeg 直拼）。
- **How to apply**：V1.5 冻结期不改代码；如走查中多镜合并仍是刚需，再评估把多镜运行
  自动切成 `"scene"` 流式模式（需用户确认，放 V1.6 或后续）。

### #104 走查模式 / 成片模式双模式生成（2026-08-10，用户「一镜出片自动下一镜」+ 升级为顶层模式切换）

- **用户需求**：「逐镜生成不能自动化吗，生成完一个镜头后直接结束任务出镜头视频然后自动进行下一个镜头生成」。
  随后用户给出完整设计：把「逐镜」勾选框升级为 **走查模式 / 成片模式** 顶层切换 + 走查队列 + 「从此镜继续」。
- **根因**（承接 #103）：SPA 多镜范围（含「全部镜头」）`exportMode` 固定 `"all"` 整片合并
  → 大范围触发 `_guard_merge_memory` 内存保护；且每镜之间需要用户手动逐次点「生成当前镜头」。
- **双模式设计**（`WorkbenchView.vue` + `workbench.ts` + `TaskCenter.vue`）：
  - 生成栏「模式」切换（radio pills，**默认走查**，UI-only 不写项目文件）：
    - **走查模式**（默认）：多镜范围逐镜串行。每镜 `exportMode:"segments"` 独立提交 → 后端只采样该镜、
      SaveVideo 直接输出该镜单段视频（不合并整条时间线 → 不触发内存保护）；一镜 onFinish 成功 →
      固化该镜指纹 + 自动提交下一镜（衔接自动走 r2v/fl2v 续接）；失败/取消 → 停。
    - **成片模式**：多镜范围走 `"all"` 整片合并输出（目标段新内容 + 其余段缓存填充拼成整片预览）。
      需较高内存（#45 事故：6 镜 1475 帧曾 15.5GB 触发内存保护崩溃）；生成栏在成片模式且多镜时
      显示「⚠ 多镜合并」提示。单镜范围（current/单镜按钮）两模式都走 segments，不做合并。
  - **从此镜继续**（范围下拉新项 `fromShot`）：当前镜头 + 后续全部（拍平顺序）逐镜串行 ——
    走查中某镜失败，修好后点「▶ 从此镜继续」直接续拍，不用重选范围（对《第七号站台》走查续拍特别有用）。
  - **走查队列**：`TaskRecord.shotStates` 记录每镜状态图 `{镜头id: pending|running|done|failed|cancelled}`
    （对象键按插入序）；`TaskCenter` 面板渲染「🎬 走查队列 · N 镜」每镜 ✓/▶/○/✗ 清单（任务结束后仍可回看）。
    摘要行显示「🎬 走查队列 · 阶段 · 第 N/M 镜」。
  - `runSequentialGenerate`（批次循环）+ `runOneSequentialShot`（单镜提交）：单镜 segments 结构、
    `targetShotIds:[sid]`、进度 `overallValue=已完镜数/overallMax=总镜数`、`progress.segment=第 i+1/total 镜`。
  - `TaskRecord` 加 `currentIndex`（当前第几镜）+ `shotStates`（走查队列）；`TaskCenter` 摘要行
    「生成中」只认 `activeTask`（status===running），任务结束后归位「任务中心」。
- **坑（How to apply）**：
  ① **陈旧事件保护**：逐镜串行下晚到的上一镜 WS 事件可能误收尾当前镜 —— `onFinish` 里
  `if (expectedPid && pid !== expectedPid) return`（expectedPid 在 `run()` resolve 后回填）；
  `finishOnce` 守卫保证 resolve 恰好一次。
  ② **running 状态只批量收尾**：每镜不调 `setRunning(false)`（否则会提前解锁生成按钮）；
  `runSequentialGenerate` 整批结束后统一 `setRunning(false)` + 刷段状态。
  ③ **取消语义**：`onTaskCancel` 置 `cancelledByUser` → `interrupt()` → 当前镜 onFinish(false) →
  批次标 cancelled 停止后续镜（shotStates 该镜标 cancelled）。
  ④ **r2v/fl2v 续接锚点**：逐镜顺序生成恰好满足「前驱先生成」约束；若中途某镜从未生成过且无缓存，
  仍会报「段间连贯」——先逐镜补上前驱或整批跑。
  ⑤ **radio pills 交互**：`.gen-mode-pill input` 绝对定位 + `pointer-events:none`（label 激活转发），
  高亮类 `.active` 手动绑定 v-model；测试里 radio 用 DOM 设置 `checked=true` + trigger("change") 驱动 v-model。
  ⑥ **shotStates 响应式**：对象是普通引用，`patchTask` 时须 `{ ...shotStates }` 展开成新对象让 Vue 重新包裹，
  直接改引用内字段不触发视图更新。
- **验证**：236 测试全绿（走查 describe 重写：模式 pill 默认走查 / 全部镜头按序逐镜 segments / 失败即停 /
  成片模式单次 all / 成片单镜仍 segments / fromShot 从此镜继续 / 走查队列 shotStates+面板渲染）+
  `npm run build` 通过；双目录已同步（4 文件 md5 一致）。纯前端，**刷新 vite dev 即生效，无需重启 Comfy Desktop**。

### #105 成片模式前端误判「生成失败」根治（2026-08-10，用户实测成片 11 镜后端成功但前端弹失败）

- **用户实测**：《第七号站台》成片模式选「全部镜头」11 镜提交，后端 `Prompt executed in 00:17:43`
  完整成功（11 段全 done + `Merged 11 segment audio clip(s) for export=all` + 视频正常生成），
  但前端任务中心弹「生成失败 / 未能定位到具体镜头 / Node ID —」，开始时间 19:24:14。
- **根因**（`directorRun.ts`）：兜底轮询 `MAX_POLL_TICKS = 300`（300×3s = **15 分钟硬上限**）。
  成片任务跑 17:43，第 15 分钟时仍在该跑段 10/11，`tickFallbackPolling` 超时分支**无条件** `settleRun(false)`
  → `onFinish(false, undefined)` → 前端标失败、无 errorDetail → 弹窗「生成失败/未能定位到具体镜头/Node ID —」。
  随后 WS `execution_success`（19:41:58 到达）被 `settled` 挡住不再触发 → 任务永久显示失败（视频实际已产出）。
  走查模式未踩中是因为逐镜串行每镜独立提交、单镜跑不完 15 分钟。
- **修复**：超时兜底不能只看 tick 数，先查 `/history` + `/queue` 确认任务是否真的消失：
  ① history completed → 成功收尾；② history error → 失败收尾（带完整错误信息）；
  ③ `/queue` 仍 running/queued → **任务还在路上，`pollTicks` 重置继续等**（长任务不被误杀）；
  ④ 两者都查不到 → 彻底失联才按失败收尾（防永久 running）。
  同时 `MAX_POLL_TICKS` 300 → **2400**（2400×3s = 2 小时硬上限，仅作失联兜底）。
- **坑（How to apply）**：
  ① **轮询超时 ≠ 任务失败**：tick 上限语义是「失联兜底」，不是「任务时长上限」——任何长任务
  （成片 all 合并 / 大范围生成）都必须先确认任务真消失才判失败，否则会误杀成功任务。
  ② **WS execution_success 晚到被 settled 挡住**：一旦 `settleRun(false)` 先触发，后续 WS 成功事件
  永不生效——这是「视频有了但任务显示失败」的表象来源，必须从根上避免提前 settle。
  ③ 重置 tick 后 `onPoll` 仍上报 running/queued，进度 UI 不受影响。
- **验证**：新增 4 用例（超上限仍 in queue → 不判失败 / 之后 history completed → 成功收尾 /
  彻底失联 → 失败收尾 / 超上限 history error → 失败+错误信息完整）；`directorRunFallback.test.ts` 6→9 例，
  全量 239 passed | 1 skipped + `npm run build` 通过。纯前端，刷新 vite dev 即生效。
- **复验**：2026-08-10 用户刷新 vite dev 后《第七号站台》成片模式+全部镜头（11 镜）重跑，任务中心正常显示成功、不再弹「生成失败」，成片可打开。闭环确认。

### 《第七号站台》走查总结（2026-08-10，V1.4+V1.5 合体验证收官）

- **目标**：V1.5 冻结后不先开 V1.6 开发，用真实短片项目完整走一遍，验证 V1.4（稳定生产闭环）+ V1.5（素材库/@资产/继承）的**生产效率与资产一致性**。走查暴露的问题优先于新 UI 功能。
- **项目规模**：3 场景（地铁站台/站务控制室/天台）· 11 镜 · 2 角色 · 3 地点 · 3 道具 · 3 风格，成片约 60s。
- **已完成验证**：
  ① 全局 9 项资产建库/上传参考图顺畅；② 场景池复制/新建/留空走全局符合预期（S1 复制4+独有2 / S2 复制3 / S3 复制4）；
  ③ 镜 1 生效资产徽标四层继承（显式>@>场景默认>全局补缺）全对 + S1 同名去重只显示一条；
  ④ **走查模式逐镜生成 8 镜（镜 4-11）全部 ✓**（#104：地铁站台 4/5/6 + 站务控制室 7/8 + 天台 9/10/11，队列状态全程正确、未触发失败即停）；
  ⑤ **成片模式 11 镜 all 合并出整片 + 播放正常**（#105 复验）。
- **走查暴露并已修复**：#98 继承徽标误判（addShot 预写默认值）/ #99-#100 自动保存尝试与回滚（改回手动保存）/ #101 播放全片加载旧视频（缓存镜头身份）/ #102 左侧镜头删除按钮点不到 / #103 多镜合并内存保护（使用指引）/ #104 逐镜生成自动化·走查/成片双模式 / #105 成片模式误判失败根治。
- **遗留问题清单**（`第七号站台_走查问题清单.md`，全部进 V1.6 候选）：
  Q1 新建项目无命名入口（UI 缺口）；Q2 cast 单选链多角色同框不支持（设计缺陷→V1.6 cast 集合级候选）；
  Q4 自动保存替代方案（localStorage 缓存/编辑即写，生产事故级候选）；Q5 SPA 无独立导出 UI（scene/movie 流式导出无前端入口→V1.6 导出入口候选）。
- **待验项**（非阻塞，可并 V1.6 补验）：镜 2 @命中复验（#98 修复后）、S3 镜 10 全息手表全局补缺、重开项目资产保留、单镜下载/播放队列/取消重生成手验、成片质量抽查（@林澈 跨场景一致性/运镜/接缝/音频）。

### #106 V1.6-C 项目/集命名（2026-08-10，走查 Q1 修复 · V1.6 第一刀）

- **背景**：走查 Q1「新建项目无命名入口」——项目名固定「未命名项目」（home 页可见）、集标题固定「第一集」（工作台顶栏可见），均无改名入口。V1.6 定稿拍板：C 是第一步，极小化 inline 编辑，不增加项目管理页/设置页/命名弹窗。
- **改动**（纯前端，无后端变更）：
  - `stores/workbench.ts`：新增 `renameProject(name)` / `renameEpisode(title)` 原子操作（trim 写回 + touch 置脏），走既有保存链路（Dirty → 💾 保存）。
  - `workbench/WorkbenchView.vue`：
    - **首页项目卡片** `proj-name` 旁新增 ✎ → inline input `[ 项目名____ ] ✓`，`@click.stop` 不触发打开项目；确认 = Enter / ✓ / blur（幂等守卫防 enter+blur 双触发），Esc 取消。改名走 `load→改 name→save` 纯前端往返（复用 `api.loadProject`/`api.saveProject`，不新增后端路由），成功后本地列表即时更新。
    - **工作台顶栏集标题** `wb-project` 旁新增 ✎ → inline input，确认 = Enter / ✓，Esc 取消；写回 store `renameEpisode` → dirty。
  - 样式：`.rename-btn`（✎，hover 才显示）、`.rename-ok`（✓）、`.proj-rename-input` / `.ep-title-input`（inline 输入框）。
- **测试**：store 4 例（项目/集改名写回 + trim + 置脏、空名不生效不置脏）+ workbenchRender 2 例（首页卡片 ✎→输入→✓→saveProject(name 更新)+列表刷新；顶栏集 ✎→输入→Enter→store 写回+dirty）。**245 passed | 1 skipped**，vue-tsc + vite build 全绿。
- **同步**：双目录 md5 已校验一致。**纯前端改动，刷新 SPA 即生效**（已保存项目无需重开）。
- **后续**：V1.6 实施顺序 C → A（成片导出）→ B（多角色 cast 集合），见 `V16_PLAN.md` 定稿。

### #107 V1.6-A 成片导出（2026-08-10，V1.6 核心 · 后端路由 + 前端导出闭环）

- **背景**：V1.5 走查 Q5「SPA 无独立导出 UI」——scene/movie 流式导出只有后端能力，前端无入口。
  V1.6 定稿：**A 是 P0 core**，目标是把镜头生产工作台正式变成「可交付成片的生产工作台」。
  无独立 Export Center 页面；导出 = 顶栏按钮 + 模态框 + 任务中心一个 kind=export 任务。
- **后端**（`director/http_routes.py` + `director/tests/test_export_route.py` 8 测试全绿）：
  - 新增 `minimax_director_export` handler：`node_id`（`[A-Za-z0-9_\-]+` 白名单）、`shot_ids`（有序 string[]）、
    `scope`（movie|scene）、`scene_id`、`fps`（默认 24）、`prefix`、`skip_missing`。
  - 扫描 `minimax_seg_cache/<node_id>/`，用 `_segment_cache_shot_id` 读 meta.json 的 `id`（镜头身份）构建 `id_to_seg`
    （同 id 多段取最新 mtime），**保持 shot_ids 顺序**做 `ordered`；严格模式任一缺失 → `{"error":"not_cached","missing":[...]}`；
    skip_missing 且无可用段 → `nothing_to_export`；ffmpeg 缺失 → `ffmpeg_missing`。
  - 每段 `save_segment_mp4` + `merge_segment_mp4s`（ffmpeg concat，内存≈1-2 段，不触发 #103 内存保护）。
  - 成功返回 `{path, filename, subfolder, type, scope, sceneId, segments, missing}`。
  - 测试 stub 了 torch（含 `_torch.float32`）→ stream_export 导入不炸；严格/skip_missing 两个分支都断言。
- **前端**：
  - `services/comfyApi.ts`：`ExportOptions {fps,prefix,skipMissing}`、`ExportMissingShot`、`ExportResponse`；
    `exportMovie(nodeId, shotIds, opts)` / `exportScene(nodeId, sceneId, shotIds, opts)` → POST `/minimax/director/export`。
  - `stores/workbench.ts`：`TaskRecord` 新增 `kind`（"generate"|"export"，默认 generate）、`exportStage {encoding,total}|null`、
    `exportFile`；`addTask` 加 `kind` 参数；新增 `startExportTask(input)` —— 建 kind=export 任务 →
    api.exportScene/exportMovie → 3s setInterval 客户端估算 `exportStage`（编码 Shot x/N → 合并成片）→
    业务错误（resp.error）标 failed 落 error/errorDetail、成功设 exportFile + finalVideo（`/view?filename=...&type=output`）。
  - `workbench/WorkbenchView.vue`：顶栏「⭳ 导出」按钮**三态**（🟢 全可导出 / 🟡 有缺失或过期 / 🔴 无可导出内容）；
    导出模态框：整部影片/当前场景 radio + 「预计：N 个已生成镜头」+ [取消][开始导出]；
    **导出前检测**：`exportCheckFor` 按参数指纹 `st-stale` 判过期 + 按段缓存判缺失 → `⚠ 有 2 个镜头尚未生成`（列出具体镜头）
    + [仅导出已完成][去生成缺失镜头][取消]；**导出按钮禁用守卫** `wb.running || exportBusy`（后端导出占事件循环，防并发）。
  - `workbench/TaskCenter.vue`：任务中心渲染导出任务——`kind` 徽标（导出=绿/生成=蓝）、`exportStageLabel`
    （编码 Shot 08/8 → 合并成片）、`exportPct`（N+1 步进度条）；取消/重新生成按钮仅 kind=generate 显示；
    空态文案改为「点顶部「▶ 生成」生成镜头，或「⭳ 导出」导出成片」。
- **测试**：新 `frontend/tests/exportTask.test.ts` **7 例**（kind 默认/显式、movie 成功落账 + URL 编码、scene 透传、
  not_cached 业务错误、网络异常、targetShotIds=null 全量）。全量 **252 passed** + vue-tsc/vite build 全绿
  （修了一处 vue-tsc：`v-if="progress || exportStage"` 后分支里 `progress` 无法窄化 → 改可选链）。
  workbenchRender 空态断言同步更新。后端 `python3 -m py_compile` OK + `test_export_route.py` 8 测试全绿。
- **同步**：8 文件双目录 md5 全部一致（http_routes.py、test_export_route.py、workbench.ts、WorkbenchView.vue、
  TaskCenter.vue、comfyApi.ts、exportTask.test.ts、workbenchRender.test.ts）。
- **生效**：后端 1 文件（http_routes.py）需**重启 Comfy Desktop**；前端 5 文件 dev server 重启 + 浏览器硬刷新。
- **已知坑**：导出进度是**客户端估算**（后端 /export 阻塞式单请求，不流式回传）；长导出期间事件循环被占，
  `exportBusy` 锁掉所有生成/导出入口（防并发）。`st-stale` 判定复用 shotFingerprint 参数指纹——「参数已修改需要重新生成」。
- **后续**：V1.6-B 多角色 cast 集合（castId → castIds → cast_assets[]），见 `V16_PLAN.md`。

### #108 V1.6-B-前端 多角色 cast 集合（2026-08-10，V1.6-B 前端半期 · castIds + resolveCollection("cast") + chip UI）

- **背景**：V1.6-B 用户拍板「做彻底」——`@林雪 和 @陈默` 若只 `castId: "林雪"`，H3 实际只拿林雪参考图，
  是**生成质量问题**（身份一致性/多人同框/第二角色服装/脸部特征），不只是 UI。本条目=前端半期；
  后端 `cast_assets[]` 列表见 #109。
- **数据模型**（`models/project.ts`）：Shot 新增 `castIds?: string[]`；保留 `castId?` 兼容旧数据，
  读取时由 `normalizeShotInheritance` 迁移（单值+manual → 数组）。
- **Inheritance Resolver**（`core/inheritanceResolver.ts`）：
  - 新 `shotCastIds(shot)` helper：castIds 优先，回退旧单值 castId。
  - 新 `resolveCollection("cast")` 集合级解析（复用 prop/style 逻辑）：
    **canonical identity 去重** = 显式 castIds ∪ @命中 cast，按名称判重（`trim+lowercase`），场景池优先；
    无显式无 @命中 → 场景默认 → 全局首个（单选兜底，保持旧链）。
  - `resolveOne` 瘦身为仅 location（cast 移到 collection）。
- **store**（`stores/workbench.ts`）：新 `setShotCasts`（数组写回+去重+空回自动）、`toggleShotCast`
  （chip 包含→移除/不含→追加）；`updateShotCast` 改为数组写回；`normalizeShotInheritance` 执行
  单值→数组迁移；`addShot` 不预写 castIds；`cleanupAssetRefs` 处理数组清理。
- **生成链路**：`directorCore.toTimelineShot` 输出 `castIds` + `castId=首元素`；`minimaxH3Adapter.toSegment`
  透传 `castIds`（防御性 `?? []`，旧结构兼容）；`shotFingerprint` castIds 入指纹（参数变需重生成）；
  `timelineImporter` 读 `castIds`（strArr，兼容 snake_case）。
- **UI**（`workbench/WorkbenchView.vue`）：人物区域改 **chip 集合** `[thumb 名 ×] [thumb 名 ×] ＋ 添加人物`
  （popover picker，排除已选）；生效区多徽标渲染；@绑定 cast 用 toggleShotCast。
- **测试**：coreModules 5 例（多角色集合/显式∪@去重/@补足/旧单值兼容）+ workbenchEdit 2 例
  （setShotCasts/toggleShotCast）+ roundtrip 1 例（castIds 贯穿 + castId=首元素）+ 旧断言迁移
  （normalize/rename/remove/renameAsset castId→castIds）。全量 **259 passed** + vue-tsc + vite build 全绿。
- **同步**：12 文件双目录 md5 一致（project.ts、inheritanceResolver.ts、workbench.ts、timeline.ts、
  directorCore.ts、minimaxH3Adapter.ts、shotFingerprint.ts、WorkbenchView.vue、timelineImporter.ts、
  coreModules.test.ts、workbenchEdit.test.ts、roundtrip.test.ts）。
- **生效**：纯前端，dev server 重启 + 浏览器硬刷新。
- **已知坑**：`toSegment` 必须防御性读 `shot.castIds ?? []`——测试/旧结构手搓 TimelineShot 可能无该字段；
  生产链路都经 directorCore 必带。normalize 迁移规则：单值 castId + manual=true → castIds，清 castId；
  单值 + manual=false → 直接清（残留）。
- **后续**：#109 V1.6-B-后端 plan/gen_timeline/executor `cast_assets` 列表，然后 V1.6 冻结 + Golden Path 验收。

### #109 V1.6-B-后端 多角色 cast 集合后端（2026-08-10，V1.6-B 后端半期 · cast_assets 列表 + canonical 去重 + 缓存指纹按列表失效）

- **背景**：承接 #108 前端 `castIds[]`。后端原 `SegmentPlan.cast_asset: GlobalAsset | None` 单值，
  若 `@林雪 和 @陈默` 只带 `castId: "林雪"`，H3 实际只拿林雪参考图 → 第二角色身份/服装/脸部无锚定。
  本条目把数据模型迁成 **cast 列表**，执行器逐一注入角色资产图（天然支持多人同框）。
- **数据模型**（`director/plan.py`）：`SegmentPlan.cast_asset` → **`cast_assets: list[GlobalAsset]`**
  （`field(default_factory=list)`，位于 `location_asset` 前，AST 校验字段顺序 idx=15/16）。
  新增兼容 property `cast_asset`（返回 `cast_assets[0] if cast_assets else None`）——
  旧代码（缓存指纹/一致性检测读单值）不用改。
- **解析**（`director/gen_timeline.py`）：
  - 新 `_dedup_cast_ids(cast_pool, ids)`：**canonical identity 去重**（按名 `trim+lowercase` 判重，保留池顺序）。
  - 段解析块重写：优先读 `castIds`（list，兼容 `cast_ids`）→ 旧单值 `castId`/`cast_id` 迁移单元素 →
    `<picture>` 标签 `@命中 cast`（`_match_asset_exact`）并入 → `_dedup_cast_ids` 合并去重 →
    回退链（提示词命名匹配 → 场景默认 → 全局默认）→ 逐 ID 匹配 `cast_pool` 生成 `seg_cast_assets`（名称去重）。
  - `tag_assets` 去重集合改 `a.id for a in (*seg_cast_assets, location_asset, *extra_assets)`。
  - SegmentPlan 构造 `cast_asset=...` → `cast_assets=seg_cast_assets`（`cast_asset=` 关键字零残留）。
- **执行器**（`director/executor_core.py`，r2v 注入管线）：`getattr(seg, "cast_assets", None) or []`，
  逐资产 `asset_items.append(("角色资产图", name, tensor))`（location 仍单值在后）。
- **缓存指纹**（`director/segment_cache.py`）：`assets` 键从单值 cast_asset 改 **cast_assets 列表逐一指纹**
  （空列表时回退旧单值兼容）；成员数变化（单角色→双角色）指纹必变 → 缓存必失效。
- **测试**（`director/tests/test_segment_cache_identity.py`）：原 `test_fingerprint_asset_image_change_invalidates`
  两处 `seg.cast_asset=` → `seg.cast_assets=[...]`；新增 `test_fingerprint_cast_count_change_invalidates`
  （`[林雪]` vs `[林雪,陈默]` → 指纹必须不同）。单独跑 **8 passed** + py_compile 双绿。
- **坑（预存基建，与本改动无关）**：后端全量 pytest 7 failed 定性为**测试基建污染**——
  各测试文件模块级 `sys.modules.setdefault("torch", stub)`（先收集者胜出，test_export_route 的 stub 无
  `.save` → 缓存写跳过）+ 各自 monkeypatch `_FOLDER_PATHS.get_output_directory`（后 import 覆盖先 import 的，
  缓存写到错误目录）。`identity` 与 `stale` 单独跑全绿，组合跑因基建互相污染。**不改测试基建**，
  V16_PLAN 后端验收门只要求 py_compile（全绿）；VM 无 torch 属预存环境限制。
- **涉及**：`director/plan.py`、`director/gen_timeline.py`、`director/executor_core.py`、
  `director/segment_cache.py`、`director/tests/test_segment_cache_identity.py`。已双目录同步 md5 全 OK、
  清 __pycache__。**改后必须重启 Comfy Desktop**。
- **后续**：#276 V1.6 冻结 + Golden Path 真实整片交付验收（含多角色专项：Shot 05 林雪+陈默 →
  castIds=[林雪,陈默] → 两个参考图进 timeline → H3 生成 → 检查两人身份一致性）。

### #110 V1.6 冻结前 4 个 UI/UX 反馈修复（2026-08-10，Golden Path 走查反馈 · canonical 去重 + label 点击转发根治 + 顶栏项目名）

- **背景**：Golden Path 走查时用户实测反馈 4 个 UI/UX bug：
  ①人物行可重复添加同名角色；②点击人物框任意位置删除第一个人物（判定范围过大）；
  ③「生成当前镜头」想要在镜头设置面板下有独立入口；④首页重命名项目后进工作台左上角仍显示「第一集」。
- **① 重复添加根治**（`frontend/src/workbench/WorkbenchView.vue`）：
  - 新增 `castNameKey(a)`：`trim().toLowerCase()`，空名回退 id——与 `inheritanceResolver.ts:nameKey` 同一判重规则。
  - `castSelectedItems`：先按 canonical 键去重（场景副本+全局原件同名只显示一次）。
  - `castOptions`：场景优先 + canonical 去重 + 排除已选（选中后同名立即从候选消失）。
  - `mergeCastId(shot, assetId)`：同名命中已选角色 → **替换 id 不追加**；否则追加。`addCast`/`bindMention`（@命中 cast）都走它——从「toggle 切换」改「add-only 合并」，杜绝重复。
- **② 点击任意位置删除根治（关键）**：表面只有 × 按钮绑了 `@click`，但实际整行任意点击都删除——
  **根因 = 人物行外层是 `<label class="fld">`（无 `for`），label 会把点击转发给第一个可交互子元素（× 按钮）**。
  修复：cast 行外层 `<label>` → `<div>`（`.fld` 是 class 选择器，样式不变）；× 与「＋ 添加人物」按钮补 `type="button"` + `@click.stop`。
  **坑**：Vue/HTML 中 `label` 无 `for` 时的点击转发是隐式行为，测试能复现（点 `.cast-chip-name` 触发删除）——UI 行容器优先用 `div`。
- **③ 生成此镜入口确认**：镜头设置面板本就有「▶ 生成此镜」按钮（segments 安全路径），补 `type="button"` + title（防表单内默认 submit），无新增。
- **④ 顶栏主标题=项目名**：新增 `projectName` computed（`wb.project?.name ?? episode.title`），顶栏改
  `项目名 / 集标题 ✎` 双段式（`.wb-project-name` 主标题 600 字重 + `.wb-project-ep` 副标题灰小字），
  首页重命名后进入工作台立即同步显示项目名（不再只显示「第一集」）。
- **测试**（`frontend/tests/workbenchRender.test.ts`，+3，共 262）：
  ①同名角色（场景副本+全局原件）只显示一次、选中后同名立即排除；②点 chip 主体不删除、点 × 才删除、addCast 同名替换不重复；
  ③顶栏主标题=项目名、重命名后同步更新。
- **验证**：workbenchRender 67 passed + 全量 vitest **262 passed**（259+3）+ vue-tsc EXIT=0 + vite build 全绿；
  双目录同步 WorkbenchView.vue / workbenchRender.test.ts md5 全 OK。纯前端改动，刷新页面即生效（无需重启后端）。
- **涉及**：`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/workbenchRender.test.ts`。
- **后续**：#276 V1.6 冻结 + Golden Path 真实整片交付验收（多角色专项 + 导出 + 重开项目复验）。

### #111 V1.3-D 参数指纹持久化（2026-08-10，Golden Path 反馈 · 重开项目后仍判「参数已变」）

- **背景**：Golden Path 多角色专项期间用户反馈「改完状态灯没变橙色」——修改 Prompt/角色后镜头应显示
  st-stale（参数已变需重新生成），但没变。排查定位为**设计缺陷**：
  `paramHashes`（生成成功时的参数指纹）是纯内存态，`loadProject` 时直接 `{}` 清空且永不落盘。
  → 刷新页面 / 重开项目后所有已生成镜头的指纹基线丢失，`isShotStale` 的 `if (!stored) return false`
  永远返回 false → 修改参数永远不会变橙。这是「缓存还在但参数已变」判定失效的根因。
- **修复**（纯前端，指纹随项目文件持久化）：
  - `frontend/src/models/project.ts`：`Project` 接口新增 `fingerprints?: Record<string, string>`（shotId → 生成成功时指纹）。
  - `frontend/src/stores/workbench.ts` `loadProject`：`state.paramHashes = { ...(project.fingerprints ?? {}) }`
    —— 重开项目从磁盘恢复指纹基线。
  - `frontend/src/stores/workbench.ts` `recordParamHashes`：`Object.assign` 到 `paramHashes` 后同步写回
    `state.project.fingerprints`（增量合并）——保存项目时经 `serializeProject` 落盘。
  - `deserializeProject`/`serializeProject` 是普通 JSON 往返，fingerprints 字段天然保留，无需改动。
- **测试**（`frontend/tests/workbenchRender.test.ts`，+3，共 265）：
  ①`loadProject` 从 `project.fingerprints` 恢复 `paramHashes`；②`recordParamHashes` 增量写回项目模型 fingerprints；
  ③端到端回归：mock 带指纹的旧项目 → 经「最近项目」卡片打开（openProject 路径）→ 参数未变 st-done 不误报橙 →
  `updateShotContent` 改 Prompt → 状态灯变橙 st-stale。
- **验证**：workbenchRender 70 passed + 全量 vitest **265 passed**（+3）+ vue-tsc EXIT=0 + vite build 全绿；
  双目录同步 project.ts / workbench.ts / workbenchRender.test.ts md5 全 OK。纯前端改动，刷新页面即生效。
- **已知坑（How to apply）**：老项目文件没有 fingerprints 字段 → 需要**重新生成一次**镜头后才会记录新基线，
  之后修改参数才会变橙。这是预期行为（指纹只在「生成成功」时固化）。
- **涉及**：`frontend/src/models/project.ts`、`frontend/src/stores/workbench.ts`、`frontend/tests/workbenchRender.test.ts`。
- **后续**：#276 V1.6 冻结 + Golden Path 真实整片交付验收（多角色专项重新验证 + 导出 + 重开项目复验）。

### #112 V1.6 冻结 + Golden Path 真实整片交付验收通过（2026-08-10，用户实测确认全绿）

- **背景**：V1.6 定位 = 把「镜头生产工作台」升级为「可交付成片的生产工作台」。实施顺序 C（命名）→ A（导出）→
  B（多角色 cast）→ 冻结 → Golden Path。V16_PLAN §5 两条验收剧本全部跑通，**V1.6 正式冻结**。
- **Golden Path 主线验收**（新建项目 → 命名「第七号站台」→ 创建 Scene → 建立人物/地点/道具/风格 →
  生成 8~11 个 Shot → **修改其中一个 Prompt → 确认橙色「需重新生成」** → 重新生成 → 全部缓存完成 →
  导出当前 Scene → 播放 → 导出整片 → 下载 MP4 → 重新打开项目 → 再次播放/导出）——**用户实测全部通过**。
  其中「修改 Prompt → 橙色需重新生成」在 #111 指纹持久化修复后完成最终验证（修复前刷新即丢基线、永远不变橙）。
- **多角色专项验收**：Shot 05 林雪+陈默 → `castIds=[林雪, 陈默]` → 两个参考图进 timeline → H3 生成 →
  检查两人身份一致性——**用户实测通过**（V1.6-B cast_assets 列表 + canonical 去重注入生效）。
- **冻结范围**：V1.6 不再追加功能。V1.6-D 视频替换明确延后 V1.7（牵涉视频资产/缓存/指纹/导出多链路，V16_PLAN §4）。
- **文档收尾**：`V16_PLAN.md` §6 清单 7 项全勾 + Golden Path 验收结果注记；`第七号站台_走查问题清单.md` 加 Q7
  （Golden Path 全量验收通过，V1.6 冻结）。双目录同步。
- **涉及**：`V16_PLAN.md`、`第七号站台_走查问题清单.md`、`CHANGELOG.md`。
- **后续**：V1.7 候选（视频替换）或新大版本规划由用户按生产实际需求决定。

### #113 进入 V1.6.x Production Hardening · 生产观察期（2026-08-10，用户拍板）

- **决策**：V1.6 正式冻结，**暂不开发 V1.7 视频替换**，进入**真实生产观察期**。V1.6 Golden Path 已把最关键的生产闭环跑通
  （项目 → 素材 → 分镜 → Prompt → H3 → 缓存 → 单镜复验 → 场景 → 整片 → MP4），当前最有价值的事不是加功能，
  而是用真实项目连续生产、收集问题，再决定 V1.7 主题。
- **观察期规则**：只修 Bug，不主动增加业务功能。**准入规则** = 连续完成 2～3 个真实项目且无阻断生产 Bug，才正式进入 V1.7。
  **问题分级**：P0 阻断生产 → 立即修 / P1 明显影响生产 → V1.6.x / P2 体验问题 → 累积 / P3 新功能想法 → V1.7 backlog。
- **V1.7 backlog（不预设主题）**：视频替换（当前候选核心）、剪辑/时间线能力、批量生产效率、质量控制——
  届时根据真实生产记录决定哪个痛点最大，就让哪个成为 V1.7 核心。
- **文档**：新建 `V1.6x_PRODUCTION_HARDENING.md`（观察期计划 + checkpoint 登记 + V1.7 backlog + 分级问题登记表）；
  `V16_PLAN.md` 头部状态改「已冻结 → 进入观察期」。双目录同步。
- **涉及**：`V1.6x_PRODUCTION_HARDENING.md`（新）、`V16_PLAN.md`、`CHANGELOG.md`。
- **后续**：用真实作品继续生产，观察期问题按 P0/P1/P2/P3 登记；完成 2～3 个真实项目后触发 V1.7 主题决策。

### #114 V1.7 主题拍板「剧本 → AI 视频制作计划」+《第七号站台》扩展（2026-08-10，用户评审）

- **决策**：V1.7 主题正式定为**剧本 → AI 视频制作计划**（不是视频替换）。核心哲学（用户原话）：**AI 负责把「剧本语言」转换成「生产语言」，你负责最后确认**。
  V1.6 解决「我有 Prompt 怎么稳定生产」，V1.7 解决「我有剧本怎么快速得到可生产的 Prompt + 资产 + 运镜」，两者接起来 = AI 短剧制作工具。
  **本次只写规划，不写代码**（用户明确「先不要写代码，先做一整份规划」）。
- **规划文档**：新建 `V17_PLAN.md`（规划稿，待评审）。要点：
  - 自动化链：剧本 → 自动拆 Scene/Shot → 自动识别资产 → 自动扫描 assets/ 取参考图 → 自动生成 H3 → 自动运镜 → 进入现有 Workbench（aiDraft）→ 人工审核 → 逐镜生成。
  - **P0** = 剧本解析（LLM 理解 + 规则切分/校验，输出对齐 ProjectModel）+ 资产自动匹配（扫描 assets/{characters,locations,props,styles} + 精确匹配 + LLM 别名映射 + 低置信度人工候选）+ 自动 H3 Prompt（结构化 画面/摄影/风格/声音/负面 → 规则组合 + `@资产` 标签 + 模板版本化）。
  - **P1** = 自动运镜：**运镜模板库**（人物登场→Wide→Medium Push-in / 行走→Tracking / 紧张→Slow Push-in / 见物→OTS→Push-in / 对话→双人中景·正反打 / 情绪特写→CU+Slow Push / 环境→Wide Establishing / 追逐→Handheld / 回忆→Slow Dolly / 结尾→Slow Pull-out）+ **跨镜连贯**（方向延续 + 景别衔接，如 Tracking Right → Continue Right + Push-in → OTS → Reverse Shot）。
  - LLM vs 规则 vs 人工确认分工矩阵：AI 只产草稿，**生成视频必经人工确认**（产品底线）。
  - 技术选型：`TextBackend` 文本后端抽象（扩展 `vlm_backends.py` 懒加载释放哲学；本地 Qwen 文本模型 vs OpenAI 兼容 API 双实现待评审）、docx 用 python-docx、资产目录 os 扫描、运镜模板 JSON 规则表。
  - 落地改动清单（后端 `script_parser`/`text_backends`/`script_analyzer`/`asset_scanner`/`prompt_builder_v17`/`camera_template` + 前端剧本导入/候选确认/aiDraft）+ P0/P1 里程碑验收 + 风险边界 + 待评审决策点（V17_PLAN §15）。
- **观察期文档更新**：`V1.6x_PRODUCTION_HARDENING.md` §5 由「候选主题不预设」改为「**V1.7 主题已定：剧本→制作计划** + 其余候选」；§4 checkpoint 项目 #1 登记《第七号站台》（进行中，4 场景 14 镜）。
- **《第七号站台》扩展**：新增第 4 场景「清晨站台」（order 3，后日谈：清晨林澈穿旧站务制服回站台、旧地铁卡亮暖光、上车前站台幻影告别）+
  3 镜（镜头 12-14，id `a0000000…000c/000d/000e`，首镜 continuity=none 后两镜 auto，castId 显式绑定林澈）。
  项目现为 **4 场景 14 镜**：地铁站台 6 / 站务控制室 2 / 天台 3 / 清晨站台 3。
- **涉及**：`V17_PLAN.md`（新）、`V1.6x_PRODUCTION_HARDENING.md`、`{ComfyUI input}/minimax_studio/projects/第七号站台/project.json`、`CHANGELOG.md`。双目录同步（V17_PLAN + V1.6x 已同步）。
- **后续**：V1.7 开发节奏待评审 + 观察期数据；评审决策点见 `V17_PLAN.md §16`（P0 是否含 UI / 运镜模板规模 / aiDraft 留存 / 开发节奏）。

### #115 V1.7 规划拍板：显存生命周期硬约束 + P0-C 改名 + 本地 Qwen 先行（2026-08-10，用户评审）

- **决策**：用户评审通过 `V17_PLAN.md` 整体规划，**保留 P0/P1 拆法**。三条关键修正：
  1. **显存生命周期 = V1.7 硬性架构约束（不是可选优化）**：本地 Qwen（TextBackend）与 MiniMax H3 **不允许同时常驻 GPU 显存**。RTX 5080 24GB + ComfyUI 跑 H3，Qwen 只做「剧本→结构化生产数据」，任务轻量没必要常驻。用户已实际踩过 6.3GB 内存保护 / 视频生成资源问题，这次从架构上避免显存竞争。
  2. **P0-C 改定位「结构化生产 Prompt 草稿」**：不叫「自动生成最终 H3 Prompt」——AI 产出 Visual/Camera/Style/Sound/Negative 分区草稿进 AI Draft，**不直接成为 H3 黑盒 Prompt 生成器**；以后 H3 模板换版本不会推翻剧本解析系统。
  3. **LLM 后端开发顺序：第一阶段先本地 Qwen**（`TextBackend` 只做 LocalQwen），最终双实现（LocalQwen + OpenAI 兼容），业务层同一接口。理由：SPA 已是本地 ComfyUI 生产链，「剧本→本地解析→本地资产→本地 H3→本地导出」全程不上传第三方。
- **显存安全门（V17_PLAN 新增 §8）**：
  - 互斥原则：Qwen 与 H3 不允许同时持有 GPU 模型；H3 生成中禁止启动 Qwen 推理，Qwen 分析中禁止启动 H3。
  - 服务层生命周期 `with text_backend.session(): plan = analyze_script()` → 退出卸载模型 + `torch.cuda.empty_cache()` → 再进 H3。
  - 产品内两阶段运行：**AI 分析模式**（Qwen → Script→Plan→资产→Prompt→Camera→AI Draft→释放）/**视频生成模式**（H3 逐镜生成，与 V1.6「逐镜生成」完全契合）。用户无需关心显存。
  - 落地：`text_backends.py` 提供 `session()` 上下文管理器 + 互斥门（占用则等待或明确报错，不静默）；卸载必须含 `torch.cuda.empty_cache()`。
- **V17_PLAN.md 同步更新**：状态「规划稿」→「**已拍板**」；§0 表格加显存生命周期 / LLM 后端顺序 / P0-C 定位三行；§3 自动化链加「⛔ 显存安全门」环；§8 新章节（显存安全门）；P0-C 改名「结构化生产 Prompt 草稿」；§11 技术选型改「第一阶段本地 Qwen」+ §12 落地清单 text_backends 加 session/互斥；§13 里程碑 P0 加显存安全门验收；§15 风险表更新；§16 决策点 1 标记已拍板；§17 实施清单加显存安全门项。
- **待定决策点（V17_PLAN §16，剩余 4 项）**：P0 是否含剧本导入 UI / 运镜模板库初始规模 / aiDraft 采纳后留存 / 开发节奏（评审通过即开发 vs 先完成观察期 2~3 项目）。
- **涉及**：`V17_PLAN.md`、`CHANGELOG.md`。

### #116 V1.7 最终拍板：4 决策点全定 + GPU 模型状态检查（2026-08-10，用户评审）

- **决策**：用户对 V1.7 最后 4 项决策点全部拍板，另加 1 项安全机制：
  1. **P0 含「剧本导入 UI」：P0 就做完整 UI**（📄 导入剧本 → 选剧本文件 → 选 assets 文件夹 → AI 分析 → 资产匹配确认 → 生成 AI Draft → 进 Workbench），**不做命令导入**。理由：V1.7 核心入口，用户目标已从「ComfyUI 视频生成工作台」变成「剧本 → 分镜计划 → AI 视频 → 成片」生产工具，不能仍是「技术工具」体验。Golden Path 入口即「📄 导入剧本」。
  2. **运镜模板规模：先 10 条基础模板 + 补 4~6 条常用**（战斗 / 蒙太奇 / 空镜转场 / 环绕 / 升降），不宜一开始做几十种。V17_PLAN §7 已补 5 条常用模板表。
  3. **aiDraft 留存：保留为历史元数据，不影响正常 Shot**（排查「AI 生成 vs 人工修改」有价值），**不参与指纹 / 状态灯 / 导出 / 续接逻辑**。
  4. **开发节奏：先观察期跑 2~3 个真实项目，再正式开 V1.7**（V1.6 刚冻结，先确认生产链稳定，比马上加功能更稳）。
- **新增安全机制（GPU 模型状态检查，V17_PLAN §8.5）**：**Qwen 分析完成 ≠ 立即开始 H3**，必须经过强制关卡：
  ```
  Qwen session 结束 → del model → gc.collect() → torch.cuda.empty_cache()
  → 确认 GPU model registry = empty → 才允许启动 H3
  ```
  若检测到 Qwen 未完全释放：⚠️ 提示「AI 分析模型尚未完全释放，正在清理显存……」——**而不是直接启动 H3**。检查对象=ComfyUI `comfy.model_management` 已加载模型 registry + 自定义 TextBackend 持有引用双重确认。目的：从架构上杜绝「理论上 Qwen 已卸载、为什么 H3 又 OOM」这类最难排查的问题。
- **硬件口径**：用户确认其机器为 **RTX 5080**（用户口径 16GB 显存，与此前 #115 记录「24GB」有出入——架构约束不变：无论显存多大，Qwen 与 H3 都不同时驻留 GPU，Qwen 一次性把剧本变成生产计划后彻底退出 GPU，H3 再独占显存）。
- **V17_PLAN.md 同步更新**：§0 拍板表加 5 行（GPU 状态检查 / 剧本导入 UI / 运镜模板规模 / aiDraft 留存 / 开发节奏）；§7 补「常用补充模板 4~5 条」；§8 新增 §8.5 GPU 模型状态检查；§9 aiDraft 保留为历史元数据；§11 剧本导入入口改「P0 就做完整 UI」；§13 Golden Path 更新为用户拍板流程；§14 开发节奏=先观察期；§16 改为「决策点拍板记录（全部已拍板）」；§17 实施清单加「P0-剧本导入完整 UI」+ GPU 状态检查。
- **涉及**：`V17_PLAN.md`、`CHANGELOG.md`。

### #117 观察期项目路线确定：《第七号站台》冻结为回归项目 + 项目 #2/#3/#4（2026-08-10，用户拍板）

- **决策**：**《第七号站台》冻结**，不再继续大规模生成——它已验证多轮，继续用容易把「已熟悉的路径」当成稳定性；角色转为 **Golden Path 回归项目**（以后 V1.7/V1.8 改动拿它跑关键回归）。
- **观察期项目路线（用户 2026-08-10 拍板）**：
  - **项目 #2 完全新项目**：换一套全新条件（新剧本/新人物/新地点/新道具）；8~15 Shot；**至少一次多角色同框**；**至少一次 R2V/FL2V 续接**；改 Prompt→橙→重生成；删除/复制/重命名资产；导出 Scene；导出整片；关闭→重开→播放/导出——一次覆盖 V1.4~V1.6 核心生产链。
  - **项目 #3 压力测试**：15~30 Shot / 多 Scene / 同一地点重复 / 同一角色跨 Scene / 2~3 人同框 / 多道具风格 / 连续 R2V/FL2V / 中途改 Prompt / 部分失败重生成 / 整片导出。目的**不是成片质量而是压力测试生产系统**。
  - **项目 #4 发布项目**：真正准备发布/使用的短剧；前两个基本无阻断 Bug 才做，此时才问「这个工具能不能真的拿来生产」。
  - **达标**：#2+#3+#4 无新的结构性问题 → V1.6 基本进入稳定生产阶段 → **V1.7 正式开工**。
- **V1.6x_PRODUCTION_HARDENING.md 更新**：§4 项目 #1 标冻结+回归项目定位；新增「观察期项目路线」表（#2/#3/#4）；§5 开发节奏改「先完成观察期 2~3 个真实项目再正式开 V1.7」。
- **下一步**：新建观察期项目 #2 → 完整跑一遍 → 记录所有问题 → 连续 2~3 个项目稳定 → 正式启动 V1.7。
- **涉及**：`V1.6x_PRODUCTION_HARDENING.md`、`CHANGELOG.md`。

### #118 观察期项目 #2《山雨客栈》设计文档定稿（2026-08-10，用户拍板方向后产出）

- **背景**：用户拍板项目 #2 方向 = **古风武侠** + 2 人同框为主 + 1 次 3 人同框 + 8~10 镜 + **剧本+资产全交给我**（AskUserQuestion 回答）。题材与《第七号站台》（雨夜城市悬疑）彻底拉开差异。
- **产出**：新文档 `V1.6x_PROJECT2_山雨客栈.md`（设计稿）：
  - **剧本**：雨歇黄昏剑客走进「山雨楼」→ 老板娘递茶 → 夜半蒙面刺客夺剑 → 三人对峙打斗 → 天明后院无言相对。3 角色（沈青崖/柳如烟/蒙面人）× 2 场景（山雨楼外/客栈大堂）+ 1 风格增强场景（后院石阶）× 2 道具（旧剑匣/青瓷茶碗）× 1 风格（古风水墨）。
  - **9 镜**：每镜带 `T2V/R2V` 续接标注 + 明确运镜（Slow Push-in / Tracking / Shot-Reverse-Shot / Orbit 环绕 / Handheld Tracking 战斗 / Slow Pull-out）+ 五区 Prompt（visual/camera/style/sound/negative）+ 负面词。
  - **续接**：**6 处 R2V**（02/03/04/05/07/08）+ 3 处独立 T2V（01 空镜起/06 悬念转折/09 清晨收尾）——故意用 T2V 断掉连续段，验证「叙事段落切换 + 悬念插入」的独立起镜。
  - **多角色同框**：03 双人同框、04↔05 完整正反打、**07 三人同框（环绕运镜）**、08 三人同场景不同景别层次。
  - **资产参考图提示词**（§6）：5 张必做（3 角色 + 2 地点）+ 3 张可选（后院/旧剑匣/古风水墨），中英双语，供本地 ComfyUI 出图。
  - **验收映射表**（§5）：9 项验收（新资产全新建 / 9 镜 / 多角色同框 / R2V 续接 / 改 Prompt→橙→重生成 / 资产增删改 / 导出 Scene / 导出整片 / 关闭重开复验）→ 对应具体镜头/操作。
- **已同步部署目录**（md5 校验一致）。
- **下一步（给用户）**：本地出图 5~8 张 → SPA 建项目传资产 → 按分镜表逐镜填 Prompt → 逐镜生成 → 跑验收映射 → 问题登记 `V1.6x_PRODUCTION_HARDENING.md §6`。
- **涉及**：`V1.6x_PROJECT2_山雨客栈.md`（新）、`CHANGELOG.md`。

### #119 观察期项目 #2《山雨客栈》资产入库 + project.json 落地（2026-08-11）

- **背景**：用户按 §6 提示词在本地 ComfyUI 出好 8 张图，放 `D:\夸克网盘\山雨客栈`，说「其他的你帮我操作吧」——我来完成：资产入库 + 项目结构 + SPA 可打开。
- **资产入库**：8 张 PNG 复制到 `ComfyUI-Shared\input\minimax_studio\assets\`，md5 与源目录逐一校验一致（沈青崖/柳如烟/蒙面人/山雨楼外/客栈大堂/后院石阶/旧剑匣/古风水墨）。「青瓷茶碗」用户没出图 → 该道具跳过，全片 Prompt 里茶碗作为场景元素描述而非资产引用（§3 本就标注可选）。
- **project.json**：`ComfyUI-Shared\input\minimax_studio\projects\山雨客栈\project.json`。
  - **目录名 = JSON id = "山雨客栈"**（吸取《第七号站台》教训：其目录名与 json id 不一致，前端 openProject 用目录名、saveProject 用 json id，不一致会保存分裂；本次统一避免）。
  - 1 集 3 场景 9 镜：山雨楼外(2 镜)/客栈大堂(6 镜)/后院石阶(1 镜)。
  - **全局资产 8 项**：3 角色 + 3 地点 + 1 道具（旧剑匣）+ 1 风格（古风水墨），id 全部 `10000000-…` 段。
  - **场景池资产**：每场景 assets 内嵌 cast/locations/props/styles（id 用 `20000000-…` 段），defaultCastId/defaultLocationId 指向场景池——与前端 `resolveCollection("cast")` canonical 去重逻辑（#108）匹配。
  - **9 镜全部预填五区 Prompt**（visual/camera/style/sound/negativePrompt），每镜 cameraText 带明确运镜（Slow Push-in / Tracking / Push-in / Shot-Reverse-Shot / Handheld + Push-in / Orbit / Handheld Tracking / Slow Pull-out）——符合「每镜必有运镜」H3 规范。
  - **续接设计**：镜 01/06/09 `continuityMode:"none"`（独立 T2V：首镜/悬念转折/清晨收尾），镜 02/03/04/05/07/08 `continuityMode:"auto"`（R2V 续接）——6 处 R2V 对应设计稿 §4。
  - **castIds 多角色**：镜 03 双人（沈青崖+柳如烟）、镜 07 三人同框（沈青崖+柳如烟+蒙面人）、镜 08 战斗三人、镜 09 双人收尾；全部 `castManual:true`。
  - **字段完整性校验通过**：JSON 合法、shot/scene/asset 必填字段齐全、castIds 引用全局资产 id 有效、sceneId 归属一致、referenceImage/imageFile 全部指向已存在文件、continuityMode/taskType 取值合法。
- **验收映射（§5）一次就绪**：改 Prompt→橙→重生成（选 07）、资产增删改（素材库）、导出 Scene/整片、关闭重开复验——这些操作现在都有真实项目可跑。
- **下一步（给用户）**：SPA 打开项目「山雨客栈」→ 逐镜走查 Prompt（或直接按稿生成）→ 逐镜生成 01→09 → 中途改 07 验证橙灯重生成 → 资产增删改 → 导出 Scene/整片 → 关闭重开复验；发现问题登记 `V1.6x_PRODUCTION_HARDENING.md §6`。
- **涉及**：`ComfyUI-Shared/input/minimax_studio/projects/山雨客栈/project.json`（新）、`ComfyUI-Shared/input/minimax_studio/assets/`（+8 PNG）、`CHANGELOG.md`。

### #120 V1.7 正式立项 + Phase 0 交付（text_backends + 显存安全门，2026-08-11）

- **背景**：用户正式拍板启动 V1.7「剧本 → AI 视频制作计划」（Script-to-Plan），并明确 **第一提交只做 Phase 0：Local Qwen + TextBackend + 显存安全门**——先证明「Qwen 稳定分析 → 强 JSON → 完整卸载 → H3 生成链不受影响」，再推进剧本解析。
- **V17_PLAN.md 全面更新为正式立项**（642 行）：
  - 顶部新增 ⛔ **产品底线**：「V1.7 不负责『替用户拍片』，只负责把剧本转换成一套经过人工审核即可生产的 AI 视频制作计划」。
  - §0 拍板结论改正式立项口径 + §0.1 范围控制（只做 P0-A/P0-B/P0-C/P1-D 四件事 + ❌ 不做清单：自动参考图/视频/剪辑/配乐/字幕/改成片/H3参数优化/无限发挥）+ §0.2 最终目标（`作品/script.md + assets/{characters,locations,props,styles}` → 全流程）。
  - §4 **Qwen 不决定镜头数量**（Qwen 理解动作/情绪/对话 → 规则按动作密度拆镜，防「一段剧情拆 17 镜」）。
  - §5 资产三级匹配链路（精确 → alias → Qwen 语义 → 置信度人工确认）+ **⛔ AI 永远不能覆盖已有资产**（禁止创建「林雪_2」）。
  - §6 **Prompt Template Registry**（`prompt_templates/v1.json`，Shot 存 `promptTemplateVersion`，V1.8→h3-v2 老项目不重生成）。
  - §7 **previousCameraState 跨镜状态机**（movementDirection/shotSize/cameraPosition/subjectDirection，下一镜参考上一镜）。
  - §9.0 **「AI 制作计划」预览阶段**（每镜 ✓/⚠ + AssetConfirmDialog + ShotReviewBadge「🤖 AI 草稿」+ 采纳后即普通 Shot）。
  - §13 里程碑改为 **Phase 0-5 开发顺序 + 每阶段独立验收 PASS 才继续**；§14 开发节奏改「正式立项，Phase 0 先行」；§16 追加 12 条正式立项拍板；§17 实施清单 Phase 0 先行勾选。
- **Phase 0-A 后端交付**：`director/text_backends.py`（新，14KB）：
  - `TextBackend` 抽象（`analyze_text` / `analyze_json` / `close` / `session()`），业务层只依赖此接口。
  - `LocalQwenBackend`（transformers fp16，`models/text` 目录，ComfyUI Desktop Python 3.13 零编译主路径）。
  - 强 JSON 输出：`_extract_json_block`（剥代码块/去尾逗号/去注释）+ `RETRY_HINT` 失败重试一次——复用 qwen_vl_feedback 容错模式，独立实现避免反向依赖。
  - `TextSession` 显存安全上下文（§8）：进程内互斥锁 + ComfyUI GPU model registry 检查（H3 占用拒绝启动）+ 退出即 `close() → empty_cache()`。
- **Phase 0-B 互斥门接入**：`executor_core.py::execute_director_plan_core` 顶部加 H3 侧检查——`text_backend_active()` 为 True 时抛「AI 分析模型正在占用 GPU」；检查失败只告警不阻断 H3 生产链（函数内轻量导入）。
- **Phase 0-C mock 测试**：`director/tests/test_text_backends.py`（新）——**15 项全绿**，覆盖：强 JSON 各种容错格式 / analyze_json 重试链 / session 生命周期释放 / 并发 session 拒绝 / GPU registry 非空拒绝且锁释放 / LocalQwen 加载-分析-卸载-`empty_cache` / 缺 config.json 报错 / 模型目录发现。全 mock（假 torch/comfy/transformers/folder_paths），`python3 director/tests/test_text_backends.py` 直跑 + pytest 兼容。
- **py_compile 双绿**：`text_backends.py` / `executor_core.py` / `test_text_backends.py`。
- **双目录同步 md5 全一致**：`director/text_backends.py`、`director/tests/test_text_backends.py`、`director/executor_core.py`、`V17_PLAN.md`。
- **下一步（Phase 1 前置）**：需重启 Comfy Desktop 后：①把本地 Qwen 文本模型（含 config.json 目录）放到 `ComfyUI/models/text/`；②走 `with text_backend.session():` 验证真实模型分析→强 JSON→卸载→`torch.cuda.empty_cache()`→registry empty→H3 正常生成（Phase 0 独立验收四步）；验收 PASS 才进 Phase 1 剧本解析。
- **涉及**：`V17_PLAN.md`（更新）、`director/text_backends.py`（新）、`director/executor_core.py`（+互斥门）、`director/tests/test_text_backends.py`（新）、`CHANGELOG.md`。

### #121 V1.7 文本后端改 Ollama API 首选（OllamaBackend + 进程级显存互斥，2026-08-11）

- **背景**：用户在 `D:\ollama_models` 确认本机已有 `qwen3:14b`（Ollama 管理，GGUF 9.27GB，manifest digest `sha256:a8cc1361...`），**拍板 V1.7 文本后端改为 Ollama API 优先**：
  - 架构 = `TextBackend → OllamaBackend（首先实现，qwen3:14b）→ OpenAICompatibleBackend（后续）`；**不要把架构写死成 LocalQwenBackend**；`LocalQwenBackend`（transformers fp16）降为备用。
  - **不折腾 GGUF 转 Transformers，也不装 llama-cpp-python**（ComfyUI Desktop Python 3.13 无 cp313 wheel 是已知坑）。
  - Qwen3:14B Q4 够用：剧本实体识别 / 人物地点道具提取 / 别名归一 / Scene-Shot 结构化 / 动作情绪对话提取 / 五区 Prompt 草稿 / 辅助资产匹配。
  - 保持「Qwen → 结构化分区（visual/camera/style/sound/negative）→ Prompt Builder → H3 Prompt」链路，**不让 Qwen 直接输出最终 H3 Prompt**。
  - **Phase 0 第一项 = OllamaBackend + Qwen3:14B 连通性/显存互斥验收**，先不要一上来写完整剧本解析。
- **`director/text_backends.py` 重构（OllamaBackend 首选）**：
  - 新增 `OllamaBackend`：HTTP API（`urllib.request` 纯标准库），`base_url` 默认 `http://localhost:11434`（`OLLAMA_HOST` 环境变量可覆盖），`model="qwen3:14b"`（`TEXT_LLM_MODEL` 可覆盖），`keep_alive="5m"`。
    - `ping()`：`GET /api/tags` 探测服务可达。
    - `analyze_text()`：`POST /api/generate`，`stream=False` + `options={num_predict, temperature}` + `keep_alive`；返回 `response`。
    - `loaded_models()`：`GET /api/ps` 返回驻留模型名列表。
    - `unload()`：`POST /api/generate {prompt:"", keep_alive:0}` 请求立即卸载。
    - `close()`：unload + `/api/ps` 确认 qwen 不再驻留（驻留则打 warning）。
    - `preflight()`：进入 session 前 ping Ollama + 其他驻留模型告警。
  - **`TextSession` 进程级显存互斥（§8.5 更新）**：进入 → 进程内互斥锁 + ComfyUI GPU model registry 检查 + `preflight()`；退出 → `close()`（Ollama 卸载 keep_alive=0 + /api/ps 确认）→ 释放锁 → `torch.cuda.empty_cache()`。
  - `create_default_text_backend()` 改为 **直接返回 OllamaBackend**（V1.7 首选）；`LocalQwenBackend` / `find_default_text_model_dir()` 保留为备用路径。
  - 关键认知（V17_PLAN §8.5）：**Ollama 是独立进程，`del model / gc.collect()` 只对 Transformers 语义有效；对 Ollama 必须以「进程级卸载 + /api/ps 驻留确认」为准**。
- **`executor_core.py` H3 侧互斥门保持**（`text_backend_active()` 检查，Ollama 分析中拒绝启动 H3）。
- **`director/tests/test_text_backends.py` 扩展为 28 项全绿**（Ollama 全链路 mock，monkeypatch `urllib.request.urlopen` 假 Ollama 服务）：
  - analyze_text payload（model/prompt/stream/keep_alive/options）/ temperature / analyze_json 强 JSON / retry；
  - ping 可达/不可达 / loaded_models / unload keep_alive=0 / close 卸载+确认；
  - session 全链路（进入 ping → 分析 → 退出 keep_alive=0 卸载 → /api/ps 确认 → 锁释放 → empty_cache）；
  - 互斥拒绝：GPU registry 非空 / Ollama 不可达 / 并发 session；退出可重入；
  - create_default_text_backend 返回 OllamaBackend + `TEXT_LLM_MODEL`/`OLLAMA_HOST` 环境变量覆盖；`_default_ollama_base`。
  - LocalQwen 15 项原测试全部保留。
- **py_compile 双绿**：`text_backends.py` / `test_text_backends.py`。
- **V17_PLAN.md 全面更新为 Ollama 架构**：§0 表格（LLM 后端/显存生命周期/GPU 状态检查/开发节奏）、§8/§8.5（进程级互斥 + keep_alive=0 + /api/ps 确认 + 三重确认）、§11 技术选型、§12 落地清单、§13 Phase 0 交付物与验收、§14/§15/§16/§17（追加第 13 条拍板：Ollama API 首选）。
- **双目录同步 md5 全一致**：`director/text_backends.py`、`director/tests/test_text_backends.py`、`V17_PLAN.md`。
- **下一步**：重启 Comfy Desktop + 启动 Ollama（`ollama serve`）后做 Phase 0 真实连通性验收：`with text_backend.session():` → 真实 Qwen3:14B 分析剧本（强 JSON）→ 退出 keep_alive=0 卸载 → `/api/ps` 确认空 → H3 正常生成（互斥门拦截/放行验证）。PASS 才进 Phase 1 剧本解析。
- **涉及**：`V17_PLAN.md`（更新）、`director/text_backends.py`（重构）、`director/tests/test_text_backends.py`（扩展）、`CHANGELOG.md`。

### #122 Phase 0 真实验收脚本 `tools/phase0_ollama_check.py`（2026-08-11）

- **背景（Why）**：#121 完成了 OllamaBackend 代码 + 28 项 mock 测试，但 mock 只证明「假 Ollama 服务」下逻辑正确；V17_PLAN §13 Phase 0 独立验收第 1 项（真实 Ollama ping → 真实 Qwen3:14B 强 JSON → keep_alive=0 卸载 → /api/ps 确认 → registry empty → 互斥门放行）**没有可执行入口**——用户问「这一步在哪」时发现只有文档没有脚本，于是落地成 `tools/phase0_ollama_check.py`。
- **脚本功能（不 mock，直连真实 Ollama）**：
  - ① `_check_ollama_reachable`：`ping()`（/api/tags）→ Ollama 不可达直接 FAIL 提示 `ollama serve`；
  - ② `_check_model_installed`：读 /api/tags 确认 `qwen3:14b` 已拉取，缺失提示 `ollama pull qwen3:14b`；
  - ③ `_check_real_analysis`：`with backend.session():` 进 session → **真实分析《山雨客栈》迷你剧本片段**（强 JSON 校验 scene/characters/locations/props/actions 五字段全在才算 PASS）→ 验证 `text_backend_active()` 为 True；
  - ④ `_check_unload_and_lock`：session 退出后 `text_backend_active()` 复位 False + `loaded_models()`（/api/ps）确认 qwen3:14b 不再驻留；
  - ⑤ `_check_gpu_registry`（仅 ComfyUI Python 环境执行，非 ComfyUI 自动跳过）：`gpu_models_loaded() == 0`；
  - ⑥ `_check_mutex_gate`：互斥门反向验证——手动 `_acquire_text_active()` 激活后检测 `text_backend_active()==True`（模拟 H3 侧检查会拦截），finally 释放并确认复位。
  - 退出码：0 = 全 PASS；1 = 有 FAIL（缺 Ollama / 模型缺失 / 强 JSON 失败 / 卸载未确认）。
- **两种运行方式**：
  - 系统 Python（只验 Ollama 链路，⑤ 跳过）：`python tools/phase0_ollama_check.py`
  - **ComfyUI Desktop Python（推荐，含 ⑤ registry 检查）**：`"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools/phase0_ollama_check.py`
- **自测**：用假 Ollama 服务（monkeypatch `urllib.request.urlopen`）验证 PASS 场景（5/5 PASS 退出码 0）与三个 FAIL 分支（Ollama down / 模型缺失 / 强 JSON 失败均退出码 1）；`py_compile` 通过。
- **双目录同步 md5 全一致**：`tools/phase0_ollama_check.py`。
- **下一步**：用户在 Windows 本机确认 `ollama list` 有 `qwen3:14b` 后，跑上面命令做**真实验收**；PASS 后进 Phase 1 剧本解析。
- **涉及**：`tools/phase0_ollama_check.py`（新增）、`CHANGELOG.md`。

### #123 Phase 0 真实验收 PASS（用户实测 5/5 + H3 正常生成，2026-08-11）

- **结果（用户实测）**：`tools/phase0_ollama_check.py` 用 ComfyUI Desktop Python 跑出 **5/5 PASS**——①Ollama ping 可达 ②`qwen3:14b` 已拉取（本机 `ollama list` 共 3 个模型：`huihui_ai/qwen3.5-abliterated:9b`/`qwen3:14b`/`shaw/dmeta-embedding-zh:latest`）③`with text_backend.session():` 真实 Qwen3:14B 分析《山雨客栈》迷你剧本 → 强 JSON 五字段全对（scene=清晨山雨客栈、characters=林雪/陈默、locations=山雨客栈、props=伞/茶壶、actions=推门收伞环视/抬眼搭话/对坐交谈）④退出后 `text_backend_active()` 复位 False + `/api/ps` 确认 qwen3:14b 不再驻留 ⑥互斥门反向验证通过。
- **⑤ 跳过说明**：独立脚本进程 import 不到 `comfy` 包（它在 ComfyUI 根目录），且即便导入也查不到 ComfyUI **主进程**的 GPU registry——⑤的 registry 级联验证由 28 项 mock 测试（GPU busy 拒绝）与 ⑥ 互斥门覆盖；真实场景由用户重启 Comfy Desktop 后 **H3 实测兜底**。
- **H3 实测**：用户重启 Comfy Desktop → SPA 正常生成一镜 H3 → **互斥门放行、显存无残留、未报「AI 分析模型正在占用 GPU」拦截**。V17_PLAN §13 Phase 0 四项独立验收全部满足。
- **V17_PLAN.md 更新**：§13 Phase 0 追加「✅ 验收结果（2026-08-11 用户实测 PASS）」；§17 实施清单 Phase 0 新增勾选行「真实验收脚本 + 用户实测 PASS」。
- **结论**：**Phase 0 验收通过，正式进入 Phase 1 剧本解析**（script_parser 规则拆镜 + script_analyzer Qwen 强 JSON + 规则校验兜底）。
- **涉及**：`V17_PLAN.md`（更新）、`CHANGELOG.md`。

### #124 V1.7 Phase 1 · Commit 1：ProductionPlan Schema v1 + 规则拆 Scene/Shot（2026-08-11）

- **背景（Why）**：Phase 0（Ollama + TextBackend + 进程级显存互斥）验收 PASS（#123）后进入 Phase 1 剧本解析。用户 2026-08-11 拍板 **Commit 1 = Schema 先行 + 规则拆 Scene/Shot**——ProductionPlan Schema v1 是 parser 的**契约**（不是 Commit 3）；**规则确定镜头结构，Qwen 只负责语义理解**（V1.7 全期不变，见 V17_PLAN §4 核心原则）。
- **`director/production_plan.py`（新，Schema v1 = parser 契约）**：
  - dataclasses：Dialogue{speaker,text} / Character{name,role} / Prop{name} / Shot{shot_id,source_text,duration_sec,characters[],props[],actions[],emotion,dialogue[],visual_intent} / Scene{scene_id,title,location_name,time,weather,shots[]} / ProjectInfo / Validation / ProductionPlan + to_dict/from_dict + validate() + dump_json/load_json/ensure_plan_file。
  - 常量：MIN_SHOT_DURATION=2、MAX_SHOT_DURATION=8、MAX_SHOTS_PER_SCENE=8、SCHEMA_VERSION="v1"。
  - validate()：errors=无场景 / 缺 scene_id / Scene 无 Shot / 重复 shot_id；warnings=超 8 镜 / source_text 空 / duration 越界 / dialogue.speaker 不在角色表；status=valid|invalid。
  - **刻意不放**（分属 Phase 2/3/4）：castIds / locationId / assetId / generationMode / h3Prompt / camera / refs。`duration_sec`=成片时间轴秒数 ≠ H3 generation duration（Phase 3 映射）。dialogue.speaker→characters[].name→Phase 2 Asset Registry→castIds[]，**Phase 1 绝不生成 Asset ID**。validation 只做机器校验，人工审核=SPA render.status=review。
- **`director/script_parser.py`（新，纯规则拆 Scene/Shot，不碰 Qwen）**：
  - 读取：read_script 编码容错 utf-8-sig→utf-8→gb18030→gbk（Phase 1 只支持 .md/.txt；docx 推迟 Phase 1.5，不给 ComfyUI Python 加 python-docx）。
  - Scene 切分（尽量少切）：markdown 标题 / 「第X场·幕·景」中文序号 / slugline（内|外|INT.|EXT. 地点 时间）/「时间·地点·天气」标签行 / 无标题兜底单场景；**空场景跳过不占编号**（scene_seq 计数器，修「文档大标题产生空场景占用 scene_01」问题）。
  - Shot 规则（优先级）：显式镜头标记（镜头/Shot N）硬切 → 时间/地点变化 → 人物行为变化（**强动作动词**，排除 停/顿/答 等弱动词，修「水声没有停过」「滴答」误拆镜）→ 对白跟随 → 默认合并；硬上限 MAX_SHOTS_PER_SCENE=8 超出合并相邻最短并记 warning（merge warning 与 validate() 分离，修 warning 丢失）。
  - 时长估算：estimate_duration_sec 默认 5s，字数/动作密度 +1s，clamp [2,8]。
  - 实体提取（规则层轻量，语义补全交 Qwen Commit 2）：`_paren_names`「名（备注）」finditer 全取 + 按动词长度降序去前缀（修「查看沈青崖」算进括号人名）；`_collect_candidate_names` 场景级候选；`_dialogue_from_line` 标准「X：/X说：」+ **行首最长候选名前缀匹配**（修「林雪点头，走到他对面坐下：」speaker 未识别）；复杂句 speaker 留空交 Qwen。
  - source_text 逐字保留原文不改写。
- **`director/tests/test_script_parser.py`（新，15 函数 58 断言）**：schema round-trip（顶层字段 / 刻意不放 castIds 等 / duration 越界 warning / speaker 不在角色表 warning / 重复 shot_id error）、Scene 切分（无标题 / markdown 标题 / 第X场 / slugline / 标签行）、Shot 规则（镜头标记 / 时间变化 / 上限合并 / 默认合并）、时长 clamp、对白 / 角色提取、原文保留、文件读取编码容错、Phase 1 空字段（visual_intent/actions/emotion/props 空）。
- **运行结果**：直接 `python3 director/tests/test_script_parser.py` → **58 PASS / 0 FAIL**；`py_compile` 双绿。
- **真实样例《山雨客栈》**：2 场景 5 镜 26s——scene_01 清晨·客栈大堂 2 镜（环境 / 林雪推门收伞）、scene_02 午后·后院石阶 3 镜（柳如烟蹲查 / 沈青崖皱眉 / 柳如烟摇头）；场景角色表正确（林雪 / 柳如烟·沈青崖）。
- **pytest 基建说明**：`pytest director/tests/test_script_parser.py` 仍报 15 errors（根目录 `__init__.py` 导入 torch 缺失，ComfyUI node 入口），与 test_text_backends.py 同样受影响——**判定为项目既有 pytest 收集基建问题，非本 Commit 引入**；项目惯例 = 直接 `python3` 运行测试文件。已删除临时 conftest.py。
- **V17_PLAN.md 更新（#313）**：§0 状态行（Phase 0 PASS + Commit 1 已交付）/ §4 输出 Schema 正式拍板说明 + §4.0 Phase 1 字段定死 / §4 核心原则加 **visual_intent ≠ content.visual** 架构原则（必经 Generation Planning + Official H3 Skill → H3 Prompt Builder） / §11 docx 推迟 / §12 落地清单 / §13 Phase 1 拆分 Commit 1+2 / §15.1 **P0 外部依赖 V17-P0-EXT-H3-SKILL-REF**（base-en.txt/ref-en.txt，BLOCKED/NOT FOUND，查找顺序固定，找到前 Phase 3 可判定但 Phase 4 不实现）/ §15.2 docx 推迟 / §16 第 14-20 条拍板 / §17 Commit 1 勾选 + Commit 2 与验收待办。
- **下一步**：Commit 2 = `director/script_analyzer.py`（OllamaBackend Qwen3:14B `analyze_scene` Scene 批量强 JSON + `analyze_shot` fallback + 超时/重试/JSON 校验/规则兜底；**只 with session() 不碰显存**）；Phase 1 验收 = `tools/phase1_script_test.py` 跑《山雨客栈》13 项 ✓。
- **涉及**：`director/production_plan.py`（新）、`director/script_parser.py`（新）、`director/tests/test_script_parser.py`（新）、`V17_PLAN.md`（更新）、`CHANGELOG.md`。

### #125 V1.7 Phase 1 · Commit 2：script_analyzer Qwen 语义理解补全（2026-08-11）

- **背景（Why）**：Commit 1（#124）规则拆好 Scene/Shot 结构后，由 Qwen 补全语义字段。用户 2026-08-11 拍板 Commit 2 边界：**Qwen 不决定镜头数量/边界**（规则锁死），只补 characters[].role / props / actions / emotion / dialogue.speaker 回填 / visual_intent 草稿；**只 `with backend.session()` 不碰显存**（不启动/卸载 Ollama、不查 GPU、不改 keep_alive，全权交给 TextSession）；强 JSON 兜底 + 规则兜底。
- **`director/script_analyzer.py`（新）**：
  - 单一公共入口 `analyze_scene(llm, scene)`：Scene 批量强 JSON（上下文连贯，prompt 带全部镜头原文 + index 序号）→ 失败 fallback 逐镜 `analyze_shot()` → 再失败保留规则 Shot。`analyze_script(plan, backend=None)` 顶层：一个 `with backend.session()` 遍历全部 Scene，缺省 `create_default_text_backend()`（Ollama qwen3:14b）。
  - 合并语义 = **规则优先，Qwen 只补空**：`_merge_characters`（规则角色保序 + Qwen 补 role + 追加新角色，canonical trim/lowercase 去重）；`_merge_props`（规则道具优先 + Qwen 追加去重）；`_merge_dialogues`（规则对白保序 + 同 text 回填 speaker + 补新台词按 text 去重）；actions/emotion/visual_intent = `规则值 or Qwen 值`（规则 Phase 1 恒空，未来规则填值不被覆盖）。
  - **index 匹配防误配**：`_shot_data_for_index`——批量结果只要出现任何 index 字段就只用 index 精确匹配（缺失返回 None → 该镜保留规则），**防「部分乱序」的批量结果把错位数据误配给别的镜头**；整批无 index 才按数组顺序兜底。
  - 显存安全守卫：`analyze_scene` 入口检查 `text_backend_active()`，不在 `with backend.session()` 内直接调用抛 RuntimeError（防绕过显存安全门）。模块内无任何 unload / gpu_models_loaded / keep_alive 操作。
  - 字段类型健壮性：`_clean_str/_clean_str_list/_clean_char_like/_clean_dialogues` 对 Qwen 非法类型（characters 是 str、actions 是 dict、emotion 是 int）规范化降级，不崩溃；超长 source_text 仅 prompt 截断（`_bounded_text` 600 字 + …，不改落盘原文）。
  - 顶层 `analyze_script` 结束后 `plan.validate()`（机器侧结构校验），分析失败只追加 `validation.warnings`，**不改 status 语义**（人工审核仍走 SPA review）。
- **`director/tests/test_script_analyzer.py`（新，19 函数 74 断言）**：mock FakeTextBackend（继承 TextBackend，session() 走**真实** TextSession → 真实互斥锁 + gpu_models_loaded + preflight/close，能真实验证「只 with session() 不碰显存」）。覆盖：Scene 批量补全（role 回填/新角色追加/道具/动作/情绪/speaker 回填/visual_intent + 结构不变）、批量失败 fallback 逐镜、analyze_script 单 session 多场景（close 恰好一次 / unload 零调用 / 退出后互斥锁释放）、**session 外调用 analyze_scene → RuntimeError**、损坏 JSON 自动重试、规则优先合并、canonical 去重、超时/None 兜底、backend 全失败 plan 结构完整 + warnings、空场景零调用、字段类型非法降级、截断、Phase 1 补全后仍无 castIds/assetId/generationMode/h3Prompt/camera/refs、index 乱序/缺失匹配、无 index 顺序兜底。
- **运行结果**：`python3 director/tests/test_script_analyzer.py` → **74 PASS / 0 FAIL**；`python3 director/tests/test_script_parser.py`（Commit 1 回归）→ **58 PASS / 0 FAIL**；`py_compile` 4 文件全绿。双目录 md5 全一致。
- **真 bug 修复**：初版 `_shot_data_for_index` 顺序兜底会把错位的 `index=1` 数据误配给第二镜（test 18 抓出）——改为「有 index 只用 index 匹配，无 index 才按顺序」。
- **下一步**：Phase 1 验收 = `tools/phase1_script_test.py` 跑《山雨客栈》13 项 ✓（真实 Ollama qwen3:14b 连通验收，含互斥门反向验证）；随后 Commit 3 = 剧本导入正式 UI 入口。
- **涉及**：`director/script_analyzer.py`（新）、`director/tests/test_script_analyzer.py`（新）、`CHANGELOG.md`。

### #126 V1.7 Phase 1 真实验收 PASS（用户实测 12/12，2026-08-11）

- **背景（Why）**：Commit 1（#124）+ Commit 2（#125）交付后，按 V17_PLAN §13「独立验收（PASS 才进入 Commit 3）」跑真实 Ollama 验收。用户本机运行 `tools/phase1_script_test.py`（ComfyUI Desktop venv Python）。
- **结果**：**12/12 PASS**，退出码 0。明细：①Ollama ping ✓ ②qwen3:14b 已拉取（共 3 个模型）✓ ③规则拆 2 场景（scene_01 2 镜 / scene_02 3 镜）✓ ④镜头边界对齐 5 镜（规则约束未失控）✓ ⑤analyze_script 单 session 全链路 + session 正确开合 ✓ ⑥Qwen 补全生效 2/5 镜完整（role/props/actions/emotion/visual_intent）✓ ⑦补全前后 shot_id/source_text/duration_sec 零改动（Qwen 不决定镜头结构）✓ ⑧对白 speaker 2/2 回填 ✓ ⑨validate()=valid 无 warning ✓ ⑩输出 JSON 无 castIds/locationId/assetId/generationMode/h3Prompt/camera/refs ✓ ⑪退出后 text_backend_active() 复位 False + /api/ps 确认 qwen3:14b 不再驻留 ✓ ⑫GPU registry（venv Python 非 ComfyUI 运行时环境自动跳过——实质已在 Phase 0 #123 于 ComfyUI Desktop 验证 empty + H3 正常生成）⑬互斥门反向验证（session 激活时 H3 被拦截 + 锁释放复位）✓。
- **Phase 1 全链路验收通过** → 进入 **Commit 3 = 剧本导入正式 UI 入口**（用户 V1.7 立项拍板 P0 含剧本导入完整 UI，不做命令导入；剧本 → 规则拆 Scene/Shot → Qwen 语义补全 → SPA 审核）。
- **涉及**：`tools/phase1_script_test.py`（新增于 #125，本次无代码改动）、`V17_PLAN.md`（§17 勾选 + 顶部状态行）、`CHANGELOG.md`。

### #127 V1.7 Commit 3：剧本导入正式 UI 入口（2026-08-11）

- **背景（Why）**：Phase 1 真实验收 PASS（#126）后，按 V17_PLAN §0 拍板进入 Commit 3 = **剧本导入正式 UI 入口**（用户立项时明确「P0 含剧本导入完整 UI，不做命令导入」）。用户拍板两条决策：**① 一步完成** = 点「导入剧本」时规则拆 + Qwen 补全一次做完，Qwen 失败自动降级为规则结果 + 警告，不阻塞；**② visual_intent 存 Shot.description 审核备注**（不进 content.visual，遵守「visual_intent ≠ content.visual」原则，Phase 3/4 才生成最终 prompt）。
- **后端**（`director/http_routes.py` + `director/tests/test_script_import.py` 新增）：`POST /minimax/director/script/import`——body `{script_text, title, source_file, analyze}`；`parse_script`（规则拆 Scene/Shot）→ `analyze_script`（Qwen 语义补全）→ 返回 `{plan, rule_only, warnings}`。**Qwen 失败 → `rule_only=True` + 保留规则结构 + warnings，不阻塞导入**；`analyze=false` 跳过 Qwen；空剧本/非 JSON → 400。**⛔ 显存完全不动**：handler 用 `asyncio.to_thread` 包裹 `analyze_script`（内部自己创建 `backend.session()`），模块内零 unload/gpu_models_loaded/keep_alive 操作。结构字段（shot_id/source_text/duration_sec）由规则锁死，Qwen 只补 characters[].role/props/actions/emotion/dialogue.speaker/visual_intent。
- **前端**：
  - `models/project.ts`：`Shot.description?: string`（visual_intent 审核备注，只作参考不参与生成）。
  - `services/comfyApi.ts`：`importScript()` + `ScriptImportResult` / `ProductionPlanJson` 类型。
  - `core/productionPlanToProject.ts`（新增，纯函数）：ProductionPlan → SPA Project——scenes → Episode.scenes（sceneId/name/location/time/weather/order）；shots → Shot 骨架（id/sceneId/order/durationSec，content.visual 保持空）；**visual_intent + source_text 原文并入 Shot.description**；characters/props → Scene.assets 注册（按名去重）**但不绑 castIds/locationId**（Phase 2 Asset Registry 才做）；输出绝无 Phase 2/3/4 字段；`planSummary()` 审核摘要（场景/镜头/角色去重/总时长）。
  - `workbench/WorkbenchView.vue`：home 页新增「📜 导入剧本」按钮 + 弹窗——粘贴文本 / 选择 .md/.txt 文件 → 「解析剧本」→ **ProductionPlan 审核预览**（摘要 + rule_only 警告 + warnings + 逐场景逐镜 source_text/角色/visual_intent）→「应用为 SPA 项目」（映射 → saveProject → loadProject → 进工作台，id=`script-<时间戳>` 不覆盖已有项目）。
- **测试/校验**：后端 test_script_import **27 断言 PASS** + 回归 58+74+28+27 全绿 + py_compile OK；前端 productionPlanToProject 单测 **11 PASS** + 全量 **276 PASS**（16 文件）+ vue-tsc/vite build 全绿；双目录 md5 全一致（8 个改动文件逐一校验 OK）。另顺手修 2 个既有 TS 错误（`updateShotCast` 死参数 `_manual` 前缀豁免；mergeCastId 类型守卫 `Boolean(a)` → `a !== undefined`）。
- **⛔ 需重启 Comfy Desktop**：新路由在 PromptServer 启动时注册，热更新无效（同已知坑 16）。
- **涉及**：`director/http_routes.py`、`director/tests/test_script_import.py`、`frontend/src/models/project.ts`、`frontend/src/services/comfyApi.ts`、`frontend/src/core/productionPlanToProject.ts`（新增）、`frontend/src/workbench/WorkbenchView.vue`、`frontend/src/stores/workbench.ts`、`frontend/tests/productionPlanToProject.test.ts`（新增）、`V17_PLAN.md`、`CHANGELOG.md`。

### #128 V1.7 Phase 2：资产自动匹配（P0-B 剧本 → 资产）（2026-08-11）

- **背景（Why）**：Phase 1 真实验收 PASS（#126）+ Commit 3 剧本导入 UI 收官（#127）后，进入 V1.7 立项 P0-B **剧本 → 资产**。用户拍板三条决策（AskUserQuestion 全选推荐项）：**① 固定共享资产库** = 扫描 `{ComfyUI input}/minimax_studio/assets/`（既有 33 张平铺 PNG），零配置；**② 纯规则先行** = 精确 → 归一后精确 → 子串包含 → 置信度打分，**不跑 Qwen 语义**（Qwen 语义匹配留 Phase 4）；**③ 高置信度自动绑** = 置信度 ≥0.9 自动绑定、0.5~0.89 弹候选人工确认、无候选留空。
- **⛔ 两条铁律**：**AI 绝不覆盖已有资产**——只复用资产库已有文件，绝不创建「林雪_2」，未匹配留空进工作台手工补图；**显存完全不动**——asset matcher 是纯规则、零 Ollama/GPU 依赖，`/assets/scan` 路由用 `asyncio.to_thread(match_plan, plan)` 包裹。
- **后端**（`director/asset_matcher.py` 新增 + `director/http_routes.py`）：
  - 匹配链路四级：**① 精确全等**（conf 1.0 auto）→ **② 归一后精确**（剥「角色_/设定卡_/_主视角/_图/_v1」等前后缀，conf 0.95 auto）→ **③ 子串包含**（名长≥2 双向，conf 0.8 pending）→ 无候选 none。`_key()` 去空白 lower；`_norm_asset_name()` 前后缀清洗。
  - `scan_asset_library(root=None)`：延迟 `import folder_paths`（纯单测传 root 不依赖 comfy），列出图片扩展名文件，`image_file` = `minimax_studio/assets/<name>`（ComfyUI input 相对路径，前端 `comfyInputUrl()` 预览）。
  - `collect_plan_entities(plan)`：cast（角色）/prop（道具）/location（scene.location_name）三类实体提取，按 `(kind, key)` 去重保序。
  - `match_plan(plan, library)` → `{assets: [...], matches: [{kind, name, status, matched, asset_name?, image_file?, confidence, candidates:[{name, image_file, score}]}]}`；candidates 最多 5 条。
  - 路由 `POST /minimax/director/assets/scan`：body `{plan}`（ProductionPlan JSON，必须有 scenes），400 bad_json/bad_plan；`asyncio.to_thread(match_plan, plan)` 执行返回。
- **前端**：
  - `services/comfyApi.ts`：`scanAssets(plan)` + `AssetScanResult` / `AssetLibraryItem` / `AssetMatch` 类型。
  - `core/productionPlanToProject.ts`：`opts.matches`（`ConfirmedAssetMatch[]`，auto 自动 + pending 用户确认）→ **高置信度绑定**——cast → Scene.assets.cast 资产填 imageFile + 按 shot 角色绑 castIds（资产 id=实体名）；prop → props 资产填 imageFile；location → locations 资产注册 + scene.defaultLocationId + shot.locationId。**不传 matches 行为不变**（注册不绑定，无 Phase 2 字段）。
  - `workbench/WorkbenchView.vue`：解析剧本成功后自动调 `scanAssets`（失败仅提示不阻塞审核）→ 审核预览新增 **🎨 资产自动匹配区**——auto 条目显示缩略图 + 「自动 ✓」；pending 条目下拉候选（缩略图实时预览）人工确认；none 条目提示「进工作台手动补图」；顶部统计「N 自动 · N 待确认 · N 未匹配」。`applyScriptProject` 时把 auto + 已确认候选传给 `productionPlanToProject({matches})`。
- **测试/校验**：后端 `test_asset_matcher.py` **48 断言 PASS**（归一/扫描/实体提取/四级匹配/pending 候选/无候选/none/全链路/不创建新资产/路由 400+200/路由注册）+ py_compile 双绿；前端 productionPlanToProject Phase 2 绑定单测 5 个新增 + 全量 **281 PASS**（16 文件）+ vue-tsc/vite build 全绿；双目录 md5 全一致（7 个改动文件逐一校验 OK）。真 bug 修复：`_norm_asset_name` 漏纯「图」后缀（「林雪图」→「林雪」），补入 `_NAME_SUFFIXES`。
- **⛔ 需重启 Comfy Desktop**：新 `/assets/scan` 路由在 PromptServer 启动时注册，热更新无效（同已知坑 16）。
- **涉及**：`director/asset_matcher.py`（新增）、`director/http_routes.py`、`director/tests/test_asset_matcher.py`（新增）、`frontend/src/services/comfyApi.ts`、`frontend/src/core/productionPlanToProject.ts`、`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/productionPlanToProject.test.ts`、`V17_PLAN.md`、`CHANGELOG.md`。

### #129 V1.7 Phase 3（P0-C）：结构化生产 Prompt 草稿生成器（AI Draft，2026-08-11）

- **背景（Why）**：V17_PLAN §6 P0-C = **结构化生产 Prompt 草稿**（不是最终 H3 Prompt）。Phase 2（#128）资产匹配收官后，Phase 3 把 ProductionPlan 已结构化字段（Qwen 补全的 visual_intent/emotion/actions/dialogue + 场景时间/地点/天气）按 **Prompt Template Registry** 模板规则组合成五区中文草稿，供导入审核预览 + 人工编辑。用户拍板三条决策（AskUserQuestion 全选推荐项）：**① 纯规则 + 模板组合**（零显存、可复现、导入快）；**② 导入弹窗加「🤖 AI Draft」折叠预览**；**③ 中文草稿**（与剧本语言一致，英文转换留 Phase 4）。
- **⛔ 显存铁律**：`prompt_builder_v17.py` 纯规则、零 Ollama/GPU 依赖（与 asset_matcher 同策略，`test_no_gpu_side_effects` 断言源码无 unload/gpu_models_loaded/keep_alive/empty_cache/urlopen/requests/torch）；路由 `asyncio.to_thread` 只避免阻塞事件循环。
- **后端**：
  - `director/prompt_builder_v17.py`（新增）：`DEFAULT_TEMPLATE_VERSION="h3-v1"`；`load_template_registry()` 版本→文件（`prompt_templates/v1.json`，缺文件/格式非法抛 ValueError 防静默回退）；`judge_generation_mode()` **强连续动作→fl2v / 续镜→r2v / 首镜→t2v**（V17-P0-EXT 允许 Phase 3 做，仅「制作计划建议」，SPA taskType 保持 auto）；五区构建 `_build_visual/_build_camera/_build_style/_build_sound/_build_negative`；`_pick_camera_intent` 优先级 **establish（首镜无角色）→ close（特写词）→ dialogue（有对白，优先于动作防「抬眼/说话」误判）→ motion（动作词）→ emotion（强情绪）→ ending（尾镜）→ default**；`build_plan_drafts(plan)` → `{template_version, drafts:[{scene_id, shot_id, generation_mode, draft:{visual,camera,style,sound,negative,camera_intent}}]}`（dict 或 dataclass 通用，`_get/_get_name` 统一取值）。
  - `director/prompt_templates/v1.json`（新增）：version h3-v1、sections 五区模板、camera.intents 6 意图 + 顶层 default、palettes（雨/夜/黄昏/雪/清晨/default）、ambients、sound_hints（dialogue/music_tense/music_calm）。**改措辞不动解析逻辑；未来 h3-v2 老项目不重新生成**。
  - `director/http_routes.py`：新路由 `POST /minimax/director/prompt/draft`（body `{plan}`，400 bad_json/bad_plan，`asyncio.to_thread(build_plan_drafts)`）。
- **前端**：
  - `services/comfyApi.ts`：`buildPromptDrafts(plan)` + `PromptDraftResult` / `PromptDraftItem` 类型。
  - `core/productionPlanToProject.ts`：`opts.drafts`（按 `scene_id:shot_id` 命中）+ `opts.promptTemplateVersion` → 五区填入 `shot.content`（visual/cameraText/style/soundText/negativePrompt）+ `shot.aiDraft=true` + `shot.promptTemplateVersion`。**不传 drafts 行为不变**（content.visual 保持空、无 aiDraft/promptTemplateVersion）。
  - `models/project.ts`：Shot 加 `aiDraft?: boolean`（仅元数据标记，不参与指纹/生成）、`promptTemplateVersion?: string`。
  - `workbench/WorkbenchView.vue`：解析剧本成功后自动调 `buildPromptDrafts`（失败仅提示不阻塞审核）→ 审核预览新增 **🤖 AI Draft 制作计划草稿区**（资产匹配区之后）——每镜五区只读展示（画面/运镜/风格/声音/负面）+ generation_mode 徽标（FL2V 强动作/R2V 续接/T2V 首镜）+ 模板版本徽标；`applyScriptProject` 传 drafts + template_version。
- **测试/校验**：后端 `test_prompt_builder.py` **68 断言 PASS**（模板 Registry/未知版本 ValueError/generation_mode/visual 优先+fallback/camera 6 意图/style/sound/negative/build_plan_drafts 全链路 3 镜顺序+建议[fl2v,r2v,t2v]/纯规则零显存/路由 400+200）+ py_compile 3 文件通过 + 回归 asset_matcher 48 PASS + script_import 27 PASS；前端 productionPlanToProject Phase 3 映射单测 **6 个新增**（五区命中/逐镜独立/部分命中/不传行为不变/空数组/跨场景同名不串）+ 全量 **287 PASS**（16 文件）+ vue-tsc/vite build 全绿；双目录 md5 全一致（10 个改动文件 + dist 整目录逐一校验 OK）。
- **⛔ 需重启 Comfy Desktop**：新 `/prompt/draft` 路由在 PromptServer 启动时注册，热更新无效（同已知坑 16）。
- **涉及**：`director/prompt_builder_v17.py`（新增）、`director/prompt_templates/v1.json`（新增）、`director/http_routes.py`、`director/tests/test_prompt_builder.py`（新增）、`frontend/src/services/comfyApi.ts`、`frontend/src/models/project.ts`、`frontend/src/core/productionPlanToProject.ts`、`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/productionPlanToProject.test.ts`、`frontend/dist/`、`V17_PLAN.md`、`CHANGELOG.md`。

### #130 V1.7 Phase 4：Workbench 审核态 + 补资产闭环（AI 制作计划 UI，2026-08-11）

- **背景（Why）**：V17_PLAN §9.0「AI 制作计划」审核态 = **AI 只把剧本转换成经人工审核即可生产的制作计划，AI 不直接开拍**。Phase 3（#129）导入剧本即生成 AI Draft 后，Phase 4 让 Workbench 成为审核台：① 顶部横幅统计待审核镜 + ② 未采纳草稿生成硬拦截 + ③ 每镜 ✓/⚠ 三项就绪判定（资产绑定 / 五区 Prompt / 运镜）+ ④ ⚠「资产未找到」→ 从共享资产库补资产闭环。用户 2026-08-11 四决策全对齐（横幅/硬拦截/三项判定/补资产弹窗）。
- **⛔ 产品底线铁律**：`adopted=true` 后即普通 Shot 走现有链路；`aiDraft` 保留为历史元数据，**不参与指纹/状态灯/导出/续接**（与 shotReviewStatus 同准则）。
- **后端**：**零新增路由**——Phase 4-C 复用现有 `POST /minimax/director/assets/scan`（#128 已建，纯规则零显存），**纯前端，刷新即生效**。
- **前端（Phase 4-A）**：`core/shotReviewStatus.ts`（新增）`shotReviewStatus(shot,{castIds,assets})` 三项判定纯函数：① castIds 应有资产且已传参考图（未绑定→`角色「X」未绑定资产`/缺图→`参考图缺失`）+ 生效资产（地点/道具/风格）缺图→`资产「X」未上传参考图`；② `content.visual` 非空；③ `content.cameraText` 非空。任何缺失→ok=false + issues 人类可读。
- **前端（Phase 4-B）**：`stores/workbench.ts` 加 `adoptAiDraft(shotId)` / `adoptAllAiDrafts()`（幂等，采纳即 adopted=true）；`models/project.ts` Shot 加 `adopted?: boolean`；`workbench/WorkbenchView.vue` 审核态：`reviewActive`/`pendingReviewShots`/`reviewStatusOf(s)`（非当前镜独立 resolveInheritance 保证卡片状态不被选中镜影响）、顶部 `.review-banner`（待审核计数 + 就绪统计 + 一键采纳）、Shot 卡片 `🤖 ✓/⚠` 徽标（ready/notready + 采纳按钮 + 已采纳态）、生成按钮 `review-gate` 警示态（未采纳草稿点击→store 记「请先采纳 AI 草稿」不触发生成，采纳后放行）。
- **前端（Phase 4-C）**：`workbench/AssetConfirmDialog.vue`（新增）审核态补资产弹窗——`MissingAssetRef`（mode=cast 未绑定角色 / asset 生效资产缺图）+ `buildMiniPlan()` 构造**只含缺失实体**的最小 ProductionPlan 调 `/assets/scan`（assets 列表 + 实体匹配 auto/pending/none）+ 候选自动默认选中（auto 有图→池中候选）→ 确认后写回：cast 未绑定→复用池中同名 / `addAsset` 从共享库注册（**绝不创建「林雪_2」**）→ `setShotCasts` 写入真实资产 id（剔除旧实体名/旧 id 引用）；asset 缺图→`updateAsset` 直接填 imageFile（**不新建、不覆盖已有图**）；无匹配→确认置灰 + 共享库搜索手动选图。WorkbenchView 集成：`missingAssetRefs(s)` 解析 reviewStatus asset issues → 徽标 title「点此补资产」+ 点击打开弹窗 → 确认后关闭 + 徽标转就绪。
- **测试/校验**：前端新增 `tests/assetConfirmDialog.test.ts`（4 用例：打开→scanAssets plan 构造→auto 默认选中→确认注册资产+castIds 写回+emit / cast 已存在缺图→复用补图不新建 / asset 缺图无匹配置灰→搜索库手动选图→updateAsset / 空态+close）+ workbenchRender.test.ts 加 2 集成用例（角色未绑定→徽标可点开→候选确认→castIds 写回真实 id+徽标转 ready+弹窗关 / 仅运镜缺口→点徽标不开弹窗）；全量 **311 PASS**（18 文件）+ vue-tsc/vite build 全绿；双目录 md5 全一致（src+test 递归 diff 无差异）。
- **⛔ 免重启提示**：Phase 4 纯前端（复用 #128 路由），**刷新页面即生效，无需重启 Comfy Desktop**。
- **涉及**：`frontend/src/core/shotReviewStatus.ts`（新增）、`frontend/src/workbench/AssetConfirmDialog.vue`（新增）、`frontend/src/workbench/WorkbenchView.vue`、`frontend/src/stores/workbench.ts`、`frontend/src/models/project.ts`、`frontend/tests/assetConfirmDialog.test.ts`（新增）、`frontend/tests/workbenchRender.test.ts`、`frontend/tests/workbenchEdit.test.ts`、`frontend/tests/shotReviewStatus.test.ts`（新增）、`frontend/dist/`、`V17_PLAN.md`、`CHANGELOG.md`。

### #131 V1.7 Phase 4 验收拦截修复：director 包内绝对导入 → 相对导入（2026-08-11）

- **背景（Why）**：用户实测剧本导入报 `⚠ 剧本解析失败：ComfyUI /minimax/director/script/import 请求失败：405 Method Not Allowed`，重启 Comfy Desktop 后**仍 405**。排查 `comfyui.log` 找到真根因：`MiniMax H3 Director HTTP routes failed to load: No module named 'director'`——路由注册代码在启动时抛异常被 `__init__.py` 的 try/except 吞掉只打 warning，`register_routes()` 从未执行，**所有 `/minimax/director/*` 路由（含 V1.6 老路由 projects/export/upload_chunk）都没注册** → POST 打到 ComfyUI catch-all 返回 405（有 GET 兜底所以不是 404）。
- **根因**：Phase 1 新增 `director/script_parser.py`（第 38 行）与 `director/script_analyzer.py`（第 40/48 行）顶层用**绝对导入** `from director.production_plan import ...` / `from director.text_backends import ...`。项目测试跑 `python3 director/tests/test_xxx.py` 时靠 `sys.path.insert` 把项目根加入 → `director` 是顶层包所以能通过；但 **ComfyUI 加载 custom_nodes 时包路径是 `custom_nodes.ComfyUI_MiniMaxH3_Director.director`，顶层没有 `director` 模块** → `__init__.py` 第 35 行 `from .director.http_routes import register_routes` 的导入链一进入 script_analyzer 即抛 `No module named 'director'`。
- **修复**：两处 `from director.xxx` 改为相对导入 `from .production_plan` / `from .text_backends`（http_routes.py 本来就是相对导入；asset_matcher.py 的 folder_paths 是延迟导入不受影响）。`tools/` 与 `director/tests/` 里的绝对导入是手动/测试专用，ComfyUI 不加载，保持不变。
- **验证**：① 逻辑回归——`test_script_parser` 58 PASS / `test_script_analyzer` 74 PASS / `test_text_backends` 28 PASS，全绿；② **决定性模拟**——构造 `custom_nodes/ComfyUI_MiniMaxH3_Director/director` 包结构（顶层无 `director`）+ mock server/folder_paths/aiohttp，`import ...director.http_routes` 成功 + `register_routes()` 返回 True + **19 条路由全部注册**（script/import、assets/scan、prompt/draft、export、projects、upload_chunk、snapshot 等）；③ 双目录 md5 一致 + 清除双方 `director/__pycache__` 强制重编译。
- **⚠ 必须重启 Comfy Desktop**（路由 PromptServer 启动时注册，热更新无效；本次改动=修好加载，重启后 `comfyui.log` 应出现 `MiniMax H3 Director HTTP routes registered` 且不再有 `failed to load`）。
- **涉及**：`director/script_parser.py`、`director/script_analyzer.py`、`CHANGELOG.md`。

### #132 V1.7 Phase 4 采纳失效修复：跨场景 shot_id 碰撞 → 前端全局唯一 Shot.id（2026-08-11）

- **背景（Why）**：用户实测剧本导入后逐镜采纳 AI 草稿，报「**采纳我点了七个之后就点击不了了**」——点成功 7 个后，剩余镜头采纳按钮点击无任何反应。
- **根因**（跨层证据链）：①后端 `director/script_parser.py` 第 479 行 `for j, unit in enumerate(units, start=1)` 的 `j` 是**场景内**镜头序号（每场景重置），`shot_id=f"shot_{j:02d}"` → **跨场景 id 重复**（scene_01 有 shot_01，scene_02 也有 shot_01）；②前端 `productionPlanToProject.ts` 第 113 行直接用 `sh.shot_id` 作 `Shot.id`（无全局唯一化）；③`workbench.ts` `findShot()` **全局首个匹配**（episodes→scenes→shots 顺序，找到第一个同 id 就返回）；④`adoptAiDraft()` 对**已采纳**镜头幂等 return → 后续场景同 id 镜头点采纳时 `findShot` 命中**其它场景已采纳的同名镜头**，直接 return，点击无效果。
- **用户实测证据（决定性）**：`minimax_studio/projects/script-20260810200624/project.json`（项目名 cesi）导入结果 scene_01(shot_01~03 全部已采纳) + scene_02(shot_01~07：**04~07 已采纳**——这些 id 在 scene_01 不存在、findShot 命中自身；**01~03 未采纳**——id 撞 scene_01 已采纳镜头、点击被吞) + scene_03(shot_01~02 未采纳，同样撞 scene_01)。用户视觉上点成功的恰好 = 3（scene_01 全部）+ 4（scene_02 的 04~07）= **7 个**，剩余 5 个全部失效。
- **修复**：`productionPlanToProject.ts` 改为**跨场景递增计数器**生成全局唯一 `Shot.id`（shot_01 → shot_02 → …，2+7+2=11 镜则 shot_01~shot_11），`name` 同步全局唯一；`order` 保持场景内序号（生成/导出依赖）；Phase 3 草稿匹配 key 用后端 `scene_id:shot_id`（独立于前端 id），命中不受影响。
- **验证**：①`productionPlanToProject.test.ts` 26 测试全绿（新增「跨场景 shot_id 碰撞 → 前端 id 全局唯一」describe：全局连续递增/跨场景同后端 id 前端不同/order 场景内保留/三场景 2+7+2=11 镜 id 连续不碰撞）；②前端全量 **315 测试全绿**；③`npm run build` 全绿；④双目录 md5 全一致（源码 + dist）。
- **⛔ 旧项目数据迁移（2026-08-11 补充完成）**：修复对**新导入**项目自动生效（全局唯一 id）；旧导入项目（cesi，`minimax_studio/projects/script-20260810200624`）project.json 已存重复 id，用户复验时仍在旧项目上操作（未重新导入）导致点击依旧被吞 → **已直接迁移项目数据**：shot id + name 全局唯一化（scene_01 保持 shot_01~03，scene_02 → shot_04~10，scene_03 → shot_11~12），`order`/`adopted`/`aiDraft`/`content` 等全部字段保留，备份 `project.json.bak_132`；用户刷新 SPA 重新打开项目即可继续采纳剩余镜头。其他项目（山雨客栈/第七号站台/proj_cybercity 等）经脚本扫描均无重复 id 隐患。
- **涉及**：`frontend/src/core/productionPlanToProject.ts`、`frontend/tests/productionPlanToProject.test.ts`、`minimax_studio/projects/script-20260810200624/project.json`（数据迁移 + 备份）、`CHANGELOG.md`。

### #133 V1.7 Phase 5（P1-D）：自动运镜模板库 + 跨镜状态机 + 前端模板下拉（2026-08-11）

- **用户决策（2026-08-11 评审）**：①跨镜连贯边界 = **场景内连贯 + 切换场景重置**（next_state 每场景首镜置 None）；②模板数据源 = **后端权威 + 前端拉取**（GET `/minimax/director/camera/templates`）；③前端交互 = **模板下拉 + 手编共存**（选模板自动填 cameraText + 记 cameraTemplate id；手编清空该标记 = 自定义）。
- **5-A 后端模板库（`director/camera_template.py`，67 PASS）**：`CAMERA_TEMPLATES` 15 条（10 基础 + 5 补充：establish/enter/walk/tense/see_object/dialogue/emotion_close/chase/memory/ending/combat/montage/empty_transition/orbit/boom），每条含 id/name/intent/keywords/template/shot_size/movement/camera_position/movement_direction。`pick_camera(shot, scene, prev_state, is_last_shot)` 优先级：①场景首镜无角色→establish ②特写意图→see_object/emotion_close ③对白→dialogue ④关键词命中→细分模板（walk 关键词含「开门/收伞/穿过」等强动词）⑤强情绪→emotion_close ⑥尾镜→ending ⑦default。`_apply_continuity` 三规则：方向延续（左/右跟移前缀「承接上镜X向运动」）/ 对话反打（over_shoulder →「承接上镜视线，反打对切」）/ 避免重复推近（prev push_in+close_up + cur push_in →「保持近景，微推」）。`build_plan_cameras(plan)` 与 `list_camera_templates()` 供路由用。
- **5-B 后端集成（`prompt_builder_v17.py` + `http_routes.py`，50 PASS）**：`build_plan_drafts(..., use_camera_planner=True)` 每场景 `prev_state=None` 重置、逐镜 `pick_camera` 写 `draft["camera"]`/`camera_intent`/`camera_template`；新增路由 `GET /minimax/director/camera/templates` + `POST /minimax/director/camera/plan`（asyncio.to_thread 防阻塞）。路由注册从 19 → **21 条**。⛔ 纯规则零显存零 Ollama；`#131` 相对导入纪律保持（`.camera_template`、`.prompt_builder_v17`）。
- **5-C 前端模板下拉（纯前端，刷新即生效）**：`comfyApi.ts` 加 `fetchCameraTemplates()` + `CameraTemplate/CameraTemplateList` 类型 + `PromptDraftItem.camera_template`；`models/project.ts` 加 `Shot.cameraTemplate`（元数据，不参与指纹/生成）；新增 `src/core/cameraTemplates.ts` 纯函数（sceneLocationName/sceneTimeHint/shotSubject/shotObject/shotEmotion/findCameraTemplate/fillCameraTemplate 占位符替换，与后端 `.format` 语义对齐）；`productionPlanToProject.ts` 草稿命中时持久化 `cameraTemplate`；`stores/workbench.ts` 加 `updateShotCameraTemplate`；`WorkbenchView.vue` 摄影分区加模板 `<select>`（onMounted 拉取、失败静默降级手编）+ 手编 cameraText 自动清模板标记。
- **真 bug（本 Commit 修复）**：`onCameraTextInput` 误把 `"cameraText"` 当 shotId 传给 `updateShotSection`（参数位错 → findShot 返回 null → 写回无效）——TS 编译直接暴露（Expected 3 arguments, but got 2），已改 `updateShotSection(shot.id, "cameraText", v)`。
- **验证**：①后端 5-A 67 PASS + 5-B 50 PASS（含 GET/POST 路由、场景重置无「承接上镜」前缀、use_camera_planner=False 降级、no-GPU 源码扫描无 unload/keep_alive/torch/ollama）；②前端新增 `tests/cameraTemplates.test.ts`（17）+ `productionPlanToProject.test.ts` 补 Phase 5 映射（3）→ **335 测试全绿**；③`npm run build` 全绿（vue-tsc + vite）；④双目录 md5 全一致（源码 + dist 预构建）。
- **涉及**：`director/camera_template.py`、`director/prompt_builder_v17.py`、`director/http_routes.py`、`director/tests/test_camera_template.py`、`director/tests/test_camera_plan.py`、`frontend/src/services/comfyApi.ts`、`frontend/src/models/project.ts`、`frontend/src/core/cameraTemplates.ts`、`frontend/src/core/productionPlanToProject.ts`、`frontend/src/stores/workbench.ts`、`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/cameraTemplates.test.ts`、`frontend/tests/productionPlanToProject.test.ts`、`CHANGELOG.md`。
- **⛔ 前端无需重启 Comfy Desktop（纯前端刷新即生效）；后端路由已注册需重启一次**（重启后 `comfyui.log` 应见 `routes registered`）。

### #134 实体抽取加固（#354-#359 + #133 运镜模板修复，2026-08-11）

- **用户 bug 报告（触发全部工作）**：实测《山雨客栈》「📄 导入剧本」真实流水线后资产匹配输出极差——「**5 自动 · 2 待确认 · 23 未匹配**」；Qwen 输出垃圾实体：「角色 镜头一」~「镜头九」（**镜头标记被当角色**）、「道具 青衫」「道具 客栈大堂」「道具 檐角」「道具 雾气」「道具 灯笼」「道具 烛火」「道具 木桌」「道具 长凳」「道具 油灯」。用户定性：**不是 Qwen3:14B 差，而是「实体抽取 Prompt + Schema + 后处理 + 资产分类设计不够严格」**。用户拍板方案 = **分层加固（推荐）**：增量加固保持现有结构/测试，不重写完整 Entity Registry。
- **分层流水线（用户拍板）**：**Rule Extractor → Qwen Entity Judge → Entity Registry（`entity_cleanse.py` 纯规则层）→ Asset Matcher**。
- **#354 Schema（`director/production_plan.py`）**：`Entity` dataclass（id/name/type/source/confidence）+ `EntityType`（character/location/prop/costume/environment/effect/architecture/vehicle/creature/unknown）+ `EntitySource`（**script = 进匹配 / inferred = 视觉补充永不进匹配**）+ `Shot.entities` + `new_entity_id(used)` 支持跨镜 used 集合防撞车（与规则实体 ent_001 不冲突）。
- **#355 Qwen 实体 Judge（`director/script_analyzer.py`）**：Prompt 升级「**只能从剧本原文中提取实体，不得为了视觉完整性自行创造**」+ typed entities（name/type/source/confidence 强 JSON）；跨镜 `used_ids` 线程化（Rule Extractors 生成的 id 一并收集，Qwen 新实体从可用池取号）。
- **#356 清洗层（`director/entity_cleanse.py` 新建，纯规则零显存）**：`INVALID_ENTITY_PATTERNS`（`^镜头[一二三四五六七八九十\d]+` 等丢弃）；`normalize_entity_name`（全角/空格归一 + 去「的」「一个」等量词前缀）；`hint_type`/`correct_type` 类型强纠正（青衫→costume / 檐角→architecture / 雾气→effect / 灯笼、烛火、油灯→prop）；canonical 去重；**置信度门控**：`CONF_ENTER=0.60`（≥0.60 且 source=script 才进匹配）、`CONF_AUTO_ENTITY=0.85`（≥0.85 且类型资产存在 → can_auto）；`CONF_SEED=1.0`/`CONF_RULE_CHAR=0.95`；**双模式注册表**：无实体的 plan → legacy 回退（保旧测试），有实体 → 仅消费清洗后注册表（**props 不回填 shot.props，杜绝「道具 青衫」**）；`build_entity_registry(plan)` 返回漏斗统计 `{discovered, invalid_dropped, dedup_dropped, seeded, cleansed, entered, excluded, auto, pending, none}`。
- **#357 资产匹配接入（`director/asset_matcher.py`）**：`match_plan` 消费 `build_entity_registry(plan)`，遍历 `reg["entries"]` 中 `e["entered"]` 条目，四级匹配不变；新增 `_cap_pending`——`can_auto=False` 的实体即使精确命中也被**限制为 pending**（走人工确认）；每个匹配透出 `entity_type/entity_source/entity_confidence/entity_id/can_auto`；`kind_for_type`：character→cast / location→location / else→prop。
- **#358 后端测试 + 双目录同步**：`test_script_analyzer` 补 typed 实体/used_ids/防创造约束；`test_entity_cleanse.py`（66 断言：类型纠正/镜头标记丢弃/门控/双模式/漏斗计数）；`test_asset_matcher` 接 registry（60 断言含 can_auto/pending 限制）；全量回归 script_parser 58 / script_analyzer 95 / asset_matcher 60 / entity_cleanse 66 / text_backends 28 / script_import 27 / prompt_builder 68 / camera_plan 50 / camera_template 67 全绿；双目录 md5 全一致。
- **#359 前端漏斗 + chip（纯前端刷新即生效）**：`comfyApi.ts` `AssetScanResult` 加 `funnel` 10 字段、`AssetMatch` 加 `entity_type/entity_source/entity_confidence/entity_id/can_auto`；`WorkbenchView.vue` 资产匹配确认区漏斗文案「AI 发现 N → 丢弃 M → 清洗 K → 进入 J」+ 每行类型 chip（角色/地点/道具/服装/环境/特效/建筑/载具/生物）+ 置信度 % + 门控提示（<85% 需确认 / AI 推断实体不进匹配）；`workbenchRender.test.ts`/`assetConfirmDialog.test.ts` fixture 兼容 → **前端 335 测试全绿 + build 全绿 + dist 同步**。
- **Golden Path 验收脚本升级（`tools/golden_path_script_test.py`）**：Fake 后端产出 typed 实体（角色/道具 script 0.9）+ 注入垃圾「镜头N」（script 0.8）+ 推断实体「烛火」（inferred 0.5）→ 验收断言：**无 `^镜头` 伪实体进匹配、无 `entity_source=="inferred"` 进匹配、漏斗统计齐全**；打印漏斗明细 → **--mock 离线全链路 14/14 PASS**（AI 发现 43 → 丢弃 34 → 清洗 12 → 进入 11 → auto 7 / 待确认 0 / 未匹配 4）。
- **⚠ 附带修复（#133 遗留真 bug）**：`director/camera_template.py` 的 dialogue 模板 `movement=static`（「中景，正反打对切，{subject}轮流入画。」**无运镜词**，违反用户硬规则「每镜必须带明确运镜设计」且 golden_path ⑩ 必挂）→ 改为「…轮流入画，**随对话节奏轻微推近**」+ `movement=push_in`/`movement_direction=in`；对白镜头因此带推近运镜（substring 断言 `"正反打对切"` 不受影响）。修复后 camera_template 67 / camera_plan 50 / prompt_builder 68 / golden path mock 14/14 全绿。
- **⛔ 纪律（本次强化）**：实体模式**不回填 shot.props**；AI 永不创建新资产只复用现有文件（绝不生成「林雪_2」）；纯规则层零显存零 Ollama（源码扫描无 unload/keep_alive/torch/ollama）；#131 相对导入纪律保持（`.entity_cleanse` 等）。
- **涉及**：`director/production_plan.py`、`director/script_analyzer.py`、`director/entity_cleanse.py`、`director/asset_matcher.py`、`director/camera_template.py`、`director/tests/test_entity_cleanse.py`、`director/tests/test_asset_matcher.py`、`director/tests/test_script_analyzer.py`、`director/tests/test_camera_template.py`、`tools/golden_path_script_test.py`、`frontend/src/services/comfyApi.ts`、`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/workbenchRender.test.ts`、`frontend/tests/assetConfirmDialog.test.ts`、`V17_PLAN.md`、`CHANGELOG.md`。
- **⛔ 前端无需重启 Comfy Desktop（纯前端刷新即生效）；后端实体/清洗/匹配逻辑改动需重启一次 Comfy Desktop 生效**。

### #361-#368 Phase 1.1 语义准确性加固（2026-08-11，用户评审驱动）

- **用户评审触发全部工作**：Golden Path 14/14 PASS 只证明「管线完整性」，不证明「语义准确性」。用户明确拍板 **「不要继续 Phase 2，先做一个非常小的 Phase 1.1」**，只改三件事：① Entity 分成两层 **ScriptEntity / VisualElement**（剧本实体 vs 视觉元素）；② ScriptEntity 增加 **type / asset_requirement / source / confidence**；③ Asset Matcher 加 **硬约束**（character→只能匹配 cast / location→只能匹配 location / prop→只能匹配 prop）并增加 **semantic incompatible → NONE**。另附具体要求：地点层级关系（山雨楼→山雨楼外 显示「⚠ 建议参考 山雨楼外 相似度 0.80 [接受][重新选择]」）、泛化匹配（石阶→后院石阶 标记 generic_reference，原文实体不被资产匹配结果反向污染）、Qwen 拆 **Task A（Script Fact Extraction，只允许剧本明确出现实体）/ Task B（Visual Interpretation，允许合理视觉推断）**、明确 **Asset extraction ≠ Visual description**、结果展示分组（角色✓/地点✓?建议参考/道具✓?未找到/视觉元素·进Prompt/无需资产匹配 N 计数）。
- **#363 Schema 扩展（`director/production_plan.py`）**：新增 `AssetRequirement` 类（REQUIRED="required" / RECOMMENDED="recommended" / NONE="none"）+ `asset_requirement_for_type(etype)`（character/location→REQUIRED；prop/costume/architecture/vehicle/creature→RECOMMENDED；environment/effect/unknown→NONE）；`Entity` dataclass 新增 `asset_requirement` 字段；新增 `VisualElement` dataclass（name/type/confidence + to_dict/from_dict）；`Shot` 新增 `visual_elements: List[VisualElement]`（to_dict/from_dict 已更新）。
- **#364 Qwen 拆 Task A/B（`director/script_analyzer.py`）**：`_SCENE_ANALYZE_TEMPLATE`/`_SHOT_ANALYZE_TEMPLATE` 重写为嵌套结构——`script_facts`（characters/locations/props/costumes/entities[{name,type,confidence}]）/ `visual_interpretation`（visual_elements/actions/emotion）；Prompt 铁律「【资产抽取 ≠ 视觉描述】环境/氛围/光线/天气/烟尘类词（晨光、山雾、寒气、雨丝、暖黄灯火、尘埃、风、阴影、烛火、倒影、波纹、山色、暮色）一律【不得】放进 script_facts.entities」「衣物/配饰飘动摆动归入 actions 不作为 costume 实体」；新增 `_nested_or_flat`（兼容旧扁平结构）/`_clean_visual_elements`（{name,type,confidence} 去重）/`_merge_visual_elements`（规则+Qwen 合并）；`_copy_entity` 带 asset_requirement；新实体 `asset_requirement=asset_requirement_for_type(qe["type"])`。
- **#365 清洗层分流（`director/entity_cleanse.py`）**：`_to_entry` 加 `requirement = asset_requirement_for_type(etype)`；`entered = source==SCRIPT and conf>=CONF_ENTER and requirement != AssetRequirement.NONE`（required/recommended 才进匹配）；条目新增 asset_requirement 键；`_collect_visual_elements(plan)`（遍历 shot.visual_elements 去重保序）；`_rebuild_gates(entry)` 按最终 type 重算 asset_requirement/entered/can_auto；`build_entity_registry` merged 后逐条 `_rebuild_gates`；`visual_only_list`（requirement==NONE）+ `visual_elements` 输出 = `_collect_visual_elements(plan)` + visual_only 旧数据兜底；funnel 新增 `visual_only`，`excluded = max(0, cleansed - entered - visual_only)`。
- **#366 类型硬约束 + 语义判定（`director/asset_matcher.py`）**：新增词表 `_SEMANTIC_INCOMPATIBLE_SUFFIX`（匣柄鞘尖盖套锁盒袋箱绳环钮扣夹把带面——旧剑→旧剑匣 剔除）/`_LOCATION_HIERARCHY_SUFFIX`（外内堂厅楼台殿阁口门前东西南北旁侧附近门口——山雨楼→山雨楼外）/`_LOCATION_HIERARCHY_PREFIX`（后院客栈酒楼…）/`_GENERIC_MODIFIERS_PREFIX`（含 旧破小大老新——石阶→后院石阶）；`scan_asset_library` 每条资产加 `kind` 字段（文件名前缀 角色_→cast 等）；`_infer_asset_kinds` 用 entered 实体名归一全等逆向锚定 unknown 资产 kind；`_type_compatible(etype, asset_kind)`：character→cast / location→location / prop 类→prop，asset_kind unknown 放行，etype None/unknown 放行；`_judge_relation` → match_kind（semantic_incompatible 剔除候选 / location_hierarchy / generic_reference / contains）；`match_one` 重写输出 `match_kind`/`suggest`（suggest = location_hierarchy or generic_reference）；`match_plan` 每条 match 透出 `asset_requirement`/`match_kind`/`suggest`，返回契约 4 键 `{assets, matches, visual_elements, funnel}`。
- **#367 前端分组展示（纯前端刷新即生效）**：`comfyApi.ts` `AssetScanResult` 加 `visual_elements` + funnel `visual_only`；`AssetMatch` 加 `asset_requirement/match_kind/suggest`；`WorkbenchView.vue` 资产匹配确认区重写为**分组展示**——`SCRIPT_MATCH_GROUP_DEFS`（角色/地点/道具）空组过滤，auto 显示「✓ 匹配」、suggest 显示「⚠ 建议参考 {{asset_name}} 相似度 {{confidence}}」+ [接受][重新选择] 按钮 + 接受态「建议已接受 ✓」+ 缩略图，pending 候选下拉保留，none 显示「✗ 未找到 · 进工作台手动补图」；新增**视觉元素区**「🌫 视觉元素 · 进 Prompt（无需资产匹配 N）」chips（name + 类型 em）；新增 `.script-assets-*` 样式族。
- **#368 测试 + 同步 + 验收脚本升级**：后端全量回归全绿（asset_matcher 63 / entity_cleanse 68 / script_analyzer 95 / script_parser 58 / prompt_builder 68 / camera_plan 50 / camera_template 67 / script_import 27 / text_backends 28 / segment_cache_identity 8）；前端 335 测试 + build 全绿 + dist 同步；`tools/golden_path_script_test.py` ⑨ 加 **Phase 1.1 契约断言**：visual_elements 是 list + funnel 含 visual_only + 每条 match 透出 asset_requirement/match_kind/suggest + suggest 与 match_kind 一致 + 视觉元素不混入匹配（name 不与 matches 重叠）+ 漏斗打印视觉元素计数 → **--mock 离线 14/14 PASS**（AI 发现 43 → 丢弃 34 → 清洗 12 → 进入 11 → auto 7 / 待确认 0 / 未匹配 4 / 视觉元素 1）；双目录 md5 全一致。
- **⛔ 纪律（延续）**：实体模式不回填 shot.props；AI 永不创建新资产只复用文件；纯规则零显存零 Ollama（Type 硬约束/语义判定/词表全部纯 Python）；#131 相对导入纪律保持。**前端刷新即生效；后端实体/清洗/匹配改动需重启一次 Comfy Desktop 生效**。
- **涉及**：`director/production_plan.py`、`director/script_analyzer.py`、`director/entity_cleanse.py`、`director/asset_matcher.py`、`director/tests/test_asset_matcher.py`、`tools/golden_path_script_test.py`、`frontend/src/services/comfyApi.ts`、`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/workbenchRender.test.ts`、`frontend/tests/assetConfirmDialog.test.ts`、`V17_PLAN.md`、`CHANGELOG.md`。

### #369 显存安全门竞态修复：/api/ps 卸载确认改为轮询等待（2026-08-11，用户真实 Ollama 验收驱动）

- **用户真实验收 13/14，唯一 FAIL=【⑬】**：`✓ PASS text_backend_active() 已复位 False` 但 `✗ FAIL /api/ps 仍驻留: ['qwen3:14b']`。用户分析确认：**Python Session 已关闭 ≠ Ollama runner 已释放**——`text_backend_active=False`（进程内锁）与 `/api/ps` 显示驻留（Ollama runner 生命周期）本来就是两个独立维度，两者同时成立是**正常可观测状态**，不是 Session 没关。根因 = **Ollama 的 keep_alive=0 卸载是异步的**：请求发出后 scheduler 释放 runner 需要时间，旧 `close()` 发完 keep_alive=0 **立即**查 /api/ps，查询落在卸载完成前 → 误报驻留。
- **修复（`director/text_backends.py`）**：`OllamaBackend.close()` 从「unload → 查一次 /api/ps」改为 **「unload → 轮询 /api/ps 直到模型消失」**——0.5s 间隔，默认 `unload_wait=15.0s`（`__init__` 新参数可调）；模型消失即返回；**超时仍未消失才 log.warning**（显存互斥铁律不放宽为接受驻留）。`__init__` 新增 `unload_wait` 参数；新增 `import time`。
- **修复（`tools/golden_path_script_test.py`）**：`_check_unload_and_lock` 同步改为**先轮询 /api/ps 等待模型消失（0.5s 间隔，默认 15s 超时）再判定**——超时仍驻留才 FAIL，避免「立即查询误报」。消息头更新为「keep_alive=0 + /api/ps 轮询等待」。
- **mock 测试（`director/tests/test_text_backends.py`）**：`_fake_urlopen` 的 /api/ps 分支新增**延迟卸载语义**——`_OL_STATE["unload_delay"]`（keep_alive=0 后还需几次 /api/ps 才真正消失）+ `pending_unload`/`unload_remaining`，默认 0（现有测试快速通过）；`_install_fake_urlopen` 重置异步卸载状态防跨测试残留；新增 **`test_ollama_close_polls_until_unloaded`**（unload_delay=2 模拟「前两次 /api/ps 仍驻留、第三次消失」，断言 close() 轮询 ≥2 次且退出时模型确认消失）→ **text_backends 29 项全 PASS（原 28 + 新 1）**。
- **回归**：golden_path --mock **14/14 PASS**（⑬ 轮询等待路径 PASS：`/api/ps 已确认 qwen3:14b 不再驻留`）；py_compile 双绿；双目录 md5 全一致。
- **⛔ 纪律（用户拍板）**：**千万不要为了让 14/14 PASS 把 /api/ps 检查删掉或放宽**——这个 FAIL 恰恰说明安全门在正常工作（成功发现 Python 层已结束但 Ollama 层未释放）；修复方向 = 等待异步卸载完成，而不是接受驻留。keep_alive=0 已确认真传到底层（`unload()` → POST `/api/generate` body `{"model": "qwen3:14b", "prompt": "", "stream": false, "keep_alive": 0}`，`_api` 直接 `json.dumps(payload)`）。
- **待办（用户提出 P0-2，与本次修复分离）**：本次 9 镜只 1/9 完整补全 + `ollama 输出非 JSON，重试一次` 出现两次 → Qwen 输出稳定性需单独调查（JSON schema / 批量 Scene Prompt / 上下文长度 / retry fallback），**两个问题不要混在一起修**。
- **涉及**：`director/text_backends.py`、`director/tests/test_text_backends.py`、`tools/golden_path_script_test.py`、`CHANGELOG.md`。

### #370 V1.7 Golden Path 前端导入→审核链路核对（#352，2026-08-11）：真实 ProductionPlan 走通 SPA

- **触发**：用户指示「先让这套真实的 ProductionPlan 进入 SPA，看看导入、Scene/Shot 展示、资产状态、审核按钮、修改后回写是否完整，再决定下一刀改哪里」。
- **核对结论（五个环节均完整）**：
  1. **导入**：`runScriptImport` → POST `/script/import`（规则拆 + Qwen 补全，失败自动降级 `rule_only` 不阻塞）→ `res.plan`；`scanAssetsForPlan`（/assets/scan）+ `scanPromptDrafts`（/prompt/draft）并行（均纯规则零显存，失败不阻塞审核）。
  2. **Scene/Shot 展示**：左侧 Scene block + Shot 卡片（虚边 `shot-draft` 未采纳 / 实边 `shot-draft-ok` 已采纳）；右侧 Scene 设置（名称/描述/时间/天气/地点/参考图/默认资产）+ Shot 设置（人物 chips 多角色/地点/生效资产继承/时长/Prompt 五区/运镜模板/衔接/尾帧）。
  3. **资产状态**：`buildConfirmedMatches`（auto 自动 + pending 用户确认 + ⚠ 建议参考 [接受][重新选择]）→ `productionPlanToProject` 绑 `castIds/locationId/imageFile`；生效资产 `inherit-grid` + 来源徽标；⚠ 资产未找到 → `AssetConfirmDialog` 补资产闭环（复用 /assets/scan）。**关键确认**：Phase 1.1 实体抽取后 `script_analyzer._merge_characters` 仍回填 `characters` 列表（规则优先 + Qwen 补空），前端 castIds 绑定不失效。
  4. **审核按钮**：审核横幅（N 镜待审核 + 就绪统计 + 采纳全部）+ 逐镜 🤖 ✓/⚠ badge + `shotReviewStatus` 三项判定（资产/Prompt/运镜）+ ✓ 采纳 + 已采纳标记。
  5. **修改后回写**：进工作台编辑 → `serializeProject` → `saveProject` → `loadProject` 往返；dirty 标记 + 退出保护（手动保存版，#100）。
- **契约 gap 修复（`frontend/src/services/comfyApi.ts`）**：后端真实 `Shot.to_dict()` 输出 Phase 1.1 的 `entities` + `visual_elements`，前端 `ProductionPlanJson.scenes[].shots` 未声明 → 补两组**可选**类型（`entities?`：entity_id/name/type/source/confidence/asset_requirement/aliases；`visual_elements?`：name/type/confidence）。TS 类型与运行时数据对齐；映射层不消费这两组（仅导入弹窗漏斗展示，`/assets/scan` 走 entity 注册表）。
- **真实形状回归测试（`frontend/tests/productionPlanToProject.test.ts` 新增 3 例）**：模拟后端真实 to_dict 形状（含 entities/visual_elements）→ 断言①走映射不抛错且 description 含 visual_intent + 原文；②castIds 绑定仍走 characters（entities 不参与）、序列化输出绝不含 entity_id/visual_elements（⛔ AI 不创建资产）；③planSummary 角色去重只看 characters。
- **回归**：productionPlanToProject 32 PASS（原 29 + 新 3）；全量前端 338 PASS（原 335 + 新 3）；`vue-tsc --noEmit` 通过；双目录 md5 全一致（纯前端改动，刷新 SPA 即生效，无需重启 Comfy Desktop）。
- **剧本原文审核区已实施（#370 续，用户拍板「加剧本原文审核区」）**：Shot 详情区「时长」与「Prompt」之间新增折叠块「📜 剧本原文」——`v-if="shot.description"`（无 description 的镜头不渲染）、默认展开（`scriptOriginalOpen = ref(true)`，导演审核参照高频）、`<pre>` 只读展示 `shot.description`（visual_intent 备注 + 逐字原文/对白）、折叠头复用 `wb-right-sub` + `ps-toggle` 视觉、`.script-original` 样式（pre-wrap / max-height 160px 滚动）。纯前端，刷新 SPA 即生效。
- **回归（含剧本原文区）**：`vue-tsc --noEmit` 通过；全量前端 **338 PASS**（原 335 + 3）；双目录 md5 全一致。
- **涉及**：`frontend/src/services/comfyApi.ts`、`frontend/src/workbench/WorkbenchView.vue`、`frontend/tests/productionPlanToProject.test.ts`、`CHANGELOG.md`。

### #371 ③ SPA 真实验收《山雨客栈》：真实 ProductionPlan → SPA Model 全链路（2026-08-11，用户拍板顺序 ③→①→②）

- **触发**：用户拍板开发顺序 ③ SPA 真实验收 → ① P0-2 Qwen 稳定性 → ② #351 真实复验。核心论证：「先让真实数据跑一遍 SPA，你会知道到底哪些 Qwen 字段真的影响 UI」；验收重点是 **Backend ProductionPlan → SPA Model → UI** 这条边，**暂不做生成视频**。
- **S1 · VM 规则层拆镜**：`parse_script_file` 跑《山雨客栈.md》→ 3 场景（山雨楼外/客栈大堂/后院石阶）2+6+1=9 镜，duration 全在 [2,8]，location_name 正确，source_text 逐字保留。
- **S2 · VM 实体清洗+资产匹配**：模拟 Qwen（_merge_shot → build_entity_registry → match_plan）→ 7 auto（3 角色+3 地点+旧剑匣）、旧剑 none（semantic_incompatible 剔除旧剑匣候选）、视觉元素全进 visual_elements 不进 matches；漏斗 discovered=26→cleansed=11→entered=11→auto=7/none=4。
- **S3 · 前端 productionPlanToProject 映射验收**（`frontend/tests/productionPlanToProject.test.ts` 末尾新增 describe 块「③ SPA 真实验收」）：真实《山雨客栈》9 镜 fixtures 断言 6 项——①Scene 顺序+2+6+1=9 ②前端 Shot.id 全局唯一连续 shot_01..shot_09 且 order 场景内保留 ③shot_id/source_text/duration_sec 逐字不变（description 含「原文：」）④角色→castIds（空镜 shot_01 undefined/shot_06 蒙面人/shot_07 三人）⑤地点→locationId+defaultLocationId+locations 资产 ⑥旧剑匣 imageFile 填充/旧剑留空 + 视觉元素不进资产列表 + 输出无 entity_id/visual_elements/asset_requirement。39/39 PASS；全量前端 345 PASS | 1 skipped；`vue-tsc --noEmit` EXIT=0。
- **S4 · 用户本机真实验收脚本 `tools/spa_acceptance_shanyu.py`（新建）**：复用 golden_path_script_test.py 路径解析 + FakeTextBackend 模式。7 段 21 项断言=①规则拆 Scene/Shot ②Qwen 语义补全（validation.warnings 语义 + shot_id/source_text/duration_sec 逐字不变）③④⑤⑥资产匹配（3 角色 auto/3 地点 auto/旧剑匣 auto/旧剑 none/视觉元素不进 matches）⑦五区草稿（9 条 + camera 含运镜词 + 五区齐全）。落盘 3 个真实形状 fixtures：`frontend/fixtures/shanyu_real_plan.json`（ProductionPlanJson）/`shanyu_real_matches.json`（7 条 auto ConfirmedAssetMatch）/`shanyu_real_drafts.json`（PromptDraftItem）。浏览器核对清单 7 条（导入 3 场景 9 镜 3 角色/Scene 顺序/剧本原文对照区/资产匹配状态/视觉元素不进资产/逐镜采纳+改 Prompt 变橙/暂不生成视频）。
- **mock 场景序号 bug（自修）**：`analyze_scene` 批量 prompt 每场景内从 `[1]` 重新编号（场景1=[1..2]/场景2=[1..6]/场景3=[1]），FakeTextBackend 初版用全局 key 直接索引 → 场景 2/3 错配（烛台/酒坛漏显示）。修= `_offset` 累计已处理镜头数把场景内序号映射回全局 key。修复后 21/21 PASS EXIT=0，漏斗完整（烛台/酒坛正确 none）。真实 Ollama 链路无此问题（Qwen 按 prompt [n] 返回场景内序号）。
- **回归/同步**：双目录 md5 全一致（脚本 + 3 fixtures）；纯后端脚本 + 前端 fixtures，不重启 Comfy Desktop。
- **真实 fixture 回归暴露映射缺口并修复（#371 续）**：新增前端测试「真实 fixture（shanyu_real_plan/matches.json）走映射全链验收」首次运行 **1 FAIL**——`collectSceneAssets` 只读 `shot.props`，但 Phase 1.1 后真实后端道具走 `shot.entities`（type=prop），`shot.props` 保持空 → 旧剑匣/青瓷碗/烛台/酒坛等道具资产在 SPA 映射中全部丢失。**这正是用户预言的「先让真实数据跑一遍 SPA，你会知道到底哪些 Qwen 字段真的影响 UI」**。修复=`productionPlanToProject.ts::collectSceneAssets` 道具合并收集（`shot.props` 规则层兼容 + `shot.entities` type=prop 实体层）；类型 `ProductionPlanJson.shots[].entities?` 已有（#370 契约补齐），无需再动 comfyApi.ts。修复后全量前端 **346 PASS**（原 345 + 真实 fixture 回归 1）+ `vue-tsc --noEmit` EXIT=0；双目录 md5 全一致。纯前端刷新 SPA 即生效。
- **涉及**：`tools/spa_acceptance_shanyu.py`（新建）、`frontend/src/core/productionPlanToProject.ts`、`frontend/tests/productionPlanToProject.test.ts`、`frontend/fixtures/shanyu_real_{plan,matches,drafts}.json`（新建）、`CHANGELOG.md`。
- **下一步（用户拍板顺序）**：用户本机跑 `"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\spa_acceptance_shanyu.py` 真实 Ollama 全链路 + 浏览器按 7 条核对清单走查导入→审核；验收通过后进入 ① P0-2 Qwen 稳定性调查（9 镜仅 1/9 完整补全 + JSON 重试×2）。

### #375 P0-3 实体污染修复：镜头标记/称谓不得进 characters/locations/props（2026-08-11，用户 SPA 真实验收驱动）

- **触发（产品级 bug）**：用户 SPA 真实页面验收《山雨客栈》发现——**角色 11**（柳如烟✓/沈青崖✓ + 镜头一~九✗）、地点误含「山道」（Shot 内空间描述被升级成 Scene Location）、AI Draft 出现「中景，缓慢推近镜头二、沈青崖……」内部标记泄漏。**后端 22/22 PASS 只验证了「正确角色被识别」，没验证「错误实体被排除」= 验收设计漏洞**。用户拍板：「不要为了追求 22/22 PASS 而继续加测试数量，应该先把镜头一~九从角色系统里彻底消灭」；⛔ **不要改前端 UI 过滤（治标）**，正确链路 = 剧本→Parser→Qwen→Entity Cleaner→丢弃镜头标记→ProductionPlan→SPA。用户建议模式 `^(镜头|shot)\s*[\d一二三四五六七八九十百]+` + 第X镜/镜头X/shot_X/shot-X → 统一归类 `invalid_entity` reason=shot_marker；称谓（老板娘/客官/一个人/有人）也判定。
- **根因三层**：① `script_parser._collect_candidate_names` 的「X：」对白行首规则把镜头标记行「镜头一：远景…」当说话者 → 进 shot.characters；② `entity_cleanse._collect_rule_character_names`（seeds 角色兜底）从 shot.characters 提取且不过滤 INVALID_ENTITY_PATTERNS → 注册表 95% 置信度 character「镜头一」（95%=CONF_RULE_CHAR=0.95 种子，铁证）；③ `prompt_builder._subject_text` 用 shot.characters 拼接 → 「镜头二、沈青崖」。
- **修复（三层，源头+清洗双保险）**：
  - `script_parser.py`（规则源头）：`_collect_candidate_names` 跳过 `_SHOT_MARK_RE` 匹配行（镜头标记行不参与角色候选）；`_dialogue_from_line` 开头 `if _SHOT_MARK_RE.match(line): return None`（镜头标记行不是对白）。
  - `script_analyzer.py`（Qwen 源头）：`_merge_characters` 重写，规则+Qwen 两层都过 `is_invalid_entity_name`——镜头标记/称谓伪实体一律不进入 shot.characters。
  - `entity_cleanse.py`（清洗兜底）：新增 `invalid_entity_reason(name)`（reason=shot_marker/appellation/empty）+ `_APPELLATION_NAMES`/`_AMBIGUOUS_SHORT_NAMES` 称谓黑名单（老板娘/客官/一个人/有人/男子/女子/大侠/两人 等）+ INVALID_ENTITY_PATTERNS 补 `^第X个镜头`；`_collect_rule_character_names`/`_collect_rule_prop_names` 加过滤；**Location 层级降级**=新增 `_scene_location_set`/`_location_aligned`/`_degrade_unaligned_locations`——Shot 内空间描述（山道/檐角/门口/柜台/桌边）不对齐 Scene 正式地点（scene.location_name）→ 降级 `type=environment` 进 visual_elements（留 Prompt）不进 Location 资产匹配；对齐的层级参考（山雨楼 ⊂ 山雨楼外）保留 location。
- **测试**：新建 `director/tests/test_entity_pollution.py`（P0-3 专项验收 25 断言）——①规则层源头（镜头一：行不产生「镜头一」角色、speaker 正确）；②`_merge_characters` 过滤 Qwen 幻觉（镜头一/客官/老板娘）；③注册表兜底（shot.characters 已污染 → 注册表仍干净）；④Qwen entities 路径（invalid_dropped 计数 + entered 无镜头标记）；⑤Location 层级（山雨楼保留/山道+檐角降级 environment 进 visual_elements）；⑥asset_matcher 端到端（matches 无镜头一/客官/山道）。**后端全量回归**：entity_cleanse 68 + parser 58 + analyzer 95 + matcher 63 + prompt_builder 68 + pollution 25 = **377 PASS / 0 FAIL**；py_compile 全绿；双目录 md5 全一致。
- **涉及**：`director/entity_cleanse.py`、`director/script_parser.py`、`director/script_analyzer.py`、`director/tests/test_entity_pollution.py`（新建）、`CHANGELOG.md`。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。用户本机动作：重新导入《山雨客栈》剧本验证「角色 3」。
- **新开发顺序（覆盖先前 ③→①→②）**：P0-3（本次）→ **P0-4 Prompt Builder 内部标记泄漏**（shot_id/scene_id/镜头编号只作结构元数据，不进 visual/camera/style/sound）→ **P0-5 重跑 SPA《山雨客栈》验收**（目标=角色 3/地点 3/道具 1+/视觉元素 N，且补「错误实体被排除」断言）→ P0-2 Qwen 稳定性（正确性优先）→ #351。

### #376 P0-4 Prompt Builder 内部标记泄漏修复（2026-08-11，P0-3 直接后续）

- **触发**：P0-3（#375）后用户报告的另一面——AI Draft「中景，缓慢推近镜头二、沈青崖……」内部标记泄漏。P0-3 修的是「镜头一~九进角色系统」（characters 污染），P0-4 修的是「镜头编号进五区措辞」（visual_intent/emotion 文本泄漏）。用户拍板：「shot_id/scene_id/镜头编号只能作结构元数据，不进 visual/camera/style/sound」。
- **根因**：五区措辞的文本来源全来自 Qwen 生成的语义字段——`visual` 区直接拼 `shot.visual_intent`（Qwen 原文含「镜头二」）；`camera`/`sound` 区拼 `shot.emotion`（可能含编号）；fallback subject 拼 `shot.characters`（P0-3 已干净，仍加防御）。
- **修复（公共清洗模块 + 全措辞接入）**：
  - 新建 `director/prompt_sanitize.py`：`strip_internal_markers(text)` 统一清洗入口——只删「镜头编号」类内部标记（`镜头+N` / `第N镜` / `shot_N` / `scene_N` / 括号包裹【镜头一】/（第2镜）/ `scene:shot` 组合），**「远景镜头」这类「镜头」不带编号不误删**；清洗后做残留标点清理（孤立顿号/逗号/冒号），避免「中景，缓慢推近、沈青崖」式残渣。`INTERNAL_MARKER_PATTERNS` 可测。纯规则零显存。
  - `prompt_builder_v17.py`：import + `_subject_text`/`_actions_hint`/`_props_hint`/`_build_visual`（visual_intent 清洗后为空回退 fallback）/`_build_camera`（emotion）/`_build_sound`（emotion）全部过清洗。
  - `camera_template.py`：import + `_subject_text`/`_props_text`/`pick_camera` emotion 清洗（camera 措辞不泄漏）。
- **测试**：新建 `director/tests/test_prompt_sanitize.py`（34 断言）——①18 种标记模式清洗 + 「远景镜头/镜头切换/山道」等正文不误删 ②build_shot_draft visual_intent 含「镜头二」→ visual 区不含且保留沈青崖 ③emotion 含「镜头七」→ camera/sound 区不含 ④fallback subject 脏字符过滤 ⑤pick_camera 脏字符不进 camera 措辞。**后端全量回归**：prompt_builder 68 + camera_template 67 + camera_plan 50 + prompt_sanitize 34 + entity_pollution 25 + entity_cleanse 68 + parser 58 + analyzer 95 + matcher 63 = **528 PASS / 0 FAIL**；py_compile 全绿；双目录 md5 全一致。
- **涉及**：`director/prompt_sanitize.py`（新建）、`director/prompt_builder_v17.py`、`director/camera_template.py`、`director/tests/test_prompt_sanitize.py`（新建）、`CHANGELOG.md`。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**下一步 = P0-5 重跑 SPA《山雨客栈》验收**（目标=角色 3/地点 3/道具 1+/视觉元素 N，且补「错误实体被排除」断言）。

### #377 P0-5 重跑 SPA《山雨客栈》验收：补「错误实体被排除」断言（2026-08-11，P0-4 直接后续）

- **触发**：用户拍板「P0-5 重新运行 SPA《山雨客栈》验收，目标=角色 3/地点 3/道具 1+/视觉元素 N」——上轮 ③ S4 验收只验证了「正确角色被识别」，没验证「错误角色被排除」，正好是真实 SPA 页面「角色 11（镜头一~九）」的验收设计漏洞。本次把「错误实体被排除」固化为验收点。
- **增强 `tools/spa_acceptance_shanyu.py`（真实验收脚本，非新增测试）**：
  - ⑧ 错误实体被排除（P0-3）：规则层角色无伪实体（`is_invalid_entity_name`）/ Qwen 补全后角色无伪实体 / matches 无镜头标记与称谓伪实体 / **角色 matches 恰好 3**（沈青崖·柳如烟·蒙面人）/ **地点 matches 恰好 3 正式地点**（山雨楼外·客栈大堂·后院石阶）/ Shot 内空间描述（山道/檐角/门口/柜台/桌边）不升级为 Location 资产 / registry entered 无伪实体 / **注入式验证清洗能力**——把「镜头一/镜头七/客官」塞进 shot.entities（真实 SPA「角色 11」污染路径）→ entered 无这些 + invalid_dropped ≥ 3。
  - ⑨ AI Draft 内部标记不泄漏（P0-4）：真实链路 9 镜五区措辞无内部标记（`镜头N/第N镜/shot_N/scene_N`）+ **注入式验证**——visual_intent 混入「镜头二、沈青崖」、characters 混入「镜头一」→ 草稿清洗后无标记且保留正文「沈青崖」。
  - 浏览器核对清单更新：角色 3（无「镜头一~九」）+ AI Draft 无内部标记。
- **结果**：mock 离线自测 **33 PASS / 0 FAIL**（链路：规则拆镜 → Qwen 补全 → 实体清洗 → 资产匹配 → 五区草稿全通）；py_compile 全绿；双目录 md5 全一致。
- **涉及**：`tools/spa_acceptance_shanyu.py`（增强）、`CHANGELOG.md`。
- **⛔ 纯脚本增强，无 Python 模块改动，无需重启 Comfy Desktop**。**下一步 = 用户本机跑真实 Ollama 版验收**：`python tools\spa_acceptance_shanyu.py`（Ollama 需已启动且有 qwen3:14b），目标=角色 3/地点 3/道具 1+/视觉元素 N；随后 SPA 导入《山雨客栈》按核对清单核对（角色 3 无镜头一~九 / AI Draft 无「镜头N」）；验收后进 **P0-2 Qwen 输出稳定性调查**（9 镜仅 1/9 完整补全 + JSON 重试×2，与 #369 分离不混修）。

### #378 P0-2 Qwen 输出稳定性：双轨制规则兜底 + 地点精确对齐 + visual_elements 去重 + 实体分类收紧（2026-08-11，用户拍板「现在修」）

- **触发**：用户 2026-08-11 真实 SPA 验收三处 FAIL 驱动，用户拍板「P0-2 现在修，先别进 Phase 2 Asset Registry」——①**蒙面人漏检（角色 3→2）**：Qwen 未返回蒙面人→规则层也没兜住→注册表直接丢角色（「必须修掉再往下走，否则 Asset Registry 建在会随机漏角色的输入层上」）；②**地点 matches 恰好 3 得了 5**：`_location_aligned` 互含判定让 山雨楼（⊂山雨楼外）/石阶（⊂后院石阶）放行升级；③**visual_elements 与正式地点重复**：后院石阶/客栈大堂 已进 location_name 又重复进 visual_elements。
- **用户架构铁律（双轨制 dual-track）**：`剧本原文 → [规则实体提取 | Qwen语义分析] → 合并器 → Entity Resolver → Cast/Location/Prop → Asset`。规则层负责「不能漏」（剧本原文里的核心实体必须先建候选），Qwen 负责「理解」（代词消解等），**Qwen 未返回的实体不得被删除**。
- **修复① 双轨制规则兜底（`director/script_parser.py`）**：
  - 动作主语角色提取：对每个强动词（`_STRONG_VERB_RE`：推/走/落座/环视/拔/退/立/走近…）向前回退到句界，取句首 2-4 个 CJK 字符作候选名，窗口校验（≤6 字且全在 `_ACTION_WINDOW_OK`），环境/道具后缀过滤（`_ENV_SUBJECT_SUFFIX`），场景级频次≥2（`_scene_action_subject_names`）。→ 蒙面人/柳如烟 即使无对白无括号也进角色候选（修复前规则层 0 个动作主语）。
  - 动作宾语道具提取：`_PROP_OBJECT_RE` = 道具动词（背/端/放/拔/抽/举/斟/沏…）+ 桥接字 + 量词 + 2-4 CJK 名词（`_PROP_NOUN_SUFFIX`：剑/碗/匣…）。→ 旧剑匣/茶碗 规则兜底进 props。
  - Qwen 只增不删：`entity_cleanse.build_entity_registry` seed 策略改为「规则 seed（含旧剑匣/茶碗）仅在 qwen_norms 已含该名时被覆盖」，Qwen 未返回的规则实体照常补位进匹配。
- **修复② 地点精确对齐（`entity_cleanse._location_aligned`）**：互含判定 → **exact-only**（`return key in scene_locs`）。山雨楼/石阶 不对齐 → 降级 ENVIRONMENT 只进 visual_elements（Prompt）；山雨楼外/客栈大堂/后院石阶 精确 → 保留 Location 进匹配。
- **修复③ visual_elements 去重（`_collect_visual_elements`）**：跳过 `_scene_location_set(plan)`，正式 Scene 地点不再重复进 visual_elements（山道作为空间描述进 visual_elements 仍可接受）。
- **修复④ 实体分类收紧（`production_plan.asset_requirement_for_type`）**：character/location→**required**；prop/vehicle/creature→**recommended**；**costume/architecture/environment/effect/unknown→none**（青衫/檐角/山道 只进 Prompt，不进资产匹配）。服装(青衫)/天气(雨歇)/光线(晨光)/氛围(阴影)/动作(推门)/空间描述(山道) 一律只进 Prompt。
- **测试更新 + 新增 backstop**：
  - `test_script_parser.py` 新增 `test_p02_dual_track_backstop`（[16]）：无对白无括号文本→沈青崖/柳如烟/蒙面人 全进角色；旧剑匣/茶碗 进 props；镜头标记/暖黄灯火/衣袂带风/大堂 不进角色。
  - `test_entity_cleanse.py` 按新契约更新 `test_registry_qwen_mode`（entered=3/visual_only=3/seeded=1；山雨楼→environment/青衫→costume none）+ 新增 `test_visual_elements_skip_scene_locations`（[8] 正式地点不进 ve）。
  - `test_entity_pollution.py` 重写 `test_location_hierarchy_degradation`（山雨楼/山道/檐角 全降级 ENVIRONMENT）+ 新增 `test_qwen_drop_keeps_rule_character`（[7] Qwen 只返回沈青崖→柳如烟/蒙面人 仍被规则保留，置信度=CONF_RULE_CHAR）。
  - `test_asset_matcher.py` 重写 `test_match_plan_entity_mode_gates_garbage`（[10] matches=2：沈青崖 cast auto + 山雨楼外 location auto；青衫/山雨楼/雨雾 只进 visual_elements；funnel auto=2/pending=0）。
  - `tools/spa_acceptance_shanyu.py` ⑧ 段新增两断言：**P0-2 双轨制规则层角色兜底含 3 核心角色**（EXPECTED_ROLES ⊆ rule_chars）+ **正式 Scene 地点不再进 visual_elements**（EXPECTED_LOCATIONS ∩ ve_names = ∅）。
- **修复⑤ 道具正则贪婪吞字（`script_parser._prop_noun_from_capture`，2026-08-11 VM 回归补修）**：`_PROP_OBJECT_RE` 的 `([一-龥]{2,4})` 贪婪捕获会把道具名词后的动词/副词/方位吞进来——「背着旧剑匣从山道」→`旧剑匣从`、「放下茶碗却不喝」→`茶碗却不`，`endswith(后缀)` 校验失败 → 规则层漏掉 旧剑匣/茶碗（恰好是「规则层不能漏」的反例）。修=新建 `_prop_noun_from_capture()`：在捕获串内部找**以道具后缀结尾的最长合法名词前缀**（`rfind` 后缀 + 2-4 字 + 全 CJK），旧剑匣/茶碗 从被吞串里还原。回归又暴露两个噪声道具：①`来一碗`（「端来一碗热茶」——`来` 不在桥接字表，量词前动词补语被当名词）→ 桥接字加「来」；②`昏黄灯`（「挂着昏黄灯笼」——`笼` 不在后缀表，退取 3 字 `昏黄灯`）→ 后缀表加「笼」得完整 `昏黄灯笼`（灯笼是剧本真实物体，语义可接受）；另加桥接字「出」消 `出旧剑`（「拔出旧剑」）。**修复后真实剧本规则道具集=`[旧剑, 旧剑匣, 昏黄灯笼, 茶碗, 青瓷碗]`**——目标道具全含、`来一碗/昏黄灯/出旧剑` 全消。
- **VM 全量回归（2026-08-11）**：`test_script_parser` 58 + `test_entity_cleanse` 71 + `test_entity_pollution` 33 + `test_asset_matcher` 63 + `test_script_analyzer` 95 + `test_prompt_sanitize` 34 = **334 PASS / 0 FAIL**；`tools/golden_path_script_test.py --mock` **14/14 PASS**（角色 auto 绑定 柳如烟/沈青崖/蒙面人，旧剑匣 auto）；`tools/spa_acceptance_shanyu.py --mock` **35 PASS / 0 FAIL**（角色 matches 恰好 3、地点 matches 恰好 3 正式地点、规则层兜底含 3 核心角色、正式地点不进 visual_elements、无伪实体、AI Draft 无内部标记）；双目录 md5 全一致。
- **涉及**：`director/script_parser.py`、`director/entity_cleanse.py`、`director/production_plan.py`、`director/tests/test_script_parser.py`、`director/tests/test_entity_cleanse.py`、`director/tests/test_entity_pollution.py`、`director/tests/test_asset_matcher.py`、`tools/spa_acceptance_shanyu.py`、`CHANGELOG.md`。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**✅ 真实 Ollama 验收 PASS（2026-08-11 用户实测）**：`D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe tools\spa_acceptance_shanyu.py`（源目录不带 --mock）→ **35/35 全绿**——3 Scene / 9 Shot / 3 Character（含蒙面人）/ 3 Location 正式地点 / 旧剑匣等正确 Props / 无镜头一~九 / 无重复 Location visual_elements。**P0-2 正式关闭，进入 Phase 2 Asset Registry**。

### #379 Phase 2-1：Asset Identity + Persistent Binding（稳定资产身份 + 持久绑定 Checkpoint）（2026-08-11，用户拍板）

- **触发**：用户批准 Phase 2-1（进入 Phase 2 Asset Registry 前先建稳定地基），五条要求——①真正稳定的 **Asset IDs**（`asset_001`/`asset_002` 永久绑定实际图片资产，不等于显示名称）；②**entity_id 与 asset_id 分离**（Entity = 剧本实体身份 + canonical_name + type；Asset = 永久资产身份 + name + path；Binding = entity_key → asset_id）；③**人工确认必须真正落盘**（重新导入复用已接受的确认，不再重新 pending）；④**Registry 绝不反向污染实体发现**（数据流单向：剧本 → ProductionPlan → Entity Registry → Cleanse → Asset Matcher → Persistent Binding）；⑤置信度集中配置顺带做但不扩大范围。
- **约束（延续）**：显存安全门铁律（#369，绝不删/放宽 /api/ps 检查）；纯规则零显存零 Ollama；AI 绝不覆盖已有资产只复用文件；⛔ 不改前端 UI 过滤治标。
- **后端（`director/`）**：
  - 新建 `asset_registry.py`（**纯规则持久化 Binding Checkpoint**，52 断言）：`AssetRegistry` 落盘 `{ComfyUI input}/minimax_studio/asset_registry.json`；稳定 **entity_key = `{清洗后最终类型}:{canonical_name}`**（如 `character:柳如烟` / `location:山雨楼外`）；永久 **asset_id = `asset_{N:03d}`** 顺序分配但永远绑定 image_file；`Binding(entity_key → asset_id)` + `AssetRecord(asset_id/name/path)`；**accepted（source=user）覆盖 matcher**（重新匹配时命中 registry → `match_kind="persisted"`，不再重新 pending）。
  - `asset_matcher.py`：消费注册表——**persisted 命中优先**（覆盖 exact 1.0 / 归一 0.95 / 子串 0.8 / none）；**数据流单向锁死**：Entity 发现 → 清洗 → 匹配 → 绑定，注册表只做「绑定结果持久化」，**绝不反向回填实体候选**（Registry 不产生新实体，不污染剧本原文）。
  - `http_routes.py`：`POST /minimax/director/assets/binding`（保存绑定集，`asyncio.to_thread` 零显存）+ 读取路由（前端重开项目/重新导入时拉取已接受绑定）。
  - `entity_cleanse.py`：实体清洗后输出 entity_key（供 registry 主键）。
- **前端（`frontend/`）**：
  - `src/services/comfyApi.ts`：`AssetBinding`/`AssetBindingPayload` 接口 + `saveAssetBinding()`/`fetchAssetBindings()`；`AssetMatch` 补 `entity_key: string` / `asset_id: string | null` / `binding_status?` / `binding_source?`。
  - `src/core/productionPlanToProject.ts`：`ConfirmedAssetMatch` 补 `entity_key?`/`asset_id?`/`sourceEntityId?`/`userConfirmed?`；`collectSceneAssets` cast/props/locations push 均带 `sourceEntityId`（#371 S5 后资产也带来源实体 id，重新导入经 entity_key 复用确认）。
  - `src/models/project.ts`：`Asset.sourceEntityId?`（剧本导入自动匹配时记录；手工建资产/旧项目无）。
  - `src/workbench/WorkbenchView.vue`：`scriptBindings` 状态 + `persistConfirmedBindings()`——**用户确认（pending 选候选 / 接受建议）source=user 落盘，纯 auto 匹配 source=system**；匹配区 `persisted` 徽标；**重新导入时复用已接受的绑定，不重新 pending**。
- **本会话附带规则层修复（镜头 7 漏检沈青崖）**：`director/script_parser.py`——①`_STRONG_ACTION_VERBS` 补「起身」；②`_SUBJECT_BOUNDARY` 补中文引号「」『』（对白「剑，交出来。」后的动作「沈青崖起身，横剑护在柳如烟身前」主语回溯不被引号阻断）→ 镜头 7 正确提取 `[蒙面人, 沈青崖, 柳如烟]`（此前漏检沈青崖）；`frontend/fixtures/shanyu_real_plan.json` 重新落盘。
- **测试/验证**：后端全量回归全绿——asset_registry 52 / script_parser 58 / script_analyzer 95 / entity_cleanse 71 / entity_pollution 33 / asset_matcher 63 / prompt_sanitize 34 / prompt_builder 68 / camera_plan 50 / camera_template 67 / script_import 27 / text_backends 29；前端全量 **351/351 PASS** + `vue-tsc -b && vite build` **全绿**（含 tests 目录 TS 类型收紧）；双目录 rsync checksum 复验 **0 差异**。
- **涉及**：`director/asset_registry.py`（新建）、`director/asset_matcher.py`、`director/http_routes.py`、`director/entity_cleanse.py`、`director/script_parser.py`、`director/tests/test_asset_registry.py`（新建）、`frontend/src/{core/productionPlanToProject.ts, models/project.ts, services/comfyApi.ts, workbench/WorkbenchView.vue}`、`frontend/tests/{productionPlanToProject,assetConfirmDialog}.test.ts`、`frontend/fixtures/shanyu_real_{plan,matches,drafts}.json`、`CHANGELOG.md`。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**下一步 = 用户本机验证**：重启 Comfy Desktop → 重新导入《山雨客栈》→ 核对**已接受的绑定被复用（不重新 pending）** + `asset_registry.json` 已落盘 + 资产匹配确认区 `persisted` 徽标。

### #380 P0-3b：动作/空间短语→实体名粘连修复（持灯退到柜台边→「灯」）（2026-08-11，用户拍板「先让开发侧做一个很小的修复」）

- **触发**：用户 SPA「🎨 资产自动匹配」真实输出 `灯退到柜 道具 90% ✗ 未找到`——根因定位在 **Asset Matcher 之前的实体抽取/清洗层**（与已修的「镜头一/镜头七/客官」污染同类，非 Matcher 匹配错）。「柳如烟持灯退到柜台边」被拆出实体「灯退到柜」= 动作/空间短语粘进实体名。
- **约束（用户明确拍板）**：⛔ **通用规则，不用黑名单**（`if entity == "灯退到柜": drop` 被拒绝）；纯规则零显存零 Ollama；不改前端 UI 过滤治标；显存安全门铁律（#369）延续。
- **修复 ① `director/script_parser.py`（规则层兜底）**：
  - `_PROP_OBJECT_VERBS` 定义后新增公开别名 `PROP_OBJECT_VERBS = _PROP_OBJECT_VERBS`，供 entity_cleanse 复用（**单一来源防两表漂移**）；
  - `_PROP_NOUN_SUFFIX` 补单字道具后缀 `"茶", "酒"`（端茶/斟酒/点灯/提壶）；
  - `_PROP_OBJECT_RE` 捕获组 `{2,4}` → `{1,4}`（**单字道具合法化**：持灯/端茶/拔刀；此前 `她持灯` 后接标点只有 1 字匹配不上=假阴性）；
  - `_prop_noun_from_capture`：动作短语阻断逻辑（最早 ACTION_VERBS 索引截断），docstring 落实「长度门槛从 2 放宽到 1」。
- **修复 ② `director/entity_cleanse.py`（清洗层兜底）**：
  - import 同时引入 `ACTION_VERBS` + `PROP_OBJECT_VERBS` 两表；
  - 新增 `_ACTION_BRIDGE_PREFIXES`（按长度降序）：`("正在", "着", "了", "过", "地", "得", "又", "也", "已", "便", "就", "只", "再")`；
  - 重写 `_cleanse_action_phrase_name`：while 循环**同时扫描两表**——首位动词裁掉 / 中间动词裁到动词前 / 末尾动词跳过；裁剪后循环剥 `_ACTION_BRIDGE_PREFIXES`（「背着旧剑匣走来」→ 背着→着→旧剑匣）；CJK-only 校验；**整名动作短语返回 None**（调用方按 invalid_dropped 丢弃）；
  - `_to_entry` 改 `Optional[Dict]`，先过 cleanse，None 直接返回；`build_entity_registry` 中 `entry is None → invalid_dropped += 1; continue`；
  - `_collect_visual_elements` 用 `_ve_is_scene_location`：**剥中文类型后缀（环境/特效/建筑/服装…）+ exact/互含匹配跳过正式 Scene 地点变体**（后院石阶环境/客栈大堂环境/山雨楼建筑不进 visual_elements；山道环境/檐角建筑/雾气环境保留）。
- **`director/script_analyzer.py` 确认无需改动**：两处 Qwen prompt 已含「实体名=纯名词」硬约束（「柳如烟持灯退到柜台边的实体是『灯』，不是『灯退到柜』；动作短语退到/走向/扑来/拿起不得拼进实体名，动作归 actions，方位归 visual_intent」）。
- **测试/验证**：
  - `test_script_parser.py`：新增 [17] `test_p03b_action_phrase_props`——用户 5 回归用例（持灯退到柜台边→灯 / 端茶走到桌边→茶 / 拔刀扑来→刀 / 背着旧剑匣走来→旧剑匣 / 拿起茶碗放在桌上→茶碗）+ 3 负例（放牛/扫地/说笑非道具宾语）；[15] `test_visual_intent_empty` 更新（收伞→道具伞，无道具断言移到 shots[2]）；**补调用 [16] `test_p02_dual_track_backstop`（此前定义了 main() 从未调用=回归漏洞）**；74 PASS / 0 FAIL。
  - `test_entity_cleanse.py`：新增 [9] `test_p03b_action_phrase_cleanse`（9 清洗用例含灯退到柜→灯/剑走向门→剑/持灯→灯/端茶→茶 + 12 纯名词原样保留 + 「拿着」→None 丢弃）+ [10] `test_p03b_visual_elements_skip_suffixed_locations`（3 场景后缀变体不进 ve，非正式保留）；99 PASS / 0 FAIL。
  - `tools/spa_acceptance_shanyu.py`：⑩ 增强——`_is_verb_contaminated`（cleanse 后名 ≠ 原名即污染）断言 matches/entered 无粘连名；注入「灯退到柜/剑走向门/拿着」验证 entered 无粘连 + invalid_dropped 增 ≥1；断言正式地点后缀变体不在 visual_elements；**基线修正 `base_invalid = reg["funnel"]["invalid_dropped"]`（曾误用 polluted_reg 基线导致断言失败）**；--mock **43 PASS / 0 FAIL**。
  - `golden_path_script_test.py --mock` **14/14 PASS**；后端全量回归 **662 PASS / 0 FAIL**（11 个标准格式测试文件）+ 其余 6 文件全绿；`py_compile` 双绿；双目录 md5 **5/5 一致**。
- **涉及**：`director/script_parser.py`、`director/entity_cleanse.py`、`director/tests/test_script_parser.py`、`director/tests/test_entity_cleanse.py`、`tools/spa_acceptance_shanyu.py`、`CHANGELOG.md`。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**下一步 = 用户本机跑真实 Ollama 版验收**：`tools/spa_acceptance_shanyu.py`（不带 --mock）应 43/43 全绿——角色 matches 恰好 3 / 地点恰好 3 / 镜头伪实体 0 / 正式地点进 visual_elements 0 / 无「灯退到柜」类粘连名 / 道具集=旧剑/旧剑匣/昏黄灯笼/茶碗/青瓷碗；验收通过后关闭 #395 → Phase 2-1 关闭条件核验（asset_registry.json 含 accepted 绑定 + 二次导入 persisted）→ 进 Phase 2 正式 Asset Registry 全量。

### #381 P0-3b 真实验收 44/44 PASS + script_analyzer 双目录同步遗漏修复（2026-08-11）

- **✅ 真实 Ollama 验收 PASS（2026-08-11 用户实测）**：`"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\spa_acceptance_shanyu.py`（源目录不带 --mock）→ **44 PASS / 0 FAIL**——3 Scene / 9 Shot / 角色 matches 恰好 3（沈青崖/柳如烟/蒙面人）/ 地点恰好 3 正式 / 无镜头一~九 / 注入「灯退到柜/剑走向门/拿着」→ entered 无粘连名（灯/剑保留、拿着丢弃 invalid_dropped 增 ≥1）/ 正式地点带后缀变体（后院石阶环境/客栈大堂环境/山雨楼建筑）不进 visual_elements / AI Draft 五区无内部标记。漏斗 discovered=32→cleansed=21→entered=18→auto=7→pending=0→none=11（**刀/剑/旧剑/昏黄灯笼/桌/热茶/短刃/窄刀/茶碗/青瓷碗/老槐树→none 是正确行为**：资产库无对应图片，绝不因数字好看而强匹配/创建资产）。
- **P0-2 + P0-3 + P0-3b + P0-4 全部收口**：用户拍板「不要继续在《山雨客栈》上堆规则」，正式进入 Phase 2 Asset Registry。
- **⚠ 发现并修复 P0-3b 同步遗漏**：全量 .py 双目录 md5 排查发现 `director/script_analyzer.py` **SRC 与 DEP 不一致**——SRC 有两处 P0-3b「实体名=纯名词」Qwen prompt 约束（「柳如烟持灯退到柜台边的实体是『灯』，不是『灯退到柜』…」），DEP 缺失。修复 = SRC 覆盖同步 DEP，md5 一致（762fb0…）。**影响**：ComfyUI Desktop 后端加载的是 DEP，此前 Qwen prompt 缺这道约束（虽有规则层兜底但双目录必须一致）；已同步，**后端需重启 Comfy Desktop**。
- **Phase 2 硬性回归测试确认在位**：`test_asset_registry.py` **52 PASS / 0 FAIL**——[5] accepted binding 覆盖 matcher（match_kind=persisted）/ [5b] pending 确认后→persisted（不再重新 pending）/ [6] ⛔ Registry 不反向生成实体（剧本没写「蒙面人」但 registry 有 character:蒙面人 binding → matches 不出现）/ [7] 资产被删回退规则匹配不悬空 / [9][9b][10] 路由落盘+校验+注册。
- **Phase 2-1 代码全链路确认就绪**：后端 `asset_registry.py`（entity_key={清洗后类型}:{canonical_name} / ensure_asset_id 同一 image_file 永久同一 asset_id / set_binding accepted+source=user 落盘 minimax_studio/asset_registry.json）+ `asset_matcher.py` persisted 覆盖（accepted→match_kind=persisted）+ `http_routes.py` POST /assets/binding + GET /assets/bindings + 前端 `comfyApi.ts` confirmAssetBinding/fetchAssetBindings + `WorkbenchView.vue` scriptBindingAccepted「已确认」徽标。**asset_registry.json 尚未落盘**（用户还没在 SPA 做过人工确认保存）——二次导入验收即验证此链路。
- **涉及**：`director/script_analyzer.py`（DEP 同步）、`CHANGELOG.md`。
- **下一步 = 用户 SPA 二次导入验收**：重启 Comfy Desktop → 第一次导入《山雨客栈》人工确认绑定落盘 → 检查 asset_registry.json 含 accepted 绑定 → 第二次重新导入应显示 persisted 直接复用而非 pending → Phase 2-1 关闭 → 正式开 Phase 2-2（Registry 跨项目长期记忆层；alias 归一=entity_id 不随 Qwen 变化，用户已列为 Phase 2-2 重点但 Alias 大规模扩展后置）。

### #382 SPA「应用为 SPA 项目」POST /assets/binding 全部 400 根因修复（2026-08-11，用户 SPA 真实验收驱动）

- **触发**：用户重启 SPA（Vite v6.4.3 + 隐身窗重新导入《山雨客栈》）后，SPA 数据与后端脚本一致（漏斗 32→18→21→18→7 自动 · 0 待确认 · 11 未匹配 · 视觉元素 3，角色 3 全 95%✓ / 地点 3 全匹配 / 旧剑匣✓），但点击「应用为 SPA 项目」后浏览器 Network 显示 **7 个 `POST /minimax/director/assets/binding` 全部 400 (Bad Request)**，且 `asset_registry.json` 从未创建。
- **根因（跨实例 asset_id 悬空）**：
  1. `POST /assets/scan` → `minimax_director_assets_scan` 调 `default_registry()`（每次新建实例）→ `match_plan(..., registry)` 里 `ensure_asset_id` 给资产库每条分配 `asset_id`，但 **`match_plan` 从不 `registry.save()`** → asset_id 只存活于该实例内存；
  2. 前端拿 scan 返回的 `matches[i].asset_id`（如 `asset_001`）发 `POST /assets/binding`；
  3. `minimax_director_assets_binding` 又 `default_registry()` **新建实例从磁盘 load**——磁盘上没有 asset_id → `set_binding` 里 `self._assets.get(asset_id)` 返回 None → 返回 **400 `bad_asset_id`（asset_id 未注册）**；
  4. 前端 `persistConfirmedBindings` 的 `api.saveAssetBinding(...)` catch 静默吞掉 → registry 永远不落盘 → 二次导入永远看不到 persisted。
- **修复（最小改动用例驱动）**：`director/asset_matcher.py::match_plan` 在 `ensure_asset_id` 分配循环后加 **`registry.save()`**——分配完立即落盘，binding 路由新实例 load 即可读到同一 asset_id。`save()` 本身幂等（`dirty=False` 无新分配/无快照变更 → 不写盘），不传 registry 行为完全不变（向后兼容）。
- **回归测试**：`test_asset_registry.py` 新增 **[11] ⛔ scan 分配 asset_id 落盘（400 bad_asset_id 回归）**——match_plan 后断言 registry JSON 已落盘 / JSON assets 含分配 asset_id / **新实例磁盘重读 asset_id 一致** / **新实例 set_binding 成功**（跨实例闭环）。**58 PASS / 0 FAIL**。
- **验证**：`test_asset_registry` 58 + `test_asset_matcher` 63 + `test_entity_cleanse` 99 + `test_entity_pollution` 33 + `test_prompt_sanitize` 34 + `test_script_parser` 74 + `test_script_analyzer` 95 全部 PASS + `golden_path --mock` 14/14 + `spa_acceptance --mock` 43/43 + 双目录 md5 一致。
- **⚠ 全量 `pytest director/tests/` 有 26 失败定性为既有测试基建污染**（`test_asset_registry` 模块级注入 fake `folder_paths`/`server`/`aiohttp` 到 `sys.modules`，污染同进程后跑的 test 文件；单独跑 `test_export_route` 8 PASS / `test_segment_cache_stale` 7 PASS；`test_text_backends` 单独跑就 29 errors = 依赖缺失）——非本次改动引入，已知坑延续。
- **涉及**：`director/asset_matcher.py`（+`registry.save()`）、`director/tests/test_asset_registry.py`（+[11] 6 断言）、`CHANGELOG.md`。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**下一步 = 用户二次导入验收**：重启 Comfy Desktop → 重新导入《山雨客栈》→ 点「应用为 SPA 项目」（应不再 400）→ 检查 `D:\Comfy-Desktop\ComfyUI-Shared\input\minimax_studio\asset_registry.json` 含 accepted 绑定 → 第二次重新导入显示蓝色「✓ 已确认」persisted 复用 → Phase 2-1 关闭 → Phase 2-2 Asset Registry。

### #382b `/snapshots` 500 排查结论 + list_snapshots/list_projects 防御性硬化（2026-08-11，用户报告 500 驱动）

- **用户报告**：浏览器 Console `Failed to load resource: the server responded with a status of 500 (Internal Server Error):5173/minimax/director/snapshots?limit=50`。
- **排查结论（500 是旧实例残留，非当前服务器）**：
  1. `comfyui.log`（18:43 重启后）**无 `list_snapshots failed` warning**——而 director 的 logger 确实写 comfyui.log（line 77 路由注册行即证据），说明 18:43 后新服务器从未抛过该异常；
  2. Shared 快照目录 41 个 `.json` **逐文件体检全合法 dict**（VM 脚本验证）；
  3. VM 用真实 Shared 路径注入 folder_paths 复现 `list_snapshots` → 正常返回 41 条；
  4. 故判定：用户看到的 500 发生在 **18:43 重启前旧实例**（浏览器 Console 保留旧请求报错），当前服务器无此问题。
- **顺手硬化（观察期只修 Bug，防复现）**：`director/project_store.py` 两个函数有三个能真 500 的防御漏洞，全部堵死——
  1. `_ensure_root()` 在 try 块外：`os.makedirs` 权限/磁盘满 OSError → 直接冒泡 500 → 挪进 try；
  2. 顶层 JSON 非 dict（`json.load` 只保证是 JSON，不保证是对象）→ `data.get` AttributeError → 500 → 加 `isinstance(data, dict)` 跳过；
  3. `segments`/`scenes`/`episodes` 非 list → `len()` TypeError → 500 → `isinstance(x, list)` 否则计 0。
  `list_snapshots` 与 `list_projects`（同一批 SPA 项目列表加载路径）一并修。
- **回归测试**：`test_project_store.py` 新增 `test_list_snapshots_projects_defensive`——快照目录塞顶层非 dict（list）JSON + segments=42/scenes="x" 的 dict JSON + 项目目录塞顶层非 dict project.json，断言：非 dict 跳过 / dict 字段非 list 计 0 不崩 / 坏项目跳过。**9 项全部通过**（含既有 8 项）+ py_compile 双绿 + **双目录 md5 一致**。
- **涉及**：`director/project_store.py`（list_snapshots/list_projects 防御性硬化）、`director/tests/test_project_store.py`（+1 测试）。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**下一步 = 回 #399 验收**：刷新 SPA（Ctrl+Shift+R）确认项目/快照列表加载无 500 → 二次导入《山雨客栈》验证 persisted 复用。

### #383 Phase 2-2 commit 1：Stable Entity Key / Alias Binding（2026-08-11，用户拍板正式进入 Phase 2-2）

- **背景**：Phase 2-1 关闭（《山雨客栈》7 binding 持久化 → 7/7 persisted 真实基线）。用户拍板 Phase 2-2 第一个 commit 只做「身份稳定性」：**Qwen 每次解析的 entity_id 可以变化，但同一实体的资产绑定不能跟着变化**。目标不是扩大实体识别能力。
- **职责分离固化**：`ProductionPlan` 里 `entity_id` = 本次解析临时身份（`new_entity_id` 进程内计数器分配 ent_xxx，跨解析/跨进程必然变化——Qwen 追加实体时尤其如此）；`entity_key` = 稳定业务身份（`{清洗后最终类型}:{canonical_name}`）→ Asset Registry → `asset_id`（永久绑定 image_file）。**绑定只认 entity_key，绝不用 entity_id 查询/落盘**。前端 `persistConfirmedBindings` 发 `entity_key`；`sourceEntityId`(entity_id) 仅作 SPA 资产追溯元数据。
- **改动（文档固化 + 硬测试锁定，无行为变更——代码本就 entity_key 优先）**：
  1. `director/asset_registry.py`：模块 docstring 加 Phase 2-2 职责分离图 + 「entity_id 不是跨解析主键」铁律说明；
  2. `director/production_plan.py`：`Entity.entity_id` 字段加注释「临时身份，非跨解析主键」；
  3. `director/tests/test_asset_registry.py` 新增 **[12] ⛔ Qwen entity_id 跨解析变化 → 绑定稳定**：第一次解析柳如烟 `entity_id=ent_003` → auto 带 asset_id → 用户确认绑定落盘；第二次解析同一实体 `entity_id=ent_027`（故意不同，模拟下次 Qwen 分配）+ 新实例从磁盘 load → 断言 `entity_id` 确实是新值 / `match_kind=persisted` / `status=auto` / **`asset_id` 与第一次完全一致** / `binding_source=user` / **bindings 数量不变**（不因 entity_id 变化产生重复绑定）。
- **验证**：`test_asset_registry` **65 PASS/0 FAIL**（+7 断言）+ 相关回归全绿：asset_matcher 63 / entity_cleanse 99 / entity_pollution 33 / script_parser 74 / script_analyzer 95 / prompt_sanitize 34 / project_store 9 项 + py_compile 双绿 + **双目录 md5 一致**。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**下一步 = 用户重启后跑真实验收**：跑 `tools/spa_acceptance_shanyu.py`（真实 Ollama）→ 两次导入《山雨客栈》核验 entity_id 变化不影响 7/7 persisted → 关闭 commit 1 → commit 2（Alias 归一：同名不同写法的 entity 归一到同一 entity_key）。

### #384 Phase 2-2 commit 2：Alias 归一（2026-08-11，用户拍板「开 commit 2：Alias 归一」）

- **背景**：commit 1 已锁死 entity_id 变化不影响绑定（测试 [12]）。但 Qwen 每次解析**同一角色可能给出不同写法**——柳如烟/柳姑娘/柳小姐、沈青崖/沈大侠、蒙面人/蒙面客——这些写法会生成不同 `entity_key`（`character:柳姑娘` ≠ `character:柳如烟`），导致**同一实体的绑定被写成多条、无法复用**。commit 2 解决「同名不同写法归一到同一 entity_key」。
- **设计边界（用户拍板）**：⛔ **只对 character 类型做别名归一**——location/prop 刻意不做（山雨楼≠山雨楼外、旧剑≠旧剑匣 是语义不同物，由 P0-2 精确对齐 + semantic_incompatible 锁死，归并反而错）。不做分目录扫描 / 大规模 Alias 库 / 改 Qwen Prompt / 增 EntityType / 强匹配。**锚点 = 剧本上下文驱动**：来自 `script_parser` 提取的 `shot.characters`（已过滤镜头标记/称谓伪实体），不硬编码任何人物表——「不要继续在《山雨客栈》上堆规则」。
- **改动（`director/entity_cleanse.py`，纯规则零显存零 Ollama）**：
  1. `_APPELLATION_SUFFIXES`：称谓后缀词表（老板娘/姑娘/小姐/公子/大侠/女侠/少侠/侠客/先生/前辈/夫人/客官/客/兄/姐/妹/娘/郎…）；
  2. `_character_anchor_map(plan)`：规则层角色名 → `{canonical: display}` 锚点表（去重保序，脚本上下文驱动）；
  3. `resolve_character_alias(name, anchors)`：**⛔ 保守，歧义不猜**——已规范名不动；剥称谓后缀（最长优先）得主干；主干精确命中唯一锚点 → 归一；主干是唯一锚点前缀（柳→柳如烟）/ ≥2 字后缀（如烟→柳如烟）→ 归一；多命中/无命中 → 原样返回；
  4. `build_entity_registry`：`_degrade_unaligned_locations` 之后、规则补位种子之前接入——character 条目归一后 `entry.name=锚点规范名`、原样名进 `aliases` → `entity_key` 跨解析稳定；
  5. `__all__` 导出 `resolve_character_alias`。
- **硬回归测试**：
  - `test_entity_cleanse.py` **[11]**：`resolve_character_alias` 单元（柳姑娘→柳如烟/柳小姐→柳如烟/沈大侠→沈青崖/沈公子→沈青崖/蒙面客→蒙面人/已规范名不动/无锚点原样/非角色原样）+ 歧义不猜（柳如烟+柳无痕 双锚点 → 柳姑娘 原样）+ 空锚点原样 + 注册表级合并（柳姑娘+沈大侠+蒙面客 归一为单条规范条目、aliases 累积）——**115 PASS**（+16 断言）；
  - `test_asset_registry.py` **[13] ⛔ Qwen 称谓变体跨解析 → 绑定稳定**：第一次解析「柳如烟」→ auto + 用户确认落盘；第二次解析「柳姑娘」→ matches 无柳姑娘（已归一）→ `entity_key=character:柳如烟` 命中 → **`match_kind=persisted` + `asset_id` 与第一次完全一致 + binding_source=user + bindings 数量不变**；第三次「沈大侠」→ 归一 沈青崖（无 binding 仍规则 exact，entity_key 稳定）；第四次「蒙面客」无锚点 → 原样保留（歧义不猜）——**77 PASS**（+12 断言）。
- **验证**：后端全量回归全绿（entity_cleanse 115 / asset_registry 77 / asset_matcher / script_parser / script_analyzer / entity_pollution / prompt_sanitize / project_store / text_backends / camera_template / camera_plan / segment_cache / gen_media_tags / core_sampling / export_route）+ `golden_path_script_test.py --mock` **14/14 PASS** + `spa_acceptance_shanyu.py --mock` **43/43 PASS** + **双目录 md5 一致**。
- **⛔ 后端需重启 Comfy Desktop**（Python 代码改动）。**下一步 = 用户重启后真实验收**：跑 `tools/spa_acceptance_shanyu.py`（真实 Ollama 版）后重新导入《山雨客栈》两次核验绑定不分裂（7/7 persisted 复用，不会因 Qwen 写「柳姑娘/沈大侠/蒙面客」类变体生成重复 binding）→ 关闭 commit 2 → 开 commit 3（Persisted 复用 / alias 归一化验收 / 注册表不是实体来源，Phase 2-2 收尾核验）。

### #385 Phase 2-2 commit 3：收尾核验（Persisted 复用 / alias 归一验收 / 注册表不是实体来源，2026-08-11 用户拍板「进 commit 3」）

- **定位**：Phase 2-2 收尾核验，把「跨解析稳定绑定」验收闭环补完整。不新增大规模功能——盘点确认三大主题测试大多已在（[6] 注册表不反向生成实体 / [12] entity_id 跨解析稳定 / [13] alias 变体绑定稳定），缺两块验收缺口：①**完整剧本两次导入的端到端测试**（现有测试是单镜头/单实体层面）；②**验收脚本 spa_acceptance 无 alias 归一 + persisted 复用断言**（用户真实验收跑的就是它）。
- **新增 `test_asset_registry.py` [14] ⛔ 完整剧本两次导入 → 全部 persisted 复用**：完整《山雨客栈》风格 plan（2 镜头 4 实体：柳如烟/沈青崖/旧剑匣/山雨楼外）→ 第一次导入全量 auto + 用户确认 4 绑定落盘 → 第二次导入 Qwen 给称谓变体（柳姑娘/沈大侠）+ 全部 entity_id 重分配 → 断言：matches 无变体名（已归一到锚点）+ **4/4 `match_kind=persisted` + binding_source=user + asset_id 与第一次完全一致 + bindings 数量不变 + 注册表无 `character:柳姑娘`/`character:沈大侠`**——**77 → 84 PASS**（+7 断言）。
- **`tools/spa_acceptance_shanyu.py` 新增 ⑪ Alias 归一 + Persisted 复用**：用临时 registry（不碰真实 `asset_registry.json`）→ 第一次导入真实 qwen_plan → 模拟用户在 SPA 人工确认全部 auto 绑定落盘 → 第二次 deepcopy + 注入称谓变体 + 全 entity_id 重分配 → 断言：matches 无变体名 + **7/7 persisted 复用（不重新 pending）+ bindings 数量不变**。浏览器核对清单加第 9 条（第二次导入蓝色「✓ 已确认」persisted 复用）。**43 → 47 PASS**（+4 断言）。
- **验证**：后端全量回归全绿（entity_cleanse 115 / asset_registry 84 / asset_matcher 63 / script_analyzer 95 / script_parser 74 / entity_pollution 33 / prompt_sanitize 34 / camera_template 67 / camera_plan 50 / 其余全部 OK）+ `golden_path_script_test.py --mock` **14/14 PASS** + `spa_acceptance_shanyu.py --mock` **47/47 PASS** + **双目录 md5 一致**。
- **Phase 2-2 三件套闭环**：commit 1（#383 Stable Entity Key：entity_id 临时身份不影响绑定，[12]）+ commit 2（#384 Alias 归一：角色称谓变体归一到规则层锚点，只 character、歧义不猜，[11][13]）+ commit 3（#385 收尾核验：[6] 注册表不是实体来源 + [14] 完整剧本两次导入端到端 + ⑪ 验收脚本断言）。**跨解析稳定绑定全部锁定**——实体名写法变化、entity_id 变化都不再分裂绑定，已人工确认的绑定跨解析永久复用。
- **✅ 最终真实验收 PASS（2026-08-11 用户实测）**：`tools/spa_acceptance_shanyu.py`（真实 Ollama）→ **48 PASS / 0 FAIL**——⑪ 段三条核心断言全过：第一次导入确认落盘 **7 个 binding**（≥1）→ 第二次导入 matches **无称谓变体**（柳姑娘/沈大侠 已归一）→ **7/7 条 persisted 复用（Qwen 变体不重新 pending）** → **bindings 数量不变（7 → 7，变体不产生重复绑定）**。SPA 重新导入《山雨客栈》→ 资产匹配区 **7 条「✓ 已确认」persisted 复用、0 待确认**、无变体名重复绑定；`asset_registry.json` 仍 7 条。**Phase 2-2 正式关闭，V1.7 全阶段交付收官。**
- **⛔ 后端需重启 Comfy Desktop**（commit 1+2 的 Python 代码改动）。**下一步 = 最终 Golden Path 剧本验收**（导入《山雨客栈》→ 审核 → 逐镜生成 → 导出 → 重开项目复验，§17 最后一项未打勾）。

### #386 Golden Path 全流程验收 PASS（剧本 → AI 制作计划 → 生产成片全链路，2026-08-11 用户实测）

- **定位**：V1.7 最终端到端验收——把「剧本 → AI 视频制作计划」管线从头跑到尾，验证生产闭环。用户按四步走：导入 → 审核 → 生成 → 导出 + 重开复验，全部通过后拍板「收尾吧」。
- **第一步 导入 → 资产 → AI Draft PASS**：《山雨客栈》导入 → **3 场景 / 9 镜头 / 3 角色** → 资产匹配 **7/7 persisted 复用、0 待确认、Qwen 称谓变体 0、Scene 顺序正确、剧本逐字保留** → **AI Draft 9/9**（五区齐全、内部标记泄漏 0）。
- **第二步 Workbench 逐镜审核 PASS**：9 镜全量 adopted=True，逐镜审核采纳。
- **第三步 H3 逐镜生成 PASS**：9 镜 mp4 全落盘（r2v 续接 00096~00104，448×256，5.1-5.9s），无空文件、可解码。
- **第四步 导出 + 重开复验 PASS**：导出成片 `MiniMax_Studio_Movie_00002_.mp4`（5.6MB / 48.9s / 448×256）；重开项目复验 persisted 绑定保留。
- **✅ V1.7 全阶段交付收官**：Phase 0（Ollama + 显存互斥）→ Phase 1（规则拆镜 + Qwen 语义补全 + 导入 UI）→ Phase 2/2-1/2-2（资产自动匹配 + Stable Entity Key + Alias 归一 + 持久绑定）→ Phase 3（Prompt Builder 五区草稿）→ Phase 4（Workbench 审核）→ Phase 5（自动运镜）→ **Golden Path 端到端**。「剧本语言 → 生产语言 → 人工审核 → 逐镜生成 → 成片导出」全闭环，V1.7 正式收官。

### #387 V1.8-1 Shot 视频 + Prompt 联动详情区（2026-08-11，用户方案：预览时把 shot 对应视频 + 提示词放到镜头视频下面）

- **定位**：V1.8「生成与预览体验」第一优先级。用户原话：预览视频时把 SPA 下方界面的 shot 对应视频以及提示词放到镜头视频下面。纯前端改造（WorkbenchView.vue + 渲染测试）。
- **① 主播放器下方新增 Shot 详情区（当前镜头档案）**：随当前镜头联动显示——`🎬 镜头名 + 场景 + 时长 + 任务类型 + 续接方式 + 状态灯`；左：单镜视频（`segment_mp4` → blob，生成后自动加载、可独立播放/下载）+ `⬇ 下载 mp4`；右：`📜 剧本原文`（只读折叠）+ Prompt 五区（`🎬 画面描述 / 🎥 摄影 / 🎨 风格 / 🔊 声音 / ❌ 负面`）+ 生成参数速览 chips（状态变化/智能尾帧/分辨率/帧率）+ 动作区（`▶ 播放 / ✎ 编辑 Prompt / 🔄 重新生成`）。可折叠。
- **② 底部 Shot 列升级为 Storyboard 卡片**：`画面缩略图（video 首帧 → canvas → dataURL，懒加载 + 内存缓存 + 失败降级占位）+ S1/S2… 编号 + 镜头名 + 时长 + 状态灯`；点击卡片 → `selectShotAndPlay`：选中镜头 + 有缓存则主播放器直接播该镜（无缓存只切详情区，不触发播放）。
- **③ 联动**：`watch(shot.id)` + `watch(wb.segStatus)` → 刷新详情区单镜视频 + 预热缩略图；切镜丢弃迟到结果（`detailShotId` 竞态守卫）；组件卸载 revoke blob。
- **工程细节**：`ensureThumb` 加 canvas 2d 能力守卫（happy-dom/jsdom 测试环境跳过，避免 video 解码挂起）；`URL.revokeObjectURL` 延迟 3s（避免解码中引用失效）。
- **验证**：vue-tsc + vite build 通过；workbenchRender 新增 4 用例（五区详情渲染 / Storyboard 卡片编号+联动 / 未生成不拉视频 / 生成成功自动加载 segmentMp4）；全量 355 测试通过；双目录同步 md5 一致。

### #388 V1.8-2 手动工作流选择（Workflow Registry）（2026-08-11，用户需求：增加手动换工作流的按键）

- **定位**：V1.8「生成与预览体验」第二优先级。默认 + 已保存 + 手动导入 workflow_api.json；自动扫描 MiniMaxH3Director 节点；无/多节点提示（「❌ 此工作流不是 MiniMax H3 Director 工作流」/「⚠️ 检测到 N 个，请选择生成节点」）；提交生成时使用选中的工作流 + 识别出的节点 id 注入 timeline_data。
- **① 新增 `frontend/src/core/workflowRegistry.ts`（纯函数模块）**：`scanDirectorNodeIds` 扫描 class_type 含 MiniMaxH3Director 的节点（过滤顶层元数据 key）；`parseWorkflowJson` 解析 workflow_api.json（坏 JSON / 无 class_type 节点返回可读错误）；`buildWorkflowEntry` / `buildBuiltinEntry` 构建注册表条目（单节点自动选中、多节点强制用户选择）；`resolveActiveWorkflow` 判定四态 `invalid/none/ok/multi`；持久化 key `minimax_studio.workflowRegistry.v1` + `loadWorkflowRegistry` 防御性还原。
- **② 生成栏头部新增工作流控制区 `.wf-ctl`**：下拉选择（默认内置 + 已导入条目）、节点状态徽标（`✓ 节点 N` / `⚠️ N 个节点` / `❌ 非 H3 工作流`）、🗑 删除（仅手动导入，回退内置）、`＋ 导入`按钮。
- **③ 两个 Teleport 弹窗**：多节点选择弹窗（「检测到 N 个 MiniMaxH3Director 节点」→ 列表选择生成节点，未选前生成被阻止）；导入弹窗（文件选择 + 文本粘贴 → 解析 → 扫描 → 入注册表并激活，多节点自动弹选择）。
- **④ 生成链路改造**：`handleGenerate` 开头 workflow validation gates（`none` → setError「不是 MiniMax H3 Director 工作流」；`multi`/未选 → setError「请选择生成节点」+ 自动弹窗）；两处 workflow 构建点（单次/成片 + 逐镜走查）改用 `activeWorkflow.value.workflow` + `directorNodeId.value`；`DirectorRunService.run(structure, workflow, nodeId, callbacks)` 把 timeline_data 注入 `sanitized[nodeId].inputs.timeline_data` + `timeline`；全部硬编码 `DIRECTOR_NODE_ID` → computed `directorNodeId`（无效回退内置 `"5"`，保证缓存读取/导出不传 null）。
- **⑤ 持久化**：localStorage 存手动注册表（内置不入库）+ `.active` 激活 id；onMount 恢复。**修复**：`confirmWorkflowImport` 原本只写注册表不写 `.active` → 导入后刷新丢失激活项（测试暴露），抽出 `persistActiveWorkflowId()` 统一在导入/下拉切换/删除三处落盘。
- **⑥ 切换工作流后 `refreshSegStatus()` 按新节点 id 重读段缓存**，状态灯/详情区跟着正确节点走。
- **验证**：workflowRegistry 纯函数单测 18 个 + workbenchRender 新增 7 用例（默认渲染 / 0 节点生成被阻止 / 多节点自动弹窗 / 有效导入生成用节点 9 / 弹窗选择后生成用所选节点 / localStorage 持久化重挂恢复 / 删除回退内置清空注册表）全绿；全量 380 测试通过（+1 跳过）；vue-tsc + vite build 通过；双目录同步 md5 一致。
- **设计约束**：timeline_data / workflow JSON 仍为内部执行格式；V1.8-2 按需求把 workflow 以「导入文件 + 语义化选择」暴露，不引入直接 JSON 编辑入口。

### #389 V1.8-3 生成预计时间可视化（ETA）（2026-08-11，用户拍板「开始」V1.8-3）

- **定位**：V1.8「生成与预览体验」第三优先级。任务中心实时显示：当前镜头进度条 + 本镜剩余 + 全片剩余 + 已用时间；基于历史成功生成数据（分辨率/fps/帧数/任务类型）做特征加权预测，无历史回退经验估算。
- **① 新增 `frontend/src/core/generationEta.ts`（纯函数模块）**：`estimateShotMs`（taskType 不匹配直接淘汰；分辨率精确=1、不匹配=0.4 且每帧耗时按像素比缩放 clamp 0.4–2.5；fps 精确=1、±2=0.7、否则=0.3 → 加权平均每帧耗时 × 目标帧数）；`currentShotFraction`（prepare/context_encode/sample/decode 四阶段分数——采样阶段只报端点 0/1，故当前镜进度为离散 4 档）；`computeRunEta`（走查用 currentIndex 定位、单次用 progress.segment 定位；本镜剩余 = 预测×(1–进度) + 后续镜预测累加）；`buildGenMeta`（每镜 frames = 17k+5 网格、taskType 归一、逐镜预测写入）；`formatEtaMs`（mm:ss / h:mm:ss）；持久化 key `minimax_studio.generationEta.v1`（cap 200，仅记成功采样）。
- **② 生成链路接入（WorkbenchView.vue + workbench.ts）**：任务创建后立即 `buildTaskGenMeta` 预计算整片逐镜预测并 `patchTask` 挂到 `TaskRecord.genMeta`；成功路径记录采样——走查模式逐镜精确计时（每镜独立提交），单次/多镜头模式按帧数比例分摊总耗时；`deriveFrameCount` 在 minimaxH3Adapter 导出供 ETA 复用。
- **③ TaskCenter ETA 展示（底部任务栏，低频状态信息落点）**：摘要行加 `⏱ 已用 · 剩`；面板新增「⏱ 生成预计」卡片——预计总时长 + 已用/本镜剩余/全片剩余三行 + 当前镜头进度条（🎬 镜头名 + 百分比，fill 宽度）+ 阶段标签 + 置信度（`可信 X% · N 条样本`，无样本时 `首次生成 · 经验估算`）；1 秒 ticker 实时刷新。
- **验证**：generationEta 纯函数单测 20 个 + 全量回归 400 passed（+1 既有跳过）；vue-tsc + vite build 通过；双目录同步 md5 一致。

### #390 V1.9 Workflow Studio：工作流可视化编辑器 + 快捷参数面板（2026-08-11，用户方案「V1.8 这样设计」①-⑥）

- **定位**：把 sample-workflow.json 从「黑盒模板」变成「可视化可编辑对象」。用户需求①工作流管理 UI、②点击节点→右栏改参数、③不硬编码全部参数（timeline_data 项目数据 vs workflow 采样参数分离）、④节点三态 🟢🔵🔴、⑤右上角「工作流参数」快捷面板、⑥第一版范围 = 导入→可视化→改 widget 参数→保存→用该工作流生成（不做自由连线）。纯前端，后端零改动。
- **① 新增 `frontend/src/core/workflowStudio.ts`（纯函数模块）**：`nodeRole`（class_type 关键字 → director/system/normal 三态，SYSTEM 含 SaveVideo/CreateVideo/PreviewImage/VHS_VideoCombine/ImageScale）；`scanWorkflowGraph`（节点+连接边，数组引用 [srcId, slot] 提取）；`widgetsOf`（跳过连接引用/BDGROUP/managed 隐藏字段，Director 字段分 free/project/managed 三组，`DIRECTOR_MANAGED_FIELDS`=timeline_data/timeline，`DIRECTOR_PROJECT_FIELDS`=width/height/ref_max_size/total_frames/frame_rate/global_prompt 只读）；`inferWidgetKind`（int/float/str/bool/combo，FLOAT_FIELDS/INT_FIELDS 白名单修正 cfg:1.0 判 float、steps 判 int）；`setNodeWidget`（深拷贝写回，不污染原对象）；`findLoraNode/insertLoraNode/removeLoraNode`（受控连线：插入 LoraLoaderModelOnly 到 Director.model 前、移除后恢复上游，错误 no-director/model-not-connected/already-lora/no-lora）；`resolveSeed`（-1=随机，正数整数化）。
- **② 新增 `frontend/src/core/workflowLayout.ts`（纯函数 DAG 布局）**：最长路径分层（无入边→层 0，其余 max(上游层)+1），同层按对象 key 序排布，产出每节点 x/y + 画布尺寸（NODE_W=216/NODE_H=76/GAP 常量与组件样式一致），无第三方依赖。
- **③ 新增 `frontend/src/workbench/WorkflowStudio.vue`（完整节点图编辑器）**：左侧 SVG 连线节点图（DAG 曲线 + 节点卡按角色着色 🔵Director/🔴系统/🟢普通，点击选中）+ 右侧检查器（LoRA 区块编辑/一键插入/移除 + 节点参数表单，free 可编辑、project 只读带「⚙ 由项目控制」提示、system 锁定带 🔒 横幅）+ 保存/另存为（内置工作流保存自动转另存为，禁止覆盖内置）+ 未保存确认关闭。
- **④ 新增 `frontend/src/workbench/WorkflowParamsPanel.vue`（快捷参数面板）**：右上角 ⚙ 弹层，只露高频采样参数（steps/cfg/seed/sampler/scheduler/shift_video/shift_audio，按 widgetsOf+白名单存在才显示）+ LoRA 强度快速改 +「高级节点编辑器 →」跳 WorkflowStudio；与编辑器共用同一工作流草稿语义（修改不落盘，保存/另存为才写回 Registry）。
- **⑤ WorkbenchView 接入 + seed 随机**：工作流区新增「⚙ 参数」「◈ 编辑」两个入口；保存/另存为写回 Workflow Registry（`onStudioSave` 改激活条目并 persist，`onStudioSaveAs` 走 `buildWorkflowEntry` 新增条目并激活）；生成链路两条路径（单次生成 + 走查逐镜）在 `syncWorkflowFrameRate` 后调 `randomizeSeedIfNeeded`——工作流 seed=-1 时每次生成替换随机种子（正数保持固定）。
- **验证**：workflowStudio 单测 20 + workflowLayout 单测 5 + 全量回归 425 passed；vue-tsc --noEmit + vite build 通过（修复 `MapIterator.map` 在 vue-tsc -b 下类型报错，改 `Array.from(byLayer.values(), ...)`）；双目录同步 md5 一致。

### #391 V1.10 生成体验：② 生成中刷新/关闭恢复（2026-08-12，用户拍板「①EPT ②刷新恢复 + UX polish 一起做」）

- **定位**：浏览器刷新/关闭重开后，正在 ComfyUI 里跑的生成任务不丢——重新进入工作台自动探测 `/queue` 恢复，显示「🎬 Shot 07 正在 ComfyUI 生成」而非「○ 未生成」。前后端任务状态解耦（前端 store 刷新即清，但要能从后端恢复）。
- **新增 `frontend/src/core/inFlightSnapshot.ts`（纯函数）**：`IN_FLIGHT_KEY="minimax_studio.inFlight.v1"`；`InFlightSnapshotBase`（projectId/currentPromptId/currentIndex/currentShotId/shotCount/shotOrder/targetShotIds/shotStates/startedAt/genMeta/finalVideos）+ `loadInFlight/saveInFlight/clearInFlight`。任务开始 `run()` 时写快照，`settleRun()` 结束清空。
- **`services/directorRun.ts` 加 `resume(promptId, callbacks)`**：按 promptId 重建进度回调 + 起轮询（/history 已有该任务则直接收敛，否则 /queue 等它跑完），复用 `settleRun()` 单出口——不重复加任务、不重复建占位。
- **WorkbenchView `restoreInFlight(projectId)`**：`openProject` 时若有本项目的飞行快照 → 重建 TaskRecord（`genMeta: snap.genMeta ?? undefined`、progress 初始「正在向 ComfyUI 恢复生成状态…」）+ `runService.resume` + `buildRestoreCallbacks`（onProgress 用结构类型 `{phaseLabel?;framesLabel?;phaseValue?;phaseMax?}`，`phaseLabel ?? ""`/`framesLabel ?? ""` 满足 setProgress 必选类型）。快照无 currentPromptId 或项目不匹配 → `clearInFlight()` 直接退出，不建占位任务。
- **坑**：`restoredTaskOf` 早期方案错（snap.taskId ≠ 前端 addTask 生成的 id）→ 废弃，恢复任务一律新建 id。

### #392 V1.10 生成体验：① 顶部常驻状态横幅 + 区间 ETA + 可信度三级（2026-08-12）

- **定位**：生成时顶栏常驻横幅「🎬 生成中 Shot 07/09 + 进度条 + 约剩 2–4 分钟（区间非精确 mm:ss）」，点击展开详情（当前阶段/已用/预计剩余区间/预计完成区间/本镜预计/本项目预计/查看任务详情/⏹ 停止）。
- **WorkbenchView 新增 `runBanner` 计算属性 + banner* computeds**：取 `wb.currentTaskId` 的 running 任务；`computeRunEta` 得 `interval=etaIntervalMs(remainingMs, confidence)` → `formatEtaInterval(lo,hi)`（「约剩 2–4 分钟」「约 1 分钟内」）；`confidenceLevel`/`CONF_LEVEL_DOT`/`CONF_LEVEL_LABEL` 三级圆点（●稳定 ◐收敛 ○数据不足）；`bannerFinishText` 预计完成区间 HH:MM–HH:MM。`bannerTimer` 每秒 `nowTick` 刷新。
- **模板**：`</header>` 后加 `.gen-banner` 块（主行 badge/Shot N/镜头名/进度条/阶段/约剩/已用/caret；`.gen-banner-detail` 折叠网格 + 置信说明 foot + 「查看任务详情」(@taskCenterSignal++) + 「⏹ 停止」(@onTaskCancel)）。CSS 全部新增。
- **TaskCenter 加 `openSignal` prop**：`watch(() => props.openSignal ?? 0)` 增加即 `panelOpen=true`（横幅「查看任务详情」联动打开面板）。
- **坑**：横幅阶段文案用 `phaseStepLabel`（真实 step，勿伪造百分比）；展开网格里本镜/全片预计用 `formatEtaMs`（`fmtClock` 别名未定义，build 报 TS2339 → 统一用 `formatEtaMs`）；`bannerFinishText` 里 `const now` 未使用报 TS6133 → 删。

### #393 V1.10 生成体验：⑤ ETA 可信度三级视觉化（2026-08-12，与 #392 同批）

- **定位**：ETA 可信度不只是一行小字，而是三级圆点 + 悬停说明。● 预测较稳定 / ◐ 预测正在收敛 / ○ 数据不足。
- **`generationEta.ts` 新增**：`confidenceLevel(c)`（≥0.5→2，0.2~0.5→1，否则 0）、`CONF_LEVEL_DOT`（●◐○）、`CONF_LEVEL_LABEL`（三级说明）、`etaIntervalMs`（稳定±20%/收敛±40%/数据不足±60%）、`formatEtaInterval`。
- **接入**：WorkbenchView 顶栏横幅（`confDot`+`confLabel` title）+ TaskCenter 当前生成大卡（`tc-eta-conf-dot` 圆点 + foot 文案）。

### #394 V1.10 生成体验：⑥ 当前生成大卡 vs 队列彻底分离 + 历史耗时（2026-08-12）

- **定位**：① TaskCenter「当前生成」大卡强化（独立边框 + 大镜名 + 真实阶段 + 预测依据），与下方「走查队列/任务清单」彻底分离；② 队列每镜带 耗时/预计剩余；③ 预测依据文字「基于最近 N 个已完成镜头」；④ Shot 卡「⏱ 上次生成 7m 18s」+ 项目层「平均/最快/最慢/已生成 6/9 镜」。
- **`generationEta.ts` 新增**：`lastMsForShot(history, {taskType,frames,width,height,projectId})`（taskType 精确 + 分辨率精确 + 帧数±2，取最近 finishedAt）、`ProjectTimeStats`/`projectTimeStats(history, projectId)`、`phaseStepLabel`。
- **WorkbenchView**：`lastMsOfShot`/`lastGenTextOf`（Shot 卡 `.shot-lastgen`「⏱ 7m 18s」）、`projectStats`(+`.proj-stats` 平均/最快/最慢/已生成 N 镜)、`etaHistoryForTaskCenter` computed（每秒随 `nowTick` 刷新传入 TaskCenter）。
- **TaskCenter**：`history` prop（`GenHistoryRecord[]`）；eta-card 改「🎬 当前生成」+ `phaseFor(activeTask)`（真实 step）+ foot「● 可信 X% · 基于最近 N 个已完成镜头」（`eta.matchedTotal`）；队列 `shotTimeText(qst,sid)`（done 显示最近实测 `⏱`，pending/running 显示 `预计 mm:ss`，`timeTitle` 悬停说明）+ 队列标题右端「预计剩余 mm:ss」（`queueRemainingText`）；`.tq-time`/`.tc-queue-eta`/`.tc-eta-conf-dot` CSS。
- **坑**：`lastMsForShot` 用 `meta.width/height`（genMeta 全局尺寸），别用 shot 局部；done 镜取实测、pending 取 `genMeta.shots[].predictedMs`。

### #395 V1.10 生成体验：④ 真实阶段 step 显示（2026-08-12，后端 reporting 2026-08-11 已落）

- **定位**：把「百分比」换成真实阶段 step「Sampling · Step 18/30」；后端拿不到真实 step 就不伪造。
- **后端（已落前篇）**：`director/core_sampling.py` `_step_callback` 采样前 + 每步 `notify("sample", step+1, max(total_steps,1))`（step/total 真实步进）；`executor_core.py` `_report_sample_phase(phase, value, max_value)` 发布 `phase="sample", phase_value=value, phase_max=max_value`；`progress.py` 透出 `phase_max`。
- **前端**：`minimaxH3Adapter` 解析 `phaseValue/phaseMax` → `TaskRecord.progress`；`phaseStepLabel(p)` 当 `phaseMax>1` 显示「phaseLabel · Step v/max」，否则只显示 phaseLabel；顶栏横幅 `bannerPhaseLabel` + TaskCenter `phaseFor` 均据此渲染。
- **坑**：本次「双目录同步」补同步了 `director/core_sampling.py` + `executor_core.py`（前篇只改了源目录未同步部署）——改 Director 后端后**必须重启 Comfy Desktop** 让 ComfyUI 重载节点（Python 字节码缓存 `__pycache__` 已清）。

### #396 V1.11 Shot 生成历史（版本管理）（2026-08-12，用户实测 bug 驱动）

- **定位**：用户实测「重复生成 shot_02 后，点镜头页显示的是**第一遍**视频，任务中心打开成片却是**刚生成**的」——根因是 SPA 把「Shot 当前成片」与「最近一次生成」混为一个 `finalVideo`，无版本历史，无法并排对比选优。
- **方案（用户拍板 P1 核心版）**：把「Shot → 生成结果」升级为**版本管理**。一个 Shot 可有 N 个 Generation，每个 Generation 独立保存「视频 + 五区 Prompt 快照 + 参数快照」；`activeGenerationId` 决定当前成片；**最新生成 ≠ 当前成片**（仅首版自动采纳，用户点 ⭐ 设为当前成片 切换）。TaskCenter 只管任务状态，生成完成时往 `Shot.generations[]` 写一条 Generation。
- **数据模型**：`models/project.ts` 新增 `ShotGenRecord`（id/createdAt + 归档 videoFile + outputFilename + prompt{visual/negativePrompt/cameraText/style/soundText} + params{taskType/continuityMode/width/height/frameRate/workflowId/workflowName} + durationMs）与 `Shot.generations?` + `Shot.activeGenerationId?`。命名刻意避开既有 `ShotGeneration`（那是生成参数，勿混淆）。`serialize/deserializeProject` 纯 JSON 往返 → 新字段自动持久化 project.json。
- **后端归档路由**：`director/http_routes.py` 新增 `POST /minimax/director/generations/archive`——把 `{output_dir}/{filename}` 用 `_safe_basename` 清洗各路径段复制到 `{input_dir}/minimax_studio/generations/{project_id}/{shot_id}/{generation_id}.mp4`；input 目录经 `/view?type=input` 服务（comfyInputUrl），重启后仍存活。**改后端必须重启 Comfy Desktop**。
- **前端 UI**（`WorkbenchView.vue`）：中心播放区下方新增**版本条**（Layer 2，与 Shot 导航 Layer 1 严格分离）——每版缩略图 `#N` + 生成时间 + 三态徽标（🆕 最新生成 / ⭐ 当前采用 / ○ 历史）；点版块切到该版并让右侧 Prompt 面板跟随显示该版只读快照 + 参数明细 + 「复制到当前草稿」；左侧 Shot 卡 + 时间线卡显示 `[N版]`（+⭐ 则已采纳）。放大为浏览器全屏简易版（Cinema/A-B 后续）。
- **Store**（`workbench.ts`）：`addGeneration`（仅首版自动采纳，touch()）/ `setActiveGeneration`（校验存在）/ `removeGeneration`（重落 active 到最后一版）。
- **三条完成路径挂钩**：单镜直跑（scopeIds.length===1 才建记录，多镜合并输出不映射单镜）/ 逐镜走查队列 / 刷新恢复（`createGeneration` 内部调归档路由 + 写入 + 切播放器到最新版）。
- **测试**：`workbenchEdit.test.ts` 新增 10 条（版本新增/首版自动采纳/切换当前成片/删除重落/快照复制等），全量 435 测试 + build 全绿。
- **坑**：① `activeWorkflow` 取工作流名用 `.entry?.name`（`ActiveWorkflow` 无直接 `.name`）；② 归档失败回退 `outputFilename` 播放（`genVideoUrl`：优先 archived `comfyInputUrl`，兜底 `/view?type=output`）；③ 多镜合并输出不建 Generation 记录（无法映射单镜）。

### #397 V1.11.1 UI 收敛：砍掉 2 层重复播放器（2026-08-12，用户评审「中心区 5 层冗余」后拍板）

- **定位**：用户评审 V1.11 截图后指出中心区出现 **5 层重复信息**（① 中央当前 Generation 视频 → ② 下方 `Generation #1 · 当前成片` → ③ 生成历史又放缩略视频 → ④ Shot 详情区又放视频 → ⑤ 底部 Storyboard 缩略图）。拍板 UI 原则：**「一个地方负责『看视频』；一个地方负责『选版本』；一个地方负责『看镜头信息』。不要一个 Shot 出现三个播放器」「视频只播放一次，版本只展示缩略图，信息只展示一次」**。这是「该做 UI 收敛而不是继续堆模块」的里程碑。
- **改动**（`WorkbenchView.vue`，纯前端，后端零改动）：
  - **① 中央唯一大播放器**（`.player-wrap`）：顶部新增 `.player-head`（🎬 shot 名 / 场景 / 时长 + 右上角唯一 `[⛶]`）。主播放器仍是唯一 `<video>`，`playerMeta` 优先显示「当前查看的版本」→ 播放队列 → ⭐ 当前成片。点任何历史版本只替换这里的视频。
  - **② 纯版本条**（`.gen-strip`，原「生成历史」改造）：缩略图改为**图片首帧**（`genThumbMap`，canvas 160×90 jpeg，无 canvas 降级 🎬 占位）**不是 `<video>`**；卡只含 `#N` / 实际时长（`genDurMap` 从 video 元数据读，回退镜头设定时长）/ 时间 / ⭐当前|🆕最新 徽标；删除了原 `.gen-snapshot` 整块（Prompt/参数不再塞进版本卡）。底部 `[⛶ 放大预览] [⭐ 设为当前成片]`。
  - **③ Shot 信息卡纯文字**（`.shot-detail`）：删除内嵌 `<video>`（V1.8-1 详情播放器移除）；只保留 📜 剧本原文 / ✨ Prompt 五区 / 👤 资产 / ⚙ 生成参数 chips；查看历史版本时显示该版只读快照（`当前显示 Generation #N 的历史快照` + `📋 复制该版到当前草稿`）。操作收敛为 `[✎ 编辑] [🔄 重新生成] [⬇]`。
  - **④ Storyboard 时间线不动**（跨镜导航）。
  - **Cinema Mode**（Teleport 全屏）：唯一 `[⛶]` 放大入口 → 全屏大视频 + 底部版本圆点条 `#1 #2 #3`（● 当前查看 / ⭐ 当前成片）+ 版本标签 + `[⭐ 设为当前成片]` + `[✕ 关闭]`（Esc 也关）。V1 不做 A/B 对比。
  - **数据流**：`viewGeneration(g)` 只切 `viewedGenId` + `setFinalVideo`（主播放器）；`setActiveCurrent()` 把当前查看的版本采纳为 ⭐；`warmGenThumbs()` 幂等预热版本缩略图；`infoPrompt/infoGenParams/infoAssetNames` 让信息卡跟随「查看的版本 or 当前草稿」。
- **测试**：`workbenchRender.test.ts` 新增 14 条 V1.11.1 用例（版本条纯缩略图无 `<video>` / 点击版本卡切主播放器 / Cinema 圆点切换+设当前成片+关闭 / 信息卡显示历史快照+复制按钮 / `.sd-media` 不存在），原「segStatus 刷新后详情区自动加载单镜视频」改为断言**不再**调用 segmentMp4。全量 **439 测试 + build 全绿**。
- **坑**：① 版本卡/信息卡都显示 `Generation #N`，但语义不同（版本条=选版本，信息卡=该版为什么长这样），头部 chip 用 `sd-gen-chip` + `sd-gen-active` 区分；② 版本缩略图依赖 `canvas.getContext("2d")`，测试环境（happy-dom）静默跳过不报错；③ 放大入口全局只有主播放器 `[⛶]` 与版本条 `[⛶ 放大预览]` 两处（同一 `openCinema`），Cinema 内不再提供第二个播放器。

### #398 V1.11.2 修复：生成版本灰色看不了（subfolder 丢失，用户实测 bug）

- **现象**：用户报告版本条里生成版本缩略图是灰色 🎬 占位、点开也没法看。主播放器能播同一份视频，只有版本条/版本详情灰 → 定位为「归档链路 subfolder 两处丢失」。
- **根因**（H3 视频实际落在 ComfyUI **`output/video/`** 子目录，不是 output 根目录）：
  1. 后端 `minimax_generation_archive`：`src = os.path.join(output_dir, filename)` 没拼 `subfolder` → 源文件找不到 → 404 → 前端 `catch` 静默 `videoFile=""` → 生成历史没有持久视频源。
  2. 前端 `genVideoUrl` 回退：`/view?type=output&filename=xxx.mp4` 没带 `subfolder=video` → 404 → 缩略图 `ensureGenThumb` 静默失败 → 灰占位 + 播放失败。
  3. 证据：ComfyUI-Shared `input/minimax_studio/` 无 `generations/` 目录；所有 project.json 无 `"generations"` 键（视频只存在于 SPA 内存 / output 临时区）。
- **改动**（5 个文件，subfolder 贯穿全链路）：
  - `director/http_routes.py`：archive 路由接受可选 `subfolder`，`src = join(output_dir, subfolder, filename)`；缺参 400 守卫改为**先校验原始字段再 `_safe_basename`**（原实现 `_safe_basename("")` 因 `name or "video.mp4"` 回退永非空 → 守卫是死代码，缺参会被静默归档到伪目录，顺手修正）。
  - `frontend/src/models/project.ts`：`ShotGenRecord` 增 `outputSubfolder?`。
  - `frontend/src/services/comfyApi.ts`：`archiveGeneration` 接受并透传 `subfolder`。
  - `frontend/src/workbench/WorkbenchView.vue`：`createGeneration` 收 `subfolder` 落 `outputSubfolder`；3 处 `fv` 构造 + onVideo 类型透传 `ref.subfolder`；`genVideoUrl` 回退 URL 带 `subfolder=video`。
  - `frontend/src/core/inFlightSnapshot.ts`：`InFlightShotVideo` 增 `subfolder?`（刷新恢复链路同修）。
- **测试**：新增 `director/tests/test_generation_archive_route.py`（5 用例：带 subfolder 归档成功/副本真实落盘、无 subfolder 根目录归档、源缺失 404、缺参 400、坏 JSON 400）——后端全量回归 20 个测试文件全部 PASS；前端 **439 测试 + build 全绿**（压缩前已跑）。
- **坑**：① archive 成功后 `video_file` 是 input 相对路径（`minimax_studio/generations/...`），前端 `comfyInputUrl` 直接可播、重启不丢；② 改 `http_routes.py` 后**必须重启 Comfy Desktop** 才生效（路由启动时注册）；③ 已归档的旧版本历史（`videoFile=""`）在本次修复前不会自动补数据，重新生成该镜后版本条即可见。④ `ensureGenThumb` 对跨域 canvas 静默降级是既定兜底，URL 修对后不会再灰。

### #399 V1.11.3 修复：新建项目底部残留上一项目 shot（用户实测 bug）

- **现象**：新开一个项目后，底部任务中心仍显示上一个项目的镜头任务/成片，看起来像「换了个项目但任务还在」。
- **根因**：`loadProject` 只替换 `state.project`，但没有清 `tasks / currentTaskId / finalVideo / preview / lastError / currentSceneId / currentShotId / selectedShotIds / segStatus / genScope` 等项目级瞬态 → 底部任务中心（渲染 `wb.tasks`）残留上一项目的任务；切项目后主区游标也停留在旧位置。
- **改动**（3 个前端文件，纯前端）：
  - `frontend/src/stores/workbench.ts`：`loadProject` 重写为「载入项目 = 全新会话」——**无条件复位 14 项项目级瞬态**（`tasks` 清空、`currentTaskId` 置 null、`finalVideo/preview` 置 null、`lastError` 置 null、`genScope="all"`、`selectedShotIds` 清空、`segStatus` 置 null、`currentEpisodeId/currentSceneId/currentShotId` 复位到新项目第一集第一场景第一镜、`currentPane` 随游标、`dirty=false`、`lastSavedAt=null`、`paramHashes` 取新项目指纹）。
  - `frontend/tests/workbenchEdit.test.ts`：新增 `describe("V1.11.3 loadProject 复位项目级瞬态")` 2 用例（载入空项目：全瞬态清空 + 游标落 ep1 无场景；载入有场景新项目：任务清空 + 游标落 sc_a/shot1）。
  - `frontend/tests/workbenchRender.test.ts`：新增 `describe("V1.11.3 新建项目不残留上一项目镜头/任务（#399）")` 1 用例（UI 级：载入示例 → 加 3 镜任务 → 返回首页 → 新建项目 → 断言任务/镜头卡片全清空）。
- **验证**：前端全量 **442 passed / 1 skipped**（新增 3 条回归用例）+ `npm run build` 全绿（vue-tsc 类型检查 + vite 构建通过）。
- **坑**：本次改动**纯前端**，只需刷新浏览器重新加载 SPA，**无需重启 Comfy Desktop**。

### #400 P0-① 中文自然语言 → H3 Prompt 转换机制重构（2026-08-13，用户拍板四项设计决策）

> P0-① 是「H3 链路审计」后的最高优先级重构：把「剧本中文原文 → H3 Prompt」从黑盒拼串改成**三层可追溯**结构，保证原文事实绝不被 AI 覆盖、AI 只做补全、规则只做运镜/光线等硬信息。

- **背景**：H3 链路审计发现旧 prompt_builder 把用户原文、AI 补全、规则模板混成一坨字符串，无法区分「哪些词来自剧本、哪些是 AI 编的、哪些是规则上的」→ 用户追查「为什么这镜长这样」没有依据。
- **用户拍板四项设计决策（验收约束）**：
  1. **原文事实最高优先级**：双轨合并必须保留原文——用户原文 → 事实抽取 → DirectorIntent → 去重合并 → H3 Prompt；任何 AI 补全不得覆盖原文。
  2. **Qwen 跨界实体先过边界检查**：原始实体 → 规则实体 → Qwen 补充 → 边界检查 → 合法实体 → Asset Matcher；跨镜越界的幻觉实体必须丢弃（`entity_in_shot_scope`，源文本短于 20 字不起用）。
  3. **`minimax-h3-project-v1` 是项目自己的 schema**，不是模拟官方 MiniMax H3 Skill（不做官方字段面抄作业）。
  4. **[REF: 名] 标签只做可读性**，真实 ref-image 绑定走结构化映射 `{"references": [{"entity_key", "entity_name", "image_file"}]}`，不依赖 prompt 内文本顺序。
- **后端新结构（三层）**：
  - **L1 `user_original_intent`**：剧本 source_text 逐字保留（不被覆盖），provenance=USER。
  - **L2 `DirectorIntent`**（`director/production_plan.py` IntentSource 枚举 + dataclass；`director/director_intent.py` build_shot_intent/build_plan_intents）：`retained_facts`（原文事实逐字，USER）/ `emotion`（AI 推断，AI）/ `composition`（=visual_intent，AI）/ `continuity.camera_desc`（规则运镜，RULE）+ 字段级 provenance 枚举 USER/AI/RULE/MIX；实体经边界检查 + 角色锚点归一（`plan_entity_key` 把别名并入 canonical）。
  - **L3 H3 Prompt**（`director/h3_prompt_builder.py` build_h3_prompt/build_plan_h3_prompts）：`integrated_multimodal_description`（时间轴 `[0-{d}s]` 前缀 + 原文事实 + AI 补全 + 镜头/光线/氛围 + `[REF: 名]`）/ `overall_soundscape` / `non_diegetic_music` + 段落级 provenance `{user_facts, ai_supplement, rule_generated}` 每段三条目清单 + 结构化 `references`。
- **去重保护**：difflib.SequenceMatcher ratio≥0.7 的 AI 句丢弃，绝不覆盖原文事实。
- **路由**：`/prompt/h3`（build_plan_intents + build_plan_h3_prompts，含 bindings_by_shot/duration_by_shot）、`/prompt/draft`（build_plan_drafts 返回 drafts + intents 同源）。
- **前端三层可追溯 UI**（models/project.ts `H3PromptSnapshot` + WorkbenchView.vue + 导入弹窗）：每镜「原文（橙 label）→ AI 理解（蓝 label，badge = AI 构图/AI 情绪/规则运镜）→ H3（绿 label，三段 + 原文 N / AI N / 规则 N 计数 badge + [REF] chip）」只读审计视图；不参与指纹/生成（生成时后端独立组装）。
- **真实回归 #466**：新增 `tools/regr_p0_shot1_4_7.py`（复刻 /prompt/h3 真实链路，load 真实 fixture `frontend/fixtures/shanyu_real_plan.json`，断言 85 项）：
  - **Shot 1**（scene_01:shot_01，环境镜）：L1=原文逐字；越界「旧剑匣」被边界检查丢弃（实体 3→2，仅留山雨楼/老槐树）；references 2/2。
  - **Shot 4**（scene_02:shot_02，对白+角色）：「客官，落座歇脚，茶先暖着。」逐字进 H3 描述；AI 构图/情绪 badge。
  - **Shot 7**（scene_02:shot_05，多人+动作）：沈青崖/柳如烟/蒙面人/短刃 4 实体全进 references（角色锚点归一后键一致）；「剑，交出来。」逐字保留；运镜=RULE。
  - 全覆盖维度：三层结构 / 字段级 provenance 大写枚举 / L1==source_text 严格逐字 / retained_facts 全进描述 / AI 只作补充句 / `[REF: 名]` 标签齐全且计数=references 数 / 三段段落级 provenance / 纯规则零显存零网络 grep。
  - **VM 85 PASS / 0 FAIL** + **用户本机实测 85 PASS / 0 FAIL**（`cd /d D:\夸克网盘\ComfyUI_MiniMaxH3_Director && "D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\regr_p0_shot1_4_7.py`）。
- **坑**：DirectorIntent 是**派生数据**，不持久化进 ProductionPlan JSON（每次 build_plan_intents 重建）；provenance 枚举是大写值（与前端 DTO 小写注释无关，UI 用 badge 名不渲染枚举值）；`Shot` 无 scene_id 属性（scene-scoped shot_id）——写 duration_by_shot 等 dict 用 `{sc.scene_id}:{s.shot_id}`。
- **未动基础设施**：Asset Registry / Stable Entity Key / Persisted Binding / executor continuity / 三级反馈 / camera state machine / Workflow Studio / ETA / project persistence 均零改动。

### #401 P0 修复包：cesi Shot 01 跨镜资产污染根治（#468 审计报告落地，2026-08-13）

> 背景：审计报告 `tools/audit_cesi_shot01_asset_pollution.md` 发现 cesi Shot 01（空镜远景）的错误执行结果把**其他镜才该出现的道具（旧剑匣/伞）注入 refs**，且山雨楼外没成为 location ref。根因 = gen_timeline 把**整个场景池 props 全量**塞进该镜 extra_assets → executor 逐条注入 ref_image，跨镜资产污染。用户批准四项修复包并给验收工作流。

- **P0-1 后端 Shot 级资产边界**：`director/gen_timeline.py` 原「场景池 props 全量注入 extra_assets」块替换为**仅按该镜显式 `propIds`（或 <picture> 显式标签）检场景池**；`director/executor_core.py` 资产注入拆成模块级纯函数 `build_shot_asset_items(cast, location, extra, tag)`，注入顺序=角色→场景→道具→tag。Scene assets 保留为场景级素材管理（进「场景素材组」折叠区），**绝不作 per-Shot 默认 refs**。铁律①：一个 Shot 拿不到其他 Shot 的资产。
- **P0-4 Location 标题兜底**：`director/script_parser.py` `_location_candidate_from_title` 从「子串 replace 时间/天气词」改为**边界整词剥除**（startswith 循环 + 剥「的」+ 去括号/逗号天气段）——「第一场：山雨楼外」→ 山雨楼外，「清晨的站台」→ 站台；绝不再把单字「雨」（同时是天气词与地名词「山雨楼外」）子串替换掉。
- **P0-3 硬测试**：`director/tests/test_asset_shot_boundary.py`（6/6 全绿）——不只看 project.json，两层断言最终 generation refs：plan 层每段 extra_assets 只含显式 propIds 声明；executor 层 `build_shot_asset_items` 折叠为注入列表并按矩阵断言 forbidden 绝不在。Matrix（《山雨客栈》：Shot01 空镜灯笼 allowed 山雨楼外/灯笼 forbidden 旧剑匣/伞/沈青崖；Shot02 allowed 山雨楼外/沈青崖/旧剑匣/伞 forbidden 柳如烟/蒙面人；Shot03 allowed 客栈大堂/柳如烟/茶碗 forbidden 旧剑匣/伞/蒙面人）+ 旧段无 propIds 不扩散场景池 + V1.2.7 `<picture>` 标签回归（显式注入 id 去重）+ P0-4 标题兜底 4 例。测试用 timeline output 144×80（防 OOM，864×480 真实 torch.full 每镜 ~618MB×3 被杀）——注意 `_tensorized()` 把 plan 值换算带真实 tensor 的 GlobalAsset 直调 executor（plan 层资产因 imageFile 不存在 tensor=None 会整体跳过）。
- **P0-2 Workbench「本镜实际引用资产」**：`frontend/src/workbench/WorkbenchView.vue` + `stores/workbench.ts`——「👤 资产」区只显示**本镜实际引用资产**（resolved 各处 source=shot/scene/project 语义继承 + 显式 propIds/styles + @命中），Scene Asset Pool 改独立可折叠「📦 场景素材组 ▸」（默认收拢），徽标「有图/无图」。`frontend/src/core/inheritanceResolver.ts` prop/style 分支=仅显式 ∪ @命中，无场景池全量（前会话已改，本轮 UI + 测试收口）。
- **前端测试**：`frontend/tests/workbenchRender.test.ts` P0-2 断言重写（生效资产区不得出现场景池「黑伞」；点开场景素材组折叠区后其中含黑伞；`findAll(".b-scene-mat")` 是「有图/无图」徽标 class **非**名字容器，名字在 `.scenemat-grid .inherit-asset`，且折叠默认不在 DOM 须先 trigger click 再断言）+ `coreModules.test.ts` P0-1 prop/style 边界 5 例（前会话已改）。
- **验证**：后端关键 3 个测试文件全绿（test_asset_shot_boundary 6/6、test_script_parser+test_script_import 24、test_gen_media_tags 8；全量 pytest 失败=存量 sys.modules 顺序污染 + 环境依赖 ffmpeg，与本轮无关）；前端 **444 passed / 1 skipped + build 全绿**（vue-tsc + vite）。
- **坑**：① 同步用 rsync 覆盖 DEP `director/` + `frontend/dist/`；本包未改 `http_routes.py` → **无需重启 Comfy Desktop，刷新浏览器即可**；② R1 验收在 ComfyUI 里**重新导入《山雨客栈》剧本**后选 Shot 01（空镜前景），生成的 refs 须含 `山雨楼外.png`（或 `灯笼.png`）、绝无 `旧剑匣.png`/`伞.png`；R2 选 Shot 02 验证 `沈青崖.png` + `旧剑匣.png` + `伞.png` 齐全；③ 旧项目（已生成的 timeline/缓存）不受影响，重新生成即按新边界。

### #452 P0-③ Shot-to-Shot 连贯性：上一镜状态 + 镜头关系 + 必须保持清单（2026-08-13，后端纯规则零 LLM）

> 背景：H3_PIPELINE_AUDIT §五 P0-③ 三要素——①Previous Shot State 结构化、②镜头关系 transition_type、③必须保持 vs 允许变化分离。运镜已由 camera_template 状态机满足；本轮补「上一镜状态 + 镜头关系 + 一致性保护」进 DirectorIntent.continuity，并**可选**渲染进 H3 description（严格向后兼容：首镜/旧数据渲染不变）。

- **`director/director_intent.py`** 新增 3 个纯函数（零 LLM）：
  - `_prev_shot_state(prev)` → 上一镜**结构化身份字段**投影（subject/characters[:3]/location/lighting/emotion/camera_template），**不含 source_text 逐字、不含动作**——动作/表情由本镜自主推进（may_change 语义），遵守「不将原始文本强制注入 H3 Prompt」。
  - `_transition_type(prev, cur_template_id, ...)` → 镜头关系规则推导：无上一镜→`""`（场景首镜）；对白+对白→`cut`；过肩+对白→`reverse`（camera_desc 已有「反打对切」措辞不重复渲染）；特写→全景→`close_to_wide`；全景→特写→`wide_to_close`；特写+特写→`reaction`；同地点且同主体/共享角色 → 动作重叠→`match_action` 否则→`continuation`；默认→`cut`。
  - `_must_keep_items(prev, ...)` → 身份类一致性清单（共享角色「外观一致」/相同地点「场景不变」/相同光线「光线氛围延续」）；`may_change`（镜头景别/动作推进/情绪表情）只结构化记录**不注入**。
  - `build_shot_intent` 加 `prev_intent: Optional[DirectorIntent]=None`（向后兼容，默认 None 不改变既有调用）；continuity 有 prev 时追加 `transition_type`/`prev_shot_state`/`must_keep`/`may_change`。`build_plan_intents` 场景内维护 `prev_intent` 逐镜传递、**场景切换自动重置**（每场景首镜 prev=None）。
- **`director/h3_prompt_builder.py`** `_continuation_block(intent, main_txt)`（可选用）：仅当 `continuity.prev_shot_state` 存在时输出——「延续上一镜，{共享角色/同主体}（仍在{地点}）。」+ 镜头关系句（`_TRANSITION_CN` 只渲染 continuation/reaction/close_to_wide/wide_to_close/match_action，reverse/cut 的衔接措辞 camera_desc 已覆盖）+「保持{must_keep}。」。延续句只在共享主体/角色且未逐字重复时才写。provenance 标 `rule_generated`。
- **后端测试**：新增 `director/tests/test_shot_continuity.py`（42 用例）——transition 9 分支、prev_shot_state 结构化（无 source_text/动作）、must_keep 清单（含 may_change 不进）、双场景 build_plan_intents 场景切换重置、build_h3_prompt 有 prev 渲染/首镜不渲染/不注入上一镜 source_text 碎片/rule_generated、向后兼容（无 prev_shot_state 渲染不变）。顺带修复存量失败 `test_asset_matcher.py`「funnel 结构齐全」断言（#459 加的 `boundary_dropped` 键未同步进断言）。
- **验证**：后端全量 23 个 `director/tests/test_*.py` 全绿（SRC 与 DEP 双端）；双目录 rsync + md5 一致。
- **坑**：① 纯后端规则层，无需重启 Comfy Desktop 也能跑测试；但 DirectorIntent 是派生数据（不落 project.json），前端正式生成链路走 V1.7 导入后重建，**想看到新 H3 需要重新导入剧本触发 AI Draft 重生成**；② 延续块只在「有上一镜且内容不重复」时出现，首镜/单镜/无实质延续信息时为空——刻意克制，避免 H3 描述冗余。

### #453 P1-④ Generation History 完善：current_generation_id 落盘 + 加载兜底（2026-08-13，纯前端）

> 背景：V1.11 已实现 `Shot.generations[]` + `activeGenerationId`（⭐ 当前成片），`serializeProject` 整树序列化会让 generations 自然持久化。本轮补两个缺口：①没有「current_generation_id 随保存落盘 + 重载恢复」的闭环硬测试；②若 `activeGenerationId` 指向不存在的版本（外部编辑 / generations 部分清理 / 旧数据损坏），UI 信息卡会显示悬空的「⭐ 当前成片」。

- **store**：`frontend/src/stores/workbench.ts` 新增 `normalizeGenerationState(project)`（幂等，对健康项目无副作用，不新增/不删除 generations、不改变引用——#455 隔离契约不受影响）：无 generations → 清空 `activeGenerationId`；`activeGenerationId` 不在 generations 里 → 回退到最后一条（等价「最新」）；否则保留用户选的版本。`loadProject` 在 `normalizeShotInheritance(project)` 之后调用它。
- **回归测试**：`frontend/tests/workbenchRender.test.ts` 新增 `#453 Generation History 持久化 round-trip` describe（4 用例）——①保存 payload 断言落盘 `generations`（2 条）+ `activeGenerationId="g2"`（用户「⭐ 设为当前成片」选的版本），再 `serializeProject → deserializeProject → loadProject` round-trip 后 store 恢复 + 版本条 `.gb-adopted` 徽标仍在 #2（#1 无 ⭐）；②悬空 `activeGenerationId` → loadProject 回退到最后一条；③无 generations → 清空；④`addGeneration` 首版自动采纳 / 后续版本不自动切换 / `removeGeneration` 删当前成片回落到最后一条 / 删空后清空。
- **验证**：完整前端套件 **456 passed / 0 skipped**（452 + 4 新增）+ build（vue-tsc + vite）全绿；双目录同步 md5 一致。
- **坑**：纯前端，无需重启 Comfy Desktop，刷新浏览器即可；`normalizeGenerationState` 只做加载兜底，不改动保存链路（保存仍走 `serializeProject` 整树序列化）。

### #455 P1-⑥ 新项目状态隔离验证：shots/generations 不跨项目泄漏（2026-08-13，纯前端）

> 背景：#399 已修「loadProject 只换 state.project 不清 tasks/成片/预览/游标」；本轮给「项目级瞬态隔离」补对象级硬测试，防将来回归成浅合并（把 A 项目 shot 的生成历史/资产/角色带进 B 项目同名 shot）。

- **测试**：`frontend/tests/workbenchEdit.test.ts`（V1.11.3 describe 块内新增「跨项目切载」用例）——projectA 的 shot1 挂 generations+activeGenerationId+castIds+castManual+content，loadProject(projA) 后断言生效；loadProject(projB) 后逐项断言 **sB.generations 为 undefined、activeGenerationId undefined、content.visual 是 B 的内容、castIds undefined**、游标落 ep1/sc_a/shot1、tasks 0、finalVideo null、dirty false。
- **设计**：loadProject 直接替换 `state.project` 引用（不浅合并），故隔离天然成立；测试钉死该契约，任何未来实现若改成合并式更新会立刻红。
- **验证**：`npx vitest run tests/workbenchEdit.test.ts` 全绿（68 用例）；完整前端套件 **448 passed / 1 skipped** + build（vue-tsc + vite）全绿。
- **坑**：无需重启 Comfy Desktop，刷新浏览器即可（纯前端）。

### #456 P1-⑦ 修复镜头时长输入「自动变化」Bug（2026-08-13，用户实测 bug，纯前端）

> 背景：用户现场输入 5.5 时一松开就跳回旧值（5/6），小数秒输不进去。根因=`@change` 才写回 store，但输入过程中任一次响应式重渲染（进度轮询/切镜 watch 触发的 patch）都把 `:value` 反写为旧 store 值，把正在输入的 5.5/5.55 吞掉。

- **修复**：`frontend/src/workbench/WorkbenchView.vue`——镜头时长输入改「编辑期草稿」模式：`durationDraft` ref 顶住 DOM value（重渲染只对比草稿、不改用户输入），`@input` 只更新草稿，`@change`（失焦/回车）才 `Number(raw)` 解析并写回 store，随后清空草稿；`watch([shot, scene])` 切镜/切场景时无条件清草稿。空串/`NaN` 被拦截不写坏 store。
- **回归测试**：`frontend/tests/workbenchRender.test.ts` 新增 `#456 镜头时长输入编辑期草稿` describe（3 用例）钉死四条不变量：①输入中数值保持 5.5 不动、期间重渲染不反写、未失焦 store 不变；②空/非数字输入被拦后 store 保持最后合法值、输入框回显 store 值；③编辑途中切镜草稿清空、新镜回显自己时长。
- **关键测试经验（How to apply）**：@vue/test-utils `setValue` 对 number input 会**同步连发 input+change**（change 即提交），无法模拟「输入中未失焦」；且 setValue("") 的同步事件风暴在 happy-dom 下会让最后一次渲染 patch 因 `oldValue===newValue` 被跳过、DOM 残留空值——**测试必须用真实时序**：`el.value=…` + `dispatchEvent(new Event("input",{bubbles:true}))` + flush 隔开，再单独 `trigger("change")` + flush（与真实浏览器打字/失焦两次独立事件一致）。探针已验证该时序下 happy-dom 回显完全正常。
- **验证**：`npx vitest run tests/workbenchRender.test.ts tests/workbenchEdit.test.ts` 全绿；完整前端套件 **448 passed / 1 skipped** + build 全绿。
- **坑**：纯前端，无需重启 Comfy Desktop，刷新浏览器即可；`durationDraft` 只在「镜头设置」面板内生效，右侧切到「🎬 场景设置」（`.scene-name` → `selectSceneSettings` 设 `currentPane="scene"`）会卸载镜头面板、草稿自然丢失，属既定语义。

### #454 P1-⑤ 视频播放稳定性：灰卡/URL 失效修复（2026-08-13，纯前端）

> 背景：生成版本视频靠 `/view?type=output` 指向 ComfyUI output 临时目录，ComfyUI 重启/文件被清理后 URL 失效 → 播放器静默黑屏/灰卡，无任何提示；播放队列中单镜视频缺失会中断整条队列。

- **修复**：`frontend/src/workbench/WorkbenchView.vue`——
  ① 主播放器 + Cinema Mode 两个 `<video>` 补 `@error="onPlayerVideoError"` / `@loadeddata="onPlayerVideoLoaded"`：URL 失效不再无声，先自动回退到本镜**归档版本**（`gen.videoFile` → input 目录 `comfyInputUrl`，重启不丢），回退也失败/无归档则显示明确错误横幅（`.player-err` / `.cinema-err`）；同一 URL 只回退一次（`playerRetriedUrl` 防无限切），`loadeddata` 成功加载即清横幅。
  ② 移除 `watch(finalVideo.url)` 自动清空（它会把「已自动切换至归档版本」消息在 `setFinalVideo` 后立即吞掉）；改为在用户主动操作处清空：`viewGeneration`（点版本卡）、`createGeneration`（生成完成切最新版）。
  ③ `loadPlayIndex` 单镜 `segmentMp4` 失败不再中断整条队列——跳过该镜（`跳过 XX（视频不可用）· 继续播放`）播下一镜；全部失败才 `setError("没有可播放的视频…")`。
- **回归测试**：`frontend/tests/workbenchRender.test.ts` 新增 `#454 视频播放稳定性` describe（4 用例）：①有归档版本 `@error` 自动切到 input 归档并提示；②无归档显示明确错误 + `loadeddata` 清横幅；③播放队列单镜失败跳到下一镜（`segmentMp4` 调用 2 次 + 播放器有 blob）；④全队列失败给明确错误。`@error` 用 `trigger("error")` 直接派发（error 不冒泡但元素级监听可捕获）。
- **验证**：完整前端套件 **452 passed / 0 skipped**（448 + 4 新增）+ build（vue-tsc + vite）全绿；双目录同步 md5 一致。
- **坑**：纯前端，无需重启 Comfy Desktop，刷新浏览器即可；归档回退只发生在「本镜存在带 `videoFile` 的版本」且当前 URL 非该归档时，避免无意义切换。

### #457 P2-⑧⑨ UI 打磨：已采纳降权 + 版本条精简（2026-08-13，纯前端刷新即生效）

> 背景：P0-③ Shot-to-Shot 连贯性（#452）落地后 UI 打磨两处——⑧ 已采纳卡片视觉降权（把视觉重心留给「待审核」+「当前选中」卡片）；⑨ Generation History 版本条与主播放器去重全屏入口（沿用用户原则「信息只展示一次」#397）。

- **⑧ 已采纳降权**：`frontend/src/workbench/WorkbenchView.vue`——
  ① `.shot-card.shot-draft-ok` 移除整卡绿色虚线边框 + 绿色背景 → 透明（此前 `#2f6b42`/`#1e2a24`）；已采纳是终态，不需要整卡强调；
  ② `.shot-draft-badge.adopted` 从绿底绿边强徽标 → 无边框小字（`color:#6f8a75`）；
  ③ 已采纳卡片上的「✓ 采纳」按钮从卡片消失（`.shot-draft-adopt`），只留小徽标 → 视觉重心落在待审核 + 当前选中卡片。
- **⑨ 全屏入口唯一化**：gen-strip-foot 删除与主播放器右上角 [⛶] Cinema Mode（`.ph-cinema`）重复的「⛶ 放大预览」按钮，foot 只剩版本操作「⭐ 设为当前成片」；`.gen-foot-adopt` 移除 `margin-left:auto`（foot 现为单按钮居中）。
- **测试**：`frontend/tests/workbenchRender.test.ts`——新增 `P2-⑨ 版本条 foot 只保留「⭐ 设为当前成片」` 用例（foot 恰 1 按钮、无「放大预览」、`.ph-cinema` 仍存在）；增强既有 1385 用例断言 `.shot-draft-adopt` 消失。workbenchRender 104 用例全绿；完整前端套件 23 文件 / **457 passed** + build（vue-tsc + vite）全绿。
- **坑**：纯前端，无需重启 Comfy Desktop，刷新浏览器即可；`.ph-cinema` 是全屏唯一入口，测试 1674-1675 依赖它，勿删。

### #451 P0-② H3 Prompt Skill 整理：使用链路确认 + 可替换性盘点（2026-08-13，纯审计不改代码）

> 背景：P0-①（#400）建好三层可追溯后，盘清「H3 Prompt Skill」的真实使用链路与可替换性，为后续把 Skill 规范接入产品链路做前置。**本条目不编写 Skill、不改代码**。审计文档 `H3_PROMPT_SKILL_AUDIT.md`（已同步部署）。

- **确认 1｜产品链路不使用任何 Skill**：正式链路 = 后端 `h3_prompt_builder.py`（项目 Schema `minimax-h3-project-v1`）+ 前端 `promptSections.ts` 分区合并。Skill 仅是 Claude 写 H3 提示词的工作辅助，不进入用户生成链路。
- **确认 2｜Skill 文件位置**：插件只读缓存目录 `skills-plugin/<uuid>/skills/`；9 个 H3 相关 skill 目录**全部只有 SKILL.md 单文件，无 references/ 子目录**。
- **确认 3｜两套模板并存**：① 旧版 `prompt_builder_v17.py` + `prompt_templates/v1.json`（五区中文草稿，`POST /prompt/draft`，供 Workbench 人工审核）；② 新版 `h3_prompt_builder.py`（三段 `minimax-h3-project-v1`，`POST /prompt/h3`，正式 H3 Prompt + references + provenance）。两套共享同一 `build_plan_intents`（DirectorIntent 状态机）来源，非重复实现；Qwen 只产出 DirectorIntent 结构化字段，不直接生成 Prompt 文本。
- **确认 4｜缺失引用**：`h3-prompt-writing/SKILL.md` 引用 `references/base-en.txt` 与 `references/ref-en.txt`，**两文件均不存在**（skill 包 + 全仓库 find 无结果）；co-op-game-intro 引 2 个、3d-animation 引 5 个 references/*.md 也全部缺失。用户 2026-08-13 已拍板官方仓库不存在 → 用项目自己的 schema。
- **确认 5｜最终进入 H3 的 Prompt 转换层数**（关键）：当前生成提交 `prompt = buildShotPromptText(shot)`（`promptSections.ts`：visual+cameraText+style+soundText 按语言逗号拼接，**不翻译不套模板**）→ 后端 `gen_timeline` 读 `seg_data.get("prompt")` → `strip_media_tags` → 进 workflow。**三段式 H3Prompt（/prompt/h3）目前仅审计预览 + Shot.h3Prompt 快照展示，尚未接入生成提交**——这是后续若要把 Skill 规范接入生成时的切换点（本条目不实施）。
- **可替换性**：替换 Skill 极易（产品不依赖任何 skill）；`h3_prompt_builder` 的 `SCHEMA_VERSION` 已项目化可版本化升级；残留风险 = h3-prompt-writing 引用缺失文件，建议未来用项目 schema 文档替换其 references 引用（待用户拍板）。
- **验证**：grep 确认 http_routes 同时 import 两 builder、前端提交 prompt 来源、后端 gen_timeline 读 seg.prompt、三段字段仅出现在 /prompt/h3 + WorkbenchView 展示 + comfyApi 类型；find 确认无 base-en/ref-en 文件。

### #475-#482 P0-A 三段式 H3 Prompt 接入生成提交 + P0-B Build ID（2026-08-13）

> 背景：#451 审计「三段式 H3Prompt 尚未接入生成提交」是已知缺口。本轮 P0-A 把它接上生成闭环，并补 P0-B 两个工程项。**用户拍板的正式生成链（#479 验收标准）**：`原始剧本 → DirectorIntent → 用户在 Workbench 修改 → overrides_by_shot → build_shot_intent() → h3_prompt_builder → 三段式 H3 Prompt → ComfyUI / H3`——**取代**旧链（五区字符串 → 直接拼接）。明确不做：Shot-to-Shot、CLIP/DINO、Scene Manager、新 UI 功能。

- **#475/#476 侦察**：确认后端 `/prompt/h3` 路由消费链（`/prompt/h3 → build_plan_intents → h3_prompt_builder`）、SPA 生成提交点（`handleGenerate / runSequentialGenerate / runOneSequentialShot` 三处拿到 shot prompt 后进 buildTimelinePayload）。
- **#477 P0-A 后端五区 overrides**（`director/production_plan.py` + `director/director_intent.py` + `director/h3_prompt_builder.py` + `director/http_routes.py`）：
  - `build_shot_intent(overrides=…)` → `_apply_user_overrides`：`visual→composition`、`cameraText→camera_desc`、`style→style`、`soundText→audio.ambient`；**`source_text` 绝不可被 override**（原文事实最高优先级）。
  - `build_plan_intents(overrides_by_shot=…)` 接受 `{scene_id}:{shot_id}` 键字典；`/prompt/h3` 路由透传 `overrides_by_shot / bindings_by_shot / duration_by_shot`。
  - 新增 `director/tests/test_h3_prompt_overrides.py`（28 用例，依赖桩走纯规则）：五区各覆盖字段、空分区不覆盖（保留 AI/规则默认）、source_text 永不覆盖、provenance 标 USER。
- **#478 P0-A 前端 plan 持久化**：`Project.plan` 进项目数据（`frontend/src/models/project.ts` + store），Workbench 编辑进 project data，**保存→关闭→重开 survive**；override 数据来源可追溯。
- **#479 P0-A 生成时实时重建**（`frontend/src/core/rebuildPlan.ts` + `frontend/src/core/directorCore.ts` + `frontend/src/workbench/WorkbenchView.vue` + `frontend/src/services/comfyApi.ts`）：
  - `rebuildPlanFromProject`：从当前 Project 重建 ProductionPlan（有 plan 取 plan 权威 source_text，无 plan 从 description「原文：」提取），carry actions/emotion/dialogue/visual_intent/entities/visual_elements。
  - `buildOverridesByShot`：五区编辑 → `{scene_id}:{shot_id}` 键 overrides（空分区不写）。
  - `buildDurationsFromProject` / `buildBindingsFromProject`：时长 → `duration_by_shot`（H3 `[0-Ns]` 时间轴）；本镜显式 castIds/propIds → `bindings_by_shot` entity_key（`{type}:{canonName}`，sourceEntityId 命中 plan 实体则 asset_id=实体 id，location 兜底 scene.referenceImage）。
  - `rebuildSubmissionPrompts()`：生成前调 `/prompt/h3`（带 overrides/bindings/durations）→ `mapPlanPromptsToShots` → 三段式提交文本；**成功则作为提交 prompt，失败/空则回退旧 `buildShotPromptText`（向后兼容）**。
  - `buildTimelineStructure` 加 `promptOverride: {[shotId]: 三段文本}`：有 override 的镜 prompt=三段提交文本（不用旧五区合并），无 override 镜回退 buildShotPromptText。
- **#480 P0-A 回归测试**：后端 `test_h3_prompt_overrides.py` 28 用例 + 全量 24 测试文件全绿；前端 `tests/rebuildPlan.test.ts`（27 用例：serializeH3Submission 三段拼接/空分区跳过、mapPlanPromptsToShots、buildDurationsFromProject、buildBindingsFromProject 含实体/位置兜底）+ `tests/roundtrip.test.ts` P0-A describe（promptOverride 接入 buildTimelineStructure、无 override 不改变行为）。
- **#481 P0-B 删除旧 build 死代码**：`web/index.html` + `web/assets/`（旧 SPA 构建产物，引用 `/assets/index-uj67xJA7.js`，无任何代码引用）删除——`WEB_DIRECTORY="./web/js"` 只服务 legacy 面板，SPA 走 vite dev server/dist，死代码纯占位。
- **#482 P0-B 顶栏 Build ID**（纯前端）：`vite.config.ts` 加 `define: { __BUILD_ID__ }`（每次 build/dev 启动唯一，格式 `v{pkg.version}·b{yymmdd}-{hhmm}`）+ `src/env.d.ts` 声明 + `src/core/buildId.ts` 透出 + `WorkbenchView.vue` 顶栏右侧极弱视觉小字 `.wb-build-id`（hover 显示完整构建号，报 bug 精确对构建）。本次构建：`v0.1.0·b260813-2313`。
- **验证**：后端全量 24 个 `director/tests/test_*.py` 全绿（SRC 与 DEP 双端）；前端 **485 passed / 24 files** + `npm run build` 全绿（vue-tsc + vite）；`grep -o 'v0\.1\.0·b[0-9]\{6\}-[0-9]\{4\}' dist/assets/index-*.js` 确认注入；双目录 diff 代码目录完全一致。
- **坑**：① 改 `director/http_routes.py` + `director/director_intent.py` + `h3_prompt_builder.py` 后**必须重启 Comfy Desktop**（路由启动时注册）；② #482 纯前端刷新即生效；③ override 只覆盖五区编辑过的字段，未改字段仍走 AI/规则默认；④ 生成链回退是**有意的向后兼容**（/prompt/h3 失败/空不阻塞生成，回退旧五区合并），排查问题时先看 TaskCenter 错误是否有 /prompt/h3 报错。

### #484 诊断「视频生成不了，一直在解码」（2026-08-14 诊断中）

> 用户报告 SPA 生成跑到 decode 阶段后 4-7 分钟无输出。隔离脚本 `tools/diag_decode_hang.py` v1 复现出「视频解码 OOM 13.6GB」，**v2 证实那是脚本假阳性**，真根因在 SPA 侧（aimdo 动态驻留），结论如下，修复待隔离重跑 + SPA 实测后再定。

- **v1 假阳性根因**：隔离脚本没包 `torch.inference_mode()`。ViT3D 视频解码器有 36 层 transformer，正常推理（ComfyUI `execution.py:751` 用 `with torch.inference_mode():` 包整个 prompt 执行）中间激活随层即算即丢；没有 inference_mode 时 autograd 图保留 36 层全部中间激活（一层 ~450MB × 36 ≈ 13.6GB）→ 直接 OOM。**隔离测试的 OOM 不代表 SPA 路径也会 OOM**。
- **v2 修复**（`tools/diag_decode_hang.py`，纯诊断工具零产品代码）：解码全程包 `torch.inference_mode()`（与正式执行一致）+ 显存记账（`_mem_summary`：allocated/reserved/peak）+ VAE patcher 类型打印 + `--tiny`（256x256/22 帧单时间块快速基线）+ 两 VAE 加载后峰值重置。
- **SPA 侧主嫌疑（待证实）**：MiniMax H3 视频 VAE 5GB，aimdo 动态驻留会把它 staged（SPA 日志曾见 `MiniMaxH3VideoVAE prepared for dynamic VRAM loading. 4965MB Staged`）；若解码时逐层换入，2 时间块 × ~48 tile × 36 层 ≈ 3400 次权重换入 → 4-7 分钟。隔离（完全驻留）下应为秒级~分钟级。
- **验证**：SRC/DEP md5 一致；ast.parse 语法双端 OK；下一轮 = 用户跑 v2（默认参数 + `--tiny` + `--no-tile` 三连）→ 按输出定 SPA 侧修复（force_full_load / 解码前卸载 UNET / 关闭 aimdo staged）。

### #485 根因实锤 + VAE 非动态化修复（2026-08-14 交付）

> **根因（实锤）**：隔离脚本 v2 三连全绿（`--tiny` 1.2s/5115MiB、默认 864×480/124帧 14.1s/5333MiB、`--no-tile` 8.6s/5761MiB，全部 `VAE patcher: ModelPatcher is_dynamic=False`）→ 解码本身在完全驻留下秒级完成，**问题在 SPA 侧 aimdo 动态驻留**。`main.py:278` 检测到 comfy-aimdo 后全局执行 `comfy.model_patcher.CoreModelPatcher = comfy.model_patcher.ModelPatcherDynamic`，服务端所有 `comfy.sd.VAE()` 构造的 patcher 都变动态；MiniMax H3 视频 VAE 5GB 被 staged，解码时 36 层 ViT3D × 7 时间块 × ~48 tile 逐层换页 → 4-7 分钟「卡解码」。隔离脚本不跑 main.py 所以不受影响。

- **修复**（`director/vae_residency.py` 新建）：`ensure_vae_resident(vae)` 解码前把动态 VAE patcher 换成**非动态代理**并设 `vae.disable_offload=True` 让 `VAE.decode` 走 `force_full_load=True` 全量驻留路径。主路径 = ComfyUI 官方 `ModelPatcherDynamic.get_non_dynamic_delegate()`（VAELoader 注册的 `cached_patcher_init` 工厂，从 vae 文件重建 ops 干净的非动态模型）；工厂缺失/抛错回退 `vae.get_sd()` 重建 + 强制基类 `ModelPatcher`（绕开 main.py 的 CoreModelPatcher 替换）。按 vae 文件路径做模块级缓存避免每次重读 5GB。幂等：非动态 patcher 时直接返回 False。
- **接入**（`director/executor_core.py::_decode_av_latent`）：在 split AV latent 之前对视频 + 音频两个 VAE 各调一次 `ensure_vae_resident`，成功则打 `[DECODE] VAE 非动态化完成 t=… video_swapped=… audio_swapped=…`。
- **单元测试**（`director/tests/test_vae_residency.py` 新建，7 用例）：幂等 noop / 动态+工厂走官方 delegate / 同 key 缓存复用不重建 / 工厂缺失回退 get_sd / 工厂抛错回退 / 重建失败 noop 不破坏原状态 / 无 patcher 返回 False。**7/7 全绿**。
- **验证**：SRC/DEP md5 一致；executor_core.py ast.parse OK；`test_vae_residency.py` 7/7 PASS。`http_routes.py` 未动，但 executor_core 变了，**稳妥起见重启 Comfy Desktop**。
- **坑**：① `ModelPatcherDynamic.load` 有 `assert not full_load` → 单设 `disable_offload=True` 必崩，必须先换非动态 patcher；② `get_non_dynamic_delegate()` 需要 `cached_patcher_init` 工厂，纯 `comfy.sd.VAE()`（无 VAELoader 路径）会抛 RuntimeError → 走 get_sd 回退；③ `segment_continuity.py` 里的 `vae.encode()` 保持动态 patcher 可接受（编码在解码前，且为参考 latent 量小）。

### #486 解码诊断打印 latent dict 崩溃修复（2026-08-14）

> 用户实测复测 #485 时生成报 `'dict' object has no attribute 'shape'`。根因是 **#484 诊断插桩**在 `_decode_av_latent` 里直接 `video_latent.shape`，而 ComfyUI 0.32.0 的 `LTXVSeparateAVLatent.execute` 返回 latent **dict**（`{"samples": tensor, ...}`，ComfyUI latent 标准格式）→ 崩在 split 后的打印行，解码根本没开始。`VAEDecode().decode()` 要的本来就是 dict，真正的问题只在诊断打印这几行。

- **修复**（`director/executor_core.py`）：新增 `_latent_meta(x)` 稳健提取 (shape, device, dtype)——dict 取 `x.get("samples")`，裸 tensor 直接用，无 shape 返回 `(None,None,None)`；`_decode_av_latent` 的 split 打印改走 `_latent_meta`。
- **单元测试**（`director/tests/test_latent_meta.py` 新建，5 用例）：dict latent / 裸 tensor / 空 dict / dict 无 samples / None，**5/5 全绿**；vae_residency 7/7 回归全绿；SRC/DEP md5 一致。
- **验证**：`executor_core.py` ast.parse OK；**改 executor_core 需重启 Comfy Desktop**。

### #487 音频 VAE 解码 device mismatch 修复（2026-08-14 用户实测复测 #485）

> 用户重启后复测，解码崩在 `comfy\ldm\minimax\audio_vae.py:423`（`x = self.dec_in_proj(z)` Conv1d）：
> `RuntimeError: Input type (torch.cuda.FloatTensor) and weight type (torch.FloatTensor) should be the same`。
> 用户问「为啥突然坏了，之前还是好的」——**正是 #485 修复引入的**：之前动态 patcher 的 forward 走 aimdo 换页
> （自动把权重搬上 cuda，不报 device 错）；换非动态后，load 与 forward 用了**两个不同的模型对象**。

- **根因**：`comfy.sd.VAE` 结构是 `self.patcher` 与 `self.first_stage_model` 两个引用。`VAE.decode` 里
  `load_models_gpu([self.patcher], force_full_load=self.disable_offload)` 加载的是 **patcher.model**（#485 换上的
  非动态新模型 A，被 load 到 cuda），而 forward 走 `self.first_stage_model.decode(samples)`（仍是**原动态模型 B**，
  权重在 cpu）→ input 在 cuda、weight 在 cpu。视频 VAE 侥幸没崩（此前 encode 路径已把 B 换页过 cuda），
  **音频 VAE 从未被加载过 → 必崩**。
- **修复**（`director/vae_residency.py::ensure_vae_resident`）：换 patcher 后**同步 `vae.first_stage_model = nd.model`**，
  让 load 与 forward 指向**同一模型对象**（主路径 delegate 的 `.model` / 回退路径 `fresh.first_stage_model` 都是新重建的
  ops 干净模型，非 aimdo 挂钩）。`disable_offload=True` 依旧保留。
- **测试**（`director/tests/test_vae_residency.py` 扩到 7 用例全绿）：`_FakeNonDynamicPatcher` 补 `model` 属性；
  各用例新增核心断言 `vae.first_stage_model is vae.patcher.model`（主路径/回退路径/缓存复用三处）+ 重建失败 noop
  时 first_stage_model 保持原引用不变。
- **验证**：`test_vae_residency.py` 7/7 + `test_latent_meta.py` 5/5 全绿；SRC/DEP md5 一致；ast.parse OK。
  **改 executor_core/vae_residency 需重启 Comfy Desktop。**
- **坑**：① 后续动 `ensure_vae_resident` 时必须保持「patcher.model ≡ first_stage_model」不变量（`VAE.decode`
  load 走 patcher、forward 走 first_stage_model）；② 动态 patcher 的 `load_device` 必非 cpu（cpu 会在
  `ModelPatcherDynamic.__new__` 直接重定向成普通 ModelPatcher），回退路径继承 load_device 是安全的；
  ③ 视频 VAE 不崩不代表音频不崩——**两路解码共用同一修复路径，验收必须跑含音频的完整生成**。

### #488-#496 V1.12 种子便捷化（2026-08-14 用户需求「把种子的改变方式弄得便捷一些」）

> 用户拍板（逐字）：「1 要 2 要 3 要 4 要 5 不要 6 要 A1 B3」= ① 🎲 骰子按钮 ✅ ② 种子回显 ✅
> ③ 同种子/换种子重试 ✅ ④ Generation History 记种子 + 以此种子重放 ✅ ⑤ 每镜独立种子 ❌
> ⑥ 全局种子开关 ✅ A1 = 显式「🎲 随机 / 🔒 固定」两态 ✅（B3 因 ⑤ 不做而失效）。
> 设计铁律：**seed 唯一真相 = workflow Director 节点 `inputs.seed`**（-1=随机、0/正数=固定）；
> 不引入独立 localStorage seed 状态（避免与 WorkflowParamsPanel/Workflow Studio 冲突）。

- **core/workflowStudio.ts**：新增 `rollRandomSeed()`（掷 0 ≤ s < 2^31 合法种子，与 `resolveSeed(-1)`
  语义一致）、`readWorkflowSeed(workflow)`（读 Director 节点当前 seed；-1=随机模式；无 Director 节点/null）。
- **WorkbenchView.vue**：
  - `randomizeSeedIfNeeded` 改用 `rollRandomSeed()`（深拷贝副本上把 seed=-1 换成实际随机值，**不污染原 workflow**）。
  - 顶栏新增 `.seed-ctl` 控件：🎲 按钮（掷新种子并固定=换种子重试）、固定模式数字输入框（min=0，**用户无需输入 -1**）、
    「🎲 随机 / 🔒 固定」切换 pill。读写直接走 `activeWorkflow.workflow` 的 Director 节点 `inputs.seed` +
    `persistWorkflowRegistry()` 持久化。
  - **种子回显**：生成提交时 `randomizeSeedIfNeeded` 后 `readWorkflowSeed` 读实际值 → 三个提交点
    （主单镜 / 走查逐镜 / 恢复快照）都经 `createGeneration(shotId, video, { t0, seed })` 写入
    `ShotGenRecord.params.seed` → 详情面板参数 chips 显示「种子」行；`genParamLines` 已加。
  - **同种子/换种子重试**：详情面板 `sd-actions` 新增「🎲 以此种子重放」按钮（仅当当前查看版本有 seed），
    内部守卫 `replayViewedSeed()`（模板不做类型窄化），把该种子写回工作流后重新生成本镜。
  - **走查/恢复链路**：`saveSnap`/`onSubmitted` 携带 seed → `inFlightSnapshot.seed`；恢复快照 onFinish
    用 `snap.seed` 建版本记录，刷新恢复后历史版本种子不丢。
- **models/project.ts**：`ShotGenRecord.params.seed?: number`（随机模式 = 实际掷出的具体值，一键以此重放）。
- **inFlightSnapshot.ts**：`InFlightSnapshotBase.seed?: number`。
- **测试**（workflowStudio.test.ts 扩到 26 用例，新增 6 个）：`rollRandomSeed` 合法区间+两次不同；
  `readWorkflowSeed` 固定值/-1/小数整数化/无 Director→null/非法类型→null。
- **验证**：前端 491 测试全绿 + `npm run build` 通过；SRC/DEP 双目录 md5 一致。
- **坑（How to apply）**：① 模板表达式**不要用 `!` 非空断言**（Vue 模板非 TS 解析），用 computed 守卫法
  （`viewedSeed` + `replayViewedSeed`）；② 数字输入框 type=number 用户输入不了 -1——随机模式的 -1 全部
  由 `toggleSeedMode()` 按钮内部写入，固定输入框 min=0 只接受非负整数；③ seed 控件改的是
  `activeWorkflow.value.workflow` 引用（findDirectorNode 返回引用），写后必须 `persistWorkflowRegistry()`
  才持久化；④ 纯前端改动，刷新即生效，**无需重启 Comfy Desktop**。

### #497-#502 A1 Prompt 重复污染 + A2 生成后自动保存（2026-08-14）

> 来源：用户实机 Shot 01（《山雨客栈》P0-A 验收）seg_0000.meta.json 提交文本发现四类重复污染
> （原文×2 / 「镜头：」×2 / 「静谧孤寂」×3 / 双句号），且 project.json 未随生成自动落盘。
> 用户拍板：先修 A1 → 再修 A2 → 回归 → Shot 01 标准验收 → 9 镜实拍。**只做这两项，不顺手重构**
> Prompt Builder / Scene Manager / CLIP / Shot-to-Shot。

**A1 Prompt 重复污染（h3_prompt_builder.py，四修）**
- **A1-1 原文事实重复**：`_strip_fact_duplicates(comp, facts, joined)` 剥离 composition（Workbench visual
  覆盖）内与 retained_facts 逐字重叠的原文——先整段 joined（汉字边界正则 `(?<![一-鿿])…(?![一-鿿])`
  防「山雨楼」误伤「山雨楼外」），再逐 fact（len≥3、按长到短）剥离，最后清理连续/首尾标点。
  剥离后为空 → 不再追加（facts 区已覆盖）。
- **A1-2 「镜头：」双前缀**：`_strip_camera_prefix` 对 continuity.camera_desc / camera_position /
  movement 统一剥自带前缀，渲染侧只拼一次「镜头：」。
- **A1-3 「静谧孤寂」三重复**：emotion 追加「氛围{emo}」前增加 `not _contains_noun(comp, emo)` 检查
  （用户把情绪词写进 visual 后不再重复渲染）。
- **A1-4 双句号「。。」**：style/lighting 渲染前检查结尾已在 `。？！` 则不重复补句号。
- **回归测试**（test_h3_prompt_builder.py 扩到 45 用例，新增 14 断言）：`test_a1_prompt_dedup_regression`
  复现 seg_0000 四类污染场景断言「原文恰一次/镜头前缀恰一次/情绪词不重复/无双句号」；
  `test_a1_fact_strip_boundary` 验证短 fact 不误伤更长词。后端 26 测试文件全 PASS。

**A2 生成成功后自动保存（WorkbenchView.vue，纯前端）**
- `createGeneration` 完成回调末尾追加 `void saveCurrentProject(true)`：生成成功后自动把 Generation
  版本记录 + Workbench 修改落盘 project.json，刷新/重开项目后记录仍在，不再依赖用户手动点 💾。
- `saveCurrentProject` 新增 `silent` 参数（silent=true 不弹「✓ 已保存」提示气泡，仅静默落盘）。
- **回归测试**（workbenchRender.test.ts 扩到 105 用例）：A2 用例模拟单镜生成，断言 ① saveProject
  自动被调用 ② payload 含该镜 generations[1] + activeGenerationId（首版自动采纳）③ serialize→
  deserialize→loadProject 重载后版本与 current_generation_id 保留。
- **验证**：前端 492 测试全绿 + `npm run build` 通过（期间修一处类型错误：saveCurrentProject 加了
  可选参数后模板 `@click` 必须显式 `saveCurrentProject()`，否则 Vue 把 PointerEvent 当 silent 传参
  报 TS2345）；后端 26 文件全 PASS；SRC/DEP 双目录 md5 一致。
- **坑（How to apply）**：`saveCurrentProject(silent=false)` 这类带可选参数的方法在模板里被
  `@click="fn"` 引用时，事件对象会被当作第一个参数传入导致类型不匹配——**模板必须写 `@click="fn()"`**；
  给 store 方法加默认参数同样要注意调用点（组件内直接调用 `fn()` 不受影响）。

### #506 批量逐镜生成第二次卡死根因修复（2026-08-14）

> 来源：用户实机批量跑《山雨客栈》12 镜，第二次卡在 Shot 09 之后（Shot 10 未启动），
> 无报错、无 interrupt，前端任务中心永久「running」。Shot 08/09 后端已 `Prompt executed`
> 但无归档、无 generations 记录。
>
> **根因（跨镜竞态）**：走查批量逐镜共用同一 `DirectorRunService` 实例。上一镜的
> 兜底轮询（`tickFallbackPolling` await `/history` 挂起中）或晚到 WS 事件会在下一镜
> `run()` 重置 `settled=false` 之后继续执行：
> - `settleRun` 原来**无条件置共享 `this.settled = true`**，未校验 `promptId`；
> - 于是上一镜 stale settle 把当前镜的 `settled` 置 true → 当前镜真实
>   `execution_success` 被 `if (this.settled) return` 吞掉 → `onFinish` 永不触发 →
>   `runOneSequentialShot` 的 Promise 永不 resolve → 批量永久卡死。
> - WorkbenchView 的 `expectedPid` 陈旧保护只吞掉回调，无法撤销已污染的 `settled` 标志。

**修复（`frontend/src/services/directorRun.ts`，纯前端，V1.11.4）**
1. **`settleRun` 加 promptId 守卫**（核心）：`if (promptId !== this.pendingPromptId) return;`
   只允许当前 pending prompt 收尾，陈旧收尾不再触碰共享 `settled` 标志。
2. **`case "executed"` 按 prompt_id 过滤**：上一镜晚到的 executed 不得污染当前镜 lastFv
   （否则错误地拿旧成片归档当前镜）。
3. **`execution_success` 改走 `settleWithVideoBackfill`**：先 `await backfillVideoFromHistory`
   再 settle——executed 事件丢失时 onFinish 触发前 lastFv 已就绪，归档不丢版本（修 Shot 08/09 无归档伴生症状）。
4. **`backfillVideoFromHistory` 加 promptId 守卫**：上一镜在途回填不得在下一镜 run() 之后
   把旧 URL 喂给当前镜 onVideo。
5. **`tickFallbackPolling` 两处 `await historyById` 后补重查** `this.settled || promptId !== this.pendingPromptId`：
   在途 tick 不得在 await 后继续收尾/上报旧状态（belt-and-suspenders，settleRun 守卫已兜底）。

- **回归测试**：新增 `frontend/tests/directorRunStale.test.ts`（3 用例）——
  ① 跨镜核心：上一镜在途 tick 的 stale settle 不吞当前镜收尾；
  ② 上一镜晚到 executed 不污染当前镜 lastFv；
  ③ execution_success 先 await 回填再 settle（onVideo 调用序早于 onFinish）。
  既有 `directorRunFallback.test.ts` 首用例补 `await vi.advanceTimersByTimeAsync(0)`（收尾改异步）。
- **验证**：前端 495 测试全绿（+3）+ `npm run build` 通过；SRC/DEP 双目录 md5 一致。
- **坑（How to apply）**：跨镜复用的服务状态（`settled`/`pendingPromptId`/`pendingCallbacks`）必须
  以 prompt_id 为界做守卫，**任何 async 方法在 await 之后都要重查当前 prompt 是否仍是自己**；
  纯前端改动，刷新即生效，**无需重启 Comfy Desktop**（但 SPA 需重新加载前端资源）。

### #513-#521 P0 Story Timeline 全链路：双来源连续性 + 前端时间线 UI（2026-08-14，用户拍板「小说→导演理解→剧情节拍→场景调度→分镜→H3」）

> **用户核心需求**（逐字保留）：漫剧实际呈现一定是不同场景之间反复切换来推动剧情；
> Scene 是「空间/资产容器」，Timeline 是「播放顺序」；同一 Scene 可在一章内多次引用，
> 不同 Scene 按剧情交叉剪辑（如 Chapter 37 大堂/后院交叉成 8 镜）。
> **P0 硬性要求**：连续性必须双来源合成 —— `Timeline Previous = 叙事前驱` +
> `Scene Previous = 场景空间状态前驱`（例：01大堂→02后院→03大堂，Shot 03 叙事前驱=Shot 02 后院，
> 但大堂空间状态前驱=Shot 01 大堂）。**不要让 Qwen 决定一切**（三层分级 🟢原文事实 / 🟣AI 推断 / 🔵导演规则）。

**后端（P0-1~P0-4，改 `plan.py`/`gen_timeline.py`/`camera_template.py` 需重启 Comfy Desktop）**
1. **`ProductionPlan.timeline` 播放顺序**：`["scene_id:shot_id", ...]` 显式交叉剪辑条目；缺省=场景树顺序（严格向后兼容）。
2. **双前驱连续性**：`DirectorIntent.continuity` 同时携带 `timeline_prev`（按 timeline 上一镜）+ `scene_prev`（按场景空间上一镜）；
   场景切换时两者可不同，空间状态（角色/灯光/道具）以 `scene_prev` 为准，叙事衔接以 `timeline_prev` 为准。
3. **Scene Last State**：`build_plan_intents` 维护每场景 last_state，切换回旧场景时恢复其空间状态。
4. **运镜状态机**：`camera_template` 按 timeline 传递 prev_state，保证切场景后运镜仍有连续性。
5. **后端测试**：`test_shot_continuity.py` 交叉剪辑双前驱用例全绿。

**前端（P0-5~P0-8，纯前端，刷新即生效）**
1. **`Episode.timeline`**（`models/project.ts`）：存前端全局 `Shot.id`（跨场景唯一）；`Shot.planShotId` 映射后端 `scene_id:shot_id`。
2. **导入透传**（`productionPlanToProject.ts`）：`buildEpisodeTimeline` 把 plan.timeline → 前端全局 Shot.id，
   显式条目优先 + 未覆盖镜头按场景树顺序补齐（timeline 完全物化不悬空）；plan.timeline 缺省/空 → 不写 Episode.timeline 字段（向后兼容）。
3. **拍平**（`directorCore.ts`）：`resolveFlattenedShots(episode)` 已导出——有 timeline 按显式顺序 + 未覆盖补齐；
   无 timeline 退化场景树顺序。**UI 显示与生成执行共用此函数**（Workbench Storyboard 板块同序渲染）。
4. **store**（`workbench.ts`）：`ensureTimeline` 懒初始化（首次拖拽才按场景树顺序生成）；
   `reorderTimeline(shotId, targetIndex)` 拖拽排序（targetIndex 钳制、同位置返回 false 不 touch）；
   `addShot`/`removeShot`/`duplicateShot`/`removeScene` 全部维护 timeline 集合一致性（新镜插入/删镜移除/副本紧跟原镜/删场景清镜）。
5. **时间线 UI**（`WorkbenchView.vue`）：底部 Storyboard 顶部新增「🎞 全片播放顺序」时间线条，
   场景色条 + 场景名标签 + 编号 + HTML5 拖拽排序；点击时间线卡 `selectShotAndPlay` 选中并播放。
6. **跨场景点击联动修复（P0-7 核心 bug）**：`selectShot` 现同步 `currentSceneId` 到镜头所属场景——
   否则 `getCurrentShot()` 只在当前场景内 find，跨场景点击会回退当前场景首镜（详情区/主播放器显示错镜）。
7. **前端测试**：新增 `tests/storyTimeline.test.ts`（20 用例：拍平 6 + 导入透传 5 + reorderTimeline 5 + 场景树一致性 4）；
   **前端 515 测试全绿** + `npm run build` 通过；SRC/DEP 双目录同步 + 77 文件 md5 一致。

- **坑（How to apply）**：① timeline 是**播放顺序唯一权威源**，场景树操作只维护「集合一致」，只有 reorderTimeline 改播放顺序；
  ② `buildEpisodeTimeline` 会完全物化 timeline（显式 + 补齐），部分 timeline 的缺口由 resolveFlattenedShots 拍平时补齐，不是 bug；
  ③ `resolveFlattenedShots` 已导出，**任何新增「按播放顺序遍历镜头」的逻辑都必须用它**，别再自己写场景树遍历；
  ④ `selectShot` 必须同步 currentSceneId（见上），否则跨场景选中错乱。

### #525 P1-A-5 Timeline 主视图 + 地点/人物筛选（2026-08-14，纯前端）

> **P1-A 用户拍板方向（逐字保留）**：UI 主视图最小侵入——Timeline 成为镜头主要导航与播放顺序入口；
> 用户入口从「地点→Shot」调整为「🎬 Story Timeline 01→02→…→30」+「📍 地点库」；
> Scene 产品概念逐步降级为 Location（世界中持续存在的空间/环境/资产容器，**不决定播放顺序**）；
> 旧项目保留 `sceneId` 映射兼容 `locationId`，无需重新导入；Timeline 是 Shot 的唯一播放顺序与主要导航权威源。

**改动（纯前端 `WorkbenchView.vue`，刷新即生效，无需重启 Comfy Desktop）**
1. **时间线主视图**：中间主区（`wb-center`）顶部新增 `tl-overview` 概览条（🎞 全片播放顺序 +
   `N 镜 · M 地点 · K 人物 · 共 mm:ss` + 引导文案），其下 `timeline` 卡片列表从旧底部位置整体上移为主视图；
   点击卡片 `selectShotAndPlay` 选中并播放（沿用 P0 交互）。
2. **地点/人物筛选**：概览条右缘两个下拉（📍 全部地点 / 👤 全部人物）+「✕ 清除筛选」；
   `tlLocOptions` 只列「有镜头的场景」；`tlCastOptions` 取时间线各镜 cast 名称并集
   （`resolveInheritance` 解析，localeCompare("zh") 排序）；`filteredTimelineShots` 按 `s.sceneId` +
   cast 名包含做交集过滤，空态给明确引导。
3. **拖拽重排不受筛选影响**：`onTlDrop` 用完整 `timelineShots.value.findIndex(...)`（非 filtered），
   筛选态下重排仍落在正确全局索引。
4. **布局**：`.wb-center { column }`，timeline 顶部固定（flex:0 0 auto），播放器占剩余；
   旧 `.tl-head`/`.tl-head-title` 已删。

**测试**：`tests/workbenchRender.test.ts` 新增「P1-A-5 Timeline 主视图 + 地点/人物筛选」describe（5 用例）——
概览统计与卡片顺序、地点筛选+清除、人物筛选（选项含林雪/陈默）、联合筛选交集+空态、无 cast 时人物下拉 disabled。
验证：前端测试全绿 + `npm run build` 通过；SRC/DEP 双目录同步。

- **坑（How to apply）**：① 任何新增「按播放顺序遍历镜头」逻辑必须用 `resolveFlattenedShots`；
  ② 筛选仅影响渲染，`onTlDrop` 的 findIndex 必须查完整 timeline，否则筛选态下重排错位。

### #530 P1-B-1 小说章节理解：StoryBeat Schema + Qwen 整章理解（2026-08-14，任务 #530）

> **上游**：P1B_STORY_ANALYZER_PLAN.md（用户 2026-08-14 拍板）+ 3 个锁死实现细节
> （target 是推荐区间非硬性 / Shot Blueprint 是候选骨架 / source_text 原文归属 Beat）。
> **边界**：B-1 只做「整章理解 → StoryBeat 列表」。**不碰 Shot**（P1-B-3）、
> **不做 Beat 数量约束/合并**（P1-B-2）、**不动工作台**（拍板③）。执行顺序 P1-B-1→B-5。

**改动**
1. `director/production_plan.py`：新增 `StoryBeat` dataclass（12 字段：beat_id/title/summary/
   dramatic_function/scene_id/time/weather/**text_segments**/order/entries/source/transition_reason），
   `to_dict`/`from_dict` 全支持；`ProductionPlan.beats`（缺省空列表，老数据严格向后兼容）。
   `from_dict` 防御加固：`text_segments`/`entries` 非 list、`order` 非法 → 默认值不崩
   （新增 `_as_int_defensive` helper）。
2. `director/story_analyzer.py`（新文件）：`DRAMATIC_FUNCTIONS`（9 个剧情功能常量）、
   `_STORY_ANALYZE_TEMPLATE`（Qwen 整章理解强 JSON prompt：title/summary/dramatic_function/
   location/time/weather/text_segments/characters/props/dialogue/emotion/transition_reason +
   覆盖铁律 + 只读不创造）、`extract_beats(text, backend, *, title, max_tokens, temperature)`
   → `(List[StoryBeat], rule_only, warnings)`；`_normalize_beats_json`（dramatic_function 未知值
   归一为 transition + warning；覆盖缺口只记 warning，规则补齐归 B-2）；`_coverage_gap`
   （去空白包含 + 双指针缺口定位）；`_rule_fallback_beats`（退化路径按段拆，source="rule"）；
   `_rule_dramatic_function`（关键词启发式）。**Qwen 只做剧情理解，不决定播放顺序/镜头边界**
   （P0 铁律）。退化路径（拍板②）：Qwen 全挂/analyze=False → 纯规则 + warnings 明示
   「未经过 AI 剧情理解，需人工检查」，不阻塞。
3. 测试：`director/tests/test_plan_beats.py`（42 断言：round-trip/老数据兼容/防御/dramatic_function
   校验）+ `director/tests/test_story_analyzer.py`（41 断言：AI 正常路径/归一/三种退化/覆盖缺口/
   空正文/_coverage_gap 纯函数/_rule_dramatic_function/session close 恰 1 次）。
   `test_script_parser.py`「顶层字段齐全」断言同步加 `beats`（Schema 扩展预期影响，非回归）。

**验证**：两个新测试全绿；后端 29 个测试文件全量 0 失败；`py_compile` 通过；
SRC/DEP 双目录同步 md5 一致；DEP 副本新测试同样全绿。纯后端零前端改动，**无需重启 Comfy Desktop**。

- **坑（How to apply）**：① `ProductionPlan.to_dict()` 顶层 key 现在是
  `{project, scenes, validation, timeline, beats}`，任何断言精确 key 集合的测试都要带 `beats`；
  ② `extract_beats` 的 `_coverage_gap` 只是去空白近似检查，正式「Shot.source_text 拼接 → 完整覆盖
  Beat.text_segments → 完整覆盖小说正文」覆盖链在 P1-B-3 的 `test_story_analyzer_shots.py` 测死；
  ③ B-1 故意把「覆盖缺口补齐」和「8-16 数量约束」留到 P1-B-2，别提前越界实现。

### #531 P1-B-2 Beat → Timeline：动态数量约束 + 超限合并不截断 + 覆盖补齐 + 地点规范化（2026-08-14，任务 #531）

> **上游**：P1B_STORY_ANALYZER_PLAN.md §5/§7 + 用户拍板①（target 8-16 是**推荐区间非硬性**，
> 真实节拍数 < 8 不硬拆）+ 锁死实现细节 3（source_text 原文归属 Beat，覆盖链 P1-B-3 测死）。
> **边界**：只做「Beat → Timeline（纯规则）」，**不碰 Shot**（P1-B-3 Shot Blueprint）。
> 执行顺序 P1-B-1→B-5 不变，未提前动工作台。

**改动**（全部在 `director/story_analyzer.py`，纯规则零 LLM）
1. **动态数量约束**：`BEAT_TARGET_MIN=8`/`BEAT_TARGET_MAX=16`/`BEAT_WORDS_PER_BEAT=700`，
   `target_beat_count(text) = clamp(round(len/700), 8, 16)`（空文本 → 0）。仅作合并上界，
   超限才触发合并，**不凑数**。
2. **超限合并不截断**：`_merge_beats_to_target`——len>target 时 while 合并相邻对，优先
   低信息（transition/短 summary）相邻对 > 同 scene_id 相邻对 > 拼接最短对；合并 =
   `_merge_adjacent_beats`（text_segments/entries 拼接不丢原文；dramatic_function 取更具体
   `_DRAMATIC_PRIORITY`：confrontation > dialogue > action > reveal > emotional >
   introduce_threat > plant_clue > introduce_character > transition）；每次合并记 warning。
   合并后仍超 → 继续；**绝不截断/丢弃任何 text_segment**。
3. **覆盖补齐**：`_fill_coverage_gaps`——缺口段落（去空白后不被任何 text_segment 包含）
   追加到「缺口前最近已覆盖段落的 Beat」（首段缺口 → 第 0 个 Beat）+ warning。
   ⚠ **深拷贝修复**：`dataclasses.replace(b, text_segments=list(b.text_segments))`，
   原浅拷贝会污染入参 beats 的 text_segments（process_beats 承诺不改入参）。
4. **地点规范化**：`_normalize_location_ids`——用 `entity_cleanse.normalize_entity_name`
   （去空白+小写）归一，同名合并同一 `scene_id=location_XX`；返回 `(beats, loc_map)`
   （loc_map: scene_id → 规范地名，供 B-3 建 Scene/审核展示）；无地点 → ""。
5. **Timeline**：`beat_timeline(beats)` 按 (order, beat_id) 升序 → `["{scene_id}:{beat_id}"]`，
   **Beat 顺序权威**（Qwen 不决定播放顺序）；B-2 用 beat_id 占位，B-3 展开为 shot_id。
6. **编排**：`process_beats(text, beats_meta, warnings) → (beats, loc_map)` = 超限合并 →
   覆盖补齐 → 地点规范化；`work` 用 `dataclasses.replace` 复制，不改 beats_meta 入参。
7. 测试：`director/tests/test_story_analyzer_timeline.py`（16 函数，含「入参未被污染（深拷贝修复）」
   断言锁死；退化路径集成：extract_beats(None) → process_beats 覆盖完整 + 4 段不硬凑）。

**验证**：新测试全绿；后端全量回归 + `py_compile` 通过；SRC/DEP 双目录同步一致。
纯后端零前端改动，**无需重启 Comfy Desktop**（story_analyzer 不在 http_routes 热更链路）。

- **坑（How to apply）**：① `_merge_beats_to_target`/`_fill_coverage_gaps`/`_normalize_location_ids`
  全都不改入参（复制/深拷贝 text_segments），后续在它们之上叠 Shot 拆分时同样遵守；
  ② `beat_timeline` 条目是 `scene_id:beat_id` 占位，P1-B-3 拆 Shot 后必须换 `scene_id:shot_id`，
  别把 beat 条目当最终播放顺序落盘；③ 合并取 dramatic_function「更具体」用 `_dramatic_priority`，
  新增 dramatic_function 值必须同步优先级表，否则未知值归末尾。

### #532 P1-B-3 Beat → Shot：dramatic_function Shot Blueprint + Shot 独立 Location + 覆盖链（2026-08-14，任务 #532）

> **上游**：P1B_STORY_ANALYZER_PLAN.md §6/§7 + 用户拍板②③（Shot Blueprint 是**候选骨架/最小导演模板**，
> 按 Beat 内容特征决定启用哪些模板，非一句话一个镜头；原文归属 Beat，Shot 是对 Beat 原文的导演切分引用，覆盖链测死）。
> **边界**：只做「Beat → Shot（纯规则零 LLM）」，导演意图写入 visual_intent 由 P1-B-4 投影到 DirectorIntent；
> 不建前端、不加路由（P1-B-5）。执行顺序 P1-B-1→B-5 不变。

**改动**（全部在 `director/story_analyzer.py` + 新测试，纯规则零 LLM）
1. **§6.1 Shot Blueprint 模板表**：`SHOT_BLUEPRINTS`——9 个 dramatic_function → 导演候选镜头序列
   （shot_type/camera_position/movement/intent，部分带 `optional: True`）；复用常量模板
   `_ESTABLISHING_TEMPLATE`/`_INSERT_TEMPLATE`/`_CLOSEUP_TEMPLATE`/`_REACTION_TEMPLATE`。
2. **§6.2 特征启用**：`_shot_features`（func/len/short<60/has_dialogue(「/：)/has_action/has_prop_clue/
   location_change(≥2 已知地点)/emotion_shift）+ `_select_template_subsets` 6 步规则——optional 默认去
   除非 has_prop_clue；dialogue 无对白降级 establishing+reaction；短 Beat 只取前 1-2（<40 字 1、40-59 字 2）；
   has_prop_clue 追加 insert；location_change 前置 establishing；emotion_shift 追加 closeup 慢推。
3. **§6.3 Shot 独立 Location**：`resolve_shot_location` 三步兜底（shot 文本首个已知地点 → 继承上一 shot →
   beat.scene_id）；`_first_location_in_text` 长名优先；无地点 → 兜底 `location_00`「未命名地点」
   （绝不污染已确认 Location）。
4. **§6.4 拆句不硬拆**：`_split_segments_into_chunks` 按句号/问号/感叹号/分号切句，贪心分组
   `n = max(1, min(n, len(parts)))`，chunks 拼接 = 原文（覆盖链保证），不逐句切镜（6 句对话仍 3 镜）。
5. **§6.5 覆盖链**：`shot_coverage_check`——所有 Shot.source_text 按 **timeline 播放顺序** 拼接
   （保序去重）完整覆盖 Beat.text_segments 拼接 → 完整覆盖小说正文；缺口 → warning。
   ⚠ **关键修复**：跨地点交叉剪辑时 scene 分组 ≠ 播放顺序，覆盖链必须按 timeline 展平
   （timeline=顺序权威，P0 铁律），否则「回到旧场景」的镜头会误报缺口。
6. **§7 编排**：`build_plan_from_beats(text, beats_meta, warnings) → ProductionPlan`
   ——复用 process_beats（合并→补齐→地点规范化，**不改入参**），逐 Beat 拆 Shot → resolve 独立 Location →
   scene 聚合 → timeline 逐 Beat 逐 shot（`scene_id:shot_id`）→ beats.entries 记录 → 覆盖校验。
   导演意图写入 `shot.visual_intent`（格式 `镜头类型=X；景别=X；运镜=X；作用=X`），
   P1-B-4 的 `build_shot_intent` 会把 visual_intent 投影为 DirectorIntent.composition（贯通点）。
7. 测试：`director/tests/test_story_analyzer_shots.py`（19 函数：模板表/特征启用/三步兜底/拆句还原/
   覆盖链单 Beat+多 Beat+去重/跨地点交叉剪辑+同名合并/不流水账/退化路径集成/无地点兜底/不改入参）。

**验证**：新测试全绿；后端全量回归 + `py_compile` 通过；SRC/DEP 双目录同步一致（含 story_analyzer.py +
  测试文件）。纯后端零前端改动，**无需重启 Comfy Desktop**。⚠ VM bash 卡死，md5/实跑验证移交用户本机。

- **坑（How to apply）**：① 覆盖链展平必须走 timeline（scene 分组仅存储，跨地点交叉剪辑会打乱顺序）；
  ② `build_plan_from_beats` 的 `plan.validate()` 会覆盖 `self.validation`，导入 warnings 必须**无条件合并**
  （`list(warnings) + cov + plan.validation.warnings`），不能只在 cov 非空时合并；③ 测试地点名必须与
  loc_map 全称一致（「大堂」≠「山雨楼大堂」），且台词提及别的地点会让镜头落该地点（合理启发式，测试要规避）；
  ④ 短文本（<60 字）只出 1-2 镜，断言镜头数的测试必须用 ≥60 字文本。

### #533 P1-B-4 Shot → DirectorIntent：端到端接线回归（复用 build_plan_intents + camera_template，零实现改动）（2026-08-14，任务 #533）

> **上游**：P1B_STORY_ANALYZER_PLAN.md §8 行 332（用户拍板：「复用已验收 build_plan_intents（USER→RULE→AI）+
> camera_template 运镜；**不新架构**，只接线 + 回归｜无新文件（接线验证）｜回归：build_plan_intents 对 story 产出的 plan 全通过」）。
> **边界**：只做「Shot → DirectorIntent」接线回归，**零实现改动**（build_plan_intents 直接可吃 B-3 的 ProductionPlan）；
> 不建前端、不加路由（P1-B-5）。执行顺序 P1-B-1→B-5 不变。

**改动**（全部为新增测试 `director/tests/test_story_analyzer_intents.py`，8 个测试函数，零 LLM 零 GPU）
1. **[1] 贯通点 visual_intent→composition**：B-3 写入 shot.visual_intent（`镜头类型=X；景别=X；运镜=X；作用=X`）
   由 build_shot_intent 逐字投影为 DirectorIntent.composition（蓝图层）；原文逐字 user_original_intent；
   audio.ambient=雨声淅沥（weather 贯通）；intent 键 == timeline 条目。
2. **[2] 运镜接线**：pick_camera 按 timeline 传 prev_state → camera_position/movement/camera_desc/camera_template
   四字段全非空（B-3 shot 无 cast 也吃 establish 兜底）；首镜 has_prev=False、后续 True；尾镜 is_last_shot=True。
3. **[3] 纯环境镜 establish 兜底**：无 cast 无实体 → camera_position=全景/movement=缓摇（establish wide/pan）、
   composition 含 B-3 远景拉远、subject 回退场景名「山雨楼外」、characters 为空。
4. **[4] 双前驱连续性（跨地点交叉剪辑）**：大堂→后院→大堂：后院首镜 scene_cut（timeline_prev_shot_id=大堂前驱、
   无 scene_prev_state）；切回大堂 cross_cut（timeline_prev_shot_id=后院前驱 + scene_prev_shot_id=大堂首镜 +
   scene_prev_state.location=山雨楼大堂）；timeline 首镜无 transition_type。
5. **[5] 退化路径全链路**：extract_beats(None)→plan→intents，user_original_intent 按 timeline 拼接 == 原文
   （`_core` 逐字贯穿）；rule_only=True 不阻塞。
6. **[6] 实体边界**：B-3 shot 不产 entities → intent.entities 为空（char_anchors 过滤安全）；subject 非空。
7. **[7] overrides_by_shot 兼容**：P0-A 五区 overrides 仍工作——visual→composition（provenance.composition=USER）、
   cameraText→camera_desc；**原文事实不可覆盖**（user_original_intent 不变）；未覆盖镜不受影响。
8. **[8] 不 mutate plan**：build_plan_intents 后 shot.source_text/timeline 未变；覆盖链仍完整。

**验证**：新测试 8 函数全绿；配合 `test_story_analyzer_shots.py`（B-3，19 函数）+ `test_story_analyzer_timeline.py`（B-2）+
  `test_story_analyzer.py`（B-1）+ 后端全量回归 + `py_compile`；SRC/DEP 双目录同步一致（含新测试文件）。
  纯后端零前端改动，**无需重启 Comfy Desktop**。

**#533 修复：DEP 双目录同步（2026-08-15，用户本机实测 6+1 失败根因）**
用户重跑发现 `test_story_analyzer_intents.py` [4] 双前驱 6 断言失败 + `test_script_parser.py`
「缺省 timeline = 场景树顺序」1 断言失败。根因实锤：**DEP 部署目录停在 P0 收官前**（P0 #513-#521
声称「已同步 DEP」但实际未同步）。已从 SRC 全文件覆盖同步 6 文件到 DEP：`director_intent.py`
（632 行，补 scene_cut/cross_cut/scene_last/timeline_prev_shot_id/scene_prev_state）、`script_parser.py`
（820 行，补 `default_timeline`）、`camera_template.py`（488 行，补 `scene_has_prev` + `build_plan_cameras`
按播放顺序传态）、`h3_prompt_builder.py`（530 行，补 scene_cut/cross_cut 过渡词）、`test_shot_continuity.py`
（320 行）、`test_timeline.py`（223 行，DEP 缺失补齐新建）。修复后用户重跑全量回归**全部通过**
（intents 62 通过/0 失败、script_parser 75 PASS/0 FAIL、timeline 23、shot_continuity 44、director_intent 21、
camera_template 67，后端其余测试文件全绿）；`director/` 全目录 md5 扫描 DIFF=0/MISSING=0/EXTRA=0，
6 个同步文件字节级一致（2 个 Read/Write 行尾差异文件经 bash cp 精确对齐）。**P1-B-4 正式收官**。
⚠ 教训：P0 后端验收时「已同步 DEP」表述与实际落盘不符，后端代码同步必须 md5 校验，不能只看声明。

- **坑（How to apply）**：① **composition 层（B-3 蓝图层 visual_intent）与 camera 层（规则状态机层
  camera_position/movement）语义分离是设计意图**，B-4 不合并——两者可能不同（如 composition 远景/拉远 vs
  camera 全景/缓摇），断言别强制相等；② B-3 shot 的 characters/dialogue **非空**（script_parser 抽取），
  仅 entities/actions/emotion 为空——测试对 subject 断言而非 characters 是否为空；③ `_shot_text` 拼接
  visual_intent 使 B-3 蓝图的「特写」等关键词会被运镜状态机消费，B-3→B-4 运镜语义天然连贯；
  ④ 跨地点交叉剪辑断言必须用「同场景出现两次」文本（大堂→后院→大堂），否则 scene_cut/cross_cut 不触发；
  ⑤ 全部 15 相机模板 + default 的 shot_size/movement 都在 `_SHOT_SIZE_CN`/`_MOVEMENT_CN` 映射内，
  `_camera_cn` 恒非空——后续新增模板必须同步映射表，否则非空断言挂。

### #534 P1-B-5 小说章节导入路由 + 前端「📖 小说章节」页签 + 收尾（2026-08-15，任务 #534）

> **上游**：P1B_STORY_ANALYZER_PLAN.md §8（用户拍板：「P1-B-5：POST /story/import 路由 + 前端导入弹窗
> 「📖 小说章节」页签（Beat + dramatic_function 审核预览）+ 收尾（回归/build/双目录同步/CHANGELOG/memory）」）。
> **P1-B 最后一段**：此后 P1-B 五段全部收官，P2 Global Story Bible（跨章状态）待后续。
> ⛔ **http_routes.py 改动 → 需重启 Comfy Desktop 生效**。

**改动**
1. **后端路由** `director/http_routes.py`：新增 `POST /minimax/director/story/import`
   （`minimax_director_story_import`，line 756）。body `{story_text, title, source_file, analyze}`：
   小说正文（自然语言，零标记）→ `create_default_text_backend`（真实 Ollama 单 session，失败不阻塞）
   → `extract_beats`（B-1 Qwen 整章理解，asyncio.to_thread）→ `build_plan_from_beats`（B-2/B-3 规则编排）
   → 返回 `ProductionPlan.to_dict()`（含 beats + timeline）+ `rule_only` + `warnings`，与 `/script/import` 同构。
   空正文 400；编排异常 500；后端不可用自动降级纯规则 + warnings。
2. **后端测试** `director/tests/test_story_import.py`（新增，7 函数，零 LLM 零 GPU）：空正文/缺字段 400、
   rule_only 降级、plan 结构（scenes/shots/timeline/beats 齐全）、title 透传、analyze=False 跳过后端。
3. **前端 API** `frontend/src/services/comfyApi.ts`：`storyImport()`（POST `/minimax/director/story/import`）
   + `StoryImportResult` 接口（`plan`/`rule_only`/`warnings`）。
4. **前端 UI** `frontend/src/workbench/WorkbenchView.vue`：home 页新增「📖 导入小说」按钮；
   导入弹窗双页签「📜 剧本 / 📖 小说章节」（状态互不干扰）；小说页签 = 正文输入区
   （粘贴或选 .md/.txt 文件）+ 提交 → Beats 审核预览（dramatic_function 分组卡片 + 地点列表 + 播放顺序 + 警告）
   → 应用到 Workbench。复用剧本导入审核预览骨架（formal 流程不碰内部 JSON）。
5. **前端测试** `frontend/tests/storyImport.test.ts`（新增，6 测试）：页签切换、输入区绑定、
   storyImport 请求组装、Beats 预览渲染、应用到项目、错误横幅。

**#534 修复：workbenchRender.test.ts 6 个预存失败**（排查中发现，与 P1-B-5 无关但阻塞全量回归）
- 根因①（P0-2 断言漏改）：`P1-A-3 场景→地点` 改名后 line 315 仍断言「↑ 场景默认」，
  实际渲染「↑ 地点默认」→ 改断言。
- 根因②（P1-A-5 mountTl 缺陷）：`mountTl()` 只 `loadProject` + `mount`，view 初始 `"home"`，
  `loadProject` 不切 view → timeline 主视图（`<template v-else>`）不渲染 → 5 个 P1-A-5 测试全空 DOM。
  改：先 `enterWorkbench`（点「载入示例工程」）再 `loadProject(project)`。
- 根因③（fixture 缺 `castManual`）：`tlProject()` 的 shot 只填 `castIds` 不设 `castManual: true`，
  `normalizeShotInheritance` 按契约「castIds 有值 ⟺ manual=true」把 castIds 清空 → 陈默解析丢失、
  人物统计只剩 1、筛选空。改：fixture `mk()` 补 `castManual: true`（真实导入/UI 原子操作均带 manual）。

**验证**
- 前端：526 测试全绿（416 + workbenchRender 110，e2e.verify 1 skipped 既有）+ `npm run build` 通过。
- 后端：story 模块 67 全绿（test_story_import 7 + test_story_analyzer_timeline 16 + test_story_analyzer_shots 19
  + test_story_analyzer 12 + test_story_analyzer_intents 8 + test_script_import 5）。
- ⚠ VM 后端全量 37 失败为**预存环境失败**（test_export_route 8 / test_segment_cache_stale 7 /
  test_generation_archive_route 5 / test_asset_shot_boundary 5 / test_segment_cache_identity 4 /
  test_core_sampling_interrupt 4 / test_prompt_builder 2 / test_text_backends 1 / test_asset_registry 1；
  导出路由/缓存/采样中断/文本后端/资产注册表模块，与 P1-B-5 story/import 零交集，VM 缺
  fastapi/comfy/模型目录导致）→ **用户本机需跑全量回归验收**（见汇报终端指令）。
- 双目录同步：SRC→DEP 覆盖 http_routes.py + test_story_import.py + 前端 6 src + 5 test + dist；
  `director/` md5 DIFF=0、`frontend/src+tests` md5 DIFF=0 ✓。

- **坑（How to apply）**：① `/story/import` 返回 plan 结构与 `/script/import` 同构（都含 scenes/shots/
  timeline/beats），前端可复用同一审核预览骨架——新增导入源只需改「正文→Beats」一段；② Qwen 后端
  创建失败是**软失败**（rule_only=True + warnings），路由绝不 500 阻断正式流程；③ story_analyzer
  的 `extract_beats` 是 to_thread 阻塞调用，路由层必须 `asyncio.to_thread` 包裹，否则卡事件循环；
  ④ 测试 fixture 构造显式 castIds 必须带 `castManual: true`（`normalizeShotInheritance` 会清掉
  manual 假的残留，这是 UI 原子操作与导入通道的共同契约）；⑤ view 是 WorkbenchView **组件内** ref，
  store `loadProject` 不切视图——测试若需 timeline 主视图，必须先触发进入 workbench 的 handler。

### #538 P2-P1 Global Story Bible：数据模型 + 持久化层（2026-08-15，任务 #538）

- **背景**：P1-B 完成单章导入，但每章独立理解、跨章不连续（第 5 章不知道沈青崖是谁/剑匣是第 2 章线索）。P2 加项目级 Global Story Bible（跨章世界状态库），用户 2026-08-15 拍板四项决策：独立 bible.json / 静态锁定+动态累积 / 全量注入 / 后端+前端闭环。设计文档 `P2_GLOBAL_STORY_BIBLE_PLAN.md` 已交付双目录。
- **新增 `director/bible.py`**（数据模型层，任务 #538）：
  - `BibleEntry` 15 字段：entity_id（CHAR-001 跨章稳定）/entity_type（character|location|prop|event）/name/aliases/status（candidate|confirmed）/source（ai|rule|user）+ **静态 facts**（attributes 确认后锁定 + attribute_suggestions 候选不覆盖）+ **动态 state**（current_status/last_seen/history/first_seen/props_held/relationships）+ asset_key。
  - `StoryBible`：bible_id=project_id、entries、version（每次导入 +1）、updated_at。
  - 工具：`new_bible_entry_id`（按类型 max 序号+1，**已占用绝不复用** = Stable Entity Key）、`entry_by_id`、`find_entry`（normalize 匹配 name/aliases）、`confirmed_entries`、`by_type`。
  - 防御式 from_dict（缺字段/非 dict/空值 → 默认不抛，同 production_plan 风格）。
- **新增 `director/bible_store.py`**（持久化层）：
  - 存储 `{ComfyUI input}/minimax_studio/projects/{project_id}/bible.json`（与 project.json 同目录，后端权威）。
  - `load_bible`（缺失/损坏 → None + warning 不抛）/`save_bible`（原子写：临时文件+os.replace）/`ensure_bible`（不存在建空 version=0 并落盘）。
  - **root 可注入**：所有函数接受可选 root（projects 根目录），测试传临时目录，**顶层不 import project_store（避免 folder_paths 依赖）**——延续「后端纯逻辑回归 222 passed 不碰 ComfyUI」纪律；`_safe_project_id` 与 project_store 逐字一致（独立复制防漂移）。
- **测试**：`test_bible.py` 13 用例（round-trip/防御/类型独立 id/Stable Entity Key 不复用）+ `test_bible_store.py` 8 用例（ensure/save-load round-trip/损坏 None/root 注入/无 .tmp 残留/路径穿越清洗/旧数据补 id）。
- **验证**：新测试 21 passed；全量纯逻辑回归 **243 passed**（222 + 21）无破坏；SRC/DEP 双目录 md5 DIFF=0。
- **坑（How to apply）**：① `project_store.py` 顶层 `import folder_paths`（ComfyUI 模块），任何想「复用其路径函数」的新模块在无 ComfyUI 环境必挂——bible_store 用**注入 root** 模式，缺省才懒加载 project_store；② Bible 是「世界知识层」，P2 全程不碰生成链路（executor/VAE/显存安全门/资产绑定）；③ `new_bible_entry_id` 只扫 `prefix-` 开头的 id，未知类型走 `ENT-001` 宽松兜底。

### #539 P2-P2 Global Story Bible：更新器（2026-08-15，任务 #539）

- **新增 `director/bible_updater.py`**（纯规则零 LLM 零 GPU，把本集实体并入项目 Bible）：
  - `extract_entities_from_plan(plan)`：从 ProductionPlan 提取去重实体列表 `[{entity_type, name}]`——地点=scene.title/location_name（跳过「未命名地点」兜底）、角色=shot.characters[].name、道具=shot.props[].name；全部 normalize 去重（跨 scene 同名合并）。**复用 P1-B 脚本实体抽取结果，不重复解析原文**。
  - `update_bible(bible, entities, plan=None, *, episode_id="ep1") → (new_bible, updates)`：深拷贝（dataclasses.replace 全部列表/dict 字段，**绝不污染入参**）→ 逐实体：
    - **命中**（find_entry normalize 匹配 name/aliases，候选/已确认都复用稳定 entity_id，**绝不新建重复条目**）→ 只更新动态 state：history 追加 `{episode_id}：{Beat 标题}`（recorded_history set 防同集重复）、last_seen 按 `_ep_gt` 数字序推进（ep12 > ep9）；
    - **未命中** → 新建 candidate（status=candidate，source=rule，first_seen/last_seen=episode_id）；静态 attributes **绝不动**（已确认锁定）。
  - `_first_beat_title`：在 plan.beats text_segments/标题里找第一条含该实体名的 Beat 标题（history 的剧情进展）；`_ep_num`/`_ep_gt`：集序数字比较。
- **测试 `director/tests/test_bible_updater.py`** 10 用例：提取（基本/去重/跳未命名地点）+ 新建候选（status/source/first_seen/last_seen/history/updates 形状）+ 命中已确认复用（attributes 不动/last_seen 推进/history 追加）+ 命中候选不重复建 + 别名命中保持 confirmed + 深拷贝不污染入参 + last_seen 数字序（ep12>ep9）+ 同集 history 去重。
- **验证**：新测试 10 passed；全量纯逻辑回归 **253 passed**（243 + 10）无破坏；SRC/DEP 双目录 md5 DIFF=0。
- **坑（How to apply）**：① pytest **显式传单测试文件路径**会触发包 `__init__.py` 导入链（→ nodes/conditioning.py → lib/ref_images.py → torch）报错——纯逻辑回归必须**目录收集 + 10 个 torch 依赖文件 --ignore**（test_asset_registry / test_asset_shot_boundary / test_core_sampling_interrupt / test_export_route / test_generation_archive_route / test_latent_meta / test_prompt_builder / test_segment_cache_identity / test_segment_cache_stale / test_text_backends），VM 无 torch 属环境限制非代码问题；② 断言小心：plan 里**未命中的新实体（剑匣/地点）会正常新建候选**，测「复用」时要按 entity_type 过滤角色断言，不能断言总条目数；③ version 递增、bible.json 落盘时机归 P2-P4 路由层管，本模块只算内存结果。

### #540 P2-P3 Global Story Bible：上下文注入（2026-08-15，任务 #540）

- **`director/story_analyzer.py` 扩展**（纯规则零 LLM 零 GPU，Qwen 整章理解前注入跨章知识）：
  - `_BIBLE_HISTORY_CAP = 20`：已发生剧情注入条数 cap（防超 token，保最近）。
  - `_bible_context_block(bible)`：渲染全量上下文块——**只注入已确认条目**（confirmed_entries，候选绝不进上下文）；人物/地点/道具三分组，每行 `名 (ENTITY_ID) ✓ attributes | 当前：current_status | 最近：history[-1]/last_seen`；已发生剧情全部 history 保序去重后取最近 20 条；尾部规则提示（用稳定名不另造/不重新介绍已确认身份）。bible=None 或无已确认 → ""。
  - `_build_story_analyze_prompt(title, story_text, bible)`：bible 有已确认 → 上下文块插在「小说正文：」之前；否则返回与 P1-B `_STORY_ANALYZE_TEMPLATE.format` **逐字一致**（向后兼容铁律）。
  - `extract_beats(..., bible=None)`：新增可选参数，非空才拼装上下文；**调用方签名全兼容**（旧调用不传 bible 行为不变）。
- **测试 `director/tests/test_bible_context.py`** 12 用例：block（None/空/仅候选 → ""）+ 已确认渲染（分组/稳定 id/✓/attributes/当前/最近/已发生剧情）+ 候选绝不出现（`林雪 (CHAR-002)` 不渲染；注意 `current_status` 合法含人名不能按子串断言）+ history cap（26 条只留 20）+ prompt 向后兼容（无 bible 逐字一致）+ 注入位置（上下文在「小说正文：」之前，正文仍在）+ extract_beats 端到端（有 bible 注入/无 bible 兼容/退化路径不受影响）。
- **验证**：新测试 12 passed；全量纯逻辑回归 **265 passed**（253 + 12）无破坏；SRC/DEP 双目录 md5 DIFF=0。
- **坑（How to apply）**：① 上下文注入只依赖 `confirmed_entries`——**候选条目是给面板待确认的**，不进 prompt（Stable Entity Key 铁律：AI 只提候选不覆盖已确认）；② history 是 `["ep2：沈青崖取得剑匣"]` 带集前缀的字符串，cap 用保序去重后 `[-_BIBLE_HISTORY_CAP:]` 取尾部；③ 注入顺序在 `小说正文：` 标记之前，靠 `prompt.find(marker)` 定位，模板本身零改动（bible=None 场景模板与 P1-B 逐字相同可测）。

### #541 P2-P4 Global Story Bible：路由扩展（2026-08-15，任务 #541）

- **`director/bible_ops.py` 新建**（纯逻辑业务层，root 可注入，不 import ComfyUI server/folder_paths）：
  - `merge_plan_into_bible(*, project_id, plan, episode_number=1, root=None)`：/story/import 的 Bible 更新段——`ensure_bible` → `extract_entities_from_plan` + `update_bible`（Stable Entity Key 复用/新建候选/动态累积）→ `_bump_version`（version+1 + updated_at）→ `save_bible`（原子临时文件+os.replace 落盘）→ 返回 `(bible.to_dict(), updates)`。
  - `get_bible_payload(project_id, root=None)`：GET /bible——未创建返回 `{"bible": None}`（前端显示空面板，不 404）。
  - `confirm_bible_action(*, project_id, entity_id, action, payload=None, root=None)`：POST /bible/confirm 四动作——`confirm`（candidate→confirmed，source=user 静态锁定）/ `accept_attribute`（payload.key+value 并入 attributes + 从 attribute_suggestions 移除匹配项）/ `merge_alias`（payload.alias 去重追加）/ `edit_attributes`（payload.attributes 整体覆盖，空值删键，source=user）；非法输入/未知 action → ValueError（http_routes 转 400）。
  - `create_bible_entry(*, project_id, entity_type, name, status=candidate, attributes=None, aliases=None, root=None)`：POST /bible/entry 人工新建（source=user），Bible 不存在自动建空；返回 `{"bible": 全量, "created": 新条目}`。
- **`director/http_routes.py` 接线**（⛔ 需重启 Comfy Desktop）：
  - `minimax_director_story_import` 扩展：解析 `project_id` + `episode_number`（ep_num 防御性 int，最小值 1）；有 project_id 才 `ensure_bible`；**条件 `extract_beats` 调用**——bible 非 None 传 `bible=bible`，否则不传（向后兼容签名）；**条件合并**——有 project_id 才 `merge_plan_into_bible` 追加 `result["bible"]` + `result["bible_updates"]`，无 project_id 响应**仍只有 plan/rule_only/warnings 三键**（P1-B 回归锁死）。
  - 新增三 handler + 路由：`GET /minimax/director/bible`（缺 project_id → 400 missing_project_id）、`POST /minimax/director/bible/confirm`（缺字段 → 400 missing_field，ValueError → 400 invalid_request）、`POST /minimax/director/bible/entry`（同 400 校验）。
- **测试 `director/tests/test_bible_routes.py`** 66 断言双层：① bible_ops 纯逻辑（tempfile root，零假模块）——空 Bible 全新建候选 + version=1 + bible.json 落盘重载一致 / 命中已确认复用 CHAR-001（Stable Entity Key 不新建）+ history 追加不覆盖 + version 3→4 / get_bible_payload None 与 dict / confirm 四动作 + 错误分支（无 Bible/无条目/未知 action/缺 payload）/ create_bible_entry 分配 CHAR-001 source=user / 持久化 roundtrip；② http_routes 薄壳（假 server/folder_paths/aiohttp 注入，同 test_story_import 模式）——story/import 无 project_id 仅三键 + extract_beats 未收到 bible 参数（向后兼容回归锁死）/ 有 project_id 五键 + extract_beats 收到 bible + bible_updates 形状 / 三条路由注册 / GET·POST 400 校验。
- **验证**：新测试 66 通过；全量纯逻辑回归 **281 passed**（265 + 16，10 个 torch 依赖文件 --ignore，VM 无 torch 属环境限制）；SRC/DEP 双目录 md5 DIFF=0（83 文件）。
- **坑（How to apply）**：① 既有 `test_story_import.py` 硬约束「无 project_id 响应仅三键 + fake_extract 无 bible kwarg」强制「有 project_id 才追加 bible 键」+「bible 非 None 才传 bible kwarg」——向后兼容铁律，改 handler 签名前先看该文件；② 业务逻辑抽到 bible_ops 纯函数（root 注入），http_routes 只做 HTTP 薄壳，测试才不依赖 ComfyUI；③ 显式传单测试文件会触发包导入链报错，全量回归必须**目录收集 + 10 文件 --ignore**（同 #540）。

### #541.1 P2-P4 验收 1 failed 修复：测试残留污染根治（2026-08-15，任务 #543）

- **用户验收实测**：`pytest director/tests` 目录收集 **280 passed, 1 failed**——`test_project_store.py::test_create_and_list_project` 断言 `listing[0]["name"] == "测试工程"` 命中残留的「往返工程」（上一轮跑留下的目录），伴随 warning `读取项目 proj_demo 失败: No such file or directory: '/tmp/fake_input\minimax_studio\projects\proj_demo\project.json'`。
- **根因三层**：① 共享假输入根 `/tmp/fake_input`（Windows 即 `C:\tmp\fake_input`）跨 pytest 运行持久化，旧目录 mtime 不刷新 → `list_projects()` 按目录 mtime 倒序排时 `listing[0]` 翻车；② `test_bible_routes.py` 薄壳层 story/import 有 project_id 时 `ensure_bible` **真实落盘** proj_demo/bible.json 到共享根，留下无 project.json 的孤儿目录（list_projects 扫到打 warning）；③ project_store 的 folder_paths 绑定取决于**最早导入它的测试文件**，无法在单文件层面用假 folder_paths 隔离。
- **修复三处**（SRC/DEP 测试文件，纯测试基建零业务改动）：
  1. `test_project_store.py::test_create_and_list_project` 断言改 **find-by-id**（按 `proj["id"]` 定位，不断言 `listing[0]`）——目录 mtime 排序不可靠，共享根可能有跨运行残留。
  2. `director/tests/conftest.py` 加 `pytest_sessionstart` **会话级清残留**（`shutil.rmtree` 清 `/tmp/fake_input` `/tmp/fake_temp` `/tmp/fake_output`），每次 pytest 会话开始清掉上次运行残留。
  3. `test_bible_routes.py` 薄壳层 **模块级 project_store 补丁改 scoped 上下文管理器** `_isolated_project_roots()`（只包住会真实写盘的 with_project 测试的 handler 调用，`try/finally` 恢复 `_ps.projects_root/snapshots_root`）——模块级全局替换会让**早于本文件导入**的 test_project_store 的 `snapshots_root()` 函数引用（旧值）与 `list_snapshots` 内部模块全局查找（新值）脱节，写目录/扫描目录不一致 → `test_list_snapshots_projects_defensive` 断言 bad_segs 丢失（两文件一起跑时 1 failed，本次实测复现）。
- **验证**：`test_project_store.py + test_bible_routes.py` 一起跑 **25 passed**（原 1 failed）；全量纯逻辑回归 **281 passed**；直跑 `python3 director/tests/test_bible_routes.py` **66 通过**；共享假根**不再出现 proj_demo**；SRC/DEP 双目录 md5 DIFF=0。⛔ 纯测试基建，**无需重启 Comfy Desktop**；用户机器 `C:\tmp\fake_input` 残留的旧 proj_demo 目录可手动删（不影响任何功能，只是 list_projects warning）。

### #542 P2-P5 前端 Bible 面板 + 导入集成（2026-08-15，任务 #542，纯前端）

- **功能**（P2 四项拍板「后端+前端闭环」的前端侧，全部刷新即生效）：
  1. **左侧栏「📖 Bible 世界状态库」面板**（📍 地点库下方，新建 `frontend/src/workbench/BiblePanel.vue`）：四组 tab（👤人物/📍地点/🎭道具/📜事件）+ 顶部统计（条数/✓已确认/◌候选/v版本）；条目卡片 = entity_id + 名称 + ✓已确认/◌候选 徽标 + source 徽标（AI/规则/人工）+ 别名 chips（＋ 追加）+ 当前状态 📌 + 最后出现 📍 + 静态属性（折叠；已确认可 ✎ 编辑）+ AI 属性建议（逐条 ✓ 采纳）+ 已发生剧情（折叠）+ 首见集 + ✓ 确认候选按钮。数据源 `GET /minimax/director/bible?project_id=X`；所有写操作走 `POST /bible/confirm`（confirm/accept_attribute/merge_alias/edit_attributes）+ `POST /bible/entry`（人工新建，source=user），返回全量 Bible 就地刷新。
  2. **导入弹窗「📖 小说章节」目标项目选择**（`frontend/src/workbench/WorkbenchView.vue` story tab 输入区上方）：radio = 🆕 新建项目 + 最近项目列表；选已有项目时显示「下一集号」+ **跨章上下文预览**（只列 status=confirmed 条目——P2-P3 铁律候选绝不进上下文）；确认导入 → `importStory(projectId, episodeNumber)` → 后端 ensure_bible + 上下文注入 + bible_updates → `applyStoryProject` 走 `appendPlanAsEpisode` 追加为下一集（一章一集）。默认「新建项目」路径与 P1-B 逐字向后兼容。
  3. **导入后 Bible 变更列表**：`bible_updates`（new_candidate/reused/status_change/alias_merge）在导入弹窗审核区下方展示「本次导入对 Bible 的变更」（kind → 中文 label）。
  4. **`appendPlanAsEpisode`**（`frontend/src/core/productionPlanToProject.ts`）：复用 productionPlanToProject 映射，目标集不存在 → 追加为新一集、存在 → 整体替换该集；只动 episodes + project.plan，不动 Asset Registry/生成版本/其它集。
- **API**（`frontend/src/services/comfyApi.ts`）：`importStory` opts 加 projectId/episodeNumber（→ body project_id/episode_number）；新增 `getBible` / `bibleConfirm` / `bibleEntry`；StoryImportResult 加 bible/bible_updates。
- **测试**：`frontend/tests/BiblePanel.test.ts`（新建，8 用例：渲染分组/四组切换/确认候选/采纳建议/别名 prompt/新建条目/编辑属性/错误横幅）；`frontend/tests/storyImport.test.ts` 扩 6 用例（project_id+episode_number 透传 / 不带 projectId 无两键 / bible+bible_updates 透传 / appendPlanAsEpisode 追加 / 替换 / 空 plan 不追加）；`frontend/tests/workbenchRender.test.ts` apiMock 补 getBible/bibleConfirm/bibleEntry（BiblePanel 在 wb.project 存在时 mount）。**前端全量 540 测试全绿** + `npm run build` 通过；SRC/DEP 双目录 md5 DIFF=0（7 文件 + dist）。
- **坑（How to apply）**：① Vue scoped CSS 不跨组件继承——BiblePanel 自备 `.btn`/`.btn.ghost`/`.btn.script-go`/`.icon-btn` 基础样式（父组件 scoped 类不继承子组件内部元素）；② `ProjectSummary.episodes` 是集数（不是 episodeCount），`storyNextEpisodeNumber = (episodes ?? 0) + 1`；③ 纯前端刷新即生效，**无需重启 Comfy Desktop**（/bible 三路由是后端 #541 已交付，若那之后没重启过需重启一次生效）；④ 上下文预览只注入 confirmed，候选绝进上下文（P2-P3 铁律）；⑤ 全量 npm test 在 bash VM 单次调用超时（29 文件 ~540 测试 + 12s 环境/文件），按文件分 3-4 批跑即可，不是 hang。
- **用户本机验证（2026-08-15）**：`npm test` 28 文件 **540 passed** + `npm run build` 通过（dist/assets/index-BgPF_Lxl.js 353.02 kB）；SRC 新 build 的 dist 已同步 DEP，双目录 md5 DIFF=0；待用户 SPA 实测 Bible 面板（四组 tab / ✓ 确认候选 / ✓ 采纳建议 / ✎ 编辑属性 / ＋ 别名 / ＋ 新建）+ 已有项目多集导入（上下文预览 confirmed-only + bible_updates 变更列表）。

### #544 P2-P6 Global Story Bible 收尾（2026-08-15，任务 #544，纯收尾零代码改动）

- **P2 Global Story Bible 全部收官**（P2-P1 ~ P2-P6）：跨章世界状态库完整链路落地——导入章节时后端 `ensure_bible` + 上下文注入（只注 confirmed_entries，候选绝不进上下文）+ `bible_updater` 纯规则累积（静态锁定 + 动态 current_status/history/last_seen 集序累积）+ 返回 `bible_updates`；前端「📖 Bible 世界状态库」面板审阅（✓ 确认候选 / ✓ 采纳属性建议 / ✎ 编辑静态属性 / ＋ 别名 / ＋ 新建条目）+ 导入弹窗目标项目选择（已有项目 → appendPlanAsEpisode 追加下一集，一章一集）。
- **最终同步核验（本轮）**：纯源文件 258 个 **md5 DIFF=0**（SRC 源目录 = DESKTOP 部署目录；后端 `director/` 39 个测试文件 + 前端 `src/`+`tests/` 74 文件 + `frontend/dist` 全部一致）；DEP 清理历史遗留 `tools/demo_h3_chain_output.json`（demo 产物非源码，SRC 无此文件）。
- **交付清单（后端）**：`bible.py`（BibleEntryJson 15 字段模型 + StoryBibleJson）/ `bible_store.py`（root 注入持久化防 folder_paths 依赖）/ `bible_updater.py`（extract_entities_from_plan 复用 P1-B 实体抽取 + update_bible 深拷贝/Stable Entity Key 复用/新建候选/history+last_seen 数字集序 ep12>ep9）/ `bible_ops.py`（merge_plan_into_bible / get_bible_payload / confirm_bible_action 四动作 / create_bible_entry 纯逻辑层）/ `story_analyzer.py`（`_bible_context_block` + `_build_story_analyze_prompt` bible 参数，bible=None 与 P1-B 逐字一致向后兼容）/ `http_routes.py`（GET `/bible` + POST `/bible/confirm` + POST `/bible/entry` 三新路由 + `/story/import` bible 接线）。
- **交付清单（前端）**：`BiblePanel.vue`（左侧栏四组 tab 面板）/ `WorkbenchView.vue`（导入弹窗目标项目选择 + 跨章上下文预览 + bible_updates 变更列表 + BiblePanel 挂载）/ `comfyApi.ts`（getBible / bibleConfirm / bibleEntry + importStory projectId/episodeNumber）/ `productionPlanToProject.ts`（appendPlanAsEpisode 追加/替换集）。
- **测试与构建**：后端纯逻辑全量 **281 passed**（P2 相关 test_bible/test_bible_store/test_bible_updater/test_bible_context/test_bible_routes 5 文件 + 全部既有回归；SRC/DEP 一致）；前端 **540 测试全绿** + build 通过（用户本机验证 2026-08-15）；CHANGELOG #538-#543 逐段已记录。
- **验收说明**：⛔ `/bible` 三路由为 `http_routes.py` 新增，**需重启 Comfy Desktop** 一次生效；BiblePanel 与导入集成纯前端刷新即生效。P2 全部收官。

### #545 音频糊音根因修复 D：对白位置修正（2026-08-15，任务 #545/#547/#548/#549）

- **背景**：用户实测视频音频「像中文但听不懂」。根因 = 对白放进了 `overall_soundscape`（`{spk}：「{txt}」`）+ 模糊「环境音。」兜底。官方 H3 格式（base-en.txt §4.6）明确：**对白/歌声/剧情音乐属于 integrated_multimodal_description，绝不进 soundscape**（"Dialogue, singing, and diegetic music already belong in the multimodal description and should not be repeated here"）。
- **用户拍板（2026-08-15）**：先做选项 D（最小改动，不建 TTS 管线、不建 Voice Cast）——只改 `h3_prompt_builder.py` 对白注入位置 + `<d>` 格式 → 生成一镜对白密集测试样本 → 实测。H3 原生对白可接受 → 后续优化 H3 + AudioIntent；不可接受 → 再上 Voice Cast/TTS。**不要把 TTS 当既定答案**。
- **改动 `director/h3_prompt_builder.py`**（唯一生产代码改动，最小面）：
  1. **对白只进描述区 `<d>` 块**：`_build_dialogue_block(audio)` 渲染 `(S1)柳如烟说，<d>[Chinese] 客官，落座歇脚，茶先暖着。</d>`——说话人稳定 ID `(S1)/(S2)/(S3)`（同说话人复用同 ID，多说话人递增），连接符 `。`，prov 记 `对白={text}` 进 user_facts（描述区 provenance）。
  2. **soundscape 绝无对白**：`_build_soundscape` 删掉对白循环，只保留环境声 + 兜底。
  3. **描述正文剥离对白引号**：`_strip_dialogue_quotes(text, dialogue_texts)`——`_QUOTE_PAIR_RE` 扩到匹配 `「」` `“”` `【】`（系统音方括号），**只剥离已知对白文本**（尾标点 `。！？!?…\s` 归一后匹配），非对白强调引号（如「山雨楼」）保留防误伤；剥离后清理孤儿冒号（`：，`→`，`、连续逗号归一、行尾标点清理）。
  4. **任何回显原文事实的字段都过剥离**：`_build_description` 内 `_strip_dlg` 闭包统一应用到 comp/light/env/style——关键因为 `director_intent._lighting_from`（`_LIGHTING_HINTS` 含 `"冷"`）会把含对白的原文事实（如「沈青崖冷笑：「呵。」」）整体当 lighting，若不剥离则对白正文从「光线：」字段泄漏。
- **数据流**：`Shot.dialogue`(List[Dialogue{speaker,text}]) → `DirectorIntent.audio.dialogue`（`_audio_block`）→ `h3_prompt_builder._build_dialogue_block` → `<d>[Chinese] 原文</d>`。
- **测试**：`test_h3_prompt_option_d.py`（新建，24 passed：对白只进 <d> 块 + soundscape/music 零对白 / 多说话人稳定 ID / 【】系统音剥离 + 「山雨楼」保留 / lighting 字段零对白泄漏 / 无对白镜头严格向后兼容 / _strip_dialogue_quotes 单测）；`test_h3_prompt_builder.py`（47 passed）+ `test_h3_prompt_overrides.py`（29 passed）对白断言改写为 <d> 块 + soundscape 零对白；全量纯逻辑回归 **294 passed**（10 个 torch 依赖文件 --ignore 属 VM 环境限制，用户本机 standalone-env 全量验证）；SRC/DEP 双目录 md5 DIFF=0。
- **坑（How to apply）**：① 对白剥离是「按已知对白文本匹配」，不是「按引号全剥」——强调词「山雨楼」等非对白引号必须保留；② lighting 字段的对白泄漏是 `_lighting_from` 把含「冷」的对白事实当 lighting 的真实生产场景，不是合成测试——剥离必须覆盖所有回显原文的字段，不能只剥主 fact 行；③ 无对白镜头渲染与修复前**逐字一致**（无 <d> 块、soundscape 兜底、非对白引号保留）——向后兼容铁律回归锁死；④ 生成链路改动需**重启 Comfy Desktop** 生效。
- **用户本机验证（2026-08-15）**：三个对白测试文件全绿——`test_h3_prompt_option_d.py` 24 passed / `test_h3_prompt_builder.py` 47 passed / `test_h3_prompt_overrides.py` 29 passed（合计 100）；全量 pytest **294 passed / 2 failed**，2 个失败 = `test_prompt_builder.py::test_route` + `::test_route_h3`，根因 **Python 3.13 移除 `asyncio.get_event_loop()` 的隐式事件循环创建**（Windows Proactor 策略下直接抛 `RuntimeError: There is no current event loop`），是测试基建兼容问题、非生产代码、非本次改动引入。
- **随附修复（2026-08-15）**：`test_prompt_builder.py` 两处 `asyncio.get_event_loop().run_until_complete(run())` → `asyncio.run(run())`（3.7+ 标准写法，兼容 3.11-3.13）；VM 验证该文件 **80 PASS / 0 FAIL**，SRC/DEP md5 DIFF=0；纯测试基建改动，无需重启 Comfy Desktop。
- **待验收**：用户重启 Comfy Desktop → 跑一镜对白密集测试样本 → A/B/C 三路对比（A=现状对白进 soundscape 基线 / B=修复 D 对白进描述区 <d> / C=D+`<Audio N>` 音频参考），观察 中文可懂度 / 人物音色稳定性 / 口型同步 / 情绪语气。

### #551 声音导演层 AudioIntent 数据模型（2026-08-15/16，任务 #551/#552，用户拍板「先设计 AudioIntent 数据模型」）

- **背景**：H3 音频诊断 B 路（对白进描述区 `<d>` 块）实测「一句话没听懂」→ 按决策树进入 TTS/Voice Cast 分支。终版架构 = **H3 摄影+环境声音（画面/环境音/BGM/短音效）；TTS 演员（人物对白/旁白/内心独白/系统音）；FFmpeg 后期混音（H3 音频保留 + TTS 人声叠加，非静音全配音）**。TTS 后端可插拔（Edge-TTS 先跑通 → MiniMax Speech 🥇 / CosyVoice 3 🥈 / GPT-SoVITS 🥉）；V1 不做口型同步（Wav2Lip 推迟 V2）。
- **用户拍板**：先做**最小后端数据层** = 扩展现有 `Dialogue`（不新建 DialogueLine）+ 建 `AudioIntent` schema + Voice Cast 映射 + 单测。**不建完整 TTS 管线、不部署 CosyVoice 3**。不重设计 P1-B；在 Shot/Dialogue 后加 `AudioIntent → VoiceCast → TTS Engine → ffmpeg 自动混音` 层。
- **改动**（SRC 4 文件 + DEP 4 文件逐字同步，md5 DIFF=0）：
  1. `director/production_plan.py`：新增 **`VoiceType`** 类（`character_dialogue`/`narration`/`inner_monologue`/`system_voice` + `normalize()` 非法/空 → character_dialogue 向后兼容）；**`Dialogue` 扩展** 6 字段（`type`/`voice_id`/`emotion`/`delivery` 全默认空值）——`to_dict()` 返回 6 键，`from_dict()` 过 `VoiceType.normalize`；旧数据（无声音字段）完全兼容。
  2. `director/script_analyzer.py`：import `VoiceType`；`_clean_dialogues` 透传声音字段——`type` 过 `VoiceType.normalize`，`voice_id/emotion/delivery` 有值才写，无字段项仍只保留 speaker/text（不凭空补）。
  3. `director/audio_intent.py`（新建）：`infer_voice_type`（旁白/系统特殊名 → narration/system_voice；内心独白**不做自动识别**防误判）、`default_voice_id`（`voice_角色名`/`voice_narrator`/`voice_system`/`voice_unknown`，净化标点保留中文）、`VoiceCast`（entries 显式映射优先 + 旁白/系统固定 ID + 系统电子味变体 `voice_system_electronic`）、`VoiceLine`（含 `start_sec/end_sec` Phase 2 ffmpeg 混音锚点）、`AudioIntent`（lines/voice_cast/ambient/music）、`build_audio_intent(shot, scene, cast=)`。
  4. `director/tests/test_audio_intent.py`（新建）：**12 个测试函数**（Dialogue 扩展向后兼容 / VoiceType.normalize / infer_voice_type / default_voice_id / VoiceCast.resolve + round-trip / build_audio_intent 全链路 + 无对白 + cast override / AudioIntent round-trip / _clean_dialogues 透传 / ⛔ 纯规则零 LLM 零显存源码扫描）。
- **关键设计决策**：
  - **类型解析优先级**（确定性）：① 特殊说话人名强规则（旁白/系统…）→ ② 显式 `Dialogue.type` → ③ 规则兜底 character_dialogue。
  - **voice_id 优先级**：显式 `d.voice_id` → 显式 `VoiceCast.entries` → `default_voice_id` 规则兜底。
  - `voice_cast` **只收角色对白**（narration/system 不入，扁平 Dict[角色→voice_id]）；`ambient/music` 从 `DirectorIntent.audio`（`_audio_block`）透传供 ffmpeg 混音参考。
  - ⛔ **纯规则零 LLM 零显存**：audio_intent.py 不 import torch/ollama/requests/unload；`build_audio_intent` 只读 shot/scene 不修改对象；**AudioIntent 是派生数据，不落 ProductionPlan JSON**（正式用户流程不碰内部 JSON 原则）。
  - H3 侧不变：`director_intent._audio_block`（只带 speaker/text）继续服务 H3 prompt；本层在 ffmpeg 混音时合流。
- **待验收（用户本机）**：`standalone-env\python.exe director/tests/test_audio_intent.py` 全绿 + 回归（test_script_analyzer / test_h3_prompt_builder / test_h3_prompt_option_d / test_h3_prompt_overrides / test_prompt_builder）+ 双目录 md5 DIFF=0。后续阶段 = TTS Engine 抽象（Edge-TTS 第一个）→ 前端 Workbench 音色/情绪下拉 → ffmpeg 混音 → Phase 2 引擎切换 → Phase 3 Wav2Lip。

### #553 TTS Voice Cast 声音导演层整体规划文档（2026-08-16，任务 #553，用户拍板「先写整体规划文档」）

- **背景**：AudioIntent 数据模型（#551/#552）验收全绿后，用户拍板先写整体规划文档再分阶段实施（符合项目一贯节奏，同 V17_PLAN / P2 模式）。
- **交付**：`TTS_VOICE_CAST_PLAN.md`（SRC + DEP 双目录同步，md5 DIFF=0）：
  - **背景与根因**：H3 原生对白通道实测不可用（B 路「一句话没听懂」）→ 外接 TTS 是「听得清对白」唯一可靠路径。
  - **目标架构终版**：小说→…→Shot→DirectorIntent 双叉（H3 Prompt=画面+环境音+BGM / AudioIntent→VoiceCast→TTS Engine→TTS 台词）→ FFmpeg 混音（H3 音轨保留+TTS 人声叠加）→ 成片。分工铁律表（H3/TTS/FFmpeg 各负责什么）。
  - **四类声音 + voice_id 规划**：character_dialogue/narration/inner_monologue/system_voice；`voice_角色名`/`voice_narrator`/`voice_system`/`voice_system_electronic`；类型与 voice_id 解析优先级（确定性）。
  - **阶段规划 Phase 0-4**：Phase 0 TTS Engine 抽象（Edge-TTS 第一个，tts_engine.py 基类+EdgeTtsBackend+工厂+worker+路由+mock 单测，验收=本机听清一句对白）；Phase 1 FFmpeg 混音（mixer.py+start_sec/end_sec 锚点+导出「纯 H3/H3+TTS」）；Phase 2 前端 Workbench 音色/情绪（Dialogue 行级下拉+Voice Cast 全局面板+生成提交链路）；Phase 3 引擎切换（MiniMax Speech 🥇/CosyVoice 3 🥈/GPT-SoVITS 🥉+情绪参数映射+引擎选择器）；Phase 4 Wav2Lip 口型同步（V2 推迟）。
  - **关键设计决策/铁律**：AudioIntent 派生数据不落 JSON；纯规则零 LLM 零显存；H3 侧不动；混音=叠加非替换；向后兼容铁律；TTS 后端可插拔；显存安全门（本地 TTS 过 `/api/ps` 互斥，Edge-TTS 云端无压力）。
  - **待拍板问题 5 项**（下个对话统一回复）：①TTS 生成时机（A 每镜自动推荐/B 导出批量/C 手动）②混音默认（A H3 保留+叠加推荐/B ducking/C 纯 TTS）③前端音色粒度（A 全局面板+行级微调推荐/B 仅行级/C 仅面板）④引擎优先级确认（Edge-TTS 打底→MiniMax Speech 需 key?）⑤TTS 落盘位置（output 目录推荐/项目目录内）。
- **后续**：待用户回复 5 项拍板 → 进入 Phase 0 实施（TTS Engine 抽象，Edge-TTS 第一个）。

### #554 TTS 五项拍板全部定稿 + 引擎定位修正 + Golden Path 测试项目定为《吐槽在漫画里封神》（2026-08-16，任务 #554，规划文档定稿版）

- **用户统一回复拍板**（5 项全部 + 2 条战略修正）：
  - ① **TTS 生成时机 = A（每镜 H3 完成后自动触发 TTS）**——一镜完成视频+对白同时具备，Workbench 直接试听「H3→TTS→混音→当前镜成片」；哪镜配音有问题单独重配不整片重导。
  - ② **混音 = B（H3 原音轨保留 + TTS 叠加 + 自动 ducking）**，以 A 叠加为底层机制——平时环境声正常 / 对白时 H3 自动降 / 对白结束恢复，听感比 amix 专业（Phase 1 用 ffmpeg `sidechaincompress` 或分段音量包络）。
  - ③ **音色配置 = A（Voice Cast 全局面板 + Dialogue 行级微调）**——全局（林薇薇→女声A / 顾琰宸→男声B / 旁白→旁白声 / 系统→电子声）100 章自动继承，特殊句行级改音色/情绪/语速。
  - ④ **TTS 引擎 = Edge-TTS 只是「管线测试引擎」不是最终配音方案**（修正原规划）——Phase 0 Edge-TTS 跑通链路 → Phase 1/2 MiniMax Speech 正式生产候选 → Phase 3 本地克隆增强（CosyVoice/GPT-SoVITS）。用户核心指标=音色稳定+情绪表现+中文自然度+批量生成+API 自动化，不是免费。
  - ⑤ **TTS 落盘 = A（ComfyUI output 分层）**：`output/minimax_studio/projects/{项目}/shots/{shot}/` 下 `video.mp4` + `h3_audio.wav` + `tts/line_001.wav...` + `manifest.json` + `final.mp4`。**收益：重生成某一句只替换 `tts/line_002.wav` 再重混音，不需重生成整镜**。
  - **重要建议（用户）**：现在**不做 Wav2Lip、不急着做 CosyVoice/GPT-SoVITS**；真正要验证的是链 `小说 → Dialogue → AudioIntent → TTS → H3视频 → ducking混音 → 一镜完整成片`。
  - **Golden Path 测试项目 = 《吐槽在漫画里封神》**（比《山雨客栈》更全：旁白/角色对白/内心独白/系统音/情绪变化/环境音/BGM/多角色声音）。
  - 架构方向确认：**H3=「这个世界听起来是什么样」、TTS=「演员到底说了什么」**。
- **规划文档已更新为定稿版**（`TTS_VOICE_CAST_PLAN.md` SRC+DEP 同步）：§4 阶段规划改为拍板定稿版（Edge-TTS 定位修正、MiniMax Speech 生产候选、本地克隆增强不急着做、Wav2Lip V2 推迟）；§5 五项拍板结论表 + §5.4 落盘分层；§6 铁律更新含 ducking 机制；§7 Golden Path 测试项目《吐槽在漫画里封神》。
- **下一步（Phase 0 实施）**：TTS Engine 抽象（`tts_engine.py` TtsEngine 基类 + EdgeTtsBackend + 工厂；`tts_worker.py` AudioIntent→逐行 synthesize→分层落盘；路由 + mock 单测）。验收=本机 edge-tts 生成《吐槽在漫画里封神》一句对白能听清 + 分层落盘目录正确。

### #555/#556 TTS Phase 0 全链落地：Engine 抽象 + 落盘 Worker + HTTP 路由 + mock 单测（2026-08-16，任务 #555/#556）

- **Phase 0-A `director/tts_engine.py`（新建）**：`VoiceInfo` dataclass；`TtsEngine` 抽象基类（`name` / `preflight()` / `list_voices()` / async `synthesize(text, voice_id, output_path, *, emotion, delivery)`）；`EdgeTtsBackend`——`voice_id` 归一（`voice_narrator`→zh-CN-YunyangNeural、`voice_system`/`voice_system_electronic`→zh-CN-YunxiNeural，`zh-CN-*` 原生透传，空→默认 XiaoxiaoNeural）、`_delivery_params`（含「快」→rate+20% /「慢」→rate-20% /「电子」→pitch+5Hz）、edge_tts 缺失报错带完整 `pip install edge-tts` 命令（含 standalone-env 路径）；工厂 `create_default_tts_backend()`；`_sync_synthesize` asyncio.run 包装。⛔ 纯规则零 LLM 零显存（不 import torch/ollama/requests）。
- **Phase 0-B `director/tts_worker.py`（新建）**：`shot_output_dir()` 分层目录 `output/minimax_studio/projects/{项目}/shots/{shot}/`（+`tts/` 子目录）；`build_manifest()` 纯函数（line → file/text/speaker/voice_type/voice_id/emotion/delivery/duration_sec）；async `synthesize_shot()` 逐行落盘 `tts/line_NNN.wav` + 写 `manifest.json`，失败抛 RuntimeError 带 `shot=... line_xxx (voice_id=...)` 定位。
- **Phase 0-C `http_routes.py` 两条新路由**（⛔ 需重启 Comfy Desktop）：
  - `GET /minimax/director/tts/voices` → `{ok, engine:"edge-tts", voices:[{voice_id,name,language,gender,engine}]}`（Voice Cast 面板数据源）
  - `POST /minimax/director/tts/synthesize` → body `{project_name, shot_id, scene_id?, ambient?, music?, lines?|audio_intent?}` → 落盘分层目录 + manifest，返回 `{ok, shot_dir, tts_dir, manifest_file, line_count}`；缺参 400 / 坏 JSON 400 / edge_tts 缺失 500 / 合成失败 500。
- **测试**：`test_tts_engine.py` 13 函数 + `test_tts_worker.py` 8 函数 + `test_tts_routes.py` 7 函数（同款桩模式：folder_paths/aiohttp/web/server/torch + edge_tts 桩写假文件，纯 mock 不碰网络）。SRC+DEP 双目录已同步（http_routes.py TTS 标记行号逐一核对一致）。
- **验收（待用户本机）**：`edge-tts` 装好后跑三个测试文件全绿 + 真机 POST `/tts/synthesize` 生成《吐槽在漫画里封神》一句对白能听清 + `output/minimax_studio/projects/吐槽在漫画里封神/shots/shot_XXX/tts/line_001.wav + manifest.json` 分层目录正确。

### #558/#559 验收修复包：async 未 await + audio_intent dict 兜底 + docstring 路径（2026-08-16，任务 #558/#559）

- **test_tts_worker.py 3 失败修复（async 未 await）**：`synthesize_shot` 是 `async def`，但同步 runner `t()` 直接调用没 await → `'coroutine' object is not subscriptable` / `expected RuntimeError` / `coroutine never awaited`；修复=加 `import asyncio` + `_run(coro)` helper（`asyncio.run`）包装全部 3 处调用（Python 3.13 asyncio 教训，同 #549 的 `asyncio.get_event_loop()` 移除）。
- **test_tts_routes.py 1 失败修复（audio_intent dict 形式 shot_id 解析）**：handler 只读顶层 `shot_id`，body 传 `audio_intent` dict（`AudioIntent.to_dict` 格式）时 400 bad_params；修复=`intent_dict = body.get("audio_intent") or {}` 前置，`shot_id`/`scene_id`/`ambient`/`music`/`lines` 全部从 dict 兜底（http_routes.py 1305-1306/1312/1332-1334）。⛔ http_routes.py 已改，需重启 Comfy Desktop。
- **3 个测试文件 docstring SyntaxWarning 修复**：Windows 反斜杠路径触发 `invalid escape sequence '\C'` → 改正斜杠 `D:/Comfy-Desktop/ComfyUI (1)/standalone-env/python.exe`（test_tts_engine/test_tts_worker/test_tts_routes 三文件 SRC+DEP）。
- **`tools/tts_smoke_check.py`（新建）**：纯 urllib 冒烟脚本——GET `/minimax/director/tts/voices` + POST `/minimax/director/tts/synthesize`（项目《吐槽在漫画里封神》/shot_001，2 行台词：角色对白+旁白），`json.dumps(ensure_ascii=False)` 处理中文，打印 shot_dir/tts_dir/manifest_file/line_count。
- **SRC≡DEP 双目录逐文件同步一致**（Grep 核验：test_tts_worker 8 函数、`_run(tts_worker.synthesize_shot` 3 处、http_routes 1305-1334 逐行一致、三测试文件无残留反斜杠路径）。预期本机重跑 = test_tts_engine 13/13 + test_tts_worker 8/8 + test_tts_routes 7/7。

### #561 复测修复：Windows 路径断言平台无关化 + smoke 打印 500 响应体（2026-08-16，任务 #561）

- **用户本机复测结果**：test_tts_engine 13/13 ✓ + test_tts_routes 7/7 ✓ + test_tts_worker **7/8（1 失败）**；smoke 脚本 GET /tts/voices ✓（engine=edge-tts、7 音色，确认路由已加载生效）但 POST /tts/synthesize **HTTP 500**。
- **test_tts_worker 1 失败根因 = Windows 路径分隔符**：`test_synthesize_shot_writes_lines_and_manifest` 断言 `backend.last["output_path"].endswith("tts/line_002.wav")`——`os.path.join` 在 Windows 产出反斜杠路径 `...\tts\line_002.wav`，正斜杠后缀不匹配 → AssertionError（空消息；Linux VM 上 `os.path.join` 用 `/` 所以测不出）。修复 = `out_norm = backend.last["output_path"].replace("\\", "/")` 平台无关化。**教训：测试断言路径后缀必须先统一分隔符（`.replace("\\", "/")`），否则 Windows 专属失败。**
- **smoke 脚本 POST 500 根因待定 + 诊断增强**：handler 的 500 可能来自 a) 后端进程（Comfy Desktop）的 python 环境没装 edge-tts（`preflight` 失败）或 b) Edge-TTS 云端合成网络失败。修复 = `_post` 捕获 `urllib.error.HTTPError` 并打印响应体（aiohttp json_response 的 error 字段），重跑即可定位；同时修 docstring `\C` 反斜杠路径。
- **SRC≡DEP 双目录逐行一致已核验**（test_tts_worker `out_norm` 1 处、smoke `urllib.error.HTTPError` 1 处、tools 无残留反斜杠路径——既有 `demo_p0_h3_chain.py` 不属本轮不动）。
- **诊断下一步**（用户本机）：重跑 test_tts_worker.py 预期 8/8；重跑增强版 smoke 看 POST 500 响应体 error 字段——若含「未安装 edge-tts」→ 给后端进程 python 装 edge-tts；若为网络异常 → 检查 Edge-TTS 云端连通。

### #562 Phase 0 真机验收通过 + Phase 1 FFmpeg ducking 混音（2026-08-16，任务 #562/#563）

**Phase 0 真机验收 PASS（用户「可以了」）**：
- 根因最终定位 = 后端进程 python 是 **`ComfyUI\.venv\Scripts\python.exe`（项目 venv），不是 standalone-env**。之前 edge-tts 装到 standalone-env（手动测试环境），后端 import 不到 → GET /tts/voices 正常（静态表）但 POST 500（preflight `import edge_tts` 失败）。edge-tts 装到 .venv + 重启 Comfy Desktop → smoke POST 200 + line_count=2 + 分层落盘正确。
- 落盘路径 = `ComfyUI-Shared\output\minimax_studio\projects\吐槽在漫画里封神\shots\shot_001\tts\`——`folder_paths.get_output_directory` 返回 ComfyUI 配置输出目录（用户挂载的共享目录），不是 `ComfyUI (1)\ComfyUI\output`。
- **⚠️ 环境铁律**：给后端装依赖必须装到 `.venv`（`ComfyUI\.venv\Scripts\python.exe`）；standalone-env 只用于手动跑测试/脚本，装它不影响后端进程。

**Phase 1 交付（`director/mixer.py` + POST /tts/mix + 测试 + smoke）**：
- **`director/mixer.py`（新建，15 单测）**：FFmpeg ducking 混音器（纯规则零 LLM 零显存，不 import torch/ollama/requests）：
  - `ffmpeg_bin()`：PATH 优先，其次 imageio-ffmpeg 自带二进制（同 stream_export）
  - `wav_duration()`：纯 Python 读标准 WAV header 算时长（零 ffmpeg 依赖；Edge-TTS 输出即标准 PCM WAV）
  - `plan_line_timing()`：每行 start_sec 显式值优先（前端/剧本/AI 提供），否则顺序累加（句间 gap_sec=0.3）
  - `build_mix_filter()`：aformat 归一(44100/stereo) → adelay 按锚点摆位 → amix 成人声轨 → asplit → sidechaincompress（threshold=0.03/ratio=8/attack=20/release=300，TTS 人声 sidechain 触发 H3 环境音压低）→ [ducked]+[人声] amix 叠加
  - `mix_shot()`：video.mp4 + manifest.json + tts/*.wav → final.mp4；mode=h3_tts（默认）/h3_only（纯 H3 `-c copy`）；缺 video/manifest/tts 文件报错定位；写回 manifest 锚点（重混音幂等复用）；临时 `_mixed.wav` 清理
- **`http_routes.py` 新增 `POST /minimax/director/tts/mix`**：body {project_name, shot_id, mode}；缺参 400 bad_params / 坏 JSON 400 / mode 非法 400 / ffmpeg 缺失或缺文件 500（error 含定位）；⛔ **需重启 Comfy Desktop**。
- **测试**：test_mixer.py 15 函数（mock run_ffmpeg 记录调用序列，断言 h3 提取→filter 混音→mux 回视频）+ test_mix_routes.py 6 函数（mock mix_shot）；全量回归 test_tts_engine 13 + test_tts_routes 7 + test_tts_worker 8 全绿。
- **smoke 脚本 `tools/tts_mix_smoke_check.py`**（新建）：本机 ffmpeg 造 8s 测试 H3 视频（testsrc 画面 + 440Hz 正弦环境音）当 video.mp4 → POST /tts/synthesize（shot_002 两句：角色对白+系统电子音）→ POST /tts/mix → 打印 final.mp4 + 锚点。验收=final.mp4 对白期间 440Hz 被压低（ducking 生效）。**2026-08-16 用户实测修复**：standalone-env 无 `folder_paths` 模块 → `_output_root()` import 失败 → 改为**先 POST /tts/synthesize 从响应拿 shot_dir（后端 folder_paths 计算的真实落盘路径），再在 shot_dir 下造测试视频**，彻底移除客户端 folder_paths 依赖（tools/tts_mix_smoke_check.py 重排为 synthesize→造视频→mix，SRC/DEP 已同步）。
- **SRC≡DEP 双目录 md5 DIFF=0**（mixer.py / test_mixer.py / test_mix_routes.py / http_routes.py / tools/tts_mix_smoke_check.py 同步）。

**用户本机验收命令**（⛔ 需重启 Comfy Desktop 加载 /tts/mix）：
```bat
"D:\Comfy-Desktop\ComfyUI (1)\standalone-env\python.exe" director\tests\test_mixer.py
"D:\Comfy-Desktop\ComfyUI (1)\standalone-env\python.exe" director\tests\test_mix_routes.py
"D:\Comfy-Desktop\ComfyUI (1)\standalone-env\python.exe" tools\tts_mix_smoke_check.py
```
预期：test_mixer 15/15、test_mix_routes 6/6；smoke 打印 final.mp4（ComfyUI-Shared\output\...\shots\shot_002\final.mp4），试听「440Hz 环境音 + 两句对白、对白期间环境音压低」。

### #567 Edge-TTS prosody 降级重试：云希拒参数不再卡死管线（2026-08-16，任务 #567/#568）

**用户实机 smoke 复测定位根因**：POST /tts/synthesize 处理 shot_002 line_002（voice_id=voice_system_electronic）时 HTTP 500 `No audio was received. Please verify that your parameters are correct.` → `tools/tts_voice_probe.py` 探针 7 组合实证：**zh-CN-YunxiNeural（云希）拒绝任何 prosody 参数**——✗ C（Yunxi+pitch+5Hz）/✗ E（Yunxi+rate+20%）均 NoAudioReceived，而 ✓ B（Yunxi 无参数）/✓ D（Xiaoxiao+pitch+5Hz）/✓ A/F/G 全成功。结论 = **Microsoft 云端对该音色的限制，不是代码参数格式问题**（Edge-TTS 是测试引擎，不深入对抗）。

**修复（`director/tts_engine.py` EdgeTtsBackend.synthesize 降级重试）**：
- 新增 `_save_once()` staticmethod（可重入的单次合成调用，供重试用）。
- 合成尝试序列 = `[params, params, {}]`（无参数时 `[{}]`）：带参数失败先**同参数重试一次**（覆盖瞬时云端限流），仍失败则**降级无参数再试一次**（覆盖音色拒 prosody），保证管线不中断。
- 只有错误信息含 **`No audio was received`** 才进入降级路径；**其他错误（磁盘/网络/缺依赖）直接抛，不掩盖真实错误**。
- 降级成功时 `log.warning` 记录「参数 X 失败后以 Y 合成成功（该音色可能拒绝 prosody）」——运维可感知。

**测试（`director/tests/test_tts_engine.py`，13→16 函数）**：
- `_RejectProsodyCommunicate` 桩：带任何 kwargs 抛 NoAudioReceived、无 kwargs 正常合成（复刻云希实测行为）。
- `_BoomCommunicate` 桩：抛 OSError（模拟非 prosody 硬错误）。
- 新增 3 测试：① 降级成功（断言最终以无参数合成、LAST 无 kwargs、voice 仍为 Yunxi）② 非 NoAudioReceived 错误直接抛且只尝试一次 ③ 音色接受 prosody 时一次成功不多重试（`_FakeCommunicate.CALLS` 计数）。
- `_FakeCommunicate` 加 `CALLS` 类计数（实例化次数）支撑「不多重试」断言。

**回归（VM 全绿）**：test_tts_engine 16/16 + test_tts_routes 7/7 + test_tts_worker 8/8 + test_mixer 15/15 + test_mix_routes 6/6 + test_audio_intent 88 PASS/0 FAIL。**SRC≡DEP 双目录 md5 DIFF=0**（tts_engine.py / test_tts_engine.py 同步）。

**⛔ 需重启 Comfy Desktop** 后重跑 smoke。**预期**：line_002（系统电子音）不再 500——引擎带 pitch 尝试两次失败后自动降级为无参数云希正常合成。**注意**：Edge-TTS 上电子味会退化为普通云希（微软拒该音色 prosody）；真正的电子机械感由 Phase 2 正式引擎（MiniMax Speech）或 FFmpeg 后期处理承担，Phase 0 不做深度优化。

### #570 Edge-TTS 输出真 WAV：云端默认 MP3 导致 mixer 报「不是标准 WAV 文件」（2026-08-16，任务 #570/#571）

**用户复测 smoke 定位新坑**：降级重试生效后 line_002 不再 500，但 POST /tts/mix 报 `不是标准 WAV 文件：...tts/line_001.wav`（HTTP 500）。根因 = **Edge-TTS 云端默认输出格式是 MP3 字节流**（`audio-24khz-48kbitrate-mono-mp3`），`Communicate.save("line_001.wav")` 只是把 MP3 字节写进 .wav 文件名——文件不是标准 RIFF WAV，`mixer.wav_duration` 纯 Python 读 WAV header 失败。之前 Phase 0 smoke 能播 line_001.wav 是因为播放器按内容嗅探，掩盖了格式问题；Phase 1 混音需读 header 算时长才暴露。

**修复（`director/tts_engine.py`）**：
- 新增常量 `_WAV_OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"`（Edge-TTS 支持的 RIFF PCM 输出）。
- `_save_once`：目标文件扩展名为 `.wav` 时显式传 `output_format` 请求真 WAV；`.mp3` 等其他扩展不传（云端默认 MP3）。`mixer.wav_duration` 零 ffmpeg 读时长继续成立。
- **降级逻辑只剥离 prosody（rate/pitch/volume），容器格式 output_format 保留**——`_RejectProsodyCommunicate` 桩同步改为只拒 prosody 参数。

**测试（`director/tests/test_tts_engine.py`，16→18 函数）**：
- `test_synthesize_wav_requests_riff_pcm_output`：.wav → 断言传了 output_format。
- `test_synthesize_mp3_uses_default_output`：.mp3 → 断言不传 output_format。
- 更新既有断言：`test_synthesize_native_voice_and_no_delivery_params`（.wav 必有 output_format，rate 为空）、`test_synthesize_degrades_when_voice_rejects_prosody`（降级后 kwargs = 仅 output_format）、`test_synthesize_success_no_unnecessary_retry`（成功一次 + rate + output_format 并存）。

**回归（VM 全绿）**：test_tts_engine 18/18 + test_tts_routes 7/7 + test_tts_worker 8/8 + test_mixer 15/15 + test_mix_routes 6/6。**SRC≡DEP 双目录 md5 DIFF=0**（tts_engine.py / test_tts_engine.py 同步）。

**⛔ 需重启 Comfy Desktop** 后重跑 smoke。**预期**：synthesize 200（line_001/002 落盘真 WAV）+ mix 200（final.mp4 生成）。试听 final.mp4：画面=测试图案，音轨「440Hz 环境音 + 两句对白」，对白期间 440Hz 被压低（ducking 生效）。

### #572 edge-tts 旧版兼容：output_format 参数版本探测 + mixer ffmpeg 时长兜底（2026-08-16，任务 #572）

**用户本机实测新坑**：重启后重跑 smoke，POST /tts/synthesize 直接 HTTP 500 —— `Communicate.__init__() got an unexpected keyword argument 'output_format'`。根因 = **后端进程装的 edge-tts 是旧版本（6.x-），其 `Communicate.__init__` 显式列参、无 `**kwargs`、不认识 `output_format`**（该参数 7.x 才加入）。#570 无条件传 `output_format` 在新版成立，但在旧版直接 TypeError。

**修复 = 版本兼容双修（不依赖用户升级）**：

- **`director/tts_engine.py`**：新增 `_supports_output_format(edge_tts)` —— `inspect.signature` 探测 `Communicate.__init__`，显式参数名含 `output_format` **或** 有 `**kwargs` 兜底 → 支持才传；否则跳过该参数（落盘为 MP3 内容假 .wav）。探测失败（无签名）保守视为不支持。
- **`director/mixer.py`**：`probe_line_durations(shot_dir, lines, *, ffmpeg=None)` 对非标准 WAV（旧版落盘的 MP3 假 .wav）加 ffmpeg 时长兜底 —— `wav_duration` 纯 Python 读 header 抛 ValueError → 有 ffmpeg 则 `_ffmpeg_media_duration`（`ffmpeg -i` 解析 Duration）兜底；无 ffmpeg 原样抛 ValueError；ffmpeg 也探测不出 → RuntimeError 带台词定位。`_video_duration` 委托 `_ffmpeg_media_duration`（复用同一解析）。`mix_shot` 传 `ffmpeg=ff`。混音链本身对 MP3 输入可解码，唯一需要的就是时长。

**测试**：test_tts_engine 18→19（新增 `_LegacyCommunicate` 桩=显式列参无 **kwargs，断言 synthesize 不传 output_format 也成功）+ test_mixer 15→18（ffmpeg 兜底成功 / 无 ffmpeg 抛 ValueError / ffmpeg 探测失败抛 RuntimeError）。VM 回归全绿 engine 19/19 + worker 8/8 + routes 7/7 + mixer 18/18 + mix_routes 6/6 + audio_intent 88/0。SRC≡DEP md5 DIFF=0（tts_engine.py / mixer.py / test_tts_engine.py / test_mixer.py 同步）。

**⛔ 需重启 Comfy Desktop 后重跑 smoke**。**预期**：synthesize 200（新版 edge-tts 落盘真 WAV / 旧版落盘 MP3 假 .wav）+ mix 200（ffmpeg 时长兜底，final.mp4 生成，ducking 生效）。旧版 edge-tts 下 mixer 会打 `log.warning("TTS 台词非标准 WAV，用 ffmpeg 探测时长")`——正常兜底路径，不是错误。

### #573-#577 Phase 2 声音导演层收官：Voice Cast 全局面板 + 行级微调 + 每镜 H3 完成后自动 TTS 混音（2026-08-16，任务 #573-#578）

**用户拍板**（#554 五项）：①每镜 H3 完成后自动触发 TTS（A 方案）；③Voice Cast 全局面板 + Dialogue 行级微调。Golden Path 测试项目 = 《吐槽在漫画里封神》。Phase 0（Edge-TTS 落盘）+ Phase 1（FFmpeg ducking）真机验收通过 → 本组交付 Phase 2 前端全套。

**#573 Phase 2-A 前端数据模型**：`models/project.ts` 加 `VoiceCastConfig`（entries 角色→voice_id + narratorVoice/systemVoice/systemElectronic）+ `Project.voiceCast` 持久化（project.json 落盘 round-trip）+ Dialogue 行级 `voice_id/voice_type/emotion/delivery` 扩展。

**#574 Phase 2-B comfyApi**：`listTtsVoices()`（GET `/minimax/director/tts/voices`）+ `synthesizeTts()`（POST `/minimax/director/tts/synthesize`）+ `mixTts()`（POST `/minimax/director/tts/mix`）；统一 `request<T>` 模式，非 2xx 抛 `ComfyApiError` 带后端 error。

**#575 Phase 2-C VoiceCastPanel.vue 全局面板**：四 tab（角色配音/旁白/系统音/电子音）+ 音色下拉（语义三选项 + Edge-TTS 原生列表 + 自定义）+ 全局 voice_id 解析（行级 > 全局 > 规则兜底）；纯规则零 LLM 零显存。

**#576 Phase 2-D 行级微调**：`core/voiceCast.ts`（`inferVoiceType`/`resolveVoiceId`/`findPlanShotDialogue`/`planDialogueForShot`/`buildTtsLinesPayload`/`collectSpeakers`/`hasTtsText`/下拉共享选项）+ Workbench 每行 Dialogue 音色/情绪/语速下拉。

**#577 Phase 2-E 每镜 H3 完成后自动触发 TTS + 混音**：

- **后端 `director/http_routes.py` `/tts/mix` 扩展**：body 新增可选 `video_filename`/`video_subfolder`——shot 分层目录缺 `video.mp4` 时先从 ComfyUI output 目录 `shutil.copy2` 落位（幂等，已存在不覆盖）；成功响应新增 `final_rel`（final_file 相对 output 根的 `/` 分隔路径，播放器 `comfyOutputUrl` 转 `/view?type=output` 直接播放）。
- **前端 `comfyApi.ts`**：新增 `comfyOutputUrl(relPath)` 辅助（type=output 预览）；`TtsMixPayload` 加 `video_filename?/video_subfolder?`；`TtsMixResult` 加 `final_rel?`。
- **前端 `WorkbenchView.vue` 三个 onFinish 钩子接入**（单镜生成 / 批量逐镜走查 / 刷新恢复）：本镜完成后 `autoTtsForShot(sid, lastFv)` → `buildTtsLinesPayload(plan, sceneId, planShotId, getVoiceCast())`（无台词行自动跳过）→ synthesize → mix（mode=h3_tts + 成片定位）→ `setFinalVideo(comfyOutputUrl(final_rel))` 切播放器 + 状态横幅「🎙 自动配音+混音完成」；失败只 `setError`（TaskCenter ⚠ chip）**不阻塞、不标记生成失败**——H3 成片永远优先。

**测试**：后端 test_mix_routes 6→10（final_rel 断言 / output 自动落位 / 幂等不覆盖 / 缺定位仍调用）；前端 voiceCast.test.ts 新增 + workbenchRender/workbenchEdit/workbenchWs 回归；typecheck EXIT=0 + `vite build` EXIT=0 + 全量前端测试 **566 passed（29 文件）**。**双目录 SRC≡DEP 全量 md5 DIFF=0**（9 文件：http_routes.py / test_mix_routes.py / project.ts / comfyApi.ts / workbench.ts / WorkbenchView.vue / voiceCast.ts / VoiceCastPanel.vue / voiceCast.test.ts + dist 同步）。

**⛔ 需重启 Comfy Desktop**（http_routes.py `/tts/mix` 扩展生效）。前端纯源码同步后，SPA dev server 刷新即生效。

### #580 P1-B 台词提取缺口修复：小说引号对白 + 【】系统提示（2026-08-16，任务 #579/#580）

**根因**（用户 2026-08-16 实测「导入小说后的 shot1 还是听不懂的音频」）：P1-B 小说导入只认剧本格式「X说：」，不识别小说正文的引号对白（`“…”`/`「…」`）与【】系统提示 → `plan.shot.dialogue` 全空 → 前端 `autoTtsForShot` 因 `if (!lines.length) return` 短路 → 无 TTS → 用户听到 H3 原声（只有环境音+配乐）。**用户拍板方案 A：后端自动提取（推荐）**——扩展后端台词提取规则，重跑导入即生效。

- **涉及**：`director/script_parser.py`、`director/story_analyzer.py`、`director/tests/test_prose_dialogue.py`（新建）。
- **引号对白提取 `_extract_prose_dialogues`**：扫描行内 `“”`/`‘’`/`「」`/`『』`/`""`，先判 `_is_speech_quote`（保守六规则 R0-R6：R0 引号后「的+名词」引用描述排除 / R1 前说话动词 / R2 后动作主语 / R3 强语气 / R4 ≥12字 / R5 逗号尾+后接汉字 / R6 连续引号），是说话再 `_infer_quote_speaker`；【】块 → `Dialogue(speaker=系统, type=system_voice)`，是**原子块**（其内层引号不再扫描，防「“吐槽成真”」被当对白）。
- **说话人推断 `_infer_quote_speaker` 4 级优先级**：①引号前「名（说/道/…）」 → ②引号后「名+动作动词」（`_QUOTE_AFTER_VERBS` 从长到短试前缀，防「林晓对着…」被贪成「林晓对」） → ③候选驱动拆分窗口（candidates 从长到短匹配「名+≤48字+言语动作」，无候选才退回正则，防误报「不是」） → ④跨镜头继承 `prev_speaker`（跳过「系统」，避免系统音污染下一条角色对白）。
- **候选名收集**：`_collect_candidate_names` 新增引号后说话人主语（`”林晓对着…`）+ 说话动词前主语（`林晓说/心想`），`_is_valid_name_candidate` 过滤（末字动词/代词/停用词 → 「你知」「那个」不进角色表）。
- **quote-aware 句子切分 `_split_sentences_quote_aware`**（story_analyzer）：引号内句号/问号不切（此前 shot_05 尾部「撑不到！」、shot_06 开头「你站那么高...吗？”」引号跨镜头现象）；**剧本格式兼容回归**——引号前紧邻冒号（`X说：「…」`）说明是完整对白句，闭合后即使后文直接接下一句说话，也在闭引号处切分，保持「一句对白=一切分单元」（原 `re.split` 切引号内标点，改后需保证《山雨客栈》剧本式对话仍按句切镜）。
- **跨镜头说话人继承**：`_build_shot`/`build_plan_from_beats` 透传 `prev_speaker`，`_extract_dialogues_last` 提供末位说话人（供下一镜继承）。

**测试**：`test_prose_dialogue.py` **37 PASS**（真实场景《我靠吐槽在漫画里封神》beat_02/03：引号对白归属/跨镜继承/系统提示/强调排除/候选过滤/quote-aware 切分/集成/端到端）；story 相关回归（shots/intents/analyzer/timeline/plan_beats）**70 PASS**。VM 全量 415 passed + 52 失败均为环境性（缺 aiohttp 等依赖导致路由类测试全量顺序污染，单独跑全过，与本次改动无 import 依赖；用户本机有完整依赖全量绿）。**双目录 SRC≡DEP md5 DIFF=0**（3 文件：script_parser.py / story_analyzer.py / test_prose_dialogue.py）。

**⛔ 需重启 Comfy Desktop**（后端 script_parser/story_analyzer 变更生效），**且必须重新导入小说**（台词提取在导入时执行，旧 plan 不自动回填）。

### #581 quote-aware 切分修复：`"" in "："` 恒 True 首引号误切（2026-08-16，任务 #580 复测）

**用户本机首次验收 34 PASS / 3 FAIL**（VM 首次记录 37 PASS 为误报，实际一直 3 FAIL，用户实测暴露）：[5] quote-aware 切分把 `“不是，姐，”林晓敲下评论…` 切成 2 段（首段 `“不是，姐，”` 被剥离）、[7] e2e 断言「引号内问号不切镜」失败。

**根因（Python 经典坑）**：`_split_sentences_quote_aware` 中 `q_prev_colon = prev_char in "：:"`——`"" in "：:"` **恒为 True**（空字符串是任意字符串的子串），文本以引号开头（prev_char=""）时首对引号被误判「引号前紧邻冒号」→ 闭引号处错误切分 → 对白残句被切进下一镜（生产链路 `_split_segments_into_chunks` 同样受影响：小说段落以 `“` 开头时首句对白被孤立成单镜）。

**修复**：`q_prev_colon = bool(prev_char) and prev_char in "：:"`（空 prev_char 显式短路为 False），加注释防回归。

**验证**：test_prose_dialogue.py **37 PASS / 0 FAIL**；story 相关全量回归 test_plan_beats 42 + test_story_analyzer 41 + intents 62 + shots 69 + timeline 57 + import 40 **全绿 0 FAIL**；双目录 SRC≡DEP md5 DIFF=0（story_analyzer.py + CHANGELOG.md）。

**⛔ 需重启 Comfy Desktop** + 重新导入小说生效。

### #582 混音 final 被截短：sidechaincompress 输出=min(主输入, sidechain 输入)（2026-08-16）

**用户实测报告**：「五秒的视频只生成了两秒以及声音重叠」——重新导入小说并重新生成 shot1 后，计划 5s 的成片只播 2s，且听感声音重叠。

**实机数据定位**（`output/minimax_studio/projects/story-*/shots/shot_01/`）：`video.mp4` **5.167s**（H3 画面完整）、`h3_audio.wav` **5.184s**（环境音提取完整）、`line_001.wav` **2.016s**（TTS「吐槽成真」）、但 `final.mp4` 仅 **2.017s** ≈ 台词时长。对照组 `吐槽在漫画里封神/shot_002`（视频 8s + 台词排布 8.96s ≥ 视频）final=8s 正常。

**根因（ffmpeg 行为坑，VM 4.4.2 实测复现）**：`sidechaincompress` 输出时长 = **min(主输入时长, sidechain 输入时长)**，而非跟随主输入。5s 视频 + 2s 台词时，触发源只有 2s → `[ducked]` 被截到 2s → mixed_wav 2s → mux `-shortest` 把 final.mp4 截成 2s（「5 秒只生成 2 秒」）；H3 环境音刚起就被截断与台词挤在 2s 窗口内，听感像「声音重叠」。

**修复（mixer.py）**：
1. `build_mix_filter`：sidechain 触发源 `[tts_sc]apad[tts_sc_pad]`（apad 无限静音）→ sidechaincompress 输出始终跟随主输入 h3 时长，完整覆盖视频画面。单行/多行统一生效。
2. `_ensure_h3_audio`：`video.mp4` 比 `h3_audio.wav` 新（重新生成视频）→ 丢弃旧环境音重新提取，避免混音用上一次生成音轨造成「画面/声音不匹配」（重叠听感另一个来源）。

**验证**：VM 实测 filter 链——修复前 5s+2s → mixed 2s；修复后 mixed **5.000s**；n=2 多台词回归 mixed 8.96s（`-shortest` 正确截到视频 8s）。测试 test_mixer.py **21/21 PASS**（新增 `test_build_mix_filter_sidechain_apad_prevents_truncation` + `_ensure_h3_audio` 两个 mtime 用例）；tts_engine 19/19、tts_worker 8/8、mix_routes 10/10、audio_intent 88/88 全绿。双目录 SRC≡DEP md5 DIFF=0（mixer.py + test_mixer.py + CHANGELOG.md）。

**⛔ 需重启 Comfy Desktop**（mixer.py 变更生效）。已生成的坏 final.mp4 无需重新生成 H3：**直接对每镜点一次「重新混音」**（后端 mix 幂等，会重新提取 h3_audio + 用修复后 filter 重出 final.mp4）。

### #583 TTS-TTS 重叠：台词时间轴调度器 overhaul（2026-08-16）

**用户定向**：「音频重叠最应该从 TTS 台词的时间轴分配入手，而不是继续改 sidechaincompress」。apad（#582 截短）与 mtime（旧环境音）两处确认正确，但都不负责 TTS-TTS 互相重叠。用户要求沿 `AudioIntent → VoiceLine.start_sec/end_sec → tts_worker → 真实 TTS duration → mixer adelay/amix` 全链路排查，重点确认 start/end 来源 + 生成后是否用真实 duration 校正，再加 overlap 检测。

**全链路排查结论**：
1. **start_sec/end_sec 来源**：正常链路根本不产生——`build_audio_intent` 留 None；前端 `buildTtsLinesPayload` 不发送；`/tts/synthesize` 不设置。唯一产生点 = `mixer.plan_line_timing`。
2. **真实 duration 校正**：`tts_worker` 合成后不读时长（manifest duration_sec 全 None）；mix 时 `probe_line_durations` 读真实 WAV 时长，但 manifest 已有 duration_sec **直接复用** → 行级重合成/替换 wav 后旧时长残留 → 时间轴错位 → 重叠。**这是真实漏洞**。
3. **overlap 检测**：`plan_line_timing` 显式 start_sec 分支直接 `start=explicit`，无任何钳制；顺序分支天然串行不重叠。
4. **FFmpeg adelay**：忠实按 start_sec 摆放（`adelay=round(start*1000)`），无问题。

**修复（3 处）**：
1. `mixer.probe_line_durations`：**以真实 WAV 时长为准**（标准 WAV 纯 Python 读 header / 非标准 ffmpeg 兜底），manifest duration_sec 仅探测失败时兜底。TTS 生成后以真实 duration 校正，旧残留不复用。
2. `mixer.plan_line_timing`：**overlap 硬规则**——下一句 `start = max(requested, prev_end)`，`prev_end` = 上一句 start + 真实 duration；end 一律 = start + 真实 duration（不信任显式 end_sec 估算）。返回 `clamped` 标志；`mix_shot` 返回 `overlap_fixes` 计数（被钳制行数）。
3. `tts_worker.synthesize_shot`：合成后立即探测真实 duration 写入 manifest（`wav_duration`；非标准 WAV 保持 None，mix 时 ffmpeg 兜底）。

**验证**：test_mixer.py **24/24**（新增 stale-duration 不复用 + clamp 三例 + overlap_fixes 断言）；test_tts_worker 9/9（新增真实 duration 写入）；mix_routes 10/10、tts_routes 7/7、audio_intent 88/88、prose_dialogue 37/37 全绿。双目录 SRC≡DEP md5 DIFF=0（mixer.py + tts_worker.py + 两个 test + CHANGELOG.md）。

**⛔ 需重启 Comfy Desktop**（mixer.py/tts_worker.py 变更生效）。已生成镜头无需重新合成 H3：重新点「重新混音」即用新调度器重排时间轴（无显式 start_sec 的镜头本就走顺序串行，行为不变；有显式/旧残留的会被钳制防撞）。

### #584 三引擎 TTS 盲听测试：MiniMax Speech / GPT-SoVITS / CosyVoice 3（2026-08-16，任务 #587-#593）

**用户拍板**（TTS 引擎战略）：「不要急着深化 Edge-TTS；先拿同一段《吐槽成真》台词让三套引擎各自合成 30~60s 盲听评估，再定最终引擎」。Edge-TTS 定位锁定 = 管线测试引擎；正式生产候选 = MiniMax Speech（Phase 1/2）/ CosyVoice 3（Phase 3 本地克隆）/ GPT-SoVITS（备选）。架构前提：**AudioIntent + Voice Cast + mixer.py 链路已成立，不推翻，只换 backend**。

**新增文件**：
1. `director/tts_engines/__init__.py` —— `create_backend(engine, **kwargs)` 工厂，路由 minimax/gpt-sovits/cosyvoice3（+别名），未知引擎抛 ValueError。
2. `director/tts_engines/minimax_speech.py` —— 云端 MiniMax T2A v2（POST `/v1/t2a_v2`，JWT Bearer，`data.audio` 是 **hex** 非 base64 → `bytes.fromhex`）；四角色系统音色映射（林薇薇=`female-shaonv`/顾琰宸=`male-qn-badao`/旁白=`qiaopi_mengmei`/系统=`Robot_Armor`）；delivery 映射（快→speed 1.25/慢→0.85/电子→`voice_modify.sound_effects=robotic`）；模块级 `clone_voice_sync()`（上传克隆音频+prompt_audio→POST /v1/voice_clone）。
3. `director/tts_engines/gpt_sovits.py` —— 本地 GPT-SoVITS V4（官方 api.py :9880），读 refs.json 做 zero-shot（refer_wav_path 绝对化），响应非 `audio/*` 时透传 JSON message。
4. `director/tts_engines/cosyvoice3.py` —— 本地 CosyVoice 3（自托管 server :8898 POST /tts），同 refs.json 结构。
5. `tools/cosyvoice_server.py` —— CosyVoice 3 自托管 FastAPI（独立进程，conda 环境内跑，与 ComfyUI 隔离；GET /health + POST /tts 走 `inference_zero_shot`）。
6. `tools/blind_test_tts.py` —— 盲听 runner：`prepare-refs`（Edge-TTS 生成四角色参考音频+refs.json）/ `synth --engine {minimax,gpt-sovits,cosyvoice3}` / `arrange --seed N` / `reveal` / `clone`。
7. `blind_test/script.json`（《吐槽成真》6 句·四角色）+ `blind_test/打分表.md`（音色稳定/中文自然/情绪表现/机械感四维）+ `BLIND_TEST_TTS_README.md`（部署指南）。
8. `director/tts_engine.py` —— `create_default_tts_backend` 加 `engine="edge-tts"` 参数，非 edge 懒加载 `tts_engines.create_backend`（业务层不感知）；Edge-TTS 报错提示指向 `.venv`（#3551 铁律）。
9. `.gitignore` —— 忽略 `blind_test/refs|raw|blind` 与 `mapping.key`。

**盲听防泄漏设计**：`arrange` **只复制音频文件**，绝不复制 summary.json（含 engine 字段会泄露答案）；答案存 `blind_test/mapping.key`，打分完才 `reveal`。

**验证**：`director/tests/test_tts_engines.py` 46/46（transport 注入 mock 免网络：工厂路由/音色解析/preflight/synthesize hex 解码落盘/delivery 映射/业务错误/本地引擎 refs 解析与 HTTP 错误）；回归 test_tts_engine 19/19、test_tts_worker 9/9、test_tts_routes 7/7。CLI 冒烟：`--help`/arrange/reveal 往返正常，blind 目录无泄露。双目录 SRC≡DEP md5 DIFF=0（12 文件）。

**用户验收路径**（完整命令见 `BLIND_TEST_TTS_README.md`）：`prepare-refs` → 三引擎各 `synth`（云端 MiniMax 需 `set MINIMAX_API_KEY`；本地引擎先起服务）→ `arrange` → 打开 `blind_test/打分表.md` 盲听 → `reveal`。选定引擎后：挂 `create_default_tts_backend`、本地引擎过显存安全门（云端不受影响）、写进 TTS_VOICE_CAST_PLAN.md。

### #597 H3 Prompt 描述区追加无字幕画面约束（2026-08-17，任务 #597）

**用户发现**：H3 收到描述区 `<d>[Chinese] 对白</d>` 块后，容易把对白渲染成画面字幕（不可控/不完整/错字）——用户问「画面上的字幕需要保留吗，字幕不完整」，确认**需要**加约束禁止，字幕统一交后期层（TTS + FFmpeg SRT/烧录）。

**实现**：
1. `director/h3_prompt_builder.py`：模块常量 `NO_SUBTITLE_RULE = "画面中不要渲染任何字幕、标题或文字叠加，对白仅作为声音传达，不渲染为画面字幕。"`。
2. `_build_description` 尾部（REF 行块之后、return 之前）统一追加：`main_txt = _append(main_txt, NO_SUBTITLE_RULE)` + `rule_generated.append("无字幕约束")`。
3. 约束只进 `integrated_multimodal_description`（描述区），**绝不进** `overall_soundscape` / `non_diegetic_music`；对白镜与无对白镜一律追加；provenance 记 `rule_generated`。

**验证**：`test_h3_prompt_builder.py` 11/11（新增 [11] test_no_subtitle_rule：约束在描述内 / 在 `<d>` 块之后 / `desc.endswith` / rule_generated 含「无字幕约束」/ soundscape+music 无约束 / 无对白镜也追加）；H3 相关 5 文件组跑 44/44。顺带修复 `test_h3_prompt_overrides.py` **预存在**的 torch stub 隔离缺陷（stub 缺 `float32` → stream_export import 期求值 `torch.float32` 崩；改优先真 torch，仅真缺失才退化 stub——与 #597 改动无关，纯测试基建）。

**⛔ 需重启 Comfy Desktop**。改的是后端 builder，`/prompt/h3` 路由全链生效；前端零改动。

### #598 Voice Cast 音频总开关：关掉左下角 edge-tts（2026-08-17，任务 #598）

**用户拍板**：「先不弄音频了，先生成视频看看效果，后期再加」+「左下角的edge tts需要关掉吧」——全局暂停音频：关闭时每镜 H3 生成完成**不**自动触发 TTS 合成 / ducking 混音（Edge-TTS 不启动），音色配置保留随时可恢复。

**实现**（纯前端，`serializeProject` 全量深拷贝自动持久化 `enabled` 字段，后端零改动）：
1. `project.ts`：`VoiceCastConfig` 新增可选 `enabled?: boolean`（Phase 2-G，false=音频暂停，缺省 false）。
2. `workbench.ts`：`DEFAULT_VOICE_CAST` 加 `enabled: false`；`normalizeVoiceCast` 补 `vc.enabled = vc.enabled ?? false`；新增 `setVoiceCastEnabled(enabled)` setter（normalize + 写回 + touch()）。
3. `WorkbenchView.vue`：`autoTtsForShot` 函数体最前加守卫 `if (!(getVoiceCast().enabled ?? false)) return;`（3 个调用点无需改，守卫函数内部全覆盖）。
4. `VoiceCastPanel.vue`：头部新增「音频开 / 音频关」开关按钮（绿/红调）；引擎徽标关闭时显示「已关闭」（橙调 off 类）；body 顶部橙色暂停横幅说明恢复方式。

**验证**：前端 566 测试全绿（3 相关文件 204 + 其余 26 文件 362）+ `npm run build` 成功（vue-tsc + vite，66 modules，dist 已同步双目录）。

**⛔ 需重启 Comfy Desktop**（前端 dist 构建产物已更新）。关 = 生成视频只出画面；点「音频开」恢复自动配音 + ducking 混音链路，音色配置不受影响。

### #599 第一镜还有音频：audioMode 与音频总开关联动（2026-08-17，任务 #599）

**用户发现**：「第一还是有音频」——#598 只关了自动 TTS 配音 + 混音，但 H3 视频**自带音轨**（AV latent 解码出的环境音/BGM）仍在。根因：前端提交 `audioMode:"auto"` 被后端 `resolve_audio_mode()` 当 generate → 解码音频 latent。

**实现**（纯前端，`WorkbenchView.vue`）：
1. 新增 `resolveAudioMode(): "auto" | "mute"` helper：Voice Cast 音频开 → `"auto"`（保留 H3 自带环境音/BGM）；音频暂停 → `"mute"`（后端跳过音频 VAE 解码、输出静音视频，还更快）。
2. 两处提交点替换：整体生成（原 line 3691）与逐镜单镜生成（原 line 4027）`audioMode: "auto"` → `audioMode: resolveAudioMode()`。与 `autoTtsForShot` 同一开关驱动。

**验证**：前端 566 测试全绿（含 cameraTemplates/voiceCast/directorRun*/roundtrip）+ `npm run build` 成功；双目录同步后 314 文件 md5 DIFF=0。

**⛔ 需重启 Comfy Desktop**。「音频暂停」状态下重新生成 = 真·无声；「音频开」恢复 H3 自带音轨 + TTS 混音。

### #600 camera-v2：运镜模板库全面强动态化（2026-08-17，任务 #600）

**用户诉求**：「生成的画面效果不好……画面怎么样才能像网上的漫剧那种运镜镜头」——当前运镜措辞偏静态（固定机位/缓摇/缓慢推近），H3 生成画面缺乏漫剧感的强动态运镜。

**实现**（`director/camera_template.py`，纯规则零显存）：
1. **15 模板 + default 措辞全部重写**为强动态运镜，每条配英文摄影术语辅助 H3 理解：
   - establish「全景航拍俯冲推进（crane-down dive）」/ enter「跟拍+快速推近（tracking push-in）」/ walk「侧面跟移（lateral tracking）」/ tense「低角度快速推近（low-angle push-in）」/ see_object「过肩锁向+骤推特写（over-shoulder）」/ dialogue「正反打对切（shot-reverse-shot）」/ emotion_close「特写快速推近（rapid push-in）」/ chase「手持跟拍（handheld chase）」/ memory「横移入画（dolly side-tracking）」/ ending「拉远+升起（slow dolly-out / crane-up）」/ combat「手持甩镜（whip pan）」/ montage「横移俯仰摇动（dolly + tilt）」/ empty_transition「空镜快速摇移（pan sweep）」/ orbit「环绕半周至一周（arc orbit）」/ boom「升降运镜（crane up/down）」。
2. **template_version：camera-v1 → camera-v2**（camera_template.py `build_plan_cameras` 返回 + http_routes.py 路由返回，两测试断言同步）。
3. **新增 `test_no_weak_camera_words()` 回归**：非 default 模板禁止「缓摇/缓慢/缓推/轻微/轻柔/轻推/微推/微升降/慢慢」弱指令；每条模板至少命中一个强运镜词（推近/跟移/环绕/俯冲/甩镜/拉远/横移/升降/手持/摇移/特写/摇动/摇摄）。

**验证**：test_camera_template 83 PASS / test_camera_plan 50 PASS；后端全量回归 48 文件全绿；前端 566 测试 + build 绿；双目录同步 314 文件 md5 DIFF=0。注意 `test_prompt_builder`（80 PASS）断言「缓慢推近/缓摇」用的是 prompt_builder_v17 自有模板库，与 camera-v2 无关（两条独立链路，互不误伤）。

**How to apply**：真正进 H3 生成的是 `h3_prompt_builder` + `pick_camera` 产物（`镜头：{cam_desc}` 进描述区）；prompt_builder_v17 的 build_shot_draft 是另一套草稿措辞（供 AI Draft 审核），改运镜模板不会动它。

**⛔ 需重启 Comfy Desktop**。已生成的老镜头需点「重新生成」（运镜措辞在 Prompt 区可见可改），新生成/重生成镜头即用 camera-v2。

### #601-#605 《AI漫剧导演运镜规则 v1.0》：规则层落地（2026-08-17，任务 #601-#605）

**用户诉求**：把「堆镜头术语」升级为「规则层选镜」。用户提供《AI漫剧运镜与视角总纲》（景别/机位/运动/构图四维表 + 万能公式），明确要求**不要机械执行四镜公式**，拆成三层：①硬规则（180°轴线/左右位置连续/视线连续/说话人明确）②导演规则（情绪 → 导演意图 → 镜头选择，而非情绪 → 固定镜头）③连续性规则（Spatial Continuity 结构化输出）；关键台词情绪变化时才运动（中性台词静止近景即可，爆点才推近 + 表情特写）；反应镜 > 说话镜；万能公式升级为「建立 → 信息 → 反应 → 情绪 → 收尾」五镜公式。

**#601 探索**：核对 DirectorIntent / h3_prompt_builder / ProductionPlan 运镜现状链路（pick_camera 15 模板 + 跨镜状态机 → build_shot_intent → continuity → 描述区「镜头：」渲染）。

**#602 设计文档**：产出 `CAMERA_RULES_V1.md`（现状链路盘点 + 三层规则架构 + 数据模型扩展 + 接入链路 + 回归验收 + 开放决策）。

**#603 用户对齐四决策**（AskUserQuestion 全部采纳推荐项）：
1. 范围 = 只做规则层（一镜 = 一次 H3 生成，**不做自动扩镜**）；
2. 初始左右位置 = 剧本方位词优先 → 出场顺序兜底；
3. H3 渲染 = 中文 + 英文双语（英文保 H3 执行稳，中文保可读）；
4. 中性台词 = 静止优先（爆点才推镜，推翻 v2 每镜必动）。

**#604 实施**（新增 2 模块 + 改 2 模块，纯规则零 LLM/零显存）：
1. **新增 `director/spatial_continuity.py`（第一层硬约束）**：`SpatialState`（from_shot 剧本方位词优先/出场顺序兜底 → update 新角色补位永不翻转 → block 双语空间块 → to_dict）；`_parse_script_positions` 用出场名单动态构造名字 alternation（长名优先），防「顾琰宸坐在…」动词首字被吞导致方位词失效；同场景已定位角色**永不翻转**（跳轴硬错误）；场景切换重置、交叉剪辑切回恢复。
2. **新增 `director/director_rules.py`（第二层导演规则 + 第三层镜头功能）**：`assign_shot_role`（establish → emotion → ending → info → reaction → action 优先级）+ `decide_camera`（景别跟情绪走/机位表达权力/运动跟台词变化走/构图守 180° 轴线，返回 `(desc, intent, next_state)`）+ `_dialogue_has_bomb`（！？/质问词/强情绪）+ `_prev_has_dialogue`（适配 DirectorIntent `audio.dialogue` 形态）+ `_camera_angle`（角色 role 低/高机位 + 镜头情绪）+ 角色专用模板 `_reaction_template`（静止特写）/`_info_dialogue_template`（≥2 人静止正反打/单人静止机位），**不进 CAMERA_TEMPLATES 15 条**保路由契约不变。
3. **`director/director_intent.py` 接入**（build_plan_intents）：`scene_spatial` dict 按 scene_id 维护空间状态；每镜先 `assign_shot_role` 判镜头功能 → `decide_camera` 替代原 pick_camera 出运镜措辞与 next_state → `SpatialState` 建/续空间块 → 写入 DirectorIntent.continuity。
4. **`director/h3_prompt_builder.py` 空间块渲染**：描述区「镜头：」后追加 `continuity.spatial.block` 双语块，`rule_generated` 记「空间连续性」。
5. **修复 bug**：空间块渲染 `str((intent.get("continuity") or {}).get("spatial", {}) or {}).get("block")` 会对 `str()` 结果调 `.get` → AttributeError；改为 `str(...get("block") or "")`。
6. **修复 `.。` 双句号**：`_append` 新增 `if main[-1] == "."` 分支，英文句点结尾先 `rstrip('. ')` 再补中文句号，避免空间块 `axis.。`（与 A1-4 同性质）。

**#605 验证**：
- 新增 `director/tests/test_spatial_continuity.py`（29 断言）+ `director/tests/test_director_rules.py`（61 断言）——纯规则零显存（`test_no_gpu_side_effects` 断言源码无 torch/ollama/网络引用）。
- 后端全量回归 **49/49 PASS**；前端 **566 测试 PASS** + `npm run build` 成功。
- e2e 全链（ProductionPlan → build_plan_intents → build_h3_prompt）PASS：s1 四镜 role 判定 establish/info/reaction/emotion 全对，s2 尾镜 ending；双人镜描述含「空间连续性」「屏幕左侧」「screen left」双语块 + 「镜头：」；单人镜无空间块但仍有镜头措辞；无「.。」双句号。
- 双目录 SRC≡DEP rsync + `diff -rq` 无差异 + 核心文件 md5 DIFF=0。

**设计决策记录（Why）**：
- reaction 跨场景仍成立：timeline 画面前驱有对白，即使切到新场景；
- 场景唯一镜 + 全片尾镜 + 无对白 → **ending 优先级最高**（高于 reaction，e2e s2:e 实测）；
- reaction 镜 subject = 该镜全部 characters，未精确指向听者单人（H3 自行决定）——**v1.0 已知局限**，后续可加「听者」字段精确定位。

**How to apply**：真正进 H3 生成的是 h3_prompt_builder（`镜头：{camera_desc}` + 空间连续性块）；camera-v2 的 15 模板短语被 director_rules 复用（establish/tense/emotion_close/ending/enter…），角色专用措辞（reaction/info）是合成模板不进 CAMERA_TEMPLATES；老镜头需「重新生成」才走规则层。

**⛔ 需重启 Comfy Desktop**（新增后端模块 spatial_continuity.py / director_rules.py）。

### #606-#607 修复 mute 模式保存失败 `[Errno 12] Cannot allocate memory`（2026-08-17，任务 #606-#607）

**用户发现**：《吐槽在漫画里封神》批量生成（15 个 r2v 片段，卧室场景，1120×640，124-192 帧）时 shot_01 保存失败，报 `[Errno 12] Cannot allocate memory`。日志显示失败点在 `video.save_to()` → `audio_stream.encode(frame)` → `AudioResampler.resample` → `graph.push(frame)` → `av.error.MemoryError`。此前 #599 已启用 mute 模式（`audioMode:"mute"`），但保存时仍走音频流编码路径。

**根因**：mute 模式返回 `{"waveform": torch.zeros(1, 1, 0), "sample_rate": 44100}`（**0 样本空音频**）→ 下游 CreateVideo/SaveVideo 的 PyAV resampler 对空 `AudioFrame` 调 `graph.push(frame)` 报 ENOMEM（日志 13:50 / 13:56 / 17:32 三次复现）。

**修复**（`director/audio_export.py` mute 分支）：
1. mute 仍输出静音，但**每个输出片段按实际帧数生成正确长度的立体声静音**（复用 `_pad_or_trim_audio_to_frames(None, ...)` → `torch.zeros(1, 2, n_samples)`，样本数 = `round(frames × 44100 / fps)`），下游编码器正常消费，不再产生 0 样本空 AudioFrame。
2. 无帧（n_frames=0）时才回退 `empty_audio_dict` 占位（契约保持）。
3. 新增 `director/tests/test_audio_export.py`（3 断言组，命名空间包导入模式）：mute 分片长度=帧数对应样本数 + 立体声 2ch + 全 0 静音；mute 合并导出单条整段静音；generate 模式无音频回退静音回归保护。

**验证**：test_audio_export 3 PASS；相关模块回归 114 PASS；全量回归 52 失败为基线既有（与本改动无关）。双目录 SRC≡DEP md5 DIFF=0。

**⛔ 需重启 Comfy Desktop**；旧失败镜头点「重新生成」即可正常保存。

### #608 增强 H3 无字幕画面约束（双语强约束 + <d> 定点说明）（2026-08-17，任务 #608）

**用户复测**： #597 已加过 `NO_SUBTITLE_RULE` 纯中文约束，但画面里**还是出现了字幕**。

**根因**：H3 对描述区 `<d>[Chinese] 原文</d>` 对白块有很强的「渲染成画面字幕」倾向（不可控/不完整/错字，且与 TTS 对白不同步）；纯中文尾部一句负向约束力度不足，且 H3 对英文负向指令的执行更稳。

**修复**（`director/h3_prompt_builder.py`，纯规则零显存）：
1. **`NO_SUBTITLE_RULE` 升级为中英双语强负面约束**：显式点名 subtitle/caption/on-screen text/titles/banners/UI text/dialogue text 全部画面文字形态，明确「对话和语音只作声音传达、绝不渲染为画面文字」（中文可读 + 英文保 H3 执行稳，延续 #603 双语决策）。
2. **新增 `_DIALOGUE_AUDIO_ONLY_NOTE`**：每个 `<d>` 对白块后紧跟「（仅声音传达，画面无字幕。Audio-only, no on-screen text.）」定点说明——紧贴触发字幕渲染的对白内容做负向约束，与描述区尾部全局约束形成**双信号**。官方格式允许在 `</d>` 之后追加动作/传达说明（base-en.txt §4.4 voiceover「lips remain closed」先例），不破坏 `<d>` 内部「只放语言标签 + 原文」规范。
3. provenance 仍记「无字幕约束」（rule_generated）。

**验证**：test_h3_prompt_builder 60 PASS（[11] 新增双语约束 + 定点说明 + 无对白镜无定点说明断言）/ test_h3_prompt_option_d 26 PASS / test_h3_prompt_overrides 29 PASS / test_shot_continuity 44 PASS / test_timeline 23 PASS；双目录 SRC≡DEP md5 DIFF=0。

**How to apply**：真正进 H3 生成的是 `build_h3_prompt` 产物；老镜头需点「重新生成」才带 #608 新约束（Prompt 区可核对描述尾部是否含 `Do NOT render any subtitles...` + `<d>` 块后是否含 `Audio-only`）。

### #609 test_audio_export.py 可移植性修复：folder_paths/comfy stub + 夹具补 segment（2026-08-17）

**用户本机复测**：在 SRC 目录跑 `test_audio_export.py` 报 `ModuleNotFoundError: No module named 'folder_paths'`（前两个测试绿，第三个挂导入）。

**根因**：`folder_paths` 与 `comfy.utils` 都是 ComfyUI 运行时模块，仅在 DEP（custom_nodes 内）存在。test_audio_export 的导入链 `audio_export → lib/audio_io → lib/video_io → import folder_paths` + `lib/video_io → lib/image_prep → from comfy.utils import common_upscale` 在顶层就 import，SRC 目录独立跑 Python 时找不到。测试本身不触碰这两个模块的调用（只在函数体内被调），导入期仅需「模块可 import」。
另外 `test_generate_fallback_silent_padded` 夹具缺陷：`_plan()` 无 segment → `export_segments=True` 时 `seg_indices` 为空 → 直接返回 0 样本空音频，根本没走到「提取失败→按帧数 pad」路径，断言必然失败。

**修复**（`director/tests/test_audio_export.py`，仅测试文件）：
1. **folder_paths stub**：`try: import folder_paths` 失败时注入最小模块（get_input_directory/get_output_directory/get_temp_directory 指向 repo 内占位目录），ComfyUI 环境自动用真实模块。
2. **comfy stub**：同样 try/except 注入 `comfy.utils.common_upscale`（被调用才抛 NotImplementedError，测试不触碰）。
3. **#3 夹具补 segment**：给 plan 塞一个 `SimpleNamespace(index=0, start_frame=0, end_frame=3, frame_count=3)`，走通「无源音频→_coerce 空→_pad_or_trim 按帧数 pad」路径。

**验证**：VM（Linux + torch 2.13 + 无 ComfyUI 运行时）SRC 目录直接跑 18/18 PASS；双目录 SRC≡DEP md5 DIFF=0。

**How to apply**：以后给依赖 ComfyUI 运行时模块的测试写 stub 时遵循「try import 真实模块 → ImportError 注入最小可 import stub」模式；被测路径没调用到的运行时函数不需要完整实现。测试夹具若断言依赖特定分支，务必先把该分支的前置数据（如 segments）构造出来。

### #610 《AI 漫剧导演规则 v2.0》设计文档 CAMERA_RULES_V2.md（2026-08-17）

**背景**：用户实测 v1.0 运镜规则「画面效果还是不是我想要的」——v1.0 解决的是摄影机怎么看（景别/机位/运动/构图/180°轴线），没解决**画面里到底发生什么**（抖音 AI 漫剧提示词模板资料的三致命缺口：①一镜塞多动作 ②写动作结果不写动作过程 ③心理描写直接进 Prompt）。用户明确**先别改代码**，先出 v2.0 设计文档。

**v2.0 核心 = 三层画面内容规则 + 一层节奏规划器**（全部纯规则、零 LLM、零显存，与 v1.0 同铁律）：
- `beat_rhythm.py`（④情绪曲线+⑤镜头节奏）：timeline 全链张力曲线 tension 0-3 + rhythm(build/peak/release/hold)；**关键修正「上升段才推镜、峰值本身定格」**（用户「第四镜甚至不要动镜头」精确落地）；尾镜钩子语义替代 v1.0 纯 ending 拉远。
- `core_action.py`（②单镜头单核心动作）：动词优先级排序（强动作>表情>姿态）+ 折叠连续动作 + 镜头功能裁决主角 + 动作过程措辞（静态动词→过程描写）。
- `psych_visualize.py`（③心理描写可视化）：心理词探测 → 动作/表情映射库（慌乱/震惊/嘲讽/愤怒/悲伤/恐惧 6 类），**绝不改写 retained_facts 原文**，只进 AI/规则层。
- H3 渲染升级（⑨）：`核心动作：{core_action}` + `表情：{expression}` + 防崩坏双语约束 `_ANTI_BREAK_RULE`（人物五官/结构/画风稳定）+ 构图句（D2 决策）。

**九大模块映射**：①镜头功能=v1.0 shot_role 已有 ②③④新增 ⑤随④自动 ⑥v1.0 spatial+composition_note ⑦Bible/Asset 已有+注入 H3（决策点 D1 建议先只靠参考图锚定）⑧location+must_keep 已有 ⑨v1.0 camera_desc 按 tension 措辞。

**数据模型**：DirectorIntent 新增 4 派生字段 `core_action/expression/tension/rhythm`（+`composition_note` 可选），`from_dict` 全缺省严格向后兼容，ProductionPlan JSON 结构不动。

**实施计划**：P0 三纯规则模块+单测 → P1 decide_camera 接 tension+build_shot_intent 新字段+_build_description 渲染升级（tension=0 默认即 v1.0 行为可一键关）→ P2 script_analyzer 动作拆分细化+心理词预标记 → P3 前端展示（D3 决策：先验证画面再动 UI）→ P4 实机《吐槽成真》4 镜 A/B 对比验收。

**开放决策**：D1 Bible 外观注入方式（建议 B 参考图）D2 构图句双语（建议 A）D3 前端展示时机（建议 B）D4 Dialogue.emotion 反哺表情（建议 A）。

**回滚策略**：新模块全是新增派生字段+新增渲染句，回滚=移除 3 处调用点+渲染层 2 行注释，v1.0 完整还原。

**How to apply**：v2.0 文档 323 行，含 4 镜示范（甩支票→捏支票→「就这？」→顾琰宸愣住）；用户在 SRC 根目录可随时打开对照；拍板后按 P0→P4 落地。

### #611 CAMERA_RULES_V2 修订：Visual Style 层 + 反应镜硬规则 + 预设库（2026-08-17）

**用户深度评审 CAMERA_RULES_V2 v1 版**（拍板①②③三模块继续，核心修正两点）：

**修正 1：缺的不是运镜提示词，是「漫剧镜头语言」→ 新增 Visual Style / Art Direction 层（⑧）**。
Camera Rules=怎么拍，Visual Style=拍出来长什么样。**「画面不对」隐藏根因=视觉基底每镜随机**（动漫/国漫/写实/电影感每镜临时拼）——镜头语言正确但成品不像漫剧；抖音成熟漫剧「大量写实风」= **视觉基底全剧级固定**。新增 `director/visual_style.py`：`VisualStyleProfile` 数据模型（art_direction/style_keywords/fidelity/color_tone/negative_rules/per_scene）+ **STYLE_PRESETS 预设库 7 预设**（douyin_semi_realistic 抖音半写实漫剧【推荐】/japan_cel 日漫赛璐璐/cn_3d 国产3D/pixar/ink_guofeng 水墨国风/korean_thick 韩漫厚涂/cinematic_real 写实电影）。**用户拍板：内置预设+一键选择，全剧统一注入 H3 画风块**（描述区顶部固定前缀 + 双语 + ≤60 字；negative_rules 合并进防崩坏尾部）。

**「抖音半写实漫剧」8 部分拆解**（用户原话结构化，已写入 §3.5）：人物半写实动漫（真人五官+动漫皮肤，真人70%/二次元30%）/ 电影级自然光（cinematic lighting） / 85mm 浅景深 / 低饱和暖调奶油色 / 真实材质（PBR/发丝/真实肤质） / DOF 前景清后景虚 / 电影构图（人物占60%+前景焦点+侧脸留白） / 整体=电影摄影+半写实动漫人物+国漫渲染。英文风格串已内置（Modern Chinese semi-realistic anime…）。

**修正 2：反应镜头提升为硬规则**（§3.4 `plan_reaction_shots`）：v1.0 reaction 是软选择，v2 硬触发——连续对白 `dlg_streak>=2` 或台词爆点（！？/质问词）后**强制下一镜 reaction**，不给听者台词只给表情变化（「顾琰宸瞳孔微缩，表情第一次明显错愕」——一个镜头产生喜剧效果）。落地：beat_rhythm 后执行，输出 `continuity.reaction_forced`，景别锁特写/运动锁静止。

**四层总架构**（§2.1）：Story Bible（谁在什么地方 ✅）/ Beat Rhythm（为什么这么拍 🆕）/ Camera Rules（怎么拍 ✅）/ Visual Style（拍出来长什么样 🆕）——四层合起来才是「自动导演」。模块编号改为用户版九模块（①核心动作 ②心理视觉化 ③张力节奏 ④空间连续 ⑤构图 ⑥人物稳定 ⑦场景稳定 ⑧Visual Style ⑨Camera/H3），shot_role 内嵌不作单列。

**实施计划更新**：P0 加 plan_reaction_shots + visual_style.py（含预设库）；P3 前端预设下拉；决策 D5 已拍板预设库、新增 D7（自定义预设暂不做）。文档约 430 行，双目录 md5 DIFF=0。

1. **r2v 段加新 per-segment 字段** → 必须同步进 `buildTimelinePayload` 白名单（`minimax_timeline.js`），否则"前端填了后端收不到"（#16）。
2. **报告显示 "Text-to AV"** → 第一反应是开关没勾/字段丢失（r2vAutoContinuity 不持久化），不是代码 bug（#7）。重启后多数开关要重勾。
3. **诊断显示"某段开始时的值"** → 必须在段开始捕获快照，不能段末读共享可变状态（#17）。
4. **数字输入框"值自己跳"** → 防抖回调无条件回写显示值 / flush 没跳过 activeElement（#15）。
5. **段 2 复刻段 1 / 动作没推进** → ①前缀是否十二修版本 ②提示词是否写足具体动作动词（#10）。
6. **关闭智能选帧后复刻上一段** → 智能选帧默认开启是稳妥的，别建议关（#18）。
7. **多手/换脸类崩坏不重跑** → 按设计如此（三级双确认），不是 bug；要触发重跑需像素异常（#23）。
8. **FL2VA 段首帧不是上段尾帧** → 检查显式首帧是否短路（显式优先）+ allow_without_continuity（#19）。
9. **Qwen 反馈不可用** → 依赖没装（`RuntimeError 未安装 transformers`）或模型目录不对（`FileNotFoundError`，要含 config.json 的目录）。
10. **Qwen 开关重启后要重勾**（面板开关不持久化的坑同 #7，qwenVlEnabled 字段在 normalizeOutputContinuity 显式列表里）。
11. **改 i18n 只加 ZH**，EN 无对应项（除 continuityMode 那批核心路由字段）。
12. **bash VM 卡死** → 用文件工具直接改部署目录（与源目录逐文件对应）。
13. **导出模式相关**：scene 档下未选段且无缓存 → 报错提示（见 #25）；movie 档需要 ffmpeg（PATH 或 imageio-ffmpeg），缺失时报清晰错误；旧工作流加载后默认 `"scene"`。Scene mp4 / Movie.mp4 直接落在 ComfyUI 输出目录（`{prefix}_{counter}_SceneNN.mp4` / `_Movie.mp4`）。
14. **Scene Manager 相关**：段归属场景写 `seg.sceneId`（camelCase），别写 `scene_id`；新增段级字段必须进 payload 白名单（同 #16）；删场景自动清空该场景下段的 `sceneId`（段不删）；场景排序看 `order` 不看数组下标；「素材库」「导出中心」两个 tab 在 `setActiveNavTab` 切换时才 `syncFromWidgets`，切 tab 才刷新（#26）。
15. **UI 2.0 导航条刷新时机**（#30）：场景增删/段归属变化走 `commit()`（已挂场景栏+镜头条刷新）；镜头选中走 `updateSelectionUI` 尾部（只刷镜头条）。新增"改场景归属"入口时务必在 commit 链路刷，否则导航条不更新。
16. **HTTP 路由不生效**：`http_routes.py` 加新路由后必须重启 Comfy Desktop（PromptServer 路由启动时注册，热更新无效）。
17. **前端块级作用域坑**（#31）：在 `if (isR2v)` 块内用 `const` 声明的 DOM 容器（如折叠分组），后续独立的 `if (isR2v)` 块引用会 ReferenceError——跨块共享的容器必须在 forEach 回调顶部 `let` 声明再赋值。
18. **fl2v 新增 i18n 键**（#32）：`fl2v.promptEmpty`、`panel.fl2v.shotPrompt/shotN` 都是 ZH+EN 双语——卡片摘要行和详情区标题在两种语言下都会显示，缺 EN 键会直接显示 key 名。
19. **生成栏范围与 run-selection 联动**（#33）：`_genScope` 是纯 UI 状态不落 timeline；点「生成」才把范围写入 `runSelection`；`supportsRunSelect()` 为 false 时 `setGenScope`/`updateGenScopeUI` 都会降级为「全部」，否则 UI 显示「当前场景」但 payload 白名单清零实际跑全部；fl2v 场景范围必须过滤 startFrame（`_sceneRunIndices`），否则 `normalizeRunSelection` 清空成空集。
20. **run-status 位置变更**（#33）：运行状态已从节点底部迁到顶部生成栏（`.bd-genbar` 内 `.bd-run-status`），所有 `data-r="run-status"` 引用不变；改布局时别再加回底部旧块（会造成重复进度条）。
21. **高级设置面板高度**（#34）：新增行/组必须同步 `getAdvancedPanelUiHeight`（折叠=46px，展开=头栏+四组 advancedGroupH 累加），否则节点高度与 DOM 不符会裁切；`minimax_director_panel.js` 不能删（`readDirectorQwenFields` 被 advanced_panel import）。
22. **生成下拉范围（#37）**：fl2v「生成当前镜头」对选中非起始段自动映射到起始帧段；pending/failed 计数依赖 `_segCacheStatus`（首次进入未刷新会显示 0，生成后即刷新）；旧 `genbar.scopeSelect` 键和 `.bd-genbar-scope` DOM 已移除勿引用。
23. **段资产候选池场景优先（#38）**：`resolveSegmentAssetPool` 镜像后端 `_scene_asset_pool`——🎬 场景素材组在前、🌐 全局资产按 id 去重补缺；同一素材不会出现在两组；段无 `sceneId` 时只有 🌐 全局组（既定语义）；前端若改池解析顺序，必须与 `gen_timeline.py::_scene_asset_pool` 同步。
24. **衔接模式单一数据源（#39）**：r2v 卡片衔接模式只由顶部徽章下拉（`buildContinuityStrip`）控制，折叠区 cmRow 已删；别往 `cfgGroupCont` 加第二个衔接 select。**P5 修正后布局**：衔接徽章 + Prompt 放 body 右列（`r2vMain`），参考素材左列，`assetSelRow` 标题下方常显，`cfgWrap` 沉底——改顺序时注意：r2v 的 Prompt 先建后挂进 `r2vMain`、非 r2v 仍在 media 之后挂载（grid 布局依赖 append 顺序）；`assetSelRow` 是立即挂、`cfgWrap` 是延迟挂，子元素仍在原位置创建，别把子元素挂进未进 DOM 的容器。
25. **导出内存崩溃（#45）**：`movie`/`scene` 模式场景内合并会先过 `_scene_merge_fits`，放不下自动降级 ffmpeg 直拼（每镜 mp4 已落盘，不会丢镜头）；但**每镜 mp4 / 场景 mp4 落盘都在输出目录**，用户可在 ComfyUI 输出目录找到 `Director_SceneNN_ShotMM.mp4` 直接下载——「全片导出（movie）」的内存合并只发生在场景级，不是全片级；再次遇到导出失败时先用 `tools/recover_segment_cache.py` 从 `output/minimax_seg_cache` 找回，不要重跑 4 小时。
26. **单镜下载按钮状态（#46）**：下载按钮可用性只看 `_segCacheStatus`（成功绿点）；生成中/失败/未生成都禁用。若用户生成成功但按钮仍灰，先怀疑 `refreshSegmentCacheStatus` 没刷新（首次进入显示 0 是既定行为），点一下状态灯刷新链路或重开卡片。后端路由 `segment_mp4` 首次点击会编码落盘 `seg_%04d.mp4`（需要几秒到几十秒），之后秒回；期间不要连点，临时文件按 pid 区分不会写坏。**缓存无音频的旧镜（#45 之前）下载出来是静音视频，属正常**——新生成才带音频缓存。
27. **SPA 别把 `"auto"` 写进 timeline 段 taskType（#73）**：后端 `SUPPORTED_TASK_KEYS` 只有 `t2v/i2v/fl2v/r2v/v2v/rv2v`，`resolve_task_key("auto")` 原样返回 → executor 抛 `Task 'auto' is not supported`。auto 的正确表达 = **不写段 taskType 字段**（段继承全局，如 r2v），auto 智能路由由 `continuityMode` 独立承担（阶段 D 关键词路由）。SPA 已把 auto 归一化为空串并条件省略字段；后续若新增 Adapter/生成路径，务必遵守「taskType 只写具体模式、auto 用省略表达」。
28. **单镜生成播整段（#81）**：`exportMode:"all"` 时 SaveVideo 输出全片（未选段走缓存/passthrough 填充）——单镜范围必须传 `exportMode:"segments"` 让后端只输出该镜；**别用 segment_mp4 兜底**（缓存可能未落盘会回退全片）。任务记录 finalVideo 与播放器一致；想看成片点「生成全部/场景」。
29. **单镜重生成 r2v/fl2v 段报「段间连贯」（#82）**：根因①已跑段不写回 `completed_outputs`（已修：run 循环补写回）；②缓存指纹含全局字段（`continuity_enabled` 前端对非 fl2v 清零 → stale）。续接取锚点已改 `load_segment_cache(..., allow_stale=True)`（打 warning 不拒绝）；**合并/透传路径仍严格拒 stale**。若仍报「段间连贯」= 前驱段从未生成且无缓存（非 stale），先生成前驱或「生成全部/场景」。改 segment_cache/executor 后**必须重启 Comfy Desktop**。
30. **生成中播放入口被锁（#83）**：播放可用性只看该镜 `isShotCached`（缓存成功绿点），与 `wb.running` 无关——生成中可回看其它已完成的镜头；`playVideo` 别再加 `wb.running` 守卫（正在生成且无缓存的镜头已被 `collectPlayableShotIds` 按 cached 集合过滤）。若「打开成片」可看但「播放此镜」灰，先查 `segStatus` 是否含该镜 cached（成功绿点），不是 running。
31. **单镜下载报 `Could not determine output format`（#84）**：`save_segment_mp4` 的 `av.open` 必须显式 `format="mp4"`，别依赖文件扩展名——`segment_mp4` 路由的临时文件是 `seg_XXXX.mp4.tmp.{pid}`（原子替换前不叫 `.mp4`），扩展名推断会 500。新增编码调用点时若传了非 `.mp4` 结尾的路径（含 tmp/临时文件），务必带 `format="mp4"`。改 stream_export 后**必须重启 Comfy Desktop**。
32. **Ollama 后端（#121）**：V1.7 文本后端首选 `OllamaBackend`（`http://localhost:11434`，模型 `qwen3:14b`）——Ollama 是**独立进程**，`del model / gc.collect()` 只对 Transformers 语义有效；显存互斥靠「session 退出 → keep_alive=0 卸载 → `/api/ps` 确认 qwen 不再驻留 → `torch.cuda.empty_cache()` → registry empty → 允许 H3」进程级链路。Ollama 没启动（`ollama serve`）时 `preflight()` 会抛「Ollama 服务不可达」拒绝进入 session。**别把 Ollama 模型放 `ComfyUI/models/text/`**（那是 LocalQwenBackend 备用路径）；`create_default_text_backend()` 永远返回 OllamaBackend，除非显式构造 LocalQwenBackend。
33. **pytest 收集根包报 torch 缺失（#124）**：`director/__init__.py`（ComfyUI node 入口）会 `import torch`，pytest 收集整包/子测试时 15 errors（test_script_parser / test_text_backends 同样受影响）——**项目既有 pytest 收集基建问题，不是某个测试引入**；`--import-mode=importlib` 与 conftest.py 都无法绕过。**项目惯例 = 直接 `python3 director/tests/test_xxx.py` 运行测试文件**（无第三方依赖），别再为 pytest 加 conftest。

### #612-#616 P0 实施：《AI 漫剧导演规则 v2.0》四纯规则模块落地（2026-08-17，任务 #615-#621）

**背景**：CAMERA_RULES_V2 定稿后用户拍板「开始 P0 实施（推荐）」。P0 = 四个**纯规则模块**全部落地（零 LLM / 零显存 / 零网络），为 P1 接线（decide_camera 接 tension + build_shot_intent 新字段 + _build_description 渲染升级）铺路。**硬约束**：`retained_facts` 原文逐字保底绝不覆盖；新增派生字段 from_dict 全缺省向后兼容，ProductionPlan JSON 结构不动。

**新增模块（director/ 下 4 文件 + 4 测试文件）**：

1. **`core_action.py`（① 单镜头单核心动作）**：动词优先级 `_STRONG_VERBS`（强动作，含「抬到/抬起/抬了」）> 表情动作 > 姿态 > 环境；`_fold` 同主语连续强动作折叠成过程链（最多并 2 个）；`_processify` 静态/结果性动词→过程措辞（「握拳」→「垂在身侧的手缓缓收紧，指节逐渐绷紧」）；`best_cls>=2`（姿态/环境）不选核心。**核心修正（比文档更严）**：一镜只留一个核心动作，视线类小动作省略。测试 `test_core_action.py` **21 PASS / 0 FAIL**。
2. **`psych_visualize.py`（② 心理描写视觉化）**：`PSYCH_MAP` 心理词→(表情变化, 动作过程) 映射库（慌乱/紧张/震惊/嘲讽/愤怒/悲伤/害怕/心动/轻蔑 等），`visualize_psych` 出表情句、`psych_process_action` 出动作过程短语；无心理词→空串（渲染层跳过，向后兼容）。**铁律**：只返回派生句，绝不改写 `source_text`/`retained_facts` 原文。测试 **26 PASS / 0 FAIL**。
3. **`beat_rhythm.py`（③ 张力节奏 + ③′ 反应镜硬规则）**：`plan_rhythm` 按 `resolve_timeline_order` 全链遍历出 `tension`(0-3)+`rhythm`(build/peak/release/hold)；**关键修正落地：上升段（2→3）才判 peak（定格静止），不是越高越推**；`_ROLE_TENSION_BOOST` establish 压低/ending 收束。`plan_reaction_shots` 硬规则：连续对白 `dlg_streak>=2` 或台词爆点（！？/质问词）→ **下一镜强制 reaction**（该镜须有角色；P1 渲染：只给表情不给台词）。测试 **28 PASS / 0 FAIL**。
4. **`visual_style.py`（⑧ Visual Style 预设库）**：`VisualStyleProfile` dataclass（profile_id/art_direction/style_keywords/fidelity/color_tone/negative_rules/per_scene，to_dict/from_dict round-trip）+ `STYLE_PRESETS` 7 预设（**douyin_semi_realistic 抖音半写实漫剧【推荐】**/japan_cel/cn_3d/pixar/ink_guofeng/korean_thick/cinematic_real）+ `style_block(profile, scene)` 固定画风块。douyin 预设含用户英文风格串逐词保留（Modern Chinese semi-realistic anime…Ultra HD）+ 8 部分拆解（真人70%/二次元30%、电影级自然光、85mm 浅景深、低饱和暖调、PBR 真实材质、Soft bokeh、电影构图、国漫渲染）；`per_scene` 场景级微调（雨夜/回忆）只追加不改总纲。**全剧级一致性**：同一 profile 每镜画风块完全相同（不静默漂移）。测试 **59 PASS / 0 FAIL**。

**数据模型**：DirectorIntent 新增 `core_action/expression/tension/rhythm/composition_note` + `continuity.reaction_forced`（P1 才接线赋值），`from_dict` 全缺省——旧项目/旧 JSON 零改动。

**验证（全量回归）**：新单测合计 **134 PASS / 0 FAIL**；`director/tests/` 全部 **54 个测试文件退出码 0**（含 test_director_rules / test_h3_prompt_builder / test_camera_template / test_spatial_continuity 等既有回归，**0 破坏**）。每个新模块 `test_no_gpu_side_effects` 源码级检查无 torch/ollama/网络引用。

**How to apply**：P1 接线点已锁定——① `director_rules.decide_camera` 接 `tension` 参数控制运镜措辞（tension=0 默认即 v1.0 行为可一键关）；② `build_shot_intent` 调 `core_action.extract_core_action` / `psych_visualize.visualize_psych` / `beat_rhythm.plan_rhythm` + `plan_reaction_shots` / `visual_style.style_block` 填充新字段；③ `_build_description` 渲染升级按 CAMERA_RULES_V2 顺序（画风块→核心动作→表情→构图→运镜→防崩坏尾部）。`visual_style` 预设选择落前端（P3 预设下拉，D7 自定义预设暂不做）。

### #617-#626 P1 实施：《AI 漫剧导演规则 v2.0》四模块接入真实生成链路（2026-08-17/18，任务 #622-#626）

**背景**：P0 四个纯规则模块验收通过后用户拍板「验证通过开始P1」。P1 = 把 core_action / psych_visualize / beat_rhythm / visual_style 接进真实生成链路（decide_camera → build_plan_intents → _build_description → /prompt/h3）。**硬约束**：tension=0 默认逐字节兼容 v1.0（一键可关）；style_profile=None 不注入画风块/防崩坏；`retained_facts` 原文逐字保底；纯规则零 LLM/零显存/零网络。

**改动**：

1. **`director_rules.decide_camera` 接 `tension` + `reaction_forced`（P1-A，任务 #623）**：模板选择后、`_apply_continuity` 前插入 v2.0 决策块——`reaction_forced=True` → `_reaction_template`（景别锁 close_up / 运动锁 static）；`tension>=3` → 峰值定格 peak_hold（特写+静止，**已特写静止则原样不替换不重复措辞**）；`tension==2` 且 movement==static 且 role 非 establish/ending → 上升缓推 rising_push（保留原景别，static→push_in）。`next_state` 从 adjusted 读取自动同步新模板的运动/景别，跨镜状态机正确。
2. **`build_plan_intents` + `build_shot_intent` 接四模块（P1-B，任务 #624）**：DirectorIntent 填 `core_action`（`extract_core_action` + 空则 `psych_process_action` 补位）/`expression`（`visualize_psych`）/`tension`+`rhythm`（`plan_rhythm`）/`reaction_forced`（`plan_reaction_shots`，命中镜 role 覆盖为 reaction 且 continuity 写 `reaction_forced=True`，**仅 forced 写键不膨胀**）；provenance 派生字段全 `IntentSource.RULE`。旧链路（无计划/规则失败）全缺省空/0 不变。
3. **`_build_description` 渲染升级（P1-C，任务 #625）**：新增 `_ANTI_BREAK_RULE` 双语防崩坏固定句 + `_merge_negative_rules`（`_ANTI_BREAK_KEYS` 主题命中跳过默认负面，自定义保留，dict 形式兼容）。注入顺序=画风块（`style_profile` 非 None，在 head 的 location 之后）→核心动作句→表情句（与 retained_facts 逐字重复或 ≥0.7 相似则跳过）→防崩坏尾部（NO_SUBTITLE_RULE 之前）。`build_plan_h3_prompts` 透传 `style_profile` + `scene_name=intent.location`；`http_routes.py` `/prompt/h3` body 读 `style_profile`（None 或 dict，非法 400 bad_style_profile）。
4. **synthetic 模板中文景别兜底（#626 回归修复）**：peak_hold/rising_push/reaction 不在 `_TEMPLATE_INDEX`，`_camera_cn` 映射不出中文 → `camera_position`/`movement` 空（test_story_analyzer_intents 2 失败暴露）。修复=`build_shot_intent` 新增 `camera_shot_size`/`camera_movement` 兜底参数（build_plan_intents 传 next_state 结构化枚举 → `_SHOT_SIZE_CN`/`_MOVEMENT_CN` 直接映射）；`next_state=None` 初始化防双回退失败 NameError。tension=0 旧模板幂等不受影响。

**验证**：`director/tests/test_v2_p1_integration.py`（新增）**141 PASS / 0 FAIL**（覆盖 tension=0 逐字节兼容 / reaction_forced / peak_hold / rising_push / 四模块接线 / to_dict-roundtrip / 画风块+防崩坏渲染 / negative 去重 / style_profile 透传 / 零显存源码检查 / synthetic 中文兜底）；全量回归 `director/tests/` **55 个测试文件退出码 0**（test_story_analyzer_intents 修复后 62/62）。双目录 SRC≡DEP **md5 DIFF=0**（排除 node_modules/dist）。

**How to apply**：tension/reaction_forced 目前由 `plan_rhythm`/`plan_reaction_shots` 纯规则自动推导（全链无开关）；style_profile 尚未接入前端（P3 预设下拉再给 `/prompt/h3` 传 body）。前端不传 style_profile 即旧链路逐字节不变。防崩坏固定句只在 style_profile 非 None 时注入（不改变默认无画风约束的历史行为）。

### #627-#630 P2 实施：《AI 漫剧导演规则 v2.0》输入质量提升（2026-08-18，任务 #627-#630）

**背景**：P1 全链验收通过后用户拍板「开始P2」。P2（CAMERA_RULES_V2 §7 P2 行）= `script_analyzer` 升级：**① actions 拆分更细**（一条一动作）；**② 心理词预标记**（软约束转硬输入）。验收 = 新剧本《吐槽成真》4 镜样例导入，DirectorIntent 4 字段（core_action/expression/tension/rhythm）正确派生。**硬约束**：纯规则零 LLM/零显存/零网络；`retained_facts` 原文逐字保底绝不覆盖；Qwen 语义只补全不覆盖规则动作。

**改动**：

1. **`script_analyzer.py` actions 拆分细化（#627-628）**：Qwen 常把多动作塞一条（「捏起支票，缓缓抬到眼前」）。新增纯规则 `_split_actions`（`_ACTION_SEP_RE` 标点正则 + `_ACTION_CONJUNCTIONS` 连词拆分，`_ACTION_MIN_LEN=2` 最短守卫，去重、幂等、空条目跳过）；`_merge_shot` 里 `actions = _split_actions(list(shot.actions) or qwen_actions)` 对**规则动作和 Qwen 动作都过拆分**。batch/single 模板 actions 描述升级：「每条只含一个动作」+「捏起支票，缓缓抬到眼前」必须拆两条 + 衣物/配饰飘动归 actions 不作 costume 实体（**single 模板里该指令必须同行书写，换行会破坏字符串匹配——P2 测试 1 失败暴露**）。
2. **心理词预标记（软约束转硬输入，#629）**：`psych_visualize.py` 新增 `tag_psych(shot)`/`tag_psych_text(text)`（source_text→emotion→actions→visual_intent 多源扫描保序去重）；`_psych_hit` **硬输入优先**——先读 `shot.psych_tags` 命中即返回，无预标记才回退运行时扫描（旧行为逐字节保留）。`production_plan.Shot` 新增 `psych_tags: List[str]` 字段（to_dict/from_dict 往返，旧数据缺省 [] 向后兼容）。`script_analyzer._merge_shot` 合并后 `psych_tags=tag_psych(merged)` 预标记落盘；`story_analyzer._build_shot` 小说导入路径同样预标记（try/except 兜底 []）。
3. **《吐槽成真》4 镜全链集成（#630）**：新 `director/tests/test_v2_p2_script_input.py` **108 PASS / 0 FAIL**（11 测试函数：_split_actions 单测 / tag_psych 多源 / 硬输入优先 / merge 拆分+预标记 / Shot 往返 / 小说路径预标记 / prompt 拆分指令 / 4 镜全链 / 爆点 peak 路径 / 零显存源码检查）；`test_psych_visualize.py` 追加 P2 覆盖 **44 PASS / 0 FAIL**。全量回归 `director/tests/` **56/56 文件退出码 0**。双目录 SRC≡DEP **md5 DIFF=0**。

**设计发现（汇报时需向用户说明）**：
- **镜3 emotion='轻蔑' → tension=1 而非 3**：`beat_rhythm._base_tension` **emotion 关键词优先于爆点词**（轻蔑→1 先于「就这」→3）。这是既有锁定逻辑；独立 `test_bomb_peak_path` 验证 emotion 留空时爆点词驱动 → tension=3/peak。
- **镜2 被 v1.0 规则判为 reaction**：无对白 + 上镜有对白 → `assign_shot_role` 判 reaction（「听比说重要」，test_director_rules 锁定行为）；reaction 镜只挑表情动作，`[林雪推开店门,环视堂内]` 均非表情动词 → core_action 空（惊讶不在 PSYCH_MAP 无补位）。**若希望镜2 走 action（推门进店），属 v1.0 `assign_shot_role` 反应判定细化（例如同角色续动作不判 reaction），超出 P2 范围，留给后续决策**——强动作路径已由镜3（拿起菜单）与 test_bomb_peak_path shot_01（action 镜）覆盖验证。

**How to apply**：P2 不引入开关，分析阶段即固化拆分+预标记；老镜头重新导入/重新分析才带新 actions/psych_tags（已落盘 plan 不追溯）。`psych_tags` 是纯新增字段，旧 Project JSON 无该键默认 [] 不崩。动作拆分是规则层，永远在 Qwen 语义补全之后生效、且绝不覆盖原文。

## 待办 / 未来方向

- **待办⑥ Scene Manager（导演系统一级对象）已落地**（#72-77）：场景管理/素材库/导出中心三个 tab + 每场景导出 + 全片导出。后续可扩展：Ref2VA 参考图按场景注入、Qwen 状态跟踪按场景聚合、Prompt 按场景批量改写。
- **里程碑 C 重跑触发分支**（规则 abnormal 时换种子重跑）机制已实现，但用户实测未触发（防误判验证）；等待画面同时含像素异常时自然触发验证。
- **未来精确评分**：Qwen 负责理解、CLIP/DINO 负责算相似度——加 `clip_score`/`dino_score` 模块做视觉 embedding（本期只留接口）。
- **手/脸目标检测计数**（多手/多脸也触发重跑）——用户已决定**暂不做**（保持双确认轻量）。
- **Qwen 提取状态不准** → 用户手填优先，Qwen 兜底（已有）；可考虑让用户一键采用 Qwen 提取结果回填 state_change。
- 设计文档 STATE_MANAGER_DESIGN.md 第 7 节 UI 示意图是完整 State Manager 愿景，后续功能按它演进。



### #631-#633 P3 实施：《AI 漫剧导演规则 v2.0》Visual Style 预设下拉（2026-08-18，任务 #631-#633）

**背景**：P2 全链验收通过后用户拍板「开始P3」。P3 = 前端 Visual Style 预设下拉（7 预设，默认「抖音半写实漫剧」）+ 当前 profile 预览。已裁定决策：D3=B、D5=内置预设库+一键选择、D7=B。方案 C：后端 GET /minimax/director/style/presets 返回 7 完整预设 dict（单一事实来源），前端面板拉取→下拉选择→完整 profile dict 存 Project.styleProfile→生成提交透传 /prompt/h3 的 style_profile。**硬约束**：纯规则零 LLM/零显存/零网络；style_profile=None 旧链路逐字节不变。

**改动**：

1. **后端预设库路由（#631）**：`visual_style.py` 新增 `presets_payload()`；`http_routes.py` 注册 `GET /minimax/director/style/presets` → `{"presets": ..., "preset_version": "visual-style-v1"}`。`test_visual_style.py` `test_presets_payload()` 68 PASS。
2. **前端数据模型与类型（#632）**：`comfyApi.ts` 定义 `VisualStyleProfileJson` + `StylePresetsResult`；`buildH3Prompts` 第 5 参类型修复为 `VisualStyleProfileJson`（原 `Record<string,unknown>` 触发 TS2345）；新增 `fetchStylePresets()`。`project.ts` `Project.styleProfile?`。
3. **core/visualStyle.ts 纯函数层（新建）**：`DEFAULT_STYLE_PROFILE_ID='douyin_semi_realistic'`；`DEFAULT_STYLE_PROFILE_JSON`（douyin 完整兜底，含 85mm/浅景深/电影级英文串，四组关键词/双语 negative/per_scene 雨夜·回忆）；`normalizeStyleProfile`/`stylePresetOptions`/`fidelityLabel`/`colorToneLabel`/`styleProfileSummary`/`perSceneLabels`。
4. **store 与面板（#632）**：`workbench.ts` `getStyleProfile`/`setStyleProfile`/`resetStyleProfile`/`normalizeStyleProfile` + `loadProject` 归一化；新建 `VisualStylePanel.vue`；`WorkbenchView` 挂载面板 + 3 处 `buildH3Prompts` 传第 5 参。
5. **前端测试（#633）**：`tests/visualStyle.test.ts` 22 PASS；前端 vitest **588 PASS**（批 A 261 + 批 B 327）+ `npm run build` 通过。

**验证**：后端 56 测试文件退出码 0；前端 vitest 588 + build 绿；双目录 SRC≡DEP **md5 DIFF=0**（326 文件）。

**How to apply**：`project.styleProfile` 存完整 dict 而非 profile_id；`getStyleProfile()` 归一化保证默认恒成立；`DEFAULT_STYLE_PROFILE_JSON` 与后端 douyin 双份维护唯一例外；后端 `/style/presets` 需重启 Comfy Desktop；生成链路传 `buildH3Prompts` 第 5 参。下一步 P4 = 实机《吐槽成真》4 镜 A/B。

---

## #640-643 剧本导入反馈：时间线原文摘要 + 应用后横幅（2026-08-18）

**背景**：用户导入《吐槽成真》应用后，SPA 工作台顶部 Timeline 视图看起来像「旧剧本缓存」。诊断结论：**导入链路无 bug**——统计条「4 镜 · 1 地点 · 共 0:20」精确匹配新剧本；`productionPlanToProject` 全新构建 Project；`loadProject` 完全替换。误判根因=UX：①「0 人物」（castIds 仅在用户确认资产绑定后写入，defaultCastId 从不设置，resolveInheritance 无回退）②空卡片无缩略图、shot_01~04 与旧项目视觉不可区分。用户拍板两处 UX 改进。

**改动**：

1. **核心纯函数层（新建 `frontend/src/core/importFeedback.ts`）**：
   - `planCastNames(plan)`：剧本全部角色名（遍历 scenes→shots→characters，去重保序，空白名过滤）。
   - `pendingCastNames(plan, confirmed)`：剧本角色 − 已确认绑定（`kind=cast` 且 `image_file` 非空，与 `productionPlanToProject.buildMatchIndex` 过滤规则一致）→ 待绑定角色列表。
   - `sourceSummary(shot, maxLen=20)`：从 `shot.description`「原文：」段提取前 maxLen 字（超长截断加省略号），无原文 → 空串（不渲染该行）。复用 `rebuildPlan.extractSourceText`。
2. **时间线卡片原文摘要（#641，`WorkbenchView.vue`）**：`.tl-info` 中 `.tl-meta` 后新增 `tlSummaryOf(s)` 摘要行（`sourceSummary(shot, 24)`，flex-basis:100%，11px 灰蓝）。导入项目一眼可见新剧本内容，与旧项目区分。
3. **应用后顶栏横幅（#642，`WorkbenchView.vue`）**：`applyScriptProject` 成功块设置 `importBanner`（title=项目名、N 镜 · N 地点、待绑定角色列表 `pendingCastNames(res.plan, confirmed)`）；横幅模板位于 `</header>` 后（✅ 徽标 + 标题 + 元信息 + ⚠ 待绑定角色 chip + 提示语 + ✕ 关闭）；样式 `.import-banner` 渐变绿 + `.ib-*` 子元素。

**验证**：`tests/importFeedback.test.ts` 11 PASS（planCastNames 3 + pendingCastNames 4 + sourceSummary 4）；前端 vitest 全量 **32 文件 599 PASS / 1 skipped**（原 588 + 新增 11）；`npm run build` 绿；双目录 SRC≡DEP **md5 DIFF=0**（importFeedback.ts / importFeedback.test.ts / WorkbenchView.vue / dist 全部一致）。

**How to apply**：导入剧本应用后——时间线卡片出现「镜头 N + 原文前 24 字」摘要；顶栏绿色横幅提示「已应用《吐槽成真》：4 镜 · 1 地点 · 角色待绑定参考图」。角色绑定参考图后重新导入，横幅 pendingCasts 相应减少。纯前端改动，无需重启后端；SPA 用 vite dev（5173）热重载或替换部署版 dist。下一步 P4 = 实机《吐槽成真》4 镜 A/B 继续。

---

## #644-651 生成约束层 v1.0：Prompt Compiler 全链路（P0+P1+P2 收官，2026-08-18）

**背景（请求 B）**：用户「画风可以了，但提示词约束弱」——失败案例=办公室 3 人群像（2 个相同角色同画面）。8 层约束规范：Visual Style / Scene Lock / Character Lock（含 Character Count 硬约束）/ Spatial Lock / Action Timeline / Camera Lock（一镜一摄影机状态）/ Performance Lock（禁止动作）/ Generation Constraints（禁止清单）。六项优先=Character Lock + Character Count + Spatial Lock + Action Timeline + Camera Lock + Forbidden Conditions。设计文档 `PROMPT_COMPILER_V1.md` 已拍板 4 项：外观来源=Qwen 导入产出；站位来源=规则推导+扩展；景别冲突=警告+提示拆镜；交付范围=纯后端先行。

**P0（#646-648，纯规则模块零 LLM 零显存）**：`shot_plan.py`（Shot Planning，`build_shot_plan(intent, appearance_map=None, role_map=None)`→ShotPlan dict，含 character_count/character_locks/spatial/action_timeline/camera/forbidden）+ `prompt_compiler.py`（`compile_constraint_block(ShotPlan)` 渲染双语约束块，外观锁定行已内建）+ `constraint_checker.py`（`build_constraint_check` 报告，多景别 warning「请拆成两个 Shot」）。

**P1（#649，接线真实生成链路）**：`director_intent.py` `build_shot_intent/build_plan_intents` 填 `intent.constraint`（provenance.constraint=RULE）；`h3_prompt_builder._build_description` 角色>0 时注入双语约束块（角色锁定/动作顺序/镜头锁定/禁止/外观锁定）+ rule_generated 记「约束块」；无 constraint 向后兼容不注入。`test_constraint_wiring.py` 覆盖失败案例回放真实接线 32 PASS。

**P2（#650，Qwen 角色外观档案——本次）**：`production_plan.py` Character 加 `appearance: str=""`、ProductionPlan 加 `character_profiles: Dict[str,str]`（to_dict/from_dict 往返）。`script_analyzer.py` 场景模板加可选顶层 `character_profiles`（Task C 铁律=只从原文+合理外观推断，绝不自行创造服装）+ `_clean_character_profiles` 归一化（canonical trim+lower、空值丢弃、先到先得）+ `_merge_characters` 外观合并优先级「规则 Character.appearance > Qwen 单镜 > appearance_map」只补空绝不覆盖 + `_fallback_appearance` 规则兜底（costumes+role，仅单角色镜头配对防张冠李戴）+ `analyze_script` 聚合（既有 plan.character_profiles 持久化权威 > Qwen setdefault > 规则兜底）。`director_intent.py` `build_plan_intents` 从 plan.character_profiles 建 appearance_map 接线 `build_shot_intent`。附带修复：analyze_script 之前丢失 timeline/beats（pre-existing gap）→ 现已保留。约束块渲染「外观锁定：柳如烟=青衫襦裙，发髻簪钗，全程不变。」+「Appearance lock: 柳如烟 — …. Unchanged throughout.」双语。

**测试**：约束层+analyzer 全绿 **760 PASS / 0 FAIL**——test_script_analyzer 128（P2 新增 5 函数 24 断言）、test_constraint_wiring 37（P2 外观锁定 5）、test_shot_plan 22、test_prompt_compiler 8、test_constraint_checker 11、test_director_intent 21、test_camera_plan 50、test_plan_beats 42、test_v2_p1_integration 141、test_v2_p2_script_input 108、test_h3_prompt_overrides 29、test_prompt_builder 80、test_camera_template 83。

**验证**：双目录 SRC≡DEP **md5 DIFF=0**（production_plan.py / script_analyzer.py / director_intent.py / test_script_analyzer.py / test_constraint_wiring.py 5 文件一致）。

**How to apply**：Qwen 导入剧本→场景理解产出角色外观档案→聚合进 ProductionPlan.character_profiles→build_plan_intents 注入 CharacterLock.appearance→H3 Prompt 双语外观锁定。已有存档（持久化 character_profiles）恒优先；Qwen 缺外观时走规则兜底（costumes+role 单角色镜头）；全空则约束块只锁名字+数量不臆造外观。P3 前端 Visual Style 预设下拉在 #631-633 已并行的背景下，P4=实机《吐槽成真》4 镜 A/B 继续。

---

## #652 新建项目 405 修复（2026-08-18）：部署目录缺 3 个约束层模块致插件加载失败

**现象**：MiniMax Studio「新建项目」→ `POST /minimax/director/projects` → **405 Method Not Allowed**。

**根因**（用户报告「405」，不是 404，也不是路由注册机制问题）：`D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\custom_nodes\ComfyUI_MiniMaxH3_Director`（DEP 部署目录）在 P1/P2 同步时**只覆盖了既有文件、漏掉了 3 个新增 .py**——`director/shot_plan.py`、`director/prompt_compiler.py`、`director/constraint_checker.py`（及对应 3 个测试）。而 `director_intent.py` 已更新为 P1 版，第 28 行顶层 `from .shot_plan import build_shot_plan` → DEP 缺 `shot_plan.py` → **`ModuleNotFoundError`** → `__init__.py` 里 `from .nodes.director import ...` 未包 try/except 直接抛 → **整个插件 import 失败** → ComfyUI 记「IMPORT FAILED」继续启动 → **所有 /minimax/director/* 路由一个都没注册** → POST 落到 `web.static('/', web_root)` 兜底（GET/HEAD only）→ 405（GET 则是 404，SPA 未触发所以用户只看到 POST 报错）。

**修复**：SRC→DEP 补齐 8 个文件并清 `__pycache__`（cpython-310/313 旧字节码）——`shot_plan.py` / `prompt_compiler.py` / `constraint_checker.py` / 3 个测试 + 内容漂移的 `http_routes.py`（P1 constraint_check 块）`h3_prompt_builder.py`。**md5 DIFF=0**。stub 模拟 ComfyUI 加载链全通（director_intent→shot_plan / http_routes→constraint_checker / h3_prompt_builder）。测试全绿：test_shot_plan 22 / test_prompt_compiler 8 / test_constraint_checker 11 / test_constraint_wiring 37 / test_director_intent 21。

**⚠ 教训**：同步 SRC→DEP 不能只按「改动过的文件列表」拷贝——必须 `diff -rq` 全量核对**新增文件**。新增模块被既有文件 import 时，漏拷=整插件加载失败（表现为 405/404 而非 500）。

**用户动作**：需**完全退出 ComfyUI Desktop 后重启**（进程内仍持有失败的 import 状态；热重载/重载前端无效），再新建项目验证。
