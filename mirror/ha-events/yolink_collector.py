"""
yolink_collector.py — YoLink cloud MQTT subscriber.

YoLink uses a two-step auth:
  1. POST to token URL to get an OAuth bearer token.
  2. Connect to MQTT (plain TCP, port 8003) using that token as the MQTT password.

Device events arrive on topic  yl-home/{home_id}/+/report
State updates arrive on topic  yl-home/{home_id}/+/event

We subscribe to  yl-home/{home_id}/#  to catch everything.
The home_id is fetched from the YoLink API after obtaining a token.
"""
import asyncio
import json
import logging
import time
import urllib.request
import urllib.parse
from typing import Callable

import paho.mqtt.client as mqtt

import config
from store import EventStore

log = logging.getLogger("yolink_collector")

RECONNECT_DELAY = 30


def _get_token() -> tuple[str, str]:
    """Returns (access_token, home_id)."""
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": config.YOLINK_USER_ACCESS_ID,
            "client_secret": config.YOLINK_SECRET_KEY,
        }
    ).encode()
    req = urllib.request.Request(config.YOLINK_TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"YoLink token error: {body}")

    # Fetch home info to get the home_id for topic construction
    api_req = urllib.request.Request(
        config.YOLINK_API_URL,
        data=json.dumps({"method": "Home.getGeneralInfo"}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(api_req, timeout=15) as resp:
        info = json.loads(resp.read())
    home_id = info.get("data", {}).get("id", "")
    if not home_id:
        raise RuntimeError(f"YoLink home_id not found: {info}")
    log.info("YoLink authenticated, home_id=%s", home_id)
    return token, home_id


def _build_client(
    store: EventStore,
    token: str,
    home_id: str,
    on_event: Callable | None,
    loop: asyncio.AbstractEventLoop,
) -> mqtt.Client:
    client = mqtt.Client(
        client_id=f"ha-events-{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )
    # YoLink MQTT auth: the access token is the MQTT *username* (password is
    # ignored). The UAID is only for the OAuth token request, not for MQTT —
    # passing it as the username gets the broker to refuse with CONNACK rc=5
    # ("not authorized").
    client.username_pw_set(username=token, password=None)
    # YoLink's broker on api.yosmart.com:8003 speaks plain MQTT, not MQTT-over-TLS.
    # Calling tls_set() here makes paho send a TLS ClientHello to a plaintext port,
    # which the broker drops mid-handshake → "SSL: UNEXPECTED_EOF_WHILE_READING".
    # So: no tls_set(). The bearer token (the username) is what authenticates us.

    def on_connect(c, userdata, flags, rc, properties=None):
        if rc == 0:
            topic = f"yl-home/{home_id}/#"
            c.subscribe(topic)
            log.info("YoLink MQTT connected, subscribed to %s", topic)
        else:
            log.error("YoLink MQTT connect failed: rc=%d", rc)

    def on_message(c, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError:
            payload = {"raw": msg.payload.decode("utf-8", errors="replace")}
        topic = msg.topic
        # topic format: yl-home/{home_id}/{device_id}/{type}
        parts = topic.split("/")
        device_id = parts[2] if len(parts) > 2 else "unknown"
        msg_type = parts[3] if len(parts) > 3 else "unknown"
        entity_id = f"yolink.{device_id}"
        ts_ms = payload.get("time", time.time() * 1000)
        ts = ts_ms / 1000.0 if ts_ms > 1e10 else float(ts_ms)
        loop.call_soon_threadsafe(
            store.insert,
            "yolink",
            entity_id,
            msg_type,
            None,
            payload,
            None,
            ts,
        )
        log.debug("YoLink %s %s state=%s", msg_type, device_id,
                  payload.get("data", {}).get("state", "?"))
        if on_event:
            loop.call_soon_threadsafe(on_event, "yolink", entity_id, payload)

    client.on_connect = on_connect
    client.on_message = on_message
    return client


async def run(store: EventStore, on_event: Callable | None = None):
    """Reconnecting YoLink MQTT collector loop."""
    if not config.YOLINK_USER_ACCESS_ID or not config.YOLINK_SECRET_KEY:
        log.warning("YoLink credentials not set — YoLink collector disabled.")
        return

    loop = asyncio.get_event_loop()

    while True:
        client = None
        try:
            token, home_id = await loop.run_in_executor(None, _get_token)
            client = _build_client(store, token, home_id, on_event, loop)
            await loop.run_in_executor(
                None,
                lambda: client.connect(
                    config.YOLINK_MQTT_HOST, config.YOLINK_MQTT_PORT, keepalive=60
                ),
            )
            client.loop_start()
            # Keep the coroutine alive until cancelled or error
            while client.is_connected() or not client._state:
                await asyncio.sleep(5)
            raise RuntimeError("YoLink MQTT disconnected")
        except asyncio.CancelledError:
            log.info("YoLink collector cancelled")
            if client:
                client.loop_stop()
                client.disconnect()
            raise
        except Exception as e:
            log.warning("YoLink collector error (%s), retrying in %ds", e, RECONNECT_DELAY)
            if client:
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(RECONNECT_DELAY)
