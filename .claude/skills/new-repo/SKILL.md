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
(visibility, type, auto-deploy); ask for the name and description as free text if
the user hasn't already given them. Don't decide anything the user should — but
do fall back to the noted defaults if they say "whatever" or skip a choice.

- **name** (required) — repo + local dir name, e.g. `weather-bot`.
  Must match `^[A-Za-z0-9._-]+$` (no spaces) or the provisioner will skip it.
- **visibility** — `private` (default) or `public`.
- **type** — `generic` (default), `python`, or `node`. Controls `.gitignore`.
- **description** (optional) — one line; **must not contain a `|`**.
- **autodeploy** — `on` (default) or `off`. When on, the provisioner commits an
  empty `.auto-deploy` flag to the new repo so schedrunner keeps it in sync on
  the Mac (remote-authoritative: pushes to its default branch are pulled, and
  local edits in the Mac's clone are overwritten). Choose `off` for a repo you
  intend to hand-edit on the Mac.
- **source** (optional) — a repo (`name` or `owner/repo`) to **copy** instead of
  scaffolding. When set, the new repo is a clean copy of that repo's current
  snapshot (no fork link, no history); `type` is ignored. Use this when the user
  says "copy X as a new repo named Y" / "duplicate X".

## Step 2 — Append to `repos.register`

Add exactly one `|`-delimited line to `repos.register` at the schedrunner repo
root, in this field order:

```
name|visibility|type|description|autodeploy|source
```

Examples:
- scaffold: `weather-bot|private|python|Fetches and posts the daily forecast.|on`
- copy:     `md-halo|private|generic|Clean copy of iOS-shell.|on|iOS-shell`

- Don't duplicate a name that's already listed.
- Leave existing entries untouched.
- Trailing fields may be omitted: `autodeploy` defaults to `on`, `source` to
  empty (scaffold). Include `source` only to copy an existing repo.

## Step 3 — Commit & push (follow the PR workflow)

Per this repo's CLAUDE.md, commit on a feature branch and open a PR — never
straight to the default branch, and start a fresh branch/PR if the previous one
already merged. **The Mac auto-deploys schedrunner's _default_ branch**, so the
repo is provisioned only once the new entry reaches the default branch (i.e.
after the PR merges).

## Step 4 — Tell the user what happens next

Explain: once the PR merges, the Mac's next provisioning tick (every ~2 min)
creates `<owner>/<name>` with starter files and pushes it; then they can open it
in a fresh Claude Code session (phone included). With `autodeploy=on` (the
default) the repo ships with a `.auto-deploy` flag, so schedrunner already keeps
the Mac's clone in sync — no extra step. If they also want it to run on a
*schedule*, point them at schedrunner's `register.sh` (see the repo CLAUDE.md);
that's separate from auto-deploy.

## Notes

- Provisioning is idempotent: existence on GitHub is the gate, so the register
  never needs editing once a repo is created.
- The starter `CLAUDE.md` (`templates/CLAUDE.md.tmpl`) points at schedrunner, so
  every provisioned repo is born schedrunner-aware.
- If you happen to be in a session that *can* create repos directly (a Mac
  session with authenticated `gh`, or a cloud session whose GitHub scope allows
  it), you may instead run `provision-repos.sh` yourself, or create + push the
  scaffold directly — the end state is the same.
