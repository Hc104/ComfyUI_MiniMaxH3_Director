# H3 Prompt Skill 整理审计（P0-② #451）

> 用途：确认「H3 Prompt Skill」在项目中的**实际使用链路**与**可替换性**，为后续
> （如需要）把 Skill 规范接入产品链路做前置盘点。**本审计不编写 Skill、不改代码**。
> 日期：2026-08-13。配套：[[H3_PIPELINE_AUDIT.md]]（P0-① 链路审计）。

---

## 一、结论速览

| 问题 | 结论 |
|---|---|
| 当前产品链路使用哪个 Skill？ | **不使用任何 Skill**。正式链路是后端代码内建的项目 Schema（`h3_prompt_builder.py`）+ 前端分区合并（`promptSections.ts`）。Skill 仅是 Claude 写 H3 提示词时的工作辅助，不进入用户生成链路。 |
| Skill 文件在哪？ | 插件只读缓存目录 `...\skills-plugin\<uuid>\skills\h3-prompt-writing\`，**只有 SKILL.md（35 行），无 references/ 子目录**。9 个 H3 相关 skill 目录全部只有 SKILL.md 单文件。 |
| SPA/Qwen 用哪个 Prompt 模板？ | **两套并存**：① 旧版 `prompt_builder_v17.py` + `director/prompt_templates/v1.json`（五区中文草稿，供人工审核）；② 新版 `h3_prompt_builder.py`（三段式 `minimax-h3-project-v1`，正式 H3 Prompt，**目前仅审计预览用**）。Qwen 只产出 DirectorIntent 结构化字段，不直接生成 Prompt 文本。 |
| 是否存在旧版/新版并存？ | **是**，但两套共享同一 `build_plan_intents`（DirectorIntent 状态机）来源，非重复实现。 |
| 是否引用缺失的 base-en.txt / ref-en.txt？ | **是**。`h3-prompt-writing/SKILL.md` 引用了 `references/base-en.txt` 与 `references/ref-en.txt`，但**两个文件都不存在**（skill 包与全仓库均无）。用户 2026-08-13 已拍板：官方仓库不存在 → 建立项目自己的 schema。 |
| 最终进入 H3 的 Prompt 经过几层转换？ | 见 §三。核心：**当前真正进 H3 的是前端五区中文合并文本，三段式 H3Prompt 尚未接入生成**。 |

---

## 二、Skill 现状（确认结果）

### 2.1 h3-prompt-writing（核心 skill）

位置：`skills-plugin/<uuid>/skills/h3-prompt-writing/SKILL.md`（35 行）。

- 定义了五种任务模式：T2VA / I2VA / FL2VA / L2VA / Ref2VA。
- **base 模式**：`integrated_multimodal_description` → `overall_soundscape` → `non_diegetic_music`（三段）。
- **Ref2VA 模式**：`subject_definitions` → `summary` → `retention_analysis` → `detailed_description` → `overall_soundscape` → `non_diegetic_music`（六段）。
- **缺失引用**：SKILL.md 明确要求「read `references/base-en.txt`」「read `references/ref-en.txt`」，但该 skill 目录下**没有 references/ 子目录**。`find` 全仓库 + skill 目录均无结果。

> ⚠️ 使用该 skill 写 H3 提示词时会遇到「引用的模板文件不存在」。此前 Claude 侧靠
> [[h3-prompt-writing-guide]]（9 个 skill 提炼的构造规范）兜底，不依赖缺失文件。

### 2.2 9 个 H3 相关 skill 的 references 引用情况

| Skill | 引用 references | 存在？ |
|---|---|---|
| `h3-prompt-writing` | `references/base-en.txt`、`references/ref-en.txt` | ❌ 缺失 |
| `co-op-game-intro-generator` | `references/h3-confirmation-image-template.md`、`references/h3-video-prompt-template.md` | ❌ 缺失 |
| `3d-animation-short-generator` | `references/shot-table-spec.md`、`storyboard-guidelines.md`、`model-selection.md`、`fallback-policy.md`、`qc-checklist.md` | ❌ 缺失 |
| `handdrawn-live-video-generator` | 无 | ✅ 自包含 |
| `brand-promo-video-generator` | 无 | ✅ 自包含 |
| `music-video-subtitle-generator` | 无 | ✅ 自包含 |
| `paper-collage-explainer-generator` | 无 | ✅ 自包含 |
| `papercraft-stop-motion-explainer` | 无 | ✅ 自包含 |
| `minimalist-product-ad-generator` | 无 | ✅ 自包含 |

> 3 个 skill 引用了共 9 个不存在的 references 文件；6 个 skill 自包含。

---

## 三、产品链路：最终进入 H3 的 Prompt 转换层数（确认结果）

```
L0 剧本 source_text（用户原文）
 └─ ① script_parser.py 纯规则拆 Scene/Shot
 └─ ② script_analyzer.py（Qwen3:14B 本地 LLM）→ ProductionPlan
 │     （场景/镜头/实体/对白/retained_facts/emotion/composition，provenance 字段级）
 └─ ③ entity_cleanse.py 实体清洗 + asset_matcher.py 资产绑定
 └─ ④ director_intent.build_plan_intents（纯规则运镜状态机 + P0-③ 跨镜连贯）
 │     → DirectorIntent（L2 导演意图，含 continuity.prev_shot_state / transition_type / must_keep）
 │
 ├─ A. 人工审核草稿（旧版 V1.7，仍在用）
 │     └─ ⑤ prompt_builder_v17.build_plan_drafts（+ prompt_templates/v1.json 模板注册表）
 │           → 五区中文草稿 visual/camera/style/sound/negative
 │           → Workbench AI Draft 审核 → 采纳 → 存 Shot.content.*
 │
 └─ B. 正式 H3 Prompt（P0-① #400 新建）
       └─ ⑥ h3_prompt_builder.build_plan_h3_prompts
             → 三段 minimax-h3-project-v1 + 时间轴 + [REF] 可读标签 + 结构化 references + provenance
             → POST /minimax/director/prompt/h3 → Shot.h3Prompt 快照（审计预览展示）
             → ⚠️ 尚未接入生成提交
