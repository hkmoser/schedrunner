# DEVELOPMENT.md — extending __APP_NAME__

This template is a **server-driven iOS + PWA app**. The Vapor server composes a JSON
manifest (theme + screen tree + live data); thin clients render it. Most product changes
are server-side only — no client rebuild required.

## Quick start

```bash
bash setup.sh __APP_NAME__ com.example.myapp __TEAM_ID__
cp Deploy/.env.example Deploy/.env   # set TS_HOST (required); everything else is optional
make deploy
```

Open `https://<TS_HOST>/` on iPhone (Tailscale ON) → Share → Add to Home Screen.

---

## Adding a new screen

### 1. Add a provider

Create `Server/Sources/App/Providers/MyDataProvider.swift`:

```swift
import Vapor

struct MyDataProvider: DataProvider {
    let key = "mydata"
    let ttl: TimeInterval = 300   // cache 5 min

    func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        // fetch from an API, file, or any source
        .obj([
            ("title", .string("My Data")),
            ("valueFormatted", .string("$42")),
        ])
    }

    func stub(config: AppConfig) -> JSONValue {
        .obj([("title", .string("My Data")), ("valueFormatted", .string("—"))])
    }
}
```

### 2. Register a route

In `routes.swift`, add a composer and a route:

```swift
let myComposer = Composer(providers: [MyDataProvider()])

app.get("screen", "myscreen") { req async -> Manifest in
    await myComposer.build(
        client: req.client,
        config: req.application.__APP_NAME_LOWER__Config,
        cache: req.application.providerCache,
        templates: req.application.templates,
        logger: req.logger,
        screen: req.application.templates.myscreen   // see step 4
    )
}
```

### 3. Add a nav entry

In `Server/Sources/App/Templates/nav.json`, add:

```json
{ "title": "My Screen", "icon": "chart.bar.fill", "path": "/screen/myscreen" }
```

Available icons: any SF Symbol name (e.g. `"star.fill"`, `"map.fill"`, `"bell.fill"`).

### 4. Create the screen template

Create `Server/Sources/App/Templates/myscreen.json`. The manifest's `data` object
contains your provider's key (`mydata`); bind fields with `"binding": "mydata.title"`.

```json
{
  "type": "screen",
  "style": { "background": "$bg" },
  "children": [{
    "type": "scroll",
    "children": [{
      "type": "vstack",
      "style": { "spacing": 16, "padding": 16 },
      "children": [
        {
          "type": "text",
          "binding": "mydata.title",
          "style": { "font": "largeTitle", "weight": "bold", "color": "$textPrimary" }
        },
        {
          "type": "card",
          "style": { "background": "$cardBg", "cornerRadius": 20, "padding": 16 },
          "children": [
            { "type": "text", "binding": "mydata.valueFormatted",
              "style": { "font": "headline", "color": "$textPrimary" } }
          ]
        }
      ]
    }]
  }]
}
```

### 5. Register the template

In `Composer.swift`, add `myscreen` to the `Templates` struct and `load()`:

```swift
public let myscreen: JSONValue
// in load():
myscreen: try loadResource("myscreen"),
```

### 6. Add to the cache warmer

In `Warmer.swift`, add a `Target` so the screen is warmed proactively:

```swift
Target(composer: Composer(providers: [MyDataProvider()]), screen: { $0.myscreen }),
```

### 7. Deploy

```bash
make update   # recompile server + PWA; no restart needed for template-only changes
```

---

## Component reference

The SDUI manifest schema lives in `Shared/schema/manifest.schema.json`.
The most useful component types:

| Type | Props / bindings | Notes |
|------|-----------------|-------|
| `text` | `text` (literal) or `binding` | `font`: largeTitle/headline/body/subhead/caption/caption2 |
| `card` | `children` | Container with optional `background`, `cornerRadius`, `padding` |
| `hstack` / `vstack` | `children`, `spacing`, `align` | Horizontal/vertical stacks |
| `spacer` | — | Fills available space |
| `divider` | `color` | Horizontal rule |
| `badge` | `text` or `binding` | Colored pill; `color` = `"up"` / `"down"` / `"$accent"` / hex |
| `image` | `name` (SF Symbol) or `binding` | SF Symbol icon |
| `table` | `columns`, `rows` | `repeat` over `provider.columns` / `provider.rows` |
| `lineChart` | `binding` → `[{color,points,x?,dashed?}]` | `height` in style |
| `markdown` | `binding` | Safe Markdown → HTML / AttributedString |
| `field` | `type`, `key`, `value` + `submit` action | Editable input |

### Repeat pattern (lists)

```json
{
  "type": "vstack",
  "props": { "repeat": "mydata.items" },
  "children": [
    { "type": "text", "binding": "item.label", "style": { "font": "body" } }
  ]
}
```

### Actions

```json
"action": { "type": "navigate", "urlBinding": "item.path" }
"action": { "type": "openURL", "urlBinding": "item.url" }
"action": { "type": "refresh" }
"action": { "type": "submit", "path": "/my-endpoint" }
```

---

## BigQuery sidecar

