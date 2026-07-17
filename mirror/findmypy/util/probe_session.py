#!/usr/bin/env python3
"""
Session-warmth probe — diagnoses WHY FindMy needs /accountLogin so often.

The cron pipeline calls /accountLogin on nearly every 5-min run because each
fresh process cold-starts (no in-memory FindMy context) and the on-disk service
cookies have gone stale. That burns Apple's per-session renewal quota and kills
the session within hours.

The open question this probe answers with data:

    Is the cold-start 450 caused by (A) pure TIME-BASED cookie/token expiry,
    or (B) loss of the in-memory _server_ctx that a fresh process rebuilds?

    - If (A): a long-lived daemon collecting every 5 min hits the SAME wall
      (token stale after ~2-3 min regardless of process) → a daemon does NOT help
      at a 5-min cadence; only faster polling or graceful re-auth helps.
    - If (B): a daemon that keeps _server_ctx in memory sails past the 5-min gap
      with zero /accountLogin calls → a daemon IS the fix.

Method: authenticate ONCE, then fetch device locations repeatedly with growing
gaps between fetches, all in the SAME process. For each fetch we record whether
holding the session in memory for N seconds forced a new /accountLogin. Cross-
reference against the cron logs (which show ~2-3 min disk-cookie staleness) to
distinguish (A) from (B).

SAFETY:
  - By default the probe COPIES ~/.pyicloud to a temp dir and runs against the
    copy, so a bug here cannot touch the live cron session. The copy still shares
    the same Apple account, so /accountLogin calls it makes DO count against the
    same trust-token budget — PAUSE THE CRON while probing (see below).
  - Pass --live to run against the real ~/.pyicloud (not recommended while cron runs).
  - Never falls back to SRP (uses the same _CronPyiCloudService pattern as cron),
    so it cannot trigger the SRP rate limiter.

BEFORE RUNNING — pause the cron so two processes don't hit Apple auth at once:
    # comment out the afm_live.py line in your crontab, or:
    touch /tmp/afm_live.lock && python -c "import fcntl;fcntl.flock(open('/tmp/afm_live.lock','w'),fcntl.LOCK_EX)" &
  (simplest: just edit the crontab, run the probe, then restore it.)

USAGE:
    python util/probe_session.py                 # isolated copy, default schedule
    python util/probe_session.py --live          # against real ~/.pyicloud
    python util/probe_session.py --gaps 30,60,120,300,300,600
    python util/probe_session.py --quick         # short run: 30,90,180,300

Output: verbose stdout AND a timestamped log file in util/probe-logs/.
"""

import argparse
import datetime
import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

# The cron pipeline (afm_live.py) holds an exclusive flock on this file for its
# whole run and exits immediately ("Skipped (still running)") if it can't get it.
# The probe grabs the same lock up front so every cron tick during the probe
# no-ops, then releases it on exit — auto-pausing and restoring the live service
# with no crontab edits.
AFM_LOCK_PATH = "/tmp/afm_live.lock"
# How long to wait for an already-running cron instance to finish before probing.
# A healthy 450-recovery run can take ~6 min; give it headroom.
LOCK_WAIT_SECS = 480

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloudFailedLoginException,
    PyiCloudAuthRequiredException,
    PyiCloudAPIResponseException,
    PyiCloud2FARequiredException,
)
try:
    from pyicloud.exceptions import PyiCloudAcceptTermsException
except ImportError:
    PyiCloudAcceptTermsException = None

APPLE_ID = os.environ.get("AFM_APPLE_ID", "joe@joemoser.com")
LIVE_COOKIE_DIR = os.path.expanduser("~/.pyicloud")

# Default gap schedule (seconds between successive in-process fetches). Chosen to
# map the staleness boundary AND directly test the 5-min cron cadence (300s) twice
# plus a 10-min gap. Total runtime ≈ sum of gaps.
DEFAULT_GAPS = [30, 60, 90, 120, 150, 180, 210, 240, 300, 300, 600]
QUICK_GAPS = [30, 90, 180, 300]


# ── logging ───────────────────────────────────────────────────────────────────

class Tee:
    """Write to stdout and a log file simultaneously."""
    def __init__(self, path):
        self.f = open(path, "a", buffering=1)
        self.path = path

    def __call__(self, msg=""):
        line = str(msg)
        print(line)
        self.f.write(line + "\n")

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


