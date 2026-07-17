import Foundation
import WidgetKit
import AppIntents

// MARK: - Payloads (mirror the server's /widget/* compact JSON)

struct WShipItem: Codable, Hashable {
    let label: String
    let meta: String
    let shipPath: String
    let repo: String
}
struct WReposPayload: Codable { let items: [WShipItem] }
struct WActivityPayload: Codable {
    let title: String?       // header — "Activity", or "Home Lights" in disabled/masked mode
    let status: String; let place: String; let meta: String; let icon: String?
    let category: String?; let elapsed: String?; let clockRange: String?
    let startedAt: Double?   // unix seconds of the current segment start (0/nil = none)
}
struct WBalanceGroup: Codable, Hashable { let name: String; let total: String; let direction: String }
struct WBalancesPayload: Codable { let netFormatted: String; let asOfFormatted: String; let groups: [WBalanceGroup] }
struct WDeployPayload: Codable {
    let headline: String; let headlineDirection: String
    let serverBadge: String; let serverDirection: String
    let iosBadge: String; let iosDirection: String
    let failureStage: String; let failureDetail: String
    let canRedeploy: Bool; let installURL: String; let buildSHA: String
}
struct WHealthProvider: Codable, Hashable { let name: String; let ok: Bool; let ago: String }
struct WHealthPayload: Codable {
    let build: String; let statusFormatted: String; let direction: String
    let providers: [WHealthProvider]
}
struct WRepoPayload: Codable {
    let name: String; let branch: String
    let gitFormatted: String; let gitDirection: String
    let deployFormatted: String; let deployDirection: String
    let lastFormatted: String
}
struct WLogPayload: Codable { let name: String; let metaFormatted: String; let tail: String }
struct WListItem: Codable { let id: String; let name: String }

// MARK: - Networking (over Tailscale, to the Vapor server)

enum WidgetAPI {
    /// The Tailscale host, baked into the extension's Info.plist from Config.xcconfig.
    static func host() -> String {
        (Bundle.main.object(forInfoDictionaryKey: "DashboardHost") as? String) ?? ""
    }

    /// HTTPS port (nil = default 443). A next-channel build talks to :8443.
    static func port() -> Int? {
        let p = (Bundle.main.object(forInfoDictionaryKey: "DashboardPort") as? String).flatMap(Int.init)
        return (p == nil || p == 443) ? nil : p
    }

    /// "host" or "host:8443" — for URLs built by string interpolation.
    static func authority() -> String {
        port().map { "\(host()):\($0)" } ?? host()
    }

    /// GET a compact widget payload. nil on any failure (off Tailscale, server down) — the widget
    /// then keeps showing its last timeline.
    static func get<T: Decodable>(_ path: String) async -> T? {
        let h = host()
        guard !h.isEmpty, let url = URL(string: "https://\(authority())\(path)") else { return nil }
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    /// POST a ship action (create-PR-if-needed → merge → delete) using the item's ship path
    /// (e.g. "/repos_pr?owner=…&branch=…"). Returns whether the merge actually succeeded.
    @discardableResult
    static func ship(_ shipPath: String) async -> Bool {
        let h = host()
        guard !h.isEmpty else { return false }
        var comps = URLComponents()
        comps.scheme = "https"
        comps.host = h
        comps.port = port()
        if let q = shipPath.firstIndex(of: "?") {
            comps.path = String(shipPath[..<q])
            comps.percentEncodedQuery = String(shipPath[shipPath.index(after: q)...])
        } else {
            comps.path = shipPath
        }
        guard let url = comps.url else { return false }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["items": []])
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return false }
        // A ship reports its real outcome via statusDirection ("down" = failed) on a 200.
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let dir = obj["statusDirection"] as? String {
            return dir != "down"
        }
        return true
    }
}

// MARK: - AppEntity types for configurable widgets (iOS 17+)

struct RepoEntity: AppEntity {
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Repository")
    static var defaultQuery = RepoEntityQuery()
    var id: String
    var name: String
    var displayRepresentation: DisplayRepresentation { DisplayRepresentation(title: "\(name)") }
}
struct RepoEntityQuery: EntityQuery {
    func entities(for identifiers: [String]) async throws -> [RepoEntity] {
        let items: [WListItem]? = await WidgetAPI.get("/widget/repos_list")
        return (items ?? []).filter { identifiers.contains($0.id) }.map { RepoEntity(id: $0.id, name: $0.name) }
    }
    func suggestedEntities() async throws -> [RepoEntity] {
        let items: [WListItem]? = await WidgetAPI.get("/widget/repos_list")
        return (items ?? []).map { RepoEntity(id: $0.id, name: $0.name) }
    }
}
struct RepoSelectionIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Repository"
    static var description = IntentDescription("Which repository to display.")
    @Parameter(title: "Repository") var repo: RepoEntity?
}

struct LogEntity: AppEntity {
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Log File")
    static var defaultQuery = LogEntityQuery()
    var id: String
    var name: String
    var displayRepresentation: DisplayRepresentation { DisplayRepresentation(title: "\(name)") }
}
struct LogEntityQuery: EntityQuery {
    func entities(for identifiers: [String]) async throws -> [LogEntity] {
        let items: [WListItem]? = await WidgetAPI.get("/widget/logs_list")
        return (items ?? []).filter { identifiers.contains($0.id) }.map { LogEntity(id: $0.id, name: $0.name) }
    }
    func suggestedEntities() async throws -> [LogEntity] {
        let items: [WListItem]? = await WidgetAPI.get("/widget/logs_list")
        return (items ?? []).map { LogEntity(id: $0.id, name: $0.name) }
    }
}
struct LogSelectionIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Log File"
    static var description = IntentDescription("Which log file to display.")
    @Parameter(title: "Log File") var logFile: LogEntity?
}

// MARK: - One-tap Ship (interactive widget action; iOS 17+)

struct ShipIntent: AppIntent {
    static var title: LocalizedStringResource = "Ship branch / PR"
    static var isDiscoverable: Bool = false

    @Parameter(title: "Ship path") var shipPath: String

    init() {}
    init(shipPath: String) { self.shipPath = shipPath }

    func perform() async throws -> some IntentResult {
        _ = await WidgetAPI.ship(shipPath)
        // Refresh the To-Ship widget so the merged item drops off.
        WidgetCenter.shared.reloadTimelines(ofKind: "ReposWidget")
        return .result()
    }
}

// MARK: - One-tap Redeploy (kicks the deploy hook from the Deploy widget)

struct RedeployIntent: AppIntent {
    static var title: LocalizedStringResource = "Redeploy server"
    static var isDiscoverable: Bool = false

    init() {}

    func perform() async throws -> some IntentResult {
        _ = await WidgetAPI.ship("/deploy_kick")
        WidgetCenter.shared.reloadTimelines(ofKind: "DeployWidget")
        return .result()
    }
}
