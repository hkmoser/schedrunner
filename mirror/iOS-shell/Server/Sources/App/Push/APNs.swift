import Foundation
import Vapor
import Crypto

/// APNs configuration from env (see Deploy/.env.example). All optional: when the key isn't
/// configured, push is simply disabled and the server runs exactly as before.
public struct APNsConfig: Sendable {
    public let keyP8: String      // contents of the AuthKey .p8 (PKCS#8 PEM)
    public let keyID: String      // the key's 10-char Key ID
    public let teamID: String     // your Apple Developer Team ID
    public let bundleID: String   // the app's bundle id (APNs topic)
    public let useSandbox: Bool   // APNS_ENV=sandbox → api.sandbox.push.apple.com

    public var host: String { useSandbox ? "api.sandbox.push.apple.com" : "api.push.apple.com" }

    /// Build from env, auto-deriving everything but the key so setup is just "provide the .p8":
    ///  - key:    APNS_KEY_P8 (inline) → APNS_KEY_PATH (file) → auto-detect an AuthKey_*.p8
    ///            dropped in ~/.config/dashboard/apns (or APNS_KEY_DIR).
    ///  - key id: APNS_KEY_ID → else parsed from the AuthKey_<ID>.p8 filename.
    ///  - team:   APNS_TEAM_ID → else the iOS build's DEVELOPMENT_TEAM.
    ///  - bundle: APNS_BUNDLE_ID → else PRODUCT_BUNDLE_IDENTIFIER → else com.joemoser.dashboard.
    /// Returns nil (push simply disabled) only if no key is found.
    public static func load() -> APNsConfig? {
        func env(_ k: String) -> String? { Environment.get(k).flatMap { $0.isEmpty ? nil : $0 } }

        var keyP8: String?
        var keyPath: String?
        if let inline = env("APNS_KEY_P8") {
            keyP8 = inline
        } else if let path = env("APNS_KEY_PATH") {
            keyPath = path; keyP8 = try? String(contentsOfFile: path, encoding: .utf8)
        } else if let found = findAuthKey() {
            keyPath = found; keyP8 = try? String(contentsOfFile: found, encoding: .utf8)
        }
        guard let raw = keyP8, !raw.isEmpty else { return nil }
        let p8 = normalizeP8(raw)

        guard let keyID = env("APNS_KEY_ID") ?? keyPath.flatMap(keyIDFromFilename), !keyID.isEmpty,
              let teamID = env("APNS_TEAM_ID") ?? env("DEVELOPMENT_TEAM") else { return nil }
        let bundleID = env("APNS_BUNDLE_ID") ?? env("PRODUCT_BUNDLE_IDENTIFIER") ?? "com.joemoser.dashboard"
        return APNsConfig(keyP8: p8, keyID: keyID, teamID: teamID, bundleID: bundleID,
                          useSandbox: env("APNS_ENV")?.lowercased() == "sandbox")
    }

    /// Canonicalize a pasted `.p8` into a PEM that swift-crypto will actually parse. People paste
    /// keys with the newlines collapsed to spaces (single-line text fields strip them), as literal
    /// `\n` escapes, or with no `-----BEGIN/END-----` armor at all. We pull out the base64 body —
    /// whatever survived — and re-emit canonical 64-column PKCS#8 PEM, so the key parses regardless
    /// of how it was pasted. Idempotent on an already-valid key.
    static func normalizeP8(_ raw: String) -> String {
        let unescaped = raw.replacingOccurrences(of: "\\n", with: "\n")
        let stripped = unescaped
            .replacingOccurrences(of: "-----BEGIN PRIVATE KEY-----", with: "")
            .replacingOccurrences(of: "-----END PRIVATE KEY-----", with: "")
        let b64 = String(String.UnicodeScalarView(
            stripped.unicodeScalars.filter { !CharacterSet.whitespacesAndNewlines.contains($0) }))
        guard !b64.isEmpty else { return unescaped }
        var lines: [String] = []
        var i = b64.startIndex
        while i < b64.endIndex {
            let j = b64.index(i, offsetBy: 64, limitedBy: b64.endIndex) ?? b64.endIndex
            lines.append(String(b64[i..<j])); i = j
        }
        return "-----BEGIN PRIVATE KEY-----\n" + lines.joined(separator: "\n") + "\n-----END PRIVATE KEY-----\n"
    }

