# H3 当前链路审计报告

> 日期：2026-08-13 · 审计人：Claude（基于真实代码阅读，非假设）
> 背景：V1.7 Golden Path 已交付，进入真实使用打磨阶段。本报告回答"用户自然语言 → Director → H3 Prompt"这一层的现状与问题。

---

## 一、链路总览：每一步由谁负责

```
用户中文剧本 / 短篇故事
  │
  ▼ ① 剧本输入        http_routes.py POST /script/import → script_parser.parse_script（纯规则拆 Scene/Shot）
  │                    原文逐字保留在 shot.source_text（_SOURCE_TEXT_MAX=600 截断进 Qwen）
  ▼ ② Qwen 语义理解    script_analyzer.py analyze_script（OllamaBackend qwen3:14b，/api/generate）
  │                    _SCENE_ANALYZE_TEMPLATE：Task A 剧本事实 + Task B 视觉推断 + visual_intent 一句话
  ▼ ③ 实体解析清洗      entity_cleanse.py build_entity_registry（纯规则：伪实体剔除/类型纠正/去重/置信度门控）
  ▼ ④ 资产匹配          asset_matcher.py match_plan（纯规则三级打分）+ asset_registry.py（persisted binding）
  ▼ ⑤ AI Draft 映射    productionPlanToProject.ts（前端）→ Shot.content 五区中文草稿 + aiDraft=true
  ▼ ⑥ Prompt 生成      后端 prompt_builder_v17.py build_plan_drafts（POST /prompt/draft，五区中文草稿）
  │                    前端 promptSections.ts buildShotPromptText（四区原样拼接，实际生成链路用）
  ▼ ⑦ Camera 运镜       camera_template.py pick_camera（15 模板 + 跨镜状态机，按镜头目的选择）
  ▼ ⑧ Continuity        executor_core.py state_holder（文字状态前缀）+ _build_minimax_inputs（prev_tail 续接）
  │                     segment_continuity.py（SCAIL，仅 Wan 链路生效，H3 为 no-op）
  ▼ ⑨ Workflow         前端 Workflow Registry/Studio → timeline_data JSON → Director 节点
  ▼ ⑩ H3 生成           nodes/conditioning.py run_minimax_conditioning → core_sampling.sample_single_stage
  ▼ ⑪ Generation       前端 createGeneration → POST /generations/archive → Shot.generations[] + activeGenerationId
  ▼ ⑫ Shot UI           WorkbenchView.vue（唯一大播放器 + 版本条 + TaskCenter）
```

---

## 二、逐层现状与问题

### ① 剧本输入（script_parser）— ✅ 正确，保持不动

- 纯规则拆 Scene/Shot，`source_text` 逐字保留 = **用户原文的永久保存层**。
- P0-2 双轨制兜底（动作主语/道具在规则层先建候选，Qwen 只增不删）已验收。
- ⚠️ 小问题：`duration_sec` 规则猜 [2,8] 秒，用户没表达过时长；导入时前端再 clamp 2-8 秒，超 8 秒静默截断无提示。

### ② Qwen 语义理解（script_analyzer）— ⚠️ 核心问题区

现状：Qwen 读每镜截断到 600 字的原文，输出固定 JSON 字段（characters/entities/visual_elements/actions/emotion/visual_intent/dialogue）。

发现的问题：
1. **`visual_intent` 一句话压缩**：用户整镜构图/氛围/潜台词被压成一句 Qwen 草稿，且与最终 content.visual 隔了两层转换（Phase 3 草稿 → 前端拼接），任何一层都可能稀释意图。
2. **Task B 明确授权"合理视觉推断"**：Qwen 被允许补用户没写的内容（视觉元素只进 Prompt 不进资产匹配，风险受控，但意图外内容确实进入了最终 Prompt）。
3. **`_SOURCE_TEXT_MAX=600` 截断**：长镜头尾部原文 Qwen 看不到，语义补全基于残缺文本。
4. **台词 speaker 由 Qwen 回填**：复杂句说话者靠猜；`_merge_dialogues` 还会追加 Qwen 幻觉台词（Prompt 里"不得改动台词原文"是软约束）。
5. **`role` 字段纯属 Qwen 创作**：过度解读风险。

