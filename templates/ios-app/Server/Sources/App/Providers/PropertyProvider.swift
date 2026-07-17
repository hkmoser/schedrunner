import Foundation
import Vapor

/// Rental-property Zestimate card.
///
/// Zillow retired its free public Zestimate API in 2021, so there is no
/// ToS-clean, no-key way to fetch a live Zestimate. This provider therefore:
///   1. uses a live value if a RapidAPI Zillow key (RAPIDAPI_KEY) is configured,
///   2. otherwise shows a value you set once (PROPERTY_ZESTIMATE), and
///   3. always links to the Zillow listing so the card is useful regardless.
public struct PropertyProvider: DataProvider {
    public let key = "property"
    public let ttl: TimeInterval = 12 * 60 * 60

    public init() {}

    private struct SearchResponse: Decodable {
        struct Prop: Decodable {
            let zpid: Int?
            let zestimate: Double?
            let rentZestimate: Double?
            let address: String?
        }
        let props: [Prop]?
    }

    /// The /property?zpid= details response (full record; the search result often omits the
    /// zestimate, so we follow up by zpid to actually get the number).
    private struct PropertyDetail: Decodable {
        let zestimate: Double?
        let rentZestimate: Double?
        let price: Double?
        let hdpUrl: String?
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let url = config.propertyURL ?? Self.zillowSearchURL(config.propertyAddress)

        // 1) Live lookup via RapidAPI, if a key is configured. Best-effort: any failure falls
        // through to the manually-configured value below.
        if let key = config.rapidAPIKey {
            let host = "zillow-com1.p.rapidapi.com"
            func rapid(_ path: String) async -> Data? {
                // NB: assign first — a trailing closure as the last expr in a `guard` condition is
                // illegal in Swift (ambiguous with the guard body), so don't inline this into guard.
                let resp = try? await client.get(URI(string: "https://\(host)\(path)")) { req in
                    req.headers.replaceOrAdd(name: "X-RapidAPI-Key", value: key)
                    req.headers.replaceOrAdd(name: "X-RapidAPI-Host", value: host)
                    req.headers.replaceOrAdd(name: .accept, value: "application/json")
                }
                guard let resp else {
                    logger.warning("Zillow request failed (network) for \(path)")
                    return nil
                }
                // 401/403 = bad/over-quota RapidAPI key; log the status + a snippet so it's
                // diagnosable (the card otherwise silently falls back to the manual value).
                if resp.status.code < 200 || resp.status.code >= 300 {
                    let snippet = resp.body.map { String(buffer: $0).prefix(160) } ?? ""
                    logger.warning("Zillow \(resp.status.code) for \(path): \(snippet)")
                }
                return resp.body.map { Data(buffer: $0) }
            }
            let loc = config.propertyAddress.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""

            if let data = await rapid("/propertyExtendedSearch?location=\(loc)"),
               let decoded = try? JSONDecoder().decode(SearchResponse.self, from: data),
               let prop = decoded.props?.first {
                // Search sometimes carries the zestimate directly…
                if let z = prop.zestimate {
                    return card(config: config, zestimate: z, rent: prop.rentZestimate,
                                sourceNote: "Zestimate · live", url: url)
                }
                // …but usually it doesn't — follow the zpid to the full property record.
                if let zpid = prop.zpid,
                   let detail = await rapid("/property?zpid=\(zpid)"),
                   let pd = try? JSONDecoder().decode(PropertyDetail.self, from: detail),
                   let z = pd.zestimate ?? pd.price {
                    let listing = pd.hdpUrl.map { "https://www.zillow.com\($0)" } ?? url
                    return card(config: config, zestimate: z, rent: pd.rentZestimate,
                                sourceNote: pd.zestimate != nil ? "Zestimate · live" : "List price · live",
                                url: listing)
                }
            }
            logger.warning("Zillow RapidAPI returned no usable zestimate for \(config.propertyAddress); using configured value")
        }

        // 2) Configured manual value.
        if let z = config.propertyZestimate {
            return card(config: config, zestimate: z, rent: nil, sourceNote: "Zestimate · set manually", url: url)
        }

        // 3) No value available — still link out.
        return .obj([
            ("address", .string(config.propertyAddress)),
            ("valueFormatted", .string("Tap to view on Zillow")),
            ("rentFormatted", .string("")),
            ("sourceFormatted", .string("Set PROPERTY_ZESTIMATE or RAPIDAPI_KEY for a value")),
            ("url", .string(url)),
        ])
    }

    public func stub(config: AppConfig) -> JSONValue {
        card(
            config: config,
            zestimate: config.propertyZestimate ?? 525_000,
            rent: 2_650,
            sourceNote: config.propertyZestimate == nil ? "Zestimate · sample" : "Zestimate · set manually",
            url: config.propertyURL ?? Self.zillowSearchURL(config.propertyAddress)
        )
    }

    private func card(config: AppConfig, zestimate: Double, rent: Double?, sourceNote: String, url: String) -> JSONValue {
        .obj([
            ("address", .string(config.propertyAddress)),
            ("valueFormatted", .string(Fmt.usdWhole(zestimate))),
            ("rentFormatted", .string(rent.map { "Rent: \(Fmt.usdWhole($0))/mo" } ?? "")),
            ("sourceFormatted", .string(sourceNote)),
            ("url", .string(url)),
        ])
    }

    private static func zillowSearchURL(_ address: String) -> String {
        let slug = address.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? address
        return "https://www.zillow.com/homes/\(slug)_rb/"
    }
}
