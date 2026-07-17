import SwiftUI
import MapKit

/// Renders a `map` value { center:{lat,lon}, segments:[{category,color,points,center}] }
/// as a MapKit map: moves as polylines, stops as markers. Native analog of the web map.
struct MapComponentView: View {
    let value: JSONValue?

    private func coord(_ v: JSONValue?) -> CLLocationCoordinate2D? {
        guard let lat = v?["lat"]?.doubleValue, let lon = v?["lon"]?.doubleValue else { return nil }
        return CLLocationCoordinate2D(latitude: lat, longitude: lon)
    }

    private func points(_ seg: JSONValue) -> [CLLocationCoordinate2D] {
        (seg["points"]?.arrayValue ?? []).compactMap { p in
            let a = p.arrayValue ?? []
            guard a.count >= 2, let lat = a[0].doubleValue, let lon = a[1].doubleValue else { return nil }
            return CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }
    }

    private var segments: [JSONValue] { value?["segments"]?.arrayValue ?? [] }
    private var center: CLLocationCoordinate2D? { coord(value?["center"]) }

    var body: some View {
        if let center {
            Map(initialPosition: .region(MKCoordinateRegion(
                center: center, latitudinalMeters: 8000, longitudinalMeters: 8000))) {
                ForEach(Array(segments.enumerated()), id: \.offset) { _, seg in
                    let color = Color(hex: seg["color"]?.stringValue ?? "#6ea8fe")
                    let pts = points(seg)
                    if seg["category"]?.stringValue == "Moving", pts.count > 1 {
                        MapPolyline(coordinates: pts).stroke(color, lineWidth: 3)
                    } else if let c = coord(seg["center"]) ?? pts.first {
                        Annotation("", coordinate: c) {
                            Circle().fill(color)
                                .overlay(Circle().stroke(.white, lineWidth: 1.5))
                                .frame(width: 12, height: 12)
                        }
                    }
                }
            }
            .mapStyle(.standard(elevation: .flat))
        } else {
            Text("No recent location")
                .font(.subheadline).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}
