# QC note template

One note per clip. Keep it next to the clip as `<stem>.qc.md`; copy the summary into the sidecar `qc_why` and `media_qc.notes` fields.

```
QC <PASS|FAIL> <YYYY-MM-DD HH:MM TZ> - <stem>
Reviewer: <name or role>
Tech: <codec> <WxH> <fps> ~<seconds>s, <size MB>
Summary: <one line, e.g. "single pack, label readable, 2 hands, no model text, #ad burned in">
```

## Checklist (any FAIL fails the clip)

| Check | Result | Note |
|---|---|---|
| No model-generated on-screen text; overlays spelled right | PASS / FAIL | |
| Correct product, pack readable, no morphing | PASS / FAIL | |
| Exactly one product; no duplicate pack | PASS / FAIL | |
| Hands: five fingers, nothing fused or melting | PASS / FAIL | |
| Face stable, no warping, same identity across cuts | PASS / FAIL | |
| Physics: nothing floating or passing through solids | PASS / FAIL | |
| No scene cut / crossfade ghost inside one take | PASS / FAIL | |
| Framing: product not covering face; no blank frames | PASS / FAIL | |
| Audio: matching voice, no long dead air, no clipped words | PASS / FAIL | |
| No price, deal, urgency, invented proof or medical claims | PASS / FAIL | |
| No third-party faces, watermarks, handles, URLs, app UI | PASS / FAIL | |
| `#ad` burned in and readable | PASS / FAIL | |
| Format: 9:16 video (or 2:3 still), target duration and size | PASS / FAIL | |

## FAIL reason codes (use in the summary)

`MODEL_TEXT` `WRONG_SKU` `PACK_MORPH` `DUPLICATE_PACK` `HAND_MELT` `FACE_WARP` `FLOATING_OBJECT` `SCENE_CUT` `CROSSFADE_GHOST` `NEAR_LENS` `DEAD_AIR` `CLAIM` `THIRD_PARTY_MARK` `TECH`

Example FAIL: `QC FAIL 2026-01-01 10:42 UTC - sample_cut_007 - DUPLICATE_PACK: second jar on the counter while one is held at ~8-13s`
