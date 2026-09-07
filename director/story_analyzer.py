#!/usr/bin/env python3
"""P1-B · story_analyzer（自然语言小说 → Story Beat 剧情结构层）。

用户 2026-08-14 拍板（P1B_STORY_ANALYZER_PLAN.md）：
- 输入：一章小说自然语言正文（零标记，无「第X场/镜头一」）。
- B-1 部分（本文件）：**整章理解 → beats JSON 校验 → StoryBeat 归一化**。
- B-2 部分（本文件）：**Beat → Timeline（纯规则）**——动态数量约束（target 推荐区间 8-16）、
  超限合并不截断、覆盖缺口补齐、地点规范化、Beat 顺序权威的播放顺序（beat_timeline）。
- **不碰 Shot**（那是 P1-B-3 Shot Blueprint）。
- 铁律（全期不变）：
  1. Qwen 只做**剧情理解**（剧情结构），**不决定播放顺序**（timeline）、**不决定镜头边界**。
  2. 剧情结构优先：真实剧情转折就是 Beat 边界，不为凑数量硬拆（target 8-16 是推荐区间）。
  3. 覆盖底线：所有 Beat 的 text_segments 拼接必须完整覆盖小说正文（无遗漏、无重复、原文逐字）。
  4. 显存安全门：本模块只 `with backend.session()`，启动/卸载/GPU 全部交给 TextSession。
- 退化路径（用户拍板②）：Qwen 全挂 / analyze=False → 纯规则按段拆 Beat（source="rule"），
  不阻塞，但 warnings 明确「未经过 AI 剧情理解，需人工检查」。

运行方式（同其它后端测试，无第三方依赖、不连 Ollama）：
    python3 director/tests/test_story_analyzer.py
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, List, Optional, Tuple

from .bible import (
    BIBLE_ENTITY_CHARACTER,
    BIBLE_ENTITY_LOCATION,
    BIBLE_ENTITY_PROP,
    BIBLE_STATUS_CONFIRMED,
    StoryBible,
    confirmed_entries,
)
from .entity_cleanse import normalize_entity_name
from .production_plan import ProductionPlan, ProjectInfo, Scene, Shot, StoryBeat, Validation
from .script_parser import (
    _collect_candidate_names,
    _extract_characters,
    _extract_dialogues,
    _extract_dialogues_last,
    _extract_props,
    estimate_duration_sec,
)
from .text_backends import TextBackend

# ---------------------------------------------------------------------------
# 剧情功能（dramatic_function）取值集合
# ---------------------------------------------------------------------------
# 用户 2026-08-14 拍板：Beat 的 dramatic_function 是后续 Shot 导演调度（Shot Blueprint）
# 和运镜决策的输入。Schema 层 StoryBeat.dramatic_function 保持 str 宽松（未知值归一）。
DRAMATIC_FUNCTIONS: Dict[str, str] = {
    "introduce_character": "建立镜头：引入人物/地点",
    "dialogue": "对切/双人：对话",
    "plant_clue": "插入特写：埋线索（如剑匣）",
    "introduce_threat": "威胁建立：反派/危险出现",
    "confrontation": "正反打/推进：对峙冲突",
    "action": "动态运镜：动作",
    "reveal": "先隐藏后揭示：反转/揭秘",
    "emotional": "特写/慢推：情绪",
    "transition": "建立镜头：过渡转场",
}

# 整章小说正文进 prompt 的截断上限（防长章超 token；不影响落盘原文）
_SOURCE_TEXT_MAX = 12000
# 单条 text_segment 进 prompt 的截断上限
_SEGMENT_TEXT_MAX = 4000

# ---------------------------------------------------------------------------
# Qwen 整章理解 prompt（强 JSON；只做剧情理解，不改结构）
# ---------------------------------------------------------------------------
_STORY_ANALYZE_TEMPLATE = """你是专业小说→漫剧导演层的剧情结构分析助手。下面是一章小说的正文（自然语言，无任何剧本标记）。你的任务是理解剧情，把它拆成连续的 **Story Beat**（剧情节拍），并标注每个 Beat 的**剧情功能**（dramatic_function）。

小说标题：{title}

小说正文：
{story_text}

请只输出一个 JSON 对象，不要输出任何其他文字、代码块或注释。JSON 结构：
{{
  "beats": [
    {{
      "title": "节拍标题（一句话，如：沈青崖进入山雨楼）",
      "summary": "剧情摘要（2-3 句，讲清这一节发生了什么）",
      "dramatic_function": "introduce_character",
      "location": "主发生地点名",
      "time": "时间",
      "weather": "天气",
      "text_segments": ["本 Beat 覆盖的小说原文片段1", "原文片段2"],
      "characters": ["角色名"],
      "props": ["道具名"],
      "dialogue": ["本 Beat 出现的台词原文"],
      "emotion": "本 Beat 主导情绪",
      "transition_reason": "为什么在这里切换（导演理解，供人工审核参考，不决定播放顺序）"
    }}
  ]
}}

【铁律】
- **beats 覆盖全部正文**：所有 beats 的 text_segments 按顺序拼接必须完整覆盖小说正文（不得遗漏、不得重复、不得改写，逐字保留原文）。text_segments 必须是正文的逐字片段。
- **dramatic_function 只能是以下之一**（否则规则层会归一为 transition 并记录警告）：
  introduce_character（建立镜头：引入人物/地点）、dialogue（对话）、plant_clue（埋线索）、
  introduce_threat（威胁建立）、confrontation（对峙冲突）、action（动作）、
  reveal（先隐藏后揭示）、emotional（情绪）、transition（过渡转场）。
