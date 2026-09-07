/** Multi prompt-group UI for t2i / i2i / r2i / t2v / i2v / r2v (prompt batch mode). */

import { api } from "../../scripts/api.js";
import {
    DEFAULT_ASPECT_RATIO,
    DEFAULT_MEGAPIXELS,
    defaultDurationSec,
    defaultFrameCount,
    durationToClampedMiniMaxFrames,
    durationToMiniMaxFrames,
    framesToDurationSec,
    imageBatchVariant,
    isVideoBatchTask,
    MAX_GEN_FRAMES,
    MAX_REFERENCE_AUDIOS,
    MAX_REFERENCE_IMAGES,
    MAX_REFERENCE_VIDEOS,
    maxDurationSec,
    MINIMAX_CANVAS_MULTIPLE,
    minDurationSec,
    minFrameCount,
    newBatchSegment,
    preferredDurationSecFromFrames,
    refAudioLabel,
    refImageLabel,
    refVideoLabel,
    resolveTaskKey,
    roundDurationSec,
    sumFrameCounts,
} from "./minimax_gen_timeline.js";
import { wirePromptImageMentions, attachPromptHighlight } from "./minimax_prompt_mentions.js";
import { t } from "./minimax_i18n.js";

const _players = new WeakMap();
/** r2v picture grid: 9 slots in 3×3; reveal 3 → 6 → 9. */
const R2V_PICTURE_SLOTS = MAX_REFERENCE_IMAGES;
const R2V_PICTURE_STEP = 3;
let _activeR2vMedia = null;

function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
}

/** 阶段 D/架构④：衔接方式四态——auto=自动续接、none=独立镜头(不续接)、ref2va=强制状态驱动、fl2va=强制首尾帧硬锁。 */
function normalizeContinuityMode(v) {
    const s = String(v ?? "auto").trim().toLowerCase();
    return s === "none" || s === "ref2va" || s === "fl2va" ? s : "auto";
}

export function formatMediaDuration(sec) {
    if (!Number.isFinite(sec) || sec < 0) return "--:--";
    const total = Math.max(0, Math.round(sec));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
}

function pauseActiveR2vMedia(except = null) {
    if (_activeR2vMedia && _activeR2vMedia !== except) {
        try {
            _activeR2vMedia.pause();
        } catch (_) { /* ignore */ }
        const btn = _activeR2vMedia._r2vPlayBtn;
        if (btn) btn.textContent = "▶";
    }
    if (_activeR2vMedia !== except) _activeR2vMedia = null;
}

export function bindR2vMediaPlayback(mediaEl, playBtn, progressWrap = null) {
    mediaEl.classList.add("bd-r2v-media");
    mediaEl._r2vPlayBtn = playBtn;
    const fill = progressWrap?.querySelector?.(".bd-r2v-progress-fill");
    const syncBtn = () => {
        playBtn.textContent = mediaEl.paused ? "▶" : "⏸";
    };
    const syncProgress = () => {
        if (!progressWrap || !fill) return;
        const dur = mediaEl.duration;
        const pct = Number.isFinite(dur) && dur > 0
            ? Math.min(100, Math.max(0, (mediaEl.currentTime / dur) * 100))
            : 0;
        fill.style.width = `${pct}%`;
        progressWrap.classList.toggle("active", !mediaEl.paused);
        progressWrap.classList.toggle("playing", !mediaEl.paused);
    };
    playBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (mediaEl.paused) {
            pauseActiveR2vMedia(mediaEl);
            mediaEl.play().catch(() => {});
            _activeR2vMedia = mediaEl;
        } else {
            mediaEl.pause();
            if (_activeR2vMedia === mediaEl) _activeR2vMedia = null;
        }
        syncBtn();
        syncProgress();
    });
    mediaEl.addEventListener("play", () => {
        pauseActiveR2vMedia(mediaEl);
        _activeR2vMedia = mediaEl;
        syncBtn();
        syncProgress();
    });
    mediaEl.addEventListener("pause", () => {
        syncBtn();
        syncProgress();
    });
    mediaEl.addEventListener("timeupdate", syncProgress);
    mediaEl.addEventListener("ended", () => {
        if (_activeR2vMedia === mediaEl) _activeR2vMedia = null;
        mediaEl.currentTime = 0;
        syncBtn();
        syncProgress();
        progressWrap?.classList.remove("active", "playing");
        if (fill) fill.style.width = "0%";
    });
    if (progressWrap && fill) {
        progressWrap.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const dur = mediaEl.duration;
            if (!Number.isFinite(dur) || dur <= 0) return;
            const rect = progressWrap.getBoundingClientRect();
            const ratio = rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0;
            mediaEl.currentTime = Math.min(dur, Math.max(0, ratio * dur));
            syncProgress();
        });
    }
}

export function wireMediaDuration(mediaEl, durEl, onReady) {
    const apply = () => {
        if (!Number.isFinite(mediaEl.duration) || mediaEl.duration === Infinity) return;
        durEl.textContent = formatMediaDuration(mediaEl.duration);
        onReady?.(mediaEl.duration);
    };
    mediaEl.addEventListener("loadedmetadata", apply);
    if (mediaEl.readyState >= 1) apply();
}

/**
 * User-facing seconds (1 decimal). durationSec is the source of truth when set;
 * only fall back to frames for legacy rows that never stored durationSec.
 */
function resolveSegmentDurationSec(seg, defFc) {
    if (seg.durationSec != null && Number.isFinite(Number(seg.durationSec))) {
        const { durationSec } = durationToClampedMiniMaxFrames(seg.durationSec, 24);
        return durationSec;
    }
    const fc = parseInt(seg.frameCount ?? seg.length ?? seg._videoFrameCount ?? defFc, 10) || defFc;
    return preferredDurationSecFromFrames(fc, 24);
}

/** Apply seconds to a segment by index (avoids stale closures after normalize). */
function applyBatchSegmentDuration(editor, index, rawSec) {
    const taskKey = resolveTaskKey(editor.getTaskKey?.() || editor.taskTypeWidget?.value);
    const seg = editor.timeline.segments?.[index];
    if (!seg || !isVideoBatchTask(taskKey)) return null;
    const clamped = clamp(
        Number(rawSec) || defaultDurationSec(taskKey),
        minDurationSec(),
        maxDurationSec(),
    );
    const { frames, durationSec } = durationToClampedMiniMaxFrames(clamped, 24);
    seg.durationSec = durationSec;
    seg.frameCount = frames;
    seg.length = frames;
    seg._videoFrameCount = frames;
    // Stale drag preview must not override batch totals.
    if (editor._previewSegments) editor._previewSegments = null;
    normalizeImageBatchSegments(editor);
    return editor.timeline.segments[index] || null;
}

/** Flush visible 秒数 inputs into segments before a full card re-render. */
function flushBatchDurationInputs(editor) {
    const list = editor?.batchList;
    if (!list) return;
    const taskKey = resolveTaskKey(editor.getTaskKey?.() || editor.taskTypeWidget?.value);
    if (!isVideoBatchTask(taskKey)) return;
    for (const input of list.querySelectorAll("input[data-batch-sec-index]")) {
        const index = parseInt(input.getAttribute("data-batch-sec-index"), 10);
        if (!Number.isFinite(index)) continue;
        // 不提交用户正在编辑的输入框：半截值（如 "3."）会被当完成值提交，导致秒数"自己变"。
        if (input === document.activeElement) continue;
        clearTimeout(input._t);
        input._t = null;
        const displayed = parseFloat(input.value);
        if (!Number.isFinite(displayed)) continue;
        const seg = editor.timeline.segments?.[index];
        const current = Number(seg?.durationSec);
        // Skip if already in sync (avoid churn while typing the same committed value).
        if (seg && Number.isFinite(current) && roundDurationSec(displayed) === roundDurationSec(current)) {
            continue;
        }
        applyBatchSegmentDuration(editor, index, displayed);
    }
}

