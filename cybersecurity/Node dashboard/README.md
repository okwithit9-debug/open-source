# Node dashboard

A small, **read-only** live dashboard for a home or lab GPU cluster: **one Mac + N Linux / NVIDIA DGX nodes reached over SSH**. It shows GPU and cluster health at a glance, and it also runs a **monitor-only security section** for every machine: logins, open ports, firewall and SSH settings, key, user, cron and startup changes, pending updates, new LAN devices, a score per node and an alert feed.

It is two Python files with **no dependencies** (standard library only). It runs on the Mac, talks to each node over your existing SSH keys and serves a single page on `http://127.0.0.1:8095/`.

> Built for a DGX Spark (GB10) cluster, but nothing is Spark-specific. Any Linux box with `bash`, `ss` and (optionally) `nvidia-smi` and `docker` works as a node.

---

## What it monitors

### Cluster panels (refresh every ~3.5 s)

One card per node:

- **Status:** online, **OFFLINE** (with last-seen time), BUSY, or IDLE / IDLE (serving)
- **Host:** hostname, uptime, load average
- **GPU:** temperature, utilisation and power from `nvidia-smi`, with sparkline history
- **Memory:** from `/proc/meminfo`. This handles unified-memory GPUs such as GB10, where `nvidia-smi` reports memory as N/A. Per-process GPU memory is shown when the driver reports it.
- **CPU:** utilisation (from `/proc/stat` deltas) and max thermal-zone temperature
- **Disk:** free space on `/`
- **Network:** RX/TX in Mb/s per interface (from `/sys/class/net/*/statistics` deltas). You can give interfaces friendly names such as "LAN Wi-Fi" or "Fabric -> Node 2". Interfaces whose label contains `Fabric` are sorted first and charted.
- **Docker:** container list (read only)
- **Tensor-parallel role (optional):** `--tp` / `--rank` / `--master`, parsed from your inference container's `Cmd`

Optional panels:

- **Inference endpoint:** any OpenAI-compatible server, such as vLLM, TensorRT-LLM, llama.cpp, or an SSH-forwarded port. It reads `/health` and `/v1/models` and shows answering or down, the model ids, `requests_running`, streams and the TP layout across nodes.
- **Local model on the Mac:** `/v1/models` on a local port, plus an optional launchd job (loaded or pid) and a log tail.

### Security section (monitor only)

Every node, including the Mac itself, gets eight checks. Each check is **OK / WARN / ALERT**:

| Check | Linux nodes (over SSH, no sudo) | Mac (local) |
|---|---|---|
| **Logins / sessions** | sshd failed and invalid attempts in the last 1 h and 24 h, counted per connection, with source IPs, from `/var/log/auth.log` (`adm` group) or the journal. Also: accepted logins by method and IP, `who`, `last`, and live SSH/RDP connections. Logins from your home IPv6 prefixes count as "home". | `log show` for sshd, `who`, `last`, inbound :22 / :5900 |
| **Exposure: open ports vs known-good** | `ss -tlnp` compared with the baseline and with `expected_ports` from config. A new port bound to all interfaces is an **ALERT**. Also reads the effective sshd `PasswordAuthentication` / `PermitRootLogin` (parsed from the config files). | `netstat` listeners plus process names, Remote Login state |
| **Firewall / SSH config** | ufw enabled, ufw / nftables / firewalld service state, sshd config | Application Firewall, stealth mode, FileVault, SIP, Gatekeeper |
| **Integrity: key, user, cron and startup changes** | `authorized_keys` fingerprints and hash, sudoers (hash if readable, otherwise size, mtime and owner), user crontab hash, `/etc/cron.d`, enabled systemd units, login-shell users, extra UID 0 accounts, docker containers | `authorized_keys`, sudoers metadata, crontab hash, LaunchAgents / LaunchDaemons hashes, local users |
| **Updates pending** | update-notifier counts, `apt list --upgradable` (security count), apt stamp age, reboot-required | `softwareupdate -l` (cached, runs in the background) |
| **Kernel** | 24 h kernel log: soft lockups, hung tasks, OOM kills, NVIDIA Xid errors, Oops / BUG | n/a |
| **Tunnel** (optional) | a named cloudflared container is up, plus its `/ready` metrics endpoint | HEAD request to your public URL every 5 min |
| **New LAN devices** | n/a | the Mac's own ARP cache (`arp -an`, **no scanning**) compared with the baseline MAC addresses; randomised MACs are flagged. Outbound public connections by process are shown as information only. |

**Scores:** each node scores `100 − 20 per ALERT check − 7 per WARN check`, and the overall score is the mean of the node scores.

