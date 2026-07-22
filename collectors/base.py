"""Collector contract.

New sources (git, samba, a future SIEM export, etc.) just implement this
class and register in `auditsys.collectors.ALL_COLLECTORS` — no changes
needed anywhere else (store/CLI/TUI/reports all work off `Event` objects).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Optional

from ..models import Event, CoverageWindow


class Collector(ABC):
    name: str = "base"

    def __init__(self, config: dict[str, Any], host: str):
        self.config = config
        self.host = host

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """Return (True, '') if this collector's backend is usable on this
        host, else (False, human-readable reason)."""

    @abstractmethod
    def coverage(self) -> CoverageWindow:
        """Best-known (start_ts, end_ts) this source can actually answer
        for right now (e.g. journald retention, auditd rule install time)."""

    @abstractmethod
    def sync(self, since_ts: Optional[str]) -> Iterable[Event]:
        """Yield normalized Events with ts > since_ts (None = from the
        earliest available data)."""
