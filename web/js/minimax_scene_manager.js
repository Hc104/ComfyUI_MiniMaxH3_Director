/**
 * Scene Manager 面板（待办⑥）：Project → Scene（一级对象）→ Shot。
 *
 * 设计决策（用户 2026-08-07 AskUserQuestion 三项确认）：
 * - 入口：顶部导航 tab「场景管理」（timeline.js buildDOM 已建 navBar/scenesPane）。
 * - 数据模型：timeline.scenes 新块（后端 plan.py SceneGroup / gen_timeline._load_scene_groups）。
 * - 实施范围：完整落地（数据模型 + 面板 + 段归属场景 + 每场景导出 + 全片按场景拼接）。
 *
 * 面板能力：
 * - 场景卡片列表：新建 / 重命名 / 删除 / 排序（上移/下移，按 order 排序）。
 * - 场景详情：名称 / 地点 / 时间 / 素材组（角色/场景/道具/风格参考）+ 镜头列表 + 每场景导出。
 * - 素材组：每个场景独立 assets（cast/locations/props/styles），后端优先场景素材组，其次全局资产库。
 * - 镜头列表：列出归属本场景的 segments（Shot 序号 + 时长 + 提示词摘要）。
 * - 每场景导出：预设 exportMode=scene + runSelection=[本场景镜头]，随后触发 ComfyUI 运行。
 *
 * 状态持久化：全部写 timeline.scenes 与 segment.sceneId（后端 gen_timeline 据此解析）。
 */
import { api } from "../../scripts/api.js";
import { t } from "./minimax_i18n.js";

