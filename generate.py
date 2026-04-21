#!/usr/bin/env python3
"""
Generate print-ready PDFs for BTC Map stand at BTC Prague 2026.

Outputs (1:10 scale, CMYK, with crop marks):
  - output/counter.pdf    Trim 1200x1050 mm, bleed 50 mm/side  -> 130x115 mm
  - output/back-wall.pdf  Trim 3000x2500 mm, bleed 10 mm/side  -> 302x252 mm
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import cairosvg
import qrcode
from PIL import Image, ImageDraw, ImageFont

# The ultra-high-res counter source is intentionally huge; disable Pillow's
# decompression-bomb safety for this project.
Image.MAX_IMAGE_PIXELS = None
from reportlab.lib.colors import CMYKColor, Color
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"
OUTPUT = ROOT / "output"
BUILD = ROOT / ".build"
OUTPUT.mkdir(exist_ok=True)
BUILD.mkdir(exist_ok=True)

LOGO_SVG = ASSETS / "btcmap-logo.svg"
MAP_SVG = ASSETS / "street-map.svg"
COUNTER_IMG = ASSETS / "the-map-is-the-territory.svg"

# ---------------------------------------------------------------------------
# Brand colors (sourced from btcmap.org light-mode Tailwind config,
# converted to CMYK approximations for print).
#
#   name       hex        CMYK approx (C, M, Y, K)
#   teal       #E4EBEC    0.10, 0.05, 0.07, 0.00  (page background)
#   primary    #144046    0.72, 0.12, 0.23, 0.73  (headings text)
#   body       #3E6267    0.43, 0.07, 0.09, 0.60  (body text)
#   link       #0099AF    1.00, 0.13, 0.20, 0.31  (links / buttons)
#   hover      #00B7D2    1.00, 0.13, 0.00, 0.18  (hover / accent)
# Gradient sub-heading: 45deg  #0ECD71 (green) -> #040273 (navy)
# ---------------------------------------------------------------------------
C_PAGE_BG   = CMYKColor(0.10, 0.05, 0.07, 0.00)   # #E4EBEC
C_PRIMARY   = CMYKColor(0.72, 0.12, 0.23, 0.73)   # #144046
C_BODY      = CMYKColor(0.43, 0.07, 0.09, 0.60)   # #3E6267
C_LINK      = CMYKColor(1.00, 0.13, 0.20, 0.31)   # #0099AF

# Gradient endpoints (RGB, for the rasterised sub-heading)
GRAD_START_RGB = (0x0E, 0xCD, 0x71)
GRAD_END_RGB   = (0x04, 0x02, 0x73)

# Kept for reference / crop marks
WHITE = CMYKColor(0, 0, 0, 0)
REG_BLACK = CMYKColor(1, 1, 1, 1)
BRAND_GREEN = CMYKColor(0.95, 0.00, 0.50, 0.44)   # #0B9072 - older full-bleed color
BLEED_FILL = WHITE

# Output design scale: 1:10 (1 mm of artwork = 0.1 mm on PDF)
SCALE = 0.1

# Required raster resolution at FULL print scale.
# Because the PDF is at 1:10, embedded rasters must be 10x denser at output
# scale to satisfy 300 dpi at 1:1 final print size.
TARGET_DPI_FULL = 300
# DPI at output scale required to achieve TARGET_DPI_FULL at 1:1
TARGET_DPI_OUTPUT = TARGET_DPI_FULL / SCALE   # = 3000 dpi at output scale

# Hard caps to keep Cairo happy and file sizes sane
MAX_RASTER_PX = 12000          # logo/foreground detail (must hit 300 dpi at 1:1)
MAX_BG_RASTER_PX = 6000        # decorative backgrounds (lower density acceptable)
MAX_QR_PX = 6000               # QR (binary art - very compressible)

# Crop mark spec
CROP_LEN_MM = 5.0          # length of each crop mark line (at output scale)
CROP_OFFSET_MM = 2.0       # gap between trim edge and start of crop mark
CROP_WEIGHT_PT = 0.25      # 0.25 pt hairline

# QR rendering: high error correction so the logo could be overlaid later if wanted
QR_URL = "https://btcmap.org"


# ---------------------------------------------------------------------------
# Font registration
# ---------------------------------------------------------------------------
# Primary typeface is Manrope (as used on btcmap.org). We register Regular,
# Bold and ExtraBold and expose their PDF names for use by drawing code.
FONT_DIR = ASSETS / "fonts"
FONT_REGULAR_TTF  = FONT_DIR / "Manrope-Regular.ttf"
FONT_BOLD_TTF     = FONT_DIR / "Manrope-Bold.ttf"
FONT_XBOLD_TTF    = FONT_DIR / "Manrope-ExtraBold.ttf"
FONT_ICONS_TTF    = FONT_DIR / "MaterialIcons-Regular.ttf"


def register_fonts() -> tuple[str, str, str, str]:
    """Register Manrope + Material Icons in ReportLab and return
    (xbold, bold, regular, icons) names."""
    missing = [p for p in (FONT_REGULAR_TTF, FONT_BOLD_TTF,
                           FONT_XBOLD_TTF, FONT_ICONS_TTF) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"Fonts missing: {missing}. Expected in assets/fonts/.")
    pdfmetrics.registerFont(TTFont("Manrope",          str(FONT_REGULAR_TTF)))
    pdfmetrics.registerFont(TTFont("Manrope-Bold",     str(FONT_BOLD_TTF)))
    pdfmetrics.registerFont(TTFont("Manrope-ExtraBold", str(FONT_XBOLD_TTF)))
    pdfmetrics.registerFont(TTFont("MaterialIcons",    str(FONT_ICONS_TTF)))
    return "Manrope-ExtraBold", "Manrope-Bold", "Manrope", "MaterialIcons"


# ---------------------------------------------------------------------------
# Asset rasterization
# ---------------------------------------------------------------------------
def rasterize_svg(svg_path: Path, out_path: Path, target_w_px: int) -> Path:
    """Render an SVG to PNG at a given pixel width using cairosvg."""
    if out_path.exists() and out_path.stat().st_mtime > svg_path.stat().st_mtime:
        return out_path
    cairosvg.svg2png(
        url=str(svg_path),
        write_to=str(out_path),
        output_width=target_w_px,
    )
    return out_path


def make_qr_png(url: str, out_path: Path, target_px: int) -> Path:
    """Generate a high-resolution monochrome QR PNG."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=20,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    img = img.resize((target_px, target_px), Image.Resampling.NEAREST)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def render_gradient_text_png(
    text: str,
    font_path: Path,
    font_px: int,
    out_path: Path,
    start_rgb: tuple[int, int, int] = GRAD_START_RGB,
    end_rgb: tuple[int, int, int] = GRAD_END_RGB,
    angle_deg: float = 45.0,
    padding_px: int = 10,
) -> Path:
    """Render `text` with a linear gradient fill to a transparent PNG.

    Equivalent of the CSS:
        background: linear-gradient(45deg, #0ecd71, #040273);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;

    The gradient runs from start_rgb (bottom-left at 45deg) to end_rgb
    (top-right), matching the `-webkit-linear-gradient(45deg, ...)` convention
    used on btcmap.org.
    """
    import math

    font = ImageFont.truetype(str(font_path), font_px)
    # Measure text bbox
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t

    W = tw + 2 * padding_px
    H = th + 2 * padding_px

    # 1) Alpha mask of the text (white-on-transparent)
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.text((padding_px - l, padding_px - t), text, font=font, fill=255)

    # 2) Gradient image. CSS `-webkit-linear-gradient(45deg, ...)` goes from
    # bottom-left to top-right, so the gradient vector points up and to the
    # right at 45 degrees.
    ang = math.radians(angle_deg)
    vx, vy = math.cos(ang), -math.sin(ang)   # y inverted (image coords)
    # Normalise projection range over the image
    # Corners projected onto (vx, vy): find min/max
    corners = [(0, 0), (W, 0), (0, H), (W, H)]
    projections = [x * vx + y * vy for x, y in corners]
    pmin, pmax = min(projections), max(projections)
    span = pmax - pmin if pmax != pmin else 1.0

    grad = Image.new("RGB", (W, H))
    grad_px = grad.load()
    s = start_rgb
    e = end_rgb
    for y in range(H):
        for x in range(W):
            t_ = ((x * vx + y * vy) - pmin) / span
            r_ = int(s[0] + (e[0] - s[0]) * t_)
            g_ = int(s[1] + (e[1] - s[1]) * t_)
            b_ = int(s[2] + (e[2] - s[2]) * t_)
            grad_px[x, y] = (r_, g_, b_)

    # 3) Compose: use the gradient as fill, masked by the text.
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    out.save(out_path, "PNG", optimize=True)
    return out_path


