import Foundation

/// Fetches manifests from the server over Tailscale HTTPS. Host + port come from Info.plist
/// (DashboardHost/DashboardPort), set via Config.xcconfig — a next-channel build talks to
/// :8443 instead of the default :443.
struct DashboardClient {
    let host: String
    let port: Int?   // nil = default 443

    init() {
        self.host = (Bundle.main.object(forInfoDictionaryKey: "DashboardHost") as? String) ?? ""
        let p = (Bundle.main.object(forInfoDictionaryKey: "DashboardPort") as? String).flatMap(Int.init)
        self.port = (p == nil || p == 443) ? nil : p
    }

    /// "host" or "host:8443" — for URLs built by string interpolation.
    private var authority: String { port.map { "\(host):\($0)" } ?? host }

    /// Main dashboard. Sends the device timezone so times render in the user's zone.
    func fetch() async throws -> Manifest {
        try await fetchPage("/dashboard")
    }

    /// Any server page path, e.g. "/screen/bigquery" or "/screen/docs?path=Home".
    func fetchPage(_ path: String) async throws -> Manifest {
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port
        var queryItems = [URLQueryItem(name: "tz", value: TimeZone.current.identifier)]
        // A page path may carry its own query (e.g. Docs ?path=…); keep them separate.
        if let q = path.firstIndex(of: "?") {
            components.path = String(path[..<q])
            let extra = URLComponents(string: "?" + path[path.index(after: q)...])?.queryItems ?? []
            queryItems.append(contentsOf: extra)
        } else {
            components.path = path
        }
        components.queryItems = queryItems
        guard let url = components.url else { throw URLError(.badURL) }

        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(Manifest.self, from: data)
    }

    /// The latest published native build (from `/app/version.json`) for the in-app update
    /// prompt. nil if unreachable / not published yet.
    struct AppVersionInfo: Decodable {
        let version: String
        let build: String
        let installURL: String
    }
    func appVersionInfo() async -> AppVersionInfo? {
        guard !host.isEmpty, let url = URL(string: "https://\(authority)/app/version.json") else { return nil }
        guard let (data, response) = try? await URLSession.shared.data(from: url),
              let http = response as? HTTPURLResponse, http.statusCode == 200 else { return nil }
        return try? JSONDecoder().decode(AppVersionInfo.self, from: data)
    }

    /// Register APNs tokens with the server so it can push log-failure alerts (`token`) and
    /// Live Activity updates (`liveActivityToken`). Best-effort; failures are ignored (we
    /// re-register next launch).
    func registerPush(token: String? = nil, liveActivityToken: String? = nil) async {
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port
        components.path = "/register_push"
        guard let url = components.url else { return }
        var payload: [String: String] = [:]
        if let token { payload["token"] = token }
        if let liveActivityToken { payload["liveActivityToken"] = liveActivityToken }
        guard !payload.isEmpty else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        _ = try? await URLSession.shared.data(for: request)
    }

    /// POST form items (e.g. config edits) to a server path like "/config".
    @discardableResult
    func post(_ path: String, items: [[String: String]]) async throws -> Data {
        var components = URLComponents()
        components.scheme = "https"
        components.host = host
        components.port = port
        // A submit url may carry its own query (e.g. /repos_pr?owner=…&branch=…) — keep it
        // separate from the path, which URLComponents.path can't contain.
        if let q = path.firstIndex(of: "?") {
            components.path = String(path[..<q])
            components.percentEncodedQuery = String(path[path.index(after: q)...])
        } else {
            components.path = path
        }
        guard let url = components.url else { throw URLError(.badURL) }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["items": items])
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return data
    }
}
