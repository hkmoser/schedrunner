# CLAUDE.md — project guide for __APP_NAME__

Read this first. This repo is a **server-driven __APP_NAME_LOWER__**: a Vapor server composes a JSON
**manifest** (theme + screen tree + live data); thin clients render it. Two clients share one
contract. **The web PWA is the live, deploy-now product**; the native iOS app is built in parallel.

## Golden rule
Most changes should be **server-side only, no client redeploy**. To change layout/theme/content,
edit `Server/Sources/App/Templates/{screen,theme}.json` and run `make update`. The installed PWA
and native app pick it up on next refresh. Only touch client code to add a *new component type
or capability*. See **DEVELOPMENT.md** for the full extension guide.

## Deploy (turn-key)
```bash
cp Deploy/.env.example Deploy/.env   # fill TS_HOST (required); rest is optional
make doctor                          # preflight — prints what's missing
make deploy                          # idempotent bring-up + healthcheck
```
iPhone (Tailscale ON): open `https://<TS_HOST>/` in Safari → Share → Add to Home Screen.
Full details: `Deploy/RUNBOOK.md`.

## Architecture
- `Server/` (Vapor): `Composer` assembles theme + providers → manifest JSON.
  `Providers/WelcomeProvider.swift` is the placeholder home-screen provider — replace it with
  your own data source. `Providers/SystemProviders.swift` has `ConfigProvider` and
  `SettingsProvider` (backed by the optional BigQuery sidecar).
  `configure.swift` starts the APNs push client and cache warmer.
- `bq_sidecar/` (Python): optional BigQuery + Secret Manager service — **disabled by default**.
  Enable by setting `BQ_DATASET` and running `make bq`. See DEVELOPMENT.md.
- `Web/` (TypeScript PWA): `src/sdui/*` renders the manifest; Workbox SW (offline-first).
- `App/` (SwiftUI): mirrors the web SDUI; adds BGTask refresh, Face ID, APNs push, Live Activity.
- `Shared/schema/manifest.schema.json` — the ONE contract. Keep server templates and both
  renderers in sync with this.
- `Widget/` (WidgetKit): home-screen widget extension.

## Conventions
- Secrets: env only, via `Deploy/.env` (git-ignored). Never commit keys.
- Native identifiers live in `Config.xcconfig` (one place).
- Provider formatting (%, dates, $) is done **server-side**; clients stay dumb.
- Unknown component types / unresolved bindings = graceful degradation, never a crash.

## Branch policy
- **`next`** for new features and experiments (PR your branch into `next`; test in the Next app).
- **`main`** for production fixes or when the owner asks to ship.
- Promotion: `next → main` merge once proven. See DEVELOPMENT.md § "next channel".

## Verify
- Web: `cd Web && npm install && npm test`
- Server: `cd Server && swift test`
- After deploy: `curl https://<TS_HOST>/healthz` or `make doctor`
