import type { Manifest } from "./manifest";
import { SUPPORTED_SCHEMA_MAJOR } from "./manifest";

export interface CompatResult {
  ok: boolean;
  reason?: "tooNew" | "malformed";
}

// Guard rendering so a future server change can never brick an installed shell.
export function checkCompat(manifest: unknown): CompatResult {
  if (manifest == null || typeof manifest !== "object") return { ok: false, reason: "malformed" };
  const m = manifest as Partial<Manifest>;
  if (typeof m.schemaVersion !== "number" || m.screen == null) return { ok: false, reason: "malformed" };
  if (m.schemaVersion > SUPPORTED_SCHEMA_MAJOR) return { ok: false, reason: "tooNew" };
  return { ok: true };
}
