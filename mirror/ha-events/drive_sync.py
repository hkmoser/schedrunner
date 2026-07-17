"""
drive_sync.py — Write event summaries to Google Drive.

Google Drive is mounted locally at the path in config.GDRIVE_PATH
(~/CloudStorage/GoogleDrive-joe@joemoser.com/My Drive/Smart Home Events).
We write plain JSON/CSV files there at regular intervals so the data
is accessible from any device via Drive.

Files written
-------------
  events_YYYY-MM-DD.jsonl   — newline-delimited JSON, one event per line
  summary_latest.json       — rolling stats (counts, last-seen per source)
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from store import EventStore

log = logging.getLogger("drive_sync")


def _ensure_dir(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_daily_log(store: EventStore, gdrive: Path):
    """Append today's new events to the daily JSONL file."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    out_path = gdrive / f"events_{today}.jsonl"

    # Determine last written timestamp
    marker_path = gdrive / ".last_sync_ts"
    since = 0.0
    if marker_path.exists():
        try:
            since = float(marker_path.read_text().strip())
        except Exception:
            pass

    # Fetch rows since last sync
    rows = store._con.execute(
        "SELECT id,source,entity_id,event_type,old_state,new_state,context,ts,created_at "
        "FROM events WHERE ts > ? AND event_type != 'snapshot' ORDER BY ts ASC",
        (since,),
    ).fetchall()

    if not rows:
        return

    written = 0
    max_ts = since
    with out_path.open("a", encoding="utf-8") as f:
        for row in rows:
            (rid, source, entity_id, event_type, old_s, new_s, ctx, ts, created_at) = row
            record = {
                "id": rid,
                "source": source,
                "entity_id": entity_id,
                "event_type": event_type,
                "old_state": json.loads(old_s) if old_s else None,
                "new_state": json.loads(new_s) if new_s else None,
                "context": json.loads(ctx) if ctx else None,
                "ts": ts,
                "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            }
            f.write(json.dumps(record) + "\n")
            written += 1
            if ts > max_ts:
                max_ts = ts

    marker_path.write_text(str(max_ts))
    log.info("Drive sync: wrote %d events to %s", written, out_path.name)


def _write_summary(store: EventStore, gdrive: Path):
    """Write a rolling summary JSON."""
    hour_ago = time.time() - 3600
    day_ago = time.time() - 86400

    summary = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "counts": {
            "last_hour": store.count_since(hour_ago),
            "last_24h": store.count_since(day_ago),
            "total": store._con.execute("SELECT COUNT(*) FROM events WHERE event_type != 'snapshot'").fetchone()[0],
        },
        "by_source": {},
    }

    for source in ("ha", "hue", "yolink", "eero"):
        row = store._con.execute(
            "SELECT COUNT(*), MAX(ts) FROM events WHERE source=? AND event_type != 'snapshot'",
            (source,),
        ).fetchone()
        count, last_ts = row
        summary["by_source"][source] = {
            "total": count,
            "last_event_at": (
                datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
                if last_ts
                else None
            ),
            "last_hour": store.count_since(hour_ago, source),
        }

    out_path = gdrive / "summary_latest.json"
    out_path.write_text(json.dumps(summary, indent=2))
    log.debug("Drive summary updated: %s", summary["counts"])


async def run(store: EventStore):
    """Periodic Drive sync loop."""
    gdrive = _ensure_dir(config.GDRIVE_PATH)
    log.info("Drive sync target: %s", gdrive)

    while True:
        await asyncio.sleep(config.DRIVE_SYNC_INTERVAL)
        try:
            _write_daily_log(store, gdrive)
            _write_summary(store, gdrive)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Drive sync error (non-fatal): %s", e)
