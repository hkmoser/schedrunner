#!/bin/bash
# Sets up an isolated test venv with the timlaing/pyicloud fork and runs the auth test.
# Never touches the production venv or existing session files.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$REPO_DIR/.venv-test"

echo "=== Timlaing pyicloud fork — isolated auth test ==="
echo "Production venv:      $REPO_DIR/.venv  (untouched)"
echo "Test venv:            $VENV  (isolated)"
echo "Test cookie dir:      ~/.pyicloud-test  (isolated)"
echo "Production cookies:   ~/.pyicloud  (untouched)"
echo ""

# Create isolated venv if it doesn't exist
if [ ! -f "$VENV/bin/python" ]; then
    echo "[setup] Creating isolated test venv..."
    python3 -m venv "$VENV"
fi

# Install pyicloud from PyPI (2.x = timlaing fork, properly versioned)
echo "[setup] Installing pyicloud from PyPI into test venv..."
"$VENV/bin/pip" install --quiet --upgrade "pyicloud>=2.0"
echo "[setup] Installed: $("$VENV/bin/pip" show pyicloud | grep -E '^(Name|Version|Location)')"
echo ""

echo "[setup] Running auth test..."
"$VENV/bin/python" "$SCRIPT_DIR/test_timlaing_auth.py"