export const SCENE_MANAGER_STYLES = `
.bd-sc{display:flex;flex-direction:column;gap:8px;width:100%;box-sizing:border-box}
.bd-sc-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.bd-sc-title{color:#f0f0f0;font-size:12px;font-weight:650;letter-spacing:.02em}
.bd-sc-hint{color:#777;font-size:10px;line-height:1.4}
.bd-sc-grid{display:flex;flex-wrap:wrap;gap:8px}
.bd-sc-card{position:relative;background:#181818;border:1px solid #2c2c2c;border-radius:8px;padding:8px 10px;min-width:150px;max-width:240px;cursor:pointer;display:flex;flex-direction:column;gap:5px;box-sizing:border-box;transition:border-color .12s,background .12s}
.bd-sc-card:hover{border-color:#444;background:#1e1e1e}
.bd-sc-card.active{border-color:#4fff8f;background:#16251e}
.bd-sc-card-head{display:flex;align-items:center;gap:6px}
.bd-sc-card-num{color:#4fff8f;font-size:10px;font-weight:700;opacity:.8}
.bd-sc-card-name{color:#e8e8e8;font-size:12px;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bd-sc-card-del{color:#777;font-size:12px;line-height:1;cursor:pointer;padding:2px;flex-shrink:0}
.bd-sc-card-del:hover{color:#f88}
.bd-sc-card-meta{color:#9a9a9a;font-size:10px;line-height:1.35;min-height:0}
.bd-sc-card-thumbs{display:flex;gap:4px;flex-wrap:wrap}
.bd-sc-card-thumb{width:34px;height:34px;border-radius:5px;object-fit:cover;border:1px solid #333;background:#0c0c0c}
.bd-sc-card-thumb.empty{display:flex;align-items:center;justify-content:center;color:#555;font-size:9px}
.bd-sc-card-foot{display:flex;align-items:center;justify-content:space-between;gap:6px;color:#8a8a8a;font-size:10px}
.bd-sc-card-ops{display:flex;gap:2px}
.bd-sc-card-op{background:#222;color:#aaa;border:1px solid #333;border-radius:3px;padding:1px 5px;font-size:10px;line-height:1.3;cursor:pointer}
.bd-sc-card-op:hover{background:#333;color:#fff}
.bd-sc-empty{color:#666;font-size:11px;padding:14px;border:1px dashed #2e2e2e;border-radius:8px;line-height:1.6;width:100%;box-sizing:border-box}
.bd-sc-detail{display:flex;flex-direction:column;gap:8px;background:#151515;border:1px solid #2a2a2a;border-radius:10px;padding:10px 12px;box-sizing:border-box}
.bd-sc-detail-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.bd-sc-detail-title{color:#e8e8e8;font-size:12px;font-weight:650}
.bd-sc-fields{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.bd-sc-field{display:flex;align-items:center;gap:4px;color:#9a9a9a;font-size:10px}
.bd-sc-field input{background:#1f1f1f;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:4px 6px;font-size:11px;width:110px;box-sizing:border-box}
.bd-sc-field input:focus{outline:none;border-color:#4fff8f}
.bd-sc-assets-title{color:#c8c8c8;font-size:11px;font-weight:600;margin-top:2px}
.bd-sc-assets{display:flex;flex-direction:column;gap:6px}
.bd-sc-assets-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.bd-sc-assets-label{color:#9a9a9a;font-size:10px;min-width:52px}
.bd-sc-assets-items{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
.bd-sc-asset{position:relative;width:44px;height:44px;border:1px solid #3a3a3a;border-radius:6px;overflow:hidden;background:#0c0c0c;display:flex;align-items:flex-end;justify-content:center;cursor:default;flex-shrink:0}
.bd-sc-asset img{width:100%;height:100%;object-fit:cover;display:block}
.bd-sc-asset .cap{position:absolute;left:0;right:0;bottom:0;background:rgba(0,0,0,.62);color:#ddd;font-size:8px;text-align:center;padding:1px 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.bd-sc-asset .x{position:absolute;top:1px;right:1px;background:rgba(0,0,0,.65);color:#f88;font-size:10px;line-height:1;padding:1px 3px;border-radius:3px;cursor:pointer}
.bd-sc-asset .x:hover{background:rgba(120,20,20,.8)}
.bd-sc-assets-def{background:#1f1f1f;color:#bbb;border:1px solid #333;border-radius:4px;font-size:10px;max-width:96px;padding:2px 3px}
.bd-sc-assets-add{background:#1a2a22;color:#4fff8f;border:1px solid #2f5a44;border-radius:4px;padding:2px 8px;font-size:10px;cursor:pointer;flex-shrink:0}
.bd-sc-assets-add:hover{background:#234029}
.bd-sc-shots-title{color:#c8c8c8;font-size:11px;font-weight:600;margin-top:2px}
.bd-sc-shots{display:flex;flex-direction:column;gap:3px;max-height:150px;overflow:auto}
.bd-sc-shot{display:flex;align-items:center;gap:8px;background:#1c1c1c;border:1px solid #2a2a2a;border-radius:5px;padding:4px 7px}
.bd-sc-shot-idx{color:#4fff8f;font-size:10px;font-weight:700;min-width:44px;flex-shrink:0}
.bd-sc-shot-dur{color:#8a8a8a;font-size:10px;min-width:34px;flex-shrink:0}
.bd-sc-shot-prompt{color:#b8b8b8;font-size:10px;line-height:1.3;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bd-sc-shot-empty{color:#666;font-size:10px;padding:6px;border:1px dashed #2a2a2a;border-radius:5px}
`;

/* ---------------------------------------------------------------------------
 * 上传 / 预览辅助（轻量副本，与 image_batch.js 行为一致）
 * ------------------------------------------------------------------------- */

function pickFile(accept, onFile) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none";
    const cleanup = () => input.remove();
    input.onchange = () => {
        const file = input.files?.[0];
        cleanup();
        if (file) onFile(file);
    };
    input.addEventListener("cancel", cleanup);
    document.body.appendChild(input);
    input.click();
}

async function uploadImage(file) {
    const body = new FormData();
    body.append("image", file);
    body.append("type", "input");
    body.append("overwrite", "true");
    const resp = await api.fetchApi("/upload/image", { method: "POST", body });
    if (!resp.ok) throw new Error(await resp.text() || `Upload failed (${resp.status})`);
    return resp.json();
}

