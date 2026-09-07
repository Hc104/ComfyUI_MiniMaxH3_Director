/**
 * MiniMax Studio — 项目语义模型（模型无关）
 *
 * 这是前端唯一数据模型：ShotModel → TimelineStructure → Adapter → timeline_data。
 * 本文件不出现任何 H3 / prompt_batch / fl2v 字段名（那是 Adapter 的职责）。
 *
 * 原则（v1.1 已拍板）：
 * - `durationSec` 是镜头时长的唯一真相源；`frameCount/length` 由 Adapter 派生。
 * - Shot 是内容单位（内容/摄影/声音），不是模型参数容器。
 * - Scene 是世界观/一致性单位：默认素材、地点/时间/天气，镜头自动继承。
 */

import type { ProductionPlanJson, VisualStyleProfileJson } from "@/services/comfyApi";


export type AssetKind = "cast" | "location" | "prop" | "style";

/** 全局资产库条目 / 场景素材组条目（两者同构）。 */
export interface Asset {
  id: string;
  name: string;
  kind: AssetKind;
  /** ComfyUI input 目录相对路径（imageFile），浏览器可经 /view 预览。 */
  imageFile: string;
  /** V1.7 Phase 2-1（#135）：来源剧本实体 id（ent_xxx，剧本导入自动匹配时记录，
   *  指向 productionPlanToProject 消费的实体；手工建资产/旧项目无此字段）。 */
  sourceEntityId?: string;
  description?: string;
  aliases?: string[];
}

/** 场景素材组：四类资产。V1.5 完整功能，V1 允许为空。 */
export interface SceneAssets {
  cast: Asset[];
  locations: Asset[];
  props: Asset[];
  styles: Asset[];
}

/** 镜头生成参数（V1 只支持 r2v 的子集，字段名去 H3 化）。 */
export interface ShotGeneration {
  /** auto=按内容自动路由；V1 固定 r2v。 */
  taskType: "auto" | "r2v" | "fl2v";
  /** 续接方式：auto=跟随全局；none=独立镜头。 */
  continuityMode: "auto" | "none" | "ref2va" | "fl2va";
  smartTail: boolean;
  /** 状态变化描述（用于状态跟踪/Qwen 检测）。 */
  stateChange: string;
  consistencyCheck: boolean | "auto";
  /** 导入时从段 start 恢复的帧序号（排序用，非后端契约字段）。 */
  order?: number;
}

export interface ShotCamera {
  shotSize: string; // 景别：全景/中景/近景/特写…
  movement: string; // 运镜：推/拉/摇/移/环绕/升降…
  speed: string; // 速度：缓慢/平稳/急促
  depthOfField: string; // 景深：浅景深/深景深
}

export interface ShotSound {
  ambient: string; // 环境音
  music: string; // 音乐
}

export interface ShotRefs {
  refImages: RefImage[];
  refAudios: RefAudio[];
  refVideos: RefVideo[];
  genImage: GenImage;
}

export interface RefImage {
  index: number;
  imageFile: string;
  fileName?: string;
  type?: string;
  subfolder?: string;
}

export interface RefAudio {
  index: number;
  audioFile: string;
  fileName?: string;
}

export interface RefVideo {
  index: number;
  videoFile: string;
  fileName?: string;
}

export interface GenImage {
  imageFile: string;
  fileName?: string;
}

/** 渲染状态：运行时态不落盘；lastPromptHash 持久。 */
export interface ShotRender {
  lastPromptHash?: string;
  status?: "idle" | "queued" | "running" | "success" | "review" | "failed";
}

/**
 * 生成版本记录（V1.11 生成历史）：每一次成功生成独立保存
 * 「视频 + Prompt 快照 + 生成参数快照」。一个 Shot 可有 N 条；activeGenerationId 决定当前成片。
 * 注意：此类型与 ShotGeneration（生成参数，taskType/continuityMode）完全不同名，勿混淆。
 */
