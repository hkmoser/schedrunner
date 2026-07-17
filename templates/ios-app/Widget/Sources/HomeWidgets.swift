import WidgetKit
import SwiftUI
import AppIntents

// Widgets refresh on this cadence (iOS budgets actual delivery); a tap/ship reloads immediately.
private let refreshInterval: TimeInterval = 30 * 60

// MARK: - 1) To Ship — open PRs / unmerged branches with one-tap Ship

struct ReposEntry: TimelineEntry { let date: Date; let items: [WShipItem] }

struct ReposTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> ReposEntry { ReposEntry(date: Date(), items: []) }
    func getSnapshot(in context: Context, completion: @escaping (ReposEntry) -> Void) {
        completion(ReposEntry(date: Date(), items: []))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<ReposEntry>) -> Void) {
        Task {
            let payload: WReposPayload? = await WidgetAPI.get("/widget/repos")
            let entry = ReposEntry(date: Date(), items: payload?.items ?? [])
            completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(refreshInterval))))
        }
    }
}

struct ReposWidgetView: View {
    var entry: ReposEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "chevron.left.forwardslash.chevron.right")
                Text("To Ship").font(.headline)
                Spacer()
                if !entry.items.isEmpty {
                    Text("\(entry.items.count)").font(.caption).foregroundStyle(.secondary)
                }
            }
            if entry.items.isEmpty {
                Spacer()
                Text("Nothing to ship").font(.subheadline).foregroundStyle(.secondary)
                Spacer()
            } else {
                ForEach(entry.items.prefix(family == .systemLarge ? 6 : 3), id: \.self) { item in
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(item.label).font(.caption).lineLimit(1)
                            Text(item.repo).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer()
                        Button(intent: ShipIntent(shipPath: item.shipPath)) {
                            Text("Ship").font(.caption2).bold()
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.blue)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct ReposWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ReposWidget", provider: ReposTimelineProvider()) { entry in
            ReposWidgetView(entry: entry)
                .padding(12)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("To Ship")
        .description("Open PRs / branches with one-tap Ship.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

// MARK: - 2) Activity — latest status from the Activity page

// Activity should feel live — ask for a data refresh more often than the other widgets (iOS
// still budgets actual delivery; the elapsed time ticks locally regardless).
private let activityRefreshInterval: TimeInterval = 10 * 60

struct ActivityEntry: TimelineEntry {
    let date: Date; let title: String; let status: String; let place: String; let meta: String; let icon: String
    let category: String; let elapsed: String; let clockRange: String
    let startedAt: Date?   // current segment start → live-ticking elapsed
}

struct ActivityTimelineProvider: TimelineProvider {
    private func blank(_ place: String) -> ActivityEntry {
        ActivityEntry(date: Date(), title: "Activity", status: "", place: place, meta: "", icon: "location.fill",
                      category: "", elapsed: "", clockRange: "", startedAt: nil)
    }
    func placeholder(in context: Context) -> ActivityEntry { blank("Locating…") }
    func getSnapshot(in context: Context, completion: @escaping (ActivityEntry) -> Void) { completion(blank("Locating…")) }
    func getTimeline(in context: Context, completion: @escaping (Timeline<ActivityEntry>) -> Void) {
        Task {
            let p: WActivityPayload? = await WidgetAPI.get("/widget/activity")
            let started = p?.startedAt ?? 0
            let entry = ActivityEntry(date: Date(), title: p?.title ?? "Activity",
                                      status: p?.status ?? "", place: p?.place ?? "",
                                      meta: p?.meta ?? "", icon: p?.icon ?? "location.fill",
                                      category: p?.category ?? "", elapsed: p?.elapsed ?? "",
                                      clockRange: p?.clockRange ?? "",
                                      startedAt: started > 0 ? Date(timeIntervalSince1970: started) : nil)
            completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(activityRefreshInterval))))
        }
    }
}

struct ActivityWidgetView: View {
    var entry: ActivityEntry

    // Static fallback ("Stopped · 1h 25m") when there's no start time to tick from.
    private var elapsedLine: String {
        guard !entry.elapsed.isEmpty else { return entry.status }
        return entry.status.isEmpty ? entry.elapsed : "\(entry.status) · \(entry.elapsed)"
    }

    // ST → time AT location; MV → time IN motion — ticking LIVE via Text(_, style:.timer).
    @ViewBuilder private var elapsedView: some View {
        if let start = entry.startedAt {
            HStack(spacing: 4) {
                if !entry.status.isEmpty { Text("\(entry.status) ·") }
                Text(start, style: .timer)
            }
            .font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
        } else if !elapsedLine.isEmpty {
            Text(elapsedLine).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                // Server-driven icon + title: in disabled/masked mode these become a lightbulb +
                // "Home Lights" so the tile reads as an innocuous Home Lights widget.
                Image(systemName: entry.icon).foregroundStyle(.blue)
                Text(entry.title).font(.headline)
                Spacer()
            }
            Spacer()
            Text(entry.place.isEmpty ? (entry.status.isEmpty ? "—" : entry.status) : entry.place)
                .font(.title3).fontWeight(.semibold).lineLimit(2)
            elapsedView
            if !entry.clockRange.isEmpty {
                Text(entry.clockRange).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            } else if !entry.meta.isEmpty {
                Text(entry.meta).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct ActivityWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ActivityWidget", provider: ActivityTimelineProvider()) { entry in
            ActivityWidgetView(entry: entry)
                .padding(12)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Activity")
        .description("Latest location/activity status.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

// MARK: - 3) Balances — by group, with the net total

struct BalancesEntry: TimelineEntry { let date: Date; let net: String; let asOf: String; let groups: [WBalanceGroup] }

struct BalancesTimelineProvider: TimelineProvider {
    func placeholder(in context: Context) -> BalancesEntry { BalancesEntry(date: Date(), net: "", asOf: "", groups: []) }
    func getSnapshot(in context: Context, completion: @escaping (BalancesEntry) -> Void) {
        completion(BalancesEntry(date: Date(), net: "", asOf: "", groups: []))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<BalancesEntry>) -> Void) {
        Task {
            let p: WBalancesPayload? = await WidgetAPI.get("/widget/balances")
            let entry = BalancesEntry(date: Date(), net: p?.netFormatted ?? "", asOf: p?.asOfFormatted ?? "", groups: p?.groups ?? [])
            completion(Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(refreshInterval))))
        }
    }
}

struct BalancesWidgetView: View {
    var entry: BalancesEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "dollarsign.circle.fill").foregroundStyle(.green)
                Text("Balances").font(.headline)
                Spacer()
                if !entry.net.isEmpty { Text(entry.net).font(.headline) }
            }
            Divider()
            if entry.groups.isEmpty {
                Spacer()
                Text("No balances").font(.subheadline).foregroundStyle(.secondary)
                Spacer()
            } else {
                ForEach(entry.groups.prefix(family == .systemLarge ? 6 : 4), id: \.self) { g in
                    HStack {
                        Text(g.name).font(.caption).lineLimit(1)
                        Spacer()
                        Text(g.total).font(.caption).fontWeight(.medium)
                            .foregroundStyle(g.direction == "down" ? .red : .primary)
                    }
                }
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct BalancesWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "BalancesWidget", provider: BalancesTimelineProvider()) { entry in
            BalancesWidgetView(entry: entry)
                .padding(12)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Balances")
        .description("Account balances by group.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}
