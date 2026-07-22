"""Unified event schema shared by every collector.

Every collector, regardless of source (auditd/journalctl/docker/stat),
normalizes its raw data into `Event` instances. Nothing downstream (store,
CLI, TUI, reports) needs to know where an event came from beyond the
`source` field.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

# Known categories / actions. Collectors should stick to these where possible
# so filtering ("--action modify") is consistent across sources; `detail`
# carries anything source-specific that doesn't fit the common shape.
CATEGORIES = ("file", "sudo", "auth", "docker")
ACTIONS = (
    "create", "modify", "delete", "rename", "chmod", "chown",
    "sudo_exec", "sudo_fail",
    "login", "logout", "login_fail",
    "docker_exec", "docker_api",
)


@dataclass
class Event:
    ts: str  # ISO8601 UTC, e.g. 2026-07-22T10:15:00+00:00
    source: str  # 'auditd' | 'journalctl' | 'docker' | 'stat'
    category: str  # see CATEGORIES
    action: str  # see ACTIONS
    actor: Optional[str] = None  # resolved username, if known
    uid: Optional[int] = None
    target: Optional[str] = None  # file path / command / container name
    host: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def to_row(self) -> tuple:
        return (
            self.ts, self.source, self.category, self.action,
            self.actor, self.uid, self.target, self.host,
            json.dumps(self.detail, default=str),
        )

    @classmethod
    def from_row(cls, row: tuple) -> "Event":
        # row layout must match store.EVENT_COLUMNS (minus id/coverage_id)
        (_id, ts, source, category, action, actor, uid, target, host,
         detail_json, _coverage_id) = row
        detail = json.loads(detail_json) if detail_json else {}
        return cls(ts=ts, source=source, category=category, action=action,
                   actor=actor, uid=uid, target=target, host=host, detail=detail)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CoverageWindow:
    source: str
    host: str
    start_ts: Optional[str]
    end_ts: Optional[str]
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)
