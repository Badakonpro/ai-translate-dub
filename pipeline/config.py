import os
import shutil
from pathlib import Path
from typing import Any, Dict

import yaml


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "outputs"
CONFIG_PATH = PROJECT_DIR / "config.yaml"
CONFIG_EXAMPLE_PATH = PROJECT_DIR / "config.example.yaml"


DEFAULT_CONFIG: Dict[str, Any] = {
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "ollama": {
        "host": "http://localhost:11434",
        "model": "qwen3:latest",
    },
    "defaults": {
        "whisper_model": "small",
        "source_lang": "auto",
        "target_lang": "Chinese",
        "translation_backend": "ollama",
    },
    "translation": {
        "context_enabled": False,
        "parallel_enabled": False,
        "parallel_workers": 3,
    },
    "app": {
        "server_name": "127.0.0.1",
        "server_port": 7860,
        "output_dir": "outputs",
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def ensure_user_config() -> Path:
    if not CONFIG_PATH.exists() and CONFIG_EXAMPLE_PATH.exists():
        shutil.copyfile(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
    return CONFIG_PATH


def load_config(config_path: str = None) -> Dict[str, Any]:
    path = Path(config_path) if config_path else CONFIG_PATH
    if not path.exists():
        return DEFAULT_CONFIG

    with open(path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}
    return deep_merge(DEFAULT_CONFIG, user_config)


def get_output_dir(config: Dict[str, Any] = None) -> Path:
    cfg = config or load_config()
    configured = cfg.get("app", {}).get("output_dir", "outputs")
    output_dir = Path(configured)
    if not output_dir.is_absolute():
        output_dir = PROJECT_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def env_or_config(env_name: str, config_value: str = "") -> str:
    return os.environ.get(env_name) or config_value or ""


def save_config(updates: Dict[str, Any], config_path: str = None) -> None:
    """Persist settings back to config.yaml.

    *updates* is a nested dict that will be deep-merged over the existing file
    so only the supplied keys are changed.
    """
    path = Path(config_path) if config_path else CONFIG_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            current = yaml.safe_load(f) or {}
    else:
        current = {}
    merged = deep_merge(current, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, default_flow_style=False)


def find_ffmpeg() -> str:
    """Return the full path to the ffmpeg executable.

    Resolution order:
    1. FFMPEG_PATH environment variable (explicit override from Electron)
    2. PATH search via shutil.which
    3. Common installation paths for macOS (Homebrew) and Linux

    macOS GUI apps launched from Finder/DMG have a stripped PATH that does
    not include /opt/homebrew/bin, so we fall back to known locations.
    """
    explicit = os.environ.get("FFMPEG_PATH", "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit

    cmd = shutil.which("ffmpeg")
    if cmd:
        return cmd

    fallbacks = [
        "/opt/homebrew/bin/ffmpeg",   # Homebrew on Apple Silicon
        "/usr/local/bin/ffmpeg",       # Homebrew on Intel macOS / Linux
        "/usr/bin/ffmpeg",
        "/snap/bin/ffmpeg",
    ]
    for path_ in fallbacks:
        if os.path.isfile(path_) and os.access(path_, os.X_OK):
            return path_

    raise FileNotFoundError(
        "ffmpeg not found. Please install it (e.g. 'brew install ffmpeg') "
        "and make sure it is on your PATH."
    )
