# TTS Voice Cast 声音导演层整体规划（P 系列）

> 决策时间线：
> - 2026-08-15 决策树进入 TTS/Voice Cast 分支（B 路实测失败）
> - 2026-08-16 拍板「先设计 AudioIntent 数据模型」→ #551/#552 验收全绿（88 PASS / 0 FAIL）
> - 2026-08-16 拍板「先写整体规划文档」→ #553
> - **2026-08-16 五项待拍板全部定稿 + 引擎定位修正 + 测试项目定为《吐槽在漫画里封神》→ #554**

## 0. 背景与根因（为什么必须上 TTS）

H3 音频诊断全链路（#545 系列）结论：**H3 原生对白通道实测不可用**——用户实测「一句话没听懂」：

- 对白进 `overall_soundscape`（修复前）→ 糊；
- 对白进描述区 `<d>[Chinese]` 块（修复 D，#545）→ 仍糊。

根因：H3 擅长沙龙式整体音频（环境音 / BGM / 短音效 / 氛围），**不擅长「一段长对白逐字清晰」**。官方格式也注明对白属描述区，但模型对中文长对白的可懂度不达标。

**结论**：`听得清对白`的唯一可靠路径 = 外接 TTS。H3 继续负责画面 + 环境声音，TTS 负责"演员念台词"，FFmpeg 负责把两者合成。

**架构方向（用户确认）**：完整音频问题已被拆成两个独立问题——**H3 负责「这个世界听起来是什么样」，TTS 负责「演员到底说了什么」**。

## 1. 已完成（#551/#552，验收全绿 88 PASS / 0 FAIL）

| 文件 | 内容 |
|---|---|
| `director/production_plan.py` | `VoiceType` 四类声音 + `normalize()`；`Dialogue` 扩展 6 字段（type/voice_id/emotion/delivery，全默认空值向后兼容） |
| `director/script_analyzer.py` | `_clean_dialogues` 透传声音字段（Qwen 识别不丢，type 过 normalize） |
| `director/audio_intent.py`（新建） | `infer_voice_type` / `default_voice_id` / `VoiceCast`（含 `voice_system_electronic`）/ `VoiceLine`（含 start_sec/end_sec 混音锚点）/ `AudioIntent` / `build_audio_intent(shot, scene, cast=)` |
| `director/tests/test_audio_intent.py`（新建） | 12 个测试函数；⛔ 纯规则零 LLM 零显存扫描 |

**数据流**：`Shot.dialogue`(6 字段) → `build_audio_intent()`（纯规则）→ `AudioIntent{lines[voice_id 已解析], voice_cast, ambient, music}` → TTS Engine。

## 2. 目标架构终版

```
小说 → Qwen → Beat → Timeline → Shot → DirectorIntent
                                          │
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
                    H3 Prompt                          AudioIntent
               （画面+环境音+BGM+短音效）               （声音导演计划）
                          │                               │
                          ▼                               ▼
                 H3 生成视频（带音轨）              Voice Cast → TTS Engine
                                                          │（Edge-TTS 测试引擎 →
                                                          ▼  MiniMax Speech 生产候选
                                                  TTS 台词音频（逐行落盘）→ 本地克隆增强）
                          │                               │
                          └───────────┬───────────────────┘
                                      ▼
              FFmpeg 混音（H3 原音轨保留 + TTS 人声叠加 + 自动 ducking）
                                      ▼
                                   成片
```

### 分工铁律

| 层 | 负责 | 不负责 |
|---|---|---|
| **H3** | 摄影 + 环境声音（画面 / 环境音 / BGM / 短音效）——「世界听起来什么样」 | 长对白逐字清晰（实测做不到） |
| **TTS** | 演员（人物对白 / 旁白 / 内心独白 / 系统音）——「演员说了什么」 | 画面 / 环境音 / BGM |
| **FFmpeg** | 后期混音：H3 音频**保留** + TTS 人声**叠加** + **对白时自动 ducking**（非静音全配音） | 内容生产 |

- V1 不做口型同步（Wav2Lip 推迟 V2，**明确不做**，用户拍板）。
- 不重设计 P1-B：声音导演层是在 Shot/Dialogue 之后追加的独立层。

## 3. 四类声音类型（VoiceType）与 voice_id 规划

