# BTC Prague 2026 — Print Design Project

## Background

Print-ready PDF generation for a BTC Map stand at the **BTC Prague 2026** conference.
Two deliverables:

1. **Counter Image** (Front Desk Graphic): 1200×1050 mm + 50 mm bleed, 1:10 scale
2. **Back Wall Image**: 3000×2500 mm + 10 mm bleed, 1:10 scale

Both must be print-ready PDFs with 300 dpi embedded raster images, CMYK colour space, and crop marks.

## Brand & Design System

- **Website reference**: btcmap.org (light mode)
- **Background**: `#E4EBEC` (teal)
- **Typography**: Manrope (Regular 400, Bold 700, ExtraBold 800) from Google Fonts gstatic
- **Gradient**: 45deg `#0ECD71` → `#040273`
- **Icons**: Material Design icons in outlined circles (outlined style, not filled)
- **Confetti**: stars and bursts only, full brand colours, placed in clear zones away from text

## Project Structure

```
btc-prague-2026/
├── generate.py                    # Main generator script (Python/ReportLab)
├── .gitignore                     # Git ignore rules
├── AGENTS.md                      # This file
├── assets/
│   ├── btcmap-logo.svg            # BTC Map logo (from btcmap.org)
│   ├── street-map.svg             # Street map overlay pattern
│   ├── the-map-is-the-territory.svg  # Counter image (vector)
│   └── fonts/
│       ├── Manrope-Regular.ttf    # 400
│       ├── Manrope-Bold.ttf       # 700
│       ├── Manrope-ExtraBold.ttf  # 800
│       └── MaterialIcons-Regular.ttf  # Material Design icons
├── output/
│   ├── counter.pdf                # Final counter deliverable
│   └── back-wall.pdf              # Final back wall deliverable
└── .build/                        # Cache dir (raster intermediates, previews)
```

## Tech Stack

- **Python 3.11+**
- **ReportLab**: PDF composition, layouts, drawing primitives
- **cairosvg**: SVG → PNG rasterization
- **Pillow (PIL)**: Image manipulation, gradient text rendering
- **fonttools**: Font subsetting (optional)

## Build & Run

```bash
# Ensure virtual environment is active
source .venv/bin/activate

# Regenerate both PDFs
python generate.py
```

## Key Technical Details

### DPI at 1:10 Scale
To achieve 300 dpi at 1:1 final print, embedded rasters must be rendered at **3000 dpi** at the 1:10 output scale.

Example: a 3072×3072 px image at 3000 dpi ≈ 26 mm at 1:10 scale, which becomes 260 mm at 1:1 — acceptable for print.

### Gradient Text Workaround
ReportLab cannot do CSS `background-clip: text` with gradients natively. Solution: render text as high-res PNG via Pillow with gradient fill, then embed as image.

### SVG Mask Bug
`cairosvg` renders `<mask>` elements defined outside `<defs>` as visible content. **Fix**: preprocess SVG to move all `<defs>` to the top before rasterization.

### Helvetica Reference
ReportLab includes a non-embedded `/Helvetica` reference by default even when unused. This is a PDF base-14 font and is acceptable for print RIPs.

### PIL Decompression Bomb
Large PNGs (e.g., 12288×12288 px) trigger Pillow's safety limit. Set `Image.MAX_IMAGE_PIXELS = None` when working with large raster intermediates.

### CMYK
All output is CMYK. ICC profile conversion should be applied if the printer provides a specific profile.

## Git Conventions

- `.build/` is excluded from git (contains generated intermediates)
- `.venv/` is excluded
- `opencode.json` is excluded
- Output PDFs (`output/*.pdf`) **are** committed for convenience

## File Size Limits

- Counter PDF: max 50 MB (current ~2 MB)
- Back wall PDF: max 100 MB (current ~6 MB)

## Regenerating

If assets or design requirements change, edit `generate.py` and run:

```bash
python generate.py
```

Both PDFs will be regenerated in `./output/`.
