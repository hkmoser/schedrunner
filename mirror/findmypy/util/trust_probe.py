#!/usr/bin/env python3
"""
Replay GET /2sv/trust against the session already on disk.

No SRP, no password, no new 2FA code — it reuses the session tokens auth.py
saved, so it is safe to run repeatedly. Prints the raw status, header names and
body that pyicloud's trust_session() swallows. Token values are never printed.

The repair itself lives in afm_trust_repair, which the daemon also calls.

    .venv/bin/python util/trust_probe.py
    .venv/bin/python util/trust_probe.py --dir ~/.pyicloud
    .venv/bin/python util/trust_probe.py --repair
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import afm_trust_repair as repair_mod

SAFE_HEADERS = (
    "content-type", "retry-after", "x-apple-i-request-id", "x-apple-api-version",
    "x-responding-instance", "server", "date",
)

args = sys.argv[1:]
cookie_dir = repair_mod.COOKIE_DIR
if "--dir" in args:
    cookie_dir = os.path.expanduser(args[args.index("--dir") + 1])
do_repair = "--repair" in args

print(f"Apple ID   : {repair_mod.APPLE_ID}")
print(f"Cookie dir : {cookie_dir}")
print(f"Files      : {sorted(os.listdir(cookie_dir)) if os.path.isdir(cookie_dir) else '(missing)'}")
remaining = repair_mod.cooldown_remaining()
if remaining:
    print(f"Cooldown   : {remaining:.0f}s left (the probe ignores it; the daemon does not)")
print()

try:
    api = repair_mod.build_probe_service(cookie_dir)
except Exception as exc:
    print(f"FATAL: could not construct service: {type(exc).__name__}: {exc}")
    sys.exit(1)

dicts = repair_mod.session_dicts(api)
if len(dicts) > 1 and set(dicts[0]) != set(dicts[1]):
    print("NOTE: this build exposes two session dicts with DIFFERENT keys —")
    print(f"  richest : {sorted(dicts[0])}")
    print(f"  other   : {sorted(dicts[1])}")
    print("  A writer that saves only one of them loses tokens.")
    print()

if not dicts or not dicts[0]:
    print("No session data on disk — run auth.sh first.")
    sys.exit(1)

print("Session data keys (values not printed):")
for key in sorted(dicts[0]):
    val = dicts[0][key]
    print(f"  {key:24} {f'len={len(val)}' if isinstance(val, str) else repr(val)}")
print()

print("Cookies:")
try:
    for cookie in api.session.cookies:
        if cookie.expires:
            print(f"  {cookie.name:32} expires in {(cookie.expires - time.time()) / 86400:.2f} d")
        else:
            print(f"  {cookie.name:32} session cookie")
except Exception as exc:
    print(f"  (could not read: {exc})")
print()

try:
    url, resp = repair_mod.replay_trust(api)
except Exception as exc:
    print(f"FATAL: trust replay raised: {type(exc).__name__}: {exc}")
    sys.exit(1)

print(f"GET {url}")
print(f"  status : {resp.status_code} {resp.reason}")
print(f"  header names: {sorted(resp.headers)}")
for name in SAFE_HEADERS:
    if name in resp.headers:
        print(f"  {name}: {resp.headers[name]}")

header = repair_mod.trust_token_header(resp)
print(f"  trust-token header present: {bool(header)}"
      + (f" ({header}, len={len(resp.headers[header])})" if header else ""))
body = resp.text or ""
print(f"  body ({len(body)} bytes): {body[:600]!r}")
print()

if not resp.ok or not header:
    print("No trust token in the response. The status and body above are the reason")
    print("trust_session() returned False.")
    sys.exit(1)

print("Apple DID return a trust token on this replay.")
if not do_repair:
    print("Rerun with --repair to save it and re-mint the session cookies:")
    print(f"  .venv/bin/python util/trust_probe.py --dir {cookie_dir} --repair")
    sys.exit(0)

if not repair_mod.persist_headers(api, resp, cookie_dir):
    sys.exit(1)

try:
    api._authenticate_with_token()
    print("[repair] /accountLogin OK — session repaired without SRP")
    repair_mod.clear_cooldown()
except Exception as exc:
    print(f"[repair] /accountLogin still failing after trust: {type(exc).__name__}: {exc}")
    sys.exit(1)

print()
print("Now verify:  .venv/bin/python util/check_session.py")
