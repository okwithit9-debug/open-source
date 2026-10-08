# Script and hook templates by content type

Fill the brief first, then pick the beat map for the content type.

## Brief (every asset)

```
Product:        <exact name>            ASIN: <B0XXXXXXXX>
Ideal customer: <who pays; age band / situation / role>
Misery:         <pain they want gone>
Miracle:        <end state they want>
Hook family:    problem | curiosity | trust | specific-person   (pick ONE)
Frame:          product (what it uniquely does) | status (ritual, who it is for)
Marketing beat: <story / demo / result that bridges misery -> miracle>
Sales ask:      <one CTA>  ->  <one destination>
Proof:          <only real, checkable proof, or none>
```

## Hook families (structure only; write your own words)

| Family | Use when | Seeds |
|---|---|---|
| Problem | Viewers are not stopping | "If you have ever [annoying thing]...", "Stop [common mistake].", "Nobody tells you this about [X]" |
| Curiosity | Demo pays off by second 6 | "I was not going to post this", "This should not work", "3 things I wish I knew before [X]" |
| Trust | Clicks but no orders | Read a real 3-star review out loud; who it is for and who it is not for |
| Specific person | You know exactly who buys | "If you are 35+ and still dealing with [X]", "For people who have tried everything" |

Category spine that works well for beauty: *shelf full, still guessing* -> *knowing what works for this skin*.

## Beat maps

### Face + before/after still (2:3 still or short loop)
1. Problem-first title (search words, not "Amazon find")
2. Creator face; honest before/after only when fair and representative
3. Small product tile
4. Burned `#ad`; caption carries CTA and tagged link

### Apply-product (~15 s)
1. 0-2 s: problem or product close-up with a 3-5 word title
2. 2-10 s: product in use (open, texture, apply)
3. 10-15 s: result look + "Find it on Amazon / link below"

### Benefits still / idea canvas
1. Title = the miracle in plain words
2. 2-4 outcome lines (what changes for the viewer), not ingredient percentages
3. Face or lifestyle image + product tile, 2:3, neutral background

### Yapper (30-90 s, fully scripted)
1. Hook (one family)
2. Misery + failed solutions ("I tried X, Y, Z")
3. Turn ("I finally stopped guessing")
4. Reveal: product enters because of the story, held or used
5. Mechanism in plain words (one benefit)
6. Optional objection kill ("it is one step", "it is leave-on")
7. One ask

Every sentence must move the sell. Cut waffle; never pad length.

### Persona modular stitch (30-45 s)
| Beat | Length | Job |
|---|---|---|
| Hook | ~5 s | Stop the scroll (misery, curiosity, specific person) |
| Story | ~10 s | Relatable use; problem shown |
| Proof | ~10 s | Honest review energy, who it is for / not for, result |
| CTA | ~5-10 s | One ask + destination |

Mint each beat as its own cut, QC each, then `scripts/stitch.py`.

### Realistic UGC (one room)
- Pick ONE scene: car, bathroom vanity, bedroom mirror, couch, kitchen, closet, walk-and-talk.
- Story cuts: handheld phone look, no product in frame yet.
- Reveal cut: reach, five-finger grip on the correct pack, name + one benefit + ask.
- Trim dead air after the last word on every cut.

## Fail the script if

- More than one product or offer
- Feature dump with no misery/miracle
- No ask, or more than one destination
- Any invented proof, price, deal or urgency
