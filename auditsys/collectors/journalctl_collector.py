"""Parses journalctl for the sudo audit trail and SSH login/logout events.

This is the "who connected when" + "who ran sudo, what command" collector.
It shells out to `journalctl -o json`, which gives us structured fields
instead of scraping syslog text formatting by hand.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import Event, CoverageWindow
from .. import logger as log
from .base import Collector

_SUDO_CMD_RE = re.compile(
    r"^\s*(?P<user>\S+)\s*:\s*"
    r"(?:TTY=(?P<tty>\S+?)\s*;\s*)?"
    r"(?:PWD=(?P<pwd>\S+?)\s*;\s*)?"
    r"USER=(?P<targetuser>\S+?)\s*;\s*"
    r"COMMAND=(?P<command>.*)$"
)
_SUDO_FAIL_RE = re.compile(r"authentication failure.*user=(?P<user>\S+)")
_SSH_ACCEPTED_RE = re.compile(
    r"Accepted (?P<method>\S+) for (?P<user>\S+) from (?P<ip>\S+) port (?P<port>\d+)"
)
_SSH_FAILED_RE = re.compile(
    r"Failed (?P<method>\S+) for (?:invalid user )?(?P<user>\S+) from (?P<ip>\S+) port (?P<port>\d+)"
)
_SSH_DISCONNECT_RE = re.compile(
    r"Disconnected from user (?P<user>\S+) (?P<ip>\S+) port (?P<port>\d+)"
)


def _usec_to_iso(usec: str) -> str:
    dt = datetime.fromtimestamp(int(usec) / 1_000_000, tz=timezone.utc)
    return dt.isoformat()


def _iso_to_journalctl_since(ts: str) -> str:
    dt = datetime.fromisoformat(ts)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


class JournalctlCollector(Collector):
    name = "journalctl"

    def available(self) -> tuple[bool, str]:
        if shutil.which("journalctl") is None:
            return False, "journalctl binary not found"
        try:
            subprocess.run(
                ["journalctl", "-n", "1"], capture_output=True, timeout=5, check=False
            )
        except Exception as exc:  # noqa: BLE001 - report any failure as unavailable
            return False, f"journalctl not usable: {exc}"
        return True, ""

    def _run_journalctl(self, args: list[str]) -> list[dict]:
        cmd = ["journalctl", "-o", "json", "--no-pager"] + args
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as exc:  # noqa: BLE001
            log.warn(f"journalctl invocation failed ({' '.join(cmd)}): {exc}")
            return []
        if proc.returncode != 0 and not proc.stdout:
            log.warn(f"journalctl returned {proc.returncode}: {proc.stderr.strip()}")
            return []
        entries = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def coverage(self) -> CoverageWindow:
        earliest = self._run_journalctl(["-n", "1", "--reverse"])
        latest = self._run_journalctl(["-n", "1"])
        start_ts = _usec_to_iso(earliest[0]["__REALTIME_TIMESTAMP"]) if earliest else None
        end_ts = _usec_to_iso(latest[0]["__REALTIME_TIMESTAMP"]) if latest else None
        note = (
            "Bounded by journald retention/vacuum settings on this host; "
            "not a complete historical record."
        )
        return CoverageWindow(source=self.name, host=self.host, start_ts=start_ts,
                              end_ts=end_ts, note=note)

    def sync(self, since_ts: Optional[str]) -> Iterable[Event]:
        since_args = ["--since", _iso_to_journalctl_since(since_ts)] if since_ts else []

        for entry in self._run_journalctl(["_COMM=sudo"] + since_args):
            yield from self._parse_sudo_entry(entry)

        for entry in self._run_journalctl(["-t", "sshd"] + since_args):
            yield from self._parse_sshd_entry(entry)

    def _parse_sudo_entry(self, entry: dict) -> Iterable[Event]:
        message = entry.get("MESSAGE", "")
        ts = _usec_to_iso(entry["__REALTIME_TIMESTAMP"])
        m = _SUDO_CMD_RE.match(message)
        if m:
            yield Event(
                ts=ts, source=self.name, category="sudo", action="sudo_exec",
                actor=m.group("user"), target=m.group("command"), host=self.host,
                detail={
                    "tty": m.group("tty"), "pwd": m.group("pwd"),
                    "as_user": m.group("targetuser"), "raw": message,
                },
            )
            return
        m = _SUDO_FAIL_RE.search(message)
        if m:
            yield Event(
                ts=ts, source=self.name, category="sudo", action="sudo_fail",
                actor=m.group("user"), target=None, host=self.host,
                detail={"raw": message},
            )

    def _parse_sshd_entry(self, entry: dict) -> Iterable[Event]:
        message = entry.get("MESSAGE", "")
        ts = _usec_to_iso(entry["__REALTIME_TIMESTAMP"])
        m = _SSH_ACCEPTED_RE.search(message)
        if m:
            yield Event(
                ts=ts, source=self.name, category="auth", action="login",
                actor=m.group("user"), target=m.group("ip"), host=self.host,
                detail={"method": m.group("method"), "port": m.group("port"), "raw": message},
            )
            return
        m = _SSH_FAILED_RE.search(message)
        if m:
            yield Event(
                ts=ts, source=self.name, category="auth", action="login_fail",
                actor=m.group("user"), target=m.group("ip"), host=self.host,
                detail={"method": m.group("method"), "port": m.group("port"), "raw": message},
            )
            return
        m = _SSH_DISCONNECT_RE.search(message)
        if m:
            yield Event(
                ts=ts, source=self.name, category="auth", action="logout",
                actor=m.group("user"), target=m.group("ip"), host=self.host,
                detail={"port": m.group("port"), "raw": message},
            )
