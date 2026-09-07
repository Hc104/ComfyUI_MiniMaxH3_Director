# TTS 三引擎盲听测试指南（BLIND_TEST_TTS_README）

> 2026-08-16 用户拍板：先拿同一段《吐槽成真》台词做三引擎盲听测试，再定最终 TTS 引擎。
> 本目录把 MiniMax Speech（云端）/ GPT-SoVITS（本地）/ CosyVoice 3（本地）各合成一版，
> 匿名成 A/B/C，你盲听打分后 `reveal` 揭晓。

**架构前提**：AudioIntent + Voice Cast + mixer.py 的链路已成立，本测试不改任何现有架构——
三套引擎全部实现同一 `TtsEngine` 接口（`director/tts_engines/`），最终选定后只是换一个 backend。

---

## 0. 目录结构

```
ComfyUI_MiniMaxH3_Director/
├── director/tts_engines/            ← 三套可插拔后端（本次新增）
│   ├── minimax_speech.py            ← 云端 MiniMax T2A v2（hex 解码/音色映射/克隆辅助）
│   ├── gpt_sovits.py                ← 本地 GPT-SoVITS V4（zero-shot，HTTP 调 api.py）
│   └── cosyvoice3.py                ← 本地 CosyVoice 3（zero-shot，HTTP 调自托管 server）
├── tools/
│   ├── blind_test_tts.py            ← 盲听测试 runner（prepare-refs/synth/arrange/reveal/clone）
│   └── cosyvoice_server.py          ← CosyVoice 3 自托管 FastAPI 服务（独立进程）
├── blind_test/
│   ├── script.json                  ← 共享台词脚本（6 句·四角色）
│   ├── refs/                        ← prepare-refs 生成的角色克隆参考音频
│   ├── raw/                         ← 各引擎合成产物（不盲听）
│   ├── blind/A|B|C/                 ← arrange 生成的盲听目录
│   ├── mapping.key                  ← A/B/C 答案（打分前别打开！）
│   └── 打分表.md                    ← 四维打分表
└── BLIND_TEST_TTS_README.md         ← 本文件
```

---

## 1. 快速上手（3 步）

全程用 **ComfyUI `.venv`** 的 Python（后端依赖环境铁律，见 CHANGELOG #3551）：

```bat
cd /d D:\夸克网盘\ComfyUI_MiniMaxH3_Director

REM ① 生成四角色克隆参考音频（Edge-TTS 生成；想换真人参考见 §6）
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py prepare-refs

REM ② 三引擎各合成一遍（引擎服务按 §3/§4/§5 先就绪）
REM    云端 MiniMax：先 set MINIMAX_API_KEY（见 §2）
REM    本地 GPT-SoVITS / CosyVoice：先起服务
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py synth --engine minimax
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py synth --engine gpt-sovits
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py synth --engine cosyvoice3

REM ③ 洗牌成盲听目录 → 打开打分表盲听打分 → 揭晓
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py arrange
start blind_test\打分表.md
REM …… 听完填完表，再运行 ↓
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py reveal
```

单个引擎出问题不影响其他引擎：`synth --engine xxx` 独立运行，可反复重跑（覆盖输出）。

---

## 2. MiniMax Speech（云端，最省事，不占 5080）

**前置**：一个 MiniMax API Key（与 H3 同一平台，你大概率已有）。

密钥地址：https://platform.minimaxi.com/user-center/basic-information/interface-key

```bat
set MINIMAX_API_KEY=你的密钥

cd /d D:\夸克网盘\ComfyUI_MiniMaxH3_Director
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py synth --engine minimax
```

- 默认模型 `speech-2.8-hd`（最新，支持语气词标签）；API 报模型不可用时换：
  `synth --engine minimax --minimax-model speech-2.6-hd`
- 四角色音色映射（官方系统音色）：林薇薇=`female-shaonv` 少女 / 顾琰宸=`male-qn-badao`
  霸道青年 / 旁白=`qiaopi_mengmei` 俏皮萌妹 / 系统=`Robot_Armor` 机械战甲。
- 资费参考：Turbo 套餐约 ¥360~¥400（200 万字符/月）、HD 套餐约 ¥630~¥700。
- 可选：为角色做专属克隆音色（需实名认证）→ §7。

---

## 3. GPT-SoVITS（本地，zero-shot 声音克隆）

**资源**：官网整合包（含 5080 驱动）或源码 + Python 3.9~3.11 环境；V4 推理约 4~6GB 显存。

### 3.1 部署（以官方整合包为例）

1. 下载 GPT-SoVITS 最新整合包（v4，百度网盘见官方 README），解压后双击 `1-GPT-SoVITS-WebUI.bat`。
2. 首次使用在 WebUI 内完成：模型下载、SSL 证书、`api.py` 相关依赖勾选。
3. 关闭 WebUI，改用命令行起 **api.py**（盲听测试走 API 端口 9880）：

```bat
cd /d D:\GPT-SoVITS
REM -dr/-dt 只设默认参考（可用 refs 里任一段），本后端每次请求自带角色参考，最终以请求参数为准
python api.py -dr D:\夸克网盘\ComfyUI_MiniMaxH3_Director\blind_test\refs\guyanchen.wav -dt "我是顾琰宸。江海市的天，我说了算。" -dl zh -p 9880
```

