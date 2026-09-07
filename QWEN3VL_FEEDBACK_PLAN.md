# 待办⑤ 实施计划：Qwen3-VL 三级反馈（AI 导演判断）v2

> 状态：**里程碑 A 已定稿（用户实测通过）+ 里程碑 B 已定稿（用户实测通过）+ 里程碑 C 已定稿（用户实测通过：防误判验证）**
> 日期：2026-08-07
> 目标：把死板的关键词路由升级为"AI 用眼睛看画面"的导演判断——自动提取状态、检查一致性、严重错误自动重跑。
> 原则（用户确认）：**勿全量检查，成本高**；**不要一次做大**，先做 A、再叠加 B/C。
> 硬件（用户告知）：**16GB 显存 + 24GB 内存**。

---

## 0. 一句话概述

每个镜头生成完后，把它的尾帧/关键帧交给本地 Qwen3-VL 视觉模型"看"一下：
- **一级（状态提取）**：输出结构化状态 JSON（角色/地点/时间/动作 + **character_state + camera_state**）→ 更新导演状态库。
- **二级（一致性检测）**：只在关键镜头用参考图对比生成帧，输出**结构化判断**（match 布尔 + reason）。
- **三级（失败重跑）**：Qwen 发现问题 **+ 规则检测确认**后才重跑该段。

分层用意：贵的视觉推理只花在刀刃上，普通镜头只跑最便宜的一级。

### ⚠️ 核心设计铁律（用户最大建议）

**Qwen 是场记，不是导演。**

```
✅ Qwen 看完 → 提出状态 → 导演状态库更新 → 下一镜头参考
❌ Qwen 看完 → 自动改剧情 → 重新生成        （禁止，风险大）
```

Qwen 只负责"记录和检查"，**不直接决定生成**。生成策略永远由 Director Core（状态库 + 镜头路由）决定，Qwen 只是它的眼睛。

---

## 1. 环境探查结论（已核实，2026-08-07）

| 项 | 结论 |
|---|---|
| ComfyUI 内置 VLM 设施 | **无**。`comfy/` 无 llm/vlm 目录，`comfy_extras/` 无 gguf/vlm 节点 |
| `nodes_qwen.py` | 是 Qwen Image Edit 文生图节点，**不是** Qwen3-VL VLM，不可复用 |
| `models/LLM`、`models/GGUF` | 空目录，仅约定俗成的模型放置位 |
| `comfyui-qwenmultiangle` | 不加载 VLM，无 requirements，不可复用 |
| 模型推理栈 | 需要**新装**（见 §2） |

**结论**：Qwen3-VL 反馈必须作为独立模块接入，不走 ComfyUI 内置加载器。显存策略靠"段间错峰 + 强制卸载"。

---

## 2. 技术选型：VLM Backend Interface（不写死单一方案）

**用户决策：接受 llama.cpp + Qwen3-VL GGUF，但用抽象接口解耦，换后端不改导演逻辑。**

```
VLM Backend Interface
        |
        |-- llama.cpp GGUF（默认方案）
        |
        |-- transformers（备选方案）
```

```python
class VisionBackend:
    """视觉后端抽象接口。所有导演逻辑只依赖此接口，不感知具体后端。"""
    def analyze(self, image, prompt: str) -> str:
        """喂单张/少量关键帧 + 提示词，返回模型原始文本输出。"""
        raise NotImplementedError


class QwenLlamaBackend(VisionBackend):
    """llama.cpp + Qwen3-VL GGUF（推荐，int4 显存最小）。"""
    ...

class QwenTransformersBackend(VisionBackend):
    """transformers + qwen-vl-utils（备选，依赖更重）。"""
    ...
```

### 推荐方案：Qwen3-VL-4B-Instruct GGUF + llama.cpp

| 项 | 选择 | 说明 |
|---|---|---|
| 模型 | Qwen3-VL-4B-Instruct（Q4_K_M GGUF） | 视觉语言模型，看帧输出 JSON；int4 量化后 ~3GB 显存 |
| 推理库 | `llama-cpp-python`（CUDA 版） | llama.cpp 官方支持 Qwen3-VL 视觉（mmproj 投影） |
| 模型放置 | `models/vlm/Qwen3-VL-4B-Instruct-Q4_K_M.gguf` + `.mmproj` | 新建 `models/vlm/` 目录 |
| 加载方式 | 段间懒加载：推理时加载、用完立即释放 | 不与 H3 生成抢显存 |
| 输出解析 | 结构化提示词 → 强制 JSON → `json.loads` 容错 | 防模型输出非 JSON |

**备选方案**：`transformers` + `qwen-vl-utils` + `bitsandbytes` 4bit。显存 ~4-5GB。通过同一 `VisionBackend` 接口接入，导演逻辑零改动。

