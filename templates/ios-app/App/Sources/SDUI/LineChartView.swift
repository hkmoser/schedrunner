import SwiftUI
import Charts

/// Normalized multi-line chart (relative performance), the native analog of the
/// web SVG chart. Binds to an array of { color, points: [Double] }.
struct LineChartView: View {
    let series: JSONValue?
    let theme: JSONValue

    private struct Line: Identifiable {
        let id: Int
        let color: Color
        let points: [Double]
        let x: [Double]?      // normalized 0..1 x positions; nil → even spacing by index
        let dashed: Bool
    }

    private var lines: [Line] {
        guard let arr = series?.arrayValue else { return [] }
        var out: [Line] = []
        for (i, s) in arr.enumerated() {
            guard let pts = s["points"]?.arrayValue?.compactMap({ $0.doubleValue }), pts.count > 1 else { continue }
            let hex = s["color"]?.stringValue ?? theme["colors"]?["accent"]?.stringValue ?? "#6ea8fe"
            let x = s["x"]?.arrayValue?.compactMap { $0.doubleValue }
            let dashed = s["dashed"]?.boolValue ?? false
            out.append(Line(id: i, color: Color(hex: hex), points: pts,
                            x: (x?.count == pts.count ? x : nil), dashed: dashed))
        }
        return out
    }

    var body: some View {
        Chart {
            ForEach(lines) { line in
                ForEach(Array(line.points.enumerated()), id: \.offset) { idx, value in
                    LineMark(
                        x: .value("t", line.x?[idx] ?? Double(idx)),
                        y: .value("v", value),
                        series: .value("s", line.id)
                    )
                    .foregroundStyle(line.color)
                    .lineStyle(StrokeStyle(lineWidth: 2, dash: line.dashed ? [5, 4] : []))
                    .interpolationMethod(.catmullRom)
                }
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartLegend(.hidden)
    }
}
