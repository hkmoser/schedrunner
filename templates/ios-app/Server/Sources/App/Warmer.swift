import Foundation
import Vapor

/// Background cache-warming. The whole point of the app is that opening it — even for a
/// moment before going offline — shows the absolute latest data. This loop periodically
/// re-composes the base pages so provider cache entries stay fresh on a schedule.
enum Warmer {
    struct Target {
        let composer: Composer
        let screen: @Sendable (Templates) -> JSONValue?
    }

    /// Pages to keep warm. Mirrors the composers built in `routes(_:)`.
    /// Add a Target here whenever you add a new screen in routes.swift.
    static func targets() -> [Target] {
        [
            Target(composer: Composer(providers: [WelcomeProvider()]), screen: { _ in nil }),
            Target(composer: Composer(providers: [DeployProvider()]), screen: { $0.deploy }),
        ]
    }

    private struct WarmerTaskKey: StorageKey { typealias Value = Task<Void, Never> }

    private struct WarmerLifecycle: LifecycleHandler {
        func shutdown(_ application: Application) {
            application.storage[WarmerTaskKey.self]?.cancel()
        }
    }

    static func start(on app: Application) {
        let interval = max(5, app.__APP_NAME_LOWER__Config.warmInterval)
        app.logger.info("cache warmer on: every \(Int(interval))s")
        let targets = targets()
        let task = Task {
            while !Task.isCancelled {
                for target in targets {
                    if Task.isCancelled { break }
                    _ = await target.composer.build(
                        client: app.client,
                        config: app.__APP_NAME_LOWER__Config,
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
