# MiniMaxH3Director · 分镜提示词放置规范（Scene → Shot）

> **这份文档是给 AI 看的说明书。** 无论你是要**生成分镜提示词**，还是**读取/修改一份现有工作流**，都请先读完本文，再动手。
>
> **⚠️ 输出形态：你交付的是「每个镜头一段纯文本提示词」，不是 JSON 文件。** 用户会把你的输出**逐段粘贴到节点每个镜头的「提示词」文本框**。JSON 只是节点内部的存储结构，供你理解字段用，**不要输出 JSON**。
>
> 本文解决的问题：**不同场景的提示词该放在哪里、怎么放，才不会放错**。看完本文你会知道：
> 1. 提示词永远放在**镜头（Segment）**上，不放在场景（Scene）上；
> 2. 镜头靠 `sceneId` 归属到场景，场景靠 `order` 排序；
> 3. 角色/场景/道具/风格参考图怎么引用、优先级怎么算；
> 4. 节点内部的分镜 JSON 长什么样（仅供理解，不要输出）。
>
> 所有字段名与实际代码一致，可放心照抄。本文件只讲"分镜数据怎么写"，界面操作见 `OPERATION_GUIDE.md`，实现细节见 `CHANGELOG.md`。

---

## 0. 三个必须记住的核心规则

1. **提示词在镜头（Segment）上，不在场景（Scene）上。**
   `timeline.scenes[]` 里的每个场景只存「元信息 + 素材组」；所有画面描述都在 `timeline.segments[]` 的 `prompt` 字段。**不要把分镜描述写进场景对象。**

2. **镜头归属场景靠 `sceneId`，场景排序靠 `order`。**
   每个镜头 `segments[i].sceneId` 填它所属场景的 `id`。不填 = 无归属（后端按 location 连续性兜底）。场景在导出、面板里的顺序由 `scene.order` 决定，**不看数组下标**。

3. **资产引用按名字自动匹配 + 优先级是"场景素材组 > 全局资产库"。**
   提示词里写 `@角色名`（如 `@陆玄`），系统会在「该场景素材组 → 全局资产库」里按名字匹配，自动注入参考图。场景素材组（`scenes[].assets`）永远优先于全局资产库（`timeline.assets`）。

---

## 1. 数据模型总览

```
Project（一个 timeline JSON）
├── timeline.scenes[]        ← 场景（一级对象，素材组 + 镜头归属单位）
│    每个 Scene：{ id, name, location, time, order,
│                  assets: { cast[], locations[], props[], styles[] },
│                  defaultCastId, defaultLocationId }
├── timeline.segments[]      ← 镜头（Shot，提示词真正所在）
│    每个 Segment：{ id, prompt, negativePrompt, sceneId, castId, locationId,
│                    continuityMode, stateChange, durationSec/frameCount, refs[] }
├── timeline.assets{}        ← 全局资产库（4 类：cast / locations / props / styles）
│    每类是一个资产数组：[{ id, name, imageFile }]
└── timeline.output{}        ← 输出/导出设置（exportMode 等）
```

**三级资产结构：全局资产库（Global Asset Library）→ 场景素材组（Scene Asset Group）→ 镜头引用（Shot Reference）。**

---

## 2. Scene 场景对象（`timeline.scenes[]`）

一个场景 = 一组素材（角色/场景/道具/风格参考）+ 归属它的镜头。场景**不存提示词**。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填，唯一。镜头用这个值填 `segments[].sceneId`。格式建议 `sc+时间戳36进制`（如 `scm1x2y3`），任意唯一串即可。 |
| `name` | string | 场景名，如 `Scene 01`、`圣城广场·清晨`。 |
| `location` | string | 地点描述（可空）。仅作元信息；未设 `sceneId` 的镜头会用 location 连续性兜底分组。 |
| `time` | string | 时间描述（可空）。 |
| `order` | number | **排序依据**，决定场景在面板/导出中心的先后。建议 1,2,3…。 |
| `assets.cast` | array | 本场景角色素材组 `[{id,name,imageFile}]`。优先级高于全局资产库。 |
| `assets.locations` | array | 本场景场景素材组（同构）。 |
| `assets.props` | array | 本场景道具素材组（同构）。 |
| `assets.styles` | array | 本场景风格参考素材组（同构）。 |
| `defaultCastId` | string | 本场景默认角色（指向 `assets.cast` 里的某个 `id`），段没手动选角色时兜底。 |
| `defaultLocationId` | string | 本场景默认场景（指向 `assets.locations` 里的某个 `id`），同理。 |

**资产对象结构**（场景素材组和全局资产库完全一致）：

```json
{ "id": "sca1abc", "name": "陆玄", "imageFile": "cast/陆玄.png" }
```

