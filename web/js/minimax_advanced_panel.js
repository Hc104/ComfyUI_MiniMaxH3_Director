/**
 * 高级设置折叠面板（UI 2.0 第四优先级）。
 *
 * 设计决策（用户 2026-08-08，AskUserQuestion 确认）：
 * - 折叠形态：DOM 折叠面板（非 BDGROUP 原生折叠、非混合方案）。
 * - 位置：时间线底部，所有模式可见（prompt_batch / fl2v / video 通用）。
 * - 内容六组：
 *     Qwen3-VL 视觉反馈（原 AI 导演设置面板迁入，batch 独立面板移除）
 *     采样设置  cfg + seed + control_after_generate
 *     高级采样  steps + sampler + scheduler + shift_video + shift_audio
 *     性能      clear_vram_between_segments + export_source_images
 *     导出设置  exportMode + maxExportFrames + continuityEnabled/overlap（UI 2.1 P6 收编）
 *     音频      audioMode（UI 2.1 P6 收编）
 * - 关键约束：原生 widget 必须保留为真实 widget 对象（后端 execute() 读它们的值），
 *   面板只做「镜像读写」——读 widget.value 填 DOM，改 DOM 写回同一 widget 对象。
 *   原生控件全部隐藏（HIDDEN_WIDGETS 增加对应名），BDGROUP 组头一并隐藏。
 * - 高度：折叠仅头部（≈46px）；展开按四组固定行高累加（Qwen 级别行是否显示
 *   取决于 qwenVlEnabled），getDirectorUiHeight 调用 getAdvancedPanelUiHeight。
 */
import { t } from "./minimax_i18n.js";
import { readDirectorQwenFields } from "./minimax_director_panel.js";

export const ADVANCED_PANEL_STYLES = `
.bd-adv-panel{width:100%;box-sizing:border-box;display:flex;flex-direction:column;gap:0;flex:0 0 auto;background:linear-gradient(165deg,#1b1b1b 0%,#141414 55%,#111 100%);border:1px solid #2c2c2c;border-radius:12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.035)}
.bd-adv-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 14px;cursor:pointer;user-select:none;border-bottom:1px solid rgba(255,255,255,.06)}
.bd-adv-head:hover{background:rgba(255,255,255,.02)}
.bd-adv-title{color:#f0f0f0;font-size:12.5px;font-weight:650;letter-spacing:.02em}
.bd-adv-caret{color:#888;font-size:11px;transition:transform .15s;width:12px;text-align:center}
.bd-adv-body{display:flex;flex-direction:column;gap:10px;padding:12px 14px}
.bd-adv-body.bd-adv-collapsed{display:none}
.bd-adv-group{display:flex;flex-direction:column;gap:7px;background:#0e0e0e;border:1px solid #262626;border-radius:10px;padding:9px 12px}
.bd-adv-group-title{color:#eaeaea;font-size:11px;font-weight:700;letter-spacing:.02em}
.bd-adv-row{display:flex;align-items:center;gap:8px;font-size:11px;color:#bbb;min-height:26px}
.bd-adv-row-label{color:#9db4e8;flex-shrink:0;width:78px}
.bd-adv-row select{background:#101010;border:1px solid #3a3a3a;color:#ddd;border-radius:6px;font-size:11px;padding:3px 6px;cursor:pointer;min-width:0;flex:1 1 auto;max-width:280px}
.bd-adv-row select:hover{border-color:#4a6aa0}
.bd-adv-row select option{background:#131313}
.bd-adv-num{background:#101010;border:1px solid #3a3a3a;color:#ddd;border-radius:6px;font-size:11px;padding:3px 6px;width:110px;min-width:0}
.bd-adv-num:focus{outline:none;border-color:#4a6aa0}
.bd-adv-dice{background:#101010;border:1px solid #3a3a3a;color:#ddd;border-radius:6px;font-size:11px;padding:3px 8px;cursor:pointer;flex-shrink:0}
.bd-adv-dice:hover{border-color:#4a6aa0;color:#fff}
.bd-adv-toggle{display:inline-flex;cursor:pointer;user-select:none;align-items:center}
.bd-adv-toggle input{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#4fff8f;flex-shrink:0}
.bd-adv-toggle:hover{color:#dfffe9}
.bd-adv-level.hidden{display:none}
.bd-adv-hint{color:#7a7a7a;font-size:10px;line-height:1.5}
`;