> ⚠️ 需要你下载的文件（唯一外部准备）：
> - `Qwen3-VL-4B-Instruct-Q4_K_M.gguf`（约 3GB）
> - 对应 `.mmproj` 视觉投影文件（约几百 MB）
> - 放置到 `models/vlm/`
> - 在 ComfyUI 的 Python 环境 `pip install llama-cpp-python`（CUDA 版）

---

## 3. 新增/改动文件清单

### 后端（核心）

| 文件 | 改动 |
|---|---|
| **`director/vlm_backends.py`**（**新增**） | `VisionBackend` 抽象基类 + `QwenLlamaBackend` / `QwenTransformersBackend` 两个实现；统一 `analyze(image, prompt)` |
| **`director/qwen_vl_feedback.py`**（**新增**） | 导演级反馈逻辑（依赖 vlm_backends，不感知具体后端）：帧→PIL→后端 analyze→JSON 解析→三级函数 `extract_state` / `check_consistency` / `detect_issue` + 显存释放 |
| `director/plan.py` | `DirectorPlan` 加 3 字段：`qwen_vl_enabled: bool = False`、`qwen_vl_level: int = 1`、`qwen_vl_key_segments: bool = False`；新增 `QwenVlFeedback` dataclass（state_json / match_result / issues / rerun_required） |
| `director/gen_timeline.py` | 解析 `output.qwenVlEnabled` / `output.qwenVlLevel` / `output.qwenVlKeySegments` 到 plan 字段 |
| `director/executor_core.py` | **段生成完成后、`cleanup_segment_vram` 窗口内**插入反馈回调：一级→`extract_state` 更新 `state_holder["current"]`；二级→关键镜头跑 `check_consistency`；三级→`detect_issue` 为真且规则确认→标记重跑 |
| `director/state_rule_check.py`（**新增**，三级用） | 规则检测：手数量/人脸数量/分辨率异常等（不依赖 VLM，纯视觉/规则判断，与 Qwen 双确认） |
| `nodes/director_common.py` | 报告加 `Qwen3-VL 反馈:` 行 |
| `requirements.txt` | 加 `llama-cpp-python`（或注明手动安装） |

### 前端

| 文件 | 改动 |
|---|---|
| `web/js/minimax_director_panel.js`（**新增**） | 独立「AI 导演设置」面板（用户决策 #6）：自动续接 / 状态跟踪 / FL2VA路由 / Qwen视觉反馈 / 质量控制 分组 |
| `web/js/minimax_image_batch.js` | r2v 工具栏的 自动续接/状态跟踪/FL2VA 入口收敛到新面板（或保留快捷开关） |
| `web/js/minimax_timeline.js` | `buildTimelinePayload` 白名单带 `qwenVlEnabled` / `qwenVlLevel` / `qwenVlKeySegments`（**任务 #29 的教训**） |
| `web/js/minimax_i18n.js` | 加 ZH 键（`panel.director.*` / `tooltip.qwenVl` 等） |

---

## 4. 关键实现设计

### 4.1 显存/内存策略（16GB 显存 + 24GB 内存）

```
段 N 生成 → 采样完成 → decoded 落盘/缓存
        → cleanup_segment_vram() 清 H3 显存
        → 【Qwen3-VL 窗口】懒加载后端 → 喂尾帧/关键帧 → 推理 → 拿 JSON → 立即释放后端 + torch.cuda.empty_cache()
        → 段 N+1 加载 H3 继续生成
```

- 后端**不常驻**，每段用完即释放。4B int4 加载约 1-3 秒，可接受。
- 若 OOM：第一反应查 VLM 后端是否未正确卸载（不是代码 bug）。
- RAM：4B GGUF 加载约 4GB，24GB 内存无压力。

### 4.2 一级：状态提取（用户增强：+ character_state + camera_state）

**用户核心洞察：接缝问题的本质就是缺少镜头状态。** 状态提取不只报"陆玄在广场"，还报角色姿态/朝向/情绪 + 镜头景别/角度，为未来 FL2VA/R2V 路由提供决策依据。

- 输入：本镜尾帧（1 张）+ 上一镜状态（上下文）+ 本镜提示词。
- Prompt 模板（强制 JSON）：
  ```
  你是视频分镜场记。看图，输出 JSON：
  {
    "character": "陆玄",
    "location": "圣城广场",
    "time": "黄昏",
    "character_state": {
      "pose": "站立",
      "direction": "朝向天碑",
      "emotion": "震惊"
    },
    "camera_state": {
      "shot": "中景",
      "angle": "正面"
    }
  }
  只输出 JSON，不要解释。
  ```
