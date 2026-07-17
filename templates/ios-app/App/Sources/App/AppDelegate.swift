import UIKit

/// Bridges the UIKit app-delegate callbacks SwiftUI doesn't expose — specifically the APNs
/// device-token registration result — into PushManager. Wired via @UIApplicationDelegateAdaptor.
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // Set the notification delegate + actionable categories early so even a cold-launch tap
        // (app opened FROM a repo-merge notification) is routed to the right screen / action.
        PushManager.shared.configure()
        return true
    }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task { @MainActor in PushManager.shared.didRegister(deviceToken: deviceToken) }
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // Best-effort: no token this launch (e.g. no network / not signed in). We retry on
        // the next launch when PushManager re-registers.
    }

    /// Silent (content-available) push from the server's SilentRefreshPush timer: iOS wakes us
    /// in the background here. Re-pull the whole offline cache so the app is current the moment
    /// it's next opened — even though it was closed. Report .newData/.noData so iOS keeps
    /// granting background time. Writes straight to the on-disk CacheStore (no UI needed).
    func application(_ application: UIApplication,
                     didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                     fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void) {
        Task {
            let ok = await BackgroundRefresh.refreshAll()
            completionHandler(ok ? .newData : .noData)
        }
    }
}
