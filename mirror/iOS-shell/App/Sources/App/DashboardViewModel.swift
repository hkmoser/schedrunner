import SwiftUI
import UserNotifications

@MainActor
final class DashboardViewModel: ObservableObject {
    @Published var manifest: Manifest?            // the main dashboard
    @Published var currentPath: String = "/dashboard"
    @Published var pages: [String: Manifest] = [:] // fetched tab pages by path
    @Published var localCalendar: JSONValue?
    @Published var compat: SchemaCompat.Result = .ok
    @Published var isOffline = false
    @Published var mode: AccessMode?              // nil = locked (show passcode)
    /// When each page's data was last cached, for freshness display + offline awareness.
    @Published private(set) var pageFetchedAt: [String: Date] = [:]

    private let cache = CacheStore()
    private let client = DashboardClient()
    private let calendar = CalendarProvider()
    /// Prefetch the nav pages once per launch so tabs open instantly / work offline.
    private var didPrefetch = false
    /// Tracks the last-seen AFM category so Live Activity is only auto-dismissed on a
    /// Moving→Stopped transition, not every time "stopped" appears in the data.
    private var lastActivityCategory: String?
    /// Live-tick: bumped on a timer so freshness ("Updated Nm ago") advances on its own,
    /// re-deriving scope.meta without a refetch — matching the web's ticking clock.
    @Published private var clockTick = 0
    private var clock: Timer?

    deinit { clock?.invalidate() }

    /// All-even passcode → a single decoy dashboard with dummy content, no menu.
    var restricted: Bool { mode == .decoy }

    /// Title of the active page, resolved from the (possibly nested) nav tree.
    var activeTitle: String {
        func find(_ items: [NavItem]?) -> String? {
            for it in items ?? [] {
                if it.path == currentPath { return it.title }
                if let t = find(it.children) { return t }
            }
            return nil
        }
        return find(current?.nav) ?? "Dashboard"
    }

    /// The passcode gate resolved: enter the app for the chosen mode.
    func unlock(_ m: AccessMode) {
        mode = m
        currentPath = "/dashboard"
        if m == .decoy {
            // Render dummy content locally; never touch the server.
            compat = .ok
            manifest = Manifest.decoy()
        } else {
            Task { await load(currentPath) }
            // Full access only: ask for notifications + register for APNs (never in decoy).
            PushManager.shared.requestAuthorizationAndRegister()
            checkForUpdate()
            // Apply a deep link that arrived while locked (e.g. a notification tapped at launch).
            if let p = pendingDeepLink { pendingDeepLink = nil; selectTab(p) }
        }
    }

    /// Drive navigation from a notification tap. If still locked, buffer it until full unlock.
    func handleDeepLink(_ path: String) {
        guard mode == .full else { pendingDeepLink = path; return }
        selectTab(path)
    }

    /// Re-fetch a page only if it's the one on screen — used after a notification merge action
    /// so the Repos screen reflects the result without forcing a navigation.
    func refreshIfShowing(_ path: String) {
        guard currentPath == path else { return }
        Task { await load(path) }
    }

    /// Require the passcode again (called when the app goes to the background).
    func lockApp() { mode = nil }

    /// A deep link (e.g. from a notification tap) waiting for a full unlock to be applied.
    @Published private var pendingDeepLink: String?

    func bootstrap() {
        // Route notification taps (deep-link to a screen / merge action) through this VM.
        PushManager.shared.viewModel = self
        // Rehydrate every cached page (dashboard + visited/prefetched tabs) so the whole
        // app is browsable offline immediately, before any network call returns.
        for cached in cache.loadAll() {
            pageFetchedAt[cached.path] = cached.fetchedAt
            if cached.path == "/dashboard" { apply(cached.manifest) } else { pages[cached.path] = cached.manifest }
        }
        Task { localCalendar = await calendar.load() }
        startClock()
    }

