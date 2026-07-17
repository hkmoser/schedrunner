#!/usr/bin/env bash
# Build the web bundle + server and (re)install the KeepAlive LaunchAgent.
# No sudo here, so this is safe for automatic/unattended `make update`.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

LABEL="$(channel_label __BUNDLE_ID__.server)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/__APP_NAME_LOWER__"
BIN="$REPO_ROOT/Server/.build/release/Run"
PORT="${PORT:-$CH_SERVER_PORT}"
WEB_DIST="${WEB_DIST:-$REPO_ROOT/Web/dist}"
# The server talks to ITS channel's sidecar (stable :8099 / next :8100) unless overridden.
BQ_SIDECAR_URL="${BQ_SIDECAR_URL:-http://127.0.0.1:$CH_SIDECAR_PORT}"
UID_NUM="$(id -u)"
# Short git SHA of what we're deploying, surfaced at /healthz so you can confirm the
# running server is the build you expect (answers "did my deploy actually take?").
GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# Self-heal the checkout's channel pin: a worktree deployed with an explicit CHANNEL=next
# BEFORE the pin feature existed has no Deploy/.channel — so later flag-less commands there
# (make ios-deploy, make update) silently ran as stable and built/published stable artifacts
# into the next stack. Writing the pin on any explicit non-stable deploy closes that gap.
if [[ "$DASHBOARD_CHANNEL" != "stable" && ! -f "$REPO_ROOT/Deploy/.channel" ]]; then
  printf '%s\n' "$DASHBOARD_CHANNEL" > "$REPO_ROOT/Deploy/.channel"
  ok "pinned checkout channel: Deploy/.channel = $DASHBOARD_CHANNEL"
fi

c_bold "== build web =="
# Resolve npm into PATH before use — launchd/cron environments don't source the user's
# shell profile, so nvm/Homebrew Node is absent from the bare PATH.
resolve_node || fail "Node/npm not found — install via Homebrew (\`brew install node\`) or nvm. Current PATH: $PATH"
info "using npm: $(command -v npm) ($(npm --version 2>/dev/null || echo unknown))"
# APP_BUILD stamps the bundle so the loaded version is visible in the app (drawer footer);
# APP_BUILD_TIME records when this bundle was built/deployed, shown as a date/time under it.
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
( cd "$REPO_ROOT/Web" && (npm ci || npm install) && APP_BUILD="$GIT_SHA" APP_BUILD_TIME="$BUILD_TIME" APP_CHANNEL="$DASHBOARD_CHANNEL" npm run build )
ok "web bundle at $WEB_DIST"
# A web rebuild emptied Web/dist — re-publish the native iOS OTA payload if one was built,
# so a previously-installed /app/ install URL keeps working after `make update`.
publish_app_bundle

c_bold "== build server =="
# The Vapor server is the ONLY Swift the deploy compiles — the native iOS app
# (App/) is never built here. A `-c release` build is slow, so skip it when nothing
# server-side changed: routine PWA/content updates then just rebuild the web bundle.
# Rebuild only when the binary is missing, when any Server source OR bundled Template
# changed since the last successful build, or when forced. The editable templates
# live under Server/Sources, so a template edit correctly triggers a rebuild.
#   WEB_ONLY=1           -> never build the server (used by `make update-web`)
#   FORCE_SERVER_BUILD=1 -> always build the server (used by `make update-server`)
STAMP="$REPO_ROOT/Server/.build/release/.deploy-build-stamp"
# CONTENT hash of everything that affects the binary (sources + manifest). The old detection used
# file mtime (find -newer), which was UNRELIABLE: a git pull/merge can leave file mtimes equal to
# or older than the stamp (1-second resolution, merge-commit checkout timing), so a needed rebuild
# got skipped and server changes silently never deployed. Hashing the actual content rebuilds
# whenever the source truly changes — independent of mtime, clock, or how git updated the tree.
server_src_hash() {
  local paths=("$REPO_ROOT/Server/Sources" "$REPO_ROOT/Server/Package.swift")
  [[ -f "$REPO_ROOT/Server/Package.resolved" ]] && paths+=("$REPO_ROOT/Server/Package.resolved")
  # `|| true` so a transient find/shasum hiccup can't abort the deploy under `set -e`/pipefail;
  # an empty hash just forces a (safe) rebuild rather than skipping one.
  { find "${paths[@]}" -type f 2>/dev/null \
      | LC_ALL=C sort \
      | while IFS= read -r f; do shasum "$f" 2>/dev/null; done \
      | shasum | awk '{print $1}'; } || true
}
CUR_HASH="$(server_src_hash)"
need_build=0; build_reason=""
if [[ "${WEB_ONLY:-0}" == "1" && -x "$BIN" ]]; then
  build_reason="web-only: server build skipped by request"
elif [[ "${FORCE_SERVER_BUILD:-0}" == "1" ]]; then
  need_build=1; build_reason="forced (FORCE_SERVER_BUILD=1)"
