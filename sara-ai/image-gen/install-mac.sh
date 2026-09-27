#!/usr/bin/env bash
# Install ComfyUI on the Mac (Apple Silicon). Does not touch Tabby / Hermes / 27B.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"

COMFYUI_HOME="${COMFYUI_HOME:-$HOME/ComfyUI}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for the Mac. Spark cannot hold FLUX next to Tabby." >&2
  echo "See image-gen/README.md (coexistence)." >&2
  exit 1
fi

if [[ -d "$COMFYUI_HOME/.git" ]]; then
  echo "ComfyUI already present at $COMFYUI_HOME"
else
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_HOME"
fi

python3 -m venv "$COMFYUI_HOME/.venv"
# shellcheck disable=SC1091
source "$COMFYUI_HOME/.venv/bin/activate"
python -m pip install --upgrade pip
# MPS / Mac wheels. Do not install CUDA or Nunchaku here.
pip install torch torchvision torchaudio
pip install -r "$COMFYUI_HOME/requirements.txt"
pip install huggingface_hub

mkdir -p \
  "$COMFYUI_HOME/models/checkpoints" \
  "$COMFYUI_HOME/models/loras" \
  "$COMFYUI_HOME/input" \
  "$COMFYUI_HOME/output"

echo
echo "ComfyUI ready at $COMFYUI_HOME"
echo "Next: ./download-models.sh   then   ./start.sh"
echo "Do not download flux1-dev.safetensors (FP16 ~24 GB) or any FLUX.2 / video pack."
