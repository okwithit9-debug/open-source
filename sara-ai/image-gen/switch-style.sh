#!/usr/bin/env bash
# Point the house at photoreal | anime | illustrated.
# Open WebUI keeps one ComfyUI workflow. After switching, import the JSON
# in Admin → Settings → Images (PersistentConfig). Extra accounts stay users.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
STYLE="${1:-}"

case "$STYLE" in
  photoreal|photo|realism) FILE="flux-fp8-photoreal.json" ; LABEL="photoreal" ;;
  anime)                   FILE="flux-fp8-anime.json" ; LABEL="anime" ;;
  illustrated|illustration|storybook) FILE="flux-fp8-illustrated.json" ; LABEL="illustrated" ;;
  *)
    echo "Usage: $0 photoreal|anime|illustrated" >&2
    exit 1
    ;;
esac

SRC="$ROOT/workflows/$FILE"
DEST="$ROOT/workflows/current.json"
cp "$SRC" "$DEST"
echo "Active style: $LABEL"
echo "Workflow: $DEST"
echo
echo "In Open WebUI (admin device only):"
echo "  Admin → Settings → Images"
echo "  Engine = ComfyUI"
echo "  Base URL = http://host.docker.internal:8188"
echo "  Import / paste $DEST"
echo "  Map nodes from workflows/nodes.json (prompt=6, width/height=5, steps/seed=3)"
echo
echo "Users pick a look in the prompt too (anime / photo / storybook)."
echo "The LoRA + baked prefix on this workflow is the real style jump."
echo "Queue stays one image at a time. Chat still hits Tabby :8001."
