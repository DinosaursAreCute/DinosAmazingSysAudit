"""Screens for the auditsys TUI.

Every screen reads from the same Store the CLI uses, and from `self.app.config`
— the single shared config object living on the App instance — rather than
loading its own snapshot. That's what makes SettingsScreen edits apply
immediately: the next screen you open (or the next refresh on an existing
one) picks up whatever was just changed, without restarting the TUI.

The main menu is the single entry point users navigate from; every other
screen pushes onto the stack and pops back with Escape.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, Log, OptionList, Select, Static, Switch,
    TextArea, Tree,
)
from textual.widgets.option_list import Option

from .. import config as cfgmod
from ..cli import _correlate_docker
from ..collectors import ALL_COLLECTORS
from ..store import Store, local_hostname
from .common import fill_event_table, format_coverage, populate_path_tree, setup_event_table

ACTION_OPTIONS_FILE = [
    ("All actions", ""), ("create", "create"), ("modify", "modify"), ("delete", "delete"),
    ("rename", "rename"), ("chmod", "chmod"), ("chown", "chown"),
]
CATEGORY_OPTIONS = [
    ("All categories", ""), ("file", "file"), ("sudo", "sudo"), ("auth", "auth"), ("docker", "docker"),
]
VERBOSITY_OPTIONS = [("normal", "normal"), ("minimal", "minimal"), ("verbose", "verbose")]
COLLECTOR_NAMES = ("auditd", "journalctl", "docker", "stat")


class MainMenuScreen(Screen):
    """Landing screen: pick an operation."""

    BINDINGS = [("q", "app.quit", "Quit")]

    MENU_ITEMS = [
        ("files", "📁 Browse files / blame a path (supports recursive directory checks)"),
        ("logons", "🔑 Logons — who connected, when (SSH login/logout)"),
        ("sudo", "🛡  Sudo trail — who ran what as root, and failures"),
        ("docker", "🐳 Docker activity — CLI commands correlated with daemon events"),
        ("users", "👤 User lookup — pick a user, see everything they did"),
        ("coverage", "📊 Coverage report — what time ranges we actually have data for"),
        ("sync", "🔄 Sync now — pull fresh events from all enabled collectors"),
        ("settings", "⚙️  Settings — collectors, watched paths, retention, reports"),
        ("quit", "🚪 Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("  auditsys — pick an operation", id="menu-title")
        yield OptionList(*(Option(label, id=key) for key, label in self.MENU_ITEMS), id="menu")
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        key = event.option_id
        if key == "quit":
            self.app.exit()
        elif key == "files":
            self.app.push_screen(FilesScreen())
        elif key == "logons":
            self.app.push_screen(LogonsScreen())
        elif key == "sudo":
            self.app.push_screen(SudoScreen())
        elif key == "docker":
            self.app.push_screen(DockerScreen())
        elif key == "users":
            self.app.push_screen(UserListScreen())
        elif key == "coverage":
            self.app.push_screen(CoverageScreen())
        elif key == "sync":
            self.app.push_screen(SyncScreen())
        elif key == "settings":
            self.app.push_screen(SettingsScreen())


class BaseFilteredScreen(Screen):
    """Common scaffolding: coverage line + table + Escape-to-back.

    Config/store are wired up in on_mount (not __init__) so every screen
    reads `self.app.config` fresh each time it's opened — the live,
    possibly-just-edited-in-Settings config, not a snapshot from whenever
    the class happened to be instantiated.
    """

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.quit", "Quit")]
    coverage_sources: tuple[str, ...] = ()

    def compose_filters(self) -> ComposeResult:
        """Override in subclasses to yield filter widgets."""
        return []
        yield  # pragma: no cover - makes this a generator if unused

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="filters"):
            yield from self.compose_filters()
        yield Static("", id="coverage")
        yield DataTable(id="table")
        yield Footer()

    def _connect(self) -> None:
        self.config = self.app.config
        self.store = Store(cfgmod.get_store_path(self.config))

    def on_mount(self) -> None:
        self._connect()
        setup_event_table(self.query_one("#table", DataTable))
        self.refresh_data()

    def refresh_coverage(self) -> None:
        windows = [w for s in self.coverage_sources for w in self.store.get_coverage(s)]
        self.query_one("#coverage", Static).update(format_coverage(windows))

    def refresh_data(self) -> None:
        events = self.load_events()
        fill_event_table(self.query_one("#table", DataTable), events)
        self.refresh_coverage()

    def load_events(self) -> list:
        raise NotImplementedError

    def action_refresh(self) -> None:
        self.refresh_data()


class FilesScreen(BaseFilteredScreen):
    """Blame browser: a file tree (built from recorded events, not the live
    filesystem, so deleted paths still show up) alongside the usual
    path/recursive filters. Clicking a tree node fills in the path filter —
    a leaf file selects exact history, a directory branch turns recursive
    on automatically. Each tree label is annotated with its most recent
    actor/action so you get a "who did what" glance before clicking."""

    TITLE = "Files / Blame"
    coverage_sources = ("auditd", "stat")
    BINDINGS = BaseFilteredScreen.BINDINGS + [("r", "refresh", "Refresh")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="files-body"):
            yield Tree("/", id="path-tree")
            with Vertical(id="files-main"):
                with Horizontal(id="filters"):
                    yield from self.compose_filters()
                yield Static("", id="coverage")
                yield DataTable(id="table")
        yield Footer()

    def compose_filters(self) -> ComposeResult:
        yield Input(placeholder="/etc, a filename, or a directory to check recursively...", id="path")
        yield Label("recursive")
        yield Switch(value=False, id="recursive")
        yield Select(ACTION_OPTIONS_FILE, id="action", value="", allow_blank=False)

    def on_mount(self) -> None:
        self._connect()
        setup_event_table(self.query_one("#table", DataTable))
        self._populate_tree()
        self.refresh_data()

    def _populate_tree(self) -> None:
        tree = self.query_one("#path-tree", Tree)
        latest = self.store.latest_per_path(category="file")
        populate_path_tree(tree, latest)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data:
            return
        is_dir = bool(event.node.children)
        self.query_one("#path", Input).value = data
        self.query_one("#recursive", Switch).value = is_dir
        self.refresh_data()

    def load_events(self) -> list:
        path = self.query_one("#path", Input).value.strip()
        recursive = self.query_one("#recursive", Switch).value
        action = self.query_one("#action", Select).value or None
        if not path:
            return self.store.query(category="file", action=action, limit=1000)
        if recursive:
            return self.store.query(target_path=path, recursive=True, category="file",
                                     action=action, limit=2000)
        return self.store.query(target_like=path, category="file", action=action, limit=1000)

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "path":
            self.refresh_data()

    def on_switch_changed(self, message: Switch.Changed) -> None:
        if message.switch.id == "recursive":
            self.refresh_data()

    def on_select_changed(self, message: Select.Changed) -> None:
        if message.select.id == "action":
            self.refresh_data()

    def action_refresh(self) -> None:
        self._populate_tree()
        self.refresh_data()


class LogonsScreen(BaseFilteredScreen):
    """SSH login/logout/failed-login trail."""

    TITLE = "Logons"
    coverage_sources = ("journalctl",)

    ACTION_OPTIONS = [("All", ""), ("login", "login"), ("login_fail", "login_fail"), ("logout", "logout")]

    def compose_filters(self) -> ComposeResult:
        yield Input(placeholder="filter by user...", id="user")
        yield Select(self.ACTION_OPTIONS, id="action", value="", allow_blank=False)

    def load_events(self) -> list:
        user = self.query_one("#user", Input).value.strip() or None
        action = self.query_one("#action", Select).value or None
        return self.store.query(category="auth", actor=user, action=action, limit=1000)

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "user":
            self.refresh_data()

    def on_select_changed(self, message: Select.Changed) -> None:
        if message.select.id == "action":
            self.refresh_data()


class SudoScreen(BaseFilteredScreen):
    """Sudo audit trail."""

    TITLE = "Sudo Trail"
    coverage_sources = ("journalctl",)

    def compose_filters(self) -> ComposeResult:
        yield Input(placeholder="filter by user...", id="user")
        yield Label("failed only")
        yield Switch(value=False, id="failed_only")

    def load_events(self) -> list:
        user = self.query_one("#user", Input).value.strip() or None
        failed_only = self.query_one("#failed_only", Switch).value
        action = "sudo_fail" if failed_only else None
        return self.store.query(category="sudo", actor=user, action=action, limit=1000)

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "user":
            self.refresh_data()

    def on_switch_changed(self, message: Switch.Changed) -> None:
        if message.switch.id == "failed_only":
            self.refresh_data()


class DockerScreen(BaseFilteredScreen):
    """Docker activity: CLI-invocation events correlated with daemon events."""

    TITLE = "Docker Activity"
    coverage_sources = ("auditd", "docker")

    def compose_filters(self) -> ComposeResult:
        yield Input(placeholder="search actor / target...", id="search")
        yield Select(VERBOSITY_OPTIONS, id="verbosity", value="normal", allow_blank=False)

    def on_mount(self) -> None:
        self._connect()
        # honor whatever verbosity Settings currently has as the default
        default_verbosity = self.config.get("collectors", {}).get("docker", {}).get("verbosity", "normal")
        self.query_one("#verbosity", Select).value = default_verbosity
        setup_event_table(self.query_one("#table", DataTable))
        self.refresh_data()

    def load_events(self) -> list:
        verbosity = self.query_one("#verbosity", Select).value
        search = self.query_one("#search", Input).value.strip().lower()
        events = self.store.query(category="docker", limit=2000)
        correlated = _correlate_docker(events, verbosity)
        if search:
            correlated = [
                e for e in correlated
                if search in (e.actor or "").lower() or search in (e.target or "").lower()
            ]
        return correlated

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "search":
            self.refresh_data()

    def on_select_changed(self, message: Select.Changed) -> None:
        if message.select.id == "verbosity":
            self.refresh_data()


class UserListScreen(Screen):
    """Pick a user, see everything they did across every category."""

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        store = Store(cfgmod.get_store_path(self.app.config))
        try:
            actors = store.list_actors()
        finally:
            store.close()
        yield Header(show_clock=True)
        yield Static("  Select a user to see their full activity")
        options = [Option(actor, id=actor) for actor in actors]
        yield OptionList(*options, id="actor-list") if options else Static(
            "  No actors recorded yet — run a sync first."
        )
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.app.push_screen(UserActivityScreen(actor=event.option_id))


class UserActivityScreen(BaseFilteredScreen):
    """Full cross-category timeline for one user."""

    coverage_sources = ("auditd", "journalctl", "docker", "stat")

    def __init__(self, actor: str) -> None:
        self.actor = actor
        super().__init__()
        self.title = f"Activity: {actor}"

    def compose_filters(self) -> ComposeResult:
        yield Static(f"  Everything recorded for [b]{self.actor}[/]", id="user-label")
        yield Select(CATEGORY_OPTIONS, id="category", value="", allow_blank=False)

    def load_events(self) -> list:
        category = self.query_one("#category", Select).value or None
        return self.store.query(actor=self.actor, category=category, limit=2000)

    def on_select_changed(self, message: Select.Changed) -> None:
        if message.select.id == "category":
            self.refresh_data()


class CoverageScreen(Screen):
    """What time ranges each source actually has data for, per host."""

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("r", "refresh", "Refresh"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="cov-table")
        yield Footer()

    def on_mount(self) -> None:
        self.store = Store(cfgmod.get_store_path(self.app.config))
        table = self.query_one("#cov-table", DataTable)
        table.add_columns("Source", "Host", "Start", "End", "Note")
        table.cursor_type = "row"
        self.refresh_data()

    def refresh_data(self) -> None:
        table = self.query_one("#cov-table", DataTable)
        table.clear()
        for w in self.store.get_coverage():
            table.add_row(w.source, w.host, w.start_ts or "-", w.end_ts or "-", w.note)

    def action_refresh(self) -> None:
        self.refresh_data()


class SyncScreen(Screen):
    """Runs all enabled collectors in a background thread, streaming progress."""

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("  Syncing... (press Escape to go back once finished)")
        yield Log(id="sync-log")
        yield Footer()

    def on_mount(self) -> None:
        self.run_sync()

    @work(thread=True)
    def run_sync(self) -> None:
        config = self.app.config
        log = self.query_one("#sync-log", Log)
        host = config.get("host_label") or local_hostname()
        store = Store(cfgmod.get_store_path(config))
        total = 0
        for name, collector_cls in ALL_COLLECTORS.items():
            collector_cfg = config.get("collectors", {}).get(name, {})
            if not collector_cfg.get("enabled", True):
                self.app.call_from_thread(log.write_line, f"[{name}] disabled, skipping")
                continue
            collector = collector_cls(config, host)
            ok, reason = collector.available()
            if not ok:
                self.app.call_from_thread(log.write_line, f"[{name}] unavailable: {reason}")
                continue
            since = store.get_cursor(name, host)
            self.app.call_from_thread(log.write_line, f"[{name}] syncing since {since or 'beginning'}")
            events = list(collector.sync(since))
            n = store.insert_events(events)
            total += n
            if events:
                store.set_cursor(name, host, max(e.ts for e in events))
            store.set_coverage(collector.coverage())
            self.app.call_from_thread(log.write_line, f"[{name}] ingested {n} events")
        store.close()
        self.app.call_from_thread(log.write_line, f"\nDone — {total} new events total.")


class SettingsScreen(Screen):
    """Edit collectors/paths/retention/report settings.

    "Save" does two things at once: (1) mutates `self.app.config` in place
    so every other screen sees the change on its very next open/refresh —
    no restart needed — and (2) writes the same config to disk (the file it
    was originally loaded from, or the standard default location if there
    wasn't one yet) so it survives the next `audit-tui`/`audit-cli` run too.
    """

    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="settings-form"):
            yield Label("Collectors enabled")
            for name in COLLECTOR_NAMES:
                with Horizontal():
                    yield Label(name, classes="settings-label")
                    yield Switch(value=True, id=f"enabled-{name}")

            yield Label("Docker verbosity (minimal / normal / verbose)")
            yield Select(VERBOSITY_OPTIONS, id="docker-verbosity", value="normal", allow_blank=False)

            with Horizontal():
                yield Label("Correlate docker exec <-> daemon events", classes="settings-label")
                yield Switch(value=True, id="docker-correlate-logs")

            yield Label("Store retention (days)")
            yield Input(id="retention-days", type="integer")

            yield Label("Watched paths (one per line — used by install-audit-rules.sh)")
            yield TextArea(id="watched-paths")

            yield Label("Report output directory (blank = auto)")
            yield Input(id="report-output-dir")

            yield Label("Host label override (blank = system hostname)")
            yield Input(id="host-label")

            with Horizontal():
                yield Button("Save", id="save-btn", variant="success")
                yield Button("Reset", id="reset-btn")
            yield Static("", id="save-status")
        yield Footer()

    def on_mount(self) -> None:
        self._load_from_config(self.app.config)

    def _load_from_config(self, config: dict) -> None:
        collectors = config.get("collectors", {})
        for name in COLLECTOR_NAMES:
            self.query_one(f"#enabled-{name}", Switch).value = collectors.get(name, {}).get("enabled", True)
        docker_cfg = collectors.get("docker", {})
        self.query_one("#docker-verbosity", Select).value = docker_cfg.get("verbosity", "normal")
        self.query_one("#docker-correlate-logs", Switch).value = docker_cfg.get("correlate_logs", True)
        self.query_one("#retention-days", Input).value = str(config.get("store", {}).get("retention_days", 90))
        self.query_one("#watched-paths", TextArea).text = "\n".join(config.get("watched_paths", []))
        self.query_one("#report-output-dir", Input).value = config.get("report", {}).get("output_dir") or ""
        self.query_one("#host-label", Input).value = config.get("host_label") or ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset-btn":
            self._load_from_config(self.app.config)
            self.query_one("#save-status", Static).update("Reverted unsaved edits.")
            return
        if event.button.id != "save-btn":
            return
        self._save()

    def _save(self) -> None:
        updated = copy.deepcopy(self.app.config)
        collectors = updated.setdefault("collectors", {})
        for name in COLLECTOR_NAMES:
            collectors.setdefault(name, {})["enabled"] = self.query_one(f"#enabled-{name}", Switch).value
        docker_cfg = collectors.setdefault("docker", {})
        docker_cfg["verbosity"] = self.query_one("#docker-verbosity", Select).value
        docker_cfg["correlate_logs"] = self.query_one("#docker-correlate-logs", Switch).value

        retention_raw = self.query_one("#retention-days", Input).value.strip()
        try:
            retention_days = int(retention_raw) if retention_raw else 90
        except ValueError:
            self.query_one("#save-status", Static).update(
                f"[red]'{retention_raw}' isn't a whole number of days — not saved.[/]"
            )
            return
        updated.setdefault("store", {})["retention_days"] = retention_days

        watched_text = self.query_one("#watched-paths", TextArea).text
        updated["watched_paths"] = [line.strip() for line in watched_text.splitlines() if line.strip()]

        output_dir = self.query_one("#report-output-dir", Input).value.strip()
        updated.setdefault("report", {})["output_dir"] = output_dir or None

        host_label = self.query_one("#host-label", Input).value.strip()
        updated["host_label"] = host_label or None

        existing_path = updated.get("_config_file")
        target_path = existing_path if existing_path else str(cfgmod.default_config_path())
        written = cfgmod.save_config(updated, Path(target_path))
        updated["_config_file"] = str(written)

        # Apply immediately — every screen reads self.app.config live.
        self.app.config = updated

        self.query_one("#save-status", Static).update(f"[green]Saved and applied — {written}[/]")
