import Foundation
import Vapor

/// Resolves the effective location + timezone for the dashboard. If lat/lon are
/// configured explicitly they win; otherwise we IP-geolocate the server's network
/// ("my current location") and adopt its timezone too. The result is cached for a
/// day since a stationary Mac mini's location rarely changes.
public actor LocationResolver {
    private var cached: (config: AppConfig, at: Date)?
    private let ttl: TimeInterval = 24 * 60 * 60
    private var nameCache: [String: String] = [:]

    private struct GeoResponse: Decodable {
        let latitude: Double?
        let longitude: Double?
        let city: String?
        let region: String?
        let timezone: String?
    }

    private struct ReverseGeo: Decodable {
        let city: String?
        let locality: String?
        let principalSubdivision: String?
    }

    /// Returns a copy of `config` with concrete latitude/longitude/locationName/timezone.
    public func effective(_ config: AppConfig, client: Client, logger: Logger) async -> AppConfig {
        // Explicit coordinates (from the device or env): use them, and derive a
        // human place name from them when one wasn't pinned via DASHBOARD_LOCATION.
        if let lat = config.latitude, let lon = config.longitude {
            var out = config
            out.latitude = lat
            out.longitude = lon
            if let name = config.locationName {
                out.locationName = name
            } else {
                out.locationName = await reverseGeocode(lat: lat, lon: lon, client: client, logger: logger) ?? "Current Location"
            }
            return out
        }

        if let cached, Date().timeIntervalSince(cached.at) < ttl {
            // Location is cached, but weatherAtHome is request-specific (the ?loc toggle),
            // so carry it from this request rather than the cached value.
            var out = cached.config
            out.weatherAtHome = config.weatherAtHome
            return out
        }

        var out = config
        do {
            let geo = try await client.getJSON("https://ipapi.co/json/", as: GeoResponse.self)
            if let lat = geo.latitude, let lon = geo.longitude {
                out.latitude = lat
                out.longitude = lon
                out.locationName = config.locationName ?? geo.city ?? "Current Location"
                // Only adopt the geo timezone if one wasn't pinned (env or client).
                if !config.timezonePinned, let tz = geo.timezone, TimeZone(identifier: tz) != nil {
                    out.timezone = tz
                }
                cached = (out, Date())
                return out
            }
        } catch {
            logger.warning("IP geolocation failed: \(error); falling back to default location")
        }

        // Fallback so weather still renders.
        out.latitude = out.latitude ?? 37.7749
        out.longitude = out.longitude ?? -122.4194
        out.locationName = out.locationName ?? "Home"
        return out
    }

    /// Coordinates -> city name via BigDataCloud's free, keyless reverse geocoder.
    /// Cached per ~0.001° so repeated requests don't re-call it.
    private func reverseGeocode(lat: Double, lon: Double, client: Client, logger: Logger) async -> String? {
        let key = String(format: "%.3f,%.3f", lat, lon)
        if let cachedName = nameCache[key] { return cachedName }
        let url = "https://api.bigdatacloud.net/data/reverse-geocode-client"
            + "?latitude=\(lat)&longitude=\(lon)&localityLanguage=en"
        do {
            let r = try await client.getJSON(url, as: ReverseGeo.self)
            let name = [r.city, r.locality, r.principalSubdivision]
                .compactMap { $0 }
                .first { !$0.isEmpty }
            if let name {
                nameCache[key] = name
                return name
            }
        } catch {
            logger.warning("reverse geocoding failed: \(error)")
        }
        return nil
    }
}

