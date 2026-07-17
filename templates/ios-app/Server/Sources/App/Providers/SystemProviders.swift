import Foundation
import Vapor

extension CharacterSet {
    static let sidecarQueryValue: CharacterSet = {
        var set = CharacterSet.alphanumerics
        set.insert(charactersIn: "-._~/")
        return set
    }()
}

/// App preferences (stored in the sidecar's config table). Renders the Config screen.
/// Falls back to a stub when the BigQuery sidecar is not running.
public struct ConfigProvider: DataProvider {
    public let key = "config"
    public let ttl: TimeInterval = 0
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/config", as: JSONValue.self)
        return try value.requireOK("config")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Preferences")),
            ("subtitleFormatted", .string("BigQuery sidecar not running · see DEVELOPMENT.md to enable")),
            ("items", .array([])),
        ])
    }
}

/// App secrets/keys (Google Secret Manager via the sidecar). Renders the Settings screen.
/// Falls back to a stub when the sidecar is not running.
public struct SettingsProvider: DataProvider {
    public let key = "settings"
    public let ttl: TimeInterval = 0
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/settings", as: JSONValue.self)
        return try value.requireOK("settings")
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("title", .string("Secrets & Keys")),
            ("subtitleFormatted", .string("BigQuery sidecar not running · see DEVELOPMENT.md to enable")),
            ("groups", .array([])),
        ])
    }
}
