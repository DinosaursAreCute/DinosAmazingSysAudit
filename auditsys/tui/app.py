"""Interactive TUI for browsing audit events — menu-driven.

Deliberately reads the same Store the CLI does — the TUI is a viewer on top
of exactly what `audit-cli sync` already collected, not a separate data path.
See `screens.py` for the individual operations (files/blame, logons, sudo,
docker, user lookup, coverage, sync).
"""
from __future__ import annotations

from textual.app import App

from .screens import MainMenuScreen


class AuditTUI(App):
    TITLE = "auditsys"
    CSS = """
    #filters { height: 3; padding: 0 1; }
    #coverage { height: auto; padding: 0 1; color: $warning; }
    #menu-title { padding: 1 2; text-style: bold; }
    #menu { height: 1fr; }
    DataTable { height: 1fr; }
    Log { height: 1fr; }
    #files-body { height: 1fr; }
    #path-tree { width: 38; border-right: solid $panel-lighten-2; }
    #files-main { width: 1fr; }
    """

    def on_mount(self) -> None:
        self.push_screen(MainMenuScreen())


def run() -> None:
    AuditTUI().run()


if __name__ == "__main__":
    run()
