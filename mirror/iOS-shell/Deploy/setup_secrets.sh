#!/usr/bin/env bash
# One-time (idempotent) Google Secret Manager setup for the in-app Settings page: enables the
# API and best-effort grants the ADC identity access. Skips cleanly when gcloud is absent or
# SECRETS_BACKEND=file (the Settings page then falls back to a 0600 local file). No sudo.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

[[ "${SECRETS_BACKEND:-}" == "file" ]] && { info "SECRETS_BACKEND=file — skipping Secret Manager setup"; exit 0; }

if ! have gcloud; then
  warn "gcloud not found — Secret Manager setup skipped. The Settings page will fall back to a"
  info "0600 local file (~/.config/dashboard/secrets.json). Install the Google Cloud SDK to use Secret Manager."
  exit 0
fi

# Project: BQ_PROJECT, else the project segment of BQ_DATASET ("project.dataset"), else gcloud's.
PROJECT="${BQ_PROJECT:-}"
if [[ -z "$PROJECT" && -n "${BQ_DATASET:-}" && "$BQ_DATASET" == *.* ]]; then PROJECT="${BQ_DATASET%%.*}"; fi
[[ -z "$PROJECT" ]] && PROJECT="$(gcloud config get-value project 2>/dev/null)"
if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  warn "no GCP project resolved (set BQ_DATASET='project.dataset' or BQ_PROJECT) — skipping Secret Manager setup"
  exit 0
fi

c_bold "== Secret Manager setup (project: $PROJECT) =="

# 1) Enable the API (idempotent; needs serviceusage.services.enable, which project owners have).
if gcloud services enable secretmanager.googleapis.com --project "$PROJECT" 2>/tmp/dashboard-sm.err; then
  ok "Secret Manager API enabled"
else
  warn "couldn't enable the Secret Manager API:"
  info "$(cat /tmp/dashboard-sm.err 2>/dev/null)"
  info "Enable it once by hand: gcloud services enable secretmanager.googleapis.com --project $PROJECT"
fi

# 2) Best-effort grant the active identity access. A no-op if you're already project owner;
#    a clean skip if you lack setIamPolicy. (Assumes the ADC identity == the active gcloud
#    account, the usual case for `gcloud auth application-default login`.)
ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
if [[ -n "$ACCOUNT" && "$ACCOUNT" != "(unset)" ]]; then
  member_type="user"; [[ "$ACCOUNT" == *.gserviceaccount.com ]] && member_type="serviceAccount"
  if gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="$member_type:$ACCOUNT" --role="roles/secretmanager.admin" \
        --condition=None >/dev/null 2>/tmp/dashboard-iam.err; then
    ok "granted $ACCOUNT roles/secretmanager.admin"
  else
    info "skipped IAM grant for $ACCOUNT (likely already owner, or no setIamPolicy permission):"
    info "$(head -n1 /tmp/dashboard-iam.err 2>/dev/null)"
  fi
fi

ok "Secret Manager ready — enter settings in the app under System → Settings"
