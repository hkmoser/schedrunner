#!/usr/bin/env bash
# Put the server behind Tailscale HTTPS on https://$TS_HOST.
# `tailscale serve` terminates TLS and proxies to the local Vapor port.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

c_bold "== TLS / tailscale serve =="
require_env TS_HOST PORT || fail "Set TS_HOST and PORT in Deploy/.env"
resolve_tailscale || fail "tailscale CLI not found (install the app, see 'make doctor')"

# Verify a cert can be issued (idempotent). Probe in a temp dir since `tailscale
# cert` writes <host>.crt/.key to the CWD; `tailscale serve` fetches its own cert.
if ! ( cd "$(mktemp -d)" && ts cert "$TS_HOST" ) >/dev/null 2>&1; then
  fail "No HTTPS cert for $TS_HOST. Run 'make doctor' for the one-time admin-console steps."
fi
ok "HTTPS cert available for $TS_HOST"

# Proxy https://$TS_HOST:$CH_TLS_PORT/ -> http://127.0.0.1:$PORT (background, persists across reboots).
# stable terminates on :443 (https://$TS_HOST/); next on :8443 (https://$TS_HOST:8443/) — a distinct
# origin, so iOS installs it as a separate PWA with its own cache. Both can serve at once.
ts serve --bg --https="${CH_TLS_PORT}" "http://127.0.0.1:${PORT}" \
  || ts serve --https="${CH_TLS_PORT}" "http://127.0.0.1:${PORT}" --bg \
  || fail "tailscale serve failed; check 'tailscale serve status'"

_url_port_sfx=""; [[ "$CH_TLS_PORT" != "443" ]] && _url_port_sfx=":$CH_TLS_PORT"
ok "serving https://$TS_HOST$_url_port_sfx/ -> 127.0.0.1:$PORT ($DASHBOARD_CHANNEL)"
ts serve status || true
