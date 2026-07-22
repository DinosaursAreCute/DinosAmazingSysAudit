from __future__ import annotations

import json as _json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from . import config as cfgmod
from . import logger as log
from .collectors import ALL_COLLECTORS
from .models import Event
from .store import Store, local_hostname
from .reports import html_report, csv_report, json_report

console = Console()


def _get_store(ctx: click.Context) -> Store:
    return Store(cfgmod.get_store_path(ctx.obj["config"]))


@click.group()
@click.option("--log-level", default="INFO", type=click.Choice(["DEBUG", "INFO", "WARN", "ERROR"]))
@click.pass_context
def main(ctx: click.Context, log_level: str) -> None:
    """audit-cli — who did what, on this host, and since when we actually know."""
    ctx.ensure_object(dict)
    config = cfgmod.load_config()
    log.configure(min_level=log_level)
    ctx.obj["config"] = config


@main.command()
@click.option("--source", default="all", help="Collector to run (default: all enabled)")
@click.pass_context
def sync(ctx: click.Context, source: str) -> None:
    """Run collectors, ingesting new events into the local store."""
    config = ctx.obj["config"]
    host = config.get("host_label") or local_hostname()
    store = _get_store(ctx)
    names = [source] if source != "all" else list(ALL_COLLECTORS.keys())
    total = 0
    for name in names:
        collector_cfg = config.get("collectors", {}).get(name, {})
        if not collector_cfg.get("enabled", True):
            log.info(f"[{name}] disabled in config, skipping")
            continue
        collector_cls = ALL_COLLECTORS.get(name)
        if collector_cls is None:
            log.warn(f"unknown collector: {name}")
            continue
        collector = collector_cls(config, host)
        ok, reason = collector.available()
        if not ok:
            log.warn(f"[{name}] unavailable: {reason}")
            continue
        since = store.get_cursor(name, host)
        log.info(f"[{name}] syncing since {since or 'beginning'}")
        events = list(collector.sync(since))
        n = store.insert_events(events)
        total += n
        if events:
            latest = max(e.ts for e in events)
            store.set_cursor(name, host, latest)
        cw = collector.coverage()
        store.set_coverage(cw)
        log.info(f"[{name}] ingested {n} events")
    console.print(f"[green]sync complete[/] — {total} new events")


def _format_events(events: list[Event], fmt: str) -> None:
    if fmt == "json":
        console.print_json(_json.dumps([e.as_dict() for e in events]))
        return
    if fmt == "plain":
        for e in events:
            console.print(f"{e.ts}\t{e.source}\t{e.category}\t{e.action}\t{e.actor}\t{e.target}")
        return
    table = Table(show_lines=False)
    for col in ("ts", "source", "action", "actor", "target"):
        table.add_column(col)
    for e in events:
        table.add_row(e.ts, e.source, e.action, e.actor or "-", (e.target or "-")[:80])
    console.print(table)
    if not events:
        console.print("[yellow]no events matched[/]")


@main.command()
@click.argument("path")
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--action", default=None)
@click.option("--recursive", is_flag=True, default=False,
              help="Match this path and everything under it (directory-tree blame), "
                   "instead of the default fuzzy substring match on a single path.")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "plain"]))
@click.pass_context
def blame(ctx: click.Context, path: str, since: Optional[str], until: Optional[str],
          action: Optional[str], recursive: bool, fmt: str) -> None:
    """Who created/modified/deleted/renamed/chmod'd/chown'd a path, and when.

    Without --recursive: fuzzy substring match (handy for partial names).
    With --recursive: exact path-tree match — /etc also matches
    /etc/nginx/nginx.conf, but not /etc2/unrelated."""
    store = _get_store(ctx)
    if recursive:
        events = store.query(target_path=path, recursive=True, category="file",
                              action=action, since=since, until=until)
    else:
        events = store.query(target_like=path, category="file", action=action,
                              since=since, until=until)
    _format_events(events, fmt)
    _print_coverage_note(store, ["auditd", "stat"])


@main.command()
@click.option("--user", default=None)
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--failed-only", is_flag=True, default=False)
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "plain"]))
@click.pass_context
def sudo(ctx: click.Context, user: Optional[str], since: Optional[str], until: Optional[str],
         failed_only: bool, fmt: str) -> None:
    """Sudo audit trail: who ran what as who, and failed attempts."""
    store = _get_store(ctx)
    action = "sudo_fail" if failed_only else None
    events = store.query(category="sudo", actor=user, action=action, since=since, until=until)
    _format_events(events, fmt)
    _print_coverage_note(store, ["journalctl"])


@main.command(name="docker")
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--verbosity", default=None, type=click.Choice(["minimal", "normal", "verbose"]))
@click.option("--with-logs", is_flag=True, default=False,
              help="Best-effort fetch `docker logs` for containers touched in range")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "plain"]))
@click.pass_context
def docker_cmd(ctx: click.Context, since: Optional[str], until: Optional[str],
               verbosity: Optional[str], with_logs: bool, fmt: str) -> None:
    """Docker attribution: CLI-invocation events (auditd) correlated with
    the daemon's own lifecycle events (docker events), so a `docker rm`
    typed by a person is tied to the container it acted on."""
    config = ctx.obj["config"]
    verbosity = verbosity or config.get("collectors", {}).get("docker", {}).get("verbosity", "normal")
    store = _get_store(ctx)
    events = store.query(category="docker", since=since, until=until, limit=2000)
    correlated = _correlate_docker(events, verbosity)
    if with_logs:
        _attach_logs(correlated)
    _format_events(correlated, fmt)
    _print_coverage_note(store, ["auditd", "docker"])