看到 `Server started at 127.0.0.1:9880` 即可。**保持此窗口不关**。

### 3.2 合成

```bat
cd /d D:\夸克网盘\ComfyUI_MiniMaxH3_Director
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py synth --engine gpt-sovits
```

> 服务地址默认 `http://127.0.0.1:9880`；改了端口用 `--base-url http://127.0.0.1:9xxx` 覆盖。

---

## 4. CosyVoice 3（本地，0.5B 模型，约 4GB 显存）

模型：Fun-CosyVoice3-0.5B-2512（9 语言 + 18 中文方言，zero-shot + instruct 情绪/语速）。

### 4.1 部署

```bat
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice
conda create -n cosyvoice python=3.11
conda activate cosyvoice
pip install -r requirements.txt
pip install -U torch torchaudio --index-url https://download.pytorch.org/whl/cu126
git submodule update --init --recursive
REM 下载 Fun-CosyVoice3-0.5B 模型放到 pretrained_models\Fun-CosyVoice3-0.5B
REM   （HuggingFace: FunAudioLLM/Fun-CosyVoice3-0.5B；国内可用 hf-mirror.com）
```

### 4.2 启动自托管服务（独立进程，与 ComfyUI 隔离）

```bat
conda activate cosyvoice
cd /d D:\...\CosyVoice
python D:\夸克网盘\ComfyUI_MiniMaxH3_Director\tools\cosyvoice_server.py --model_dir pretrained_models/Fun-CosyVoice3-0.5B --port 8898
```

看到 `CosyVoice 3 server 启动：http://127.0.0.1:8898` 即可。**保持此窗口不关**。

> Windows 注意：CosyVoice 官方 Windows 痛点 = ttsfrd wheel 仅 linux + pynini/WeTextProcessing 难装；
> 官方已有 fallback（缺 ttsfrd 时走 wetext 文本前端）。启动报缺依赖先看 `requirements.txt`，
> 并确认 `third_party/Matcha-TTS` 子模块已拉取。

### 4.3 合成

```bat
cd /d D:\夸克网盘\ComfyUI_MiniMaxH3_Director
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py synth --engine cosyvoice3
```

---

## 5. 盲听打分

```bat
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py arrange
```

`blind_test/blind/A|B|C` 生成后，**用播放器逐目录听**（每个目录内按 `01_system → 06_linweiwei` 顺序）。

打分表 `blind_test/打分表.md` 四维打分：音色稳定 / 中文自然 / 情绪表现 / 机械感。
打完分（或把表发给我）再运行：

```bat
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py reveal
```

---

## 6. 换真人参考（可选）

`blind_test/refs/<角色>.wav` 当前是 Edge-TTS 生成的共享参考（保证三引擎公平可比）。
想用真人音色：把对应 wav 换成你的录音（3~10s 单人干音），并同步改
`blind_test/refs/refs.json` 里该角色的 `prompt_text`（与音频内容一致），然后重跑 `synth`。

---

## 7. MiniMax 专属克隆音色（可选，Phase B 再做）

```bat
set MINIMAX_API_KEY=你的密钥
cd /d D:\夸克网盘\ComfyUI_MiniMaxH3_Director
"D:\Comfy-Desktop\ComfyUI (1)\ComfyUI\.venv\Scripts\python.exe" tools\blind_test_tts.py clone ^
  --file D:\你的音频\林薇薇_10s.wav --voice-id clone_linweiwei --prompt-text "我是林薇薇，这一场戏我赢定了。"
```

克隆成功返回 `voice_id=clone_linweiwei`；把映射写进后端 voice_map 即完成接入。
注意：需实名认证；克隆音色 7 天未调用自动删除。

---

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| `synth` 报「未配置 MiniMax API Key」 | `set MINIMAX_API_KEY=...` 后重跑（§2） |
| MiniMax 报 1004 | Key 无效/鉴权失败，重新复制 key |
| MiniMax 报 1002 | 触发限流，等一会儿重试 |
| MiniMax 报「模型不可用」 | 换 `--minimax-model speech-2.6-hd` |
| GPT-SoVITS 连接失败 | 确认 api.py 窗口在跑、端口 9880；改端口用 `--base-url` |
| CosyVoice 3 连接失败 | 确认 cosyvoice_server 窗口在跑、端口 8898；启动时带 `--port` 对齐 |
| 本地引擎出音频但角色音色都一样 | 参考音频没换对——确认 `blind_test/refs/` 各角色 wav 是各自的 |
| 缺 requests / edge-tts | 用 ComfyUI `.venv` 装：`.venv\Scripts\python.exe -m pip install requests edge-tts` |

---

## 9. 选定引擎后

把打分结果告诉我（或发 `打分表.md`），我会：
1. 把选定引擎挂到 `create_default_tts_backend`（/ 路由），Voice Cast 面板 + Dialogue 行级下拉
   直接列出该引擎音色；
2. 本地引擎接入显存安全门（5080 上 H3 生成与本地 TTS 互斥，云端 MiniMax 不受影响）；
3. 写进 TTS_VOICE_CAST_PLAN.md 生产方案。

**全程不推翻 AudioIntent + mixer.py 现有链路，只换 backend。**
