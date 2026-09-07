# MiniMax H3 无缝衔接 —— 正确机制（官方源码版）

> 状态：2026-08-07 已根据官方源码修正，**init_latent latent 注入方案已废弃**。

## 官方源码事实（comfy_extras/nodes_minimax_h3.py）

1. **AV latent 永远是 zeros NestedTensor**：`_empty_av_latent()` 固定生成
   `NestedTensor((video_zeros, audio_zeros))`，从不承载任何生成内容。往这个 latent 里
   注入内容（latent chaining）在去噪时根本保不住，是死路。

2. **first_frame / last_frame 才是真正的硬锁定**：它们被 VAE 编码后放进
   `minimax_keyframes` 条件（每步重注入、永不去噪）：
   ```python
   if first_frame is not None:
       img = _resize(first_frame[:1], width, height, "disabled")
       keyframes.append({"resolved_frame_index": 0, "image": img})
   ...
   for kf in keyframes:
       kf["latent"] = vae.encode(kf.pop("image"))
   cond = node_helpers.conditioning_set_values(cond, {
       "minimax_keyframes": keyframes,
       "minimax_frame_count": frame_count,
   })
   ```

3. **fl2v 自动交接镜头**：`first_frame = 上一段尾帧`（prev_tail[-1:]）走
   `MiniMaxH3ImageToVideo.execute(first_frame=...)` 路径。ref_images 保持 None，
   确保不走 ReferenceToVideo 路径（那条没有 first_frame 参数）。

## 当前实现（director/executor_core.py + nodes/conditioning.py）

- `_build_minimax_inputs()`：fl2v 无显式首帧时，按面板「续接方式」路由：
  - **参考图续接（默认，handoff_mode="image"）**：无显式尾帧 + audio_vae 已连 → `ref_image_0 = prev_tail[-1:]`，
    走 `MiniMaxH3ReferenceToVideo`（参看图生视频，延续画面+运动趋势，替代 first_frame 硬锁）。
  - **从视频续接（handoff_mode="video"，2026-08-07）**：无显式尾帧 + audio_vae 已连 + prev_tail ≥ 5 帧 →
    `ref_videos = {"ref_video_0": prev_tail.clone()}`（上段整段视频），走 ReferenceToVideo，模型能看到完整运动轨迹。
    提示词用「延续参考视频 <Video 1>…继承其运动轨迹」；比参考图更吃显存。
  - 回退：有显式尾帧或 audio_vae 未连 → `first_frame = prev_tail[-1:]` 硬锁（ImageToVideo）。
- `run_minimax_conditioning()`：透传 first_frame/last_frame 或 ref_images/ref_videos 到官方节点。
- **提示词**：`fl2v_timeline.py` 新增 `REF_CONT_PROMPT_PREFIX/SUFFIX`（"延续参考图 <Picture 1>…"）和
  `REF_VIDEO_CONT_PROMPT_PREFIX/SUFFIX`（"延续参考视频 <Video 1>…"），`reinforce_fl2v_prompt(reference_mode=True/video_mode=True)`。
- **面板**：`minimax_fl2v.js` 加「续接方式」下拉（参考图续接/从视频续接），读写 `timeline.output.handoffMode`；
  `plan.py` 新增 `DirectorPlan.handoff_mode`，`build_fl2v_director_plan` 读 `output.handoffMode=="video"`。
- **报告诊断**：每段报告新增 `衔接: first_frame=参考图续接(ref_image_0=上段尾帧…)/从视频续接(ref_video_0=上段整段视频…); last_frame=…`，
  task_hint 如实显示 "fl2v — Reference-to AV … (~1 ref image(s)/ref video(s))"（旧 hint 只统计 refs，曾误导排查）。
- **allow_reference bug（2026-08-07 修复）**：旧调用点 `allow_reference=handoff_reference`，而 handoff_reference 与
  handoff_video 互斥——选「从视频续接」时 handoff_video=True → handoff_reference=False → allow_reference=False，
  导致 `_build_minimax_inputs` 的从视频分支被跳过、误回退 first_frame 硬锁。修复为
  `allow_reference=(handoff_reference or handoff_video)`，具体走图/走视频由 handoff_mode 决定。
  另加诊断：auto_handoff 回退硬锁时报告会追加原因（`audio_vae 未连接` 或 `续接方式未选择从视频续接`）。
