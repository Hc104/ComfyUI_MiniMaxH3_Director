#!/usr/bin/env python3
"""H3 Prompt Builder（P0-①，用户 2026-08-13 拍板 ③/④）。

项目自己的 H3 Prompt Schema：``minimax-h3-project-v1`` —— **内部规范，非官方
MiniMax H3 Skill**（三段结构灵感来自 h3-prompt-writing skill 通用框架，但字段名/
组装规则/时间轴/参考图标签为本项目实现，文档明确标注，可随项目版本化升级）。

输入：DirectorIntent（``director_intent.build_shot_intent`` 产物，**已过实体
边界检查**，见 entity_cleanse.entity_in_shot_scope）+ 可选资产绑定
（entity_key → {asset_id, image_file, ref_image}）。
输出：三段 H3 Prompt + 结构化 references + 段落级来源标记。

设计铁律（用户拍板）：
1. **双轨合并去重**：原文事实（retained_facts 逐字）优先逐字进描述；AI 补全
   （emotion/composition）作为补充句，与原文句子级去重（相似度过高 → 跳过 AI 句），
   绝不覆盖原文 —— 原文事实最高优先级。
2. **三段结构**：integrated_multimodal_description / overall_soundscape /
   non_diegetic_music，含 [0-{d}s] 时间轴前缀。
3. **[REF: 实体名] 仅作可读标签**；真实参考图绑定 = 结构化 references
   （entity_key → asset_id → image_file → ref_image），**不依赖 Prompt 文本顺序**，
   增删角色/改顺序/重新匹配都不会错位。
4. **provenance** 记录每段「原文事实 / AI 补全 / 规则生成」三类来源，供回归审计
   （用户硬性要求：每镜从 source_text 追溯到最终 H3 Prompt）。
5. **对白位置（#545 根因修复 D，2026-08-15）**：对白**只进** integrated_multimodal_description
   的 ``<d>[Chinese] 原文</d>`` 块（说话人稳定 ID ``(S1)/(S2)``），**绝不进** overall_soundscape
   （官方 base-en.txt §4.6：「对白/唱歌/剧情音乐属于集成描述区，不应在音景重复」）。
   描述正文渲染时剥离与已知对白匹配的「」引号段，避免对白在描述区出现两次；
   provenance 仍保留原文事实逐字（只改渲染层）。

纯规则、零 LLM、零显存、无网络（标准库 difflib/re/dataclasses）。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .production_plan import EntityType, IntentSource
from .prompt_compiler import compile_constraint_block  # 生成约束层 v1.0（P1 接线）
from .shot_plan import ShotPlan  # 生成约束层 v1.0（P1 接线：constraint dict → ShotPlan 重建）

SCHEMA_VERSION = "minimax-h3-project-v1"

# 无字幕约束（#597/#608，2026-08-17）：H3 收到描述区 <d> 对白块后容易把它渲染成画面字幕
# （不可控/不完整/错字，且与 TTS 对白不同步）。字幕统一交后期层（TTS 对白 + FFmpeg
# 烧录/外挂 SRT），因此描述区尾部统一追加该负向约束；provenance 记 rule_generated。
# #608 升级（2026-08-17）：纯中文约束实测压不住 H3（用户复测画面仍出现字幕），升级为
# 中英双语强负面约束——H3 对英文负向指令的执行更稳，且显式点名 subtitle/caption/
# on-screen text/dialogue text 等全部画面文字形态，杜绝 H3 把 <d> 对白渲染成字幕。
NO_SUBTITLE_RULE = (
    "画面中不要渲染任何字幕、标题、文字叠加或对白字幕，对白仅作为声音传达，"
    "绝不渲染为画面文字。Do NOT render any subtitles, captions, on-screen text, "
    "titles, banners, UI text, or dialogue text in the picture. Dialogue and speech "
    "are audio-only and must never appear as on-screen text or subtitles."
)

# #608（2026-08-17）：紧跟每个 <d> 对白块的「仅声音」说明——紧贴触发字幕渲染的内容
# （<d> 对白块）做定点负向约束，与描述区尾部全局约束形成双信号。官方格式允许在
# </d> 之后追加动作/传达说明（base-en.txt §4.4 voiceover「lips remain closed」先例）。
_DIALOGUE_AUDIO_ONLY_NOTE = "（仅声音传达，画面无字幕。Audio-only, no on-screen text.）"

# v2.0 Visual Style 层防崩坏约束（CAMERA_RULES_V2 §5.1，P1 接线）：画风块注入后，
# 描述区尾部统一追加双语防崩坏负向约束——人物一致性 + 人体结构 + 画风统一是
# 抖音漫剧「一镜一镜崩脸」的核心痛点，固定句（非每镜临时生成）保证全剧级一致。
_ANTI_BREAK_RULE = (
    "人物五官、发型、服饰全程保持不变，人体结构正常，无畸形崩坏，画风统一，与参考图一致。"
    "Do NOT change the character's facial features, hairstyle, or costume. "
    "Keep proper human anatomy. No deformed limbs, no distortion. "
    "Maintain a consistent art style matching the references."
)

# 防崩坏主题关键词：命中即视为已被 _ANTI_BREAK_RULE 覆盖（合并 negative_rules 时跳过），
# 避免内置预设默认负面（visual_style._COMMON_NEGATIVE）与固定句双重输出。
_ANTI_BREAK_KEYS: Tuple[str, ...] = (
    "崩坏", "畸形", "变形", "五官", "发型", "服饰", "人体结构", "结构正常",
    "distortion", "deformed", "distort", "anatomy", "facial", "limb", "costume",
    "consistent appearance", "hairstyle",
)


def _merge_negative_rules(profile: Any) -> str:
    """VisualStyleProfile.negative_rules 合并进防崩坏句（与 _ANTI_BREAK_RULE 去重）。

    profile 可为 VisualStyleProfile dataclass 或 dict（from_dict 兼容）。返回合并字符串，
    已逐字出现在固定句或命中防崩坏主题关键词的项跳过（内置预设负面内化于固定句），
    只保留自定义负面（如「无文字水印」「保持眼神清澈」）。
    """
    try:
        negs = profile.negative_rules if hasattr(profile, "negative_rules") \
            else ((profile or {}).get("negative_rules") or [])
    except Exception:  # pragma: no cover - 坏 profile 兜底
        return ""
    extra: List[str] = []
    for n in (negs or []):
        t = str(n).strip()
        if not t:
            continue
        if t in _ANTI_BREAK_RULE:
            continue
        if any(k in t for k in _ANTI_BREAK_KEYS):
            continue
        extra.append(t)
    return "".join(extra)


# 时间轴前缀：{int 秒} → "[0-5s]"；{float 秒} → "[0-4.5s]"
_TIMELINE_RE = re.compile(r"^\[0-\d")


@dataclass
class H3Prompt:
    """H3 Prompt 组装产物（项目 Schema minimax-h3-project-v1）。

    references：结构化参考图绑定（entity_key → asset_id → image_file → ref_image），
    与 Prompt 中的 [REF: 名] 可读标签一一对应，但**绑定以本数组为准**。
    provenance：段落级来源标记（user_facts / ai_supplement / rule_generated），
    供 UI 三层可追溯与回归审计。
    """

    schema: str = SCHEMA_VERSION
    shot_id: str = ""
    scene_id: str = ""
    duration_sec: float = 5.0
    integrated_multimodal_description: str = ""
    overall_soundscape: str = ""
    non_diegetic_music: str = ""
    references: List[Dict[str, str]] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "duration_sec": self.duration_sec,
            "integrated_multimodal_description": self.integrated_multimodal_description,
            "overall_soundscape": self.overall_soundscape,
            "non_diegetic_music": self.non_diegetic_music,
            "references": list(self.references),
            "timeline": list(self.timeline),
            "provenance": self.provenance,
        }


def _time_label(duration_sec: float) -> str:
    """duration → H3 时间轴前缀："[0-5s]" / "[0-4.5s]"。"""
    d = float(duration_sec or 0.0)
    if d <= 0:
        d = 5.0
    if d == int(d):
        return f"[0-{int(d)}s]"
    return f"[0-{d:g}s]"


def _too_similar(a: str, b: str, ratio: float = 0.7) -> bool:
    """句子级去重：a 与 b 相似度过高 → 跳过 b（保留 a=原文事实优先）。"""
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio


def _contains_noun(text: str, chunk: str) -> bool:
    """chunk（实体名/短句）逐字出现在 text 中（词根级去重用）。"""
    return bool(chunk) and chunk in text


def _strip_camera_prefix(s: str) -> str:
    """输入可能自带「镜头：」前缀（Workbench cameraText 字段录入时带）→ 渲染侧不重复。"""
    s = s.strip()
    return s[len("镜头："):] if s.startswith("镜头：") else s


def _strip_fact_duplicates(comp: str, facts: List[str], joined: str) -> str:
    """composition（visual 覆盖）内与 retained_facts 逐字重叠的原文剥离（A1-1）。

    原文事实区已无条件逐字输出（最高优先级），若 composition 嵌套了完整原文（用户把
    剧本原文抄进 visual 框）会造成整句重复。策略：
      1. 整段 joined（facts 逗号拼接）在 comp 内 → 整段剥离（最典型形态）；
      2. 逐 fact 完整子串剥离，要求子串前后非汉字（防「山雨楼」误伤「山雨楼外」）；
      3. 清理剥离后残留的连续 / 首尾逗号顿号。
    返回剥离后的 composition；剥离后为空 → 上层不再追加（facts 区已覆盖该信息）。
    """
    if not comp or not facts:
        return comp
    out = comp
    if joined:
        escaped = re.escape(joined)
        pat = re.compile(rf"(?<![一-鿿]){escaped}(?![一-鿿])")
        out = pat.sub("", out)
    for f in sorted((f for f in facts if f and len(f) >= 3), key=len, reverse=True):
        escaped = re.escape(f)
        pat = re.compile(rf"(?<![一-鿿]){escaped}(?![一-鿿])")
        out = pat.sub("", out)
    out = re.sub(r"[，,、\s]{2,}", "，", out)
    out = re.sub(r"^[，,、\s]+", "", out)
    out = re.sub(r"[，,、\s]+$", "", out)
    return out.strip()


_QUOTE_PAIR_RE = re.compile(r"[「“【]([^「」”】]*?)[」”】]")


def _strip_dialogue_quotes(text: str, dialogue_texts: List[str]) -> str:
    """渲染层剥离已进 <d> 通道的对白引号段（防对白在描述区出现两次，#545 修复 D）。

    只剥离「引号内文与已知对白文本（去尾部句读后）匹配」的段；非对白引号
    （如「山雨楼」「吐槽值」类强调词）不匹配则保留。provenance 仍记录原始
    事实逐字，仅改渲染文本 —— 对白正文唯一出口 = 描述区 ``<d>`` 块。
    """
    if not text or not dialogue_texts:
        return text
    norms = {
        re.sub(r"[。！？!?…\s]+$", "", t).strip()
        for t in dialogue_texts if t and t.strip()
    }

    def _repl(m: re.Match) -> str:
        inner = m.group(1)
        if re.sub(r"[。！？!?…\s]+$", "", inner).strip() in norms:
            return ""
        return m.group(0)

    out = _QUOTE_PAIR_RE.sub(_repl, text)
    # 清理剥离后悬挂的标点：
    #  1. 剥离对白后孤立的引导冒号（后接逗号/顿号或串尾）：「林薇薇挑眉：，」→「林薇薇挑眉，」
    #     （「时间：午夜」类非对白冒号后接正文，不受影响）
    #  2. 多分隔符合并 + 尾部逗号/顿号/空白清理。
    out = re.sub(r"[：:](?=[，,、]|$)", "", out)
    out = re.sub(r"[，,、]{2,}", "，", out)
    out = re.sub(r"[：:，,、\s]+$", "", out.rstrip())
    return out.strip()


def default_entity_key(e: Dict[str, Any]) -> str:
    """实体 dict → entity_key（`:类型:canonical名`，与 asset_registry.entity_key 一致）。"""
    return f"{str(e.get('type', '') or 'unknown').strip().lower()}:{_canon(e.get('name', ''))}"


def _canon(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip().lower())


def plan_entity_key(e: Dict[str, Any], char_anchors: Optional[Dict[str, str]] = None) -> str:
    """build_plan 场景的实体 key：角色别名（柳姑娘→柳如烟）经锚点归一到 canonical，
    与资产绑定侧（entity_cleanse 清洗后最终名）对齐，保证 references 匹配成功。"""
    name = str(e.get("name", "") or "")
    if char_anchors and str(e.get("type", "")) == EntityType.CHARACTER:
        from .entity_cleanse import resolve_character_alias  # noqa: PLC0415
        canon = resolve_character_alias(name, char_anchors)
        if canon and canon != name:
            name = canon
    return f"{str(e.get('type', '') or 'unknown').strip().lower()}:{_canon(name)}"


def _join_facts(facts: List[str]) -> str:
    """retained_facts 逐字合并（保序，逗号连接，去空）。原文事实主文本。"""
    return "，".join([f.strip() for f in facts if f.strip()])


def _append(main: str, suffix: str) -> str:
    """追加句子（句号分隔），维护标点闭合：对白引号「…」结尾先补句号，
    引号内已有句号（「…。」）则不补，避免 rstrip 吃掉引号后的句读。"""
    if not main:
        return suffix or ""
    if not suffix:
        return main
    if main[-1] in "。？！":
        return f"{main}{suffix}"
    if main[-1] == ".":
        # 英文句点结尾（V1.0 空间连续性块 Spatial Continuity 以 . 收尾）：
        # 转中文句号再分隔，避免「.。」双句号（与 A1-4 同性质）。
        return f"{main.rstrip('. ')}。{suffix}"
    if main[-1] in "」』”":
        if len(main) >= 2 and main[-2] in "。？！":
            return f"{main}{suffix}"
        return f"{main}。{suffix}"
    return f"{main.rstrip('，；、')}。{suffix}"


def _append_inline(main: str, suffix: str) -> str:
    """逗号延续追加（AI 补全/画面细节），引号闭合时先补句号。"""
    if not main:
        return suffix or ""
    if main[-1] in "」』”":
        if len(main) >= 2 and main[-2] in "。？！":
            return f"{main}{suffix}"
        return f"{main}。{suffix}"
    return f"{main.rstrip('。，；、')}，{suffix}"


# ---------- P0-③ 跨镜延续渲染（可选用，向后兼容） ----------
# 镜头关系中文标签（渲染侧；director_intent._transition_type 只产英文枚举）。
# 注意：reverse/cut 的衔接措辞已由 camera_desc 前缀（「承接上镜视线，反打对切」等）
# 覆盖，这里不重复渲染；只补景别节奏 / 反应 / 延续语义。
# P0 Story Timeline（2026-08-14 用户拍板）新增场景切换类：
#   scene_cut = 切入全新场景；cross_cut = 平行剪辑切回本场景（有 scene 前驱）。
_TRANSITION_CN: Dict[str, str] = {
    "continuation": "延续上一镜的叙事，",
    "reaction": "特写接特写，突出反应。",
    "close_to_wide": "由特写切至全景，拉开空间。",
    "wide_to_close": "由全景切至特写，收紧视线。",
    "match_action": "承接上一镜动作，连续推进。",
    "scene_cut": "切入新场景，重新建立空间。",
    "cross_cut": "平行剪辑切回本场景，延续此前的空间状态。",
}


def _continuation_block(intent: Dict[str, Any], main_txt: str) -> str:
    """P0-③ 跨镜延续块：上一镜状态 + 镜头关系 + 必须保持清单。

    仅当 ``continuity.prev_shot_state`` 存在（本镜有上一镜）时输出；首镜 / 无 prev
    为空串（严格向后兼容：旧数据 / 单镜链路渲染不变）。

    遵守「不将原始文本强制注入 H3 Prompt」：prev_shot_state 只含**结构化身份字段**
    （主体/角色/地点/光线/情绪/运镜模板），不含上一镜 source_text 逐字。
    """
    cont = intent.get("continuity") or {}
    prev_state = cont.get("prev_shot_state") or {}
    if not prev_state:
        return ""
    parts: List[str] = []
    prev_subject = str(prev_state.get("subject", "") or "").strip()
    prev_chars = [str(c) for c in (prev_state.get("characters") or []) if str(c).strip()]
    prev_loc = str(prev_state.get("location", "") or "").strip()
    cur_subject = str(intent.get("subject", "") or "").strip()
    cur_chars = [str(c) for c in (intent.get("characters") or []) if str(c).strip()]

    # 延续句：上一镜与本镜共享主体/角色才写「延续」，且不逐字重复已出现文本。
    shared = [c for c in prev_chars if c in cur_chars]
    holder = "、".join(shared) or (prev_subject if prev_subject == cur_subject else "")
    if holder and not _contains_noun(main_txt, holder):
        where = (
            f"，仍在{prev_loc}"
            if prev_loc and not _contains_noun(main_txt, prev_loc)
            else ""
        )
        parts.append(f"延续上一镜，{holder}{where}。")

    # 镜头关系句（camera_desc 已覆盖的反打/承接不重复渲染）。
    trans_cn = _TRANSITION_CN.get(str(cont.get("transition_type", "") or ""))
    if trans_cn:
        parts.append(trans_cn)

    # 必须保持清单（身份类一致性：角色外观 / 场景 / 光线）。
    keep = [str(k) for k in (cont.get("must_keep") or []) if str(k).strip()]
    if keep:
        parts.append("保持" + "、".join(keep) + "。")

    return " ".join(parts)


def _build_dialogue_block(audio: Dict[str, Any]) -> tuple[str, Dict[str, List[str]]]:
    """对白块（官方 <d> 格式 + 说话人稳定 ID，进 integrated_multimodal_description）。

    官方 H3 格式（base-en.txt §4.4）：对白/唱歌/剧情音乐属于集成描述区，用
    ``<d>[Language] 原文</d>`` 包裹，说话人稳定 ID ``(S1)/(S2)``（首现分配，
    同一说话人保持同一 ID），**绝不进 overall_soundscape**（#545 根因修复 D）。

    #608（2026-08-17）：每个 <d> 块后紧跟 ``_DIALOGUE_AUDIO_ONLY_NOTE``（仅声音
    传达、画面无字幕），紧贴触发字幕渲染的对白内容做定点负向约束；官方格式允许在
    </d> 之后追加动作/传达说明（§4.4 voiceover「lips remain closed」先例），
    不破坏 <d> 内部「只放语言标签 + 原文」的规范。

    无对白返回 ("", 空 prov)，严格向后兼容（山雨客栈类无对白镜头渲染不变）。
    对白原文逐字保留在 <d> 内，不译不改。
    """
    dialogues = [
        {"speaker": str(d.get("speaker", "") or "").strip(),
         "text": str(d.get("text", "") or "").strip()}
        for d in (audio.get("dialogue") or [])
        if str(d.get("text", "") or "").strip()
    ]
    prov: Dict[str, List[str]] = {
        "user_facts": [], "ai_supplement": [], "rule_generated": [],
    }
    if not dialogues:
        return "", prov
    spk_ids: Dict[str, str] = {}
    parts: List[str] = []
    for d in dialogues:
        spk = d["speaker"]
        if spk not in spk_ids:
            spk_ids[spk] = f"(S{len(spk_ids) + 1})"
        sid = spk_ids[spk]
        who = f"{sid}{spk}说" if spk else f"{sid}说"
        parts.append(f"{who}，<d>[Chinese] {d['text']}</d>{_DIALOGUE_AUDIO_ONLY_NOTE}")
        prov["user_facts"].append(f"对白={d['text']}")
    return "。".join(parts), prov


def _build_description(
    intent: Dict[str, Any],
    *,
    duration_sec: float,
    ref_line: str,
    style_profile: Any = None,
    scene_name: Optional[str] = None,
) -> tuple[str, Dict[str, List[str]]]:
    """assembled integrated_multimodal_description + 三类来源标记。

    结构（H3 可读中文）：
      [0-5s] {location}。{画风块（style_profile 非 None 时，v2.0 固定前缀）}。
      {subject}。{原文事实逐字（对白引号已剥离进 <d> 块）}，
      {核心动作句}。{表情句}。{AI 补全}。镜头：{景别}{运镜}。光线：{光线}。
      氛围：{环境元素}。{对白块：(S1)XX说，<d>[Chinese] 原文</d>（仅声音说明）}。
      {参考图行}{防崩坏双语}{无字幕约束}（v2.0 + #597/#608）
    去重规则（双轨合并，原文优先）：
      - AI 补全句与任一原文事实句子相似度 ≥0.7 → 丢弃 AI 句；
      - 光线/环境若已逐字出现在组合文本 → 不重复追加；
      - v2.0 核心动作/表情句与原文事实 ≥0.7 相似 → 不追加（防止规则句与原文重复）。
    对白唯一出口 = 描述区 <d> 块（#545 根因修复 D），描述正文不再重复对白引号。
    无字幕约束（#597/#608）：每个 <d> 对白块后紧跟 _DIALOGUE_AUDIO_ONLY_NOTE（仅声音
      定点约束），描述区尾部统一追加双语强约束 NO_SUBTITLE_RULE（中英双信号），防 H3
      把 <d> 对白渲染成画面字幕；字幕统一交后期层（TTS 对白 + FFmpeg 烧录/外挂 SRT）。
    v2.0 Visual Style（CAMERA_RULES_V2 §5.1，P1 接线）：style_profile 非 None 时，
      描述区顶部注入固定画风块（style_block，全剧级一致），尾部跟随 _ANTI_BREAK_RULE
      防崩坏双语 + profile.negative_rules（负面约束）。style_profile=None → 画风块与
      防崩坏句全部跳过（旧链路输出逐字节不变，严格向后兼容）。
    """
    facts = [f.strip() for f in (intent.get("retained_facts") or []) if f.strip()]
    joined = _join_facts(facts)
    audio = intent.get("audio") or {}
    dlg_texts = [
        str(d.get("text", "") or "").strip()
        for d in (audio.get("dialogue") or [])
        if str(d.get("text", "") or "").strip()
    ]
    # 对白唯一出口 = 描述区 <d> 块（#545 修复 D）：渲染层剥离与已知对白匹配的「」引号段
    render_joined = _strip_dialogue_quotes(joined, dlg_texts) if dlg_texts else joined

    # 所有会回显原文事实的片段（composition/lighting/environment/style）统一走剥离，
    # 防对白引号经这些字段泄漏回描述正文（例：director_intent._lighting_from 会把
    # 含对白的原文事实整体当作 lighting）。剥离后为空 → 上层「未逐字出现」判断自然跳过。
    def _strip_dlg(txt: str) -> str:
        return _strip_dialogue_quotes(txt, dlg_texts) if (dlg_texts and txt) else txt

    user_facts: List[str] = list(facts)
    ai_supplement: List[str] = []
    rule_generated: List[str] = []

    # ---- 规则：时间轴 + 地点 ----
    head = f"{_time_label(duration_sec)} "
    loc = str(intent.get("location", "") or "").strip()
    if loc:
        head += f"{loc}。"
        rule_generated.append(f"地点={loc}")

    # ---- v2.0 Visual Style：全剧级固定画风块（描述区顶部；style_profile 非 None 才注入）----
    # 画风块 = style_block(profile, scene) 固定文本，全剧级一致（§3.5 不静默漂移）。
    # style_profile=None → 跳过（旧链路输出逐字节不变，严格向后兼容）。
    if style_profile is not None:
        from .visual_style import style_block  # noqa: PLC0415
        blk = style_block(style_profile, scene=scene_name or "") or ""
        if blk:
            head += blk
            rule_generated.append("画风块")

    # ---- 规则：主体结构句（subject 未逐字出现在原文事实时才有）----
    subject = str(intent.get("subject", "") or "").strip()
    main_txt = ""
    if subject and not _contains_noun(joined, subject):
        main_txt = f"{subject}为主体。"
        rule_generated.append(f"主体={subject}")

    # ---- 原文事实逐字（最高优先级；对白引号已剥离进 <d> 通道）----
    if render_joined:
        main_txt += render_joined

    # ---- v2.0 内容规则：一镜一核心动作 + 表情变化（纯规则；非空且与原文不重复才渲染）----
    # core_action/expression 由 build_plan_intents 调 extract_core_action/visualize_psych
    # 派生；与 retained_facts 逐字或 ≥0.7 相似 → 跳过（原文事实区已覆盖）。
    core_action_txt = str(intent.get("core_action", "") or "").strip()
    if (core_action_txt and not _contains_noun(main_txt, core_action_txt)
            and not any(_too_similar(f, core_action_txt) for f in facts)):
        main_txt = _append(main_txt, f"核心动作：{core_action_txt}")
        rule_generated.append(f"核心动作={core_action_txt}")
    expr_txt = str(intent.get("expression", "") or "").strip()
    if (expr_txt and not _contains_noun(main_txt, expr_txt)
            and not any(_too_similar(f, expr_txt) for f in facts)):
        main_txt = _append(main_txt, f"表情：{expr_txt}")
        rule_generated.append(f"表情={expr_txt}")

    # ---- AI 补全：composition + emotion（句子级去重，原文优先）----
    # P0-A #477：五区 visual 覆盖后 provenance[composition]=USER → 归「规则生成」
    # 桶（用户最终意图，非 AI 补全），仍参与句子级去重、不覆盖原文事实。
    ai_parts: List[str] = []
    comp = str(intent.get("composition", "") or "").strip()
    # A1-1：composition（visual 覆盖）可能嵌套原文事实（Workbench visual 框带原文），
    # 先剥离重复原文，避免「原文事实逐字」+「comp 内原文」整句重复。
    comp = _strip_fact_duplicates(comp, facts, joined)
    comp = _strip_dlg(comp)
    comp_is_user = intent.get("provenance", {}).get("composition") == IntentSource.USER
    if comp and not any(_too_similar(f, comp) for f in facts):
        ai_parts.append(comp)
        (rule_generated if comp_is_user else ai_supplement).append(comp)
    emo = str(intent.get("emotion", "") or "").strip()
    # A1-3：情绪词已逐字出现在 composition（用户/AI 画面描述）→ 不重复追加「氛围{emo}」。
    if (emo and not any(_too_similar(f, emo) for f in facts)
            and not _contains_noun(comp, emo)):
        ai_parts.append(f"氛围{emo}")
        ai_supplement.append(emo)
    if ai_parts:
        main_txt = _append_inline(main_txt, "，".join(ai_parts))

    # ---- P0-③ 跨镜延续（上一镜状态 + 镜头关系 + 必须保持；有 prev 才渲染）----
    cont_txt = _continuation_block(intent, main_txt)
    if cont_txt:
        main_txt = _append(main_txt, cont_txt)
        rule_generated.append(f"跨镜延续={cont_txt}")

    # ---- 规则：相机（P0-① 完整运镜措辞优先；结构化景别+运镜兜底）----
    cam_desc = _strip_camera_prefix(
        str(intent.get("continuity", {}).get("camera_desc", "") or "").strip())
    cam_pos = _strip_camera_prefix(str(intent.get("camera_position", "") or "").strip())
    move = _strip_camera_prefix(str(intent.get("movement", "") or "").strip())
    cam_txt = ""
    if cam_desc:
        cam_txt = f"镜头：{cam_desc}"
    elif cam_pos or move:
        cam_txt = f"镜头：{cam_pos}{move}。"
        rule_generated.append(f"镜头={cam_pos}{move}")
    if cam_txt:
        main_txt = _append(main_txt, cam_txt)
        if cam_desc:
            rule_generated.append(f"镜头={cam_desc}")

    # ---- V1.0 空间连续性（#601 用户拍板）：180° 轴线/左右位置/视线轴 双语块 ----
    # 第一层硬约束防晕：双人对白 A 左 B 右一旦跳轴观众直接晕；block 由
    # spatial_continuity.SpatialState 生成（中文可读 + 英文保 H3 执行稳）。
    sp_block = str(((intent.get("continuity") or {}).get("spatial", {}) or {}).get("block") or "")
    if sp_block and sp_block not in main_txt:
        main_txt = _append(main_txt, sp_block)
        rule_generated.append("空间连续性")

    # ---- 生成约束层 v1.0（PROMPT_COMPILER_V1 §3.2，P1 接线）：双语约束块 ----
    # 位置：空间连续性块之后、光线之前（约束是画面硬边界，优先于装饰性描述）。
    # intent.constraint = ShotPlan.to_dict()（角色锁/空间锁/动作顺序/镜头锁/禁止），
    # 经 ShotPlan.from_dict 重建后 compile_constraint_block 渲染双语块；空块跳过。
    constraint = intent.get("constraint")
    if isinstance(constraint, dict) and constraint.get("character_count", 0) > 0:
        cblock = compile_constraint_block(ShotPlan.from_dict(constraint))
        if cblock and cblock not in main_txt:
            main_txt = _append(main_txt, cblock)
            rule_generated.append("约束块")

    # ---- 光线（来源：原文 lighting 或 AI 补全；未逐字出现才追加）----
    light = _strip_dlg(str(intent.get("lighting", "") or "").strip())
    if light and not _contains_noun(main_txt, light):
        # A1-4：字段可能自带句读，结尾已是句号/叹号/问号则不重复追加。
        if light[-1] not in "。？！":
            light += "。"
        main_txt = _append(main_txt, f"光线：{light}")
        src = intent.get("provenance", {}).get("lighting", IntentSource.MIX)
        (ai_supplement if src in (IntentSource.AI, IntentSource.MIX)
         else rule_generated).append(f"光线={light}")

    # ---- 环境元素（未逐字出现才追加；值统一剥离对白引号防泄漏）----
    env_missing = [v for v in (intent.get("environment") or [])
                   if _strip_dlg(str(v).strip()) and not _contains_noun(main_txt, _strip_dlg(str(v).strip()))]
    env_rendered = [_strip_dlg(str(v).strip()) for v in env_missing]
    if env_rendered:
        env_txt = "氛围：" + "、".join(env_rendered) + "。"
        main_txt = _append(main_txt, env_txt)
        rule_generated.append("环境=" + "、".join(env_rendered))

    # ---- 风格（P0-A #477：五区 style 覆盖；无覆盖为空不渲染；对白引号统一剥离）----
    style_txt = _strip_dlg(str(intent.get("style", "") or "").strip())
    if style_txt and not _contains_noun(main_txt, style_txt):
        # A1-4：五区 style 字段常自带句号，结尾已是句读则不重复追加。
        if style_txt[-1] not in "。？！":
            style_txt += "。"
        main_txt = _append(main_txt, f"风格：{style_txt}")
        rule_generated.append(f"风格={style_txt}")

    # ---- 对白块（官方 <d> 格式 + 说话人 ID；#545 根因修复 D，放描述区尾部）----
    dlg_block, dlg_prov = _build_dialogue_block(audio)
    if dlg_block:
        main_txt = _append(main_txt, dlg_block)
        user_facts.extend(dlg_prov["user_facts"])
        ai_supplement.extend(dlg_prov["ai_supplement"])
        rule_generated.extend(dlg_prov["rule_generated"])

    # ---- [REF] 参考图行（可读标签；真实绑定在 references）----
    if ref_line:
        main_txt = _append(main_txt, ref_line)

    # ---- v2.0 Visual Style：防崩坏双语约束（尾部；跟随 style_profile，None 跳过）----
    # 固定句 _ANTI_BREAK_RULE（全剧级一致）+ profile.negative_rules（合并去重：
    # 默认负面集「人物不变/人体结构/无畸变」已内化在固定句，逐字/主题重叠项跳过）。
    if style_profile is not None:
        anti = _ANTI_BREAK_RULE
        extra = _merge_negative_rules(style_profile)
        if extra:
            anti = anti + extra
        main_txt = _append(main_txt, anti)
        rule_generated.append("防崩坏约束")

    # ---- 无字幕约束（#597/#608，2026-08-17）：H3 对 <d> 块易渲染成画面字幕（不完整/错字，
    #      与 TTS 对白不同步）。字幕统一交后期层；对白块已带定点「仅声音」说明（#608），
    #      此处再追加双语强负向约束，双信号压住 H3 的画面文字渲染倾向。----
    main_txt = _append(main_txt, NO_SUBTITLE_RULE)
    rule_generated.append("无字幕约束")

    return head + main_txt, {
        "user_facts": user_facts,
        "ai_supplement": ai_supplement,
        "rule_generated": rule_generated,
    }


def _build_soundscape(intent: Dict[str, Any]) -> tuple[str, Dict[str, List[str]]]:
    """overall_soundscape：仅环境音（对白已进描述区 <d> 通道，#545 根因修复 D）。

    官方 H3 格式（base-en.txt §4.6）：对白/唱歌/剧情音乐属于 integrated_multimodal_description
    （用 <d>[Language] 原文</d> 表达），**绝不重复进 soundscape**。此处只写 1-4 句
    具体环境/动作/非语言人声；无规则环境音时兜底「环境音」。
    """
    audio = intent.get("audio") or {}
    ambient = str(audio.get("ambient", "") or "").strip()
    sounds: List[str] = []
    prov: Dict[str, List[str]] = {"user_facts": [], "ai_supplement": [], "rule_generated": []}
    if ambient:
        sounds.append(ambient)
        prov["rule_generated"].append(f"环境音={ambient}")
    if not sounds:
        sounds.append("环境音")
        prov["rule_generated"].append("环境音兜底")
    text = "，".join(sounds)
    if text and text[-1] not in "。？！":
        text += "。"
    return text, prov


def _build_music(intent: Dict[str, Any]) -> tuple[str, Dict[str, List[str]]]:
    """non_diegetic_music：规则音乐类型 + AI 情绪描述补充。"""
    audio = intent.get("audio") or {}
    music = str(audio.get("music", "") or "").strip() or "舒缓氛围配乐"
    prov: Dict[str, List[str]] = {"user_facts": [], "ai_supplement": [], "rule_generated": []}
    prov["rule_generated"].append(f"音乐={music}")
    emo = str(intent.get("emotion", "") or "").strip()
    if emo and not _too_similar(f"氛围{emo}", music):
        music = f"{music}，营造{emo}的氛围。"
        prov["ai_supplement"].append(f"情绪={emo}")
    else:
        music = f"{music}。"
    return music, prov


def build_h3_prompt(
    intent: Any,
    *,
    duration_sec: float = 5.0,
    bindings: Optional[Dict[str, Dict[str, str]]] = None,
    entity_key_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
    ref_prefix: str = "[REF: ",
    ref_suffix: str = "]",
    style_profile: Any = None,
    scene_name: Optional[str] = None,
) -> H3Prompt:
    """单镜 DirectorIntent → H3Prompt（项目 Schema minimax-h3-project-v1）。

    参数：
      intent            DirectorIntent（dataclass 或 to_dict() 产物）
      duration_sec      镜头时长（默认 5s，进 [0-5s] 时间轴）
      bindings          资产绑定 {entity_key: {asset_id, image_file, ref_image}}
      entity_key_fn      实体 dict → entity_key 匹配函数（默认 default_entity_key；
                         build_plan_h3_prompts 自动用 plan_entity_key 归一角色别名）
      ref_prefix/suffix 可读标签包裹（默认 [REF: 名]）
      style_profile     v2.0 VisualStyleProfile（dataclass/dict）；None 跳过画风块与
                        防崩坏句（旧链路输出不变）；非 None 注入全剧级画风块 + _ANTI_BREAK_RULE
      scene_name        画风块 per_scene 场景级微调（默认 None；build_plan_h3_prompts
                        自动传 intent.location）
    """
    d = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)
    bindings = bindings or {}
    key_fn = entity_key_fn or default_entity_key

    # ---- references：结构化绑定（不依赖文本顺序）----
    refs: List[Dict[str, str]] = []
    seen_keys: set[str] = set()
    ref_names: List[str] = []
    for e in d.get("entities") or []:
        name = str(e.get("name", "") or "")
        if not name:
            continue
        key = key_fn(e)
        if key in bindings and key not in seen_keys:
            seen_keys.add(key)
            b = bindings[key]
            refs.append({
                "entity_key": key,
                "entity_name": name,
                "asset_id": str(b.get("asset_id", "") or ""),
                "image_file": str(b.get("image_file", "") or ""),
                "ref_image": str(b.get("ref_image", "") or ""),
            })
            ref_names.append(name)

    ref_line = ""
    if ref_names:
        tags = " ".join(f"{ref_prefix}{n}{ref_suffix}" for n in ref_names)
        ref_line = f"参考图：{tags}"

    description, desc_prov = _build_description(
        d, duration_sec=duration_sec, ref_line=ref_line,
        style_profile=style_profile, scene_name=scene_name)
    soundscape, snd_prov = _build_soundscape(d)
    music, mus_prov = _build_music(d)

    return H3Prompt(
        shot_id=str(d.get("shot_id", "") or ""),
        scene_id=str(d.get("scene_id", "") or ""),
        duration_sec=float(duration_sec or 5.0),
        integrated_multimodal_description=description,
        overall_soundscape=soundscape,
        non_diegetic_music=music,
        references=refs,
        timeline=[{"start": 0.0, "end": float(duration_sec or 5.0),
                   "label": _time_label(duration_sec)}],
        provenance={
            "integrated_multimodal_description": desc_prov,
            "overall_soundscape": snd_prov,
            "non_diegetic_music": mus_prov,
        },
    )


def build_plan_h3_prompts(
    intent_map: Dict[str, Any],
    *,
    plan: Any = None,
    bindings_by_shot: Optional[Dict[str, Dict[str, Dict[str, str]]]] = None,
    duration_by_shot: Optional[Dict[str, float]] = None,
    style_profile: Any = None,
) -> Dict[str, H3Prompt]:
    """遍历 DirectorIntent 字典（{scene_id:shot_id: intent}）构建 H3Prompt。

    plan 提供角色锚点（_character_anchor_map）→ 实体 key 归一（柳姑娘→柳如烟），
    与资产绑定侧一致；bindings_by_shot 为 {scene_id:shot_id: {entity_key: {…}}}。
    style_profile（v2.0 VisualStyleProfile/dict）透传 build_h3_prompt；
    逐镜 scene_name 自动取 intent.location（画风块 per_scene 场景级微调）。
    返回与 intent_map 同 key 的 H3Prompt 字典。
    """
    char_anchors: Optional[Dict[str, str]] = None
    if plan is not None:
        try:
            from .entity_cleanse import _character_anchor_map  # noqa: PLC0415
            char_anchors = _character_anchor_map(plan)
        except Exception:  # pragma: no cover - 锚点失败则用默认 key
            char_anchors = None

    def _key_fn(e: Dict[str, Any]) -> str:
        return plan_entity_key(e, char_anchors)

    out: Dict[str, H3Prompt] = {}
    for key, intent in (intent_map or {}).items():
        b = (bindings_by_shot or {}).get(key, {})
        dur = (duration_by_shot or {}).get(key, 5.0)
        scene = intent.location if hasattr(intent, "location") else \
            (dict(intent).get("location") if isinstance(intent, dict) else None)
        out[key] = build_h3_prompt(
            intent,
            duration_sec=float(dur or 5.0),
            bindings=b,
            entity_key_fn=_key_fn,
            style_profile=style_profile,
            scene_name=str(scene or "") or None,
        )
    return out


__all__ = [
    "SCHEMA_VERSION",
    "H3Prompt",
    "build_h3_prompt",
    "build_plan_h3_prompts",
    "default_entity_key",
    "plan_entity_key",
]