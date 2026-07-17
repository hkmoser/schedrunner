#!/usr/bin/env bash
# Dump the dashboard's data schemas (every BigQuery table the app reads + the field shape
# of the Drive-synced Messages/Smart-Home exports) into docs/SCHEMAS.md so they're visible
# in the repo. Field names/types only by default; SCHEMAS_SAMPLES=1 also includes a few
# sample rows (private repos only). Uses the machine's gcloud ADC — no keys, no sudo.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

SIDE="$REPO_ROOT/bq_sidecar"
PY="$SIDE/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"  # fall back if the sidecar venv isn't built (run 'make bq')

c_bold "== dump data schemas =="
if ( cd "$SIDE" && "$PY" dump_schemas.py "$REPO_ROOT/docs/SCHEMAS.md" ); then
  ok "wrote docs/SCHEMAS.md — review it, then commit so Claude can read the real shapes"
  info "Tip: SCHEMAS_SAMPLES=1 make schemas  also dumps a few rows (private repo only)"
else
  fail "schema dump failed — ensure google-cloud-bigquery is installed ('make bq' builds the venv)"
fi
