# Sara AI

This `sara-ai/` folder is the Sara AI stack: configs and docs for local chat and image generation.

**Sara AI** is a private, installable chat for a few users talking to an already-live Spark Tabby model from phones. Quick chat only — no computer-use, no agent loops, no extra GPU, no cloud subscription.

This folder is **configs + docs**. Compose starts Open WebUI. It does **not** start, stop, rebuild, or reconfigure the model.

Agent / computer-use stacks stay out of this folder. Sara AI is a separate front door to the Tabby endpoint. Do not merge them.

---

## What you get

| Piece | Role |
|-------|------|
| Open WebUI (`docker-compose.yml`) | Named logins, per-user history, PWA, image + webcam. Proxies to Tabby. |
| Tailscale Serve (preferred) | HTTPS on a private tailnet. No raw public port. ACL: named users → UI only. |
| Cloudflare Tunnel (alt) | HTTPS URL + **required** Access email allowlist if some users should not run Tailscale. |
| Tabby on Spark `:8001` | Already serving Flash. **Do not touch.** |
| ComfyUI on the **Mac** | FLUX.1-dev FP8 + LoRAs. Not on Spark. See [`image-gen/`](image-gen/). |

```
phones (Tailscale or Cloudflare)
        │  HTTPS
        ▼
Open WebUI  (a Mac or an always-on LAN box, 127.0.0.1:8080)
        │  OpenAI-compatible
        ▼
Spark Tabby  http://LLM_LAN_IP:8001/v1
        model: Qwen3.8-Flash-Next-Uncensored-exl3-4bpw
```

Mac **oMLX 27B** on `:8000` is the Hermes backup (weights may still be downloading). **Do not point this app at it.**

**Roadmap:** Locked split: Spark = Tabby chat + FFmpeg edit (no AI video, no EXO); Mac = ComfyUI/FLUX image+flyer; swap rejected. **Next (not built):** Clipper skill on Spark **plus retention**; Mac image purge cron; **7-day** Open WebUI chat-history purge. **Later (phase):** **Voice** — STT/TTS in Sara AI chat; optional Mac voice LoRA TBD; not on Spark next to Tabby; after image gen + Clipper skill. Disk + chat rules: [`RETENTION.md`](RETENTION.md). Do not ship Clipper or live FLUX without those wipes.

---

## Do not touch (hard)

From this folder, from compose, and from any Sara AI helper:

- The agent runtime config
- Tabby model weights
- The Tabby systemd unit
- Mac 27B oMLX LaunchAgent / `:8000`
- Do not start EXO
- Do not download another model
- Do not rebuild Tabby or the EXL3 serve
- Do not run agentic or computer-use tests through this UI
- Do not put real secrets in git — `.env` only, from `.env.example`

If Tabby is down, fix it from the Hermes/Spark handoff — not from here.

---

## Model (already live)

| | |
|---|---|
| Endpoint | `http://LLM_LAN_IP:8001/v1` |
| Model id | `Qwen3.8-Flash-Next-Uncensored-exl3-4bpw` |
| Decode | ~50 tok/s text |
| Context | 64k, KV Q4, MTP k=3 |
| Vision | Weights present. Image upload + webcam **on**. Vision decode is slower — about **half** tok/s (~25). Long photo chats will feel that. |

Compose reads `OPENAI_API_BASE_URL` so a different always-on box on the LAN can still reach `LLM_LAN_IP`.

---

## 1. LAN: Open WebUI on Mac (or a small always-on box)

Needs Docker Desktop (Mac) or Docker Engine (Linux). The host must reach Spark on the 10G / `LLM_LAN_IP` LAN.

```bash
cd sara-ai
cp .env.example .env
# set WEBUI_SECRET_KEY (python3 -c "import secrets; print(secrets.token_hex(32))")
docker compose up -d
```

Local check (this host only):

```bash
curl -fsS http://127.0.0.1:8080/health
# From the same host, confirm Tabby is still the live one — read-only:
curl -fsS http://LLM_LAN_IP:8001/v1/models
```

