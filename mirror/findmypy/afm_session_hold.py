#!/usr/bin/env python3
"""
afm_session_hold.py — dead-session hold for afm_live_daemon.

WHY THIS EXISTS
    The daemon runs under a KeepAlive LaunchAgent with ThrottleInterval=300.
    When Apple rejects the session, the daemon alerts and exits(1) — and launchd
    dutifully restarts it 5 minutes later, where afm_pipeline.build_api() calls
    /accountLogin with the same dead token. Forever. That is ~288 rejected auth
    attempts a day against an account Apple has already said "no" to.

    Per CLAUDE.md, that hammering is exactly what makes Apple rate-limit SRP and
    withhold trust tokens for 24h — so the restart loop actively sabotages the
    util/auth.sh run that is the only thing that can fix the outage. This module
    breaks that loop: once Apple has rejected the session, the daemon stops
    contacting Apple and only re-probes on an escalating schedule (15m -> 30m ->
    hourly), which is ~24 attempts/day instead of ~288.

    This does NOT fix a dead session. Nothing here restores tracking; only
    `bash util/auth.sh` (SRP + 2FA + trust) does. This only stops the daemon from
    making that recovery harder.

SELF-CLEARING (no manual state file to delete)
    The hold is keyed to a fingerprint of ~/.pyicloud (file names/sizes/mtimes).
    util/auth.py installs a new session directory with an atomic os.rename, so
    every file is new and the fingerprint necessarily changes. The next daemon
    restart sees a different fingerprint, drops the hold, and reconnects. The
    documented recovery procedure stays exactly "run auth.sh".

FAIL OPEN, ALWAYS
    Every function here swallows its own errors and resolves to "no hold". A
    missing, corrupt, truncated or unreadable state file means the daemon runs
    normally. A bug in this module must never be able to keep a working session
    offline — the worst it may do is fail to suppress a restart.

STDLIB ONLY
    No pyicloud, pandas or BigQuery imports, so this is importable (and testable)
    on any machine without credentials.
"""

import dataclasses
import hashlib
import json
import os
import time

# State file for the hold. Overridable for tests / alternate homes.
HOLD_STATE_PATH = os.environ.get(
    "AFM_SESSION_HOLD_FILE", os.path.expanduser("~/.pyicloud-daemon-session-dead")
)
# The session directory whose fingerprint keys the hold. auth.sh replaces this
# wholesale, which is what makes the hold self-clearing.
SESSION_DIR = os.environ.get("AFM_COOKIE_DIR", os.path.expanduser("~/.pyicloud"))

# Escalating re-probe delays after consecutive rejections: 15 min, 30 min, then
# hourly forever. Over 24h that is ~24 auth attempts (vs ~288 at ThrottleInterval
# =300), while still recovering within the hour if Apple's refusal was temporary.
PROBE_BACKOFF_SECS = (900, 1800, 3600)

# Keep the persisted reason bounded — it is only ever used for a log line.
_MAX_REASON_LEN = 200


@dataclasses.dataclass(frozen=True)
class HoldStatus:
    """Result of check_hold(). `active` True means: do NOT contact Apple."""

    active: bool
    reason: str = ""
    phase: str = ""
    rejections: int = 0
    retry_in: float = 0.0
    next_probe_at: float = 0.0
    # Why we are NOT holding, for logging: no-state, session-replaced,
    # probe-window, unreadable, fingerprint-unknown.
    detail: str = ""

    def describe(self):
        """One-line human summary for the daemon log."""
        if self.active:
            mins = self.retry_in / 60.0
            return (
                f"session marked dead ({self.rejections} consecutive rejection(s), "
                f"last at {self.phase or 'unknown'}: {self.reason or 'no detail'}) — "
                f"not contacting Apple; next probe in {mins:.0f} min. "
                f"Run `bash util/auth.sh` to fix."
            )
        if self.detail == "session-replaced":
            return "~/.pyicloud changed since the rejection (auth.sh ran) — hold dropped"
        if self.detail == "probe-window":
            return (
                f"re-probe window reached after {self.rejections} rejection(s) — "
                f"attempting one connection"
            )
        return "no dead-session hold"


def _state_path():
    return HOLD_STATE_PATH


def _session_dir():
    return SESSION_DIR


