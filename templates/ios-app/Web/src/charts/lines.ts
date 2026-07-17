import type { JSONValue } from "../sdui/manifest";

export interface ChartSeries {
  color: string;
  points: number[];
  x?: number[]; // normalized 0..1 x positions; defaults to even spacing by index
  dashed?: boolean; // render as a dashed line (e.g. estimated/backfilled history)
}

// Build a normalized multi-line SVG (relative performance). All series share one
// y-scale so lines are visually comparable. Mirrors the native Swift Charts card.
export function renderLineChart(
  raw: JSONValue | undefined,
  height: number,
  fallbackColor: string,
): SVGSVGElement {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  const width = 320; // viewBox units; scales responsively via CSS width:100%
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "chart");
  svg.style.width = "100%";
  svg.style.height = `${height}px`;

  const series: ChartSeries[] = [];
  if (Array.isArray(raw)) {
    for (const s of raw) {
      if (s && typeof s === "object" && Array.isArray((s as Record<string, JSONValue>).points)) {
        const pts = ((s as Record<string, JSONValue>).points as JSONValue[])
          .map((n) => Number(n))
          .filter((n) => Number.isFinite(n));
        const color = String((s as Record<string, JSONValue>).color ?? fallbackColor);
        const xRaw = (s as Record<string, JSONValue>).x;
        const x = Array.isArray(xRaw) ? xRaw.map((v) => Number(v)).filter((v) => Number.isFinite(v)) : undefined;
        const dashed = Boolean((s as Record<string, JSONValue>).dashed);
        if (pts.length > 1) {
          series.push({ color, points: pts, x: x && x.length === pts.length ? x : undefined, dashed });
        }
      }
    }
  }
  if (series.length === 0) return svg;

  let min = Infinity;
  let max = -Infinity;
  for (const s of series) for (const p of s.points) {
    if (p < min) min = p;
    if (p > max) max = p;
  }
  const range = max - min || 1;
  const pad = 6;
  const usableH = height - pad * 2;

  for (const s of series) {
    const n = s.points.length;
    const coords = s.points.map((p, i) => {
      const x = (s.x ? s.x[i] : i / (n - 1)) * width;
      const y = pad + (1 - (p - min) / range) * usableH;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    });
    const poly = document.createElementNS(NS, "polyline");
    poly.setAttribute("points", coords.join(" "));
    poly.setAttribute("fill", "none");
    poly.style.stroke = s.color; // via style (not attribute) so a var(--c-*) color resolves
    poly.setAttribute("stroke-width", "2");
    poly.setAttribute("stroke-linejoin", "round");
    poly.setAttribute("stroke-linecap", "round");
    if (s.dashed) poly.setAttribute("stroke-dasharray", "5 4");
    poly.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(poly);
  }
  return svg;
}
