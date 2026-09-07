#!/usr/bin/env python3
"""V1.7 Phase 1 · script_parser（纯规则拆 Scene/Shot，不碰 Qwen）。

职责（V17_PLAN §4 / P0-A，用户 2026-08-11 拍板）：
1. 读取 .md / .txt 剧本（UTF-8 → GB18030 容错；Phase 1 不支持 .docx，见 §15/拍板⑩）。
2. Scene 切分（规则确定，尽量少切）：
   a. markdown 标题（# / ## / ###…）；
   b. 中文场景序号行（「第X场/景/幕」）；
   c. slugline（内|外|室内|室外 地点 时间）；
   d. 「时间：/地点：/天气：」标签行（不新建场景，只更新当前场景字段）；
   e. 兜底：无标题时整篇为一个场景。
3. Shot 规则拆分（优先级从高到低）：
   a. 显式镜头标记（「镜头 N」「Shot N」「【镜头 N】」）→ 按标记硬切；
   b. 时间/地点变化（句首新时间词 / 新 slugline / 标签行）→ 切；
   c. 人物行为变化（「人物名 + 动作」独立句 = 新镜头边界）；
   d. 对白段落（说话内容跟随当前人物动作，不强制拆镜）；
   e. 默认合并（无信号 → 整段一镜）。
4. 硬上限：MAX_SHOTS_PER_SCENE=8，超出合并相邻最短镜并记 warning；
   时长规则估算：默认 5s，字数/动作密度微调，clamp [MIN_SHOT_DURATION, MAX_SHOT_DURATION]=[2,8]。
5. 原始文本保留：shot.source_text = 该镜头覆盖的原文（逐字保留，不改写）。
6. 不生成：castIds / locationId / assetId / generationMode / h3Prompt / camera / refs。

设计原则（V17_PLAN §4 核心原则）：LLM 负责「理解」，规则负责「切分/校验/兜底」。
Qwen 不决定镜头数量 —— 本模块是唯一决定镜头边界的地方。

运行方式（同 text_backends 测试）：
    python3 director/tests/test_script_parser.py
或 pytest：
    pytest director/tests/test_script_parser.py
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .production_plan import (
    MAX_SHOTS_PER_SCENE,
    MAX_SHOT_DURATION,
    MIN_SHOT_DURATION,
    Character,
    Dialogue,
    ProductionPlan,
    ProjectInfo,
    Prop,
    Scene,
    Shot,
    Validation,
    VoiceType,
    default_timeline,
)

# ---------------- 场景标题 / 标签 / slugline ----------------

_MD_TITLE_RE = re.compile(r"^\s*(#{1,4})\s+(.+?)\s*$")
_SCENE_INDEX_RE = re.compile(r"^\s*第\s*([一二三四五六七八九十百千\d]+)\s*[场幕景]\s*[:：]?\s*(.*)$")
_SLUG_RE = re.compile(
    r"^\s*(?:内|外|室内|室外|INT\.|EXT\.)[\s　./-]+(.+?)\s*$"
)
_TAG_RE = re.compile(r"^\s*(时间|地点|天气|场景)[：:]\s*(.+?)\s*$")

# ---------------- 时间 / 天气词 ----------------

_TIME_HINTS = (
    "清晨", "早晨", "黎明", "拂晓", "早上", "上午",
    "正午", "中午", "午后", "下午",
    "黄昏", "傍晚", "日暮", "入夜",
    "夜晚", "深夜", "午夜", "凌晨", "夜里", "白天", "夜",
)
_WEATHER_HINTS = ("细雨", "大雨", "暴雨", "小雨", "微雨", "毛毛雨", "雪", "雨", "晴", "阴", "多云", "雾", "风", "雷", "烈日")

# ---------------- 镜头标记 / 动作 / 对白 ----------------

_SHOT_MARK_RE = re.compile(
    r"^\s*【?\s*(?:镜头|鏡頭|Shot|SHOT)\s*([一二三四五六七八九十\d]+)?\s*】?\s*[:：]?\s*(.*)$"
)
_ACTION_VERBS = (
    "推", "拉", "拽", "走", "跑", "奔", "坐", "站", "立",
    "看", "望", "盯", "扫", "环视", "抬眼", "低头", "垂眸", "转身", "回头",
    "点头", "摇头", "摆手", "挥手", "抱拳", "拱手",
    "打开", "推开", "关上", "关门", "收伞", "放下", "拿起", "取出", "掏出",
    "递", "倒", "斟", "沏", "喝", "饮", "端起",
    "开口", "说", "道", "喊", "叫", "唤", "问", "答", "叹", "笑", "哭",
    "轻声", "喃喃", "沉声", "厉声", "冷冷", "漠然",
    "皱眉", "蹙眉", "皱眉", "凝视", "打量",
    "拔", "抽", "握", "掷", "扔", "挥", "劈", "斩", "刺", "出剑", "收剑",
    "跨", "迈", "靠近", "走近", "离开", "退", "停", "顿",
    "指", "敲", "拍", "擦", "抹", "摘", "戴", "换", "系", "解",
    "蹲", "跪", "扑", "冲", "追", "挡", "护",
)
_CONTINUE_PREFIXES = ("而", "并", "接着", "随后", "然后", "只见", "紧接着", "随后", "便", "于是")
_CONTINUE_ENDINGS = ("，", "、", ",", "；", ";")

# 强动作动词：明确人物行为，才触发「人物行为变化」拆镜。
# （排除 停/顿/等 这类可作环境状态描述的弱动词，避免「水声没有停过」被误判为人物动作。）
_STRONG_ACTION_VERBS = (
    "推", "拉", "拽", "走", "跑", "奔", "坐", "站", "立",
    "看", "望", "盯", "扫", "环视", "抬眼", "低头", "垂眸", "转身", "回头",
    "点头", "摇头", "摆手", "挥手", "抱拳", "拱手",
    "打开", "推开", "关上", "关门", "收伞", "放下", "拿起", "取出", "掏出",
    "递", "倒", "斟", "沏", "喝", "饮", "端起",
    "开口", "说", "道", "喊", "叫", "唤", "问", "叹", "笑", "哭",
    "轻声", "喃喃", "沉声", "厉声", "冷冷", "漠然",
    "皱眉", "蹙眉", "凝视", "打量",
    "拔", "抽", "握", "掷", "扔", "挥", "劈", "斩", "刺", "出剑", "收剑",
    "跨", "迈", "靠近", "走近", "离开", "退",
    "指", "敲", "拍", "擦", "抹", "摘", "戴", "换", "系", "解",
    "蹲", "跪", "扑", "冲", "追", "挡", "护", "起身",
)
# 全量动作动词：括号人名去动词前缀 / 时长动作密度统计用（含弱动词）。
# 注意「答」被刻意排除出强集：单字易与拟声词「滴答」误配；对白由「：」判定即可拆镜。
_ACTION_VERBS = _STRONG_ACTION_VERBS + ("停", "顿", "等", "起", "来", "去", "答")
# P0-3b：公开别名，供 entity_cleanse 动作短语实体名清洗复用（避免两表漂移）。
ACTION_VERBS = _ACTION_VERBS

_PAREN_CHAR_RE = re.compile(r"([一-龥]{1,4})[（(]([^）)]{1,12})[）)]")

# 说话动词（对白 speaker 判定用；按长度降序匹配）
_SPEAK_VERBS = (
    "轻声说", "沉声说", "低声说", "厉声说", "高声说",
    "说", "道", "问", "喊", "答", "叹", "笑道", "叹道",
    "开口", "低声", "轻声", "沉声", "厉声", "喃喃", "淡淡", "冷冷",
)

# ---------------------------------------------------------------------------
# 小说正文对白提取（#580 方案 A：引号对白 + 【】系统提示）
# 背景：P1-B 小说导入后 plan.shot.dialogue 全空（用户 2026-08-16 反馈「shot1 音频
# 不像人话」→ 根因 = 自动配音无台词来源）。现有 _dialogue_from_line 只认剧本格式
# 「X：台词」，不识别小说正文引号对白与【】系统提示。这里补纯规则提取。
# ---------------------------------------------------------------------------

# 对白引号对（书名号《》是引用/标题，绝不当作对白）
_QUOTE_CLOSE_MAP = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"'}
_QUOTE_OPEN_CHARS = tuple(_QUOTE_CLOSE_MAP.keys())

# 说话动词（引号说话人判定 + 角色候选；含内心独白动词）
_SPEECH_VERBS_EXT = (
    "轻声说", "沉声说", "低声说", "厉声说", "高声说", "喃喃道", "淡淡道",
    "说", "道", "问", "喊", "答", "叹", "笑道", "叹道", "开口", "喃喃",
    "低语", "心想", "暗想", "心说", "寻思", "自语", "念道", "叫道",
    "吼道", "喝道", "回应", "回答", "应道", "骂道", "怒道", "冷冷道",
    "轻笑", "嘀咕", "嘟囔", "反问", "追问", "开口说",
)
_SPEECH_ALT = "|".join(sorted(_SPEECH_VERBS_EXT, key=len, reverse=True))

# 引号后紧跟的角色名后置动作/介词（确认「X」是说话人主语而非名词短语）
_QUOTE_AFTER_VERBS = (
    "对着", "看着", "听着", "盯着", "扫了", "揉了揉", "叹了", "笑了",
    "敲", "站", "走", "转", "说", "道", "问", "喊", "叫", "答", "开口",
    "抬", "低", "抿", "握", "翻", "点", "划", "伸", "皱", "别过", "回",
    "看", "听", "想", "放", "拿", "从", "往", "朝",
)

# 言语动作词（拆分对白窗口/角色候选：名 + 言语动作 → 说话人）
_SPEECH_ACTIONS = (
    "评论", "说", "道", "问", "回复", "吐槽", "嘀咕", "骂", "喊", "叫",
    "写", "发", "敲", "念叨", "嘟囔", "回嘴", "反驳", "插话", "打字", "读",
)

# 结尾是动词/功能字 → 不像人名（防「你知」「那个」式垃圾候选）
_VERBISH_END = set(
    "知道问看听说想感评论念叨读写画打字回骂喊叫劝哄答叹笑这个那个一样说话讲诉评点说破嚷嚷"
)
# 代词/指代词/高频非人名（说话人推断停用词）
_NAME_STOP = set(
    "他她它我你咱俺这那谁大家自己我们你们他们她们它们咱俩咱两个"
    "那个这个一个一下什么怎么这样那样这些那些有人某些哪个哪样哪里"
)

# 引号后紧跟的名词化后缀「的+X」→ 引用描述（写满了"…"的嫌恶），非说话
_QUOTE_DE_NOUN_RE = re.compile(r"^的[一-龥]{1,8}")

# ---------------------------------------------------------------------------
# P0-2 双轨制 · 动作主语角色 / 动作宾语道具（纯规则兜底，Qwen 只增不删）
# ---------------------------------------------------------------------------
# 背景（用户 2026-08-11 拍板）：真实《山雨客栈》里「蒙面人」只以动作主语出现
# （无括号备注、无对白），规则层原先提取 0 个角色 → 全部押注 Qwen；
# Qwen 一旦漏返回，角色直接消失（SPA 验收 角色 3→2）。双轨制铁律：
#   规则层负责「不能漏」——原文直接出现的核心实体（沈青崖/柳如烟/蒙面人/
#   旧剑匣/青瓷茶碗…）必须先建立候选；
#   Qwen 负责「理解」——代词消解等语义补充，但 Qwen 未返回不得删除规则层已建立实体。

# 句子边界：动作主语判定从强动词向前回溯到最近边界
# （含中文引号「」『』：对白「…」后的角色动作「沈青崖起身」不能被引号阻断主语）
_SUBJECT_BOUNDARY = set("。！？；;，,\n「」『』")

# 名字到此为止：动词/桥接/副词/介词字符，出现在主语名后即视为「名字已结束」
_NAME_BREAK_CHARS = (
    "的了着过正在又也已便就只一再地得步逆侧顺迎反回向从往把将用依被让叫使令"
    "渐缓轻慢猛倏疾忙忽抬低转停顿" + "".join(_STRONG_ACTION_VERBS)
)

# 名字与动词之间的「桥接窗口」允许字符（副词/介词/量词/动词单字/方位/光景）
_ACTION_WINDOW_OK = set(
    _NAME_BREAK_CHARS + "光身影气色声步步缓缓慢慢轻轻静静默默匆匆口前下上里外"
)

# 环境/道具名词后缀：动作主语候选不得以此结尾（防「旧剑匣/客栈大门/山雾」当角色）
_ENV_SUBJECT_SUFFIX = (
    "门", "口", "楼", "堂", "台", "阶", "道", "檐", "柜", "桌", "椅", "窗",
    "墙", "梁", "柱", "瓦", "灯", "烛", "火", "雾", "雨", "风", "光", "影",
    "气", "水", "石", "树", "路", "桥", "山", "林", "云", "天", "地", "色",
    "声", "匣", "盒", "箱", "袋", "剑", "刀", "碗", "杯", "壶", "坛", "伞",
    "车", "船", "房", "屋", "场", "巷", "街", "亭", "庙", "殿",
)

_STRONG_VERB_RE = re.compile("|".join(sorted(_STRONG_ACTION_VERBS, key=len, reverse=True)))

# 动作宾语道具：动词 + 桥接 + 量词 + 名词（Qwen 漏检道具时规则层保留）
_PROP_OBJECT_VERBS = (
    "背", "挎", "拿", "端", "接", "放", "收", "挂", "拔", "抽", "出",
    "提", "抱", "抬", "举", "捧", "握", "持", "拎", "揣", "夹", "拖", "扛",
    "倒", "斟", "沏", "喝", "饮", "擦拭", "擦", "敲", "拍", "掷", "扔",
    "挥", "劈", "斩", "刺", "递", "拭", "抚", "摸", "摘", "戴", "换", "系",
    "解", "打开", "取出", "掏出", "放下", "拿起", "端起", "翻开",
)
# P0-3b：道具动词公开别名，供 entity_cleanse 首位动词裁剪复用（避免两表漂移）。
PROP_OBJECT_VERBS = _PROP_OBJECT_VERBS
_PROP_QUANTIFIER = (
    r"(?:一[把碗盏壶只个柄根支条口张封面]|[两三四五六七]?"
    r"[把碗盏壶只个柄根支条口张封面])?"
)
_PROP_OBJECT_RE = re.compile(
    r"(?:" + "|".join(sorted(_PROP_OBJECT_VERBS, key=len, reverse=True)) + r")"
    r"[的地着过了正在又也已便就只一再来出]{0,2}"
    + _PROP_QUANTIFIER +
    r"([一-龥]{1,4})"  # P0-3b：1~4 字（动词+单字名词=合法道具宾语：持灯/端茶/拔刀）
)
_PROP_NOUN_SUFFIX = (
    "剑", "刀", "碗", "杯", "壶", "灯", "烛", "香", "书", "信", "酒",
    "坛", "桌", "凳", "椅", "柜", "匾", "帘", "匣", "盒", "袋", "箱",
    "瓶", "扇", "镜", "钟", "锤", "斧", "枪", "棒", "弓", "箭", "笛",
    "琴", "棋", "盘", "碟", "罐", "瓢", "勺", "簪", "佩", "带", "冠",
    "靴", "袍", "衣", "衫", "裙", "绳", "链", "锁", "杖", "伞", "笠", "帆",
    "笼",
    # P0-3b：常见单字道具（端茶/斟酒/点灯/提壶）——动词+单字名词是合法道具宾语。
    # 通用词典扩充（正向规则，非黑名单）：任何以「茶/酒」结尾的道具宾语都被识别。
    "茶", "酒",
)


def _strip_shot_prefix(line: str) -> str:
    """剥掉「镜头 N：」前缀，返回该镜头正文；非镜头行原样返回。"""
    m = _SHOT_MARK_RE.match(line)
    if m:
        return (m.group(2) or "").strip()
    return line.strip()


def _lead_name(seg: str) -> str:
    """取句首候选主语名（2~4 汉字；遇到动词/桥接字即停）。"""
    name = ""
    for ch in seg:
        if len(name) >= 4:
            break
        if ch in _NAME_BREAK_CHARS or not ("一" <= ch <= "鿿"):
            break
        name += ch
    return name


def _is_window_ok(rest: str) -> bool:
    """名字到动词之间的桥接段是否合法（≤6 字，只允许副词/介词/量词等窗口字）。"""
    if len(rest) > 6:
        return False
    return all(ch in _ACTION_WINDOW_OK for ch in rest)


def _action_subject_names_in_line(line: str) -> List[str]:
    """单行内动作主语名（P0-2 双轨制规则兜底）。

    算法：对每个强动作动词，从动词位置向前找最近句子边界；边界与动词之间
    的文本 = 候选主语 + 桥接段。名字取开头 2~4 汉字，桥接段校验窗口字。
    例：「蒙面人一步步走近」→ 蒙面人；「沈青崖推门走进」→ 沈青崖；
    「旧剑匣放在膝边」→ 旧剑匣（后续按环境/道具后缀过滤）。
    """
    text = _strip_shot_prefix(line)
    if not text:
        return []
    names: List[str] = []
    for m in _STRONG_VERB_RE.finditer(text):
        j = m.start() - 1
        while j >= 0 and text[j] not in _SUBJECT_BOUNDARY:
            j -= 1
        seg = text[j + 1:m.start()]
        name = _lead_name(seg)
        if len(name) < 2 or name in names:
            continue
        if _is_window_ok(seg[len(name):]):
            names.append(name)
    return names


def _action_subject_names_in_lines(lines: List[str]) -> List[str]:
    """多行内动作主语名（去重保序）。"""
    out: List[str] = []
    for line in lines:
        for nm in _action_subject_names_in_line(line):
            if nm not in out:
                out.append(nm)
    return out


def _scene_action_subject_names(lines: List[str]) -> List[str]:
    """场景级动作主语角色（P0-2 核心实体兜底）。

    规则：动作主语名在 ≥1 个镜头行出现、原文出现 ≥2 行、且不以环境/道具
    名词后缀结尾。保证「沈青崖/柳如烟/蒙面人」这类只以动作主语出现的核心
    角色（无括号、无对白）即使 Qwen 没返回也不被删；同时排除「暖黄灯火/
    雨刚/旧剑匣/柜台后柳」等单句动作宾语/环境名词误当角色。
    """
    line_idx: Dict[str, set] = {}
    for i, line in enumerate(lines):
        for nm in _action_subject_names_in_line(line):
            line_idx.setdefault(nm, set()).add(i)
    text_lines = [_strip_shot_prefix(l) for l in lines]
    out: List[str] = []
    for nm, _idxs in line_idx.items():
        if sum(1 for t in text_lines if nm in t) < 2:
            continue
        if nm.endswith(_ENV_SUBJECT_SUFFIX):
            continue
        if nm not in out:
            out.append(nm)
    return out


def _prop_noun_from_capture(cap: str) -> Optional[str]:
    """从正则捕获串提取以道具后缀结尾的最长合法名词前缀。

    捕获串是「名词 + 可能被贪婪量词吞进来的动词/副词/方位」（`[一-龥]{2,4}`
    会多吞汉字，如「旧剑匣从」「茶碗却不」）。这里在捕获串内部找以道具后缀
    结尾的最长合法名词：保证「背着旧剑匣从山道」→ 旧剑匣、「放下茶碗却不喝」
    → 茶碗（P0-2 双轨制：Qwen 漏检时规则层兜底不丢正确道具）。

    P0-3b（用户 2026-08-11 拍板「动作/空间短语不能进入实体名」）：贪婪量词还会
    把动词后的动作短语吞进捕获串（「柳如烟持灯退到柜台边」→ 捕获「灯退到柜」）。
    先做动作短语阻断：捕获串内出现动作动词（退/走/来/扑/冲向…）时在**最早**动词
    处截断，只留动词前的名词（「灯退到柜」→「灯」；「茶走到桌」→「茶」；
    「刀扑来」→「刀」）。同时允许单字道具（「持灯」「端茶」「拔刀」这类动词+单字
    名词也是合法道具宾语）——长度门槛从 2 放宽到 1。
    """
    # 1) 动作短语阻断：把被贪婪量词吞进来的动作短语截掉，只留名词前缀
    cut = -1
    for v in ACTION_VERBS:
        idx = cap.find(v)
        if idx >= 0 and (cut < 0 or idx < cut):
            cut = idx
    if cut >= 0:
        cap = cap[:cut]
    # 2) 道具后缀：以道具后缀结尾的最长合法名词（含单字道具）
    best: Optional[str] = None
    for suf in _PROP_NOUN_SUFFIX:
        idx = cap.rfind(suf)
        if idx < 0:
            continue
        cand = cap[: idx + len(suf)]
        if 1 <= len(cand) <= 4 and all("一" <= ch <= "鿿" for ch in cand):
            if best is None or len(cand) > len(best):
                best = cand
    return best


def _extract_props_in_line(line: str) -> List[str]:
    """单行内动作宾语道具名（P0-2 双轨制道具兜底）。

    例：「沈青崖背着旧剑匣从山道走来」→ 旧剑匣；「柳如烟放下茶碗」→ 茶碗；
    「沈青崖收过一把旧剑」→ 旧剑。要求名词以道具后缀结尾（强精确）。
    """
    text = _strip_shot_prefix(line)
    if not text:
        return []
    out: List[str] = []
    for m in _PROP_OBJECT_RE.finditer(text):
        noun = _prop_noun_from_capture(m.group(1))
        if noun and noun not in out:
            out.append(noun)
    return out


def _extract_props(lines: List[str]) -> List[Prop]:
    """shot 内动作宾语道具（去重保序）。"""
    names: List[str] = []
    for line in lines:
        for nm in _extract_props_in_line(line):
            if nm not in names:
                names.append(nm)
    return [Prop(name=n) for n in names]


# ---------------- 读取 ----------------

def read_script(path: str) -> str:
    """读取 .md / .txt。UTF-8（含 BOM）优先，GB18030 兜底。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"剧本文件不存在: {path}")
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"无法解码剧本文件: {path}")


