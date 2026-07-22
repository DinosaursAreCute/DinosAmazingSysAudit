from __future__ import annotations

from textual.app import App

from .. import config as cfgmod
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
    #settings-form { height: 1fr; padding: 1 2; }
    #settings-form Label { margin-top: 1; color: $text-muted; }
    #save-status { padding: 1 2; color: $success; }
    """

    def __init__(self) -> None:
        super().__init__()
        # One shared, mutable config for the whole app session. Screens read
        # `self.app.config` (not their own `load_config()` snapshot) so a
        # save in SettingsScreen is visible to every other screen the next
        # time it's opened, without restarting the TUI.
        self.config: dict = cfgmod.load_config()

    def on_mount(self) -> None:
        self.push_screen(MainMenuScreen())


def run() -> None:
    AuditTUI().run()


if __name__ == "__main__":
    run()
