"""Smoke test: call every wwMcp tool over real stdio against a live wwApi.

This is the drift catcher: when a wwApi endpoint changes shape, this fails.
It WRITES (wwApi has no delete): each run leaves 2 question drafts and 1
scenario draft under the smoke owner, tagged `test`. It therefore refuses to
run unless the bound key's label is the smoke owner (default `mcptest`).

    uv run python scripts/smoke.py            # skips the LLM-backed tools
    uv run python scripts/smoke.py --llm      # also calls server_draft_* (costs tokens)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
TAG = "test"
EXPECTED_TOOLS = {
    "whoami", "authoring_guide", "list_pools", "list_questions", "list_scenarios",
    "scenario_status", "preview_scenario", "save_question_drafts",
    "save_scenario_draft", "update_question_draft", "update_scenario_draft",
    "server_draft_questions", "server_draft_scenario",
    "approve_question", "unapprove_question", "approve_scenario",
    "unapprove_scenario", "edit_live_question", "tag_live", "untag_live",
}

SM_FIELDS = {
    "title": "Smoke Test Profiles", "units": "loci", "tags": ["test"],
    "context": "academic", "design": "experiment",
    "body_null": (
        "A forensic scientist claims that the average number of matches in a "
        "DNA profile is {{{comparison_phrase}}} {{{null_value}}} loci. You "
        "decide to check her claim."
    ),
    "body_alt": (
        "A forensic scientist claims that the average number of matches in a "
        "DNA profile is {{{null_value}}} loci. You suspect the true average "
        "is actually {{{comparison_phrase}}} {{{null_value}}} loci."
    ),
    "flavor_null": "", "flavor_alt": "",
    "null_range": [13, 20], "n_range": [20, 60], "sd_range": [1, 3],
    "null_dp": 0, "obs_dp": 0,
}

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((bool(ok), name, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))
    return bool(ok)


async def run(llm: bool, owner: str) -> int:
    params = StdioServerParameters(
        command="uv", args=["run", "--project", str(ROOT), "wwmcp"], env=dict(os.environ)
    )
    async with Client(params) as c:

        async def call(tool: str, **args):
            r = await c.call_tool(tool, args)
            sc = getattr(r, "structured_content", None)
            if sc is None:
                raise RuntimeError(f"{tool}: no structured content: {r}")
            return sc.get("result", sc) if set(sc) == {"result"} else sc

        tools = {t.name for t in (await c.list_tools()).tools}
        check(tools == EXPECTED_TOOLS, "tool list",
              f"missing {EXPECTED_TOOLS - tools}, extra {tools - EXPECTED_TOOLS}")

        who = await call("whoami")
        print(f"     bound to {who.get('base_url')} as {who.get('user')} (profile {who.get('profile')})")
        if not check(who.get("ok") and who.get("user") == owner, "whoami is the smoke owner",
                     f"got {who.get('user')!r}, want {owner!r}; refusing to write"):
            return 1

        # --- guidance
        for fmt in ("tf", "mc"):
            g = await call("authoring_guide", kind="question", format=fmt)
            check(g.get("ok") and g.get("house_rules") and g.get("shape"), f"authoring_guide question {fmt}", str(g)[:200])
        g = await call("authoring_guide", kind="scenario")
        methods = [m["sub_method"] for m in g.get("sub_methods", [])]
        check("single_mean" in methods, "authoring_guide scenario method list", str(g)[:200])
        g = await call("authoring_guide", kind="scenario", sub_method="single_mean")
        check(g.get("ok") and g.get("spec", {}).get("fields"), "authoring_guide scenario spec", str(g)[:200])
        if not g.get("house_rules"):
            print("     NOTE scenario house rules not served by this wwApi yet (GET /v1/draft/chat/prompt)")
        else:
            check("comparison_phrase" in g["house_rules"], "authoring_guide scenario house rules")

        p = await call("list_pools")
        check(p.get("ok") and "question_pools" in p and "scenario_pools" in p, "list_pools", str(p)[:200])

        # --- concept questions
        before = (await call("list_questions"))["n"]
        bad = await call("save_question_drafts", questions=[
            {"format": "tf", "text": "The sample mean is a statistic.", "is_true": True, "tags": [TAG]},
            {"format": "mc", "text": "Too few choices?", "tags": [TAG],
             "choices": [{"text": "only one", "correct": True}]},
        ])
        issues = bad.get("issues") or []
        check(not bad.get("ok") and bad.get("http_status") == 422 and issues and issues[0].get("index") == 1,
              "save_question_drafts rejects a bad batch with per-index issues", str(bad)[:300])
        check((await call("list_questions"))["n"] == before, "a rejected batch saves nothing")

        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        saved = await call("save_question_drafts", questions=[
            {"format": "tf", "text": f"[smoke {stamp}] The sample mean is a statistic.",
             "is_true": True, "tags": [TAG]},
            {"format": "mc", "text": f"[smoke {stamp}] Which of these is a parameter?", "tags": [TAG],
             "choices": [{"text": "The population mean", "correct": True},
                         {"text": "The sample mean", "correct": False},
                         {"text": "The sample size", "correct": False}]},
        ])
        qs = saved.get("questions") or []
        if not check(saved.get("ok") and len(qs) == 2 and all(q["status"] == "draft" for q in qs),
                     "save_question_drafts", str(saved)[:300]):
            return 1
        tf_id, mc_id = qs[0]["question_id"], qs[1]["question_id"]

        lst = await call("list_questions", tag=TAG, status="draft")
        ids = {q["question_id"] for q in lst["questions"]}
        row = next((q for q in lst["questions"] if q["question_id"] == tf_id), {})
        check({tf_id, mc_id} <= ids and row.get("correct") == "True", "list_questions (tag, status, compact)", str(row))
        one = await call("list_questions", question_id=mc_id, compact=False)
        check(one["n"] == 1 and len(one["questions"][0]["answer"]["choices"]) == 3, "list_questions full by id")

        u = await call("update_question_draft", question_id=tf_id,
                       changes={"text": f"[smoke {stamp}] The sample mean is a parameter.", "is_true": False})
        check(u.get("ok") and u.get("text", "").endswith("parameter."), "update_question_draft", str(u)[:300])
        row = (await call("list_questions", question_id=tf_id))["questions"][0]
        check(row["correct"] == "False", "update_question_draft changed the answer", str(row))

        r = await call("edit_live_question", question_id=tf_id, changes={"text": "x"})
        check(not r.get("ok") and "draft" in r.get("error", ""), "edit_live_question refuses a draft", str(r))
        r = await call("tag_live", tag="test2", question_id=tf_id)
        check(not r.get("ok"), "tag_live refuses a draft question", str(r))

        a = await call("approve_question", question_id=tf_id)
        check(a.get("ok") and a.get("status") == "approved" and TAG in (a.get("tags") or []), "approve_question", str(a)[:300])
        pools = await call("list_pools", kind="questions", user=owner)
        check(any(x["tag"] == TAG for x in pools.get("question_pools", [])), "approved question is in its pool", str(pools))

        r = await call("update_question_draft", question_id=tf_id, changes={"text": "x"})
        check(not r.get("ok") and "edit_live_question" in r.get("error", ""), "update_question_draft refuses an approved row", str(r))
        r = await call("update_question_draft", question_id=mc_id, changes={"is_true": True})
        check(not r.get("ok") and "choices" in r.get("error", ""), "update_question_draft rejects a mismatched short-form answer", str(r))
        r = await call("approve_question", question_id="q_x/../../scenarios")
        check(not r.get("ok") and "valid id" in r.get("error", ""), "ids are checked before they reach a URL", str(r))
        r = await call("list_questions", format="essay")
        check(not r.get("ok"), "list_questions rejects an unknown format")
        r = await call("edit_live_question", question_id=tf_id, changes={"tags": ["nope"]})
        check(not r.get("ok"), "edit_live_question refuses tags")
        e = await call("edit_live_question", question_id=tf_id,
                       changes={"text": f"[smoke {stamp}] The sample mean is a parameter (edited live)."})
        check(e.get("ok") and "edited live" in e.get("text", ""), "edit_live_question", str(e)[:300])

        t = await call("tag_live", tag="test2", question_id=tf_id)
        check(t.get("ok"), "tag_live question", str(t))
        row = (await call("list_questions", question_id=tf_id))["questions"][0]
        check("test2" in row["tags"], "tag_live shows in live tags", str(row))
        t = await call("untag_live", tag="test2", question_id=tf_id)
        check(t.get("ok"), "untag_live question", str(t))

        un = await call("unapprove_question", question_id=tf_id)
        check(un.get("ok") and un.get("status") == "draft" and TAG in (un.get("tags") or []), "unapprove_question", str(un)[:300])

        # --- scenarios
        pv = await call("preview_scenario", sub_method="single_mean", fields=SM_FIELDS)
        check(pv.get("ok") and pv.get("question_text") and len(pv.get("variants") or []) == 6,
              "preview_scenario (6 variants)", str(pv)[:300])
        check("solution" not in pv and "data" not in pv, "preview_scenario is trimmed")
        badf = dict(SM_FIELDS, body_null="No placeholders here, and long enough to pass the length rule.")
        pv = await call("preview_scenario", sub_method="single_mean", fields=badf)
        check(not pv.get("ok") and pv.get("issues"), "preview_scenario reports issues", str(pv)[:300])

        fields = dict(SM_FIELDS, title=f"Smoke {stamp}")
        s = await call("save_scenario_draft", sub_method="single_mean", fields=fields)
        if not check(s.get("ok") and s.get("scenario_id"), "save_scenario_draft", str(s)[:300]):
            return 1
        sid = s["scenario_id"]
        ls = await call("list_scenarios", scenario_id=sid, include_fields=True)
        got = (ls.get("scenarios") or [{}])[0]
        check(ls["n"] == 1 and got.get("status") == "draft" and got.get("fields", {}).get("title") == fields["title"],
              "list_scenarios with fields", str(got)[:300])
        ls = await call("list_scenarios", scenario_id=sid)
        check(not {"fields", "fields_json"} & set(ls["scenarios"][0]), "list_scenarios omits fields by default")

        fields2 = dict(fields, flavor_null="Good luck.", flavor_alt="Good luck.")
        u = await call("update_scenario_draft", scenario_id=sid, fields=fields2)
        check(u.get("ok"), "update_scenario_draft", str(u)[:300])

        a = await call("approve_scenario", scenario_id=sid)
        check(a.get("ok") and a.get("status") == "generating", "approve_scenario starts generation", str(a)[:300])
        status, deadline = "generating", time.time() + 240
        while status == "generating" and time.time() < deadline:
            await asyncio.sleep(3)
            st = await call("scenario_status", scenario_id=sid)
            status = st.get("status")
        check(status == "approved", "scenario_status reaches approved", str(st))
        if status == "approved":
            r = await call("update_scenario_draft", scenario_id=sid, fields=fields2)
            check(not r.get("ok") and r.get("http_status") == 409, "update_scenario_draft refuses an approved scenario", str(r))
            t = await call("tag_live", tag=TAG, scenario_id=sid)
            check(t.get("ok"), "tag_live scenario (method resolved from the id)", str(t))
            pools = await call("list_pools", kind="scenarios", user=owner, tag=TAG)
            check(any(x["sub_method"] == "single_mean" for x in pools.get("scenario_pools", [])), "scenario is in its pool", str(pools))
            t = await call("untag_live", tag=TAG, scenario_id=sid)
            check(t.get("ok"), "untag_live scenario", str(t))
            un = await call("unapprove_scenario", scenario_id=sid)
            check(un.get("ok") and un.get("status") == "draft", "unapprove_scenario", str(un))

        # --- server-side drafting
        with tempfile.TemporaryDirectory() as d:
            big = Path(d) / "big.txt"
            big.write_text("x" * 40_001)
            r = await call("server_draft_questions", format="tf", source_path=str(big))
            check(not r.get("ok") and "40000" in r.get("error", ""), "server_draft_questions rejects an oversized file locally", str(r))
            r = await call("server_draft_questions", format="tf", source_path=str(Path(d) / "missing.txt"))
            check(not r.get("ok"), "server_draft_questions rejects a missing file")
            if llm:
                src = Path(d) / "src.txt"
                src.write_text(
                    "A parameter is a number that describes a population. A statistic is a "
                    "number computed from a sample. The sample mean is a statistic and is "
                    "used to estimate the population mean, which is a parameter. Statistics "
                    "vary from sample to sample; parameters are fixed but usually unknown."
                )
                r = await call("server_draft_questions", format="tf", source_path=str(src), n=2)
                check(r.get("ok") and r.get("candidates"), "server_draft_questions (LLM)", str(r)[:300])
                r = await call("server_draft_scenario", sub_method="single_mean",
                               message="A scenario about the sugar content of maple syrup.")
                check(r.get("ok") and r.get("fields", {}).get("body_null"), "server_draft_scenario (LLM)", str(r)[:300])
            else:
                print("SKIP server_draft_questions / server_draft_scenario LLM calls (pass --llm)")

        print(f"\nleft behind under '{owner}': question drafts {tf_id}, {mc_id}; scenario draft {sid}")

    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also call the LLM-backed server_draft_* tools")
    ap.add_argument("--owner", default=os.environ.get("WWMCP_SMOKE_OWNER", "mcptest"))
    ns = ap.parse_args()
    sys.exit(asyncio.run(run(ns.llm, ns.owner)))
