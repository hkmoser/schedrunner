import SwiftUI

/// Access mode chosen by the entry passcode.
enum AccessMode { case full, decoy }

/// 4-digit passcode → access mode:
///   • 1937            → full experience (all pages)
///   • all-even digits → decoy __APP_NAME_LOWER__ only (dummy content, no navigation)
/// Anything else returns nil (rejected).
func classifyPasscode(_ code: String) -> AccessMode? {
    guard code.count == 4, code.allSatisfy(\.isNumber) else { return nil }
    if code == "1937" { return .full }
    if code.allSatisfy({ ($0.wholeNumberValue ?? 1) % 2 == 0 }) { return .decoy }
    return nil
}

/// Numeric passcode keypad shown before the app unlocks. Mirrors Web/src/sdui/lock.ts.
struct PasscodeView: View {
    let onUnlock: (AccessMode) -> Void

    @State private var code = ""
    @State private var shake = false

    private let keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"]
    private let cols = Array(repeating: GridItem(.fixed(76), spacing: 18), count: 3)

    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "lock.fill")
                .font(.system(size: 30)).foregroundStyle(.secondary)
            Text("Enter Passcode").font(.headline)
            HStack(spacing: 18) {
                ForEach(0..<4, id: \.self) { i in
                    Circle()
                        .strokeBorder(Color.secondary, lineWidth: 1.5)
                        .background(Circle().fill(i < code.count ? Color.accentColor : .clear))
                        .frame(width: 14, height: 14)
                }
            }
            .offset(x: shake ? -8 : 0)
            .animation(.default, value: shake)
            .padding(.bottom, 8)
            LazyVGrid(columns: cols, spacing: 18) {
                ForEach(keys, id: \.self) { k in
                    if k.isEmpty {
                        Color.clear.frame(width: 76, height: 76)
                    } else {
                        Button { tap(k) } label: {
                            Text(k)
                                .font(.system(size: k == "⌫" ? 22 : 28))
                                .frame(width: 76, height: 76)
                                .background(k == "⌫" ? Color.clear : Color.white.opacity(0.05))
                                .clipShape(Circle())
                                .overlay {
                                    if k != "⌫" { Circle().stroke(Color.white.opacity(0.12)) }
                                }
                        }
                        .foregroundStyle(k == "⌫" ? Color.secondary : Color.primary)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(32)
    }

    private func tap(_ k: String) {
        if k == "⌫" {
            if !code.isEmpty { code.removeLast() }
            return
        }
        guard code.count < 4, !shake else { return }
        code += k
        guard code.count == 4 else { return }
        if let mode = classifyPasscode(code) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.14) { onUnlock(mode) }
        } else {
            shake = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                shake = false
                code = ""
            }
        }
    }
}