**Alert feed:** findings are de-duplicated and written to an append-only `alerts.jsonl` when they appear and when they clear. The feed is shown newest first.

**Baseline accept:**

- **First run:** a baseline is created automatically for each node. This automatic baseline does **not** acknowledge services already listening on all interfaces, so those stay WARN until you accept them.
- **Accept everything:** "Accept current as baseline" in the UI, or `POST /api/security/baseline`.
- **One node:** `?node=node1`.
- **One part of one node (scoped accept):** `?node=node1&scope=ports`. Scopes are `launchd`, `ak` (authorized_keys), `sudoers`, `cron_user`, `cron_sys`, `users`, `units`, `docker` and `ports`.

### Refresh intervals (all can be overridden with environment variables)

| What | Default | Env |
|---|---|---|
| Cluster panels (server poll) | 3.5 s | `NODE_DASH_POLL` |
| Mac security checks (local, cheap) | 20 s | `NODE_SEC_MAC` |
| Node security: logins, sessions, ports, docker, tunnel | 30 s | `NODE_SEC_FAST` |
| Node security: sshd config, firewall, integrity hashes, kernel log, update notifier | 120 s (in the **same** ssh exec as the 30 s check) | `NODE_SEC_SLOW` |
| `apt list --upgradable` | hourly | `NODE_SEC_APT` |
| macOS `softwareupdate -l` | every 6 h | `NODE_SEC_SWU` |
| macOS sshd log (1 h window) | every 15 min | `NODE_SEC_MACLOG` |
| Browser UI refresh | 5 s ("Refresh now" button forces a full recheck) | n/a |

---

## Design principles

- **Read only.** It never changes firewall rules, sshd, users, services, packages, containers or the network, on any machine. The only things it writes are its own cache and state files under `~/.cache/node-dashboard/` on the machine running it.
- **No sudo.** Every check runs as your normal user. Checks that need root are skipped or degrade gracefully. For example, `/var/log/auth.log` needs the `adm` group, and a sudoers file you cannot read is tracked by its metadata instead of its hash.
- **One SSH command per node per cycle.** All of a node's checks are batched into a single `ssh <node> bash -s` over an SSH **ControlMaster** socket (`ControlPersist=120`), so polling costs almost nothing and doesn't spam auth logs. Timeouts are short, and an offline node fails soft without blocking the others.
- **No AI and no cloud.** No LLM calls, telemetry, external services or accounts. The only outbound request is the optional tunnel HEAD probe to *your own* URL.
- **No secrets stored.** It never reads passwords or private keys. It records only key **fingerprints**, file hashes and sshd log lines.
- **Standard library only.** No `pip install`, no build step: one HTML page, inline CSS and JS.

---

## Requirements

- **The machine running it:** macOS with Python 3.9+ (`/usr/bin/python3` works). Linux also works, but the Mac-specific security checks then show errors or n/a.
- **Each node:** Linux with `bash`, `ss` (iproute2), `awk` and `journalctl`. `nvidia-smi` and `docker` are optional: without them those panels stay empty.
- **SSH:** passwordless key login from the Mac to each node. The dashboard uses `BatchMode=yes`, so it never prompts. See `examples/ssh_config.example`.
- **Optional:** add your node user to the `adm` group (`sudo usermod -aG adm $USER`, a one-time change you make yourself) so failed logins can be read from `/var/log/auth.log` without sudo. Otherwise it falls back to the journal.

## Setup

```bash
git clone https://github.com/<you>/<this-repo>.git
cd "<this-repo>/cybersecurity/Node dashboard"
cp config.example.json config.json      # config.json is git-ignored
$EDITOR config.json                     # your nodes, ssh aliases, LAN IPs, expected ports
ssh node1 true                          # make sure key login works for every node
python3 server.py
open http://127.0.0.1:8095/
```

## Configuration (`config.json`)

The dashboard looks for `$NODE_DASH_CONFIG`, then `config.json`, then `config.example.json`, all next to `server.py`.

