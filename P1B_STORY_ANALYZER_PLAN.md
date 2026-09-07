# P1-B 设计方案（用户拍板版）：自然语言小说导入（story_analyzer.py）

- 设计日期：2026-08-14
- 状态：**用户已拍板（2026-08-14）→ 待动工**
- 上游：P1 五项硬决策 + 《小说导演层架构审计.md》（#509-#512） + P0 Story Timeline（#513-#521） + P1-A Timeline 主视图（#521-#526 已验收）
- 目标：用户把小说**一章自然语言正文**直接扔进来 → 系统自动 **理解剧情 → 拆 Story Beat（含 dramatic_function）→ 生成导演调度层 Timeline → 按剧情功能生成可拍摄 Shot 序列 → Locations 资产容器**，复用已验收的 entity_cleanse / asset_matcher / director_intent / camera_template / h3_prompt_builder，产出落 `ProductionPlan`（含 beats + timeline），走现有导入审核 → 应用 → 工作台链路。

---

## 0. 用户拍板纪要（2026-08-14，逐字要点）

1. **Beat 上限**：不采用固定 ≤12，也不采用简单「每 300 字一个」。采用 **「Qwen 剧情节拍识别 + 规则数量约束 + 超限合并」** 的动态策略，默认建议 **8–16 个**，根据章节长度与剧情复杂度动态调整，**不得截断剧情**。
2. **Qwen 失败**：允许**纯规则降级，不阻塞导入**，但必须**明确 warnings**：告知用户当前结果未经过 AI 剧情理解、需人工检查。
3. **工作台 Beat 展示**：**本轮不加**工作台 Story Beats 面板；Beat 只在小说导入审核阶段展示；工作台继续以 **Timeline + Location** 为核心。
4. **一章一集**：确认「一章 → 一个 Episode」；100 章多集管理 + 跨章 Global Story Bible 放 **P2**。
5. **Beat 增加 `dramatic_function`（剧情功能）字段**：`introduce_character / dialogue / plant_clue / introduce_threat / confrontation / action / reveal / emotional / transition` 等，作为后续 Shot 导演调度和运镜决策的输入。
6. **Shot 不能按动作句一对一拆分**：要结合 **Beat 剧情功能 + 动作单元 + 对白 + 导演规则** 生成真正可拍摄的镜头序列（不是「沈青崖走进来→镜头1、柳如烟抬头→镜头2」的流水账）。
7. **核心原则（四层不混）**：**Location 是世界资产容器、Timeline 是播放顺序、Beat 是剧情结构、Shot 是实际拍摄单位**。

---

## 1. 目标与范围

### 1.1 要做的
1. **输入自由**：整章小说正文，零标记（不要「第X场/镜头一」）。
2. 自动产出 **Chapter → Beats → Timeline → Shots → Locations**（P1 拍板产出形状）。
3. **Scene = Location 容器**（P1 决策②）：`location_name` = 地点名，`scene_id` 即兼容 `locationId`，**不决定播放顺序**。
4. **Timeline = 播放顺序唯一权威源**（P1 决策⑤）：Shot 级 `"scene_id:shot_id"` 列表，承载跨地点交叉剪辑，是导演调度层。
5. **不覆盖已确认 Location**（P1 决策④）：AI 只提候选，`asset_registry` 已确认的 Stable Entity Key 优先，绝不覆盖。
6. **Beat 是剧情结构层**：每章产出一组 Story Beat（含 `dramatic_function`），只描述「这段剧情为什么存在、讲了什么」，不决定播放顺序、不决定镜头边界。

### 1.2 明确不做（P1-B 边界）
- ❌ 多章管理 / 章间连续性（→ **P2 Global Story Bible**）。
- ❌ 一次处理 100 章（→ P2；P1-B 一次一章，一章 = 一个 Episode）。
- ❌ 工作台重设计（P1 决策①最小侵入：P1-A 已交付 Timeline 主视图 + 地点库，本轮不重做）。
- ❌ 自动参考图 / 视频 / 剪辑 / 配乐（V17_PLAN §0.1 既有边界）。
- ❌ 工作台新增 Story Beats 面板（拍板③）。

