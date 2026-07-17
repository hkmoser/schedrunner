#!/usr/bin/env python3
"""Unit tests for the Balances net-worth-over-time card (_networth_cards), with a fake
BigQuery client so no real BigQuery is needed.

    python3 bq_sidecar/test_networth.py
"""
import datetime
import os

os.environ.setdefault("BQ_DATASET", "test-proj.home_afm")  # _ynab_history_table needs a project

import app  # bq_sidecar/app.py  # noqa: E402


class _Row:
    def __init__(self, d):
        self._d = d

    def items(self):
        return self._d.items()


class _Result(list):
    pass


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def result(self, timeout=None):
        return _Result(self._rows)


class _FakeClient:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc

    def query(self, sql):
        if self._raise:
            raise self._raise
        return _FakeQuery(self._rows)


def _rows(start, nets, backfill_days):
    out = []
    for i, nw in enumerate(nets):
        d = start + datetime.timedelta(days=i)
        out.append(_Row({"snapshot_date": d, "net_worth": float(nw),
                         "source": "backfill" if i < backfill_days else "actual"}))
    return out


def test_series_split_and_summary():
    start = datetime.date(2026, 5, 22)
    nets = [44000, 44300, 44100, 44800, 45200, 46000, 47200, 48100, 49000, 51002.77]
    cards = app._networth_cards(_FakeClient(_rows(start, nets, backfill_days=4)))
    assert len(cards) == 1, cards
    c = cards[0]
    assert c["titleFormatted"] == "Net worth"
    assert c["currentFormatted"] == "$51,002.77", c["currentFormatted"]
    assert c["currentDirection"] == "up"
    # Two series: a dashed backfill prefix (incl. the join point) + a solid actual line.
    s = c["series"]
    assert len(s) == 2, s
    assert s[0].get("dashed") is True and s[1].get("dashed") in (None, False)
    assert len(s[0]["points"]) == 5 and len(s[1]["points"]) == 6, (s[0], s[1])  # overlap one
    assert len(s[0]["x"]) == len(s[0]["points"]) and len(s[1]["x"]) == len(s[1]["points"])
    # Change = last - first; arrow up; span shows low/high; footnote names the estimate.
    assert c["changeFormatted"].startswith("▲ $7,002.77"), c["changeFormatted"]
    assert c["changeDirection"] == "up"
    assert c["rangeFormatted"] == "May 22 – May 31", c["rangeFormatted"]
    assert c["spanFormatted"] == "Low $44,000.00 · High $51,002.77", c["spanFormatted"]
    assert c["footnoteFormatted"] == "≈ first 4 days estimated", c["footnoteFormatted"]
    print("✓ net-worth card: split series, change, span, footnote")


def test_degrades_to_no_card():
    # Missing/unreadable history table → no card (never an error).
    assert app._networth_cards(_FakeClient(raise_exc=RuntimeError("404 not found"))) == []
    # Fewer than two points → no trend to draw.
    one = app._networth_cards(_FakeClient(_rows(datetime.date(2026, 6, 1), [42000], 0)))
    assert one == [], one
    # All-actual → a single solid series.
    cards = app._networth_cards(_FakeClient(_rows(datetime.date(2026, 6, 1), [42000, 42500, 43000], 0)))
    assert len(cards) == 1 and len(cards[0]["series"]) == 1
    assert cards[0]["series"][0].get("dashed") in (None, False)
    assert cards[0]["footnoteFormatted"] == ""
    print("✓ net-worth card degrades cleanly (missing table / too few points / all-actual)")


if __name__ == "__main__":
    test_series_split_and_summary()
    test_degrades_to_no_card()
    print("✓ networth tests passed")
