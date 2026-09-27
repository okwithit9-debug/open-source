# Household image gen (Mac ComfyUI → Open WebUI)

ChatGPT-style pictures for a few named users **inside the same Open WebUI app**. Upload/describe → image. Style jump: photoreal / anime / illustrated via LoRAs.

Fully local. No cloud image APIs. **Not** Hermes. This folder is the local image path.

---

## Locked split (2026-09-17)

**LOCKED: Mac.** Not awaiting A/B. Rejection record: [`SWAP-VS-MAC.md`](SWAP-VS-MAC.md).

| Box | Job | Forbidden |
|-----|-----|-----------|
| **Spark** | Tabby + Flash EXL3 (always-on chat). FFmpeg **video editing only** (CPU/disk; Tabby stays up). | FLUX, ComfyUI, AI video gen, swap-unload Tabby |
| **Mac** | ComfyUI + FLUX.1-dev FP8 + LoRAs — **image + flyer gen** | Putting FLUX on Spark; pointing household chat at 27B `:8000` |

**Rejected:** Spark swap-on-demand. ~7 GiB headroom vs FLUX 12–16 GiB; ~3–7 min chat outage per gen is unacceptable.

## Coexistence numbers (why the lock)

Measured **2026-09-17** with Tabby serving `Qwen3.8-Flash-Next-Uncensored-exl3-4bpw` on Spark `:8001`:

| Figure | Value |
|--------|--------|
| Tabby python (`nvidia-smi`) | ~70 855 MiB (~69.2 GiB) |
| Host RAM | 121 GiB total, ~114 GiB used |
| `MemAvailable` | **~6.9 GiB** |
| cgroup | ~41.8 GiB / peak ~93.9 GiB |
| FLUX.1-dev FP8 peak | ~12–16 GiB |
| FLUX.1-dev FP16 peak | ~32 GiB |
| FLUX.2 full | ~94 GiB — **never** |
| Video models | **never** |

**6.9 GiB free cannot hold 12–16 GiB FLUX next to 69 GiB Tabby.** Spark UMA has no graceful OOM — co-resident ComfyUI will hang the box and take chat with it.

**Nunchaku** (even if it lands well on GB10 ARM) does not change the call: you still need headroom Tabby does not have, and this stack must **not** unload EXL3.

| Host | Verdict |
|------|---------|
| **Mac ComfyUI (LOCKED)** | Yes. Open WebUI → `http://host.docker.internal:8188`. Chat stays on Spark Tabby `:8001`. |
| Spark + Tabby + FLUX | **No.** OOM/hang. |
| Spark swap-on-demand | **Rejected** (locked). [`SWAP-VS-MAC.md`](SWAP-VS-MAC.md). |
| Spare box, no Tabby | systemd template exists; not needed. |

Do not start EXO. Do not change EXL3 weights or the Tabby unit. Do not stop Mac oMLX 27B from this folder. If the Mac is tight, close browsers — do not unload the 27B backup unless the operator explicitly asks.

---

## What you install (Mac)

| Piece | Path / size |
|-------|-------------|
| ComfyUI | `$HOME/ComfyUI` (git), headless API `127.0.0.1:8188` |
| FLUX.1-dev **FP8** | `Comfy-Org/flux1-dev` → `flux1-dev-fp8.safetensors` **16.1 GiB** (17.2 GB). All-in-one (UNet+CLIP-L+T5+VAE). |
| Photoreal LoRA | `XLabs-AI/flux-RealismLora` `lora.safetensors` → `flux-photoreal.safetensors` **~22 MB** |
| Anime LoRA | `alfredplpl/flux.1-dev-modern-anime-lora` `modern-anime-lora-2.safetensors` → `flux-anime.safetensors` **~146 MB** |
| Illustration LoRA | `alvdansen/frosting_lane_flux` `flux_dev_frostinglane_araminta_k.safetensors` → `flux-illustrated.safetensors` **~165 MB** |

Do **not** download `flux1-dev.safetensors` (FP16 ~23.8 GB), FLUX.2, or any video pack.

Accept the FLUX.1 [dev] non-commercial license: https://huggingface.co/black-forest-labs/FLUX.1-dev

```bash
cd sara-ai/image-gen
cp .env.example .env          # optional HF_TOKEN
./install-mac.sh
./download-models.sh          # ~16.4 GiB disk
./start.sh                    # or launchd/install.sh then kickstart
./smoke-test.sh photoreal
```

Peak while sampling: **~12–16 GiB unified on the Mac**. Spark `nvidia-smi` must stay Tabby-only.

Stop: `./stop.sh` (ComfyUI only).

---

## Mac status (2026-09-17, verified)

ComfyUI 0.3.60 at `~/ComfyUI`, Python 3.12 venv, MPS OK. **Not serving yet.**

