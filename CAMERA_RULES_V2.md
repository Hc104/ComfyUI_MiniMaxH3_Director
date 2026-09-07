# 《AI 漫剧导演规则 v2.0》设计文档

> 2026-08-17 · 面向 MiniMax H3 Director 的「画面内容层」导演规则
> 状态：**用户评审 v1 版后拍板方向**（①②③ 三模块确认继续 + ⑧ Visual Style 层新增 + 反应镜硬规则升级）
> 上游：#601-605 v1.0 运镜规则层（已上线）→ 本版解决「画面到底长什么样」
> 资料输入：抖音 AI 漫剧单集提示词模板（模板 A/B + 景别参考 + 高频踩坑）+ 用户 2026-08-17 深度评审

---

## 0. 为什么 v1.0 还不够

v1.0（CAMERA_RULES_V1.md）核心心法是「运镜是情绪放大器」，解决的命题是 **摄影机怎么看**：
景别/机位/运动/构图/180° 轴线/镜头功能（establish/info/reaction/emotion/ending）。

但用户实测「画面效果还是不是我想要的」——问题不在摄影机，而在**画面里到底发生什么**。
对照你收集的资料，三个致命缺口浮出来：

1. **一个镜头塞了太多动作**。现在 Shot.source_text 可能包含「转身 + 表情变化 + 看人 + 对手反应 + 双人关系」，H3 一次生成塞五件事 → 生成器各顾一头、画面杂乱。资料铁律：**一个镜头 = 一个主要视觉事件**。
2. **写的是动作结果，不是动作过程**。「顾琰宸愤怒，双手握拳」→ 模型只给一个人站着；「垂在身侧的手缓缓收紧，指节逐渐绷紧」→ 这才是视频。
3. **心理描写直接进了 Prompt**。小说「她心里一阵慌乱」H3 不知道拍什么；要转换成「呼吸变急，眼神短暂躲闪，手指不自觉攥紧裙摆」。

v1.0 已经覆盖的（不用重做）：镜头功能、180° 轴线/左右位置、情绪爆点才推镜、中性台词静止、反应>说话、跨镜延续、人物稳定（Global Bible + Asset Registry + Stable Entity Key）、场景稳定、无字幕约束。

**v2.0 新增的核心 = 三层画面内容规则 + 一层节奏规划器**，全部纯规则、零 LLM、零显存（与 v1.0 同铁律），落在 DirectorIntent 与 H3 Prompt Builder 之间。

---

## 1. 资料吸收映射表

你贴的资料真正值得吸收的，不是把它当 Prompt 模板，而是它点出的「控制维度」。逐条映射到本系统的落点：

| 资料观点 | v2.0 落点 | 现状 |
|---|---|---|
| 每个镜头只做 1 个核心动作 | 新增 `core_action.py`（①） | 无——source_text 多动作直接进 Prompt |
| 写动作过程不写静态姿势 | core_action 措辞模板 + `psych_visualize`（①②） | 部分——camera 措辞已强动态，动作本体没管 |
| 心理描写→眼神/皱眉/攥手 | 新增 `psych_visualize.py`（②） | 弱——script_analyzer 有软约束，无硬规则 |
| 情绪浓度越高镜头越近（且峰值可定格） | 新增 `beat_rhythm.py` 张力档位（③） | 部分——五镜公式是局部，无整条曲线 |
| 结尾留悬念钩子 | `beat_rhythm` 尾镜张力决策（③） | 有 ending 收尾，无「钩子」语义 |
| 固定人设每镜都带 | Bible attributes 注入 H3（⑥，决策点 D1） | 有 Bible/Asset，未注入 H3 画面描述 |
| 反应>说话（拍听众） | 已有 v1.0 reaction；**v2 升级为硬规则（§3.4）** | 软选择 → 硬触发 |
| 画风+人设+动作+场景+景别+运镜+画质+防崩坏 | 11 层渲染升级 + 防崩坏双语约束（⑨） | 散文式渲染，防崩坏只有字幕约束 |
| **抖音漫剧大量写实风（用户评审）** | **新增 `VisualStyleProfile` 全剧级视觉基底（⑧，§3.5）** | 无——视觉基底每镜随机决定 |

---

## 2. Director Layer：九大模块 + 四层总架构

### 2.1 四层总架构（用户评审后定稿）

用户 2026-08-17 评审的核心认识：**缺的不是更多运镜提示词，而是「漫剧镜头语言」**。
Camera Rules 只是其中一层——「怎么拍」。系统要接近「自动导演」，必须四层分开：