Open `http://127.0.0.1:8080`. First signup is the admin account (or set `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD` in `.env` before first boot).

`WEBUI_BIND` defaults to `127.0.0.1`. Do not set it to `0.0.0.0`. **Never** publish Tabby `:8001` itself. **Never** open `:8001` on the router.

After the first admin exists, set `ENABLE_SIGNUP=false` in `.env` and `docker compose up -d` so strangers cannot mint accounts. Leave signup off.

---

## 2. Create 3–4 named users

Admin → **Users**. Create one login per person. Example:

| Name | Role |
|------|------|
| House admin | Admin |
| User | User |
| User | User |
| User | User |

Each login keeps its own chat history on the **server** (7-day auto-purge — [`RETENTION.md`](RETENTION.md)). **Strong unique password per person.** Do not share the admin login. Extra accounts stay **user**, not admin. Admin stays on the admin's devices only.

Optional: Admin → Groups if you later want a restricted group with the same chat-only permissions. Compose turns off tools, code interpreter, web search, and workspace skills. **File / image upload** and **local ComfyUI generate** stay on for users.

Disable leftover workspace toys in Admin → Settings if a later Open WebUI version grows a new toggle. Household use is **chat + photos only**.

---

## 3. Image upload + webcam (vision)

Flash reasons about pictures. Vision is the same model, slower (~half tok/s).

1. In a chat, pick the Flash model (`Qwen3.8-Flash-Next-Uncensored-exl3-4bpw`).
2. Attachment **+** → upload a photo **or** camera / webcam.
3. Ask about the picture. Keep it one image (or a few) per turn so the GPU stays usable for everyone.

Uploads are capped in compose (`RAG_FILE_MAX_SIZE=8` MB, 4 files, `jpg/jpeg/png/webp`). Images go to **local Tabby only** — no third-party logging, no analytics, no public share links. Uploaded photos are still sensitive while they exist; they follow the 7-day chat / artifact purge ([`RETENTION.md`](RETENTION.md)). Phones are not the store.

Phones: the camera control needs **HTTPS** (step 4). `http://127.0.0.1` on the Mac is fine for a laptop webcam test.

iOS: after you Add to Home Screen, grant Camera when asked.

---

## 3b. Image generation (Mac ComfyUI — not Spark)

ChatGPT-style **generate** for the same four accounts. Describe (or upload + “draw this as …”) → one local picture. Style jump: photoreal / anime / illustrated.

**LOCKED (2026-09-17): Mac-hosted image + flyer gen.** Spark keeps Tabby up for chat. FFmpeg on Spark is **video editing only** (CPU/disk; no AI video gen). Spark swap-on-demand is **rejected** (~7 GiB headroom vs FLUX 12–16 GiB; ~3–7 min chat outage per gen). Map §3 + [`image-gen/README.md`](image-gen/README.md). Rejection record: [`image-gen/SWAP-VS-MAC.md`](image-gen/SWAP-VS-MAC.md).

```bash
cd sara-ai/image-gen
./install-mac.sh && ./download-models.sh   # ~16.4 GiB disk on the Mac
./start.sh                                 # 127.0.0.1:8188
./smoke-test.sh photoreal
```

Open WebUI (this compose) already points `COMFYUI_BASE_URL` at `http://host.docker.internal:8188`. Parent (admin): Admin → Settings → Images → engine ComfyUI → import `image-gen/workflows/flux-fp8-photoreal.json` → map nodes from `image-gen/workflows/nodes.json`.

| Style | Hugging Face | How the house selects it |
|-------|----------------|--------------------------|
| Photoreal | `XLabs-AI/flux-RealismLora` | `./image-gen/switch-style.sh photoreal` then re-import workflow |
| Anime | `alfredplpl/flux.1-dev-modern-anime-lora` | `./image-gen/switch-style.sh anime` |
| Illustrated | `alvdansen/frosting_lane_flux` | `./image-gen/switch-style.sh illustrated` |

