// Regression suite — locks in the cross-cutting capabilities that have repeatedly
// regressed across updates: navigation (consistency + clickability), layout/spacing
// invariants (shell fills the viewport, no dead space, drawer scrolls), the passcode
// gate, freshness/offline "refresh times", hard refresh, the version stamp, and that
// EVERY page template still renders. Run with: npm run test:regression
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const pub = resolve(here, "..", "public");
const src = resolve(here, "..", "src");

const dom = new JSDOM("<!doctype html><html><body><div id='app'></div></body></html>");
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { renderScreen } = await import("../src/sdui/renderer.ts");
const { buildDrawer, buildAppBar, navLeaves, activeTitle } = await import("../src/sdui/drawer.ts");
const { classifyPasscode } = await import("../src/sdui/lock.ts");
const { freshnessLabel, statusText } = await import("../src/data/cache.ts");
const { prefetchTargets, collectInternalLinks, isParameterized } = await import("../src/data/prefetch.ts");
const { renderMarkdown } = await import("../src/sdui/markdown.ts");
const { decoyManifest } = await import("../src/sdui/decoy.ts");

const golden = JSON.parse(readFileSync(resolve(here, "..", "..", "Shared", "schema", "golden-manifest.json"), "utf8"));
const styles = readFileSync(resolve(src, "styles.css"), "utf8");
const click = (el) => el.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
let n = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); n++; };
const eq = (a, b, msg) => { assert.equal(a, b, msg); n++; };

// Return the body of a top-level CSS rule `selector { ... }` (first match).
function cssRule(selector) {
  const re = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{([^}]*)\\}");
  const m = styles.match(re);
  return m ? m[1] : null;
}

// ─────────────────────────────────────────── Passcode gate
eq(classifyPasscode("1937"), "full", "1937 → full experience");
for (const c of ["2468", "0000", "2002", "8642"]) eq(classifyPasscode(c), "decoy", `all-even ${c} → decoy`);
for (const c of ["1234", "1111", "2461", "0001"]) eq(classifyPasscode(c), null, `mixed/odd ${c} → rejected`);
for (const c of ["", "12", "123", "12345", "12a4", "abcd"]) eq(classifyPasscode(c), null, `malformed "${c}" → rejected`);

// ─────────────────────────────────────────── Refresh times / freshness + offline
eq(freshnessLabel(Date.now()), "Updated just now", "fresh → just now");
ok(/^Updated 5m ago$/.test(freshnessLabel(Date.now() - 5 * 60_000)), "5m old");
ok(/^Updated 3h ago$/.test(freshnessLabel(Date.now() - 3 * 3600_000)), "3h old");
ok(/^Updated 2d ago$/.test(freshnessLabel(Date.now() - 2 * 86400_000)), "2d old");
eq(statusText(Date.now(), true), "Updated just now", "online status reads freshness");
ok(statusText(Date.now() - 5 * 60_000, false).startsWith("Offline · "), "offline status prefixes Offline");
ok(statusText(Date.now() - 5 * 60_000, false).includes("5m ago"), "offline status keeps the age");
eq(statusText(undefined, true), "Updating…", "no cache yet → Updating…");