# ---------------- 工具 ----------------

def _extract_time(text: str) -> str:
    for t in _TIME_HINTS:
        if t in text:
            return t
    return ""


def _extract_weather(text: str) -> str:
    for w in _WEATHER_HINTS:
        if w in text:
            return w
    return ""


def _location_candidate_from_title(title: str) -> str:
    """P0-4：场景标题兜底 location 候选。

    场景 header 无「地点：」标签时，从标题本身提取地点候选参加实体抽取与
    persisted 绑定匹配（「第一场：山雨楼外」→「山雨楼外」→ registry
    location:山雨楼外→asset_015）。仅「第X场/幕/景：xxx」形式且 xxx 非空才提取；
    纯编号标题（第X场 后无内容）与不带动词性前缀的标题返回 ""（不猜测地点）。

    时间/天气提示词只做「边界剥除」（候选开头/结尾的整词），绝不替换候选
    中间字符——单字「雨」同时是天气词与地名词（「山雨楼外」），子串替换会把
    真实地名刮坏。括号天气（「站台（夜雨）」）与逗号分隔的时间段整体剔除，
    「清晨的站台」剥前缀「清晨」「的」→「站台」。
    """
    m = _SCENE_INDEX_RE.match(title or "")
    if not m:
        return ""
    cand = (m.group(2) or "").strip()
    if not cand:
        return ""
    # 括号里的时间/天气提示整体剔除（「山雨楼外（夜雨）」「站台，雨夜」）。
    cand = re.sub(r"[（(][^（）()]*[)）]", " ", cand)
    # 顿/逗号后的时间天气段落整体剔除（「山雨楼外，倾盆大雨」→「山雨楼外」）。
    cand = re.split(r"[，,。；;]", cand)[0]
    # 只剥候选开头的整词提示（时间/天气/连接词「的」），循环到剥不动为止；
    # 剥到空串说明标题纯是时间提示（「第三场：夜晚」），不猜测地点。
    while True:
        moved = False
        cand = cand.strip(" ·　-—")
        for w in _TIME_HINTS + _WEATHER_HINTS:
            if cand.startswith(w):
                cand = cand[len(w):]
                moved = True
                break
        if cand.startswith("的"):
            cand = cand[1:]
            moved = True
        if not moved:
            break
    return cand.strip(" ·　-—")