---

## 2. 核心数据流（拍板后版本）

```
小说章节正文（自然语言，零标记）
  → POST /minimax/director/story/import   {story_text, title, source_file, analyze}
  → story_analyzer.analyze_story(text, backend)
        P1-B-1  Qwen 整章理解（单 session，显存安全门内）
                llm.analyze_json(STORY_ANALYZE_TEMPLATE)
                → {beats: [{title, summary, dramatic_function,
                            location, time, weather,
                            text_segments: [原文片段…], characters, props,
                            dialogue: […], emotion, transition_reason}]}
        P1-B-2  Beat → Timeline（纯规则，可复现）
                动态 Beat 数量约束（8–16，超限合并，不截断）
                Timeline = Beat 顺序权威；transition_reason 仅存元数据供审核
        P1-B-3  Beat → Shot（纯规则，导演调度）
                dramatic_function 命中 Shot Blueprint 模板
                + 动作单元 + 对白 → 镜头序列（景别/镜头类型/运镜方向）
                每 Shot 独立 Location（跨地点交叉剪辑的落点）
                原文逐字保留进 Shot.source_text
        P1-B-4  Shot → DirectorIntent（复用已验收：USER→RULE→AI）
        P1-B-5  DirectorIntent → H3（复用已验收：h3_prompt_builder → 三段式 H3）
  → {plan: ProductionPlan(含 beats + timeline), rule_only, warnings}
  → 前端导入弹窗「📖 小说章节」页签审核预览：
      章节 → Beats 分组卡片（🎬 title / summary / 🎭 dramatic_function
             / 📍 location / 🕐 time / 🌦 weather / 镜头数 / transition_reason 折叠）
            + 📍 地点列表（source=ai 待确认标记） + 🎞 播放顺序摘要 + ⚠ 警告
  → 应用为 SPA 项目（productionPlanToProject 复用；project.plan 已存原始 plan 含 beats）
  → 工作台（P1-A 已交付：📍 地点库 + 🎞 Timeline 主视图）
```

### 2.1 复用零改动部分
- `entity_cleanse.py`（实体清洗/类型纠正/置信度门控/边界检查）——地点词提取、类型纠正同链路。
- `asset_matcher.py` + `asset_registry.py`（资产匹配 + 持久化绑定）。
- `director_intent.py`（P1-B-4）、`camera_template.py` / `h3_prompt_builder.py`（P1-B-5）——按 shot 独立寻址，不依赖场景树，**架构不动**。
- `production_plan.py` 的 `timeline` / `resolve_timeline_order` / `shot_lookup` / `default_timeline`。
- 前端 `productionPlanToProject.ts` 的 `buildEpisodeTimeline` / `sceneFromPlan`（scene_id=locationId 兼容，零改动）。

---

## 3. Schema 扩展（production_plan.py）

### 3.1 新增 `StoryBeat`（含 dramatic_function）
```python
@dataclass
class StoryBeat:
    beat_id: str = ""               # beat_01（规则层分配）
    title: str = ""                 # 节拍标题（沈青崖进入山雨楼）
    summary: str = ""               # 剧情摘要（Qwen）
    dramatic_function: str = ""     # 剧情功能：introduce_character / dialogue /
                                    #   plant_clue / introduce_threat / confrontation /
                                    #   action / reveal / emotional / transition …
    scene_id: str = ""              # 主发生 Location（scene_id 即 locationId 兼容）
    time: str = ""                  # 时间（黄昏）
    weather: str = ""               # 天气（雨）
    text_segments: List[str] = field(default_factory=list)  # 🟢 本 Beat 覆盖的小说原文片段（逐字，覆盖校验的源）
    order: int = 0                  # 剧情顺序（规则）
    entries: List[str] = field(default_factory=list)  # ["scene_id:shot_id", ...] 本 Beat 镜头
    source: str = "ai"              # ai | rule（Qwen 失败降级为 rule）
    transition_reason: str = ""     # AI 候选：为什么切到这里（仅审核参考，不决定播放顺序）
```

