#!/usr/bin/env bash
# Headless ComfyUI API on the Mac. Localhost only.
# Does not start, stop, or unload Spark Tabby / Hermes / 27B / EXO.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"

COMFYUI_HOME="${COMFYUI_HOME:-$HOME/ComfyUI}"
LISTEN="${COMFYUI_LISTEN:-127.0.0.1}"
PORT="${COMFYUI_PORT:-8188}"

if [[ ! -x "$COMFYUI_HOME/.venv/bin/python" ]]; then
  echo "ComfyUI venv missing. Run ./install-mac.sh" >&2
  exit 1
fi
if [[ ! -f "$COMFYUI_HOME/models/checkpoints/flux1-dev-fp8.safetensors" ]]; then
  echo "FLUX FP8 checkpoint missing. Run ./download-models.sh" >&2
  exit 1
fi
if [[ "$LISTEN" != "127.0.0.1" && "$LISTEN" != "localhost" ]]; then
  echo "Refusing listen=$LISTEN — ComfyUI stays on localhost." >&2
  echo "Open WebUI (Docker on this Mac) reaches it via host.docker.internal." >&2
  exit 1
fi

cd "$COMFYUI_HOME"
# shellcheck disable=SC1091
source .venv/bin/activate
exec python main.py \
  --listen "$LISTEN" \
  --port "$PORT" \
  --disable-auto-launch \
  --preview-method none \
  --dont-print-server
