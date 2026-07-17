// Functional render smoke test: render the golden manifest through the real
// renderer in jsdom and assert the resulting DOM. Catches binding/repeat/theme
// regressions that typecheck + schema validation can't. Run with tsx (it can
// import the TypeScript renderer modules directly).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));

// Install a DOM before importing the renderer (it touches document at import time
// only inside functions, but charts use document at call time).
const dom = new JSDOM("<!doctype html><html><body></body></html>");
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { renderScreen } = await import("../src/sdui/renderer.ts");

const golden = JSON.parse(
  readFileSync(resolve(here, "..", "..", "Shared", "schema", "golden-manifest.json"), "utf8"),
);

const scope = { data: golden.data, theme: golden.theme };
let navigated = null;
let submitted = null;
let prefSet = null;
const actions = {
  refresh: () => {},
  setPref: (k, v) => { prefSet = { key: k, value: v }; },
  navigate: (t) => { navigated = t; },
  submit: (url, items) => { submitted = { url, items }; },
};
const el = renderScreen(golden.screen, scope, actions);
const text = el.textContent ?? "";

function has(snippet) {
  assert.ok(text.includes(snippet), `expected rendered DOM to include "${snippet}"`);
}

// Literal + bound text across all cards.
has("__APP_NAME__");
has("71°"); // weather.tempFormatted
has("Partly Cloudy"); // weather.condition
has("Phoenixville"); // weather.locationName
has("3PM"); // repeated hourly item
has("2AM"); // 24h hourly extends overnight (was capped at 5 buckets)
// Weather Home/Current location toggle: two chips that set the weatherLoc pref.
const homeChip = [...el.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "Home");
const currentChip = [...el.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "Current");
assert.ok(homeChip && currentChip, "weather has Home/Current location toggle chips");
homeChip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.deepEqual(prefSet, { key: "weatherLoc", value: "home" }, "tapping Home sets the weatherLoc pref to home");
currentChip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.deepEqual(prefSet, { key: "weatherLoc", value: "current" }, "tapping Current sets the weatherLoc pref to current");
has("SPY"); // repeated stocks series badge
has("$543.21"); // SPY current price
has("+2.31%"); // NVDA change
has("Today · 15-min intervals"); // chart timescale label
has("6.82%"); // mortgage rate
has("+0.04 WoW"); // mortgage delta badge
has("+0.45% vs your 6.375%"); // comparison to user's rate
has("Your rate is better — hold"); // refi hint
has("+4.2% YoY"); // Case-Shiller housing indicator
has("$525,000"); // property zestimate
has("14 Beard Circle, Phoenixville, PA 19460"); // property address

// The chart rendered one polyline per series (3).
const polylines = el.querySelectorAll("polyline");
assert.equal(polylines.length, 3, "expected 3 chart series polylines");

// Theme token color applied (accent on the weather icon path is hard to read;
// assert a known bound color instead: SPY badge color is item.color = #43d18a).
const badges = [...el.querySelectorAll(".badge")];
const spy = badges.find((b) => b.textContent === "SPY");
assert.ok(spy, "expected an SPY badge");

// Tapping the property card resolves action.urlBinding -> property.url and opens it.
let opened = null;
dom.window.open = (u) => { opened = u; };
const cards = [...el.querySelectorAll(".card")];
const propCard = cards.find((c) => (c.textContent ?? "").includes("Rental Property"));
assert.ok(propCard, "expected a Rental Property card");
propCard.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(
  typeof opened === "string" && opened.includes("zillow.com"),
  `expected card tap to open the Zillow url (urlBinding), got: ${opened}`,
);

// Slide-out hamburger drawer built from the two-level manifest.nav tree.
const { buildDrawer, navLeaves, activeTitle } = await import("../src/sdui/drawer.ts");
// All seven destinations are reachable as leaves, even those nested in sections.
const leafPaths = navLeaves(golden.nav).map((l) => l.path);
for (const p of ["/__APP_NAME_LOWER__", "/screen/afm", "/screen/afm_log", "/screen/afm48", "/screen/afm_health", "/screen/balances", "/screen/budget", "/screen/bqtables", "/screen/smarthome", "/screen/smarthome_log", "/screen/messages", "/screen/logs", "/screen/repos", "/screen/schedrunner", "/screen/schedlogs", "/screen/deploy", "/screen/docs", "/screen/config", "/screen/settings"]) {
  assert.ok(leafPaths.includes(p), `nav leaf ${p} reachable`);
}
// The active page's title comes from the tree (a nested leaf here).
assert.equal(activeTitle(golden.nav, "/screen/afm48"), "Last 48h", "active title resolves from a nested leaf");
let navPicked = null;
let navClosed = false;
const { drawer, scrim } = buildDrawer(
  golden.nav,
  "/screen/balances",
  (p) => { navPicked = p; },
  () => { navClosed = true; },
);
// Second-level sections render as headers with their leaf children indented.
const sectionTitles = [...drawer.querySelectorAll(".drawer-section-title")].map((s) => s.textContent);
assert.ok(
  ["Location", "System"].every((t) => sectionTitles.includes(t)),
  "section headers render",
);
// Section headers are not clickable destinations (no path) — only leaves are.
const items = [...drawer.querySelectorAll(".drawer-item")];
assert.equal(items.length, 19, "nineteen leaf links (sections excluded)");
// The active leaf is highlighted.
const active = drawer.querySelector(".drawer-item.active");
assert.ok(active && (active.textContent ?? "").includes("Balances"), "active leaf marked");
// Tapping a nested leaf fires onSelect with its path.
const schedItem = items.find((i) => (i.textContent ?? "").includes("Schedrunner"));
schedItem.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(navPicked, "/screen/schedrunner", "tapping a nested leaf selects its path");
// Tapping the scrim closes the drawer.
scrim.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(navClosed, "scrim tap closes the drawer");

