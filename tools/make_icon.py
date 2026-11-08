#!/usr/bin/env python3
"""rust/songspec-app/assets/icon.png -> icon.ico (16 - 256 px, for the .exe) and icon_256.png (the window / taskbar icon).
Transparent background kept; the picture is centred on a square canvas first. Run after replacing icon.png, then rebuild.
Run:  .venv/Scripts/python tools/make_icon.py"""
from pathlib import Path

from PIL import Image

A = Path(__file__).resolve().parents[1] / "rust" / "songspec-app" / "assets"
im = Image.open(A / "icon.png").convert("RGBA")
bbox = im.getbbox()
if bbox:
    im = im.crop(bbox)
side = max(im.size)
sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
big = sq.resize((256, 256), Image.LANCZOS)
big.save(A / "icon_256.png")
big.save(A / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", A / "icon.ico", "and", A / "icon_256.png")