### 3.2 `ProductionPlan` 加字段
```python
beats: List[StoryBeat] = field(default_factory=list)
```
- `to_dict` / `from_dict` 支持（缺省空列表 → 老数据严格向后兼容）。
- `validate()` **不参与结构校验**（beats 是元数据层）；可加 warning（如「Beat 数超过上限已合并」）。
- 不动 `scenes` / `timeline` / `validation` 现有语义。

### 3.3 dramatic_function 取值集合
```python
DRAMATIC_FUNCTIONS = {
    "introduce_character",  # 建立镜头：引入人物/地点
    "dialogue",             # 对切/双人：对话
    "plant_clue",           # 插入特写：埋线索（剑匣）
    "introduce_threat",     # 威胁建立：反派/危险出现
    "confrontation",        # 正反打/推进：对峙冲突
    "action",               # 动态运镜：动作
    "reveal",               # 先隐藏后揭示：反转/揭秘
    "emotional",            # 特写/慢推：情绪
    "transition",           # 建立镜头：过渡转场
}
```
校验在 story_analyzer 层（未知值 → 规则映射为 `transition` + warning），Schema 层保持 str 宽松。

### 3.4 与现有模型的映射关系
| 小说概念 | 落点 | 说明 |
|---|---|---|
| Location（地点） | `Scene`（scene_id = locationId 兼容） | **每个 Shot 有独立 Location**（shot 文本中提取地点词，fallback 继承 Beat 主地点） |
| Story Beat | `ProductionPlan.beats` | 剧情结构层；`dramatic_function` 是导演调度输入 |
| 播放顺序 | `ProductionPlan.timeline` | `"scene_id:shot_id"` 列表，Beat 顺序权威，Shot 级调度 |
| Shot | `Scene.shots` | 由「剧情功能 + 动作单元 + 对白 + 导演规则」生成，非动作句一对一 |
| 原文 | `Beat.text_segments`（逐字，**原文属于 Beat**）；`Shot.source_text` = 对 Beat 原文的导演切分引用 | §6.5 归属规则：Shot 拼接（保序去重）完整覆盖 Beat.text_segments → 完整覆盖小说正文 |

---

## 4. LLM / 规则分工（P0 铁律）

| 步骤 | 责任方 | 硬约束 |
|---|---|---|
| ① 剧情理解 | **Qwen** | 强 JSON；`text_segments` 必须逐字指回原文；只读不创造 |
| ② Beat 拆分 | **Qwen 提议 + 规则校验** | **动态数量约束 8–16**（按章节长度/复杂度），超限**合并相邻 Beat（不截断剧情）**；`text_segments` 拼接必须覆盖全部原文（缺口 → 规则补齐 + warning） |
| ③ Location 识别 | **Qwen 提议 + 规则确认** | entity_cleanse 类型纠正；**asset_registry 已确认 Stable Entity Key 优先，绝不覆盖已确认 Location**；新地点 source=ai 待人工确认 |
| ④ Timeline 调度 | **规则（Beat 顺序）** | **Qwen 不决定播放顺序**；transition_reason 仅存元数据；人工在 Timeline UI 拖拽（P1-A 已交付） |
| ⑤ 拆 Shot | **规则（剧情功能 Blueprint + 动作单元 + 对白）** | **Qwen 不决定镜头边界/镜头数量**；dramatic_function 命中镜头模板，模板+动作单元+对白共同决定镜头序列 |
| 实体 / 资产 | **复用现有** | entity_cleanse → asset_matcher → asset_registry |