def tint_png_to_white_on_alpha(src: Path, dst: Path) -> Path:
    """Convert a PNG to white-foreground with original alpha (for map overlay)."""
    img = Image.open(src).convert("RGBA")
    pixels = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            # treat any non-transparent pixel as white
            pixels[x, y] = (255, 255, 255, a)
    img.save(dst, "PNG", optimize=True)
    return dst


# ---------------------------------------------------------------------------
# Crop marks
# ---------------------------------------------------------------------------
def draw_crop_marks(c: canvas.Canvas, trim_w_mm: float, trim_h_mm: float,
                    bleed_mm: float) -> None:
    """Draw 4 corner crop marks just outside the trim box.

    The PDF coordinate system is in points, scaled so 1 unit = 1 mm via
    canvas.scale() in caller. All crop dimensions are at OUTPUT scale.
    """
    c.saveState()
    # Registration black (100% all four channels) so crop marks print on every plate
    c.setStrokeColorCMYK(1, 1, 1, 1)
    c.setLineWidth(CROP_WEIGHT_PT)
    # Origin (0,0) is bottom-left of bleed box; trim box is inset by bleed
    x0 = bleed_mm
    y0 = bleed_mm
    x1 = bleed_mm + trim_w_mm
    y1 = bleed_mm + trim_h_mm
    o = CROP_OFFSET_MM
    L = CROP_LEN_MM

    # Bottom-left
    c.line(x0 - o - L, y0, x0 - o, y0)            # horizontal
    c.line(x0, y0 - o - L, x0, y0 - o)            # vertical
    # Bottom-right
    c.line(x1 + o, y0, x1 + o + L, y0)
    c.line(x1, y0 - o - L, x1, y0 - o)
    # Top-left
    c.line(x0 - o - L, y1, x0 - o, y1)
    c.line(x0, y1 + o, x0, y1 + o + L)
    # Top-right
    c.line(x1 + o, y1, x1 + o + L, y1)
    c.line(x1, y1 + o, x1, y1 + o + L)
    c.restoreState()