Extra accounts stay **users**. They type photo / anime / storybook in the prompt; the loaded LoRA is the real jump. One image at a time — a few users share the Mac queue. Chat tokens still hit Tabby; generate does not load FLUX on Spark.

This does **not** un-retire enterprise ComfyUI (Imagine / TPT). Household only.

---

## 4. Remote access (pick one)

No raw public port on Tabby. Open WebUI stays on localhost; a front door does HTTPS.

### A. Tailscale Serve — preferred

Private to the tailnet. Each person installs Tailscale, then opens one HTTPS URL.

1. Create a tailnet (one admin account is enough). Enable **MagicDNS** and **HTTPS certificates** (admin console → DNS).
2. Install Tailscale on the Mac / always-on box that runs compose. Sign in.
3. With Open WebUI up:

   ```bash
   ./tailscale/serve.sh
   # or: tailscale serve --bg 8080
   ```

4. Note the HTTPS URL Serve prints. Put it in `.env` as `WEBUI_URL` and recreate: `docker compose up -d`.
5. **Each person:**
   - Install Tailscale (iOS / Android / their laptop).
   - Accept the invite / sign in to the same tailnet.
   - Open that HTTPS URL.
   - Log into Open WebUI with **their** named account.
6. **Install as an app (PWA):**
   - iPhone / iPad: Safari → Share → **Add to Home Screen**.
   - Android: Chrome → menu → **Install app** / Add to Home screen.
   - HTTPS from Serve is what makes the install prompt real.

ACL tip: if some users should only reach this service, tag the Open WebUI node and grant those users that tag only. Serve still follows tailnet ACLs.

`tailscale funnel` puts the same port on the public internet. This repo refuses Funnel unless you export `SARA_AI_ALLOW_FUNNEL=1`. Use Serve.

ACL: paste `tailscale/acl.hujson.example` into the tailnet ACL. Tag the Open WebUI host `tag:sara-ai`. Grant those users **443 only** to that tag. Do **not** advertise the LLM LAN as a subnet — phones must not get a path to Tabby.

Compose profile `tailscale` is optional for a headless box that is not already running the Tailscale app. Host Serve is simpler on the Mac. Sidecar needs `TS_AUTHKEY` in `.env` (reusable, tagged; store in a password manager).

### B. Cloudflare Tunnel — if phones should not run Tailscale

1. Cloudflare Zero Trust → Networks → Tunnels → create a tunnel.
2. **Host `cloudflared`** (simplest on the Mac): point ingress at `http://127.0.0.1:8080`. See `cloudflare/config.yml.example`.
3. **Or** `docker compose --profile cloudflare up -d` with `CLOUDFLARE_TUNNEL_TOKEN` in `.env`. In the dashboard, the public hostname’s service must be `http://open-webui:8080` (compose DNS), not Tabby.
4. Add a public hostname (`chat.example.com`).
5. **Cloudflare Access is required** (not optional): email allowlist for the named user addresses only. See `cloudflare/access-policy.md`. Do not leave the hostname open.
6. Set `WEBUI_URL` to `https://chat.example.com` and recreate compose.
7. Each person opens the URL → Access → Open WebUI login → Add to Home Screen / Install (same PWA steps).

Never create a tunnel, port-forward, or WAN firewall rule to `LLM_LAN_IP:8001`.

---

## 5. Concurrency (one GPU)

One Spark GPU, one Tabby process. 3–4 **light** chats are fine: they **queue**. Two people sending at once take turns; the UI waits.

Heavy concurrent long prompts (big pastes, multi-image vision, huge histories) slow **everyone**. Vision already runs at about half tok/s.

No second Spark is required for this house. If it starts to feel stuck, shorten context / one photo at a time / don’t send four essays at once.

---

## Chat-only

This stack is a family chat client. It is not Hermes.

