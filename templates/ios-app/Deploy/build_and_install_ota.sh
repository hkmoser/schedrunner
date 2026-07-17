#!/usr/bin/env bash
# Build the native iOS app and install it on your iPhone over Tailscale, cable-free.
#
# Pipeline:  xcodegen generate → xcodebuild archive (Release, automatic signing)
#         →  exportArchive (development method) → signed __APP_NAME__.ipa + manifest.plist
#         →  publish both + an icon into the server's web bundle ($WEB_DIST/app)
#         →  print the itms-services:// URL to open in Safari on the phone.
#
# Run on the Mac mini AFTER `make deploy` (the server must be up and reachable at TS_HOST
# over HTTPS — iOS requires HTTPS for OTA install, which Tailscale Serve provides).
#
# Turn-key: the only thing you must do once is sign into Xcode with your Apple ID
# (Xcode → Settings → Accounts) — Apple's 2FA can't be scripted. Everything else is
# auto-resolved: the Team ID is detected from your signing certificate, the host is read
# from Deploy/.env (TS_HOST), and xcodegen is installed via Homebrew if missing. For the
# FIRST install, plug the iPhone in once so automatic signing can register it; after that
# it's cable-free. (Override detection with DEVELOPMENT_TEAM in Config.xcconfig if needed.)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

# Channel-aware: a NEXT-channel build gets its own bundle id (<id>.next — installable alongside
# stable), display name ("__APP_NAME__ Next"), server port (:8443, via gen_ios_config →
# Config.local.xcconfig), and publishes its OTA payload to ITS origin (https://$TS_HOST:8443/app/).
OTA_SFX=""; [[ "$CH_TLS_PORT" != "443" ]] && OTA_SFX=":$CH_TLS_PORT"
APP_TITLE="__APP_NAME__"; [[ "$DASHBOARD_CHANNEL" != "stable" ]] && APP_TITLE="__APP_NAME__ Next"

XCCONFIG="$REPO_ROOT/Config.xcconfig"
BUILD_DIR="$REPO_ROOT/build"
ARCHIVE="$BUILD_DIR/__APP_NAME__.xcarchive"
IPA_DIR="$BUILD_DIR/ipa"
WEB_DIST="${WEB_DIST:-$REPO_ROOT/Web/dist}"

c_bold "== build & install OTA =="

# --- read a `KEY = value` setting out of Config.xcconfig ---------------------------------
xc() {
  grep -E "^[[:space:]]*$1[[:space:]]*=" "$XCCONFIG" 2>/dev/null \
    | head -n1 | sed -E "s/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//"
}

# --- preflight: tools --------------------------------------------------------------------
have xcodebuild || fail "xcodebuild not found — install Xcode, then: sudo xcode-select -s /Applications/Xcode.app && sudo xcodebuild -license accept"
if ! have xcodegen; then
  if have brew; then info "xcodegen missing — installing via Homebrew…"; brew install xcodegen || fail "brew install xcodegen failed"; fi
  have xcodegen || fail "xcodegen not found and Homebrew unavailable — install it:  brew install xcodegen"
fi
[[ -f "$XCCONFIG" ]] || fail "missing $XCCONFIG"

# --- preflight: signing identity (informational — NOT a hard gate) -----------------------
# A development certificate is minted on the first signed build by `-allowProvisioningUpdates`,
# so a freshly-added Apple ID legitimately has no cert YET — failing here was a false positive
# (it blocked the very build that creates the cert). Just report the state; a genuinely
# missing/not-signed-in account fails clearly at the archive step with the same remediation.
if security find-identity -v -p codesigning 2>/dev/null | grep -qiE 'Apple Develop(ment|er)'; then
  ok "code-signing identity present"
else
  warn "No Apple Development cert yet — automatic signing will create one during archive."
  info "Requires being signed into Xcode → Settings → Accounts. If archive fails on signing,"
  info "create it once: Xcode → Settings → Accounts → your team → Manage Certificates… → + → Apple Development."