结论：结构性设计是"Qwen 输出只是增强草稿，人工最终审核"。意图丢失的主因是**下游 Prompt 层不消费 source_text 原文**，只消费 Qwen 结构化字段。

### ③ 实体解析（entity_cleanse）— ✅ 正确，保持不动

纯规则清洗（伪实体/称谓/类型纠正/canonical 去重/置信度门控/漏斗），P0-2~P0-3b 全绿验收。注册表种子与 Qwen 实体二次合并是唯一轻微风险（别名归一已部分缓解）。

### ④ 资产匹配（asset_registry）— ✅ 正确，⛔ 铁律保持不动

- Stable Entity Key + Alias 归一 + persisted binding 已验收（真实验收 48/48 + 两次导入 7/7 persisted 复用）。
- **用户明确要求：不因本次 Prompt 优化推翻 Phase 2-2 逻辑。**

### ⑤ AI Draft 映射（productionPlanToProject）— ✅ 基本正确

visual_intent → description（只读备注，不进 content.visual，用户拍板）。五区草稿 + `aiDraft=true` + 审核门控（`genBlockedByReview`）完整。

### ⑥ Prompt 生成 — 🔴 最大问题区（Phase 4 缺失）

**关键发现：Phase 4（按官方 base-en/ref-en 组装最终 H3 Prompt）从未实现。**

1. `prompt_builder_v17.py` docstring 明确「英文转换留 Phase 4（依赖官方 base-en/ref-en）」——**Phase 4 不存在**。
2. **`references/base-en.txt` 和 `references/ref-en.txt` 全仓库不存在**（find 无结果）。h3-prompt-writing skill 的 SKILL.md 引用它们，但 skill 目录里只有 SKILL.md，没有 references/ 子目录。
3. 当前进 H3 的文本 = 前端 `promptSections.ts buildShotPromptText` 四区**原样拼接**（中文，不翻译不套模板），完全没有 `integrated_multimodal_description / overall_soundscape / non_diegetic_music` 三段结构。
4. **三层 Prompt 并存**：
   - 后端 `prompt_builder_v17`（五区中文草稿，AI Draft / 审核用）
   - 前端 `promptSections.ts`（四区拼接，**实际生成链路用**）
   - 前端 `promptBuilder.ts`（advanced 三段英文结构，**死代码**，无人 import）
   - 旧版 `AI_PROMPT_WRITER_INSTRUCTIONS.md`（指导外部 AI 写英文三段 H3，与新版五区中文草稿不互通）
5. 三个措辞源并存：v1.json camera.intents（已被状态机绕过，废旧镜像）、camera_template.py 15 条硬编码、lib/official_pe_templates.py（Bernini 扩写，勿动）。

结论：**用户中文意图 → H3 最终 Prompt 的"最后一公里"是断的**。目前直接用的是中文拼接文本 + 少量运镜措辞，没有真正执行 H3 Prompt Skill 的官方结构。

### ⑦ Camera 运镜（camera_template）— ✅ 基本正确，可增强

按镜头目的选择（establish/close/dialogue/motion/emotion/ending），跨镜状态机（方向延续/对话反打/避免重复推近），场景切换重置。符合用户"运镜不能每镜随机"的要求，但**镜头关系（transition_type）未记录**（见 §九）。

### ⑧ Continuity — 🟡 已有一半，缺结构化的"上一镜状态"

现有：
- 像素级：prev_tail → ref_image_0/ref_video_0（智能尾帧）——**已验收，勿动**。
- 文字级：executor_core state_holder + `format_state_prefix`（"接续上一镜的状态：..."前缀）——已验收。
- 运镜级：camera_template 跨镜状态机。

缺失（用户 §七 要求）：
- **Previous Shot State 结构化**（location/characters/position/appearance/action/props/lighting/time/camera/emotional）——目前只有 `state_holder["current"]` 一个字符串，由 Qwen 提取，无结构化字段。
- **"必须保持 vs 允许变化"分离**（用户 §十）——H3 里完全没有，导致 AI 可能为满足新 Prompt 重画整个场景。
- **镜头关系 transition_type**（continuation/reaction/cut/reverse/close_up/match_action...）——无记录。

### ⑨ Workflow — ✅ 正确，保持不动

Workflow Registry/Studio 已交付（V1.9），timeline_data → Director 节点链路稳定。

