#!/usr/bin/env bash
# Stop Mac ComfyUI only. Does not touch Tabby / Hermes / 27B / EXO.
set -euo pipefail

PORT="${COMFYUI_PORT:-8188}"
if launchctl print "gui/$(id -u)/com.example.chat.comfyui" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/com.example.chat.comfyui" || true
fi

# Match the headless server started by start.sh / launchd — not random python.
pkill -f "ComfyUI/main.py" 2>/dev/null || true
pkill -f "comfyui.*--port ${PORT}" 2>/dev/null || true
echo "ComfyUI stop requested (port $PORT). Tabby on Spark is unchanged."
