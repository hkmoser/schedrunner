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
import time

APPLE_ID = "joe@joemoser.com"
PROD_COOKIE_DIR = os.path.expanduser("~/.pyicloud")

# Pipeline state files that describe the OLD session. A freshly installed session
# must not inherit them, or afm_live.py starts out already in backoff/hold-off.
STATE_FILES = [
    os.path.expanduser("~/.pyicloud-auth-backoff"),           # progressive auth backoff
    os.path.expanduser("~/.pyicloud-transient-ctr"),          # consecutive 450-exit counter
    os.path.expanduser("~/.pyicloud-transient-last"),         # transient alert cooldown
    os.path.expanduser("~/.pyicloud-accountlogin-holdoff"),   # /accountLogin holdoff
    os.path.expanduser("~/.pyicloud-expiry-warned"),          # cookie-expiry warning marker
]

# Smoke-test retry policy. Apple's FindMy endpoint returns 450 on the first call
# of a fresh session (no _server_ctx yet); pyicloud re-auths and retries once, and
# if that retry also 450s it raises PyiCloudAuthRequiredException. afm_live.py
# already treats that exception as transient and self-healing — so must this.
SMOKE_ATTEMPTS = 3
SMOKE_BACKOFF = [20, 45]  # seconds before attempts 2 and 3

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

# Optional exception types — names differ across pyicloud versions, so every
# lookup is defensive and the classifier below tolerates any of them missing.
def _exc_type(name):
    return getattr(sys.modules["pyicloud.exceptions"], name, None)

_AuthRequired = _exc_type("PyiCloudAuthRequiredException")
_2FARequired = _exc_type("PyiCloud2FARequiredException")
_2SARequired = _exc_type("PyiCloud2SARequiredException")
_NotActivated = _exc_type("PyiCloudServiceNotActivatedException")

# A smoke-test failure of one of these means the session itself is bad — installing
# it would only hand the cron a dead session. Anything else (notably the FindMy 450
# → PyiCloudAuthRequiredException path) is transient and worth installing, because
# the alternative is another SRP round that risks a 24 h rate-limit lockout.
_FATAL_SMOKE_ERRORS = tuple(
    t for t in (PyiCloudFailedLoginException, _2FARequired, _2SARequired, _NotActivated)
    if t is not None
)


def session_file_path(api, cookie_dir: str) -> str:
    """The file pyicloud actually reads its session JSON back from.

    This build names it after the sanitized Apple ID (joejoemosercom.session),
    NOT md5(apple_id).session — an earlier guess here wrote the post-trust token
    to an md5 filename that pyicloud never reads, leaving an orphan file in
    ~/.pyicloud and the real session file holding the pre-trust token. Ask
    pyicloud for the path, fall back to whatever .session file it already wrote,
    and only then guess.
    """
    for obj in (api, getattr(api, "session", None)):
        for attr in ("session_path", "_session_path"):
            path = getattr(obj, attr, None)
            if isinstance(path, str) and path:
                return path
    existing = sorted(glob.glob(os.path.join(cookie_dir, "*.session")))
    if existing:
        return existing[0]
    return os.path.join(cookie_dir, "".join(c for c in APPLE_ID if c.isalnum()) + ".session")


def install_session(src_dir: str) -> bool:
    """Atomically swap src_dir into PROD_COOKIE_DIR. Returns True on success.

    Strategy: copytree to a staging dir (slow, safe — live session untouched),
    then atomic os.rename (single syscall) to swap it into place.
    A cron run during the copy sees the live session unchanged; one during the
    rename sees either old or new — never a partial state.
    """
    print()
    print(f"Installing session → {PROD_COOKIE_DIR} ...")
    bak = PROD_COOKIE_DIR + ".bak"
    staging = PROD_COOKIE_DIR + ".installing"
    try:
        if os.path.exists(staging):
            shutil.rmtree(staging)
        shutil.copytree(src_dir, staging)           # slow copy — live session untouched
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
        return True
    except Exception as exc:
        print(f"  ERROR: {exc}")
        if os.path.exists(bak) and not os.path.exists(PROD_COOKIE_DIR):
            shutil.move(bak, PROD_COOKIE_DIR)
            print("  Restored backup.")
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        return False


def clear_pipeline_state() -> None:
    """Drop the old session's backoff/hold-off markers so the cron resumes clean."""
    cleared = []
    for path in STATE_FILES:
        try:
            if os.path.exists(path):
                os.remove(path)
                cleared.append(os.path.basename(path))
        except Exception as exc:
            print(f"  WARNING: could not remove {path}: {exc}")
    if cleared:
        print(f"Cleared pipeline state: {', '.join(cleared)}")


# ── --install-from: recover a preserved temp session without another SRP ───────
# When a run gets a valid session but the smoke test dies on something fatal, the
# temp dir is kept. This installs it directly — no password, no SRP, no rate-limit
# risk. Only worth using when the failure looked recoverable (e.g. Apple was down).

if "--install-from" in sys.argv:
    _idx = sys.argv.index("--install-from")
    if _idx + 1 >= len(sys.argv):
        print("ERROR: --install-from needs a directory argument")
        sys.exit(1)
    _src = os.path.expanduser(sys.argv[_idx + 1])
    if not os.path.isdir(_src):
        print(f"ERROR: {_src} is not a directory")
        sys.exit(1)
    if not glob.glob(os.path.join(_src, "*.session")):
        print(f"ERROR: {_src} holds no *.session file — that is not a pyicloud session dir")
        sys.exit(1)
    print(f"Installing preserved session from {_src} (no SRP, no 2FA)")
    if not install_session(_src):
        sys.exit(1)
    clear_pipeline_state()
    print("\nDone. The cron job will use this session on its next run.")
    sys.exit(0)

