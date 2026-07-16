# CLAUDE.md — data collector

## What this repo is
A scheduled data collector. schedrunner reads `service.yaml` to run it on a cron.

## Conventions
- Node 20+, ESM ("type": "module"), no build step unless needed.
- Collector logic in `src/`; output markdown in `data/`.
- Single-purpose. If it grows a second job, split the repo.

## Managed files — do not hand-edit
Files in `managed-files.txt` are owned by the template and synced from it.
Edit them upstream in the template, not here — local edits are overwritten on sync.

## Pull requests
Before opening a PR, check whether an open PR for this work already exists.
If one is open, push to its branch. If it was closed or merged, open a fresh PR.
