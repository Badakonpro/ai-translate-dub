import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# 清除 SOCKS 代理以避免 httpx 报错
for _key in ("ALL_PROXY", "all_proxy"):
    os.environ.pop(_key, None)

import gradio as gr
import gradio_client.utils as gradio_client_utils

from pipeline.audio_extractor import extract_audio
from pipeline.config import ensure_user_config, get_output_dir, load_config, save_config
from pipeline.diagnostics import format_runtime_report
from pipeline.transcriber import transcribe
from pipeline.subtitle_muxer import generate_srt, mux_subtitles, burn_subtitles

CONFIG = load_config()
OUTPUT_DIR = get_output_dir(CONFIG)

_ALLOWED_WHISPER_MODELS = {"tiny", "base", "small", "medium", "large"}
_ALLOWED_BACKENDS = {"ollama", "deepseek"}
_MAX_TRANSLATION_WORKERS = 8
_MAX_VIDEO_TITLE_CHARS = 300

# Maps Chinese UI labels → internal position keys used by burn_subtitles()
_SUBTITLE_POSITION_MAP = {
    "底部居中":  "bottom-center",
    "底部靠左":  "bottom-left",
    "底部靠右":  "bottom-right",
    "顶部居中":  "top-center",
    "顶部靠左":  "top-left",
    "顶部靠右":  "top-right",
    "画面中央":  "middle-center",
}


def _coerce_translation_workers(value, default=3):
    try:
        workers = int(value)
    except (TypeError, ValueError):
        workers = default
    return max(1, min(workers, _MAX_TRANSLATION_WORKERS))


def _clean_optional_text(value, max_chars=_MAX_VIDEO_TITLE_CHARS):
    text = " ".join(str(value or "").split())
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


_original_json_schema_to_python_type = gradio_client_utils._json_schema_to_python_type


def _json_schema_to_python_type_compat(schema, defs=None):
    if isinstance(schema, bool):
        return "Any"
    return _original_json_schema_to_python_type(schema, defs)


gradio_client_utils._json_schema_to_python_type = _json_schema_to_python_type_compat


