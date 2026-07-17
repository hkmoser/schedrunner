import Foundation
import UIKit
import UserNotifications

/// Requests notification permission, registers for APNs, forwards the device token to the
/// server (POST /register_push), and handles notification interactions. Repo-merge alerts are
/// actionable: a TAP deep-links to the Repos screen; a LONG-PRESS exposes a "Merge now" action
/// (the REPO_MERGE category) that ships the PR/branch directly via its `shipPath`.
@MainActor
final class PushManager: NSObject {
    static let shared = PushManager()
    private let client = __APP_NAME__Client()
    /// Set by the view model so a notification tap can drive navigation.
    weak var viewModel: __APP_NAME__ViewModel?

    /// Category id + action id for the repo-merge notification (must match the server payload's
    /// `aps.category`).
    private static let repoMergeCategory = "REPO_MERGE"
    private static let mergeAction = "MERGE"

    /// Set up the notification delegate + actionable categories. Call once at launch so even a
    /// cold-launch tap (app opened FROM the notification) is routed.
    func configure() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        // The "Merge now" action requires the device to be unlocked (Face ID / passcode at the OS
        // level) before it fires — a merge shouldn't be triggerable from a locked screen. No
        // .foreground: it merges DIRECTLY in the background (a local notification confirms the
        // result), so a long-press → Merge never has to open the app.
        let merge = UNNotificationAction(identifier: Self.mergeAction, title: "Merge now",
                                         options: [.authenticationRequired])
        let category = UNNotificationCategory(identifier: Self.repoMergeCategory, actions: [merge],
                                              intentIdentifiers: [], options: [])
        center.setNotificationCategories([category])
    }

    /// Ask once for permission; on grant, register for remote notifications. The device
    /// token arrives asynchronously in AppDelegate → didRegister(deviceToken:).
    func requestAuthorizationAndRegister() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else { return }
            Task { @MainActor in UIApplication.shared.registerForRemoteNotifications() }
        }
    }

    /// APNs delivered a device token — hex-encode it and send it to the server.
    func didRegister(deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { await client.registerPush(token: hex) }
    }

    /// Ship a PR/branch directly from the notification action (create-PR-if-needed → merge →
    /// delete branch happens server-side). Confirms with a local notification so a background
    /// merge still gives feedback, and refreshes the Repos screen if it's showing.
    func mergeDirectly(_ shipPath: String, label: String) async {
        do {
            let data = try await client.post(shipPath, items: [])
            // The ship reports its real outcome via statusDirection ("down" = failed) on a 200.
            var ok = true
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let dir = obj["statusDirection"] as? String { ok = dir != "down" }
            notifyLocal(title: ok ? "Merged ✓" : "Merge failed", body: label)
        } catch {
            notifyLocal(title: "Merge failed", body: label)
        }
        viewModel?.refreshIfShowing("/screen/repos")
    }

    /// Post an immediate local notification (used to confirm a background merge action).
    private func notifyLocal(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let req = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }
}

extension PushManager: UNUserNotificationCenterDelegate {
    /// Show repo/log alerts as a banner even while the app is in the foreground.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    /// A notification was tapped or one of its actions chosen. TAP → deep-link to the Repos
    /// screen; "Merge now" → ship the PR/branch directly.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse,
                                            withCompletionHandler completionHandler: @escaping () -> Void) {
        let content = response.notification.request.content
        let shipPath = content.userInfo["shipPath"] as? String
        let navPath = (content.userInfo["navPath"] as? String) ?? "/screen/repos"
        let label = content.body
        let isMerge = response.actionIdentifier == PushManager.mergeAction
        Task { @MainActor in
            if isMerge, let shipPath {
                await PushManager.shared.mergeDirectly(shipPath, label: label)
            } else {
                PushManager.shared.viewModel?.handleDeepLink(navPath)
            }
            completionHandler()
        }
    }
}
