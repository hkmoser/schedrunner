import Foundation
import Vapor

/// Sends a SILENT (content-available) background push to every registered device on a timer.
/// The push carries no alert/sound — iOS wakes the app in the background, which runs its
/// `BackgroundRefresh.refreshAll()` to re-pull the offline cache, so the app is current the
/// moment you open it even after being closed. This is the strongest practical "fresh on open"
/// mechanism within iOS limits (BGAppRefreshTask is opportunistic; a content-available push is
/// a much stronger, data-driven wake — though iOS still budgets/coalesces delivery).
///
/// Cheap server-side: it only sends a push (no BigQuery). The app's resulting refresh hits the
/// already-warm provider cache and skips BigQuery pages, so this never drives upstream cost.
/// DASHBOARD_SILENT_PUSH_INTERVAL controls the cadence (default 1800s; 0 = OFF).
enum SilentRefreshPush {
    private struct TaskKey: StorageKey { typealias Value = Task<Void, Never> }
    private struct Lifecycle: LifecycleHandler {
        func shutdown(_ application: Application) { application.storage[TaskKey.self]?.cancel() }
    }

    static func start(on app: Application) {
        let interval = app.__APP_NAME_LOWER__Config.silentPushInterval
        guard interval > 0 else { return }
        app.logger.info("silent background-refresh push on: every \(Int(interval))s")
        let task = Task {
            // Wait a cycle before the first send: a just-(re)started server has nothing new to
            // announce, and the warmer is still filling the cache.
            try? await Task.sleep(nanoseconds: UInt64(max(60, interval) * 1_000_000_000))
            while !Task.isCancelled {
                await pushWake(app: app)
                try? await Task.sleep(nanoseconds: UInt64(max(60, interval) * 1_000_000_000))
            }
        }
        app.storage[TaskKey.self] = task
        app.lifecycle.use(Lifecycle())
    }

    private static func pushWake(app: Application) async {
        guard let apns = app.apns,
              let tokens = await app.deviceTokens?.alertTokens(), !tokens.isEmpty else { return }
        // content-available:1 with NO alert/sound = a silent background wake. apns-push-type is
        // sent as "background" and priority 5 (required by APNs for background pushes), both
        // handled by APNsClient.send for the .background type.
        let payload: [String: Any] = ["aps": ["content-available": 1]]
        for token in tokens {
            let code = await apns.send(payload, to: token, type: .background, client: app.client, logger: app.logger)
            if code == 410 || code == 400 { await app.deviceTokens?.removeAlert(token) }
        }
    }
}
