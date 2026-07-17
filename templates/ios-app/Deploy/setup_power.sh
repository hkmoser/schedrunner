#!/usr/bin/env bash
# One-time: keep the Mac mini awake so the __APP_NAME_LOWER__ stays reachable 24/7.
# NON-INTERACTIVE: uses `sudo -n` so it never blocks unattended deploys. If sudo
# needs a password, it prints the manual command and continues (non-fatal).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

c_bold "== prevent sleep =="
if sudo -n pmset -a sleep 0 2>/dev/null; then
  sudo -n systemsetup -setcomputersleep Never 2>/dev/null || true
  ok "system sleep disabled"
else
  warn "Skipped (needs admin). Run once so the mini never sleeps, then deploys won't need it:"
  info "  make power      # or: sudo pmset -a sleep 0 && sudo systemsetup -setcomputersleep Never"
fi
