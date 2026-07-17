import Foundation
import Vapor

/// Runtime configuration sourced entirely from environment variables (see
/// Deploy/.env.example). Secrets never live in the repo. Missing data keys
/// degrade gracefully to stub data so the server always returns a valid manifest.
public struct AppConfig: Sendable {
    /// Default "Home" weather location when DASHBOARD_LAT/LON aren't set — ZIP 20007
    /// (Washington, DC). Used by the weather card's Home toggle so it doesn't IP-geolocate.
    public static let homeDefault: (lat: Double, lon: Double, name: String) =
        (38.9183, -77.0792, "Washington, DC 20007")

    /// nil = auto-detect from the server's network (IP geolocation).
    public var latitude: Double?
    public var longitude: Double?
    public var locationName: String?
    /// Defaults to the Mac mini's system timezone ("my current timezone").
    public var timezone: String
    /// True when the timezone was pinned (env or client-supplied) and must not be
    /// overridden by IP geolocation.
    public var timezonePinned: Bool
    public var twelveDataKey: String?
    public var fredKey: String?
    public var stockSymbols: [String]
    /// The user's existing mortgage rate, for comparison (percent, e.g. 6.375).
    public var userMortgageRate: Double
    /// Rental property tracked by the Zestimate card.
    public var propertyAddress: String
    /// Manual Zestimate fallback (USD) when no live source is configured.
    public var propertyZestimate: Double?
    /// Zillow listing URL the property card opens when tapped.
    public var propertyURL: String?
    /// Optional RapidAPI key enabling a live Zillow lookup.
    public var rapidAPIKey: String?
    /// Base URL of the local BigQuery sidecar.
    public var bqSidecarURL: String
    /// Path to the built web bundle (Web/dist) to serve as static files.
    public var webDist: String?
    /// Per-request: the folder/file (relative to /Private) the Docs page is viewing.
    public var docsPath: String?
    /// Per-request: the BigQuery dataset the BQ Tables page is drilling into (else nil = list datasets).
    public var bqDataset: String?
    /// Per-request: the BigQuery table the BQ Tables page is drilling into (else nil).
    public var bqTable: String?
    /// Per-request: the BQ Tables table view — "columns" (schema) or "preview" (100 rows).
    public var bqView: String?
    /// Per-request: the Smart Home event-type filter (tier 2; comma-separated keys). nil =
    /// use the useful defaults; "" = none selected. Distinct nil vs "" is preserved.
    public var smartHomeTypes: String?
    /// Per-request: the Smart Home SOURCE filter (tier 1; comma-separated source keys).
    /// nil = all sources; "" = none selected. Distinct nil vs "" is preserved.
    public var smartHomeSources: String?
    /// Per-request: weather location mode — true = "Home" (the server/IP/env location),
    /// false = the device's current coordinates. Toggled on the weather card (?loc=home).
    public var weatherAtHome: Bool
    /// Per-request: the Activity time range — "today" (default), "yesterday", or "week".
    public var afmRange: String?
    /// Per-request: the stop coordinates/name the "Label location" screen pre-fills.
    public var knownLat: String?
    public var knownLon: String?
    public var knownPlace: String?
    /// Per-request: the Logs status filter (comma-separated keys) and the log file the
    /// 24h detail view is showing.
    public var logsStatus: String?
    public var logFile: String?
    /// Per-request: the Last-48 page tab — "now" (default, the afm_now view) or "raw".
    public var afm48View: String?
    /// Per-request: the ship-confirm screen's repo/branch context (owner/name/branch/base/pr).
    public var shipOwner: String?
    public var shipName: String?
    public var shipBranch: String?
    public var shipBase: String?
    public var shipPR: String?

    /// Background cache-warming: keep the server's ProviderCache fresh on a timer so the
    /// instant a client opens (even briefly before going offline) it pulls the latest,
    /// rather than the server fetching lazily on the first request. (DASHBOARD_WARM=0 off.)
    public var warmEnabled: Bool
    /// Seconds between warm passes over the cheap pages (DASHBOARD_WARM_INTERVAL, default 60).
    public var warmInterval: TimeInterval
    /// Seconds between warm passes over the BigQuery-backed pages (afm, afm48). 0 = never
    /// warm them (the safe default — warming them on a timer adds BigQuery cost that the
    /// lazy-on-request design otherwise avoids). DASHBOARD_WARM_BQ_INTERVAL to opt in.
    public var warmBQInterval: TimeInterval
    /// Seconds between log-failure watcher polls of the sidecar /logs (push an APNs alert
    /// when a service newly fails). DASHBOARD_LOG_WATCH_INTERVAL, default 120; min 30.
    public var logWatchInterval: TimeInterval
    /// Seconds between Live Activity update pushes from the AFM current-state. 0 = OFF (the
    /// default — polling AFM hits BigQuery, so opt in knowingly). DASHBOARD_LIVE_ACTIVITY_INTERVAL.
    public var liveActivityInterval: TimeInterval
    /// AFM category whose ARRIVAL auto-dismisses the Live Activity (a transition into it), e.g.
    /// "stopped" = dismiss on arrival. DASHBOARD_LIVE_ACTIVITY_DISMISS_ON, default "stopped".
    public var liveActivityDismissOn: String
    /// Seconds between repo-watcher polls that push an APNs alert when a new open PR or
    /// unmerged branch appears on GitHub. 0 = OFF (default; each poll makes GitHub API calls).
    /// DASHBOARD_REPO_WATCH_INTERVAL.
    public var repoWatchInterval: TimeInterval
    /// Seconds between SILENT (content-available) background pushes that wake the iOS app to
    /// refresh its offline cache, so it's current the moment you open it even while-closed.
    /// Cheap (no BigQuery); iOS throttles delivery regardless. DASHBOARD_SILENT_PUSH_INTERVAL,
    /// default 1800 (30 min); 0 = OFF.
    public var silentPushInterval: TimeInterval
    /// Master notification switch — when false, NO alert pushes are sent (log-failure or repo),
    /// regardless of the per-type flags. DASHBOARD_NOTIFY_ENABLED, default true. (Silent
    /// background-refresh push is data, not a notification, so it's not gated by this.)
    public var notifyEnabled: Bool
    /// Per-type: send log-failure alerts. DASHBOARD_NOTIFY_LOG_FAILURES, default true.
    public var notifyLogFailures: Bool
    /// Per-type: send new-PR / unmerged-branch alerts. DASHBOARD_NOTIFY_REPOS, default true.
    public var notifyRepos: Bool
    /// Per-type: send an alert when the post-pull hook (auto_deploy.sh) newly fails. Default true.
    public var notifyDeployFailures: Bool