def _correlate_docker(events: list[Event], verbosity: str, window_seconds: int = 5) -> list[Event]:
    """Join auditd `docker_exec` (has actor, no container id) with docker
    daemon `docker_api` events (has container/image id, no actor) by time
    proximity, so results carry both "who typed the command" and "what it
    actually did to the daemon."""
    exec_events = [e for e in events if e.source == "auditd"]
    api_events = [e for e in events if e.source == "docker"]
    if verbosity == "minimal":
        return sorted(exec_events + api_events, key=lambda e: e.ts, reverse=True)

    def parse_ts(e: Event) -> datetime:
        return datetime.fromisoformat(e.ts)

    for api_event in api_events:
        api_ts = parse_ts(api_event)
        best, best_delta = None, None
        for exec_event in exec_events:
            delta = abs((parse_ts(exec_event) - api_ts).total_seconds())
            if delta <= window_seconds and (best_delta is None or delta < best_delta):
                best, best_delta = exec_event, delta
        if best:
            api_event.actor = api_event.actor or best.actor
            if verbosity == "verbose":
                api_event.detail["correlated_command"] = best.target
                api_event.detail["correlated_delta_seconds"] = best_delta
    return sorted(exec_events + api_events, key=lambda e: e.ts, reverse=True)


def _attach_logs(events: list[Event]) -> None:
    for e in events:
        if e.source != "docker":
            continue
        attrs = e.detail.get("attributes", {})
        cid = attrs.get("name") or attrs.get("id")
        if not cid:
            continue
        proc = subprocess.run(
            ["docker", "logs", "--tail", "50", cid],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            e.detail["logs_tail"] = proc.stdout[-4000:]
        else:
            e.detail["logs_tail_error"] = (
                "logs unavailable (container likely removed, or no persistent "
                f"log driver configured): {proc.stderr.strip()[:200]}"
            )


@main.command()
@click.argument("path")
@click.option("--recursive", is_flag=True, default=False,
              help="Consider the whole subtree under path, not just an exact match.")
@click.pass_context
def who(ctx: click.Context, path: str, recursive: bool) -> None:
    """Shorthand: last known actor for a path, plus the coverage caveat."""
    store = _get_store(ctx)
    if recursive:
        events = store.query(target_path=path, recursive=True, category="file", limit=1)
    else:
        events = store.query(target_like=path, category="file", limit=1)
    if not events:
        console.print(f"[yellow]no recorded events for[/] {path}")
    else:
        e = events[0]
        console.print(f"[bold]{e.target}[/]: last touched by [cyan]{e.actor}[/] "
                      f"({e.action}) at {e.ts} — source: {e.source}")
    _print_coverage_note(store, ["auditd", "stat"])


@main.command()
@click.option("--source", default=None)
@click.pass_context
def coverage(ctx: click.Context, source: Optional[str]) -> None:
    """Show what time ranges each source actually has data for."""
    store = _get_store(ctx)
    windows = store.get_coverage(source)
    table = Table()
    for col in ("source", "host", "start_ts", "end_ts", "note"):
        table.add_column(col)
    for w in windows:
        table.add_row(w.source, w.host, w.start_ts or "-", w.end_ts or "-", w.note)
    console.print(table)


def _print_coverage_note(store: Store, sources: list[str]) -> None:
    windows = [w for s in sources for w in store.get_coverage(s)]
    if not windows:
        console.print("[dim]No coverage recorded yet — run `audit-cli sync` first.[/]")
        return
    for w in windows:
        console.print(f"[dim]coverage ({w.source}): {w.start_ts or '?'} → {w.end_ts or '?'} — {w.note}[/]")


@main.group()
def config() -> None:
    """View/validate config."""


@config.command(name="show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    console.print_json(_json.dumps(ctx.obj["config"]))


@config.command(name="init")
@click.option("--path", default=None, help="Where to write the default config")
def config_init(path: Optional[str]) -> None:
    target = Path(path) if path else Path.home() / ".config" / "auditsys" / "config.yaml"
    written = cfgmod.write_default_config(target)
    console.print(f"[green]wrote default config to[/] {written}")


@main.command()
@click.option("--format", "fmt", default="html", type=click.Choice(["html", "csv", "json"]))
@click.option("--category", default=None, help="file|sudo|auth|docker")
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--out", "out_path", default=None, help="Output file path (default: auto in report dir)")
@click.pass_context
def report(ctx: click.Context, fmt: str, category: Optional[str], since: Optional[str],
           until: Optional[str], out_path: Optional[str]) -> None:
    """Generate a report file (interactive HTML, or CSV/JSON for pipelines)."""
    config = ctx.obj["config"]
    store = _get_store(ctx)
    events = store.query(category=category, since=since, until=until, limit=100000)
    coverage_windows = store.get_coverage()
    report_dir = cfgmod.get_report_dir(config)
    report_dir.mkdir(parents=True, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_name = f"audit-report-{ts_tag}.{fmt if fmt != 'html' else 'html'}"
    out = Path(out_path) if out_path else report_dir / default_name

    if fmt == "html":
        html_report.render(events, coverage_windows, out, title="Audit Report")
    elif fmt == "csv":
        csv_report.render(events, out)
    else:
        json_report.render(events, coverage_windows, out)
    console.print(f"[green]report written:[/] {out}")


if __name__ == "__main__":
    main()
