/** @-mention picker for MiniMax H3 director refs (图片 → <Picture N>, 音频 → <Audio J>). */

import { api } from "../../scripts/api.js";
import {
    refAudioLabel,
    refAudioPromptTag,
    refImageLabel,
    refImagePromptTag,
    refVideoLabel,
    refVideoPromptTag,
} from "./minimax_gen_timeline.js";
import { t } from "./minimax_i18n.js";

const MENTION_STYLES = `
.bd-mention-menu{position:fixed;z-index:10050;min-width:210px;max-width:300px;max-height:240px;overflow:auto;background:#252525;border:1px solid #444;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.45);padding:4px 0}
.bd-mention-menu.hidden{display:none!important}
.bd-mention-title{padding:6px 10px 4px;font-size:10px;color:#888;user-select:none}
.bd-mention-item{display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;font-size:11px;color:#ddd}
.bd-mention-item:hover,.bd-mention-item.active{background:#333;color:#fff}
.bd-mention-item img{width:36px;height:36px;object-fit:cover;border-radius:4px;flex-shrink:0;background:#111;border:1px solid #333}
.bd-mention-item .bd-mention-label{font-weight:600;color:#4fff8f}
.bd-mention-empty{padding:10px 12px;font-size:11px;color:#888;text-align:center;line-height:1.4}
`;

let stylesInjected = false;

function injectStyles() {
    if (stylesInjected) return;
    stylesInjected = true;
    const el = document.createElement("style");
    el.textContent = MENTION_STYLES;
    document.head.appendChild(el);
}

function inputViewUrl(filename, type = "input") {
    const subfolder = filename.includes("/") ? filename.slice(0, filename.lastIndexOf("/")) : "";
    const base = subfolder ? filename.slice(subfolder.length + 1) : filename;
    const params = new URLSearchParams({ filename: base, type });
    if (subfolder) params.set("subfolder", subfolder);
    return api.apiURL(`/view?${params.toString()}`);
}

function refThumbUrl(ref) {
    if (ref?.imageFile) return inputViewUrl(ref.imageFile, "input");
    if (ref?.imageB64) {
        return ref.imageB64.startsWith("data:") ? ref.imageB64 : `data:image/png;base64,${ref.imageB64}`;
    }
    return "";
}

function listAvailableMentions(refs, audios, videos) {
    const items = [];
    for (const r of [...(refs || [])]
        .filter((x) => x?.imageFile || x?.imageB64)
        .sort((a, b) => Number(a.index ?? a.slot ?? 0) - Number(b.index ?? b.slot ?? 0))) {
        const index = Number(r.index ?? r.slot ?? 0);
        items.push({
            index,
            kind: "image",
            label: refImageLabel(index),
            tag: refImagePromptTag(index),
            thumb: refThumbUrl(r),
        });
    }
    for (const v of [...(videos || [])]
        .filter((x) => x?.videoFile || x?.fileName)
        .sort((a, b) => Number(a.index ?? a.slot ?? 0) - Number(b.index ?? b.slot ?? 0))) {
        const index = Number(v.index ?? v.slot ?? 0);
        items.push({
            index,
            kind: "video",
            label: refVideoLabel(index),
            tag: refVideoPromptTag(index),
            thumb: "",
        });
    }
    for (const a of [...(audios || [])]
        .filter((x) => x?.audioFile || x?.fileName)
        .sort((a, b) => Number(a.index ?? a.slot ?? 0) - Number(b.index ?? b.slot ?? 0))) {
        const index = Number(a.index ?? a.slot ?? 0);
        items.push({
            index,
            kind: "audio",
            label: refAudioLabel(index),
            tag: refAudioPromptTag(index),
            thumb: "",
        });
    }
    return items;
}

function positionMenu(menu, textarea) {
    const rect = textarea.getBoundingClientRect();
    menu.style.left = `${Math.max(8, rect.left)}px`;
    menu.style.top = `${Math.min(window.innerHeight - 16, rect.bottom + 4)}px`;
    menu.style.maxWidth = `${Math.max(210, rect.width)}px`;
}

