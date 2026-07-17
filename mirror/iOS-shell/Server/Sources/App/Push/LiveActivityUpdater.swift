import Foundation
import Vapor

/// Pushes Live Activity updates from the AFM current-state so the lock-screen status stays
/// live even when the app is closed. OPT-IN (DASHBOARD_LIVE_ACTIVITY_INTERVAL > 0): each poll
/// hits the sidecar /afm (BigQuery-backed), so it's off by default to avoid surprise cost.
/// Only pushes when the content actually changed, to avoid needless APNs traffic.
enum LiveActivityUpdater {
    private struct TaskKey: StorageKey { typealias Value = Task<Void, Never> }
    private struct Lifecycle: LifecycleHandler {
        func shutdown(_ application: Application) { application.storage[TaskKey.self]?.cancel() }
    }

    /// AFM current-state pushed to the Live Activity — mirrors the widget's data. Equatable so we
    /// only push on change.
    private struct AFMState: Equatable {
        let status, place, meta, category, elapsed, clockRange: String
        let startedAt: Int
    }

    static func start(on app: Application) {
        let interval = app.dashboardConfig.liveActivityInterval
        guard interval > 0 else { return }
        let dismissOn = app.dashboardConfig.liveActivityDismissOn.lowercased()
        app.logger.info("live-activity updater on: every \(Int(interval))s (auto-dismiss on '\(dismissOn)')")
        let task = Task {
            var last: AFMState?
            var lastCategory: String?
            while !Task.isCancelled {
                // Only poll (BigQuery /afm) while a Live Activity is actually running — i.e. a
                // push token is registered. When none, this is a free no-op, so enabling the
                // updater never bills BigQuery unless the user has an active Live Activity.
                let tokens = await app.deviceTokens?.liveActivityTokens() ?? []
                if tokens.isEmpty {
                    last = nil; lastCategory = nil   // reset so the next activity seeds fresh
                    try? await Task.sleep(nanoseconds: UInt64(max(30, interval) * 1_000_000_000))
                    continue
                }
                if let state = await currentState(app: app) {
                    let cat = state.category.lowercased()
                    // Auto-dismiss: end the moment the category CHANGES into the dismiss target
                    // (default "stopped" = arrival) — a transition, not the steady state, so
                    // triggering it while already stopped doesn't insta-dismiss.
                    if let lc = lastCategory, lc != cat, !dismissOn.isEmpty, cat == dismissOn {
                        await send(state, event: "end", app: app)
                        last = nil
                    } else if state != last {
                        await send(state, event: "update", app: app)
                        last = state
                    }
                    lastCategory = cat
                }
                try? await Task.sleep(nanoseconds: UInt64(max(30, interval) * 1_000_000_000))
            }
        }
        app.storage[TaskKey.self] = task
        app.lifecycle.use(Lifecycle())
    }

    /// The current-state from the sidecar /afm JSON (currentState block). Nil on error/empty.
    private static func currentState(app: Application) async -> AFMState? {
        let url = "\(app.dashboardConfig.bqSidecarURL)/afm"
        guard let json = try? await app.client.getJSON(url, as: JSONValue.self),
              case .object(let obj) = json,
              case .object(let cur)? = obj["currentState"] else { return nil }
        func s(_ key: String) -> String { if case .string(let v)? = cur[key] { return v }; return "" }
        func i(_ key: String) -> Int {
            switch cur[key] { case .int(let n)?: return n; case .double(let d)?: return Int(d); default: return 0 }
        }
        let st = AFMState(status: s("statusFormatted"), place: s("placeFormatted"), meta: s("metaFormatted"),
                          category: s("category"), elapsed: s("elapsedFormatted"),
                          clockRange: s("clockRangeFormatted"), startedAt: i("startedAtEpoch"))
        return (st.status.isEmpty && st.place.isEmpty) ? nil : st
    }

    /// Send an update or end event to every registered Live Activity token. The content-state
    /// keys match DashboardActivityAttributes.ContentState exactly.
    private static func send(_ st: AFMState, event: String, app: Application) async {
        guard let apns = app.apns,
              let tokens = await app.deviceTokens?.liveActivityTokens(), !tokens.isEmpty else { return }
        let contentState: [String: Any] = [
            "status": st.status, "place": st.place, "meta": st.meta,
            "category": st.category, "elapsed": st.elapsed, "clockRange": st.clockRange,
            "startedAt": st.startedAt,
        ]
        var aps: [String: Any] = ["timestamp": Int(Date().timeIntervalSince1970),
                                  "event": event, "content-state": contentState]
        if event == "end" { aps["dismissal-date"] = Int(Date().timeIntervalSince1970) }
        let payload: [String: Any] = ["aps": aps]
        for token in tokens {
            let code = await apns.send(payload, to: token, type: .liveactivity, client: app.client, logger: app.logger)
            if code == 410 || code == 400 { await app.deviceTokens?.removeLiveActivity(token) }
        }
    }
}
