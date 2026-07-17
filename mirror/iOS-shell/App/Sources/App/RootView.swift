import SwiftUI

struct RootView: View {
    @ObservedObject var vm: DashboardViewModel

    var body: some View {
        ZStack {
            Color(hex: "#0b1020").ignoresSafeArea()
            if vm.mode != nil {
                DashboardView(vm: vm)
            } else {
                PasscodeView(onUnlock: vm.unlock)
            }
        }
    }
}
