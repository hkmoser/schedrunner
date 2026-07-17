#!/usr/bin/env bash
# Shared helpers for the deploy scripts. Sourced, not executed.

set -euo pipefail

# Repo root (Deploy/ is one level down).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/Deploy/.env"

# launchd runs jobs with a bare PATH (/usr/bin:/bin:...), so tools installed via Homebrew —
# xcodegen, node, brew itself — vanish when a deploy is triggered by the auto-deploy timer or
# /deploy_kick instead of an interactive shell ("xcodegen not found and Homebrew unavailable").
# Make every deploy script see the same tools regardless of how it was invoked.
case ":$PATH:" in
  *":/opt/homebrew/bin:"*) ;;
  *) export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" ;;
esac

# --- release channel (stable | next) ---------------------------------------------------------
# Two full stacks can run side by side on one Mac, each installable as its own PWA:
#   stable (default): server :8080, sidecar :8099, TLS :443,  labels unsuffixed  → https://$TS_HOST/
#   next            : server :8081, sidecar :8100, TLS :8443, labels '…-next'    → https://$TS_HOST:8443/
# Everything below is DERIVED from DASHBOARD_CHANNEL so the two never collide. Stable keeps its
# EXACT current values (empty suffix, same ports) — this block is purely additive, a no-op unless
# DASHBOARD_CHANNEL=next. Next lives in its own git worktree, so builds/dist/stamps are separate too.
#
# The channel is PINNED to the checkout via Deploy/.channel (written by setup_channel.sh in the
# next worktree, absent in the stable checkout). Reading it here — before any derivation — means a
# bare `make update`, the auto-deploy timer, or any hook run inside the next worktree stays in the
# next channel even when DASHBOARD_CHANNEL isn't in the environment. Without this pin, a forgotten
# CHANNEL=next flag (or launchd's bare env) would run as 'stable' FROM the next tree and overwrite
# stable's LaunchAgents with next's code. An explicit env var still wins (deliberate override).
if [[ -z "${DASHBOARD_CHANNEL:-}" && -f "$REPO_ROOT/Deploy/.channel" ]]; then
  DASHBOARD_CHANNEL="$(tr -d '[:space:]' < "$REPO_ROOT/Deploy/.channel")"
fi
DASHBOARD_CHANNEL="${DASHBOARD_CHANNEL:-stable}"
case "$DASHBOARD_CHANNEL" in
  stable) CHANNEL_SUFFIX="";      CH_SERVER_PORT=8080; CH_SIDECAR_PORT=8099; CH_TLS_PORT=443 ;;
  next)   CHANNEL_SUFFIX="-next"; CH_SERVER_PORT=8081; CH_SIDECAR_PORT=8100; CH_TLS_PORT=8443 ;;
  *) c_red 2>/dev/null "unknown DASHBOARD_CHANNEL='$DASHBOARD_CHANNEL' (use stable|next)"; exit 1 ;;
esac
# Append the channel suffix to a base launchd label (no-op on stable).
channel_label() { printf "%s%s" "$1" "$CHANNEL_SUFFIX"; }

c_red()   { printf "\033[31m%s\033[0m\n" "$*"; }
c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
c_bold()  { printf "\033[1m%s\033[0m\n" "$*"; }

