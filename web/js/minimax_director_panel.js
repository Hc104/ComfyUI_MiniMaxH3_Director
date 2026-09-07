/**
 * AI 导演设置面板（待办⑤，里程碑 A）。
 *
 * 设计决策（用户 2026-08-07）：
 * - Qwen 视觉反馈属于「整个导演系统」，不属于 R2V——不放 r2v 工具栏，独立成面板。
 * - 面板未来承载：自动续接 / 状态跟踪 / FL2VA路由 / Qwen视觉反馈 / 质量控制 五组。
 *   里程碑 A 只实现 Qwen 视觉反馈组（开关 + 级别），其余组后续里程碑填入。
 * - 状态持久化到 timeline.output（qwenVlEnabled / qwenVlLevel），后端 gen_timeline 据此解析。
 */
import { t } from "./minimax_i18n.js";

/** 默认从 timeline.output 读 Qwen 反馈开关状态（字段缺失默认关）。 */
function readQwenEnabled(output) {
    const raw = output?.qwenVlEnabled ?? output?.qwen_vl_enabled ?? false;
    if (raw === true) return true;
    if (typeof raw === "string") return !["false", "0", "off", "no"].includes(raw.trim().toLowerCase());
    return !!raw;
}

/** 默认从 timeline.output 读 Qwen 反馈级别（字段缺失默认 1）。 */
function readQwenLevel(output) {
    const raw = output?.qwenVlLevel ?? output?.qwen_vl_level ?? 1;
    const n = parseInt(raw, 10);
    return n === 2 || n === 3 ? n : 1;
}

/**
 * 挂载「AI 导演设置」面板。
 * @param {HTMLElement} parentEl  挂载点（r2v batch 工具栏下方）
 * @param {object} [editor]  Director 编辑器实例（可空，之后用 attachEditor 绑定）
 * @returns {{el: HTMLElement, syncFromWidgets: Function, attachEditor: Function}}
 */
export function mountDirectorPanel(parentEl, editor) {
    const el = document.createElement("div");
    el.className = "bd-director-panel hidden";
    el.innerHTML = `
        <div class="bd-director-panel-head" data-r="director-head">
            <span class="bd-director-panel-title" data-i18n="panel.director.title">AI 导演设置</span>
            <span class="bd-director-panel-caret" data-r="director-caret">▾</span>
        </div>
        <div class="bd-director-panel-body" data-r="director-body">
            <div class="bd-director-group" data-r="director-group-qwen">
                <div class="bd-director-group-title" data-i18n="panel.director.qwenGroup">Qwen3-VL 视觉反馈</div>
                <label class="bd-director-row bd-director-qwen-toggle" title="${t("tooltip.qwenVl")}">
                    <input type="checkbox" data-r="director-qwen-cb">
                    <span data-i18n="panel.director.qwenEnabled">启用（AI 导演判断）</span>
                </label>
                <div class="bd-director-row bd-director-qwen-level hidden" data-r="director-qwen-level">
                    <span class="bd-director-row-label" data-i18n="panel.director.qwenLevel">反馈级别</span>
                    <select data-r="director-qwen-level-sel">
                        <option value="1" data-i18n="panel.director.qwenLevel1">1 状态提取</option>
                        <option value="2" data-i18n="panel.director.qwenLevel2">2 +一致性检测</option>
                        <option value="3" data-i18n="panel.director.qwenLevel3">3 +失败重跑</option>
                    </select>
                </div>
                <div class="bd-director-row bd-director-qwen-hint" data-r="director-qwen-hint">
                    <span data-i18n="panel.director.qwenHint">在段间清理显存后加载 Qwen3-VL（约 3GB），提取本镜尾帧状态自动更新状态跟踪；用户手填状态优先。</span>
                </div>
            </div>
            <div class="bd-director-group bd-director-placeholder" data-i18n="panel.director.moreGroups">自动续接 / 状态跟踪 / FL2VA路由 / 质量控制 将在后续里程碑加入本面板。</div>
        </div>
    `;
    parentEl.appendChild(el);

    const head = el.querySelector('[data-r="director-head"]');
    const body = el.querySelector('[data-r="director-body"]');
    head.addEventListener("click", () => {
        const collapsed = body.classList.toggle("bd-director-collapsed");
        el.querySelector('[data-r="director-caret"]').textContent = collapsed ? "▸" : "▾";
    });

    const qwenCb = el.querySelector('[data-r="director-qwen-cb"]');
    const levelWrap = el.querySelector('[data-r="director-qwen-level"]');
    const levelSel = el.querySelector('[data-r="director-qwen-level-sel"]');
    let _editor = editor || null;

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
    });

    levelSel.addEventListener("change", () => {
        commitOutput((o) => { o.qwenVlLevel = parseInt(levelSel.value, 10); });
    });

    /** 绑定真实 editor（挂载时可能还没有）。 */
    function attachEditor(ed) {
        _editor = ed || null;
        if (_editor?.timeline) syncFromWidgets();
    }

    /** 从 timeline.output 同步 widget 状态（重渲染后调用）。 */
    function syncFromWidgets() {
        if (!_editor?.timeline) return;
        const output = _editor.timeline.output || {};
        const on = readQwenEnabled(output);
        qwenCb.checked = on;
        levelWrap.classList.toggle("hidden", !on);
        levelSel.value = String(readQwenLevel(output));
        el.classList.remove("hidden");
    }

    return { el, syncFromWidgets, attachEditor };
}

/** 从 timeline.output 读 Qwen 反馈开关（供 buildTimelinePayload 白名单判断）。 */
export function readDirectorQwenFields(editor) {
    const output = editor.timeline?.output || {};
    const enabled = readQwenEnabled(output);
    return {
        qwenVlEnabled: enabled,
        qwenVlLevel: readQwenLevel(output),
    };
}
