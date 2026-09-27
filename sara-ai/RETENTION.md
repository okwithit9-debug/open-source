# Sara AI — locked retention / anti-clutter

**Locked 2026-09-17.** Product **Sara AI**.

Phones are **not** the store. The Open WebUI **server** (DB + volume + host disk next to it) is. No other Sara AI process requires long-lived personal chat or media retention.

This file is the rule. **No live deletes from this doc.** Guardrails belong in the Open WebUI purge job, Clipper skill, and image-gen runbook **before** those pipelines go live.

---

## Locked policy

| What | Retention |
|------|-----------|
| **Chat history** (Open WebUI server DB / `open-webui-data` volume: threads, messages, embedded blobs) | Auto-purge after **7 days**. |
| **Spark clips** (FFmpeg / yt-dlp / whisper outputs + scratch + transcripts) | Delete **immediately after chat delivery** (user can view/download). Wipe temps on success and after a logged failure. |
| **Mac ComfyUI household image/flyer outputs** (`output/` + staging) | Purge after **24 hours** (or sooner: optional 2–6h). Spark is never an image store. |
| **Vision uploads** sitting as disk artifacts | Same as chat/media clutter: not a library. Do not copy uploaded photos to Spark. Follow the security README while they exist. |

Spark and Mac must **not** grow a media library.

---

## Exempt from purge (do not delete in the purge job)

Explicit durable docs only:

- User-pinned notes (explicit pin only)

Not chat clutter — **leave these alone**:

- Model weights (Tabby / Spark EXL3, Mac FLUX + LoRAs, Hermes 27B oMLX)
- Open WebUI **install** (image, compose, named volume *structure*, first-boot admin settings)
- Tailscale / Cloudflare / ACL configs
- Named **accounts** (logins, passwords, roles)

The purge job deletes **old chats and generated/uploaded media**, not the product.

---

## Clipper (Spark — planned skill)

Clips are requested **via Sara AI** (Open WebUI skill). FFmpeg / yt-dlp / whisper stay CPU/disk; Tabby stays up. No AI video gen.

| When | What to delete on Spark |
|------|-------------------------|
| Clip **successfully delivered in chat** (user can view/download) | Clip files + scratch + transcripts **immediately**. No “keep a copy just in case.” |
| Job **fails** (after a log line) | Same temps: FFmpeg/yt-dlp/whisper work dirs. |

- **No long-term clip archive on Spark.** If someone wants to keep it, they download from chat before the 7-day history purge. Spark = ephemeral workspace only.
- A separate posting library is a **different** lane. Skill output must not pile up next to Tabby.

Do not implement the skill until it includes this wipe.

---

## Image / flyer gen (Mac ComfyUI)

Images are **not** stored on Spark. Ever.

After the image is **delivered to the chat recipient**, auto-delete household files from ComfyUI `output/` and any staging:

| Knob | Lock |
|------|------|
| Default | **24h** after successful delivery |
| Optional | Shorter **2–6h** (the operator sets this in the cron / script env) |
| Spark | Must never receive or keep gens |

Do not accumulate flyer/image clutter on the Mac. Weights and LoRAs stay; **outputs** go.

Cron / launchd timer is **planned** (roadmap). Not installed from this PR.

---

## Chat history (Open WebUI server)

- Store: server SQLite / `open-webui-data` volume. **Not** the phones.
- Age-out: threads and message blobs **older than 7 days** are purged. That includes embedded copies of clips, images, and vision uploads that rode along in the chat.
- A download on a phone does not become the archive. If someone needs something durable, pin a note outside the chat database.
- Accounts and the Open WebUI install survive the job. Recreating the volume from scratch is **not** the 7-day purge (that would wipe logins).

The 7-day chat purge job is **planned**. Not installed from this PR.

---

## General

- Purge jobs cover: Open WebUI chat rows (7d), Spark clip/temps (immediate after delivery), Mac `output/` + staging (24h or sooner).
- Photos uploaded for **vision** are still sensitive (local Tabby only). They are not a ComfyUI library and they are not exempt.
- No other Sara AI process (Voice later, Clipper, image gen) gets a longer personal chat/media keep.
