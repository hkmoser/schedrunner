#!/usr/bin/env bash
# Set up the BigQuery sidecar: a Python venv + a KeepAlive LaunchAgent. Uses the
# machine's existing Application Default Credentials (no keys in the repo). No sudo.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

LABEL="$(channel_label com.joemoser.dashboard.bq)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/dashboard"
SIDE="$REPO_ROOT/bq_sidecar"
VENV="$SIDE/.venv"
PORT="${BQ_SIDECAR_PORT:-$CH_SIDECAR_PORT}"

command -v python3 >/dev/null 2>&1 || fail "python3 not found (install it, e.g. brew install python)"

c_bold "== BigQuery sidecar =="
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip >/dev/null
"$VENV/bin/pip" install --quiet -r "$SIDE/requirements.txt"
ok "python deps installed (google-cloud-bigquery, secret-manager)"

# Enable Google Secret Manager for the in-app Settings page (idempotent; skips cleanly if
# gcloud is absent or SECRETS_BACKEND=file). Never fails the sidecar setup.
bash "$REPO_ROOT/Deploy/setup_secrets.sh" || true

env_xml="$(plist_env_xml "BQ_SIDECAR_PORT=$PORT" "DASHBOARD_CHANNEL=$DASHBOARD_CHANNEL")"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$VENV/bin/python</string><string>$SIDE/app.py</string></array>
  <key>WorkingDirectory</key><string>$SIDE</string>
  <key>EnvironmentVariables</key>
  <dict>
$env_xml  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/bq$CHANNEL_SUFFIX.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/bq$CHANNEL_SUFFIX.err.log</string>
</dict>
</plist>
PLIST

reload_service

c_bold "== bq healthcheck =="
healthy=0
for _ in 1 2 3 4 5 6; do
  if curl -fsS --max-time 4 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 2
done
if [[ $healthy -eq 1 ]]; then
  ok "sidecar healthy on 127.0.0.1:${PORT}"
  # Exercise the real page (afm if a dataset is configured, else the generic query).
  endpoint="/query"; [[ -n "${BQ_DATASET:-}" ]] && endpoint="/afm"
  resp="$(curl -fsS --max-time 30 "http://127.0.0.1:${PORT}${endpoint}" 2>/dev/null || true)"
  err="$("$VENV/bin/python" -c 'import sys,json
try: print(json.load(sys.stdin).get("error","") or "")
except Exception: print("")' <<<"$resp" 2>/dev/null)"
  if [[ -n "$err" ]]; then
    warn "sidecar query (${endpoint}) errored:"
    info "$err"
    info "Logs: tail -f \"$LOG_DIR/bq$CHANNEL_SUFFIX.err.log\""
  else
    ok "sidecar query (${endpoint}) returned data"
  fi
else
  warn "sidecar not healthy yet — check: tail -f \"$LOG_DIR/bq$CHANNEL_SUFFIX.err.log\""
fi
