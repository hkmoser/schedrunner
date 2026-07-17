import Foundation
import Vapor

struct HealthResponse: Content {
    let status: String
    let schemaVersion: Int
    let build: String
    let lastOk: [String: String]
    let errors: [String: String]
}

// Compact payloads for the Home Screen widgets (small + fast; the widget extension fetches these
// over Tailscale rather than parsing a full manifest). All reuse the cached providers.
struct WidgetShipItem: Content { let label: String; let meta: String; let shipPath: String; let repo: String }
struct WidgetReposResponse: Content { let items: [WidgetShipItem] }
/// Result of the Deploy page's "Redeploy now" kick (submit reads statusDirection).
/// `polling: true` tells the client the deploy is running in background — show "Building…"
/// and poll the page every 5 s instead of immediately showing "✓ Done".
struct DeployKick: Content { let ok: Bool; let statusDirection: String; let statusFormatted: String; let polling: Bool }
struct WidgetActivityResponse: Content {
    let title: String       // widget header — "Activity", or "Home Lights" when masked (disabled mode)
    let status: String; let place: String; let meta: String; let icon: String
    let category: String; let elapsed: String; let clockRange: String
    let startedAt: Double   // unix seconds of the current segment start (0 = none) — widget ticks live
}
/// Sidecar /devices_together result: the privacy overlay for the Activity widget.
struct TogetherInfo: Content { let together: Bool; let status: String?; let place: String?; let meta: String?; let icon: String? }
struct WidgetBalanceGroup: Content { let name: String; let total: String; let direction: String }
struct WidgetBalancesResponse: Content { let netFormatted: String; let asOfFormatted: String; let groups: [WidgetBalanceGroup] }
struct WidgetDeployResponse: Content {
    let headline: String; let headlineDirection: String
    let serverBadge: String; let serverDirection: String
    let iosBadge: String; let iosDirection: String
    let failureStage: String   // "" | "server" | "ios"
    let failureDetail: String; let canRedeploy: Bool
    let installURL: String; let buildSHA: String
}
struct WidgetHealthProviderItem: Content { let name: String; let ok: Bool; let ago: String }
struct WidgetHealthResponse: Content {
    let build: String; let statusFormatted: String; let direction: String
    let providers: [WidgetHealthProviderItem]
}
struct WidgetRepoResponse: Content {
    let name: String; let branch: String
    let gitFormatted: String; let gitDirection: String
    let deployFormatted: String; let deployDirection: String
    let lastFormatted: String
}
struct WidgetLogResponse: Content { let name: String; let metaFormatted: String; let tail: String }
struct WidgetListItem: Content { let id: String; let name: String }

/// Fetch a provider's data through the shared ProviderCache (so widgets reuse the page's cached
/// value — no extra BigQuery/GitHub cost), falling back to its stub on a cold miss.
func widgetProviderData(_ req: Request, _ provider: DataProvider) async -> JSONValue {
    let cfg = req.application.dashboardConfig
    let r = await req.application.providerCache.value(for: provider.cacheKey(cfg), ttl: provider.ttl) {
        try await provider.fetch(client: req.client, config: cfg, logger: req.logger)
    }
    switch r {
    case .fresh(let v), .stale(let v): return v
    case .miss: return provider.stub(config: cfg)
    }
}

/// Body of POST /register_push. Both optional so the app can register either token alone.
struct RegisterPushBody: Content {
    let token: String?              // standard remote-notification (alert) token
    let liveActivityToken: String?  // per-activity push-to-update token for the Live Activity
}

