# CLAUDE.md — findmypy

This file provides context for AI assistants working in this repository.

## Standing Instructions

- **PR workflow**: Always keep an open PR from `claude/add-claude-documentation-C50k2` → `master`. Whenever the current PR is closed (merged or declined), immediately open a new one with the latest commits — do this proactively without waiting to be asked.

## Project Overview

**findmypy** is a Python library (v0.1.11, Alpha) that wraps Apple's FindMy API, allowing Python code to retrieve device locations, play sounds, send messages, and activate lost mode. It was created as a replacement for `pyicloud` for device tracking in home-assistant, avoiding repeated iCloud login emails.

The repository contains two distinct layers:
1. **The installable library** (`findmypy/`) — published to PyPI, used by external consumers.
2. **Personal automation scripts** (root-level `.py` files) — a private data pipeline that feeds device data into Google BigQuery and sends push notifications via Pushcut.

## Repository Structure

```
findmypy/
├── findmypy/              # Installable Python package
│   ├── __init__.py
│   ├── base.py            # Core: FindMyPyConnection, FindMyPyManager, FindMyPyDevice
│   ├── exceptions.py      # Custom exception hierarchy
│   └── AppleCA.pem        # Apple root CA cert for SSL pinning
│
├── setup.py               # Package metadata (name, version, classifiers)
├── requirements.txt       # Runtime dependencies
├── README.md
│
├── sample.py              # Minimal library usage example
├── icloud.py              # PyiCloudService integration example
├── icloud-test.py         # Ad-hoc PyiCloud testing
│
├── afm_live.py            # Cron tracking pipeline (rollback target — untouched)
├── afm_live_test.py       # Test variant of afm_live.py
├── afm_live_bak.py        # Backup copy of afm_live.py
│
├── afm_live_daemon.py     # Persistent daemon (production) — see util/DAEMON.md
├── afm_pipeline.py        # Shared collection body used by the daemon
├── afm_session_hold.py    # Dead-session hold: stops the launchd restart loop
├── test_session_hold.py   # unittest suite for the hold (stdlib stubs, no creds)
│
├── lib_bq.py              # BigQuery helpers: append_to_bigquery(), materialize_view()
├── lib_bq_changealerts.py # Change-detection and Pushcut alert logic
├── bq.py                  # Older BigQuery alert script
├── bq-append.py           # BigQuery append utility
├── bq-test.py             # Ad-hoc BigQuery testing
│
├── retool.py              # Trigger Retool workflows
├── util/auth.sh           # Clears pyicloud cache, prompts 2FA re-auth
│
└── ctr_last.txt           # Persistent state file for alert deduplication
```

## Core Library (`findmypy/`)

### Classes

**`FindMyPyConnection`** (`base.py:30`)
- Holds Base64-encoded credentials and constructs the per-account API URL.
- `callAPI(url, payload)` — POSTs JSON to Apple's FindMy endpoint with Apple-specific headers; raises `FindMyPyLoginException` on 401, `FindMyPyApiException` on other non-2xx responses.
- SSL verification uses the bundled `AppleCA.pem`, not the system trust store.

**`FindMyPyManager`** (`base.py:49`)
- Manages a `dict` of `FindMyPyDevice` keyed by device ID.
- `init_devices_list()` — initial population; creates new `FindMyPyDevice` instances.
- `refresh_all_device()` / `refresh_device(id)` — update existing device content in place (uses `.update()`), creates new entries if not seen before.
- Remote actions: `play_sound_on_device()`, `display_message_on_device()`, `set_lost_mode_on_device()`.

**`FindMyPyDevice`** (`base.py:199`)
- Thin wrapper around the raw API JSON dict (`self.content`).
- `location()` — calls `refresh_all_device()` and returns `content["location"]`.
- `status(additional=[])` — returns a filtered dict of `batteryLevel`, `deviceDisplayName`, `deviceStatus`, `name`, plus any extra requested fields.
- Delegates all remote actions to `FindMyPyManager`.