- **剧情结构优先**：真实剧情转折就是 Beat 边界；不为凑数量硬拆，不把多个完整节拍强行合并。
- **只读不创造**：location/characters/props/dialogue 只从正文提取；不要补充正文没有的内容。"""


# ---------------------------------------------------------------------------
# P2-P3（#540）：Global Story Bible 上下文注入
# ---------------------------------------------------------------------------
# 用户 2026-08-15 拍板（P2_GLOBAL_STORY_BIBLE_PLAN.md §7）：全量注入——已确认
# 人物/地点/道具身份 + 各自当前状态 + 最近剧情进展摘要。只注入**已确认**条目
# （confirmed_entries），候选条目不进上下文（AI 只提候选不覆盖已确认）。
_BIBLE_HISTORY_CAP = 20  # 已发生剧情注入条数 cap（防超 token，保最近）


def _bible_context_block(bible: Optional[StoryBible]) -> str:
    """渲染 Global Story Bible 全量上下文块。

    Args:
        bible: 项目 Bible；None 或无已确认条目 → 返回 ""（调用方据此不拼装，
            模板与 P1-B 逐字一致，向后兼容）。

    Returns:
        上下文块字符串（不含「小说正文：」标记）；空串表示无可注入内容。
    """
    if bible is None:
        return ""
    confirmed = confirmed_entries(bible)
    if not confirmed:
        return ""

    def _attr_line(e: BibleEntry) -> str:
        attr_str = "，".join(f"{k}={v}" for k, v in e.attributes.items())
        seg = f"{e.name} ({e.entity_id}) ✓"
        if attr_str:
            seg += f" {attr_str}"
        if e.current_status:
            seg += f" | 当前：{e.current_status}"
        if e.history:
            seg += f" | 最近：{e.history[-1]}"
        elif e.last_seen:
            seg += f" | 最近：{e.last_seen}"
        return seg

    char_lines = [_attr_line(e) for e in confirmed if e.entity_type == BIBLE_ENTITY_CHARACTER]
    loc_lines = [_attr_line(e) for e in confirmed if e.entity_type == BIBLE_ENTITY_LOCATION]
    prop_lines = [_attr_line(e) for e in confirmed if e.entity_type == BIBLE_ENTITY_PROP]

    # 已发生剧情：全部已确认条目的 history 保序去重，cap 最近 20 条
    history_lines: List[str] = []
    seen: set = set()
    for e in confirmed:
        for h in e.history:
            key = normalize_entity_name(h)
            if key and key not in seen:
                seen.add(key)
                history_lines.append(h)
    history_lines = history_lines[-_BIBLE_HISTORY_CAP:]

    lines = ["【跨章知识（Global Story Bible）】"]
    if char_lines:
        lines.append("人物：")
        lines.extend(f"- {l}" for l in char_lines)
    if loc_lines:
        lines.append("地点：")
        lines.extend(f"- {l}" for l in loc_lines)
    if prop_lines:
        lines.append("道具：")
        lines.extend(f"- {l}" for l in prop_lines)
    if history_lines:
        lines.append("已发生剧情：")
        lines.extend(f"- {l}" for l in history_lines)

    lines.append("")
    lines.append("【规则】以上为已确认/已沉淀的世界状态。分析本章时保持身份一致：")
    lines.append("- 命中上述人物/地点/道具的引用，用其稳定名，不另造新名。")
    lines.append("- 不重新介绍已确认身份；只在剧情有发展时更新状态。")
    lines.append("")
    return "\n".join(lines)


def _build_story_analyze_prompt(
    title: str,
    story_text: str,
    bible: Optional[StoryBible],
) -> str:
    """组装 Qwen 整章理解 prompt：bible 有已确认内容 → 正文前注入跨章上下文块。

    bible=None / 无已确认 → 返回与 P1-B 逐字一致的模板（向后兼容铁律）。
    """
    prompt = _STORY_ANALYZE_TEMPLATE.format(
        title=title or "未命名章节",
        story_text=story_text,
    )
    ctx = _bible_context_block(bible)
    if ctx:
        marker = "小说正文："
        idx = prompt.find(marker)
        if idx >= 0:
            prompt = prompt[:idx] + ctx + "\n" + prompt[idx:]
    return prompt


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

def _bounded_text(text: str, max_len: int = _SOURCE_TEXT_MAX) -> str:
    """超长文本截断（仅用于 prompt，不改变落盘原文）。"""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _clean_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v).strip()


def _clean_str_list(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        s = _clean_str(x)
        if s:
            out.append(s)
    return out


def validate_dramatic_function(value: Any) -> Tuple[str, bool]:
    """dramatic_function 校验。非法/未知 → ("transition", False)；合法 → (原值, True)。"""
    s = _clean_str(value)
    if s in DRAMATIC_FUNCTIONS:
        return s, True
    return "transition", False


# ---------------------------------------------------------------------------
# Qwen 输出归一化 + 覆盖校验
# ---------------------------------------------------------------------------

def _normalize_beats_json(
    data: Any, original_text: str, warnings: List[str]
) -> Tuple[List[StoryBeat], bool]:
    """Qwen beats JSON → StoryBeat 列表（强校验 + 归一化）。失败返回 ([] , True)。"""
    if not isinstance(data, dict):
        warnings.append("AI 返回不是 JSON 对象")
        return [], True
    raw = data.get("beats")
    if not isinstance(raw, list) or not raw:
        warnings.append("AI 返回 beats 为空或非法")
        return [], True

    beats: List[StoryBeat] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        func, ok = validate_dramatic_function(item.get("dramatic_function"))
        if not ok:
            warnings.append(f"beat {i + 1} dramatic_function 非法，已归一为 transition")
        beats.append(StoryBeat(
            beat_id=f"beat_{i + 1:02d}",
            title=_clean_str(item.get("title")),
            summary=_clean_str(item.get("summary")),
            dramatic_function=func,
            scene_id=_clean_str(item.get("location")),  # B-2 地点规范化后正式分配 scene_id
            time=_clean_str(item.get("time")),
            weather=_clean_str(item.get("weather")),
            text_segments=_clean_str_list(item.get("text_segments")),
            order=i + 1,
            source="ai",
            transition_reason=_clean_str(item.get("transition_reason")),
        ))

    if not beats:
        warnings.append("AI 返回 beats 全部非法")
        return [], True

    # 覆盖校验（用户锁死细节 3 的源头层）：拼接 text_segments 是否覆盖原文（去空白）。
    # 缺口只记 warning；规则补齐归 P1-B-2（本阶段不越界）。
    segments = [s for b in beats for s in b.text_segments]
    gap = _coverage_gap(original_text, segments)
    if gap:
        warnings.append(f"AI 理解存在覆盖缺口（约 {len(gap)} 字未覆盖），P1-B-2 将规则补齐")
    return beats, False


def _coverage_gap(original_text: str, segments: List[str]) -> str:
    """返回未覆盖原文片段（简单去空白包含检查）；完全覆盖返回空串。

    算法：原文去空白字符序列 src、拼接序列 joined；双指针贪心匹配 src 是否
    joined 的子序列。匹配不上的中段即缺口（返回前 60 字做提示）。
    注意：这只做「去空白后的包含/顺序」近似检查，正式覆盖链测死在
    test_story_analyzer_shots.py（P1-B-3）。
    """
    src = "".join((original_text or "").split())
    if not src:
        return ""
    joined = "".join("".join((s or "").split()) for s in segments)
    if joined and src in joined:
        return ""
    i = j = 0
    while i < len(src) and j < len(joined):
        if src[i] == joined[j]:
            i += 1
            j += 1
        else:
            j += 1
    if i < len(src):
        return src[i:i + 60]
    return src


# ---------------------------------------------------------------------------
# 退化路径（Qwen 全挂 / analyze=False）：纯规则按段拆 Beat
# ---------------------------------------------------------------------------

def _rule_dramatic_function(paragraph: str) -> str:
    """退化路径：段落内容启发式推断 dramatic_function（规则层，可复现）。"""
    table = [
        ("confrontation", ("对峙", "拔剑", "拔刀", "怒视", "冷笑", "剑拔弩张")),
        ("action", ("冲", "撞", "踢", "挥", "闪", "追", "逃", "破门", "扑")),
        ("introduce_threat", ("蒙面", "黑影", "杀气", "突然", "暗中", "潜伏")),
        ("plant_clue", ("剑匣", "信", "令牌", "地图", "钥匙", "包袱")),
        ("dialogue", ("说道", "问道", "开口", "低声道", "回答", "「")),
        ("emotional", ("泪", "颤抖", "叹息", "沉默", "心悸", "攥紧")),
        ("introduce_character", ("走进", "进入", "登场", "出现", "踏入")),
        ("reveal", ("发现", "真相", "竟", "原来", "揭开", "瞥见")),
    ]
    for func, kws in table:
        if any(k in paragraph for k in kws):
            return func
    return "transition"


def _rule_beat_title(paragraph: str) -> str:
    first = re.split(r"[。！？!?；;]", paragraph, maxsplit=1)[0].strip()
    return first[:24] or paragraph[:24]


def _rule_fallback_beats(text: str) -> List[StoryBeat]:
    """退化路径：整章按空行/段落拆 Beat（source="rule"），每段一个 Beat。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if not paragraphs:
        paragraphs = [(text or "").strip()] if (text or "").strip() else []
    beats: List[StoryBeat] = []
    for i, p in enumerate(paragraphs):
        beats.append(StoryBeat(
            beat_id=f"beat_{i + 1:02d}",
            title=_rule_beat_title(p),
            summary="",
            dramatic_function=_rule_dramatic_function(p),
            time="",
            weather="",
            text_segments=[p],
            order=i + 1,
            source="rule",
            transition_reason="",
        ))
    return beats