`name` 与提示词里的 `@名` 匹配用，命名要跟提示词里写的一致（如都叫"陆玄"，别叫"Luxuan"）。

---

## 3. Segment 镜头对象（`timeline.segments[]`）

**这里是提示词放置的唯一正确位置。** 每个数组元素 = 一个镜头。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填，唯一。 |
| `prompt` | string | **本镜头的画面提示词（核心）**。支持 `@角色名/场景名` 自动匹配资产、`<Picture N>`/`<Video N>`/`<Audio N>` 引用素材。**每镜必须带明确的运镜设计**（推/拉/摇/移/环绕/升降），不要写一镜到底固定机位。 |
| `negativePrompt` | string | 负面提示词（可空）。 |
| `sceneId` | string | **本镜头归属的场景 id**（`scenes[]` 里某个 `id`）。空串 = 未归属。这是「场景提示词放置」的关键字段——**同场景的镜头填同一个 `sceneId`**。 |
| `castId` | string | 手动指定角色资产 id（可空；不填时按 `prompt` 里的 `@角色名` 自动匹配，再兜底场景默认/全局默认）。 |
| `locationId` | string | 手动指定场景资产 id（可空；同理）。 |
| `continuityMode` | string | `auto` / `ref2va` / `fl2va`。`auto`=按提示词关键词自动路由（拔剑/变身/打斗/跳跃/开门/奔跑…自动转 fl2v）；`fl2va`=强制首尾帧硬锁；`ref2va`=强制参考图状态驱动。 |
| `stateChange` | string | 本镜结束后传给下一镜的状态描述（可空，如"陆玄拔剑冲向敌人"）。 |
| `durationSec` | number | 镜头时长（秒）。建议 ≤15s。与 `frameCount` 二选一即可（前端以 durationSec 为准）。 |
| `frameCount` | number | 总帧数。与 `durationSec` 二选一。 |
| `refs` | array | 参考图列表 `[{index,imageFile}]`。r2v 段通常不手填（自动续接 + 资产注入），fl2v 段 index 0=首帧、1=结束帧。 |
| `taskType` | string | 任务类型（t2v/r2v/fl2v 等；通常在 timeline 全局设置，段级可覆盖）。 |

**提示词运镜规范（重要）**：每镜 prompt 必须写明镜头运动，推荐用这样的弧线：「俯冲 → 跟移 → 摇 → 推近 → 环绕 → 升起拉远」。示例：

```
陆玄站在圣城广场中央，衣袍随风微动，镜头缓缓推近，随后环绕半周
```

---

## 4. 全局资产库（`timeline.assets{}`）

| 键 | 说明 |
|---|---|
| `cast` | 全局角色库 `[{id,name,imageFile}]` |
| `locations` | 全局场景库 |
| `props` | 全局道具库 |
| `styles` | 全局风格参考库 |
| `defaultCastId` | 全局默认角色 id（可空） |
| `defaultLocationId` | 全局默认场景 id（可空） |

**资产继承链（Scene → Shot → Scene Assets → Global Assets）**：

层级从高到低——

1. **段（Shot）手动选择**：`segments[].castId / locationId`。镜头卡片里选了具体资产即生效，且标记 `castManual/locationManual=true`（含显式选「无」= 空串，表示本镜头不注入任何角色/场景资产，禁止被自动继承覆盖）。
2. **场景默认**：所属场景的 `defaultCastId / defaultLocationId`。镜头未手动选择时，自动继承场景默认（前端 `inheritSceneAssetsToShots` + 后端兜底双重保障；场景默认变更后，未手动选择的镜头会跟随）。
3. **同名匹配**：提示词里的 `@名字` 在「场景素材组 + 全局资产库」合并池里匹配，场景素材组优先。仅在镜头未手动选择、且场景无默认时生效。
4. **全局默认**：`timeline.assets.defaultCastId / defaultLocationId`。

附加素材：每个镜头（segment）还可以上传**自己的参考图/音频/视频**（`refs / refAudios / refVideos`），独立于场景与全局资产库，作为该镜头独有素材注入。

**Scene 与 Global 的关系**：Scene 素材组是"本场景专用"的资产（在 Scene Manager 里上传/管理），同名资产优先于全局；全局资产库是兜底池，任何镜头都能引用。两者在镜头下拉里按 `🎬 场景素材组 / 🌐 全局资产库` 分组展示。

---

## 5. 节点内部结构示例（仅供 AI 理解，不要输出这个）

下面这个例子展示节点内部 `timeline` JSON 里**两个场景、四个镜头**的正确放法。**这不是你要交付的格式**——你的交付格式见第 6 节（纯文本提示词列表）。看懂这个 JSON 是为了让你理解：每段提示词对应哪个字段、场景归属靠什么连接（`sceneId` 指回 `scenes[].id`）。

