#!/usr/bin/env python3
"""docs/logo.svg: "SongSpec" in Coolvetica and a thin "Analyzer" (Segoe UI Light, matched to Coolvetica's cap height), navy, with the letters turned into outlines (so the logo looks the same where
the font is not installed, e.g. on GitHub). Needs the font file (Typodermic's Coolvetica, free for logos) and fontTools.
Run:  .venv/Scripts/python tools/make_logo.py [path/to/Coolvetica Rg.otf]"""
import os
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts/Coolvetica Rg.otf"
OUT = Path(__file__).resolve().parents[1] / "docs" / "logo.svg"
THIN = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeuil.ttf"
BOLD_WORD, THIN_WORD, SUB = "SongSpec", "Analyzer", "KEY  ·  BPM  ·  CHORDS  ·  DETUNE"
NAVY_TOP, NAVY_BOTTOM, GREY = "#13265c", "#0a1433", "#64748b"


def outline(font, text, size, tracking=0.0):
    """-> (svg path data at origin, advance width) for `text` at `size` px; tracking in em."""
    gs, cmap = font.getGlyphSet(), font.getBestCmap()
    upm = font["head"].unitsPerEm
    s = size / upm
    pen = SVGPathPen(gs)
    x = 0.0
    for ch in text:
        name = cmap.get(ord(ch))
        if name is None:
            continue
        gs[name].draw(TransformPen(pen, (s, 0, 0, -s, x, 0)))   # font units are y-up, SVG is y-down
        x += gs[name].width * s + tracking * size
    return pen.getCommands(), x - tracking * size


def cap_height(font):
    return font["OS/2"].sCapHeight / font["head"].unitsPerEm


def main():
    write(OUT, NAVY_TOP, NAVY_BOTTOM, GREY)
    write(OUT.with_name("logo-dark.svg"), "#c9d6ff", "#a9bcf5", "#8b93a7")    # for GitHub's dark theme (navy is unreadable there)


def write(out, top, bottom, grey):
    font, thin = TTFont(str(FONT)), TTFont(str(THIN))
    W, size = 760, 92
    bold, bw = outline(font, BOLD_WORD, size)
    gap = 0.22 * size
    light, lw = outline(thin, THIN_WORD, size * cap_height(font) / cap_height(thin))
    tw = bw + gap + lw
    sub, sw = outline(font, SUB, 17, tracking=0.12)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} 160" width="{W}" height="160" role="img" aria-label="SongSpec Analyzer">
  <defs>
    <linearGradient id="navy" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{top}"/>
      <stop offset="1" stop-color="{bottom}"/>
    </linearGradient>
  </defs>
  <path transform="translate({(W - tw) / 2:.1f} 92)" fill="url(#navy)" d="{bold}"/>
  <path transform="translate({(W - tw) / 2 + bw + gap:.1f} 92)" fill="url(#navy)" d="{light}"/>
  <path transform="translate({(W - sw) / 2:.1f} 134)" fill="{grey}" d="{sub}"/>
</svg>
'''
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out} (title {tw:.0f} px wide)")


if __name__ == "__main__":
    main()
