# wwMcp plan

Decisions from the 2026-11-10 design session (Dave + Claude). wwMcp is an MCP
server that lets a pi agent author wwApi content: concept questions and base
scenarios. It is a pure client of wwApi `/v1`; it reuses no wwUtils code.

## Shape

- **Own project**, sibling of `wwApi` and `wwUtils`. Python, `uv`, FastMCP,
  stdio.
- **Orchestrator model.** The pi main agent always holds the plan and the
  context. Who writes a batch is its choice, per batch:
  - itself (writes fields, calls the save tools);
  - the server's drafting LLM (the `server_draft_*` tools);
  - a pi subagent on a local model (delegation stays on the pi side; the MCP
    server knows nothing about it).
  Every path goes through wwApi's validators.
- **Bound at launch.** One process talks to one wwApi. Profiles (base URL +
  labelled key) live in `~/.config/wwmcp/config.json`, outside git;
  `WWMCP_PROFILE` picks one (else the file's `default_profile`). An unlabelled
  key refuses to start. Prod is the eventual default; dev while building.
- **Not visible to ordinary pi sessions.** `wwapi` goes in pi's
  `mcp-servers.json` but not in `AGENT_SERVERS.main`; a `wwapi` profile in
  `mcp-scope.ts` plus a `pi-wwapi [dev|prod]` launcher is the explicit start.
  One server name for both targets: the launcher exports `WWMCP_PROFILE`, the
  catalog entry passes it through, and the check subagent inherits it. The
  target is shown by `whoami` and in every tier 2 confirm prompt (not in the
  tool name, as first sketched: two names would need two subagent files).

## Writing rules

The house rules live in wwApi's drafting prompts and are pulled from the
server, never copied here.

- Concept questions: `GET /v1/draft/questions/prompt?format=` (exists).
- Scenarios: `GET /v1/draft/chat/prompt?sub_method=` (added to wwApi for this
  project, mirroring the questions one). Against an older wwApi the guide
  says the rules were unavailable.
- `authoring_guide` returns field spec + prompt verbatim under a short preface
  (the prompt's reply-format lines do not apply to a tool-using agent).

## Tiers

1. **Free:** read, guide, preview, save and edit drafts, server-draft.
2. **Confirmed** (pi extension prompts; fails closed with no UI): approve,
   unapprove, live question edits, live tagging.
3. **Not exposed:** retire (terminal).

## Tools (phase 1)

| Tier | Tool | wwApi |
|---|---|---|
| free | `whoami` | `GET /v1/whoami` + profile, base URL |
| free | `authoring_guide` | scenario-fields spec + drafting prompt |
| free | `list_pools` | `GET /v1/tags` |
| free | `list_questions` | `GET /v1/questions` (tag/format/status filters, compact mode) |
| free | `list_scenarios` | `GET /v1/scenarios` (method/status filters) |
| free | `scenario_status` | `GET /v1/scenarios/status` |
| free | `preview_scenario` | `POST /v1/draft/preview` (variants on) |
| free | `save_question_drafts` | `POST /v1/questions` (batch, all-or-nothing) |
| free | `save_scenario_draft` | `POST /v1/scenarios` |
| free | `update_question_draft` | `PATCH /v1/questions/<id>`, drafts only |
| free | `update_scenario_draft` | `PATCH /v1/scenarios` (server: draft/failed only) |
| free | `server_draft_questions` | `POST /v1/draft/questions`; reads the source file itself |
| free | `server_draft_scenario` | `POST /v1/draft/chat` |
| confirmed | `approve_question`, `unapprove_question` | question approve routes |
| confirmed | `approve_scenario`, `unapprove_scenario` | scenario approve routes |
| confirmed | `edit_live_question` | same PATCH, approved rows only (no format/tags) |
| confirmed | `tag_live`, `untag_live` | `POST` / `DELETE /v1/tags` |

Tier 2 tools have their own names so the pi guard matches on tool name alone.
The draft/live split on questions is enforced by wwApi, not only here: both
edit tools send `expect_status` with the PATCH and wwApi refuses (409) if the
row is in another status, so an approve landing mid-edit cannot turn a draft
edit into a live one.
There is no `edit_live_scenario`: wwApi only edits draft/failed scenarios, so a
scenario fix is unapprove -> edit -> approve (seed block is stable across that).

## Working rules for the orchestrator

- **Review, then save.** wwApi has no delete verb, so a saved reject is
  permanent clutter. Candidates stay in chat (or a local `.md` for big
  batches) until Dave accepts them. Scenarios are shown as rendered previews,
  both framings.
- **Read before drafting.** `list_pools` first; reuse an existing tag spelling.
  A new tag needs Dave's confirmation in chat. Before drafting into an existing
  tag, check the pool for repeats.
- **Pool check subagent** (one-shot, `gemma4-12b:off`, read-only allowlist of
  the two list tools): given candidates + tag, returns per candidate new /
  overlaps `<id>` with the stem quoted / related. It reports, never drops.
  T/F negations count as overlap.

## Testing

- Unit tests only for logic that lives here (config, filters, draft-vs-live
  refusals, source-file size limit). No per-endpoint mocks: they cannot see
  drift.
- `scripts/smoke.py` calls every tool against dev with the `mcptest` key,
  tagging writes `test`. This is the drift catcher.
- Dave's live pi sessions against dev.
- `pi/test-wwapi-guard.ts` drives the guard with a fake pi API (no model).
- Cleanup: `scripts/cleanup_owner.py` removes one owner's rows from a
  workspace SQLite. Dave runs it; nothing else deletes.

## Later phases

- Practice-question scaffolds (draft via server, save onto the scenario,
  review gate, per-kind preview).
- RPC librarian: a long-lived `pi --mode rpc` pool reader behind an
  `ask_librarian` tool, once pools or sessions outgrow the one-shot check.
- In-session activation (hidden server + a menu toggle) if `mcp-menu` returns.
- Hermetic wwApi for the smoke test (own db copy, own port).
- Digest mode for the pool subagent (coverage map before drafting).
