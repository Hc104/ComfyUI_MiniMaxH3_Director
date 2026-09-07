# 《AI 漫剧导演运镜规则 v1.0》设计文档

> 2026-08-17 · 面向 MiniMax H3 Director 自动拆镜/生成 Prompt 的运镜规则层
> 状态：**待用户拍板**（拍板后按实施计划落地）
> 上游：#600 camera-v2 强动态运镜（已上线）→ 本版把「堆镜头术语」升级为「规则层选镜」

---

## 0. 为什么从「堆术语」升级为「规则层」

#600 已把所有模板改造成强动态措辞（俯冲推进/正反打/甩镜），但**选择逻辑没变**——
仍按剧情关键词命中固定模板。用户评审后指出三个致命问题：

1. **机械套四镜公式会套路化**：「感情越浓越近、关键台词必须动、所有对话都正反打」是**硬规则**而非默认规则 → 成片千篇一律。
2. **最缺的是连续性**：《吐槽在漫画里封神》双人对白，A 左 B 右一旦跳轴，观众直接晕。现有系统**完全不管角色屏幕位置/视线轴**。
3. **运动过度**：「关键台词必须动」会逼 AI 每句都推镜 → 该静止的近景反而更有力。

**核心心法**（用户原话）：运镜是「情绪放大器」，不是「炫技工具」。
**规则不是「情绪 → 固定镜头」，而是「情绪 → 导演意图 → 镜头选择」。**

---

## 1. 现状链路盘点（已核对代码）

```
ProductionPlan (shot: emotion/dialogue/actions/visual_intent)
   ↓ 按 timeline 播放顺序
build_plan_intents (director_intent.py)
   ├─ pick_camera (camera_template.py)   ← 15 模板 + 跨镜状态机，关键词命中
   │     → camera_desc（完整运镜措辞，含前缀/正反打/景别节奏）
   └─ build_shot_intent → DirectorIntent
        ├─ camera_position / movement   （模板 → 中文景别/运镜）
        └─ continuity：camera_desc / transition_type / prev_shot_state / must_keep / may_change
   ↓
h3_prompt_builder._build_description
   ├─ 镜头：{camera_desc}                ← 渲染主措辞
   ├─ 跨镜延续块（上镜状态+镜头关系+must_keep）
   └─ 对白 <d> 块 + 无字幕约束（#597）
```

**关键缺环**：
| 缺什么 | 现状 | 用户要求 |
|---|---|---|
| 镜头功能（establish/info/reaction/emotion/ending） | 无，只有粗分类 intent | 每镜先定「观众该看谁的反应」 |
| 景别/机位/运动/构图四维决策 | 模板写死 | 按情绪浓度/权力关系/台词功能选 |
| 180° 轴线 + 左右位置 + 视线轴 | 无 | **硬约束，防晕** |
| 反应 > 说话 | 无 | 特写给听众 |
| 关键台词情绪变化才运动 | v2 全动态（过度） | 情绪爆点才推镜 |

---

## 2. 三层规则架构（对应你的总纲调整）

### 第一层：连续性硬约束（必须遵守 · 防晕）

**新模块 `director/spatial_continuity.py`（纯规则，零 LLM/零显存）**

每场景维护一个 **空间状态**：

```python
SpatialState = {
  "axis": {"林薇薇": "screen_left", "顾琰宸": "screen_right"},  # 同场景固定
  "eye_line": "林薇薇->顾琰宸",          # 视线轴（谁看向谁）
  "camera_on_axis": True,               # 机位永在 A–B 轴线上
}
```

**规则**（全部硬约束）：
- 同场景内角色屏幕左右位置**一经建立永不翻转**（跳轴是硬错误）；
- 初始位置判定优先级：①剧本原文「X 在 Y 左边/右边/对面/一侧」→ ②出场顺序（首角色左、次角色右）→ ③前镜继承；
- 过肩镜（OTS）：说话人=前景主体时，**前景在屏幕另一侧**（如拍顾琰宸，林薇薇在前景左侧），听者方向=视线方向；
- 视线方向：A 看 B（B 在右）→ A 视线朝右；反打时 B 视线朝左；
- 场景切换 → 空间状态重置（新场景重新建立）；
- 交叉剪辑切回原场景 → 恢复该场景上次空间状态（与 scene_prev_state 同机制）。