### Exception Hierarchy (`exceptions.py`)

```
FindMyPyException
├── FindMyPyApiException(httpcode)   — non-2xx HTTP responses
├── FindMyPyJsonException(reason)    — JSON parse failures
├── FindMyPyNoDevicesException       — API returns no "content" key
└── FindMyPyLoginException           — HTTP 401
```

### Apple API Constants (`base.py`)

| Constant | Value |
|---|---|
| Base URL | `https://fmipmobile.icloud.com` |
| Init/refresh | `/fmipservice/device/<apple_id>/initClient` |
| Play sound | `/playSound` |
| Lost device | `/lostDevice` |
| Send message | `/sendMessage` |
| Timeout | 15 seconds |

## Packaging

- **Package name**: `findmypy`
- **Version**: `0.1.11` — update in `setup.py:6` when releasing
- **Python requirement**: `>=3.7`
- **Package data**: `AppleCA.pem` must be included; it is declared in `setup.py` via `package_data`
- Install: `pip install findmypy` or `pip install -e .` for local dev

## Dependencies

The library itself only requires `requests`. The extra dependencies in `requirements.txt` are for the automation scripts:

| Package | Used by |
|---|---|
| `requests>=2.26.0` | `findmypy` library |
| `pyicloud` | `afm_live.py`, example scripts |
| `pandas` | BigQuery pipeline scripts |
| `google-cloud-bigquery` | `lib_bq.py`, pipeline scripts |
| `pandas-gbq>=0.26.1` | `lib_bq.py` (`append_to_bigquery`) |
| `google-api-python-client` | BigQuery view materialization |
| `google-cloud-bigquery-storage` | Fast BQ reads |
| `db-dtypes` | BQ-compatible Pandas dtypes |

## Development Workflows