```

### 3.1 真正进入 H3 生成的 prompt（现状）

- 前端 `directorCore.ts` 提交生成时：`prompt = buildShotPromptText(shot)`（`promptSections.ts`）。
- `buildShotPromptText` = `visual + cameraText + style + soundText` 按输入语言逗号拼接
  （含 CJK → 中文逗号「，」，否则「, 」；**不翻译、不套模板**）。
- 后端 `gen_timeline.py`：`seg_prompt = seg_data.get("prompt")` → `strip_media_tags` → 进 workflow。

> **结论**：当前直进 H3 的是「用户中文五区合并文本」。三段式 H3Prompt（B 链路）已建好、
> 三层可追溯已落地（AI 理解 → H3 Prompt → 来源标记），但**生成提交尚未切换到三段式**。

### 3.2 三段式 H3Prompt 为何未接入生成（推测）

- #400 的范围是「建立转换机制 + 三层可追溯 + 展示」，生成链路切换未在本轮范围。
- 若要把三段式接入生成，需把「采纳审核后的 Shot.content」→ h3_prompt_builder → 三段合并
  作为提交 prompt（或至少让采纳后的内容进入三段式再提交）。**这是后续可选工作，本审计不实施。**

---

## 四、可替换性（#451 核心交付）

1. **替换 Skill 极易**：产品链路不依赖任何 skill。只需新增/更新 skill（如 `minimax-h3-project-v1`），
   Claude 侧写 H3 提示词遵循新规范即可，对产品零影响。
2. **正式 H3 Prompt Schema 已项目化**：`h3_prompt_builder.py` 的 `SCHEMA_VERSION =
   "minimax-h3-project-v1"`，字段名/组装规则/时间轴/参考图标签为项目自己实现，可随项目版本化升级，
   不受外部 skill 变化影响。
3. **残留风险**：`h3-prompt-writing` skill 的 SKILL.md 引用缺失的 base-en/ref-en 文件。
   若未来有人严格按该 skill 执行会读不到模板。建议（**待用户拍板，不在此实施**）：
   - 用项目自己的 `minimax-h3-project-v1` schema 文档替换该 skill 的 references 引用；或
   - 更新 skill 指向项目内实际使用的三段结构说明。

---

## 五、验证记录

- `find skills-plugin/.../skills/h3-prompt-writing/` → 仅 `SKILL.md`。
- `find <repo> -name "base-en*" -o -name "ref-en*"` → 无结果。
- `grep` 确认 http_routes.py 同时 import `build_plan_drafts`（v17）与 `build_plan_h3_prompts`。
- `grep` 确认前端生成提交 prompt = `buildShotPromptText(shot)`（promptSections.ts）。
- `grep` 确认后端 gen_timeline 读 `seg_data.get("prompt")`，不消费三段字段。
- 三段字段仅出现在 http_routes（/prompt/h3）、WorkbenchView（展示）与 comfyApi 类型。
