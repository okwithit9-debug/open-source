#!/usr/bin/env python3
"""Trim dead air from modular cuts and concatenate them into one 9:16 video.

Each cut's speech window is found with an RMS loudness scan, so quiet room
tone after the last word is treated as silence (ffmpeg silencedetect alone
often misses it). Cuts are trimmed, scaled/padded to one size and joined.

    python3 stitch.py --out final.mp4 hook.mp4 story.mp4 proof.mp4 cta.mp4
    python3 stitch.py --out final.mp4 --no-trim a.mp4 b.mp4

Requires ffmpeg and ffprobe on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import array
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        text=True,
    )
    return float(out.strip())


def has_audio(path: Path) -> bool:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        text=True,
    )
    return bool(out.strip())


def speech_window(path: Path, threshold: float, pad: float, win: float = 0.05, gap: float = 0.35) -> tuple[float, float]:
    """Return (start, end) seconds of sustained speech, padded. Falls back to the full clip."""
    total = duration(path)
    if not has_audio(path):
        return 0.0, total
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        run(["ffmpeg", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        with wave.open(str(wav), "rb") as w:
            rate = w.getframerate()
            samples = array.array("h")
            samples.frombytes(w.readframes(w.getnframes()))
    step = max(1, int(rate * win))
    loud = []
    for i in range(0, max(0, len(samples) - step), step):
        chunk = samples[i : i + step]
        rms = (sum(x * x for x in chunk) / len(chunk)) ** 0.5
        if rms >= threshold:
            loud.append(i / rate)
    if not loud:
        return 0.0, total
    runs, start, prev = [], loud[0], loud[0]
    for t in loud[1:]:
        if t - prev > gap:
            runs.append((start, prev + win))
            start = t
        prev = t
    runs.append((start, prev + win))
    s = max(0.0, runs[0][0] - pad)
    e = min(total, runs[-1][1] + pad)
    if e - s < 1.5:
        return 0.0, total
    return s, e


def encode_segment(src: Path, dst: Path, start: float, end: float, w: int, h: int, fps: int) -> None:
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{max(0.1, end - start):.3f}"]
    if not has_audio(src):
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest"]
    cmd += [
        "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", str(dst),
    ]
    run(cmd)


def stitch(cuts: list[Path], out: Path, trim: bool, threshold: float, pad: float, size: str, fps: int) -> dict:
    w, h = (int(x) for x in size.lower().split("x"))
    report = []
    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, cut in enumerate(cuts):
            s, e = speech_window(cut, threshold, pad) if trim else (0.0, duration(cut))
            part = Path(td) / f"part{i:02d}.mp4"
            encode_segment(cut, part, s, e, w, h, fps)
            parts.append(part)
            report.append({"cut": cut.name, "start": round(s, 3), "end": round(e, 3), "kept_sec": round(e - s, 3)})
        listing = Path(td) / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        out.parent.mkdir(parents=True, exist_ok=True)
        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-map_metadata", "-1", "-movflags", "+faststart", str(out),
        ])
    return {"out": str(out), "duration_sec": round(duration(out), 2), "beats": report}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cuts", nargs="+", type=Path, help="QC-passed cuts in beat order")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--no-trim", action="store_true", help="keep each cut whole")
    ap.add_argument("--threshold", type=float, default=1800.0, help="RMS level counted as speech (16-bit scale)")
    ap.add_argument("--pad", type=float, default=0.15, help="seconds kept before/after speech")
    ap.add_argument("--size", default="720x1280")
    ap.add_argument("--fps", type=int, default=24)
    a = ap.parse_args()
    missing = [str(c) for c in a.cuts if not c.is_file()]
    if missing:
        print("missing cuts: " + ", ".join(missing), file=sys.stderr)
        return 1
    info = stitch(a.cuts, a.out, not a.no_trim, a.threshold, a.pad, a.size, a.fps)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
