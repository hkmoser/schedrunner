#!/usr/bin/env python3
"""Unit tests for the Docs browser's resilience to Google Drive for Desktop's transient
EDEADLK ('Resource deadlock avoided') on a folder listing (no Drive needed).

    python3 bq_sidecar/test_docs.py
"""
import errno
import os
import tempfile

import app  # bq_sidecar/app.py


def test_retry_os_absorbs_transient():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        return "ok"

    assert app._retry_os(flaky, attempts=4, delay=0) == "ok"
    assert calls["n"] == 3, calls

    # A non-transient error is re-raised immediately (not retried away).
    def hard():
        calls["n"] += 1
        raise OSError(errno.ENOENT, "missing")

    calls["n"] = 0
    try:
        app._retry_os(hard, attempts=4, delay=0)
        assert False, "should have raised"
    except OSError as exc:
        assert exc.errno == errno.ENOENT and calls["n"] == 1, calls
    print("✓ _retry_os retries EDEADLK, re-raises real errors")


def test_docs_deadlock_degrades_friendly():
    """A folder whose listing keeps raising EDEADLK yields a friendly message that names
    the Drive/online-only cause, not a raw [Errno 11]."""
    with tempfile.TemporaryDirectory() as base:
        sub = os.path.join(base, "Journal", "me")
        os.makedirs(sub)
        os.environ["DOCS_DIR"] = base
        os.environ["DOCS_MIRROR"] = "/nonexistent/mirror"  # force the live path, not a mirror
        real_listdir = os.listdir

        def boom(path, *a, **k):
            if os.path.normpath(path) == os.path.normpath(sub):
                raise OSError(errno.EDEADLK, "Resource deadlock avoided")
            return real_listdir(path, *a, **k)

        # Keep the retry semantics but drop the real backoff so the test stays fast.
        real_retry = app._retry_os
        os.listdir = boom
        app._retry_os = lambda fn, attempts=4, delay=0.25: real_retry(fn, attempts=attempts, delay=0)
        try:
            out = app.get_docs("Journal/me")
        finally:
            os.listdir = real_listdir
            app._retry_os = real_retry
            del os.environ["DOCS_DIR"]
            del os.environ["DOCS_MIRROR"]

        assert out["entries"] == [] and out["rowCount"] == 0, out
        msg = out.get("emptyFormatted", "")
        assert "Errno" not in msg and "deadlock" not in msg.lower(), msg
        assert "Google Drive" in msg and "online-only" in msg, msg
    print("✓ get_docs degrades a Drive deadlock to a friendly message")


if __name__ == "__main__":
    test_retry_os_absorbs_transient()
    test_docs_deadlock_degrades_friendly()
    print("✓ docs tests passed")
