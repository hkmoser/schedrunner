import Foundation
import Vapor

/// 30-year fixed mortgage rate from BigQuery (via the sidecar `/mortgage` — default
/// Datahub.mortgage_mnd_30yr, rate_pct dated by rate_date), with:
///  - delta vs the prior reading,
///  - comparison to the user's existing rate, and
///  - a secondary housing-market indicator: the Case-Shiller U.S. National Home
///    Price Index (CSUSHPINSA) from FRED, shown as YoY change (best-effort; needs FRED_KEY).
public struct MortgageProvider: DataProvider {
    public let key = "mortgage"
    public let ttl: TimeInterval = 6 * 60 * 60

    public init() {}

    private struct Response: Decodable {
        struct Observation: Decodable {
            let date: String
            let value: String
        }
        let observations: [Observation]
    }

    /// The sidecar /mortgage payload: raw rate numbers + ISO date (the server formats them here).
    private struct SidecarRate: Decodable {
        let rate: Double?
        let priorRate: Double?
        let asOf: String?
        let label: String?
        let error: String?
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        // --- 30-yr fixed rate from BigQuery (via the localhost sidecar) ---
        let rateURL = "\(config.bqSidecarURL)/mortgage"
        let m = try await client.getJSON(rateURL, as: SidecarRate.self)
        guard let rate = m.rate else { throw ProviderError.badResponse(m.error ?? "no mortgage rate") }
        let prior = m.priorRate
        let delta = prior.map { rate - $0 }

        // --- comparison to the user's current rate ---
        let yours = config.userMortgageRate
        let vs = rate - yours
        let vsText: String
        if abs(vs) < 0.005 {
            vsText = "Same as your \(Self.pct(yours))"
        } else {
            vsText = "\(Self.signed(vs)) vs your \(Self.pct(yours))"
        }
        // Green when the market is at/below your rate (a refi opportunity).
        let vsDirection = vs <= 0 ? "up" : "down"
        let refiHint: String
        if vs <= -0.25 { refiHint = "Market is lower — refinancing could save" }
        else if vs >= 0.25 { refiHint = "Your rate is better — hold" }
        else { refiHint = "About the same as your rate" }

        // --- secondary indicator: Case-Shiller home prices (FRED, best-effort) ---
        let housing: JSONValue
        if let fred = config.fredKey {
            housing = await Self.housing(client: client, apiKey: fred, logger: logger)
        } else {
            housing = .obj([
                ("label", .string("Home Prices · Case-Shiller")),
                ("valueFormatted", .string("—")),
                ("levelFormatted", .string("")),
                ("asOfFormatted", .string("set FRED_KEY")),
                ("direction", .string("up")),
            ])
        }

        let asOf = m.asOf.map(Self.prettyDate) ?? "—"
        return .obj([
            ("label", .string(m.label ?? "30-Yr Fixed")),
            ("rateFormatted", .string(Self.pct(rate))),
            ("asOfFormatted", .string("As of \(asOf)")),
            ("deltaFormatted", .string(Self.deltaString(delta))),
            ("direction", .string((delta ?? 0) >= 0 ? "up" : "down")),
            ("yourRateFormatted", .string("Your rate: \(Self.pct(yours))")),
            ("vsYoursFormatted", .string(vsText)),
            ("vsYoursDirection", .string(vsDirection)),
            ("refiHint", .string(refiHint)),
            ("housing", housing),
        ])
    }

    public func stub(config: AppConfig) -> JSONValue {
        let yours = config.userMortgageRate
        let vs = 6.82 - yours
        return .obj([
            ("label", .string("30-Yr Fixed")),
            ("rateFormatted", .string("6.82%")),
            ("asOfFormatted", .string("As of (sample)")),
            ("deltaFormatted", .string("+0.04 WoW")),
            ("direction", .string("up")),
            ("yourRateFormatted", .string("Your rate: \(Self.pct(yours))")),
            ("vsYoursFormatted", .string("\(Self.signed(vs)) vs your \(Self.pct(yours))")),
            ("vsYoursDirection", .string(vs <= 0 ? "up" : "down")),
            ("refiHint", .string(vs >= 0.25 ? "Your rate is better — hold" : "About the same as your rate")),
            ("housing", .obj([
                ("label", .string("Home Prices · Case-Shiller")),
                ("valueFormatted", .string("+4.2% YoY")),
                ("levelFormatted", .string("Index 322.1")),
                ("asOfFormatted", .string("As of (sample)")),
                ("direction", .string("up")),
            ])),
        ])
    }

    // MARK: - Case-Shiller home price index (YoY)

    private static func housing(client: Client, apiKey: String, logger: Logger) async -> JSONValue {
        let url = "https://api.stlouisfed.org/fred/series/observations"
            + "?series_id=CSUSHPINSA&api_key=\(apiKey)&file_type=json&sort_order=desc&limit=13"
        do {
            let r = try await client.getJSON(url, as: Response.self)
            guard let latest = r.observations.first, let level = Double(latest.value) else {
                throw ProviderError.badResponse("no CS observations")
            }
            let yearAgo = r.observations.count >= 13 ? Double(r.observations[12].value) : nil
            let yoy = yearAgo.map { (level / $0 - 1) * 100 }
            return .obj([
                ("label", .string("Home Prices · Case-Shiller")),
                ("valueFormatted", .string(yoy.map { "\(Fmt.percent($0)) YoY" } ?? "—")),
                ("levelFormatted", .string("Index \(String(format: "%.1f", level))")),
                ("asOfFormatted", .string("As of \(monthYear(latest.date))")),
                ("direction", .string((yoy ?? 0) >= 0 ? "up" : "down")),
            ])
        } catch {
            logger.warning("Case-Shiller fetch failed: \(error)")
            return .obj([
                ("label", .string("Home Prices · Case-Shiller")),
                ("valueFormatted", .string("—")),
                ("levelFormatted", .string("")),
                ("asOfFormatted", .string("unavailable")),
                ("direction", .string("up")),
            ])
        }
    }

    // MARK: - Formatting

    /// Up to 3 decimals, trailing zeros trimmed: 6.375 -> "6.375%", 6.820 -> "6.82%".
    private static func pct(_ value: Double) -> String {
        var s = String(format: "%.3f", value)
        while s.contains(".") && (s.hasSuffix("0") || s.hasSuffix(".")) { s.removeLast() }
        return s + "%"
    }

    private static func signed(_ value: Double) -> String {
        let sign = value >= 0 ? "+" : "−"
        return "\(sign)\(String(format: "%.2f", abs(value)))%"
    }

    private static func deltaString(_ delta: Double?) -> String {
        guard let d = delta else { return "—" }
        let sign = d >= 0 ? "+" : "−"
        return "\(sign)\(String(format: "%.2f", abs(d))) vs prior"
    }

    private static func prettyDate(_ iso: String) -> String { reformat(iso, to: "MMM d, yyyy") }
    private static func monthYear(_ iso: String) -> String { reformat(iso, to: "MMM yyyy") }

    private static func reformat(_ iso: String, to pattern: String) -> String {
        let inFmt = DateFormatter()
        inFmt.dateFormat = "yyyy-MM-dd"
        inFmt.timeZone = TimeZone(identifier: "UTC")
        // Tolerate a "2026-06-25T00:00:00" timestamp by parsing just the date portion.
        guard let date = inFmt.date(from: String(iso.prefix(10))) else { return iso }
        let outFmt = DateFormatter()
        outFmt.dateFormat = pattern
        return outFmt.string(from: date)
    }
}