export interface ShotGenRecord {
  id: string;
  createdAt: number;
  /** 归档后的视频 input 相对路径（minimax_studio/generations/<project>/<shot>/<gen>.mp4）。
   *  空 = 归档失败，回退播放 outputFilename。 */
  videoFile: string;
  /** 当次输出 mp4 文件名（ComfyUI output 目录 basename，归档失败回退用）。 */
  outputFilename: string;
  /** 当次输出 mp4 的 output 子目录（如 H3 的 "video"）。归档失败回退 /view 时必须带上，
   *  否则 ComfyUI 在 output 根目录找不到文件（H3 输出在 output/video/）。 */
  outputSubfolder?: string;
  /** 当次提交的五区 Prompt 快照（只读；「复制到当前草稿」后才可编辑）。 */
  prompt: {
    visual: string;
    negativePrompt?: string;
    cameraText?: string;
    style?: string;
    soundText?: string;
  };
  /** 当次生成参数快照。 */
  params: {
    taskType: string;
    continuityMode: string;
    width: number;
    height: number;
    frameRate: number;
    workflowId?: string;
    workflowName?: string;
    /** V1.12：本次实际提交的种子（随机模式 = 实际掷出的具体值；一键以此重放）。 */
    seed?: number;
  };
  /** 生成耗时（ms）。 */
  durationMs?: number;
}

/**
 * P0-① 三层可追溯：H3 Prompt 审计快照（导入应用时按最终绑定生成，只读追溯视图）。
 * 结构 = 三层：剧本原文 → AI 理解（DirectorIntent）→ H3 Prompt（三段 + references + 来源标记）。
 * 只作审计/核验，**不参与指纹与生成**（后端生成时独立组装 H3 Prompt），每镜可从
 * userOriginalIntent 逐字追溯到 prompt.integratedDescription。
 */
export interface H3PromptSnapshot {
  /** 第一层：剧本原文（source_text 逐字，未覆盖）。 */
  userOriginalIntent: string;
  /** 第二层：AI 理解（规则抽取原文事实 + Qwen 推断补全；字段级来源标记）。 */
  intent: {
    retainedFacts: string[];
    emotion: string;
    composition: string;
    cameraDesc: string;
    /** 字段级来源标记（user/rule/ai/mix）。 */
    provenance: Record<string, string>;
  };
  /** 第三层：H3 Prompt 三段 + 结构化 references + 段落级来源标记。 */
  prompt: {
    schema: string;
    integratedDescription: string;
    soundscape: string;
    music: string;
    references: { entityKey: string; entityName: string; imageFile: string }[];
    provenance: Record<
      string,
      { userFacts: string[]; aiSupplement: string[]; ruleGenerated: string[] }
    >;
  };
}

/**
 * 镜头（视觉单位）。V1 聚焦 content + refs + generation 的最小集，
 * camera/sound 是 V2 结构化 Prompt 的预留，V1 可留空。
 */