// ─────────────────────────────────────────── Navigation: every leaf reachable + has a page sample that renders
const leaves = navLeaves(golden.nav);
ok(leaves.length >= 10, `nav exposes the full set of leaves (${leaves.length})`);
const sampleFor = (p) => (p === "/__APP_NAME_LOWER__" ? "__APP_NAME_LOWER__" : p.replace(/^\/screen\//, "").split("?")[0]) + "-sample.json";
for (const leaf of leaves) {
  ok(typeof leaf.path === "string" && leaf.path.startsWith("/"), `leaf "${leaf.title}" has a path`);
  ok(leaf.icon, `leaf "${leaf.title}" has an icon`);
  const file = resolve(pub, sampleFor(leaf.path));
  let sample;
  try { sample = JSON.parse(readFileSync(file, "utf8")); }
  catch { assert.fail(`missing page sample for ${leaf.path} (${sampleFor(leaf.path)})`); }
  const el = renderScreen(sample.screen, { data: sample.data, theme: sample.theme }, stubActions());
  ok((el.textContent ?? "").trim().length > 10, `page ${leaf.path} renders non-empty`);
  n++;
}

// Active title resolves from the (possibly nested) tree.
eq(activeTitle(golden.nav, leaves[0].path), leaves[0].title, "active title resolves a leaf");

// ─────────────────────────────────────────── Drawer: every leaf is a clickable button
let picked = null;
const { drawer, scrim } = buildDrawer(golden.nav, leaves[0].path, (p) => { picked = p; }, () => {});
const items = [...drawer.querySelectorAll(".drawer-item")].filter((b) => !b.classList.contains("drawer-hard-refresh") && !b.classList.contains("drawer-prefetch"));
eq(items.length, leaves.length, "every nav leaf is a drawer item (none dropped)");
for (const leaf of leaves) {
  const btn = items.find((b) => (b.textContent ?? "").includes(leaf.title));
  ok(btn, `drawer has an item for "${leaf.title}"`);
  picked = null;
  click(btn);
  eq(picked, leaf.path, `tapping "${leaf.title}" navigates to ${leaf.path} (clickable)`);
}
ok(drawer.querySelectorAll(".drawer-section-title").length >= 2, "section headers render");

// ─────────────────────────────────────────── Offline prefetch covers every page
// Routine background prefetch must target the __APP_NAME_LOWER__ + every nav leaf (so the latest
// of everything is cached for offline), minus the page already on screen.
const everyPage = new Set(["/__APP_NAME_LOWER__", ...leaves.map((l) => l.path)]);
const targets = prefetchTargets(golden.nav, "/__APP_NAME_LOWER__", "/screen/afm");
ok(targets.includes("/__APP_NAME_LOWER__"), "prefetch includes the __APP_NAME_LOWER__");
ok(!targets.includes("/screen/afm"), "prefetch skips the page currently on screen");
ok(new Set(targets).size === targets.length, "prefetch targets are de-duplicated");
eq(targets.length, everyPage.size - 1, "prefetch covers every page except the current one");
for (const leaf of leaves) {
  if (leaf.path !== "/screen/afm") ok(targets.includes(leaf.path), `prefetch includes ${leaf.title}`);
}
// A query string on the current path is ignored when excluding it.
ok(!prefetchTargets(golden.nav, "/__APP_NAME_LOWER__", "/screen/smarthome?types=light").includes("/screen/smarthome"),
   "prefetch matches the current page ignoring its query string");
// Already-cached parameterized sub-pages (e.g. a specific BQ-table preview, a Docs path)
// are also swept, so anything you've opened stays warm offline — not just base nav pages.
const cached = ["/screen/bqtables?dataset=home_afm&table=afm_now&view=preview", "/screen/docs?path=Home/Notes.md"];
const withExtra = prefetchTargets(golden.nav, "/__APP_NAME_LOWER__", "/screen/logs", cached);
ok(withExtra.includes(cached[0]), "prefetch sweeps a cached BQ-table preview sub-page");
ok(withExtra.includes(cached[1]), "prefetch sweeps a cached Docs sub-page");
ok(withExtra.includes("/screen/bqtables"), "the base BQ-tables page is still swept alongside its sub-page");
eq(new Set(withExtra).size, withExtra.length, "sub-page sweep stays de-duplicated");
// Base/"main" pages are prioritized: every non-parameterized page comes before any
// parameterized sub-page so the important pages cache first.
const firstParam = withExtra.findIndex(isParameterized);
const lastMain = withExtra.map(isParameterized).lastIndexOf(false);
ok(firstParam === -1 || lastMain < firstParam, "main pages are ordered before parameterized sub-pages");
ok(withExtra.filter(isParameterized).length >= 2, "the test set actually includes parameterized sub-pages");
// The deep crawl discovers sub-pages by following internal links inside a fetched page,
// so EVERY BQ-table preview (etc.) gets cached, not just visited ones.
const links = collectInternalLinks({
  bqtables: {
    tables: [{ navHref: "/screen/bqtables?dataset=home_afm" }],
    tabs: [{ navHref: "/screen/bqtables?dataset=home_afm&table=afm_now&view=preview" }],
  },
  ev: { mapsUrl: "https://maps.apple.com/?ll=1,2" },
  dup: { a: "/screen/bqtables?dataset=home_afm" },
});
ok(links.includes("/screen/bqtables?dataset=home_afm"), "crawl follows a dataset drill-down link");
ok(links.includes("/screen/bqtables?dataset=home_afm&table=afm_now&view=preview"), "crawl follows a table-preview link");
ok(!links.some((l) => l.includes("maps.apple.com")), "crawl ignores external (non-internal) links");
eq(new Set(links).size, links.length, "discovered links are de-duplicated");

// ─────────────────────────────────────────── App bar: title + freshness/offline + refresh, on every page
let refreshed = 0;
const bar = buildAppBar("Home", () => {}, { label: "Updated 3m ago", offline: false, onRefresh: () => { refreshed++; } });
ok(bar.querySelector(".appbar-menu"), "app bar has a menu button");
ok(!bar.querySelector(".appbar-status"), "app bar does NOT duplicate the freshness text (lives in the page header)");
ok(!bar.querySelector(".appbar-offline"), "no offline pill when reachable");
click(bar.querySelector(".appbar-refresh"));
eq(refreshed, 1, "app-bar refresh fires");
ok(buildAppBar("Home", () => {}, { label: "Offline · 3m ago", offline: true, onRefresh() {} }).querySelector(".appbar-offline"), "offline pill (sticky) when unreachable");

// ─────────────────────────────────────────── Release-channel badge (the "next" app)
ok(!bar.querySelector(".appbar-channel"), "stable build shows no channel badge");
const nextBar = buildAppBar("Home", () => {}, { label: "x", offline: false, onRefresh() {} }, undefined, "next");
eq(nextBar.querySelector(".appbar-channel")?.textContent, "NEXT", "next build shows a NEXT badge in the shell");

// ─────────────────────────────────────────── "Keep offline" pin (shell control, every page)
let pinToggled = 0;
const pinnedBar = buildAppBar("Activity", () => {}, { label: "Updated 1m ago", offline: false, onRefresh() {} }, { pinned: true, onToggle: () => { pinToggled++; } });
const pinBtn = pinnedBar.querySelector(".appbar-pin");
ok(pinBtn, "app bar shows the keep-offline pin control");
eq(pinBtn.getAttribute("aria-pressed"), "true", "pin reflects the pinned state");
click(pinBtn);
eq(pinToggled, 1, "tapping the pin fires its toggle");
ok(!buildAppBar("Home", () => {}, { label: "x", offline: false, onRefresh() {} }, { pinned: false, onToggle() {} }).querySelector(".appbar-pin.pinned"),
   "unpinned page renders the pin without the pinned class");

// ─────────────────────────────────────────── Hard refresh: works online, disabled offline
let hard = 0;
const on = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run: () => { hard++; }, enabled: true }).drawer;
const onBtn = [...on.querySelectorAll(".drawer-item")].find((b) => (b.textContent ?? "").includes("Hard refresh"));
ok(onBtn && !onBtn.disabled, "hard refresh enabled online");
click(onBtn); eq(hard, 1, "hard refresh fires online");
const off = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run: () => { hard++; }, enabled: false }).drawer;
const offBtn = [...off.querySelectorAll(".drawer-item")].find((b) => (b.textContent ?? "").includes("Hard refresh"));
ok(offBtn && offBtn.disabled && offBtn.classList.contains("disabled"), "hard refresh disabled offline (can't brick the app)");
click(offBtn); eq(hard, 1, "disabled hard refresh is a no-op");

