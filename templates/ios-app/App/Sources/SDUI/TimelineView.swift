import SwiftUI

/// Google-Maps-style timeline of activity segments. Binds to an array of
/// { index, mode, icon, place, timeFormatted, durationFormatted, distanceFormatted,
///   categoryColor }. Tapping an entry focuses that segment on the map.
struct TimelineView: View {
    let value: JSONValue?
    let mapId: String
    let actions: SDUIActions

    private var segments: [JSONValue] { value?.arrayValue ?? [] }

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, seg in
                let color = Color(hex: seg["categoryColor"]?.stringValue ?? "#6ea8fe")
                let mode = seg["mode"]?.stringValue ?? ""
                let place = seg["place"]?.stringValue ?? ""
                let icon = seg["icon"]?.stringValue ?? "mappin.circle.fill"
                let meta = [seg["timeFormatted"]?.stringValue, seg["durationFormatted"]?.stringValue,
                            seg["distanceFormatted"]?.stringValue]
                    .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
                let mapsURL = seg["mapsUrl"]?.stringValue.flatMap { URL(string: $0) }
                let isStop = seg["category"]?.stringValue == "Stopped"
                let durMin = seg["durationMin"]?.doubleValue ?? 0
                let extra = min(110.0, 13.0 * (durMin > 0 ? durMin : 0).squareRoot())
                HStack(alignment: .center, spacing: 12) {
                    if isStop {
                        Image(systemName: icon)
                            .font(.system(size: 13)).foregroundStyle(.white)
                            .frame(width: 30, height: 30)
                            .background(Circle().fill(color))
                    } else {
                        // Bare icon (no circle) so stops read as the hierarchy.
                        Image(systemName: icon)
                            .font(.system(size: 15)).foregroundStyle(color)
                            .frame(width: 30, height: 24)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(place.isEmpty ? mode : (mode.isEmpty ? place : "\(mode) · \(place)"))
                            .font(.subheadline).fontWeight(.semibold)
                            .foregroundStyle(.primary)
                        Text(meta).font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(isStop ? 10 : 0)
                    .background {
                        // Stops get a light translucent box for separation.
                        if isStop {
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(Color.white.opacity(0.05))
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.06)))
                        }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        if let idx = seg["index"]?.doubleValue { actions.focus(mapId, Int(idx)) }
                    }
                    if let mapsURL {
                        Button { actions.openURL(mapsURL) } label: {
                            Image(systemName: "map.fill").foregroundStyle(.tint)
                                .frame(width: 34, height: 34)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 4)
                // Longer stays/segments get more vertical room; content stays centered.
                .frame(minHeight: 44 + CGFloat(extra), alignment: .center)
            }
        }
    }
}
