import Foundation
import Vapor

/// Stocks via Twelve Data (free tier: 800/day, 8/min, delayed). We cache ~15 min
/// server-side so polling stays well under quota. Lines are normalized to 100 at
/// the window's first close so relative performance is directly comparable.
public struct StocksProvider: DataProvider {
    public let key = "stocks"
    public let ttl: TimeInterval = 15 * 60

    public init() {}

    private static let palette = ["#6ea8fe", "#43d18a", "#ffd166", "#ff6b6b", "#b388ff"]
    private static let names: [String: String] = [
        "SPY": "S&P 500", "NVDA": "NVIDIA", "VEEV": "Veeva", "QQQ": "Nasdaq 100",
    ]

    private struct SymbolSeries: Decodable {
        struct Value: Decodable {
            let datetime: String
            let close: String
        }
        let values: [Value]?
        let status: String?
        let message: String?
    }

    /// Twelve Data's whole-request failure shape: {"code":401,"message":"…","status":"error"}.
    private struct ErrorEnvelope: Decodable {
        let code: Int?
        let message: String?
        let status: String?
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        guard let apiKey = config.twelveDataKey else { throw ProviderError.missingKey }
        let symbols = config.stockSymbols.joined(separator: ",")
        let url = "https://api.twelvedata.com/time_series"
            + "?symbol=\(symbols)&interval=15min&outputsize=26&apikey=\(apiKey)"

        // Fetch raw so a bad key / exhausted quota / bad symbol surfaces Twelve Data's own
        // message (in the log + /healthz) instead of an opaque JSON-decode error.
        let resp = try await client.get(URI(string: url))
        guard let buffer = resp.body else { throw ProviderError.badResponse("empty response from Twelve Data") }
        let data = Data(buffer: buffer)
        if let err = try? JSONDecoder().decode(ErrorEnvelope.self, from: data), err.status == "error" {
            let code = err.code.map { " [\($0)]" } ?? ""
            throw ProviderError.badResponse("Twelve Data error\(code): \(err.message ?? "unknown")")
        }
        guard let decoded = try? JSONDecoder().decode([String: SymbolSeries].self, from: data) else {
            throw ProviderError.badResponse("unexpected Twelve Data response (check symbols / single-symbol request)")
        }

        var series: [JSONValue] = []
        for (i, symbol) in config.stockSymbols.enumerated() {
            if let s = decoded[symbol], s.status == "error" {
                logger.warning("Twelve Data \(symbol): \(s.message ?? "error")")
            }
            guard let s = decoded[symbol], let values = s.values, values.count > 1 else { continue }
            // Twelve Data returns newest-first; reverse to chronological.
            let closes = values.reversed().compactMap { Double($0.close) }
            guard let base = closes.first, base != 0, let last = closes.last else { continue }
            let points = closes.map { JSONValue.double(($0 / base) * 100) }
            let changePct = (last / base - 1) * 100
            series.append(.obj([
                ("symbol", .string(symbol)),
                ("name", .string(Self.names[symbol] ?? symbol)),
                ("priceFormatted", .string(Fmt.usd(last))),
                ("changePctFormatted", .string(Fmt.percent(changePct))),
                ("direction", .string(Fmt.direction(changePct))),
                ("color", .string(Self.palette[i % Self.palette.count])),
                ("points", .array(points)),
            ]))
        }
        guard !series.isEmpty else {
            throw ProviderError.badResponse("no usable series for \(symbols) — check the symbols, key, or daily quota")
        }

        return .obj([
            ("asOfFormatted", .string("As of \(Self.timeString(config.timezone)) (15m delayed)")),
            ("timescaleFormatted", .string("Today · 15-min intervals")),
            ("series", .array(series)),
        ])
    }

    public func stub(config: AppConfig) -> JSONValue {
        func s(_ sym: String, _ name: String, _ price: String, _ pct: String, _ dir: String, _ color: String, _ pts: [Double]) -> JSONValue {
            .obj([
                ("symbol", .string(sym)), ("name", .string(name)),
                ("priceFormatted", .string(price)),
                ("changePctFormatted", .string(pct)), ("direction", .string(dir)),
                ("color", .string(color)), ("points", .array(pts.map { JSONValue.double($0) })),
            ])
        }
        return .obj([
            ("asOfFormatted", .string("As of \(Self.timeString(config.timezone)) (sample)")),
            ("timescaleFormatted", .string("Today · 15-min intervals")),
            ("series", .array([
                s("SPY", "S&P 500", "$543.21", "+0.84%", "up", "#6ea8fe", [100, 100.2, 99.9, 100.4, 100.6, 100.5, 100.84]),
                s("NVDA", "NVIDIA", "$132.40", "+2.31%", "up", "#43d18a", [100, 100.8, 101.2, 100.9, 101.8, 102.0, 102.31]),
                s("VEEV", "Veeva", "$228.65", "-0.42%", "down", "#ff6b6b", [100, 99.8, 99.6, 99.9, 99.5, 99.4, 99.58]),
            ])),
        ])
    }

    private static func timeString(_ timezone: String) -> String {
        let df = DateFormatter()
        df.dateFormat = "h:mm a"
        df.timeZone = TimeZone(identifier: timezone) ?? .current
        return df.string(from: Date())
    }
}
