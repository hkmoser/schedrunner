"""
ha_collector.py — Home Assistant WebSocket event subscriber.

Connects to the HA WebSocket API, authenticates with a long-lived token,
and subscribes to the event types configured via config.HA_EVENT_TYPES
(defaults to every event type; set a list like "state_changed" to narrow it).
On reconnect it also fetches a full state snapshot so nothing is missed
during gaps.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import websockets

import config
from store import EventStore

log = logging.getLogger("ha_collector")

RECONNECT_DELAY = 10  # seconds between reconnect attempts


def _parse_ts(ts_str: str | None) -> float:
    """Parse HA's ISO-8601 time_fired into a Unix timestamp (fallback: now)."""
    if ts_str:
        try:
            return (
                datetime.fromisoformat(ts_str.rstrip("Z"))
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except Exception:
            pass
    return time.time()


async def _fetch_all_states(ws, msg_id: int) -> list[dict]:
    """Request a full state dump via get_states."""
    await ws.send(json.dumps({"id": msg_id, "type": "get_states"}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            return msg.get("result", [])


async def _subscribe(ws, msg_id: int, event_type: str | None, pending: list) -> None:
    """Send a subscribe_events request and wait for its result.

    When subscribing to more than one type, HA may start streaming events
    before we've read every subscription result, so any 'event' messages that
    arrive meanwhile are buffered into `pending` for the caller to process.
    """
    payload = {"id": msg_id, "type": "subscribe_events"}
    if event_type:
        payload["event_type"] = event_type
    await ws.send(json.dumps(payload))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == msg_id and msg.get("type") == "result":
            if not msg.get("success"):
                raise RuntimeError(f"Subscribe failed ({event_type or 'all'}): {msg}")
            return
        if msg.get("type") == "event":
            pending.append(msg)


def _store_event(store: EventStore, sub_ids: dict, msg: dict,
                 on_event: Callable | None) -> None:
    """Persist a single HA 'event' message, handling any event type."""
    evt = msg.get("event", {})
    # event_type is present on the event itself; fall back to the subscription.
    event_type = evt.get("event_type") or sub_ids.get(msg.get("id")) or "unknown"
    data = evt.get("data", {}) or {}
    ctx = evt.get("context")
    ts = _parse_ts(evt.get("time_fired"))
    entity_id = data.get("entity_id")

    if event_type == "state_changed":
        old = data.get("old_state")
        new = data.get("new_state")
        row_id = store.insert(
            source="ha",
            entity_id=entity_id,
            event_type=event_type,
            old_state=old,
            new_state=new,
            context=ctx,
            ts=ts,
        )
        log.debug("HA [%s] %s → %s (row %d)", entity_id,
                  old.get("state") if old else "?",
                  new.get("state") if new else "?", row_id)
        if on_event:
            on_event("ha", entity_id, new)
    else:
        # Generic event: store the full data payload in new_state.
        row_id = store.insert(
            source="ha",
            entity_id=entity_id,
            event_type=event_type,
            new_state=data,
            context=ctx,
            ts=ts,
        )
        log.debug("HA event %s (entity=%s, row %d)", event_type, entity_id, row_id)
        if on_event:
            on_event("ha", entity_id, data)


async def _run_once(store: EventStore, on_event: Callable | None = None):
    """Open one WebSocket session. Raises on disconnect."""
    url = config.HA_URL.replace("http://", "ws://").replace("https://", "wss://")
    url = url.rstrip("/") + "/api/websocket"

    async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
        # --- handshake ---
        hello = json.loads(await ws.recv())
        assert hello.get("type") == "auth_required", f"Unexpected: {hello}"

        await ws.send(json.dumps({"type": "auth", "access_token": config.HA_TOKEN}))
        auth_result = json.loads(await ws.recv())
        if auth_result.get("type") != "auth_ok":
            raise RuntimeError(f"HA auth failed: {auth_result}")
        log.info("HA authenticated (HA version %s)", auth_result.get("ha_version"))

        msg_id = 1

        # --- snapshot all current states ---
        try:
            states = await _fetch_all_states(ws, msg_id)
            msg_id += 1
            log.info("HA snapshot: %d entities", len(states))
            for s in states:
                store.insert(
                    source="ha",
                    entity_id=s.get("entity_id"),
                    event_type="snapshot",
                    new_state=s,
                    ts=time.time(),
                )
        except Exception as e:
            log.warning("Snapshot failed (non-fatal): %s", e)

        # --- subscribe to the configured event types ---
        event_types = config.HA_EVENT_TYPES
        subscribe_all = (not event_types) or any(
            t.lower() in ("*", "all") for t in event_types
        )

        # sub_id -> event_type (None == "all event types")
        sub_ids: dict[int, str | None] = {}
        pending: list[dict] = []  # events that arrive during subscription setup

        if subscribe_all:
            await _subscribe(ws, msg_id, None, pending)
            sub_ids[msg_id] = None
            log.info("HA subscribed to ALL event types (sub id=%d)", msg_id)
            msg_id += 1
        else:
            for et in event_types:
                await _subscribe(ws, msg_id, et, pending)
                sub_ids[msg_id] = et
                log.info("HA subscribed to %s (sub id=%d)", et, msg_id)
                msg_id += 1

        # Flush any events buffered while subscriptions were being set up.
        for msg in pending:
            if msg.get("id") in sub_ids:
                _store_event(store, sub_ids, msg, on_event)

        # --- event loop ---
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") != "event" or msg.get("id") not in sub_ids:
                continue
            _store_event(store, sub_ids, msg, on_event)


async def run(store: EventStore, on_event: Callable | None = None):
    """Reconnecting HA collector loop."""
    while True:
        try:
            await _run_once(store, on_event)
        except asyncio.CancelledError:
            log.info("HA collector cancelled")
            raise
        except Exception as e:
            log.warning("HA collector error (%s), retrying in %ds", e, RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)