    /// Look for an `AuthKey_*.p8` you simply dropped in a known dir (no config needed).
    private static func findAuthKey() -> String? {
        let fm = FileManager.default
        let home = fm.homeDirectoryForCurrentUser.path
        let dirs = [Environment.get("APNS_KEY_DIR"),
                    "\(home)/.config/dashboard/apns",
                    "\(home)/.config/dashboard"].compactMap { $0 }
        for dir in dirs {
            if let files = try? fm.contentsOfDirectory(atPath: dir),
               let f = files.first(where: { $0.hasPrefix("AuthKey_") && $0.hasSuffix(".p8") }) {
                return "\(dir)/\(f)"
            }
        }
        return nil
    }

    /// Human-readable report of what's resolved vs missing, for the /test_push diagnostic.
    public static func diagnose() -> String {
        func env(_ k: String) -> String? { Environment.get(k).flatMap { $0.isEmpty ? nil : $0 } }
        var parts: [String] = []
        var keyPath: String?
        var keyRaw: String?
        if let inline = env("APNS_KEY_P8") {
            keyRaw = inline
            parts.append("key=inline APNS_KEY_P8 ✓")
        } else if let p = env("APNS_KEY_PATH") {
            keyPath = p
            keyRaw = try? String(contentsOfFile: p, encoding: .utf8)
            parts.append("key=APNS_KEY_PATH \(FileManager.default.fileExists(atPath: p) ? "✓" : "✗ FILE MISSING")")
        } else if let f = findAuthKey() {
            keyPath = f
            keyRaw = try? String(contentsOfFile: f, encoding: .utf8)
            parts.append("key=auto-detected \(f) ✓")
        } else {
            parts.append("key=✗ NONE (paste APNS_KEY_P8 on Settings, or drop AuthKey_*.p8 in ~/.config/dashboard/apns)")
        }
        // The #1 silent failure: a key that's PRESENT but doesn't parse (pasting collapses the
        // .p8 newlines). Validate it the same way the client does, so the diagnosis is honest.
        if let raw = keyRaw, !raw.isEmpty {
            if (try? P256.Signing.PrivateKey(pemRepresentation: normalizeP8(raw))) == nil {
                parts.append("⚠️ key does NOT parse as an EC .p8 — re-paste the ENTIRE AuthKey_*.p8 (incl. the BEGIN/END lines)")
            }
        }
        let kid = env("APNS_KEY_ID") ?? keyPath.flatMap(keyIDFromFilename)
        parts.append("keyID=\(kid ?? "✗ MISSING (set APNS_KEY_ID — required when you PASTE the key)")")
        parts.append("teamID=\(env("APNS_TEAM_ID") ?? env("DEVELOPMENT_TEAM") ?? "✗ MISSING (set APNS_TEAM_ID)")")
        parts.append("APNS_ENV=\(env("APNS_ENV") ?? "production [set 'sandbox' for dev/OTA builds]")")
        return parts.joined(separator: " · ")
    }

    /// Apple names the key file AuthKey_<KEYID>.p8 — the Key ID is right there.
    private static func keyIDFromFilename(_ path: String) -> String? {
        let name = (path as NSString).lastPathComponent
        guard name.hasPrefix("AuthKey_"), name.hasSuffix(".p8") else { return nil }
        let id = name.dropFirst("AuthKey_".count).dropLast(".p8".count)
        return id.isEmpty ? nil : String(id)
    }
}