fi

# --- resolve identifiers automatically (explicit Config.xcconfig still wins) --------------
BUNDLE_ID="$(xc PRODUCT_BUNDLE_IDENTIFIER)"
[[ -n "$BUNDLE_ID" ]] || fail "PRODUCT_BUNDLE_IDENTIFIER missing in Config.xcconfig"
# Config.xcconfig holds the BASE id; a non-stable channel's IPA is actually built with the
# suffixed id (gen_ios_config writes it into Config.local.xcconfig for xcodebuild). The
# manifest/export metadata MUST match the IPA's real bundle id — an OTA manifest whose
# bundle-identifier differs from the IPA fails on-device with "Unable to Install".
[[ "$DASHBOARD_CHANNEL" != "stable" ]] && BUNDLE_ID="$BUNDLE_ID.$DASHBOARD_CHANNEL"

# Team ID: an explicit, non-placeholder Config.xcconfig value wins; else auto-detect.
TEAM_ID="$(xc DEVELOPMENT_TEAM)"
if [[ -z "$TEAM_ID" || "$TEAM_ID" == "ABCDE12345" ]]; then
  TEAM_ID="$(detect_team_id || true)"
  [[ -n "$TEAM_ID" ]] && info "auto-detected Team ID: $TEAM_ID"
fi
[[ "$TEAM_ID" =~ ^[A-Z0-9]{10}$ ]] \
  || fail "Could not determine your Team ID. Set DEVELOPMENT_TEAM in Config.xcconfig (developer.apple.com/account → Membership)."

# Host: Deploy/.env TS_HOST is the single source of truth; fall back to Config.xcconfig.
HOST_FROM_XC="$(xc DASHBOARD_HOST)"
[[ "$HOST_FROM_XC" == "macmini.example.ts.net" ]] && HOST_FROM_XC=""
TS_HOST="${TS_HOST:-$HOST_FROM_XC}"
[[ -n "$TS_HOST" ]] \
  || fail "No host configured. Set TS_HOST in Deploy/.env (same value as the rest of the deploy)."
ok "team=$TEAM_ID  bundle=$BUNDLE_ID  host=$TS_HOST"

# --- first-install hint: is a device connected for automatic registration? ---------------
if have xcrun; then
  dev_udid="$(xcrun devicectl list devices 2>/dev/null | grep -oiE '[0-9A-F]{8}-[0-9A-F]{16}|[0-9A-F]{40}' | head -n1 || true)"
  if [[ -n "$dev_udid" ]]; then
    info "iPhone detected ($dev_udid) — automatic signing will register it if new."
  else
    warn "No iPhone detected over USB. If this is the FIRST install, plug it in once so the device can be registered; reinstalls don't need it."
  fi
fi

# --- generate the Xcode project from project.yml -----------------------------------------
# Write Config.local.xcconfig (host/team from .env) so a later Xcode GUI build agrees with us.
bash "$REPO_ROOT/Deploy/gen_ios_config.sh" || true
info "xcodegen generate"
( cd "$REPO_ROOT" && xcodegen generate ) || fail "xcodegen failed"
PROJECT="$REPO_ROOT/__APP_NAME__.xcodeproj"
[[ -d "$PROJECT" ]] || fail "expected $PROJECT after xcodegen"

# --- archive (Release, automatic signing; -allowProvisioningUpdates registers/provisions)-
mkdir -p "$BUILD_DIR"
ARCH_LOG="$BUILD_DIR/xcodebuild-archive.log"
rm -rf "$ARCHIVE"
info "xcodebuild archive (this can take a few minutes; log → ${ARCH_LOG#$REPO_ROOT/})…"

