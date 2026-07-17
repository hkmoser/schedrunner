"""BigQuery sidecar for the __APP_NAME_LOWER__.

Runs queries against BigQuery using the Mac mini's existing Application Default
Credentials (the same auth your other tooling uses). No credentials live here.

Endpoints (localhost only):
  GET  /healthz            liveness
  GET  /query              run BQ_QUERY (generic demo/table)
  GET  /afm                Activity: group the selected device's Find My fixes into
                           stay/move segments + a recent path for the map (?hours=, 24h default)
  GET  /afm_raw            the raw fix-history ('transaction') table for AFM over the
                           window as columns/rows (?hours=, 48h default)
  GET  /config             read the key/value config table (creates it if missing;
                           seeds an 'afm_device' dropdown of locatable devices)
  POST /config             upsert config rows  {"items":[{key,value,type,label}]}
  GET  /balances           current account balances (budget-sheet snapshot table, else the
                           YNAB-synced table), totalled with assets vs liabilities; also
                           carries the net-worth-over-time chart (ynab_balances_history)
  GET  /budget             avg monthly spending per category over the last 12 full months
                           (ynab_transactions), grouped into buckets with subtotals + range
  GET  /smarthome          summarize the smart-home event log (status/last-update +
                           recent events) the ha-events service syncs to Google Drive
  GET  /logs               tail the Mac's ~/log directory (LOG_DIR)
  GET  /repos              git + deploy status of every repo under ~/Dropbox/Source (REPOS_DIR)
  GET  /schedrunner        summarize schedrunner's scheduled-job status (SCHEDRUNNER_DIR /
                           SCHEDRUNNER_STATUS), falling back to its latest log
  GET  /schedlogs          per-script status from schedrunner's log files — last-run age,
                           duration, OK/FAILED, output snippet (SCHEDRUNNER_LOG_DIR)
  GET  /docs?path=         Markdown browser over Google Drive /Private (DOCS_DIR)
  GET  /bqtables?table=    list the BQ dataset's tables, or one table's field structure

Config via environment:
  BQ_DATASET        "project.dataset" (required)
  BQ_PROJECT        billing project (default: derived from BQ_DATASET)
  BQ_AFM_HISTORY    fix-history table (default: {BQ_DATASET}.afm_latest_live)
  BQ_AFM_DEVICE     default tracked deviceName (default: "iPhone 12 mini")
  BQ_AFM_MOVE_M     meters of movement that starts a new segment (default: 150)
  BQ_CONFIG_TABLE   override config table id (default: {BQ_DATASET}.__APP_NAME_LOWER___config)
  BQ_CONFIG_CREATE  "1" to create the config table if missing (default: 1)
  BQ_BALANCES_TABLE balances snapshot table (default: {BQ_DATASET}.__APP_NAME_LOWER___balances)
  BQ_YNAB_TABLE     YNAB balances table (default: {BQ_DATASET}.ynab_balances)
  BQ_YNAB_MILLIUNITS "1" if balances are stored in YNAB milliunits (default: 0)
  BQ_QUERY/BQ_TITLE/BQ_MAX_ROWS   the generic /query endpoint
  BQ_BILLING_TABLE  GCP billing export table id (e.g. project.dataset.gcp_billing_export_v1_...)
  SMARTHOME_DIR     Drive-synced 'Smart Home Events' folder (auto-detected if unset)
  BQ_SIDECAR_PORT   listen port (default 8099)
"""
import json
import os
import datetime
import decimal
import re as _re
import time as _time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_QUERY = (
    "SELECT name, SUM(number) AS total "
    "FROM `bigquery-public-data.usa_names.usa_1910_2013` "
    "WHERE state = 'CA' GROUP BY name ORDER BY total DESC LIMIT 10"
)

CONFIG_SCHEMA_TYPES = ("string", "int", "float", "bool", "enum")
TS_TYPES = ("TIMESTAMP", "DATETIME", "DATE")
NUMERIC_TYPES = ("INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC")


def _project():
    # Explicit BQ_PROJECT, else the project segment of BQ_DATASET ("project.dataset"),
    # else let the client fall back to ADC's default (may be unset).
    p = os.environ.get("BQ_PROJECT")
    if p:
        return p
    ds = os.environ.get("BQ_DATASET", "")
    return ds.split(".")[0] if "." in ds else (ds or None)


def _client():
    from google.cloud import bigquery
    return bigquery.Client(project=_project())


def _coerce(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    return str(value)


def _dataset():
    ds = os.environ.get("BQ_DATASET")
    if not ds:
        raise RuntimeError("BQ_DATASET is not set (expected 'project.dataset')")
    return ds


def _afm_table():
    return os.environ.get("BQ_AFM_TABLE") or f"{_dataset()}.afm_now_live_mat"


# Preferred timestamp column names when one isn't pinned via BQ_AFM_TS.
TS_PREFERRED = ("date_time", "created_at", "timestamp", "datetime", "ts", "time")


def _config_table():
    return os.environ.get("BQ_CONFIG_TABLE") or f"{_dataset()}.__APP_NAME_LOWER___config"


# ---------------------------------------------------------------- /query

def run_query():
    client = _client()
    query = os.environ.get("BQ_QUERY", DEFAULT_QUERY)
    max_rows = int(os.environ.get("BQ_MAX_ROWS", "100"))
    result = client.query(query).result(timeout=30)
    columns = [f.name for f in result.schema]
    rows = []
    for i, row in enumerate(result):
        if i >= max_rows:
            break
        rows.append([_coerce(v) for v in row.values()])
    now = datetime.datetime.now().strftime("%-I:%M %p")
    return {
        "title": os.environ.get("BQ_TITLE", "BigQuery"),
        "subtitleFormatted": f"{client.project} · {len(rows)} rows · as of {now}",
        "columns": columns, "rows": rows, "rowCount": len(rows),
    }


# ---------------------------------------------------------------- /afm

import json as _json
import urllib.request as _urlreq
import urllib.parse as _urlparse

_geo_cache = {}


def _int_arg(qs, name, default):
    """Read a single integer query-string arg, clamped sane; fall back on bad input."""
    try:
        return max(1, min(168, int(qs.get(name, [default])[0])))
    except (TypeError, ValueError):
        return default


def _maps_url(lat, lon, label=None):
    """Apple Maps deep link (opens the Maps app on iOS, web elsewhere)."""
    q = _urlparse.quote(label) if label else f"{lat},{lon}"
    return f"https://maps.apple.com/?ll={lat},{lon}&q={q}"


def _reverse_geocode(lat, lon):
    """Coordinates -> short place label via BigDataCloud's free keyless endpoint."""
    key = f"{round(lat, 3)},{round(lon, 3)}"
    if key in _geo_cache:
        return _geo_cache[key]
    label = None
    try:
        url = ("https://api.bigdatacloud.net/data/reverse-geocode-client"
               f"?latitude={lat}&longitude={lon}&localityLanguage=en")
        with _urlreq.urlopen(url, timeout=8) as r:
            d = _json.load(r)
        city = d.get("city") or d.get("locality") or ""
        region = d.get("principalSubdivisionCode") or d.get("principalSubdivision") or ""
        region = region.split("-")[-1] if region else ""
        label = ", ".join([p for p in (city, region) if p]) or d.get("localityInfo", {}).get("administrative", [{}])[0].get("name")
    except Exception:
        label = None
    _geo_cache[key] = label
    return label


def _afm_history_table():
    return os.environ.get("BQ_AFM_HISTORY") or f"{_dataset()}.afm_latest_live"


# Default tracked device (the `deviceName` column), overridable via the Config page.
DEFAULT_DEVICE = os.environ.get("BQ_AFM_DEVICE", "iPhone 12 mini")


def _device_options(client, hours=24):
    """Locatable devices over the window (those actually reporting a position),
    most-active first, with a friendly label and a flag for the '12 mini' model."""
    hist = _afm_history_table()
    try:
        sql = f"""
            SELECT deviceName,
                   ANY_VALUE(name) AS name,
                   COUNT(*) AS n,
                   LOGICAL_OR(LOWER(deviceModel) LIKE '%12mini%') AS is12m
            FROM `{hist}`
            WHERE deviceName IS NOT NULL AND latitude IS NOT NULL
              AND TIMESTAMP_MILLIS(SAFE_CAST(timeStamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(hours)} HOUR)
            GROUP BY deviceName
            ORDER BY n DESC
        """
        out = []
        for r in client.query(sql).result(timeout=20):
            d = dict(r.items())
            name, dn = d.get("name"), d.get("deviceName")
            label = f"{name} ({dn})" if name and name != dn else dn
            out.append({"value": dn, "label": label, "n": int(d.get("n") or 0), "is12m": bool(d.get("is12m"))})
        return out
    except Exception:
        return []


def _best_default_device(opts):
    """Prefer the locatable '12 mini'-model device (the user's '12m'), else the most active."""
    for o in opts:
        if o.get("is12m"):
            return o["value"]
    return opts[0]["value"] if opts else DEFAULT_DEVICE


def _config_value(client, key, default=None):
    """Read a single value from the config table (used to pick the Activity device)."""
    from google.cloud import bigquery
    try:
        table_id = _config_table()
        sql = f"SELECT value FROM `{table_id}` WHERE key = @key LIMIT 1"
        cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("key", "STRING", key)])
        rows = list(client.query(sql, job_config=cfg).result(timeout=20))
        return rows[0]["value"] if rows and rows[0]["value"] else default
    except Exception:
        return default


def _config_truthy(client, key):
    """Whether a config value reads as on (1/true/yes/on, any case)."""
    return str(_config_value(client, key) or "").strip().lower() in ("1", "true", "yes", "on")


# Activity time-range filter: chip key -> label. 'today' (the past 24h) is the default.
_AFM_RANGES = [("today", "Today"), ("yesterday", "Yesterday"), ("week", "This week")]


def _afm_window(range_key, hours, now):
    """Resolve the Activity time window to (start, end, key, label). 'today' = the past
    `hours` (24); 'yesterday' = the previous calendar day; 'week' = the past 7 days. The
    end is nudged 1s past `now` so the latest fix is included."""
    soon = now + datetime.timedelta(seconds=1)
    if range_key == "yesterday":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (midnight - datetime.timedelta(days=1), midnight, "yesterday", "Yesterday")
    if range_key == "week":
        return (now - datetime.timedelta(days=7), soon, "week", "This week")
    return (now - datetime.timedelta(hours=hours), soon, "today", "Today")


def afm_segments(hours=24, range_key=None):
    """Group a device's fixes over the window into segments (stays between moves)
    and return them plus a recent path for the map. A new segment starts whenever
    the device jumps more than BQ_AFM_MOVE_M meters from the previous fix — the same
    'location changed' idea findmypy's change alerts use. The window is the Today /
    Yesterday / This-week range (`range_key`), defaulting to Today = the past `hours`."""
    from google.cloud import bigquery

    hours = int(hours)
    client = _client()
    hist = _afm_history_table()
    # The tracked device is chosen on the Config page (afm_device). Fall back to a
    # locatable device if the chosen one has no fixes (e.g. the offline 12 mini).
    opts = _device_options(client, hours)
    locatable = {o["value"] for o in opts}
    device = _config_value(client, "afm_device", None)
    if device not in locatable and opts:
        device = _best_default_device(opts)
    device = device or DEFAULT_DEVICE
    move_dist = float(os.environ.get("BQ_AFM_MOVE_M", "200"))
    charge_col = os.environ.get("BQ_AFM_CHARGE_COL", "batteryStatus")

    sql = f"""
        SELECT date_time,
               SAFE_CAST(latitude AS FLOAT64) AS lat,
               SAFE_CAST(longitude AS FLOAT64) AS lon,
               CAST(`{charge_col}` AS STRING) AS chg,
               SAFE_CAST(batteryLevel AS FLOAT64) AS lvl
        FROM `{hist}`
        WHERE deviceName = @device
        ORDER BY date_time
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("device", "STRING", device)])
    raw = list(client.query(sql, job_config=cfg).result(timeout=40))

    now_dt = datetime.datetime.now()
    win_start, win_end, range_key, range_label = _afm_window(range_key, hours, now_dt)
    pts = []
    all_rows = []
    for r in raw:
        t = _parse_dt(r["date_time"])
        if not t or t < win_start or t >= win_end:
            continue
        lat = float(r["lat"]) if r["lat"] is not None else None
        lon = float(r["lon"]) if r["lon"] is not None else None
        lvl = float(r["lvl"]) if r["lvl"] is not None else None
        if lat is not None and lon is not None:
            pts.append((t, lat, lon))
        all_rows.append((t, lat, lon, (r["chg"] or "").strip(), lvl))

    runs = _segment(pts, move_dist)
    known = _read_known_locs(client)  # label-a-stop: name recognized places

    entries, map_segments = [], []
    moving_min = stopped_min = 0
    geocodes = 0
    for idx, (cls, rpts) in enumerate(runs):
        start, end = rpts[0][0], rpts[-1][0]
        dur_min = max(0, int((end - start).total_seconds() // 60))
        if cls == "stop":
            clat = sum(p[1] for p in rpts) / len(rpts)
            clon = sum(p[2] for p in rpts) / len(rpts)
            # A saved known location wins over a reverse-geocoded city name.
            known_name = _match_known(clat, clon, known)
            if known_name:
                place = known_name
            else:
                place = (_reverse_geocode(clat, clon) if geocodes < 25 else None) or f"{clat:.4f}, {clon:.4f}"
                geocodes += 1
            stopped_min += dur_min
            # Known places get a star + warm colour; unknown stops stay neutral grey.
            color = "#ffd166" if known_name else "#9aa4c4"
            icon = "star.fill" if known_name else "mappin.circle.fill"
            entries.append({
                "_t": start, "startEpoch": int(start.timestamp()),
                "index": idx, "place": place, "category": "Stopped", "mode": "Stopped",
                "icon": icon, "categoryColor": color, "known": bool(known_name),
                "timeFormatted": _seg_time(start, end), "durationFormatted": _dur(dur_min),
                "durationMin": dur_min, "distanceFormatted": "", "mapsUrl": _maps_url(clat, clon, place),
                "labelHref": "/screen/afm_label?lat=%.6f&lon=%.6f&place=%s" % (clat, clon, _urlparse.quote(place)),
                "lat": clat, "lon": clon,
            })
            map_segments.append({"index": idx, "category": "Stopped", "color": color,
                                 "points": [[clat, clon]], "center": {"lat": clat, "lon": clon},
                                 "durationMin": dur_min})
        else:
            coords = [[p[1], p[2]] for p in rpts]
            dist_m = sum(_haversine(rpts[i - 1][1], rpts[i - 1][2], rpts[i][1], rpts[i][2]) for i in range(1, len(rpts)))
            dest = _reverse_geocode(rpts[-1][1], rpts[-1][2]) if geocodes < 25 else None
            geocodes += 1
            moving_min += dur_min
            mph = (dist_m / 1609.34) / max(dur_min / 60.0, 1 / 60.0)
            mode, icon, color = _travel_mode(mph)
            entries.append({
                "_t": start, "startEpoch": int(start.timestamp()),
                "index": idx, "place": (f"to {dest}" if dest else mode), "category": "Moving",
                "mode": mode, "icon": icon, "categoryColor": color,
                "timeFormatted": _seg_time(start, end), "durationFormatted": _dur(dur_min),
                "durationMin": dur_min,
                "distanceFormatted": (f"{dist_m / 1609:.1f} mi" if dist_m else ""),
                "lat": rpts[-1][1], "lon": rpts[-1][2],
            })
            map_segments.append({"index": idx, "category": "Moving", "color": color,
                                 "points": coords, "center": {"lat": rpts[-1][1], "lon": rpts[-1][2]}})

    # Charging changes: a plug-in needs discharging -> charging, an unplug needs
    # charging -> discharging (status value sets are configurable).
    charge_events = _charge_events(all_rows)
    entries.extend(charge_events)

    entries.sort(key=lambda e: e["_t"], reverse=True)  # most recent first
    for e in entries:
        e.pop("_t", None)

    center = map_segments[-1]["center"] if map_segments else None
    focus_idx = next((s["index"] for s in reversed(map_segments) if s["category"] == "Moving"), None)
    if focus_idx is None and map_segments:
        focus_idx = map_segments[-1]["index"]
    map_obj = {"center": center, "segments": map_segments, "focus": focus_idx}

    now_s = now_dt.strftime("%-I:%M %p")
    if entries:
        ch = f" · {len(charge_events)} charge" if charge_events else ""
        subtitle = f"{device} · {_dur(moving_min)} moving · {_dur(stopped_min)} stopped{ch} · {now_s}"
    else:
        avail = ", ".join(o["value"] for o in opts[:4]) or "none"
        subtitle = f"No fixes for {device} ({range_label.lower()}). Locatable: {avail}. Pick one on Config."

    # Today / Yesterday / This-week filter chips (server-driven, toggle via ?range=).
    ranges = [{
        "key": k, "label": lbl, "active": k == range_key,
        "labelFormatted": lbl,
        "color": "$accent" if k == range_key else "$textSecondary",
        "navHref": f"/screen/afm?range={k}",
    } for k, lbl in _AFM_RANGES]

    # Current-state block (top of the page): newest stop/move + latest battery/seen time.
    loc_entries = [e for e in entries if e.get("category") in ("Stopped", "Moving")]
    cur = loc_entries[0] if loc_entries else None
    batt = last_seen = ""
    if all_rows:
        lt, _la, _lo, _ls, llvl = all_rows[-1]
        last_seen = _ago(lt, now_dt) if lt else ""
        if llvl is not None:
            pct = int(round(llvl * 100)) if llvl <= 1.5 else int(round(llvl))
            batt = f"battery {pct}%"
    if cur:
        meta = " · ".join(b for b in (batt, (f"seen {last_seen}" if last_seen else "")) if b)
        current_state = {
            "statusFormatted": cur.get("mode") or cur.get("category") or "Located",
            "placeFormatted": cur.get("place") or "",
            "metaFormatted": meta,
            # For the widget: elapsed in the current segment + its clock-time range, and whether
            # it's a Stopped or Moving segment (so ST shows time-at-location, MV time-in-motion).
            "category": cur.get("category") or "",
            "elapsedFormatted": cur.get("durationFormatted") or "",
            "clockRangeFormatted": cur.get("timeFormatted") or "",
            # Unix start of the CURRENT segment so the widget can tick the elapsed time LIVE
            # (SwiftUI Text(_, style:.timer)) without waiting on a network refresh.
            "startedAtEpoch": cur.get("startEpoch") or 0,
            "icon": cur.get("icon") or "mappin.circle.fill",
            "color": cur.get("categoryColor") or "#9aa4c4",
            "mapsUrl": cur.get("mapsUrl", ""),
        }
    else:
        current_state = {
            "statusFormatted": "No recent location",
            "placeFormatted": device,
            "metaFormatted": f"No fixes ({range_label.lower()})",
            "icon": "questionmark.circle",
            "color": "#9aa4c4",
            "mapsUrl": "",
        }

    disabled = _config_truthy(client, "activity_widget_disabled")
    return {
        "title": f"Activity · {range_label}",
        "subtitleFormatted": subtitle,
        "device": device,
        "rangeFormatted": range_label,
        "currentState": current_state,
        "ranges": ranges,
        "map": map_obj,
        "segments": entries,
        "rowCount": len(entries),
        # Manual override for the Home Screen Activity widget: when on, the widget is forced into
        # its disabled/masked "Home Lights" mode regardless of device proximity. Rendered as a
        # switch + Apply on the Activity page; read by /devices_together.
        "widgetDisabled": {"key": "activity_widget_disabled",
                           "value": "true" if disabled else "false",
                           "type": "bool", "label": "Disable Activity widget (show Home Lights)"},
        "widgetDisabledStateFormatted": ("Widget is forced to Home Lights mode."
                                         if disabled else "Widget shows your real activity."),
    }


def _travel_mode(mph):
    """Classify a moving segment by average speed -> (label, SF Symbol, color)."""
    if mph < 6:
        return "Walking", "figure.walk", "#43d18a"
    if mph < 180:
        return "Driving", "car.fill", "#6ea8fe"
    return "Flying", "airplane", "#b388ff"


# Raw batteryStatus values meaning charging vs not. UCH/DCH (charging up / discharging)
# are derived from the batteryLevel trend, not from the status column.
CHARGING_STATUS = {v.strip() for v in os.environ.get("BQ_AFM_CHARGING_VALUES", "Charging").split(",") if v.strip()}
DISCHARGING_STATUS = {v.strip() for v in os.environ.get("BQ_AFM_DISCHARGING_VALUES", "NotCharging").split(",") if v.strip()}


def _time_only(t):
    return t.strftime("%-I:%M %p")


def _charge_events(all_rows):
    """Plug-in / unplug events combining raw batteryStatus with the battery-level
    trend (UCH = level rising, DCH = level falling):
      - Started charging: NotCharging+DCH -> Charging+UCH (status charging AND level rising)
      - Unplugged:        Charging -> NotCharging+DCH      (status not-charging AND level falling)
    The level trend is sticky across flat readings so a transition isn't missed."""
    events = []
    prev_level = None
    trend = None  # 'up' (UCH) / 'down' (DCH)
    prev_cls = None
    for (t, lat, lon, status, level) in all_rows:
        if level is not None and prev_level is not None:
            if level > prev_level:
                trend = "up"
            elif level < prev_level:
                trend = "down"
            # equal -> keep last trend
        if level is not None:
            prev_level = level

        if status in CHARGING_STATUS and trend == "up":
            cls = "chg"
        elif status in DISCHARGING_STATUS and trend == "down":
            cls = "dis"
        else:
            cls = None
        if cls is None:
            continue
        if prev_cls is not None and cls != prev_cls:
            if prev_cls == "dis" and cls == "chg":
                label, icon, color = "Started charging", "bolt.fill", "#ffd166"
            else:
                label, icon, color = "Unplugged", "powerplug.fill", "#9aa4c4"
            ev = {
                "_t": t, "category": "Charge", "mode": label, "place": "",
                "icon": icon, "categoryColor": color, "timeFormatted": _time_only(t),
                "durationFormatted": "", "durationMin": 0,
            }
            if lat is not None and lon is not None:
                ev["mapsUrl"] = _maps_url(lat, lon)
            events.append(ev)
        prev_cls = cls
    return events


def _parse_dt(s):
    try:
        return datetime.datetime.strptime(s, "%Y%m%d_%H%M%S")
    except Exception:
        return None


def _haversine(lat1, lon1, lat2, lon2):
    import math
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _segment(pts, move_dist, min_seconds=240):
    """Mark each fix stopped/moving vs the previous fix, group into runs, then merge
    sub-4-minute runs into a neighbor to absorb GPS jitter and brief stops."""
    n = len(pts)
    if n == 0:
        return []
    if n == 1:
        return [("stop", pts)]
    marks = ["stop"]
    for i in range(1, n):
        d = _haversine(pts[i - 1][1], pts[i - 1][2], pts[i][1], pts[i][2])
        marks.append("move" if d > move_dist else "stop")
    runs = []
    s = 0
    for i in range(1, n + 1):
        if i == n or marks[i] != marks[s]:
            runs.append([marks[s], pts[s:i]])
            s = i
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i in range(len(runs)):
            rpts = runs[i][1]
            if (rpts[-1][0] - rpts[0][0]).total_seconds() < min_seconds:
                if i > 0:
                    runs[i - 1][1].extend(rpts)
                    runs.pop(i)
                else:
                    runs[1][1] = rpts + runs[1][1]
                    runs.pop(0)
                changed = True
                break
    return [(c, p) for c, p in runs]


def _seg_time(start, end):
    if not start or not end:
        return ""
    s = start.astimezone()
    e = end.astimezone()
    if s.strftime("%p") == e.strftime("%p") and s.date() == e.date():
        return f"{s.strftime('%-I:%M')}–{e.strftime('%-I:%M %p')}"
    return f"{s.strftime('%-I:%M %p')} – {e.strftime('%-I:%M %p')}"


def _dur(minutes):
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _log_clock(dt, now):
    """Compact absolute timestamp for a newest-first log: time only when today, weekday +
    time within a week, else month/day + time. Both args are timezone-aware local datetimes."""
    if dt is None:
        return ""
    if dt.date() == now.date():
        return dt.strftime("%-I:%M %p")
    if (now.date() - dt.date()).days < 7:
        return dt.strftime("%a %-I:%M %p")
    return dt.strftime("%b %-d · %-I:%M %p")


