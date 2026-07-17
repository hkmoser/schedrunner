# CLAUDE.md — project guide for the Mac mini agent

Read this first. This repo is a **server-driven dashboard**: a Vapor server composes a JSON
**manifest** (theme + screen tree + live data); thin clients render it. Two clients share one
contract. **The web PWA is the live, deploy-now product**; the native iOS app is built in parallel
but its device install is deferred until paid Apple Developer enrollment.

## Golden rule
Most changes should be **server-side, no client redeploy**. To change layout/theme/content, edit
`Server/Sources/App/Templates/{screen,theme}.json` and run `make update`. The installed PWA / app
picks it up on next refresh. Only touch client code to add a *new component type or capability*.

## Deploy (turn-key)
```
cp Deploy/.env.example Deploy/.env   # fill TS_HOST, TWELVEDATA_KEY, FRED_KEY, DASHBOARD_*
make doctor                          # preflight — fixes are printed explicitly
make deploy                          # idempotent bring-up + healthcheck, prints the iPhone install URL
```
iPhone (Tailscale ON): open `https://<TS_HOST>/` in Safari → Share → Add to Home Screen.
Full details + troubleshooting: `Deploy/RUNBOOK.md`. The one manual prerequisite is enabling
Tailscale HTTPS in the admin console (doctor explains it).

