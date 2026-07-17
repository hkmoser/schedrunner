"""
eero_collector.py — eero (Amazon) cloud API poller.

eero exposes no public push/event stream, so this collector polls the
unofficial eero cloud API (the same endpoints the eero mobile app uses)
on a fixed interval and records:

  * Network detail — overall network state, gateway, timezone, etc.
                                          (event_type "network_update")
  * Access-point data  — each eero node's model, status, connected-client
    count, IP, mesh quality, etc.        (event_type "ap_update")
  * Connected client devices — which AP they're on, online/offline, signal
                                          (event_type "device_update")
  * Network activity series — data-usage throughput and (with eero Secure)
    blocked/inspected/adblock insights   (event_type "activity")

Activity endpoints are GET requests that carry a JSON body with the time
window ({"start", "end", "cadence", "timezone"}); a bodyless GET 404s.

To avoid storing identical state on every poll, each entity's payload is
hashed and only re-inserted when it changes.

Authentication
--------------
eero authenticates with a session token obtained via a one-time SMS/email
verification flow. Run `python setup_eero.py` once to log in; it writes
EERO_SESSION_TOKEN to your .env. The token is sent as the `s=<token>`
cookie on every request.
"""
import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

try:  # stdlib on 3.9+, used to honor the network's own timezone
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import aiohttp

import config
from store import EventStore

log = logging.getLogger("eero_collector")

RECONNECT_DELAY = 60  # seconds to wait after a transient error


