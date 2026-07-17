import SwiftUI

/// Resolution scope passed down the render tree. Mirrors the web Scope.
struct Scope {
    var data: JSONValue
    var theme: JSONValue
    var item: JSONValue?
    /// Local providers (e.g. calendar) injected under `local.*`. Native-only.
    var local: JSONValue?
    /// Page meta (freshness etc.) exposed under `meta.*`, matching the web. The client
    /// overrides `meta.updatedAtFormatted` with the real, measured freshness.
    var meta: JSONValue?
}

enum BindingResolver {
    /// Resolve a dotted path like "stocks.series" or "item.changePctFormatted".
    static func resolve(_ path: String, _ scope: Scope) -> JSONValue? {
        guard !path.isEmpty else { return nil }
        var root: JSONValue?
        var rest = path

        if path == "item" || path.hasPrefix("item.") {
            root = scope.item
            rest = String(path.dropFirst(4)).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        } else if path == "local" || path.hasPrefix("local.") {
            root = scope.local
            rest = String(path.dropFirst(5)).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        } else if path == "meta" || path.hasPrefix("meta.") {
            root = scope.meta
            rest = String(path.dropFirst(4)).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        } else {
            root = scope.data
        }

        guard !rest.isEmpty else { return root }
        var cur = root
        for key in rest.split(separator: ".") {
            cur = cur?[String(key)]
            if cur == nil { return nil }
        }
        return cur
    }

    /// Display text from binding (with optional props.fallback) or literal props.text.
    static func text(binding: String?, props: JSONValue?, _ scope: Scope) -> String? {
        if let binding {
            if let v = resolve(binding, scope)?.stringValue { return v }
            return props?["fallback"]?.stringValue
        }
        return props?["text"]?.stringValue
    }

    /// Resolve a color spec ($token, #hex, "up"/"down", or a binding path) to a Color.
    static func color(_ spec: String?, _ scope: Scope) -> Color? {
        guard let spec, !spec.isEmpty else { return nil }
        var v = spec
        if !spec.hasPrefix("$") && !spec.hasPrefix("#") {
            guard let resolved = resolve(spec, scope)?.stringValue else { return nil }
            v = resolved
        }
        if v == "up" { v = "$up" }
        if v == "down" { v = "$down" }
        if v.hasPrefix("$") {
            let token = String(v.dropFirst())
            guard let hex = scope.theme["colors"]?[token]?.stringValue else { return nil }
            return Color(hex: hex)
        }
        return Color(hex: v)
    }
}

extension Color {
    /// Init from "#rrggbb" / "#rrggbbaa" hex. Returns clear on parse failure.
    init(hex: String) {
        let s = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        var value: UInt64 = 0
        guard Scanner(string: s).scanHexInt64(&value) else { self = .clear; return }
        let r, g, b, a: Double
        switch s.count {
        case 6:
            r = Double((value >> 16) & 0xFF) / 255
            g = Double((value >> 8) & 0xFF) / 255
            b = Double(value & 0xFF) / 255
            a = 1
        case 8:
            r = Double((value >> 24) & 0xFF) / 255
            g = Double((value >> 16) & 0xFF) / 255
            b = Double((value >> 8) & 0xFF) / 255
            a = Double(value & 0xFF) / 255
        default:
            self = .clear; return
        }
        self = Color(.sRGB, red: r, green: g, blue: b, opacity: a)
    }
}
