#!/bin/bash
# run_export.sh — Schedrunner wrapper for export_messages.py
#
# macOS Full Disk Access (FDA) is required to read ~/Library/Messages/chat.db.
# Terminal.app has FDA on this Mac. We open a Terminal session via Launch
# Services (open -a Terminal), which runs the script inside Terminal's process
# tree — subprocesses inherit Terminal's FDA entitlement.
#
# Called every 60 min by schedrunner. Output captured to schedrunner's log.
set -uo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK="/tmp/messages_export.lock"
INNER_LOG="/tmp/messages_export_out_$$.log"
DONE_FILE="/tmp/messages_export_done_$$"
RUNNER="/tmp/messages_export_runner_$$.sh"

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
trap 'rm -f "$LOCK" "$RUNNER"' EXIT INT TERM

echo "[$(ts)] messages-export: starting incremental export"

# Write a self-contained runner script that Terminal will execute.
# Terminal's FDA entitlement is inherited by all processes it spawns.
cat > "$RUNNER" << INNERSCRIPT
#!/bin/bash
python3 '${SCRIPT_DIR}/export_messages.py' > '${INNER_LOG}' 2>&1
printf '%s' \$? > '${DONE_FILE}'
exit
INNERSCRIPT
chmod +x "$RUNNER"

# Open in Terminal via Launch Services — works from launchd agent context
# without requiring osascript Automation permissions.
open -a Terminal "$RUNNER"

# Poll for the done marker (max 5 min)
waited=0
while [[ ! -f "$DONE_FILE" && $waited -lt 300 ]]; do
    sleep 2
    (( waited += 2 ))
done

# Stream Python output to stdout → schedrunner captures it to its log
if [[ -f "$INNER_LOG" ]]; then
    cat "$INNER_LOG"
    rm -f "$INNER_LOG"
else
    echo "[$(ts)] WARNING: no output captured from Python script (timed out?)"
fi

exit_status=$(cat "$DONE_FILE" 2>/dev/null | tr -d '[:space:]' || echo "1")
rm -f "$DONE_FILE"

echo "[$(ts)] messages-export: done (exit $exit_status)"
[[ "$exit_status" == "0" ]] || exit 1
