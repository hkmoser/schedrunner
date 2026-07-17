"""
Experimental smart alert module for AFM.

Sends ONLY to new Pushcut channels  →  "AFM Smart e16" / "AFM Smart 12m"
Never touches existing channels or state files.

Integration (zero change to existing behavior):
  Add a single line at the end of afm_live.py, after the existing import:
      import lib_bq_smartalerts
  Or run standalone:
      python lib_bq_smartalerts.py
"""

import datetime
import json
import math
import os
import time

import pandas as pd
import requests
from google.cloud import bigquery

# ── Config ───────────────────────────────────────────────────────────────────

_API_KEY = "QFNjvttld5Fem3eor-5pd"
_PROJECT = "ecstatic-pod-443723-f6"
_DATASET = "home_afm"

_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_state.json")

# Pushcut channel prefix — all new, never overlap with existing "AFM hm/full" channels
_CHAN_PREFIX = "AFM%20Smart"

# Battery level thresholds that trigger a standalone alert (downward crossings only)
_BATTERY_WARN_LEVELS = [0.50, 0.20, 0.10]

# Minimum seconds a device must remain stopped before an "Arrived" alert fires.
# Stops shorter than this (traffic lights, brief parking) are silently absorbed
# into the ongoing moving segment.
_MIN_ARRIVAL_SECS = 720  # 12 minutes

# All mvmt_type values that mean "in motion". Anything not in this set is treated
# as stationary. Add new values here if the BQ view introduces new movement codes.
_MOVING_TYPES = {"Moving", "DR", "WK"}

# Per-channel toggle keys in dashboard_config (value='true'/'false').
# Records are created automatically with value='true' on first run.
# Smart channel gating is implemented below; change channel records are
# created here as a central registry but gating lives in lib_bq_changealerts.py.
_ALERT_CONFIG_KEYS = {
    # Smart alerts (AFM Smart *)
    "alerts.smart_channel_e16": "AFM Smart e16 Pushcut channel",
    "alerts.smart_channel_12m": "AFM Smart 12m Pushcut channel",
    # Change alerts (AFM hm / full *)
    "alerts.change_channel_hm_e16":           "AFM hm e16 Pushcut channel",
    "alerts.change_channel_hm_12m":           "AFM hm 12m Pushcut channel",
    "alerts.change_channel_full_e16":         "AFM full e16 Pushcut channel",
    "alerts.change_channel_full_12m":         "AFM full 12m Pushcut channel",
    "alerts.change_channel_full_e16_changes": "AFM full e16 changes Pushcut channel",
    "alerts.change_channel_full_12m_changes": "AFM full 12m changes Pushcut channel",
}

# OSM reverse-geocode cache: re-fetch only when device moves > ~111 m
_OSM_CACHE_DECIMALS = 3


def _is_moving(mvmt: str) -> bool:
    return mvmt in _MOVING_TYPES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(val) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    return str(val).strip()


def _fmt_num(val) -> str:
    """Round a numeric value to at most 2 decimal places for display.
    Falls back to the raw string if val is not a plain number (e.g. '0.3 mi')."""
    s = _norm(val)
    try:
        n = float(s)
        if not math.isfinite(n):
            return s
        return f"{round(n, 2):g}"
    except (ValueError, TypeError):
        return s