| 层 | 命题 | 载体 | 状态 |
|---|---|---|---|
| **Story Bible** | 谁在什么地方 | bible.py + Asset Registry + Location | ✅ 已有 |
| **Beat Rhythm** | 为什么这个时候这么拍（情绪曲线/节奏/反应） | beat_rhythm.py + plan_reaction_shots（v2 新增） | 🆕 |
| **Camera Rules** | 怎么拍（景别/机位/运动/构图/轴线） | spatial_continuity + director_rules + camera_template（v1 已有） | ✅ 已有 |
| **Visual Style** | 拍出来是什么视觉产品（画风/写实度/光影/材质） | VisualStyleProfile（v2 新增） | 🆕 |

> **「画面不对」的隐藏根因之一**：视觉基底每镜随机（动漫/国漫/写实/电影感）。
> 景别/机位/运镜/动作/张力/构图都对了，但视觉基底随机 → 镜头语言正确，成品却不像漫剧。
> 抖音成熟漫剧「大量写实风」不是巧合——**视觉基底是全剧级固定条件，不是每镜临时决定**。

### 2.2 Director Layer 九大模块（用户版）

```
① 核心动作       → core_action.py（一镜一核心动作，本版核心）
② 心理视觉化     → psych_visualize.py（心理→动作/表情，本版核心）
③ 张力节奏       → beat_rhythm.py（情绪曲线 → tension → 推镜/定格/留白，本版核心）
④ 空间连续       → spatial_continuity.py 已有（180° 轴线/左右位置/视线轴）
⑤ 构图           → spatial 补 composition_note 渲染句
⑥ 人物稳定       → Global Bible + Asset Registry 已有；v2 注入 H3（决策 D1）
⑦ 场景稳定       → location 资产 + must_keep 已有
⑧ Visual Style   → VisualStyleProfile 全剧级视觉基底（**本版新增，核心**）
⑨ Camera / H3 运镜 → v1.0 camera_desc 按 tension 选措辞
```

> 镜头功能（shot_role：establish/info/reaction/emotion/ending/action）作为底层机制，
> 内嵌于各模块（③张力节奏选镜、④空间连续对切、⑨措辞），不再单列。

优先级排序：**①②③ 全新纯规则模块 + ⑧ Visual Style 层（必须做）→ ⑨ 渲染升级（必做）→ ④⑤⑥⑦ 强化渲染（可选）**。

---

## 3. 新模块设计（三个纯规则模块）

### 3.1 `core_action.py` —— 单镜头单核心动作（②）

**命题**：一个镜头 = 一个主要视觉事件。即使原文有多个动作，本镜只渲染核心那个，其余降级为辅助提示。

**输入**：`shot.actions`（原文动作序列）+ `shot.visual_intent` + `shot.source_text` + 镜头功能 `shot_role`。
**输出**：`core_action: str`（唯一核心视觉事件，含动作过程措辞）+ `core_subject: str`（谁在动）。

**规则（纯函数 `extract_core_action`）**：

1. **动词优先级排序**（强动作 > 表情动作 > 姿态 > 环境）：
   - 强动作：拿起/甩落/推门/拔剑/追上/跪下/转身（产生位移或物体交互）→ 核心事件候选
   - 表情动作：抬眼/垂眸/抿唇/皱眉/攥紧（面部/手部微动作）→ 次选（无强动作时作核心）
   - 姿态：站着/坐着/沉默（静态）→ 不选为核心（交给场景/镜头承载）
2. **折叠连续动作**：同主语、语义连续的多个动词合为一个核心事件。
   例：「捏起支票 → 抬到眼前 → 看向顾琰宸」→「漫不经心捏起桌上的支票，缓缓抬到眼前」。
3. **镜头功能裁决**：双人镜各有动作时，核心动作取**本镜镜头功能的主角**——
   - `reaction`（拍听众）→ 取听者的反应动作
   - `emotion`（拍情绪者）→ 取情绪者的动作
   - `info` → 取说话者/主动作
4. **动作过程措辞**：核心动作渲染为「动作过程」而非「动作结果」。
   静态动词（握紧/站着）→ 改写为过程（垂在身侧的手缓缓收紧，指节逐渐绷紧）。
   （措辞表复用 §4 心理可视化的动作库，同一套）

**落点**：`DirectorIntent.core_action`（新派生字段）。H3 渲染为 `核心动作：{core_action}`，紧跟原文事实之后、AI 补全之前。

**失败兜底**：无法从动作序列选出核心（纯环境镜）→ `core_action = ""`，渲染层跳过该句（严格向后兼容）。

---

### 3.2 `psych_visualize.py` —— 小说心理描写视觉化（③）

