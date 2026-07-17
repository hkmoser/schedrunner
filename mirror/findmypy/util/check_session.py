#!/usr/bin/env python3
"""
Quick session and API health check — no SRP, no side effects.

Run via:  .venv/bin/python util/check_session.py
"""
import os, sys

APPLE_ID = "joe@joemoser.com"
COOKIE_DIR = os.path.expanduser("~/.pyicloud")

try:
    from pyicloud import PyiCloudService
    from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudAuthRequiredException
    import pyicloud
    print(f"pyicloud: {getattr(pyicloud, '__version__', 'unknown')}  ({pyicloud.__file__})")
except ImportError as e:
    print(f"ERROR: cannot import pyicloud — {e}")
    sys.exit(1)

print(f"Cookie dir: {COOKIE_DIR}")
files = os.listdir(COOKIE_DIR) if os.path.isdir(COOKIE_DIR) else []
print(f"Session files: {files or '(none)'}")
print()

class _CheckService(PyiCloudService):
    """Token-only auth — never falls back to SRP."""
    def _authenticate(self):
        try:
            self._authenticate_with_token()
        except Exception as exc:
            raise RuntimeError(f"token auth failed: {exc}") from exc

print("Attempting token auth (no SRP)...")
try:
    api = _CheckService(APPLE_ID, cookie_directory=COOKIE_DIR)
except RuntimeError as exc:
    print(f"FAIL — {exc}")
    print()
    print("Session is expired or invalid. Run:  bash util/auth.sh")
    sys.exit(1)
except (PyiCloudFailedLoginException, PyiCloudAuthRequiredException) as exc:
    print(f"FAIL — {type(exc).__name__}: {exc}")
    print()
    print("Session is expired or invalid. Run:  bash util/auth.sh")
    sys.exit(1)
except Exception as exc:
    print(f"FAIL — unexpected: {type(exc).__name__}: {exc}")
    sys.exit(1)

print(f"Token auth OK")
print(f"  requires_2fa : {api.requires_2fa}")
print(f"  requires_2sa : {api.requires_2sa}")
print()

if api.requires_2fa or api.requires_2sa:
    print("WARNING: Apple is asking for 2FA — session exists but is not trusted.")
    print("Run:  bash util/auth.sh")
    sys.exit(1)

print("Fetching device list...")
try:
    devices = list(api.devices)
    print(f"OK — {len(devices)} device(s) found:")
    for d in devices:
        model = d.data.get("deviceModel") or d.data.get("rawDeviceModel") or "?"
        print(f"  {d}  [{model}]")
    print()
    print("Session is healthy. Pipeline should work.")
except Exception as exc:
    print(f"FAIL — device list error: {type(exc).__name__}: {exc}")
    sys.exit(1)
