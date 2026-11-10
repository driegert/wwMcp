"""wwMcp: MCP tools for authoring wwApi content (concept questions and base
scenarios). A thin client of wwApi /v1; see PLAN.md for the design.

Tiers: FREE tools read, preview, and work on drafts. CONFIRMED tools change
what students are served; the pi guard extension prompts the instructor on
each call, keyed on the tool names in CONFIRMED_TOOLS."""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import logic
from .client import LLM_TIMEOUT, WwApi
from .config import ConfigError, load_profile

CONFIRMED_TOOLS = (
    "approve_question", "unapprove_question",
    "approve_scenario", "unapprove_scenario",
    "edit_live_question", "tag_live", "untag_live",
)

INSTRUCTIONS = """\
Author wwApi content for an instructor: concept questions (true/false,
multiple choice) and base scenarios for randomized statistics questions.

Working rules:
1. Call authoring_guide before writing anything; it carries the house rules.
2. Call list_pools first and reuse an existing tag spelling exactly. A new tag
   needs the instructor's agreement in chat.
3. Review, then save. wwApi has NO delete: a saved draft is permanent. Show
   candidates to the instructor (scenarios as preview_scenario output, every
   framing) and save only what they accept.
4. approve_*, unapprove_*, edit_live_question, tag_live and untag_live change
   what students see. Only call them when the instructor asks.
5. A failed call returns ok=false with wwApi's message and any `issues`; fix
   those and retry rather than rephrasing blindly.
"""

GUIDE_PREFACE = (
    "These are the house rules wwApi's own drafting model is given. Follow "
    "the content rules. Ignore the lines about reply format ('Reply with JSON "
    "only', 'return the COMPLETE updated field set'): you submit through "
    "tools, not by replying with JSON."
)

QUESTION_SHAPE = {
    "true_false": {
        "format": "tf", "text": "<one declarative statement>",
        "is_true": True, "tags": ["<pool tag>"],
    },
    "multiple_choice": {
        "format": "mc", "text": "<stem>",
        "choices": [
            {"text": "<correct choice>", "correct": True},
            {"text": "<distractor>", "correct": False},
        ],
        "tags": ["<pool tag>"],
    },
    "notes": [
        "multiple choice: 3-5 choices, exactly one correct",
        "tags: lowercase, ^[a-z0-9][a-z0-9_-]{0,63}$; a question with no tag "
        "is never served, even once approved",
        "webwork: optional boolean, default true",
    ],
}

mcp = MCPServer("wwapi", instructions=INSTRUCTIONS, log_level="WARNING")
_api: WwApi | None = None


def api() -> WwApi:
    global _api
    if _api is None:
        _api = WwApi(load_profile())
    return _api


