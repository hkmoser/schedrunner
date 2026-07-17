import type { Manifest } from "../sdui/manifest";

const PREFIX = "dashboard.page.v1:";
const LEGACY_KEY = "dashboard.manifest.v1"; // earlier dashboard-only cache

export interface CachedManifest {
  manifest: Manifest;
  fetchedAt: number; // epoch ms
}

// Per-page cache so every tab (dashboard, Activity, Config) paints instantly from
// the last-known data and works offline — the location data is cached aggressively
// on the device, not just the home screen. The Workbox SW is the second layer.
export function loadCached(path = "/dashboard"): CachedManifest | null {
  try {
    const raw = localStorage.getItem(PREFIX + path) ?? (path === "/dashboard" ? localStorage.getItem(LEGACY_KEY) : null);
    return raw ? (JSON.parse(raw) as CachedManifest) : null;
  } catch {
    return null;
  }
}

export function saveCached(manifest: Manifest, path = "/dashboard"): void {
  try {
    const payload: CachedManifest = { manifest, fetchedAt: Date.now() };
    localStorage.setItem(PREFIX + path, JSON.stringify(payload));
  } catch {
    // Quota/availability errors are non-fatal; the SW cache still covers offline.
  }
}

/** Drop every cached page manifest (used by the hard-refresh / "start fresh" action). */
export function clearCachedPages(): void {
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && (k.startsWith(PREFIX) || k === LEGACY_KEY)) keys.push(k);
    }
    keys.forEach((k) => localStorage.removeItem(k));
  } catch {
    /* non-fatal */
  }
}

/** Every page path currently in the cache — including parameterized sub-pages you've
 *  opened (e.g. a specific BQ-table preview). Used by the prefetch sweep so anything
 *  you've visited is kept warm for offline, not just the base nav pages. */
export function cachedPagePaths(): string[] {
  const out: string[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(PREFIX)) out.push(k.slice(PREFIX.length));
      else if (k === LEGACY_KEY) out.push("/dashboard");
    }
  } catch {
    /* non-fatal */
  }
  return out;
}

/** Rough page count + byte size of the per-page cache (UTF-16 ≈ 2 bytes/char), for the
 *  "~N pages · ~X MB" offline-cache readout in the drawer. */
export function cacheStats(): { pages: number; bytes: number } {
  let pages = 0;
  let bytes = 0;
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && (k.startsWith(PREFIX) || k === LEGACY_KEY)) {
        pages++;
        const v = localStorage.getItem(k);
        bytes += (k.length + (v ? v.length : 0)) * 2;
      }
    }
  } catch {
    /* non-fatal */
  }
  return { pages, bytes };
}

export function freshnessLabel(fetchedAt: number): string {
  const mins = Math.floor((Date.now() - fetchedAt) / 60000);
  if (mins < 1) return "Updated just now";
  if (mins < 60) return `Updated ${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `Updated ${hrs}h ago`;
  return `Updated ${Math.floor(hrs / 24)}d ago`;
}

// The app-bar/header status: real freshness from the cache time, or an Offline label
// (keeping the age) when the server is unreachable. Single source of truth so the
// app bar and the per-page meta binding always agree.
export function statusText(fetchedAt: number | undefined, reachable: boolean): string {
  const base = fetchedAt ? freshnessLabel(fetchedAt) : "Updating…";
  return reachable ? base : base.replace(/^Updated /, "Offline · ");
}
