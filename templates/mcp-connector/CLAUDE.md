# CLAUDE.md — MCP connector

## What this repo is
An MCP server running as a Cloudflare Worker. schedrunner deploys it via `service.yaml` (wrangler deploy) on change.

## Conventions
- TypeScript, ESM. Tools defined in `src/`.
- Enforce business logic (append-only, pending-only writes, auth) in code, not prompts.
- Each tool has a name, description, and inputSchema.

## Managed files — do not hand-edit
Files in `managed-files.txt` are owned by the template and synced from it.
Edit them upstream in the template, not here — local edits are overwritten on sync.

## Pull requests
Before opening a PR, check whether an open PR for this work already exists.
If one is open, push to its branch. If it was closed or merged, open a fresh PR.
