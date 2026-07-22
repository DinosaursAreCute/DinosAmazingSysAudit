from __future__ import annotations

import json
from pathlib import Path

from ..models import CoverageWindow, Event


def render(events: list[Event], coverage: list[CoverageWindow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "coverage": [c.as_dict() for c in coverage],
        "event_count": len(events),
        "events": [e.as_dict() for e in events],
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
