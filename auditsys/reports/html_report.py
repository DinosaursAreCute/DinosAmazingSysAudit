from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..models import CoverageWindow, Event

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render(events: list[Event], coverage: list[CoverageWindow], out_path: Path,
           title: str = "Audit Report") -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(
        title=title,
        generated_at=datetime.now(timezone.utc).isoformat(),
        event_count=len(events),
        events_json=json.dumps([e.as_dict() for e in events], default=str),
        coverage_json=json.dumps([c.as_dict() for c in coverage], default=str),
    )
    out_path.write_text(html, encoding="utf-8")
