# iOS-Shell — Personal Server-Driven Dashboard

A personal dashboard built as a **server-driven UI (SDUI)** shell. A small Swift/Vapor service on
a Mac mini composes a JSON **manifest** (component tree + data + theme); thin clients just *render*
it. Two clients share one manifest:

- **Web PWA** (`Web/`, TypeScript) — the deploy-first client. Installable to the iPhone home screen,
  offline-capable, no Apple Developer account required.
- **Native iOS app** (`App/`, SwiftUI) — built in parallel; device install/OTA deferred until paid
  Apple Developer enrollment.

Because both clients render the same manifest, anything you change server-side (layout, theme,
ordering, content) appears in both **without redeploying the client**.

## Cards
Weather · SPY/NVDA/VEEV relative trend · 30-yr mortgage rate · Calendar (native only; on web it
shows an "available in the iOS app" placeholder).

## Quick start (Mac mini)
```
cp Deploy/.env.example Deploy/.env   # fill in TWELVEDATA_KEY, FRED_KEY, SHARED_SECRET, LAT/LON/TZ
make doctor                          # preflight: tells you exactly what's missing
make deploy                          # idempotent bring-up + healthcheck, prints the install URL
```
Then on the iPhone (Tailscale on): open `https://<TS_HOST>/` in Safari → **Share → Add to Home Screen**.

See `Deploy/RUNBOOK.md` for details and the native (post-enrollment) path.

## Local development (no Mac mini / no Tailscale)
```
cd Server && swift run            # serves http://localhost:8080  (stubbed data if no API keys)
cd Web && npm install && npm run dev   # Vite dev server, proxies /dashboard to :8080
```

## Layout
- `Server/` — Vapor: data providers, manifest composer, serves the web bundle + `/dashboard`.
- `Web/` — TypeScript PWA renderer (Vite).
- `App/` — SwiftUI native shell (mirrors the web renderer behind the same schema).
- `Shared/schema/manifest.schema.json` — the one contract both renderers validate against.
- `Deploy/` — turn-key scripts (`up.sh`, `doctor.sh`) + `Makefile` targets.
