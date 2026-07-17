import "./styles.css";
import { registerSW } from "virtual:pwa-register";
import type { JSONValue, Manifest } from "./sdui/manifest";
import { checkCompat } from "./sdui/compat";
import { applyTheme, activeTheme } from "./sdui/theme";
import { renderScreen } from "./sdui/renderer";
import { buildAppBar, buildDrawer, activeTitle } from "./sdui/drawer";
import type { Scope } from "./sdui/binding";
import type { ActionContext, SubmitResult } from "./sdui/actions";
import type { FormItem } from "./sdui/form";
import { fetchManifestAt, postJSON } from "./data/client";
import { loadCached, saveCached, clearCachedPages, cachedPagePaths, cacheStats, statusText } from "./data/cache";
import { locationQuery, requestGeolocation } from "./data/location";
import { prefetchTargets, collectInternalLinks, isParameterized } from "./data/prefetch";
import { pinnedPaths, isPinned, togglePin } from "./data/pins";
import { focusMap } from "./sdui/map";
import { renderLockScreen, type AccessMode } from "./sdui/lock";
import { decoyManifest } from "./sdui/decoy";

const app = document.getElementById("app")!;

// Drive the app-shell/drawer height from the visible viewport so the layout fills the
// screen EXACTLY on iOS standalone PWAs. There, `position:fixed; inset:0` can resolve to
// a layout viewport taller than the interactive area, leaving a dead, non-interactive
// band at the bottom — which also swallows taps on the lowest nav item (Config).
// window.innerHeight is the true visible height (and, unlike visualViewport, doesn't
// shrink when the keyboard overlays), so re-applying it on resize/rotate keeps the shell
// pinned to the live area. See `.app-shell`/`.drawer` in styles.css.
function syncAppHeight(): void {
  document.documentElement.style.setProperty("--app-height", `${window.innerHeight}px`);
}
syncAppHeight();
window.addEventListener("resize", syncAppHeight);
window.addEventListener("orientationchange", () => {
  syncAppHeight();
  window.setTimeout(syncAppHeight, 300); // iOS reports a stale innerHeight right after rotating
});

// Set by the passcode gate: "decoy" shows a single dummy dashboard with no menu
// (all-even code); "full" shows the whole app (1937).
let restricted = false;
const MAIN = "/dashboard";

let current = {
  path: MAIN,
  query: locationQuery(),
  manifest: undefined as Manifest | undefined,
  fetchedAt: undefined as number | undefined, // when the ACTIVE page was last pulled into the app
  reachable: true, // did the last fetch to the server succeed (false = offline / off Tailscale)
};
let loadToken = 0;

function pageQuery(path: string): string {
  return path === MAIN ? current.query : "";
}

// Accurate freshness for the active page, derived from when its data was actually
// cached into the app — and flipped to an Offline label when the server is unreachable.
function statusLabel(): string {
  return statusText(current.fetchedAt, current.reachable);
}

// Overwrite the manifest's meta freshness with the real, client-measured value so
// every template that binds `meta.updatedAtFormatted` shows the truth (not the
// server's "Updated just now" placeholder), including while offline.
function applyMeta(manifest: Manifest): void {
  const data = (manifest.data ?? {}) as Record<string, JSONValue>;
  const meta = (data.meta && typeof data.meta === "object" && !Array.isArray(data.meta)
    ? data.meta
    : {}) as Record<string, JSONValue>;
  meta.updatedAtFormatted = statusLabel();
  meta.stale = !current.reachable;
  data.meta = meta;
  manifest.data = data;
}

// Loaded-version line for the drawer footer, so it's obvious whether a deploy
// landed: the app (bundle) build is baked in at build time; the server build comes
// from the manifest meta. If these don't change after `make update`, the bundle is
// stale (hard refresh) or the server didn't redeploy.
function buildInfo(manifest: Manifest): string {
  const meta = (manifest.data?.meta ?? {}) as Record<string, JSONValue>;
  const server = typeof meta.build === "string" ? meta.build : "";
  const channel = __APP_CHANNEL__ === "next" ? "NEXT · " : "";
  return `${channel}app ${__APP_BUILD__}` + (server && server !== "dev" ? ` · server ${server}` : "");
}

