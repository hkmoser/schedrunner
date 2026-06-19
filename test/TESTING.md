# Testing

Run the whole suite:

```bash
bash test/run.sh
```

It runs every `test/test_*.sh` and exits non-zero if any assertion fails. CI runs
it on every push and PR (`.github/workflows/test.yml`). See the **Testing
discipline** section in `../CLAUDE.md` for the rules every change must follow.

## How the suite is built

- **`lib.sh`** — tiny assertion helpers (`assert_eq`, `assert_contains`,
  `assert_file`, `assert_status`, `poll_until`, …) plus a hermetic environment:
  git config is isolated to a temp file (no host config, no commit signing), and
  temp dirs are cleaned up automatically.
- **Real collaborators, not mocks.** Tests drive the *real* scripts with real
  `git` against throwaway local repos in temp dirs. The only thing stubbed is
  `gh` (an exported shell function in `lib.sh`), because it is the external
  service that talks to GitHub. Configure it per-test with `GH_STUB_*` env vars.
- Each `test_*.sh` is self-contained: source `lib.sh`, run cases, call `finish`.

## What's covered

| File                    | Script under test    | Cases (happy path + edge/error)                                   |
|-------------------------|----------------------|-------------------------------------------------------------------|
| `test_register.sh`      | `register.sh`        | valid interval/daily/startup, multi-token, duplicate, bad value/time, unknown cadence, relative path, too few args |
| `test_runner.sh`        | `runner.sh`          | interval due/not-due, comment/blank/malformed, bad interval, unknown cadence, bad daily time, daily non-match, live lock skip, stale lock reclaim |
| `test_auto_deploy.sh`   | `auto-deploy.sh`     | reset on advance (empty flag), post-pull hook runs, up-to-date no-op, failing hook reported, single-instance lock |
| `test_provision.sh`     | `provision-repos.sh` | scaffold new repo, autodeploy=off, python gitignore, already-exists skip, malformed name, comments, unauthenticated gh, create failure |
| `test_syntax.sh`        | all `*.sh`           | every shell script parses (`bash -n`)                              |

## Known coverage gaps (need real launchd / a real clock)

These can't be asserted deterministically by running the scripts directly:

- **`runner.sh` `startup` happy path** and **`daily` happy path** depend on the
  machine clock / `/proc/uptime`; the suite covers their validation and
  non-matching branches instead.
- **LaunchAgent behavior** (`AbandonProcessGroup`, `KeepAlive`) — covered by the
  manual macOS-only check below.

## Manual macOS check: `launchd-gap-test.sh`

`test/launchd-gap-test.sh` verifies the one thing only real launchd can show:
that a script the runner backgrounds keeps running after the runner exits
(`AbandonProcessGroup=true`) and that launchd does not respawn the job
immediately (`KeepAlive=false`). It is **not** part of `run.sh` (it needs a macOS
launchd session) and is fully self-isolated with a temporary LaunchAgent.

```bash
bash test/launchd-gap-test.sh   # ~40s, no sudo; expect "2 passed, 0 failed"
```

It uses a temporary LaunchAgent label (`com.joemoser.runner-selftest`) in a
sandbox under `$HOME`; it never touches `com.joemoser.runner`, the live repo,
`scripts.conf`, or `~/Library/LaunchAgents`, and a trap reverses everything on
exit or Ctrl-C.
