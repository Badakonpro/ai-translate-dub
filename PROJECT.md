# Video Translation & Subtitling Tool

自动视频翻译 + 字幕工具：提取视频语音 → Whisper 转录 → LLM 翻译 → 封装 MKV 软字幕。

## 项目结构

```
ai 翻译配音/
├── .gitignore                  # 忽略 outputs/、__pycache__/、config.yaml 等
├── config.yaml                 # API 密钥、模型配置（不入库）
├── config.example.yaml         # 配置文件模板（入版本库，供协作者参考）
├── requirements.txt            # Python 依赖
├── app.py                      # Gradio Web UI 入口
├── pipeline/                   # 核心处理管线
│   ├── __init__.py
│   ├── audio_extractor.py      # 1. 音频提取（ffmpeg → 16kHz mono WAV）
│   ├── transcriber.py          # 2. 语音识别（Whisper → 带时间戳的文本段）
│   ├── translator.py           # 3. 翻译引擎（DeepSeek / Ollama）
│   ├── config.py               # 配置加载、默认值、输出目录管理
│   ├── diagnostics.py          # 运行环境检查
│   └── subtitle_muxer.py       # 4. 字幕封装（生成 SRT + ffmpeg 封装 MKV）
├── desktop_app.py              # 桌面 App 启动入口（本地服务 + 浏览器）
├── build_macos_app.sh          # macOS .app 打包脚本
├── ai_translate_dub.spec       # PyInstaller 打包配置
├── run_web.sh                  # Web 模式快捷启动脚本
└── outputs/                    # 输出目录（不入库）
    ├── *_audio.wav             # 临时音频文件（处理后自动删除）
    ├── *_Chinese.srt           # 独立字幕文件
    └── *_subtitled.mkv         # 带软字幕的视频
```

## 架构设计

### 数据流

```
┌─────────────────┐
│  video_input.mp4 │  用户上传
└────────┬────────┘
         │
    ┌────▼────┐  ffmpeg: -vn -acodec pcm_s16le -ar 16000 -ac 1
    │ 1. 音频  │────────────────────────────────────────────▶ audio.wav
    │   提取   │                                             (16kHz mono)
    └────┬────┘
         │
    ┌────▼────┐  Whisper: model.transcribe(word_timestamps=True)
    │ 2. 语音  │────────────────────────────────────────────▶ segments[]
    │   识别   │   [{start, end, text}, ...]
    └────┬────┘
         │
    ┌────▼────┐  DeepSeek API / Ollama API
    │ 3. 翻译  │────────────────────────────────────────────▶ translated[]
    │          │   [{start, end, text(translated)}, ...]
    └────┬────┘
         │
    ┌────▼────┐  生成 SRT 格式字幕
    │ 4. 字幕  │────────────────────────────────────────────▶ output.srt
    │   生成   │
    └────┬────┘
         │
    ┌────▼────┐  ffmpeg: -c copy -c:s srt → MKV 容器
    │ 5. 封装  │────────────────────────────────────────────▶ output.mkv
    │  MKV    │   (视频流 + 音频流 + 软字幕流)
    └─────────┘
```

### 模块详解

#### 1. audio_extractor.py — 音频提取

| 项目 | 说明 |
|------|------|
| 输入 | 任意格式视频文件 (mp4/mkv/avi/mov 等) |
| 输出 | 16kHz 单声道 PCM WAV (`{video}_audio.wav`) |
| 依赖 | ffmpeg (系统级) |
| 核心参数 | `-vn` 丢弃视频、`-acodec pcm_s16le` PCM编码、`-ar 16000` 采样率、`-ac 1` 单声道 |

**为什么是 16kHz mono WAV？**
Whisper 模型训练时使用 16kHz 采样率，传入其他采样率会被自动重采样。提前在 ffmpeg 阶段转换可以：
- 减少后续音频文件体积
- 避免 Whisper 内部重采样的开销

