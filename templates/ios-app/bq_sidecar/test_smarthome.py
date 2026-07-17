#!/usr/bin/env python3
"""Unit tests for the Smart Home TWO-TIER filter (source × type), 24h counts, and the
per-event detail rows (no BigQuery/Drive).

Runnable anywhere: builds a temp 'Smart Home Events' folder with a summary file and a
jsonl of synthetic events, points SMARTHOME_DIR at it, and exercises get_smarthome().

    python3 bq_sidecar/test_smarthome.py
"""
import datetime
import json
import os
import tempfile

import app  # bq_sidecar/app.py (BigQuery imports are lazy, so this is import-safe)


def _iso(dt):
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _build_dir(tmp):
    now = datetime.datetime.now(datetime.timezone.utc)
    events = []

    def add_ha(entity, n, minutes_ago_start=5):
        for i in range(n):
            ts = now - datetime.timedelta(minutes=minutes_ago_start + i)
            events.append({"ts": _iso(ts), "source": "ha",
                           "new_state": {"entity_id": entity, "state": "on"},
                           "event_type": "state_changed",
                           "attributes": {"friendly_name": entity.split(".")[-1]}})

    # Tier-1 source "ha": a mix of types (light/lock/binary_sensor + noisy sensor).
    add_ha("light.kitchen", 4)
    add_ha("lock.front_door", 2)
    add_ha("binary_sensor.motion", 3)
    add_ha("sensor.temperature", 10)  # noisy numeric domain
    # Tier-1 source "eero": device_update events (no entity_id ⇒ type = event_type).
    for i in range(3):
        ts = now - datetime.timedelta(minutes=2 + i)
        events.append({"ts": _iso(ts), "source": "eero", "event_type": "device_update",
                       "device": "Living Room TV", "ip": f"192.168.1.{20 + i}"})
    # One stale event (>24h ago) that must be excluded from the 24h window.
    events.append({"ts": _iso(now - datetime.timedelta(hours=30)),
                   "source": "ha", "new_state": {"entity_id": "light.old", "state": "off"}})

    fname = os.path.join(tmp, f"events_{now:%Y%m%d}.jsonl")
    with open(fname, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    with open(os.path.join(tmp, "summary_latest.json"), "w") as f:
        json.dump({"generated_at": _iso(now), "counts": {"last_hour": 22, "last_24h": 22, "total": 22},
                   "by_source": {"ha": {"last_event_at": _iso(now), "total": 19, "last_hour": 19}}}, f)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        _build_dir(tmp)
        os.environ["SMARTHOME_DIR"] = tmp

        # Classification: entity domain wins, else event_type, else source.
        assert app._event_type({"new_state": {"entity_id": "light.kitchen"}}) == "light"
        assert app._event_type({"entity_id": "lock.x"}) == "lock"
        assert app._event_type({"event_type": "device_update", "source": "eero"}) == "device_update"

        # ---- Defaults: all sources on; useful types on (noisy 'sensor' off).
        out = app.get_smarthome(None, None)
        srcs = {s["key"]: s for s in out["sourceFilters"]}
        assert set(srcs) == {"ha", "eero"}, srcs.keys()
        assert srcs["ha"]["count"] == 19 and srcs["eero"]["count"] == 3, srcs
        assert srcs["ha"]["active"] and srcs["eero"]["active"], "all sources on by default"
        assert srcs["eero"]["label"] == "Eero", srcs["eero"]
        types = {t["key"]: t for t in out["types"]}
        assert set(types) == {"light", "lock", "binary_sensor", "sensor", "device_update"}, types.keys()
        assert types["device_update"]["count"] == 3 and types["device_update"]["active"]
        assert types["light"]["active"] and types["sensor"]["active"], "sensor now on by default (all sensor activities shown)"
        # Events = (all sources) ∩ (activity types): light4 + lock2 + binary3 + sensor10 + device_update3 = 22.
        assert len(out["events"]) == 22, len(out["events"])
        assert "2/2 sources" in out["filterSummaryFormatted"], out["filterSummaryFormatted"]

        # ---- Per-event detail rows for tap-to-expand.
        ev0 = out["events"][0]
        assert ev0["detailsLabel"] == "Details"
        keys = {r["keyFormatted"] for r in ev0["details"]}
        assert "Source" in keys and "Type" in keys, keys

        # ---- Source toggle preserves the (default) type selection and pins sources.
        eero_chip = srcs["eero"]
        assert eero_chip["navHref"] == "/screen/smarthome?sources=ha", eero_chip["navHref"]

        # ---- Tier-1 drill-down: filter to source 'ha' only → eero events excluded, and
        # the type list/counts are scoped to ha (no device_update).
        out_ha = app.get_smarthome(None, "ha")
        assert all(ev["source"] == "ha" for ev in out_ha["events"]), "only ha events"
        ha_types = {t["key"] for t in out_ha["types"]}
        assert "device_update" not in ha_types, "type list scoped to selected sources"
        assert len(out_ha["events"]) == 19, len(out_ha["events"])  # light4 + lock2 + binary3 + sensor10

        # ---- Tier-2 toggle preserves the source selection in the href.
        out_src = app.get_smarthome(None, "ha,eero")
        light = next(t for t in out_src["types"] if t["key"] == "light")
        assert "sources=ha,eero" in light["navHref"] and "types=" in light["navHref"], light["navHref"]

        # ---- Empty type selection → no events, clear empty state.
        out_none = app.get_smarthome("", None)
        assert out_none["events"] == [] and out_none["emptyFormatted"]

    print("✓ smarthome two-tier filter + detail tests passed")


if __name__ == "__main__":
    main()
