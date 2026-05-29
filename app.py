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
_ALLOWED_BACKENDS = {"ollama", "deepseek", "openai", "anthropic"}
_MAX_TRANSLATION_WORKERS = 8
_MAX_VIDEO_TITLE_CHARS = 300

# Maps UI h_align labels (Chinese or English) → internal keys used by burn_subtitles()
_SUBTITLE_HALIGN_MAP = {
    "居中": "center",  "Center": "center",
    "靠左": "left",    "Left":   "left",
    "靠右": "right",   "Right":  "right",
}

# Ordered list of target languages shown in the Dropdown
_TARGET_LANGS = [
    "Chinese", "English", "Japanese", "Korean",
    "French", "German", "Spanish", "Russian", "Portuguese",
    "Italian", "Arabic", "Hindi", "Thai", "Vietnamese",
    "Turkish", "Dutch", "Polish", "Indonesian",
    "Swedish", "Danish", "Norwegian", "Finnish",
    "Czech", "Romanian", "Hungarian", "Greek",
    "Hebrew", "Ukrainian", "Malay", "Tagalog",
    "Bengali", "Swahili", "Catalan", "Croatian",
    "Slovak", "Bulgarian", "Serbian", "Lithuanian",
    "Latvian", "Estonian", "Slovenian",
]

_LANG_MAP = {"中文": "zh", "English": "en"}

