# Cloudflare Access (required if you use the Tunnel)

A Tunnel hostname without Access is an open door to the Open WebUI login page. Do not do that.

Zero Trust → Access → Applications → Self-hosted:

| Field | Value |
|-------|--------|
| Application | Sara AI |
| Domain | `chat.example.com` (the Tunnel public hostname) |
| Session | 24h or less on shared tablets; 7d max |
| Policy | Allow |
| Include | Emails — the named user addresses only |
| Identity | One-time PIN (email) is enough; no public IdP |

Do **not** add “Everyone” / “Accept all”. Do **not** skip Access “just for testing” on a real hostname.

The Tunnel origin must be Open WebUI (`http://127.0.0.1:8080` on the host, or `http://open-webui:8080` in compose). Never `http://LLM_LAN_IP:8001`.

See `config.yml.example` for ingress.