def _elapsed(since_iso: str | None, now: datetime.datetime) -> str:
    if not since_iso:
        return "?"
    try:
        delta = now - datetime.datetime.fromisoformat(since_iso)
        total = int(delta.total_seconds())
        if total < 60:
            return f"{total}s"
        m, s = divmod(total, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m"
    except Exception:
        return "?"


def _location_label(row: dict, osm_name: str | None = None) -> str:
    if row.get("at_hm"):
        return "home"
    # OSM reverse-geocode result takes priority — more accurate for nearby venues/hotels
    if osm_name:
        return osm_name
    poi = _norm(row.get("poi_name"))
    if poi:
        dist = _fmt_num(row.get("poi_distance"))
        return f"{poi} ({dist})" if dist else poi
    ref = _norm(row.get("cls_loc_ref"))
    ref_dist = _fmt_num(row.get("cls_loc_ref_dist"))
    if ref:
        return f"{ref} {ref_dist}".strip()
    return "unknown location"


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _osrm_driving(lat1: float, lon1: float, lat2: float, lon2: float,
                  ds: dict) -> tuple[float | None, float | None]:
    """Driving duration (min) and distance (mi) from OSRM public router.
    Result cached in ds by rounded coordinates; API only hit when position changes."""
    lat1r = round(lat1, _OSM_CACHE_DECIMALS)
    lon1r = round(lon1, _OSM_CACHE_DECIMALS)
    lat2r = round(lat2, _OSM_CACHE_DECIMALS)
    lon2r = round(lon2, _OSM_CACHE_DECIMALS)
    if (ds.get("osrm_lat1") == lat1r and ds.get("osrm_lon1") == lon1r
            and ds.get("osrm_lat2") == lat2r and ds.get("osrm_lon2") == lon2r
            and "osrm_drive_min" in ds):
        return ds.get("osrm_drive_min"), ds.get("osrm_drive_mi")
    try:
        r = requests.get(
            f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}",
            params={"overview": "false"},
            headers={"User-Agent": "findmypy-smartalerts/1.0 (joe@joemoser.com)"},
            timeout=10,
        )
        if r.status_code != 200:
            return None, None
        data = r.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None, None
        route = data["routes"][0]
        drive_min = route["duration"] / 60
        drive_mi  = route["distance"] / 1609.34
    except Exception as exc:
        print(f"[smart] OSRM error: {exc}")
        return None, None
    ds["osrm_lat1"]      = lat1r
    ds["osrm_lon1"]      = lon1r
    ds["osrm_lat2"]      = lat2r
    ds["osrm_lon2"]      = lon2r
    ds["osrm_drive_min"] = drive_min
    ds["osrm_drive_mi"]  = drive_mi
    return drive_min, drive_mi


def _battery_threshold_crossed(prev: float, curr: float) -> float | None:
    for thr in sorted(_BATTERY_WARN_LEVELS, reverse=True):
        if prev > thr >= curr:
            return thr
    return None


def _derive_battery_state(bat_status: str, bat_level: float,
                          prev_bat_level: float, prev_state: str) -> str:
    """Map Apple batteryStatus + level trend to a stable 3-state model.

    States: 'charging' | 'charged' | 'discharging'

    Apple reports batteryStatus='NotCharging' both when the device is
    genuinely unplugged AND when it is plugged in but throttling (e.g.
    Optimized Charging, or already at/near full). We disambiguate by level
    trend: any level drop at all → discharging; level flat or rising →
    remain in 'charged' (throttled) rather than falsely firing 'Unplugged'.
    """
    status_lower = (bat_status or "").lower()

    if "charg" in status_lower:          # "Charging"
        return "charging"

    if "full" in status_lower:           # "Full" — plugged in at 100 %
        return "charged"

    # "NotCharging", "Unknown", or anything else:
    # any level drop — even a single tick — means genuinely unplugged.
    if bat_level < prev_bat_level:
        return "discharging"

    # Level flat or rising. If we were previously plugged in, stay there
    # (throttled charging). If we were already discharging keep discharging
    # (brief plateau doesn't mean the device was plugged in).
    if prev_state in ("charging", "charged"):
        return "charged"

    return "discharging"


def _nominatim_lookup(lat: float, lon: float, ds: dict) -> tuple[str | None, str | None]:
    """OSM reverse-geocode at building level; returns (poi_name, address).
    Result is cached in device state by rounded position — API is only hit
    when the device moves more than ~111 m from the last cached fix."""
    if not (lat and lon):
        return None, None
    lat_r = round(lat, _OSM_CACHE_DECIMALS)
    lon_r = round(lon, _OSM_CACHE_DECIMALS)
    if (ds.get("osm_lat") == lat_r and ds.get("osm_lon") == lon_r
            and "osm_name" in ds):
        return ds.get("osm_name"), ds.get("osm_addr")
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json",
                    "zoom": 18, "addressdetails": 1},
            headers={"User-Agent": "findmypy-smartalerts/1.0 (joe@joemoser.com)"},
            timeout=10,
        )
        if r.status_code != 200:
            return None, None
        data = r.json()
        name = data.get("name") or None
        adr  = data.get("address", {})
        parts = [p for p in [
            adr.get("house_number"), adr.get("road"),
            adr.get("city") or adr.get("town") or adr.get("village"),
        ] if p]
        address = ", ".join(parts) or None
    except Exception as exc:
        print(f"[smart] Nominatim error: {exc}")
        return None, None
    ds["osm_lat"]  = lat_r
    ds["osm_lon"]  = lon_r
    ds["osm_name"] = name
    ds["osm_addr"] = address
    return name, address