**输出**（进 `continuity.spatial`，H3 渲染为双语行）：

```
Spatial Continuity:
- Gu Yanchen: screen left
- Lin Weiwei: screen right
- Eye-line axis: fixed (Lin–Gu)
- Camera remains on the Lin–Gu axis
```

> 为什么双语：H3 对英文摄影术语（screen left / over-the-shoulder / eye-line）执行更稳，
> 中文作可读语义。与 #600 英文术语辅助同策略。

---

### 第二层：导演规则（情绪 → 意图 → 镜头选择）

**镜头功能 Shot Macro-Role**（新纯规则 `assign_shot_role`，替代机械四镜）：

| 功能 | 判定 | 落点 |
|---|---|---|
| `establish` 建立 | 场景首镜且无角色 / 纯环境 | 全景交代空间 |
| `info` 信息 | 有对白且无强情绪（普通说事） | 中近景、**静止**或轻微 |
| `reaction` 反应 | 无对白、上镜有对白、有人物（**听比说重要**） | 特写给听众 |
| `emotion` 情绪 | 对白含爆发（！？/质问/表白）或镜头情绪强 | 特写 + 推镜 |
| `ending` 收尾 | 场景尾镜（无对白进行中） | 中全景拉远/留白 |

**四维决策**（新纯规则 `decide_dimensions`）：

| 维度 | 决策依据 | 取值 |
|---|---|---|
| **景别**（看多远） | 情绪浓度/镜头功能 | 情绪镜→Extreme close-up；反应镜→Close-up/OTS；信息镜→Medium；建立/收尾→Wide/Medium full |
| **机位**（从哪看） | 权力关系 + 情绪 | 强者/反派/威严→Low-angle；弱小/无助/受害者→High-angle；平等/亲近→Eye-level |
| **运动**（怎么动） | **关键台词情绪变化才动** | 情绪爆点→Slow push-in；离开/真相→Pull out；走路/追逐→Tracking/Handheld；中性台词→**静止近景** |
| **构图**（怎么摆） | 关系 + 180° 轴线 | 亲密/对峙→Two-shot；轴线对白→OTS（前景听者）；反应→单人特写；**反应>说话** |

**机位权力关系来源**：角色 role 关键词（强者/反派/威严/家主/上司 → 低机位；女侠/弱势/无助/受害者 → 高机位）＋
镜头情绪（惊恐/压迫 → 低机位）。

**关键台词情绪变化才运动的实现**：
- 情绪爆点判定：镜头 emotion ∈ 强情绪表（紧张/愤怒/惊恐/震惊/崩溃/表白…）**或** 对白行含 `！？`/质问词（「还是说」「就这？」「凭什么」）/情绪词；
- 命中 → movement 启用（Slow push-in / Pull out）；
- 未命中 → movement = `static`，`镜头：中近景静止，聚焦{subject}，画面沉稳。`（保留呼吸感，不动）；
- 这条直接推翻 v2「每镜必动」，正是用户「该静止的力量」要求。

---

### 第三层：五镜对话公式（默认对话节奏）

**现状**：所有对白镜 → `shot-reverse-shot`，无「观众该看谁」的优先级。
**升级**：对话场景默认节奏 = **建立 → 信息 → 反应 → 情绪 → 收尾**，
每镜通过 **Shot Macro-Role** 落位，相机跟随其功能：

```
Shot 01 建立    两人 + 豪华办公室            → establish（全景交代）
Shot 02 信息    林薇薇拿起支票               → info（中景，静止）
Shot 03 情绪    林薇薇近景「就这？」          → emotion（特写 + 慢推）
Shot 04 反应    顾琰宸第一次愣住              → reaction（特写给听众）
Shot 05 爆点    林薇薇「白月光只值这个价？」   → emotion（推镜 + 情绪）
Shot 06 反应    顾琰宸瞳孔收缩                → reaction（特写）
Shot 07 收尾    空气凝固，两人对峙            → ending（拉远/留白）
```

