# afm_live_daemon — deploy & rollback

Persistent replacement for the cron-spawned `afm_live.py`. Keeps one iCloud
session warm in memory so `/accountLogin` drops from ~144/day to a handful,
which is what was exhausting Apple's per-session renewal quota and killing
tracking every few hours. See the header of `afm_live_daemon.py` for the full
rationale, and `util/probe_session.py` for the diagnostic that proved it.

## Files

| File | Role |
|---|---|
| `afm_live_daemon.py` | The daemon: builds one session, loops `run_pipeline` every `AFM_INTERVAL_SECS`. |
| `afm_pipeline.py` | Shared collection body (device loop → CSV → BigQuery → alerts). |
| `afm_live.py` | **Untouched.** The cron fallback — instant rollback target. |
| `util/com.joemoser.afm-daemon.plist` | KeepAlive LaunchAgent (no StartInterval). launchd keeps the daemon alive. |
| `util/install_daemon.sh` | Idempotent (re)install of the LaunchAgent. Called by auto-deploy. |

## How schedrunner owns it

schedrunner auto-deploys the repo, so it owns the daemon by **installing the
LaunchAgent on deploy**: the deploy runs `util/install_daemon.sh`, which copies
`com.joemoser.afm-daemon.plist` into `~/Library/LaunchAgents` and (re)loads it.
From then on **launchd** keeps the daemon alive — `KeepAlive=true` restarts it if
it ever exits (crash, or session-death exit), throttled by `ThrottleInterval`
(300s). The daemon is persistent and loops internally every `AFM_INTERVAL_SECS`;
there is no StartInterval. That is the "owned by schedrunner (via auto-deploy) but
not run on the schedule" model — schedrunner installs it, launchd supervises it.

## Pre-flight (do this BEFORE flipping schedrunner over)

The pipeline can't run in CI — it needs the real session, BigQuery creds, and
Pushcut. Validate on the Mac first:

1. **Session healthy?** Confirm `util/auth.sh` last reported `trust_token saved: True`.
2. **One-cycle smoke test**, with the cron still owning the schedule (the daemon
   grabs `/tmp/afm_live.lock`, so a cron tick during the test just no-ops):

   ```bash
   cd /Users/joemoser/Dropbox/Source/afm/findmypy
   AFM_INTERVAL_SECS=999999 .venv/bin/python afm_live_daemon.py
   ```

   Watch for: `session constructed`, `Device loop done: N device(s)`, the BigQuery
   append + materialize lines, then `[cycle] OK`. Ctrl-C to stop after the first
   cycle. Confirm a row landed in `home_afm.afm_latest_live` for this minute and
   the CSV under `afm/` matches what a cron run produces.
3. Only after that looks right, hand the plist to schedrunner.

## Cut over

Cutover is automatic on the next deploy: findmypy's `.auto-deploy` hook runs
`bash util/install_daemon.sh`, which (re)installs and loads the LaunchAgent.
launchd then starts the daemon (RunAtLoad) and keeps it alive (KeepAlive).

**Disabling the cron version needs no schedrunner edit.** The daemon holds
`/tmp/afm_live.lock` for its whole life, so every `afm_live.py` tick schedrunner
still dispatches sees the lock and exits in <1s ("Skipped (still running)").
The cron path is effectively off while the daemon runs, and `afm_live.py` is never
modified. (If you later want schedrunner to stop even dispatching the no-op tick,
remove the `afm_live.py` entry from the schedrunner repo's job list — optional.)

BigQuery uses gcloud ADC (auto-found via `$HOME`) and Pushcut is in the script,
so the plist needs no credential env — nothing to fill in.

To start it right now without waiting for a deploy: `bash util/install_daemon.sh`.

Then tail the log:
```bash
tail -f ~/log/afm-daemon.log
```
Expect `[auth] session constructed` **once** at startup, then one `[cycle] OK`
every ~5 min. `/accountLogin` should be near-silent.

## Rollback (instant)

```bash
bash util/install_daemon.sh uninstall
```
Uninstalling stops the daemon and releases `/tmp/afm_live.lock`, so the cron
`afm_live.py` resumes on its next tick with zero code changes. To make rollback
survive future deploys, also revert the `bash util/install_daemon.sh` line in
`.auto-deploy` (otherwise the next deploy reinstalls the daemon).

> Note: while the agent is loaded, `KeepAlive=true` means launchd restarts the
> daemon if you just `kill` it. To actually stop it, use `install_daemon.sh
> uninstall` (i.e. `launchctl bootout`), not `pkill`.

## Behavior notes

- **Session death** (needs `auth.sh`): the daemon sends one Pushcut alert
  (deduped 30 min) and exits; launchd restarts it every `ThrottleInterval` (300s).
  After you run `auth.sh`, the next restart picks up the fresh session — no manual
  daemon restart needed.
- **Transient errors** (Apple 5xx/429, network, an un-self-healed 450): logged,
  retried after 60s, session kept in memory. No exit.
- **Data-plane errors** (BigQuery/pandas/alerts): logged + alerted, but the daemon
  does **not** die — the session is fine and the next cycle proceeds.
- **Mutual exclusion**: the daemon holds `/tmp/afm_live.lock` for its whole life,
  so a stray cron `afm_live.py` tick can never run a second concurrent session.
- **CSV retention**: BigQuery is the only consumer, so the per-minute `afm/*.csv`
  files are scratch. The daemon runs `cleanup_old_csvs` at startup and every 24h,
  deleting `afm/afm_*.csv` older than `AFM_CSV_RETENTION_DAYS` (default 7). The
  legacy aggregate `afm_all.csv` is no longer written (it was dead weight and the
  worst Dropbox churn) and ages out of the folder within the retention window.
  Rollback note: the untouched `afm_live.py` fallback still writes `afm_all.csv` if
  you ever revert to it.
