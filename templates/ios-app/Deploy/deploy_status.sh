#!/usr/bin/env bash
# Answers "is the server the phone talks to actually running my latest code?" — the recurring
# "I don't see my changes" question. Server-side changes (templates, nav, new pages, sidecar)
# reach the app WITHOUT an app rebuild, but only once the SERVER is redeployed. This compares
# the deployed build (from /healthz, stamped with the git sha at deploy time) to your local
# HEAD, checks the sidecar, and flags any provider errors.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

PORT="${PORT:-$CH_SERVER_PORT}"
HEAD_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

c_bold "== deploy status =="
info "local HEAD:      $HEAD_SHA  ($(git -C "$REPO_ROOT" log -1 --format='%s' 2>/dev/null | cut -c1-60))"

# Prefer the local server (always reachable on the Mac); note the Tailscale URL for the phone.
health="$(curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null || true)"
[[ -z "$health" && -n "${TS_HOST:-}" ]] && health="$(curl -fsS --max-time 6 "https://${TS_HOST}/healthz" 2>/dev/null || true)"

if [[ -z "$health" ]]; then
  warn "server not answering on :${PORT} (or https://${TS_HOST:-<TS_HOST>}) — run: make update"
  exit 1
fi

# Pull fields out of the /healthz JSON without assuming jq is installed.
field() { printf '%s' "$health" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))" 2>/dev/null; }
deployed="$(field build)"
schema="$(field schemaVersion)"
info "deployed build:  ${deployed:-unknown}  (schema v${schema:-?})"

if [[ "$deployed" == "$HEAD_SHA" ]]; then
  ok "server is CURRENT — running your latest commit. Server-side changes are live; refresh the app."
else
  warn "server is STALE — running $deployed but HEAD is $HEAD_SHA."
  info "  → deploy your changes:  git pull origin main && make update"
  info "     (nav/template/new-page changes need the server rebuilt + restarted to load them.)"
fi

# Any providers currently failing? (e.g. a down sidecar surfaces here as errors.)
errs="$(printf '%s' "$health" | python3 -c "import sys,json;e=json.load(sys.stdin).get('errors',{});print('\n'.join(f'  {k}: {v}' for k,v in e.items()))" 2>/dev/null || true)"
if [[ -n "$errs" ]]; then
  warn "provider errors (these pages show stub data until fixed):"
  printf '%s\n' "$errs"
  info "A sidecar-backed error usually means the sidecar is down — check: make bq"
else
  ok "no provider errors reported"
fi

# Direct sidecar reachability (the new log pages are sidecar-backed).
bq_port="${BQ_SIDECAR_PORT:-$CH_SIDECAR_PORT}"
if curl -fsS --max-time 4 "http://127.0.0.1:${bq_port}/healthz" >/dev/null 2>&1; then
  ok "sidecar healthy on :${bq_port}"
else
  warn "sidecar NOT reachable on :${bq_port} — sidecar-backed pages (Activity, Smart Home, the new logs) will show stubs. Fix: make bq"
fi
