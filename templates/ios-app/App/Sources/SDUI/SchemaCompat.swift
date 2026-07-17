import Foundation

enum SchemaCompat {
    enum Result: Equatable {
        case ok
        case tooNew
    }

    /// Guard rendering so a future server schema can't brick an installed shell.
    static func check(_ manifest: Manifest) -> Result {
        manifest.schemaVersion > Manifest.supportedSchemaMajor ? .tooNew : .ok
    }
}
