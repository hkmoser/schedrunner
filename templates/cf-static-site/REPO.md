# <repo name>

**Purpose:** <one line>
**Type:** app (Cloudflare Workers static site)
**Domains:** primary.com [+ secondary1.com ...]

## Runtime & deploy
- Stack: static HTML/CSS + Cloudflare Worker (redirect logic only)
- Deploys via: schedrunner (`service.yaml` → `npx wrangler deploy`)
- No build step; `public/` is served directly from the ASSETS binding

## Domains
- Primary: <primary.com> — served from ASSETS
- Aliases: <www.primary.com, secondary.com, ...> — 301 → primary

## Key files
- `public/index.html` — the site (edit this)
- `src/index.js` — Worker: serve primary, redirect all others
- `wrangler.jsonc` — name, PRIMARY_DOMAIN, routes

## Invariants / gotchas
- Every domain in `routes` must be an active zone in Cloudflare before deploy
- `run_worker_first: true` means the Worker sees every request, including 404s
- Adding a domain: append two `routes` entries (bare + www), then `wrangler deploy`
- Credentials come from GCP Secret Manager (`cloudflare-api-token`, `cloudflare-account-id`)
  via schedrunner's `secrets.sh` — never stored in the repo or in env files