| type | 含义 | 示例 | voice_id |
|---|---|---|---|
| `character_dialogue` | 角色对白 | 林薇薇：「完了。穿越了。」 | `voice_角色名`（净化标点保留中文，如 `voice_柳如烟`） |
| `narration` | 旁白 | 林晓感觉胸口那股郁结之气…… | `voice_narrator`（固定） |
| `inner_monologue` | 内心独白 | 「完了。穿越了。」（内心） | 角色 voice_id（同角色对白） |
| `system_voice` | 系统音 | 【吐槽值+50！】 | `voice_system`（固定）/ `voice_system_electronic`（delivery 含「电子」时） |

- **类型解析优先级**（确定性）：① 特殊说话人名强规则（旁白/系统…）→ ② 显式 `Dialogue.type` → ③ 规则兜底 character_dialogue。
- **voice_id 优先级**：显式 `d.voice_id` → 显式 `VoiceCast.entries` → `default_voice_id` 规则兜底。
- `VoiceCast` 是「角色 → 音色」的**跨集稳定映射**（100 章同一个林薇薇音色不变）。

## 4. 阶段规划（拍板定稿版）

### Phase 0 — TTS Engine 抽象（Edge-TTS = 管线测试引擎）

> **定位（用户拍板修正）**：Edge-TTS **不是最终配音方案**，只是管线测试引擎。只要能证明
> `Dialogue → TTS → WAV/MP3 → 时间轴 → FFmpeg → 成片` 整条链路成立即可，**不做深度优化、不花过多精力**。

> **✅ 实施状态（2026-08-16，任务 #554/#555/#556）**：0-A/0-B/0-C 已全部落地，SRC+DEP 双目录同步。
> 待用户本机真实验收（edge-tts 装好 → 三测试全绿 → POST /tts/synthesize 一句对白能听清 + 分层落盘目录正确）。

- ✅ 0-A `director/tts_engine.py`：`TtsEngine` 基类（`synthesize(text, voice_id, output_path, *, emotion, delivery)` + `list_voices()` + `preflight()`）+ `EdgeTtsBackend`（微软 Edge TTS，免费 / 中文音色多 / 云端 API 无显存压力）+ 工厂 `create_default_tts_backend()`。voice_id 归一（voice_narrator→Yunyang / voice_system*→Yunxi）+ delivery 解析（快/慢→rate、电子→pitch）。13 单测。
- ✅ 0-B `director/tts_worker.py`：`AudioIntent.lines` → 逐行 synthesize → **分层落盘**（§5.4）+ manifest.json（line → text/voice_id/emotion/delivery/时长）。8 单测。
- ✅ 0-C HTTP 路由 `GET /tts/voices` + `POST /tts/synthesize`（测试用）+ 纯 mock 单测（不碰网络，Edge-TTS 真实验收在用户本机）。7 单测。⛔ http_routes.py 改动需重启 Comfy Desktop。
- **验收**：本机 edge-tts 生成一句《吐槽在漫画里封神》对白，能听清 + 分层落盘目录结构正确。

### Phase 1/2 — MiniMax Speech（正式生产候选）

> 用户核心指标：**音色稳定 + 情绪表现 + 中文自然度 + 批量生成 + API 自动化**，不是"免费"。
> MiniMax Speech 与 H3 同生态，为最终漫剧连续生产候选引擎（可能需要 API key，待确认）。

- 1-A FFmpeg 混音器（§5.3 ducking 机制）+ 时间轴锚点 + 导出「纯 H3 / H3+TTS」。
- 2-A 前端 Workbench 音色/情绪（Dialogue 行级下拉 + Voice Cast 全局面板）。
- 2-B `MiniMaxSpeechBackend` 适配 + 引擎选择器。

### Phase 3 — CosyVoice 3 / GPT-SoVITS（本地克隆增强）

> **不急着做**（用户拍板）。定位：音色克隆 / 特殊角色 / 定制声音。CosyVoice 3 本地（RTX 5080）需过显存安全门；GPT-SoVITS 音色克隆。

### Phase 4 — Wav2Lip 口型同步（V2，推迟）

> **明确不做**（用户拍板）。TTS 音频驱动视频唇形。

## 5. 拍板结论（2026-08-16 用户统一回复）

