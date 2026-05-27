"""Render menubar icons once at build time. Run: python scripts/render_icons.py"""
from pathlib import Path

from AppKit import NSBitmapImageRep, NSImage, NSPNGFileType, NSGraphicsContext
from Foundation import NSMakeRect

OUT = Path(__file__).resolve().parent.parent / "resources"
OUT.mkdir(exist_ok=True)

SYMBOLS = {
    "icon-on.png": "circle.fill",
    "icon-off.png": "circle",
}
SIZE = 18  # menubar template icon point size

for filename, symbol in SYMBOLS.items():
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None)
    if image is None:
        raise SystemExit(f"SF Symbol {symbol!r} not available; macOS 11+ required")
    image.setSize_((SIZE, SIZE))
    image.setTemplate_(True)

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, SIZE * 2, SIZE * 2, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0
    )
    rep.setSize_((SIZE, SIZE))

    NSGraphicsContext.saveGraphicsState()
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.setCurrentContext_(ctx)
    image.drawInRect_(NSMakeRect(0, 0, SIZE, SIZE))
    NSGraphicsContext.restoreGraphicsState()

    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    out_path = OUT / filename
    data.writeToFile_atomically_(str(out_path), True)
    print(f"wrote {out_path}")
