// @vitest-environment happy-dom
/**
 * V1.2 分镜工作台 UI 冒烟测试：
 *   真实挂载 WorkbenchView，验证 home→workbench 双视图切换、场景/镜头设置面板、
 *   @资产识别高亮、继承徽标、场景/镜头/素材增删交互。
 *
 * runService 替换为假实现（不碰网络/WS）；样例工程从 fixtures 载入。
 * 注意：@资产解析只在 Prompt 里出现「@名字」时触发（与后端命名匹配规则一致）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises, enableAutoUnmount } from "@vue/test-utils";
import { nextTick } from "vue";
import WorkbenchView from "@/workbench/WorkbenchView.vue";
import {
  useWorkbench,
  getCurrentShot,
  getCurrentScene,
  selectScene,
  addShotRefImage,
  loadProject,
  setRunning,
  setError,
  clearSelectedShots,
  setSegStatus,
  updateShotContent,
  addAsset,
  renameProject,
  recordParamHashes,
  addGeneration,
  setActiveGeneration,
  removeGeneration,
  addTask,
} from "@/stores/workbench";
import type { ShotGenRecord, Project, Episode, Scene, Shot, Asset } from "@/models/project";
import { shotFingerprint } from "@/workbench/shotFingerprint";
import { loadSampleProject } from "@/fixtures";
import { WORKFLOW_REGISTRY_KEY, BUILTIN_WORKFLOW_ID } from "@/core/workflowRegistry";
import type { AssetScanResult, ProductionPlanJson } from "@/services/comfyApi";
import { serializeProject, deserializeProject } from "@/services/projectStore";

// Teleport 到 body 的内容不会随组件根卸载自动清理，须显式 enableAutoUnmount
// 保证每个用例结束后组件树（含 Teleport 锚点）被正确卸载，避免跨用例污染。
enableAutoUnmount(afterEach);

/** V1.7 实体抽取加固（#357）+ Phase 1.1：funnel 固定 0 值（纯前端测试不依赖后端统计）。 */
const EMPTY_FUNNEL = {
  discovered: 0, invalid_dropped: 0, dedup_dropped: 0, seeded: 0,
  cleansed: 0, entered: 0, visual_only: 0, excluded: 0, auto: 0, pending: 0, none: 0,
};

