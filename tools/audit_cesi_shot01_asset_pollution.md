# 审计报告：cesi Shot 01 跨镜资产污染 + 山雨楼外未识别 + Prompt 五区回退

> 审计模式：**只审计，不改代码**（audit-only）。
> 审计对象：项目 `script-20260812173341`（SPA 显示名 cesi）· Shot 01 · Generation #1 快照
> 审计时间：2026-08-13
> 证据全部来自真实落盘数据（project.json / snapshot / asset_registry.json）+ 源码调用链，无猜测。
> 范围：`script_parser → script_analyzer → entity_cleanse → asset_matcher → productionPlanToProject → project.json → generation payload → gen_timeline → executor`。

---

## 0. 结论速览（TL;DR）

| 现象 | 严重度 | 结论 |
|---|---|---|
| Shot 01 显示「灯笼、旧剑匣、伞」 | 🔴 明确 Bug | 场景池（全场 props 合并）被当作「单镜资产」展示 + **生成侧 extra_assets 把场景池 props 全量注入段 ref** |
| `山雨楼外` 没有成为 location 参考图 | 🔴 高概率 Bug | cesi 剧本 scene header 无 location_name → plan 无 location 实体 → binding 无法触发（registry 里绑定存在但空转） |
| 最终 prompt 是旧五区拼接 | 🟠 架构分期结果 | 生成链路走 `buildShotPromptText` 五区 join；三层 DirectorIntent / h3_prompt_builder 只在 `/prompt/h3` 审计路由，未接入生成链 |

审计发现两个系统性问题（铁律候选）：
1. **一个 Shot 的生成输入，理论上可以拿到其他 Shot 的资产**（gen_timeline `extra_assets` 全场景池注入，无 per-shot 边界）。
2. **用户原文在生成 prompt 里被改写**（visual 区是 script_analyzer 对 source_text 的 AI 转写；原文只保留在 description「原文：」行）。

---

## 1. 审计对象与证据清单

| 证据文件 | 路径 | 用途 |
|---|---|---|
| 项目数据 | `minimax_studio/projects/script-20260812173341/project.json` | 场景/镜头/资产真实状态 |
| 生成快照 | `minimax_studio/snapshots/20260813_013454_*.json` | Generation #1 实际 payload（segment prompt / refs / scenes） |
| 资产注册表 | `minimax_studio/asset_registry.json` | persisted 绑定真实状态 |
| 前端映射 | `frontend/src/core/productionPlanToProject.ts` | 剧本 → SPA Project |
| 继承解析 | `frontend/src/core/inheritanceResolver.ts` | Workbench「👤 资产」行数据源 |
| 生成 prompt | `frontend/src/core/directorCore.ts` / `promptSections.ts` | toTimelineShot / buildShotPromptText |
| 段装配 | `frontend/src/adapters/minimaxH3Adapter.ts` | TimelineStructure → timeline_data |
| 后端段解析 | `director/gen_timeline.py` | timeline_data → SegmentPlan（含 extra_assets） |
| 后端执行 | `director/executor_core.py` | 段资产注入 / ref 组装 |
| 实体→资产 | `director/asset_matcher.py` / `entity_cleanse.py` | 匹配 / 边界检查 |
| H3 三层 | `director/h3_prompt_builder.py` / `http_routes.py` | DirectorIntent 消费点（仅审计路由） |

---

## 2. 问题 G（先证）：真实数据流是什么？

用户要求先看真实数据，逐层核对：

```
```
剧本(cesi) ──script_analyzer──▶ ProductionPlan
   └ scene_01「第一场：山雨楼外」：location_name 为空（无「地点：」标签，仅标题）
   └ shot_01：characters=[]，props=[]，entities=[]（空镜远景）
   └ shot_02：灯笼
   └ shot_03：旧剑匣、伞、沈青崖
        ├─entity_cleanse──▶ 注册表：沈青崖(character,0.9+)、旧剑匣(prop)、灯笼(prop)、伞(prop)
        ├─asset_matcher──▶ match_plan：
        │    ├ 沈青崖→角色_沈青崖_主视图.png ✓（persisted/auto）
        │    ├ 旧剑匣→旧剑匣.png ✓（persisted/auto）
        │    ├ 灯笼→无候选（资产库无灯笼图）
        │    ├ 伞→无候选（资产库无伞图）
        │    └ ★ location 实体 = 0 条（因为 scene location_name 为空 → collect_plan_entities 没得收集）
        │        → `山雨楼外` 的 binding（location:山雨楼外→asset_015）**从未被任何匹配消费**
        └─productionPlanToProject──▶ project.json：
             scene_01.assets = { cast:[沈青崖], props:[灯笼, 旧剑匣, 伞], locations:[], styles:[] }   ← 场景池=全场并集
             scene_01.defaultLocationId = 空（locMatch 未命中）
             shot_01 = { castIds:[], locationId: 空, content.visual=首段 AI 转写 }

