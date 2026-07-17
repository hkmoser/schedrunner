# CLAUDE.md

Guidance for Claude Code (and other agents) working in **sync-ynab**.

## Project

YNAB to BigQuery sync

## Pull request workflow

Make changes on a feature branch and open a PR — never commit straight to the
default branch. **If your previous PR has already been merged, do not keep
pushing to that merged branch.** Once a PR is merged, start a brand-new branch
off the latest default branch and open a **new** PR for the next set of changes.

## Scheduling & auto-deploy (schedrunner)

This machine runs **schedrunner**, a lightweight shell scheduler at
`~/Dropbox/Source/schedrunner/`. To run a script in this repo on a schedule, or
to auto-deploy this repo on every push, read
`~/Dropbox/Source/schedrunner/CLAUDE.md` — it is the canonical registration
contract. In short:

- **Scheduled execution:** add an `interval` / `daily` / `startup` line to
  schedrunner's `scripts.conf` pointing at an **absolute** path in this repo
  (or use schedrunner's `register.sh` helper).
- **Auto-deploy:** add a `.auto-deploy` file to this repo's root — empty means
  "pull-only", a non-empty file is run as a bash post-pull hook on every remote
  advance. The repo must be cloned under `~/Dropbox/Source/`.

These two are independent; use either or both.
