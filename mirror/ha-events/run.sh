#!/bin/bash
# run.sh — Start ha-events as a background service (called by schedrunner).
# Schedrunner cadence: interval|5  (every 5 min: relaunches main.py if it died,
# no-ops if already running). The atomic start lock below keeps overlapping ticks
# and the boot window to a single instance.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/Dropbox/Source/schedrunner/log"
LOG_FILE="$LOG_DIR/ha-events.log"
PID_FILE="$REPO_DIR/.ha-events.pid"
VENV="$REPO_DIR/.venv"
PYTHON="$VENV/bin/python"

mkdir -p "$LOG_DIR"

# --- logging helper ---
# One greppable line per lifecycle transition. Status is the first word:
#   SKIP    — another start was already in progress (no-op)
#   RUNNING — already up; nothing to do
#   START   — (re)launching main.py
#   OK      — launched and still alive after the liveness check (success)
#   FAILED  — could not start, or main.py died immediately (failure)
log() { printf '%s run.sh: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"; }

# --- atomic start lock (closes the boot-window double-start race) ---
# mkdir is atomic and portable (stock macOS has no flock(1)). The lock is held
# only for run.sh's brief execution, then released on exit — long enough to make
# the PID check + launch + PID write below a single critical section, even if
# schedrunner (or .auto-deploy) fires run.sh concurrently.
LOCK_DIR="$REPO_DIR/.ha-events.startlock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    # A lock older than 2 min means a previous run.sh died mid-start; steal it.
    if [ -n "$(find "$LOCK_DIR" -prune -mmin +2 2>/dev/null)" ]; then
        rmdir "$LOCK_DIR" 2>/dev/null || true
        mkdir "$LOCK_DIR" 2>/dev/null || { log "SKIP could not acquire start lock"; exit 0; }
    else
        log "SKIP another start already in progress"
        exit 0
    fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
# Catch anything that aborts under `set -e` (e.g. venv/pip failure) with a clear line.
trap 'rc=$?; log "FAILED run.sh aborted (line ${LINENO}, exit ${rc})"' ERR

# --- already running? ---
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log "RUNNING already up (pid $OLD_PID); nothing to do"
        exit 0
    fi
    log "RUNNING stale pid $OLD_PID not alive; will relaunch"
    rm -f "$PID_FILE"
fi

# --- venv / deps ---
if [ ! -x "$PYTHON" ]; then
    log "START creating venv"
    python3 -m venv "$VENV" >> "$LOG_FILE" 2>&1
fi
if ! "$PYTHON" -m pip install --quiet -r "$REPO_DIR/requirements.txt" >> "$LOG_FILE" 2>&1; then
    log "FAILED dependency install (pip); see log above"
    exit 1
fi

# --- .env must exist ---
if [ ! -f "$REPO_DIR/.env" ]; then
    log "FAILED .env missing at $REPO_DIR/.env — copy .env.template and fill it in"
    exit 1
fi

# --- launch ---
log "START launching main.py"
nohup "$PYTHON" "$REPO_DIR/main.py" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

# --- liveness check: did it actually come up, or crash on startup? ---
# Catches the common immediate failures (bad .env, import/config errors) that
# would otherwise be logged as a successful "start".
sleep 2
if kill -0 "$NEW_PID" 2>/dev/null; then
    log "OK ha-events started (pid $NEW_PID)"
else
    log "FAILED ha-events exited within 2s of launch (pid $NEW_PID); see log above"
    rm -f "$PID_FILE"
    exit 1
fi