function formatPreviewFps(value) {
    const fps = Math.round(Number(value) * 100) / 100;
    if (Number.isInteger(fps)) return String(fps);
    return fps.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function stopPlayer(el) {
    const st = _players.get(el);
    if (!st) return;
    st.playing = false;
    if (st.timer) {
        clearInterval(st.timer);
        st.timer = null;
    }
}

function stopAllPlayers(root) {
    root?.querySelectorAll(".bd-batch-vpreview")?.forEach((wrap) => stopPlayer(wrap));
    pauseActiveR2vMedia(null);
    root?.querySelectorAll("video.bd-r2v-media, audio.bd-r2v-media")?.forEach((m) => {
        try { m.pause(); } catch (_) { /* ignore */ }
    });
}

export const IMAGE_BATCH_STYLES = `
.bd-btn.bd-disabled,.bd-btn:disabled{opacity:.38;cursor:not-allowed;pointer-events:none}
.bd-mode button.bd-disabled,.bd-mode button:disabled{opacity:.38;cursor:not-allowed;pointer-events:none}
.bd-batch{width:100%;box-sizing:border-box;display:flex;flex-direction:column;gap:8px}
.bd-batch-i2v-notice{display:none;color:#ffb74d;background:#3a2a12;border:1px solid #a67c00;border-radius:6px;padding:8px 10px;font-size:11px;line-height:1.5}
.bd-batch-i2v-notice.visible{display:block}
.bd-batch-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.bd-batch-run-select.active{background:#1a3a2a;color:#4fff8f;border-color:#4fff8f}
.bd-batch-run-all{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:#aaa;cursor:pointer;user-select:none}
.bd-batch-run-all.hidden{display:none!important}
.bd-batch-run-all input{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#4fff8f}
/* r2v 自动续接（ref2va 模型）：无参考素材时自动用上一段整段视频续接 */
.bd-batch-r2v-auto{display:inline-flex;align-items:center;gap:6px;color:#bbb;font-size:11px;background:#161616;border:1px solid #3a3a3a;border-radius:6px;padding:6px 10px;cursor:pointer;user-select:none;transition:border-color .15s,background .15s}
.bd-batch-r2v-auto:hover{border-color:#5a5a5a}
.bd-batch-r2v-auto.active{border-color:rgba(79,255,143,.55);background:#152018;color:#dfffe9}
.bd-batch-r2v-auto.hidden{display:none!important}
.bd-batch-r2v-auto input{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#4fff8f;flex-shrink:0}
.bd-batch-r2v-auto span{line-height:1.3}
.bd-batch-list{display:flex;flex-direction:column;gap:8px;width:100%;max-height:640px;overflow-y:auto;padding-right:2px}
.bd-batch-card{background:linear-gradient(165deg,#1a1a1a 0%,#141414 55%,#111 100%);border:1px solid #2c2c2c;border-radius:10px;padding:12px 14px;display:grid;gap:10px;align-items:stretch;box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}
/* t2v: 提示词为主，预览收成右侧窄栏 */
.bd-batch-card.bd-batch-plain{grid-template-columns:minmax(0,1fr) minmax(132px,168px)}
/* i2v / r2i: 源图或参考 | 提示词 | 窄预览 */
.bd-batch-card.bd-batch-source,.bd-batch-card.bd-batch-refs:not(.bd-batch-r2v){grid-template-columns:auto minmax(0,1fr) minmax(132px,168px)}
.bd-batch-plain .bd-batch-head,.bd-batch-source .bd-batch-head,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-head{padding-bottom:2px;border-bottom:1px solid rgba(255,255,255,.06);margin-bottom:2px}
.bd-batch-plain .bd-batch-head b,.bd-batch-source .bd-batch-head b,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-head b{color:#f0f0f0;font-size:12px;font-weight:650}
.bd-batch-plain .bd-batch-prompts,.bd-batch-source .bd-batch-prompts,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-prompts{background:#0c0c0c;border:1px solid #262626;border-radius:10px;padding:10px 12px;gap:6px}
.bd-batch-plain .bd-batch-prompts .bd-label,.bd-batch-source .bd-batch-prompts .bd-label,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-prompts .bd-label{color:#eaeaea;font-size:11px;font-weight:700;letter-spacing:.02em}
.bd-batch-plain .bd-batch-prompts textarea,.bd-batch-source .bd-batch-prompts textarea,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-prompts textarea{background:#101010;border-color:#2e2e2e;border-radius:8px;padding:10px;font-size:12px;line-height:1.45}
.bd-batch-plain .bd-batch-preview,.bd-batch-source .bd-batch-preview,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-preview{border-radius:10px;border-color:#262626;background:#0c0c0c}
/* ——— r2v asset stage (polished) ——— */
.bd-batch-card.bd-batch-r2v{display:flex;flex-direction:column;gap:12px;padding:14px 16px;background:linear-gradient(165deg,#1c1c1c 0%,#141414 52%,#111 100%);border:1px solid #2c2c2c;border-radius:12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.035);align-items:stretch}
.bd-batch-card.running{border-color:#4fff8f;box-shadow:0 0 0 1px rgba(79,255,143,.25)}
.bd-batch-card.done{border-color:#3a5080}
.bd-batch-card.run-skipped{opacity:.42}
/* selected / run-on must win over .done so timeline ↔ card selection stays visible */
.bd-batch-card.selected,.bd-batch-card.selected.done{border-color:#4fff8f;box-shadow:0 0 0 1px rgba(79,255,143,.35)}
.bd-batch-card.run-on:not(.run-skipped){border-color:#3a7a55}
.bd-batch-card.selected.run-on,.bd-batch-card.selected.run-on.done{border-color:#4fff8f;box-shadow:0 0 0 1px rgba(79,255,143,.4)}
.bd-batch-head{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.bd-batch-r2v .bd-batch-head{padding-bottom:2px;border-bottom:1px solid rgba(255,255,255,.06);margin-bottom:2px}
.bd-batch-head b{color:#ccc;font-size:11px}
.bd-batch-r2v .bd-batch-head b{color:#f0f0f0;font-size:13px;font-weight:650;letter-spacing:.02em}
.bd-batch-run-check{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#4fff8f;flex-shrink:0}
.bd-batch-head-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.bd-batch-fc{display:flex;align-items:center;gap:4px;color:#888;font-size:10px}
.bd-batch-r2v .bd-batch-fc{color:#9a9a9a;font-size:11px;gap:6px;background:#0e0e0e;border:1px solid #2a2a2a;border-radius:8px;padding:4px 8px}
.bd-batch-fc input{width:52px;background:#181818;border:1px solid #444;border-radius:4px;color:#eee;padding:3px 5px;font-size:11px}
.bd-batch-r2v .bd-batch-fc input{width:56px;background:#161616;border-color:#3a3a3a;border-radius:6px;padding:4px 6px}
.bd-batch-del{background:transparent;border:1px solid #553;color:#f88;border-radius:4px;padding:3px 8px;font-size:10px;cursor:pointer}
.bd-batch-r2v .bd-batch-del{border-radius:8px;padding:5px 10px;font-size:11px;border-color:#4a3030;color:#f0a0a0}
.bd-batch-del:hover{background:#3a1515}
.bd-batch-tags{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap}
.bd-batch-tag{display:inline-flex;align-items:center;gap:3px;background:#161616;border:1px solid #333;color:#bbb;border-radius:6px;padding:2px 7px;font-size:10px;line-height:1.2;white-space:nowrap}
/* P4 三级资产层级：角色标签来源标记——🎬 场景素材组 / 🌐 全局资产库 */
.bd-batch-tag.from-scene{background:#152a20;border-color:#2f6b4a;color:#aef0c8}
.bd-batch-tag.from-global{background:#1a1f2e;border-color:#3a4666;color:#b8c6f0}
.bd-batch-r2v-asset-sel select optgroup{color:#8f8f8f;font-size:10.5px;font-style:normal;background:#141414}
.bd-batch-r2v-asset-sel select optgroup option{color:#ddd;font-size:10.5px}
.bd-batch-shot-run{background:#1d3a2c;border:1px solid #2f6b4a;color:#7fffae;border-radius:6px;padding:4px 10px;font-size:11px;cursor:pointer;transition:filter .12s}
.bd-batch-shot-run:hover{filter:brightness(1.15)}
/* UI 2.2：单镜 mp4 下载按钮（复用生成钮风格，蓝绿色系区分） */
.bd-batch-shot-dl{background:#1c2a3a;border:1px solid #2f5b8a;color:#8fc8ff;border-radius:6px;padding:4px 10px;font-size:11px;cursor:pointer;transition:filter .12s}
.bd-batch-shot-dl:hover:not(:disabled){filter:brightness(1.2)}
.bd-batch-shot-dl:disabled{opacity:.4;cursor:not-allowed}
.bd-batch-head .bd-shot-dot{width:8px;height:8px;margin-left:6px;flex:0 0 auto}
.bd-batch-head .bd-shot-dot.st-success{background:#4fff8f}
.bd-batch-head .bd-shot-dot.st-running{background:#4da3ff}
.bd-batch-head .bd-shot-dot.st-review{background:#ffa94d}
.bd-batch-head .bd-shot-dot.st-failed{background:#ff6b6b}
.bd-batch-media{display:flex;flex-direction:column;gap:4px;min-width:88px;max-width:120px}
/* Left = assets (narrower) · Right = prompt + preview (wider) */
.bd-batch-r2v-body{display:grid;grid-template-columns:minmax(260px,.85fr) minmax(0,1.4fr);gap:12px;width:100%;align-items:stretch}
.bd-batch-r2v-assets{display:flex;flex-direction:column;gap:10px;min-width:0}
.bd-batch-r2v-main{display:flex;flex-direction:column;gap:10px;min-width:0;min-height:0}
.bd-r2v-section{background:#0c0c0c;border:1px solid #262626;border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:8px;min-width:0;box-sizing:border-box}
.bd-r2v-section-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.bd-r2v-section-title{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#eaeaea}
.bd-r2v-section-count{font-size:11px;color:#7d7d7d;font-variant-numeric:tabular-nums;letter-spacing:.02em}
.bd-batch-src{width:88px;height:88px;border:1px dashed #555;border-radius:4px;background:#111;display:flex;align-items:center;justify-content:center;cursor:pointer;overflow:hidden;color:#666;font-size:9px;text-align:center;padding:4px;box-sizing:border-box}
.bd-batch-src.has-img{border-style:solid;border-color:#444}
.bd-batch-src img{width:100%;height:100%;object-fit:contain;background:#000}
.bd-batch-refs{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;width:108px}
.bd-batch-r2v .bd-batch-refs{grid-template-columns:repeat(3,minmax(0,1fr));width:100%;max-width:none;gap:6px}
.bd-batch-r2v .bd-batch-ref.bd-r2v-pic-hidden{display:none!important}
.bd-r2v-pics-toggle{align-self:stretch;margin-top:2px;background:transparent;border:1px dashed #333;border-radius:8px;color:#9a9a9a;font-size:11px;padding:6px 8px;cursor:pointer;transition:border-color .15s,color .15s,background .15s}
.bd-r2v-pics-toggle:hover{border-color:#555;color:#ddd;background:#121212}
.bd-batch-ref{position:relative;aspect-ratio:1;border:1px dashed #555;border-radius:3px;background:#111;display:flex;align-items:center;justify-content:center;cursor:pointer;overflow:hidden;font-size:8px;color:#666}
.bd-batch-r2v .bd-batch-ref{aspect-ratio:1;min-height:0;border-radius:8px;border:1px dashed #333;background:#080808;color:#555;font-size:10px;transition:border-color .15s,background .15s,transform .12s}
.bd-batch-r2v .bd-batch-ref:hover{border-color:#5a5a5a;background:#101010}
.bd-batch-ref.has-img{border-style:solid}
.bd-batch-r2v .bd-batch-ref.has-img{border-color:#3a3a3a;background:#000}
.bd-batch-ref img{width:100%;height:100%;object-fit:cover}
.bd-batch-r2v .bd-batch-ref img{width:100%;height:100%;object-fit:contain;object-position:center;background:#000}
.bd-batch-r2v .bd-batch-ref .dot{position:absolute;left:6px;top:6px;width:7px;height:7px;border-radius:50%;background:#4fff8f;box-shadow:0 0 0 2px rgba(0,0,0,.5);z-index:2}
.bd-batch-r2v .bd-batch-ref .cap{position:absolute;left:0;right:0;bottom:0;padding:14px 6px 5px;background:linear-gradient(180deg,transparent,rgba(0,0,0,.78));color:#ddd;font-size:10px;font-weight:600;text-align:center;pointer-events:none;z-index:2}
.bd-batch-r2v .bd-batch-ref:not(.has-img) .cap{position:static;padding:0;background:none;color:#666;font-weight:500}
.bd-batch-ref .x{position:absolute;top:0;right:2px;color:#f88;font-size:10px;display:none;line-height:1}
.bd-batch-r2v .bd-batch-ref .x{top:4px;right:4px;width:20px;height:20px;border-radius:6px;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.72);color:#ff9a9a;font-size:14px;font-weight:700;z-index:3}
.bd-batch-ref:hover .x{display:block}
.bd-batch-r2v .bd-batch-ref:hover .x,.bd-batch-r2v .bd-batch-ref:focus-within .x{display:flex}
.bd-batch-media-block{display:flex;flex-direction:column;gap:4px;min-width:0}
.bd-batch-media-block .bd-label{color:#888;font-size:10px}
.bd-batch-audios,.bd-batch-videos{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;width:100%;max-width:420px}
.bd-batch-r2v .bd-batch-videos,.bd-batch-r2v .bd-batch-audios{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));max-width:none;gap:7px;width:100%}
.bd-batch-audio,.bd-batch-video{position:relative;min-height:44px;border:1px dashed #555;border-radius:4px;background:#111;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;cursor:pointer;padding:6px 4px;box-sizing:border-box;font-size:9px;color:#666;text-align:center;line-height:1.25}
.bd-batch-r2v .bd-batch-audio,.bd-batch-r2v .bd-batch-video{min-height:0;height:auto;flex-direction:column;align-items:stretch;justify-content:flex-start;gap:6px;padding:6px;border-radius:8px;border:1px dashed #333;background:#080808;text-align:left;font-size:11px;color:#777;transition:border-color .15s,background .15s}
.bd-batch-r2v .bd-batch-audio:hover,.bd-batch-r2v .bd-batch-video:hover{border-color:#555;background:#101010}
.bd-batch-audio.has-audio,.bd-batch-video.has-video{border-style:solid;border-color:#4a6a4a;color:#cfe;background:#152015}
.bd-batch-r2v .bd-batch-audio.has-audio,.bd-batch-r2v .bd-batch-video.has-video{border-color:#2f4a38;background:#101812;color:#d8ebe0}
.bd-batch-audio:hover,.bd-batch-video:hover{border-color:#7a9cff}
.bd-r2v-thumb{position:relative;width:38px;height:38px;border-radius:7px;background:#1a1a1a;border:1px solid #2e2e2e;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#666;font-size:14px;overflow:hidden}
.bd-batch-r2v .bd-batch-video .bd-r2v-thumb,.bd-r2v-thumb-video{width:100%;height:auto;aspect-ratio:16/9;border-radius:6px}
.bd-batch-r2v .bd-batch-audio .bd-r2v-thumb{width:100%;height:44px;border-radius:6px}
.bd-r2v-thumb-video video{width:100%;height:100%;object-fit:cover;display:block;background:#000;pointer-events:none}
.bd-r2v-play{position:absolute;inset:0;margin:auto;width:28px;height:28px;border:0;border-radius:50%;background:rgba(0,0,0,.62);color:#fff;font-size:12px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;z-index:2}
.bd-r2v-play:hover{background:rgba(20,20,20,.82);color:#4fff8f}
.bd-batch-r2v .has-audio .bd-r2v-thumb,.bd-batch-r2v .has-video .bd-r2v-thumb{border-color:#3a5a45;color:#8fdfb0;background:#152018}
.bd-r2v-meta{min-width:0;flex:1;display:flex;flex-direction:column;gap:2px}
.bd-batch-r2v .bd-batch-video .bd-r2v-meta,.bd-batch-r2v .bd-batch-audio .bd-r2v-meta{flex-direction:row;align-items:center;justify-content:space-between;gap:4px}
.bd-r2v-meta .tag{color:#cfcfcf;font-size:11px;font-weight:650}
.bd-r2v-dur{flex-shrink:0;min-width:2.6em;text-align:right;font-size:11px;color:#8a9;font-variant-numeric:tabular-nums}
.bd-batch-r2v .bd-batch-audio .name,.bd-batch-r2v .bd-batch-video .name{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8aa;font-size:10px;padding:0}
.bd-batch-r2v .bd-batch-video.has-video .name,.bd-batch-r2v .bd-batch-audio.has-audio .name{display:none}
.bd-batch-r2v .bd-batch-video:not(.has-video) .name,.bd-batch-r2v .bd-batch-audio:not(.has-audio) .name{display:block;color:#666}
.bd-batch-r2v .bd-batch-audio audio.bd-r2v-media{position:absolute;width:0;height:0;opacity:0;pointer-events:none}
.bd-r2v-progress{display:none;width:100%;height:3px;border-radius:99px;background:#222;overflow:hidden;cursor:pointer}
.bd-r2v-progress.active{display:block}
.bd-r2v-progress-fill{height:100%;width:0;background:linear-gradient(90deg,#2a6b4a,#4fff8f);border-radius:99px;transition:width .08s linear}
.bd-r2v-progress.playing .bd-r2v-progress-fill{transition:none}
.bd-batch-audio .name,.bd-batch-video .name{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#9ad;font-size:9px;padding:0 2px}
.bd-batch-audio .x,.bd-batch-video .x{position:absolute;top:1px;right:3px;color:#f88;font-size:12px;display:none;line-height:1}
.bd-batch-r2v .bd-batch-audio .x,.bd-batch-r2v .bd-batch-video .x{position:absolute;top:8px;right:8px;width:20px;height:20px;border-radius:6px;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.72);color:#ff9a9a;font-size:14px;font-weight:700;z-index:3}
.bd-batch-audio:hover .x,.bd-batch-video:hover .x{display:block}
.bd-batch-r2v .bd-batch-audio:hover .x,.bd-batch-r2v .bd-batch-video:hover .x{display:flex}
.bd-batch-prompts{display:flex;flex-direction:column;gap:4px;min-width:0}
.bd-batch-prompts .bd-label{color:#888;font-size:10px}
.bd-batch-r2v .bd-batch-prompts{background:#0c0c0c;border:1px solid #262626;border-radius:10px;padding:10px 12px;gap:6px;flex:1 1 auto;min-height:260px;display:flex;flex-direction:column}
.bd-batch-r2v .bd-batch-prompts .bd-label{color:#eaeaea;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.bd-batch-prompts textarea{width:100%;min-height:88px;background:#181818;border:1px solid #333;border-radius:4px;color:#eee;padding:6px;resize:vertical;font-size:11px;box-sizing:border-box;font-family:inherit;line-height:1.35}
.bd-batch-plain .bd-batch-prompts textarea,.bd-batch-source .bd-batch-prompts textarea{min-height:120px;height:100%;resize:vertical}
.bd-batch-r2v .bd-batch-prompts textarea{min-height:240px;height:100%;flex:1;resize:vertical;background:#101010;border-color:#2e2e2e;border-radius:8px;padding:10px;font-size:12px;line-height:1.45}
.bd-batch-preview{background:#0d0d0d;border:1px solid #333;border-radius:4px;min-height:100px;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:hidden;color:#555;font-size:10px;text-align:center;padding:4px;box-sizing:border-box}
.bd-batch-plain .bd-batch-preview,.bd-batch-source .bd-batch-preview,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-preview{width:100%;max-width:168px;min-height:120px;justify-self:end}
.bd-batch-r2v .bd-batch-preview{min-height:160px;flex:0 0 auto;height:auto;border-radius:10px;border-color:#262626;background:#0c0c0c;padding:8px;font-size:11px;color:#666}
.bd-batch-preview img{max-width:100%;max-height:140px;object-fit:contain;display:block}
.bd-batch-plain .bd-batch-preview img,.bd-batch-source .bd-batch-preview img{max-height:120px}
.bd-batch-r2v .bd-batch-preview img{max-height:100%}
.bd-batch-vpreview{width:100%;height:100%;display:flex;flex-direction:column;align-items:stretch;gap:4px;min-height:0}
.bd-batch-vpreview canvas{width:100%;flex:1 1 auto;min-height:72px;max-height:140px;background:#000;border-radius:3px;display:block;object-fit:contain}
.bd-batch-r2v .bd-batch-vpreview canvas{border-radius:8px;max-height:160px;min-height:96px}
.bd-batch-plain .bd-batch-vpreview canvas,.bd-batch-source .bd-batch-vpreview canvas{max-height:120px;min-height:64px}
.bd-batch-vpreview-ctrl{display:flex;align-items:center;justify-content:center;gap:6px;flex-shrink:0}
.bd-batch-vpreview-ctrl button{font-size:10px;padding:2px 8px}
.bd-batch-vpreview-meta{color:#666;font-size:9px;text-align:center;flex-shrink:0}
@media(max-width:860px){
.bd-batch-r2v-body,.bd-batch-r2v-foot{grid-template-columns:1fr}
.bd-batch-r2v .bd-batch-preview{min-height:110px}
.bd-batch-card.bd-batch-plain{grid-template-columns:minmax(0,1fr) minmax(120px,140px)}
.bd-batch-card.bd-batch-source,.bd-batch-card.bd-batch-refs:not(.bd-batch-r2v){grid-template-columns:auto minmax(0,1fr) minmax(120px,140px)}
}
@media(max-width:720px){
.bd-batch-card,.bd-batch-card.bd-batch-plain,.bd-batch-card.bd-batch-source,.bd-batch-card.bd-batch-refs:not(.bd-batch-r2v){grid-template-columns:1fr}
.bd-batch-plain .bd-batch-preview,.bd-batch-source .bd-batch-preview,.bd-batch-refs:not(.bd-batch-r2v) .bd-batch-preview{max-width:none;justify-self:stretch;min-height:80px}
.bd-batch-r2v .bd-batch-refs{grid-template-columns:repeat(3,minmax(0,1fr))}
}
/* 全局资产库（Phase B）：角色/场景资产自动注入 */
.bd-batch-r2v-assets{display:inline-flex;align-items:center;gap:6px;color:#bbb;font-size:11px;background:#161616;border:1px solid #3a3a3a;border-radius:6px;padding:6px 10px;cursor:pointer;user-select:none;transition:border-color .15s,background .15s}
.bd-batch-r2v-assets:hover{border-color:#5a5a5a}
.bd-batch-r2v-assets.active{border-color:rgba(79,255,143,.55);background:#152018;color:#dfffe9}
.bd-batch-r2v-assets.hidden{display:none!important}
.bd-batch-r2v-assets input{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#4fff8f;flex-shrink:0}
.bd-batch-r2v-assets span{line-height:1.3}
.bd-batch-assets{display:flex;flex-direction:column;gap:8px;background:#131313;border:1px solid #2c2c2c;border-radius:10px;padding:10px 12px}
.bd-batch-assets.hidden{display:none!important}
.bd-batch-assets-title{color:#eaeaea;font-size:11px;font-weight:700;letter-spacing:.02em;border-bottom:1px solid rgba(255,255,255,.06);padding-bottom:6px}
.bd-batch-assets-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.bd-batch-assets-label{color:#bbb;font-size:11px;flex-shrink:0;min-width:44px}
.bd-batch-assets-items{display:flex;gap:6px;flex-wrap:wrap;flex:1;min-width:120px}
.bd-batch-asset{position:relative;width:56px;height:56px;border:1px solid #3a3a3a;border-radius:6px;overflow:hidden;background:#0c0c0c;display:flex;align-items:flex-end;justify-content:center;cursor:default;flex-shrink:0}
.bd-batch-asset img{width:100%;height:100%;object-fit:cover;display:block}
.bd-batch-asset .cap{position:absolute;left:0;right:0;bottom:0;font-size:9px;color:#fff;background:rgba(0,0,0,.55);padding:1px 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center;cursor:text}
.bd-batch-asset .x{position:absolute;top:2px;right:2px;width:16px;height:16px;line-height:15px;text-align:center;font-size:11px;color:#fff;background:rgba(0,0,0,.65);border-radius:50%;cursor:pointer;display:none}
.bd-batch-asset:hover .x{display:block}
.bd-batch-asset-rename{position:absolute;left:0;right:0;bottom:0;width:100%;font-size:9px;color:#fff;background:rgba(0,0,0,.8);border:1px solid #4fff8f;border-radius:2px;padding:1px 2px;box-sizing:border-box;z-index:2}
.bd-batch-assets-def{background:#101010;border:1px solid #3a3a3a;color:#ddd;border-radius:6px;font-size:11px;padding:4px 6px;max-width:130px}
.bd-batch-assets-add{background:#1c1c1c;border:1px solid #3a3a3a;color:#ccc;border-radius:6px;font-size:11px;padding:5px 9px;cursor:pointer}
.bd-batch-assets-add:hover{background:#262626;border-color:#5a5a5a}
.bd-batch-r2v-asset-sel{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.bd-batch-r2v-asset-sel-item{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:#bbb}
.bd-batch-r2v-asset-sel-label{color:#999}
.bd-batch-r2v-asset-sel-item select{background:#101010;border:1px solid #3a3a3a;color:#ddd;border-radius:6px;font-size:11px;padding:3px 6px;max-width:150px}
/* 状态跟踪（阶段 C）：工具栏开关 + 每镜状态变更输入框 */
.bd-batch-r2v-state{display:inline-flex;align-items:center;gap:6px;color:#bbb;font-size:11px;background:#161616;border:1px solid #3a3a3a;border-radius:6px;padding:6px 10px;cursor:pointer;user-select:none;transition:border-color .15s,background .15s}
.bd-batch-r2v-state:hover{border-color:#5a5a5a}
.bd-batch-r2v-state.active{border-color:rgba(122,160,255,.55);background:#131a26;color:#dbe7ff}
.bd-batch-r2v-state.hidden{display:none!important}
.bd-batch-r2v-state input{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#7aa0ff;flex-shrink:0}
.bd-batch-r2v-state-input{display:flex;align-items:center;gap:6px;font-size:11px;color:#9db4e8;margin:6px 0 0;flex-wrap:wrap}
.bd-batch-r2v-state-input input{flex:1 1 160px;min-width:120px;background:#101010;border:1px solid #2e3a55;color:#dbe7ff;border-radius:6px;font-size:11px;padding:5px 8px;box-sizing:border-box}
.bd-batch-r2v-state-input input::placeholder{color:#5a6a8a}
/* 智能尾帧选择（待办④）：工具栏总开关复用状态跟踪样式 */
.bd-batch-r2v-smarttail{display:inline-flex;align-items:center;gap:6px;color:#bbb;font-size:11px;background:#161616;border:1px solid #3a3a3a;border-radius:6px;padding:6px 10px;cursor:pointer;user-select:none;transition:border-color .15s,background .15s}
.bd-batch-r2v-smarttail:hover{border-color:#5a5a5a}
.bd-batch-r2v-smarttail.active{border-color:rgba(122,160,255,.55);background:#131a26;color:#dbe7ff}
.bd-batch-r2v-smarttail.hidden{display:none!important}
.bd-batch-r2v-smarttail input{width:14px;height:14px;margin:0;cursor:pointer;accent-color:#7aa0ff;flex-shrink:0}
/* 每段卡片：智能尾帧三态下拉 */
.bd-batch-r2v-smarttail-sel{display:flex;align-items:center;gap:6px;font-size:11px;color:#9db4e8;margin:6px 0 0}
.bd-batch-r2v-smarttail-sel select{background:#101010;border:1px solid #2e3a55;color:#dbe7ff;border-radius:6px;font-size:11px;padding:4px 6px;box-sizing:border-box;cursor:pointer}
.bd-batch-r2v-smarttail-sel select:hover{border-color:#4a6aa0}
/* 里程碑 B：关键镜头三态下拉（二级一致性检测） */
.bd-batch-r2v-consistency{display:flex;align-items:center;gap:6px;font-size:11px;color:#9db4e8;margin:6px 0 0}
.bd-batch-r2v-consistency select{background:#101010;border:1px solid #3a5a3a;color:#dbe7ff;border-radius:6px;font-size:11px;padding:4px 6px;box-sizing:border-box;cursor:pointer}
.bd-batch-r2v-consistency select:hover{border-color:#5a9a5a}
/* 阶段 D：每段卡片「衔接模式」三态下拉（auto/ref2va/fl2va） */
.bd-batch-r2v-continuity{display:flex;align-items:center;gap:6px;font-size:11px;color:#9db4e8;margin:6px 0 0}
.bd-batch-r2v-continuity select{background:#101010;border:1px solid #2e3a55;color:#dbe7ff;border-radius:6px;font-size:11px;padding:4px 6px;box-sizing:border-box;cursor:pointer}
.bd-batch-r2v-continuity select:hover{border-color:#4a6aa0}
.bd-batch-r2v-continuity option{background:#131313}
/* ——— UI 2.1 P5：衔接徽章 + Prompt 视觉主体（body 右列）——— */
.bd-batch-cont{display:flex;align-items:center;gap:8px;background:linear-gradient(90deg,#14242e,#101822);border:1px solid #2c4a5a;border-radius:10px;padding:6px 10px;flex-wrap:wrap}
.bd-batch-cont-label{color:#8fd6ea;font-size:11px;font-weight:700;letter-spacing:.04em;white-space:nowrap}
.bd-batch-cont-sel{background:#0c151c;border:1px solid #3a5e70;color:#d9ecf5;border-radius:8px;font-size:11.5px;font-weight:600;padding:4px 8px;cursor:pointer;box-sizing:border-box;min-width:0;flex:1 1 auto}
.bd-batch-cont-sel:hover{border-color:#5d8ca3}
.bd-batch-cont-sel option{background:#101a22}
.bd-batch-r2v .bd-batch-prompts.bd-batch-prompts-center{background:#0c0c0c;border:1px solid #26303c;border-radius:10px;padding:10px 12px;gap:6px;flex:1 1 auto;display:flex;flex-direction:column;min-height:200px}
.bd-batch-r2v .bd-batch-prompts.bd-batch-prompts-center .bd-label{color:#e8f0f8;font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.bd-batch-r2v .bd-batch-prompts.bd-batch-prompts-center textarea{min-height:120px;height:100%;flex:1;resize:vertical;background:#101318;border-color:#2c3644;border-radius:8px;padding:10px;font-size:12.5px;line-height:1.5;color:#eee}
.bd-batch-r2v .bd-batch-prompts.bd-batch-prompts-center textarea:focus{border-color:#4a7a9a;outline:none}
/* ——— UI 2.0 第二优先级：镜头设置折叠模块（续接/状态/生成控制）——— */
.bd-batch-cfg{display:flex;flex-direction:column;gap:0;margin:2px 0 0;width:100%;box-sizing:border-box}
.bd-batch-cfg-head{display:flex;align-items:center;gap:6px;width:100%;border:1px solid #262626;border-radius:8px;background:#0e0e0e;color:#c6c6c6;font-size:11px;font-weight:600;padding:6px 10px;cursor:pointer;user-select:none;text-align:left;box-sizing:border-box;transition:border-color .12s,background .12s}
.bd-batch-cfg-head:hover{border-color:#3a3a3a;background:#141414}
.bd-batch-cfg-caret{display:inline-flex;color:#7d7d7d;font-size:9px;transition:transform .12s;flex-shrink:0}
.bd-batch-cfg-head.open .bd-batch-cfg-caret{transform:rotate(90deg)}
.bd-batch-cfg-summary{margin-left:auto;font-weight:400;color:#7a7a7a;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:58%}
.bd-batch-cfg-body{display:none;flex-direction:column;gap:6px;padding:8px 10px;border:1px solid #1f1f1f;border-top:none;border-radius:0 0 8px 8px;background:#0a0a0a;box-sizing:border-box}
.bd-batch-cfg-head.open+.bd-batch-cfg-body{display:flex}
.bd-batch-cfg-group{display:flex;flex-direction:column;gap:3px}
.bd-batch-cfg-group+.bd-batch-cfg-group{margin-top:4px;padding-top:6px;border-top:1px dashed #242424}
.bd-batch-cfg-group-title{font-size:10px;font-weight:700;color:#5a6a85;letter-spacing:.04em;margin:0;text-transform:uppercase}
/* FL2VA 模式：首帧/结束帧槽位强调（区别于普通参考图槽位） */
.bd-batch-r2v-body .bd-batch-ref .cap.bd-fl2va-cap{color:#f2c94c;font-weight:600}
`;

const BATCH_CHUNK_SIZE = 8 * 1024 * 1024;
const BATCH_UPLOAD_SOFT_LIMIT = 95 * 1024 * 1024;

async function uploadImage(file) {
    const body = new FormData();
    body.append("image", file);
    body.append("type", "input");
    body.append("overwrite", "true");
    const resp = await api.fetchApi("/upload/image", { method: "POST", body });
    if (!resp.ok) throw new Error(await resp.text() || `Upload failed (${resp.status})`);
    return resp.json();
}

async function uploadChunked(file) {
    const uploadId = crypto.randomUUID();
    const totalChunks = Math.ceil(file.size / BATCH_CHUNK_SIZE);
    for (let i = 0; i < totalChunks; i++) {
        const start = i * BATCH_CHUNK_SIZE;
        const end = Math.min(start + BATCH_CHUNK_SIZE, file.size);
        const body = new FormData();
        body.append("upload_id", uploadId);
        body.append("chunk_index", String(i));
        body.append("total_chunks", String(totalChunks));
        body.append("filename", file.name);
        body.append("chunk", file.slice(start, end), `${file.name}.part`);
        const resp = await api.fetchApi("/minimax/director/upload_chunk", { method: "POST", body });
        if (!resp.ok) throw new Error(await resp.text() || t("upload.chunkFailed", { status: resp.status }));
        const data = await resp.json();
        if (data.name) return data;
    }
    throw new Error(t("upload.chunkIncomplete"));
}

async function uploadMedia(file) {
    if (file.size <= BATCH_UPLOAD_SOFT_LIMIT) {
        try {
            return await uploadImage(file);
        } catch (err) {
            const msg = String(err?.message || err || "");
            if (!/too large|size|413/i.test(msg)) throw err;
        }
    }
    return uploadChunked(file);
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

export function mountImageBatchPanel(root) {
    const panel = document.createElement("div");
    panel.className = "bd-batch hidden";
    panel.dataset.r = "batch-panel";
    panel.innerHTML = `
        <div class="bd-batch-toolbar">
            <button type="button" class="bd-btn bd-btn-primary" data-a="batch-add" data-i18n="batch.addPromptGroup">+ 添加提示词组</button>
            <button type="button" class="bd-btn bd-batch-run-select hidden" data-a="batch-run-select" data-i18n="toolbar.runSelect" data-i18n-title="tooltip.batchRunSelect">选择运行</button>
            <label class="bd-batch-run-all hidden" data-r="batch-run-all-wrap" data-i18n-title="tooltip.runSelectAll">
                <input type="checkbox" data-r="batch-run-all-cb">
                <span data-i18n="toolbar.selectAll">全选</span>
            </label>
            <label class="bd-batch-r2v-auto hidden" data-r="batch-r2v-auto" title="${t("tooltip.r2vAutoContinuity")}">
                <input type="checkbox" data-r="batch-r2v-auto-cb">
                <span data-i18n="panel.batch.r2vAutoContinuity">自动续接上段</span>
            </label>
            <label class="bd-batch-r2v-assets hidden" data-r="batch-r2v-assets" title="${t("tooltip.globalAssets")}">
                <input type="checkbox" data-r="batch-r2v-assets-cb">
                <span data-i18n="panel.batch.globalAssets">全局资产库</span>
            </label>
            <label class="bd-batch-r2v-state hidden" data-r="batch-r2v-state" title="${t("tooltip.stateTracking")}">
                <input type="checkbox" data-r="batch-r2v-state-cb">
                <span data-i18n="panel.batch.stateTracking">状态跟踪</span>
            </label>
            <label class="bd-batch-r2v-smarttail hidden" data-r="batch-r2v-smarttail" title="${t("tooltip.smartTail")}">
                <input type="checkbox" data-r="batch-r2v-smarttail-cb">
                <span data-i18n="panel.batch.smartTail">智能尾帧</span>
            </label>
            <span class="bd-meta" data-r="batch-hint" data-i18n="batch.hint.defaultImage">每组生成 1 张图片</span>
        </div>
        <div class="bd-batch-assets hidden" data-r="batch-assets-panel">
            <div class="bd-batch-assets-title" data-i18n="panel.batch.assetsTitle">🌐 全局资产（角色 / 场景 / 道具 / 风格参考）</div>
            <div class="bd-batch-assets-row">
                <span class="bd-batch-assets-label" data-i18n="panel.batch.castLabel">角色库</span>
                <div class="bd-batch-assets-items" data-r="batch-assets-cast"></div>
                <select class="bd-batch-assets-def" data-r="batch-assets-cast-def" title="${t("tooltip.globalAssetsDefault")}"></select>
                <button type="button" class="bd-batch-assets-add" data-r="batch-assets-cast-add" data-i18n="panel.batch.addAsset">+ 添加</button>
            </div>
            <div class="bd-batch-assets-row">
                <span class="bd-batch-assets-label" data-i18n="panel.batch.locLabel">场景库</span>
                <div class="bd-batch-assets-items" data-r="batch-assets-loc"></div>
                <select class="bd-batch-assets-def" data-r="batch-assets-loc-def" title="${t("tooltip.globalAssetsDefault")}"></select>
                <button type="button" class="bd-batch-assets-add" data-r="batch-assets-loc-add" data-i18n="panel.batch.addAsset">+ 添加</button>
            </div>
            <div class="bd-batch-assets-row">
                <span class="bd-batch-assets-label" data-i18n="panel.batch.propLabel">道具库</span>
                <div class="bd-batch-assets-items" data-r="batch-assets-prop"></div>
                <button type="button" class="bd-batch-assets-add" data-r="batch-assets-prop-add" data-i18n="panel.batch.addAsset">+ 添加</button>
            </div>
            <div class="bd-batch-assets-row">
                <span class="bd-batch-assets-label" data-i18n="panel.batch.styleLabel">风格参考库</span>
                <div class="bd-batch-assets-items" data-r="batch-assets-style"></div>
                <button type="button" class="bd-batch-assets-add" data-r="batch-assets-style-add" data-i18n="panel.batch.addAsset">+ 添加</button>
            </div>
        </div>
        <div class="bd-batch-i2v-notice" data-r="batch-i2v-notice"></div>
        <div class="bd-batch-list" data-r="batch-list"></div>`;
    root.appendChild(panel);
    return {
        panel,
        list: panel.querySelector('[data-r="batch-list"]'),
        hint: panel.querySelector('[data-r="batch-hint"]'),
        i2vNotice: panel.querySelector('[data-r="batch-i2v-notice"]'),
        addBtn: panel.querySelector('[data-a="batch-add"]'),
        runSelectBtn: panel.querySelector('[data-a="batch-run-select"]'),
        runSelectAllWrap: panel.querySelector('[data-r="batch-run-all-wrap"]'),
        runSelectAllCb: panel.querySelector('[data-r="batch-run-all-cb"]'),
        r2vAutoWrap: panel.querySelector('[data-r="batch-r2v-auto"]'),
        r2vAutoCb: panel.querySelector('[data-r="batch-r2v-auto-cb"]'),
        r2vAssetsWrap: panel.querySelector('[data-r="batch-r2v-assets"]'),
        r2vAssetsCb: panel.querySelector('[data-r="batch-r2v-assets-cb"]'),
        assetsPanel: panel.querySelector('[data-r="batch-assets-panel"]'),
        assetsCast: panel.querySelector('[data-r="batch-assets-cast"]'),
        assetsLoc: panel.querySelector('[data-r="batch-assets-loc"]'),
        assetsCastDef: panel.querySelector('[data-r="batch-assets-cast-def"]'),
        assetsLocDef: panel.querySelector('[data-r="batch-assets-loc-def"]'),
        assetsCastAdd: panel.querySelector('[data-r="batch-assets-cast-add"]'),
        assetsLocAdd: panel.querySelector('[data-r="batch-assets-loc-add"]'),
        assetsProp: panel.querySelector('[data-r="batch-assets-prop"]'),
        assetsStyle: panel.querySelector('[data-r="batch-assets-style"]'),
        assetsPropAdd: panel.querySelector('[data-r="batch-assets-prop-add"]'),
        assetsStyleAdd: panel.querySelector('[data-r="batch-assets-style-add"]'),
        stateWrap: panel.querySelector('[data-r="batch-r2v-state"]'),
        stateCb: panel.querySelector('[data-r="batch-r2v-state-cb"]'),
        smartTailWrap: panel.querySelector('[data-r="batch-r2v-smarttail"]'),
        smartTailCb: panel.querySelector('[data-r="batch-r2v-smarttail-cb"]'),
    };
}

export function wireBatchRunSelectControls(editor, batchUi) {
    editor.batchRunSelectBtn = batchUi.runSelectBtn;
    editor.batchRunSelectAllWrap = batchUi.runSelectAllWrap;
    editor.batchRunSelectAllCb = batchUi.runSelectAllCb;
    batchUi.runSelectBtn?.addEventListener("click", (e) => {
        e.stopPropagation();
        editor.toggleRunSelectMode?.();
    });
    batchUi.runSelectAllCb?.addEventListener("change", (e) => {
        e.stopPropagation();
        if (!editor.isRunSelectEnabled?.()) return;
        editor.setRunSelectionAll?.(batchUi.runSelectAllCb.checked);
    });
}

function cloneRefs(refs) {
    if (!Array.isArray(refs) || !refs.length) return [];
    try {
        return JSON.parse(JSON.stringify(refs));
    } catch {
        return refs.map((r) => ({ ...r }));
    }
}

/** Copy global.refs into batch segments that have no refs (r2i / r2v). */
export function migrateGlobalRefsIntoBatchSegments(editor, taskKey) {
    const key = resolveTaskKey(taskKey || editor.getTaskKey?.() || "");
    if (key !== "r2i" && key !== "r2v") return false;
    const globalRefs = editor.timeline?.global?.refs;
    if (!Array.isArray(globalRefs) || !globalRefs.length) return false;
    let moved = false;
    for (const seg of editor.timeline.segments || []) {
        if ((seg.refs || []).length) continue;
        seg.refs = cloneRefs(globalRefs);
        moved = true;
    }
    return moved;
}

/** ---------- 全局资产库（Phase B）：角色/场景资产自动注入 ---------- */

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

function getAssetsBlock(editor) {
    const timeline = editor.timeline || (editor.timeline = {});
    if (!timeline.assets || typeof timeline.assets !== "object") timeline.assets = {};
    const a = timeline.assets;
    // 资产键统一：旧数据 assets.location（单数）→ assets.locations（复数）。
    migrateLocationAssetKey(a);
    // 全局资产库升级（待办⑥）：道具 prop / 风格参考 style 类目。
    // 同时做资产名防御清洗（拒绝 [object File]），防止脏名流入序列化。
    for (const kind of ["cast", "locations", "props", "styles"]) {
        if (!Array.isArray(a[kind])) a[kind] = [];
        a[kind] = a[kind]
            .filter((x) => x && typeof x === "object")
            .map(sanitizeAssetName);
    }
    if (a.defaultCastId == null) a.defaultCastId = "";
    if (a.defaultLocationId == null) a.defaultLocationId = "";
    return a;
}

function newAssetId() {
    return "a" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

async function addGlobalAsset(editor, kind) {
    pickFile("image/*,.jpg,.jpeg,.png,.webp,.bmp,.gif", async (file) => {
        try {
            if (!file?.type?.startsWith("image/") && !/\.(jpe?g|png|webp|bmp|gif)$/i.test(file.name || "")) {
                throw new Error("Not an image file");
            }
            const uploaded = await uploadImage(file);
            const imageFile = relPath(uploaded);
            if (!imageFile) throw new Error("Upload returned empty filename");
            const a = getAssetsBlock(editor);
            const list = a[kind] || (a[kind] = []);
            const asset = { id: newAssetId(), name: fileBaseName(file) || file.name || "", imageFile };
            list.push(asset);
            if (kind === "cast") {
                if (!a.defaultCastId) a.defaultCastId = asset.id;
            } else if (kind === "locations") {
                if (!a.defaultLocationId) a.defaultLocationId = asset.id;
            }
            editor.renderImageBatchGroups?.();
            editor.commit?.(false, { syncTimeline: true });
            editor.updateDomWidgetHeight?.();
        } catch (err) {
            console.error("[MiniMax H3Director] global asset upload failed:", err);
            alert(t("upload.alertFailed", { err: err?.message || err }));
        }
    });
}

function removeGlobalAsset(editor, kind, id) {
    const a = getAssetsBlock(editor);
    const list = a[kind] || [];
    const idx = list.findIndex((x) => x.id === id);
    if (idx < 0) return;
    list.splice(idx, 1);
    if (kind === "cast") {
        if (a.defaultCastId === id) a.defaultCastId = a.cast[0]?.id || "";
    } else if (kind === "locations") {
        if (a.defaultLocationId === id) a.defaultLocationId = a.locations[0]?.id || "";
    }
    for (const seg of editor.timeline.segments || []) {
        if (seg.castId === id) seg.castId = "";
        if (seg.locationId === id) seg.locationId = "";
    }
    editor.renderImageBatchGroups?.();
    editor.commit?.(false, { syncTimeline: true });
    editor.updateDomWidgetHeight?.();
}

function renderAssetItem(editor, kind, asset) {
    const el = document.createElement("div");
    el.className = "bd-batch-asset";
    el.title = asset.name || asset.id || "";
    el.innerHTML = "";
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
        removeGlobalAsset(editor, kind, asset.id);
    };
    el.appendChild(x);
    // 改名：点击名称就地编辑，回车/失焦保存。改名即改「提示词命名自动匹配」用的名字。
    const doRename = () => {
        const input = document.createElement("input");
        input.className = "bd-batch-asset-rename";
        input.value = asset.name || asset.id || "";
        input.spellcheck = false;
        const commit = () => {
            const v = input.value.trim();
            if (v) asset.name = v;
            editor.renderImageBatchGroups?.();
            editor.commit?.(false, { syncTimeline: true });
            editor.updateDomWidgetHeight?.();
        };
        const cancel = () => {
            input.replaceWith(cap);
        };
        input.onkeydown = (e) => {
            e.stopPropagation();
            if (e.key === "Enter") { commit(); }
            else if (e.key === "Escape") { cancel(); }
        };
        input.onblur = commit;
        cap.replaceWith(input);
        input.focus();
        input.select();
    };
    cap.onclick = (e) => {
        e.stopPropagation();
        doRename();
    };
    return el;
}

function fillAssetDefSelect(sel, assets, currentId) {
    if (!sel) return;
    sel.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = t("batch.assets.noDefault");
    sel.appendChild(none);
    for (const asset of assets) {
        const opt = document.createElement("option");
        opt.value = asset.id;
        opt.textContent = asset.name || asset.id;
        if (asset.id === currentId) opt.selected = true;
        sel.appendChild(opt);
    }
    sel.value = currentId || "";
}

function renderGlobalAssetsPanel(editor) {
    const a = getAssetsBlock(editor);
    const castBox = editor.batchAssetsCast;
    const locBox = editor.batchAssetsLoc;
    const propBox = editor.batchAssetsProp;
    const styleBox = editor.batchAssetsStyle;
    if (castBox) {
        castBox.innerHTML = "";
        for (const asset of a.cast) castBox.appendChild(renderAssetItem(editor, "cast", asset));
    }
    if (locBox) {
        locBox.innerHTML = "";
        for (const asset of a.locations) locBox.appendChild(renderAssetItem(editor, "locations", asset));
    }
    if (propBox) {
        propBox.innerHTML = "";
        for (const asset of a.props) propBox.appendChild(renderAssetItem(editor, "props", asset));
    }
    if (styleBox) {
        styleBox.innerHTML = "";
        for (const asset of a.styles) styleBox.appendChild(renderAssetItem(editor, "styles", asset));
    }
    fillAssetDefSelect(editor.batchAssetsCastDef, a.cast, a.defaultCastId);
    fillAssetDefSelect(editor.batchAssetsLocDef, a.locations, a.defaultLocationId);
}

/**
 * P4 三级资产层级：段资产候选池 = 当前场景素材组（优先）→ 全局资产库（兜底）。
 * 与后端 gen_timeline._scene_asset_pool 同语义（场景优先、id 去重），保证
 * 「场景素材组里配的角色/场景」在段卡片下拉里可选、可用。
 * @returns {{scene: object|null, sceneAssets: object[], globalAssets: object[], pool: object[]}}
 */
function resolveSegmentAssetPool(editor, seg, kind) {
    const a = getAssetsBlock(editor);
    const globalAssets = Array.isArray(a[kind]) ? a[kind] : [];
    const scenes = editor.timeline?.scenes || [];
    const scene = scenes.find((s) => s && s.id === seg?.sceneId) || null;
    const sceneAssets = (scene?.assets && Array.isArray(scene.assets[kind]) ? scene.assets[kind] : []);
    if (!sceneAssets.length) return { scene, sceneAssets, globalAssets, pool: globalAssets };
    const seen = new Set();
    const pool = [];
    for (const asset of sceneAssets) {
        if (asset?.id && !seen.has(asset.id)) { seen.add(asset.id); pool.push(asset); }
    }
    for (const asset of globalAssets) {
        if (asset?.id && !seen.has(asset.id)) { seen.add(asset.id); pool.push(asset); }
    }
    return { scene, sceneAssets, globalAssets, pool };
}

function makeSegmentAssetSelect(editor, kind, seg) {
    const { scene, sceneAssets, globalAssets } = resolveSegmentAssetPool(editor, seg, kind);
    const current = kind === "cast" ? seg.castId : seg.locationId;
    const wrap = document.createElement("label");
    wrap.className = "bd-batch-r2v-asset-sel-item";
    const label = document.createElement("span");
    label.className = "bd-batch-r2v-asset-sel-label";
    label.textContent = kind === "cast" ? t("batch.assets.cast") : t("batch.assets.loc");
    const sel = document.createElement("select");
    sel.title = scene
        ? t("tooltip.assetPoolScene", { scene: scene.name || "" })
        : t("tooltip.assetPoolGlobal");
    const none = document.createElement("option");
    none.value = "";
    none.textContent = t("batch.assets.none");
    sel.appendChild(none);
    // 🎬 当前场景素材组（优先）。
    if (sceneAssets.length) {
        const ogScene = document.createElement("optgroup");
        ogScene.label = `🎬 ${scene?.name || t("assets.pool.scene")}`;
        for (const asset of sceneAssets) {
            const opt = document.createElement("option");
            opt.value = asset.id;
            opt.textContent = asset.name || asset.id;
            if (asset.id === current) opt.selected = true;
            ogScene.appendChild(opt);
        }
        sel.appendChild(ogScene);
    }
    // 🌐 全局资产库（兜底，场景已含的 id 去重）。
    if (globalAssets.length) {
        const ogGlobal = document.createElement("optgroup");
        ogGlobal.label = `🌐 ${t("assets.pool.global")}`;
        for (const asset of globalAssets) {
            if (sceneAssets.some((s) => s.id === asset.id)) continue;
            const opt = document.createElement("option");
            opt.value = asset.id;
            opt.textContent = asset.name || asset.id;
            if (asset.id === current) opt.selected = true;
            ogGlobal.appendChild(opt);
        }
        sel.appendChild(ogGlobal);
    }
    sel.value = current || "";
    sel.onchange = () => {
        if (kind === "cast") {
            seg.castId = sel.value;
            seg.castManual = true;
        } else {
            seg.locationId = sel.value;
            seg.locationManual = true;
        }
        editor.commit?.(false, { syncTimeline: true });
        editor.scheduleRender?.();
    };
    wrap.appendChild(label);
    wrap.appendChild(sel);
    return wrap;
}

/** 架构固底③：本镜生成模式（R2V / FL2V）。
 *  写 seg.taskType；选「自动」时删字段，跟随全局 task_type + continuity 路由规则。
 *  后端 gen_timeline 解析：seg_task = taskType || 全局 task_type，continuityMode 可再覆盖。 */
function normalizeSegmentMode(tt) {
    const v = String(tt || "").toLowerCase();
    if (v === "fl2v" || v.includes("fl2v")) return "fl2v";
    if (v === "r2v" || v.includes("r2v")) return "r2v";
    return "auto";
}

function makeSegmentModeSelect(editor, seg) {
    const wrap = document.createElement("label");
    wrap.className = "bd-batch-r2v-asset-sel-item";
    wrap.style.flex = "0 0 auto";
    const label = document.createElement("span");
    label.className = "bd-batch-r2v-asset-sel-label";
    label.textContent = t("shot.mode") || "模式";
    const sel = document.createElement("select");
    sel.className = "bd-batch-mode-sel";
    const opts = [
        ["auto", t("shot.mode.auto") || "自动"],
        ["r2v", t("shot.mode.r2v") || "R2V"],
        ["fl2v", t("shot.mode.fl2v") || "FL2V"],
    ];
    const cur = normalizeSegmentMode(seg.taskType);
    for (const [v, labelText] of opts) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = labelText;
        if (v === cur) o.selected = true;
        sel.appendChild(o);
    }
    sel.value = cur;
    sel.onchange = () => {
        if (sel.value === "auto") delete seg.taskType;
        else seg.taskType = sel.value;
        editor.scheduleTimelineSync?.();
        editor.commit?.(false, { syncTimeline: true });
        editor.renderImageBatchGroups?.();
    };
    wrap.appendChild(label);
    wrap.appendChild(sel);
    return wrap;
}
/** 写入 seg.sceneId；架构固底①：有场景时本镜必须归属一个场景（无「无场景」空选项），
 *  只有场景列表为空时才显示「未建场景」占位。段仍可独立导出。 */
function makeSegmentSceneSelect(editor, seg) {
    const scenes = editor.timeline.scenes || [];
    const wrap = document.createElement("label");
    wrap.className = "bd-batch-r2v-asset-sel-item";
    wrap.style.flex = "0 0 auto";
    const label = document.createElement("span");
    label.className = "bd-batch-r2v-asset-sel-label";
    label.textContent = t("scene.belong") || "所属场景";
    const sel = document.createElement("select");
    const sorted = [...scenes].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    if (!sorted.length) {
        const none = document.createElement("option");
        none.value = "";
        none.textContent = t("scene.noScenes") || "未建场景";
        sel.appendChild(none);
    }
    for (const sc of sorted) {
        const opt = document.createElement("option");
        opt.value = sc.id;
        opt.textContent = sc.name || sc.id;
        if (sc.id === seg.sceneId) opt.selected = true;
        sel.appendChild(opt);
    }
    // 架构固底①：有场景且本镜尚未归属时，默认归属第一个场景（order 最小），与
    // ensureShotsInScene 的归一化逻辑一致，避免「无场景」残留。
    if (sorted.length && !seg.sceneId) seg.sceneId = sorted[0].id;
    sel.value = seg.sceneId || (sorted[0]?.id || "");
    sel.onchange = () => {
        seg.sceneId = sel.value || (sorted[0]?.id || "");
        editor.commit?.(false, { syncTimeline: true });
        editor.scheduleRender?.();
    };
    wrap.appendChild(label);
    wrap.appendChild(sel);
    return wrap;
}
function autoMatchSegmentAssets(seg, assets) {
    if (!assets || !assets.length || !seg || typeof seg.prompt !== "string" || !seg.prompt.trim()) return "";
    const text = seg.prompt;
    for (const asset of assets) {
        const name = (asset.name || "").trim();
        if (name.length < 2) continue;
        if (text.includes(name)) return asset.id;
    }
    return "";
}

export function ensureImageBatchTimeline(editor) {
    editor.timeline.editMode = "segment";
    editor.timeline.output = editor.timeline.output || {};
    const taskKey = resolveTaskKey(editor.getTaskKey?.() || editor.taskTypeWidget?.value);
    editor.timeline.output.mode = "fixed";
    if (!editor.timeline.output.aspectRatio) editor.timeline.output.aspectRatio = DEFAULT_ASPECT_RATIO;
    if (editor.timeline.output.megapixels == null) editor.timeline.output.megapixels = DEFAULT_MEGAPIXELS;
    if (editor.timeline.output.multiple == null) editor.timeline.output.multiple = MINIMAX_CANVAS_MULTIPLE;
    if (!isVideoBatchTask(taskKey)) {
        editor.timeline.output.exportMode = "all";
    }
    const defFc = defaultFrameCount(taskKey);
    if (taskKey === "i2v") {
        editor.timeline.video = {
            fileName: "",
            videoFile: "",
            subfolder: "",
            type: "input",
            frames: [],
            frameMap: [],
        };
        editor.timeline.videoClips = [];
    }
    if (!editor.timeline.segments?.length) {
        editor.timeline.segments = [newBatchSegment({ durationSec: defaultDurationSec(taskKey) })];
    }
    // r2i/r2v need per-group refs. If the user came from rv2v (global refs) or left
    // refs only on global, copy them into empty batch groups so generation actually
    // receives reference_image_* — otherwise it silently behaves like t2v/t2i.
    migrateGlobalRefsIntoBatchSegments(editor, taskKey);
    for (const seg of editor.timeline.segments) {
        if (isVideoBatchTask(taskKey)) {
            const { frames, durationSec } = durationToClampedMiniMaxFrames(
                resolveSegmentDurationSec(seg, defFc),
                24,
            );
            seg.durationSec = durationSec;
            seg.frameCount = frames;
            seg.length = frames;
            seg._videoFrameCount = frames;
        } else {
            const prevFc = parseInt(seg.frameCount ?? seg.length, 10) || 0;
            if (prevFc > 1) seg._videoFrameCount = prevFc;
            seg.frameCount = 1;
            seg.length = 1;
        }
        seg.negativePrompt = seg.negativePrompt ?? "";
        seg.genImage = seg.genImage || { imageFile: seg.imageFile || "" };
        // Do NOT clear refs for i2v — backend ignores them, but wiping here breaks
        // r2v → i2v → r2v (user loses uploaded reference images).
        seg.refs = seg.refs || [];
        seg.refAudios = seg.refAudios || seg.ref_audios || [];
        seg.refVideos = seg.refVideos || seg.ref_videos || [];
        seg.previewB64 = seg.previewB64 || "";
        seg.previewFrames = seg.previewFrames || [];
        seg.previewFps = seg.previewFps || parseFloat(editor.frameRateWidget?.value || 24);
        if (!seg.id) seg.id = newBatchSegment().id;
    }
    normalizeImageBatchSegments(editor);
}

/** 架构固底①：每个 Shot 必须属于一个 Scene。
 *  scenes 非空时，把 sceneId 为空或指向不存在场景的 segment 自动归入
 *  order 最小的场景（单场景场景下 = 唯一场景）。返回是否发生变更。
 *  在 normalizeImageBatchSegments 开头调用，所有 commit 链路统一兜底。 */
export function ensureShotsInScene(editor) {
    const scenes = editor.timeline?.scenes;
    if (!Array.isArray(scenes) || !scenes.length) return false;
    const sorted = scenes.slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const first = sorted[0];
    if (!first?.id) return false;
    const validIds = new Set(scenes.map((s) => s.id));
    const segs = editor.timeline?.segments || [];
    let changed = false;
    for (const seg of segs) {
        if (!seg) continue;
        if (!seg.sceneId || !validIds.has(seg.sceneId)) {
            seg.sceneId = first.id;
            changed = true;
        }
    }
    return changed;
}

/** 架构固底②：Scene 资产自动继承给 Shot。
 *  段未手动指定 castId/locationId 时，自动继承所属场景的 defaultCastId/defaultLocationId；
 *  已有值或已手动选择（castManual/locationManual=true，含显式"无"）不覆盖（手动优先）。
 *  无场景归属或场景未设默认时不动作。返回是否发生变更。
 *  在 normalizeImageBatchSegments 中 ensureShotsInScene 之后调用。 */
export function inheritSceneAssetsToShots(editor) {
    const scenes = editor.timeline?.scenes;
    if (!Array.isArray(scenes) || !scenes.length) return false;
    const byId = new Map(scenes.filter((s) => s && s.id).map((s) => [s.id, s]));
    const segs = editor.timeline?.segments || [];
    let changed = false;
    for (const seg of segs) {
        if (!seg) continue;
        const sc = byId.get(seg.sceneId || "");
        if (!sc) continue;
        // 只继承"未选择"的段：castManual=true（含显式选"无"）表示用户已做决定，不得覆盖。
        if (!seg.castId && !seg.castManual && sc.defaultCastId) { seg.castId = sc.defaultCastId; changed = true; }
        if (!seg.locationId && !seg.locationManual && sc.defaultLocationId) { seg.locationId = sc.defaultLocationId; changed = true; }
    }
    return changed;
}

export function normalizeImageBatchSegments(editor) {
    ensureShotsInScene(editor);
    inheritSceneAssetsToShots(editor);
    const taskKey = resolveTaskKey(editor.getTaskKey?.() || editor.taskTypeWidget?.value);
    const isVideo = isVideoBatchTask(taskKey);
    const defFc = defaultFrameCount(taskKey);
    const defSec = defaultDurationSec(taskKey);
    let start = 0;
    const fixed = [];
    for (const seg of editor.timeline.segments) {
        let fc = 1;
        let durationSec;
        if (isVideo) {
            const resolved = durationToClampedMiniMaxFrames(
                clamp(resolveSegmentDurationSec(seg, defFc) || defSec, minDurationSec(), maxDurationSec()),
                24,
            );
            fc = resolved.frames;
            durationSec = resolved.durationSec;
        }
        fixed.push({
            ...seg,
            start,
            length: fc,
            frameCount: fc,
            ...(isVideo ? { durationSec } : {}),
            negativePrompt: seg.negativePrompt ?? "",
            genImage: seg.genImage || { imageFile: "" },
            refs: seg.refs || [],
            refAudios: seg.refAudios || [],
            refVideos: seg.refVideos || [],
            _videoFrameCount: isVideo ? fc : seg._videoFrameCount,
            previewB64: seg.previewB64 || "",
            previewFrames: seg.previewFrames || [],
            previewFps: seg.previewFps || parseFloat(editor.frameRateWidget?.value || 24),
        });
        start += fc;
    }
    if (!fixed.length) fixed.push(newBatchSegment({ durationSec: defSec }));
    editor.timeline.segments = fixed;
    editor.timeline.totalFrames = start || fixed[0].frameCount;
}

export function addImageBatchGroup(editor) {
    const taskKey = resolveTaskKey(editor.getTaskKey?.() || editor.taskTypeWidget?.value);
    editor.timeline.segments.push(newBatchSegment({
        durationSec: defaultDurationSec(taskKey),
        negativePrompt: "",
    }));
    normalizeImageBatchSegments(editor);
    editor.selectedIndex = Math.max(0, editor.timeline.segments.length - 1);
    editor.renderImageBatchGroups();
    editor.commit();
    editor.updateVideoNameLabel?.();
    editor.updateDomWidgetHeight?.();
}

export function deleteImageBatchGroup(editor, index) {
    if (editor.timeline.segments.length <= 1) return;
    editor.timeline.segments.splice(index, 1);
    normalizeImageBatchSegments(editor);
    editor.selectedIndex = clamp(
        editor.selectedIndex > index ? editor.selectedIndex - 1 : editor.selectedIndex,
        0,
        editor.timeline.segments.length - 1,
    );
    editor.renderImageBatchGroups();
    editor.commit();
    editor.updateVideoNameLabel?.();
    editor.updateDomWidgetHeight?.();
}

function pickFile(accept, onFile) {
    // Keep input in DOM until change/cancel — otherwise some Chromium builds
    // drop the dialog result when the element is GC'd.
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none";
    const cleanup = () => {
        input.remove();
    };
    input.onchange = () => {
        const file = input.files?.[0];
        cleanup();
        if (file) onFile(file);
    };
    input.addEventListener("cancel", cleanup);
    document.body.appendChild(input);
    input.click();
}

async function uploadSegSource(editor, index) {
    const segId = editor.timeline.segments[index]?.id;
    pickFile("image/*,.jpg,.jpeg,.png,.webp,.bmp,.gif", async (file) => {
        try {
            if (!file?.type?.startsWith("image/") && !/\.(jpe?g|png|webp|bmp|gif)$/i.test(file.name || "")) {
                throw new Error("Not an image file");
            }
            const uploaded = await uploadImage(file);
            const imageFile = relPath(uploaded);
            if (!imageFile) throw new Error("Upload returned empty filename");
            // Resolve by id — normalize may replace segment object references.
            const seg = (editor.timeline.segments || []).find((s) => s.id === segId)
                || editor.timeline.segments[index];
            if (!seg) return;
            // Write genImage immediately so UI updates even if dimension probe fails/hangs.
            seg.genImage = { imageFile, width: 0, height: 0 };
            seg.imageFile = imageFile;
            editor.renderImageBatchGroups();
            editor.updateOutputPreview?.();
            editor.commit(false, { syncTimeline: true });
            try {
                const dims = await readImageDimensions(file);
                const live = (editor.timeline.segments || []).find((s) => s.id === seg.id) || seg;
                if (live.genImage?.imageFile === imageFile) {
                    live.genImage = { imageFile, width: dims.width, height: dims.height };
                    editor.updateOutputPreview?.();
                    editor.scheduleTimelineSync?.();
                }
            } catch (dimErr) {
                console.warn("[MiniMax H3Director] batch source dims skipped:", dimErr);
            }
        } catch (err) {
            console.error("[MiniMax H3Director] batch source upload failed:", err);
            alert(t("upload.alertFailed", { err: err?.message || err }));
        }
    });
}

function readImageDimensions(file) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        const done = (fn, arg) => {
            clearTimeout(timer);
            URL.revokeObjectURL(url);
            fn(arg);
        };
        const timer = setTimeout(() => done(reject, new Error("Image dimension timeout")), 8000);
        img.onload = () => done(resolve, { width: img.naturalWidth, height: img.naturalHeight });
        img.onerror = () => done(reject, new Error("Failed to read image dimensions"));
        img.src = url;
    });
}