def afm_log(hours=24, range_key=None):
    """A literal, newest-first log of activity EVENTS (each stop / move / charge), showing the
    minimum needed to understand each: when · what · where · duration/distance. Reuses the same
    segmentation as the Activity page (via afm_segments), just reshaped into a flat event log."""
    base = afm_segments(hours=hours, range_key=range_key)
    now = datetime.datetime.now().astimezone()
    events = []
    for e in base.get("segments", []):
        se = e.get("startEpoch")
        dt = datetime.datetime.fromtimestamp(se).astimezone() if se else None
        # Primary label: the specific mode (Stopped / Driving / Walking / Started charging), then
        # the place, then distance/duration — the least text that still explains the event.
        label = e.get("mode") or e.get("category") or "Event"
        extra = " · ".join(x for x in (e.get("distanceFormatted"), e.get("durationFormatted")) if x)
        line = " · ".join(x for x in (label, e.get("place") or "", extra) if x)
        events.append({
            "timeFormatted": _log_clock(dt, now) if dt else (e.get("timeFormatted") or ""),
            "icon": e.get("icon") or "circle.fill",
            "color": e.get("categoryColor") or "#9aa4c4",
            "lineFormatted": line,
        })
    # afm_segments already returns segments newest-first.
    range_label = base.get("rangeFormatted", "")
    ranges = [{
        "key": k, "label": lbl, "active": k == (range_key or "today"),
        "labelFormatted": lbl,
        "color": "$accent" if k == (range_key or "today") else "$textSecondary",
        "navHref": f"/screen/afm_log?range={k}",
    } for k, lbl in _AFM_RANGES]
    return {
        "title": f"Activity Log · {range_label}" if range_label else "Activity Log",
        "subtitleFormatted": base.get("subtitleFormatted", ""),
        "ranges": ranges,
        "events": events,
        "rowCount": len(events),
        "emptyFormatted": "" if events else "No activity events in this window.",
    }


# ---------------------------------------------------------------- /afm_raw

def _afm48_tabs(active):
    """Last-48 page tabs: 'Now' (the afm_now materialized view; default) and '48h history'
    (the raw fix dump). Each navigates via ?view=."""
    def tab(key, label, href):
        return {"key": key, "label": label, "navHref": href, "active": key == active,
                "color": "$accent" if key == active else "$textSecondary"}
    return [tab("now", "Now", "/screen/afm48"),
            tab("raw", "48h history", "/screen/afm48?view=raw")]


def afm_now():
    """The afm_now materialized view (current per-device snapshot) as a columns/rows
    table — the DEFAULT tab of the Last-48 page."""
    client = _client()
    table = _afm_table()
    max_rows = int(os.environ.get("BQ_AFM_NOW_MAX", "200"))
    # Newest-first when the view has a date column, else its natural order.
    queries = [
        f"SELECT * FROM `{table}` ORDER BY date_time DESC LIMIT {max_rows}",
        f"SELECT * FROM `{table}` LIMIT {max_rows}",
    ]
    result, last_exc = None, None
    for q in queries:
        try:
            result = client.query(q).result(timeout=40)
            break
        except Exception as exc:  # noqa: BLE001 - try the next query shape
            last_exc = exc
    if result is None:
        return {"title": "AFM now", "subtitleFormatted": f"No data · check {table.split('.')[-1]}",
                "tabs": _afm48_tabs("now"), "view": "now",
                "columns": [], "rows": [], "rowCount": 0,
                "error": str(last_exc) if last_exc else None}
    columns = [f.name for f in result.schema]
    rows = [[_coerce(v) for v in row.values()] for row in result]
    now_s = datetime.datetime.now().strftime("%-I:%M %p")
    return {"title": "AFM now", "subtitleFormatted": f"{table.split('.')[-1]} · {len(rows)} rows · as of {now_s}",
            "tabs": _afm48_tabs("now"), "view": "now",
            "columns": columns, "rows": rows, "rowCount": len(rows)}


def afm_transactions(hours=48):
    """The raw fix-history ('transaction') table for AFM over the last `hours`, as a
    plain columns/rows table — every column the table has, newest first. The SECOND tab
    of the Last-48 page. `timeStamp` is epoch-millis (string); we filter/sort on it and
    fall back to an unfiltered dump if that column isn't present."""
    hours = int(hours)
    client = _client()
    hist = _afm_history_table()
    max_rows = int(os.environ.get("BQ_AFM_RAW_MAX", "300"))
    window = (f"TIMESTAMP_MILLIS(SAFE_CAST(timeStamp AS INT64)) >= "
              f"TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {hours} HOUR)")
    queries = [
        f"SELECT * FROM `{hist}` WHERE {window} ORDER BY SAFE_CAST(timeStamp AS INT64) DESC LIMIT {max_rows}",
        f"SELECT * FROM `{hist}` ORDER BY date_time DESC LIMIT {max_rows}",
        f"SELECT * FROM `{hist}` LIMIT {max_rows}",
    ]
    result, last_exc = None, None
    for q in queries:
        try:
            result = client.query(q).result(timeout=40)
            break
        except Exception as exc:  # noqa: BLE001 - try the next query shape
            last_exc = exc
    if result is None:
        return {
            "title": f"AFM · last {hours}h",
            "subtitleFormatted": f"No data · check the {hist.split('.')[-1]} table",
            "tabs": _afm48_tabs("raw"), "view": "raw",
            "columns": [], "rows": [], "rowCount": 0,
            "error": str(last_exc) if last_exc else None,
        }
    columns = [f.name for f in result.schema]
    rows = [[_coerce(v) for v in row.values()] for row in result]
    now_s = datetime.datetime.now().strftime("%-I:%M %p")
    return {
        "title": f"AFM · last {hours}h",
        "subtitleFormatted": f"{hist.split('.')[-1]} · {len(rows)} rows · as of {now_s}",
        "tabs": _afm48_tabs("raw"), "view": "raw",
        "columns": columns, "rows": rows, "rowCount": len(rows),
    }


# ---------------------------------------------------------------- known locations

def _known_locs_table():
    return os.environ.get("BQ_KNOWN_LOCS_TABLE") or f"{_dataset()}.known_locs"


def _ensure_known_locs_table(client):
    """Create the known_locs table on first use (label-a-stop feature). Disable with
    BQ_KNOWN_LOCS_CREATE=0 if you'd rather provision it yourself."""
    from google.cloud import bigquery
    table_id = _known_locs_table()
    if os.environ.get("BQ_KNOWN_LOCS_CREATE", "1") != "1":
        return table_id
    schema = [
        bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("latitude", "FLOAT64"),
        bigquery.SchemaField("longitude", "FLOAT64"),
        bigquery.SchemaField("radius_m", "FLOAT64"),
        bigquery.SchemaField("updated_at", "TIMESTAMP"),
    ]
    client.create_table(bigquery.Table(table_id, schema=schema), exists_ok=True)
    return table_id


def _read_known_locs(client):
    """All saved known locations as [{name, lat, lon, radius_m}]; best-effort (returns
    [] if the table is missing or unreadable, so Activity never breaks on it)."""
    try:
        table_id = _ensure_known_locs_table(client)
        rows = client.query(
            f"SELECT name, SAFE_CAST(latitude AS FLOAT64) AS lat, "
            f"SAFE_CAST(longitude AS FLOAT64) AS lon, SAFE_CAST(radius_m AS FLOAT64) AS radius_m "
            f"FROM `{table_id}`").result(timeout=20)
        out = []
        for r in rows:
            if r["lat"] is None or r["lon"] is None:
                continue
            out.append({"name": r["name"], "lat": float(r["lat"]), "lon": float(r["lon"]),
                        "radius_m": float(r["radius_m"]) if r["radius_m"] is not None else 150.0})
        return out
    except Exception:  # noqa: BLE001
        return []


def _match_known(clat, clon, known):
    """Name of the nearest known location whose radius contains (clat, clon), else None."""
    best, best_d = None, None
    for k in known:
        d = _haversine(clat, clon, k["lat"], k["lon"])
        if d <= k.get("radius_m", 150.0) and (best_d is None or d < best_d):
            best, best_d = k["name"], d
    return best


def get_known_locs(lat=None, lon=None, place=None):
    """Label screen: a prefilled form to save a coordinate as a known location, plus the
    list of existing ones."""
    client = _client()
    _ensure_known_locs_table(client)
    locs = _read_known_locs(client)
    try:
        flat = float(lat) if lat not in (None, "") else None
        flon = float(lon) if lon not in (None, "") else None
    except (TypeError, ValueError):
        flat = flon = None
    fields = [
        {"key": "name", "value": (place or "").strip(), "type": "string", "label": "Name"},
        {"key": "latitude", "value": ("" if flat is None else f"{flat:.6f}"), "type": "float", "label": "Latitude"},
        {"key": "longitude", "value": ("" if flon is None else f"{flon:.6f}"), "type": "float", "label": "Longitude"},
        {"key": "radius_m", "value": "150", "type": "int", "label": "Radius (m)"},
    ]
    items = [{
        "name": k["name"],
        "metaFormatted": f"{k['lat']:.4f}, {k['lon']:.4f} · {int(k['radius_m'])}m",
    } for k in sorted(locs, key=lambda x: x["name"].lower())]
    subtitle = (f"Save {flat:.4f}, {flon:.4f} as a known place"
                if flat is not None else "Add a place you visit so Activity names it")
    return {
        "title": "Label location",
        "subtitleFormatted": subtitle,
        "fields": fields,
        "locs": items,
        "countFormatted": f"{len(items)} known location" + ("" if len(items) == 1 else "s"),
        "backHref": "/screen/afm",
        "rowCount": len(items),
    }


def upsert_known_loc(items):
    """Upsert a known location from submitted form items (keyed name/latitude/longitude/
    radius_m), MERGE-ing on the name. Returns the refreshed label screen."""
    from google.cloud import bigquery
    client = _client()
    table_id = _ensure_known_locs_table(client)
    d = {it.get("key"): it.get("value") for it in items if isinstance(it, dict)}
    name = (d.get("name") or "").strip()

    def _f(x, default=None):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    if not name:
        out = get_known_locs(d.get("latitude"), d.get("longitude"), name)
        out["error"] = "Name is required."
        return out
    lat, lon = _f(d.get("latitude")), _f(d.get("longitude"))
    rad = _f(d.get("radius_m"), 150.0) or 150.0
    sql = (
        f"MERGE `{table_id}` T "
        "USING (SELECT @name AS name, @lat AS latitude, @lon AS longitude, @rad AS radius_m) S "
        "ON T.name = S.name "
        "WHEN MATCHED THEN UPDATE SET latitude = S.latitude, longitude = S.longitude, "
        "radius_m = S.radius_m, updated_at = CURRENT_TIMESTAMP() "
        "WHEN NOT MATCHED THEN INSERT (name, latitude, longitude, radius_m, updated_at) "
        "VALUES (S.name, S.latitude, S.longitude, S.radius_m, CURRENT_TIMESTAMP())"
    )
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("name", "STRING", name),
        bigquery.ScalarQueryParameter("lat", "FLOAT64", lat),
        bigquery.ScalarQueryParameter("lon", "FLOAT64", lon),
        bigquery.ScalarQueryParameter("rad", "FLOAT64", rad),
    ])
    client.query(sql, job_config=cfg).result(timeout=30)
    return {"ok": True, **get_known_locs(d.get("latitude"), d.get("longitude"), name)}


# ---------------------------------------------------------------- /config

def _ensure_config_table(client):
    from google.cloud import bigquery
    table_id = _config_table()
    if os.environ.get("BQ_CONFIG_CREATE", "1") != "1":
        return table_id
    schema = [
        bigquery.SchemaField("key", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("value", "STRING"),
        bigquery.SchemaField("type", "STRING"),
        bigquery.SchemaField("label", "STRING"),
        bigquery.SchemaField("category", "STRING"),
        bigquery.SchemaField("updated_at", "TIMESTAMP"),
    ]
    client.create_table(bigquery.Table(table_id, schema=schema), exists_ok=True)
    # Migrate older tables that predate the category column.
    try:
        client.query(f"ALTER TABLE `{table_id}` ADD COLUMN IF NOT EXISTS category STRING").result(timeout=30)
    except Exception:
        pass
    return table_id


def _select_config(client, table_id):
    """Read config rows, tolerating tables that don't yet have a category column."""
    try:
        rows = client.query(f"SELECT key, value, type, label, category FROM `{table_id}`").result(timeout=30)
    except Exception:
        rows = client.query(f"SELECT key, value, type, label FROM `{table_id}`").result(timeout=30)
    return {r["key"]: dict(r.items()) for r in rows}


def _group_for(key, category):
    """Group = explicit category, else the 'group.' key prefix (naming convention),
    else 'General'."""
    if category:
        return category.strip()
    if "." in key:
        return key.split(".", 1)[0].replace("_", " ").title()
    return "General"


def _upsert_one(client, table_id, item):
    from google.cloud import bigquery
    key = (item.get("key") or "").strip()
    if not key:
        return False
    value = "" if item.get("value") is None else str(item.get("value"))
    vtype = item.get("type") or "string"
    if vtype not in CONFIG_SCHEMA_TYPES:
        vtype = "string"
    label = item.get("label") or key
    category = item.get("category")  # None => keep existing on update
    sql = (
        f"MERGE `{table_id}` T "
        "USING (SELECT @key AS key, @value AS value, @type AS type, @label AS label, @category AS category) S "
        "ON T.key = S.key "
        "WHEN MATCHED THEN UPDATE SET value = S.value, type = S.type, label = S.label, "
        "category = COALESCE(S.category, T.category), updated_at = CURRENT_TIMESTAMP() "
        "WHEN NOT MATCHED THEN INSERT (key, value, type, label, category, updated_at) "
        "VALUES (S.key, S.value, S.type, S.label, S.category, CURRENT_TIMESTAMP())"
    )
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("key", "STRING", key),
        bigquery.ScalarQueryParameter("value", "STRING", value),
        bigquery.ScalarQueryParameter("type", "STRING", vtype),
        bigquery.ScalarQueryParameter("label", "STRING", label),
        bigquery.ScalarQueryParameter("category", "STRING", category),
    ])
    client.query(sql, job_config=cfg).result(timeout=30)
    return True


def get_config():
    client = _client()
    table_id = _ensure_config_table(client)
    device_opts = _device_options(client)
    locatable = {o["value"] for o in device_opts}

    rows = _select_config(client, table_id)

    # Seed the Activity device selector, or self-heal a selection that has no
    # location data (e.g. the offline iPhone 12 mini), so Activity is never empty.
    current = (rows.get("afm_device") or {}).get("value")
    if "afm_device" not in rows or (locatable and current not in locatable):
        _upsert_one(client, table_id, {"key": "afm_device", "value": _best_default_device(device_opts),
                                       "type": "enum", "label": "Activity device", "category": "Activity"})
        rows = _select_config(client, table_id)

    # Seed the BQ-preview row-limit so it's editable on the Config page (overrides the
    # default LIMIT used by the BQ Tables Preview tab).
    if "bq_preview_limit" not in rows:
        _upsert_one(client, table_id, {"key": "bq_preview_limit", "value": "1000",
                                       "type": "int", "label": "BQ preview row limit", "category": "System"})
        rows = _select_config(client, table_id)

    items = []
    for key in sorted(rows):
        d = rows[key]
        item = {
            "key": d.get("key"),
            "value": "" if d.get("value") is None else str(d.get("value")),
            "type": d.get("type") or "string",
            "label": d.get("label") or d.get("key"),
            "group": _group_for(d.get("key") or "", d.get("category")),
        }
        if item["key"] == "afm_device":
            opts = list(device_opts)
            if item["value"] and item["value"] not in locatable:
                opts = [{"value": item["value"], "label": item["value"]}] + opts
            item["type"] = "enum"
            item["options"] = opts
        items.append(item)

    # Group items into sections (General last; alphabetical otherwise).
    by_group = {}
    for it in items:
        by_group.setdefault(it["group"], []).append(it)
    order = sorted(by_group, key=lambda g: (g == "General", g.lower()))
    groups = [{"title": g, "items": by_group[g]} for g in order]

    return {
        "title": "Config",
        "subtitleFormatted": f"{table_id.split('.')[-1]} · {len(items)} settings",
        "items": items, "groups": groups, "rowCount": len(items),
    }


def upsert_config(items):
    client = _client()
    table_id = _ensure_config_table(client)
    n = 0
    for item in items:
        if _upsert_one(client, table_id, item):
            n += 1
    return {"ok": True, "updated": n, **get_config()}


# ---------------------------------------------------------------- /balances

def _balances_table():
    return os.environ.get("BQ_BALANCES_TABLE") or f"{_dataset()}.__APP_NAME_LOWER___balances"


