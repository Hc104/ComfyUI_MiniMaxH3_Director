/**
 * ShotPrompt / RenderPlan — 渲染规划产物（Director Core 输出，模型无关）。
 */

export interface ShotPrompt {
  /** 画面描述（可含 @资产提及，由 Prompt Builder 解析后注入）。 */
  visual: string;
  /** 摄影：景别/运镜/速度/景深（V2 结构化；V1 可空）。 */
  camera?: string;
  /** 风格（V2 风格预设；V1 可空）。 */
  style?: string;
  /** 声音：环境音 + 音乐（V2；V1 可空）。 */
  sound?: string;
  negative?: string;
}

export interface RenderPlan {
  mode: "fixed" | "long_edge";
  width: number;
  height: number;
  refMaxSize: number;
  maxExportFrames: number;
  exportMode: "segments" | "scene" | "movie" | "all";
  continuityEnabled: boolean;
  continuityOverlapFrames: number;
  /** 要渲染的镜头 id 列表；null=全部。 */
  targetShotIds: string[] | null;
}