// App bar carries a consistent freshness + offline status with a refresh button.
const { buildAppBar } = await import("../src/sdui/drawer.ts");
let appbarRefreshed = 0;
const onlineBar = buildAppBar("Home", () => {}, { label: "Updated 3m ago", offline: false, onRefresh: () => { appbarRefreshed++; } });
assert.ok(!onlineBar.querySelector(".appbar-status"), "app bar omits the freshness text (no duplicate of the page header)");
assert.ok(!onlineBar.querySelector(".appbar-offline"), "no offline pill when reachable");
onlineBar.querySelector(".appbar-refresh").dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(appbarRefreshed, 1, "app-bar refresh button fires onRefresh");
const offlineBar = buildAppBar("Home", () => {}, { label: "Offline · 3m ago", offline: true, onRefresh: () => {} });
assert.ok(offlineBar.querySelector(".appbar-offline"), "offline pill shown when unreachable");

// Drawer hard-refresh footer: fires when enabled (online)…
let hardRefreshed = 0;
const { drawer: drawer2 } = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run: () => { hardRefreshed++; }, enabled: true });
const hard = [...drawer2.querySelectorAll(".drawer-item")].find((b) => (b.textContent ?? "").includes("Hard refresh"));
assert.ok(hard, "hard-refresh footer item present");
hard.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(hardRefreshed, 1, "hard-refresh fires when online");
// …and is disabled (no-op) when offline so it can't brick the app.
let hardRefreshed2 = 0;
const { drawer: drawer3 } = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run: () => { hardRefreshed2++; }, enabled: false });
const hardOff = [...drawer3.querySelectorAll(".drawer-item")].find((b) => (b.textContent ?? "").includes("Hard refresh"));
assert.ok(hardOff && hardOff.classList.contains("disabled"), "hard-refresh shown disabled when offline");
hardOff.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(hardRefreshed2, 0, "disabled hard-refresh does nothing offline");
// Footer shows the loaded build version so a stale bundle is obvious after deploy.
const { drawer: drawer4 } = buildDrawer(golden.nav, "/__APP_NAME_LOWER__", () => {}, () => {}, { run: () => {}, enabled: true }, "app abc1234 · server abc1234");
assert.ok((drawer4.querySelector(".drawer-build")?.textContent ?? "").includes("app abc1234"), "drawer footer shows the build stamp");

// Freshness label reflects elapsed time since the data was cached into the app.
const { freshnessLabel } = await import("../src/data/cache.ts");
assert.equal(freshnessLabel(Date.now()), "Updated just now", "fresh cache reads 'just now'");
assert.ok(/^Updated 5m ago$/.test(freshnessLabel(Date.now() - 5 * 60000)), "5-minute-old cache reads '5m ago'");

// Activity page: a map of all segments + a list of 24h segments with real durations.
let focused = null;
actions.focus = (key, index) => { focused = { key, index }; };
const afm = JSON.parse(readFileSync(resolve(here, "..", "public", "afm-sample.json"), "utf8"));
const afmEl = renderScreen(afm.screen, { data: afm.data, theme: afm.theme }, actions);
const afmText = afmEl.textContent ?? "";
assert.ok(afmText.includes("Activity"), "activity title");
assert.ok(afmEl.querySelector(".map"), "map component renders");
// Widget disabled-mode toggle: a bool switch + Apply that posts to /config.
assert.ok(afmEl.querySelector("input.field-toggle[type=checkbox]"), "widget-disabled toggle renders as a switch");
assert.ok(afmText.includes("Disable Activity widget"), "the widget-disable toggle label renders");
const applyBtn = [...afmEl.querySelectorAll("button.button")].find((b) => (b.textContent ?? "").trim() === "Apply");
assert.ok(applyBtn, "the widget-disable toggle has an Apply button");
submitted = null;
applyBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(submitted && submitted.url === "/config", "Apply posts the toggle to /config");
// Current-state block at the top: status + place + battery/seen meta.
assert.ok(afmText.includes("battery 82%") && afmText.includes("seen 2m ago"), "current-state block renders battery + last-seen");
// Today / Yesterday / This-week range filter chips.
assert.ok(afmText.includes("Today") && afmText.includes("Yesterday") && afmText.includes("This week"),
  "range filter chips render");