def _money(x):
    """Dollar amount with thousands separators; negatives render as -$1,234.56."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "$0.00"
    s = f"${abs(v):,.2f}"
    return f"-{s}" if v < 0 else s


# Column shapes we accept for a "balances"-style table, most descriptive first.
# Account name may be `account` or `name`; amount may be `balance` or `amount`;
# `as_of` / `type` are optional. NULL names are filtered in Python.
_BALANCE_SELECTS = [
    "SELECT account, SAFE_CAST(balance AS FLOAT64) AS balance, type, as_of",
    "SELECT account, SAFE_CAST(balance AS FLOAT64) AS balance, as_of",
    "SELECT account, SAFE_CAST(balance AS FLOAT64) AS balance, type",
    "SELECT account, SAFE_CAST(balance AS FLOAT64) AS balance",
    "SELECT name AS account, SAFE_CAST(balance AS FLOAT64) AS balance, type, as_of",
    "SELECT name AS account, SAFE_CAST(balance AS FLOAT64) AS balance, as_of",
    "SELECT name AS account, SAFE_CAST(balance AS FLOAT64) AS balance, type",
    "SELECT name AS account, SAFE_CAST(balance AS FLOAT64) AS balance",
    "SELECT account, SAFE_CAST(amount AS FLOAT64) AS balance",
    "SELECT name AS account, SAFE_CAST(amount AS FLOAT64) AS balance",
]


def _read_balance_rows(client, table_id):
    """Read account/balance rows from a table, trying each accepted column shape.
    Returns (rows, error) — rows is None only if every shape failed."""
    last = None
    for sel in _BALANCE_SELECTS:
        try:
            return list(client.query(f"{sel} FROM `{table_id}` ORDER BY balance DESC").result(timeout=30)), None
        except Exception as exc:  # noqa: BLE001 - try the next column shape
            last = exc
    return None, last


def _list_tables(client, needle, exclude=()):
    """Table names in the dataset whose name contains `needle` (case-insensitive),
    minus any in `exclude`. Used to auto-discover a balances/ynab table when the
    configured one is missing or renamed."""
    try:
        ds = _dataset()
        sql = (f"SELECT table_name FROM `{ds}.INFORMATION_SCHEMA.TABLES` "
               f"WHERE LOWER(table_name) LIKE '%{needle.lower()}%' ORDER BY table_name")
        out = []
        for r in client.query(sql).result(timeout=20):
            name = r["table_name"]
            if not any(e in name.lower() for e in exclude):
                out.append(name)
        return out
    except Exception:
        return []


def _resolve_balance_table(client, table_id, needle, exclude=()):
    """Read the configured table; if that yields nothing, auto-discover a sibling
    table matching `needle` and read that. Returns (rows, used_table, candidates)."""
    rows, _ = _read_balance_rows(client, table_id)
    if rows is not None:
        return rows, table_id, []
    candidates = _list_tables(client, needle, exclude)
    for name in candidates:
        cid = f"{_dataset()}.{name}"
        if cid == table_id:
            continue
        rows, _ = _read_balance_rows(client, cid)
        if rows is not None:
            return rows, cid, candidates
    return None, table_id, candidates


# Account → group, by name keywords. Order matters: real-estate wins (so an Ally
# real-estate account lands in Real Estate, not Lifestyle), then the WF "shared" account,
# then any Ally account (Lifestyle), then WF bills/spending (Operational). Overridable by
# editing these keyword lists; anything unmatched falls into "Other" so nothing is lost.
BALANCE_GROUP_ORDER = ["Operational", "Shared", "Lifestyle", "Real Estate", "Other"]
_RE_KEYWORDS = ("real estate", "property", "mortgage", "rental", "zillow", "zestimate",
                "home loan", "heloc", "escrow")
_OPERATIONAL_KEYWORDS = ("wf", "wells", "bill", "spending", "checking")


def _account_group(name):
    n = (name or "").lower()
    if any(k in n for k in _RE_KEYWORDS):
        return "Real Estate"
    if "shared" in n:          # WF shared
        return "Shared"
    if "ally" in n:            # Ally (non-real-estate) → Lifestyle
        return "Lifestyle"
    if any(k in n for k in _OPERATIONAL_KEYWORDS):
        return "Operational"
    return "Other"


def _ynab_history_table():
    # Daily net-worth history (one row per account per day) from the YNAB → BigQuery
    # pipeline; lives in the same `home_ynab` dataset. BQ_YNAB_HISTORY_TABLE overrides.
    env = os.environ.get("BQ_YNAB_HISTORY_TABLE")
    if env:
        return env
    proj = _project()
    return f"{proj}.home_ynab.ynab_balances_history" if proj else f"{_dataset()}.ynab_balances_history"


def _networth_cards(client):
    """Net-worth-over-time card(s) for the Balances page, from `ynab_balances_history`
    (one row per account per day → net worth = SUM(balance) per snapshot_date). Returns
    a 0-or-1-element list so the template can `repeat` over it: the card simply
    disappears when the history table is absent/unreadable — never an error.

    Backfilled (`source = 'backfill'`) leading days are approximations, so they render
    as a dashed segment that joins the solid 'actual' line."""
    sql = (
        "SELECT snapshot_date, ROUND(SUM(balance), 2) AS net_worth, "
        "IF(LOGICAL_OR(source = 'backfill'), 'backfill', 'actual') AS source "
        f"FROM `{_ynab_history_table()}` GROUP BY snapshot_date ORDER BY snapshot_date"
    )
    try:
        rows = list(client.query(sql).result(timeout=30))
    except Exception:  # noqa: BLE001 - table missing / unreadable → just omit the card
        return []
    pts = []
    for r in rows:
        d = dict(r.items())
        sd, nw = d.get("snapshot_date"), d.get("net_worth")
        if sd is None or nw is None:
            continue
        pts.append((sd, float(nw), d.get("source") or "actual"))
    if len(pts) < 2:
        return []  # need at least two points to draw a trend

    n = len(pts)
    nets = [p[1] for p in pts]
    xs = [i / (n - 1) for i in range(n)]
    # Contiguous leading 'backfill' prefix → dashed; overlap one point so it meets the line.
    split = 0
    while split < n and pts[split][2] == "backfill":
        split += 1
    if 0 < split < n:
        series = [
            {"dashed": True, "color": "#8893b5", "x": xs[:split + 1], "points": nets[:split + 1]},
            {"x": xs[split:], "points": nets[split:]},  # color omitted → theme accent
        ]
    elif split >= n:  # everything is estimated
        series = [{"dashed": True, "color": "#8893b5", "x": xs, "points": nets}]
    else:
        series = [{"x": xs, "points": nets}]

    first_net, last_net = nets[0], nets[-1]
    delta = last_net - first_net
    lo, hi = min(nets), max(nets)
    d0, d1 = pts[0][0], pts[-1][0]
    try:
        days = (d1 - d0).days
    except Exception:  # noqa: BLE001
        days = n - 1

    def _fd(d):
        try:
            return d.strftime("%b %-d")
        except Exception:  # noqa: BLE001
            return str(d)

    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
    return [{
        "titleFormatted": "Net worth",
        "currentFormatted": _money(last_net),
        "currentDirection": "down" if last_net < 0 else "up",
        "changeFormatted": f"{arrow} {_money(abs(delta))} · {days}d",
        "changeDirection": "down" if delta < 0 else "up",
        "rangeFormatted": f"{_fd(d0)} – {_fd(d1)}",
        "spanFormatted": f"Low {_money(lo)} · High {_money(hi)}",
        "footnoteFormatted": (f"≈ first {split} day{'' if split == 1 else 's'} estimated"
                              if split > 0 else ""),
        "series": series,
    }]


def get_balances():
    """Current account balances, pushed into BigQuery from the budget sheet (an Apps
    Script sums each account's ledger and upserts a snapshot table on a timer). We
    read the latest, format and total it — assets (positive) on top, liabilities
    below.

    Tolerant of column-name drift and of the table being renamed (auto-discovers a
    `*balance*` table in the dataset); degrades to a clear, populated empty state
    that names what it found, so the page is never a mystery blank."""
    client = _client()
    ynab_id = _ynab_table()
    table_id = _balances_table()
    # Balances are sourced from the YNAB-synced table (home_ynab.ynab_balances) first;
    # if that's empty/missing fall back to the budget-sheet snapshot, then any
    # *balance* table — so the page shows real numbers from whatever is populated.
    rows, used, candidates = _resolve_balance_table(client, ynab_id, "ynab")
    if rows is None:
        rows, used, more = _resolve_balance_table(client, table_id, "balance")
        candidates = candidates + [c for c in more if c not in candidates]
    if rows is None:
        found = ", ".join(candidates) if candidates else "none"
        return {
            "title": "Balances",
            "subtitleFormatted": f"No balances table found · looked for {ynab_id.split('.')[-1]} · tables: {found}",
            "netFormatted": _money(0), "netDirection": "up", "asOfFormatted": "",
            "accounts": [], "netWorthCards": [], "rowCount": 0,
        }
    # YNAB stores amounts in milliunits; scale when reading a ynab-named table and the flag is set.
    scale = 1000.0 if ("ynab" in used.lower() and os.environ.get("BQ_YNAB_MILLIUNITS", "0") == "1") else 1.0

    accounts, net, as_of = [], 0.0, None
    for r in rows:
        d = dict(r.items())
        name = d.get("account")
        if not name:
            continue
        bal = float(d.get("balance") or 0.0) / scale
        net += bal
        ao = d.get("as_of")
        if ao is not None and (as_of is None or ao > as_of):
            as_of = ao
        accounts.append({
            "name": name,
            "balance": bal,
            "balanceFormatted": _money(bal),
            "direction": "down" if bal < 0 else "up",
            "kind": _ynab_kind(d.get("type"), bal),
        })

    # Group accounts (Operational / Shared / Lifestyle / Real Estate / Other) with a
    # subtotal per group; empty groups are dropped.
    for a in accounts:
        a["group"] = _account_group(a["name"])
    groups = []
    for g in BALANCE_GROUP_ORDER:
        members = [a for a in accounts if a["group"] == g]
        if not members:
            continue
        sub = sum(a["balance"] for a in members)
        groups.append({
            "title": g,
            "accounts": members,
            "subtotal": sub,
            "subtotalFormatted": _money(sub),
            "subtotalDirection": "down" if sub < 0 else "up",
            "countFormatted": f"{len(members)} account" + ("" if len(members) == 1 else "s"),
        })

    as_of_str = ""
    if as_of is not None:
        try:
            as_of_str = as_of.astimezone().strftime("as of %b %-d, %-I:%M %p")
        except Exception:
            as_of_str = f"as of {as_of}"
    src = f" · {used.split('.')[-1]}" if used != table_id else ""
    subtitle = f"{len(accounts)} accounts{src}" + (f" · {as_of_str}" if as_of_str else "")
    return {
        "title": "Balances",
        "subtitleFormatted": subtitle,
        "netFormatted": _money(net),
        "netDirection": "down" if net < 0 else "up",
        "asOfFormatted": as_of_str,
        "netWorthCards": _networth_cards(client),
        "groups": groups,
        "accounts": accounts,
        "rowCount": len(accounts),
    }


# ---------------------------------------------------------------- /budget

def _ynab_transactions_table():
    env = os.environ.get("BQ_YNAB_TX_TABLE")
    if env:
        return env
    proj = _project()
    return f"{proj}.home_ynab.ynab_transactions" if proj else f"{_dataset()}.ynab_transactions"


def _ynab_categories_table():
    env = os.environ.get("BQ_YNAB_CATEGORIES_TABLE")
    if env:
        return env
    proj = _project()
    return f"{proj}.home_ynab.ynab_categories" if proj else f"{_dataset()}.ynab_categories"


# Spending buckets. Hybrid mapping (per the request): a YNAB category-GROUP whose name
# contains a bucket's word maps straight through (the groups that already "line up");
# otherwise keyword-match the group name, then the category name; anything left lands in
# "Other" so nothing is dropped. Edit these token lists to refine against real names.
BUDGET_BUCKET_ORDER = ["Essential", "Shared", "Goals", "Lifestyle", "Real Estate", "Other"]
_BUCKET_TOKENS = {
    "Real Estate": ("real estate", "property", "rental", "mortgage", "hoa", "escrow", "landlord"),
    "Shared": ("shared",),
    "Goals": ("goal", "saving", "invest", "sinking", "emergency", "retirement"),
    "Essential": ("essential", "needs", "fixed", "bills", "utilit", "insurance",
                  "grocer", "medical", "health", "rent", "transport"),
    "Lifestyle": ("lifestyle", "fun", "discretionary", "wants", "entertain", "leisure",
                  "dining", "restaurant", "travel", "shopping", "subscription"),
}
# Priority when several buckets could match: real estate & shared win first.
_BUCKET_PRIORITY = ["Real Estate", "Shared", "Goals", "Essential", "Lifestyle"]


def _budget_bucket(group_name, category_name):
    for text in ((group_name or "").lower(), (category_name or "").lower()):
        for bucket in _BUCKET_PRIORITY:
            if any(t in text for t in _BUCKET_TOKENS[bucket]):
                return bucket
    return "Other"


def get_budget(months=12):
    """Average monthly spending per category over the last `months` FULL calendar months
    (the current partial month excluded), grouped into spending buckets with subtotals.
    Each category shows its average monthly spend plus the min–max range across those
    months, to inform future budgeting.

    Reads YNAB transactions — net outflow per category (YNAB amounts are negative for
    spending and in milliunits; refunds net down; transfers and uncategorized excluded) —
    joined to category groups for the bucket mapping. Degrades to a clean, named empty
    state if the transactions table is absent."""
    client = _client()
    tx = _ynab_transactions_table()
    sql = f"""
        WITH d AS (
          SELECT SAFE.PARSE_DATE('%Y-%m-%d', SUBSTR(date, 1, 10)) AS dt,
                 category_id, category_name, amount
          FROM `{tx}`
          WHERE deleted = FALSE AND transfer_account_id IS NULL AND category_name IS NOT NULL
        )
        SELECT category_id, category_name, DATE_TRUNC(dt, MONTH) AS m,
               -SUM(amount) / 1000.0 AS spend
        FROM d
        WHERE dt >= DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL {int(months)} MONTH), MONTH)
          AND dt <  DATE_TRUNC(CURRENT_DATE(), MONTH)
        GROUP BY category_id, category_name, m
    """
    try:
        rows = list(client.query(sql).result(timeout=45))
    except Exception as exc:  # noqa: BLE001 - table missing/unreadable → clean empty state
        return {
            "title": "Budget",
            "subtitleFormatted": f"No transactions table · {tx.split('.')[-1]}",
            "totalAvgFormatted": _money(0), "windowFormatted": "",
            "buckets": [], "rowCount": 0,
            "emptyFormatted": f"Couldn't read spending: {str(exc)[:120]}",
        }

    # category id → group name, for the bucket mapping (optional; name-keyword fallback).
    groups = {}
    try:
        for r in client.query(
                f"SELECT id, category_group_name FROM `{_ynab_categories_table()}`").result(timeout=30):
            d = dict(r.items())
            groups[d.get("id")] = d.get("category_group_name")
    except Exception:  # noqa: BLE001
        pass

    months_seen = set()
    per_cat = {}  # cid -> {"name", "by_month": {month: spend}}
    for r in rows:
        d = dict(r.items())
        cid, m = d.get("category_id"), d.get("m")
        months_seen.add(m)
        slot = per_cat.setdefault(cid, {"name": d.get("category_name") or "Uncategorized", "by_month": {}})
        slot["by_month"][m] = slot["by_month"].get(m, 0.0) + float(d.get("spend") or 0.0)

    sorted_months = sorted(months_seen)
    divisor = len(sorted_months) or 1  # full months actually covered (≈ `months`)

    buckets_map = {}
    total_avg = 0.0
    for cid, slot in per_cat.items():
        vals = [slot["by_month"].get(m, 0.0) for m in sorted_months]  # 0 in months with no spend
        total = sum(vals)
        if abs(total) < 0.005:
            continue  # nothing spent in the window
        avg = total / divisor
        lo, hi = min(vals), max(vals)
        rng = f"{_money(lo)} – {_money(hi)}"
        # Lumpy lines (range exceeds a month's average — e.g. Lifestyle, or categories
        # with $0/very-low months) understate as a monthly average, so also express a
        # projected annual budget (avg × 12 — a true trailing-12-month total over a full
        # 12-month window) to plan against.
        variable = avg > 0 and (hi - lo) >= avg
        annual = f"~${avg * 12:,.0f}/yr" if variable else ""
        if variable:
            rng += f" · {annual} projected"
        cat = {
            "name": slot["name"],
            "avg": avg,
            "avgFormatted": f"{_money(avg)}/mo",
            "rangeFormatted": rng,
            "variable": variable,
            "annualFormatted": annual,
            "direction": "down" if avg < 0 else "up",
        }
        buckets_map.setdefault(_budget_bucket(groups.get(cid), slot["name"]), []).append(cat)
        total_avg += avg

    buckets = []
    for b in BUDGET_BUCKET_ORDER:
        cats = buckets_map.get(b)
        if not cats:
            continue
        cats.sort(key=lambda c: c["avg"], reverse=True)
        sub = sum(c["avg"] for c in cats)
        buckets.append({
            "title": b,
            "subtotal": sub,
            "subtotalFormatted": f"{_money(sub)}/mo",
            "subtotalDirection": "down" if sub < 0 else "up",
            "countFormatted": f"{len(cats)} categor" + ("y" if len(cats) == 1 else "ies"),
            "categories": cats,
        })

    def _mlabel(m):
        try:
            return m.strftime("%b %Y")
        except Exception:  # noqa: BLE001
            return str(m)

    window = f"{_mlabel(sorted_months[0])} – {_mlabel(sorted_months[-1])}" if sorted_months else ""
    return {
        "title": "Budget",
        "subtitleFormatted": (f"Avg monthly spending · last {divisor} full month"
                              + ("" if divisor == 1 else "s") + (f" · {window}" if window else "")),
        "totalAvgFormatted": f"{_money(total_avg)}/mo",
        "windowFormatted": window,
        "buckets": buckets,
        "rowCount": sum(len(b["categories"]) for b in buckets),
        "emptyFormatted": "" if buckets else f"No categorized spending in the last {int(months)} full months.",
    }


# ---------------------------------------------------------------- /gcp_costs

def _billing_table():
    return os.environ.get("BQ_BILLING_TABLE")


def get_gcp_costs():
    """GCP billing cost summary: current month total + 12-month trend + per-service breakdown.
    Reads the GCP billing export table (BQ_BILLING_TABLE env var). Degrades cleanly when
    the table is not configured or contains no data."""
    import calendar

    def _short_label(yyyymm):
        yr, mo = int(yyyymm[:4]), int(yyyymm[4:])
        return f"{calendar.month_abbr[mo]} '{str(yr)[2:]}"

    def _long_label(yyyymm):
        yr, mo = int(yyyymm[:4]), int(yyyymm[4:])
        return f"{calendar.month_name[mo]} {yr}"

    _empty = {
        "title": "GCP Costs", "subtitleFormatted": "", "monthFormatted": "—",
        "totalFormatted": "—", "mtdLabelFormatted": "", "changeFormatted": "",
        "changeDirection": "neutral", "series": [], "firstLabelFormatted": "",
        "lastLabelFormatted": "", "services": [], "rowCount": 0,
    }

    table = _billing_table()
    if not table:
        return {**_empty,
                "subtitleFormatted": "Set BQ_BILLING_TABLE to enable",
                "emptyFormatted": ("Set BQ_BILLING_TABLE (e.g. project.dataset.gcp_billing_export_v1_…) "
                                   "in your .env to track GCP costs.")}

    client = _client()
    monthly_sql = f"""
        SELECT
          invoice.month AS month,
          ROUND(
            SUM(cost)
            + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)),
            2
          ) AS net_cost
        FROM `{table}`
        WHERE invoice.month >= FORMAT_DATE('%Y%m', DATE_SUB(CURRENT_DATE(), INTERVAL 12 MONTH))
        GROUP BY 1
        ORDER BY 1
    """
    service_sql = f"""
        SELECT
          service.description AS service,
          ROUND(
            SUM(cost)
            + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)),
            2
          ) AS net_cost
        FROM `{table}`
        WHERE invoice.month = FORMAT_DATE('%Y%m', CURRENT_DATE())
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 20
    """
    try:
        monthly_rows = [dict(r.items()) for r in client.query(monthly_sql).result(timeout=45)]
        service_rows = [dict(r.items()) for r in client.query(service_sql).result(timeout=30)]
    except Exception as exc:  # noqa: BLE001
        return {**_empty,
                "subtitleFormatted": f"Query failed · {table.split('.')[-1]}",
                "emptyFormatted": f"Couldn't read billing: {str(exc)[:160]}"}

    if not monthly_rows:
        return {**_empty,
                "subtitleFormatted": f"No data · {table.split('.')[-1]}",
                "emptyFormatted": "No billing data found in the configured table."}

    monthly = sorted([(str(r["month"]), float(r["net_cost"] or 0.0)) for r in monthly_rows],
                     key=lambda x: x[0])

    today = datetime.date.today()
    current_yyyymm = today.strftime("%Y%m")
    today_str = today.strftime("%b %-d, %Y")

    # Separate completed months from current partial month
    current_cost = None
    completed = monthly
    if monthly and monthly[-1][0] == current_yyyymm:
        current_cost = monthly[-1][1]
        completed = monthly[:-1]

    display_cost = current_cost if current_cost is not None else (completed[-1][1] if completed else 0.0)
    display_yyyymm = monthly[-1][0] if monthly else current_yyyymm

    # Month-over-month change (compare current partial vs previous full month)
    change_str = ""
    change_dir = "neutral"
    if current_cost is not None and completed:
        prev_cost = completed[-1][1]
        prev_label = _long_label(completed[-1][0])
        delta = current_cost - prev_cost
        sign = "+" if delta >= 0 else ""
        if prev_cost:
            pct = delta / abs(prev_cost) * 100
            change_str = f"{sign}{_money(delta)} ({sign}{pct:.0f}%) vs {prev_label}"
        else:
            change_str = f"{sign}{_money(delta)} vs {prev_label}"
        change_dir = "down" if delta > 0 else ("up" if delta < 0 else "neutral")

    # Build lineChart series: solid completed months, dashed current partial
    all_months = monthly
    n = len(all_months)
    x_vals = [round(i / (n - 1), 6) if n > 1 else 0.0 for i in range(n)]
    chart_color = "#3B82F6"
    series = []
    if n > 1 and current_cost is not None and completed:
        series.append({"color": chart_color,
                        "points": [m[1] for m in completed],
                        "x": x_vals[:len(completed)], "dashed": False})
        series.append({"color": chart_color,
                        "points": [completed[-1][1], current_cost],
                        "x": [x_vals[len(completed) - 1], x_vals[-1]], "dashed": True})
    elif n > 0:
        series.append({"color": chart_color,
                        "points": [m[1] for m in all_months],
                        "x": x_vals, "dashed": False})

    # Service breakdown for current month
    services = []
    svc_total = sum(float(r.get("net_cost") or 0.0) for r in service_rows)
    for r in service_rows:
        cost = float(r.get("net_cost") or 0.0)
        if abs(cost) < 0.005:
            continue
        pct = (cost / svc_total * 100) if svc_total else 0.0
        services.append({
            "name": r.get("service") or "Unknown",
            "costFormatted": _money(cost),
            "pctFormatted": f"{pct:.0f}%",
        })

    mtd = "Month to date" if current_cost is not None else ""
    return {
        "title": "GCP Costs",
        "subtitleFormatted": f"12-month trend · {today_str}",
        "monthFormatted": _long_label(display_yyyymm),
        "totalFormatted": _money(display_cost),
        "mtdLabelFormatted": mtd,
        "changeFormatted": change_str,
        "changeDirection": change_dir,
        "series": series,
        "firstLabelFormatted": _short_label(all_months[0][0]) if all_months else "",
        "lastLabelFormatted": _short_label(all_months[-1][0]) if all_months else "",
        "services": services,
        "rowCount": n,
        "emptyFormatted": "" if all_months else "No billing data found.",
    }


# ---------------------------------------------------------------- /smarthome

import glob as _glob


def _gdrive_my_drives():
    """Google Drive for Desktop 'My Drive' roots. On macOS these live under
    ~/Library/CloudStorage/GoogleDrive-<account>/My Drive; some setups use
    ~/CloudStorage/... — probe both so auto-detection actually finds the mount.
    Returns every match (most setups have one)."""
    home = os.path.expanduser("~")
    roots = []
    for parent in (os.path.join(home, "Library", "CloudStorage"),
                   os.path.join(home, "CloudStorage")):
        roots.extend(sorted(_glob.glob(os.path.join(parent, "GoogleDrive-*", "My Drive"))))
    return roots


# Friendly label / SF Symbol / accent per event source.
SOURCE_META = {
    "ha": ("Home Assistant", "house.fill", "#6ea8fe"),
    "hue": ("Hue", "lightbulb.fill", "#ffd166"),
    "yolink": ("YoLink", "sensor.tag.radiowaves.forward.fill", "#43d18a"),
    "eero": ("Eero", "wifi", "#6ea8fe"),
    "unifi": ("UniFi", "wifi", "#43d18a"),
}

# Friendly label / SF Symbol / accent per event *type* (entity domain, else event_type).
TYPE_META = {
    "light": ("Light", "lightbulb.fill", "#ffd166"),
    "switch": ("Switch", "switch.2", "#6ea8fe"),
    "binary_sensor": ("Binary Sensor", "sensor.fill", "#43d18a"),
    "sensor": ("Sensor", "thermometer", "#9aa4c4"),
    "lock": ("Lock", "lock.fill", "#f4a259"),
    "cover": ("Cover", "blinds.horizontal.closed", "#6ea8fe"),
    "climate": ("Climate", "thermometer.snowflake", "#43d18a"),
    "fan": ("Fan", "fanblades.fill", "#6ea8fe"),
    "person": ("Person", "person.fill", "#6ea8fe"),
    "device_tracker": ("Presence", "location.fill", "#6ea8fe"),
    "media_player": ("Media", "play.rectangle.fill", "#bb86fc"),
    "alarm_control_panel": ("Alarm", "shield.fill", "#ff6b6b"),
    "camera": ("Camera", "video.fill", "#ff6b6b"),
    "scene": ("Scene", "theatermasks.fill", "#bb86fc"),
    "automation": ("Automation", "gearshape.2.fill", "#9aa4c4"),
    "script": ("Script", "scroll.fill", "#9aa4c4"),
    "sun": ("Sun", "sun.max.fill", "#ffd166"),
    "weather": ("Weather", "cloud.sun.fill", "#9aa4c4"),
    "update": ("Update", "arrow.triangle.2.circlepath", "#9aa4c4"),
}

# Housekeeping/system domains that are NOT default-selected in the filter — they drown out the
# real-world activity. Anything present and not in here is on by default. NOTE: `sensor` is
# intentionally NOT excluded anymore — the Recent activity feed should surface all sensor
# activities (motion/contact/occupancy/etc.) alongside light changes and camera motion.
SMARTHOME_NOISE_TYPES = {
    "sun", "update", "number", "select", "weather", "automation",
    "script", "persistent_notification", "zone", "input_number", "input_select",
}


def _type_meta(t):
    if t in TYPE_META:
        return TYPE_META[t]
    label = str(t).replace("_", " ").strip().title() or "Event"
    return (label, "dot.radiowaves.left.and.right", "#9aa4c4")


def _source_meta(s):
    """Friendly label / icon / color for an event source (tier-1 filter), tolerating
    sources beyond the curated set (e.g. eero, unifi, …)."""
    if s in SOURCE_META:
        return SOURCE_META[s]
    label = str(s).replace("_", " ").strip().title() or "Source"
    return (label, "dot.radiowaves.left.and.right", "#9aa4c4")


def _event_details(e):
    """Flatten one raw event into ordered key/value rows for the tap-to-expand detail
    view — known fields first, then attributes, then any remaining top-level scalars."""
    if not isinstance(e, dict):
        return []
    rows, seen = [], set()

    def add(k, v):
        if v is None or v == "" or k in seen:
            return
        seen.add(k)
        rows.append({"keyFormatted": k, "valueFormatted": str(v)[:200]})

    new_state = e.get("new_state") if isinstance(e.get("new_state"), dict) else {}
    old_state = e.get("old_state") if isinstance(e.get("old_state"), dict) else {}
    add("Source", e.get("source"))
    add("Type", _event_type(e))
    add("Event", e.get("event_type") or e.get("type"))
    add("Entity", new_state.get("entity_id") or e.get("entity_id") or e.get("entity"))
    add("State", e.get("state") if e.get("state") is not None else new_state.get("state"))
    add("Was", old_state.get("state"))
    add("When", e.get("ts") or e.get("time") or e.get("timestamp") or e.get("created_at"))
    attrs = e.get("attributes") if isinstance(e.get("attributes"), dict) else {}
    for k, v in attrs.items():
        if isinstance(v, (str, int, float, bool)):
            add(str(k).replace("_", " ").title(), v)
    _known = {"source", "event_type", "type", "entity_id", "entity", "state", "ts", "time",
              "timestamp", "created_at", "new_state", "old_state", "attributes", "last_event_at"}
    for k, v in e.items():
        if k not in _known and isinstance(v, (str, int, float, bool)):
            add(str(k).replace("_", " ").title(), v)
    return rows[:24]


def _event_type(e):
    """Classify an event by entity domain (light, lock, sensor…), else event_type,
    else source — the dimension the filter groups and counts on."""
    if not isinstance(e, dict):
        return "event"
    new_state = e.get("new_state") if isinstance(e.get("new_state"), dict) else {}
    eid = new_state.get("entity_id") or e.get("entity_id") or e.get("entity") or ""
    if isinstance(eid, str) and "." in eid:
        return eid.split(".")[0]
    et = e.get("event_type") or e.get("type")
    if et:
        return str(et)
    return str(e.get("source") or "event")


def _event_device_class(e):
    """The Home Assistant device_class (motion/door/occupancy/…) if present, lowercased."""
    if not isinstance(e, dict):
        return ""
    ns = e.get("new_state") if isinstance(e.get("new_state"), dict) else {}
    for attrs in (e.get("attributes"), ns.get("attributes")):
        if isinstance(attrs, dict) and attrs.get("device_class"):
            return str(attrs["device_class"]).lower()
    return ""


def _event_name_blob(e):
    """Lowercased entity id + friendly name + device — for keyword matching (motion/camera)."""
    if not isinstance(e, dict):
        return ""
    ns = e.get("new_state") if isinstance(e.get("new_state"), dict) else {}
    attrs = e.get("attributes") if isinstance(e.get("attributes"), dict) else {}
    parts = (ns.get("entity_id"), e.get("entity_id"), e.get("entity"),
             attrs.get("friendly_name"), e.get("device"), e.get("event_type"))
    return " ".join(str(p).lower() for p in parts if p)


def _is_camera(e):
    """Whether the event comes from a camera / doorbell (for camera-motion labeling)."""
    blob = _event_name_blob(e)
    return (_event_type(e) == "camera" or _event_device_class(e) == "camera"
            or any(w in blob for w in ("camera", "doorbell", "cam ", "cam.")))


def _is_motion(e):
    """Whether the event is a motion/occupancy/person detection (incl. camera motion)."""
    if _event_device_class(e) in ("motion", "occupancy", "presence", "moving"):
        return True
    blob = _event_name_blob(e)
    return any(w in blob for w in ("motion", "person_detected", "person detected",
                                   "human", "occupancy", "presence detected"))


def _event_state(e):
    """The new/current state value as a string ('on'/'off'/'open'/…), or '' if none."""
    s = e.get("state")
    if s is None:
        ns = e.get("new_state") if isinstance(e.get("new_state"), dict) else {}
        s = ns.get("state")
    return "" if s is None else str(s)


# Truthy vs falsy binary-sensor states, for natural phrasing.
_STATE_ON = {"on", "detected", "true", "1", "open", "opened", "unlocked", "home", "motion"}
_STATE_OFF = {"off", "clear", "false", "0", "closed", "locked", "not_home", "away", "no_motion"}


def _activity_phrase(e):
    """A natural description of what happened ('Motion detected', 'Turned on', 'Opened',
    'Unlocked', '72°F') for the Recent activity feed, so events read as activity not raw state."""
    etype = _event_type(e)
    state = _event_state(e).strip()
    low = state.lower()
    dc = _event_device_class(e)
    if _is_motion(e):
        if low in _STATE_ON:
            return "Camera motion detected" if _is_camera(e) else "Motion detected"
        if low in _STATE_OFF:
            return "Motion cleared"
    if etype in ("light", "switch", "fan", "input_boolean", "media_player"):
        if low in _STATE_ON:
            return "Turned on"
        if low in _STATE_OFF:
            return "Turned off"
        if low == "playing":
            return "Playing"
        if low == "paused":
            return "Paused"
    if etype == "binary_sensor" or dc in ("door", "window", "opening", "garage_door"):
        if low in _STATE_ON:
            return "Opened" if dc in ("door", "window", "opening", "garage_door") else "Active"
        if low in _STATE_OFF:
            return "Closed" if dc in ("door", "window", "opening", "garage_door") else "Clear"
    if etype == "lock":
        if low in ("unlocked",):
            return "Unlocked"
        if low in ("locked",):
            return "Locked"
    if etype == "cover":
        if low in ("open", "opened"):
            return "Opened"
        if low in ("closed",):
            return "Closed"
    return state


def _smarthome_dir():
    """Locate the Drive-synced 'Smart Home Events' folder. SMARTHOME_DIR wins;
    otherwise probe the usual Google Drive for Desktop mount points (the folder
    lives under Private/Home, but tolerate a top-level copy too)."""
    env = os.environ.get("SMARTHOME_DIR")
    cands = [env] if env else []
    home = os.path.expanduser("~")
    for gd in _gdrive_my_drives():
        cands.append(os.path.join(gd, "Private", "Smart Home Events"))
        cands.append(os.path.join(gd, "Private", "Home", "Smart Home Events"))
        cands.append(os.path.join(gd, "Smart Home Events"))
    cands.append(os.path.join(home, "Smart Home Events"))
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return env  # let the caller raise a clear FileNotFound if nothing matched


def _parse_iso_or_epoch(v):
    """Event timestamps arrive as ISO-8601 (HA) or epoch seconds/ms (collectors)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            secs = v / 1000.0 if v > 1e11 else float(v)
            return datetime.datetime.fromtimestamp(secs, datetime.timezone.utc)
        except Exception:
            return None
    if isinstance(v, str):
        s = v.strip()
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            try:
                return datetime.datetime.fromtimestamp(float(s), datetime.timezone.utc)
            except Exception:
                return None
    return None