- 输出写入 `state_holder["current"]`（拼接为状态前缀）。**用户手填优先**：用户填了 stateChange 用用户的，没填才用 Qwen 提取的。

### 4.3 二级：一致性检测（用户调整：结构化判断，非 0~1 分数）

**用户指出：视觉模型不一定适合做 embedding 相似度，0~1 分数不可靠。** 改为让 Qwen 输出结构化判断：

- 输入：角色/场景参考图 + 生成帧（关键镜头才跑）。
- 输出（强制 JSON）：
  ```
  {
    "character_match": true,
    "scene_match": true,
    "clothing_match": false,
    "reason": "人物衣服颜色发生变化"
  }
  ```
- **关键镜头判定（用户设计：自动 + 手动可覆盖，已实现）**：每段卡片有「关键镜头」三态下拉
  （`seg.consistencyCheck`）——`undefined/"auto"`=有角色/场景资产注入即自动检测（有对照物才算关键镜）、
  `true`=强制检测（无资产时底层优雅降级为"未查"）、`false`=跳过（即使有资产注入）。后端
  `gen_timeline` 解析成 `SegmentPlan.consistency_check`，executor 按三态路由。
- `match=false` 时在报告提醒；**不重跑**（重跑留给三级）。
- **未来精确评分**（用户架构：Qwen 负责理解、CLIP/DINO 负责算相似度）：加 `clip_score` / `dino_score` 模块做视觉 embedding，输出精确数值。本期不实现，留接口。

### 4.4 三级：失败重跑（用户调整：Qwen + 规则双确认）— ✅ 已实现（待用户实测）

**用户指出：不让 Qwen 一个模型决定重跑，否则误判→无限生成。**

```
Qwen 报严重问题（issues 命中"崩坏/多手/换脸/畸形…"关键词，severity=high）
        +
state_rule_check.py 规则确认（纯像素客观检测：过暗/过曝/纯色/模糊/分辨率异常）
        ↓
confirm_rerun 双确认成立 → 换种子重跑（最多 1 次）→ 替换输出
```

- **Qwen 只做语义判断**：`detect_issue` 采样首/中/尾帧喂 VLM，输出 `{issues[], severity, reason}`。强 JSON + 失败重试一次；severity 缺失/乱写时按 issues 命中 `SEVERE_ISSUE_KEYWORDS` 兜底判 high/low。
- **规则做客观确认**：`state_rule_check.rule_check_frames` 对帧做像素级检测（平均亮度 <0.03 过暗 / >0.97 过曝、标准差 <0.012 纯色、Laplacian 方差 <0.00035 模糊、分辨率 <320×240），全部用 torch 张量运算，无 VLM 依赖。
- **双确认（confirm_rerun）**：Qwen issues 非空 + severity=high **且** 规则检测 abnormal → 才重跑；任一侧不成立都不重跑（防 Qwen 单方面误判 → 无限生成）。
- 重跑：`seed = (seed+1) & 0xFFFFFFFF` 重新采样，替换 decoded/chunk/audio 输出 + 写段缓存；重跑失败保留原输出并记 `qwen_rerun_note`。报告追加一行 `重跑: 已重跑(原因; seed=…→…)` / `不重跑: 原因` / `轻微问题: …`。
- **三级默认关**（成本最高，每段多一次 VLM 推理），用户显式开启才启用。

### 4.5 前端：独立「AI 导演设置」面板（用户决策 #6）

**用户指出：Qwen 不属于 R2V，属于整个导演系统，不该埋在 r2v 工具栏。**

```
AI导演设置
├ 自动续接
├ 状态跟踪
├ FL2VA路由
├ Qwen视觉反馈   ← 开关 + 级别（关/1/2/3）+ 关键镜头标记
└ 质量控制
```

- 开关语义：
  - **关闭**（默认）：完全不走 Qwen3-VL，行为与现在一致。
  - **1级**：只状态提取（含 character_state / camera_state）。
  - **2级**：状态提取 + 一致性检测（关键镜头）。
  - **3级**：全部 + 失败重跑（Qwen + 规则双确认）。

---

## 5. 最终架构（用户定稿）

```
                 剧本
                   |
                   ↓
             Director Core
                   |
       -------------------------
       |                       |
    状态库                  镜头路由
       |                       |
       |              ----------------
       |              |              |
       |             R2V           FL2V
       |
       ↓
       Qwen3-VL Feedback
       |
       ├ 状态提取
       ├ 一致性检查
       └ 错误反馈
```

---

## 6. 实施顺序（里程碑 A→B→C，用户确认顺序正确）

