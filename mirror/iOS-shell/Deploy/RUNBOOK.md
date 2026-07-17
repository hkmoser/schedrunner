# Deploy Runbook (Mac mini)

Turn-key deploy of the web dashboard over Tailscale HTTPS. The native iOS app is a
separate, later step (needs paid Apple Developer enrollment) — see the bottom.

## TL;DR
```bash
git clone <repo> && cd iOS-Shell
cp Deploy/.env.example Deploy/.env        # fill in the values (see below)
make doctor                                # fix anything it flags
make deploy                                # builds, serves, healthchecks, prints install URL
```
Then on the iPhone (Tailscale ON): open `https://<TS_HOST>/` in Safari → **Share → Add to Home Screen**.

## What you fill into Deploy/.env
- `TS_HOST` — your Mac mini's MagicDNS name from `tailscale status` (e.g. `macmini.tailnet-abcd.ts.net`).
- `TWELVEDATA_KEY` — free key from https://twelvedata.com/ (markets card).
- `FRED_KEY` — free key from https://fredaccount.stlouisfed.org/apikeys (mortgage card).
- `DASHBOARD_LAT` / `DASHBOARD_LON` / `DASHBOARD_LOCATION` / `DASHBOARD_TZ` — for weather.
- Without the keys, those cards show clearly-labeled **sample data** — the app still works.

## The ONE manual prerequisite: enable Tailscale HTTPS
`itms-services` is not involved for web, but installable PWAs and service workers require
**HTTPS**, which Tailscale provides — once you enable it:
1. Open https://login.tailscale.com/admin/dns
2. Enable **MagicDNS** (if not already).
3. Under **HTTPS Certificates**, click **Enable HTTPS** and acknowledge (names go on a public ledger).

`make doctor` checks this and prints these exact steps if it's not done.

## What `make deploy` does (idempotent, re-runnable)
1. **bootstrap** — ensure swift (Xcode CLT), Homebrew, node, tailscale; `tailscale up`.
2. **doctor** — preflight: toolchain, `.env`, `TS_HOST`, Tailscale connected + HTTPS cert.
3. **setup_tls** — `tailscale serve` proxies `https://$TS_HOST/` → `127.0.0.1:$PORT`.
4. **setup_server** — `npm run build` (web) + `swift build -c release` (server), disable sleep,
   install a **KeepAlive LaunchAgent** (logs to `~/Library/Logs/dashboard/`).
5. **healthcheck** — polls `/healthz`, validates `/dashboard` (schema v1 + all cards), confirms `/` serves the web app.

## Day-2 operations
- `make doctor` — health probe anytime the dashboard looks wrong.
- `make logs` — tail server logs.
- `make update` — after `git pull`: rebuild + reload the server **and the BigQuery sidecar** (no sudo).
- `make deploy-status` — **is the running server actually my latest code?** Compares the deployed
  build (`/healthz`) to local HEAD, lists provider errors, checks the sidecar. Run it after every
  deploy to confirm it took.
- `make restart` — bounce the service.

### How deploys happen here (external service + post-pull hook)
An **external service** owns the schedule: it **`git pull`s**, then runs the repo-root **`./.auto-deploy`**
script, which `exec`s **`Deploy/auto_deploy.sh`** (= `make auto-deploy`). That hook makes the **running**
server match the just-pulled code — a `git pull`
alone changes nothing, because the templates/routes are compiled into the Vapor binary and loaded at
process start, so the server serves stale content until it's rebuilt + restarted. The hook is:
- **idempotent** — no-op when `/healthz` already reports the checked-out HEAD (safe to run every tick);
- **change-scoped** — rebuilds the server only if its sources changed, always reloads the service,
  redeploys the sidecar, mirrors docs (a full `make update`), then triggers the opt-in iOS rebuild;
- **safe** — `make update` fails loudly on a compile error, so a broken commit leaves the previous
  good binary running (recorded FAILED); the server never goes down for a bad build;
- **verified** — confirms `/healthz` reports the new build after restart.

So server-side changes (templates, nav, new pages, sidecar) reach the app automatically once merged —
**no app rebuild** (only native/Swift changes need `make ios-deploy`).

### Verify + manual deploy
- `make deploy-status` — is the running server my latest code? (compares `/healthz` build to HEAD,
  lists provider errors, checks the sidecar). **Run this whenever "I don't see my changes."**
- `make auto-deploy` — run the post-pull hook by hand (what the external service runs).
- `make auto-deploy-status` — last hook run + log tail.
- Manual fallback: `git pull origin main && make update-server && make deploy-status`, then
  pull-to-refresh / drawer → Hard refresh on the phone.