// Human "built …" date/time for the line under the version, from the bundle build
// stamp baked in at build time — so it's obvious at a glance how old the loaded
// build is, not just an opaque SHA.
function buildDate(): string {
  const t = Date.parse(__APP_BUILD_TIME__);
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `Built ${date} · ${time}`;
}

function showMessage(title: string, detail: string) {
  app.replaceChildren();
  const wrap = document.createElement("div");
  wrap.className = "message";
  const h = document.createElement("div");
  h.className = "message-title";
  h.textContent = title;
  const p = document.createElement("div");
  p.className = "message-detail";
  p.textContent = detail;
  wrap.append(h, p);
  app.appendChild(wrap);
}

// A brief, self-dismissing toast (non-blocking) — used for lightweight notices like an
// iOS-only feature tapped on the web PWA.
let toastTimer: number | undefined;
function toast(message: string) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("toast-show");
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el?.classList.remove("toast-show"), 3600);
}

const actions: ActionContext = {
  refresh: () => void refresh(),
  notifyNativeOnly: (feature) =>
    toast(`${feature} are available in the iOS app only — not in the web app.`),
  setPref: (key, value) => {
    try {
      localStorage.setItem(`pref.${key}`, JSON.stringify(value));
    } catch {
      /* non-fatal */
    }
    // The weather Home/Current toggle changes the dashboard query — re-pull so it applies.
    if (key === "weatherLoc") {
      current.query = locationQuery();
      if (current.path === MAIN) void refresh();
    }
  },
  navigate: (target) => void selectTab(target),
  submit: (url, items) => submit(url, items),
  focus: (key, index) => focusMap(key, index),
};

function render(manifest: Manifest) {
  const compat = checkCompat(manifest);
  if (!compat.ok) {
    showMessage(
      compat.reason === "tooNew" ? "Update needed" : "Couldn't load",
      compat.reason === "tooNew"
        ? "This dashboard shell is older than the server. Update the app."
        : "The dashboard data was malformed.",
    );
    return;
  }
  applyTheme(manifest.theme);
  if (!restricted) applyMeta(manifest); // real freshness/offline for in-page meta bindings
  const scope: Scope = { data: manifest.data ?? {}, theme: activeTheme(manifest.theme) };

  const shell = document.createElement("div");
  shell.className = "app-shell";
  const host = document.createElement("div");
  host.className = "screen-host";
  host.appendChild(renderScreen(manifest.screen, scope, actions));

  // Restricted (decoy) mode is a single page: no app bar or menu to navigate away.
  if (!restricted) {
    // Sub-pages carry a ?query (e.g. Docs ?path=…); match nav by pathname only.
    const navPath = current.path.split("?")[0];
    const appbar = buildAppBar(
      activeTitle(manifest.nav, navPath),
      () => shell.classList.add("nav-open"),
      { label: statusLabel(), offline: !current.reachable, onRefresh: () => void refresh() },
      { pinned: isPinned(current.path), onToggle: () => toggleCurrentPin() },
    );
    const { drawer, scrim } = buildDrawer(
      manifest.nav,
      navPath,
      (path) => {
        shell.classList.remove("nav-open");
        void selectTab(path);
      },
      () => shell.classList.remove("nav-open"),
      { run: () => void hardRefresh(), enabled: current.reachable },
      buildInfo(manifest),
      buildDate(),
      { run: () => void prefetchAll(true), statusText: prefetchStatus(), running: prefetching },
    );
    shell.append(appbar, host, scrim, drawer);
  } else {
    shell.appendChild(host);
  }
  app.replaceChildren(shell);
}