function relPath(upload) {
    const name = upload.name || upload.filename;
    const sub = (upload.subfolder || "").replace(/\\/g, "/").replace(/\/$/, "");
    return sub ? `${sub}/${name}` : name;
}

function viewUrl(imageFile) {
    const norm = String(imageFile || "").replace(/\\/g, "/");
    const slash = norm.lastIndexOf("/");
    const filename = slash >= 0 ? norm.slice(slash + 1) : norm;
    const subfolder = slash >= 0 ? norm.slice(0, slash) : "";
    const params = new URLSearchParams({ filename, type: "input" });
    if (subfolder) params.set("subfolder", subfolder);
    return api.apiURL(`/view?${params.toString()}`);
}

function fileBaseName(path) {
    // File 对象（或任何带 name 的资产对象）优先取 .name，避免 String(File) === "[object File]"
    // 污染资产序列化。仅字符串路径走路径切分。
    if (path && typeof path === "object") {
        return typeof path.name === "string" && path.name ? path.name : "";
    }
    const s = String(path || "").replace(/\\/g, "/");
    const base = s.split("/").pop() || s;
    return base === "[object File]" ? "" : base;
}

function sanitizeAssetName(asset) {
    // 防御旧数据：前端曾误存 "[object File]" 作为资产名（String(File) 的结果）。
    // 加载/读取时清洗，空名回退 imageFile 文件名。
    if (!asset || typeof asset !== "object") return asset;
    const name = asset.name;
    if (name && name !== "[object File]") return asset;
    const img = String(asset.imageFile || "").replace(/\\/g, "/").split("/").pop() || "";
    asset.name = img || "";
    return asset;
}

function migrateLocationAssetKey(assetsBlock) {
    // 资产键统一：只保留 locations（复数）。旧数据 assets.location（单数）迁移到 locations。
    if (!assetsBlock || typeof assetsBlock !== "object") return assetsBlock;
    const legacy = assetsBlock.location;
    if (Array.isArray(legacy)) {
        if (!Array.isArray(assetsBlock.locations)) {
            assetsBlock.locations = legacy;
        } else if (legacy.length) {
            const ids = new Set(assetsBlock.locations.map((x) => x && x.id).filter(Boolean));
            for (const item of legacy) {
                if (item && typeof item === "object" && !(item.id && ids.has(item.id))) {
                    assetsBlock.locations.push(item);
                    if (item.id) ids.add(item.id);
                }
            }
        }
    }
    delete assetsBlock.location;
    return assetsBlock;
}

