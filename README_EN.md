# AI Manga Director System · ComfyUI MiniMax H3 Director (Derivative Fork)

> An **AI manga / short-drama production pipeline** — from a raw novel / script text to a voiced episode — built as a second-development layer on top of AIMixer's MiniMax H3 Director node (Apache-2.0).
> One person + a local GPU + open-source models: text in, an AI-dubbed manga episode out.

**中文文档** → [README.md](README.md)

![MiniMaxH3Director workflow screenshot](docs/screenshot.png)

---

## ⚠️ Credit / Provenance

This repository is a **fork (derivative work)**, not built from scratch:

- **Base**: forked from [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director) (Apache-2.0). All upstream commit history and the LICENSE are preserved intact. The original node's capabilities (multi-segment timeline / t2v / i2v / fl2v / r2v / v2v / rv2v / native stereo audio) are described in the base section below.
- **Derivative layer**: an "**AI Manga Director layer**" added on top — story understanding, shot planning, camera/visual-style rules, H3 prompt compilation, asset-consistency control, TTS voice cast, and a Vue SPA storyboard workbench. It was committed in a single commit (`10664e7`, ~ +95k lines).
- All contact channels (QQ / QQ groups / Bilibili / Comfyit) belong to **[AIMixer](https://github.com/AIMixer)** — contact the original author via the upstream repo. This fork is for personal learning and job-portfolio purposes.
- **For the original node only**, use upstream: [github.com/AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director).

---

## What this is

The base node "generates one shot"; the director layer "acts like a director — breaking the whole story into shots and supervising it through to the final cut".

```
novel / script text
   │  story_analyzer · script_parser (local Qwen chapter understanding + rule-based fallback)
   ▼
Story Timeline + shots (ProductionPlan: Chapter → Beat → Shot → DirectorIntent)
   │  shot_plan · dual-predecessor continuity · Global Story Bible
   ▼
Per-shot "director intent" (content / action / psychology / camera / style / audio / entities)
   │  camera_template · core_action · psych_visualize · beat_rhythm · visual_style
   ▼
H3 prompt compilation (three-section + bilingual constraints + no-subtitle constraint)
   │  h3_prompt_builder · prompt_compiler · constraint_checker
   ▼
MiniMax H3 generation (base node executes t2v/fl2v/r2v/v2v/rv2v)
   │  Qwen3-VL frame feedback → 3-level re-run · vae_residency decode fix
   ▼
TTS voice cast → FFmpeg ducking mix → final episode
   │  tts_engine (pluggable) · tts_worker · mixer
   ▼
Vue3 + TS storyboard workbench SPA (projects / timeline / shot cards / task center / archive)
```

**Design principle**: content layer, director-rules layer, prompt-compilation layer, execution layer, sound layer, and UI layer are decoupled. Anything that can be a pure rule is never left to an LLM (testable, reproducible, zero VRAM).

---

## What this fork adds (the director layer)

1. **Script / novel import & story understanding** — `story_analyzer` / `script_parser` / `script_analyzer` / `entity_cleanse`; natural-language chapter understanding with structured output; `production_plan.py` is the core data model (`timeline` = single source of truth for playback order); `bible.py` / `bible_store.py` / `bible_updater.py` maintain a Global Story Bible for cross-shot consistency.

2. **Shot planning & director intent** — `shot_plan.py` (9 shot blueprints); `director_intent.py` as the single intermediate representation; dual-predecessor continuity (previous timeline shot + last state of the scene) to fix jump cuts in cross-editing.

3. **Director rules layer (v1/v2)** — `camera_template.py` (cross-shot camera state machine), `spatial_continuity.py`, `core_action.py` (one action per shot), `psych_visualize.py` (psychology → visual metaphor), `beat_rhythm.py` (emotion curve → camera intensity), `visual_style.py` (7 preset visual styles for the whole episode), `director_rules.py`.

4. **H3 prompt compiler & constraint layer** — `h3_prompt_builder.py` compiles DirectorIntent into the official three-section H3 prompt; `prompt_compiler.py` / `constraint_checker.py` inject 8 layers of hard constraints (character appearance, crowd counts, spatial relations, no-subtitle…); `prompt_sanitize.py` dedupes/de-pollutes. Everything is traceable (AI understanding → director intent → final H3 prompt).

5. **Assets & character consistency** — 3-level asset inheritance (Global → Scene → Shot) with `asset_matcher.py`; `asset_registry.py` persistent entity binding; Qwen character-appearance profiles (rules first, LLM fills gaps, never invents costumes); `qwen_vl_feedback.py` + `state_rule_check.py` give a Qwen3-VL visual feedback loop with 3-level re-run policy.

6. **Execution-layer hardening** — `executor_core.py` / `gen_timeline.py` / `segment_*` / `stream_export.py`; `vae_residency.py` fixes the aimdo dynamic-VAE decode hang; `vram_cleanup.py` / memory safety gate before queueing.

7. **Sound director layer (Voice Cast / TTS)** — `audio_intent.py` + dialogue extraction; `tts_engine.py` with pluggable backends (Edge-TTS / GPT-SoVITS / CosyVoice 3 / MiniMax Speech in `director/tts_engines/`); `tts_worker.py` + `mixer.py` (FFmpeg sidechaincompress ducking) solve overlapping / truncated dialogue. Architecture: **H3 = picture + ambience, TTS = actors, then ducking mix**.

8. **Frontend SPA workbench** (`frontend/`, Vue3 + TS + Vite) — project management, Timeline main view, location library, shot cards, prompt-section editor with `@`-asset autocomplete, task center (queue separation, real stage, ETA, seed replay), generation version archive, Voice Cast global panel. 50 source files + 32 test files.

9. **Engineering quality** — 60 backend pytest files (`director/tests`) + frontend vitest; dual-directory (dev/deploy) md5-sync discipline; full `CHANGELOG.md`; concise design docs (camera rules / prompt compiler / TTS voice cast / SPA blueprint) recording the key architecture decisions.

---

## Validation

This fork has been exercised **end-to-end on real scripts** in several forms: 4-shot A/B of director rules v2 + the prompt constraint layer, an 11-shot UI walkthrough, and a **12-shot full-episode golden path** (script import → rule-based shot split → entity matching → per-shot generation → voice dub → final cut). Acceptance-run logs and per-shot review checklists are kept locally and are not published in this repo.

> Demo videos are being produced and will be linked here (hosted externally, not stored in this repo).

---

## Repo layout (short)

```
├─ nodes/        base node definitions (MiniMaxH3Director + director-layer entry)
├─ lib/          base low-level libraries
├─ web/js/       base in-node UI (minimax_*.js director panel, heavily extended)
├─ director/     ★ NEW director layer (Python backend, 55 modules)
│  ├─ production_plan.py / director_intent.py     data model & intent
│  ├─ story_analyzer / script_analyzer / bible_*  story understanding
│  ├─ camera_template / visual_style / core_action / psych_visualize / beat_rhythm / director_rules / spatial_continuity
│  ├─ h3_prompt_builder / prompt_compiler / constraint_checker / shot_plan
│  ├─ asset_matcher / asset_registry / entity_cleanse / qwen_vl_feedback / state_rule_check
│  ├─ tts_engine / tts_worker / mixer / audio_intent / tts_engines/
│  ├─ executor_core / gen_timeline / plan / segment_* / stream_export / vae_residency / vram_cleanup
│  ├─ http_routes.py / project_store.py           HTTP routes & project file layer
│  ├─ prompt_templates/
│  └─ tests/     60 pytest files
├─ frontend/     ★ NEW SPA storyboard workbench (Vue3 + TS + Vite)
├─ tools/        NEW helper scripts (acceptance / blind-TTS tests / diagnostics)
├─ example_workflows/   base example workflows
└─ docs/ + *.md  design docs (NOVEL_TO_MANGA_GUIDE / CAMERA_RULES_V1/V2 / PROMPT_COMPILER_V1 / TTS_VOICE_CAST_PLAN / …)
```

---

## Install (this fork)

Prerequisites are the same as the base: **ComfyUI ≥ v0.30.0** (with official MiniMax H3 nodes) + MiniMax H3 weights (fl2va/ref2va UNETs, Qwen3-VL CLIP, dual VAEs). Story understanding needs a local text model (Qwen via Ollama); the visual feedback loop needs Qwen3-VL (can be disabled).

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Hc104/ComfyUI_MiniMaxH3_Director.git
cd ComfyUI_MiniMaxH3_Director
pip install -r requirements.txt
```

Restart ComfyUI. (Or install via **ComfyUI Manager → Install via Git URL** with the URL above.)

Frontend SPA (optional):

```bash
cd frontend
npm install
npm run build      # production build  (# npm run dev for dev mode)
npm test           # vitest
```

Backend tests:

```bash
python -m pytest director/tests -q
```

See **[OPERATION_GUIDE.md](OPERATION_GUIDE.md)** for full operation steps.

---

## Base functionality (inherited, by AIMixer)

> The capabilities below come from upstream [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director) and are fully preserved.

**MiniMaxH3Director** is a single-node director for long-form, multi-segment MiniMax H3 audio–video generation — timeline planning, conditioning, sampling, AV decode, and export in one place. It wraps the official `MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo` + `MiniMaxH3SigmaShift` + `KSampler` pipeline with native stereo audio.

- **Multi-segment timeline** — split / equal-split / smart shot-split (PySceneDetect) / append, with visual timeline + thumbs.
- **Task modes** — `t2v`, `i2v`, `fl2v`, `r2v` (reference material groups), `v2v`, `rv2v`.
- **fl2v** — first/last-frame groups; **r2v** — up to 9 images / 3 audios / 3 videos per group with `<Picture N>` / `<Video K>` / `<Audio J>` or `@` tags.
- **v2v / rv2v** — source-video timeline editing, auto `<Video 1>` binding, optional refs and audio modes.
- **Run select**, **native stereo audio**, **run report**.

Inputs: `model` → `video_vae` → `audio_vae` → `clip`. Outputs: `images` → `audio` → `fps` → `frame_count` → `source_images` → `report`.

> CLIP Loader type must be `minimax` (Qwen3-VL). Use **fl2va** UNET for `t2v/i2v/fl2v`; **ref2va** for `r2v/v2v/rv2v`.

Default sampling: 0.4MP 16:9 (864×480), 5s/124 frames @ 24 fps (17k+5 grid), 25 steps, `res_multistep` + `simple`, CFG 1.0, sigma shift video 12 / audio 3.

Model weights, example workflows and upstream ecosystem: see the **[upstream README](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director)**.

---

## Docs

| Doc | Purpose |
|---|---|
| [OPERATION_GUIDE.md](OPERATION_GUIDE.md) | Full operation guide (UI, fields, workflows, troubleshooting) |
| [CHANGELOG.md](CHANGELOG.md) | Index of every change (#650+) with files & known gotchas |
| [NOVEL_TO_MANGA_GUIDE.md](NOVEL_TO_MANGA_GUIDE.md) | Novel → manga end-to-end guide |
| [CAMERA_RULES_V1.md](CAMERA_RULES_V1.md) / [CAMERA_RULES_V2.md](CAMERA_RULES_V2.md) | Director camera rules v1/v2 design |
| [PROMPT_COMPILER_V1.md](PROMPT_COMPILER_V1.md) | Constraint-layer design (8 layers) |
| [TTS_VOICE_CAST_PLAN.md](TTS_VOICE_CAST_PLAN.md) | Sound-director architecture (H3 + TTS + ducking) |
| [FRONTEND_SPA_BLUEPRINT.md](FRONTEND_SPA_BLUEPRINT.md) | SPA storyboard workbench blueprint |

---

## Acknowledgements & License

- Base: [AIMixer/ComfyUI_MiniMaxH3_Director](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director) (Apache-2.0)
- [Comfy-Org / ComfyUI](https://github.com/Comfy-Org/ComfyUI) — official MiniMax H3 support; [MiniMax-AI](https://github.com/MiniMax-AI) — MiniMax H3 model
- [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) — weights & docs

This fork's additions are also released under **Apache-2.0**; LICENSE is unchanged from upstream.