// Token-based so a tab tap is always responsive: it switches instantly from cache
// and a slow in-flight load never blocks (or overwrites) a newer tap.
async function selectTab(path: string) {
  // Restricted (decoy) mode is a single local page: no navigation or network.
  if (restricted) return;
  const token = ++loadToken;
  current.path = path;
  document.body.classList.add("refreshing");
  const cachedPage = loadCached(path);
  if (cachedPage) {
    current.manifest = cachedPage.manifest;
    current.fetchedAt = cachedPage.fetchedAt; // paint reflects when this page was really cached
    render(cachedPage.manifest);
  }
  try {
    const manifest = await fetchManifestAt(path, pageQuery(path));
    if (token !== loadToken) return; // superseded by a newer selection
    current.manifest = manifest;
    current.fetchedAt = Date.now();
    current.reachable = true;
    saveCached(manifest, path);
    render(manifest);
  } catch {
    if (token !== loadToken) return;
    // Server unreachable (off Tailscale / down). Keep showing cache, flag offline.
    current.reachable = false;
    if (cachedPage || current.manifest) {
      render(current.manifest!);
    } else {
      showMessage(
        "Offline",
        path === MAIN
          ? "Can't reach the dashboard (is Tailscale on?) and there's nothing cached yet."
          : "That page is unavailable offline and isn't cached yet.",
      );
    }
  } finally {
    if (token === loadToken) document.body.classList.remove("refreshing");
  }
}

// Hard refresh: drop every cache (our page store + the service-worker caches),
// unregister the SW, and reload so the app starts completely fresh.
async function hardRefresh(): Promise<void> {
  // Never wipe caches while the server is unreachable — there'd be nothing to reload.
  if (!current.reachable) {
    showMessage("Offline", "Reconnect to Tailscale before hard-refreshing — clearing the cache now would leave nothing to load.");
    return;
  }
  clearCachedPages();
  try {
    if ("caches" in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
    if (navigator.serviceWorker) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    }
  } catch {
    /* best-effort; reload anyway */
  }
  location.reload();
}

async function refresh() {
  if (restricted) {
    renderDecoy();
    return;
  }
  await selectTab(current.path);
}

// Background prefetch: keep the latest of EVERY page in the cache (not just visited
// ones) so the whole app is available offline. Sweeps the nav, fetches each page, and
// stores it in the per-page cache (and, via the fetch, the service-worker page cache).
let prefetching = false;
let lastPrefetchAt = 0;
let prefetchDone = 0;
const PREFETCH_MIN_GAP = 90_000; // throttle foreground-triggered sweeps

function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

// The offline-cache status line shown in the drawer footer: running progress, else the
// number of cached pages + an estimated size and when they were last refreshed.
function prefetchStatus(): string {
  if (prefetching) return `Caching pages… ${prefetchDone}`;
  const { pages, bytes } = cacheStats();
  const pinned = pinnedPaths().length;
  const pinNote = pinned ? ` · ${pinned} pinned` : "";
  const size = `${pages} page${pages === 1 ? "" : "s"} · ~${fmtBytes(bytes)}${pinNote}`;
  if (!lastPrefetchAt) return pages ? `${size} · tap to refresh` : "Not cached yet — tap to cache for offline";
  const mins = Math.floor((Date.now() - lastPrefetchAt) / 60000);
  const ago = mins < 1 ? "just now" : mins < 60 ? `${mins}m ago` : `${Math.floor(mins / 60)}h ago`;
  return `Cached ${ago} · ${size}`;
}

// Live-update the drawer footer's prefetch button + status without a full re-render (a
// re-render would close the open drawer). No-ops when the drawer isn't mounted.
function updatePrefetchUI(): void {
  const btn = document.querySelector(".drawer-prefetch") as HTMLButtonElement | null;
  if (btn) {
    btn.disabled = prefetching;
    btn.classList.toggle("disabled", prefetching);
    const label = btn.querySelector(".drawer-prefetch-label");
    if (label) label.textContent = prefetching ? "Caching…" : "Cache all pages now";
  }
  const status = document.querySelector(".drawer-prefetch-status");
  if (status) status.textContent = prefetchStatus();
}

