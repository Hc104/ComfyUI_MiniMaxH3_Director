#!/usr/bin/env python3
"""Visual Style / Art Direction 层（v2.0 ⑧，用户 2026-08-17 评审新增核心）。

命题：Camera Rules 回答「怎么拍」，Visual Style 回答「拍出来是什么视觉产品」。
抖音成熟 AI 漫剧「大量写实风」不是随机风格——**视觉基底是全剧级固定条件，
不是每镜临时决定**。用户拍板：内置预设库 + 一键选择，Director 生成 H3 时
自动注入对应视觉风格描述，**全剧所有镜头统一**。

设计铁律（CAMERA_RULES_V2 §3.5）：
1. ``VisualStyleProfile``：全剧级持久化对象（与 Bible/Asset 同级），
   含 art_direction / style_keywords / fidelity / color_tone / negative_rules / per_scene。
2. ``STYLE_PRESETS``：内置 7 预设（推荐「抖音半写实漫剧」）。
3. ``style_block``：H3 描述区固定画风块（中文，全剧级一致，可读）。
4. ⛔ 默认每镜画风一致；未显式切换时，H3 画风块全剧级完全相同（不静默漂移）。
5. ⛔ 纯规则零 LLM / 零显存。

核心入口：
    style_block(profile, scene="") -> str
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 「抖音半写实漫剧」推荐预设 · 内置英文风格串（用户提供，进 art_direction）。
_DOUYIN_EN = (
    "Modern Chinese semi-realistic anime. Ultra detailed character. Realistic facial "
    "proportions. Delicate skin shading. Natural makeup. Detailed hair strands. Cinematic "
    "photography. Movie lighting. Warm daylight. Shallow depth of field. 85mm lens. Soft "
    "bokeh. Photorealistic materials. Anime rendering. High-end Chinese animation style. "
    "Consistent character appearance. Natural facial expressions. Ultra HD."
)

# 「抖音半写实漫剧」8 部分拆解（用户原话结构化，进 docstring/art_direction，供参考）：
# ① 人物：半写实动漫（真人五官+动漫皮肤，真人70%/二次元30%，鼻子真实体积/嘴唇厚度真实）
# ② 光影：电影级自然光（柔和漫反射/鼻梁高光/嘴唇湿润反光/头发边缘光）
# ③ 镜头：85mm 浅景深、焦点在人眼
# ④ 色彩：低饱和暖调奶油色（不用高饱和/赛博朋克/霓虹/高对比）
# ⑤ 材质：真实（一根根发丝/真实肤质非磨皮/针织纹理/PBR 饰品）
# ⑥ 景深：前景清、后景逐层虚化
# ⑦ 构图：人物占 60%、前景焦点、背景虚、侧脸留白、三分法
# ⑧ 整体：电影摄影+半写实动漫人物+国漫渲染（介于原神/日漫与真人电影之间）

# 全剧级负面约束（防崩坏，双语；H3 渲染时合并进 _ANTI_BREAK_RULE）。
_COMMON_NEGATIVE: List[str] = [
    "人物五官、发型、服饰全程保持不变，人体结构正常，无畸形崩坏，画风统一。",
    "Keep consistent facial features, hairstyle and costume. Proper anatomy. No distortion.",
]


@dataclass
class VisualStyleProfile:
    """全剧级视觉基底（持久化，与 Bible/Asset 同级）。

    - ``art_direction``：一句话画风总纲（含英文串，进 H3「画风：」行）；
    - ``style_keywords``：分组关键词 人物/光影/材质/整体；
    - ``fidelity``：写实度档位 full_realistic / semi_realistic / stylized；
    - ``color_tone``：色调基调 cold / warm / dark / daylight；
    - ``negative_rules``：全剧级防崩坏负面（合并进 _ANTI_BREAK_RULE）；
    - ``per_scene``：场景级微调（只改光影/色调局部词，不改画风总纲）。
    """
    profile_id: str = "douyin_semi_realistic"
    name: str = ""
    art_direction: str = ""
    style_keywords: Dict[str, str] = field(default_factory=dict)
    fidelity: str = "semi_realistic"
    color_tone: str = "warm"
    negative_rules: List[str] = field(default_factory=list)
    per_scene: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "art_direction": self.art_direction,
            "style_keywords": dict(self.style_keywords),
            "fidelity": self.fidelity,
            "color_tone": self.color_tone,
            "negative_rules": list(self.negative_rules),
            "per_scene": dict(self.per_scene),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VisualStyleProfile":
        if not isinstance(data, dict) or not data:
            return cls()
        return cls(
            profile_id=str(data.get("profile_id") or "douyin_semi_realistic"),
            name=str(data.get("name", "")),
            art_direction=str(data.get("art_direction", "")),
            style_keywords={str(k): str(v) for k, v in (data.get("style_keywords") or {}).items()},
            fidelity=str(data.get("fidelity") or "semi_realistic"),
            color_tone=str(data.get("color_tone") or "warm"),
            negative_rules=[str(r) for r in (data.get("negative_rules") or [])],
            per_scene={str(k): str(v) for k, v in (data.get("per_scene") or {}).items()},
        )


def _preset(
    profile_id: str, name: str, art_direction: str,
    keywords: Dict[str, str], fidelity: str, color_tone: str,
    negative_rules: Optional[List[str]] = None,
    per_scene: Optional[Dict[str, str]] = None,
) -> VisualStyleProfile:
    return VisualStyleProfile(
        profile_id=profile_id,
        name=name,
        art_direction=art_direction,
        style_keywords=keywords,
        fidelity=fidelity,
        color_tone=color_tone,
        negative_rules=list(negative_rules or _COMMON_NEGATIVE),
        per_scene=dict(per_scene or {}),
    )


# ---------- 内置 7 预设库 ----------

STYLE_PRESETS: Dict[str, VisualStyleProfile] = {
    # 抖音半写实漫剧【推荐】——用户 8 部分拆解 + 英文串。
    "douyin_semi_realistic": _preset(
        "douyin_semi_realistic",
        "抖音半写实漫剧（Semi-Realistic Cinematic Anime）",
        f"现代国漫写实风。{_DOUYIN_EN}",
        {
            "人物": "半写实动漫人物，真实人体比例，细腻五官，自然皮肤材质",
            "光影": "电影级光影，真实环境反射，柔和轮廓光",
            "材质": "真实布料，真实头发，真实皮肤，真实金属",
            "整体": "高精度国漫，电影感，写实人物，轻动漫化",
        },
        "semi_realistic",
        "warm",
        per_scene={"雨夜": "冷调，光线清冷克制", "回忆": "暖黄褪色，柔光"},
    ),
    # 日漫赛璐璐风。
    "japan_cel": _preset(
        "japan_cel",
        "日漫赛璐璐风",
        "Japanese anime cel style. Flat cel shading. Clean linework. High saturation. Anime rendering.",
        {
            "人物": "日漫赛璐璐上色，干净线条，大眼精致五官",
            "光影": "平涂明暗，赛璐璐影，高饱和",
            "材质": "动画材质，无真实纹理",
            "整体": "典型日本动画剧场感",
        },
        "stylized",
        "daylight",
    ),
    # 国产 3D 动画风。
    "cn_3d": _preset(
        "cn_3d",
        "国产3D动画风",
        "Chinese 3D animation style. High-end CG rendering. Stylized realistic characters. Dynamic lighting.",
        {
            "人物": "国产3D动画角色，类腾讯动漫渲染，精致建模",
            "光影": "3D 全局光照，轮廓光，环境反射",
            "材质": "3D 次表面散射皮肤，真实布料物理",
            "整体": "高精度国产3D动画质感",
        },
        "semi_realistic",
        "daylight",
    ),
    # Pixar 风。
    "pixar": _preset(
        "pixar",
        "Pixar 风",
        "Pixar style. Stylized CG. Soft rounded shapes. Warm colorful palette. Pixar-quality rendering.",
        {
            "人物": "皮克斯风格化角色，圆润五官，夸张表情",
            "光影": "柔和布光，暖色氛围",
            "材质": "皮克斯材质渲染，软质表面",
            "整体": "皮克斯剧场动画质感",
        },
        "stylized",
        "warm",
    ),
    # 水墨国风。
    "ink_guofeng": _preset(
        "ink_guofeng",
        "水墨国风",
        "Chinese ink wash style. Elegant brush strokes. Monochrome ink. Guofeng aesthetic.",
        {
            "人物": "水墨笔触勾勒人物，写意五官",
            "光影": "留白与墨色浓淡",
            "材质": "宣纸与墨韵",
            "整体": "古典水墨国风意境",
        },
        "stylized",
        "cold",
    ),
    # 韩漫厚涂风。
    "korean_thick": _preset(
        "korean_thick",
        "韩漫厚涂风",
        "Korean webtoon thick painting style. Painterly rendering. Rich colors. Mature characters.",
        {
            "人物": "韩漫厚涂角色，立体五官，成熟气质",
            "光影": "厚涂笔触光影，浓郁色彩",
            "材质": "油画质感",
            "整体": "韩漫 webtoon 封面质感",
        },
        "semi_realistic",
        "warm",
    ),
    # 写实电影风。
    "cinematic_real": _preset(
        "cinematic_real",
        "写实电影风",
        "Photorealistic cinematic style. Real human face. Natural light. Film color grading. Shallow DOF.",
        {
            "人物": "接近真人电影角色，真实肤质与五官",
            "光影": "电影级布光，自然光",
            "材质": "完全写实材质",
            "整体": "真人电影质感",
        },
        "full_realistic",
        "daylight",
    ),
}


def get_preset(preset_id: str) -> VisualStyleProfile:
    """预设库取 profile；未知 id → 推荐预设（douyin_semi_realistic）。"""
    return STYLE_PRESETS.get(preset_id or "", STYLE_PRESETS["douyin_semi_realistic"])


def presets_payload() -> Dict[str, Dict[str, Any]]:
    """7 预设完整 dict（单一事实来源，供 GET /minimax/director/style/presets）。

    前端 Visual Style 面板不硬编码副本：拉取本 payload → 下拉选择 →
    选中的完整 profile dict 存 project.styleProfile → 生成时透传 /prompt/h3
    的 style_profile（后端 style_block 需要完整字段渲染画风块）。
    """
    return {pid: p.to_dict() for pid, p in STYLE_PRESETS.items()}


def style_block(profile: Any, scene: str = "") -> str:
    """H3 描述区固定画风块（中文，全剧级一致）。

    返回格式：
        画风：{art_direction}。人物：…。光影：…。材质：…。整体：…。
    场景级微调：``per_scene`` 命中 → 追加「（{scene}：…）」。
    空 profile → ""（渲染层跳过，严格向后兼容）。
    """
    if isinstance(profile, VisualStyleProfile):
        p = profile
    else:
        p = VisualStyleProfile.from_dict(profile)
    if not p.art_direction and not p.style_keywords:
        return ""
    parts: List[str] = [f"画风：{p.art_direction or p.name}"]
    for label in ("人物", "光影", "材质", "整体"):
        v = str(p.style_keywords.get(label, "") or "").strip()
        if v:
            parts.append(f"{label}：{v}")
    block = "。".join(parts) + "。"
    if scene and p.per_scene.get(scene):
        block += f"（{scene}：{p.per_scene[scene]}）"
    return block


__all__ = [
    "VisualStyleProfile",
    "STYLE_PRESETS",
    "get_preset",
    "presets_payload",
    "style_block",
]
