#!/usr/bin/env python3
"""Package a QC-passed clip as post-ready: mp4 + sidecar JSON + captions + QC note.

    python3 package_ready.py --clip out.mp4 --asin B0XXXXXXXX \
        --product "Brand Product Name" --kind yapper \
        --hook "Your hook line" --benefit "one plain-words benefit" \
        --qc PASS --qc-notes "single pack, 2 hands, no model text, #ad burned in" \
        --asin-proof "2026-01-01 amazon.com: title matches, in stock"

Writes into --out-dir (default: $READY_DIR, else ./ready):
  <stem>.mp4          copy of the clip (never overwrites unless --force)
  <stem>.json         sidecar with tagged link, captions, QC block
  <stem>.caption.txt  captions for humans
  <stem>.qc.md        QC note

Refuses to package when QC is not PASS, the ASIN is malformed, the live proof
is missing, or any caption fails lint_copy.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asin_check import is_valid_asin, tagged_url  # noqa: E402
from lint_copy import lint  # noqa: E402

DISCLOSURE = "#ad \u00b7 As an Amazon Associate I earn from qualifying purchases."


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def probe(path: Path) -> dict:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", str(path)],
            text=True,
        )
        d = json.loads(out)
        s = (d.get("streams") or [{}])[0]
        return {
            "width": s.get("width"),
            "height": s.get("height"),
            "duration_sec": round(float(d.get("format", {}).get("duration", 0)), 2),
        }
    except Exception:
        return {"width": None, "height": None, "duration_sec": None}


def build_captions(product: str, hook: str, benefit: str, link: str, handle: str, hashtags: str) -> dict:
    tags = (" " + hashtags.strip()) if hashtags.strip() else ""
    return {
        "x": {"caption": f"#ad {hook} {product} - {benefit}.\nFind it on Amazon: {link}{tags}"},
        "threads": {"caption": f"{hook}\n{product} - {benefit}. Find it on Amazon.\n{DISCLOSURE}\n{link}"},
        "pinterest": {
            "title": f"{hook} - {product}"[:100],
            "description": f"{benefit[:1].upper() + benefit[1:]}. Find it on Amazon. {DISCLOSURE}{tags}",
            "link": link,
        },
        "instagram": {"caption": f"#ad {hook}\n{product} - {benefit}.\nLink in bio ({handle}). As an Amazon Associate I earn from qualifying purchases.{tags}"},
        "tiktok": {"caption": f"#ad {hook}\n{product} - {benefit}.\nLink in bio ({handle}). As an Amazon Associate I earn from qualifying purchases.{tags}"},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip", required=True, type=Path)
    ap.add_argument("--asin", required=True)
    ap.add_argument("--product", required=True, help="exact product name as on the listing")
    ap.add_argument("--kind", required=True,
                    choices=["face-before-after-still", "apply-product", "benefits", "yapper",
                             "persona-stitch", "realistic-ugc", "other"])
    ap.add_argument("--hook", required=True)
    ap.add_argument("--benefit", required=True)
    ap.add_argument("--hook-family", default="problem", choices=["problem", "curiosity", "trust", "specific-person"])
    ap.add_argument("--hashtags", default="")
    ap.add_argument("--beats", default="", help="comma list for stitched pieces, e.g. hook,story,proof,cta")
    ap.add_argument("--qc", required=True, choices=["PASS", "FAIL"])
    ap.add_argument("--qc-notes", required=True)
    ap.add_argument("--reviewer", default=os.environ.get("QC_REVIEWER", "YOUR_NAME"))
    ap.add_argument("--asin-proof", required=True, help="one line: date + what you verified on the live page")
    ap.add_argument("--tag", default=os.environ.get("AFFILIATE_TAG", "YOUR_TAG-20"))
    ap.add_argument("--handle", default=os.environ.get("SOCIAL_HANDLE", "YOUR_HANDLE"))
    ap.add_argument("--out-dir", type=Path, default=Path(os.environ.get("READY_DIR", "ready")))
    ap.add_argument("--stem")
    ap.add_argument("--force", action="store_true", help="overwrite existing files with the same stem")
    a = ap.parse_args()

    errors = []
    if a.qc != "PASS":
        errors.append("QC is not PASS - fix or regenerate the cut first")
    if not a.clip.is_file():
        errors.append(f"clip not found: {a.clip}")
    asin = a.asin.strip().upper()
    if not is_valid_asin(asin):
        errors.append(f"malformed ASIN: {a.asin}")
    if not a.asin_proof.strip():
        errors.append("missing --asin-proof (verify the live product page today)")
    if errors:
        for e in errors:
            print("ERROR:", e, file=sys.stderr)
        return 1

    link = tagged_url(asin, a.tag)
    captions = build_captions(a.product, a.hook.strip(), a.benefit.strip().rstrip("."), link, a.handle, a.hashtags)
    lint_errors = []
    for platform, block in captions.items():
        text = "\n".join(str(v) for v in block.values())
        for issue in lint(text, require_disclosure=True, expected_tag=a.tag):
            if not issue.startswith("warning:"):
                lint_errors.append(f"{platform}: {issue}")
    if lint_errors:
        for e in lint_errors:
            print("LINT:", e, file=sys.stderr)
        return 1

    now = dt.datetime.now().astimezone()
    stem = a.stem or f"{now:%Y-%m-%d}_{slugify(a.product)}_{a.kind}"
    out = a.out_dir
    out.mkdir(parents=True, exist_ok=True)
    targets = {ext: out / f"{stem}{ext}" for ext in (".mp4", ".json", ".caption.txt", ".qc.md")}
    clash = [str(p) for p in targets.values() if p.exists()]
    if clash and not a.force:
        print("ERROR: would overwrite (use --force or --stem): " + ", ".join(clash), file=sys.stderr)
        return 1

    info = probe(a.clip)
    qc_line = f"QC PASS {now:%Y-%m-%d %H:%M %Z} - {stem}. {a.qc_notes}"
    beats = [b.strip() for b in a.beats.split(",") if b.strip()]
    sidecar = {
        "stem": stem,
        "file": targets[".mp4"].name,
        "product": {"slug": slugify(a.product), "asin": asin, "name": a.product},
        "kind": a.kind,
        "persona_stitch": a.kind == "persona-stitch",
        "beats": beats,
        "hook_family": a.hook_family,
        "affiliate_url": link,
        "link_status": "VERIFIED",
        "asin_live_proof": a.asin_proof.strip(),
        "duration_sec": info["duration_sec"],
        "resolution": f"{info['width']}x{info['height']}" if info["width"] else None,
        "aspect": "9:16" if info["width"] and info["height"] and abs(info["width"] / info["height"] - 9 / 16) < 0.02 else None,
        "qc": "PASS",
        "qc_why": qc_line,
        "media_qc": {"status": "PASS", "reviewer": a.reviewer, "at": now.isoformat(timespec="seconds"), "notes": a.qc_notes},
        "disclosure": DISCLOSURE,
        "captions": captions,
    }

    shutil.copy2(a.clip, targets[".mp4"])
    targets[".json"].write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    cap_lines = []
    for platform, block in captions.items():
        cap_lines.append(f"## {platform}")
        for k, v in block.items():
            cap_lines.append(f"{k}: {v}")
        cap_lines.append("")
    targets[".caption.txt"].write_text("\n".join(cap_lines), encoding="utf-8")
    targets[".qc.md"].write_text(
        f"# {qc_line}\n\nReviewer: {a.reviewer}\n"
        f"Tech: {sidecar['resolution']} ~{info['duration_sec']}s\n"
        f"Product: {a.product} ({asin})\nLink: {link}\nASIN proof: {a.asin_proof.strip()}\n",
        encoding="utf-8",
    )
    for p in targets.values():
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