// A BQ-table preview runs a real BigQuery query, so we don't re-pull those on the light
// 5-min timer (cost) — only on a deep crawl (boot / reconnect / the manual button).
function isExpensive(path: string): boolean {
  return path.startsWith("/screen/bqtables") && path.includes("view=preview");
}

const MAX_CRAWL = 400; // safety cap so the crawl can't run away

// Cache pages for offline. `deep` follows the internal links inside each fetched page —
// crawling the whole navigable graph (every dataset → table → preview, Docs path, log
// detail, …) so ALL sub-pages are cached, not just the ones visited. The shallow mode
// (timer/foreground) just re-warms the base nav pages + already-cached pages, skipping
// the expensive BQ previews. Base/"main" pages are always cached before parameterized
// sub-pages (two-tier queue), so the important pages land first even mid-crawl.
async function prefetchAll(deep = false): Promise<void> {
  if (restricted || prefetching) return;
  if (typeof navigator !== "undefined" && navigator.onLine === false) return;
  prefetching = true;
  prefetchDone = 0;
  updatePrefetchUI();

  const seen = new Set<string>();
  const pins = new Set(pinnedPaths());
  const pinnedQ: string[] = []; // user-pinned pages — drained first, always refreshed
  const mainQ: string[] = []; // base pages (no query) — next
  const paramQ: string[] = []; // parameterized sub-pages — only once the rest are done
  const enqueue = (p: string) => {
    if (!p || p === current.path || seen.has(p)) return;
    if (!/^\/(screen\/|dashboard)/.test(p)) return; // internal pages only
    const pinned = pins.has(p);
    // Pinned pages are user-opted-in: always refresh them, even on the light 5-min timer
    // and even if expensive (a BQ preview). Other expensive pages are skipped off the timer
    // so we don't re-bill BigQuery — they're only cached on a deep crawl.
    if (!deep && !pinned && isExpensive(p)) return;
    const q = pinned ? pinnedQ : isParameterized(p) ? paramQ : mainQ;
    if (!q.includes(p)) q.push(p);
  };
  prefetchTargets(current.manifest?.nav, MAIN, current.path, [...cachedPagePaths(), ...pinnedPaths()]).forEach(enqueue);

  let fails = 0;
  try {
    while ((pinnedQ.length || mainQ.length || paramQ.length) && seen.size < MAX_CRAWL) {
      // Pinned first, then base pages, then parameterized sub-pages.
      const path = (pinnedQ.length ? pinnedQ.shift() : mainQ.length ? mainQ.shift() : paramQ.shift())!;
      if (seen.has(path)) continue;
      seen.add(path);
      try {
        const manifest = await fetchManifestAt(path, pageQuery(path));
        saveCached(manifest, path);
        fails = 0;
        if (deep) collectInternalLinks(manifest.data).forEach(enqueue); // discover sub-pages
      } catch {
        if (++fails >= 3) break; // likely offline — stop hammering
      }
      prefetchDone = seen.size;
      updatePrefetchUI();
    }
    lastPrefetchAt = Date.now();
  } finally {
    prefetching = false;
    updatePrefetchUI();
  }
}

// Throttled entry point. `force` bypasses the throttle; `deep` crawls every sub-page
// (boot / reconnect / manual) vs. the light periodic re-warm.
function maybePrefetch(force = false, deep = false): void {
  if (restricted) return;
  if (!force && Date.now() - lastPrefetchAt < PREFETCH_MIN_GAP) return;
  void prefetchAll(deep);
}

// Toggle "keep this page offline" for the current page. Re-render so the pin icon updates,
// and when newly pinned (and online) kick a sweep so it's cached right away rather than
// waiting for the next timer tick.
function toggleCurrentPin(): void {
  const nowPinned = togglePin(current.path);
  if (current.manifest) render(current.manifest);
  if (nowPinned && current.reachable) maybePrefetch(true, false);
}

