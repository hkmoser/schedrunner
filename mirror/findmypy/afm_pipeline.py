#!/usr/bin/env python3
"""
Shared collection pipeline — the reusable body extracted from afm_live.py.

afm_live.py (the cron fallback) is intentionally left UNTOUCHED so it can be
reverted to instantly. This module is a faithful port of its collection body:
device loop -> per-minute CSV -> BigQuery append -> view materialization ->
change/smart alerts. The BigQuery rows are identical to what afm_live.py writes;
the surrounding auth/backoff state-machine (which the persistent daemon does not
need) is omitted, and the legacy afm_all.csv aggregate is no longer written since
BigQuery is the only consumer.

Used by afm_live_daemon.py. When the daemon has proven itself, afm_live.py can be
deleted and this becomes the single source of truth.

run_pipeline(api) is called repeatedly by the daemon against a SINGLE long-lived
PyiCloudService, so:
  - all per-run state (timestamps, filenames) is computed inside the function,
  - the alert modules (lib_bq_changealerts / lib_bq_smartalerts), which do their
    work as import side-effects, are reloaded each call rather than imported once.
"""

import csv
import datetime
import importlib
import os
import sys
import time

import pandas as pd

import lib_bq

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloudFailedLoginException,
    PyiCloudAuthRequiredException,
    PyiCloud2FARequiredException,
)

# ── config (matches afm_live.py) ──────────────────────────────────────────────

APPLE_ID = os.environ.get("AFM_APPLE_ID", "joe@joemoser.com")
COOKIE_DIR = os.path.expanduser("~/.pyicloud")
BASE_PATH = os.environ.get("AFM_BASE_PATH", "/Users/joemoser/Dropbox/Source/afm/findmypy/")
# BigQuery is the only consumer of the data, so the per-minute CSVs are scratch:
# written, read once to append to BQ, then disposable. Delete them after this many
# days so the afm/ folder (under Dropbox) doesn't grow without bound.
RETENTION_DAYS = int(os.environ.get("AFM_CSV_RETENTION_DAYS", "7"))

COLUMN_ORDER = ['date_time', 'deviceID', 'deviceName', 'deviceDisplayName', 'name', 'deviceStatus', 'batteryLevel', 'positionType', 'timeStamp', 'latitude', 'longitude', 'horizontalAccuracy', 'verticalAccuracy', 'locationFinished', 'isOld', 'isInaccurate', 'altitude', 'floorLevel', 'secureLocationTs', 'locationType', 'secureLocation', 'locationMode', 'addresses', 'prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou', 'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel', 'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake']

SKIP_MODELS = ['FifthGen-white', 'SecondGen-white', 'iphoneSE-1-1-0', 'AirPods_8194', 'FourthGen', 'MacBookPro15_1-spacegray', 'TenthGen-2-3-0', 'MacBookPro15_2-spacegray', 'FirstGen', 'Mac15_6-spaceblack', 'Mac14_3', 'MacPro3_1', 'MacBookPro5_5']

# rawDeviceModel -> deviceModel remaps (verbatim from afm_live.py)
_MODEL_REMAP = {
    'iPhone17,3': 'iphone12mini-1-17-0',
    'iPhone13,1': 'i12o',
    'iPhone18,2': 'iPhone16-1-4-0',
    'iPhone17,2': 'i16o',
}


class CronPyiCloudService(PyiCloudService):
    """PyiCloudService that never falls back to SRP (same guarantee as
    afm_live.py's _CronPyiCloudService). Token-only auth; force_refresh passes
    through so a mid-run 450 re-mints via /accountLogin, never SRP."""

    def authenticate(self, force_refresh: bool = False, service=None) -> None:
        super().authenticate(force_refresh=force_refresh, service=service)

    def _authenticate(self) -> None:
        # Only token auth. Re-raise as-is; the daemon decides transient vs fatal.
        self._authenticate_with_token()


def build_api():
    """Construct the long-lived, no-SRP iCloud service. Called ONCE by the daemon."""
    return CronPyiCloudService(APPLE_ID, cookie_directory=COOKIE_DIR)


