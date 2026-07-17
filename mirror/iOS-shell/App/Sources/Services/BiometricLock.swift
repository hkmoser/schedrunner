import Foundation
import LocalAuthentication

/// Face ID gate. Falls back to device passcode. Failure leaves the app locked.
@MainActor
final class BiometricLock: ObservableObject {
    @Published var isUnlocked = false

    func authenticate() {
        let context = LAContext()
        context.localizedFallbackTitle = "Use Passcode"
        var error: NSError?
        let policy: LAPolicy = .deviceOwnerAuthentication // biometrics, then passcode
        guard context.canEvaluatePolicy(policy, error: &error) else {
            // No biometrics/passcode configured — don't hard-lock the owner out.
            isUnlocked = true
            return
        }
        context.evaluatePolicy(policy, localizedReason: "Unlock your dashboard") { [weak self] success, _ in
            Task { @MainActor in self?.isUnlocked = success }
        }
    }

    func lock() { isUnlocked = false }
}
