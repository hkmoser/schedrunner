import type { JSONValue } from "../sdui/manifest";
import { resolveColor, type Scope } from "../sdui/binding";

// A Databricks-style run-history bar chart: one bar per recent iteration, height ∝ processing
// time (pre-normalized 0..100 by the server), colored by success/failure. Clients stay dumb —
// they only draw what the server computed. Mirrors the native BarChartView.
export function renderBarChart(
  raw: JSONValue | undefined,
  height: number,
  scope: Scope,
): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "barchart";
  wrap.style.height = `${height}px`;

  const bars = Array.isArray(raw) ? raw : [];
  for (const b of bars) {
    if (!b || typeof b !== "object" || Array.isArray(b)) continue;
    const o = b as Record<string, JSONValue>;
    const hp = Math.max(0, Math.min(100, Number(o.heightPct) || 0));
    const color = resolveColor(String(o.color ?? "$textSecondary"), scope) ?? "var(--c-textSecondary)";
    const label = o.labelFormatted != null ? String(o.labelFormatted) : "";
    const caption = o.captionFormatted != null ? String(o.captionFormatted) : "";

    const cell = document.createElement("div");
    cell.className = "barchart-cell";
    cell.title = [label, caption].filter(Boolean).join(" · ");

    const track = document.createElement("div");
    track.className = "barchart-track";
    const bar = document.createElement("div");
    bar.className = "barchart-bar";
    bar.style.height = `${hp}%`;
    bar.style.background = color;
    track.appendChild(bar);

    const lab = document.createElement("div");
    lab.className = "barchart-label";
    lab.textContent = label;

    cell.appendChild(track);
    cell.appendChild(lab);
    wrap.appendChild(cell);
  }
  return wrap;
}
