"""Ingests the docker daemon's own event log (`docker events`).

This is a *second, independent* signal from `AuditdCollector`'s
`docker_exec` events (which come from watching the `docker` CLI binary's
execve calls). `docker events` sees daemon-level lifecycle (container
create/start/die/destroy, image pull/rm, volume/network changes) even for
API calls that didn't go through the local CLI (remote clients, compose,
CI runners) — but it generally does NOT carry an OS username, only
whatever the docker daemon knows (container/image ids, sometimes a
`com.docker.compose.*` label).

Attribution ("who") for docker actions comes from correlating this with
auditd's `docker_exec` events by timestamp — that join happens at query
time (see cli.py `docker` command), not at ingestion, so verbosity/log
correlation stays a presentation concern instead of bloating the store.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from ..models import Event, CoverageWindow
from .. import logger as log
from .base import Collector


class DockerCollector(Collector):
    name = "docker"

    def available(self) -> tuple[bool, str]:
        if shutil.which("docker") is None:
            return False, "docker binary not found"
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return False, f"docker daemon not reachable: {proc.stderr.strip()[:200]}"
        return True, ""

    def coverage(self) -> CoverageWindow:
        # docker's internal event buffer is bounded and daemon-config
        # dependent; we don't get a clean "earliest" query, so report the
        # requested lookback window as an honest approximation.
        note = (
            "docker events only covers what the running daemon still buffers; "
            "container-removal also destroys that container's own `docker logs` "
            "unless a persistent log driver is configured."
        )
        return CoverageWindow(source=self.name, host=self.host, start_ts=None,
                              end_ts=datetime.now(timezone.utc).isoformat(), note=note)

    def sync(self, since_ts: Optional[str]) -> Iterable[Event]:
        since = since_ts or (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        until = datetime.now(timezone.utc).isoformat()
        cmd = [
            "docker", "events",
            "--since", since, "--until", until,
            "--format", "{{json .}}",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception as exc:  # noqa: BLE001
            log.warn(f"docker events invocation failed: {exc}")
            return
        if proc.returncode != 0:
            log.warn(f"docker events returned {proc.returncode}: {proc.stderr.strip()[:200]}")
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield self._to_event(rec)

    def _to_event(self, rec: dict) -> Event:
        ts_ns = rec.get("timeNano")
        if ts_ns:
            ts = datetime.fromtimestamp(int(ts_ns) / 1_000_000_000, tz=timezone.utc).isoformat()
        else:
            ts = datetime.now(timezone.utc).isoformat()
        actor_attrs = rec.get("Actor", {}).get("Attributes", {})
        target = (
            actor_attrs.get("name")
            or rec.get("Actor", {}).get("ID", "")[:12]
            or rec.get("id", "")[:12]
        )
        detail = {
            "type": rec.get("Type"),
            "action": rec.get("Action"),
            "attributes": actor_attrs,
        }
        return Event(
            ts=ts, source=self.name, category="docker", action="docker_api",
            actor=None, target=f"{rec.get('Type')}:{target} {rec.get('Action')}",
            host=self.host, detail=detail,
        )
