#!/usr/bin/env bash
# setup.sh — bootstrap a new static site on Cloudflare Workers
# Usage: ./setup.sh primary.com [secondary1.com secondary2.com ...]
#
# Credentials are pulled from Google Cloud Secret Manager via schedrunner's
# secrets.sh. Store them once:
#   gcloud secrets create cloudflare-api-token --data-file=- <<< "tok_..."
#   gcloud secrets create cloudflare-account-id --data-file=- <<< "abc123"
#
# For the API token, create a Custom Token with these permissions:
#   Zone / Zone / Edit             (account-level — needed to create zones)
#   Zone / Zone Settings / Edit    (account-level)
#   Zone / Workers Routes / Edit   (account-level)
#   Account / Workers Scripts / Edit
#
# Zone handling:
#   - If a domain zone doesn't exist yet, setup.sh creates it via the CF API
#     and prints the nameservers to set at your registrar.
#   - If a zone exists but is Pending (nameservers not yet delegated), setup.sh
#     prints the nameservers and exits — re-run after delegation is confirmed.
#   - If all zones are Active, setup.sh deploys the worker.
#
# Prerequisites:
#   - schedrunner checked out at ~/Dropbox/Source/schedrunner (GCP SA configured)
#   - jq installed  (brew install jq)
#   - wrangler installed globally (npm install -g wrangler)
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

# ── Zone management ───────────────────────────────────────────────────────
cf_api() {
  curl -s -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
          -H "Content-Type: application/json" "$@"
}

# ensure_zone <domain>
# Creates the zone if it doesn't exist. Returns 0 if zone is Active and ready,
# 1 if zone is Pending (nameservers not yet delegated — caller should exit).
ensure_zone() {
  local domain="$1"
  local resp zone_id status nameservers

  resp=$(cf_api "https://api.cloudflare.com/client/v4/zones?name=${domain}&account.id=${CLOUDFLARE_ACCOUNT_ID}")
  zone_id=$(echo "$resp" | jq -r '.result[0].id // empty')

  if [[ -z "$zone_id" ]]; then
    echo "  $domain: zone not found — creating…"
    resp=$(cf_api -X POST "https://api.cloudflare.com/client/v4/zones" \
      -d "{\"name\":\"${domain}\",\"account\":{\"id\":\"${CLOUDFLARE_ACCOUNT_ID}\"},\"jump_start\":false,\"type\":\"full\"}")
    zone_id=$(echo "$resp" | jq -r '.result.id // empty')
    if [[ -z "$zone_id" ]]; then
      echo "ERROR: failed to create zone for $domain:" >&2
      echo "$resp" | jq -r '.errors[]?.message' >&2
      exit 1
    fi
    echo "  $domain: zone created."
  fi

  status=$(cf_api "https://api.cloudflare.com/client/v4/zones/${zone_id}" | jq -r '.result.status')
  nameservers=$(cf_api "https://api.cloudflare.com/client/v4/zones/${zone_id}" \
    | jq -r '.result.name_servers[]' 2>/dev/null | tr '\n' ' ')

  if [[ "$status" != "active" ]]; then
    echo ""
    echo "  ⚠  $domain is $status — nameservers not yet delegated."
    echo "     Set these at your registrar, then re-run setup.sh:"
    echo ""
    for ns in $nameservers; do echo "       $ns"; done
    echo ""
    return 1
  fi

  echo "  $domain: Active ✓"
  return 0
}

echo "Checking Cloudflare zones…"
pending=0
all_domains=("$PRIMARY" "${SECONDARIES[@]}")
for d in "${all_domains[@]}"; do
  ensure_zone "$d" || pending=1
done

if [[ "$pending" -eq 1 ]]; then
  echo "One or more zones are pending delegation. Re-run setup.sh once nameservers are active."
  exit 1
fi
echo "All zones active."

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
echo "  1. SSL/TLS: set mode to 'Full (strict)' in each zone's SSL/TLS tab"
echo "     in the Cloudflare dashboard."
echo ""
echo "  2. If you migrated a live domain: restore DNS records (A, MX,"
echo "     CNAME, TXT) that existed before the nameserver switch."
echo ""
echo "  3. Smoke test:"
for d in "${all_domains[@]}"; do
  echo "       curl -sI https://${d}/ | head -2"
  echo "       curl -sI https://www.${d}/ | head -2"
done
echo ""
echo "  5. Edit public/index.html to replace the placeholder welcome page."
echo ""
echo "  6. git add -A && git commit -m 'chore: init ${PRIMARY}'"
echo ""
