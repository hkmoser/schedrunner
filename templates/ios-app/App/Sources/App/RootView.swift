import SwiftUI

struct RootView: View {
    @ObservedObject var vm: __APP_NAME__ViewModel

    var body: some View {
        ZStack {
            Color(hex: "#0b1020").ignoresSafeArea()
            if vm.mode != nil {
                __APP_NAME__View(vm: vm)
            } else {
                PasscodeView(onUnlock: vm.unlock)
            }
        }
    }
}
