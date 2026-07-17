import Foundation
import Vapor

/// Weather via Open-Meteo (free, no API key). Needs only lat/lon + timezone.
public struct WeatherProvider: DataProvider {
    public let key = "weather"
    public let ttl: TimeInterval = 15 * 60

    public init() {}

    // Vary the cache by location + timezone so a client's precise coordinates get
    // their own entry instead of a stale IP-based one.
    public func cacheKey(_ config: AppConfig) -> String {
        String(format: "weather:%.2f,%.2f:%@:%@", config.latitude ?? 0, config.longitude ?? 0,
               config.timezone, config.weatherAtHome ? "home" : "cur")
    }

    private struct Response: Decodable {
        struct Current: Decodable {
            let temperature_2m: Double
            let weather_code: Int
        }
        struct Daily: Decodable {
            let temperature_2m_max: [Double]
            let temperature_2m_min: [Double]
        }
        struct Hourly: Decodable {
            let time: [String]
            let temperature_2m: [Double]
            let weather_code: [Int]
        }
        let current: Current
        let daily: Daily
        let hourly: Hourly
    }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        let tz = config.timezone.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? config.timezone
        let lat = config.latitude ?? 37.7749
        let lon = config.longitude ?? -122.4194
        let url = "https://api.open-meteo.com/v1/forecast"
            + "?latitude=\(lat)&longitude=\(lon)"
            + "&current=temperature_2m,weather_code"
            + "&daily=temperature_2m_max,temperature_2m_min"
            + "&hourly=temperature_2m,weather_code"
            + "&temperature_unit=fahrenheit&timezone=\(tz)&forecast_days=2"

        let r = try await client.getJSON(url, as: Response.self)
        let (icon, condition) = Self.symbol(for: r.current.weather_code)

        let hi = r.daily.temperature_2m_max.first ?? r.current.temperature_2m
        let lo = r.daily.temperature_2m_min.first ?? r.current.temperature_2m

        // The next 24 hourly buckets at/after the current hour (times are in config tz).
        let parser = DateFormatter()
        parser.dateFormat = "yyyy-MM-dd'T'HH:mm"
        parser.timeZone = TimeZone(identifier: config.timezone) ?? .current
        let now = Date()
        var hourly: [JSONValue] = []
        for (i, t) in r.hourly.time.enumerated() {
            guard let date = parser.date(from: t), date >= now.addingTimeInterval(-3600) else { continue }
            guard i < r.hourly.temperature_2m.count, i < r.hourly.weather_code.count else { break }
            let (hourIcon, _) = Self.symbol(for: r.hourly.weather_code[i])
            hourly.append(.obj([
                ("timeFormatted", .string(Fmt.hour(date, timezone: config.timezone))),
                ("icon", .string(hourIcon)),
                ("tempFormatted", .string(Fmt.temp(r.hourly.temperature_2m[i]))),
            ]))
            if hourly.count >= 24 { break }
        }

        // Home/Current toggle: the active mode is rendered in accent.
        let atHome = config.weatherAtHome
        return .obj([
            ("locationName", .string(config.locationName ?? "Home")),
            ("tempFormatted", .string(Fmt.temp(r.current.temperature_2m))),
            ("condition", .string(condition)),
            ("icon", .string(icon)),
            ("hiFormatted", .string("H:\(Fmt.temp(hi))")),
            ("loFormatted", .string("L:\(Fmt.temp(lo))")),
            ("homeColor", .string(atHome ? "$accent" : "$textSecondary")),
            ("currentColor", .string(atHome ? "$textSecondary" : "$accent")),
            ("hourly", .array(hourly)),
        ])
    }

    public func stub(config: AppConfig) -> JSONValue {
        .obj([
            ("locationName", .string(config.locationName ?? "Home")),
            ("tempFormatted", .string("62°")),
            ("condition", .string("Partly Cloudy")),
            ("icon", .string("cloud.sun.fill")),
            ("hiFormatted", .string("H:68°")),
            ("loFormatted", .string("L:54°")),
            ("homeColor", .string(config.weatherAtHome ? "$accent" : "$textSecondary")),
            ("currentColor", .string(config.weatherAtHome ? "$textSecondary" : "$accent")),
            ("hourly", .array([
                .obj([("timeFormatted", .string("2PM")), ("icon", .string("cloud.sun.fill")), ("tempFormatted", .string("62°"))]),
                .obj([("timeFormatted", .string("3PM")), ("icon", .string("sun.max.fill")), ("tempFormatted", .string("63°"))]),
                .obj([("timeFormatted", .string("4PM")), ("icon", .string("sun.max.fill")), ("tempFormatted", .string("62°"))]),
                .obj([("timeFormatted", .string("5PM")), ("icon", .string("cloud.sun.fill")), ("tempFormatted", .string("60°"))]),
                .obj([("timeFormatted", .string("6PM")), ("icon", .string("cloud.fill")), ("tempFormatted", .string("58°"))]),
            ])),
        ])
    }

    /// WMO weather code -> (SF Symbol name, human condition).
    static func symbol(for code: Int) -> (String, String) {
        switch code {
        case 0: return ("sun.max.fill", "Clear")
        case 1, 2: return ("cloud.sun.fill", "Partly Cloudy")
        case 3: return ("cloud.fill", "Overcast")
        case 45, 48: return ("cloud.fog.fill", "Fog")
        case 51, 53, 55, 56, 57: return ("cloud.drizzle.fill", "Drizzle")
        case 61, 63, 65, 66, 67, 80, 81, 82: return ("cloud.rain.fill", "Rain")
        case 71, 73, 75, 77, 85, 86: return ("cloud.snow.fill", "Snow")
        case 95, 96, 99: return ("cloud.bolt.fill", "Thunderstorms")
        default: return ("cloud.fill", "Cloudy")
        }
    }
}
