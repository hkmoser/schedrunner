import Foundation
import Vapor

/// Watches build/.auto-deploy-status on a 60-second timer and sends an APNs alert when the
/// post-pull hook newly transitions to FAILED — so a deploy build failure reaches your phone
/// immediately instead of waiting for the next time you open the Deploy screen.
/// Edge-triggered: a currently-failed state only fires once; it re-fires after a recovery + new failure.
/// Gated on `notifyEnabled` + `notifyDeployFailures` (DASHBOARD_NOTIFY_DEPLOY_FAILURES, default on).
enum DeployFailureWatcher {
    private struct TaskKey: StorageKey { typealias Value = Task<Void, Never> }
    private struct Lifecycle: LifecycleHandler {
        func shutdown(_ application: Application) { application.storage[TaskKey.self]?.cancel() }
    }

    static func start(on app: Application, config: APNsConfig) {
        let interval: TimeInterval = 60
        app.logger.info("deploy-failure watcher on: every \(Int(interval))s")
        let task = Task {
            var lastState: String? = nil   // nil = first pass; seed baseline without alerting
            while !Task.isCancelled {
                let current = readStatus(app: app)
                if let prev = lastState {
                    let isFailed = current.hasPrefix("FAILED")
                    let wasFailed = prev.hasPrefix("FAILED")
                    if isFailed && !wasFailed
                        && app.__APP_NAME_LOWER__Config.notifyEnabled
                        && app.__APP_NAME_LOWER__Config.notifyDeployFailures {
                        await notify(current, app: app)
                    }
                }
                lastState = current
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
        app.storage[TaskKey.self] = task
        app.lifecycle.use(Lifecycle())
    }

    private static func readStatus(app: Application) -> String {
        let repo = Environment.get("DASHBOARD_REPO")
            ?? FileManager.default.currentDirectoryPath
        let path = repo + "/build/.auto-deploy-status"
        return (try? String(contentsOfFile: path, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    private static func notify(_ status: String, app: Application) async {
        guard let apns = app.apns,
              let tokens = await app.deviceTokens?.alertTokens(), !tokens.isEmpty else { return }
        let payload: [String: Any] = [
            "aps": [
                "alert": ["title": "Deploy failed", "body": status],
                "sound": "default",
                "interruption-level": "time-sensitive"
            ]
        ]
        for token in tokens {
            let code = await apns.send(payload, to: token, type: .alert, client: app.client, logger: app.logger)
            if code == 410 || code == 400 { await app.deviceTokens?.removeAlert(token) }
        }
        app.logger.info("pushed deploy-failure alert [\(status)] to \(tokens.count) device(s)")
    }
}
