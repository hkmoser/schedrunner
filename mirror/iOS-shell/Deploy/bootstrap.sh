#!/usr/bin/env bash
# Install/verify the toolchain on the Mac mini. Idempotent: safe to re-run.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

c_bold "== bootstrap =="

# Xcode Command Line Tools provide `swift` (needed to build the Vapor server).
if ! have swift; then
  warn "Swift not found — installing Xcode Command Line Tools."
  xcode-select --install || true
  fail "Re-run 'make deploy' after the Command Line Tools install completes."
fi
ok "swift $(swift --version 2>/dev/null | head -1)"

# SwiftPM cannot link package manifests under the Command Line Tools on many
# setups (Undefined symbols: PackageDescription.Package.__allocating_init).
# Point the active developer dir at full Xcode, which builds manifests correctly.
dev_dir="$(xcode-select -p 2>/dev/null || true)"
if [[ "$dev_dir" == *CommandLineTools* ]]; then
  if [[ -d /Applications/Xcode.app ]]; then
    warn "Active toolchain is Command Line Tools — switching to Xcode (needed for SwiftPM + the iOS build)."
    sudo xcode-select -s /Applications/Xcode.app/Contents/Developer \
      && ok "switched to $(xcode-select -p)" \
      || warn "Run manually: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
  else
    warn "Using Command Line Tools and no Xcode found. If 'swift build' fails to link"
    info "PackageDescription, install Xcode (App Store) then run:"
    info "  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
  fi
fi

# Homebrew (for node + tailscale).
if ! have brew; then
  warn "Homebrew not found — installing."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
ok "homebrew $(brew --version | head -1)"

# Node (web build).
if ! have node; then
  info "installing node…"
  brew install node
fi
ok "node $(node --version)"

# Tailscale (mesh VPN + HTTPS). Prefer an already-installed app (incl. App Store
# GUI app, whose CLI is inside the bundle and not on PATH).
if ! resolve_tailscale; then
  info "installing tailscale…"
  brew install --cask tailscale || brew install tailscale || \
    warn "Install the Tailscale app from the Mac App Store or https://tailscale.com/download, then re-run."
  resolve_tailscale || true
fi
if resolve_tailscale; then
  ok "tailscale found ($TAILSCALE_BIN)"
  # Bring the node online (no-op if already up).
  if ! ts status >/dev/null 2>&1; then
    warn "Tailscale is not connected — connect via the menu-bar app, or run: sudo tailscale up"
  fi
else
  warn "tailscale not found yet; connect it, then re-run 'make deploy'."
fi

ok "bootstrap complete"
