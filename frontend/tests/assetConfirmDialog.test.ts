// @vitest-environment happy-dom
/**
 * V1.7 Phase 4-C：AssetConfirmDialog 补资产弹窗单测。
 *
 * 直接挂载组件（不经过 WorkbenchView），runService 替换为假实现（scanAssets 返回
 * 可控的共享库 + 实体匹配），样例工程从 fixtures 载入。验证：
 *   - 打开 → 构造最小 plan 调 /assets/scan（characters/location_name 正确）；
 *   - 候选展示 + auto 默认选中 + 确认按钮可用性；
 *   - cast 未绑定 → 从共享库注册资产 + setShotCasts 写回真实资产 id；
 *   - cast 已存在但缺图 → 复用资产补图 + 绑定（不新建）；
 *   - asset 缺图（地点/道具）→ updateAsset 填 imageFile；
 *   - 无匹配 → 确认置灰；搜索库手动选图后放行；
 *   - 关闭按钮 emit close。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import AssetConfirmDialog, { type MissingAssetRef } from "@/workbench/AssetConfirmDialog.vue";
import { useWorkbench, loadProject, setRunning } from "@/stores/workbench";
import { loadSampleProject } from "@/fixtures";
import type { AssetScanResult, AssetMatch, ProductionPlanJson } from "@/services/comfyApi";

const apiMock = {
  scanAssets: vi.fn(
    async (_plan: ProductionPlanJson): Promise<AssetScanResult> => ({ assets: [], matches: [], funnel: EMPTY_FUNNEL, visual_elements: [] }),
  ),
};
const runServiceMock = { api: apiMock };

beforeEach(() => {
  vi.clearAllMocks();
  const st = useWorkbench() as unknown as { runService: unknown };
  st.runService = runServiceMock;
  setRunning(false);
  loadProject(loadSampleProject());
});

function sampleCtx() {
  const st = useWorkbench() as { project: { episodes: import("@/models/project").Episode[] } };
  const ep = st.project!.episodes[0];
  const sc = ep.scenes[0];
  const s = sc.shots[0];
  return { ep, sc, s };
}

function ensureAssets(sc: { assets?: import("@/models/project").SceneAssets }) {
  if (!sc.assets) sc.assets = { cast: [], locations: [], props: [], styles: [] };
  return sc.assets;
}

async function mountDlg(
  ep: import("@/models/project").Episode,
  sc: import("@/models/project").Scene,
  s: import("@/models/project").Shot,
  missing: MissingAssetRef[],
) {
  const wrapper = mount(AssetConfirmDialog, {
    props: { shot: s, scene: sc, episode: ep, missing },
  });
  await flushPromises();
  return wrapper;
}

const castLib = (name: string) => `minimax_studio/assets/${name}.png`;

function autoMatch(kind: AssetMatch["kind"], name: string): AssetMatch {
  return {
    kind,
    name,
    status: "auto",
    matched: true,
    asset_name: name,
    image_file: castLib(name),
    confidence: 1,
    candidates: [{ name, image_file: castLib(name), score: 1 }],
    entity_type: kind === "cast" ? "character" : kind === "location" ? "location" : "prop",
    entity_source: "script",
    entity_confidence: 0.95,
    entity_id: "ent_001",
    can_auto: true,
    asset_requirement: kind === "prop" ? "recommended" : "required",
    match_kind: "exact",
    suggest: false,
    // Phase 2-1（#135）：稳定 entity_key / 永久 asset_id（测试固定值即可）。
    entity_key: `${kind === "cast" ? "character" : kind === "location" ? "location" : "prop"}:${name}`,
    asset_id: null,
  };
}

/** V1.7 实体抽取加固（#357）+ Phase 1.1：funnel 固定 0 值（纯前端测试不依赖后端统计）。 */
const EMPTY_FUNNEL = {
  discovered: 0, invalid_dropped: 0, dedup_dropped: 0, seeded: 0,
  cleansed: 0, entered: 0, visual_only: 0, excluded: 0, auto: 0, pending: 0, none: 0,
};

