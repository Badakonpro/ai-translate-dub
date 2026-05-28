# Changelog

## 1.0.0 - 2026-05-28

Initial GitHub release.

- Added the Gradio video processing pipeline: audio extraction, Whisper transcription, translation, SRT generation, and subtitle muxing/burning.
- Added Ollama and DeepSeek translation backends.
- Added optional Ollama model pull controls.
- Added optional global translation context extraction before segment-by-segment translation.
- Added optional parallel translation with configurable worker count.
- Added persistent Whisper model caching and in-process model reuse.
- Added detailed transcription progress updates during Whisper inference.
- Added Electron macOS packaging and DMG creation scripts.
