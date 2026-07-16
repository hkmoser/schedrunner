# <repo name>

**Purpose:** <one line>
**Type:** service (data collector)
**Role:** <what consumes this data>

## Runtime & run
- Stack: Node 20 (ESM)
- Runs via: schedrunner cron (`service.yaml`)

## Interface
- Source: <URL / API>
- Output: markdown in `data/<date>.md`
- Env: SOURCE_URL

## Key files
- `src/collect.js` — <what it fetches / writes>
- `service.yaml` — schedule + run command

## Deploy & schedule
- Schedule: <cron>
- Runner: schedrunner (Mac mini)

## Invariants / gotchas
- <...>
