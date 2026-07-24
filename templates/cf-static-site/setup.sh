#!/usr/bin/env bash
# setup.sh — bootstrap a new static site on Cloudflare Workers
# Usage: ./setup.sh primary.com [secondary1.com secondary2.com ...]
#
# Prerequisites:
#   - CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID set (or .env sourced)
#   - jq installed  (brew install jq)
#   - wrangler installed globally (npm install -g wrangler)
#   - Every domain already added as a zone in Cloudflare and delegated
#     (nameservers pointed to Cloudflare). See README.md.
set -euo pipefail

# ── Load .env if present ───────────────────────────────────────────────────
if [[ -f .env ]]; then
  # shellcheck source=/dev/null
  set -a; source .env; set +a
fi

# ── Args ───────────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "Usage: ./setup.sh primary.com [secondary1.com secondary2.com ...]" >&2
  exit 1
fi

PRIMARY="$1"
shift
SECONDARIES=("$@")

# ── Validate env ───────────────────────────────────────────────────────────
missing=()
[[ -z "${CLOUDFLARE_API_TOKEN:-}" ]] && missing+=("CLOUDFLARE_API_TOKEN")
[[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]] && missing+=("CLOUDFLARE_ACCOUNT_ID")
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: missing environment variable(s): ${missing[*]}" >&2
  echo "  Copy .env.example → .env and fill in your credentials, or export them." >&2
  exit 1
fi

command -v jq    >/dev/null 2>&1 || { echo "ERROR: jq not found — brew install jq" >&2; exit 1; }
command -v wrangler >/dev/null 2>&1 || { echo "ERROR: wrangler not found — npm install -g wrangler" >&2; exit 1; }

# ── Verify every domain zone exists in Cloudflare ─────────────────────────
cf_api() {
  curl -s -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
          -H "Content-Type: application/json" "$@"
}

check_zone() {
  local domain="$1"
  local resp
  resp=$(cf_api "https://api.cloudflare.com/client/v4/zones?name=${domain}&account.id=${CLOUDFLARE_ACCOUNT_ID}")
  local count
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

# ── Derive worker name (strip dots, keep it short and slug-safe) ───────────
WORKER_NAME=$(echo "$PRIMARY" | tr '.' '-' | tr '[:upper:]' '[:lower:]')

# ── Build routes array ─────────────────────────────────────────────────────
# primary + www.primary + each secondary + www.secondary
build_routes() {
  local domains=("$PRIMARY" "${SECONDARIES[@]}")
  local entries=()
  for d in "${domains[@]}"; do
    entries+=("{\"pattern\":\"${d}\",\"custom_domain\":true}")
    entries+=("{\"pattern\":\"www.${d}\",\"custom_domain\":true}")
  done
  # join with commas
  local IFS=','
  echo "[${entries[*]}]"
}

ROUTES=$(build_routes)

# ── Rewrite wrangler.jsonc ─────────────────────────────────────────────────
echo "Updating wrangler.jsonc…"

# jq can't parse jsonc (comments), so strip // … comments first, edit, then
# preserve the comment header lines from the original template.
HEADER=$(head -3 wrangler.jsonc)   # first 3 lines are comments we want to keep

# Strip single-line // comments, feed through jq, write back.
# We keep blank lines and structure; comments are collateral damage (acceptable
# for a one-time setup script — the result is plain JSON that wrangler accepts).
sed '/^[[:space:]]*\/\//d' wrangler.jsonc \
  | jq \
      --arg name "$WORKER_NAME" \
      --arg primary "$PRIMARY" \
      --argjson routes "$ROUTES" \
      '.name = $name | .vars.PRIMARY_DOMAIN = $primary | .routes = $routes' \
  > wrangler.jsonc.tmp

# Prepend the retained comment header
{
  echo "$HEADER"
  cat wrangler.jsonc.tmp
} > wrangler.jsonc

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
echo "  1. For each domain, confirm Cloudflare is the authoritative DNS"
echo "     (check dash.cloudflare.com → zone → Overview → 'Active')."
echo ""
echo "  2. SSL/TLS: set mode to 'Full (strict)' in each zone's SSL/TLS tab."
echo ""
echo "  3. If you migrated a live domain: restore any DNS records (A, MX,"
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