public func routes(_ app: Application) throws {
    let composer = Composer(providers: [
        WeatherProvider(),
        StocksProvider(),
        MortgageProvider(),
        PropertyProvider(),
        RepoBannerProvider(),
    ])
    let bqComposer = Composer(providers: [BigQueryProvider()])
    let afmComposer = Composer(providers: [AFMProvider()])
    let afm48Composer = Composer(providers: [AFM48Provider()])
    let afmLogComposer = Composer(providers: [AFMLogProvider()])
    let afmHealthComposer = Composer(providers: [AFMHealthProvider()])
    let smartHomeLogComposer = Composer(providers: [SmartHomeLogProvider()])
    let deployComposer = Composer(providers: [DeployProvider()])
    let knownLocComposer = Composer(providers: [KnownLocProvider()])
    let configComposer = Composer(providers: [ConfigProvider()])
    let settingsComposer = Composer(providers: [SettingsProvider()])
    let balancesComposer = Composer(providers: [BalancesProvider()])
    let budgetComposer = Composer(providers: [BudgetProvider()])
    let gcpCostsComposer = Composer(providers: [GCPCostsProvider()])
    let smartHomeComposer = Composer(providers: [SmartHomeProvider()])
    let bqTablesComposer = Composer(providers: [BQTablesProvider()])
    let logsComposer = Composer(providers: [LogsProvider()])
    let logFileComposer = Composer(providers: [LogFileProvider()])
    let messagesComposer = Composer(providers: [MessagesProvider()])
    let reposComposer = Composer(providers: [ReposProvider()])
    let reposShipComposer = Composer(providers: [ReposShipProvider()])
    let schedrunnerComposer = Composer(providers: [SchedrunnerProvider()])
    let schedlogsComposer = Composer(providers: [SchedLogsProvider()])
    let docsComposer = Composer(providers: [DocsProvider()])

    // The SDUI manifest: theme + screen (from templates) + live data (from providers).
    // The client may supply its own location/timezone (?lat=&lon=&tz=&place=) so
    // weather + times reflect the device, not the server's network.
    app.get("dashboard") { req async -> Manifest in
        var config = req.application.dashboardConfig
        // ?loc=home shows weather for the home location instead of the device.
        config.weatherAtHome = req.query[String.self, at: "loc"] == "home"
        if config.weatherAtHome {
            // Pin Home to the configured location (DASHBOARD_LAT/LON), defaulting to
            // ZIP 20007 (Washington, DC) — not IP geolocation, which was imprecise.
            if config.latitude == nil || config.longitude == nil {
                config.latitude = AppConfig.homeDefault.lat
                config.longitude = AppConfig.homeDefault.lon
                config.locationName = config.locationName ?? AppConfig.homeDefault.name
            }
        } else if let lat = req.query[Double.self, at: "lat"], let lon = req.query[Double.self, at: "lon"] {
            // Device coordinates win: derive the place name from them (reverse
            // geocode), not from a possibly-stale DASHBOARD_LOCATION.
            config.latitude = lat
            config.longitude = lon
            config.locationName = nil
        }
        if let tz = req.query[String.self, at: "tz"], TimeZone(identifier: tz) != nil {
            config.timezone = tz
            config.timezonePinned = true
        }
        if let place = req.query[String.self, at: "place"], !place.isEmpty {
            config.locationName = place
        }
        return await composer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger
        )
    }

    // The BigQuery page: a navigable sub-screen rendering the sidecar's query result.
    app.get("screen", "bigquery") { req async -> Manifest in
        await bqComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.bigquery
        )
    }

    // Activity page. ?range= drives the Today / Yesterday / This-week filter.
    app.get("screen", "afm") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.afmRange = req.query[String.self, at: "range"]
        return await afmComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.afm
        )
    }

    // "Label location" screen — prefilled from a tapped Activity stop (?lat=&lon=&place=).
    app.get("screen", "afm_label") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.knownLat = req.query[String.self, at: "lat"]
        config.knownLon = req.query[String.self, at: "lon"]
        config.knownPlace = req.query[String.self, at: "place"]
        return await knownLocComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.afmLabel
        )
    }

    // "Ship branch" confirm screen — reached from a repo's PR/branch row (?owner=&name=
    // &branch=&base=&pr=). Its submit posts to /repos_pr to create+merge+delete.
    app.get("screen", "repos_ship") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.shipOwner = req.query[String.self, at: "owner"]
        config.shipName = req.query[String.self, at: "name"]
        config.shipBranch = req.query[String.self, at: "branch"]
        config.shipBase = req.query[String.self, at: "base"]
        config.shipPR = req.query[String.self, at: "pr"]
        return await reposShipComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.reposShip
        )
    }

    // Activity Log — a flat newest-first log of every stop/move/charge (?range= like Activity).
    app.get("screen", "afm_log") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.afmRange = req.query[String.self, at: "range"]
        return await afmLogComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.afmLog
        )
    }

    // AFM Live health — a precise per-run success/failure + back-off timeline of afm_live (24h).
    app.get("screen", "afm_health") { req async -> Manifest in
        return await afmHealthComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.afmHealth
        )
    }

    // Deploy status — server current/stale + iOS build BUILDING/STUCK/done, read from build/*.status.
    app.get("screen", "deploy") { req async -> Manifest in
        return await deployComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.deploy
        )
    }

    // Smart Home Log — newest-first active state changes only (motion / door / lights).
    app.get("screen", "smarthome_log") { req async -> Manifest in
        return await smartHomeLogComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.smarthomeLog
        )
    }

    // Last-48 page — two tabs: afm_now (default) and the raw 48h history (?view=raw).
    app.get("screen", "afm48") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.afm48View = req.query[String.self, at: "view"]
        return await afm48Composer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.afm48
        )
    }

    // BigQuery tables page (a tab) — list tables, drill into field structure via ?table=.
    app.get("screen", "bqtables") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.bqDataset = req.query[String.self, at: "dataset"]
        config.bqTable = req.query[String.self, at: "table"]
        config.bqView = req.query[String.self, at: "view"]
        return await bqTablesComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.bqtables
        )
    }

    // Editable config page (a tab).
    app.get("screen", "config") { req async -> Manifest in
        await configComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.config
        )
    }

    // Settings page — enter config/secrets in the app, stored via the sidecar's secrets backend.
    app.get("screen", "settings") { req async -> Manifest in
        await settingsComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.settings
        )
    }

    // Account balances page (a tab) — the sheet pushes a snapshot into BigQuery.
    app.get("screen", "balances") { req async -> Manifest in
        await balancesComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.balances
        )
    }

    // Budget page (a tab): average monthly spending per category over the last 6 full
    // months, grouped into buckets with subtotals + a min–max range per category.
    app.get("screen", "budget") { req async -> Manifest in
        await budgetComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.budget
        )
    }

    // GCP billing costs page: 12-month trend + current month total + service breakdown.
    app.get("screen", "gcp_costs") { req async -> Manifest in
        await gcpCostsComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.gcpCosts
        )
    }

    // Smart-home event-log summary page (a tab; also the restricted-passcode landing).
    // ?sources= (tier 1) × ?types= (tier 2) drive the two-level multi-select filter
    // (absent types = useful defaults; absent sources = all sources).
    app.get("screen", "smarthome") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.smartHomeTypes = req.query[String.self, at: "types"]
        config.smartHomeSources = req.query[String.self, at: "sources"]
        return await smartHomeComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.smarthome
        )
    }

    // Logs page (a tab): tail of the Mac's ~/log directory. ?status= filters by flag.
    app.get("screen", "logs") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.logsStatus = req.query[String.self, at: "status"]
        return await logsComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.logs
        )
    }

    // One log's 24h detail + flagged anomalies (?file=), opened from a Logs card.
    app.get("screen", "logfile") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.logFile = req.query[String.self, at: "file"]
        return await logFileComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.logfile
        )
    }

    // Repos page (a tab): git + deploy status of schedrunner's repos.
    app.get("screen", "repos") { req async -> Manifest in
        await reposComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.repos
        )
    }

    // Schedrunner page (a tab): scheduled-job status summary.
    app.get("screen", "schedrunner") { req async -> Manifest in
        await schedrunnerComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.schedrunner
        )
    }

    // Sched Logs page (a tab): per-script status from schedrunner's log files.
    app.get("screen", "schedlogs") { req async -> Manifest in
        await schedlogsComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.schedlogs
        )
    }

    // Docs page (a tab): markdown browser over Google Drive /Private. The ?path=
    // query selects the folder or .md file being viewed.
    app.get("screen", "docs") { req async -> Manifest in
        var config = req.application.dashboardConfig
        config.docsPath = req.query[String.self, at: "path"]
        return await docsComposer.build(
            client: req.client,
            config: config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.docs
        )
    }

    // Messages page: recent iMessage/SMS from the Mac + latest Gmail headers.
    app.get("screen", "messages") { req async -> Manifest in
        await messagesComposer.build(
            client: req.client,
            config: req.application.dashboardConfig,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.messages
        )
    }

    // Write config edits / known locations: proxy the body to the sidecar's MERGE-upsert.
    func proxyPost(_ req: Request, to endpoint: String) async throws -> Response {
        let url = "\(req.application.dashboardConfig.bqSidecarURL)\(endpoint)"
        let body = req.body.data
        let upstream = try await req.client.post(URI(string: url)) { out in
            out.headers.contentType = .json
            if let body { out.body = body }
        }
        let response = Response(status: upstream.status)
        response.headers.contentType = .json
        if let buffer = upstream.body { response.body = .init(buffer: buffer) }
        return response
    }
    app.post("config") { req async throws -> Response in try await proxyPost(req, to: "/config") }
    // Saving a setting writes it to the sidecar's store, but the RUNNING server still holds the
    // env it loaded at boot (and the APNs client built from it). Restart the launchd service on a
    // successful save so SecretsHydrator re-runs and clients rebuild with the new value — no manual
    // `make restart`. The restart fires AFTER the response flushes; a no-op off launchd / dev.
    app.post("settings") { req async throws -> Response in
        let resp = try await proxyPost(req, to: "/settings")
        if (200..<300).contains(resp.status.code) { scheduleSelfRestart(req) }
        return resp
    }
    app.post("known_locs") { req async throws -> Response in try await proxyPost(req, to: "/known_locs") }
    // 1-click ship: create PR (if needed) → merge → delete branch. The repo/branch context
    // rides in the query (?owner=&name=&branch=&base=&pr=); forward it to the sidecar.
    app.post("repos_pr") { req async throws -> Response in
        let q = req.url.query.map { "?\($0)" } ?? ""
        let resp = try await proxyPost(req, to: "/repos_pr\(q)")
        // A ship merges + deletes the branch; drop the cached repos list + home banner so the
        // very next refresh shows the result (branch gone) instead of waiting out the 60s TTL —
        // that's what "visualizes completion" for the client.
        if (200..<300).contains(resp.status.code) {
            await req.application.providerCache.invalidate("repos", "repoBanner", "deploy")
        }
        return resp
    }

    // "Redeploy now" from the Deploy page — runs the post-pull hook (.auto-deploy) so a stale
    // server can be brought current from the phone. Spawned DETACHED (nohup, its own I/O) so it
    // survives the very restart it triggers; the response returns immediately. Behind the passcode
    // gate + Tailscale. A no-op-safe "kick": auto_deploy.sh no-ops when already current.
    app.post("deploy_kick") { req -> DeployKick in
        let repo = Environment.get("DASHBOARD_REPO")
            ?? Environment.get("WEB_DIST").flatMap { w in w.range(of: "/Web/dist", options: .backwards).map { String(w[..<$0.lowerBound]) } }
            ?? FileManager.default.currentDirectoryPath
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", "cd \(repo) && nohup bash .auto-deploy >> build/deploy-kick.log 2>&1 &"]
        do { try p.run() } catch {
            return DeployKick(ok: false, statusDirection: "down", statusFormatted: "Couldn't start redeploy: \(error.localizedDescription)", polling: false)
        }
        return DeployKick(ok: true, statusDirection: "up",
                          statusFormatted: "Redeploy started — the server will rebuild + restart. Refresh in ~30–60s.",
                          polling: true)
    }

    // --- Home Screen widgets: compact JSON the widget extension fetches over Tailscale ---
    // 1) Open PRs / unmerged branches to ship (each carries the /repos_pr ship path for the
    //    widget's one-tap Ship button).
    app.get("widget", "repos") { req async -> WidgetReposResponse in
        let data = await widgetProviderData(req, ReposProvider())
        var items: [WidgetShipItem] = []
        for repo in data["repos"]?.arrayValue ?? [] {
            let name = repo["name"]?.stringValue ?? ""
            for it in repo["shipItems"]?.arrayValue ?? [] {
                let path = it["shipPostHref"]?.stringValue ?? ""
                guard !path.isEmpty else { continue }
                items.append(WidgetShipItem(label: it["labelFormatted"]?.stringValue ?? "",
                                            meta: it["metaFormatted"]?.stringValue ?? "",
                                            shipPath: path, repo: name))
            }
        }
        return WidgetReposResponse(items: items)
    }
    // 2) Latest Activity status (the AFM current-state block), with a privacy overlay: when the
    //    two configured devices are together, the sidecar returns dummy content + an alternate
    //    icon so the widget masks the real status (the Activity PAGE is unaffected).
    app.get("widget", "activity") { req async -> WidgetActivityResponse in
        let cur = await widgetProviderData(req, AFMProvider())["currentState"]
        let sidecar = req.application.dashboardConfig.bqSidecarURL
        let t = try? await req.client.getJSON("\(sidecar)/devices_together", as: TogetherInfo.self)
        if t?.together == true {
            // Disabled/masked mode: disguise the widget as a "Home Lights" tile — a lightbulb icon
            // and the header "Home Lights" — with no timing, so nothing about where/when leaks.
            return WidgetActivityResponse(title: "Home Lights",
                                          status: t?.status ?? "", place: t?.place ?? "",
                                          meta: t?.meta ?? "", icon: "lightbulb.fill",
                                          category: "", elapsed: "", clockRange: "", startedAt: 0)
        }
        return WidgetActivityResponse(title: "Activity",
                                      status: cur?["statusFormatted"]?.stringValue ?? "",
                                      place: cur?["placeFormatted"]?.stringValue ?? "",
                                      meta: cur?["metaFormatted"]?.stringValue ?? "", icon: t?.icon ?? "location.fill",
                                      category: cur?["category"]?.stringValue ?? "",
                                      elapsed: cur?["elapsedFormatted"]?.stringValue ?? "",
                                      clockRange: cur?["clockRangeFormatted"]?.stringValue ?? "",
                                      startedAt: cur?["startedAtEpoch"]?.doubleValue ?? 0)
    }
    // 3) Balances by group (per-group subtotal + the net total).
    app.get("widget", "balances") { req async -> WidgetBalancesResponse in
        let data = await widgetProviderData(req, BalancesProvider())
        let groups = (data["groups"]?.arrayValue ?? []).map {
            WidgetBalanceGroup(name: $0["title"]?.stringValue ?? "",
                               total: $0["subtotalFormatted"]?.stringValue ?? "",
                               direction: $0["subtotalDirection"]?.stringValue ?? "up")
        }
        return WidgetBalancesResponse(netFormatted: data["netFormatted"]?.stringValue ?? "",
                                      asOfFormatted: data["asOfFormatted"]?.stringValue ?? "",
                                      groups: groups)
    }
    // 4) Deploy pipeline status — headline + server/iOS badges + failure card + redeploy action.
    app.get("widget", "deploy") { req async -> WidgetDeployResponse in
        let data = await widgetProviderData(req, DeployProvider())
        let serverDir = data["serverBadgeDirection"]?.stringValue ?? "#9aa4c4"
        let iosDir   = data["iosDirection"]?.stringValue ?? "#9aa4c4"
        var failureStage = ""
        var failureDetail = ""
        if serverDir == "down" {
            failureStage = "server"
            failureDetail = data["remedyFormatted"]?.stringValue
                ?? data["autoDeployFormatted"]?.stringValue ?? ""
        } else if iosDir == "down" {
            failureStage = "ios"
            failureDetail = data["iosDetailFormatted"]?.stringValue ?? ""
        }
        let canRedeploy = !(data["redeployActions"]?.arrayValue ?? []).isEmpty
        let installURL  = (data["installActions"]?.arrayValue ?? [])
            .compactMap { $0["installURL"]?.stringValue }.first ?? ""
        // Extract just the SHA portion from "checked-out HEAD: abc1234"
        let headLine = data["headFormatted"]?.stringValue ?? ""
        let buildSHA = headLine.components(separatedBy: ": ").last ?? headLine
        return WidgetDeployResponse(
            headline: data["headlineFormatted"]?.stringValue ?? "",
            headlineDirection: data["headlineDirection"]?.stringValue ?? "#9aa4c4",
            serverBadge: data["serverBadgeFormatted"]?.stringValue ?? "",
            serverDirection: serverDir,
            iosBadge: data["iosStatusFormatted"]?.stringValue ?? "",
            iosDirection: iosDir,
            failureStage: failureStage, failureDetail: failureDetail,
            canRedeploy: canRedeploy, installURL: installURL, buildSHA: buildSHA)
    }
    // 5) Server health summary — build SHA + per-provider last-ok / error state.
    app.get("widget", "health") { req async -> WidgetHealthResponse in
        let snapshot = await req.application.providerCache.healthSnapshot()
        let errors   = await req.application.providerCache.errorsSnapshot()
        let build    = Environment.get("DASHBOARD_BUILD") ?? "dev"
        let errorCount = errors.count
        let items = snapshot.keys.sorted().map { key in
            WidgetHealthProviderItem(name: key, ok: errors[key] == nil, ago: snapshot[key] ?? "")
        }
        return WidgetHealthResponse(
            build: build,
            statusFormatted: errorCount == 0 ? "All providers OK" : "\(errorCount) error\(errorCount == 1 ? "" : "s")",
            direction: errorCount == 0 ? "up" : "down",
            providers: items)
    }
    // 6) Single repo status — caller passes ?name= (the AppEntity id).
    app.get("widget", "repo") { req async -> WidgetRepoResponse in
        let name = req.query[String.self, at: "name"] ?? ""
        let data  = await widgetProviderData(req, ReposProvider())
        let repos = data["repos"]?.arrayValue ?? []
        let r = repos.first { $0["name"]?.stringValue == name } ?? repos.first
        return WidgetRepoResponse(
            name: r?["name"]?.stringValue ?? name,
            branch: r?["branch"]?.stringValue ?? "",
            gitFormatted: r?["gitFormatted"]?.stringValue ?? "",
            gitDirection: r?["gitDirection"]?.stringValue ?? "#9aa4c4",
            deployFormatted: r?["deployFormatted"]?.stringValue ?? "",
            deployDirection: r?["deployDirection"]?.stringValue ?? "#9aa4c4",
            lastFormatted: r?["lastFormatted"]?.stringValue ?? "")
    }
    // 7) Repos list for widget back-panel (AppEntity suggestions).
    app.get("widget", "repos_list") { req async -> [WidgetListItem] in
        let data = await widgetProviderData(req, ReposProvider())
        return (data["repos"]?.arrayValue ?? []).compactMap {
            guard let n = $0["name"]?.stringValue, !n.isEmpty else { return nil }
            return WidgetListItem(id: n, name: n)
        }
    }
    // 8) Single log file status — caller passes ?name= (the AppEntity id).
    app.get("widget", "log") { req async -> WidgetLogResponse in
        let name = req.query[String.self, at: "name"] ?? ""
        let data  = await widgetProviderData(req, LogsProvider())
        let files = data["files"]?.arrayValue ?? []
        let f = files.first { $0["name"]?.stringValue == name } ?? files.first
        return WidgetLogResponse(
            name: f?["name"]?.stringValue ?? name,
            metaFormatted: f?["metaFormatted"]?.stringValue ?? "",
            tail: f?["tail"]?.stringValue ?? "")
    }
    // 9) Log files list for widget back-panel (AppEntity suggestions).
    app.get("widget", "logs_list") { req async -> [WidgetListItem] in
        let data = await widgetProviderData(req, LogsProvider())
        return (data["files"]?.arrayValue ?? []).compactMap {
            guard let n = $0["name"]?.stringValue, !n.isEmpty else { return nil }
            return WidgetListItem(id: n, name: n)
        }
    }

    // Register the app's APNs tokens so the server can push log-failure alerts (`token`) and
    // Live Activity updates (`liveActivityToken`). Stored regardless of whether APNs keys are
    // set yet, so tokens accrue and pushes start the moment the key is configured.
    app.post("register_push") { req async throws -> HTTPStatus in
        let body = try req.content.decode(RegisterPushBody.self)
        if let t = body.token { await req.application.deviceTokens?.addAlert(t) }
        if let t = body.liveActivityToken { await req.application.deviceTokens?.addLiveActivity(t) }
        return .ok
    }

    // Send a test push to every registered device — verifies APNs end-to-end (key → JWT →
    // Apple → phone). Returns the per-token APNs status so failures are diagnosable.
    app.get("test_push") { req async -> Response in
        let app = req.application
        func json(_ obj: [String: Any]) -> Response {
            let data = (try? JSONSerialization.data(withJSONObject: obj, options: .prettyPrinted)) ?? Data("{}".utf8)
            let r = Response(status: .ok); r.headers.contentType = .json; r.body = .init(data: data); return r
        }
        guard let apns = app.apns else {
            return json(["ok": false,
                         "status": "APNs not configured. The line below shows exactly what's missing; fix it then `make restart`.",
                         "diagnosis": APNsConfig.diagnose()])
        }
        guard let tokens = await app.deviceTokens?.alertTokens(), !tokens.isEmpty else {
            return json(["ok": false, "status": "No registered device tokens yet. Open the app, unlock fully, ALLOW notifications, then retry."])
        }
        let payload: [String: Any] = ["aps": ["alert": ["title": "Dashboard", "body": "Test push — it works ✅"], "sound": "default"]]
        var results: [[String: Any]] = []
        for t in tokens {
            let code = await apns.send(payload, to: t, type: .alert, client: app.client, logger: req.logger)
            results.append(["token": String(t.prefix(8)) + "…", "apnsStatus": code])
        }
        return json(["ok": true, "status": "sent to \(tokens.count) device(s) — check your phone", "results": results])
    }

    // Liveness + last successful refresh per card, for `make doctor` / monitoring.
    app.get("healthz") { req async -> HealthResponse in
        let snapshot = await req.application.providerCache.healthSnapshot()
        let errors = await req.application.providerCache.errorsSnapshot()
        return HealthResponse(
            status: "ok",
            schemaVersion: Manifest.currentSchemaVersion,
            build: Environment.get("DASHBOARD_BUILD") ?? "dev",
            lastOk: snapshot,
            errors: errors
        )
    }
}

/// Restart the launchd service so a just-saved setting takes effect immediately. The running
/// process loaded its env at boot, so new settings (APNs key, intervals, secrets) are otherwise
/// invisible until the next manual restart. We kick the service `kickstart -k` AFTER the HTTP
/// response has flushed (a short delay), so the save request still returns cleanly; KeepAlive
/// brings the fresh process right back up and it re-hydrates from the sidecar. A no-op when not
/// running under launchd (dev / Linux) or when `DASHBOARD_SELF_RESTART=0`.
func scheduleSelfRestart(_ req: Request) {
    guard Environment.get("DASHBOARD_SELF_RESTART") != "0" else { return }
    let label = Environment.get("DASHBOARD_SERVICE_LABEL") ?? "com.joemoser.dashboard.server"
    let logger = req.logger
    logger.info("settings saved — restarting \(label) to apply the new config")
    Task.detached {
        try? await Task.sleep(nanoseconds: 1_200_000_000)  // let the save response flush first
        #if canImport(Darwin)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "launchctl kickstart -k gui/$(id -u)/\(label)"]
        try? p.run()
        #endif
    }
}
