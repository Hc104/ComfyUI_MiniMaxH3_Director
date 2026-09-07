# P0-① 真实转换链路演示：《山雨客栈》镜头 1

> 日期：2026-08-13
> 数据真实性：全部来自用户本机真实运行 —— 用户 Windows 机器 Ollama qwen3:14b 跑
> `tools/demo_p0_h3_chain.py`（2026-08-13 00:21 落盘），非 mock、非编造。
> 与 project.json 落盘的 content.visual 逐字比对一致 → 链路可复现。
>
> 本演示回答一个验收问题：**「用户意图有没有被正确传递到 H3」**。
> 每步标注信息来源：🟢 USER=用户原文逐字保留 · 🔵 RULE=规则层确定性生成 ·
> 🟣 AI=Qwen 补全/推断 · 🟡 MIX=规则定结构 + AI 补语义

---

## 0. 一句话结论

**当前链路丢意图**：用户的导演细节（雨刚停、山雾从林间漫向、二层木楼檐角挂灯、湿石阶映暖光、老槐树滴水）在 Qwen→visual_intent 一句话压缩时被大面积精简，进 H3 的只有 Qwen 改写后的 40 个字。**新设计（DirectorIntent + H3 Prompt Builder）用 `user_original_intent` 把用户原文逐字保住，AI 只做「结构化补全」，不再改写**。

---

## 1. 主演示：镜头 1 完整六步链路

### Step 1 · 用户原文（保存层，逐字保留）

系统唯一保存层 `shot.source_text`：

> **镜头 1**：雨刚停，山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄灯笼，湿漉漉的石阶映着暖光，门前老槐树滴着水珠。

🟢 用户导演细节清单（本镜共 8 处）：雨刚停 / 山雾 / 从林间漫向 / 山间客栈山雨楼 / 二层木楼檐角 / 挂着昏黄灯笼 / 湿漉漉的石阶映着暖光 / 门前老槐树滴着水珠。

### Step 2 · Qwen 结构化结果（真实输出）

Ollama qwen3:14b 对镜头 1 的语义补全（`script_analyzer` 输出，验证稳定）：

| 字段 | 值 | 来源 |
|---|---|---|
| characters | `[]`（无角色） | 🟣 AI |
| props | `[昏黄灯笼, 旧剑匣]` | 🟡 灯笼=原文事实；**旧剑匣=AI 从镜头 2 上下文越界带回**（⚠ 见 §5-B） |
| entities | 山雨楼(architecture,0.95) / 旧剑匣(prop,0.9) / 老槐树(creature,0.85) | 🟡 mix |
| visual_elements | 山雾 / 昏黄灯笼 / 湿漉漉的石阶 / 雨后水珠 | 🟡 全是原文事实，AI 分类 |
| actions | 山雾漫向客栈 / 檐角挂着灯笼 / 石阶映暖光 / 老槐树滴水 | 🟡 原文事实，AI 动词化 |
| emotion | 静谧朦胧的雨后黄昏 | 🟣 AI |
| **visual_intent** | **全景俯拍山雾笼罩的山雨楼，暖黄灯笼与湿石阶形成冷暖对比，老槐树水珠特写增强雨后质感** | 🟣 AI 一句话压缩 |

⚠ visual_intent 对照原文：**保留**了 山雾/山雨楼/灯笼/石阶/老槐树/水珠；**丢失**了 雨刚停、从林间漫向、二层木楼檐角、「湿漉漉」、门前、滴着水珠的动态感；**新增**了 Qwen 的构图建议（全景俯拍、冷暖对比、特写增强）。

### Step 3 · DirectorIntent（🚧 新设计）

`director/director_intent.py`（P0-① 待实现）——统一中间数据结构，**9 个意图字段 + 来源标注**：

