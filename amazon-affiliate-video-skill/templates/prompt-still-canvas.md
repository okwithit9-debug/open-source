# Prompt shell: idea-canvas still (2:3, 1000x1500)

Three lanes; mix them freely for the same ASIN.

| Lane | Layout | Job |
|---|---|---|
| Face + before/after | Title, persona face, small before/after pair (only if honest), product tile | Problem-first search title |
| Apply-product | Title, persona using the product in frame, product tile | Show it in use |
| Benefits | Title, persona or lifestyle image, 2-4 outcome bullets, product tile | Emotion / end state |

## Face image prompt (example generator: Grok Imagine, image mode)

```
Photoreal portrait of the same person as the attached face reference,
<expression>, soft daylight, neutral cream background, natural skin texture.
No text, no logos, no products in frame.
```

For an apply-product still, add: `holding the attached product exactly as the reference, applying to <area>, five distinct fingers`.

## Compose (do the text yourself)

Compose with any editor or Pillow; never ask the model to write the words.

```
Canvas:   1000x1500, cream/beige/black neutrals
Top:      #ad pill (small, always visible)
Title:    <problem or miracle in search words, max ~8 words>
Subtitle: <who it is for, max ~6 words>
Image:    face / apply / lifestyle
Bullets:  <outcome 1> / <outcome 2> / <outcome 3>      (benefits lane only)
Tile:     product image on white
Footer:   YOUR_BRAND (optional, small)
```

## Rules

- Benefits are outcomes, not ingredient percentages.
- Before/after must be honest and representative; never fabricate a result.
- No prices, deals, ratings badges you cannot back, or Amazon UI.
- Pin destination: `https://www.amazon.com/dp/ASIN?tag=YOUR_TAG-20`.