def _hash(obj) -> str:
    """Stable hash of a JSON-serializable payload, for change detection."""
    return hashlib.sha1(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


async def _get(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json()


def _insert_if_changed(
    store: EventStore,
    seen: dict,
    on_event: Callable | None,
    entity_id: str,
    event_type: str,
    payload: dict,
) -> bool:
    """Insert an event only when the payload differs from the last seen one."""
    h = _hash(payload)
    if seen.get(entity_id) == h:
        return False
    seen[entity_id] = h
    store.insert(
        source="eero",
        entity_id=entity_id,
        event_type=event_type,
        new_state=payload,
        ts=time.time(),
    )
    if on_event:
        on_event("eero", entity_id, payload)
    return True


def _ap_ident(ap: dict) -> str:
    return (
        ap.get("serial")
        or ap.get("url", "").rstrip("/").rsplit("/", 1)[-1]
        or "unknown"
    )


def _device_ident(dev: dict) -> str:
    return (
        dev.get("mac")
        or dev.get("url", "").rstrip("/").rsplit("/", 1)[-1]
        or "unknown"
    )


# Activity series name -> (network-relative path, insight_type or None).
# data_usage is the raw throughput series; the insights variants require
# an eero Secure subscription.
_ACTIVITY_SPECS: dict[str, tuple[str, str | None]] = {
    "data_usage": ("/data_usage", None),
    "blocked": ("/insights", "blocked"),
    "inspected": ("/insights", "inspected"),
    "adblock": ("/insights", "adblock"),
}


def _activity_window(period: str, tz_name: str) -> tuple[str, str, str]:
    """Return (start, end, cadence) for an activity request.

    eero wants the window as UTC ISO-8601 with a trailing 'Z' and a cadence
    of 'hourly' (day view) or 'daily' (week/month). Times are anchored in the
    network's own timezone so the buckets line up with the eero app.
    """
    tz = ZoneInfo(tz_name) if (ZoneInfo and tz_name) else timezone.utc
    try:
        now = datetime.now(tz)
    except Exception:  # bad/unknown tz name -> fall back to UTC
        now = datetime.now(timezone.utc)

    if period == "month":
        start, cadence = now - timedelta(days=30), "daily"
    elif period == "week":
        start, cadence = now - timedelta(days=7), "daily"
    else:  # "day"
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cadence = "hourly"

    def _fmt(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    return _fmt(start), _fmt(now), cadence


async def _poll_activity(
    session: aiohttp.ClientSession,
    store: EventStore,
    on_event: Callable | None,
    seen: dict,
    base: str,
    net_id: str,
    tz_name: str,
):
    """Fetch each configured activity series for one network.

    These eero endpoints are GET requests that carry a JSON body describing
    the time window — a bodyless GET returns 404.
    """
    start, end, cadence = _activity_window(config.EERO_ACTIVITY_PERIOD, tz_name)
    for name in config.EERO_ACTIVITY_TYPES:
        spec = _ACTIVITY_SPECS.get(name)
        if not spec:
            log.warning("eero: unknown activity type %r, skipping", name)
            continue
        path, insight_type = spec
        body = {"start": start, "end": end, "cadence": cadence, "timezone": tz_name}
        if insight_type:
            body["insight_type"] = insight_type
        try:
            async with session.get(base + path, json=body) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as e:
            log.warning(
                "eero: activity %s unavailable for net %s: %s", name, net_id, e
            )
            continue
        entity_id = f"eero.activity.{net_id}.{name}.{config.EERO_ACTIVITY_PERIOD}"
        if _insert_if_changed(store, seen, on_event, entity_id, "activity", data):
            log.debug("eero activity %s (%s) recorded for net %s",
                      name, config.EERO_ACTIVITY_PERIOD, net_id)


async def _poll_network(
    session: aiohttp.ClientSession,
    store: EventStore,
    on_event: Callable | None,
    seen: dict,
    net: dict,
):
    """Pull AP data, client devices, and activity history for one network."""
    net_url = net.get("url")  # e.g. "/2.2/networks/1234567"
    if not net_url:
        return
    net_id = net_url.rstrip("/").rsplit("/", 1)[-1]
    base = config.EERO_API_URL + net_url

    # --- network detail (records overall state + supplies the timezone the
    #     activity endpoints need) ---
    tz_name = "UTC"
    try:
        detail = await _get(session, base)
        ndata = detail.get("data", detail) if isinstance(detail, dict) else {}
        tz_name = (ndata.get("timezone") or {}).get("value") or "UTC"
        _insert_if_changed(
            store, seen, on_event, f"eero.network.{net_id}", "network_update", ndata
        )
    except Exception as e:
        log.warning("eero: network detail fetch failed for net %s: %s", net_id, e)

    # --- access points (eero nodes) ---
    try:
        eeros = await _get(session, base + "/eeros")
        aps = eeros.get("data", eeros) if isinstance(eeros, dict) else eeros
        for ap in aps or []:
            entity_id = f"eero.ap.{_ap_ident(ap)}"
            if _insert_if_changed(store, seen, on_event, entity_id, "ap_update", ap):
                log.debug("eero AP %s (%s) status=%s",
                          ap.get("location"), _ap_ident(ap), ap.get("status"))
    except Exception as e:
        log.warning("eero: eeros fetch failed for net %s: %s", net_id, e)

    # --- connected client devices ---
    try:
        devices = await _get(session, base + "/devices")
        devs = devices.get("data", devices) if isinstance(devices, dict) else devices
        for dev in devs or []:
            entity_id = f"eero.device.{_device_ident(dev)}"
            if _insert_if_changed(store, seen, on_event, entity_id, "device_update", dev):
                log.debug("eero device %s connected=%s",
                          dev.get("nickname") or dev.get("hostname"),
                          dev.get("connected"))
    except Exception as e:
        log.warning("eero: devices fetch failed for net %s: %s", net_id, e)

    # --- activity / data-usage history ---
    await _poll_activity(session, store, on_event, seen, base, net_id, tz_name)


def _extract_networks(account: dict) -> list[dict]:
    """Pull the network list out of a /2.2/account response.

    eero wraps most responses in {"meta": ..., "data": ...}, but be tolerant
    of the wrapper being absent and of `networks` being either a list or a
    {"count": N, "data": [...]} object.
    """
    data = account.get("data", account) if isinstance(account, dict) else {}
    if not isinstance(data, dict):
        return []
    nets = data.get("networks", [])
    if isinstance(nets, dict):
        nets = nets.get("data", [])
    return nets if isinstance(nets, list) else []


async def _run_once(store: EventStore, on_event: Callable | None, seen: dict):
    """One full poll across all networks on the account."""
    headers = {
        "User-Agent": "ha-events",
        "Content-Type": "application/json",
        "Cookie": f"s={config.EERO_SESSION_TOKEN}",
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        account = await _get(session, config.EERO_API_URL + "/2.2/account")
        networks = _extract_networks(account)
        if not networks:
            data = account.get("data", account) if isinstance(account, dict) else {}
            log.warning(
                "eero: no networks found on account. account keys=%s, "
                "data keys=%s, networks=%r",
                list(account.keys()) if isinstance(account, dict) else type(account),
                list(data.keys()) if isinstance(data, dict) else type(data),
                data.get("networks") if isinstance(data, dict) else None,
            )
            return
        for net in networks:
            await _poll_network(session, store, on_event, seen, net)


async def run(store: EventStore, on_event: Callable | None = None):
    """Polling eero collector loop."""
    if not config.EERO_SESSION_TOKEN:
        log.warning(
            "EERO_SESSION_TOKEN not set — eero collector disabled. "
            "Run setup_eero.py to log in."
        )
        return

    seen: dict[str, str] = {}  # entity_id -> last payload hash

    while True:
        try:
            await _run_once(store, on_event, seen)
        except asyncio.CancelledError:
            log.info("eero collector cancelled")
            raise
        except Exception as e:
            status = getattr(e, "status", None)
            if status in (401, 403):
                log.error(
                    "eero auth failed (HTTP %s) — session token expired. "
                    "Re-run setup_eero.py. Disabling eero collector.", status
                )
                return
            log.warning("eero collector error (%s), retrying in %ds", e, RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)
            continue
        await asyncio.sleep(config.EERO_POLL_INTERVAL)
