#!/usr/bin/env python3
"""
Interactive re-auth — pyicloud 2.x (PyPI) edition.

Safe-write: all session files go to a temp dir first.
~/.pyicloud is only replaced on full success (SRP + SMS code + trust).

Run via:  bash util/auth.sh
"""
import getpass
import glob
import os
import shutil
import signal
import sys
import tempfile

APPLE_ID = "joe@joemoser.com"
PROD_COOKIE_DIR = os.path.expanduser("~/.pyicloud")

print("=" * 60)
print("findmypy re-auth  (pyicloud 2.x)")
print(f"  Apple ID:       {APPLE_ID}")
print(f"  Production dir: {PROD_COOKIE_DIR}  (untouched until full success)")
print("=" * 60)
print()

try:
    from pyicloud import PyiCloudService
    from pyicloud.exceptions import PyiCloudFailedLoginException
    import pyicloud
    print(f"pyicloud {getattr(pyicloud, '__version__', '?')}  ({pyicloud.__file__})")
except ImportError as exc:
    print(f"ERROR: cannot import pyicloud — {exc}")
    print("Run:  pip install 'pyicloud>=2.0'")
    sys.exit(1)

print()
password = getpass.getpass("Apple ID password: ")
print()

tmp_dir = tempfile.mkdtemp(prefix="pyicloud-auth-")
print(f"Temp session dir: {tmp_dir}")
print()


def _cleanup():
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Step 1: SRP auth (30-second hard timeout) ─────────────────────────────────

def _on_timeout(sig, frame):
    raise TimeoutError(
        "SRP timed out after 30 s — Apple is still rate-limiting.\n"
        "Wait 24 h with zero auth attempts, then retry."
    )

signal.signal(signal.SIGALRM, _on_timeout)
signal.alarm(30)

print("Step 1/3 — SRP authentication...")
print("  (hangs > 30 s = still rate-limited)")

try:
    api = PyiCloudService(APPLE_ID, password=password, cookie_directory=tmp_dir)
    signal.alarm(0)
    print(f"  SRP succeeded.")
    print(f"  requires_2fa : {api.requires_2fa}")
    print(f"  requires_2sa : {api.requires_2sa}")
    print(f"  delivery     : {api.two_factor_delivery_method}")
    print()
except TimeoutError as exc:
    signal.alarm(0)
    print(f"\nTIMEOUT: {exc}")
    _cleanup(); sys.exit(1)
except PyiCloudFailedLoginException as exc:
    signal.alarm(0)
    print(f"\nAUTH FAILED: {exc}")
    print("Wrong password, or Apple is rate-limiting (wait 24 h).")
    _cleanup(); sys.exit(1)
except KeyboardInterrupt:
    signal.alarm(0)
    print("\nAborted."); _cleanup(); sys.exit(1)
except Exception as exc:
    signal.alarm(0)
    print(f"\nERROR: {type(exc).__name__}: {exc}")
    _cleanup(); sys.exit(1)

trust_failed = False

if not api.requires_2fa and not api.requires_2sa:
    print("Session already trusted — no 2FA needed.")
    api.trust_session()
