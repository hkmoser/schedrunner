import SwiftUI

/// Typed accessor over a node's permissive `style` JSON object. Unknown keys are
/// ignored. Mirrors the web renderer's applyStyle.
struct StyleSpec {
    let raw: JSONValue?

    var padding: CGFloat? { (raw?["padding"]?.doubleValue).map { CGFloat($0) } }
    var spacing: CGFloat? { (raw?["spacing"]?.doubleValue).map { CGFloat($0) } }
    var cornerRadius: CGFloat? { (raw?["cornerRadius"]?.doubleValue).map { CGFloat($0) } }
    var opacity: Double? { raw?["opacity"]?.doubleValue }
    var width: CGFloat? { (raw?["width"]?.doubleValue).map { CGFloat($0) } }
    var height: CGFloat? { (raw?["height"]?.doubleValue).map { CGFloat($0) } }
    var colorSpec: String? { raw?["color"]?.stringValue }
    var backgroundSpec: String? { raw?["background"]?.stringValue }
    var fontRole: String? { raw?["font"]?.stringValue }
    var weight: String? { raw?["weight"]?.stringValue }
    var align: String? { raw?["align"]?.stringValue }
    /// A row of items (e.g. filter chips) that should wrap / not be clipped.
    var wrap: Bool { raw?["wrap"]?.boolValue == true }

    func color(_ scope: Scope) -> Color? { BindingResolver.color(colorSpec, scope) }
    func background(_ scope: Scope) -> Color? { BindingResolver.color(backgroundSpec, scope) }

    var font: Font? {
        guard let role = fontRole else { return weightFont(nil) }
        let base: Font
        switch role {
        case "largeTitle": base = .largeTitle
        case "title": base = .title
        case "title2": base = .title2
        case "title3": base = .title3
        case "headline": base = .headline
        case "subhead": base = .subheadline
        case "caption": base = .caption
        case "caption2": base = .caption2
        default: base = .body
        }
        return base.weight(swiftWeight)
    }

    private func weightFont(_ f: Font?) -> Font? {
        guard weight != nil else { return f }
        return (f ?? .body).weight(swiftWeight)
    }

    var swiftWeight: Font.Weight {
        switch weight {
        case "medium": return .medium
        case "semibold": return .semibold
        case "bold": return .bold
        default: return fontRole == "headline" ? .semibold : .regular
        }
    }

    var alignment: HorizontalAlignment {
        switch align {
        case "center": return .center
        case "trailing": return .trailing
        default: return .leading
        }
    }

    var frameAlignment: Alignment {
        switch align {
        case "center": return .center
        case "trailing": return .trailing
        default: return .leading
        }
    }
}
