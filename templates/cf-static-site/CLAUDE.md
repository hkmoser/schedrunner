# CLAUDE.md — cf-static-site

A Cloudflare Workers static site. No build step, no framework. Worker redirect logic + static assets.

## Deploy flow

```
public/          ← static files served directly from ASSETS binding
src/index.js     ← Worker: serve primary, redirect everything else
wrangler.jsonc   ← config (name, PRIMARY_DOMAIN, routes)
```

First deploy (new site):
```bash
cp .env.example .env          # fill CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID
./setup.sh primary.com [secondaries...]
```

Subsequent deploys (content or config change):
```bash
wrangler deploy
```

## Editing content

Edit `public/index.html` directly — inline CSS, no build. Redeploy with `wrangler deploy`.

To add a page: create `public/about.html`; it's served at `/about.html` automatically via the ASSETS binding.

## Adding a domain

1. Add the domain as a zone in Cloudflare and delegate nameservers (see README.md).
2. Add two entries to `routes` in `wrangler.jsonc`:
   ```jsonc
   { "pattern": "newdomain.com",     "custom_domain": true },
   { "pattern": "www.newdomain.com", "custom_domain": true }
   ```
3. Run `wrangler deploy`.

The Worker 301-redirects any non-primary hostname to `https://<PRIMARY_DOMAIN>` automatically — no code change needed.

## Changing the primary domain

1. Update `vars.PRIMARY_DOMAIN` in `wrangler.jsonc`.
2. Make sure the new primary has entries in `routes`.
3. Run `wrangler deploy`.

## Branch / PR convention

Work on a feature branch; open a PR to merge to `main`.

**Before pushing to a branch to update a PR, check whether that PR is already closed/merged.** If it is, do not push to the old branch — start a fresh branch off `main` and open a new PR. One merged (or closed) PR = one finished unit of work.

## Dry-run check

```bash
npx wrangler deploy --dry-run
```

Validates config and assets without touching Cloudflare. Run this before opening a PR that changes `wrangler.jsonc` or `src/index.js`.
