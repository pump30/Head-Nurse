"""Render menubar icons once at build time. Run: python scripts/render_icons.py

Downloads SAP-icons.ttf from OpenUI5 CDN and renders the 'tools-opportunity'
glyph as a macOS menubar template icon (black on transparent, @2x).
"""
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "resources"
OUT.mkdir(exist_ok=True)

FONT_URL = "https://openui5.hana.ondemand.com/resources/sap/ui/core/themes/base/fonts/SAP-icons.ttf"
FONT_CACHE = Path(__file__).resolve().parent / ".SAP-icons.ttf"

# SAP icon 'tools-opportunity' codepoint (decimal 57527 = 0xe0b7)
ICON_CHAR = chr(57527)

# icon-on: full opacity (active), icon-off: 50% opacity (inactive)
VARIANTS = {
    "icon-on.png": 255,
    "icon-off.png": 128,
}

SIZE = 36  # 2x pixels for 18pt retina menubar icon
FONT_SIZE = 30


def ensure_font() -> Path:
    if FONT_CACHE.exists():
        return FONT_CACHE
    print(f"Downloading SAP-icons.ttf from {FONT_URL} ...")
    data = urllib.request.urlopen(FONT_URL, timeout=30).read()
    FONT_CACHE.write_bytes(data)
    print(f"  cached to {FONT_CACHE}")
    return FONT_CACHE


def render():
    font_path = ensure_font()
    pil_font = ImageFont.truetype(str(font_path), FONT_SIZE)

    for filename, alpha in VARIANTS.items():
        img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        bbox = draw.textbbox((0, 0), ICON_CHAR, font=pil_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (SIZE - text_w) / 2 - bbox[0]
        y = (SIZE - text_h) / 2 - bbox[1]

        draw.text((x, y), ICON_CHAR, font=pil_font, fill=(0, 0, 0, alpha))

        out_path = OUT / filename
        img.save(out_path)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    render()