```python
@dataclass
class DirectorIntent:
    shot_id: str
    # —— 用户意图（最高优先级，任何阶段不得被覆盖）——
    user_original_intent: str          # 🟢 = source_text 逐字原文
    retained_facts: list[str]          # 🟢 从原文抽取的导演事实（雨刚停/从林间漫向/…）
    # —— 结构化导演信息 ——
    subject: str                       # 🟡 本镜主体（山雨楼=建筑）
    characters: list[CharacterRef]     # 🟡 出场角色 + role（本镜空）
    location: str                      # 🔵 场景 location_name（山雨楼外）
    action: list[str]                  # 🟡 动作序列（原文事实，保序）
    emotion: str                       # 🟣 AI 情绪基调（静谧朦胧的雨后黄昏）
    composition: str                   # 🟣 AI 构图建议（全景俯拍/冷暖对比/水珠特写）
    camera_position: str               # 🔵 景别（establish→全景）
    movement: str                      # 🔵 运镜（缓摇）
    lighting: str                      # 🟡 光线（原文昏黄灯笼/暖光 + AI 冷暖对比）
    environment: list[str]             # 🟡 环境/氛围（visual_elements）
    continuity: dict                    # 🔵 首镜（无前镜状态）
    audio: dict                        # 🔵 规则（雨→雨声；无对白；舒缓音乐）
    entities: list[EntityRef]          # 🟡 剧本事实（供资产匹配，含 source 标注）
    # —— 来源标注（每个字段可追溯）——
    provenance: dict[str, str]         # 字段名 → USER/RULE/AI/MIX
```

**设计要点**：
1. `user_original_intent` = `source_text` 逐字，是最终 H3 组装时的**原文保底**：AI 压缩丢失的细节从这里拿回。
2. `retained_facts` 用规则从原文抽导演事实（雨刚停→雨后水珠；从林间漫向→空间关系；二层木楼檐角→建筑细节），**不依赖 AI 老实**。
3. Qwen 只填 `emotion/composition` 这类「原文没有、但导演需要」的推断字段；**原文有的字段 Qwen 只分类不改写**。

### Step 4 · Prompt Sections（真实五区草稿，prompt_builder_v17 输出）

镜头 1 当前真实草稿（Qwen 补全后）：

```
visual:   全景俯拍山雾笼罩的山雨楼，暖黄灯笼与湿石阶形成冷暖对比，老槐树水珠特写增强雨后质感   ← 🟣 visual_intent 原样透传
camera:   全景，缓摇交代山雨楼外环境，黄昏、雨氛围。                                            ← 🔵 establish 模板
style:    电影感，冷色调，湿地面反射，雨丝可见，画面干净通透，人物边缘清晰。                    ← 🔵 规则（weather=雨→冷色调）
sound:    雨声淅沥，舒缓氛围配乐。                                                              ← 🔵 规则（雨→雨声；无强情绪→舒缓）
negative: 画面模糊，肢体扭曲，多余肢体…                                                        ← 🔵 固定负面词
camera_intent: establish · generation_mode: t2v · camera_template: establish
```

**问题标注（真实证据）**：`visual` = Qwen visual_intent 原样透传（v1.json 模板就是 `{visual_intent}`）。
→ 用户原文 8 处细节只剩 6 个词根，**雨刚停/从林间漫向/二层木楼檐角/湿漉漉/门前 全部丢失**。

### Step 5 · 最终 H3 Prompt（🚧 h3_prompt_builder.py 设计输出）

`director/h3_prompt_builder.py`（P0-① 待实现）。**项目自己的 H3 Prompt Schema**（非官方，见 §6-C）：