// Version stamp surfaced when provided, with a human build date/time under it.
const ver = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run() {}, enabled: true }, "app abc · server abc", "Built Jun 20, 2026 · 3:45 PM").drawer;
ok((ver.querySelector(".drawer-build")?.textContent ?? "").includes("app abc"), "drawer shows the build/version stamp");
ok((ver.querySelector(".drawer-build-date")?.textContent ?? "").includes("Built Jun 20, 2026"), "drawer shows a version date/time under the build stamp");

// Offline-cache (prefetch) indicator + manual control in the drawer footer.
let prefetchRan = 0;
const pfDrawer = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run() {}, enabled: true }, "app abc", "Built …",
  { run: () => { prefetchRan++; }, statusText: "All pages cached · 2m ago", running: false }).drawer;
const pfBtn = pfDrawer.querySelector(".drawer-prefetch");
ok(pfBtn && !pfBtn.disabled, "a 'cache all pages' button renders (enabled when idle)");
ok((pfDrawer.querySelector(".drawer-prefetch-status")?.textContent ?? "").includes("All pages cached · 2m ago"),
   "the prefetch status shows when everything was last cached (with time)");
click(pfBtn); eq(prefetchRan, 1, "tapping it runs the prefetch manually");
// While running, the button shows a 'Caching…' label and is disabled.
const pfRunning = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run() {}, enabled: true }, "app abc", "Built …",
  { run() {}, statusText: "Caching all pages… 3/12", running: true }).drawer;
const runningBtn = pfRunning.querySelector(".drawer-prefetch");
ok(runningBtn && runningBtn.disabled && (runningBtn.textContent ?? "").includes("Caching…"), "running state shows 'Caching…' and disables the button");
ok((pfRunning.querySelector(".drawer-prefetch-status")?.textContent ?? "").includes("3/12"), "running status shows progress");

