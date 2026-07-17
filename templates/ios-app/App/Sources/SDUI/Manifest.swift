import Foundation

/// The SDUI manifest, decoded from GET /__APP_NAME_LOWER__. Matches
/// Shared/schema/manifest.schema.json and the server's Manifest.
struct Manifest: Codable, Equatable {
    var schemaVersion: Int
    var generatedAt: String
    var theme: JSONValue?
    var data: JSONValue?
    var nav: [NavItem]?
    var screen: Node

    /// MAJOR schema version this client understands.
    static let supportedSchemaMajor = 1
}

struct NavItem: Codable, Equatable, Identifiable {
    var title: String
    var icon: String?
    /// Leaf destination. Omitted for section headers that only group `children`.
    var path: String?
    /// Optional second-level items, making this a section header in the menu.
    var children: [NavItem]?
    var id: String { path ?? title }
}

/// A node in the UI tree. Decoded permissively: unknown `type`s still decode and
/// render an inert placeholder rather than failing the whole manifest.
struct Node: Codable, Equatable {
    var type: String
    var nodeID: String?
    var props: JSONValue?
    var style: JSONValue?
    var binding: String?
    var action: Action?
    var children: [Node]?

    enum CodingKeys: String, CodingKey {
        case type
        case nodeID = "id"
        case props, style, binding, action, children
    }
}

struct Action: Codable, Equatable {
    var type: String
    var url: String?
    var urlBinding: String?
    var screenId: String?
    var key: String?
    var value: JSONValue?
}
