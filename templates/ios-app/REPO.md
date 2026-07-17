# __APP_NAME__

**Purpose:** <one line>
**Type:** app (iOS + Vapor server + PWA)
**Role:** <who uses this and what it shows>

## Runtime & deploy
- Stack: Swift/Vapor server + TypeScript PWA + SwiftUI native app
- Deploys via: schedrunner auto-deploy (`Deploy/auto_deploy.sh`)
- Runs on: Mac mini (Tailscale-reachable at `DASHBOARD_HOST`)

## Identifiers
- Bundle ID: `__BUNDLE_ID__`
- BGTask: `__BUNDLE_ID__.refresh`
- LaunchAgent: `__BUNDLE_ID__.server`
- Team ID: `__TEAM_ID__`

## Key files
- `Config.xcconfig` — single source of truth for all iOS build identifiers
- `project.yml` — XcodeGen project definition (generates `__APP_NAME__.xcodeproj`)
- `Deploy/.env` — runtime secrets (git-ignored; copy from `.env.example`)
- `Deploy/auto_deploy.sh` — post-pull hook (rebuild + restart server)
- `Server/Sources/App/` — Vapor routes + providers + templates
- `Web/src/` — TypeScript PWA (server-driven UI)
- `App/Sources/` — SwiftUI native app

## Setup (first time)
```bash
bash setup.sh __APP_NAME__ __BUNDLE_ID_PREFIX__ __TEAM_ID__
cp Deploy/.env.example Deploy/.env   # fill in TS_HOST and keys
make doctor
make deploy
```

## Invariants / gotchas
- `BackgroundRefresh.swift` taskID and `project.yml` BGTaskSchedulerPermittedIdentifiers must stay in sync
- All iOS identifiers flow from `Config.xcconfig`; never hardcode bundle IDs elsewhere
- `Deploy/gen_ios_config.sh` writes `Config.local.xcconfig` at build time (git-ignored)
- The `next` channel gets bundle ID `__BUNDLE_ID__.next` and runs on a separate port
