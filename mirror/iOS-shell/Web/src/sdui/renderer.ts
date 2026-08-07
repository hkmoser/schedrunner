import type { Action, JSONValue, Node, Style } from "./manifest";
import { resolveColor, resolvePath, resolveText, type Scope } from "./binding";
import { fontSize, fontWeight } from "./theme";
import { iconGlyph } from "./registry";
import { runAction, type ActionContext } from "./actions";
import { renderLineChart } from "../charts/lines";
import { renderBarChart } from "../charts/bars";
import { renderMap } from "./map";
import { renderMarkdown } from "./markdown";
import { FormState } from "./form";

interface Ctx {
  scope: Scope;
  actions: ActionContext;
  form: FormState;
}

// Remembers collapsed/expanded disclosure sections across re-renders this session.
const disclosureState = new Map<string, boolean>();

// Apply a node's style onto an element. Unknown style keys are ignored (forward-compatible).
function applyStyle(el: HTMLElement, style: Style | undefined, scope: Scope) {
  if (!style) return;
  if (style.padding != null) el.style.padding = `${style.padding}px`;
  if (style.spacing != null) el.style.gap = `${style.spacing}px`;
  if (style.cornerRadius != null) el.style.borderRadius = `${style.cornerRadius}px`;
  if (style.opacity != null) el.style.opacity = String(style.opacity);
  if (style.width != null) el.style.width = typeof style.width === "number" ? `${style.width}px` : style.width;
  if (style.height != null) el.style.height = typeof style.height === "number" ? `${style.height}px` : style.height;
  const fg = resolveColor(style.color, scope);
  if (fg) el.style.color = fg;
  const bg = resolveColor(style.background, scope);
  if (bg) el.style.background = bg;
  const fs = fontSize(style.font);
  if (fs) el.style.fontSize = fs;
  const fw = fontWeight(style.font, style.weight);
  if (fw) el.style.fontWeight = fw;
  if (style.align) {
    const map: Record<string, string> = { leading: "flex-start", center: "center", trailing: "flex-end" };
    el.style.alignItems = map[style.align] ?? "stretch";
  }
  // Let a row of items (e.g. filter chips) wrap instead of overflowing.
  if (style.wrap) el.style.flexWrap = "wrap";
}

// If a node has props.repeat, return the resolved array to iterate; otherwise null.
function repeatItems(node: Node, scope: Scope): JSONValue[] | null {
  const path = node.props?.repeat;
  if (typeof path !== "string") return null;
  const arr = resolvePath(path, scope);
  return Array.isArray(arr) ? arr : [];
}

function renderChildren(node: Node, ctx: Ctx, parent: HTMLElement) {
  const items = repeatItems(node, ctx.scope);
  if (items != null) {
    for (const item of items) {
      const childScope: Scope = { ...ctx.scope, item };
      for (const child of node.children ?? []) {
        const el = renderNode(child, { ...ctx, scope: childScope });
        if (el) parent.appendChild(el);
      }
    }
    return;
  }
  for (const child of node.children ?? []) {
    const el = renderNode(child, ctx);
    if (el) parent.appendChild(el);
  }
}

function stack(node: Node, ctx: Ctx, direction: "row" | "column"): HTMLElement {
  const el = document.createElement("div");
  el.className = `stack stack-${direction}`;
  el.style.display = "flex";
  el.style.flexDirection = direction;
  if (node.style?.spacing == null) el.style.gap = "var(--spacing, 12px)";
  applyStyle(el, node.style, ctx.scope);
  attachAction(el, node, ctx);
  renderChildren(node, ctx, el);
  return el;
}

// Resolve an action's dynamic fields against the current scope (e.g. openURL's
// urlBinding -> a concrete url from the data bag).
function resolveAction(action: Action | undefined, scope: Scope): Action | undefined {
  if (!action) return action;
  // urlBinding fills the target of openURL and navigate (e.g. a per-item docs path).
  if (action.urlBinding) {
    const u = resolvePath(action.urlBinding, scope);
    return { ...action, url: typeof u === "string" ? u : action.url };
  }
  if (action.valueBinding) {
    const v = resolvePath(action.valueBinding, scope);
    return { ...action, value: v ?? action.value };
  }
  return action;
}