// Polling timer for async server-side jobs (e.g. deploy). Cancelled on a new submit.
let _pollTimer: ReturnType<typeof setTimeout> | null = null;
let _pollCount = 0;
function startPolling() {
  if (_pollTimer != null) { clearTimeout(_pollTimer); _pollTimer = null; }
  _pollCount = 0;
  const tick = async () => {
    _pollCount++;
    await refresh();
    if (_pollCount < 24) {  // 24 × 5 s = 2 min max
      _pollTimer = setTimeout(tick, 5000);
    } else {
      _pollTimer = null;
    }
  };
  _pollTimer = setTimeout(tick, 5000);
}

async function submit(url: string, items: FormItem[]): Promise<SubmitResult> {
  if (!url) return { ok: false };
  // Report success/failure; the button shows the spinner → done/failed and refreshes.
  // When the response carries `polling: true` the server started an async job — begin
  // refreshing the page every 5 s so the user sees live status without manual refreshes.
  try {
    const res = await postJSON(url, { items });
    if (res && typeof res === "object") {
      const r = res as Record<string, unknown>;
      const ok = (r.statusDirection as string | undefined) !== "down";
      if (ok && r.polling === true) {
        startPolling();
        return { ok: true, polling: true };
      }
      return { ok };
    }
    return { ok: true };
  } catch {
    return { ok: false };
  }
}

// --- boot: passcode gate first, then the app for the unlocked mode ---
function startApp() {
  // cache-first paint of the dashboard, then revalidate.
  const cached = loadCached(MAIN);
  if (cached) {
    current.manifest = cached.manifest;
    render(cached.manifest);
  } else {
    showMessage("Loading…", "Fetching your dashboard.");
  }
  void selectTab(MAIN);

  // Prime the offline cache shortly after boot (let the dashboard paint first): a DEEP
  // crawl that follows links so every sub-page (incl. BQ previews) is cached. The 5-min
  // timer does a LIGHT re-warm (base + cached pages, skipping the costly BQ previews).
  window.setTimeout(() => maybePrefetch(true, true), 3000);
  window.setInterval(() => maybePrefetch(true, false), 5 * 60_000);

  requestGeolocation(() => {
    current.query = locationQuery();
    if (current.path === MAIN) void refresh();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void refresh();
      maybePrefetch(); // throttled re-sweep when the app is foregrounded
    }
  });

  // Reconnect handling: retry when the device comes back online; flag offline at once when it drops.
  window.addEventListener("online", () => {
    void refresh();
    maybePrefetch(true, true); // back online → deep re-cache everything
  });
  window.addEventListener("offline", () => {
    current.reachable = false;
    if (current.manifest) render(current.manifest);
  });

  // Esc closes the slide-out menu.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") document.querySelector(".app-shell")?.classList.remove("nav-open");
  });

  // Register the SW and actively poll for a new deploy. iOS standalone PWAs are lazy
  // about checking, which is why fresh changes often don't appear — nudging update()
  // on an interval and whenever the app is foregrounded makes new bundles land
  // (autoUpdate then swaps + reloads) without a manual hard refresh.
  registerSW({
    immediate: true,
    onRegisteredSW(_swUrl, r) {
      if (!r) return;
      const check = () => void r.update().catch(() => {});
      window.setInterval(check, 60000);
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") check();
      });
    },
  });
}

// Decoy (all-even passcode): render a single dummy dashboard locally — no network,
// no menu, nothing real.
function renderDecoy() {
  current.manifest = decoyManifest();
  current.path = MAIN;
  render(current.manifest);
  registerSW({ immediate: true });
}

function unlock(mode: AccessMode) {
  restricted = mode === "decoy";
  current.path = MAIN;
  if (restricted) renderDecoy();
  else startApp();
}

// Follow the system light/dark setting live: when it flips (e.g. iOS 'Automatic' at
// sunset), re-apply the active palette. Components resolve colors to CSS variables, so
// updating the variables restyles everything in place — no re-render, drawer stays put.
try {
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (current.manifest) applyTheme(current.manifest.theme);
  });
} catch {
  /* no matchMedia (old engine) — stays on the default palette */
}

renderLockScreen(app, unlock);