- Scene 01「圣城广场·白天」：Shot 01、Shot 02
- Scene 02「暗巷·夜晚」：Shot 03、Shot 04

```json
{
  "timeline": {
    "scenes": [
      {
        "id": "sc-sq",
        "name": "圣城广场·白天",
        "location": "圣城广场",
        "time": "白天",
        "order": 1,
        "assets": {
          "cast": [ { "id": "sca-lx", "name": "陆玄", "imageFile": "cast/陆玄.png" } ],
          "locations": [ { "id": "sca-plaza", "name": "圣城广场", "imageFile": "loc/圣城广场.png" } ],
          "props": [],
          "styles": []
        },
        "defaultCastId": "sca-lx",
        "defaultLocationId": "sca-plaza"
      },
      {
        "id": "sc-lane",
        "name": "暗巷·夜晚",
        "location": "暗巷",
        "time": "夜晚",
        "order": 2,
        "assets": {
          "cast": [ { "id": "sca-lx", "name": "陆玄", "imageFile": "cast/陆玄.png" } ],
          "locations": [ { "id": "sca-lane", "name": "暗巷", "imageFile": "loc/暗巷.png" } ],
          "props": [],
          "styles": []
        },
        "defaultCastId": "sca-lx",
        "defaultLocationId": "sca-lane"
      }
    ],
    "assets": {
      "cast": [ { "id": "sca-lx", "name": "陆玄", "imageFile": "cast/陆玄.png" } ],
      "locations": [
        { "id": "sca-plaza", "name": "圣城广场", "imageFile": "loc/圣城广场.png" },
        { "id": "sca-lane", "name": "暗巷", "imageFile": "loc/暗巷.png" }
      ],
      "props": [],
      "styles": []
    },
    "segments": [
      {
        "id": "seg01",
        "sceneId": "sc-sq",
        "prompt": "陆玄站在圣城广场中央，身姿挺拔，仰头望向天碑，阳光洒落。镜头缓缓推近。",
        "negativePrompt": "",
        "castId": "sca-lx",
        "locationId": "sca-plaza",
        "continuityMode": "auto",
        "durationSec": 8
      },
      {
        "id": "seg02",
        "sceneId": "sc-sq",
        "prompt": "陆玄目光一凝，猛地拔剑，剑光闪动，脚步迅疾踏出。镜头俯冲跟移。",
        "negativePrompt": "",
        "castId": "sca-lx",
        "locationId": "sca-plaza",
        "continuityMode": "auto",
        "durationSec": 8
      },
      {
        "id": "seg03",
        "sceneId": "sc-lane",
        "prompt": "夜晚暗巷，陆玄收剑入鞘，缓步前行，灯光昏暗。镜头摇移跟随，随后升起拉远。",
        "negativePrompt": "",
        "castId": "sca-lx",
        "locationId": "sca-lane",
        "continuityMode": "auto",
        "durationSec": 8
      },
      {
        "id": "seg04",
        "sceneId": "sc-lane",
        "prompt": "陆玄停下脚步，抬头望向巷口，神色凝重。镜头推近特写。",
        "negativePrompt": "",
        "castId": "sca-lx",
        "locationId": "sca-lane",
        "continuityMode": "auto",
        "durationSec": 8
      }
    ],
    "output": {
      "exportMode": "scene"
    }
  }
}
```

---

## 6. 如果你是"生成分镜的 AI"：输出要求

**⚠️ 重要：你交付的是「每个镜头一段纯文本提示词」，不是 JSON 文件。** 用户会把你的输出**逐段粘贴到节点每个镜头的「提示词」文本框**，并按场景标注把每个镜头的「所属场景」下拉选到对应场景。第 5 节的 JSON 只是节点内部结构，仅供你理解字段，**不要输出 JSON**。

收到剧本/设定后，请按以下步骤输出：

1. **先按场景分组**：按剧本地点/时间变化切分场景，列出场景清单（场景名 + 地点/时间）。**不要给场景写提示词**。
2. **按播放顺序编号写镜头**：每个镜头一段文本，段前标注它属于哪个场景（格式见下面示例）。
3. **每镜提示词要求**：
   - 开头点明角色 + 场景（用 `@名` 触发资产匹配，如 `@陆玄站在圣城广场…`）；
   - 描述当前动作/状态变化，动作用**具体动词**（低头+伸手+镜头拉近），别写弱动作（"缓缓低头"）；
   - **结尾必须写运镜**（推/拉/摇/移/环绕/升降/俯冲…）；
   - 控制在 1~2 句话，中文。
4. **资产名一致性**：场景素材组和全局资产库里已有的 `name`，提示词里必须用同名引用（`@陆玄` 匹配 name="陆玄"）。
5. **跨场景切换**：场景变（地点/时间变）就从新场景继续编号；镜头按播放顺序排列。
6. **输出格式（纯文本，可直接粘贴，示例）**：

