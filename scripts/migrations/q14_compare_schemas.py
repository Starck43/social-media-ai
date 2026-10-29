#!/usr/bin/env python3
"""Read-only semantic comparison of the local public and test schemas.

No application rows are read. The only tolerated differences are documented
legacy objects retained in ``public`` and, with ``--before-bridge``, the
intentional absence of the Q-14 action-audit objects before its compatibility
bridge is installed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

PUBLIC = "public"
TEST = "test_schema"
LEGACY_PUBLIC_TABLES = {"oauth_pending"}
LEGACY_PUBLIC_ENUMS = {"analyze_type"}
LEGACY_ORDERED_ENUMS = {"bot_action_status", "bot_trigger_type"}
AUDIT_TABLE = "action_audit_events"
AUDIT_FUNCTION = "guard_action_audit_mutation"
AUDIT_TRIGGERS = {"action_audit_events_immutable", "action_audit_events_no_truncate"}
RECONCILIATION_LABELS = {
    "bot_actions.agent_task_id FK": "ON DELETE CASCADE -> SET NULL",
    "tenant_channels UNIQUE": "(tenant_id, channel, chat_id) -> (channel, chat_id)",
}


def _norm_sql(value: str | None, schema: str) -> str | None:
    """Normalize schema qualification, benign identifier quotes and whitespace.

    String literal contents and SQL operators remain unchanged so logical
    differences in CHECK/function/index expressions stay visible.
    """
    if value is None:
        return None
    out: list[str] = []
    in_string = False
    in_identifier = False
    pending_space = False
    index = 0
    while index < len(value):
        char = value[index]
        if (
            not in_string
            and not in_identifier
            and (index == 0 or not (value[index - 1].isalnum() or value[index - 1] in "_$"))
        ):
            schema_match = re.match(rf'"?{re.escape(schema)}"?\s*\.', value[index:])
            if schema_match:
                if pending_space and out and out[-1] != " ":
                    out.append(" ")
                out.append("<schema>.")
                index += schema_match.end()
                pending_space = False
                continue
        if char == "'":
            if in_string and index + 1 < len(value) and value[index + 1] == "'":
                out.extend(("'", "'"))
                index += 2
                continue
            in_string = not in_string
            if pending_space and out and not in_string:
                out.append(" ")
            pending_space = False
            out.append(char)
        elif char == '"' and not in_string:
            if not in_identifier:
                end = value.find('"', index + 1)
                if end >= 0:
                    identifier = value[index + 1 : end]
                    if re.fullmatch(r"[a-z_][a-z0-9_$]*", identifier):
                        if pending_space and out and out[-1] != " ":
                            out.append(" ")
                        pending_space = False
                        out.append(identifier)
                        index = end + 1
                        continue
            in_identifier = not in_identifier
            out.append(char)
        elif char.isspace() and not in_string:
            pending_space = True
        else:
            if pending_space and out and out[-1] != " " and char != " ":
                out.append(" ")
            pending_space = False
            out.append(char)
        index += 1
    return "".join(out).strip()


def _norm_check(value: str, schema: str) -> str:
    """Normalize only equivalent text-array casts in PostgreSQL ANY comparisons.

    pg_dump replay may move an unbounded varchar[] -> text[] cast onto each
    constant element. Preserve literal contents, operators and all other casts.
    """
    normalized = _norm_sql(value, schema) or ""
    literal = r"'(?:[^']|'')*'::character varying(?:::text)?"
    pattern = re.compile(
        rf"(=\s*ANY\s*\(\s*)ARRAY\[((?:{literal})(?:\s*,\s*{literal})*)\](::text\[\])?(?=\s*\))",
        re.IGNORECASE,
    )

    def replace(match):
        elements = re.findall(literal, match.group(2), re.IGNORECASE)
        if not match.group(3) and not all(element.lower().endswith("::text") for element in elements):
            return match.group(0)
        elements = [
            re.sub(r"::character varying(?:::text)?$", "::text", item, flags=re.IGNORECASE) for item in elements
        ]
        return match.group(1) + "ARRAY[" + ",".join(elements) + "]"

    return pattern.sub(replace, normalized)


def _norm_default(value: str | None, type_name: str, schema: str) -> str | None:
    normalized = _norm_sql(value, schema)
    if normalized is None:
        return None
    # PostgreSQL can deparse the same floating literal as `1.0` or as
    # `'1'::double precision`. Collapse only a complete numeric literal whose
    # target type is already compared separately; leave all expressions intact.
    if type_name in {"real", "double precision"}:
        candidate = normalized.strip()
        while candidate.startswith("(") and candidate.endswith(")"):
            candidate = candidate[1:-1].strip()
        match = re.fullmatch(
            r"(?:'([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)'::(?:real|double precision)|"
            r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?))",
            candidate,
        )
        if match:
            try:
                number = Decimal(match.group(1) or match.group(2)).normalize()
                return f"numeric-literal:{number:f}"
            except InvalidOperation:
                pass
    return normalized


def _norm_function_config(value: str, schema: str) -> str:
    if value.startswith("search_path="):
        parts = [part.strip().strip('"') for part in value.partition("=")[2].split(",")]
        normalized = ["<schema>" if part == schema else part for part in parts]
        return "search_path=" + ", ".join(normalized)
    return _norm_sql(value, schema) or value


def _rows(connection, query: str, **params: Any) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return [dict(row) for row in connection.execute(text(query), params).mappings()]


def _catalog(connection, schema: str) -> dict[str, Any]:
    schema_filter = {"schema": schema}
    relations = _rows(
        connection,
        """SELECT c.oid, c.relname, c.relkind
           FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
           ORDER BY c.relname""",
        **schema_filter,
    )
    tables = {row["relname"]: row["relkind"] for row in relations}
    columns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(
        connection,
        """SELECT c.relname AS table_name, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
                  a.attnotnull, pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_expr,
                  a.attidentity, a.attgenerated, coll.collname AS collation
           FROM pg_catalog.pg_attribute a
           JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
           LEFT JOIN pg_catalog.pg_collation coll ON coll.oid = a.attcollation
           WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
             AND a.attnum > 0 AND NOT a.attisdropped
           ORDER BY c.relname, a.attnum""",
        **schema_filter,
    ):
        columns[row["table_name"]].append(
            {
                "name": row["attname"],
                "type": _norm_sql(row["type"], schema),
                "nullable": not row["attnotnull"],
                "default": _norm_default(row["default_expr"], _norm_sql(row["type"], schema) or "", schema),
                "identity": row["attidentity"],
                "generated": row["attgenerated"],
                "collation": row["collation"],
            }
        )

    constraints: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(
        connection,
        """SELECT con.oid, tbl.relname AS table_name, con.contype, con.conkey, con.confkey,
                  con.condeferrable, con.condeferred, con.convalidated,
                  COALESCE((to_jsonb(con)->>'connullsnotdistinct')::boolean, false) AS nulls_not_distinct,
                  con.confmatchtype, con.confupdtype, con.confdeltype,
                  ARRAY(
                    SELECT att.attname
                    FROM jsonb_array_elements_text(
                      COALESCE(NULLIF(to_jsonb(con)->'confdelsetcols', 'null'::jsonb), '[]'::jsonb)
                    ) WITH ORDINALITY AS deleted(attnum, ord)
                    JOIN pg_catalog.pg_attribute att
                      ON att.attrelid = con.confrelid AND att.attnum = deleted.attnum::smallint
                    ORDER BY deleted.ord
                  ) AS delete_set_columns,
                  ref_ns.nspname AS ref_schema, ref_tbl.relname AS ref_table,
                  pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
           FROM pg_catalog.pg_constraint con
           JOIN pg_catalog.pg_class tbl ON tbl.oid = con.conrelid
           JOIN pg_catalog.pg_namespace ns ON ns.oid = tbl.relnamespace
           LEFT JOIN pg_catalog.pg_class ref_tbl ON ref_tbl.oid = con.confrelid
           LEFT JOIN pg_catalog.pg_namespace ref_ns ON ref_ns.oid = ref_tbl.relnamespace
           WHERE ns.nspname = :schema
           ORDER BY tbl.relname, con.contype, con.oid""",
        **schema_filter,
    ):
        table_oid = row["oid"]
        constraint_columns = _rows(
            connection,
            """SELECT a.attname
               FROM unnest((SELECT conkey FROM pg_catalog.pg_constraint WHERE oid = :oid))
                    WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_catalog.pg_attribute a ON a.attrelid = (
                    SELECT conrelid FROM pg_catalog.pg_constraint WHERE oid = :oid
               ) AND a.attnum = k.attnum ORDER BY k.ord""",
            oid=table_oid,
        )
        ref_columns = (
            _rows(
                connection,
                """SELECT a.attname
               FROM unnest((SELECT confkey FROM pg_catalog.pg_constraint WHERE oid = :oid))
                    WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_catalog.pg_attribute a ON a.attrelid = (
                    SELECT confrelid FROM pg_catalog.pg_constraint WHERE oid = :oid
               ) AND a.attnum = k.attnum ORDER BY k.ord""",
                oid=table_oid,
            )
            if row["contype"] == "f"
            else []
        )
        item: dict[str, Any] = {
            "kind": row["contype"],
            "columns": [part["attname"] for part in constraint_columns],
            "deferrable": row["condeferrable"],
            "initially_deferred": row["condeferred"],
            "validated": row["convalidated"],
            "nulls_not_distinct": row["nulls_not_distinct"],
        }
        if row["contype"] == "f":
            item.update(
                {
                    "references": {
                        "schema": "<schema>" if row["ref_schema"] == schema else row["ref_schema"],
                        "table": row["ref_table"],
                        "columns": [part["attname"] for part in ref_columns],
                    },
                    "match": row["confmatchtype"],
                    "on_update": row["confupdtype"],
                    "on_delete": row["confdeltype"],
                    "delete_set_columns": row["delete_set_columns"],
                }
            )
        elif row["contype"] == "c":
            item["expression"] = _norm_check(row["definition"], schema)
        constraints[row["table_name"]].append(item)

    indexes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(
        connection,
        """SELECT tbl.relname AS table_name, idx.relname AS index_name, i.indexrelid,
                  i.indisunique, i.indisprimary, i.indisvalid, i.indisready, i.indislive,
                  COALESCE((to_jsonb(i)->>'indnullsnotdistinct')::boolean, false) AS nulls_not_distinct,
                  i.indnkeyatts, i.indnatts, am.amname AS method,
                  pg_catalog.pg_get_expr(i.indpred, i.indrelid, true) AS predicate
           FROM pg_catalog.pg_index i
           JOIN pg_catalog.pg_class tbl ON tbl.oid = i.indrelid
           JOIN pg_catalog.pg_namespace ns ON ns.oid = tbl.relnamespace
           JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
           JOIN pg_catalog.pg_am am ON am.oid = idx.relam
           WHERE ns.nspname = :schema
           ORDER BY tbl.relname, idx.relname""",
        **schema_filter,
    ):
        key_parts = _rows(
            connection,
            """SELECT pos, pg_catalog.pg_get_indexdef(:index_oid, pos, true) AS expression,
                      opc.opcname, coll.collname,
                      ((i.indoption[pos - 1] & 1) <> 0) AS descending,
                      ((i.indoption[pos - 1] & 2) <> 0) AS nulls_first
               FROM generate_series(1, :key_count) AS pos
               JOIN pg_catalog.pg_index i ON i.indexrelid = :index_oid
               LEFT JOIN pg_catalog.pg_opclass opc ON opc.oid = i.indclass[pos - 1]
               LEFT JOIN pg_catalog.pg_collation coll ON coll.oid = i.indcollation[pos - 1]
               ORDER BY pos""",
            index_oid=row["indexrelid"],
            key_count=row["indnatts"],
        )
        indexes[row["table_name"]].append(
            {
                "name": row["index_name"],
                "unique": row["indisunique"],
                "primary": row["indisprimary"],
                "valid": row["indisvalid"],
                "ready": row["indisready"],
                "live": row["indislive"],
                "nulls_not_distinct": row["nulls_not_distinct"],
                "method": row["method"],
                "keys": [
                    {
                        "expression": _norm_sql(part["expression"], schema),
                        "opclass": part["opcname"],
                        "collation": part["collname"],
                        "descending": part["descending"],
                        "nulls_first": part["nulls_first"],
                    }
                    for part in key_parts
                    if part["pos"] <= row["indnkeyatts"]
                ],
                "included": [
                    _norm_sql(part["expression"], schema) for part in key_parts if part["pos"] > row["indnkeyatts"]
                ],
                "predicate": _norm_sql(row["predicate"], schema),
            }
        )

    enums: dict[str, list[str]] = {}
    for row in _rows(
        connection,
        """SELECT t.typname, e.enumlabel
           FROM pg_catalog.pg_type t
           JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
           JOIN pg_catalog.pg_enum e ON e.enumtypid = t.oid
           WHERE n.nspname = :schema ORDER BY t.typname, e.enumsortorder""",
        **schema_filter,
    ):
        enums.setdefault(row["typname"], []).append(row["enumlabel"])

    sequences: dict[str, dict[str, Any]] = {}
    sequence_owners: dict[str, str] = {}
    for row in _rows(
        connection,
        """SELECT c.relname, s.seqtypid::regtype::text AS type, s.seqstart, s.seqincrement,
                  s.seqmax, s.seqmin, s.seqcache, s.seqcycle,
                  tbl.relname AS owner_table, a.attname AS owner_column
           FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_catalog.pg_sequence s ON s.seqrelid = c.oid
           LEFT JOIN pg_catalog.pg_depend d ON d.objid = c.oid AND d.deptype IN ('a', 'i')
           LEFT JOIN pg_catalog.pg_class tbl ON tbl.oid = d.refobjid
           LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = tbl.oid AND a.attnum = d.refobjsubid
           WHERE n.nspname = :schema ORDER BY c.relname""",
        **schema_filter,
    ):
        owner = [row["owner_table"], row["owner_column"]] if row["owner_table"] else None
        semantic_key = f"owned:{owner[0]}.{owner[1]}" if owner else f"standalone:{row['relname']}"
        sequences[semantic_key] = {
            "name": row["relname"],
            "type": _norm_sql(row["type"], schema),
            "start": row["seqstart"],
            "increment": row["seqincrement"],
            "max": row["seqmax"],
            "min": row["seqmin"],
            "cache": row["seqcache"],
            "cycle": row["seqcycle"],
            "owner": owner,
        }
        sequence_owners[row["relname"]] = semantic_key

    for table_columns in columns.values():
        for column in table_columns:
            default = column["default"]
            if default is None:
                continue
            relation = re.fullmatch(r"nextval\('(?:(?:public|test_schema)\.)?([^']+)'::regclass\)", default)
            if relation and relation.group(1) in sequence_owners:
                column["default"] = f"nextval('<{sequence_owners[relation.group(1)]}>'::regclass)"

    functions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(
        connection,
        """SELECT p.proname, p.prokind, pg_catalog.pg_get_function_identity_arguments(p.oid) AS arguments,
                  pg_catalog.pg_get_function_result(p.oid) AS result, l.lanname,
                  p.provolatile, p.proparallel, p.proisstrict, p.prosecdef,
                  p.proconfig,
                  CASE WHEN p.prokind = 'a' THEN NULL ELSE pg_catalog.pg_get_functiondef(p.oid) END AS definition,
                  ag.aggkind, ag.aggnumdirectargs,
                  ag.aggtransfn::regproc::text AS transition_function,
                  ag.aggfinalfn::regproc::text AS final_function,
                  ag.aggcombinefn::regproc::text AS combine_function,
                  ag.aggserialfn::regproc::text AS serialize_function,
                  ag.aggdeserialfn::regproc::text AS deserialize_function,
                  ag.aggtranstype::regtype::text AS transition_type,
                  ag.aggtransspace, ag.agginitval, ag.aggminitval
           FROM pg_catalog.pg_proc p
           JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
           JOIN pg_catalog.pg_language l ON l.oid = p.prolang
           LEFT JOIN pg_catalog.pg_aggregate ag ON ag.aggfnoid = p.oid
           WHERE n.nspname = :schema ORDER BY p.proname, arguments""",
        **schema_filter,
    ):
        signature = _norm_sql(f"({row['arguments']}) -> {row['result']}", schema)
        functions[row["proname"]].append(
            {
                "signature": signature,
                "kind": row["prokind"],
                "language": row["lanname"],
                "volatility": row["provolatile"],
                "parallel": row["proparallel"],
                "strict": row["proisstrict"],
                "security_definer": row["prosecdef"],
                "config": (
                    [_norm_function_config(item, schema) for item in row["proconfig"]] if row["proconfig"] else None
                ),
                "definition": _norm_sql(row["definition"], schema),
                "aggregate": (
                    {
                        "kind": row["aggkind"],
                        "direct_args": row["aggnumdirectargs"],
                        "transition_function": _norm_sql(row["transition_function"], schema),
                        "final_function": _norm_sql(row["final_function"], schema),
                        "combine_function": _norm_sql(row["combine_function"], schema),
                        "serialize_function": _norm_sql(row["serialize_function"], schema),
                        "deserialize_function": _norm_sql(row["deserialize_function"], schema),
                        "transition_type": _norm_sql(row["transition_type"], schema),
                        "transition_space": row["aggtransspace"],
                        "initial_value": row["agginitval"],
                        "moving_initial_value": row["aggminitval"],
                    }
                    if row["prokind"] == "a"
                    else None
                ),
            }
        )

    triggers: list[dict[str, Any]] = []
    for row in _rows(
        connection,
        """SELECT tbl.relname AS table_name, t.tgname, p.proname AS function_name,
                  t.tgenabled, t.tgdeferrable, t.tginitdeferred, t.tgtype,
                  pg_catalog.pg_get_triggerdef(t.oid, true) AS definition
           FROM pg_catalog.pg_trigger t
           JOIN pg_catalog.pg_class tbl ON tbl.oid = t.tgrelid
           JOIN pg_catalog.pg_namespace n ON n.oid = tbl.relnamespace
           JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
           WHERE n.nspname = :schema AND NOT t.tgisinternal
           ORDER BY tbl.relname, t.tgname""",
        **schema_filter,
    ):
        triggers.append(
            {
                "table": row["table_name"],
                "name": row["tgname"],
                "function": row["function_name"],
                "enabled": row["tgenabled"],
                "deferrable": row["tgdeferrable"],
                "initially_deferred": row["tginitdeferred"],
                "events": row["tgtype"],
                "definition": _norm_sql(row["definition"], schema),
            }
        )

    return {
        "tables": tables,
        "columns": dict(columns),
        "constraints": dict(constraints),
        "indexes": dict(indexes),
        "enums": enums,
        "sequences": sequences,
        "functions": dict(functions),
        "triggers": triggers,
    }


def _as_set_delta(left: set[Any], right: set[Any]) -> dict[str, list[Any]]:
    return {
        "missing_in_public": sorted(right - left, key=repr),
        "extra_in_public": sorted(left - right, key=repr),
    }


def _allowed_legacy_public_difference(
    public: dict[str, Any], test: dict[str, Any], before_bridge: bool, allow_legacy: bool
):
    allowed: list[str] = []
    if allow_legacy and "oauth_pending" in public["tables"] and "oauth_pending" not in test["tables"]:
        allowed.append("public retains legacy oauth_pending and its owned schema objects")
    if allow_legacy and "analyze_type" in public["enums"] and "analyze_type" not in test["enums"]:
        allowed.append("public retains legacy analyze_type enum")
    if allow_legacy:
        for enum_name in sorted(LEGACY_ORDERED_ENUMS):
            left, right = public["enums"].get(enum_name), test["enums"].get(enum_name)
            if left is not None and right is not None and left != right and set(left) == set(right):
                allowed.append(f"public has historical {enum_name} label order")
    if before_bridge and AUDIT_TABLE not in public["tables"] and AUDIT_TABLE in test["tables"]:
        allowed.append("public predates action_audit_events bridge objects")
    return allowed


def _constraint_contract(**values: Any) -> dict[str, Any]:
    contract = {
        "deferrable": False,
        "initially_deferred": False,
        "validated": True,
        "nulls_not_distinct": False,
    }
    contract.update(values)
    return contract


def _planned_reconciliations(public: dict[str, Any], test: dict[str, Any]) -> dict[str, Any]:
    """Return exact source/target contracts for the two authorized model fixes."""
    pairs: dict[str, Any] = {}
    source_fk = _constraint_contract(
        kind="f",
        columns=["agent_task_id"],
        references={"schema": "<schema>", "table": "agent_tasks", "columns": ["id"]},
        match="s",
        on_update="a",
        on_delete="c",
        delete_set_columns=[],
    )
    target_fk = {**source_fk, "on_delete": "n"}
    source_unique = _constraint_contract(kind="u", columns=["tenant_id", "channel", "chat_id"])
    target_unique = _constraint_contract(kind="u", columns=["channel", "chat_id"])

    fk_pair = _matching_pair(
        public["constraints"].get("bot_actions", []), test["constraints"].get("bot_actions", []), source_fk, target_fk
    )
    if fk_pair:
        pairs["bot_actions.agent_task_id FK"] = {"constraint": fk_pair}

    source_index, target_index = _tenant_channel_index_pair(
        public["indexes"].get("tenant_channels", []), test["indexes"].get("tenant_channels", [])
    )
    unique_pair = _matching_pair(
        public["constraints"].get("tenant_channels", []),
        test["constraints"].get("tenant_channels", []),
        source_unique,
        target_unique,
    )
    if unique_pair is not None and source_index is not None and target_index is not None:
        pairs["tenant_channels UNIQUE"] = {
            "constraint": unique_pair,
            "index": (source_index, target_index),
        }
    return pairs


def _matching_pair(source: list[dict[str, Any]], target: list[dict[str, Any]], source_value: dict, target_value: dict):
    source_index = next((index for index, item in enumerate(source) if item == source_value), None)
    target_index = next((index for index, item in enumerate(target) if item == target_value), None)
    if source_index is None or target_index is None:
        return None
    return source_index, target_index


def _tenant_channel_index_pair(source: list[dict[str, Any]], target: list[dict[str, Any]]):
    """Match the exact unique btree whose only change drops the tenant key."""

    def base_matches(index: dict[str, Any]) -> bool:
        return (
            index["unique"] is True
            and index["primary"] is False
            and index["valid"] is True
            and index["ready"] is True
            and index["live"] is True
            and index["nulls_not_distinct"] is False
            and index["method"] == "btree"
            and index["included"] == []
            and index["predicate"] is None
        )

    def key_is_default(key: dict[str, Any]) -> bool:
        return key["descending"] is False and key["nulls_first"] is False and key["opclass"] is not None

    for source_pos, source_item in enumerate(source):
        if not base_matches(source_item) or len(source_item["keys"]) != 3:
            continue
        expressions = [key["expression"] for key in source_item["keys"]]
        if expressions != ["tenant_id", "channel", "chat_id"]:
            continue
        if not all(key_is_default(key) for key in source_item["keys"]):
            continue
        source_first = source_item["keys"][0]
        if source_first["collation"] is not None:
            continue
        for target_pos, target_item in enumerate(target):
            if not base_matches(target_item) or len(target_item["keys"]) != 2:
                continue
            if target_item["keys"] == source_item["keys"][1:]:
                return source_pos, target_pos
    return None, None


def _remove_pair(source: list[Any], target: list[Any], pair: tuple[int, int] | None) -> tuple[list[Any], list[Any]]:
    if pair is None:
        return source, target
    source_index, target_index = pair
    return (
        [item for index, item in enumerate(source) if index != source_index],
        [item for index, item in enumerate(target) if index != target_index],
    )


def compare(
    public: dict[str, Any],
    test: dict[str, Any],
    *,
    before_bridge: bool = False,
    model_tables: set[str] | None = None,
    allow_reconciliation: bool = False,
    allow_legacy: bool = True,
) -> dict[str, Any]:
    allowed_public_tables = {
        name
        for name in LEGACY_PUBLIC_TABLES
        if allow_legacy and name in public["tables"] and name not in test["tables"]
    }
    allowed_public_enums = {
        name for name in LEGACY_PUBLIC_ENUMS if allow_legacy and name in public["enums"] and name not in test["enums"]
    }
    ignored_audit = before_bridge and AUDIT_TABLE not in public["tables"] and AUDIT_TABLE in test["tables"]
    recognized_reconciliations = _planned_reconciliations(public, test) if allow_reconciliation else {}
    planned_reconciliations: dict[str, Any] = {}
    for name, pair in recognized_reconciliations.items():
        if name == "bot_actions.agent_task_id FK":
            source_index, target_index = pair["constraint"]
            planned_reconciliations[name] = {
                "change": RECONCILIATION_LABELS[name],
                "source": {"constraint": public["constraints"]["bot_actions"][source_index]},
                "target": {"constraint": test["constraints"]["bot_actions"][target_index]},
            }
        else:
            source_constraint, target_constraint = pair["constraint"]
            source_index, target_index = pair["index"]
            planned_reconciliations[name] = {
                "change": RECONCILIATION_LABELS[name],
                "source": {
                    "constraint": public["constraints"]["tenant_channels"][source_constraint],
                    "index": public["indexes"]["tenant_channels"][source_index],
                },
                "target": {
                    "constraint": test["constraints"]["tenant_channels"][target_constraint],
                    "index": test["indexes"]["tenant_channels"][target_index],
                },
            }

    differences: dict[str, Any] = {}
    if model_tables is not None:
        test_application_tables = set(test["tables"]) - {"alembic_version"}
        missing = sorted(model_tables - test_application_tables)
        extra = sorted(test_application_tables - model_tables)
        if missing or extra:
            differences["test_schema_model_tables"] = {"missing": missing, "extra": extra}
    allowed = _allowed_legacy_public_difference(public, test, before_bridge, allow_legacy)
    index_name_mismatches: list[str] = []
    for category in ("tables", "enums", "sequences", "functions"):
        left, right = public[category], test[category]
        names_left, names_right = set(left), set(right)
        ignore_left: set[str] = set()
        ignore_right: set[str] = set()
        if category == "tables":
            ignore_left |= allowed_public_tables
            if ignored_audit:
                ignore_right.add(AUDIT_TABLE)
        if category == "enums" and allow_legacy:
            ignore_left |= allowed_public_enums
        if category == "functions":
            if ignored_audit:
                ignore_right.add(AUDIT_FUNCTION)
            if allow_legacy:
                public_legacy_trigger_functions = {
                    item["function"] for item in public["triggers"] if item["table"] in allowed_public_tables
                }
                for name in public_legacy_trigger_functions:
                    if name not in test["functions"]:
                        ignore_left.add(name)
        if category == "sequences" and ignored_audit:
            ignore_right |= {name for name, item in right.items() if item["owner"] and item["owner"][0] == AUDIT_TABLE}
        delta = _as_set_delta(names_left - ignore_left, names_right - ignore_right)
        changed = {}
        for name in sorted((names_left & names_right) - ignore_left - ignore_right):
            left_value, right_value = left[name], right[name]
            equal = left_value == right_value
            if category == "sequences":
                left_semantic = {key: value for key, value in left_value.items() if key != "name"}
                right_semantic = {key: value for key, value in right_value.items() if key != "name"}
                equal = left_semantic == right_semantic
            if not equal:
                changed[name] = {"public": left_value, "test_schema": right_value}
        if category == "enums" and allow_legacy:
            for enum_name in LEGACY_ORDERED_ENUMS:
                if enum_name in changed and set(changed[enum_name]["public"]) == set(changed[enum_name]["test_schema"]):
                    changed.pop(enum_name)
        if any(delta.values()) or changed:
            differences[category] = {**delta, "changed": changed}

    table_names_public, table_names_test = set(public["tables"]), set(test["tables"])
    shared_tables = (table_names_public & table_names_test) - allowed_public_tables
    if ignored_audit:
        shared_tables.discard(AUDIT_TABLE)
    for category in ("columns", "constraints", "indexes"):
        changed = {}
        for table in sorted(shared_tables):
            left = public[category].get(table, []) if category == "columns" else public[category].get(table, [])
            right = test[category].get(table, [])
            if category == "columns":
                left = {item["name"]: item for item in left}
                right = {item["name"]: item for item in right}
                column_delta = _as_set_delta(set(left), set(right))
                column_changed = {
                    name: {"public": left[name], "test_schema": right[name]}
                    for name in sorted(set(left) & set(right))
                    if left[name] != right[name]
                }
                if any(column_delta.values()) or column_changed:
                    changed[table] = {**column_delta, "changed": column_changed}
            elif category == "indexes":
                if allow_reconciliation and table == "tenant_channels":
                    pair = recognized_reconciliations.get("tenant_channels UNIQUE", {}).get("index")
                    left, right = _remove_pair(left, right, pair)
                left_semantic = [{key: value for key, value in item.items() if key != "name"} for item in left]
                right_semantic = [{key: value for key, value in item.items() if key != "name"} for item in right]
                left_semantic.sort(key=lambda item: json.dumps(item, sort_keys=True))
                right_semantic.sort(key=lambda item: json.dumps(item, sort_keys=True))
                if left_semantic != right_semantic:
                    changed[table] = {"public": left, "test_schema": right}
                elif sorted(item["name"] for item in left) != sorted(item["name"] for item in right):
                    index_name_mismatches.append(table)
            else:
                if category == "constraints":
                    # Constraint names vary between historical DDL and metadata
                    # create_all. Compare full semantics while ignoring names.
                    if (
                        allow_reconciliation
                        and table == "bot_actions"
                        and "bot_actions.agent_task_id FK" in recognized_reconciliations
                    ):
                        left, right = _remove_pair(
                            left, right, recognized_reconciliations["bot_actions.agent_task_id FK"]["constraint"]
                        )
                    if (
                        allow_reconciliation
                        and table == "tenant_channels"
                        and "tenant_channels UNIQUE" in recognized_reconciliations
                    ):
                        left, right = _remove_pair(
                            left, right, recognized_reconciliations["tenant_channels UNIQUE"]["constraint"]
                        )
                    left = sorted(left, key=lambda item: json.dumps(item, sort_keys=True))
                    right = sorted(right, key=lambda item: json.dumps(item, sort_keys=True))
                if left != right:
                    changed[table] = {"public": left, "test_schema": right}
        if changed:
            differences[category] = {"changed": changed}

    public_triggers = public["triggers"]
    test_triggers = test["triggers"]
    if allow_legacy:
        public_triggers = [item for item in public_triggers if item["table"] not in allowed_public_tables]
    if ignored_audit:
        test_triggers = [
            item for item in test_triggers if not (item["table"] == AUDIT_TABLE and item["name"] in AUDIT_TRIGGERS)
        ]
    if public_triggers != test_triggers:
        differences["triggers"] = {"public": public_triggers, "test_schema": test_triggers}

    if index_name_mismatches:
        allowed.append(
            "index names differ while indexed expressions and options match: " + ", ".join(index_name_mismatches)
        )
    # Existing public-only legacy tables have associated sequences and indexes;
    # those are explicitly covered by the table allowlist rather than treated
    # as unrelated drift.
    if "sequences" in differences:
        seq_delta = differences["sequences"]
        seq_delta["extra_in_public"] = sorted(
            set(seq_delta["extra_in_public"])
            - {
                name
                for name, item in public["sequences"].items()
                if item["owner"] and item["owner"][0] in allowed_public_tables
            }
        )
        if not any(seq_delta.values()) and not seq_delta.get("changed"):
            differences.pop("sequences")

    # The Q-14 legacy table allowlist includes its local constraints and indexes.
    for category in ("constraints", "indexes"):
        if category in differences:
            for table in allowed_public_tables:
                differences[category].get("changed", {}).pop(table, None)
            if not differences[category].get("changed"):
                differences.pop(category)

    return {
        "status": "DRIFT" if differences else "PASS",
        "allowed_differences": allowed,
        "planned_reconciliations": planned_reconciliations,
        "differences": differences,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before-bridge",
        action="store_true",
        help="allow the two explicit model reconciliations and public to lack only action-audit bridge objects",
    )
    parser.add_argument(
        "--strict-legacy", action="store_true", help="require historical public objects and enum order to match"
    )
    args = parser.parse_args(argv)

    from sqlalchemy.engine import make_url

    from app.core.config import settings
    from app.core.database import engine
    from app.models import Base

    url = make_url(settings.POSTGRES_URL)
    if (url.host not in (None, "localhost", "127.0.0.1")) or url.database != "social_manager":
        print(json.dumps({"status": "REFUSED", "reason": "expected local social_manager database"}))
        return 2

    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
            exists = {
                row["nspname"]
                for row in _rows(
                    connection,
                    "SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname IN (:public, :test)",
                    public=PUBLIC,
                    test=TEST,
                )
            }
            if exists != {PUBLIC, TEST}:
                print(
                    json.dumps({"status": "REFUSED", "reason": "required schemas missing", "present": sorted(exists)})
                )
                return 2
            public_catalog, test_catalog = _catalog(connection, PUBLIC), _catalog(connection, TEST)
            if "alembic_version" not in public_catalog["tables"] or "alembic_version" not in test_catalog["tables"]:
                print(json.dumps({"status": "REFUSED", "reason": "alembic_version table missing"}))
                return 2
            model_tables = {table.name for table in Base.metadata.sorted_tables}
            result = compare(
                public_catalog,
                test_catalog,
                before_bridge=args.before_bridge,
                model_tables=model_tables,
                allow_reconciliation=args.before_bridge,
                allow_legacy=not args.strict_legacy,
            )
    except Exception as exc:
        # Keep connection strings, SQL values and application data out of output.
        print(json.dumps({"status": "ERROR", "reason": type(exc).__name__}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