**命题**：小说心理描写（她心里一阵慌乱）不能直接进 H3；先转换成「动作/表情的可视变化」。

**输入**：`shot.source_text` + `shot.emotion`。
**输出**：`expression: str`（表情变化句）+ 可插入动作序列前部的动作短语。

**规则（纯函数 `visualize_psych`）**：

1. **心理词探测**：source_text/emotion 命中心理词表 → 激活可视化。
2. **动作/表情映射库**（可扩展 dict，关键词 → 视觉化短语）：

| 心理 | 表情变化（expression） | 动作过程（可并入 core_action） |
|---|---|---|
| 慌乱/心慌/紧张 | 眼神短暂躲闪，睫毛轻颤 | 呼吸变急，手指不自觉攥紧衣摆 |
| 震惊/惊愕/愣住 | 瞳孔微缩，表情短暂凝滞 | 动作一顿，身体微微僵住 |
| 得意/从容/嘲讽 | 嘴角浮现一抹嘲讽的弧度 | 目光直视对方，缓缓抬眸 |
| 愤怒/压抑怒火 | 下颌绷紧，眼底翻涌 | 垂在身侧的手缓缓收紧，指节逐渐绷紧 |
| 悲伤/落寞 | 目光低垂，眼底微红 | 指尖在杯沿无意识摩挲 |
| 害怕/恐惧 | 瞳孔微缩，嘴唇抿紧 | 脚步下意识后退半步，肩线微微绷紧 |

3. **⛔ 铁律：绝不改写原文**。`retained_facts` 逐字保底（v1 铁律不变）；心理可视化是**新增的规则/AI 补全层**，追加在原文事实之后、进 `ai_supplement`/`rule_generated` provenance，与「AI 只补充不覆盖」一致。
4. **无心理词 → 输出空**，渲染层跳过（向后兼容）。

**落点**：`DirectorIntent.expression`（新派生字段）。H3 渲染为 `表情：{expression}`，紧跟核心动作之后。

---

### 3.3 `beat_rhythm.py` —— 情绪曲线 + 镜头节奏（④⑤）

**命题**：先理解整条情绪曲线（压迫→反击→反转→震惊→再压迫），再决定哪镜特写/静止/留白/推镜。这是比「景别跟情绪走」更高一层的**镜头节奏导演**。

**输入**：timeline 播放顺序（`resolve_timeline_order`）的每镜 `(dramatic_function, emotion)` + 镜头功能。
**输出**：每镜 `tension: int`（0-3 张力档位）+ `rhythm: str`（build/peak/release/hold）。

**规则（纯函数 `plan_rhythm(plan)`，一次遍历全链）**：

1. **基础张力**：emotion 关键词 → 0-3（表见下）。
2. **曲线模式识别**（dramatic_function 序列）：
   - `introduce_threat`/`confrontation` 强 → 上升（build）
   - `reveal`/`emotional` 且上镜在 build → 峰值（peak）
   - `reaction`/`dialogue` 且上镜在 peak → 释放（release）
   - 连续同张力 → 维持（hold）
   - 全镜无情绪、无威胁 → 0 档（establish/info/transition）
3. **张力档位 → 镜头决策**（v2.0 修正 v1.0 的「情绪→特写+推镜」一刀切）：

| tension | 语义 | 景别 | 运动 | 典型镜头 |
|---|---|---|---|---|
| 0 | 建立/过渡/收尾 | 全景 | 静止或缓摇 | establish/ending |
| 1 | 信息/日常 | 中景 | **静止** | info 说事 |
| 2 | 情绪上升 | 近景 | 缓推（push-in） | 反击建立、张力攀升 |
| 3 | 峰值/爆发 | 特写 | **定格（静止）** | 「就这？」反转、顾琰宸愣住 |

> ⛔ **关键修正（用户「第四镜甚至不要动镜头」的精确落地）**：不是「张力越高越推」。
> **上升段（tension 2→3）才推镜；峰值本身（tension 3）定格**——静止特写在爆发点比推镜更有力量。
> 释放段（peak 后）：`pull-out`/拉远留白（钩子感）。

4. **尾镜钩子**：场景/章尾镜如果下一镜还有戏 → 渲染层追加「镜头在情绪峰值处定格，画面余韵留白」（钩子语义，替代 v1.0 纯 ending 拉远）。

**落点**：`DirectorIntent.tension` + `continuity.rhythm`（新派生字段）。`decide_camera` 运动决策改为接收 tension：2→push-in、3→static close-up、0→pull-out。

---

### 3.4 反应镜头硬规则（用户评审升级）

