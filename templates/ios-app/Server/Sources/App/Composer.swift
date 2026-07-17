import Foundation
import Vapor

/// Loads the editable UI templates (theme + screen tree) once. Editing these JSON
/// files reshapes the __APP_NAME_LOWER__ with no client redeploy — the core SDUI promise.
public struct Templates: Sendable {
    public let theme: JSONValue
    public let screen: JSONValue
    public let bigquery: JSONValue
    public let afm: JSONValue
    public let afm48: JSONValue
    public let afmLog: JSONValue
    public let afmHealth: JSONValue
    public let afmLabel: JSONValue
    public let config: JSONValue
    public let settings: JSONValue
    public let balances: JSONValue
    public let budget: JSONValue
    public let smarthome: JSONValue
    public let smarthomeLog: JSONValue
    public let logs: JSONValue
    public let logfile: JSONValue
    public let repos: JSONValue
    public let reposShip: JSONValue
    public let schedrunner: JSONValue
    public let schedlogs: JSONValue
    public let docs: JSONValue
    public let bqtables: JSONValue
    public let gcpCosts: JSONValue
    public let messages: JSONValue
    public let deploy: JSONValue
    public let nav: JSONValue

    public static func load() throws -> Templates {
        Templates(
            theme: try loadResource("theme"),
            screen: try loadResource("screen"),
            bigquery: try loadResource("bigquery"),
            afm: try loadResource("afm"),
            afm48: try loadResource("afm48"),
            afmLog: try loadResource("afm_log"),
            afmHealth: try loadResource("afm_health"),
            afmLabel: try loadResource("afm_label"),
            config: try loadResource("config"),
            settings: try loadResource("settings"),
            balances: try loadResource("balances"),
            budget: try loadResource("budget"),
            smarthome: try loadResource("smarthome"),
            smarthomeLog: try loadResource("smarthome_log"),
            logs: try loadResource("logs"),
            logfile: try loadResource("logfile"),
            repos: try loadResource("repos"),
            reposShip: try loadResource("repos_ship"),
            schedrunner: try loadResource("schedrunner"),
            schedlogs: try loadResource("schedlogs"),
            docs: try loadResource("docs"),
            bqtables: try loadResource("bqtables"),
            gcpCosts: try loadResource("gcp_costs"),
            messages: try loadResource("messages"),
            deploy: try loadResource("deploy"),
            nav: try loadResource("nav")
        )
    }

    private static func loadResource(_ name: String) throws -> JSONValue {
        let candidates: [URL?] = [
            Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Templates"),
            Bundle.module.url(forResource: name, withExtension: "json"),
        ]
        guard let url = candidates.compactMap({ $0 }).first else {
            throw ProviderError.badResponse("template \(name).json not found in bundle")
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(JSONValue.self, from: data)
    }
}

extension Application {
    private struct TemplatesKey: StorageKey { typealias Value = Templates }
    public var templates: Templates {
        get {
            guard let t = storage[TemplatesKey.self] else { fatalError("Templates not loaded") }
            return t
        }
        set { storage[TemplatesKey.self] = newValue }
    }
}

/// Assembles the manifest: theme + screen from templates, data bag from providers
/// (each isolated behind the last-good cache so one failure can't blank the board).
public struct Composer: Sendable {
    public let providers: [DataProvider]
    private let location = LocationResolver()

    public init(providers: [DataProvider]) {
        self.providers = providers
    }

    public func build(client: Client, config rawConfig: AppConfig, cache: ProviderCache, templates: Templates, logger: Logger, screen: JSONValue? = nil) async -> Manifest {
        // Resolve effective location + timezone first (auto-detect when unset).
        let config = await location.effective(rawConfig, client: client, logger: logger)

        var data: [String: JSONValue] = [:]
        var anyStale = false

        for provider in providers {
            let result = await cache.value(for: provider.cacheKey(config), ttl: provider.ttl) {
                try await provider.fetch(client: client, config: config, logger: logger)
            }
            switch result {
            case .fresh(let value):
                data[provider.key] = value
            case .stale(let value):
                data[provider.key] = value
                anyStale = true
            case .miss:
                data[provider.key] = provider.stub(config: config)
                anyStale = true
                logger.warning("provider \(provider.key) missed; serving stub")
            }
        }

        data["meta"] = .obj([
            ("updatedAtFormatted", .string(anyStale ? "Showing last known data" : "Updated just now")),
            ("stale", .bool(anyStale)),
            // Deployed server build (git sha set by setup_server.sh) so the client can
            // show it — makes "did my deploy land?" answerable from the UI.
            ("build", .string(Environment.get("DASHBOARD_BUILD") ?? "dev")),
        ])

        return Manifest(
            schemaVersion: Manifest.currentSchemaVersion,
            generatedAt: ISO8601DateFormatter().string(from: Date()),
            theme: templates.theme,
            data: .object(data),
            nav: templates.nav,
            screen: screen ?? templates.screen
        )
    }
}
