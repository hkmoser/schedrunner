#!/bin/bash
# setup_yolink.sh — One-time YoLink credential setup.
#
# Prompts for your YoLink User Access Credentials (UAID + Secret Key) and
# writes them into .env. Get the values from the YoLink mobile app:
#   Settings → Account → Advanced Settings → User Access Credentials (UAC)
#
# Usage:  bash setup_yolink.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
TEMPLATE="$REPO_DIR/.env.template"

# Set (or replace) a KEY=VALUE line in .env without touching other keys.
# Uses grep filtering rather than sed so secret characters (= + / etc.) are
# written literally with no escaping surprises.
set_env() {
    local key="$1" val="$2" tmp
    tmp="$(mktemp)"
    if [ -f "$ENV_FILE" ]; then
        grep -v "^${key}=" "$ENV_FILE" > "$tmp" || true
    fi
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$ENV_FILE"
}

# Make sure a .env exists. Seed from the template if present so other keys
# (HA_TOKEN, HUE_KEY, …) are preserved, then we overwrite just the YoLink lines.
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$TEMPLATE" ]; then
        cp "$TEMPLATE" "$ENV_FILE"
        echo "Created $ENV_FILE from .env.template"
    else
        : > "$ENV_FILE"
        echo "Created empty $ENV_FILE"
    fi
fi

echo
echo "Enter your YoLink User Access Credentials (from the YoLink app:"
echo "  Settings → Account → Advanced Settings → User Access Credentials)."
echo

read -r -p "YoLink UAID (ua_...): " UAID
# Silent prompt for the secret so it isn't echoed to the terminal / scrollback.
read -r -s -p "YoLink Secret Key (sec_v1_...): " SECRET
echo

# Basic sanity checks — warn but don't hard-fail (formats may change over time).
if [ -z "$UAID" ] || [ -z "$SECRET" ]; then
    echo "ERROR: both values are required; nothing written." >&2
    exit 1
fi
case "$UAID" in
    ua_*) ;;
    *) echo "Warning: UAID usually starts with 'ua_' — got '${UAID}'. Continuing." >&2 ;;
esac
case "$SECRET" in
    sec_*) ;;
    *) echo "Warning: Secret Key usually starts with 'sec_'. Continuing." >&2 ;;
esac

set_env "YOLINK_USER_ACCESS_ID" "$UAID"
set_env "YOLINK_SECRET_KEY" "$SECRET"

# .env holds secrets — keep it owner-only.
chmod 600 "$ENV_FILE" 2>/dev/null || true

echo
echo "Wrote YoLink credentials to $ENV_FILE"
echo "  YOLINK_USER_ACCESS_ID=${UAID}"
echo "  YOLINK_SECRET_KEY=********  (hidden)"
echo
echo "Restart the service (bash run.sh) and look for:"
echo "  YoLink authenticated, home_id=..."