function attachAction(el: HTMLElement, node: Node, ctx: Ctx) {
  if (!node.action) return;
  el.style.cursor = "pointer";
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    runAction(resolveAction(node.action, ctx.scope), ctx.actions);
  });
}

// Copy text to the clipboard with a fallback for older/locked-down WebKit, and give the
// button brief "Copied" feedback. Used by the copy link on each code/log block.
async function copyText(text: string, btn: HTMLButtonElement): Promise<void> {
  let ok = false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch {
    ok = false;
  }
  if (!ok) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch {
      ok = false;
    }
  }
  btn.textContent = ok ? "Copied" : "Press to select";
  btn.classList.toggle("copied", ok);
  window.setTimeout(() => {
    btn.textContent = "Copy";
    btn.classList.remove("copied");
  }, 1400);
}

function placeholder(label: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "placeholder";
  el.textContent = import.meta.env?.DEV ? `⟨${label}⟩` : "";
  return el;
}

// Render one node into a DOM element. Returns null when the node produces nothing
// (e.g. a text node whose binding is unresolved and has no fallback).
export function renderNode(node: Node, ctx: Ctx): HTMLElement | null {
  switch (node.type) {
    case "screen": {
      const el = document.createElement("div");
      el.className = "screen";
      applyStyle(el, node.style, ctx.scope);
      renderChildren(node, ctx, el);
      return el;
    }
    case "scroll": {
      const el = document.createElement("div");
      el.className = "scroll";
      applyStyle(el, node.style, ctx.scope);
      renderChildren(node, ctx, el);
      return el;
    }
    case "vstack":
    case "list":
      return stack(node, ctx, "column");
    case "hstack":
    case "row":
      return stack(node, ctx, "row");
    case "zstack": {
      const el = document.createElement("div");
      el.className = "zstack";
      el.style.display = "grid";
      applyStyle(el, node.style, ctx.scope);
      renderChildren(node, ctx, el);
      return el;
    }
    case "spacer": {
      const el = document.createElement("div");
      el.style.flex = "1 1 auto";
      return el;
    }
    case "divider": {
      const el = document.createElement("div");
      el.className = "divider";
      const c = resolveColor(node.style?.color, ctx.scope);
      if (c) el.style.background = c;
      return el;
    }
    case "card": {
      const el = document.createElement("div");
      el.className = "card";
      el.style.display = "flex";
      el.style.flexDirection = "column";
      if (node.style?.spacing == null) el.style.gap = "8px";
      applyStyle(el, node.style, ctx.scope);
      attachAction(el, node, ctx);
      renderChildren(node, ctx, el);
      return el;
    }
    case "text": {
      const text = resolveText(node.binding, node.props, ctx.scope);
      if (text == null) return null;
      const el = document.createElement("div");
      el.className = "text";
      el.textContent = text;
      applyStyle(el, node.style, ctx.scope);
      attachAction(el, node, ctx);
      return el;
    }
    case "image": {
      const name = node.binding ? resolvePath(node.binding, ctx.scope) : node.props?.name;
      const el = document.createElement("div");
      el.className = "image";
      el.textContent = iconGlyph(typeof name === "string" ? name : undefined);
      applyStyle(el, node.style, ctx.scope);
      return el;
    }
    case "badge": {
      const text = resolveText(node.binding, node.props, ctx.scope);
      if (text == null) return null;
      const el = document.createElement("span");
      el.className = "badge";
      el.textContent = text;
      const c = resolveColor(node.style?.color, ctx.scope);
      if (c) {
        el.style.color = c;
        el.style.borderColor = c;
      }
      // A badge with an action becomes a tappable chip (e.g. filter toggles).
      attachAction(el, node, ctx);
      return el;
    }
    case "lineChart": {
      const el = document.createElement("div");
      el.className = "chart-wrap";
      const h = typeof node.style?.height === "number" ? node.style.height : 160;
      const fallback = "var(--c-accent)"; // CSS var so the chart line follows light/dark
      const data = node.binding ? resolvePath(node.binding, ctx.scope) : undefined;
      el.appendChild(renderLineChart(data, h, fallback));
      return el;
    }
    case "barChart": {
      // Run-history bars (Logs): height ∝ processing time, color = success/failure.
      const data = node.binding ? resolvePath(node.binding, ctx.scope) : undefined;
      if (!Array.isArray(data) || data.length === 0) return null;
      const el = document.createElement("div");
      el.className = "chart-wrap barchart-wrap";
      const h = typeof node.style?.height === "number" ? node.style.height : 96;
      el.appendChild(renderBarChart(data, h, ctx.scope));
      return el;
    }
    case "table": {
      const v = node.binding ? resolvePath(node.binding, ctx.scope) : undefined;
      const obj = v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, JSONValue>) : undefined;
      // No bound object at all (e.g. the BQ Columns tab, where `preview` is absent) →
      // render nothing rather than an empty "No rows" table.
      if (!obj) return null;
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      const errMsg = obj?.error;
      if (typeof errMsg === "string" && errMsg) {
        const e = document.createElement("div");
        e.className = "table-error";
        e.textContent = errMsg;
        wrap.appendChild(e);
        return wrap;
      }
      const columns = Array.isArray(obj?.columns) ? (obj!.columns as JSONValue[]) : [];
      const rows = Array.isArray(obj?.rows) ? (obj!.rows as JSONValue[]) : [];
      const table = document.createElement("table");
      table.className = "table";
      const thead = document.createElement("thead");
      const htr = document.createElement("tr");
      for (const c of columns) {
        const th = document.createElement("th");
        th.textContent = String(c);
        htr.appendChild(th);
      }
      thead.appendChild(htr);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      for (const r of rows) {
        const tr = document.createElement("tr");
        const cells = Array.isArray(r) ? (r as JSONValue[]) : [r];
        for (const cell of cells) {
          const td = document.createElement("td");
          td.textContent = cell == null ? "" : String(cell);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      wrap.appendChild(table);
      if (rows.length === 0 && !errMsg) {
        const empty = document.createElement("div");
        empty.className = "table-error";
        empty.textContent = "No rows.";
        wrap.appendChild(empty);
      }
      return wrap;
    }

    case "map": {
      const el = document.createElement("div");
      el.className = "map";
      const h = typeof node.style?.height === "number" ? node.style.height : 220;
      el.style.height = `${h}px`;
      const data = node.binding ? resolvePath(node.binding, ctx.scope) : undefined;
      const mapId = typeof node.props?.mapId === "string" ? node.props.mapId : "activity";
      renderMap(el, data, mapId);
      return el;
    }

    case "disclosure": {
      // Collapsible section: a tappable header (summary) over its children.
      const details = document.createElement("details");
      details.className = "disclosure";
      const title = resolveText(node.binding, node.props, ctx.scope) ?? "";
      const key = (node.props?.id as string) ?? `disc:${title}`;
      const openDefault = node.props?.open !== false;
      details.open = disclosureState.has(key) ? disclosureState.get(key)! : openDefault;
      details.addEventListener("toggle", () => disclosureState.set(key, details.open));
      const summary = document.createElement("summary");
      summary.className = "disclosure-summary";
      summary.textContent = title;
      details.appendChild(summary);
      const body = document.createElement("div");
      body.className = "disclosure-body";
      renderChildren(node, ctx, body);
      details.appendChild(body);
      return details;
    }

    case "code": {
      // Monospace preformatted block (log tails, raw output) with a copy-to-clipboard link.
      const text = resolveText(node.binding, node.props, ctx.scope);
      if (text == null || text === "") return null;
      const wrap = document.createElement("div");
      wrap.className = "code-wrap";
      const el = document.createElement("pre");
      el.className = "code";
      el.textContent = text;
      applyStyle(el, node.style, ctx.scope);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "code-copy";
      copy.textContent = "Copy";
      copy.setAttribute("aria-label", "Copy to clipboard");
      copy.addEventListener("click", (e) => {
        e.stopPropagation();
        void copyText(text, copy);
      });
      wrap.append(el, copy);
      return wrap;
    }

    case "markdown": {
      // Rendered Markdown (docs viewer). Empty content renders nothing.
      const text = resolveText(node.binding, node.props, ctx.scope);
      if (text == null || text.trim() === "") return null;
      const el = document.createElement("div");
      el.className = "markdown";
      el.innerHTML = renderMarkdown(text);
      return el;
    }

    case "timeline":
      return renderTimeline(node, ctx);

    case "field":
      return renderField(node, ctx);

    case "button": {
      const el = document.createElement("button");
      el.className = "button";
      const baseText = resolveText(node.binding, node.props, ctx.scope) ?? "";
      el.textContent = baseText;
      applyStyle(el, node.style, ctx.scope);
      const action = node.action;
      el.addEventListener("click", () => {
        if (action?.type === "submit") {
          // urlBinding lets a per-row submit POST to a context-carrying url (e.g. the repos
          // "Ship" bar → /repos_pr?owner=…&branch=…). Show progress in place: a spinner while
          // posting, then ✓ (and a refresh so the result updates) or Retry on failure.
          const url = resolveAction(action, ctx.scope)?.url ?? "";
          if (!url || el.dataset.phase === "working" || el.dataset.phase === "done" || el.dataset.phase === "polling") return;
          el.dataset.phase = "working";
          el.disabled = true;
          el.classList.remove("is-failed");
          el.classList.add("is-working");
          el.innerHTML = '<span class="spinner" aria-hidden="true"></span>Working…';
          void Promise.resolve(ctx.actions.submit(url, ctx.form.toItems())).then((result) => {
            const ok = typeof result === "boolean" ? result : (result?.ok ?? true);
            const polling = typeof result === "object" && result !== null && (result as { polling?: boolean }).polling === true;
            el.classList.remove("is-working");
            if (ok && polling) {
              // Async job started — show "Building…" (disabled) while the server-side polling
              // loop refreshes the page every 5 s. The button resets naturally when the page
              // re-renders with the updated status.
              el.dataset.phase = "polling";
              el.textContent = "Building…";
            } else if (ok) {
              el.dataset.phase = "done";
              el.classList.add("is-done");
              el.textContent = "✓ Done";
              window.setTimeout(() => ctx.actions.refresh(), 900);
            } else {
              el.dataset.phase = "idle";
              el.disabled = false;
              el.classList.add("is-failed");
              el.textContent = "Retry";
              window.setTimeout(() => { el.classList.remove("is-failed"); el.textContent = baseText; }, 1800);
            }
          });
        } else {
          runAction(resolveAction(action, ctx.scope), ctx.actions);
        }
      });
      return el;
    }
    case "propertyCard": {
      const raw = node.binding ? resolvePath(node.binding, ctx.scope) : undefined;
      const prop = raw && typeof raw === "object" && !Array.isArray(raw)
        ? (raw as Record<string, JSONValue>)
        : {};
      return renderPropertyCard(prop, ctx);
    }

    default:
      // Unknown component type -> inert placeholder; never throw.
      return placeholder(node.type);
  }
}

// A Google-Maps-style timeline of segments. Binds to an array of
// { index, mode, icon, place, timeFormatted, durationFormatted, distanceFormatted,
//   categoryColor }. Tapping an entry focuses that segment on the map (props.mapId).
function renderTimeline(node: Node, ctx: Ctx): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "timeline";
  const arr = node.binding ? resolvePath(node.binding, ctx.scope) : undefined;
  const segs = Array.isArray(arr) ? (arr as JSONValue[]) : [];
  const mapId = typeof node.props?.mapId === "string" ? node.props.mapId : "activity";

  for (const s of segs) {
    const seg = (s && typeof s === "object" && !Array.isArray(s)) ? (s as Record<string, JSONValue>) : {};
    const color = typeof seg.categoryColor === "string" ? seg.categoryColor : "#6ea8fe";
    const isStop = seg.category === "Stopped";
    const entry = document.createElement("div");
    entry.className = isStop ? "tl-entry tl-stop" : "tl-entry";
    // Give longer stays/segments more vertical room — representative, not linear.
    const durMin = typeof seg.durationMin === "number" ? seg.durationMin : Number(seg.durationMin) || 0;
    entry.style.minHeight = `${44 + Math.min(120, Math.round(13 * Math.sqrt(Math.max(0, durMin))))}px`;

    const rail = document.createElement("div");
    rail.className = "tl-rail";
    // Stops are filled circles (primary); moves/charges are bare icons (secondary).
    const dot = document.createElement("div");
    dot.className = isStop ? "tl-dot" : "tl-dot tl-bare";
    if (isStop) dot.style.background = color;
    dot.textContent = iconGlyph(typeof seg.icon === "string" ? seg.icon : undefined);
    rail.appendChild(dot);

    const body = document.createElement("div");
    body.className = isStop ? "tl-body tl-boxed" : "tl-body";
    const title = document.createElement("div");
    title.className = "tl-title";
    const mode = typeof seg.mode === "string" ? seg.mode : "";
    const place = typeof seg.place === "string" ? seg.place : "";
    // Stops headline the place ("Edison, NJ"); moves read naturally ("Driving to …").
    title.textContent = isStop
      ? place || mode || "Stop"
      : place && place.startsWith("to ")
        ? `${mode} ${place}`
        : place && place !== mode
          ? `${mode} · ${place}`
          : mode || place;
    const meta = document.createElement("div");
    meta.className = "tl-meta";
    // Stops lead with dwell time (the key fact); moves lead with time + distance.
    const order = isStop
      ? [seg.durationFormatted, seg.timeFormatted]
      : [seg.timeFormatted, seg.durationFormatted, seg.distanceFormatted];
    meta.textContent = order.map((b) => (typeof b === "string" ? b : "")).filter(Boolean).join(" · ");
    body.append(title, meta);

    entry.append(rail, body);

    const mapsUrl = typeof seg.mapsUrl === "string" ? seg.mapsUrl : "";
    if (mapsUrl) {
      const link = document.createElement("a");
      link.className = "tl-maps";
      link.href = mapsUrl;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = "Open in Maps";
      link.textContent = iconGlyph("mappin");
      link.addEventListener("click", (e) => e.stopPropagation()); // don't also focus
      entry.appendChild(link);
    }

    // Label-this-place: stops carry a labelHref to the "Label location" screen.
    const labelHref = typeof seg.labelHref === "string" ? seg.labelHref : "";
    if (labelHref) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "tl-label";
      tag.title = seg.known ? "Edit this known place" : "Label this place";
      tag.textContent = iconGlyph(seg.known ? "star.fill" : "tag");
      tag.addEventListener("click", (e) => {
        e.stopPropagation(); // don't also focus the map
        ctx.actions.navigate(labelHref);
      });
      entry.appendChild(tag);
    }

    const idx = typeof seg.index === "number" ? seg.index : Number(seg.index);
    if (Number.isFinite(idx)) {
      entry.style.cursor = "pointer";
      entry.addEventListener("click", () => ctx.actions.focus(mapId, idx));
    }
    wrap.appendChild(entry);
  }
  return wrap;
}