def _scene_header(line: str) -> Optional[Tuple[str, str, str, str]]:
    """返回 (title, location_name, time, weather)；不是场景标题行返回 None。

    P0-4：无「地点：」标签时 location_name 用场景标题兜底候选（第X场：xxx）。
    """
    m = _MD_TITLE_RE.match(line)
    if m:
        title = m.group(2).strip()
        loc = _location_candidate_from_title(title)
        return (title, loc, _extract_time(title), _extract_weather(title))
    m = _SCENE_INDEX_RE.match(line)
    if m:
        title = m.group(2).strip() or f"第{m.group(1)}场"
        loc = _location_candidate_from_title(line.strip())
        return (title, loc, _extract_time(title), _extract_weather(title))
    m = _SLUG_RE.match(line)
    if m:
        loc = m.group(1).strip()
        for t in _TIME_HINTS:
            loc = loc.replace(t, "")
        for w in _WEATHER_HINTS:
            loc = loc.replace(w, "")
        loc = loc.strip(" ·　 ")
        return (line.strip(), loc, _extract_time(line), _extract_weather(line))
    return None


def _split_scenes(lines: List[str]) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None

    def flush() -> None:
        nonlocal cur
        if cur:
            scenes.append(cur)
            cur = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        tag = _TAG_RE.match(line)
        if tag:
            if cur is None:
                cur = {"title": "（无标题场景）", "location_name": "", "time": "", "weather": "", "lines": []}
            key, val = tag.group(1), tag.group(2).strip()
            if key == "时间":
                cur["time"] = val
            elif key == "地点":
                cur["location_name"] = val
            elif key == "天气":
                cur["weather"] = val
            continue
        header = _scene_header(line)
        if header is not None:
            flush()
            cur = {
                "title": header[0],
                "location_name": header[1],
                "time": header[2],
                "weather": header[3],
                "lines": [],
            }
            continue
        if cur is None:
            cur = {"title": "（无标题场景）", "location_name": "", "time": "", "weather": "", "lines": []}
        cur["lines"].append(line)
    flush()
    return scenes


