import Foundation

/// A generic JSON value used to pass the editable UI tree (templates) through
/// untouched and to assemble the data bag dynamically. This is the Swift analog
/// of the permissive JSON values the web renderer uses, and it keeps the served
/// manifest conformant to Shared/schema/manifest.schema.json by construction.
public indirect enum JSONValue: Codable, Equatable {
    case string(String)
    case double(Double)
    case int(Int)
    case bool(Bool)
    case null
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let b = try? container.decode(Bool.self) {
            self = .bool(b)
        } else if let i = try? container.decode(Int.self) {
            self = .int(i)
        } else if let d = try? container.decode(Double.self) {
            self = .double(d)
        } else if let s = try? container.decode(String.self) {
            self = .string(s)
        } else if let a = try? container.decode([JSONValue].self) {
            self = .array(a)
        } else if let o = try? container.decode([String: JSONValue].self) {
            self = .object(o)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "Unsupported JSON value")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let s): try container.encode(s)
        case .double(let d): try container.encode(d)
        case .int(let i): try container.encode(i)
        case .bool(let b): try container.encode(b)
        case .null: try container.encodeNil()
        case .array(let a): try container.encode(a)
        case .object(let o): try container.encode(o)
        }
    }
}

// MARK: - Ergonomic constructors

extension JSONValue {
    static func num(_ value: Double) -> JSONValue { .double(value) }

    /// Build an object from an ordered list of pairs (order is illustrative only;
    /// JSON objects are unordered on the wire).
    static func obj(_ pairs: [(String, JSONValue)]) -> JSONValue {
        var dict: [String: JSONValue] = [:]
        for (k, v) in pairs { dict[k] = v }
        return .object(dict)
    }

    // MARK: - Read accessors (used to extract fields for the compact /widget/* endpoints)

    subscript(_ key: String) -> JSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }
    var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }
    var arrayValue: [JSONValue]? {
        if case .array(let a) = self { return a }
        return nil
    }
    var doubleValue: Double? {
        switch self {
        case .double(let d): return d
        case .int(let i): return Double(i)
        default: return nil
        }
    }
}
