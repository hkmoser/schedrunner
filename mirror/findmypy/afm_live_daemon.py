#!/usr/bin/env python3
"""
afm_live_daemon.py — persistent replacement for the cron-spawned afm_live.py.

WHY THIS EXISTS
    The cron model spawns a fresh process every 5 min. Each fresh process starts
    with the FindMy service's in-memory _server_ctx = None, so it is forced down
    pyicloud's heavier initClient path, which 450s whenever the on-disk cookies
    are more than ~2-3 min old — i.e. on essentially every run. Each 450 triggers
    an /accountLogin, and Apple revokes the session after a limited number of
    those, killing tracking every few hours.

    The probe (util/probe_session.py) proved that a session held in memory stays
    warm across a 10-minute idle gap with ZERO /accountLogin calls. This daemon
    keeps ONE PyiCloudService alive and loops over it, so /accountLogin drops from
    ~144/day to a handful — well within Apple's tolerance.

OWNERSHIP / SUPERVISION
    Runs under a KeepAlive LaunchAgent owned by schedrunner (no StartInterval —
    the process is persistent, not scheduled). launchd restarts it if it dies;
    ThrottleInterval spaces restarts. On a genuine session death it alerts and
    exits(1); after util/auth.sh it recovers on the next launchd restart.

DEAD-SESSION HOLD (see afm_session_hold.py)
    KeepAlive + ThrottleInterval=300 means a dead session would otherwise be
    re-presented to Apple every 5 minutes forever (~288 rejected auth attempts a
    day). That hammering is what makes Apple rate-limit SRP and withhold trust
    tokens for 24h — it sabotages the auth.sh run that is the only real fix. So a
    rejection is recorded to ~/.pyicloud-daemon-session-dead, and while that hold
    is active startup short-circuits WITHOUT contacting Apple, re-probing on an
    escalating 15m/30m/60m schedule (~24 attempts/day). The hold is keyed to a
    fingerprint of ~/.pyicloud, which auth.sh replaces via atomic rename — so
    recovery is still just "run auth.sh", with no state file to clean up by hand.
    Transient failures (5xx/429/network) are untouched by this and keep the
    existing 5-minute cadence.

MUTUAL EXCLUSION WITH THE CRON FALLBACK
    Holds the same /tmp/afm_live.lock that afm_live.py uses, for its whole life.
    While the daemon runs, any stray cron afm_live.py tick sees the lock and exits
    ("Skipped (still running)"). Stop the daemon and the cron fallback resumes —
    this is the instant rollback path.

CONFIG (env overrides)
    AFM_INTERVAL_SECS   seconds between collection cycles (default 300)
    AFM_APPLE_ID        Apple ID (default joe@joemoser.com)
    AFM_BASE_PATH       repo path for CSV output
    PUSHCUT_API_KEY     Pushcut key for health alerts
"""

import datetime
import fcntl
import os
import signal
import sys
import time

import requests

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

import afm_pipeline
import afm_session_hold
import afm_trust_repair

INTERVAL_SECS = int(os.environ.get("AFM_INTERVAL_SECS", "300"))
LOCK_PATH = "/tmp/afm_live.lock"
PUSHCUT_API_KEY = os.environ.get("PUSHCUT_API_KEY", "QFNjvttld5Fem3eor-5pd")

# After a transient failure, wait this long before the next cycle instead of the
# full interval, so we recover quickly once Apple settles.
TRANSIENT_RETRY_SECS = 60
# Dedup the "run auth.sh" alert across launchd restarts (in-memory state is lost
# on exit, so use a file cooldown like afm_live.py does).
DEAD_ALERT_FILE = os.path.expanduser("~/.pyicloud-daemon-alert-last")
DEAD_ALERT_COOLDOWN_SECS = 1800  # at most one session-dead alert per 30 min
EXPIRY_WARN_DAYS = 5
EXPIRY_WARN_FILE = os.path.expanduser("~/.pyicloud-expiry-warned")
_SESSION_COOKIE_NAMES = ("X-APPLE-WEBAUTH-HSA-TRUST", "X-APPLE-WEBAUTH-TOKEN")

