import asyncio
import tempfile
from pathlib import Path

import yaml

from auditsys import config as cfgmod
from auditsys.tui.app import AuditTUI
from auditsys.tui.screens import DockerScreen, MainMenuScreen, SettingsScreen


def test_settings_apply_immediately_and_persist_to_disk(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        config_write_path = Path(d) / "config.yaml"

        fixed_config = {
            "store": {"path": str(db_path)},
            "collectors": {
                "auditd": {"enabled": True},
                "journalctl": {"enabled": True},
                "docker": {"enabled": True, "verbosity": "normal", "correlate_logs": True},
                "stat": {"enabled": True},
            },
            "watched_paths": ["/etc"],
            "report": {"output_dir": None},
            "host_label": None,
        }
        monkeypatch.setattr(cfgmod, "load_config", lambda: dict(fixed_config))
        monkeypatch.setattr(cfgmod, "default_config_path", lambda: config_write_path)

        async def body():
            app = AuditTUI()
            async with app.run_test() as pilot:
                menu = app.screen.query_one("#menu")
                menu.highlighted = 7  # "settings"
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, SettingsScreen)

                # prefilled from current config
                assert app.screen.query_one("#docker-verbosity").value == "normal"
                assert app.screen.query_one("#watched-paths").text.strip() == "/etc"

                # edit: turn off the stat collector, bump verbosity to verbose,
                # widen watched paths, set a retention value
                app.screen.query_one("#enabled-stat").value = False
                app.screen.query_one("#docker-verbosity").value = "verbose"
                app.screen.query_one("#watched-paths").text = "/etc\n/var/lib/docker"
                app.screen.query_one("#retention-days").value = "30"
                await pilot.pause()

                app.screen.query_one("#save-btn").press()
                await pilot.pause()

                status = app.screen.query_one("#save-status")
                assert "Saved and applied" in status.content

                # (1) applied immediately to the live shared config
                assert app.config["collectors"]["stat"]["enabled"] is False
                assert app.config["collectors"]["docker"]["verbosity"] == "verbose"
                assert app.config["watched_paths"] == ["/etc", "/var/lib/docker"]
                assert app.config["store"]["retention_days"] == 30

                # next screen opened reflects it without any restart
                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(app.screen, MainMenuScreen)
                menu = app.screen.query_one("#menu")
                menu.highlighted = 3  # "docker"
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, DockerScreen)
                assert app.screen.query_one("#verbosity").value == "verbose"

        asyncio.run(body())

        # (2) persisted to disk
        assert config_write_path.exists()
        on_disk = yaml.safe_load(config_write_path.read_text())
        assert on_disk["collectors"]["stat"]["enabled"] is False
        assert on_disk["collectors"]["docker"]["verbosity"] == "verbose"
        assert on_disk["watched_paths"] == ["/etc", "/var/lib/docker"]
        assert on_disk["store"]["retention_days"] == 30
        assert "_config_file" not in on_disk  # internal bookkeeping key stripped
