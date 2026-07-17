// Device location + timezone for the weather/time. The phone knows these far more
// reliably than server-side IP geolocation, so the client sends them to /dashboard.

const COORDS_KEY = "geo.coords.v1";

export interface Coords {
  lat: number;
  lon: number;
}

/** IANA timezone of this device, e.g. "America/New_York". No permission needed. */
export function deviceTimezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

export function loadCoords(): Coords | null {
  try {
    const raw = localStorage.getItem(COORDS_KEY);
    return raw ? (JSON.parse(raw) as Coords) : null;
  } catch {
    return null;
  }
}

function saveCoords(c: Coords) {
  try {
    localStorage.setItem(COORDS_KEY, JSON.stringify(c));
  } catch {
    /* non-fatal */
  }
}

const WEATHER_LOC_KEY = "pref.weatherLoc";

/** Weather location mode: "home" (server/home location) or "current" (this device). */
export function weatherLocPref(): "home" | "current" {
  try {
    return JSON.parse(localStorage.getItem(WEATHER_LOC_KEY) || '"current"') === "home" ? "home" : "current";
  } catch {
    return "current";
  }
}

/** Build the query string sent to /dashboard from device tz + cached coords, honoring
 *  the Home/Current weather toggle: Home sends ?loc=home (no coords → server location). */
export function locationQuery(): string {
  const params = new URLSearchParams();
  const tz = deviceTimezone();
  if (tz) params.set("tz", tz);
  if (weatherLocPref() === "home") {
    params.set("loc", "home");
  } else {
    const coords = loadCoords();
    if (coords) {
      params.set("lat", String(coords.lat));
      params.set("lon", String(coords.lon));
    }
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

/**
 * Ask the browser for GPS coordinates (prompts once). On success caches them and
 * invokes `onUpdate` so the dashboard can refresh with the precise location.
 */
export function requestGeolocation(onUpdate: () => void) {
  if (!("geolocation" in navigator)) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const next: Coords = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      const prev = loadCoords();
      saveCoords(next);
      // Refresh only if this is new/meaningfully different, to avoid a loop.
      if (!prev || Math.abs(prev.lat - next.lat) > 0.01 || Math.abs(prev.lon - next.lon) > 0.01) {
        onUpdate();
      }
    },
    () => {
      /* denied/unavailable: server falls back to configured/IP location */
    },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 6 * 60 * 60 * 1000 },
  );
}