run_archive() {
  rm -rf "$ARCHIVE"
  xcodebuild \
    -project "$PROJECT" \
    -scheme __APP_NAME__ \
    -configuration Release \
    -destination 'generic/platform=iOS' \
    -archivePath "$ARCHIVE" \
    -allowProvisioningUpdates \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    DASHBOARD_HOST="$TS_HOST" \
    CODE_SIGN_STYLE=Automatic \
    archive 2>&1 | tee "$ARCH_LOG"
  return "${PIPESTATUS[0]}"
}

# The classic recurring codesign failure ("Command CodeSign failed" with errSecInternalComponent /
# "User interaction is not allowed") is the login keychain blocking codesign's access to the
# signing key. Two triggers make it RECUR even after a one-time fix:
#   1. the keychain re-locks (reboot / headless run with no GUI login), and
#   2. automatic signing (-allowProvisioningUpdates) mints a FRESH cert whose new private key has
#      a default partition list that does NOT include codesign: — so the old grant doesn't cover it.
# The durable answer is to re-unlock + re-grant on EVERY build, non-interactively. The password is
# read from the secrets store (app_setting: env → sidecar Secret Manager → 0600 file — the SAME
# backend as the APNs key, so no .env and no env var needed), falling back to a TTY prompt only
# when interactive and nothing is stored.

# Where does the Mac login password come from, without prompting? (empty = unavailable)
_keychain_pw() {
  local pw="${DASHBOARD_KEYCHAIN_PW:-}"
  [[ -z "$pw" ]] && pw="$(app_setting DASHBOARD_KEYCHAIN_PW 2>/dev/null || true)"
  printf '%s' "$pw"
}
_login_keychain() {
  local kc; kc="$(security default-keychain -d user 2>/dev/null | tr -d ' "')"
  [[ -n "$kc" && -e "$kc" ]] || kc="$HOME/Library/Keychains/login.keychain-db"
  printf '%s' "$kc"
}
# Unlock + grant codesign access + disable auto-lock, given a password. Idempotent; safe to run
# every build. Returns non-zero only if the unlock itself fails (wrong password).
_apply_keychain_grant() {
  local pw="$1" kc; kc="$(_login_keychain)"
  security unlock-keychain -p "$pw" "$kc" 2>/dev/null \
    || { warn "keychain unlock failed (wrong password?)"; return 1; }
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pw" "$kc" >/dev/null 2>&1 || true
  security set-keychain-settings "$kc" >/dev/null 2>&1 || true   # no idle/sleep auto-lock
  return 0
}

# PROACTIVE: before the first archive, if a password is available non-interactively, unlock + grant
# so a headless run (locked login keychain) or an existing-cert-with-reset-ACL succeeds on the FIRST
# attempt instead of failing → healing → retrying. A true no-op when no password is stored.
prep_keychain() {
  local pw; pw="$(_keychain_pw)"
  [[ -z "$pw" ]] && return 0
  if _apply_keychain_grant "$pw"; then
    ok "keychain pre-armed for codesign (unlocked · codesign access granted · auto-lock disabled)"
  fi
}

# REACTIVE fallback: called after an archive fails on the codesign ACL — most often because THIS
# build just minted a fresh cert. Re-grant covers the new key. Non-interactive when the password is
# stored; prompts only on a TTY as a last resort.
heal_keychain() {
  local pw prompted=0
  pw="$(_keychain_pw)"
  if [[ -z "$pw" ]]; then
    if [[ -t 0 ]]; then
      read -r -s -p "  Mac login password (so codesign can use the signing key): " pw; echo
      prompted=1
    else
      warn "codesign blocked and no stored password — set it once so this heals unattended:"
      warn "  Settings → Deploy → \"Mac login password\" (DASHBOARD_KEYCHAIN_PW), or run \`make ios-keychain\`."
      return 1
    fi
  fi
  if _apply_keychain_grant "$pw"; then
    ok "keychain unlocked + codesign access granted (covers the freshly-minted cert)"
    # If we had to PROMPT for it, persist it now so this is the LAST time — every future build,
    # including the non-interactive background auto-deploy, reads it and never prompts again.
    if [[ "$prompted" == 1 ]]; then
      persist_setting DASHBOARD_KEYCHAIN_PW "$pw"
      ok "stored the keychain password → future builds (incl. auto-deploy) won't prompt again"
    fi
  else
    return 1
  fi
}

