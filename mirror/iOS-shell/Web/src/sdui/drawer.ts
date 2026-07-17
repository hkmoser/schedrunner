import type { NavItem } from "./manifest";
import { navIconSVG } from "./navicons";

// The nav model supports two levels: a top-level item is either a direct link
// (has `path`) or a section header (`children` of leaf links). These helpers and
// the drawer/app-bar below render that tree as a slide-out hamburger menu.

/** All leaf items (those with a path), flattened depth-first. */
export function navLeaves(nav: NavItem[] | undefined): NavItem[] {
  const out: NavItem[] = [];
  for (const it of nav ?? []) {
    if (it.children && it.children.length) out.push(...navLeaves(it.children));
    else if (it.path) out.push(it);
  }
  return out;
}

/** Title of the page at `activePath` (searched across the tree); falls back sensibly. */
export function activeTitle(nav: NavItem[] | undefined, activePath: string): string {
  const hit = navLeaves(nav).find((l) => l.path === activePath);
  return hit?.title ?? "Dashboard";
}

/** Top app bar: a hamburger button + the current page title, and (optionally) a
 *  freshness/offline status with a refresh button — shown identically on every page. */
export interface AppBarStatus {
  label: string;        // e.g. "Updated 3m ago"
  offline: boolean;     // server unreachable (off Tailscale / down)
  onRefresh: () => void;
}

/** "Keep offline" pin for the current page — flags it to be aggressively re-cached so it's
 *  current by the time you go offline. Shown on every page (shell) for consistency. */
export interface AppBarPin {
  pinned: boolean;
  onToggle: () => void;
}

const REFRESH_SVG =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-.5 4"/><path d="M20 4v5h-5"/></svg>';

// Bookmark icon — outline when unpinned, filled when this page is kept offline.
function pinSVG(pinned: boolean): string {
  return `<svg viewBox="0 0 24 24" width="20" height="20" fill="${pinned ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>`;
}

export function buildAppBar(
  title: string,
  onMenu: () => void,
  status?: AppBarStatus,
  pin?: AppBarPin,
  channel?: string,
): HTMLElement {
  const bar = document.createElement("header");
  bar.className = "appbar";
  // Release channel of THIS bundle (baked in at build time; parameter overrides for tests).
  const ch = channel ?? (typeof __APP_CHANNEL__ !== "undefined" ? __APP_CHANNEL__ : "stable");

  const menu = document.createElement("button");
  menu.type = "button";
  menu.className = "appbar-menu";
  menu.setAttribute("aria-label", "Open menu");
  menu.innerHTML =
    '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>';
  menu.addEventListener("click", onMenu);

  const label = document.createElement("div");
  label.className = "appbar-title";
  label.textContent = title;

  bar.append(menu, label);

  // Non-stable channel → an always-visible badge in the shell, so there's never any doubt
  // which app you're in (the two channels are otherwise near-identical inside).
  if (ch !== "stable") {
    const chip = document.createElement("span");
    chip.className = "appbar-channel";
    chip.textContent = ch.toUpperCase();
    bar.appendChild(chip);
  }

  if (status || pin) {
    const spacer = document.createElement("div");
    spacer.className = "appbar-spacer";
    bar.appendChild(spacer);
  }

  // Freshness text lives in each page's header; the app bar keeps just the sticky
  // controls (offline pill + pin + refresh) so "Updated …" isn't shown twice.
  if (status?.offline) {
    const pill = document.createElement("span");
    pill.className = "appbar-offline";
    pill.textContent = "Offline";
    bar.appendChild(pill);
  }

  // "Keep offline" pin for this page (filled when pinned) — re-rendered by the caller so
  // its state stays correct after a tap.
  if (pin) {
    const pinBtn = document.createElement("button");
    pinBtn.type = "button";
    pinBtn.className = "appbar-refresh appbar-pin" + (pin.pinned ? " pinned" : "");
    pinBtn.setAttribute("aria-label", pin.pinned ? "Stop keeping this page offline" : "Keep this page offline");
    pinBtn.setAttribute("aria-pressed", pin.pinned ? "true" : "false");
    pinBtn.title = pin.pinned ? "Kept offline — tap to unpin" : "Keep this page offline";
    pinBtn.innerHTML = pinSVG(pin.pinned);
    pinBtn.addEventListener("click", pin.onToggle);
    bar.appendChild(pinBtn);
  }

  if (status) {
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "appbar-refresh";
    refresh.setAttribute("aria-label", "Refresh");
    refresh.innerHTML = REFRESH_SVG;
    refresh.addEventListener("click", status.onRefresh);
    bar.appendChild(refresh);
  }
  return bar;
}

