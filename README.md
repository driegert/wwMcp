# wwMcp

MCP server for authoring [wwApi](../wwApi) content from a pi agent: concept
questions (true/false, multiple choice) and base scenarios. A thin client of
wwApi `/v1`. Design and decisions: [PLAN.md](PLAN.md).

## Setup

```bash
uv sync
mkdir -p ~/.config/wwmcp && cp config.example.json ~/.config/wwmcp/config.json
chmod 600 ~/.config/wwmcp/config.json      # then put real labelled keys in it
```

The server binds to one wwApi for its whole life: `WWMCP_PROFILE` picks the
profile, else `default_profile` in the config. `WWMCP_CONFIG` overrides the
config path. A key with no instructor label refuses to start.

```bash
WWMCP_PROFILE=dev uv run wwmcp             # stdio MCP server
```

Wiring into pi (launcher, confirmation guard, check subagent): [pi/INSTALL.md](pi/INSTALL.md).

## Tests

```bash
uv run pytest                              # logic that lives here; no network
node --experimental-strip-types pi/test-wwapi-guard.ts    # the pi confirmation guard
WWMCP_PROFILE=dev uv run python scripts/smoke.py          # every tool, live, over stdio
WWMCP_PROFILE=dev uv run python scripts/smoke.py --llm    # also the server-draft tools
```

The smoke test writes: wwApi has no delete, so each run leaves two question
drafts and one scenario draft. It refuses to run unless the key's label is
the smoke owner (`mcptest`). Clear that owner from a workspace with:

```bash
uv run python scripts/cleanup_owner.py --db ../wwApi/workspace/workspace.sqlite3 --owner mcptest          # dry run
uv run python scripts/cleanup_owner.py --db ../wwApi/workspace/workspace.sqlite3 --owner mcptest --apply  # backs up first
```