def _is_continuation(line: str, prev: str) -> bool:
    """判断 line 是否是上一行的延续（不拆镜）。"""
    if line.startswith(_CONTINUE_PREFIXES):
        return True
    if prev.rstrip().endswith(_CONTINUE_ENDINGS):
        return True
    return False


def _is_env_line(line: str) -> bool:
    """判断是否为纯环境/状态描写（不含人物动作）。"""
    # 含引号对白但不含动作动词 → 视为对白句（不算环境）
    if "：" in line or ":" in line:
        return False
    for v in _ACTION_VERBS:
        if v in line:
            return False
    return True


def _is_action_sentence(line: str) -> bool:
    """人物行为/对白句 → 可作为镜头边界（用强动作动词，避免环境状态误判）。"""
    if "：" in line or ":" in line:
        # 对白行（说话即动作），只要不是纯环境标签
        return not line.startswith(("时间", "地点", "天气"))
    for v in _STRONG_ACTION_VERBS:
        if v in line:
            return True
    return False


def _has_new_scene_signal(cur_lines: List[str], line: str) -> bool:
    """时间/地点变化信号 → 拆镜。"""
    if not cur_lines:
        return False
    if _SLUG_RE.match(line):
        return True
    if _TAG_RE.match(line) and line.startswith(("时间", "地点", "天气")):
        return True
    for t in _TIME_HINTS:
        if line.startswith(t):
            return True
    return False


