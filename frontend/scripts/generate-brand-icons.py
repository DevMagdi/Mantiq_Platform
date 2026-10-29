#!/usr/bin/env python3
"""Generate the Mantiq icon set + og:image from one geometry (ADR-034 §3).

The mark is an abstract «M» whose vertices are network nodes, filled with the official gold diagonal
gradient (Gold Light → Mantiq Gold → Gold Dark). The same numbers are used by
`src/components/BrandMark.tsx` (`MARK_PATH` / `MARK_NODES`); `tests/brand.test.ts` fails if the two
drift apart, so this script is the only place the PNG set comes from.

    cd frontend && python scripts/generate-brand-icons.py

Requirements: Pillow (`pip install pillow`). No other dependency, no network, deterministic output —
the script runs offline in CI and rewrites byte-identical files for the same Pillow version.

Writes:
    public/favicon.ico            16/32/48 px, navy field + gold mark
    public/favicon-32.png         32 px
    public/apple-touch-icon.png   180 px, full-bleed navy (iOS applies its own mask)
    public/icon-192.png           192 px, navy, rounded
    public/icon-512.png           512 px, navy, rounded
    public/icon-512-maskable.png  512 px, navy, ~20% safe-area padding
    public/og-image.png           1200×630 navy field, gold mark, «Mantiq»
    public/brand/mantiq-mark.svg            transparent, gold gradient
    public/brand/mantiq-mark-on-navy.svg    navy rounded tile
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
# the issued-policy PDF (backend/app/services/documents.py) embeds this PNG: reportlab cannot read SVG,
# and a rasterised mark keeps the letterhead and the app pixel-identical.
PDF_MARK = ROOT.parent / "backend" / "app" / "assets" / "brand" / "mantiq-mark.png"
BRAND = PUBLIC / "brand"
FONT_BOLD = ROOT.parent / "backend" / "app" / "assets" / "fonts" / "DejaVuSans-Bold.ttf"

# ── the geometry (kept in sync with BrandMark.tsx by tests/brand.test.ts) ──────────────────────────
PATH_START = (14.0, 49.0)
PATH_POINTS = [(14.0, 18.5), (32.0, 39.0), (50.0, 18.5), (50.0, 49.0)]
STROKE = 5.5
NODES = [(14.0, 18.5, 6.0), (32.0, 39.0, 5.8), (50.0, 18.5, 6.0), (14.0, 49.0, 4.8), (50.0, 49.0, 4.8)]
SVG_PATH = "M14 49V18.5l18 20.5 18-20.5V49"

NAVY = (14, 23, 40)
NAVY_MID = (36, 53, 83)
GOLD = (189, 167, 126)
GOLD_LIGHT = (227, 210, 168)
GOLD_DARK = (143, 122, 82)
ON_DARK = (232, 226, 212)
ON_DARK_MUTED = (232, 226, 212)
STOPS = [(0.0, GOLD_LIGHT), (0.52, GOLD), (1.0, GOLD_DARK)]

SUPERSAMPLE = 4


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _gradient_at(t: float) -> tuple[int, int, int]:
    for (p0, c0), (p1, c1) in zip(STOPS, STOPS[1:]):
        if p0 <= t <= p1:
            return _mix(c0, c1, (t - p0) / (p1 - p0))
    return STOPS[-1][1]


def gradient_tile(size: int, bbox: tuple[float, float, float, float] | None = None) -> Image.Image:
    """Diagonal (top-left → bottom-right) three-stop gradient, built small and resized to stay smooth.

    `bbox` (in output pixels) normalises the ramp to the *mark*, not to the canvas — otherwise a mark
    that covers the middle of a large tile only ever samples the middle of the gradient and reads flat.
    """
    small = 128
    img = Image.new("RGB", (small, small))
    px = img.load()
    x0, y0, x1, y1 = bbox or (0.0, 0.0, float(size), float(size))
    for y in range(small):
        for x in range(small):
            sx = x / (small - 1) * size
            sy = y / (small - 1) * size
            t = ((sx - x0) / max(1.0, x1 - x0) + (sy - y0) / max(1.0, y1 - y0)) / 2
            px[x, y] = _gradient_at(min(1.0, max(0.0, t)))
    return img.resize((size, size), Image.Resampling.BICUBIC)


def mark_bbox(size: int, scale: float) -> tuple[float, float, float, float]:
    """Pixel bounds of the mark's ink, so the gradient can be normalised to it."""
    unit = size / 64.0 * scale
    pad = (size - 64 * unit) / 2
    xs = [p[0] for p in PATH_POINTS] + [c[0] for c in NODES]
    ys = [p[1] for p in PATH_POINTS] + [c[1] for c in NODES]
    rmax = max(n[2] for n in NODES) + STROKE / 2
    return (pad + (min(xs) - rmax) * unit, pad + (min(ys) - rmax) * unit,
            pad + (max(xs) + rmax) * unit, pad + (max(ys) + rmax) * unit)


