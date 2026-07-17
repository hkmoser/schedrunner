#!/usr/bin/env bash
# THE post-pull deploy hook. An EXTERNAL service owns the schedule: it runs `git pull` and then
# runs THIS script. So this script's whole job is: make the RUNNING server match the just-pulled
# code. A `git pull` alone changes nothing — the templates/routes/providers are compiled into the
# Vapor binary and loaded at process start — so without a rebuild + restart the server serves stale
# content (the classic "I pulled but don't see my changes"). This closes that gap.
#
# Contract with the external deploy service:
#   1. it does `git pull` (or otherwise updates the working tree), THEN
#   2. it runs `bash Deploy/auto_deploy.sh` (equivalently `make auto-deploy`).
# This script does NOT pull and is NOT gated behind a toggle — being invoked IS the trigger.
#
#   - IDEMPOTENT: a true no-op when the server already reports the checked-out HEAD, so it's safe
#     to run on every tick even when nothing changed (no needless restarts).
#   - CHANGE-SCOPED: rebuilds the Vapor server only if its sources changed (content hash), always
#     reloads the service, redeploys the sidecar, and mirrors docs — i.e. a full `make update`.
#   - GATED ON GREEN: `make update`/setup_server.sh fails loudly on a compile error, so a broken
#     commit leaves the previous good binary running; this records FAILED and the server stays up.
#   - VERIFIES: confirms /healthz reports the new build after restart.
#   - OBSERVABLE: appends to build/auto-deploy.log and writes build/.auto-deploy-status each run.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

LOG="$REPO_ROOT/build/auto-deploy.log"
STATUS="$REPO_ROOT/build/.auto-deploy-status"
PORT="${PORT:-$CH_SERVER_PORT}"
mkdir -p "$REPO_ROOT/build"
exec >>"$LOG" 2>&1
stamp() { date '+%Y-%m-%d %H:%M:%S'; }
say()   { echo "[$(stamp)] $*"; }
record(){ echo "$1 $(stamp)" > "$STATUS"; }

git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { say "not a git repo; abort"; record "SKIPPED(not-git)"; exit 0; }

# The code that SHOULD be running (checked-out HEAD) vs what the server reports it's running
# (/healthz build = the git sha stamped at its last deploy).
head_sha="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
deployed="$(curl -fsS --max-time 6 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null \
            | python3 -c 'import sys,json;print(json.load(sys.stdin).get("build",""))' 2>/dev/null || true)"

if [[ -n "$deployed" && "$deployed" == "$head_sha" ]]; then
  record "CURRENT($head_sha)"; exit 0     # server already matches the tree — nothing to do
fi

if [[ -z "$deployed" ]]; then
  say "server not answering on :${PORT} — (re)deploying to bring it up at $head_sha"
else
  say "server is stale ($deployed) vs checked-out HEAD ($head_sha) — redeploying"
fi

# Rebuild the server if its sources changed (content-hash; loud on a bad compile) + reload the
# service, redeploy the sidecar, mirror docs. This is exactly what `make update` runs.
deploy_steps() {
  bash "$REPO_ROOT/Deploy/setup_server.sh" && bash "$REPO_ROOT/Deploy/setup_bq.sh" && bash "$REPO_ROOT/Deploy/sync_docs.sh"
}
record "RUNNING($head_sha)"   # visible on the Deploy screen during the compile (can take 30-90s)
if ! deploy_steps; then
  # One retry after 30 s catches transient failures (Swift Package Manager network hiccup,
  # momentary disk-full, launchctl timing). A real compile error will fail again.
  say "first attempt failed — retrying once in 30s (catches transient package/network errors)"
  sleep 30
  if ! deploy_steps; then
    say "deploy step failed on retry (see above) — previous server keeps running"; record "FAILED(build)"
    exit 1   # non-zero so the external service knows the deploy failed
  fi
fi
sleep 2
now="$(curl -fsS --max-time 6 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null \
       | python3 -c 'import sys,json;print(json.load(sys.stdin).get("build",""))' 2>/dev/null || true)"
if [[ "$now" == "$head_sha" ]]; then
  say "deployed OK — server now at $now"; record "SUCCESS($head_sha)"
  bash "$REPO_ROOT/Deploy/ios_autodeploy.sh" || true   # native rebuild too, if DEPLOY_IOS on + native changed
else
  say "redeployed but /healthz build is '$now' (expected $head_sha) — check server logs"; record "WARN(build=$now)"
  exit 1   # surface the mismatch to the external service (it ran, but the server isn't current)
fi