If the server is **stale**, the hook or a manual `make update` didn't run/succeed — it is never a code
problem that server-side changes "don't show"; it's that the running binary wasn't rebuilt from the tree.
- **Change the UI with no redeploy of the phone:** edit `Server/Sources/App/Templates/screen.json`
  or `theme.json`, run `make update`, pull-to-refresh / reopen the PWA. The layout/theme changes live.

## Balances tab (real account balances)
The Balances tab shows real numbers from whatever balances table is populated, in order:
1. the budget-sheet `dashboard_balances` snapshot (set up via `Deploy/balances_sync.gs` — paste into the
   sheet's **Extensions ▸ Apps Script**, add the **BigQuery API** service, set `PROJECT_ID`, run
   `syncBalances` then `installTrigger`), else
2. any `*balance*` table in the dataset, else
3. the YNAB-synced `ynab_balances` table (set `BQ_YNAB_MILLIUNITS=1` if it's stored in YNAB milliunits).

So if you sync YNAB balances into BigQuery, Balances shows them automatically — the separate YNAB tab
was removed as duplicative. Pin a specific table with `BQ_BALANCES_TABLE`. If nothing is found the page
shows a clear empty state naming the tables it saw (no redeploy needed — it's all sidecar-side).

## Smart Home tab + passcode
- The **Smart Home** tab summarizes the event log the Mac mini's `ha-events` service writes to
  Google Drive (`Private/Home/Smart Home Events`). The sidecar reads the Drive-synced files locally —
  auto-detected under the Google Drive for Desktop mount; set `SMARTHOME_DIR` only to override.
- The app opens to a **4-digit passcode**: `1937` unlocks the full app; an all-even code (e.g. `2468`)
  opens a **decoy dashboard** — a normal-looking dashboard rendered locally with dummy content, no
  menu, and no network calls, so an onlooker never sees real data. Client-side gate, no server change.

## BQ Tables tab
The **BQ Tables** tab (under System) lists the dataset's tables via the sidecar's `/bqtables`; tapping
a table drills into its field structure (name / type / mode / description, nested RECORD fields
flattened) via `/bqtables?table=`. No setup beyond `BQ_DATASET`.

## 48h Transactions tab
The **48h Transactions** tab dumps the raw AFM fix-history ('transaction') table for the last
48h as a scrollable columns/rows table — it hits the sidecar's `/afm_raw?hours=48` (cap rows with
`BQ_AFM_RAW_MAX`, default 300) and is cached independently. No setup beyond the AFM history table
the Activity tab already uses.

## System tabs (Logs, Repos) + Docs
These read the Mac's local filesystem through the sidecar — nothing inbound, no BigQuery.
- **Logs** (`/screen/logs`) tails `~/log`: the newest files + last lines of each. Override the
  directory / counts with `LOG_DIR`, `LOG_MAX_FILES`, `LOG_TAIL_LINES`.
- **Repos** (`/screen/repos`) shows git + deploy status for **every git repo under `~/Dropbox/Source`**
  (`REPOS_DIR`) — a recursive, depth-bounded scan that skips `node_modules`/build dirs and won't descend
  into a repo. An explicit `repos.json`/`repos.txt` in that dir (paths or names) overrides the scan.
  Deploy status comes from a per-repo marker file (`.last_deploy` / `.deployed_at`) if your deploy
  tooling writes one; otherwise it's derived from git (clean + in sync = deployed; ahead/dirty = needs deploy).
- **Schedrunner** (`/screen/schedrunner`) summarizes scheduled-job status — each job's last/next run and
  pass/fail, with failures first. It reads schedrunner's status file (`status.json`/`state.json` or a
  per-job state dir under `SCHEDRUNNER_DIR`; point `SCHEDRUNNER_STATUS` at the exact path if it lives
  elsewhere), and falls back to tailing the latest schedrunner log when no status file is present.
- **Docs** (`/screen/docs`) is a Markdown browser over Google Drive `/Private` (`DOCS_DIR`, auto-detected
  under the Drive for Desktop mount). Tap folders to descend, tap an `.md` file to read it rendered.
  Google Drive for Desktop keeps files **online-only** by default, so the sidecar can read them as empty
  ("can't render"). To guarantee availability, `make update` (and `make docs`) **mirror** the `/Private`
  Markdown tree into a local dir (`DOCS_MIRROR`, default `~/.cache/dashboard/docs`) — rsync materializes
  the placeholders — and the sidecar reads that mirror first, so Docs works even offline / with the Drive
  app closed. Re-run `make docs` (or add it to schedrunner on a timer) to pick up new/changed docs. The
  zero-setup alternative: right-click the `/Private` folder in Google Drive → **Offline access → Available
  offline**.