**命题**：传统 AI 生成容易「A 说 → A 继续说 → A 继续说」；真正的漫剧是「A 说 → B 的反应 → A 再说 → B 更大的反应」。
尤其喜剧题材（《吐槽成真》），**反应镜头甚至比台词本身更重要**。用户明确建议：把反应镜头**提升为硬规则**，不再只是 shot_role 的一种软选择。

**规则（纯函数 `plan_reaction_shots`，beat_rhythm 后、core_action 前执行）**：

1. **连续对白计数**：按 timeline 顺序遍历，统计连续「有对白镜」个数 `dlg_streak`。
2. **硬触发 A**：`dlg_streak >= 2` 且当前镜有对白 → **下一镜强制 reaction**（拍上一镜的听众，
   不给听者安排台词，只给表情/动作变化）。
3. **硬触发 B**：当前镜是台词爆点（对白含 `！？`/质问词/爆发词）→ **紧邻下一镜强制 reaction**。
4. **反应镜内容**：不让他说话。用 `psych_visualize` 表情库给听者一个可看的反应——
   「瞳孔微缩，表情第一次出现明显错愕」「嘴角不明显地动了一下」——这一个镜头就产生喜剧/戏剧效果。
5. **优先级**：硬规则触发时 reaction 覆盖 beat_rhythm 的张力分配（tension 档位仍给，
   但景别锁定特写、运动锁定静止——定格的力量）。

**《吐槽成真》示范（用户原话落地）**：

```
林薇薇：“就这？”              ← 台词爆点（硬触发 B）
→ 切顾琰宸。                    ← 强制 reaction，不让马上说话
   顾琰宸瞳孔微缩，表情第一次出现明显错愕。← 不配台词，纯表情变化
   这一个镜头就产生喜剧效果。
```

**落点**：`build_plan_intents` 内 beat_rhythm 后调用，输出 `continuity.reaction_forced: bool`；
`assign_shot_role` 的 reaction 分支读取该标记（由 beat_rhythm 直接改写 role 亦可）。

---

### 3.5 Visual Style / Art Direction 层（用户评审新增 · 本版另一核心）

**命题**：Camera Rules 回答「怎么拍」，Visual Style 回答「拍出来是什么视觉产品」。
抖音成熟 AI 漫剧「大量写实风」不是随机风格——**视觉基底是全剧级固定条件，不是每镜临时决定**。
现在 H3 每镜的风格段若无全剧级约束，就会每镜随机发挥 → 镜头语言正确但成品不像漫剧。

**输入**：全剧级 `VisualStyleProfile`（持久化资产，与 Bible 同级）。
**输出**：每镜 H3 描述区**固定注入**的画风块 + 全剧级负面约束。

#### VisualStyleProfile 数据模型（新持久化对象，全剧级）

```python
class VisualStyleProfile:
    profile_id: str                  # style-001
    name: str                        # 「现代国漫写实风」
    art_direction: str               # 一句话画风总纲（进 H3「画风：」行）
    style_keywords: Dict[str, str]   # 分组关键词：人物/光影/材质/整体
    fidelity: str                    # full_realistic / semi_realistic / stylized（写实度档位）
    color_tone: str                  # cold / warm / dark / daylight（色调基调）
    negative_rules: List[str]        # 全剧级防崩坏负面（合并进 _ANTI_BREAK_RULE）
    per_scene: Dict[str, str]        # 场景级微调（可选：雨夜冷调/回忆暖黄/梦境柔光）
```

#### 「抖音半写实漫剧」中文速览（推荐预设的模板文本）

```
画风：现代国漫写实风
人物：半写实动漫人物，真实人体比例，细腻五官，自然皮肤材质
光影：电影级光影，真实环境反射，柔和轮廓光
材质：真实布料，真实头发，真实皮肤，真实金属
整体：高精度国漫，电影感，写实人物，轻动漫化
```

#### Visual Style Preset 预设库（用户评审新增：选预设，不手写风格）

用户 2026-08-17 补充拍板：**不要让用户每次写「国漫、写实、电影感」**——系统内置预设，
用户选一个，Director 生成 H3 时自动注入对应视觉风格描述，**全剧所有镜头统一**。

预设列表（内置常量，可后续扩充）：

| preset_id | 名称 | 说明 |
|---|---|---|
| `douyin_semi_realistic` | 抖音半写实漫剧（**推荐**） | Semi-Realistic Cinematic Anime，抖音/红果/小程序漫剧最火风格 |
| `japan_cel` | 日漫赛璐璐风 | 平涂、高饱和、赛璐璐上色 |
| `cn_3d` | 国产 3D 动画风 | 类腾讯动漫 3D 渲染 |
| `pixar` | Pixar 风 | 皮克斯材质渲染 |
| `ink_guofeng` | 水墨国风 | 水墨笔触 |
| `korean_thick` | 韩漫厚涂风 | 厚涂上色 |
| `cinematic_real` | 写实电影风 | 接近真人电影 |

