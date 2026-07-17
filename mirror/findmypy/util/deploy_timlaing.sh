#!/bin/bash
# One-step deploy: switches production venv to the timlaing/pyicloud fork.
# Creates a timestamped session backup and generates a matching rollback script.
#
# Prerequisites:
#   bash util/test_timlaing_auth.sh  must have completed successfully first.
#
# What this does:
#   1. Backs up ~/.pyicloud session → ~/.pyicloud-bak-TIMESTAMP
#   2. Backs up current pyicloud version info for rollback
#   3. Installs timlaing/pyicloud fork into production venv
#   4. Copies authenticated test session (~/.pyicloud-test) → ~/.pyicloud
#   5. Generates util/rollback_timlaing_TIMESTAMP.sh
#   6. Runs a quick smoke test
#
# Known data change after deploy:
#   The 'deviceName' column in BigQuery will change format for new rows:
#   OLD: "Joe's iPhone"
#   NEW: "iPhone 16 Pro: Joe's iPhone"
#   This is cosmetic — BQ views use deviceDisplayName for display, not deviceName.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PROD_VENV="$REPO_DIR/.venv"
TEST_COOKIE_DIR="$HOME/.pyicloud-test"
PROD_COOKIE_DIR="$HOME/.pyicloud"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_COOKIE_DIR="$HOME/.pyicloud-bak-$TS"
ROLLBACK_SCRIPT="$SCRIPT_DIR/rollback_timlaing_$TS.sh"

echo "=== timlaing/pyicloud deploy ==="
echo "Timestamp:       $TS"
echo "Production venv: $PROD_VENV"
echo "Cookie backup:   $BACKUP_COOKIE_DIR"
echo "Rollback script: $ROLLBACK_SCRIPT"
echo ""

# ── Pre-flight checks ─────────────────────────────────────────────────────────

if [ ! -f "$PROD_VENV/bin/python" ]; then
    echo "ERROR: Production venv not found at $PROD_VENV"
    exit 1
fi

if [ ! -d "$TEST_COOKIE_DIR" ] || [ -z "$(ls -A "$TEST_COOKIE_DIR" 2>/dev/null)" ]; then
    echo "ERROR: No test session found in $TEST_COOKIE_DIR"
    echo "Run bash util/test_timlaing_auth.sh first and complete 2FA."
    exit 1
fi

echo "[check] Pre-flight checks passed."
echo ""

# ── Capture current pyicloud info for rollback ────────────────────────────────

OLD_VERSION=$("$PROD_VENV/bin/pip" show pyicloud 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "unknown")
OLD_LOCATION=$("$PROD_VENV/bin/pip" show pyicloud 2>/dev/null | grep "^Location:" | awk '{print $2}' || echo "")
echo "[backup] Current pyicloud version: $OLD_VERSION"

# ── Back up session files ─────────────────────────────────────────────────────

if [ -d "$PROD_COOKIE_DIR" ]; then
    cp -r "$PROD_COOKIE_DIR" "$BACKUP_COOKIE_DIR"
    echo "[backup] Session backed up: $BACKUP_COOKIE_DIR"
else
    echo "[backup] No existing session at $PROD_COOKIE_DIR — nothing to back up."
    mkdir -p "$BACKUP_COOKIE_DIR"  # placeholder so rollback script is consistent
fi

# ── Generate rollback script ──────────────────────────────────────────────────

cat > "$ROLLBACK_SCRIPT" <<ROLLBACK
#!/bin/bash
# Auto-generated rollback for timlaing deploy at $TS
# Run this to undo the deploy and restore the previous state exactly.

set -e
PROD_VENV="$PROD_VENV"
BACKUP_COOKIE_DIR="$BACKUP_COOKIE_DIR"
PROD_COOKIE_DIR="$PROD_COOKIE_DIR"
OLD_VERSION="$OLD_VERSION"

echo "=== Rolling back timlaing deploy ($TS) ==="

echo "[rollback] Reinstalling pyicloud \$OLD_VERSION from PyPI..."
"\$PROD_VENV/bin/pip" install --quiet "pyicloud==\$OLD_VERSION"