print_archive_failure() {
  c_red "── xcodebuild failure (the actual reason) ──"
  grep -nE "error:|No profiles|No signing certificate|Failed to register|doesn't include|provisioning profile|requires a provisioning|Communication with Apple|session has expired|CodeSign failed|errSec|code object is not signed|resource fork|not allowed" "$ARCH_LOG" | tail -n 25 || true
  # Codesign failures print the real reason a line or two ABOVE "Command CodeSign failed".
  grep -nE -B2 "Command CodeSign failed" "$ARCH_LOG" | tail -n 12 || true
  echo
  fail "archive failed (full log: ${ARCH_LOG#$REPO_ROOT/}). If the lines above mention signing/profiles: confirm your Apple ID + team in Xcode → Settings → Accounts (and that you cleared the 2FA prompt), make sure the iPhone is plugged in + UNLOCKED so the device can be registered, or mint the cert via Manage Certificates… → + → Apple Development. If they're Swift 'error:' lines, it's a build error — send them over."
}

# Pre-arm the keychain so the FIRST archive succeeds on a headless box / after cert rotation
# (no-op when no password is stored; the reactive heal below is the fallback).
prep_keychain
if ! run_archive; then
  # Auto-heal the recurring keychain/codesign ACL failure and retry once before giving up.
  if grep -qiE "errSecInternalComponent|Command CodeSign failed|User interaction is not allowed" "$ARCH_LOG"; then
    warn "codesign couldn't access the signing key — healing the keychain and retrying once"
    if heal_keychain && run_archive; then :; else print_archive_failure; fi
  else
    print_archive_failure
  fi
fi
ok "archived → ${ARCHIVE#$REPO_ROOT/}"

# --- verify the app icon actually made it into the build ---------------------------------
# The blank-home-screen-icon bug is silent: the app installs fine, it's just iconless. Catch it
# HERE, at build time, instead of discovering it on the phone. iOS loads an asset-catalog icon
# via the TOP-LEVEL CFBundleIconName string AND the compiled Assets.car containing that named
# icon — verify BOTH are present in the archived app, and fail loudly if either is missing.
verify_app_icon() {
  local app; app="$(find "$ARCHIVE/Products/Applications" -maxdepth 1 -name '*.app' | head -n1)"
  [[ -d "$app" ]] || { warn "icon check: no .app in the archive — skipping"; return 0; }
  local plist="$app/Info.plist"
  local iconname; iconname="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconName' "$plist" 2>/dev/null || true)"
  if [[ "$iconname" != "AppIcon" ]]; then
    c_red "── app icon MISSING from the build ──"
    warn "CFBundleIconName is '${iconname:-<absent>}' in the built Info.plist (expected 'AppIcon')."
    fail "The home screen will show a blank icon. Ensure project.yml sets the TOP-LEVEL CFBundleIconName: AppIcon (not only the nested CFBundleIcons key), then rebuild."
  fi
  if [[ -f "$app/Assets.car" ]] && have xcrun; then
    if ! xcrun assetutil --info "$app/Assets.car" 2>/dev/null | grep -qi "AppIcon"; then
      c_red "── app icon MISSING from Assets.car ──"
      warn "The compiled asset catalog has no 'AppIcon' — actool didn't emit it."
      fail "Check App/Resources/Assets.xcassets/AppIcon.appiconset (a valid 1024² opaque PNG + matching Contents.json) and ASSETCATALOG_COMPILER_APPICON_NAME=AppIcon, then rebuild."
    fi
  fi
  ok "app icon verified in the build (CFBundleIconName=AppIcon + present in Assets.car)"
}
verify_app_icon