# ---------------------------------------------------------------------------
# Page geometry helper
# ---------------------------------------------------------------------------
def begin_page(c: canvas.Canvas, trim_w_mm: float, trim_h_mm: float,
               bleed_mm: float, fill: CMYKColor = BLEED_FILL) -> tuple[float, float]:
    """Configure the canvas for a page where 1 user-unit == 1 mm at output
    scale. Returns (page_w_mm, page_h_mm) including bleed.
    """
    page_w_mm = trim_w_mm + 2 * bleed_mm
    page_h_mm = trim_h_mm + 2 * bleed_mm
    c.setPageSize((page_w_mm * mm, page_h_mm * mm))
    # Scale so subsequent drawing uses millimetres directly
    c.scale(mm, mm)
    # Fill bleed area
    c.setFillColor(fill)
    c.rect(0, 0, page_w_mm, page_h_mm, stroke=0, fill=1)
    return page_w_mm, page_h_mm


def set_pdf_boxes(c: canvas.Canvas, trim_w_mm: float, trim_h_mm: float,
                  bleed_mm: float) -> None:
    """Annotate the page with TrimBox / BleedBox so the printer's RIP knows
    the trim location.
    """
    page_w_pt = (trim_w_mm + 2 * bleed_mm) * mm
    page_h_pt = (trim_h_mm + 2 * bleed_mm) * mm
    bleed_pt = bleed_mm * mm
    # ReportLab box setters take (llx, lly, urx, ury) in points
    trim_box = (bleed_pt, bleed_pt, page_w_pt - bleed_pt, page_h_pt - bleed_pt)
    bleed_box = (0, 0, page_w_pt, page_h_pt)
    c.setTrimBox(trim_box)
    c.setBleedBox(bleed_box)
    c.setCropBox(trim_box)


