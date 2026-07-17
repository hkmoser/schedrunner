import Foundation
import Vapor
#if canImport(Glibc)
import Glibc      // kill()
#elseif canImport(Darwin)
import Darwin
#endif

/// Deploy status, surfaced in the app so you can see from your phone the health of each
/// pipeline stage (Merge → CI → Server → iOS app) without SSHing to the Mac.
/// Reads local deploy artifacts the server can access directly (`build/.auto-deploy-status`,
/// `build/.ios-build-status`, the build logs) + git HEAD. CI + shippable branches come from
/// the sidecar's /deploy_repo (GitHub) — best-effort, so the page renders if sidecar is down.
public struct DeployProvider: DataProvider {
    public let key = "deploy"
    public let ttl: TimeInterval = 10   // short — this is live status, not cached content
    public init() {}

    public func cacheKey(_ config: AppConfig) -> String { "deploy" }

    public func fetch(client: Client, config: AppConfig, logger: Logger) async throws -> JSONValue {
        var pairs = build()
        if let repo = try? await client.getJSON("\(config.bqSidecarURL)/deploy_repo", as: JSONValue.self),
           case .object(let obj) = repo {
            for k in ["ciFormatted", "ciDirection", "ciStages", "ciRunUrl", "ciRunLinkFormatted",
                      "ciLogFormatted", "ciLogLabel", "shipItems", "shipLabel"] where obj[k] != nil {
                pairs.append((k, obj[k]!))
            }
        } else {
            pairs.append(("shipLabel", .string("CI / branches unavailable (sidecar down)")))
        }
        return .obj(pairs)
    }
    public func stub(config: AppConfig) -> JSONValue { .obj(build()) }

