/**
 * 导出中心 + 素材库总览（待办⑥ 导演系统一级对象配套）。
 *
 * - 导出中心（nav-export）：每场景导出（SceneNN.mp4）+ 全片导出（Movie.mp4，ffmpeg 直拼）。
 * - 素材库（nav-assets）：全局资产库四类目（角色/场景/道具/风格参考）+ 各场景素材组总览。
 *
 * 复用：
 * - exportScene 来自 minimax_scene_manager.js（预设 exportMode=scene + runSelection=本场景镜头）。
 * - 全片导出：exportMode=movie（后端走 ffmpeg concat 场景 mp4 → Movie.mp4，几乎零内存）。
 *
 * 状态持久化：只读面板，不改 timeline 结构；点击操作通过 editor.commit + queuePrompt 触发。
 */
import { api } from "../../scripts/api.js";
import { t } from "./minimax_i18n.js";
import { exportScene } from "./minimax_scene_manager.js";

export const EXPORT_CENTER_STYLES = `
/* 素材库总览 */
.bd-al{display:flex;flex-direction:column;gap:10px;width:100%;box-sizing:border-box}
.bd-al-block{display:flex;flex-direction:column;gap:6px;background:#151515;border:1px solid #2a2a2a;border-radius:10px;padding:10px 12px;box-sizing:border-box}
.bd-al-block-title{color:#c8c8c8;font-size:11px;font-weight:650;letter-spacing:.02em}
.bd-al-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.bd-al-label{color:#9a9a9a;font-size:10px;min-width:66px;flex-shrink:0}
.bd-al-thumbs{display:flex;gap:4px;flex-wrap:wrap;flex:1;min-width:0}
.bd-al-thumb{width:40px;height:40px;border-radius:6px;object-fit:cover;border:1px solid #333;background:#0c0c0c}
.bd-al-thumb.empty{display:flex;align-items:center;justify-content:center;color:#555;font-size:9px;border-style:dashed}
.bd-al-count{color:#777;font-size:10px;flex-shrink:0}
.bd-al-edit{background:#1a2a22;color:#4fff8f;border:1px solid #2f5a44;border-radius:4px;padding:2px 8px;font-size:10px;cursor:pointer;flex-shrink:0}
.bd-al-edit:hover{background:#234029}
.bd-al-scene-row{display:flex;align-items:center;gap:8px;background:#1c1c1c;border:1px solid #2a2a2a;border-radius:6px;padding:5px 8px;cursor:pointer;flex-wrap:wrap}
.bd-al-scene-row:hover{border-color:#4a4a4a}
.bd-al-scene-num{color:#4fff8f;font-size:10px;font-weight:700;flex-shrink:0}
.bd-al-scene-name{color:#e0e0e0;font-size:11px;font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bd-al-scene-meta{color:#8a8a8a;font-size:10px;flex:1;min-width:0;text-align:right}

/* 导出中心 */
.bd-ex{display:flex;flex-direction:column;gap:10px;width:100%;box-sizing:border-box}
.bd-ex-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.bd-ex-title{color:#f0f0f0;font-size:12px;font-weight:650;letter-spacing:.02em}
.bd-ex-hint{color:#777;font-size:10px;line-height:1.5}
.bd-ex-scenes{display:flex;flex-direction:column;gap:5px}
.bd-ex-scene{display:flex;align-items:center;gap:8px;background:#181818;border:1px solid #2a2a2a;border-radius:8px;padding:7px 9px;flex-wrap:wrap}
.bd-ex-scene:hover{border-color:#3d3d3d}
.bd-ex-scene-num{color:#4fff8f;font-size:11px;font-weight:700;min-width:34px;flex-shrink:0}
.bd-ex-scene-name{color:#e0e0e0;font-size:11px;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bd-ex-scene-stat{color:#8a8a8a;font-size:10px;flex-shrink:0}
.bd-ex-btn{background:#1a2a22;color:#4fff8f;border:1px solid #2f5a44;border-radius:5px;padding:4px 10px;font-size:10px;cursor:pointer;flex-shrink:0}
.bd-ex-btn:hover{background:#234029}
.bd-ex-btn:disabled{opacity:.4;cursor:not-allowed}
.bd-ex-movie{display:flex;flex-direction:column;gap:5px;background:#151515;border:1px solid #2a2a2a;border-radius:10px;padding:10px 12px;box-sizing:border-box}
.bd-ex-movie-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.bd-ex-movie-title{color:#c8c8c8;font-size:11px;font-weight:650}
.bd-ex-empty{color:#666;font-size:11px;padding:12px;border:1px dashed #2e2e2e;border-radius:8px;line-height:1.6;width:100%;box-sizing:border-box}
`;