```
【场景：圣城广场·白天】
镜头1：@陆玄站在圣城广场中央，身姿挺拔，仰头望向天碑，阳光洒落。镜头缓缓推近。
镜头2：@陆玄目光一凝，猛地拔剑，剑光闪动，脚步迅疾踏出。镜头俯冲跟移。

【场景：暗巷·夜晚】
镜头3：夜晚暗巷，@陆玄收剑入鞘，缓步前行，灯光昏暗。镜头摇移跟随，随后升起拉远。
镜头4：@陆玄停下脚步，抬头望向巷口，神色凝重。镜头推近特写。
```

**用户拿到后怎么用**：把每段提示词粘进对应镜头的「提示词」框；同场景的镜头（如镜头1、2）在「所属场景」下拉选同一个场景，镜头3、4 选另一个场景。

---

## 7. 如果你是"读取/修改工作流的 AI"：解析顺序

拿到一份工作流后，按这个顺序理解。**如果你需要把内容提取给用户（比如"每个场景讲了什么"），输出仍用第 6 节的纯文本提示词列表格式，不要扔 JSON 给用户。**

1. **定位 `timeline.scenes[]`**：读出所有场景的 `id` 和 `order`。按 `order` 排序得到场景播放顺序。
2. **遍历 `timeline.segments[]`**：每个元素看 `sceneId` → 落到对应场景。**收集同场景的所有镜头**（保持它们在数组里的顺序）。
3. **提示词在 `segments[i].prompt`**：要改提示词就改这里；要查"某场景讲了什么"就过滤出该 `sceneId` 的镜头读它们的 `prompt`。
4. **资产归属**：场景的素材组在 `scenes[x].assets`，全局资产在 `timeline.assets`。段没显式 `castId/locationId` 时，按 `@名字` 匹配（场景素材组优先，其次全局）。
5. **导出语义**：`timeline.output.exportMode` 决定导出档位——`scene`=每场景一个 mp4、`movie`=ffmpeg 直拼整片、`segments`=每镜一个、`all`=内存合并。**未归属场景的镜头**（`sceneId` 为空）自动按 location 连续性兜底分组。
6. **改动铁律**：改字段名、加字段，必须保持 camelCase（前端）与 snake_case 兼容（后端两种都读）；**只改 segments 数组里已有的键**，别新增后端不认识的顶层块。

---

## 8. 常见坑（AI 最容易放错的地方）

| 错误 | 正确 |
|---|---|
| 把分镜描述写进 `scenes[]` 场景对象 | 提示词永远在 `segments[].prompt`；场景对象只有元信息 + 素材组 |
| 忘了填 `segments[].sceneId`，或填成场景 `name` | 填场景对象的 **`id`**（如 `sc-sq`），不是名字 |
| 同场景的镜头 `sceneId` 填得五花八门 | 同场景所有镜头填**同一个** `sceneId` |
| 场景顺序靠数组位置 | 靠 `scenes[].order`（导出/面板排序只看 order） |
| 提示词里写资产名但素材组里名字不一致（如提示词"陆玄"、素材 name="LuXuan"） | 两边名字必须完全一致，否则自动匹配失效 |
| 忘了写运镜、一镜到底固定机位 | 每镜 prompt 末尾必须有运镜设计 |
| 场景换了但没换 `sceneId` | 地点/时间变化 → 换到对应场景的 `sceneId` |
| `assets.cast` 与全局 `assets.cast` 里 asset id 冲突 | id 保持全局唯一（前端 `sca` 前缀，场景资产与全局资产都可独立）；后端按名字 + id 双重解析，冲突时场景优先 |
| 删了场景还想让镜头保留归属 | 删除场景后所有 `sceneId` 被置空（按设计），需重新归属 |

---

## 9. 相关文件

| 文件 | 用途 |
|---|---|
| `director/plan.py` | 后端数据模型（`SceneGroup`/`SegmentPlan`/`GlobalAsset`） |
| `director/gen_timeline.py` | timeline JSON → plan 解析（`_load_scene_groups`/`build_scene_groups`/资产匹配） |
| `director/executor_core.py` | 执行：资产注入/续接/状态跟踪/导出 |
| `web/js/minimax_scene_manager.js` | Scene Manager 面板（场景 CRUD/素材组/每场景导出） |
| `web/js/minimax_export_center.js` | 素材库总览 + 导出中心 |
| `web/js/minimax_image_batch.js` | r2v 批 UI（段卡片/「所属场景」下拉/资产面板） |
| `web/js/minimax_gen_timeline.js` | 前端 timeline 数据模型（`newBatchSegment`） |

> 本文件是**面向 AI 的分镜数据规范**；字段名与实际代码逐字一致。改代码导致字段变化时，请同步更新本文。