def process_video(
    video_path,
    whisper_model,
    source_lang,
    target_lang,
    video_title,
    translation_backend,
    deepseek_api_key,
    deepseek_model,
    ollama_host,
    ollama_model,
    ollama_auto_pull,
    use_translation_context,
    parallel_translation,
    translation_workers,
    burn_subs,
    sub_font_size,
    sub_position,
    progress=gr.Progress(),
):
    """
    Main processing pipeline. Called when the user clicks 'Process'.
    Returns: (status_message, mkv_output_path, srt_output_path, translation_context)
    """
    if video_path is None:
        return "请先上传一个视频文件。", None, None, ""

    # ── Input validation ────────────────────────────────────────────────────
    if whisper_model not in _ALLOWED_WHISPER_MODELS:
        return f"无效的 Whisper 模型：{whisper_model}", None, None, ""
    if translation_backend not in _ALLOWED_BACKENDS:
        return f"无效的翻译后端：{translation_backend}", None, None, ""
    translation_workers = _coerce_translation_workers(translation_workers)
    use_translation_context = bool(use_translation_context)
    parallel_translation = bool(parallel_translation)
    if not parallel_translation:
        translation_workers = 1

    video_path = str(video_path)
    # Sanitize stem: keep only alphanumeric, dash, underscore, dot to prevent
    # path traversal in output filenames.
    raw_stem = Path(video_path).stem
    video_stem = "".join(ch for ch in raw_stem if ch.isalnum() or ch in ("-", "_", ".")) or "video"
    context_title = _clean_optional_text(video_title) or raw_stem
    run_id = uuid4().hex[:8]
    audio_path = None
    translation_context = ""

    try:
        # Step 1: Extract audio
        progress(0.05, desc="1/5 正在提取音频...")
        audio_path = extract_audio(video_path, str(OUTPUT_DIR))
        progress(0.15, desc="音频提取完成。")

        # Step 2: Transcribe with Whisper
        progress(0.20, desc=f"2/5 正在加载 Whisper {whisper_model} 模型...")

        def transcription_progress(pct, msg):
            progress(0.20 + pct * 0.30, desc=f"2/5 {msg}")

        segments = transcribe(
            audio_path,
            model_size=whisper_model,
            language=source_lang,
            progress_callback=transcription_progress,
        )

        if not segments:
            return "没有检测到可转录的人声。", None, None, ""

        progress(0.50, desc=f"转录完成，共 {len(segments)} 段字幕。")

        # Override config with UI values
        translator = _build_translator(translation_backend, deepseek_api_key, ollama_host, ollama_model, ollama_auto_pull, deepseek_model=deepseek_model)

        if use_translation_context:
            progress(0.50, desc="3/5 正在生成全局翻译上下文...")

            def context_progress(pct, msg):
                progress(0.50 + pct * 0.05, desc=f"3/5 {msg}")

            translation_context = translator.build_translation_context(
                segments,
                source_lang=source_lang,
                target_lang=target_lang,
                video_title=context_title,
                progress_callback=context_progress,
            )
            progress(0.55, desc="全局翻译上下文已生成。")

        # Step 3: Translate
        progress(0.55, desc=f"3/5 正在使用 {translation_backend} 翻译为 {target_lang}...")

        def translation_progress(pct, msg):
            progress(0.55 + pct * 0.25, desc=f"3/5 {msg}")

        translated = translator.translate(
            segments,
            source_lang=source_lang,
            target_lang=target_lang,
            progress_callback=translation_progress,
            parallel_enabled=parallel_translation,
            max_workers=translation_workers,
            translation_context=translation_context,
        )

        translation_mode = f"{translation_workers} 路并行" if parallel_translation and translation_workers > 1 else "单路"
        progress(0.80, desc=f"翻译完成，共 {len(translated)} 段字幕（{translation_mode}）。")

        # Step 4: Generate SRT
        progress(0.85, desc="4/5 正在生成 SRT 字幕文件...")
        safe_target = "".join(ch for ch in target_lang if ch.isalnum() or ch in ("-", "_")) or "translated"
        srt_path = str(OUTPUT_DIR / f"{video_stem}_{safe_target}_{run_id}.srt")
        generate_srt(translated, srt_path)

        # Step 5: Embed subtitles
        if burn_subs:
            progress(0.90, desc="5/5 正在硬烧录字幕（需重新编码，速度较慢）...")
            output_video_path = str(OUTPUT_DIR / f"{video_stem}_{safe_target}_{run_id}_hardburned.mp4")
            pos_internal = _SUBTITLE_POSITION_MAP.get(sub_position, "bottom-center")
            burn_subtitles(
                video_path,
                srt_path,
                output_video_path,
                font_size=int(sub_font_size),
                position=pos_internal,
                progress_callback=lambda p, m: progress(0.90 + p * 0.10, desc=f"5/5 {m}"),
            )
            progress(1.0, desc="完成。")
            summary = (
                f"处理完成，字幕已硬烧录入视频画面（{len(translated)} 段）。\n\n"
                f"- 视频文件：`{Path(output_video_path).name}`\n"
                f"- 字幕文件：`{Path(srt_path).name}`\n\n"
                "字幕已永久烧录入画面，可在任意播放器直接显示。"
            )
            return summary, output_video_path, srt_path, translation_context
        else:
            progress(0.90, desc="5/5 正在封装 MKV 软字幕...")
            mkv_path = str(OUTPUT_DIR / f"{video_stem}_{safe_target}_{run_id}_subtitled.mkv")
            mux_subtitles(
                video_path,
                srt_path,
                mkv_path,
                subtitle_title=target_lang,
                progress_callback=lambda p, m: progress(0.90 + p * 0.10, desc=f"5/5 {m}"),
            )
            progress(1.0, desc="完成。")
            summary = (
                f"处理完成，共封装 {len(translated)} 段字幕。\n\n"
                f"- 视频文件：`{Path(mkv_path).name}`\n"
                f"- 字幕文件：`{Path(srt_path).name}`\n\n"
                "建议使用 VLC、IINA、Infuse 等支持 MKV 软字幕的播放器打开。"
            )
            return summary, mkv_path, srt_path, translation_context

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}", None, None, translation_context
    finally:
        if audio_path:
            try:
                os.remove(audio_path)
            except OSError:
                pass


