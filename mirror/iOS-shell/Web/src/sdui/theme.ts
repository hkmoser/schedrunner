import type { Theme } from "./manifest";

// True when the device/system prefers dark. Falls back to LIGHT when it can't be
// determined (e.g. no matchMedia), per the configured default.
export function prefersDark(): boolean {
  try {
    return typeof window !== "undefined" && !!window.matchMedia
      && window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

// The palette to use right now: the theme's `dark` map when the system prefers dark and
// one is provided, else `colors` (the light/default palette). Components resolve color
// tokens to `var(--c-*)`, so swapping these vars restyles everything live — no re-render.
export function activeColors(theme: Theme | undefined): Record<string, string> {
  const t = (theme ?? {}) as Theme & { dark?: Record<string, string> };
  if (prefersDark() && t.dark) return t.dark;
  return t.colors ?? {};
}

// The theme as it should render now (its `.colors` set to the active palette), so the
// render scope and the CSS variables agree on light vs dark.
export function activeTheme(theme: Theme | undefined): Theme {
  return { ...(theme ?? {}), colors: activeColors(theme) } as Theme;
}

// Map the active palette onto CSS custom properties on :root so a single swap reskins
// everything (and re-applying on a light/dark change restyles live). Mirrors
// ThemeResolver.swift (tokens -> Color/Font).
export function applyTheme(theme: Theme | undefined, root: HTMLElement = document.documentElement) {
  const colors = activeColors(theme);
  for (const [name, value] of Object.entries(colors)) {
    root.style.setProperty(`--c-${name}`, value);
  }
  if (theme?.spacing != null) root.style.setProperty("--spacing", `${theme.spacing}px`);
  if (theme?.radius != null) root.style.setProperty("--radius", `${theme.radius}px`);
  const family = theme?.fonts?.family;
  if (typeof family === "string") root.style.setProperty("--font-family", family);
  // Let the UA theme form controls, scrollbars, and the status bar to match.
  root.style.colorScheme = prefersDark() ? "dark" : "light";
}

// Font role token -> CSS size/line-height. Roles match iOS Dynamic Type names.
const FONT_SIZES: Record<string, string> = {
  largeTitle: "34px",
  title: "28px",
  title2: "22px",
  title3: "20px",
  headline: "17px",
  body: "17px",
  subhead: "15px",
  caption: "13px",
  caption2: "11px",
};

export function fontSize(role: string | undefined): string | undefined {
  if (!role) return undefined;
  return FONT_SIZES[role];
}

const FONT_WEIGHTS: Record<string, string> = {
  regular: "400",
  medium: "500",
  semibold: "600",
  bold: "700",
  headline: "600",
};

export function fontWeight(role: string | undefined, weight: string | undefined): string | undefined {
  if (weight && FONT_WEIGHTS[weight]) return FONT_WEIGHTS[weight];
  if (role && FONT_WEIGHTS[role]) return FONT_WEIGHTS[role];
  return undefined;
}
