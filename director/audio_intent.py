#!/usr/bin/env python3
"""声音导演层：AudioIntent 数据模型 + Voice Cast + 纯规则派生（TTS/Voice Cast 分支）。

架构（用户 2026-08-15 确认终版，见 tts-voice-cast-plan.md）：
    Shot.dialogue（Dialogue 扩展 type/voice_id/emotion/delivery）
        → build_audio_intent()（纯规则，零 LLM 零显存）
        → AudioIntent（声音导演计划）
            ├─ voice_cast: Dict[str, str]  角色 → voice_id（本镜涉及）
            ├─ lines:      List[VoiceLine] 逐行 TTS 台词（voice_id 已解析）
            └─ ambient/music: H3 环境音+BGM 透传（ffmpeg 混音参考）
        → TTS Engine（可插拔：Edge-TTS / CosyVoice 3 / MiniMax Speech / GPT-SoVITS）
        → ffmpeg 自动混音（H3 音频保留 + TTS 人声叠加）

分工铁律（B 路实测失败后的架构决策，#545 系列）：
  - H3  = 摄影 + 环境声音（画面 / 环境音 / BGM / 短音效）
  - TTS = 演员（人物对白 / 旁白 / 内心独白 / 系统音）
  - FFmpeg = 后期混音
``director_intent._audio_block``（DirectorIntent.audio）继续服务 H3 prompt 侧（只带
speaker/text），本模块是新加的 TTS 配音层，二者在 ffmpeg 混音时合流。

⛔ 纯规则零 LLM 零显存：本模块不 import torch / ollama / requests；派生只读 shot/scene，
不修改任何对象（AudioIntent 是派生数据，不落 ProductionPlan JSON）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .director_intent import _audio_block
from .production_plan import VoiceType

# 特殊说话人识别（中文语境；命中 → 强规则覆盖 Dialogue.type）
_NARRATOR_SPEAKERS = ("旁白", "叙述", "叙述者", "旁白音", "画外音", "说书人")
_SYSTEM_SPEAKERS = ("系统", "系统音", "提示音", "小助手", "AI", "人工智能")

# voice_id 净化：去掉空白/标点，保留中文（voice_柳如烟 这类稳定可读 ID）
_ID_CLEAN_RE = re.compile(
    r"[\s（）()「」『』【】\[\]《》\"'“”‘’：:，,。.、；;？！?!…—\-]+"
)


def infer_voice_type(speaker: str, text: str = "") -> str:
    """从说话人名推断声音类型（规则）。

    特殊说话人名（旁白/系统…）→ narration/system_voice；其余 → character_dialogue。
    内心独白**不做自动识别**（文本特征脆弱、易误判），靠显式 Dialogue.type 或前端标注。
    """
    spk = (speaker or "").strip()
    for n in _NARRATOR_SPEAKERS:
        if n in spk:
            return VoiceType.NARRATION
    for s in _SYSTEM_SPEAKERS:
        if s in spk:
            return VoiceType.SYSTEM_VOICE
    return VoiceType.CHARACTER_DIALOGUE


def default_voice_id(speaker: str, voice_type: str) -> str:
    """角色名 → 稳定 voice_id（跨 100 章角色声音稳定）。

    旁白/系统 → 固定 ID；普通角色 → ``voice_`` + 净化角色名（保留中文）。
    显式 VoiceCast.entries 优先（本函数只做规则兜底）。
    """
    if voice_type == VoiceType.NARRATION:
        return "voice_narrator"
    if voice_type == VoiceType.SYSTEM_VOICE:
        return "voice_system"
    spk = (speaker or "").strip()
    if not spk:
        return "voice_unknown"
    return "voice_" + _ID_CLEAN_RE.sub("", spk)


@dataclass
class VoiceCast:
    """Voice Cast：角色 → voice_id 稳定映射（跨 100 章角色声音稳定）。

    entries:            显式角色映射（用户/前端/AI 配置，优先于规则兜底）
                        例如 柳如烟 → voice_liu_ruyan / voice_lin_xiao
    narrator_voice:     旁白固定音色
    system_voice:       系统音固定音色
    system_electronic:  系统音电子味变体（delivery 含「电子」时启用）
    """

    entries: Dict[str, str] = field(default_factory=dict)
    narrator_voice: str = "voice_narrator"
    system_voice: str = "voice_system"
    system_electronic: str = "voice_system_electronic"

    def resolve(self, speaker: str, voice_type: str, delivery: str = "") -> str:
        """解析 voice_id：旁白/系统固定 ID → 显式 entries → 规则兜底。"""
        spk = (speaker or "").strip()
        if voice_type == VoiceType.NARRATION:
            return self.narrator_voice
        if voice_type == VoiceType.SYSTEM_VOICE:
            if "电子" in (delivery or ""):
                return self.system_electronic
            return self.system_voice
        if spk in self.entries:
            return self.entries[spk]
        return default_voice_id(spk, voice_type)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": dict(self.entries),
            "narrator_voice": self.narrator_voice,
            "system_voice": self.system_voice,
            "system_electronic": self.system_electronic,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VoiceCast":
        if not isinstance(data, dict):
            return cls()
        entries: Dict[str, str] = {}
        for k, v in (data.get("entries") or {}).items():
            if v is not None:
                entries[str(k)] = str(v)
        return cls(
            entries=entries,
            narrator_voice=str(data.get("narrator_voice", "") or "voice_narrator"),
            system_voice=str(data.get("system_voice", "") or "voice_system"),
            system_electronic=str(
                data.get("system_electronic", "") or "voice_system_electronic"
            ),
        )


@dataclass
class VoiceLine:
    """一行待 TTS 台词（Voice Cast 解析后的声音导演计划单元）。

    start_sec/end_sec 为 Phase 2 ffmpeg 混音的时间轴锚点（本次数据模型先留 None，
    混音层按对白在镜内的分布填充）。
    """

    text: str = ""
    speaker: str = ""
    voice_type: str = VoiceType.CHARACTER_DIALOGUE
    voice_id: str = ""
    emotion: str = ""
    delivery: str = ""
    shot_id: str = ""
    scene_id: str = ""
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "speaker": self.speaker,
            "voice_type": self.voice_type,
            "voice_id": self.voice_id,
            "emotion": self.emotion,
            "delivery": self.delivery,
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VoiceLine":
        if not isinstance(data, dict):
            return cls()
        return cls(
            text=str(data.get("text", "")),
            speaker=str(data.get("speaker", "")),
            voice_type=VoiceType.normalize(data.get("voice_type", "")),
            voice_id=str(data.get("voice_id", "")),
            emotion=str(data.get("emotion", "")),
            delivery=str(data.get("delivery", "")),
            shot_id=str(data.get("shot_id", "")),
            scene_id=str(data.get("scene_id", "")),
            start_sec=data.get("start_sec"),
            end_sec=data.get("end_sec"),
        )


@dataclass
class AudioIntent:
    """声音导演层计划（AudioIntent → Voice Cast → TTS Engine → ffmpeg 混音）。

    lines:      逐行 TTS 台词（voice_id 已解析，可直接喂 TTS Engine）
    voice_cast: 角色 → voice_id 扁平映射（本镜涉及的角色对白）
    ambient:    H3 环境音参考（DirectorIntent.audio 透传，混音时保留）
    music:      H3 BGM 参考（混音时保留）
    """

    shot_id: str = ""
    scene_id: str = ""
    lines: List[VoiceLine] = field(default_factory=list)
    voice_cast: Dict[str, str] = field(default_factory=dict)
    ambient: str = ""
    music: str = ""

    def has_voice(self) -> bool:
        return bool(self.lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "lines": [ln.to_dict() for ln in self.lines],
            "voice_cast": dict(self.voice_cast),
            "ambient": self.ambient,
            "music": self.music,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AudioIntent":
        if not isinstance(data, dict):
            return cls()
        return cls(
            shot_id=str(data.get("shot_id", "")),
            scene_id=str(data.get("scene_id", "")),
            lines=[VoiceLine.from_dict(ln) for ln in data.get("lines", [])],
            voice_cast={
                str(k): str(v)
                for k, v in (data.get("voice_cast") or {}).items()
            },
            ambient=str(data.get("ambient", "")),
            music=str(data.get("music", "")),
        )


def _collect_cast(lines: List[VoiceLine]) -> Dict[str, str]:
    """从已解析台词收集「角色 → voice_id」（仅角色对白，narration/system 不入）。"""
    cast: Dict[str, str] = {}
    for ln in lines:
        if ln.voice_type != VoiceType.CHARACTER_DIALOGUE or not ln.speaker:
            continue
        cast.setdefault(ln.speaker, ln.voice_id)
    return cast


def build_audio_intent(
    shot: Any,
    scene: Any,
    *,
    cast: Optional[VoiceCast] = None,
) -> AudioIntent:
    """从 Shot + Scene 派生声音导演计划（纯规则，零 LLM 零显存）。

    ⛔ 只读 shot/scene，不修改任何对象；AudioIntent 是派生数据，不落 ProductionPlan JSON。
    cast 缺省 = 空 VoiceCast（规则兜底 voice_id）。

    每行类型解析优先级（确定性）：
      1. 特殊说话人名（旁白/系统…）→ 强规则覆盖（narration/system_voice）；
      2. 其余 → Dialogue.type 显式值（inner_monologue/system_voice 显式生效）；
      3. 仍为 character_dialogue → 规则兜底。
    """
    audio = _audio_block(shot, scene)
    vc = cast or VoiceCast()
    shot_id = str(getattr(shot, "shot_id", "") or "")
    scene_id = str(getattr(scene, "scene_id", "") or "")
    lines: List[VoiceLine] = []
    for d in (shot.dialogue or []):
        text = (d.text or "").strip()
        if not text:
            continue
        spk = (d.speaker or "").strip()
        vt = infer_voice_type(spk, text)  # ① 特殊说话人名强规则
        if vt == VoiceType.CHARACTER_DIALOGUE:
            vt = VoiceType.normalize(d.type or "")  # ② 显式 Dialogue.type
        vid = (d.voice_id or "").strip() or vc.resolve(spk, vt, d.delivery)
        lines.append(VoiceLine(
            text=text,
            speaker=spk,
            voice_type=vt,
            voice_id=vid,
            emotion=(d.emotion or "").strip(),
            delivery=(d.delivery or "").strip(),
            shot_id=shot_id,
            scene_id=scene_id,
        ))
    return AudioIntent(
        shot_id=shot_id,
        scene_id=scene_id,
        lines=lines,
        voice_cast=_collect_cast(lines),
        ambient=str(audio.get("ambient", "") or ""),
        music=str(audio.get("music", "") or ""),
    )
