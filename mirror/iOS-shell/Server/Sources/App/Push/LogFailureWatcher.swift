import Foundation
import Vapor

/// Polls the sidecar's `/logs` on a timer and sends an APNs alert when a service NEWLY flips
/// to failed (statusKey == "failed"). Edge-triggered: an already-failing service doesn't
/// re-alert until it recovers and fails again, and the first pass after (re)start just seeds
/// the baseline so a restart doesn't blast a push for every already-failing service.
enum LogFailureWatcher {
    private struct WatcherTaskKey: StorageKey { typealias Value = Task<Void, Never> }
    private struct WatcherLifecycle: LifecycleHandler {
        func shutdown(_ application: Application) { application.storage[WatcherTaskKey.self]?.cancel() }
    }

    static func start(on app: Application, config: APNsConfig) {
        let interval = max(30, app.dashboardConfig.logWatchInterval)
        app.logger.info("log-failure watcher on: every \(Int(interval))s")
        let task = Task {
            var known: Set<String> = []   // services currently failed (already notified)
            var seeded = false
            while !Task.isCancelled {
                let failed = await currentFailures(app: app)
                if !seeded {
                    known = failed; seeded = true        // baseline; don't notify on the first read
                } else {
                    let newly = failed.subtracting(known)
                    // Gate on the master + per-type notification switches. The baseline still
                    // advances (known = failed) so flipping alerts back on doesn't re-fire for
                    // services that were already failing while they were off.
                    if !newly.isEmpty && app.dashboardConfig.notifyEnabled && app.dashboardConfig.notifyLogFailures {
                        await notify(Array(newly).sorted(), app: app)
                    }
                    known = failed
                }
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
        app.storage[WatcherTaskKey.self] = task
        app.lifecycle.use(WatcherLifecycle())
    }

    /// Names of services whose latest log status is "failed", read from the sidecar /logs JSON
    /// (`files[].statusKey`). Returns empty on any error so a transient blip never alerts.
    private static func currentFailures(app: Application) async -> Set<String> {
        let url = "\(app.dashboardConfig.bqSidecarURL)/logs"
        guard let json = try? await app.client.getJSON(url, as: JSONValue.self),
              case .object(let obj) = json,
              case .array(let files)? = obj["files"] else { return [] }
        var failed: Set<String> = []
        for f in files {
            guard case .object(let fo) = f,
                  case .string(let status)? = fo["statusKey"], status.lowercased() == "failed",
                  case .string(let name)? = fo["name"] else { continue }
            failed.insert(name)
        }
        return failed
    }

    private static func notify(_ services: [String], app: Application) async {
        guard let apns = app.apns,
              let tokens = await app.deviceTokens?.alertTokens(), !tokens.isEmpty else { return }
        let title = services.count == 1 ? "Log failure" : "\(services.count) log failures"
        let payload: [String: Any] = [
            "aps": [
                "alert": ["title": title, "body": services.joined(separator: ", ")],
                "sound": "default",
                "interruption-level": "time-sensitive"
            ]
        ]
        for token in tokens {
            let code = await apns.send(payload, to: token, type: .alert, client: app.client, logger: app.logger)
            // 410 Gone / 400 BadDeviceToken → the install is gone; drop it so we stop trying.
            if code == 410 || code == 400 { await app.deviceTokens?.removeAlert(token) }
        }
        app.logger.info("pushed log-failure alert for [\(services.joined(separator: ", "))] to \(tokens.count) device(s)")
    }
}
