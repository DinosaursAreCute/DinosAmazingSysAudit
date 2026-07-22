#!/usr/bin/env bash
# shellcheck disable=SC1091
#
# Reverses scripts/install.sh: stops/removes the systemd timer, removes the
# auditd watch/exec rules, deletes the isolated venv(s) and wrapper commands,
# strips the PATH lines added to shell rc files, and removes config.
#
# The audit DATABASE and generated reports are left alone by default — that's
# collected evidence, not an install artifact — pass --purge-data to also
# remove those.
set -euo pipefail

PURGE_DATA=0
VENV_DIR=""
BIN_DIR=""

usage() {
    cat <<EOF
Usage: $0 [--purge-data] [--venv-dir PATH] [--bin-dir PATH]

  --purge-data     also delete the audit database and generated reports
                    (/var/lib/auditsys, /var/log/auditsys, ~/.local/share/auditsys)
  --venv-dir PATH  venv location to remove, if not one of the defaults
                    (/opt/auditsys/venv, ~/.local/share/auditsys/venv)
  --bin-dir PATH   wrapper location to remove, if not one of the defaults
                    (/usr/local/bin, ~/.local/bin)

Safe to re-run — every step tolerates things already being gone.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge-data) PURGE_DATA=1; shift ;;
        --venv-dir) VENV_DIR="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown arg: $1" >&2; usage; exit 1 ;;
    esac
done

log() { echo "[uninstall] $*"; }
have_sudo() { command -v sudo >/dev/null 2>&1; }

log "stopping/removing systemd timer + service (if present)"
if have_sudo; then
    sudo systemctl disable --now audit-system-sync.timer >/dev/null 2>&1 || true
    sudo rm -f /etc/systemd/system/audit-system-sync.service /etc/systemd/system/audit-system-sync.timer
    sudo systemctl daemon-reload || true
fi

log "removing auditd watch/exec rules"
if have_sudo && command -v auditctl >/dev/null 2>&1; then
    sudo rm -f /etc/audit/rules.d/auditsys.rules
    sudo auditctl -D -k auditsys >/dev/null 2>&1 || true
    if command -v augenrules >/dev/null 2>&1; then
        sudo augenrules --load >/dev/null 2>&1 || true
    fi
fi

log "removing venv(s)"
for dir in "/opt/auditsys/venv" "$HOME/.local/share/auditsys/venv" "$VENV_DIR"; do
    [[ -n "$dir" ]] || continue
    if [[ -d "$dir" ]]; then
        if [[ -w "$(dirname "$dir")" ]]; then
            rm -rf "$dir"
        elif have_sudo; then
            sudo rm -rf "$dir"
        fi
        log "removed $dir"
    fi
done

log "removing wrapper commands"
for dir in "/usr/local/bin" "$HOME/.local/bin" "$BIN_DIR"; do
    [[ -n "$dir" ]] || continue
    for cmd in audit-cli audit-tui auditsys; do
        target="$dir/$cmd"
        if [[ -e "$target" || -L "$target" ]]; then
            if [[ -w "$dir" ]]; then
                rm -f "$target"
            elif have_sudo; then
                sudo rm -f "$target"
            fi
            log "removed $target"
        fi
    done
done

log "stripping PATH lines added to shell rc files"
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [[ -f "$rc" ]] && grep -qF "# added by auditsys installer" "$rc"; then
        # remove the marker line and the export line immediately after it
        sed -i '/# added by auditsys installer/,+1d' "$rc"
        log "cleaned PATH entry from $rc"
    fi
done

log "removing config"
rm -rf "$HOME/.config/auditsys"
if have_sudo; then
    sudo rm -rf /etc/audit-system
fi

if [[ "$PURGE_DATA" -eq 1 ]]; then
    log "PURGING audit database and reports (--purge-data was passed)"
    rm -rf "$HOME/.local/share/auditsys"
    if have_sudo; then
        sudo rm -rf /var/lib/auditsys /var/log/auditsys
    fi
else
    log "leaving the audit database/reports in place — pass --purge-data to remove those too"
    log "  (~/.local/share/auditsys, /var/lib/auditsys, /var/log/auditsys)"
fi

cat <<EOF

Done. Removed: systemd timer/service, auditd rules, venv(s), wrapper
commands, PATH rc entries, config.
$( [[ "$PURGE_DATA" -eq 1 ]] && echo "Also removed: audit database and reports." || echo "Audit database and reports were left in place." )

The extracted project source directory itself (wherever you ran 'tar xzf'
from) isn't touched by this script — remove that manually:
  rm -rf /path/to/audit-system
EOF
