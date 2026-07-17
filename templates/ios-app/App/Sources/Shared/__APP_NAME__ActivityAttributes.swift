import ActivityKit
import Foundation

/// Live Activity data for the "latest activity status" (the AFM current-state: where the
/// device is now + battery/last-seen). This file is compiled into BOTH the app and the
/// widget extension (see project.yml `sources`), so the type is shared by source and NO App
/// Group is required — dynamic updates arrive over APNs as the `ContentState`.
struct __APP_NAME__ActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public var status: String       // e.g. "Stopped", "Driving", "At Office"
        public var place: String        // human place / address (ST) or destination (MV)
        public var meta: String         // e.g. "battery 82% · seen 2 min ago"
        public var category: String     // "Stopped" / "Moving" (empty when unknown)
        public var elapsed: String      // static fallback duration ("1h 25m")
        public var clockRange: String   // segment clock range ("2:15 – 3:40 PM")
        public var startedAt: Double     // unix start of the current segment (0 = none → ticks live)
        public init(status: String, place: String, meta: String,
                    category: String = "", elapsed: String = "", clockRange: String = "", startedAt: Double = 0) {
            self.status = status
            self.place = place
            self.meta = meta
            self.category = category
            self.elapsed = elapsed
            self.clockRange = clockRange
            self.startedAt = startedAt
        }
    }

    public var title: String        // fixed for the activity's life, e.g. "Activity"
    public init(title: String) { self.title = title }
}