- **r2v 自动续接（ref2va 模型，2026-08-07）**：r2v 工作流使用 ref2va checkpoint（`minimax_h3_ref2va_*`，
  对 `<Picture N>` 参考图遵循最强）。fl2v 工作流用 fl2va checkpoint（`minimax_h3_fl2va_*`，对参考视频不敏感），
  「从视频续接」在 fl2v 上效果不佳 → 移植到 r2v 工作流。机制：
  - 面板 r2v batch 工具栏新增「自动续接上段」开关，存 `timeline.output.r2vAutoContinuity`（独立于
    continuityEnabled——前端对非 fl2v 模式会强制清零该字段）。**默认开启（2026-08-07 修复）**：
    旧行为字段缺失 = 关闭，重启后工作流 JSON 不保留该字段 → 第 2+ 段静默退化成 t2v。现改为
    字段缺失一律视为 True（`gen_timeline` 里 `_raw` 默认 True，只有显式 false/"false" 才关），
    前端开关读缺失字段也默认勾选。
  - `build_gen_director_plan` 读 `output.r2vAutoContinuity` → `plan.r2v_auto_handoff`（仅 r2v 生效）。
  - 执行器：段 task=r2v 且 `r2v_auto_handoff` 且 `index>0` 时，`prev_tail = resolve_prev_segment_output(...,
    allow_without_continuity=True)`（放行 continuity_enabled 关闭的场景）；`_build_minimax_inputs` 在该段
    无显式参考媒体（refs/ref_videos/ref_audios 全空）时注入**图片锚点**（2026-08-07 图片锚点方案，去视频）：
    `ref_image_0 = prev_tail[-1:]`（`<Picture 1>`，上段尾帧，接缝起点）+ 继承上一段 r2v 段的
    角色/场景参考图 `ref_image_1..N`（`<Picture 2>…`），全部走 ReferenceToVideo（**不注入 ref_video**）。
  - 提示词：`_r2v_handoff_prompt(ref_image_count=N)` 动态生成「新镜头接续：<Picture 1> 是上一镜头的最后一帧…
    <Picture 2> 是角色参考图…<Picture 3> 是场景参考图…随后发生新的动作与运镜，不要复刻或重放上一镜头」前缀
    （已含 `<Video N>` 标签则跳过）。
  - 前置条件：需 audio_vae 已连（r2v reference conditioning 必需）；上一段须已生成（无则报错提示先跑上一段）。
- **"一段视频放两遍" 问题（2026-08-07 修复）**：纯 ref_video 续接时，ref2va 在提示词全是"延续/保持"语义下
  会把参考视频再放一遍。先改为**复合锚点**（尾帧图 + 整段视频），实测仍被 ref2va 重放；最终改为**纯图片锚点**
  （尾帧图 + 继承角色/场景图，去掉 ref_video）。ref2va 对图片遵循最强，锁接缝锁人物场景，且不会复刻上一段。
- **接缝跳（机位/朝向变）——机制性限制（2026-08-07 九修）**：纯图片锚点解决"放两遍"后，新问题：段 2 首帧
  与段 1 尾帧机位/朝向不一致（例：段 1 尾帧人物正对镜头仰头看天，段 2 首帧人物背对镜头面向天碑）。
  **根因（官方源码确认）**：`MiniMaxH3ReferenceToVideo` 的 ref_images 全部编码进 `minimax_refs`（条件参考），
  **没有 first_frame 硬锁**——ref2va 里 `ref_image_0` 语义是"参考此画面/主体"，生成时首帧机位/朝向/景别可自由发挥。
  真正的首帧硬锁 `minimax_keyframes` 只在 `MiniMaxH3ImageToVideo`（fl2va 路径）有；官方两节点互斥，
  `model_base.extra_conds` 中 refs 分支还会覆盖 keyframes 分支的 `cond_video_latents`——**官方代码层面无法在
  ref2va 段上同时"首帧硬锁 + 参考图"**（改 ComfyUI 核心文件不可取，升级即覆盖）。
  **对策（状态继承，2026-08-07 十一修）**：放弃"首帧像素级复刻"——ref2va 没有硬锁能力，
  且锁首帧会把下一镜焊死在上一镜的姿势/机位上（实测：段 2 要求低头却两次都抬头）。
  `_r2v_handoff_prompt` 改为**状态继承语义**：只锁定"同一角色、同一场景、同一时间"，
  `<Picture 1>` 尾帧降级为"环境/构图/光线参考，不必逐像素复刻"，并明确"机位和景别可以自由切换，
  随后按描述发生新的动作与运镜"。角色/场景资产图仍作为 `<Picture 2/3>` 保持主体一致。
  **同时用户提示词配合**：段 2 写清楚"新动作 + 新画面里必须出现什么"（如"低头凝视天碑碑身纹路，
  天碑矗立画面中央"），不要再写会被段 1 尾帧姿势覆盖的动作。