def _split_by_shot_marks(lines: List[str]) -> List[List[str]]:
    """显式镜头标记硬切。"""
    units: List[List[str]] = []
    cur: List[str] = []
    for line in lines:
        if _SHOT_MARK_RE.match(line):
            if cur:
                units.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        units.append(cur)
    return units


def _split_by_action_units(lines: List[str]) -> List[List[str]]:
    """时间/地点/人物行为变化 → 动作单元拆镜；对白跟随；无信号默认整段一镜。"""
    units: List[List[str]] = []
    cur: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _has_new_scene_signal(cur, stripped):
            units.append(cur)
            cur = [stripped]
            continue
        if _is_action_sentence(stripped) and cur and not _is_continuation(stripped, cur[-1]):
            units.append(cur)
            cur = [stripped]
            continue
        cur.append(stripped)
    if cur:
        units.append(cur)
    return units


def _merge_overlimit(units: List[List[str]], max_n: int) -> Tuple[List[List[str]], int]:
    """超上限时合并相邻最短的 unit，直到 ≤ max_n。返回 (units, 合并次数)。"""
    chunks = [list(u) for u in units]
    merged = 0
    while len(chunks) > max_n:
        best_i = 0
        best_len = float("inf")
        for k in range(len(chunks) - 1):
            combo = len(chunks[k]) + len(chunks[k + 1])
            if combo < best_len:
                best_len = combo
                best_i = k
        chunks[best_i] = chunks[best_i] + chunks[best_i + 1]
        del chunks[best_i + 1]
        merged += 1
    return chunks, merged


# ---------------- 时长估算 ----------------

