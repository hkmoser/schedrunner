#!/usr/bin/env bash
# FIRST-TIME bring-up of the NEXT channel on the Mac mini. Run once from the STABLE checkout:
#   bash Deploy/first_next_deploy.sh
# Idempotent — safe to re-run if a step fails. Requires the channel machinery to be on main
# (merge the channels PR first) and the stable stack already deployed (Deploy/.env filled in).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

echo "== 1/5 preflight (stable checkout: $here) =="
[[ -f Deploy/setup_channel.sh ]] || { echo "✗ Deploy/setup_channel.sh missing — merge the channels PR to main and 'git pull' first"; exit 1; }
[[ -f Deploy/.env ]] || { echo "✗ Deploy/.env missing — the stable stack must be set up first (make deploy)"; exit 1; }
[[ -f Deploy/.channel ]] && { echo "✗ this checkout is pinned to '$(cat Deploy/.channel)' — run from the STABLE checkout"; exit 1; }
git pull --ff-only origin main || echo "! pull failed (offline?) — continuing with the current tree"

echo
echo "== 2/5 create the next branch + worktree =="
make next-setup

echo
echo "== 3/5 deploy the next stack (server :8081 · sidecar :8100 · TLS :8443) =="
cd ../iOS-Shell-next
make deploy          # channel comes from the worktree's Deploy/.channel pin — no flag needed

echo
echo "== 4/5 install the self-heal timer for next =="
make auto-deploy-timer

echo
echo "== 5/5 verify both stacks =="
make channel-status

ts_host="$(grep -E '^TS_HOST=' Deploy/.env | head -n1 | cut -d= -f2- | tr -d '"'"'"' ')"
echo
echo "===== NEXT channel is up ====="
echo "On your iPhone (Tailscale ON):"
echo "  1) Open  https://${ts_host:-<TS_HOST>}:8443/  in Safari"
echo "  2) Share → Add to Home Screen  → the app installs as 'Next'"
echo "  3) Unlock with the usual passcode"
echo
echo "Day-to-day: commit features to branch 'next', then here:  git pull && make deploy"
echo "Promote to stable by merging next → main."
