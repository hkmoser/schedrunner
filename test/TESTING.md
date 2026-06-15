# Testing the non-blocking runner on the Mac (without touching the live setup)

This lets you verify the reworked `runner.sh` (detached scripts, per-script
locks, no blocking `wait`) **before** it becomes your live scheduler. None of
the steps below modify your running schedrunner: the live repo at
`~/Dropbox/Source/schedrunner` stays on its current branch, the installed
LaunchAgent (`com.joemoser.runner`) is untouched, and the live `scripts.conf`,
`log/`, and `.last_run_times` are never read or written.

## 1. Automated behavior test (recommended)

The test copies `runner.sh` into a private temp sandbox, rewrites its lock path
into that sandbox, and runs ticks against throwaway scripts. It asserts:

- the runner returns immediately even when a script sleeps for seconds
  (non-blocking);
- scripts in a tick run in parallel and each logs to its own file;
- a script that outlives its interval is **skipped**, not piled up;
- a stale lock (dead pid) is reclaimed;
- locking **fails open** — if the lock dir is unusable, the script still runs.

### Get the branch onto the Mac without disturbing the live checkout

Use a separate worktree so your live `~/Dropbox/Source/schedrunner` stays on its
current branch and keeps running normally:

```bash
cd ~/Dropbox/Source/schedrunner
git fetch origin claude/schedrunnwr-claude-md-5bktku
git worktree add /tmp/schedrunner-test claude/schedrunnwr-claude-md-5bktku
```

`/tmp/schedrunner-test` is outside `~/Dropbox/Source/`, so neither Dropbox nor
auto-deploy touches it.

### Run it

```bash
cd /tmp/schedrunner-test
bash test/test-runner.sh
```

Expect `RESULT: 8 passed, 0 failed` and exit code 0. The run takes ~15s (it
waits on deliberately slow test scripts).

### Clean up when done

```bash
cd ~/Dropbox/Source/schedrunner
git worktree remove /tmp/schedrunner-test
```

> Prefer a plain clone? `git clone -b claude/schedrunnwr-claude-md-5bktku <repo-url> /tmp/schedrunner-test`
> works too — just run the test from there and `rm -rf` it afterward.

## 2. Optional: verify the launchd part by hand

The automated test exercises all of `runner.sh`'s logic but **cannot** verify
the one launchd-specific guarantee: that `AbandonProcessGroup=true` keeps
detached scripts alive after `runner.sh` exits. To check that directly, run a
**temporary** LaunchAgent with its own label (it never touches
`com.joemoser.runner`):

```bash
SB=/tmp/schedrunner-ld-test
mkdir -p "$SB/log"
# use the reworked runner from the worktree, with its lock dir inside the sandbox
sed "s#^LOCK_BASE=.*#LOCK_BASE=\"$SB/locks\"#" /tmp/schedrunner-test/runner.sh > "$SB/runner.sh"
: > "$SB/.last_run_times"
printf '#!/bin/bash\nsleep 25\necho "survived at $(date)"\n' > "$SB/slow.sh"
chmod +x "$SB/slow.sh"
echo "interval|1|$SB/slow.sh" > "$SB/scripts.conf"

cat > "$SB/com.joemoser.runner-selftest.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.joemoser.runner-selftest</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$SB/runner.sh</string></array>
  <key>StartInterval</key><integer>10</integer>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>$SB/runner.out</string>
  <key>StandardErrorPath</key><string>$SB/runner.err</string>
</dict></plist>
PLIST

launchctl bootstrap gui/$(id -u) "$SB/com.joemoser.runner-selftest.plist"
echo "Loaded. Waiting 30s for a slow run to outlive the runner..."
sleep 30
echo "---- slow.sh log ----"; cat "$SB/log/slow.sh.log"
```

You should see a `Running ...slow.sh` line and then, ~25s later, a
`survived at ...` line — proving the script kept running and logging after the
short-lived runner exited. You should also see multiple ticks (the runner fired
again on schedule and logged `Skipped (still running)` while the slow one ran).

Tear it down:

```bash
launchctl bootout gui/$(id -u)/com.joemoser.runner-selftest
rm -rf /tmp/schedrunner-ld-test
```

## 3. What this does not deploy

Running these tests changes nothing about your live scheduler. To actually adopt
the new behavior you still need the normal rollout: merge the branch to the
default branch, `git pull` it on the Mac, and re-run `loader/install.sh` so the
LaunchAgent reloads with the new `KeepAlive`/`AbandonProcessGroup` keys.
