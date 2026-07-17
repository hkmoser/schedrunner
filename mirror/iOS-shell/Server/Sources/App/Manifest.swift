import Foundation
import Vapor

/// The SDUI manifest served at GET /dashboard. Structure matches
/// Shared/schema/manifest.schema.json. `theme` and `screen` are passed through
/// from editable templates; `data` is assembled from live providers.
public struct Manifest: Content {
    public var schemaVersion: Int
    public var generatedAt: String
    public var theme: JSONValue
    public var data: JSONValue
    public var nav: JSONValue?
    public var screen: JSONValue

    public static let currentSchemaVersion = 1
}
