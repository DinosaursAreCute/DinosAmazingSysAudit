import asyncio
import tempfile
from pathlib import Path

from auditsys import config as cfgmod
from auditsys.models import Event
from auditsys.store import Store
from auditsys.tui.app import AuditTUI
from auditsys.tui.screens import FilesScreen, MainMenuScreen, UserActivityScreen, UserListScreen


def _seed(db_path: Path) -> None:
    store = Store(db_path)
    store.insert_events([
        Event(ts="2026-07-22T10:00:05+00:00", source="auditd", category="file",
              action="delete", actor="alice", target="/etc/important.conf", host="srv2"),
        Event(ts="2026-07-22T10:00:06+00:00", source="auditd", category="file",
              action="modify", actor="bob", target="/etc/nginx/nginx.conf", host="srv2"),
        Event(ts="2026-07-22T10:00:07+00:00", source="auditd", category="file",
              action="modify", actor="carol", target="/etc2/unrelated.conf", host="srv2"),
    ])
    store.close()


def test_tui_menu_navigation_and_recursive_path_filter(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        _seed(db_path)
        fixed_config = {
            "store": {"path": str(db_path)},
            "collectors": {k: {"enabled": False} for k in ("auditd", "journalctl", "docker", "stat")},
            "watched_paths": [],
        }
        monkeypatch.setattr(cfgmod, "load_config", lambda: fixed_config)

        async def body():
            app = AuditTUI()
            async with app.run_test() as pilot:
                assert isinstance(app.screen, MainMenuScreen)

                menu = app.screen.query_one("#menu")
                menu.highlighted = 0  # "files"
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, FilesScreen)

                app.screen.query_one("#path").value = "/etc"
                await pilot.pause()
                app.screen.query_one("#recursive").value = True
                await pilot.pause()
                table = app.screen.query_one("#table")
                # must match /etc/important.conf + /etc/nginx/nginx.conf, NOT /etc2/unrelated.conf
                assert table.row_count == 2

                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(app.screen, MainMenuScreen)

                menu = app.screen.query_one("#menu")
                menu.highlighted = 4  # "users"
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, UserListScreen)
                actor_list = app.screen.query_one("#actor-list")
                actor_list.highlighted = 0
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, UserActivityScreen)
                assert app.screen.query_one("#table").row_count >= 1

        asyncio.run(body())


def test_files_tree_click_selects_leaf_and_directory(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        _seed(db_path)
        fixed_config = {
            "store": {"path": str(db_path)},
            "collectors": {k: {"enabled": False} for k in ("auditd", "journalctl", "docker", "stat")},
            "watched_paths": [],
        }
        monkeypatch.setattr(cfgmod, "load_config", lambda: fixed_config)

        async def body():
            app = AuditTUI()
            async with app.run_test() as pilot:
                menu = app.screen.query_one("#menu")
                menu.highlighted = 0  # "files"
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, FilesScreen)

                tree = app.screen.query_one("#path-tree")

                def find(node, target_path):
                    if node.data == target_path:
                        return node
                    for child in node.children:
                        found = find(child, target_path)
                        if found:
                            return found
                    return None

                # clicking the exact leaf file -> exact match, recursive off
                leaf = find(tree.root, "/etc/important.conf")
                assert leaf is not None
                tree.select_node(leaf)
                await pilot.pause()
                assert app.screen.query_one("#path").value == "/etc/important.conf"
                assert app.screen.query_one("#recursive").value is False
                assert app.screen.query_one("#table").row_count == 1

                # clicking the /etc directory branch -> recursive on, both files show, not /etc2
                branch = find(tree.root, "/etc")
                assert branch is not None
                tree.select_node(branch)
                await pilot.pause()
                assert app.screen.query_one("#path").value == "/etc"
                assert app.screen.query_one("#recursive").value is True
                assert app.screen.query_one("#table").row_count == 2

        asyncio.run(body())
