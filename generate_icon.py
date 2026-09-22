"""
generate_icon.py
================
Generates the Manga Translator application icon (a PNG + a multi-size .ico)
purely with Pillow, so no external assets are required.

Design: a dark rounded-square background with a white speech bubble whose
tail points to the bottom-left; inside the bubble the Japanese hiragana
"あ" appears on the left and the Hebrew letter "א" on the right, separated
by a small arrow - visually communicating "Japanese text -> Hebrew text
inside a speech bubble", which is exactly what the app does.

Outputs:
    assets/icon.png   (256x256, used by Flet window icon)
    assets/icon.ico   (16/32/48/64/128/256, used by the Windows .exe)
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# arabic_reshaper + bidi are needed so the Hebrew "א" glyph is shaped
# correctly even inside the icon.
import arabic_reshaper
from bidi.algorithm import get_display

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

SIZE = 256


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        ASSETS / "fonts" / "VarelaRound-Regular.ttf",
        ASSETS / "fonts" / "DavidLibre-Regular.ttf",
        ASSETS / "fonts" / "Assistant-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _rounded_square(draw: ImageDraw.ImageDraw, size: int, radius: int,
                    color) -> None:
    draw.rounded_rectangle((0, 0, size - 1, size - 1),
                           radius=radius, fill=color)


def _speech_bubble(draw: ImageDraw.ImageDraw, box, color) -> None:
    """Draw a rounded speech bubble with a bottom-left tail."""
    x0, y0, x1, y1 = box
    body = [x0, y0, x1, y1]
    draw.rounded_rectangle(body, radius=28, fill=color)
    # Tail: a triangle pointing bottom-left from the bubble's lower-left.
    tail = [(x0 + 28, y1 - 2),
            (x0 + 28, y1 + 34),
            (x0 + 4, y1 - 2)]
    draw.polygon(tail, fill=color)


def _shape_hebrew(letter: str) -> str:
    return get_display(arabic_reshaper.reshape(letter))


def make_icon(size: int = SIZE) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark rounded-square background (manga-page black with a subtle blue tint).
    bg = (26, 27, 38, 255)
    _rounded_square(draw, size, radius=52, color=bg)

    # A thin light ring for a "polished app" feel.
    draw.rounded_rectangle((0, 0, size - 1, size - 1),
                           radius=52, outline=(255, 255, 255, 40), width=2)

    # White speech bubble, centered, leaving room for the tail.
    bw = int(size * 0.74)
    bh = int(size * 0.50)
    bx0 = (size - bw) // 2
    by0 = int(size * 0.16)
    _speech_bubble(draw, (bx0, by0, bx0 + bw, by0 + bh), (255, 255, 255, 255))

    # Characters inside the bubble: "あ" (left) -> "א" (right).
    font_size = int(bh * 0.62)
    font = _load_font(font_size)
    ja = "あ"
    he = _shape_hebrew("א")

    # Measure to centre each glyph vertically.
    ja_bbox = draw.textbbox((0, 0), ja, font=font)
    he_bbox = draw.textbbox((0, 0), he, font=font)
    ja_w = ja_bbox[2] - ja_bbox[0]
    ja_h = ja_bbox[3] - ja_bbox[1]
    he_w = he_bbox[2] - he_bbox[0]
    he_h = he_bbox[3] - he_bbox[1]

    bubble_cy = by0 + bh // 2
    # Place JA on the left third, HE on the right third, arrow between.
    ja_x = bx0 + int(bw * 0.12) - ja_bbox[0]
    ja_y = bubble_cy - ja_h // 2 - ja_bbox[1]
    he_x = bx0 + int(bw * 0.88) - he_w - he_bbox[0]
    he_y = bubble_cy - he_h // 2 - he_bbox[1]

    draw.text((ja_x, ja_y), ja, font=font, fill=(26, 27, 38, 255))
    draw.text((he_x, he_y), he, font=font, fill=(26, 27, 38, 255))

    # Small arrow between the two glyphs.
    arr_color = (90, 95, 120, 255)
    ay = bubble_cy
    ax0 = bx0 + int(bw * 0.40)
    ax1 = bx0 + int(bw * 0.60)
    draw.line((ax0, ay, ax1, ay), fill=arr_color, width=max(2, size // 80))
    # Arrowhead pointing right.
    head = max(4, size // 50)
    draw.polygon(
        [(ax1, ay), (ax1 - head, ay - head), (ax1 - head, ay + head)],
        fill=arr_color,
    )

    return img


def main() -> None:
    icon = make_icon(SIZE)
    png_path = ASSETS / "icon.png"
    ico_path = ASSETS / "icon.ico"
    icon.save(png_path, format="PNG")
    # Windows .ico with multiple sizes for crisp taskbar / explorer rendering.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icons = [icon.resize((s, s), Image.LANCZOS) for s in sizes]
    icons[-1].save(ico_path, format="ICO",
                   sizes=[(s, s) for s in sizes],
                   append_images=icons[:-1])
    print(f"Wrote {png_path} ({png_path.stat().st_size} bytes)")
    print(f"Wrote {ico_path} ({ico_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