    /// Advance freshness labels every 30s without a network refetch (just re-renders, which
    /// re-derives scope.meta.updatedAtFormatted from the unchanged pageFetchedAt).
    private func startClock() {
        clock?.invalidate()
        clock = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.clockTick &+= 1 }
        }
    }

    /// The manifest for the active tab.
    var current: Manifest? {
        currentPath == "/dashboard" ? manifest : pages[currentPath]
    }

    func selectTab(_ path: String) {
        if restricted { return }  // decoy mode is a single local page
        currentPath = path
        Task { await load(path) }
    }

    func navigate(_ target: String) {
        if restricted { return }  // no navigation away in restricted mode
        if target == "back" { currentPath = "/dashboard" } else { selectTab(target) }
    }

    func refresh() async {
        if restricted { return }  // nothing to refresh in decoy mode
        await load(currentPath)
    }

    private func load(_ path: String) async {
        do {
            let m = try await client.fetchPage(path)
            if path == "/dashboard" { apply(m) } else { pages[path] = m }
            cache.save(path, m)                    // persist EVERY page, not just the dashboard
            pageFetchedAt[path] = Date()
            isOffline = false
            // After the dashboard lands, warm the rest of the nav once so tabs are instant.
            if path == "/dashboard" && !didPrefetch { didPrefetch = true; prefetchNavPages() }
            // Keep the "latest activity status" Live Activity in sync when AFM data arrives.
            if path == "/screen/afm" { updateLiveActivity(from: m) }
        } catch {
            isOffline = (current == nil)
        }
    }

    /// Build the Live Activity content-state from the AFM current-state (data.bq.currentState —
    /// AFMProvider's key is "bq"). Mirrors the Activity widget: status/place/meta + category,
    /// elapsed, clock range, and the segment start (so the elapsed ticks live).
    private func liveActivityState(from m: Manifest) -> DashboardActivityAttributes.ContentState {
        let cur = m.data?["bq"]?["currentState"]
        return DashboardActivityAttributes.ContentState(
            status: cur?["statusFormatted"]?.stringValue ?? "",
            place: cur?["placeFormatted"]?.stringValue ?? "",
            meta: cur?["metaFormatted"]?.stringValue ?? "",
            category: cur?["category"]?.stringValue ?? "",
            elapsed: cur?["elapsedFormatted"]?.stringValue ?? "",
            clockRange: cur?["clockRangeFormatted"]?.stringValue ?? "",
            startedAt: cur?["startedAtEpoch"]?.doubleValue ?? 0)
    }

    /// When AFM data lands: UPDATE the Live Activity if it's running, and AUTO-DISMISS it on a
    /// Moving→Stopped transition (arrival). Uses the same transition logic as the server-side
    /// LiveActivityUpdater: only dismisses when the category *changes* to stopped, not every
    /// time "stopped" appears — prevents immediate dismissal when the device is already at rest.
    private func updateLiveActivity(from m: Manifest) {
        guard mode == .full, ActivityManager.shared.isRunning else {
            if !ActivityManager.shared.isRunning { lastActivityCategory = nil }
            return
        }
        let category = (m.data?["bq"]?["currentState"]?["category"]?.stringValue ?? "").lowercased()
        let prev = lastActivityCategory
        lastActivityCategory = category
        if category == "stopped", let prev, prev != "stopped" {
            ActivityManager.shared.end()
            lastActivityCategory = nil
            return
        }
        ActivityManager.shared.update(liveActivityState(from: m))
    }

    /// A short, user-visible result of the last Live Activity trigger — shown as an alert so a
    /// failed start explains itself instead of silently doing nothing.
    @Published var liveActivityMessage: String?

    /// The in-app trigger: start the Live Activity from the latest AFM data (fetching it if the
    /// page hasn't been loaded), or stop it if already running. Always reports an outcome.
    func toggleLiveActivity() {
        guard mode == .full else { return }
        if ActivityManager.shared.isRunning {
            ActivityManager.shared.end()
            lastActivityCategory = nil
            liveActivityMessage = "Live Activity stopped."
            return
        }
        // Fast pre-check: if the OS has Live Activities turned off, say so before anything else.
        guard ActivityManager.shared.areActivitiesEnabled else {
            liveActivityMessage = "Live Activities are turned off for Dashboard. Enable them in "
                + "Settings → Dashboard → Live Activities (and make sure Settings → Face ID & "
                + "Passcode / Screen Time isn’t restricting them), then try again."
            return
        }
        func begin(_ m: Manifest?) {
            guard let m else { liveActivityMessage = "Couldn’t load Activity data — check your connection."; return }
            let state = liveActivityState(from: m)
            if state.status.isEmpty && state.place.isEmpty {
                liveActivityMessage = "No current activity to show yet. Open the Activity page so the "
                    + "latest location loads, then try again."
                return
            }
            switch ActivityManager.shared.start(state) {
            case .started:
                liveActivityMessage = "Live Activity started — check your Lock Screen / Dynamic Island."
            case .alreadyRunning:
                liveActivityMessage = "Live Activity updated."
            case .notEnabled:
                liveActivityMessage = "Live Activities are turned off for Dashboard (Settings → Dashboard)."
            case .failed(let err):
                liveActivityMessage = "Couldn’t start the Live Activity: \(err)"
            }
        }
        if let m = pages["/screen/afm"] {
            begin(m)
        } else {
            Task {
                let m = try? await client.fetchPage("/screen/afm")
                if let m { pages["/screen/afm"] = m }
                begin(m)
            }
        }
    }

    /// Background-fetch the nav destinations (once per launch) and cache them to disk, so
    /// opening any tab is instant and works offline — mirrors the web PWA's prefetch.
    private func prefetchNavPages() {
        guard mode == .full, let nav = current?.nav else { return }
        var paths: [String] = []
        func walk(_ items: [NavItem]?) {
            for it in items ?? [] {
                if let p = it.path, p != "/dashboard" { paths.append(p) }
                walk(it.children)
            }
        }
        walk(nav)
        Task {
            for p in paths where pages[p] == nil {   // skip ones already cached this launch
                if let m = try? await client.fetchPage(p) {
                    pages[p] = m
                    cache.save(p, m)
                    pageFetchedAt[p] = Date()
                }
            }
        }
    }

    /// POST form items and report success, so a SubmitButton can show progress → done/failed.
    /// The button decides when to refresh (after showing "done"), so the result is visible before
    /// the page reloads (e.g. a shipped PR row vanishing).
    @discardableResult
    func submit(_ url: String, _ items: [[String: String]]) async -> Bool {
        guard mode == .full else { return false }
        do {
            let data = try await client.post(url, items: items)
            // A ship result reports the real outcome via statusDirection ("down" = the PR/merge
            // failed) even though the HTTP POST returned 200. Honor it so the button shows Retry,
            // not a false ✓. Other saves carry no such field → success on 200.
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let dir = obj["statusDirection"] as? String {
                return dir != "down"
            }
            return true
        } catch { return false }
    }

    private func apply(_ m: Manifest) {
        compat = SchemaCompat.check(m)
        manifest = m
    }

    var scope: Scope {
        let m = current
        return Scope(
            data: m?.data ?? .object([:]),
            theme: m?.theme ?? .object([:]),
            item: nil,
            local: .object(["calendar": localCalendar ?? .null]),
            // Override meta.updatedAtFormatted with the REAL, client-measured freshness so
            // every "Updated …" binding shows the truth (incl. Offline), like the web.
            meta: .object(["updatedAtFormatted": .string(activeFreshness)])
        )
    }

    // MARK: - Freshness (mirrors web data/cache.ts)

    /// "Updated just now / Updated Nm ago / …", flipped to "Offline · …" when unreachable.
    func freshness(for path: String) -> String {
        guard let at = pageFetchedAt[path] else { return isOffline ? "Offline" : "Updating…" }
        let mins = Int(Date().timeIntervalSince(at) / 60)
        let base: String
        if mins < 1 { base = "Updated just now" }
        else if mins < 60 { base = "Updated \(mins)m ago" }
        else if mins < 1440 { base = "Updated \(mins / 60)h ago" }
        else { base = "Updated \(mins / 1440)d ago" }
        return isOffline ? base.replacingOccurrences(of: "Updated ", with: "Offline · ") : base
    }

    var activeFreshness: String {
        _ = clockTick                       // re-evaluate as the 30s clock advances
        return freshness(for: currentPath)
    }

    // MARK: - Offline cache controls (drawer footer)

    @Published private(set) var prefetching = false
    @Published private(set) var prefetchDone = 0
    @Published private(set) var lastCacheAllAt: Date?

    /// All nav destinations (+ the dashboard), in order, for a full cache sweep.
    private func allNavPaths() -> [String] {
        var paths = ["/dashboard"]
        func walk(_ items: [NavItem]?) {
            for it in items ?? [] {
                if let p = it.path, p != "/dashboard" { paths.append(p) }
                walk(it.children)
            }
        }
        walk(current?.nav)
        return paths
    }

    /// "Cache all pages now": fetch every nav page and persist it, even ones already cached.
    func cacheAllPages() {
        guard mode == .full, !prefetching else { return }
        prefetching = true
        prefetchDone = 0
        let paths = allNavPaths()
        Task {
            for p in paths {
                if let m = try? await client.fetchPage(p) {
                    if p == "/dashboard" { apply(m) } else { pages[p] = m }
                    cache.save(p, m)
                    pageFetchedAt[p] = Date()
                }
                prefetchDone += 1
            }
            prefetching = false
            lastCacheAllAt = Date()
        }
    }

    /// "Hard refresh": clear the on-disk cache and re-pull the dashboard + active page.
    /// Online-only — wiping the cache offline would leave nothing to show.
    func hardRefresh() {
        guard mode == .full, !isOffline else { return }
        cache.clear()
        pages.removeAll()
        pageFetchedAt.removeAll()
        Task {
            await load("/dashboard")
            if currentPath != "/dashboard" { await load(currentPath) }
        }
    }

    /// The drawer's offline-cache status line.
    var cacheStatusText: String {
        if prefetching { return "Caching pages… \(prefetchDone)" }
        let s = cache.stats()
        let size = "\(s.pages) page\(s.pages == 1 ? "" : "s") · ~\(Self.fmtBytes(s.bytes))"
        if let at = lastCacheAllAt {
            let mins = Int(Date().timeIntervalSince(at) / 60)
            let ago = mins < 1 ? "just now" : mins < 60 ? "\(mins)m ago" : "\(mins / 60)h ago"
            return "Cached \(ago) · \(size)"
        }
        return s.pages > 0 ? "\(size) · tap to refresh" : "Not cached yet — tap to cache"
    }

    private static func fmtBytes(_ bytes: Int) -> String {
        if bytes < 1024 { return "\(bytes) B" }
        let kb = Double(bytes) / 1024
        if kb < 1024 { return String(format: "%.0f KB", kb) }
        return String(format: "%.1f MB", kb / 1024)
    }

    /// App + server build line for the drawer footer.
    var buildInfo: String {
        let v = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        let b = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
        return "app \(v) (\(b))"
    }

    // MARK: - In-app update (native binary can't self-update; we one-tap into the installer)

    @Published var updateAvailable = false
    private(set) var updateInstallURL: URL?

    /// Poll the server's published build (/app/version.json); if it's newer than this binary,
    /// surface a banner whose Install button opens the itms-services URL (one tap → installer).
    func checkForUpdate() {
        guard mode == .full else { return }
        Task {
            guard let info = await client.appVersionInfo() else { return }
            let installed = Int(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "0") ?? 0
            if let server = Int(info.build), server > installed, let url = URL(string: info.installURL) {
                updateInstallURL = url
                updateAvailable = true
                // Nudge with an app-icon badge so a waiting update is visible without opening.
                try? await UNUserNotificationCenter.current().setBadgeCount(1)
            } else {
                updateAvailable = false
                try? await UNUserNotificationCenter.current().setBadgeCount(0)
            }
        }
    }
}