| On | Off |
|----|-----|
| Login, named accounts, per-user history | Tools / tool servers |
| Image upload + webcam → Flash vision (Tabby) | Code interpreter / execution |
| PWA on phones | Web search |
| Local generate → Mac ComfyUI FLUX FP8 | Cloud DALL·E / Gemini / Automatic1111 |
| | Agent loops / computer-use |
| | Ollama, EXO, Spark ComfyUI, 27B `:8000` as chat |

If someone enables tools in the admin UI later, turn them back off. This is chat-only — **no tool or agent access to the home LAN.**

---

## Security

Short checklist. Details are in compose / `.env.example` / the snippets.

1. **No public Tabby.** `:8001` stays LAN / Tailscale-only. Policy if anyone ever retargets listen (not from this folder): bind **`127.0.0.1`** (local only) or the **LAN / Tailscale IP** (`LLM_LAN_IP`). Never `0.0.0.0` facing the public internet. **Never open `:8001` on the router.** Do **not** rebind, restart, Funnel, or Tunnel Tabby from this stack. The live Spark unit is already on the home LAN — leave it. Phones never get a subnet route to the LLM LAN.
2. **Auth required.** `WEBUI_AUTH` is forced on. After bootstrap, `ENABLE_SIGNUP=false`. Unique strong passwords. Extra accounts = user. No shared admin. No anonymous access.
3. **TLS.** Phones use Tailscale Serve (MagicDNS + HTTPS) or Cloudflare Tunnel **plus** Access. No plain HTTP from phones.
4. **API key hygiene.** `OPENAI_API_KEY` and `WEBUI_SECRET_KEY` live in host `.env` / a password manager. Placeholders only in git. Open WebUI → Tabby is LAN (or docker extra_hosts), never the public tunnel.
5. **Least privilege.** Community sharing, marketplace update checks, API keys, channels, memories, webhooks, tools, code, web search: off. `OFFLINE_MODE` + `HF_HUB_OFFLINE` so the UI does not pull untrusted models. **Generate** is local Mac ComfyUI only — never OpenAI/Gemini, never Spark FLUX.
6. **Uploads / generate.** Chat uploads 8 MB, 4 files, `jpg/jpeg/png/webp` → Tabby. Generated PNGs stay on the Mac ComfyUI output dir. No share links, no analytics. Uploaded photos and gens are still sensitive.
7. **Sessions.** `JWT_EXPIRES_IN=7d`. After HTTPS is up: `WEBUI_SESSION_COOKIE_SECURE=true` and `WEBUI_AUTH_COOKIE_SECURE=true` (SameSite=strict). Log out on shared tablets.
8. **Allowlist.** Tailscale: `tailscale/acl.hujson.example` (user group → `tag:sara-ai:443`). Cloudflare: `cloudflare/access-policy.md` (named emails only). Default path: the Open WebUI host already has a LAN path to `LLM_LAN_IP`. Optional Magicsock: if the UI box is *not* on that LAN, a **host-only** Tailscale subnet/exit for that one machine can reach Tabby — never approve that route for phones or non-admin users.
9. **Compose.** Open WebUI image is pinned to `v0.11.3` (not `:latest`). `cap_drop: ALL`, `no-new-privileges`. Chats persist on named volume `open-webui-data` (Docker-managed; do not world-chmod a bind mount). Read-only rootfs is optional — `docker compose -f docker-compose.yml -f docker-compose.readonly.yml up -d` (tmpfs for `/tmp`). Drop the overlay if the UI will not start.
10. **Do not.** Anonymous mode, signup left on, raw port-forward of Tabby or ComfyUI `:8188`, Funnel without a hard think, pointing chat at Hermes / `:8000`, or running FLUX on Spark next to Tabby.
11. **Image-gen RAM.** Spark `MemAvailable` ~6.9 GiB (2026-09-17) < FLUX FP8 12–16 GiB. ComfyUI on Mac, `127.0.0.1` only, one job. Do not unload EXL3. See [`image-gen/README.md`](image-gen/README.md).

### Lost phone / revoke / rotate

