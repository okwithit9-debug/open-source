---
name: amazon-affiliate-video-skill
description: >-
  Use when producing short-form Amazon affiliate videos and stills end to end:
  pick a trending product with a verified ASIN, write a hook and script for one
  content type, generate cuts with any image/video generator (Grok Imagine is
  one example), QC every cut against a pass/fail rubric, optionally stitch cuts
  with ffmpeg, and package a post-ready clip with caption, disclosure, tagged
  link and QC note. Generator-agnostic and compliance-first.
---
# Amazon affiliate video skill

A repeatable pipeline for making short (about 15 to 90 seconds) vertical videos and 2:3 stills that send viewers to one Amazon product page through your own Associates tracking ID.

Every finished asset ships as a pair:

```
<stem>.mp4    the QC-passed clip (9:16, no audio issues, disclosure burned in)
<stem>.json   the sidecar: product, ASIN, tagged link, captions, QC note
```

Never hand off a bare video. The sidecar is what makes a clip "post-ready".

Placeholders used throughout: `YOUR_TAG-20` (Associates tracking ID), `YOUR_BRAND`, `YOUR_HANDLE`, `/path/to/ready` (output folder), `http://localhost:PORT` (any local service you run). Copy [`scripts/config.example.env`](scripts/config.example.env) to `.env` and fill it in.

---

## Pipeline at a glance

| Step | Output | Tooling |
|---|---|---|
| 1. Product pick | One ASIN verified live today, with a scorecard | [`templates/product-pick.md`](templates/product-pick.md), [`scripts/asin_check.py`](scripts/asin_check.py) |
| 2. Script and hook | Beat sheet for one content type, one hook family | [`templates/script-beats.md`](templates/script-beats.md) |
| 3. Generate | Stills and/or 9:16 cuts | Any generator; prompt shells in [`templates/prompt-persona-cut.md`](templates/prompt-persona-cut.md), [`templates/prompt-still-canvas.md`](templates/prompt-still-canvas.md) |
| 4. QC | PASS/FAIL per cut, written reason | [`templates/qc-note.md`](templates/qc-note.md), [`scripts/qc_sheet.py`](scripts/qc_sheet.py) |
| 5. Stitch (optional) | One longer piece from several PASS cuts | [`scripts/stitch.py`](scripts/stitch.py) |
| 6. Package | `<stem>.mp4` + `<stem>.json` + caption text + QC note | [`scripts/burn_disclosure.py`](scripts/burn_disclosure.py), [`scripts/package_ready.py`](scripts/package_ready.py) |
| 7. Compliance | Lint pass on every caption and spoken line | [`scripts/lint_copy.py`](scripts/lint_copy.py), section below |

---

## 1. Trending product pick (live data only)

- Pull trend signals **today**: short-video virals (including TikTok Shop products), Amazon Best Sellers / Movers and Shakers for the category, and one cross-check (Pinterest search, Google Trends, review velocity).
- A viral product only counts if the **same product** has a live Amazon US listing today. No ASIN, out of stock, or wrong variant means WATCH, not KEEP.
- Score it with [`templates/product-pick.md`](templates/product-pick.md) (8 rows, 0 to 2 each, keep at 10 or more with at least one strong trend row and one strong purchase row).
- Verify the ASIN on amazon.com the day you make content: title matches the exact pack you will show, in stock, correct size/shade. Record a one-line proof (date, title, rating count) in the sidecar `asin_live_proof` field.
- Never pick from memory or yesterday's list. Never invent ASINs, ranks, review counts or commission rates. If live data is unreachable, write `NEED_LIVE_DATA` and stop.

`python3 scripts/asin_check.py B0XXXXXXXX` validates the format and prints the clean product URL plus your tagged link.

## 2. Script and hook by content type

Write the script **before** touching a generator. Every asset gets:

- **One** product, **one** ideal customer, **one** misery (pain) and **one** miracle (end state).
- **One** hook family matched to why the viewer has not bought yet: *has the problem*, *needs to be curious*, *does not trust you yet*, or *one specific person*.
- A marketing beat (story, demo, result) **then** a sales ask (one CTA, one destination).
- Payoff or problem shown by about second 6.

Content types in the rotation (beat maps in [`templates/script-beats.md`](templates/script-beats.md)):

| Type | Shape |
|---|---|
| Face + before/after still | Creator face, problem-first title, honest before/after when fair |
| Apply-product | The product in use on camera (texture, apply, result) |
| Benefits still / idea canvas | 2 to 4 outcome lines (benefits, not ingredient dumps) on a 2:3 canvas |
| Yapper | 30 to 90 s face-to-camera friend story; fully scripted, feels unscripted |
| Persona modular stitch | Hook ~5 s, story ~10 s, proof ~10 s, CTA ~5 to 10 s, minted as separate cuts and stitched |
| Realistic UGC | Phone-real scene (car, vanity, couch...), story first, product revealed because of the story |

## 3. Image and video generation (generator-agnostic)

Any text/image-to-video model works if it can hold a face and a product across cuts. Grok Imagine is used here as one example: attach a clean product reference image and a persona face reference, paste the filled prompt shell, generate a ~15 s 9:16 clip, download the MP4.

Rules that apply to every generator:

