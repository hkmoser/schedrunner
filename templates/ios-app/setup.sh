#!/bin/bash
# One-time setup: replace template tokens with real app identifiers.
# Usage: bash setup.sh AppName com.example.myapp ABCDE12345
#   AppName           — CamelCase app name (e.g. MyDash)
#   com.example.myapp — reverse-DNS bundle ID prefix (e.g. com.example.myapp)
#   ABCDE12345        — Apple Developer Team ID (from developer.apple.com/account)
#
# After setup, commit the changes, then run: make deploy
set -euo pipefail

APP_NAME="${1:-}"
BUNDLE_ID_PREFIX="${2:-}"
TEAM_ID="${3:-}"

if [[ -z "$APP_NAME" || -z "$BUNDLE_ID_PREFIX" || -z "$TEAM_ID" ]]; then
  echo "Usage: bash setup.sh <AppName> <com.example.bundle> <TeamID>" >&2
  exit 1
fi

APP_NAME_LOWER=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]')
BUNDLE_ID="${BUNDLE_ID_PREFIX}.${APP_NAME_LOWER}"

echo "Setting up: APP_NAME=$APP_NAME  BUNDLE_ID=$BUNDLE_ID  TEAM_ID=$TEAM_ID"

# --- Substitute tokens in text files ---
find . -type f \( \
  -name "*.swift" -o -name "*.json" -o -name "*.yaml" -o -name "*.yml" \
  -o -name "*.md" -o -name "*.sh" -o -name "*.py" -o -name "*.ts" \
  -o -name "*.tsx" -o -name "*.js" -o -name "*.html" -o -name "*.css" \
  -o -name "*.xcconfig" -o -name "*.plist" -o -name "*.toml" \
  -o -name "Makefile" -o -name ".gitignore" -o -name ".env*" \
\) \
  -not -path './.git/*' \
  -not -path './node_modules/*' \
  -not -path './.build/*' | while read -r f; do
    sed -i \
      -e "s/__BUNDLE_ID__/$BUNDLE_ID/g" \
      -e "s/__BUNDLE_ID_PREFIX__/$BUNDLE_ID_PREFIX/g" \
      -e "s/__TEAM_ID__/$TEAM_ID/g" \
      -e "s/__APP_NAME_LOWER__/$APP_NAME_LOWER/g" \
      -e "s/__APP_NAME__/$APP_NAME/g" \
      "$f"
done

# --- Rename files that contain token names ---
for f in $(find . -type f -name "*__APP_NAME__*" -o -type f -name "*__APP_NAME_LOWER__*" \
                           -o -type f -name "*__BUNDLE_ID__*" 2>/dev/null); do
  newf=$(echo "$f" \
    | sed -e "s/__BUNDLE_ID__/$BUNDLE_ID/g" \
          -e "s/__APP_NAME_LOWER__/$APP_NAME_LOWER/g" \
          -e "s/__APP_NAME__/$APP_NAME/g")
  if [[ "$f" != "$newf" ]]; then
    mv "$f" "$newf"
    echo "renamed: $f"
  fi
done

echo ""
echo "Setup complete. Next steps:"
echo "  1. cp Deploy/.env.example Deploy/.env   # fill in TS_HOST"
echo "  2. make doctor                            # preflight check"
echo "  3. make deploy                            # bring up the server + PWA"
echo "  4. git add -A && git commit -m 'chore: initialize $APP_NAME from template'"
