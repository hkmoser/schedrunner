#!/usr/bin/env python3
"""Unit tests for the Messages page's Drive-synced iMessage reader (no Drive/IMAP needed).

    python3 bq_sidecar/test_messages.py
"""
import datetime
import json
import os
import tempfile

import app  # bq_sidecar/app.py


def test_imessage_from_drive():
    now = datetime.datetime.now(datetime.timezone.utc)
    with tempfile.TemporaryDirectory() as d:
        recs = [
            {"sender": "+15551234567", "text": "Hey are you around?", "ts": "2026-06-20T17:00:00Z", "is_from_me": False},
            {"sender": "+15551234567", "text": "On my way", "ts": "2026-06-20T17:01:00Z", "is_from_me": True},
            {"from": "Mom", "body": "Call me", "ts": "2026-06-20T16:13:20Z", "direction": "received"},
        ]
        with open(os.path.join(d, "messages_20260620.jsonl"), "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        os.environ["MESSAGES_DIR"] = d
        try:
            items, status = app._imessage_items(now, 25)
        finally:
            del os.environ["MESSAGES_DIR"]
        assert status["status"] == "OK", status
        # Newest first; is_from_me → "Me" + outbound; direction string → inbound.
        assert [i["titleFormatted"] for i in items] == ["Me", "+15551234567", "Mom"], items
        assert items[0]["direction"] == "out" and items[1]["direction"] == "in"
        assert items[0]["detailFormatted"] == "On my way"
        assert all("_t" not in i for i in items), "sort key must not leak"

    # JSON array / {messages:[…]} shape also parses; flexible field names.
    with tempfile.TemporaryDirectory() as d2:
        json.dump({"messages": [{"contact": "Alice", "message": "hi", "ts": "2026-06-20T16:00:00Z"}]},
                  open(os.path.join(d2, "latest.json"), "w"))
        os.environ["MESSAGES_DIR"] = d2
        try:
            items2, _ = app._imessage_items(now, 25)
        finally:
            del os.environ["MESSAGES_DIR"]
        assert items2 and items2[0]["titleFormatted"] == "Alice" and items2[0]["detailFormatted"] == "hi"

    # Missing folder degrades cleanly.
    os.environ["MESSAGES_DIR"] = "/nonexistent/messages"
    try:
        items3, status3 = app._imessage_items(now, 25)
    finally:
        del os.environ["MESSAGES_DIR"]
    assert items3 == [] and status3["status"] == "Not found"
    print("✓ Drive-synced iMessage reader (jsonl + json + degradation)")


if __name__ == "__main__":
    test_imessage_from_drive()
    print("✓ messages tests passed")