### Local setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements.txt
```

### pyicloud library

The production venv (`~/.venv`) uses the **timlaing/pyicloud fork** (not the default PyPI package).
This fork adds SRP-6a support required by Apple after October 2024 and fixes 2FA code delivery.

`afm_live.py` uses `_CronPyiCloudService` (a subclass that overrides `_authenticate()`) so
that if Apple requires 2FA re-validation during a cron run, the script exits cleanly with a
Pushcut alert instead of attempting SRP (which burns rate-limit quota and can corrupt the session).

### Re-authentication (2FA)

Apple sessions expire (typically every 1–3 months for properly trusted clients). When the
Pushcut alert "session needs re-auth" fires, run:

```bash
bash util/auth.sh
```

**Critical safety property:** `auth.py` writes all session files to a temp dir and only
swaps them into `~/.pyicloud` (atomic `os.rename`) once the session is worth keeping.
A failed or interrupted auth run **never touches the live session**.

**Smoke test is not a gate on the 450.** After 2FA, the first FindMy call on a brand-new
session normally returns 450 (Apple has no `_server_ctx` for it yet) and pyicloud raises
`PyiCloudAuthRequiredException` when its own retry also 450s — the same exception
`afm_live.py` classifies as transient and self-healing. `auth.py` therefore retries the
smoke test up to 3× (20 s, then 45 s, re-minting FindMy cookies between attempts) and, if
it still doesn't pass, offers to install anyway (default **yes**). Only a session-level
failure — `PyiCloudFailedLoginException`, `PyiCloud2FARequiredException`,
`PyiCloud2SARequiredException`, `PyiCloudServiceNotActivatedException` — blocks the install,
because those mean Apple rejected the credential itself, not just the FindMy call.
Discarding a freshly trusted session over a 450 is strictly worse than installing it: the
only way back is another SRP round, which is exactly what triggers the 24 h rate limit.

**Never re-send the 2FA code.** pyicloud's `_srp_authentication()` already calls
`_request_2fa_code()`, which sends both the push notification and the SMS. A second
`PUT /verify/phone` from `auth.py` would invalidate the first code and leave the user
waiting for a replacement they have no way to predict is coming.

**`validate_2fa_code()` has two distinct failure shapes.** Returning `False` means the
code was ACCEPTED and `trust_session()` itself failed (propagation lag, or Apple
suppressing trust) — the trust token is on disk and the session is worth keeping, but the
in-memory session is untrusted so `api.devices` cannot work and the smoke test is skipped.
Raising means the code was wrong or the network failed.

**A 421 does not mean the credentials are gone — replay trust first.** Measured
2026-11-03, on a session Apple had been rejecting for hours: `POST /accountLogin`
returned `421 Authentication required for Account`, while `GET
/appleauth/auth/2sv/trust` answered **204 with a complete fresh credential set** —
trust token, session token, session id, scnt, account country. Persisting those
five headers and re-calling `/accountLogin` brought the session back with no SRP
and no 2FA code.

This is also the step `trust_session()` performs and discards: it catches the
exception from the `/accountLogin` that follows the trust GET, logs it, returns
`False`, and leaves the trust token it just received unsaved. That is why
`auth.py` reported `trust_token: False` twice in a row while a replay succeeded
instantly.

`afm_trust_repair.attempt_repair()` is that replay. It is wired into three places,
and always runs **before** the dead-session hold and before any "run auth.sh"
alert:

- `auth.py`, when `validate_2fa_code()` returns `False` — so a re-auth recovers
  itself instead of offering a partial session.
- `afm_live_daemon` startup, via `_build_api_repairing()`, which retries
  `build_api()` once after a successful repair and otherwise re-raises the original
  exception so the existing alert and hold paths are unchanged.
- `afm_live_daemon`'s cycle handlers for `PyiCloudFailedLoginException` /
  `PyiCloud2FARequiredException`, and the post-`force_refresh` failure.

**The repair is rate-limited on purpose.** It costs one GET plus one
`/accountLogin`, so a repair that cannot succeed must not become the hammering
`afm_session_hold` exists to stop: one attempt per 30 min, tracked in
`~/.pyicloud-trust-repair-last`, cleared on success. `auth.py` passes `force=True`
because a human is present and waiting.

**Diagnosing a trust failure:** `util/trust_probe.py` replays the same call and
prints the raw status, header names and body — no SRP, no password, no 2FA code,
so it is safe to run repeatedly. `--repair` persists and re-mints. Token values
are never printed.

```bash
.venv/bin/python util/trust_probe.py
.venv/bin/python util/trust_probe.py --repair
```

**Two session dicts.** Some pyicloud builds store response headers on
`api.session_data`, others on `api.session.data`. A writer that saves only one
silently loses tokens. `afm_trust_repair.session_dicts()` returns every dict the
build exposes, richest first; `auth.py` and the repair both write the **merge** of
all of them. Never write just one, and never hardcode a session filename.

**Session file naming.** This pyicloud build names the session pair after the sanitized
Apple ID — `joejoemosercom.session` / `joejoemosercom.cookiejar` — not `md5(apple_id).session`.
`auth.py`'s post-trust flush asks pyicloud for the path, then falls back to whichever
`*.session` file pyicloud already wrote. An earlier version guessed the md5 name, so the
post-trust `dsWebAuthToken` went into a file pyicloud never reads (that stray
`cd609d0f7969d7e841b161eca48fedf2.session` in `~/.pyicloud` is the leftover) while the real
session file kept the pre-trust token. Never hardcode a session filename here.

**A good session is never thrown away.** When `auth.py` declines to install, it leaves the
temp session on disk and prints its path. Install it later without any SRP or 2FA:

```bash
.venv/bin/python util/auth.py --install-from /var/folders/.../pyicloud-auth-XXXX
```

**State reset on install:** `auth.py` clears `~/.pyicloud-auth-backoff`,
`~/.pyicloud-transient-ctr`, `~/.pyicloud-transient-last`,
`~/.pyicloud-accountlogin-holdoff`, and `~/.pyicloud-expiry-warned`, so the new session
doesn't inherit the old one's backoff or hold-off state. `auth.sh` clears the
`/accountLogin` holdoff only when `auth.py` exits 0 — after a failed run the live session
is still the old one, and its holdoff is what protects it from token burn.

**Rate limiting:** Apple rate-limits SRP after repeated failures. Wait a full 24 hours with
zero auth attempts before retrying. The cron job with `_CronPyiCloudService` does NOT
consume rate-limit quota — it only calls `_authenticate_with_token()`, never SRP.

**Progressive backoff:** After the first auth failure, `afm_live.py` writes `~/.pyicloud-auth-backoff`
and applies escalating delays before retrying Apple's auth endpoint (1st failure: retry
immediately; 2nd: 1 min; 3rd: 2 min; 4th+: 5 min). The following are treated as transient
(no backoff counter increment): 5xx and 429 HTTP responses; `PyiCloudAPIResponseException` with
`code=None` (no HTTP response, i.e. a network-level error); `_CronNetworkError` (raised by
`_authenticate()` when it detects a `PyiCloudAPIResponseException(code=None)` in the exception
cause chain, meaning a `ConnectionError`/`Timeout` was converted by pyicloud before arriving);
raw `PyiCloudAuthRequiredException` at mid-pipeline (FindMy 450 → `_refresh_client_with_reauth()`
aborted, but the session itself is still valid — always self-heals on the next cron run; never
requires `auth.sh`).
`auth.sh` clears this file on successful re-auth so the pipeline resumes on the next cron tick.

**Session lifetime and slow runs:** After `auth.sh`, the cron pipeline holds a `dsWebAuthToken`
that Apple periodically invalidates. The FindMy endpoint returns HTTP 450 when it expires;
`_CronPyiCloudService` catches this and calls `/accountLogin` to refresh the token. A run that
takes 229–380 seconds is a **healthy** 450→/accountLogin recovery, not a failure. The startup
`/validate` call (POST /validate using the 30-day X-APPLE-WEBAUTH-TOKEN) always succeeds, so
errors appearing after "Script started at:" are mid-pipeline FindMy failures, not startup auth
failures. After 6–10 `/accountLogin` renewals over ~2–3 hours, Apple's backend refuses further
renewals and returns "Invalid authentication token" — at that point only `auth.sh` can fix it.
A session whose trust step actually completed gets longer-lived tokens from Apple and tolerates
more renewals. Do NOT run `auth.sh` proactively — unnecessary SRP calls trigger rate limiting
and can result in Apple refusing trust tokens for 24 h.

**Reading `auth.sh` output — what success actually looks like.** Do *not* watch for
`trust_token saved: True`: in `util/auth.py` that line only prints inside the
`*** Code ACCEPTED, but trust_session() failed. ***` branch (`auth.py:174`), i.e. it appears
only on the **degraded** path where Apple took the 2FA code but `/accountLogin` still came back
`hsaTrustedBrowser: False`. A fully healthy run never prints it. The success markers are:

```
  SRP succeeded.
  validate_2fa_code → True  (trust_session called internally)
  Session data flushed to tmp_dir after trust_session ✓
  Cookie jar flushed to tmp_dir after trust_session ✓
