import Foundation
import ActivityKit

/// Starts/maintains the "latest activity status" Live Activity from the AFM current-state and
/// forwards its push-to-update token to the server, so the server can push live updates while
/// the app is backgrounded/closed. While the app is foregrounded it updates the activity
/// directly from data it already fetched.
@MainActor
final class ActivityManager {
    static let shared = ActivityManager()
    private let client = DashboardClient()
    private var current: Activity<DashboardActivityAttributes>?

    private init() {
        // Re-adopt an already-running activity (e.g. from a previous launch) so we update it
        // instead of spawning a duplicate, and resume forwarding its push token.
        if let existing = Activity<DashboardActivityAttributes>.activities.first {
            current = existing
            observeToken(of: existing)
        }
    }

    var isRunning: Bool { current != nil }

    /// Whether the OS currently permits Live Activities (Settings → <app> → Live Activities).
    var areActivitiesEnabled: Bool { ActivityAuthorizationInfo().areActivitiesEnabled }

    /// Outcome of a start attempt, so the caller can tell the user WHY nothing appeared instead
    /// of failing silently (the classic "Live Activities never work for me").
    enum StartResult: Equatable {
        case started
        case alreadyRunning
        case notEnabled          // Settings → <app> → Live Activities is off (or restricted)
        case failed(String)      // Activity.request threw
    }

    /// Start the Live Activity on demand (the in-app trigger). If one is already running, just
    /// update it. Returns the outcome so the UI can surface a reason on failure.
    @discardableResult
    func start(_ state: DashboardActivityAttributes.ContentState, title: String = "Activity") -> StartResult {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return .notEnabled }
        guard current == nil else { update(state); return .alreadyRunning }
        do {
            let activity = try Activity.request(
                attributes: DashboardActivityAttributes(title: title),
                content: ActivityContent(state: state, staleDate: nil),
                pushType: .token
            )
            current = activity
            observeToken(of: activity)
            return .started
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    /// Update the running activity (no-op if not running — we no longer auto-start).
    func update(_ state: DashboardActivityAttributes.ContentState) {
        guard let current else { return }
        Task { await current.update(ActivityContent(state: state, staleDate: nil)) }
    }

    /// End + dismiss the activity immediately (the auto-dismiss / manual stop).
    func end() {
        guard let current else { return }
        self.current = nil
        Task { await current.end(nil, dismissalPolicy: .immediate) }
    }

    /// Stream the per-activity push token to the server (it changes/rotates over time).
    private func observeToken(of activity: Activity<DashboardActivityAttributes>) {
        Task {
            for await tokenData in activity.pushTokenUpdates {
                let hex = tokenData.map { String(format: "%02x", $0) }.joined()
                await client.registerPush(liveActivityToken: hex)
            }
        }
    }
}