function newAssetId() {
    return "sca" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

function newSceneId() {
    return "sc" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function formatMediaDuration(sec) {
    if (!Number.isFinite(sec) || sec < 0) return "--:--";
    const total = Math.max(0, Math.round(sec));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
}

/* ---------------------------------------------------------------------------
 * timeline.scenes 块读写
 * ------------------------------------------------------------------------- */

export function getScenesBlock(editor) {
    const timeline = editor.timeline || (editor.timeline = {});
    if (!Array.isArray(timeline.scenes)) timeline.scenes = [];
    for (const sc of timeline.scenes) {
        if (!sc || typeof sc !== "object") continue;
        if (!sc.id) sc.id = newSceneId();
        sc.name = sc.name ?? "";
        sc.location = sc.location ?? "";
        sc.time = sc.time ?? "";
        sc.order = sc.order ?? 0;
        sc.assets = sc.assets || {};
        // 资产键统一：旧数据 assets.location（单数）→ assets.locations（复数）。
        migrateLocationAssetKey(sc.assets);
        // 场景素材组四类目 + 资产名防御清洗（拒绝 [object File] 脏名）。
        for (const kind of ["cast", "locations", "props", "styles"]) {
            if (!Array.isArray(sc.assets[kind])) sc.assets[kind] = [];
            sc.assets[kind] = sc.assets[kind]
                .filter((x) => x && typeof x === "object")
                .map(sanitizeAssetName);
        }
        sc.defaultCastId = sc.defaultCastId ?? "";
        sc.defaultLocationId = sc.defaultLocationId ?? "";
    }
    return timeline.scenes;
}

function commitEditor(editor) {
    if (!editor) return;
    editor.commit?.(false, { syncTimeline: true });
    editor.updateDomWidgetHeight?.();
}

/** 渲染面板（重渲染列表 + 详情）。 */
function renderAll(editor, ui) {
    renderSceneList(editor, ui);
    renderSceneDetail(editor, ui, ui._selectedSceneId);
}

/* ---------------------------------------------------------------------------
 * 场景 CRUD
 * ------------------------------------------------------------------------- */

export function addScene(editor) {
    const scenes = getScenesBlock(editor);
    const maxOrder = scenes.reduce((m, s) => Math.max(m, s.order ?? 0), 0);
    scenes.push({
        id: newSceneId(),
        name: `Scene ${String(scenes.length + 1).padStart(2, "0")}`,
        location: "",
        time: "",
        order: maxOrder + 1,
        assets: { cast: [], locations: [], props: [], styles: [] },
        defaultCastId: "",
        defaultLocationId: "",
    });
    commitEditor(editor);
    return scenes[scenes.length - 1];
}

export function deleteScene(editor, sceneId) {
    const scenes = getScenesBlock(editor);
    const idx = scenes.findIndex((s) => s.id === sceneId);
    if (idx < 0) return;
    scenes.splice(idx, 1);
    // 架构固底①：Scene 是 Shot 的父级，Shot 必须属于某个 Scene。
    // 删除场景后其下镜头归入剩余 order 最小的场景；无场景时留空（等用户重建）。
    const remaining = scenes.slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const fallbackId = remaining[0]?.id || "";
    for (const seg of editor.timeline?.segments || []) {
        if (seg.sceneId === sceneId) seg.sceneId = fallbackId;
    }
    commitEditor(editor);
}

export function moveScene(editor, sceneId, dir) {
    const scenes = getScenesBlock(editor);
    scenes.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const idx = scenes.findIndex((s) => s.id === sceneId);
    const j = idx + (dir > 0 ? 1 : -1);
    if (idx < 0 || j < 0 || j >= scenes.length) return;
    const tmp = scenes[idx].order;
    scenes[idx].order = scenes[j].order;
    scenes[j].order = tmp;
    commitEditor(editor);
}

/* ---------------------------------------------------------------------------
 * 素材组资产（场景独立 assets）
 * ------------------------------------------------------------------------- */

function uploadSceneAsset(editor, scene, kind) {
    pickFile("image/*,.jpg,.jpeg,.png,.webp,.bmp,.gif", async (file) => {
        try {
            if (!file?.type?.startsWith("image/") && !/\.(jpe?g|png|webp|bmp|gif)$/i.test(file.name || "")) {
                throw new Error("Not an image file");
            }
            const uploaded = await uploadImage(file);
            const imageFile = relPath(uploaded);
            if (!imageFile) throw new Error("Upload returned empty filename");
            const sc = (editor.timeline?.scenes || []).find((s) => s.id === scene.id) || scene;
            if (!sc.assets) sc.assets = {};
            if (!Array.isArray(sc.assets[kind])) sc.assets[kind] = [];
            const asset = { id: newAssetId(), name: fileBaseName(file) || file.name || "", imageFile };
            sc.assets[kind].push(asset);
            if (kind === "cast" && !sc.defaultCastId) sc.defaultCastId = asset.id;
            if (kind === "locations" && !sc.defaultLocationId) sc.defaultLocationId = asset.id;
            commitEditor(editor);
        } catch (err) {
            console.error("[MiniMax H3Director] scene asset upload failed:", err);
            alert(t("upload.alertFailed", { err: err?.message || err }));
        }
    });
}

function removeSceneAsset(editor, scene, kind, assetId) {
    const sc = (editor.timeline?.scenes || []).find((s) => s.id === scene.id) || scene;
    const list = sc.assets?.[kind];
    if (!Array.isArray(list)) return;
    const idx = list.findIndex((x) => x.id === assetId);
    if (idx < 0) return;
    list.splice(idx, 1);
    if (kind === "cast" && sc.defaultCastId === assetId) sc.defaultCastId = sc.assets.cast?.[0]?.id || "";
    if (kind === "locations" && sc.defaultLocationId === assetId) sc.defaultLocationId = sc.assets.locations?.[0]?.id || "";
    commitEditor(editor);
}

function renderSceneAssetItem(editor, scene, kind, asset) {
    const el = document.createElement("div");
    el.className = "bd-sc-asset";
    el.title = asset.name || asset.id || "";
    const img = document.createElement("img");
    img.src = viewUrl(asset.imageFile);
    img.draggable = false;
    el.appendChild(img);
    const cap = document.createElement("span");
    cap.className = "cap";
    cap.textContent = asset.name || asset.id || "?";
    cap.title = t("batch.assets.rename");
    el.appendChild(cap);
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "×";
    x.title = t("batch.assets.remove");
    x.onclick = (e) => {
        e.stopPropagation();
        removeSceneAsset(editor, scene, kind, asset.id);
    };
    el.appendChild(x);
    const doRename = () => {
        const input = document.createElement("input");
        input.style.cssText = "position:absolute;left:0;right:0;bottom:0;background:#222;color:#eee;border:1px solid #4fff8f;border-radius:3px;font-size:9px;width:100%;box-sizing:border-box;z-index:3";
        input.value = asset.name || asset.id || "";
        input.spellcheck = false;
        const commit = () => {
            const v = input.value.trim();
            if (v) asset.name = v;
            commitEditor(editor);
        };
        const cancel = () => input.remove();
        input.onkeydown = (e) => {
            e.stopPropagation();
            if (e.key === "Enter") { commit(); }
            else if (e.key === "Escape") { cancel(); }
        };
        input.onblur = () => { input.remove(); };
        el.appendChild(input);
        input.focus();
        input.select();
    };
    cap.onclick = (e) => {
        e.stopPropagation();
        doRename();
    };
    return el;
}

const ASSET_KINDS = [
    ["cast", "scene.assetsCast"],
    ["locations", "scene.assetsLocations"],
    ["props", "scene.assetsProps"],
    ["styles", "scene.assetsStyles"],
];

/* ---------------------------------------------------------------------------
 * 渲染
 * ------------------------------------------------------------------------- */

function sceneShots(editor, sceneId) {
    return (editor.timeline?.segments || [])
        .map((s, i) => ({ seg: s, index: i }))
        .filter(({ seg }) => seg.sceneId === sceneId);
}

function sceneShotDuration(editor, seg) {
    const fc = seg.frameCount ?? seg.length ?? 0;
    if (seg.durationSec != null && Number.isFinite(Number(seg.durationSec))) {
        return Number(seg.durationSec);
    }
    if (!fc) return 0;
    const fps = Math.max(0.001, editor.getFrameRate?.() || 24);
    return fc / fps;
}

function renderSceneList(editor, ui) {
    const grid = ui.grid;
    if (!grid) return;
    grid.innerHTML = "";
    const scenes = getScenesBlock(editor).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    if (!scenes.length) {
        const empty = document.createElement("div");
        empty.className = "bd-sc-empty";
        empty.textContent = t("scene.empty");
        grid.appendChild(empty);
        return;
    }
    for (const sc of scenes) {
        const shots = sceneShots(editor, sc.id);
        const card = document.createElement("div");
        card.className = "bd-sc-card" + (sc.id === ui._selectedSceneId ? " active" : "");
        card.dataset.id = sc.id;
        card.title = t("scene.selectHint");

        const head = document.createElement("div");
        head.className = "bd-sc-card-head";
        const num = document.createElement("span");
        num.className = "bd-sc-card-num";
        num.textContent = String(scenes.indexOf(sc) + 1).padStart(2, "0");
        const name = document.createElement("span");
        name.className = "bd-sc-card-name";
        name.textContent = sc.name || `Scene ${String(scenes.indexOf(sc) + 1).padStart(2, "0")}`;
        const del = document.createElement("span");
        del.className = "bd-sc-card-del";
        del.textContent = "×";
        del.title = t("scene.delete");
        del.onclick = (e) => {
            e.stopPropagation();
            deleteScene(editor, sc.id);
        };
        head.append(num, name, del);
        card.appendChild(head);

        const meta = document.createElement("div");
        meta.className = "bd-sc-card-meta";
        const metaParts = [sc.location, sc.time].filter(Boolean);
        meta.textContent = metaParts.join(" · ") || t("scene.noLoc");
        card.appendChild(meta);

        const thumbs = document.createElement("div");
        thumbs.className = "bd-sc-card-thumbs";
        const thumbSrcs = [];
        const defCast = (sc.assets?.cast || []).find((a) => a.id === sc.defaultCastId) || sc.assets?.cast?.[0];
        const defLoc = (sc.assets?.locations || []).find((a) => a.id === sc.defaultLocationId) || sc.assets?.locations?.[0];
        if (defCast?.imageFile) thumbSrcs.push(defCast.imageFile);
        if (defLoc?.imageFile) thumbSrcs.push(defLoc.imageFile);
        for (const src of thumbSrcs.slice(0, 3)) {
            const im = document.createElement("img");
            im.className = "bd-sc-card-thumb";
            im.src = viewUrl(src);
            thumbs.appendChild(im);
        }
        if (!thumbSrcs.length) {
            const empty = document.createElement("span");
            empty.className = "bd-sc-card-thumb empty";
            empty.textContent = t("scene.noThumb");
            thumbs.appendChild(empty);
        }
        card.appendChild(thumbs);

        const foot = document.createElement("div");
        foot.className = "bd-sc-card-foot";
        const durTotal = shots.reduce((s, { seg }) => s + sceneShotDuration(editor, seg), 0);
        const stat = document.createElement("span");
        stat.textContent = `${shots.length} ${t("scene.shots")} · ${formatMediaDuration(durTotal)}`;
        const ops = document.createElement("span");
        ops.className = "bd-sc-card-ops";
        const mkOp = (label, title, fn) => {
            const b = document.createElement("button");
            b.className = "bd-sc-card-op";
            b.textContent = label;
            b.title = title;
            b.onclick = (e) => { e.stopPropagation(); fn(); };
            ops.appendChild(b);
        };
        mkOp("↑", t("scene.up"), () => moveScene(editor, sc.id, -1));
        mkOp("↓", t("scene.down"), () => moveScene(editor, sc.id, 1));
        foot.append(stat, ops);
        card.appendChild(foot);

        card.onclick = () => {
            ui._selectedSceneId = sc.id;
            renderSceneList(editor, ui);
            renderSceneDetail(editor, ui, sc.id);
        };
        grid.appendChild(card);
    }
}

function renderSceneDetail(editor, ui, sceneId) {
    const detail = ui.detail;
    if (!detail) return;
    detail.innerHTML = "";
    const sc = (editor.timeline?.scenes || []).find((s) => s.id === sceneId);
    if (!sc) {
        const empty = document.createElement("div");
        empty.className = "bd-sc-empty";
        empty.textContent = t("scene.detailEmpty");
        detail.appendChild(empty);
        return;
    }

    const head = document.createElement("div");
    head.className = "bd-sc-detail-head";
    const title = document.createElement("span");
    title.className = "bd-sc-detail-title";
    title.textContent = t("scene.detail");
    const exportBtn = document.createElement("button");
    exportBtn.className = "bd-btn bd-btn-primary bd-btn-sm";
    exportBtn.textContent = t("scene.export");
    exportBtn.title = t("scene.exportHint");
    exportBtn.onclick = () => exportScene(editor, sc.id);
    head.append(title, exportBtn);
    detail.appendChild(head);

    const fields = document.createElement("div");
    fields.className = "bd-sc-fields";
    const mkField = (label, value, onChange) => {
        const wrap = document.createElement("label");
        wrap.className = "bd-sc-field";
        const lab = document.createElement("span");
        lab.textContent = label;
        const input = document.createElement("input");
        input.value = value ?? "";
        input.spellcheck = false;
        input.onchange = () => { onChange(input.value.trim()); };
        input.onkeydown = (e) => {
            e.stopPropagation();
            if (e.key === "Enter") input.blur();
        };
        wrap.append(lab, input);
        fields.appendChild(wrap);
    };
    mkField(t("scene.name"), sc.name, (v) => { sc.name = v; commitEditor(editor); });
    mkField(t("scene.location"), sc.location, (v) => { sc.location = v; commitEditor(editor); });
    mkField(t("scene.time"), sc.time, (v) => { sc.time = v; commitEditor(editor); });
    detail.appendChild(fields);

    const assetsTitle = document.createElement("div");
    assetsTitle.className = "bd-sc-assets-title";
    assetsTitle.textContent = t("scene.assets");
    detail.appendChild(assetsTitle);

    const assetsBox = document.createElement("div");
    assetsBox.className = "bd-sc-assets";
    for (const [kind, i18nKey] of ASSET_KINDS) {
        const row = document.createElement("div");
        row.className = "bd-sc-assets-row";
        const label = document.createElement("span");
        label.className = "bd-sc-assets-label";
        label.textContent = t(i18nKey);
        const items = document.createElement("div");
        items.className = "bd-sc-assets-items";
        const list = sc.assets?.[kind] || [];
        for (const asset of list) {
            items.appendChild(renderSceneAssetItem(editor, sc, kind, asset));
        }
        row.appendChild(label);
        row.appendChild(items);
        // 默认选择（仅 cast / locations 支持默认）。
        if (kind === "cast" || kind === "locations") {
            const def = document.createElement("select");
            def.className = "bd-sc-assets-def";
            const none = document.createElement("option");
            none.value = "";
            none.textContent = t("scene.noDefault");
            def.appendChild(none);
            for (const asset of list) {
                const opt = document.createElement("option");
                opt.value = asset.id;
                opt.textContent = asset.name || asset.id;
                const cur = kind === "cast" ? sc.defaultCastId : sc.defaultLocationId;
                if (asset.id === cur) opt.selected = true;
                def.appendChild(opt);
            }
            def.value = kind === "cast" ? sc.defaultCastId : sc.defaultLocationId;
            def.onchange = () => {
                if (kind === "cast") sc.defaultCastId = def.value;
                else sc.defaultLocationId = def.value;
                commitEditor(editor);
            };
            row.appendChild(def);
        }
        const add = document.createElement("button");
        add.className = "bd-sc-assets-add";
        add.textContent = "+";
        add.title = t("scene.assetsAdd");
        add.onclick = () => uploadSceneAsset(editor, sc, kind);
        row.appendChild(add);
        assetsBox.appendChild(row);
    }
    detail.appendChild(assetsBox);

    const shotsTitle = document.createElement("div");
    shotsTitle.className = "bd-sc-shots-title";
    shotsTitle.textContent = t("scene.shotsTitle");
    detail.appendChild(shotsTitle);

    const shotsBox = document.createElement("div");
    shotsBox.className = "bd-sc-shots";
    const shots = sceneShots(editor, sc.id);
    if (!shots.length) {
        const empty = document.createElement("div");
        empty.className = "bd-sc-shot-empty";
        empty.textContent = t("scene.shotsEmpty");
        shotsBox.appendChild(empty);
    } else {
        for (const { seg, index } of shots) {
            const row = document.createElement("div");
            row.className = "bd-sc-shot";
            const idx = document.createElement("span");
            idx.className = "bd-sc-shot-idx";
            idx.textContent = `Shot ${String(index + 1).padStart(2, "0")}`;
            const dur = document.createElement("span");
            dur.className = "bd-sc-shot-dur";
            dur.textContent = formatMediaDuration(sceneShotDuration(editor, seg));
            const prompt = document.createElement("span");
            prompt.className = "bd-sc-shot-prompt";
            prompt.textContent = (seg.prompt || "").replace(/\s+/g, " ").trim() || "—";
            row.append(idx, dur, prompt);
            shotsBox.appendChild(row);
        }
    }
    detail.appendChild(shotsBox);
}

/* ---------------------------------------------------------------------------
 * 每场景导出：预设 exportMode=scene + runSelection=本场景镜头 → 触发运行
 * ------------------------------------------------------------------------- */

export function exportScene(editor, sceneId) {
    if (!editor?.timeline) return;
    const indices = (editor.timeline.segments || [])
        .map((s, i) => (s.sceneId === sceneId ? i : -1))
        .filter((i) => i >= 0);
    editor.timeline.output = editor.timeline.output || {};
    editor.timeline.output.exportMode = "scene";
    editor.timeline.runSelectEnabled = indices.length > 0;
    editor.timeline.runSelection = indices;
    commitEditor(editor);
    try {
        // 与 ComfyUI 顶部 Run 相同路径：flush 后 queue 当前图。
        window.app?.queuePrompt?.();
    } catch (err) {
        console.warn("[MiniMax H3Director] auto-run after scene export setup failed:", err);
    }
}

/* ---------------------------------------------------------------------------
 * 面板挂载（mountDirectorPanel 同范式）
 * @returns {{el: HTMLElement, syncFromWidgets: Function, attachEditor: Function}}
 * ------------------------------------------------------------------------- */

export function mountSceneManagerPanel(parentEl, editor) {
    const el = document.createElement("div");
    el.className = "bd-sc";
    el.innerHTML = `
        <div class="bd-sc-toolbar">
            <div style="display:flex;flex-direction:column;gap:2px">
                <span class="bd-sc-title" data-i18n="scene.title">场景管理</span>
                <span class="bd-sc-hint" data-i18n="scene.hint">场景 = 素材组（角色/场景/道具/风格参考）+ 镜头（Shot）</span>
            </div>
            <button type="button" class="bd-btn bd-btn-primary" data-a="scene-add" data-i18n="scene.add">+ 新建场景</button>
        </div>
        <div class="bd-sc-grid" data-r="scenes-grid"></div>
        <div class="bd-sc-detail" data-r="scenes-detail"></div>
    `;
    parentEl.appendChild(el);

    const ui = {
        grid: el.querySelector('[data-r="scenes-grid"]'),
        detail: el.querySelector('[data-r="scenes-detail"]'),
        addBtn: el.querySelector('[data-a="scene-add"]'),
        _selectedSceneId: "",
    };
    let _editor = editor || null;

    ui.addBtn.addEventListener("click", () => {
        const sc = addScene(_editor);
        ui._selectedSceneId = sc?.id || "";
        renderAll(_editor, ui);
    });

    /** 绑定真实 editor（挂载时可能还没有）。 */
    function attachEditor(ed) {
        _editor = ed || null;
        if (_editor?.timeline) syncFromWidgets();
    }

    /** 从 timeline.scenes 重渲染面板（重渲染/加载后调用）。 */
    function syncFromWidgets() {
        if (!_editor?.timeline) return;
        getScenesBlock(_editor);
        // 选中场景可能已删除 → 回落第一个。
        const scenes = _editor.timeline.scenes || [];
        if (!scenes.some((s) => s.id === ui._selectedSceneId)) {
            ui._selectedSceneId = scenes[0]?.id || "";
        }
        renderAll(_editor, ui);
    }

    /** 选中指定场景（素材库/导出中心跳转用）。 */
    function selectScene(sceneId) {
        const scenes = _editor?.timeline?.scenes || [];
        if (!scenes.some((s) => s.id === sceneId)) return;
        ui._selectedSceneId = sceneId;
        renderAll(_editor, ui);
    }

    return { el, syncFromWidgets, attachEditor, selectScene };
}