**为什么 Timeline 由规则定**：P0 铁律「不要让 Qwen 决定一切」；Beat 顺序即叙事顺序，规则可完全复现、可回归测试；Qwen 的 transition_reason 作为导演理解补充（审核参考），调度权威永远是 规则 + 人工。

**为什么 Shot 由规则拆（拍板⑥）**：镜头是实际拍摄单位，镜头边界与数量是导演调度层职责。Qwen 只提供剧情理解（beat 的 dramatic_function / 动作 / 对白），**镜头模板由规则给出**，避免「一个动作句 = 一个镜头」的流水账式分镜。

---

## 5. Beat 数量策略（拍板①）

> **target 语义（用户 2026-08-14 锁死实现细节 1）**：`clamp(round(len/700), 8, 16)` 是
> **推荐目标区间，不是硬性必须达到的数量**。剧情结构优先：一章只有 2 个真实戏剧转折，
> 就产出 2 个 Beat，**绝不为了凑 8 个硬拆**。8–16 只约束「超限合并」的上界，
> 不约束「下限必须凑满」。合并策略与「不截断剧情」保留。

```
# 目标 Beat 数量（推荐目标区间，剧情结构优先，非硬性）：按章节长度动态（默认 8–16）
target = clamp(round(len(text) / 700), 8, 16)
# 真实节拍数 < 8 → 保持真实节拍数（不凑数、不硬拆）
# 1000 字→8；5000 字→8；7000 字→10；11000 字→16

# 超限合并策略（不截断剧情）：
#   1) Qwen 提议的 beats 超出 target → 优先合并「过渡性/低信息」Beat
#      （dramatic_function=transition 或 summary 很短 或 与相邻 Beat 地点/人物相同）
#   2) 合并 = 相邻两 Beat 的 text_segments 拼接 + title/summary 取主、dramatic_function 取
#      「更具体」的一个（confrontation > dialogue > action > plant_clue > transition …）
#   3) 绝不丢弃任何 text_segment；合并后若仍超 target → 继续合并最短的相邻 Beat
#   4) 合并过程记入 plan.validation.warnings（如「Beat 05/06 合并：连续对话过碎」）
```

**正确性底线**：无论合并还是 Qwen 缺段，最终 `text_segments` 拼接必须覆盖原文（去空白包含检查）；缺口由规则补齐到最近的 Beat + warning。

---

## 6. Beat → Shot 导演规则（拍板⑥ 核心，P1-B-3）

### 6.1 镜头模板库（Shot Blueprint）
每种 dramatic_function 对应一组规则化镜头模板（景别 / 镜头类型 / 运镜方向 / 作用）。这是「导演怎么拍」的骨架，Qwen 不参与。

| dramatic_function | 镜头模板序列（规则） | 作用 |
|---|---|---|
| `introduce_character` | establishing(远景·慢推) → wide(中景·跟移·人物走来) → medium(近景·跟拍·动作进入) → pov(主观·环视) | 建立人物/空间 |
| `dialogue` | two_shot(双人·中景·固定) → closeup(近景·正打) → closeup(近景·反打) [+insert(道具/反应·特写·可选)] | 对切/双人 |
| `plant_clue` | establishing(中景·固定) → insert(特写·慢推·线索) | 埋线索 |
| `introduce_threat` | wide(远景·暗处) → tracking(跟移·逼近) → pov(目标视角·聚焦) | 威胁建立 |
| `confrontation` | wide(全景·推进) → medium(双人·正反打) → closeup(特写·剑柄/眼神) → reaction(反应·拉远) | 对峙冲突 |
| `action` | wide(全景·甩镜) → medium(跟拍·动态) → fast_zoom(特写·推进) | 动作 |
| `reveal` | medium(中景·先遮挡) → push(特写·揭示) → reaction(反应·特写) | 反转/揭秘 |
| `emotional` | closeup(特写·慢推) → extreme_closeup(大特写·静) | 情绪 |
| `transition` | establishing(远景·拉远/环视) | 转场过渡 |