elif [[ ! -x "$BIN" ]]; then
  need_build=1; build_reason="no server binary yet"
elif [[ ! -f "$STAMP" ]]; then
  need_build=1; build_reason="no prior build stamp"
elif [[ "$(cat "$STAMP" 2>/dev/null)" != "$CUR_HASH" ]]; then
  need_build=1; build_reason="Server sources changed (content hash differs)"
else
  build_reason="Server unchanged since last build"
fi

if [[ "$need_build" == "1" ]]; then
  info "building server ($build_reason)"
  # NB: do NOT swallow a build failure — a failed compile here used to fall through
  # and keep the previously-built binary running, so server-side changes (nav, new
  # pages, template edits) silently never appeared. Fail loudly instead.
  if ! ( cd "$REPO_ROOT/Server" && swift build -c release ); then
    fail "server build FAILED — fix the Swift errors above and re-run 'make update'. The previously-built server is still running, which is why your changes aren't showing."
  fi
  [[ -x "$BIN" ]] || fail "server binary not found at $BIN"
  mkdir -p "$(dirname "$STAMP")"; printf '%s\n' "$CUR_HASH" > "$STAMP"   # record what we built
  ok "server built"
else
  [[ -x "$BIN" ]] || fail "server binary not found at $BIN (can't skip the first build)"
  ok "$build_reason — reusing existing binary (no swift build; FORCE_SERVER_BUILD=1 to force)"
fi

c_bold "== install LaunchAgent =="
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

# EnvironmentVariables block from Deploy/.env plus computed paths.
env_xml=""
add_env() { env_xml+="      <key>$1</key><string>$2</string>"$'\n'; }
add_env "WEB_DIST" "$WEB_DIST"
add_env "PORT" "$PORT"
add_env "BQ_SIDECAR_URL" "$BQ_SIDECAR_URL"   # channel's own sidecar (stable :8099 / next :8100)
add_env "DASHBOARD_CHANNEL" "$DASHBOARD_CHANNEL"
add_env "DASHBOARD_BUILD" "$GIT_SHA"
add_env "DASHBOARD_REPO" "$REPO_ROOT"   # so the Deploy page can read build/*.status + git HEAD
# Surface the Apple Team ID + bundle id so APNs can auto-derive them (just drop the .p8).
ios_team="$(detect_team_id 2>/dev/null || true)"
[[ "$ios_team" =~ ^[A-Z0-9]{10}$ ]] && add_env "DEVELOPMENT_TEAM" "$ios_team"
ios_bundle="$(grep -E '^[[:space:]]*PRODUCT_BUNDLE_IDENTIFIER[[:space:]]*=' "$REPO_ROOT/Config.xcconfig" 2>/dev/null | head -n1 | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//')"
# The next channel's native app uses a suffixed bundle id (gen_ios_config), so this server's
# APNs topic must match it — otherwise pushes/Live Activity updates target the wrong app.
[[ -n "$ios_bundle" && "$DASHBOARD_CHANNEL" != "stable" ]] && ios_bundle="$ios_bundle.$DASHBOARD_CHANNEL"
[[ -n "$ios_bundle" ]] && add_env "PRODUCT_BUNDLE_IDENTIFIER" "$ios_bundle"
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *"="* ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    key="$(echo "$key" | xargs)"
    # Skip keys we compute per-channel above (avoid duplicate plist keys).
    case "$key" in ""|WEB_DIST|PORT|BQ_SIDECAR_URL|DASHBOARD_CHANNEL) continue ;; esac
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    # XML-escape & < >
    val="${val//&/&amp;}"; val="${val//</&lt;}"; val="${val//>/&gt;}"
    add_env "$key" "$val"
  done < "$ENV_FILE"
fi

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$BIN</string><string>serve</string><string>--env</string><string>production</string></array>
  <key>WorkingDirectory</key><string>$REPO_ROOT/Server</string>
  <key>EnvironmentVariables</key>
  <dict>
$env_xml  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/server$CHANNEL_SUFFIX.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/server$CHANNEL_SUFFIX.err.log</string>
</dict>
</plist>
PLIST

reload_service

c_bold "== local healthcheck =="
healthy=0
for _ in 1 2 3 4 5 6 7 8; do
  if curl -fsS --max-time 4 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 2
done
if [[ $healthy -eq 1 ]]; then
  ok "server healthy on 127.0.0.1:${PORT}"
  running="$(curl -fsS --max-time 4 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null || true)"
  info "deployed build: $GIT_SHA"
  case "$running" in
    *"$GIT_SHA"*) ok "running server reports build $GIT_SHA" ;;
    *) warn "running /healthz did not report build $GIT_SHA — it may still be the old process. Response: $running" ;;
  esac
else
  fail "server not healthy yet — check: tail -f \"$LOG_DIR/server$CHANNEL_SUFFIX.err.log\""
fi