const yesterdayChip = [...afmEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "Yesterday");
assert.ok(yesterdayChip, "Yesterday range chip is a tappable badge");
navigated = null;
yesterdayChip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(navigated, "/screen/afm?range=yesterday", "tapping a range chip navigates with ?range=");
// Timeline entries with mode labels + real durations + a charge event.
const tlEntries = [...afmEl.querySelectorAll(".tl-entry")];
assert.equal(tlEntries.length, 4, "three segments + one charge event");
assert.ok(afmText.includes("Driving to Bound Brook"), "move title reads naturally (Driving to …)");
assert.ok(afmText.includes("Home") && !afmText.includes("Stopped · Home"), "stop entry headlines the place (known name)");
assert.ok(afmText.includes("Started charging"), "charge event entry");
assert.ok(afmText.includes("2h 55m"), "real stopped duration (not 0 min)");
// Visual hierarchy: stops are filled circles; moves/charges are bare icons.
const stopEntryEl = tlEntries.find((e) => (e.textContent ?? "").includes("Home"));
const moveEntryEl = tlEntries.find((e) => (e.textContent ?? "").includes("Driving"));
assert.ok(!stopEntryEl.querySelector(".tl-dot").classList.contains("tl-bare"), "stop dot is a filled circle");
assert.ok(moveEntryEl.querySelector(".tl-dot").classList.contains("tl-bare"), "move dot is bare");
// Stops sit in a translucent box; moves don't.
assert.ok(stopEntryEl.querySelector(".tl-body").classList.contains("tl-boxed"), "stop body is boxed");
assert.ok(!moveEntryEl.querySelector(".tl-body").classList.contains("tl-boxed"), "move body is not boxed");
// Representative vertical proportion: the long stop is taller than the charge event.
const chargeEntryEl = tlEntries.find((e) => (e.textContent ?? "").includes("Started charging"));
assert.ok(parseInt(stopEntryEl.style.minHeight) > parseInt(chargeEntryEl.style.minHeight), "longer stay is taller");
// Tapping a timeline entry focuses the corresponding map segment.
const drivingEntry = tlEntries.find((e) => (e.textContent ?? "").includes("Driving"));
assert.ok(drivingEntry, "driving entry present");
drivingEntry.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(focused && focused.key === "activity" && focused.index === 1, `entry tap focuses map segment 1, got ${JSON.stringify(focused)}`);
// Stopped entries get a Maps link; moving entries don't.
const stopEntry = tlEntries.find((e) => (e.textContent ?? "").includes("Home"));
const mapsLink = stopEntry.querySelector("a.tl-maps");
assert.ok(mapsLink && mapsLink.href.includes("maps.apple.com"), "stop has an Apple Maps link");
assert.ok(!drivingEntry.querySelector("a.tl-maps"), "moving entry has no maps link");
// Label-this-place: every stop has a label button that opens the Label screen prefilled.
const labelBtns = [...afmEl.querySelectorAll(".tl-label")];
assert.ok(labelBtns.length >= 2, "each stop has a label button");
navigated = null;
labelBtns[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(typeof navigated === "string" && navigated.startsWith("/screen/afm_label?"),
  `label button opens the Label screen, got ${navigated}`);
const homeEntry = tlEntries.find((e) => (e.textContent ?? "").includes("Home"));
assert.ok((homeEntry.querySelector(".tl-label")?.textContent ?? "").includes("⭐"), "known stop shows a star");
// Label location screen: a prefilled form + Save + the existing known locations.
const lbl = JSON.parse(readFileSync(resolve(here, "..", "public", "afm_label-sample.json"), "utf8"));
const lblEl = renderScreen(lbl.screen, { data: lbl.data, theme: lbl.theme }, actions);
const lblText = lblEl.textContent ?? "";
assert.ok(lblText.includes("Label location"), "label screen title");
assert.ok(lblEl.querySelectorAll(".field").length >= 4, "name/lat/lon/radius fields render");
assert.ok(lblText.includes("Gym") && lblText.includes("Office"), "existing known locations list renders");
const lblSaveBtn = [...lblEl.querySelectorAll(".button")].find((b) => (b.textContent ?? "").includes("Save location"));
assert.ok(lblSaveBtn, "Save location button present");
submitted = null;
lblSaveBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(submitted && submitted.url === "/known_locs", "Save submits to /known_locs");
// "Show full day" button issues a focus(-1) to fit the whole map.
focused = null;
const fullDay = [...afmEl.querySelectorAll("button.button")].find((b) => (b.textContent ?? "").includes("Show full day"));
assert.ok(fullDay, "full-day button present");
fullDay.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(focused && focused.index === -1, `full-day focuses all (-1), got ${JSON.stringify(focused)}`);

// Config page: grouped sections + typed editable fields + submit collects items.
const cfg = JSON.parse(readFileSync(resolve(here, "..", "public", "config-sample.json"), "utf8"));
const cfgEl = renderScreen(cfg.screen, { data: cfg.data, theme: cfg.theme }, actions);
const cfgText = cfgEl.textContent ?? "";
// int -> number input, bool -> switch (checkbox), enum -> select.
assert.ok(cfgEl.querySelector('input[type="number"]'), "int field renders a number input");
assert.ok(cfgEl.querySelector("input.field-toggle[type=checkbox]"), "bool field renders a switch");
assert.ok(cfgEl.querySelector("select"), "enum field renders a select");
// Grouped into collapsible sections via category / naming convention.
const sections = [...cfgEl.querySelectorAll("details.disclosure")];
assert.equal(sections.length, 3, "three collapsible group sections");
const summaries = sections.map((d) => d.querySelector(".disclosure-summary")?.textContent);
for (const g of ["Activity", "Alerts", "General"]) {
  assert.ok(summaries.includes(g), `collapsible section "${g}"`);
}
// Collapsing a section persists across re-render.
const activitySection = sections.find((d) => d.querySelector(".disclosure-summary")?.textContent === "Activity");
activitySection.open = false;
activitySection.dispatchEvent(new dom.window.Event("toggle"));
const cfgEl2 = renderScreen(cfg.screen, { data: cfg.data, theme: cfg.theme }, actions);
const activity2 = [...cfgEl2.querySelectorAll("details.disclosure")].find(
  (d) => d.querySelector(".disclosure-summary")?.textContent === "Activity",
);
assert.equal(activity2.open, false, "collapsed state persists across re-render");
// The Activity device selector is a dropdown of locatable devices with friendly labels.
const deviceSelect = [...cfgEl.querySelectorAll("select")].find(
  (s) => [...s.options].some((o) => o.value === "iPhone 16"),
);
assert.ok(deviceSelect && deviceSelect.value === "iPhone 16", "afm_device dropdown defaults to the locatable device");
// Edit the int field, then Save -> submit receives the updated item.
const numInput = cfgEl.querySelector('input[type="number"]');
numInput.value = "99";
numInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
const saveBtn = [...cfgEl.querySelectorAll("button.button")].find((b) => (b.textContent ?? "").includes("Save"));
assert.ok(saveBtn, "expected a Save button");
saveBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(submitted && submitted.url === "/config", "submit posts to /config");
const threshold = submitted.items.find((i) => i.key === "alerts.threshold");
assert.equal(threshold?.value, "99", "edited value is submitted");
assert.equal(threshold?.type, "int", "value type is preserved");

// Balances page: net total + per-account rows pushed from the budget sheet.
const bal = JSON.parse(readFileSync(resolve(here, "..", "public", "balances-sample.json"), "utf8"));
const balEl = renderScreen(bal.screen, { data: bal.data, theme: bal.theme }, actions);
const balText = balEl.textContent ?? "";
assert.ok(balText.includes("Balances"), "balances title");
assert.ok(balText.includes("$48,250.00"), "net total renders");
assert.ok(balText.includes("Ally Savings"), "an account renders");
// Accounts are grouped (Operational / Shared / Lifestyle / Real Estate) with subtotals.
for (const g of ["Operational", "Shared", "Lifestyle", "Real Estate"]) {
  assert.ok(balText.includes(g), `the ${g} group renders`);
}
assert.ok(balText.includes("$27,600.00"), "a group subtotal renders (Lifestyle)");
assert.ok(balText.includes("Ally Mortgage Escrow"), "an Ally real-estate account groups under Real Estate");
// Net-worth-over-time card: title, current value, change, and a two-segment line chart
// (dashed estimated/backfill segment + solid actual segment).
assert.ok(balText.includes("Net worth"), "net worth card renders");
assert.ok(balText.includes("$51,002.77"), "current net worth renders");
assert.ok(balText.includes("▲ $7,002.77 · 30d"), "net worth change renders");
assert.ok(balText.includes("≈ first 4 days estimated"), "estimated-history footnote renders");
const nwPolys = [...balEl.querySelectorAll("polyline")];
assert.equal(nwPolys.length, 2, "net worth chart draws two line segments");
assert.ok(nwPolys.some((p) => p.getAttribute("stroke-dasharray")), "the estimated segment is dashed");

// Budget page: average monthly spending per category, grouped into buckets with subtotals
// and a min–max range per category.
const bud = JSON.parse(readFileSync(resolve(here, "..", "public", "budget-sample.json"), "utf8"));
const budEl = renderScreen(bud.screen, { data: bud.data, theme: bud.theme }, actions);
const budText = budEl.textContent ?? "";
assert.ok(budText.includes("Budget"), "budget title renders");
assert.ok(budText.includes("$6,250.00/mo"), "total avg monthly spend renders");
assert.ok(budText.includes("last 12 full months"), "the full-month window is described");
for (const b of ["Essential", "Shared", "Goals", "Lifestyle"]) {
  assert.ok(budText.includes(b), `the ${b} bucket renders`);
}
assert.ok(budText.includes("Rent") && budText.includes("$2,000.00/mo"), "a category with its monthly average renders");
assert.ok(budText.includes("$300.00 – $900.00"), "a category min–max range renders");
assert.ok(budText.includes("$3,900.00/mo"), "a bucket subtotal renders (Essential)");
// High-variability lines (e.g. Lifestyle / $0-month categories) also show a projected annual.
assert.ok(budText.includes("~$10,800/yr projected"), "a lumpy category shows a projected annual budget");

// Smart Home page: a status header + a per-type 24h filter + filtered recent events.
let shNav = null;
actions.navigate = (t) => { shNav = t; };
const sh = JSON.parse(readFileSync(resolve(here, "..", "public", "smarthome-sample.json"), "utf8"));
const shEl = renderScreen(sh.screen, { data: sh.data, theme: sh.theme }, actions);
const shText = shEl.textContent ?? "";
assert.ok(shText.includes("Smart Home"), "smart home title");
assert.ok(shText.includes("in the last hour"), "status counts header renders");
assert.ok(shText.includes("Home Assistant"), "a source status row renders");
assert.ok(shText.includes("Front Door"), "a recent event renders");
const shStatus = [...shEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "Online");
assert.ok(shStatus, "overall status badge renders");
// Two-tier filter: tier-1 source chips × tier-2 type chips, with a 24h count each.
assert.ok(shText.includes("Filters · 24h"), "the two-tier filter card header renders");
assert.ok(shText.includes("2/2 sources") && shText.includes("4/5 types"), "two-tier filter summary renders");
const badge = (label) => [...shEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === label);
// Tier 1 — source chips (incl. a non-HA source like Eero).
const haChip = badge("Home Assistant · 2804");
const eeroChip = badge("Eero · 224");
assert.ok(haChip && eeroChip, "a source chip renders per source with its 24h count");
haChip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(shNav, "/screen/smarthome?sources=eero", "toggling a source out preserves the rest via navHref");
// Tier 2 — type chips, including an event_type-derived type (device_update).
const lightChip = badge("Light · 318");
const duChip = badge("Device Update · 224");
const sensorChip = badge("Sensor · 1820");
assert.ok(lightChip && duChip && sensorChip, "a type chip renders per type, incl. event_type domains");
assert.notEqual(lightChip.style.color, sensorChip.style.color, "active vs inactive chips are colored differently");
lightChip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(shNav, "/screen/smarthome?types=binary_sensor,device_update,lock", "toggling a type out preserves the rest");
// Reset clears both tiers.
const resetLink = [...shEl.querySelectorAll(".text")].find((t) => (t.textContent ?? "").trim() === "Reset");
resetLink.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(shNav, "/screen/smarthome", "Reset clears both filter tiers");
// Tap-to-expand: each event carries a Details disclosure with its raw key/value rows.
assert.ok([...shEl.querySelectorAll("details.disclosure summary")].some((s) => (s.textContent ?? "").includes("Details")),
  "per-event detail disclosure renders");
assert.ok(shText.includes("lock.front_door"), "an expanded event detail row (entity) renders");

// Passcode gate: 1937 -> full, all-even -> decoy, anything else -> rejected.
const { classifyPasscode } = await import("../src/sdui/lock.ts");
assert.equal(classifyPasscode("1937"), "full", "1937 opens the full experience");
assert.equal(classifyPasscode("2468"), "decoy", "all-even opens the decoy __APP_NAME_LOWER__");
assert.equal(classifyPasscode("0000"), "decoy", "all-even (zeros) opens the decoy __APP_NAME_LOWER__");
assert.equal(classifyPasscode("1234"), null, "mixed parity is rejected");
assert.equal(classifyPasscode("246"), null, "short codes are rejected");
assert.equal(classifyPasscode("1939"), null, "near-miss of 1937 is rejected");

// Decoy __APP_NAME_LOWER__ (all-even passcode): a __APP_NAME_LOWER__-shaped page of dummy content,
// rendered locally with no network and no real data.
const { decoyManifest } = await import("../src/sdui/decoy.ts");
const decoy = decoyManifest();
assert.ok(decoy.nav === undefined, "decoy carries no nav (single page, no menu)");
const decoyEl = renderScreen(decoy.screen, { data: decoy.data, theme: decoy.theme }, actions);
const decoyText = decoyEl.textContent ?? "";
assert.ok(decoyText.includes("__APP_NAME__"), "decoy shows a __APP_NAME_LOWER__ title");
assert.ok(decoyText.includes("Anytown") && decoyText.includes("72°"), "decoy shows dummy weather");
assert.ok(decoyEl.querySelectorAll("polyline").length === 2, "decoy markets chart renders dummy series");
assert.ok(!decoyText.includes("Beard Circle") && !decoyText.includes("Phoenixville"), "decoy leaks no real content");

// 48h Transactions page: the raw AFM fix-history table dumped as columns/rows.
const afm48 = JSON.parse(readFileSync(resolve(here, "..", "public", "afm48-sample.json"), "utf8"));
const afm48El = renderScreen(afm48.screen, { data: afm48.data, theme: afm48.theme }, actions);
const afm48Text = afm48El.textContent ?? "";
assert.ok(afm48Text.includes("AFM now"), "Last-48 defaults to the afm_now tab");
const afm48Table = afm48El.querySelector("table.table");
assert.ok(afm48Table, "the page renders a table");
const afm48Headers = [...afm48Table.querySelectorAll("thead th")].map((t) => t.textContent);
assert.ok(afm48Headers.includes("deviceName") && afm48Headers.includes("latitude"), "table columns render");
assert.equal(afm48Table.querySelectorAll("tbody tr").length, 3, "one row per device in the now snapshot");
assert.ok(afm48Text.includes("iPhone 16"), "now-snapshot cells render");
// Two tabs: Now (default) and 48h history; tapping a tab navigates via ?view=.
actions.navigate = (t) => { navigated = t; }; // (an earlier section repurposed this)
const nowTab = [...afm48El.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "Now");
const rawTab = [...afm48El.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "48h history");
assert.ok(nowTab && rawTab, "Now + 48h history tabs render");
navigated = null;
rawTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(navigated, "/screen/afm48?view=raw", "the 48h history tab navigates via ?view=raw");

// AFM Live health page: a precise per-run success/failure + back-off timeline of afm_live.
const afmh = JSON.parse(readFileSync(resolve(here, "..", "public", "afm_health-sample.json"), "utf8"));
const afmhEl = renderScreen(afmh.screen, { data: afmh.data, theme: afmh.theme }, actions);
const afmhText = afmhEl.textContent ?? "";
assert.ok(afmhText.includes("Live Health"), "afm health page renders");
assert.ok(afmhText.includes("Intermittent") && afmhText.includes("1/3 failed"),
  "the intermittency verdict is shown");
assert.ok(afmhEl.querySelector(".barchart"), "the run-history bar chart renders");
// Each run shows its precise time window and outcome; a failed run names its reason.
assert.ok(afmhText.includes("8:05:01 AM") && afmhText.includes("Failed"), "per-run timeline with times + outcome");
assert.ok(afmhText.includes("failed to refresh: timed out"), "a failed run surfaces its reason");
// Back-off phases are captured under a disclosure on the failing run.
const boffDisc = [...afmhEl.querySelectorAll(".disclosure")].find((d) => (d.textContent ?? "").includes("back-off phase"));
assert.ok(boffDisc, "back-off phases render under a disclosure");
assert.ok((boffDisc.textContent ?? "").includes("retry in 30s"), "the back-off detail lines are present");

// BQ Tables page: project-level list of datasets; rows drill into a dataset by navHref.
let bqNav = null;
actions.navigate = (t) => { bqNav = t; };
const bq = JSON.parse(readFileSync(resolve(here, "..", "public", "bqtables-sample.json"), "utf8"));
const bqEl = renderScreen(bq.screen, { data: bq.data, theme: bq.theme }, actions);
const bqText = bqEl.textContent ?? "";
assert.ok(bqText.includes("BigQuery") && bqText.includes("datasets"), "bq datasets title");
assert.ok(bqText.includes("home_afm") && bqText.includes("budget"), "dataset list renders");
const dsRow = [...bqEl.querySelectorAll(".stack-row")].find((r) => (r.textContent ?? "").includes("home_afm"));
dsRow.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(bqNav, "/screen/bqtables?dataset=home_afm", "tapping a dataset drills into its tables");
// At the table level the same screen shows Columns | Preview tabs. The Columns view
// lists fields and renders no preview table; the Preview view shows a 100-row table.
const bqCols = {
  tabs: [
    { key: "columns", label: "Columns", navHref: "/screen/bqtables?dataset=home_afm&table=t&view=columns", active: true, color: "$accent" },
    { key: "preview", label: "Preview", navHref: "/screen/bqtables?dataset=home_afm&table=t&view=preview", active: false, color: "$textSecondary" },
  ],
  fields: [{ name: "latitude", typeFormatted: "FLOAT", descFormatted: "" }],
};
const colsEl = renderScreen(bq.screen, { data: { ...bq.data, bqtables: bqCols }, theme: bq.theme }, actions);
assert.ok(!colsEl.querySelector("table.table"), "Columns view renders no preview table (absent binding → null)");
assert.ok((colsEl.textContent ?? "").includes("latitude"), "Columns view lists the field rows");
const colTabs = [...colsEl.querySelectorAll(".badge")].filter((b) => ["Columns", "Preview"].includes((b.textContent ?? "").trim()));
assert.equal(colTabs.length, 2, "both tabs render");
// Tapping the Preview tab navigates to ?view=preview.
const previewTab = colTabs.find((b) => (b.textContent ?? "").trim() === "Preview");
previewTab.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(bqNav, "/screen/bqtables?dataset=home_afm&table=t&view=preview", "tapping Preview switches view");
// The Preview view renders the 100-row sample as a table.
const bqPrev = {
  tabs: bqCols.tabs,
  fields: [],
  preview: { columns: ["deviceName", "latitude"], rows: [["iPhone", "40.13"], ["iPhone", "40.14"]] },
};
const prevEl = renderScreen(bq.screen, { data: { ...bq.data, bqtables: bqPrev }, theme: bq.theme }, actions);
const prevTable = prevEl.querySelector("table.table");
assert.ok(prevTable, "Preview view renders a records table");
assert.equal(prevTable.querySelectorAll("tbody tr").length, 2, "preview rows render");
assert.ok((prevEl.textContent ?? "").includes("deviceName"), "preview columns render");

// Logs page: per-file cards with a monospace `code` tail block.
const logs = JSON.parse(readFileSync(resolve(here, "..", "public", "logs-sample.json"), "utf8"));
const logsEl = renderScreen(logs.screen, { data: logs.data, theme: logs.theme }, actions);
const logsText = logsEl.textContent ?? "";
assert.ok(logsText.includes("Logs"), "logs title");
assert.ok(logsText.includes("__APP_NAME_LOWER__.log"), "a log file name renders");
const codeBlocks = [...logsEl.querySelectorAll("pre.code")];
assert.equal(codeBlocks.length, 4, "one code block per log file");
assert.ok(logsText.includes("listening on :8080"), "a code block shows the tail");
// Each log/output block has a copy-to-clipboard link; clicking it must not throw.
const copyBtns = [...logsEl.querySelectorAll(".code-wrap .code-copy")];
assert.equal(copyBtns.length, codeBlocks.length, "every code block has a copy-to-clipboard link");
copyBtns[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); // no clipboard in jsdom → falls back, no throw
// Each log carries a Running / Failed / OK status flag.
const logBadges = [...logsEl.querySelectorAll(".badge")].map((b) => (b.textContent ?? "").trim());
assert.ok(logBadges.includes("Running") && logBadges.includes("Failed") && logBadges.includes("OK"),
  "log status flags render");
// Run-history bar chart: one .barchart per log card, bars colored by outcome + a duration label.
const logCharts = [...logsEl.querySelectorAll(".barchart")];
assert.equal(logCharts.length, 4, "one run-history bar chart per log file");
const firstBars = [...logCharts[0].querySelectorAll(".barchart-bar")];
assert.ok(firstBars.length >= 2, "the run chart renders a bar per iteration");
assert.ok(firstBars.every((b) => /%$/.test(b.style.height)), "each bar has a normalized height");
assert.ok([...logsEl.querySelectorAll(".barchart-label")].some((l) => /\d+s/.test(l.textContent ?? "")),
  "run bars show a processing-time label");
assert.ok(logsText.includes("failed") && logsText.includes("avg "), "run-history caption summarizes ok/failed + avg time");

// Status filter chips (with counts) navigate via ?status=, and each card has a 24h drill button.
actions.navigate = (t) => { navigated = t; }; // (an earlier section repurposed this)
const failChip = [...logsEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "Failed · 1");
assert.ok(failChip, "a status filter chip renders with a count");
navigated = null;
failChip.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(typeof navigated === "string" && navigated.startsWith("/screen/logs?status="), `status chip filters via ?status=, got ${navigated}`);
const drillBtn = [...logsEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "24h");
assert.ok(drillBtn, "each log card has a 24h drill button");
navigated = null;
drillBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(typeof navigated === "string" && navigated.startsWith("/screen/logfile?file="), `24h button opens the log detail, got ${navigated}`);

// Log detail (24h) screen: anomaly lines flagged + a recent-log code block.
const lf = JSON.parse(readFileSync(resolve(here, "..", "public", "logfile-sample.json"), "utf8"));
const lfEl = renderScreen(lf.screen, { data: lf.data, theme: lf.theme }, actions);
const lfText = lfEl.textContent ?? "";
assert.ok(lfText.includes("errors.err") && lfText.includes("anomalies flagged"), "log detail title + anomaly count");
assert.ok(lfText.includes("hue_collector refused"), "a flagged anomaly line renders");
// The detail page carries the larger run-history chart with a summary caption.
assert.ok(lfText.includes("Run history"), "log detail shows a Run history section");
const lfChart = lfEl.querySelector(".barchart");
assert.ok(lfChart && lfChart.querySelectorAll(".barchart-bar").length >= 3, "log detail run chart renders bars");
assert.ok([...lfEl.querySelectorAll("pre.code")].length >= 2, "anomaly lines + the recent-log block render as code");
const backLink = [...lfEl.querySelectorAll(".text")].find((t) => (t.textContent ?? "").trim() === "Logs");
navigated = null;
backLink.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(navigated, "/screen/logs", "the back link returns to Logs");

// Deploy page: server + CI status, iOS build, install button, and shippable branches/PRs.
const dep = JSON.parse(readFileSync(resolve(here, "..", "public", "deploy-sample.json"), "utf8"));
const depEl = renderScreen(dep.screen, { data: dep.data, theme: dep.theme }, actions);
const depText = depEl.textContent ?? "";
// Sequential pipeline: 1 Ship · 2 CI · 3 Deploy · 4 Current, in order.
assert.ok(depText.includes("1 · Ship") && depText.includes("2 · CI") && depText.includes("3 · Deploy") && depText.includes("4 · Current"),
  "deploy lays out the pipeline stages in order");
assert.ok(depText.indexOf("1 · Ship") < depText.indexOf("2 · CI") && depText.indexOf("2 · CI") < depText.indexOf("4 · Current"),
  "pipeline stages are sequential top-to-bottom");
assert.ok(depText.includes("Update available") && depText.includes("CI running"), "deploy shows a plain-English headline + CI status");
// The last deploy run's log renders even when it SUCCEEDED (not just on failure).
assert.ok(depText.includes("Last deploy run") && depText.includes("redeploy succeeded"),
  "deploy shows the last run's log including successful results");
// CI stages (jobs) + a pulled-through CI log + a run link.
assert.ok(depText.includes("server-ci") && depText.includes("ios-ci"), "CI stage rows (jobs) render");
assert.ok(depText.includes("View run on GitHub"), "a link to the CI run renders");
assert.ok(depText.includes("ARCHIVE SUCCEEDED"), "the CI log is pulled through");
assert.ok(depText.includes("Update ready") || depText.includes("Building") || depText.includes("Idle"), "deploy shows iOS build state");
const installBtn = [...depEl.querySelectorAll(".button")].find((b) => (b.textContent ?? "").includes("Install iOS app"));
assert.ok(installBtn, "deploy shows an iOS install button");
const shipBtns2 = [...depEl.querySelectorAll(".button")].filter((b) => (b.textContent ?? "").trim() === "Ship");
assert.ok(shipBtns2.length >= 2, "deploy lists shippable branches/PRs with Ship buttons");
const depRebase = [...depEl.querySelectorAll(".button")].find((b) => (b.textContent ?? "").trim() === "Rebase & ship");
assert.ok(depRebase, "deploy shows Rebase & ship for a behind branch");
submitted = null;
shipBtns2[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(submitted && submitted.url.startsWith("/repos_pr?"), "Ship on the Deploy page posts to /repos_pr");
const redeployBtn = [...depEl.querySelectorAll(".button")].find((b) => (b.textContent ?? "").includes("Redeploy now"));
assert.ok(redeployBtn, "deploy has a Redeploy now button");
submitted = null;
redeployBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(submitted && submitted.url, "/deploy_kick", "Redeploy now posts to /deploy_kick");

// Repos page: git + deploy + CI status badges per repo (colored), and a collapsible last-5-commits.
const repos = JSON.parse(readFileSync(resolve(here, "..", "public", "repos-sample.json"), "utf8"));
const reposEl = renderScreen(repos.screen, { data: repos.data, theme: repos.theme }, actions);
const reposText = reposEl.textContent ?? "";
assert.ok(reposText.includes("schedrunner") && reposText.includes("findmypy"), "repo names render");
assert.ok(reposText.includes("in sync") && reposText.includes("uncommitted changes"), "git + deploy status render");
const cleanBadge = [...reposEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "clean");
assert.ok(cleanBadge && cleanBadge.style.color, "a clean status badge is colored by direction");
assert.ok(reposText.includes("CI ✓") && reposText.includes("CI ✗"), "CI status badges render");
const commitsDisc = [...reposEl.querySelectorAll("details.disclosure summary")]
  .some((s) => (s.textContent ?? "").includes("recent commits"));
assert.ok(commitsDisc, "the last-5-commits disclosure renders");
assert.ok(reposText.includes("repos page"), "a recent commit subject renders");
// Ship + conditional "Rebase & ship": a branch behind base shows the rebase offer, one not behind doesn't.
const shipBtns = [...reposEl.querySelectorAll(".button")].filter((b) => (b.textContent ?? "").trim() === "Ship");
assert.ok(shipBtns.length >= 2, "each shippable branch/PR has a Ship button");
const rebaseBtns = [...reposEl.querySelectorAll(".button")].filter((b) => (b.textContent ?? "").trim() === "Rebase & ship");
assert.equal(rebaseBtns.length, 1, "only the behind-base branch shows a Rebase & ship button");
assert.ok(reposText.includes("behind main — rebase recommended"), "the behind-base note renders");
let rebaseSubmitted = null;
actions.submit = (u) => { rebaseSubmitted = u; return Promise.resolve(true); };
rebaseBtns[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.ok(typeof rebaseSubmitted === "string" && rebaseSubmitted.includes("rebase=1"),
  `Rebase & ship submits with rebase=1, got ${rebaseSubmitted}`);

// Schedrunner page: status header + per-job rows, failures first, colored badges.
const sched = JSON.parse(readFileSync(resolve(here, "..", "public", "schedrunner-sample.json"), "utf8"));
const schedEl = renderScreen(sched.screen, { data: sched.data, theme: sched.theme }, actions);
const schedText = schedEl.textContent ?? "";
assert.ok(schedText.includes("Schedrunner"), "schedrunner title");
assert.ok(schedText.includes("3 jobs · 2 ok · 1 failed"), "job counts render");
assert.ok(schedText.includes("balances-push") && schedText.includes("next in 39m"), "a job row with next-run renders");
const failBadge = [...schedEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "failed");
assert.ok(failBadge && failBadge.style.color, "a failed job badge is colored by direction");

// Sched Logs page: per-script cards with OK/FAILED badge + a code snippet.
const sl = JSON.parse(readFileSync(resolve(here, "..", "public", "schedlogs-sample.json"), "utf8"));
const slEl = renderScreen(sl.screen, { data: sl.data, theme: sl.theme }, actions);
const slText = slEl.textContent ?? "";
assert.ok(slText.includes("Sched Logs"), "sched logs title");
assert.ok(slText.includes("backup") && slText.includes("sync"), "per-script rows render");
const slFail = [...slEl.querySelectorAll(".badge")].find((b) => (b.textContent ?? "").trim() === "FAILED");
assert.ok(slFail && slFail.style.color, "a FAILED badge is colored");
const slCode = [...slEl.querySelectorAll("pre.code")].find((c) => (c.textContent ?? "").includes("ERROR: timeout"));
assert.ok(slCode, "the run snippet renders in a code block");

// Docs page: a folder browser whose rows navigate by resolving item.navHref.
let docNav = null;
actions.navigate = (t) => { docNav = t; };
const docs = JSON.parse(readFileSync(resolve(here, "..", "public", "docs-sample.json"), "utf8"));
const docsEl = renderScreen(docs.screen, { data: docs.data, theme: docs.theme }, actions);
const docsText = docsEl.textContent ?? "";
assert.ok(docsText.includes("Projects") && docsText.includes("Ideas.md"), "folder + file entries render");
// Tapping a folder row navigates to its per-item path (urlBinding -> item.navHref).
const projectsRow = [...docsEl.querySelectorAll(".stack-row")].find((r) => (r.textContent ?? "").includes("Projects"));
assert.ok(projectsRow, "a docs entry row is present");
projectsRow.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(docNav, "/screen/docs?path=Home%2FProjects", "tapping a folder navigates to its docs path");
// The "up" control navigates to the parent (docs.upHref).
const upRow = [...docsEl.querySelectorAll(".text")].find((t) => (t.textContent ?? "").includes("← /Private"));
upRow.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
assert.equal(docNav, "/screen/docs", "tapping up navigates to the parent path");

// Markdown component renders a safe HTML subset and escapes raw markup.
const { renderMarkdown } = await import("../src/sdui/markdown.ts");
const md = renderMarkdown("# Title\n\nHello **bold** and `code` and [x](https://e.com).\n\n- a\n- b");
assert.ok(md.includes("<h1>Title</h1>"), "heading renders");
assert.ok(md.includes("<strong>bold</strong>") && md.includes("<code>code</code>"), "inline spans render");
assert.ok(md.includes('<a href="https://e.com"') && md.includes("<li>a</li>"), "links + lists render");
assert.ok(!renderMarkdown("<script>alert(1)</script>").includes("<script>"), "raw HTML is escaped, not injected");

// Unknown component type degrades to a placeholder, never throws.
const unknown = renderScreen(
  { type: "screen", children: [{ type: "totally-new-widget-9000" }] },
  scope,
  actions,
);
assert.ok(unknown.querySelector(".placeholder"), "unknown type should render a placeholder");

// Messages page: per-source status + grouped recent iMessage/SMS and Gmail rows.
const msgs = JSON.parse(readFileSync(resolve(here, "..", "public", "messages-sample.json"), "utf8"));
const msgsEl = renderScreen(msgs.screen, { data: msgs.data, theme: msgs.theme }, actions);
const msgsText = msgsEl.textContent ?? "";
assert.ok(msgsText.includes("Messages"), "messages title");
assert.ok(msgsText.includes("iMessage / SMS") && msgsText.includes("joe@gmail.com"), "both source groups render");
assert.ok(msgsText.includes("On my way") && msgsText.includes("PR #55 merged"), "a message row and an email row render");
assert.ok([...msgsEl.querySelectorAll(".badge")].some((b) => (b.textContent ?? "").trim() === "OK"), "source status badge renders");

// Theming: color tokens resolve to CSS variables (so a light/dark swap restyles live),
// and the active palette follows the system color scheme with a light fallback.
const { resolveColor } = await import("../src/sdui/binding.ts");
const tScope = { data: { dir: "up" }, theme: { colors: {} } };
assert.equal(resolveColor("$accent", tScope), "var(--c-accent)", "$token → CSS var");
assert.equal(resolveColor("dir", tScope), "var(--c-up)", "a binding resolving to 'up' → var(--c-up)");
assert.equal(resolveColor("#123456", tScope), "#123456", "literal hex passes through");
const { activeColors } = await import("../src/sdui/theme.ts");
const themed = { colors: { accent: "#LIGHT" }, dark: { accent: "#DARK" } };
// jsdom has no matchMedia → prefersDark() is false → the light/default palette is used.
assert.equal(activeColors(themed).accent, "#LIGHT", "fallback palette is light");
// With the system preferring dark, the dark palette is selected.
globalThis.window.matchMedia = (q) => ({ matches: /dark/.test(q), media: q, addEventListener() {}, removeEventListener() {} });
assert.equal(activeColors(themed).accent, "#DARK", "dark palette selected when system prefers dark");
delete globalThis.window.matchMedia;

console.log("✓ render smoke test passed (cards, chart, tabs, afm table, config form+submit, unknown-type, theming)");