| Event | What to do |
|-------|------------|
| Someone loses a phone | Tailscale admin → Machines / Users → disable or remove that device. Cloudflare Access → revoke the user’s session. Open WebUI → change **that** user’s password (Admin → Users). They sign in again on the replacement. |
| Parent laptop lost | Same, plus rotate `WEBUI_SECRET_KEY` (invalidates every session) and the admin password. Recreate compose after the new secret is in `.env`. |
| Rotate admin password | Open WebUI Settings (admin account) or Admin → Users. Store the new password in a password manager. |
| Rotate Tabby key (if you ever set one) | Change it only in host `.env`. Do not commit. Recreate the container. Do not change the Tabby unit unless an explicit ops task says so. |
| Shared tablet | Log out of Open WebUI when done. Shorter Access session (24h) if you use Cloudflare. |

---

## Files

| Path | What |
|------|------|
| `docker-compose.yml` | Open WebUI → Tabby. Optional `tailscale` / `cloudflare` profiles. |
| `docker-compose.readonly.yml` | Optional read-only rootfs overlay (tmpfs `/tmp`). |
| `.env.example` | All knobs. Copy to `.env`. |
| `tailscale/serve.sh` | Host Serve (Funnel refused unless `SARA_AI_ALLOW_FUNNEL=1`). |
| `tailscale/serve.json.example` | Sidecar Serve config (`${TS_CERT_DOMAIN}`). |
| `tailscale/acl.hujson.example` | Household-only grants; no Tabby subnet. |
| `cloudflare/config.yml.example` | Host `cloudflared` ingress. |
| `cloudflare/access-policy.md` | Access email allowlist (required with Tunnel). |
| [`image-gen/`](image-gen/) | Mac ComfyUI install, FLUX FP8, LoRAs, smoke test, Open WebUI Images wiring. |
| [`RETENTION.md`](RETENTION.md) | Locked: 7-day chat-history purge; Spark clips wiped after delivery; Mac `output/` 24h. Exempt: pinned notes, weights, install, ACL, accounts. |

---

## Ops notes

- **Restart UI only:** `docker compose restart` in this folder. That is not a Tabby restart.
- **Upgrade UI:** bump the image tag in `docker-compose.yml`, `docker compose pull && docker compose up -d`. Chats live in the `open-webui-data` volume until the **7-day** purge ([`RETENTION.md`](RETENTION.md)). The volume/install/accounts survive; old threads do not.
- **Persistent config:** Open WebUI writes some env values into its DB on first boot. After that, change them in Admin → Settings or recreate with a fresh volume (wipes history).
- **Secrets:** `.env`, tunnel tokens, Tailscale auth keys, user passwords → a password manager. Not git.
- **Volume:** `open-webui-data` is a named Docker volume. If you switch to a host bind mount, create the dir yourself and do not `chmod 777`.
- **LAN path:** the Open WebUI host reaches Tabby at `LLM_LAN_IP`. Compose on another LAN host still uses `OPENAI_API_BASE_URL=http://LLM_LAN_IP:8001/v1`. The container uses the host’s LAN; other phones do not.
- **LibreChat** would also sit in front of Tabby. This repo standardizes on Open WebUI (PWA + light accounts).

---

## Done checklist (operator)

- [ ] `.env` filled; compose up; `/health` 200
- [ ] Admin + a few named users; signup off; extra accounts are users
- [ ] Model list shows Flash from `:8001` (not 27B / not Hermes)
- [ ] Photo + webcam work; family knows vision is slower; upload caps on
- [ ] Tailscale Serve **or** Cloudflare Tunnel + Access; phones on HTTPS; PWA installed
- [ ] Cookie Secure flipped on after HTTPS; `WEBUI_URL` / CORS match the HTTPS URL
- [ ] Tailscale ACL or Cloudflare Access allowlist applied
- [ ] Tabby / Hermes / 27B / EXO left alone; `:8001` not on the router
- [ ] Image gen: Mac ComfyUI up; Admin Images = ComfyUI; smoke PNG; Spark memory unchanged
- [ ] Style workflows imported (photoreal default); extra accounts are users; queue = 1
