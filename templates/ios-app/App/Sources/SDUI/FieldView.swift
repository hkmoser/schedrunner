import SwiftUI

/// An editable form field whose widget is chosen by the value's `type`. Binds to a
/// { key, label, value, type, options? } object, or uses props.field/label/fieldType.
struct FieldView: View {
    let node: Node
    let scope: Scope
    @EnvironmentObject private var form: FormStore

    private var bound: JSONValue? { node.binding.flatMap { BindingResolver.resolve($0, scope) } }
    private var key: String { bound?["key"]?.stringValue ?? node.props?["field"]?.stringValue ?? "" }
    private var label: String { bound?["label"]?.stringValue ?? node.props?["label"]?.stringValue ?? key }
    private var type: String { bound?["type"]?.stringValue ?? node.props?["fieldType"]?.stringValue ?? "string" }
    private var initial: String { bound?["value"]?.stringValue ?? node.props?["value"]?.stringValue ?? "" }
    private var placeholder: String { bound?["placeholderFormatted"]?.stringValue ?? "" }
    private var options: [(value: String, label: String)] {
        (bound?["options"]?.arrayValue ?? node.props?["options"]?.arrayValue ?? []).compactMap { opt in
            if let v = opt["value"]?.stringValue { return (v, opt["label"]?.stringValue ?? v) }
            if let s = opt.stringValue { return (s, s) }
            return nil
        }
    }

    var body: some View {
        Group {
            if key.isEmpty {
                EmptyView()
            } else {
                HStack {
                    Text(label).font(.subheadline).foregroundStyle(.secondary)
                    Spacer()
                    widget
                }
                .padding(.vertical, 6)
                .onAppear { form.seed(key, initial, type) }
            }
        }
    }

    @ViewBuilder private var widget: some View {
        switch type {
        case "secret", "password":
            // Masked entry; placeholder shows "•••• set" when a value is already stored.
            SecureField(placeholder, text: form.binding(key))
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 200)
                .textFieldStyle(.roundedBorder)
                .textContentType(.oneTimeCode)
        case "bool":
            Toggle("", isOn: Binding(
                get: { (form.values[key] ?? initial) == "true" },
                set: { form.values[key] = $0 ? "true" : "false" }
            ))
            .labelsHidden()
        case "enum":
            Picker("", selection: form.binding(key)) {
                ForEach(options, id: \.value) { Text($0.label).tag($0.value) }
            }
            .pickerStyle(.menu)
        case "int", "float", "number":
            TextField("", text: form.binding(key))
                .keyboardType(.numbersAndPunctuation)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 160)
                .textFieldStyle(.roundedBorder)
        default:
            TextField("", text: form.binding(key))
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 200)
                .textFieldStyle(.roundedBorder)
        }
    }
}
