# <repo name>

**Purpose:** <one line>
**Type:** service (MCP connector)
**Role:** <which chats/agents call these tools>

## Runtime & deploy
- Stack: TypeScript on Cloudflare Workers
- Deploys via: schedrunner (`service.yaml` → wrangler deploy)

## Interface (MCP tools)
- `<tool>` — <what it does, args, side effects>

## Key files
- `src/index.ts` — tool definitions + handlers
- `wrangler.toml` — Worker config

## Invariants / gotchas
- <...>