The `bq_sidecar/` Python service gives the server access to BigQuery via the Mac's
Application Default Credentials (no keys in the repo). It is **disabled by default** —
the Vapor server runs fine without it, and the Preferences / Secrets & Keys screens
degrade to a stub when it's not running.

### Enable the sidecar

1. Set `BQ_DATASET=your-project.your_dataset` in `Deploy/.env`.
2. Authenticate: `gcloud auth application-default login` on the Mac mini.
3. Start the sidecar: `make bq` (or `cd bq_sidecar && python3 -m uvicorn main:app --port 8099`).
4. Add `BQ_SIDECAR_PORT=8099` to `.env`.
5. `make deploy` — the auto_deploy.sh hook will keep the sidecar running.

### Using BigQuery in a provider

```swift
struct MyBQProvider: DataProvider {
    let key = "mybq"
    let ttl: TimeInterval = 300

    func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        // The sidecar's /query endpoint runs arbitrary SQL
        let url = "\(config.bqSidecarURL)/query"
        // POST your query or use the existing /afm, /balances, /config, /settings endpoints
        // See bq_sidecar/main.py for the full endpoint list
        let value = try await client.getJSON(url, as: JSONValue.self)
        return try value.requireOK("mybq")
    }

    func stub(config: AppConfig) -> JSONValue { .obj([("rows", .array([]))]) }
}
```

Mark it `expensive: true` in `Warmer.targets()` if it runs a BigQuery query — expensive
targets are only warmed when `DASHBOARD_WARM_BQ_INTERVAL > 0` so queries aren't re-billed
on every warm pass.

---

## Push notifications (APNs)

Push is **infrastructure-ready** — the server wires up APNs the moment you provide a key.

### Setup

1. Go to developer.apple.com → Account → Keys → + → enable "Apple Push Notifications service".
2. Download the `.p8` file and drop it in `~/.config/__APP_NAME_LOWER__/apns/` on the Mac mini.
3. That's it — everything else (Key ID, Team ID, bundle ID) is auto-derived. Add `APNS_ENV=sandbox`
   to `.env` for Xcode/OTA dev builds; remove it for TestFlight/App Store.
4. `make restart` to reload the server with the new APNs config.
5. Open the native app, unlock fully, allow notifications — then test with `GET /test_push`.

### Sending custom push alerts

The server's `Push/APNs.swift` provides an `APNsClient`. Inject it into your own watcher:

```swift
// In configure.swift, after APNs is set up:
MyWatcher.start(on: app, config: apnsCfg)
```

See `Push/LogFailureWatcher.swift` for a complete example of a polling watcher that
sends an alert push when a condition is newly met.

### Silent background refresh

Set `DASHBOARD_SILENT_PUSH_INTERVAL=1800` in `.env` to wake the native app every 30 min
so its offline cache is current the moment you open it. This is a content-available push
(no banner) that triggers the app's `BGTask` refresh. iOS throttles delivery; 30 min is
a good floor.

---

## Live Activity (lock screen / Dynamic Island)

The template ships with the `Widget/` extension and `ActivityManager.swift` wired up.
Live Activity is **off by default** (`DASHBOARD_LIVE_ACTIVITY_INTERVAL=0`).

To use it, edit `Widget/LiveActivityWidget.swift` to match your content type, then set
`DASHBOARD_LIVE_ACTIVITY_INTERVAL=60` in `.env`. The server's `LiveActivityUpdater`
pushes the current state on the interval via APNs push-to-update tokens.

---

## next channel (staging environment)

Two full stacks run side by side on the Mac mini:

| Channel | Branch | Port | App name |
|---------|--------|------|----------|
| stable | `main` | 8080 | __APP_NAME__ |
| next | `next` | 8081 | __APP_NAME__ Next (purple) |

**First-time setup:** `bash Deploy/first_next_deploy.sh` (from the stable checkout).

After that, `cd ../iOS-Shell-next && git pull && make deploy` updates the next channel.
The next bundle ID gets `.next` appended (`__BUNDLE_ID__.next`) so it installs ALONGSIDE
stable on your device. See `Deploy/RUNBOOK.md` for full ops detail.

**Branch policy:** target `next` for new features and experiments; `main` only for
production fixes or when you're ready to ship. Promote via `next → main` merge.

---

## AppConfig fields

`Server/Sources/App/Config.swift` defines `AppConfig`, which is loaded from env vars at
startup. Add new fields here for any provider that needs configuration from `.env`.

```swift
// In AppConfig:
public var myAPIKey: String?

// In AppConfig.load(_:):
myAPIKey: str("MY_API_KEY"),
```

Then read it in your provider via `config.myAPIKey`.

---

## Testing

```bash
cd Web && npm install && npm test       # typecheck + schema contract + jsdom render
cd Server && swift test                 # Vapor unit tests (needs Swift/Xcode on Mac)
```

After deploy: `curl https://<TS_HOST>/healthz` and `make doctor`.

The regression suite in `Web/scripts/regression.test.mjs` locks in nav consistency,
passcode, freshness/offline, and that every page sample renders. Keep it green.
