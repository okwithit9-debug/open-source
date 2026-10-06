#!/usr/bin/env python3
"""Node dashboard: live, read-only status board for a Mac + N Linux / DGX nodes.

Serves a dark status board + JSON API. Every node in config.json is polled with
ONE batched `ssh <node> bash -s` per cycle over an SSH ControlMaster socket.
Optionally shows a local OpenAI-compatible inference endpoint and a local
fallback model. Never starts/stops containers, never uses sudo, never writes
anything on the nodes. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:  # security layer (monitor-only); dashboard keeps working if it fails to import
    import security
except Exception as _sec_err:  # noqa: BLE001
    security = None  # type: ignore[assignment]
    print(f"security module disabled: {_sec_err}", flush=True)

HERE = Path(__file__).resolve().parent


def _load_config() -> Dict[str, Any]:
    """config.json next to this file (or $NODE_DASH_CONFIG); falls back to config.example.json."""
    env = os.environ.get("NODE_DASH_CONFIG")
    for p in ([Path(env).expanduser()] if env else []) + [HERE / "config.json", HERE / "config.example.json"]:
        if p.is_file():
            cfg = json.loads(p.read_text())
            cfg["_path"] = str(p)
            return cfg
    raise SystemExit("no config.json found (copy config.example.json to config.json and edit it)")


CONFIG = _load_config()
_SAFE = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")

PORT = int(os.environ.get("NODE_DASH_PORT", CONFIG.get("port", 8095)))
BIND = os.environ.get("NODE_DASH_BIND", CONFIG.get("bind", "127.0.0.1"))
POLL_SECS = float(os.environ.get("NODE_DASH_POLL", CONFIG.get("poll_secs", 3.5)))
SSH_TIMEOUT = float(os.environ.get("NODE_DASH_SSH_TIMEOUT", CONFIG.get("ssh_timeout", 4)))
HISTORY = int(os.environ.get("NODE_DASH_HISTORY", "60"))
TITLE = str(CONFIG.get("title") or "GPU Node Cluster")
STATE_PATH = Path(
    os.environ.get("NODE_DASH_STATE", str(Path.home() / ".cache" / "node-dashboard" / "state.json"))
).expanduser()
CTRL_DIR = Path(os.environ.get("NODE_DASH_CTRL", "/tmp/node-dashboard-ssh"))
CTRL_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Nodes come from config.json ("nodes": [...]); see config.example.json.
NODES: List[Dict[str, Any]] = []
for _n in CONFIG.get("nodes") or []:
    if not _SAFE.match(str(_n.get("id", ""))) or not _SAFE.match(str(_n.get("ssh", ""))):
        raise SystemExit(f"bad node id/ssh in config: {_n!r}")
    if _n.get("ssh_hostname") and not _SAFE.match(str(_n["ssh_hostname"])):
        raise SystemExit(f"bad ssh_hostname in config: {_n!r}")
    NODES.append({
        "id": _n["id"],
        "label": _n.get("label") or _n["id"],
        "ssh": _n["ssh"],
        "ssh_hostname": _n.get("ssh_hostname") or None,
        "hostname_hint": _n.get("hostname_hint") or _n["ssh"],
        "lan": _n.get("lan") or "",
        "iface_labels": _n.get("iface_labels") or {},
    })

INFER = dict(CONFIG.get("inference") or {})
INFER_ON = bool(INFER.get("enabled"))
INFER_CONTAINER = str(INFER.get("container") or "")
if INFER_CONTAINER and not re.match(r"^[A-Za-z0-9_.-]{1,64}$", INFER_CONTAINER):
    raise SystemExit("inference.container must be a plain container name")
LOCAL_MODEL = dict(CONFIG.get("local_model") or {})
LOCAL_ON = bool(LOCAL_MODEL.get("enabled"))

REMOTE_SCRIPT = r"""
set +e
echo HOSTNAME=$(hostname 2>/dev/null)
echo UPTIME_SEC=$(cut -d. -f1 /proc/uptime 2>/dev/null)
echo LOADAVG=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)
echo NPROC=$(nproc 2>/dev/null)
# CPU: total jiffies for util delta
CPU_LINE=$(grep '^cpu ' /proc/stat 2>/dev/null)
echo CPU_STAT=$CPU_LINE
# memory (unified on GB10 / DGX Spark)
awk '/^MemTotal:/{printf "MEM_TOTAL_KB=%s\n",$2} /^MemAvailable:/{printf "MEM_AVAIL_KB=%s\n",$2} /^MemFree:/{printf "MEM_FREE_KB=%s\n",$2} /^Buffers:/{printf "MEM_BUFFERS_KB=%s\n",$2} /^Cached:/{printf "MEM_CACHED_KB=%s\n",$2} /^SwapTotal:/{printf "SWAP_TOTAL_KB=%s\n",$2} /^SwapFree:/{printf "SWAP_FREE_KB=%s\n",$2}' /proc/meminfo 2>/dev/null
# CPU temps (milliC)
TMAX=0; TSUM=0; TN=0
for z in /sys/class/thermal/thermal_zone*/temp; do
  [ -r "$z" ] || continue
  v=$(cat "$z" 2>/dev/null)
  case "$v" in (*[!0-9]*) continue;; esac
  TSUM=$((TSUM+v)); TN=$((TN+1))
  [ "$v" -gt "$TMAX" ] && TMAX=$v
