import Foundation
import Vapor

/// A source of one card's data bag. Providers format their own values (percent,
/// dates, °) so the server owns presentation and the clients stay dumb.
public protocol DataProvider: Sendable {
    /// Key under `data` in the manifest (e.g. "weather").
    var key: String { get }
    /// Cache freshness window in seconds.
    var ttl: TimeInterval { get }
    /// Fetch live data. Throw on any failure; the cache handles fallback.
    func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue
    /// Plausible offline/no-key data so the dashboard is never empty.
    func stub(config: AppConfig) -> JSONValue
    /// Cache identity. Defaults to `key`; location-dependent providers vary it by
    /// coordinates so different locations don't share a cached value.
    func cacheKey(_ config: AppConfig) -> String
}

extension DataProvider {
    public func cacheKey(_ config: AppConfig) -> String { key }
}

enum ProviderError: Error {
    case missingKey
    case badResponse(String)
    case decode(String)
}

extension JSONValue {
    /// The sidecar replies 200 with `{… "error": "…"}` when an endpoint throws. For a
    /// structured page that just renders blank, so treat a non-empty `error` (or a
    /// non-object) as a fetch failure — the ProviderCache then serves the last-good
    /// value or the provider stub instead of a broken screen.
    func requireOK(_ what: String) throws -> JSONValue {
        guard case .object(let obj) = self else {
            throw ProviderError.badResponse("\(what): non-object response")
        }
        if case .string(let msg)? = obj["error"], !msg.isEmpty {
            throw ProviderError.badResponse("\(what): \(msg)")
        }
        return self
    }
}

extension Client {
    /// GET a URL and decode the JSON body into `T`. The client read/connect
    /// timeout (set in configure) bounds how long a hung upstream can stall us.
    func getJSON<T: Decodable>(_ url: String, as type: T.Type) async throws -> T {
        let response = try await self.get(URI(string: url)) { req in
            req.headers.replaceOrAdd(name: .accept, value: "application/json")
        }
        guard response.status == .ok, let body = response.body else {
            throw ProviderError.badResponse("status \(response.status.code) for \(url)")
        }
        do {
            return try JSONDecoder().decode(T.self, from: Data(buffer: body))
        } catch {
            throw ProviderError.decode("\(error)")
        }
    }
}

// MARK: - Formatting helpers shared by providers

enum Fmt {
    static func temp(_ value: Double) -> String { "\(Int(value.rounded()))°" }

    static func usd(_ value: Double, fractionDigits: Int = 2) -> String {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.maximumFractionDigits = fractionDigits
        f.minimumFractionDigits = fractionDigits
        return f.string(from: NSNumber(value: value)) ?? String(format: "$%.\(fractionDigits)f", value)
    }

    static func usdWhole(_ value: Double) -> String { usd(value, fractionDigits: 0) }

    static func percent(_ value: Double) -> String {
        let sign = value >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", value))%"
    }

    static func direction(_ value: Double) -> String { value >= 0 ? "up" : "down" }

    static func hour(_ date: Date, timezone: String) -> String {
        let df = DateFormatter()
        df.dateFormat = "ha"
        df.amSymbol = "AM"
        df.pmSymbol = "PM"
        df.timeZone = TimeZone(identifier: timezone) ?? .current
        return df.string(from: date)
    }
}