> **Blueprint 语义（用户 2026-08-14 锁死实现细节 2）**：上表是**候选镜头骨架 / 最小导演
> 模板**，不是每个 Beat 无条件整套生成。若每个 dialogue Beat 无条件 3 镜，一章 12 Beat
> 会膨胀到几十上百镜。实际启用哪些模板由 Beat 内容特征决定（§6.2）。

### 6.2 镜头序列合成（规则，可复现；Blueprint 是候选骨架，按特征启用）

**启用判定（每 Beat 独立）**——模板不是无条件全量生成，先算 Beat 内容特征：
```
features = {
  "len": len(beat_text),                       # Beat 内容长度
  "characters": count(beat.characters),         # 人物数量
  "has_dialogue": bool(beat.dialogue),          # 是否存在对白
  "has_action": bool(beat.action_units),        # 是否存在动作
  "has_prop_clue": bool(beat.props),            # 是否存在道具/线索
  "location_change": beat.locations_changed,    # 是否发生地点变化
  "emotion_shift": beat.emotion_shift,          # 是否有明显情绪转折
}
# 规则映射特征 → 实际启用模板子集（示例）：
#   短 Beat（len < 60）      → 只启用前 1-2 个模板
#   无对白                  → 跳过 two_shot 的 closeup 正反打，改 action/reaction 模板
#   has_prop_clue           → 追加 insert 特写
#   location_change         → 追加 establishing（新地点建立）
#   emotion_shift           → 追加 closeup 慢推
```
```
for beat in beats:
    blueprint = SHOT_BLUEPRINTS[beat.dramatic_function]   # 候选骨架（§6.1）
    specs = _select_template_subsets(blueprint, features) # 规则：按特征启用子集
    units = split_action_units(beat_text)                  # 复用 script_parser 动作单元规则
    dialogues = beat.dialogue
    for i, spec in enumerate(specs):
        shot_text = pick_text(units, spec.role, dialogues, i)  # 从动作单元/对白取本镜内容
        shot = Shot(source_text=shot_text, …)                 # 见 §6.5 归属规则
        beat.entries.append(f"{shot.scene_id}:{shot.shot_id}")
```
结果：**不是一句话一个镜头，也不是每个 Beat 套固定模板**——由剧情功能打底、内容特征调形。

### 6.3 Shot 独立 Location（拍板「每个 Beat 内允许：大堂 → 后院 → 大堂」的落点）
```
def resolve_shot_location(shot_text, beat):
    # 1) shot_text 中首个 Location 实体（entity_cleanse 类型纠正后，asset_registry 已确认优先）
    # 2) 若 shot_text 无地点词 → 继承上一 shot 的 location
    # 3) 首镜无地点词 → 继承 beat.scene_id（主地点）
    # 4) 规范化为 scene_id（同名合并；新地点 source=ai 待人工确认；绝不覆盖已确认 Location）
```
这样 `Beat 01（进入客栈）` 自然拆出「山雨楼外 establishing → 山雨楼大堂 wide/pov」多个 Scene，Timeline 即跨地点交叉剪辑。

### 6.4 为什么这样不是流水账
- **数量**：由 dramatic_function 模板决定（dialogue 至少 2-3 镜含正反打，不是一句一个镜头）。
- **类型**：模板指定景别/镜头类型（pov / two_shot / insert / tracking / fast_zoom），天然产生镜头语言。
- **内容**：每镜从动作单元 + 对白中取对应片段（不是逐句截断）。
- 人工可在 Timeline UI / Workbench 继续拖拽、增删镜（P1-A / 既有 Workbench 能力）。

### 6.5 Shot.source_text 归属规则（用户 2026-08-14 锁死实现细节 3）

**原文属于 Beat，Shot 是对 Beat 原文的导演切分引用**：
- `Beat.text_segments` 逐字保存小说原文（Qwen 输出 / 规则补齐，覆盖校验的源）。
- Shot 只**引用** Beat 原文中对应的片段（source_text = 该镜所覆盖的原文切片），
  不得把整个 Beat 原文复制进每个 Shot。
