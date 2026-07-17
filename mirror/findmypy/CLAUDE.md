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
├── afm_live.py            # Production tracking pipeline (runs on a schedule)
├── afm_live_test.py       # Test variant of afm_live.py
├── afm_live_bak.py        # Backup copy of afm_live.py
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
copies them to `~/.pyicloud` after full success (SRP + 2FA + `trust_session()` + devices
accessible). A failed or interrupted auth run **never touches the live session**.

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
Getting `trust_token saved: True` from `auth.sh` allows Apple to issue longer-lived tokens and
tolerate more renewals per session. Do NOT run `auth.sh` proactively — unnecessary SRP calls
trigger rate limiting and can result in Apple refusing trust tokens for 24 h.

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
