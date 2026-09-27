#!/usr/bin/env bash
# One test image against local ComfyUI. Does not call Spark / Tabby / Hermes.
# Expected envelope: Mac unified memory +12–16 GiB while sampling; Spark unchanged.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"

PORT="${COMFYUI_PORT:-8188}"
BASE="http://127.0.0.1:${PORT}"
STYLE="${1:-photoreal}"
PROMPT="${2:-a red bicycle parked under a maple tree, daytime, no people}"

case "$STYLE" in
  photoreal|photo) WF="$ROOT/workflows/flux-fp8-photoreal.json" ;;
  anime)           WF="$ROOT/workflows/flux-fp8-anime.json" ;;
  illustrated|illustration) WF="$ROOT/workflows/flux-fp8-illustrated.json" ;;
  *) echo "Usage: $0 [photoreal|anime|illustrated] [prompt]" >&2; exit 1 ;;
esac

if ! curl -fsS "$BASE/system_stats" >/dev/null; then
  echo "ComfyUI not reachable at $BASE — ./start.sh first." >&2
  exit 1
fi

TMP="$(mktemp)"
python3 - "$WF" "$PROMPT" "$TMP" <<'PY'
import json, sys, uuid
wf = json.load(open(sys.argv[1]))
wf["6"]["inputs"]["text"] = sys.argv[2]
wf["3"]["inputs"]["seed"] = 42
body = {"prompt": wf, "client_id": str(uuid.uuid4())}
json.dump(body, open(sys.argv[3], "w"))
PY

echo "Queueing one $STYLE image (batch_size=1). Watch Activity Monitor; expect ~12–16 GiB."
RESP="$(curl -fsS -X POST "$BASE/prompt" -H 'Content-Type: application/json' --data @"$TMP")"
PROMPT_ID="$(python3 -c "import json,sys; print(json.load(sys.stdin)['prompt_id'])" <<<"$RESP")"
echo "prompt_id=$PROMPT_ID"

for _ in $(seq 1 180); do
  HIST="$(curl -fsS "$BASE/history/$PROMPT_ID")"
  DONE="$(python3 -c "import json,sys; h=json.load(sys.stdin); print('yes' if h else 'no')" <<<"$HIST")"
  if [[ "$DONE" == "yes" ]]; then
    echo "$HIST" | python3 -c "import json,sys; h=json.load(sys.stdin); print(json.dumps(h, indent=2)[:2000])"
    echo "Smoke OK. Image is under \$COMFYUI_HOME/output/. Spark Tabby should still be serving :8001."
    rm -f "$TMP"
    exit 0
  fi
  sleep 2
done

rm -f "$TMP"
echo "Timed out waiting for $PROMPT_ID" >&2
exit 1
