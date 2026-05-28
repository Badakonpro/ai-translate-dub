# AI 翻译配音

AI 翻译配音是一个本地优先的视频转录和字幕翻译工具。它会从视频中提取音频，用 Whisper 生成带时间轴的字幕，再通过 Ollama 或 DeepSeek 翻译，最后输出 SRT 字幕和带字幕轨的 MKV，或硬烧录字幕的 MP4。

当前版本：`1.0.0`

## 功能

- 上传视频后自动提取 16 kHz 单声道音频。
- 使用 OpenAI Whisper 转录字幕，支持 `tiny`、`base`、`small`、`medium`、`large`。
- Whisper 模型缓存到本地持久目录，已下载且校验通过的模型会复用。
- 转录阶段显示更细的进度、音频时长、等待时间和当前模型。
- 支持 Ollama 本地模型和 DeepSeek API 两种翻译后端。
- 可选“全局翻译上下文”：翻译前先基于标题和字幕抽样生成术语、风格和主题提示，再传给每条字幕翻译。
- 可选并行翻译，并可调整并行路数。
- 支持输出 MKV 软字幕，或将字幕硬烧录到 MP4。
- Electron macOS 外壳，启动后运行本地 Gradio UI。

## 安装使用

### 从 Release 安装

1. 在 GitHub Releases 下载 `AI翻译配音-1.0.0-arm64.dmg`。
2. 打开 DMG，将 App 放到 Applications。
3. 启动 `AI翻译配音.app`。

运行环境仍需要本机有 Python 3 和 Python 依赖。源码方式可用下面的命令安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

还需要安装 `ffmpeg`：

```bash
brew install ffmpeg
```

如果使用 Ollama，需要先启动 Ollama，并在界面中填写或拉取模型，例如 `qwen3:latest`。

### 从源码运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

启动后打开本地 Gradio 页面，默认地址为 `http://127.0.0.1:7860`。

### Electron 开发模式

```bash
npm install
npm start
```

### 打包 DMG

```bash
npm install
npm run dist:dmg
```

生成的 DMG 位于 `dist-electron/`。

## 配置

复制配置模板后按需修改：

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 不应提交到 Git。它通常包含本地 API Key、默认模型和输出目录设置。

常用配置项：

- `defaults.whisper_model`：默认 Whisper 模型。
- `defaults.translation_backend`：`ollama` 或 `deepseek`。
- `ollama.host` / `ollama.model`：本地 Ollama 服务和模型名。
- `deepseek.api_key` / `deepseek.model`：DeepSeek API 设置。
- `translation.context_enabled`：是否默认启用全局翻译上下文。
- `translation.parallel_enabled` / `translation.parallel_workers`：是否默认启用并行翻译和并行路数。

## 模型缓存

Whisper 模型默认缓存到：

```text
~/Library/Application Support/AI翻译配音/whisper-models
```

如果旧目录 `~/.cache/whisper` 里已有有效模型，应用会自动复制到新缓存目录。已缓存且校验通过的模型不会重复下载。同一次应用运行中再次使用同一个模型时，会直接复用内存中的模型。

## 输出文件

默认输出目录为 `outputs/`，包括：

- `.srt`：翻译后的字幕文件。
- `.mkv`：带软字幕轨的视频。
- `.mp4`：硬烧录字幕时生成的视频。

这些生成文件默认被 `.gitignore` 忽略。

## 版本发布

当前 release 版本使用 `package.json` / `package-lock.json` 中的版本号，并以 Git tag `v1.0.0` 发布。

发布前建议检查：

```bash
python3 -m compileall app.py desktop_app.py pipeline
node -c main.js
npm run dist:dmg
```

## 许可

MIT
