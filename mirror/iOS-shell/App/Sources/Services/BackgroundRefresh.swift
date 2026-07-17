import Foundation
import BackgroundTasks
import UserNotifications
import WidgetKit

/// Schedules a periodic background refresh of the manifest cache. The identifier
/// must match BGTaskSchedulerPermittedIdentifiers in Info.plist. (Guaranteed
/// freshness via silent push is a documented post-MVP upgrade.)
enum BackgroundRefresh {
    static let taskID = "com.joemoser.dashboard.refresh"

    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: taskID, using: nil) { task in
            guard let task = task as? BGAppRefreshTask else { return }
            handle(task)
        }
    }

    static func schedule() {
        let request = BGAppRefreshTaskRequest(identifier: taskID)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 2 * 60 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(_ task: BGAppRefreshTask) {
        schedule() // chain the next one
        let work = Task {
            let ok = await refreshAll()
            task.setTaskCompleted(success: ok)
        }
        task.expirationHandler = { work.cancel() }
    }

    /// Refresh the WHOLE offline cache (dashboard + every cheap nav page), not just home, so an
    /// offline open right after a granted background window has the latest of everything — the
    /// background equivalent of the foreground `prefetchNavPages()`. BigQuery-billed pages
    /// (Activity, BQ Tables) are skipped so an opportunistic background timer never re-bills
    /// BigQuery — same policy as the server warmer and the web's light re-warm. Returns false
    /// only if the dashboard itself couldn't be fetched (no connectivity), so iOS can reschedule.
    static func refreshAll() async -> Bool {
        let client = DashboardClient()
        let cache = CacheStore()
        guard let dashboard = try? await client.fetch() else { return false }
        cache.save("/dashboard", dashboard)
        var paths: [String] = []
        func walk(_ items: [NavItem]?) {
            for it in items ?? [] {
                if let p = it.path, p != "/dashboard", !isExpensive(p) { paths.append(p) }
                walk(it.children)
            }
        }
        walk(dashboard.nav)
        for p in paths {
            if Task.isCancelled { break }
            if let m = try? await client.fetchPage(p) { cache.save(p, m) }
        }
        await updateBadge(client: client)
        // Push fresh data to the Home Screen widgets whenever the app refreshes (silent push /
        // BGTask), so the Activity widget keeps up rather than waiting on its own timeline.
        WidgetCenter.shared.reloadAllTimelines()
        return true
    }

    /// Reflect an available iOS-Shell update as an app-icon badge, even while the app is closed
    /// (this runs from the BGTask + silent push). Set to 1 when the server's published build is
    /// newer than this binary, else cleared — mirrors DashboardViewModel.checkForUpdate.
    private static func updateBadge(client: DashboardClient) async {
        guard let info = await client.appVersionInfo() else { return }
        let installed = Int(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "0") ?? 0
        let server = Int(info.build) ?? 0
        try? await UNUserNotificationCenter.current().setBadgeCount(server > installed ? 1 : 0)
    }

    /// BigQuery-billed pages a background timer must not hit (re-billing risk). The dashboard,
    /// Balances, Budget, Smart Home, Messages, Logs, Repos, Docs, Config, etc. are all cheap.
    private static func isExpensive(_ path: String) -> Bool {
        path.hasPrefix("/screen/afm") || path.hasPrefix("/screen/bqtables")
    }
}
