#!/usr/bin/env bash
# Capture the iPhone's UDID so the OTA build can be signed for it.
#
# With Apple Developer enrollment + Xcode AUTOMATIC signing, the easiest path is to just
# plug the phone into the Mac and press Run once in Xcode — Xcode registers the device on
# the developer portal for you. This script is the cable-free helper: it detects the UDID
# of a connected/paired iPhone (or takes one you pass), saves it to Deploy/.device_udid
# (git-ignored), and reminds you to register it. build_and_install_ota.sh reads that file
# only to print a friendlier message; signing itself is handled by Xcode automatic signing.
#
# Usage:
#   bash Deploy/register_device.sh                 # auto-detect a connected device
#   bash Deploy/register_device.sh 00008110-XXXX…  # record a UDID explicitly
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

UDID_FILE="$REPO_ROOT/Deploy/.device_udid"

c_bold "== register device =="

udid="${1:-}"

# Try to auto-detect a single connected/paired iPhone when none was passed.
detect_udid() {
  local out=""
  # Xcode 15+ : devicectl lists paired devices (USB or network).
  if have xcrun && xcrun devicectl list devices >/dev/null 2>&1; then
    out="$(xcrun devicectl list devices 2>/dev/null \
      | grep -iE 'iphone|ipad' \
      | grep -oiE '[0-9A-F]{8}-[0-9A-F]{16}|[0-9A-F]{40}' | head -n1)"
  fi
  # Fallback: xctrace device list (older Xcode).
  if [[ -z "$out" ]] && have xcrun; then
    out="$(xcrun xctrace list devices 2>/dev/null \
      | grep -iE 'iphone|ipad' | grep -v -i simulator \
      | grep -oiE '\(([0-9A-F-]{25,40})\)' | tr -d '()' | head -n1)"
  fi
  # Fallback: libimobiledevice if installed (brew install libimobiledevice).
  if [[ -z "$out" ]] && have idevice_id; then
    out="$(idevice_id -l 2>/dev/null | head -n1)"
  fi
  printf '%s' "$out"
}

if [[ -z "$udid" ]]; then
  info "No UDID given — trying to auto-detect a connected/paired iPhone…"
  udid="$(detect_udid)"
fi

if [[ -z "$udid" ]]; then
  warn "Could not auto-detect a device UDID."
  cat <<'EOF'

  Find it one of these ways, then re-run with it:
    • Plug the iPhone into the Mac, open Finder → select the iPhone → click under its name
      until the UDID shows → right-click → Copy.
    • Xcode → Window → Devices and Simulators → select the phone → "Identifier".
    • On the phone: Settings → General → About → tap "Serial Number" to cycle to UDID.

  Then:  bash Deploy/register_device.sh <UDID>
EOF
  exit 1
fi

printf '%s\n' "$udid" > "$UDID_FILE"
ok "Saved device UDID to ${UDID_FILE#$REPO_ROOT/}: $udid"

cat <<EOF

Next:
  • Register this device with your team (one time). The simplest route is Xcode AUTOMATIC
    signing — open the project (run \`xcodegen generate\` then open Dashboard.xcodeproj),
    plug in the phone, select it as the destination, and press Run once. Xcode adds the
    device to developer.apple.com/account → Devices and provisions it for you.
  • Prefer to do it by hand? developer.apple.com/account → Devices → + → paste the UDID.
  • Then build & install over Tailscale:  bash Deploy/build_and_install_ota.sh
EOF
