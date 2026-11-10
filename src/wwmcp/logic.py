"""The logic that lives in wwMcp itself (everything else is a pass-through to
wwApi). Pure functions, unit-tested in tests/test_logic.py."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SOURCE_TEXT_MAX = 40_000  # wwApi returns 413 above this; never truncate silently

FORMAT_ALIASES = {
    "tf": "true_false",
    "true_false": "true_false",
    "mc": "multiple_choice",
    "multiple_choice": "multiple_choice",
}


STATUSES = ("draft", "approved", "retired")
WIRE_KEYS = ("format", "text", "answer", "tags", "webwork")

# wwApi ids: q_<owner>_<NNNN>, <prefix>_<owner>_<NN>, bank ids like sm_01.
# Checked before an id is put in a URL path.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def valid_id(item_id: str) -> bool:
    return isinstance(item_id, str) and bool(ID_RE.match(item_id))


def canonical_format(fmt: str) -> str | None:
    return FORMAT_ALIASES.get((fmt or "").strip().lower())


def to_wire_question(q: dict[str, Any], fmt: str | None = None) -> dict[str, Any]:
    """Accept the short authoring form and return wwApi's wire object.

    Short forms:
      true/false : {"format": "tf", "text": ..., "is_true": true, "tags": [...]}
      multiple   : {"format": "mc", "text": ..., "choices": [{"text", "correct"}], "tags": [...]}
    A full wire object (with its own `answer`) passes through untouched, so
    server-drafted candidates can be saved as they came back.

    `fmt` pins the format (edits: the stored row's format wins over anything
    in `q`). Content validation is wwApi's job, but a short-form answer that
    cannot be converted raises ValueError: silently dropping it would turn an
    intended answer change into a text-only edit."""
    if not isinstance(q, dict):
        raise ValueError("each question must be an object")
    q = dict(q)
    fmt = fmt or canonical_format(str(q.get("format", ""))) or q.get("format")
    if fmt is not None:
        q["format"] = fmt
    has_short = [k for k in ("is_true", "choices") if k in q]
    if "answer" in q:
        if has_short:
            raise ValueError(f"give either `answer` or {'/'.join(has_short)}, not both")
    elif has_short:
        if fmt == "true_false":
            if "choices" in q:
                raise ValueError("a true/false question takes `is_true`, not `choices`")
            if not isinstance(q["is_true"], bool):
                raise ValueError("`is_true` must be the JSON boolean true or false, not a string")
            truth = q["is_true"]
            q["answer"] = {
                "format": "true_false",
                "randomize_choices": False,
                "choices": [
                    {"text": "True", "correct": truth},
                    {"text": "False", "correct": not truth},
                ],
            }
        elif fmt == "multiple_choice":
            if "is_true" in q:
                raise ValueError("a multiple-choice question takes `choices`, not `is_true`")
            if not isinstance(q["choices"], list):
                raise ValueError("`choices` must be a list of {text, correct} objects")
            q["answer"] = {
                "format": "multiple_choice",
                "randomize_choices": bool(q.get("randomize_choices", True)),
                "choices": q["choices"],
            }
        else:
            raise ValueError("format must be tf (true_false) or mc (multiple_choice)")
    # Allowlist: only wwApi's question fields ever leave here. A caller cannot
    # smuggle status, owner or expect_status through `changes` (wwApi drops
    # unknown keys too; this does not rely on it).
    return {k: q[k] for k in WIRE_KEYS if k in q}


def correct_answer(row: dict[str, Any]) -> str | None:
    choices = (row.get("answer") or {}).get("choices") or []
    for c in choices:
        if isinstance(c, dict) and c.get("correct"):
            return c.get("text")
    return None


def compact_question(row: dict[str, Any]) -> dict[str, Any]:
    """Stem + correct answer only: enough to judge overlap, cheap on context."""
    return {
        "question_id": row.get("question_id"),
        "format": row.get("format"),
        "status": row.get("status"),
        "tags": row.get("tags") or [],
        "text": row.get("text"),
        "correct": correct_answer(row),
    }


def filter_questions(
    rows: list[dict[str, Any]],
    *,
    tag: str = "",
    fmt: str = "",
    status: str = "",
    question_id: str = "",
) -> list[dict[str, Any]]:
    want_fmt = canonical_format(fmt) if fmt else None
    out = []
    for r in rows:
        if question_id and r.get("question_id") != question_id:
            continue
        if tag and tag not in (r.get("tags") or []):
            continue
        if want_fmt and r.get("format") != want_fmt:
            continue
        if status and r.get("status") != status:
            continue
        out.append(r)
    return out


def filter_scenarios(
    rows: list[dict[str, Any]],
    *,
    sub_method: str = "",
    status: str = "",
    scenario_id: str = "",
) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if scenario_id and r.get("scenario_id") != scenario_id:
            continue
        if sub_method and r.get("sub_method") != sub_method:
            continue
        if status and r.get("status") != status:
            continue
        out.append(r)
    return out


def scenario_view(row: dict[str, Any], include_fields: bool) -> dict[str, Any]:
    """wwApi ships a scenario's authored fields as a JSON string (`fields_json`).
    Return the row with a parsed `fields` object, or with no fields at all."""
    out = {k: v for k, v in row.items() if k not in ("fields_json", "fields")}
    if include_fields:
        raw = row.get("fields", row.get("fields_json"))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        out["fields"] = raw
    return out


def draft_gate(row: dict[str, Any] | None, item_id: str, live_tool: str) -> str | None:
    """Refusal message for a drafts-only tool, or None when the row is a draft."""
    if row is None:
        return f"no such item of yours: {item_id}"
    status = row.get("status")
    if status == "draft":
        return None
    if status == "approved":
        return (
            f"{item_id} is approved (live). This tool only edits drafts; "
            f"use {live_tool}, which asks the instructor to confirm."
        )
    return f"{item_id} is {status}; only drafts can be edited here."


def live_gate(row: dict[str, Any] | None, item_id: str, draft_tool: str) -> str | None:
    """Refusal message for a live-only tool, or None when the row is approved."""
    if row is None:
        return f"no such item of yours: {item_id}"
    status = row.get("status")
    if status == "approved":
        return None
    if status == "draft":
        return f"{item_id} is a draft; use {draft_tool} (no confirmation needed)."
    return f"{item_id} is {status}; only approved items can be changed here."


def read_source(path: str) -> tuple[str | None, str | None]:
    """Read a source file for server-side drafting -> (text, error)."""
    p = Path(path).expanduser()
    if not p.is_file():
        return None, f"not a file: {p}"
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, f"{p} is not UTF-8 text"
    except OSError as e:
        return None, f"could not read {p}: {e}"
    if not text.strip():
        return None, f"{p} is empty"
    if len(text) > SOURCE_TEXT_MAX:
        return None, (
            f"{p} is {len(text)} characters; the limit is {SOURCE_TEXT_MAX}. "
            "Split it or save the relevant section to its own file "
            "(no silent truncation)."
        )
    return text, None


_PREVIEW_STATS = (
    "null_value", "direction", "comparison_phrase", "alpha",
    "sample_size_n", "sample_mean", "sample_sd",
)


def trim_preview(resp: dict[str, Any]) -> dict[str, Any]:
    """Keep what a reviewer reads: the rendered text in every framing, the
    part prompts, and a few of the drawn numbers. Drops data and solution."""
    payload = resp.get("payload") or {}
    subs = payload.get("substitutions") or {}
    out: dict[str, Any] = {
        "ok": True,
        "preview_seed": resp.get("preview_seed"),
        "title": (payload.get("template") or {}).get("title"),
        "question_text": payload.get("question_text"),
        "parts": [
            {"id": p.get("id"), "text": p.get("text")}
            for p in payload.get("questions") or []
        ],
        "drawn": {k: subs[k] for k in subs if k in _PREVIEW_STATS or k.startswith(
            ("n1", "n2", "mean", "sd", "sample_", "p_hat", "x1", "x2"))},
    }
    if resp.get("variants") is not None:
        out["variants"] = [
            {
                "focus": v.get("focus"),
                "direction": v.get("direction"),
                "question_text": v.get("question_text"),
            }
            for v in resp["variants"]
        ]
    return out
