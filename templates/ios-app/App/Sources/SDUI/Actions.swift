import SwiftUI

/// Declarative action runner injected via the environment. Unknown action types
/// are no-ops, matching the web renderer.
struct SDUIActions {
    var refresh: () -> Void = {}
    var setPref: (String, JSONValue?) -> Void = { _, _ in }
    var openURL: (URL) -> Void = { _ in }
    var navigate: (String) -> Void = { _ in }
    /// Submit form items to a url; returns whether the POST succeeded so the button can show an
    /// in-progress spinner and a done/failed state. Does NOT refresh — the caller decides when.
    var submit: (String, [[String: String]]) async -> Bool = { _, _ in false }
    var focus: (String, Int) -> Void = { _, _ in }
    /// Toggle the "latest activity" Live Activity (start if off, stop if on). iOS-only.
    var liveActivity: () -> Void = {}

    func run(_ action: Action?) {
        guard let action else { return }
        switch action.type {
        case "liveActivity":
            liveActivity()
        case "refresh":
            refresh()
        case "openURL":
            if let s = action.url, let url = URL(string: s) { openURL(url) }
        case "setPref":
            if let key = action.key { setPref(key, action.value) }
        case "navigate":
            if action.screenId == "back" || action.url == "back" { navigate("back") }
            else if let url = action.url { navigate(url) }
        case "focus":
            focus(action.key ?? "activity", Int(action.value?.doubleValue ?? -1))
        case "none":
            break
        default:
            break
        }
    }
}

private struct SDUIActionsKey: EnvironmentKey {
    static let defaultValue = SDUIActions()
}

extension EnvironmentValues {
    var sduiActions: SDUIActions {
        get { self[SDUIActionsKey.self] }
        set { self[SDUIActionsKey.self] = newValue }
    }
}
