#!/usr/bin/env bash
# Download FLUX.1-dev FP8 + three style LoRAs onto the Mac.
# Does not run on Spark. Does not unload Tabby. No FLUX.2 / video.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"

COMFYUI_HOME="${COMFYUI_HOME:-$HOME/ComfyUI}"
CKPT_DIR="$COMFYUI_HOME/models/checkpoints"
LORA_DIR="$COMFYUI_HOME/models/loras"

if [[ ! -d "$COMFYUI_HOME" ]]; then
  echo "Run ./install-mac.sh first (COMFYUI_HOME=$COMFYUI_HOME missing)." >&2
  exit 1
fi

if [[ -n "${HF_TOKEN:-}" ]]; then
  export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
fi

HF=(hf)
if ! command -v hf >/dev/null 2>&1; then
  if [[ -x "$COMFYUI_HOME/.venv/bin/hf" ]]; then
    HF=("$COMFYUI_HOME/.venv/bin/hf")
  else
    echo "hf not found. ./install-mac.sh installs huggingface_hub in the venv." >&2
    exit 1
  fi
fi

echo "FLUX.1 [dev] license (informational only):"
echo "  https://huggingface.co/black-forest-labs/FLUX.1-dev"
echo "Comfy-Org/flux1-dev is not gated and downloads without a token."
echo

# 17,246,524,772 bytes ≈ 16.1 GiB (17.2 GB). Do NOT fetch flux1-dev.safetensors.
"${HF[@]}" download Comfy-Org/flux1-dev flux1-dev-fp8.safetensors \
  --local-dir "$CKPT_DIR"

# Photoreal ~22 MB
"${HF[@]}" download XLabs-AI/flux-RealismLora lora.safetensors \
  --local-dir "$LORA_DIR/_xlabs"
cp "$LORA_DIR/_xlabs/lora.safetensors" "$LORA_DIR/flux-photoreal.safetensors"

# Anime ~146 MB (v2). Trigger baked in workflow node 12: "modern anime style"
"${HF[@]}" download alfredplpl/flux.1-dev-modern-anime-lora modern-anime-lora-2.safetensors \
  --local-dir "$LORA_DIR/_anime"
cp "$LORA_DIR/_anime/modern-anime-lora-2.safetensors" "$LORA_DIR/flux-anime.safetensors"

# Illustration ~165 MB. Trigger baked in workflow node 12: "frstingln illustration"
"${HF[@]}" download alvdansen/frosting_lane_flux flux_dev_frostinglane_araminta_k.safetensors \
  --local-dir "$LORA_DIR/_frost"
cp "$LORA_DIR/_frost/flux_dev_frostinglane_araminta_k.safetensors" \
  "$LORA_DIR/flux-illustrated.safetensors"

echo
echo "Expected on disk:"
echo "  $CKPT_DIR/flux1-dev-fp8.safetensors     ~16.1 GiB"
echo "  $LORA_DIR/flux-photoreal.safetensors    ~22 MB"
echo "  $LORA_DIR/flux-anime.safetensors        ~146 MB"
echo "  $LORA_DIR/flux-illustrated.safetensors  ~165 MB"
echo "Peak RAM while generating: ~12–16 GiB on the Mac (not on Spark)."
echo
ls -lh "$CKPT_DIR/flux1-dev-fp8.safetensors" \
  "$LORA_DIR/flux-photoreal.safetensors" \
  "$LORA_DIR/flux-anime.safetensors" \
  "$LORA_DIR/flux-illustrated.safetensors"
