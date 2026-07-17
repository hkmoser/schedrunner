import Foundation
import Vapor

/// Per-provider last-good cache. Keeps the dashboard resilient: a provider that
/// times out or errors never blanks its card — we serve the last good value with
/// a `stale` flag, and only fall back to stub data if we've never succeeded.
public actor ProviderCache {
    public enum Result {
        case fresh(JSONValue)
        case stale(JSONValue)
        case miss
    }

    private struct Entry {
        var value: JSONValue
        var fetchedAt: Date
    }

    private var entries: [String: Entry] = [:]
    private var lastOk: [String: Date] = [:]
    private var lastError: [String: String] = [:]

    public init() {}

    /// Return cached value if within `ttl`; otherwise attempt `refresh`. On refresh
    /// failure, return the last-good value (stale) or `.miss` if none exists.
    public func value(
        for key: String,
        ttl: TimeInterval,
        refresh: () async throws -> JSONValue
    ) async -> Result {
        if let e = entries[key], Date().timeIntervalSince(e.fetchedAt) < ttl {
            return .fresh(e.value)
        }
        do {
            let v = try await refresh()
            entries[key] = Entry(value: v, fetchedAt: Date())
            lastOk[key] = Date()
            lastError.removeValue(forKey: key)
            return .fresh(v)
        } catch {
            lastError[key] = "\(error)"   // surfaced at /healthz so a failing card is diagnosable
            if let e = entries[key] {
                return .stale(e.value)
            }
            return .miss
        }
    }

    /// The last refresh error per key (cleared on success), so /healthz shows WHY a card is
    /// serving stale/stub data — e.g. "Twelve Data error [401]: invalid api key".
    public func errorsSnapshot() -> [String: String] { lastError }

    /// Drop cached entries so the next `value(for:)` re-fetches fresh. Used after a ship merges a
    /// branch so the repos list / home banner immediately reflect it instead of waiting out the TTL.
    public func invalidate(_ keys: String...) {
        for k in keys { entries.removeValue(forKey: k) }
    }

    /// ISO-8601 timestamps of the last successful refresh per key, for /healthz.
    public func healthSnapshot() -> [String: String] {
        let fmt = ISO8601DateFormatter()
        var out: [String: String] = [:]
        for (k, d) in lastOk { out[k] = fmt.string(from: d) }
        return out
    }
}

extension Application {
    private struct ProviderCacheKey: StorageKey { typealias Value = ProviderCache }
    public var providerCache: ProviderCache {
        if let existing = storage[ProviderCacheKey.self] { return existing }
        let new = ProviderCache()
        storage[ProviderCacheKey.self] = new
        return new
    }
}
