#!/usr/bin/env python3
"""③ SPA 真实验收《山雨客栈》：真实 ProductionPlan → SPA 前端可消费数据。

目标（用户 2026-08-11 拍板，顺序 ③→①→②）：先把「真实 Backend ProductionPlan →
SPA Model」这条边跑通并逐项核对，暂不做生成视频。

本脚本在**用户本机**跑（真实 Ollama qwen3:14b），复现 SPA「📜 导入剧本」后端链路：
  剧本 → 规则拆 Scene/Shot → Qwen 语义补全（analyze_script 单 session）
  → entity_cleanse + asset_matcher（实体清洗 + 资产匹配）
  → prompt_builder_v17（五区 AI 草稿 + 运镜模板）

产出（落盘到 frontend/fixtures/，供前端 vitest fixture 与 SPA 导入核对）：
  shanyu_real_plan.json    真实 ProductionPlan（含 entities/visual_elements）
  shanyu_real_matches.json 已确认资产绑定（auto，ConfirmedAssetMatch 形状）
  shanyu_real_drafts.json  五区 AI 草稿（PromptDraftItem 形状）

逐项断言（用户验收点，PASS/FAIL）：
  ① Scene 顺序 = 山雨楼外 → 客栈大堂 → 后院石阶；2+6+1=9 镜
  ② shot_id / source_text / duration_sec 逐字不变（Qwen 不决定镜头结构）
  ③ 角色（沈青崖/柳如烟/蒙面人）→ cast 匹配 auto（进 castIds）
  ④ 地点（山雨楼外/客栈大堂/后院石阶）→ location 匹配 auto（进 locationId）
  ⑤ 旧剑匣 → 已匹配；旧剑 → 未匹配（semantic_incompatible 剔除旧剑匣候选）
  ⑥ 视觉元素（山雾/晨光/灯火…）→ 只在 visual_elements，绝不进资产匹配
  ⑦ 每镜五区 AI 草稿 + 运镜措辞含明确运镜词（H3 硬规则）
  ⑧ 错误实体被排除（P0-3）：规则层/Qwen 后角色无「镜头一~九/称谓」伪实体；
     角色 matches 恰好 3、地点 matches 恰好 3 正式地点（山道/檐角/门口/柜台
     等 Shot 内空间描述不升级为 Location 资产）；registry invalid_dropped ≥ 1
  ⑨ AI Draft 内部标记不泄漏（P0-4）：五区措辞不含「镜头N/第N镜/shot_N/scene_N」，
     即使 visual_intent 混入「镜头二」也被清洗并保留正文（注入式验证）
  ⑩ 动作/空间短语不进实体名（P0-3b）：matches/entered 无「灯退到柜/剑走向门」类
     粘连名（纯名词规则）；注入「灯退到柜/剑走向门/拿着」→ 清洗保留 灯/剑、
     「拿着」丢弃（invalid_dropped 增 ≥1）；正式地点带后缀变体（后院石阶环境/
     客栈大堂环境/山雨楼建筑）不进 visual_elements

用法（Windows 本机，Ollama 需已启动且 `ollama list` 里有 qwen3:14b）：
  A. ComfyUI Desktop Python（推荐）：
     "D:\\Comfy-Desktop\\ComfyUI (1)\\ComfyUI\\.venv\\Scripts\\python.exe" tools\\spa_acceptance_shanyu.py
  B. 系统 Python：
     python tools\\spa_acceptance_shanyu.py
  离线自测（不连 Ollama，用内置 Fake 按《山雨客栈》内容补全）：
     python tools\\spa_acceptance_shanyu.py --mock \
        --script "<山雨客栈.md 路径>" --assets-root "<minimax_studio/assets 目录>"

退出码 0 = 全部 PASS；1 = 有 FAIL（FAIL 多为 Qwen 补全不全，见 P0-2）。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from director.production_plan import (  # noqa: E402
    Character,
    Entity,
    EntitySource,
    EntityType,
    ProductionPlan,
    Scene,
    Shot,
)
from director.script_parser import parse_script_file  # noqa: E402
from director.script_analyzer import analyze_script  # noqa: E402
from director.asset_matcher import match_plan, scan_asset_library  # noqa: E402
from director.asset_registry import AssetRegistry  # noqa: E402
from director.entity_cleanse import (  # noqa: E402
    _cleanse_action_phrase_name,
    build_entity_registry,
    is_invalid_entity_name,
)
from director.prompt_builder_v17 import (  # noqa: E402
    build_plan_drafts,
    build_shot_draft,
    load_template_registry,
)
from director.text_backends import TextBackend, create_default_text_backend  # noqa: E402

EXPECTED_SCENES = ("第一场：山雨楼外", "第二场：客栈大堂", "第三场：后院石阶")
EXPECTED_SHOTS_PER_SCENE = (2, 6, 1)
EXPECTED_TOTAL = 9
EXPECTED_LOCATIONS = ("山雨楼外", "客栈大堂", "后院石阶")
EXPECTED_ROLES = ("沈青崖", "柳如烟", "蒙面人")
KEY_PROPS = {"旧剑匣": "auto", "旧剑": "none"}
VISUAL_ELEMENT_NAMES = ("山雾", "晨光", "暖黄灯火", "夜风", "寒气", "烛火", "寒光", "湿石阶")
CAMERA_MOVEMENT_WORDS = (
    "推", "拉", "摇", "移", "升降", "环绕", "俯", "仰",
    "跟", "转", "弧线", "推进", "拉远", "运动", "特写",
)

# P0-4（2026-08-11 用户拍板）：AI Draft 五区措辞不得含内部标记（镜头编号/shot_id/scene_id）。
# 与 prompt_sanitize.INTERNAL_MARKER_PATTERNS 语义一致；「远景镜头」等正文含义不误伤。
_INTERNAL_MARKER_RE = re.compile(
    r"(?:镜头\s*[一二三四五六七八九十百\d]+|第\s*[一二三四五六七八九十百\d]+\s*镜|"
    r"shot[\s_\-]*\d+|scene[\s_\-]*\d+)",
    re.I,
)

FIXTURES_DIR = os.path.join(_REPO_ROOT, "frontend", "fixtures")

# ---------------------------------------------------------------------------
# 路径解析（与 golden_path_script_test.py 一致）
# ---------------------------------------------------------------------------
STUDIO_SUBDIR = os.path.join("minimax_studio", "scripts")
ASSETS_SUBDIR = os.path.join("minimax_studio", "assets")


def _resolve_script_path(explicit: str) -> str:
    cands = [explicit, os.environ.get("SHAN_YU_SCRIPT", "")]
    try:
        import folder_paths  # noqa: PLC0415

        cands.append(os.path.join(folder_paths.get_input_directory(), STUDIO_SUBDIR, "山雨客栈.md"))
    except Exception:
        pass
    cands += [
        r"D:\Comfy-Desktop\ComfyUI-Shared\input\minimax_studio\scripts\山雨客栈.md",
        os.path.join(_REPO_ROOT, "..", "ComfyUI-Shared", "input", "minimax_studio",
                     "scripts", "山雨客栈.md"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "找不到《山雨客栈》剧本，请用 --script 指定，或设 SHAN_YU_SCRIPT 环境变量。"
        "候选: " + " | ".join(repr(c) for c in cands if c)
    )


def _resolve_assets_root(explicit: str) -> str:
    cands = [explicit, os.environ.get("MINIMAX_STUDIO_ASSETS", "")]
    try:
        import folder_paths  # noqa: PLC0415

        cands.append(os.path.join(folder_paths.get_input_directory(), ASSETS_SUBDIR))
    except Exception:
        pass
    cands += [
        r"D:\Comfy-Desktop\ComfyUI-Shared\input\minimax_studio\assets",
        os.path.join(_REPO_ROOT, "..", "ComfyUI-Shared", "input", "minimax_studio", "assets"),
    ]
    for c in cands:
        if c and os.path.isdir(c):
            return c
    raise FileNotFoundError(
        "找不到共享资产库，请用 --assets-root 指定，或设 MINIMAX_STUDIO_ASSETS 环境变量。"
    )


# ---------------------------------------------------------------------------
# 内置 Fake（--mock 离线自测）：按《山雨客栈》真实内容补全，验证脚本本身正确
# ---------------------------------------------------------------------------
_FAKE_QWEN = {
    1: {"script_facts": {"characters": [], "props": [], "entities": [
        {"name": "山雨楼外", "type": "location", "source": "script", "confidence": 0.97}]},
        "visual_interpretation": {"visual_elements": [
            {"name": "山雾", "type": "effect", "confidence": 0.9},
            {"name": "暖黄灯火", "type": "effect", "confidence": 0.85},
            {"name": "湿石阶", "type": "effect", "confidence": 0.7}],
            "actions": [], "emotion": "宁静"},
        "dialogue": [],
        "visual_intent": "远景建立：雨歇黄昏，山雾漫向客栈，檐角昏黄灯笼，湿石阶映暖光，老槐树滴水珠。"},
    2: {"script_facts": {"characters": [{"name": "沈青崖"}], "props": [], "entities": [
        {"name": "沈青崖", "type": "character", "source": "script", "confidence": 0.98},
        {"name": "山雨楼外", "type": "location", "source": "script", "confidence": 0.95},
        {"name": "旧剑匣", "type": "prop", "source": "script", "confidence": 0.92}]},
        "visual_interpretation": {"visual_elements": [{"name": "山雾", "type": "effect", "confidence": 0.65}],
            "actions": ["走来", "停步", "望", "推门"], "emotion": "平静带戒备"},
        "dialogue": [],
        "visual_intent": "沈青崖负旧剑匣从山道走来，石阶停步望檐角灯笼，低头推门。"},
    3: {"script_facts": {"characters": [{"name": "沈青崖"}, {"name": "柳如烟"}], "props": [], "entities": [
        {"name": "沈青崖", "type": "character", "source": "script", "confidence": 0.98},
        {"name": "柳如烟", "type": "character", "source": "script", "confidence": 0.98},
        {"name": "客栈大堂", "type": "location", "source": "script", "confidence": 0.95},
        {"name": "青瓷碗", "type": "prop", "source": "script", "confidence": 0.8}]},
        "visual_interpretation": {"visual_elements": [{"name": "暖黄灯火", "type": "effect", "confidence": 0.9}],
            "actions": ["推门走进", "低头擦拭", "抬眼"], "emotion": "目光相接，安静片刻"},
        "dialogue": [],
        "visual_intent": "沈青崖推门进大堂，暖黄灯火扑面，柜台后柳如烟擦碗抬眼，二人目光相接。"},
    4: {"script_facts": {"characters": [{"name": "柳如烟"}], "props": [], "entities": [
        {"name": "柳如烟", "type": "character", "source": "script", "confidence": 0.98}]},
        "visual_interpretation": {"visual_elements": [{"name": "暖黄灯火", "type": "effect", "confidence": 0.85}],
            "actions": ["放下茶碗", "绕过柜台", "端茶", "走向堂中"], "emotion": "平淡"},
        "dialogue": [{"speaker": "柳如烟", "text": "客官，落座歇脚，茶先暖着。"}],
        "visual_intent": "柳如烟放碗绕过柜台，端热茶走向堂中，语气平淡招呼。"},
    5: {"script_facts": {"characters": [{"name": "沈青崖"}], "props": [], "entities": [
        {"name": "沈青崖", "type": "character", "source": "script", "confidence": 0.98},
        {"name": "旧剑", "type": "prop", "source": "script", "confidence": 0.85}]},
        "visual_interpretation": {"visual_elements": [],
            "actions": ["落座", "接过茶碗", "敲桌面", "目光落在"], "emotion": "压抑试探"},
        "dialogue": [{"speaker": "沈青崖", "text": "这里…可曾收过一把旧剑？"}],
        "visual_intent": "沈青崖落座接碗不喝，指节轻敲桌面，目光落在空剑架，低声询问。"},
    6: {"script_facts": {"characters": [{"name": "蒙面人"}], "props": [], "entities": [
        {"name": "蒙面人", "type": "character", "source": "script", "confidence": 0.98},
        {"name": "客栈大堂", "type": "location", "source": "script", "confidence": 0.9}]},
        "visual_interpretation": {"visual_elements": [
            {"name": "夜风", "type": "effect", "confidence": 0.8},
            {"name": "寒气", "type": "effect", "confidence": 0.75},
            {"name": "烛火", "type": "effect", "confidence": 0.7}],
            "actions": ["推开", "灌入", "立在门口"], "emotion": "压迫"},
        "dialogue": [],
        "visual_intent": "客栈大门砰然被推开，夜风灌入，蒙面人逆光立门口，黑纱蒙面腰悬窄刀，烛火乱晃。"},
    7: {"script_facts": {"characters": [{"name": "蒙面人"}, {"name": "沈青崖"}, {"name": "柳如烟"}], "props": [], "entities": [
        {"name": "蒙面人", "type": "character", "source": "script", "confidence": 0.98},
        {"name": "沈青崖", "type": "character", "source": "script", "confidence": 0.97},
        {"name": "柳如烟", "type": "character", "source": "script", "confidence": 0.97}]},
        "visual_interpretation": {"visual_elements": [{"name": "烛火", "type": "effect", "confidence": 0.6}],
            "actions": ["走近", "起身", "横剑", "退后半步", "按住"], "emotion": "对峙"},
        "dialogue": [{"speaker": "蒙面人", "text": "剑，交出来。"}],
        "visual_intent": "蒙面人步步走近沉声要剑，沈青崖起身横剑护柳如烟，柳如烟退步按柜台下短刃，三人三角对峙。"},
    8: {"script_facts": {"characters": [{"name": "蒙面人"}, {"name": "沈青崖"}], "props": [], "entities": [
        {"name": "蒙面人", "type": "character", "source": "script", "confidence": 0.97},
        {"name": "沈青崖", "type": "character", "source": "script", "confidence": 0.97},
        {"name": "旧剑", "type": "prop", "source": "script", "confidence": 0.85},
        {"name": "烛台", "type": "prop", "source": "script", "confidence": 0.75},
        {"name": "酒坛", "type": "prop", "source": "script", "confidence": 0.7}]},
        "visual_interpretation": {"visual_elements": [{"name": "寒光", "type": "effect", "confidence": 0.8}],
            "actions": ["拔刀跃起", "侧身避过", "出鞘", "短兵相接"], "emotion": "激烈打斗"},
        "dialogue": [],
        "visual_intent": "蒙面人拔刀跃起，沈青崖侧身避过旧剑出鞘寒光一闪，短兵相接，烛台打翻酒坛碎裂，柳如烟退后按刃戒备。"},
    9: {"script_facts": {"characters": [{"name": "沈青崖"}, {"name": "柳如烟"}], "props": [], "entities": [
        {"name": "沈青崖", "type": "character", "source": "script", "confidence": 0.98},
        {"name": "柳如烟", "type": "character", "source": "script", "confidence": 0.97},
        {"name": "后院石阶", "type": "location", "source": "script", "confidence": 0.95},
        {"name": "旧剑匣", "type": "prop", "source": "script", "confidence": 0.9},
        {"name": "旧剑", "type": "prop", "source": "script", "confidence": 0.8}]},
        "visual_interpretation": {"visual_elements": [
            {"name": "晨光", "type": "effect", "confidence": 0.9},
            {"name": "山雾", "type": "effect", "confidence": 0.6}],
            "actions": ["坐着拭剑", "放在膝边", "端来", "放在身侧", "转身回屋"], "emotion": "无言"},
        "dialogue": [],
        "visual_intent": "天明雨停，后院石阶沈青崖拭剑旧剑匣放膝边，柳如烟端茶放身侧无言转身回屋，晨光洒落。"},
}


class FakeTextBackend(TextBackend):
    """按《山雨客栈》内容返回 Qwen 语义补全（离线自测脚本本身）。

    注意：analyze_scene 的批量 prompt 每场景内从 [1] 重新编号（场景1=[1..2]，
    场景2=[1..6]，场景3=[1]）。这里用 _offset 累计「已处理镜头数」把场景内序号
    映射回 _FAKE_QWEN 的全局镜头 key（1..9），避免场景 2/3 错配。
    """

    name = "fake"

    def __init__(self) -> None:
        self.base_url = "mock://fake"
        self.model = "fake"
        self._offset = 0

    def ping(self, timeout: float = 10.0) -> bool:
        return True

    def loaded_models(self) -> list[str]:
        return []

    def _api(self, path, payload=None, *, method="POST", timeout=None):
        return {"models": [{"name": "qwen3:14b"}]}

    def analyze_text(self, prompt, *, max_tokens=1024, temperature=0.0) -> str:
        nums = []
        for line in prompt.splitlines():
            m = re.match(r"^\[\s*(\d+)\s*\]\s*(.*)$", line)
            if m:
                nums.append(int(m.group(1)))
        if not nums:
            data = dict(_FAKE_QWEN[self._offset + 1], index=1)
            self._offset += 1
            return json.dumps(data, ensure_ascii=False)
        n = max(nums)
        shots = []
        for i in range(1, n + 1):
            global_i = self._offset + i
            data = dict(_FAKE_QWEN.get(global_i, _FAKE_QWEN[1]))
            data["index"] = i
            shots.append(data)
        self._offset += n
        return json.dumps({"shots": shots}, ensure_ascii=False)

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 断言收集
# ---------------------------------------------------------------------------
PASS_N = [0]
FAIL_N = [0]


def check(ok: bool, label: str) -> None:
    if ok:
        PASS_N[0] += 1
        print(f"  ✓ PASS  {label}")
    else:
        FAIL_N[0] += 1
        print(f"  ✗ FAIL  {label}")


def _m(res: dict, name: str):
    return next((x for x in res["matches"] if x["name"] == name), None)


def run_checks(script_path: str, assets_root: str, *, mock: bool) -> int:
    print("=" * 72)
    print("③ SPA 真实验收《山雨客栈》：Backend ProductionPlan → SPA Model")
    print("=" * 72)

    rule_plan = parse_script_file(script_path)
    backend: TextBackend = FakeTextBackend() if mock else create_default_text_backend()
    print(f"\n[链路] 剧本: {os.path.basename(script_path)}")
    print(f"[链路] 后端: {backend.name} model={getattr(backend, 'model', '?')}")
    print(f"[链路] 资产库: {assets_root}")

    if not mock:
        ok = backend.ping()
        check(ok, f"Ollama ping: {backend.name}")
        if not ok:
            print("Ollama 未响应，请先 ollama serve。")
            return 1

    # ① 规则拆 Scene/Shot（结构不变性基准）
    print("\n--- ① 规则拆 Scene/Shot ---")
    got_scenes = [sc.title for sc in rule_plan.scenes]
    got_counts = [len(sc.shots) for sc in rule_plan.scenes]
    check(got_scenes == list(EXPECTED_SCENES), f"Scene 顺序 {got_scenes} == {list(EXPECTED_SCENES)}")
    check(got_counts == list(EXPECTED_SHOTS_PER_SCENE), f"每场景镜头数 {got_counts} == {list(EXPECTED_SHOTS_PER_SCENE)}")
    check(sum(got_counts) == EXPECTED_TOTAL, f"总镜头 {sum(got_counts)} == {EXPECTED_TOTAL}")
    check(all(sc.location_name in EXPECTED_LOCATIONS for sc in rule_plan.scenes),
          f"location_name ∈ {list(EXPECTED_LOCATIONS)}")
    rule_flat = [(sc.scene_id, sh.shot_id, sh.source_text, sh.duration_sec)
                 for sc in rule_plan.scenes for sh in sc.shots]

    # ② Qwen 语义补全（analyze_script 单 session，显存安全门）
    print("\n--- ② Qwen 语义补全（单 session，用完即卸载）---")
    qwen_plan = analyze_script(rule_plan, backend)
    check(not qwen_plan.validation.warnings or all("保留规则" in w or "失败" in w or "JSON" in w
                                                   for w in qwen_plan.validation.warnings),
          f"validation.warnings 仅记录分析失败/降级（{len(qwen_plan.validation.warnings)} 条）")
    qwen_flat = [(sc.scene_id, sh.shot_id, sh.source_text, sh.duration_sec)
                 for sc in qwen_plan.scenes for sh in sc.shots]
    check(qwen_flat == rule_flat,
          "shot_id / source_text / duration_sec 逐字不变（Qwen 不决定镜头结构）")
    check(got_scenes == [sc.title for sc in qwen_plan.scenes], "Scene 顺序 Qwen 后不变")

    # ③④⑤⑥ 实体清洗 + 资产匹配
    print("\n--- ③④⑤⑥ 实体清洗 + 资产匹配 ---")
    library = scan_asset_library(assets_root)
    res = match_plan(qwen_plan, library=library)
    matches = res["matches"]
    funnel = res["funnel"]
    print(f"  [漏斗] discovered={funnel.get('discovered')} cleansed={funnel.get('cleansed')} "
          f"entered={funnel.get('entered')} auto={funnel.get('auto')} pending={funnel.get('pending')} "
          f"none={funnel.get('none')}")
    for m in matches:
        print(f"    [{m['status']:7s}] {m['name']:8s} {m['entity_type']:9s} conf={m['entity_confidence']:.2f} "
              f"→ {m.get('asset_name')}")

    for role in EXPECTED_ROLES:
        mm = _m(res, role)
        check(mm and mm["status"] == "auto",
              f"角色 {role} → cast auto 匹配（{mm.get('asset_name') if mm else '未匹配'}）")
    for loc in EXPECTED_LOCATIONS:
        mm = _m(res, loc)
        check(mm and mm["status"] == "auto",
              f"地点 {loc} → location auto 匹配（{mm.get('asset_name') if mm else '未匹配'}）")
    jx = _m(res, "旧剑匣")
    check(jx and jx["status"] == "auto", f"旧剑匣 → 已匹配（{jx.get('asset_name') if jx else '未匹配'}）")
    jian = _m(res, "旧剑")
    check(jian and jian["status"] == "none",
          f"旧剑 → 未匹配（semantic_incompatible 剔除旧剑匣候选）{'' if jian and jian['status']=='none' else '（旧剑匣候选未被剔除！）'}")

    ve_names = {v["name"] for v in res["visual_elements"]}
    check(bool(ve_names), f"visual_elements 非空（{sorted(ve_names)[:4]}…）")
    check(not any(m["entity_type"] == "effect" for m in matches),
          "视觉元素（effect）绝不进入资产匹配 matches")
    leak = [v for v in VISUAL_ELEMENT_NAMES if v in ve_names]
    check(len(leak) >= 1, f"预期视觉元素至少 1 项在清单（{sorted(leak)}）")
    check(
        not (set(EXPECTED_LOCATIONS) & ve_names),
        f"P0-2 正式 Scene 地点不再进 visual_elements（重复={sorted(set(EXPECTED_LOCATIONS) & ve_names)}）",
    )

    # ⑦ 五区 AI 草稿 + 运镜
    print("\n--- ⑦ 五区 AI 草稿 + 运镜 ---")
    drafts_res = build_plan_drafts(qwen_plan)
    drafts = drafts_res.get("drafts", [])
    check(len(drafts) == EXPECTED_TOTAL, f"drafts 每镜一条（{len(drafts)} == {EXPECTED_TOTAL}）")
    cam_words_ok = all(
        any(w in (d.get("draft", {}).get("camera", "") or "") for w in CAMERA_MOVEMENT_WORDS)
        for d in drafts
    )
    check(cam_words_ok, "每镜 camera 措辞含明确运镜词（H3 硬规则，无固定机位）")
    five_ok = all(
        all(k in d.get("draft", {}) for k in ("visual", "camera", "style", "sound", "negative"))
        for d in drafts
    )
    check(five_ok, "每镜草稿五区齐全（visual/camera/style/sound/negative）")

    # ⑧ 错误实体被排除（P0-3 核心验收点：镜头标记/称谓/空间描述不得升级）
    print("\n--- ⑧ 错误实体被排除（P0-3）---")
    rule_chars = {
        c.name for sc in rule_plan.scenes for sh in sc.shots for c in sh.characters
    }
    check(
        not any(is_invalid_entity_name(n) for n in rule_chars),
        f"规则层角色无伪实体（{sorted(rule_chars)}）",
    )
    check(
        set(EXPECTED_ROLES) <= rule_chars,
        f"P0-2 双轨制：规则层角色兜底含 3 核心角色（{sorted(rule_chars)} ⊇ {sorted(EXPECTED_ROLES)}）",
    )
    qwen_chars = {
        c.name for sc in qwen_plan.scenes for sh in sc.shots for c in sh.characters
    }
    check(
        not any(is_invalid_entity_name(n) for n in qwen_chars),
        f"Qwen 补全后角色无伪实体（{sorted(qwen_chars)}）",
    )
    check(
        not any(is_invalid_entity_name(m["name"]) for m in matches),
        "matches 无镜头标记/称谓伪实体",
    )
    char_matches = [m for m in matches if m["entity_type"] == "character"]
    check(
        {m["name"] for m in char_matches} == set(EXPECTED_ROLES),
        f"角色 matches 恰好 3（{sorted(m['name'] for m in char_matches)} == {sorted(EXPECTED_ROLES)}）",
    )
    loc_matches = [m for m in matches if m["entity_type"] == "location"]
    check(
        {m["name"] for m in loc_matches} == set(EXPECTED_LOCATIONS),
        f"地点 matches 恰好 3 正式地点（{sorted(m['name'] for m in loc_matches)} == {sorted(EXPECTED_LOCATIONS)}）",
    )
    shot_space = ("山道", "檐角", "门口", "柜台", "桌边")
    check(
        all(m["name"] not in shot_space for m in loc_matches),
        "Shot 内空间描述（山道/檐角/门口/柜台/桌边）不升级为 Location 资产",
    )
    reg = build_entity_registry(qwen_plan)
    entered_names = [e["name"] for e in reg["entries"] if e["entered"]]
    check(
        not any(is_invalid_entity_name(n) for n in entered_names),
        f"registry entered 无伪实体（{sorted(entered_names)}）",
    )
    # 注入式验证清洗能力：把 Qwen 幻觉的「镜头一/镜头七/客官」塞进 shot.entities
    # （正是真实 SPA 报告里「角色 11」的污染路径）→ 注册表必须丢弃、invalid_dropped 增加。
    polluted = copy.deepcopy(qwen_plan)
    polluted.scenes[0].shots[0].entities.extend(
        [
            Entity(name="镜头一", type=EntityType.CHARACTER,
                   source=EntitySource.SCRIPT, confidence=0.95),
            Entity(name="镜头七", type=EntityType.CHARACTER,
                   source=EntitySource.SCRIPT, confidence=0.95),
            Entity(name="客官", type=EntityType.CHARACTER,
                   source=EntitySource.SCRIPT, confidence=0.9),
        ]
    )
    polluted_reg = build_entity_registry(polluted)
    polluted_entered = [e["name"] for e in polluted_reg["entries"] if e["entered"]]
    check(
        not any(n in polluted_entered for n in ("镜头一", "镜头七", "客官")),
        f"注入污染 → entered 无镜头一/镜头七/客官（{sorted(polluted_entered)}）",
    )
    check(
        polluted_reg["funnel"].get("invalid_dropped", 0) >= 3,
        f"注入污染 → 漏斗 invalid_dropped ≥ 3（实际 {polluted_reg['funnel'].get('invalid_dropped', 0)}）",
    )

    # ⑨ AI Draft 内部标记不泄漏（P0-4：shot_id/镜头编号不得进五区措辞）
    print("\n--- ⑨ AI Draft 内部标记不泄漏（P0-4）---")
    marker_leak = [
        (d["scene_id"], d["shot_id"], sec, text)
        for d in drafts
        for sec, text in d.get("draft", {}).items()
        if sec in ("visual", "camera", "style", "sound")
        and _INTERNAL_MARKER_RE.search(str(text))
    ]
    check(not marker_leak, f"真实链路 9 镜五区措辞无内部标记（{marker_leak[:2]}）")
    # 注入式验证：即使 Qwen 把「镜头二」混入 visual_intent / characters，也清洗不泄漏。
    dirty = Shot(
        shot_id="shot_00", source_text="注入测试", duration_sec=5,
        characters=[Character(name="沈青崖"), Character(name="镜头一")],
        entities=[], props=[], actions=[], emotion="", dialogue=[],
        visual_intent="中景，缓慢推近镜头二、沈青崖，雨幕中凝望",
    )
    dirty_scene = Scene(scene_id="scene_00", title="注入", location_name="山雨楼外",
                        time="黄昏", weather="雨", shots=[dirty])
    tmpl = load_template_registry()
    dd = build_shot_draft(dirty, dirty_scene, tmpl, has_prev=True, is_last_shot=False)
    _clean = all(
        not _INTERNAL_MARKER_RE.search(str(dd[k]))
        for k in ("visual", "camera", "style", "sound")
    )
    check(_clean, f"注入「镜头二/镜头一」→ 草稿无内部标记（visual={dd['visual']!r}）")
    check("沈青崖" in dd["visual"], f"注入清洗后保留正文「沈青崖」（{dd['visual']!r}）")

    # ⑩ 动作/空间短语不进实体名（P0-3b：纯名词规则，无黑名单）
    print("\n--- ⑩ 动作/空间短语不进实体名（P0-3b）---")

    def _is_verb_contaminated(name: str) -> bool:
        cleaned = _cleanse_action_phrase_name(name)
        return cleaned is not None and cleaned != name

    contaminated_matches = [m["name"] for m in matches if _is_verb_contaminated(m["name"])]
    check(
        not contaminated_matches,
        f"matches 无动作短语实体名（{contaminated_matches or '无'}）",
    )
    contaminated_entered = [
        e["name"] for e in reg["entries"]
        if e["entered"] and _is_verb_contaminated(e["name"])
    ]
    check(
        not contaminated_entered,
        f"registry entered 无动作短语实体名（{contaminated_entered or '无'}）",
    )
    # 注入式验证：把「灯退到柜/剑走向门/拿着」塞进 shot.entities——
    # 正是真实 SPA 报告「灯退到柜 道具 90% ✗ 未找到」的污染路径。
    polluted2 = copy.deepcopy(qwen_plan)
    polluted2.scenes[0].shots[0].entities.extend(
        [
            Entity(name="灯退到柜", type=EntityType.PROP,
                   source=EntitySource.SCRIPT, confidence=0.9),
            Entity(name="剑走向门", type=EntityType.PROP,
                   source=EntitySource.SCRIPT, confidence=0.9),
            Entity(name="拿着", type=EntityType.PROP,
                   source=EntitySource.SCRIPT, confidence=0.9),
        ]
    )
    polluted2_reg = build_entity_registry(polluted2)
    polluted2_entered = [e["name"] for e in polluted2_reg["entries"] if e["entered"]]
    check(
        not any(_is_verb_contaminated(n) for n in polluted2_entered),
        f"注入动作短语 → entered 无粘连名（{sorted(polluted2_entered)}）",
    )
    check(
        "灯" in polluted2_entered or "剑" in polluted2_entered,
        f"注入动作短语 → 清洗保留名词核心 灯/剑（entered={sorted(polluted2_entered)}）",
    )
    base_invalid = reg["funnel"].get("invalid_dropped", 0)
    check(
        polluted2_reg["funnel"].get("invalid_dropped", 0) >= base_invalid + 1,
        f"注入动作短语 → 漏斗 invalid_dropped 增 ≥1（{base_invalid}→{polluted2_reg['funnel'].get('invalid_dropped', 0)}）",
    )
    # 正式地点带中文类型后缀的变体不得进 visual_elements（带后缀的正式地点=重复）
    ve_names_all = [v["name"] for v in reg["visual_elements"]]
    for suffixed in ("后院石阶环境", "客栈大堂环境", "山雨楼建筑"):
        check(
            suffixed not in ve_names_all,
            f"正式地点后缀变体 {suffixed} 不在 visual_elements（{sorted(ve_names_all)}）",
        )

    # ⑪ Alias 归一 + Persisted 复用（Phase 2-2 commit 2+3：跨解析稳定绑定验收）
    print("\n--- ⑪ Alias 归一 + Persisted 复用（Phase 2-2）---")
    tmp_root = tempfile.mkdtemp(prefix="reg_accept_")
    reg_p = AssetRegistry(tmp_root).load()
    # 第一次导入：当前真实 qwen_plan → 全量 auto → 模拟用户在 SPA 人工确认全部落盘
    res1 = match_plan(qwen_plan, library=library, registry=reg_p)
    auto_ek = {}
    for m0 in res1["matches"]:
        if m0["status"] == "auto" and m0.get("asset_id"):
            auto_ek[m0["entity_key"]] = m0["asset_id"]
    for ek, aid in auto_ek.items():
        reg_p.set_binding(ek, aid, status="accepted", source="user")
    reg_p.save()
    n1 = len(reg_p.list_bindings())
    check(n1 >= 1, f"第一次导入：确认落盘 {n1} 个 binding（≥1）")
    # 第二次导入：把角色实体换成称谓变体 + 全新 entity_id → 全部 persisted 复用
    alias_plan = copy.deepcopy(qwen_plan)
    for sc2 in alias_plan.scenes:
        for sh2 in sc2.shots:
            for e2 in sh2.entities:
                e2.entity_id = f"ent_2_{e2.name}"  # 模拟下次解析全新临时 id
                if e2.type == EntityType.CHARACTER:
                    if e2.name == "柳如烟":
                        e2.name = "柳姑娘"
                    elif e2.name == "沈青崖":
                        e2.name = "沈大侠"
    res2 = match_plan(alias_plan, library=library, registry=reg_p)
    names2 = [m["name"] for m in res2["matches"]]
    check("柳姑娘" not in names2 and "沈大侠" not in names2,
          f"第二次导入 matches 无称谓变体（{sorted(names2)}）")
    persisted2 = [m for m in res2["matches"] if m.get("match_kind") == "persisted"]
    check(len(persisted2) == n1,
          f"第二次导入 {len(persisted2)}/{n1} 条 persisted 复用（Qwen 变体不重新 pending）")
    n2 = len(reg_p.list_bindings())
    check(n2 == n1, f"bindings 数量不变（{n1} → {n2}，变体不产生重复绑定）")

    # 落盘 fixtures（真实形状，供前端 vitest fixture 与 SPA 导入核对）
    os.makedirs(FIXTURES_DIR, exist_ok=True)
    plan_json = qwen_plan.to_dict()
    confirmed = [
        {"kind": m["kind"], "name": m["name"], "asset_name": m.get("asset_name"),
         "image_file": m.get("image_file")}
        for m in matches if m["status"] == "auto" and m.get("image_file")
    ]
    for fname, payload in (
        ("shanyu_real_plan.json", plan_json),
        ("shanyu_real_matches.json", confirmed),
        ("shanyu_real_drafts.json", drafts_res),
    ):
        path = os.path.join(FIXTURES_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  💾 落盘 fixtures/{fname}（{len(payload) if isinstance(payload, list) else 'object'}）")

    # 浏览器核对清单
    print("\n" + "=" * 72)
    print("浏览器核对清单（SPA「📜 导入剧本」→ 走查）")
    print("=" * 72)
    print("""  1. 导入《山雨客栈.md》→ 弹窗应显示 3 场景 / 9 镜 / 角色 3（无「镜头一~九」）。
  2. Scene 顺序 = 山雨楼外 → 客栈大堂 → 后院石阶；Shot 2+6+1=9。
  3. 每镜「📜 剧本原文」对照区 = visual_intent 备注 + 逐字原文。
  4. 资产匹配：沈青崖/柳如烟/蒙面人/山雨楼外/客栈大堂/后院石阶/旧剑匣 → 已匹配；
     旧剑/青瓷碗/烛台/酒坛 → 未匹配（不自动创建资产）。
  5. 视觉元素（山雾/晨光/灯火/寒气…）不出现在资产列表，只在 Prompt 草稿。
  6. 逐镜采纳 → 9 镜进入人工审核态；改 Prompt 变橙 → 重生成草稿。
  7. AI Draft 措辞无「镜头N/第N镜/shot_N/scene_N」内部标记（P0-4）。
  8. 暂不做生成视频 —— 只验证 Backend ProductionPlan → SPA Model 这条边。
  9. 第二次导入《山雨客栈》：已确认的角色绑定显示蓝色「✓ 已确认」persisted 复用，
     不重新 pending；即使 Qwen 把角色写成「柳姑娘/沈大侠」也不产生重复绑定（alias 归一）。""")

    print("\n" + "=" * 72)
    total = PASS_N[0] + FAIL_N[0]
    print(f"结果: {PASS_N[0]} PASS / {FAIL_N[0]} FAIL（共 {total}）")
    if FAIL_N[0]:
        print("注意：FAIL 多为 Qwen 补全不全（P0-2 已知：9 镜仅 1/9 完整补全）。")
        print("链路本身（规则拆镜/匹配/草稿）已验证；SPA 导入仍可进行，缺字段会在 UI 显现。")
    return 0 if FAIL_N[0] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="③ SPA 真实验收《山雨客栈》")
    ap.add_argument("--mock", action="store_true", help="离线自测（内置 Fake，不连 Ollama）")
    ap.add_argument("--script", default="", help="剧本文件路径")
    ap.add_argument("--assets-root", default="", help="资产库目录")
    args = ap.parse_args()
    script_path = _resolve_script_path(args.script)
    assets_root = _resolve_assets_root(args.assets_root)
    return run_checks(script_path, assets_root, mock=args.mock)


if __name__ == "__main__":
    sys.exit(main())
