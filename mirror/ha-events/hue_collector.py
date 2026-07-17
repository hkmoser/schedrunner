"""
hue_collector.py — Philips Hue v2 CLIP API SSE subscriber.

Connects to the Hue bridge's Server-Sent Events stream and stores
every light/sensor/button change. SSL verification is skipped because
Hue bridges use self-signed certificates.

Prerequisites
-------------
Run setup_hue.py once to create a Hue application key, then add
HUE_KEY=<key> to your .env file.
"""
import asyncio
import json
import logging
import ssl
import time
from typing import Callable

import aiohttp

import config
from store import EventStore

log = logging.getLogger("hue_collector")

RECONNECT_DELAY = 15

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _parse_sse_data(raw: str) -> list[dict]:
    """Parse one SSE message block (may contain multiple JSON payloads)."""
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            try:
                items = json.loads(payload)
                if isinstance(items, list):
                    events.extend(items)
            except json.JSONDecodeError:
                pass
    return events


async def _run_once(store: EventStore, on_event: Callable | None = None):
    url = f"https://{config.HUE_IP}/eventstream/clip/v2"
    headers = {
        "hue-application-key": config.HUE_KEY,
        "Accept": "text/event-stream",
    }

    connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
    timeout = aiohttp.ClientTimeout(total=None, sock_read=60)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            log.info("Hue SSE connected (bridge %s)", config.HUE_IP)
            buf = ""
            async for chunk in resp.content.iter_any():
                buf += chunk.decode("utf-8", errors="replace")
                # SSE messages end with a blank line
                while "\n\n" in buf:
                    msg, buf = buf.split("\n\n", 1)
                    events = _parse_sse_data(msg)
                    for evt in events:
                        etype = evt.get("type", "update")
                        for resource in evt.get("data", []):
                            rtype = resource.get("type", "unknown")
                            rid = resource.get("id", "")
                            entity_id = f"{rtype}.{rid}"
                            row_id = store.insert(
                                source="hue",
                                entity_id=entity_id,
                                event_type=etype,
                                new_state=resource,
                                ts=time.time(),
                            )
                            log.debug("Hue %s %s (row %d)", etype, entity_id, row_id)
                            if on_event:
                                on_event("hue", entity_id, resource)


async def run(store: EventStore, on_event: Callable | None = None):
    """Reconnecting Hue SSE collector loop."""
    if not config.HUE_KEY:
        log.warning("HUE_KEY not set — Hue collector disabled. Run setup_hue.py.")
        return

    while True:
        try:
            await _run_once(store, on_event)
        except asyncio.CancelledError:
            log.info("Hue collector cancelled")
            raise
        except Exception as e:
            log.warning("Hue collector error (%s), retrying in %ds", e, RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)
