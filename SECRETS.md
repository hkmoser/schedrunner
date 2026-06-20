# Shared secrets — Google Cloud Secret Manager

Every schedrunner-managed repo reads the **same** credentials from one place —
Google Cloud Secret Manager — through the `secrets.sh` helper. Secrets never live
in a repo, in Dropbox, or on disk at rest; they are fetched on demand.

```bash
source /Users/joemoser/Dropbox/Source/schedrunner/secrets.sh
export OPENAI_API_KEY="$(get_secret openai-api-key)"   # fetch on demand
secret_to_var db-password DB_PASSWORD                   # or export directly
```

A single dedicated **read-only service account** backs every repo, so they all
share the same creds. Auth is isolated to its own gcloud config, so your
interactive `gcloud` account is never touched.

---

## One-time setup on the Mac

These run where `gcloud` is authenticated (the Mac), not from a cloud session.
Replace `YOUR_PROJECT` with your GCP project id.

```bash
# 1. Select the project and enable Secret Manager
gcloud config set project YOUR_PROJECT
gcloud services enable secretmanager.googleapis.com

# 2. Create the read-only service account
gcloud iam service-accounts create schedrunner-secrets \
  --display-name="schedrunner secret accessor"

# 3. Grant it read-only access to secrets (accessor only — cannot create/modify)
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="serviceAccount:schedrunner-secrets@YOUR_PROJECT.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# 4. Create a key and store it OUTSIDE Dropbox/repos (so it is never synced,
#    committed, or wiped by auto-deploy)
mkdir -p ~/.config/schedrunner
gcloud iam service-accounts keys create ~/.config/schedrunner/gcp-sa.json \
  --iam-account=schedrunner-secrets@YOUR_PROJECT.iam.gserviceaccount.com
chmod 600 ~/.config/schedrunner/gcp-sa.json

# 5. Point secrets.sh at the project (the key path defaults to the file above)
cat > ~/.config/schedrunner/secrets.env <<'EOF'
SCHEDRUNNER_GCP_PROJECT=YOUR_PROJECT
EOF
chmod 600 ~/.config/schedrunner/secrets.env

# 6. Verify (no secret value is printed by `check`)
cd ~/Dropbox/Source/schedrunner
./secrets.sh check
```

## Managing secrets

```bash
# Create a secret
printf 'sk-xxxxx' | gcloud secrets create openai-api-key --data-file=-

# Rotate it (adds a new version; get_secret uses "latest" automatically)
printf 'sk-yyyyy' | gcloud secrets versions add openai-api-key --data-file=-

# List / read back
gcloud secrets list
./secrets.sh get openai-api-key
```

Secret names should match Secret Manager's rules (letters, digits, `-`, `_`).

## Using secrets from a repo

`secrets.sh` is safe to `source` — it sets no shell options and leaks nothing
into your shell. Reach for a secret only when you need it:

```bash
#!/bin/bash
set -euo pipefail
source /Users/joemoser/Dropbox/Source/schedrunner/secrets.sh

API_KEY="$(get_secret openai-api-key)"      # variable, in-memory only
secret_to_var db-password DB_PASSWORD        # export DB_PASSWORD
get_secret some-secret 3                      # a specific version
```

Python (or any language) can shell out the same way, or read
`SCHEDRUNNER_GCP_PROJECT` and use the Google client library against the same
service-account key — the helper is just the convenient default.

## API reference (`secrets.sh`)

| Call | Effect |
|------|--------|
| `get_secret <name> [version]`   | Print the secret value to stdout (default version `latest`) |
| `secret_to_var <name> <var>`    | `export <var>` with the secret value |
| `secrets_check` / `./secrets.sh check` | Validate config + auth without printing any secret |
| `./secrets.sh get <name> [ver]` | CLI form of `get_secret` |

Configuration (env vars win; otherwise read from `secrets.env`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCHEDRUNNER_GCP_PROJECT`   | — (required) | project id holding the secrets |
| `SCHEDRUNNER_GCP_SA_KEY`    | `~/.config/schedrunner/gcp-sa.json` | service-account key file |
| `SCHEDRUNNER_SECRETS_CONF`  | `~/.config/schedrunner/secrets.env` | config file to source |
| `SCHEDRUNNER_GCLOUD_CONFIG` | `~/.config/schedrunner/gcloud` | isolated gcloud config dir |

## Security notes

- **Key lives outside Dropbox/repos** (`~/.config/schedrunner/`), so it is never
  synced across devices, committed, or wiped by auto-deploy's hard reset.
- **Read-only role**: `roles/secretmanager.secretAccessor` can read secret values
  but cannot create, modify, or delete them — limiting blast radius if the key
  leaks. Manage secrets with your own (admin) gcloud account.
- **Shared by design**: one service account → every repo can read every secret
  ("same creds as needed"). If you later want a repo restricted to specific
  secrets, grant `secretAccessor` on those individual secrets to a separate
  service account instead of project-wide.
- **Rotation**: rotate values with `gcloud secrets versions add`; rotate the key
  with `gcloud iam service-accounts keys create` + delete the old key id.
- **Under launchd**: scripts that source `secrets.sh` need `gcloud` on `PATH`.
  Homebrew installs it under `/opt/homebrew/bin` (Apple Silicon) — make sure that
  is on the script's `PATH`, as the LaunchAgent starts with a minimal environment.
  `secrets.sh` fails loudly (`gcloud CLI not found on PATH`) if it is missing.