def _ago(dt, now):
    if not dt:
        return "—"
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _retry_os(fn, attempts=4, delay=0.25):
    """Run a filesystem op, retrying transient OS errors. Google Drive for Desktop's
    virtual filesystem intermittently raises EDEADLK ('Resource deadlock avoided') and
    EAGAIN when a long-lived process touches a folder/file; a short backoff clears it."""
    import time
    import errno
    transient = {errno.EDEADLK, errno.EAGAIN, getattr(errno, "EBUSY", 16)}
    last = None
    for i in range(attempts):
        try:
            return fn()
        except OSError as exc:
            last = exc
            if exc.errno not in transient:
                raise
            time.sleep(delay * (i + 1))
    raise last


def _tail_lines(path, n, budget=262144):
    """Read roughly the last `budget` bytes and return the final `n` non-blank lines."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - budget))
        data = f.read().decode("utf-8", "replace")
    lines = [ln for ln in data.splitlines() if ln.strip()]
    return lines[-n:]


def _event_view(e, now):
    """Tolerantly turn one logged event dict into a display row (schema may drift)."""
    if not isinstance(e, dict):
        return None
    dt = _parse_iso_or_epoch(e.get("ts") or e.get("time") or e.get("timestamp")
                             or e.get("created_at") or e.get("last_event_at"))
    source = (e.get("source") or "ha")
    attrs = e.get("attributes") if isinstance(e.get("attributes"), dict) else {}
    new_state = e.get("new_state") if isinstance(e.get("new_state"), dict) else {}
    name = (attrs.get("friendly_name") or new_state.get("entity_id")
            or e.get("entity_id") or e.get("entity") or e.get("device") or "event")
    label, src_icon, src_color = SOURCE_META.get(source, (source, "dot.radiowaves.left.and.right", "#9aa4c4"))
    etype_key = _event_type(e)
    type_label, type_icon, type_color = _type_meta(etype_key)
    # A natural "what happened" phrase (Motion detected / Turned on / Opened / …) rather than the
    # raw state, so the feed reads as activity. Motion (incl. camera motion) gets a distinct icon.
    phrase = _activity_phrase(e)
    if _is_motion(e):
        icon = "figure.walk.motion" if _event_state(e).lower() in _STATE_ON else "sensor.fill"
        color = "#ff6b6b"
    else:
        icon, color = type_icon, type_color   # per-event semantic icon (light/lock/sensor/…)
    detail = phrase or type_label
    return {
        "_t": dt or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
        "source": source, "sourceLabel": label, "sourceIcon": src_icon, "sourceColor": src_color,
        "icon": icon, "color": color,
        "type": etype_key, "typeLabel": type_label,
        "title": str(name), "detail": detail,
        "timeFormatted": (dt.astimezone().strftime("%-I:%M %p") if dt else ""),
        "agoFormatted": _ago(dt, now),
    }


def _scan_events_24h(d, now, hours=24, max_files=3, budget=4 * 1024 * 1024):
    """Parse events from the newest jsonl files (reading at most `budget` bytes from
    the tail of each) and keep those within the last `hours`. Returns (dt, raw) pairs
    newest-last per file; used for per-type 24h counts and the filtered recent list."""
    cutoff = now - datetime.timedelta(hours=hours)
    # The directory listing itself can EDEADLK on Google Drive's virtual FS — retry, and treat a
    # persistent failure as "no files" rather than letting it blow up the whole Smart Home page.
    try:
        files = sorted(_retry_os(lambda: _glob.glob(os.path.join(d, "events_*.jsonl")),
                                 attempts=6, delay=0.4))
    except Exception as exc:  # noqa: BLE001
        print(f"[smarthome] event-file listing failed ({exc}); returning no events", flush=True)
        return []
    out = []
    for path in files[-max_files:]:
        try:
            lines = _retry_os(lambda p=path: _tail_lines(p, 100000, budget=budget))
        except Exception:
            continue
        for ln in lines:
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if not isinstance(e, dict):
                continue
            dt = _parse_iso_or_epoch(e.get("ts") or e.get("time") or e.get("timestamp")
                                     or e.get("created_at") or e.get("last_event_at"))
            if dt is not None and dt >= cutoff:
                out.append((dt, e))
    return out


def get_smarthome(types_param=None, sources_param=None):
    """Summarize the smart-home event log the Mac mini's ha-events service writes to
    Google Drive: a status/last-update header from summary_latest.json, then a TWO-TIER
    multi-select filter — tier 1 = source (Home Assistant, Hue, Eero, …); tier 2 = type
    (entity domain / event_type), scoped to the selected sources — and the recent events
    filtered to (selected sources ∩ selected types), each carrying its full raw details.

    `types_param` / `sources_param` are the raw ?types= / ?sources= values: None = use
    the defaults (useful types; all sources); a comma-separated list (possibly empty) =
    exactly those keys selected."""
    d = _smarthome_dir()
    if not d or not os.path.isdir(d):
        # Degrade to a named empty state (repo convention) rather than raising — a raise
        # becomes an opaque error/blank on the phone; this tells you exactly what's missing.
        return {
            "title": "Smart Home",
            "subtitleFormatted": "event source unavailable",
            "statusFormatted": "No event source",
            "statusDirection": "down",
            "countsFormatted": (f"Smart Home Events folder not found. Set SMARTHOME_DIR or mount "
                                f"Google Drive on the Mac (tried: {d or 'no candidates'})."),
            "sources": [], "typeChips": [], "sourceChips": [],
            "recent": [], "events": [],
            "filterSummaryFormatted": "",
            "emptyFormatted": "No smart-home event source — is Google Drive mounted on the Mac mini?",
        }

    now = datetime.datetime.now(datetime.timezone.utc)

    def _read_summary():
        with open(os.path.join(d, "summary_latest.json")) as f:
            return json.load(f)
    # The summary only feeds the header counts/status; the real events come from the scan below.
    # A Drive EDEADLK that outlasts the retry must NOT fail the whole page — degrade to no summary.
    try:
        summary = _retry_os(_read_summary, attempts=6, delay=0.4)
    except Exception as exc:  # noqa: BLE001
        print(f"[smarthome] summary read failed ({exc}); continuing without it", flush=True)
        summary = {}
    counts = summary.get("counts", {}) or {}
    by_source = summary.get("by_source", {}) or {}
    generated = _parse_iso_or_epoch(summary.get("generated_at"))

    sources = []
    for key in ("ha", "hue", "yolink"):
        s = by_source.get(key) or {}
        last = _parse_iso_or_epoch(s.get("last_event_at"))
        total = int(s.get("total") or 0)
        label, icon, color = SOURCE_META.get(key, (key, "sensor.fill", "#9aa4c4"))
        if last is None:
            status, direction = ("No data", "down")
        elif (now - last).total_seconds() < 3600:
            status, direction = ("Online", "up")
        else:
            status, direction = ("Stale", "down")
        sources.append({
            "key": key, "label": label, "icon": icon, "color": color,
            "status": status, "direction": direction,
            "lastFormatted": _ago(last, now),
            "metaFormatted": f"{status} · {_ago(last, now)}" if last else status,
            "lastHour": int(s.get("last_hour") or 0),
            "lastHourFormatted": f"{int(s.get('last_hour') or 0)}/hr",
            "total": total,
        })

    # Scan the last 24h of events for per-source/-type counts + the recent (filtered) list.
    scanned = _scan_events_24h(d, now)

    def _src_of(e):
        return ((e.get("source") if isinstance(e, dict) else None) or "ha")

    # ---- Tier 1: source. Counts are over ALL scanned events (independent of the type filter).
    source_counts = {}
    for _dt, e in scanned:
        s = _src_of(e)
        source_counts[s] = source_counts.get(s, 0) + 1
    all_sources = sorted(source_counts, key=lambda s: (-source_counts[s], s))
    if sources_param is None:
        selected_sources = list(all_sources)            # default: every source on
    else:
        want_s = {s for s in (p.strip() for p in sources_param.split(",")) if s}
        selected_sources = [s for s in all_sources if s in want_s]
    selected_sources_set = set(selected_sources)

    # ---- Tier 2: type, scoped to the selected sources (a real drill-down).
    type_counts = {}
    for _dt, e in scanned:
        if _src_of(e) in selected_sources_set:
            t = _event_type(e)
            type_counts[t] = type_counts.get(t, 0) + 1
    all_types = sorted(type_counts, key=lambda t: (-type_counts[t], t))
    if types_param is None:
        selected_types = [t for t in all_types if t not in SMARTHOME_NOISE_TYPES]
    else:
        want_t = {s for s in (p.strip() for p in types_param.split(",")) if s}
        selected_types = [t for t in all_types if t in want_t]
    selected_types_set = set(selected_types)

    # Build a /screen/smarthome href. A chip toggle pins ITS dimension and preserves the
    # other one verbatim (the incoming param), so the two tiers compose cleanly.
    def _href(types_sel=None, sources_sel=None):
        parts = []
        if types_sel is not None:
            parts.append("types=" + _urlparse.quote(",".join(sorted(types_sel)), safe=","))
        elif types_param is not None:
            parts.append("types=" + _urlparse.quote(types_param, safe=","))
        if sources_sel is not None:
            parts.append("sources=" + _urlparse.quote(",".join(sorted(sources_sel)), safe=","))
        elif sources_param is not None:
            parts.append("sources=" + _urlparse.quote(sources_param, safe=","))
        return "/screen/smarthome" + ("?" + "&".join(parts) if parts else "")

    # Tier-1 source filter chips (toggle a source, preserving the type selection).
    source_filters = []
    for s in all_sources:
        active = s in selected_sources_set
        label, icon, _c = _source_meta(s)
        toggled = (selected_sources_set - {s}) if active else (selected_sources_set | {s})
        source_filters.append({
            "key": s, "label": label, "icon": icon,
            "count": source_counts[s],
            "labelFormatted": f"{label} · {source_counts[s]}",
            "active": active,
            "color": "$accent" if active else "$textSecondary",
            "navHref": _href(sources_sel=toggled),
        })

    # Tier-2 type filter chips (toggle a type, preserving the source selection).
    types = []
    for t in all_types:
        active = t in selected_types_set
        label, icon, _c = _type_meta(t)
        toggled = (selected_types_set - {t}) if active else (selected_types_set | {t})
        types.append({
            "key": t, "label": label, "icon": icon,
            "count": type_counts[t],
            "labelFormatted": f"{label} · {type_counts[t]}",
            "active": active,
            "color": "$accent" if active else "$textSecondary",
            "navHref": _href(types_sel=toggled),
        })

    # Recent events filtered to (selected sources ∩ selected types), newest first, each
    # carrying its full set of raw details for the tap-to-expand view.
    events = []
    for _dt, e in scanned:
        if _src_of(e) not in selected_sources_set:
            continue
        ev = _event_view(e, now)
        if not ev or ev.get("type") not in selected_types_set:
            continue
        ev["details"] = _event_details(e)
        ev["detailsLabel"] = "Details"
        events.append(ev)
    events.sort(key=lambda e: e["_t"], reverse=True)
    events = events[:40]
    for ev in events:
        ev.pop("_t", None)

    total_24h = sum(source_counts.values())
    filter_summary = (f"{len(selected_sources)}/{len(all_sources)} sources · "
                      f"{len(selected_types)}/{len(all_types)} types · "
                      f"{len(events)} of {total_24h} events (24h)")

    online = any(s["direction"] == "up" for s in sources)
    return {
        "title": "Smart Home",
        "subtitleFormatted": f"Updated {_ago(generated, now)}" if generated else "Smart Home",
        "statusFormatted": "Online" if online else "Offline",
        "statusDirection": "up" if online else "down",
        "countsFormatted": (f"{counts.get('last_hour', 0)} in the last hour · "
                            f"{counts.get('last_24h', 0)} in 24h · {counts.get('total', 0)} total"),
        "sources": sources,
        "sourceFilters": source_filters,
        "types": types,
        "filterSummaryFormatted": filter_summary,
        "allHref": _href(types_sel=all_types),
        "allSourcesHref": _href(sources_sel=all_sources),
        "defaultsHref": "/screen/smarthome",
        "events": events,
        "rowCount": len(events),
        "emptyFormatted": ("No events match the selected sources/types — tap one above to show it."
                           if not events else ""),
    }


def _is_active_change(e):
    """Whether an event is an ACTIVE state-change notification worth logging — motion detection,
    door/contact sensors, lights on/off (plus locks/covers). Excludes numeric sensor telemetry,
    device/housekeeping updates, and anything without a discrete on/off/open/closed transition."""
    if _is_motion(e):
        return True
    etype = _event_type(e)
    state = _event_state(e).strip().lower()
    if etype in ("light", "switch", "fan", "input_boolean"):
        return state in _STATE_ON or state in _STATE_OFF
    if etype == "binary_sensor":          # door / window / contact / occupancy / motion — all discrete
        return True
    if etype == "lock":
        return state in ("locked", "unlocked")
    if etype == "cover":
        return state in ("open", "opened", "closed")
    return False


def smarthome_log():
    """A literal, newest-first log of ACTIVE smart-home state changes only — motion detected,
    door/contact sensors, lights on/off (and locks/covers) — showing the minimum needed to read
    each: when · <entity> · <what happened>. Numeric sensor telemetry and housekeeping are dropped."""
    d = _smarthome_dir()
    if not d or not os.path.isdir(d):
        # Named empty state instead of a raise (which would blank the page / read as "offline").
        return {
            "title": "Smart Home Log",
            "subtitleFormatted": "event source unavailable",
            "events": [],
            "rowCount": 0,
            "emptyFormatted": (f"Smart Home Events folder not found — set SMARTHOME_DIR or mount "
                               f"Google Drive on the Mac mini (tried: {d or 'no candidates'})."),
        }
    now = datetime.datetime.now(datetime.timezone.utc)
    now_local = now.astimezone()
    scanned = _scan_events_24h(d, now)
    rows = []
    for dt, e in scanned:
        if not _is_active_change(e):
            continue
        ev = _event_view(e, now)
        if not ev:
            continue
        rows.append((dt, {
            "timeFormatted": _log_clock(dt.astimezone(), now_local),
            "icon": ev["icon"], "color": ev["color"],
            "lineFormatted": " · ".join(x for x in (ev.get("title"), ev.get("detail")) if x),
        }))
    rows.sort(key=lambda r: r[0], reverse=True)          # newest first
    events = [r[1] for r in rows]
    return {
        "title": "Smart Home Log",
        "subtitleFormatted": f"{len(events)} state changes · last 24h",
        "events": events,
        "rowCount": len(events),
        "emptyFormatted": "" if events else "No motion / door / light activity in the last 24h.",
    }


# ---------------------------------------------------------------- /ynab

def _ynab_table():
    # The Balances page is sourced from the YNAB-synced table, which lives in its own
    # `home_ynab` dataset (NOT the configured BQ_DATASET, which is the AFM dataset).
    # BQ_YNAB_TABLE overrides the fully-qualified id.
    env = os.environ.get("BQ_YNAB_TABLE")
    if env:
        return env
    proj = _project()
    return f"{proj}.home_ynab.ynab_balances" if proj else f"{_dataset()}.ynab_balances"


def _ynab_kind(type_str, bal):
    """A friendly account-kind label: prefer YNAB's `type` (camelCase -> words),
    else fall back to asset/liability by sign."""
    if type_str:
        t = str(type_str).strip()
        # creditCard -> Credit Card, otherwiseSavings -> Savings, etc.
        words = "".join((" " + c if c.isupper() else c) for c in t).strip()
        return words[:1].upper() + words[1:]
    return "Liability" if bal < 0 else "Asset"


# (The standalone YNAB page was removed — the Balances page now sources real numbers
# from the YNAB-synced table when the budget-sheet snapshot is empty. `_ynab_table`
# and `_ynab_kind` above are reused by get_balances.)


# ---------------------------------------------------------------- /logs

def _int_env(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# Heuristic signals for flagging a log's recent state at a glance. Failure wins, then
# an in-progress signal, then success — scanned over the last few lines of the tail.
_LOG_FAIL_RE = _re.compile(
    r"traceback|\bexception\b|\bfatal\b|\bpanic\b|\bfail(?:ed|ure)?\b|\berrors?\b|"
    r"\bdenied\b|\brefused\b|\bcrash(?:ed)?\b|\bsegfault\b|\bunable to\b|"
    r"\bno such file\b|\bnot found\b|\btimed?\s*out\b|\bhttp/\S*\s*5\d\d\b|❌|\bERR\b", _re.I)
# Lines that look like failures but aren't ("0 errors", "no error", "errors: 0", "ok").
_LOG_FAIL_NEG_RE = _re.compile(
    r"\b(?:no|0|zero|without)\s+errors?\b|errors?[:=]\s*0\b|\bok\b|✅|"
    r"\b(?:0|no)\s+fail(?:ure)?s?\b|fail(?:ure)?s?[:=]\s*0\b", _re.I)
_LOG_RUN_RE = _re.compile(r"\b(?:running|starting|started|in[- ]progress|building|syncing|working|fetch(?:ing)?)\b", _re.I)
_LOG_OK_RE = _re.compile(r"\b(?:done|success(?:ful)?|completed?|finished|healthy|passed|listening|ok)\b|✅", _re.I)

# --- run/iteration interpretation --------------------------------------------------------
# DECISIVE markers are terminal verdicts (exit codes, FAILED/SUCCESS, traceback) that outrank
# incidental "error" mentions — so a run that retried then succeeded reads as OK, and a stray
# "error" in a healthy run doesn't flip it to Failed. The last decisive marker in a run wins.
_FAIL_DECISIVE_RE = _re.compile(
    r"\bFAILED\b|\bFATAL\b|\bpanic\b|traceback|\btraceback \(most recent call last\)|"
    r"\bexit(?:ed|\s+with)?\s+(?:code|status)?\s*[:=]?\s*[1-9]\d*\b|"
    r"\b(?:returned|rc|status)\s*[:=]?\s*[1-9]\d*\b|non-?zero\s+exit|"
    r"\berror:\s|\bERROR\b|❌", _re.I)
_OK_DECISIVE_RE = _re.compile(
    r"\bSUCCESS(?:FUL(?:LY)?)?\b|\ball\s+tests?\s+passed\b|\bcompleted\b|\bfinished\b|"
    r"\bexit(?:ed|\s+with)?\s+(?:code|status)?\s*[:=]?\s*0\b|"
    r"\b(?:returned|rc|status)\s*[:=]?\s*0\b|\bdone\b|✅|\bhealthy\b|listening on", _re.I)
# A line marking the START of a new run/iteration (banner rules, cron/launch/run headers).
_LOG_RUN_START_RE = _re.compile(
    r"^\s*[-=*#~]{4,}\s*$|"                                     # a banner rule ==== / ---- / ####
    r"\b(?:start(?:ing|ed)?|begin(?:ning)?|launch(?:ing|ed)?|"
    r"run\s*#?\s*\d+|iteration\s*#?\s*\d+|=== )\b", _re.I)


def _line_signal(l):
    """Classify a single line as a run verdict signal: ('fail'|'ok', decisive) or None.
    decisive=True marks a terminal verdict (exit code / FAILED|SUCCESS / traceback)."""
    neg = bool(_LOG_FAIL_NEG_RE.search(l))
    if _FAIL_DECISIVE_RE.search(l) and not neg:
        return ("fail", True)
    if _OK_DECISIVE_RE.search(l):
        return ("ok", True)
    if _LOG_FAIL_RE.search(l) and not neg:
        return ("fail", False)
    if neg or _LOG_OK_RE.search(l):
        return ("ok", False)
    return None


def _run_outcome(lines):
    """Verdict for a run's lines: 'ok' | 'fail' | 'unknown'. A decisive terminal marker wins
    (last one seen); otherwise any real failure beats a weak success signal."""
    last_decisive = None
    weak_fail = weak_ok = False
    for l in lines:
        sig = _line_signal(l)
        if not sig:
            continue
        kind, decisive = sig
        if decisive:
            last_decisive = kind
        elif kind == "fail":
            weak_fail = True
        else:
            weak_ok = True
    if last_decisive:
        return last_decisive
    if weak_fail:
        return "fail"
    if weak_ok:
        return "ok"
    return "unknown"


def _dur_short(secs):
    """Compact duration: '48s', '1m12s', '2h03m'. None → ''."""
    if secs is None:
        return ""
    secs = int(round(secs))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m{s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if m else f"{h}h"


def _parse_runs(lines, max_runs=8, gap_min=None):
    """Segment log lines into runs/iterations (oldest→newest, last `max_runs`). Each run:
    {outcome, seconds|None, end|None, nlines}. Boundaries come from explicit run-start markers;
    when the log has none, fall back to timestamp GAPS (a > gap_min-minute jump starts a run)."""
    lines = [l for l in (lines or []) if l.strip()]
    if not lines:
        return []
    gap = (gap_min if gap_min is not None else _int_env("LOG_RUN_GAP_MIN", 5)) * 60
    times = [_line_time(l) for l in lines]

    starts = [i for i, l in enumerate(lines) if _LOG_RUN_START_RE.search(l)]
    if len(starts) < 2:  # no clear run headers → segment by timestamp gaps
        starts, prev = [0], None
        for i, t in enumerate(times):
            if t is None:
                continue
            if prev is not None and (t - prev).total_seconds() > gap:
                starts.append(i)
            prev = t
    starts = sorted(set([0] + starts))

    bounds = starts + [len(lines)]
    runs = []
    for a, b in zip(bounds, bounds[1:]):
        if b <= a:
            continue
        seg_times = [t for t in times[a:b] if t is not None]
        start = seg_times[0] if seg_times else None
        end = seg_times[-1] if seg_times else None
        secs = (end - start).total_seconds() if (start and end and end >= start) else None
        runs.append({"outcome": _run_outcome(lines[a:b]), "seconds": secs,
                     "end": end or start, "nlines": b - a})
    return runs[-max_runs:]


def _run_chart(runs):
    """A Databricks-style run-history bar chart: one bar per recent run, height ∝ processing
    time, colored by success/failure. Clients stay dumb — heights are pre-normalized to 0..100
    and colors are theme tokens. Returns {bars, captionFormatted, hasData, emptyFormatted}."""
    durs = [r["seconds"] for r in runs if r["seconds"] is not None]
    dmax = max(durs) if durs else 0
    color = {"ok": "$up", "fail": "$down", "unknown": "$textSecondary"}
    bars = []
    for r in runs:
        secs = r["seconds"]
        if secs is not None and dmax > 0:
            h = 12 + 88 * (secs / dmax)     # floor at 12% so even a fast run stays visible
        else:
            h = 55                           # no timing available → uniform bar; color carries it
        bars.append({
            "heightPct": round(h, 1),
            "color": color.get(r["outcome"], "$textSecondary"),
            "outcome": r["outcome"],
            "labelFormatted": _dur_short(secs) if secs is not None else "·",
            "captionFormatted": r["end"].strftime("%H:%M") if r["end"] else "",
        })
    n_ok = sum(1 for r in runs if r["outcome"] == "ok")
    n_fail = sum(1 for r in runs if r["outcome"] == "fail")
    parts = []
    if runs:
        parts.append(f"{len(runs)} run" + ("s" if len(runs) != 1 else ""))
        if n_ok:
            parts.append(f"{n_ok} ok")
        if n_fail:
            parts.append(f"{n_fail} failed")
        if durs:
            parts.append(f"avg {_dur_short(sum(durs) / len(durs))}")
    return {"bars": bars, "hasData": bool(bars),
            "captionFormatted": " · ".join(parts),
            "emptyFormatted": "" if bars else "No distinct runs detected in this window"}


def _log_status(tail, mtime, now):
    """Classify a log tail as Running / Failed / OK / idle. Returns (label, color, rank) where
    lower rank sorts first (running, failed, ok, idle). The verdict uses decisive terminal
    markers (exit codes, FAILED/SUCCESS, traceback) via _run_outcome — so a retried-then-passed
    run reads OK and a stray "error" in a healthy run doesn't flip it to Failed."""
    lines = [l for l in (tail or "").splitlines() if l.strip()]
    if not lines:
        return ("—", "#9aa4c4", 3)
    last = lines[-12:]  # the most recent lines reflect current state
    outcome = _run_outcome(last)
    if outcome == "fail":
        return ("Failed", "down", 1)
    if any(_LOG_RUN_RE.search(l) for l in last) and outcome != "ok":
        recent = bool(mtime) and (now - mtime).total_seconds() < 600
        return ("Running" if recent else "Running?", "#6ea8fe", 0)
    if outcome == "ok":
        return ("OK", "up", 2)
    return ("—", "#9aa4c4", 3)