const ADV_HEAD_H = 38;
const ADV_BODY_PAD = 24;
const ADV_GROUP_PAD = 20;
const ADV_GROUP_TITLE = 18;
const ADV_ROW_H = 26;
const ADV_HINT_H = 32;
const ADV_GROUP_GAP = 10;
const ADV_ROW_GAP = 7;

/** 一组（标题 + rows 个行元素 + 内部 gap）的估算高度。 */
function advancedGroupH(rowHeights) {
    let h = ADV_GROUP_PAD + ADV_GROUP_TITLE;
    h += rowHeights.reduce((a, b) => a + b, 0);
    h += (1 + rowHeights.length - 1) * ADV_ROW_GAP; // 标题→行 + 行间 gap
    return h;
}

/** 高级设置面板占用的节点高度（折叠仅头栏；展开按组累加）。 */
export function getAdvancedPanelUiHeight(editor) {
    if (editor?._advPanelCollapsed) return ADV_HEAD_H + 8;
    const qwen = editor?.timeline ? readDirectorQwenFields(editor) : { qwenVlEnabled: false };
    const qwenOn = !!qwen?.qwenVlEnabled;
    const qwenRows = qwenOn ? [ADV_ROW_H, ADV_ROW_H, ADV_HINT_H] : [ADV_ROW_H, ADV_HINT_H];
    let h = ADV_HEAD_H + 8 + ADV_BODY_PAD;
    h += advancedGroupH(qwenRows); // Qwen3-VL 视觉反馈
    h += ADV_GROUP_GAP + advancedGroupH([ADV_ROW_H, ADV_ROW_H, ADV_ROW_H]); // 采样设置 cfg/seed/control
    h += ADV_GROUP_GAP + advancedGroupH([ADV_ROW_H, ADV_ROW_H, ADV_ROW_H, ADV_ROW_H, ADV_ROW_H]); // 高级采样
    h += ADV_GROUP_GAP + advancedGroupH([ADV_ROW_H, ADV_ROW_H]); // 性能
    h += ADV_GROUP_GAP + advancedGroupH([ADV_ROW_H, ADV_ROW_H, ADV_ROW_H, ADV_ROW_H]); // 导出设置
    h += ADV_GROUP_GAP + advancedGroupH([ADV_ROW_H]); // 音频
    return h;
}

/**
 * 挂载「高级设置」折叠面板。
 * @param {HTMLElement} parentEl  挂载点（时间线 mainBody 底部）
 * @param {object} [editor]  Director 编辑器实例（可空，之后用 attachEditor 绑定）
 * @returns {{el: HTMLElement, syncFromWidgets: Function, attachEditor: Function}}
 */