#### 2. transcriber.py — 语音识别

| 项目 | 说明 |
|------|------|
| 输入 | 16kHz mono WAV 文件 |
| 输出 | `[{start: float, end: float, text: str}, ...]` |
| 依赖 | `openai-whisper` (本地运行) |
| 模型选项 | tiny / base / small / medium / large（体积与精度递增） |

**模型选择建议：**

| 模型 | 参数量 | 相对速度 | 英文准确率 | 中文/多语言 | 适用场景 |
|------|--------|----------|-----------|------------|---------|
| tiny | 39M | ~32x | 较低 | 较差 | 快速测试、短片段 |
| base | 74M | ~16x | 一般 | 一般 | 日常简单翻译 |
| small | 244M | ~6x | 较好 | 较好 | 多数场景推荐 |
| medium | 769M | ~2x | 好 | 好 | 高精度需求 |
| large | 1550M | ~1x | 最好 | 最好 | 专业级转录 |

> **推荐**：medium 是精度与性能的最佳平衡点。中文/日语等多语言场景建议 small 起步。

**语言检测**：
- `"auto"`：Whisper 自动检测（多数情况准确）
- 手动指定：`English`, `Japanese`, `Chinese` 等（减少误检测，提升精度）

#### 3. translator.py — 翻译引擎

**类层次：**

```
BaseTranslator (ABC)
├── DeepSeekTranslator
│   ├── API: api.deepseek.com (OpenAI 兼容)
│   ├── 模型: deepseek-chat
│   └── 鉴权: API Key (通过 UI 输入或 config.yaml)
│
└── OllamaTranslator
    ├── API: localhost:11434 (本地)
    ├── 模型: 可配置 (qwen3, llama3, gemma3 等)
    └── 鉴权: 无（本地服务）
```

**翻译策略 — 带上下文窗口：**

```
┌──────────────┬──────────────┬──────────────┐
│  前一句文本    │  当前句文本    │  后一句文本    │
│  (上下文)     │  (翻译目标)   │  (上下文)     │
└──────────────┴──────────────┴──────────────┘
         │              │              │
         └──────────────┼──────────────┘
                        ▼
              LLM 翻译（保持连贯性）
```

每次翻译将前后相邻字幕作为上下文传给 LLM，避免孤立的逐句翻译导致的语义断裂。

**怎么选后端？**

| 场景 | 推荐 |
|------|------|
| 离线、隐私优先 | Ollama + qwen3 |
| 追求翻译质量 | DeepSeek API |
| 国内网络、低成本 | DeepSeek API |
| 长视频批量处理 | DeepSeek API（更快） |

#### 4. subtitle_muxer.py — 字幕封装

**SRT 格式示例：**
```
1
00:00:00,500 --> 00:00:03,200
欢迎来到今天的视频。

2
00:00:03,500 --> 00:00:07,800
今天我们要讨论一个重要的话题。
```

**MKV 封装 vs 烧录硬字幕：**

| 方式 | 软字幕 (MKV) | 硬字幕 (烧录) |
|------|-------------|-------------|
| 开关 | ✅ 可自由开关 | ❌ 永久叠加 |
| 多语言 | ✅ 多轨道切换 | ❌ 只能一种 |
| 可编辑 | ✅ 可提取/编辑后重新封装 | ❌ 不可逆 |
| 性能 | ✅ 无需重新编码 | ❌ 需要重新编码（慢） |
| 兼容性 | VLC/IINA/MKV 播放器 | 所有播放器 |

> **本项目使用软字幕**：ffmpeg `-c copy` 直接复制视频/音频流，仅添加字幕轨道，速度快且无损画质。

### Web UI 布局（Gradio）