def estimate_duration_sec(text: str) -> int:
    """规则估算成片时长（秒）。默认 5s，字数/动作密度微调，clamp [2,8]。"""
    n = len(text)
    base = 5
    if n >= 90:
        base += 1
    if n >= 160:
        base += 1
    action_count = sum(1 for v in _STRONG_ACTION_VERBS if v in text)
    if action_count >= 4:
        base += 1
    return max(MIN_SHOT_DURATION, min(MAX_SHOT_DURATION, base))


# ---------------- 实体提取（规则层只做轻量，语义补全交给 Qwen） ----------------

def _is_valid_name_candidate(name: str) -> bool:
    """是否为「像人名」的候选（≥2 字、非代词、结尾非动词/功能字）。"""
    if len(name) < 2:
        return False
    if name in _NAME_STOP:
        return False
    if name[-1] in _VERBISH_END:
        return False
    return True


def _clean_quote_text(s: str) -> str:
    """对白文本清洗：去空白/换行，剥残留引号。"""
    s = re.sub(r"\s+", "", s)
    return s.strip("“”‘’「」『』\"《》 ")


def _is_speech_quote(line: str, qs: int, qe: int) -> bool:
    """判定引号片段是否为「说出来」的对白（排除强调/拟声/引用词）。

    规则（保守优先，宁可漏不可错）：
    R0  引号后紧接「的+名词」（写满了"…"的嫌恶）→ 引用描述，非说话；
    R1  引号前说话动词 → 说话；
    R2  引号后「名（说/道/…）」→ 说话；
    R3  长句含句末标点/强语气词 → 说话；
    R4  超长句（≥12 字）→ 说话；
    R5  逗号尾 + 后接汉字（拆句对白「…，X…」）→ 说话；
    R6  前一个引号紧贴结束 → 连续对白（“…”“…”）。
    """
    seg = line[qs:qe].strip()
    before = line[:qs]
    after = line[qe:].lstrip("”’」』\"")  # qe 是闭引号位置，跳过引号字符看后文
    n = len(seg)
    if n < 2:
        return False
    if _QUOTE_DE_NOUN_RE.match(after):
        return False
    # R1: 引号前说话动词（允许中间只有冒号/逗号/空格/引号）
    if re.search(rf"(?:{_SPEECH_ALT})[：:，,]?\s*[“‘「『\"]*$", before):
        return True
    # R2: 引号后「名（说/道/…）」
    if re.match(rf"\s*[一-龥]{{1,4}}(?:{_SPEECH_ALT})[：:]?", after):
        return True
    # R3
    if n >= 6 and (
        any(c in seg for c in "！？。…：")
        or seg[-1] in "吗吧啊呀呢嘛呐哈噻嘞哟哦噢"
    ):
        return True
    # R4
    if n >= 12:
        return True
    # R5
    if seg.endswith(("，", ",")) and after[:1] and "一" <= after[0] <= "鿿":
        return True
    # R6
    if before.rstrip().endswith(("”", "』", "」", "’", '"')):
        return True
    return False


def _infer_quote_speaker(
    line: str,
    qs: int,
    qe: int,
    prev_speaker: str,
    candidates: Optional[List[str]],
) -> str:
    """引号对白说话人推断（纯规则、保守；返回 '' 表示未知，走 Voice Cast 兜底）。

    优先级：引号前「名（说/道/…）」 > 引号后「名 + 动作动词」 >
    拆分对白窗口（名 + 言语动作）> 继承上一句 > 空。
    """
    before = line[:qs]
    after = line[qe:].lstrip("”’」』\"")  # qe 是闭引号位置，跳过引号字符看后文
    # 1) 引号前紧贴说话动词：名（说/道/问/…）
    m = re.search(rf"([一-龥]{{2,4}})(?:{_SPEECH_ALT})[：:，,]?[“‘「『\"]?$", before[:24])
    if m:
        name = m.group(1)
        if _is_valid_name_candidate(name):
            return name
    # 2) 引号后紧贴说话人主语：名 + 动作动词（从长到短试，防贪婪吞字：
    #    「林晓对着…」若贪成「林晓对」+「着…」动词校验即失败）
    m = re.match(r"\s*([一-龥]{1,4})", after)
    if m:
        full = m.group(1)
        for k in range(min(4, len(full)), 1, -1):
            name = full[:k]
            rest = after[m.start() + len(name):]
            if (
                _is_valid_name_candidate(name)
                and re.match(r"(?:" + "|".join(_QUOTE_AFTER_VERBS) + r")", rest)
            ):
                return name
    # 3) 拆分对白窗口：本引号前出现「名 + 言语动作」。
    #    候选驱动（优先）：candidates 从长到短试，避免正则前缀试误报「不是」这类非人名。
    window = before[-80:]
    action_re = r"(?:" + "|".join(sorted(_SPEECH_ACTIONS, key=len, reverse=True)) + r")"
    if candidates:
        for name in sorted(candidates, key=len, reverse=True):
            if _is_valid_name_candidate(name) and re.search(
                rf"{re.escape(name)}[^。！？\n]{{0,48}}?{action_re}", window
            ):
                return name
    elif any(a in window for a in _SPEECH_ACTIONS):
        m = re.search(rf"([一-龥]{{2,4}})[^。！？\n]{{0,48}}?{action_re}", window)
        if m:
            name = m.group(1)
            if (
                _is_valid_name_candidate(name)
                and name not in ("屏幕", "画面", "键盘", "手指", "声音", "语气",
                                 "原文", "漫画", "评论", "手机", "女主", "男主",
                                 "恶毒", "主角", "读者", "观众", "空气", "心口")
            ):
                return name
    # 4) 继承
    return prev_speaker if prev_speaker else ""