# ── /accountLogin instrumentation ─────────────────────────────────────────────
# /accountLogin frequency is the single metric that predicts Apple session death:
# a warm daemon should fire it rarely (a handful/day). Wrapping the token-auth call
# lets us count it directly instead of inferring it from collection time.
_accountlogin_log = []  # list of (ts_epoch, elapsed_s, outcome)


def install_accountlogin_logging(api, log=print):
    """Wrap api._authenticate_with_token to record every /accountLogin call. Call
    ONCE after build_api(), before the first api.devices access."""
    orig = api._authenticate_with_token

    def wrapped():
        t0 = time.time()
        outcome = "ok"
        try:
            orig()
        except Exception as e:
            outcome = f"FAIL:{type(e).__name__}"
            raise
        finally:
            elapsed = time.time() - t0
            _accountlogin_log.append((t0, elapsed, outcome))
            log(f"[auth] /accountLogin #{len(_accountlogin_log)} {outcome} in {elapsed:.1f}s")

    api._authenticate_with_token = wrapped


def accountlogin_total():
    return len(_accountlogin_log)


def accountlogin_count_since(secs):
    cutoff = time.time() - secs
    return sum(1 for ts, _, _ in _accountlogin_log if ts >= cutoff)


def _force_locate(mgr, log=print):
    """Force the FindMy manager to fetch fresh locations from Apple this cycle.

    Reuses the warm session (no /accountLogin unless cookies actually expired).
    Tries the timlaing public method first, then older pyicloud names. Any auth
    failure propagates to the daemon's classifier (transient 450 vs session dead)."""
    for attr, kwargs in (("refresh", {"locate": True}),
                         ("refresh_client", {}),
                         ("_refresh_client_with_reauth", {"locate": True})):
        fn = getattr(mgr, attr, None)
        if callable(fn):
            fn(**kwargs)
            return
    log("[pipeline] WARNING: device manager has no refresh method — data may be stale")


