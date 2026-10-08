#!/usr/bin/env python3
"""Burn a readable '#ad' disclosure (and optional title) into a video with ffmpeg.

    python3 burn_disclosure.py in.mp4 out.mp4
    python3 burn_disclosure.py in.mp4 out.mp4 --title "No white cast" --title-until 3

Font: set FONT_FILE to a .ttf/.otf path, otherwise ffmpeg's default font is used.
Audio is copied unchanged. Metadata is stripped from the output.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def drawtext(text: str, x: str, y: str, size: str, extra: str = "") -> str:
    font = os.environ.get("FONT_FILE", "").strip()
    font_opt = f"fontfile='{font}':" if font else ""
    return (
        f"drawtext={font_opt}text='{esc(text)}':fontcolor=white:fontsize={size}:"
        f"shadowcolor=0x0000008C:shadowx=2:shadowy=2:x={x}:y={y}{extra}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--label", default="#ad")
    ap.add_argument("--title", help="optional short overlay title (you write it, not the model)")
    ap.add_argument("--title-until", type=float, default=3.0, help="seconds the title stays on screen")
    a = ap.parse_args()
    filters = [drawtext(a.label, "w*0.06", "h*0.105", "h*0.04")]
    if a.title:
        filters.append(drawtext(a.title, "(w-text_w)/2", "h*0.18", "h*0.045", f":enable='lt(t,{a.title_until})'"))
    cmd = [
        "ffmpeg", "-y", "-i", a.src, "-vf", ",".join(filters),
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-map_metadata", "-1", "-movflags", "+faststart", a.dst,
    ]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