### ⑩ H3 生成 — ✅ 正确，保持不动

conditioning.py + core_sampling.py，H3_ALIGN=32 修复、任务路由、VRAM 互斥门、三级反馈全部已验收。

### ⑪ Generation — 🟡 基本可用，有风险

- Shot.generations[] + activeGenerationId + ⭐设为当前成片已实现（V1.11）。
- 最新生成**不会**自动成为当前成片（符合用户 §十五 要求）。
- ⚠️ 风险：
  1. **归档失败静默降级**：`createGeneration` 里 archiveGeneration 失败被 catch 吞掉，videoFile="" → 回退到 output 临时文件（`/view?type=output`），**ComfyUI 重启/清 output 后灰卡**。这是"灰色视频无法播放"的头号根因。
  2. **多镜合并成片不产生逐镜 versions**：scope_ids.length>1 时不调用 createGeneration，切镜后无版本可回看。
  3. **切镜不重置 finalVideo**：`watch([shot, scene])` 不清 wb.finalVideo，切到无视频的新镜头，大播放器仍播上一镜成片。
  4. **subfolder 传递链脆弱**：归档路由完全依赖前端从 executed 事件带回 subfolder，SaveVideo 节点的 subfolder 无后端校验。

### ⑫ Shot UI — 🟡 需要打磨

- 唯一大播放器 + 版本条缩略图已收敛（V1.11.1）。
- 三处信息重复：genStateLabel / cinemaGenLabel / detailGenLabel（版本条 + 影院 + Shot 卡头部近乎相同）。
- "已采纳"视觉：目前是绿虚线边框 + `🤖 ✓ 已采纳` 徽标 + 顶栏横幅 + 生成拦截，用户觉得权重过高。

---

## 三、时间输入 Bug（用户 §十一）专项排查

审计结论：**前端时长输入框（右栏「时长（秒）」）是 `@change` 非 `v-model`，`updateShotDuration → clampDur` 只保留 1 位小数，无任何写回链**——即当前代码不应存在"输入过程中数字自动变化"。

已排查到的真实差异源：
1. **17k+5 帧网格误差**：输入 3s@24fps=72 帧 → snap 到 73 帧 → 实际视频 3.04s。版本条 `genDurText` 读视频元数据真实时长，显示 3.0→3.1s 与设定 3s 有出入（**显示差异，非输入回写**）。
2. **导入 clamp 2-8 秒**：超范围静默截断。
3. **历史遗留**：#28 曾修过「r2v 素材组秒数输入框自动变化」——那个是素材组秒数框，与本问题可能同源或不同。

待用户复现确认：是"时长（秒）"主输入框、还是高级面板的某个秒数框。若主框确实变化，需现场抓包判断是 clampDur 还是别的组件 watch 回写。

---

## 四、明确：不要动（已验收，铁律）

| 模块 | 理由 |
|---|---|
| asset_registry.py（entity_key/alias/persisted binding） | Phase 2-2 验收 48/48，用户明确要求不动 |
| executor_core 续接/资产注入/状态跟踪/三级反馈 | 架构固底 + Golden Path 实测验收 |
| fl2v_timeline / plan.py 的 reinforce_* 硬锁前缀 | r2v 续接用户实测验收 |
| segment_continuity.py 常数与张量逻辑 | 调参防花屏，H3 链路 no-op 可忽略 |
| prompt_sanitize.py | P0-4 验收 |
| camera_template.py 状态机 | Phase 5 验收 |
| Workflow Registry/Studio、ETA、项目持久化 | V1.9/V1.10/V1.1.1 交付 |
| lib/official_pe_templates.py | Bernini verbatim |
| prompt_enhance_media.py | 用户真实验收替换任务 |

---

## 五、明确：应该修改 / 待实现（按优先级）

### P0-① 中文自然语言 → H3 Prompt 转换（最大问题）
**目标**：用户自然语言 → 剧本理解 → 导演结构 → H3 Prompt，不强制用户学 H3 语法。

**三层分离设计**（用户 §四）：
- **Story/Script Layer**：保存用户原文（source_text 已有，保持不动）。
- **Director Layer**（新增/加强）：谁/哪里/做什么/前后镜头关系/情绪/时间/空间/连续性/镜头目的/运镜/转场 → **结构化 DirectorIntent**。
- **H3 Prompt Layer**（新增，Phase 4 落地）：按 H3 Skill 官方结构组装最终 Prompt。

