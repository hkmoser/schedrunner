import Foundation
import Vapor

/// Polls the sidecar `/repos` and sends an APNs alert when a NEW open PR or unmerged branch
/// appears on GitHub. Edge-triggered + seeds a baseline on (re)start so it doesn't alert for
/// everything already open. OPT-IN (DASHBOARD_REPO_WATCH_INTERVAL > 0): each poll makes GitHub
/// API calls (via the sidecar), so it's off by default.
enum RepoWatcher {
    private struct TaskKey: StorageKey { typealias Value = Task<Void, Never> }
    private struct Lifecycle: LifecycleHandler {
        func shutdown(_ application: Application) { application.storage[TaskKey.self]?.cancel() }
    }

    /// One open PR / unmerged branch: the human label plus the ship path (POST /repos_pr?…) that
    /// creates-PR-if-needed → merges → deletes the branch. The ship path lets the notification
    /// carry a "Merge now" action that ships directly, and a tap deep-link to the Repos screen.
    private struct Item { let id: String; let label: String; let shipPath: String }

    static func start(on app: Application) {
        let interval = app.__APP_NAME_LOWER__Config.repoWatchInterval
        guard interval > 0 else { return }
        app.logger.info("repo watcher on: every \(Int(interval))s (PRs/branches via GitHub)")
        let task = Task {
            while !Task.isCancelled {
                let items = await currentOpen(app: app)
                let ids = Set(items.map(\.id))
                // Skip empty reads (no open items OR a transient GitHub blip) so a hiccup never
                // wipes the baseline and then re-floods on the next successful poll.
                if !ids.isEmpty {
                    // PERSISTENT dedup: a given PR/branch fires EXACTLY ONCE — across restarts
                    // (settings-save auto-restart, deploys) AND across the GitHub API's flaky,
                    // time-budgeted branch enumeration. nil = never seeded → seed silently.
                    let prior = loadSeen(app)
                    if let prior {
                        let newly = items.filter { !prior.contains($0.id) }.sorted { $0.label < $1.label }
                        // Still advance the baseline below even when alerts are off, so toggling
                        // notifications back on doesn't flood with everything that appeared while off.
                        if !newly.isEmpty && app.__APP_NAME_LOWER__Config.notifyEnabled && app.__APP_NAME_LOWER__Config.notifyRepos {
                            await notify(newly, app: app)
                        }
                    }
                    // ACCUMULATE (union) — never prune to this poll's set. The sidecar's
                    // /branches read is bounded by a time budget and returns a PARTIAL list that
                    // varies per poll, so a branch flaps in and out. Pruning to `ids` dropped it
                    // from the baseline and re-alerted when it reappeared — the cause of the
                    // repeated notifications. Remembering every id ever seen fixes it.
                    saveSeen((prior ?? []).union(ids), app)
                }
                try? await Task.sleep(nanoseconds: UInt64(max(60, interval) * 1_000_000_000))
            }
        }
        app.storage[TaskKey.self] = task
        app.lifecycle.use(Lifecycle())
    }

    /// File holding the ids we've already alerted on, so dedup survives restarts. Next to the
    /// push-token store by default; DASHBOARD_REPO_STATE overrides.
    private static func seenURL(_ app: Application) -> URL {
        if let p = Environment.get("DASHBOARD_REPO_STATE"), !p.isEmpty { return URL(fileURLWithPath: p) }
        return URL(fileURLWithPath: app.directory.workingDirectory).appendingPathComponent("repo_notified.json")
    }
    /// Loaded baseline, or nil when the file doesn't exist yet (first run → seed without alerting).
    private static func loadSeen(_ app: Application) -> Set<String>? {
        guard let data = try? Data(contentsOf: seenURL(app)),
              let arr = try? JSONDecoder().decode([String].self, from: data) else { return nil }
        return Set(arr)
    }
    private static func saveSeen(_ ids: Set<String>, _ app: Application) {
        if let data = try? JSONEncoder().encode(Array(ids).sorted()) {
            try? data.write(to: seenURL(app), options: .atomic)
        }
    }

    /// Every open PR + unmerged branch, with its ship path. Empty on error.
    private static func currentOpen(app: Application) async -> [Item] {
        let url = "\(app.__APP_NAME_LOWER__Config.bqSidecarURL)/repos"
        guard let json = try? await app.client.getJSON(url, as: JSONValue.self),
              case .object(let obj) = json, case .array(let repos)? = obj["repos"] else { return [] }
        var out: [Item] = []
        for r in repos {
            guard case .object(let ro) = r else { continue }
            let repo = { if case .string(let n)? = ro["name"] { return n }; return "?" }()
            func collect(_ key: String, _ tag: String) {
                guard case .array(let arr)? = ro[key] else { return }
                for e in arr {
                    guard case .object(let eo) = e,
                          case .string(let label)? = eo["labelFormatted"] else { continue }
                    let ship = { if case .string(let s)? = eo["shipPostHref"] { return s }; return "" }()
                    out.append(Item(id: "\(repo)|\(tag)|\(label)", label: "\(repo): \(label)", shipPath: ship))
                }
            }
            collect("prs", "pr")
            collect("branches", "branch")
        }
        return out
    }

    /// One actionable notification PER new item, so each carries unambiguous repo context: a tap
    /// deep-links to the Repos screen (`navPath`), and — when the item is shippable — the
    /// REPO_MERGE category + `shipPath` give a long-press "Merge now" action that ships directly.
    private static func notify(_ entries: [Item], app: Application) async {
        guard let apns = app.apns,
              let tokens = await app.deviceTokens?.alertTokens(), !tokens.isEmpty else { return }
        for item in entries {
            var aps: [String: Any] = [
                "alert": ["title": "New PR / branch", "body": item.label],
                "sound": "default"
            ]
            if !item.shipPath.isEmpty { aps["category"] = "REPO_MERGE" }
            var payload: [String: Any] = ["aps": aps, "navPath": "/screen/repos"]
            if !item.shipPath.isEmpty { payload["shipPath"] = item.shipPath }
            for token in tokens {
                let code = await apns.send(payload, to: token, type: .alert, client: app.client, logger: app.logger)
                if code == 410 || code == 400 { await app.deviceTokens?.removeAlert(token) }
            }
        }
        app.logger.info("pushed \(entries.count) repo alert(s) to \(tokens.count) device(s)")
    }
}