- **覆盖链（第一批测试就测死，test_story_analyzer_shots.py）**：
  ```
  所有 Shot.source_text 的拼接（保序、去重后）
        ↓ 完整覆盖（无遗漏、无重复）
  Beat.text_segments 的拼接
        ↓ 完整覆盖
  小说正文
  ```
- 切镜不产生「原文重复计算」（同一句不能出现在两个 Shot）也不产生「原文丢失」
  （每句至少落在一个 Shot）。

---

## 7. 规则映射算法（story_analyzer.py 核心）

```
def analyze_story(text: str, backend: TextBackend | None = None,
                  *, max_tokens=4096, temperature=0.0) -> tuple[ProductionPlan, bool, list[str]]:
    beats_meta = []          # Qwen 提议的 beats（含 dramatic_function）
    rule_only = False
    warnings = []
    if backend is not None and text_backend_active():
        with backend.session() as llm:
            data = llm.analyze_json(STORY_ANALYZE_TEMPLATE.format(story_text=_bounded(text)))
            beats_meta = _validate_beats_json(data)     # 强 JSON 校验；失败 → [] 走退化
    if not beats_meta:
        rule_only = True
        warnings.append("AI 剧情理解不可用/失败，结果由规则生成，未经 AI 分析，需人工检查。")
        beats_meta = _rule_fallback_beats(text)         # 按段落拆 + 规则提地点
    plan = build_plan_from_beats(text, beats_meta, warnings)   # 纯规则映射（§5/§6）
    plan.validate()
    return plan, rule_only, warnings


def build_plan_from_beats(text, beats_meta, warnings) -> ProductionPlan:
    # §5  Beat 数量动态约束：target=clamp(round(len/700), 8, 16)；超限合并（不截断）；合并记 warning
    # §6  Beat → Shot：dramatic_function 命中 Blueprint + 动作单元 + 对白 → 镜头序列
    #     Shot 独立 Location（§6.3，asset_registry 已确认优先，绝不覆盖）
    # 地点规范化：同名合并；首次出现建 scene_XX（location_name 归一后名）
    # timeline = 逐 Beat 逐 shot 的 "scene_id:shot_id"（Beat 顺序权威）
    # 覆盖校验：text_segments 拼接 vs 原文（去空白）包含检查；缺口规则补齐 + warning
    # plan.beats 记录；project = ProjectInfo(title=title, source_file=source_file)
    # plan.timeline = timeline（非空即显式，前端 buildEpisodeTimeline 直接消费）
```

**退化路径（拍板②）**：Qwen 全挂 / analyze=False → 整章按段拆 Beat（source=rule，dramatic_function 由段落内容启发式推断）+ 规则提地点 → §6 拆 Shot。产出结构完整可审核的 ProductionPlan（含 beats + timeline），**warnings 明示「未经过 AI 剧情理解，需人工检查」**，不阻塞导入。

---

## 8. 路由与前端

### 8.1 后端新路由
`POST /minimax/director/story/import`
- body：`{story_text, title, source_file, analyze}`（analyze 缺省 True）
- 流程：`parse_story → analyze_story（Qwen，失败降级）`，复用 `asyncio.to_thread`
- 返回：`{plan, rule_only, warnings}`（与 `/script/import` 同构，前端可复用审核 UI 骨架）
- ⛔ 新路由需重启 Comfy Desktop（既有铁律）

### 8.2 前端（拍板③）
1. **导入弹窗「📖 小说章节」页签**（与「📜 剧本」并存）：
   - 粘贴小说正文（.md/.txt 文件也可）
   - 审核预览：章节标题 + **Beats 分组卡片**（🎬 title / summary / 🎭 dramatic_function chip / 📍 location / 🕐 time / 🌦 weather / 镜头数 / transition_reason 折叠）/ **📍 地点列表**（含 source=ai 待确认标记）/ **🎞 播放顺序摘要**（逐 Beat 顺序）/ ⚠ 警告
   - 「应用为项目」→ saveProject + loadProject（id=`story-<ts>`）