- **段 2 复刻段 1（2026-08-07 十二修）**：状态继承语义下，段 2 仍基本复刻段 1（头只低一点又抬回）。
  根因是 ref2va 对图片锚点遵循太强、弱约束压不住。前缀再强化为"新动作"语义：开头"画面与动作都是新的"、
  "人物必须做出清晰可见的新动作，机位景别自由切换、镜头重新取景"、`<Picture 1>` 尾帧"仅作环境/构图/光线参考，
  不要复刻姿势画面"、收尾"必须有清晰可见的新动作与情节推进，画面不要与上一镜头雷同"。
  用户提示词配合：写**具体可执行的新动作**（低头俯视+伸手轻抚碑文+镜头拉近特写），不能只写"人物缓缓低头"。

## 部署

文件同步到 Comfy Desktop（工作区源 `D:\夸克网盘\ComfyUI_MiniMaxH3_Director` → 部署目录 `D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\custom_nodes\ComfyUI_MiniMaxH3_Director`）：
```
director/core_sampling.py      # 已回滚 init_latent（官方 zeros latent 注入无效）
director/executor_core.py      # 诊断 + 续接方式路由 + r2v 自动续接（图片锚点：尾帧+继承角色/场景图）
director/plan.py               # DirectorPlan.handoff_mode + r2v_auto_handoff
director/fl2v_timeline.py      # REF_VIDEO_CONT 提示词 + 读 output.handoffMode
director/gen_timeline.py       # 读 output.r2vAutoContinuity → plan.r2v_auto_handoff
director/segment_continuity.py # resolve_prev_segment_output 加 allow_without_continuity 放行
nodes/conditioning.py          # hint 如实显示 keyframe 状态
web/js/minimax_fl2v.js         # 「续接方式」下拉 + syncFl2v + bindFl2vEvents
web/js/minimax_image_batch.js  # r2v batch 工具栏「自动续接上段」开关 + sync + bind
web/js/minimax_i18n.js         # 新增 ZH 键（EN 无对应项）
```
完全退出并重启 Comfy Desktop（__pycache__ 会按源码 mtime 自动重建）。

## 验证方法

跑 2 段 fl2v（continuity 开），面板「续接方式」切到「从视频续接」，看报告第二段的：
- task_hint 应为 `fl2v — Reference-to AV … (~1 ref video(s))`（从视频续接生效）
- 衔接行应为 `first_frame=从视频续接(ref_video_0=上段整段视频(…帧)); last_frame=无 last_frame`
- 若显示 `fl2v — Reference-to AV … (~1 ref image(s))`，说明仍是参考图续接（下拉未生效或值没写入）
- 若显示 `fl2v — First Frame (i2v keyframe)`，说明回退到了 first_frame 硬锁（audio_vae 未连或该镜有显式尾帧）

## 验证方法（r2v 自动续接）

用「参考主体生视频」工作流（ref2va 模型），2 段 r2v：第 1 段正常上传参考图（人物正脸 + 场景），
第 2 段**不传任何素材**，勾选 batch 工具栏「自动续接上段」开关，跑全部。看报告第二段的：
- task_hint 应为 `r2v — Reference-to AV … (~1+ 张 ref image)`（图片锚点生效，**无 ref video**）
- 衔接行应为 `first_frame=图片锚点续接(ref_image_0=上段尾帧 + 继承N张角色/场景参考图); last_frame=无 last_frame`
- 若显示 `r2v — Reference-to AV … (~0 refs)` 且提示退化成 t2v，说明开关未开或 `r2vAutoContinuity` 没写进 output
- 若衔接行显示 `复合续接` 或含 `ref_video_0`，说明部署未更新或重启未生效（还跑在旧版本）
- 若报错「需要上一段…请先运行上一段」，说明第 1 段未生成或无缓存（先用「选择运行」只跑第 1 段，再跑全部）
- 视觉检查：段 2 首帧应等于段 1 尾帧（接缝不跳），人物/场景与段 1 一致，且段 2 主体内容应为**新动作**
  而非段 1 复刻/重放
