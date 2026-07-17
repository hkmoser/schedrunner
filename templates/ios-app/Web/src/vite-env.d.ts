/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

// Build stamp injected at build time (git short SHA, else build timestamp) so the
// loaded bundle version is visible in the UI — see vite.config.ts `define`.
declare const __APP_BUILD__: string;
// ISO timestamp of when this bundle was built — shown as a human date/time under the
// version line in the drawer footer.
declare const __APP_BUILD_TIME__: string;
// Release channel: "stable" (default) or "next" — used to show a NEXT marker in the app bar.
declare const __APP_CHANNEL__: string;
