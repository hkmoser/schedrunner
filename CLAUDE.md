# CLAUDE.md

Guidance for Claude Code (and other agents) on how to register a repo with
**schedrunner** — the lightweight shell scheduler that lives in this repo.

This file is the canonical reference for the registration contract. When you
are working in *another* repo and want it to run on a schedule and/or
auto-deploy on every push, follow the recipes below.

## Pull request workflow (read first)

Make changes on a feature branch and open a PR — never commit straight to the
default branch. **If your previous PR has already been merged, do not keep
pushing to that merged branch.** Once a PR is merged its branch is closed
history; start a brand-new branch off the latest default branch and open a
**new** PR for the next set of changes. One merged PR = one done unit of work;
every new change after that needs its own fresh branch and PR.

## What schedrunner is

`runner.sh` is launched once a minute by a macOS LaunchAgent
(`loader/com.joemoser.runner.plist`). On each tick it reads `scripts.conf` and
runs any script that is "due". A separate script, `auto-deploy.sh`, is itself
registered in `scripts.conf` to run every minute and handles git-based
auto-deployment for any repo that opts in.

So there are **two independent ways** to register a repo. Pick whichever fits
(you can use both):

1. **Scheduled execution** — run a script on an interval / daily / at startup.
2. **Auto-deploy** — pull and redeploy a repo automatically whenever its remote
   advances.

Conventions assumed by schedrunner:
- Repos live under `~/Dropbox/Source/` (i.e. `/Users/joemoser/Dropbox/Source/`).
  schedrunner itself is at `~/Dropbox/Source/schedrunner/`.
- All paths in `scripts.conf` are **absolute**.

---

## 1. Register for scheduled execution (`scripts.conf`)

Add one line per script to `scripts.conf` in the schedrunner repo. Each
non-comment line is a `|`-delimited record:

```
cadence_type|cadence_value|script_path [args...]
```

### Cadence types

| Type       | Value           | Behavior                                  |
|------------|-----------------|-------------------------------------------|
| `interval` | minutes (int)   | Run every N minutes                       |
| `daily`    | `HH:MM` (24h)   | Run once per day at that wall-clock time  |
| `startup`  | (ignored)       | Run once when machine uptime < 90 s       |

### Rules & gotchas

- **Use absolute paths.** schedrunner does not `cd` into your repo before
  running; the script must work from any working directory.
- **Make the script executable** (`chmod +x your_script.sh`) or invoke it
  through an interpreter (see the Python example below).
- Everything after the first space in the `script_path` field is treated as
  arguments and passed to `eval`, so you can include interpreters and flags.
- `interval` cadence is anchored to the last run recorded in
  `.last_run_times`, not to a fixed clock; `elapsed >= N` triggers a run.
- `daily` fires only when the current `HH:MM` matches exactly, and only once
  per day — so the time you pick must align with a minute boundary.
- Lines beginning with `#` and malformed lines are skipped.
- Each script's stdout/stderr is appended to `log/<script-basename>.log`
  (git-ignored). Scripts run in the background; runner waits for them to finish.

### Examples

```conf
# Run a shell script every 5 minutes
interval|5|/Users/joemoser/Dropbox/Source/myrepo/heartbeat.sh

# Run a Python script via its venv every 10 minutes (note the interpreter)
interval|10|/Users/joemoser/Dropbox/Source/myrepo/.venv/bin/python /Users/joemoser/Dropbox/Source/myrepo/job.py

# Run once daily at 08:02
daily|08:02|/Users/joemoser/Dropbox/Source/myrepo/daily_report.sh

# Run once shortly after boot
startup|ignored|/Users/joemoser/Dropbox/Source/myrepo/on_boot.sh
```

### Steps to register

1. In your repo, add the script you want scheduled and make it executable.
2. Edit `~/Dropbox/Source/schedrunner/scripts.conf` and add one record per the
   format above, using an **absolute** path.
3. Commit the change to schedrunner. The next minute tick picks it up — no
   reload needed (the LaunchAgent re-reads `scripts.conf` every run).
4. Confirm it ran by checking `~/Dropbox/Source/schedrunner/log/<script>.log`.