async function assignSegRefFromFile(editor, index, slot, file) {
    if (!file?.type?.startsWith("image/")) return;
    try {
        const uploaded = await uploadImage(file);
        const seg = editor.timeline.segments[index];
        if (!seg) return;
        seg.refs = (seg.refs || []).filter((r) => Number(r.index ?? r.slot) !== slot);
        seg.refs.push({ index: slot, imageFile: relPath(uploaded), imageB64: "" });
        editor.renderImageBatchGroups();
        editor.commit();
    } catch (err) {
        console.error("[MiniMax H3Director] batch ref upload failed:", err);
    }
}

async function uploadSegRef(editor, index, slot) {
    pickFile("image/*", (file) => assignSegRefFromFile(editor, index, slot, file));
}

function moveBatchRefSlot(editor, segIndex, fromSlot, toSlot) {
    if (fromSlot === toSlot) return;
    const seg = editor.timeline.segments[segIndex];
    if (!seg) return;
    const refs = [...(seg.refs || [])];
    const fromRef = refs.find((r) => Number(r.index ?? r.slot) === fromSlot);
    if (!fromRef) return;
    const toRef = refs.find((r) => Number(r.index ?? r.slot) === toSlot);
    seg.refs = refs.filter((r) => {
        const idx = Number(r.index ?? r.slot);
        return idx !== fromSlot && idx !== toSlot;
    });
    seg.refs.push({ ...fromRef, index: toSlot, slot: undefined });
    if (toRef) {
        seg.refs.push({ ...toRef, index: fromSlot, slot: undefined });
    }
    editor.renderImageBatchGroups();
    editor.commit();
}

