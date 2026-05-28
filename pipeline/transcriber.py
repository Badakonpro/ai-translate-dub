import logging
import hashlib
import os
import platform
import shutil
import threading
import time
import urllib.request
import wave
from urllib.parse import urlparse
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Language name to code mapping for common languages
LANG_MAP = {
    "auto": None,
    "english": "en", "chinese": "zh", "japanese": "ja", "korean": "ko",
    "french": "fr", "german": "de", "spanish": "es", "russian": "ru",
    "portuguese": "pt", "italian": "it", "arabic": "ar", "hindi": "hi",
    "thai": "th", "vietnamese": "vi", "turkish": "tr", "dutch": "nl",
    "polish": "pl", "indonesian": "id",
}

_MAX_DOWNLOAD_ATTEMPTS = 3
_MODEL_MEMORY_CACHE = {}
_MODEL_CACHE_LOCK = threading.Lock()
_WHISPER_TQDM_LOCK = threading.Lock()

# Approximate model sizes (MB) for display / ETA purposes
_MODEL_SIZES_MB = {
    "tiny": 75, "base": 145, "small": 461, "medium": 1457, "large": 2948,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_whisper_cache_dir() -> Path:
    """Return the persistent directory used for Whisper model files."""
    explicit = os.environ.get("WHISPER_CACHE_DIR", "").strip()
    if explicit:
        return Path(os.path.expanduser(explicit))

    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "AI翻译配音" / "whisper-models"

    return Path(os.path.expanduser("~/.cache/AI翻译配音/whisper-models"))

def get_language_code(lang: str) -> Optional[str]:
    """Convert a language name to its ISO code. Returns None for 'auto'."""
    if not lang or lang.lower() == "auto":
        return None
    return LANG_MAP.get(lang.lower(), lang.lower())


def _audio_duration_seconds(audio_path: str) -> Optional[float]:
    try:
        with wave.open(str(audio_path), "rb") as wav:
            rate = wav.getframerate()
            if rate <= 0:
                return None
            return wav.getnframes() / float(rate)
    except Exception:
        return None


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "未知"
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:d}:{sec:02d}"


def _format_transcribe_status(
    pct: float,
    elapsed: float,
    duration: Optional[float],
    model_size: str,
) -> str:
    pct = max(0.0, min(1.0, pct))
    waited = _format_duration(elapsed)
    if pct <= 0.001:
        if duration:
            return (
                f"正在转录：正在分析第一段音频，音频 {_format_duration(duration)}，"
                f"已等待 {waited}，模型 {model_size}"
            )
        return f"正在转录：正在分析第一段音频，已等待 {waited}，模型 {model_size}"
    if duration:
        done = duration * pct
        return (
            f"正在转录：{_format_duration(done)} / {_format_duration(duration)} "
            f"({pct:.0%})，已等待 {waited}，模型 {model_size}"
        )
    return f"正在转录：{pct:.0%}，已等待 {waited}，模型 {model_size}"


def _model_url(model_size: str) -> Optional[str]:
    try:
        import whisper as _w
        return _w._MODELS.get(model_size)
    except Exception:
        return None


def _model_filename(model_size: str) -> str:
    url = _model_url(model_size) or ""
    parsed_name = Path(urlparse(url).path).name
    return parsed_name or f"{model_size}.pt"


def _cached_model_path(model_size: str) -> Path:
    return get_whisper_cache_dir() / _model_filename(model_size)


def _legacy_cached_model_path(model_size: str) -> Path:
    return get_whisper_cache_dir() / f"{model_size}.pt"


def _old_default_cache_paths(model_size: str) -> List[Path]:
    old_dir = Path(os.path.expanduser("~/.cache/whisper"))
    return [
        old_dir / _model_filename(model_size),
        old_dir / f"{model_size}.pt",
    ]


def _expected_sha256(model_size: str) -> Optional[str]:
    """Extract the expected SHA256 from whisper's CDN URL.
    URL format: .../models/<64-char sha256>/<name>.pt
    """
    url = _model_url(model_size) or ""
    for part in reversed(url.rstrip("/").split("/")):
        if len(part) == 64 and all(c in "0123456789abcdef" for c in part):
            return part
    return None