Smoke test — FindMy device access...
  N device(s): ...
Installing session → /Users/joemoser/.pyicloud ...
  Files: [...]
AUTH COMPLETE
```

`validate_2fa_code → True` plus a non-empty smoke-test device list is the pass condition —
the smoke test is only reachable when the in-memory session is trusted. If you instead get
`*** Code ACCEPTED, but trust_session() failed. ***`, the code was right but trust did not
hold; answer `N` to the install prompt and retry after 24 h rather than installing a partial
session.

**Alert timing:** The Pushcut alert fires on the **second** consecutive mid-pipeline failure
(not the first), because the first failure's next-attempt wait is 0 min and it sometimes
self-heals on the immediate retry. If the retry also fails, the alert fires and `util/auth.sh`
is required. `PyiCloudAuthRequiredException` mid-pipeline is never counted toward this threshold
— it is treated as a transient 450 and uses the separate 30-minute transient-alert cooldown.

**Startup alert messages distinguish three distinct failure modes:**
- `"iCloud session needs re-auth (run util/auth.sh)"` — `PyiCloudFailedLoginException` or other: session token dead, Apple rejected `/accountLogin`. Run `auth.sh`.
- `"Session untrusted — auth.sh ran but trust didn't hold (run util/auth.sh again, complete full 2FA + trust flow)"` — `PyiCloud2FARequiredException` wrapped in `_CronAuthRequired`: `/accountLogin` succeeded but `is_trusted_session=False`. `auth.sh` was recently run but Apple's trust propagation failed or the trust token wasn't accepted. Run `auth.sh` again and ensure the trust step completes.
- `"FindMy 450 reauth failed (transient — self-heals next run, no action needed)"` — raw `PyiCloudAuthRequiredException` mid-pipeline: uses transient-alert path, never fires "run auth.sh".

**PID lock:** `afm_live.py` holds an exclusive `fcntl.flock` on `/tmp/afm_live.lock` for
its entire run. If a previous instance is still running when the next cron fires, the new
instance exits immediately (`Skipped (still running)`). This prevents two concurrent instances
from both calling Apple auth simultaneously, which reliably invalidates the session.

### Dead-session hold (`afm_session_hold.py`)

Production runs `afm_live_daemon.py` under a `KeepAlive` LaunchAgent with
`ThrottleInterval=300`. On session death the daemon alerts and `exit(1)`, and launchd restarts
it 5 minutes later — so without a brake, a dead session is re-presented to Apple **~288 times a
day**. Per the rate-limiting notes above, that hammering is exactly what makes Apple withhold
trust tokens for 24 h, i.e. the restart loop actively sabotages the `auth.sh` run that is the
only real fix.

`afm_session_hold.py` (stdlib only — no pyicloud/pandas/BigQuery, so it is importable and
testable anywhere) records rejections in `~/.pyicloud-daemon-session-dead`:

- **Armed** when Apple rejects the session — `PyiCloudFailedLoginException`,
  `PyiCloudAuthRequiredException` or `PyiCloud2FARequiredException`, at startup, mid-cycle, or
  after a failed `force_refresh`. Recorded just before the existing exit path.
- **While held**, daemon startup short-circuits and exits `0` *without contacting Apple*, and
  without taking `/tmp/afm_live.lock`.
- **Re-probes** on an escalating schedule — 15 min → 30 min → hourly (capped) — so a refusal
  that turns out to be temporary still recovers within the hour. ~24 auth attempts/day
  instead of ~288.
- **Self-clearing.** The hold is keyed to a fingerprint of `~/.pyicloud` (file names, sizes,
  mtimes). `util/auth.py` installs the new session with an atomic `os.rename`, so every file is
  new and the fingerprint necessarily changes; the next launchd restart drops the hold and
  reconnects. **The recovery procedure is still exactly "run `auth.sh`"** — there is no state
  file to delete by hand. The hold is also cleared whenever `build_api()` succeeds and after
  every healthy cycle.
- **Fails open, always.** Missing, corrupt, truncated, unreadable or inconsistent state — or an
  unexpected exception anywhere in the module — resolves to "no hold". A bug here can only fail
  to suppress a restart; it can never keep a working session offline.
- **Transient failures are untouched.** 5xx, 429 and network errors keep the existing 60 s /
  5-minute cadence. The hold is only for sessions Apple has actually rejected.
- Exception classification, Pushcut alert semantics and the `afm_live.lock` mutual exclusion are
  all unchanged.

It does **not** fix a dead session — nothing in the repo does except `bash util/auth.sh`. It
only stops the daemon from making that recovery harder.

Tests: `python3 -m unittest test_session_hold -v` (stubs pyicloud and `afm_pipeline`, so it runs
without credentials or network). The key regression test simulates an hour of
`ThrottleInterval=300` restarts against a dead session and asserts Apple is contacted 3 times
(initial rejection + probes at +15 m and +45 m) rather than 13.

### Switching to the timlaing fork (deploy process)

If the production venv is on the old PyPI pyicloud, migrate in two steps:

```bash
# Step 1: isolated test (never touches production venv or session)
bash util/test_timlaing_auth.sh   # enters 2FA code, writes to ~/.pyicloud-test

