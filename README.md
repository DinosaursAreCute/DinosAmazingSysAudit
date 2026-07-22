# auditsys

A modular "who did what, and when" tool for a small fleet of Linux servers —
built after a test server's whole docker stack got nuked because someone
connected to the wrong box, and it took hours of manual digging to find out
who and what happened.

It is a **query and reporting layer over auditd + journalctl + the docker
daemon's own event log**, not a monitoring daemon that magically knows
history nobody logged. See [Limitations](#limitations) before you trust an
answer.

## What it answers

- **"Who connected to this server, and when?"** — SSH login/logout, via journalctl.
- **"Who ran sudo, and what command?"** — sudo audit trail, via journalctl.
- **"Who touched this file, and what did they do to it?"** — file blame
  (create/modify/delete/rename/chmod/chown), via auditd watch rules.
- **"Who ran `docker ...`, and what did it actually do to the daemon?"** —
  the incident-response feature. auditd's `loginuid` (`auid`) attributes the
  command back to the *real logged-in user* even when it was run via
  `sudo`/`su` as root, and it's correlated against the docker daemon's own
  event log (`docker events`) so you see both "alice ran `docker system
  prune -af`" and "→ 3 containers destroyed" as one picture. Best-effort
  `docker logs` pulls for touched containers, if still available.

Exposed via:
- **`audit-cli`** — scriptable, filterable, pipeable (`--format json` for `jq`).
- **`audit-tui`** — interactive browser (Textual) for poking around: a file
  tree for blame (click a directory to auto-check it recursively, click a
  file for its exact history), per-user activity timelines, and a
  **Settings screen** — toggle collectors, edit watched paths, retention,
  docker verbosity, and report output dir right there; Save applies it to
  the running session immediately and writes it to your config file so it
  survives restart too.
- **`audit-cli report`** — generates a file: a self-contained **interactive
  HTML** report (click a chart bar or an actor name to filter the table, no
  external JS/CDN dependency so it works on an air-gapped box), or **CSV/JSON**
  for feeding other tools.

## Quickstart

```bash
# 1. Install system deps + the package (detects apt/dnf/yum/pacman/zypper)
./scripts/install.sh --with-timer

# 2. Install the auditd rules that make file/docker attribution work
#    (this is the fix for "who ran the command that nuked everything")
sudo ./bin/install-audit-rules.sh

# 3. Pull in events (needs root for full coverage — see Permissions below)
sudo audit-cli sync

# 4. Ask questions
audit-cli blame /etc/nginx/nginx.conf
audit-cli sudo --user alice --since "2026-07-22 00:00:00"
audit-cli docker --since "2026-07-22 09:00:00" --verbosity verbose --with-logs
audit-cli who /var/lib/docker/volumes/webapp-data

# 5. Or browse interactively
audit-tui

# 6. Or generate a shareable report
audit-cli report --format html --category docker --since "2026-07-22 09:00:00"
```

Each server is queried independently (per your setup) — run the same
`audit-cli`/`audit-tui` on whichever box you need to investigate. There's no
central aggregation server in this version; `report --format json` on each
host gives you a consistent, diffable export if you want to eyeball
multiple hosts side by side.

## Permissions

`audit-cli sync` (and the systemd timer, which already runs as root) needs
**root** for full coverage:

- `ausearch`/`auditctl` (the `auditd` collector) refuse to run as a normal
  user — without root, that collector silently reports 0 events every time,
  with no error, which is worse than an obvious failure.
- The docker socket (`/run/docker.sock`) is normally root-only unless your
  user is in the `docker` group — without access, the `docker` collector
  reports unavailable.
- `journalctl` and the `stat` fallback work fine unprivileged, so
  `audit-cli blame`/`sudo`/`docker`/`who` (read-only queries against the
  already-synced store) don't need root at all — only `sync` does.

If a collector reports "unavailable," check whether the *reason* is a
permissions problem (run `sync` under `sudo`) or something else — e.g.
`docker: ... no such file or directory` means the docker daemon itself
isn't running (`sudo systemctl start docker`), not a permissions issue;
`sudo` won't fix that one.

**Gotcha:** because `sync` needs root but the query commands don't, running
`sudo audit-cli sync` and plain `audit-cli blame ...` can silently read/write
*two different databases* — the store path auto-picks `/var/lib/auditsys/`
when run as root vs `~/.local/share/auditsys/` otherwise, and `sudo` resets
`$HOME` to root's on some distros (including Arch-based ones), which also
changes which config file gets loaded. If `sudo` and non-`sudo` runs seem to
disagree about what's in the store, this is almost always why. Fix it once
and for all by setting an explicit, invocation-invariant path in your config:

```yaml
store:
  path: /var/lib/auditsys/audit.db   # always this file, regardless of who runs the command
```

(and make sure whichever user runs read-only queries can actually read that
path — `chmod`/group ownership as appropriate.)

## Architecture

```
collector (auditd / journalctl / docker / stat)
     → normalizes into a unified Event
        → SQLite store (~/.local/share/auditsys/audit.db, or
          /var/lib/auditsys/audit.db if run as root)
           → CLI / TUI / report renderers (all read the same store)
```

Every collector implements the same contract
(`auditsys/collectors/base.py`: `available()`, `coverage()`, `sync()`), so
adding a new source (git, samba, a SIEM export, ...) means writing one file
and registering it in `auditsys/collectors/__init__.py` — nothing else
changes.

`utils/sys_logger.sh`'s conventions (timestamped, colored, leveled,
console+file) are ported to `auditsys/logger.py` for the tool's *own*
operational logging — that's separate from the audit **event** data, which
lives in the SQLite store, not in a log file.

## Configuration

Copy `etc/audit-system.yaml.example` to one of:
- `./audit-system.yaml`
- `~/.config/auditsys/config.yaml`
- `/etc/audit-system/config.yaml`

or point `$AUDITSYS_CONFIG` at a custom path. `audit-cli config init` writes
the defaults for you. Key knobs: `watched_paths` (what
`install-audit-rules.sh` sets up watches for), `collectors.docker.verbosity`
(`minimal`/`normal`/`verbose` — controls how much correlated detail docker
events carry), `report.output_dir`, `store.retention_days`.

## Limitations — read this before trusting an answer

Linux does not store "who created this file" as file metadata. The only
history that exists is what was **already being logged** when the event
happened:

- **auditd** only has history from when its watch rules were installed and
  while it was running. `audit-cli coverage` shows you the actual window per
  source — the tool never silently implies "complete history."
- **journalctl** is bounded by `journald`'s retention/vacuum settings on
  that host.
- **`docker events`** only covers what the running daemon still buffers,
  and **removing a container destroys its `docker logs`** unless you've
  configured a persistent log driver — `--with-logs` will tell you plainly
  when logs are gone rather than pretending they aren't.
- If a path has no auditd coverage, the `stat` collector fills in with
  current owner + mtime/ctime only — tagged `confidence: low` everywhere,
  never presented as verified history.

**Practical takeaway:** the sooner you run `bin/install-audit-rules.sh` on a
box, the better its coverage window looks the next time something goes
wrong. This tool can't retroactively fix today's incident on a server that
wasn't already being watched — but it makes sure the *next* one is
answerable in minutes, not hours.

## Existing software this complements (not replaces)

- **Falco** — purpose-built container/runtime syscall auditing; consider it
  as a future collector for deeper docker/k8s behavior than execve-watching
  can see.
- **Laurel** — turns raw auditd logs into structured JSON; worth adopting
  as a preprocessing step if `ausearch` parsing ever becomes a bottleneck.
- **tlog** — full terminal session recording (everything typed, not just
  docker), if you need that level of detail.
- **Wazuh** — if this ever needs to become a real multi-host SIEM with
  central dashboards/alerting instead of "query each server," this is the
  natural next step up.

## Testing

```bash
pip install --break-system-packages -e .
pip install --break-system-packages pytest
pytest tests/ -v
```

Tests use fixture text (synthetic `ausearch`/`journalctl` output), not a
live auditd/journald — they specifically pin down the loginuid-through-sudo
attribution behavior and the docker exec↔daemon-event correlation logic.

## Project layout

```
auditsys/
  logger.py, config.py, models.py, store.py     # core
  collectors/                                    # auditd, journalctl, docker, stat
  cli.py                                          # audit-cli
  tui/app.py                                      # audit-tui
  reports/                                        # html/csv/json renderers
bin/install-audit-rules.sh                        # root-only auditd rule installer
scripts/install.sh                                # multi-distro system installer
systemd/                                          # periodic sync service+timer
etc/audit-system.yaml.example
tests/
```
