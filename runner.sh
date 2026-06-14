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
    (
      echo "[$(date)] Running $script_path"
      eval "$script_path"
      echo "[$(date)] Finished $script_path"
      echo "----------------------------------------"
    ) >> "$log_path" 2>&1 &

    update_run_time "$script_path" "$now"
  else
    existing_run=$(get_last_run "$script_path")
    [[ -n "$existing_run" ]] && update_run_time "$script_path" "$existing_run"
  fi

done < "$CONFIG_FILE"

wait  # Waits for all backgrounded tasks to complete
mv "$temp_file" "$RUNTIME_TRACKER"