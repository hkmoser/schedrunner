#!/usr/bin/env bash
# Native iOS deploy status — is auto-build on, what's the last/current build, and (the point of
# this) is a background build IN PROGRESS or STUCK? The iOS build runs detached from the deploy
# (nohup), logging to build/ios-autodeploy.log with a status line in build/.ios-build-status.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

STATUS_FILE="$REPO_ROOT/build/.ios-build-status"
LOG="$REPO_ROOT/build/ios-autodeploy.log"
ARCH_LOG="$REPO_ROOT/build/xcodebuild-archive.log"
status="$(cat "$STATUS_FILE" 2>/dev/null || echo 'never run')"

# A file's mtime as an epoch integer — BSD (macOS) `stat -f` first, GNU `stat -c` fallback, each
# validated numeric so a wrong-platform stat can't feed junk into the arithmetic below.
mtime() {
  local m
  m="$(stat -f %m "$1" 2>/dev/null)"; [[ "$m" =~ ^[0-9]+$ ]] && { echo "$m"; return; }
  m="$(stat -c %Y "$1" 2>/dev/null)"; [[ "$m" =~ ^[0-9]+$ ]] && { echo "$m"; return; }
  echo 0
}
now="$(date +%s)"

echo "auto-build (DEPLOY_IOS): $(app_flag_on DEPLOY_IOS && echo ON || echo 'OFF — native changes will NOT auto-build')"
echo "current commit #:        $(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || echo '?')  (the build the app would update TO)"
echo "last iOS build status:   $status"
echo "published:               $(sed -n 's/.*"version":"\([^"]*\)".*/\1/p' "$REPO_ROOT/build/ipa/version.json" 2>/dev/null || echo 'none') · build $(sed -n 's/.*"build":"\([^"]*\)".*/\1/p' "$REPO_ROOT/build/ipa/version.json" 2>/dev/null || echo '-')"

# --- in-progress / stuck detection ---------------------------------------------------------
if [[ "$status" == RUNNING* ]]; then
  pid="$(printf '%s' "$status" | sed -n 's/.*pid \([0-9]*\).*/\1/p')"
  log_idle=$(( now - $(mtime "$LOG") ))
  arch_idle=$(( now - $(mtime "$ARCH_LOG") ))
  idle=$log_idle; [[ $arch_idle -lt $idle ]] && idle=$arch_idle   # most recent of the two logs
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "state:                   BUILDING (pid $pid) — log last advanced ${idle}s ago"
    if [[ $idle -gt 900 ]]; then
      warn "the build log hasn't advanced in >15m — likely STUCK (waiting on codesign/provisioning, a network hang, or a first-time device-registration prompt)."
      info "  inspect:  tail -n 60 ${ARCH_LOG#$REPO_ROOT/}   and   ps -p $pid -o etime,command"
      info "  unstick:  kill $pid ; then re-run 'make ios-deploy' interactively to see where it blocks"
    else
      info "  watch it:  tail -f ${LOG#$REPO_ROOT/}"
    fi
  else
    warn "status says RUNNING but pid ${pid:-?} is not alive — the build DIED without recording a result."
    info "  why:      tail -n 60 ${LOG#$REPO_ROOT/}   (and ${ARCH_LOG#$REPO_ROOT/} for the xcodebuild reason)"
  fi
elif [[ "$status" == FAILED* ]]; then
  warn "last build FAILED. Reason:"
  info "  tail -n 60 ${LOG#$REPO_ROOT/}   ·   grep -nE 'error:|CodeSign|errSec|No profiles' ${ARCH_LOG#$REPO_ROOT/}"
fi

echo "logs:                    ${LOG#$REPO_ROOT/}  (+ ${ARCH_LOG#$REPO_ROOT/} for xcodebuild)"
