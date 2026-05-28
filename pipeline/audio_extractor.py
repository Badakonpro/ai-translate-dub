import subprocess
import os
from pathlib import Path

from .config import find_ffmpeg


def extract_audio(video_path: str, output_dir: str = None) -> str:
    """
    Extract audio from video file and convert to 16kHz mono WAV for Whisper.

    Args:
        video_path: Path to the input video file.
        output_dir: Directory for the output audio file. Defaults to same dir as video.

    Returns:
        Path to the extracted audio file (WAV, 16kHz, mono).
    """
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if output_dir:
        out_dir = Path(output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = video_path.parent

    audio_path = out_dir / f"{video_path.stem}_audio.wav"

    cmd = [
        find_ffmpeg(),
        "-i", str(video_path),
        "-vn",                          # no video
        "-acodec", "pcm_s16le",         # 16-bit PCM
        "-ar", "16000",                 # 16kHz sample rate
        "-ac", "1",                     # mono
        "-y",                           # overwrite
        str(audio_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed:\n{result.stderr}")

    return str(audio_path)