function bindBatchRefDrop(slot, editor, index, slotIndex) {
    const hasImg = slot.classList.contains("has-img");
    slot.draggable = hasImg;
    slot.addEventListener("dragstart", (e) => {
        if (!hasImg) {
            e.preventDefault();
            return;
        }
        editor._batchRefDragMoved = false;
        const payload = JSON.stringify({ segIndex: index, from: slotIndex });
        e.dataTransfer.setData("application/x-minimax-ref-slot", payload);
        e.dataTransfer.setData("text/plain", payload);
        e.dataTransfer.effectAllowed = "move";
    });
    slot.addEventListener("dragend", () => {
        setTimeout(() => { editor._batchRefDragMoved = false; }, 0);
    });
    slot.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const types = [...(e.dataTransfer?.types || [])];
        e.dataTransfer.dropEffect = types.includes("application/x-minimax-ref-slot")
            ? "move"
            : "copy";
    });
    slot.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const raw = e.dataTransfer.getData("application/x-minimax-ref-slot")
            || e.dataTransfer.getData("text/plain");
        if (raw) {
            try {
                const data = JSON.parse(raw);
                if (Number(data.segIndex) !== index) return;
                editor._batchRefDragMoved = true;
                moveBatchRefSlot(editor, index, Number(data.from), slotIndex);
                return;
            } catch (_) { /* fall through */ }
        }
        const f = e.dataTransfer.files?.[0];
        if (f) assignSegRefFromFile(editor, index, slotIndex, f);
    });
}