def _push(dev: str, title: str, text: str, notification_id: str | None = None,
          config: dict[str, bool] | None = None) -> None:
    if config is not None and not config.get(f"alerts.smart_channel_{dev}", True):
        print(f"[smart] SKIP (disabled)  {dev}: {title}")
        return
    url = f"https://api.pushcut.io/{_API_KEY}/notifications/{_CHAN_PREFIX}%20{dev}"
    payload: dict = {"title": title, "text": text}
    if notification_id:
        payload["id"] = notification_id
    try:
        r = requests.post(url, json=payload, timeout=10)
        status = "OK" if r.status_code == 200 else f"FAILED {r.status_code}"
        print(f"[smart] {status}  {dev}: {title}")
    except Exception as exc:
        print(f"[smart] push error: {exc}")


# ── State I/O ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Dashboard config ──────────────────────────────────────────────────────────

def _ensure_config_records(client: bigquery.Client) -> None:
    """MERGE smart alert keys into dashboard_config; only inserts missing rows."""
    rows = ",\n      ".join(
        f"STRUCT('{k}' AS key, 'true' AS value, 'boolean' AS type, '{label}' AS label, 'Alerts' AS category)"
        for k, label in _ALERT_CONFIG_KEYS.items()
    )
    print("[bq] Ensuring smart alert config records ...")
    _t = time.time()
    client.query(f"""
        MERGE `{_PROJECT}.{_DATASET}.dashboard_config` T
        USING (SELECT * FROM UNNEST([{rows}])) S
        ON T.key = S.key
        WHEN NOT MATCHED THEN
            INSERT (key, value, type, label, category, updated_at)
            VALUES (S.key, S.value, S.type, S.label, S.category, CURRENT_TIMESTAMP())
    """).result(timeout=300)
    print(f"[bq] Config records done in {time.time() - _t:.2f}s")


def _load_alert_config(client: bigquery.Client) -> dict[str, bool]:
    """Return {key: enabled} for all smart alert config keys. Defaults to True if missing."""
    keys_sql = ", ".join(f"'{k}'" for k in _ALERT_CONFIG_KEYS)
    print("[bq] Loading smart alert config ...")
    _t = time.time()
    rows = list(client.query(f"""
        SELECT key, value
        FROM `{_PROJECT}.{_DATASET}.dashboard_config`
        WHERE key IN ({keys_sql})
    """).result(timeout=300))
    print(f"[bq] Alert config loaded in {time.time() - _t:.2f}s")
    config = {k: True for k in _ALERT_CONFIG_KEYS}
    for row in rows:
        config[row["key"]] = _norm(row["value"]).lower() not in ("false", "0", "no", "off")
    return config


# ── BigQuery query ────────────────────────────────────────────────────────────

def _query_latest(client: bigquery.Client) -> pd.DataFrame:
    sql = f"""
        WITH latest AS (
            SELECT deviceModel, MAX(date_time) AS max_dt
            FROM `{_PROJECT}.{_DATASET}.afm_now_ch_live_mat`
            WHERE deviceModel NOT LIKE '%iPhone16-1-4-0-iphone12mini%'
            GROUP BY deviceModel
        )
        SELECT
            CASE
                WHEN t.deviceModel LIKE '%one16%'  THEN 'e16'
                WHEN t.deviceModel LIKE '%12min%'  THEN '12m'
            END AS dev,
            t.date_time,
            t.mvmt_type,
            t.at_hm,
            t.in_nh,
            t.cls_loc_ref,
            t.cls_loc_ref_dist,
            t.twd_loc,
            t.dist_ch,
            t.dist_mph,
            t.batteryLevel,
            t.batteryStatus,
            t.battery_stat,
            t.battery_ch,
            t.battery_ch_hr,
            t.poi_name,
            t.poi_address,
            t.poi_distance,
            t.horizontalAccuracy,
            t.loc_active,
            t.time_diff_seconds,
            t.so_same_loc,
            t.latitude,
            t.longitude
        FROM `{_PROJECT}.{_DATASET}.afm_now_ch_live_mat` t
        JOIN latest
          ON t.deviceModel = latest.deviceModel
         AND t.date_time   = latest.max_dt
    """
    print("[bq] Querying latest device data for smart alerts ...")
    _t = time.time()
    j = client.query(sql)
    j.result(timeout=300)
    print(f"[bq] Latest data query done in {time.time() - _t:.2f}s")
    return j.to_dataframe()