| # | 项目 | 拍板 |
|---|---|---|
| ① | TTS 生成时机 | **A：每镜 H3 完成后自动触发 TTS**。一镜完成 → 视频+对白同时具备 → Workbench 可直接试听「H3→TTS→混音→当前镜成片」；哪镜配音有问题单独重配，不整片重导 |
| ② | 混音 | **B：H3 原音轨保留 + TTS 叠加 + 自动 ducking**（以 A 叠加为底层机制）。平时 H3 环境声正常；对白出现时 H3 自动降一点；对白结束恢复。听感比单纯 amix 专业 |
| ③ | 音色配置 | **A：Voice Cast 全局面板 + Dialogue 行级微调**。全局（林薇薇→女声A / 顾琰宸→男声B / 旁白→旁白声 / 系统→电子声）100 章自动继承；某句特殊台词行级改（音色/情绪/语速） |
| ④ | TTS 引擎 | **Edge-TTS 先跑通（仅管线测试）→ MiniMax Speech 正式生产候选 → 本地克隆引擎（CosyVoice/GPT-SoVITS）增强**。Edge-TTS 不做深度优化 |
| ⑤ | 落盘 | **A：ComfyUI output / 项目 / shot 分层**（§5.4） |

### 5.4 落盘分层（用户拍板细化）

```
ComfyUI/output/
└── minimax_studio/
    └── projects/
        └── 吐槽在漫画里封神/
            ├── shots/
            │   ├── shot_001/
            │   │   ├── video.mp4      ← H3 生成视频（原音轨保留）
            │   │   ├── h3_audio.wav   ← H3 原音轨提取（Phase 1 混音用）
            │   │   ├── tts/
            │   │   │   ├── line_001.wav
            │   │   │   └── line_002.wav
            │   │   ├── manifest.json  ← line→text/voice_id/emotion/delivery/时长
            │   │   └── final.mp4      ← Phase 1 混音成片
            │   └── shot_002/
            └── ...
```

**收益**：重新生成某一句 → 只替换 `tts/line_002.wav` → 重新混音即可，**不需要重新生成整镜**。

## 6. 关键设计决策 / 铁律（含 ducking）

1. **AudioIntent 是派生数据，不落 ProductionPlan JSON**（正式用户流程不碰内部 JSON 原则）。
2. **纯规则零 LLM 零显存**：`audio_intent.py` 不 import torch/ollama/requests（测试源码扫描锁死）；TTS 合成是唯一外部调用点。
3. **H3 侧不动**：`director_intent._audio_block`（只带 speaker/text）继续服务 H3 prompt；TTS 层在 ffmpeg 混音时合流。**别再把对白塞回 H3 prompt**（会糊）。
4. **混音 = 叠加非替换 + 自动 ducking**：
   - 底层机制：H3 音频**保留** + TTS 人声**叠加**（不静音全配音）；
   - 对白期间 H3 环境声**自动降低**（ducking，对白结束恢复）；
   - 实现方案（Phase 1）：ffmpeg `sidechaincompress`（TTS 人声作为 sidechain 触发 H3 环境声压低），或分段音量包络。验收标准：雨声持续可闻但人声清晰，听感专业。
5. **向后兼容铁律**：旧项目数据（无 type/voice_id 字段）→ `VoiceType.normalize` → character_dialogue；无 TTS 材料时导出纯 H3 逐字一致。
6. **TTS 后端可插拔**：Edge-TTS 测试引擎 → MiniMax Speech 生产候选 → 本地克隆增强。统一 `TtsEngine` 接口。
7. **显存安全门**：本地 TTS（CosyVoice 3）进显存互斥链路（`/api/ps` 检查 + 进程级互斥，⛔ 铁律绝不删）；Edge-TTS / MiniMax Speech 云端无此问题。
8. **Edge-TTS 定位**：管线测试引擎，证明链路成立即可，不做深度优化。

## 7. Golden Path 测试项目：《吐槽在漫画里封神》

用户拍板：**本篇比《山雨客栈》更适合作为 TTS Golden Path**，因为它同时包含：

- 旁白（narration）
- 角色对白（character_dialogue）
- 内心独白（inner_monologue）
- 系统音（system_voice，吐槽值/提示音）
- 情绪变化（emotion）
- 环境音（ambient）
- BGM（music）
- 多角色声音（Voice Cast 映射）

**Golden Path 目标链**（当前核心验证）：
```
小说 → Dialogue → AudioIntent → TTS → H3视频 → ducking混音 → 一镜完整成片
```
这一条跑通，项目就从「AI 生成视频」跨到「AI 自动制作漫剧」。

## 8. 参考

- `memory/tts-voice-cast-plan.md`（架构决策 + #551 交付 + #554 拍板）
- `memory/h3-audio-diagnosis.md`（B 路实测失败根因，H3 原生对白通道关闭）
- `director/audio_intent.py`（数据模型实现）
- `director/tests/test_audio_intent.py`（12 单测）