function removeSegRef(editor, index, slot) {
    const seg = editor.timeline.segments[index];
    if (!seg) return;
    seg.refs = (seg.refs || []).filter((r) => Number(r.index ?? r.slot) !== slot);
    editor.renderImageBatchGroups();
    editor.commit();
}

async function uploadSegAudio(editor, index, slot) {
    pickFile("audio/*,.wav,.mp3,.flac,.ogg,.m4a,.aac", async (file) => {
        try {
            const uploaded = await uploadMedia(file);
            const seg = editor.timeline.segments[index];
            if (!seg) return;
            seg.refAudios = (seg.refAudios || []).filter((r) => Number(r.index ?? r.slot) !== slot);
            seg.refAudios.push({
                index: slot,
                audioFile: relPath(uploaded),
                fileName: uploaded?.name || file.name,
                type: "input",
                subfolder: uploaded?.subfolder || "",
            });
            editor.renderImageBatchGroups();
            editor.commit();
        } catch (err) {
            console.error("[MiniMax H3Director] batch audio upload failed:", err);
            alert(t("upload.refAudioFailed", { err: err?.message || err }));
        }
    });
}

function removeSegAudio(editor, index, slot) {
    const seg = editor.timeline.segments[index];
    if (!seg) return;
    seg.refAudios = (seg.refAudios || []).filter((r) => Number(r.index ?? r.slot) !== slot);
    editor.renderImageBatchGroups();
    editor.commit();
}

async function uploadSegVideo(editor, index, slot) {
    pickFile("video/*,.mp4,.mov,.webm,.mkv", async (file) => {
        try {
            const uploaded = await uploadMedia(file);
            const seg = editor.timeline.segments[index];
            if (!seg) return;
            const videoFile = relPath(uploaded);
            seg.refVideos = (seg.refVideos || []).filter((r) => Number(r.index ?? r.slot) !== slot);
            seg.refVideos.push({
                index: slot,
                videoFile,
                fileName: uploaded?.name || file.name,
                type: "input",
                subfolder: uploaded?.subfolder || "",
            });
            editor.renderImageBatchGroups();
            editor.commit();
        } catch (err) {
            console.error("[MiniMax H3Director] batch video upload failed:", err);
            alert(t("upload.refVideoBatchFailed", { err: err?.message || err }));
        }
    });
}

function removeSegVideo(editor, index, slot) {
    const seg = editor.timeline.segments[index];
    if (!seg) return;
    seg.refVideos = (seg.refVideos || []).filter((r) => Number(r.index ?? r.slot) !== slot);
    editor.renderImageBatchGroups();
    editor.commit();
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

function countFilledRefs(seg) {
    let imgs = 0;
    let videos = 0;
    let audios = 0;
    for (const r of seg.refs || []) {
        const idx = Number(r.index ?? r.slot);
        if (r?.imageFile && Number.isFinite(idx) && idx >= 0 && idx < R2V_PICTURE_SLOTS) imgs += 1;
    }
    for (const r of seg.refVideos || []) if (r?.videoFile || r?.fileName) videos += 1;
    for (const r of seg.refAudios || []) if (r?.audioFile || r?.fileName) audios += 1;
    return { imgs, videos, audios };
}

function createR2vSection(title, countText) {
    const section = document.createElement("div");
    section.className = "bd-r2v-section";
    const head = document.createElement("div");
    head.className = "bd-r2v-section-head";
    head.innerHTML = `
        <span class="bd-r2v-section-title">${title}</span>
        <span class="bd-r2v-section-count">${countText}</span>`;
    section.appendChild(head);
    return section;
}

function renderAudioSlot(el, ref, slot, index, editor, { r2v = false } = {}) {
    const label = refAudioLabel(slot);
    const file = ref?.audioFile || ref?.fileName || "";
    el.className = `bd-batch-audio${file ? " has-audio" : ""}`;
    el.title = file
        ? t("ref.audioTitleFilled", { label, file })
        : t("ref.clickUpload", { label });
    el.innerHTML = "";
    if (r2v) {
        const thumb = document.createElement("div");
        thumb.className = "bd-r2v-thumb";
        const meta = document.createElement("div");
        meta.className = "bd-r2v-meta";
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = label;
        meta.appendChild(tag);
        el.appendChild(thumb);
        el.appendChild(meta);
        if (file) {
            const playBtn = document.createElement("button");
            playBtn.type = "button";
            playBtn.className = "bd-r2v-play";
            playBtn.title = t("batch.r2v.play");
            playBtn.textContent = "▶";
            thumb.appendChild(playBtn);
            const dur = document.createElement("span");
            dur.className = "bd-r2v-dur";
            dur.textContent = ref?.durationSec != null
                ? formatMediaDuration(ref.durationSec)
                : "--:--";
            meta.appendChild(dur);
            const progress = document.createElement("div");
            progress.className = "bd-r2v-progress";
            progress.title = t("batch.r2v.seek");
            progress.innerHTML = `<div class="bd-r2v-progress-fill"></div>`;
            el.appendChild(progress);
            const audio = document.createElement("audio");
            audio.preload = "metadata";
            audio.src = viewUrl(file);
            audio.className = "bd-r2v-media";
            el.appendChild(audio);
            bindR2vMediaPlayback(audio, playBtn, progress);
            wireMediaDuration(audio, dur, (sec) => {
                if (ref) ref.durationSec = sec;
            });
            const x = document.createElement("span");
            x.className = "x";
            x.textContent = "×";
            x.onclick = (e) => { e.stopPropagation(); removeSegAudio(editor, index, slot); };
            el.appendChild(x);
        } else {
            thumb.textContent = "♪";
            const hint = document.createElement("span");
            hint.className = "name";
            hint.textContent = t("batch.r2v.uploadHint");
            meta.appendChild(hint);
        }
        return;
    }
    if (file) {
        const tag = document.createElement("span");
        tag.textContent = label;
        el.appendChild(tag);
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = fileBaseName(file);
        el.appendChild(name);
        const x = document.createElement("span");
        x.className = "x";
        x.textContent = "×";
        x.onclick = (e) => { e.stopPropagation(); removeSegAudio(editor, index, slot); };
        el.appendChild(x);
    } else {
        el.textContent = t("ref.audioUpload", { label });
    }
}

function renderVideoSlot(el, ref, slot, index, editor, { r2v = false } = {}) {
    const label = refVideoLabel(slot);
    const file = ref?.videoFile || ref?.fileName || "";
    el.className = `bd-batch-video${file ? " has-video" : ""}`;
    el.title = file
        ? t("ref.videoTitleFilled", { label, file })
        : t("ref.videoTitleEmpty", { label });
    el.innerHTML = "";
    if (r2v) {
        const thumb = document.createElement("div");
        thumb.className = "bd-r2v-thumb bd-r2v-thumb-video";
        const meta = document.createElement("div");
        meta.className = "bd-r2v-meta";
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = label;
        meta.appendChild(tag);
        el.appendChild(thumb);
        el.appendChild(meta);
        if (file) {
            const video = document.createElement("video");
            video.preload = "metadata";
            video.muted = true;
            video.playsInline = true;
            video.src = viewUrl(file);
            video.className = "bd-r2v-media";
            thumb.appendChild(video);
            const playBtn = document.createElement("button");
            playBtn.type = "button";
            playBtn.className = "bd-r2v-play";
            playBtn.title = t("batch.r2v.play");
            playBtn.textContent = "▶";
            thumb.appendChild(playBtn);
            const dur = document.createElement("span");
            dur.className = "bd-r2v-dur";
            dur.textContent = ref?.durationSec != null
                ? formatMediaDuration(ref.durationSec)
                : "--:--";
            meta.appendChild(dur);
            bindR2vMediaPlayback(video, playBtn);
            playBtn.addEventListener("click", () => {
                video.muted = false;
            });
            wireMediaDuration(video, dur, (sec) => {
                if (ref) ref.durationSec = sec;
            });
            video.addEventListener("loadeddata", () => {
                if (video.readyState >= 2 && video.currentTime < 0.05) {
                    try { video.currentTime = Math.min(0.1, (video.duration || 1) * 0.05); } catch (_) { /* ignore */ }
                }
            }, { once: true });
            const x = document.createElement("span");
            x.className = "x";
            x.textContent = "×";
            x.onclick = (e) => { e.stopPropagation(); removeSegVideo(editor, index, slot); };
            el.appendChild(x);
        } else {
            thumb.textContent = "▶";
            const hint = document.createElement("span");
            hint.className = "name";
            hint.textContent = t("batch.r2v.uploadHint");
            meta.appendChild(hint);
        }
        return;
    }
    if (file) {
        const tag = document.createElement("span");
        tag.textContent = label;
        el.appendChild(tag);
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = fileBaseName(file);
        el.appendChild(name);
        const x = document.createElement("span");
        x.className = "x";
        x.textContent = "×";
        x.onclick = (e) => { e.stopPropagation(); removeSegVideo(editor, index, slot); };
        el.appendChild(x);
    } else {
        el.textContent = t("ref.videoUpload", { label });
    }
}

/**
 * r2v layout: left = pictures/videos/audio · right = prompt + preview (returned).
 * @returns {HTMLElement} main column for prompt/preview
 */
function appendR2vMediaSections(card, seg, index, editor) {
    // 阶段 D：衔接模式=fl2va 时，本卡片的「参考图」区语义切换为 FL2VA 首尾帧硬锁输入链：
    //   slot 0 = 首帧（可选，留空自动取上段末帧）
    //   slot 1 = 结束帧（可选，留空则仅首帧硬锁）
    // 其余参考图/视频/音频槽位对 fl2v 路径无意义，隐藏避免混淆。
    const cmMode = normalizeContinuityMode(seg.continuityMode);
    const isFl2va = cmMode === "fl2va";
    const counts = countFilledRefs(seg);
    const body = document.createElement("div");
    body.className = "bd-batch-r2v-body";

    const assets = document.createElement("div");
    assets.className = "bd-batch-r2v-assets";

    const imgSection = createR2vSection(
        isFl2va ? t("batch.r2v.sectionFl2vaFrames") : t("batch.r2v.sectionPictures"),
        isFl2va ? "2/2" : `${counts.imgs}/${R2V_PICTURE_SLOTS}`,
    );
    const refs = document.createElement("div");
    refs.className = "bd-batch-refs";
    const slotCount = isFl2va ? 2 : R2V_PICTURE_SLOTS;
    if (!editor._r2vPicsVisible) editor._r2vPicsVisible = {};
    const segKey = String(seg.id ?? index);
    let highestFilled = -1;
    for (const r of seg.refs || []) {
        const idx = Number(r.index ?? r.slot);
        if (r?.imageFile && Number.isFinite(idx)) highestFilled = Math.max(highestFilled, idx);
    }
    const minVisible = highestFilled >= 0
        ? Math.min(R2V_PICTURE_SLOTS, Math.ceil((highestFilled + 1) / R2V_PICTURE_STEP) * R2V_PICTURE_STEP)
        : R2V_PICTURE_STEP;
    let visible = Number(editor._r2vPicsVisible[segKey]) || R2V_PICTURE_STEP;
    visible = Math.max(R2V_PICTURE_STEP, Math.min(R2V_PICTURE_SLOTS, visible));
    if (visible < minVisible) visible = minVisible;
    editor._r2vPicsVisible[segKey] = visible;

    const applyPicVisibility = () => {
        refs.querySelectorAll(".bd-batch-ref").forEach((el, i) => {
            el.classList.toggle("bd-r2v-pic-hidden", i >= visible);
        });
    };

    for (let i = 0; i < slotCount; i++) {
        const ref = (seg.refs || []).find((r) => Number(r.index ?? r.slot) === i);
        const slot = document.createElement("div");
        slot.className = "bd-batch-ref";
        if (!isFl2va && i >= visible) slot.classList.add("bd-r2v-pic-hidden");
        const labelOverride = isFl2va
            ? (i === 0 ? t("batch.r2v.firstFrame") : t("batch.r2v.endFrame"))
            : undefined;
        renderR2vRefSlot(slot, ref, i, index, editor, labelOverride);
        slot.onclick = () => {
            if (editor._batchRefDragMoved) {
                editor._batchRefDragMoved = false;
                return;
            }
            uploadSegRef(editor, index, i);
        };
        bindBatchRefDrop(slot, editor, index, i);
        refs.appendChild(slot);
    }
    imgSection.appendChild(refs);

    if (!isFl2va) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "bd-r2v-pics-toggle";
        const syncToggleLabel = () => {
            if (visible < R2V_PICTURE_SLOTS) {
                const next = Math.min(R2V_PICTURE_STEP, R2V_PICTURE_SLOTS - visible);
                toggle.textContent = t("batch.r2v.expandPics", { n: next });
            } else {
                toggle.textContent = t("batch.r2v.collapsePics");
            }
        };
        syncToggleLabel();
        toggle.onclick = (e) => {
            e.stopPropagation();
            if (visible < R2V_PICTURE_SLOTS) {
                visible = Math.min(R2V_PICTURE_SLOTS, visible + R2V_PICTURE_STEP);
            } else {
                visible = Math.max(R2V_PICTURE_STEP, minVisible);
            }
            editor._r2vPicsVisible[segKey] = visible;
            applyPicVisibility();
            syncToggleLabel();
            editor.updateDomWidgetHeight?.();
        };
        imgSection.appendChild(toggle);
    }
    assets.appendChild(imgSection);

    // FL2VA 模式：参考视频/音频槽位对 fl2v 硬锁路径无意义，隐藏避免混淆。
    if (!isFl2va) {
        const videoSection = createR2vSection(
            t("batch.r2v.sectionVideos"),
            `${counts.videos}/${MAX_REFERENCE_VIDEOS}`,
        );
        const videos = document.createElement("div");
        videos.className = "bd-batch-videos";
        for (let i = 0; i < MAX_REFERENCE_VIDEOS; i++) {
            const ref = (seg.refVideos || []).find((r) => Number(r.index ?? r.slot) === i);
            const slot = document.createElement("div");
            renderVideoSlot(slot, ref, i, index, editor, { r2v: true });
            slot.onclick = (e) => {
                if (e.target.closest?.(".bd-r2v-play, .bd-r2v-dur, .bd-r2v-progress, .x, video, audio")) return;
                if (ref && e.target.closest?.(".bd-r2v-thumb")) {
                    slot.querySelector(".bd-r2v-play")?.click();
                    return;
                }
                uploadSegVideo(editor, index, i);
            };
            videos.appendChild(slot);
        }
        videoSection.appendChild(videos);
        assets.appendChild(videoSection);

        const audioSection = createR2vSection(
            t("batch.r2v.sectionAudios"),
            `${counts.audios}/${MAX_REFERENCE_AUDIOS}`,
        );
        const audios = document.createElement("div");
        audios.className = "bd-batch-audios";
        for (let i = 0; i < MAX_REFERENCE_AUDIOS; i++) {
            const ref = (seg.refAudios || []).find((r) => Number(r.index ?? r.slot) === i);
            const slot = document.createElement("div");
            renderAudioSlot(slot, ref, i, index, editor, { r2v: true });
            slot.onclick = (e) => {
                if (e.target.closest?.(".bd-r2v-play, .bd-r2v-dur, .bd-r2v-progress, .x, video, audio")) return;
                if (ref && e.target.closest?.(".bd-r2v-thumb")) {
                    slot.querySelector(".bd-r2v-play")?.click();
                    return;
                }
                uploadSegAudio(editor, index, i);
            };
            audios.appendChild(slot);
        }
        audioSection.appendChild(audios);
        assets.appendChild(audioSection);
    }

    const main = document.createElement("div");
    main.className = "bd-batch-r2v-main";

    body.appendChild(assets);
    body.appendChild(main);
    card.appendChild(body);
    return main;
}

