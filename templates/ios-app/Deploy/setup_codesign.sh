#!/usr/bin/env bash
# One-time codesign keychain setup so `make ios-deploy` AND the non-interactive auto-deploy never
# prompt for your Mac password. It:
#   1. unlocks the login keychain,
#   2. grants codesign + Apple tools access to the signing keys (set-key-partition-list), and
#   3. disables the keychain's auto-lock so it stays unlocked (set-keychain-settings).
# Run once: `make ios-keychain`. After that, native builds are fully unattended. The password is
# used in-place and never stored. (On a headless Mac mini behind Tailscale, leaving the login
# keychain unlocked is a reasonable trade for unattended signing; run this only on that machine.)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

kc="$(security default-keychain -d user 2>/dev/null | tr -d ' "')"
[[ -n "$kc" && -e "$kc" ]] || kc="$HOME/Library/Keychains/login.keychain-db"
info "login keychain: $kc"

pw="${DASHBOARD_KEYCHAIN_PW:-}"
if [[ -z "$pw" ]]; then
  read -r -s -p "Mac login password (used once to set up codesign; NOT stored): " pw; echo
fi

security unlock-keychain -p "$pw" "$kc" || fail "keychain unlock failed (wrong password?)"
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pw" "$kc" >/dev/null 2>&1 || true
security set-keychain-settings "$kc" >/dev/null 2>&1 || true

ok "codesign keychain ready: unlocked · codesign access granted · auto-lock disabled"

# PERSIST the password to the secrets store (same backend as the APNs key: the 0600 local file —
# written directly so it does NOT depend on the sidecar being up — plus a best-effort POST to the
# sidecar so Secret Manager gets it too). NOT Deploy/.env, NOT an env var. This is what makes it
# DURABLE across cert rotation: automatic signing periodically mints a fresh signing cert whose key
# lacks codesign access, and the build's non-interactive heal re-grants it by reading this. Without
# it, a rotated cert brings the interactive prompt right back.
persist_setting DASHBOARD_KEYCHAIN_PW "$pw"
ok "keychain password stored (0600 local file + sidecar if up) → future builds heal unattended after cert rotation"

ok "make ios-deploy and the auto-deploy will no longer prompt for a password."
