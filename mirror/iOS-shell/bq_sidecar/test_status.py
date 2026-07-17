#!/usr/bin/env python3
"""Unit tests for the log status flag + status sorting on /logs and /schedlogs
(no BigQuery/Drive). Runnable anywhere:

    python3 bq_sidecar/test_status.py
"""
import datetime
import os
import tempfile

import app  # bq_sidecar/app.py (BigQuery imports are lazy, so this is import-safe)


def test_log_status():
    now = datetime.datetime.now(datetime.timezone.utc)
    fresh = now - datetime.timedelta(minutes=2)
    old = now - datetime.timedelta(hours=3)
    # (tail, mtime) -> expected (label, rank)
    cases = [
        ("12:00 listening on :8080\nhealthcheck ok\n/dashboard 200 14ms", old, "OK", 2),
        ("Starting job\nTraceback (most recent call last):\n Exception: boom", old, "Failed", 1),
        ("sync complete\n0 errors\nall good", old, "OK", 2),          # negation: "0 errors" isn't a failure
        ("nightly-sync starting\nfetching items...", fresh, "Running", 0),
        ("building bundle\ncompiling sources", old, "Running?", 0),    # in-progress but file is stale
        ("step 1 done\nFATAL: disk full", old, "Failed", 1),
        ("WARNING hue: 403 Forbidden, retrying in 15s", old, "—", 3),  # a warning/retry, not a failure
    ]
    for tail, mtime, label, rank in cases:
        got_label, _color, got_rank = app._log_status(tail, mtime, now)
        assert got_label == label, f"{tail!r}: expected {label}, got {got_label}"
        assert got_rank == rank, f"{tail!r}: expected rank {rank}, got {got_rank}"
    print("✓ _log_status classifies running/failed/ok/idle")


def _write(d, name, body):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(body)


def test_schedlogs_sorted_by_status():
    SEP = "-" * 40
    ok_block = (
        "[Fri Jun 19 01:00:00 EDT 2026] Running /x/ok.sh\n"
        "did the thing\n"
        "[Fri Jun 19 01:00:05 EDT 2026] Finished /x/ok.sh\n" + SEP + "\n"
    )
    failed_block = (
        "[Fri Jun 19 02:00:00 EDT 2026] Running /x/bad.sh\n"
        "boom\nFAILED\n"
        "[Fri Jun 19 02:00:03 EDT 2026] Finished /x/bad.sh\n" + SEP + "\n"
    )
    running_block = (
        "[Fri Jun 19 03:00:00 EDT 2026] Running /x/busy.sh\n"
        "still working\n"  # no Finished line ⇒ running
    )
    with tempfile.TemporaryDirectory() as d:
        _write(d, "ok.log", ok_block)
        _write(d, "bad.log", failed_block)
        _write(d, "busy.log", running_block)
        os.environ["SCHEDRUNNER_LOG_DIR"] = d
        try:
            out = app.get_schedlogs()
        finally:
            del os.environ["SCHEDRUNNER_LOG_DIR"]
    order = [(e["name"], e["statusFormatted"]) for e in out["entries"]]
    assert order[0][1] == "running?", f"running first, got {order}"
    assert order[1][1] == "FAILED", f"failed second, got {order}"
    assert order[2][1] == "OK", f"ok third, got {order}"
    # Internal sort keys must not leak into the response.
    assert all("_rank" not in e and "_ts" not in e for e in out["entries"]), "sort keys leaked"
    print("✓ get_schedlogs orders running → failed → ok")


def test_logs_filter_and_drill():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "bad.log", "start\nTraceback (most recent call last):\nException: boom\n")
        _write(d, "ok.log", "listening on :8080\nhealthcheck ok\n200 14ms\n")
        _write(d, "busy.log", "nightly-sync starting\nfetching items...\n")
        os.environ["LOG_DIR"] = d
        try:
            out = app.get_logs()
            keys = {f["name"]: f["statusKey"] for f in out["files"]}
            assert keys == {"bad.log": "failed", "ok.log": "ok", "busy.log": "running"}, keys
            # Sorted running → failed → ok.
            assert [f["statusKey"] for f in out["files"]] == ["running", "failed", "ok"]
            chips = {c["key"]: c["count"] for c in out["statusFilters"]}
            assert chips == {"running": 1, "failed": 1, "ok": 1}, chips
            assert all(f["detailHref"].startswith("/screen/logfile?file=") for f in out["files"])
            # ?status= filter narrows the list.
            only_failed = app.get_logs("failed")
            assert [f["name"] for f in only_failed["files"]] == ["bad.log"], only_failed["files"]
            # Drill-down flags anomalies and is path-traversal safe.
            lf = app.get_logfile("bad.log")
            assert lf["rowCount"] == 2 and any("Traceback" in a["lineFormatted"] for a in lf["anomalies"])
            assert app.get_logfile("../../etc/passwd").get("error"), "traversal is blocked"
        finally:
            del os.environ["LOG_DIR"]
    print("✓ get_logs status filter + get_logfile anomalies/guard")


def test_balance_groups():
    cases = {
        "WF Bills": "Operational", "WF Spending": "Operational", "Wells Fargo Checking": "Operational",
        "WF Shared": "Shared",
        "Ally Savings": "Lifestyle", "Ally Spending": "Lifestyle",
        "Ally Mortgage Escrow": "Real Estate", "Chase Real Estate": "Real Estate", "Rental Property": "Real Estate",
        "Vanguard Brokerage": "Other",
    }
    for name, group in cases.items():
        assert app._account_group(name) == group, f"{name!r}: expected {group}, got {app._account_group(name)}"
    # Real estate wins over Ally (so an Ally real-estate account isn't Lifestyle).
    assert app._account_group("Ally Mortgage") == "Real Estate"
    print("✓ _account_group maps WF/Ally/real-estate accounts")


if __name__ == "__main__":
    test_log_status()
    test_schedlogs_sorted_by_status()
    test_logs_filter_and_drill()
    test_balance_groups()
    print("✓ status/sort tests passed")
