#!/bin/bash
# setup.sh — substitute template tokens with real app identifiers.
# Run once immediately after cloning from the template repo.
#
# Usage:
#   bash setup.sh <AppName> <bundle.id.prefix> <TEAM_ID>
#
# Example:
#   bash setup.sh MyWeather com.example AB12CD34EF
#
# This produces:
#   APP_NAME          = MyWeather
#   APP_NAME_LOWER    = myweather
#   BUNDLE_ID_PREFIX  = com.example
#   BUNDLE_ID         = com.example.myweather
#   TEAM_ID           = AB12CD34EF
set -euo pipefail

APP_NAME="${1:-}"
BUNDLE_ID_PREFIX="${2:-}"
TEAM_ID="${3:-}"

if [[ -z "$APP_NAME" || -z "$BUNDLE_ID_PREFIX" || -z "$TEAM_ID" ]]; then
  echo "usage: bash setup.sh <AppName> <bundle.id.prefix> <TEAM_ID>"
  echo "  AppName         CamelCase app name, e.g. MyWeather"
  echo "  bundle.id.prefix  e.g. com.example"
  echo "  TEAM_ID         10-char Apple team ID, e.g. AB12CD34EF"
  exit 1
fi

APP_NAME_LOWER=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]')
BUNDLE_ID="${BUNDLE_ID_PREFIX}.${APP_NAME_LOWER}"

echo "==> Substituting tokens:"
echo "    APP_NAME         = $APP_NAME"
echo "    APP_NAME_LOWER   = $APP_NAME_LOWER"
echo "    BUNDLE_ID_PREFIX = $BUNDLE_ID_PREFIX"
echo "    BUNDLE_ID        = $BUNDLE_ID"
echo "    TEAM_ID          = $TEAM_ID"
echo ""

# Rename files that contain __APP_NAME__ or __BUNDLE_ID__ in their name
find . -type f \( -name '*__APP_NAME__*' -o -name '*__BUNDLE_ID__*' \) | while read f; do
  new=$(echo "$f" \
    | sed "s/__APP_NAME__/$APP_NAME/g" \
    | sed "s/__BUNDLE_ID__/$BUNDLE_ID/g")
  mkdir -p "$(dirname "$new")"
  mv "$f" "$new"
  echo "  renamed: $f -> $new"
done

# Substitute tokens in all text files
find . -type f \
  -not -path './.git/*' \
  -not -name 'setup.sh' \
  -not -name '*.png' \
  -not -name '*.pdf' \
  -not -name '*.p8' \
  -not -name '*.mobileprovision' | while read f; do
  # Skip binary files
  file "$f" 2>/dev/null | grep -qE 'text|JSON|XML|plist|script|source|data' || continue
  sed -i \
    -e "s|__BUNDLE_ID_PREFIX__|$BUNDLE_ID_PREFIX|g" \
    -e "s|__BUNDLE_ID__|$BUNDLE_ID|g" \
    -e "s|__APP_NAME_LOWER__|$APP_NAME_LOWER|g" \
    -e "s|__APP_NAME__|$APP_NAME|g" \
    -e "s|__TEAM_ID__|$TEAM_ID|g" \
    "$f" 2>/dev/null || true
done

echo "==> Done. Next steps:"
echo "    1. cp Deploy/.env.example Deploy/.env  # fill in TS_HOST and keys"
echo "    2. make doctor                          # preflight checks"
echo "    3. make deploy                          # bring up the server"