def _sha256_of_file(path: Path) -> str:
    """Memory-efficient SHA256 (chunk by chunk, not all at once)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_hash_is_valid(path: Path, model_size: str) -> bool:
    expected = _expected_sha256(model_size)
    if not expected:
        return True
    actual = _sha256_of_file(path)
    if actual != expected:
        logger.warning("SHA256 mismatch for %s: expected %s, got %s",
                       path, expected, actual)
        return False
    return True


def _purge_model_cache(model_size: str) -> None:
    cache_dir = get_whisper_cache_dir()
    stems = {model_size, Path(_model_filename(model_size)).stem}
    paths = []
    for stem in stems:
        paths.extend(cache_dir.glob(f"{stem}*"))
    for f in set(paths):
        try:
            f.unlink()
            logger.warning("Deleted model cache file: %s", f)
        except OSError as e:
            logger.warning("Could not delete %s: %s", f, e)


def _verify_cached_model(model_size: str) -> bool:
    """Return True only if the cached .pt exists and its SHA256 matches."""
    cached = _cached_model_path(model_size)
    if not cached.exists():
        legacy = _legacy_cached_model_path(model_size)
        if legacy != cached and legacy.exists() and _file_hash_is_valid(legacy, model_size):
            cached.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(cached)
            logger.info("Migrated Whisper model cache from %s to %s", legacy, cached)
            return True
        for old_cache in _old_default_cache_paths(model_size):
            if old_cache != cached and old_cache.exists() and _file_hash_is_valid(old_cache, model_size):
                cached.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_cache, cached)
                logger.info("Copied existing Whisper model cache from %s to %s", old_cache, cached)
                return True
        return False
    return _file_hash_is_valid(cached, model_size)


def _download_model(model_size: str, progress_callback=None) -> None:
    """Stream-download a Whisper model file with live MB/% progress.

    Uses a .tmp file so a partial download never leaves a corrupt .pt behind.
    Socket timeout is 300 s (idle between chunks) to handle slow networks.
    """
    url = _model_url(model_size)
    if not url:
        raise ValueError(f"Unknown model size: {model_size!r}")

    cache_dir = get_whisper_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = _cached_model_path(model_size)
    tmp = target.with_name(target.name + ".tmp")
    approx_mb = _MODEL_SIZES_MB.get(model_size, 0)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            chunk_size = 524288  # 512 KB

            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        mb_done = downloaded / 1_048_576
                        if total:
                            mb_total = total / 1_048_576
                            pct = downloaded / total
                            msg = (f"正在下载 Whisper {model_size} 模型："
                                   f"{mb_done:.0f} / {mb_total:.0f} MB  ({pct:.0%})")
                        else:
                            ref = f" / ~{approx_mb} MB" if approx_mb else ""
                            msg = f"正在下载 Whisper {model_size} 模型：{mb_done:.0f}{ref} MB"
                        progress_callback(0.10 + min(pct if total else 0, 1) * 0.07, msg)

        tmp.replace(target)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ── Public API ────────────────────────────────────────────────────────────────

def transcribe(
    audio_path: str,
    model_size: str = "medium",
    language: str = "auto",
    progress_callback=None,
) -> List[dict]:
    """
    Transcribe audio with Whisper and return timestamped segments.
    """
    model = _load_model_with_retry(model_size, progress_callback)

    transcribe_opts: dict = {}
    lang_code = get_language_code(language)
    if lang_code:
        transcribe_opts["language"] = lang_code

    result = _run_transcribe_with_progress(
        model,
        audio_path,
        model_size=model_size,
        progress_callback=progress_callback,
        transcribe_opts=transcribe_opts,
    )

    if progress_callback:
        progress_callback(0.90, "转录完成。")

    segments = []
    for seg in result["segments"]:
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })

    logger.info("Transcribed %d segments, language: %s",
                len(segments), result.get("language", "unknown"))
    return segments


def _run_transcribe_with_progress(
    model,
    audio_path: str,
    model_size: str,
    progress_callback=None,
    transcribe_opts: dict = None,
) -> dict:
    """Run Whisper transcribe while forwarding real tqdm progress plus heartbeats."""
    transcribe_opts = transcribe_opts or {}
    duration = _audio_duration_seconds(audio_path)
    started_at = time.monotonic()
    state = {
        "pct": 0.0,
        "updated_at": started_at,
    }
    state_lock = threading.Lock()
    result_box: list = []
    error_box: list = []

    def set_progress(pct: float) -> None:
        with state_lock:
            state["pct"] = max(state["pct"], min(1.0, max(0.0, pct)))
            state["updated_at"] = time.monotonic()

    def current_status() -> tuple[float, str]:
        with state_lock:
            pct = state["pct"]
        elapsed = time.monotonic() - started_at
        if pct <= 0.001:
            mapped_pct = 0.20 + min(elapsed / 120, 1.0) * 0.03
        else:
            mapped_pct = 0.20 + min(pct, 0.985) * 0.68
        return mapped_pct, _format_transcribe_status(pct, elapsed, duration, model_size)

    class ProgressTqdm:
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get("total")
            if self.total is None and args:
                self.total = args[0]
            self.total = float(self.total or 0)
            self.disable = bool(kwargs.get("disable", False))
            self.n = 0.0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, n=1):
            self.n += n
            if not self.disable and self.total > 0:
                set_progress(self.n / self.total)

    def _do_transcribe():
        try:
            transcribe_func = getattr(model.transcribe, "__func__", model.transcribe)
            tqdm_module = getattr(transcribe_func, "__globals__", {}).get("tqdm")
            if tqdm_module is None or not hasattr(tqdm_module, "tqdm"):
                result_box.append(
                    model.transcribe(audio_path, word_timestamps=True, verbose=False, **transcribe_opts)
                )
                return

            with _WHISPER_TQDM_LOCK:
                original_tqdm = tqdm_module.tqdm
                tqdm_module.tqdm = ProgressTqdm
                try:
                    result_box.append(
                        model.transcribe(audio_path, word_timestamps=True, verbose=False, **transcribe_opts)
                    )
                finally:
                    tqdm_module.tqdm = original_tqdm
        except Exception as exc:
            error_box.append(exc)

    if progress_callback:
        duration_label = _format_duration(duration)
        progress_callback(0.20, f"开始转录：音频 {duration_label}，模型 {model_size}。")

    thread = threading.Thread(target=_do_transcribe, daemon=True)
    thread.start()

    last_emit = 0.0
    last_pct = -1.0
    while thread.is_alive():
        time.sleep(1)
        if not progress_callback:
            continue
        pct, msg = current_status()
        now = time.monotonic()
        if pct - last_pct >= 0.006 or now - last_emit >= 3:
            progress_callback(pct, msg)
            last_emit = now
            last_pct = pct

    thread.join()

    if error_box:
        raise error_box[0]
    if not result_box:
        raise RuntimeError("Whisper 转录未返回结果。")

    if progress_callback:
        progress_callback(0.90, "转录完成。")
    return result_box[0]


# ── Internal: download + load with retry ─────────────────────────────────────

def _load_model_with_retry(model_size: str, progress_callback=None):
    """Download (with byte-level progress) then load a Whisper model.

    Flow per attempt:
      1. Pre-verify any existing cache (memory-efficient SHA256).
         If corrupt/missing → purge and stream-download with progress.
      2. Verify the freshly downloaded file before handing to whisper.
      3. Load model in a background thread; heartbeat every 2 s so the UI
         does not freeze during the 1-3 min load for medium/large models.
      4. On checksum failure during load → purge and retry (up to 3 x).
    """
    import whisper

    cache_dir = get_whisper_cache_dir()
    cache_key = (model_size, str(cache_dir))

    with _MODEL_CACHE_LOCK:
        cached_model = _MODEL_MEMORY_CACHE.get(cache_key)
        if cached_model is not None:
            if progress_callback:
                progress_callback(0.20, f"已复用内存中的 Whisper {model_size} 模型。")
            return cached_model

        for attempt in range(1, _MAX_DOWNLOAD_ATTEMPTS + 1):

            # ── 1. Pre-verify cache ────────────────────────────────────────────
            if not _verify_cached_model(model_size):
                _purge_model_cache(model_size)
                approx = _MODEL_SIZES_MB.get(model_size, "?")
                if progress_callback:
                    if attempt == 1:
                        progress_callback(0.10,
                            f"正在下载 Whisper {model_size} 模型（约 {approx} MB），请耐心等待…")
                    else:
                        progress_callback(0.10,
                            f"模型校验失败，重新下载 '{model_size}' "
                            f"（第 {attempt}/{_MAX_DOWNLOAD_ATTEMPTS} 次）…")

                try:
                    _download_model(model_size, progress_callback)
                except Exception as exc:
                    logger.error("Download attempt %d failed: %s", attempt, exc)
                    if attempt >= _MAX_DOWNLOAD_ATTEMPTS:
                        raise RuntimeError(
                            f"Whisper 模型 '{model_size}' 下载失败（已重试 {_MAX_DOWNLOAD_ATTEMPTS} 次）：\n{exc}"
                        ) from exc
                    continue

                # Verify freshly downloaded file before loading
                if not _verify_cached_model(model_size):
                    logger.error("Fresh download of '%s' still fails SHA256", model_size)
                    _purge_model_cache(model_size)
                    if attempt >= _MAX_DOWNLOAD_ATTEMPTS:
                        raise RuntimeError(
                            f"Whisper 模型 '{model_size}' 下载后 SHA256 校验仍然失败。\n"
                            "请检查网络或关闭代理后重试。"
                        )
                    continue
            else:
                cache_path = _cached_model_path(model_size)
                if progress_callback:
                    progress_callback(0.10, f"已找到本地 Whisper {model_size} 模型缓存：{cache_path}")

            # ── 2. Load in background thread with heartbeat ────────────────────
            approx_mb = _MODEL_SIZES_MB.get(model_size, 0)
            if progress_callback:
                progress_callback(0.17,
                    f"模型文件校验通过，正在加载 Whisper {model_size} 到内存…"
                    f"（{approx_mb} MB，可能需要数分钟，请勿关闭）")

            result_box: list = []
            error_box:  list = []

            def _do_load():
                try:
                    result_box.append(whisper.load_model(model_size, download_root=str(cache_dir)))
                except Exception as e:
                    error_box.append(e)

            thread = threading.Thread(target=_do_load, daemon=True)
            thread.start()

            elapsed = 0
            while thread.is_alive():
                time.sleep(2)
                elapsed += 2
                if progress_callback:
                    estimated_s = max(approx_mb / 50, 30)
                    pct = min(0.17 + (elapsed / estimated_s) * 0.025, 0.195)
                    progress_callback(pct,
                        f"正在加载 Whisper {model_size} 到内存… 已等待 {elapsed}s")
            thread.join()

            if error_box:
                exc = error_box[0]
                if "SHA256 checksum" in str(exc):
                    logger.error("whisper.load_model SHA256 error on attempt %d", attempt)
                    _purge_model_cache(model_size)
                    if attempt < _MAX_DOWNLOAD_ATTEMPTS:
                        continue
                    raise RuntimeError(
                        f"Whisper 模型 '{model_size}' 校验持续失败，请检查网络后重试。"
                    ) from exc
                raise exc  # unrelated error, propagate as-is

            if result_box:
                model = result_box[0]
                _MODEL_MEMORY_CACHE[cache_key] = model
                if progress_callback:
                    progress_callback(0.20, f"Whisper {model_size} 模型加载完成。")
                return model

    cache_path = _cached_model_path(model_size)
    raise RuntimeError(
        f"Whisper 模型 '{model_size}' 经过 {_MAX_DOWNLOAD_ATTEMPTS} 次尝试仍无法加载。\n\n"
        f"建议：\n"
        f"  1. 检查网络，暂时关闭代理/VPN 后重试\n"
        f"  2. 手动删除 {cache_path} 后重启应用\n"
        f"  3. 改用较小的模型（tiny / base / small）"
    )