**里程碑 A（一级状态提取，含 character_state + camera_state，推荐先做）— ✅ 已定稿（用户实测通过）**
1. ✅ `vlm_backends.py`：`VisionBackend` 接口 + `QwenLlamaBackend`（先做 llama.cpp，transformers 留接口）
2. ✅ `qwen_vl_feedback.py` 骨架：加载 + 单帧推理 + JSON 解析（不接 executor，先用脚本自测）
3. ✅ 后端字段 + gen_timeline 解析
4. ✅ executor 挂回调 + 状态自动更新（含 character_state/camera_state 进状态库）
5. ✅ 前端「AI 导演设置」面板 + 级别开关（关/1级）+ 报告显示
6. ✅ 用户实测：状态跟踪用 Qwen 提取的状态填充，报告显示 `Qwen3-VL 反馈: 状态=…`

**里程碑 B（二级一致性）— ✅ 已定稿（用户实测通过）**
7. ✅ `check_consistency` 结构化判断 + 关键镜头三态覆盖（前端下拉 + 后端 per-segment 路由）
8. ✅ 报告显示 match 结果 + reason

**里程碑 C（三级失败重跑）— ✅ 已定稿（用户实测通过：防误判验证）**
9. ✅ `state_rule_check.py` 规则检测（纯像素：过暗/过曝/纯色/模糊/分辨率）+ `qwen_vl_feedback.detect_issue`（Qwen 语义）+ `confirm_rerun` 双确认 → 换种子重跑、限 1 次、替换输出
10. ✅ 前端级别扩展为 3 级（关/1级/2级/3级，i18n 说明更新）
11. ✅ mock 自测通过（JSON 容错 / detect_issue / confirm_rerun / 规则阈值）+ 部署同步 + 记忆/文档更新
12. ✅ 用户实测：段1 Qwen 报「多手、肢体畸形、面部崩坏、画面崩坏」severity=high → 规则无客观异常 → 显示「不重跑: Qwen 报…但规则无客观异常」——双确认防误判验证通过；用户确认保持现状（不加目标检测、不降像素阈值）

---

## 7. 验证标准

| 里程碑 | 通过标准 |
|---|---|
| A | ✅ 已通过：报告出现 `Qwen3-VL 反馈: 状态=「…」`；段 2 状态前缀用段 1 的 Qwen 提取状态；显存无 OOM |
| B | ✅ 已通过：关键镜头（段1 r2v 有资产注入）报告出现 `一致性: 角色=True; 场景=True; 服装=True (人物面部发型与服装细节与参考图一致…)`；fl2v 硬锁段（无资产注入）不跑 |
| C | ✅ 已通过（防误判）：段1 Qwen 报「多手、肢体畸形、面部崩坏、画面崩坏」severity=high + 规则无客观异常 → 报告显示 `不重跑: Qwen 报…但规则无客观异常，不重跑`——双确认防误判生效；多手类语义错误按设计不重跑（用户确认保持现状）。重跑触发分支（规则 abnormal 时换种子重跑）机制已实现，待画面同时含像素异常时自然触发 |

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 16GB 显存 OOM | 段间错峰 + 强制卸载；模型降级 2B；用户可关反馈 |
| llama.cpp 构建失败（Windows） | VLM Backend Interface 换 transformers 实现，导演逻辑零改动 |
| 模型输出非 JSON | 强 JSON prompt + `json.loads` 容错 + 失败重试一次 |
| Qwen 误判导致无限重跑 | 三级双确认（规则检测）+ 每段限重跑 1 次 |
| Qwen 提取状态不准 | 用户手填优先，Qwen 提取兜底 |

---

## 9. 决策点状态（v2 已确认）

1. ✅ **技术选型**：llama.cpp + Qwen3-VL GGUF，**做成 VLM Backend Interface 不写死**（用户 2026-08-07）
2. ✅ **里程碑顺序**：A→B→C（用户确认）
3. ✅ **一级状态提取增强**：增加 `character_state` + `camera_state`（用户确认，接缝本质=缺镜头状态）
4. ✅ **Qwen 权限**：场记不是导演，只更新状态库不直接决定生成（用户最大建议）
5. ✅ **二级一致性**：结构化判断（match + reason），非 0~1 分数；未来 CLIP/DINO embedding 留接口
6. ✅ **三级重跑**：Qwen + 规则双确认，限重跑 1 次
7. ✅ **前端入口**：独立「AI 导演设置」面板（不属于 R2V）
8. ✅ **模型/后端已就绪**：用户下载 Qwen3-VL-4B-Instruct（transformers 格式）到 `models/vlm/Qwen3-VL-4B-Instruct/`；因 Python 3.13 无 llama-cpp-python 预编译 wheel，**改用 transformers 后端**（QwenTransformersBackend，fp16，约 9GB，段间错峰加载）