/* ---------------------------------------------------------------------------
 * 小工具（只读展示用，与 scene_manager.js 一致）
 * ------------------------------------------------------------------------- */

function viewUrl(imageFile) {
    const norm = String(imageFile || "").replace(/\\/g, "/");
    const slash = norm.lastIndexOf("/");
    const filename = slash >= 0 ? norm.slice(slash + 1) : norm;
    const subfolder = slash >= 0 ? norm.slice(0, slash) : "";
    const params = new URLSearchParams({ filename, type: "input" });
    if (subfolder) params.set("subfolder", subfolder);
    return api.apiURL(`/view?${params.toString()}`);
}

function commitEditor(editor) {
    if (!editor) return;
    editor.commit?.(false, { syncTimeline: true });
    editor.updateDomWidgetHeight?.();
}

function getScenesBlock(editor) {
    const timeline = editor.timeline || (editor.timeline = {});
    if (!Array.isArray(timeline.scenes)) timeline.scenes = [];
    return timeline.scenes;
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

function getAssetsBlock(editor) {
    const timeline = editor.timeline || (editor.timeline = {});
    if (!timeline.assets || typeof timeline.assets !== "object") timeline.assets = {};
    const a = timeline.assets;
    migrateLocationAssetKey(a);
    if (!Array.isArray(a.cast)) a.cast = [];
    if (!Array.isArray(a.locations)) a.locations = [];
    if (!Array.isArray(a.props)) a.props = [];
    if (!Array.isArray(a.styles)) a.styles = [];
    return a;
}

const ASSET_KINDS = [
    ["cast", "panel.batch.castLabel"],
    ["locations", "panel.batch.locLabel"],
    ["props", "panel.batch.propLabel"],
    ["styles", "panel.batch.styleLabel"],
];

function sceneShotCount(editor, sceneId) {
    return (editor.timeline?.segments || []).filter((s) => s.sceneId === sceneId).length;
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

function sceneTotalDuration(editor, sceneId) {
    return (editor.timeline?.segments || [])
        .filter((s) => s.sceneId === sceneId)
        .reduce((sum, seg) => sum + sceneShotDuration(editor, seg), 0);
}

function formatMediaDuration(sec) {
    if (!Number.isFinite(sec) || sec < 0) return "--:--";
    const total = Math.max(0, Math.round(sec));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
}

/* ---------------------------------------------------------------------------
 * 素材库总览面板（nav-assets）
 * @returns {{el: HTMLElement, syncFromWidgets: Function, attachEditor: Function}}
 * ------------------------------------------------------------------------- */

export function mountAssetLibraryPanel(parentEl, editor) {
    const el = document.createElement("div");
    el.className = "bd-al";
    el.innerHTML = `
        <div class="bd-al-block">
            <div class="bd-al-block-title" data-i18n="assets.library.globalTitle">🌐 全局资产库</div>
            <div data-r="al-global"></div>
        </div>
        <div class="bd-al-block">
            <div class="bd-al-block-title" data-i18n="assets.library.sceneTitle">🎬 场景素材组</div>
            <div data-r="al-scenes"></div>
        </div>
        <div class="bd-al-block">
            <div class="bd-al-block-title" data-i18n="assets.library.hintTitle">说明</div>
            <div class="bd-al-hint" style="color:#777;font-size:10px;line-height:1.6" data-i18n="assets.library.hint">全局资产库在「时间线 → 素材组」编辑；各场景素材组在「场景管理」编辑，优先级高于全局资产库。</div>
        </div>
    `;
    parentEl.appendChild(el);

    const ui = { global: el.querySelector('[data-r="al-global"]'), scenes: el.querySelector('[data-r="al-scenes"]') };
    let _editor = editor || null;

    function renderGlobal(editor) {
        const box = ui.global;
        if (!box) return;
        box.innerHTML = "";
        const a = getAssetsBlock(editor);
        for (const [kind, i18nKey] of ASSET_KINDS) {
            const list = a[kind] || [];
            const row = document.createElement("div");
            row.className = "bd-al-row";
            const label = document.createElement("span");
            label.className = "bd-al-label";
            label.textContent = t(i18nKey);
            const thumbs = document.createElement("div");
            thumbs.className = "bd-al-thumbs";
            if (!list.length) {
                const empty = document.createElement("span");
                empty.className = "bd-al-thumb empty";
                empty.textContent = t("assets.library.empty") || "—";
                thumbs.appendChild(empty);
            } else {
                for (const asset of list.slice(0, 6)) {
                    const im = document.createElement("img");
                    im.className = "bd-al-thumb";
                    im.src = viewUrl(asset.imageFile);
                    im.title = asset.name || asset.id || "";
                    thumbs.appendChild(im);
                }
            }
            const count = document.createElement("span");
            count.className = "bd-al-count";
            count.textContent = `${list.length}`;
            const edit = document.createElement("button");
            edit.type = "button";
            edit.className = "bd-al-edit";
            edit.textContent = t("assets.library.edit") || "编辑";
            edit.onclick = () => editor?.setActiveNavTab?.("timeline");
            row.append(label, thumbs, count, edit);
            box.appendChild(row);
        }
    }

    function renderScenes(editor) {
        const box = ui.scenes;
        if (!box) return;
        box.innerHTML = "";
        const scenes = getScenesBlock(editor).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        if (!scenes.length) {
            const empty = document.createElement("div");
            empty.className = "bd-al-empty";
            empty.style.cssText = "color:#666;font-size:10px;padding:8px;border:1px dashed #2a2a2a;border-radius:6px";
            empty.textContent = t("scene.empty");
            box.appendChild(empty);
            return;
        }
        for (const sc of scenes) {
            const row = document.createElement("div");
            row.className = "bd-al-scene-row";
            const num = document.createElement("span");
            num.className = "bd-al-scene-num";
            num.textContent = String(scenes.indexOf(sc) + 1).padStart(2, "0");
            const name = document.createElement("span");
            name.className = "bd-al-scene-name";
            name.textContent = sc.name || `Scene ${String(scenes.indexOf(sc) + 1).padStart(2, "0")}`;
            const parts = [];
            for (const [kind, i18nKey] of ASSET_KINDS) {
                const n = (sc.assets?.[kind] || []).length;
                if (n > 0) parts.push(`${t(i18nKey)} ${n}`);
            }
            const meta = document.createElement("span");
            meta.className = "bd-al-scene-meta";
            meta.textContent = parts.join(" · ") || t("assets.library.noAssets") || "无素材";
            row.onclick = () => {
                editor?.setActiveNavTab?.("scenes");
                editor?.sceneManagerPanel?.selectScene?.(sc.id);
            };
            row.append(num, name, meta);
            box.appendChild(row);
        }
    }

    function renderAll(editor) {
        if (!editor?.timeline) return;
        renderGlobal(editor);
        renderScenes(editor);
    }

    function attachEditor(ed) {
        _editor = ed || null;
        if (_editor?.timeline) renderAll(_editor);
    }

    function syncFromWidgets() {
        renderAll(_editor);
    }

    return { el, syncFromWidgets, attachEditor };
}

/* ---------------------------------------------------------------------------
 * 导出中心面板（nav-export）
 * @returns {{el: HTMLElement, syncFromWidgets: Function, attachEditor: Function}}
 * ------------------------------------------------------------------------- */

export function mountExportCenterPanel(parentEl, editor) {
    const el = document.createElement("div");
    el.className = "bd-ex";
    el.innerHTML = `
        <div class="bd-ex-toolbar">
            <div style="display:flex;flex-direction:column;gap:2px">
                <span class="bd-ex-title" data-i18n="export.center.title">导出中心</span>
                <span class="bd-ex-hint" data-i18n="export.center.hint">每场景导出 → Director_SceneNN.mp4（场景内自动接缝/曝光统一）；全片导出 → 把各场景 mp4 用 ffmpeg 直拼成 Movie.mp4（几乎零内存）。</span>
            </div>
        </div>
        <div class="bd-ex-scenes" data-r="ex-scenes"></div>
        <div class="bd-ex-movie">
            <div class="bd-ex-movie-head">
                <span class="bd-ex-movie-title" data-i18n="export.movie.title">全片导出</span>
                <button type="button" class="bd-ex-btn" data-a="ex-movie" data-i18n="export.movie.btn">导出 Movie.mp4</button>
            </div>
            <span class="bd-ex-hint" data-i18n="export.movie.hint">把全部已归属场景的镜头按场景顺序编码后，用 ffmpeg 直拼成 Movie.mp4。未归属任何场景的镜头会自动归入 location 兜底组。</span>
        </div>
    `;
    parentEl.appendChild(el);

    const ui = { scenes: el.querySelector('[data-r="ex-scenes"]'), movieBtn: el.querySelector('[data-a="ex-movie"]') };
    let _editor = editor || null;

    function renderScenes(editor) {
        const box = ui.scenes;
        if (!box) return;
        box.innerHTML = "";
        const scenes = getScenesBlock(editor).slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        if (!scenes.length) {
            const empty = document.createElement("div");
            empty.className = "bd-ex-empty";
            empty.textContent = t("export.center.empty");
            box.appendChild(empty);
            return;
        }
        for (const sc of scenes) {
            const row = document.createElement("div");
            row.className = "bd-ex-scene";
            const num = document.createElement("span");
            num.className = "bd-ex-scene-num";
            num.textContent = String(scenes.indexOf(sc) + 1).padStart(2, "0");
            const name = document.createElement("span");
            name.className = "bd-ex-scene-name";
            name.textContent = sc.name || `Scene ${String(scenes.indexOf(sc) + 1).padStart(2, "0")}`;
            const shots = sceneShotCount(editor, sc.id);
            const dur = sceneTotalDuration(editor, sc.id);
            const stat = document.createElement("span");
            stat.className = "bd-ex-scene-stat";
            stat.textContent = `${shots} ${t("scene.shots")} · ${formatMediaDuration(dur)}`;
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "bd-ex-btn";
            btn.textContent = t("scene.export");
            btn.title = t("scene.exportHint");
            btn.disabled = shots === 0;
            btn.onclick = () => exportScene(editor, sc.id);
            row.append(num, name, stat, btn);
            box.appendChild(row);
        }
    }

    function renderAll(editor) {
        if (!editor?.timeline) return;
        renderScenes(editor);
    }

    function attachEditor(ed) {
        _editor = ed || null;
        if (_editor?.timeline) renderAll(_editor);
    }

    function syncFromWidgets() {
        renderAll(_editor);
    }

    ui.movieBtn.addEventListener("click", () => {
        if (!_editor?.timeline) return;
        _editor.timeline.output = _editor.timeline.output || {};
        _editor.timeline.output.exportMode = "movie";
        _editor.timeline.runSelectEnabled = false;
        _editor.timeline.runSelection = [];
        commitEditor(_editor);
        try {
            window.app?.queuePrompt?.();
        } catch (err) {
            console.warn("[MiniMax H3Director] auto-run after movie export setup failed:", err);
        }
    });

    return { el, syncFromWidgets, attachEditor };
}
