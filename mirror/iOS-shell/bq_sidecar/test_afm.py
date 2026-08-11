#!/usr/bin/env python3
"""Unit test for the Activity time-range window (no BigQuery).

    python3 bq_sidecar/test_afm.py
"""
import datetime
import os

import app  # bq_sidecar/app.py (BigQuery imports are lazy, so this is import-safe)


def test_afm_window():
    now = datetime.datetime(2026, 6, 20, 14, 30, 0)  # Sat 2:30pm

    # Default / "today" / unknown all resolve to the past 24h.
    for key in (None, "today", "bogus"):
        s, e, k, lbl = app._afm_window(key, 24, now)
        assert (k, lbl) == ("today", "Today"), (key, k, lbl)
        assert s == now - datetime.timedelta(hours=24), (key, s)
        assert e > now and e <= now + datetime.timedelta(seconds=2), (key, e)

    # Yesterday = the previous calendar day (local midnight boundaries).
    s, e, k, lbl = app._afm_window("yesterday", 24, now)
    assert (k, lbl) == ("yesterday", "Yesterday")
    assert s == datetime.datetime(2026, 6, 19, 0, 0, 0), s
    assert e == datetime.datetime(2026, 6, 20, 0, 0, 0), e

    # This week = the past 7 days.
    s, e, k, lbl = app._afm_window("week", 24, now)
    assert (k, lbl) == ("week", "This week")
    assert s == now - datetime.timedelta(days=7), s

    print("✓ _afm_window resolves today / yesterday / week")


def test_match_known():
    known = [
        {"name": "Home", "lat": 40.4862, "lon": -74.4518, "radius_m": 150.0},
        {"name": "Gym", "lat": 40.5000, "lon": -74.4700, "radius_m": 100.0},
    ]
    assert app._match_known(40.4862, -74.4518, known) == "Home", "exact centroid matches"
    assert app._match_known(40.4866, -74.4520, known) == "Home", "within radius matches"
    assert app._match_known(40.60, -74.60, known) is None, "far away matches nothing"
    assert app._match_known(40.4862, -74.4518, []) is None, "no known locations → None"
    # Nearest wins when two contain the point.
    overlap = [
        {"name": "Far", "lat": 40.5010, "lon": -74.4700, "radius_m": 5000.0},
        {"name": "Near", "lat": 40.5000, "lon": -74.4700, "radius_m": 5000.0},
    ]
    assert app._match_known(40.5000, -74.4700, overlap) == "Near", "nearest known location wins"
    print("✓ _match_known resolves stop → known location name")


class _FakeClient:
    """Counts queries so we can prove the device list isn't re-queried per request."""

    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def query(self, sql):
        self.calls += 1
        return self

    def result(self, timeout=None):
        return [self]

    def items(self):
        return self.rows.items()


def test_device_options_cache():
    os.environ.setdefault("BQ_AFM_HISTORY", "proj.ds.afm_latest_live")
    app._device_options_cache.clear()
    c = _FakeClient({"deviceName": "iPhone 15", "name": "Joe", "n": 7, "is12m": False})
    expected = [{"value": "iPhone 15", "label": "Joe (iPhone 15)", "n": 7, "is12m": False}]

    assert app._device_options(c, 24) == expected
    assert c.calls == 1, "first call queries BigQuery"

    # Repeated calls inside the TTL reuse the cache — this is the whole point: /afm is
    # hit every ~60s by the server's warmer and this query is a full-scan GROUP BY.
    for _ in range(5):
        assert app._device_options(c, 24) == expected
    assert c.calls == 1, f"cached calls must not re-query (got {c.calls})"

    # Each window caches separately, and an expired entry re-queries.
    assert app._device_options(c, 48) == expected
    assert c.calls == 2, "a different window is its own cache entry"
    fetched, ttl, opts = app._device_options_cache[24]
    app._device_options_cache[24] = (fetched - ttl - 1, ttl, opts)
    assert app._device_options(c, 24) == expected
    assert c.calls == 3, "an expired entry re-queries"

    # A failed refresh serves the last-good list rather than blanking the dropdown.
    class _Boom(_FakeClient):
        def query(self, sql):
            raise RuntimeError("bigquery down")

    fetched, ttl, opts = app._device_options_cache[24]
    app._device_options_cache[24] = (fetched - ttl - 1, ttl, opts)
    assert app._device_options(_Boom({}), 24) == expected, "stale-but-good beats empty"

    app._device_options_cache.clear()
    print("✓ _device_options caches the device list and survives a failed refresh")


if __name__ == "__main__":
    test_afm_window()
    test_match_known()
    test_device_options_cache()
    print("✓ afm window + known-loc + device-cache tests passed")