export interface Shot {
  id: string;
  sceneId: string;
  order: number;
  name?: string;
  /** P0 Story Timeline（2026-08-14 用户拍板）：后端 plan 中的 `scene_id:shot_id`
   *  （`{scene_id}:{shot_id}`）映射。后端执行/连续性按该键寻址；前端按 timeline 拍平
   *  时用它把 Episode.timeline 条目映射回本镜（Shot.id 前端唯一，跨场景不碰撞）。 */
  planShotId?: string;
  /** V1.7 Commit 3 剧本导入：Qwen visual_intent 草稿的审核备注。
   *  只作参考，不参与生成（content.visual 由 Phase 3/4 生成）。 */
  description?: string;
  /** V1.7 Phase 3（P0-C）：本镜五区 Prompt 来自「AI 制作计划草稿」。
   *  仅元数据标记，不参与指纹/生成；进工作台后五区可直接编辑。 */
  aiDraft?: boolean;
  /** V1.7 Phase 3（P0-C）：生成该草稿的 Prompt Template Registry 版本（h3-v1）。
   *  持久化供未来 h3-v2 老项目不重新生成。 */
  promptTemplateVersion?: string;
  /** V1.7 Phase 5（P1-D）：本镜 cameraText 来源的运镜模板 id（walk/dialogue/…）。
   *  AI Draft 采纳时记录；用户改模板下拉自动填 cameraText 并更新；手编清空（= 自定义）。
   *  仅元数据标记，不参与指纹/生成。 */
  cameraTemplate?: string;
  /** V1.7 Phase 4（Workbench 审核）：该 AI 草稿是否已被人工采纳。
   *  aiDraft 保留为历史元数据（V17 拍板）；adopted=true 表示已过审核门槛，
   *  之后走普通 Shot 语义（生成/导出/续接无特殊分支）。不参与指纹/状态灯。 */
  adopted?: boolean;
  /** 唯一真相源：时长（秒）。 */
  durationSec: number;
  content: {
    /** 画面描述（主 prompt，用户可见；V2 分区编辑器主输入）。 */
    visual: string;
    negativePrompt?: string;
    /** 摄影分区：景别/运镜/速度/景深 自由文本（V2 分区编辑器）。 */
    cameraText?: string;
    /** 风格分区：光影/色调/美术风格 自由文本（V2 分区编辑器）。 */
    style?: string;
    /** 声音分区：环境音/音乐 自由文本（V2 分区编辑器）。 */
    soundText?: string;
  };
  camera?: ShotCamera;
  sound?: ShotSound;
  refs?: ShotRefs;
  /** @资产绑定（V1.5 完整交互，V1 直接读写）。 */
  /** 单角色兼容字段（V1.6-B 前）；读取时由 normalizeShotInheritance 迁移进 castIds。 */
  castId?: string;
  /** V1.6-B 多角色集合：本镜所有角色（canonical identity 判重：显式 ∪ @命中）。 */
  castIds?: string[];
  /** P0-1 per-shot 资产边界：本镜实际出现的道具/风格参考（剧本导入自动绑定、
   *  Workbench 维护）。只命中这些 id 的场景池 props/styles 才进入本镜生成 refs，
   *  场景素材组本身是「候选池」不是「默认注入集」。 */
  propIds?: string[];
  styleIds?: string[];
  locationId?: string;
  castManual?: boolean;
  locationManual?: boolean;
  generation?: ShotGeneration;
  /** V1.11 生成历史：每一次成功生成记录一条（视频+Prompt+参数快照）。持久化于 project.json。 */
  generations?: ShotGenRecord[];
  /** V1.11 当前成片 generation id（⭐ 设为当前成片）。最新生成 ≠ 当前成片，仅首版自动采纳。 */
  activeGenerationId?: string;
  /** P0-①（#400）：H3 Prompt 审计快照（剧本导入应用时按最终绑定生成；只读追溯视图）。 */
  h3Prompt?: H3PromptSnapshot;
  render?: ShotRender;
}

/**
 * 场景（故事单位 + 世界观一致性管理器）。
 * 镜头自动继承 defaultCastId/defaultLocationId/defaultStyleId。
 */
export interface Scene {
  id: string;
  name: string;
  order: number;
  description?: string;
  location?: string;
  time?: string;
  weather?: string;
  /** 场景参考图（ComfyUI input 相对路径），新镜头自动继承。 */
  referenceImage?: string;
  assets?: SceneAssets;
  defaultCastId?: string;
  defaultLocationId?: string;
  defaultStyleId?: string;
  /** 属于本场景的镜头（按 order 排序）。 */
  shots: Shot[];
}

