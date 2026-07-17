#!/usr/bin/env python3
"""Unit test for the Activity time-range window (no BigQuery).

    python3 bq_sidecar/test_afm.py
"""
import datetime

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


if __name__ == "__main__":
    test_afm_window()
    test_match_known()
    print("✓ afm window + known-loc tests passed")