def session_fingerprint(path=None):
    """Fingerprint the session dir from file names/sizes/mtimes.

    Returns "" when the fingerprint cannot be established (dir missing, empty or
    unreadable). "" is the fail-open sentinel: callers treat an unknown
    fingerprint as "cannot prove the session is still the rejected one", so no
    hold is enforced.
    """
    directory = path if path is not None else _session_dir()
    try:
        entries = []
        with os.scandir(directory) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                st = entry.stat()
                entries.append((entry.name, st.st_size, st.st_mtime_ns))
        if not entries:
            return ""
        entries.sort()
        digest = hashlib.sha256()
        for name, size, mtime_ns in entries:
            digest.update(f"{name}\0{size}\0{mtime_ns}\0".encode("utf-8", "replace"))
        return digest.hexdigest()
    except Exception:
        return ""


def read_hold():
    """Return the persisted hold dict, or None if absent/corrupt/unreadable."""
    try:
        with open(_state_path()) as f:
            state = json.load(f)
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    # A state file without a usable rejection count is not actionable.
    try:
        rejections = int(state.get("rejections", 0))
    except Exception:
        return None
    if rejections < 1:
        return None
    state["rejections"] = rejections
    return state


def _backoff_for(rejections):
    idx = min(max(rejections, 1), len(PROBE_BACKOFF_SECS)) - 1
    return PROBE_BACKOFF_SECS[idx]


def record_rejection(reason="", phase="", now=None):
    """Record that Apple rejected the session, and arm/escalate the hold.

    Called just before the daemon exits on a session-death path. Consecutive
    rejections against the SAME session escalate the re-probe delay; a rejection
    against a different session (fingerprint changed, i.e. a fresh auth.sh
    session that Apple also refused) restarts the escalation at the first step.

    Never raises. Returns the state dict that was written, or None if it could
    not be persisted (in which case the daemon simply keeps its old behavior).
    """
    now = time.time() if now is None else now
    try:
        fingerprint = session_fingerprint()
        previous = read_hold()
        rejections = 1
        first_at = now
        if previous and previous.get("fingerprint") and fingerprint:
            if previous.get("fingerprint") == fingerprint:
                rejections = previous["rejections"] + 1
                first_at = previous.get("first_rejected_at", now)
        state = {
            "rejections": rejections,
            "first_rejected_at": first_at,
            "last_rejected_at": now,
            "next_probe_at": now + _backoff_for(rejections),
            "fingerprint": fingerprint,
            "reason": str(reason)[:_MAX_REASON_LEN],
            "phase": str(phase)[:_MAX_REASON_LEN],
        }
        tmp = _state_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, _state_path())  # atomic: never leave a half-written file
        return state
    except Exception:
        return None


def check_hold(now=None):
    """Decide whether the daemon may contact Apple.

    Returns a HoldStatus. `.active` True means the session is known-dead and the
    re-probe time has not arrived: skip startup entirely, contact nothing.

    Resolves to inactive (fail open) whenever anything is missing, unreadable or
    inconsistent, and clears the state when the session directory has been
    replaced (auth.sh).
    """
    now = time.time() if now is None else now
    try:
        state = read_hold()
        if state is None:
            return HoldStatus(active=False, detail="no-state")

        stored_fp = state.get("fingerprint") or ""
        current_fp = session_fingerprint()

        # Cannot prove the on-disk session is still the rejected one -> fail open.
        # Deliberately do NOT clear: if Apple rejects again, record_rejection
        # keeps escalating rather than restarting the ladder.
        if not stored_fp or not current_fp:
            return HoldStatus(active=False, detail="fingerprint-unknown")

        # auth.sh installed a new session dir (atomic rename => new mtimes).
        if stored_fp != current_fp:
            clear_hold()
            return HoldStatus(active=False, detail="session-replaced")

        try:
            next_probe_at = float(state.get("next_probe_at", 0.0))
        except Exception:
            return HoldStatus(active=False, detail="unreadable")

        rejections = state["rejections"]
        reason = str(state.get("reason", ""))
        phase = str(state.get("phase", ""))

        if now >= next_probe_at:
            # Probe window: allow exactly this one attempt. State is left in
            # place so a fresh rejection escalates instead of resetting.
            return HoldStatus(
                active=False,
                detail="probe-window",
                rejections=rejections,
                reason=reason,
                phase=phase,
                next_probe_at=next_probe_at,
            )

        return HoldStatus(
            active=True,
            reason=reason,
            phase=phase,
            rejections=rejections,
            retry_in=max(0.0, next_probe_at - now),
            next_probe_at=next_probe_at,
        )
    except Exception:
        # Any unforeseen failure in this module must never keep a working
        # session offline.
        return HoldStatus(active=False, detail="unreadable")


def clear_hold():
    """Drop the hold (session is healthy again). Never raises. True if removed."""
    try:
        os.remove(_state_path())
        return True
    except Exception:
        return False