# ---------------------------------------------------------------------------
# P1-B-2 · Beat → Timeline（纯规则，可复现；Qwen 不决定播放顺序）
# ---------------------------------------------------------------------------
# 设计文档 §5/§7（用户 2026-08-14 拍板 + 锁死实现细节 1）：
#   - target = clamp(round(len/700), 8, 16) 是**推荐目标区间，不是硬性数量**。
#     剧情结构优先：真实节拍数 < 8 就保持原数，绝不为了凑 8 个硬拆。
#     8–16 只约束「超限合并」的上界。
#   - 超限合并不截断：优先合并过渡性/低信息 Beat；合并 = text_segments 拼接 +
#     更具体的 dramatic_function 保留；绝不丢弃任何 text_segment；合并记 warning。
#   - 覆盖底线：所有 Beat 的 text_segments 拼接必须覆盖原文；缺口规则补齐 + warning。
#   - Timeline = Beat 顺序权威（规则）；transition_reason 仅存元数据供人工审核。

BEAT_TARGET_MIN = 8
BEAT_TARGET_MAX = 16
BEAT_WORDS_PER_BEAT = 700
_LOCATION_ID_PREFIX = "location_"

# dramatic_function 优先级（合并时保留「更具体」的一个）：靠前 = 更具体。
# 设计文档 §5：confrontation > dialogue > action > plant_clue > transition …
_DRAMATIC_PRIORITY: List[str] = [
    "confrontation",
    "dialogue",
    "action",
    "reveal",
    "emotional",
    "introduce_threat",
    "plant_clue",
    "introduce_character",
    "transition",
]


def _dramatic_priority(func: str) -> int:
    """dramatic_function 优先级序号（越小越具体）。未知值 → 末尾（低优先级）。"""
    s = _clean_str(func)
    if s in _DRAMATIC_PRIORITY:
        return _DRAMATIC_PRIORITY.index(s)
    return len(_DRAMATIC_PRIORITY)


def target_beat_count(text: str) -> int:
    """推荐目标 Beat 数（推荐区间，非硬性）：clamp(round(len/700), 8, 16)。

    - 空文本 → 0。
    - 约 1000 字 → 8；5000 字 → 8；7000 字 → 10；11000 字 → 16；15000 字 → 16（封顶）。
    - 真实节拍数 < 8 → 保持真实节拍数（剧情结构优先，不凑数，见 _merge_beats_to_target）。
    """
    text = (text or "").strip()
    if not text:
        return 0
    return max(BEAT_TARGET_MIN, min(BEAT_TARGET_MAX, round(len(text) / BEAT_WORDS_PER_BEAT)))


def _low_info_score(beat: StoryBeat) -> int:
    """低信息度评分（越高越优先合并）：transition +2；短 summary（<8 字）+1。"""
    score = 0
    if beat.dramatic_function == "transition":
        score += 2
    if len(_clean_str(beat.summary)) < 8:
        score += 1
    return score


def _merge_adjacent_beats(a: StoryBeat, b: StoryBeat) -> StoryBeat:
    """相邻两 Beat 合并（§5 合并规则）：
    - text_segments 拼接（绝不丢弃原文）；entries 拼接；
    - title/summary 取信息量更大的一方；dramatic_function 取「更具体」（优先级更高）的一方；
    - scene_id/time/weather/source/transition_reason 保首（a 优先，空则取 b）。"""
    summary_a = _clean_str(a.summary)
    summary_b = _clean_str(b.summary)
    return StoryBeat(
        beat_id=a.beat_id,
        title=_clean_str(a.title) or _clean_str(b.title),
        summary=summary_a if len(summary_a) >= len(summary_b) else summary_b,
        dramatic_function=(
            a.dramatic_function
            if _dramatic_priority(a.dramatic_function) <= _dramatic_priority(b.dramatic_function)
            else b.dramatic_function
        ),
        scene_id=_clean_str(a.scene_id) or _clean_str(b.scene_id),
        time=_clean_str(a.time) or _clean_str(b.time),
        weather=_clean_str(a.weather) or _clean_str(b.weather),
        text_segments=list(a.text_segments) + list(b.text_segments),
        order=a.order,
        entries=list(a.entries) + list(b.entries),
        source=_clean_str(a.source) or _clean_str(b.source) or "ai",
        transition_reason=_clean_str(a.transition_reason) or _clean_str(b.transition_reason),
    )