/**
 * Wire @-mention dropdown on a prompt textarea.
 * Typing `@` lists uploaded reference images / audios; pick one to insert tags.
 */
export function wirePromptImageMentions(editor, textarea, getMedia) {
    if (!textarea || textarea.dataset.mentionWired) return;
    textarea.dataset.mentionWired = "1";
    injectStyles();

    let menu = null;
    let mentionStart = -1;
    let activeIndex = 0;
    let filtered = [];

    const ensureMenu = () => {
        if (menu) return menu;
        menu = document.createElement("div");
        menu.className = "bd-mention-menu hidden";
        menu.setAttribute("role", "listbox");
        document.body.appendChild(menu);
        return menu;
    };

    const closeMenu = () => {
        mentionStart = -1;
        filtered = [];
        activeIndex = 0;
        if (menu) menu.classList.add("hidden");
    };

    const renderMenu = (query) => {
        const m = ensureMenu();
        const media = typeof getMedia === "function" ? getMedia() : {};
        const all = listAvailableMentions(media.refs, media.audios, media.videos);
        const q = (query || "").toLowerCase();
        filtered = all.filter((item) => {
            if (!q) return true;
            const label = String(item.label || "").toLowerCase();
            const tag = String(item.tag || "").toLowerCase();
            return label.includes(q) || tag.includes(q)
                || (item.kind === "image" && `picture ${item.index + 1}`.includes(q))
                || (item.kind === "video" && `video ${item.index + 1}`.includes(q))
                || (item.kind === "audio" && `audio ${item.index + 1}`.includes(q));
        });
        m.innerHTML = "";
        const title = document.createElement("div");
        title.className = "bd-mention-title";
        title.textContent = t("mention.title");
        m.appendChild(title);

        if (!filtered.length) {
            const empty = document.createElement("div");
            empty.className = "bd-mention-empty";
            empty.textContent = all.length ? t("mention.emptyFilter") : t("mention.emptyNoUpload");
            m.appendChild(empty);
        } else {
            filtered.forEach((item, i) => {
                const row = document.createElement("div");
                row.className = `bd-mention-item${i === activeIndex ? " active" : ""}`;
                row.dataset.index = String(i);
                if (item.thumb) {
                    const img = document.createElement("img");
                    img.src = item.thumb;
                    img.alt = item.label;
                    row.appendChild(img);
                }
                const label = document.createElement("span");
                label.innerHTML = `<span class="bd-mention-label">${item.label}</span>`;
                row.appendChild(label);
                row.onmousedown = (e) => {
                    e.preventDefault();
                    insertMention(item.tag);
                };
                m.appendChild(row);
            });
        }
        positionMenu(m, textarea);
        m.classList.remove("hidden");
    };

    const insertMention = (tag) => {
        const text = textarea.value;
        const cursor = textarea.selectionStart;
        const before = text.slice(0, mentionStart);
        const after = text.slice(cursor);
        const next = `${before}${tag} ${after}`;
        textarea.value = next;
        const pos = before.length + tag.length + 1;
        textarea.setSelectionRange(pos, pos);
        closeMenu();
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        textarea.focus();
    };

    const openIfMention = () => {
        const cursor = textarea.selectionStart;
        const before = textarea.value.slice(0, cursor);
        const match = before.match(/@([^\s@]*)$/);
        if (!match) {
            closeMenu();
            return;
        }
        mentionStart = cursor - match[0].length;
        activeIndex = 0;
        renderMenu(match[1]);
    };

    textarea.addEventListener("input", openIfMention);
    textarea.addEventListener("click", openIfMention);
    textarea.addEventListener("keyup", (e) => {
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) openIfMention();
    });

    textarea.addEventListener("keydown", (e) => {
        if (menu?.classList.contains("hidden") || !filtered.length) return;
        if (e.key === "ArrowDown") {
            e.preventDefault();
            activeIndex = (activeIndex + 1) % filtered.length;
            renderMenu(textarea.value.slice(mentionStart + 1, textarea.selectionStart));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            activeIndex = (activeIndex - 1 + filtered.length) % filtered.length;
            renderMenu(textarea.value.slice(mentionStart + 1, textarea.selectionStart));
        } else if (e.key === "Enter" || e.key === "Tab") {
            e.preventDefault();
            insertMention(filtered[activeIndex].tag);
        } else if (e.key === "Escape") {
            e.preventDefault();
            closeMenu();
        }
    });

    document.addEventListener("mousedown", (e) => {
        if (!menu || menu.classList.contains("hidden")) return;
        if (e.target === textarea || menu.contains(e.target)) return;
        closeMenu();
    });

    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("resize", closeMenu);
}

