"""
store.py — SQLite-backed event store.

Schema
------
events: one row per device event, all sources.
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,       -- 'ha', 'hue', 'yolink'
    entity_id   TEXT,                   -- e.g. 'light.kitchen'
    event_type  TEXT,                   -- e.g. 'state_changed'
    old_state   TEXT,                   -- JSON blob (nullable)
    new_state   TEXT,                   -- JSON blob (nullable)
    context     TEXT,                   -- JSON blob (nullable)
    ts          REAL    NOT NULL,       -- Unix timestamp UTC
    created_at  TEXT    DEFAULT (datetime('now','utc'))
);

CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
"""


class EventStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.executescript(_DDL)
        self._con.commit()

    def insert(
        self,
        source: str,
        entity_id: str | None,
        event_type: str,
        old_state: Any = None,
        new_state: Any = None,
        context: Any = None,
        ts: float | None = None,
    ) -> int:
        if ts is None:
            ts = time.time()
        row = (
            source,
            entity_id,
            event_type,
            json.dumps(old_state) if old_state is not None else None,
            json.dumps(new_state) if new_state is not None else None,
            json.dumps(context) if context is not None else None,
            ts,
        )
        cur = self._con.execute(
            "INSERT INTO events (source,entity_id,event_type,old_state,new_state,context,ts)"
            " VALUES (?,?,?,?,?,?,?)",
            row,
        )
        self._con.commit()
        return cur.lastrowid

    def recent(self, limit: int = 100, source: str | None = None) -> list[dict]:
        if source:
            rows = self._con.execute(
                "SELECT * FROM events WHERE source=? ORDER BY ts DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [c[0] for c in self._con.execute("SELECT * FROM events LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]

    def count_since(self, since_ts: float, source: str | None = None) -> int:
        if source:
            return self._con.execute(
                "SELECT COUNT(*) FROM events WHERE ts>=? AND source=?",
                (since_ts, source),
            ).fetchone()[0]
        return self._con.execute(
            "SELECT COUNT(*) FROM events WHERE ts>=?", (since_ts,)
        ).fetchone()[0]

    def close(self):
        self._con.close()