#### 「抖音半写实漫剧」推荐预设 · 8 部分拆解（用户原话结构化）

① **人物风格**（半写实动漫）：真人五官 + 动漫皮肤；五官比例接近真人、鼻子有真实体积、
嘴唇厚度真实、睫毛多、眼睛略放大、下巴略尖、脸型略修饰。写实度 ≈ 真人 70% / 二次元 30%。
② **光影风格**（电影级自然光）：柔和自然光、面部漫反射、鼻梁高光、嘴唇湿润反光、头发边缘光、景深虚化。
③ **镜头语言**（电影近景）：85mm 浅景深、背景虚化、主体突出、焦点在人眼。
④ **色彩风格**（低饱和暖调）：奶油色、电影调色；**不用**动漫高饱和/赛博朋克/霓虹/高对比。
⑤ **材质**（真实材质）：头发一根根发丝、皮肤真实肤质非磨皮、衣服真实针织纹理、饰品 PBR 材质。
⑥ **景深**（浅景深 DOF）：前景清楚、后景逐层虚化。
⑦ **构图**（电影构图）：人物占画面 60%、前景焦点、背景虚、侧脸留白、三分法。
⑧ **整体风格**：电影摄影 + 半写实动漫人物 + 国漫渲染——不像原神/崩铁/日漫，也不像真人电影，介于两者之间。

**内置英文风格串**（用户提供，进 `art_direction` + `style_keywords.overall`）：

```
Modern Chinese semi-realistic anime. Ultra detailed female character. Realistic facial
proportions. Delicate skin shading. Natural makeup. Detailed hair strands. Cinematic
photography. Movie lighting. Warm daylight. Shallow depth of field. 85mm lens. Soft bokeh.
Photorealistic materials. Anime rendering. High-end Chinese animation style. Consistent
character appearance. Natural facial expressions. Ultra HD.
```

> 用户判断：预设选择 = 让「所有镜头保持统一风格」的最稳路径，比每镜临时拼提示词稳定得多。
> 这也是「画面不像漫剧」的根治方向：**视觉基底全剧级锁定，用户只做一次选择。**

#### H3 注入方式（固定前缀块，全剧级一致）

描述区固定段（紧随原文事实之前）：

```
画风：{art_direction}。{人物/光影/材质/整体 四组关键词摘要（≤60 字）}。
```

尾部负面约束合并 `_ANTI_BREAK_RULE` + `negative_rules`（全剧级，双语）。

#### 微调与例外（不破坏全剧级固定）

- 场景级微调：`per_scene` 只改光影/色调局部词（如雨夜冷调），**不改画风总纲**；
- 特殊镜例外（回忆/梦境/反转）可整体切换 profile（决策 D6），但须显式声明，**不静默漂移**；
- ⛔ 铁律：**默认每镜画风一致**；未显式切换时，H3 画风块全剧级完全相同。

**落点**：`VisualStyleProfile` 持久化于 Project（与 Bible/Asset 同级，前端可编辑、可预设选择）；
`h3_prompt_builder` 读 profile 渲染画风块 + 合并负面约束。

---

## 4. 数据模型改造点（全部派生、不落 ProductionPlan JSON）

`DirectorIntent` 新增字段（`director_intent.build_shot_intent` 派生，`from_dict` 缺省空值向后兼容）：

```python
core_action: str = ""          # ① 本镜唯一核心视觉事件（含动作过程措辞）
expression: str = ""           # ② 本镜核心表情变化（谁的脸怎么变）
tension: int = 0               # ③ 张力档位 0-3（来自 beat_rhythm）
rhythm: str = ""               # ③ build/peak/release/hold
composition_note: str = ""     # ⑤ 构图说明（画左/画右/前景/视线；spatial 已有，补可读句）
# ⑥（决策点 D1）style_hints: List[str]  # 在场角色外观特征（Bible attributes 注入，token 受限）
# ⑨ negative_hints 由 h3_prompt_builder 常量承载（防崩坏双语约束），不进 intent
# ③（硬规则）continuity.reaction_forced: bool = False  # 反应镜硬规则触发标记（§3.4）
```

`Shot` 不动（原有 actions/emotion/dialogue/visual_intent 已是输入）；`Dialogue` 的 `emotion/delivery` 已供 TTS 层，v2.0 可反哺表情判断（决策点 D4）。

