import SwiftUI
import WidgetKit

@main
struct __APP_NAME__App: App {
    @StateObject private var vm = __APP_NAME__ViewModel()
    @Environment(\.scenePhase) private var scenePhase
    // UIKit delegate for APNs device-token callbacks (SwiftUI doesn't surface them).
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        BackgroundRefresh.register()
    }

    var body: some Scene {
        WindowGroup {
            RootView(vm: vm)
                .preferredColorScheme(.dark)
                .tint(Color(hex: "#6ea8fe"))
                .task { vm.bootstrap() }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                if vm.mode != nil { Task { await vm.refresh() }; vm.checkForUpdate() }
                WidgetCenter.shared.reloadAllTimelines()   // refresh widgets when the app opens
            case .background:
                BackgroundRefresh.schedule()
                vm.lockApp()  // require the passcode again on return
            default:
                break
            }
        }
    }
}
