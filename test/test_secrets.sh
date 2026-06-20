#!/bin/bash
# test_secrets.sh — secrets.sh (Google Cloud Secret Manager accessor). The only
# external service, the `gcloud` CLI, is stubbed as an exported function; the
# helper's own logic (config validation, auth activation, fetch, var export) is
# exercised for real.
source "$(dirname "$0")/lib.sh"

# --- gcloud stub ------------------------------------------------------------
# Reads/serves a fake secret store from $GCLOUD_STUB_DIR/<name>. Logs calls to
# $GCLOUD_STUB_LOG. `auth list` returns $GCLOUD_STUB_ACTIVE.
gcloud() {
  [[ -n "${GCLOUD_STUB_LOG:-}" ]] && printf '%s\n' "$*" >> "$GCLOUD_STUB_LOG"
  case "$1 ${2:-}" in
    "auth list") printf '%s\n' "${GCLOUD_STUB_ACTIVE:-}" ;;
    "auth activate-service-account") return 0 ;;
    "secrets versions")
      local a name=""
      for a in "$@"; do [[ "$a" == --secret=* ]] && name="${a#--secret=}"; done
      if [[ -n "$name" && -f "$GCLOUD_STUB_DIR/$name" ]]; then
        cat "$GCLOUD_STUB_DIR/$name"; return 0
      fi
      echo "ERROR: NOT_FOUND: Secret [$name] not found." >&2; return 1 ;;
    *) return 0 ;;
  esac
}
export -f gcloud

SA_EMAIL="sa@test.iam.gserviceaccount.com"

# fresh per-case environment: a fake SA key, a secret store, isolated config
new_env() {
  local d; d="$(make_tmpdir)"
  printf '{"client_email":"%s"}\n' "$SA_EMAIL" > "$d/key.json"
  mkdir -p "$d/store" "$d/gcloud"
  printf '%s' "$d"
}
# run secrets.sh CLI with a complete, valid config pointed at sandbox <dir>
run_get() {  # <dir> <args...>
  local d="$1"; shift
  SCHEDRUNNER_SECRETS_CONF=/nonexistent \
  SCHEDRUNNER_GCP_PROJECT=test-project \
  SCHEDRUNNER_GCP_SA_KEY="$d/key.json" \
  SCHEDRUNNER_GCLOUD_CONFIG="$d/gcloud" \
  GCLOUD_STUB_DIR="$d/store" \
  GCLOUD_STUB_ACTIVE="$SA_EMAIL" \
  GCLOUD_STUB_LOG="$d/gcloud.log" \
    bash "$REPO_ROOT/secrets.sh" "$@"
}

# --- happy path: get a secret -----------------------------------------------
d="$(new_env)"; printf 'sk-test-123' > "$d/store/openai-api-key"
out="$(run_get "$d" get openai-api-key)"; rc=$?
assert_status 0 "$rc" "get existing secret: exit 0"
assert_eq "sk-test-123" "$out" "get existing secret: prints the value"

# --- a specific version is passed through to gcloud -------------------------
d="$(new_env)"; printf 'v5-value' > "$d/store/rotating"
out="$(run_get "$d" get rotating 5)"
assert_eq "v5-value" "$out" "get with version: prints the value"
assert_contains "$(cat "$d/gcloud.log")" "secrets versions access 5 --secret=rotating" "version is forwarded to gcloud"

# --- error: missing project config ------------------------------------------
d="$(new_env)"
out="$(SCHEDRUNNER_SECRETS_CONF=/nonexistent SCHEDRUNNER_GCP_SA_KEY="$d/key.json" \
       SCHEDRUNNER_GCLOUD_CONFIG="$d/gcloud" GCLOUD_STUB_DIR="$d/store" \
       bash "$REPO_ROOT/secrets.sh" get whatever 2>&1)"; rc=$?
assert_status 1 "$rc" "missing project: exit 1"
assert_contains "$out" "SCHEDRUNNER_GCP_PROJECT is not set" "missing project: explains why"

# --- error: missing service-account key -------------------------------------
d="$(new_env)"
out="$(SCHEDRUNNER_SECRETS_CONF=/nonexistent SCHEDRUNNER_GCP_PROJECT=test-project \
       SCHEDRUNNER_GCP_SA_KEY="$d/does-not-exist.json" SCHEDRUNNER_GCLOUD_CONFIG="$d/gcloud" \
       bash "$REPO_ROOT/secrets.sh" get whatever 2>&1)"; rc=$?
assert_status 1 "$rc" "missing key file: exit 1"
assert_contains "$out" "service-account key not found" "missing key file: explains why"

# --- error: secret does not exist -------------------------------------------
d="$(new_env)"
out="$(run_get "$d" get nope 2>&1)"; rc=$?
assert_status 1 "$rc" "missing secret: exit 1"
assert_contains "$out" "could not read secret 'nope'" "missing secret: explains why"

# --- behavior: SA is activated when it isn't the active account -------------
d="$(new_env)"; printf 'x' > "$d/store/s"
SCHEDRUNNER_SECRETS_CONF=/nonexistent SCHEDRUNNER_GCP_PROJECT=test-project \
SCHEDRUNNER_GCP_SA_KEY="$d/key.json" SCHEDRUNNER_GCLOUD_CONFIG="$d/gcloud" \
GCLOUD_STUB_DIR="$d/store" GCLOUD_STUB_ACTIVE="" GCLOUD_STUB_LOG="$d/g.log" \
  bash "$REPO_ROOT/secrets.sh" get s >/dev/null 2>&1
assert_contains "$(cat "$d/g.log")" "auth activate-service-account" "inactive SA: activated before access"

# --- behavior: SA already active -> no re-activation ------------------------
d="$(new_env)"; printf 'x' > "$d/store/s"
run_get "$d" get s >/dev/null 2>&1
assert_not_contains "$(cat "$d/gcloud.log")" "activate-service-account" "active SA: not re-activated"

# --- sourcing is side-effect free: no option/CLOUDSDK_CONFIG leak -----------
d="$(new_env)"; printf 'pw' > "$d/store/db-password"
leak="$(
  set +eu
  unset CLOUDSDK_CONFIG
  export SCHEDRUNNER_SECRETS_CONF=/nonexistent SCHEDRUNNER_GCP_PROJECT=test-project
  export SCHEDRUNNER_GCP_SA_KEY="$d/key.json" SCHEDRUNNER_GCLOUD_CONFIG="$d/gcloud"
  export GCLOUD_STUB_DIR="$d/store" GCLOUD_STUB_ACTIVE="$SA_EMAIL"
  source "$REPO_ROOT/secrets.sh"
  secret_to_var db-password DB_PASSWORD
  eflag=no; [[ $- == *e* ]] && eflag=yes
  uflag=no; [[ $- == *u* ]] && uflag=yes
  printf 'var=%s cloudsdk=%s eflag=%s uflag=%s' \
    "$DB_PASSWORD" "${CLOUDSDK_CONFIG:-unset}" "$eflag" "$uflag"
)"
assert_contains "$leak" "var=pw" "secret_to_var: exported the secret into a variable"
assert_contains "$leak" "cloudsdk=unset" "sourcing: does not leak CLOUDSDK_CONFIG into caller"
assert_contains "$leak" "eflag=no" "sourcing: does not enable 'set -e' in caller"
assert_contains "$leak" "uflag=no" "sourcing: does not enable 'set -u' in caller"

finish