2. **productionPlanToProject**：Scene 映射零改动（scene_id=locationId 兼容）；beats 天然随 `project.plan` 持久化，无需新前端字段。
3. **工作台 Beats 展示**：**本轮不加**（拍板③）。Beats 元数据存 `project.plan`，P2 再做章间/章内 Beats 浏览 UI。工作台导航由 P1-A 已交付的 📍 地点库 + 🎞 Timeline 主视图承载。

---

## 9. 实施拆分（拍板后，按 P1-B-1 … P1-B-5）

| 子阶段 | 内容 | 改动文件 | 验证 |
|---|---|---|---|
| **P1-B-1 小说章节理解** | `StoryBeat`（含 `dramatic_function`）+ `ProductionPlan.beats` + 序列化；`DRAMATIC_FUNCTIONS` 常量；`story_analyzer.py` 的 Qwen 整章理解（beats JSON 含 dramatic_function） | `production_plan.py` + `story_analyzer.py`(骨架) + `test_plan_beats.py` | 单测：to_dict/from_dict 往返 + 老数据兼容 + dramatic_function 校验 |
| **P1-B-2 Beat → Timeline** | §5 动态数量约束（8–16）+ 超限合并不截断；Timeline 生成（Beat 顺序权威） | `story_analyzer.py` + `test_story_analyzer_timeline.py` | 单测：数量约束 / 合并策略 / 覆盖校验 / 退化路径 |
| **P1-B-3 Beat → Shot** | §6 Shot Blueprint（dramatic_function 模板表）+ 动作单元 + 对白 → 镜头序列；Shot 独立 Location（§6.3） | `story_analyzer.py` + `test_story_analyzer_shots.py` | 单测：各 dramatic_function 模板 / 跨地点 / 不流水账（非逐句切镜）/ 地点合并与已确认优先 |
| **P1-B-4 Shot → DirectorIntent** | **复用已验收** `director_intent.build_plan_intents`（USER→RULE→AI）+ `camera_template` 运镜；**不新架构**，只接线 + 回归 | 无新文件（接线验证） | 回归：build_plan_intents 对 story 产出的 plan 全通过 |
| **P1-B-5 路由 + 前端 + 收尾** | `POST /story/import` + `test_story_import.py`；前端导入弹窗「📖 小说章节」页签（Beat + dramatic_function 审核预览）；全量回归 / build / 双目录同步 / CHANGELOG / memory / 汇报 | `http_routes.py` + 前端 `WorkbenchView.vue` + `productionPlanToProject.ts` + 测试 | 后端测试 + py_compile；前端测试 + build；双目录 md5 一致 |

---

## 10. 明确不碰（铁律，已验收层）
- Asset Registry / Stable Entity Key / Alias 归一（#388/#402/#404）——**只读复用，绝不改**。
- Shot 级资产边界（#471）、DirectorIntent 三层可追溯（#400）、h3_prompt_builder + P0-A 提交链路（#461/#477-480）——P1-B-4/P1-B-5 只接线不改架构。
- executor / segment_cache / 导出 / 归档 / 播放。
- 显存安全门（/api/ps）、VAE 非动态化（#485/#487）。
- 现有 `/script/import`（结构化剧本）路由与行为**不动**（新路由并行）。
- `production_plan.py` 加字段**不破坏旧数据**（beats 缺省空列表）。
- **工作台 Story Beats 面板本轮不加**（拍板③）。

---

## 11. 核心原则（拍板⑦，执行时挂在嘴边）
> **Location 是世界资产容器，Timeline 是播放顺序，Beat 是剧情结构，Shot 是实际拍摄单位。四者不要混为一谈。**