def _extract_prose_dialogues(
    line: str,
    prev_speaker: str = "",
    candidates: Optional[List[str]] = None,
) -> Tuple[List[Dialogue], str]:
    """从一行小说正文提取多条对白（引号对白 + 【】系统提示）。

    返回 (dialogues, last_speaker)。【】→ speaker="系统"、type=system_voice；
    系统提示是原子块（其内部引号不再扫描）。last_speaker 供跨 chunk/镜头继承
    （跳过系统，避免「系统」污染下一条角色对白的说话人）。
    """
    out: List[Dialogue] = []
    cur = prev_speaker if prev_speaker and prev_speaker != "系统" else ""
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "【":
            j = line.find("】", i)
            if j == -1:
                break
            body = _clean_quote_text(line[i + 1:j])
            if body:
                out.append(
                    Dialogue(
                        speaker="系统",
                        text=body,
                        type=VoiceType.SYSTEM_VOICE,
                    )
                )
            i = j + 1
            continue
        if ch in _QUOTE_OPEN_CHARS:
            j = line.find(_QUOTE_CLOSE_MAP[ch], i + 1)
            if j == -1:
                break  # 未闭合残句（理论不该出现，quote-aware 切分后）
            # qs/qe 用「内容边界」（不含引号字符）：_is_speech_quote/_infer_quote_speaker
            # 内部 before/after 已对闭引号 lstrip，规则才能看到引号后的动作主语。
            if _is_speech_quote(line, i + 1, j):
                text = _clean_quote_text(line[i + 1:j])
                if text:
                    spk = _infer_quote_speaker(line, i + 1, j, cur, candidates)
                    out.append(
                        Dialogue(speaker=spk, text=text, type=VoiceType.CHARACTER_DIALOGUE)
                    )
                    if spk:
                        cur = spk
            i = j + 1
            continue
        i += 1
    return out, cur


def _paren_names(line: str) -> List[str]:
    """从「XX（备注）」提取所有括号人名，去掉前导动作动词（「查看沈青崖（…）」→「沈青崖」）。"""
    names: List[str] = []
    for m in _PAREN_CHAR_RE.finditer(line):
        name = m.group(1).strip()
        for v in sorted(_ACTION_VERBS, key=len, reverse=True):
            if name.startswith(v) and len(name) > len(v):
                name = name[len(v):]
                break
        if name and name not in names:
            names.append(name)
    return names


def _collect_candidate_names(lines: List[str]) -> List[str]:
    """候选角色名：括号备注「名（备注）」 + 标准「X：/X说：」对白行首 + 动作主语兜底。

    在 Scene 级收集（同一场景内角色通用），再传给各 Shot 的对白/角色提取。
    P0-3（用户 2026-08-11 拍板）：镜头标记行「镜头一：远景…」不是对白，
    必须排除 —— 否则「镜头一」会被当成角色候选（SPA 真实页面角色 11 的规则层源头）。
    P0-2（双轨制）：动作主语角色（沈青崖/蒙面人…）也进候选，Qwen 只增不删。
    """
    names: List[str] = []
    for line in lines:
        if _SHOT_MARK_RE.match(line):
            continue
        for name in _paren_names(line):
            if name and name not in names:
                names.append(name)
        m = re.match(
            r"^\s*([一-龥]{1,4})\s*(?:" + "|".join(_SPEAK_VERBS) + r")*\s*[：:]\s*", line
        )
        if m:
            n = m.group(1)
            if n and n not in names:
                names.append(n)
        # #580 小说正文：引号后说话人主语（”林晓对着…→ 林晓）
        for qm in re.finditer(
            r"[”’」』\"]([一-龥]{2,4})(?:" + "|".join(_QUOTE_AFTER_VERBS) + r")", line
        ):
            n = qm.group(1)
            if _is_valid_name_candidate(n) and n not in names:
                names.append(n)
        # #580 小说正文：说话动词前主语（林晓说/心想 → 林晓，全文找不只行首）
        for vm in re.finditer(rf"([一-龥]{{2,4}})(?:{_SPEECH_ALT})", line):
            n = vm.group(1)
            if _is_valid_name_candidate(n) and n not in names:
                names.append(n)
    # P0-2 双轨制：动作主语角色（原文直接出现）→ 候选兜底
    for nm in _scene_action_subject_names(lines):
        if nm and nm not in names:
            names.append(nm)
    # #580 防垃圾候选：结尾动词/功能字 / 代词指代（「你知」「那个」→ 不进角色表）
    cleaned = []
    for n in names:
        if n in _NAME_STOP or (len(n) >= 2 and n[-1] in _VERBISH_END):
            continue
        if n not in cleaned:
            cleaned.append(n)
    return cleaned


def _dialogue_from_line(line: str, candidates: List[str]) -> Optional[Dialogue]:
    """从一行提取一条对白。speaker 只取确定性高的：
    1) 标准「X：/X说：」；2) 行首候选名 + 含冒号（说话者是句首动作主语）。
    复杂句（宾语/长修饰）speaker 留空，交给 Qwen 补全。
    P0-3：镜头标记行（「镜头一：…」）不是对白，直接返回 None。
    """
    if _SHOT_MARK_RE.match(line):
        return None
    # 1) 标准「X说：/X：」
    m = re.match(
        r"^\s*([一-龥]{1,4})\s*(?:" + "|".join(_SPEAK_VERBS) + r")*\s*[：:]\s*", line
    )
    speaker = m.group(1) if m and m.group(1) in candidates else ""
    if not speaker:
        # 2) 行首候选名 + 含冒号（说话者是句首动作主语；最长候选名优先）
        head = ""
        for name in sorted(candidates, key=len, reverse=True):
            if line.startswith(name):
                head = name
                break
        if head and ("：" in line or ":" in line):
            speaker = head
    if not speaker:
        return None
    ci = min(i for i, ch in enumerate(line) if ch in "：:")
    tail = line[ci + 1:].strip().lstrip("\"「『 “‘")
    text = re.split(r"[\"」』”’]", tail)[0].strip()
    if not text:
        return None
    return Dialogue(speaker=speaker, text=text)


