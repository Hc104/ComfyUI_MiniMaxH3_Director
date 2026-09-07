# 《AI 漫剧导演生成约束层 v1.0》（PROMPT_COMPILER_V1）

> 2026-08-18 · 用户汇报「画风可以了，但提示词的约束感觉有点弱了」立项 · 四项决策已拍板
> 状态：📐 设计定稿，待用户拍板后进入 P0 实施

## 0. 背景与根因

用户实测失败案例（提示词原文）：

```
顾琰宸从西装内袋掏出支票，将支票从手中甩落到林薇薇面前的桌面，
支票落桌后轻微滑动，林薇薇没有伸手去拿，
中景拍摄顾琰宸甩出支票，特写支票滑动至桌面，林薇薇保持静止，冷漠
```

生成画面出现三个病灶：**办公室多出第三个人 / 同一个角色复制成两个同框 / 一个镜头自己切成两个景别**。

**诊断（用户判断 + 我核实现有链路后一致）**：根因不是提示词不够长，而是**缺一层「生成约束」**——分镜描述只说了「发生什么」，没锁死「画面里只允许出现谁、有几个、不许干什么、机位不许怎么变」。三个具体病灶：

1. **一镜两景别**：「中景拍摄…」+「特写…」两个矛盾机位指令同时进一个 Shot → H3 收到矛盾信息自行切镜 → 切镜正是角色复制/多人物最容易爆发的时刻。
2. **只有名字，没有硬计数**：「顾琰宸」「林薇薇」只是两个名字，没告诉 H3「画面里只有这两个人，每个人只出现一次」→ H3 自由补路人、补第三角色。
3. **动作是线性文字不是编号时间轴**：掏→甩落→滑动→不伸手挤在一段文字里，动作边界模糊；「冷漠」这种心理词直接进画面，H3 自行脑补动作。

## 1. 架构原则（用户定稿）

- **分镜描述 ≠ 视频生成 Prompt**。分镜是「剧情意图」，Prompt 是「画面执行指令」，二者之间必须隔一层编译。
- 完整链路：`小说 → Beat → Shot → DirectorIntent → Shot Planning → Prompt Compiler → H3 Prompt`。
- 六项优先（用户点名）：**Character Lock + Character Count + Spatial Lock + Action Timeline + Camera Lock + Forbidden Conditions** 进入 Prompt Compiler，才算系统层面处理「第三个人/角色复制/自行切景别」。
- 纯规则模块：零 LLM 零显存零网络（与 `core_action.py`/`beat_rhythm.py`/`spatial_continuity.py` 同策略），单测覆盖。

## 2. 与既有规则层的关系（不推翻，只加层）

| 既有模块 | 提供什么 | 本层怎么用 |
|---|---|---|
| `spatial_continuity.SpatialState` ✅ | 180° 轴线 + 左右站位 + 视线轴（双语块） | Spatial Lock 数据源，直接复用 block() |
| `core_action.py` ✅ | 一镜一核心动作（动词优先级+过程措辞） | Action Timeline 的 primary 标记 |
| `beat_rhythm.py` ✅ | tension/rhythm/reaction_forced | 禁动镜（峰值定格静止）→ Camera Lock 增强 |
| `visual_style.py` ✅ | 画风块 + 防崩坏双语 | 画风约束（已有，本层不再动） |
| `camera_template.py` ✅ | 景别/运镜结构化字段 | Camera Lock 单机位状态来源 |

**新增层**（本设计文档主体）：`shot_plan.py` + `prompt_compiler.py` + `constraint_checker.py`。

## 3. 数据模型

### 3.1 `shot_plan.py` —— Shot Planning 层

```python
@dataclass
class CharacterLock:
    name: str
    appearance: str = ""            # Qwen 导入产出（P1）/ 规则提取 / 空
    role: str = ""                  # 剧本角色定位（已有）

@dataclass
class ActionItem:
    text: str                       # 动作短语
    subject: str = ""               # 动作主体（谁做）
    is_primary: bool = False        # 核心动作标记（来自 core_action）

@dataclass
class ShotPlan:
    shot_id: str
    scene_id: str
    character_count: int = 0
    character_locks: List[CharacterLock] = field(default_factory=list)
    spatial_lock: str = ""          # 复用 spatial_continuity block()（双语）
    action_timeline: List[ActionItem] = field(default_factory=list)
    camera_lock: Dict[str, str] = field(default_factory=dict)  # {shot_size, movement, cn}
    performance_lock: List[str] = field(default_factory=list)  # 禁止动作（原文否定句）
    forbidden: List[str] = field(default_factory=list)         # 生成禁止事项
    shot_sizes_seen: List[str] = field(default_factory=list)   # 检测到的景别（≥2 冲突）
```