def run_pipeline(api, log=print):
    """One full collection + upload + alert cycle against a warm `api`.

    Faithful port of afm_live.py's pipeline body (the auth/backoff bookkeeping is
    deliberately omitted — the daemon keeps the session warm in memory). Raises on
    failure so the daemon's loop can classify it (transient vs session-dead)."""
    dt = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    dt_full = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = BASE_PATH + "afm/afm_" + dt + ".csv"
    file_exists = os.path.exists(filename)

    log("Script started at: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    start_time = time.time()

    with open(filename, "a", newline="") as f:
        w = csv.DictWriter(f, COLUMN_ORDER)
        if not file_exists:
            w.writeheader()

        device_count = 0
        log(f"[pipeline] Starting device loop at {datetime.datetime.now().strftime('%H:%M:%S')}")

        # Force a fresh locate from Apple THIS cycle. `dev.location` only returns the
        # manager's cached content, and `for dev in api.devices` re-fetches only when
        # the manager's internal is_alive TTL (~30 min) has expired — so without this,
        # most cycles wrote a STALE snapshot to BigQuery. refresh() reuses the warm
        # _server_ctx (a light refreshClient POST), so it does NOT trigger /accountLogin.
        # (Cron never hit this: each process built a new manager, fresh-fetching in __init__.)
        _al_before = accountlogin_total()
        mgr = api.devices
        _force_locate(mgr, log)
        _al_cycle = accountlogin_total() - _al_before
        if _al_cycle:
            log(f"[auth] /accountLogin fired {_al_cycle}x this cycle "
                f"({accountlogin_count_since(3600)} in last 60min)")

        for dev in mgr:
            k = dev.data.get("id") or dev.data.get("deviceID") or dev.data.get("udid")

            if dev.data.get('deviceModel') in SKIP_MODELS:
                continue

            device_count += 1
            prefix = {'date_time': dt_full, 'deviceID': k, 'deviceName': dev}

            data1 = dev.status(['prsId', 'batteryStatus', 'lowPowerMode', 'deviceWithYou',
                                'locationEnabled', 'deviceClass', 'deviceModel', 'rawDeviceModel',
                                'locationCapable', 'trackingInfo', 'audioChannels', 'darkWake'])

            _remap = _MODEL_REMAP.get(data1.get('rawDeviceModel'))
            if _remap:
                data1['deviceModel'] = _remap

            loc = dev.location  # 450 -> /accountLogin recovery fires here if session stale

            data2 = {'positionType': 'None'} if not loc else loc

            w.writerow(prefix | data1 | data2)

        log(f"[pipeline] Device loop done: {device_count} device(s) written")

    log("Data collection from iCloud completed at: {}. Time taken: {:.2f} seconds".format(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

    df1 = pd.read_csv(filename)

    # NOTE: the legacy aggregate afm_all.csv is intentionally NOT written here.
    # BigQuery is the only consumer, so appending to (and re-syncing to Dropbox)
    # an ever-growing file every cycle was pure waste. The per-minute file above
    # is all that's needed to feed BigQuery, and it's cleaned up by cleanup_old_csvs.

    log("Writing to files completed at: {}. Time taken: {:.2f} seconds".format(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

    lib_bq.append_to_bigquery(df1, 'afm_latest_live')

    # afm_now_live_mat must complete first — the other two views depend on it.
    lib_bq.materialize_view(view_name='afm_now_live', destination_table='afm_now_live_mat')

    # The remaining two views are independent; run them in parallel.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(lib_bq.materialize_view, view_name='afm_change_live_vw', destination_table='afm_change_live_vw_mat'): 'afm_change_live_vw',
            pool.submit(lib_bq.materialize_view, view_name='afm_now_ch_live',    destination_table='afm_now_ch_live_mat'):    'afm_now_ch_live',
        }
        for future in as_completed(futures):
            future.result()  # re-raise so the caller's handler sees it

    log("Uploading to BigQuery completed at: {}. Time taken: {:.2f} seconds".format(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), time.time() - start_time))

    # Change- and smart-alerts do their work as import side-effects. In a long-lived
    # process a plain `import` only runs once, so we re-execute each cycle. Run each
    # EXACTLY once per cycle: import the first time, reload thereafter. (A naive
    # `import X; reload(X)` would execute the module twice on the very first cycle.)
    _run_alert_module('lib_bq_changealerts')
    _run_alert_module('lib_bq_smartalerts')

    return device_count


def _run_alert_module(name):
    """Execute an alert module's top-level code once. import on first use, reload after."""
    if name in sys.modules:
        importlib.reload(sys.modules[name])
    else:
        importlib.import_module(name)


def cleanup_old_csvs(log=print):
    """Delete afm/*.csv files older than RETENTION_DAYS. Safe to call anytime;
    never raises. Also sweeps the now-defunct afm_all.csv, which ages out once the
    daemon stops appending to it. Returns (files_deleted, bytes_freed)."""
    afm_dir = os.path.join(BASE_PATH, "afm")
    cutoff = time.time() - RETENTION_DAYS * 86400
    deleted, freed = 0, 0
    try:
        for entry in os.scandir(afm_dir):
            if not entry.is_file():
                continue
            name = entry.name
            if not (name.startswith("afm_") and name.endswith(".csv")):
                continue
            try:
                st = entry.stat()
                if st.st_mtime < cutoff:
                    size = st.st_size
                    os.remove(entry.path)
                    deleted += 1
                    freed += size
            except FileNotFoundError:
                pass
            except Exception as e:
                log(f"[cleanup] could not remove {name}: {e}")
    except FileNotFoundError:
        log(f"[cleanup] {afm_dir} not found — nothing to clean")
        return (0, 0)
    except Exception as e:
        log(f"[cleanup] scan failed: {e}")
        return (deleted, freed)
    log(f"[cleanup] removed {deleted} CSV(s) older than {RETENTION_DAYS}d, "
        f"freed {freed / 1_048_576:.1f} MB")
    return (deleted, freed)