def mark_mask(size: int, scale: float, offset: tuple[float, float] = (0.0, 0.0), nodes: bool = True) -> Image.Image:
    """An L-mode mask of the mark, drawn at SUPERSAMPLE× and downscaled (anti-aliased edges)."""
    big = size * SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    unit = big / 64.0 * scale
    ox = offset[0] * big
    oy = offset[1] * big

    def pt(p: tuple[float, float]) -> tuple[float, float]:
        return (ox + p[0] * unit + (big - 64 * unit) / 2, oy + p[1] * unit + (big - 64 * unit) / 2)

    width = max(1, round(STROKE * unit))
    points = [pt(PATH_START)] + [pt(p) for p in PATH_POINTS]
    for start, end in zip(points, points[1:]):
        draw.line([start, end], fill=255, width=width, joint="curve")
    radius = width / 2
    for x, y in points:  # round caps and joins
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=255)
    if nodes:
        for cx, cy, r in NODES:
            x, y = pt((cx, cy))
            rr = r * unit
            draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=255)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def rounded_mask(size: int, radius_ratio: float) -> Image.Image:
    mask = Image.new("L", (size * SUPERSAMPLE, size * SUPERSAMPLE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, mask.width - 1, mask.height - 1], radius=int(mask.width * radius_ratio), fill=255
    )
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def navy_tile(size: int, radius_ratio: float, mark_scale: float, nodes: bool = True) -> Image.Image:
    tile = Image.new("RGBA", (size, size), (*NAVY, 0))
    tile.paste(Image.new("RGBA", (size, size), (*NAVY, 255)), (0, 0), rounded_mask(size, radius_ratio))
    gold = gradient_tile(size, mark_bbox(size, mark_scale)).convert("RGBA")
    tile.paste(gold, (0, 0), mark_mask(size, mark_scale, nodes=nodes))
    return tile


def transparent_mark(size: int, nodes: bool = True) -> Image.Image:
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(gradient_tile(size, mark_bbox(size, 1.0)).convert("RGBA"), (0, 0), mark_mask(size, 1.0, nodes=nodes))
    return out


def write_svgs() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    nodes_svg = "\n".join(
        f'  <circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="url(#mantiq-gold)" />' for cx, cy, r in NODES
    )
    gradient = (
        '<linearGradient id="mantiq-gold" x1="10" y1="10" x2="54" y2="54" gradientUnits="userSpaceOnUse">\n'
        '    <stop offset="0" stop-color="#E3D2A8" />\n'
        '    <stop offset="0.52" stop-color="#BDA77E" />\n'
        '    <stop offset="1" stop-color="#8F7A52" />\n'
        "  </linearGradient>"
    )
    transparent = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" role="img" '
        'aria-label="Mantiq">\n'
        f"  <defs>\n  {gradient}\n  </defs>\n"
        f'  <path d="{SVG_PATH}" fill="none" stroke="url(#mantiq-gold)" stroke-width="{STROKE:g}" '
        'stroke-linecap="round" stroke-linejoin="round" />\n'
        f"{nodes_svg}\n"
        "</svg>\n"
    )
    on_navy = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" role="img" '
        'aria-label="Mantiq">\n'
        f"  <defs>\n  {gradient}\n  </defs>\n"
        '  <rect width="64" height="64" rx="15" fill="#0E1728" />\n'
        f'  <path d="{SVG_PATH}" fill="none" stroke="url(#mantiq-gold)" stroke-width="{STROKE:g}" '
        'stroke-linecap="round" stroke-linejoin="round" />\n'
        f"{nodes_svg}\n"
        "</svg>\n"
    )
    (BRAND / "mantiq-mark.svg").write_text(transparent, encoding="utf-8")
    (BRAND / "mantiq-mark-on-navy.svg").write_text(on_navy, encoding="utf-8")
    PDF_MARK.parent.mkdir(parents=True, exist_ok=True)
    # 96 px, displayed at 27 pt in the PDF: ~2.5× device resolution, and small enough to keep the
    # issued document in the same weight class as before (a 512 px copy tripled the file size)
    transparent_mark(96).save(PDF_MARK, optimize=True)


def write_og_image() -> None:
    width, height = 1200, 630
    img = Image.new("RGB", (width, height), NAVY)
    draw = ImageDraw.Draw(img)
    # a quiet network motif: two node rings in Network Blue, well under the text
    for cx, cy, r in ((1050, 118, 190), (935, 520, 92)):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=NAVY_MID, width=3)
    mark_size = 300
    mark = transparent_mark(mark_size * 2)
    img.paste(mark.resize((mark_size, mark_size), Image.Resampling.LANCZOS), (100, 150), mark.resize((mark_size, mark_size), Image.Resampling.LANCZOS))
    draw.rectangle([100, 150 + mark_size + 34, 100 + 96, 150 + mark_size + 38], fill=GOLD)
    font_big = ImageFont.truetype(str(FONT_BOLD), 126)
    font_small = ImageFont.truetype(str(FONT_BOLD), 34)
    draw.text((440, 232), "Mantiq", font=font_big, fill=ON_DARK)
    draw.text((444, 372), "Insurance Brokerage OS", font=font_small, fill=GOLD)
    draw.text((444, 424), "Mantiq Technologies", font=font_small, fill=ON_DARK_MUTED)
    img.save(PUBLIC / "og-image.png", optimize=True)


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    write_svgs()

    icon_512 = navy_tile(512, 0.22, 0.62)
    icon_512.save(PUBLIC / "icon-512.png", optimize=True)
    navy_tile(192, 0.22, 0.62).save(PUBLIC / "icon-192.png", optimize=True)
    navy_tile(512, 0.22, 0.50).save(PUBLIC / "icon-512-maskable.png", optimize=True)
    # iOS masks the corners itself, so the touch icon is full-bleed navy
    navy_tile(180, 0.0, 0.62).save(PUBLIC / "apple-touch-icon.png", optimize=True)
    navy_tile(32, 0.22, 0.70, nodes=False).save(PUBLIC / "favicon-32.png", optimize=True)

    # Pillow's ICO writer downsamples one source image into every requested size, so the source is the
    # 48px frame; the 16px frame would drop the node dots anyway, which the smallest size does not miss.
    navy_tile(48, 0.22, 0.66).convert("RGBA").save(
        PUBLIC / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    write_og_image()
    print("brand assets written:", ", ".join(sorted(p.name for p in PUBLIC.iterdir() if p.is_file())))


if __name__ == "__main__":
    main()