- **Consistent creator persona.** One locked face reference and one voice across every cut of a video and across a channel. Do not imitate a real person. Disclose AI-generated people where the platform requires it.
- **Product fidelity.** Attach the real pack image; name the exact on-pack wording; ban look-alike neighbor products by name.
- **Hands.** Five distinct fingers, thumb opposing, nothing fused or melting into the pack. If hands will fail, have the pack stand on the counter instead.
- **No generated text.** Ask for zero on-screen text from the model. Add titles and the `#ad` disclosure afterwards with ffmpeg so spelling is always right.
- **Spoken lines are scripted.** Put exact spoken lines in the prompt. Keep the spoken CTA generic ("link below") unless you own the destination.
- **Stills:** compose 2:3 (1000x1500) idea canvases in an image editor or with Pillow from a PASS frame or a generated face still. Keep a ratio of roughly two stills per video in a still-heavy channel such as Pinterest.

## 4. QC rubric (pass/fail, written reason every time)

Make a contact sheet (`python3 scripts/qc_sheet.py clip.mp4`) and watch the clip once at full speed with sound. Any single FAIL fails the cut.

| Check | FAIL when |
|---|---|
| On-screen text | Any model-generated caption, word, number or garbled lettering; misspelled overlay |
| Product fidelity | Wrong SKU, wrong size/shade, unreadable brand, pack morphs, opens when it should not |
| Single product | A duplicate pack appears, or a second product competes |
| Hands and faces | Extra/missing/fused fingers, melting hands, warped or drifting face, identity change between cuts |
| Physics | Floating objects, product passing through a closed cap, teleporting props |
| Continuity | Hard scene cut or crossfade ghost inside one take; outfit or room drift across stitched cuts |
| Framing | Product pushed into the lens covering the face; headless frames; black or blank frames |
| Audio | Mismatched voice, multi-second dead air after the last word, clipped words |
| Claims | Price, deal, Prime, % off, urgency, invented results or medical claims (spoken or written) |
| Third-party marks | Another creator's face, watermark, handle or URL; Amazon UI screenshots unless you own them |
| Tech | Not 9:16 (video) or 2:3 (still); wrong duration; file too large for the target platform |

Fail one cut, regenerate that cut only. Write the result with [`templates/qc-note.md`](templates/qc-note.md). Only PASS cuts move on.

## 5. Optional modular stitch

When the sell needs more than one ~15 s cut:

```bash
python3 scripts/stitch.py --out /path/to/ready/stem.mp4 hook.mp4 story.mp4 proof.mp4 cta.mp4
```

`stitch.py` finds the speech window in each cut with an RMS loudness scan (room-tone tails count as silence), trims the dead air, normalizes to 720x1280, and concatenates. QC each cut first, then QC the stitched piece as one video. Same face, voice, outfit and room across all beats; one product per stitch.

## 6. Post-ready packaging

1. Burn the disclosure: `python3 scripts/burn_disclosure.py in.mp4 out.mp4` (top-left `#ad`, readable size, never covered by the product).
2. Package:

```bash
python3 scripts/package_ready.py \
  --clip out.mp4 --asin B0XXXXXXXX --product "Brand Product Name" \
  --kind yapper --hook "Your hook line" --benefit "One plain-words benefit" \
  --qc PASS --qc-notes "single pack, 2 hands, no model text, #ad burned in" \
  --asin-proof "2026-01-01 amazon.com: title matches, in stock"
```

This writes into `READY_DIR` (default `/path/to/ready`):

- `<stem>.mp4` (copied)
- `<stem>.json` sidecar (product, ASIN, `https://www.amazon.com/dp/ASIN?tag=YOUR_TAG-20`, per-platform captions, QC block)
- `<stem>.caption.txt` (human-readable captions)
- `<stem>.qc.md` (QC PASS note)

It refuses to package a FAIL, a malformed ASIN, or captions that fail the compliance lint.

## 7. Compliance notes

- **Disclosure (FTC):** `#ad` at the start of every caption and burned into every video/still, plus the Associates statement: *"As an Amazon Associate I earn from qualifying purchases."* Disclosure must be visible without tapping "more".
- **No price or deal claims:** no prices, "Prime", "% off", "deal", "sale", "today only", "limited time", "lowest price", countdown urgency. Amazon prices change and Associates rules restrict showing them outside approved tools.
- **Honest proof only:** no invented reviews, ratings, user counts, before/after results or clinical claims. No medical or diagnostic claims.
- **Links:** use the full tagged product link. Do not cloak or redirect Amazon links in a way that hides the destination, and do not put affiliate links in email, PDFs or offline media.
- **Trademarks:** do not imply endorsement by Amazon or the brand. No Amazon logos as your branding.
- **AI labels:** follow each platform's AI-generated content labeling rules for synthetic people and voices.
- **Platform rules:** some networks restrict direct affiliate links in captions; route those through your own landing page (`https://YOUR_SITE.example`) or the bio link, and keep the same disclosure.

`python3 scripts/lint_copy.py caption.txt` flags banned claims and a missing disclosure.

---

## Done when

The product's ASIN was verified today, the clip passed every QC row with a written note, the disclosure is burned in and in every caption, the sidecar carries the tagged link and the QC note, and the lint is clean.