const apiMock = {
  listProjects: vi.fn(async () => [] as { id: string; name: string; episodes: number; updatedAt: string }[]),
  listSnapshots: vi.fn(async () => [] as { id: string; name: string; segments: number; scenes: number; time: string }[]),
  loadProject: vi.fn(async (_projectId: string): Promise<Record<string, unknown>> => {
    throw new Error("not used");
  }),
  saveProject: vi.fn(async (_projectId: string, _project: Record<string, unknown>) => undefined),
  createProject: vi.fn(async () => ({ id: "p_fake", name: "未命名项目", episodes: 1, updatedAt: "" })),
  loadSnapshot: vi.fn(async () => {
    throw new Error("not used");
  }),
  deleteProject: vi.fn(async () => undefined),
  deleteSnapshot: vi.fn(async () => undefined),
  assertDirectorNode: vi.fn(async () => undefined),
  uploadFile: vi.fn(async () => ({ name: "a.png", subfolder: "minimax_studio/assets", type: "input" as const })),
  uploadImage: vi.fn(async () => ({ name: "a.png", subfolder: "minimax_studio/assets", type: "input" as const })),
  segmentStatus: vi.fn(
    async (): Promise<{ cached: number[]; states: Record<string, string> }> => ({ cached: [], states: {} }),
  ),
  segmentMp4: vi.fn(
    async () => ({ blob: new Blob(["x"], { type: "video/mp4" }), filename: "Shot02.mp4" }),
  ),
  // Phase 4-C：共享资产库扫描（AssetConfirmDialog 打开时调用）。
  scanAssets: vi.fn(
    async (_plan: ProductionPlanJson): Promise<AssetScanResult> => ({ assets: [], matches: [], funnel: EMPTY_FUNNEL, visual_elements: [] }),
  ),
  // P2-P5（#542）：Bible 面板（左侧栏挂载后 load；示例工程无 bible → null）。
  getBible: vi.fn(async () => ({ bible: null })),
  bibleConfirm: vi.fn(async () => ({ bible: null })),
  bibleEntry: vi.fn(async () => ({ created: undefined, bible: null })),
};
const runServiceMock = {
  api: apiMock,
  connect: vi.fn(async () => undefined),
  run: vi.fn(async (_structure: unknown, _workflow: unknown, _nodeId: string, callbacks: unknown) => {
    // 模拟一次完整生成：默认立即触发 onFinish(ok) → 任务进入 done 态。
    // 单个用例可用 mockImplementationOnce 覆盖（如失败态/onVideo）。
    (callbacks as { onFinish?: (pid: string, ok: boolean) => void }).onFinish?.("prompt_fake", true);
    return "prompt_fake";
  }),
  pollSegmentStatus: vi.fn(async () => apiMock.segmentStatus()),
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear(); // V1.8-2 工作流注册表持久化 → 每用例重置，避免跨用例污染
  const st = useWorkbench() as unknown as { runService: unknown };
  st.runService = runServiceMock;
  // segmentStatus 默认无缓存；单个用例内可用 mockResolvedValue 覆盖。
  apiMock.segmentStatus.mockResolvedValue({ cached: [], states: {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
  // 弹窗经 Teleport 到 body，测试间清理避免残留影响后续用例
  document.body.innerHTML = "";
});

async function mountWorkbench() {
  const wrapper = mount(WorkbenchView);
  await flushPromises();
  return wrapper;
}

/** 通过 home 底部「开发工具 → 载入示例工程」进入工作台视图。 */
async function enterWorkbench(wrapper: Awaited<ReturnType<typeof mountWorkbench>>) {
  const loadBtn = wrapper.findAll(".dev-row button").find((b) => b.text().includes("载入示例工程"));
  expect(loadBtn, "应能找到「载入示例工程」按钮").toBeTruthy();
  await loadBtn!.trigger("click");
  await flushPromises();
}

/** 全片拍平镜头列表（与生成链 flattenedShots 同序）。 */
function flatShots() {
  const ep = useWorkbench().project!.episodes[0];
  return ep.scenes.flatMap((sc) => sc.shots.slice().sort((a, b) => a.order - b.order));
}

/** V1.11 生成版本记录工厂（测试注入版本条用）。 */
function makeGen(id: string, opts: Partial<ShotGenRecord> = {}): ShotGenRecord {
  return {
    id,
    createdAt: Date.now(),
    videoFile: `minimax_studio/generations/proj/shot/${id}.mp4`,
    outputFilename: `${id}.mp4`,
    prompt: {
      visual: `视觉${id}`,
      negativePrompt: `负面${id}`,
      cameraText: `运镜${id}`,
      style: `风格${id}`,
      soundText: `声音${id}`,
    },
    params: { taskType: "r2v", continuityMode: "auto", width: 1280, height: 720, frameRate: 24, workflowId: "wf1", workflowName: "内置" },
    durationMs: 5000,
    ...opts,
  };
}

/** 点击生成模式 pill（走查/成片）—— radio 的 checked 由 DOM 设置 + change 事件触发 v-model。 */
async function checkMode(wrapper: Awaited<ReturnType<typeof mountWorkbench>>, mode: "walkthrough" | "final") {
  const radio = wrapper.find(`.gen-mode-pill input[value="${mode}"]`);
  expect(radio.exists(), `应能找到 ${mode} 模式 radio`).toBe(true);
  (radio.element as HTMLInputElement).checked = true;
  await radio.trigger("change");
  await flushPromises();
}

describe("V1.2 分镜工作台 UI", () => {
  it("home 视图渲染：品牌/最近项目/新建项目/开发工具（未进工作台）", async () => {
    const wrapper = await mountWorkbench();
    expect(wrapper.text()).toContain("MiniMax Studio");
    expect(wrapper.text()).toContain("最近项目");
    expect(wrapper.text()).toContain("新建项目");
    expect(wrapper.text()).toContain("开发工具");
    expect(wrapper.find(".wb-head").exists()).toBe(false);
  });

  it("载入示例工程 → 工作台：顶栏 + 场景树 + 3 张镜头卡片", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    expect(wrapper.find(".wb-head").exists()).toBe(true);
    expect(wrapper.text()).toContain("霓虹街区");
    expect(wrapper.text()).toContain("雨夜登场");
    expect(wrapper.text()).toContain("数据塔");
    expect(wrapper.findAll(".shot-card")).toHaveLength(3);
  });

  it("点击地点名 → 地点设置面板（默认人物/地点/风格 + 素材库两级池）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".scene-name")[0].trigger("click");
    expect(wrapper.text()).toContain("地点设置");
    expect(wrapper.text()).toContain("镜头默认值");
    expect(wrapper.text()).toContain("素材库");
    expect(wrapper.text()).toContain("默认人物");
    expect(wrapper.text()).toContain("林雪");
    // V1.5-P0-A：素材库两级切换（场景素材 / 全局资产）
    expect(wrapper.text()).toContain("📍 地点素材");
    expect(wrapper.text()).toContain("🌐 全局资产");
  });

  it("V1.5-P0-A 素材库两级切换：场景素材 ↔ 全局资产，同名去重不重复", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".scene-name")[0].trigger("click");
    await flushPromises();
    // 默认场景素材（sc1 霓虹街区池）：林雪 + 霓虹街区，无数据塔
    const sceneNames = wrapper.findAll(".lib-card-name").map((w) => w.text());
    expect(sceneNames).toContain("林雪");
    expect(sceneNames).toContain("霓虹街区");
    expect(sceneNames).not.toContain("数据塔");
    // 切到全局资产 → 数据塔补进、林雪仍只有一条（场景池不去全局池，此处全局池本身就 1 条）
    await wrapper.findAll(".lib-scope-btn")[1].trigger("click");
    await flushPromises();
    const globalNames = wrapper.findAll(".lib-card-name").map((w) => w.text());
    expect(globalNames).toContain("数据塔");
    expect(globalNames).toContain("霓虹街区");
    expect(globalNames.filter((n) => n === "林雪")).toHaveLength(1);
  });

  it("V1.5-P0-A 全局资产「复制到本场景」→ 场景池新增一次性快照副本", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".scene-name")[0].trigger("click");
    await wrapper.findAll(".lib-scope-btn")[1].trigger("click"); // 切到全局资产
    await flushPromises();
    const cards = wrapper.findAll(".lib-card");
    const towerCard = cards.find((c) => c.find(".lib-card-name").text() === "数据塔");
    expect(towerCard, "全局池应有「数据塔」卡片").toBeTruthy();
    const copyBtn = towerCard!.find(".lib-card-ops .icon-btn[title='复制到本地点']");
    expect(copyBtn, "全局资产卡片应有「复制到本地点」按钮").toBeTruthy();
    await copyBtn!.trigger("click");
    await flushPromises();
    // 复制后自动切回场景素材 scope，sc1 场景池出现「数据塔」副本（新 id）
    const sc1 = getCurrentScene()!;
    expect(sc1.assets!.locations.map((l) => l.name)).toContain("数据塔");
    expect(sc1.assets!.locations).toHaveLength(2);
    // 全局池条目不受影响（一次性快照，仍是独立 id）
    const ep = useWorkbench().project!.episodes[0];
    expect(ep.assets!.locations.map((l) => l.name)).toContain("数据塔");
    expect(ep.assets!.locations[0].id).not.toBe(sc1.assets!.locations.find((l) => l.name === "数据塔")!.id);
  });

  it("点击镜头卡片 → 镜头设置 + 继承徽标（人物=镜头指定 / 地点=场景默认）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    // 样例镜头 s1：人物显式指定，地点未指定 → 场景默认 loc_neon
    await wrapper.findAll(".shot-card")[0].trigger("click");
    expect(wrapper.text()).toContain("镜头设置");
    expect(wrapper.text()).toContain("▶ 生成此镜");
    expect(wrapper.text()).toContain("生效：林雪");
    expect(wrapper.text()).toContain("⚡ 镜头指定");
    expect(wrapper.text()).toContain("生效：霓虹街区");
    expect(wrapper.text()).toContain("↑ 地点默认");
  });

  it("V1.6-B 人物 chip：同名角色（场景副本+全局原件）只显示一次、选中后同名立即排除（杜绝重复添加）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const sc = getCurrentScene()!;
    // 场景池再建一个同名「林雪」（新 id），模拟场景/全局两个副本并存
    addAsset("cast", "林雪", { scope: "scene", sceneId: sc.id, imageFile: "lx_scene.png" });
    await wrapper.findAll(".shot-card")[0].trigger("click");
    await flushPromises();
    // 先移除样例已显式选的林雪（→ 自动继承，picker 才出现同名选项）
    await wrapper.find(".cast-chip-x").trigger("click");
    await flushPromises();
    expect(wrapper.find(".cast-chip").exists()).toBe(false);
    expect(wrapper.text()).toContain("✨ 自动继承");
    // 打开 picker：canonical 去重后「林雪」只列一次（场景池优先）
    await wrapper.find(".cast-add-btn").trigger("click");
    let pickerNames = wrapper.findAll(".cast-picker-item").map((b) => b.text());
    expect(pickerNames.filter((n) => n.includes("林雪")).length).toBe(1);
    // 选中它 → 只出现一个 chip
    await wrapper.findAll(".cast-picker-item")[0].trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".cast-chip")).toHaveLength(1);
    expect(getCurrentShot()!.castIds!.length).toBe(1);
    // 再打开 picker：同名「林雪」已被排除（无论场景副本还是全局原件）
    await wrapper.find(".cast-add-btn").trigger("click");
    pickerNames = wrapper.findAll(".cast-picker-item").map((b) => b.text());
    expect(pickerNames.filter((n) => n.includes("林雪"))).toHaveLength(0);
  });

  it("V1.6-B 人物 chip：点 chip 主体不删除，点 × 才删除；addCast 同名替换不重复", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    await flushPromises();
    const shot = getCurrentShot()!;
    expect(shot.castManual).toBe(true);
    // 点 chip 主体（名字区）不应触发删除
    await wrapper.find(".cast-chip-name").trigger("click");
    await flushPromises();
    expect(getCurrentShot()!.castManual).toBe(true);
    // 点 × 才删除
    await wrapper.find(".cast-chip-x").trigger("click");
    await flushPromises();
    expect(getCurrentShot()!.castManual).toBe(false);
    expect(wrapper.find(".cast-chip").exists()).toBe(false);
  });

  it("V1.6-B 顶栏主标题=项目名（重命名后同步显示），副标题=集标题", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    expect(wrapper.find(".wb-project-name").text()).toBe("赛博都市");
    expect(wrapper.find(".wb-project-ep").text()).toBe("霓虹之夜");
    renameProject("第七号站台");
    await flushPromises();
    expect(wrapper.find(".wb-project-name").text()).toBe("第七号站台");
    expect(wrapper.find(".wb-project").text()).toContain("霓虹之夜");
  });

  it("P0-2/P0-1 生效资产=本镜实际引用；场景池素材只进「场景素材组」折叠区", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    // 注入场景池道具 + 全局池道具/风格（scope 明确 → 各写各池，不污染样例 fixture）
    const sc = getCurrentScene()!;
    addAsset("prop", "黑伞", { scope: "scene", sceneId: sc.id, imageFile: "p1.png" });
    addAsset("prop", "摩托", { scope: "episode" });
    addAsset("style", "霓虹赛博", { scope: "episode" });
    await wrapper.findAll(".shot-card")[0].trigger("click");
    await flushPromises();
    const text = wrapper.text();
    // 单选类（样例 s1：人物显式 / 地点场景默认）
    expect(text).toContain("生效：林雪");
    expect(text).toContain("⚡ 镜头指定");
    expect(text).toContain("生效：霓虹街区");
    expect(text).toContain("↑ 地点默认");
    // P0-1/P0-2 边界（铁律①）：场景池/全局池道具·风格绝不做本镜默认 refs →
    // 生效资产区没有 黑伞/摩托/霓虹赛博（它们只存在于「场景素材组」候选池）。
    expect(text).not.toContain("生效：黑伞");
    expect(text).not.toContain("生效：摩托");
    expect(text).not.toContain("生效：霓虹赛博");
    // 生效资产行数 = 人物 + 地点 = 2 行（未绑定道具/风格不占行）。
    expect(wrapper.findAll(".inherit-row")).toHaveLength(2);
    // 场景池素材进独立折叠区「场景素材组」：黑伞（场景池道具）可见为候选素材。
    expect(text).toContain("地点素材组");
    // 点击头部展开折叠区，再断言黑伞在场景素材组候选池（生效资产区之外）。
    await wrapper.find(".scenemat-head").trigger("click");
    await flushPromises();
    const scenematNames = wrapper.findAll(".scenemat-grid .inherit-asset").map((w) => w.text());
    expect(scenematNames.some((n) => n.includes("黑伞"))).toBe(true);
    // 生效资产区（非 scenemat）不得出现黑伞。
    const expireNames = wrapper.findAll(".inherit-grid:not(.scenemat-grid) .inherit-asset").map((w) => w.text());
    expect(expireNames.some((n) => n.includes("黑伞"))).toBe(false);
  });

  it("V1.5-P1 无道具/风格资产时生效资产区只列人物+地点", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    expect(wrapper.findAll(".inherit-row")).toHaveLength(2);
    expect(wrapper.findAll(".inherit-kind").map((w) => w.text())).toEqual(["人物", "地点"]);
  });

  it("@ 输入实时识别资产：mark 高亮 + 命中 chips", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    const ta = wrapper.find(".prompt-input");
    await ta.setValue("@林雪走进@霓虹街区");
    await flushPromises();
    // 2 个命中 chip：林雪(cast) + 霓虹街区(location)
    expect(wrapper.findAll(".mention-chip")).toHaveLength(2);
    const layerHtml = wrapper.find(".prompt-layer").html();
    expect(layerHtml).toContain('class="media-chip mc-asset m-cast"');
    expect(layerHtml).toContain('class="media-chip mc-asset m-loc"');
    // @匹配优先级高于场景默认 → 地点徽标变「镜头指定」
    expect(wrapper.text()).toContain("生效：霓虹街区");
  });

  it("媒体标签 chip 在 Prompt 框内渲染缩略图小图标", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    const ta = wrapper.find(".prompt-input");
    // 有图资产 → 缩略图 img
    await ta.setValue("林雪走进<picture>林雪</picture>的街道");
    await flushPromises();
    const layerHtml = wrapper.find(".prompt-layer").html();
    expect(layerHtml).toContain('class="media-chip mc-picture"');
    expect(layerHtml).toContain('class="media-chip-thumb"');
    expect(layerHtml).toContain("filename=linxue.png");
    // 无图资产 → emoji 占位图标
    await ta.setValue("手持<picture>能量剑</picture>");
    await flushPromises();
    const layerHtml2 = wrapper.find(".prompt-layer").html();
    expect(layerHtml2).toContain('class="media-chip mc-picture"');
    expect(layerHtml2).toContain('class="media-chip-icon"');
    expect(layerHtml2).not.toContain("media-chip-thumb");
  });

  it("媒体 chip 导演台风格：缩略图 + label 文字（同 minimax_prompt_mentions.js）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    const ta = wrapper.find(".prompt-input");
    await ta.setValue("林雪走进<picture>林雪</picture>的街道");
    await flushPromises();
    const layerHtml = wrapper.find(".prompt-layer").html();
    // chip = ghost（隐形原文撑宽，保光标对齐）+ visual（缩略图 + label）
    expect(layerHtml).toContain('class="media-chip mc-picture"');
    expect(layerHtml).toContain("media-chip-ghost");
    expect(layerHtml).toContain("media-chip-visual");
    expect(layerHtml).toContain("media-chip-thumb");
    expect(layerHtml).toContain("filename=linxue.png");
    expect(layerHtml).toContain("media-chip-label");
    expect(layerHtml).toContain(">林雪<");
    // ghost 内是原始标签文本（转义后）——宽度 == textarea 原文 → 光标不漂移
    expect(layerHtml).toContain("&lt;picture&gt;林雪&lt;/picture&gt;");
  });

  it("Prompt 放大弹窗：点 ⛶ 放大 → 屏幕居中大编辑框（Teleport body），输入同步写回，关闭复原", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    // 初始无弹窗（弹窗经 Teleport 渲染到 body，不在组件树内）
    expect(document.body.querySelector(".prompt-modal")).toBeNull();
    await wrapper.find(".prompt-zoom-btn").trigger("click");
    await flushPromises();
    const modalEl = document.body.querySelector(".prompt-modal");
    expect(modalEl, "应能在 body 找到放大弹窗").toBeTruthy();
    expect(modalEl!.textContent).toContain("提示词放大编辑");
    // 弹窗 textarea 输入同步写回当前镜头
    const ta = modalEl!.querySelector("textarea") as HTMLTextAreaElement;
    ta.value = "弹窗输入@林雪";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    await flushPromises();
    expect(getCurrentShot()!.content.visual).toBe("弹窗输入@林雪");
    // 弹窗内高亮层渲染 @ 资产 chip
    const modalLayer = modalEl!.querySelector(".prompt-layer") as HTMLElement;
    expect(modalLayer.innerHTML).toContain('class="media-chip mc-asset m-cast"');
    // 关闭 → 弹窗消失
    const closeBtn = modalEl!.querySelector(".prompt-modal-close") as HTMLElement;
    closeBtn.click();
    await flushPromises();
    expect(document.body.querySelector(".prompt-modal")).toBeNull();
  });

  it("官方编号标签 <Picture 1> 在 Prompt 框渲染 chip（V1.2.7 #66 回归）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    const ta = wrapper.find(".prompt-input");
    await ta.setValue("林雪走进<Picture 1>");
    await flushPromises();
    const layerHtml = wrapper.find(".prompt-layer").html();
    // chip 必须被渲染（无论有无缩略图），而不是纯文本
    expect(layerHtml).toContain('class="media-chip mc-picture"');
    expect(layerHtml).toContain("Picture 1");
  });

  it("放大弹窗内 @ 输入：下拉只在弹窗显示（completionTarget 分离，卡片不重复渲染）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    // 卡片 Prompt 框输入 @ → 卡片下拉出现，弹窗不存在
    const cardTaEl = wrapper.find(".prompt-input").element as HTMLTextAreaElement;
    cardTaEl.value = "@林雪";
    cardTaEl.setSelectionRange(3, 3);
    cardTaEl.dispatchEvent(new Event("input", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find(".wb-right .prompt-box .tag-complete").exists()).toBe(true);
    expect(document.body.querySelector(".prompt-modal .tag-complete")).toBeNull();
    // 打开放大弹窗
    await wrapper.find(".prompt-zoom-btn").trigger("click");
    await flushPromises();
    const modalEl = document.body.querySelector(".prompt-modal") as HTMLElement;
    expect(modalEl, "应能在 body 找到放大弹窗").toBeTruthy();
    // 弹窗内输入 @ → 下拉只出现在弹窗，卡片下拉消失
    const modalTa = modalEl!.querySelector("textarea") as HTMLTextAreaElement;
    modalTa.value = "@林雪";
    modalTa.setSelectionRange(3, 3);
    modalTa.dispatchEvent(new Event("input", { bubbles: true }));
    await flushPromises();
    expect(modalEl!.querySelector(".tag-complete")).toBeTruthy();
    expect(wrapper.find(".wb-right .prompt-box .tag-complete").exists()).toBe(false);
    // 关闭弹窗 → 下拉复位，卡片可再次弹出
    const closeBtn = modalEl!.querySelector(".prompt-modal-close") as HTMLElement;
    closeBtn.click();
    await flushPromises();
    cardTaEl.value = "@林雪";
    cardTaEl.setSelectionRange(3, 3);
    cardTaEl.dispatchEvent(new Event("input", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find(".wb-right .prompt-box .tag-complete").exists()).toBe(true);
  });

  it("@ 选参考素材 → 插入官方编号标签 <Picture N>，不显示文件名", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    // 给当前镜头挂一张参考图（编号 = 已有 refImages 数 + 1）
    const shot = getCurrentShot()!;
    const beforeLen = (shot.refs?.refImages ?? []).length;
    addShotRefImage(shot.id, "minimax_studio/assets/tail.png");
    const expectedTag = `<Picture ${beforeLen + 1}>`;
    const taEl = wrapper.find(".prompt-input").element as HTMLTextAreaElement;
    taEl.value = "@tail";
    taEl.setSelectionRange(5, 5);
    taEl.dispatchEvent(new Event("input", { bubbles: true }));
    await flushPromises();
    // 候选下拉出现，包含 ref 源参考图（分组标题「参考图」+ 候选 tail.png）
    const groups = wrapper.findAll(".wb-right .prompt-box .tag-group-label");
    expect(groups.map((g) => g.text())).toContain("参考图");
    const items = wrapper.findAll(".wb-right .prompt-box .tag-item");
    expect(items.length).toBeGreaterThan(0);
    const refItem = items.find((b) => b.text().includes("tail.png"));
    expect(refItem, "应能看到参考图候选 tail.png").toBeTruthy();
    // 点击 ref 候选 → 插入官方编号标签，不含文件名
    await refItem!.trigger("mousedown");
    await flushPromises();
    const visual = getCurrentShot()!.content.visual;
    expect(visual).toBe(expectedTag);
    expect(visual).not.toContain("tail.png");
    // 高亮层把编号标签渲染成 chip（导演台形式：缩略图 + Picture N label）
    const layerHtml = wrapper.find(".prompt-layer").html();
    expect(layerHtml).toContain('class="media-chip mc-picture"');
    expect(layerHtml).toContain(expectedTag.slice(1, -1)); // "Picture 2"
  });

  it("点 @命中 chip → 绑定到镜头字段（locationManual=true）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    const ta = wrapper.find(".prompt-input");
    await ta.setValue("@林雪走进@霓虹街区");
    await flushPromises();
    const chips = wrapper.findAll(".mention-chip");
    const locChip = chips.find((c) => c.text().includes("霓虹街区"));
    expect(locChip, "应能找到地点命中 chip").toBeTruthy();
    await locChip!.trigger("click");
    const shot = getCurrentShot()!;
    expect(shot.locationId).toBe("loc_neon");
    expect(shot.locationManual).toBe(true);
  });

  it("场景行 ＋镜头 → 镜头数 +1，新镜头不预写默认值、生效区走场景默认（Q3 #98）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const before = wrapper.findAll(".shot-card").length;
    const addShotBtn = wrapper.findAll(".scene-name .icon-btn").find((b) => b.attributes("title") === "新增镜头");
    expect(addShotBtn, "应能找到「新增镜头」按钮").toBeTruthy();
    await addShotBtn!.trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".shot-card").length).toBe(before + 1);
    const shot = getCurrentShot()!;
    // Q3 修复（#98）：不再预写场景默认值，继承链在渲染时动态解析。
    // 否则 resolveOne 的 defaultId===explicitId 会把「场景默认」误判成「⚡镜头指定」。
    expect(shot.castId).toBeUndefined();
    expect(shot.castManual).toBeUndefined();
    // 渲染层：新镜头生效区仍显示场景默认人物（↑ 场景默认）
    await wrapper.findAll(".shot-card")[before].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("生效：林雪");
    expect(wrapper.text()).toContain("↑ 地点默认");
  });

  it("＋ 地点 → 地点数 +1 并进入地点设置面板", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const before = wrapper.findAll(".scene-block").length;
    await wrapper.find(".add-scene").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".scene-block").length).toBe(before + 1);
    expect(wrapper.text()).toContain("地点设置");
    const sc = getCurrentScene()!;
    expect(sc.shots).toHaveLength(0);
  });

  it("场景面板新建素材（道具）→ 自动展开编辑表单 → 保存 → 素材库卡片出现", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".scene-name")[0].trigger("click");
    vi.stubGlobal("prompt", vi.fn().mockReturnValue("新道具"));
    await wrapper.findAll(".asset-group-head .icon-btn")[2].trigger("click"); // 道具组（cast/location/prop/style）
    await flushPromises();
    // V1.5-P0-A：新建后自动进入内联编辑表单（名称/描述/别名/参考图）
    expect(wrapper.find(".lib-edit").exists(), "新建后应自动展开编辑表单").toBe(true);
    // 保存 → 卡片显示新资产
    await wrapper.find(".lib-edit-actions .btn.small.primary").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("新道具");
    expect(getCurrentScene()!.assets!.props.map((p) => p.name)).toContain("新道具");
  });

  it("场景面板编辑字段 → 写回 store（时间/天气）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".scene-name")[0].trigger("click");
    // 时间输入框
    const timeInput = wrapper.find('label.fld input[placeholder="夜晚"]');
    await timeInput.setValue("清晨");
    await timeInput.trigger("change");
    expect(getCurrentScene()!.time).toBe("清晨");
  });
});