_I18N = {
    "zh": {
        "app_title": "# QuietKit",
        "app_desc": "上传视频后自动提取语音、转录、翻译，并输出带可开关软字幕的 MKV 文件。",
        "sec_upload": "### 1. 上传视频",
        "video_label": "源视频",
        "sec_asr": "### 2. 语音识别",
        "whisper_label": "Whisper 模型",
        "whisper_info": "模型越大越准但越慢。正式使用建议 small / medium。",
        "source_lang_label": "源语言",
        "source_lang_info": "选择 auto 可自动检测。",
        "sec_translate": "### 3. 翻译设置",
        "target_lang_label": "目标语言",
        "target_lang_info": "从列表中选择目标语言。",
        "video_title_label": "视频标题/主题",
        "video_title_placeholder": "可选；留空时使用上传文件名作为主题线索",
        "use_context_label": "翻译前生成全局上下文",
        "use_context_info": "开启后会在逐条翻译前额外调用一次当前翻译模型。",
        "backend_label": "翻译后端",
        "api_key_label": "API Key",
        "api_key_placeholder": "sk-...",
        "fetch_models_btn": "🔄 拉取模型列表",
        "deepseek_model_label": "DeepSeek 模型",
        "deepseek_model_info": "点击「拉取模型列表」可获取最新可用模型。",
        "ollama_host_label": "Ollama Host",
        "ollama_model_label": "Ollama 模型",
        "ollama_auto_pull_label": "缺失时自动拉取",
        "pull_ollama_btn": "拉取模型",
        "parallel_label": "启用并行翻译",
        "workers_label": "并行路数",
        "workers_info": "仅在启用并行翻译时生效。",
        "save_btn": "💾 保存设置",
        "burn_subs_label": "硬烧录字幕（永久嵌入视频画面）",
        "burn_subs_info": "启用后字幕直接烧录进视频，无需播放器支持字幕轨道；但需重新编码视频，速度较慢。默认输出 MP4。",
        "font_size_label": "字幕字号",
        "h_align_label": "水平对齐",
        "h_align_choices": ["居中", "靠左", "靠右"],
        "margin_v_label": "垂直位置（距底部边缘像素）",
        "margin_v_info": "数值越大字幕越靠近画面中的4，0 = 紧贴底部边缘。20-80 适合底部，200+ 可放居中或顶部。",
        "process_btn": "▶ 开始处理",
        "sec_result": "### 4. 结果",
        "video_out_label": "下载输出视频",
        "srt_out_label": "下载字幕文件（SRT）",
        "ctx_output_label": "全局上下文",
        "playback_md": (
            "- VLC / IINA：打开 MKV 后在字幕菜单中开启或切换字幕轨道\n"
            "- QuickTime：对 MKV 软字幕支持不完整，不建议使用\n"
            "- SRT 文件也可以在多数播放器里手动加载"
        ),
        "save_ok": "✅ 设置已保存，下次启动自动生效。",
        "save_fail": "❌ 保存失败：",
        "fetch_ok": "✅ 拉取成功，共 {} 个模型。",
        "fetch_fail_no_key": "❌ 请先填写 DeepSeek API Key。",
        "fetch_fail": "❌ 拉取失败：",
        "initial_hint": "上传视频并点击 **▶ 开始处理**。",
        "pull_ok": "模型 `{}` 已拉取完成。",
        "pull_fail": "拉取失败：",
        "no_video": "请先上传一个视频文件。",
        "no_speech": "没有检测到可转录的人声。",
        "invalid_whisper": "无效的 Whisper 模型：",
        "invalid_backend": "无效的翻译后端：",
        "step1": "1/5 正在提取音频…",
        "step1_done": "音频提取完成。",
        "step2": "2/5 正在加载 Whisper {} 模型…",
        "step3_ctx": "3/5 正在生成全局翻译上下文…",
        "step3_ctx_done": "全局翻译上下文已生成。",
        "step3_trans": "3/5 正在使用 {} 翻译为 {}…",
        "trans_parallel": "{} 路并行",
        "trans_single": "单路",
        "trans_done": "翻译完成，共 {} 段字幕（{}）。",
        "step4": "4/5 正在生成 SRT 字幕文件…",
        "step5_burn": "5/5 正在硬烧录字幕（需重新编码，速度较慢）…",
        "step5_mux": "5/5 正在封装 MKV 软字幕…",
        "complete": "完成。",
        "summary_burn": (
            "处理完成，字幕已硬烧录入视频画面（{n} 段）。\n\n"
            "- 视频文件：`{video}`\n- 字幕文件：`{srt}`\n\n"
            "字幕已永久烧录入画面，可在任意播放器直接显示。"
        ),
        "summary_mux": (
            "处理完成，共封装 {n} 段字幕。\n\n"
            "- 视频文件：`{video}`\n- 字幕文件：`{srt}`\n\n"
            "建议使用 VLC、IINA、Infuse 等支持 MKV 软字幕的播放器打开。"
        ),
        "transcribe_done": "转录完成，共 {} 段字幕。",
        "acc_context": "全局翻译上下文",
        "acc_deepseek": "DeepSeek API 设置",
        "acc_ollama": "Ollama 设置",
        "acc_parallel": "并行翻译",
        "acc_subtitle": "字幕样式",
        "acc_runtime": "运行环境",
        "acc_ctx_output": "本次翻译上下文",
        "acc_playback": "播放说明",
        "acc_openai": "OpenAI 设置",
        "acc_anthropic": "Anthropic 设置",
        "openai_api_key_label": "OpenAI API Key",
        "openai_base_url_label": "Base URL（留空使用官方默认）",
        "openai_model_label": "OpenAI 模型",
        "fetch_openai_btn": "🔄 拉取模型列表",
        "anthropic_api_key_label": "Anthropic API Key",
        "anthropic_model_label": "Anthropic 模型",
        "fetch_openai_fail_no_key": "❌ 请先填写 OpenAI API Key。",
    },
    "en": {
        "app_title": "# QuietKit",
        "app_desc": "Upload a video to automatically extract audio, transcribe, translate, and output an MKV with switchable subtitles.",
        "sec_upload": "### 1. Upload Video",
        "video_label": "Source Video",
        "sec_asr": "### 2. Speech Recognition",
        "whisper_label": "Whisper Model",
        "whisper_info": "Larger models are more accurate but slower. Recommended: small / medium.",
        "source_lang_label": "Source Language",
        "source_lang_info": "Choose 'auto' for automatic detection.",
        "sec_translate": "### 3. Translation Settings",
        "target_lang_label": "Target Language",
        "target_lang_info": "Select a target language from the list.",
        "video_title_label": "Video Title / Topic",
        "video_title_placeholder": "Optional; leave empty to use the filename as context",
        "use_context_label": "Generate global translation context",
        "use_context_info": "Makes one extra API call before segment translation to build context.",
        "backend_label": "Translation Backend",
        "api_key_label": "API Key",
        "api_key_placeholder": "sk-...",
        "fetch_models_btn": "🔄 Fetch Model List",
        "deepseek_model_label": "DeepSeek Model",
        "deepseek_model_info": "Click 'Fetch Model List' to get the latest available models.",
        "ollama_host_label": "Ollama Host",
        "ollama_model_label": "Ollama Model",
        "ollama_auto_pull_label": "Auto-pull if missing",
        "pull_ollama_btn": "Pull Model",
        "parallel_label": "Enable parallel translation",
        "workers_label": "Parallel Workers",
        "workers_info": "Only effective when parallel translation is enabled.",
        "save_btn": "💾 Save Settings",
        "burn_subs_label": "Hard-burn subtitles (embed permanently into video)",
        "burn_subs_info": "Subtitles are burned into the video. No player subtitle support needed, but re-encoding is required (slower). Outputs MP4.",
        "font_size_label": "Subtitle Font Size",
        "h_align_label": "Horizontal Align",
        "h_align_choices": ["Center", "Left", "Right"],
        "margin_v_label": "Vertical Position (px from edge)",
        "margin_v_info": "Higher value moves subtitle towards screen center. 20-80 = near bottom, 200+ = center or top.",
        "process_btn": "▶ Process Video",
        "sec_result": "### 4. Results",
        "video_out_label": "Download Output Video",
        "srt_out_label": "Download Subtitle File (SRT)",
        "ctx_output_label": "Global Context",
        "playback_md": (
            "- VLC / IINA: Open MKV, then enable subtitles from the subtitle menu\n"
            "- QuickTime: Limited MKV soft-subtitle support; not recommended\n"
            "- You can also load the SRT file manually in most players"
        ),
        "save_ok": "✅ Settings saved. Will take effect on next launch.",
        "save_fail": "❌ Save failed: ",
        "fetch_ok": "✅ Fetched {} models.",
        "fetch_fail_no_key": "❌ Please enter your DeepSeek API Key first.",
        "fetch_fail": "❌ Failed to fetch: ",
        "initial_hint": "Upload a video and click **▶ Process Video**.",
        "pull_ok": "Model `{}` pulled successfully.",
        "pull_fail": "Pull failed: ",
        "no_video": "Please upload a video file first.",
        "no_speech": "No speech detected for transcription.",
        "invalid_whisper": "Invalid Whisper model: ",
        "invalid_backend": "Invalid translation backend: ",
        "step1": "1/5 Extracting audio…",
        "step1_done": "Audio extracted.",
        "step2": "2/5 Loading Whisper {} model…",
        "step3_ctx": "3/5 Generating global translation context…",
        "step3_ctx_done": "Translation context generated.",
        "step3_trans": "3/5 Translating to {} using {}…",
        "trans_parallel": "{}-thread parallel",
        "trans_single": "single-thread",
        "trans_done": "Translation complete: {} segments ({}).",
        "step4": "4/5 Generating SRT subtitle file…",
        "step5_burn": "5/5 Hard-burning subtitles (re-encoding, slower)…",
        "step5_mux": "5/5 Muxing MKV soft subtitles…",
        "complete": "Done.",
        "summary_burn": (
            "Done — subtitles hard-burned into video ({n} segments).\n\n"
            "- Video: `{video}`\n- Subtitles: `{srt}`\n\n"
            "Subtitles are permanently embedded; playable in any video player."
        ),
        "summary_mux": (
            "Done — {n} subtitle segments muxed.\n\n"
            "- Video: `{video}`\n- Subtitles: `{srt}`\n\n"
            "Use VLC, IINA, or Infuse for soft-subtitle MKV playback."
        ),
        "transcribe_done": "Transcription complete: {} segments.",
        "acc_context": "Global Translation Context",
        "acc_deepseek": "DeepSeek API Settings",
        "acc_ollama": "Ollama Settings",
        "acc_parallel": "Parallel Translation",
        "acc_subtitle": "Subtitle Style",
        "acc_runtime": "Runtime Environment",
        "acc_ctx_output": "Translation Context",
        "acc_playback": "Playback Notes",
        "acc_openai": "OpenAI Settings",
        "acc_anthropic": "Anthropic Settings",
        "openai_api_key_label": "OpenAI API Key",
        "openai_base_url_label": "Base URL (leave empty for official default)",
        "openai_model_label": "OpenAI Model",
        "fetch_openai_btn": "🔄 Fetch Model List",
        "anthropic_api_key_label": "Anthropic API Key",
        "anthropic_model_label": "Anthropic Model",
        "fetch_openai_fail_no_key": "❌ Please enter your OpenAI API Key first.",
    },
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
    openai_api_key,
    openai_model,
    openai_base_url,
    anthropic_api_key,
    anthropic_model,
    use_translation_context,
    parallel_translation,
    translation_workers,
    burn_subs,
    sub_font_size,
    sub_h_align,
    sub_margin_v,
    lang="中文",
    progress=gr.Progress(),
):
    """
    Main processing pipeline. Called when the user clicks 'Process'.
    Returns: (status_message, mkv_output_path, srt_output_path, translation_context)
    """
    t = _I18N.get(_LANG_MAP.get(lang, "zh"), _I18N["zh"])

    if video_path is None:
        return t["no_video"], None, None, "", gr.update(value=t["process_btn"], interactive=True)

    # ── Input validation ────────────────────────────────────────────
    if whisper_model not in _ALLOWED_WHISPER_MODELS:
        return t["invalid_whisper"] + whisper_model, None, None, "", gr.update(value=t["process_btn"], interactive=True)
    if translation_backend not in _ALLOWED_BACKENDS:
        return t["invalid_backend"] + translation_backend, None, None, "", gr.update(value=t["process_btn"], interactive=True)
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
        progress(0.05, desc=t["step1"])
        audio_path = extract_audio(video_path, str(OUTPUT_DIR))
        progress(0.15, desc=t["step1_done"])

        # Step 2: Transcribe with Whisper
        progress(0.20, desc=t["step2"].format(whisper_model))

        def transcription_progress(pct, msg):
            progress(0.20 + pct * 0.30, desc=f"2/5 {msg}")

        segments = transcribe(
            audio_path,
            model_size=whisper_model,
            language=source_lang,
            progress_callback=transcription_progress,
        )

        if not segments:
            return t["no_speech"], None, None, "", gr.update(value=t["process_btn"], interactive=True)

        progress(0.50, desc=t["transcribe_done"].format(len(segments)))

        # Override config with UI values
        translator = _build_translator(
            translation_backend, deepseek_api_key, ollama_host, ollama_model, ollama_auto_pull,
            deepseek_model=deepseek_model,
            openai_api_key=openai_api_key, openai_model=openai_model, openai_base_url=openai_base_url,
            anthropic_api_key=anthropic_api_key, anthropic_model=anthropic_model,
        )

        if use_translation_context:
            progress(0.50, desc=t["step3_ctx"])

            def context_progress(pct, msg):
                progress(0.50 + pct * 0.05, desc=f"3/5 {msg}")

            translation_context = translator.build_translation_context(
                segments,
                source_lang=source_lang,
                target_lang=target_lang,
                video_title=context_title,
                progress_callback=context_progress,
            )
            progress(0.55, desc=t["step3_ctx_done"])

        # Step 3: Translate
        progress(0.55, desc=t["step3_trans"].format(target_lang, translation_backend))

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

        if parallel_translation and translation_workers > 1:
            translation_mode = t["trans_parallel"].format(translation_workers)
        else:
            translation_mode = t["trans_single"]
        progress(0.80, desc=t["trans_done"].format(len(translated), translation_mode))

        # Step 4: Generate SRT
        progress(0.85, desc=t["step4"])
        safe_target = "".join(ch for ch in target_lang if ch.isalnum() or ch in ("-", "_")) or "translated"
        srt_path = str(OUTPUT_DIR / f"{video_stem}_{safe_target}_{run_id}.srt")
        generate_srt(translated, srt_path)

        # Step 5: Embed subtitles
        if burn_subs:
            progress(0.90, desc=t["step5_burn"])
            output_video_path = str(OUTPUT_DIR / f"{video_stem}_{safe_target}_{run_id}_hardburned.mp4")
            h_align_internal = _SUBTITLE_HALIGN_MAP.get(sub_h_align, "center")
            burn_subtitles(
                video_path,
                srt_path,
                output_video_path,
                font_size=int(sub_font_size),
                h_align=h_align_internal,
                margin_v=int(sub_margin_v),
                progress_callback=lambda p, m: progress(0.90 + p * 0.10, desc=f"5/5 {m}"),
            )
            progress(1.0, desc=t["complete"])
            summary = t["summary_burn"].format(
                n=len(translated),
                video=Path(output_video_path).name,
                srt=Path(srt_path).name,
            )
            return summary, output_video_path, srt_path, translation_context, gr.update(value=t["process_btn"], interactive=True)
        else:
            progress(0.90, desc=t["step5_mux"])
            mkv_path = str(OUTPUT_DIR / f"{video_stem}_{safe_target}_{run_id}_subtitled.mkv")
            mux_subtitles(
                video_path,
                srt_path,
                mkv_path,
                subtitle_title=target_lang,
                progress_callback=lambda p, m: progress(0.90 + p * 0.10, desc=f"5/5 {m}"),
            )
            progress(1.0, desc=t["complete"])
            summary = t["summary_mux"].format(
                n=len(translated),
                video=Path(mkv_path).name,
                srt=Path(srt_path).name,
            )
            return summary, mkv_path, srt_path, translation_context, gr.update(value=t["process_btn"], interactive=True)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}", None, None, translation_context, gr.update(value=t["process_btn"], interactive=True)
    finally:
        if audio_path:
            try:
                os.remove(audio_path)
            except OSError:
                pass


