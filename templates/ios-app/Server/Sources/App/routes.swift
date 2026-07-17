import Foundation
import Vapor

struct HealthResponse: Content {
    let status: String
    let schemaVersion: Int
    let build: String
    let lastOk: [String: String]
    let errors: [String: String]
}

struct DeployKick: Content {
    let ok: Bool
    let statusDirection: String
    let statusFormatted: String
    let polling: Bool
}

struct WidgetHealthProviderItem: Content { let name: String; let ok: Bool; let ago: String }
struct WidgetHealthResponse: Content {
    let build: String; let statusFormatted: String; let direction: String
    let providers: [WidgetHealthProviderItem]
}

struct RegisterPushBody: Content {
    let token: String?
    let liveActivityToken: String?
}

public func routes(_ app: Application) throws {
    let welcomeComposer = Composer(providers: [WelcomeProvider()])
    let deployComposer  = Composer(providers: [DeployProvider()])
    let configComposer  = Composer(providers: [ConfigProvider()])
    let settingsComposer = Composer(providers: [SettingsProvider()])

    // Home screen: the SDUI welcome manifest. Replace WelcomeProvider with your own data.
    app.get("__APP_NAME_LOWER__") { req async -> Manifest in
        await welcomeComposer.build(
            client: req.client,
            config: req.application.__APP_NAME_LOWER__Config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger
        )
    }

    // Deploy status — server current/stale + iOS build state, read from build/*.status.
    app.get("screen", "deploy") { req async -> Manifest in
        await deployComposer.build(
            client: req.client,
            config: req.application.__APP_NAME_LOWER__Config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.deploy
        )
    }

    // App preferences (backed by the BigQuery sidecar when enabled).
    app.get("screen", "config") { req async -> Manifest in
        await configComposer.build(
            client: req.client,
            config: req.application.__APP_NAME_LOWER__Config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.config
        )
    }

    // Secrets & API keys (stored in Google Secret Manager via the sidecar when enabled).
    app.get("screen", "settings") { req async -> Manifest in
        await settingsComposer.build(
            client: req.client,
            config: req.application.__APP_NAME_LOWER__Config,
            cache: req.application.providerCache,
            templates: req.application.templates,
            logger: req.logger,
            screen: req.application.templates.settings
        )
    }

    // Proxy config/settings writes to the BigQuery sidecar (no-op if sidecar is down).
    func proxyPost(_ req: Request, to endpoint: String) async throws -> Response {
        let url = "\(req.application.__APP_NAME_LOWER__Config.bqSidecarURL)\(endpoint)"
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
    app.post("settings") { req async throws -> Response in
        let resp = try await proxyPost(req, to: "/settings")
        if (200..<300).contains(resp.status.code) { scheduleSelfRestart(req) }
        return resp
    }

    // "Redeploy now" — runs the post-pull hook (.auto-deploy) from the phone.
    app.post("deploy_kick") { req -> DeployKick in
        let repo = Environment.get("DASHBOARD_REPO")
            ?? Environment.get("WEB_DIST").flatMap { w in
                w.range(of: "/Web/dist", options: .backwards).map { String(w[..<$0.lowerBound]) }
            }
            ?? FileManager.default.currentDirectoryPath
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", "cd \(repo) && nohup bash .auto-deploy >> build/deploy-kick.log 2>&1 &"]
        do { try p.run() } catch {
            return DeployKick(ok: false, statusDirection: "down",
                              statusFormatted: "Couldn't start redeploy: \(error.localizedDescription)",
                              polling: false)
        }
        return DeployKick(ok: true, statusDirection: "up",
                          statusFormatted: "Redeploy started — rebuilding + restarting. Refresh in ~30–60s.",
                          polling: true)
    }

    // Register APNs device token for push (log-failure alerts + silent background refresh).
    app.post("register_push") { req async throws -> HTTPStatus in
        let body = try req.content.decode(RegisterPushBody.self)
        if let t = body.token { await req.application.deviceTokens?.addAlert(t) }
        if let t = body.liveActivityToken { await req.application.deviceTokens?.addLiveActivity(t) }
        return .ok
    }

    // Send a test push to verify APNs end-to-end (key → JWT → Apple → phone).
    app.get("test_push") { req async -> Response in
        let app = req.application
        func json(_ obj: [String: Any]) -> Response {
            let data = (try? JSONSerialization.data(withJSONObject: obj, options: .prettyPrinted)) ?? Data("{}".utf8)
            let r = Response(status: .ok); r.headers.contentType = .json; r.body = .init(data: data); return r
        }
        guard let apns = app.apns else {
            return json(["ok": false,
                         "status": "APNs not configured.",
                         "diagnosis": APNsConfig.diagnose()])
        }
        guard let tokens = await app.deviceTokens?.alertTokens(), !tokens.isEmpty else {
            return json(["ok": false, "status": "No registered device tokens yet. Open the app, unlock, allow notifications."])
        }
        let payload: [String: Any] = ["aps": ["alert": ["title": "__APP_NAME__", "body": "Test push ✅"], "sound": "default"]]
        var results: [[String: Any]] = []
        for t in tokens {
            let code = await apns.send(payload, to: t, type: .alert, client: app.client, logger: req.logger)
            results.append(["token": String(t.prefix(8)) + "…", "apnsStatus": code])
        }
        return json(["ok": true, "status": "sent to \(tokens.count) device(s)", "results": results])
    }

    // Widget: server health summary (build SHA + per-provider last-ok / error state).
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

    // Liveness + last successful refresh per provider.
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

/// Restart the launchd service so a just-saved setting takes effect immediately.
func scheduleSelfRestart(_ req: Request) {
    guard Environment.get("DASHBOARD_SELF_RESTART") != "0" else { return }
    let label = Environment.get("DASHBOARD_SERVICE_LABEL") ?? "__BUNDLE_ID__.server"
    let logger = req.logger
    logger.info("settings saved — restarting \(label) to apply new config")
    Task.detached {
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        #if canImport(Darwin)
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "launchctl kickstart -k gui/$(id -u)/\(label)"]
        try? p.run()
        #endif
    }
}
