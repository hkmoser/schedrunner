# cf-static-site

A template for deploying simple static sites to **Cloudflare Workers with static assets**. Clone once per domain; run `setup.sh` to configure and deploy.

## How it works

The Worker binds your `public/` folder as a static asset binding (`ASSETS`). Every request hits the Worker first (`run_worker_first: true`). Requests for the primary domain or `www.<primary>` are served directly from the asset binding; all other hostnames (secondary domains, aliases) receive a `301` redirect to `https://<primary>` preserving the path and query string.

```
public/index.html     ← the actual page, no build step
src/index.js          ← the Worker (redirect logic)
wrangler.jsonc        ← config; setup.sh rewrites name, vars, routes
setup.sh              ← one-command bootstrap
```

## Credentials

Cloudflare credentials are stored in **Google Cloud Secret Manager** and fetched at deploy time via schedrunner's `secrets.sh`. Store them once:

```bash
source ~/Dropbox/Source/schedrunner/secrets.sh

# Store your Cloudflare API token (see permissions below)
gcloud secrets create cloudflare-api-token \
  --replication-policy=automatic \
  --data-file=- <<< "your-token-here"

# Store your account ID (dash.cloudflare.com → top-right → Account ID)
gcloud secrets create cloudflare-account-id \
  --replication-policy=automatic \
  --data-file=- <<< "your-account-id-here"
```

`setup.sh` sources `secrets.sh` automatically. No `.env` files, no credentials in the repo.

If schedrunner lives somewhere other than `~/Dropbox/Source/schedrunner`, set `SCHEDRUNNER_DIR` before running `setup.sh`.

### Required API token permissions

Create a token at **dash.cloudflare.com → Profile → API Tokens** (scope to your account, not a single zone):

| Permission | Why |
|------------|-----|
| **Workers Scripts : Edit** | Deploy the worker |
| **Zone : Read** | Verify domain zones exist |
| **DNS : Edit** | Wrangler wires custom domains |

## Prerequisites

| Tool | Install |
|------|---------|
| [wrangler](https://developers.cloudflare.com/workers/wrangler/) | `npm install -g wrangler` |
| [jq](https://jqlang.org) | `brew install jq` |
| schedrunner with GCP SA configured | See `SECRETS.md` in schedrunner |
| Domains added as Cloudflare zones | See below |

## One manual step before setup.sh will work

**Each domain must be a zone in Cloudflare and must be delegated (nameservers pointing to Cloudflare) before running `setup.sh`.**

Steps per domain:
1. Go to **dash.cloudflare.com → Add a domain** and follow the wizard.
2. Cloudflare gives you two nameserver hostnames (e.g. `ava.ns.cloudflare.com`).
3. Set those as the nameservers at your registrar (GoDaddy, Namecheap, Porkbun, etc.).
4. Wait for propagation — the zone shows **Active** in Cloudflare when done.

> **If migrating a live domain:** export your existing DNS records first (DNS → Records → Export in the Cloudflare dashboard). After the nameserver switch you'll need to re-enter any A/MX/CNAME/TXT records that matter.

## Deploying a new site

```bash
# 1. Clone the template into your new site's repo
git clone https://github.com/hkmoser/cf-static-site my-site
cd my-site && rm -rf .git && git init

# 2. Bootstrap (primary domain first, then any secondaries)
./setup.sh primary.com alias1.com alias2.com

# 3. Edit the welcome page
$EDITOR public/index.html

# 4. Commit and push
git add -A && git commit -m "chore: init primary.com"
git remote add origin git@github.com:hkmoser/<repo>.git
git push -u origin main
```

## Editing content

All content lives in `public/`. The site is a single HTML file with inline CSS — no bundler, no framework, no build step. To redeploy after an edit:

```bash
wrangler deploy
```

## Adding a domain to an existing site

1. Add the domain as a zone in Cloudflare and delegate nameservers (see above).
2. Append entries to `routes` in `wrangler.jsonc`:
   ```jsonc
   { "pattern": "newdomain.com",     "custom_domain": true },
   { "pattern": "www.newdomain.com", "custom_domain": true }
   ```
3. Run `wrangler deploy`.
4. Confirm the zone shows Active, then smoke-test with `curl -sI https://newdomain.com/`.

The Worker automatically 301-redirects any non-primary hostname — no code change needed.

## Environment variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `cloudflare-api-token` | GCP Secret Manager | Auth for wrangler + CF API |
| `cloudflare-account-id` | GCP Secret Manager | Scopes zone lookup to your account |
| `PRIMARY_DOMAIN` | `wrangler.jsonc → vars` | Canonical hostname; set by setup.sh |
| `SCHEDRUNNER_DIR` | shell (optional) | Override schedrunner path (default: `~/Dropbox/Source/schedrunner`) |