def save_settings(whisper_model_val, source_lang_val, target_lang_val,
                  translation_backend_val, deepseek_api_key_val, deepseek_model_val,
                  ollama_host_val, ollama_model_val,
                  openai_api_key_val, openai_model_val, openai_base_url_val,
                  anthropic_api_key_val, anthropic_model_val,
                  use_translation_context_val,
                  parallel_translation_val, translation_workers_val,
                  lang="中文"):
    """Write current UI values back to config.yaml so they survive restarts."""
    t = _I18N.get(_LANG_MAP.get(lang, "zh"), _I18N["zh"])
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
        "openai": {
            "api_key": openai_api_key_val or "",
            "model": openai_model_val or "gpt-4o",
            "base_url": openai_base_url_val or "https://api.openai.com/v1",
        },
        "anthropic": {
            "api_key": anthropic_api_key_val or "",
            "model": anthropic_model_val or "claude-sonnet-4-5",
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
        return t["save_ok"]
    except Exception as e:
        return t["save_fail"] + str(e)


def _build_translator(backend, deepseek_api_key, ollama_host, ollama_model, ollama_auto_pull=False,
                      deepseek_model=None, openai_api_key=None, openai_model=None, openai_base_url=None,
                      anthropic_api_key=None, anthropic_model=None):
    """Build translator instance from UI parameters."""
    if backend == "deepseek":
        from pipeline.translator import DeepSeekTranslator
        key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        model = deepseek_model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        return DeepSeekTranslator(api_key=key, model=model)
    elif backend == "openai":
        from pipeline.translator import OpenAITranslator
        key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        model = openai_model or os.environ.get("OPENAI_MODEL", "gpt-4o")
        base_url = (openai_base_url or "").strip() or "https://api.openai.com/v1"
        return OpenAITranslator(api_key=key, model=model, base_url=base_url)
    elif backend == "anthropic":
        from pipeline.translator import AnthropicTranslator
        key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        model = anthropic_model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        return AnthropicTranslator(api_key=key, model=model)
    elif backend == "ollama":
        from pipeline.translator import OllamaTranslator
        host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        model = ollama_model or os.environ.get("OLLAMA_MODEL", "qwen3:latest")
        return OllamaTranslator(host=host, model=model, auto_pull_model=bool(ollama_auto_pull))
    else:
        raise ValueError(f"Unknown backend: {backend}")


def fetch_deepseek_models(api_key, lang="中文", progress=gr.Progress()):
    """Pull model list from DeepSeek API and return updated Dropdown choices."""
    from pipeline.translator import DeepSeekTranslator
    t = _I18N.get(_LANG_MAP.get(lang, "zh"), _I18N["zh"])
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key or key.strip().startswith("sk-your-"):
        return gr.update(), t["fetch_fail_no_key"]
    try:
        translator = DeepSeekTranslator(api_key=key)
        models = translator.list_models()
        return gr.update(choices=models, value=models[0] if models else "deepseek-chat"), t["fetch_ok"].format(len(models))
    except Exception as exc:
        return gr.update(), t["fetch_fail"] + str(exc)


def fetch_openai_models(api_key, base_url, lang="中文", progress=gr.Progress()):
    """Pull model list from OpenAI API and return updated Dropdown choices."""
    from pipeline.translator import OpenAITranslator
    t = _I18N.get(_LANG_MAP.get(lang, "zh"), _I18N["zh"])
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key or key.strip().startswith("sk-your-"):
        return gr.update(), t["fetch_openai_fail_no_key"]
    try:
        resolved_url = (base_url or "").strip() or "https://api.openai.com/v1"
        translator = OpenAITranslator(api_key=key, base_url=resolved_url)
        models = translator.list_models()
        return gr.update(choices=models, value=models[0] if models else "gpt-4o"), t["fetch_ok"].format(len(models))
    except Exception as exc:
        return gr.update(), t["fetch_fail"] + str(exc)


def pull_ollama_model(ollama_host, ollama_model, lang="中文", progress=gr.Progress()):
    from pipeline.translator import OllamaTranslator
    t = _I18N.get(_LANG_MAP.get(lang, "zh"), _I18N["zh"])

    host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    model = ollama_model or os.environ.get("OLLAMA_MODEL", "qwen3:latest")
    translator = OllamaTranslator(host=host, model=model, auto_pull_model=True)

    try:
        translator.pull_model(progress_callback=lambda pct, msg: progress(pct, desc=msg))
    except Exception as exc:
        return t["pull_fail"] + str(exc)

    return t["pull_ok"].format(model)


# ── Gradio UI ────────────────────────────────────────────────────────────────

def build_ui():
    ensure_user_config()
    config = load_config()
    defaults = config.get("defaults", {})
    deepseek_config = config.get("deepseek", {})
    openai_config = config.get("openai", {})
    anthropic_config = config.get("anthropic", {})
    ollama_config = config.get("ollama", {})
    translation_config = config.get("translation", {})
    context_enabled_default = bool(translation_config.get("context_enabled", False))
    parallel_enabled_default = bool(translation_config.get("parallel_enabled", False))
    parallel_workers_default = max(2, _coerce_translation_workers(translation_config.get("parallel_workers", 3)))

    def switch_language(lang_label):
        lang_code = _LANG_MAP.get(lang_label, "zh")
        t = _I18N.get(lang_code, _I18N["zh"])
        return (
            gr.update(value=t["app_title"]),
            gr.update(value=t["app_desc"]),
            gr.update(value=t["sec_upload"]),
            gr.update(label=t["video_label"]),
            gr.update(value=t["sec_asr"]),
            gr.update(label=t["whisper_label"], info=t["whisper_info"]),
            gr.update(label=t["source_lang_label"], info=t["source_lang_info"]),
            gr.update(value=t["sec_translate"]),
            gr.update(label=t["target_lang_label"], info=t["target_lang_info"]),
            gr.update(label=t["video_title_label"], placeholder=t["video_title_placeholder"]),
            gr.update(label=t["use_context_label"], info=t["use_context_info"]),
            gr.update(label=t["backend_label"]),
            gr.update(label=t["api_key_label"], placeholder=t["api_key_placeholder"]),
            gr.update(value=t["fetch_models_btn"]),
            gr.update(label=t["deepseek_model_label"], info=t["deepseek_model_info"]),
            gr.update(label=t["ollama_host_label"]),
            gr.update(label=t["ollama_model_label"]),
            gr.update(label=t["ollama_auto_pull_label"]),
            gr.update(value=t["pull_ollama_btn"]),
            gr.update(label=t["parallel_label"]),
            gr.update(label=t["workers_label"], info=t["workers_info"]),
            gr.update(value=t["save_btn"]),
            gr.update(label=t["burn_subs_label"], info=t["burn_subs_info"]),
            gr.update(label=t["font_size_label"]),
            gr.update(label=t["h_align_label"], choices=t["h_align_choices"], value=t["h_align_choices"][0]),
            gr.update(label=t["margin_v_label"]),
            gr.update(value=t["process_btn"]),
            gr.update(value=t["sec_result"]),
            gr.update(label=t["video_out_label"]),
            gr.update(label=t["srt_out_label"]),
            gr.update(label=t["ctx_output_label"]),
            gr.update(value=t["playback_md"]),
            gr.update(value=t["initial_hint"]),
            gr.update(label=t["acc_context"]),
            gr.update(label=t["acc_ollama"]),
            gr.update(label=t["acc_openai"]),
            gr.update(label=t["acc_anthropic"]),
            gr.update(label=t["acc_deepseek"]),
            gr.update(label=t["acc_parallel"]),
            gr.update(label=t["acc_subtitle"]),
            gr.update(label=t["acc_runtime"]),
            gr.update(value=format_runtime_report(lang_code)),
            gr.update(label=t["acc_ctx_output"]),
            gr.update(label=t["acc_playback"]),
        )

    theme = gr.themes.Soft(
        primary_hue="blue",
        secondary_hue="slate",
    )

    with gr.Blocks(theme=theme, title="QuietKit") as demo:
        # ── Header row with title + language switch ──
        with gr.Row(equal_height=True):
            with gr.Column(scale=4):
                app_title_md = gr.Markdown("# QuietKit")
            with gr.Column(scale=1, min_width=140):
                lang_radio = gr.Radio(
                    choices=["中文", "English"],
                    value="中文",
                    show_label=False,
                    container=False,
                )
        app_desc_md = gr.Markdown(
            "上传视频后自动提取语音、转录、翻译，并输出带可开关软字幕的 MKV 文件。"
        )

        with gr.Row():
            # ── Left Column: Input & Config ──
            with gr.Column(scale=1):
                sec_upload_md = gr.Markdown("### 1. 上传视频")
                video_input = gr.Video(label="源视频", sources=["upload"])

                sec_asr_md = gr.Markdown("### 2. 语音识别")
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

                sec_translate_md = gr.Markdown("### 3. 翻译设置")
                target_lang = gr.Dropdown(
                    choices=_TARGET_LANGS,
                    value=defaults.get("target_lang", "Chinese"),
                    label="目标语言",
                    info="选择常用语言，或直接输入自定义语言名称。",
                )
                with gr.Accordion(_I18N["zh"]["acc_context"], open=False) as acc_context:
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
                    choices=["ollama", "deepseek", "openai", "anthropic"],
                    value=defaults.get("translation_backend", "ollama"),
                    label="翻译后端",
                )

                with gr.Accordion(_I18N["zh"]["acc_ollama"], open=False) as acc_ollama:
                    ollama_host = gr.Textbox(
                        label="Ollama Host",
                        value=os.environ.get("OLLAMA_HOST", ollama_config.get("host", "http://localhost:11434")),
                        placeholder="http://localhost:11434",
                    )
                    ollama_model = gr.Textbox(
                        label="Ollama 模型",
                        value=os.environ.get("OLLAMA_MODEL", ollama_config.get("model", "qwen3:latest")),
                        placeholder="qwen3:latest",
                    )
                    ollama_auto_pull = gr.Checkbox(
                        label="缺失时自动拉取",
                        value=False,
                    )
                    pull_ollama_btn = gr.Button("拉取模型")
                    ollama_model_status = gr.Markdown("")

                with gr.Accordion(_I18N["zh"]["acc_openai"], open=False) as acc_openai:
                    openai_api_key = gr.Textbox(
                        label=_I18N["zh"]["openai_api_key_label"],
                        type="password",
                        placeholder="sk-...",
                        value=os.environ.get("OPENAI_API_KEY", openai_config.get("api_key", "")),
                    )
                    openai_base_url = gr.Textbox(
                        label=_I18N["zh"]["openai_base_url_label"],
                        value=openai_config.get("base_url", "https://api.openai.com/v1"),
                        placeholder="https://api.openai.com/v1",
                    )
                    with gr.Row():
                        fetch_openai_btn = gr.Button("🔄 拉取模型列表", size="sm")
                        openai_model_status = gr.Markdown("")
                    from pipeline.translator import OpenAITranslator
                    openai_model = gr.Dropdown(
                        choices=OpenAITranslator._FALLBACK_MODELS,
                        value=openai_config.get("model", "gpt-4o"),
                        label=_I18N["zh"]["openai_model_label"],
                        allow_custom_value=True,
                    )

                with gr.Accordion(_I18N["zh"]["acc_anthropic"], open=False) as acc_anthropic:
                    anthropic_api_key = gr.Textbox(
                        label=_I18N["zh"]["anthropic_api_key_label"],
                        type="password",
                        placeholder="sk-ant-...",
                        value=os.environ.get("ANTHROPIC_API_KEY", anthropic_config.get("api_key", "")),
                    )
                    from pipeline.translator import AnthropicTranslator
                    anthropic_model = gr.Dropdown(
                        choices=AnthropicTranslator._FALLBACK_MODELS,
                        value=anthropic_config.get("model", "claude-sonnet-4-5"),
                        label=_I18N["zh"]["anthropic_model_label"],
                    )

                with gr.Accordion(_I18N["zh"]["acc_deepseek"], open=False) as acc_deepseek:
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

                with gr.Accordion(_I18N["zh"]["acc_parallel"], open=False) as acc_parallel:
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

                with gr.Accordion(_I18N["zh"]["acc_subtitle"], open=False) as acc_subtitle:
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
                    sub_h_align = gr.Radio(
                        choices=_I18N["zh"]["h_align_choices"],
                        value=_I18N["zh"]["h_align_choices"][0],
                        label="水平对齐",
                        visible=False,
                    )
                    sub_margin_v = gr.Slider(
                        minimum=0, maximum=500, step=1, value=20,
                        label="垂直位置（距底部边缘像素）",
                        info="数值越大字幕越靠近画面中央，0 = 紧贴底部边缘。20-80 适合底部，200+ 可放居中或顶部。",
                        visible=False,
                    )

                process_btn = gr.Button("▶ 开始处理", variant="primary", size="lg")

            # ── Right Column: Output ──
            with gr.Column(scale=1):
                sec_result_md = gr.Markdown("### 4. 结果")
                status_text = gr.Markdown("上传视频并点击 **开始处理**。")

                with gr.Row():
                    mkv_output = gr.File(label="下载输出视频", visible=True)
                    srt_output = gr.File(label="下载字幕文件（SRT）", visible=True)

                with gr.Accordion(_I18N["zh"]["acc_runtime"], open=False) as acc_runtime:
                    runtime_md = gr.Markdown(format_runtime_report("zh"))

                with gr.Accordion(_I18N["zh"]["acc_ctx_output"], open=False) as acc_ctx_output:
                    context_output = gr.Textbox(
                        label="全局上下文",
                        lines=8,
                        interactive=False,
                    )

                with gr.Accordion(_I18N["zh"]["acc_playback"], open=False) as acc_playback:
                    playback_md_comp = gr.Markdown(
                        "- VLC / IINA：打开 MKV 后在字幕菜单中开启或切换字幕轨道\n"
                        "- QuickTime：对 MKV 软字幕支持不完整，不建议使用\n"
                        "- SRT 文件也可以在多数播放器里手动加载"
                    )

        # ── Event Binding ──
        process_btn.click(
            fn=lambda: gr.update(value="⏳ 处理中...", interactive=False),
            outputs=[process_btn],
            queue=False,
        ).then(
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
                openai_api_key,
                openai_model,
                openai_base_url,
                anthropic_api_key,
                anthropic_model,
                use_translation_context,
                parallel_translation,
                translation_workers,
                burn_subs,
                sub_font_size,
                sub_h_align,
                sub_margin_v,
                lang_radio,
            ],
            outputs=[status_text, mkv_output, srt_output, context_output, process_btn],
        )
        pull_ollama_btn.click(
            fn=pull_ollama_model,
            inputs=[ollama_host, ollama_model, lang_radio],
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
                openai_api_key,
                openai_model,
                openai_base_url,
                anthropic_api_key,
                anthropic_model,
                use_translation_context,
                parallel_translation,
                translation_workers,
                lang_radio,
            ],
            outputs=[save_status],
        )
        fetch_deepseek_btn.click(
            fn=fetch_deepseek_models,
            inputs=[deepseek_api_key, lang_radio],
            outputs=[deepseek_model, deepseek_model_status],
        )
        fetch_openai_btn.click(
            fn=fetch_openai_models,
            inputs=[openai_api_key, openai_base_url, lang_radio],
            outputs=[openai_model, openai_model_status],
        )
        burn_subs.change(
            fn=lambda v: (gr.update(visible=v), gr.update(visible=v), gr.update(visible=v)),
            inputs=[burn_subs],
            outputs=[sub_font_size, sub_h_align, sub_margin_v],
        )
        parallel_translation.change(
            fn=lambda v: gr.update(visible=v),
            inputs=[parallel_translation],
            outputs=[translation_workers],
        )
        lang_radio.change(
            fn=switch_language,
            inputs=[lang_radio],
            outputs=[
                app_title_md, app_desc_md, sec_upload_md, video_input,
                sec_asr_md, whisper_model, source_lang,
                sec_translate_md, target_lang, video_title, use_translation_context,
                translation_backend, deepseek_api_key, fetch_deepseek_btn, deepseek_model,
                ollama_host, ollama_model, ollama_auto_pull, pull_ollama_btn,
                parallel_translation, translation_workers, save_btn,
                burn_subs, sub_font_size, sub_h_align, sub_margin_v,
                process_btn, sec_result_md, mkv_output, srt_output, context_output,
                playback_md_comp, status_text,
                acc_context, acc_ollama, acc_openai, acc_anthropic, acc_deepseek, acc_parallel,
                acc_subtitle, acc_runtime, runtime_md, acc_ctx_output, acc_playback,
            ],
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
