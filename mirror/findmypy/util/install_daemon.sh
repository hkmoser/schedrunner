#!/bin/bash
# install_daemon.sh — (re)install the afm_live_daemon LaunchAgent. Idempotent.
#
# Designed to be called by schedrunner's auto-deploy AFTER the repo is updated,
# so a deploy always lands the latest plist and restarts the daemon on the new
# code. Safe to run by hand too.
#
#   bash util/install_daemon.sh            # install / reload (default)
#   bash util/install_daemon.sh uninstall  # stop + remove the agent
#
# What it does: copies the plist into ~/Library/LaunchAgents, boots out any
# running instance, then bootstraps the new one. launchd (KeepAlive) then keeps
# the daemon alive across crashes and session deaths.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.joemoser.afm-daemon"
SRC_PLIST="$REPO/util/$LABEL.plist"
DEST_DIR="$HOME/Library/LaunchAgents"
DEST_PLIST="$DEST_DIR/$LABEL.plist"
GUI="gui/$(id -u)"

uninstall() {
    echo "[install_daemon] uninstalling $LABEL"
    launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
    rm -f "$DEST_PLIST"
    # Releasing the daemon frees /tmp/afm_live.lock so the cron fallback resumes.
    echo "[install_daemon] removed. cron afm_live.py fallback can resume."
}

install() {
    if [ ! -f "$SRC_PLIST" ]; then
        echo "[install_daemon] ERROR: $SRC_PLIST not found" >&2
        exit 1
    fi
    mkdir -p "$DEST_DIR"
    # launchd fails silently if the StandardOutPath dir is missing — ensure ~/log.
    mkdir -p "$HOME/log"
    cp "$SRC_PLIST" "$DEST_PLIST"
    echo "[install_daemon] copied plist -> $DEST_PLIST"

    # Reload: bootout old (ignore if absent), bootstrap new, enable.
    launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$GUI" "$DEST_PLIST"
    launchctl enable "$GUI/$LABEL"
    echo "[install_daemon] (re)loaded $LABEL — launchd will keep it alive"
    echo "[install_daemon] logs: ~/log/afm-daemon.log"
}

case "${1:-install}" in
    uninstall|remove|stop) uninstall ;;
    install|reload|"")      install ;;
    *) echo "usage: $0 [install|uninstall]" >&2; exit 2 ;;
esac
