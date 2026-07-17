#!/usr/bin/env python3
"""Unit tests for the Budget page (get_budget): per-category monthly-average spending,
min–max range, and hybrid bucket mapping — with a fake BigQuery client (no BigQuery).

    python3 bq_sidecar/test_budget.py
"""
import datetime
import os

os.environ.setdefault("BQ_DATASET", "test-proj.home_afm")  # table helpers need a project

import app  # bq_sidecar/app.py  # noqa: E402


class _Row:
    def __init__(self, d):
        self._d = d

    def items(self):
        return self._d.items()


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def result(self, timeout=None):
        return list(self._rows)


class _FakeClient:
    """Returns transaction rows for the spend query and category rows for the group query,
    distinguished by a marker in the SQL."""
    def __init__(self, tx_rows, cat_rows, tx_raise=None):
        self._tx, self._cat, self._tx_raise = tx_rows, cat_rows, tx_raise

    def query(self, sql):
        if "category_group_name" in sql:
            return _Q(self._cat)
        if self._tx_raise:
            raise self._tx_raise
        return _Q(self._tx)


def _m(y, mo):
    return datetime.date(y, mo, 1)


def test_budget_buckets_avg_and_range():
    # Two categories over 3 months. Rent is in an "Essential" group (lines up); Dining in
    # a "Lifestyle" group; Vacation Fund maps via the Goals keyword on its name.
    tx = [
        _Row({"category_id": "c1", "category_name": "Rent", "m": _m(2026, 3), "spend": 2000.0}),
        _Row({"category_id": "c1", "category_name": "Rent", "m": _m(2026, 4), "spend": 2000.0}),
        _Row({"category_id": "c1", "category_name": "Rent", "m": _m(2026, 5), "spend": 2000.0}),
        _Row({"category_id": "c2", "category_name": "Dining", "m": _m(2026, 3), "spend": 300.0}),
        _Row({"category_id": "c2", "category_name": "Dining", "m": _m(2026, 5), "spend": 600.0}),
        _Row({"category_id": "c3", "category_name": "Vacation Fund", "m": _m(2026, 4), "spend": 900.0}),
    ]
    cats = [
        _Row({"id": "c1", "category_group_name": "Essential"}),
        _Row({"id": "c2", "category_group_name": "Lifestyle"}),
        _Row({"id": "c3", "category_group_name": "Misc"}),  # no group match → name keyword "fund"? no; "Goals"
    ]
    app._client = lambda: _FakeClient(tx, cats)
    out = app.get_budget(months=3)

    assert out["title"] == "Budget"
    titles = [b["title"] for b in out["buckets"]]
    # Bucket order preserved (Essential, then Lifestyle); Goals present via "Vacation Fund"?
    assert "Essential" in titles and "Lifestyle" in titles, titles
    ess = next(b for b in out["buckets"] if b["title"] == "Essential")
    rent = next(c for c in ess["categories"] if c["name"] == "Rent")
    # 3 months, $2000 each → avg $2,000.00/mo, range $2,000–$2,000.
    assert rent["avgFormatted"] == "$2,000.00/mo", rent
    assert rent["rangeFormatted"] == "$2,000.00 – $2,000.00", rent
    assert ess["subtotalFormatted"] == "$2,000.00/mo"

    # Rent is steady → not flagged variable, no annual projection.
    assert rent["variable"] is False and "/yr" not in rent["rangeFormatted"], rent

    life = next(b for b in out["buckets"] if b["title"] == "Lifestyle")
    dining = next(c for c in life["categories"] if c["name"] == "Dining")
    # Dining: 300, 0 (no April spend), 600 over 3 months → avg 300, range $0 – $600.
    # Lumpy (range 600 ≥ avg 300) → also a projected annual run-rate of avg×12 = $3,600/yr.
    assert dining["avgFormatted"] == "$300.00/mo", dining
    assert dining["variable"] is True, dining
    assert dining["annualFormatted"] == "~$3,600/yr", dining
    assert dining["rangeFormatted"] == "$0.00 – $600.00 · ~$3,600/yr projected", dining
    # window label spans the 3 months
    assert out["windowFormatted"] == "Mar 2026 – May 2026", out["windowFormatted"]
    assert "last 3 full months" in out["subtitleFormatted"], out["subtitleFormatted"]
    print("✓ budget: per-category avg + range, bucket subtotals, window label")


def test_budget_degrades_without_table():
    app._client = lambda: _FakeClient([], [], tx_raise=RuntimeError("404 Not found: ynab_transactions"))
    out = app.get_budget(months=6)
    assert out["buckets"] == [] and out["rowCount"] == 0
    assert out["emptyFormatted"].startswith("Couldn't read spending"), out
    print("✓ budget degrades cleanly when the transactions table is missing")


def test_bucket_mapping_priority():
    # Group name that lines up wins; real-estate keyword beats essential; unknown → Other.
    assert app._budget_bucket("Shared Expenses", "Whatever") == "Shared"
    assert app._budget_bucket("Housing", "Mortgage Payment") == "Real Estate"
    assert app._budget_bucket("Lifestyle", "Concerts") == "Lifestyle"
    assert app._budget_bucket("Mystery", "Widget") == "Other"
    print("✓ bucket mapping: group-lines-up, keyword fallback, Other catch-all")


if __name__ == "__main__":
    test_budget_buckets_avg_and_range()
    test_budget_degrades_without_table()
    test_bucket_mapping_priority()
    print("✓ budget tests passed")
