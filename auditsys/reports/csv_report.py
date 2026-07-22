from __future__ import annotations

import csv
from pathlib import Path

from ..models import Event

FIELDS = ["ts", "source", "category", "action", "actor", "uid", "target", "host", "detail"]


def render(events: list[Event], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for e in events:
            row = e.as_dict()
            row["detail"] = str(row.get("detail", {}))
            writer.writerow(row)
