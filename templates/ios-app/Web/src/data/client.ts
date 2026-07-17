import type { Manifest } from "../sdui/manifest";

// Dev-only fallbacks so pages preview without a running server.
const DEV_SAMPLES: Record<string, string> = {
  "/__APP_NAME_LOWER__": "/__APP_NAME_LOWER__-sample.json",
  "/screen/bigquery": "/bigquery-sample.json",
  "/screen/afm": "/afm-sample.json",
  "/screen/afm48": "/afm48-sample.json",
  "/screen/config": "/config-sample.json",
  "/screen/balances": "/balances-sample.json",
  "/screen/smarthome": "/smarthome-sample.json",
  "/screen/bqtables": "/bqtables-sample.json",
  "/screen/logs": "/logs-sample.json",
  "/screen/repos": "/repos-sample.json",
  "/screen/schedrunner": "/schedrunner-sample.json",
  "/screen/schedlogs": "/schedlogs-sample.json",
  "/screen/docs": "/docs-sample.json",
};

// Fetch a manifest from any server path (the main __APP_NAME_LOWER__ or a sub-page).
// Served same-origin (the Vapor server also hosts this bundle), so no CORS.
export async function fetchManifestAt(path: string, query = "", signal?: AbortSignal): Promise<Manifest> {
  try {
    const res = await fetch(`${path}${query}`, {
      headers: { Accept: "application/json" },
      signal,
    });
    if (!res.ok) throw new Error(`${path} returned ${res.status}`);
    return (await res.json()) as Manifest;
  } catch (err) {
    if (import.meta.env.DEV && DEV_SAMPLES[path]) {
      const res = await fetch(DEV_SAMPLES[path], { signal });
      if (res.ok) return (await res.json()) as Manifest;
    }
    throw err;
  }
}

export function fetchManifest(query = "", signal?: AbortSignal): Promise<Manifest> {
  return fetchManifestAt("/__APP_NAME_LOWER__", query, signal);
}

// POST JSON to a server path (e.g. submitting config edits). Returns the response
// body if any.
export async function postJSON(path: string, body: unknown): Promise<unknown> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} returned ${res.status}`);
  return res.json().catch(() => null);
}
