import type { Action } from "./manifest";
import type { FormItem } from "./form";

/** Result of a submit POST. `polling: true` means the server started an async job — the
 *  client should show "Building…" and keep refreshing the page rather than "✓ Done". */
export interface SubmitResult { ok: boolean; polling?: boolean }

export interface ActionContext {
  refresh: () => void;
  setPref: (key: string, value: unknown) => void;
  navigate: (target: string) => void;
  // Returns whether the POST succeeded so the button can show progress → done/failed. Does NOT
  // refresh — the button refreshes after showing "done" so the result is visible first.
  submit: (url: string, items: FormItem[]) => Promise<SubmitResult> | void;
  focus: (key: string, index: number) => void;
  // iOS-native-only capabilities (e.g. Live Activities). On the web PWA there's nothing to do
  // but tell the user where it lives, so the button isn't a silent no-op.
  notifyNativeOnly?: (feature: string) => void;
}

// Declarative action vocabulary. Unknown action types are no-ops by design.
export function runAction(action: Action | undefined, ctx: ActionContext) {
  if (!action) return;
  switch (action.type) {
    case "refresh":
      ctx.refresh();
      break;
    case "openURL":
      if (action.url) window.open(action.url, "_blank", "noopener");
      break;
    case "setPref":
      if (action.key) ctx.setPref(action.key, action.value ?? null);
      break;
    case "navigate":
      // Push a server-provided page (url), or pop with screenId/url "back".
      if (action.screenId === "back" || action.url === "back") ctx.navigate("back");
      else if (action.url) ctx.navigate(action.url);
      break;
    case "focus":
      // Highlight a segment on a registered map (key = mapId, value = index).
      ctx.focus(action.key ?? "activity", Number(action.value ?? 0));
      break;
    case "liveActivity":
      // Live Activities are an iOS-native capability — the web PWA can't present one. Explain
      // rather than doing nothing, so the button reads as intentional, not broken.
      ctx.notifyNativeOnly?.("Live Activities");
      break;
    case "none":
    default:
      break;
  }
}