function renderR2vRefSlot(el, ref, slot, index, editor, labelOverride) {
    const label = labelOverride || refImageLabel(slot);
    const capClass = `cap${labelOverride ? " bd-fl2va-cap" : ""}`;
    const has = !!ref?.imageFile;
    el.classList.toggle("has-img", has);
    el.innerHTML = "";
    el.title = t("ref.clickUploadMove", { label });
    if (has) {
        const img = document.createElement("img");
        img.src = viewUrl(ref.imageFile);
        img.draggable = false;
        el.appendChild(img);
        const dot = document.createElement("span");
        dot.className = "dot";
        el.appendChild(dot);
        const cap = document.createElement("span");
        cap.className = capClass;
        cap.textContent = label;
        el.appendChild(cap);
        const x = document.createElement("span");
        x.className = "x";
        x.textContent = "×";
        x.onclick = (e) => { e.stopPropagation(); removeSegRef(editor, index, slot); };
        el.appendChild(x);
    } else {
        const cap = document.createElement("span");
        cap.className = capClass;
        cap.textContent = label;
        el.appendChild(cap);
    }
}

function renderSourceSlot(el, imageFile) {
    el.classList.toggle("has-img", !!imageFile);
    if (imageFile) {
        el.innerHTML = `<img src="${viewUrl(imageFile)}" alt="">`;
    } else {
        el.textContent = t("batch.uploadSource");
    }
}

function renderRefSlot(el, ref, slot, index, editor) {
    const label = refImageLabel(slot);
    el.classList.toggle("has-img", !!ref?.imageFile);
    el.innerHTML = "";
    el.title = t("ref.clickUploadMove", { label });
    if (ref?.imageFile) {
        const img = document.createElement("img");
        img.src = viewUrl(ref.imageFile);
        img.draggable = false;
        el.appendChild(img);
        const x = document.createElement("span");
        x.className = "x";
        x.textContent = "×";
        x.onclick = (e) => { e.stopPropagation(); removeSegRef(editor, index, slot); };
        el.appendChild(x);
    } else {
        el.textContent = label;
    }
}

function frameSrc(b64) {
    if (!b64) return "";
    return b64.startsWith("data:") ? b64 : `data:image/jpeg;base64,${b64}`;
}

function loadFrameImages(frames) {
    return Promise.all(frames.map((b64) => new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = frameSrc(b64);
    })));
}

function drawFrame(canvas, img) {
    const ctx = canvas.getContext("2d");
    if (!ctx || !img) return;
    const cw = canvas.clientWidth || 160;
    const ch = canvas.clientHeight || 90;
    if (canvas.width !== cw) canvas.width = cw;
    if (canvas.height !== ch) canvas.height = ch;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, cw, ch);
    const scale = Math.min(cw / img.naturalWidth, ch / img.naturalHeight);
    const dw = img.naturalWidth * scale;
    const dh = img.naturalHeight * scale;
    ctx.drawImage(img, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
}

function mountVideoPreview(el, seg, running, fps) {
    stopPlayer(el);
    el.innerHTML = "";
    if (running) {
        el.textContent = t("batch.generating");
        return;
    }
    const frames = (seg.previewFrames?.length ? seg.previewFrames : null)
        || (seg.previewB64 ? [seg.previewB64] : null);
    if (!frames?.length) {
        el.textContent = t("batch.previewVideoAfterRun");
        return;
    }
    const wrap = document.createElement("div");
    wrap.className = "bd-batch-vpreview";
    const canvas = document.createElement("canvas");
    canvas.height = 90;
    const ctrl = document.createElement("div");
    ctrl.className = "bd-batch-vpreview-ctrl";
    const playBtn = document.createElement("button");
    playBtn.type = "button";
    playBtn.className = "bd-btn";
    playBtn.textContent = t("batch.play");
    const meta = document.createElement("div");
    meta.className = "bd-batch-vpreview-meta";
    meta.textContent = t("batch.previewMeta", { n: frames.length, fps: formatPreviewFps(fps) });
    ctrl.appendChild(playBtn);
    wrap.appendChild(canvas);
    wrap.appendChild(ctrl);
    wrap.appendChild(meta);
    el.appendChild(wrap);

    const state = { playing: false, timer: null, idx: 0, images: null };
    _players.set(wrap, state);

    loadFrameImages(frames).then((images) => {
        state.images = images;
        drawFrame(canvas, images[0]);
    }).catch(() => {
        meta.textContent = t("batch.previewLoadFailed");
    });

    playBtn.onclick = (e) => {
        e.stopPropagation();
        if (!state.images?.length) return;
        if (state.playing) {
            state.playing = false;
            if (state.timer) clearInterval(state.timer);
            state.timer = null;
            playBtn.textContent = t("batch.play");
            return;
        }
        state.playing = true;
        playBtn.textContent = t("batch.pause");
        const interval = Math.max(20, 1000 / Math.max(1, fps));
        state.timer = setInterval(() => {
            if (!state.images?.length) return;
            state.idx = (state.idx + 1) % state.images.length;
            drawFrame(canvas, state.images[state.idx]);
        }, interval);
    };
}

function renderImagePreview(el, seg, running) {
    stopPlayer(el);
    el.innerHTML = "";
    if (running) {
        el.textContent = t("batch.generating");
        return;
    }
    if (seg.previewB64) {
        const img = document.createElement("img");
        img.src = frameSrc(seg.previewB64);
        img.alt = "preview";
        el.appendChild(img);
        return;
    }
    el.textContent = t("batch.previewAfterRun");
}

function renderPreview(el, seg, running, isVideo, fps) {
    if (isVideo) mountVideoPreview(el, seg, running, fps);
    else renderImagePreview(el, seg, running);
}

/* ---------------------------------------------------------------------------
 * UI 2.1 P5：Shot 卡片视觉中心——衔接徽章行 + Prompt 大输入区。
 *
 * 这两个辅助把「本镜怎么接上一镜」和「本镜拍什么」从卡片深处提到卡片顶部，
 * 成为镜头的视觉主体；参考素材/预览与低频设置排在它们之后。
 * ------------------------------------------------------------------------- */

/**
 * 衔接徽章行：🔗 衔接 + 四态下拉（自动续接 / 独立镜头 / Ref2VA 状态驱动 / FL2VA 首尾帧硬锁）。
 * 架构固底④：直接写 seg.continuityMode——本镜「怎么接上一镜」独立于生成模式（taskType），
 * 与「智能尾帧」也是两个独立可单独开关的功能。替代原来收在「镜头设置」折叠里的衔接 select。
 */
function buildContinuityStrip(editor, seg) {
    const strip = document.createElement("div");
    strip.className = "bd-batch-cont";
    const label = document.createElement("span");
    label.className = "bd-batch-cont-label";
    label.textContent = t("cont.badge");
    label.title = t("cont.badgeTitle");
    const sel = document.createElement("select");
    sel.className = "bd-batch-cont-sel";
    sel.title = t("cont.badgeTitle");
    const opts = [
        ["auto", t("cont.mode.auto")],
        ["none", t("cont.mode.none")],
        ["ref2va", t("cont.mode.ref2va")],
        ["fl2va", t("cont.mode.fl2va")],
    ];
    const curCm = normalizeContinuityMode(seg.continuityMode);
    for (const [v, labelText] of opts) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = labelText;
        if (v === curCm) o.selected = true;
        sel.appendChild(o);
    }
    sel.onchange = () => {
        if (sel.value === "auto") delete seg.continuityMode;
        else seg.continuityMode = sel.value;
        editor.scheduleTimelineSync?.();
        editor.commit?.(false, { syncTimeline: true });
        // 切换 FL2VA 时参考图区语义变为首尾帧槽位，需重渲染卡片。
        editor.renderImageBatchGroups?.();
    };
    strip.appendChild(label);
    strip.appendChild(sel);
    return strip;
}

/**
 * Prompt 大输入区。r2v 下加 `bd-batch-prompts-center`（卡片顶部全宽视觉主体）。
 * 事件绑定与旧内联块完全一致：oninput 写 seg.prompt、素材命名高亮 + 引用提示。
 */
function createShotPromptBlock(editor, seg, isR2v) {
    const prompts = document.createElement("div");
    prompts.className = "bd-batch-prompts" + (isR2v ? " bd-batch-prompts-center" : "");
    const ph = t(isR2v ? "placeholder.batchR2v" : "placeholder.batchDefault");
    prompts.innerHTML = `
        <span class="bd-label">${t("batch.prompt")}</span>
        <textarea data-f="prompt" placeholder=""></textarea>`;
    const ta = prompts.querySelector("textarea");
    ta.placeholder = ph;
    ta.value = seg.prompt || "";
    const promptEl = prompts.querySelector('[data-f="prompt"]');
    promptEl.oninput = (e) => {
        seg.prompt = e.target.value;
        seg.negativePrompt = "";
        editor.scheduleTimelineSync();
    };
    if (isR2v) {
        wirePromptImageMentions(editor, promptEl, () => ({
            refs: seg.refs || [],
            audios: seg.refAudios || [],
            videos: seg.refVideos || [],
        }));
        attachPromptHighlight(editor, promptEl, () => ({
            refs: seg.refs || [],
            audios: seg.refAudios || [],
            videos: seg.refVideos || [],
        }), () => getAssetsBlock(editor));
    }
    return prompts;
}

/**
 * 下载单镜 mp4（从磁盘缓存编码，后端 GET /minimax/director/segment_mp4）。
 * 供 r2v 卡片 / fl2v 卡片下载按钮共用。未生成时后端返回 404，短暂提示。
 */
export function downloadSegmentMp4(editor, index) {
    const nodeId = editor?.node?.id;
    if (nodeId == null || index == null) return;
    const url = `/minimax/director/segment_mp4?node_id=${encodeURIComponent(String(nodeId))}&index=${encodeURIComponent(String(index))}`;
    api.fetchApi(url)
        .then((resp) => {
            if (!resp.ok) {
                return resp.json()
                    .then((d) => { throw new Error((d && d.message) || `HTTP ${resp.status}`); })
                    .catch((err) => {
                        if (err instanceof Error) throw err;
                        throw new Error(`HTTP ${resp.status}`);
                    });
            }
            return resp.blob();
        })
        .then((blob) => {
            const objectUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = objectUrl;
            a.download = `Shot${String(Number(index) + 1).padStart(2, "0")}.mp4`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(objectUrl), 3000);
        })
        .catch((err) => {
            console.warn("MiniMax Director 单镜下载失败:", err);
            // 下载失败要可见（无 toast 体系，用原生 alert 兜底），
            // 常见原因：缓存被参数改动失效/节点 id 对不上缓存目录。
            try {
                window.alert((err && err.message) ? `下载失败：${err.message}` : "下载失败：未知错误");
            } catch (_) { /* 弹窗失败不致命 */ }
        });
}

