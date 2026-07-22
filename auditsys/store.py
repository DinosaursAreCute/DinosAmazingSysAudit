"""SQLite-backed event store.

One file, zero server, trivially inspectable with the `sqlite3` CLI if
needed. Ingestion is incremental via per-(source,host) cursors so re-running
`sync` never duplicates rows.
"""
from __future__ import annotations

import socket
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import Event, CoverageWindow

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    source      TEXT NOT NULL,
    category    TEXT NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT,
    uid         INTEGER,
    target      TEXT,
    host        TEXT,
    detail      TEXT,
    coverage_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_target ON events(target);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);

CREATE TABLE IF NOT EXISTS coverage_windows (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source   TEXT NOT NULL,
    host     TEXT NOT NULL,
    start_ts TEXT,
    end_ts   TEXT,
    note     TEXT,
    UNIQUE(source, host)
);

CREATE TABLE IF NOT EXISTS cursors (
    source  TEXT NOT NULL,
    host    TEXT NOT NULL,
    last_ts TEXT,
    PRIMARY KEY (source, host)
);
"""

EVENT_COLUMNS = (
    "id", "ts", "source", "category", "action", "actor", "uid", "target",
    "host", "detail", "coverage_id",
)


def local_hostname() -> str:
    return socket.gethostname()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- ingestion -----------------------------------------------------
    def insert_events(self, events: Iterable[Event]) -> int:
        rows = [e.to_row() for e in events]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO events (ts, source, category, action, actor, uid, "
            "target, host, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_cursor(self, source: str, host: str) -> Optional[str]:
        cur = self.conn.execute(
            "SELECT last_ts FROM cursors WHERE source=? AND host=?", (source, host)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def set_cursor(self, source: str, host: str, last_ts: str) -> None:
        self.conn.execute(
            "INSERT INTO cursors (source, host, last_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(source, host) DO UPDATE SET last_ts=excluded.last_ts",
            (source, host, last_ts),
        )
        self.conn.commit()

    def set_coverage(self, cw: CoverageWindow) -> None:
        self.conn.execute(
            "INSERT INTO coverage_windows (source, host, start_ts, end_ts, note) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(source, host) DO UPDATE SET "
            "start_ts=COALESCE(coverage_windows.start_ts, excluded.start_ts), "
            "end_ts=excluded.end_ts, note=excluded.note",
            (cw.source, cw.host, cw.start_ts, cw.end_ts, cw.note),
        )
        self.conn.commit()

    def get_coverage(self, source: Optional[str] = None) -> list[CoverageWindow]:
        q = "SELECT source, host, start_ts, end_ts, note FROM coverage_windows"
        params: tuple = ()
        if source:
            q += " WHERE source=?"
            params = (source,)
        return [CoverageWindow(*row) for row in self.conn.execute(q, params)]

    # -- querying --------------------------------------------------------
    def query(
        self,
        *,
        target_like: Optional[str] = None,
        target_path: Optional[str] = None,
        recursive: bool = False,
        category: Optional[str] = None,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        host: Optional[str] = None,
        limit: int = 500,
        order: str = "DESC",
    ) -> list[Event]:
        """`target_path` does real path-tree matching (exact, or exact +
        every descendant when `recursive=True`) — distinct from
        `target_like`, which is a free-text substring search used for
        fuzzy/actor/command lookups and can match unrelated paths that
        merely share characters."""
        clauses: list[str] = []
        params: list[Any] = []
        if target_path:
            norm = target_path.rstrip("/") or "/"
            if recursive:
                clauses.append("(target = ? OR target LIKE ?)")
                params.append(norm)
                params.append(f"{norm}/%" if norm != "/" else "/%")
            else:
                clauses.append("target = ?")
                params.append(norm)
        elif target_like:
            clauses.append("target LIKE ?")
            params.append(f"%{target_like}%")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        if host:
            clauses.append("host = ?")
            params.append(host)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order_sql = "DESC" if order.upper() != "ASC" else "ASC"
        sql = (
            f"SELECT {', '.join(EVENT_COLUMNS)} FROM events {where} "
            f"ORDER BY ts {order_sql} LIMIT ?"
        )
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [Event.from_row(row) for row in rows]

    def latest_per_path(self, category: str = "file", limit: int = 20000) -> dict[str, Event]:
        """Most recent event per distinct target path — used to label the
        blame file-tree without a query per node."""
        events = self.query(category=category, limit=limit)  # default order=DESC
        result: dict[str, Event] = {}
        for e in events:
            if e.target and e.target not in result:
                result[e.target] = e
        return result

    def list_actors(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT actor, COUNT(*) as n FROM events WHERE actor IS NOT NULL "
            "GROUP BY actor ORDER BY n DESC"
        ).fetchall()
        return [row[0] for row in rows]

    def list_hosts(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT host FROM events WHERE host IS NOT NULL ORDER BY host"
        ).fetchall()
        return [row[0] for row in rows]

    def purge_older_than(self, cutoff_ts: str) -> int:
        cur = self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff_ts,))
        self.conn.commit()
        return cur.rowcount