def _fail(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _bad_id(*ids: str) -> dict[str, Any] | None:
    """Ids go into URL paths; refuse anything that is not a plain wwApi id."""
    for i in ids:
        if not logic.valid_id(i):
            return _fail(f"not a valid id: {i!r}")
    return None


def _edit_question(
    question_id: str, changes: dict[str, Any], *, expect: str, other_tool: str,
) -> dict[str, Any]:
    """Shared body of update_question_draft / edit_live_question. `expect` is
    the status the row must have. It is checked here for a clear message and
    AGAIN by wwApi (expect_status) inside the write, so an approve that lands
    between the two cannot turn a draft edit into a live one."""
    if (bad := _bad_id(question_id)):
        return bad
    if not isinstance(changes, dict) or not changes:
        return _fail("changes is empty")
    if "format" in changes:
        return _fail("format cannot be changed; create a new question instead")
    if expect == "approved" and "tags" in changes:
        return _fail("tags cannot change here; use tag_live / untag_live")
    row, err = _question_row(question_id)
    if err:
        return err
    gate = logic.draft_gate if expect == "draft" else logic.live_gate
    refusal = gate(row, question_id, other_tool)
    if refusal:
        return _fail(refusal)
    try:
        patch = logic.to_wire_question(changes, fmt=row["format"])
    except ValueError as e:
        return _fail(str(e))
    patch.pop("format", None)
    patch["expect_status"] = expect
    return api().patch(f"/v1/questions/{question_id}", json=patch)


def _question_row(question_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(row, error_response). row is None when the id is not the caller's."""
    r = api().get("/v1/questions")
    if not r["ok"]:
        return None, r
    rows = logic.filter_questions(r.get("questions") or [], question_id=question_id)
    return (rows[0] if rows else None), None


def _scenario_rows(owner: str = "mine") -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    r = api().get("/v1/scenarios")
    if not r["ok"]:
        return [], r
    rows = list(r.get("mine") or [])
    if owner == "all":
        seen = {x.get("scenario_id") for x in rows}
        rows += [x for x in (r.get("approved") or []) if x.get("scenario_id") not in seen]
    return rows, None


# --------------------------------------------------------------------------
# FREE: read and guidance


@mcp.tool()
def whoami() -> dict[str, Any]:
    """Which wwApi this server is bound to (profile, base URL) and the
    instructor label of its key. Check this before any write if unsure
    whether you are on dev or prod."""
    a = api()
    r = a.get("/v1/whoami")
    r["profile"] = a.profile.name
    r["base_url"] = a.profile.base_url
    return r


@mcp.tool()
def authoring_guide(kind: str, format: str = "", sub_method: str = "") -> dict[str, Any]:
    """The rules and field spec for writing one kind of content. Call before
    drafting, and pass the result to any subagent you delegate drafting to.

    kind="question": give format ("tf" or "mc"). Returns the house rules and
      the object shape save_question_drafts accepts.
    kind="scenario": give sub_method for that method's rules and ordered field
      spec (ids, kinds, limits, required placeholders). Omit sub_method to
      list the methods you can author."""
    a = api()
    if kind == "question":
        fmt = logic.canonical_format(format)
        if not fmt:
            return _fail("format must be tf (true_false) or mc (multiple_choice)")
        r = a.get("/v1/draft/questions/prompt", format=fmt)
        if not r["ok"]:
            return r
        return {
            "ok": True, "kind": "question", "format": fmt,
            "how_to_read": GUIDE_PREFACE,
            "house_rules": r.get("system_prompt"),
            "shape": QUESTION_SHAPE[fmt],
            "shape_notes": QUESTION_SHAPE["notes"],
        }
    if kind == "scenario":
        if not sub_method:
            return a.get("/v1/scenario-fields")
        spec = a.get("/v1/scenario-fields", sub_method=sub_method)
        if not spec["ok"]:
            return spec
        out: dict[str, Any] = {
            "ok": True, "kind": "scenario", "sub_method": sub_method,
            "how_to_read": GUIDE_PREFACE,
        }
        rules = a.get("/v1/draft/chat/prompt", sub_method=sub_method)
        if rules["ok"]:
            out["house_rules"] = rules.get("system_prompt")
        else:
            out["house_rules"] = None
            out["house_rules_unavailable"] = (
                "this wwApi does not serve the scenario rules yet "
                f"({rules.get('http_status')}). Work from the field spec, check every "
                "draft with preview_scenario, and tell the instructor the rules "
                "were not available."
            )
        out["spec"] = {k: v for k, v in spec.items() if k not in ("ok", "http_status")}
        return out
    return _fail('kind must be "question" or "scenario"')


@mcp.tool()
def list_pools(kind: str = "both", tag: str = "", user: str = "") -> dict[str, Any]:
    """Existing tags and pool sizes. A pool is (user, tag); its exact tag
    string is what serves questions, so reuse spellings from here.

    kind: "questions", "scenarios" or "both". tag/user filter (default: every
    instructor's pools)."""
    r = api().get("/v1/tags", kind=kind, tag=tag, user=user)
    if not r["ok"]:
        return r
    out: dict[str, Any] = {"ok": True}
    if "pools" in r:
        out["scenario_pools"] = r["pools"]
    if "questions" in r:
        counts: dict[tuple[str, str], int] = {}
        for row in r["questions"] or []:
            key = (row.get("user_id"), row.get("tag"))
            counts[key] = counts.get(key, 0) + 1
        out["question_pools"] = [
            {"user_id": u, "tag": t, "n": n} for (u, t), n in sorted(counts.items())
        ]
    return out


@mcp.tool()
def list_questions(
    tag: str = "", format: str = "", status: str = "",
    question_id: str = "", compact: bool = True,
) -> dict[str, Any]:
    """Your own concept questions (the key's instructor), filtered.

    tag / format ("tf"|"mc") / status ("draft"|"approved"|"retired") narrow
    the list; question_id fetches one. compact=true (default) returns stem,
    correct answer, tags and status only; compact=false returns full objects
    with every choice (use it before editing one)."""
    if format and not logic.canonical_format(format):
        return _fail("format must be tf (true_false) or mc (multiple_choice)")
    if status and status not in logic.STATUSES:
        return _fail(f"status must be one of: {', '.join(logic.STATUSES)}")
    r = api().get("/v1/questions")
    if not r["ok"]:
        return r
    rows = logic.filter_questions(
        r.get("questions") or [], tag=tag, fmt=format, status=status,
        question_id=question_id,
    )
    if compact:
        rows = [logic.compact_question(x) for x in rows]
    return {"ok": True, "n": len(rows), "questions": rows}


@mcp.tool()
def list_scenarios(
    sub_method: str = "", status: str = "", scenario_id: str = "",
    owner: str = "mine", include_fields: bool = False,
) -> dict[str, Any]:
    """User-authored scenarios. owner="mine" (default; every status) or "all"
    (adds other instructors' approved ones). Filter by sub_method, status, or
    one scenario_id. include_fields=true adds the authored fields (use it
    before editing one)."""
    rows, err = _scenario_rows(owner)
    if err:
        return err
    rows = logic.filter_scenarios(
        rows, sub_method=sub_method, status=status, scenario_id=scenario_id
    )
    rows = [logic.scenario_view(x, include_fields) for x in rows]
    return {"ok": True, "n": len(rows), "scenarios": rows}


@mcp.tool()
def scenario_status(scenario_id: str) -> dict[str, Any]:
    """Where one of your scenarios is: draft, generating, approved, failed or
    retired. Poll this after approve_scenario (generation takes a while)."""
    if (bad := _bad_id(scenario_id)):
        return bad
    return api().get("/v1/scenarios/status", scenario_id=scenario_id)


# --------------------------------------------------------------------------
# FREE: draft work


@mcp.tool()
def preview_scenario(
    sub_method: str, fields: dict[str, Any],
    preview_seed: int = 1, full: bool = False,
) -> dict[str, Any]:
    """Validate scenario fields and render them as a student would see them.
    Saves nothing. Returns the rendered text for one draw plus `variants`:
    all six focus x direction framings. A student sees exactly ONE framing, so
    each must read correctly on its own. Show these to the instructor before
    saving. Invalid fields return ok=false with `issues`.

    preview_seed changes the drawn numbers. full=true returns wwApi's whole
    payload (data, solution) instead of the trimmed view."""
    r = api().post(
        "/v1/draft/preview",
        json={"sub_method": sub_method, "fields": fields,
              "preview_seed": preview_seed, "variants": True},
        timeout=LLM_TIMEOUT,
    )
    if not r["ok"] or full:
        return r
    return logic.trim_preview(r)


@mcp.tool()
def save_question_drafts(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Save accepted concept questions as drafts. All or nothing: if any
    question is invalid nothing is saved and `issues` names each failing
    index (0-based) so you can fix and resend. Only save what the instructor
    accepted: there is no delete.

    Each question, short form:
      {"format": "tf", "text": "...", "is_true": true, "tags": ["tag"]}
      {"format": "mc", "text": "...", "tags": ["tag"],
       "choices": [{"text": "...", "correct": true}, {"text": "...", "correct": false}, ...]}
    A candidate object from server_draft_questions is also accepted as it
    came back; add "tags": ["<tag>"] to each one first. Returns the saved
    questions with their new ids."""
    if not questions:
        return _fail("questions is empty")
    wire, issues = [], []
    for i, q in enumerate(questions):
        try:
            wire.append(logic.to_wire_question(q))
        except ValueError as e:
            issues.append({"index": i, "problems": [str(e)]})
    if issues:
        return _fail("one or more questions could not be read; nothing was saved", issues=issues)
    return api().post("/v1/questions", json={"questions": wire})


@mcp.tool()
def save_scenario_draft(sub_method: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Save a scenario as a new draft and return its scenario_id. Run
    preview_scenario first and save only after the instructor accepts the
    preview: there is no delete. Invalid fields return `issues`."""
    return api().post("/v1/scenarios", json={"sub_method": sub_method, "fields": fields})


@mcp.tool()
def update_question_draft(question_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Edit one of your DRAFT concept questions. `changes` holds only the
    parts to change: text, tags (replaces the set), webwork, and/or the answer
    (is_true for true/false, choices for multiple choice). Format cannot
    change. Refuses approved questions; edit_live_question is for those."""
    return _edit_question(
        question_id, changes, expect="draft", other_tool="edit_live_question"
    )


@mcp.tool()
def update_scenario_draft(scenario_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Replace the fields of one of your draft (or failed) scenarios. Send the
    COMPLETE field set, not a diff: read it with list_scenarios
    (include_fields=true), change it, check with preview_scenario, then send.
    An approved scenario cannot be edited; the route is unapprove_scenario,
    edit, approve_scenario (its seed block is unchanged by that cycle)."""
    if (bad := _bad_id(scenario_id)):
        return bad
    return api().patch("/v1/scenarios", json={"scenario_id": scenario_id, "fields": fields})


@mcp.tool()
def server_draft_questions(
    format: str, source_path: str, n: int = 10, guidance: str = "",
    revise_question: dict[str, Any] | None = None, instruction: str = "",
) -> dict[str, Any]:
    """Have wwApi's own drafting model write concept-question candidates from
    a source file (lecture notes, a .qmd). This tool reads the file itself, so
    the text never passes through your context. Nothing is saved; review the
    candidates with the instructor, then save_question_drafts.

    format "tf"|"mc"; source_path absolute, UTF-8, at most 40000 characters;
    n 1-20; guidance = optional steer ("focus on p-value interpretation").
    Revise mode: pass revise_question (one candidate) and instruction (at most
    240 characters) to get that one candidate rewritten."""
    fmt = logic.canonical_format(format)
    if not fmt:
        return _fail("format must be tf (true_false) or mc (multiple_choice)")
    text, err = logic.read_source(source_path)
    if err:
        return _fail(err)
    body: dict[str, Any] = {"format": fmt, "source_text": text}
    if revise_question is not None:
        if not revise_question:
            return _fail("revise_question is empty")
        if not instruction.strip():
            return _fail("instruction is required when revise_question is given")
        if len(instruction) > 240:
            return _fail("instruction must be at most 240 characters")
        try:
            body["question"] = logic.to_wire_question(revise_question, fmt=fmt)
        except ValueError as e:
            return _fail(str(e))
        body["instruction"] = instruction
    else:
        if not 1 <= n <= 20:
            return _fail("n must be between 1 and 20")
        body["n"] = n
        if guidance.strip():
            body["guidance"] = guidance
    return api().post("/v1/draft/questions", json=body, timeout=LLM_TIMEOUT)


@mcp.tool()
def server_draft_scenario(
    sub_method: str, message: str, fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Have wwApi's own drafting model write or revise scenario fields.
    message = what you want ("a scenario about maple syrup sugar content").
    Pass the current `fields` to revise them. Returns a complete field set;
    nothing is saved. Check it with preview_scenario."""
    body: dict[str, Any] = {"sub_method": sub_method, "message": message}
    if fields:
        body["fields"] = fields
    return api().post("/v1/draft/chat", json=body, timeout=LLM_TIMEOUT)


# --------------------------------------------------------------------------
# CONFIRMED: these change what students are served


@mcp.tool()
def approve_question(question_id: str) -> dict[str, Any]:
    """CONFIRMED. Approve a draft concept question: it joins the pool of each
    of its tags and can be drawn by students at once. With no tags it approves
    but is never served. Undo with unapprove_question."""
    if (bad := _bad_id(question_id)):
        return bad
    return api().post(f"/v1/questions/{question_id}/approve")


@mcp.tool()
def unapprove_question(question_id: str) -> dict[str, Any]:
    """CONFIRMED. Return an approved concept question to draft. It leaves its
    pools at once; its tags come back as draft tags and reapply on approve."""
    if (bad := _bad_id(question_id)):
        return bad
    return api().post(f"/v1/questions/{question_id}/unapprove")


@mcp.tool()
def approve_scenario(scenario_id: str) -> dict[str, Any]:
    """CONFIRMED. Approve a draft scenario. wwApi generates its seed bank in
    the background (status "generating"); poll scenario_status until
    "approved" or "failed". Once approved it serves by scenario_id and can be
    tagged into pools with tag_live. Undo with unapprove_scenario."""
    if (bad := _bad_id(scenario_id)):
        return bad
    return api().post("/v1/scenarios/approve", json={"scenario_id": scenario_id})


@mcp.tool()
def unapprove_scenario(scenario_id: str) -> dict[str, Any]:
    """CONFIRMED. Return an approved scenario to draft. It stops serving at
    once; a later approve regenerates the same seed block."""
    if (bad := _bad_id(scenario_id)):
        return bad
    return api().post(f"/v1/scenarios/{scenario_id}/unapprove")


@mcp.tool()
def edit_live_question(question_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """CONFIRMED. Edit an APPROVED concept question in place (typo fixes).
    Students who have not yet been served it see the new version. `changes`
    may hold text, webwork, and/or the answer (is_true or choices). Not
    format, and not tags: use tag_live / untag_live for those."""
    return _edit_question(
        question_id, changes, expect="approved", other_tool="update_question_draft"
    )


def _tag_params(
    tag: str, question_id: str, scenario_id: str, sub_method: str, method_type: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if bool(question_id) == bool(scenario_id):
        return None, _fail("give exactly one of question_id or scenario_id")
    if (bad := _bad_id(question_id or scenario_id)):
        return None, bad
    if question_id:
        row, err = _question_row(question_id)
        if err:
            return None, err
        refusal = logic.live_gate(row, question_id, "update_question_draft (set tags)")
        if refusal:
            return None, _fail(refusal)
        return {"tag": tag, "question_id": question_id}, None
    if not (sub_method and method_type):
        rows, err = _scenario_rows("all")
        if err:
            return None, err
        hit = logic.filter_scenarios(rows, scenario_id=scenario_id)
        if hit:
            sub_method = sub_method or hit[0].get("sub_method", "")
            method_type = method_type or hit[0].get("method_type", "")
    if not (sub_method and method_type):
        return None, _fail(
            "sub_method and method_type are required for a scenario that is "
            "not a user scenario (e.g. a bank scenario)"
        )
    return {
        "tag": tag, "method_type": method_type,
        "sub_method": sub_method, "scenario_id": scenario_id,
    }, None


@mcp.tool()
def tag_live(
    tag: str, question_id: str = "", scenario_id: str = "",
    sub_method: str = "", method_type: str = "",
) -> dict[str, Any]:
    """CONFIRMED. Add a served item to one of your pools: an APPROVED concept
    question (question_id) or a servable scenario (scenario_id; a bank
    scenario also needs sub_method and method_type). This changes the pool's
    membership, which changes what un-served students draw. Draft questions
    carry their tags via update_question_draft instead."""
    params, err = _tag_params(tag, question_id, scenario_id, sub_method, method_type)
    if err:
        return err
    return api().request("POST", "/v1/tags", params=params)


@mcp.tool()
def untag_live(
    tag: str, question_id: str = "", scenario_id: str = "",
    sub_method: str = "", method_type: str = "",
) -> dict[str, Any]:
    """CONFIRMED. Remove a served item from one of your pools (your own tag
    rows only). Same arguments as tag_live."""
    params, err = _tag_params(tag, question_id, scenario_id, sub_method, method_type)
    if err:
        return err
    return api().request("DELETE", "/v1/tags", params=params)


def main() -> None:
    try:
        a = api()
    except ConfigError as e:
        sys.exit(f"wwmcp: {e}")
    # Bound at launch: an unlabelled key cannot author, so refuse to start.
    # An unreachable server is transient; start anyway and let tools report it.
    who = a.get("/v1/whoami")
    if who["ok"] and not who.get("user"):
        sys.exit(
            f"wwmcp: the key for profile '{a.profile.name}' has no instructor "
            "label; authoring needs a labelled key"
        )
    if who.get("http_status") in (401, 403):
        sys.exit(f"wwmcp: {a.profile.base_url} rejected the key: {who.get('error')}")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