# ── Per-device alert logic ────────────────────────────────────────────────────

def _process_device(dev: str, row: dict, ds: dict, now: datetime.datetime,
                    config: dict[str, bool],
                    e16_loc: dict | None = None) -> dict:
    """
    Evaluate one device snapshot. Fires alerts on meaningful transitions only.
    Brief stops (< _MIN_ARRIVAL_SECS) are silently absorbed into the moving segment.
    Returns the updated device-state dict.
    """
    curr_mvmt      = _norm(row.get("mvmt_type"))
    curr_at_hm     = bool(row.get("at_hm"))
    curr_bat_stat  = _norm(row.get("battery_stat"))
    curr_bat_lvl   = float(row.get("batteryLevel") or 0)
    curr_twd       = _norm(row.get("twd_loc"))
    curr_speed     = row.get("dist_mph")
    curr_dt        = _norm(row.get("date_time"))
    curr_lat       = float(row.get("latitude") or 0)
    curr_lon       = float(row.get("longitude") or 0)
    curr_accuracy  = float(row.get("horizontalAccuracy") or 50)

    osm_name, osm_addr = _nominatim_lookup(curr_lat, curr_lon, ds)
    curr_loc_label = _location_label(row, osm_name=osm_name)

    prev_mvmt      = _norm(ds.get("mvmt_type"))
    prev_loc_label = _norm(ds.get("loc_label"))
    prev_bat_stat  = _norm(ds.get("battery_stat"))
    prev_bat_lvl   = float(ds.get("battery_level") or curr_bat_lvl)

    # Segment-based notification IDs — change on every mode transition so prior
    # segments stay visible in Pushcut history. Stored in ds so they persist
    # across pipeline runs within the same segment.
    # moving_segment_id and st_segment_id are set when each segment starts.

    def _eta_line() -> str | None:
        if dev == "e16" or not e16_loc:
            return None
        try:
            lat1 = float(row.get("latitude") or 0)
            lon1 = float(row.get("longitude") or 0)
            lat2 = float(e16_loc.get("latitude") or 0)
            lon2 = float(e16_loc.get("longitude") or 0)
            mph  = float(curr_speed or 0)
            if not (lat1 and lon1 and lat2 and lon2 and mph > 0):
                return None
            dist = _haversine_miles(lat1, lon1, lat2, lon2)
            speed_eta = f"~{round(dist / mph * 60)} min to e16 ({round(dist, 2)} mi)"
            drive_min, drive_mi = _osrm_driving(lat1, lon1, lat2, lon2, ds)
            if drive_min is not None and drive_mi is not None:
                drive_eta = f"~{round(drive_min)} min drive to e16 ({round(drive_mi, 2)} mi road)"
                return f"{speed_eta}\n{drive_eta}"
            return speed_eta
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def _at_same_stop() -> bool:
        """True if current coordinates are within GPS-error tolerance of the stored stop."""
        slat = ds.get("stop_lat")
        slon = ds.get("stop_lon")
        if not (curr_lat and curr_lon and slat and slon):
            return True  # no reference — treat as same to avoid false positives
        dist_m = _haversine_miles(curr_lat, curr_lon, float(slat), float(slon)) * 1609.34
        sacc = float(ds.get("stop_accuracy") or 50)
        # 100 m floor absorbs GPS directional noise when reported accuracy is optimistic.
        return dist_m <= max(curr_accuracy, sacc, 100)

    # Set to True when arrival confirmation or loc_changed fires this run,
    # so the ongoing ST block doesn't also fire and overwrite the arrival text.
    _st_just_fired = False

    # ── Arrival debounce ──────────────────────────────────────────────────────
    # When a device stops we start a timer rather than immediately firing "Arrived".
    # ds["mvmt_type"] is intentionally NOT updated until the stop is confirmed so
    # that prev_mvmt stays "Moving" and the device can silently resume the trip.

    pending_since = ds.get("pending_arrival_since")

    if pending_since:
        if _is_moving(curr_mvmt):
            # Resumed before threshold — brief stop, trip continues uninterrupted.
            ds.pop("pending_arrival_since", None)
            ds.pop("pending_arrival_loc", None)
            # Fall through: "still moving" block below will send an update.
        else:
            delta = (now - datetime.datetime.fromisoformat(pending_since)).total_seconds()
            if delta >= _MIN_ARRIVAL_SECS:
                seg_elapsed = _elapsed(ds.get("mvmt_since"), now)
                home_tag = "🏠 Home" if curr_at_hm else "📍 Arrived"
                title = f"{home_tag} · {dev}"
                lines = [f"At {curr_loc_label}", f"Travelled for {seg_elapsed}"]
                poi_addr = osm_addr or _norm(row.get("poi_address"))
                if poi_addr and poi_addr != curr_loc_label:
                    lines.append(poi_addr)
                lines.append(f"Battery {round(curr_bat_lvl * 100)}% · {curr_bat_stat}")
                # Use the ID pre-assigned at debounce-start; generate a fallback only
                # if state was somehow missing it (e.g. first run after code deploy).
                st_seg_id = ds.get("st_segment_id") or f"smart-st-{dev}-{now.strftime('%Y%m%d%H%M')}"
                ds["st_segment_id"] = st_seg_id
                _st_just_fired      = True  # suppress same-run ongoing ST
                _push(dev, title, "\n".join(lines), notification_id=st_seg_id, config=config)
                ds.pop("pending_arrival_since", None)
                ds.pop("pending_arrival_loc",   None)
                ds["mvmt_type"]     = curr_mvmt
                ds["mvmt_since"]    = pending_since  # actual stop time, not confirmation time
                ds["loc_label"]     = curr_loc_label
                ds["stop_lat"]      = curr_lat
                ds["stop_lon"]      = curr_lon
                ds["stop_accuracy"] = curr_accuracy
            # else: threshold not met yet — do nothing, check again next run

    else:
        # ── Clean movement transitions ────────────────────────────────────────

        mvmt_changed = curr_mvmt != prev_mvmt
        loc_changed  = (
            not mvmt_changed
            and not _is_moving(curr_mvmt)
            and curr_lat and curr_lon
            and ds.get("stop_lat")
            and not _at_same_stop()
        )

        if mvmt_changed:
            seg_elapsed = _elapsed(ds.get("mvmt_since"), now)

            if _is_moving(curr_mvmt):
                # Clean departure from a confirmed stationary state.
                title = f"🚗 Moving · {dev}"
                lines = []
                if prev_loc_label and prev_loc_label != "unknown location":
                    lines.append(f"Left {prev_loc_label} after {seg_elapsed}")
                if curr_twd:
                    lines.append(f"Heading → {curr_twd}")
                try:
                    if curr_speed is not None:
                        lines.append(f"{round(float(curr_speed))} mph")
                except (ValueError, TypeError):
                    pass
                eta = _eta_line()
                if eta:
                    lines.append(eta)
                lines.append(f"Battery {round(curr_bat_lvl * 100)}% · {curr_bat_stat}")
                mv_seg_id = f"smart-mv-{dev}-{now.strftime('%Y%m%d%H%M')}"
                ds["moving_segment_id"] = mv_seg_id
                ds["last_moving_dt"]    = curr_dt
                _push(dev, title, "\n".join(lines), notification_id=mv_seg_id, config=config)
                ds["mvmt_type"]  = curr_mvmt
                ds["mvmt_since"] = now.isoformat()
                ds["loc_label"]  = curr_loc_label
                ds.pop("st_segment_id", None)   # stale ST segment ends at departure
                ds.pop("stop_lat",      None)
                ds.pop("stop_lon",      None)
                ds.pop("stop_accuracy", None)

            else:
                if _is_moving(prev_mvmt):
                    # Transitioning FROM a moving state — start debounce.
                    # Do NOT update mvmt_type yet so the device can silently resume.
                    # Pre-assign the ST segment ID NOW so the arrival notification and all
                    # subsequent ongoing ST updates share the exact same stable ID, even
                    # if the confirmation block fires on a later pipeline run.
                    ds["pending_arrival_since"] = now.isoformat()
                    ds["pending_arrival_loc"]   = curr_loc_label
                    ds["st_segment_id"]         = f"smart-st-{dev}-{now.strftime('%Y%m%d%H%M')}"
                else:
                    # Already confirmed stationary; mvmt_type label changed
                    # (e.g. "ST" → "Stationary") but it's still the same stop.
                    # Carry forward the existing segment without re-debouncing.
                    ds["mvmt_type"] = curr_mvmt

        elif loc_changed:
            seg_elapsed = _elapsed(ds.get("mvmt_since"), now)
            title = f"📍 Now at {curr_loc_label} · {dev}"
            lines = []
            if prev_loc_label and prev_loc_label != "unknown location":
                lines.append(f"Was at {prev_loc_label} for {seg_elapsed}")
            poi_addr = osm_addr or _norm(row.get("poi_address"))
            if poi_addr and poi_addr != curr_loc_label:
                lines.append(poi_addr)
            lines.append(f"Battery {round(curr_bat_lvl * 100)}% · {curr_bat_stat}")
            _push(dev, title, "\n".join(lines), config=config)
            _st_just_fired      = True  # suppress same-run ongoing ST
            ds["st_segment_id"] = f"smart-st-{dev}-{now.strftime('%Y%m%d%H%M')}"
            ds["loc_label"]        = curr_loc_label
            ds["stop_lat"]         = curr_lat
            ds["stop_lon"]         = curr_lon
            ds["stop_accuracy"]    = curr_accuracy
            ds["mvmt_since"]       = now.isoformat()

    # ── Ongoing movement updates (every new data point while moving) ──────────
    # Runs regardless of pending state — covers the "resumed after brief stop" case.
    # Same notification_id replaces the prior moving alert in Pushcut.

    if _is_moving(curr_mvmt) and curr_dt and curr_dt != _norm(ds.get("last_moving_dt")):
        elapsed = _elapsed(ds.get("mvmt_since"), now)
        title = f"🚗 {elapsed} moving · {dev}"
        lines = []
        if curr_twd:
            lines.append(f"→ {curr_twd}")
        try:
            mph = round(float(curr_speed)) if curr_speed is not None else None
            if mph:
                lines.append(f"{mph} mph")
        except (ValueError, TypeError):
            pass
        eta = _eta_line()
        if eta:
            lines.append(eta)
        lines.append(f"Near {curr_loc_label}")
        lines.append(f"Battery {round(curr_bat_lvl * 100)}% · {curr_bat_stat}")
        mv_seg_id = ds.get("moving_segment_id", f"smart-mv-{dev}-0")
        _push(dev, title, "\n".join(lines), notification_id=mv_seg_id, config=config)
        ds["last_moving_dt"] = curr_dt

    # ── Ongoing stationary updates (cumulative time at location) ─────────────
    # Fires on every new data point while confirmed stationary (mvmt_type == ST).
    # Uses the segment ID from arrival confirmation so it updates in-place and
    # remains a distinct notification from the preceding moving segment.

    st_seg_id = ds.get("st_segment_id")
    if (st_seg_id
            and not _is_moving(curr_mvmt)
            and not ds.get("pending_arrival_since")
            and not _st_just_fired):
        elapsed = _elapsed(ds.get("mvmt_since"), now)
        home_tag = "🏠" if curr_at_hm else "📍"
        title = f"{home_tag} {curr_loc_label} · {elapsed} · {dev}"
        lines = [f"For {elapsed}", f"Battery {round(curr_bat_lvl * 100)}% · {curr_bat_stat}"]
        _push(dev, title, "\n".join(lines), notification_id=st_seg_id, config=config)
        # Advance reference point to prevent GPS drift accumulation over long stops.
        ds["stop_lat"]      = curr_lat
        ds["stop_lon"]      = curr_lon
        ds["stop_accuracy"] = curr_accuracy

    # ── Battery status transition (3-state model) ────────────────────────────
    # Derive a stable state from Apple's raw status + level trend to avoid
    # false 'Unplugged' alerts during throttled charging (NotCharging at high %).

    prev_battery_state = _norm(ds.get("battery_state"))
    curr_battery_state = _derive_battery_state(
        _norm(row.get("batteryStatus")), curr_bat_lvl, prev_bat_lvl, prev_battery_state
    )

    if curr_battery_state != prev_battery_state and prev_battery_state:
        seg_elapsed = _elapsed(ds.get("bat_since"), now)
        if curr_battery_state == "charging":
            title = f"🔋 Charging · {dev}"
            text  = (f"Battery {round(curr_bat_lvl * 100)}%\n"
                     f"At {curr_loc_label}\n"
                     f"Was unplugged for {seg_elapsed}")
        elif curr_battery_state == "charged":
            title = f"🔋 Charged · {dev}"
            text  = (f"Battery {round(curr_bat_lvl * 100)}%\n"
                     f"At {curr_loc_label}\n"
                     f"Finished charging after {seg_elapsed}")
        else:  # discharging
            title = f"🔌 Unplugged · {dev}"
            text  = (f"Battery {round(curr_bat_lvl * 100)}%\n"
                     f"At {curr_loc_label}\n"
                     f"Was charging for {seg_elapsed}")
        _push(dev, title, text, notification_id=f"smart-bat-stat-{dev}", config=config)
        ds["bat_since"] = now.isoformat()

    ds["battery_state"] = curr_battery_state
    ds["battery_stat"]  = curr_bat_stat  # keep raw value for changealerts compatibility

    # ── Battery level threshold warnings ─────────────────────────────────────

    thr = _battery_threshold_crossed(prev_bat_lvl, curr_bat_lvl)
    if thr is not None:
        _push(dev, f"⚠️ Battery {round(thr * 100)}% · {dev}",
              f"Down to {round(curr_bat_lvl * 100)}%\n{curr_bat_stat}\nAt {curr_loc_label}",
              notification_id=f"smart-bat-lvl-{dev}", config=config)

    # ── Persist defaults (first run only) ─────────────────────────────────────

    ds.setdefault("mvmt_type",     curr_mvmt)
    ds.setdefault("mvmt_since",    now.isoformat())
    ds.setdefault("loc_label",     curr_loc_label)
    ds.setdefault("stop_lat",      curr_lat)
    ds.setdefault("stop_lon",      curr_lon)
    ds.setdefault("stop_accuracy", curr_accuracy)
    ds.setdefault("battery_state", curr_battery_state)
    ds.setdefault("battery_stat",  curr_bat_stat)
    ds.setdefault("bat_since",     now.isoformat())
    ds["battery_level"] = curr_bat_lvl
    # Ensure an ST segment exists for devices that were already stationary on first run
    # (empty state) or after a state reset — without this the ongoing ST block never fires.
    if not _is_moving(curr_mvmt) and not ds.get("pending_arrival_since"):
        ds.setdefault("st_segment_id", f"smart-st-{dev}-{now.strftime('%Y%m%d%H%M')}")

    return ds


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    now = datetime.datetime.now()
    print(f"[smart] {now.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        client = bigquery.Client(project=_PROJECT)
        try:
            _ensure_config_records(client)
        except Exception as exc:
            print(f"[smart] config MERGE skipped: {exc}")
        config = _load_alert_config(client)
        df = _query_latest(client)
    except Exception as exc:
        print(f"[smart] BQ error: {exc}")
        return

    state = _load_state()

    # Extract e16's current lat/lon once so any moving device can compute ETA to it.
    e16_rows = df[df["dev"] == "e16"]
    e16_loc: dict | None = None
    if not e16_rows.empty:
        r = e16_rows.iloc[0]
        if r.get("latitude") and r.get("longitude"):
            e16_loc = {"latitude": float(r["latitude"]), "longitude": float(r["longitude"])}

    for _, row in df.iterrows():
        dev = _norm(row.get("dev"))
        if not dev:
            continue
        state[dev] = _process_device(dev, row.to_dict(), state.get(dev, {}), now,
                                     config=config, e16_loc=e16_loc)

    _save_state(state)
    print("[smart] done")


try:
    run()
except Exception as _exc:
    _msg = f"[smart] unhandled error: {type(_exc).__name__}: {_exc}"
    print(_msg)
    try:
        import requests as _req
        _req.post(
            f"https://api.pushcut.io/{_API_KEY}/notifications/AFM%20health",
            json={"title": "AFM stat", "text": _msg[:500]},
            timeout=10,
        )
    except Exception:
        pass