def save_settings(whisper_model_val, source_lang_val, target_lang_val,
                  translation_backend_val, deepseek_api_key_val,
                  deepseek_model_val,
                  ollama_host_val, ollama_model_val,
                  use_translation_context_val,
                  parallel_translation_val, translation_workers_val):
    """Write current UI values back to config.yaml so they survive restarts."""
    updates = {
        "defaults": {
            "whisper_model": whisper_model_val,
            "source_lang": source_lang_val,
            "target_lang": target_lang_val,
            "translation_backend": translation_backend_val,
        },
        "deepseek": {
            "api_key": deepseek_api_key_val or "",
            "model": deepseek_model_val or "deepseek-chat",
        },
        "ollama": {
            "host": ollama_host_val or "http://localhost:11434",
            "model": ollama_model_val or "qwen3:latest",
        },
        "translation": {
            "context_enabled": bool(use_translation_context_val),
            "parallel_enabled": bool(parallel_translation_val),
            "parallel_workers": _coerce_translation_workers(translation_workers_val),
        },
    }
    try:
        save_config(updates)
        return "✅ 设置已保存，下次启动自动生效。"
    except Exception as e:
        return f"❌ 保存失败：{e}"


def _build_translator(backend, api_key, ollama_host, ollama_model, ollama_auto_pull=False, deepseek_model=None):
    """Build translator instance from UI parameters."""
    if backend == "deepseek":
        from pipeline.translator import DeepSeekTranslator
        key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        model = deepseek_model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        return DeepSeekTranslator(api_key=key, model=model)
    elif backend == "ollama":
        from pipeline.translator import OllamaTranslator
        host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        model = ollama_model or os.environ.get("OLLAMA_MODEL", "qwen3:latest")
        return OllamaTranslator(host=host, model=model, auto_pull_model=bool(ollama_auto_pull))
    else:
        raise ValueError(f"Unknown backend: {backend}")


def fetch_deepseek_models(api_key, progress=gr.Progress()):
    """Pull model list from DeepSeek API and return updated Dropdown choices."""
    from pipeline.translator import DeepSeekTranslator
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key or key.strip().startswith("sk-your-"):
        return gr.update(), "❌ 请先填写 DeepSeek API Key。"
    try:
        translator = DeepSeekTranslator(api_key=key)
        models = translator.list_models()
        return gr.update(choices=models, value=models[0] if models else "deepseek-chat"), f"✅ 拉取成功，共 {len(models)} 个模型。"
    except Exception as exc:
        return gr.update(), f"❌ 拉取失败：{exc}"


def pull_ollama_model(ollama_host, ollama_model, progress=gr.Progress()):
    from pipeline.translator import OllamaTranslator

    host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    model = ollama_model or os.environ.get("OLLAMA_MODEL", "qwen3:latest")
    translator = OllamaTranslator(host=host, model=model, auto_pull_model=True)

    try:
        translator.pull_model(progress_callback=lambda pct, msg: progress(pct, desc=msg))
    except Exception as exc:
        return f"拉取失败：{exc}"

    return f"模型 `{model}` 已拉取完成。"


# ── Gradio UI ────────────────────────────────────────────────────────────────

