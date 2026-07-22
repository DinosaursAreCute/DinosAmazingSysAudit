"""Fallback collector: current filesystem metadata for configured paths.

Used when a path has no auditd watch coverage. This can only ever report
"here's who currently owns it and when it last changed" — never real
history — so every event from this collector is tagged
`detail.confidence = "low"` and the CLI/reports must never present it as
equivalent to an auditd-sourced event.
"""
from __future__ import annotations

import os
import pwd
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..models import Event, CoverageWindow
from .. import logger as log
from .base import Collector


def _uid_to_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid:{uid}"


class StatCollector(Collector):
    name = "stat"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def coverage(self) -> CoverageWindow:
        note = (
            "Current filesystem metadata only (owner + mtime/ctime) — not a "
            "history. Low-confidence fallback for paths without auditd coverage."
        )
        return CoverageWindow(source=self.name, host=self.host, start_ts=None,
                              end_ts=datetime.now(timezone.utc).isoformat(), note=note)

    def sync(self, since_ts: Optional[str]) -> Iterable[Event]:
        watched = self.config.get("watched_paths", [])
        since_dt = datetime.fromisoformat(since_ts) if since_ts else None
        for raw_path in watched:
            path = Path(raw_path)
            if not path.exists():
                continue
            try:
                st = path.stat()
            except OSError as exc:
                log.warn(f"stat collector: cannot stat {path}: {exc}")
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if since_dt and mtime <= since_dt:
                continue
            yield Event(
                ts=mtime.isoformat(), source=self.name, category="file",
                action="modify", actor=_uid_to_name(st.st_uid), uid=st.st_uid,
                target=str(path), host=self.host,
                detail={
                    "confidence": "low",
                    "mode": oct(st.st_mode),
                    "ctime": datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat(),
                    "note": "current owner/mtime only, not verified history",
                },
            )