### How this repo actually gets deployed (important)
An **external service** (outside this repo) owns the deploy schedule on the Mac mini. Its contract is:
**(1) `git pull`** the latest, then **(2) run the repo-root `./.auto-deploy` script.** That script is
the hook the service points at; it just `cd`s to the repo and `exec`s `Deploy/auto_deploy.sh` (so edit
the hook's *behavior* in `Deploy/auto_deploy.sh`, not in `.auto-deploy`).
`Deploy/auto_deploy.sh` is THE post-pull hook: it makes the **running** server match the just-pulled
code — because a `git pull` alone changes nothing (templates/routes/providers are compiled into the
Vapor binary and loaded at process start, so the server serves stale content until it's rebuilt +
restarted). The hook is idempotent (no-op when `/healthz` already reports the checked-out HEAD),
rebuilds the server only if its sources changed, reloads the service, redeploys the sidecar, mirrors
docs, verifies `/healthz`, and triggers the opt-in native iOS rebuild. It does **not** pull (the
external service does) and is **not** gated behind a toggle (being run is the trigger).
**So: server-side changes reach the app automatically once merged — via that external pull + hook.**
Verify any time with **`make deploy-status`** (compares the deployed `/healthz` build to local HEAD +
checks the sidecar); inspect the last hook run with **`make auto-deploy-status`**. If the server is
stale, the hook (or `git pull && make update-server`) didn't run/succeed — it is never a code issue
that server-side changes "don't show", it's that the running binary wasn't rebuilt from the new tree.

**`make update` only recompiles the Vapor server when it changed.** The deploy compiles just
*one* Swift target — the Vapor **server** (the native iOS `App/` is never built here). Because a
`-c release` build is slow, `make update` rebuilds it **only when a `Server/` source or a bundled
`Templates/*.json` changed since the last build** (tracked by a `.build/release/.deploy-build-stamp`);
otherwise it reuses the existing binary and just rebuilds the PWA. `make update-web` forces a
web-only update (never touches Swift); `make update-server` (or `FORCE_SERVER_BUILD=1`) forces a
full server rebuild.

### Release channels: stable + next (which branch to target)
Two full stacks run side by side on the Mac, each its own installed PWA:
- **stable** = branch **`main`**, this checkout, server `:8080` / sidecar `:8099` / `https://<TS_HOST>/`,
  app "Dashboard". Deployed automatically by the external pull + `.auto-deploy` hook. This is the
  daily-driver production app.
- **next** = branch **`next`**, a git **worktree at `../iOS-Shell-next`**, server `:8081` / sidecar
  `:8100` / `https://<TS_HOST>:8443/`, app "Next" (purple, `NEXT` footer marker). Updated manually:
  `cd ../iOS-Shell-next && git pull && make deploy` (or its own `make auto-deploy-timer`).

Mechanics: everything derives from `DASHBOARD_CHANNEL`, which is **pinned to the checkout** by
`Deploy/.channel` (written by `make next-setup`; absent = stable). The pin means any command run
inside a worktree is channel-correct without flags — nothing run from the next tree can touch
stable's launchd labels, ports, or logs (each channel has its own, e.g. `server-next.err.log`).
Shared on purpose: `Deploy/.env`, BigQuery data, Drive mirrors, and the Settings store (Settings
edits are machine-global). Native iOS OTA is **channel-aware**: a next-channel build gets bundle
id `<id>.next` (installs ALONGSIDE stable), name "Dashboard Next", talks to `:8443`
(`DASHBOARD_PORT` via `gen_ios_config.sh` → Info.plist `DashboardPort`), and publishes its OTA
payload at `https://<TS_HOST>:8443/app/`.
First-time bring-up: `bash Deploy/first_next_deploy.sh` (from the stable checkout). Ops detail:
`Deploy/RUNBOOK.md` § "Stable + Next channels". `make channel-status` shows both stacks.

**Branch policy for agents:** target **`next`** for new features, experiments, and anything that
changes behavior/architecture (PR your working branch into `next`; the owner tests it in the Next
app). Target **`main`** only for fixes to current production behavior (bugfixes, deploy/ops
repairs, docs) or when the owner explicitly asks to ship to stable. **Promotion is a `next → main`
merge** once a feature is proven — never cherry-pick around it. When in doubt, propose `next`.

## Architecture
- `Shared/schema/manifest.schema.json` — the ONE contract. `golden-manifest.json` is the fixture
  both renderers test against. **Keep the server templates and both renderers in sync with this.**
- `Server/` (Vapor): `Composer` = templates + provider data; `Providers/` (Open-Meteo, Twelve Data,
  FRED, Zillow, BigQuery); `ProviderCache` = per-card last-good + stale flag (one failure never blanks
  the board); serves `/dashboard`, the navigable `/screen/bigquery` page, `/healthz`, and the web bundle.
  A **background cache warmer** (`Warmer.swift`, started from `configure.swift`) keeps `ProviderCache`
  fresh on a timer so opening the app — even briefly before going offline — pulls the absolute latest
  rather than triggering a lazy first-request fetch. Cheap pages warm every `DASHBOARD_WARM_INTERVAL`s
  (default 60); BigQuery-backed pages (Activity/Last-48) are **not** warmed unless
  `DASHBOARD_WARM_BQ_INTERVAL>0` (so a timer never re-bills BigQuery); `DASHBOARD_WARM=0` disables it.
  Skipped under tests.
- `bq_sidecar/` (Python): tiny localhost service over BigQuery via the machine's Application Default
  Credentials (NO keys in the repo). Endpoints: `/query` (generic), `/afm?range=` (Find My "Activity":
  groups a device's fixes into stay/move **segments** using BigQuery GIS + reverse-geocoded
  place names, plus a recent path for the map and a **current-state block** (`currentState`: where the
  device is now + battery + last-seen); the window is a **Today (past 24h, default) /
  Yesterday / This-week** filter via `?range=`, and stop **map dots are sized by dwell time**. A
  stop can be **labeled as a known location** via its tag button → the `/screen/afm_label` form
  (`KnownLocProvider` GET `/known_locs?lat=&lon=&place=`) → `POST /known_locs` upserts a new
  `known_locs` BigQuery table [name/lat/lon/radius_m; created on first use unless
  `BQ_KNOWN_LOCS_CREATE=0`; `BQ_KNOWN_LOCS_TABLE` overrides]; a stop within a saved radius then
  shows that **name + a star** instead of a geocoded city),
  `/config` GET/POST (key/value `dashboard_config`
  table with a `category` column, created/migrated if missing; POST upserts via parameterized `MERGE`,
  grouped into sections by `category` or a `group.key` naming convention), `/balances` (real account
  balances — sourced from the YNAB-synced `home_ynab.ynab_balances` table first [its own dataset, NOT
  `BQ_DATASET`/AFM; `BQ_YNAB_TABLE` overrides], falling back to the budget-sheet `dashboard_balances`
  snapshot then any `*balance*` table; totals it and **groups accounts (Operational / Shared / Lifestyle /
  Real Estate / Other) with a per-group subtotal** via name-keyword rules in `_account_group`. The old
  standalone `/ynab` page was folded into this. The page also carries a **net-worth-over-time chart**
  (`_networth_cards` → a 0-or-1-element `netWorthCards` list the template `repeat`s over, so it vanishes
  cleanly when absent): net worth = `SUM(balance)` per `snapshot_date` from `home_ynab.ynab_balances_history`
  [`BQ_YNAB_HISTORY_TABLE` overrides], with the **backfilled/estimated leading days drawn as a dashed
  segment** joining the solid actual line), `/budget` (the **Budget** page: **average monthly spending per
  category over the last 12 full calendar months** — net outflow from `home_ynab.ynab_transactions`
  [milliunits→$, transfers/uncategorized excluded; `BQ_YNAB_TX_TABLE` overrides], **grouped into buckets
  Essential / Shared / Goals / Lifestyle / Real Estate / Other with per-bucket subtotals** via
  `_budget_bucket` [hybrid: a YNAB category-GROUP name that lines up maps straight through, else
  keyword-match group then category; groups read from `home_ynab.ynab_categories`], each category showing
  its **avg/mo + a min–max range**; **lumpy lines** (range ≥ the monthly average — e.g. Lifestyle or
  categories with $0/very-low months) also express a **projected annual budget** (run-rate `avg × 12`)
  to plan against; degrades to a named empty state if the
  transactions table is absent), `/bqtables?dataset=&table=&view=` (browses the whole project:
  datasets → a dataset's tables → a table viewed as either its **field structure** (`view=columns` —
  name/type/mode/description, nested RECORD fields flattened) or a **records preview**
  (`view=preview`, newest-first: `ORDER BY` the table's first DATE/TIME column when it has one, else the
  natural order reversed; `LIMIT` defaults to 1000 and is overridable via the `bq_preview_limit` config
  key on the Config page, capped at 5000); the two are **Columns | Preview tabs** that `navigate` to
  `?view=`),
  `/smarthome?sources=&types=` (summarizes the smart-home event log the Mac mini's `ha-events` service
  syncs to Google Drive: a status/last-update header from `summary_latest.json`, then a **TWO-TIER 24h
  filter** — tier 1 = **source** (Home Assistant / Hue / Eero / …, via `?sources=`, default = all
  present); tier 2 = **type** (entity domain like light/lock/binary_sensor, else event_type like
  device_update, else source; via `?types=`, scoped to the selected sources, defaulting to "the most
  useful types" = everything present that isn't noisy `sensor`/`sun`/`update`/…). A chip toggle pins its
  own dimension and preserves the other verbatim; `?types=`/`?sources=` empty = none, absent = its
  default. Recent events are **filtered to (selected sources ∩ selected types)**, each with a
  **tap-to-expand Details** disclosure listing its full raw key/values; scanned from the
  newest `events_*.jsonl` under the Drive-synced `Private/Home/Smart Home Events` folder —
  `SMARTHOME_DIR` overrides; no BigQuery), `/logs?status=` (tails the Mac's `~/log` dir — newest files +
  last lines of each, each **flagged Running / Failed / OK / idle** from its tail and the list **sorted by
  that status** (running, failed, ok, idle) then most-recently-updated, with a **multi-select status
  filter** via `?status=`; each card's **24h button** opens `/screen/logfile?file=` → `/logfile`, a
  best-effort **last-24h view with anomaly lines flagged** (failures + warnings); `LOG_DIR`,
  `LOG_DETAIL_BYTES`/`LOG_DETAIL_LINES`), `/repos` (git + deploy
  status of every repo found under `~/Dropbox/Source`, **newest-commit-first**, each with its **last 5
  commits** (tap to expand) and an **opt-in latest-CI badge** — recursive depth-bounded scan, skips
  `node_modules`/build dirs and won't descend into a repo; an explicit `repos.json`/`repos.txt`
  overrides; deploy status from a per-repo marker else derived from git; CI off unless `REPOS_GH_TOKEN`
  is set (GitHub Actions, best-effort, bounded by `REPOS_CI_BUDGET`s); `REPOS_DIR`), `/schedlogs`
  (per-script run summaries, **sorted by status** running → failed → ok → idle then most-recent),
  `/schedrunner` (scheduled-job status:
  reads schedrunner's status artifact — `status.json`/state dir, flexible per-job keys — else tails its
  latest log; `SCHEDRUNNER_STATUS`), `/docs?path=` (Markdown browser over
  Google Drive `/Private` — lists subfolders + `.md` files, or returns a file's raw markdown; path is
  confined to the root. Reads a **local mirror** (`DOCS_MIRROR`, populated by `make docs` /
  `Deploy/sync_docs.sh`) in preference to the live mount, so Docs works even when Drive files are
  online-only / the Drive app is closed / you're offline; `DOCS_DIR` overrides the live source),
  `/messages` (the **Messages** page: recent iMessage/SMS parsed from a **Drive-synced** export folder
  [`MESSAGES_DIR`; newest `*.jsonl`/`*.json` first, tolerant of field names] + the latest INBOX headers
  from each Gmail account via IMAP app-passwords [`GMAIL_<n>_USER`/`GMAIL_<n>_PASS`]; each source
  degrades independently; no BigQuery). The `/logs`, `/repos`, `/docs`, `/messages` endpoints read the
  local filesystem / Drive mirror only (no BigQuery). The billing project is derived from `BQ_DATASET`. Deploy
  with `make bq`; the one required setting is `BQ_DATASET`. **`make schemas`** (`Deploy/dump_schemas.sh` →
  `bq_sidecar/dump_schemas.py`) dumps every BQ table's + Drive export's real field shape to
  **`docs/SCHEMAS.md`** (committed) — read it before changing a provider/template, so bindings match the
  real columns/fields rather than guesses (`SCHEMAS_SAMPLES=1` adds sample rows; private repo only).
  **`make schemas-refresh`** (`Deploy/refresh_schemas.sh`) is the unattended variant for a daily
  schedule: it regenerates the dump in an isolated git worktree (never disturbing the live deploy
  checkout) and pushes it to the `schemas/auto` branch — a true no-op when only the timestamp changed.
- Pages/navigation: every manifest carries `nav`, a **two-level tree** rendered as a **slide-out
  hamburger menu** (top-level items are either direct links with a `path`, or section headers with
  `children`; web `sdui/drawer.ts` with inline-SVG icons from `sdui/navicons.ts`, native `NavMenu.swift`
  with SF Symbols). Destinations = Home `/dashboard`; **Location** › Activity `/screen/afm` + Last 48
  `/screen/afm48` (**two tabs** via `?view=`: **Now** [default] = the `afm_now` materialized view via
  `/afm_now`, and **48h history** = the raw fix-history dump via `/afm_raw?hours=48` — both columns/rows);
  Balances `/screen/balances`; Budget `/screen/budget`; Smart Home `/screen/smarthome`; Messages `/screen/messages`; **System** › Logs `/screen/logs` + Repos
  `/screen/repos` + Schedrunner `/screen/schedrunner` (scheduled-job status summary) + BQ Tables
  `/screen/bqtables` (a BigQuery browser: project → datasets → a dataset's tables → a table viewed via
  **Columns | Preview tabs**, `?dataset=`/`?table=`/`?view=`); Docs
  `/screen/docs` (a folder browser whose rows `navigate` to `/screen/docs?path=…`
  resolved per-item from `item.navHref` via an action `urlBinding`); Config `/screen/config`. `POST /config` proxies
  edits to the sidecar. Balances degrades to a clean empty state that names what it found if no balances
  table exists, rather than erroring. The **app bar** (shell, every page) carries a live freshness +
  **Offline** status and a refresh button; the drawer footer has **Hard refresh** (online-only — disabled
  while offline so it can't wipe the cache with nothing to reload; clears the page cache +
  Workbox caches, unregisters the SW, reloads). Freshness is computed client-side from each page's cache
  `fetchedAt` (`main.ts` overrides `meta.updatedAtFormatted`), live-ticked, and flips to `Offline · …`
  when a fetch fails (off Tailscale / server down) while still rendering from cache. A client-side 4-digit **passcode gate**
  (`Web/src/sdui/lock.ts`; native `PasscodeView.swift`) runs before boot: `1937` → full app; all-even
  digits → a **decoy dashboard** rendered locally with dummy content only (restricted: menu hidden, no
  navigation, no network, no real data — `Web/src/sdui/decoy.ts` / `App/.../Manifest+Decoy.swift`).
  Components: `table`, `map`
  (web Leaflet / native MapKit), `timeline` (stop/move/charge entries), `field` (editable input whose
  widget follows the value `type` — bool renders as a switch), `code` (monospace log/output block with a
  copy-to-clipboard link),
  `markdown` (safe Markdown subset → HTML on web / AttributedString on native), `lineChart` (web SVG /
  native Swift Charts; binds to `[{color,points,x?,dashed?}]` — optional normalized `x` lets segments
  share one axis, `dashed` renders estimated history), `submit` action. The
  menu is built from `nav`.
- `Web/` (TypeScript PWA): `src/sdui/*` renders the manifest; Workbox SW (network-first manifest,
  versioned shell cache); installable + offline. A **background prefetch** (`data/prefetch.ts` +
  `main.ts`) caches pages for offline. A **deep crawl** (boot, reconnect, the manual button) follows
  the internal links inside each fetched manifest (`collectInternalLinks`) — datasets → tables →
  preview/columns, Docs paths, log details, … — so **every** sub-page is cached, not just visited ones
  (bounded by `MAX_CRAWL`). A **three-tier queue** caches **pinned pages first, then base/main pages,
  then parameterized sub-pages**, so the important pages land before the long tail. A **light re-warm**
  on the 5-min timer / foreground refreshes the base nav
  pages + already-cached pages but **skips BQ-table previews** (each runs a real BigQuery query, so
  it's not re-billed on a timer — only on a deep crawl). Any page can be **pinned for offline** via the
  app-bar bookmark toggle (shell, every page; `data/pins.ts`) — pinned pages are refreshed on **every**
  sweep including the light timer and **even if expensive** (you opted in), bounding the otherwise-
  infinite page space (GPS coords, the whole BQ/Docs trees). The drawer footer shows the offline-cache
  status — progress while running, else **"Cached <time> · N pages · ~<size> · N pinned"** (estimated
  from `cache.cacheStats()`) — and a **Cache all pages now** button that triggers the deep crawl on
  demand. (Note: iOS web PWAs cannot refresh while fully closed — no Periodic Background Sync, and web
  push must show a notification — so the server warmer + aggressive on-open/foreground refresh is the
  ceiling; true while-closed refresh needs the deferred native `BGTask`.)
- `App/` (SwiftUI): `App/Sources/SDUI/*` mirrors `Web/src/sdui/*` 1:1; adds private on-device
  calendar (EventKit → `local.calendar.*`), Face ID, BGTask refresh. **Caching** persists every
  fetched page (not just `/dashboard`) keyed by a stable FNV-1a hash and prefetches the nav once
  per launch, so the whole app is browsable offline (`CacheStore`/`DashboardViewModel`).
  **Push** (`PushManager`/`AppDelegate`): registers for APNs on full unlock and forwards the
  device token to the server's `/register_push` for **log-failure alerts**. **Live Activity**
  (`Widget/` extension + `ActivityManager`): a lock-screen/Dynamic-Island "latest activity status"
  from `data.afm.currentState`; its attributes type is shared by SOURCE with the extension (no App
  Group), updated live via APNs. Device install needs Xcode 26 (iOS 26 floor on the test device).

## Verify
- Web (runs anywhere with Node): `cd Web && npm install && npm test`
  (typecheck + schema-contract validation + jsdom render smoke test + **regression suite**
  `scripts/regression.test.mjs`, which locks in nav consistency/clickability, layout/spacing invariants,
  passcode, freshness/offline, hard-refresh, and that every page sample renders — keep it green).
- Server (needs Swift/Xcode, i.e. the Mac mini): `cd Server && swift test`.
- Real-browser e2e (`cd Web && npm run test:e2e`, Playwright on an iPhone viewport): passcode unlock,
  no-dead-space, drawer reachability/clickability. Needs a browser — runs in **GitHub Actions**
  (`.github/workflows/web-ci.yml`) on every PR, and locally after `npx playwright install chromium`. The
  cloud sandbox blocks the browser download, so e2e runs in CI, not in web sessions. `.mcp.json` wires the
  Playwright MCP for interactive browser driving wherever Claude Code runs with network.
- After deploy: `curl https://<TS_HOST>/healthz` and `/dashboard` (or `make doctor`).
- Cross-check kept honest: `Server/.../Templates/screen.json` is byte-identical to
  `Shared/schema/golden-manifest.json`'s `screen`; the web render test validates that tree.

## Conventions
- Secrets: env only, via `Deploy/.env` (git-ignored). Never commit keys.
- Native identifiers live in `Config.xcconfig` (one place). iOS 18 floor.
- Provider formatting (°, %, dates) is done **server-side**; clients stay dumb.
- **Light/dark follows the system.** `theme.json` carries two palettes — `colors` (light, the
  default/fallback) and `dark` — sharing one token set (`bg/cardBg/textPrimary/accent/up/down/divider`
  + shell tokens `appbarBg/cardBorder/overlay/overlayActive/mapBg/onAccent`). Web: `resolveColor`
  emits `var(--c-token)` and `applyTheme` sets the active palette's vars (picked via
  `prefers-color-scheme`, re-applied live on change — no re-render); `styles.css` defines both palettes
  as CSS-var fallbacks (`:root` light + `@media (prefers-color-scheme: dark)`). Native: `JSONValue.activeTheme(dark:)`
  swaps `colors`→`dark` by the SwiftUI `colorScheme`. Add new colors as tokens in **both** palettes; never hard-code a hex in a client.
- Unknown component types / unresolved bindings / newer schema = graceful degradation, never a crash.
- **Shell consistency (check on every change):** nav (app bar + slide-out drawer) and the freshness/Offline
  status live in the shell (`main.ts` + `sdui/drawer.ts`), so they must look and behave identically on every
  screen — never reimplement per page. Layouts must refit to fill the shell (the scroll area flexes; no dead
  space below the body — `.app-shell` is `height:100%`). Freshness timestamps must reflect the real time a
  page's data was cached into the app, and show Offline when the server is unreachable.

## Built (native, beyond the web PWA)
Native OTA/device install (`make ios-deploy` — archive → signed IPA → OTA over Tailscale or
direct-USB install; needs Xcode 26 for an iOS-26 device). **APNs push** for log-failure alerts
(server `Push/`: ES256-JWT `APNsClient` + `LogFailureWatcher` + `/register_push`; configured via
`APNS_*` in `.env`). **Live Activity** for the latest activity status (`Widget/` extension +
`LiveActivityUpdater`, opt-in via `DASHBOARD_LIVE_ACTIVITY_INTERVAL`).

## Deferred (seams in place, do NOT build without being asked)
Web Push (PWA notifications), home-screen widgets, JavaScriptCore logic hot-swap.
