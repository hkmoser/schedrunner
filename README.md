# schedrunner

A lightweight shell-based task scheduler for macOS. It runs as a LaunchAgent (via `loader/`) and executes scripts on configurable intervals or daily schedules — no cron, no dependencies beyond bash.

## How it works

`runner.sh` is called every minute by the LaunchAgent. It reads `scripts.conf` and decides which scripts are due to run based on their cadence. Output from each script is appended to a per-script log file under `log/`.

## Configuration — `scripts.conf`

Each non-comment line is a `|`-delimited record:

```
cadence_type|cadence_value|script_path [args...]
```

### Cadence types

| Type | Value | Behavior |
|------|-------|----------|
| `interval` | minutes (integer) | Run every N minutes |
| `daily` | `HH:MM` (24h) | Run once per day at that time |
| `startup` | (ignored) | Run once when uptime < 90 s |

### Example

```
# Run every minute
interval|1|/Users/joemoser/Dropbox/Source/schedrunner/auto-deploy.sh

# Run every 5 minutes
interval|5|/Users/joemoser/Dropbox/Source/afm/findmypy/.venv/bin/python /Users/joemoser/Dropbox/Source/afm/findmypy/afm_live.py

# Run once daily at 8:02 am
daily|08:02|/Users/joemoser/Dropbox/Source/afm/ynab/get_all.py
```

Lines beginning with `#` are ignored. The `script_path` field may include arguments (everything after the first space is passed to `eval`).

## Installation

```bash
cd loader
bash install.sh
```

This copies the LaunchAgent plist to `~/Library/LaunchAgents/` and loads it.

## Uninstall

```bash
cd loader
bash uninstall.sh
```

## Scripts included

| Script | Cadence | Description |
|--------|---------|-------------|
| `auto-deploy.sh` | every 1 min | Scans `~/Dropbox/Source/` for repos with a `.auto-deploy` flag file; fetches origin and resets to remote if ahead, then runs `.auto-deploy` as a post-pull hook if non-empty. |
| `runner.sh` | (the scheduler itself) | Reads `scripts.conf` and dispatches due scripts. |

## Logs

Each script's output is written to `log/<script-basename>.log`. The `log/` directory and `*.log` files are git-ignored.

## Runtime state

`.last_run_times` tracks when each script last ran (used by the interval logic). It is git-ignored.
