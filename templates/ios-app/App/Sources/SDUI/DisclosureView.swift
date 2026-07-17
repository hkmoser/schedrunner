import SwiftUI

/// Collapsible section: a tappable header over its children. Native analog of the
/// web `disclosure` component (HTML <details>).
struct DisclosureSection: View {
    let title: String
    let children: [Node]
    let scope: Scope
    @State private var expanded = true

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(children.enumerated()), id: \.offset) { _, child in
                    NodeView(node: child, scope: scope)
                }
            }
        } label: {
            Text(title.uppercased())
                .font(.caption).fontWeight(.semibold)
                .foregroundStyle(.secondary)
        }
        .tint(.secondary)
    }
}
