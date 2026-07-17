// Minimal, safe Markdown → HTML for the Docs viewer. Input is escaped first, so
// no raw HTML from a document can inject markup; we then apply a small, well-known
// subset (headings, bold/italic/code, links, lists, blockquotes, code fences, rules).

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Inline spans, applied to already-escaped text.
function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\b_([^_]+)_\b/g, "<em>$1</em>")
    // [text](http(s)://url) — only safe http(s) links.
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

export function renderMarkdown(src: string): string {
  const lines = escapeHtml(src.replace(/\r\n?/g, "\n")).split("\n");
  const out: string[] = [];
  let i = 0;
  let inList: "ul" | "ol" | null = null;

  const closeList = () => {
    if (inList) {
      out.push(`</${inList}>`);
      inList = null;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block ``` ... ```
    if (/^\s*```/.test(line)) {
      closeList();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      out.push(`<pre class="md-code"><code>${buf.join("\n")}</code></pre>`);
      continue;
    }

    // Headings # … ######
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      closeList();
      const level = h[1].length;
      out.push(`<h${level}>${inline(h[2])}</h${level}>`);
      i++;
      continue;
    }

    // Horizontal rule
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      closeList();
      out.push("<hr>");
      i++;
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      closeList();
      out.push(`<blockquote>${inline(line.replace(/^\s*>\s?/, ""))}</blockquote>`);
      i++;
      continue;
    }

    // Unordered list
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      if (inList !== "ul") {
        closeList();
        out.push("<ul>");
        inList = "ul";
      }
      out.push(`<li>${inline(ul[1])}</li>`);
      i++;
      continue;
    }

    // Ordered list
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      if (inList !== "ol") {
        closeList();
        out.push("<ol>");
        inList = "ol";
      }
      out.push(`<li>${inline(ol[1])}</li>`);
      i++;
      continue;
    }

    // Blank line
    if (/^\s*$/.test(line)) {
      closeList();
      i++;
      continue;
    }

    // Paragraph (gather consecutive non-blank, non-special lines)
    closeList();
    const para: string[] = [line];
    i++;
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^(#{1,6})\s|^\s*```|^\s*[-*+]\s|^\s*\d+\.\s|^\s*>\s?/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  closeList();
  return out.join("\n");
}
