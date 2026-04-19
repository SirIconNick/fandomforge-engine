"""Generate FandomForge Grab extension icons at 16/32/48/128 px.

Run from the repo root:
    tools/.venv/bin/python browser-extensions/chrome/icons/generate.py

Regenerates icon16.png, icon32.png, icon48.png, icon128.png next to this file.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FORGE = (255, 90, 31, 255)
INK = (11, 11, 14, 255)
WHITE = (255, 255, 255, 255)

OUT_DIR = Path(__file__).resolve().parent


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    # Try a few bold fonts present on macOS / most dev machines; fall back to default.
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFCompact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded-square background in forge orange
    radius = max(2, size // 6)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=FORGE)

    # Inner subtle bevel
    draw.rounded_rectangle(
        [(1, 1), (size - 2, size - 2)],
        radius=max(1, radius - 1),
        outline=(0, 0, 0, 40),
        width=1,
    )

    # "FF" letters, ink-colored, centered
    text = "FF"
    font_size = max(8, int(size * 0.56))
    font = _find_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1] - max(1, size // 32)
    draw.text((x, y), text, fill=INK, font=font)

    # Tiny white download arrow in the bottom-right corner (only readable on 48/128)
    if size >= 48:
        pad = size // 10
        arrow_w = size // 3
        ax = size - pad - arrow_w
        ay = size - pad - arrow_w
        head_h = arrow_w // 2
        shaft_w = max(2, arrow_w // 6)
        cx = ax + arrow_w // 2
        # Shaft
        draw.rectangle(
            [(cx - shaft_w // 2, ay), (cx + shaft_w // 2, ay + arrow_w - head_h)],
            fill=WHITE,
        )
        # Arrowhead
        draw.polygon(
            [
                (ax, ay + arrow_w - head_h),
                (ax + arrow_w, ay + arrow_w - head_h),
                (cx, ay + arrow_w),
            ],
            fill=WHITE,
        )

    return img


def main() -> None:
    for size in (16, 32, 48, 128):
        out = OUT_DIR / f"icon{size}.png"
        make_icon(size).save(out, "PNG")
        print(f"wrote {out.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
