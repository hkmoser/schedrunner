#!/usr/bin/env python3
"""
boot-notify: Post-boot health check + Pushcut notification.

Fired at startup via schedrunner (startup|ignored|.../run.sh).
Sleeps to let services settle, then checks key daemons and sends
a Pushcut push to the "Mac Boot" notification.
"""

import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

PUSHCUT_API_KEY = "QFNjvttld5Fem3eor-5pd"
PUSHCUT_NOTIFICATION = "Mac Boot"   # Set this up in the Pushcut app

# Seconds to wait after boot before checking — lets Tailscale, LaunchAgents,
# and the VirtualBox VM finish initialising before we declare anything broken.
SETTLE_SECS = 180

# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd, **kw):
    """Run a shell command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **kw)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def push(title: str, text: str) -> None:
    name = PUSHCUT_NOTIFICATION.replace(" ", "%20")
    url  = f"https://api.pushcut.io/{PUSHCUT_API_KEY}/notifications/{name}"
    payload = json.dumps({"title": title, "text": text}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[boot-notify] Pushcut sent ({r.status}): {title}")
    except urllib.error.URLError as e:
        print(f"[boot-notify] Pushcut FAILED: {e}")


# ── Checks ────────────────────────────────────────────────────────────────────

def check_internet() -> tuple[bool, str]:
    rc, _, _ = run(["curl", "-sf", "--max-time", "8", "--head", "https://apple.com"])
    return rc == 0, "Internet"


def check_tailscale() -> tuple[bool, str]:
    rc, out, _ = run(["tailscale", "status", "--json"])
    if rc != 0:
        return False, "Tailscale"
    try:
        state = json.loads(out).get("BackendState", "")
        return state == "Running", "Tailscale"
    except Exception:
        return False, "Tailscale"


def check_launchagent(label: str, friendly: str) -> tuple[bool, str]:
    rc, _, _ = run(["launchctl", "list", label])
    return rc == 0, friendly


def check_process(pattern: str, friendly: str) -> tuple[bool, str]:
    rc, _, _ = run(["pgrep", "-f", pattern])
    return rc == 0, friendly


def check_ssh_github() -> tuple[bool, str]:
    # Non-interactive SSH test — exit code 1 = auth OK (PermitOpen denial),
    # code 255 = network/key failure.
    rc, _, _ = run(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
         "-o", "ConnectTimeout=8", "-T", "git@github.com"],
    )
    # GitHub returns 1 with "successfully authenticated" message when auth works
    return rc == 1, "SSH/GitHub"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"[boot-notify] started at {datetime.now():%Y-%m-%d %H:%M:%S}, "
          f"sleeping {SETTLE_SECS}s for services to settle…")
    time.sleep(SETTLE_SECS)

    checks = [
        check_internet(),
        check_tailscale(),
        check_launchagent("com.joemoser.runner",          "schedrunner"),
        check_launchagent("com.joemoser.dashboard.server","iOS-Shell sidecar"),
        check_launchagent("com.smart-journal.server",     "smart-journal"),
        check_process("ha-events",                        "ha-events"),
        check_process("VBoxHeadless",                     "VirtualBox HA VM"),
        check_ssh_github(),
    ]

    ok   = [(passed, name) for passed, name in checks if passed]
    warn = [(passed, name) for passed, name in checks if not passed]

    lines = []
    for _, name in warn:
        lines.append(f"⚠️ {name}")
    for _, name in ok:
        lines.append(f"✅ {name}")

    # Uptime
    rc, out, _ = run(["uptime"])
    uptime_str = out.split("up ")[-1].split(",")[0].strip() if "up " in out else "unknown"
    lines.append(f"Uptime: {uptime_str}")

    title = ("✅ Mac mini back online" if not warn
             else f"⚠️ Mac mini needs attention ({len(warn)} issue{'s' if len(warn)>1 else ''})")
    text  = "\n".join(lines)

    print(f"[boot-notify] {title}")
    for line in lines:
        print(f"  {line}")

    push(title, text)


if __name__ == "__main__":
    main()
