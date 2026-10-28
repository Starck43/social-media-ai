#!/usr/bin/env python3
"""Snapshot a PostgreSQL schema layout so before/after runs can be diffed.

Layout only: no row data except the Alembic revision. Copy the JSON to a temp
path before a run, write it again afterwards, then compare with --diff.
"""

import argparse
import json
import os
import sys


def snapshot(schema):
    import psycopg2

    url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("POSTGRES_URL")
    if not url:
        raise SystemExit("TEST_POSTGRES_URL or POSTGRES_URL must be set")
    with psycopg2.connect(url) as conn, conn.cursor() as cur:

        def rows(sql, *args):
            cur.execute(sql, args)
            return sorted(row[0] for row in cur.fetchall())

        state = {
            "tables": rows(
                """select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
                   where n.nspname=%s and c.relkind in ('r','p')""",
                schema,
            ),
            "sequences": rows(
                """select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
                   where n.nspname=%s and c.relkind='S'""",
                schema,
            ),
            "types": rows(
                """select t.typname from pg_type t join pg_namespace n on n.oid=t.typnamespace
                   where n.nspname=%s and t.typtype in ('e','d','c')""",
                schema,
            ),
            "functions": rows(
                """select p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                   where n.nspname=%s""",
                schema,
            ),
            "triggers": rows(
                """select c.relname || '.' || t.tgname from pg_trigger t
                   join pg_class c on c.oid=t.tgrelid join pg_namespace n on n.oid=c.relnamespace
                   where n.nspname=%s and not t.tgisinternal""",
                schema,
            ),
            "indexes": rows(
                """select schemaname || '.' || indexname from pg_indexes where schemaname=%s""",
                schema,
            ),
        }
        for table in ("alembic_version", "model_types"):
            if table not in state["tables"]:
                continue
            column = "version_num" if table == "alembic_version" else "id"
            cur.execute(f"select count(*), max({column})::text from {schema}.{table}")
            state[f"{table}_rows"], state[f"{table}_max"] = cur.fetchone()
        return state


def diff(before, after):
    problems = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if isinstance(old, list) and isinstance(new, list):
            added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
            if added:
                problems.append(f"{key}: added {added}")
            if removed:
                problems.append(f"{key}: removed {removed}")
        elif old != new:
            problems.append(f"{key}: {old} -> {new}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema")
    parser.add_argument("out", nargs="?")
    parser.add_argument("--diff", metavar="BEFORE", help="compare against an earlier snapshot")
    args = parser.parse_args()
    state = snapshot(args.schema)
    if args.diff:
        with open(args.diff, encoding="utf-8") as handle:
            problems = diff(json.load(handle), state)
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    text = json.dumps(state, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
