#!/usr/bin/env bash
# FALLBACK self-deploy timer. The external deploy service is supposed to run `.auto-deploy` after
# it `git pull`s, but in practice it hasn't been — leaving the server stale on an old build. This
# installs a LaunchAgent that runs `.auto-deploy` every AUTO_DEPLOY_INTERVAL seconds (default 300),
# so the running server tracks the checked-out tree no matter what. It's safe/cheap: `.auto-deploy`
# → auto_deploy.sh is idempotent — a curl to /healthz + a no-op when the deployed build already
# equals HEAD, and a full rebuild + restart only when they differ. It does NOT pull (the external
# service still owns that). `make auto-deploy-timer-off` removes it.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

LABEL="$(channel_label __BUNDLE_ID__.autodeploy)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/__APP_NAME_LOWER__"
INTERVAL="${AUTO_DEPLOY_INTERVAL:-300}"
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

if [[ "${1:-}" == "off" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  ok "auto-deploy timer removed"
  exit 0
fi

c_bold "== auto-deploy fallback timer =="
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$REPO_ROOT/.auto-deploy</string></array>
  <key>WorkingDirectory</key><string>$REPO_ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>DASHBOARD_CHANNEL</key><string>$DASHBOARD_CHANNEL</string>
  </dict>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/autodeploy$CHANNEL_SUFFIX.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/autodeploy$CHANNEL_SUFFIX.err.log</string>
</dict>
</plist>
PLIST

reload_service
ok "auto-deploy timer installed — runs .auto-deploy every ${INTERVAL}s (no-op when already current)"
info "the server will now self-heal to the checked-out HEAD; check: make deploy-status / make auto-deploy-status"
info "turn off with: make auto-deploy-timer-off"
