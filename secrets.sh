#!/bin/bash
# secrets.sh — shared accessor for Google Cloud Secret Manager.
#
# Goal: every schedrunner-managed repo reads the SAME shared credentials through
# ONE helper, so secrets live in exactly one place (GCP Secret Manager) and never
# in a repo, in Dropbox, or on disk at rest.
#
# Source it from any script and fetch on demand:
#   source /Users/joemoser/Dropbox/Source/schedrunner/secrets.sh
#   API_KEY="$(get_secret openai-api-key)"        # prints the value
#   secret_to_var db-password DB_PASSWORD          # exports DB_PASSWORD
#
# Or use it as a CLI:
#   ./secrets.sh get openai-api-key
#   ./secrets.sh get openai-api-key 3              # a specific version
#   ./secrets.sh check                             # verify auth + config only
#
# Configuration (env vars win; otherwise read from the config file below):
#   SCHEDRUNNER_GCP_PROJECT    GCP project id that holds the secrets   (required)
#   SCHEDRUNNER_GCP_SA_KEY     path to the service-account key json
#                              (default: ~/.config/schedrunner/gcp-sa.json)
#   SCHEDRUNNER_SECRETS_CONF   config file to source for the above
#                              (default: ~/.config/schedrunner/secrets.env)
#   SCHEDRUNNER_GCLOUD_CONFIG  isolated gcloud config dir for the service account
#                              (default: ~/.config/schedrunner/gcloud)
#
# Auth is a dedicated, READ-ONLY service account (roles/secretmanager.secretAccessor)
# activated into an ISOLATED gcloud config, so it never disturbs your interactive
# `gcloud` account. See SECRETS.md for the one-time setup on the Mac.
#
# NOTE: safe to `source` — it sets no global shell options (no set -e/-u) and
# does not leak CLOUDSDK_CONFIG into the calling shell.

# ---- configuration (resolved at source time) ------------------------------
: "${SCHEDRUNNER_SECRETS_CONF:=$HOME/.config/schedrunner/secrets.env}"
# shellcheck disable=SC1090
[[ -f "$SCHEDRUNNER_SECRETS_CONF" ]] && source "$SCHEDRUNNER_SECRETS_CONF"
: "${SCHEDRUNNER_GCP_SA_KEY:=$HOME/.config/schedrunner/gcp-sa.json}"
: "${SCHEDRUNNER_GCLOUD_CONFIG:=$HOME/.config/schedrunner/gcloud}"

_secrets_err() { echo "secrets.sh: $*" >&2; }

# Run gcloud with the service account's isolated config, without leaking that
# config dir into the caller's environment (local -x is function-scoped).
_sr_gcloud() { local -x CLOUDSDK_CONFIG="$SCHEDRUNNER_GCLOUD_CONFIG"; gcloud "$@"; }

# Validate config and ensure the service account is the active gcloud identity.
# Cached for the life of the shell via _SECRETS_READY.
_secrets_init() {
  [[ -n "${_SECRETS_READY:-}" ]] && return 0

  if [[ -z "${SCHEDRUNNER_GCP_PROJECT:-}" ]]; then
    _secrets_err "SCHEDRUNNER_GCP_PROJECT is not set (configure $SCHEDRUNNER_SECRETS_CONF)"
    return 1
  fi
  command -v gcloud >/dev/null 2>&1 || { _secrets_err "gcloud CLI not found on PATH"; return 1; }
  if [[ ! -f "$SCHEDRUNNER_GCP_SA_KEY" ]]; then
    _secrets_err "service-account key not found: $SCHEDRUNNER_GCP_SA_KEY"
    return 1
  fi

  local sa_email active
  sa_email="$(sed -n 's/.*"client_email"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
              "$SCHEDRUNNER_GCP_SA_KEY" | head -1)"
  active="$(_sr_gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)"
  if [[ -z "$sa_email" || "$active" != "$sa_email" ]]; then
    _sr_gcloud auth activate-service-account ${sa_email:+"$sa_email"} \
        --key-file="$SCHEDRUNNER_GCP_SA_KEY" --quiet >/dev/null 2>&1 \
      || { _secrets_err "failed to activate service account from $SCHEDRUNNER_GCP_SA_KEY"; return 1; }
  fi
  _SECRETS_READY=1
}

# get_secret <name> [version] -> prints the secret value to stdout
get_secret() {
  [[ -n "${1:-}" ]] || { _secrets_err "usage: get_secret <name> [version]"; return 2; }
  _secrets_init || return 1
  _sr_gcloud secrets versions access "${2:-latest}" \
      --secret="$1" --project="$SCHEDRUNNER_GCP_PROJECT" --quiet 2>/dev/null \
    || { _secrets_err "could not read secret '$1' (missing, or no access)"; return 1; }
}

# secret_to_var <name> <varname> -> export <varname> with the secret value
secret_to_var() {
  [[ -n "${1:-}" && -n "${2:-}" ]] || { _secrets_err "usage: secret_to_var <name> <varname>"; return 2; }
  local _value
  _value="$(get_secret "$1")" || return 1
  printf -v "$2" '%s' "$_value"
  export "${2?}"
}

# secrets_check -> validate config + auth without printing any secret
secrets_check() {
  _secrets_init || return 1
  echo "secrets.sh: ready (project=$SCHEDRUNNER_GCP_PROJECT, key=$SCHEDRUNNER_GCP_SA_KEY)"
}

# CLI dispatch only when executed directly (not when sourced).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    get)   shift; get_secret "$@" ;;
    check) secrets_check ;;
    *) echo "usage: ${0##*/} {get <name> [version] | check}" >&2; exit 2 ;;
  esac
fi