## Reboot resilience
The server runs as a user **LaunchAgent**, which requires a login session. For unattended
restarts after a power blip, enable **auto-login** (System Settings → Users & Groups → Login Options),
or convert the agent to a system **LaunchDaemon**. Sleep is disabled by `setup_server.sh` (`pmset`).

## Stable + Next channels (test new features safely)
Run two full stacks side by side on the same Mac so you always have a **stable** app plus a
**next** app for trying new features — each installs as its own PWA with its own cache.

| | Stable (default) | Next |
|---|---|---|
| Branch | `main` | `next` |
| Checkout | this repo | a git **worktree** at `../iOS-Shell-next` |
| Server / sidecar / TLS | `:8080` / `:8099` / `:443` | `:8081` / `:8100` / `:8443` |
| launchd labels | `com.joemoser.dashboard.*` | `…*-next` |
| URL | `https://$TS_HOST/` | `https://$TS_HOST:8443/` |
| Home-screen app | "Dashboard" | "Dashboard Next" (purple, "NEXT" in the footer) |

Everything is derived from `DASHBOARD_CHANNEL` (stable unless set to `next`), so **stable is
untouched** — the external pull+hook keeps deploying `main` on `:8080` exactly as before.

**One-time setup:**
```
make next-setup                       # creates the `next` branch + ../iOS-Shell-next worktree
cd ../iOS-Shell-next
make deploy CHANNEL=next              # brings up :8081 / :8100 and serves https://$TS_HOST:8443/
make auto-deploy-timer CHANNEL=next   # (optional) self-heal the next stack to branch `next`
```
Then on the phone open `https://$TS_HOST:8443/` in Safari → **Add to Home Screen** — you now have
both apps installed. `make channel-status` shows both at a glance.

**Workflow:** develop features on `next`, test them in the Next app; when happy, **promote by
merging `next → main`** and the Stable app picks them up on its normal deploy. Both channels share
`Deploy/.env` (secrets) and — by default — read the same BigQuery/Drive data through their own
sidecar, so the data is identical; only the code/behavior differs.

**Isolation guarantees + known shared state:**
- The channel is **pinned to the checkout** (`Deploy/.channel`, written by `next-setup`; absent =
  stable) and baked into every LaunchAgent plist — so a bare `make update`, the auto-deploy timer,
  or a Redeploy tap inside the next worktree can never touch stable's labels, ports, or bundles.
- Each channel gets its **own log files** (`server-next.err.log`, `bq-next.out.log`, …).
- **Shared on purpose** (both channels are the same person's data on one Mac): BigQuery tables
  (`dashboard_config`, `known_locs`, balances/budget), the Drive mirrors, and the **Settings/
  secrets store** — flipping a setting in the Next app reconfigures the machine, and stable will
  see it after its next restart. Treat Settings edits as machine-global, not per-channel.
- **Native iOS OTA is channel-aware**: `make ios-deploy` (or the DEPLOY_IOS auto-build) in the
  next worktree produces a SEPARATE app — bundle id `<id>.next`, named "Dashboard Next", talking
  to `:8443` — installable alongside stable from `https://$TS_HOST:8443/app/`. First next build:
  automatic signing self-provisions the new bundle id (plug the phone in once if it complains
  about device registration).
- Both home-screen apps share the same icon art for now; they're distinguished by label
  ("Dashboard" vs "Next"/"Dashboard Next") and, inside the app, an always-visible purple
  **NEXT badge in the app bar** on every page (web + native), plus the NEXT footer marker (PWA).

## CI minutes (GitHub Actions cost)
This is a **private repo**, so Actions minutes bill against the account's included quota
(Free: 2,000/mo) — and **macOS runners burn minutes at a 10× multiplier**. When the quota (or
spending limit) is exhausted, every job "fails" instantly: 1–2 s, no runner assigned, **no
logs at all** — that's GitHub refusing to start, not a code failure. Re-run after the billing
cycle resets, or raise the limit under Settings → Billing (overage ≈ $0.008/min Linux,
$0.08/min macOS).

How the workflows are kept cheap:
- **Web CI** — Linux (1×), path-filtered: only runs when `Web/`/`Shared/` change.
- **Server CI** — **Linux** in the official Swift container (the server is Vapor + swift-crypto,
  fully cross-platform), path-filtered to `Server/`. Was macOS (10×) for no benefit — the Mac
  mini compiles the real binary itself at deploy time.
- **iOS CI** — the one genuinely-macOS job, so it's **PR-only** (no push-to-main run; the merged
  tree already passed on the PR) with cancel-in-progress and a timeout.

