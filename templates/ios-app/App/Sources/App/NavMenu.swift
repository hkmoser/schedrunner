import SwiftUI

/// Top app bar: hamburger + title, an Offline pill, and a refresh button — the sticky shell
/// controls. Freshness ("Updated …") lives in each page's header via `meta.updatedAtFormatted`
/// (so it isn't shown twice), matching the web shell.
struct AppBarView: View {
    let title: String
    let offline: Bool
    let onMenu: () -> Void
    let onRefresh: () -> Void

    /// True in the "__APP_NAME__ Next" build (bundle id `<id>.next`, set by gen_ios_config).
    /// Shows an always-visible NEXT badge so the two side-by-side apps can't be confused.
    private var isNextChannel: Bool {
        Bundle.main.bundleIdentifier?.hasSuffix(".next") == true
    }

    var body: some View {
        HStack(spacing: 8) {
            Button(action: onMenu) {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 20))
                    .frame(width: 40, height: 40)
            }
            .foregroundStyle(.primary)

            Text(title).font(.headline)
            if isNextChannel {
                Text("NEXT")
                    .font(.system(size: 10, weight: .heavy))
                    .kerning(0.8)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Capsule().fill(Color(red: 0.49, green: 0.36, blue: 1.0)))
                    .foregroundStyle(.white)
            }
            Spacer(minLength: 4)

            if offline {
                Text("Offline")
                    .font(.caption2).fontWeight(.semibold)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Capsule().fill(Color.orange.opacity(0.22)))
                    .foregroundStyle(.orange)
            }
            Button(action: onRefresh) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 18))
                    .frame(width: 40, height: 40)
            }
            .foregroundStyle(.primary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.ultraThinMaterial)
    }
}

/// Slide-out navigation drawer for the two-level `nav` tree, with a footer that mirrors the
/// web: offline-cache status + "Cache all pages now", "Hard refresh", and the build stamp.
struct NavDrawerView: View {
    @ObservedObject var vm: __APP_NAME__ViewModel
    let onSelect: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    Text("MENU")
                        .font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
                        .padding(.horizontal, 12).padding(.vertical, 8)

                    ForEach(vm.current?.nav ?? []) { item in
                        if let children = item.children, !children.isEmpty {
                            HStack(spacing: 10) {
                                Image(systemName: item.icon ?? "folder")
                                Text(item.title.uppercased())
                                    .font(.caption).fontWeight(.semibold)
                            }
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 12).padding(.top, 10)
                            ForEach(children) { child in
                                if let path = child.path { row(child, path: path, indent: true) }
                            }
                        } else if let path = item.path {
                            row(item, path: path, indent: false)
                        }
                    }
                }
                .padding(.top, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            footer
        }
        .frame(maxWidth: 300, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(hex: "#161c2e"))
    }

    /// Footer: cache-all + status, hard refresh, build stamp.
    private var footer: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider().overlay(Color.white.opacity(0.12))

            Button { vm.cacheAllPages() } label: {
                Label(vm.prefetching ? "Caching…" : "Cache all pages now", systemImage: "arrow.down.circle")
                    .font(.subheadline)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .disabled(vm.prefetching)
            .foregroundStyle(vm.prefetching ? Color.secondary : Color.primary)

            Text(vm.cacheStatusText)
                .font(.caption2).foregroundStyle(.secondary)

            Button { vm.hardRefresh() } label: {
                Label(vm.isOffline ? "Hard refresh (online only)" : "Hard refresh",
                      systemImage: "arrow.triangle.2.circlepath")
                    .font(.subheadline)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .disabled(vm.isOffline)
            .foregroundStyle(vm.isOffline ? Color.secondary : Color.primary)

            Text(vm.buildInfo)
                .font(.caption2).foregroundStyle(.tertiary)
                .padding(.top, 2)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.black.opacity(0.18))
    }

    @ViewBuilder private func row(_ item: NavItem, path: String, indent: Bool) -> some View {
        Button { onSelect(path) } label: {
            HStack(spacing: 12) {
                Image(systemName: item.icon ?? "circle").frame(width: 22)
                Text(item.title)
                Spacer()
            }
            .padding(.vertical, 10)
            .padding(.leading, indent ? 24 : 12)
            .padding(.trailing, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(path == vm.currentPath ? Color.accentColor.opacity(0.16) : Color.clear)
            .foregroundStyle(path == vm.currentPath ? Color.accentColor : Color.primary)
        }
    }
}
