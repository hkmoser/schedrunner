#!/usr/bin/env python3
"""
Trust replay: recover a session Apple rejected, without SRP.

A session dies when /accountLogin returns 421 "Authentication required for
Account". Observed 2026-11-03: in that state GET /appleauth/auth/2sv/trust still
answers 204 and hands back a complete fresh credential set — trust token, session
token, session id, scnt, account country. Replaying that one call and persisting
those headers brings the session back, so a 421 does not have to mean auth.sh.

This is also the step pyicloud's own trust_session() performs and then throws
away: it catches the exception from the /accountLogin that follows the trust GET,
logs it, and returns False — leaving the trust token it just received unsaved.

Rate-limited by design. The replay costs one GET plus one POST /accountLogin, so
a repair that cannot succeed must not become the hammering that afm_session_hold
exists to stop: at most one attempt per REPAIR_COOLDOWN_SECS per machine.

    from afm_trust_repair import attempt_repair
    if attempt_repair(log=log):
        api = afm_pipeline.build_api()
"""
import json
import glob
import os
import time

import requests

APPLE_ID = os.environ.get("AFM_APPLE_ID", "joe@joemoser.com")
COOKIE_DIR = os.environ.get("AFM_COOKIE_DIR", os.path.expanduser("~/.pyicloud"))
COOLDOWN_PATH = os.environ.get(
    "AFM_REPAIR_STATE", os.path.expanduser("~/.pyicloud-trust-repair-last")
)
REPAIR_COOLDOWN_SECS = 1800
TRUST_TIMEOUT_SECS = 30
FALLBACK_AUTH_ENDPOINT = "https://idmsa.apple.com/appleauth/auth"
FALLBACK_HEADER_DATA = {
    "X-Apple-ID-Account-Country": "account_country",
    "X-Apple-ID-Session-Id": "session_id",
    "X-Apple-Session-Token": "session_token",
    "X-Apple-TwoSV-Trust-Token": "trust_token",
    "scnt": "scnt",
}


def header_data():
    try:
        from pyicloud.session import HEADER_DATA
        if isinstance(HEADER_DATA, dict) and HEADER_DATA:
            return HEADER_DATA
    except Exception:
        pass
    return FALLBACK_HEADER_DATA


def cooldown_remaining(now=None):
    """Seconds until another repair is allowed. 0 when a repair may run now."""
    now = time.time() if now is None else now
    try:
        with open(COOLDOWN_PATH) as handle:
            last = float(handle.read().strip())
    except Exception:
        return 0
    remaining = REPAIR_COOLDOWN_SECS - (now - last)
    return remaining if remaining > 0 else 0


def _record_attempt(now=None):
    try:
        with open(COOLDOWN_PATH, "w") as handle:
            handle.write(str(time.time() if now is None else now))
    except Exception:
        pass


def clear_cooldown():
    try:
        if os.path.exists(COOLDOWN_PATH):
            os.remove(COOLDOWN_PATH)
            return True
    except Exception:
        pass
    return False


def build_probe_service(cookie_dir=None, apple_id=None):
    """A PyiCloudService that loads the session from disk and authenticates nothing.

    Constructing the normal service calls /accountLogin, which is the call that is
    failing — so the override is what makes a repair possible at all.
    """
    from pyicloud import PyiCloudService

    class _ProbeService(PyiCloudService):
        def authenticate(self, force_refresh: bool = False, service=None) -> None:
            return

    return _ProbeService(
        apple_id or APPLE_ID,
        password=None,
        cookie_directory=cookie_dir or COOKIE_DIR,
    )


def session_dicts(api):
    """Every session-data dict this pyicloud build exposes, richest first.

    Some builds store response headers on api.session_data and others on
    api.session.data. Writing only one is how a trust token goes missing.
    """
    found = []
    for obj, attr in ((api, "session_data"), (getattr(api, "session", None), "data")):
        candidate = getattr(obj, attr, None)
        if isinstance(candidate, dict) and not any(candidate is d for d in found):
            found.append(candidate)
    return sorted(found, key=len, reverse=True)