关键动作：
1. 实现 **Phase 4 最终 H3 Prompt 组装器**：
   - 补齐 `references/base-en.txt` / `references/ref-en.txt`（可从 skill 或官方文档重建）。
   - 新增 `director/h3_prompt_builder.py`：DirectorIntent（中文）→ 英文 H3 三段结构（integrated_multimodal_description / overall_soundscape / non_diegetic_music），带时间轴标注、参考图标签、保留对白原文。
   - 挂在 `prompt_builder_v17`（AI Draft 预览）与生成链路（executor 前）。
2. **Director Layer 结构化**：新增 `director/director_intent.py`（或扩展 production_plan），保存每镜的导演意图字段（vision/action/emotion/relation/continuity），Qwen 只负责"理解剧本 → 填导演意图"，不再直接写 H3 措辞。
3. **语义漂移防线**：Qwen 输出 → 导演意图 → H3 之间保持单向、可追溯；`source_text` 作为最终可回溯原文。

### P0-② H3 Prompt Skill 整理
1. **确认使用链路**：当前 H3 Skill 只存在于 Claude 侧（SKILL.md 引用缺失的 references 文件），**代码链路从未执行它**。真实链路是"中文拼接文本直进 H3"。
2. **可替换性**：skill 规则不应硬编码在 Vue/TS 里。建议把 H3 结构规范收进一个后端 Python 模块（如 `h3_prompt_builder.py` + `prompt_templates/h3-v2.json`），前端只透传，替换模板不动解析逻辑。
3. **清理死代码**：`promptBuilder.ts`（无人 import）、`prompt_enhance_runtime.maybe_enhance_segment_prompt`（无调用者）、v1.json camera.intents（被状态机绕过）。
4. **旧版指令对齐**：`AI_PROMPT_WRITER_INSTRUCTIONS.md` 与新链路结构对齐或废弃。

### P0-③ Shot-to-Shot 连贯性
1. **Previous Shot State 结构化**：在 executor_core state_holder 基础上，新增结构化字段（location/characters/position/appearance/action/props/lighting/time/emotional），由 Qwen 提取填入，而非单一字符串。
2. **镜头关系 transition_type**：在 Director Layer 记录（continuation/reaction/cut/reverse/close_up/wide_to_close/close_to_wide/match_action/match_direction），生成时注入下一镜 Prompt。
3. **"必须保持 vs 允许变化"分离**：H3 Prompt 中区分（必须保持：角色外观/服装/发型/地点/道具/时间/色调/空间关系；允许变化：镜头距离/机位/动作/表情/运镜/景别/构图）。
4. **运镜按目的选择已满足**（camera_template），保留。

### P1-④ Generation History
- （#453 已梳理，V1.11 已实现大部分）补：多镜合并成片落逐镜版本 / 归档失败重试 / 切镜重置 finalVideo。

### P1-⑤ 视频播放稳定性
- 归档失败静默降级 → 加"视频准备中"过渡态 + 后端执行结束主动归档兜底。
- subfolder 传递链加后端校验。

### P1-⑥ 新项目状态隔离
- #399 已修复任务残留；补：loadProject 复位 running/progress/lastPromptId（低危）。

### P1-⑦ 时间输入 Bug
- 待用户复现确认位置；若为主输入框则现场抓包。

### P2-⑧⑨ UI 打磨
- 已采纳降权（改小徽标，去掉大面积绿框/横幅拦截提示的视觉权重）。
- Generation UI 简化（砍掉三处重复信息）。

---

## 六、一句话结论

工具的"壳"（资产/工作流/ETA/版本管理）已经全部搭好且验收通过。**真正决定成片质量、且最值得投入的地方，是"用户中文意图 → 导演层 → H3 Prompt"这一层**：Phase 4 最终 H3 Prompt 组装器缺失（skill 引用的 base-en/ref-en 文件不存在）、当前直进 H3 的是中文拼接文本、没有执行 H3 Skill 官方结构、没有"必须保持 vs 允许变化"区分、没有结构化的镜头关系。这正是用户判断的"下一阶段最值得投入的地方"。