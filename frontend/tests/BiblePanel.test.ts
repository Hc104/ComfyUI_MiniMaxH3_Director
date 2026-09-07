// @vitest-environment happy-dom
/**
 * P2-P5（#542）：Global Story Bible 面板单测。
 *
 * 直接挂载 BiblePanel（不经过 WorkbenchView），runService.api 替换为可控假实现。
 * 验证：
 *  - mount → GET /bible（project_id 正确）+ 渲染四组 tab + 条目卡片
 *    （✓已确认/◌候选 徽标、别名、当前状态、最后出现、历史折叠）；
 *  - 四组切换过滤 entity_type；
 *  - ✓ 确认候选 → bibleConfirm(projectId, entity_id, "confirm")，返回全量 Bible 就地刷新；
 *  - ✓ 采纳属性建议 → bibleConfirm(..., "accept_attribute", {key,value})；
 *  - ＋ 别名 → window.prompt → bibleConfirm(..., "merge_alias", {alias})；
 *  - ＋ 新建条目 → bibleEntry(projectId, entity_type, name, {aliases})；
 *  - ✎ 编辑静态属性 → bibleConfirm(..., "edit_attributes", {attributes 空值剔除})；
 *  - getBible 失败 → 错误横幅。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import BiblePanel from "@/workbench/BiblePanel.vue";
import { useWorkbench } from "@/stores/workbench";
import type { StoryBibleJson, BibleEntryJson } from "@/services/comfyApi";

function makeEntry(over: Partial<BibleEntryJson> = {}): BibleEntryJson {
  return {
    entity_id: "CHAR-000",
    entity_type: "character",
    name: "测试角色",
    aliases: [],
    status: "confirmed",
    source: "ai",
    attributes: {},
    attribute_suggestions: [],
    current_status: "",
    last_seen: "",
    history: [],
    first_seen: "",
    props_held: [],
    relationships: [],
    asset_key: "",
    ...over,
  };
}

function makeBible(over: Partial<StoryBibleJson> = {}): StoryBibleJson {
  return {
    bible_id: "bible_demo",
    project_id: "proj_demo",
    version: 2,
    updated_at: "2026-08-15T00:00:00.000Z",
    entries: [
      makeEntry({
        entity_id: "CHAR-001",
        name: "沈青崖",
        aliases: ["沈青山"],
        status: "confirmed",
        attributes: { 身份: "江湖刀客" },
        attribute_suggestions: [{ key: "武器", value: "青铜剑" }],
        current_status: "风雪中推开庙门",
        last_seen: "ep1",
        history: ["ep1：进入破庙"],
        first_seen: "ep1",
      }),
      makeEntry({
        entity_id: "CHAR-002",
        name: "林雪",
        status: "candidate",
        first_seen: "ep1",
      }),
      makeEntry({
        entity_id: "LOC-001",
        entity_type: "location",
        name: "风雪山神庙",
        status: "confirmed",
        attributes: { 环境: "破败庙宇" },
        first_seen: "ep1",
      }),
      makeEntry({
        entity_id: "PROP-001",
        entity_type: "prop",
        name: "剑匣",
        status: "candidate",
        first_seen: "ep1",
      }),
      makeEntry({
        entity_id: "EVT-001",
        entity_type: "event",
        name: "夜袭客栈",
        status: "confirmed",
        first_seen: "ep1",
      }),
    ],
    ...over,
  };
}

let getBibleMock: ReturnType<typeof vi.fn>;
let bibleConfirmMock: ReturnType<typeof vi.fn>;
let bibleEntryMock: ReturnType<typeof vi.fn>;

const apiMock = {
  getBible: (...a: unknown[]) => (getBibleMock as unknown as (...args: unknown[]) => unknown)(...a),
  bibleConfirm: (...a: unknown[]) => (bibleConfirmMock as unknown as (...args: unknown[]) => unknown)(...a),
  bibleEntry: (...a: unknown[]) => (bibleEntryMock as unknown as (...args: unknown[]) => unknown)(...a),
};
const runServiceMock = { api: apiMock };

beforeEach(() => {
  vi.clearAllMocks();
  const st = useWorkbench() as unknown as { runService: unknown };
  st.runService = runServiceMock;
  getBibleMock = vi.fn(async () => ({ bible: makeBible() }));
  bibleConfirmMock = vi.fn(async () => ({ bible: makeBible() }));
  bibleEntryMock = vi.fn(async () => ({ created: makeEntry(), bible: makeBible() }));
});

afterEach(() => vi.unstubAllGlobals());

async function mountPanel() {
  const wrapper = mount(BiblePanel, { props: { projectId: "proj_demo" } });
  await flushPromises();
  return wrapper;
}

describe("BiblePanel 渲染与分组（#542）", () => {
  it("mount → GET /bible(project_id) + 渲染四组 tab + 条目卡片", async () => {
    const w = await mountPanel();
    expect(getBibleMock).toHaveBeenCalledWith("proj_demo");
    // 四组 tab（人物/地点/道具/事件）
    const tabs = w.findAll(".bible-tab");
    expect(tabs.map((t) => t.text())).toEqual(["👤 人物2", "📍 地点1", "🎭 道具1", "📜 事件1"]);
    // 默认人物组：沈青崖（confirmed）+ 林雪（candidate）
    expect(w.text()).toContain("沈青崖");
    expect(w.text()).toContain("林雪");
    expect(w.findAll(".bible-badge.confirmed")).toHaveLength(1);
    expect(w.findAll(".bible-badge.candidate")).toHaveLength(1);
    // 别名 + 当前状态 + 最后出现 + 历史
    expect(w.text()).toContain("沈青山");
    expect(w.text()).toContain("风雪中推开庙门");
    expect(w.text()).toContain("ep1：进入破庙");
    // 统计条：5 条 / ✓ 3 已确认 / ◌ 2 候选 / v2
    expect(w.text()).toContain("5 条");
    expect(w.text()).toContain("✓ 3 已确认");
    expect(w.text()).toContain("◌ 2 候选");
    expect(w.text()).toContain("v2");
  });

  it("四组 tab 切换过滤 entity_type", async () => {
    const w = await mountPanel();
    const tabs = w.findAll(".bible-tab");
    await tabs[1].trigger("click"); // 📍 地点
    expect(w.text()).toContain("风雪山神庙");
    expect(w.text()).not.toContain("沈青崖");
    await tabs[2].trigger("click"); // 🎭 道具
    expect(w.text()).toContain("剑匣");
    expect(w.text()).not.toContain("风雪山神庙");
    await tabs[3].trigger("click"); // 📜 事件
    expect(w.text()).toContain("夜袭客栈");
    expect(w.text()).not.toContain("剑匣");
  });

  it("getBible 失败 → 错误横幅", async () => {
    getBibleMock.mockRejectedValueOnce(new Error("backend down"));
    const w = await mountPanel();
    expect(w.text()).toContain("backend down");
  });
});

describe("BiblePanel 操作（#542）", () => {
  it("✓ 确认候选 → bibleConfirm(projectId, entity_id, 'confirm') 并就地刷新 Bible", async () => {
    // CHAR-002 林雪为候选 → 确认后徽标变已确认
    const updated = makeBible();
    updated.entries = updated.entries.map((e) =>
      e.entity_id === "CHAR-002" ? { ...e, status: "confirmed" as const } : e,
    );
    bibleConfirmMock.mockResolvedValueOnce({ bible: updated });
    const w = await mountPanel();
    expect(w.findAll(".bible-confirm")).toHaveLength(1); // 只有候选林雪有确认按钮
    await w.find(".bible-confirm").trigger("click");
    await flushPromises();
    expect(bibleConfirmMock).toHaveBeenCalledWith("proj_demo", "CHAR-002", "confirm");
    // 就地刷新：已确认徽标从 1 → 2
    expect(w.findAll(".bible-badge.confirmed")).toHaveLength(2);
    expect(w.findAll(".bible-badge.candidate")).toHaveLength(0);
  });

  it("✓ 采纳属性建议 → bibleConfirm(..., 'accept_attribute', {key,value})", async () => {
    const w = await mountPanel();
    const sug = w.find(".bible-sug-accept");
    expect(sug.text()).toContain("采纳");
    await sug.trigger("click");
    await flushPromises();
    expect(bibleConfirmMock).toHaveBeenCalledWith("proj_demo", "CHAR-001", "accept_attribute", {
      key: "武器",
      value: "青铜剑",
    });
  });

  it("＋ 别名 → prompt → bibleConfirm(..., 'merge_alias', {alias})", async () => {
    vi.stubGlobal("prompt", vi.fn(() => "沈青"));
    const w = await mountPanel();
    const add = w.find(".bible-alias-add"); // CHAR-001 已有别名 → 卡片内 ＋ 按钮
    await add.trigger("click");
    await flushPromises();
    expect(bibleConfirmMock).toHaveBeenCalledWith("proj_demo", "CHAR-001", "merge_alias", {
      alias: "沈青",
    });
  });

  it("＋ 新建条目 → bibleEntry(projectId, entity_type, name, {aliases})", async () => {
    const w = await mountPanel();
    const newBtns = w.findAll(".bible-ops .icon-btn");
    await newBtns[1].trigger("click"); // ＋ 新建
    const inputs = w.findAll(".bible-new-input");
    expect(inputs).toHaveLength(2);
    await inputs[0].setValue("新人物");
    await inputs[1].setValue("小沈, 阿青");
    await w.find(".bible-new-ops .btn.script-go").trigger("click");
    await flushPromises();
    expect(bibleEntryMock).toHaveBeenCalledWith("proj_demo", "character", "新人物", {
      aliases: ["小沈", "阿青"],
    });
  });

  it("✎ 编辑静态属性 → bibleConfirm(..., 'edit_attributes', {attributes 空值剔除})", async () => {
    const w = await mountPanel();
    // 展开「静态属性（1）」
    await w.findAll(".bible-details summary")[0].trigger("click");
    await w.find(".bible-edit-btn").trigger("click");
    await flushPromises();
    const keyInput = w.find(".bible-edit-key");
    const valInput = w.find(".bible-edit-val");
    expect((keyInput.element as HTMLInputElement).value).toBe("身份");
    expect((valInput.element as HTMLInputElement).value).toBe("江湖刀客");
    // 改值 + 加一行空行（应被剔除）
    await valInput.setValue("无名刀客");
    await w.find(".bible-edit-ops .btn.ghost").trigger("click"); // ＋ 行
    const rows = w.findAll(".bible-edit-row");
    await rows[1].find(".bible-edit-key").setValue("   ");
    await rows[1].find(".bible-edit-val").setValue("");
    await w.find(".bible-edit-ops .btn.script-go").trigger("click");
    await flushPromises();
    expect(bibleConfirmMock).toHaveBeenCalledWith("proj_demo", "CHAR-001", "edit_attributes", {
      attributes: { 身份: "无名刀客" },
    });
  });
});
