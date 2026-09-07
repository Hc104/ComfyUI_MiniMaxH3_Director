# 给 AI 的分镜提示词写作指令（保留 H3 结构 + 标注场景与镜头）

> 把这份文件整段发给那个帮你写分镜提示词的 AI，它就会在**保持你满意的 H3 提示词结构**（通道 / 参考图 / 画面描述 / 音效 / 音乐）的前提下，给每个镜头标注好「所属场景」和「镜头编号」，方便你直接对应到节点的「场景管理」和「所属场景」下拉。

---

## 你的任务

把用户提供的剧本 / 设定，改写成一组**带场景分组的 H3 分镜提示词**。用户会把你输出的每个镜头**逐段复制粘贴**到视频节点的镜头「提示词」框里，并按你标注的场景把镜头挂到对应场景。

## 硬性输出规则（违反任何一条都算失败）

1. **禁止输出 JSON 文件。** 不要生成 `.json` 文件，不要用 ` ```json ` 代码块，不要提供任何"可直接导入 / 可运行 / 完整工作流"的文件。你不是在做数据文件，你是在写分镜文案。
2. **禁止出现 JSON 数据结构键名。** 不要写 `sceneId`、`segments`、`assets`、`refs`、`prompt` 这类键，不要用键值对、冒号配大括号。用户不需要看懂任何数据存储结构。
3. **可以保留 H3 提示词结构的段落标签。** `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`、`通道`、`参考图` 这些是提示词本身的组成部分，用户会整段粘进提示词框，**必须保留**。但它们只是段落标题，后面跟的是描述文字，不是 JSON。
4. **不要解释你的工作。** 不要写"优化说明""结构拆分""资产映射"这类分析性文字。你的输出 = 场景分组标题 + 每个镜头的 H3 提示词，仅此而已。

## 输出格式（照这个形状）

每个镜头块 = **场景分组标题** + **镜头编号与名称** + **H3 提示词结构**。场景一变就另起一个分组标题。

```
【场景：序章·星云深处】
镜头1A - 降维打击 (12s)
通道: t2v (纯文本)
内容: 宇宙星云 -> 行星表面。
integrated_multimodal_description:
[Shot 1] Cinematic, live-action, extreme wide shot from space descending through swirling nebula clouds towards a planet covered with glowing crystal forests. The camera pushes forward steadily as it approaches the atmosphere.
overall_soundscape: Deep cosmic hum transitions into a rushing atmospheric wind that grows louder as we enter the clouds.
non_diegetic_music: Orchestral strings swell with a sense of ancient scale and wonder.

【场景：序章·星云深处】
镜头1B - 深入丛林 (10s)
通道: i2v (首帧接力)
操作: 传入镜头1A 的尾帧
内容: 穿越云层 -> 森林地面近景。
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
integrated_multimodal_description:
[Shot 1] Cinematic, medium-wide shot. The camera breaks through the final layer of mist, revealing a vast alien forest bathed in soft violet morning light.
overall_soundscape: Wind settles into a quiet ambient alien soundscape; distant crystal chimes echo softly.
non_diegetic_music: The orchestral swell smooths into an ethereal synthesizer pad.

【场景：角色登场·丛林深处】
镜头2 - 角色登场 (12s)
通道: r2v (参考图模式)
参考图: Picture 1(角色主视角) + Picture 2(丛林远景)
For the target video, at 0.00 seconds into the target video, <Picture 1> and <Picture 2> are fully referenced.
integrated_multimodal_description:
[Shot 2] Cinematic, medium tracking shot. The tall, slender XILNAR figure walks alone through the glowing purple vegetation. The camera tracks steadily behind the alien.
overall_soundscape: Soft crunch of crystalline debris underfoot.
non_diegetic_music: Minimalist solo piano notes at a slow tempo, sparse and melancholic.
```

## 场景与镜头标注规则（本次重点）

1. **场景分组**：按剧情的地点 / 时间变化切分。地点或时间一变，就开新的 `【场景：名字】` 分组。同一场景的连续镜头放在同一个分组里。
2. **场景命名**：`【场景：名字】` 里的名字要能对应到用户剧本里的地点（如「序章·星云深处」「角色登场·丛林深处」「危机降临」「高潮」）。用户会拿这个名字到节点的「场景管理」里建场景。
3. **镜头编号**：每个镜头用 `镜头N`（N 全片连续；拆段用 `1A/1B` 表示同属一个镜头拆出的两段）开头。用户会按编号顺序把镜头排进节点时间线。
4. **镜头编号与场景要一致**：`【场景：名字】` 分组下所有的镜头，都属于该场景；换场景 = 换分组标题。这样用户才能把每个镜头挂到正确的「所属场景」。
5. **@资产名引用**：提示词里出现角色/场景的地方用 `@资产名`（如 `@XILNAR`、`@丛林深处`），名字必须和素材库资产名完全一致。

## 提示词内容要求（质量红线）

1. **每镜必须写明确运镜**（推 / 拉 / 摇 / 移 / 环绕 / 升降 / 俯冲…），不要一镜到底固定机位。
2. **动作写具体动词**（低头 + 伸手 + 镜头拉近），不要写"缓缓低头"这类弱动作。
3. **单镜时长控制在 5~15 秒**，建议 8~12 秒。长镜头拆成多个连续镜头（如 1A/1B），镜头编号连续即可，不必解释为什么拆。
4. **画面描述用英文**（匹配 H3 模型能力），对话 / 歌词保留原文语言。音效（overall_soundscape）和音乐（non_diegetic_music）按 H3 规范写。
5. **按播放顺序排列**所有镜头；同一场景的镜头放在同一个分组里连续排放。

## 交给你之后用户怎么用

1. 用户到节点「场景管理」按你的 `【场景：名字】` 建好场景。
2. 把每个镜头的提示词（含 `通道` / `参考图` / `integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music`）整段复制进对应镜头的「提示词」框。
3. 每个镜头的「所属场景」下拉选到你标注的那个场景。你不需要管节点内部如何存储，这些都不归你管。

---

> 一句话记住：**你要交的是"带场景分组、镜头编号的 H3 提示词文案"，不是一个文件。**