def _ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def pause_live_cron(log):
    """Acquire afm_live.py's flock so cron ticks no-op for the probe's duration.
    Returns the open file handle (keep it alive — closing releases the lock) or
    None if the lock could not be taken. Blocks up to LOCK_WAIT_SECS for an
    in-flight cron run to finish first."""
    lf = open(AFM_LOCK_PATH, "w")
    deadline = time.monotonic() + LOCK_WAIT_SECS
    announced = False
    while True:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            log(f"[pause] acquired {AFM_LOCK_PATH} — live cron is paused for the probe")
            return lf
        except IOError:
            if not announced:
                log(f"[pause] a cron run is in progress; waiting up to "
                    f"{LOCK_WAIT_SECS}s for it to finish before probing...")
                announced = True
            if time.monotonic() > deadline:
                log(f"[pause] WARNING: could not acquire {AFM_LOCK_PATH} within "
                    f"{LOCK_WAIT_SECS}s. Proceeding WITHOUT pausing cron — expect "
                    f"noisier results (two sessions may hit Apple auth). Consider "
                    f"pausing the crontab manually.")
                lf.close()
                return None
            time.sleep(5)


def resume_live_cron(lock_handle, log):
    """Release the flock so the next cron tick runs normally."""
    if lock_handle is None:
        return
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()
        log(f"[resume] released {AFM_LOCK_PATH} — live cron will run on its next tick")
    except Exception as e:
        log(f"[resume] WARNING: failed to release lock cleanly: {e} "
            f"(it releases automatically when this process exits)")


# ── no-SRP service (same guarantee as the cron's _CronPyiCloudService) ─────────

class _ProbeService(PyiCloudService):
    """PyiCloudService that never falls back to SRP. Mirrors _CronPyiCloudService
    from afm_live.py: pass force_refresh through, and only ever token-auth."""

    def authenticate(self, force_refresh: bool = False, service=None) -> None:
        super().authenticate(force_refresh=force_refresh, service=service)

    def _authenticate(self) -> None:
        # Only token auth. If it fails, re-raise as-is (do NOT let the base class
        # fall through to SRP). The probe treats any auth failure as fatal.
        self._authenticate_with_token()


# ── /accountLogin instrumentation ─────────────────────────────────────────────
# We log every /accountLogin but — unlike the cron — we do NOT skip, sleep, or
# raise. We want the RAW behavior: does the in-memory session need a renewal?

_accountlogin_events = []  # list of dicts: {n, ts_mono, elapsed, outcome}


def install_accountlogin_probe(api, log):
    _orig = api._authenticate_with_token

    def _wrapped():
        n = len(_accountlogin_events) + 1
        t0 = time.monotonic()
        log(f"    >>> /accountLogin #{n} FIRED at {_ts()} "
            f"(a 450 or force_refresh triggered a token renewal)")
        outcome = "ok"
        try:
            _orig()
        except Exception as e:
            outcome = f"FAIL:{type(e).__name__}"
            raise
        finally:
            elapsed = time.monotonic() - t0
            _accountlogin_events.append(
                {"n": n, "ts_mono": t0, "elapsed": elapsed, "outcome": outcome}
            )
            log(f"    <<< /accountLogin #{n} {outcome} in {elapsed:.1f}s")

    api._authenticate_with_token = _wrapped


# ── session state snapshots ───────────────────────────────────────────────────

def session_token_prefix(api):
    try:
        tok = api.session_data.get("session_token", "") if hasattr(api, "session_data") else ""
        if not tok:
            # timlaing stores it on the session object
            tok = getattr(getattr(api, "session", None), "data", {}).get("session_token", "")
        return (tok[:16] + f"...(len={len(tok)})") if tok else "<none>"
    except Exception as e:
        return f"<err:{e}>"


def trust_token_present(api):
    try:
        data = getattr(getattr(api, "session", None), "data", {}) or {}
        if not data and hasattr(api, "session_data"):
            data = api.session_data or {}
        return bool(data.get("trust_token"))
    except Exception:
        return None


def server_ctx_id(api):
    """Identity of the FindMy manager's in-memory _server_ctx. If this stays the
    same object across fetches, the in-memory context is being reused (daemon-like)."""
    try:
        mgr = getattr(api, "_devices", None)
        if mgr is None:
            return "<no-manager-yet>"
        ctx = getattr(mgr, "_server_ctx", "<no-attr>")
        if ctx is None:
            return "None"
        return f"id={hex(id(ctx))}"
    except Exception as e:
        return f"<err:{e}>"


def cookie_expiry_snapshot(api, log, names=("X-APPLE-WEBAUTH-TOKEN", "X-APPLE-WEBAUTH-HSA-TRUST")):
    try:
        now = time.time()
        for c in api.session.cookies:
            if c.name in names:
                if c.expires:
                    days = (c.expires - now) / 86400.0
                    log(f"      cookie {c.name}: expires in {days:.2f} days")
                else:
                    log(f"      cookie {c.name}: session cookie (no expiry)")
    except Exception as e:
        log(f"      cookie snapshot failed: {e}")


# ── the fetch ─────────────────────────────────────────────────────────────────

