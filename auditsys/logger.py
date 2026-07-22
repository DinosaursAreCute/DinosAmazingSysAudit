"""Operational logger for auditsys itself.

This mirrors the conventions of the project's existing `utils/sys_logger.sh`
(timestamped, leveled, colored console output + plain-text file mirror) so
the tool's own logging feels consistent with the rest of the codebase.

IMPORTANT: this logs the *tool's own operation* (debug/info/warn/error while
auditsys runs). It is NOT where collected audit events (who-did-what) live —
those go in the sqlite event store (see auditsys.store). Don't conflate the
two, mirroring the note in the architecture doc.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import threading

_LOCK = threading.Lock()

# ANSI color codes, matching sys_logger.sh's palette.
_NC = "\033[0m"
_COLORS = {
    "DEBUG": "\033[0;36m",  # cyan
    "INFO": "\033[0;32m",  # green
    "WARN": "\033[0;33m",  # yellow
    "ERROR": "\033[0;31m",  # red
}

_LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]


class Logger:
    def __init__(self, log_file: str | None = None, min_level: str = "INFO",
                 use_color: bool | None = None) -> None:
        self.log_file = log_file
        self.min_level = min_level if min_level in _LEVELS else "INFO"
        self.use_color = sys.stderr.isatty() if use_color is None else use_color
        if self.log_file:
            d = os.path.dirname(self.log_file)
            if d:
                os.makedirs(d, exist_ok=True)

    def _enabled(self, level: str) -> bool:
        return _LEVELS.index(level) >= _LEVELS.index(self.min_level)

    def _emit(self, level: str, message: str) -> None:
        if not self._enabled(level):
            return
        timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _LOCK:
            if self.use_color:
                color = _COLORS.get(level, "")
                sys.stderr.write(f"[{timestamp}] [{color}{level}{_NC}] {message}\n")
            else:
                sys.stderr.write(f"[{timestamp}] [{level}] {message}\n")
            sys.stderr.flush()
            if self.log_file:
                with open(self.log_file, "a", encoding="utf-8") as fh:
                    fh.write(f"[{timestamp}] [{level}] {message}\n")

    def debug(self, message: str) -> None:
        self._emit("DEBUG", message)

    def info(self, message: str) -> None:
        self._emit("INFO", message)

    def warn(self, message: str) -> None:
        self._emit("WARN", message)

    def error(self, message: str) -> None:
        self._emit("ERROR", message)


# Module-level default logger, configured lazily via configure().
_default: Logger | None = None


def configure(log_file: str | None = None, min_level: str = "INFO") -> Logger:
    global _default
    _default = Logger(log_file=log_file, min_level=min_level)
    return _default


def get() -> Logger:
    global _default
    if _default is None:
        _default = Logger()
    return _default


def debug(message: str) -> None:
    get().debug(message)


def info(message: str) -> None:
    get().info(message)


def warn(message: str) -> None:
    get().warn(message)


def error(message: str) -> None:
    get().error(message)