done
echo CPU_TEMP_MAX_MC=$TMAX
echo CPU_TEMP_AVG_MC=$(( TN>0 ? TSUM/TN : 0 ))
# GPU via nvidia-smi (memory often N/A on GB10 unified)
NS=$(nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,utilization.memory,power.draw --format=csv,noheader,nounits 2>/dev/null | head -1)
echo NVIDIA_CSV=$NS
# per-process GPU mem if reported
GPUMEM=$(nvidia-smi --query-compute-apps=used_gpu_memory --format=csv,noheader,nounits 2>/dev/null | awk '{s+=$1} END{print s+0}')
echo GPU_PROC_MEM_MIB=$GPUMEM
# disk root
df -B1 / 2>/dev/null | awk 'NR==2{printf "DISK_TOTAL=%s\nDISK_USED=%s\nDISK_AVAIL=%s\n",$2,$3,$4}'
# net bytes + addrs
for i in /sys/class/net/*; do
  n=$(basename "$i")
  case "$n" in lo|docker*|veth*|br-*) continue;; esac
  rx=$(cat "$i/statistics/rx_bytes" 2>/dev/null || echo 0)
  tx=$(cat "$i/statistics/tx_bytes" 2>/dev/null || echo 0)
  oper=$(cat "$i/operstate" 2>/dev/null || echo unknown)
  ip=$(ip -4 -o addr show dev "$n" 2>/dev/null | awk '{print $4}' | head -1)
  echo "NET|$n|$oper|$rx|$tx|${ip:-}"
done
# docker (read-only list)
if command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.Names}}|{{.Status}}|{{.Image}}' 2>/dev/null | while IFS= read -r line; do
    echo "DOCKER|$line"
  done
  # Tensor-parallel role from the inference container's Cmd (--tp/--rank/--master), if configured
  if [ -n "__INFER_CONTAINER__" ]; then
    CMD=$(docker inspect __INFER_CONTAINER__ --format '{{json .Config.Cmd}}' 2>/dev/null)
    echo "TP_CMD=$CMD"
  fi
fi
echo OK=1
"""

REMOTE_SCRIPT = REMOTE_SCRIPT.replace("__INFER_CONTAINER__", INFER_CONTAINER)

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "updated_at": None,
    "nodes": {},
    "inference": {},
    "local_model": {},
    "history": {},
}
_prev_net: Dict[str, Dict[str, Tuple[int, int, float]]] = {}
_prev_cpu: Dict[str, Tuple[int, int, float]] = {}  # idle, total, t


def _load_persisted() -> Dict[str, Any]:
    try:
        if STATE_PATH.is_file():
            return json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_persisted(data: Dict[str, Any]) -> None:
    try:
        STATE_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


_persisted = _load_persisted()


def _ssh_base(host: str, hostname_override: Optional[str] = None) -> List[str]:
    ctrl = str(CTRL_DIR / f"%r@%h:%p")
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={max(1, int(SSH_TIMEOUT))}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={ctrl}",
        "-o",
        "ControlPersist=120",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
    ]
    if hostname_override:
        # Command-line -o HostName wins over ~/.ssh/config (useful when a tool rewrites HostName).
        cmd.extend(["-o", f"HostName={hostname_override}"])
    cmd.append(host)
    return cmd


def ssh_collect(host: str, hostname_override: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    """Return (ok, stdout, error)."""
    cmd = _ssh_base(host, hostname_override=hostname_override) + ["bash", "-s"]
    try:
        p = subprocess.run(
            cmd,
            input=REMOTE_SCRIPT,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT + 2,
        )
        if p.returncode != 0:
            err = (p.stderr or p.stdout or f"exit {p.returncode}").strip()
            return False, p.stdout or "", err[:300]
        return True, p.stdout, None
    except subprocess.TimeoutExpired:
        return False, "", "ssh timeout"
    except Exception as e:
        return False, "", str(e)[:300]


def parse_remote(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "nets": [],
        "dockers": [],
        "raw": {},
    }
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("NET|"):
            parts = line.split("|")
            if len(parts) >= 6:
                out["nets"].append(
                    {
                        "iface": parts[1],
                        "oper": parts[2],
                        "rx": int(parts[3] or 0),
                        "tx": int(parts[4] or 0),
                        "ip": parts[5] or None,
                    }
                )
            continue
        if line.startswith("DOCKER|"):
            parts = line.split("|", 3)
            if len(parts) >= 4:
                out["dockers"].append(
                    {"name": parts[1], "status": parts[2], "image": parts[3]}
                )
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out["raw"][k] = v
    return out


def _cpu_util(sid: str, cpu_stat: str, now: float) -> Optional[float]:
    # cpu user nice system idle iowait irq softirq steal ...
    parts = cpu_stat.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        nums = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    prev = _prev_cpu.get(sid)
    _prev_cpu[sid] = (idle, total, now)
    if not prev:
        return None
    didle = idle - prev[0]
    dtotal = total - prev[1]
    if dtotal <= 0:
        return None
    util = max(0.0, min(100.0, 100.0 * (1.0 - didle / dtotal)))
    return round(util, 1)


def _net_rates(
    sid: str, nets: List[Dict[str, Any]], now: float, labels: Dict[str, str]
) -> List[Dict[str, Any]]:
    prev_map = _prev_net.get(sid, {})
    new_map: Dict[str, Tuple[int, int, float]] = {}
    result = []
    for n in nets:
        iface = n["iface"]
        rx, tx = n["rx"], n["tx"]
        new_map[iface] = (rx, tx, now)
        rx_mbps = tx_mbps = None
        if iface in prev_map:
            prx, ptx, pt = prev_map[iface]
            dt = now - pt
            if dt > 0.2:
                rx_mbps = round((rx - prx) * 8 / dt / 1e6, 2)  # Mb/s
                tx_mbps = round((tx - ptx) * 8 / dt / 1e6, 2)
                if rx_mbps < 0:
                    rx_mbps = 0.0
                if tx_mbps < 0:
                    tx_mbps = 0.0
        result.append(
            {
                "iface": iface,
                "label": labels.get(iface, iface),
                "oper": n["oper"],
                "ip": n["ip"],
                "rx_mbps": rx_mbps,
                "tx_mbps": tx_mbps,
                "rx_bytes": rx,
                "tx_bytes": tx,
            }
        )
    _prev_net[sid] = new_map
    # Prefer fabric + LAN first
    order = []
    for key in ("Fabric", "LAN"):
        for r in result:
            if key in r["label"] and r not in order:
                order.append(r)
    for r in result:
        if r not in order:
            order.append(r)
    return order


def _parse_nvidia(csv_line: str) -> Dict[str, Any]:
    # name, temp, util.gpu, util.mem, power
    if not csv_line or csv_line.startswith("["):
        return {}
    parts = [p.strip() for p in csv_line.split(",")]
    def num(i: int) -> Optional[float]:
        if i >= len(parts):
            return None
        s = parts[i]
        if not s or s.upper() == "N/A" or s.startswith("["):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return {
        "name": parts[0] if parts else None,
        "temp_c": num(1),
        "util_pct": num(2),
        "mem_util_pct": num(3),
        "power_w": num(4),
    }


def _tp_from_cmd(cmd_json: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"tp": None, "rank": None, "master": None, "raw": cmd_json}
    if not cmd_json or cmd_json in ("", "null"):
        return info
    try:
        arr = json.loads(cmd_json)
    except Exception:
        return info
    if not isinstance(arr, list):
        return info
    for i, tok in enumerate(arr):
        if tok == "--tp" and i + 1 < len(arr):
            try:
                info["tp"] = int(arr[i + 1])
            except ValueError:
                info["tp"] = arr[i + 1]
        if tok == "--rank" and i + 1 < len(arr):
            try:
                info["rank"] = int(arr[i + 1])
            except ValueError:
                info["rank"] = arr[i + 1]
        if tok == "--master" and i + 1 < len(arr):
            info["master"] = arr[i + 1]
    return info


def build_node(meta: Dict[str, Any], now: float) -> Dict[str, Any]:
    sid = meta["id"]
    ok, stdout, err = ssh_collect(meta["ssh"], hostname_override=meta.get("ssh_hostname"))
    base = {
        "id": sid,
        "label": meta["label"],
        "ssh": meta["ssh"],
        "lan": meta["lan"],
        "online": False,
        "error": err,
        "last_seen": _persisted.get("last_seen", {}).get(sid),
        "hostname": meta["hostname_hint"],
        "uptime_sec": None,
        "uptime_human": None,
        "loadavg": None,
        "nproc": None,
        "cpu_util_pct": None,
        "cpu_temp_c": None,
        "gpu": {},
        "memory": {},
        "disk": {},
        "nets": [],
        "dockers": [],
        "tp_role": None,
        "busy": None,
        "status": "OFFLINE",
    }
    if not ok:
        # keep last_seen
        return base

    parsed = parse_remote(stdout)
    raw = parsed["raw"]
    if raw.get("OK") != "1" and not raw.get("HOSTNAME"):
        base["error"] = "empty/partial remote"
        return base

    base["online"] = True
    base["error"] = None
    base["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    _persisted.setdefault("last_seen", {})[sid] = base["last_seen"]
    _persisted.setdefault("last_seen_epoch", {})[sid] = now

    base["hostname"] = raw.get("HOSTNAME") or meta["hostname_hint"]
    try:
        up = int(raw.get("UPTIME_SEC") or 0)
    except ValueError:
        up = 0
    base["uptime_sec"] = up
    days, rem = divmod(up, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        base["uptime_human"] = f"{days}d {hours}h {mins}m"
    else:
        base["uptime_human"] = f"{hours}h {mins}m"
    base["loadavg"] = raw.get("LOADAVG")
    try:
        base["nproc"] = int(raw.get("NPROC") or 0) or None
    except ValueError:
        pass
    base["cpu_util_pct"] = _cpu_util(sid, raw.get("CPU_STAT", ""), now)
    try:
        tmax = int(raw.get("CPU_TEMP_MAX_MC") or 0)
        base["cpu_temp_c"] = round(tmax / 1000.0, 1) if tmax else None
    except ValueError:
        pass

    gpu = _parse_nvidia(raw.get("NVIDIA_CSV", ""))
    try:
        proc_mib = float(raw.get("GPU_PROC_MEM_MIB") or 0)
    except ValueError:
        proc_mib = 0.0
    if proc_mib > 0:
        gpu["proc_mem_gb"] = round(proc_mib / 1024.0, 1)
        gpu["memory_note"] = "per-process used_gpu_memory (GB10 unified; nvidia-smi total N/A)"
    else:
        gpu["proc_mem_gb"] = None
        gpu["memory_note"] = "unified memory (e.g. GB10): nvidia-smi memory fields N/A; see system RAM"
    base["gpu"] = gpu

    try:
        mt = int(raw.get("MEM_TOTAL_KB") or 0)
        ma = int(raw.get("MEM_AVAIL_KB") or 0)
        used = mt - ma
        base["memory"] = {
            "total_gb": round(mt / 1048576, 1),
            "used_gb": round(used / 1048576, 1),
            "avail_gb": round(ma / 1048576, 1),
            "used_pct": round(100.0 * used / mt, 1) if mt else None,
            "unified": True,
            "note": "system / unified CPU+GPU memory via /proc/meminfo",
        }
    except ValueError:
        base["memory"] = {}

    try:
        dt = int(raw.get("DISK_TOTAL") or 0)
        du = int(raw.get("DISK_USED") or 0)
        da = int(raw.get("DISK_AVAIL") or 0)
        base["disk"] = {
            "total_tb": round(dt / 1e12, 2),
            "used_tb": round(du / 1e12, 2),
            "avail_tb": round(da / 1e12, 2),
            "used_pct": round(100.0 * du / dt, 1) if dt else None,
        }
    except ValueError:
        base["disk"] = {}

    base["nets"] = _net_rates(sid, parsed["nets"], now, meta.get("iface_labels") or {})
    base["dockers"] = parsed["dockers"]

    tp = _tp_from_cmd(raw.get("TP_CMD", ""))
    if tp.get("tp") is not None:
        base["tp_role"] = {
            "tp": tp["tp"],
            "rank": tp["rank"],
            "master": tp["master"],
        }

    # busy heuristic
    gutil = gpu.get("util_pct") or 0
    load1 = 0.0
    if base["loadavg"]:
        try:
            load1 = float(base["loadavg"].split()[0])
        except ValueError:
            pass
    busy = (gutil >= 5) or (load1 >= 2.0) or (proc_mib > 1000 and gutil >= 1)
    # a running inference container counts as serving (model loaded)
    prefix = str(INFER.get("container_prefix") or INFER_CONTAINER or "")
    serving = bool(prefix) and any(d["name"].startswith(prefix) for d in base["dockers"])
    if not base["online"]:
        base["status"] = "OFFLINE"
        base["busy"] = False
    elif busy:
        base["status"] = "BUSY"
        base["busy"] = True
    elif serving:
        base["status"] = "IDLE (serving)"
        base["busy"] = False
    else:
        base["status"] = "IDLE"
        base["busy"] = False
    return base


def http_json(url: str, timeout: float = 2.5) -> Tuple[bool, Any, Optional[str]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "node-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(body), None
    except Exception as e:
        return False, None, str(e)[:200]


def collect_inference() -> Dict[str, Any]:
    """Optional OpenAI-compatible endpoint (e.g. an SSH-forwarded vLLM/TRT-LLM/llama.cpp server)."""
    base_url = str(INFER.get("base_url") or "http://127.0.0.1:8888").rstrip("/")
    out: Dict[str, Any] = {
        "enabled": INFER_ON, "label": INFER.get("label") or "Inference endpoint", "base_url": base_url,
        "answering": False, "health_ok": None, "models_ok": False, "error": None, "health": None,
        "model_ids": [], "tp_layout": None, "requests_running": None, "busy": None, "streams": None,
    }
    if not INFER_ON:
        return out
    hp = INFER.get("health_path", "/health")
    ok, data, err = http_json(base_url + hp, timeout=2.0) if hp else (None, None, None)
    models_ok, models, merr = http_json(base_url + INFER.get("models_path", "/v1/models"), timeout=2.0)
    out.update({"health_ok": ok, "models_ok": models_ok, "error": err or merr, "health": data if ok else None})
    if models_ok and isinstance(models, dict):
        out["model_ids"] = [m.get("id") for m in models.get("data") or [] if isinstance(m, dict)]
        out["answering"] = bool(out["model_ids"])
    if ok and isinstance(data, dict):
        out["requests_running"] = data.get("requests_running")
        out["busy"] = data.get("busy")
        out["streams"] = data.get("streams")
        out["answering"] = out["answering"] or bool(data.get("ok"))
    return out


def collect_local_model() -> Dict[str, Any]:
    """Optional model server on this Mac (OpenAI-compatible /v1/models) + optional launchd job + log tail."""
    base_url = str(LOCAL_MODEL.get("base_url") or "http://127.0.0.1:8000").rstrip("/")
    out: Dict[str, Any] = {"enabled": LOCAL_ON, "label": LOCAL_MODEL.get("label") or "Local model",
                           "base_url": base_url, "up": False, "error": None, "models": [], "loaded": None,
                           "job": None, "recent": []}
    if not LOCAL_ON:
        return out
    ok, data, err = http_json(base_url + "/v1/models", timeout=2.0)
    out["error"] = err
    if ok and isinstance(data, dict):
        out["up"] = True
        for m in data.get("data") or []:
            if isinstance(m, dict):
                out["models"].append({"id": m.get("id"), "state": m.get("state"), "loaded": m.get("loaded")})
                if m.get("loaded"):
                    out["loaded"] = m.get("id")
        if not out["loaded"] and out["models"]:
            out["loaded"] = out["models"][0]["id"]
    label = str(LOCAL_MODEL.get("launchd_label") or "")
    if label:
        job: Dict[str, Any] = {"label": label, "loaded": False, "pid": None}
        try:
            p = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=3)
            for line in p.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[2] == label:
                    job["loaded"] = True
                    job["pid"] = int(parts[0]) if parts[0].isdigit() else None
                    break
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)[:120]
        out["job"] = job
    log = str(LOCAL_MODEL.get("log_path") or "")
    if log:
        lp = Path(log).expanduser()
        if lp.is_file():
            try:
                lines = [ln for ln in lp.read_text(errors="replace").splitlines() if ln.strip()]
                out["recent"] = lines[-4:]
            except Exception:
                pass
    return out


def _push_hist(sid: str, key: str, value: Optional[float], now: float) -> None:
    if value is None:
        return
    hist = _state.setdefault("history", {})
    series: Deque = hist.setdefault(f"{sid}.{key}", deque(maxlen=HISTORY))
    series.append({"t": now, "v": value})


def poll_once() -> None:
    now = time.time()
    nodes: Dict[str, Dict[str, Any]] = {}
    threads = []
    results: Dict[str, Dict[str, Any]] = {}

    def one(meta: Dict[str, Any]) -> None:
        results[meta["id"]] = build_node(meta, now)

    for meta in NODES:  # one ssh per node, in parallel; ControlMaster keeps it cheap
        t = threading.Thread(target=one, args=(meta,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=SSH_TIMEOUT + 3)

    for meta in NODES:
        sid = meta["id"]
        nodes[sid] = results.get(sid) or {
            "id": sid, "label": meta["label"], "online": False, "status": "OFFLINE",
            "error": "collector miss", "last_seen": _persisted.get("last_seen", {}).get(sid),
        }

    inf = collect_inference()
    roles = []
    for sid, s in nodes.items():
        role = s.get("tp_role")
        if role and role.get("tp") is not None:
            roles.append({"node": sid, "label": s.get("label"), "hostname": s.get("hostname"),
                          "tp": role.get("tp"), "rank": role.get("rank"), "master": role.get("master")})
    roles.sort(key=lambda r: (r.get("rank") is None, 999 if r.get("rank") is None else r.get("rank")))
    if roles:
        tp = roles[0].get("tp")
        inf["tp_layout"] = {"tp": tp, "nodes": roles,
                            "summary": f"TP{tp}: " + " + ".join(f"{r['label']}(r{r['rank']})" for r in roles)}
    else:
        inf["tp_layout"] = {"tp": None, "nodes": [], "summary": "no tensor-parallel container found"}

    local = collect_local_model()

    for sid, s in nodes.items():
        _push_hist(sid, "gpu_util", (s.get("gpu") or {}).get("util_pct"), now)
        _push_hist(sid, "gpu_temp", (s.get("gpu") or {}).get("temp_c"), now)
        _push_hist(sid, "cpu_util", s.get("cpu_util_pct"), now)
        _push_hist(sid, "mem_pct", (s.get("memory") or {}).get("used_pct"), now)
        fab_rx, fab_n = 0.0, 0
        for n in s.get("nets") or []:
            if n.get("rx_mbps") is not None and "Fabric" in (n.get("label") or ""):
                fab_rx += n["rx_mbps"]
                fab_n += 1
        if fab_n:
            _push_hist(sid, "fabric_rx_mbps", round(fab_rx, 2), now)

    _save_persisted(_persisted)
    hist_out = {k: list(dq) for k, dq in _state.get("history", {}).items()}

    with _lock:
        _state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
        _state["updated_epoch"] = now
        _state["nodes"] = nodes
        _state["inference"] = inf
        _state["local_model"] = local
        _state["history"] = {k: deque(v, maxlen=HISTORY) for k, v in hist_out.items()}
        _state["_history_json"] = hist_out
        _state["notes"] = {
            "unified_memory": "GB10 (DGX Spark) uses unified memory; nvidia-smi memory.used/total is often N/A. "
                              "The dashboard uses /proc/meminfo + per-process GPU memory when reported.",
            "poll_secs": POLL_SECS,
        }


def poller() -> None:
    while True:
        try:
            poll_once()
        except Exception as e:
            with _lock:
                _state["collector_error"] = str(e)[:300]
        time.sleep(POLL_SECS)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__</title>
<style>
:root {
  --bg: #0b0f14;
  --panel: #121821;
  --panel2: #182131;
  --border: #243044;
  --text: #e6edf7;
  --muted: #8b9bb4;
  --green: #3ddc97;
  --yellow: #f0c14b;
  --red: #ff5c7a;
  --blue: #5aa9ff;
  --cyan: #4fd1c5;
  --orange: #ff9f43;
  --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  --sans: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px 18px 28px;
  background: radial-gradient(1200px 600px at 10% -10%, #152033 0%, var(--bg) 55%);
  color: var(--text); font-family: var(--sans);
}
h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: 0.02em; }
.sub { color: var(--muted); font-size: 12px; margin-bottom: 14px; }
.grid { display: grid; grid-template-columns: repeat(__COLS__, 1fr); gap: 12px; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
.row2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 12px; margin-top: 12px; }
@media (max-width: 1100px) { .row2 { grid-template-columns: 1fr; } }
.card {
  background: linear-gradient(180deg, var(--panel) 0%, #0e141d 100%);
  border: 1px solid var(--border); border-radius: 14px; padding: 14px 14px 12px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  min-height: 280px;
}
.card.offline { border-color: #5a2030; background: linear-gradient(180deg, #1a1014 0%, #120b0e 100%); }
.card h2 { margin: 0; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.badge {
  font-size: 11px; font-weight: 700; letter-spacing: 0.04em;
  padding: 3px 8px; border-radius: 999px; border: 1px solid transparent;
}
.badge.on { background: rgba(61,220,151,0.12); color: var(--green); border-color: rgba(61,220,151,0.35); }
.badge.busy { background: rgba(240,193,75,0.12); color: var(--yellow); border-color: rgba(240,193,75,0.35); }
.badge.off { background: rgba(255,92,122,0.14); color: var(--red); border-color: rgba(255,92,122,0.4); }
.meta { color: var(--muted); font-size: 11px; margin-top: 4px; font-family: var(--mono); }
.metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
.metric {
  background: var(--panel2); border: 1px solid var(--border); border-radius: 10px; padding: 8px 9px;
}
.metric .k { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; }
.metric .v { font-size: 15px; font-weight: 650; margin-top: 2px; font-family: var(--mono); }
.metric .v small { color: var(--muted); font-size: 11px; font-weight: 500; }
.bar { height: 6px; background: #0a1018; border-radius: 99px; margin-top: 6px; overflow: hidden; }
.bar > span { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--cyan)); }
.bar.hot > span { background: linear-gradient(90deg, var(--orange), var(--red)); }
.section { margin-top: 12px; }
.section h3 { margin: 0 0 6px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
table { width: 100%; border-collapse: collapse; font-size: 11px; font-family: var(--mono); }
td, th { padding: 3px 4px; text-align: left; border-bottom: 1px solid #1c2738; }
th { color: var(--muted); font-weight: 600; }
.sparkline { width: 100%; height: 28px; display: block; margin-top: 6px; }
.dock { font-size: 11px; font-family: var(--mono); color: var(--text); }
.dock .dim { color: var(--muted); }
.err { color: var(--red); font-size: 12px; margin-top: 8px; }
.pillrow { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.pill {
  background: var(--panel2); border: 1px solid var(--border); border-radius: 10px;
  padding: 8px 10px; min-width: 120px;
}
.pill .k { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.pill .v { font-size: 14px; font-weight: 650; margin-top: 2px; font-family: var(--mono); }
footer { margin-top: 14px; color: var(--muted); font-size: 11px; }
a { color: var(--blue); }
</style>
</head>
<body>
  <h1>__TITLE__</h1>
  <div class="sub">Live board · auto-refresh ~3–5s · <span id="stamp">…</span> · <a href="/api/status">/api/status</a></div>
  <div class="grid" id="nodes"></div>
  <div class="row2">
    <div class="card" id="inf-card"></div>
    <div class="card" id="mac-card"></div>
  </div>
  <footer>Read-only polls over SSH ControlMaster (one ssh per node per cycle, no sudo). Unified-memory GPUs: nvidia-smi total often N/A. Never starts, stops or changes anything.</footer>
<script>
const $ = (id) => document.getElementById(id);
function pctBar(p, hotAt=85) {
  if (p == null || Number.isNaN(p)) return '';
  const cls = p >= hotAt ? 'bar hot' : 'bar';
  return `<div class="${cls}"><span style="width:${Math.max(0,Math.min(100,p))}%"></span></div>`;
}
function sparkline(points, color='#5aa9ff') {
  if (!points || points.length < 2) return '';
  const vals = points.map(p => p.v);
  const min = Math.min(...vals), max = Math.max(...vals);
  const w = 240, h = 28, n = vals.length;
  const span = (max - min) || 1;
  let d = '';
  vals.forEach((v,i) => {
    const x = (i/(n-1))*w;
    const y = h - ((v-min)/span)*(h-4) - 2;
    d += (i? 'L':'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
  });
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
}
function fmt(v, unit='', digits=1) {
  if (v == null || v === undefined) return '—';
  if (typeof v === 'number') return v.toFixed(digits) + (unit? ' '+unit : '');
  return String(v) + (unit? ' '+unit : '');
}
function badge(status, online) {
  if (!online) return '<span class="badge off">OFFLINE</span>';
  if (status && status.startsWith('BUSY')) return `<span class="badge busy">${status}</span>`;
  return `<span class="badge on">${status || 'ONLINE'}</span>`;
}
function renderNode(s, hist) {
  const offline = !s.online;
  const g = s.gpu || {}, m = s.memory || {}, d = s.disk || {};
  const nets = (s.nets || []).filter(n => n.ip || (n.label && n.label !== n.iface)).slice(0,6);
  const docks = (s.dockers || []).slice(0,6);
  const gu = hist[`${s.id}.gpu_util`] || [];
  const gt = hist[`${s.id}.gpu_temp`] || [];
  let lastSeen = s.last_seen ? `Last seen: ${s.last_seen}` : 'Last seen: unknown';
  let role = '';
  if (s.tp_role) role = ` · TP${s.tp_role.tp} rank ${s.tp_role.rank}`;
  return `<div class="card ${offline?'offline':''}">
    <h2>${s.label} ${badge(s.status, s.online)}</h2>
    <div class="meta">${s.hostname || '—'} · ${s.lan || ''}${role}<br/>uptime ${s.uptime_human || '—'} · load ${s.loadavg || '—'} · ${offline? lastSeen : 'live'}</div>
    ${offline && s.error ? `<div class="err">${esc(s.error)}</div>` : ''}
    <div class="metrics">
      <div class="metric"><div class="k">GPU temp / util</div><div class="v">${fmt(g.temp_c,'°C',0)} <small>/ ${fmt(g.util_pct,'%',0)}</small></div>${pctBar(g.util_pct)}${sparkline(gu,'#f0c14b')}</div>
      <div class="metric"><div class="k">GPU power</div><div class="v">${fmt(g.power_w,'W',1)}</div><div class="meta" style="margin-top:4px">${g.name||''}</div></div>
      <div class="metric"><div class="k">Unified memory</div><div class="v">${fmt(m.used_gb,'/',1)}${fmt(m.total_gb,'GB',1)}</div>${pctBar(m.used_pct)}${m.note?`<div class="meta">${g.proc_mem_gb!=null?('GPU proc '+g.proc_mem_gb+' GB'):'nvidia-smi mem N/A'}</div>`:''}</div>
      <div class="metric"><div class="k">CPU util / temp</div><div class="v">${fmt(s.cpu_util_pct,'%',0)} <small>/ ${fmt(s.cpu_temp_c,'°C',0)}</small></div>${pctBar(s.cpu_util_pct)}</div>
      <div class="metric"><div class="k">Disk free</div><div class="v">${fmt(d.avail_tb,'TB',2)} <small>free</small></div>${pctBar(d.used_pct,90)}</div>
      <div class="metric"><div class="k">GPU temp hist</div>${sparkline(gt,'#ff9f43')}<div class="meta">last ${gu.length||0} samples</div></div>
    </div>
    <div class="section"><h3>Network (Mb/s)</h3>
      <table><tr><th>Link</th><th>IP</th><th>RX</th><th>TX</th></tr>
      ${nets.map(n=>`<tr><td>${n.label||n.iface}</td><td>${n.ip||'—'}</td><td>${n.rx_mbps==null?'…':n.rx_mbps.toFixed(2)}</td><td>${n.tx_mbps==null?'…':n.tx_mbps.toFixed(2)}</td></tr>`).join('') || '<tr><td colspan=4>—</td></tr>'}
      </table>
    </div>
    <div class="section"><h3>Docker</h3>
      <div class="dock">${docks.map(x=>`• ${esc(x.name)} <span class="dim">${esc(x.status)}</span>`).join('<br/>') || '—'}</div>
    </div>
  </div>`;
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function renderInference(g) {
  if (!g.enabled) return `<h2>Inference endpoint <span class="badge busy">NOT CONFIGURED</span></h2><div class="meta">Set "inference.enabled" in config.json to watch an OpenAI-compatible endpoint (/health + /v1/models).</div>`;
  const h = g.health || {};
  const streams = h.streams || g.streams || {};
  const ok = g.answering;
  return `<h2>${esc(g.label)} ${ok?'<span class="badge on">ANSWERING</span>':'<span class="badge off">DOWN</span>'}</h2>
    <div class="meta">${esc(g.base_url)} · models ${esc((g.model_ids||[]).join(', ')||'—')}</div>
    <div class="pillrow">
      <div class="pill"><div class="k">TP layout</div><div class="v">${esc((g.tp_layout&&g.tp_layout.summary)||'—')}</div></div>
      <div class="pill"><div class="k">requests_running</div><div class="v">${g.requests_running==null?'—':esc(g.requests_running)}</div></div>
      <div class="pill"><div class="k">busy</div><div class="v">${g.busy==null?'—':esc(g.busy)}</div></div>
      <div class="pill"><div class="k">streams</div><div class="v">${streams.decoding!=null?`${esc(streams.decoding)} dec / ${esc(streams.prefilling||0)} pre / max ${esc(streams.max||'?')}`:'—'}</div></div>
      <div class="pill"><div class="k">health</div><div class="v">${g.health_ok==null?'n/a':(g.health_ok?'ok':'fail')}</div></div>
    </div>
    ${g.error && !ok ? `<div class="err">${esc(g.error)}</div>`:''}
    <div class="section"><h3>Notes</h3><div class="meta">Chat hangs while /v1/models is still up usually mean a tensor-parallel collective is wedged (e.g. a peer node is dark). The dashboard only reads /health, /v1/models and the container Cmd.</div></div>`;
}
function renderLocal(m) {
  if (!m.enabled) return `<h2>Local model <span class="badge busy">NOT CONFIGURED</span></h2><div class="meta">Set "local_model.enabled" in config.json to watch a model server on this Mac.</div>`;
  const j = m.job;
  return `<h2>${esc(m.label)} ${m.up?'<span class="badge on">UP</span>':'<span class="badge off">DOWN</span>'}</h2>
    <div class="meta">${esc(m.base_url)}${j?(' · launchd '+esc(j.label)):''}</div>
    <div class="pillrow">
      <div class="pill"><div class="k">Loaded model</div><div class="v">${esc(m.loaded||'—')}</div></div>
      ${j?`<div class="pill"><div class="k">launchd job</div><div class="v">${j.loaded?(j.pid?('pid '+j.pid):'loaded'):'not loaded'}</div></div>`:''}
    </div>
    ${(m.recent||[]).length?`<div class="section"><h3>Log tail</h3><div class="dock dim">${m.recent.map(esc).join('<br/>')}</div></div>`:''}`;
}
async function tick() {
  try {
    const r = await fetch('/api/status', {cache:'no-store'});
    const j = await r.json();
    $('stamp').textContent = j.updated_at || '…';
    const order = j.node_order || Object.keys(j.nodes||{});
    $('nodes').innerHTML = order.map(id => renderNode(j.nodes[id]||{id,label:id,online:false}, j.history||{})).join('');
    $('inf-card').innerHTML = renderInference(j.inference||{});
    $('mac-card').innerHTML = renderLocal(j.local_model||{});
  } catch (e) {
    $('stamp').textContent = 'fetch error: ' + e;
  }
}
tick();
setInterval(tick, 3500);
</script>
</body>
</html>
"""

HTML = (HTML.replace("__TITLE__", TITLE.replace("&", "&amp;").replace("<", "&lt;"))
        .replace("__COLS__", str(max(1, min(3, len(NODES))))))

if security is not None:
    HTML = security.inject_html(HTML)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        # quiet
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path in ("/api/status", "/api/status.json"):
            with _lock:
                hist = _state.get("_history_json") or {
                    k: list(v) for k, v in (_state.get("history") or {}).items()
                }
                payload = {
                    "updated_at": _state.get("updated_at"),
                    "updated_epoch": _state.get("updated_epoch"),
                    "node_order": [n["id"] for n in NODES],
                    "nodes": _state.get("nodes") or {},
                    "inference": _state.get("inference") or {},
                    "local_model": _state.get("local_model") or {},
                    "history": hist,
                    "notes": _state.get("notes") or {},
                    "collector_error": _state.get("collector_error"),
                }
            body = json.dumps(payload, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/healthz":
            self._send(200, b'{"ok":true}', "application/json")
            return
        if security is not None and path.startswith("/api/security"):
            res = security.handle_get(path)
            if res is not None:
                self._send(*res)
                return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        path, _, query = self.path.partition("?")
        if security is not None and path.startswith("/api/security"):
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= 4096:
                self.rfile.read(n)
            res = security.handle_post(path, self.headers, query)
            if res is not None:
                self._send(*res)
                return
        self._send(404, b"not found", "text/plain")


def main() -> None:
    print(f"node-dashboard listening on http://{BIND}:{PORT}/ ({len(NODES)} nodes, config {CONFIG['_path']})", flush=True)
    if BIND not in ("127.0.0.1", "::1", "localhost"):
        print("WARNING: bound to a non-loopback address; this dashboard has no authentication.", flush=True)
    # Bind first so /healthz works while the initial SSH poll runs (an offline node can take a few seconds).
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    t = threading.Thread(target=poller, daemon=True)
    t.start()
    if security is not None:
        # Security collectors: read-only, reuse the same SSH ControlMaster helper.
        security.start(NODES, _ssh_base, STATE_PATH.parent / "security", cfg=CONFIG)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