fail() { c_red "✗ $*"; exit 1; }
ok()   { c_green "✓ $*"; }
warn() { c_yellow "! $*"; }
info() { printf "  %s\n" "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

# Augment PATH with Node.js/npm. When scripts run via launchd or cron the user's
# shell profile is not sourced, so nvm/Homebrew Node is invisible. Tries in order:
#   1. npm already on PATH (interactive shells, sudo -E, etc.)
#   2. Homebrew — Apple Silicon (/opt/homebrew) then Intel (/usr/local)
#   3. nvm — resolves the 'default' alias, falls back to the latest installed version
# Exports into PATH on success; returns 1 if no Node found.
resolve_node() {
  have npm && return 0
  local p
  for p in /opt/homebrew/bin /usr/local/bin; do
    [[ -x "$p/npm" ]] && { export PATH="$p:$PATH"; return 0; }
  done
  local nvm_dir="${NVM_DIR:-$HOME/.nvm}"
  if [[ -d "$nvm_dir/versions/node" ]]; then
    local ver=""
    if [[ -f "$nvm_dir/alias/default" ]]; then
      ver="$(cat "$nvm_dir/alias/default" | tr -d '[:space:]')"
      # resolve one level of alias indirection (e.g. "lts/*" → "lts/iron")
      [[ -f "$nvm_dir/alias/$ver" ]] && ver="$(cat "$nvm_dir/alias/$ver" | tr -d '[:space:]')"
      ver="${ver#v}"
    fi
    if [[ -n "$ver" && -x "$nvm_dir/versions/node/v$ver/bin/npm" ]]; then
      export PATH="$nvm_dir/versions/node/v$ver/bin:$PATH"; return 0
    fi
    # Fall back to the latest installed version
    local best; best="$(ls -v "$nvm_dir/versions/node/" 2>/dev/null | tail -1)"
    [[ -n "$best" && -x "$nvm_dir/versions/node/$best/bin/npm" ]] && {
      export PATH="$nvm_dir/versions/node/$best/bin:$PATH"; return 0
    }
  fi
  return 1
}

# Best-effort auto-detect the 10-char Apple Developer Team ID so the user never has to
# look it up / hand-edit Config.xcconfig. Tries, in order: an already-installed
# provisioning profile, then the OU of the "Apple Development" signing certificate (which
# *is* the Team ID). Prints nothing if it can't tell. macOS only (security/plutil/openssl).
detect_team_id() {
  local t p
  # 1) TeamIdentifier from any installed provisioning profile (present after one build).
  if have security && have plutil; then
    for p in "$HOME/Library/MobileDevice/Provisioning Profiles/"*.mobileprovision; do
      [[ -f "$p" ]] || continue
      t="$(security cms -D -i "$p" 2>/dev/null | plutil -extract TeamIdentifier.0 raw - 2>/dev/null)"
      if [[ "$t" =~ ^[A-Z0-9]{10}$ ]]; then printf '%s' "$t"; return 0; fi
    done
  fi
  # 2) OU of the Apple Development cert = Team ID (works before any profile exists).
  if have security && have openssl; then
    t="$(security find-certificate -a -c 'Apple Develop' -p 2>/dev/null \
        | openssl x509 -noout -subject -nameopt multiline 2>/dev/null \
        | sed -n 's/.*organizationalUnitName *= *//p' | head -n1 | tr -d '[:space:]')"
    if [[ "$t" =~ ^[A-Z0-9]{10}$ ]]; then printf '%s' "$t"; return 0; fi
  fi
  return 1
}

# Resolve the Tailscale CLI. On macOS the GUI app (App Store / standalone) does NOT
# put `tailscale` on PATH — the CLI lives inside the app bundle. Sets TAILSCALE_BIN.
TAILSCALE_BIN=""
resolve_tailscale() {
  if [[ -n "$TAILSCALE_BIN" ]]; then return 0; fi
  if command -v tailscale >/dev/null 2>&1; then TAILSCALE_BIN="tailscale"; return 0; fi
  local candidates=(
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    "/Applications/Tailscale.app/Contents/MacOS/tailscale"
    "/opt/homebrew/bin/tailscale"
    "/usr/local/bin/tailscale"
  )
  local p
  for p in "${candidates[@]}"; do
    [[ -x "$p" ]] && { TAILSCALE_BIN="$p"; return 0; }
  done
  return 1
}

# Run the resolved tailscale CLI.
ts() { "$TAILSCALE_BIN" "$@"; }

# Load Deploy/.env into the environment if present. Parses KEY=VALUE line by line
# (does NOT `source`), so values may contain spaces and need no quoting, e.g.
#   DASHBOARD_LOCATION=San Francisco
load_env() {
  [[ -f "$ENV_FILE" ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"                       # strip trailing CR (CRLF files)
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" != *"="* ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"       # ltrim key
    key="${key%"${key##*[![:space:]]}"}"       # rtrim key
    val="${val%\"}"; val="${val#\"}"           # strip surrounding double quotes
    val="${val%\'}"; val="${val#\'}"           # strip surrounding single quotes
    export "$key=$val"
  done < "$ENV_FILE"

  # Deploy/.env describes the STABLE stack (PORT=8080, BQ_SIDECAR_PORT=8099). For any other
  # channel those values must NOT apply, or `${PORT:-…}` in the scripts would pin next onto
  # stable's ports and collide. Force the channel's own ports here so every script that calls
  # load_env gets them automatically. Stable is untouched (keeps .env's values).
  if [[ "$DASHBOARD_CHANNEL" != "stable" ]]; then
    export PORT="$CH_SERVER_PORT"
    export BQ_SIDECAR_PORT="$CH_SIDECAR_PORT"
    export BQ_SIDECAR_URL="http://127.0.0.1:$CH_SIDECAR_PORT"
  fi
}

# Read an app-entered setting (Settings page → Secret Manager / local file) so deploy SCRIPTS
# can honor in-app toggles, not just the Vapor server. Precedence: a real env var / Deploy/.env
# wins (explicit override), else the sidecar's resolved settings, else empty. The sidecar reads
# whichever backend is active (Secret Manager or the 0600 file), so this works for both.
app_setting() {
  local key="$1" base v
  if [[ -n "${!key:-}" ]]; then printf '%s' "${!key}"; return 0; fi
  base="${BQ_SIDECAR_URL:-http://127.0.0.1:8099}"
  v="$(curl -fsS --max-time 4 "$base/settings_resolved" 2>/dev/null \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('values',{}).get('$key',''))" 2>/dev/null || true)"
  # Fallback to the 0600 local store directly, so it works even if the sidecar isn't up yet.
  if [[ -z "$v" ]]; then
    local f="${SECRETS_FILE:-$HOME/.config/dashboard/secrets.json}"
    [[ -f "$f" ]] && v="$(python3 -c "import json;print(json.load(open('$f')).get('$key',''))" 2>/dev/null || true)"
  fi
  printf '%s' "$v"
}

# Persist an app setting to the 0600 local secrets file (the same store app_setting reads as its
# file fallback), so a value entered once is available non-interactively forever after — even if the
# sidecar is down. Also POSTs to the sidecar if it's reachable, so Secret Manager gets it too.
persist_setting() {
  local key="$1" val="$2"
  local f="${SECRETS_FILE:-$HOME/.config/dashboard/secrets.json}"
  mkdir -p "$(dirname "$f")"
  KEY="$key" VAL="$val" FILE="$f" python3 - <<'PY' 2>/dev/null || return 0
import json, os
f = os.environ["FILE"]
try:
    d = json.load(open(f))
    if not isinstance(d, dict): d = {}
except Exception:
    d = {}
d[os.environ["KEY"]] = os.environ["VAL"]
os.umask(0o077)
json.dump(d, open(f, "w"))
os.chmod(f, 0o600)
PY
  local base="${BQ_SIDECAR_URL:-http://127.0.0.1:8099}"
  KEY="$key" VAL="$val" python3 -c 'import json,os;print(json.dumps({"items":[{"key":os.environ["KEY"],"value":os.environ["VAL"]}]}))' 2>/dev/null \
    | curl -fsS --max-time 5 -X POST "$base/settings" -H 'Content-Type: application/json' --data @- >/dev/null 2>&1 || true
}

# Truthy test for an app setting / env flag: 1/true/yes/on (any case) → enabled (returns 0).
app_flag_on() {
  case "$(printf '%s' "$(app_setting "$1")" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

require_env() {
  local missing=0
  for var in "$@"; do
    if [[ -z "${!var:-}" ]]; then
      warn "missing required env: $var"
      missing=1
    fi
  done
  return $missing
}

# Publish the native iOS OTA payload into the served web bundle.
# The canonical artifacts live in build/ipa/ (persistent, git-ignored, where xcodebuild
# exports them + the script writes manifest.plist/index.html). This copies them into
# $WEB_DIST/app/ if present. It is called BOTH by build_and_install_ota.sh AND by
# setup_server.sh after every web build — so `make update`/`make update-web`, which wipe
# Web/dist, automatically re-publish the IPA instead of silently dropping it. A true no-op
# when no IPA has been built yet. Returns 0 always (never block a web deploy on this).
publish_app_bundle() {
  local web_dist="${WEB_DIST:-$REPO_ROOT/Web/dist}"
  local src="$REPO_ROOT/build/ipa"
  [[ -f "$src/Dashboard.ipa" ]] || return 0     # nothing built yet → nothing to publish
  local dest="$web_dist/app"
  mkdir -p "$dest" || return 0
  local f
  for f in Dashboard.ipa manifest.plist index.html version.json; do
    [[ -f "$src/$f" ]] && cp -f "$src/$f" "$dest/$f"
  done
  ok "republished iOS OTA payload → ${dest#$REPO_ROOT/} (https://${TS_HOST:-<TS_HOST>}/app/)"
  return 0
}

# Emit a launchd <EnvironmentVariables> body from extra KEY=VAL args (first) plus
# every entry in Deploy/.env. XML-escapes values; later args win over .env.
plist_env_xml() {
  local pair key val skip
  for pair in "$@"; do
    key="${pair%%=*}"; val="${pair#*=}"
    val="${val//&/&amp;}"; val="${val//</&lt;}"; val="${val//>/&gt;}"
    printf '      <key>%s</key><string>%s</string>\n' "$key" "$val"
  done
  [[ -f "$ENV_FILE" ]] || return 0
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *"="* ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    key="$(echo "$key" | xargs)"
    [[ -z "$key" ]] && continue
    skip=0
    for pair in "$@"; do [[ "${pair%%=*}" == "$key" ]] && skip=1 && break; done
    [[ $skip -eq 1 ]] && continue
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    val="${val//&/&amp;}"; val="${val//</&lt;}"; val="${val//>/&gt;}"
    printf '      <key>%s</key><string>%s</string>\n' "$key" "$val"
  done < "$ENV_FILE"
}

# Idempotently (re)load a LaunchAgent for the current GUI domain. Handles the
# "already loaded" case that makes `launchctl bootstrap` fail with EIO (error 5):
# bootout first, wait for teardown, then bootstrap — and if bootstrap still
# reports the job present, just kickstart-restart it. Uses globals LABEL/PLIST.
reload_service() {
  local uid; uid="$(id -u)"
  local domain="gui/$uid"
  local target="$domain/$LABEL"

  launchctl bootout "$target" 2>/dev/null || true
  # Wait for the old job to fully unload (avoids the bootstrap I/O error).
  local i
  for i in 1 2 3 4 5; do
    launchctl print "$target" >/dev/null 2>&1 || break
    sleep 1
  done

  if launchctl bootstrap "$domain" "$PLIST" 2>/tmp/dashboard-launchctl.err; then
    ok "LaunchAgent loaded ($LABEL)"
  elif launchctl print "$target" >/dev/null 2>&1; then
    warn "LaunchAgent already loaded — restarting"
  else
    # Legacy fallback for older launchd quirks.
    launchctl load -w "$PLIST" 2>/dev/null \
      || fail "launchctl could not load the service: $(cat /tmp/dashboard-launchctl.err 2>/dev/null)"
    ok "LaunchAgent loaded via legacy load ($LABEL)"
  fi

  launchctl enable "$target" 2>/dev/null || true
  launchctl kickstart -k "$target" 2>/dev/null || true
}
