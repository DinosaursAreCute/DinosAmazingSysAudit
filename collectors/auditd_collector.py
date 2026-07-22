"""Parses auditd (via `ausearch`) for file watch + execve events.

The critical property this collector relies on: auditd's `auid` (loginuid)
field records the *original login user*, and survives `su`/`sudo` — so a
`docker system prune` run as root via sudo still attributes back to the
human who logged in. This is the fix for "someone connected and nuked
everything" style incidents: journalctl tells you *who connected*, this
tells you *what they actually executed*, tied to the same identity.

Expects watch/exec rules to already be installed (see
`bin/install-audit-rules.sh`), tagged with `-k auditsys` so we can pull just
our events with `ausearch -k auditsys`.
"""
from __future__ import annotations

import pwd
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import Event, CoverageWindow
from .. import logger as log
from .base import Collector

AUDIT_KEY = "auditsys"

_KV_RE = re.compile(r'(\w+)=("[^"]*"|\S+)')
# `ausearch -i` (interpreted) prints a human-readable local timestamp, not a
# raw epoch: msg=audit(MM/DD/YYYY HH:MM:SS.mmm:serial). Raw (non -i) mode
# would print msg=audit(epoch.ms:serial) instead — we standardize on -i so
# uid/auid come back as resolved usernames too, so only handle this format.
_MSG_RE = re.compile(
    r"msg=audit\((?P<date>\d{2}/\d{2}/\d{4}) (?P<time>\d{2}:\d{2}:\d{2}\.\d+):(?P<id>\d+)\)"
)

# best-effort syscall -> action mapping; auditd/interpreted syscall names.
_SYSCALL_ACTION = {
    "unlink": "delete", "unlinkat": "delete",
    "rename": "rename", "renameat": "rename", "renameat2": "rename",
    "chmod": "chmod", "fchmod": "chmod", "fchmodat": "chmod",
    "chown": "chown", "fchown": "chown", "fchownat": "chown", "lchown": "chown",
    "execve": "docker_exec",  # refined further based on target path
    "execveat": "docker_exec",
    "open": "modify", "openat": "modify", "creat": "modify",
    "mkdir": "create", "mkdirat": "create",
}

_DOCKER_PATH_HINTS = ("docker", "containerd", "runc")


def _uid_to_name(uid: Optional[str]) -> Optional[str]:
    if uid is None:
        return None
    try:
        uid_int = int(uid)
    except ValueError:
        return uid
    if uid_int == 4294967295:  # unset loginuid (kernel thread / no login)
        return None
    try:
        return pwd.getpwuid(uid_int).pw_name
    except KeyError:
        return f"uid:{uid_int}"