/** Attach @-mention to global + segment positive prompt fields. */
export function mountPromptImageMentions(editor) {
    if (!editor) return;
    wirePromptImageMentions(editor, editor.globalPrompt, () => ({
        refs: editor.timeline?.global?.refs || [],
        audios: editor.timeline?.global?.refAudios || [],
    }));
    wirePromptImageMentions(editor, editor.segPrompt, () => {
        const seg = editor.timeline?.segments?.[editor.selectedIndex];
        return {
            refs: seg?.refs || [],
            audios: seg?.refAudios || [],
        };
    });
}

/* ------------------------------------------------------------------ */
/* 提示词素材高亮层（Phase B 增强，2026-08-07）                         */
/* 在 prompt textarea 上叠一层只读高亮：把 <Picture N>/<Video N>/      */
/* <Audio N>/@资产名 渲染成带缩略图的彩色卡片，和正文区分。             */
/* 底层仍是纯文本 textarea（保存 / 模型解析 / 自动匹配全部不受影响）。  */
/* ------------------------------------------------------------------ */

const PROMPT_HL_STYLES = `
.bd-phl-wrap{position:relative;flex:1 1 auto;min-height:240px;overflow:hidden}
.bd-phl-hl{position:absolute;top:0;left:0;right:0;overflow:hidden;pointer-events:none;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;box-sizing:border-box;padding:10px;border:1px solid transparent;border-radius:8px;font-family:inherit;font-size:12px;line-height:1.45;color:rgba(255,255,255,.92);z-index:1}
.bd-phl-token{display:inline;background:rgba(79,255,143,.14);color:#5bff9e;border:1px solid rgba(79,255,143,.4);border-radius:4px;padding:0 3px;margin:0 1px;font-weight:600;white-space:nowrap}
.bd-phl-token img{width:15px;height:15px;object-fit:cover;border-radius:3px;vertical-align:-3px;margin-right:2px;display:inline-block}
.bd-phl-token.t-video{background:rgba(90,150,255,.15);color:#7ea7ff;border-color:rgba(90,150,255,.4)}
.bd-phl-token.t-audio{background:rgba(255,180,60,.15);color:#ffc06a;border-color:rgba(255,180,60,.4)}
.bd-phl-token.t-asset{background:rgba(255,110,120,.15);color:#ff8f9c;border-color:rgba(255,110,120,.4)}
.bd-phl-wrap textarea{color:transparent!important;caret-color:#fff!important;background:transparent!important;position:absolute!important;top:0!important;left:0!important;right:0!important;bottom:0!important;width:auto!important;height:auto!important;min-height:0!important;resize:none!important;overflow-y:auto!important;overflow-x:hidden!important;scrollbar-width:none!important;overflow-anchor:none!important;z-index:2!important;box-sizing:border-box!important;padding:10px!important;border:1px solid #2e2e2e!important;border-radius:8px!important;font-family:inherit!important;font-size:12px!important;line-height:1.45!important;white-space:pre-wrap!important;word-wrap:break-word!important;overflow-wrap:break-word!important;margin:0!important}
.bd-phl-wrap textarea::selection{background:rgba(255,255,255,.22)}
.bd-phl-wrap textarea::-webkit-scrollbar{display:none!important}
`;