describe("V1.3 生成范围（6 态下拉）", () => {
  beforeEach(() => {
    // store 是模块级单例，前一用例的 running/project/多选会残留污染：
    // 每个用例重置回干净样例工程 + 空闲运行态。
    loadProject(loadSampleProject());
    setRunning(false);
    clearSelectedShots();
    setSegStatus([], [], {});
  });

  it("头部范围下拉含 7 选项 + 主按钮显示当前范围与计数", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const sel = wrapper.find(".gen-scope");
    expect(sel.exists()).toBe(true);
    const opts = sel.findAll("option").map((o) => o.attributes("value"));
    expect(opts).toEqual(["all", "current", "selected", "scene", "pending", "failed", "fromShot"]);
    // 默认「全部镜头」：计数 = 3 个镜头
    expect(wrapper.find(".gen-ctl .btn.primary").text()).toContain("全部镜头");
    expect(wrapper.find(".gen-ctl .btn.primary").text()).toContain("（3）");
  });

  it("切「当前镜头」→ 生成 → run 收到 runSelection=[当前镜头 id]", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    await wrapper.find(".gen-scope").setValue("current");
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).toHaveBeenCalledTimes(1);
    const structure = runServiceMock.run.mock.calls[0][0] as { runSelection: string[] | null };
    expect(structure.runSelection).toEqual([getCurrentShot()!.id]);
  });

  it("多选镜头 → 「选中镜头」范围 → 逐镜串行：每镜单独提交 runSelection=[该镜]", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const boxes = wrapper.findAll(".shot-sel");
    await boxes[0].trigger("click");
    await boxes[2].trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".shot-card.sel")).toHaveLength(2);
    await wrapper.find(".gen-scope").setValue("selected");
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    // 走查模式（默认）：每镜单独提交，run 调用 2 次，各带 1 镜
    expect(runServiceMock.run).toHaveBeenCalledTimes(2);
    const c0 = runServiceMock.run.mock.calls[0][0] as { runSelection: string[] | null };
    const c1 = runServiceMock.run.mock.calls[1][0] as { runSelection: string[] | null };
    expect(c0.runSelection).toHaveLength(1);
    expect(c1.runSelection).toHaveLength(1);
    const shots = flatShots();
    expect(c0.runSelection![0]).toBe(shots[0].id);
    expect(c1.runSelection![0]).toBe(shots[2].id);
  });

  it("状态灯：segment_status cached=[0] → 第 1 张卡片 st-done，其余 st-none", async () => {
    apiMock.segmentStatus.mockResolvedValue({ cached: [0], states: {} });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    const statuses = wrapper.findAll(".shot-card .shot-status");
    expect(statuses[0].classes()).toContain("st-done");
    expect(statuses[1].classes()).toContain("st-none");
    expect(statuses[2].classes()).toContain("st-none");
  });

  it("「未完成」范围：跳过已缓存镜头，只逐镜生成未缓存镜头", async () => {
    apiMock.segmentStatus.mockResolvedValue({ cached: [0], states: {} });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await wrapper.find(".gen-scope").setValue("pending");
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    // 逐镜串行：2 个未缓存镜头 → 2 次单独提交
    expect(runServiceMock.run).toHaveBeenCalledTimes(2);
    const c0 = runServiceMock.run.mock.calls[0][0] as { runSelection: string[] | null };
    const c1 = runServiceMock.run.mock.calls[1][0] as { runSelection: string[] | null };
    // 段 0（第一个镜头）已缓存 → 不含当前镜头（getCurrentShot 返回第一个镜头）
    expect(c0.runSelection).not.toContain(getCurrentShot()!.id);
    expect(c0.runSelection).toHaveLength(1);
    expect(c1.runSelection).toHaveLength(1);
  });

  it("「失败」范围：states 标 failed 的镜头才进入 runSelection", async () => {
    apiMock.segmentStatus.mockResolvedValue({ cached: [], states: { "1": "failed" } });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await wrapper.find(".gen-scope").setValue("failed");
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    const structure = runServiceMock.run.mock.calls[0][0] as { runSelection: string[] | null };
    expect(structure.runSelection).toHaveLength(1);
    // 段 1 = 第二个镜头
    expect(structure.runSelection![0]).toBe(useWorkbench().project!.episodes[0].scenes[0].shots[1].id);
  });
});

describe("V1.5 走查 #104 走查/成片双模式", () => {
  beforeEach(() => {
    loadProject(loadSampleProject());
    setRunning(false);
    clearSelectedShots();
    setSegStatus([], [], {});
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
    useWorkbench().paramHashes = {};
  });

  it("生成栏含「模式」切换（默认走查 pill 高亮 + 成片 pill 未激活）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const pills = wrapper.findAll(".gen-mode-pill");
    expect(pills.length, "生成栏应有走查/成片两个模式 pill").toBe(2);
    const walk = pills.find((p) => p.text().includes("走查"))!;
    const fin = pills.find((p) => p.text().includes("成片"))!;
    expect(walk.classes(), "走查模式默认激活").toContain("active");
    expect(fin.classes(), "成片模式默认未激活").not.toContain("active");
  });

  it("全部镜头（走查模式默认）→ 逐镜串行：run 按序提交 3 次，每镜 exportMode=segments + runSelection=[单镜]", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).toHaveBeenCalledTimes(3);
    const shots = flatShots();
    for (let i = 0; i < 3; i++) {
      const s = runServiceMock.run.mock.calls[i][0] as { runSelection: string[] | null; output: { exportMode: string } };
      expect(s.runSelection, `第 ${i + 1} 次提交应为单镜`).toEqual([shots[i].id]);
      expect(s.output.exportMode, "走查每镜独立分镜导出").toBe("segments");
    }
    const st = useWorkbench();
    expect(st.tasks[0].status).toBe("done");
    expect(st.tasks[0].shotCount).toBe(3);
    // 逐镜成功 → 每镜指纹固化
    expect(st.paramHashes[shots[0].id]).toBeTruthy();
    expect(st.paramHashes[shots[2].id]).toBeTruthy();
    // 任务中心面板能看到任务条目
    await wrapper.find(".tc-summary").trigger("click");
    await flushPromises();
    expect(wrapper.find(".tc-panel").text()).toContain("3 镜");
  });

  it("走查串行失败：某镜失败 → 任务 failed + 失败镜头归位，后续不再提交", async () => {
    // 第 1 次提交成功（镜头 1），第 2 次提交失败（镜头 2）→ 停止，第 3 镜不跑
    runServiceMock.run
      .mockImplementationOnce(async (_s, _w, _n, c) => {
        (c as { onFinish?: (pid: string, ok: boolean) => void }).onFinish?.("p1", true);
        return "p1";
      })
      .mockImplementationOnce(async (_s, _w, _n, c) => {
        (c as { onFinish?: (pid: string, ok: boolean) => void }).onFinish?.("p2", false);
        return "p2";
      });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    const st = useWorkbench();
    expect(st.tasks[0].status).toBe("failed");
    const shots = flatShots();
    expect(st.tasks[0].failedShotIds).toEqual([shots[1].id]);
    expect(runServiceMock.run).toHaveBeenCalledTimes(2);
  });

  it("切到成片模式 → 多镜范围单次提交 exportMode=all（不做逐镜）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await checkMode(wrapper, "final");
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).toHaveBeenCalledTimes(1);
    const s = runServiceMock.run.mock.calls[0][0] as { runSelection: string[] | null; output: { exportMode: string } };
    expect(s.output.exportMode, "成片模式多镜整片合并").toBe("all");
    const st = useWorkbench();
    expect(st.tasks[0].shotStates, "非走查任务无 shotStates").toBeNull();
  });

  it("成片模式下单镜范围仍是 segments（不合并）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await checkMode(wrapper, "final");
    await wrapper.find(".gen-scope").setValue("current");
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).toHaveBeenCalledTimes(1);
    const s = runServiceMock.run.mock.calls[0][0] as { output: { exportMode: string } };
    expect(s.output.exportMode, "单镜无论何模式都走 segments").toBe("segments");
  });

  it("从此镜继续：当前镜头 + 后续全部逐镜串行（走查续拍）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const shots = flatShots();
    // 当前镜头切到第 2 镜（回眸）→ 从此镜继续 = s2 + s3
    useWorkbench().currentShotId = shots[1].id;
    await wrapper.find(".gen-scope").setValue("fromShot");
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).toHaveBeenCalledTimes(2);
    const c0 = runServiceMock.run.mock.calls[0][0] as { runSelection: string[] | null };
    const c1 = runServiceMock.run.mock.calls[1][0] as { runSelection: string[] | null };
    expect(c0.runSelection).toEqual([shots[1].id]);
    expect(c1.runSelection).toEqual([shots[2].id]);
  });

  it("走查队列：任务记录 shotStates 齐全 + 任务中心面板渲染每镜状态", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    const st = useWorkbench();
    const shots = flatShots();
    expect(st.tasks[0].shotStates).toBeTruthy();
    expect(Object.keys(st.tasks[0].shotStates!)).toEqual([shots[0].id, shots[1].id, shots[2].id]);
    expect(Object.values(st.tasks[0].shotStates!)).toEqual(["done", "done", "done"]);
    // 面板渲染走查队列
    await wrapper.find(".tc-summary").trigger("click");
    await flushPromises();
    const panelText = wrapper.find(".tc-panel").text();
    expect(panelText).toContain("走查队列");
    expect(panelText).toContain("✓");
  });
});

describe("V1.3-D 参数指纹持久化（#282 刷新/重开项目后仍判「参数已变」）", () => {
  beforeEach(() => {
    loadProject(loadSampleProject());
    setRunning(false);
    clearSelectedShots();
    setSegStatus([], [], {});
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
  });

  it("loadProject 从项目文件 fingerprints 恢复 paramHashes（重开项目不丢基线）", () => {
    const project = loadSampleProject();
    const shot = project.episodes[0].scenes[0].shots[0];
    // 模拟磁盘上的旧项目文件：已记录过镜头 1 生成成功时的指纹
    project.fingerprints = { [shot.id]: "fp_shot1" };
    loadProject(project);
    expect(useWorkbench().paramHashes[shot.id], "恢复后的 paramHashes 应含镜头 1 指纹").toBe("fp_shot1");
  });

  it("recordParamHashes 增量写回项目模型 fingerprints（保存时落盘）", () => {
    loadProject(loadSampleProject());
    const project = useWorkbench().project!;
    const shots = project.episodes[0].scenes[0].shots;
    recordParamHashes({ [shots[0].id]: "fp_a" });
    recordParamHashes({ [shots[1].id]: "fp_b" });
    expect(project.fingerprints![shots[0].id], "镜头 1 指纹已写回").toBe("fp_a");
    expect(project.fingerprints![shots[1].id], "镜头 2 指纹已写回").toBe("fp_b");
  });

  it("重开项目：指纹与当前参数一致 → st-done；改 Prompt → st-stale 橙色（#282 回归）", async () => {
    const project = loadSampleProject();
    const shot = project.episodes[0].scenes[0].shots[0];
    // 模拟上次生成成功时固化的指纹（与当前参数一致 → 重开后初始不 stale）
    project.fingerprints = {
      [shot.id]: shotFingerprint(shot, { outputSize: { width: 864, height: 480 }, frameRate: 24 }),
    };
    apiMock.listProjects.mockResolvedValue([{ id: "p1", name: "重开项目", episodes: 1, updatedAt: "" }]);
    apiMock.loadProject.mockResolvedValue(JSON.parse(JSON.stringify(project)) as Record<string, unknown>);
    apiMock.segmentStatus.mockResolvedValue({ cached: [0], states: {} });
    const wrapper = await mountWorkbench();
    await wrapper.find(".proj-card").trigger("click");
    await flushPromises();
    // 已进入工作台；指纹已恢复且参数未变 → 镜头 1 st-done（不误报橙）
    expect(wrapper.find(".wb-head").exists()).toBe(true);
    const statuses = wrapper.findAll(".shot-card .shot-status");
    expect(statuses[0].classes(), "参数未变应 st-done").toContain("st-done");
    expect(statuses[0].classes()).not.toContain("st-stale");
    // 修改 Prompt → 指纹变化 → 状态灯变橙 st-stale
    updateShotContent(shot.id, "全新提示词：列车驶过，车窗倒映赛博霓虹");
    await flushPromises();
    const stale = wrapper.findAll(".shot-card .shot-status");
    expect(stale[0].classes(), "修改参数后应 st-stale").toContain("st-stale");
    expect(stale[0].attributes("title")).toContain("参数已变");
  });
});

