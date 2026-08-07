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
    /// uses (nil = the default dashboard screen).
    /// - `afm`: Activity-page provider — warmed on its own `DASHBOARD_WARM_AFM_INTERVAL`
    ///   cadence (default 120 s) so the page is never stale on open.
    /// - `expensive`: other BigQuery-backed pages (afm48, afmLog) — only warmed when
    ///   `DASHBOARD_WARM_BQ_INTERVAL > 0`; the lazy-on-request design avoids that cost by
    ///   default. An `afm` target also runs when `doBQ` fires (covers the BQ interval too).
    struct Target {
        let composer: Composer
        let screen: @Sendable (Templates) -> JSONValue?
        let expensive: Bool
        let afm: Bool
    }

    /// The base-page warm set. Mirrors the composers built in `routes(_:)` — base variants
    /// only (no per-request query params). Keep in sync when a new base page is added there.
    static func targets() -> [Target] {
        [
            // Dashboard: weather / stocks / mortgage / property — cheap external APIs.
            Target(composer: Composer(providers: [WeatherProvider(), StocksProvider(), MortgageProvider(), PropertyProvider(), RepoBannerProvider()]), screen: { _ in nil }, expensive: false, afm: false),
            Target(composer: Composer(providers: [BalancesProvider()]), screen: { $0.balances }, expensive: false, afm: false),
            Target(composer: Composer(providers: [SmartHomeProvider()]), screen: { $0.smarthome }, expensive: false, afm: false),
            Target(composer: Composer(providers: [SmartHomeLogProvider()]), screen: { $0.smarthomeLog }, expensive: false, afm: false),
            Target(composer: Composer(providers: [DeployProvider()]), screen: { $0.deploy }, expensive: false, afm: false),
            Target(composer: Composer(providers: [MessagesProvider()]), screen: { $0.messages }, expensive: false, afm: false),
            Target(composer: Composer(providers: [HousingProvider()]), screen: { $0.housing }, expensive: false, afm: false),
            Target(composer: Composer(providers: [LogsProvider()]), screen: { $0.logs }, expensive: false, afm: false),
            Target(composer: Composer(providers: [ReposProvider()]), screen: { $0.repos }, expensive: false, afm: false),
            Target(composer: Composer(providers: [SchedrunnerProvider()]), screen: { $0.schedrunner }, expensive: false, afm: false),
            Target(composer: Composer(providers: [SchedLogsProvider()]), screen: { $0.schedlogs }, expensive: false, afm: false),
            Target(composer: Composer(providers: [DocsProvider()]), screen: { $0.docs }, expensive: false, afm: false),
            Target(composer: Composer(providers: [ConfigProvider()]), screen: { $0.config }, expensive: false, afm: false),
            // BQ Tables base view lists datasets (no row preview) — cheaper than a preview query.
            Target(composer: Composer(providers: [BQTablesProvider()]), screen: { $0.bqtables }, expensive: false, afm: false),
            // Activity page — warmed on its own AFM interval (default 120 s) so it's never
            // stale on open. Also runs when the broader BQ interval fires.
            Target(composer: Composer(providers: [AFMProvider()]), screen: { $0.afm }, expensive: true, afm: true),
            // Other BQ-backed pages — only warmed when DASHBOARD_WARM_BQ_INTERVAL > 0.
            Target(composer: Composer(providers: [AFM48Provider()]), screen: { $0.afm48 }, expensive: true, afm: false),
            Target(composer: Composer(providers: [AFMLogProvider()]), screen: { $0.afmLog }, expensive: true, afm: false),
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
        let interval    = max(5, app.dashboardConfig.warmInterval)
        let afmInterval = app.dashboardConfig.warmAFMInterval
        let bqInterval  = app.dashboardConfig.warmBQInterval
        app.logger.info("cache warmer on: every \(Int(interval))s; AFM warming \(afmInterval > 0 ? "every \(Int(afmInterval))s" : "off"); BQ warming \(bqInterval > 0 ? "every \(Int(bqInterval))s" : "off")")
        let targets = targets()
        let task = Task {
            // Warm once at boot so the cache is hot before the first client arrives, then
            // on the interval. The AFM and BQ cadences are each tracked separately from the
            // main tick, so slow BQ billing never bunches up with cheap-page refreshes.
            var lastAFM = Date.distantPast
            var lastBQ  = Date.distantPast
            while !Task.isCancelled {
                let doAFM = afmInterval > 0 && Date().timeIntervalSince(lastAFM) >= afmInterval
                let doBQ  = bqInterval  > 0 && Date().timeIntervalSince(lastBQ)  >= bqInterval
                if doAFM { lastAFM = Date() }
                if doBQ  { lastBQ  = Date() }
                for target in targets {
                    if Task.isCancelled { break }
                    guard !target.expensive || doBQ || (target.afm && doAFM) else { continue }
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
