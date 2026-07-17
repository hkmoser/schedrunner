import Foundation
import Vapor

/// Background cache-warming. The whole point of the app is that opening it — even for a
/// moment before going offline — shows the absolute latest data. That only holds if the
/// server keeps its own `ProviderCache` warm on a schedule instead of fetching lazily on
/// the first client request. This loop periodically re-composes the base pages, which
/// drives each provider's cache entry to refresh. Per-provider TTLs gate the actual
/// upstream calls, so warming more often than a TTL is a cheap no-op.
enum Warmer {
    /// A base page to keep warm: the composer that renders it and the screen template it
    /// uses (nil = the default dashboard screen). `expensive` = BigQuery-backed; those are
    /// only warmed when `DASHBOARD_WARM_BQ_INTERVAL > 0`, so we never re-bill BigQuery on a
    /// timer by default (the lazy-on-request design otherwise avoids that cost).
    struct Target {
        let composer: Composer
        let screen: @Sendable (Templates) -> JSONValue?
        let expensive: Bool
    }

    /// The base-page warm set. Mirrors the composers built in `routes(_:)` — base variants
    /// only (no per-request query params). Keep in sync when a new base page is added there.
    static func targets() -> [Target] {
        [
            // Dashboard: weather / stocks / mortgage / property — cheap external APIs.
            Target(composer: Composer(providers: [WeatherProvider(), StocksProvider(), MortgageProvider(), PropertyProvider(), RepoBannerProvider()]), screen: { _ in nil }, expensive: false),
            Target(composer: Composer(providers: [BalancesProvider()]), screen: { $0.balances }, expensive: false),
            Target(composer: Composer(providers: [SmartHomeProvider()]), screen: { $0.smarthome }, expensive: false),
            Target(composer: Composer(providers: [SmartHomeLogProvider()]), screen: { $0.smarthomeLog }, expensive: false),
            Target(composer: Composer(providers: [DeployProvider()]), screen: { $0.deploy }, expensive: false),
            Target(composer: Composer(providers: [MessagesProvider()]), screen: { $0.messages }, expensive: false),
            Target(composer: Composer(providers: [LogsProvider()]), screen: { $0.logs }, expensive: false),
            Target(composer: Composer(providers: [ReposProvider()]), screen: { $0.repos }, expensive: false),
            Target(composer: Composer(providers: [SchedrunnerProvider()]), screen: { $0.schedrunner }, expensive: false),
            Target(composer: Composer(providers: [SchedLogsProvider()]), screen: { $0.schedlogs }, expensive: false),
            Target(composer: Composer(providers: [DocsProvider()]), screen: { $0.docs }, expensive: false),
            Target(composer: Composer(providers: [ConfigProvider()]), screen: { $0.config }, expensive: false),
            // BQ Tables base view lists datasets (no row preview) — cheaper than a preview query.
            Target(composer: Composer(providers: [BQTablesProvider()]), screen: { $0.bqtables }, expensive: false),
            // BigQuery-backed, billed per query — only warmed when DASHBOARD_WARM_BQ_INTERVAL > 0.
            Target(composer: Composer(providers: [AFMProvider()]), screen: { $0.afm }, expensive: true),
            Target(composer: Composer(providers: [AFM48Provider()]), screen: { $0.afm48 }, expensive: true),
            Target(composer: Composer(providers: [AFMLogProvider()]), screen: { $0.afmLog }, expensive: true),
        ]
    }

    private struct WarmerTaskKey: StorageKey { typealias Value = Task<Void, Never> }

    private struct WarmerLifecycle: LifecycleHandler {
        func shutdown(_ application: Application) {
            application.storage[WarmerTaskKey.self]?.cancel()
        }
    }

    /// Start the warm loop on a detached task; cancelled cleanly at app shutdown.
    static func start(on app: Application) {
        let interval = max(5, app.dashboardConfig.warmInterval)
        let bqInterval = app.dashboardConfig.warmBQInterval
        app.logger.info("cache warmer on: every \(Int(interval))s; BQ warming \(bqInterval > 0 ? "every \(Int(bqInterval))s" : "off")")
        let targets = targets()
        let task = Task {
            // Warm once at boot so the cache is hot before the first client arrives, then
            // on the interval. A slow BQ cadence is tracked separately from the main tick.
            var lastBQ = Date.distantPast
            while !Task.isCancelled {
                let doBQ = bqInterval > 0 && Date().timeIntervalSince(lastBQ) >= bqInterval
                if doBQ { lastBQ = Date() }
                for target in targets where !target.expensive || doBQ {
                    if Task.isCancelled { break }
                    _ = await target.composer.build(
                        client: app.client,
                        config: app.dashboardConfig,
                        cache: app.providerCache,
                        templates: app.templates,
                        logger: app.logger,
                        screen: target.screen(app.templates)
                    )
                }
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
        app.storage[WarmerTaskKey.self] = task
        app.lifecycle.use(WarmerLifecycle())
    }
}