describe("V1.3 任务中心", () => {
  beforeEach(() => {
    loadProject(loadSampleProject());
    setRunning(false);
    clearSelectedShots();
    setSegStatus([], [], {});
    // tasks 也是模块级单例，前一 describe 的生成用例会残留任务 → 清空
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
  });

  it("空闲态：底部任务中心摘要 + 展开面板空态提示", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    expect(wrapper.find(".tc-summary").exists()).toBe(true);
    expect(wrapper.find(".tc-summary").text()).toContain("任务中心");
    expect(wrapper.find(".tc-summary").text()).toContain("空闲");
    await wrapper.find(".tc-summary").trigger("click");
    await flushPromises();
    expect(wrapper.find(".tc-panel").exists()).toBe(true);
    expect(wrapper.find(".tc-panel").text()).toContain("▶ 生成");
  });

  it("生成一次（全部镜头）→ 任务记录 running→done，含范围摘要与镜头数", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    const st = useWorkbench();
    expect(st.tasks).toHaveLength(1);
    expect(st.tasks[0].scopeLabel).toContain("全部镜头");
    expect(st.tasks[0].shotCount).toBe(3);
    // run mock 默认立即触发 onFinish(true) → done + promptId
    expect(st.tasks[0].status).toBe("done");
    expect(st.tasks[0].promptId).toBe("prompt_fake");
    // 展开面板能看到任务条目
    await wrapper.find(".tc-summary").trigger("click");
    await flushPromises();
    expect(wrapper.find(".tc-panel").text()).toContain("全部镜头");
    expect(wrapper.find(".tc-panel").text()).toContain("3 镜");
  });

  it("失败任务：mock onFinish(false) → status failed + error 提示", async () => {
    runServiceMock.run.mockImplementationOnce(async (_s, _w, _n, c) => {
      (c as { onFinish?: (pid: string, ok: boolean) => void }).onFinish?.("prompt_fake", false);
      return "prompt_fake";
    });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    const st = useWorkbench();
    expect(st.tasks[0].status).toBe("failed");
    expect(st.tasks[0].error).toContain("生成失败");
  });

  it("「打开成片」：任务 finalVideo 喂入中央播放器", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    runServiceMock.run.mockImplementationOnce(async (_s, _w, _n, c) => {
      const cb = c as {
        onVideo?: (v: { url: string; ref: { filename: string } }) => void;
        onFinish?: (pid: string, ok: boolean) => void;
      };
      cb.onVideo?.({ url: "/view?filename=a.mp4", ref: { filename: "Shot01.mp4" } });
      cb.onFinish?.("prompt_fake", true);
      return "prompt_fake";
    });
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    await wrapper.find(".tc-summary").trigger("click");
    await flushPromises();
    const openBtn = wrapper.findAll(".tc-btn").find((b) => b.text().includes("打开成片"));
    expect(openBtn, "应出现「打开成片」按钮").toBeTruthy();
    await openBtn!.trigger("click");
    await flushPromises();
    expect(useWorkbench().finalVideo?.filename).toBe("Shot01.mp4");
  });

  it("单镜生成：提交 exportMode=segments，播放器直接播该镜（不拼全片）", async () => {
    runServiceMock.run.mockImplementationOnce(async (s, _w, _n, c) => {
      const structure = s as { output?: { exportMode?: string } };
      // 单镜范围必须走分镜导出，后端 SaveVideo 才只输出这一镜。
      expect(structure.output?.exportMode, "单镜生成应走分镜导出").toBe("segments");
      const cb = c as {
        onVideo?: (v: { url: string; ref: { filename: string } }) => void;
        onFinish?: (pid: string, ok: boolean) => void;
      };
      cb.onVideo?.({ url: "/view?filename=shot02.mp4", ref: { filename: "shot02.mp4" } });
      cb.onFinish?.("prompt_fake", true);
      return "prompt_fake";
    });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const miniGen = wrapper.find('button.shot-gen-mini[title="只生成此镜"]');
    expect(miniGen.exists(), "应有单镜生成按钮").toBe(true);
    await miniGen.trigger("click");
    await flushPromises();
    const st = useWorkbench();
    // onVideo 直接播单段（不再依赖 segment_mp4 缓存，也无全片回退路径）。
    expect(st.finalVideo?.filename, "播放器应播本次生成的单段而非全片").toBe("shot02.mp4");
    expect(st.tasks[0]?.finalVideo?.filename, "任务记录同一单段视频").toBe("shot02.mp4");
    expect(apiMock.segmentMp4, "不再依赖 segment_mp4 缓存").not.toHaveBeenCalled();
  });

  it("A2 生成成功后自动保存：版本记录落盘，无需手动点 💾（2026-08-14）", async () => {
    runServiceMock.run.mockImplementationOnce(async (_s, _w, _n, c) => {
      const cb = c as {
        onVideo?: (v: { url: string; ref: { filename: string } }) => void;
        onFinish?: (pid: string, ok: boolean) => void;
      };
      cb.onVideo?.({ url: "/view?filename=shot01.mp4", ref: { filename: "shot01.mp4" } });
      cb.onFinish?.("prompt_fake", true);
      return "prompt_fake";
    });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const firstShotId = flatShots()[0].id;
    const savesBefore = apiMock.saveProject.mock.calls.length;

    await wrapper.find('button.shot-gen-mini[title="只生成此镜"]').trigger("click");
    await flushPromises();

    // ① 生成成功 → 自动触发 saveProject（用户未点 💾）
    expect(apiMock.saveProject.mock.calls.length, "A2 生成成功应自动保存项目").toBeGreaterThan(savesBefore);

    // ② 落盘 payload 含该镜版本记录 + current_generation_id（首版自动采纳）
    const payload = apiMock.saveProject.mock.calls[apiMock.saveProject.mock.calls.length - 1][1] as {
      episodes: { scenes: { shots: { id: string; generations?: { id: string }[]; activeGenerationId?: string }[] }[] }[];
    };
    const savedShot = payload.episodes[0].scenes.flatMap((sc) => sc.shots).find((sh) => sh.id === firstShotId)!;
    expect(savedShot.generations, "保存 payload 应含本次生成版本").toHaveLength(1);
    expect(savedShot.activeGenerationId, "首版自动采纳为当前成片").toBe(savedShot.generations![0].id);

    // ③ 重载（刷新/重开项目）后版本仍在 → 生成记录不再依赖手动保存
    loadProject(deserializeProject(serializeProject(useWorkbench().project!)));
    await flushPromises();
    const reloaded = flatShots().find((x) => x.id === firstShotId)!;
    expect(reloaded.generations, "重载后生成版本保留").toHaveLength(1);
    expect(reloaded.activeGenerationId, "重载后 current_generation_id 保留").toBe(savedShot.generations![0].id);
  });

  it("卡片下载按钮：无缓存禁用，segStatus 缓存后可用", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const shots = useWorkbench().project!.episodes[0].scenes.flatMap((sc) => sc.shots.map((s) => s.id));
    setSegStatus(shots, [], {});
    await flushPromises();
    expect((wrapper.findAll(".shot-dl")[0].element as HTMLButtonElement).disabled).toBe(true);
    setSegStatus(shots, [0], {});
    await flushPromises();
    expect((wrapper.findAll(".shot-dl")[0].element as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("V1.3 参数指纹（参数已变需重生成）", () => {
  beforeEach(() => {
    loadProject(loadSampleProject());
    setRunning(false);
    clearSelectedShots();
    setSegStatus([], [], {});
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
    useWorkbench().paramHashes = {};
  });

  it("生成成功 → 记录指纹；修改 Prompt 后 cached 镜头状态灯变 st-stale", async () => {
    apiMock.segmentStatus.mockResolvedValue({ cached: [0], states: {} });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    // 生成全部 3 镜 → onFinish(true) 记录 3 镜指纹
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    const w = useWorkbench();
    const firstShotId = w.project!.episodes[0].scenes[0].shots[0].id;
    expect(w.paramHashes[firstShotId], "应记录生成时指纹").toBeTruthy();
    // 修改第一个镜头 prompt → 指纹变化 → 状态灯 st-stale
    updateShotContent(firstShotId, "全新提示词");
    await flushPromises();
    const statuses = wrapper.findAll(".shot-card .shot-status");
    expect(statuses[0].classes()).toContain("st-stale");
    expect(statuses[0].attributes("title")).toContain("参数已变");
  });

  it("未生成过的 cached 镜头（无指纹）→ 仍显示 st-done 不误报", async () => {
    // paramHashes 为空，cached=[0] → 段 0 有缓存但无指纹记录 → st-done
    apiMock.segmentStatus.mockResolvedValue({ cached: [0], states: {} });
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    const statuses = wrapper.findAll(".shot-card .shot-status");
    expect(statuses[0].classes()).toContain("st-done");
    expect(statuses[0].classes()).not.toContain("st-stale");
  });
});

describe("V2 分区编辑器（Prompt 更多分区折叠）", () => {
  it("默认折叠：仅见头部，分区与预览不可见", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    expect(wrapper.find(".prompt-sections-head").exists()).toBe(true);
    expect(wrapper.find(".prompt-sections-body").exists()).toBe(false);
  });

  it("展开 → 4 分区；编辑摄影/风格写回 store，合并预览实时更新（中文逗号）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    await wrapper.find(".prompt-sections-head").trigger("click");
    await flushPromises();
    const textareas = wrapper.findAll(".ps-sec textarea");
    expect(textareas).toHaveLength(4); // 摄影 / 风格 / 声音 / 负面
    await textareas[0].setValue("近景，缓慢推近");
    await textareas[1].setValue("赛博朋克夜色");
    await flushPromises();
    // store 写回
    const w = useWorkbench();
    const s1 = w.project!.episodes[0].scenes[0].shots[0];
    expect(s1.content.cameraText).toBe("近景，缓慢推近");
    expect(s1.content.style).toBe("赛博朋克夜色");
    // 合并预览 = 画面 + 摄影 + 风格，按中文逗号拼接
    const preview = wrapper.find(".ps-preview-text").text();
    expect(preview).toContain("林雪（雨衣");
    expect(preview).toMatch(/近景，缓慢推近，赛博朋克夜色/);
  });

  it("负面 Prompt 迁入折叠区后仍可编辑写回", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await wrapper.findAll(".shot-card")[0].trigger("click");
    await wrapper.find(".prompt-sections-head").trigger("click");
    const neg = wrapper.findAll(".ps-sec textarea")[3];
    await neg.setValue("text, logo, watermark, 模糊");
    await flushPromises();
    const w = useWorkbench();
    expect(w.project!.episodes[0].scenes[0].shots[0].content.negativePrompt).toBe(
      "text, logo, watermark, 模糊",
    );
  });
});

describe("左侧分镜操作区（下载按钮可点性回归）", () => {
  it("下载按钮是卡片直接子元素，独立于 hover 浮层（浮层不挤占常显按钮布局）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const card = wrapper.findAll(".shot-card")[0];
    const dl = card.find(".shot-dl");
    expect(dl.exists(), "下载按钮应存在于卡片").toBe(true);
    // 下载按钮直接挂在卡片下（不在 .shot-ops 内），hover 浮层不会包裹/覆盖它
    expect(dl.element.parentElement?.className).toContain("shot-card");
    // 浮层按钮带文字标签 → 更大点击目标
    const opsText = card.find(".shot-ops").text();
    for (const label of ["上移", "下移", "复制", "重命名", "删除"]) {
      expect(opsText).toContain(label);
    }
  });

  it("浮层操作可用：点「删除」镜头数 -1", async () => {
    vi.stubGlobal("confirm", () => true);
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const before = wrapper.findAll(".shot-card").length;
    const delBtn = wrapper
      .findAll(".shot-ops .icon-btn")
      .find((b) => b.text().includes("删除"));
    expect(delBtn, "应能找到删除镜头按钮").toBeTruthy();
    await delBtn!.trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".shot-card")).toHaveLength(before - 1);
  });
});

describe("生成中可播放已生成镜头（#83 回归）", () => {
  beforeEach(() => {
    loadProject(loadSampleProject());
    setRunning(true); // 模拟正在生成另一镜
    clearSelectedShots();
    apiMock.segmentStatus.mockResolvedValue({ cached: [0, 2], states: {} });
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
    useWorkbench().paramHashes = {};
  });

  it("生成中：已缓存镜头「播放此镜」可用；无缓存镜头仍禁用", async () => {
    const shots = useWorkbench().project!.episodes[0].scenes.flatMap((sc) => sc.shots.map((s) => s.id));
    setSegStatus(shots, [0, 2], {});
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    const playBtns = wrapper.findAll('button.shot-gen-mini[title="播放此镜"]');
    expect(playBtns).toHaveLength(3);
    // 段 0、段 2 有缓存 → 可播；段 1 无缓存 → 禁用（不因 wb.running 一刀切禁播）。
    expect((playBtns[0].element as HTMLButtonElement).disabled).toBe(false);
    expect((playBtns[2].element as HTMLButtonElement).disabled).toBe(false);
    expect((playBtns[1].element as HTMLButtonElement).disabled).toBe(true);
  });

  it("生成中：点已缓存镜头「播放此镜」→ segment_mp4 被调用（入口不被 wb.running 拦）", async () => {
    const shots = useWorkbench().project!.episodes[0].scenes.flatMap((sc) => sc.shots.map((s) => s.id));
    setSegStatus(shots, [0, 2], {});
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    const playBtns = wrapper.findAll('button.shot-gen-mini[title="播放此镜"]');
    await playBtns[2].trigger("click");
    await flushPromises();
    expect(apiMock.segmentMp4, "生成中播放已生成镜头应走 segment_mp4").toHaveBeenCalled();
    const w = useWorkbench();
    expect(w.finalVideo?.filename, "播放器应显示该镜 mp4").toBe("Shot02.mp4");
  });
});

