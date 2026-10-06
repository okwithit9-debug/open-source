#!/usr/bin/env python3
"""Security monitoring layer for the node dashboard (MONITOR ONLY).

Collects cheap, read-only security signals from the local Mac and the Linux /
DGX nodes listed in config.json (over the dashboard's existing SSH
ControlMaster), compares them to a saved baseline, keeps an alerts feed, and
serves /api/security.

Never changes firewall / sshd / users / services / packages / network.
Never reads or stores passwords or private keys (only key *fingerprints*,
file hashes, and sshd log lines).

Cadence (env overridable):
  NODE_SEC_MAC    20s   Mac (local, cheap): firewall/stealth, listeners, sessions, launch agents
                        + other integrity hashes, sshd config, ARP
  NODE_SEC_FAST   30s   nodes: logins, sessions, listening ports, docker, tunnel readiness
  NODE_SEC_SLOW   120s  nodes: sshd config, firewall, integrity hashes, kernel log, update notifier
                        (batched into the SAME single ssh exec as the 30s check, so one ssh
                        command per node per cycle, over the existing ControlMaster, no sudo)
  POST /api/security/refresh  immediate full recheck (rate-limited to one per 5 s)
  apt list --upgradable  hourly (cached)
  macOS softwareupdate -l every 6h (cached, background)
  macOS `log show` sshd 1h every 15 min (24h only if Remote Login is on, hourly)
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

MAC_SECS = float(os.environ.get("NODE_SEC_MAC", "20"))
FAST_SECS = float(os.environ.get("NODE_SEC_FAST", "30"))
SLOW_SECS = float(os.environ.get("NODE_SEC_SLOW", "120"))
TICK_SECS = 5.0
APT_SECS = float(os.environ.get("NODE_SEC_APT", "3600"))
MAC_SWU_SECS = float(os.environ.get("NODE_SEC_SWU", str(6 * 3600)))
MAC_LOG1H_SECS = float(os.environ.get("NODE_SEC_MACLOG", "900"))
MAC_LOG24_SECS = 3600.0
EXT_PROBE_SECS = 300.0
SSH_TIMEOUT = float(os.environ.get("NODE_SEC_SSH_TIMEOUT", "12"))

MAC_ID = "mac"
# Defaults only. configure(cfg) replaces these from config.json at start-up.
NODE_ORDER: List[str] = [MAC_ID]
NODE_LABELS: Dict[str, str] = {MAC_ID: "Mac"}

# Wildcard (all-interface) listeners that are expected per node (config: "expected_ports").
EXPECTED_ANY: Dict[str, Dict[int, str]] = {MAC_ID: {49152: "rapportd (Apple Continuity)"}}
# Friendly names for common ports (config "security.port_names" adds/overrides).
PORT_NAMES: Dict[int, str] = {
    22: "sshd", 53: "systemd-resolved", 631: "CUPS", 3350: "xrdp-sesman", 3389: "RDP",
    3390: "GNOME RDP (user session)", 5900: "Screen Sharing (VNC)", 8080: "web UI",
    8095: "this dashboard", 8888: "LLM API", 11434: "ollama", 20241: "cloudflared metrics",
    9222: "Chrome DevTools", 49152: "rapportd",
}
# Friendly names for LAN IPs in the ARP view (config "security.lan_labels").
LAN_LABELS: Dict[str, str] = {}
# Home IPv6 prefixes. Logins from these are "home", not "public IP".
# config "security.home_ipv6_prefixes" + optional env extras (comma-separated CIDRs).
# Learned automatically too: every global /64 seen on the Mac (ifconfig) or on a node
# (`ip -6 addr`, REMOTE_FAST) is remembered for HOME_V6_REMEMBER_SECS (30 days).
HOME_V6_STATIC: List[str] = [
    x.strip() for x in os.environ.get("NODE_SEC_HOME_V6", "").split(",") if x.strip()]
HOME_V6_REMEMBER_SECS = float(os.environ.get("NODE_SEC_HOME_V6_DAYS", "30")) * 86400
# Optional Cloudflare tunnel check (config "security.tunnel"), e.g.
# {"node": "node1", "container": "cloudflared", "metrics_port": 20241, "public_url": "https://example.com/"}
TUNNEL: Optional[Dict[str, Any]] = None
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def configure(cfg: Dict[str, Any]) -> None:
    """Load node list, expected ports, LAN labels, home IPv6 prefixes and tunnel from config."""
    global NODE_ORDER, NODE_LABELS, EXPECTED_ANY, LAN_LABELS, HOME_V6_STATIC, TUNNEL
    local = cfg.get("local") or {}
    sec = cfg.get("security") or {}
    order, labels = [MAC_ID], {MAC_ID: local.get("label") or "Mac"}
    exp: Dict[str, Dict[int, str]] = {
        MAC_ID: {int(k): str(v) for k, v in (local.get("expected_ports") or {49152: "rapportd"}).items()}}
    for n in cfg.get("nodes") or []:
        order.append(n["id"])
        labels[n["id"]] = n.get("label") or n["id"]
        exp[n["id"]] = {int(k): str(v) for k, v in (n.get("expected_ports") or {22: "sshd"}).items()}
    NODE_ORDER, NODE_LABELS, EXPECTED_ANY = order, labels, exp
    PORT_NAMES.update({int(k): str(v) for k, v in (sec.get("port_names") or {}).items()})
    LAN_LABELS = {str(k): str(v) for k, v in (sec.get("lan_labels") or {}).items()}
    HOME_V6_STATIC = list(sec.get("home_ipv6_prefixes") or []) + HOME_V6_STATIC
    t = sec.get("tunnel")
    if t and t.get("node") in labels and _NAME_RE.match(str(t.get("container", ""))):
        TUNNEL = {"node": t["node"], "container": t["container"],
                  "metrics_port": int(t.get("metrics_port") or 20241), "public_url": t.get("public_url") or ""}
    else:
        TUNNEL = None
    for nid in NODE_ORDER:
        _raw.setdefault(nid, {})


# --------------------------------------------------------------------------
# Remote (Linux) scripts. Read-only. No sudo. Sent over the dashboard's
# existing SSH ControlMaster via `bash -s`.
# --------------------------------------------------------------------------
REMOTE_FAST = r"""
set +e
export LC_ALL=C
echo "NOW=$(date +%s)"
ss -Htlnp 2>/dev/null | awk '{p=""; for(i=6;i<=NF;i++) p=p $i; print "L|" $4 "|" p}'
AUTHFILE=/var/log/auth.log
if [ -r "$AUTHFILE" ] && head -c 4 "$AUTHFILE" | grep -qE '^[0-9]{4}'; then AUTH_SRC=auth.log; else AUTH_SRC=journal; fi
echo "AUTH_SRC=$AUTH_SRC"
NOWE=$(date +%s)
if [ "$AUTH_SRC" = "auth.log" ]; then
  grep -aE 'sshd\[[0-9]+\]: (Failed |Invalid user |Accepted )' "$AUTHFILE" 2>/dev/null
else
  journalctl -t sshd --since "-24h" --no-pager -q -o short-iso 2>/dev/null | grep -aE 'sshd\[[0-9]+\]: (Failed |Invalid user |Accepted )'
fi | awk -v now="$NOWE" '
function dfc(y,m,d,  era,yoe,doy,doe){ if(m<=2) y--; era=int(y/400); yoe=y-era*400; doy=int((153*(m+(m>2?-3:9))+2)/5)+d-1; doe=yoe*365+int(yoe/4)-int(yoe/100)+doy; return era*146097+doe-719468 }
function ep(ts,  e,rest,off,sg,oh,om){
  e=dfc(substr(ts,1,4)+0,substr(ts,6,2)+0,substr(ts,9,2)+0)*86400+substr(ts,12,2)*3600+substr(ts,15,2)*60+substr(ts,18,2);
  rest=substr(ts,20); sub(/^\.[0-9]+/,"",rest);
  if (rest ~ /^[+-][0-9][0-9]:?[0-9][0-9]/) { sg=(substr(rest,1,1)=="-")?-1:1; oh=substr(rest,2,2)+0; om=substr(rest,length(rest)-1,2)+0; e-=sg*(oh*3600+om*60) }
  return e }