def _extract_dialogues(
    lines: List[str],
    candidates: Optional[List[str]] = None,
    prev_speaker: str = "",
) -> List[Dialogue]:
    """从若干行提取对白（剧本格式 + #580 小说正文引号对白/【】系统提示）。

    prev_speaker：进入本组行前的说话人（跨 chunk/镜头继承，来自上一镜尾）。
    返回按原文顺序的去重结果；last_speaker 由调用方通过 _extract_dialogues_last 获取。
    """
    if candidates is None:
        candidates = _collect_candidate_names(lines)
    out: List[Dialogue] = []
    cur = prev_speaker if prev_speaker and prev_speaker != "系统" else ""
    for line in lines:
        d = _dialogue_from_line(line, candidates)
        if d:
            out.append(d)
            if d.speaker:
                cur = d.speaker
            continue
        prose, cur = _extract_prose_dialogues(line, cur, candidates)
        out.extend(prose)
    # 去重保序（同人同台词只留一次；带说话人的首现优先）
    seen = set()
    seen_texts: set[str] = set()
    uniq: List[Dialogue] = []
    for d in out:
        key = (d.speaker, d.text)
        if key in seen:
            continue
        seen.add(key)
        # 同 text 已出现过但当时 speaker 为空 → 用这次带说话人的补上
        if d.speaker and d.text in seen_texts:
            for prev in uniq:
                if prev.text == d.text and not prev.speaker:
                    prev.speaker = d.speaker
                    break
            continue
        uniq.append(d)
        seen_texts.add(d.text)
    return uniq


def _extract_dialogues_last(lines: List[str], prev_speaker: str = "") -> str:
    """提取对白后的末位说话人（供跨镜头继承；跳过「系统」）。"""
    if not lines:
        return prev_speaker
    candidates = _collect_candidate_names(lines)
    cur = prev_speaker if prev_speaker and prev_speaker != "系统" else ""
    for line in lines:
        d = _dialogue_from_line(line, candidates)
        if d:
            if d.speaker:
                cur = d.speaker
            continue
        _, cur = _extract_prose_dialogues(line, cur, candidates)
    return cur


def _extract_characters(lines: List[str], candidates: Optional[List[str]] = None) -> List[Character]:
    if candidates is None:
        candidates = _collect_candidate_names(lines)
    names: List[str] = []
    for line in lines:
        for name in _paren_names(line):
            if name and name not in names:
                names.append(name)
    for d in _extract_dialogues(lines, candidates):
        if d.speaker and d.speaker not in names:
            names.append(d.speaker)
    # P0-2 双轨制：本镜头动作主语角色（在场景候选池内）→ 补进角色表
    for nm in _action_subject_names_in_lines(lines):
        if nm in candidates and nm not in names:
            names.append(nm)
    return [Character(name=n) for n in names]


# ---------------- 主入口 ----------------

def parse_script(text: str, source_file: str = "", title: str = "") -> ProductionPlan:
    """纯规则：剧本文本 → ProductionPlan Schema v1。

    只做规则拆 Scene/Shot + 时长估算 + 轻量实体（对白说话者/括号人物）。
    Qwen 语义理解（角色/道具/动作/情绪/视觉意图）在 Commit 2 script_analyzer 补全。
    """
    if not text or not text.strip():
        plan = ProductionPlan(
            project=ProjectInfo(title=title, source_file=source_file),
            scenes=[],
            validation=Validation(status="pending"),
        )
        plan.validate()
        return plan

    raw_lines = text.splitlines()
    scene_dicts = _split_scenes(raw_lines)
    scenes: List[Scene] = []
    parser_warnings: List[str] = []
    scene_seq = 0

    for sd in scene_dicts:
        lines = sd["lines"]
        if not lines:
            continue  # 空场景（如文档大标题）跳过，不占编号
        scene_seq += 1
        scene_id = f"scene_{scene_seq:02d}"

        # 1) 显式镜头标记优先；2) 否则动作单元拆镜
        if any(_SHOT_MARK_RE.match(l) for l in lines):
            units = _split_by_shot_marks(lines)
        else:
            units = _split_by_action_units(lines)

        # 硬上限：超出合并 + warning
        if len(units) > MAX_SHOTS_PER_SCENE:
            units, merged = _merge_overlimit(units, MAX_SHOTS_PER_SCENE)
            parser_warnings.append(
                f"场景 {scene_id} 镜头数超过上限 {MAX_SHOTS_PER_SCENE}，已合并 {merged} 组相邻镜头"
            )

        # 场景级角色候选名（同一场景内角色通用，供各镜对白/角色提取）
        scene_candidates = _collect_candidate_names(lines)

        shots: List[Shot] = []
        for j, unit in enumerate(units, start=1):
            source_text = "\n".join(unit).strip()
            shots.append(
                Shot(
                    shot_id=f"shot_{j:02d}",
                    source_text=source_text,
                    duration_sec=estimate_duration_sec(source_text),
                    characters=_extract_characters(unit, scene_candidates),
                    props=_extract_props(unit),  # P0-2 双轨制：动作宾语道具规则兜底（Qwen 只增不删）
                    actions=[],  # Qwen 补全
                    emotion="",  # Qwen 补全
                    dialogue=_extract_dialogues(unit, scene_candidates),
                    visual_intent="",  # Qwen 补全
                )
            )

        scenes.append(
            Scene(
                scene_id=scene_id,
                title=sd["title"],
                location_name=sd["location_name"],
                time=sd["time"],
                weather=sd["weather"],
                shots=shots,
            )
        )

    plan = ProductionPlan(
        project=ProjectInfo(title=title, source_file=source_file),
        scenes=scenes,
        validation=Validation(status="pending"),
    )
    plan.timeline = default_timeline(plan)  # P0 Story Timeline：默认场景树顺序
    plan.validate()
    # validate() 只产生结构校验 warnings；合并 parser 级 warnings（如超上限合并）
    if parser_warnings:
        plan.validation.warnings = parser_warnings + plan.validation.warnings
    return plan


def parse_script_file(path: str, title: str = "") -> ProductionPlan:
    """读取 + 解析剧本文件。title 缺省用文件名。"""
    text = read_script(path)
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
    return parse_script(text, source_file=os.path.basename(path), title=title)


__all__ = [
    "read_script",
    "parse_script",
    "parse_script_file",
    "estimate_duration_sec",
    "MAX_SHOTS_PER_SCENE",
    "MIN_SHOT_DURATION",
    "MAX_SHOT_DURATION",
]