| Key | Meaning |
|---|---|
| `title`, `bind`, `port`, `poll_secs`, `ssh_timeout` | Page title, listen address (keep `127.0.0.1`), port, poll interval, SSH connect timeout |
| `local.label`, `local.expected_ports` | Display name of the Mac, and ports the Mac is expected to listen on for all interfaces |
| `nodes[]` | One entry per Linux node. Fields:<br>`id`: short, letters, digits, `-` and `_`<br>`label`<br>`ssh`: a Host alias from `~/.ssh/config`, or `user@host`<br>`ssh_hostname` (optional): forces `-o HostName=…`, for when another tool rewrites your ssh config<br>`lan`: shown on the card<br>`iface_labels`: interface name → friendly label<br>`expected_ports`: port → name of services expected to listen on all interfaces |
| `inference` | Optional OpenAI-compatible endpoint. Fields: `enabled`, `label`, `base_url`, `health_path` (empty to skip), `models_path`, `container` (the docker name whose `Cmd` has `--tp/--rank`), `container_prefix` (a running container with this prefix marks the node "serving") |
| `local_model` | Optional model server on the Mac. Fields: `enabled`, `label`, `base_url`, `launchd_label`, `log_path` |
| `security.lan_labels` | IP → friendly name in the LAN device table |
| `security.home_ipv6_prefixes` | Your home IPv6 prefixes (CIDR), so logins from them count as "home". Global /64s seen on your machines are also learned automatically and remembered for 30 days. |
| `security.port_names` | Extra port → name labels |
| `security.tunnel` | `null`, or `{"node": "node1", "container": "cloudflared", "metrics_port": 20241, "public_url": "https://example.com/"}` |

Environment overrides:

- `NODE_DASH_PORT`, `NODE_DASH_BIND`, `NODE_DASH_POLL`, `NODE_DASH_SSH_TIMEOUT`
- `NODE_DASH_STATE` (state file; the security data lives next to it under `security/`)
- `NODE_DASH_CTRL` (ControlMaster socket dir, default `/tmp/node-dashboard-ssh`)
- `NODE_SEC_DIR`, `NODE_SEC_HOME_V6` (comma-separated CIDRs), and the `NODE_SEC_*` intervals above

## Running it in the background

**macOS (launchd).** Edit the paths in `examples/com.example.node-dashboard.plist`, then:

```bash
cp examples/com.example.node-dashboard.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.example.node-dashboard.plist
# stop / remove
launchctl bootout "gui/$(id -u)/com.example.node-dashboard"
```

Run it as a per-user **LaunchAgent**, not a root LaunchDaemon, so it uses your SSH keys and needs no root.

**Linux (systemd user unit).** Use `examples/node-dashboard.service`:

```bash
mkdir -p ~/.config/systemd/user && cp examples/node-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now node-dashboard
```

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/` | The dashboard page |
| GET | `/api/status` | Cluster panels JSON: nodes, inference, local model, history |
| GET | `/healthz` | `{"ok":true}` |
| GET | `/api/security` | Full security view: per-node checks, scores, findings, the last 100 alerts, baseline info, cadence |
| GET | `/api/security/alerts` | Alert feed only |
| POST | `/api/security/baseline[?node=ID[&scope=SCOPE]]` | Accept the current state as the baseline (all nodes, one node, or one scope of one node) |
| POST | `/api/security/refresh` | Run an immediate full recheck (rate-limited to one every 5 s) |

POST requests must send the header `X-Node-Dashboard: 1`. This is a simple CSRF guard: a plain cross-site form POST from another web page gets `403`.

```bash
curl -s -X POST -H 'X-Node-Dashboard: 1' http://127.0.0.1:8095/api/security/refresh
curl -s -X POST -H 'X-Node-Dashboard: 1' 'http://127.0.0.1:8095/api/security/baseline?node=node1&scope=ports'
```

## Security notes

- **Bind to `127.0.0.1` only (the default).** There is **no authentication**. The page shows hostnames, IPs, login sources, open ports and LAN devices, which is a map of your network. Don't put it on `0.0.0.0`, a public interface, a port-forward or a public tunnel. To view it from another machine, use an SSH tunnel: `ssh -L 8095:127.0.0.1:8095 your-mac`.
- **It's a monitor, not a firewall.** Use what it finds to decide on changes (key-only SSH, enabling ufw, closing ports), and make those changes yourself.
- **Don't publish your data.** The cache directory (`~/.cache/node-dashboard/`) and `config.json` contain your real IPs, MACs and fingerprints, so keep them out of git. `.gitignore` already excludes `config.json`.
- `StrictHostKeyChecking=accept-new` is used for the ControlMaster connections. Pre-populate `known_hosts` if you want strict pinning from the first connection.
- If `security.py` fails to import, the cluster panels keep working without it.

## Files

```
server.py                 HTTP server, cluster collectors, page
security.py               monitor-only security collectors, baseline, alerts, security UI
config.example.json       example config (a Mac + 3 nodes); copy to config.json
examples/com.example.node-dashboard.plist   launchd LaunchAgent example
examples/node-dashboard.service             systemd user unit example
examples/ssh_config.example                 ~/.ssh/config example
```

## License

MIT. See the [LICENSE](../../LICENSE) at the repository root.
