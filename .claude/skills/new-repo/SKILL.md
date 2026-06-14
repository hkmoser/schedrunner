---
name: new-repo
description: Scaffold and create a new GitHub repository that is "schedrunner-aware" — generate starter files (CLAUDE.md, .gitignore, README), create the repo on GitHub, and push so it can be opened immediately in Claude Code (including on mobile/web). Use when the user asks to create, spin up, scaffold, or bootstrap a new repo or project.
---

# new-repo

Create a new GitHub repository pre-wired with starter files so it can be opened
in a fresh Claude Code session right away, and so the agent in that session
already knows this machine runs **schedrunner** (the scheduler in this repo).

## When to use

The user says something like "create a new repo for X", "spin up a project
called Y", "scaffold a new repo", etc.

## Inputs to gather

Ask only for what's missing; infer sensible defaults otherwise.

- **name** (required): the repo name, e.g. `weather-bot`.
- **description** (optional): one line describing the repo.
- **visibility** (optional, default `private`): `private` or `public`.
- **type** (optional, default `generic`): `generic`, `python`, or `node` —
  controls which `.gitignore` lines and starter files are emitted.
- **schedrunner** (optional): whether this repo should be registered for
  scheduled execution and/or auto-deploy now. Default: no — just emit the
  CLAUDE.md hint so it can be registered later. Only set it up if the user asks.

## Steps

1. **Generate the starter files** from `templates/` in this skill directory.
   Substitute `{{REPO_NAME}}`, `{{DESCRIPTION}}`, and `{{YEAR}}`:
   - `CLAUDE.md`   ← `templates/CLAUDE.md.tmpl` (makes the repo schedrunner-aware)
   - `.gitignore`  ← `templates/gitignore.tmpl` (append the `python`/`node`
     section if that `type` was chosen)
   - `README.md`   ← `templates/README.md.tmpl`
   - If `type` is `python`, you may also add a minimal entry point; if `node`,
     a minimal `package.json`. Keep it small — the user can flesh it out.

2. **Create the repo on GitHub and push.** Prefer whichever is available:
   - **Cloud / mobile / web sessions (no Mac):** use the GitHub MCP tools —
     `mcp__github__create_repository` to create it, then `mcp__github__push_files`
     to push the starter files to the default branch in one commit. This needs
     no local clone and no LaunchAgent, so it works from iPhone.
   - **Local Mac session:** `gh repo create <name> --<private|public>`, then
     `git init`, add the starter files, commit, and `git push -u origin main`.
     If the user wants it under schedrunner's conventions, clone/create it under
     `~/Dropbox/Source/`.

3. **(Only if the user asked) Register with schedrunner.** schedrunner registers
   repos two independent ways — see `../../../CLAUDE.md` for the full contract:
   - **Scheduled execution:** add a line to schedrunner's `scripts.conf`. Use
     the helper at the schedrunner repo root:
     `./register.sh interval 5 /Users/joemoser/Dropbox/Source/<name>/<script>.sh`
     (also `daily HH:MM ...` and `startup - ...`). Then commit `scripts.conf`.
   - **Auto-deploy:** add a `.auto-deploy` file to the new repo's root — empty
     for pull-only, or a bash post-pull hook if it needs a build/restart step.
     The repo must live under `~/Dropbox/Source/` for the poller to find it.

4. **Report back** the repo URL and, if registered, what was registered. Tell
   the user they can now open the repo in a new Claude Code session (e.g. on
   iPhone).

## Notes

- The starter `CLAUDE.md` deliberately points at
  `~/Dropbox/Source/schedrunner/CLAUDE.md`, so every repo created this way is
  born knowing how to register — no extra discovery step needed.
- Keep starter files minimal and idiomatic; this is a scaffold, not a framework.