# Filter keys in display order, indexed by a file's status rank (running/failed/ok/idle).
_LOG_STATUS_KEYS = [("running", "Running"), ("failed", "Failed"), ("ok", "OK"), ("idle", "Idle")]
# Anomaly signals for the 24h detail view: reuse the failure regex, add warnings.
_LOG_WARN_RE = _re.compile(r"\bwarn(?:ing)?\b|\bdeprecat|\bretry(?:ing)?\b", _re.I)
_LOG_TS_RE = _re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def get_logs(status_param=None):
    """Tail the Mac's ~/log directory: the newest log files and the last lines of each,
    flagged Running / Failed / OK / idle and sorted by that then most-recently-updated.
    An optional ?status= multi-select narrows the list; each card links to a 24h detail."""
    d = os.path.expanduser(os.environ.get("LOG_DIR") or "~/log")
    if not os.path.isdir(d):
        raise FileNotFoundError(f"log dir not found: {d} (set LOG_DIR)")
    now = datetime.datetime.now(datetime.timezone.utc)
    max_files = _int_env("LOG_MAX_FILES", 8)
    tail_n = _int_env("LOG_TAIL_LINES", 40)
    # Scan a deeper tail than we DISPLAY so the run-history chart sees several iterations back;
    # the visible code block still shows only the last tail_n lines.
    scan_n = max(tail_n, _int_env("LOG_RUN_SCAN_LINES", 400))
    runs_n = _int_env("LOG_RUN_HISTORY", 8)
    exts = (".log", ".out", ".err", ".txt")

    paths = []
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if os.path.isfile(p) and name.lower().endswith(exts) and not name.startswith("."):
            paths.append(p)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    files = []
    for p in paths[:max_files]:
        st = os.stat(p)
        name = os.path.basename(p)
        mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
        try:
            scanned = _tail_lines(p, scan_n)
        except Exception as exc:  # noqa: BLE001
            scanned = [f"(could not read: {exc})"]
        tail = "\n".join(scanned[-tail_n:])
        status_fmt, status_color, rank = _log_status(tail, mtime, now)
        files.append({
            "name": name,
            "metaFormatted": f"{_human_size(st.st_size)} · {_ago(mtime, now)}",
            "statusFormatted": status_fmt,
            "statusColor": status_color,
            "statusKey": _LOG_STATUS_KEYS[rank][0],
            "detailHref": "/screen/logfile?file=" + _urlparse.quote(name),
            "tail": tail or "(empty)",
            "runChart": _run_chart(_parse_runs(scanned, max_runs=runs_n)),
            "_rank": rank,
            "_mtime": st.st_mtime,
        })
    # Sort by status (running, failed, ok, idle) then most-recently-updated within each.
    files.sort(key=lambda f: (f["_rank"], -f["_mtime"]))
    for f in files:
        f.pop("_rank", None)
        f.pop("_mtime", None)

    # Per-status filter chips (toggle a status in/out via ?status=); default = all shown.
    label_for = dict(_LOG_STATUS_KEYS)
    present = [k for k, _ in _LOG_STATUS_KEYS if any(f["statusKey"] == k for f in files)]
    counts = {k: sum(1 for f in files if f["statusKey"] == k) for k in present}
    if status_param is None:
        selected = list(present)
    else:
        want = {s for s in (x.strip() for x in status_param.split(",")) if s}
        selected = [k for k in present if k in want]
    sel = set(selected)

    def _status_href(keys):
        return ("/screen/logs?status=" + _urlparse.quote(",".join(sorted(keys)), safe=",")
                if keys else "/screen/logs?status=")

    chips = []
    for k in present:
        active = k in sel
        toggled = (sel - {k}) if active else (sel | {k})
        chips.append({
            "key": k, "label": label_for[k], "count": counts[k],
            "labelFormatted": f"{label_for[k]} · {counts[k]}",
            "active": active,
            "color": "$accent" if active else "$textSecondary",
            "navHref": _status_href(toggled),
        })

    shown = [f for f in files if f["statusKey"] in sel]
    subtitle = f"{len(shown)} of {len(paths)} logs · {d}" if paths else f"No logs in {d}"
    return {"title": "Logs", "subtitleFormatted": subtitle, "dir": d,
            "statusFilters": chips, "allHref": "/screen/logs",
            "files": shown, "rowCount": len(shown),
            "emptyFormatted": ("No logs match the selected statuses — tap one above."
                               if not shown and files else "")}


def _line_time(line):
    """Best-effort timestamp from the start of a log line (ISO-ish 'YYYY-MM-DD HH:MM:SS')."""
    m = _LOG_TS_RE.search(line[:40])
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def get_logfile(name):
    """One log's recent activity (best-effort last 24h) with anomaly lines flagged — the
    drill-down behind each Logs card's '24h' button. File name is confined to LOG_DIR."""
    d = os.path.expanduser(os.environ.get("LOG_DIR") or "~/log")
    safe = os.path.basename(name or "")  # never escape the log dir
    path = os.path.join(d, safe)
    if not safe or not os.path.isfile(path):
        return {"title": "Log", "subtitleFormatted": f"not found: {safe}", "backHref": "/screen/logs",
                "code": "", "anomalies": [], "rowCount": 0, "error": f"{safe} not found"}
    now = datetime.datetime.now(datetime.timezone.utc)
    st = os.stat(path)
    mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
    budget = _int_env("LOG_DETAIL_BYTES", 262144)
    max_lines = _int_env("LOG_DETAIL_LINES", 1500)
    try:
        lines = _retry_os(lambda: _tail_lines(path, max_lines, budget=budget))
    except Exception as exc:  # noqa: BLE001
        return {"title": safe, "subtitleFormatted": f"could not read: {exc}", "backHref": "/screen/logs",
                "code": "", "anomalies": [], "rowCount": 0, "error": str(exc)}

    # Trim to the last 24h when lines carry timestamps (best-effort; undated lines kept).
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)  # naive local, matches log clocks
    start = 0
    for i in range(len(lines) - 1, -1, -1):
        t = _line_time(lines[i])
        if t is not None and t < cutoff:
            start = i + 1
            break
    window = lines[start:]
    dated = any(_line_time(l) for l in window)

    # Flag anomalies: failure lines (win) then warnings, excluding "0 errors"/"no error".
    anomalies = []
    for l in window:
        if _LOG_FAIL_RE.search(l) and not _LOG_FAIL_NEG_RE.search(l):
            anomalies.append({"lineFormatted": l[:300], "color": "down"})
        elif _LOG_WARN_RE.search(l):
            anomalies.append({"lineFormatted": l[:300], "color": "#ffd166"})
    anomalies = anomalies[-30:]  # most recent

    status_fmt, status_color, _rank = _log_status("\n".join(window[-12:]), mtime, now)
    span = "last 24h" if dated else "recent"
    run_chart = _run_chart(_parse_runs(window, max_runs=_int_env("LOG_RUN_HISTORY", 8)))
    return {
        "title": safe,
        "subtitleFormatted": f"{_human_size(st.st_size)} · {_ago(mtime, now)} · {span} · {len(window)} lines",
        "statusFormatted": status_fmt, "statusColor": status_color,
        "runChart": run_chart,
        "anomalyCountFormatted": (f"{len(anomalies)} anomalies flagged" if anomalies
                                  else "No anomalies in this window"),
        "anomalies": anomalies,
        "code": "\n".join(window[-400:]) or "(empty)",
        "backHref": "/screen/logs",
        "rowCount": len(anomalies),
    }


# ---------------------------------------------------------------- /afm_health

# Back-off / retry phase signals (distinct from a hard failure): the job paused and will retry.
_LOG_BACKOFF_RE = _re.compile(
    r"back(?:ing)?[\s-]*off|retry(?:ing)?|rate[\s-]?limit(?:ed|ing)?|too many requests|"
    r"\b429\b|throttl|\bwait(?:ing)?\s+\d|\bsleep(?:ing)?\s+\d|exponential", _re.I)
_BACKOFF_SECS_RE = _re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s(?:ec(?:onds?)?)?|m(?:in(?:utes?)?)?)\b", _re.I)


def _backoff_seconds(line):
    """Best-effort seconds a back-off line waited ('retry in 30s', 'sleeping 2m', 'wait 500ms')."""
    m = _BACKOFF_SECS_RE.search(line)
    if not m:
        return None
    v = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    if unit.startswith("ms"):
        return v / 1000.0
    if unit.startswith("m"):
        return v * 60.0
    return v


def _afm_live_log_path():
    """Locate the afm_live log: AFM_LIVE_LOG wins (absolute path, or a basename under LOG_DIR);
    otherwise glob LOG_DIR for the newest 'afm*live*' log-ish file. Returns a path or None."""
    d = os.path.expanduser(os.environ.get("LOG_DIR") or "~/log")
    env = os.environ.get("AFM_LIVE_LOG")
    if env:
        p = os.path.expanduser(env if os.path.isabs(env) else os.path.join(d, env))
        return p if os.path.isfile(p) else None
    if not os.path.isdir(d):
        return None
    exts = (".log", ".out", ".err", ".txt")
    cands = [os.path.join(d, n) for n in os.listdir(d)
             if not n.startswith(".") and n.lower().endswith(exts)
             and "afm" in n.lower() and "live" in n.lower()]
    if not cands:
        return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


def _afm_run_records(lines, gap_min=None):
    """Segment log lines into runs with PRECISE start/end + per-run detail (outcome, failure
    reason, back-off phases). Same boundary logic as _parse_runs, but keeps each run's lines so
    the timeline can show exactly when it ran, why it failed, and how it backed off."""
    lines = [l for l in (lines or []) if l.strip()]
    if not lines:
        return []
    gap = (gap_min if gap_min is not None else _int_env("LOG_RUN_GAP_MIN", 5)) * 60
    times = [_line_time(l) for l in lines]
    starts = [i for i, l in enumerate(lines) if _LOG_RUN_START_RE.search(l)]
    if len(starts) < 2:                      # no run headers → segment by timestamp gaps
        starts, prev = [0], None
        for i, t in enumerate(times):
            if t is None:
                continue
            if prev is not None and (t - prev).total_seconds() > gap:
                starts.append(i)
            prev = t
    starts = sorted(set([0] + starts))
    bounds = starts + [len(lines)]
    runs = []
    for a, b in zip(bounds, bounds[1:]):
        if b <= a:
            continue
        seg = lines[a:b]
        seg_times = [t for t in times[a:b] if t is not None]
        start = seg_times[0] if seg_times else None
        end = seg_times[-1] if seg_times else None
        secs = (end - start).total_seconds() if (start and end and end >= start) else None
        outcome = _run_outcome(seg)
        reason = ""
        if outcome == "fail":                # the last decisive/failing line is the reason
            for l in reversed(seg):
                sig = _line_signal(l)
                if sig and sig[0] == "fail":
                    reason = l.strip()[:200]
                    break
        backoffs = [{"line": l.strip()[:200], "secs": _backoff_seconds(l)}
                    for l in seg if _LOG_BACKOFF_RE.search(l)]
        runs.append({"outcome": outcome, "seconds": secs, "start": start, "end": end or start,
                     "nlines": b - a, "reason": reason, "backoffs": backoffs})
    return runs


def get_afm_health():
    """A precise per-run timeline of the afm_live job over the past day: exactly when each run
    succeeded or failed, plus the back-off/retry phases inside failing runs — so intermittent
    issues stand out from a flat outage. Reads the afm_live log (AFM_LIVE_LOG / LOG_DIR)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    empty_chart = {"bars": [], "hasData": False, "captionFormatted": "", "emptyFormatted": ""}
    path = _afm_live_log_path()
    if not path:
        d = os.path.expanduser(os.environ.get("LOG_DIR") or "~/log")
        return {"title": "Live Health", "subtitleFormatted": "afm_live log not found",
                "statusFormatted": "No log", "statusColor": "down", "summaryFormatted": "",
                "runChart": empty_chart, "runs": [], "rowCount": 0,
                "emptyFormatted": (f"Couldn't find the afm_live log. Set AFM_LIVE_LOG to its path, "
                                   f"or place an afm_live*.log under {d}.")}
    st = os.stat(path)
    mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
    try:
        lines = _retry_os(lambda: _tail_lines(path, _int_env("LOG_DETAIL_LINES", 1500),
                                              budget=_int_env("LOG_DETAIL_BYTES", 262144)))
    except Exception as exc:  # noqa: BLE001
        return {"title": "Live Health", "subtitleFormatted": f"could not read: {exc}",
                "statusFormatted": "Error", "statusColor": "down", "summaryFormatted": "",
                "runChart": empty_chart, "runs": [], "rowCount": 0, "emptyFormatted": str(exc)}

    # Trim to the last 24h using line timestamps (naive local, matching the log's own clock).
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)
    start_idx = 0
    for i in range(len(lines) - 1, -1, -1):
        t = _line_time(lines[i])
        if t is not None and t < cutoff:
            start_idx = i + 1
            break
    window = lines[start_idx:]
    records = _afm_run_records(window)

    n = len(records)
    n_ok = sum(1 for r in records if r["outcome"] == "ok")
    n_fail = sum(1 for r in records if r["outcome"] == "fail")
    n_back = sum(len(r["backoffs"]) for r in records)
    fail_pct = round(100 * n_fail / n) if n else 0
    if n == 0:
        status, scolor = "No runs", "#9aa4c4"
    elif n_fail == 0:
        status, scolor = f"Healthy — {n_ok}/{n} ok", "up"
    elif n_ok == 0:
        status, scolor = f"Down — {n_fail}/{n} failed", "down"
    else:                                    # successes AND failures interleaved = intermittent
        status, scolor = f"Intermittent — {n_fail}/{n} failed ({fail_pct}%)", "#ffd166"

    # The bar chart wants oldest→newest; the timeline reads newest-first.
    chart = _run_chart([{"outcome": r["outcome"], "seconds": r["seconds"], "end": r["end"]}
                        for r in records][-_int_env("LOG_RUN_HISTORY", 12):])

    def _fmt_t(t):
        return t.strftime("%-I:%M:%S %p") if t else "—"

    rows = []
    for r in reversed(records):              # newest first
        label, color = {"ok": ("OK", "up"), "fail": ("Failed", "down")}.get(
            r["outcome"], ("—", "#9aa4c4"))
        span = _fmt_t(r["start"])
        if r["start"] and r["end"] and r["end"] != r["start"]:
            span = f"{_fmt_t(r['start'])} – {_fmt_t(r['end'])}"
        meta = []
        if r["seconds"] is not None:
            meta.append(_dur_short(r["seconds"]))
        if r["backoffs"]:
            btot = sum(b["secs"] for b in r["backoffs"] if b["secs"])
            meta.append(f"{len(r['backoffs'])} back-off" + ("s" if len(r["backoffs"]) != 1 else "")
                        + (f" · {_dur_short(btot)}" if btot else ""))
        meta.append(f"{r['nlines']} lines")
        boff = [{"lineFormatted": "· " + b["line"]} for b in r["backoffs"]]
        rows.append({
            "timeFormatted": span,
            "outcomeFormatted": label, "color": color,
            "metaFormatted": " · ".join(meta),
            "reasonFormatted": r["reason"],
            "backoffLabel": (f"{len(boff)} back-off phase" + ("s" if len(boff) != 1 else "")
                             if boff else ""),
            "backoffs": boff,
        })

    dated = any(_line_time(l) for l in window)
    return {
        "title": "Live Health",
        "subtitleFormatted": (f"afm_live · {os.path.basename(path)} · "
                              f"{'last 24h' if dated else 'recent'} · updated {_ago(mtime, now)}"),
        "statusFormatted": status, "statusColor": scolor,
        "summaryFormatted": (f"{n} runs · {n_ok} ok · {n_fail} failed"
                             + (f" · {n_back} back-off phases" if n_back else "")),
        "runChart": chart,
        "runs": rows, "rowCount": len(rows),
        "emptyFormatted": "" if rows else "No afm_live runs detected in the last 24h.",
    }


# ---------------------------------------------------------------- /repos

def _git(repo, *args, timeout=10):
    import subprocess
    try:
        out = subprocess.run(["git", "-C", repo, *args],
                             capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _schedrunner_dir():
    return os.path.expanduser(os.environ.get("SCHEDRUNNER_DIR") or "~/Dropbox/Source/schedrunner")


def _repos_dir():
    """Root the Repos page scans for git repos. Defaults to ~/Dropbox/Source (all
    projects), overridable with REPOS_DIR."""
    return os.path.expanduser(os.environ.get("REPOS_DIR") or "~/Dropbox/Source")


# Directories never worth descending into while hunting for repos.
_REPO_SKIP_DIRS = {
    "node_modules", ".venv", "venv", "env", "__pycache__", "Pods", "Carthage",
    "build", ".build", "dist", "DerivedData", ".next", ".cache", "vendor", ".tox",
}


def _find_git_repos(root, max_depth=3):
    """All git working trees under `root`, depth-bounded. A directory containing a
    `.git` is a repo and we don't descend into it; hidden/heavy dirs are skipped."""
    found = []

    def walk(d, depth):
        if os.path.isdir(os.path.join(d, ".git")):
            found.append(d)
            return  # a repo — don't recurse into it
        if depth >= max_depth:
            return
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        for name in names:
            if name.startswith(".") or name in _REPO_SKIP_DIRS:
                continue
            p = os.path.join(d, name)
            if os.path.isdir(p) and not os.path.islink(p):
                walk(p, depth + 1)

    walk(os.path.abspath(root), 0)
    return found


def _discover_repos(base):
    """Repos to show: an explicit config list if present (repos.json/.txt with paths
    or names), else every git repo found under `base` (recursive, depth-bounded)."""
    for cfgname in ("repos.json", "config.json", "schedrunner.json"):
        cfg = os.path.join(base, cfgname)
        if os.path.isfile(cfg):
            try:
                data = json.load(open(cfg))
                items = data.get("repos") if isinstance(data, dict) else data
                paths = []
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, str):
                            paths.append(it)
                        elif isinstance(it, dict):
                            paths.append(it.get("path") or it.get("repo") or it.get("dir") or it.get("name"))
                elif isinstance(items, dict):
                    paths = list(items.values())
                resolved = [os.path.expanduser(p if os.path.isabs(p) else os.path.join(base, p))
                            for p in paths if p]
                if resolved:
                    return resolved
            except Exception:  # noqa: BLE001
                pass
    for cfgname in ("repos.txt", "repos"):
        cfg = os.path.join(base, cfgname)
        if os.path.isfile(cfg):
            resolved = []
            for line in open(cfg):
                line = line.strip()
                if line and not line.startswith("#"):
                    resolved.append(os.path.expanduser(line if os.path.isabs(line) else os.path.join(base, line)))
            if resolved:
                return resolved
    return _find_git_repos(base)


def _deploy_status(repo, ahead, behind, clean, now):
    """Deploy state from a marker file the deploy tooling may leave, else derived
    from git (clean + in sync = deployed; ahead/dirty = needs deploy)."""
    for marker in (".last_deploy", ".deployed_at", ".schedrunner_deploy"):
        p = os.path.join(repo, marker)
        if os.path.isfile(p):
            try:
                txt = open(p).read().strip()
            except Exception:  # noqa: BLE001
                txt = ""
            dt = _parse_iso_or_epoch(txt) or _parse_iso_or_epoch(os.path.getmtime(p))
            stale = ahead > 0 or not clean
            base = f"deployed {_ago(dt, now)}" if dt else "deployed"
            return (base + (" · changes since" if stale else ""), "down" if stale else "up")
    if not clean:
        return ("uncommitted changes", "down")
    if ahead > 0:
        return (f"{ahead} undeployed commit{'s' if ahead != 1 else ''}", "down")
    if behind > 0:
        return ("behind origin", "down")
    return ("in sync", "up")


def _repo_commits(repo, now, n=5):
    """The last `n` commits as {msgFormatted, metaFormatted} for an at-a-glance history
    (shown in a collapsible section per repo)."""
    raw = _git(repo, "log", f"-{n}", "--format=%h%x1f%s%x1f%cI%x1f%an")
    commits = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 4:
            continue
        sha, subject, iso, author = parts
        dt = _parse_iso_or_epoch(iso)
        when = _ago(dt, now) if dt else ""
        commits.append({
            "msgFormatted": f"{sha} {subject}"[:80],
            "metaFormatted": " · ".join(x for x in (author, when) if x),
        })
    return commits


def _ci_status(repo, now):
    """Best-effort latest GitHub Actions conclusion for a repo. Opt-in: only runs when
    REPOS_GH_TOKEN (or GITHUB_TOKEN) is set, so /repos stays local-only and fast by
    default. Returns (formatted, direction) or None; never raises."""
    token = os.environ.get("REPOS_GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    try:
        origin = _git(repo, "remote", "get-url", "origin")
        m = _re.search(r"github\.com[:/]+([^/]+?)/(.+?)(?:\.git)?/?$", origin or "")
        if not m:
            return None
        owner, name = m.group(1), m.group(2)
        url = f"https://api.github.com/repos/{owner}/{name}/actions/runs?per_page=1"
        req = _urlreq.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ios-shell-__APP_NAME_LOWER__",
        })
        with _urlreq.urlopen(req, timeout=3) as resp:
            data = _json.load(resp)
        runs = data.get("workflow_runs") or []
        if not runs:
            return None
        run = runs[0]
        concl = (run.get("conclusion") or run.get("status") or "").lower()
        when = _parse_iso_or_epoch(run.get("updated_at") or run.get("created_at"))
        label = {"success": "CI ✓", "failure": "CI ✗", "cancelled": "CI cancelled",
                 "timed_out": "CI timeout", "in_progress": "CI running",
                 "queued": "CI queued"}.get(concl, f"CI {concl}")
        if when:
            label += f" · {_ago(when, now)}"
        direction = ("up" if concl == "success"
                     else "down" if concl in ("failure", "cancelled", "timed_out") else "#9aa4c4")
        return (label, direction)
    except Exception:  # noqa: BLE001
        return None