// An editable form field whose widget is chosen by the value's `type`. Binds to a
// { key, label, value, type, options? } object, or uses props.field/label/fieldType.
function renderField(node: Node, ctx: Ctx): HTMLElement | null {
  const bound = node.binding ? resolvePath(node.binding, ctx.scope) : undefined;
  const obj = bound && typeof bound === "object" && !Array.isArray(bound) ? (bound as Record<string, JSONValue>) : undefined;
  const key = String(obj?.key ?? node.props?.field ?? "");
  if (!key) return null;
  const label = String(obj?.label ?? node.props?.label ?? key);
  const type = String(obj?.type ?? node.props?.fieldType ?? "string");
  const initial = obj?.value ?? node.props?.value ?? "";
  const initStr = initial == null ? "" : String(initial);
  ctx.form.set(key, initStr, type);

  const row = document.createElement("label");
  row.className = "field";
  const lab = document.createElement("span");
  lab.className = "field-label";
  lab.textContent = label;
  row.appendChild(lab);

  let input: HTMLElement;
  if (type === "bool") {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "field-toggle";
    cb.checked = initStr === "true" || initStr === "1";
    ctx.form.set(key, cb.checked ? "true" : "false", type);
    cb.addEventListener("change", () => ctx.form.set(key, cb.checked ? "true" : "false", type));
    input = cb;
  } else if (type === "enum") {
    const rawOpts = (obj?.options ?? node.props?.options) as JSONValue | undefined;
    const list = (Array.isArray(rawOpts) ? rawOpts : []).map((o) => {
      if (o && typeof o === "object" && !Array.isArray(o)) {
        const r = o as Record<string, JSONValue>;
        const value = String(r.value ?? "");
        return { value, label: String(r.label ?? value) };
      }
      return { value: String(o), label: String(o) };
    });
    const sel = document.createElement("select");
    sel.className = "field-input";
    for (const { value, label } of list) {
      const o = document.createElement("option");
      o.value = value;
      o.textContent = label;
      if (value === initStr) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => ctx.form.set(key, sel.value, type));
    input = sel;
  } else {
    const inp = document.createElement("input");
    inp.className = "field-input";
    const secret = type === "secret" || type === "password";
    inp.type = secret ? "password" : type === "int" || type === "float" || type === "number" ? "number" : "text";
    inp.value = initStr;
    // Masked fields show "•••• set" as a placeholder and start blank (we never echo secrets).
    if (secret) {
      const ph = obj?.placeholderFormatted;
      if (typeof ph === "string") inp.placeholder = ph;
      inp.autocomplete = "off";
    }
    inp.addEventListener("input", () => ctx.form.set(key, inp.value, type));
    input = inp;
  }
  row.appendChild(input);
  return row;
}