export function renderImageBatchGroups(editor) {
    const list = editor.batchList;
    if (!list) return;
    flushBatchDurationInputs(editor);
    stopAllPlayers(list);
    const key = resolveTaskKey(editor.getTaskKey?.() || editor.taskTypeWidget?.value);
    const variant = imageBatchVariant(key);
    const isVideo = isVideoBatchTask(key);
    const runningIdx = editor._runHighlightSeg;
    const fps = parseFloat(editor.frameRateWidget?.value || editor.timeline?.frameRate || 24);

    if (editor.batchHint) {
        const hintKey = `batch.hint.${key}`;
        editor.batchHint.textContent = t(hintKey) !== hintKey
            ? t(hintKey)
            : t(isVideo ? "batch.hint.defaultVideo" : "batch.hint.defaultImage");
    }
    if (editor.batchI2vNotice) {
        const needsRefs = key === "r2i" || key === "r2v";
        const hasAnyMedia = (editor.timeline.segments || []).some((s) => (
            (s.refs || []).length > 0
            || (s.refAudios || []).length > 0
            || (s.refVideos || []).length > 0
        ));
        if (needsRefs && !hasAnyMedia) {
            editor.batchI2vNotice.textContent = t(key === "r2v" ? "batch.notice.r2vNoRefs" : "batch.notice.r2iNoRefs");
            editor.batchI2vNotice.classList.add("visible");
        } else {
            editor.batchI2vNotice.classList.remove("visible");
            editor.batchI2vNotice.textContent = "";
        }
    }
    const addBtn = editor.batchPanel?.querySelector('[data-a="batch-add"]');
    if (addBtn) {
        addBtn.textContent = t(key === "r2v" ? "batch.addRefGroup" : "batch.addPromptGroup");
        addBtn.setAttribute("data-i18n", key === "r2v" ? "batch.addRefGroup" : "batch.addPromptGroup");
        // r2v: add from toolbar (left of task select), like fl2v.
        addBtn.classList.toggle("hidden", key === "r2v");
    }
    // r2v 自动续接开关（ref2va 模型）：仅 r2v 显示，状态存 timeline.output.r2vAutoContinuity。
    const r2vAutoWrap = editor.batchPanel?.querySelector('[data-r="batch-r2v-auto"]');
    const r2vAutoCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-auto-cb"]');
    if (r2vAutoWrap) r2vAutoWrap.classList.toggle("hidden", key !== "r2v");
    if (r2vAutoCb) {
        const out = editor.timeline.output || {};
        // 默认开启：字段缺失（旧工作流/重启后未持久化）视为 true，只有显式 false 才关闭。
        const raw = out.r2vAutoContinuity ?? out.r2v_auto_continuity ?? true;
        const on = !(raw === false || (typeof raw === "string" && ["false", "0", "off", "no"].includes(raw.trim().toLowerCase())));
        r2vAutoCb.checked = on;
        r2vAutoWrap?.classList.toggle("active", on);
    }
    // 全局资产库开关（Phase B）：仅 r2v 显示，状态存 timeline.output.globalAssetsEnabled。
    const assetsWrap = editor.batchPanel?.querySelector('[data-r="batch-r2v-assets"]');
    const assetsCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-assets-cb"]');
    if (assetsWrap) assetsWrap.classList.toggle("hidden", key !== "r2v");
    if (assetsCb) {
        const out = editor.timeline.output || {};
        const raw = out.globalAssetsEnabled ?? out.global_assets_enabled ?? true;
        const on = !(raw === false || (typeof raw === "string" && ["false", "0", "off", "no"].includes(raw.trim().toLowerCase())));
        assetsCb.checked = on;
        assetsWrap?.classList.toggle("active", on);
    }
    const assetsPanel = editor.batchAssetsPanel;
    if (assetsPanel) {
        const showAssets = key === "r2v" && !!(assetsCb?.checked);
        assetsPanel.classList.toggle("hidden", !showAssets);
        if (showAssets) renderGlobalAssetsPanel(editor);
    }
    // 状态跟踪开关（阶段 C）：仅 r2v 显示，状态存 timeline.output.stateTrackingEnabled。
    const stateWrap = editor.batchPanel?.querySelector('[data-r="batch-r2v-state"]');
    const stateCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-state-cb"]');
    if (stateWrap) stateWrap.classList.toggle("hidden", key !== "r2v");
    if (stateCb) {
        const out = editor.timeline.output || {};
        const raw = out.stateTrackingEnabled ?? out.state_tracking_enabled ?? true;
        const on = !(raw === false || (typeof raw === "string" && ["false", "0", "off", "no"].includes(raw.trim().toLowerCase())));
        stateCb.checked = on;
        stateWrap?.classList.toggle("active", on);
    }
    // 智能尾帧选择总开关（待办④）：仅 r2v 显示，状态存 timeline.output.smartTailEnabled。
    const smartWrap = editor.batchPanel?.querySelector('[data-r="batch-r2v-smarttail"]');
    const smartCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-smarttail-cb"]');
    if (smartWrap) smartWrap.classList.toggle("hidden", key !== "r2v");
    if (smartCb) {
        const out = editor.timeline.output || {};
        const raw = out.smartTailEnabled ?? out.smart_tail_enabled ?? true;
        const on = !(raw === false || (typeof raw === "string" && ["false", "0", "off", "no"].includes(raw.trim().toLowerCase())));
        smartCb.checked = on;
        smartWrap?.classList.toggle("active", on);
    }

    list.innerHTML = "";
    editor.timeline.segments.forEach((seg, index) => {
        const isR2v = key === "r2v";
        // UI 2.0 第二优先级：镜头设置折叠模块的分组容器（作用域提升到回调级，供后续控制行挂载）。
        let cfgGroupCont = null;
        let cfgGroupGen = null;
        let cfgSummary = null;
        // UI 2.1 P5：资产选择行 + 镜头设置折叠沉底（先建后挂，等 body 之后再 append，保证
        // 视觉顺序：标题 → 衔接徽章 → Prompt → 参考素材/预览 → 资产选择 → 折叠设置）。
        let assetSelRow = null;
        let cfgWrap = null;
        // UI 2.1 P5：Prompt 主体（r2v 提前到卡片顶部；非 r2v 保持原 grid 位置）。
        let prompts = null;
        const card = document.createElement("div");
        const layoutClass = isR2v
            ? "bd-batch-r2v"
            : (variant === "source" ? "bd-batch-source"
                : (variant === "refs" ? "bd-batch-refs" : "bd-batch-plain"));
        card.className = `bd-batch-card ${layoutClass}`;
        const runSelectOn = !!(editor.isRunSelectEnabled?.() && editor.supportsRunSelect?.());
        const runEnabled = !runSelectOn || !!editor.isSegmentRunEnabled?.(index);
        // r2v: always show focus selected. t2v/i2v: only run-select participation chrome.
        if (isR2v && index === editor.selectedIndex) card.classList.add("selected");
        if (index === runningIdx) card.classList.add("running");
        if (runSelectOn && runEnabled) card.classList.add("run-on");
        if (runSelectOn && !runEnabled) card.classList.add("run-skipped");
        card.onclick = (e) => {
            if (e.target.closest?.("button, input, textarea, select, .bd-batch-ref, .bd-batch-audio, .bd-batch-video, .bd-batch-src, .bd-r2v-section, .bd-r2v-play, .x, video, audio")) {
                return;
            }
            if (editor.selectedIndex === index) return;
            editor.selectedIndex = index;
            editor._syncR2vCardSelection?.();
            editor.scheduleRender?.();
            editor.updateVideoNameLabel?.();
        };
        const hasPreview = isVideo
            ? (seg.previewFrames?.length > 0 || seg.previewB64)
            : !!seg.previewB64;
        if (hasPreview && index !== runningIdx) card.classList.add("done");

        const head = document.createElement("div");
        head.className = "bd-batch-head";
        // Timeline + cards stay in sync for run-select (incl. r2v).
        if (runSelectOn) {
            const runCb = document.createElement("input");
            runCb.type = "checkbox";
            runCb.className = "bd-batch-run-check";
            runCb.checked = runEnabled;
            runCb.title = t("tooltip.batchRunCheck");
            runCb.onclick = (e) => {
                e.stopPropagation();
                editor.toggleSegmentRun(index);
            };
            head.appendChild(runCb);
        }
        const title = document.createElement("b");
        // UI 2.1 P1：镜头卡片标题 = 🎬 Shot N（替代「素材组 N / 提示词组 N」）+ 状态灯。
        title.textContent = `🎬 ${t("shot.title", { n: String(index + 1).padStart(2, "0") })}`;
        const segSt = editor._segCacheStatus?.get?.(index);
        if (segSt) {
            const sdot = document.createElement("span");
            sdot.className = "bd-shot-dot";
            if (segSt === "success") sdot.classList.add("st-success");
            else if (segSt === "running") sdot.classList.add("st-running");
            else if (segSt === "review") sdot.classList.add("st-review");
            else if (segSt === "failed") sdot.classList.add("st-failed");
            sdot.title = t(`shotStatus.${segSt}`);
            title.appendChild(sdot);
        }
        head.appendChild(title);
        const meta = document.createElement("div");
        meta.className = "bd-batch-head-meta";
        if (isVideo) {
            const secRow = document.createElement("label");
            secRow.className = "bd-batch-fc";
            const curSec = resolveSegmentDurationSec(seg, defaultFrameCount(key));
            const { frames, durationSec: syncedSec } = durationToClampedMiniMaxFrames(curSec, 24);
            const playSec = framesToDurationSec(frames, 24);
            seg.durationSec = syncedSec;
            seg.frameCount = frames;
            seg.length = frames;
            seg._videoFrameCount = frames;
            secRow.innerHTML = `${t("batch.seconds")} <input type="number" data-batch-sec-index="${index}" min="${minDurationSec()}" max="${maxDurationSec()}" step="0.1" value="${seg.durationSec}" title="${t("batch.durationTooltip", { frames, play: playSec })}">`;
            const secInput = secRow.querySelector("input");
            const applySec = () => {
                const updated = applyBatchSegmentDuration(editor, index, secInput.value);
                if (!updated) return;
                const play = framesToDurationSec(updated.frameCount, 24);
                // 正在输入时不回写显示值：防抖触发的 applySec 会把半截输入（如 "3."）覆盖成取整值，
                // 导致秒数框"自己变"。值已在 applyBatchSegmentDuration 里提交，失焦后再同步显示。
                if (document.activeElement !== secInput) {
                    secInput.value = String(updated.durationSec);
                    secInput.title = t("batch.durationTooltip", {
                        frames: updated.frameCount,
                        play,
                    });
                }
                editor.scheduleTimelineSync();
                editor.scheduleRender?.();
                editor.updateVideoNameLabel?.();
                editor.updateOutputPreview?.();
                // Keep total_frames widget in sync with sum of group frames.
                if (editor.totalFramesWidget) {
                    editor.totalFramesWidget.value = sumFrameCounts(editor.timeline.segments);
                }
            };
            secInput.onchange = applySec;
            secInput.oninput = () => {
                clearTimeout(secInput._t);
                secInput._t = setTimeout(applySec, 200);
            };
            secInput.onblur = () => {
                clearTimeout(secInput._t);
                secInput._t = null;
                applySec();
            };
            meta.appendChild(secRow);
        }
        // UI 2.1 P1：镜头标签（所属场景 / 角色）——一眼看出素材层级归属。
        const tags = document.createElement("span");
        tags.className = "bd-batch-tags";
        // P4 三级资产层级：段资产候选池 = 当前场景素材组优先 → 全局兜底（角色标签来源标记）。
        const castPool = resolveSegmentAssetPool(editor, seg, "cast");
        const locPool = resolveSegmentAssetPool(editor, seg, "locations");
        const scenesBlock = editor.timeline?.scenes || [];
        const scTag = scenesBlock.find((s) => s.id === seg.sceneId);
        if (scTag?.name) {
            const tag = document.createElement("span");
            tag.className = "bd-batch-tag";
            tag.textContent = `🎬 ${scTag.name}`;
            tag.title = t("shot.tagScene");
            tags.appendChild(tag);
        }
        const castInScene = castPool.sceneAssets.find((c) => c.id === seg.castId);
        const castTag = castInScene || castPool.globalAssets.find((c) => c.id === seg.castId);
        if (castTag?.name) {
            const tag = document.createElement("span");
            tag.className = "bd-batch-tag" + (castInScene ? " from-scene" : " from-global");
            tag.textContent = `${castInScene ? "🎬" : "🌐"} ${castTag.name}`;
            tag.title = castInScene
                ? t("shot.tagCastScene", { scene: castPool.scene?.name || "" })
                : t("shot.tagCastGlobal");
            tags.appendChild(tag);
        }
        if (tags.children.length) meta.appendChild(tags);

        // UI 2.1 P1：生成本镜按钮（runSelection=[index] → 单镜头生成）。
        const genShot = document.createElement("button");
        genShot.type = "button";
        genShot.className = "bd-batch-shot-run";
        genShot.textContent = t("shot.generate");
        genShot.disabled = !editor.supportsRunSelect?.();
        genShot.onclick = (e) => {
            e.stopPropagation();
            editor._genScope = "select";
            editor.timeline.runSelectEnabled = true;
            editor.timeline.runSelection = [index];
            editor.updateGenScopeUI?.();
            editor.updateRunSelectUI?.();
            editor.commit?.(true, { syncTimeline: true });
            if (window.app?.queuePrompt) window.app.queuePrompt(1, "Director");
        };
        meta.appendChild(genShot);

        // UI 2.2：下载本镜 mp4（后端从磁盘缓存编码；未生成则禁用）。
        const dlShot = document.createElement("button");
        dlShot.type = "button";
        dlShot.className = "bd-batch-shot-dl";
        dlShot.textContent = t("shot.download");
        const cachedSt = editor._segCacheStatus?.get?.(index);
        const canDl = cachedSt === "success";
        dlShot.disabled = !canDl;
        dlShot.title = canDl
            ? t("tooltip.shotDownload")
            : t("tooltip.shotDownloadPending");
        dlShot.onclick = (e) => {
            e.stopPropagation();
            downloadSegmentMp4(editor, index);
        };
        meta.appendChild(dlShot);

        const del = document.createElement("button");
        del.type = "button";
        del.className = "bd-batch-del";
        del.textContent = t("batch.delete");
        del.disabled = editor.timeline.segments.length <= 1;
        del.onclick = (e) => { e.stopPropagation(); deleteImageBatchGroup(editor, index); };
        meta.appendChild(del);
        head.appendChild(meta);
        card.appendChild(head);

        // 全局资产库（Phase B）：每段选择注入哪个角色/场景资产。
        // 字段缺失（新建段/旧工作流）时回填默认；显式 "" 表示"无"。
        // P4：默认/自动匹配走「场景素材组优先 → 全局兜底」的合并池。
        if (isR2v) {
            // 提示词命名自动匹配：未手动选择过的段，每次渲染按提示词里的资产名自动匹配；
            // 手动下拉选择过（含选"无"）则尊重手动值，不覆盖。
            const castDefault = castPool.scene?.defaultCastId || getAssetsBlock(editor).defaultCastId || "";
            const locDefault = locPool.scene?.defaultLocationId || getAssetsBlock(editor).defaultLocationId || "";
            if (!seg.castManual) seg.castId = autoMatchSegmentAssets(seg, castPool.pool) || castDefault;
            if (!seg.locationManual) seg.locationId = autoMatchSegmentAssets(seg, locPool.pool) || locDefault;
            assetSelRow = document.createElement("div");
            assetSelRow.className = "bd-batch-r2v-asset-sel";
            assetSelRow.appendChild(makeSegmentAssetSelect(editor, "cast", seg));
            assetSelRow.appendChild(makeSegmentAssetSelect(editor, "location", seg));
            assetSelRow.appendChild(makeSegmentSceneSelect(editor, seg));
            // 架构固底③：本镜生成模式（R2V / FL2V），per-segment taskType。
            assetSelRow.appendChild(makeSegmentModeSelect(editor, seg));
            // P5 修正：资产选择行回到标题下方（常显靠上，先选素材再写词），不沉底。
            card.appendChild(assetSelRow);

            // UI 2.0 第二优先级：把续接/状态/生成控制收进「镜头设置」折叠模块。
            // 资产选择（角色/场景/所属场景）留在表面始终可见（高频）；低频控制折叠隐藏。
            const cfgOpen = !!editor._batchCfgOpen;
            cfgWrap = document.createElement("div");
            cfgWrap.className = "bd-batch-cfg";
            const cfgHead = document.createElement("button");
            cfgHead.type = "button";
            cfgHead.className = "bd-batch-cfg-head" + (cfgOpen ? " open" : "");
            cfgHead.title = t("batch.cfgToggleTitle");
            const caret = document.createElement("span");
            caret.className = "bd-batch-cfg-caret";
            caret.textContent = "▸";
            const cfgTitle = document.createElement("span");
            cfgTitle.textContent = t("batch.cfg");
            const cfgSummaryEl = document.createElement("span");
            cfgSummaryEl.className = "bd-batch-cfg-summary";
            cfgSummary = cfgSummaryEl;
            cfgHead.append(caret, cfgTitle, cfgSummaryEl);
            cfgHead.onclick = (e) => {
                e.stopPropagation();
                editor._batchCfgOpen = !editor._batchCfgOpen;
                cfgHead.classList.toggle("open", editor._batchCfgOpen);
            };
            const cfgBody = document.createElement("div");
            cfgBody.className = "bd-batch-cfg-body";
            cfgWrap.appendChild(cfgHead);
            cfgWrap.appendChild(cfgBody);
            // P5：折叠模块沉底，等 body 之后再挂到卡片。
            // 续接组：状态变更（衔接模式已上移到顶部徽章行）。
            cfgGroupCont = document.createElement("div");
            cfgGroupCont.className = "bd-batch-cfg-group";
            const cfgContTitle = document.createElement("div");
            cfgContTitle.className = "bd-batch-cfg-group-title";
            cfgContTitle.textContent = t("batch.cfgContinuity");
            cfgGroupCont.appendChild(cfgContTitle);
            cfgBody.appendChild(cfgGroupCont);
            // 生成组：仅剩关键镜头（架构固底④：智能尾帧已迁入「续接设置」组）。
            cfgGroupGen = document.createElement("div");
            cfgGroupGen.className = "bd-batch-cfg-group";
            const cfgGenTitle = document.createElement("div");
            cfgGenTitle.className = "bd-batch-cfg-group-title";
            cfgGenTitle.textContent = t("batch.cfgGenerate");
            cfgGroupGen.appendChild(cfgGenTitle);
            cfgBody.appendChild(cfgGroupGen);
            // 折叠头摘要：两个独立功能各自的状态——🔗 衔接方式 + 🎯 智能尾帧。
            if (cfgSummary) {
                const curCm2 = normalizeContinuityMode(seg.continuityMode);
                const cmLabelMap = { auto: t("cont.mode.auto"), none: t("cont.mode.none"), ref2va: t("cont.mode.ref2va"), fl2va: t("cont.mode.fl2va") };
                const stIsAuto = seg.smartTail === undefined || seg.smartTail === "" || seg.smartTail === null;
                const stVal = stIsAuto
                    ? t("batch.smartTailAuto")
                    : (String(seg.smartTail) === "true" || seg.smartTail === true ? t("batch.smartTailOn") : t("batch.smartTailOff"));
                cfgSummary.textContent = `${t("batch.cfgSummaryCont")} ${cmLabelMap[curCm2] || curCm2} · ${t("batch.cfgSummaryTail")} ${stVal}`;
            }
        } else {
            // 非 r2v 镜头也可归属场景（Scene Manager 导出按场景分组）。
            const sceneSelRow = document.createElement("div");
            sceneSelRow.className = "bd-batch-r2v-asset-sel";
            sceneSelRow.appendChild(makeSegmentSceneSelect(editor, seg));
            card.appendChild(sceneSelRow);
        }

        // 状态跟踪（阶段 C）：每镜可填「状态变更」——本镜结束时应落地的关键状态
        // （动作/地点/时间/情绪），系统拼进下一镜 prompt 前。不填则只靠参考图续接。
        if (isR2v) {
            const stateRow = document.createElement("label");
            stateRow.className = "bd-batch-r2v-state-input";
            const stateLabel = document.createElement("span");
            stateLabel.textContent = t("batch.stateChange");
            const stateInput = document.createElement("input");
            stateInput.type = "text";
            stateInput.placeholder = t("batch.stateChangePh");
            stateInput.value = seg.stateChange || "";
            stateInput.oninput = (e) => {
                seg.stateChange = e.target.value;
                editor.scheduleTimelineSync?.();
            };
            stateInput.onchange = () => {
                editor.commit?.(false, { syncTimeline: true });
            };
            stateRow.appendChild(stateLabel);
            stateRow.appendChild(stateInput);
            if (cfgGroupCont) cfgGroupCont.appendChild(stateRow);
            else card.appendChild(stateRow);
        }

        // 智能尾帧选择（待办④ / 架构固底④）：每段三态覆盖——跟随全局 / 强制开启 / 强制关闭。
        // 存 seg.smartTail：undefined=跟随全局（不进 payload）、true/false=强制覆盖。
        // 架构固底④：智能尾帧是独立于「镜头衔接」的第二个续接功能，放「续接设置」组单独开关。
        if (isR2v) {
            const stRow = document.createElement("label");
            stRow.className = "bd-batch-r2v-smarttail-sel";
            const stLabel = document.createElement("span");
            stLabel.textContent = t("batch.smartTail");
            const stSel = document.createElement("select");
            const stOpts = [
                ["auto", t("batch.smartTailAuto")],
                ["on", t("batch.smartTailOn")],
                ["off", t("batch.smartTailOff")],
            ];
            const cur = seg.smartTail === undefined || seg.smartTail === "" || seg.smartTail === null
                ? "auto"
                : (String(seg.smartTail) === "true" || seg.smartTail === true ? "on" : "off");
            for (const [v, label] of stOpts) {
                const o = document.createElement("option");
                o.value = v;
                o.textContent = label;
                if (v === cur) o.selected = true;
                stSel.appendChild(o);
            }
            stSel.onchange = () => {
                if (stSel.value === "auto") delete seg.smartTail;
                else seg.smartTail = stSel.value === "on";
                editor.scheduleTimelineSync?.();
                editor.commit?.(false, { syncTimeline: true });
            };
            stRow.appendChild(stLabel);
            stRow.appendChild(stSel);
            // 架构固底④：智能尾帧迁入「续接设置」组（cfgGroupCont），与状态变更并列，
            // 与「镜头衔接」徽章、生成模式（taskType）完全独立。
            if (cfgGroupCont) cfgGroupCont.appendChild(stRow);
            else card.appendChild(stRow);
        }

        // 里程碑 B：关键镜头标记（二级一致性检测）——三态覆盖：跟随自动 / 强制 / 跳过。
        // 自动=有角色/场景资产注入即检测（后端判定）；手动可加（强制）/减（跳过）。
        // 存 seg.consistencyCheck：undefined/"auto"=自动、true=强制、false=跳过。
        if (isR2v) {
            const ckRow = document.createElement("label");
            ckRow.className = "bd-batch-r2v-consistency";
            const ckLabel = document.createElement("span");
            ckLabel.textContent = t("batch.consistencyCheck");
            const ckSel = document.createElement("select");
            const ckOpts = [
                ["auto", t("batch.consistencyAuto")],
                ["on", t("batch.consistencyOn")],
                ["off", t("batch.consistencyOff")],
            ];
            const curCk = (seg.consistencyCheck === undefined || seg.consistencyCheck === "" || seg.consistencyCheck === null || seg.consistencyCheck === "auto")
                ? "auto"
                : (String(seg.consistencyCheck) === "true" || seg.consistencyCheck === true ? "on" : "off");
            for (const [v, label] of ckOpts) {
                const o = document.createElement("option");
                o.value = v;
                o.textContent = label;
                if (v === curCk) o.selected = true;
                ckSel.appendChild(o);
            }
            ckSel.onchange = () => {
                if (ckSel.value === "auto") delete seg.consistencyCheck;
                else seg.consistencyCheck = ckSel.value === "on";
                editor.scheduleTimelineSync?.();
                editor.commit?.(false, { syncTimeline: true });
            };
            ckRow.appendChild(ckLabel);
            ckRow.appendChild(ckSel);
            if (cfgGroupGen) cfgGroupGen.appendChild(ckRow);
            else card.appendChild(ckRow);
        }

        if (variant === "source") {
            const media = document.createElement("div");
            media.className = "bd-batch-media";
            const src = document.createElement("div");
            src.className = "bd-batch-src";
            renderSourceSlot(src, seg.genImage?.imageFile);
            src.onclick = () => uploadSegSource(editor, index);
            media.appendChild(src);
            card.appendChild(media);
        }
        let r2vMain = null;
        if (variant === "refs" && isR2v) {
            r2vMain = appendR2vMediaSections(card, seg, index, editor);
        } else if (variant === "refs") {
            const media = document.createElement("div");
            media.className = "bd-batch-media";
            const refs = document.createElement("div");
            refs.className = "bd-batch-refs";
            for (let i = 0; i < MAX_REFERENCE_IMAGES; i++) {
                const ref = (seg.refs || []).find((r) => Number(r.index ?? r.slot) === i);
                const slot = document.createElement("div");
                slot.className = "bd-batch-ref";
                renderRefSlot(slot, ref, i, index, editor);
                slot.onclick = () => {
                    if (editor._batchRefDragMoved) {
                        editor._batchRefDragMoved = false;
                        return;
                    }
                    uploadSegRef(editor, index, i);
                };
                bindBatchRefDrop(slot, editor, index, i);
                refs.appendChild(slot);
            }
            media.appendChild(refs);
            card.appendChild(media);
        }

        // UI 2.1 P5：非 r2v 镜头才在这里建 Prompt（r2v 的 Prompt 放 body 右列）。
        if (!isR2v) prompts = createShotPromptBlock(editor, seg, false);

        const preview = document.createElement("div");
        preview.className = "bd-batch-preview";
        renderPreview(preview, seg, index === runningIdx, isVideo, seg.previewFps || fps);

        if (isR2v && r2vMain) {
            // P5 修正：body 右列 = 衔接徽章 → Prompt（视觉主体）→ 预览；左列仍是参考素材。
            // 这样「先看/传参考图（左）→ 再写提示词（右）」的动线不被打断，Prompt 依旧视觉中心。
            r2vMain.appendChild(buildContinuityStrip(editor, seg));
            r2vMain.appendChild(createShotPromptBlock(editor, seg, true));
            r2vMain.appendChild(preview);
        } else {
            card.appendChild(prompts);
            card.appendChild(preview);
        }

        // UI 2.1 P5：镜头设置折叠沉底（body 之后；资产选择行已在标题下方常显）。
        if (isR2v) {
            if (cfgWrap) card.appendChild(cfgWrap);
        }

        list.appendChild(card);
    });
}

