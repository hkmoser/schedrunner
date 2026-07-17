import WidgetKit
import SwiftUI
import AppIntents

// Refresh cadence for the two dev-focused widgets.
private let deployRefresh: TimeInterval = 60       // Deploy: check frequently — deploy can complete quickly
private let healthRefresh: TimeInterval = 5 * 60   // Health: provider errors move slowly

// MARK: - Color helper (server direction strings → SwiftUI Color)

private func widgetColor(_ dir: String) -> Color {
    switch dir {
    case "up":     return .green
    case "down":   return .red
    case "#6ea8fe": return .blue
    default:       return .secondary
    }
}

// MARK: - 4) Deploy — pipeline status with failure card + actions

struct DeployEntry: TimelineEntry {
    let date: Date
    let payload: WDeployPayload?
}

struct DeployTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> DeployEntry { DeployEntry(date: Date(), payload: nil) }
    func getSnapshot(in context: Context, completion: @escaping (DeployEntry) -> Void) {
        completion(DeployEntry(date: Date(), payload: nil))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<DeployEntry>) -> Void) {
        Task {
            let p: WDeployPayload? = await WidgetAPI.get("/widget/deploy")
            let entry = DeployEntry(date: Date(), payload: p)
            completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(deployRefresh))))
        }
    }
}

struct DeployWidgetView: View {
    var entry: DeployEntry
    @Environment(\.widgetFamily) private var family

    private var p: WDeployPayload? { entry.payload }

    // Two-dot status row: server + iOS badges side by side.
    @ViewBuilder private var statusRow: some View {
        HStack(spacing: 12) {
            Label {
                Text(p?.serverBadge ?? "Server")
                    .font(.caption2)
                    .foregroundStyle(widgetColor(p?.serverDirection ?? ""))
            } icon: {
                Image(systemName: "server.rack")
                    .foregroundStyle(widgetColor(p?.serverDirection ?? ""))
            }
            Label {
                Text(p?.iosBadge ?? "iOS")
                    .font(.caption2)
                    .foregroundStyle(widgetColor(p?.iosDirection ?? ""))
            } icon: {
                Image(systemName: "iphone")
                    .foregroundStyle(widgetColor(p?.iosDirection ?? ""))
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            // Header
            HStack(spacing: 6) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .foregroundStyle(widgetColor(p?.headlineDirection ?? ""))
                Text("Deploy").font(.headline)
                Spacer()
                if let sha = p?.buildSHA, !sha.isEmpty {
                    Text(sha).font(.caption2).foregroundStyle(.secondary)
                }
            }

            if family == .systemSmall {
                // Small: headline + two status badges
                Spacer()
                Text(p?.headline ?? "Loading…")
                    .font(.caption)
                    .foregroundStyle(widgetColor(p?.headlineDirection ?? ""))
                    .lineLimit(2)
                Spacer()
                statusRow
            } else {
                // Medium: show failure card when broken, or clean OK state
                let stage = p?.failureStage ?? ""
                if !stage.isEmpty {
                    // Failure card
                    Spacer()
                    Text(stage == "server" ? "Server build failed" : "iOS build needs attention")
                        .font(.subheadline).fontWeight(.semibold)
                        .foregroundStyle(.red)
                    Text(p?.failureDetail ?? "")
                        .font(.caption2).foregroundStyle(.secondary).lineLimit(3)
                    Spacer()
                    HStack(spacing: 8) {
                        if p?.canRedeploy == true {
                            Button(intent: RedeployIntent()) {
                                Label("Redeploy", systemImage: "arrow.triangle.2.circlepath")
                                    .font(.caption2).bold()
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.blue)
                        }
                        if let url = p?.installURL, !url.isEmpty, stage == "ios",
                           let dest = URL(string: url) {
                            Link(destination: dest) {
                                Label("Install", systemImage: "arrow.down.circle")
                                    .font(.caption2).bold()
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.green)
                        }
                    }
                } else {
                    // OK state: headline + status row
                    Spacer()
                    Text(p?.headline ?? "Loading…")
                        .font(.subheadline)
                        .foregroundStyle(widgetColor(p?.headlineDirection ?? ""))
                        .lineLimit(2)
                    Spacer()
                    statusRow
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct DeployWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "DeployWidget", provider: DeployTimelineProvider()) { entry in
            DeployWidgetView(entry: entry)
                .padding(12)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Deploy")
        .description("Deploy pipeline status: server + iOS build health.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

// MARK: - 5) Server Health — build SHA + provider error count

struct HealthEntry: TimelineEntry {
    let date: Date
    let payload: WHealthPayload?
}

struct HealthTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> HealthEntry { HealthEntry(date: Date(), payload: nil) }
    func getSnapshot(in context: Context, completion: @escaping (HealthEntry) -> Void) {
        completion(HealthEntry(date: Date(), payload: nil))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<HealthEntry>) -> Void) {
        Task {
            let p: WHealthPayload? = await WidgetAPI.get("/widget/health")
            let entry = HealthEntry(date: Date(), payload: p)
            completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(healthRefresh))))
        }
    }
}

struct ServerHealthWidgetView: View {
    var entry: HealthEntry
    @Environment(\.widgetFamily) private var family