describe("AssetConfirmDialog：审核态补资产", () => {
  it("打开 → 构造最小 plan 调 /assets/scan；显示候选；auto 默认选中 → 确认写回 castIds + 注册资产 + emit confirmed", async () => {
    const { ep, sc, s } = sampleCtx();
    const missing: MissingAssetRef[] = [{
      mode: "cast",
      entityName: "测试角色A",
      assetId: "",
      poolAsset: null,
      assetKind: "cast",
      text: "角色「测试角色A」未绑定资产",
    }];
    apiMock.scanAssets.mockResolvedValue({
      assets: [{ name: "测试角色A", image_file: castLib("测试角色A") }],
      matches: [autoMatch("cast", "测试角色A")],
      funnel: EMPTY_FUNNEL,
      visual_elements: [],
    });

    const w = await mountDlg(ep, sc, s, missing);

    // ① plan 构造：characters 带上缺失角色。
    expect(apiMock.scanAssets).toHaveBeenCalledTimes(1);
    const plan = apiMock.scanAssets.mock.calls[0][0] as {
      scenes: { shots: { characters: { name: string }[] }[] }[];
    };
    expect(plan.scenes[0].shots[0].characters[0].name).toBe("测试角色A");

    // ② 候选渲染 + auto 默认选中 → 确认可点。
    expect(w.find(".ac-cand").exists()).toBe(true);
    expect(w.find(".ac-foot .primary").attributes("disabled")).toBeUndefined();

    // ③ 确认 → 注册 cast 资产（复用共享库文件，不新建「_2」）+ 写回 castIds。
    await w.find(".ac-foot .primary").trigger("click");
    await flushPromises();

    const pool = ensureAssets(sc).cast;
    const created = pool.find((a) => a.name === "测试角色A");
    expect(created).toBeTruthy();
    expect(created!.imageFile).toBe(castLib("测试角色A"));
    expect(s.castIds).toContain(created!.id);
    expect(w.emitted("confirmed")).toBeTruthy();
  });

  it("cast 已存在池中但缺图 → 复用资产补图 + 绑定（不新建资产）", async () => {
    const { ep, sc, s } = sampleCtx();
    ensureAssets(sc).cast.push({ id: "cast_x", name: "测试角色B", kind: "cast", imageFile: "" });
    const missing: MissingAssetRef[] = [{
      mode: "cast",
      entityName: "测试角色B",
      assetId: "cast_x",
      poolAsset: ensureAssets(sc).cast.find((a) => a.id === "cast_x")!,
      assetKind: "cast",
      text: "角色「测试角色B」参考图缺失",
    }];
    apiMock.scanAssets.mockResolvedValue({
      assets: [{ name: "测试角色B", image_file: castLib("测试角色B") }],
      matches: [autoMatch("cast", "测试角色B")],
      funnel: EMPTY_FUNNEL,
      visual_elements: [],
    });

    const w = await mountDlg(ep, sc, s, missing);
    await w.find(".ac-foot .primary").trigger("click");
    await flushPromises();

    expect(ensureAssets(sc).cast.find((a) => a.id === "cast_x")!.imageFile).toBe(castLib("测试角色B"));
    expect(s.castIds).toContain("cast_x");
    // 不新建资产：池中仍只有一个「测试角色B」。
    expect(ensureAssets(sc).cast.filter((a) => a.name === "测试角色B")).toHaveLength(1);
  });

  it("生效资产缺图（地点）→ 无匹配确认置灰 → 搜索库手动选图 → updateAsset 填 imageFile", async () => {
    const { ep, sc, s } = sampleCtx();
    ensureAssets(sc).locations.push({ id: "loc_x", name: "测试地点", kind: "location", imageFile: "" });
    const missing: MissingAssetRef[] = [{
      mode: "asset",
      entityName: "测试地点",
      assetId: "loc_x",
      poolAsset: ensureAssets(sc).locations.find((a) => a.id === "loc_x")!,
      assetKind: "location",
      text: "资产「测试地点」未上传参考图",
    }];
    // 共享库无实体匹配（none）→ 候选区空。
    apiMock.scanAssets.mockResolvedValue({
      assets: [{ name: "测试地点参考", image_file: castLib("测试地点参考") }],
      matches: [{
        kind: "location", name: "测试地点", status: "none", matched: false,
        asset_name: null, image_file: null, confidence: 0, candidates: [],
        entity_type: "location", entity_source: "script", entity_confidence: 0.9,
        entity_id: "ent_002", can_auto: true,
        asset_requirement: "required", match_kind: "none", suggest: false,
        // Phase 2-1（#135）：稳定 entity_key / 永久 asset_id（测试固定值即可）。
        entity_key: "location:测试地点",
        asset_id: null,
      }],
      funnel: EMPTY_FUNNEL,
      visual_elements: [],
    });

    const w = await mountDlg(ep, sc, s, missing);
    expect(w.find(".ac-foot .primary").attributes("disabled")).toBeDefined();

    // 搜索共享库 → 出现库图片 → 选中 → 确认放行。
    await w.find(".ac-search").setValue("测试地点参考");
    await flushPromises();
    expect(w.find(".ac-lib .ac-cand").exists()).toBe(true);
    await w.find(".ac-lib .ac-cand").trigger("click");
    await flushPromises();
    expect(w.find(".ac-foot .primary").attributes("disabled")).toBeUndefined();

    await w.find(".ac-foot .primary").trigger("click");
    await flushPromises();
    expect(ensureAssets(sc).locations.find((a) => a.id === "loc_x")!.imageFile).toBe(castLib("测试地点参考"));
    expect(w.emitted("confirmed")).toBeTruthy();
  });

  it("无缺失项 → 空态提示；关闭按钮 emit close", async () => {
    const { ep, sc, s } = sampleCtx();
    const w = await mountDlg(ep, sc, s, []);
    expect(w.text()).toContain("没有资产缺口");
    await w.find(".ac-close").trigger("click");
    expect(w.emitted("close")).toBeTruthy();
  });
});
