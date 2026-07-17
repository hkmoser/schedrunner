#!/usr/bin/env bash
# Background, change-detected native iOS rebuild — triggered by the deploy AFTER the web is
# live, so the slow archive never blocks or delays the web build.
#
# - OPT-IN: does nothing unless DEPLOY_IOS=1 in Deploy/.env.
# - CHANGE-DETECTED: no-op unless a native source (App/, Widget/, project.yml, Config.xcconfig)
#   changed since the last successful iOS build (a stamp, mirroring the server's build stamp).
# - NON-BLOCKING: the archive/export/publish runs detached in the background; this script
#   returns in well under a second, so `make deploy` finishes as fast as a web-only deploy.
# - OBSERVABLE: reports the PREVIOUS background build's result on entry, and the build writes a
#   status file (build/.ios-build-status) so a silent failure is never invisible.
#
# The published IPA + version.json let the installed app offer an in-app "Update available"
# button — no cable, no Safari. NOTE: this PUBLISHES; it does not install. Tap the in-app
# banner (or run `make ios-deploy` with the phone plugged in) to actually put it on the device.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

STATUS="$REPO_ROOT/build/.ios-build-status"
LOG="$REPO_ROOT/build/ios-autodeploy.log"
STAMP="$REPO_ROOT/build/.ios-build-stamp"
mkdir -p "$REPO_ROOT/build"

# Surface the previous background build's outcome so a prior silent failure is visible NOW.
if [[ -f "$STATUS" ]]; then
  case "$(cat "$STATUS" 2>/dev/null)" in
    SUCCESS*) ok "iOS: last background build OK ($(cat "$STATUS"))" ;;
    FAILED*)  warn "iOS: last background build FAILED — $(cat "$STATUS"). See: tail -n 40 ${LOG#$REPO_ROOT/}" ;;
  esac
fi

# Controlled by an IN-APP toggle (Settings → Deploy → "Auto-build iOS app on deploy"), read
# from the sidecar's settings store. A Deploy/.env DEPLOY_IOS still overrides if you set one.
if ! app_flag_on DEPLOY_IOS; then
  info "iOS auto-build is OFF. The app is NOT rebuilt by \`make deploy\`."
  info "  → turn it on in the app: Settings → Deploy → \"Auto-build iOS app on deploy\" (then re-deploy),"
  info "    or build right now with \`make ios-deploy\`."
  exit 0
fi

# Has any native source changed since the last successful iOS build?
#
# CONTENT hash of everything that affects the IPA (App/, Widget/, project.yml, Config.xcconfig).
# The old detection used file mtime (find -newer "$STAMP"), which was UNRELIABLE for the exact
# reason the server build hit and fixed: a `git pull`/merge can leave file mtimes equal to or
# older than the stamp (1-second resolution, checkout/merge timing), so a needed rebuild was
# silently skipped and native changes never rolled out. Hashing the actual bytes rebuilds
# whenever a source truly changes — independent of mtime, clock, or how git updated the tree.
# The stamp now stores the HASH we last built (written on success), not just a touch time.
ios_src_hash() {
  local paths=("$REPO_ROOT/App" "$REPO_ROOT/Widget" "$REPO_ROOT/project.yml" "$REPO_ROOT/Config.xcconfig")
  # `|| true` so a transient find/shasum hiccup can't abort under set -e/pipefail; an empty hash
  # just forces a (safe) rebuild rather than skipping one.
  { find "${paths[@]}" -type f 2>/dev/null \
      | LC_ALL=C sort \
      | while IFS= read -r f; do shasum "$f" 2>/dev/null; done \
      | shasum | awk '{print $1}'; } || true
}
CUR_HASH="$(ios_src_hash)"

changed=0
if [[ ! -f "$STAMP" ]]; then
  changed=1
elif [[ "$(cat "$STAMP" 2>/dev/null)" != "$CUR_HASH" ]]; then
  changed=1
fi

if [[ $changed -eq 0 ]]; then
  info "iOS: no native changes since last build — skipping (the app is server-driven; content updates need no rebuild)"
  exit 0
fi

c_bold "== iOS: native changes detected — building in the background =="
info "archive/export/publish is detached; the deploy continues immediately."
info "progress: tail -f ${LOG#$REPO_ROOT/}    ·    result: cat ${STATUS#$REPO_ROOT/}"

# Detach: the heavy build runs independently of this deploy. Stamp + status only on success so a
# failed/interrupted build retries next deploy and the failure is recorded. Never installs over
# USB here (no device); it publishes the OTA payload for the in-app updater.
build_n="$(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || echo '?')"
# The subshell records RUNNING (with its own pid + start time) as its FIRST act, then overwrites
# with SUCCESS/FAILED (and the real completion time) when done — so the status file reflects an
# in-progress build, and `make ios-status` can tell "still building" from "stuck" from "done".
nohup bash -c '
  echo "RUNNING build '"$build_n"' since $(date "+%Y-%m-%d %H:%M:%S") pid $$" > "'"$STATUS"'"
  if bash "'"$REPO_ROOT"'/Deploy/build_and_install_ota.sh"; then
    printf "%s\n" "'"$CUR_HASH"'" > "'"$STAMP"'"   # record the CONTENT hash we built (not a touch time)
    echo "SUCCESS build '"$build_n"' at $(date "+%Y-%m-%d %H:%M:%S")" > "'"$STATUS"'"
    echo "[ios-autodeploy] success — OTA payload published (build '"$build_n"'); tap the in-app Update banner to install."
  else
    echo "FAILED build '"$build_n"' at $(date "+%Y-%m-%d %H:%M:%S")" > "'"$STATUS"'"
    echo "[ios-autodeploy] FAILED — see log above (signing/keychain are the usual causes)."
  fi
' >"$LOG" 2>&1 &
disown
ok "iOS build launched in background (deploy not blocked) — check result later: make ios-status"