# --- render ExportOptions from the template with this team/bundle/host --------------------
EXPORT_OPTS="$BUILD_DIR/ExportOptions.plist"
mkdir -p "$BUILD_DIR"
# __TS_HOST__ becomes host[:port] — the xcodebuild-emitted manifest's appURL must point at
# THIS channel's origin, or the phone downloads the OTHER channel's IPA under this build's
# metadata (bundle-id mismatch → "Unable to Install").
sed -e "s/__TEAM_ID__/$TEAM_ID/g" \
    -e "s/__BUNDLE_ID__/$BUNDLE_ID/g" \
    -e "s/__TS_HOST__/$TS_HOST$OTA_SFX/g" \
    "$REPO_ROOT/Deploy/ExportOptions.plist" > "$EXPORT_OPTS"

# --- export the signed IPA (+ manifest.plist) --------------------------------------------
rm -rf "$IPA_DIR"
info "xcodebuild -exportArchive"
xcodebuild -exportArchive \
  -archivePath "$ARCHIVE" \
  -exportOptionsPlist "$EXPORT_OPTS" \
  -exportPath "$IPA_DIR" \
  -allowProvisioningUpdates || fail "export failed"

IPA="$(find "$IPA_DIR" -maxdepth 1 -name '*.ipa' | head -n1)"
[[ -n "$IPA" ]] || fail "no .ipa produced in $IPA_DIR"
# Normalize the filename the manifest/URL expect.
if [[ "$(basename "$IPA")" != "__APP_NAME__.ipa" ]]; then
  mv -f "$IPA" "$IPA_DIR/__APP_NAME__.ipa"; IPA="$IPA_DIR/__APP_NAME__.ipa"
fi
ok "exported → ${IPA#$REPO_ROOT/} ($(du -h "$IPA" | cut -f1))"

# --- app version + build number (for the manifest + in-app update check) -----------------
# Build number = git commit count (matches what gen_ios_config bakes into CFBundleVersion),
# so the published version.json build > the installed one whenever there are new commits —
# which is what makes the in-app "Update available" banner reliably appear.
BUILD="$(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || true)"
BUILD="${BUILD:-1}"
# Visible version derives from the same commit count (0.1.<count>), matching the MARKETING_VERSION
# gen_ios_config bakes into Config.local.xcconfig — so version.json, the install page, and the
# app all agree, and the version increments every deploy instead of being a static 0.1.0.
VERSION="0.1.$BUILD"

# --- ensure a manifest.plist exists (xcodebuild usually emits one; synthesize if not) ----
MANIFEST="$IPA_DIR/manifest.plist"
if [[ ! -f "$MANIFEST" ]]; then
  info "synthesizing manifest.plist"
  cat > "$MANIFEST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>items</key><array><dict>
  <key>assets</key><array>
    <dict><key>kind</key><string>software-package</string>
          <key>url</key><string>https://$TS_HOST$OTA_SFX/app/__APP_NAME__.ipa</string></dict>
    <dict><key>kind</key><string>display-image</string>
          <key>url</key><string>https://$TS_HOST$OTA_SFX/icons/apple-touch-icon.png</string></dict>
    <dict><key>kind</key><string>full-size-image</string>
          <key>url</key><string>https://$TS_HOST$OTA_SFX/icons/apple-touch-icon.png</string></dict>
  </array>
  <key>metadata</key><dict>
    <key>bundle-identifier</key><string>$BUNDLE_ID</string>
    <key>bundle-version</key><string>$VERSION</string>
    <key>kind</key><string>software</string>
    <key>title</key><string>$APP_TITLE</string>
  </dict>
</dict></array></dict></plist>
EOF
fi

