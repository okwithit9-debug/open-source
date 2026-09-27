# Spark swap vs Mac ComfyUI — LOCKED

**Locked 2026-09-17.** Operator decision. GitHub is source of truth. No A/B. Do not implement a Spark swap.

Eval was written before the lock (same measurements). This file is now the **rejection record**, not a decision menu.

---

## LOCKED: Mac-ComfyUI

Household **image + flyer gen** = Mac ComfyUI + FLUX.1-dev FP8 + photoreal / anime / illustrated LoRAs.

| Box | Locked job |
|-----|------------|
| **Spark** | Tabby + `Qwen3.8-Flash-Next-Uncensored-exl3-4bpw` (always-on household chat). FFmpeg = **video editing only** (CPU/disk; Tabby stays up). **No AI video gen.** |
| **Mac** | ComfyUI + FLUX FP8 + LoRAs (image / flyer). |

**Rejected: Spark swap-on-demand** (stop Tabby → load FLUX → gen → unload → reload Tabby).

Reasons (user lock):

- Spark headroom **~7 GiB** `MemAvailable` (2026-09-17) vs FLUX FP8 **12–16 GiB**. Cannot co-reside.
- Swap blackout **~3–7 min** chat per gen — **unacceptable** for always-on household chat.

Recipe: [`README.md`](README.md).

---

## Memory (why swap was even on the table)

| Source | Figure |
|--------|--------|
| 2026-09-17 live | Tabby python ~70 855 MiB (~69.2 GiB); host ~114 / 121 GiB used; **MemAvailable ~6.9 GiB**; cgroup ~41.8 / peak **~93.9 GiB** |
| FLUX.1-dev FP8 | ~12–16 GiB peak |
| Pack on disk | EXL3 ~102G, 9 shards + ngram (Tabby weight pack) |

6.9 GiB free < 12–16 GiB FLUX. Co-reside = UMA hang. FFmpeg edits do not touch GPU — Tabby stays.

---

## Load / unload estimates (historical — do not build)

No live stop/start was run. Estimates from unit files, pack size, typical NVMe/EXL3/ComfyUI:

| Step | Estimate | Basis |
|------|----------|--------|
| Stop Tabby + UMA reclaim | **15–60 s** | ~70 GiB UMA resident; cgroup peaked ~94 GiB |
| ComfyUI + FLUX FP8 load | **30–90 s** | 16.1 GiB checkpoint |
| One 1024² / 20-step gen | **10–40 s** | GB10, batch 1 |
| Unload ComfyUI + reclaim | **10–40 s** | |
| Tabby cold reload | **2–5 min** | ~102G pack; `TimeoutStartSec=300` on `tabby-exl3-8001.service` |
| **Chat blackout per job** | **~3–7 min** (user lock); up to ~10 min cold | Why swap is rejected |

“Free KV only” does not free 12–16 GiB.

---

## Failure modes (why swap stays rejected)

1. UMA hard hang if Tabby and FLUX overlap  
2. Tabby does not come back  
3. systemd `Restart=on-failure` race  
4. Open WebUI 300 s timeout vs multi-minute swap  
5. Chat + Hermes `:8001` 502 during gen  
6. Zombie CUDA after `stop`  
7. Orchestrator crash mid-swap  

Mac path: images fail if ComfyUI is down; **chat stays up**.

---

## Do not implement

The old “if a Spark swap is chosen” script list is **void**. No swap-lock, no `systemctl stop tabby-exl3-8001` from this folder, no ComfyUI on Spark.

FFmpeg on Spark remains editing-only and is not a swap trigger.