# Step 2: deploy immediately after test completes (within 5 minutes)
bash util/deploy_timlaing.sh      # installs fork, copies session, generates rollback script
```

**Rollback:** `deploy_timlaing.sh` generates `util/rollback_timlaing_TIMESTAMP.sh` before
making any changes. Run it to restore the previous pyicloud version and session.

**Smoke test safety:** The smoke test inside `deploy_timlaing.sh` uses `_SmokeTestService`
(same pattern as `_CronPyiCloudService`) so that a failed token auth exits cleanly rather
than attempting SRP and corrupting the freshly-copied session.

**Session age after deploy:** Expect weeks-to-months before re-auth is needed. Do NOT run
`auth.sh` proactively — unnecessary SRP attempts trigger rate limiting.

### Running the tracking pipeline
```bash
python afm_live.py
```
Intended to run on a cron schedule. It:
1. Connects via `PyiCloudService`
2. Collects location + status data for all tracked devices
3. Appends a row to a per-minute CSV and an aggregate CSV
4. Uploads to BigQuery (`append_to_bigquery`)
5. Materializes BQ views (`materialize_view`)
6. Calls `lib_bq_changealerts.py` logic for change detection and Pushcut notifications

### Testing
There is no automated test suite. Testing is done ad-hoc:
- Use `afm_live_test.py` to test the pipeline without side effects
- Use `bq-test.py` to test BigQuery operations in isolation
- Use `icloud-test.py` to test PyiCloud connectivity

## Key Conventions

- **Naming**: Classes use `PascalCase`; methods and variables use `snake_case`.
- **Type hints**: Present on `__init__` and public method signatures; keep them consistent when adding new methods.
- **Exception handling**: Raise specific `FindMyPy*` exceptions from the library layer; do not swallow exceptions silently. The bare `except` at `base.py:75` is a known issue — prefer `except (json.JSONDecodeError, ValueError)`.
- **API calls**: All calls go through `FindMyPyConnection.callAPI()`. Do not add raw `requests` calls elsewhere in the library.
- **SSL**: Always use the bundled `AppleCA.pem` for verification; do not pass `verify=False`.
- **Credentials**: The library accepts credentials at construction time. Automation scripts currently hardcode some values — prefer environment variables for any new credential references.
- **State files** (`ctr_last.txt`, etc.): Plain text files used to persist alert state between pipeline runs. Do not delete them without understanding downstream alert deduplication logic.

## Known Issues / Technical Debt

- `base.py:75`: Bare `except:` clause should be `except (json.JSONDecodeError, ValueError):`.
- `display_message_on_device` (`base.py:132`) calls the play-sound endpoint instead of `ICLOUD_API_COMMAND_MESSAGE` — this appears to be a bug.
- `init_devices_list` and `refresh_all_device` share almost identical logic and could be deduplicated.
- Some automation scripts contain hardcoded paths (Dropbox), email addresses, and GCP project IDs — these should be moved to environment variables or a config file.

## Scope Boundaries

When modifying this repository, keep these concerns separate:

- Changes to `findmypy/` affect the published library — ensure backward compatibility and bump the version in `setup.py`.
- Changes to root-level scripts (pipeline, BigQuery, alerts) are personal automation and do not affect the published package.
- `AppleCA.pem` should not be changed unless Apple rotates their root CA.
