#!/usr/bin/env bash
# Optional launchd (on-demand). RunAtLoad stays false so 27B keeps Mac RAM
# until someone wants pictures. Does not touch Tabby.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
START="$ROOT/start.sh"
DEST="$HOME/Library/LaunchAgents/com.example.chat.comfyui.plist"
LOGDIR="$HOME/Library/Logs"
mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"

sed \
  -e "s|COMFYUI_START_SH|$START|g" \
  -e "s|COMFYUI_WORKDIR|$ROOT|g" \
  -e "s|COMFYUI_LOG_OUT|$LOGDIR/sara-ai-comfyui.out.log|g" \
  -e "s|COMFYUI_LOG_ERR|$LOGDIR/sara-ai-comfyui.err.log|g" \
  "$ROOT/launchd/com.example.chat.comfyui.plist" > "$DEST"

launchctl bootout "gui/$(id -u)/com.example.chat.comfyui" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installed $DEST (RunAtLoad=false)."
echo "Start:  launchctl kickstart -k gui/\$(id -u)/com.example.chat.comfyui"
echo "   or:  $ROOT/start.sh"
echo "Stop:   $ROOT/stop.sh"
