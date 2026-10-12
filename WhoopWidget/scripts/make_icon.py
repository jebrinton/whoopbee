#!/usr/bin/env python3
"""Generates the app icon set (needs Pillow, which /usr/bin/python3 on this Mac has)."""
from pathlib import Path
from PIL import Image, ImageDraw
def icon(px):
    S = 4
    im = Image.new('RGBA', (px*S, px*S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, px*S-1, px*S-1], radius=px*S*0.22, fill=(0x1c, 0x5c, 0xab, 255))
    y0 = px*S*0.47
    d.rectangle([px*S*0.14, y0-px*S*0.011, px*S*0.86, y0+px*S*0.011], fill=(255, 255, 255, 140))
    offs = [0.72, 0.80, 0.52, 0.56, 0.44, 0.50, 0.40, 0.47]
    for i, o in enumerate(offs):
        x = px*S*(0.16 + 0.68*i/(len(offs)-1)); y = px*S*o
        rr = px*S*(0.040 if i < len(offs)-1 else 0.055)
        d.ellipse([x-rr, y-rr, x+rr, y+rr], fill=(255, 255, 255, 255))
    return im.resize((px, px), Image.LANCZOS)
base = Path(__file__).resolve().parent.parent / "WhoopWidget/Assets.xcassets/AppIcon.appiconset"
for px in (16, 32, 64, 128, 256, 512, 1024):
    icon(px).save(base / f"icon_{px}.png")
print("icons written to", base)
