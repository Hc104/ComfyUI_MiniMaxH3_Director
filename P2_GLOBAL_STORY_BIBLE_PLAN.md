# P2 · Global Story Bible 设计文档（跨章世界状态库）

- 日期：2026-08-15
- 状态：**用户已拍板四项决策**，待拍板整体方案后按 P2-P1..P2-P6 实施
- 前置：P1-B（#530-#534）已全部收官——单章导入链路「正文 → Beats → Timeline → Shots → Locations」已稳定

## 1. 背景与目标

P1-B 完成「一章小说 → Story Beats → Timeline → Shots → Locations」。但**每章独立理解，跨章不连续**：
- 第 5 章导入时，系统不知道沈青崖是谁、不知道剑匣是第 2 章埋的线索、不知道林雪上一章刚被劫走。
- Qwen 每次只见一章正文，跨章一致性靠运气：同一角色可能被识别成两个名字，第 5 章可能重新「介绍」主角，道具归属会漂移。

P2 加**项目级 Global Story Bible（跨章世界状态库）**：把已读章节沉淀出的「人物/地点/道具/已发生剧情/当前状态」保存下来，**每一章导入时带着已知世界去理解**，产出跨章一致的分镜。100 章漫剧化由此成立。

## 2. 用户拍板决策（2026-08-15）

| 维度 | 决策 |
|---|---|
| 存储 | **独立 `bible.json`**（项目级，后端权威，前端经路由读写；不污染 project.json 前端结构） |
| 确认策略 | **静态锁定 + 动态累积**：静态 facts（外观/性格/环境/归属）AI 只提候选，人工确认才写入、确认后 AI 不覆盖；动态 state（当前状态/道具位置/已发生剧情/最后出现）自动累积 |
| 上下文注入 | **全量注入**：已确认人物/地点/道具身份 + 各自当前状态 + 最近剧情进展摘要 |
| 范围 | **后端 + 前端闭环**：数据模型/更新器/上下文注入/路由 + 前端 Bible 面板 |

## 3. 铁律（全期不变）

1. **Stable Entity Key**：AI 只提候选，绝不覆盖已确认条目（与 asset_registry 的 accepted binding 同机制）。已确认 `CHAR-001` 之后持续复用，不每章新建 CHAR-019/037/081。
2. **不碰生成链路**：Bible 只在「导入理解」层工作。executor/segment_cache/导出/归档/播放/VAE（#485/#487）/显存安全门（/api/ps）/Asset Registry 资产绑定（#388/#402/#404）/Shot 级资产边界（#471）/DirectorIntent/h3_prompt_builder/P0-A 提交链路全部不动。
3. **向后兼容**：`bible=None`（老调用、无 project_id）时，导入行为与 P1-B 完全一致——零注入、零更新。
4. **四层不混**：Bible 是「世界知识层」，不改 Beat（剧情结构）/ Timeline（播放顺序）/ Shot（拍摄单位）/ Location（世界资产容器）的既有职责。
5. **显存安全门**：Bible 全链路纯规则零 GPU（实体提取/匹配/注入组装都是文本层）；Qwen 调用仍走既有 TextSession 显存安全门。

## 4. 数据模型（`director/bible.py`）

### 4.1 `BibleEntry`（一条世界状态记录）

```python
@dataclass
class BibleEntry:
    entity_id: str            # CHAR-001 / LOC-001 / PROP-001 / EVT-001（规则层分配，跨章稳定）
    entity_type: str          # character | location | prop | event
    name: str                 # 规范化名（沈青崖 / 山雨楼大堂 / 剑匣）
    aliases: List[str]        # 别名（青崖/沈公子），normalize_entity_name 归一
    status: str               # candidate(候选) | confirmed(已确认·锁定)
    source: str               # ai | rule | user（人工录入/编辑置 user）
    # —— 静态 facts（confirmed 后锁定，AI 永不覆盖）——
    attributes: Dict[str, str]  # character: {appearance, personality}
                                # location:  {environment, style, props}
                                # prop:      {owner, purpose, appearance}
    # AI 对已确认条目提出的新属性候选（不直接覆盖，面板逐条采纳/忽略）
    attribute_suggestions: List[Dict] = field(default_factory=list)
    # —— 动态 state（自动累积，供上下文注入）——
    current_status: str       # 当前状态（沈青崖：重伤未愈；剑匣：在林雪手中）
    last_seen: str            # 最后出现集（ep3）
    history: List[str]        # 已发生剧情（"ep2：沈青崖取得剑匣"），尾部追加
    first_seen: str           # 首见集（ep1）
    props_held: List[str]     # character 当前持有的道具 entity_id（动态）
    relationships: List[str]  # 人物关系候选（"沈青崖-林雪：故交"），面板可确认
    # —— 关联 ——
    asset_key: str            # 关联 asset_registry 的 entity_key（可空，P2 不做资产绑定）
```