echo "[rollback] Restoring session files..."
rm -rf "\$PROD_COOKIE_DIR"
cp -r "\$BACKUP_COOKIE_DIR" "\$PROD_COOKIE_DIR"

echo "[rollback] Done. Production venv and session restored to pre-deploy state."
echo "           Backup left in place at: \$BACKUP_COOKIE_DIR"
ROLLBACK

chmod +x "$ROLLBACK_SCRIPT"
echo "[rollback] Rollback script generated: $ROLLBACK_SCRIPT"
echo ""

# ── Install timlaing fork ─────────────────────────────────────────────────────

echo "[deploy] Installing pyicloud from PyPI into production venv..."
"$PROD_VENV/bin/pip" install --quiet --upgrade "pyicloud>=2.0"
NEW_VERSION=$("$PROD_VENV/bin/pip" show pyicloud 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "unknown")
echo "[deploy] Installed pyicloud $NEW_VERSION"

# ── Copy authenticated session ────────────────────────────────────────────────

echo "[deploy] Copying authenticated test session → $PROD_COOKIE_DIR ..."
rm -rf "$PROD_COOKIE_DIR"
cp -r "$TEST_COOKIE_DIR" "$PROD_COOKIE_DIR"
echo "[deploy] Session files:"
ls -la "$PROD_COOKIE_DIR/" 2>/dev/null || echo "  (empty)"

# ── Smoke test ────────────────────────────────────────────────────────────────

echo ""
echo "[test] Running smoke test (loading session, listing devices)..."
# Uses _SmokeTestService (mirrors _CronPyiCloudService) so that if Apple requires
# 2FA during the smoke test, we exit with an error instead of falling back to SRP
# and corrupting the freshly-copied session with invalid cookie state.
"$PROD_VENV/bin/python" - <<'PYEOF'
import os, sys
from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudAuthRequiredException

class _SmokeAuthRequired(Exception):
    """Raised instead of PyiCloudAuthRequiredException to avoid depending on
    the timlaing fork's constructor signature (which requires a response object)."""
    pass

class _SmokeTestService(PyiCloudService):
    """Never falls back to SRP — exits cleanly if token auth fails."""
    def _authenticate(self):
        try:
            self._authenticate_with_token()
        except Exception as exc:
            raise _SmokeAuthRequired(str(exc)) from exc

try:
    api = _SmokeTestService("joe@joemoser.com",
                            cookie_directory=os.path.expanduser("~/.pyicloud"))
except (PyiCloudFailedLoginException, PyiCloudAuthRequiredException, _SmokeAuthRequired) as exc:
    print(f"  AUTH ERROR: {exc}")
    print("  Session needs re-auth — run bash util/auth.sh, then re-run deploy.")
    print("  The session in ~/.pyicloud was NOT corrupted (SRP was not attempted).")
    sys.exit(1)
except Exception as exc:
    print(f"  ERROR: {exc}")
    sys.exit(1)

print(f"  requires_2fa: {api.requires_2fa}")
print(f"  requires_2sa: {api.requires_2sa}")

try:
    devices = list(api.devices)
    print(f"  Devices found: {len(devices)}")
    for d in devices[:5]:
        print(f"    str(dev) = '{d}'  |  model = {d.data.get('deviceModel','?')}")
    print("  SMOKE TEST PASSED")
except Exception as exc:
    print(f"  FindMy access failed: {exc}")
    sys.exit(1)
PYEOF

echo ""
echo "=== Deploy complete ==="
echo ""
echo "  New fork:        pyicloud $NEW_VERSION (timlaing)"
echo "  Session backup:  $BACKUP_COOKIE_DIR"
echo "  Rollback:        bash $ROLLBACK_SCRIPT"
echo ""
echo "NOTE: The 'deviceName' column in BigQuery will have a new format for new rows:"
echo "  OLD format: \"Joe's iPhone\""
echo "  NEW format: \"iPhone 16 Pro: Joe's iPhone\""
echo "  (cosmetic only — BQ views use deviceDisplayName for device identification)"
echo ""
echo "Run 'python afm_live.py' to verify the full pipeline."
