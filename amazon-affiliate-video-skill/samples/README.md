# Samples

Example outputs from this pipeline, one per content type. All are AI-generated (synthetic creator persona and scenes), re-encoded to H.264 at up to 720 px wide, with all container metadata stripped. **Audio was removed** from these public copies; your real outputs keep their voice track.

| File | Content type | Description |
|---|---|---|
| [`01-face-before-after-still.mp4`](01-face-before-after-still.mp4) | Face + before/after still | Close-up face goes from dry, textured skin to a smooth finish around a Tatcha moisturizer jar, with a problem-first title and "Shop on Amazon / Link below" closer. |
| [`02-apply-product.mp4`](02-apply-product.mp4) | Apply-product | La Roche-Posay Toleriane Double Repair moisturizer is squeezed out, applied to the face and shown on the counter at the end. |
| [`03-benefits.mp4`](03-benefits.mp4) | Benefits (persona talk) | The persona holds Mario Badescu Drying Lotion at a bathroom vanity and talks through three outcomes rather than ingredients. |
| [`04-yapper-friend-story.mp4`](04-yapper-friend-story.mp4) | Yapper (face-to-camera friend story) | The persona talks to camera on a couch like telling a friend, then picks up the Laneige Lip Sleeping Mask jar for the reveal. |
| [`05-realistic-ugc.mp4`](05-realistic-ugc.mp4) | Realistic UGC | Phone-style selfie clip where the persona crouches by a counter and casually shows EltaMD UV Clear. |

Not included: a persona modular stitch sample. Build one from your own QC-passed cuts with [`../scripts/stitch.py`](../scripts/stitch.py).

Notes:

- The first frame of the Imagine-style clips (03 to 05) is the product reference image the generator was given; that is normal for image-to-video.
- `#ad` is burned in on every clip. Some clips show it twice (one from the template, one burned in afterwards); in your own work keep only the burned-in one.
- Brand names and packaging belong to their owners and appear only to show product fidelity. No endorsement is implied.
