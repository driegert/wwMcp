"""Remove everything one owner authored from a wwApi workspace SQLite.

wwApi has no delete verb, so test content (the `mcptest` key) is cleared here.
Dry run by default: prints what would go. --apply backs the database up next
to itself, then deletes in one transaction.

    uv run python scripts/cleanup_owner.py --db ../wwApi/workspace/workspace.sqlite3 --owner mcptest
    uv run python scripts/cleanup_owner.py --db ... --owner mcptest --apply

Removes: the owner's concept questions and their tags; the owner's scenarios
with their templates, bank rows and observations; the owner's scenario tags.
--reset-counters also drops the owner's id counters so ids restart at 1 (only
sensible for a throwaway owner: reused ids can collide with frozen WeBWorK
caches). The global seed-block counter is never touched.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def plan(con: sqlite3.Connection, owner: str) -> list[tuple[str, str, tuple]]:
    """(label, WHERE-qualified table expression, params) in delete order."""
    scen = "SELECT scenario_id FROM user_scenarios WHERE owner = ?"
    seeds = f"SELECT seed, method_type, sub_method FROM user_solutions WHERE scenario_id IN ({scen})"
    # Observations hang off (seed, method_type, sub_method), not a scenario id.
    # Seed blocks are allocated per scenario so they should never be shared,
    # but never delete a row another owner's bank still points at.
    kept = f"SELECT seed, method_type, sub_method FROM user_solutions WHERE scenario_id NOT IN ({scen})"
    return [
        ("scenario observations",
         f"user_observations WHERE (seed, method_type, sub_method) IN ({seeds}) "
         f"AND (seed, method_type, sub_method) NOT IN ({kept})", (owner, owner)),
        ("scenario bank rows", f"user_solutions WHERE scenario_id IN ({scen})", (owner,)),
        ("scenario templates", f"user_templates WHERE scenario_id IN ({scen})", (owner,)),
        ("scenario tags (owner's)", "tags WHERE user_id = ?", (owner,)),
        ("scenarios", "user_scenarios WHERE owner = ?", (owner,)),
        ("question tags", "question_tags WHERE user_id = ?", (owner,)),
        ("questions", "user_questions WHERE owner = ?", (owner,)),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--reset-counters", action="store_true")
    ns = ap.parse_args()

    if not OWNER_RE.match(ns.owner):
        sys.exit("owner must match ^[a-z0-9][a-z0-9_-]{0,63}$")
    if not ns.db.is_file():
        sys.exit(f"no such database: {ns.db}")

    con = sqlite3.connect(ns.db, timeout=10)
    con.execute("PRAGMA busy_timeout = 10000")
    steps = plan(con, ns.owner)
    # Exact match on the owner component: `_` in an owner is a LIKE wildcard.
    counters = [
        (name, value)
        for name, value in con.execute("SELECT name, value FROM workspace_counters")
        if name == f"q:{ns.owner}"
        or (name.startswith("scn:") and name.split(":")[2:] == [ns.owner])
    ]
    # Other instructors' tags pointing at this owner's scenarios would go stale.
    foreign = con.execute(
        "SELECT user_id, tag, scenario_id FROM tags WHERE user_id != ? AND scenario_id IN "
        "(SELECT scenario_id FROM user_scenarios WHERE owner = ?)", (ns.owner, ns.owner),
    ).fetchall()

    print(f"{ns.db}  owner={ns.owner}  ({'APPLY' if ns.apply else 'dry run'})")
    total = 0
    for label, expr, params in steps:
        n = con.execute(f"SELECT COUNT(*) FROM {expr}", params).fetchone()[0]
        total += n
        print(f"  {n:6d}  {label}")
    for name, value in counters:
        print(f"          counter {name} = {value}" + ("  (will reset)" if ns.reset_counters else "  (kept)"))
    for row in foreign:
        print(f"  WARNING another instructor's tag references a scenario being removed: {row}")
    if foreign:
        print("  Those tag rows are left alone and will 404 as stale; untag them first if that matters.")

    if not ns.apply:
        print("dry run: nothing changed. Re-run with --apply to delete.")
        return 0

    # Take the write lock FIRST so nothing can change between the backup and
    # the delete; the backup reads the last committed state over a second
    # connection.
    con.isolation_level = None
    con.execute("BEGIN IMMEDIATE")
    try:
        total = sum(
            con.execute(f"SELECT COUNT(*) FROM {expr}", params).fetchone()[0]
            for _label, expr, params in steps
        )
        if total == 0 and not (ns.reset_counters and counters):
            con.execute("ROLLBACK")
            print("nothing to delete.")
            return 0
        backup = ns.db.with_name(f"{ns.db.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        src, dst = sqlite3.connect(ns.db, timeout=10), sqlite3.connect(backup)
        src.backup(dst)
        src.close()
        dst.close()
        print(f"backup written: {backup}")
        for _label, expr, params in steps:
            con.execute(f"DELETE FROM {expr}", params)
        if ns.reset_counters:
            con.executemany(
                "DELETE FROM workspace_counters WHERE name = ?", [(n,) for n, _ in counters]
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print("deleted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