**新增持久化对象 `VisualStyleProfile`**（§3.5）：全剧级视觉基底，存 Project（与 Bible 同级），
含 `art_direction / style_keywords / fidelity / color_tone / negative_rules / per_scene`。
前端可编辑 + 预设选择；`h3_prompt_builder` 渲染画风块 + 合并负面约束。

**接入链路（改后）**：

```
build_plan_intents（按 timeline）
  ├─ beat_rhythm.plan_rhythm(plan)            ← ③ 先算全链张力曲线
  ├─ plan_reaction_shots(plan, roles)         ← ③ 反应镜硬规则（§3.4：dlg_streak≥2 / 台词爆点后强制）
  ├─ core_action.extract_core_action(shot, role)   ← ① 每镜核心动作
  ├─ psych_visualize.visualize_psych(shot)          ← ② 心理→表情/动作
  ├─ decide_camera(..., tension=…)            ← v1.0 相机 + v2.0 张力运动决策
  └─ build_shot_intent(..., core_action, expression, tension, rhythm, reaction_forced)
        ↓
h3_prompt_builder._build_description
  ├─ 画风：{VisualStyleProfile 画风块}          ← ⑧（新增，描述区顶部，全剧级固定）
  ├─ 原文事实逐字（不变）
  ├─ 核心动作：{core_action}                    ← ①（新增，原文事实后）
  ├─ 表情：{expression}                         ← ②（新增）
  ├─ 构图：{composition_note}（双语可选）        ← ⑤（新增）
  ├─ AI 补全 / 镜头 / 光线 / 氛围 / 对白 / 跨镜延续 / 空间连续性（不变）
  └─ 防崩坏双语约束（新增，尾部）                ← ⑨（合并 VisualStyleProfile.negative_rules）
```

---

## 5. H3 Prompt 渲染升级 + 《吐槽成真》4 镜示范

### 5.1 渲染层新增

1. **核心动作句**：`核心动作：{core_action}` —— 一镜只渲染一个核心视觉事件。
2. **表情句**：`表情：{expression}` —— 特写/近景镜头的表情变化（无则跳过）。
3. **构图句**（决策点 D2，双语可选）：`构图：{composition_note}`。
4. **防崩坏双语约束**（新增常量 `_ANTI_BREAK_RULE`，追加描述区尾部，与 NO_SUBTITLE_RULE 同款双语策略）：

```
人物五官、发型、服饰全程保持不变，人体结构正常，无畸形崩坏，画风统一，与参考图一致。
Do NOT change the character's facial features, hairstyle, or costume. Keep proper human
anatomy. No deformed limbs, no distortion. Maintain a consistent art style matching the references.
```

### 5.2 《吐槽成真》四镜对比示范

**剧情**：顾琰宸甩支票施压 → 林薇薇捏支票反击 → 林薇薇「就这？」反转 → 顾琰宸愣住。

**v1.0 现状输出（举例）**——每镜都有电影感措辞，但动作多、无节奏：

```
镜2 info：豪华私人办公室。顾琰宸。林薇薇。…镜头：中近景正反打对切，画面沉稳克制。
镜3 emotion：…镜头：情绪特写快速推近，放大嘲讽的情绪张力。
```

**v2.0 目标输出**：

```
镜1（tension 1 · info · 施压）
  核心动作：顾琰宸将支票从手中甩落到林薇薇面前的桌面，支票落桌后轻微滑动。
  林薇薇没有伸手去拿。
  构图：顾琰宸在画左呈站立压迫姿态，林薇薇在画右。
  镜头：中近景，平视，静止。

镜2（tension 2 · emotion · 反击建立）
  核心动作：林薇薇伸出两根手指，漫不经心地捏起桌上的支票，缓缓抬到眼前。
  表情：嘴角浮现一抹带有嘲讽意味的笑。
  镜头：中近景，缓慢推近。

镜3（tension 3 · emotion · 反转峰值）
  核心动作：林薇薇用支票轻轻敲击桌面，目光直视顾琰宸。
  表情：眼神带着若有若无的嘲讽。
  对白：(S1)林薇薇说，<d>[Chinese] 就这？</d>
  镜头：特写推近面部后定格（峰值定格）。

镜4（tension 3 · reaction · 震惊——拍顾琰宸，不拍林薇薇）
  核心动作：顾琰宸原本冷漠的表情突然凝固，瞳孔微微收缩。
  表情：眉头极轻地皱起，第一次露出明显意外。
  镜头：锁定特写，静止（定格的力量）。
```