def do_fetch(api, log):
    """Force a fresh FindMy client refresh and count devices. Returns
    (device_count, accountlogin_fired, elapsed, error_or_None)."""
    calls_before = len(_accountlogin_events)
    t0 = time.monotonic()
    err = None
    count = 0
    try:
        # Access api.devices then iterate — this is what dev.location does under the
        # hood in afm_live. Force a locate refresh by touching .location on each.
        for dev in api.devices:
            try:
                _ = dev.location  # attribute in timlaing fork; triggers refresh path
            except TypeError:
                _ = dev.location()  # older API shape
            count += 1
    except PyiCloudAuthRequiredException as e:
        err = f"PyiCloudAuthRequiredException (raw 450, reauth aborted): {e}"
    except PyiCloud2FARequiredException as e:
        err = f"PyiCloud2FARequiredException (is_trusted_session=False): {e}"
    except PyiCloudFailedLoginException as e:
        err = f"PyiCloudFailedLoginException (session dead): {e}"
    except PyiCloudAPIResponseException as e:
        err = f"PyiCloudAPIResponseException code={getattr(e,'code',None)}: {e}"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0
    fired = len(_accountlogin_events) - calls_before
    return count, fired, elapsed, err


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="run against real ~/.pyicloud instead of an isolated copy")
    ap.add_argument("--gaps", type=str, default=None,
                    help="comma-separated seconds between fetches, e.g. 30,60,120,300")
    ap.add_argument("--quick", action="store_true", help="short schedule: 30,90,180,300")
    ap.add_argument("--no-pause", action="store_true",
                    help="do NOT pause the live cron via its flock (default: pause it)")
    args = ap.parse_args()

    if args.gaps:
        gaps = [int(x) for x in args.gaps.split(",") if x.strip()]
    elif args.quick:
        gaps = QUICK_GAPS
    else:
        gaps = DEFAULT_GAPS

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe-logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(
        log_dir, "probe_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
    )
    log = Tee(log_path)

    log("=" * 78)
    log(f"findmypy session-warmth probe   started {datetime.datetime.now().isoformat()}")
    log(f"  Apple ID       : {APPLE_ID}")
    log(f"  gap schedule   : {gaps}  (total ≈ {sum(gaps)//60} min {sum(gaps)%60} s)")
    log(f"  log file       : {log_path}")
    log("=" * 78)

    # ── pause the live cron (via its flock) unless told not to ────────────────
    lock_handle = None
    if args.no_pause:
        log("[pause] --no-pause set — leaving live cron running (results may be noisier)")
    else:
        lock_handle = pause_live_cron(log)

    # ── isolate the session unless --live ─────────────────────────────────────
    tmp_dir = None
    if args.live:
        cookie_dir = LIVE_COOKIE_DIR
        log(f"[setup] LIVE MODE — using real {cookie_dir} (make sure cron is paused!)")
    else:
        if not os.path.isdir(LIVE_COOKIE_DIR):
            log(f"[setup] ERROR: {LIVE_COOKIE_DIR} does not exist — nothing to copy. "
                f"Run util/auth.sh first.")
            log.close()
            sys.exit(1)
        tmp_dir = tempfile.mkdtemp(prefix="pyicloud-probe-")
        cookie_dir = os.path.join(tmp_dir, ".pyicloud")
        shutil.copytree(LIVE_COOKIE_DIR, cookie_dir)
        log(f"[setup] ISOLATED — copied {LIVE_COOKIE_DIR} → {cookie_dir}")
        log(f"[setup] (the live session is untouched; still pause cron to avoid "
            f"two sessions hitting Apple auth at once)")

    try:
        # ── authenticate ONCE ─────────────────────────────────────────────────
        log(f"\n[auth] {_ts()} constructing service (token-only, no SRP)...")
        t0 = time.monotonic()
        try:
            api = _ProbeService(APPLE_ID, cookie_directory=cookie_dir)
        except PyiCloud2FARequiredException as e:
            log(f"[auth] FATAL: is_trusted_session=False — trust token bad/missing. "
                f"Run util/auth.sh to get trust_token saved: True.  ({e})")
            raise SystemExit(1)
        except PyiCloudFailedLoginException as e:
            log(f"[auth] FATAL: session dead (Invalid authentication token). "
                f"Run util/auth.sh.  ({e})")
            raise SystemExit(1)
        log(f"[auth] constructed in {time.monotonic()-t0:.1f}s")

        install_accountlogin_probe(api, log)

        log(f"[auth] session_token : {session_token_prefix(api)}")
        log(f"[auth] trust_token    : {'present' if trust_token_present(api) else 'MISSING'}")
        cookie_expiry_snapshot(api, log)

        # ── warm-up fetch (this is the cold-start; a /accountLogin here is EXPECTED
        #    and mirrors what every fresh cron process does) ─────────────────────
        log(f"\n[warmup] {_ts()} first fetch (cold-start — /accountLogin here is normal)")
        count, fired, elapsed, err = do_fetch(api, log)
        last_login_mono = _accountlogin_events[-1]["ts_mono"] if _accountlogin_events else t0
        log(f"[warmup] devices={count} accountLogin_fired={fired} "
            f"elapsed={elapsed:.1f}s server_ctx={server_ctx_id(api)} err={err}")

        # ── timed fetch cycles ────────────────────────────────────────────────
        results = []
        for i, gap in enumerate(gaps, 1):
            log(f"\n[cycle {i}/{len(gaps)}] sleeping {gap}s (holding session in memory)...")
            time.sleep(gap)

            secs_since_login = (time.monotonic() - last_login_mono)
            log(f"[cycle {i}] {_ts()} fetching  "
                f"(t+{secs_since_login:.0f}s since last /accountLogin)")
            ctx_before = server_ctx_id(api)
            count, fired, elapsed, err = do_fetch(api, log)
            ctx_after = server_ctx_id(api)
            if _accountlogin_events:
                last_login_mono = _accountlogin_events[-1]["ts_mono"]

            verdict = "STALE→renewed" if fired else "still-warm"
            log(f"[cycle {i}] gap={gap}s  devices={count}  accountLogin_fired={fired}  "
                f"[{verdict}]  elapsed={elapsed:.1f}s")
            log(f"[cycle {i}] server_ctx: {ctx_before} -> {ctx_after}  "
                f"session_token={session_token_prefix(api)}")
            if err:
                log(f"[cycle {i}] ERROR: {err}")
            results.append({
                "cycle": i, "gap": gap, "secs_since_login": round(secs_since_login),
                "devices": count, "accountlogin_fired": fired,
                "warm": fired == 0 and not err, "elapsed": round(elapsed, 1),
                "server_ctx_stable": ctx_before == ctx_after, "error": err,
            })

        # ── summary / verdict ─────────────────────────────────────────────────
        log("\n" + "=" * 78)
        log("SUMMARY  (each cycle held the session in memory for `gap` seconds, then fetched)")
        log("=" * 78)
        log(f"{'gap':>5} {'t+login':>8} {'devs':>5} {'reNEW':>6} {'warm':>5} "
            f"{'ctx_stbl':>9} {'sec':>5}  error")
        for r in results:
            log(f"{r['gap']:>5} {r['secs_since_login']:>8} {r['devices']:>5} "
                f"{r['accountlogin_fired']:>6} {str(r['warm']):>5} "
                f"{str(r['server_ctx_stable']):>9} {r['elapsed']:>5}  {r['error'] or ''}")

        log(f"\ntotal /accountLogin calls over whole run: {len(_accountlogin_events)} "
            f"(1 warm-up cold-start is expected)")

        # Find the largest gap that stayed warm and the smallest that went stale.
        warm_gaps = [r["gap"] for r in results if r["warm"]]
        stale_gaps = [r["gap"] for r in results if not r["warm"] and not r["error"]]
        max_warm = max(warm_gaps) if warm_gaps else None
        min_stale = min(stale_gaps) if stale_gaps else None

        log("\nINTERPRETATION")
        if warm_gaps and (min_stale is None or max_warm >= 300):
            log(f"  ✓ Session stayed WARM across gaps up to {max_warm}s in-process.")
            log(f"    A 300s (cron-interval) gap did NOT force /accountLogin while the")
            log(f"    session was held in memory.  → The cold-start 450 is caused by")
            log(f"    losing the in-memory FindMy context between processes, NOT by")
            log(f"    time-based token expiry.  → A DAEMON WOULD FIX THIS.")
        elif min_stale is not None and min_stale <= 180:
            log(f"  ✗ Session went STALE in-process after only {min_stale}s "
                f"(needed /accountLogin).")
            log(f"    Holding the session in memory did NOT prevent renewal at the")
            log(f"    cron cadence.  → The limit is TIME-BASED token expiry, not lost")
            log(f"    in-memory context.  → A 5-MIN DAEMON WOULD NOT HELP; the real")
            log(f"    levers are polling faster than ~{min_stale}s, or re-authing more")
            log(f"    gracefully (fewer, better-spaced /accountLogin calls).")
        else:
            log(f"  ? Mixed result. max warm gap={max_warm}s, min stale gap={min_stale}s.")
            log(f"    Compare against cron logs: cron disk-cookies go stale ~2-3 min. If")
            log(f"    in-process stays warm much longer than that, in-memory context is")
            log(f"    the difference and a daemon helps.")
        log("=" * 78)

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            log(f"[cleanup] removed isolated copy {tmp_dir}")
        resume_live_cron(lock_handle, log)
        log(f"[done] full log saved to: {log_path}")
        log.close()


if __name__ == "__main__":
    main()