**派生规则**（纯规则）：

- `character_count` = `len(intent.characters)`；为 0 → 检查器记 warning（送 H3 前自动补）。
- `character_locks`：每个角色一条；`appearance` 来自 P1 的外观档案（本阶段无则空串）。
- `spatial_lock`：直接取 `intent.continuity.spatial.block`（v1.0 已有双语块）。
- `action_timeline`：`intent.action` 保序编号；与 `core_action` 匹配项标 `is_primary`；无 action 时退化为单条核心动作。
- `camera_lock`：`intent.camera_position` + `intent.movement` 组装单机位状态。
- `performance_lock`：原文否定句抽取——`X 没有/未/不 V`（如「林薇薇没有伸手去拿」→「林薇薇不得伸手拿支票」）；`X 保持静止` → 禁动。
- `shot_sizes_seen`：扫描 `user_original_intent + retained_facts + composition` 的景别词（远景/全景/中景/近景/特写），去重保序。
- `forbidden`：基础清单 = 「画面中不得出现未列出的角色 / 每个角色不得重复出现 / 不得中途切换景别 / 场景不得改变」+ performance_lock 逐条双语。

### 3.2 `prompt_compiler.py` —— Prompt Compiler 层

```python
def compile_constraint_block(plan: ShotPlan) -> str:
    """ShotPlan → 双语约束块（注入 integrated_multimodal_description）。"""
```

输出结构（**中文 + 英文双语**，英文保 H3 执行稳，中文保可读——沿用 v1.0 双语铁律）：

```
角色锁定：画面中只有 2 名角色——顾琰宸、林薇薇；每名角色全程只出现一次，不得复制。
Character lock: exactly 2 characters — 顾琰宸, 林薇薇. Each appears exactly once, no duplicates.
空间锁定：…（复用 spatial block，已有双语）
动作顺序：1. 掏出支票 2. 甩落桌面 3. 支票滑动 4. 林薇薇不伸手（核心动作：甩落支票）。
Action sequence: 1.. 2.. 3.. 4.. (primary: 甩落支票).
镜头锁定：单机位 中景 固定机位，全镜不变，禁止中途切换景别或机位。
Camera lock: single camera state (medium, static) throughout. No shot-size or camera change.
禁止：画面不得出现第三个角色；不得切换场景；林薇薇不得伸手拿支票。
Forbidden: no third character; scene unchanged; 林薇薇 must not reach for the check.
```

注入位置：`_build_description` 中「空间连续性块」之后、「光线/氛围」之前（约束是画面硬边界，优先于装饰性描述）。

### 3.3 `constraint_checker.py` —— 镜头硬约束检查器（送 H3 前）

```python
@dataclass
class ConstraintIssue:
    severity: str      # error | warning | info
    code: str          # multi_shot_size / missing_character_count / multi_primary_action / ...
    message: str       # 中文提示（用户可见）
    fix: str = ""      # 建议动作

def check_shot_plan(plan: ShotPlan) -> List[ConstraintIssue]: ...
def check_intent(intent) -> List[ConstraintIssue]: ...   # 从 DirectorIntent 重建检查（路由用）
```

| code | severity | 触发 | message |
|---|---|---|---|
| `multi_shot_size` | warning | `shot_sizes_seen` ≥2 种 | ⚠️ 当前镜头包含 中景 + 特写，请拆成两个 Shot |
| `missing_character_count` | warning | `character_count` == 0 | 角色数量未明确，送 H3 前自动补「Exactly N characters」 |
| `multi_primary_action` | warning | 核心动作候选 ≥2 | 本镜有多个核心动作，已自动拆主/次（核心=…） |
| `missing_primary_action` | warning | 无 core_action 且无 action | 本镜缺少核心动作，请补充明确动作 |
| `empty_camera` | warning | 景别/运镜皆空 | 本镜缺少机位设置，H3 可能自行切景别 |
| `character_duplication` | info | 角色名重复 | 出场角色名单含重复项，已去重 |
| `scene_locked` | info | 场景已锁定 | 本镜场景锁定：… |