    public static func load(_ env: Environment) -> AppConfig {
        func str(_ key: String) -> String? {
            Environment.get(key).flatMap { $0.isEmpty ? nil : $0 }
        }
        let symbols = (str("STOCK_SYMBOLS") ?? "SPY,NVDA,VEEV")
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        return AppConfig(
            latitude: str("DASHBOARD_LAT").flatMap(Double.init),
            longitude: str("DASHBOARD_LON").flatMap(Double.init),
            locationName: str("DASHBOARD_LOCATION"),
            timezone: str("DASHBOARD_TZ") ?? TimeZone.current.identifier,
            timezonePinned: str("DASHBOARD_TZ") != nil,
            twelveDataKey: str("TWELVEDATA_KEY"),
            fredKey: str("FRED_KEY"),
            stockSymbols: symbols.isEmpty ? ["SPY", "NVDA", "VEEV"] : symbols,
            userMortgageRate: str("USER_MORTGAGE_RATE").flatMap(Double.init) ?? 6.375,
            propertyAddress: str("PROPERTY_ADDRESS") ?? "14 Beard Circle, Phoenixville, PA 19460",
            propertyZestimate: str("PROPERTY_ZESTIMATE").flatMap(Double.init),
            propertyURL: str("PROPERTY_ZILLOW_URL"),
            rapidAPIKey: str("RAPIDAPI_KEY"),
            bqSidecarURL: str("BQ_SIDECAR_URL") ?? "http://127.0.0.1:8099",
            webDist: str("WEB_DIST"),
            docsPath: nil,
            bqDataset: nil,
            bqTable: nil,
            bqView: nil,
            smartHomeTypes: nil,
            smartHomeSources: nil,
            weatherAtHome: false,
            afmRange: nil,
            knownLat: nil,
            knownLon: nil,
            knownPlace: nil,
            logsStatus: nil,
            logFile: nil,
            afm48View: nil,
            shipOwner: nil,
            shipName: nil,
            shipBranch: nil,
            shipBase: nil,
            shipPR: nil,
            warmEnabled: str("DASHBOARD_WARM").map { $0 != "0" && $0.lowercased() != "false" } ?? true,
            warmInterval: str("DASHBOARD_WARM_INTERVAL").flatMap(Double.init) ?? 60,
            warmBQInterval: str("DASHBOARD_WARM_BQ_INTERVAL").flatMap(Double.init) ?? 0,
            logWatchInterval: str("DASHBOARD_LOG_WATCH_INTERVAL").flatMap(Double.init) ?? 120,
            liveActivityInterval: str("DASHBOARD_LIVE_ACTIVITY_INTERVAL").flatMap(Double.init) ?? 0,
            liveActivityDismissOn: str("DASHBOARD_LIVE_ACTIVITY_DISMISS_ON") ?? "stopped",
            repoWatchInterval: str("DASHBOARD_REPO_WATCH_INTERVAL").flatMap(Double.init) ?? 0,
            silentPushInterval: str("DASHBOARD_SILENT_PUSH_INTERVAL").flatMap(Double.init) ?? 1800,
            notifyEnabled: str("DASHBOARD_NOTIFY_ENABLED").map { $0 != "0" && $0.lowercased() != "false" } ?? true,
            notifyLogFailures: str("DASHBOARD_NOTIFY_LOG_FAILURES").map { $0 != "0" && $0.lowercased() != "false" } ?? true,
            notifyRepos: str("DASHBOARD_NOTIFY_REPOS").map { $0 != "0" && $0.lowercased() != "false" } ?? true,
            notifyDeployFailures: str("DASHBOARD_NOTIFY_DEPLOY_FAILURES").map { $0 != "0" && $0.lowercased() != "false" } ?? true
        )
    }
}

extension Application {
    private struct AppConfigKey: StorageKey { typealias Value = AppConfig }
    public var dashboardConfig: AppConfig {
        get {
            guard let cfg = storage[AppConfigKey.self] else {
                fatalError("AppConfig not configured")
            }
            return cfg
        }
        set { storage[AppConfigKey.self] = newValue }
    }
}
