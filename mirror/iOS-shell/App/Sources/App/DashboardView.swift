import SwiftUI

/// Renders the active page's screen behind a top app bar + slide-out menu, or
/// graceful states. Restricted (decoy) mode hides the menu entirely.
struct DashboardView: View {
    @ObservedObject var vm: DashboardViewModel
    @Environment(\.openURL) private var openURL
    @Environment(\.colorScheme) private var colorScheme
    @State private var showMenu = false

    /// vm.scope with its theme set to the palette matching the system light/dark setting.
    private var activeScope: Scope {
        var s = vm.scope
        s.theme = s.theme.activeTheme(dark: colorScheme == .dark)
        return s
    }

    private var actions: SDUIActions {
        SDUIActions(
            refresh: { Task { await vm.refresh() } },
            setPref: { _, _ in },
            openURL: { openURL($0) },
            navigate: { vm.navigate($0) },
            submit: { url, items in await vm.submit(url, items) },
            liveActivity: { vm.toggleLiveActivity() }
        )
    }

    var body: some View {
        ZStack(alignment: .leading) {
            VStack(spacing: 0) {
                if !vm.restricted {
                    AppBarView(
                        title: vm.activeTitle,
                        offline: vm.isOffline,
                        onMenu: { withAnimation { showMenu = true } },
                        onRefresh: { Task { await vm.refresh() } }
                    )
                    if vm.updateAvailable, let url = vm.updateInstallURL {
                        // UIApplication.open is more reliable than SwiftUI openURL for the
                        // itms-services scheme (hands off to the system installer).
                        UpdateBanner { UIApplication.shared.open(url) }
                    }
                }
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .refreshable { await vm.refresh() }
            }

            if showMenu && !vm.restricted {
                Color.black.opacity(0.5)
                    .ignoresSafeArea()
                    .onTapGesture { withAnimation { showMenu = false } }
                    .transition(.opacity)
                NavDrawerView(vm: vm) { path in
                    withAnimation { showMenu = false }
                    vm.selectTab(path)
                }
                .transition(.move(edge: .leading))
            }
        }
        // Surface the Live Activity trigger's outcome (started / disabled / error) so tapping the
        // button is never a silent no-op.
        .alert("Live Activity", isPresented: Binding(
            get: { vm.liveActivityMessage != nil },
            set: { if !$0 { vm.liveActivityMessage = nil } }
        )) {
            Button("OK", role: .cancel) { vm.liveActivityMessage = nil }
        } message: {
            Text(vm.liveActivityMessage ?? "")
        }
    }

    @ViewBuilder private var content: some View {
        switch vm.compat {
        case .tooNew:
            MessageView(title: "Update needed",
                        detail: "This dashboard shell is older than the server. Update the app.")
        case .ok:
            if let manifest = vm.current {
                // .id resets the page's FormStore when switching tabs.
                PageContent(manifest: manifest, scope: activeScope, actions: actions)
                    .id(vm.currentPath)
            } else if vm.isOffline {
                MessageView(title: "Offline",
                            detail: "Can't reach the dashboard and there's nothing cached yet.")
            } else {
                ProgressView().controlSize(.large).tint(.white)
            }
        }
    }
}

/// One page, with its own FormStore for editable fields.
private struct PageContent: View {
    let manifest: Manifest
    let scope: Scope
    let actions: SDUIActions
    @StateObject private var form = FormStore()

    var body: some View {
        NodeView(node: manifest.screen, scope: scope)
            .environment(\.sduiActions, actions)
            .environmentObject(form)
    }
}

/// "Update available" bar shown when the server has a newer native build. Tapping starts the
/// install IN PLACE — the bar flips to an "Installing…" status on the home screen rather than
/// navigating away — then fires the itms-services URL (one tap into the system installer; no
/// Safari, no cable). The final iOS install confirmation is Apple-enforced. We can't observe the
/// OTA install itself, so completion shows when the updated build relaunches (the bar/badge clear
/// via the version check); if the system prompt is cancelled, the bar reverts after a bit.
struct UpdateBanner: View {
    let onInstall: () -> Void
    @State private var installing = false

    var body: some View {
        Button(action: tap) {
            HStack(spacing: 8) {
                if installing {
                    ProgressView().controlSize(.small).tint(Color.accentColor)
                    Text("Installing… confirm on the prompt").fontWeight(.semibold)
                    Spacer()
                } else {
                    Image(systemName: "arrow.down.app.fill")
                    Text("Update available").fontWeight(.semibold)
                    Spacer()
                    Text("Install").fontWeight(.semibold)
                    Image(systemName: "chevron.right").font(.caption)
                }
            }
            .font(.subheadline)
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(Color.accentColor.opacity(0.18))
            .foregroundStyle(Color.accentColor)
            .animation(.default, value: installing)
        }
        .disabled(installing)
    }

    private func tap() {
        guard !installing else { return }
        installing = true
        onInstall()   // hands off to the system installer; stays on this screen
        // No completion callback from an OTA install — revert if the user cancelled the prompt so
        // the bar isn't stuck "Installing…". A real install replaces the app before this fires.
        Task { try? await Task.sleep(nanoseconds: 30_000_000_000); installing = false }
    }
}

struct MessageView: View {
    let title: String
    let detail: String
    var body: some View {
        VStack(spacing: 8) {
            Text(title).font(.title2).fontWeight(.semibold)
            Text(detail).font(.subheadline).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