describe("V1.4-P0-3 退出保护（仅 dirty 拦截，最小版）", () => {
  beforeEach(() => {
    loadProject(loadSampleProject());
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
    useWorkbench().paramHashes = {};
  });

  /** 从工作台顶栏触发「← 项目」。 */
  async function clickGoHome(wrapper: Awaited<ReturnType<typeof mountWorkbench>>) {
    const nav = wrapper.find("button.btn.nav[title='返回项目列表']");
    expect(nav.exists(), "应能找到「← 项目」按钮").toBe(true);
    await nav.trigger("click");
    await flushPromises();
  }

  it("dirty=true → beforeunload 拦截（preventDefault 生效）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    updateShotContent("s1", "未保存的修改");
    expect(useWorkbench().dirty).toBe(true);
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented, "dirty 时浏览器原生「离开此网站？」应被触发").toBe(true);
  });

  it("dirty=false → beforeunload 不拦截（正常离开）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    expect(useWorkbench().dirty).toBe(false);
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented, "clean 时不拦截浏览器刷新/关闭").toBe(false);
  });

  it("dirty=false 点 ← 项目 → 直接回项目页（不弹确认框）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await clickGoHome(wrapper);
    expect(document.body.querySelector(".leave-modal"), "clean 时不应弹出退出确认框").toBeNull();
    expect(useWorkbench().project, "返回项目页应清空当前项目").toBeNull();
    expect(wrapper.text()).toContain("最近项目");
  });

  it("dirty=true 点 ← 项目 → 弹出三选一确认框，内容正确", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    updateShotContent("s1", "x");
    await clickGoHome(wrapper);
    const modal = document.body.querySelector(".leave-modal");
    expect(modal, "dirty 时点 ← 项目应弹出确认框").toBeTruthy();
    expect(document.body.querySelector(".leave-modal-title")?.textContent).toContain("有未保存的修改");
    expect(document.body.querySelector(".leave-modal-body")?.textContent).toContain("离开后这些修改将丢失");
    const actions = Array.from(document.body.querySelectorAll(".leave-modal-actions .btn")).map((b) => b.textContent);
    expect(actions).toEqual(["保存并离开", "不保存离开", "取消"]);
  });

  it("保存并离开 → saveProject 被调用，保存成功后才清理回项目页", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    updateShotContent("s1", "x");
    await clickGoHome(wrapper);
    (document.body.querySelector(".leave-modal .leave-save") as HTMLButtonElement).click();
    await flushPromises();
    expect(apiMock.saveProject, "保存并离开应调用后端保存").toHaveBeenCalled();
    const w = useWorkbench();
    expect(w.dirty).toBe(false);
    expect(w.project, "保存成功后清理项目回项目页").toBeNull();
    expect(wrapper.text()).toContain("最近项目");
  });

  it("保存失败 → 留在当前页面，不清理、不离开", async () => {
    apiMock.saveProject.mockRejectedValueOnce(new Error("boom"));
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    updateShotContent("s1", "x");
    await clickGoHome(wrapper);
    (document.body.querySelector(".leave-modal .leave-save") as HTMLButtonElement).click();
    await flushPromises();
    const w = useWorkbench();
    expect(w.dirty, "保存失败 dirty 保持").toBe(true);
    expect(w.project, "保存失败不清项目").not.toBeNull();
    expect(document.body.querySelector(".leave-modal"), "保存失败留在当前页面（确认框仍在）").toBeTruthy();
    expect(wrapper.text()).toContain("保存失败");
  });

  it("不保存离开 → 正常清理回项目页", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    updateShotContent("s1", "x");
    await clickGoHome(wrapper);
    (document.body.querySelector(".leave-modal .leave-discard") as HTMLButtonElement).click();
    await flushPromises();
    const w = useWorkbench();
    expect(w.dirty).toBe(false);
    expect(w.project).toBeNull();
    expect(wrapper.text()).toContain("最近项目");
  });

  it("取消 → 状态完全不变（dirty 保持、留在工作台、确认框关闭）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    updateShotContent("s1", "x");
    await clickGoHome(wrapper);
    (document.body.querySelector(".leave-modal .leave-cancel") as HTMLButtonElement).click();
    await flushPromises();
    expect(document.body.querySelector(".leave-modal"), "取消后确认框应关闭").toBeNull();
    expect(useWorkbench().dirty, "取消后 dirty 状态不变").toBe(true);
    expect(useWorkbench().project, "取消后项目不清空").not.toBeNull();
    expect(wrapper.text()).toContain("雨夜登场");
    expect(wrapper.text()).not.toContain("最近项目");
  });
});

describe("V1.4-P1 WS 状态视觉（连接指示器三/四态）", () => {
  it("初始未连接：灰点 + 「未连接」", async () => {
    useWorkbench().wsStatus = "disconnected";
    const wrapper = await mountWorkbench();
    const conn = wrapper.find(".home-conn");
    expect(conn.text()).toContain("未连接");
    const dot = conn.find(".dot");
    expect(dot.classes()).not.toContain("on");
    expect(dot.classes()).not.toContain("reconn");
  });

  it("已连接：绿点(on) + 「已连接 ComfyUI」", async () => {
    useWorkbench().wsStatus = "connected";
    const wrapper = await mountWorkbench();
    const conn = wrapper.find(".home-conn");
    expect(conn.text()).toContain("已连接 ComfyUI");
    expect(conn.find(".dot").classes()).toContain("on");
  });

  it("断线重连：橙点(reconn) + 「连接断开 · 正在重连」", async () => {
    useWorkbench().wsStatus = "reconnecting";
    const wrapper = await mountWorkbench();
    const conn = wrapper.find(".home-conn");
    expect(conn.text()).toContain("连接断开 · 正在重连");
    expect(conn.find(".dot").classes()).toContain("reconn");
  });

  it("已恢复：绿点(on+pulse) + 「已恢复」", async () => {
    useWorkbench().wsStatus = "recovered";
    const wrapper = await mountWorkbench();
    const conn = wrapper.find(".home-conn");
    expect(conn.text()).toContain("已恢复");
    const dot = conn.find(".dot");
    expect(dot.classes()).toContain("on");
    expect(dot.classes()).toContain("pulse");
  });

  it("工作台顶栏 wb-conn：连接状态跟随 store（已连接）", async () => {
    useWorkbench().wsStatus = "connected";
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const conn = wrapper.find(".wb-conn");
    expect(conn.text()).toContain("已连接");
    expect(conn.find(".dot").classes()).toContain("on");
  });

  it("工作台顶栏 wb-conn：断线重连状态透传", async () => {
    useWorkbench().wsStatus = "reconnecting";
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const conn = wrapper.find(".wb-conn");
    expect(conn.text()).toContain("连接断开 · 正在重连");
    expect(conn.find(".dot").classes()).toContain("reconn");
  });
});

describe("V1.6-C 项目/集命名（inline）", () => {
  it("首页卡片 inline 改名：✎ → 输入 → ✓ → saveProject(name 更新) + 本地列表刷新", async () => {
    apiMock.listProjects.mockResolvedValue([{ id: "p1", name: "旧名", episodes: 1, updatedAt: "2026-01-01" }]);
    apiMock.loadProject.mockResolvedValue({ id: "p1", name: "旧名", createdAt: "", updatedAt: "", episodes: [] });
    const wrapper = await mountWorkbench();
    // 点击 ✎ 进入改名态
    await wrapper.find(".proj-card .rename-btn").trigger("click");
    await flushPromises();
    expect(wrapper.find(".proj-rename-input").exists()).toBe(true);
    // 输入新名 → 点 ✓ 确认
    await wrapper.find(".proj-rename-input").setValue("新名");
    await wrapper.find(".rename-ok").trigger("click");
    await flushPromises();
    // saveProject 以新名字被调用（load→save 往返）
    const saves = apiMock.saveProject.mock.calls;
    expect(saves.length).toBeGreaterThan(0);
    const last = saves[saves.length - 1];
    expect(last[0]).toBe("p1");
    expect((last[1] as { name: string }).name).toBe("新名");
    // 本地列表同步显示新名，编辑态退出
    expect(wrapper.find(".proj-name").text()).toContain("新名");
    expect(wrapper.find(".proj-rename-input").exists()).toBe(false);
  });

  it("工作台顶栏集标题 inline 改名：✎ → 输入 → Enter → store 写回 + dirty", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    // 顶栏集标题进入改名态
    await wrapper.find(".wb-project .rename-btn").trigger("click");
    await flushPromises();
    expect(wrapper.find(".ep-title-input").exists()).toBe(true);
    // 输入新标题 → Enter 确认
    await wrapper.find(".ep-title-input").setValue("第二集");
    await wrapper.find(".ep-title-input").trigger("keydown.enter");
    await flushPromises();
    const st = useWorkbench();
    expect(st.project!.episodes[0].title).toBe("第二集");
    expect(st.dirty).toBe(true);
    // 编辑态退出，顶栏显示新标题
    expect(wrapper.find(".ep-title-input").exists()).toBe(false);
    expect(wrapper.find(".wb-project").text()).toContain("第二集");
  });
});

describe("V1.7 Phase 4：AI 制作计划审核态 UI", () => {
  beforeEach(() => {
    // 前面生成类测试可能把 running 残留为 true（store 单例），
    // 复位保证审核态 UI 不受「生成中」禁用影响。
    setRunning(false);
    setError(null);
  });

  it("无 AI 草稿 → 不显示审核横幅，卡片无 AI 徽标", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    expect(wrapper.find(".review-banner").exists()).toBe(false);
    expect(wrapper.find(".shot-draft-badge").exists()).toBe(false);
    expect(wrapper.find(".shot-draft-adopt").exists()).toBe(false);
  });

  it("待审核草稿 → 横幅统计 + 卡片 ⚠/✓ 徽标 + 逐镜采纳（采纳后横幅消失）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const w = useWorkbench();
    const s1 = w.project!.episodes[0].scenes[0].shots[0];
    // 示例工程 shot0：castId=cast_linxue 有效、visual 有值、cameraText 空 → ⚠（运镜未设置）
    s1.aiDraft = true;
    await flushPromises();
    // 横幅统计
    expect(wrapper.find(".review-banner").exists()).toBe(true);
    expect(wrapper.find(".review-banner-count").text()).toContain("1 镜待审核");
    expect(wrapper.find(".review-banner-stat").text()).toContain("0/1 镜就绪");
    // 卡片徽标：待审核 ⚠ + 采纳按钮
    const badge = wrapper.find(".shot-draft-badge");
    expect(badge.exists()).toBe(true);
    expect(badge.classes()).toContain("notready");
    expect(badge.text()).toContain("⚠");
    expect(wrapper.find(".shot-draft-adopt").exists()).toBe(true);
    // 补运镜 → 就绪 ✓（三项判定全过）
    s1.content.cameraText = "中景跟移，随林雪移动";
    await flushPromises();
    expect(wrapper.find(".review-banner-stat").text()).toContain("1/1 镜就绪");
    expect(wrapper.find(".shot-draft-badge").classes()).toContain("ready");
    expect(wrapper.find(".shot-draft-badge").text()).toContain("✓");
    // 逐镜采纳 → adopted=true，徽标变已采纳，横幅消失
    await wrapper.find(".shot-draft-adopt").trigger("click");
    await flushPromises();
    expect(s1.adopted).toBe(true);
    expect(wrapper.find(".review-banner").exists()).toBe(false);
    expect(wrapper.find(".shot-draft-badge").text()).toContain("已采纳");
    // P2-⑧ 已采纳降权：采纳按钮消失，只留小徽标（完成态不作主要视觉元素）
    expect(wrapper.find(".shot-draft-adopt").exists()).toBe(false);
  });

  it("生成硬拦截：未采纳草稿 → 生成按钮警示态 + 点击不触发生成 + 提示；采纳后放行", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const w = useWorkbench();
    const s1 = w.project!.episodes[0].scenes[0].shots[0];
    s1.aiDraft = true;
    await flushPromises();
    // 生成按钮警示态（可点）
    const genBtn = wrapper.find(".gen-ctl .btn.primary");
    expect(genBtn.classes()).toContain("review-gate");
    expect(genBtn.text()).toContain("待采纳");
    // 点击 → 拦截：不触发生成，store 记错误提示
    await genBtn.trigger("click");
    await flushPromises();
    expect(runServiceMock.run).not.toHaveBeenCalled();
    expect(w.lastError).toContain("请先采纳 AI 草稿");
    // 采纳后 → 警示态解除（可正常生成）
    s1.adopted = true;
    await flushPromises();
    expect(wrapper.find(".gen-ctl .btn.primary").classes()).not.toContain("review-gate");
  });

  it("采纳全部：两镜草稿一键采纳 → 全部 adopted + 横幅消失", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const w = useWorkbench();
    const shots = w.project!.episodes[0].scenes[0].shots;
    shots[0].aiDraft = true;
    shots[1].aiDraft = true;
    await flushPromises();
    expect(wrapper.find(".review-banner-count").text()).toContain("2 镜待审核");
    await wrapper.find(".review-adopt-all").trigger("click");
    await flushPromises();
    expect(shots[0].adopted).toBe(true);
    expect(shots[1].adopted).toBe(true);
    expect(wrapper.find(".review-banner").exists()).toBe(false);
  });

  it("Phase 4-C：角色未绑定 → 徽标可点开补资产弹窗 → 选中候选确认 → castIds 写回真实资产 id + 徽标转就绪 + 弹窗关闭", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const w = useWorkbench();
    const s1 = w.project!.episodes[0].scenes[0].shots[0];
    // 制造 asset 缺口：castIds 指向不存在的 id → 「角色「ghost_hero」未绑定资产」。
    s1.aiDraft = true;
    s1.castIds = ["ghost_hero"];
    s1.content.cameraText = "中景跟移";
    // 共享资产库命中 ghost_hero → 候选「林雪」（自动选中）。
    apiMock.scanAssets.mockResolvedValue({
      assets: [{ name: "林雪", image_file: "minimax_studio/assets/林雪.png" }],
      matches: [
        {
          kind: "cast",
          name: "ghost_hero",
          status: "auto",
          matched: true,
          asset_name: "林雪",
          image_file: "minimax_studio/assets/林雪.png",
          confidence: 1,
          candidates: [{ name: "林雪", image_file: "minimax_studio/assets/林雪.png", score: 1 }],
          entity_type: "character",
          entity_source: "script",
          entity_confidence: 0.95,
          entity_id: "ent_001",
          can_auto: true,
        } as import("@/services/comfyApi").AssetMatch,
      ],
      funnel: EMPTY_FUNNEL,
      visual_elements: [],
    });
    await flushPromises();

    // 徽标 notready + 点击打开弹窗（复用 /assets/scan）。
    const badge = wrapper.find(".shot-draft-badge");
    expect(badge.classes()).toContain("notready");
    expect(badge.attributes("title") ?? "").toContain("点此补资产");
    await badge.trigger("click");
    await flushPromises();
    expect(wrapper.find(".ac-panel").exists()).toBe(true);
    expect(wrapper.find(".ac-item-name").text()).toContain("ghost_hero");
    // 自动候选已选 → 确认按钮可用 → 点击写回。
    expect(wrapper.find(".ac-foot .primary").attributes("disabled")).toBeUndefined();
    await wrapper.find(".ac-foot .primary").trigger("click");
    await flushPromises();

    // castIds 更新为真实资产 id（复用池中同名「林雪」→ cast_linxue，不新建）。
    expect(s1.castIds).toContain("cast_linxue");
    expect(s1.castIds).not.toContain("ghost_hero");
    // 徽标转就绪 + 弹窗关闭。
    expect(wrapper.find(".shot-draft-badge").classes()).toContain("ready");
    expect(wrapper.find(".ac-panel").exists()).toBe(false);
  });

  it("Phase 4-C：无资产缺口（仅运镜缺口）→ 点击徽标不打开弹窗", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const w = useWorkbench();
    const s1 = w.project!.episodes[0].scenes[0].shots[0];
    s1.aiDraft = true; // cast_linxue 有效、visual 有值，仅 cameraText 空 → 纯运镜缺口
    await flushPromises();
    const badge = wrapper.find(".shot-draft-badge");
    expect(badge.classes()).toContain("notready");
    expect(badge.attributes("title") ?? "").not.toContain("点此补资产");
    await badge.trigger("click");
    await flushPromises();
    expect(wrapper.find(".ac-panel").exists()).toBe(false);
  });
});