### 4.2 `StoryBible`

```python
@dataclass
class StoryBible:
    bible_id: str             # 与 project_id 同值（项目级）
    project_id: str
    entries: List[BibleEntry] = field(default_factory=list)
    version: int              # 每次导入 +1（供前端判断「有新变更」）
    updated_at: str
```

序列化 `to_dict/from_dict` 防御式（沿用 production_plan 的 `_as_int_defensive` 风格，老数据缺字段给默认值）。

### 4.3 状态机（Stable Entity Key 应用）

- 实体**首次**出现 → 新建 `candidate` 条目（source=ai/rule）。
- 已确认条目**再次**出现（normalize 命中 name/aliases）→ 复用 `entity_id`，**只更新动态 state**（last_seen/history/current_status/props_held），静态 attributes 不动。
- AI 对已确认条目提出**新属性** → 进 `attribute_suggestions`（不改 attributes），面板可「采纳/忽略」。
- 人工确认 candidate → `status=confirmed`，attributes 锁定。
- 新别名命中已确认条目 → 归并进 aliases 的候选（面板展示合并建议，用户确认）。

## 5. 持久化（`director/bible_store.py`）

- 路径：`projects/<project_id>/bible.json`（与 project.json 同目录，后端权威；同 asset_registry.json 的「文件即状态」思路，但属剧情层，独立文件独立演化）。
- 接口：`load_bible(project_id) -> StoryBible | None`、`save_bible(bible) -> bool`、`ensure_bible(project_id) -> StoryBible`（不存在建空 Bible 并落盘）。
- 写入原子性：临时文件 + `os.replace`（同 save_project 风格，防中断半写）。

## 6. 更新器（`director/bible_updater.py`，纯规则零 LLM 零 GPU）

输入：本集 `beats`（含 text_segments/characters/props/location/time/emotion）+ 现有 `StoryBible` + 本集编号。

- **提取**：复用 script_parser 的 `_collect_candidate_names` / `_extract_characters` / `_extract_props`；地点取自 beats 的 location / scene 标题。
- **匹配**：`normalize_entity_name` 归一 → 已确认 name/aliases 精确命中优先 → 子串包含次之 → 未命中新建 candidate。
- **动态累积**：命中条目的 last_seen=本集、history 追加一行、current_status 取 beats 里本集最新情绪/状态描述（关键词启发式）、props_held 按出现更新。
- **输出**：`(updated_bible, updates)`，updates 形如 `[{kind: "new_candidate"|"reused"|"status_change"|"alias_merge", entity_id, name, message}]`，供前端展示「本次导入对 Bible 的变更」。

## 7. 上下文注入（`story_analyzer.py` 扩展）

- `_BIBLE_CONTEXT_TEMPLATE`：在 `_STORY_ANALYZE_TEMPLATE` 的正文前插入一块全量上下文：

```
【跨章知识（Global Story Bible）】
人物：
- 沈青崖 (CHAR-001) ✓ 白衣剑客，性格冷峻 | 当前：重伤未愈 | 最近：ep2 取得剑匣
- 林雪 (CHAR-002) ◌ 持伞女子 | 当前：被劫 | 最近：ep4 被黑衣人带走
地点：
- 山雨楼大堂 (LOC-001) ✓ 雨夜烛火，江湖客栈 | 最近：ep3
道具：
- 剑匣 (PROP-001) ✓ 沈青崖所得 | 当前：在林雪手中 | 最近：ep4
已发生剧情：
- ep2 沈青崖取得剑匣；ep4 林雪被劫走

【规则】以上为已确认/已沉淀的世界状态。分析本章时保持身份一致：
- 命中上述人物/地点/道具的引用，用其稳定名，不另造新名。
- 不重新介绍已确认身份；只在剧情有发展时更新状态。
```

- `extract_beats(text, backend, *, title="", bible=None)`：bible 非空 → 拼装上下文块；bible=None 时模板与 P1-B 逐字一致（向后兼容）。
- 动态 state 全量注入，history 尾部 cap（如最近 20 条）防超 token。

## 8. 路由扩展（`http_routes.py`）

- **`POST /story/import` 扩展**：body 新增 `project_id` + `episode_number`（均可选）。
  - 有 `project_id` → `ensure_bible` → 注入全量上下文 → 导入 → 更新 Bible → response 追加 `bible`（更新后）+ `bible_updates`（变更列表）。
  - 无 `project_id` → 行为与 P1-B 完全一致（铁律 3）。
