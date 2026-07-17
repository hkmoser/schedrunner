import SwiftUI

/// Monospace preformatted block (log tails / raw output). Mirrors the web `code`
/// component — scrolls both axes, capped height.
struct CodeBlockView: View {
    let text: String

    var body: some View {
        ScrollView([.vertical, .horizontal], showsIndicators: true) {
            Text(text)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(Color(hex: "#cdd6f4"))
                .textSelection(.enabled)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxHeight: 320)
        .background(Color(hex: "#0b0f1c"))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.08)))
    }
}

/// Renders a safe subset of Markdown (docs viewer). Mirrors Web/src/sdui/markdown.ts:
/// headings, bullets/ordered lists, blockquotes, fenced code, rules, and inline
/// emphasis/code/links via AttributedString.
struct MarkdownView: View {
    let source: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(parse().enumerated()), id: \.offset) { _, block in
                row(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // Long-form docs read best in a serif — `.serif` design renders New York on iOS,
        // matching the web viewer's --font-serif. Code blocks keep their own monospaced font.
        .font(.system(size: 18, design: .serif))
        .lineSpacing(4)
    }

    private enum Block {
        case heading(Int, String)
        case bullet(String)
        case ordered(String)
        case quote(String)
        case code(String)
        case rule
        case paragraph(String)
    }

    private func parse() -> [Block] {
        var blocks: [Block] = []
        let lines = source.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var i = 0
        var para: [String] = []
        func flush() {
            if !para.isEmpty {
                blocks.append(.paragraph(para.joined(separator: " ")))
                para = []
            }
        }
        while i < lines.count {
            let trimmed = lines[i].trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                flush()
                var buf: [String] = []
                i += 1
                while i < lines.count && !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    buf.append(lines[i]); i += 1
                }
                i += 1
                blocks.append(.code(buf.joined(separator: "\n")))
                continue
            }
            if let h = heading(trimmed) { flush(); blocks.append(.heading(h.0, h.1)) }
            else if trimmed == "---" || trimmed == "***" || trimmed == "___" { flush(); blocks.append(.rule) }
            else if trimmed.hasPrefix("> ") { flush(); blocks.append(.quote(String(trimmed.dropFirst(2)))) }
            else if let b = bullet(trimmed) { flush(); blocks.append(.bullet(b)) }
            else if let o = ordered(trimmed) { flush(); blocks.append(.ordered(o)) }
            else if trimmed.isEmpty { flush() }
            else { para.append(trimmed) }
            i += 1
        }
        flush()
        return blocks
    }

    private func heading(_ s: String) -> (Int, String)? {
        var n = 0
        for ch in s { if ch == "#" { n += 1 } else { break } }
        guard n >= 1, n <= 6, s.count > n,
              s[s.index(s.startIndex, offsetBy: n)] == " " else { return nil }
        return (n, String(s.dropFirst(n + 1)))
    }

    private func bullet(_ s: String) -> String? {
        for p in ["- ", "* ", "+ "] where s.hasPrefix(p) { return String(s.dropFirst(2)) }
        return nil
    }

    private func ordered(_ s: String) -> String? {
        guard let dot = s.firstIndex(of: ".") else { return nil }
        let num = s[s.startIndex..<dot]
        guard !num.isEmpty, num.allSatisfy(\.isNumber) else { return nil }
        let rest = s[s.index(after: dot)...].trimmingCharacters(in: .whitespaces)
        return rest.isEmpty ? nil : rest
    }

    private func inline(_ s: String) -> Text {
        if let a = try? AttributedString(markdown: s) { return Text(a) }
        return Text(s)
    }

    @ViewBuilder private func row(_ block: Block) -> some View {
        switch block {
        case .heading(let lvl, let t):
            inline(t).font(headingFont(lvl)).fontWeight(.bold).padding(.top, lvl <= 2 ? 8 : 4)
        case .bullet(let t):
            HStack(alignment: .top, spacing: 8) { Text("•"); inline(t) }
        case .ordered(let t):
            HStack(alignment: .top, spacing: 8) { Text("–"); inline(t) }
        case .quote(let t):
            inline(t).italic().foregroundStyle(.secondary).padding(.leading, 12)
                .overlay(Rectangle().fill(Color.accentColor).frame(width: 3), alignment: .leading)
        case .code(let t):
            CodeBlockView(text: t)
        case .rule:
            Rectangle().fill(Color.white.opacity(0.12)).frame(height: 1).padding(.vertical, 6)
        case .paragraph(let t):
            inline(t)
        }
    }

    private func headingFont(_ lvl: Int) -> Font {
        switch lvl {
        case 1: return .system(size: 30, weight: .bold, design: .serif)
        case 2: return .system(size: 24, weight: .bold, design: .serif)
        case 3: return .system(size: 20, weight: .semibold, design: .serif)
        default: return .system(size: 18, weight: .semibold, design: .serif)
        }
    }
}