| File | Bytes | Check |
| --- | --- | --- |
| `models/checkpoints/flux1-dev-fp8.safetensors` | 17,246,524,772 | sha256 `8e91b68084b53a7fc44ed2a3756d821e355ac1a7b6fe29be760c1db532f3d88a` matches HF |
| `models/loras/flux-photoreal.safetensors` | 22,431,400 | XLabs-AI/flux-RealismLora, header OK |
| `models/loras/flux-anime.safetensors` | 153,272,832 | alfredplpl modern-anime-lora-2, header OK |
| `models/loras/flux-illustrated.safetensors` | 171,969,568 | alvdansen/frosting_lane_flux, header OK |

Next: `./start.sh`, then `./smoke-test.sh photoreal`. Spark, Tabby :8001, Hermes/oMLX untouched.

---

## LoRAs (why these)

| Style | Hugging Face | Local name | Why |
|-------|--------------|------------|-----|
| Photoreal | [`XLabs-AI/flux-RealismLora`](https://huggingface.co/XLabs-AI/flux-RealismLora) | `flux-photoreal.safetensors` | Standard FLUX realism adapter; small; no extra trigger required (workflow still prefixes “photorealistic photograph”). |
| Anime | [`alfredplpl/flux.1-dev-modern-anime-lora`](https://huggingface.co/alfredplpl/flux.1-dev-modern-anime-lora) | `flux-anime.safetensors` | Modern-anime look; trigger **modern anime style** is baked into workflow node 12 so users do not type it. v2 file, ~146 MB (not a 400 MB alvdansen dump). |
| Illustrated | [`alvdansen/frosting_lane_flux`](https://huggingface.co/alvdansen/frosting_lane_flux) | `flux-illustrated.safetensors` | Storybook / indie illustration. Trigger **frstingln illustration** baked into node 12. Distinct weights from the anime LoRA. |

Civitai mirrors exist; this repo uses Hugging Face ids only. Safe prompts still matter — LoRAs are style, not a filter.

UI-loadable copies of the three graphs also exist on the Mac at `~/ComfyUI/user/default/workflows/sara-{photoreal,anime,illustrated}.json` (same LoRAs, triggers, strengths as `workflows/flux-fp8-*.json`) for testing in the ComfyUI browser UI. Open WebUI keeps using `workflows/flux-fp8-*.json` via `switch-style.sh`.

### How users pick a style

Open WebUI has **one** ComfyUI workflow at a time.

1. **Admin device:** `./switch-style.sh photoreal|anime|illustrated` then Admin → Settings → Images → import `workflows/current.json`. Map nodes from `workflows/nodes.json` (prompt=`6`, width/height=`5`, steps/seed=`3`).
2. **Users:** Image button or “draw …” in chat. Saying *photo / anime / storybook* in the prompt helps. The loaded LoRA is the real jump.
3. There is no per-user dropdown in Open WebUI v0.11. The default is whatever the admin last imported. No slash-commands unless you add them later.

Queue = **one image**. Four users wait their turn. Do not raise `batch_size` / `n`.

---

## Open WebUI wiring

Chat stays `OPENAI_API_BASE_URL=http://LLM_LAN_IP:8001/v1`. Generate goes to ComfyUI on the Mac, not Tabby, not `:8000`.

Compose already passes (see parent `.env` + `open-webui-images.env.example`):

| Knob | Value |
|------|--------|
| `ENABLE_IMAGE_GENERATION` | `true` |
| `ENABLE_IMAGE_PROMPT_GENERATION` | `true` (Flash on Tabby rewrites the prompt — ChatGPT-style) |
| `ENABLE_IMAGE_EDIT` | `false` |
| `IMAGE_GENERATION_ENGINE` | `comfyui` |
| `COMFYUI_BASE_URL` | `http://host.docker.internal:8188` |
| `IMAGE_SIZE` | `1024x1024` |
| `IMAGE_STEPS` | `20` |
| `USER_PERMISSIONS_FEATURES_IMAGE_GENERATION` | `true` |

Admin → Settings → Images (after first boot PersistentConfig wins):

1. Engine **ComfyUI**, URL `http://host.docker.internal:8188`
2. Paste / import `workflows/flux-fp8-photoreal.json` (or current style)
3. Node map: prompt `text` → `6`; width/height → `5`; steps/seed → `3`
4. Do **not** set engine to OpenAI / Gemini / Automatic1111

`ENABLE_IMAGE_PROMPT_GENERATION` uses the **chat** model (Tabby). That is extra tokens on the GPU, not a second image model. Keep prompts short so generate does not stall the house chat queue for long.

If Open WebUI and ComfyUI are ever on different machines, Tailscale-serve **8188 to the Open WebUI host only** — never to other phones, never Funnel ComfyUI.

---

## Smoke test

```bash
./start.sh          # other terminal
./smoke-test.sh photoreal
# optional: ./smoke-test.sh anime "a cat on a windowsill"
```

Prompt used by default: *a red bicycle parked under a maple tree, daytime, no people*.

`curl http://127.0.0.1:8188/system_stats` then `POST /prompt` (scripted). Image lands in `$COMFYUI_HOME/output/`. After it is **in chat**, household outputs are purged on a timer (default **24h**, optional 2–6h). Do not keep a flyer library. Spark never stores gens. See [`../RETENTION.md`](../RETENTION.md). Cron not installed yet.

**Pass:** PNG appears; Mac process ~12–16 GiB during sample; `curl -fsS http://LLM_LAN_IP:8001/v1/models` still 200 (read-only check). **Fail:** Spark memory climbs, Tabby hangs — you put ComfyUI on the wrong box. Stop ComfyUI on Spark immediately; do not “free VRAM” by restarting Tabby from here.

This cloud agent does not download weights or run the gen.

---

## Safety / headroom

- Chat = Tabby `:8001`. Generate = Mac ComfyUI. Separate machines, separate RAM.
- Do not run ComfyUI on Spark. Do not unload EXL3. Do not start EXO. No AI video gen (Spark FFmpeg = edit only).
- ComfyUI listens `127.0.0.1` only (`start.sh` refuses `0.0.0.0`).
- On-demand: launchd `RunAtLoad=false`. Start when someone wants pictures; `./stop.sh` when done so the Mac keeps RAM for 27B / Studio work.
- One job in the ComfyUI queue. Vision uploads (webcam) still go to Tabby, not ComfyUI.
- Generated files stay on the Mac volume. No community share, no cloud log.
- No tool/agent access to the home LAN.

---

## Units

| File | Use |
|------|-----|
| `launchd/install.sh` | Mac, optional. KeepAlive/RunAtLoad off. |
| `systemd/sara-ai-comfyui.service` | Spare box **without** Tabby. **Do not enable on Spark.** |

---

## Do not

- Point image gen at Hermes, oMLX `:8000`, or a cloud DALL·E/Gemini key
- Fetch FP16 FLUX.1-dev, FLUX.2, or video checkpoints
- Open ComfyUI `:8188` on the router
- Leave Open WebUI image engine on `openai`

---

## Docker Desktop on the Mac: containers cannot reach the LAN (2026-09-17)

On Docker Desktop, from inside `sara-ai`, **every** LAN address is refused instantly — `LLM_LAN_IP:8001` (Tabby), the router, and the Mac's own private LAN address — while `host.docker.internal` works and image pulls from the internet work. The Mac host itself reaches Tabby fine (`curl http://LLM_LAN_IP:8001/v1/models` → 200). So the compose default `OPENAI_API_BASE_URL=http://LLM_LAN_IP:8001/v1` fails from the container on this box.

Fix in place (no change to Tabby or Spark):

| Piece | What |
|-------|------|
| `mac/tabby-relay.py` | 30-line asyncio TCP relay, **binds 127.0.0.1:8001 only**, forwards to `LLM_LAN_IP:8001`. |
| LaunchAgent `com.example.chat.tabby-relay` | RunAtLoad + KeepAlive. Installed by `mac/install-always-on.sh`. |
| `.env` | `OPENAI_API_BASE_URL=http://host.docker.internal:8001/v1` |

Verify: `docker exec sara-ai python -c "import urllib.request as u; print(u.urlopen('http://host.docker.internal:8001/v1/models').status)"` → `200`.

The relay is not a public port: it listens on the Mac's loopback only, and the container reaches it through Docker's host gateway. Phones never see `:8001`.

## Always-on Mac (LaunchAgents)

`launchd/install.sh` is on-demand (RunAtLoad=false) so 27B keeps RAM until someone wants pictures. This setup keeps the Mac **ready at all times** instead. `mac/install-always-on.sh` installs three LaunchAgents:

| Agent | Starts | KeepAlive |
|-------|--------|-----------|
| `com.example.chat.comfyui` | `image-gen/start.sh` (127.0.0.1:8188) | yes |
| `com.example.chat.tabby-relay` | `mac/tabby-relay.py` (127.0.0.1:8001 → Spark) | yes |
| `com.example.chat.docker-desktop` | `open -g -a Docker`; container `sara-ai` is `restart: unless-stopped` | no |

It also sets Docker Desktop `AutoStart=true` with the dashboard hidden. `./mac/install-always-on.sh remove` unloads all three.

LaunchAgents run at **login**, not at power-on. For unattended reboots, enable automatic login for the Mac user (System Settings → Users & Groups); this is incompatible with FileVault. Spark, Tabby `:8001`, Hermes and oMLX are not touched by any of this.

Verified 2026-09-17 end to end: Open WebUI chat → Flash on Tabby rewrites the prompt → ComfyUI FLUX FP8 photoreal → image in chat (`~/ComfyUI/output/sara-photoreal_00002_.png`).
