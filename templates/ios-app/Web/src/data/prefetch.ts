import type { NavItem } from "../sdui/manifest";
import { navLeaves } from "../sdui/drawer";

/** A parameterized page (has a query string) — a drill-down like a specific BQ-table
 *  preview or Docs path, vs. a base/"main" page. Used to cache main pages first. */
export function isParameterized(path: string): boolean {
  return path.includes("?");
}

// Which pages a background sweep should pull into the cache so the whole app works
// offline (not just the pages you happen to have opened): the __APP_NAME_LOWER__ plus every nav
// leaf, de-duplicated, minus the page currently on screen (the foreground loader already
// keeps that one live). Ordered base/main pages first, then parameterized sub-pages, so
// the important pages are cached before the long tail. Pure + exported for tests.
export function prefetchTargets(
  nav: NavItem[] | undefined,
  mainPath: string,
  currentPath: string,
  extra: string[] = [],
): string[] {
  const here = (currentPath || mainPath).split("?")[0];
  // __APP_NAME_LOWER__ + every nav leaf + every already-cached page (incl. parameterized
  // sub-pages like a specific BQ-table preview or a Docs path you've opened).
  const all = [mainPath, ...navLeaves(nav).map((l) => l.path), ...extra].filter(
    (p): p is string => typeof p === "string" && p.length > 0,
  );
  const uniq = Array.from(new Set(all)).filter((p) => p !== here);
  // Stable sort: base (non-parameterized) pages first, parameterized sub-pages after.
  return uniq.sort((a, b) => Number(isParameterized(a)) - Number(isParameterized(b)));
}

// Every internal page link (`/screen/…` or `/__APP_NAME_LOWER__…`) found anywhere in a fetched
// manifest's data — navHref / detailHref / backHref / tab hrefs, etc. Lets the prefetch
// crawl DISCOVER drill-down pages (a dataset's tables, a table's preview, a Docs path, a
// log's 24h view) and cache them all, not just the base nav pages. Pure + de-duplicated.
export function collectInternalLinks(data: unknown): string[] {
  const out: string[] = [];
  const visit = (v: unknown) => {
    if (typeof v === "string") {
      if (/^\/(screen\/|__APP_NAME_LOWER__)/.test(v)) out.push(v);
    } else if (Array.isArray(v)) {
      v.forEach(visit);
    } else if (v && typeof v === "object") {
      Object.values(v as Record<string, unknown>).forEach(visit);
    }
  };
  visit(data);
  return Array.from(new Set(out));
}
