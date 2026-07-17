#!/usr/bin/env python3

import json
import csv
import os
import datetime
import logging
import pandas as pd
import lib_bq
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

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

import sys
import platform

# Prevent overlapping runs: if a previous instance is still running, exit
# immediately rather than racing it on Apple auth (which can trigger 421s /
# rate limiting). The lock is released automatically when this process exits.
import fcntl
_lockfile = open('/tmp/afm_live.lock', 'w')
try:
    fcntl.flock(_lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("Skipped (still running)")
    sys.exit(0)

# Pushcut failure alert (does not depend on lib_bq_changealerts)
PUSHCUT_API_KEY = os.environ.get("PUSHCUT_API_KEY", "QFNjvttld5Fem3eor-5pd")

# Progressive backoff after consecutive auth failures to avoid hammering Apple's
# /accountLogin endpoint. _CronPyiCloudService never calls SRP so the aggressive
# rate-limit concern doesn't apply; keep waits short so transient failures resolve quickly.
# 1 failure → retry immediately, 2 → 1 min, 3 → 2 min, 4+ → 5 min
AUTH_BACKOFF_FILE = os.path.expanduser("~/.pyicloud-auth-backoff")
_AUTH_BACKOFF_SECS = [0, 0, 60, 120, 300]  # indexed by consecutive failure count (capped at 4)

# Separate dedup for transient (5xx/429) alerts — independent of the auth backoff counter.
# Using _fail_count == 0 as a guard is wrong: 5xx never writes the backoff file so the
# counter never increments, making the guard vacuously true on every tick. A separate
# timestamp file gives reliable 30-minute dedup regardless of auth failure history.
_TRANSIENT_ALERT_FILE = os.path.expanduser("~/.pyicloud-transient-last")
_TRANSIENT_ALERT_COOLDOWN_SECS = 1800  # at most one transient alert per 30 min

# Consecutive transient-exit counter and hold-off.
# When _refresh_client_with_reauth() calls /accountLogin (success) but FindMy
# immediately returns 450 again, the run exits via PyiCloudAuthRequiredException.
# Each such "post-renewal 450" consumes one dsWebAuthToken presentation slot on
# Apple's anti-abuse system without producing a working session. After 2 consecutive
# exits of this type, apply a 20-minute hold-off so the MME session token (FindMy's
# actual credential, TTL ~10-15 min) has time to propagate on Apple's CDN before
# the next /accountLogin is attempted.
_TRANSIENT_EXIT_CTR_FILE = os.path.expanduser("~/.pyicloud-transient-ctr")
_TRANSIENT_EXIT_THRESHOLD = 2    # consecutive transient exits before hold-off fires
_TRANSIENT_HOLD_SECS = 1200      # 20 min: just over one MME session TTL (~15 min)

# /accountLogin holdoff: Apple FindMy always returns 450 on the first call of a fresh cron
# process because _server_ctx is None (never persisted to disk). This cold-start 450 is
# NOT a cookie expiry — the webauth cookies written by the previous run are still valid
# (Apple's webauth cookie TTL is ~30 days). Calling /accountLogin every cron tick burns
# dsWebAuthToken presentation slots; Apple revokes the token after ~66 calls (~6 hours).
# Fix: if /accountLogin succeeded within the last 8 minutes, skip it and let the disk
# cookies carry the retry. The first FindMy retry (inside _refresh_client_with_reauth)
# will succeed because the on-disk cookies are fresh.
_ACCOUNTLOGIN_HOLDOFF_FILE = os.path.expanduser("~/.pyicloud-accountlogin-holdoff")
_ACCOUNTLOGIN_HOLDOFF_SECS = 8 * 60  # 8 min: covers one 5-min cron interval plus slack


def _should_send_transient_alert() -> bool:
    """True if no transient alert was sent in the last 30 minutes."""
    try:
        with open(_TRANSIENT_ALERT_FILE) as _f:
            return time.time() - float(_f.read().strip()) > _TRANSIENT_ALERT_COOLDOWN_SECS
    except Exception:
        return True


def _record_transient_alert() -> None:
    try:
        with open(_TRANSIENT_ALERT_FILE, 'w') as _f:
            _f.write(str(time.time()))
    except Exception:
        pass


def send_failure_alert(error):
    """Notify via Pushcut when the script fails (safe to call even if other imports fail)."""
    try:
        requests.post(
            f"https://api.pushcut.io/{PUSHCUT_API_KEY}/notifications/AFM%20health",
            json={"title": "AFM stat", "text": str(error)[:500]},
            timeout=10,
        )
    except Exception:
        pass


def _fmt_exc(e):
    """Return a clear error string, unwrapping internal cron-context wrappers.

    _CronAuthRequired is an internal marker whose str() is already formatted as
    'RealExceptionType: message'; strip the wrapper and show that directly so
    the alert reads 'PyiCloudFailedLoginException: ...' not '_CronAuthRequired: ...'.

    For other exceptions, prepend the type name. If e.code is set and the code
    isn't already in str(e) (PyiCloudAPIResponseException always appends it), add it.
    """
    if isinstance(e, (_CronAuthRequired, _CronNetworkError)):
        msg = str(e).strip()
        return msg if msg else type(e).__name__
    typ = type(e).__name__
    msg = str(e).strip()
    # Append code only for non-PyiCloudAPIResponseException types; that class already
    # includes the code in its str() via its own __init__ (e.g. "reason (421)").
    code = getattr(e, 'code', None)
    if code is not None and not isinstance(e, PyiCloudAPIResponseException):
        typ += f"({code})"
    return f"{typ}: {msg}" if msg else typ


# Proactive re-auth warning: alert a few days BEFORE the session's trust cookie
# expires, so the user can run util/auth.sh before a hard failure. The primary
# fix re-mints the 30-day webauth cookies every run, but the trust token
# (~90-day HSA-TRUST cookie) is the true re-auth driver — even a healthy running
# cron cannot renew it past Apple's ceiling. Warning on its expiry is the golden
# "re-auth soon" signal.
EXPIRY_WARN_DAYS = 5
EXPIRY_WARN_FILE = os.path.expanduser("~/.pyicloud-expiry-warned")
# Core cookies that gate the session, shortest-lived first. Alert on whichever
# expires soonest so a stalled cron (aging webauth token) also triggers a warning.
_SESSION_COOKIE_NAMES = ("X-APPLE-WEBAUTH-HSA-TRUST", "X-APPLE-WEBAUTH-TOKEN")


def check_session_expiry(api):
    """Send a Pushcut alert if a core session cookie is near expiry. Never raises."""
    try:
        soonest_days = None
        soonest_name = None
        for cookie in api.session.cookies:
            if cookie.name in _SESSION_COOKIE_NAMES and cookie.expires:
                days = (cookie.expires - time.time()) / 86400.0
                if soonest_days is None or days < soonest_days:
                    soonest_days, soonest_name = days, cookie.name
        if soonest_days is None or soonest_days > EXPIRY_WARN_DAYS:
            return  # healthy — nothing to warn about

        # Dedupe: warn at most once per calendar day to avoid 5-min spam.
        today = datetime.date.today().isoformat()
        try:
            if open(EXPIRY_WARN_FILE).read().strip() == today:
                return
        except Exception:
            pass

        msg = (f"iCloud session expires in {soonest_days:.1f} day(s) "
               f"({soonest_name}) — run util/auth.sh soon to avoid an outage.")
        print(f"[auth] {msg}")
        send_failure_alert(msg)
        try:
            with open(EXPIRY_WARN_FILE, 'w') as f:
                f.write(today)
        except Exception:
            pass
    except Exception:
        pass  # a warning check must never break the pipeline


class _CronAuthRequired(Exception):
    """Apple challenged the session in a non-interactive cron context.
    Raised instead of PyiCloudAuthRequiredException to avoid depending on
    the timlaing fork's constructor signature (which requires a response object)."""
    pass


class _SlowAccountLoginTransient(Exception):
    """Raised when /accountLogin succeeds but took >60s (Apple backend under load).
    Bypasses _authenticate()'s _CronAuthRequired wrapper so it reaches the
    mid-pipeline transient handler rather than the auth-backoff handler."""
    pass


class _CronNetworkError(Exception):
    """Network-level failure (ConnectionError/Timeout) during cron auth.
    Distinguishes transient connectivity failures from actual session expiry —
    a _CronNetworkError must NOT increment the auth backoff counter."""
    pass


class _CronPyiCloudService(PyiCloudService):
    """PyiCloudService that never falls back to SRP in a cron context.

    Two overrides:

    1. authenticate() passes force_refresh through to super() unchanged.
       - Startup (force_refresh=False): calls _validate_token() (POST /validate),
         a cheap keep-alive that confirms the 30-day X-APPLE-WEBAUTH-TOKEN is live
         without consuming any FindMy-layer credentials.
         Falls back to _authenticate_with_token() only when /validate itself fails.
       - 450 retry (force_refresh=True, called by _refresh_client_with_reauth):
         skips /validate and calls _authenticate_with_token() (POST /accountLogin)
         to re-mint the full X-APPLE-WEBAUTH-* cookie set that FindMy rejected.
       This keeps /accountLogin calls proportional to actual cookie-expiry events
       rather than once per cron tick.

       Why NOT always force_refresh=True: The dsWebAuthToken is a server-side
       credential with a TTL of ~2-4 hours. /accountLogin presents it to Apple
       on every call. Calling /accountLogin every 5 minutes (12×/hr, 288/day)
       does not exhaust a "call quota" — Apple's "Invalid authentication token"
       means the dsWebAuthToken has EXPIRED server-side, not been overused.
       However, calling /accountLogin every tick was empirically causing hard
       "Invalid authentication token" failures at ~6h, likely because aggressive
       repeated cookie exchange triggered Apple's anti-abuse detection. The
       pass-through approach (only on 450) is correct.

       The recurring 450→/accountLogin recovery pattern (229-454s runs) is caused
       by pyicloud NOT flushing updated cookies to disk after a mid-run /accountLogin
       recovery. The next process starts with the stale pre-recovery cookies and
       immediately hits 450 again. This is addressed by the _accountlogin_patch()
       instrumentation below, which forces a cookie save after each recovery.

    2. _authenticate() wraps known auth exceptions in _CronAuthRequired.
       Prevents the base _authenticate() from falling back to SRP.
       PyiCloudAcceptTermsException and unexpected exceptions propagate naturally
       so the startup handler can route them to their specific recovery paths.
    """
    def authenticate(self, force_refresh: bool = False, service=None) -> None:
        # Pass force_refresh through: startup uses /validate (cheap), 450 retries
        # use /accountLogin (re-mints cookies). _authenticate() below blocks SRP.
        super().authenticate(force_refresh=force_refresh, service=service)

    def _authenticate(self) -> None:
        try:
            self._authenticate_with_token()
        except (PyiCloudFailedLoginException, PyiCloudAuthRequiredException,
                PyiCloud2FARequiredException) as exc:
            # ConnectionError/Timeout are caught by PyiCloudSession._request() and
            # converted to PyiCloudAPIResponseException(code=None) → PyiCloudFailedLoginException.
            # Walk the cause chain: if we find a PyiCloudAPIResponseException with no
            # integer HTTP code, the root failure was a network blip, not a credential or
            # session problem. Raise _CronNetworkError so the startup handler routes it
            # to the transient path (no backoff counter increment).
            _cause = exc
            while _cause is not None:
                if isinstance(_cause, PyiCloudAPIResponseException) and not isinstance(_cause.code, int):
                    _exc_msg = exc.args[0] if exc.args else str(exc)
                    raise _CronNetworkError(
                        f"Network error during iCloud auth (no HTTP response): "
                        f"{type(exc).__name__}: {_exc_msg}"
                    ) from exc
                _cause = getattr(_cause, '__cause__', None) or getattr(_cause, '__context__', None)
            # Use exc.args[0] (the message string) rather than str(exc): pyicloud raises
            # PyiCloudFailedLoginException(msg, response) with two positional args, so
            # str(exc) renders as a Python tuple repr "('msg', <Response>)" in alerts.
            _exc_msg = exc.args[0] if exc.args else str(exc)
            raise _CronAuthRequired(f"{type(exc).__name__}: {_exc_msg}") from exc
        # PyiCloudAcceptTermsException and unexpected exceptions propagate naturally
        # so the startup handler can detect and route them correctly.


print("=== RUNTIME ENVIRONMENT ===")
print("Python executable:", sys.executable)
print("Python version:", sys.version.replace("\n", " "))
print("Virtual env:", os.environ.get("VIRTUAL_ENV", "NONE"))
print("Platform:", platform.platform())
print("===========================")

# logging.basicConfig(level=logging.DEBUG)

# Progressive backoff: check how many consecutive 421s have occurred and
# whether enough time has passed since the last one before hitting Apple again.
_backoff_state = {"count": 0, "last_ts": 0}
if os.path.exists(AUTH_BACKOFF_FILE):
    try:
        with open(AUTH_BACKOFF_FILE) as _bf:
            _backoff_state = json.load(_bf)
    except Exception:
        # Corrupt or partial write (e.g. mid-write kill). Fail safe: treat as
        # max-count so the throttle is preserved rather than silently reset to zero.
        _backoff_state = {"count": len(_AUTH_BACKOFF_SECS), "last_ts": time.time()}

_fail_count = _backoff_state.get("count", 0)
_last_ts = _backoff_state.get("last_ts", 0)
_backoff_secs = _AUTH_BACKOFF_SECS[min(_fail_count, len(_AUTH_BACKOFF_SECS) - 1)]
_elapsed = time.time() - _last_ts

if _fail_count > 0 and _backoff_secs > 0 and _elapsed < _backoff_secs:
    _wait_min = (_backoff_secs - _elapsed) / 60
    print(f"[auth] Backing off ({_fail_count} consecutive failure(s), {_wait_min:.0f}min remaining) — skipping Apple API call")
    raise SystemExit(0)

# Transient-exit hold-off: when /accountLogin succeeds but FindMy still returns 450
# (post-renewal CDN propagation lag), each such exit burns a dsWebAuthToken presentation
# slot. After _TRANSIENT_EXIT_THRESHOLD consecutive exits of this type, hold off for
# _TRANSIENT_HOLD_SECS so the MME session token has time to propagate before we try again.
_transient_ctr_state = {"count": 0, "hold_until": 0}
if os.path.exists(_TRANSIENT_EXIT_CTR_FILE):
    try:
        with open(_TRANSIENT_EXIT_CTR_FILE) as _tf:
            _transient_ctr_state = json.load(_tf)
    except Exception:
        pass
_transient_exit_count = _transient_ctr_state.get("count", 0)
_transient_hold_until = _transient_ctr_state.get("hold_until", 0)
if time.time() < _transient_hold_until:
    _hold_remaining = (_transient_hold_until - time.time()) / 60
    print(f"[auth] Transient hold-off ({_transient_exit_count} consecutive 450-exit(s), "
          f"{_hold_remaining:.0f}min remaining) — skipping to allow MME token propagation")
    raise SystemExit(0)
elif _transient_hold_until > 0 and _transient_exit_count > 0:
    # Holdoff has expired — reset counter so a slow /accountLogin after the holdoff doesn't
    # immediately chain into another holdoff. Without this, count stays at 2+ and every
    # subsequent slow call triggers another 20-min holdoff, causing multi-hour gaps.
    _transient_exit_count = 0
    try:
        with open(_TRANSIENT_EXIT_CTR_FILE, 'w') as _tf:
            json.dump({"count": 0, "hold_until": 0}, _tf)
        print(f"[auth] Transient hold-off expired — counter reset to 0 (fresh start)")
    except Exception:
        pass

# Save the pre-startup failure count so the mid-pipeline handlers can
# increment it correctly. _fail_count is the count on disk at startup;
# the successful-pipeline handler below may clear the file, but by then
# _pre_startup_fail_count has already been captured for any exception path.
_pre_startup_fail_count = _fail_count

try:
    api = _CronPyiCloudService('joe@joemoser.com',
                               cookie_directory=os.path.expanduser("~/.pyicloud"))
except Exception as _auth_exc:
    if PyiCloudAcceptTermsException and isinstance(_auth_exc, PyiCloudAcceptTermsException):
        msg = f"Apple Terms of Service update required — log in to iCloud.com to accept: {_auth_exc}"
        print(f"[auth] {msg}")
        # TOS is a distinct condition unrelated to auth token expiry. Use count=1 so
        # the next retry happens after the first backoff slot (0s), not after whatever
        # prior auth failures had accumulated. Always alert — TOS is always actionable.
        send_failure_alert(msg)
        try:
            with open(AUTH_BACKOFF_FILE, 'w') as _f:
                json.dump({"count": 1, "last_ts": time.time()}, _f)
        except Exception:
            pass
        raise SystemExit(0)
    # Transient conditions that must NOT increment the auth backoff counter:
    # 1. _CronNetworkError: ConnectionError/Timeout detected in _authenticate() — no HTTP response at all
    # 2. PyiCloudAPIResponseException with code=None: network error that bypassed _authenticate()
    # 3. HTTP 5xx/429: transient Apple server or rate-limit error; auth.sh cannot help
    # In all three cases the session may still be valid — suppress backoff.
    if isinstance(_auth_exc, _CronNetworkError) or (
        isinstance(_auth_exc, PyiCloudAPIResponseException) and (
            not isinstance(_auth_exc.code, int) or  # code=None means no HTTP response
            _auth_exc.code >= 500 or
            _auth_exc.code == 429
        )
    ):
        msg = f"Transient iCloud error at startup (not auth): {_fmt_exc(_auth_exc)}"
        print(f"[startup] {msg}")
        if _should_send_transient_alert():
            send_failure_alert(msg)
            _record_transient_alert()
        raise SystemExit(0)
    if isinstance(_auth_exc, (PyiCloudFailedLoginException, PyiCloudAuthRequiredException, _CronAuthRequired, PyiCloudAPIResponseException)):
        _cause_exc = getattr(_auth_exc, '__cause__', None)
        if isinstance(_auth_exc, _CronAuthRequired) and isinstance(_cause_exc, PyiCloud2FARequiredException):
            # auth.sh completed (SRP + 2FA + trust_session() reported success) but Apple's backend
            # returned is_trusted_session=False on the next /accountLogin — trust propagation race.
            # The user needs to run auth.sh AGAIN and complete the full trust flow.
            msg = f"Session untrusted — auth.sh ran but trust didn't hold (run util/auth.sh again, complete full 2FA + trust flow): {_fmt_exc(_auth_exc)}"
        else:
            msg = f"iCloud session needs re-auth (run util/auth.sh): {_fmt_exc(_auth_exc)}"
        print(f"[auth] {msg}")
        _new_count = _fail_count + 1
        _next_wait = _AUTH_BACKOFF_SECS[min(_new_count, len(_AUTH_BACKOFF_SECS) - 1)] // 60
        print(f"[auth] Consecutive failures: {_new_count} — next attempt in {_next_wait}min")
        # Alert on the first failure (so the user knows) and again when the max backoff
        # level is reached (so silence doesn't stretch into hours unnoticed).
        _max_count = len(_AUTH_BACKOFF_SECS) - 1
        if _new_count == 1 or _new_count == _max_count:
            send_failure_alert(msg)
        try:
            with open(AUTH_BACKOFF_FILE, 'w') as _f:
                json.dump({"count": _new_count, "last_ts": time.time()}, _f)
        except Exception:
            pass
        raise SystemExit(0)  # exit cleanly — no retry, no re-raise into Apple
    send_failure_alert(f"Unexpected startup failure — {_fmt_exc(_auth_exc)}")
    raise

# /validate succeeded: the X-APPLE-WEBAUTH-TOKEN cookie (30-day) is live.
# Do NOT clear the backoff file here. /validate can return 200 even when the
# dsWebAuthToken is expired — which means /accountLogin (called on a subsequent
# FindMy 450) would still return 421. Clearing here causes an infinite loop:
#   run N: /validate OK → backoff cleared → 450 → /accountLogin 421 → backoff count=1
#   run N+1: backoff[1]=0s wait → /validate OK → backoff cleared → count=1 again → ...
# Backoff is cleared below, only after FindMy data is actually collected,
# which confirms that the full auth chain (including dsWebAuthToken) is working.

# Proactively warn if the trust cookie is nearing expiry.
check_session_expiry(api)

# Log cookie inventory so future session degradation has a baseline to compare against.
# This runs every tick (after /validate), giving visibility into cookie lifetimes.
print("[auth] /validate succeeded — session cookies at startup:")
_now_ts = time.time()
_cookie_names_seen = []
try:
    for _c in sorted(api.session.cookies, key=lambda c: getattr(c, 'name', str(c))):
        _cname = getattr(_c, 'name', str(_c))
        _cookie_names_seen.append(_cname)
        _exp = getattr(_c, 'expires', None)
        if _exp:
            _days_left = (_exp - _now_ts) / 86400
            _exp_date = datetime.datetime.fromtimestamp(_exp).strftime('%Y-%m-%d')
            print(f"[auth]   {_cname}: expires {_exp_date} ({_days_left:.1f} days)")
        else:
            print(f"[auth]   {_cname}: no explicit expiry (session cookie)")
    if not _cookie_names_seen:
        print("[auth]   (no cookies found in session jar — may indicate a persistence problem)")
except Exception as _ci_err:
    print(f"[auth]   (cookie inventory failed: {_ci_err})")

# Log MME session token (the credential FindMy actually validates, TTL ~10-15 min).
# This is stored in the .session JSON file as 'X-Apple-Session-Token', separate from
# the cookie jar. Logging a prefix lets us confirm whether consecutive runs are reusing
# the same token (stale) or a fresh one after /accountLogin.
try:
    _session_data = {}
    _session_file = None
    _pyicloud_dir = os.path.expanduser("~/.pyicloud")
    for _sf in os.listdir(_pyicloud_dir) if os.path.isdir(_pyicloud_dir) else []:
        if _sf.endswith(".session"):
            _session_file = os.path.join(_pyicloud_dir, _sf)
            break
    if _session_file and os.path.exists(_session_file):
        with open(_session_file) as _sf_f:
            _session_data = json.load(_sf_f)
    _mme_token = _session_data.get("X-Apple-Session-Token") or _session_data.get("session_token")
    if _mme_token:
        _mme_prefix = _mme_token[:16] if len(_mme_token) >= 16 else _mme_token
        print(f"[auth] MME session token (prefix): {_mme_prefix}... (len={len(_mme_token)})")
    else:
        print(f"[auth] MME session token: NOT FOUND in session JSON — FindMy will likely 450 immediately")
        print(f"[auth]   Session JSON keys: {list(_session_data.keys())}")
except Exception as _mme_err:
    print(f"[auth] MME session token: could not read: {_mme_err}")

# Log holdoff state so we know whether this run will skip /accountLogin.
try:
    if os.path.exists(_ACCOUNTLOGIN_HOLDOFF_FILE):
        _hf_age = time.time() - float(open(_ACCOUNTLOGIN_HOLDOFF_FILE).read().strip())
        if _hf_age < _ACCOUNTLOGIN_HOLDOFF_SECS:
            print(f"[auth] /accountLogin holdoff: ACTIVE — last OK {_hf_age:.0f}s ago "
                  f"(holdoff={_ACCOUNTLOGIN_HOLDOFF_SECS}s) — will skip /accountLogin if 450 fires")
        else:
            print(f"[auth] /accountLogin holdoff: expired ({_hf_age:.0f}s ago, holdoff={_ACCOUNTLOGIN_HOLDOFF_SECS}s)")
    else:
        print("[auth] /accountLogin holdoff: not present (first run or cleared by auth.sh)")
except Exception as _hf_log_err:
    print(f"[auth] /accountLogin holdoff: could not read: {_hf_log_err}")

# --- /accountLogin call instrumentation ---
# PURPOSE: Track every /accountLogin call so we can:
#   1. Confirm whether cookies are persisted to disk after each mid-run recovery
#      (the most likely cause of back-to-back 450→/accountLogin recovery runs)
#   2. Measure calls per run and per hour to correlate with "Invalid authentication token"
#      (which occurs when the dsWebAuthToken's server-side TTL of ~2-4h is exhausted)
# This monkey-patches the bound method on the instance only; no class-level side effects.
_ACCOUNTLOGIN_LOG = os.path.expanduser("~/.pyicloud-accountlogin-log")
_accountlogin_calls: list = []  # list of ("ok"|"fail", timestamp_float)

# Report recent /accountLogin frequency from the persistent log before patching.
try:
    if os.path.exists(_ACCOUNTLOGIN_LOG):
        with open(_ACCOUNTLOGIN_LOG) as _alf:
            _al_lines = [l for l in _alf if l.strip()]
        _one_hour_ago = _now_ts - 3600
        _recent = [l for l in _al_lines if float(l.split()[0]) > _one_hour_ago]
        print(f"[auth] /accountLogin history: {len(_recent)} call(s) in last 60min "
              f"(log has {len(_al_lines)} total entries)")
except Exception as _al_read_err:
    print(f"[auth] /accountLogin history: could not read log: {_al_read_err}")

# Capture the original bound method so the wrapper can call it without recursion.
_orig_authenticate_with_token = api._authenticate_with_token

def _instrumented_authenticate_with_token():
    _call_num = len(_accountlogin_calls) + 1
    _call_ts = time.time()
    _call_time = datetime.datetime.now().strftime('%H:%M:%S')

    # HOLDOFF: Apple FindMy returns 450 on the first call of every fresh cron process
    # because _server_ctx is None (cold-start). This is NOT a cookie-expiry 450 — the
    # webauth cookies written by the last run are still valid. Skip /accountLogin and let
    # _refresh_client_with_reauth() retry FindMy with the disk cookies, which will succeed.
    try:
        if os.path.exists(_ACCOUNTLOGIN_HOLDOFF_FILE):
            _hf_age = time.time() - float(open(_ACCOUNTLOGIN_HOLDOFF_FILE).read().strip())
            if _hf_age < _ACCOUNTLOGIN_HOLDOFF_SECS:
                print(f"[auth] /accountLogin SKIPPED (holdoff active, last OK {_hf_age:.0f}s ago) — "
                      f"using disk cookies for FindMy retry")
                _accountlogin_calls.append(("skipped", _call_ts))
                return  # cookies already loaded from disk at startup; retry proceeds with them
    except Exception as _hf_err:
        print(f"[auth] /accountLogin holdoff check failed ({_hf_err}) — proceeding with call")

    print(f"[auth] /accountLogin call #{_call_num} at {_call_time} — presenting dsWebAuthToken to Apple")
    # Log cookie file state BEFORE the call to confirm what's on disk.
    _cookie_dir = os.path.expanduser("~/.pyicloud")
    try:
        _cf_before = {
            _cf: (os.path.getmtime(os.path.join(_cookie_dir, _cf)),
                  os.path.getsize(os.path.join(_cookie_dir, _cf)))
            for _cf in os.listdir(_cookie_dir)
        } if os.path.isdir(_cookie_dir) else {}
    except Exception:
        _cf_before = {}

    try:
        _orig_authenticate_with_token()
        _elapsed = time.time() - _call_ts
        _accountlogin_calls.append(("ok", _call_ts))
        print(f"[auth] /accountLogin #{_call_num} succeeded in {_elapsed:.1f}s")

        # CRITICAL FIX: Force-persist updated cookies to disk so the next cron
        # process starts with fresh cookies rather than the pre-recovery stale set.
        # Without this, every run that had a 450 mid-loop will start the NEXT run
        # with the same stale cookies, causing an immediate 450 again — the
        # "back-to-back long run" pattern seen in the logs.
        _cookies = api.session.cookies
        if hasattr(_cookies, 'save'):
            try:
                _cookies.save(ignore_discard=True)
                print(f"[auth] /accountLogin #{_call_num}: cookie jar saved to disk ✓")
            except Exception as _save_err:
                print(f"[auth] /accountLogin #{_call_num}: WARNING — cookie jar save failed: {_save_err}")
                print(f"[auth]   Cookie jar type: {type(_cookies).__name__}")
        else:
            print(f"[auth] /accountLogin #{_call_num}: WARNING — cookie jar type "
                  f"{type(_cookies).__name__} has no save() method — cookies may NOT persist to disk")

        # Write holdoff timestamp so the next cron run can skip /accountLogin.
        # The holdoff is cleared on transient-450 exit so a genuine cookie expiry
        # will force a real /accountLogin on the following run.
        try:
            with open(_ACCOUNTLOGIN_HOLDOFF_FILE, 'w') as _hfw:
                _hfw.write(str(time.time()))
        except Exception as _hfw_err:
            print(f"[auth] /accountLogin #{_call_num}: WARNING — could not write holdoff file: {_hfw_err}")

        # A slow /accountLogin (>60s) is a reliable predictor of FindMy 450 failure:
        # Apple's backend is under load, and cookies issued under load don't propagate
        # reliably regardless of how long we wait. Skip FindMy entirely and exit as
        # transient so the hold-off system gives Apple time to recover.
        # Fast calls (<60s) sleep the usual floor (30s); we still attempt FindMy.
        if _elapsed > 60:
            print(f"[auth] /accountLogin #{_call_num}: call took {_elapsed:.1f}s (>60s) — "
                  f"Apple backend under load; skipping FindMy and exiting as transient")
            _accountlogin_calls.append(("slow-skip", _call_ts))
            raise _SlowAccountLoginTransient(
                f"/accountLogin succeeded but took {_elapsed:.0f}s — "
                f"Apple backend under load; skipping FindMy to let Apple recover"
            )
        _prop_sleep = max(30, _elapsed * 0.5)
        print(f"[auth] /accountLogin #{_call_num}: sleeping {_prop_sleep:.0f}s for CDN propagation "
              f"(call took {_elapsed:.1f}s)")
        time.sleep(_prop_sleep)
        print(f"[auth] /accountLogin #{_call_num}: propagation sleep done")

        # Log the new MME session token so we can verify it changed after /accountLogin.
        try:
            _sf_path = None
            _pyi_dir = os.path.expanduser("~/.pyicloud")
            for _fn in os.listdir(_pyi_dir) if os.path.isdir(_pyi_dir) else []:
                if _fn.endswith(".session"):
                    _sf_path = os.path.join(_pyi_dir, _fn)
                    break
            if _sf_path and os.path.exists(_sf_path):
                with open(_sf_path) as _sf_f2:
                    _new_sd = json.load(_sf_f2)
                _new_mme = _new_sd.get("X-Apple-Session-Token") or _new_sd.get("session_token")
                if _new_mme:
                    print(f"[auth] /accountLogin #{_call_num}: new MME token prefix: {_new_mme[:16]}... (len={len(_new_mme)})")
                else:
                    print(f"[auth] /accountLogin #{_call_num}: WARNING — MME token not found in session JSON after /accountLogin")
        except Exception as _mme_post_err:
            print(f"[auth] /accountLogin #{_call_num}: could not read post-login MME token: {_mme_post_err}")

        # Log cookie file state AFTER the call to confirm the write happened.
        try:
            _cf_after = {
                _cf: (os.path.getmtime(os.path.join(_cookie_dir, _cf)),
                      os.path.getsize(os.path.join(_cookie_dir, _cf)))
                for _cf in os.listdir(_cookie_dir)
            } if os.path.isdir(_cookie_dir) else {}
            _changed = [_cf for _cf in _cf_after
                        if _cf not in _cf_before or _cf_after[_cf] != _cf_before[_cf]]
            _new_files = [_cf for _cf in _cf_after if _cf not in _cf_before]
            if _changed:
                for _cf in _changed:
                    _mt = datetime.datetime.fromtimestamp(_cf_after[_cf][0]).strftime('%H:%M:%S')
                    print(f"[auth]   ~/.pyicloud/{_cf}: updated mtime={_mt}, size={_cf_after[_cf][1]}B")
            else:
                print(f"[auth]   ~/.pyicloud: NO files changed after /accountLogin "
                      f"(cookie persistence may be broken)")
        except Exception as _cf_err:
            print(f"[auth]   cookie file check failed: {_cf_err}")

        # Append to persistent log for cross-run rate tracking (atomic-safe append).
        try:
            with open(_ACCOUNTLOGIN_LOG, 'a') as _alf:
                _alf.write(f"{_call_ts:.0f} ok\n")
            # Trim: keep only last 48h to prevent unbounded growth.
            # Use write-to-temp + rename for atomicity.
            _cutoff = time.time() - 172800
            _log_tmp = _ACCOUNTLOGIN_LOG + ".tmp"
            try:
                with open(_ACCOUNTLOGIN_LOG) as _alf:
                    _kept = [l for l in _alf if l.strip() and float(l.split()[0]) > _cutoff]
                with open(_log_tmp, 'w') as _alf:
                    _alf.writelines(_kept)
                os.replace(_log_tmp, _ACCOUNTLOGIN_LOG)
            except Exception:
                pass
        except Exception as _le:
            print(f"[auth] /accountLogin #{_call_num}: WARNING — could not write to log: {_le}")
    except Exception as _ae:
        _elapsed = time.time() - _call_ts
        _accountlogin_calls.append(("fail", _call_ts))
        print(f"[auth] /accountLogin #{_call_num} FAILED after {_elapsed:.1f}s: {_fmt_exc(_ae)}")
        try:
            with open(_ACCOUNTLOGIN_LOG, 'a') as _alf:
                _alf.write(f"{_call_ts:.0f} fail\n")
        except Exception:
            pass
        raise

api._authenticate_with_token = _instrumented_authenticate_with_token
# --- end /accountLogin instrumentation ---

j='AaHxu7OfOiBiW9RPAzTY7vwSj1JBHWOFhwRH6gExNXh5rVayf3TjFGKoJy2m+45YW5UJNAK/PJFIJA=='
t='AUGMI3r423xBlRKk7ikSZZP58FP99hjIG792YKzAfrECqtaVzcq1mKynHyqI5jlszlCTSyD7bce1lIy8r7dwAyWwmUyvLCYYE8/1YSPo3382+68OIz/iits7VydiuwHl83OjxK7yoE8kE4HHzm5shZOm7/i+C69OHrRNryBPhOkff9AjNm4h9A=='
stop='AXiaZlpiPpcsQR3vWRRhoMGFrCGUO0Tx68x0IOe5mml3QNQzE9DxZSPi8DjqtV36aPAARY0Y/Ri0xw=='

dt = datetime.datetime.now().strftime("%Y%m%d_%H%M")
dt_full = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

path="/Users/joemoser/Dropbox/Source/afm/findmypy/"
filename=path+"afm/afm_"+dt+".csv"
filename_all=path+"afm/afm_all.csv"
file_exists=os.path.exists(filename)
file_all_exists=os.path.exists(filename_all)

# keys = ['deviceID', 'deviceName'] | api.devices[j].location().keys() | api.devices[j].status().keys() 

column_order = ['date_time', 'deviceID', 'deviceName', 'deviceDisplayName', 'name', 'deviceStatus', 'batteryLevel', 'positionType', 'timeStamp', 'latitude', 'longitude', 'horizontalAccuracy', 'verticalAccuracy', 'locationFinished', 'isOld', 'isInaccurate', 'altitude', 'floorLevel', 'secureLocationTs', 'locationType', 'secureLocation', 'locationMode', 'addresses', 'prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou', 'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel', 'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake']

SKIP_MODELS = ['FifthGen-white','SecondGen-white','iphoneSE-1-1-0','AirPods_8194','FourthGen','MacBookPro15_1-spacegray','TenthGen-2-3-0','MacBookPro15_2-spacegray','FirstGen','Mac15_6-spaceblack','Mac14_3','MacPro3_1','MacBookPro5_5']  # Add models to skip here

print("Script started at: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
start_time = time.time()

try:
    with open(filename, "a", newline="") as f:
        w = csv.DictWriter(f, column_order)
        if not file_exists:
            w.writeheader()

        _device_count = 0
        _al_calls_before_loop = len(_accountlogin_calls)
        print(f"[pipeline] Starting device loop at {datetime.datetime.now().strftime('%H:%M:%S')}")

        for dev in api.devices:
            k = dev.data.get("id") or dev.data.get("deviceID") or dev.data.get("udid")

            if dev.data.get('deviceModel') in SKIP_MODELS:
                continue

            _device_count += 1
            _dev_name = str(dev.data.get('name', k or 'unknown'))
            _dev_loop_start = time.time()

            prefix = { 'date_time': dt_full, 'deviceID': k, 'deviceName': dev }

            data1 = dev.status(['prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou', 'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel', 'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake'])

            if data1.get('rawDeviceModel') == 'iPhone17,3':
                data1['deviceModel'] = 'iphone12mini-1-17-0'

            if data1.get('rawDeviceModel') == 'iPhone13,1':
                data1['deviceModel'] = 'i12o'

            if data1.get('rawDeviceModel') == 'iPhone18,2':
                data1['deviceModel'] = 'iPhone16-1-4-0'

            if data1.get('rawDeviceModel') == 'iPhone17,2':
                data1['deviceModel'] = 'i16o'

            loc = dev.location  # <-- no parentheses; 450→/accountLogin recovery fires here if cookies stale

            _dev_elapsed = time.time() - _dev_loop_start
            if _dev_elapsed > 5 or len(_accountlogin_calls) > _al_calls_before_loop:
                # Slow device or /accountLogin was triggered — log it so we know which device
                # caused the 450 and how long the recovery took.
                _al_this_dev = len(_accountlogin_calls) - _al_calls_before_loop
                print(f"[pipeline] Device #{_device_count} ({_dev_name!r}): "
                      f"{_dev_elapsed:.1f}s, /accountLogin calls this device: {_al_this_dev}")
                _al_calls_before_loop = len(_accountlogin_calls)

            if not loc:
                data2 = { 'positionType': 'None' }
            else:
                data2 = loc

            w.writerow(prefix | data1 | data2)

        print(f"[pipeline] Device loop done: {_device_count} device(s) written, "
              f"{len(_accountlogin_calls)} /accountLogin call(s) total")

    print("Data collection from iCloud completed at: {}. Time taken: {:.2f} seconds".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

    # FindMy data collected successfully — the full auth chain (dsWebAuthToken +
    # /accountLogin) is confirmed working. Clear backoff here, not at /validate
    # startup success, so a stale expired token cannot reset the counter and
    # create an infinite loop.
    if _accountlogin_calls:
        _outcomes = [s for s, _ in _accountlogin_calls]
        print(f"[auth] /accountLogin summary: {len(_accountlogin_calls)} call(s) this run — "
              f"outcomes: {_outcomes}")
    else:
        print("[auth] /accountLogin: 0 calls this run (FindMy cookies were still valid)")
    if os.path.exists(AUTH_BACKOFF_FILE):
        os.remove(AUTH_BACKOFF_FILE)
        _fail_count = 0
        print("[auth] FindMy pipeline succeeded — backoff state cleared")
    # Refresh holdoff timestamp if /accountLogin was called and succeeded this run,
    # so the next run's holdoff check reflects when the LAST call actually happened.
    if any(s == "ok" for s, _ in _accountlogin_calls):
        try:
            with open(_ACCOUNTLOGIN_HOLDOFF_FILE, 'w') as _hfw2:
                _hfw2.write(str(time.time()))
        except Exception:
            pass
    # Clear transient exit counter: a successful pipeline confirms /accountLogin + FindMy
    # worked end-to-end, so the CDN propagation issue is resolved. Reset to zero so the
    # hold-off doesn't fire on the next run unnecessarily.
    if _transient_exit_count > 0 and os.path.exists(_TRANSIENT_EXIT_CTR_FILE):
        try:
            os.remove(_TRANSIENT_EXIT_CTR_FILE)
            print(f"[auth] FindMy pipeline succeeded — transient exit counter cleared (was {_transient_exit_count})")
        except Exception:
            pass
    # Reset _pre_startup_fail_count so any mid-pipeline auth exception after this
    # point (e.g. from lib_bq_changealerts) uses the correct base count of 0
    # rather than the stale pre-startup count, which could write an inflated
    # backoff value and delay the next run despite the session being confirmed healthy.
    _pre_startup_fail_count = 0

    df1 = pd.read_csv(filename)

    # Append-only: avoids reading and rewriting the entire history file each run.
    df1.to_csv(filename_all, mode='a', header=not file_all_exists, index=False)

    print("Writing to files completed at: {}. Time taken: {:.2f} seconds".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

    lib_bq.append_to_bigquery(df1, 'afm_latest_live')

    # afm_now_live_mat must complete first — the other two views depend on it.
    lib_bq.materialize_view(view_name='afm_now_live', destination_table='afm_now_live_mat')

    # afm_change_live_vw_mat and afm_now_ch_live_mat are independent of each other;
    # run them in parallel to save one full job's worth of wall time.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(lib_bq.materialize_view, view_name='afm_change_live_vw', destination_table='afm_change_live_vw_mat'): 'afm_change_live_vw',
            pool.submit(lib_bq.materialize_view, view_name='afm_now_ch_live',    destination_table='afm_now_ch_live_mat'):    'afm_now_ch_live',
        }
        for future in as_completed(futures):
            future.result()  # re-raises any exception so the outer handler catches it

    print("Uploading to BigQuery completed at: {}. Time taken: {:.2f} seconds".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

    import lib_bq_changealerts
    import lib_bq_smartalerts

except Exception as e:
    # Transient check runs FIRST, before the auth-exception isinstance, so that
    # _CronNetworkError (which is not a pyicloud type) is caught here rather than
    # falling through to the unexpected-failure else-branch below.
    if isinstance(e, _CronNetworkError) or (
        isinstance(e, PyiCloudAPIResponseException) and (
            not isinstance(e.code, int) or  # code=None = no HTTP response = network error
            e.code >= 500 or
            e.code == 429
        )
    ):
        msg = f"Transient iCloud error mid-pipeline (not auth): {_fmt_exc(e)}"
        print(f"[pipeline] {msg}")
        if _should_send_transient_alert():
            send_failure_alert(msg)
            _record_transient_alert()
        raise SystemExit(0)
    # Raw PyiCloudAuthRequiredException mid-pipeline = FindMy 450 → _refresh_client_with_reauth()
    # triggered a re-auth attempt that raised, aborting this run. Every logged instance
    # self-heals on the very next cron tick — the session itself was never dead, only the
    # FindMy 450 was transient. Treat as transient: no backoff increment, no "run auth.sh".
    # Running auth.sh for a 450 is actively harmful (unnecessary SRP triggers rate limiting).
    if isinstance(e, _SlowAccountLoginTransient):
        msg = f"Slow /accountLogin (Apple backend load) — skipped FindMy, treating as transient: {_fmt_exc(e)}"
        print(f"[pipeline] {msg}")
        _new_transient_count = _transient_exit_count + 1
        _new_hold_until = 0
        if _new_transient_count >= _TRANSIENT_EXIT_THRESHOLD:
            _new_hold_until = time.time() + _TRANSIENT_HOLD_SECS
            _hold_min = _TRANSIENT_HOLD_SECS // 60
            print(f"[pipeline] slow /accountLogin: {_new_transient_count} consecutive exit(s) — "
                  f"hold-off active for {_hold_min}min")
        else:
            print(f"[pipeline] slow /accountLogin: {_new_transient_count}/{_TRANSIENT_EXIT_THRESHOLD} "
                  f"consecutive exit(s) — no hold-off yet")
        try:
            with open(_TRANSIENT_EXIT_CTR_FILE, 'w') as _tcf:
                json.dump({"count": _new_transient_count, "hold_until": _new_hold_until}, _tcf)
        except Exception as _tce:
            print(f"[pipeline] slow /accountLogin: WARNING — could not write transient counter: {_tce}")
        if _should_send_transient_alert():
            send_failure_alert(msg)
            _record_transient_alert()
        raise SystemExit(0)
    if isinstance(e, PyiCloudAuthRequiredException):
        msg = f"FindMy 450 reauth failed (transient — self-heals next run, no action needed): {_fmt_exc(e)}"
        print(f"[pipeline] {msg}")
        # _refresh_client_with_reauth() may have partially updated the cookie jar before
        # aborting. Save whatever state it left so the next run starts with the freshest
        # possible cookies rather than the pre-reauth ones.
        _cookies = api.session.cookies
        if hasattr(_cookies, 'save'):
            try:
                _cookies.save(ignore_discard=True)
                print(f"[pipeline] transient 450: cookie jar saved to disk (partial reauth state preserved)")
            except Exception as _tse:
                print(f"[pipeline] transient 450: WARNING — cookie jar save failed: {_tse}")
        # Clear holdoff: if the holdoff caused a skipped /accountLogin and the retry still
        # 450d, the cookies may have genuinely expired. Clear so the next run calls /accountLogin.
        try:
            if os.path.exists(_ACCOUNTLOGIN_HOLDOFF_FILE):
                os.remove(_ACCOUNTLOGIN_HOLDOFF_FILE)
                print(f"[pipeline] transient 450: holdoff cleared — next run will call /accountLogin")
        except Exception:
            pass
        # Increment transient exit counter. After _TRANSIENT_EXIT_THRESHOLD consecutive
        # exits of this type, write a hold_until timestamp to prevent the next run from
        # immediately calling /accountLogin again (which burns a dsWebAuthToken slot).
        _new_transient_count = _transient_exit_count + 1
        _new_hold_until = 0
        if _new_transient_count >= _TRANSIENT_EXIT_THRESHOLD:
            _new_hold_until = time.time() + _TRANSIENT_HOLD_SECS
            _hold_min = _TRANSIENT_HOLD_SECS // 60
            print(f"[pipeline] transient 450: {_new_transient_count} consecutive exit(s) — "
                  f"hold-off active for {_hold_min}min to allow MME token propagation")
        else:
            print(f"[pipeline] transient 450: {_new_transient_count}/{_TRANSIENT_EXIT_THRESHOLD} "
                  f"consecutive exit(s) — no hold-off yet")
        try:
            with open(_TRANSIENT_EXIT_CTR_FILE, 'w') as _tcf:
                json.dump({"count": _new_transient_count, "hold_until": _new_hold_until}, _tcf)
        except Exception as _tce:
            print(f"[pipeline] transient 450: WARNING — could not write transient counter: {_tce}")
        if _should_send_transient_alert():
            send_failure_alert(msg)
            _record_transient_alert()
        raise SystemExit(0)
    if isinstance(e, (PyiCloudFailedLoginException, PyiCloudAPIResponseException, _CronAuthRequired)):
        if isinstance(e, PyiCloudAPIResponseException) and e.code == 421:
            # 421 mid-pipeline: Apple wants re-auth. Apply the same progressive
            # backoff as the construction-time handler so we don't hammer Apple.
            # Mid-pipeline 421s arrive as PyiCloudAPIResponseException(code=421) directly
            # (from Find My endpoints), so e.code is reliable here.
            msg = f"iCloud 421 mid-pipeline (run util/auth.sh): {_fmt_exc(e)}"
            print(f"[auth] {msg}")
            _new_count = _pre_startup_fail_count + 1
            _next_wait = _AUTH_BACKOFF_SECS[min(_new_count, len(_AUTH_BACKOFF_SECS) - 1)] // 60
            print(f"[auth] Consecutive failures: {_new_count} — next attempt in {_next_wait}min")
            _max_count = len(_AUTH_BACKOFF_SECS) - 1
            if _pre_startup_fail_count == 0 or _new_count == _max_count:
                send_failure_alert(msg)  # alert on first failure and again at max backoff
            try:
                with open(AUTH_BACKOFF_FILE, 'w') as _f:
                    json.dump({"count": _new_count, "last_ts": time.time()}, _f)
            except Exception:
                pass
            raise SystemExit(0)  # exit cleanly — no retry, no re-raise into Apple
        # Auth failure anywhere in the pipeline — exit cleanly without re-raising.
        # Re-raising would let pyicloud's internals retry SRP against Apple,
        # potentially resetting the rate-limit cooldown.
        # Apply the same progressive backoff as 421 errors — every auth failure
        # needs to throttle, not just 421s. Without this, PyiCloudAuthRequiredException
        # failures run every 5 min indefinitely with no backoff at all.
        msg = f"Session needs re-auth (run util/auth.sh): {_fmt_exc(e)}"
        print(f"[auth] {msg}")
        # Use _pre_startup_fail_count: it captured the on-disk count before this run
        # started. If a prior 450→421 failure wrote count=N and the pipeline is now
        # failing again, _pre_startup_fail_count=N correctly yields N+1 here.
        _new_count = _pre_startup_fail_count + 1
        _next_wait = _AUTH_BACKOFF_SECS[min(_new_count, len(_AUTH_BACKOFF_SECS) - 1)] // 60
        print(f"[auth] Consecutive failures: {_new_count} — next attempt in {_next_wait}min")
        _max_count = len(_AUTH_BACKOFF_SECS) - 1
        # Alert immediately if the error is a hard "Invalid authentication token" failure —
        # these are never transient (they mean the dsWebAuthToken TTL expired server-side)
        # and waiting for count=2 creates a needless 5-minute silent window.
        # For all other auth failures, alert at count=2 (the first failure's next-wait
        # is 0 min so an immediate retry follows; if it succeeds the alert was premature).
        # Alert again when entering max backoff so silence doesn't stretch unnoticed.
        _is_hard_token_expiry = "Invalid authentication token" in str(e)
        if _is_hard_token_expiry and _new_count == 1:
            send_failure_alert(msg)
        elif _new_count >= 2 and (_new_count == 2 or _new_count == _max_count):
            send_failure_alert(msg)
        try:
            with open(AUTH_BACKOFF_FILE, 'w') as _f:
                json.dump({"count": _new_count, "last_ts": time.time()}, _f)
        except Exception:
            pass
        raise SystemExit(0)
    else:
        send_failure_alert(f"Unexpected pipeline failure — {_fmt_exc(e)}")
        raise