> **v1.0 范围**：本版在「一镜 = 一次 H3 生成」模型内落地——用户手动拆好的镜，每镜自动获得
> 对应 Macro-Role 与四维相机，不再全部正反打。
> **v1.1（后续）**：「对话场景自动扩成 5 镜」需要动 shot 拆分 + 前端回写，另立项。

---

## 3. 数据模型与代码改动点

### DirectorIntent.continuity 扩展

```python
continuity["shot_role"] = "reaction"        # establish/info/reaction/emotion/ending
continuity["spatial"] = {
  "axis": {"林薇薇": "screen_left", "顾琰宸": "screen_right"},
  "eye_line": "林薇薇->顾琰宸",
  "camera_on_axis": True,
  "block": "Spatial Continuity:\n- Lin Weiwei: screen left\n- Gu Yanchen: screen right\n- Eye-line axis: fixed (Lin–Gu)\n- Camera remains on the Lin–Gu axis",
}
```

### 改动文件清单

| 文件 | 改动 |
|---|---|
| `director/spatial_continuity.py` | **新增**：SpatialState + 建立/更新/重置 + block 渲染（纯规则） |
| `director/director_rules.py` | **新增**：assign_shot_role + decide_dimensions + 情绪爆点判定（纯规则） |
| `director/camera_template.py` | `pick_camera` 扩展：接收 shot_role/spatial，`decide_dimensions` 选模板措辞；保留 v2 强措辞库 + `_DEFAULT_TEMPLATE` 改「静止近景」版；fallback 不变 |
| `director/director_intent.py` | `build_shot_intent`/`build_plan_intents`：串 shot_role + spatial 状态机，写 `continuity.shot_role` + `continuity.spatial` |
| `director/h3_prompt_builder.py` | `_build_description`：镜头行不变（camera_desc）；新增「空间连续性」双语行渲染；情绪镜推镜/中性静止由 camera_desc 承载 |
| `director/prompt_builder_v17.py` | camera 草稿派生自 DirectorIntent（已如此），无需大改；`build_plan_drafts` 透出 shot_role/spatial |
| 测试 | 新增 `test_director_rules.py` + `test_spatial_continuity.py`；更新 `test_camera_template.py` / `test_camera_plan.py` / `test_h3_prompt_builder.py` 断言 |

### 接入链路（改后）

```
build_plan_intents（按 timeline）
  ├─ prev_state（相机状态机，已有）
  ├─ spatial_state（新增，按场景维护）
  ├─ shot_role = assign_shot_role(shot, scene, 场景序号)
  ├─ dims = decide_dimensions(shot, scene, role, spatial, prev_state)
  ├─ camera_desc = render_camera(dims)      ← v2 强措辞库渲染
  └─ build_shot_intent(… continuity.shot_role + continuity.spatial)
        ↓
h3_prompt_builder：镜头：{camera_desc} + 空间连续性双语行 + 跨镜延续 + 对白 <d>
```

---

## 4. 回归与验收

- **后端**：新 2 个测试文件 + 更新 3 个既有文件；全量 48 文件回归绿。
- **前端**：数据结构向后兼容（continuity 增字段，前端只读）；566 测试 + build 绿。
- **双目录**：SRC≡DEP 全量 md5 DIFF=0（含新增 .py + CHANGELOG）。
- **⛔ 需重启 Comfy Desktop**（后端新模块 + http_routes/相机链路）。

---

## 5. 开放决策（请拍板）

1. **范围**：v1.0 按「一镜=一次 H3」落地三层规则（推荐，改动聚焦）；
   「对话场景自动扩 5 镜」放 v1.1（需动 shot 拆分+前端）——是否同意？
2. **初始左右位置**：优先解析剧本「X在Y左边/右边/对面」→ 否则按出场顺序（首左次右）——可否？
3. **H3 渲染语言**：空间连续性块用「中文 + 英文」双语（英文保执行稳）——可否？
4. **静止镜默认措辞**：中性台词 `中近景静止`（不再每镜必动）——确认这条作为新默认？
