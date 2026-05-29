import os
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import List

from .config import find_ffmpeg

# ASS alignment values for horizontal subtitle alignment (bottom row)
_HALIGN_ASS = {
    "center": 2,
    "left":   1,
    "right":  3,
}

# ISO 639-2/B language codes for common target languages
_LANG_TO_ISO639_2 = {
    "chinese": "chi",
    "english": "eng",
    "japanese": "jpn",
    "korean": "kor",
    "french": "fre",
    "german": "ger",
    "spanish": "spa",
    "russian": "rus",
    "portuguese": "por",
    "italian": "ita",
    "arabic": "ara",
    "hindi": "hin",
    "thai": "tha",
    "vietnamese": "vie",
    "turkish": "tur",
    "dutch": "dut",
    "polish": "pol",
    "indonesian": "ind",
}


def _lang_code(subtitle_title: str) -> str:
    """Return ISO 639-2 code for the given language name, defaulting to 'und'."""
    return _LANG_TO_ISO639_2.get(subtitle_title.lower().strip(), "und")


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    millis = round((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments: List[dict], output_path: str) -> str:
    """
    Generate an SRT subtitle file from translated segments.

    Args:
        segments: [{"start": float, "end": float, "text": str}, ...]
        output_path: Path to write the .srt file.

    Returns:
        Path to the generated SRT file.
    """
    lines = []
    for i, seg in enumerate(segments, 1):
        start_ts = _format_timestamp(seg["start"])
        end_ts = _format_timestamp(seg["end"])
        lines.append(str(i))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(seg["text"])
        lines.append("")  # blank line between entries

    srt_content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)

    return output_path


def mux_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str = None,
    subtitle_title: str = "Chinese",
    progress_callback=None,
) -> str:
    """
    Mux soft subtitles into a video using ffmpeg.

    Outputs an MKV file with the subtitle track embedded as a soft (switchable) track.

    Args:
        video_path: Path to the input video.
        srt_path: Path to the SRT subtitle file.
        output_path: Path for the output MKV. Defaults to <video>_subtitled.mkv.
        subtitle_title: Title/label for the subtitle track in the player.
        progress_callback: Optional callback(progress_pct, message).

    Returns:
        Path to the output video with embedded subtitles.
    """
    video_path = Path(video_path).resolve()
    srt_path = Path(srt_path).resolve()

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT file not found: {srt_path}")

    if output_path is None:
        output_dir = video_path.parent
        output_path = str(output_dir / f"{video_path.stem}_subtitled.mkv")
    else:
        output_path = str(Path(output_path).resolve())
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(0.5, "Muxing subtitles into video...")

    cmd = [
        find_ffmpeg(),
        "-i", str(video_path),
        "-i", str(srt_path),
        "-c", "copy",                     # copy video/audio streams without re-encoding
        "-c:s", "srt",                    # subtitle codec
        "-map", "0:v:0",                 # video from first input
        "-map", "0:a?",                  # audio from first input, if present
        "-map", "1:s:0",                 # subtitle from second input
        "-metadata:s:s:0", f"title={subtitle_title}",
        "-metadata:s:s:0", f"language={_lang_code(subtitle_title)}",
        "-y",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg subtitle muxing failed:\n{result.stderr}")

    if progress_callback:
        progress_callback(1.0, "Subtitles embedded successfully.")

    return output_path


def burn_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str = None,
    font_size: int = 24,
    h_align: str = "center",
    margin_v: int = 20,
    progress_callback=None,
) -> str:
    """
    Hard-burn (permanently encode) subtitles into the video picture.

    Uses ffmpeg's subtitles filter with libass. Outputs an MP4 file.
    The video stream is re-encoded with libx264 (CRF 18, preset fast);
    the audio stream is copied without re-encoding.

    Args:
        video_path:        Path to the input video.
        srt_path:          Path to the SRT subtitle file.
        output_path:       Path for the output MP4. Defaults to <video>_hardburned.mp4.
        font_size:         Subtitle font size in points (default 24).
        h_align:           Horizontal alignment: "center" / "left" / "right" (default "center").
        margin_v:          Vertical margin in pixels from the bottom edge (default 20).
        progress_callback: Optional callback(progress_pct, message).

    Returns:
        Path to the output video with burned-in subtitles.
    """
    video_path = Path(video_path).resolve()
    srt_path = Path(srt_path).resolve()

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT file not found: {srt_path}")

    if output_path is None:
        output_path = str(video_path.parent / f"{video_path.stem}_hardburned.mp4")
    else:
        output_path = str(Path(output_path).resolve())
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    align = _HALIGN_ASS.get(h_align, 2)

    # Copy SRT to a temp path without spaces/special chars so ffmpeg's
    # subtitles filter can parse the path reliably.
    tmp_fd, tmp_srt = tempfile.mkstemp(suffix=".srt")
    try:
        os.close(tmp_fd)
        shutil.copy2(str(srt_path), tmp_srt)

        # Escape colons for the ffmpeg filtergraph.
        safe_srt = tmp_srt.replace("\\", "/").replace(":", "\\:")
        force_style = f"FontSize={font_size},Alignment={align},MarginV={margin_v}"
        vf = f"subtitles='{safe_srt}':force_style='{force_style}'"

        if progress_callback:
            progress_callback(0.05, "开始硬烧录字幕（重新编码中）...")

        cmd = [
            find_ffmpeg(),
            "-i", str(video_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-c:a", "copy",
            "-y",
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg subtitle burning failed:\n{result.stderr}")

        if progress_callback:
            progress_callback(1.0, "字幕硬烧录完成。")

        return output_path
    finally:
        try:
            os.remove(tmp_srt)
        except OSError:
            pass