export function renderScreen(
  screen: Node,
  scope: Scope,
  actions: ActionContext,
  form: FormState = new FormState(),
): HTMLElement {
  const el = renderNode(screen, { scope, actions, form });
  return el ?? document.createElement("div");
}

// Rich property card for the Housing Search page.
// Bound to an object with: id, address, price, contact, interest, status,
// actionRequired, listingUrl, photoUrl, mapsUrl, commsCount, commsSnippet.
function renderPropertyCard(prop: Record<string, JSONValue>, ctx: Ctx): HTMLElement {
  const id = String(prop.id ?? "");
  const address = String(prop.address ?? "");
  const price = String(prop.price ?? "");
  const contact = String(prop.contact ?? "");
  const interest = String(prop.interest ?? "medium");
  const status = String(prop.status ?? "flagged");
  const actionRequired = prop.actionRequired;
  const listingUrl = String(prop.listingUrl ?? "");
  const photoUrl = String(prop.photoUrl ?? "");
  const mapsUrl = String(prop.mapsUrl ?? "");
  const commsCount = typeof prop.commsCount === "number" ? prop.commsCount : 0;
  const commsSnippet = typeof prop.commsSnippet === "string" ? prop.commsSnippet : "";

  const card = document.createElement("div");
  card.className = "prop-card";

  // Photo or placeholder
  if (photoUrl) {
    const img = document.createElement("img");
    img.className = "prop-photo";
    img.src = photoUrl;
    img.alt = address;
    card.appendChild(img);
  } else {
    const ph = document.createElement("div");
    ph.className = "prop-photo-placeholder";
    ph.textContent = "🏡";
    card.appendChild(ph);
  }

  const body = document.createElement("div");
  body.className = "prop-body";

  // Address + price row
  const addrRow = document.createElement("div");
  addrRow.className = "prop-addr-row";
  const addrEl = document.createElement("div");
  addrEl.className = "prop-addr";
  addrEl.textContent = address;
  const priceEl = document.createElement("div");
  priceEl.className = "prop-price";
  priceEl.textContent = price;
  addrRow.append(addrEl, priceEl);
  body.appendChild(addrRow);

  if (contact) {
    const contactEl = document.createElement("div");
    contactEl.className = "prop-contact";
    contactEl.textContent = contact;
    body.appendChild(contactEl);
  }

  // Pipeline: Flagged → Scheduling → Scheduled → Visited
  const STEPS = ["flagged", "scheduling", "scheduled", "visited"];
  const STEP_LABELS: Record<string, string> = {
    flagged: "Flagged", scheduling: "Scheduling", scheduled: "Scheduled", visited: "Visited",
  };
  const currentIdx = STEPS.indexOf(status);
  const pipeline = document.createElement("div");
  pipeline.className = "prop-pipeline";
  for (let i = 0; i < STEPS.length; i++) {
    const step = document.createElement("div");
    step.className = "prop-pipe-step" + (i < currentIdx ? " done" : i === currentIdx ? " active" : "");
    const dot = document.createElement("div");
    dot.className = "prop-pipe-dot";
    const label = document.createElement("div");
    label.className = "prop-pipe-label";
    label.textContent = STEP_LABELS[STEPS[i]];
    step.append(dot, label);
    pipeline.appendChild(step);
    if (i < STEPS.length - 1) {
      const line = document.createElement("div");
      line.className = "prop-pipe-line";
      pipeline.appendChild(line);
    }
  }
  body.appendChild(pipeline);

  // Action banner (if the property needs follow-up)
  if (actionRequired) {
    const banner = document.createElement("div");
    banner.className = "prop-action";
    banner.textContent = typeof actionRequired === "string" && actionRequired ? actionRequired : "Action needed";
    body.appendChild(banner);
  }

  // Interest chips + external links
  const footer = document.createElement("div");
  footer.className = "prop-footer";

  const chips = document.createElement("div");
  chips.className = "prop-chips";
  for (const level of ["high", "medium", "low"]) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "prop-chip" + (interest === level ? " active-" + level : "");
    chip.textContent = level.charAt(0).toUpperCase() + level.slice(1);
    chip.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch("/housing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [{ key: "id", value: id }, { key: "interest", value: level }] }),
      });
      ctx.actions.refresh();
    });
    chips.appendChild(chip);
  }
  footer.appendChild(chips);

  const links = document.createElement("div");
  links.className = "prop-links";
  if (listingUrl) {
    const a = document.createElement("a");
    a.href = listingUrl;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "Listing ↗";
    links.appendChild(a);
  }
  if (mapsUrl) {
    const a = document.createElement("a");
    a.href = mapsUrl;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "Maps ↗";
    links.appendChild(a);
  }
  footer.appendChild(links);
  body.appendChild(footer);

  // Comms summary
  if (commsCount > 0 || commsSnippet) {
    const comms = document.createElement("div");
    comms.className = "prop-comms";
    const msgLabel = commsCount > 0 ? `💬 ${commsCount} msg${commsCount !== 1 ? "s" : ""}` : "";
    comms.textContent = msgLabel && commsSnippet ? `${msgLabel} · ${commsSnippet}` : msgLabel || commsSnippet;
    body.appendChild(comms);
  }

  // "Advance to next step" button (not shown for visited/passed)
  const NEXT_STATUS: Record<string, string> = {
    flagged: "scheduling", scheduling: "scheduled", scheduled: "visited",
  };
  if (status !== "passed" && NEXT_STATUS[status]) {
    const nextLabel = STEP_LABELS[NEXT_STATUS[status]] ?? "";
    const advanceBtn = document.createElement("button");
    advanceBtn.type = "button";
    advanceBtn.className = "prop-advance";
    advanceBtn.textContent = `Mark as ${nextLabel}`;
    advanceBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch("/housing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [{ key: "id", value: id }, { key: "status", value: NEXT_STATUS[status] }] }),
      });
      ctx.actions.refresh();
    });
    body.appendChild(advanceBtn);
  }

  // Pass button + inline form with tag chips
  if (status !== "passed") {
    const PASS_TAGS = ["Too expensive", "Wrong area", "Needs too much work", "Lost bidding", "Changed plans", "Other"];
    const passToggle = document.createElement("button");
    passToggle.type = "button";
    passToggle.className = "prop-pass-btn";
    passToggle.textContent = "Pass on this property";

    const passForm = document.createElement("div");
    passForm.className = "prop-pass-form";
    passForm.hidden = true;

    const tagWrap = document.createElement("div");
    tagWrap.className = "prop-pass-tags";
    const selectedTags = new Set<string>();
    for (const tag of PASS_TAGS) {
      const tagChip = document.createElement("button");
      tagChip.type = "button";
      tagChip.className = "prop-tag-chip";
      tagChip.textContent = tag;
      tagChip.addEventListener("click", (e) => {
        e.stopPropagation();
        if (selectedTags.has(tag)) { selectedTags.delete(tag); tagChip.classList.remove("selected"); }
        else { selectedTags.add(tag); tagChip.classList.add("selected"); }
      });
      tagWrap.appendChild(tagChip);
    }
    passForm.appendChild(tagWrap);

    const noteEl = document.createElement("textarea");
    noteEl.className = "prop-pass-note";
    noteEl.placeholder = "Optional notes…";
    noteEl.rows = 2;
    passForm.appendChild(noteEl);

    const passSubmit = document.createElement("button");
    passSubmit.type = "button";
    passSubmit.className = "prop-pass-submit";
    passSubmit.textContent = "Confirm Pass";
    passSubmit.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch("/housing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: [
            { key: "id", value: id },
            { key: "status", value: "passed" },
            { key: "passedTags", value: Array.from(selectedTags).join(", ") },
            { key: "passedNote", value: noteEl.value },
          ],
        }),
      });
      ctx.actions.refresh();
    });
    passForm.appendChild(passSubmit);

    passToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      passForm.hidden = !passForm.hidden;
    });

    body.append(passToggle, passForm);
  }

  card.appendChild(body);
  return card;
}