BEGIN { c1=now-3600; c24=now-86400 }
{
  t=ep($1); if (t < c24 || t > now+300) next;
  pid=""; if (match($0, /sshd\[[0-9]+\]/)) pid=substr($0, RSTART+5, RLENGTH-6);
  ip=""; user=""; for(i=1;i<=NF;i++){ if($i=="from") ip=$(i+1); }
  if ($0 ~ /Accepted /) {
    meth=""; for(i=1;i<=NF;i++){ if($i=="Accepted"){meth=$(i+1)} if($i=="for" && user==""){user=$(i+1)} }
    k=user "|" ip "|" meth; a24[k]++; if (t>=c1) a1[k]++; if (t>alast[k]) alast[k]=t; next
  }
  for(i=1;i<=NF;i++){ if($i=="user"){user=$(i+1); break} if($i=="for" && $(i+1)!="invalid"){user=$(i+1)} }
  typ = ($0 ~ /Failed password/) ? "password" : (($0 ~ /Failed publickey/) ? "publickey" : (($0 ~ /Invalid user/) ? "invalid-user" : "other"));
  if (pid in seen) next; seen[pid]=1;
  f24++; if (t>=c1) f1++; ip24[ip]++; if (t>=c1) ip1[ip]++;
  n++; last[n%12]=t "|" typ "|" user "|" ip
}
END {
  print "F1=" (f1+0); print "F24=" (f24+0);
  for (k in ip24) print "FIP|" k "|" ip24[k] "|" (ip1[k]+0);
  for (i=n-11;i<=n;i++) if (i>0) print "FL|" last[i%12];
  for (k in a24) print "A|" k "|" a24[k] "|" (a1[k]+0) "|" alast[k];
}'
who 2>/dev/null | awk '{print "W|" $0}'
ss -Htn state established 2>/dev/null | awk '$3 ~ /:(22|3389|3390)$/ {print "E|" $3 "|" $4}'
last -n 12 -w -F -i 2>/dev/null | grep -vE '^(reboot|wtmp|$)' | awk '{print "LA|" $0}'
if command -v docker >/dev/null 2>&1; then
  docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null | awk '{print "D|" $0}'
  if [ -n "__TUNNEL_CONTAINER__" ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx '__TUNNEL_CONTAINER__'; then
    echo "CF_READY=$(curl -s -m 2 http://127.0.0.1:__TUNNEL_PORT__/ready 2>/dev/null | tr -d '\n' | cut -c1-200)"
  fi
fi
ip -6 -o addr show scope global 2>/dev/null | awk '{print "V6|" $4}'
echo "OK=1"
"""

REMOTE_SLOW = r"""
set +e
export LC_ALL=C
for f in /etc/ssh/sshd_config.d/*.conf /etc/ssh/sshd_config; do
  [ -r "$f" ] || continue
  awk -v f="$f" 'tolower($1)=="match"{exit} /^[ \t]*(PasswordAuthentication|PermitRootLogin|KbdInteractiveAuthentication|ChallengeResponseAuthentication|PubkeyAuthentication|PermitEmptyPasswords|UsePAM|Port|ListenAddress|AllowUsers|MaxAuthTries)[ \t]/ {print "SSHCFG|" f "|" $1 "|" $2}' "$f"
done
sshd -T 2>/dev/null | grep -iE '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication|pubkeyauthentication|permitemptypasswords) ' | awk '{print "SSHT|" $1 "|" $2}'
echo "UFW_CONF=$(grep -E '^ENABLED=' /etc/ufw/ufw.conf 2>/dev/null | cut -d= -f2)"
echo "UFW_SVC=$(systemctl is-active ufw 2>/dev/null)"
echo "NFT_SVC=$(systemctl is-active nftables 2>/dev/null)"
echo "FWD_SVC=$(systemctl is-active firewalld 2>/dev/null)"
if [ -f "$HOME/.ssh/authorized_keys" ]; then
  echo "AK_SHA=$(sha256sum "$HOME/.ssh/authorized_keys" | cut -d' ' -f1)"
  ssh-keygen -lf "$HOME/.ssh/authorized_keys" 2>/dev/null | awk '{print "AK|" $0}'
else
  echo "AK_SHA=absent"
fi
for f in /etc/sudoers /etc/sudoers.d/*; do
  [ -e "$f" ] || continue
  if [ -r "$f" ]; then echo "SUD|$f|sha256:$(sha256sum "$f" | cut -d' ' -f1)"; else echo "SUD|$f|meta:$(stat -c '%s:%Y:%U:%a' "$f")"; fi
done
CT=$(crontab -l 2>/dev/null)
if [ -n "$CT" ]; then echo "CRONU=sha256:$(printf '%s' "$CT" | sha256sum | cut -d' ' -f1):lines=$(printf '%s\n' "$CT" | grep -cvE '^\s*(#|$)')"; else echo "CRONU=none"; fi
for f in /etc/crontab /etc/cron.d/*; do [ -r "$f" ] && echo "CRONS|$f|$(sha256sum "$f" | cut -d' ' -f1)"; done
systemctl list-unit-files --state=enabled --no-legend --no-pager 2>/dev/null | awk '{print "U|" $1}'
awk -F: '$7 !~ /(nologin|false|sync|halt|shutdown)$/ {print "PU|" $1 "|" $3 "|" $7}' /etc/passwd
awk -F: '$3==0 {print "UID0|" $1}' /etc/passwd
journalctl _TRANSPORT=kernel --since "-24h" --no-pager -q -o short-iso 2>/dev/null | awk '
  /NVRM: Xid/ {c="xid"}
  /soft lockup/ {c="soft_lockup"}
  /blocked for more than|hung_task/ {c="hung_task"}
  /Out of memory: Kill|oom-kill:|Killed process/ && !/NVRM/ {c="oom_kill"}
  /NV_ERR_NO_MEMORY/ {c="nvrm_nomem"}
  /Kernel panic|Oops:|BUG: / && !/soft lockup/ {c="kernel_bug"}
  c!="" {n[c]++; l[c]=substr($0,1,220); if (!(c in f)) f[c]=$1; c=""}
  END {for (k in n) print "K|" k "|" n[k] "|" f[k] "|" l[k]}'
if [ -r /var/lib/update-notifier/updates-available ]; then
  awk 'NF {print "UPN|" $0}' /var/lib/update-notifier/updates-available
  echo "UPN_MTIME=$(stat -c %Y /var/lib/update-notifier/updates-available)"
fi
echo "APT_STAMP=$(stat -c %Y /var/lib/apt/periodic/update-success-stamp 2>/dev/null)"
[ -f /var/run/reboot-required ] && echo "REBOOT_REQ=1"
if [ "${DO_APT:-0}" = "1" ]; then
  APT=$(apt list --upgradable 2>/dev/null | grep -v '^Listing')
  echo "APT_UPG=$(printf '%s\n' "$APT" | grep -c '/')"
  echo "APT_SEC=$(printf '%s\n' "$APT" | grep -c -- '-security')"
fi
echo "OK=1"
"""

_lock = threading.RLock()
_ssh_base: Optional[Callable[..., List[str]]] = None
_nodes: List[Dict[str, Any]] = []
DATA_DIR = Path.home() / ".cache" / "node-dashboard" / "security"
BASELINE_PATH = DATA_DIR / "baseline.json"
STATE_PATH = DATA_DIR / "state.json"
ALERTS_PATH = DATA_DIR / "alerts.jsonl"

_raw: Dict[str, Dict[str, Any]] = {MAC_ID: {}}
_ts: Dict[str, float] = {}
_view: Dict[str, Any] = {"updated_at": None, "nodes": {}, "overall": {}}
_baseline: Dict[str, Any] = {}
_sstate: Dict[str, Any] = {"active": {}, "lan_seen": {}}


def _now_str(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(t or time.time()))


def _load_json(p: Path, default: Any) -> Any:
    try:
        if p.is_file():
            return json.loads(p.read_text())
    except Exception:
        pass
    return default


def _save_json(p: Path, data: Any) -> None:
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
        os.replace(tmp, p)
    except Exception:
        pass


def _run(cmd: List[str], timeout: float = 10, input_text: Optional[str] = None) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input_text)
        return p.returncode, (p.stdout or "") + ("" if p.returncode == 0 else (p.stderr or ""))
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, str(e)[:200]


_local_nets: List[Any] = []  # home IPv6 nets: HOME_V6_STATIC + /64s learned from the Mac and nodes (30 days)


def _learn_v6(addrs: List[str], source: str) -> None:
    """Remember the global /64 of each address (Mac or node), then rebuild _local_nets."""
    now = time.time()
    changed = False
    with _lock:
        seen = _sstate.setdefault("v6_seen", {})
        for raw in addrs:
            try:
                a = ipaddress.ip_address(raw.split("/")[0].split("%")[0])
            except ValueError:
                continue
            if a.version != 6 or not a.is_global:
                continue
            key = str(ipaddress.ip_network(f"{a}/64", strict=False))
            ent = seen.get(key) or {}
            if key not in seen or now - float(ent.get("last", 0)) > 3600:
                changed = True
            srcs = sorted(set(ent.get("sources", [])) | {source})
            seen[key] = {"first": ent.get("first", now), "last": now, "sources": srcs}
        for key in [k for k, v in seen.items() if now - float((v or {}).get("last", 0)) > HOME_V6_REMEMBER_SECS]:
            seen.pop(key, None)
            changed = True
    _rebuild_local_nets()
    if changed:
        _save_json(STATE_PATH, _sstate)


def _rebuild_local_nets() -> None:
    global _local_nets
    nets = set()
    for c in HOME_V6_STATIC:
        try:
            nets.add(ipaddress.ip_network(c, strict=False))
        except ValueError:
            pass
    now = time.time()
    with _lock:
        for k, v in (_sstate.get("v6_seen") or {}).items():
            if now - float((v or {}).get("last", 0)) <= HOME_V6_REMEMBER_SECS:
                try:
                    nets.add(ipaddress.ip_network(k, strict=False))
                except ValueError:
                    pass
    _local_nets = sorted(nets, key=str)


def _refresh_local_nets() -> None:
    _, o = _run(["ifconfig"], 5)
    _learn_v6(re.findall(r"inet6 ([0-9a-fA-F:]+)", o), "mac")


def _is_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.strip("[]").split("%")[0])
    except ValueError:
        return False
    if a.is_private or a.is_loopback or a.is_link_local:
        return False
    return not any(a in n for n in _local_nets)


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _epoch_str(v: Any) -> str:
    e = _f(v)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e)) if e else str(v or "")


def _iso_local(ts: str) -> str:
    """'2026-10-05T14:53:11-07:00' (any offset) -> local time string."""
    try:
        from datetime import datetime
        t = ts.strip()
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        m = re.match(r"^(.*[+-]\d\d)(\d\d)$", t)
        if m and ":" not in t[-5:]:
            t = m.group(1) + ":" + m.group(2)
        return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(datetime.fromisoformat(t).timestamp()))
    except Exception:
        return ts


def _scope(addr: str) -> str:
    a = addr.strip("[]").split("%")[0]
    if a in ("*", "0.0.0.0", "::", ""):
        return "any"
    if a.startswith("127.") or a == "::1" or a == "localhost":
        return "lo"
    return "lan"


def _port_key(scope: str, port: int) -> str:
    # Ephemeral high ports bound to a specific IP / loopback change every boot; group them.
    if scope != "any" and port >= 32768:
        return f"{scope}:eph"
    return f"{scope}:{port}"


def _sort_listen(lst: List[Dict[str, Any]]) -> None:
    lst.sort(key=lambda r: ({"any": 0, "lan": 1, "lo": 2}[r["scope"]], r["port"]))


# ------------------------------ Linux nodes --------------------------------
def _ssh(meta: Dict[str, Any], script: str, timeout: float) -> Tuple[bool, str]:
    assert _ssh_base is not None
    cmd = _ssh_base(meta["ssh"], hostname_override=meta.get("ssh_hostname")) + ["bash", "-s"]
    rc, out = _run(cmd, timeout=timeout, input_text=script)
    return (rc == 0 and "OK=1" in out), out


def _parse_listen_linux(lines: List[str]) -> List[Dict[str, Any]]:
    res, seen = [], set()
    for ln in lines:
        parts = ln.split("|", 2)
        if len(parts) < 2 or ":" not in parts[1]:
            continue
        addr, port_s = parts[1].rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            continue
        m = re.search(r'\(\("([^"]+)"', parts[2] if len(parts) > 2 else "")
        if (addr, port) in seen:
            continue
        seen.add((addr, port))
        sc = _scope(addr)
        res.append({"addr": addr, "port": port, "scope": sc, "key": _port_key(sc, port),
                    "process": m.group(1) if m else "", "name": PORT_NAMES.get(port, "")})
    _sort_listen(res)
    return res


def parse_fast(out: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {"fail_ips": [], "fail_last": [], "accepted": [], "who": [], "sessions": [],
                         "last": [], "docker": [], "v6": [], "kv": {}}
    L = []
    for ln in out.splitlines():
        p = ln.split("|")
        if ln.startswith("L|"):
            L.append(ln)
        elif ln.startswith("FIP|") and len(p) >= 4:
            d["fail_ips"].append({"ip": p[1], "c24": int(p[2] or 0), "c1": int(p[3] or 0), "public": _is_public(p[1])})
        elif ln.startswith("FL|") and len(p) >= 5:
            d["fail_last"].append({"ts": _epoch_str(p[1]), "epoch": _f(p[1]), "type": p[2], "user": p[3], "ip": p[4]})
        elif ln.startswith("A|") and len(p) >= 7:
            d["accepted"].append({"user": p[1], "ip": p[2], "method": p[3], "c24": int(p[4] or 0),
                                  "c1": int(p[5] or 0), "last": _epoch_str(p[6]), "public": _is_public(p[2])})
        elif ln.startswith("W|"):
            d["who"].append(re.sub(r"\s+", " ", ln[2:].strip()))
        elif ln.startswith("E|") and len(p) >= 3:
            d["sessions"].append({"local": p[1], "peer": p[2]})
        elif ln.startswith("V6|") and len(p) >= 2:
            d["v6"].append(p[1].strip())
        elif ln.startswith("LA|"):
            d["last"].append(re.sub(r"\s+", " ", ln[3:].strip()))
        elif ln.startswith("D|"):
            q = ln[2:].split("|")
            if len(q) >= 3:
                d["docker"].append({"name": q[0], "image": q[1], "status": q[2]})
        elif re.match(r"^[A-Z0-9_]+=", ln):
            k, v = ln.split("=", 1)
            d["kv"][k] = v
    d["listen"] = _parse_listen_linux(L)
    d["fail_ips"].sort(key=lambda x: -x["c24"])
    d["accepted"].sort(key=lambda x: -x["c24"])
    d["fail_last"].sort(key=lambda x: x.get("epoch") or 0, reverse=True)
    try:
        d["fail_1h"] = int(d["kv"].get("F1", "0"))
        d["fail_24h"] = int(d["kv"].get("F24", "0"))
    except ValueError:
        d["fail_1h"] = d["fail_24h"] = None
    d["auth_src"] = d["kv"].get("AUTH_SRC")
    cf = d["kv"].get("CF_READY")
    if cf is not None:
        try:
            d["cf_ready"] = json.loads(cf) if cf else None
        except Exception:
            d["cf_ready"] = {"raw": cf[:120]}
    return d


SSHD_DEFAULTS = {"passwordauthentication": "yes", "permitrootlogin": "prohibit-password",
                 "kbdinteractiveauthentication": "yes", "pubkeyauthentication": "yes",
                 "permitemptypasswords": "no", "usepam": "no"}


def _sshd_effective(cfg_lines: List[Tuple[str, str, str]], t_lines: Dict[str, str]) -> Dict[str, Any]:
    eff: Dict[str, str] = {}
    src: Dict[str, str] = {}
    for f, k, v in cfg_lines:  # first value wins (sshd semantics); Include'd .d files come first
        kl = k.lower()
        if kl == "challengeresponseauthentication":
            kl = "kbdinteractiveauthentication"
        if kl not in eff:
            eff[kl], src[kl] = v.lower(), f
    for k, v in SSHD_DEFAULTS.items():
        if k not in eff:
            eff[k], src[k] = v, "OpenSSH default"
    method = "config parse (sshd -T needs root)"
    if t_lines:
        method = "sshd -T"
        for k, v in t_lines.items():
            eff[k], src[k] = v, "sshd -T"
    keys = ("passwordauthentication", "permitrootlogin", "kbdinteractiveauthentication", "pubkeyauthentication",
            "permitemptypasswords", "usepam")
    return {"effective": {k: eff[k] for k in keys if k in eff}, "source": src, "method": method}


def parse_slow(out: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {"kv": {}, "ak": [], "sudoers": {}, "cron_sys": {}, "units": [], "users": [],
                         "uid0": [], "kernel": {}, "upn": []}
    cfg: List[Tuple[str, str, str]] = []
    tl: Dict[str, str] = {}
    for ln in out.splitlines():
        p = ln.split("|")
        if ln.startswith("SSHCFG|") and len(p) >= 4:
            cfg.append((p[1], p[2], p[3]))
        elif ln.startswith("SSHT|") and len(p) >= 3:
            tl[p[1].lower()] = p[2].lower()
        elif ln.startswith("AK|"):
            d["ak"].append(ln[3:].strip())
        elif ln.startswith("SUD|"):
            q = ln.split("|", 2)
            if len(q) == 3:
                d["sudoers"][q[1]] = q[2]
        elif ln.startswith("CRONS|"):
            q = ln.split("|", 2)
            if len(q) == 3:
                d["cron_sys"][q[1]] = q[2]
        elif ln.startswith("U|"):
            d["units"].append(ln[2:].strip())
        elif ln.startswith("PU|"):
            d["users"].append(ln[3:].strip())
        elif ln.startswith("UID0|"):
            d["uid0"].append(ln[5:].strip())
        elif ln.startswith("K|"):
            q = ln.split("|", 4)
            if len(q) >= 5:
                d["kernel"][q[1]] = {"count": int(q[2] or 0), "first": q[3], "last_line": q[4]}
        elif ln.startswith("UPN|"):
            d["upn"].append(ln[4:].strip())
        elif re.match(r"^[A-Z0-9_]+=", ln):
            k, v = ln.split("=", 1)
            d["kv"][k] = v
    d["sshd"] = _sshd_effective(cfg, tl)
    d["sshd"]["lines"] = [f"{os.path.basename(f)}: {k} {v}" for f, k, v in cfg]
    up: Dict[str, Any] = {"source": "/var/lib/update-notifier/updates-available"}
    txt = " ".join(d["upn"])
    m = re.search(r"(\d+) updates? can be applied immediately", txt)
    up["total"] = int(m.group(1)) if m else (0 if txt else None)
    m = re.search(r"(\d+) of these updates? (?:is a|are) standard security updates?", txt)
    sec = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) of these updates? (?:is an|are) ESM Apps security updates?", txt)
    esm = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) additional security updates? can be applied with ESM", txt)
    up["esm_not_enabled_security"] = int(m.group(1)) if m else 0
    up.update({"security": sec + esm, "security_standard": sec, "security_esm": esm,
               "reboot_required": d["kv"].get("REBOOT_REQ") == "1"})
    for k, name in (("UPN_MTIME", "notifier_mtime"), ("APT_STAMP", "apt_update_stamp")):
        try:
            v = d["kv"].get(k)
            up[name] = _now_str(float(v)) if v else None
            up[name + "_epoch"] = float(v) if v else None
        except ValueError:
            pass
    d["updates"] = up
    return d


SLOW_MARK = "=====NODE-SEC-SLOW====="


def _remote_fast() -> str:
    tc = TUNNEL["container"] if TUNNEL else ""
    tp = str(TUNNEL["metrics_port"]) if TUNNEL else "20241"
    return REMOTE_FAST.replace("__TUNNEL_CONTAINER__", tc).replace("__TUNNEL_PORT__", tp)


def collect_node(meta: Dict[str, Any], do_slow: bool, do_apt: bool) -> None:
    sid = meta["id"]
    # One batched ssh exec per node per cycle: fast checks, plus the slow checks when due.
    script = _remote_fast()
    remote_fast = script
    if do_slow:
        script = remote_fast + f'\necho "{SLOW_MARK}"\n' + ("DO_APT=1\n" if do_apt else "") + REMOTE_SLOW
    ok, both = _ssh(meta, script, SSH_TIMEOUT + (8 if do_slow else 0))
    out, sep, out2 = (both or "").partition(SLOW_MARK + "\n")
    if not sep:
        out, sep, out2 = (both or "").partition(SLOW_MARK)
    with _lock:
        r = _raw.setdefault(sid, {})
        r["reachable"] = ok
        if ok:
            r["fast"] = parse_fast(out)
            r["fast_at"] = time.time()
            r["error"] = None
        else:
            lines = (out or "").strip().splitlines()
            r["error"] = lines[-1][:200] if lines else "ssh failed"
    if ok:
        try:
            _learn_v6((_raw.get(sid, {}).get("fast") or {}).get("v6") or [], sid)
        except Exception:
            pass
    if ok and do_slow:
        ok2 = bool(sep) and "OK=1" in out2
        if ok2:
            slow = parse_slow(out2)
            with _lock:
                prev = (_raw.get(sid, {}).get("slow") or {}).get("kv", {})
                if do_apt:
                    slow["kv"]["APT_AT"] = str(time.time())
                elif prev.get("APT_UPG") is not None:
                    for k in ("APT_UPG", "APT_SEC", "APT_AT"):
                        if prev.get(k) is not None:
                            slow["kv"][k] = prev[k]
                _raw[sid]["slow"] = slow
                _raw[sid]["slow_at"] = time.time()


# ------------------------------ Mac (local) -------------------------------
NETSTAT_RE = re.compile(
    r"^(tcp[46]+)\s+\d+\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+\d+\s+\d+\s+\d+\s+\d+\s+(.+?):(\d+)\s+[0-9a-fA-F]{5}\s"
)


def _mac_netstat() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    _, out = _run(["netstat", "-anv", "-p", "tcp"], timeout=8)
    uniq: Dict[Tuple[str, int], Dict[str, Any]] = {}
    est: List[Dict[str, Any]] = []
    for ln in out.splitlines():
        m = NETSTAT_RE.match(ln)
        if not m:
            continue
        proto, local, foreign, state, proc, pid = m.groups()
        if "." not in local:
            continue
        addr, port_s = local.rsplit(".", 1)
        try:
            port = int(port_s)
        except ValueError:
            continue
        if state == "LISTEN":
            sc = _scope(addr)
            uniq.setdefault((sc, port), {"addr": addr, "port": port, "scope": sc, "key": _port_key(sc, port),
                                         "process": f"{proc.strip()}:{pid}", "name": PORT_NAMES.get(port, "")})
        elif state == "ESTABLISHED" and "." in foreign:
            faddr, fport = foreign.rsplit(".", 1)
            est.append({"local": local, "lport": port, "faddr": faddr, "fport": fport, "process": proc.strip()})
    lst = list(uniq.values())
    _sort_listen(lst)
    return lst, est


def _mac_firewall() -> Dict[str, Any]:
    fw = "/usr/libexec/ApplicationFirewall/socketfilterfw"
    res: Dict[str, Any] = {}
    _, o = _run([fw, "--getglobalstate"], 5)
    res["app_firewall"] = "on" if re.search(r"is enabled|State = [12]", o) else ("off" if "disabled" in o else o.strip()[:80])
    _, o = _run([fw, "--getstealthmode"], 5)
    res["stealth"] = "on" if re.search(r"mode is on|is enabled", o) else ("off" if re.search(r"mode is off|disabled", o) else o.strip()[:80])
    _, o = _run([fw, "--getblockall"], 5)
    res["block_all"] = "off" if "disabled" in o else ("on" if o.strip() else "?")
    _, o = _run(["fdesetup", "status"], 5)
    res["filevault"] = "on" if "FileVault is On" in o else ("off" if "FileVault is Off" in o else o.strip()[:80])
    _, o = _run(["csrutil", "status"], 5)
    res["sip"] = "on" if "status: enabled" in o else ("off" if "disabled" in o else o.strip()[:80])
    _, o = _run(["spctl", "--status"], 5)
    res["gatekeeper"] = "on" if "assessments enabled" in o else ("off" if "disabled" in o else o.strip()[:80])
    return res


def _mac_sshd_cfg() -> Dict[str, Any]:
    cfg: List[Tuple[str, str, str]] = []
    keys = ("passwordauthentication", "permitrootlogin", "kbdinteractiveauthentication",
            "challengeresponseauthentication", "pubkeyauthentication", "permitemptypasswords", "usepam")
    for f in sorted(Path("/etc/ssh/sshd_config.d").glob("*")) + [Path("/etc/ssh/sshd_config")]:
        try:
            for raw in f.read_text(errors="replace").splitlines():
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                parts = s.split(None, 1)
                if parts[0].lower() == "match":
                    break
                if parts[0].lower() in keys and len(parts) > 1:
                    cfg.append((str(f), parts[0], parts[1].split()[0]))
        except Exception:
            continue
    r = _sshd_effective(cfg, {})
    r["lines"] = [f"{os.path.basename(f)}: {k} {v}" for f, k, v in cfg]
    return r


def _sha_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        try:
            st = p.stat()
            return f"meta:{st.st_size}:{int(st.st_mtime)}"
        except Exception:
            return "unreadable"


def _mac_integrity() -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    ak = Path.home() / ".ssh" / "authorized_keys"
    if ak.is_file():
        d["ak_sha"] = _sha_file(ak)
        _, o = _run(["ssh-keygen", "-lf", str(ak)], 5)
        d["ak"] = [x.strip() for x in o.splitlines() if x.strip()]
    else:
        d["ak_sha"], d["ak"] = "absent", []
    sud: Dict[str, str] = {}
    for p in [Path("/etc/sudoers")] + sorted(Path("/etc/sudoers.d").glob("*")):
        if os.access(str(p), os.R_OK):
            sud[str(p)] = "sha256:" + _sha_file(p)
        else:
            try:
                st = p.stat()
                sud[str(p)] = f"meta:{st.st_size}:{int(st.st_mtime)}:{st.st_uid}:{oct(st.st_mode & 0o777)}"
            except Exception:
                sud[str(p)] = "unreadable"
    d["sudoers"] = sud
    rc, o = _run(["crontab", "-l"], 5)
    if rc == 0 and o.strip():
        n = len([x for x in o.splitlines() if x.strip() and not x.strip().startswith("#")])
        d["cron_user"] = f"sha256:{hashlib.sha256(o.encode()).hexdigest()}:lines={n}"
    else:
        d["cron_user"] = "none"
    la: Dict[str, str] = {}
    home = str(Path.home())
    for base in (Path.home() / "Library" / "LaunchAgents", Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")):
        try:
            for p in sorted(base.glob("*.plist")):
                la[str(p).replace(home, "~")] = _sha_file(p)
        except Exception:
            continue
    d["launchd"] = la
    _, o = _run(["dscl", ".", "list", "/Users", "UserShell"], 5)
    d["users"] = [f"{p[0]}|{p[1]}" for p in (ln.split() for ln in o.splitlines())
                  if len(p) >= 2 and not re.search(r"(false|nologin|uucico)$", p[1])]
    return d


def _mac_sessions() -> Dict[str, Any]:
    _, who = _run(["who"], 5)
    _, last = _run(["last", "-12"], 5)
    return {"who": [re.sub(r"\s+", " ", x.strip()) for x in who.splitlines() if x.strip()],
            "last": [re.sub(r"\s+", " ", x.strip()) for x in last.splitlines()
                     if x.strip() and not x.startswith(("wtmp", "reboot", "shutdown"))][:12]}


def _mac_auth_log(window: str) -> Dict[str, Any]:
    pred = 'process == "sshd" OR process == "sshd-session" OR process == "sshd-auth"'
    rc, o = _run(["/usr/bin/log", "show", "--last", window, "--style", "compact", "--predicate", pred], 90)
    fails: Dict[str, str] = {}
    ips: Dict[str, int] = {}
    accepted: Dict[str, int] = {}
    lastf: List[Dict[str, str]] = []
    for ln in o.splitlines():
        if re.search(r"Failed |Invalid user ", ln):
            pm = re.search(r"sshd(?:-session|-auth)?\[(\d+)", ln)
            key = pm.group(1) if pm else ln[:40]
            if key in fails:
                continue
            im = re.search(r"from (\S+)", ln)
            ip = im.group(1) if im else "?"
            fails[key] = ip
            ips[ip] = ips.get(ip, 0) + 1
            um = re.search(r"(?:invalid user|for) (\S+) from", ln)
            lastf.append({"ts": ln[:19], "type": "password" if "password" in ln else "other",
                          "user": um.group(1) if um else "?", "ip": ip})
        elif "Accepted " in ln:
            m = re.search(r"Accepted (\S+) for (\S+) from (\S+)", ln)
            if m:
                k = f"{m.group(2)}|{m.group(3)}|{m.group(1)}"
                accepted[k] = accepted.get(k, 0) + 1
    return {"ok": rc == 0, "count": len(fails), "ips": ips, "accepted": accepted, "last": lastf[-12:][::-1],
            "at": time.time(), "window": window}


def _mac_arp() -> List[Dict[str, Any]]:
    _, o = _run(["arp", "-an"], 5)
    res = []
    for ln in o.splitlines():
        m = re.match(r"\? \((\S+)\) at (\S+) on (\S+)", ln)
        if not m or m.group(2) == "(incomplete)":
            continue
        ip, mac, iface = m.groups()
        try:
            mac = ":".join(f"{int(x, 16):02x}" for x in mac.split(":"))
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if mac.startswith(("ff:ff:ff", "01:00:5e", "33:33")) or a.is_multicast or ip.endswith(".255"):
            continue
        res.append({"ip": ip, "mac": mac, "iface": iface, "label": LAN_LABELS.get(ip, ""),
                    "random_mac": (int(mac.split(":")[0], 16) & 0x02) != 0, "self": "permanent" in ln})
    return res


def _mac_softwareupdate() -> None:
    rc, o = _run(["softwareupdate", "-l"], 300)
    labels = re.findall(r"^\s*\* Label: (.+)$", o, re.M)
    titles = re.findall(r"Title: ([^,]+),", o)
    res: Dict[str, Any] = {"at": time.time(), "at_str": _now_str(), "count": len(labels),
                           "items": [t.strip() for t in titles] or [x.strip() for x in labels],
                           "restart": len(re.findall(r"Action: restart", o))}
    if "No new software available" in o:
        res["count"], res["items"] = 0, []
    elif rc != 0 and not labels:
        res["error"] = o.strip()[-160:]
    with _lock:
        _raw[MAC_ID]["swu"] = res
        _sstate["mac_swu"] = res
        _save_json(STATE_PATH, _sstate)


def collect_mac(do_slow: bool) -> None:
    now = time.time()
    if do_slow or not _local_nets:
        _refresh_local_nets()
    listen, est = _mac_netstat()
    sessions = _mac_sessions()
    arp = _mac_arp()
    inbound = [e for e in est if e["lport"] in (22, 5900, 3283)]
    outbound: Dict[str, Dict[str, Any]] = {}
    for e in est:
        if _is_public(e["faddr"]):
            o = outbound.setdefault(e["process"], {"count": 0, "peers": set()})
            o["count"] += 1
            o["peers"].add(e["faddr"])
    out_list = sorted(({"process": k, "count": v["count"], "peers": len(v["peers"])} for k, v in outbound.items()),
                      key=lambda x: -x["count"])[:12]
    with _lock:
        r = _raw[MAC_ID]
        r.update({"reachable": True, "arp": arp, "fast_at": now,
                  "sshd_listening": any(x["port"] == 22 for x in listen),
                  "fast": {"listen": listen, "sessions": inbound, "who": sessions["who"], "last": sessions["last"],
                           "outbound": out_list}})
    if True:  # firewall/stealth, sshd config, launch agents etc.: local + cheap, so every Mac cycle
        slow = {"firewall": _mac_firewall(), "sshd": _mac_sshd_cfg(), "integrity": _mac_integrity()}
        with _lock:
            _raw[MAC_ID]["slow"] = slow
            _raw[MAC_ID]["slow_at"] = now
    if now - _ts.get("mac_log1h", 0) >= MAC_LOG1H_SECS:
        _ts["mac_log1h"] = now
        res = _mac_auth_log("1h")
        with _lock:
            _raw[MAC_ID]["auth1h"] = res
    if now - _ts.get("mac_log24", 0) >= MAC_LOG24_SECS:
        _ts["mac_log24"] = now
        if _raw[MAC_ID].get("sshd_listening"):
            res = _mac_auth_log("24h")
        else:
            res = {"ok": True, "count": 0, "ips": {}, "accepted": {}, "last": [], "at": now, "window": "24h",
                   "note": "Remote Login (sshd) not listening; 24h log scan skipped"}
        with _lock:
            _raw[MAC_ID]["auth24"] = res
    swu = _raw[MAC_ID].get("swu") or _sstate.get("mac_swu")
    if swu and "swu" not in _raw[MAC_ID]:
        _raw[MAC_ID]["swu"] = swu
    if (not swu or now - float(swu.get("at", 0)) >= MAC_SWU_SECS) and not _ts.get("swu_running"):
        _ts["swu_running"] = 1

        def _bg() -> None:
            try:
                _mac_softwareupdate()
            finally:
                _ts["swu_running"] = 0
        threading.Thread(target=_bg, name="sec-softwareupdate", daemon=True).start()


def probe_public_tunnel() -> None:
    if not TUNNEL or not TUNNEL.get("public_url"):
        return
    now = time.time()
    if now - _ts.get("ext", 0) < EXT_PROBE_SECS:
        return
    _ts["ext"] = now
    res: Dict[str, Any] = {"url": TUNNEL["public_url"], "at": _now_str(now)}
    t0 = time.time()
    try:
        req = urllib.request.Request(TUNNEL["public_url"], method="HEAD",
                                     headers={"User-Agent": "node-dashboard-sec/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            res["code"] = resp.status
    except urllib.error.HTTPError as e:
        res["code"] = e.code
    except Exception as e:  # noqa: BLE001
        res["code"] = None
        res["error"] = str(e)[:120]
    res["ms"] = int((time.time() - t0) * 1000)
    with _lock:
        _raw.setdefault(TUNNEL["node"], {})["public_probe"] = res


# ------------------------------ Baseline ----------------------------------
def _snapshot(nid: str) -> Optional[Dict[str, Any]]:
    r = _raw.get(nid) or {}
    f, s = r.get("fast"), r.get("slow")
    if not f or not s:
        return None
    snap: Dict[str, Any] = {"ports": sorted({x["key"] for x in f.get("listen", [])})}
    if nid == MAC_ID:
        ig = s.get("integrity", {})
        snap.update({"ak": sorted(ig.get("ak", [])), "ak_sha": ig.get("ak_sha"), "sudoers": ig.get("sudoers", {}),
                     "cron_user": ig.get("cron_user"), "launchd": ig.get("launchd", {}),
                     "users": sorted(ig.get("users", []))})
    else:
        kv = s.get("kv", {})
        snap.update({"ak": sorted(s.get("ak", [])), "ak_sha": kv.get("AK_SHA"), "sudoers": s.get("sudoers", {}),
                     "cron_user": kv.get("CRONU"), "cron_sys": s.get("cron_sys", {}),
                     "units": sorted(s.get("units", [])), "users": sorted(s.get("users", [])),
                     "docker": sorted(f"{d['name']}|{d['image']}" for d in f.get("docker", []))})
    return snap


BASELINE_SCOPES = ("launchd", "ak", "sudoers", "cron_user", "cron_sys", "users", "units", "docker", "ports")


def accept_baseline_scope(node: str, scope: str) -> Dict[str, Any]:
    """Accept ONE baseline field for ONE node (e.g. mac/launchd). Leaves every other field, the
    exposure acknowledgement and the LAN device list untouched."""
    with _lock:
        snap = _snapshot(node)
        base = (_baseline.get("nodes") or {}).get(node)
        if snap is None or base is None or scope not in snap:
            return {"ok": False, "error": f"nothing to accept for {node}/{scope}"}
        base[scope] = snap[scope]
        base.setdefault("scoped_accepts", {})[scope] = _now_str()
        _baseline["updated_at"] = _now_str()
        _save_json(BASELINE_PATH, _baseline)
        _alert("info", node, "baseline", f"Baseline accepted by user for {NODE_LABELS.get(node, node)} / {scope} only")
    return {"ok": True, "nodes": [node], "scope": scope, "path": str(BASELINE_PATH)}


def accept_baseline(node: Optional[str] = None, explicit: bool = True) -> Dict[str, Any]:
    """Save the current state as known-good. explicit=True also acknowledges
    current all-interface listeners (first-run auto-baseline does not)."""
    done: List[str] = []
    with _lock:
        for nid in ([node] if node else NODE_ORDER):
            snap = _snapshot(nid)
            if snap is None:
                continue
            snap["accepted_at"] = _now_str()
            snap["ack_exposure"] = bool(explicit)
            _baseline.setdefault("nodes", {})[nid] = snap
            done.append(nid)
        if node in (None, MAC_ID) and _raw[MAC_ID].get("arp") is not None:
            lan = _baseline.setdefault("lan_devices", {})
            for dev in _raw[MAC_ID]["arp"]:
                lan.setdefault(dev["mac"], {"ip": dev["ip"], "first": _now_str()})
            if explicit:
                for mac, info in (_sstate.get("lan_seen") or {}).items():
                    lan.setdefault(mac, {"ip": info.get("ip"), "first": info.get("first")})
        _baseline["updated_at"] = _now_str()
        _baseline.setdefault("created_at", _baseline["updated_at"])
        _save_json(BASELINE_PATH, _baseline)
        if done:
            _alert("info", node or "all", "baseline",
                   f"Baseline {'accepted by user' if explicit else 'created on first run'} for {', '.join(done)}")
    return {"ok": True, "nodes": done, "path": str(BASELINE_PATH)}


def _diff_list(old: List[str], new: List[str]) -> Tuple[List[str], List[str]]:
    so, sn = set(old or []), set(new or [])
    return sorted(sn - so), sorted(so - sn)


def _diff_map(old: Dict[str, str], new: Dict[str, str]) -> Tuple[List[str], List[str], List[str]]:
    old, new = old or {}, new or {}
    return (sorted(set(new) - set(old)), sorted(set(old) - set(new)),
            sorted(k for k in set(old) & set(new) if old[k] != new[k]))


# ------------------------------ Alerts ------------------------------------
def _alert(level: str, node: str, check: str, msg: str, key: Optional[str] = None) -> None:
    rec = {"ts": _now_str(), "epoch": round(time.time(), 1), "level": level, "node": node,
           "node_label": NODE_LABELS.get(node, node), "check": check, "msg": msg}
    if key:
        rec["key"] = key
    try:
        with ALERTS_PATH.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def read_alerts(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        lines = ALERTS_PATH.read_text().splitlines()[-max(limit, 1):]
    except Exception:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _reconcile(findings: List[Dict[str, Any]], evaluated_nodes: List[str]) -> None:
    """Emit an alert when a finding first appears (or escalates); 'Resolved' when it clears.
    Findings for nodes that could not be evaluated this cycle are left untouched."""
    rank = {"warn": 1, "alert": 2}
    with _lock:
        active = _sstate.setdefault("active", {})
        now_keys = set()
        for f in findings:
            k = f["key"]
            now_keys.add(k)
            prev = active.get(k)
            if prev is None or rank[f["level"]] > rank.get(prev.get("level"), 0):
                _alert(f["level"], f["node"], f["check"], f["msg"], key=k)
                active[k] = {"level": f["level"], "since": _now_str(), "msg": f["msg"]}
            else:
                prev["msg"] = f["msg"]
        for k in list(active.keys()):
            node = k.split(":", 1)[0]
            if k not in now_keys and node in evaluated_nodes:
                parts = k.split(":")
                _alert("info", node, parts[1] if len(parts) > 1 else "", f"Resolved: {active[k].get('msg', k)}", key=k)
                del active[k]
        _save_json(STATE_PATH, _sstate)


# ------------------------------ Evaluation --------------------------------
def _chk(status: str, summary: str, **details: Any) -> Dict[str, Any]:
    return {"status": status, "summary": summary, "details": details}


def _worst(*sts: str) -> str:
    order = {"ok": 0, "unknown": 0, "warn": 1, "alert": 2}
    w = "ok"
    for s in sts:
        if order.get(s, 0) > order.get(w, 0):
            w = s
    return w


def _eval_logins(nid: str, r: Dict[str, Any], f: Dict[str, Any], find: Callable[..., None]) -> Dict[str, Any]:
    if nid == MAC_ID:
        a1, a24 = r.get("auth1h") or {}, r.get("auth24") or {}
        f1, f24 = a1.get("count"), a24.get("count")
        if f24 is not None and f1 is not None:
            f24 = max(f24, f1)
        ips = dict(a24.get("ips") or {})
        for ip, c in (a1.get("ips") or {}).items():
            ips[ip] = max(ips.get(ip, 0), c)
        fail_ips = [{"ip": ip, "c24": c, "c1": (a1.get("ips") or {}).get(ip, 0), "public": _is_public(ip)}
                    for ip, c in ips.items()]
        acc = a24.get("accepted") or a1.get("accepted") or {}
        accepted = [{"user": k.split("|")[0], "ip": k.split("|")[1], "method": k.split("|")[2], "c24": v, "c1": 0,
                     "public": _is_public(k.split("|")[1])} for k, v in acc.items()]
        sessions = [{"local": x["local"], "peer": x["faddr"]} for x in f.get("sessions", [])]
        fail_last = a1.get("last") or a24.get("last") or []
        note = ("Remote Login (sshd) is ON" if r.get("sshd_listening") else
                "Remote Login (sshd) is OFF: nothing listens on :22, so no SSH logins are possible") + \
               " · macOS unified log (log show), 1h window every 15 min"
    else:
        f1, f24 = f.get("fail_1h"), f.get("fail_24h")
        fail_ips, accepted = f.get("fail_ips", []), f.get("accepted", [])
        sessions, fail_last = f.get("sessions", []), f.get("fail_last", [])
        note = f"source: {f.get('auth_src')} (failed attempts counted once per sshd connection)"
    for x in list(fail_ips) + list(accepted):  # re-evaluate with the current home IPv6 /64 list
        x["public"] = _is_public(x["ip"])
    st = "ok"
    for x in [x for x in fail_ips if x["public"]]:
        st = "alert"
        find("logins", "alert", f"Failed SSH logins from public IP {x['ip']} ({x['c24']} in 24h)", x["ip"])
    for x in [x for x in accepted if x["public"]]:
        st = "alert"
        find("logins", "alert", f"SSH {x['method']} login from public IP {x['ip']} as {x['user']} "
             f"({x['c24']}x in 24h, last {x.get('last', '?')}) - not in the home LAN ranges", "acc-" + x["ip"])
    if (f1 or 0) >= 20:
        st = "alert"
        find("logins", "alert", f"SSH brute-force burst: {f1} failed attempts in the last hour", "burst")
    elif (f1 or 0) >= 3 or (f24 or 0) >= 20:
        st = _worst(st, "warn")
        find("logins", "warn", f"{f1} failed SSH attempts in 1h / {f24} in 24h", "fails")
    pw_acc = [x for x in accepted if x["method"] == "password"]
    if pw_acc:
        st = _worst(st, "warn")
        find("logins", "warn", "Password-based SSH login accepted: " +
             ", ".join(f"{x['user']}@{x['ip']}" for x in pw_acc[:3]), "pwlogin")
    acc_total = sum(x["c24"] for x in accepted)
    summary = f"failed {f1 if f1 is not None else '?'}/1h, {f24 if f24 is not None else '?'}/24h"
    if fail_ips:
        summary += " from " + ", ".join(f"{x['ip']}({x['c24']})" for x in fail_ips[:3])
    summary += f" · {acc_total} accepted logins/24h"
    if accepted:
        summary += " (" + ", ".join(f"{x['method']} {x['ip']}×{x['c24']}" for x in accepted[:3]) + ")"
    summary += f" · {len(sessions)} live SSH/RDP conns"
    return _chk(st, summary, failed_1h=f1, failed_24h=f24, fail_sources=fail_ips, recent_failures=fail_last[:10],
                accepted=accepted[:15], sessions=sessions, who=f.get("who", []), last=f.get("last", [])[:10], note=note)


def _eval_exposure(nid: str, r: Dict[str, Any], f: Dict[str, Any], s: Dict[str, Any],
                   base: Optional[Dict[str, Any]], find: Callable[..., None]) -> Dict[str, Any]:
    listen = [dict(x) for x in f.get("listen", [])]
    sshd = s.get("sshd") or {}
    eff = sshd.get("effective") or {}
    exp = EXPECTED_ANY.get(nid, {})
    base_ports = set((base or {}).get("ports") or [])
    ack = bool((base or {}).get("ack_exposure"))
    st = "ok"
    unexpected = []
    for x in listen:
        x["expected"] = x["scope"] == "any" and x["port"] in exp
        if x["expected"]:
            x["flag"] = "expected: " + exp[x["port"]]
        if base and x["key"] not in base_ports:
            if x["scope"] == "any":
                st = "alert"
                x["flag"] = "NEW on all interfaces"
                find("exposure", "alert", f"New port open on all interfaces: {x['port']} "
                     f"({x['process'] or x['name'] or 'process hidden (root)'})", f"new-{x['key']}")
            elif x["scope"] == "lan":
                st = _worst(st, "warn")
                x["flag"] = "NEW (LAN IP)"
                find("exposure", "warn", f"New LAN-bound listener {x['addr']}:{x['port']}", f"new-{x['key']}")
            else:
                x["flag"] = "new (loopback only)"
        elif x["scope"] == "any" and not x["expected"]:
            x["flag"] = "all interfaces, acknowledged" if ack else "all interfaces, not in expected list"
            if not ack:
                unexpected.append(x)
    if unexpected:
        st = _worst(st, "warn")
        find("exposure", "warn", "Listening on all interfaces, not in expected list: " +
             ", ".join(f"{x['port']} ({(x['process'] or x['name'] or 'process hidden').split(':')[0]})" for x in unexpected),
             "unexpected-any")
    sshd_relevant = nid != MAC_ID or bool(r.get("sshd_listening"))
    if sshd_relevant and eff:
        if eff.get("permitrootlogin") == "yes":
            st = "alert"
            find("exposure", "alert", "sshd PermitRootLogin yes", "rootlogin")
        if eff.get("permitemptypasswords") == "yes":
            st = "alert"
            find("exposure", "alert", "sshd PermitEmptyPasswords yes", "emptypw")
        if eff.get("passwordauthentication") == "yes":
            st = _worst(st, "warn")
            find("exposure", "warn", "sshd allows password logins (PasswordAuthentication yes, " +
                 ("OpenSSH default" if (sshd.get("source") or {}).get("passwordauthentication") == "OpenSSH default"
                  else "set in config") + ")", "pwauth")
    anyp = [x for x in listen if x["scope"] == "any"]
    summ = f"{len(listen)} listeners; on all interfaces: " + (", ".join(str(p) for p in sorted({x["port"] for x in anyp})) or "none")
    if sshd_relevant and eff:
        summ += f" · sshd password={eff.get('passwordauthentication')} root={eff.get('permitrootlogin')}"
    elif nid == MAC_ID:
        summ += " · Remote Login off"
    rdp = [f"{x['addr']}:{x['port']} {x['process'] or x['name']}".strip() for x in listen if x["port"] in (3389, 3390, 3350, 5900)]
    return _chk(st, summ, listeners=listen, sshd=sshd, remote_desktop=rdp,
                baseline_at=(base or {}).get("accepted_at"), exposure_acknowledged=ack)


def _eval_firewall(nid: str, s: Dict[str, Any], find: Callable[..., None]) -> Optional[Dict[str, Any]]:
    if nid == MAC_ID:
        fw = s.get("firewall") or {}
        st = "ok"
        if fw.get("filevault") == "off":
            st = "alert"; find("firewall", "alert", "FileVault is OFF", "filevault")
        if fw.get("sip") == "off":
            st = "alert"; find("firewall", "alert", "System Integrity Protection is disabled", "sip")
        if fw.get("gatekeeper") == "off":
            st = _worst(st, "warn"); find("firewall", "warn", "Gatekeeper assessments disabled", "gatekeeper")
        if fw.get("app_firewall") == "off":
            st = _worst(st, "warn"); find("firewall", "warn", "macOS Application Firewall is OFF", "appfw")
        return _chk(st, f"App firewall {fw.get('app_firewall')} · stealth {fw.get('stealth')} · FileVault {fw.get('filevault')}"
                        f" · SIP {fw.get('sip')} · Gatekeeper {fw.get('gatekeeper')}", **fw)
    if not s:
        return None
    kv = s.get("kv", {})
    ufw = (kv.get("UFW_CONF") or "?").lower()
    st = "ok"
    if ufw != "yes" and kv.get("NFT_SVC") != "active" and kv.get("FWD_SVC") != "active":
        st = "warn"
        find("firewall", "warn", "No host firewall: ufw disabled (ENABLED=no); nftables and firewalld services inactive", "none")
    return _chk(st, f"ufw {'enabled' if ufw == 'yes' else 'disabled'} (ufw.service {kv.get('UFW_SVC')}) · "
                    f"nftables {kv.get('NFT_SVC')} · firewalld {kv.get('FWD_SVC')}",
                ufw_conf_enabled=ufw, ufw_service=kv.get("UFW_SVC"), nftables=kv.get("NFT_SVC"), firewalld=kv.get("FWD_SVC"),
                note="`ufw status` / iptables rules need sudo, so state is read from /etc/ufw/ufw.conf and systemd. "
                     "Docker may still install its own iptables rules.")


def _eval_integrity(nid: str, f: Dict[str, Any], s: Dict[str, Any], base: Optional[Dict[str, Any]],
                    find: Callable[..., None]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    snap = _snapshot(nid) or {}
    st = "ok"
    changes: List[str] = []

    def chg(level: str, label: str, text: str, sub: str) -> None:
        nonlocal st
        st = _worst(st, level)
        changes.append(f"{label}: {text}")
        find("integrity", level, f"{label} changed vs baseline: {text}", sub)

    if base:
        a, rm = _diff_list(base.get("ak", []), snap.get("ak", []))
        if a or rm or base.get("ak_sha") != snap.get("ak_sha"):
            chg("alert", "authorized_keys", f"+{len(a)} / -{len(rm)} keys" + (f" (new: {a[0][:70]})" if a else ""), "ak")
        ad, rmv, ch = _diff_map(base.get("sudoers", {}), snap.get("sudoers", {}))
        if ad or rmv or ch:
            chg("alert", "sudoers", f"added {ad} removed {rmv} changed {ch}", "sudoers")
        a, rm = _diff_list(base.get("users", []), snap.get("users", []))
        if a or rm:
            chg("alert", "login-shell users", f"added {a} removed {rm}", "users")
        if base.get("cron_user") != snap.get("cron_user"):
            chg("warn", "user crontab", "content hash changed", "cron")
        if nid == MAC_ID:
            ad, rmv, ch = _diff_map(base.get("launchd", {}), snap.get("launchd", {}))
            if ad or rmv or ch:
                b = os.path.basename
                chg("warn", "LaunchAgents/Daemons", f"added {[b(x) for x in ad]} removed {[b(x) for x in rmv]} "
                    f"changed {[b(x) for x in ch]}", "launchd")
        else:
            ad, rmv, ch = _diff_map(base.get("cron_sys", {}), snap.get("cron_sys", {}))
            if ad or rmv or ch:
                chg("warn", "system cron", f"added {ad} removed {rmv} changed {ch}", "cronsys")
            a, rm = _diff_list(base.get("units", []), snap.get("units", []))
            if a or rm:
                chg("warn", "enabled systemd units", f"added {a} removed {rm}", "units")
            a, rm = _diff_list(base.get("docker", []), snap.get("docker", []))
            if a:
                chg("warn", "docker containers", f"new {a}" + (f"; gone {rm}" if rm else ""), "docker")
    if nid != MAC_ID:
        extra0 = [u for u in s.get("uid0", []) if u != "root"]
        if extra0:
            st = "alert"
            find("integrity", "alert", f"Extra UID 0 accounts: {extra0}", "uid0")
    if nid == MAC_ID:
        ig = s.get("integrity") or {}
        counts = (f"authorized_keys {'absent' if ig.get('ak_sha') == 'absent' else len(ig.get('ak', []))} · launchd plists "
                  f"{len(ig.get('launchd', {}))} · shell users {len(ig.get('users', []))} · crontab "
                  f"{'none' if ig.get('cron_user') == 'none' else 'present'}")
        det = {"authorized_keys": ig.get("ak", []), "sudoers": ig.get("sudoers", {}), "users": ig.get("users", []),
               "cron_user": (ig.get("cron_user") or "")[:30], "launchd": sorted(ig.get("launchd", {}).keys())}
    else:
        kv = s.get("kv", {})
        counts = (f"keys {len(s.get('ak', []))} · enabled units {len(s.get('units', []))} · shell users "
                  f"{len(s.get('users', []))} · crontab {'none' if kv.get('CRONU') == 'none' else 'present'} · "
                  f"containers {len(f.get('docker', []))}")
        det = {"authorized_keys": s.get("ak", []), "sudoers": s.get("sudoers", {}), "users": s.get("users", []),
               "cron_user": (kv.get("CRONU") or "")[:30], "units_count": len(s.get("units", [])),
               "docker": f.get("docker", [])}
    head = ("; ".join(changes) + " · ") if changes else ("no changes vs baseline · " if base else "no baseline yet · ")
    return _chk(st, head + counts, changes=changes, baseline_at=(base or {}).get("accepted_at"), **det)


def _eval_updates(nid: str, r: Dict[str, Any], s: Dict[str, Any], find: Callable[..., None]) -> Optional[Dict[str, Any]]:
    if nid == MAC_ID:
        swu = r.get("swu")
        if not swu:
            return _chk("unknown", "softwareupdate -l running in background (cached, every 6h)…")
        n = swu.get("count") or 0
        st = "warn" if n > 0 else "ok"
        if n:
            find("updates", "warn", f"{n} macOS software update(s) pending: {', '.join(swu.get('items', [])[:3])}", "pending")
        return _chk(st, f"{n} pending" + (f" ({', '.join(swu.get('items', [])[:3])})" if n else "") +
                    f" · checked {swu.get('at_str')}" + (f" · error: {swu.get('error')}" if swu.get("error") else ""),
                    items=swu.get("items", []), checked_at=swu.get("at_str"), restart_needed=swu.get("restart"))
    if not s:
        return None
    up = dict(s.get("updates") or {})
    kv = s.get("kv", {})
    if kv.get("APT_UPG") not in (None, ""):
        up["apt_upgradable"] = int(kv["APT_UPG"])
        up["apt_from_security_pocket"] = int(kv.get("APT_SEC") or 0)
        try:
            up["apt_checked_at"] = _now_str(float(kv["APT_AT"])) if kv.get("APT_AT") else None
        except ValueError:
            pass
    sec = max(up.get("security") or 0, up.get("apt_from_security_pocket") or 0)
    up["security_effective"] = sec
    st = "ok"
    if sec >= 100:
        st = "alert"; find("updates", "alert", f"{sec} security updates pending", "sec")
    elif sec > 0:
        st = "warn"; find("updates", "warn", f"{sec} security update(s) pending", "sec")
    stamp = up.get("apt_update_stamp_epoch")
    if stamp and time.time() - stamp > 7 * 86400:
        st = _worst(st, "warn"); find("updates", "warn", "apt package lists not refreshed in over 7 days", "stale")
    if up.get("reboot_required"):
        st = _worst(st, "warn"); find("updates", "warn", "Reboot required to finish installed updates", "reboot")
    summ = (f"{up.get('total')} upgradable · {sec} security ({up.get('security_standard', 0)} standard + "
            f"{up.get('security_esm', 0)} ESM)")
    if up.get("esm_not_enabled_security"):
        summ += f" · +{up['esm_not_enabled_security']} more need ESM Apps"
    if up.get("apt_upgradable") is not None:
        summ += f" · apt list: {up['apt_upgradable']} ({up.get('apt_from_security_pocket')} from -security)"
    if up.get("reboot_required"):
        summ += " · reboot required"
    return _chk(st, summ, **up)


def _eval_kernel(s: Dict[str, Any], find: Callable[..., None]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    k = s.get("kernel") or {}
    st = "ok"
    parts = []
    for cat, label in (("soft_lockup", "soft lockup"), ("hung_task", "hung task"), ("oom_kill", "OOM kill"),
                       ("xid", "GPU Xid"), ("kernel_bug", "kernel BUG/Oops")):
        if cat in k:
            st = "warn"
            first = _iso_local(k[cat]["first"])
            parts.append(f"{label}: {k[cat]['count']} log lines since {first}")
            find("kernel", "warn", f"{label} in kernel log (last 24h): {k[cat]['count']} lines, first at {first}", cat)
    info = f" · NVRM NV_ERR_NO_MEMORY notices: {k['nvrm_nomem']['count']} (driver allocation retries, info)" if "nvrm_nomem" in k else ""
    return _chk(st, ("; ".join(parts) if parts else "no soft lockups / hung tasks / OOM kills / Xid in 24h") + info, events=k)


def _eval_tunnel(r: Dict[str, Any], f: Dict[str, Any], find: Callable[..., None]) -> Dict[str, Any]:
    dk = {d["name"]: d for d in f.get("docker", [])}
    c = dk.get(TUNNEL["container"])
    ready = f.get("cf_ready")
    pp = r.get("public_probe") or {}
    st = "ok"
    if not c or not c["status"].startswith("Up"):
        st = "alert"
        find("tunnel", "alert", f"{TUNNEL['container']} not running ({c['status'] if c else 'missing'})", "down")
    elif not isinstance(ready, dict) or ready.get("status") != 200 or not ready.get("readyConnections"):
        st = "warn"
        find("tunnel", "warn", f"cloudflared /ready not healthy: {ready}", "ready")
    code = pp.get("code")
    host = urllib.parse.urlparse(TUNNEL.get("public_url") or "").hostname or "public URL"
    if pp and code is not None and code >= 500:
        st = _worst(st, "warn"); find("tunnel", "warn", f"{host} returned HTTP {code}", "public")
    elif pp and code is None and pp.get("error"):
        st = _worst(st, "warn"); find("tunnel", "warn", f"{host} probe failed: {pp.get('error')}", "public")
    rc = ready.get("readyConnections") if isinstance(ready, dict) else "?"
    return _chk(st, f"{TUNNEL['container']}: {c['status'] if c else 'missing'} · /ready connections {rc} · "
                    f"{host} HTTP {code if pp else '…'}" + (f" ({pp.get('ms')} ms)" if pp.get("ms") else ""),
                container=c, ready=ready, public_probe=pp)


def _eval_network(r: Dict[str, Any], f: Dict[str, Any], find: Callable[..., None]) -> Dict[str, Any]:
    arp = [dict(x) for x in (r.get("arp") or [])]
    lan_base = _baseline.get("lan_devices") or {}
    seen = _sstate.setdefault("lan_seen", {})
    new = []
    for dev in arp:
        info = seen.setdefault(dev["mac"], {"ip": dev["ip"], "first": _now_str()})
        info["ip"], info["last"] = dev["ip"], _now_str()
        dev["known"] = dev["mac"] in lan_base
        if lan_base and not dev["known"]:
            new.append(dev)
    st = "warn" if new else "ok"
    for dev in new:
        find("network", "warn", f"New LAN device {dev['ip']} {dev['mac']}"
             f"{' (randomized MAC)' if dev['random_mac'] else ''} on {dev['iface']}", dev["mac"])
    return _chk(st, f"{len(arp)} devices in the Mac's ARP cache · {len(new)} new vs baseline ({len(lan_base)} known)",
                devices=arp, new=new, outbound=f.get("outbound", []))


def _eval_node(nid: str, findings: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
    """Returns (node view, fully_evaluated)."""
    r = _raw.get(nid) or {}
    f, s = r.get("fast") or {}, r.get("slow") or {}
    base = (_baseline.get("nodes") or {}).get(nid)
    if not f:
        return ({"id": nid, "label": NODE_LABELS[nid], "reachable": bool(r.get("reachable")), "error": r.get("error"),
                 "checks": {}, "status": "unknown", "score": None, "collected_at": None}, False)
    local: List[Dict[str, Any]] = []

    def find(check: str, level: str, msg: str, sub: str = "") -> None:
        local.append({"key": f"{nid}:{check}{(':' + sub) if sub else ''}", "node": nid, "check": check,
                      "level": level, "msg": msg})

    checks: Dict[str, Dict[str, Any]] = {}
    checks["logins"] = _eval_logins(nid, r, f, find)
    checks["exposure"] = _eval_exposure(nid, r, f, s, base, find)
    for name, val in (("firewall", _eval_firewall(nid, s, find)),
                      ("integrity", _eval_integrity(nid, f, s, base, find)),
                      ("updates", _eval_updates(nid, r, s, find))):
        if val is not None:
            checks[name] = val
    if nid != MAC_ID:
        k = _eval_kernel(s, find)
        if k is not None:
            checks["kernel"] = k
    if TUNNEL and nid == TUNNEL["node"]:
        checks["tunnel"] = _eval_tunnel(r, f, find)
    if nid == MAC_ID:
        checks["network"] = _eval_network(r, f, find)
    findings.extend(local)
    stale = nid != MAC_ID and not r.get("reachable")
    statuses = [c["status"] for c in checks.values()]
    alerts, warns = statuses.count("alert"), statuses.count("warn")
    view = {"id": nid, "label": NODE_LABELS[nid], "reachable": bool(r.get("reachable")), "stale": stale,
            "error": r.get("error"), "checks": checks, "status": _worst(*statuses) if statuses else "unknown",
            "score": max(0, 100 - 20 * alerts - 7 * warns), "alerts": alerts, "warns": warns,
            "findings": [x["msg"] for x in local],
            "collected_at": _now_str(r.get("fast_at")) if r.get("fast_at") else None,
            "slow_at": _now_str(r.get("slow_at")) if r.get("slow_at") else None,
            "fast_epoch": r.get("fast_at"), "slow_epoch": r.get("slow_at")}
    return view, (not stale and bool(s))


def evaluate() -> None:
    findings: List[Dict[str, Any]] = []
    evaluated: List[str] = []
    nodes: Dict[str, Any] = {}
    with _lock:
        for nid in NODE_ORDER:
            nodes[nid], full = _eval_node(nid, findings)
            if full:
                evaluated.append(nid)
    scores = [n["score"] for n in nodes.values() if n.get("score") is not None]
    overall = {"score": round(sum(scores) / len(scores)) if scores else None,
               "status": _worst(*[n["status"] for n in nodes.values()]),
               "alerts": sum(n.get("alerts", 0) for n in nodes.values()),
               "warns": sum(n.get("warns", 0) for n in nodes.values()),
               "scoring": "node score = 100 - 20 per ALERT check - 7 per WARN check; overall = mean of node scores"}
    _reconcile(findings, evaluated)
    with _lock:
        _view.update({"nodes": nodes, "overall": overall, "updated_at": _now_str(), "updated_epoch": time.time(),
                      "findings": sorted(findings, key=lambda x: (x["level"] != "alert", NODE_ORDER.index(x["node"])))})


_refresh_evt = threading.Event()
_cycle_lock = threading.Lock()


def run_cycle(force_slow: bool = False, first: bool = False) -> None:
    """One scheduler tick. Mac every MAC_SECS, nodes every FAST_SECS (slow part every SLOW_SECS,
    in the same ssh exec). force_slow = 'Refresh now' (everything except the cached apt/softwareupdate)."""
    with _cycle_lock:
        now = time.time()
        do_mac = force_slow or now - _ts.get("mac", 0) >= MAC_SECS - 0.5
        do_nodes = force_slow or now - _ts.get("fast", 0) >= FAST_SECS - 0.5
        do_slow = do_nodes and (force_slow or now - _ts.get("slow", 0) >= SLOW_SECS - 0.5)
        do_apt = do_slow and (first or now - _ts.get("apt", 0) >= APT_SECS)
        if not (do_mac or do_nodes):
            return
        threads = []
        if do_nodes:
            for meta in _nodes:
                t = threading.Thread(target=collect_node, args=(meta, do_slow, do_apt), daemon=True)
                t.start()
                threads.append(t)
        if do_mac:
            try:
                collect_mac(do_slow or first)
            except Exception as e:  # noqa: BLE001
                with _lock:
                    _raw[MAC_ID]["error"] = str(e)[:200]
            _ts["mac"] = now
        try:
            probe_public_tunnel()
        except Exception:
            pass
        for t in threads:
            t.join(timeout=SSH_TIMEOUT * 2 + 15)
        if do_nodes:
            _ts["fast"] = now
        if do_slow:
            _ts["slow"] = now
        if do_apt:
            _ts["apt"] = now
        # First-run baseline per node (does NOT acknowledge current all-interface listeners).
        for nid in NODE_ORDER:
            if nid not in (_baseline.get("nodes") or {}) and _snapshot(nid) is not None:
                accept_baseline(nid, explicit=False)
        evaluate()
        if force_slow:
            _ts["refresh_done"] = time.time()


def request_refresh() -> Dict[str, Any]:
    now = time.time()
    if now - _ts.get("refresh_req", 0) < 5:
        return {"ok": True, "queued": False, "note": "a refresh was requested less than 5 s ago"}
    _ts["refresh_req"] = now
    _refresh_evt.set()
    return {"ok": True, "queued": True, "requested_epoch": now}


def _loop() -> None:
    first = True
    while True:
        forced = _refresh_evt.is_set()
        _refresh_evt.clear()
        try:
            run_cycle(force_slow=first or forced, first=first)
            first = False
            with _lock:
                _view["collector_error"] = None
        except Exception as e:  # noqa: BLE001
            with _lock:
                _view["collector_error"] = str(e)[:300]
        _refresh_evt.wait(TICK_SECS)


def start(nodes: List[Dict[str, Any]], ssh_base: Callable[..., List[str]], data_dir: Optional[Path] = None,
          cfg: Optional[Dict[str, Any]] = None) -> None:
    global _ssh_base, _nodes, DATA_DIR, BASELINE_PATH, STATE_PATH, ALERTS_PATH, _baseline, _sstate
    configure(cfg or {"nodes": nodes})
    _ssh_base = ssh_base
    _nodes = list(nodes)
    for m in _nodes:
        _raw.setdefault(m["id"], {})
    DATA_DIR = Path(os.environ.get("NODE_SEC_DIR", str(data_dir or DATA_DIR)))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH = DATA_DIR / "baseline.json"
    STATE_PATH = DATA_DIR / "state.json"
    ALERTS_PATH = DATA_DIR / "alerts.jsonl"
    _baseline = _load_json(BASELINE_PATH, {})
    _sstate = _load_json(STATE_PATH, {"active": {}, "lan_seen": {}})
    _sstate.setdefault("active", {})
    _sstate.setdefault("lan_seen", {})
    _sstate.setdefault("v6_seen", {})
    _rebuild_local_nets()
    try:
        _refresh_local_nets()
    except Exception:
        pass
    threading.Thread(target=_loop, name="security-collector", daemon=True).start()


def api_payload() -> Dict[str, Any]:
    with _lock:
        v = json.loads(json.dumps(_view, default=str))
        b = {"path": str(BASELINE_PATH), "created_at": _baseline.get("created_at"),
             "updated_at": _baseline.get("updated_at"),
             "nodes": {k: {"accepted_at": n.get("accepted_at"), "ack_exposure": n.get("ack_exposure")}
                       for k, n in (_baseline.get("nodes") or {}).items()},
             "lan_devices": len(_baseline.get("lan_devices") or {})}
    v["alerts"] = read_alerts(100)
    v["baseline"] = b
    v["cadence"] = {"mac_secs": MAC_SECS, "fast_secs": FAST_SECS, "slow_secs": SLOW_SECS, "apt_secs": APT_SECS,
                    "mac_softwareupdate_secs": MAC_SWU_SECS, "mac_log_secs": MAC_LOG1H_SECS,
                    "ui_refresh_secs": 5}
    v["server_epoch"] = time.time()
    v["refresh"] = {"requested_epoch": _ts.get("refresh_req"), "done_epoch": _ts.get("refresh_done")}
    v["data_dir"] = str(DATA_DIR)
    v["node_order"] = list(NODE_ORDER)
    with _lock:
        v["home_v6"] = {"nets": [str(n) for n in _local_nets], "static": list(HOME_V6_STATIC),
                        "learned": dict(_sstate.get("v6_seen") or {}),
                        "remember_days": HOME_V6_REMEMBER_SECS / 86400}
    v["mode"] = "monitor-only: read-only collectors, nothing is ever changed on any node"
    return v


def handle_get(path: str) -> Optional[Tuple[int, bytes, str]]:
    if path in ("/api/security", "/api/security.json"):
        return 200, json.dumps(api_payload(), indent=2).encode("utf-8"), "application/json; charset=utf-8"
    if path == "/api/security/alerts":
        return 200, json.dumps(read_alerts(300), indent=2).encode("utf-8"), "application/json; charset=utf-8"
    return None


def handle_post(path: str, headers: Any, query: str = "") -> Optional[Tuple[int, bytes, str]]:
    if path not in ("/api/security/baseline", "/api/security/refresh"):
        return None
    # Custom header => browsers must preflight cross-origin requests, which this server never approves (CSRF guard).
    if headers.get("X-Node-Dashboard") != "1":
        return 403, b'{"ok":false,"error":"missing X-Node-Dashboard: 1 header"}', "application/json"
    if path == "/api/security/refresh":
        return 202, json.dumps(request_refresh()).encode("utf-8"), "application/json"
    m = re.search(r"(?:^|&)node=([A-Za-z0-9_-]+)", query or "")
    node = m.group(1) if m else None
    if node and node not in NODE_ORDER:
        return 400, b'{"ok":false,"error":"unknown node"}', "application/json"
    ms = re.search(r"(?:^|&)scope=([a-z_]+)", query or "")
    scope = ms.group(1) if ms else None
    if scope:
        if not node or scope not in BASELINE_SCOPES:
            return 400, b'{"ok":false,"error":"scope needs node= and one of the known scopes"}', "application/json"
        res = accept_baseline_scope(node, scope)
    else:
        res = accept_baseline(node, explicit=True)
    try:
        evaluate()
    except Exception:
        pass
    return 200, json.dumps(res).encode("utf-8"), "application/json"


# ------------------------------ UI ----------------------------------------
# Injected into the existing dashboard HTML; the existing panels are untouched.
SEC_CSS = r"""
.sechead { display:flex; align-items:center; gap:12px; margin: 26px 0 10px; flex-wrap: wrap; }
.sechead h1 { margin:0; }
.grid4 { display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; }
@media (max-width: 1500px) { .grid4 { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 800px) { .grid4 { grid-template-columns: 1fr; } }
.badge.warn { background: rgba(240,193,75,0.12); color: var(--yellow); border-color: rgba(240,193,75,0.35); }
.badge.alert { background: rgba(255,92,122,0.14); color: var(--red); border-color: rgba(255,92,122,0.4); }
.badge.unknown { background: rgba(139,155,180,0.12); color: var(--muted); border-color: rgba(139,155,180,0.35); }
.score { font-family: var(--mono); font-size: 24px; font-weight: 700; }
.score.ok { color: var(--green); } .score.warn { color: var(--yellow); } .score.alert { color: var(--red); } .score.unknown { color: var(--muted); }
.chk { display:grid; grid-template-columns: 10px 74px 1fr; gap: 6px; align-items: start; padding: 5px 0; border-bottom: 1px solid #1c2738; }
.chk .n { color: var(--muted); text-transform: uppercase; font-size: 10px; letter-spacing: .06em; padding-top: 1px; }
.chk .s { font-family: var(--mono); font-size: 11px; line-height: 1.35; word-break: break-word; }
.dot { width:9px; height:9px; border-radius: 99px; margin-top: 3px; background: var(--muted); }
.dot.ok { background: var(--green); } .dot.warn { background: var(--yellow); } .dot.alert { background: var(--red); }
.sec-card { min-height: 0; }
.sec-card details { margin-top: 8px; font-size: 11px; }
.sec-card summary { cursor: pointer; color: var(--blue); font-size: 11px; }
.btn { background: var(--panel2); color: var(--text); border:1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-size: 12px; cursor:pointer; }
.btn:hover { border-color: var(--blue); }
.feed { max-height: 380px; overflow: auto; font-family: var(--mono); font-size: 11px; }
.feed .row { padding: 4px 0; border-bottom: 1px solid #1c2738; }
.lvl-alert { color: var(--red); font-weight: 700; } .lvl-warn { color: var(--yellow); font-weight: 700; } .lvl-info { color: var(--muted); }
.flag { color: var(--yellow); } .flag.bad { color: var(--red); } .flag.okf { color: var(--muted); }
.findings { margin-top: 8px; font-size: 11px; font-family: var(--mono); }
.findings div { padding: 2px 0; }
"""

SEC_HTML = r"""
  <div class="sechead" id="security">
    <h1>Security</h1>
    <span class="score unknown" id="sec-score">—</span>
    <span id="sec-overall"></span>
    <span class="sub" style="margin:0" id="sec-stamp">monitor-only · loading…</span>
    <span style="flex:1"></span>
    <a href="/api/security">/api/security</a>
    <button class="btn" id="sec-refresh" title="Force an immediate full recheck of the Mac and all nodes (POST /api/security/refresh)">Refresh now</button>
    <button class="btn" id="sec-accept" title="Save current ports, SSH keys, users, sudoers, crontabs, startup units, containers and LAN devices as the known-good baseline">Accept current as baseline</button>
  </div>
  <div class="grid4" id="sec-nodes"></div>
  <div class="row2">
    <div class="card sec-card" id="sec-alerts"></div>
    <div class="card sec-card" id="sec-lan"></div>
  </div>
"""

SEC_JS = r"""
<script>
(function(){
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const LBL = {ok:'OK', warn:'WARN', alert:'ALERT', unknown:'…'};
const CHECKS = [['logins','Logins'],['exposure','Exposure'],['firewall','Firewall'],['integrity','Integrity'],['updates','Updates'],['kernel','Kernel'],['tunnel','Tunnel'],['network','LAN']];
function sb(st){ return `<span class="badge ${st==='ok'?'on':esc(st)}">${LBL[st]||esc(st)}</span>`; }
function portsTable(ls){
  if(!ls||!ls.length) return '—';
  return `<table><tr><th>bind</th><th>port</th><th>process</th><th>flag</th></tr>${ls.map(l=>{
    const fl = l.flag||''; const cls = fl.startsWith('NEW')?'flag bad':(fl.indexOf('not in expected')>=0?'flag':'flag okf');
    return `<tr><td>${esc(l.addr)}</td><td>${l.port}${l.name?` <span class="dim">${esc(l.name)}</span>`:''}</td><td>${esc((l.process||'').split(':')[0])||'<span class="dim">hidden</span>'}</td><td class="${cls}">${esc(fl)}</td></tr>`;}).join('')}</table>`;
}
function nodeCard(n){
  const c = n.checks||{};
  const rows = CHECKS.filter(([k])=>c[k]).map(([k,lab])=>`<div class="chk"><div class="dot ${c[k].status}"></div><div class="n">${lab}</div><div class="s">${esc(c[k].summary)}</div></div>`).join('');
  const ex = (c.exposure||{}).details||{}; const lg=(c.logins||{}).details||{};
  const fails = (lg.recent_failures||[]).slice(0,8).map(x=>`${esc(x.ts)} ${esc(x.type)} user=${esc(x.user)} from ${esc(x.ip)}`).join('<br/>')||'none';
  const sess = (lg.sessions||[]).map(x=>`${esc(x.peer)} → ${esc(x.local)}`).join('<br/>')||'none';
  const sshd = ex.sshd&&ex.sshd.effective ? Object.entries(ex.sshd.effective).map(([k,v])=>`${esc(k)}=${esc(v)}`).join(' · ') + ` <span class="dim">(${esc(ex.sshd.method)})</span>` : '—';
  const fnd = (n.findings||[]).map(x=>`<div>• ${esc(x)}</div>`).join('');
  return `<div class="card sec-card">
    <h2>${esc(n.label)} ${sb(n.status)} <span style="flex:1"></span><span class="score ${esc(n.status)}" style="font-size:20px">${n.score==null?'—':n.score}</span></h2>
    <div class="meta">${n.fast_epoch?(`checked <span class="ago" data-e="${n.fast_epoch}">…</span>`+(n.slow_epoch&&n.id!=='mac'?` · config <span class="ago" data-e="${n.slow_epoch}">…</span>`:'')):'not collected yet'}${n.stale?' · <span class="flag bad">unreachable, showing last data</span>':''}${(n.error&&!n.reachable)?(' · '+esc(n.error)):''}</div>
    <div style="margin-top:8px">${rows||'<div class="meta">collecting…</div>'}</div>
    ${fnd?`<details open><summary>findings (${(n.findings||[]).length})</summary><div class="findings">${fnd}</div></details>`:''}
    <details><summary>listening ports</summary>${portsTable(ex.listeners)}</details>
    <details><summary>sshd + remote desktop</summary><div class="dock">${sshd}<br/>RDP/VNC listeners: ${esc((ex.remote_desktop||[]).join(', ')||'none')}</div></details>
    <details><summary>failed logins / live sessions</summary><div class="dock">${fails}<br/><span class="dim">live SSH/RDP:</span> ${sess}<br/><span class="dim">${esc(lg.note||'')}</span></div></details>
  </div>`;
}
function feed(alerts){
  return `<h2>Security alerts <span class="meta" style="margin:0">newest first · alerts.jsonl</span></h2><div class="feed">${(alerts||[]).map(a=>`<div class="row"><span class="lvl-${esc(a.level)}">${esc((a.level||'').toUpperCase())}</span> ${esc(a.ts)} · <b>${esc(a.node_label||a.node)}</b> · ${esc(a.check)} — ${esc(a.msg)}</div>`).join('')||'<div class="meta">no alerts yet</div>'}</div>`;
}
function lan(n){
  const c = ((n||{}).checks||{}).network; if(!c) return '<h2>Home LAN</h2><div class="meta">collecting…</div>';
  const d = c.details||{};
  const rows = (d.devices||[]).map(x=>`<tr><td>${esc(x.ip)}</td><td>${esc(x.mac)}${x.random_mac?' <span class="dim">rand</span>':''}</td><td>${esc(x.iface)}</td><td>${esc(x.label)}</td><td class="${x.known?'flag okf':'flag'}">${x.known?'known':'NEW'}</td></tr>`).join('');
  const ob = (d.outbound||[]).map(o=>`${esc(o.process)} ${o.count}/${o.peers}`).join(' · ')||'—';
  return `<h2>Home LAN (Mac ARP cache) ${sb(c.status)}</h2><div class="meta">${esc(c.summary)} · passive, no scanning</div>
    <div class="section" style="max-height:300px;overflow:auto"><table><tr><th>IP</th><th>MAC</th><th>if</th><th>label</th><th></th></tr>${rows}</table></div>
    <div class="section"><h3>Mac outbound to public IPs (connections/hosts, info only)</h3><div class="dock">${ob}</div></div>`;
}
let skew = 0, lastUpd = 0;
function agoTxt(e){ const s = Math.max(0, Math.round(Date.now()/1000 + skew - e)); return s<90 ? s+'s ago' : (s<5400 ? Math.round(s/60)+' min ago' : Math.round(s/3600)+' h ago'); }
function paintAgo(){ document.querySelectorAll('.ago').forEach(el=>{ const e=parseFloat(el.dataset.e); if(e) el.textContent=agoTxt(e); }); }
setInterval(paintAgo, 1000);
async function secTick(){
  try{
    const r = await fetch('/api/security',{cache:'no-store'}); const j = await r.json();
    if (j.server_epoch) skew = j.server_epoch - Date.now()/1000;
    lastUpd = j.updated_epoch || lastUpd;
    const o = j.overall||{};
    $('sec-score').textContent = o.score==null?'—':o.score; $('sec-score').className='score '+(o.status||'unknown');
    $('sec-overall').innerHTML = o.status? `${sb(o.status)} <span class="meta">${o.alerts||0} alert · ${o.warns||0} warn checks</span>`:'';
    const cd = j.cadence||{};
    $('sec-stamp').innerHTML = `monitor-only · evaluated <span class="ago" data-e="${j.updated_epoch||0}">…</span> · Mac every ${cd.mac_secs||'?'}s, nodes ${cd.fast_secs||'?'}s (config ${cd.slow_secs||'?'}s) · baseline ${esc((j.baseline||{}).updated_at||'none')}`;
    $('sec-nodes').innerHTML = (j.node_order||Object.keys(j.nodes||{})).map(id=>nodeCard((j.nodes||{})[id]||{id,label:id,status:'unknown'})).join('');
    $('sec-alerts').innerHTML = feed(j.alerts);
    $('sec-lan').innerHTML = lan((j.nodes||{}).mac);
    paintAgo();
  }catch(e){ $('sec-stamp').textContent='security fetch error: '+e; }
}
$('sec-accept').addEventListener('click', async()=>{
  if(!confirm('Accept the current state of all nodes (ports, SSH keys, users, sudoers, crontabs, startup units, containers, LAN devices) as the known-good baseline?')) return;
  try{ const r = await fetch('/api/security/baseline',{method:'POST',headers:{'Content-Type':'application/json','X-Node-Dashboard':'1'},body:'{}'}); const j=await r.json(); alert('Baseline saved for: '+(j.nodes||[]).join(', ')); secTick(); }catch(e){ alert('failed: '+e); }
});
$('sec-refresh').addEventListener('click', async()=>{
  const b=$('sec-refresh'); b.disabled=true; b.textContent='Refreshing…';
  const before = lastUpd;
  try{ await fetch('/api/security/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Node-Dashboard':'1'},body:'{}'}); }catch(e){}
  for(let i=0;i<30;i++){ await new Promise(r=>setTimeout(r,1000)); await secTick(); if(lastUpd>before+0.5 && i>=1) break; }
  b.disabled=false; b.textContent='Refresh now';
});
secTick(); setInterval(secTick, 5000);
})();
</script>
"""


def inject_html(html: str) -> str:
    out = html.replace("</style>", SEC_CSS + "</style>", 1)
    out = out.replace("  <footer>", SEC_HTML + "  <footer>", 1)
    out = out.replace('<a href="/api/status">/api/status</a></div>',
                      '<a href="/api/status">/api/status</a> · <a href="#security">Security</a></div>', 1)
    out = out.replace("</body>", SEC_JS + "</body>", 1)
    return out