export function setImageBatchPreview(editor, segmentIndex, imageB64, extra = {}) {
    const seg = editor.timeline.segments[segmentIndex];
    if (!seg) return;
    seg.previewB64 = imageB64 || "";
    if (Array.isArray(extra.frames) && extra.frames.length) {
        seg.previewFrames = extra.frames;
        seg.previewFps = extra.fps || seg.previewFps || 24;
    } else if (imageB64) {
        seg.previewFrames = [imageB64];
    }
    editor.renderImageBatchGroups();
}

export function bindImageBatchEvents(editor) {
    editor.batchAddBtn?.addEventListener("click", (e) => {
        e.stopPropagation();
        addImageBatchGroup(editor);
    });
    // r2v 自动续接（ref2va 模型）：默认开启。用户取消勾选 = 显式关闭（写入 false）。
    // 独立于 continuityEnabled（前端对非 fl2v 模式会强制清零该字段），存 timeline.output.r2vAutoContinuity。
    const r2vAutoCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-auto-cb"]');
    r2vAutoCb?.addEventListener("change", () => {
        editor.timeline.output = editor.timeline.output || {};
        editor.timeline.output.r2vAutoContinuity = !!r2vAutoCb.checked;
        editor.batchPanel?.querySelector('[data-r="batch-r2v-auto"]')?.classList.toggle("active", r2vAutoCb.checked);
        editor.commit?.(false, { syncTimeline: true });
        editor.scheduleRender?.();
        editor.updateDomWidgetHeight?.();
    });
    // 全局资产库开关（Phase B）：默认开启。状态存 timeline.output.globalAssetsEnabled。
    const assetsCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-assets-cb"]');
    assetsCb?.addEventListener("change", () => {
        editor.timeline.output = editor.timeline.output || {};
        editor.timeline.output.globalAssetsEnabled = !!assetsCb.checked;
        editor.batchPanel?.querySelector('[data-r="batch-r2v-assets"]')?.classList.toggle("active", assetsCb.checked);
        editor.renderImageBatchGroups?.();
        editor.commit?.(false, { syncTimeline: true });
        editor.scheduleRender?.();
        editor.updateDomWidgetHeight?.();
    });
    editor.batchAssetsCastAdd?.addEventListener("click", (e) => {
        e.stopPropagation();
        addGlobalAsset(editor, "cast");
    });
    editor.batchAssetsLocAdd?.addEventListener("click", (e) => {
        e.stopPropagation();
        addGlobalAsset(editor, "location");
    });
    editor.batchAssetsPropAdd?.addEventListener("click", (e) => {
        e.stopPropagation();
        addGlobalAsset(editor, "props");
    });
    editor.batchAssetsStyleAdd?.addEventListener("click", (e) => {
        e.stopPropagation();
        addGlobalAsset(editor, "styles");
    });
    editor.batchAssetsCastDef?.addEventListener("change", () => {
        const a = getAssetsBlock(editor);
        a.defaultCastId = editor.batchAssetsCastDef.value || "";
        editor.renderImageBatchGroups?.();
        editor.commit?.(false, { syncTimeline: true });
        editor.updateDomWidgetHeight?.();
    });
    editor.batchAssetsLocDef?.addEventListener("change", () => {
        const a = getAssetsBlock(editor);
        a.defaultLocationId = editor.batchAssetsLocDef.value || "";
        editor.renderImageBatchGroups?.();
        editor.commit?.(false, { syncTimeline: true });
        editor.updateDomWidgetHeight?.();
    });
    // 状态跟踪开关（阶段 C）：默认开启。状态存 timeline.output.stateTrackingEnabled。
    const stateCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-state-cb"]');
    stateCb?.addEventListener("change", () => {
        editor.timeline.output = editor.timeline.output || {};
        editor.timeline.output.stateTrackingEnabled = !!stateCb.checked;
        editor.batchPanel?.querySelector('[data-r="batch-r2v-state"]')?.classList.toggle("active", stateCb.checked);
        editor.commit?.(false, { syncTimeline: true });
        editor.scheduleRender?.();
        editor.updateDomWidgetHeight?.();
    });
    // 智能尾帧选择总开关（待办④）：默认开启。状态存 timeline.output.smartTailEnabled。
    const smartCb = editor.batchPanel?.querySelector('[data-r="batch-r2v-smarttail-cb"]');
    smartCb?.addEventListener("change", () => {
        editor.timeline.output = editor.timeline.output || {};
        editor.timeline.output.smartTailEnabled = !!smartCb.checked;
        editor.batchPanel?.querySelector('[data-r="batch-r2v-smarttail"]')?.classList.toggle("active", smartCb.checked);
        editor.commit?.(false, { syncTimeline: true });
        editor.scheduleRender?.();
        editor.updateDomWidgetHeight?.();
    });
}

export function getImageBatchUiHeight(editor) {
    const n = Math.max(1, editor?.timeline?.segments?.length || 1);
    const key = resolveTaskKey(editor?.getTaskKey?.() || editor?.taskTypeWidget?.value);
    // r2v: left 3×3 assets + right tall prompt/preview.
    const rowH = key === "r2v" ? 420 : (isVideoBatchTask(key) ? 155 : 130);
    return 200 + Math.min(n, 4) * rowH + 60;
}

export function setToolbarDisabledForBatch(editor, disabled) {
    const btns = [
        editor.btnVideo,
        editor.btnVideoAppend,
        editor.root?.querySelector('[data-a="split"]'),
        editor.root?.querySelector('[data-a="smart-split"]'),
        editor.root?.querySelector('[data-a="equal"]'),
        editor.root?.querySelector('[data-a="del"]'),
        editor.root?.querySelector('[data-a="mode-global"]'),
        editor.root?.querySelector('[data-a="mode-segment"]'),
    ];
    for (const btn of btns) {
        if (!btn) continue;
        // Batch / t2v / i2v: fully hide video-editing controls (not just disable).
        btn.classList.toggle("hidden", disabled);
        btn.disabled = disabled;
        btn.classList.toggle("bd-disabled", disabled);
    }
    if (editor.equalCountInput) {
        editor.equalCountInput.classList.toggle("hidden", disabled);
        editor.equalCountInput.disabled = disabled;
        editor.equalCountInput.classList.toggle("bd-disabled", disabled);
    }
    editor.root?.querySelector('[data-r="equal-n"]')?.classList.toggle("hidden", disabled);
    editor.root?.querySelector(".bd-mode")?.classList.toggle("hidden", disabled);
}

/** r2v: fl2v-like toolbar — timeline visible; add group sits left of task select. */
export function setR2vToolbar(editor, enabled) {
    const hide = [
        editor.btnVideo,
        editor.btnVideoAppend,
        editor.root?.querySelector('[data-a="split"]'),
        editor.root?.querySelector('[data-a="smart-split"]'),
        editor.root?.querySelector('[data-a="equal"]'),
        editor.root?.querySelector('[data-a="mode-global"]'),
        editor.root?.querySelector('[data-a="mode-segment"]'),
    ];
    for (const btn of hide) {
        if (!btn) continue;
        btn.classList.toggle("hidden", enabled);
        btn.disabled = enabled;
        btn.classList.toggle("bd-disabled", enabled);
    }
    if (editor.equalCountInput) {
        editor.equalCountInput.classList.toggle("hidden", enabled);
        editor.equalCountInput.disabled = enabled;
        editor.equalCountInput.classList.toggle("bd-disabled", enabled);
    }
    editor.root?.querySelector('[data-r="equal-n"]')?.classList.toggle("hidden", enabled);
    editor.root?.querySelector(".bd-mode")?.classList.toggle("hidden", enabled);

    const del = editor.root?.querySelector('[data-a="del"]');
    if (del) {
        del.disabled = false;
        del.classList.remove("bd-disabled", "hidden");
        del.textContent = enabled ? t("toolbar.deleteSelectedGroup") : t("toolbar.deleteSegment");
        del.setAttribute("data-i18n", enabled ? "toolbar.deleteSelectedGroup" : "toolbar.deleteSegment");
        del.setAttribute("data-i18n-title", enabled ? "tooltip.deleteSelectedFl2vGroup" : "tooltip.deleteSegment");
        del.title = enabled
            ? t("tooltip.deleteSelectedFl2vGroup")
            : t("tooltip.deleteSegment");
    }
    const addBtn = editor.root?.querySelector('[data-a="r2v-add-group"]');
    if (addBtn) {
        addBtn.classList.toggle("hidden", !enabled);
        addBtn.disabled = !enabled;
    }
    const batchAdd = editor.batchPanel?.querySelector('[data-a="batch-add"]');
    if (batchAdd) batchAdd.classList.toggle("hidden", enabled);
    updateR2vToolbarBtns(editor);
}

export function updateR2vToolbarBtns(editor) {
    const addBtn = editor?.root?.querySelector?.('[data-a="r2v-add-group"]');
    if (!addBtn) return;
    const show = !!editor?.isR2vBatch?.();
    addBtn.classList.toggle("hidden", !show);
    addBtn.disabled = !show;
}
