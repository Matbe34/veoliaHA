"""Generate the integration's brand assets.

Single source of truth for `icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png`.
Re-run when the visual identity changes.

Style:
- Material-blue water droplet on transparent background.
- Vertical gradient (top: lighter, bottom: deeper) for depth.
- Soft highlight on the upper-left for a wet/glassy hint.
- Anti-aliased via 4x supersampling + LANCZOS downscale.
"""
from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent
SUPERSAMPLE = 4

# Material Blue palette
TOP_RGB = (66, 165, 245)        # Blue 400 — gradient top
BOTTOM_RGB = (13, 71, 161)      # Blue 900 — gradient bottom
HIGHLIGHT_RGB = (255, 255, 255) # White highlight


def _teardrop_polygon(size: int, n: int = 600) -> list[tuple[float, float]]:
    """Sample points along a clean teardrop outline.

    Top side: quadratic Bezier from the apex to the right edge of the bulb.
    Bottom: arc of the bulb circle.
    Left side: mirror of the top.
    """
    cx = size / 2
    apex_y = size * 0.10
    bulb_cy = size * 0.60
    bulb_r = size * 0.32

    points: list[tuple[float, float]] = []

    # Right side: Bezier from apex to (cx + bulb_r, bulb_cy)
    p0 = (cx, apex_y)
    p1 = (cx + bulb_r * 1.05, apex_y + (bulb_cy - apex_y) * 0.62)
    p2 = (cx + bulb_r, bulb_cy)
    n_side = n // 3
    for i in range(n_side + 1):
        t = i / n_side
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        points.append((x, y))

    # Bottom arc: from (cx + bulb_r, bulb_cy) down around to (cx - bulb_r, bulb_cy)
    n_arc = n // 3
    for i in range(1, n_arc + 1):
        # Sweep angle from 0 to π through the bottom.
        a = i / n_arc * math.pi
        x = cx + bulb_r * math.cos(a)
        # In PIL coords y grows downward — the "bottom" of the bulb is at +r.
        y = bulb_cy + bulb_r * math.sin(a)
        points.append((x, y))

    # Left side: mirror of the right Bezier, traversed bulb→apex.
    p0_l = (cx - bulb_r, bulb_cy)
    p1_l = (cx - bulb_r * 1.05, apex_y + (bulb_cy - apex_y) * 0.62)
    p2_l = (cx, apex_y)
    n_left = n - 2 * n_side - 1
    for i in range(1, n_left + 1):
        t = i / n_left
        x = (1 - t) ** 2 * p0_l[0] + 2 * (1 - t) * t * p1_l[0] + t ** 2 * p2_l[0]
        y = (1 - t) ** 2 * p0_l[1] + 2 * (1 - t) * t * p1_l[1] + t ** 2 * p2_l[1]
        points.append((x, y))

    return points


def _gradient_fill(size: int) -> Image.Image:
    """Vertical gradient TOP_RGB → BOTTOM_RGB across the full size."""
    grad = Image.new("RGBA", (1, size), (0, 0, 0, 0))
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(TOP_RGB[0] * (1 - t) + BOTTOM_RGB[0] * t)
        g = round(TOP_RGB[1] * (1 - t) + BOTTOM_RGB[1] * t)
        b = round(TOP_RGB[2] * (1 - t) + BOTTOM_RGB[2] * t)
        px[0, y] = (r, g, b, 255)
    return grad.resize((size, size))


def render_icon(target_size: int) -> Image.Image:
    """Render the droplet at SUPERSAMPLE× resolution, then downscale."""
    size = target_size * SUPERSAMPLE

    # 1. Alpha mask from the teardrop polygon.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(_teardrop_polygon(size), fill=255)

    # 2. Gradient-filled droplet.
    droplet = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    droplet.paste(_gradient_fill(size), (0, 0), mask)

    # 3. Soft inner highlight on the upper-left of the bulb.
    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    h_draw = ImageDraw.Draw(highlight)
    cx = size * 0.40
    cy = size * 0.50
    rx = size * 0.12
    ry = size * 0.07
    h_draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(*HIGHLIGHT_RGB, 130))
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius=size * 0.015))
    # Mask the highlight by the droplet so it stays inside the shape.
    droplet.alpha_composite(Image.composite(highlight, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask))

    # 4. Downscale with LANCZOS for clean anti-aliased edges.
    return droplet.resize((target_size, target_size), Image.LANCZOS)


def render_logo(width: int, height: int, icon_for_logo: Image.Image) -> Image.Image:
    """Wide logo: droplet on the left + 'VEOLIA WATER' label."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Icon is square; size it to the logo height with a bit of padding.
    icon_size = int(height * 0.92)
    icon_resized = icon_for_logo.resize((icon_size, icon_size), Image.LANCZOS)
    icon_y = (height - icon_size) // 2
    img.paste(icon_resized, (icon_y, icon_y), icon_resized)

    # Brand label to the right.
    label_x = icon_y + icon_size + int(height * 0.18)
    draw = ImageDraw.Draw(img)

    # Try a few common font paths; fall back to PIL default.
    font = None
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if Path(path).exists():
            font = ImageFont.truetype(path, size=int(height * 0.45))
            break
    if font is None:
        font = ImageFont.load_default()

    text = "VEOLIA WATER"
    # Vertically centre the text against the icon.
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    draw.text(
        (label_x, (height - text_h) // 2 - bbox[1]),
        text, fill=BOTTOM_RGB, font=font,
    )
    return img


def main() -> None:
    icon_256 = render_icon(256)
    icon_512 = render_icon(512)
    icon_256.save(OUT / "icon.png", "PNG")
    icon_512.save(OUT / "icon@2x.png", "PNG")

    logo_256 = render_logo(256, 64, icon_256)
    logo_512 = render_logo(512, 128, icon_512)
    logo_256.save(OUT / "logo.png", "PNG")
    logo_512.save(OUT / "logo@2x.png", "PNG")

    for f in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png"):
        path = OUT / f
        print(f"  wrote {path}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