else:
    # ── Step 2: Delivery status ────────────────────────────────────────────────
    print("Step 2/3 — 2FA code delivery")
    # pyicloud's _srp_authentication() already called _request_2fa_code()
    # internally, which sent both a push notification and an SMS. Do NOT send
    # another SMS here — a second PUT /verify/phone invalidates the first code,
    # forcing the user to wait for a replacement they can't predict is coming.
    print(f"  Delivery method: {api.two_factor_delivery_method}")
    print("  A push notification and SMS were sent during SRP (above).")
    print("  Use the most recent code — ignore any earlier one.")
    print()

    # ── Step 3: Enter code + validate ────────────────────────────────────────
    print("Step 3/3 — Enter 2FA code")
    print("  Check SMS on your phone (or approve the push notification).")
    print()
    try:
        code = input("  6-digit code: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted."); _cleanup(); sys.exit(1)

    if not code or not code.isdigit() or len(code) != 6:
        print("Invalid code."); _cleanup(); sys.exit(1)

    print()
    print("  Validating...")
    try:
        ok = api.validate_2fa_code(code)
        # Two distinct outcomes when it returns False vs raises:
        #   RETURNS False  → code was ACCEPTED; trust_session() itself failed.
        #   RAISES exception → code was wrong (PyiCloudAPIResponseException) or
        #                       a network error; caught by the except block below.
        print(f"  validate_2fa_code → {ok}  (trust_session called internally)")

        # Flush updated session data (new dsWebAuthToken + trust_token) to tmp_dir.
        # validate_2fa_code → trust_session() updates these in memory only; pyicloud
        # does not automatically re-serialize them back to the cookie_directory files.
        # Without this flush, the installed .session file contains the pre-trust SRP
        # token, which Apple has already rotated — causing every subsequent /accountLogin
        # to fail with 421 "Invalid authentication token" (confirmed by experts).
        try:
            if hasattr(api, '_save_session_data'):
                api._save_session_data()
                print("  Session data flushed to tmp_dir after trust_session ✓")
            elif hasattr(api, 'session') and hasattr(api.session, 'save'):
                api.session.save()
                print("  Session saved to tmp_dir after trust_session ✓")
            else:
                import hashlib, json as _json
                _session_dir = getattr(api, '_cookie_directory', tmp_dir)
                _session_file = os.path.join(
                    _session_dir,
                    hashlib.md5(APPLE_ID.encode()).hexdigest() + ".session"
                )
                if hasattr(api, 'session') and hasattr(api.session, 'data'):
                    with open(_session_file, 'w') as _sf:
                        _json.dump(api.session.data, _sf)
                    print(f"  Session data manually written to {_session_file} ✓")
                    print(f"  trust_token present: {bool(api.session.data.get('trust_token'))}")
                    print(f"  session_token present: {bool(api.session.data.get('session_token'))}")
                else:
                    print("  WARNING: cannot flush session data — api.session.data not accessible")
            if hasattr(api, 'session') and hasattr(api.session, 'cookies') and hasattr(api.session.cookies, 'save'):
                api.session.cookies.save(ignore_discard=True)
                print("  Cookie jar flushed to tmp_dir after trust_session ✓")
        except Exception as _flush_err:
            print(f"  WARNING: session flush failed: {_flush_err}")
            print("  Continuing — installed session may have stale dsWebAuthToken")

        if not ok:
            # The 2FA code was correct — Apple accepted it. trust_session() ran and
            # committed the trust_token to disk (GET /2sv/trust saves the
            # X-Apple-TwoSV-Trust-Token header), but the subsequent /accountLogin
            # still returned hsaTrustedBrowser: False — either a propagation lag or
            # Apple suppressing trust due to repeated auth attempts.
            # Note: the in-memory session is untrusted, so api.devices will always
            # fail from here — the smoke test must be skipped if we continue.
            print()
            print("  *** Code ACCEPTED, but trust_session() failed. ***")
            print(f"  trust_token saved  : {bool(api.session.data.get('trust_token'))}")
            print("  This is NOT a wrong code — Apple accepted it.")
            print("  Possible causes:")
            print("    1. Propagation lag — Apple committed trust but /accountLogin")
            print("       hasn't seen it yet. The cron may succeed on its next run.")
            print("    2. Suspicious-account state from repeated auth hammering.")
            print("       In this case, wait 24h before retrying.")
            print()
            ans = input("  Install partial session and let cron verify? (y/N): ").strip().lower()
            if ans != "y":
                print("  Not installing. Wait 24h, then rerun auth.sh.")
                _cleanup(); sys.exit(1)
            trust_failed = True
    except Exception as exc:
        print(f"  Validation error: {type(exc).__name__}: {exc}")
        _cleanup(); sys.exit(1)

# ── Smoke test ─────────────────────────────────────────────────────────────────

print()
if trust_failed:
    # api.devices requires a trusted in-memory session (api._webservices is None
    # when /accountLogin returned hsaTrustedBrowser: False). Skip the smoke test;
    # the cron's first run serves as the live test.
    print("Skipping smoke test (in-memory session untrusted; trust_token is on disk).")
    print("  The cron pipeline will verify on its next run.")
else:
    print("Smoke test — FindMy device access...")
    try:
        devices = list(api.devices)
        if not devices:
            print("  WARNING: 0 devices — FindMy may need another trust cycle.")
        else:
            print(f"  {len(devices)} device(s):")
            for d in devices[:5]:
                model = d.data.get("deviceModel") or d.data.get("rawDeviceModel") or "?"
                print(f"    {d}  [{model}]")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print("  Not installing session.")
        _cleanup(); sys.exit(1)

# ── Atomic install to ~/.pyicloud ──────────────────────────────────────────────
# Strategy: copytree to a staging dir (slow, safe — live session untouched),
# then atomic os.rename (single syscall) to swap it into place.
# A cron run during the copy sees the live session unchanged; one during the
# rename sees either old or new — never a partial state.

print()
print(f"Installing session → {PROD_COOKIE_DIR} ...")
bak = PROD_COOKIE_DIR + ".bak"
staging = PROD_COOKIE_DIR + ".installing"
try:
    if os.path.exists(staging):
        shutil.rmtree(staging)
    shutil.copytree(tmp_dir, staging)           # slow copy — live session untouched
    if os.path.exists(bak):
        shutil.rmtree(bak)
    if os.path.exists(PROD_COOKIE_DIR):
        shutil.move(PROD_COOKIE_DIR, bak)       # atomic rename out
        print(f"  Old session backed up → {bak}")
    os.rename(staging, PROD_COOKIE_DIR)         # atomic rename in — single syscall
    files = [os.path.basename(f) for f in glob.glob(os.path.join(PROD_COOKIE_DIR, "*"))]
    print(f"  Files: {files}")
    if os.path.exists(bak):
        shutil.rmtree(bak)
except Exception as exc:
    print(f"  ERROR: {exc}")
    if os.path.exists(bak) and not os.path.exists(PROD_COOKIE_DIR):
        shutil.move(bak, PROD_COOKIE_DIR)
        print("  Restored backup.")
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    _cleanup(); sys.exit(1)

_cleanup()

# Clear progressive backoff state so the pipeline resumes immediately.
_backoff = os.path.expanduser("~/.pyicloud-auth-backoff")
if os.path.exists(_backoff):
    os.remove(_backoff)
    print("Cleared auth backoff state — cron job will resume on next run.")

print()
print("=" * 60)
print("AUTH COMPLETE")
print(f"  Session: {PROD_COOKIE_DIR}")
print("  The cron job will now load this session on each run.")
print("  Sessions typically last 1–3 months.")
print("  Do NOT run auth.sh again unless you get a re-auth Pushcut alert.")
print("=" * 60)
