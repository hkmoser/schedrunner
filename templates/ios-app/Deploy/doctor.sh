#!/usr/bin/env bash
# Preflight + healthcheck. Tells you exactly what's missing and how to fix it.
# Exit non-zero if anything required for a successful deploy is wrong.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

c_bold "== doctor =="
problems=0

# --- Toolchain ---
for tool in swift node npm; do
  if have "$tool"; then ok "$tool installed"; else c_red "✗ $tool missing — run 'make deploy' (bootstrap)"; problems=1; fi
done
# SwiftPM manifest linking is unreliable under Command Line Tools; prefer full Xcode.
dev_dir="$(xcode-select -p 2>/dev/null || true)"
if [[ "$dev_dir" == *CommandLineTools* ]]; then
  if [[ -d /Applications/Xcode.app ]]; then
    warn "Active toolchain is Command Line Tools — 'swift build' may fail to link."
    info "Fix: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer  (make deploy does this)"
  else
    warn "No Xcode found; install it (App Store) for a reliable SwiftPM build + the iOS app."
  fi
else
  [[ -n "$dev_dir" ]] && ok "Xcode toolchain active ($dev_dir)"
fi
if resolve_tailscale; then
  ok "tailscale found ($TAILSCALE_BIN)"
  if [[ "$TAILSCALE_BIN" != "tailscale" ]]; then
    info "Tip: add it to PATH → sudo ln -sf \"$TAILSCALE_BIN\" /usr/local/bin/tailscale"
  fi
else
  c_red "✗ tailscale not found (not on PATH or in /Applications/Tailscale.app)"
  info "Fix: install the Tailscale app from the Mac App Store or https://tailscale.com/download"
  problems=1
fi

# --- Secrets / config ---
if [[ ! -f "$ENV_FILE" ]]; then
  c_red "✗ Deploy/.env missing"
  info "Fix: cp Deploy/.env.example Deploy/.env  then fill it in."
  problems=1
else
  ok "Deploy/.env present"
  if ! require_env TS_HOST; then
    # Offer the detected MagicDNS name to copy in.
    if resolve_tailscale; then
      detected="$(ts status --json 2>/dev/null | sed -n 's/.*"DNSName" *: *"\([^"]*\)\.".*/\1/p' | head -1)"
      [[ -n "$detected" ]] && info "Detected this machine's MagicDNS name: $detected"
    fi
    info "Fix: set TS_HOST in Deploy/.env to your Mac mini's MagicDNS name (see 'tailscale status')."
    problems=1
  fi
  for k in TWELVEDATA_KEY FRED_KEY; do
    if [[ -z "${!k:-}" ]]; then
      warn "$k not set — that card will show sample data until you add a key."
    fi
  done
fi

# --- Tailscale connectivity ---
if resolve_tailscale; then
  if ts status >/dev/null 2>&1; then
    ok "tailscale connected"
  else
    c_red "✗ tailscale not connected"
    info "Fix: sudo tailscale up   (or click 'Connect' in the Tailscale menu-bar app)"
    problems=1
  fi
fi

# --- Tailscale HTTPS (the one manual prerequisite) ---
if resolve_tailscale && [[ -n "${TS_HOST:-}" ]]; then
  # Probe in a temp dir: `tailscale cert` writes <host>.crt/.key to the CWD on success.
  if ( cd "$(mktemp -d)" && ts cert "$TS_HOST" ) >/dev/null 2>&1; then
    ok "tailscale HTTPS cert available for $TS_HOST"
  else
    c_red "✗ Could not obtain an HTTPS cert for $TS_HOST"
    c_yellow "  This is the ONE manual step. In the Tailscale admin console:"
    info "1) Open https://login.tailscale.com/admin/dns"
    info "2) Enable MagicDNS (if not already)."
    info "3) Under 'HTTPS Certificates', click 'Enable HTTPS' and acknowledge."
    info "4) Confirm TS_HOST in Deploy/.env matches the machine name shown by 'tailscale status'."
    info "Then re-run 'make doctor'."
    problems=1
  fi
fi

# --- Live healthcheck (only if the server is already serving) ---
# Target THIS channel's origin (stable :443, next :8443): checking stable's /healthz from the
# next worktree would compare the wrong build and suggest a command that clobbers stable.
_hz_sfx=""; [[ "$CH_TLS_PORT" != "443" ]] && _hz_sfx=":$CH_TLS_PORT"
_upd_hint="make update"; [[ "$DASHBOARD_CHANNEL" != "stable" ]] && _upd_hint="make update CHANNEL=$DASHBOARD_CHANNEL"
if [[ -n "${TS_HOST:-}" ]]; then
  if hz="$(curl -fsS --max-time 5 "https://$TS_HOST$_hz_sfx/healthz" 2>/dev/null)"; then
    ok "server responding at https://$TS_HOST$_hz_sfx/healthz"
    running="$(printf '%s' "$hz" | sed -n 's/.*"build"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    local_sha="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    if [[ -n "$running" ]]; then
      if [[ "$running" == "$local_sha" ]]; then
        ok "running build $running matches this checkout"
      else
        warn "running build is $running but this checkout is $local_sha — run '$_upd_hint' to deploy your changes"
      fi
    fi
  else
    info "(server not yet serving over HTTPS — 'make deploy' will start it)"
  fi
fi

echo
if [[ $problems -eq 0 ]]; then
  c_green "doctor: all required checks passed."
else
  c_red "doctor: found problems above. Fix them, then re-run."
  exit 1
fi
