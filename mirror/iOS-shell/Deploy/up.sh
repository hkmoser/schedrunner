#!/usr/bin/env bash
# Full, idempotent bring-up. Safe to re-run. This is what `make deploy` calls.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

c_bold "===== iOS-Shell deploy ====="

bash "$REPO_ROOT/Deploy/bootstrap.sh"
bash "$REPO_ROOT/Deploy/doctor.sh"        # preflight; stops here with guidance if not ready
bash "$REPO_ROOT/Deploy/setup_power.sh"   # one-time anti-sleep (non-interactive)
bash "$REPO_ROOT/Deploy/setup_tls.sh"
bash "$REPO_ROOT/Deploy/setup_bq.sh"
bash "$REPO_ROOT/Deploy/setup_server.sh"

load_env
PORT="${PORT:-$CH_SERVER_PORT}"

c_bold "== healthcheck =="
# Give the freshly (re)loaded service a moment to bind. The healthcheck and every printed URL
# must target THIS channel's origin — stable on :443, next on :8443 — otherwise deploying next
# would "verify" against the (healthy) stable stack and print the wrong install URL.
url_sfx=""; [[ "$CH_TLS_PORT" != "443" ]] && url_sfx=":$CH_TLS_PORT"
url_base="https://${TS_HOST}${url_sfx}"
ok_health=0
for attempt in 1 2 3 4 5 6 7 8; do
  if curl -fsS --max-time 5 "$url_base/healthz" >/dev/null 2>&1; then ok_health=1; break; fi
  # Fall back to the local port while TLS warms up.
  curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && { url_base="http://127.0.0.1:${PORT}"; ok_health=1; break; }
  sleep 2
done
[[ $ok_health -eq 1 ]] || fail "server did not become healthy; check: tail -f \"$HOME/Library/Logs/dashboard/server$CHANNEL_SUFFIX.err.log\""
ok "health endpoint responding ($url_base/healthz)"

# Validate the manifest is well-formed and complete (schema v1 + all cards).
manifest_json="$(curl -fsS --max-time 10 "$url_base/dashboard")" || fail "/dashboard did not respond"
echo "$manifest_json" | node -e '
  let s=""; process.stdin.on("data",d=>s+=d).on("end",()=>{
    const m=JSON.parse(s);
    const errs=[];
    if(m.schemaVersion!==1) errs.push("schemaVersion!=1");
    if(!m.screen) errs.push("missing screen");
    for(const k of ["weather","stocks","mortgage","meta"]) if(!m.data||!m.data[k]) errs.push("missing data."+k);
    if(errs.length){console.error("manifest invalid: "+errs.join(", "));process.exit(1);}
    console.log("  manifest OK (schema v"+m.schemaVersion+", cards: weather/stocks/mortgage)"+(m.data.meta.stale?" [some cards stale]":""));
  });
' || fail "manifest validation failed"
ok "manifest valid"

# Confirm the web app is being served.
if curl -fsS --max-time 5 "$url_base/" | grep -qi "<div id=\"app\""; then
  ok "web app served at $url_base/"
else
  warn "web app root did not return expected HTML (check WEB_DIST / build)"
fi

# Native iOS rebuild — opt-in (DEPLOY_IOS=1), change-detected, and BACKGROUNDED so it never
# delays the web build above. No-op when no native source changed. Never fails the deploy.
bash "$REPO_ROOT/Deploy/ios_autodeploy.sh" || true

echo
c_green "===== deploy complete ($DASHBOARD_CHANNEL) ====="
app_name="Dashboard"; [[ "$DASHBOARD_CHANNEL" == "next" ]] && app_name="Dashboard Next"
c_bold "On your iPhone (Tailscale ON):"
info "1) Open  https://${TS_HOST}${url_sfx}/  in Safari"
info "2) Share → Add to Home Screen"
info "3) Launch '$app_name' from the home screen"
