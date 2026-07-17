#!/usr/bin/env python3
"""
Isolated auth test for the timlaing/pyicloud fork.

Isolation guarantees:
  - Uses ~/.pyicloud-test as cookie dir — never reads or writes ~/.pyicloud
  - Runs in .venv-test — never touches the production venv
  - A failed SRP attempt here cannot invalidate your existing working session
  - The worst outcome is extending Apple's rate-limit window for future auth
    (does NOT affect the currently running main process, which uses cached cookies)

Run via:  bash util/test_timlaing_auth.sh
"""

import getpass
import json
import os
import signal
import sys

APPLE_ID = "joe@joemoser.com"
COOKIE_DIR = os.path.expanduser("~/.pyicloud-test")

os.makedirs(COOKIE_DIR, exist_ok=True)
print(f"Cookie dir: {COOKIE_DIR}  (isolated — production cookies untouched)")
print()

# ── Import check ──────────────────────────────────────────────────────────────

try:
    from pyicloud import PyiCloudService
    from pyicloud.exceptions import (
        PyiCloudFailedLoginException,
        PyiCloudAuthRequiredException,
        PyiCloud2FARequiredException,
    )
    import pyicloud
    print(f"pyicloud version : {getattr(pyicloud, '__version__', 'unknown')}")
    print(f"pyicloud location: {pyicloud.__file__}")
    print()
except ImportError as exc:
    print(f"ERROR: could not import pyicloud — {exc}")
    print("Run via:  bash util/test_timlaing_auth.sh  (it sets up the venv)")
    sys.exit(1)

# ── Password ──────────────────────────────────────────────────────────────────

password = getpass.getpass("Apple ID password: ")

# ── SRP auth with timeout ─────────────────────────────────────────────────────

def _timeout(sig, frame):
    raise TimeoutError("SRP auth timed out after 30 s — Apple may still be rate-limiting. Wait and retry.")

signal.signal(signal.SIGALRM, _timeout)
signal.alarm(30)

print("Authenticating via SRP (timlaing fork)...")
print("If this hangs > 30 s Apple is still rate-limiting — wait and retry.")

try:
    api = PyiCloudService(APPLE_ID, password=password, cookie_directory=COOKIE_DIR)
    signal.alarm(0)
except TimeoutError as exc:
    print(f"\nTIMEOUT: {exc}")
    sys.exit(1)
except (PyiCloudFailedLoginException, PyiCloudAuthRequiredException) as exc:
    signal.alarm(0)
    print(f"\nAUTH FAILED: {exc}")
    print("Apple rejected the SRP exchange. Likely still rate-limited — wait longer and retry.")
    print("Your existing production session is unaffected.")
    sys.exit(1)
except Exception as exc:
    signal.alarm(0)
    print(f"\nUNEXPECTED ERROR: {exc}")
    sys.exit(1)

print(f"SRP succeeded.")
print(f"  requires_2fa : {api.requires_2fa}")
print(f"  requires_2sa : {api.requires_2sa}")
print()

# ── 2FA ───────────────────────────────────────────────────────────────────────

if not api.requires_2fa and not api.requires_2sa:
    print("Session already trusted — no 2FA needed.")
    try:
        api.trust_session()
        print("trust_session() called to refresh.")
    except Exception as exc:
        print(f"trust_session() error (non-fatal): {exc}")
    print()
    print("SUCCESS: timlaing fork loaded existing session without re-auth.")
    print("Safe to switch production venv to the timlaing fork.")
    sys.exit(0)

# The timlaing fork (post PR #260) automatically requests code delivery
# during authenticate() — a push notification should arrive on your trusted
# devices, or SMS if push isn't available.
print("2FA required. The timlaing fork should have already requested a code.")
print("Check your iPhone/Mac for a 6-digit notification, or wait for SMS.")
print("(If nothing arrives within 30 s, the rate limit may still be active.)")
print()

code = input("Enter 6-digit 2FA code (Ctrl+C to abort): ").strip()

print("Validating code...")
try:
    result = api.validate_2fa_code(code)
    if not result:
        print("validate_2fa_code() returned False — code rejected.")
        sys.exit(1)
    print(f"validate_2fa_code() → {result}")
except Exception as exc:
    print(f"validate_2fa_code() error: {exc}")
    sys.exit(1)

print("Calling trust_session()...")
try:
    trusted = api.trust_session()
    print(f"trust_session() → {trusted}")
    if not trusted:
        print("Warning: trust_session() returned False.")
        print("FindMy access may still require re-auth, but 2FA code delivery is working.")
except Exception as exc:
    print(f"trust_session() error: {exc}")

# ── Smoke-test FindMy access ──────────────────────────────────────────────────

print()
print("Testing FindMy device access...")
try:
    devices = list(api.devices)
    print(f"  Found {len(devices)} device(s):")
    for d in devices[:5]:
        model = d.data.get("deviceModel") or d.data.get("rawDeviceModel") or "?"
        name  = str(d)
        print(f"    {name} ({model})")
    if len(devices) > 5:
        print(f"    ... and {len(devices) - 5} more")
    print()
    print("SUCCESS: timlaing fork authenticated and can access FindMy.")
    print()
    print("Next steps:")
    print("  1. Verify devices listed above look correct.")
    print("  2. If happy, update production venv:")
    print("       .venv/bin/pip install git+https://github.com/timlaing/pyicloud.git")
    print("  3. Run bash util/auth.sh once more (using the updated production venv).")
except Exception as exc:
    print(f"  FindMy access failed: {exc}")
    print("  2FA validated but FindMy may need another trust cycle.")
    sys.exit(1)