```
┌──────────────────────────────────────────────────────────┐
│            Video Translation & Subtitling Tool           │
├──────────────────────────┬───────────────────────────────┤
│                          │                               │
│  1. Upload Video         │  4. Results                   │
│  ┌──────────────────┐    │                               │
│  │   Drop video or   │    │  Status: ✓ Done               │
│  │   click to upload │    │                               │
│  └──────────────────┘    │  ┌─────────────────────────┐  │
│                          │  │ Download MKV (video)    │  │
│  2. ASR Settings         │  └─────────────────────────┘  │
│  Whisper Model: [medium▼]│                               │
│  Source Lang:   [auto  ▼]│  ┌─────────────────────────┐  │
│                          │  │ Download SRT (subtitles)│  │
│  3. Translation          │  └─────────────────────────┘  │
│  Target Lang: [Chinese  ]│                               │
│  Backend:  ○ Ollama      │  Tips:                        │
│            ● DeepSeek    │  • VLC/IINA 可开关字幕       │
│                          │  • QuickTime 不支持 MKV 字幕  │
│  ▶ DeepSeek Settings     │                               │
│  ▶ Ollama Settings       │                               │
│                          │                               │
│  [  Process Video  ]     │                               │
│                          │                               │
└──────────────────────────┴───────────────────────────────┘
```

## 环境要求

### 系统依赖

| 工具 | 版本 | 用途 | 安装 |
|------|------|------|------|
| Python | ≥ 3.9 | 运行环境 | https://python.org |
| ffmpeg | 任意新版 | 音频提取 + 字幕封装 | `brew install ffmpeg` (macOS) |
| Ollama | 最新版 | 本地 LLM 翻译（可选） | `brew install ollama` |

### Python 依赖

```
openai-whisper>=20231117    # 语音识别（本地 Whisper 模型）
gradio>=4.44.0,<5.0.0      # Web UI
openai>=1.0.0              # DeepSeek API 客户端（OpenAI 兼容）
ollama>=0.4.0              # Ollama 本地模型客户端
pysrt>=1.1.2               # SRT 字幕生成
pyyaml>=6.0                # 配置文件解析
torch>=2.0.0               # Whisper 运行依赖
requests>=2.31.0           # Ollama HTTP 调用
huggingface-hub<1.0        # Gradio 4.x 兼容性
```

**安装：**
```bash
pip3 install -r requirements.txt
```

### 首次运行清单

- [ ] 安装 ffmpeg：`brew install ffmpeg`
- [ ] 安装 Python 依赖：`pip3 install -r requirements.txt`
- [ ] 配置 `config.yaml`：
  - DeepSeek 用户：填入 `api_key`
  - Ollama 用户：确保 Ollama 已启动并拉取模型（`ollama pull qwen3`）
- [ ] 运行：`python app.py`
- [ ] 浏览器打开 `http://localhost:7860`

## App 化交付

当前项目支持两种运行形态：

| 形态 | 入口 | 用途 |
|------|------|------|
| Web 开发模式 | `./run_web.sh` 或 `python3 app.py` | 开发、调试、快速验证 |
| 桌面 App 模式 | `python3 desktop_app.py` | 启动本地 Gradio 服务并自动打开浏览器，接近最终 App 体验 |
| macOS 打包 | `./build_macos_app.sh` | 生成 `dist/AI翻译配音.app` |

### 打包为 macOS App

```bash
cd "/Users/zhizhanmu/Documents/ai 翻译配音"
./build_macos_app.sh
```

脚本会自动：

1. 创建独立构建环境 `.venv-build`
2. 安装 `requirements.txt` 和 PyInstaller
3. 根据 `ai_translate_dub.spec` 收集 Gradio、Whisper 等依赖
4. 清理可删除的 macOS 扩展属性
5. 输出 `dist/AI翻译配音.app`

### 打包前注意事项

