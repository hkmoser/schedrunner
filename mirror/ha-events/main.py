"""
main.py — ha-events orchestrator.

Runs all collectors concurrently via asyncio and syncs results to Drive.

Usage:
    python main.py [--debug]
"""
import asyncio
import logging
import signal
import sys
import time

import config
import ha_collector
import hue_collector
import yolink_collector
import eero_collector
import drive_sync
from store import EventStore


def _setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _on_event(source: str, entity_id: str, state):
    """Optional hook — called for every incoming event (runs in event loop)."""
    # Could trigger real-time notifications here (e.g., HA automations, alerts)
    pass


async def _main():
    debug = "--debug" in sys.argv
    _setup_logging(debug)
    log = logging.getLogger("main")

    log.info("ha-events STARTING")
    log.info("  HA:     %s", config.HA_URL)
    log.info("  Hue:    %s (key %s)", config.HUE_IP,
             "set" if config.HUE_KEY else "NOT SET")
    log.info("  YoLink: %s (creds %s)", config.YOLINK_MQTT_HOST,
             "set" if config.YOLINK_USER_ACCESS_ID else "NOT SET")
    log.info("  eero:   %s (token %s)", config.EERO_API_URL,
             "set" if config.EERO_SESSION_TOKEN else "NOT SET")
    log.info("  DB:     %s", config.DB_PATH)
    log.info("  Drive:  %s", config.GDRIVE_PATH)

    store = EventStore(config.DB_PATH)
    log.info("SQLite store opened")

    if not config.HA_TOKEN:
        log.error(
            "HA_TOKEN is not set. Add it to .env and restart. "
            "(Profile → Long-lived access tokens → Create token 'ha-events')"
        )

    tasks = [
        asyncio.create_task(ha_collector.run(store, _on_event), name="ha"),
        asyncio.create_task(hue_collector.run(store, _on_event), name="hue"),
        asyncio.create_task(yolink_collector.run(store, _on_event), name="yolink"),
        asyncio.create_task(eero_collector.run(store, _on_event), name="eero"),
        asyncio.create_task(drive_sync.run(store), name="drive_sync"),
    ]

    # Graceful shutdown on SIGTERM / SIGINT
    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    def _handle_signal():
        log.info("Shutdown signal received")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    log.info("ha-events RUNNING — %d collectors active: %s",
             len(tasks), ", ".join(t.get_name() for t in tasks))

    try:
        await stop.wait()
    finally:
        log.info("ha-events STOPPING — cancelling collectors…")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Final Drive sync before exit
        try:
            import drive_sync as ds
            from pathlib import Path
            gdrive = ds._ensure_dir(config.GDRIVE_PATH)
            ds._write_daily_log(store, gdrive)
            ds._write_summary(store, gdrive)
            log.info("Final Drive sync complete")
        except Exception as e:
            log.warning("Final Drive sync failed: %s", e)

        store.close()
        log.info("ha-events STOPPED — clean shutdown")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception:
        # Anything that escapes _main is a startup/runtime failure, not a clean
        # shutdown. Log it as FAILED with a traceback and exit non-zero so the
        # launcher's liveness check and schedrunner both see the failure.
        logging.getLogger("main").exception("ha-events FAILED — unhandled exception")
        sys.exit(1)
