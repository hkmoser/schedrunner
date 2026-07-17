// Lazy-loaded Leaflet map for the `map` component. Draws every segment (moves as
// polylines, stops as markers) and exposes a controller so tapping a segment in the
// list can highlight + zoom to it. Offline/unavailable degrades gracefully.

let leafletPromise: Promise<any> | null = null;
const controllers = new Map<string, { focus: (index: number) => void }>();

/** Highlight + zoom to a segment index on a registered map. Called by the list. */
export function focusMap(key: string, index: number) {
  controllers.get(key)?.focus(index);
}

function loadLeaflet(): Promise<any> {
  const w = window as any;
  if (w.L) return Promise.resolve(w.L);
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    js.onload = () => resolve((window as any).L);
    js.onerror = reject;
    document.head.appendChild(js);
  });
  return leafletPromise;
}

function empty(container: HTMLElement, msg: string) {
  container.innerHTML = `<div class="map-empty">${msg}</div>`;
}

// Stop-dot radius grows with dwell time (area ∝ minutes → radius ∝ √minutes), clamped
// to a tappable range so a 2-minute stop and a 6-hour stay are both visible but distinct.
function stopRadius(durationMin: unknown): number {
  const m = typeof durationMin === "number" ? durationMin : Number(durationMin) || 0;
  return Math.max(6, Math.min(26, 5 + Math.sqrt(Math.max(0, m)) * 1.7));
}

export function renderMap(container: HTMLElement, raw: unknown, mapId = "activity") {
  const data = (raw ?? {}) as any;
  const segs: any[] = Array.isArray(data.segments) ? data.segments : [];
  const center = data.center;
  if (!center && segs.length === 0) {
    empty(container, "No recent location");
    return;
  }
  loadLeaflet()
    .then((L) => {
      const map = L.map(container, { zoomControl: false, attributionControl: false });
      L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 19,
        attribution: "© OpenStreetMap, © CARTO",
      }).addTo(map);

      const layers = new Map<number, any>();
      const all: [number, number][] = [];
      for (const s of segs) {
        const pts: [number, number][] = (s.points || []).map((p: number[]) => [p[0], p[1]]);
        pts.forEach((p) => all.push(p));
        if (s.category === "Moving" && pts.length > 1) {
          layers.set(s.index, L.polyline(pts, { color: s.color || "#6ea8fe", weight: 3, opacity: 0.65 }).addTo(map));
        } else {
          const c = s.center || (pts[0] ? { lat: pts[0][0], lon: pts[0][1] } : null);
          if (c) {
            all.push([c.lat, c.lon]);
            // Stops are dots sized by how long the device dwelled there.
            const r = stopRadius(s.durationMin);
            const marker = L.circleMarker([c.lat, c.lon], {
              radius: r, color: "#0b1020", weight: 1.5, fillColor: s.color || "#43d18a", fillOpacity: 0.85,
            }).addTo(map);
            marker._baseRadius = r;
            layers.set(s.index, marker);
          }
        }
      }
      if (all.length > 1) map.fitBounds(all, { padding: [26, 26], maxZoom: 15 });
      else if (center) map.setView([center.lat, center.lon], 14);

      // Current position emphasis.
      if (center) {
        L.circleMarker([center.lat, center.lon], {
          radius: 7, color: "#ffffff", weight: 2, fillColor: "#6ea8fe", fillOpacity: 1,
        }).addTo(map);
      }

      let active: any = null;
      const deemphasize = () => {
        if (active) {
          if (active.setStyle) active.setStyle({ weight: 3, opacity: 0.65 });
          if (active.setRadius) active.setRadius(active._baseRadius ?? 6); // back to dwell size
          active = null;
        }
      };
      const ctrl = {
        focus: (index: number) => {
          // index < 0 => show the whole day (fit to all segments, clear highlight).
          if (index < 0) {
            deemphasize();
            if (all.length > 1) map.fitBounds(all, { padding: [26, 26], maxZoom: 15 });
            return;
          }
          const layer = layers.get(index);
          if (!layer) return;
          if (active && active !== layer) deemphasize();
          active = layer;
          if (layer.setStyle) layer.setStyle({ weight: 6, opacity: 1 });
          if (layer.setRadius) layer.setRadius((layer._baseRadius ?? 6) + 5);
          layer.bringToFront?.();
          if (layer.getBounds) map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 16 });
          else if (layer.getLatLng) map.setView(layer.getLatLng(), 16);
        },
      };
      controllers.set(mapId, ctrl);

      setTimeout(() => {
        map.invalidateSize();
        // Default-select the segment the server flagged (latest moving).
        if (typeof data.focus === "number") ctrl.focus(data.focus);
      }, 0);
    })
    .catch(() => empty(container, "Map unavailable offline"));
}