对比要点：**镜1 一个动作（甩支票）+ 林薇薇不接的细节**；**镜3 峰值定格**而不是无脑推镜；**镜4 拍听众**（用户原话：真正重要的是顾琰宸懵了）。

---

## 6. 与现有代码的接线点（函数级）

新增纯规则模块（目录 `director/`，与 v1.0 同风格：纯规则、零 LLM、零显存、可单测）：

| 新文件 | 对外函数 | 接入点 |
|---|---|---|
| `director/beat_rhythm.py` | `plan_rhythm(plan) -> Dict[shot_id, (tension, rhythm)]` | `director_intent.build_plan_intents` 开头，timeline 遍历前先算全链曲线 |
| `director/beat_rhythm.py` | `plan_reaction_shots(plan, roles) -> Set[shot_id]`（反应镜硬规则，§3.4） | `plan_rhythm` 后、core_action 前；强制改写 reaction 镜 |
| `director/core_action.py` | `extract_core_action(shot, role) -> (core_action, core_subject)` | `build_plan_intents` 内每镜，`decide_camera` 之后 `build_shot_intent` 之前 |
| `director/psych_visualize.py` | `visualize_psych(shot) -> expression` | 同 `core_action`，紧邻调用 |
| `director/visual_style.py` | `VisualStyleProfile` dataclass + `STYLE_PRESETS` 预设库（7 预设，§3.5）+ `style_block(profile, scene) -> str` | 持久化存 Project；`_build_description` 顶部渲染画风块 |

**`director_intent.build_plan_intents` 改动**（约 +15 行）：
1. 开头调用 `beat_rhythm.plan_rhythm(plan)` 得 `{shot_id: (tension, rhythm)}`；
2. 接 `plan_reaction_shots(plan, roles)` 得强制 reaction 镜集合；
3. 每镜 `decide_camera(..., tension=tension_map[shot_id])` —— 运动决策由 tension 改写；
4. `build_shot_intent(..., core_action=core_action, expression=expression, tension=tension, rhythm=rhythm, reaction_forced=...)`。

**`director.director_rules.decide_camera` 改动**（+1 参数，默认 0 严格向后兼容）：
- `tension=0`：维持现状（establish/info/ending 全静、action 关键词动态）；
- `tension=2`：emotion 镜推镜，info 镜可小幅推近（若情绪曲线已判定上升）；
- `tension=3`：强制改写运动为 static（峰值定格），景别升为 close_up。

**`director.h3_prompt_builder._build_description` 改动**（约 +15 行）：
- 描述区顶部（原文事实之前）插入 `画风：{VisualStyleProfile 画风块}`（全剧级固定，§3.5）；
- 原文事实后插入 `核心动作：{core_action}`（非空才渲染）；
- 其下插入 `表情：{expression}`（非空才渲染）；
- 尾部追加 `_ANTI_BREAK_RULE` 防崩坏双语约束 + `profile.negative_rules`（合并去重）；
- 构图句由 `composition_note`（若已由 spatial 生成可读句）渲染。

**`director.director_intent.build_shot_intent` 改动**：新增 4 个派生字段 + `continuity.reaction_forced`（§4），`from_dict` 加缺省值；`to_dict` 同步导出。

**`DirectorIntent` dataclass**：新增 `core_action/expression/tension/rhythm/composition_note`（默认值空/0）+ `continuity.reaction_forced=False`。

**`VisualStyleProfile` 持久化**：存 Project（与 Bible/Asset 同级），`project_store` 增读写；`h3_prompt_builder` 从 plan 上下文读取注入。

---

## 7. 实施计划（用户拍板后按序执行）

| 阶段 | 内容 | 验收 | 产出 |
|---|---|---|---|
| **P0** | 新增 `beat_rhythm.py`（plan_rhythm + plan_reaction_shots）/ `core_action.py` / `psych_visualize.py` 纯规则模块 + `visual_style.py`（VisualStyleProfile + 预设模板） + 单测 | VM 新单测全绿；旧测回归 0 破坏 | 4 模块 + test 文件 |
| **P1** | `decide_camera` 接 tension；`build_shot_intent` 新字段；`_build_description` 渲染升级（画风块/核心动作/表情/防崩坏） | 旧 Prompt Builder 测试全绿 + 新断言（画风块/核心动作句/防崩坏句/空值跳过） | 生成链路升级 |
| **P2** | `script_analyzer` 升级：`actions` 拆分更细 + 心理词预标记（软约束转硬输入） | 新剧本导入《吐槽成真》4 镜样例，DirectorIntent 内 4 字段正确派生 | 输入质量提升 |
| **P3** | 前端：Visual Style 预设下拉（7 预设，默认「抖音半写实漫剧」）+ 当前 profile 预览；DirectorIntent 查看器新增核心动作/表情/张力档位（决策 D3） | 前端 build + vitest 全绿 | 画风可控 + 可视性 |
| **P4** | 实机拍板：生成《吐槽成真》4 镜 vs v1.0 对比 | 用户对画面满意 | 上线 |

