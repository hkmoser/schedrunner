// Pages the user has explicitly flagged to "keep offline". The background sweep refreshes
// these aggressively alongside the main nav pages — even on the light timer and even if
// they're expensive (a BigQuery preview) — so a pinned drill-down page is always current
// by the time you go offline. Bounds the otherwise-infinite page space (GPS coords, the
// whole BQ project tree, the whole Docs tree) to just what you care about keeping.
//
// Mirrors the resilient localStorage pattern in cache.ts: every access is try/caught and
// non-fatal (the SW page cache still covers offline if storage is unavailable).
const KEY = "dashboard.pins.v1";

/** Every path currently pinned for offline (deduped, strings only). */
export function pinnedPaths(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr) ? arr.filter((p): p is string => typeof p === "string") : [];
  } catch {
    return [];
  }
}

export function isPinned(path: string): boolean {
  return pinnedPaths().includes(path);
}

/** Toggle a path's pinned state; returns the new state (true = now pinned). */
export function togglePin(path: string): boolean {
  const pins = new Set(pinnedPaths());
  const nowPinned = !pins.has(path);
  if (nowPinned) pins.add(path);
  else pins.delete(path);
  try {
    localStorage.setItem(KEY, JSON.stringify(Array.from(pins)));
  } catch {
    /* non-fatal */
  }
  return nowPinned;
}