# ---------------------------------------------------------------------------
# Counter (front desk) - 1200x1050 mm trim + 50 mm bleed
# Just the the-map-is-the-territory.jpg image, centred on a white background.
# ---------------------------------------------------------------------------
def build_counter(font_bold: str, font_reg: str) -> Path:
    trim_w, trim_h = 1200.0, 1050.0
    bleed = 50.0
    out_trim_w = trim_w * SCALE         # 120 mm
    out_trim_h = trim_h * SCALE         # 105 mm
    out_bleed = bleed * SCALE           # 5 mm

    out_path = OUTPUT / "counter.pdf"
    c = canvas.Canvas(str(out_path))
    c.setTitle("BTC Map - Counter Graphic")
    c.setAuthor("BTC Map")
    c.setSubject("BTC Prague 2026 stand")
    c.setFont(font_bold, 6)

    # White background (out to the bleed)
    page_w, page_h = begin_page(c, out_trim_w, out_trim_h, out_bleed,
                                fill=WHITE)

    # Place the image so it fills the trim height (1050 mm) centred on the
    # counter, at the 300 dpi minimum specified by the printer.
    img_h_full_mm = trim_h
    if COUNTER_IMG.suffix.lower() == ".svg":
        # SVG is vector — render via cairosvg at exact target pixel size.
        aspect = 1.0  # the source SVG is square (viewBox="0 0 500 500")
    else:
        with Image.open(COUNTER_IMG) as src:
            src_w_px, src_h_px = src.size
            aspect = src_w_px / src_h_px

    img_w_full_mm = img_h_full_mm * aspect

    # Render to a high-res raster so the final print is 300+ dpi at 1:1.
    target_px = int(round((img_w_full_mm / 25.4) * TARGET_DPI_FULL))
    embed_path = BUILD / f"counter-img-{target_px}.png"

    if not embed_path.exists() or embed_path.stat().st_mtime < COUNTER_IMG.stat().st_mtime:
        if COUNTER_IMG.suffix.lower() == ".svg":
            cairosvg.svg2png(
                url=str(COUNTER_IMG),
                write_to=str(embed_path),
                output_width=target_px,
            )
            rgba = Image.open(embed_path).convert("RGBA")
        else:
            with Image.open(COUNTER_IMG) as src:
                rgba = src.convert("RGBA").resize(
                    (target_px, int(round(target_px / aspect))),
                    Image.Resampling.LANCZOS,
                )
        # Composite onto white so any transparent areas print as white.
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        bg.convert("RGB").save(embed_path, "PNG", optimize=True)

    img_w_out_mm = img_w_full_mm * SCALE
    img_h_out_mm = img_h_full_mm * SCALE

    # Centre within trim box
    cx = out_bleed + out_trim_w / 2
    cy = out_bleed + out_trim_h / 2
    img_x = cx - img_w_out_mm / 2
    img_y = cy - img_h_out_mm / 2

    c.drawImage(str(embed_path), img_x, img_y,
                width=img_w_out_mm, height=img_h_out_mm,
                preserveAspectRatio=True)

    draw_crop_marks(c, out_trim_w, out_trim_h, out_bleed)
    set_pdf_boxes(c, out_trim_w, out_trim_h, out_bleed)
    c.showPage()
    c.save()

    print(f"  Counter image: {target_px}x{int(round(target_px/aspect))}px placed at "
          f"{img_w_full_mm:.0f}x{img_h_full_mm:.0f} mm (1:1) -> "
          f"{TARGET_DPI_FULL} dpi")
    return out_path


