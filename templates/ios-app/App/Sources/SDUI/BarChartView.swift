import SwiftUI

/// Databricks-style run-history bar chart, the native analog of the web `barchart`. One bar per
/// recent iteration: height ∝ processing time (pre-normalized 0..100 by the server), colored by
/// success/failure via theme tokens. Binds to [{ heightPct, color, labelFormatted }].
struct BarChartView: View {
    let bars: JSONValue?
    let theme: JSONValue
    var height: CGFloat

    private struct Bar: Identifiable {
        let id: Int
        let heightPct: CGFloat
        let color: Color
        let label: String
    }

    private var items: [Bar] {
        guard let arr = bars?.arrayValue else { return [] }
        return arr.enumerated().map { i, b in
            let hp = CGFloat(b["heightPct"]?.doubleValue ?? 0).clamped(0, 100)
            return Bar(id: i, heightPct: hp, color: color(b["color"]?.stringValue),
                       label: b["labelFormatted"]?.stringValue ?? "")
        }
    }

    /// Resolve a $token / #hex / "up"/"down" color against the active theme (mirrors
    /// BindingResolver.color's literal-color path; the bars only carry literal specs).
    private func color(_ spec: String?) -> Color {
        guard var v = spec, !v.isEmpty else { return .secondary }
        if v == "up" { v = "$up" }
        if v == "down" { v = "$down" }
        if v.hasPrefix("$") {
            guard let hex = theme["colors"]?[String(v.dropFirst())]?.stringValue else { return .secondary }
            return Color(hex: hex)
        }
        return Color(hex: v)
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 6) {
            ForEach(items) { bar in
                VStack(spacing: 4) {
                    Spacer(minLength: 0)
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .fill(bar.color)
                        .frame(maxWidth: 34)
                        .frame(height: max(3, height * bar.heightPct / 100))
                    Text(bar.label)
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: height + 16, alignment: .bottom)
    }
}

private extension CGFloat {
    func clamped(_ lo: CGFloat, _ hi: CGFloat) -> CGFloat { Swift.min(Swift.max(self, lo), hi) }
}