def _unquote(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == '"' and v[-1] == '"' else v


class AuditdCollector(Collector):
    name = "auditd"

    def available(self) -> tuple[bool, str]:
        if shutil.which("ausearch") is None:
            return False, "ausearch not found (auditd not installed?)"
        proc = subprocess.run(
            ["ausearch", "-k", AUDIT_KEY, "-ts", "recent"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode not in (0, 1):  # 1 = "no matches", still means it works
            reason = proc.stderr.strip() or "unknown error"
            if "you must be root" in reason.lower() or "permission" in reason.lower():
                return False, "ausearch requires root privileges"
            return False, f"ausearch not usable: {reason}"
        return True, ""

    def _run_ausearch(self, extra: list[str]) -> str:
        cmd = ["ausearch", "-k", AUDIT_KEY, "-i"] + extra
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as exc:  # noqa: BLE001
            log.warn(f"ausearch invocation failed: {exc}")
            return ""
        if proc.returncode not in (0, 1):
            log.warn(f"ausearch returned {proc.returncode}: {proc.stderr.strip()}")
        return proc.stdout

    def coverage(self) -> CoverageWindow:
        raw = self._run_ausearch(["-ts", "today"])
        groups = self._group_records(raw) if raw else []
        # Fall back to a wider window if nothing today, just to report bounds.
        if not groups:
            raw = self._run_ausearch([])
            groups = self._group_records(raw) if raw else []
        timestamps = [g["_ts"] for g in groups if "_ts" in g]
        rules_installed, rules_note = self._check_rules()
        note = (
            f"Only covers events since the auditsys watch rules were "
            f"installed and while auditd was running. {rules_note}"
        )
        return CoverageWindow(
            source=self.name, host=self.host,
            start_ts=min(timestamps) if timestamps else None,
            end_ts=max(timestamps) if timestamps else None,
            note=note,
        )

    def _check_rules(self) -> tuple[bool, str]:
        if shutil.which("auditctl") is None:
            return False, "auditctl not found to verify rule status."
        proc = subprocess.run(["auditctl", "-l"], capture_output=True, text=True, timeout=10)
        if AUDIT_KEY in proc.stdout:
            return True, "Watch rules currently installed."
        return False, (
            "No auditsys watch rules currently installed — run "
            "bin/install-audit-rules.sh to enable file/docker attribution."
        )

    def sync(self, since_ts: Optional[str]) -> Iterable[Event]:
        extra = []
        if since_ts:
            dt = datetime.fromisoformat(since_ts)
            extra = ["-ts", dt.astimezone().strftime("%m/%d/%Y"), dt.astimezone().strftime("%H:%M:%S")]
        raw = self._run_ausearch(extra)
        for group in self._group_records(raw):
            event = self._group_to_event(group)
            if event:
                yield event

    # -- parsing -----------------------------------------------------------
    def _group_records(self, raw: str) -> list[dict]:
        groups: list[dict] = []
        current: dict = {}
        current_argv: dict[int, str] = {}
        for line in raw.splitlines():
            if line.startswith("----"):
                if current:
                    if current_argv:
                        current["_argv"] = [current_argv[i] for i in sorted(current_argv)]
                    groups.append(current)
                current = {}
                current_argv = {}
                continue
            m = _MSG_RE.search(line)
            if m and "_ts" not in current:
                naive = datetime.strptime(f"{m.group('date')} {m.group('time')}", "%m/%d/%Y %H:%M:%S.%f")
                current["_ts"] = naive.astimezone().isoformat()
            is_execve_line = line.strip().startswith("type=EXECVE")
            for key, value in _KV_RE.findall(line):
                value = _unquote(value)
                if is_execve_line and re.fullmatch(r"a\d+", key):
                    current_argv[int(key[1:])] = value
                    continue
                # don't overwrite an earlier, more specific value (e.g. keep
                # the first "name=" PATH record if several are present)
                current.setdefault(key, value)
        if current:
            if current_argv:
                current["_argv"] = [current_argv[i] for i in sorted(current_argv)]
            groups.append(current)
        return groups

    def _group_to_event(self, g: dict) -> Optional[Event]:
        ts = g.get("_ts")
        if not ts:
            return None
        actor = _uid_to_name(g.get("auid")) or _uid_to_name(g.get("uid"))
        uid_val = int(g["uid"]) if g.get("uid", "").isdigit() else None
        syscall = g.get("syscall")
        exe = g.get("exe")
        path_name = g.get("name")
        target = path_name or exe or g.get("comm")

        is_docker_related = any(
            hint in (target or "").lower() or hint in (exe or "").lower()
            for hint in _DOCKER_PATH_HINTS
        )

        action = _SYSCALL_ACTION.get(syscall, "modify")
        category = "docker" if (is_docker_related and syscall in ("execve", "execveat")) else "file"
        if category == "docker":
            action = "docker_exec"
            argv = g.get("_argv")
            if argv:
                target = " ".join(argv)
            elif exe:
                target = exe

        detail = {
            "success": g.get("success"),
            "syscall": syscall,
            "exit": g.get("exit"),
            "tty": g.get("tty"),
            "comm": g.get("comm"),
            "raw_uid": g.get("uid"),
            "raw_auid": g.get("auid"),
        }
        return Event(
            ts=ts, source=self.name, category=category, action=action,
            actor=actor, uid=uid_val, target=target, host=self.host, detail=detail,
        )