**铁律不变**：纯规则零 LLM；`retained_facts` 逐字保底绝不覆盖；**画风块全剧级固定（§3.5）**；双目录 SRC≡DEP md5 DIFF=0；后端依赖装 `.venv`。

---

## 8. 开放决策（等用户拍板）

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| **D1** | Bible 外观注入 H3 画面描述？ | A. 注入 attributes（发型/服饰/外貌，token 受限）B. 只注入 Asset 参考图文件名 C. 暂不注入 | **B**——先靠参考图锚定，attributes 注入等实机看效果再定 |
| **D2** | 构图句（composition_note）是否双语渲染？ | A. 双语（同空间连续性块）B. 仅中文 C. 不渲染 | **A**——与空间连续性块同策略，H3 执行稳 |
| **D3** | 前端是否展示 4 新字段？ | A. 本次做 B. 等画面满意再做 | **B**——先验证画面再动 UI |
| **D4** | Dialogue.emotion/delivery 是否反哺表情判断？ | A. 是（对白情绪→表情）B. 否 | **A**——零成本，表情库已含情绪词表 |
| **D5** | VisualStyleProfile 入口 | 用户拍板：**内置预设库 + 一键选择**（7 预设，默认「抖音半写实漫剧」），全剧统一注入 H3 | ✅ 已拍板 |
| **D6** | 特殊镜例外是否支持 profile 整体切换？ | A. 支持（回忆/梦境/反转可切）B. v2 不做，全剧级一致 | **B**——保持全剧级铁律，切画风等实机验证后再开 |
| **D7** | 是否支持用户自定义新建预设？ | A. 预设库 + 自定义保存 B. 先用内置 7 个 | **B**——先验证内置预设画面，有需要再加 |

---

## 9. 风险与回滚

| 风险 | 影响 | 对策 |
|---|---|---|
| core_action 折叠动作误伤原文事实 | 关键剧情动作被折叠掉 | retained_facts 逐字保底不动的铁律先行；core_action 只进 AI/规则层 |
| tension 误判（peak 判定错位） | 该推镜的没推 / 该定格的动了 | beat_rhythm 是纯规则可单测；先 4 镜样例锁期望值；`tension=0` 默认即 v1.0 行为，可一键关闭 |
| 防崩坏约束引发 H3 风格波动 | 画面风格漂移 | 双语约束与 NO_SUBTITLE_RULE 同款；P1 阶段实机 4 镜 A/B 对比后再全量上 |
| **画风块全剧级固定但 H3 不执行到位** | **画面仍随机漂移，不像漫剧** | **预设 profile 参考图锚定 + 双语画风块 + P4 实机 A/B 验证；必要时再加风格参考图锚定（D7 观察项）** |
| 渲染层多段文案 Token 变长 | H3 描述超长 | 核心动作/表情句合计 ≤ 40 字；画风块 ≤ 60 字；空值跳过；composition 句 P1 默认关 |
| 反应镜硬规则误触发（对白多但不需要反应） | 节奏拖沓 | dlg_streak≥2 才硬触发；喜剧/冲突题材优先级最高；纯信息长对白可豁免（决策 D7） |
| 新字段破坏旧生成任务 | 老 workflow 反序列化失败 | `from_dict` 全缺省；`to_dict` 旧字段顺序不动；P0 先跑全量回归 |

**回滚策略**：三个新模块全部是「新增派生字段 + 新增渲染句」，不触碰 ProductionPlan JSON 结构、不触碰 retained_facts 原文。任意阶段回滚 = 移除 3 处调用点 + 渲染层 2 行注释，v1.0 行为完整还原。

---

## 附：v2.0 一句话总结

> **Camera Rules（怎么拍）+ Visual Style（拍出来长什么样）+ Story Bible（谁在什么地方）+ Beat Rhythm（为什么这个时候这么拍）——四层合起来，导演台才开始接近「自动导演」，而不只是「自动生成视频」。**
> **v2.0：一镜一核心动作、心理转可视、情绪曲线定节奏（上升推镜/峰值定格）、反应镜硬规则、全剧级视觉基底锁定画风——H3 运镜 Prompt 是这套规则的最终出口，不是起点。**