```
【integrated_multimodal_description】
[0-5s] 山雨楼外，黄昏雨后。全景俯拍：山雾从林间漫向山间客栈「山雨楼」，二层木楼檐角挂着昏黄
灯笼，湿漉漉的石阶映着暖光，门前老槐树滴着水珠。光线：黄昏暖黄灯火与雨后冷色天光对比，
湿地面反射。镜头缓摇交代环境，画面安静，雨后空气通透微凉。
   🟢 保留原文：雨刚停(→雨后)/山雾从林间漫向/二层木楼檐角挂灯笼/湿漉漉石阶映暖光/门前老槐树滴水珠
   🟣 AI 补全：全景俯拍构图、冷暖对比、空气通透微凉
   🔵 规则：全景缓摇(establish)、[0-5s]时间轴(duration=5s)、黄昏雨(场景字段)

【overall_soundscape】
雨声淅沥（雨刚停的余韵），老槐树叶尖水珠滴落石阶的嗒嗒声，远处山雾间隐约的风声。
   🟢 保留：雨刚停→雨后滴水音效（从原文事实派生）
   🔵 规则：weather=雨→雨声环境
   🟣 AI 补全：叶尖水滴声、山雾风声（画面细节的音效想象）

【non_diegetic_music】
舒缓淡雅的古风氛围配乐，低音量，旋律悠远绵长，营造静谧朦胧的雨后黄昏。
   🔵 规则：emotion 无强情绪词 → music_calm（舒缓）
   🟣 AI 补全：古风配器、低音量、悠远绵长
```

**与当前实际进 H3 的文本对比**（当前 = 前端四区中文拼接，见 Step 4）：

| 维度 | 当前链路 | 新链路 |
|---|---|---|
| 用户原文细节 | 8 处 → 剩 3 个词根 | 8 处全保留（逐字） |
| 时间轴 | 无 | [0-5s] |
| 三段结构 | 无（中文拼接直进） | integrated_multimodal_description / overall_soundscape / non_diegetic_music |
| 对白保留 | 中文原文 | 原文 + 说话者标注 |
| 参考图标签 | 无 | 有（资产引用，见 §6-D） |
| 意图可追溯 | 丢失不可查 | 每字段来源标注 |

### Step 6 · 意图传递审计表（验收依据）

| 用户原文细节 | 当前链路是否传到 H3 | 新链路是否传到 H3 | 来源 |
|---|---|---|---|
| 雨刚停 | ❌ 压缩成"雨后质感" | ✅ 雨刚停→雨后+水滴音效 | 🟢 USER→RULE 派生 |
| 山雾从林间漫向 | ✅ "山雾笼罩" | ✅ 逐字 | 🟢 USER |
| 二层木楼檐角 | ❌ 丢 | ✅ 逐字 | 🟢 USER |
| 挂着昏黄灯笼 | ✅ "暖黄灯笼" | ✅ 逐字 | 🟢 USER |
| 湿漉漉的石阶映着暖光 | ⚠️ "湿石阶"(丢暖光) | ✅ 逐字 | 🟢 USER |
| 门前老槐树滴着水珠 | ✅ "老槐树水珠" | ✅ 逐字 | 🟢 USER |
| （AI 构图：全景俯拍/冷暖对比/特写） | ✅ | ✅ | 🟣 AI |
| （规则：全景缓摇/雨声/舒缓乐） | ✅ | ✅ | 🔵 RULE |

---

## 2. 补充镜头 · 4（对白保留）

```
原文：镜头 4：柳如烟放下茶碗，从柜台后绕过，端着一碗热茶走向堂中，语气平淡：「客官，落座歇脚，茶先暖着。」
Qwen: dialogue=[{speaker:柳如烟, text:"客官，落座歇脚，茶先暖着。"}]  emotion=平淡
      visual_intent=低角度镜头，柳如烟手持茶碗从柜台后走出，暖光在瓷碗上形成反光
规则: camera=dialogue（对白→正反打）· sound="雨声淅沥，对白清晰，舒缓氛围配乐。"
```

传递审计：台词「客官，落座歇脚，茶先暖着」当前链路 ✅ 保留原文（进 content 由前端拼接）；
speaker=柳如烟 🟣 AI 回填（正确）；「语气平淡」→emotion=平淡 🟣 AI（原文事实）。
新链路下对白进入 overall_soundscape 且**原文逐字**，speaker 标注说话者，正反打运镜 🔵 规则保持。

---

## 3. 补充镜头 · 7（多人关系）