Workbench「👤 资产」显示 = resolveInheritance → resolveCollection("prop") = 场景池 props **全量** [灯笼, 旧剑匣, 伞]

生成（前端）→ directorCore.toTimelineShot → buildShotPromptText（五区 join）
          → MiniMaxH3Adapter → segments[].prompt（与快照逐字一致）
生成（后端）→ gen_timeline：
     seg.sceneId = scene_01 → seg_scene.assets.props = [灯笼, 旧剑匣, 伞]
     → extra_assets.extend(scene props **全量**)   ← 旧剑匣.png（有图）
     → executor：asset_items += 旧剑匣 → 注入 ref_image
```

**快照逐字验证**（Generation #1，segments[0] = shot_01）：

```json
{
  "id": "shot_01", "sceneId": "scene_01",
  "prompt": "远景展现山雨楼与山道尽头的雾气，突出檐角滴水的细节，全景，缓摇交代场景环境，雨氛围。，电影感，冷色调，湿地面反射，雨丝可见，画面干净通透，人物边缘清晰。，雨声淅沥，舒缓氛围配乐。",
  "refs": [], "castIds": [], "locationId": "", "genImage": {"imageFile": ""}
}
```

注意 prompt 是 **五个分区逗号拼接**（visual，camera，style。，sound。），正是 `buildShotPromptText` 的输出格式（`promptSections.ts`：`[visual, cameraText, style, sound].filter(Boolean).join('，')`）。

同时 scenes 块：`scene_01.assets.props` 含 `{"id":"旧剑匣","imageFile":"minimax_studio/assets/旧剑匣.png"}`（有图）→ 后端 `gen_timeline` 会把它挂到 shot_01 的 `extra_assets` → executor 注入。**这就是「Shot 1 的视频里冒出旧剑匣」的机制层证据。**

而顶层 `timeline.assets = {cast:[], locations:[]}`、scene 无 `defaultLocationId` → shot_01 无 location ref → `山雨楼外` 图没进生成。

---

## 3. 问题 A：为什么 Shot 01 显示「灯笼、旧剑匣、伞」？

**根因**：`collectSceneAssets`（productionPlanToProject.ts）把**场景内所有 shots** 的角色/道具合并成一个场景池（`castSeen/propsSeen` 全场景去重），**没有 per-shot 边界**；随后该场景池在两层被消费：

1. **展示层（Workbench「👤 资产」）**：`infoAssetNames = inheritance.value.map(...)`（WorkbenchView.vue L2561），而 `resolveInheritance → resolveCollection("prop")` 直接返回场景池 props 全量 → 单镜卡片显示 [灯笼, 旧剑匣, 伞]，即使 shot_01 原文只有「山雨楼/山道/雾气」。

2. **生成层（后端 real pollution）**：`gen_timeline.py` L656-659：
   ```python
   # 场景素材组里的道具 prop / 风格参考 style → 附加资产（executor 作为额外 ref 注入）
   if seg_scene:
       for _k in ("props", "styles"):
           extra_assets.extend(seg_scene.assets.get(_k) or [])
   ```
   → `executor_core.py` L711-718 把 `seg.extra_assets` 的 tensor 注入 `asset_items` → ref_image。
   → 有图的「旧剑匣.png」真的成为 Shot 01 的参考图（灯笼/伞无图被跳过）。

**代码位置**：`productionPlanToProject.ts collectSceneAssets`、`inheritanceResolver.ts resolveCollection/prop`、`gen_timeline.py:656`、`executor_core.py:709-736`。

**当前实际值**：shot_01（空镜）拿到场景池 [灯笼, 旧剑匣, 伞] 三者在 Workbench 展示；生成侧注入旧剑匣 ref。

**正确应该是什么**：单镜的资产 = 该镜**实际出现/明确 @ 引用**的实体资产。scene 池是「候选池」，不是「默认注入集」。

**最小修复方案**（仅建议，本次不改）：
- A1（展示）：Workbench 资产行改为「本镜实际引用」（castIds + 本镜 @ 标签 + 本镜 entities），场景池折叠为「场景素材组」。
- A2（生成）：gen_timeline `extra_assets` 只收集「本段 prompt @ 引用的 prop」+「本段 castIds/locationId 显式指定的资产」；不把场景池 props 全量挂段。可复用 entity_cleanse 的 shot 级边界（边界检查见 §6）。

---

## 4. 问题 B：为什么「山雨楼外」没有成为 location 参考图？

**根因链**：
1. cesi 剧本 scene header **没有 location_name**：scene_01 只有标题「第一场：山雨楼外」，无「地点：xxx」标签行 → script_parser/analyzer 的 location_name 落为空（project.json `scene_01.location` 为空、`location` 字段缺失）。
2. `collect_plan_entities`（asset_matcher.py）只从 `plan.scenes[].location_name` 收集 location 实体 → **location 实体 0 条** → asset_matcher 输出无 location match。
3. `productionPlanToProject` 的 location 分支用 `matchesByKey.get(\`location:${locName}\`)` 门控，locName 为空 → 不注册、不设 `defaultLocationId`、不设 shot.locationId。
4. **asset_registry 里的绑定是好的但空转**：`location:山雨楼外→asset_015`（accepted, updated 2026-08-11）存在，但 cesi 的 match_plan（08-13 01:33）里**根本没有 location 实体可绑定**（persisted binding 只在实体出现时覆盖，不能凭空生成实体——这是设计铁律）。
   - 佐证：registry 中 character:沈青崖 / prop:旧剑匣 的 updated_at = `2026-08-13T01:33`（cesi 导入时用户确认）；而 location 绑定 updated_at = `2026-08-11`（更早的《山雨客栈》导入）——本来就不需要重新确认，因为 cesi 根本没 location 实体。

**代码位置**：`script_parser.py` location 解析（无「地点：」标签行）、`entity_cleanse.py`/`collect_plan_entities`（实体来源无 location）、`asset_matcher.py: match_plan`（无实体可绑）、`productionPlanToProject.ts:145/167/244-253`（locMatch 门控空转）。

**当前实际值**：timeline `locations=[]`、`defaultLocationId=""`、语义位置提示缺失、生成无位置参考图。

**正确应该是什么**：scene 标题「第一场：山雨楼外」应可反向锚定 registry 已确认的 `location:山雨楼外` 绑定（标题含「山雨楼外」子串 vs 已确认 binding 的 canonical 名）。

**最小修复方案**（建议，符合「registry 只记住确认、不生成实体」）：
- B1：scene header 无 location_name 时，用场景标题（去掉「第X场：」前缀）兜底 location 候选，参与规范化匹配（与已确认 binding 比顺序）。
- B2：asset_match 输出增加「suggested_location」字段（scene title → binding 命中），前端建影时在 location 空时用建议值预填。

---

## 5. 问题 C / D：为什么最终 prompt 是旧五区拼接，而不是 h3_prompt_builder 三层？

**根因**：三条链路并存，但生成链路只走「五区拼接」：

| 链路 | 入口 | 输出 | 是否进生成 |
|---|---|---|---|
| ① 五区拼接 | `directorCore.toTimelineShot` → `buildShotPromptText` | `[visual,camera,style,sound].join('，')`（promptSections.ts） | ✅ **生成真走这条**（快照逐字证实） |
| ② 五区 draft | `/prompt/draft`（导入主路由） | PromptDraftItem 五区字段 → `content.visual/cameraText/...` | ✅ 进 project.json 的 content（审核预览） |
| ③ 三层 H3 | `/prompt/h3`（**审计路由**）+ `h3_prompt_builder.py` | DirectorIntent → 结构化 H3 Prompt（integrated_multimodal_description 等） | ❌ **只出现在「AI 理解 / H3 Prompt 展示」审计端点** |

`http_routes.py` 里 `h3_prompt_builder` 的唯一消费点是 `/minimax/director/prompt/h3`（审计/展示）。executor 不读取 DirectorIntent，只读 `segment.prompt`（已由前端拼好）→ MiniMax 输入。

**代码位置**：`promptSections.ts buildShotPromptText`、`directorCore.ts toTimelineShot`、`adapters/minimaxH3Adapter.ts toSegment(prompt)`、`http_routes.py /prompt/h3`、`h3_prompt_builder.py`。

**当前实际值**：shot_01 生成 prompt = 五区 join（visual 为 AI 对原文的转写，非原文逐字）。三层未接入。

**正确应该是什么**：三层链路是 V1.7 规划的方向（P0-③），生成链应消费 DirectorIntent（用户原文 L1 → 导演意图 L2 → H3 Prompt L3）。本次仅确认现状，接入属规划项。

**最小修复方案**：已在待办（P0-③）；本次审计不动。铁律「用户原文不能被 AI 理解改写掉」可先行落地：五区 visual 生成时强制附加/保留 source_text 原文（或至少展示 diff），避免无声改写。

---

## 6. 问题 E：shot 级资产是否严格只从当前 shot 过滤？

| 环节 | 现状 | 是否有 per-shot 边界 |
|---|---|---|
| `collectSceneAssets`（前端场景池） | 全场 props/entities 并集 | ❌ 无（设计为场景池，但被当作单镜资产展示+注入） |
| `shot.castIds`（前端） | 按本镜 characters 出现过滤 + matches 命中 | ✅ 有 |
| `locationId` | 场景单值，需 locMatch 命中 | —（本审计 cesi 无 location） |
| `gen_timeline extra_assets`（后端注入） | 场景池 props/styles 全量→段 | ❌ **无**（#A2 核心） |
| `entity_cleanse entity_in_shot_scope` | Shot 实体级边界（min_len=20） | ✅ 有，但**只用于 Qwen 实体注册表**，与资产注入链路脱节 |

结论：资产注入链路（后端 gen_timeline→executor）与场景池展示链路均无 per-shot 隔离；边界检查只存在于实体命名阶段，未延伸到资产分配阶段。

---

## 7. 问题 F：location/character/prop ref binding 是否正确？

| 绑定 | registry 状态 | 是否被消费 | 结论 |
|---|---|---|---|
| `character:沈青崖→asset_022` | accepted, user, 08-13 01:33 | ✅ match_plan → project.json cast[0].imageFile 有图 | 正确 |
| `prop:旧剑匣→asset_017` | accepted, user, 08-13 01:33 | ✅ → scene props[1].imageFile 有图 | 正确（但被 A2 错误地全量注入到其它镜） |
| `prop:灯笼` | 无资产 → 无绑定 | imageFile="" | 正常（资产库无灯笼图） |
| `prop:伞` | 无资产 → 无绑定 | imageFile="" | 正常 |
| `location:山雨楼外→asset_015` | accepted, user, 08-11 | ❌ **从未触发**（cesi 无 location 实体） | 绑定正确但**空转** = B 根因 |

结论：绑定的**持久化机制本身正确**（08-13 用户确认的 cast/prop 都正确落盘并被消费）；错误在「绑定对象根本不存在的场景」（location 实体没进 plan）与「合理绑定被错误地全场景扩散」（A2）。

---

## 8. 建议：per-shot 资产边界测试（新增，用户本机可跑）

> 铁律 ① 一个 Shot 不能拿到其他 Shot 的资产；② 用户原文不能被 AI 理解改写掉。

新增后端单测 `tests/test_asset_shot_boundary.py`（audit-only 阶段的建议，未实施）：

```python
def test_shot_never_gets_other_shot_assets():
    # 构造：scene 含 shot A（无道具）+ shot B（旧剑匣/伞）
    # 断言：shot A 的 SegmentPlan.extra_assets 不含旧剑匣/伞
    ...

def test_location_binding_fired_only_when_entity_exists():
    # registry 有 location:山雨楼外 绑定
    # 场景无 location_name → match_plan matches 里无该 location 行
    ...

def test_generation_prompt_preserves_source_text():
    # visual 区若来自 AI 转写，必须在可见层保留原文（description/原文行）
    ...
```

---

## 9. 修复方案汇总（仅建议，本次 audit-only 未做任何改动）

| # | 修复点 | 文件 | 类型 |
|---|---|---|---|
| A1 | Workbench 资产行 = 本镜实际引用（castIds + @ 标签 + 本镜实体），场景池单独折叠 | WorkbenchView.vue / inheritanceResolver.ts | 前端展示 |
| A2 | gen_timeline extra_assets 只收集本段 @ 引用/显式指定的 props，不注入场景池全量 | gen_timeline.py:656 | 后端生成 |
| B1 | scene header 无 location 时用场景标题兜底参与规范化匹配 | script_parser.py / asset_matcher.py | 后端实体 |
| B2 | match_plan 输出 suggested_location（标题→已确认 binding），前端预填 | asset_matcher.py / comfyApi.ts | 前后端 |
| C | 生成链接入 DirectorIntent / h3_prompt_builder（规划项 P0-③） | — | 架构分期 |
| 铁律② | 五区 visual 生成强制保留/展示原文 | promptSections.ts / 相关 | 防改写 |

**没有修改**：Qwen Prompt、Alias 结构、实体类型体系、Asset Registry、UI 表面（除审计展示层分析）。