print()
password = getpass.getpass("Apple ID password: ")
print()

tmp_dir = tempfile.mkdtemp(prefix="pyicloud-auth-")
print(f"Temp session dir: {tmp_dir}")
print()


def _cleanup():
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _keep_tmp(reason: str):
    """Leave the temp session on disk so a good session is never thrown away."""
    print()
    print(f"  Session NOT installed ({reason}).")
    print(f"  The authenticated session is preserved at:")
    print(f"    {tmp_dir}")
    print(f"  If you decide it is worth keeping, install it without re-running SRP:")
    print(f"    .venv/bin/python util/auth.py --install-from {tmp_dir}")
    print(f"  Otherwise just delete it:  rm -rf {tmp_dir}")


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
        # validate_2fa_code → trust_session() may leave these in memory only. If the
        # installed .session file still holds the pre-trust SRP token, Apple has
        # already rotated it and every later /accountLogin fails with 421
        # "Invalid authentication token".
        # The write MUST land on the filename pyicloud reads back (see
        # session_file_path) — writing to any other name silently does nothing.
        try:
            if hasattr(api, '_save_session_data'):
                api._save_session_data()
                print("  Session data flushed to tmp_dir after trust_session ✓")
            elif hasattr(api, 'session') and hasattr(api.session, 'save'):
                api.session.save()
                print("  Session saved to tmp_dir after trust_session ✓")
            else:
                import json as _json
                _session_dir = getattr(api, '_cookie_directory', tmp_dir)
                _session_file = session_file_path(api, _session_dir)
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
                print("  Wait 24h, then rerun auth.sh.")
                _keep_tmp("declined at the prompt")
                sys.exit(1)
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
    print("  (a 450 on the first call is normal for a brand-new session —")
    print("   Apple has no _server_ctx for it yet; pyicloud re-auths and retries)")

    smoke_err = None
    devices = None
    for attempt in range(1, SMOKE_ATTEMPTS + 1):
        try:
            devices = list(api.devices)
            smoke_err = None
            break
        except Exception as exc:
            smoke_err = exc
            print(f"  attempt {attempt}/{SMOKE_ATTEMPTS} failed: {type(exc).__name__}: {exc}")
            if attempt == SMOKE_ATTEMPTS or isinstance(exc, _FATAL_SMOKE_ERRORS):
                break
            wait = SMOKE_BACKOFF[min(attempt - 1, len(SMOKE_BACKOFF) - 1)]
            print(f"  waiting {wait}s, then re-minting FindMy cookies and retrying...")
            time.sleep(wait)
            try:
                api.authenticate(force_refresh=True, service="find")
                print("  /accountLogin OK — retrying device fetch")
            except Exception as reauth_exc:
                print(f"  re-auth before retry failed: "
                      f"{type(reauth_exc).__name__}: {reauth_exc}")

    if smoke_err is None:
        if not devices:
            print("  WARNING: 0 devices — FindMy may need another trust cycle.")
        else:
            print(f"  {len(devices)} device(s):")
            for d in devices[:5]:
                model = d.data.get("deviceModel") or d.data.get("rawDeviceModel") or "?"
                print(f"    {d}  [{model}]")
    elif isinstance(smoke_err, _FATAL_SMOKE_ERRORS):
        # The session itself is bad — Apple rejected the credential, not just the
        # FindMy call. Installing it would hand the cron a dead session.
        print(f"  FAILED (session-level): {type(smoke_err).__name__}: {smoke_err}")
        _keep_tmp("Apple rejected the session itself, not just FindMy")
        sys.exit(1)
    else:
        # Everything else — overwhelmingly PyiCloudAuthRequiredException from a
        # repeated FindMy 450 — is transient. afm_live.py already classifies this
        # exact exception as "self-heals next run, no action needed". Discarding a
        # freshly trusted session over it is strictly worse than installing it: the
        # only way back is another SRP round, which is what triggers Apple's 24 h
        # rate limiting in the first place.
        print()
        print(f"  Smoke test did not pass: {type(smoke_err).__name__}: {smoke_err}")
        print("  This is the FindMy cold-start 450, not a bad session:")
        _sdata = getattr(getattr(api, "session", None), "data", {}) or {}
        print(f"    2FA code accepted        : yes")
        print(f"    trust_token on disk      : {bool(_sdata.get('trust_token'))}")
        print(f"    session_token on disk    : {bool(_sdata.get('session_token'))}")
        print("  The pipeline treats this exception as transient and recovers from it")
        print("  on the next run, so the session is worth installing.")
        print()
        try:
            ans = input("  Install this session? (Y/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            ans = "n"
        if ans in ("n", "no"):
            _keep_tmp("declined at the prompt")
            sys.exit(1)
        print("  Installing — the cron's next run is the live test.")

# ── Atomic install to ~/.pyicloud ──────────────────────────────────────────────

if not install_session(tmp_dir):
    _keep_tmp("install failed — old session restored")
    sys.exit(1)

_cleanup()

# Drop the old session's backoff/hold-off markers so the pipeline resumes immediately.
clear_pipeline_state()

print()
print("=" * 60)
print("AUTH COMPLETE")
print(f"  Session: {PROD_COOKIE_DIR}")
print("  The cron job will now load this session on each run.")
print("  Sessions typically last 1–3 months.")
print("  Do NOT run auth.sh again unless you get a re-auth Pushcut alert.")
print("=" * 60)
