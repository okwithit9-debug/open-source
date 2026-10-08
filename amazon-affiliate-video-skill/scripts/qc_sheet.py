#!/usr/bin/env python3
"""Build a contact sheet (evenly spaced frames) for visual QC.

    python3 qc_sheet.py clip.mp4                 # writes clip_sheet.jpg next to the clip
    python3 qc_sheet.py clip.mp4 --frames 12 --cols 6 --out /path/to/sheet.jpg

Look for: model-generated text, wrong or morphing pack, duplicate packs,
fused fingers, face warping, floating objects, crossfade ghosts, third-party
watermarks/handles/URLs. Then watch once at full speed with sound.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        text=True,
    )
    return float(out.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip", type=Path)
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--height", type=int, default=360, help="height of each tile in px")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    out = a.out or a.clip.with_name(a.clip.stem + "_sheet.jpg")
    total = duration(a.clip)
    rows = -(-a.frames // a.cols)
    with tempfile.TemporaryDirectory() as td:
        tiles = []
        for i in range(a.frames):
            t = min(total - 0.1, total * i / max(1, a.frames - 1))
            tile = Path(td) / f"{i:03d}.jpg"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(a.clip),
                 "-frames:v", "1", "-vf", f"scale=-2:{a.height}", str(tile)],
                check=True,
            )
            tiles.append(tile)
        while len(tiles) < rows * a.cols:
            tiles.append(tiles[-1])
        inputs = []
        for t in tiles:
            inputs += ["-i", str(t)]
        # xstack needs pixel offsets; build them from the first tile size
        probe = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(tiles[0])],
            text=True,
        ).strip().split(",")
        tw, th = int(probe[0]), int(probe[1])
        layout = "|".join(f"{(i % a.cols) * tw}_{(i // a.cols) * th}" for i in range(len(tiles)))
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
             f"xstack=inputs={len(tiles)}:layout={layout}", "-frames:v", "1", str(out)],
            check=True,
        )
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
