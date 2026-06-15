# Launchd gap test

`test/launchd-gap-test.sh` verifies the one thing that can only be checked under
real launchd (not by running `runner.sh` directly): that the reworked LaunchAgent
keys behave as intended.

- **AbandonProcessGroup=true** — a script the runner backgrounds keeps running
  and logging *after* the short-lived runner process exits, instead of being
  killed by launchd. This is what makes the non-blocking, detached design safe.
- **KeepAlive=false** — launchd does not respawn the job the instant it exits
  (no respawn storm); `StartInterval` alone drives the cadence.

## How it stays isolated

- Uses a **temporary** LaunchAgent with its own label,
  `com.joemoser.runner-selftest` — never `com.joemoser.runner`.
- Runs a tiny self-contained launcher in a sandbox at
  `~/schedrunner-launchd-test`; it does not read or run the real `runner.sh`,
  `scripts.conf`, or any live file, and nothing is placed in
  `~/Library/LaunchAgents`.
- A cleanup trap reverses everything (unload the temp agent, remove the sandbox)
  on normal exit **and** on Ctrl-C / error.

## Run it

```bash
bash test/launchd-gap-test.sh
```

No sudo needed. It takes ~40s (it waits for a 20s background run to outlive its
launcher). The script prints three sections:

- **(a) setup** — writes the sandbox + temp plist and `launchctl bootstrap`s it.
- **(b) results** — `PASS`/`FAIL` for AbandonProcessGroup and KeepAlive, plus the
  raw `ticks.log` / `child.log`.
- **(c) teardown** — unloads the temp agent and deletes the sandbox.

Expected: both `PASS`, summary `2 passed, 0 failed`.

If your macOS rejects `launchctl bootstrap`, the script prints the legacy
`launchctl load -w` / `unload` equivalents to use instead.