let phlStylesInjected = false;

function injectPhlStyles() {
    if (phlStylesInjected) return;
    phlStylesInjected = true;
    const el = document.createElement("style");
    el.textContent = PROMPT_HL_STYLES;
    document.head.appendChild(el);
}

function escHtml(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

const PHL_TOKEN_RE = /(<Picture\s+\d+>|<Video\s+\d+>|<Audio\s+\d+>|@[^\s，。；、,，；]+)/g;

/** 把 prompt 文本拆成高亮片段；refs/audios/videos 用于取缩略图，assets 用于 @资产名。 */
function renderPromptHighlight(text, media, assets) {
    const thumbs = new Map();
    const assetThumbs = new Map();
    for (const r of [...(media?.refs || [])].filter((x) => x?.imageFile || x?.imageB64)) {
        const idx = Number(r.index ?? r.slot ?? 0);
        thumbs.set(`<Picture ${idx + 1}>`, refThumbUrl(r));
    }
    for (const r of [...(media?.videos || [])].filter((x) => x?.videoFile || x?.fileName)) {
        const idx = Number(r.index ?? r.slot ?? 0);
        thumbs.set(`<Video ${idx + 1}>`, "");
    }
    for (const r of [...(media?.audios || [])].filter((x) => x?.audioFile || x?.fileName)) {
        const idx = Number(r.index ?? r.slot ?? 0);
        thumbs.set(`<Audio ${idx + 1}>`, "");
    }
    for (const c of [...(assets?.cast || []), ...(assets?.locations || [])]) {
        const name = (c?.name || "").trim();
        if (!name) continue;
        assetThumbs.set(`@${name}`, c?.imageFile ? inputViewUrl(c.imageFile, "input") : "");
    }

    let html = "";
    let last = 0;
    let m;
    PHL_TOKEN_RE.lastIndex = 0;
    while ((m = PHL_TOKEN_RE.exec(text))) {
        html += escHtml(text.slice(last, m.index));
        const token = m[0];
        const idx = token.indexOf(">");
        const body = idx >= 0 ? token.slice(1, idx) : token;
        let thumb = "";
        let cls = "";
        if (token.startsWith("@")) {
            thumb = assetThumbs.get(token) || "";
            cls = " t-asset";
        } else {
            thumb = thumbs.get(token) || "";
            cls = token.startsWith("<Picture") ? "" : token.startsWith("<Video") ? " t-video" : " t-audio";
        }
        const thumbImg = thumb ? `<img src="${escHtml(thumb)}" alt="">` : "";
        html += `<span class="bd-phl-token${cls}">${thumbImg}${escHtml(body)}</span>`;
        last = m.index + token.length;
    }
    html += escHtml(text.slice(last));
    return html;
}

/** 给 prompt textarea 挂高亮层。返回 {wrap, hl, update}，update 在 textarea input/scroll 时调用。 */
export function attachPromptHighlight(editor, textarea, getMedia, getAssets) {
    if (!textarea || textarea.dataset.phlWired) return null;
    textarea.dataset.phlWired = "1";
    injectPhlStyles();

    const wrap = document.createElement("div");
    wrap.className = "bd-phl-wrap";
    textarea.parentNode.insertBefore(wrap, textarea);
    const hl = document.createElement("div");
    hl.className = "bd-phl-hl";
    hl.setAttribute("aria-hidden", "true");
    wrap.appendChild(hl);
    wrap.appendChild(textarea);

    const update = () => {
        const media = typeof getMedia === "function" ? getMedia() : {};
        const assets = typeof getAssets === "function" ? getAssets() : {};
        hl.innerHTML = renderPromptHighlight(textarea.value || "", media, assets);
        hl.style.transform = `translateY(${-textarea.scrollTop}px)`;
    };
    textarea.addEventListener("input", update);
    textarea.addEventListener("scroll", update, { passive: true });
    textarea.addEventListener("keyup", update);
    // 初始渲染一次
    update();
    return { wrap, hl, update };
}

