---
name: new-repo
description: Register a new GitHub repository for creation. Prompts a short series of setup questions, then appends the repo spec to schedrunner's repos.register and pushes it — the Mac mini's provisioner (provision-repos.sh) then creates the repo with starter files and pushes it to GitHub. Use when the user asks to create, spin up, scaffold, or bootstrap a new repo or project.
---

# new-repo

Queue a new repository for creation. The session you're in (often cloud/mobile)
usually can't create GitHub repos directly, so this skill instead **records the
desired repo in schedrunner's `repos.register`**. The Mac mini runs
`provision-repos.sh` on a schedule; for every register entry whose repo does not
yet exist on GitHub it creates it, scaffolds starter files (CLAUDE.md, README,
.gitignore), and pushes it. Existing repos are skipped, so the register is a
declarative manifest, not a queue you clean up.

## When to use

"create a new repo for X", "spin up a project called Y", "scaffold a repo", etc.

## Step 1 — Ask the setup questions

Collect the fields below. Use `AskUserQuestion` for the multiple-choice ones
(visibility, type, and whether to register it with schedrunner now); ask for the
name and description as free text if the user hasn't already given them. Don't
decide anything the user should — but do fall back to the noted defaults if they
say "whatever" or skip a choice.

- **name** (required) — repo + local dir name, e.g. `weather-bot`.
  Must match `^[A-Za-z0-9._-]+$` (no spaces) or the provisioner will skip it.
- **visibility** — `private` (default) or `public`.
- **type** — `generic` (default), `python`, or `node`. Controls `.gitignore`.
- **description** (optional) — one line; **must not contain a `|`**.

## Step 2 — Append to `repos.register`

Add exactly one `|`-delimited line to `repos.register` at the schedrunner repo
root, in this field order:

```
name|visibility|type|description
```

Example: `weather-bot|private|python|Fetches and posts the daily forecast.`

- Don't duplicate a name that's already listed.
- Leave existing entries untouched.

## Step 3 — Commit & push (follow the PR workflow)

Per this repo's CLAUDE.md, commit on a feature branch and open a PR — never
straight to the default branch, and start a fresh branch/PR if the previous one
already merged. **The Mac auto-deploys schedrunner's _default_ branch**, so the
repo is provisioned only once the new entry reaches the default branch (i.e.
after the PR merges).

## Step 4 — Tell the user what happens next

Explain: once the PR merges, the Mac's next provisioning tick (every ~2 min)
creates `<owner>/<name>` with starter files and pushes it; then they can open it
in a fresh Claude Code session (phone included). If they want it to also run on a
schedule or auto-deploy, point them at schedrunner's `register.sh` / `.auto-deploy`
(see the repo CLAUDE.md) — those are separate, independent steps.

## Notes

- Provisioning is idempotent: existence on GitHub is the gate, so the register
  never needs editing once a repo is created.
- The starter `CLAUDE.md` (`templates/CLAUDE.md.tmpl`) points at schedrunner, so
  every provisioned repo is born schedrunner-aware.
- If you happen to be in a session that *can* create repos directly (a Mac
  session with authenticated `gh`, or a cloud session whose GitHub scope allows
  it), you may instead run `provision-repos.sh` yourself, or create + push the
  scaffold directly — the end state is the same.
