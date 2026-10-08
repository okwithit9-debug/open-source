# Amazon affiliate video skill

An open-source, generator-agnostic workflow for making short-form Amazon affiliate videos and stills that pass a strict QC bar and ship "post-ready" with captions, disclosure and a tagged link.

It is written as an agent **skill** ([`SKILL.md`](SKILL.md)) so an AI agent can follow it step by step, but every step also works by hand.

## What is inside

```
amazon-affiliate-video-skill/
├── SKILL.md                      the full pipeline (agent-readable)
├── README.md                     this file
├── templates/
│   ├── product-pick.md           live trend + purchase scorecard, ASIN proof
│   ├── script-beats.md           brief, hook families, beat maps per content type
│   ├── prompt-persona-cut.md     video prompt shell (persona, SKU, hand, no-text locks)
│   ├── prompt-still-canvas.md    2:3 idea-canvas still lanes
│   ├── caption.md                X / Threads / Pinterest / IG / TikTok caption templates
│   ├── qc-note.md                pass/fail rubric + QC note format
│   └── sidecar.example.json      example post-ready sidecar
├── scripts/
│   ├── config.example.env        placeholders (tracking ID, output folder, handle)
│   ├── asin_check.py             ASIN format check + tagged link
│   ├── qc_sheet.py               contact sheet for visual QC
│   ├── stitch.py                 RMS dead-air trim + concat of modular cuts
│   ├── burn_disclosure.py        burn "#ad" (and an optional title) with ffmpeg
│   ├── lint_copy.py              compliance lint for captions and spoken lines
│   └── package_ready.py          writes mp4 + sidecar JSON + captions + QC note
└── samples/                      one example clip per content type
```

## Requirements

- Python 3.9+ (standard library only)
- `ffmpeg` and `ffprobe` on `PATH`
- An Amazon Associates account and tracking ID
- Any image/video generator that can keep a face and a product consistent (Grok Imagine is used as the example)

## Quick start

```bash
cd amazon-affiliate-video-skill
cp scripts/config.example.env .env      # set AFFILIATE_TAG, READY_DIR, SOCIAL_HANDLE
set -a; . ./.env; set +a

# 1. product + link
python3 scripts/asin_check.py B0XXXXXXXX

# 2-3. write the brief (templates/script-beats.md), fill templates/prompt-persona-cut.md,
#      generate ~15 s 9:16 cuts with your generator

# 4. QC every cut
python3 scripts/qc_sheet.py cut1.mp4

# 5. optional stitch
python3 scripts/stitch.py --out stitched.mp4 cut1.mp4 cut2.mp4 cut3.mp4

# 6. disclosure + package
python3 scripts/burn_disclosure.py stitched.mp4 final.mp4
python3 scripts/package_ready.py --clip final.mp4 --asin B0XXXXXXXX \
  --product "Brand Product Name" --kind persona-stitch --beats hook,story,proof,cta \
  --hook "Still guessing which one works?" --benefit "one plain-words benefit" \
  --qc PASS --qc-notes "single pack, 2 hands, no model text, #ad burned in" \
  --asin-proof "YYYY-MM-DD amazon.com: title matches, in stock"
```

The output folder then holds `<stem>.mp4`, `<stem>.json`, `<stem>.caption.txt` and `<stem>.qc.md`.

## Compliance

This workflow bakes in the basics, but you are responsible for following the FTC endorsement guides, the Amazon Associates Program Operating Agreement and each platform's rules. In short: disclose every post (`#ad` plus "As an Amazon Associate I earn from qualifying purchases."), never state prices, Prime, discounts or urgency, never invent reviews or results, and label AI-generated people where platforms require it. See the compliance section of [`SKILL.md`](SKILL.md).

## License

MIT, same as the rest of this repository. See [`../LICENSE`](../LICENSE).