// ─────────────────────────────────────────── Layout & spacing invariants (regression-prone)
const shell = cssRule(".app-shell");
ok(shell, ".app-shell rule exists");
ok(/position:\s*fixed/.test(shell) && /top:\s*0/.test(shell) && /right:\s*0/.test(shell), ".app-shell pins to the top edges of the viewport");
ok(/height:\s*var\(--app-height/.test(shell), ".app-shell height is the JS visual-viewport var (fills iOS standalone exactly — no dead band, lowest nav item is tappable)");
ok(!/100vh|100dvh/.test(shell), ".app-shell avoids 100vh/100dvh (caused PWA dead space)");
const scroll = cssRule(".scroll");
ok(/flex:\s*1/.test(scroll) && /overflow-y:\s*auto/.test(scroll), ".scroll flexes to fill and scrolls");
const drawerCss = cssRule(".drawer");
ok(/overflow-y:\s*auto/.test(drawerCss), ".drawer scrolls as a whole (all items reachable)");
ok(/touch-action:\s*pan-y/.test(drawerCss) && /overscroll-behavior:\s*contain/.test(drawerCss),
   ".drawer can scroll on iOS despite the body scroll-lock (lower nav items reachable)");
ok(/height:\s*var\(--app-height/.test(drawerCss), ".drawer matches the visual-viewport height (Config sits in the interactive area, not the dead band)");
ok(!/^\s*\.tabbar\s*\{/m.test(styles), "old bottom tab bar (.tabbar) is gone — single nav model");

// ─────────────────────────────────────────── Decoy mode (all-even passcode): local, no nav
const decoy = decoyManifest();
ok(decoy.nav === undefined, "decoy carries no nav (locked-down single page)");
ok((renderScreen(decoy.screen, { data: decoy.data, theme: decoy.theme }, stubActions()).textContent ?? "").includes("__APP_NAME__"), "decoy renders a dummy __APP_NAME_LOWER__");

// ─────────────────────────────────────────── Components used across pages still render
ok(renderMarkdown("# H\n\n- a\n\n`x` **b**").includes("<h1>H</h1>"), "markdown renders headings");
ok(!renderMarkdown("<script>alert(1)</script>").includes("<script>"), "markdown escapes raw HTML");

// ─────────────────────────────────────────── Tappable chips (filters/tabs) + table null-guard
// A badge carrying an action is a clickable chip; a table with no bound object renders nothing.
let chipNav = null;
const chipScreen = {
  type: "screen",
  children: [
    { type: "hstack", props: { repeat: "f.types" }, style: { wrap: true }, children: [
      { type: "badge", binding: "item.labelFormatted", style: { color: "item.color" }, action: { type: "navigate", urlBinding: "item.navHref" } },
    ] },
    { type: "table", binding: "f.absent" },
  ],
};
const chipData = { f: { types: [
  { labelFormatted: "Light · 3", color: "$accent", navHref: "/screen/smarthome?types=lock" },
  { labelFormatted: "Sensor · 9", color: "$textSecondary", navHref: "/screen/smarthome?types=light,sensor" },
] } };
const chipEl = renderScreen(chipScreen, { data: chipData, theme: golden.theme }, { ...stubActions(), navigate: (t) => { chipNav = t; } });
const chips = [...chipEl.querySelectorAll(".badge")];
eq(chips.length, 2, "one chip per type renders");
ok(/wrap/.test(chipEl.querySelector(".stack-row")?.style.flexWrap ?? ""), "chip row wraps (style.wrap)");
click(chips[0]); eq(chipNav, "/screen/smarthome?types=lock", "tapping a chip navigates via its navHref");
ok(!chipEl.querySelector("table.table"), "a table bound to an absent object renders nothing (no empty grid)");

// ─────────────────────────────────────────── Every sample page renders (all established capabilities)
const samples = readdirSync(pub).filter((f) => f.endsWith("-sample.json"));
ok(samples.length >= 12, `found ${samples.length} page samples to guard`);
for (const f of samples) {
  const m = JSON.parse(readFileSync(resolve(pub, f), "utf8"));
  if (!m.screen) continue;
  const el = renderScreen(m.screen, { data: m.data ?? {}, theme: m.theme ?? {} }, stubActions());
  ok((el.textContent ?? "").trim().length > 5, `${f} renders without error`);
}

function stubActions() {
  return { refresh() {}, setPref() {}, navigate() {}, submit() {}, focus() {} };
}

console.log(`✓ regression suite passed (${n} assertions: nav, layout/spacing, passcode, refresh/offline, hard-refresh, version, all pages)`);
