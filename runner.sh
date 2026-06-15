#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/scripts.conf"
RUNTIME_TRACKER="$SCRIPT_DIR/.last_run_times"
LOG_BASE="$SCRIPT_DIR/log/"

touch "$RUNTIME_TRACKER"

now=$(date +%s)
today=$(date +%Y-%m-%d)
current_time=$(date +%H:%M)
uptime_seconds=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 999999)

temp_file=$(mktemp)

# Helper functions to simulate associative array behavior
get_last_run() {
  grep -F "$1" "$RUNTIME_TRACKER" | cut -d'|' -f2
}

update_run_time() {
  echo "$1|$2" >> "$temp_file"
}

# Per-script locks prevent a script from overlapping itself when a run takes
# longer than its cadence. They live outside the repo (and Dropbox) so they are
# not synced or wiped by auto-deploy, and clear on reboot.
LOCK_BASE="/tmp/schedrunner-locks"
mkdir -p "$LOCK_BASE"

# acquire_lock <dir>: returns 0 if acquired, 1 if a previous run is still alive.
# A stale lock (recorded pid gone) is reclaimed.
acquire_lock() {
  local dir="$1" pid
  if mkdir "$dir" 2>/dev/null; then
    return 0
  fi
  pid=$(cat "$dir/pid" 2>/dev/null)
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  rm -rf "$dir"
  mkdir "$dir" 2>/dev/null
}

while IFS="|" read -r raw_cadence_type raw_cadence_value raw_script_path; do
  # Trim whitespace from each field
  cadence_type=$(echo "$raw_cadence_type" | xargs)
  cadence_value=$(echo "$raw_cadence_value" | xargs)
  script_path=$(echo "$raw_script_path" | xargs)

  # Skip comments or malformed lines
  [[ "$cadence_type" =~ ^#.*$ || -z "$cadence_type" || -z "$cadence_value" || -z "$script_path" ]] && continue

  run_script=false

  if [[ "$cadence_type" == "interval" ]]; then
    # Ensure cadence_value is a number
    if ! [[ "$cadence_value" =~ ^[0-9]+$ ]]; then
      echo "[WARN] Skipping script '$script_path': interval value '$cadence_value' is not a number"
      continue
    fi

    last_run=$(get_last_run "$script_path")
    last_run=${last_run:-0}
    elapsed=$(( (now - last_run) / 60 ))

    if [[ "$elapsed" -ge "$cadence_value" ]]; then
      run_script=true
    fi

  elif [[ "$cadence_type" == "daily" ]]; then
    # Ensure cadence_value is in HH:MM format
    if ! [[ "$cadence_value" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
      echo "[WARN] Skipping script '$script_path': daily time '$cadence_value' is not in HH:MM format"
      continue
    fi

    run_today_key="${today}_${script_path}"
    last_run_key=$(get_last_run "$run_today_key")

    if [[ "$current_time" == "$cadence_value" && -z "$last_run_key" ]]; then
      run_script=true
      update_run_time "$run_today_key" "$now"
    fi
  elif [[ "$cadence_type" == "startup" ]]; then
    if [[ "$uptime_seconds" -lt 90 ]]; then
      run_script=true
    fi
  else
    echo "[WARN] Unknown cadence type '$cadence_type' for script '$script_path'. Skipping."
    continue
  fi

  if [[ "$run_script" == true ]]; then
    log_path="$LOG_BASE$(basename "$script_path").log"
    lock_dir="$LOCK_BASE/$(echo "$script_path" | tr -c 'A-Za-z0-9._-' '_')"

    if acquire_lock "$lock_dir"; then
      # Launch detached so a slow or hung script can never block the runner or
      # the next tick. The subshell releases its own lock when it finishes.
      (
        trap 'rm -rf "$lock_dir"' EXIT
        echo "[$(date)] Running $script_path"
        # Invoke .sh scripts with explicit bash so execute bits aren't required
        # (Dropbox does not sync execute bits across devices).
        _first="${script_path%% *}"
        if [[ "$_first" == *.sh ]]; then
          bash $script_path
        else
          eval "$script_path"
        fi
        echo "[$(date)] Finished $script_path"
        echo "----------------------------------------"
      ) >> "$log_path" 2>&1 &
      echo "$!" > "$lock_dir/pid"
      disown
      update_run_time "$script_path" "$now"
    else
      # A previous run is still going — don't overlap. Keep the old timing so we
      # retry next tick once it frees up.
      echo "[$(date)] Skipped (still running): $script_path" >> "$log_path" 2>&1
      existing_run=$(get_last_run "$script_path")
      [[ -n "$existing_run" ]] && update_run_time "$script_path" "$existing_run"
    fi
  else
    existing_run=$(get_last_run "$script_path")
    [[ -n "$existing_run" ]] && update_run_time "$script_path" "$existing_run"
  fi

done < "$CONFIG_FILE"

# No `wait`: scripts run detached (the LaunchAgent sets AbandonProcessGroup), so
# the runner returns immediately every tick no matter how long a script takes.
mv "$temp_file" "$RUNTIME_TRACKER"