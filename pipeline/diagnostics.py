import importlib.util
import shutil
from dataclasses import dataclass
from typing import List

from pipeline.transcriber import get_whisper_cache_dir


@dataclass
class Diagnostic:
    name: str
    ok: bool
    message: str


_MSGS = {
    "zh": {
        "ffmpeg_ok": "已找到 ffmpeg",
        "ffmpeg_fail": "未找到 ffmpeg",
        "whisper_ok": "Whisper 依赖已安装",
        "whisper_fail": "Whisper 依赖不可用，请安装 openai-whisper",
        "whisper_cache": "模型缓存目录：{cache_dir}",
        "gradio_ok": "Gradio UI 依赖已安装",
        "gradio_fail": "Gradio 依赖不可用，请安装 gradio",
        "heading": "### 运行环境检查",
        "ok_mark": "OK",
        "fail_mark": "缺失",
        "row_fmt": "- **{name}**：{mark}，{msg}",
    },
    "en": {
        "ffmpeg_ok": "ffmpeg found",
        "ffmpeg_fail": "ffmpeg not found",
        "whisper_ok": "Whisper dependency installed",
        "whisper_fail": "Whisper unavailable, install openai-whisper",
        "whisper_cache": "Model cache dir: {cache_dir}",
        "gradio_ok": "Gradio UI dependency installed",
        "gradio_fail": "Gradio unavailable, install gradio",
        "heading": "### Runtime Environment Check",
        "ok_mark": "OK",
        "fail_mark": "Missing",
        "row_fmt": "- **{name}**: {mark}, {msg}",
    },
}


def check_runtime(lang: str = "zh") -> List[Diagnostic]:
    m = _MSGS.get(lang, _MSGS["zh"])
    checks = [
        Diagnostic(
            name="ffmpeg",
            ok=shutil.which("ffmpeg") is not None,
            message=m["ffmpeg_ok"] if shutil.which("ffmpeg") else m["ffmpeg_fail"],
        )
    ]

    has_whisper = importlib.util.find_spec("whisper") is not None
    checks.append(
        Diagnostic(
            "whisper",
            has_whisper,
            m["whisper_ok"] if has_whisper else m["whisper_fail"],
        )
    )
    cache_dir = get_whisper_cache_dir()
    checks.append(
        Diagnostic(
            "whisper-cache",
            True,
            m["whisper_cache"].format(cache_dir=cache_dir),
        )
    )

    has_gradio = importlib.util.find_spec("gradio") is not None
    checks.append(
        Diagnostic(
            "gradio",
            has_gradio,
            m["gradio_ok"] if has_gradio else m["gradio_fail"],
        )
    )

    return checks


def format_runtime_report(lang: str = "zh") -> str:
    m = _MSGS.get(lang, _MSGS["zh"])
    lines = [m["heading"]]
    for item in check_runtime(lang):
        mark = m["ok_mark"] if item.ok else m["fail_mark"]
        lines.append(m["row_fmt"].format(name=item.name, mark=mark, msg=item.message))
    return "\n".join(lines)
