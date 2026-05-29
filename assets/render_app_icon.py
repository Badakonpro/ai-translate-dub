#!/usr/bin/env python3
"""Render the project app icon PNG and ICNS assets."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
PNG_PATH = ROOT / "app-icon.png"
ICNS_PATH = ROOT / "app-icon.icns"
ICONSET_PATH = ROOT / "app-icon.iconset"
SIZE = 1024
SCALE = 4


def srgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def xy(value: float) -> int:
    return round(value * SCALE)


def box(x1: float, y1: float, x2: float, y2: float) -> tuple[int, int, int, int]:
    return xy(x1), xy(y1), xy(x2), xy(y2)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def gradient_background(size: int) -> Image.Image:
    top = srgb("#101827")
    mid = srgb("#12343B")
    bottom = srgb("#0F766E")
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        t = y / (size - 1)
        if t < 0.56:
            local = t / 0.56
            color = tuple(lerp(top[i], mid[i], local) for i in range(3))
        else:
            local = (t - 0.56) / 0.44
            color = tuple(lerp(mid[i], bottom[i], local) for i in range(3))
        draw.line((0, y, size, y), fill=(*color, 255))
    return image


def rounded_rect(draw: ImageDraw.ImageDraw, coords, radius: float, fill, outline=None, width: float = 1) -> None:
    draw.rounded_rectangle(coords, radius=xy(radius), fill=fill, outline=outline, width=xy(width))


def draw_icon() -> Image.Image:
    size = SIZE * SCALE
    icon = gradient_background(size)
    icon.putalpha(rounded_mask(size, 226 * SCALE))
    draw = ImageDraw.Draw(icon, "RGBA")

    rounded_rect(
        draw,
        box(208, 212, 816, 712),
        92,
        None,
        outline=srgb("#F8F3E7") + (255,),
        width=56,
    )
    draw.polygon([(xy(380), xy(374)), (xy(380), xy(516)), (xy(498), xy(445))], fill=srgb("#FF8A65") + (255,))

    for coords in [
        (558, 334, 598, 556),
        (626, 382, 666, 508),
        (694, 314, 734, 576),
    ]:
        rounded_rect(draw, box(*coords), 20, srgb("#2DD4BF") + (255,))

    rounded_rect(draw, box(318, 606, 608, 650), 22, srgb("#F8F3E7") + (255,))
    rounded_rect(draw, box(640, 606, 732, 650), 22, srgb("#FF8A65") + (255,))
    rounded_rect(draw, box(356, 770, 566, 810), 20, srgb("#7DD3FC") + (255,))
    rounded_rect(draw, box(604, 770, 764, 810), 20, srgb("#2DD4BF") + (255,))

    return icon.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def write_iconset(source: Image.Image) -> None:
    if ICONSET_PATH.exists():
        shutil.rmtree(ICONSET_PATH)
    ICONSET_PATH.mkdir(parents=True)
    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, target_size in sizes.items():
        source.resize((target_size, target_size), Image.Resampling.LANCZOS).save(ICONSET_PATH / filename)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET_PATH), "-o", str(ICNS_PATH)], check=True)
    shutil.rmtree(ICONSET_PATH)


def main() -> None:
    icon = draw_icon()
    icon.save(PNG_PATH)
    write_iconset(icon)
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICNS_PATH}")


if __name__ == "__main__":
    main()
