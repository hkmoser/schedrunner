import type { JSONValue, Theme } from "./manifest";

// Resolution scope passed down the render tree. `data` is the manifest value bag;
// `item` is the current element when inside a repeated node (exposes `item.*` paths).
export interface Scope {
  data: Record<string, JSONValue>;
  theme: Theme;
  item?: JSONValue;
}

// Resolve a dotted path like "stocks.series" or "item.changePctFormatted".
// `local.*` paths are native-only (EventKit etc.) and intentionally resolve to
// undefined on web so the bound node falls back gracefully.
export function resolvePath(path: string, scope: Scope): JSONValue | undefined {
  if (!path) return undefined;
  let root: JSONValue | undefined;
  let rest = path;

  if (path === "item" || path.startsWith("item.")) {
    root = scope.item;
    rest = path.slice(4).replace(/^\./, "");
  } else if (path.startsWith("local.")) {
    return undefined; // native-only namespace
  } else {
    root = scope.data as JSONValue;
  }

  if (rest === "") return root;
  let cur: JSONValue | undefined = root;
  for (const key of rest.split(".")) {
    if (cur == null || typeof cur !== "object" || Array.isArray(cur)) return undefined;
    cur = (cur as Record<string, JSONValue>)[key];
  }
  return cur;
}

// Resolve a color spec to a concrete CSS color string.
//   "$token"  -> var(--c-token)  (so light/dark switches live via CSS variables)
//   "#rrggbb" -> literal
//   "up"/"down" (or a binding that yields them) -> var(--c-up)/var(--c-down)
//   any other string -> treated as a binding path, then re-resolved
// Returning CSS custom-property references (rather than the resolved hex) means a single
// palette swap — e.g. on a system light/dark change — restyles everything with no re-render.
export function resolveColor(spec: string | undefined, scope: Scope): string | undefined {
  if (!spec) return undefined;
  let v = spec;
  if (!spec.startsWith("$") && !spec.startsWith("#")) {
    const resolved = resolvePath(spec, scope);
    if (resolved == null) return undefined;
    v = String(resolved);
  }
  if (v === "up") v = "$up";
  if (v === "down") v = "$down";
  if (v.startsWith("$")) return `var(--c-${v.slice(1)})`;
  return v;
}

// Resolve the display text for a node from binding (with optional props.fallback)
// or a literal props.text. Returns undefined when nothing renders.
export function resolveText(
  binding: string | undefined,
  props: Record<string, JSONValue> | undefined,
  scope: Scope,
): string | undefined {
  if (binding) {
    const v = resolvePath(binding, scope);
    if (v != null) return String(v);
    const fb = props?.fallback;
    return typeof fb === "string" ? fb : undefined;
  }
  const t = props?.text;
  return typeof t === "string" ? t : undefined;
}
