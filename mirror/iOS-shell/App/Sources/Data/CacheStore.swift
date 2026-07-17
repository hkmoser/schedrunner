import Foundation

/// Persists fetched manifests to disk so the app paints instantly from cache on launch and
/// works fully offline — not just the main dashboard but EVERY page that's been visited or
/// prefetched (mirrors the web PWA's multi-page cache). Stored in the App Group container
/// when available (shared with the widget/Live Activity), else the app's Caches directory.
struct CacheStore {
    struct Cached: Codable {
        var path: String
        var manifest: Manifest
        var fetchedAt: Date
    }

    private let dir: URL?

    init() {
        let appGroup = Bundle.main.object(forInfoDictionaryKey: "AppGroupID") as? String
        let base = appGroup.flatMap {
            FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: $0)
        } ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
        let dir = base?.appendingPathComponent("pages", isDirectory: true)
        if let dir {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        self.dir = dir
    }

    /// Stable, process-independent file name for a page path. (Swift's `hashValue` is seeded
    /// per launch, so it CAN'T be used for an on-disk key — use FNV-1a, which is stable.)
    private func fileURL(for path: String) -> URL? {
        guard let dir else { return nil }
        var hash: UInt64 = 1_469_598_103_934_665_603 // FNV-1a offset basis
        for byte in path.utf8 { hash = (hash ^ UInt64(byte)) &* 1_099_511_628_211 }
        return dir.appendingPathComponent(String(hash, radix: 16) + ".json")
    }

    /// Save any page by its path. The dashboard is just path "/dashboard".
    func save(_ path: String, _ manifest: Manifest) {
        guard let url = fileURL(for: path) else { return }
        let cached = Cached(path: path, manifest: manifest, fetchedAt: Date())
        if let data = try? JSONEncoder().encode(cached) {
            try? data.write(to: url, options: .atomic)
        }
    }

    /// Convenience for the main dashboard (kept so BackgroundRefresh stays unchanged).
    func save(_ manifest: Manifest) { save("/dashboard", manifest) }

    /// Load one cached page by path.
    func load(_ path: String) -> Cached? {
        guard let url = fileURL(for: path), let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(Cached.self, from: data)
    }

    /// Convenience for the main dashboard.
    func load() -> Cached? { load("/dashboard") }

    /// Wipe every cached page (the "Hard refresh" action).
    func clear() {
        guard let dir else { return }
        try? FileManager.default.removeItem(at: dir)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    /// Cached-page count + total bytes on disk, for the drawer's offline-cache status line.
    func stats() -> (pages: Int, bytes: Int) {
        guard let dir,
              let urls = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey])
        else { return (0, 0) }
        let jsons = urls.filter { $0.pathExtension == "json" }
        let bytes = jsons.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
        return (jsons.count, bytes)
    }

    /// Rehydrate every cached page (used on launch so previously-seen tabs open instantly
    /// and work offline before any network call returns).
    func loadAll() -> [Cached] {
        guard let dir,
              let urls = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        else { return [] }
        return urls.compactMap { url in
            guard url.pathExtension == "json", let data = try? Data(contentsOf: url) else { return nil }
            return try? JSONDecoder().decode(Cached.self, from: data)
        }
    }
}