def _merge_beats_to_target(
    beats: List[StoryBeat],
    target: int,
    warnings: Optional[List[str]] = None,
) -> List[StoryBeat]:
    """§5 超限合并（不截断剧情）：len(beats) > target 时合并相邻 Beat，直到不超。

    - len(beats) <= target → 原样返回（不凑数、不硬拆，拍板①锁死）。
    - 合并优先对：低信息（transition / 短 summary）相邻对 > 同 scene_id 相邻对 > 拼接最短对。
    - 每次合并写 warning；合并后仍超 target → 继续合并。
    - 绝不丢弃任何 text_segment（覆盖底线不破）。
    """
    if warnings is None:
        warnings = []
    result = [dataclasses.replace(b) for b in beats]  # 不改入参
    if target <= 0 or len(result) <= target:
        return result

    def _pair_key(i: int) -> Tuple[int, int, int, int]:
        """排序键（越大越优先合并）：(低信息分, 同地点, -拼接长, 更靠后)"""
        a, b = result[i], result[i + 1]
        low = _low_info_score(a) + _low_info_score(b)
        same_loc = 1 if (a.scene_id and a.scene_id == b.scene_id) else 0
        total_len = sum(len(_clean_str(s)) for s in a.text_segments + b.text_segments)
        return (low, same_loc, -total_len, i)

    while len(result) > target:
        if len(result) < 2:
            break
        best_i = max(range(len(result) - 1), key=_pair_key)
        a, b = result[best_i], result[best_i + 1]
        reason = _merge_reason(a, b)
        merged = _merge_adjacent_beats(a, b)
        result[best_i:best_i + 2] = [merged]
        warnings.append(f"Beat {a.beat_id}/{b.beat_id} 合并：{reason}")
    return result


def _merge_reason(a: StoryBeat, b: StoryBeat) -> str:
    """合并原因描述（供 warning 展示）。"""
    if _low_info_score(a) + _low_info_score(b) > 0:
        if a.dramatic_function == "transition" or b.dramatic_function == "transition":
            return "连续过渡/低信息节拍过碎"
        return "summary 过短、信息量低"
    if a.scene_id and a.scene_id == b.scene_id:
        return "同一地点连续节拍"
    return "节拍过密（超过目标数量），相邻合并不截断剧情"