def build_ui():
    ensure_user_config()
    config = load_config()
    defaults = config.get("defaults", {})
    deepseek_config = config.get("deepseek", {})
    ollama_config = config.get("ollama", {})
    translation_config = config.get("translation", {})
    context_enabled_default = bool(translation_config.get("context_enabled", False))
    parallel_enabled_default = bool(translation_config.get("parallel_enabled", False))
    parallel_workers_default = max(2, _coerce_translation_workers(translation_config.get("parallel_workers", 3)))

    theme = gr.themes.Soft(
        primary_hue="blue",
        secondary_hue="slate",
    )

    with gr.Blocks(theme=theme, title="AI 翻译配音") as demo:
        gr.Markdown("""
        # AI 翻译配音
        上传视频后自动提取语音、转录、翻译，并输出带可开关软字幕的 MKV 文件。
        """)

        with gr.Row():
            # ── Left Column: Input & Config ──
            with gr.Column(scale=1):
                gr.Markdown("### 1. 上传视频")
                video_input = gr.Video(label="源视频", sources=["upload"])

                gr.Markdown("### 2. 语音识别")
                whisper_model = gr.Dropdown(
                    choices=["tiny", "base", "small", "medium", "large"],
                    value=defaults.get("whisper_model", "small"),
                    label="Whisper 模型",
                    info="模型越大越准但越慢。正式使用建议 small / medium。",
                )
                source_lang = gr.Dropdown(
                    choices=["auto", "English", "Chinese", "Japanese", "Korean",
                             "French", "German", "Spanish", "Russian", "Portuguese",
                             "Italian", "Arabic", "Hindi", "Thai", "Vietnamese",
                             "Turkish", "Dutch", "Polish", "Indonesian"],
                    value=defaults.get("source_lang", "auto"),
                    label="源语言",
                    info="选择 auto 可自动检测。",
                )

                gr.Markdown("### 3. 翻译设置")
                target_lang = gr.Textbox(
                    value=defaults.get("target_lang", "Chinese"),
                    label="目标语言",
                    info="例如 Chinese、English、Japanese。",
                )
                with gr.Accordion("全局翻译上下文", open=False):
                    video_title = gr.Textbox(
                        value="",
                        label="视频标题/主题",
                        placeholder="可选；留空时使用上传文件名作为主题线索",
                    )
                    use_translation_context = gr.Checkbox(
                        label="翻译前生成全局上下文",
                        value=context_enabled_default,
                        info="开启后会在逐条翻译前额外调用一次当前翻译模型。",
                    )
                translation_backend = gr.Radio(
                    choices=["ollama", "deepseek"],
                    value=defaults.get("translation_backend", "ollama"),
                    label="翻译后端",
                )

                with gr.Accordion("DeepSeek API 设置", open=False):
                    deepseek_api_key = gr.Textbox(
                        label="API Key",
                        type="password",
                        placeholder="sk-...",
                        value=os.environ.get("DEEPSEEK_API_KEY", deepseek_config.get("api_key", "")),
                    )
                    with gr.Row():
                        fetch_deepseek_btn = gr.Button("🔄 拉取模型列表", size="sm")
                        deepseek_model_status = gr.Markdown("")
                    deepseek_model = gr.Dropdown(
                        choices=["deepseek-chat", "deepseek-reasoner"],
                        value=deepseek_config.get("model", "deepseek-chat"),
                        label="DeepSeek 模型",
                        info="点击「拉取模型列表」可获取最新可用模型。",
                        allow_custom_value=True,
                    )

                with gr.Accordion("Ollama 设置", open=False):
                    ollama_host = gr.Textbox(
                        label="Ollama Host",
                        value=os.environ.get("OLLAMA_HOST", ollama_config.get("host", "http://localhost:11434")),
                        placeholder="http://localhost:11434",
                    )
                    ollama_model = gr.Textbox(
                        label="Ollama Model",
                        value=os.environ.get("OLLAMA_MODEL", ollama_config.get("model", "qwen3:latest")),
                        placeholder="qwen3:latest",
                    )
                    ollama_auto_pull = gr.Checkbox(
                        label="缺失时自动拉取",
                        value=False,
                    )
                    pull_ollama_btn = gr.Button("拉取模型")
                    ollama_model_status = gr.Markdown("")

                with gr.Accordion("并行翻译", open=False):
                    parallel_translation = gr.Checkbox(
                        label="启用并行翻译",
                        value=parallel_enabled_default,
                    )
                    translation_workers = gr.Slider(
                        minimum=2,
                        maximum=_MAX_TRANSLATION_WORKERS,
                        step=1,
                        value=parallel_workers_default,
                        label="并行路数",
                        info="仅在启用并行翻译时生效。",
                        visible=parallel_enabled_default,
                    )

                save_btn = gr.Button("💾 保存设置", variant="secondary")
                save_status = gr.Markdown("")

                with gr.Accordion("字幕样式", open=False):
                    burn_subs = gr.Checkbox(
                        label="硬烧录字幕（永久嵌入视频画面）",
                        value=False,
                        info="启用后字幕直接烧录进视频，无需播放器支持字幕轨道；但需重新编码视频，速度较慢。默认输出 MP4。",
                    )
                    sub_font_size = gr.Slider(
                        minimum=12, maximum=72, step=1, value=24,
                        label="字幕字号",
                        visible=False,
                    )
                    sub_position = gr.Dropdown(
                        choices=["底部居中", "底部靠左", "底部靠右",
                                 "顶部居中", "顶部靠左", "顶部靠右", "画面中央"],
                        value="底部居中",
                        label="字幕位置",
                        visible=False,
                    )

                process_btn = gr.Button("开始处理", variant="primary", size="lg")

            # ── Right Column: Output ──
            with gr.Column(scale=1):
                gr.Markdown("### 4. 结果")
                status_text = gr.Markdown("上传视频并点击 **开始处理**。")

                with gr.Row():
                    mkv_output = gr.File(label="下载输出视频", visible=True)
                    srt_output = gr.File(label="下载字幕文件（SRT）", visible=True)

                with gr.Accordion("运行环境", open=False):
                    gr.Markdown(format_runtime_report())

                with gr.Accordion("本次翻译上下文", open=False):
                    context_output = gr.Textbox(
                        label="全局上下文",
                        lines=8,
                        interactive=False,
                    )

                with gr.Accordion("播放说明", open=False):
                    gr.Markdown("""
                    - VLC / IINA：打开 MKV 后在字幕菜单中开启或切换字幕轨道
                    - QuickTime：对 MKV 软字幕支持不完整，不建议使用
                    - SRT 文件也可以在多数播放器里手动加载
                    """)

        # ── Event Binding ──
        process_btn.click(
            fn=process_video,
            inputs=[
                video_input,
                whisper_model,
                source_lang,
                target_lang,
                video_title,
                translation_backend,
                deepseek_api_key,
                deepseek_model,
                ollama_host,
                ollama_model,
                ollama_auto_pull,
                use_translation_context,
                parallel_translation,
                translation_workers,
                burn_subs,
                sub_font_size,
                sub_position,
            ],
            outputs=[status_text, mkv_output, srt_output, context_output],
        )
        pull_ollama_btn.click(
            fn=pull_ollama_model,
            inputs=[ollama_host, ollama_model],
            outputs=[ollama_model_status],
        )
        save_btn.click(
            fn=save_settings,
            inputs=[
                whisper_model,
                source_lang,
                target_lang,
                translation_backend,
                deepseek_api_key,
                deepseek_model,
                ollama_host,
                ollama_model,
                use_translation_context,
                parallel_translation,
                translation_workers,
            ],
            outputs=[save_status],
        )
        fetch_deepseek_btn.click(
            fn=fetch_deepseek_models,
            inputs=[deepseek_api_key],
            outputs=[deepseek_model, deepseek_model_status],
        )
        burn_subs.change(
            fn=lambda v: (gr.update(visible=v), gr.update(visible=v)),
            inputs=[burn_subs],
            outputs=[sub_font_size, sub_position],
        )
        parallel_translation.change(
            fn=lambda v: gr.update(visible=v),
            inputs=[parallel_translation],
            outputs=[translation_workers],
        )

    return demo


if __name__ == "__main__":
    ensure_user_config()
    config = load_config()
    app_config = config.get("app", {})
    demo = build_ui()
    demo.launch(
        server_name=app_config.get("server_name", "127.0.0.1"),
        server_port=int(app_config.get("server_port", 7860)),
        share=False,
        show_error=True,
        show_api=False,
    )
