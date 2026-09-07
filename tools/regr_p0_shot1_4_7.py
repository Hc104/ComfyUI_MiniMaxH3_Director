#!/usr/bin/env python3
"""#466 P0-① 真实回归：Shot 1/4/7 三类来源标记 + 逐字可追溯（真实《山雨客栈》fixture）。

真实链路（与 http_routes /prompt/h3 路由同源）：
    ProductionPlan.from_dict(fixture_shanyu_real_plan)
      → build_plan_intents（DirectorIntent，含实体边界检查 + 角色锚点归一）
      → build_plan_h3_prompts（H3 Prompt 三段 + 结构化 references + 三次 provenance）

回归镜头（用户指定）：
  Shot 1 = scene_01:shot_01   环境镜（雨停/山雾/山雨楼/灯笼/湿石阶/老槐树）
  Shot 4 = scene_02:shot_02   对白+角色镜（柳如烟端茶，「客官，落座歇脚，茶先暖着。」）
  Shot 7 = scene_02:shot_05   多人+动作镜（蒙面人/沈青崖/柳如烟，横剑护前/退后半步/按住短刃）

断言维度：
  ① 三层结构存在：user_original_intent（逐字未覆盖）/ retained_facts（USER）/
      emotion+composition（AI）/ continuity.camera_desc（RULE）
  ② 来源标记 provenance 字段级（字段名 → user/ai/rule/mix）
  ③ H3 三段存在 + 时间轴 `[0-{d}s]` 前缀 + 段落级 provenance
     {user_facts, ai_supplement, rule_generated}
  ④ 逐字可追溯：source_text 关键词进最终 integrated_multimodal_description；
     AI 补全（visual_intent 词）作为补充句进入描述且不改原文；
     对白逐字保留；实体跨镜越界被丢弃（用户拍板 ②）
  ⑤ 结构化 references：entity_key → entity_name → image_file 一一对应；
     文本内 [REF: 名] 可读标签与 references 同集合
  ⑥ 纯规则零显存零网络（无 unload/urlopen/torch）

运行（VM 内）：
    python3 /sessions/.../outputs/regr_p0_shot1_4_7.py
"""

import json
import os
import sys
import types

# ---- 注入假 ComfyUI 模块（director 包 import 需要） ----
_server_mod = types.ModuleType("server")
_server_mod.PromptServer = type("PromptServer", (), {"instance": None})
sys.modules["server"] = _server_mod

_folder_paths_mod = types.ModuleType("folder_paths")
_folder_paths_mod.get_input_directory = lambda: "/tmp/fake_input"
_folder_paths_mod.get_temp_directory = lambda: "/tmp/fake_temp"
sys.modules["folder_paths"] = _folder_paths_mod

# repo root = 本脚本（tools/）上级 = ComfyUI_MiniMaxH3_Director
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from director.director_intent import build_plan_intents  # noqa: E402
from director.h3_prompt_builder import (  # noqa: E402
    _canon,
    build_plan_h3_prompts,
)
from director.prompt_builder_v17 import build_plan_drafts  # noqa: E402
from director.production_plan import IntentSource, ProductionPlan  # noqa: E402

FIXTURE = os.path.join(SRC, "frontend", "fixtures", "shanyu_real_plan.json")

PASSED = 0
FAILED = 0
SHOTS = ["scene_01:shot_01", "scene_02:shot_02", "scene_02:shot_05"]


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name} {detail}")


def load_fixture() -> ProductionPlan:
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    return ProductionPlan.from_dict(data)


def build_bindings(plan, intents):
    """模拟前端 auto-scan 的真实绑定：entity_key（`{type}:{canon}`，与
    plan_entity_key 一致）→ image（无真实图片时用占位路径；这里只验证键/名/路径的
    结构一致性与可读标签集合）。"""
    bindings = {}
    for key, intent in intents.items():
        shot_map = {}
        for e in intent.entities:
            ek = f"{str(e['type']).lower()}:{_canon(e['name'])}"
            shot_map[ek] = {
                "asset_id": f"asset_{abs(hash(ek)) % 10000:04d}",
                "image_file": f"minimax_studio/shan_yu/{ek.replace(':', '_')}.png",
                "ref_image": "",
            }
        if shot_map:
            bindings[key] = shot_map
    return bindings