- 首次构建会比较慢，因为 Whisper / Torch / Gradio 依赖体积较大。
- `ffmpeg` 仍然建议作为系统依赖安装：`brew install ffmpeg`。
- Ollama 后端依赖本机 Ollama 服务，不会被打进 App：需要用户本机启动 Ollama 并拉取模型。
- DeepSeek 后端不建议把真实 API Key 写死进 App。正式分发时让用户在界面输入，或在本机 `config.yaml` 配置。
- 当前生成的是本地未签名 App。如果 macOS 阻止打开，可在 Finder 中右键打开；正式分发时再增加开发者证书签名与 notarization。
- 当前桌面入口使用系统浏览器承载界面，避免 PyObjC / pywebview 在系统 Python 3.9 上的兼容问题。后续若切到 Python 3.11+，可以再加入原生 WebView 壳。

### 成熟化边界

这个版本已经具备可维护项目形态：

- 配置集中在 `config.yaml` / `config.example.yaml`
- 核心处理管线与 UI 分离
- 支持 Web 模式、桌面壳模式、macOS App 打包
- 输出文件使用唯一 ID，避免重复处理时互相覆盖
- UI 内置运行环境检查，缺失依赖能直接提示

后续如果要继续产品化，优先级建议如下：

1. 批量处理队列与任务历史
2. 双语字幕与硬字幕烧录选项
3. faster-whisper / MLX Whisper 后端，提高 macOS 性能
4. 应用图标、签名、公证、自动更新
5. 配置页持久化保存，而不是只在本次运行中读取输入

## 开发指南

### 添加新的翻译后端

1. 在 `pipeline/translator.py` 中继承 `BaseTranslator`
2. 实现 `translate(self, segments, source_lang, target_lang, progress_callback)` 方法
3. 在 `app.py` 的 `_build_translator()` 和 UI 的 `Radio` 中添加选项

```python
class NewBackendTranslator(BaseTranslator):
    def __init__(self, ...):
        # 初始化配置

    def translate(self, segments, source_lang, target_lang, progress_callback=None):
        translated = []
        for i, seg in enumerate(segments):
            # 调用你的翻译 API
            translated.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": "翻译后的文字",
            })
            if progress_callback:
                progress_callback(i / len(segments), f"...")
        return translated
```

### 添加新的 ASR 引擎

1. 修改 `pipeline/transcriber.py` 或新建文件
2. 保持输出格式：`[{start, end, text}, ...]`
3. 在 `app.py` 中接入

### 编码规范

- **类型注解**：使用 `typing` 模块（兼容 Python 3.9），如 `List[dict]` 而非 `list[dict]`
- **进度回调**：所有耗时操作接受 `progress_callback(pct, message)` 参数
- **错误处理**：异常统一在 `app.py` 的 `process_video()` 中捕获并展示给用户
- **路径处理**：使用 `pathlib.Path`，不拼接字符串路径

## 常见问题

### Q: 为什么不用 MP4 而用 MKV？
MP4 的软字幕格式 `mov_text` 功能极其有限，不支持样式、多轨道。MKV 原生支持 SRT/ASS 字幕，播放器兼容性好（除 QuickTime 外）。

### Q: Whisper 模型存储在哪？
默认在 `~/.cache/whisper/`，首次使用会自动下载。tiny ~300MB，large ~3GB。

### Q: 翻译很慢怎么办？
- 使用更小的 Whisper 模型（small 甚至 base）
- Ollama 换成更快的量化模型（如 `qwen3:4b` 代替 `qwen3:32b`）
- DeepSeek API 通常比本地 Ollama 快

### Q: 如何在手机/平板播放软字幕？
- iOS：nPlayer、VLC for Mobile、Infuse
- Android：VLC、MX Player、nPlayer

## 路线图

- [x] 本地 Whisper 语音识别
- [x] DeepSeek + Ollama 双翻译后端
- [x] MKV 软字幕封装
- [x] Gradio Web UI
- [ ] 批量视频处理
- [ ] 双语字幕（原文字幕 + 翻译字幕双轨）
- [ ] 硬字幕烧录选项
- [ ] 支持更多 ASR 引擎（如 faster-whisper）
- [ ] Docker 一键部署

## 许可证

MIT