export interface Episode {
  id: string;
  episodeNumber: number;
  title: string;
  description?: string;
  scenes: Scene[];
  /** P0 Story Timeline（2026-08-14 用户拍板）：本集播放顺序（前端全局 Shot.id，
   *  跨场景唯一；Scene 是空间/资产容器，同一 Scene 可被多次交叉引用）。
   *  缺省/空 = 场景树顺序（scene.order 升序 → shot.order 升序，严格向后兼容）。
   *  拍平 = directorCore.buildTimelineStructure 按本数组展开；镜头增删时需同步维护。 */
  timeline?: string[];
  /** 集级全局资产库（V1.5 完整；V1 可空）。 */
  assets?: SceneAssets;
}

/** TTS 声音类型（与后端 production_plan.VoiceType 对齐）。
 *  character_dialogue=角色对白 / narration=旁白 / inner_monologue=内心独白 / system_voice=系统音。 */
export type VoiceTypeId =
  | "character_dialogue"
  | "narration"
  | "inner_monologue"
  | "system_voice";

/**
 * Voice Cast 全局音色映射（Phase 2，用户 2026-08-16 拍板 #554 ③A：
 * 全局面板 + Dialogue 行级微调，100 章自动继承）。
 *
 * 存于 Project.voiceCast，经 serializeProject 持久化。行级 Dialogue 的
 * voice_id/emotion/delivery 显式值优先于本全局映射（写进 project.plan 各行）。
 */
export interface VoiceCastConfig {
  /** 音频总开关（Phase 2-G，2026-08-17）：false = 音频暂停。
   *  关：每镜 H3 生成完成后**不**自动触发 TTS 合成 + 混音（Edge-TTS 不启动），
   *  只出画面；开：自动配音 + ducking 混音。缺省 false（用户全局暂停音频）。
   *  保留 entries/固定音色配置，随时可重开。 */
  enabled?: boolean;
  /** 角色名（说话人）→ voice_id 稳定映射（跨 100 章角色声音稳定）。
   *  voice_id 为语义 ID（voice_柳如烟 / voice_narrator / zh-CN-* 原生音色名均可）。 */
  entries: Record<string, string>;
  /** 旁白固定音色（语义 ID，默认 voice_narrator → 后端归一到 zh-CN-YunyangNeural）。 */
  narratorVoice: string;
  /** 系统音固定音色（默认 voice_system → zh-CN-YunxiNeural）。 */
  systemVoice: string;
  /** 系统音电子味变体（delivery 含「电子」时启用；Edge-TTS 上云希拒 prosody 会退化）。 */
  systemElectronic: string;
}

export interface Project {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  /** V1 单集；多集管理 V3。 */
  episodes: Episode[];
  /** V1.3-D 参数指纹持久化：shotId → 生成成功时的参数指纹。
   *  重开项目后仍能判「参数已变需重新生成」（st-stale 状态灯），
   *  不再因刷新页面/重载项目丢失指纹。 */
  fingerprints?: Record<string, string>;
  /** P0-A（#478）：剧本导入时的原始 ProductionPlan（source_text 逐字权威源，
   *  scene_id/shot_id 与后端一致）。生成提交时后端 /prompt/h3 用它重建三段式
   *  H3 Prompt（DirectorIntent 从 plan 构建，保证可追溯回原始小说）。
   *  新建/手编项目无此字段 → 提交前由 rebuildPlanFromProject 从 Project 结构重建。 */
  plan?: ProductionPlanJson;
  /** Phase 2（#573）：Voice Cast 全局音色映射。缺省 → normalizeVoiceCast 兜底默认。 */
  voiceCast?: VoiceCastConfig;
  /** v2.0 P3（#631+）：Visual Style 预设（全剧级视觉基底，与后端
   *  VisualStyleProfile.to_dict 对齐）。缺省 → normalizeStyleProfile 兜底默认
   *  「抖音半写实漫剧」；生成提交时透传 /prompt/h3 的 style_profile（画风块注入）。 */
  styleProfile?: VisualStyleProfileJson;
}