# --- write the install landing page alongside the IPA in the PERSISTENT build/ipa/ --------
# All three artifacts (__APP_NAME__.ipa, manifest.plist, index.html) now live in build/ipa/,
# which survives web rebuilds. publish_app_bundle (lib.sh) copies them into Web/dist/app/,
# and setup_server.sh calls the same helper after every web build — so `make update` can't
# silently drop the published IPA. The itms-services link must be TAPPED in Safari (not typed
# into the address bar), so opening https://$TS_HOST/app/ on the phone gives a real button.
cat > "$IPA_DIR/index.html" <<EOF
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Install $APP_TITLE</title>
<style>body{font:17px -apple-system,system-ui,sans-serif;margin:0;min-height:100vh;display:flex;
flex-direction:column;align-items:center;justify-content:center;gap:1rem;background:#0b0f14;color:#e8eef5}
a.btn{background:#2f81f7;color:#fff;text-decoration:none;padding:14px 28px;border-radius:12px;font-weight:600}
small{color:#8aa0b6;max-width:22rem;text-align:center;line-height:1.4}</style></head>
<body><img src="/icons/apple-touch-icon.png" width="96" height="96" style="border-radius:22px" alt="">
<h1 style="margin:.25rem 0">$APP_TITLE $VERSION</h1>
<a class="btn" href="itms-services://?action=download-manifest&amp;url=https://$TS_HOST$OTA_SFX/app/manifest.plist">Install</a>
<small>Tap Install (Safari, Tailscale ON). After it installs, trust the developer cert under
Settings → General → VPN &amp; Device Management.</small></body></html>
EOF

# version.json — the installed app polls this to offer an in-app "Update available" button
# (compares `build` to its own CFBundleVersion, then opens `installURL`).
cat > "$IPA_DIR/version.json" <<EOF
{"version":"$VERSION","build":"$BUILD","installURL":"itms-services://?action=download-manifest&url=https://$TS_HOST$OTA_SFX/app/manifest.plist"}
EOF

# Copy the persistent payload into the live web bundle (idempotent; re-run safely).
publish_app_bundle

# --- direct USB install when a device is connected (skips OTA integrity entirely) --------
# A development-signed app installs over the cable immediately once the device is in the
# profile — which this build just ensured by registering the connected device. This is the
# most reliable path and sidesteps the OTA "integrity could not be verified" failure that
# happens when the phone wasn't connected during the build (so it's not in the profile).
installed_directly=0
if have xcrun; then
  udid_now="$(xcrun devicectl list devices 2>/dev/null | grep -oiE '[0-9A-F]{8}-[0-9A-F]{16}|[0-9A-F]{40}' | head -n1 || true)"
  if [[ -n "$udid_now" ]]; then
    info "iPhone connected ($udid_now) — installing directly over USB…"
    if xcrun devicectl device install app --device "$udid_now" "$IPA" 2>&1 | tee "$BUILD_DIR/devicectl-install.log"; then
      installed_directly=1
      ok "installed __APP_NAME__ directly to the device — look for it on the home screen"
    else
      warn "direct USB install failed (log: ${BUILD_DIR#$REPO_ROOT/}/devicectl-install.log) — use the OTA link below instead."
    fi
  fi
fi

# --- done --------------------------------------------------------------------------------
INSTALL_URL="itms-services://?action=download-manifest&url=https://$TS_HOST$OTA_SFX/app/manifest.plist"
echo
if [[ "$installed_directly" == "1" ]]; then
  c_green "Installed over USB. First launch: Settings → General → VPN & Device Management → trust your developer cert."
  info "Cable-free reinstalls are also live at:  https://$TS_HOST$OTA_SFX/app/"
else
  c_green "Install on the iPhone (Tailscale ON):"
  info "Open this page in Safari on the phone and tap Install:"
  echo "    https://$TS_HOST$OTA_SFX/app/"
  info "(or open the raw install link directly: $INSTALL_URL)"
  info "If you get \"integrity could not be verified\", the phone isn't in the signing profile yet —"
  info "connect it via USB and re-run so this build can register it (or it'll install directly over USB)."
  info "First launch: Settings → General → VPN & Device Management → trust your developer cert."
fi
