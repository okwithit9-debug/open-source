#!/usr/bin/env bash
# Put HTTPS in front of local Open WebUI. Does not touch Tabby / Hermes / 27B.
#
#   ./tailscale/serve.sh           # tailnet only (preferred)
#   ./tailscale/serve.sh --funnel  # public internet — last resort
#
# Prerequisites: Tailscale app on this host, MagicDNS + HTTPS certs enabled
# (Tailscale admin → DNS). Open WebUI already listening on 127.0.0.1:8080.
set -euo pipefail

PORT="${WEBUI_PORT:-8080}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale CLI not found. Install Tailscale, then retry." >&2
  exit 1
fi

if [[ "${1:-}" == "--funnel" ]]; then
  if [[ "${SARA_AI_ALLOW_FUNNEL:-}" != "1" ]]; then
    echo "Refusing Funnel. It publishes Open WebUI to the public internet." >&2
    echo "Prefer: $0   (Tailscale Serve, tailnet only)." >&2
    echo "Last resort: SARA_AI_ALLOW_FUNNEL=1 $0 --funnel" >&2
    exit 1
  fi
  echo "WARNING: Funnel publishes Open WebUI to the public internet."
  echo "Open WebUI login is then the only gate. Prefer Serve."
  tailscale funnel --bg "${PORT}"
else
  tailscale serve --bg "${PORT}"
fi

echo
tailscale serve status
echo
echo "Phones on the tailnet open the HTTPS URL above, then log into Open WebUI."
echo "Set WEBUI_URL in .env to the HTTPS URL Serve prints and recreate the container."