**Make macOS CI free entirely — self-hosted runner on the Mac mini** (self-hosted minutes are
never billed): repo → Settings → Actions → Runners → *New self-hosted runner* (macOS) → run the
three printed commands on the Mac → `./svc.sh install` (launchd keeps it alive). Then switch
`runs-on: macos-15` → `runs-on: [self-hosted, macOS]` in `ios-ci.yml`. Only do the switch AFTER
the runner shows "Idle" in settings — an unregistered label leaves jobs queued forever. Bonus:
warm local caches make it faster than hosted runners.

## Troubleshooting
- *Cert error in setup_tls* → HTTPS not enabled in the tailnet (see the manual step above).
- *Next app won't load on `:8443`* → run `tailscale serve status`; `make deploy CHANNEL=next`
  installs the `--https=8443` proxy. Both `:443` and `:8443` can serve at once.
- *Phone can't load the app* → Tailscale must be **ON** on the iPhone; verify with `tailscale status`.
  (If it was loaded before, the installed PWA still shows cached data offline.)
- *A card shows "sample"/stale* → that provider's key is missing or the upstream failed;
  `curl https://$TS_HOST/healthz` shows the last successful refresh per card.
- *Server won't start* → `tail -f ~/Library/Logs/dashboard/server.err.log`.
- *Recent changes aren't showing in the app* → run **`make update`** (a plain `git pull` or `make restart`
  does NOT rebuild — the server loads templates/nav once at startup, so new pages/nav/template edits
  need a rebuilt-and-restarted binary). `make update` now **fails loudly if the server build fails**
  instead of silently keeping the old binary. Verify what's actually running with
  `curl -s https://$TS_HOST/healthz` — its `build` field is the deployed git SHA; `make doctor` flags a
  mismatch with your checkout. The **drawer footer now shows the loaded version** (`app <sha> · server <sha>`):
  if it doesn't change after a deploy, the bundle is stale (use **Hard refresh**, or the SW now auto-checks
  for updates every minute + on foreground and reloads), or the server didn't redeploy (its sha is old).
  As a last resort, remove and re-add the PWA to the Home Screen.

---

## Native iOS app (paid Apple Developer enrollment)
The SwiftUI shell in `App/` renders the same `/dashboard` manifest and adds the private on-device
calendar (EventKit) + Face ID. Builds run **on the Mac mini** (Xcode required). With the server
already deployed (`make deploy`), getting it on your phone is essentially two steps:

**Turn-key install:**
1. **One time:** sign into Xcode with your Apple ID — Xcode → Settings → Accounts → **+**.
   (Apple's 2FA login is the one thing that can't be scripted.)
2. Plug the iPhone into the Mac (first install only — to register the device), then:
   ```
   make ios-deploy
   ```
   On the iPhone (Tailscale ON): open **`https://<TS_HOST>/app/`** in Safari → tap **Install** →
   trust the cert under Settings → General → VPN & Device Management. Reinstalls: just re-run
   `make ios-deploy` (no cable) and re-open the install page.

That's it — `make ios-deploy` auto-resolves everything else: it **detects your Team ID** from the
signing certificate, **reads the host** from `Deploy/.env` (`TS_HOST`), **installs xcodegen** via
Homebrew if missing, generates the project, archives (Release, automatic signing,
`-allowProvisioningUpdates` registers the connected device), exports a signed `Dashboard.ipa` +
`manifest.plist`, and publishes them + an install landing page to the server. No `Config.xcconfig`
edits required.

**Helpers / notes:**
- `make ios-generate` — just produce `Dashboard.xcodeproj` to open in Xcode (e.g. to Run on the
  Simulator, which needs no signing). `make ios-register` — record the device UDID explicitly (rarely
  needed; the build registers a connected device on its own).
- Override auto-detection only if you must: set `DEVELOPMENT_TEAM` in `Config.xcconfig` (it wins).
- Preflight is fail-fast: not signed into Xcode, or an undetectable Team ID, stops immediately with
  the exact fix — never an opaque mid-archive failure.
- The OTA payload lives in `build/ipa/` and is published into `Web/dist/app/` by a shared helper that
  `setup_server.sh` re-runs after every web build, so `make update`/`make update-web` auto-republish
  the IPA — you never have to remember to.
