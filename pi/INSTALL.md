# Wiring wwMcp into pi

Replace `/path/to/wwMcp` below with where this repository is checked out.

Four changes to `~/.pi/agent/`, none applied automatically. Ordinary pi
sessions are unaffected by all of them: `wwapi` is never added to
`AGENT_SERVERS.main`.

## 1. Catalog entry: `mcp-servers.json`

Add under `mcpServers`:

```json
"wwapi": {
  "command": "uv",
  "args": ["run", "--project", "/path/to/wwMcp", "wwmcp"],
  "env": { "WWMCP_PROFILE": "${WWMCP_PROFILE}" },
  "exposure": "deferred",
  "description": "wwApi authoring: draft, preview, save and approve concept questions and scenarios"
}
```

`${WWMCP_PROFILE}` is passed through from the pi process (the launcher sets
it). If it is unset the entry is dropped by `mcp-scope.ts` and wwMcp falls
back to `default_profile` in `~/.config/wwmcp/config.json`.

## 2. Scope: `extensions/mcp-scope.ts`

In `AGENT_SERVERS`, add the check subagent (and nothing to `main`):

```ts
	pool_checker: ["wwapi"],
```

In `PROFILE_TOOLS`, add the authoring profile:

```ts
	wwapi: {
		wwapi: [
			"whoami", "authoring_guide", "list_pools", "list_questions", "list_scenarios",
			"scenario_status", "preview_scenario", "save_question_drafts", "save_scenario_draft",
			"update_question_draft", "update_scenario_draft",
			"server_draft_questions", "server_draft_scenario",
			"approve_question", "unapprove_question", "approve_scenario", "unapprove_scenario",
			"edit_live_question", "tag_live", "untag_live",
		],
	},
```

## 3. Guard and subagent

```bash
ln -s /path/to/wwMcp/pi/wwapi-guard.ts ~/.pi/agent/extensions/wwapi-guard.ts
cp /path/to/wwMcp/pi/pool_checker.md ~/.pi/agent/agents/pool_checker.md
```

Then set `model:` in the copied `pool_checker.md` to one of your own models
(a small fast one with thinking off is enough; add `model_fallbacks:` if you
use them).

## 4. Launcher

```bash
ln -s /path/to/wwMcp/pi/pi-wwapi ~/.local/bin/pi-wwapi
pi-wwapi            # dev
pi-wwapi prod       # needs a "prod" profile in ~/.config/wwmcp/config.json
```

## Check after wiring (not yet verified)

- Confirm that an interactive `PI_AGENT_PROFILE` session still has the `subagent` tool
  and that a tier 2 tool raises the confirm prompt.
- A profile registers only its own servers: the session has `wwapi` and pi's
  built-in file tools, nothing else from the catalog.
- `pool_checker` relies on the subagent process inheriting `WWMCP_PROFILE`
  from its parent so that it reads the same wwApi.
- In a normal `pi` session, `mcp__wwapi__*` must not appear.