describe("V1.8-1 Shot 视频 + Prompt 联动详情区", () => {
  it("详情区随当前镜头联动：剧本原文 / Prompt 五区 / 生成参数速览", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    // 注入剧本原文 + 五区数据（示例工程默认只带 visual/negativePrompt）
    const w = useWorkbench();
    const s1 = w.project!.episodes[0].scenes[0].shots[0];
    s1.description = "剧本原文测试：林雪走进霓虹街区。";
    s1.content.cameraText = "缓慢推近，环绕镜头";
    s1.content.style = "赛博朋克，冷蓝霓虹";
    s1.content.soundText = "雨声，远处警笛";
    await flushPromises();

    // 选中该镜（左栏卡片）
    const card = wrapper.findAll(".shot-card").find((c) => c.text().includes(s1.name ?? s1.id));
    await card!.trigger("click");
    await flushPromises();

    const detail = wrapper.find(".shot-detail");
    expect(detail.exists(), "当前镜头应有详情区").toBe(true);
    // 剧本原文
    expect(detail.text()).toContain("剧本原文测试");
    // Prompt 五区
    expect(detail.text()).toContain("画面描述");
    expect(detail.text()).toContain(s1.content.visual.slice(0, 10));
    expect(detail.text()).toContain("摄影");
    expect(detail.text()).toContain("缓慢推近");
    expect(detail.text()).toContain("风格");
    expect(detail.text()).toContain("赛博朋克");
    expect(detail.text()).toContain("声音");
    expect(detail.text()).toContain("雨声");
    expect(detail.text()).toContain("负面");
    expect(detail.text()).toContain(s1.content.negativePrompt!.slice(0, 10));
    // 生成参数速览
    expect(detail.text()).toContain("R2V 参考生视频");
    expect(detail.text()).toContain("分辨率");
    expect(detail.text()).toContain("帧率");
  });

  it("底部 Storyboard 时间线：全片播放顺序编号 + 时长 + 状态灯；点击联动详情区", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    const cards = wrapper.findAll(".tl-card");
    expect(cards.length).toBeGreaterThan(0);
    const first = cards[0];
    // P0-7：编号 = 播放顺序位置（1 起，非拍平 S 前缀）
    expect(first.find(".tl-thumb-idx").text()).toBe("1");
    expect(first.find(".tl-scene-bar").exists()).toBe(true);
    expect(first.find(".tl-status").exists()).toBe(true);
    expect(first.find(".tl-meta").exists()).toBe(true);
    // 点击最后一个卡片 → 详情区标题切为该镜
    const flat = flatShots();
    const lastShot = flat[cards.length - 1];
    await cards[cards.length - 1].trigger("click");
    await flushPromises();
    const title = wrapper.find(".sd-title");
    expect(title.exists()).toBe(true);
    expect(title.text()).toContain(lastShot.name ?? lastShot.id);
  });

  it("未生成镜头：详情区不拉单镜视频；点击卡片不触发主播放器", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    apiMock.segmentMp4.mockClear();
    const cards = wrapper.findAll(".tl-card");
    await cards[0].trigger("click");
    await flushPromises();
    // 默认 segmentStatus 无缓存 → 详情区不请求 segmentMp4
    expect(apiMock.segmentMp4).not.toHaveBeenCalled();
  });

  it("生成成功镜头：segStatus 刷新不再拉详情区视频（V1.11.1 砍详情播放器，segmentMp4 不调用）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    apiMock.segmentMp4.mockClear();
    // 模拟后端段状态：第 0 镜成功缓存 → watch(segStatus) 触发缩略图预热
    // （happy-dom canvas 无 2d 上下文，ensureThumb/ensureGenThumb 静默跳过 → 不发任何 segmentMp4）
    const shots = flatShots();
    setSegStatus(shots.map((s) => s.id), [0], { "0": "success" });
    await flushPromises();
    expect(apiMock.segmentMp4).not.toHaveBeenCalled();
  });
});

describe("V1.11.1 UI 收敛（唯一播放器 + 纯版本条 + 信息卡 + Cinema Mode）", () => {
  /** 给首个镜头加 N 个版本记录并选中该镜（版本条渲染前置条件）。 */
  async function seedGens(wrapper: Awaited<ReturnType<typeof mountWorkbench>>, ids: string[]): Promise<ReturnType<typeof flatShots>[number]> {
    const s = flatShots()[0];
    ids.forEach((id) => addGeneration(s.id, makeGen(id)));
    await flushPromises();
    const card = wrapper.findAll(".shot-card").find((c) => c.text().includes(s.name ?? s.id));
    expect(card, "应能找到首个镜头卡").toBeTruthy();
    await card!.trigger("click");
    await flushPromises();
    return s;
  }

  it("版本条是纯图片缩略图（无 <video>）；卡片含 #N / 时长 / ⭐|🆕 徽标", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await seedGens(wrapper, ["g1", "g2"]);

    const strip = wrapper.find(".gen-strip");
    expect(strip.exists()).toBe(true);
    expect(strip.text()).toContain("生成版本 · 2 个");
    const chips = wrapper.findAll(".gen-chip");
    expect(chips.length).toBe(2);
    // 版本条是「选版本」区：缩略图是 <img> 或占位，绝无第二个 <video> 播放器
    expect(wrapper.find(".gen-chip video").exists()).toBe(false);
    expect(chips[0].find(".gen-chip-idx").text()).toBe("#1");
    expect(chips[1].find(".gen-chip-idx").text()).toBe("#2");
    // 时长（happy-dom 无 canvas → 回退镜头设定时长）
    expect(chips[0].find(".gen-dur").text()).toMatch(/s$/);
    // 徽标：首版自动采纳 → ⭐ 当前；末位 → 🆕 最新
    expect(chips[0].find(".gb-adopted").exists()).toBe(true);
    expect(chips[1].find(".gb-newest").exists()).toBe(true);
    // 版本卡保持极简：不塞 Prompt/参数（那是信息卡的职责）
    expect(chips[0].text()).not.toContain("视觉g1");
  });

  it("点击版本卡：主播放器切到该版本（唯一播放器「看视频」，版本条「选版本」）", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await seedGens(wrapper, ["g1", "g2"]);

    const chips = wrapper.findAll(".gen-chip");
    await chips[1].trigger("click"); // 点 #2
    await flushPromises();
    expect(wrapper.find(".player-video").attributes("src") ?? "").toContain("g2.mp4");
    expect(wrapper.find(".player-video-meta").text()).toContain("Generation #2");

    await chips[0].trigger("click"); // 再点 #1
    await flushPromises();
    expect(wrapper.find(".player-video").attributes("src") ?? "").toContain("g1.mp4");
    expect(wrapper.find(".player-video-meta").text()).toContain("Generation #1");
    expect(wrapper.find(".player-video-meta").text()).toContain("⭐ 当前成片");
  });

  it("Cinema Mode：唯一 [⛶] → 全屏版本圆点切换 → ⭐ 设为当前成片 → ✕ 关闭", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const s = await seedGens(wrapper, ["g1", "g2"]);

    // 先点版本 #2 → 主播放器有视频 → 顶栏 [⛶] 才可用
    const chips = wrapper.findAll(".gen-chip");
    await chips[1].trigger("click");
    await flushPromises();
    expect(wrapper.find(".ph-cinema").attributes("disabled")).toBeUndefined();
    await wrapper.find(".ph-cinema").trigger("click");
    await flushPromises();

    const mask = document.querySelector(".cinema-mask");
    expect(mask, "Cinema 全屏应经 Teleport 挂到 body").not.toBeNull();
    expect(document.querySelectorAll(".cinema-dot").length).toBe(2);

    // 当前查看 g2（active=g1）→ 圆点 1 回 g1 后「设为当前成片」禁用
    await (document.querySelectorAll(".cinema-dot")[0] as HTMLElement).click();
    await flushPromises();
    expect(document.querySelector(".cinema-gen-label")?.textContent).toContain("Generation #1");
    expect((document.querySelector(".cinema-adopt") as HTMLButtonElement).disabled).toBe(true);

    // 切回 g2 → 可设为当前成片 → 采纳后 activeGenerationId 指向 g2
    await (document.querySelectorAll(".cinema-dot")[1] as HTMLElement).click();
    await flushPromises();
    expect((document.querySelector(".cinema-adopt") as HTMLButtonElement).disabled).toBe(false);
    await (document.querySelector(".cinema-adopt") as HTMLElement).click();
    await flushPromises();
    const activeShot = flatShots().find((x) => x.id === s.id)!;
    expect(activeShot.activeGenerationId).toBe("g2");

    // 关闭全屏
    await (document.querySelector(".cinema-close") as HTMLElement).click();
    await flushPromises();
    expect(document.querySelector(".cinema-mask")).toBeNull();
  });

  it("P2-⑨ 版本条 foot 只保留「⭐ 设为当前成片」；全屏入口唯一（ph-cinema）无重复放大按钮", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await seedGens(wrapper, ["g1", "g2"]);

    // 版本条 foot：只保留「⭐ 设为当前成片」一个操作按钮，消除与 ph-cinema 重复的「⛶ 放大预览」
    const foot = wrapper.find(".gen-strip-foot");
    expect(foot.exists()).toBe(true);
    expect(foot.findAll("button").length, "foot 应只剩设为当前成片一个按钮").toBe(1);
    expect(foot.find(".gen-foot-adopt").exists()).toBe(true);
    expect(foot.text()).not.toContain("放大预览");
    // 全屏入口唯一 = 主播放器右上角 [⛶]（Cinema Mode 唯一触发点）
    expect(wrapper.find(".ph-cinema").exists()).toBe(true);
  });

  it("Shot 信息卡纯文字：查看版本 → 历史快照 + 复制按钮；无 .sd-media 播放器", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await seedGens(wrapper, ["g1", "g2"]);

    // 信息卡是纯文字：不存在任何详情区视频
    expect(wrapper.find(".sd-media").exists()).toBe(false);
    expect(wrapper.find(".shot-detail video").exists()).toBe(false);

    await wrapper.findAll(".gen-chip")[1].trigger("click");
    await flushPromises();
    const detail = wrapper.find(".shot-detail");
    // 头部 Generation 徽标
    expect(detail.find(".shot-detail-head .sd-gen-chip").text()).toContain("Generation #2");
    // 历史快照提示 + 复制到草稿
    expect(detail.text()).toContain("当前显示 Generation #2 的历史快照");
    expect(detail.text()).toContain("复制该版到当前草稿");
    // 五区显示版本快照（视觉g2），而非当前草稿
    expect(detail.text()).toContain("视觉g2");
    // 参数 chips 来自版本快照
    expect(detail.text()).toContain("任务：");
    expect(detail.text()).toContain("分辨率");
  });
});