_running = True


def log(msg=""):
    print(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def send_alert(text):
    try:
        requests.post(
            f"https://api.pushcut.io/{PUSHCUT_API_KEY}/notifications/AFM%20health",
            json={"title": "AFM daemon", "text": str(text)[:500]},
            timeout=10,
        )
    except Exception:
        pass


def send_alert_deduped(text):
    """Send at most one session-dead alert per cooldown window (survives restarts)."""
    try:
        last = float(open(DEAD_ALERT_FILE).read().strip())
        if time.time() - last < DEAD_ALERT_COOLDOWN_SECS:
            return
    except Exception:
        pass
    send_alert(text)
    try:
        with open(DEAD_ALERT_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def clear_dead_alert_cooldown():
    try:
        if os.path.exists(DEAD_ALERT_FILE):
            os.remove(DEAD_ALERT_FILE)
    except Exception:
        pass


def check_session_expiry(api):
    """Warn once/day if a core session cookie is near expiry. Never raises."""
    try:
        soonest_days = None
        soonest_name = None
        for cookie in api.session.cookies:
            if cookie.name in _SESSION_COOKIE_NAMES and cookie.expires:
                days = (cookie.expires - time.time()) / 86400.0
                if soonest_days is None or days < soonest_days:
                    soonest_days, soonest_name = days, cookie.name
        if soonest_days is None or soonest_days > EXPIRY_WARN_DAYS:
            return
        today = datetime.date.today().isoformat()
        try:
            if open(EXPIRY_WARN_FILE).read().strip() == today:
                return
        except Exception:
            pass
        msg = (f"iCloud session expires in {soonest_days:.1f} day(s) "
               f"({soonest_name}) — run util/auth.sh soon to avoid an outage.")
        log(f"[auth] {msg}")
        send_alert(msg)
        try:
            with open(EXPIRY_WARN_FILE, "w") as f:
                f.write(today)
        except Exception:
            pass
    except Exception:
        pass


def _record_session_dead(phase, e):
    """Arm the dead-session hold so launchd's next restart does NOT re-hit Apple
    with a token Apple has already rejected. Purely additive: alert semantics and
    the exit path are unchanged, and any failure here is swallowed."""
    try:
        state = afm_session_hold.record_rejection(
            reason=f"{type(e).__name__}: {e}", phase=phase)
    except Exception:
        state = None
    if state:
        log(f"[hold] session marked dead (rejection #{state['rejections']} at {phase}) — "
            f"skipping Apple auth for the next {(state['next_probe_at'] - time.time()) / 60:.0f} min. "
            f"Fix with: bash util/auth.sh")
    else:
        log("[hold] could not persist dead-session state — restart behavior unchanged")


def _clear_session_hold(why):
    """Session is demonstrably working — drop any hold. Never raises."""
    try:
        if afm_session_hold.clear_hold():
            log(f"[hold] cleared ({why})")
    except Exception:
        pass


def _build_api_repairing():
    """build_api(), but try a trust replay before accepting that the session is dead.

    A 421 from /accountLogin does not mean the credentials are gone: the trust
    endpoint will still hand back a fresh set (see afm_trust_repair). Replaying it
    costs one GET and one POST, is cooldown-limited inside the module, and turns
    the overwhelmingly common death into something that never reaches the user.
    Re-raises the original exception if the repair does not take, so the existing
    alert and hold paths behave exactly as before.
    """
    try:
        return afm_pipeline.build_api()
    except (PyiCloudFailedLoginException, PyiCloudAuthRequiredException,
            PyiCloud2FARequiredException) as exc:
        log(f"[repair] session rejected ({type(exc).__name__}) — attempting trust replay")
        if afm_trust_repair.attempt_repair(log=log):
            return afm_pipeline.build_api()
        log("[repair] trust replay did not recover the session")
        raise


def _is_transient(e):
    """Network blip / Apple 5xx / rate-limit — retry soon, session is fine."""
    return isinstance(e, PyiCloudAPIResponseException) and (
        not isinstance(getattr(e, "code", None), int)
        or e.code >= 500
        or e.code == 429
    )


def _acquire_lock():
    """Hold afm_live.lock for the daemon's whole life so the cron fallback no-ops
    and no second daemon can start. Exits immediately if the lock is already held —
    under schedrunner supervision a relaunch attempt while we're up must be a clean
    no-op, never a pile-up of blocked processes."""
    lf = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log(f"[lock] {LOCK_PATH} already held (daemon already running or a cron "
            f"tick is mid-run) — exiting cleanly")
        sys.exit(0)
    log(f"[lock] acquired {LOCK_PATH} — cron afm_live.py will no-op while this runs")
    return lf


def _handle_signal(signum, frame):
    global _running
    log(f"[daemon] received signal {signum} — will exit after current cycle")
    _running = False


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log("=" * 70)
    log(f"afm_live_daemon starting  interval={INTERVAL_SECS}s  pid={os.getpid()}")
    log("=" * 70)

    # ── dead-session hold ─────────────────────────────────────────────────────
    # If Apple has already rejected this session, do not contact Apple at all.
    # launchd restarts us every ThrottleInterval (300s); without this we would
    # burn ~288 rejected auth attempts a day, which is precisely what causes
    # Apple to rate-limit SRP and withhold trust tokens — i.e. it would sabotage
    # the util/auth.sh run that is the only actual fix. Checked BEFORE the lock:
    # a held restart is a no-op and must not hold afm_live.lock either.
    try:
        hold = afm_session_hold.check_hold()
    except Exception as e:  # fail open — never let this keep a good session down
        log(f"[hold] check failed ({type(e).__name__}: {e}) — proceeding normally")
        hold = None
    if hold is not None and hold.active:
        log(f"[hold] {hold.describe()}")
        sys.exit(0)
    if hold is not None and hold.detail in ("session-replaced", "probe-window"):
        log(f"[hold] {hold.describe()}")

    lock = _acquire_lock()  # noqa: F841 — keep handle alive for the process lifetime

    # ── construct the ONE long-lived session ──────────────────────────────────
    try:
        api = _build_api_repairing()
        log("[auth] session constructed (token-only, no SRP)")
        _clear_session_hold("session constructed")
    except PyiCloud2FARequiredException as e:
        send_alert_deduped("Session untrusted (is_trusted_session=False) — "
                           "run util/auth.sh and complete the full 2FA + trust flow.")
        log(f"[auth] FATAL: untrusted session — {e}")
        _record_session_dead("startup", e)
        sys.exit(1)
    except (PyiCloudFailedLoginException, PyiCloudAuthRequiredException) as e:
        send_alert_deduped("iCloud session needs re-auth (run util/auth.sh).")
        log(f"[auth] FATAL: session dead — {e}")
        _record_session_dead("startup", e)
        sys.exit(1)
    except Exception as e:
        if _is_transient(e):
            log(f"[auth] transient error constructing session — {e}; exiting for restart")
            sys.exit(1)  # launchd will retry after ThrottleInterval
        send_alert_deduped(f"Unexpected daemon startup failure — {type(e).__name__}: {e}")
        log(f"[auth] FATAL: unexpected — {e}")
        sys.exit(1)

    # Count every /accountLogin so we can see whether the session stays warm
    # (target: a handful/day) rather than renewing on many cycles.
    afm_pipeline.install_accountlogin_logging(api, log=log)

    check_session_expiry(api)

    # Retention sweep at startup, then once every 24h from the loop below.
    afm_pipeline.cleanup_old_csvs(log=log)
    last_cleanup = time.time()
    CLEANUP_EVERY_SECS = 24 * 3600

    # ── collection loop ───────────────────────────────────────────────────────
    consecutive_transient = 0
    while _running:
        cycle_start = time.time()
        try:
            n = afm_pipeline.run_pipeline(api, log=log)
            log(f"[cycle] OK — {n} device(s) collected  "
                f"[/accountLogin: {afm_pipeline.accountlogin_count_since(3600)} in last 60min, "
                f"{afm_pipeline.accountlogin_total()} since start]")
            consecutive_transient = 0
            clear_dead_alert_cooldown()  # healthy again: allow future dead-alerts
            _clear_session_hold("healthy cycle")
            check_session_expiry(api)
            if time.time() - last_cleanup >= CLEANUP_EVERY_SECS:
                afm_pipeline.cleanup_old_csvs(log=log)
                last_cleanup = time.time()
            sleep_for = INTERVAL_SECS

        except (PyiCloud2FARequiredException, PyiCloudFailedLoginException) as e:
            # Apple rejected the session mid-loop. Before accepting that only
            # auth.sh can fix it, replay the trust endpoint: it answers 204 with a
            # fresh credential set even while /accountLogin is returning 421.
            log(f"[cycle] session rejected — {type(e).__name__}: {e}")
            if afm_trust_repair.attempt_repair(api, log=log):
                log("[cycle] session repaired in place — continuing")
                clear_dead_alert_cooldown()
                _clear_session_hold("trust replay repaired the session")
                consecutive_transient = 0
                sleep_for = TRANSIENT_RETRY_SECS
            else:
                send_alert_deduped("iCloud session needs re-auth (run util/auth.sh).")
                log(f"[cycle] FATAL: session dead — {type(e).__name__}: {e}")
                _record_session_dead("cycle", e)
                sys.exit(1)

        except PyiCloudAuthRequiredException as e:
            # Raw 450 that pyicloud's internal reauth could not self-heal. In a warm
            # daemon this is rare. Force a token refresh once; if THAT fails hard,
            # treat as dead on the next iteration. Otherwise retry soon.
            log(f"[cycle] 450 not self-healed — forcing token refresh: {e}")
            try:
                api.authenticate(force_refresh=True)
                log("[cycle] force_refresh OK — will retry shortly")
            except (PyiCloud2FARequiredException, PyiCloudFailedLoginException) as e2:
                if afm_trust_repair.attempt_repair(api, log=log):
                    log("[cycle] session repaired after force_refresh — continuing")
                    clear_dead_alert_cooldown()
                    _clear_session_hold("trust replay repaired the session")
                else:
                    send_alert_deduped("iCloud session needs re-auth (run util/auth.sh).")
                    log(f"[cycle] FATAL after force_refresh — {type(e2).__name__}: {e2}")
                    _record_session_dead("force_refresh", e2)
                    sys.exit(1)
            except Exception as e2:
                log(f"[cycle] force_refresh failed transiently: {e2}")
            consecutive_transient += 1
            sleep_for = TRANSIENT_RETRY_SECS

        except Exception as e:
            if _is_transient(e):
                consecutive_transient += 1
                log(f"[cycle] transient iCloud error ({consecutive_transient}x): "
                    f"{type(e).__name__}: {e}")
                sleep_for = TRANSIENT_RETRY_SECS
            else:
                # Data-plane hiccup (BigQuery, pandas, alerts). Do NOT die — the
                # session is fine. Log, alert (deduped), continue next interval.
                log(f"[cycle] non-auth error: {type(e).__name__}: {e}")
                send_alert_deduped(f"AFM daemon pipeline error — {type(e).__name__}: {e}")
                sleep_for = INTERVAL_SECS

        # Sleep the remaining interval in short slices so SIGTERM is responsive.
        elapsed = time.time() - cycle_start
        remaining = max(0, sleep_for - elapsed)
        log(f"[cycle] sleeping {remaining:.0f}s (cycle took {elapsed:.0f}s)")
        slept = 0.0
        while _running and slept < remaining:
            time.sleep(min(2.0, remaining - slept))
            slept += 2.0

    log("[daemon] exited cleanly")


if __name__ == "__main__":
    main()