    private var p: WHealthPayload? { entry.payload }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "chart.bar.fill")
                    .foregroundStyle(widgetColor(p?.direction ?? ""))
                Text("Health").font(.headline)
                Spacer()
                if let build = p?.build {
                    Text(build).font(.caption2).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Text(p?.statusFormatted ?? "Loading…")
                .font(.subheadline)
                .foregroundStyle(widgetColor(p?.direction ?? ""))
            if family == .systemMedium, let providers = p?.providers, !providers.isEmpty {
                Divider()
                ForEach(providers.prefix(6), id: \.name) { item in
                    HStack {
                        Image(systemName: item.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(item.ok ? Color.green : Color.red)
                            .font(.caption2)
                        Text(item.name).font(.caption2).lineLimit(1)
                        Spacer()
                        Text(item.ago).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct ServerHealthWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ServerHealthWidget", provider: HealthTimelineProvider()) { entry in
            ServerHealthWidgetView(entry: entry)
                .padding(12)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Server Health")
        .description("Live server health: build SHA and provider status.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

// MARK: - 6) Single Repo — configurable; user picks a repo on the widget back

struct SingleRepoEntry: TimelineEntry {
    let date: Date
    let configuration: RepoSelectionIntent
    let payload: WRepoPayload?
}

struct SingleRepoTimelineProvider: AppIntentTimelineProvider {
    typealias Entry = SingleRepoEntry
    typealias Intent = RepoSelectionIntent

    func placeholder(in context: Context) -> SingleRepoEntry {
        SingleRepoEntry(date: Date(), configuration: RepoSelectionIntent(), payload: nil)
    }
    func snapshot(for configuration: RepoSelectionIntent, in context: Context) async -> SingleRepoEntry {
        SingleRepoEntry(date: Date(), configuration: configuration, payload: nil)
    }
    func timeline(for configuration: RepoSelectionIntent, in context: Context) async -> Timeline<SingleRepoEntry> {
        let name = configuration.repo?.id ?? ""
        let path = name.isEmpty ? "/widget/repo" : "/widget/repo?name=\(name.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? name)"
        let p: WRepoPayload? = await WidgetAPI.get(path)
        let entry = SingleRepoEntry(date: Date(), configuration: configuration, payload: p)
        return Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(5 * 60)))
    }
}

struct SingleRepoWidgetView: View {
    var entry: SingleRepoEntry
    @Environment(\.widgetFamily) private var family

    private var p: WRepoPayload? { entry.payload }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "chevron.left.forwardslash.chevron.right")
                    .foregroundStyle(.blue)
                Text(p?.name ?? entry.configuration.repo?.name ?? "Repo")
                    .font(.headline).lineLimit(1)
                Spacer()
            }
            if let branch = p?.branch, !branch.isEmpty {
                Text(branch).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Label {
                        Text(p?.gitFormatted ?? "—")
                            .font(.caption)
                            .foregroundStyle(widgetColor(p?.gitDirection ?? ""))
                    } icon: {
                        Image(systemName: "arrow.branch").font(.caption2)
                    }
                    Label {
                        Text(p?.deployFormatted ?? "—")
                            .font(.caption)
                            .foregroundStyle(widgetColor(p?.deployDirection ?? ""))
                    } icon: {
                        Image(systemName: "arrow.up.circle").font(.caption2)
                    }
                }
                Spacer()
            }
            if family == .systemMedium, let last = p?.lastFormatted, !last.isEmpty {
                Text(last).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct SingleRepoWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "SingleRepoWidget", intent: RepoSelectionIntent.self,
                               provider: SingleRepoTimelineProvider()) { entry in
            SingleRepoWidgetView(entry: entry)
                .padding(12)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Repo Status")
        .description("Git and deploy status for one repository.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

// MARK: - 7) Single Log — configurable; user picks a log file on the widget back

struct SingleLogEntry: TimelineEntry {
    let date: Date
    let configuration: LogSelectionIntent
    let payload: WLogPayload?
}

struct SingleLogTimelineProvider: AppIntentTimelineProvider {
    typealias Entry = SingleLogEntry
    typealias Intent = LogSelectionIntent

    func placeholder(in context: Context) -> SingleLogEntry {
        SingleLogEntry(date: Date(), configuration: LogSelectionIntent(), payload: nil)
    }
    func snapshot(for configuration: LogSelectionIntent, in context: Context) async -> SingleLogEntry {
        SingleLogEntry(date: Date(), configuration: configuration, payload: nil)
    }
    func timeline(for configuration: LogSelectionIntent, in context: Context) async -> Timeline<SingleLogEntry> {
        let name = configuration.logFile?.id ?? ""
        let path = name.isEmpty ? "/widget/log" : "/widget/log?name=\(name.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? name)"
        let p: WLogPayload? = await WidgetAPI.get(path)
        let entry = SingleLogEntry(date: Date(), configuration: configuration, payload: p)
        return Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(5 * 60)))
    }
}

struct SingleLogWidgetView: View {
    var entry: SingleLogEntry
    @Environment(\.widgetFamily) private var family

    private var p: WLogPayload? { entry.payload }

    // Last non-empty line of the tail — the most recent log entry.
    private var lastLine: String {
        (p?.tail ?? "")
            .split(separator: "\n", omittingEmptySubsequences: true)
            .last.map(String.init) ?? ""
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "doc.text.fill").foregroundStyle(.orange)
                Text(p?.name ?? entry.configuration.logFile?.name ?? "Log")
                    .font(.headline).lineLimit(1)
                Spacer()
            }
            if let meta = p?.metaFormatted, !meta.isEmpty {
                Text(meta).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
            if family == .systemMedium {
                // Medium: show last few lines of the tail in a monospace block.
                let lines = (p?.tail ?? "")
                    .split(separator: "\n", omittingEmptySubsequences: true)
                    .suffix(4)
                    .joined(separator: "\n")
                Text(lines.isEmpty ? "(no log output)" : lines)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.primary)
                    .lineLimit(4)
            } else {
                // Small: just the last line.
                Text(lastLine.isEmpty ? "(no log output)" : lastLine)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct SingleLogWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "SingleLogWidget", intent: LogSelectionIntent.self,
                               provider: SingleLogTimelineProvider()) { entry in
            SingleLogWidgetView(entry: entry)
                .padding(12)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Log File")
        .description("Latest output from one log file.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
