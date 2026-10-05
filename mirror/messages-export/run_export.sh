#!/bin/bash
# run_export.sh — headless wrapper for the exporters (NO Terminal window).
#
#   run_export.sh                     # Messages (export_messages.py)
#   run_export.sh whatsapp_export.py  # WhatsApp (or use run_whatsapp_export.sh)
#
# This just runs python3 directly. FDA is NOT a "Python" requirement — it's only
# needed to read the TCC-protected stores (~/Library/Messages/chat.db and the
# AddressBook DB). If the process that launches this script (your scheduler, or a
# terminal/IDE) already has Full Disk Access, child processes inherit it and this
# works with nothing further to configure.
#
# ONLY if a run fails on permissions do you need to grant FDA to something in the
# launch chain (the exact interpreter path is printed below on failure). For a
# fully unattended, window-free scheduler that needs just one grant, you can use
# the optional LaunchAgent: ./install_launchagent.sh
set -uo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$(command -v python3)}"
EXPORTER="${1:-export_messages.py}"
NAME="${EXPORTER%.py}"
LOCK="/tmp/${NAME}.lock"
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Single-instance guard
if [[ -f "$LOCK" ]]; then
    pid=$(cat "$LOCK" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
        echo "[$(ts)] already running (pid $pid) — skipping"
        exit 0
    fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

echo "[$(ts)] $NAME: starting incremental export (python: $PYTHON)"

# Run directly — no Terminal, no window. Output goes to stdout, which the
# scheduler (or the LaunchAgent's log file) captures.
"$PYTHON" "$SCRIPT_DIR/$EXPORTER"
status=$?

# Exit 2 means the source database couldn't be read (Full Disk Access). Anything else is a
# different problem; the Python output above says what.
if [[ $status -eq 2 ]]; then
    real_py="$("$PYTHON" -c 'import sys; print(sys.executable)' 2>/dev/null || echo "$PYTHON")"
    echo "[$(ts)] export failed (exit $status): the source database is not readable."
    echo "[$(ts)] Grant Full Disk Access to the interpreter:"
    echo "         System Settings → Privacy & Security → Full Disk Access → + →"
    echo "         $real_py"
    echo "         (reveal it in Finder: open -R \"$real_py\")"
elif [[ $status -ne 0 ]]; then
    echo "[$(ts)] export failed (exit $status) — see the error above."
fi

echo "[$(ts)] $NAME: done (exit $status)"
[[ $status -eq 0 ]] || exit 1