describe("V1.8-2 Workflow Registry 手动工作流选择", () => {
  /**
   * 打开导入弹窗并粘贴工作流 JSON 后确认导入。
   * 弹窗经 Teleport 到 body，故用 document.body 定位弹窗元素（与既有 prompt-modal/leave-modal 测试一致）。
   */
  async function importWorkflow(wrapper: Awaited<ReturnType<typeof mountWorkbench>>, wf: Record<string, unknown>): Promise<void> {
    await wrapper.find(".wf-import").trigger("click");
    await flushPromises();
    const text = document.body.querySelector(".wf-import-text") as HTMLTextAreaElement;
    expect(text, "导入弹窗应渲染到 body（Teleport）").toBeTruthy();
    text.value = JSON.stringify(wf);
    text.dispatchEvent(new Event("input", { bubbles: true }));
    await flushPromises();
    const go = document.body.querySelector(".wf-import-go") as HTMLButtonElement;
    expect(go, "确认按钮应存在").toBeTruthy();
    expect(go.disabled, "填入 JSON 后确认按钮应可点").toBe(false);
    go.click();
    await flushPromises();
  }

  it("默认工作流：生成栏有内置选项 + ✓ 节点 5 徽标", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    const sel = wrapper.find(".wf-select");
    expect(sel.exists()).toBe(true);
    const first = sel.findAll("option")[0];
    expect(first.attributes("value")).toBe(BUILTIN_WORKFLOW_ID);
    expect(first.text()).toContain("内置");
    const badge = wrapper.find(".wf-badge");
    expect(badge.text()).toContain("节点 5");
    expect(badge.classes()).toContain("wf-ok");
  });

  it("导入 0 个 Director 节点的工作流：徽标变❌，生成被阻止并提示非 H3 工作流", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await importWorkflow(wrapper, { "1": { class_type: "UNETLoader", inputs: {} } });
    expect(wrapper.find(".wf-badge").text()).toContain("非 H3 工作流");
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).not.toHaveBeenCalled();
    expect(useWorkbench().lastError).toContain("不是 MiniMax H3 Director 工作流");
  });

  it("导入多节点工作流：自动弹选择；未选生成被阻止并提示选择节点", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await importWorkflow(wrapper, {
      "1": { class_type: "UNETLoader", inputs: {} },
      "5": { class_type: "MiniMaxH3Director", inputs: {} },
      "9": { class_type: "MiniMaxH3Director", inputs: {} },
    });
    // 多节点选择弹窗出现（Teleport 到 body）
    const modal = document.body.querySelector(".wf-modal-mask") as HTMLElement;
    expect(modal, "多节点选择弹窗应出现").toBeTruthy();
    expect(modal.textContent).toContain("检测到 2 个");
    // 关闭弹窗（未选择）→ 生成被阻止
    (modal.querySelector(".wf-modal-close") as HTMLButtonElement).click();
    await flushPromises();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).not.toHaveBeenCalled();
    expect(useWorkbench().lastError).toContain("请选择生成节点");
  });

  it("导入有效工作流（节点 9）后生成：run 使用导入工作流 + 节点 9", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await importWorkflow(wrapper, {
      "1": { class_type: "UNETLoader", inputs: {} },
      "9": { class_type: "MiniMaxH3Director", inputs: { frame_rate: 24, timeline_data: [] } },
    });
    expect(wrapper.find(".wf-badge").text()).toContain("节点 9");
    // 默认范围「全部镜头」（3 镜）走查模式 → 逐镜提交
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).toHaveBeenCalled();
    const nodeId = runServiceMock.run.mock.calls[0][2] as string;
    expect(nodeId).toBe("9");
    const workflow = runServiceMock.run.mock.calls[0][1] as Record<string, unknown>;
    expect(workflow["1"]).toBeDefined();
    expect(workflow["9"]).toBeDefined();
    expect(workflow["5"]).toBeUndefined(); // 导入工作流不含内置节点 5
  });

  it("多节点工作流在弹窗选择节点后生成：run 使用所选节点", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await importWorkflow(wrapper, {
      "1": { class_type: "UNETLoader", inputs: {} },
      "5": { class_type: "MiniMaxH3Director", inputs: {} },
      "9": { class_type: "MiniMaxH3Director", inputs: {} },
    });
    const opts = Array.from(document.body.querySelectorAll(".wf-node-opt"));
    expect(opts.length).toBe(2);
    (opts[1] as HTMLButtonElement).click(); // 选节点 9
    await flushPromises();
    expect(document.body.querySelector(".wf-modal-mask"), "选择节点后弹窗应关闭").toBeNull();
    await wrapper.find(".gen-ctl .btn.primary").trigger("click");
    await flushPromises();
    expect(runServiceMock.run).toHaveBeenCalled();
    expect(runServiceMock.run.mock.calls[0][2]).toBe("9");
  });

  it("导入的工作流持久化到 localStorage，重新挂载后恢复激活", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await importWorkflow(wrapper, {
      "1": { class_type: "UNETLoader", inputs: {} },
      "9": { class_type: "MiniMaxH3Director", inputs: {} },
    });
    const raw = localStorage.getItem(WORKFLOW_REGISTRY_KEY);
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!) as { id: string; name: string }[];
    expect(parsed.length).toBe(1);
    expect(parsed[0].id.startsWith("manual:")).toBe(true);
    wrapper.unmount();
    // 重新挂载（模拟刷新）→ onMount 恢复注册表 + 激活项
    const w2 = await mountWorkbench();
    await enterWorkbench(w2);
    await flushPromises();
    expect(w2.find(".wf-badge").text()).toContain("节点 9");
  });

  it("删除手动导入工作流：回退内置，注册表清空", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await importWorkflow(wrapper, {
      "1": { class_type: "UNETLoader", inputs: {} },
      "9": { class_type: "MiniMaxH3Director", inputs: {} },
    });
    expect(wrapper.find(".wf-del").exists()).toBe(true);
    await wrapper.find(".wf-del").trigger("click");
    await flushPromises();
    expect(wrapper.find(".wf-badge").text()).toContain("节点 5");
    expect(JSON.parse(localStorage.getItem(WORKFLOW_REGISTRY_KEY) ?? "[]")).toEqual([]);
  });
});

describe("V1.11.3 新建项目不残留上一项目镜头/任务（#399）", () => {
  /** 模拟后端 create_project + load_project 返回的空项目（ep1 存在但无场景）。 */
  const EMPTY_PROJECT_JSON = {
    id: "proj_empty",
    name: "未命名项目",
    createdAt: "",
    updatedAt: "",
    episodes: [
      {
        id: "ep1",
        episodeNumber: 1,
        title: "第一集",
        scenes: [],
        assets: { cast: [], locations: [], props: [], styles: [] },
      },
    ],
  };

  it("新开项目后：底部 Storyboard 无旧卡片 + 任务中心无旧任务", async () => {
    apiMock.loadProject.mockResolvedValue(EMPTY_PROJECT_JSON);
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    // 样例工程有镜头 → Storyboard 卡片非空。
    expect(wrapper.findAll(".tl-card").length).toBeGreaterThan(0);

    // 模拟「上一项目刚生成过」：任务中心有一条任务（底部残留的旧 shot 来源）。
    const st0 = useWorkbench();
    addTask({
      scopeLabel: "全部镜头（3）",
      shotCount: 3,
      shotOrder: flatShots().map((s) => s.id),
      targetShotIds: null,
    });
    expect(st0.tasks).toHaveLength(1);

    // 返回项目页（← 项目 → clearProject）。
    await wrapper.find(".wb-head .btn.nav").trigger("click");
    await flushPromises();
    expect(useWorkbench().project).toBeNull();

    // 新建项目 → 打开空项目。
    const newBtn = wrapper.findAll(".home-actions button").find((b) => b.text().includes("新建项目"));
    expect(newBtn, "home 应有「＋ 新建项目」按钮").toBeTruthy();
    await newBtn!.trigger("click");
    await flushPromises();

    const st = useWorkbench();
    expect(st.project).toBeTruthy();
    expect(st.project!.id).toBe("proj_empty");
    expect(st.project!.episodes[0].scenes).toHaveLength(0);
    // 核心断言：底部任务中心不残留上一项目任务。
    expect(st.tasks, "新项目任务中心应为空").toHaveLength(0);
    expect(st.currentTaskId).toBeNull();
    // Storyboard / 镜头卡 / 左侧场景树都应为空。
    expect(wrapper.findAll(".tl-card"), "新项目 Storyboard 应为空").toHaveLength(0);
    expect(wrapper.findAll(".shot-card")).toHaveLength(0);
  });
});

/**
 * #456（P1-⑦）：镜头时长输入「编辑期草稿」回归测试。
 * 用户现场：输入 5.5 时一松开就跳回旧值（5 / 6），小数秒根本输不进去。
 * 根因：`@change` 才写回 store，但输入过程中任一次响应式重渲染（进度轮询/切镜 watch
 * 触发的 patch）都会把 `:value` 反写为旧 store 值，把正在输入的 5.5/5.55 吞掉。
 * 修复：输入期间用 durationDraft 顶住 DOM value（重渲染只对比草稿，不改用户输入），
 * @input 只更新草稿，@change（失焦/回车）才解析并写回 store，随后清空草稿。
 * 此块钉死四条不变量：重渲染不改输入 / 未提交不写 store / 提交后才写 / 非法输入被拦。
 */
describe("#456 镜头时长输入编辑期草稿", () => {
  it("输入中数值保持 5.5 不动，可一直改到满意；期间重渲染不反写；未失焦 store 不变", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const findDur = () => wrapper.find<HTMLInputElement>(".wb-duration-input");
    expect(findDur().exists(), "应能找到当前镜头时长输入框").toBe(true);
    // 样例项目当前镜 s1（雨夜登场）时长 5。
    expect(findDur().element.value).toBe("5");
    expect(getCurrentShot()!.durationSec).toBe(5);

    // ① 输入 5.5（只发 input 事件，不发 change）。
    // 注：test-utils setValue 对 number input 会同步连发 input+change（change 即提交），
    // 无法模拟「输入中未失焦」状态；改用真实时序：直接设 el.value + input 事件，
    // 中间隔一次 flush，与真实浏览器打字一致。
    const el = findDur().element;
    el.value = "5.5";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    await flushPromises();
    expect(findDur().element.value).toBe("5.5");

    // ② 关键回归：输入途中发生一次响应式重渲染（模拟进度轮询/任务 patch）。
    setRunning(true);
    await nextTick();
    setRunning(false);
    await nextTick();
    await flushPromises();
    // 输入框必须仍是用户正在输入的值，绝不反写回 5。
    expect(findDur().element.value, "重渲染不得把输入中的 5.5 反写回 5").toBe("5.5");

    // ③ 未失焦提交前 store 里的唯一真相源不动。
    expect(getCurrentShot()!.durationSec, "未提交前 store 保持旧值").toBe(5);

    // ④ 失焦/回车提交 → 写回 store，输入框维持 5.5。
    await findDur().trigger("change");
    await nextTick();
    await flushPromises();
    expect(getCurrentShot()!.durationSec).toBe(5.5);
    expect(findDur().element.value).toBe("5.5");
  });

  it("非法/空输入被 onDurationChange 拦截：store 保持最后一个合法值", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    // 右侧镜头设置面板带 v-else-if + shot 重渲染会重建节点，每次操作都重新查询输入框。
    const findDur = () => wrapper.find<HTMLInputElement>(".wb-duration-input");

    // 先提交一个合法值 5.5。
    await findDur().setValue("5.5");
    await findDur().trigger("change");
    await flushPromises();
    expect(getCurrentShot()!.durationSec).toBe(5.5);

    // 删空（模拟真实打字：el.value="" + input 事件，中间隔一次 flush）。
    // 注：不能用 test-utils setValue("")——它把 input+change 在同一同步 burst 连发，
    // happy-dom 下最后一次渲染 patch 时 oldValue===newValue 被跳过、DOM 残留空值，
    // 这是测试环境交互伪影；真实浏览器打字与失焦是两次独立事件，中间必有渲染。
    const el = findDur().element;
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    await flushPromises();
    expect(findDur().element.value).toBe("");
    expect(getCurrentShot()!.durationSec).toBe(5.5);

    // 失焦提交：空串不写入，且输入框回显 store 值（草稿 "" → null 触发重渲染 + :value 回落）。
    await findDur().trigger("change");
    await nextTick();
    await flushPromises();
    expect(getCurrentShot()!.durationSec, "空输入不得写坏 store").toBe(5.5);
    expect(findDur().element.value, "空输入被拦后回显 store 值").toBe("5.5");

    // 非数字（绕过 number 输入限制直接注入 DOM value）→ Number("abc")=NaN 被拦。
    // 非法提交本身不触发响应式重渲染（草稿已为 null、store 未写），DOM 靠下一次渲染自愈。
    findDur().element.value = "abc";
    await findDur().trigger("change");
    await nextTick();
    await flushPromises();
    expect(getCurrentShot()!.durationSec, "非数字不得写坏 store").toBe(5.5);
    // 制造一次无害重渲染（进度轮询等价物）→ 输入框回显 store 值。
    setRunning(true);
    await nextTick();
    setRunning(false);
    await nextTick();
    await flushPromises();
    expect(findDur().element.value, "非数字被拦后输入框在下一次渲染回显 store 值").toBe("5.5");
  });

  it("编辑途中切镜头：草稿清空，新镜头输入框回显自己的时长", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const findDur = () => wrapper.find<HTMLInputElement>(".wb-duration-input");
    expect(findDur().element.value).toBe("5"); // s1

    // 在 s1 上输入 5.5 但不提交（真实时序：el.value + input 事件），直接切到场景「数据塔」（shot s3，时长 6）。
    // 注意：不能点 .scene-name——它走 selectSceneSettings 切到「场景设置」面板，
    // 镜头设置面板（含时长输入框）会被卸载；真实「换镜不换面板」的动线是点左侧镜头卡
    // （selectShot 保持 pane="shot"）或 store selectScene（同样保持 pane="shot"）。
    const el = findDur().element;
    el.value = "5.5";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    await flushPromises();
    expect(findDur().element.value).toBe("5.5");
    expect(getCurrentShot()!.durationSec).toBe(5);
    selectScene("sc2");
    await nextTick();
    await flushPromises();
    expect(getCurrentShot()!.id).toBe("s3");
    // 草稿被 watch(shot) 清空 + 面板重建 → 输入框回显 s3 自己的时长 6，绝不含 5.5。
    expect(findDur().element.value, "切镜后不得把上一镜草稿带入新镜头").toBe("6");
    expect(getCurrentShot()!.durationSec).toBe(6);
  });
});

describe("#454 视频播放稳定性（灰卡/URL 失效修复）", () => {
  /** 给首个镜头加版本 + 选中该镜（版本条/播放器渲染前置条件）。 */
  async function selectFirstShotWithGens(
    wrapper: Awaited<ReturnType<typeof mountWorkbench>>,
    gens: ShotGenRecord[],
  ): Promise<ReturnType<typeof flatShots>[number]> {
    const s = flatShots()[0];
    gens.forEach((g) => addGeneration(s.id, g));
    await flushPromises();
    const card = wrapper.findAll(".shot-card").find((c) => c.text().includes(s.name ?? s.id));
    expect(card, "应能找到首个镜头卡").toBeTruthy();
    await card!.trigger("click");
    await flushPromises();
    return s;
  }

  it("主播放器 URL 失效且有归档版本：@error 自动切到归档 input 文件并提示", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    // g1 只有 output 临时文件（ComfyUI 重启即失效）；g2 有归档 videoFile（input 目录持久）。
    const s = await selectFirstShotWithGens(wrapper, [
      makeGen("g1", { videoFile: "", outputSubfolder: "video" }),
      makeGen("g2", { videoFile: `minimax_studio/generations/proj/shot/g2.mp4` }),
    ]);
    expect(s, "样例项目至少 3 镜").toBeTruthy();

    // 点 #1（output 临时）→ 主播放器 src 指向 /view?type=output。
    await wrapper.findAll(".gen-chip")[0].trigger("click");
    await flushPromises();
    const v = wrapper.find(".player-video");
    const srcBefore = v.attributes("src") ?? "";
    expect(srcBefore).toContain("type=output");
    expect(srcBefore).toContain("g1.mp4");
    expect(wrapper.find(".player-err").exists()).toBe(false);

    // 模拟播放失败（output 临时文件被清理/重启失效）→ 不得静默黑屏。
    await v.trigger("error");
    await flushPromises();
    const srcAfter = v.attributes("src") ?? "";
    expect(srcAfter).toContain("g2.mp4");
    expect(srcAfter).toContain("type=input");
    expect(wrapper.find(".player-err").exists(), "应出现错误横幅（而非无声灰卡）").toBe(true);
    expect(wrapper.find(".player-err").text()).toContain("已自动切换至归档版本");
    expect(useWorkbench().finalVideo?.url).toBe(srcAfter);
  });

  it("主播放器 URL 失效且无归档：显示明确错误；loadeddata 成功加载后横幅清除", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await selectFirstShotWithGens(wrapper, [makeGen("g1", { videoFile: "", outputSubfolder: "video" })]);

    await wrapper.findAll(".gen-chip")[0].trigger("click");
    await flushPromises();
    const v = wrapper.find(".player-video");
    expect((v.attributes("src") ?? "").startsWith("/view?")).toBe(true);

    // 无归档可回退 → 明确错误（替代静默黑屏/灰卡）。
    await v.trigger("error");
    await flushPromises();
    expect(wrapper.find(".player-err").text()).toContain("无法加载");

    // 视频随后成功加载（如用户重新生成 / 手动切换版本成功）→ 错误横幅清除，可再次回退。
    await v.trigger("loadeddata");
    await flushPromises();
    expect(wrapper.find(".player-err").exists(), "loadeddata 后错误横幅应清除").toBe(false);
  });

  it("播放队列单镜加载失败：跳过该镜继续播下一镜，不全片中断", async () => {
    loadProject(loadSampleProject());
    clearSelectedShots();
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
    useWorkbench().paramHashes = {};
    apiMock.segmentStatus.mockResolvedValue({ cached: [0, 2], states: {} });
    // 第 0 镜 segment_mp4 失败一次（视频缺失），后续镜头正常。
    apiMock.segmentMp4.mockImplementationOnce(async () => {
      throw new Error("mp4 不可用");
    });

    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await wrapper.find('button[title="顺序播放全部已生成镜头"]').trigger("click");
    await flushPromises();

    // 核心回归：#454 之前 catch 即中断，segmentMp4 只调 1 次且播放器无输出；
    // 现在跳到第 2 镜继续播 → 调用 2 次 + 播放器有内容。
    expect(apiMock.segmentMp4).toHaveBeenCalledTimes(2);
    expect(useWorkbench().finalVideo?.filename, "第 0 镜失败后应继续播到第 2 镜").toBe("Shot02.mp4");
    expect(wrapper.find(".player-video").attributes("src") ?? "").toContain("blob:");
  });

  it("播放队列全部镜头失败：给出明确错误而非静默", async () => {
    loadProject(loadSampleProject());
    clearSelectedShots();
    useWorkbench().tasks.splice(0);
    useWorkbench().currentTaskId = null;
    useWorkbench().paramHashes = {};
    apiMock.segmentStatus.mockResolvedValue({ cached: [0, 2], states: {} });
    apiMock.segmentMp4.mockRejectedValue(new Error("mp4 不可用"));

    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    await flushPromises();
    await wrapper.find('button[title="顺序播放全部已生成镜头"]').trigger("click");
    await flushPromises();

    expect(apiMock.segmentMp4).toHaveBeenCalledTimes(2);
    expect(useWorkbench().lastError, "全部镜头不可播时应给出明确提示").toContain("没有可播放的视频");
  });
});

