#!/bin/bash
# install_launchagent.sh — install a headless LaunchAgent that runs the export
# every hour with NO Terminal window.
#
# It execs the Python interpreter directly, so macOS attributes Full Disk Access
# to that interpreter — grant FDA to it ONCE (path printed at the end) and the
# export runs silently in the background from then on.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$(command -v python3)}"
# Resolve to the real interpreter binary (the thing TCC/FDA is keyed on).
PYREAL="$("$PYTHON" -c 'import sys; print(sys.executable)')"
INTERVAL="${INTERVAL:-3600}"   # seconds between runs (default hourly)

LABEL="com.joemoser.messages-export"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/messages-export.log"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYREAL</string>
        <string>$SCRIPT_DIR/export_messages.py</string>
    </array>
    <key>StartInterval</key>
    <integer>$INTERVAL</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
PLIST_EOF

# Reload if already installed.
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed LaunchAgent: $PLIST"
echo "  runs every ${INTERVAL}s, headless, logging to $LOG"
echo
echo "ONE-TIME SETUP — grant Full Disk Access to the interpreter so it can read"
echo "chat.db and the AddressBook DB:"
echo "  1. Reveal it in Finder:  open -R \"$PYREAL\""
echo "  2. System Settings → Privacy & Security → Full Disk Access → +"
echo "     (in the file picker press Cmd+Shift+G and paste the path if needed)"
echo "  3. Add: $PYREAL"
echo
echo "To remove later:  launchctl unload \"$PLIST\" && rm \"$PLIST\""
