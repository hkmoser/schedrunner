import ActivityKit
import WidgetKit
import SwiftUI

/// The "latest activity status" Live Activity: lock-screen banner + Dynamic Island, driven by
/// __APP_NAME__ActivityAttributes.ContentState (started by the app, updated via APNs push).
struct __APP_NAME__LiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: __APP_NAME__ActivityAttributes.self) { context in
            LockScreenLiveActivityView(state: context.state, title: context.attributes.title)
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Image(systemName: "location.fill").foregroundStyle(.blue)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.state.status).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(context.state.place.isEmpty ? context.attributes.title : context.state.place)
                        .font(.headline).lineLimit(1)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    if !context.state.meta.isEmpty {
                        Text(context.state.meta).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            } compactLeading: {
                Image(systemName: "location.fill")
            } compactTrailing: {
                Text(context.state.status).font(.caption2).lineLimit(1)
            } minimal: {
                Image(systemName: "location.fill")
            }
            .keylineTint(.blue)
        }
    }
}

/// Lock-screen / banner presentation of the Live Activity.
struct LockScreenLiveActivityView: View {
    let state: __APP_NAME__ActivityAttributes.ContentState
    let title: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: state.category == "Moving" ? "figure.walk.motion" : "location.fill")
                .font(.title2)
                .foregroundStyle(.blue)
            VStack(alignment: .leading, spacing: 2) {
                Text(state.place.isEmpty ? title : state.place)
                    .font(.headline)
                    .lineLimit(1)
                // Same data as the Activity widget: "<status> · <elapsed>" with a LIVE-ticking
                // elapsed (Text style .timer) when a segment start is known.
                if state.startedAt > 0 {
                    HStack(spacing: 4) {
                        if !state.status.isEmpty { Text("\(state.status) ·") }
                        Text(Date(timeIntervalSince1970: state.startedAt), style: .timer)
                    }
                    .font(.subheadline).foregroundStyle(.secondary)
                } else if !state.status.isEmpty {
                    Text(state.status).font(.subheadline).foregroundStyle(.secondary)
                }
                if !state.clockRange.isEmpty {
                    Text(state.clockRange).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                } else if !state.meta.isEmpty {
                    Text(state.meta).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding()
    }
}