```
原文：镜头 7：蒙面人一步步走近，沉声：「剑，交出来。」沈青崖起身，横剑护在柳如烟身前；柳如烟退后半步，手已按住柜台下的短刃。三人成三角对峙。
Qwen: characters=沈青崖/柳如烟/蒙面人（三人）dialogue=[{speaker:蒙面人, text:"剑，交出来。"}]
      actions=[横剑护前, 退后半步, 按住短刃]  emotion=紧张
      visual_intent=三角构图，沈青崖剑尖指向蒙面人，柳如烟短刃压在柜台下
规则: camera=dialogue→正反打 · sound="雨声淅沥，对白清晰，紧张氛围配乐渐起。"（emotion 命中"紧张"→music_tense）
```

传递审计：三人位置关系（身前/柜台下/对峙）✅ 保留；台词原文 ✅；「紧张」→紧张配乐 🔵 规则判定。
**新链路 DirectorIntent.continuity 预留**：本镜结束状态（三人对峙、沈青崖剑尖指向蒙面人）→ 下一镜（镜头 8 打斗）起手，P0-③ 补齐。

---

## 4. 真实意图丢失点清单（审计证据，非假设）

| # | 现象 | 证据 | 根因 |
|---|---|---|---|
| A | 用户导演细节被 AI 一句话压缩 | 镜头 1：8 处细节 → visual_intent 40 字，丢 5 处（雨刚停/从林间漫向/二层木楼檐角/湿漉漉/门前） | 下游只消费 visual_intent，不消费 source_text |
| B | AI 跨镜上下文越界 | 镜头 1 props 出现「旧剑匣」（镜头 2 的道具） | visual_elements 只进 Prompt 不进匹配，但 props/entities 会进资产匹配 → 可能误匹配资产 |
| C | 规则 fallback 无人物时质量差 | 镜头 1 无 Qwen 时 visual="人物，手持昏黄灯笼，在山雨楼外黄昏、雨。"（无人物却有"人物"） | 模板假设有主体；无角色纯环境镜 fallback 不适用 |
| D | 无时间轴/无三段结构/无参考图标签 | 当前进 H3 = 中文拼接四区文本 | Phase 4 从未实现 |

---

## 5. 设计决策（待用户拍板）

- **A. 保留策略**：`user_original_intent` 在最终 H3 组装时**逐字并入 integrated_multimodal_description**（经轻微结构整理，如去「镜头 1」前缀），还是只作为「细节保底」按规则抽取 retained_facts 填入？→ 建议：**双轨**——原文细节已由规则抽取为主，AI 压缩的视觉描述作为构图补充，两者合并去重。
- **B. AI 越界实体**（§4-B）：Qwen 补的 props/entities 若不在本镜 source_text 中出现，资产匹配前**降权或丢弃**（规则原文交集校验）？→ 建议：**丢弃**（只保留本镜原文事实），避免跨镜污染资产匹配。
- **C. H3 Prompt Schema**：确认建立**项目自己的** `minimax-h3-project-v1` Schema（三段结构灵感来自 h3-prompt-writing skill 通用框架，但字段/组装/参考图标签为本项目实现，文档明确标注非官方）。
- **D. 参考图标签**：H3 Prompt 中角色/地点/道具引用资产图时用 `[REF: 沈青崖]` 标签（对应 ref_image_0/1/2 顺序），由 h3_prompt_builder 从 DirectorIntent.entities + SPA 资产绑定生成。

---

## 6. 下一步（P0-① 实现范围，待验收后开工）

1. `production_plan.py` 加 `DirectorIntent` dataclass（§Step 3 字段）
2. `script_analyzer.py` Qwen 输出 → DirectorIntent 填充（原文字段只分类不改写；越界实体丢弃）
3. `prompt_builder_v17.py` 改输出 DirectorIntent / Prompt Sections（五区草稿定位保留，供 AI Draft 审核）
4. 新增 `director/h3_prompt_builder.py`：DirectorIntent → 最终 H3 Prompt（项目 Schema，三段 + 时间轴 + 参考标签 + 对白原文）
5. 生成链路（executor 前）接入 h3_prompt_builder；AI Draft 预览保留五区中文
6. UI 三层可追溯（原始剧本 / AI 结构化理解 / 最终 H3 Prompt）
7. 测试 + 双目录同步 + CHANGELOG + 汇报