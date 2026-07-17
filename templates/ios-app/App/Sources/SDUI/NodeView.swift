import SwiftUI

/// Renders one manifest Node (and its subtree) as SwiftUI. Mirrors the web
/// renderer's component vocabulary and graceful-degradation rules.
struct NodeView: View {
    let node: Node
    let scope: Scope
    @Environment(\.sduiActions) private var actions

    var body: some View {
        render()
    }

    // Expand children, honoring props.repeat (binds `item.*` per array element).
    private func expandedChildren() -> [(node: Node, scope: Scope)] {
        if let repeatPath = node.props?["repeat"]?.stringValue,
           let arr = BindingResolver.resolve(repeatPath, scope)?.arrayValue {
            var out: [(Node, Scope)] = []
            for item in arr {
                var s = scope
                s.item = item
                for child in node.children ?? [] { out.append((child, s)) }
            }
            return out
        }
        return (node.children ?? []).map { ($0, scope) }
    }

    @ViewBuilder private func childViews() -> some View {
        let kids = expandedChildren()
        ForEach(Array(kids.enumerated()), id: \.offset) { _, pair in
            NodeView(node: pair.node, scope: pair.scope)
        }
    }

    private var style: StyleSpec { StyleSpec(raw: node.style) }

    // Fill an action's url from its urlBinding against the current scope. Used by
    // openURL (a resolved link) and navigate (a per-item page path, e.g. docs).
    private func resolvedAction(_ action: Action?) -> Action? {
        guard var a = action else { return nil }
        if (a.type == "openURL" || a.type == "navigate" || a.type == "submit"), let path = a.urlBinding,
           let u = BindingResolver.resolve(path, scope)?.stringValue {
            a.url = u
        }
        return a
    }

    private func render() -> AnyView {
        switch node.type {
        case "screen":
            return AnyView(
                ZStack {
                    (style.background(scope) ?? Color(hex: "#0b1020")).ignoresSafeArea()
                    childViews()
                }
            )

        case "scroll":
            return AnyView(ScrollView { VStack(spacing: 0) { childViews() } })

        case "vstack", "list":
            return AnyView(
                VStack(alignment: style.alignment, spacing: style.spacing ?? 12) { childViews() }
                    .styled(style, scope)
                    .tappable(resolvedAction(node.action), actions)
            )

        case "hstack", "row":
            let row = HStack(alignment: .center, spacing: style.spacing ?? 12) { childViews() }
            // A wrapping row (filter chips/tabs) degrades to a horizontally-scrollable
            // strip on native so items aren't clipped (SwiftUI HStack doesn't wrap).
            if style.wrap {
                return AnyView(
                    ScrollView(.horizontal, showsIndicators: false) { row }
                        .styled(style, scope)
                        .tappable(resolvedAction(node.action), actions)
                )
            }
            return AnyView(
                row
                    .styled(style, scope)
                    .tappable(resolvedAction(node.action), actions)
            )

        case "zstack":
            return AnyView(ZStack { childViews() }.styled(style, scope))

        case "spacer":
            return AnyView(Spacer(minLength: 0))

        case "divider":
            return AnyView(
                Rectangle()
                    .fill(BindingResolver.color(style.colorSpec, scope) ?? Color.gray.opacity(0.3))
                    .frame(height: 1)
            )

        case "card":
            return AnyView(
                VStack(alignment: style.alignment, spacing: style.spacing ?? 8) { childViews() }
                    .frame(maxWidth: .infinity, alignment: style.frameAlignment)
                    .padding(style.padding ?? 16)
                    .background(style.background(scope) ?? Color.white.opacity(0.06))
                    .clipShape(RoundedRectangle(cornerRadius: style.cornerRadius ?? 20, style: .continuous))
                    .tappable(resolvedAction(node.action), actions)
            )

        case "text":
            if let text = BindingResolver.text(binding: node.binding, props: node.props, scope) {
                return AnyView(
                    Text(text)
                        .font(style.font ?? .body)
                        .foregroundStyle(style.color(scope) ?? .primary)
                        .tappable(resolvedAction(node.action), actions)
                )
            }
            return AnyView(EmptyView())

        case "image":
            let name = node.binding.flatMap { BindingResolver.resolve($0, scope)?.stringValue }
                ?? node.props?["name"]?.stringValue
            return AnyView(
                Image(systemName: name ?? "questionmark")
                    .font(style.font ?? .body)
                    .foregroundStyle(style.color(scope) ?? .primary)
                    .symbolRenderingMode(.multicolor)
            )

        case "badge":
            if let text = BindingResolver.text(binding: node.binding, props: node.props, scope) {
                let c = style.color(scope) ?? .accentColor
                return AnyView(
                    Text(text)
                        .font(.caption).fontWeight(.semibold)
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .foregroundStyle(c)
                        .overlay(Capsule().stroke(c, lineWidth: 1))
                        // A badge with an action is a tappable chip (filter toggles/tabs).
                        .tappable(resolvedAction(node.action), actions)
                )
            }
            return AnyView(EmptyView())

        case "lineChart":
            let series = node.binding.flatMap { BindingResolver.resolve($0, scope) }
            return AnyView(
                LineChartView(series: series, theme: scope.theme)
                    .frame(height: style.height ?? 160)
            )

        case "barChart":
            let bars = node.binding.flatMap { BindingResolver.resolve($0, scope) }
            // Empty/absent → render nothing (graceful degradation), matching the web renderer.
            guard let arr = bars?.arrayValue, !arr.isEmpty else { return AnyView(EmptyView()) }
            return AnyView(BarChartView(bars: bars, theme: scope.theme, height: style.height ?? 96))

        case "table":
            let tableValue = node.binding.flatMap { BindingResolver.resolve($0, scope) }
            // No bound object (e.g. BQ Columns tab, where `preview` is absent) → render
            // nothing rather than an empty grid. Mirrors the web renderer's null-guard.
            guard let tableValue, tableValue.objectValue != nil else { return AnyView(EmptyView()) }
            return AnyView(TableView(value: tableValue))

        case "map":
            return AnyView(
                MapComponentView(value: node.binding.flatMap { BindingResolver.resolve($0, scope) })
                    .frame(height: style.height ?? 220)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            )

        case "disclosure":
            return AnyView(DisclosureSection(
                title: BindingResolver.text(binding: node.binding, props: node.props, scope) ?? "",
                children: node.children ?? [],
                scope: scope))

        case "code":
            if let text = BindingResolver.text(binding: node.binding, props: node.props, scope), !text.isEmpty {
                return AnyView(CodeBlockView(text: text))
            }
            return AnyView(EmptyView())

        case "markdown":
            if let text = BindingResolver.text(binding: node.binding, props: node.props, scope),
               !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return AnyView(MarkdownView(source: text))
            }
            return AnyView(EmptyView())

        case "timeline":
            return AnyView(TimelineView(
                value: node.binding.flatMap { BindingResolver.resolve($0, scope) },
                mapId: node.props?["mapId"]?.stringValue ?? "activity",
                actions: actions))

        case "field":
            return AnyView(FieldView(node: node, scope: scope))

        case "button":
            let title = BindingResolver.text(binding: node.binding, props: node.props, scope) ?? ""
            if node.action?.type == "submit" {
                // urlBinding lets a per-row button POST to a context-carrying url (e.g. the
                // repos "Ship" bar → /repos_pr?owner=…&branch=…).
                let url = resolvedAction(node.action)?.url ?? ""
                return AnyView(SubmitButton(title: title, url: url, actions: actions))
            }
            return AnyView(
                Button(title) { actions.run(resolvedAction(node.action)) }
                    .buttonStyle(.borderedProminent)
            )

        default:
            // Unknown component type -> inert; never crash.
            return AnyView(EmptyView())
        }
    }
}

