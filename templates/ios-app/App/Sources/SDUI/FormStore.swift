import SwiftUI

/// Collects editable field values for a page so a `submit` action can POST them.
/// New-row controls use reserved keys (__new_key/__new_value/__new_type).
@MainActor
final class FormStore: ObservableObject {
    @Published var values: [String: String] = [:]
    private var types: [String: String] = [:]

    /// Seed an initial value once (does not clobber user edits).
    func seed(_ key: String, _ value: String, _ type: String) {
        if values[key] == nil { values[key] = value }
        types[key] = type
    }

    func binding(_ key: String) -> Binding<String> {
        Binding(get: { self.values[key] ?? "" }, set: { self.values[key] = $0 })
    }

    /// Snapshot as [{key,value,type}], assembling the __new_* controls into one item.
    func items() -> [[String: String]] {
        var out: [[String: String]] = []
        var newKey = "", newValue = "", newType = "string"
        for (key, value) in values {
            switch key {
            case "__new_key": newKey = value
            case "__new_value": newValue = value
            case "__new_type": newType = value.isEmpty ? "string" : value
            default:
                out.append(["key": key, "value": value, "type": types[key] ?? "string"])
            }
        }
        if !newKey.trimmingCharacters(in: .whitespaces).isEmpty {
            out.append(["key": newKey, "value": newValue, "type": newType])
        }
        return out
    }
}