/// Minimal APNs HTTP/2 sender. Signs a short-lived ES256 JWT with the .p8 key (swift-crypto
/// P-256) and POSTs the payload via Vapor's HTTP client (AsyncHTTPClient negotiates HTTP/2,
/// which APNs requires). No extra dependency beyond what Vapor already pulls in. The JWT is
/// reused for ~50 min (APNs allows up to 1h, and rejects tokens refreshed too often).
public actor APNsClient {
    private let config: APNsConfig
    private let key: P256.Signing.PrivateKey
    private var cached: (jwt: String, made: Date)?

    public init?(_ config: APNsConfig) {
        guard let key = try? P256.Signing.PrivateKey(pemRepresentation: config.keyP8) else { return nil }
        self.config = config
        self.key = key
    }

    public enum PushType: String { case alert, liveactivity, background }

    private static func b64url(_ d: Data) -> String {
        d.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private func bearer() throws -> String {
        if let c = cached, Date().timeIntervalSince(c.made) < 50 * 60 { return c.jwt }
        let header = "{\"alg\":\"ES256\",\"kid\":\"\(config.keyID)\"}"
        let claims = "{\"iss\":\"\(config.teamID)\",\"iat\":\(Int(Date().timeIntervalSince1970))}"
        let signingInput = Self.b64url(Data(header.utf8)) + "." + Self.b64url(Data(claims.utf8))
        let signature = try key.signature(for: Data(signingInput.utf8))
        let jwt = signingInput + "." + Self.b64url(signature.rawRepresentation)  // r||s, exactly ES256
        cached = (jwt, Date())
        return jwt
    }

    /// Send a JSON payload to one device (or live-activity) token. Returns the APNs HTTP
    /// status code (2xx = delivered; 410/"BadDeviceToken" means the token is dead).
    @discardableResult
    public func send(_ payload: [String: Any], to token: String, type: PushType = .alert,
                     client: Client, logger: Logger) async -> Int {
        do {
            let jwt = try bearer()
            let topic = type == .liveactivity ? "\(config.bundleID).push-type.liveactivity" : config.bundleID
            let body = try JSONSerialization.data(withJSONObject: payload)
            let resp = try await client.post(URI(string: "https://\(config.host)/3/device/\(token)")) { req in
                req.headers.replaceOrAdd(name: .authorization, value: "bearer \(jwt)")
                req.headers.replaceOrAdd(name: "apns-topic", value: topic)
                req.headers.replaceOrAdd(name: "apns-push-type", value: type.rawValue)
                req.headers.replaceOrAdd(name: "apns-priority", value: type == .alert ? "10" : "5")
                req.body = ByteBuffer(data: body)
            }
            if resp.status.code < 200 || resp.status.code >= 300 {
                let reason = resp.body.map { String(buffer: $0) } ?? ""
                logger.warning("APNs \(resp.status.code) for token \(token.prefix(8))…: \(reason)")
            }
            return Int(resp.status.code)
        } catch {
            logger.warning("APNs send error: \(error)")
            return 0
        }
    }
}

/// Persists registered device tokens to a JSON file so they survive restarts. Two sets:
/// `alert` tokens (standard remote-notification tokens, used by the log-failure watcher) and
/// `liveActivity` tokens (per-activity push-to-update tokens for the Live Activity).
public actor DeviceTokenStore {
    private struct Persisted: Codable { var alert: [String]; var liveActivity: [String] }
    private let url: URL
    private var alert: Set<String> = []
    private var liveActivity: Set<String> = []

    public init(path: String) {
        self.url = URL(fileURLWithPath: path)
        if let data = try? Data(contentsOf: url), let p = try? JSONDecoder().decode(Persisted.self, from: data) {
            alert = Set(p.alert); liveActivity = Set(p.liveActivity)
        }
    }

    public func alertTokens() -> [String] { Array(alert) }
    public func liveActivityTokens() -> [String] { Array(liveActivity) }

    public func addAlert(_ t: String) { guard !t.isEmpty else { return }; alert.insert(t); persist() }
    public func addLiveActivity(_ t: String) { guard !t.isEmpty else { return }; liveActivity.insert(t); persist() }
    public func removeAlert(_ t: String) { alert.remove(t); persist() }
    public func removeLiveActivity(_ t: String) { liveActivity.remove(t); persist() }

    private func persist() {
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(Persisted(alert: Array(alert), liveActivity: Array(liveActivity))) {
            try? data.write(to: url, options: .atomic)
        }
    }
}

extension Application {
    private struct APNsClientKey: StorageKey { typealias Value = APNsClient }
    public var apns: APNsClient? {
        get { storage[APNsClientKey.self] }
        set { storage[APNsClientKey.self] = newValue }
    }

    private struct DeviceTokenStoreKey: StorageKey { typealias Value = DeviceTokenStore }
    public var deviceTokens: DeviceTokenStore? {
        get { storage[DeviceTokenStoreKey.self] }
        set { storage[DeviceTokenStoreKey.self] = newValue }
    }
}