// MARK: - Style + interaction modifiers

private extension View {
    func styled(_ style: StyleSpec, _ scope: Scope) -> some View {
        self
            .padding(style.padding ?? 0)
            .modifier(OptionalFrame(width: style.width, height: style.height, alignment: style.frameAlignment))
            .background(style.background(scope) ?? .clear)
            .foregroundStyle(style.color(scope) ?? .primary)
            .opacity(style.opacity ?? 1)
    }

    func tappable(_ action: Action?, _ actions: SDUIActions) -> some View {
        modifier(TappableModifier(action: action, actions: actions))
    }
}

private struct OptionalFrame: ViewModifier {
    let width: CGFloat?
    let height: CGFloat?
    let alignment: Alignment
    func body(content: Content) -> some View {
        content.frame(width: width, height: height, alignment: alignment)
    }
}

private struct TappableModifier: ViewModifier {
    let action: Action?
    let actions: SDUIActions
    func body(content: Content) -> some View {
        if let action {
            content.contentShape(Rectangle()).onTapGesture { actions.run(action) }
        } else {
            content
        }
    }
}

/// A button that gathers the page's FormStore and submits it, showing an in-progress spinner
/// then a done/failed state (e.g. the repos "Ship" bar: Ship → spinner → ✓, then the page
/// refreshes and the shipped row disappears).
private struct SubmitButton: View {
    let title: String
    let url: String
    let actions: SDUIActions
    @EnvironmentObject private var form: FormStore
    @State private var phase: Phase = .idle
    private enum Phase { case idle, working, done, failed }

    var body: some View {
        Button(action: tap) { label }
            .buttonStyle(.borderedProminent)
            .tint(phase == .failed ? .red : (phase == .done ? .green : .accentColor))
            .disabled(phase == .working || phase == .done)
            .animation(.default, value: phase)
    }

    @ViewBuilder private var label: some View {
        switch phase {
        case .idle:
            Text(title)
        case .working:
            HStack(spacing: 6) { ProgressView().controlSize(.small).tint(.white); Text("Working…") }
        case .done:
            HStack(spacing: 6) { Image(systemName: "checkmark"); Text("Done") }
        case .failed:
            HStack(spacing: 6) { Image(systemName: "exclamationmark.triangle"); Text("Retry") }
        }
    }

    private func tap() {
        guard phase == .idle || phase == .failed else { return }
        phase = .working
        // @MainActor so the phase mutations after the await actually drive SwiftUI (a bare Task
        // resumes off the main actor, where @State changes are dropped — the bug that made the
        // spinner/done state not appear).
        Task { @MainActor in
            let ok = await actions.submit(url, form.items())
            phase = ok ? .done : .failed
            if ok {
                // Let the ✓ land, then refresh so the result (a merged PR row) updates in place.
                try? await Task.sleep(nanoseconds: 900_000_000)
                actions.refresh()
            } else {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                phase = .idle
            }
        }
    }
}
