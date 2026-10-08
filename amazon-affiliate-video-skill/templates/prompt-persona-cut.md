# Prompt shell: persona video cut (~15 s, 9:16)

Generator-agnostic. Example generator: Grok Imagine (attach the two reference images, paste the filled prompt, choose 9:16 video). Replace every `<...>`.

**Attach:** `face-ref.png` (your consistent creator persona) and `pack-ref.png` (clean product image on a plain background). For story cuts in a realistic-UGC video, attach the face only.

```
PHOTOREAL phone UGC, vertical 9:16, handheld at eye level, natural window light,
visible skin texture, slight camera drift. Not a studio ad.

PERSONA LOCK: the same woman/man as the attached face reference - same face,
hair, and voice in every take. Outfit: <outfit>. Room: <scene>.

SKU LOCK: the attached product image is the ONLY product in frame.
On-pack name exactly "<Brand Product Name>". Physical pack exactly: <shape,
color, cap/lid, size>. Copy the lettering from the reference only; never
garbled, mirrored, doubled or melted. Do not show: <look-alike neighbor
products by name>. One pack only - never a second copy on the counter.

HAND LOCK: every visible hand has exactly five distinct fingers, thumb
opposing, nothing fused or melting into the pack. If a hand would deform,
she rests the pack on the counter and keeps talking.

ACTION: <hold at chest | open and show texture | apply to <area> | reach and
reveal>. <Pack stays closed | pack opens once>.

SPOKEN (to camera, natural delivery, spoken only - never written on screen):
"<line 1 - hook>"
"<line 2 - story or benefit>"
"<line 3 - ask: Find it on Amazon, link below.>"

NO TEXT: zero on-screen text of any kind - no captions, subtitles, numbers,
logos, watermarks, website names or link graphics.

NO: price talk, deals, discounts, urgency, other products, Amazon app or
website UI, crossfades, scene cuts, floating objects.
```

## Per-type action lines

| Type | ACTION line |
|---|---|
| Apply-product | Opens the pack, shows texture on fingertip, applies to <area>, shows result |
| Benefits | Holds pack at chest, counts three outcomes on her fingers while speaking |
| Yapper | Talks to camera like a friend; pack appears only at the reveal line |
| Realistic UGC story | Selfie in <scene>, no product in frame |
| Realistic UGC reveal | Reaches off-frame, grips the correct pack with five fingers, holds it up |

## After generation

1. Download the MP4.
2. QC with `scripts/qc_sheet.py` + `templates/qc-note.md`.
3. Burn `#ad` with `scripts/burn_disclosure.py` (and any title overlay you want).
