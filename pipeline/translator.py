import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List
from urllib.parse import urlparse

from pipeline.config import env_or_config, load_config

logger = logging.getLogger(__name__)

MAX_PARALLEL_WORKERS = 16
MAX_CONTEXT_SEGMENTS = 60
MAX_CONTEXT_INPUT_CHARS = 7000
MAX_CONTEXT_OUTPUT_CHARS = 2500


DEEPSEEK_KEY_PLACEHOLDERS = {
    "your-deepseek-api-key",
    "sk-your-deepseek-api-key",
}


def _validate_http_url(url: str, label: str) -> str:
    """Raise ValueError if *url* is not a valid http/https URL (SSRF prevention)."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        raise ValueError(f"{label} 不是有效的 URL：{url!r}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{label} 必须使用 http 或 https 协议，当前值：{url!r}")
    if not parsed.netloc:
        raise ValueError(f"{label} 缺少主机名：{url!r}")
    return url.strip()


def _redact(text: str, secret: str) -> str:
    """Replace *secret* with a masked version in *text* to prevent key leakage."""
    if secret and len(secret) > 8:
        return text.replace(secret, secret[:4] + "****")
    return text


def _load_config(config_path: str = None) -> dict:
    return load_config(config_path)


def _normalize_text(text: str, max_chars: int = None) -> str:
    cleaned = " ".join(str(text or "").split())
    if max_chars and len(cleaned) > max_chars:
        return cleaned[:max_chars].rstrip() + "..."
    return cleaned


def _sample_segments_for_context(segments, max_segments: int = MAX_CONTEXT_SEGMENTS):
    total = len(segments)
    if total <= max_segments:
        return list(enumerate(segments))
    if max_segments <= 1:
        return [(0, segments[0])]

    step = (total - 1) / (max_segments - 1)
    indices = sorted({round(i * step) for i in range(max_segments)})
    return [(index, segments[index]) for index in indices]


def _format_segments_for_context(segments) -> str:
    lines = []
    total_chars = 0
    for index, seg in _sample_segments_for_context(segments):
        text = _normalize_text(seg.get("text", ""))
        if not text:
            continue
        line = f"{index + 1}. {text}"
        if total_chars + len(line) > MAX_CONTEXT_INPUT_CHARS:
            lines.append("...")
            break
        lines.append(line)
        total_chars += len(line)
    return "\n".join(lines)


def _normalize_translation_context(text: str) -> str:
    context = "\n".join(line.strip() for line in str(text or "").strip().splitlines() if line.strip())
    if len(context) > MAX_CONTEXT_OUTPUT_CHARS:
        context = context[:MAX_CONTEXT_OUTPUT_CHARS].rstrip() + "..."
    return context


def _build_translation_context_prompt(segments, source_lang: str, target_lang: str, video_title: str = "") -> str:
    title = _normalize_text(video_title, max_chars=300) or "未提供"
    transcript_sample = _format_segments_for_context(segments) or "未检测到可用字幕样本。"
    return "\n".join([
        f"Video title or topic: {title}",
        f"Source language: {source_lang}",
        f"Target language: {target_lang}",
        "",
        "Transcript sample:",
        transcript_sample,
        "",
        "Create a compact global context for translating the full subtitle file.",
        "Include only information supported by the title/sample:",
        "- topic and likely content domain",
        "- tone/register to preserve",
        "- recurring names, places, technical terms, and recommended target-language translations",
        "- subtitle style constraints such as concise wording or spoken-language naturalness",
        "Do not translate the sample line by line. Output only the context notes.",
    ])


def _resolve_worker_count(parallel_enabled: bool, max_workers, segment_count: int) -> int:
    if not parallel_enabled or segment_count <= 1:
        return 1
    try:
        workers = int(max_workers)
    except (TypeError, ValueError):
        workers = 1
    return max(1, min(workers, segment_count, MAX_PARALLEL_WORKERS))


def _translate_segments_in_parallel(
    segments,
    worker_count: int,
    translate_one,
    progress_callback=None,
    progress_start: float = 0.0,
    progress_span: float = 1.0,
):
    total = len(segments)
    translated = [None] * total
    completed = 0

    if progress_callback:
        progress_callback(progress_start, f"正在并行翻译：0/{total}（{worker_count} 路）")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_index = {
            executor.submit(translate_one, i, seg): i
            for i, seg in enumerate(segments)
        }
        try:
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                translated[index] = future.result()
                completed += 1
                if progress_callback:
                    pct = progress_start + (completed / total) * progress_span
                    progress_callback(pct, f"正在并行翻译：已完成 {completed}/{total}（{worker_count} 路）")
        except Exception:
            for pending in future_to_index:
                pending.cancel()
            raise

    return translated


class BaseTranslator(ABC):
    def build_translation_context(
        self,
        segments: List[dict],
        source_lang: str,
        target_lang: str,
        video_title: str = "",
        progress_callback=None,
    ) -> str:
        raise NotImplementedError("This translator does not support translation context extraction.")

    @abstractmethod
    def translate(
        self,
        segments: List[dict],
        source_lang: str,
        target_lang: str,
        progress_callback=None,
        parallel_enabled: bool = False,
        max_workers: int = 1,
        translation_context: str = "",
    ) -> List[dict]:
        """
        Translate a list of segments. Returns segments with translated text.

        Each segment: {"start": float, "end": float, "text": str}
        Returns: [{"start": float, "end": float, "text": str (translated)}, ...]
        """
        ...


class DeepSeekTranslator(BaseTranslator):
    def __init__(self, api_key: str = None, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com"):
        config = _load_config()
        deepseek_config = config.get("deepseek", {})
        api_key = api_key or env_or_config("DEEPSEEK_API_KEY", deepseek_config.get("api_key", ""))
        model = model or deepseek_config.get("model", "deepseek-chat")
        base_url = base_url or deepseek_config.get("base_url", "https://api.deepseek.com")
        if not api_key or api_key.strip() in DEEPSEEK_KEY_PLACEHOLDERS or api_key.startswith("sk-your-"):
            raise ValueError("DeepSeek API Key 未配置。请在界面输入 API Key，或在 config.yaml / DEEPSEEK_API_KEY 中配置。")

        _validate_http_url(base_url, "DeepSeek base_url")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def list_models(self) -> list[str]:
        """Fetch available model IDs from the DeepSeek /models endpoint.

        Returns a sorted list of model ID strings. Falls back to a default
        list if the request fails so the UI is never left empty.
        """
        _FALLBACK = ["deepseek-chat", "deepseek-reasoner"]
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            models = client.models.list()
            ids = sorted(m.id for m in models.data if m.id)
            return ids if ids else _FALLBACK
        except Exception as exc:
            logger.warning("DeepSeek list_models failed: %s", exc)
            raise RuntimeError(_redact(str(exc), self.api_key)) from None

    def build_translation_context(
        self,
        segments,
        source_lang="auto",
        target_lang="Chinese",
        video_title="",
        progress_callback=None,
    ):
        from openai import OpenAI

        if progress_callback:
            progress_callback(0.0, "正在生成全局翻译上下文...")

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        prompt = _build_translation_context_prompt(segments, source_lang, target_lang, video_title)
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": f"You prepare compact translation context for subtitle translation into {target_lang}. Output only reusable context notes, not a translation."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=700,
            )
        except Exception as exc:
            raise RuntimeError(_redact(str(exc), self.api_key)) from None

        context = _normalize_translation_context(response.choices[0].message.content)
        if progress_callback:
            progress_callback(1.0, "全局翻译上下文已生成。")
        return context

    def translate(
        self,
        segments,
        source_lang="auto",
        target_lang="Chinese",
        progress_callback=None,
        parallel_enabled: bool = False,
        max_workers: int = 1,
        translation_context: str = "",
    ):
        from openai import OpenAI

        worker_count = _resolve_worker_count(parallel_enabled, max_workers, len(segments))

        def translate_one(i, seg):
            # Use a fresh client per worker call so concurrent requests do not
            # share mutable client state across threads.
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)

            context_before = segments[i - 1]["text"] if i > 0 else ""
            context_after = segments[i + 1]["text"] if i < len(segments) - 1 else ""

            prompt = _build_translation_prompt(
                seg["text"],
                context_before,
                context_after,
                source_lang,
                target_lang,
                translation_context=translation_context,
            )

            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": f"You are a professional subtitle translator. Translate the given text to {target_lang}. Use the provided global context consistently. Output ONLY the translated text, nothing else. Keep it concise and natural for subtitles."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=512,
                )
            except Exception as exc:
                # Redact API key from exception message before surfacing to UI
                raise RuntimeError(_redact(str(exc), self.api_key)) from None

            translated_text = response.choices[0].message.content.strip()
            return {"start": seg["start"], "end": seg["end"], "text": translated_text}

        if worker_count > 1:
            return _translate_segments_in_parallel(
                segments,
                worker_count,
                translate_one,
                progress_callback=progress_callback,
            )

        translated = []
        for i, seg in enumerate(segments):
            if progress_callback:
                progress_callback(i / len(segments), f"Translating segment {i + 1}/{len(segments)}...")
            translated.append(translate_one(i, seg))

        return translated


class OpenAITranslator(BaseTranslator):
    """OpenAI-compatible translator (also works with any OpenAI-compatible endpoint)."""

    _FALLBACK_MODELS = ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini", "gpt-4-turbo"]

    def __init__(self, api_key: str = None, model: str = "gpt-4o", base_url: str = "https://api.openai.com/v1"):
        config = _load_config()
        openai_config = config.get("openai", {})
        api_key = api_key or env_or_config("OPENAI_API_KEY", openai_config.get("api_key", ""))
        model = model or openai_config.get("model", "gpt-4o")
        base_url = base_url or openai_config.get("base_url", "https://api.openai.com/v1")
        if not api_key or api_key.strip() in {"your-openai-api-key", "sk-your-openai-api-key"} or api_key.startswith("sk-your-"):
            raise ValueError("OpenAI API Key 未配置。请在界面输入 API Key，或在 config.yaml / OPENAI_API_KEY 中配置。")
        _validate_http_url(base_url, "OpenAI base_url")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def list_models(self) -> list:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            models = client.models.list()
            ids = sorted(m.id for m in models.data if m.id)
            return ids if ids else self._FALLBACK_MODELS
        except Exception as exc:
            logger.warning("OpenAI list_models failed: %s", exc)
            raise RuntimeError(_redact(str(exc), self.api_key)) from None

    def build_translation_context(self, segments, source_lang="auto", target_lang="Chinese",
                                   video_title="", progress_callback=None):
        from openai import OpenAI
        if progress_callback:
            progress_callback(0.0, "正在生成全局翻译上下文...")
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        prompt = _build_translation_context_prompt(segments, source_lang, target_lang, video_title)
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": f"You prepare compact translation context for subtitle translation into {target_lang}. Output only reusable context notes, not a translation."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=700,
            )
        except Exception as exc:
            raise RuntimeError(_redact(str(exc), self.api_key)) from None
        context = _normalize_translation_context(response.choices[0].message.content)
        if progress_callback:
            progress_callback(1.0, "全局翻译上下文已生成。")
        return context

    def translate(self, segments, source_lang="auto", target_lang="Chinese",
                  progress_callback=None, parallel_enabled=False, max_workers=1, translation_context=""):
        from openai import OpenAI
        worker_count = _resolve_worker_count(parallel_enabled, max_workers, len(segments))

        def translate_one(i, seg):
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            context_before = segments[i - 1]["text"] if i > 0 else ""
            context_after = segments[i + 1]["text"] if i < len(segments) - 1 else ""
            prompt = _build_translation_prompt(
                seg["text"], context_before, context_after, source_lang, target_lang,
                translation_context=translation_context,
            )
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": f"You are a professional subtitle translator. Translate the given text to {target_lang}. Use the provided global context consistently. Output ONLY the translated text, nothing else. Keep it concise and natural for subtitles."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=512,
                )
            except Exception as exc:
                raise RuntimeError(_redact(str(exc), self.api_key)) from None
            return {"start": seg["start"], "end": seg["end"], "text": response.choices[0].message.content.strip()}

        if worker_count > 1:
            return _translate_segments_in_parallel(segments, worker_count, translate_one,
                                                    progress_callback=progress_callback)
        translated = []
        for i, seg in enumerate(segments):
            if progress_callback:
                progress_callback(i / len(segments), f"Translating segment {i + 1}/{len(segments)}...")
            translated.append(translate_one(i, seg))
        return translated


class AnthropicTranslator(BaseTranslator):
    """Anthropic Claude translator."""

    FALLBACK_MODELS = ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-3-5"]

    def __init__(self, api_key: str = None, model: str = "claude-sonnet-4-5"):
        config = _load_config()
        anthropic_config = config.get("anthropic", {})
        api_key = api_key or env_or_config("ANTHROPIC_API_KEY", anthropic_config.get("api_key", ""))
        model = model or anthropic_config.get("model", "claude-sonnet-4-5")
        if not api_key or api_key.strip() in {"your-anthropic-api-key", "sk-ant-your-api-key"}:
            raise ValueError("Anthropic API Key 未配置。请在界面输入 API Key，或在 config.yaml / ANTHROPIC_API_KEY 中配置。")
        self.api_key = api_key
        self.model = model

    def build_translation_context(self, segments, source_lang="auto", target_lang="Chinese",
                                   video_title="", progress_callback=None):
        import anthropic
        if progress_callback:
            progress_callback(0.0, "正在生成全局翻译上下文...")
        client = anthropic.Anthropic(api_key=self.api_key)
        prompt = _build_translation_context_prompt(segments, source_lang, target_lang, video_title)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=700,
                system=f"You prepare compact translation context for subtitle translation into {target_lang}. Output only reusable context notes, not a translation.",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
        except Exception as exc:
            raise RuntimeError(_redact(str(exc), self.api_key)) from None
        context = _normalize_translation_context(response.content[0].text)
        if progress_callback:
            progress_callback(1.0, "全局翻译上下文已生成。")
        return context

    def translate(self, segments, source_lang="auto", target_lang="Chinese",
                  progress_callback=None, parallel_enabled=False, max_workers=1, translation_context=""):
        import anthropic
        worker_count = _resolve_worker_count(parallel_enabled, max_workers, len(segments))

        def translate_one(i, seg):
            client = anthropic.Anthropic(api_key=self.api_key)
            context_before = segments[i - 1]["text"] if i > 0 else ""
            context_after = segments[i + 1]["text"] if i < len(segments) - 1 else ""
            prompt = _build_translation_prompt(
                seg["text"], context_before, context_after, source_lang, target_lang,
                translation_context=translation_context,
            )
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=512,
                    system=f"You are a professional subtitle translator. Translate the given text to {target_lang}. Use the provided global context consistently. Output ONLY the translated text, nothing else. Keep it concise and natural for subtitles.",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
            except Exception as exc:
                raise RuntimeError(_redact(str(exc), self.api_key)) from None
            return {"start": seg["start"], "end": seg["end"], "text": response.content[0].text.strip()}

        if worker_count > 1:
            return _translate_segments_in_parallel(segments, worker_count, translate_one,
                                                    progress_callback=progress_callback)
        translated = []
        for i, seg in enumerate(segments):
            if progress_callback:
                progress_callback(i / len(segments), f"Translating segment {i + 1}/{len(segments)}...")
            translated.append(translate_one(i, seg))
        return translated


class OllamaTranslator(BaseTranslator):
    def __init__(self, host: str = None, model: str = None, auto_pull_model: bool = False):
        config = _load_config()
        ollama_config = config.get("ollama", {})
        host = host or env_or_config("OLLAMA_HOST", ollama_config.get("host", "http://localhost:11434"))
        model = model or env_or_config("OLLAMA_MODEL", ollama_config.get("model", "qwen3:latest"))
        _validate_http_url(host, "Ollama Host")
        self.host = host.rstrip("/")
        self.model = model
        self.auto_pull_model = auto_pull_model

    def list_model_names(self):
        import requests

        try:
            tags_resp = requests.get(f"{self.host}/api/tags", timeout=15)
            tags_resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"无法连接 Ollama：{exc}。请确认 Ollama 已启动。") from exc

        models = tags_resp.json().get("models", [])
        return {item.get("name", "") for item in models}

    def has_model(self) -> bool:
        model_names = self.list_model_names()
        if self.model in model_names:
            return True
        if ":" not in self.model:
            return any(name.split(":", 1)[0] == self.model for name in model_names)
        return False

    def ensure_model_available(self, progress_callback=None) -> None:
        if progress_callback:
            progress_callback(0.0, f"正在检查 Ollama 模型 {self.model}...")

        if self.has_model():
            if progress_callback:
                progress_callback(0.05, f"Ollama 模型 {self.model} 已就绪。")
            return

        if not self.auto_pull_model:
            raise RuntimeError(f"Ollama 模型 {self.model} 未安装。请先在 Ollama 设置中点击“拉取模型”，或勾选“缺失时自动拉取”。")

        self.pull_model(progress_callback=progress_callback, start_pct=0.02, end_pct=0.15)

    def build_translation_context(
        self,
        segments,
        source_lang="auto",
        target_lang="Chinese",
        video_title="",
        progress_callback=None,
    ):
        import requests

        def model_progress(pct, msg):
            if progress_callback:
                progress_callback(pct * 0.2, msg)

        self.ensure_model_available(model_progress)
        if progress_callback:
            progress_callback(0.2, "正在生成全局翻译上下文...")

        prompt = _build_translation_context_prompt(segments, source_lang, target_lang, video_title)
        try:
            resp = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "system": f"You prepare compact translation context for subtitle translation into {target_lang}. Output only reusable context notes, not a translation.",
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
                timeout=180,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"无法连接 Ollama：{exc}。请确认 Ollama 已启动，并已拉取模型 {self.model}。") from exc

        context = _normalize_translation_context(resp.json().get("response", ""))
        if not context:
            raise RuntimeError(f"Ollama 未返回翻译上下文：{resp.text[:300]}")
        if progress_callback:
            progress_callback(1.0, "全局翻译上下文已生成。")
        return context

    def pull_model(self, progress_callback=None, start_pct: float = 0.0, end_pct: float = 1.0) -> None:
        import json
        import requests

        if self.has_model():
            if progress_callback:
                progress_callback(end_pct, f"Ollama 模型 {self.model} 已存在。")
            return

        if progress_callback:
            progress_callback(start_pct, f"正在拉取 Ollama 模型 {self.model}...")

        last_pull_pct = start_pct
        try:
            with requests.post(
                f"{self.host}/api/pull",
                json={"name": self.model, "stream": True},
                stream=True,
                timeout=(15, None),
            ) as pull_resp:
                pull_resp.raise_for_status()
                for line in pull_resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    status = event.get("status", "正在拉取模型")
                    completed = event.get("completed")
                    total = event.get("total")
                    if progress_callback and completed and total:
                        pct = max(last_pull_pct, min(end_pct, start_pct + (completed / total) * (end_pct - start_pct)))
                        last_pull_pct = pct
                        progress_callback(pct, f"{status}：{completed / total:.0%}")
                    elif progress_callback:
                        progress_callback(last_pull_pct, status)
        except requests.RequestException as exc:
            raise RuntimeError(f"拉取 Ollama 模型 {self.model} 失败：{exc}") from exc

        if progress_callback:
            progress_callback(end_pct, f"Ollama 模型 {self.model} 拉取完成。")

    def translate(
        self,
        segments,
        source_lang="auto",
        target_lang="Chinese",
        progress_callback=None,
        parallel_enabled: bool = False,
        max_workers: int = 1,
        translation_context: str = "",
    ):
        import requests

        self.ensure_model_available(progress_callback)
        worker_count = _resolve_worker_count(parallel_enabled, max_workers, len(segments))

        def translate_one(i, seg):
            context_before = segments[i - 1]["text"] if i > 0 else ""
            context_after = segments[i + 1]["text"] if i < len(segments) - 1 else ""

            prompt = _build_translation_prompt(
                seg["text"],
                context_before,
                context_after,
                source_lang,
                target_lang,
                translation_context=translation_context,
            )

            try:
                resp = requests.post(
                    f"{self.host}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "system": f"You are a professional subtitle translator. Translate to {target_lang}. Use the provided global context consistently. Output ONLY the translated text, nothing else. Keep it concise and natural for subtitles.",
                        "stream": False,
                        "options": {"temperature": 0.3},
                    },
                    timeout=120,
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                raise RuntimeError(f"无法连接 Ollama：{exc}。请确认 Ollama 已启动，并已拉取模型 {self.model}。") from exc
            translated_text = resp.json().get("response", "").strip()
            if not translated_text:
                raise RuntimeError(f"Ollama 未返回翻译文本：{resp.text[:300]}")
            return {"start": seg["start"], "end": seg["end"], "text": translated_text}

        if worker_count > 1:
            return _translate_segments_in_parallel(
                segments,
                worker_count,
                translate_one,
                progress_callback=progress_callback,
                progress_start=0.15,
                progress_span=0.85,
            )

        translated = []
        for i, seg in enumerate(segments):
            if progress_callback:
                progress_callback(0.15 + (i / len(segments)) * 0.85, f"正在翻译第 {i + 1}/{len(segments)} 段...")
            translated.append(translate_one(i, seg))

        return translated


def _build_translation_prompt(
    text: str,
    context_before: str,
    context_after: str,
    source_lang: str,
    target_lang: str,
    translation_context: str = "",
) -> str:
    """Build a translation prompt with optional context for coherence."""
    parts = []
    if translation_context:
        parts.extend([
            "Global translation context. Apply it consistently, but do not translate this section:",
            translation_context.strip(),
            "",
        ])
    parts.append(f"Translate this subtitle text to {target_lang}:")
    if context_before:
        parts.append(f"Previous line (for context): \"{context_before}\"")
    parts.append(f"Text to translate: \"{text}\"")
    if context_after:
        parts.append(f"Next line (for context): \"{context_after}\"")
    return "\n".join(parts)


def create_translator(backend: str) -> BaseTranslator:
    """Factory to create the appropriate translator based on backend name."""
    config = _load_config()
    if backend == "deepseek":
        deepseek_config = config.get("deepseek", {})
        return DeepSeekTranslator(
            api_key=deepseek_config.get("api_key", ""),
            model=deepseek_config.get("model", "deepseek-chat"),
            base_url=deepseek_config.get("base_url", "https://api.deepseek.com"),
        )
    elif backend == "openai":
        openai_config = config.get("openai", {})
        return OpenAITranslator(
            api_key=openai_config.get("api_key", ""),
            model=openai_config.get("model", "gpt-4o"),
            base_url=openai_config.get("base_url", "https://api.openai.com/v1"),
        )
    elif backend == "anthropic":
        anthropic_config = config.get("anthropic", {})
        return AnthropicTranslator(
            api_key=anthropic_config.get("api_key", ""),
            model=anthropic_config.get("model", "claude-sonnet-4-5"),
        )
    elif backend == "ollama":
        ollama_config = config.get("ollama", {})
        return OllamaTranslator(
            host=ollama_config.get("host", "http://localhost:11434"),
            model=ollama_config.get("model", "qwen3:latest"),
        )
    else:
        raise ValueError(f"Unknown translation backend: {backend}. Use 'deepseek', 'openai', 'anthropic', or 'ollama'.")