检查器只**报告 + 建议**，不改原文（与 `psych_visualize.py` 同铁律）；自动补全（Exactly N / 拆主次）只影响编译输出，绝不动 `source_text`/`retained_facts`。

## 4. 实施阶段

### P0 —— 三个纯规则模块 + 单测（本轮核心）

| 文件 | 内容 | 单测 |
|---|---|---|
| `director/shot_plan.py` | ShotPlan/CharacterLock/ActionItem + `build_shot_plan` | `tests/test_shot_plan.py` |
| `director/prompt_compiler.py` | `compile_constraint_block` | `tests/test_prompt_compiler.py` |
| `director/constraint_checker.py` | ConstraintIssue + `check_shot_plan`/`check_intent` | `tests/test_constraint_checker.py` |

### P1 —— 接线进真实生成链路

- `DirectorIntent` 新增字段 `constraint: Optional[Dict[str, Any]] = None`（to_dict/from_dict 全缺省向后兼容）。
- `build_shot_intent` 内调用 `build_shot_plan` 填 `constraint`（provenance=RULE）。
- `_build_description`：`intent.constraint` 非 None 时渲染约束块（注入点见 §3.2）。
- `/prompt/h3` 响应新增 `constraint_check: {scene_id:shot_id: [issues]}`（前端可追溯，本轮不消费）。

### P2 —— Qwen 角色外观档案（用户决策 Q1：Qwen 导入产出）

- `Character` 加 `appearance: str = ""`；`ProductionPlan` 加 `character_profiles: Dict[str, str]`（name→appearance，跨镜聚合）。
- `script_analyzer` 场景模板加**可选** `character_profiles` 输出（Task A 扩展，铁律=只从原文+合理外观推断，绝不自行创造服装）；单镜模板兼容旧结构。
- 规则兜底：Qwen 缺字段时用 `costumes`（已有实体类型）+ `role` 拼装；两者皆空 → appearance 留空（约束块只锁名字+数量，不虚造外观）。
- 外观档案存 `ProductionPlan.character_profiles` 随 plan 流经全链路，不经 Bible 也能进 Prompt（Bible 同步为后续增强）。

### P3 —— 前端展示（用户决策 Q4：纯后端先行，本轮**不做**）

- 检查面板 / 阻断生成 / 强制跳过开关 → 下一轮。

## 5. 向后兼容（硬铁律）

1. `DirectorIntent.constraint` 缺省 None；`from_dict` 容忍缺失 → 旧 intent 渲染逐字节不变。
2. `_build_description` 有 `constraint` 才注入约束块；None → 完全跳过。
3. `/prompt/h3` 新增 `constraint_check` key；前端对未知 key 天然忽略。
4. 既有 58 个后端测试文件全部不破；新约束输出仅在 `constraint` 非空时出现。
5. 双目录同步 SRC≡DEP md5 DIFF=0 + CHANGELOG 追加。

## 6. 验收（用户案例回放）

对失败案例提示词跑 `check_shot_plan`，期望输出：

```
⚠️ warning multi_shot_size      —— 当前镜头包含 中景 + 特写，请拆成两个 Shot
⚠️ warning multi_primary_action —— 本镜有多个核心动作，已自动拆主/次（核心=甩落支票）
✅ info  character_count        —— 角色数量=2，送 H3 自动补 Exactly 2 characters
✅ info  scene_locked           —— 本镜场景锁定：豪华私人办公室
✅ info  performance            —— 林薇薇不得伸手拿支票（原文否定句）
```

生成 H3 Prompt 描述区含双语约束块（§3.2），画面应锁死 2 人、无第三人、无角色复制、单机位无景别切换。

## 7. 开放决策（遗留，本轮不阻塞）

- D1：约束块默认开/关（建议默认开，P3 前端加总开关）。
- D2：`character_duplication` 等 info 级提示是否进 UI（P3）。
- D3：外观档案是否同步写 Story Bible `attributes["appearance"]`（建议 P2 顺带，低风险）。
