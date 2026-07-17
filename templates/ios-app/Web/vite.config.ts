import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// Build stamp surfaced in the UI (drawer footer) so you can tell at a glance which
// bundle is loaded. setup_server.sh passes APP_BUILD=<git sha>; falls back to a
// timestamp for local builds.
const APP_BUILD = process.env.APP_BUILD || new Date().toISOString().slice(0, 16).replace("T", " ");
// When the bundle was built (ISO). Shown as a human date/time under the version line
// so it's obvious how old the loaded build is. setup_server.sh may pass APP_BUILD_TIME.
const APP_BUILD_TIME = process.env.APP_BUILD_TIME || new Date().toISOString();

// Release channel: "stable" (default) or "next". setup_server.sh passes APP_CHANNEL. The "next"
// channel is served from a distinct origin (https://$TS_HOST:8443/), so it installs as a SEPARATE
// PWA — we brand it ("__APP_NAME__ Next", distinct theme) so the two home-screen apps are tellable
// apart and __APP_CHANNEL__ lets the UI show a "NEXT" marker.
const APP_CHANNEL = (process.env.APP_CHANNEL || "stable").toLowerCase();
const IS_NEXT = APP_CHANNEL === "next";
const PWA_NAME = IS_NEXT ? "__APP_NAME__ Next" : "__APP_NAME__";
const PWA_THEME = IS_NEXT ? "#1a1030" : "#0b1020";   // next skews purple so it reads differently

// The PWA service worker uses a versioned (revisioned) precache for the app shell,
// and NETWORK-FIRST for the manifest endpoint so a server change shows up on next open
// while still working offline from the last cached response. skipWaiting/clientsClaim
// guarantee the newest shell takes over rather than serving a stale bundle forever.
export default defineConfig({
  define: {
    __APP_BUILD__: JSON.stringify(APP_BUILD),
    __APP_BUILD_TIME__: JSON.stringify(APP_BUILD_TIME),
    __APP_CHANNEL__: JSON.stringify(APP_CHANNEL),
  },
  server: {
    // Local dev: proxy the manifest + health to the Vapor server — stable's :8080 by default,
    // next's :8081 when APP_CHANNEL=next (or set DEV_PROXY_PORT explicitly).
    proxy: {
      "/__APP_NAME_LOWER__": {
        target: `http://localhost:${process.env.DEV_PROXY_PORT || (IS_NEXT ? "8081" : "8080")}`,
        // The "/__APP_NAME_LOWER__" key is a PREFIX match, so it also catches the static dev
        // fixture "/__APP_NAME_LOWER__-sample.json". Let Vite serve any *-sample.json file itself
        // instead of proxying it — otherwise, with no backend (CI e2e / offline dev), the
        // client's sample fallback gets a 500 and the app never boots past the loader.
        bypass: (req) => (req.url && req.url.includes("-sample.json") ? req.url : undefined),
      },
      "/healthz": `http://localhost:${process.env.DEV_PROXY_PORT || (IS_NEXT ? "8081" : "8080")}`,
    },
  },
  plugins: [
    // iOS takes the home-screen label from apple-mobile-web-app-title in index.html, which
    // OVERRIDES the PWA manifest name — so the next channel must rewrite it or both installed
    // apps would be labeled "__APP_NAME__" and be indistinguishable. "Next" keeps the label short
    // enough to show in full under the icon.
    {
      name: "channel-branding",
      transformIndexHtml(html: string) {
        if (!IS_NEXT) return html;
        return html
          .replace('<meta name="apple-mobile-web-app-title" content="__APP_NAME__" />',
                   '<meta name="apple-mobile-web-app-title" content="Next" />')
          .replace("<title>__APP_NAME__</title>", "<title>__APP_NAME__ Next</title>");
      },
    },
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/apple-touch-icon.png", "icons/favicon.svg"],
      manifest: {
        name: PWA_NAME,
        short_name: PWA_NAME,
        description: "Personal server-driven __APP_NAME_LOWER__",
        display: "standalone",
        background_color: "#0b1020",
        theme_color: PWA_THEME,
        start_url: "/",
        scope: "/",
        icons: [
          { src: "icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icons/icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,ico}"],
        skipWaiting: true,
        clientsClaim: true,
        navigateFallback: "/index.html",
        // Don't apply the SPA navigation fallback to /app/ — that path serves the native
        // iOS OTA install page + manifest.plist/IPA (Deploy/build_and_install_ota.sh), which
        // must reach the server, not the __APP_NAME_LOWER__ shell, even when the PWA SW is installed.
        navigateFallbackDenylist: [/^\/app\//],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname === "/__APP_NAME_LOWER__",
            handler: "NetworkFirst",
            options: {
              cacheName: "manifest",
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 4, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
          {
            // Tab pages incl. the Activity location data — cache aggressively so they
            // open instantly and work offline. maxEntries comfortably exceeds the crawled +
            // pinned page count (the deep crawl can reach MAX_CRAWL=400 sub-pages) so the
            // background prefetch never evicts a page it just cached.
            urlPattern: ({ url }) => url.pathname.startsWith("/screen/"),
            handler: "NetworkFirst",
            options: {
              cacheName: "pages",
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 400, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
          {
            // Map tiles (CARTO/OSM) — keep recently-viewed areas available offline.
            urlPattern: ({ url }) => url.hostname.endsWith("basemaps.cartocdn.com"),
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "map-tiles",
              expiration: { maxEntries: 500, maxAgeSeconds: 60 * 60 * 24 * 14 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // Leaflet from the CDN — immutable, cache long.
            urlPattern: ({ url }) => url.hostname === "unpkg.com",
            handler: "CacheFirst",
            options: {
              cacheName: "leaflet",
              expiration: { maxEntries: 8, maxAgeSeconds: 60 * 60 * 24 * 90 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
});
