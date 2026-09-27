#!/usr/bin/env bash
# Mac "always ready" mode for Sara AI: ComfyUI + Tabby relay + Docker Desktop (Open WebUI)
# all start at login via LaunchAgents. Overrides launchd/install.sh's on-demand default.
# Does not touch Spark, Tabby, Hermes, oMLX. Localhost binds only.
#
#   ./mac/install-always-on.sh          # install + start
#   ./mac/install-always-on.sh remove   # unload all three
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # image-gen/
LA="$HOME/Library/LaunchAgents"; U="gui/$(id -u)"; LOGS="$HOME/Library/Logs"
mkdir -p "$LA" "$LOGS"
AGENTS=(com.example.chat.comfyui com.example.chat.tabby-relay com.example.chat.docker-desktop)

if [[ "${1:-}" == "remove" ]]; then
  for a in "${AGENTS[@]}"; do launchctl bootout "$U/$a" 2>/dev/null || true; rm -f "$LA/$a.plist"; done
  echo "always-on agents removed (ComfyUI/relay/Docker keep running until reboot; use stop.sh)"; exit 0
fi

plist() { # label, stdout/err log, keepalive(true|false), args...
  local label="$1" log="$2" keep="$3"; shift 3
  { echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo "<plist version=\"1.0\"><dict><key>Label</key><string>$label</string><key>ProgramArguments</key><array>"
    for a in "$@"; do echo "<string>$a</string>"; done
    echo "</array><key>RunAtLoad</key><true/><key>KeepAlive</key><$keep/><key>ThrottleInterval</key><integer>15</integer>"
    echo "<key>StandardOutPath</key><string>$log</string><key>StandardErrorPath</key><string>$log</string></dict></plist>"
  } > "$LA/$label.plist"
  launchctl bootout "$U/$label" 2>/dev/null || true
  launchctl bootstrap "$U" "$LA/$label.plist"; echo "loaded $label"
}

# 1. ComfyUI always on (KeepAlive restarts it after a crash)
plist com.example.chat.comfyui "$LOGS/sara-ai-comfyui.out.log" true "$ROOT/start.sh"
# 2. 127.0.0.1:8001 -> LLM_LAN_IP:8001 relay (Docker Desktop VM cannot reach the LAN; see README)
plist com.example.chat.tabby-relay "$LOGS/sara-ai-tabby-relay.log" true /usr/bin/python3 "$ROOT/mac/tabby-relay.py"
# 3. Docker Desktop at login; the sara-ai container is restart: unless-stopped
plist com.example.chat.docker-desktop "$LOGS/sara-ai-docker-desktop.log" false /usr/bin/open -g -a Docker
S="$HOME/Library/Group Containers/group.com.docker/settings-store.json"
if [[ -f "$S" ]]; then python3 - "$S" <<'PY'
import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["AutoStart"]=True; d["OpenUIOnStartupDisabled"]=True
json.dump(d,open(p,"w"),indent=2); print("Docker Desktop: AutoStart=true, dashboard hidden at startup")
PY
fi

sleep 3
echo "comfy  : $(curl -sS -m 5 -o /dev/null -w '%{http_code}' 127.0.0.1:8188/system_stats)"
echo "relay  : $(curl -sS -m 5 -o /dev/null -w '%{http_code}' 127.0.0.1:8001/v1/models)"
echo "webui  : $(curl -sS -m 5 127.0.0.1:8080/health 2>/dev/null || echo 'not yet (Docker starting)')"
echo "Reminder: LaunchAgents run at LOGIN. For unattended reboots enable automatic login for this user."