# ---------------------------------------------------------------------------
# Back wall - 3000x2500 mm trim + 10 mm bleed
# Styled to match btcmap.org light mode: teal page background (#E4EBEC),
# Manrope typography, gradient sub-heading (45deg #0ecd71 -> #040273),
# primary-coloured headline, body-coloured features, link-coloured URL pill.
# ---------------------------------------------------------------------------
def build_backwall(font_xbold: str, font_bold: str, font_reg: str,
                    font_icons: str) -> Path:
    trim_w, trim_h = 3000.0, 2500.0
    bleed = 10.0
    out_trim_w = trim_w * SCALE   # 300 mm
    out_trim_h = trim_h * SCALE   # 250 mm
    out_bleed = bleed * SCALE     # 1 mm

    out_path = OUTPUT / "back-wall.pdf"
    c = canvas.Canvas(str(out_path))
    c.setTitle("BTC Map - Back Wall Graphic")
    c.setAuthor("BTC Map")
    c.setSubject("BTC Prague 2026 stand")
    # Set our embedded font as default before any page content
    c.setFont(font_bold, 6)

    # Page background: teal (#E4EBEC) - the site's light-mode body color
    page_w, page_h = begin_page(c, out_trim_w, out_trim_h, out_bleed,
                                fill=C_PAGE_BG)

    cx = out_bleed + out_trim_w / 2
    # Live design area inside trim box
    trim_x0 = out_bleed
    trim_y0 = out_bleed
    margin = out_trim_w * 0.06

    # ---- Faint street-map background overlay ----
    # On the site the street-map.svg is placed top-right at ~60% opacity over
    # the teal. Here we mirror that: top-right anchored, lightly blended in.
    map_w_out_mm = out_trim_w * 0.60
    map_h_out_mm = map_w_out_mm * (1027 / 802)
    map_in_full = (map_w_out_mm / SCALE) / 25.4
    map_px = min(MAX_BG_RASTER_PX, max(2400, int(map_in_full * 150)))
    raw_map_png = rasterize_svg(MAP_SVG, BUILD / f"map-{map_px}.png", map_px)

    # Tint the (white-on-transparent) SVG render to primary colour at ~20%
    # opacity against the teal bg so it reads as subtle map lines.
    faint_map_png = BUILD / f"map-site-faint-{map_px}.png"
    if not faint_map_png.exists():
        im = Image.open(raw_map_png).convert("RGBA")
        # Replace white with primary colour and fade alpha
        r, g, b, a = im.split()
        r = r.point(lambda _v: 0x14)
        g = g.point(lambda _v: 0x40)
        b = b.point(lambda _v: 0x46)
        a = a.point(lambda v: int(v * 0.18))
        Image.merge("RGBA", (r, g, b, a)).save(
            faint_map_png, "PNG", optimize=True)

    map_x = trim_x0 + out_trim_w - map_w_out_mm + 15
    map_y = trim_y0 + out_trim_h - map_h_out_mm + 10
    c.drawImage(str(faint_map_png), map_x, map_y,
                width=map_w_out_mm, height=map_h_out_mm,
                mask="auto", preserveAspectRatio=True)

    # ---- Header row: Logo + "BTC Map" (like the site header) ----
    logo_w_out_mm = out_trim_w * 0.10   # ~300 mm at full scale
    logo_h_out_mm = logo_w_out_mm * (344 / 267)
    logo_in_full = (logo_w_out_mm / SCALE) / 25.4
    logo_px = min(MAX_RASTER_PX, max(1500, int(logo_in_full * TARGET_DPI_FULL)))
    logo_png = rasterize_svg(LOGO_SVG, BUILD / f"logo-{logo_px}.png", logo_px)
    logo_x = trim_x0 + margin
    logo_y = trim_y0 + out_trim_h - logo_h_out_mm - margin
    c.drawImage(str(logo_png), logo_x, logo_y,
                width=logo_w_out_mm, height=logo_h_out_mm,
                mask="auto", preserveAspectRatio=True)

    # "BTC Map" right of logo, vertically centred, with the same gradient
    # as the sub-heading (45deg #0ECD71 -> #040273).
    headline = "BTC Map"
    headline_size = 32
    head_font_px = 400
    head_png = BUILD / f"headline-{hash(headline) & 0xffff}-{head_font_px}.png"
    if not head_png.exists():
        render_gradient_text_png(headline, FONT_XBOLD_TTF, head_font_px, head_png)
    head_img = Image.open(head_png)
    head_w_px, head_h_px = head_img.size
    head_aspect = head_w_px / head_h_px
    head_h_out = headline_size
    head_w_out = head_h_out * head_aspect
    head_x = logo_x + logo_w_out_mm + 6
    # Centre the headline image vertically to the logo
    head_y = logo_y + (logo_h_out_mm - head_h_out) / 2
    c.drawImage(str(head_png), head_x, head_y,
                width=head_w_out, height=head_h_out,
                mask="auto", preserveAspectRatio=True)

    # ---- Gradient sub-heading (two lines, like the site) ----
    sub_lines = ["Find places to spend", "sats wherever you are"]
    sub_target_h_mm = 14.0   # per line cap height
    sub_font_px = 220
    sub_gap_mm = 2.0         # gap between lines

    # Render each line as a separate gradient PNG and stack them
    sub_line_imgs = []
    for line in sub_lines:
        line_png = BUILD / f"subhead-{hash(line) & 0xffff}-{sub_font_px}.png"
        if not line_png.exists():
            render_gradient_text_png(line, FONT_XBOLD_TTF, sub_font_px, line_png)
        sub_line_imgs.append((line, line_png))

    # Place below the logo/headline row — tight gap (hero block feels connected)
    sub_x = trim_x0 + margin
    gap_tight = 14.0
    gap_medium = 24.0
    sub_y = logo_y - gap_tight  # start close below the logo block
    for _, line_png in sub_line_imgs:
        im = Image.open(line_png)
        sub_w_px, sub_h_px = im.size
        sub_aspect = sub_w_px / sub_h_px
        sub_h_out = sub_target_h_mm
        sub_w_out = sub_h_out * sub_aspect
        sub_y -= sub_h_out
        c.drawImage(str(line_png), sub_x, sub_y,
                    width=sub_w_out, height=sub_h_out,
                    mask="auto", preserveAspectRatio=True)
        sub_y -= sub_gap_mm

    # ---- Feature blurbs with Material Design round icons ----
    # Transparent circles with coloured outline + matching coloured icon.
    # All three glyphs have visual centre at 256/512 = 0.5000 of em height.
    features = [
        (chr(0xEA70), "Free & Open Source",
         "Our apps and the underlying data are free and open-source.",
         BRAND_GREEN),   # volunteer_activism
        (chr(0xF8D9), "Community-driven",
         "Powered by OpenStreetMap contributors worldwide.",
         C_LINK),        # diversity_3
        (chr(0xEA0B), "#SPEDN",
         "Spending sats is direct action. Join the movement.",
         CMYKColor(0.72, 0.60, 0.15, 0.70)),  # bolt
    ]
    feat_y = sub_y - gap_medium
    line_gap = 26
    title_size = 9
    body_size = 7
    icon_r = 8.0
    icon_stroke_pt = 0.5
    # Shift text further right so it doesn't crowd the icon circle
    feat_x = trim_x0 + margin + 26
    for i, (icon_char, title, body, icon_colour) in enumerate(features):
        y = feat_y - i * line_gap
        icon_cx = trim_x0 + margin + icon_r
        # Vertical centre of the whole blurb block (title + body)
        title_top    = y + title_size * 0.7
        body_bottom  = y - 10.0 - body_size * 0.1
        block_centre = (title_top + body_bottom) / 2
        icon_cy = block_centre
        # Transparent circle with coloured outline
        c.setStrokeColor(icon_colour)
        c.setLineWidth(icon_stroke_pt)
        c.circle(icon_cx, icon_cy, icon_r, stroke=1, fill=0)
        # Coloured Material Icon glyph, centred in the circle
        c.setFillColor(icon_colour)
        icon_font_size = icon_r * 1.15
        c.setFont(font_icons, icon_font_size)
        icon_text_y = icon_cy - 0.50 * icon_font_size
        c.drawCentredString(icon_cx, icon_text_y, icon_char)
        # Text
        c.setFillColor(C_PRIMARY)
        c.setFont(font_bold, title_size)
        c.drawString(feat_x, y, title)
        c.setFillColor(C_BODY)
        c.setFont(font_reg, body_size)
        c.drawString(feat_x, y - 10.0, body)

    # ---- URL pill (centred, bottom of trim) ----
    # Rendered like the site's rounded pill buttons: white text on link-blue
    pill_url = "btcmap.org"
    pill_font_size = 14
    c.setFont(font_bold, pill_font_size)
    text_w = pdfmetrics.stringWidth(pill_url, font_bold, pill_font_size)
    pad_x = 10
    pad_y = 5
    pill_w = text_w + 2 * pad_x
    pill_h = pill_font_size + 2 * pad_y
    pill_x = cx - pill_w / 2
    # Position pill with medium gap above it
    pill_y = trim_y0 + margin
    c.setFillColor(C_LINK)
    c.roundRect(pill_x, pill_y, pill_w, pill_h, pill_h / 2, stroke=0, fill=1)
    c.setFillColor(WHITE)
    c.drawCentredString(cx, pill_y + pad_y + pill_font_size * 0.18, pill_url)

    # Crop marks
    draw_crop_marks(c, out_trim_w, out_trim_h, out_bleed)

    set_pdf_boxes(c, out_trim_w, out_trim_h, out_bleed)
    c.showPage()
    c.save()
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    font_xbold, font_bold, font_reg, font_icons = register_fonts()
    print(f"Fonts: {font_xbold} / {font_bold} / {font_reg} / {font_icons}")
    counter = build_counter(font_bold, font_reg)
    print(f"Wrote {counter} ({counter.stat().st_size/1024:.1f} KB)")
    backwall = build_backwall(font_xbold, font_bold, font_reg, font_icons)
    print(f"Wrote {backwall} ({backwall.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