    // MARK: - filesystem / git helpers
    private func repoRoot() -> String {
        if let r = Environment.get("DASHBOARD_REPO"), !r.isEmpty { return r }
        if let w = Environment.get("WEB_DIST"), let range = w.range(of: "/Web/dist", options: .backwards) {
            return String(w[..<range.lowerBound])
        }
        return FileManager.default.currentDirectoryPath
    }
    private func read(_ path: String) -> String? { try? String(contentsOfFile: path, encoding: .utf8) }
    private func mtime(_ path: String) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: path))?[.modificationDate] as? Date
    }
    private func tail(_ path: String, _ n: Int) -> String {
        guard let s = read(path) else { return "" }
        return s.split(separator: "\n", omittingEmptySubsequences: false).suffix(n).joined(separator: "\n")
    }
    private func gitHead(_ repo: String) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        p.arguments = ["-C", repo, "rev-parse", "--short", "HEAD"]
        let out = Pipe(); p.standardOutput = out; p.standardError = Pipe()
        do { try p.run(); p.waitUntilExit() } catch { return "" }
        let d = out.fileHandleForReading.readDataToEndOfFile()
        return String(data: d, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
    private func ago(_ secs: Int) -> String {
        if secs < 60 { return "\(secs)s ago" }
        if secs < 3600 { return "\(secs / 60)m ago" }
        if secs < 86400 { return "\(secs / 3600)h ago" }
        return "\(secs / 86400)d ago"
    }

    // MARK: - build the local manifest data
    private func build() -> [(String, JSONValue)] {
        let repo = repoRoot()
        let dir = repo + "/build"
        let now = Date()
        let deployed = Environment.get("DASHBOARD_BUILD") ?? "dev"
        let head = gitHead(repo)

        // ── Stage 3: Server ──────────────────────────────────────────────────────────────
        // Is the running binary the checked-out code? The auto-deploy hook writes its status
        // to .auto-deploy-status (RUNNING / SUCCESS / FAILED / CURRENT / WARN / never run).
        let serverCurrent = !head.isEmpty && head == deployed
        let autoRaw = (read(dir + "/.auto-deploy-status") ?? "never run").trimmingCharacters(in: .whitespacesAndNewlines)

        // One badge that answers "what is the server doing right now?"
        let serverBadge: String
        let serverBadgeDir: String
        if autoRaw.hasPrefix("RUNNING") {
            // Hook is actively building — wrote RUNNING at the start of the compile.
            serverBadge = "Building…"; serverBadgeDir = "#6ea8fe"
        } else if serverCurrent {
            serverBadge = "✓ Current"; serverBadgeDir = "up"
        } else if autoRaw.hasPrefix("FAILED") {
            // Build error — old binary intentionally kept running.
            serverBadge = "✗ Build failed"; serverBadgeDir = "down"
        } else if !head.isEmpty {
            // Hook hasn't run since the last pull (or produced a WARN mismatch).
            serverBadge = "⚠ Stale"; serverBadgeDir = "down"
        } else {
            serverBadge = "Unknown"; serverBadgeDir = "#9aa4c4"
        }

        // Human-readable deploy status line for the body.
        let autoLabel = "Last deploy: \(autoRaw)"

        // Deploy log (auto-deploy.log or deploy-kick.log, whichever is newer).
        let deployLogPaths = [dir + "/deploy-kick.log", dir + "/auto-deploy.log"]
        let newestDeployLog = deployLogPaths
            .filter { FileManager.default.fileExists(atPath: $0) }
            .max(by: { (mtime($0) ?? .distantPast) < (mtime($1) ?? .distantPast) })
        let deployLog = newestDeployLog.map { tail($0, 60) } ?? ""
        let deployLogAge = newestDeployLog.flatMap { mtime($0) }.map { ago(Int(now.timeIntervalSince($0))) } ?? ""
        let deployLogLabel = "Server deploy log" + (deployLogAge.isEmpty ? "" : " · \(deployLogAge)")

        // Inline error snippet: last 20 lines of the deploy log shown inline in the Server card
        // when the hook failed, so the Swift error is visible at a glance without scrolling.
        var deployLogSnippetItems: [JSONValue] = []
        if autoRaw.hasPrefix("FAILED") {
            let snippet = newestDeployLog.map { tail($0, 20) } ?? ""
            if !snippet.isEmpty {
                deployLogSnippetItems = [.obj([("snippetFormatted", .string(snippet))])]
            }
        }

        // Redeploy button + remedy text — shown only when there's something to act on.
        let serverNeedsAction = (!serverCurrent && !head.isEmpty) || autoRaw.hasPrefix("FAILED")
        var redeployActions: [JSONValue] = []
        var remedyFormatted = ""
        if serverNeedsAction {
            redeployActions = [.obj([("labelFormatted", .string("Redeploy now"))])]
            if autoRaw.hasPrefix("FAILED") {
                remedyFormatted = "Build failed — fix the error above, then tap Redeploy. The old server keeps running until a clean build lands."
            } else {
                remedyFormatted = "Tap Redeploy to rebuild from the checked-out commit (\(head))."
            }
        }

        // ── Stage 4: iOS app ─────────────────────────────────────────────────────────────
        let iosRaw = (read(dir + "/.ios-build-status") ?? "never run").trimmingCharacters(in: .whitespacesAndNewlines)
        let iosLog = tail(dir + "/ios-autodeploy.log", 80)
        let codesignBlocked = iosLog.contains("errSecInternalComponent")
            || iosLog.contains("Command CodeSign failed")
            || iosLog.contains("no stored password")
        var iosStatus = "Idle", iosDir = "#9aa4c4", iosDetail = ""
        if iosRaw.hasPrefix("RUNNING") {
            var pid: Int32?
            if let r = iosRaw.range(of: "pid ") {
                pid = Int32(String(iosRaw[r.upperBound...].prefix(while: { $0.isNumber })))
            }
            let logIdle = mtime(dir + "/ios-autodeploy.log").map { Int(now.timeIntervalSince($0)) } ?? 999_999
            let archIdle = mtime(dir + "/xcodebuild-archive.log").map { Int(now.timeIntervalSince($0)) } ?? 999_999
            let idle = min(logIdle, archIdle)
            if let pid, kill(pid, 0) == 0 {
                if idle > 900 {
                    iosStatus = "Stuck?"; iosDir = "down"
                    iosDetail = "building (pid \(pid)) but the log hasn't advanced in \(ago(idle)) — likely a codesign/provisioning or device-registration hang"
                } else {
                    iosStatus = "Building"; iosDir = "#6ea8fe"
                    iosDetail = "in progress (pid \(pid)) · log advanced \(ago(idle))"
                }
            } else {
                iosStatus = "Died"; iosDir = "down"
                iosDetail = "status says RUNNING but the process is gone — the build crashed without recording a result"
            }
        } else if iosRaw.hasPrefix("SUCCESS") {
            iosStatus = "Update ready"; iosDir = "up"
            iosDetail = "OTA published — tap Install to put it on the phone. (\(iosRaw))"
        } else if iosRaw.hasPrefix("FAILED") {
            iosStatus = "Failed"; iosDir = "down"
            iosDetail = codesignBlocked
                ? "codesign can't access the signing key — the Mac login password isn't stored. Set it in Settings → Deploy → \"Mac login password\" (or run `make ios-keychain`), then Redeploy. (\(iosRaw))"
                : iosRaw
        } else {
            iosStatus = "Idle"; iosDir = "#9aa4c4"; iosDetail = iosRaw
        }

        let iosNeedsAttention = ["Failed", "Stuck?", "Died"].contains(iosStatus)
        let deployIosOn = ["1", "true", "yes", "on"].contains((Environment.get("DEPLOY_IOS") ?? "").lowercased())

        // iOS OTA install button (only when a version.json exists from a successful build).
        var installActions: [JSONValue] = []
        if let vj = read(dir + "/ipa/version.json"),
           let d = vj.data(using: .utf8),
           let any = try? JSONSerialization.jsonObject(with: d),
           let obj = any as? [String: Any],
           let url = obj["installURL"] as? String, !url.isEmpty {
            let ver = (obj["version"] as? String) ?? ""
            let bn = (obj["build"] as? String) ?? ""
            installActions = [.obj([
                ("labelFormatted", .string("Install iOS app" + (ver.isEmpty ? "" : " · v\(ver)" + (bn.isEmpty ? "" : " (build \(bn))")))),
                ("installURL", .string(url)),
            ])]
        }

        // ── Headline ─────────────────────────────────────────────────────────────────────
        // One-liner that answers "is everything OK?" at the top of the screen.
        let headline: String, headlineDir: String
        if head.isEmpty {
            headline = "Running build \(deployed) — git HEAD unknown"; headlineDir = "#9aa4c4"
        } else if serverCurrent && !iosNeedsAttention {
            headline = "✓ All up to date — server and iOS on \(deployed)"; headlineDir = "up"
        } else if autoRaw.hasPrefix("RUNNING") {
            headline = "Building… server is rebuilding from \(head)"; headlineDir = "#6ea8fe"
        } else if autoRaw.hasPrefix("FAILED") {
            headline = "✗ Server build failed — running \(deployed), latest is \(head)"; headlineDir = "down"
        } else if !serverCurrent {
            headline = "⚠ Server stale — running \(deployed), waiting to rebuild to \(head)"; headlineDir = "down"
        } else {
            headline = "⚠ iOS build needs attention"; headlineDir = "down"
        }

        return [
            ("title", .string("Deploy")),
            ("subtitleFormatted", .string("Merge  →  CI  →  Server  →  iOS app")),
            ("headlineFormatted", .string(headline)),
            ("headlineDirection", .string(headlineDir)),

            // Stage 3 · Server card
            ("serverBadgeFormatted", .string(serverBadge)),
            ("serverBadgeDirection", .string(serverBadgeDir)),
            ("autoDeployFormatted", .string(autoLabel)),
            ("headFormatted", .string(head.isEmpty ? "HEAD: unknown" : "checked-out HEAD: \(head)")),
            ("remedyFormatted", .string(remedyFormatted)),
            ("redeployActions", .array(redeployActions)),
            ("deployLogSnippetItems", .array(deployLogSnippetItems)),

            // Stage 4 · iOS app card
            ("iosStatusFormatted", .string(iosStatus)),
            ("iosDirection", .string(iosDir)),
            ("iosDetailFormatted", .string(iosDetail)),
            ("iosAutoBuildFormatted", .string(deployIosOn ? "iOS auto-build: ON" : "iOS auto-build: OFF (native changes won't rebuild)")),
            ("installActions", .array(installActions)),
            ("iosNeedsRedeploy", .bool(iosNeedsAttention && !serverNeedsAction)),

            // Logs (always shown at the bottom for debugging)
            ("deployLogLabel", .string(deployLogLabel)),
            ("deployLogFormatted", .string(deployLog.isEmpty ? "(no deploy run recorded yet — tap Redeploy to run one)" : deployLog)),
            ("logLabel", .string("iOS build log (last 80 lines)")),
            ("logFormatted", .string(iosLog.isEmpty ? "(no iOS build log yet)" : iosLog)),
        ]
    }
}
