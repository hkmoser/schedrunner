import Foundation

/// Generic JSON value mirroring the server's JSONValue and the web renderer's
/// permissive values. Decodes the manifest's theme/screen/data without a rigid
/// schema so unknown keys pass through untouched (forward-compatible).
indirect enum JSONValue: Codable, Equatable {
    case string(String)
    case double(Double)
    case int(Int)
    case bool(Bool)
    case null
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let i = try? c.decode(Int.self) { self = .int(i) }
        else if let d = try? c.decode(Double.self) { self = .double(d) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else if let o = try? c.decode([String: JSONValue].self) { self = .object(o) }
        else { throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unsupported JSON") }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .double(let d): try c.encode(d)
        case .int(let i): try c.encode(i)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }
}

// MARK: - Convenience accessors used by the renderer

extension JSONValue {
    subscript(key: String) -> JSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    var stringValue: String? {
        switch self {
        case .string(let s): return s
        case .int(let i): return String(i)
        case .double(let d): return String(d)
        case .bool(let b): return String(b)
        default: return nil
        }
    }

    var doubleValue: Double? {
        switch self {
        case .double(let d): return d
        case .int(let i): return Double(i)
        case .string(let s): return Double(s)
        default: return nil
        }
    }

    var arrayValue: [JSONValue]? {
        if case .array(let a) = self { return a }
        return nil
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let o) = self { return o }
        return nil
    }

    var boolValue: Bool? {
        switch self {
        case .bool(let b): return b
        case .int(let i): return i != 0
        case .string(let s): return s == "true" || s == "1"
        default: return nil
        }
    }

    /// The theme to render now: its `colors` swapped to the `dark` palette when the system
    /// is in dark mode and one is provided. Mirrors the web `activeColors` so light/dark
    /// follows the system on both clients (light is the default/fallback).
    func activeTheme(dark: Bool) -> JSONValue {
        guard dark, let darkColors = self["dark"], case .object(var obj) = self else { return self }
        obj["colors"] = darkColors
        return .object(obj)
    }
}