def _gh_token():
    return os.environ.get("REPOS_GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _gh_owner_name(repo):
    """(owner, name) parsed from the repo's origin remote, or None."""
    origin = _git(repo, "remote", "get-url", "origin")
    m = _re.search(r"github\.com[:/]+([^/]+?)/(.+?)(?:\.git)?/?$", origin or "")
    return (m.group(1), m.group(2)) if m else None


def _gh_request(method, url, token, body=None, timeout=5):
    """Best-effort GitHub API call. Returns (status, parsed_json|None); never raises."""
    data = _json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ios-shell-__APP_NAME_LOWER__",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = _urlreq.Request(url, data=data, method=method, headers=headers)
    try:
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (_json.loads(raw) if raw else {})
    except _urlreq.HTTPError as e:
        try:
            return e.code, _json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, None
    except Exception:  # noqa: BLE001
        return 0, None


def _ship_query(owner, name, branch, base, pr):
    return _urlparse.urlencode({"owner": owner, "name": name, "branch": branch,
                                "base": base or "main", "pr": pr if pr else ""})


def _ship_href(owner, name, branch, base, pr):
    """Deep link to the ship-confirm screen (kept as a fallback)."""
    return "/screen/repos_ship?" + _ship_query(owner, name, branch, base, pr)


def _ship_post_href(owner, name, branch, base, pr):
    """1-click POST target: create PR (if needed) → merge → delete branch, in one tap."""
    return "/repos_pr?" + _ship_query(owner, name, branch, base, pr)


def _rebase_post_href(owner, name, branch, base, pr):
    """Ship POST that first brings the branch up to date with base (rebase/update) then merges —
    the fix offered when a plain ship would fail because the branch is behind its base."""
    return "/repos_pr?" + _ship_query(owner, name, branch, base, pr) + "&rebase=1"


def _rebase_affordance(owner, name, branch, base, pr, behind):
    """A 0-or-1-element list the template repeats over so a "Rebase & ship" button shows ONLY
    when the branch is behind its base (i.e. a rebase can/should be applied), plus a human note.
    Returns (rebaseActions, behindNote)."""
    if not behind or behind <= 0:
        return ([], "")
    return ([{"labelFormatted": f"Rebase onto {base} & ship",
              "postHref": _rebase_post_href(owner, name, branch, base, pr)}],
            f"↓{behind} behind {base} — rebase recommended before shipping")


def _pr_mergeability(api, token, pr_num):
    """(mergeable: bool|None, state: str) for a PR — 'clean' | 'dirty' (real conflict) |
    'behind' | 'blocked' | 'unknown'. mergeable is None while GitHub is still computing it."""
    st, pr = _gh_request("GET", f"{api}/pulls/{pr_num}", token)
    if st != 200 or not isinstance(pr, dict):
        return (None, "unknown")
    return (pr.get("mergeable"), pr.get("mergeable_state") or "unknown")


def _update_pr_branch(api, token, pr_num):
    """Bring a PR's head branch up to date with its base — GitHub's 'Update branch' (merges the
    latest base into the head). Non-destructive (no force-push); clears a 'behind'/out-of-date
    block so the merge can proceed. A true content conflict returns 422 → must be resolved by
    hand. Returns (ok, msg)."""
    st, body = _gh_request("PUT", f"{api}/pulls/{pr_num}/update-branch", token, body={})
    if st in (200, 202):
        return (True, (body or {}).get("message") or "branch updated")
    return (False, (body or {}).get("message") or f"update-branch failed (HTTP {st})")


def _repo_prs_and_branches(repo, deadline):
    """Open PRs + unmerged branches (those WITHOUT an open PR) for a repo, via the GitHub API.
    Best-effort; returns (prs, branches) — empty on any error or no token."""
    token = _gh_token()
    on = _gh_owner_name(repo)
    if not token or not on:
        return ([], [])
    owner, name = on
    api = f"https://api.github.com/repos/{owner}/{name}"
    _st, repo_info = _gh_request("GET", api, token)
    default_branch = (repo_info or {}).get("default_branch") or "main"
    # Open PRs (cheap — one call).
    _st, pulls = _gh_request("GET", f"{api}/pulls?state=open&per_page=20", token)
    prs, pr_heads = [], set()
    for p in (pulls or []):
        head = (p.get("head") or {}).get("ref") or ""
        base = (p.get("base") or {}).get("ref") or default_branch
        pr_heads.add(head)
        # How far behind base is this PR? (a rebase is advisable when > 0). Bounded by the budget.
        behind = 0
        if not deadline or _time.monotonic() < deadline:
            _st, cmpp = _gh_request("GET", f"{api}/compare/{base}...{head}", token)
            behind = (cmpp or {}).get("behind_by") or 0
        rebase_actions, behind_note = _rebase_affordance(owner, name, head, base, p.get("number"), behind)
        prs.append({
            "number": p.get("number"),
            "labelFormatted": f"#{p.get('number')} · {p.get('title') or head}",
            "metaFormatted": f"{head} → {base}",
            "shipHref": _ship_href(owner, name, head, base, p.get("number")),
            "shipPostHref": _ship_post_href(owner, name, head, base, p.get("number")),
            "rebaseActions": rebase_actions,
            "behindNote": behind_note,
        })
    # Unmerged branches with no open PR (so you can open + ship one). A compare call per
    # branch is bounded by the shared CI/GitHub time budget.
    branches = []
    _st, br = _gh_request("GET", f"{api}/branches?per_page=50", token)
    for b in (br or []):
        if deadline and _time.monotonic() > deadline:
            break
        bn = b.get("name") or ""
        if not bn or bn == default_branch or bn in pr_heads:
            continue
        _st, cmp = _gh_request("GET", f"{api}/compare/{default_branch}...{bn}", token)
        ahead = (cmp or {}).get("ahead_by") or 0
        behind = (cmp or {}).get("behind_by") or 0
        if ahead > 0:
            rebase_actions, behind_note = _rebase_affordance(owner, name, bn, default_branch, "", behind)
            branches.append({
                "name": bn,
                "labelFormatted": bn,
                "metaFormatted": f"↑{ahead} unmerged → {default_branch}"
                                 + (f" · ↓{behind} behind" if behind else ""),
                "shipHref": _ship_href(owner, name, bn, default_branch, ""),
                "shipPostHref": _ship_post_href(owner, name, bn, default_branch, ""),
                "rebaseActions": rebase_actions,
                "behindNote": behind_note,
            })
    return (prs, branches)


def get_repos():
    """Git + deploy status of every repo under ~/Dropbox/Source (REPOS_DIR), newest commit
    first, each with its last 5 commits, (opt-in) latest CI conclusion, and (opt-in) open PRs
    + unmerged branches from GitHub."""
    base = _repos_dir()
    if not os.path.isdir(base):
        raise FileNotFoundError(f"repos dir not found: {base} (set REPOS_DIR)")
    now = datetime.datetime.now(datetime.timezone.utc)
    # CI is opt-in (needs a GitHub token) and bounded by a time budget so a slow API never
    # stalls the page; when off, /repos stays purely local.
    ci_on = bool(os.environ.get("REPOS_GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    ci_deadline = (_time.monotonic() + _int_env("REPOS_CI_BUDGET", 8)) if ci_on else None
    repos = []
    for repo in sorted(_discover_repos(base), key=lambda p: os.path.basename(p.rstrip("/")).lower()):
        if not os.path.isdir(repo):
            continue
        name = os.path.basename(repo.rstrip("/")) or repo
        if not os.path.isdir(os.path.join(repo, ".git")):
            repos.append({"name": name, "branch": "—", "gitFormatted": "not a git repo",
                          "gitDirection": "down", "deployFormatted": "—",
                          "deployDirection": "down", "lastFormatted": "", "_sortTs": 0})
            continue
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
        dirty_n = len([l for l in _git(repo, "status", "--porcelain").splitlines() if l.strip()])
        ahead = behind = 0
        counts = _git(repo, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
        if counts and ("\t" in counts or " " in counts):
            parts = counts.replace("\t", " ").split()
            if len(parts) >= 2:
                behind, ahead = int(parts[0] or 0), int(parts[1] or 0)
        clean = dirty_n == 0
        bits = []
        if dirty_n:
            bits.append(f"{dirty_n} uncommitted")
        if ahead:
            bits.append(f"↑{ahead}")
        if behind:
            bits.append(f"↓{behind}")
        git_status = "clean" if not bits else ", ".join(bits)
        git_dir = "up" if (clean and ahead == 0 and behind == 0) else "down"
        last_dt = _parse_iso_or_epoch(_git(repo, "log", "-1", "--format=%cI"))
        last_msg = _git(repo, "log", "-1", "--format=%s")
        last_fmt = " · ".join(x for x in (_ago(last_dt, now) if last_dt else "", last_msg[:60]) if x)
        deploy_fmt, deploy_dir = _deploy_status(repo, ahead, behind, clean, now)
        entry = {
            "name": name, "branch": branch,
            "gitFormatted": git_status, "gitDirection": git_dir,
            "deployFormatted": deploy_fmt, "deployDirection": deploy_dir,
            "lastFormatted": last_fmt,
            "_sortTs": last_dt.timestamp() if last_dt else 0,
        }
        commits = _repo_commits(repo, now)
        if commits:
            entry["commits"] = commits
            entry["commitsLabel"] = f"{len(commits)} recent commit" + ("s" if len(commits) != 1 else "")
        if ci_deadline and _time.monotonic() < ci_deadline:
            ci = _ci_status(repo, now)
            if ci:
                entry["ciFormatted"], entry["ciDirection"] = ci
            prs, branches = _repo_prs_and_branches(repo, ci_deadline)
            if prs:
                entry["prs"] = prs
                entry["prsLabel"] = f"{len(prs)} open PR" + ("s" if len(prs) != 1 else "")
            if branches:
                entry["branches"] = branches
                entry["branchesLabel"] = f"{len(branches)} unmerged branch" + ("es" if len(branches) != 1 else "")
            # ONE unified ship list — open PRs + unmerged branches — each with a single "Ship"
            # action (the same create-PR-if-needed → merge → delete-branch). The UI renders this
            # instead of two separate PR / branch sections with different button labels.
            ship_items = (prs or []) + (branches or [])
            if ship_items:
                entry["shipItems"] = ship_items
                entry["shipLabel"] = f"{len(ship_items)} to ship"
        repos.append(entry)
    # Most-recently-updated repos first.
    repos.sort(key=lambda r: r.get("_sortTs", 0), reverse=True)
    for r in repos:
        r.pop("_sortTs", None)
    subtitle = f"{len(repos)} repos · {base}" if repos else f"No repos under {base}"
    return {"title": "Repos", "subtitleFormatted": subtitle, "dir": base,
            "repos": repos, "rowCount": len(repos)}


def _self_repo_slug():
    """Repo to surface on the __APP_NAME_LOWER__ banner: DASHBOARD_PR_REPO (owner/name), else the git
    origin of the repo this sidecar runs in."""
    s = os.environ.get("DASHBOARD_PR_REPO")
    if s and "/" in s:
        return s.strip()
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    origin = _git(repo_root, "remote", "get-url", "origin")
    m = _re.search(r"github\.com[:/]+([^/]+?)/(.+?)(?:\.git)?/?$", origin or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _gh_job_log_tail(api, job_id, token, tail=40, cap=1_500_000):
    """Plain-text tail of a GitHub Actions job's log (urllib follows the 302 to the log blob).
    Bounded read; returns '' on any error. Strips the leading ISO timestamps GH prefixes."""
    try:
        req = _urlreq.Request(f"{api}/actions/jobs/{job_id}/logs", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "User-Agent": "ios-shell-__APP_NAME_LOWER__"})
        with _urlreq.urlopen(req, timeout=6) as resp:
            text = resp.read(cap).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for l in text.splitlines():
        # GH prefixes each line with an ISO-8601 timestamp + space; drop it for readability.
        m = _re.match(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s(.*)$", l)
        lines.append(m.group(1) if m else l)
    return "\n".join(lines[-tail:])


def _ci_pipeline(api, token, default_branch, now):
    """The latest CI run for the default branch as sequential STAGES (its jobs), plus the log
    tail of the most relevant job (the failed one, else the running one, else the newest). This
    is what the Deploy page shows for 'each stage it goes through'."""
    out = {"ciStages": [], "ciRunUrl": "", "ciRunLinkFormatted": "", "ciLogFormatted": "", "ciLogLabel": ""}
    st, runs = _gh_request("GET", f"{api}/actions/runs?per_page=1&branch={default_branch}", token)
    run = (runs or {}).get("workflow_runs", [None])[0] if isinstance(runs, dict) else None
    if not run:
        return out
    out["ciRunUrl"] = run.get("html_url") or ""
    out["ciRunLinkFormatted"] = "View run on GitHub →" if out["ciRunUrl"] else ""
    st, jobs = _gh_request("GET", f"{api}/actions/runs/{run.get('id')}/jobs?per_page=30", token)
    job_list = (jobs or {}).get("jobs", []) if isinstance(jobs, dict) else []
    focus = None  # the job whose log we pull
    for j in job_list:
        status = (j.get("status") or "").lower()       # queued / in_progress / completed
        concl = (j.get("conclusion") or "").lower()     # success / failure / cancelled / skipped
        name = j.get("name") or "job"
        if status != "completed":
            label, direction = (f"⏳ {name} · running", "#6ea8fe") if status == "in_progress" \
                else (f"◦ {name} · queued", "#9aa4c4")
        elif concl == "success":
            label, direction = f"✓ {name}", "up"
        elif concl in ("failure", "timed_out"):
            label, direction = f"✗ {name}", "down"
        elif concl in ("cancelled", "skipped"):
            label, direction = f"– {name} · {concl}", "#9aa4c4"
        else:
            label, direction = f"{name} · {concl or status}", "#9aa4c4"
        out["ciStages"].append({"nameFormatted": label, "direction": direction})
        # Prefer a failed job for the log; else the running one; else keep the last.
        if concl in ("failure", "timed_out"):
            focus = j
        elif focus is None and status == "in_progress":
            focus = j
    if focus is None and job_list:
        focus = job_list[-1]
    if focus:
        tail = _gh_job_log_tail(api, focus.get("id"), token)
        if tail:
            out["ciLogFormatted"] = tail
            out["ciLogLabel"] = f"CI log · {focus.get('name') or 'job'} (tail)"
    return out


def get_deploy_repo():
    """CI status + pipeline stages + shippable branches/PRs for THIS repo — the GitHub half of the
    Deploy page. Best-effort and opt-in on REPOS_GH_TOKEN; the Deploy page's server/iOS status
    works without it. Reuses _ci_status, _ci_pipeline (jobs+log), and _repo_prs_and_branches."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the iOS-Shell repo root
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {"ciFormatted": "", "ciDirection": "#9aa4c4", "ciRunning": False,
           "ciStages": [], "ciRunUrl": "", "ciRunLinkFormatted": "", "ciLogFormatted": "", "ciLogLabel": "",
           "shipItems": [], "shipLabel": "", "hasToken": bool(_gh_token())}
    token = _gh_token()
    if not token:
        out["ciFormatted"] = "CI: set REPOS_GH_TOKEN to show status"
        out["shipLabel"] = "Set REPOS_GH_TOKEN to see/ship branches"
        return out
    ci = _ci_status(repo, now)
    if ci:
        out["ciFormatted"], out["ciDirection"] = ci
        out["ciRunning"] = ("running" in ci[0].lower() or "queued" in ci[0].lower())
    else:
        out["ciFormatted"] = "CI: no recent runs"
    on = _gh_owner_name(repo)
    if on:
        api = f"https://api.github.com/repos/{on[0]}/{on[1]}"
        _st, info = _gh_request("GET", api, token)
        default_branch = (info or {}).get("default_branch") or "main"
        try:
            out.update(_ci_pipeline(api, token, default_branch, now))
        except Exception as exc:  # noqa: BLE001
            print(f"[deploy] ci pipeline failed: {exc}", flush=True)
    try:
        deadline = _time.monotonic() + _int_env("REPOS_CI_BUDGET", 8)
        prs, branches = _repo_prs_and_branches(repo, deadline)
        items = (prs or []) + (branches or [])
        out["shipItems"] = items
        out["shipLabel"] = (f"{len(items)} to ship" if items else "Nothing to ship — main is clean")
    except Exception as exc:  # noqa: BLE001
        out["shipLabel"] = f"Couldn't check branches ({exc})"
    return out


def get_repo_banner():
    """A 0-or-1 `cards` list for the __APP_NAME_LOWER__ banner — present when the specific repo has an
    open PR or unmerged branch. Empty (no banner) otherwise / on any error / without a token."""
    slug, token = _self_repo_slug(), _gh_token()
    if not slug or not token or "/" not in slug:
        return {"cards": []}
    owner, name = slug.split("/", 1)
    api = f"https://api.github.com/repos/{owner}/{name}"
    _st, info = _gh_request("GET", api, token)
    default_branch = (info or {}).get("default_branch") or "main"
    _st, pulls = _gh_request("GET", f"{api}/pulls?state=open&per_page=20", token)
    prs = pulls or []
    pr_heads = {(p.get("head") or {}).get("ref") for p in prs}
    branches = []
    deadline = _time.monotonic() + _int_env("REPOS_CI_BUDGET", 8)
    _st, br = _gh_request("GET", f"{api}/branches?per_page=50", token)
    for b in (br or []):
        if _time.monotonic() > deadline:
            break
        bn = b.get("name") or ""
        if not bn or bn == default_branch or bn in pr_heads:
            continue
        _st, cmp = _gh_request("GET", f"{api}/compare/{default_branch}...{bn}", token)
        if (cmp or {}).get("ahead_by", 0) > 0:
            branches.append(bn)
    n_pr, n_br = len(prs), len(branches)
    if n_pr == 0 and n_br == 0:
        return {"cards": []}
    bits = []
    if n_pr:
        bits.append(f"{n_pr} open PR" + ("s" if n_pr != 1 else ""))
    if n_br:
        bits.append(f"{n_br} branch" + ("es" if n_br != 1 else "") + " to merge")
    return {"cards": [{"labelFormatted": f"{name}: " + " · ".join(bits), "navHref": "/screen/repos"}]}


def get_repos_ship(qs):
    """Ship-confirm screen: shows the repo/branch as collected fields + a one-tap submit. The
    fields carry the context to POST /repos_pr (the same key/value submit pattern as config)."""
    def q(k):
        return (qs.get(k, [""])[0] or "").strip()
    owner, name, branch, base, pr = q("owner"), q("name"), q("branch"), q("base") or "main", q("pr")
    action = f"Merge PR #{pr}" if pr else "Create + merge a PR"
    # If we can see the PR is behind its base, proactively offer a "Rebase & ship" here too.
    rebase_actions, behind_note = [], ""
    token = _gh_token()
    if token and owner and name and pr:
        api = f"https://api.github.com/repos/{owner}/{name}"
        _mergeable, state = _pr_mergeability(api, token, pr)
        if state in ("behind", "blocked"):
            rebase_actions = [{"labelFormatted": f"Rebase onto {base} & ship",
                               "postHref": _rebase_post_href(owner, name, branch, base, pr)}]
            behind_note = f"“{branch}” is behind {base} — a rebase is recommended before merging."
    return {
        "title": "Ship branch",
        "subtitleFormatted": f"{owner}/{name}",
        "summaryFormatted": f"{action} for “{branch}” → {base}, then delete the branch.",
        "warnFormatted": "Merges into the base branch and deletes the branch. This can't be undone.",
        "behindNote": behind_note,
        "rebaseActions": rebase_actions,
        "fields": [
            {"key": "owner", "value": owner, "label": "Owner", "type": "string"},
            {"key": "name", "value": name, "label": "Repo", "type": "string"},
            {"key": "branch", "value": branch, "label": "Branch", "type": "string"},
            {"key": "base", "value": base, "label": "Base", "type": "string"},
            {"key": "pr", "value": pr, "label": "PR #", "type": "string"},
        ],
        "backHref": "/screen/repos",
    }


def _ship_result(ok, msg):
    return {"title": "Ship branch", "subtitleFormatted": "", "summaryFormatted": "",
            "warnFormatted": "", "behindNote": "", "rebaseActions": [], "fields": [],
            "backHref": "/screen/repos",
            "statusFormatted": ("✓ " if ok else "✗ ") + msg,
            "statusDirection": "up" if ok else "down"}


def ship_branch(params):
    """Create a PR for `branch` (if none open), merge it (merge commit), then delete the
    branch. Destructive — invoked by the 1-click "Ship" bar (context in the POST query)."""
    g = lambda k: (params.get(k) or "").strip()
    owner, name, branch = g("owner"), g("name"), g("branch")
    base, pr_num = g("base") or "main", g("pr")
    do_rebase = g("rebase").lower() in ("1", "true", "yes", "on")
    token = _gh_token()
    if not token:
        return _ship_result(False, "No GitHub token (set REPOS_GH_TOKEN with repo scope).")
    if not (owner and name and branch):
        return _ship_result(False, "Missing owner/name/branch.")
    api = f"https://api.github.com/repos/{owner}/{name}"
    # 1) Find or create the PR.
    if not pr_num:
        st, existing = _gh_request("GET", f"{api}/pulls?state=open&head={owner}:{branch}", token)
        if existing:
            pr_num = str(existing[0].get("number"))
        else:
            st, created = _gh_request("POST", f"{api}/pulls", token,
                                      body={"title": f"Merge {branch}", "head": branch, "base": base})
            if st not in (200, 201) or not (created or {}).get("number"):
                return _ship_result(False, (created or {}).get("message") or f"create PR failed (HTTP {st})")
            pr_num = str(created.get("number"))
    # 1b) Opt-in rebase: bring the branch up to date with base BEFORE merging (offered after a
    #     "behind base" failure). Non-destructive update; a real content conflict returns 422 and
    #     still needs manual resolution, so we stop and say so rather than force anything.
    if do_rebase:
        ok, msg = _update_pr_branch(api, token, pr_num)
        if not ok:
            return _ship_result(False, f"PR #{pr_num}: couldn't rebase {branch} onto {base} — {msg}. "
                                       f"If it's a real conflict, resolve it locally (git rebase {base}), push, then ship.")
        _time.sleep(2)  # GitHub updates the branch asynchronously — let it settle before merging.
    # 2) Merge (merge commit).
    st, merged = _gh_request("PUT", f"{api}/pulls/{pr_num}/merge", token, body={"merge_method": "merge"})
    if st != 200 or not (merged or {}).get("merged"):
        gh_msg = (merged or {}).get("message") or f"merge failed (HTTP {st})"
        # Diagnose WHY: a branch merely behind base can be rebased & retried; a real conflict can't.
        mergeable, state = _pr_mergeability(api, token, pr_num)
        if state == "dirty":
            return _ship_result(False, f"PR #{pr_num}: merge conflict between {branch} and {base}. "
                                       "A rebase can't auto-resolve this — fix the conflict locally, push, then ship.")
        if not do_rebase and (state in ("behind", "blocked") or mergeable is False or mergeable is None):
            reason = (f"{branch} is behind {base}" if state == "behind"
                      else "the branch is out of date" if state in ("blocked", "unknown")
                      else "GitHub is still checking mergeability")
            res = _ship_result(False, f"PR #{pr_num} didn't merge — {reason}. "
                                      f"A rebase onto {base} should fix it: tap “Rebase & ship”.")
            res["warnFormatted"] = "The Repos row for this branch now shows a “Rebase & ship” button."
            res["rebaseActions"] = [{"labelFormatted": f"Rebase onto {base} & ship",
                                     "postHref": _rebase_post_href(owner, name, branch, base, pr_num)}]
            return res
        return _ship_result(False, f"PR #{pr_num}: {gh_msg}")
    # 3) Delete the branch.
    st, _ = _gh_request("DELETE", f"{api}/git/refs/heads/{branch}", token)
    deleted = st in (200, 204)
    # 4) Reflect the merge in the LOCAL clone so the repos page's commit list + git/deploy status
    #    update on the post-ship refresh — the merge lives on GitHub, but get_repos reads `git log`
    #    from the local clone. Best-effort fast-forward; a dirty/diverged clone is left untouched.
    local = _local_repo_for(owner, name)
    if local:
        # One bounded fast-forward (fetch+merge) so the ship response still returns well within
        # the server's proxy read timeout. Brings the merge commit into the local log when the
        # clone is on the default branch and clean; otherwise a harmless no-op.
        _git(local, "pull", "--ff-only", "--prune", timeout=25)
    prefix = f"Rebased {branch} onto {base}, then merged" if do_rebase else "Merged"
    return _ship_result(True, f"{prefix} PR #{pr_num} and " +
                        (f"deleted {branch}." if deleted else f"kept {branch} (delete it manually)."))


def _local_repo_for(owner, name):
    """Local clone under REPOS_DIR whose GitHub remote is owner/name — so a ship can fast-forward
    it and the repos page reflects the just-merged commit. None if not found."""
    try:
        for repo in _discover_repos(_repos_dir()):
            on = _gh_owner_name(repo)
            if on and on[0].lower() == (owner or "").lower() and on[1].lower() == (name or "").lower():
                return repo
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------- /schedrunner

def _until(dt, now):
    """Relative future time, e.g. 'in 5m' / 'in 2h' / 'in 3d' / 'due'."""
    if not dt:
        return ""
    secs = int((dt - now).total_seconds())
    if secs <= 0:
        return "due"
    if secs < 3600:
        return f"in {secs // 60}m"
    if secs < 86400:
        return f"in {secs // 3600}h"
    return f"in {secs // 86400}d"


def _jobs_from(data):
    """Pull a list of (name, status-dict) out of a flexible status document: a
    {jobs|tasks|runs: [...]} list, a name->status mapping, or a bare list/dict."""
    gen = None
    if isinstance(data, dict):
        gen = data.get("generated_at") or data.get("updated_at") or data.get("ts")
        items = data.get("jobs") or data.get("tasks") or data.get("runs")
        if isinstance(items, list):
            return [(it.get("name") or it.get("job") or it.get("id") or "job", it)
                    for it in items if isinstance(it, dict)], gen
        if isinstance(items, dict):
            return [(k, v) for k, v in items.items() if isinstance(v, dict)], gen
        nested = [(k, v) for k, v in data.items() if isinstance(v, dict)]
        if nested:
            return nested, gen
    if isinstance(data, list):
        return [(it.get("name") or it.get("job") or it.get("id") or "job", it)
                for it in data if isinstance(it, dict)], gen
    return [], gen


def _job_view(name, d, now):
    """Normalize one job's flexible status dict into a display row."""
    last = _parse_iso_or_epoch(d.get("last_run") or d.get("lastRun") or d.get("last")
                               or d.get("finished_at") or d.get("ts") or d.get("time"))
    nxt = _parse_iso_or_epoch(d.get("next_run") or d.get("next") or d.get("nextRun") or d.get("scheduled"))
    raw = d.get("status") or d.get("result") or d.get("state")
    code = next((d[k] for k in ("exit", "exit_code", "code", "rc") if d.get(k) is not None), None)
    ok = None
    if isinstance(d.get("ok"), bool):
        ok = d["ok"]
    elif isinstance(raw, str):
        ok = raw.strip().lower() in ("ok", "success", "succeeded", "passed", "done", "0", "green")
    elif code is not None:
        try:
            ok = int(code) == 0
        except (TypeError, ValueError):
            ok = None
    status_txt = str(raw) if raw is not None else ("ok" if ok else "failed" if ok is False else "unknown")
    sched = d.get("schedule") or d.get("cron") or d.get("every") or ""
    dur = d.get("duration") or d.get("duration_s") or d.get("elapsed")
    msg = d.get("message") or d.get("error") or d.get("last_error") or ""
    meta = []
    if sched:
        meta.append(str(sched))
    if isinstance(dur, (int, float)):
        meta.append(f"{dur:g}s")
    elif dur:
        meta.append(str(dur))
    if msg and ok is False:
        meta.append(str(msg)[:80])
    return {
        "name": str(name),
        "statusFormatted": status_txt,
        "direction": "up" if ok else "down",
        "lastFormatted": ("ran " + _ago(last, now)) if last else "never run",
        "nextFormatted": ("next " + _until(nxt, now)) if nxt else "",
        "metaFormatted": " · ".join(meta),
        "_ok": ok,
    }


def _schedrunner_status_files(base):
    """Find schedrunner's status artifact(s): SCHEDRUNNER_STATUS (file or dir) wins,
    then common single-file names, then a per-job state directory."""
    env = os.environ.get("SCHEDRUNNER_STATUS")
    if env:
        if os.path.isdir(env):
            return sorted(_glob.glob(os.path.join(env, "*.json"))), True
        if os.path.isfile(env):
            return [env], False
    for name in ("status.json", "state.json", "last_run.json", "runs.json", "schedrunner.json"):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return [p], False
    for sub in ("state", ".state", "status", "var", "run"):
        g = sorted(_glob.glob(os.path.join(base, sub, "*.json")))
        if g:
            return g, True
    return [], False


def get_schedrunner():
    """Summarize schedrunner's run status: its scheduled jobs with last/next run and
    pass/fail, read from whatever status file(s) it writes; falls back to tailing a
    schedrunner log so the page is useful even before a status file exists."""
    base = _schedrunner_dir()
    if not os.path.isdir(base):
        raise FileNotFoundError(f"schedrunner dir not found: {base} (set SCHEDRUNNER_DIR)")
    now = datetime.datetime.now(datetime.timezone.utc)

    files, per_job_dir = _schedrunner_status_files(base)
    raw_jobs, gen = [], None
    for p in files:
        try:
            data = json.load(open(p))
        except Exception:  # noqa: BLE001
            continue
        js, g = _jobs_from(data)
        gen = gen or g
        if per_job_dir and not js and isinstance(data, dict):
            raw_jobs.append((os.path.splitext(os.path.basename(p))[0], data))
        else:
            raw_jobs.extend(js)
    jobs = [_job_view(name, d, now) for name, d in raw_jobs]
    jobs.sort(key=lambda j: (j["_ok"] is not False, j["name"].lower()))  # failures first
    failed = sum(1 for j in jobs if j["_ok"] is False)
    okc = sum(1 for j in jobs if j["_ok"] is True)
    for j in jobs:
        j.pop("_ok", None)

    log_tail, log_label = "", ""
    if not jobs:
        cands = (_glob.glob(os.path.join(base, "*.log")) + _glob.glob(os.path.join(base, "logs", "*.log"))
                 + _glob.glob(os.path.expanduser("~/log/sched*")))
        cands = [c for c in cands if os.path.isfile(c)]
        cands.sort(key=os.path.getmtime, reverse=True)
        if cands:
            log_tail = "\n".join(_tail_lines(cands[0], 40))
            log_label = "Recent log · " + os.path.basename(cands[0])

    gen_dt = _parse_iso_or_epoch(gen)
    if jobs:
        status_fmt = "Healthy" if failed == 0 else f"{failed} failing"
        status_dir = "up" if failed == 0 else "down"
        counts = f"{len(jobs)} jobs · {okc} ok" + (f" · {failed} failed" if failed else "")
        subtitle = (f"updated {_ago(gen_dt, now)} · " if gen_dt else "") + base
    elif log_tail:
        status_fmt, status_dir = "Running", "up"
        counts = "no status file — showing the latest log"
        subtitle = base
    else:
        status_fmt, status_dir = "Unknown", "down"
        counts = "no schedrunner status found"
        subtitle = f"set SCHEDRUNNER_STATUS · {base}"

    return {
        "title": "Schedrunner",
        "subtitleFormatted": subtitle,
        "statusFormatted": status_fmt,
        "statusDirection": status_dir,
        "countsFormatted": counts,
        "jobs": jobs,
        "logLabel": log_label,
        "logTail": log_tail,
        "rowCount": len(jobs),
    }


# ---------------------------------------------------------------- /schedlogs

# Wrapper lines schedrunner writes around each run, e.g.
#   [Fri Jun 19 01:39:30 EDT 2026] Running /path/to/script
#   [Fri Jun 19 01:41:02 EDT 2026] Finished /path/to/script
_SCHEDLOG_WRAPPER_RE = _re.compile(
    r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \w+ +\d+ \d{2}:\d{2}:\d{2} \w+ \d{4}\] (Running|Finished) "
)
# Capture the timestamp parts WITHOUT the timezone token: strptime's %Z does not
# reliably parse abbreviations like EDT/EST across machines, so we drop it and
# parse a naive local datetime (both Running and Finished share the same zone, and
# `now` is also naive-local, so durations and ages stay correct).
_SCHEDLOG_TS_RE = _re.compile(r"^\[(\w{3}) (\w{3}) +(\d+) (\d{2}:\d{2}:\d{2}) \w+ (\d{4})\]")


def _schedlog_dt(line):
    m = _SCHEDLOG_TS_RE.match(line)
    if not m:
        return None
    stamp = f"{m.group(1)} {m.group(2)} {int(m.group(3)):02d} {m.group(4)} {m.group(5)}"
    try:
        return datetime.datetime.strptime(stamp, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None


def get_schedlogs():
    """Parse schedrunner's per-script log files into a concise status summary: each
    script's last-run age, duration, OK/FAILED status, and a short output snippet."""
    log_dir = os.path.expanduser(
        os.environ.get("SCHEDRUNNER_LOG_DIR") or "~/Dropbox/Source/schedrunner/log"
    )
    SEPARATOR = "----------------------------------------"
    now = datetime.datetime.now()

    try:
        names = sorted(n for n in os.listdir(log_dir) if n.endswith(".log"))
    except OSError:
        return {"title": "Sched Logs", "subtitleFormatted": f"log dir not found: {log_dir}",
                "entries": [], "rowCount": 0}

    entries = []
    for fname in names:
        path = os.path.join(log_dir, fname)
        label = fname[:-4]  # strip .log
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as exc:
            entries.append({"name": label, "metaFormatted": str(exc), "statusFormatted": "ERR",
                            "statusColor": "down", "snippetFormatted": "", "_rank": 1, "_ts": 0})
            continue

        blocks = [b.strip() for b in content.split(SEPARATOR) if b.strip()]
        if not blocks:
            entries.append({"name": label, "metaFormatted": "no runs", "statusFormatted": "—",
                            "statusColor": "#9aa4c4", "snippetFormatted": "", "_rank": 3, "_ts": 0})
            continue

        lines = blocks[-1].splitlines()
        block = "\n".join(lines)
        running_line = next((l for l in lines if _SCHEDLOG_WRAPPER_RE.match(l) and "Running" in l), None)
        finished_line = next((l for l in reversed(lines) if _SCHEDLOG_WRAPPER_RE.match(l) and "Finished" in l), None)

        # Duration between Running and Finished (same run block).
        duration_str = ""
        if running_line and finished_line:
            t1, t2 = _schedlog_dt(running_line), _schedlog_dt(finished_line)
            if t1 and t2:
                duration_str = f"{max(0, int((t2 - t1).total_seconds()))}s"

        # Last-run age from the most recent wrapper timestamp.
        last_dt = next((_schedlog_dt(l) for l in reversed(lines)
                        if _SCHEDLOG_WRAPPER_RE.match(l) and _schedlog_dt(l)), None)
        ago_str = _ago(last_dt, now) if last_dt else "unknown"

        # Status: FAILED keyword wins; no Finished line ⇒ still running (not a failure).
        if "FAILED" in block:
            status_str, status_color = "FAILED", "down"
        elif finished_line is None:
            status_str, status_color = "running?", "#9aa4c4"
            duration_str = duration_str or "running?"
        else:
            status_str, status_color = "OK", "up"

        # Snippet: the last 3 meaningful lines (skip wrapper boilerplate / blanks).
        meaningful = [l for l in lines if l.strip() and not _SCHEDLOG_WRAPPER_RE.match(l)]
        snippet = "\n".join(meaningful[-3:])

        meta = ago_str + (f" · {duration_str}" if duration_str else "")
        entries.append({
            "name": label,
            "metaFormatted": meta,
            "statusFormatted": status_str,
            "statusColor": status_color,
            "snippetFormatted": snippet,
            "_rank": {"running?": 0, "FAILED": 1, "OK": 2}.get(status_str, 3),
            "_ts": last_dt.timestamp() if last_dt else 0,
        })

    # Sort by status (running, failed, ok, idle) then most-recent within each group.
    entries.sort(key=lambda e: (e.get("_rank", 3), -e.get("_ts", 0)))
    for e in entries:
        e.pop("_rank", None)
        e.pop("_ts", None)

    return {
        "title": "Sched Logs",
        "subtitleFormatted": f"{len(entries)} scripts · {log_dir}",
        "entries": entries,
        "rowCount": len(entries),
    }


# ---------------------------------------------------------------- /docs

def _docs_mirror():
    return os.path.expanduser(os.environ.get("DOCS_MIRROR") or "~/.cache/__APP_NAME_LOWER__/docs")


def _has_visible_entries(d):
    try:
        return any(not n.startswith(".") for n in os.listdir(d))
    except OSError:
        return False


def _docs_dir():
    """Locate the /Private docs root. Prefer the local MIRROR (always readable, even
    when files are 'online-only' in Drive, the Drive app is closed, or we're offline)
    once `make docs` / Deploy/sync_docs.sh has populated it; otherwise read the live
    Google Drive for Desktop mount. DOCS_DIR overrides the live source."""
    mirror = _docs_mirror()
    if os.path.isdir(mirror) and _has_visible_entries(mirror):
        return mirror
    env = os.environ.get("DOCS_DIR")
    cands = [env] if env else []
    home = os.path.expanduser("~")
    for gd in _gdrive_my_drives():
        cands.append(os.path.join(gd, "Private"))
    cands.append(os.path.join(home, "Private"))
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return env


def _safe_join(base, rel):
    """Join rel onto base, confined to base (no ../ escape)."""
    full = os.path.normpath(os.path.join(base, (rel or "").lstrip("/")))
    if full == base or full.startswith(base + os.sep):
        return full
    return base


def _docs_href(rel):
    return "/screen/docs?path=" + _urlparse.quote(rel) if rel else "/screen/docs"


def _count_docs(p):
    try:
        names = _retry_os(lambda: os.listdir(p))  # Drive FS can EDEADLK; a short retry keeps the count honest
        return sum(1 for n in names
                   if not n.startswith(".") and (os.path.isdir(os.path.join(p, n)) or n.lower().endswith(".md")))
    except Exception:  # noqa: BLE001
        return 0


def get_docs(rel):
    """Browse the Google Drive /Private tree: list folders + .md files, or render a
    selected .md file. `rel` is a path relative to /Private (confined to it)."""
    base = _docs_dir()
    if not base or not os.path.isdir(base):
        raise FileNotFoundError("Private docs folder not found (set DOCS_DIR)")
    now = datetime.datetime.now(datetime.timezone.utc)
    target = _safe_join(base, rel)
    rel_norm = os.path.relpath(target, base)
    rel_norm = "" if rel_norm == "." else rel_norm
    parent = os.path.dirname(rel_norm) if rel_norm else ""
    crumbs = "/Private" + ("/" + rel_norm if rel_norm else "")
    at_root = rel_norm == ""
    # At the root, show the actual resolved folder so a mis-resolved Drive mount
    # (e.g. the ~/Private fallback instead of the real My Drive/Private) is obvious.
    if at_root:
        crumbs = f"{crumbs} · {base}"

    out = {"subtitleFormatted": crumbs, "markdown": "", "entries": [], "rowCount": 0}
    if not at_root:
        out["upFormatted"] = "← " + ("/Private/" + parent if parent else "/Private")
        out["upHref"] = _docs_href(parent)

    if os.path.isfile(target) and target.lower().endswith((".md", ".markdown")):
        def _read():
            with open(target, encoding="utf-8", errors="replace") as f:
                return f.read(200000)
        try:
            content = _retry_os(_read)
        except Exception as exc:  # noqa: BLE001
            content = f"(could not read: {exc})"
        content = content.lstrip("﻿")  # strip a UTF-8 BOM so the first heading parses
        if not content.strip():
            # A 0-byte read on a non-empty file is the classic Google-Drive "online-only"
            # placeholder — say so instead of rendering a blank page.
            try:
                size = os.path.getsize(target)
            except OSError:
                size = 0
            content = (f"_(This file is {_human_size(size)} but read as empty — it may be online-only in "
                       f"Google Drive. Open it once on the Mac, or enable offline access.)_" if size
                       else "_(empty file)_")
        out.update({"title": os.path.basename(target), "kind": "file", "markdown": content})
        # A file's "up" goes to its containing folder.
        out["upFormatted"] = "← " + ("/Private/" + parent if parent else "/Private")
        out["upHref"] = _docs_href(parent)
        return out

    folders, files = [], []
    list_error = None
    try:
        # Google Drive for Desktop can hold EDEADLK on an online-only folder for several
        # seconds while it hydrates; give the interactive listing a longer retry budget.
        names = sorted(_retry_os(lambda: os.listdir(target), attempts=6, delay=0.4), key=str.lower)
    except Exception as exc:  # noqa: BLE001
        names, list_error = [], exc
    for name in names:
        if name.startswith("."):
            continue
        p = os.path.join(target, name)
        child_rel = os.path.join(rel_norm, name) if rel_norm else name
        try:
            is_dir = os.path.isdir(p)
        except OSError:
            continue
        if is_dir:
            folders.append({"name": name, "kind": "folder", "icon": "folder.fill",
                            "metaFormatted": f"{_count_docs(p)} items", "navHref": _docs_href(child_rel)})
        elif name.lower().endswith((".md", ".markdown")):
            try:
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc)
                meta = _ago(mtime, now)
            except OSError:
                meta = ""
            files.append({"name": name, "kind": "file", "icon": "doc.text.fill",
                          "metaFormatted": meta, "navHref": _docs_href(child_rel)})
    entries = folders + files
    out.update({"title": os.path.basename(target) or "Docs", "kind": "dir",
                "entries": entries, "rowCount": len(entries)})
    if list_error is not None:
        import errno as _errno
        transient = {_errno.EDEADLK, _errno.EAGAIN, getattr(_errno, "EBUSY", 16)}
        if isinstance(list_error, OSError) and list_error.errno in transient:
            # Google Drive for Desktop deadlocked on this folder (it's likely online-only).
            # This clears once Drive hydrates it — say so instead of leaking a raw errno.
            out["emptyFormatted"] = ("Google Drive is still loading this folder (it may be online-only). "
                                     "Pull to refresh in a moment, or run `make docs` on the Mac to mirror it offline.")
        else:
            out["emptyFormatted"] = f"Couldn't list this folder: {list_error}"
    elif not entries:
        out["emptyFormatted"] = "No subfolders or .md files here."
    return out


# ---------------------------------------------------------------- /bqtables

def get_bqtables(dataset, table, view="columns"):
    """Browse BigQuery across the whole project: list datasets, then a dataset's
    tables, then a table's field structure (RECORD fields flattened) or a 100-record
    preview. Levels are selected by ?dataset= and ?table=; the table view (Columns vs
    Preview) by ?view=."""
    from google.cloud import bigquery  # noqa: F401  (ensures the client lib is present)
    client = _client()
    project = _project() or client.project

    # Level 3 — a table's fields (Columns tab) or a 100-record sample (Preview tab).
    if table:
        ds = dataset or (_dataset().split(".")[-1] if "." in _dataset() else _dataset())
        table_id = table if table.count(".") >= 2 else f"{project}.{ds}.{table}"
        t = client.get_table(table_id)
        rows = t.num_rows if t.num_rows is not None else 0
        meta = (f"{rows:,} rows · {_human_size(t.num_bytes or 0)}"
                if (t.table_type or "TABLE") == "TABLE" else (t.table_type or "VIEW").title())

        # Two tabs over the same table: Columns (schema) and Preview (first 100 rows).
        base = ("/screen/bqtables?dataset=" + _urlparse.quote(ds)
                + "&table=" + _urlparse.quote(table))
        view = "preview" if view == "preview" else "columns"

        def _tab(key, label):
            active = view == key
            return {
                "key": key, "label": label,
                "navHref": base + "&view=" + key,
                "active": active,
                "color": "$accent" if active else "$textSecondary",
            }
        tabs = [_tab("columns", "Columns"), _tab("preview", "Preview")]

        out = {
            "title": t.table_id,
            "subtitleFormatted": f"{project}.{ds} · {meta}",
            "kind": "table",
            "upFormatted": f"← {ds}",
            "upHref": "/screen/bqtables?dataset=" + _urlparse.quote(ds),
            "tabs": tabs, "view": view, "tables": [], "fields": [], "rowCount": 0,
        }

        if view == "preview":
            # Newest-first preview as generic columns/rows (rendered by the table component).
            # Sort by the table's first DATE/TIME column when it has one; otherwise fall
            # back to reversing the natural row order. The row cap defaults to 1000 and is
            # overridable via the `bq_preview_limit` config key (BQ Tables → Config).
            try:
                date_cols = [f.name for f in t.schema
                             if f.field_type in ("TIMESTAMP", "DATETIME", "DATE", "TIME")]
                try:
                    limit = int(_config_value(client, "bq_preview_limit", "1000") or 1000)
                except (TypeError, ValueError):
                    limit = 1000
                limit = max(1, min(limit, 5000))  # guard against runaway scans
                order = f"ORDER BY `{date_cols[0]}` DESC " if date_cols else ""
                result = client.query(
                    f"SELECT * FROM `{table_id}` {order}LIMIT {limit}"
                ).result(timeout=30)
                columns = [f.name for f in result.schema]
                prows = [[_coerce(v) for v in row.values()] for row in result]
                if not date_cols:
                    prows.reverse()  # best-effort newest-first when there's no date column
                out["preview"] = {"columns": columns, "rows": prows}
                out["rowCount"] = len(prows)
                sort_note = f"newest first · {date_cols[0]}" if date_cols else "reversed"
                out["subtitleFormatted"] = f"{project}.{ds} · {meta} · {len(prows)} shown · {sort_note}"
            except Exception as exc:  # noqa: BLE001 - surface as an in-table error, never blank
                out["preview"] = {"columns": [], "rows": [], "error": str(exc)}
            return out

        fields = []

        def walk(schema, prefix=""):
            for f in schema:
                mode = "" if f.mode in ("NULLABLE", "", None) else f.mode
                fields.append({
                    "name": prefix + f.name,
                    "typeFormatted": f.field_type + (f" · {mode}" if mode else ""),
                    "descFormatted": (f.description or "")[:140],
                })
                if f.field_type in ("RECORD", "STRUCT") and f.fields:
                    walk(f.fields, prefix + f.name + ".")

        walk(t.schema)
        out["fields"] = fields
        out["rowCount"] = len(fields)
        return out

    # Level 2 — tables in a dataset.
    if dataset:
        tables = []
        for t in client.list_tables(f"{project}.{dataset}"):
            kind = (getattr(t, "table_type", None) or "TABLE").title()
            tables.append({
                "name": t.table_id,
                "kind": kind,
                "icon": "tablecells" if kind == "Table" else "doc.text.fill",
                "metaFormatted": kind,
                "navHref": ("/screen/bqtables?dataset=" + _urlparse.quote(dataset)
                            + "&table=" + _urlparse.quote(t.table_id)),
            })
        tables.sort(key=lambda x: x["name"].lower())
        out = {
            "title": dataset,
            "subtitleFormatted": f"{project}.{dataset} · {len(tables)} tables",
            "kind": "list", "tables": tables, "fields": [],
            "upFormatted": "← All datasets", "upHref": "/screen/bqtables",
            "rowCount": len(tables),
        }
        if not tables:
            out["emptyFormatted"] = f"No tables in {dataset}."
        return out

    # Level 1 — datasets in the project.
    datasets = []
    for ds in client.list_datasets(project):
        name = ds.dataset_id
        datasets.append({
            "name": name, "kind": "Dataset", "icon": "tablecells", "metaFormatted": "dataset",
            "navHref": "/screen/bqtables?dataset=" + _urlparse.quote(name),
        })
    datasets.sort(key=lambda x: x["name"].lower())
    out = {
        "title": "BigQuery",
        "subtitleFormatted": f"{project} · {len(datasets)} datasets",
        "kind": "list", "tables": datasets, "fields": [], "rowCount": len(datasets),
    }
    if not datasets:
        out["emptyFormatted"] = f"No datasets in {project}."
    return out


# ---------------------------------------------------------------- /messages

def _messages_dir():
    """Locate the Drive-synced Messages export folder (the iMessage/SMS sync writes here,
    same idea as the Smart Home events). MESSAGES_DIR wins; otherwise probe the usual
    Google Drive for Desktop mounts (Private/Home/Messages, else a top-level copy)."""
    env = os.environ.get("MESSAGES_DIR")
    cands = [env] if env else []
    for gd in _gdrive_my_drives():
        cands.append(os.path.join(gd, "Private", "Messages"))
        cands.append(os.path.join(gd, "Private", "Home", "Messages"))
        cands.append(os.path.join(gd, "Messages"))
    cands.append(os.path.expanduser("~/Messages"))
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return env  # let the caller report a clear not-found


def _load_message_records(path):
    """Parse a synced messages file: JSONL (one object per line) or a JSON array /
    {messages:[…]} object. Tolerant — skips anything unparseable."""
    out = []
    try:
        if path.endswith(".jsonl"):
            for ln in _retry_os(lambda: _tail_lines(path, 100000, budget=4 * 1024 * 1024)):
                try:
                    obj = json.loads(ln)
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:  # noqa: BLE001
                    continue
        else:
            data = json.loads(_retry_os(lambda: open(path, encoding="utf-8", errors="replace").read()))
            rows = data.get("messages") if isinstance(data, dict) else data
            if isinstance(rows, list):
                out.extend(r for r in rows if isinstance(r, dict))
    except Exception:  # noqa: BLE001
        pass
    return out


def _message_view(e, now):
    """Tolerantly turn one synced message record into a display row (schema may drift):
    sender/handle/from + text/body/message + date/ts + is_from_me/direction."""
    if not isinstance(e, dict):
        return None
    dt = _parse_iso_or_epoch(e.get("date") or e.get("ts") or e.get("timestamp")
                             or e.get("time") or e.get("sent_at") or e.get("created_at"))
    text = (e.get("text") or e.get("body") or e.get("message") or e.get("content") or "").strip()
    fm = e.get("is_from_me")
    if fm is None:
        fm = str(e.get("direction") or "").lower() in ("out", "sent", "outgoing", "from_me")
    fm = bool(fm)
    who = "Me" if fm else (e.get("sender") or e.get("handle") or e.get("from")
                           or e.get("contact") or e.get("name") or "Unknown")
    return {
        "_t": dt or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
        "titleFormatted": str(who),
        "detailFormatted": (text or "(no text)")[:140],
        "direction": "out" if fm else "in",
        "icon": "paperplane.fill" if fm else "message.fill",
        "timeFormatted": (dt.astimezone().strftime("%b %-d, %-I:%M %p") if dt else ""),
    }


def _gmail_accounts():
    """Configured Gmail accounts as [(user, app_password)] from GMAIL_<n>_USER /
    GMAIL_<n>_PASS env pairs (n = 1..8). App-passwords, not real passwords."""
    out = []
    for n in range(1, 9):
        user = os.environ.get(f"GMAIL_{n}_USER")
        pw = os.environ.get(f"GMAIL_{n}_PASS")
        if user and pw:
            out.append((user.strip(), pw.strip()))
    return out


def _imessage_items(now, limit):
    """Recent iMessage/SMS from the Drive-synced Messages export (newest files first).
    Best-effort: returns (items, status)."""
    d = _messages_dir()
    if not d or not os.path.isdir(d):
        return [], {"label": "iMessage / SMS", "status": "Not found", "direction": "down",
                    "metaFormatted": "synced Messages folder not found (set MESSAGES_DIR)"}
    try:
        files = sorted(_glob.glob(os.path.join(d, "*.jsonl")) + _glob.glob(os.path.join(d, "*.json")),
                       key=os.path.getmtime, reverse=True)
        records = []
        for path in files:
            records.extend(_load_message_records(path))
            if len(records) >= limit * 4:
                break
        views = [v for v in (_message_view(r, now) for r in records) if v]
        views.sort(key=lambda v: v["_t"], reverse=True)
        items = views[:limit]
        for v in items:
            v.pop("_t", None)
    except Exception as exc:  # noqa: BLE001
        return [], {"label": "iMessage / SMS", "status": "Error", "direction": "down",
                    "metaFormatted": str(exc)[:120]}
    if not files:
        return [], {"label": "iMessage / SMS", "status": "No data", "direction": "down",
                    "metaFormatted": f"no .jsonl/.json in {d}"}
    status = {"label": "iMessage / SMS", "status": "OK", "direction": "up",
              "metaFormatted": f"{len(items)} recent"}
    return items, status


def _gmail_items(user, pw, limit):
    """Recent INBOX headers (from/subject/date) for one Gmail account over IMAP. Best-effort."""
    import imaplib
    import email
    from email.header import decode_header, make_header
    items = []
    M = None
    try:
        M = imaplib.IMAP4_SSL("imap.gmail.com", timeout=8)
        M.login(user, pw)
        M.select("INBOX", readonly=True)
        typ, data = M.search(None, "ALL")
        ids = data[0].split()[-limit:] if data and data[0] else []
        for i in reversed(ids):
            typ, md = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not md or not md[0]:
                continue
            msg = email.message_from_bytes(md[0][1])
            def _h(name):
                try:
                    return str(make_header(decode_header(msg.get(name, "")))).strip()
                except Exception:  # noqa: BLE001
                    return msg.get(name, "")
            dt = None
            try:
                dt = email.utils.parsedate_to_datetime(msg.get("Date", ""))
            except Exception:  # noqa: BLE001
                dt = None
            items.append({
                "titleFormatted": _h("From")[:90] or "(unknown sender)",
                "detailFormatted": _h("Subject")[:140] or "(no subject)",
                "icon": "envelope.fill",
                "timeFormatted": (dt.astimezone().strftime("%b %-d, %-I:%M %p") if dt else ""),
            })
    finally:
        if M is not None:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass
    return items


def get_messages():
    """Recent iMessage/SMS from the Mac's chat.db + latest INBOX headers from each
    configured Gmail account (GMAIL_<n>_USER/PASS app-passwords). Each source degrades
    independently; nothing here needs BigQuery."""
    now = datetime.datetime.now(datetime.timezone.utc)
    msg_max = _int_env("MESSAGES_MAX", 25)
    mail_max = _int_env("MESSAGES_MAIL_MAX", 10)

    sources, groups = [], []

    msgs, msg_status = _imessage_items(now, msg_max)
    sources.append(msg_status)
    if msgs:
        groups.append({"key": "imessage", "title": "iMessage / SMS", "icon": "message.fill",
                       "kind": "messages", "items": msgs})

    accounts = _gmail_accounts()
    if not accounts:
        sources.append({"label": "Gmail", "status": "Not configured", "direction": "down",
                        "metaFormatted": "set GMAIL_1_USER/GMAIL_1_PASS (app-password) in Deploy/.env"})
    for user, pw in accounts:
        try:
            mail = _gmail_items(user, pw, mail_max)
            sources.append({"label": user, "status": "OK", "direction": "up",
                            "metaFormatted": f"{len(mail)} recent"})
            if mail:
                groups.append({"key": user, "title": user, "icon": "envelope.fill",
                               "kind": "email", "items": mail})
        except Exception as exc:  # noqa: BLE001
            sources.append({"label": user, "status": "Error", "direction": "down",
                            "metaFormatted": str(exc)[:120]})

    total = sum(len(g["items"]) for g in groups)
    return {
        "title": "Messages",
        "subtitleFormatted": (f"{total} recent · {len(groups)} source{'' if len(groups) == 1 else 's'}"
                              if total else "No messages — configure chat.db access and/or Gmail"),
        "sources": sources,
        "groups": groups,
        "rowCount": total,
        "emptyFormatted": ("" if total else
                           "Grant the sidecar Full Disk Access for iMessage, and add GMAIL_<n>_USER/PASS for email."),
    }


# ---------------------------------------------------------------- secrets / Settings page
# Enter config (incl. secrets) through the app instead of editing Deploy/.env. Stored in a
# pluggable secrets backend: a 0600 local file by default, or Google Secret Manager when the
# library + GOOGLE_CLOUD_PROJECT are available (set SECRETS_BACKEND=file to force the file).
# Resolution everywhere stays: real env var (.env) wins → else the stored value → else default,
# so .env becomes optional. At startup the sidecar hydrates os.environ from the store so all
# the existing os.environ.get(...) reads transparently pick up app-entered values.

SECRETS_FILE = os.path.expanduser(os.environ.get("SECRETS_FILE") or "~/.config/__APP_NAME_LOWER__/secrets.json")
SECRETS_SM_NAME = os.environ.get("SECRETS_SM_NAME", "__APP_NAME_LOWER__-secrets")

# The settings the app exposes, grouped. secret=True → masked input, never echoed back.
SETTINGS_SCHEMA = [
    ("Server", [("TS_HOST", "Tailscale host", False)]),
    ("Market data", [("TWELVEDATA_KEY", "Twelve Data API key", True),
                     ("FRED_KEY", "FRED API key", True),
                     ("RAPIDAPI_KEY", "RapidAPI key (Zillow)", True)]),
    ("GitHub (Repos)", [("REPOS_GH_TOKEN", "GitHub token (repo scope)", True),
                        ("DASHBOARD_REPO_WATCH_INTERVAL", "Repo watch interval (s)", False)]),
    # Team ID + bundle id are auto-derived (build team + app bundle); just provide the key.
    # Easiest of all: drop the AuthKey_<ID>.p8 file in ~/.config/__APP_NAME_LOWER__/apns and skip both.
    ("Push (APNs)", [("APNS_KEY_P8", "APNs key — paste .p8 contents (or drop the file, see below)", True),
                     ("APNS_KEY_ID", "APNs Key ID (skip if you dropped the file)", False),
                     ("APNS_ENV", "APNs environment: sandbox (dev/OTA build) or production", False),
                     ("DASHBOARD_LOG_WATCH_INTERVAL", "Log-failure watch interval (s)", False),
                     ("DASHBOARD_LIVE_ACTIVITY_INTERVAL", "Live Activity interval (s, 0=off)", False)]),
    # Notification on/off switches. A 4th tuple element marks the field type ("bool" → switch);
    # an optional 5th element is the default when unset. Notifications default ON; the Swift
    # watchers read these (DASHBOARD_NOTIFY_*).
    ("Notifications", [("DASHBOARD_NOTIFY_ENABLED", "All notifications", False, "bool"),
                       ("DASHBOARD_NOTIFY_LOG_FAILURES", "Log-failure alerts", False, "bool"),
                       ("DASHBOARD_NOTIFY_REPOS", "PR / branch alerts", False, "bool")]),
    # Deploy toggles read by the deploy SCRIPTS (via lib.sh app_setting), not the server. Default
    # OFF so `make deploy` stays web-only unless you opt in. The keychain password (secret, masked)
    # lets the native build unlock the login keychain + re-grant codesign access unattended — so a
    # rotated signing cert never brings back the interactive password prompt.
    ("Deploy", [("DEPLOY_IOS", "Auto-build iOS app on deploy", False, "bool", "false"),
                ("DASHBOARD_KEYCHAIN_PW", "Mac login password (codesign, unattended)", True)]),
]
_SECRET_KEYS = {k for _, keys in SETTINGS_SCHEMA for k, _, sec, *_ in keys if sec}


def _use_secret_manager():
    # Default backend is Google Secret Manager (set SECRETS_BACKEND=file to force the local
    # file). Falls back to the file only if the client library or a GCP project is unavailable.
    if os.environ.get("SECRETS_BACKEND") == "file":
        return False
    try:
        import google.cloud.secretmanager  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(_project())


def _file_load():
    try:
        with open(SECRETS_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _file_save(d):
    os.makedirs(os.path.dirname(SECRETS_FILE), exist_ok=True)
    fd = os.open(SECRETS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)


def _secrets_load():
    """Current stored secrets. Tries Secret Manager (bounded), falling back to the local file
    if it errors/times out — so a misconfigured SM never breaks reads."""
    if _use_secret_manager():
        try:
            from google.cloud import secretmanager
            c = secretmanager.SecretManagerServiceClient()
            name = f"projects/{_project()}/secrets/{SECRETS_SM_NAME}/versions/latest"
            return _json.loads(c.access_secret_version(name=name, timeout=8).payload.data.decode())
        except Exception as e:  # noqa: BLE001
            print(f"[secrets] Secret Manager read failed ({e}); using local file", flush=True)
            return _file_load()
    return _file_load()


def _secrets_save(d):
    """Persist secrets. Tries Secret Manager (bounded), falling back to the 0600 local file on
    ANY error/timeout — so saving from the app never hard-fails on a SM/IAM/policy issue."""
    if _use_secret_manager():
        try:
            from google.cloud import secretmanager
            c = secretmanager.SecretManagerServiceClient()
            parent = f"projects/{_project()}"
            sid = SECRETS_SM_NAME
            try:
                c.create_secret(parent=parent, secret_id=sid,
                                secret={"replication": {"automatic": {}}}, timeout=8)
            except Exception:  # noqa: BLE001
                pass  # already exists (or create not permitted but versions are)
            c.add_secret_version(parent=f"{parent}/secrets/{sid}",
                                 payload={"data": _json.dumps(d).encode()}, timeout=8)
            return
        except Exception as e:  # noqa: BLE001
            print(f"[secrets] Secret Manager write failed ({e}); saving to local file instead", flush=True)
    _file_save(d)


def hydrate_env_from_store():
    """Inject stored values into os.environ (without overriding a real env var), so every
    existing os.environ.get(...) call transparently sees app-entered config."""
    for k, v in _secrets_load().items():
        if v and not os.environ.get(k):
            os.environ[k] = str(v)


def _settings_backend_label():
    return "Google Secret Manager" if _use_secret_manager() else SECRETS_FILE


def get_settings():
    store = _secrets_load()
    groups = []
    for title, keys in SETTINGS_SCHEMA:
        fields = []
        for k, label, secret, *rest in keys:
            ftype = (rest[0] if rest else None) or ("secret" if secret else "string")
            if ftype == "bool":
                # On/off switch. Normalized to "true"/"false". Defaults to the optional 5th tuple
                # element when nothing is stored yet (else ON).
                raw = store.get(k) if store.get(k) is not None else os.environ.get(k)
                if raw is None:
                    raw = rest[1] if len(rest) > 1 else "true"
                on = str(raw).strip().lower() not in ("0", "false", "no", "off", "")
                fields.append({"key": k, "label": label, "type": "bool", "value": "true" if on else "false"})
                continue
            from_env = bool(os.environ.get(k)) and k not in store
            is_set = bool(os.environ.get(k) or store.get(k))
            fields.append({
                "key": k, "label": label,
                "type": "secret" if secret else "string",
                # Never echo a secret back; non-secrets pre-fill their current value.
                "value": "" if secret else (store.get(k) or os.environ.get(k) or ""),
                "placeholderFormatted": ("•••• set" if (secret and is_set) else
                                         ("from .env" if from_env else ("set" if is_set else "not set"))),
            })
        groups.append({"title": title, "fields": fields})
    return {"title": "Settings",
            "subtitleFormatted": f"stored in {_settings_backend_label()} · .env still wins if set",
            "groups": groups,
            "noteFormatted": "Saved values apply automatically — the server restarts itself on save."}


def upsert_settings(items):
    """Save submitted settings. An empty submit for a SECRET is ignored (so a blank masked
    field doesn't wipe a stored secret); an empty submit for a normal field clears it."""
    store = _secrets_load()
    for it in items:
        if not isinstance(it, dict):
            continue
        k, v = it.get("key"), it.get("value")
        if not k:
            continue
        if (v is None or v == "") and k in _SECRET_KEYS:
            continue
        if v is None or v == "":
            store.pop(k, None)
        else:
            store[k] = v
    _secrets_save(store)
    hydrate_env_from_store()
    return get_settings()


def get_settings_resolved():
    """Every stored value, for the Swift server to hydrate its own env at startup (localhost
    only). Includes secrets — fine over 127.0.0.1 on the same machine."""
    return {"values": _secrets_load()}


def _devices_together(client, match_a, match_b, radius_m=250.0, max_age_min=15, move_m=120.0):
    """True when the two devices matching `match_a` / `match_b` (case-insensitive substring of
    deviceName OR deviceModel) are together: their latest fixes are concurrent (within
    max_age_min of each other) AND either within radius_m (co-located) or both moving and within
    ~3x radius_m (traveling together). Best-effort; False on any gap/missing device."""
    from google.cloud import bigquery
    hist = _afm_history_table()
    sql = f"""
        SELECT who, date_time, lat, lon FROM (
          SELECT who, date_time, lat, lon,
                 ROW_NUMBER() OVER (PARTITION BY who ORDER BY date_time DESC) AS rn
          FROM (
            SELECT
              CASE
                WHEN LOWER(deviceName) LIKE @a OR LOWER(IFNULL(deviceModel,'')) LIKE @a THEN 'a'
                WHEN LOWER(deviceName) LIKE @b OR LOWER(IFNULL(deviceModel,'')) LIKE @b THEN 'b'
              END AS who,
              date_time,
              SAFE_CAST(latitude AS FLOAT64) AS lat,
              SAFE_CAST(longitude AS FLOAT64) AS lon
            FROM `{hist}`
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          ) WHERE who IS NOT NULL
        ) WHERE rn <= 2
        ORDER BY who, date_time DESC
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("a", "STRING", f"%{(match_a or '').lower()}%"),
        bigquery.ScalarQueryParameter("b", "STRING", f"%{(match_b or '').lower()}%")])
    by = {"a": [], "b": []}
    for r in client.query(sql, job_config=cfg).result(timeout=20):
        by[r["who"]].append(r)
    if not by["a"] or not by["b"]:
        return False
    la, lb = by["a"][0], by["b"][0]
    if la["lat"] is None or lb["lat"] is None:
        return False
    # Concurrent fixes (compare the two times to each other — tz-independent).
    if abs((la["date_time"] - lb["date_time"]).total_seconds()) / 60.0 > max_age_min:
        return False
    dist = _haversine(la["lat"], la["lon"], lb["lat"], lb["lon"])
    if dist <= radius_m:
        return True
    def moving(rs):
        return len(rs) >= 2 and _haversine(rs[0]["lat"], rs[0]["lon"], rs[1]["lat"], rs[1]["lon"]) >= move_m
    return moving(by["a"]) and moving(by["b"]) and dist <= radius_m * 3


def get_devices_together():
    """Privacy overlay for the Activity widget: when the two configured devices are together,
    return dummy content + an alternate icon so the widget masks the real status. All knobs are
    Config-page settable (together_device_a/_b, together_radius_m, together_dummy_*, together_icon,
    together_normal_icon). Devices default to the iPhone 16 + iPhone 12 mini."""
    client = _client()
    def cfg(k, d):
        v = _config_value(client, k)
        if v not in (None, ""):
            return v
        return os.environ.get(k.upper()) or d
    def masked():
        return {"together": True,
                "status": cfg("together_dummy_status", "Charging"),
                "place": cfg("together_dummy_place", "Home"),
                "meta": cfg("together_dummy_meta", ""),
                "icon": cfg("together_icon", "house.fill")}
    # Manual override from the Activity page: force the widget into disabled/masked mode regardless
    # of where the devices actually are (no BigQuery proximity query needed).
    if _config_truthy(client, "activity_widget_disabled"):
        return masked()
    try:
        radius = float(cfg("together_radius_m", "250"))
    except Exception:  # noqa: BLE001
        radius = 250.0
    try:
        together = _devices_together(client, cfg("together_device_a", "iphone 16"),
                                     cfg("together_device_b", "12 mini"), radius)
    except Exception as e:  # noqa: BLE001
        print(f"[widget] devices_together failed: {e}", flush=True)
        together = False
    if together:
        return masked()
    return {"together": False, "icon": cfg("together_normal_icon", "location.fill")}


def get_mortgage():
    """Latest 30-yr fixed mortgage rate from BigQuery (default Datahub.mortgage_mnd_30yr —
    rate_pct dated by rate_date), plus the prior reading for a delta. The table is settable on
    the Config page (bq_mortgage_table) or via env; columns/location likewise. Returns raw
    numbers + ISO date; the Swift server formats the card."""
    client = _client()
    # Table: env → Config page (bq_mortgage_table) → default. Use a FULL project.dataset.table if
    # the dataset lives in another project (the billing project is only assumed when unqualified).
    table = (os.environ.get("BQ_MORTGAGE_TABLE")
             or _config_value(client, "bq_mortgage_table")
             or "datahub.mortgage_mnd_30yr")
    date_col = os.environ.get("BQ_MORTGAGE_DATE_COL") or "rate_date"
    rate_col = os.environ.get("BQ_MORTGAGE_RATE_COL") or "rate_pct"
    tid = table if table.count(".") >= 2 else (f"{_project()}.{table}" if _project() else table)
    # A dataset in a non-US region fails with "not found in location US" unless the job runs in
    # that region. Use an explicit location if given, else auto-detect it from the dataset's
    # metadata so any region just works.
    location = os.environ.get("BQ_MORTGAGE_LOCATION") or _config_value(client, "bq_mortgage_location")
    if not location:
        try:
            location = client.get_dataset(".".join(tid.split(".")[:-1])).location
        except Exception:  # noqa: BLE001 — dataset in another project / no access → fall through
            location = None
    sql = (f"SELECT `{date_col}` AS d, `{rate_col}` AS r FROM `{tid}` "
           f"WHERE `{rate_col}` IS NOT NULL ORDER BY `{date_col}` DESC LIMIT 2")
    job = client.query(sql, location=location) if location else client.query(sql)
    rows = list(job.result(timeout=30))
    if not rows:
        return {"error": f"no rows in {tid}"}
    latest, prior = rows[0], (rows[1] if len(rows) > 1 else None)
    return {
        "rate": float(latest["r"]),
        "priorRate": (float(prior["r"]) if prior is not None and prior["r"] is not None else None),
        "asOf": _coerce(latest["d"]),
        "label": "30-Yr Fixed",
    }


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error_card(self, title, exc):
        return {"title": title, "subtitleFormatted": "query failed",
                "columns": [], "rows": [], "items": [], "error": str(exc)}

    def do_GET(self):
        parsed = _urlparse.urlparse(self.path)
        path = parsed.path
        # keep_blank_values so an explicit empty `?types=` (deselect all) is
        # distinguishable from the param being absent (use the useful defaults).
        qs = _urlparse.parse_qs(parsed.query, keep_blank_values=True)
        if path == "/healthz":
            return self._send(200, {"status": "ok"})
        # /afm takes an optional ?hours= window (default 24); the rest take none.
        handlers = {
            "/query": run_query,
            "/afm": lambda: afm_segments(hours=_int_arg(qs, "hours", 24),
                                         range_key=qs["range"][0] if "range" in qs else None),
            "/afm_raw": lambda: afm_transactions(hours=_int_arg(qs, "hours", 48)),
            "/afm_now": afm_now,
            "/afm_log": lambda: afm_log(hours=_int_arg(qs, "hours", 24),
                                        range_key=qs["range"][0] if "range" in qs else None),
            "/smarthome_log": smarthome_log,
            "/config": get_config,
            "/settings": get_settings,
            "/settings_resolved": get_settings_resolved,
            "/known_locs": lambda: get_known_locs(qs.get("lat", [None])[0], qs.get("lon", [None])[0],
                                                  qs.get("place", [None])[0]),
            "/balances": get_balances,
            "/mortgage": get_mortgage,
            "/devices_together": get_devices_together,
            "/budget": lambda: get_budget(months=_int_arg(qs, "months", 12)),
            "/smarthome": lambda: get_smarthome(
                qs["types"][0] if "types" in qs else None,
                qs["sources"][0] if "sources" in qs else None),
            "/logs": lambda: get_logs(qs["status"][0] if "status" in qs else None),
            "/logfile": lambda: get_logfile(qs.get("file", [""])[0]),
            "/afm_health": get_afm_health,
            "/messages": get_messages,
            "/repos": get_repos,
            "/repo_banner": get_repo_banner,
            "/deploy_repo": get_deploy_repo,
            "/repos_ship": lambda: get_repos_ship(qs),
            "/schedrunner": get_schedrunner,
            "/schedlogs": get_schedlogs,
            "/docs": lambda: get_docs(qs.get("path", [""])[0]),
            "/bqtables": lambda: get_bqtables(qs.get("dataset", [""])[0], qs.get("table", [""])[0],
                                              qs.get("view", ["columns"])[0]),
            "/gcp_costs": get_gcp_costs,
        }
        fn = handlers.get(path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            self._send(200, fn())
        except Exception as exc:  # noqa: BLE001 - surface as a card error, never crash
            self._send(200, self._error_card(path.lstrip("/") or "BigQuery", exc))

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        qs = _urlparse.parse_qs(_urlparse.urlparse(self.path).query)
        writers = {"/config": upsert_config, "/known_locs": upsert_known_loc, "/settings": upsert_settings}
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            items = body.get("items", [])
            if path == "/repos_pr":
                # 1-click ship: context rides in the query (items kept as a fallback).
                params = {k: v[0] for k, v in qs.items()}
                for it in items:
                    if isinstance(it, dict) and it.get("key"):
                        params.setdefault(it["key"], it.get("value"))
                self._send(200, ship_branch(params))
            elif path in writers:
                self._send(200, writers[path](items))
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._send(200, self._error_card(path.lstrip("/") or "Config", exc))

    def log_message(self, *args):
        pass


def main():
    hydrate_env_from_store()  # app-entered settings fill any env the deploy didn't set
    port = int(os.environ.get("BQ_SIDECAR_PORT", "8099"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"bq_sidecar listening on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