def session_file_path(api, cookie_dir):
    for obj in (api, getattr(api, "session", None)):
        for attr in ("session_path", "_session_path"):
            path = getattr(obj, attr, None)
            if isinstance(path, str) and path:
                return path
    existing = sorted(glob.glob(os.path.join(cookie_dir, "*.session")))
    return existing[0] if existing else None


def auth_endpoint(api):
    for attr in ("_auth_endpoint", "auth_endpoint", "AUTH_ENDPOINT"):
        value = getattr(api, attr, None)
        if isinstance(value, str) and value:
            return value
    return FALLBACK_AUTH_ENDPOINT


def replay_trust(api, timeout=TRUST_TIMEOUT_SECS):
    """GET /2sv/trust, bypassing PyiCloudSession's error wrapper.

    The wrapper converts an error response into an exception and discards the
    body, so the raw response is the only way to tell a retryable failure from a
    refusal.
    """
    url = f"{auth_endpoint(api)}/2sv/trust"
    headers = api._get_auth_headers()
    return url, requests.Session.request(
        api.session, "GET", url, headers=headers, timeout=timeout
    )


def trust_token_header(response):
    for name in response.headers:
        if "trust-token" in name.lower():
            return name
    return None


def persist_headers(api, response, cookie_dir, log=print):
    """Write every mapped response header into each session dict and to disk."""
    mapping = header_data()
    dicts = session_dicts(api)
    stored = {}
    for header, key in mapping.items():
        value = response.headers.get(header)
        if value:
            stored[key] = value
            for data in dicts:
                data[key] = value
    if not stored:
        return False
    log(f"[repair] stored {', '.join(f'{k}({len(v)}B)' for k, v in sorted(stored.items()))}")

    path = session_file_path(api, cookie_dir)
    if not path:
        log("[repair] no .session file to write — session not persisted")
        return False
    merged = {}
    for data in reversed(dicts):
        merged.update(data)
    try:
        with open(path, "w") as handle:
            json.dump(merged, handle)
        log(f"[repair] session written to {path}")
    except Exception as exc:
        log(f"[repair] could not write {path}: {type(exc).__name__}: {exc}")
        return False

    try:
        cookies = api.session.cookies
        if hasattr(cookies, "save"):
            cookies.save(ignore_discard=True)
            log("[repair] cookie jar saved")
    except Exception as exc:
        log(f"[repair] cookie save failed: {type(exc).__name__}: {exc}")
    return True


def attempt_repair(api=None, cookie_dir=None, log=print, force=False):
    """Try to revive the on-disk session. True only if /accountLogin then works.

    Never raises: a repair runs on the failure path, where an exception would
    replace a diagnosable auth error with a confusing one.
    """
    cookie_dir = cookie_dir or COOKIE_DIR

    if not force:
        remaining = cooldown_remaining()
        if remaining:
            log(f"[repair] skipped — last attempt {REPAIR_COOLDOWN_SECS - remaining:.0f}s "
                f"ago, {remaining:.0f}s of cooldown left")
            return False
    _record_attempt()

    try:
        if api is None:
            api = build_probe_service(cookie_dir)
        if not any(session_dicts(api)):
            log("[repair] no session data on disk — nothing to replay")
            return False
        url, response = replay_trust(api)
    except Exception as exc:
        log(f"[repair] trust replay failed: {type(exc).__name__}: {exc}")
        return False

    log(f"[repair] GET {url} → {response.status_code}")
    header = trust_token_header(response)
    if not response.ok or not header:
        body = (response.text or "")[:300]
        log(f"[repair] no trust token returned (status {response.status_code})"
            + (f", body: {body!r}" if body else ""))
        return False

    if not persist_headers(api, response, cookie_dir, log=log):
        return False

    try:
        api._authenticate_with_token()
    except Exception as exc:
        log(f"[repair] /accountLogin still failing after trust: "
            f"{type(exc).__name__}: {exc}")
        return False

    log("[repair] /accountLogin OK — session repaired without SRP")
    clear_cooldown()
    return True