function leafButton(item: NavItem, activePath: string, onSelect: (path: string) => void): HTMLElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "drawer-item" + (item.path === activePath ? " active" : "");
  const icon = document.createElement("span");
  icon.className = "drawer-icon";
  icon.innerHTML = navIconSVG(item.icon);
  const label = document.createElement("span");
  label.textContent = item.title;
  btn.append(icon, label);
  btn.addEventListener("click", () => onSelect(item.path!));
  return btn;
}

/**
 * Build the slide-out drawer + backdrop scrim for a two-level nav tree.
 * `onSelect` fires with a leaf's path; `onClose` fires when the scrim is tapped.
 * Visibility is driven by a `.nav-open` class on the app shell (see styles.css).
 */
export function buildDrawer(
  nav: NavItem[] | undefined,
  activePath: string,
  onSelect: (path: string) => void,
  onClose: () => void,
  hardRefresh?: { run: () => void; enabled: boolean },
  buildInfo?: string,
  buildDate?: string,
  prefetch?: { run: () => void; statusText: string; running: boolean },
): { drawer: HTMLElement; scrim: HTMLElement } {
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  scrim.addEventListener("click", onClose);

  const drawer = document.createElement("nav");
  drawer.className = "drawer";

  const head = document.createElement("div");
  head.className = "drawer-head";
  head.textContent = "Menu";
  drawer.appendChild(head);

  const list = document.createElement("div");
  list.className = "drawer-list";
  for (const item of nav ?? []) {
    if (item.children && item.children.length) {
      const section = document.createElement("div");
      section.className = "drawer-section";
      const title = document.createElement("div");
      title.className = "drawer-section-title";
      const icon = document.createElement("span");
      icon.className = "drawer-icon";
      icon.innerHTML = navIconSVG(item.icon);
      const label = document.createElement("span");
      label.textContent = item.title;
      title.append(icon, label);
      section.appendChild(title);
      for (const child of item.children) {
        if (child.path) section.appendChild(leafButton(child, activePath, onSelect));
      }
      list.appendChild(section);
    } else if (item.path) {
      list.appendChild(leafButton(item, activePath, onSelect));
    }
  }
  drawer.appendChild(list);

  // Footer: offline-cache status + manual prefetch, hard refresh, and the build stamp.
  if (hardRefresh || prefetch || buildInfo) {
    const footer = document.createElement("div");
    footer.className = "drawer-footer";

    // Offline cache: a "cache all pages now" button + a live status/time line, so it's
    // clear when the prefetch is running vs. when everything was last cached.
    if (prefetch) {
      const pf = document.createElement("button");
      pf.type = "button";
      pf.className = "drawer-item drawer-prefetch" + (prefetch.running ? " disabled" : "");
      pf.disabled = prefetch.running;
      pf.innerHTML =
        '<span class="drawer-icon"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg></span>' +
        `<span class="drawer-prefetch-label">${prefetch.running ? "Caching…" : "Cache all pages now"}</span>`;
      pf.addEventListener("click", prefetch.run);
      footer.appendChild(pf);

      const status = document.createElement("div");
      status.className = "drawer-prefetch-status";
      status.textContent = prefetch.statusText;
      footer.appendChild(status);
    }

    // Hard refresh — clears caches + service worker and reloads fresh. Disabled while
    // offline: clearing the cache with the server unreachable would brick the app.
    if (hardRefresh) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "drawer-item drawer-hard-refresh" + (hardRefresh.enabled ? "" : " disabled");
      const label = hardRefresh.enabled ? "Hard refresh" : "Hard refresh (online only)";
      btn.innerHTML =
        '<span class="drawer-icon"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-.5 4"/><path d="M20 4v5h-5"/></svg></span>' +
        `<span>${label}</span>`;
      if (hardRefresh.enabled) {
        btn.addEventListener("click", hardRefresh.run);
      } else {
        btn.disabled = true;
        btn.title = "Reconnect first — clearing the cache while offline would leave nothing to load.";
      }
      footer.appendChild(btn);
    }

    if (buildInfo) {
      const ver = document.createElement("div");
      ver.className = "drawer-build";
      ver.textContent = buildInfo;
      footer.appendChild(ver);
      // A human date/time under the version, so it's obvious how old this build is.
      if (buildDate) {
        const when = document.createElement("div");
        when.className = "drawer-build-date";
        when.textContent = buildDate;
        footer.appendChild(when);
      }
    }
    drawer.appendChild(footer);
  }

  return { drawer, scrim };
}
