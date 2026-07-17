import SwiftUI

/// Renders a tabular `bq`-style value { columns:[String], rows:[[…]], error? }.
/// The native analog of the web `table` component.
struct TableView: View {
    let value: JSONValue?

    private var error: String? {
        let e = value?["error"]?.stringValue
        return (e?.isEmpty == false) ? e : nil
    }
    private var columns: [String] {
        value?["columns"]?.arrayValue?.compactMap { $0.stringValue } ?? []
    }
    private var rows: [[String]] {
        (value?["rows"]?.arrayValue ?? []).map { row in
            (row.arrayValue ?? []).map { $0.stringValue ?? "" }
        }
    }

    var body: some View {
        if let error {
            Text(error)
                .font(.footnote)
                .foregroundStyle(.red)
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                    GridRow {
                        ForEach(Array(columns.enumerated()), id: \.offset) { _, c in
                            Text(c.uppercased())
                                .font(.caption2).fontWeight(.semibold)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Divider()
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                        GridRow {
                            ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                                Text(cell).font(.callout)
                            }
                        }
                    }
                }
            }
        }
    }
}
