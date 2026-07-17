import Foundation
import Vapor

public func configure(_ app: Application) async throws {
    // Pull app-entered settings from the sidecar's secrets store first (without overriding
    // real env vars), so AppConfig + watchers can be configured from the Settings page.
    if app.environment != .testing { await SecretsHydrator.hydrate() }
    app.dashboardConfig = AppConfig.load(app.environment)
    app.templates = try Templates.load()
    _ = app.providerCache // eagerly initialize the shared actor

    // Bound upstream calls so a hung provider can't stall the manifest. The read window
    // must comfortably exceed the BigQuery sidecar's own query timeouts (/afm and
    // /bqtables run queries up to ~40s) — an 8s read tripped on slow/cold BQ scans and
    // surfaced the provider stub ("sidecar unavailable"). Connect stays short.
    app.http.client.configuration.timeout = .init(connect: .seconds(5), read: .seconds(45))

    // Bind locally; Tailscale Serve terminates HTTPS and proxies to this port.
    app.http.server.configuration.hostname = Environment.get("HOST") ?? "127.0.0.1"
    if let port = Environment.get("PORT").flatMap(Int.init) {
        app.http.server.configuration.port = port
    }

    // Serve the built web bundle (Web/dist). Missing files fall through to the
    // router, so /dashboard and /healthz are unaffected.
    if var dist = app.dashboardConfig.webDist {
        if !dist.hasSuffix("/") { dist += "/" }
        if FileManager.default.fileExists(atPath: dist) {
            app.middleware.use(FileMiddleware(publicDirectory: dist, defaultFile: "index.html"))
            app.logger.info("serving web bundle from \(dist)")
        } else {
            app.logger.warning("WEB_DIST set but not found at \(dist); skipping static serving")
        }
    }

    // Device-token store is always available so the app can register for push even before
    // APNs keys are set; the watcher only pushes once APNS_* is configured.
    let tokenPath = Environment.get("APNS_TOKEN_STORE").flatMap { $0.isEmpty ? nil : $0 }
        ?? (app.directory.workingDirectory + "push_tokens.json")
    app.deviceTokens = DeviceTokenStore(path: tokenPath)

    try routes(app)

    // Keep the provider cache warm on a schedule so the data is always the latest the
    // instant a client opens (even briefly before going offline) — not fetched lazily on
    // first request. Skipped under tests so they don't make live upstream calls.
    if app.environment != .testing && app.dashboardConfig.warmEnabled {
        Warmer.start(on: app)
    }

    // APNs push (log-failure alerts + Live Activity updates) — enabled only when an APNs
    // auth key is configured. Skipped under tests.
    if app.environment != .testing, let apnsCfg = APNsConfig.load(), let apns = APNsClient(apnsCfg) {
        app.apns = apns
        LogFailureWatcher.start(on: app, config: apnsCfg)
        DeployFailureWatcher.start(on: app, config: apnsCfg)
        LiveActivityUpdater.start(on: app)   // no-op unless DASHBOARD_LIVE_ACTIVITY_INTERVAL > 0
        RepoWatcher.start(on: app)           // no-op unless DASHBOARD_REPO_WATCH_INTERVAL > 0
        SilentRefreshPush.start(on: app)     // wakes the app to refresh its cache (default 30m)
        app.logger.info("APNs push enabled (\(apnsCfg.useSandbox ? "sandbox" : "production"))")
    }
}