export function mountAdvancedPanel(parentEl, editor) {
    const el = document.createElement("div");
    el.className = "bd-adv-panel";
    el.innerHTML = `
        <div class="bd-adv-head" data-r="adv-head" title="${t("tooltip.advPanel")}">
            <span class="bd-adv-title" data-i18n="panel.adv.title">高级设置</span>
            <span class="bd-adv-caret" data-r="adv-caret">▸</span>
        </div>
        <div class="bd-adv-body bd-adv-collapsed" data-r="adv-body">
            <div class="bd-adv-group">
                <div class="bd-adv-group-title" data-i18n="panel.director.qwenGroup">Qwen3-VL 视觉反馈</div>
                <label class="bd-adv-row bd-adv-toggle" title="${t("tooltip.qwenVl")}">
                    <input type="checkbox" data-r="adv-qwen-cb">
                    <span data-i18n="panel.director.qwenEnabled">启用（AI 导演判断）</span>
                </label>
                <div class="bd-adv-row bd-adv-level hidden" data-r="adv-qwen-level">
                    <span class="bd-adv-row-label" data-i18n="panel.director.qwenLevel">反馈级别</span>
                    <select data-r="adv-qwen-level-sel">
                        <option value="1" data-i18n="panel.director.qwenLevel1">1 状态提取</option>
                        <option value="2" data-i18n="panel.director.qwenLevel2">2 +一致性检测</option>
                        <option value="3" data-i18n="panel.director.qwenLevel3">3 +失败重跑</option>
                    </select>
                </div>
                <div class="bd-adv-row bd-adv-hint">
                    <span data-i18n="panel.director.qwenHint"></span>
                </div>
            </div>
            <div class="bd-adv-group">
                <div class="bd-adv-group-title" data-i18n="widget.grpSample">采样设置</div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.cfg">CFG</span>
                    <input type="number" class="bd-adv-num" data-r="adv-cfg" step="0.01">
                </div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.seed">种子</span>
                    <input type="number" class="bd-adv-num bd-adv-seed" data-r="adv-seed" step="1">
                    <button type="button" class="bd-adv-dice" data-r="adv-seed-dice" title="${t("widget.seedDice")}">🎲</button>
                </div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.controlAfterGenerate">生成前后定制</span>
                    <select data-r="adv-control"></select>
                </div>
            </div>
            <div class="bd-adv-group">
                <div class="bd-adv-group-title" data-i18n="widget.grpAdvanced">高级采样</div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.steps">采样步数</span>
                    <input type="number" class="bd-adv-num" data-r="adv-steps" step="1">
                </div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.sampler">采样器</span>
                    <select data-r="adv-sampler"></select>
                </div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.scheduler">调度器</span>
                    <select data-r="adv-scheduler"></select>
                </div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.shiftVideo">视频偏移</span>
                    <input type="number" class="bd-adv-num" data-r="adv-shift-video" step="0.01">
                </div>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="widget.shiftAudio">音频偏移</span>
                    <input type="number" class="bd-adv-num" data-r="adv-shift-audio" step="0.01">
                </div>
            </div>
            <div class="bd-adv-group">
                <div class="bd-adv-group-title" data-i18n="widget.grpPerf">性能</div>
                <label class="bd-adv-row bd-adv-toggle" title="${t("widget.tooltip.clearVram")}">
                    <input type="checkbox" data-r="adv-clear-vram">
                    <span data-i18n="widget.clearVram">段间清理显存</span>
                </label>
                <label class="bd-adv-row bd-adv-toggle" title="${t("widget.tooltip.exportSourceImages")}">
                    <input type="checkbox" data-r="adv-export-src">
                    <span data-i18n="widget.exportSourceImages">输出原片对比</span>
                </label>
            </div>
            <div class="bd-adv-group">
                <div class="bd-adv-group-title" data-i18n="panel.adv.exportGroup">导出设置</div>
                <div class="bd-adv-row" title="${t("tooltip.exportMode")}">
                    <span class="bd-adv-row-label" data-i18n="output.exportMode.label">导出方式</span>
                    <select data-r="adv-export-mode">
                        <option value="scene" data-i18n="output.exportMode.scene">场景导出</option>
                        <option value="movie" data-i18n="output.exportMode.movie">全片导出</option>
                        <option value="segments" data-i18n="output.exportMode.segments">分镜导出</option>
                        <option value="all" data-i18n="output.exportMode.all">全片导出（内存合并）</option>
                    </select>
                </div>
                <div class="bd-adv-row" title="${t("output.maxFramesHint")}">
                    <span class="bd-adv-row-label" data-i18n="output.maxFrames">最大帧数</span>
                    <input type="number" class="bd-adv-num" data-r="adv-max-frames" min="0" max="999999" step="1">
                </div>
                <label class="bd-adv-row bd-adv-toggle" title="${t("output.segmentContinuityHint")}">
                    <input type="checkbox" data-r="adv-continuity-cb">
                    <span data-i18n="output.segmentContinuity">段间引导</span>
                </label>
                <div class="bd-adv-row">
                    <span class="bd-adv-row-label" data-i18n="output.continuityOverlap">参考帧数</span>
                    <input type="number" class="bd-adv-num" data-r="adv-continuity-overlap" min="1" max="81" step="4">
                </div>
            </div>
            <div class="bd-adv-group">
                <div class="bd-adv-group-title" data-i18n="panel.adv.audioGroup">音频</div>
                <div class="bd-adv-row" title="${t("tooltip.audioMode")}">
                    <span class="bd-adv-row-label" data-i18n="output.audio.label">声音</span>
                    <select data-r="adv-audio-mode">
                        <option value="generate" data-i18n="output.audio.generate">生成声音</option>
                        <option value="source" data-i18n="output.audio.source">使用原声</option>
                        <option value="mute" data-i18n="output.audio.mute">静音</option>
                    </select>
                </div>
            </div>
        </div>
    `;
    parentEl.appendChild(el);

    const head = el.querySelector('[data-r="adv-head"]');
    const body = el.querySelector('[data-r="adv-body"]');
    const caret = el.querySelector('[data-r="adv-caret"]');
    let _editor = editor || null;

    function resizeNode() {
        if (!_editor?.node) return;
        if (_editor.isPlaying) return;
        _editor.updateDomWidgetHeight?.();
        const sz = _editor.node.computeSize();
        _editor.node.setSize([_editor.node.size[0], sz[1]]);
        _editor.node.setDirtyCanvas?.(true, true);
    }

    function setCollapsed(collapsed) {
        body.classList.toggle("bd-adv-collapsed", !!collapsed);
        caret.textContent = collapsed ? "▸" : "▾";
        if (_editor) _editor._advPanelCollapsed = !!collapsed;
        resizeNode();
    }

    head.addEventListener("click", () => {
        setCollapsed(!body.classList.contains("bd-adv-collapsed"));
    });

    function markDirty() {
        _editor?.node?.setDirtyCanvas?.(true, true);
    }

    function commitWidget(name, value) {
        const w = _editor?.widget?.(name);
        if (!w) return;
        w.value = value;
        markDirty();
    }

    // ---- Qwen3-VL 视觉反馈（状态在 timeline.output，与 AI 导演面板同一存储） ----
    const qwenCb = el.querySelector('[data-r="adv-qwen-cb"]');
    const levelWrap = el.querySelector('[data-r="adv-qwen-level"]');
    const levelSel = el.querySelector('[data-r="adv-qwen-level-sel"]');

    function commitOutput(mutate) {
        if (!_editor?.timeline) return;
        _editor.timeline.output = _editor.timeline.output || {};
        mutate(_editor.timeline.output);
        _editor.scheduleTimelineSync?.();
        _editor.commit?.(false, { syncTimeline: true });
    }

    qwenCb.addEventListener("change", () => {
        if (qwenCb.checked) {
            levelWrap.classList.remove("hidden");
            commitOutput((o) => { o.qwenVlEnabled = true; });
        } else {
            levelWrap.classList.add("hidden");
            commitOutput((o) => { o.qwenVlEnabled = false; });
        }
        resizeNode(); // 级别行显示/隐藏 → 高度变化
    });

    levelSel.addEventListener("change", () => {
        commitOutput((o) => { o.qwenVlLevel = parseInt(levelSel.value, 10); });
    });

    // ---- 采样参数（镜像原生 widget，后端 execute() 读同一对象） ----
    const cfgInput = el.querySelector('[data-r="adv-cfg"]');
    const seedInput = el.querySelector('[data-r="adv-seed"]');
    const seedDice = el.querySelector('[data-r="adv-seed-dice"]');
    const controlSel = el.querySelector('[data-r="adv-control"]');
    const stepsInput = el.querySelector('[data-r="adv-steps"]');
    const samplerSel = el.querySelector('[data-r="adv-sampler"]');
    const schedulerSel = el.querySelector('[data-r="adv-scheduler"]');
    const shiftVideoInput = el.querySelector('[data-r="adv-shift-video"]');
    const shiftAudioInput = el.querySelector('[data-r="adv-shift-audio"]');
    const clearVramCb = el.querySelector('[data-r="adv-clear-vram"]');
    const exportSrcCb = el.querySelector('[data-r="adv-export-src"]');
    const exportModeSel = el.querySelector('[data-r="adv-export-mode"]');
    const maxFramesInput = el.querySelector('[data-r="adv-max-frames"]');
    const continuityCb = el.querySelector('[data-r="adv-continuity-cb"]');
    const continuityOverlapInput = el.querySelector('[data-r="adv-continuity-overlap"]');
    const audioModeSel = el.querySelector('[data-r="adv-audio-mode"]');

    function bindNum(input, widgetName, parse) {
        input.addEventListener("change", () => {
            const w = _editor?.widget?.(widgetName);
            if (!w) return;
            const n = parse ? parse(input.value) : Number(input.value);
            if (!Number.isFinite(n)) return;
            w.value = n;
            if (input.min != null && n < Number(input.min)) w.value = Number(input.min);
            if (input.max != null && n > Number(input.max)) w.value = Number(input.max);
            input.value = String(w.value);
            markDirty();
        });
    }
    bindNum(cfgInput, "cfg");
    bindNum(seedInput, "seed", (v) => parseInt(v, 10));
    bindNum(stepsInput, "steps", (v) => parseInt(v, 10));
    bindNum(shiftVideoInput, "shift_video");
    bindNum(shiftAudioInput, "shift_audio");

    seedDice.addEventListener("click", () => {
        const w = _editor?.widget?.("seed");
        if (!w) return;
        // 32-bit seed range avoids JS float precision loss beyond 2^53.
        w.value = Math.floor(Math.random() * 0xFFFFFFFF);
        seedInput.value = String(w.value);
        markDirty();
    });

    function controlLabels() {
        return {
            fixed: t("widget.controlFixed"),
            increment: t("widget.controlIncrement"),
            decrement: t("widget.controlDecrement"),
            randomize: t("widget.controlRandomize"),
        };
    }

    function populateCombo(selectEl, widgetName, labelMap) {
        const w = _editor?.widget?.(widgetName);
        if (!w) return;
        const values = w.options?.values || [];
        if (!values.length) return;
        selectEl.innerHTML = "";
        for (const v of values) {
            const opt = document.createElement("option");
            opt.value = v;
            opt.textContent = labelMap?.[v] ?? labelMap?.["*"] ?? String(v);
            selectEl.appendChild(opt);
        }
        if (!values.includes(w.value)) selectEl.value = values[0];
        else selectEl.value = w.value;
    }

    controlSel.addEventListener("change", () => {
        const w = _editor?.widget?.("control_after_generate")
            || _editor?.widget?.("control after generate");
        if (w) { w.value = controlSel.value; markDirty(); }
    });
    samplerSel.addEventListener("change", () => commitWidget("sampler", samplerSel.value));
    schedulerSel.addEventListener("change", () => commitWidget("scheduler", schedulerSel.value));

    clearVramCb.addEventListener("change", () => commitWidget("clear_vram_between_segments", !!clearVramCb.checked));
    exportSrcCb.addEventListener("change", () => commitWidget("export_source_images", !!exportSrcCb.checked));

    // ---- 导出设置 / 音频（镜像 timeline.output，经 onOutputField 归一化 + commit） ----
    exportModeSel.addEventListener("change", () => _editor?.onOutputField?.("exportMode", exportModeSel.value));
    maxFramesInput.addEventListener("change", () => _editor?.onOutputField?.("maxExportFrames", maxFramesInput.value));
    continuityCb.addEventListener("change", () => _editor?.onOutputField?.("continuityEnabled", continuityCb.checked));
    continuityOverlapInput.addEventListener("change", () => _editor?.onOutputField?.("continuityOverlapFrames", continuityOverlapInput.value));
    audioModeSel.addEventListener("change", () => _editor?.onOutputField?.("audioMode", audioModeSel.value));

    function syncNumInput(input, w) {
        if (!w) return;
        if (w.options?.min != null) input.min = w.options.min;
        if (w.options?.max != null) input.max = w.options.max;
        if (w.options?.step != null) input.step = w.options.step;
        input.value = String(w.value ?? "");
    }

    /** 从原生 widget / timeline.output 同步面板状态（locale 切换后也调用）。 */
    function syncFromWidgets() {
        if (!_editor) return;
        const qwen = readDirectorQwenFields(_editor);
        qwenCb.checked = !!qwen.qwenVlEnabled;
        levelWrap.classList.toggle("hidden", !qwen.qwenVlEnabled);
        levelSel.value = String(qwen.qwenVlLevel);
        syncNumInput(cfgInput, _editor.widget("cfg"));
        syncNumInput(seedInput, _editor.widget("seed"));
        syncNumInput(stepsInput, _editor.widget("steps"));
        syncNumInput(shiftVideoInput, _editor.widget("shift_video"));
        syncNumInput(shiftAudioInput, _editor.widget("shift_audio"));
        populateCombo(controlSel, "control_after_generate", controlLabels());
        if (!controlSel.options.length) populateCombo(controlSel, "control after generate", controlLabels());
        populateCombo(samplerSel, "sampler");
        populateCombo(schedulerSel, "scheduler");
        clearVramCb.checked = !!_editor.widget("clear_vram_between_segments")?.value;
        exportSrcCb.checked = !!_editor.widget("export_source_images")?.value;
        const out = _editor.timeline?.output || {};
        exportModeSel.value = ["scene", "movie", "segments", "all"].includes(out.exportMode)
            ? out.exportMode : "scene";
        maxFramesInput.value = String(out.maxExportFrames ?? 0);
        continuityCb.checked = !!out.continuityEnabled;
        continuityOverlapInput.value = String(out.continuityOverlapFrames ?? 9);
        audioModeSel.value = ["generate", "source", "mute"].includes(out.audioMode)
            ? out.audioMode : "generate";
    }

    /** 绑定真实 editor（挂载时可能还没有）。 */
    function attachEditor(ed) {
        _editor = ed || null;
        if (_editor && _editor._advPanelCollapsed == null) _editor._advPanelCollapsed = true;
        setCollapsed(!!_editor?._advPanelCollapsed);
        if (_editor?.timeline) syncFromWidgets();
    }

    if (editor) attachEditor(editor);
    return { el, syncFromWidgets, attachEditor };
}