- **新路由**：
  - `GET /minimax/director/bible?project_id=X` → `{bible}`（前端面板加载）。
  - `POST /minimax/director/bible/confirm` → `{project_id, entity_id, action, payload}`，action ∈ `confirm`（确认候选）/`accept_attribute`（采纳属性建议）/`merge_alias`（合并别名）/`edit_attributes`（人工改静态属性，source=user）。写回 bible.json。
  - `POST /minimax/director/bible/entry` → 人工新建条目（source=user）。
- ⛔ **http_routes 改动需重启 Comfy Desktop**。

## 9. 多集管理

- project.json 的 `episodes[]` 已有逐集结构（每集一个 ProductionPlan：scenes/beats/timeline）；**Bible 项目级共享**，跨集引用。
- 导入时指定目标 `episode_number`（1-99）：前端若该集不存在则先建集再导入。
- Bible 上下文注入始终使用**全部已确认内容**（跨章，不只上一集）。

## 10. 前端 Bible 面板（SPA）

- 入口：左侧栏「📍 地点库」下方新增「📖 Bible」面板（或同区 tab）。
- 内容：人物 / 地点 / 道具 / 事件 四组分类 tab；每条目卡片显示：名称 + 确认徽标（✓ 已确认 / ◌ 候选）+ 别名 + 静态属性（折叠）+ 当前状态 + 最后出现 + 历史（折叠）。
- 操作：✓ 确认候选、✓ 采纳属性建议、编辑静态属性（本地编辑→保存走 `bible/confirm` 路由）。
- 导入弹窗「📖 小说章节」集成：导入前显示「本次将注入的跨章上下文」预览；导入后显示 `bible_updates` 变更列表（新增候选 / 复用 / 状态变化）。
- 纯前端刷新即生效；新增路由需重启 Comfy Desktop 后可用。

## 11. 数据流（第 N 章）

```
粘贴第 N 章正文
  → 前端带 {project_id, episode_number} 调 POST /story/import
  → 后端 load Bible（项目级）
  → 注入全量上下文（已确认身份 + 当前状态 + 最近剧情）
  → Qwen 整章理解（带跨章知识，身份不漂移）
  → 规则拆 Shot（复用 P1-B build_plan_from_beats，零改动）
  → 更新 Bible（新增候选 + 动态状态累积）→ version+1
  → 返回 {plan, rule_only, warnings, bible, bible_updates}
  → 前端审核预览（复用）+ Bible 面板展示候选待确认
```

## 12. 实施拆分（任务 #538-#543）

- **P2-P1（#538）**：`director/bible.py` 数据模型（BibleEntry/StoryBible/序列化）+ `director/bible_store.py`（load/save/ensure）+ 单测。
- **P2-P2（#539）**：`director/bible_updater.py` 提取/匹配/动态累积 + 单测。
- **P2-P3（#540）**：上下文注入（`_BIBLE_CONTEXT_TEMPLATE` + `extract_beats` 扩展）+ 单测。
- **P2-P4（#541）**：路由扩展（/story/import bible 接线 + GET bible + confirm + entry）+ http_routes 测试。⛔ 需重启 Comfy Desktop。
- **P2-P5（#542）**：前端 Bible 面板 + 导入集成（上下文预览 / Bible 变更列表）+ 前端测试。
- **P2-P6（#543）**：收尾（回归/build/双目录 md5/CHANGELOG/memory）。
- 顺序严格 P2-P1 → P2-P6；每阶段验收后再推进。

## 13. 明确不碰（已验收层，铁律）

Asset Registry / Stable Entity Key / Alias 归一（#388/#402/#404）、Shot 级资产边界（#471）、DirectorIntent 三层可追溯（#400）、h3_prompt_builder 三段式 + P0-A 提交链路（#461/#477-480）、executor/segment_cache/导出/归档/播放、显存安全门（/api/ps）、VAE 非动态化（#485/#487）、P1-A/P1-B 已验收的 UI 与 story 管线。

## 14. 验收要点

1. 无 project_id 导入 → 行为与 P1-B 逐字一致（回归锁死）。
2. 有 Bible 时，同一角色第 1 章确认后，第 3 章再出现不再新建 entity_id（Stable Entity Key）。
3. AI 属性候选不覆盖已确认 attributes；面板采纳后才更新。
4. 动态 state（history/current_status/last_seen）随导入自动累积。
5. 全量注入后 Qwen 对已确认角色不重新介绍（人工抽检第 N 章 beats）。