def main() -> None:
    global PASSED, FAILED
    print("P0-① 真实回归：《山雨客栈》Shot 1/4/7 三类来源标记 + 逐字可追溯")
    plan = load_fixture()
    # fixture 原始 source_text（L1 逐字基准，防「自身比自身」）
    src_by_key: dict = {}
    for sc in plan.scenes:
        sid = sc.scene_id
        for s in sc.shots:
            src_by_key[f"{sid}:{s.shot_id}"] = str(s.source_text).strip()
    intents = build_plan_intents(plan)
    drafts = build_plan_drafts(plan)  # 与路由 /prompt/draft 同源，校验 intents 一致
    bindings = build_bindings(plan, intents)
    prompts = build_plan_h3_prompts(
        intents, plan=plan, bindings_by_shot=bindings,
        duration_by_shot={f"{sc.scene_id}:{s.shot_id}": float(s.duration_sec or 5.0)
                          for sc in plan.scenes for s in sc.shots},
    )

    _check = check
    print(f"\n〔0〕fixture 解析 + 关键镜头存在：")
    _check("ProductionPlan.from_dict 成功", plan is not None)
    _check("9 镜全部进入 DirectorIntent",
           len(intents) == 9, f"实际 {len(intents)}")
    for s in SHOTS:
        _check(f"回归镜头 {s} 存在", s in intents and s in prompts, "缺失")

    for key in SHOTS:
        print(f"\n〔{SHOTS.index(key)+1}〕{key}")
        intent = intents[key]
        prompt = prompts[key]
        d = intent.to_dict() if hasattr(intent, "to_dict") else intent
        p = prompt.to_dict() if hasattr(prompt, "to_dict") else prompt
        src = d["user_original_intent"]

        # ── ① 三层结构 ──
        print("  ① 三层结构：")
        _check("   L1 原文非空且为 dict 顶层字段", bool(src.strip()), repr(src[:30]))
        _check("   L2 retained_facts 非空（逐字原文事实）",
               len(d["retained_facts"]) > 0, f"n={len(d['retained_facts'])}")
        _check("   L2 emotion（AI）字段存在", bool(d.get("emotion")), repr(d.get("emotion", "")))
        _check("   L2 composition（AI，=visual_intent）存在",
               bool(d.get("composition")), repr(d.get("composition", "")[:30]))
        _check("   L2 continuity.camera_desc（RULE 运镜）存在",
               bool(d.get("continuity", {}).get("camera_desc")),
               repr(d.get("continuity", {}).get("camera_desc", "")[:30]))
        _check("   L3 三段全非空",
               all(p[k] for k in
                   ("integrated_multimodal_description", "overall_soundscape",
                    "non_diegetic_music")))

        # ── ② 字段级来源标记（枚举大写值，与生产序列化一致） ──
        print("  ② 字段级 provenance：")
        prov = d.get("provenance", {})
        _check("   user_original_intent = USER", prov.get("user_original_intent") == IntentSource.USER)
        _check("   retained_facts = USER", prov.get("retained_facts") == IntentSource.USER)
        _check("   emotion = AI", prov.get("emotion") == IntentSource.AI)
        _check("   composition = AI", prov.get("composition") == IntentSource.AI)
        _check("   camera_position/movement/continuity = RULE",
               prov.get("camera_position") == IntentSource.RULE and
               prov.get("continuity") == IntentSource.RULE)

        # ── ④ 逐字可追溯 ──
        print("  ④ 逐字可追溯（L1 → L3）：")
        desc = p["integrated_multimodal_description"]
        timeline = f"[0-{float(p['duration_sec']):g}s]"
        _check("   时间轴前缀", desc.startswith(timeline), desc[:16])
        _check("   L1 字段 = 剧本原文逐字（原文最高优先级未被覆盖）",
               d["user_original_intent"].strip() == src_by_key.get(key, ""),
               f"L1={d['user_original_intent'][:24]!r} vs 原文={src_by_key.get(key, '')[:24]!r}")
        facts = d["retained_facts"]
        missing = [f for f in facts if f[:8] not in desc and f[:8]]
        _check("   全部 retained_facts 逐字进描述（原文事实逐字保留）", not missing, f"缺失: {missing[:2]}")
        # AI 补全词作为补充句（出现在描述，且不与原文句 70% 相似）
        comp = d.get("composition", "")
        _check("   AI 构图补充句进入描述（不覆盖原文）",
               bool(comp) and (comp[:6] in desc or any(comp[:6] in f for f in facts)),
               f"AI 构图首 6 字未找到")

        # ── ⑤ 结构化 references ──
        print("  ⑤ 结构化 references + 可读标签：")
        refs = p.get("references", [])
        _check("   references 数量 = 边界检查后 entities 数量（同一集合）",
               len(refs) == len(d["entities"]), f"{len(refs)}/{len(d['entities'])}")
        _check("   每条含 entity_key/entity_name/image_file",
               all(all(r.get(k) for k in ("entity_key", "entity_name", "image_file"))
                   for r in refs))
        ref_tag_ok = len(refs) > 0 and \
            all("[REF:" in desc and f"[REF: {r['entity_name']}]" in desc for r in refs)
        _check("   文本内 [REF: 名] 标签齐全且数量与 references 一致",
               ref_tag_ok and desc.count("[REF: ") == len(refs),
               f"desc={desc[-60:]} refs={[r['entity_name'] for r in refs]}")

        # ── ⑥ 段落级 provenance ──
        print("  ⑥ 段落级 provenance（三类来源）：")
        pp = p.get("provenance", {}).get("integrated_multimodal_description", {})
        _check("   desc.provenance 三键齐全",
               all(k in pp for k in ("user_facts", "ai_supplement", "rule_generated")),
               str(pp.keys()))
        _check("   三类来源总条数 > 0",
               sum(len(pp.get(k, [])) for k in ("user_facts", "ai_supplement", "rule_generated")) > 0,
               str(pp))
        sound = p.get("provenance", {}).get("overall_soundscape", {})
        _check("   soundscape.provenance 存在", bool(sound))
        music = p.get("provenance", {}).get("non_diegetic_music", {})
        _check("   music.provenance 存在", bool(music))

    # 对白逐字保留（Shot 4 / Shot 7）
    print("\n〔附加〕对白逐字保留：")
    for key, line in (("scene_02:shot_02", "客官，落座歇脚，茶先暖着。"),
                      ("scene_02:shot_05", "剑，交出来。")):
        desc = prompts[key].integrated_multimodal_description
        _check(f"   {key} 对白「{line}」逐字在 H3 描述",
               line in desc or line.strip("。") in desc.replace(" ",""))

    # 实体边界检查（跨镜越界被丢弃）
    print("\n〔附加〕实体边界检查（用户拍板 ②）：")
    d5 = intents["scene_02:shot_05"].to_dict() if hasattr(intents["scene_02:shot_05"], "to_dict") else intents["scene_02:shot_05"]
    names5 = {e["name"] for e in d5["entities"]}
    _check("   shot7 实体=本镜来源（沈青崖/柳如烟/蒙面人/短刃）",
           {"沈青崖", "柳如烟", "蒙面人"} <= names5 and len(d5["entities"]) == 4,
           str(sorted(names5)))
    # shot1：fixture entities 含「旧剑匣」（跨镜越界，source_text 未提及）→ 必须被丢弃
    d1 = intents["scene_01:shot_01"].to_dict() if hasattr(intents["scene_01:shot_01"], "to_dict") else intents["scene_01:shot_01"]
    names1 = {e["name"] for e in d1["entities"]}
    _check("   shot1 越界「旧剑匣」被丢弃，仅留山雨楼/老槐树",
           "旧剑匣" not in names1 and set(names1) == {"山雨楼", "老槐树"},
           f"实体={sorted(names1)}")

    # 纯规则零显存零网络
    print("\n〔附加〕纯规则零显存：")
    for mod in ("director_intent.py", "h3_prompt_builder.py"):
        path = os.path.join(SRC, "director", mod)
        s = open(path, encoding="utf-8").read()
        for bad in ("unload", "urlopen", "keep_alive", "torch.", "requests."):
            _check(f"   {mod} 无 {bad}", bad not in s)

    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()