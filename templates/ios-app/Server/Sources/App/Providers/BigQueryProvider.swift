import Foundation
import Vapor

/// Fetches a query result from the local Python BigQuery sidecar (which uses the
/// machine's Application Default Credentials — no keys here) and passes the JSON
/// straight through as the `bq` data bag: { title, subtitleFormatted, columns, rows, error? }.
public struct BigQueryProvider: DataProvider {
    public let key = "bq"
    public let ttl: TimeInterval = 5 * 60

    public init() {}

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let value = try await client.getJSON("\(config.bqSidecarURL)/query", as: JSONValue.self)
        guard case .object(let obj) = value else {
            throw ProviderError.badResponse("bq sidecar returned a non-object")
        }
        // A sidecar-level error is valid data (rendered as a table error), not a failure.
        return .object(obj)
    }

    public func stub(config: AppConfig) -> JSONValue {
        func row(_ name: String, _ total: Int) -> JSONValue { .array([.string(name), .int(total)]) }
        return .obj([
            ("title", .string("BigQuery")),
            ("subtitleFormatted", .string("sidecar unavailable · sample data")),
            ("columns", .array([.string("name"), .string("total")])),
            ("rows", .array([
                row("Mary", 391_927), row("Patricia", 247_752), row("Jennifer", 244_715),
                row("Elizabeth", 230_521), row("Linda", 221_835),
            ])),
            ("rowCount", .int(5)),
        ])
    }
}