def _split_paragraphs(text: str) -> List[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if not paragraphs and (text or "").strip():
        paragraphs = [(text or "").strip()]
    return paragraphs


def _core(s: str) -> str:
    """去空白核心串（覆盖判断用）。"""
    return "".join((s or "").split())


def _fill_coverage_gaps(
    text: str,
    beats: List[StoryBeat],
    warnings: Optional[List[str]] = None,
) -> List[StoryBeat]:
    """覆盖补齐：未被任何 Beat 覆盖的段落（原文逐字）追加到「最近的 Beat」。

    - 完整覆盖 → 原样返回。
    - 缺口段落（去空白后不被任何 text_segment 包含）按原文顺序追加到
      它前面最近被覆盖段落所属的 Beat（首段缺口 → 追加到第 0 个 Beat）。
    - 缺口 + warning（规则补齐，人工可在审核阶段调整）。
    - 空 beats → 原样返回（补齐无对象，extract_beats 保证正常路径非空）。
    """
    if warnings is None:
        warnings = []
    if not beats:
        return list(beats)
    # ⚠ 必须深拷贝 text_segments：下方 append 缺口段落，浅拷贝会污染入参 beats（process_beats 承诺不改入参）
    out = [dataclasses.replace(b, text_segments=list(b.text_segments)) for b in beats]
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return out

    n = len(paragraphs)
    covered = [False] * n
    owner = [0] * n
    last_covered_beat = 0
    for i, para in enumerate(paragraphs):
        pc = _core(para)
        if not pc:
            owner[i] = last_covered_beat
            continue
        found = None
        for bi, b in enumerate(out):
            if any(pc in _core(s) for s in b.text_segments):
                found = bi
                break
        if found is None:
            owner[i] = last_covered_beat  # 缺口段：挂到最近已覆盖段落的 Beat
        else:
            owner[i] = found
            last_covered_beat = found
            covered[i] = True

    gaps = [i for i in range(n) if paragraphs[i] and not covered[i]]
    if not gaps:
        return out
    if warnings is not None:
        warnings.append(f"覆盖检查发现 {len(gaps)} 个缺口段落，已规则补齐到最近 Beat，需人工检查")
    for i in gaps:
        out[owner[i]].text_segments.append(paragraphs[i])
    return out


def _normalize_location_ids(
    beats: List[StoryBeat],
) -> Tuple[List[StoryBeat], Dict[str, str]]:
    """地点规范化（P1 决策④ + §7）：同名（归一后）合并为同一 scene_id。

    - scene_id 首现分配 `location_XX`（= locationId 兼容）；同名复用同一 id。
    - 归一用 entity_cleanse.normalize_entity_name（去空白 + 小写，已验收纯规则）。
    - 返回 (beats, loc_map)，loc_map: scene_id -> 规范地名（供审核展示 / B-3 建 Scene）。
    - 无地点名的 Beat → scene_id=""（B-3 由 shot 文本 / 上一镜继承兜底）。
    """
    out: List[StoryBeat] = []
    loc_map: Dict[str, str] = {}
    name_to_id: Dict[str, str] = {}
    counter = 1
    for b in beats:
        name = _clean_str(b.scene_id)
        key = normalize_entity_name(name) if name else ""
        if key and key in name_to_id:
            sid = name_to_id[key]
        elif key:
            sid = f"{_LOCATION_ID_PREFIX}{counter:02d}"
            counter += 1
            name_to_id[key] = sid
            loc_map[sid] = name
        else:
            sid = ""
        out.append(dataclasses.replace(b, scene_id=sid))
    return out, loc_map


def beat_timeline(beats: List[StoryBeat]) -> List[str]:
    """Beat 顺序权威的播放顺序（§4 ④，规则）：按 order 升序。

    条目格式 `"{scene_id}:{beat_id}"`——B-2 阶段 Shot 未拆，用 beat_id 占位；
    P1-B-3 拆 Shot 后由 build_plan_from_beats 展开为真正的 `"{scene_id}:{shot_id}"`。
    """
    ordered = sorted(beats, key=lambda b: (b.order, b.beat_id))
    return [f"{b.scene_id}:{b.beat_id}" for b in ordered]


def process_beats(
    text: str,
    beats_meta: List[StoryBeat],
    warnings: Optional[List[str]] = None,
) -> Tuple[List[StoryBeat], Dict[str, str]]:
    """P1-B-2 编排（纯规则）：超限合并 → 覆盖补齐 → 地点规范化。

    返回 (beats, loc_map)。B-3 在其上继续拆 Shot 并生成完整 ProductionPlan。
    不改变 beats_meta 入参。
    """
    if warnings is None:
        warnings = []
    work = [dataclasses.replace(b) for b in beats_meta]
    target = target_beat_count(text)
    if len(work) > target:
        work = _merge_beats_to_target(work, target, warnings)
    work = _fill_coverage_gaps(text, work, warnings)
    return _normalize_location_ids(work)


# ---------------------------------------------------------------------------
# P1-B-3 · Beat → Shot（Shot Blueprint 导演规则，纯规则，零 LLM 零 GPU）
# ---------------------------------------------------------------------------
# 设计文档 §6/§7（用户 2026-08-14 拍板 + 锁死实现细节 2）：
#   - Shot Blueprint = **候选骨架/最小导演模板**，按 Beat 内容特征（§6.2）决定
#     启用哪些模板，不做无条件整套生成，**不是一句话一个镜头**。
#   - 原文属于 Beat（Beat.text_segments）；Shot 是对 Beat 原文的**导演切分引用**。
#     覆盖链（§6.5，测死在 test_story_analyzer_shots.py）：
#     所有 Shot.source_text 拼接（保序去重）→ 完整覆盖 Beat.text_segments 拼接
#     → 完整覆盖小说正文（无遗漏、无重复、原文逐字）。
#   - 每 Shot 独立 Location（§6.3）：1) shot 文本首个已知地点 → 2) 继承上一 shot
#     → 3) 首镜无地点词继承 beat.scene_id；同名合并、绝不覆盖已确认 Location。
#   - 导演意图（景别/运镜/作用）写入 shot.visual_intent（P1-B-4 的
#     build_shot_intent 会把 visual_intent 投影为 DirectorIntent.composition，天然贯通）。

# 可复用模板常量（§6.1 模板表里多次出现的定位）
_ESTABLISHING_TEMPLATE: Dict[str, str] = {
    "shot_type": "establishing", "camera_position": "远景", "movement": "慢推",
    "intent": "建立环境与氛围",
}
_INSERT_TEMPLATE: Dict[str, str] = {
    "shot_type": "insert", "camera_position": "特写", "movement": "慢推",
    "intent": "线索道具特写",
}
_CLOSEUP_TEMPLATE: Dict[str, str] = {
    "shot_type": "closeup", "camera_position": "特写", "movement": "慢推",
    "intent": "情绪特写",
}
_REACTION_TEMPLATE: Dict[str, str] = {
    "shot_type": "reaction", "camera_position": "中景", "movement": "拉远",
    "intent": "反应收束",
}

# §6.1 Shot Blueprint 模板表：dramatic_function → 导演候选镜头序列。
# 每个模板：shot_type（镜头类型）/ camera_position（景别）/ movement（运镜）/ intent（作用）。
# optional=True 的模板默认不启用（除非 has_prop_clue 特征触发）。
SHOT_BLUEPRINTS: Dict[str, List[Dict[str, str]]] = {
    "introduce_character": [
        {"shot_type": "establishing", "camera_position": "远景", "movement": "慢推", "intent": "建立环境与人物登场氛围"},
        {"shot_type": "wide", "camera_position": "中景", "movement": "跟移", "intent": "人物走入画面"},
        {"shot_type": "medium", "camera_position": "近景", "movement": "跟拍", "intent": "人物动作进入"},
        {"shot_type": "pov", "camera_position": "主观", "movement": "环视", "intent": "人物视角观察环境"},
    ],
    "dialogue": [
        {"shot_type": "two_shot", "camera_position": "中景", "movement": "固定", "intent": "双人同框对话"},
        {"shot_type": "closeup", "camera_position": "近景", "movement": "正打", "intent": "说话者 A"},
        {"shot_type": "closeup", "camera_position": "近景", "movement": "反打", "intent": "说话者 B"},
        {"shot_type": "insert", "camera_position": "特写", "movement": "固定", "intent": "道具/反应特写", "optional": True},
    ],
    "plant_clue": [
        {"shot_type": "establishing", "camera_position": "中景", "movement": "固定", "intent": "建立环境"},
        {"shot_type": "insert", "camera_position": "特写", "movement": "慢推", "intent": "揭示线索道具"},
    ],
    "introduce_threat": [
        {"shot_type": "wide", "camera_position": "远景", "movement": "固定", "intent": "暗处威胁出现"},
        {"shot_type": "tracking", "camera_position": "中景", "movement": "跟移", "intent": "威胁逼近"},
        {"shot_type": "pov", "camera_position": "主观", "movement": "聚焦", "intent": "目标视角聚焦威胁"},
    ],
    "confrontation": [
        {"shot_type": "wide", "camera_position": "全景", "movement": "推进", "intent": "对峙格局建立"},
        {"shot_type": "medium", "camera_position": "中景", "movement": "正反打", "intent": "双方交锋"},
        {"shot_type": "closeup", "camera_position": "特写", "movement": "固定", "intent": "剑柄/眼神细节"},
        {"shot_type": "reaction", "camera_position": "中景", "movement": "拉远", "intent": "反应收束"},
    ],
    "action": [
        {"shot_type": "wide", "camera_position": "全景", "movement": "甩镜", "intent": "动作全貌"},
        {"shot_type": "medium", "camera_position": "中景", "movement": "跟拍", "intent": "动态追逐"},
        {"shot_type": "fast_zoom", "camera_position": "特写", "movement": "推进", "intent": "关键瞬间"},
    ],
    "reveal": [
        {"shot_type": "medium", "camera_position": "中景", "movement": "先遮挡", "intent": "隐藏真相"},
        {"shot_type": "push", "camera_position": "特写", "movement": "推进", "intent": "揭示真相"},
        {"shot_type": "reaction", "camera_position": "特写", "movement": "固定", "intent": "反应镜头"},
    ],
    "emotional": [
        {"shot_type": "closeup", "camera_position": "特写", "movement": "慢推", "intent": "情绪特写"},
        {"shot_type": "extreme_closeup", "camera_position": "大特写", "movement": "静", "intent": "情绪峰值"},
    ],
    "transition": [
        {"shot_type": "establishing", "camera_position": "远景", "movement": "拉远", "intent": "过渡转场"},
    ],
}

# §6.2 特征关键词（与 B-1 _rule_dramatic_function 表同源，可复现）
_ACTION_KEYWORDS = ("冲", "撞", "踢", "挥", "闪", "追", "逃", "扑", "拔剑", "拔刀", "破门", "追向")
_CLUE_KEYWORDS = ("剑匣", "信", "令牌", "地图", "钥匙", "包袱", "密函")
_EMOTION_KEYWORDS = ("泪", "颤抖", "叹息", "沉默", "心悸", "攥紧", "怒", "惊", "悲", "喜")


def _shot_features(beat: StoryBeat, loc_map: Dict[str, str]) -> Dict[str, Any]:
    """§6.2 特征提取：决定启用哪些 Shot Blueprint 模板子集。"""
    text = "".join(_clean_str(s) for s in beat.text_segments)
    core = normalize_entity_name(text)
    appear = _count_locations_in_text(core, loc_map)
    return {
        "func": beat.dramatic_function,
        "len": len(text),
        "short": len(text) < 60,
        "has_dialogue": "：" in text or "「" in text,
        "has_action": any(k in text for k in _ACTION_KEYWORDS),
        "has_prop_clue": any(k in text for k in _CLUE_KEYWORDS),
        "location_change": appear >= 2,
        "emotion_shift": any(k in text for k in _EMOTION_KEYWORDS),
    }


def _count_locations_in_text(core_text: str, loc_map: Dict[str, str]) -> int:
    """core_text 里出现的**不同**已知地点数（长名优先，匹配后占位防子串重复计数）。"""
    if not loc_map or not core_text:
        return 0
    seen: List[str] = []
    remaining = core_text
    for norm, sid in _location_scan_table(loc_map):
        if norm and norm in remaining:
            if sid not in seen:
                seen.append(sid)
            remaining = remaining.replace(norm, "□", 1)
    return len(seen)


def _location_scan_table(loc_map: Dict[str, str]) -> List[Tuple[str, str]]:
    """loc_map → [(归一化地点名, scene_id)]，长名优先（先匹配长名避免子串误配）。"""
    table: List[Tuple[str, str]] = []
    for sid, name in loc_map.items():
        norm = normalize_entity_name(name)
        if norm:
            table.append((norm, sid))
    table.sort(key=lambda x: len(x[0]), reverse=True)
    return table


def _select_template_subsets(
    blueprint: List[Dict[str, str]], features: Dict[str, Any]
) -> List[Dict[str, str]]:
    """§6.2 特征启用：按 Beat 内容特征决定启用哪些模板（候选骨架，非无条件整套）。

    规则（可复现，测试锁死）：
    - optional 模板默认去掉，除非 has_prop_clue（线索特写）。
    - dialogue 但无对白 → 降级为「建立 + 反应」（不硬套正反打）。
    - 短 Beat（len<60）→ 只启用前 1-2 个模板（≥40 字 2 个，否则 1 个）。
    - has_prop_clue → 追加 insert 特写。
    - location_change → 前置 establishing。
    - emotion_shift → 追加 closeup 慢推。
    """
    out = [dict(t) for t in blueprint]
    if not out:
        return out
    # 1) optional 模板：默认去掉
    if not features.get("has_prop_clue"):
        out = [t for t in out if not t.get("optional")]
    # 2) dialogue 无对白 → 降级
    if features.get("func") == "dialogue" and not features.get("has_dialogue"):
        out = [t for t in out if t["shot_type"] not in ("two_shot", "closeup")]
        if not any(t["shot_type"] == "reaction" for t in out):
            out.append(dict(_REACTION_TEMPLATE))
        if not any(t["shot_type"] == "establishing" for t in out):
            out.insert(0, dict(_ESTABLISHING_TEMPLATE))
    # 3) 短 Beat → 只保留前 1-2 个
    if features.get("short"):
        keep = 2 if features.get("len", 0) >= 40 else 1
        out = out[:keep]
    # 4) 有线索 → 追加 insert
    if features.get("has_prop_clue") and not any(t["shot_type"] == "insert" for t in out):
        out.append(dict(_INSERT_TEMPLATE))
    # 5) 地点变化 → 前置 establishing
    if features.get("location_change") and not any(t["shot_type"] == "establishing" for t in out):
        out.insert(0, dict(_ESTABLISHING_TEMPLATE))
    # 6) 情绪转折 → 追加 closeup 慢推
    if features.get("emotion_shift") and not any(
        t["shot_type"] in ("closeup", "extreme_closeup") for t in out
    ):
        out.append(dict(_CLOSEUP_TEMPLATE))
    return out


_QUOTE_CLOSE_MAP_LOCAL = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"'}


def _split_sentences_quote_aware(text: str) -> List[str]:
    """按句子边界切分，但引号内（“”/「」/‘’/""）的句末标点不当作切分点。

    #580：防止对白残句被切进不同镜头（此前 shot_05 尾部「撑不到！」、
    shot_06 开头「你站那么高...吗？”」的引号跨镜头现象）。嵌套异号引号
    （「…『…』…」）按最近闭合追踪，可正确处理。
    剧本格式兼容（#580 回归）：引号前紧邻冒号（X说：「…」）说明这是完整
    对白句，闭合后即使后文直接接下一句说话（无句末标点），也在闭引号处
    切分 —— 保持「一句对白 = 一个切分单元」。
    """
    parts: List[str] = []
    cur = ""
    open_q: Optional[str] = None
    q_prev_colon = False
    prev_char = ""
    for ch in text:
        cur += ch
        if open_q is not None:
            if ch == _QUOTE_CLOSE_MAP_LOCAL[open_q]:
                if q_prev_colon and cur:
                    parts.append(cur)
                    cur = ""
                open_q = None
            prev_char = ch
            continue
        if ch in _QUOTE_CLOSE_MAP_LOCAL:
            # ⛔ 坑：`"" in "：:"` 恒为 True（空串是任意串子串），文本以引号开头时
            # prev_char="" 会把 q_prev_colon 误判 True → 首对引号被错误切分（#581）。
            q_prev_colon = bool(prev_char) and prev_char in "：:"
            open_q = ch
        elif ch in "。！？!?；;":
            parts.append(cur)
            cur = ""
        prev_char = ch
    if cur:
        parts.append(cur)
    return parts


def _split_segments_into_chunks(segments: List[str], n: int) -> List[str]:
    """把 Beat 的原文片段按 n 份连续切分（保序，优先句子边界，不硬拆句）。

    #580：quote-aware 切分 —— 引号内句号不切，保证对白整体落进同一镜头。
    - 返回**非空** chunks（保序），拼接 = 原文（覆盖链保证，见 §6.5）。
    - 实际 chunk 数 ≤ n：句子少时不硬凑镜头（也不逐句切镜）。
    """
    text = "".join(_clean_str(s) for s in segments)
    if not text.strip():
        return []
    if n <= 1:
        return [text]
    parts = [p for p in _split_sentences_quote_aware(text) if p.strip()]
    if not parts:
        return [text]
    n = max(1, min(n, len(parts)))  # 镜头数不超过句子数
    if n == 1:
        return [text]
    total = sum(len(p) for p in parts)
    target = total / n
    groups: List[str] = []
    cur = ""
    for p in parts:
        if len(groups) == n - 1:
            cur += p
        elif cur and len(cur) + len(p) > target and len(groups) < n - 1:
            groups.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        groups.append(cur)
    return [g.strip() for g in groups if g.strip()]


def _first_location_in_text(text: str, loc_names_sorted: List[Tuple[str, str]]) -> Optional[str]:
    """shot 文本里最早出现的已知地点（长名优先，位置最靠前者）。返回 scene_id。"""
    core = normalize_entity_name(text)
    if not core or not loc_names_sorted:
        return None
    best: Optional[str] = None
    best_pos = len(core) + 1
    for name, sid in loc_names_sorted:
        pos = core.find(name)
        if pos != -1 and pos < best_pos:
            best_pos = pos
            best = sid
    return best


def resolve_shot_location(
    shot_text: str,
    beat: StoryBeat,
    loc_names_sorted: List[Tuple[str, str]],
    prev_shot_loc: Optional[str] = None,
) -> str:
    """§6.3 Shot 独立 Location（三步兜底，纯规则）。

    1) shot 文本首个已知地点 → 用它（Beat 内跨地点交叉剪辑）；
    2) 否则继承上一 shot 的地点（同 Beat 连续镜头默认同地点）；
    3) 本 Beat 首镜无地点词 → 继承 beat.scene_id。
    返回值为规范化 scene_id（loc_names_sorted 已带 scene_id）。
    """
    found = _first_location_in_text(shot_text, loc_names_sorted)
    if found is not None:
        return found
    if prev_shot_loc:
        return prev_shot_loc
    return beat.scene_id


def _build_shot(
    chunk: str,
    template: Dict[str, str],
    shot_seq: int,
    candidates: List[str],
    prev_speaker: str = "",
) -> Shot:
    """由原文 chunk + 导演模板构造 Shot（候选骨架；B-4 再补全 DirectorIntent）。

    导演意图写入 visual_intent（B-4 的 build_shot_intent 会把它投影为
    DirectorIntent.composition，作为 AI 构图建议候选）。
    prev_speaker：#580 跨镜头说话人继承（引号对白无署名时沿用上一镜说话人）。
    """
    lines = [chunk]
    shot_type = _clean_str(template.get("shot_type"))
    cam_pos = _clean_str(template.get("camera_position"))
    movement = _clean_str(template.get("movement"))
    intent = _clean_str(template.get("intent"))
    visual_intent = f"镜头类型={shot_type}；景别={cam_pos}；运镜={movement}；作用={intent}"
    # v2.0 P2 心理词预标记：分析阶段扫 source_text → Shot.psych_tags 硬输入持久化，
    # psych_visualize 优先消费（软约束转硬输入）。零 LLM 纯规则。
    psych_tags: List[str] = []
    try:
        from .psych_visualize import tag_psych_text  # noqa: PLC0415
        psych_tags = tag_psych_text(chunk)
    except Exception:  # pragma: no cover
        psych_tags = []
    return Shot(
        shot_id=f"shot_{shot_seq:02d}",
        source_text=chunk,
        duration_sec=estimate_duration_sec(chunk),
        characters=_extract_characters(lines, candidates),
        props=_extract_props(lines),
        actions=[],  # B-4 build_shot_intent 从原文重建，此处保持骨架
        emotion="",  # B-4 补全
        dialogue=_extract_dialogues(lines, candidates, prev_speaker=prev_speaker),
        visual_intent=visual_intent,
        psych_tags=psych_tags,
    )


def shot_coverage_check(plan: ProductionPlan) -> List[str]:
    """§6.5 覆盖链校验（纯规则）：所有 Shot.source_text 按 **timeline 播放顺序** 拼接
    （保序去重）必须完整覆盖 Beat.text_segments 拼接（无遗漏、无重复、原文逐字）。

    以 timeline 为顺序权威（P0 Story Timeline）：跨地点交叉剪辑时 scene 分组
    不等于播放顺序，按 scene 展平会产生误报。返回缺口 warning 列表；完全覆盖返回 []。
    """
    by_id = {sh.shot_id: sh for s in plan.scenes for sh in s.shots}
    parts = []
    for e in plan.timeline:
        sh = by_id.get(e.split(":", 1)[1])
        if sh is not None:
            parts.append(_core(sh.source_text))
    shot_flat = "".join(parts)
    beat_flat = "".join(_core(seg) for b in plan.beats for seg in b.text_segments)
    warnings: List[str] = []
    if beat_flat and shot_flat != beat_flat:
        warnings.append("Shot 覆盖链：Shot 原文拼接未完整还原 Beat 原文，需人工检查")
    return warnings


# §7 build_plan_from_beats：Story Beat → 完整 ProductionPlan（scenes/timeline/beats）
def build_plan_from_beats(
    text: str,
    beats_meta: List[StoryBeat],
    warnings: Optional[List[str]] = None,
    *,
    title: str = "",
) -> ProductionPlan:
    """编排（§7）：Beat → Shot（§6 Blueprint）→ Shot 独立 Location（§6.3）→ Scene 聚合
    → timeline（逐 Beat 逐 shot）→ beats.entries → 覆盖校验（§6.5）。

    复用 process_beats（合并→补齐→地点规范化），**不改 beats_meta 入参**。
    纯规则零 LLM 零 GPU，可直接后端路由调用。
    """
    if warnings is None:
        warnings = []
    beats, loc_map = process_beats(text, beats_meta, warnings)
    plan = ProductionPlan(
        project=ProjectInfo(title=title),
        scenes=[],
        validation=Validation(status="pending", warnings=list(warnings)),
    )
    if not beats:
        return plan

    # 小说正文未提地点 → 兜底「未命名地点」（绝不污染已确认 Location）
    fallback_scene = f"{_LOCATION_ID_PREFIX}00"
    if any(not b.scene_id for b in beats):
        loc_map.setdefault(fallback_scene, "未命名地点")
    loc_scan = _location_scan_table(loc_map)

    scene_shots: Dict[str, List[Shot]] = {}
    scene_meta: Dict[str, Tuple[str, str]] = {}  # scene_id -> (time, weather)
    timeline: List[str] = []
    beat_records: List[StoryBeat] = []
    global_seq = 0
    last_speaker = ""  # #580 跨 Beat/跨镜头说话人继承（跳过「系统」，见 _extract_dialogues_last）

    for b in beats:  # process_beats 保持原顺序 → timeline 即 Beat 顺序权威
        features = _shot_features(b, loc_map)
        blueprint = SHOT_BLUEPRINTS.get(
            b.dramatic_function, SHOT_BLUEPRINTS["transition"]
        )
        templates = _select_template_subsets(blueprint, features)
        chunks = _split_segments_into_chunks(b.text_segments, len(templates))
        if not chunks:
            continue
        candidates = _collect_candidate_names(b.text_segments)
        prev_shot_loc: Optional[str] = None
        entries: List[str] = []
        for i, chunk in enumerate(chunks):
            global_seq += 1
            template = templates[i] if i < len(templates) else templates[-1]
            shot = _build_shot(chunk, template, global_seq, candidates, last_speaker)
            last_speaker = _extract_dialogues_last([chunk], last_speaker)
            sid = resolve_shot_location(chunk, b, loc_scan, prev_shot_loc)
            prev_shot_loc = sid
            if not sid:
                sid = fallback_scene
            scene_shots.setdefault(sid, []).append(shot)
            scene_meta.setdefault(sid, (_clean_str(b.time), _clean_str(b.weather)))
            entries.append(f"{sid}:{shot.shot_id}")
            timeline.append(f"{sid}:{shot.shot_id}")
        beat_records.append(dataclasses.replace(b, entries=list(entries)))

    plan.scenes = [
        Scene(
            scene_id=sid,
            title=loc_map.get(sid, sid),
            location_name=loc_map.get(sid, sid),
            time=scene_meta.get(sid, ("", ""))[0],
            weather=scene_meta.get(sid, ("", ""))[1],
            shots=shots,
        )
        for sid, shots in scene_shots.items()
    ]
    plan.timeline = timeline
    plan.beats = beat_records
    plan.validate()  # 结构校验（覆盖 self.validation）
    cov = shot_coverage_check(plan)
    # validate() 已覆盖 self.validation，此处无条件合并导入 warnings + 覆盖链校验
    plan.validation.warnings = list(warnings) + cov + plan.validation.warnings
    return plan


# ---------------------------------------------------------------------------
# 顶层入口（B-1：整章理解 → StoryBeat 列表）
# ---------------------------------------------------------------------------

def extract_beats(
    text: str,
    backend: Optional[TextBackend] = None,
    *,
    title: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    bible: Optional[StoryBible] = None,
) -> Tuple[List[StoryBeat], bool, List[str]]:
    """整章小说理解 → StoryBeat 列表（B-1；不碰 Shot、不做数量约束/合并）。

    P2-P3（#540）：`bible` 非空且有已确认条目 → 在正文前注入 Global Story Bible
    全量上下文块（已确认身份 + 当前状态 + 最近剧情），帮 Qwen 保持跨章身份一致；
    bible=None / 无已确认 → 模板与 P1-B 逐字一致（向后兼容铁律）。

    Returns:
        (beats, rule_only, warnings)
        - beats：StoryBeat 列表。Qwen 提议时 source="ai"；退化时 source="rule"。
        - rule_only：True 表示未经 AI 剧情理解（Qwen 不可用/失败），结果需人工检查。
        - warnings：可展示警告（Qwen 失败、JSON 非法、覆盖缺口、dramatic_function 归一）。
    """
    warnings: List[str] = []
    text = (text or "").strip()
    if not text:
        return [], True, ["小说正文为空"]

    if backend is not None:
        try:
            with backend.session() as llm:
                data = llm.analyze_json(
                    _build_story_analyze_prompt(
                        title=title,
                        story_text=_bounded_text(text),
                        bible=bible,
                    ),
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            beats, failed = _normalize_beats_json(data, text, warnings)
            if not failed and beats:
                return beats, False, warnings
            warnings.append("AI 剧情理解未返回合法 Story Beat，走规则退化")
        except Exception as exc:
            warnings.append(f"AI 剧情理解调用失败: {exc}")

    # 退化路径（用户拍板②）：Qwen 全挂 / analyze=False → 纯规则，不阻塞
    warnings.append("当前结果未经过 AI 剧情理解，由规则生成，需人工检查。")
    return _rule_fallback_beats(text), True, warnings


__all__ = [
    "DRAMATIC_FUNCTIONS",
    "StoryBeat",
    "ProductionPlan",
    "BEAT_TARGET_MIN",
    "BEAT_TARGET_MAX",
    "BEAT_WORDS_PER_BEAT",
    "extract_beats",
    "validate_dramatic_function",
    "target_beat_count",
    "process_beats",
    "beat_timeline",
    "_coverage_gap",
    "_rule_fallback_beats",
    "_merge_beats_to_target",
    "_fill_coverage_gaps",
    "_normalize_location_ids",
    "_dramatic_priority",
    # P1-B-3 Beat → Shot
    "SHOT_BLUEPRINTS",
    "_shot_features",
    "_select_template_subsets",
    "_split_segments_into_chunks",
    "resolve_shot_location",
    "shot_coverage_check",
    "build_plan_from_beats",
    # P2-P3（#540）Global Story Bible 上下文注入
    "_BIBLE_HISTORY_CAP",
    "_bible_context_block",
    "_build_story_analyze_prompt",
]