describe("#453 Generation History 持久化 round-trip（current_generation_id 落盘 + 加载兜底）", () => {
  /** 深拷贝示例项目（避免改坏共享 fixture）。 */
  function cloneSample(): Project {
    return JSON.parse(JSON.stringify(loadSampleProject())) as Project;
  }

  /** 给首镜加 2 个版本，选中 #2 并「设为当前成片」（activeGenerationId 应为 g2）。 */
  async function seedTwoAndAdoptSecond(
    wrapper: Awaited<ReturnType<typeof mountWorkbench>>,
  ): Promise<ReturnType<typeof flatShots>[number]> {
    const s = flatShots()[0];
    addGeneration(s.id, makeGen("g1"));
    addGeneration(s.id, makeGen("g2"));
    await flushPromises();
    const card = wrapper.findAll(".shot-card").find((c) => c.text().includes(s.name ?? s.id));
    expect(card, "应能找到首个镜头卡").toBeTruthy();
    await card!.trigger("click");
    await flushPromises();
    // 版本条 #1/#2；点 #2 查看 → 「⭐ 设为当前成片」置为当前。
    const chips = wrapper.findAll(".gen-chip");
    expect(chips).toHaveLength(2);
    await chips[1].trigger("click");
    await flushPromises();
    const adopt = wrapper.find(".gen-foot-adopt");
    expect(adopt.exists(), "选中非当前版本后应出现「设为当前成片」按钮").toBe(true);
    await adopt.trigger("click");
    await flushPromises();
    return s;
  }

  it("保存 payload 落盘 generations + activeGenerationId；重载后 ⭐ 徽标仍在用户选版本", async () => {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    const s = await seedTwoAndAdoptSecond(wrapper);
    expect(s.activeGenerationId, "用户选版本应成为 current generation").toBe("g2");

    // ① 保存 → 断言落盘 payload 含 generations + activeGenerationId（current_generation_id）
    await wrapper.find(".btn.save").trigger("click");
    await flushPromises();
    const saves = apiMock.saveProject.mock.calls;
    expect(saves.length).toBeGreaterThan(0);
    const payload = saves[saves.length - 1][1] as {
      episodes: { scenes: { shots: { id: string; generations?: unknown[]; activeGenerationId?: string }[] }[] }[];
    };
    const savedShot = payload.episodes[0].scenes.flatMap((sc) => sc.shots).find((sh) => sh.id === s.id)!;
    expect(savedShot.generations, "保存 payload 应包含 generations 数组").toHaveLength(2);
    expect(savedShot.activeGenerationId, "保存 payload 应包含用户选的 current_generation_id").toBe("g2");

    // ② 模拟「保存后关闭再打开」：serialize → deserialize → loadProject
    loadProject(deserializeProject(serializeProject(useWorkbench().project!)));
    await flushPromises();
    const reloaded = flatShots().find((x) => x.id === s.id)!;
    expect(reloaded.generations, "重载后 generations 完整保留").toHaveLength(2);
    expect(reloaded.activeGenerationId, "重载后 current_generation_id 恢复为用户选版本").toBe("g2");

    // ③ UI 版本条 ⭐ 徽标仍在 #2（gen-chip[1]），#1 无 ⭐
    const chips = wrapper.findAll(".gen-chip");
    expect(chips).toHaveLength(2);
    expect(chips[1].find(".gb-adopted").exists(), "⭐ 当前徽标应位于 #2").toBe(true);
    expect(chips[0].find(".gb-adopted").exists(), "#1 不应有 ⭐ 徽标").toBe(false);
  });

  it("loadProject 兜底：activeGenerationId 悬空（指向已删除版本）→ 回退到最后一条", () => {
    const project = cloneSample();
    const s = project.episodes[0].scenes[0].shots[0];
    s.generations = [makeGen("g1"), makeGen("g2")];
    s.activeGenerationId = "ghost"; // 已被删除/外部清理的版本 id
    loadProject(project);
    const shot = useWorkbench().project!.episodes[0].scenes[0].shots[0];
    expect(shot.activeGenerationId, "悬空 current_generation_id 应回退到最后一条").toBe("g2");
    expect(shot.generations, "兜底不应丢 generations").toHaveLength(2);
  });

  it("loadProject 兜底：无 generations → 清空 activeGenerationId（不残留悬空 ⭐）", () => {
    const project = cloneSample();
    const s = project.episodes[0].scenes[0].shots[0];
    s.generations = undefined;
    s.activeGenerationId = "ghost";
    loadProject(project);
    const shot = useWorkbench().project!.episodes[0].scenes[0].shots[0];
    expect(shot.activeGenerationId, "无 generations 时应清空 current_generation_id").toBeUndefined();
  });

  it("addGeneration 首版自动采纳 / 后续版本不自动切换；removeGeneration 删当前成片回落到最后一条", () => {
    const project = cloneSample();
    loadProject(project);
    const s = flatShots()[0];
    expect(s.activeGenerationId).toBeUndefined();
    addGeneration(s.id, makeGen("g1"));
    expect(s.activeGenerationId, "首版自动采纳为当前成片").toBe("g1");
    addGeneration(s.id, makeGen("g2"));
    expect(s.activeGenerationId, "后续版本不自动切换当前成片").toBe("g1");
    setActiveGeneration(s.id, "g2");
    expect(s.activeGenerationId, "用户设当前成片后更新").toBe("g2");
    removeGeneration(s.id, "g2");
    expect(s.activeGenerationId, "删当前成片后回落到最后一条").toBe("g1");
    removeGeneration(s.id, "g1");
    expect(s.activeGenerationId, "删空后 current_generation_id 清空").toBeUndefined();
  });
});

describe("P1-A-5 Timeline 主视图 + 地点/人物筛选（#525）", () => {
  /** 双地点双人物项目：天台（林雪/陈默），地下车库（林雪）。3 镜 5+6+4=15s。 */
  function tlProject(): Project {
    const castLX: Asset = { id: "cast_lx", name: "林雪", kind: "cast", imageFile: "a.png" };
    const castCM: Asset = { id: "cast_cm", name: "陈默", kind: "cast", imageFile: "b.png" };
    const locA: Asset = { id: "loc_a", name: "天台", kind: "location", imageFile: "c.png" };
    const locB: Asset = { id: "loc_b", name: "地下车库", kind: "location", imageFile: "d.png" };
    const mk = (id: string, sceneId: string, order: number, castId: string, dur: number): Shot => ({
      // castManual: true 是「castIds 有值 ⟺ manual=true」契约（normalizeShotInheritance 会清掉
      // 有 castIds 但 manual 假的残留；真实导入/UI 原子操作均带 manual=true，fixture 必须一致）。
      id, sceneId, order, durationSec: dur, castIds: [castId], castManual: true, content: { visual: `镜头${id}` },
    });
    const scA: Scene = {
      id: "sc_a", name: "天台", order: 0,
      assets: { cast: [castLX, castCM], locations: [locA], props: [], styles: [] },
      shots: [mk("s1", "sc_a", 0, "cast_lx", 5), mk("s2", "sc_a", 1, "cast_cm", 6)],
    };
    const scB: Scene = {
      id: "sc_b", name: "地下车库", order: 1,
      assets: { cast: [castLX], locations: [locB], props: [], styles: [] },
      shots: [mk("s3", "sc_b", 0, "cast_lx", 4)],
    };
    const ep: Episode = {
      id: "ep1", episodeNumber: 1, title: "第一集", scenes: [scA, scB],
      assets: { cast: [castLX, castCM], locations: [locA, locB], props: [], styles: [] },
    };
    return { id: "proj_tl", name: "筛选测试", createdAt: "", updatedAt: "", episodes: [ep] };
  }

  /** 打开工作台（先点「载入示例工程」进入 workbench 视图，再替换为测试项目）。 */
  async function mountTl(project: Project = tlProject()) {
    const wrapper = await mountWorkbench();
    await enterWorkbench(wrapper);
    loadProject(project);
    await flushPromises();
    return wrapper;
  }

  it("顶部概览统计：镜/地点/人物/总时长；时间线默认按场景树顺序", async () => {
    const wrapper = await mountTl();
    const stats = wrapper.find(".tl-overview-stats");
    expect(stats.text()).toContain("3 镜");
    expect(stats.text()).toContain("2 地点");
    expect(stats.text()).toContain("2 人物");
    expect(stats.text()).toContain("共 0:15");
    const cards = wrapper.findAll(".tl-card");
    expect(cards.map((c) => c.find(".tl-name").text().trim())).toEqual(["s1", "s2", "s3"]);
  });

  it("地点筛选：只看该地点的镜头；清除按钮恢复全片", async () => {
    const wrapper = await mountTl();
    const sel = wrapper.findAll(".tl-filter-select");
    expect(sel).toHaveLength(2);
    expect(wrapper.find(".tl-filter-clear").exists()).toBe(false);
    await sel[0].setValue("sc_a");
    await flushPromises();
    expect(wrapper.findAll(".tl-card").map((c) => c.find(".tl-name").text().trim())).toEqual(["s1", "s2"]);
    expect(wrapper.find(".tl-filter-clear").exists()).toBe(true);
    await sel[0].setValue("sc_b");
    await flushPromises();
    expect(wrapper.findAll(".tl-card").map((c) => c.find(".tl-name").text().trim())).toEqual(["s3"]);
    await wrapper.find(".tl-filter-clear").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".tl-card")).toHaveLength(3);
    expect(wrapper.find(".tl-filter-clear").exists()).toBe(false);
  });

  it("人物筛选：选项=时间线各镜有效 cast 名称并集（id→名称），命中镜头收窄", async () => {
    const wrapper = await mountTl();
    const sel = wrapper.findAll(".tl-filter-select");
    const castOpts = sel[1].findAll("option").map((o) => o.text());
    expect(castOpts).toContain("林雪");
    expect(castOpts).toContain("陈默");
    await sel[1].setValue("林雪");
    await flushPromises();
    expect(wrapper.findAll(".tl-card").map((c) => c.find(".tl-name").text().trim())).toEqual(["s1", "s3"]);
    await sel[1].setValue("陈默");
    await flushPromises();
    expect(wrapper.findAll(".tl-card").map((c) => c.find(".tl-name").text().trim())).toEqual(["s2"]);
  });

  it("地点+人物联合筛选取交集；无匹配时显示空态", async () => {
    const wrapper = await mountTl();
    const sel = wrapper.findAll(".tl-filter-select");
    await sel[0].setValue("sc_a"); // 天台（s1 林雪 / s2 陈默）
    await sel[1].setValue("陈默");
    await flushPromises();
    expect(wrapper.findAll(".tl-card").map((c) => c.find(".tl-name").text().trim())).toEqual(["s2"]);
    // 林雪 × 地下车库 → 无交集（地下只有 s3 林雪，但地点≠天台）
    await sel[0].setValue("sc_b");
    await sel[1].setValue("林雪");
    await flushPromises();
    expect(wrapper.findAll(".tl-card")).toHaveLength(1);
    // 陈默 × 地下车库 → 空
    await sel[1].setValue("陈默");
    await flushPromises();
    expect(wrapper.findAll(".tl-card")).toHaveLength(0);
    expect(wrapper.find(".tl-empty").exists()).toBe(true);
  });

  it("人物下拉禁用：项目无 cast 资产时灰置", async () => {
    const p = tlProject();
    p.episodes[0].assets!.cast = [];
    p.episodes[0].scenes.forEach((sc) => {
      sc.assets!.cast = [];
      sc.shots.forEach((s) => {
        s.castIds = [];
      });
    });
    const wrapper = await mountTl(p);
    const sel = wrapper.findAll(".tl-filter-select");
    expect(sel[1].attributes("disabled")).toBeDefined();
  });
});
