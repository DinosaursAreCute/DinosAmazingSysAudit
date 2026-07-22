"""Config loading + defaults.

Search order (first found wins), falling back to built-in defaults if none
exist:
  1. $AUDITSYS_CONFIG (explicit path)
  2. ./audit-system.yaml
  3. ~/.config/auditsys/config.yaml
  4. /etc/audit-system/config.yaml

Store/report locations default to a system path when running as root
(/var/lib/auditsys, /var/log/auditsys/reports) and a per-user path
otherwise (~/.local/share/auditsys, ~/.local/share/auditsys/reports) — this
keeps `audit-cli` usable both as an ad-hoc user tool and as a root-run
service, without forcing a choice up front.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "store": {
        "path": None,  # resolved by get_store_path()
        "retention_days": 90,
    },
    "collectors": {
        "auditd": {"enabled": True},
        "journalctl": {"enabled": True},
        # verbosity controls how much detail docker-attribution events carry:
        #   minimal  -> actor, action, timestamp only
        #   normal   -> + full command line, exit code
        #   verbose  -> + correlated `docker logs`/`docker events` context
        "docker": {"enabled": True, "verbosity": "normal", "correlate_logs": True},
        "stat": {"enabled": True},
    },
    "watched_paths": [
        "/etc",
        "/usr/bin/docker",
        "/usr/bin/docker-compose",
        "/run/docker.sock",
        "/var/lib/docker",
    ],
    "report": {
        "formats": ["html", "csv", "json"],
        "output_dir": None,  # resolved by get_report_dir()
    },
    "timezone": "local",
    "host_label": None,  # override for hostname shown in reports; None -> os hostname
}


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _candidate_paths() -> list[Path]:
    candidates = []
    env_path = os.environ.get("AUDITSYS_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.cwd() / "audit-system.yaml")
    candidates.append(Path.home() / ".config" / "auditsys" / "config.yaml")
    candidates.append(Path("/etc/audit-system/config.yaml"))
    return candidates


def find_config_file() -> Path | None:
    for path in _candidate_paths():
        if path.is_file():
            return path
    return None


def load_config() -> dict[str, Any]:
    config = copy.deepcopy(DEFAULTS)
    path = find_config_file()
    if path is not None:
        with open(path, "r", encoding="utf-8") as fh:
            user_config = yaml.safe_load(fh) or {}
        config = _deep_merge(config, user_config)
    config["_config_file"] = str(path) if path else None
    return config


def get_store_path(config: dict[str, Any]) -> Path:
    explicit = config.get("store", {}).get("path")
    if explicit:
        return Path(explicit).expanduser()
    if _is_root():
        return Path("/var/lib/auditsys/audit.db")
    return Path.home() / ".local" / "share" / "auditsys" / "audit.db"


def get_report_dir(config: dict[str, Any]) -> Path:
    explicit = config.get("report", {}).get("output_dir")
    if explicit:
        return Path(explicit).expanduser()
    if _is_root():
        return Path("/var/log/auditsys/reports")
    return Path.home() / ".local" / "share" / "auditsys" / "reports"


def write_default_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(DEFAULTS, fh, sort_keys=False)
    return path


def default_config_path() -> Path:
    """Where to write a config if none was found on disk yet — matches
    `audit-cli config init`'s default, regardless of root/non-root, so the
    TUI settings screen and the CLI always agree on one canonical spot."""
    return Path.home() / ".config" / "auditsys" / "config.yaml"


def save_config(config: dict[str, Any], path: Path) -> Path:
    """Persist `config` (minus internal `_`-prefixed bookkeeping keys) to
    `path` as YAML. Used by the TUI settings screen so edits survive restart,
    on top of being applied to the live in-memory config immediately."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in config.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(clean, fh, sort_keys=False)
    return path
