import Foundation
import Vapor

/// Placeholder home-screen provider. Replace this with your own data source.
/// The `welcome` key is bound by screen.json via `"binding": "welcome.*"`.
public struct WelcomeProvider: DataProvider {
    public let key = "welcome"
    public let ttl: TimeInterval = 3600
    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        stub(config: config)
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("headline", .string("Welcome to __APP_NAME__")),
            ("body", .string("Your server-driven iOS + PWA app is running. " +
                "Edit WelcomeProvider.swift to replace this placeholder with live data, " +
                "and edit Templates/screen.json to change the layout.")),
        ])
    }
}
