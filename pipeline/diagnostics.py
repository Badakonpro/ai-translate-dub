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


def check_runtime() -> List[Diagnostic]:
    checks = [
        Diagnostic(
            name="ffmpeg",
            ok=shutil.which("ffmpeg") is not None,
            message="已找到 ffmpeg" if shutil.which("ffmpeg") else "未找到 ffmpeg",
        )
    ]

    has_whisper = importlib.util.find_spec("whisper") is not None
    checks.append(
        Diagnostic(
            "whisper",
            has_whisper,
            "Whisper 依赖已安装" if has_whisper else "Whisper 依赖不可用，请安装 openai-whisper",
        )
    )
    cache_dir = get_whisper_cache_dir()
    checks.append(
        Diagnostic(
            "whisper-cache",
            True,
            f"模型缓存目录：{cache_dir}",
        )
    )

    has_gradio = importlib.util.find_spec("gradio") is not None
    checks.append(
        Diagnostic(
            "gradio",
            has_gradio,
            "Gradio UI 依赖已安装" if has_gradio else "Gradio 依赖不可用，请安装 gradio",
        )
    )

    return checks


def format_runtime_report() -> str:
    lines = ["### 运行环境检查"]
    for item in check_runtime():
        mark = "OK" if item.ok else "缺失"
        lines.append(f"- **{item.name}**：{mark}，{item.message}")
    return "\n".join(lines)
