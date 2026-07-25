#!/usr/bin/env bash
# setup.sh — bootstrap a new static site on Cloudflare Workers
# Usage: ./setup.sh primary.com [secondary1.com secondary2.com ...]
#
# Credentials are pulled from Google Cloud Secret Manager via schedrunner's
# secrets.sh. Store them once:
#   gcloud secrets create cloudflare-api-token --data-file=- <<< "tok_..."
#   gcloud secrets create cloudflare-account-id --data-file=- <<< "abc123"
#
# Prerequisites:
#   - schedrunner checked out at ~/Dropbox/Source/schedrunner (GCP SA configured)
#   - jq installed  (brew install jq)
#   - wrangler installed globally (npm install -g wrangler)
#   - Every domain already added as a zone in Cloudflare and delegated
#     (nameservers pointed to Cloudflare). See README.md.
set -euo pipefail

# ── Pull credentials from Secret Manager ──────────────────────────────────
SECRETS_SH="${SCHEDRUNNER_DIR:-$HOME/Dropbox/Source/schedrunner}/secrets.sh"
if [[ ! -f "$SECRETS_SH" ]]; then
  echo "ERROR: secrets.sh not found at $SECRETS_SH" >&2
  echo "  Set SCHEDRUNNER_DIR if schedrunner lives elsewhere." >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$SECRETS_SH"

export CLOUDFLARE_API_TOKEN
export CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN="$(get_secret cloudflare-api-token)"
CLOUDFLARE_ACCOUNT_ID="$(get_secret cloudflare-account-id)"

# ── Args ───────────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "Usage: ./setup.sh primary.com [secondary1.com secondary2.com ...]" >&2
  exit 1
fi

PRIMARY="$1"
shift
SECONDARIES=("$@")

# ── Validate credentials loaded ────────────────────────────────────────────
missing=()
[[ -z "${CLOUDFLARE_API_TOKEN:-}" ]] && missing+=("cloudflare-api-token")
[[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]] && missing+=("cloudflare-account-id")
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Secret Manager returned empty value for: ${missing[*]}" >&2
  echo "  Check that the secret exists and the GCP service account has secretAccessor." >&2
  exit 1
fi

command -v jq       >/dev/null 2>&1 || { echo "ERROR: jq not found — brew install jq" >&2; exit 1; }
command -v wrangler >/dev/null 2>&1 || { echo "ERROR: wrangler not found — npm install -g wrangler" >&2; exit 1; }

# ── Verify every domain zone exists in Cloudflare ─────────────────────────
cf_api() {
  curl -s -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
          -H "Content-Type: application/json" "$@"
}

check_zone() {
  local domain="$1"
  local resp count
  resp=$(cf_api "https://api.cloudflare.com/client/v4/zones?name=${domain}&account.id=${CLOUDFLARE_ACCOUNT_ID}")
  count=$(echo "$resp" | jq '.result | length')
  if [[ "$count" -eq 0 ]]; then
    echo "ERROR: domain '$domain' is not a zone in your Cloudflare account." >&2
    echo "  Add it at dash.cloudflare.com → 'Add a domain', then point nameservers before running setup." >&2
    exit 1
  fi
}

echo "Verifying Cloudflare zones…"
check_zone "$PRIMARY"
for d in "${SECONDARIES[@]}"; do
  check_zone "$d"
done
echo "All zones verified."

# ── Derive worker name ─────────────────────────────────────────────────────
WORKER_NAME=$(echo "$PRIMARY" | tr '.' '-' | tr '[:upper:]' '[:lower:]')

# ── Build routes array ─────────────────────────────────────────────────────
build_routes() {
  local domains=("$PRIMARY" "${SECONDARIES[@]}")
  local entries=()
  for d in "${domains[@]}"; do
    entries+=("{\"pattern\":\"${d}\",\"custom_domain\":true}")
    entries+=("{\"pattern\":\"www.${d}\",\"custom_domain\":true}")
  done
  local IFS=','
  echo "[${entries[*]}]"
}

ROUTES=$(build_routes)

# ── Rewrite wrangler.jsonc ─────────────────────────────────────────────────
echo "Updating wrangler.jsonc…"
HEADER=$(head -3 wrangler.jsonc)

sed '/^[[:space:]]*\/\//d' wrangler.jsonc \
  | jq \
      --arg name "$WORKER_NAME" \
      --arg primary "$PRIMARY" \
      --argjson routes "$ROUTES" \
      '.name = $name | .vars.PRIMARY_DOMAIN = $primary | .routes = $routes' \
  > wrangler.jsonc.tmp

{ echo "$HEADER"; cat wrangler.jsonc.tmp; } > wrangler.jsonc
rm wrangler.jsonc.tmp
echo "wrangler.jsonc updated."

# ── Deploy ─────────────────────────────────────────────────────────────────
echo ""
echo "Deploying worker '${WORKER_NAME}'…"
wrangler deploy

# ── Post-deploy checklist ──────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Deploy complete. Manual steps remaining:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  1. Confirm Cloudflare is authoritative for each domain"
echo "     (zone Overview shows 'Active' in the dashboard)."
echo ""
echo "  2. SSL/TLS: set mode to 'Full (strict)' in each zone's SSL/TLS tab."
echo ""
echo "  3. If you migrated a live domain: restore DNS records (A, MX,"
echo "     CNAME, TXT) that existed before the nameserver switch."
echo ""
echo "  4. Smoke test:"
all_domains=("$PRIMARY" "${SECONDARIES[@]}")
for d in "${all_domains[@]}"; do
  echo "       curl -sI https://${d}/ | head -2"
  echo "       curl -sI https://www.${d}/ | head -2"
done
echo ""
echo "  5. Edit public/index.html to replace the placeholder welcome page."
echo ""
echo "  6. git add -A && git commit -m 'chore: init ${PRIMARY}'"
echo ""