---

## 2. Register for auto-deploy (`.auto-deploy` flag file)

`auto-deploy.sh` runs every minute (it is registered in `scripts.conf`) and
scans every git repo under `~/Dropbox/Source/` for a `.auto-deploy` flag file.
For each flagged repo it:

1. Detects the default branch (`origin/HEAD`, falling back to `master`/`main`).
2. `git fetch origin <branch>`.
3. If the remote has advanced, `git reset --hard FETCH_HEAD` (this **discards
   local changes** in that repo — auto-deploy repos are treated as
   remote-authoritative deploy targets, not workspaces).
4. If `.auto-deploy` is **non-empty**, runs it with `bash .auto-deploy` from the
   repo root as a post-pull hook.

### How to opt in

From your repo root, create the flag file and commit it:

```bash
# Reset-only: keep the working tree in sync with origin, no post-pull step
touch .auto-deploy

# Reset + run a build/update step on every new commit
echo 'make update' > .auto-deploy

# Multi-step post-pull hook
cat > .auto-deploy <<'EOF'
#!/bin/bash
set -euo pipefail
make build
./scripts/restart.sh
EOF

git add .auto-deploy && git commit -m "Enable schedrunner auto-deploy"
```

### Notes & gotchas

- The repo must be cloned under `~/Dropbox/Source/` and have an `origin`
  remote on its default branch.
- `.auto-deploy` runs from the repo root, so relative paths inside it are fine.
- Because step 3 does a hard reset, **never use an auto-deploy repo for local
  edits** — anything not pushed to origin will be wiped on the next advance.
- An **empty** `.auto-deploy` means "reset only, no hook". A non-empty file is
  executed as bash.
- Output is captured to `~/Dropbox/Source/schedrunner/log/auto-deploy.sh.log`.
- Keep `.auto-deploy` idempotent — it runs on every remote advance.

---

## 3. Provision brand-new repos (`repos.register`)

The two mechanisms above register an *existing* repo. To create a **new** repo,
add a line to `repos.register` instead. `provision-repos.sh` runs on the Mac via
schedrunner (registered in `scripts.conf`); for each entry whose repo does not
yet exist on GitHub it scaffolds starter files (CLAUDE.md, README, .gitignore)
under `~/Dropbox/Source/<name>` and creates + pushes the repo. Repos that already
exist are skipped, so the file is a declarative manifest and is never rewritten
by the provisioner.

Format (`|`-delimited), one repo per line:

```
name|visibility|type|description|autodeploy
```

- `visibility`: `private` (default) or `public`.
- `type`: `generic` (default), `python`, or `node` — controls `.gitignore`.
- `autodeploy`: `on` (default) or `off`. When on, the provisioner commits an
  empty `.auto-deploy` flag to the new repo so schedrunner keeps it in sync on
  the Mac (see section 2). Use `off` for a repo you intend to hand-edit locally.
- `name` must match `^[A-Za-z0-9._-]+$`; `description` must not contain `|`.

This exists because cloud/mobile Claude Code sessions generally can't create
GitHub repos directly, but the Mac (with authenticated `gh`) can. The easiest
way to add an entry is the **`/new-repo` skill**, which prompts for the fields
and appends the line. Because the Mac auto-deploys schedrunner's **default
branch**, a new entry is provisioned only once it reaches the default branch
(after its PR merges). Requires `gh` authenticated on the Mac.

---

## Quick reference

| I want to…                                   | Do this                                                            |
|----------------------------------------------|-------------------------------------------------------------------|
| Run a script every N minutes / daily / boot  | Add an `interval`/`daily`/`startup` line to `scripts.conf`        |
| Auto-pull + redeploy on every push           | Add a `.auto-deploy` file to the repo root                         |
| Both                                          | Do both — they are independent                                    |
| Create a brand-new repo                       | Add a line to `repos.register` (or use the `/new-repo` skill)      |
| Inspect what happened                         | Read `log/<script-basename>.log` in the schedrunner repo           |
| Install / uninstall the scheduler            | `cd loader && bash install.sh` (or `uninstall.sh`)                 |
