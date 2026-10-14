"""Proposed, opt-in synthetic preparation; execution needs separate owner approval.

No database provisioning, chain upgrade, stamp, create_all, reset or cleanup.
Preflight is read-only from connection startup. Rehearsal always rolls back.
Apply requires durable preflight and independently reviewed rehearsal artifacts.
This does not verify a normal Alembic0094->0095 upgrade.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import ipaddress
import json
import logging
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CODE_PIN = "9dd748cef465e09df74cc98c07ecc65748b41a8c"
RUN_ID = "7417a3edbb5e42ce990c721c900d682f"
TARGET_HOST, TARGET_PORT = "localhost", 5432
TARGET_DATABASE, TARGET_SCHEMA = "social_manager_budget_pr180", "budget_review_pr180"
DDL_OWNER, RUNNER_ROLE = "starck", "smai_pr179_probe"
WORKING_DATABASE = "social_manager"
FORBIDDEN_SCHEMAS = ("public", "test_schema", "social_manager", "pg_catalog", "information_schema")
WORKDIR = ROOT / ".agent/private-budget-review" / RUN_ID
SCRIPT_PATH = "scripts/prepare_llm_budget_fixture.py"
RUNNER_PATH = "tests/acceptance/test_llm_budget_pg.py"
DEFINITION_PATHS = (
    "app/models/tenancy/tenant.py",
    "app/models/base.py",
    "migrations/versions/0093_add_llm_dispatch_receipts.py",
    "migrations/versions/0094_add_llm_outcome_events.py",
    "migrations/versions/0095_add_llm_budget_reservations.py",
)
FIXTURES = (
    ("fresh", "budget-review-7417a3ed-fresh", "pro", 1.0, True),
    ("race", "budget-review-7417a3ed-race", "pro", 1.0, True),
    ("replay", "budget-review-7417a3ed-replay", "pro", 1.0, True),
    ("other", "budget-review-7417a3ed-other", "pro", 1.0, True),
    ("cap", "budget-review-7417a3ed-cap", "pro", 1.0, True),
    ("missing", "budget-review-7417a3ed-missing", "pro", 1.0, True),
    ("frozen", "budget-review-7417a3ed-frozen", "pro", 1.0, True),
    ("blank", "budget-review-7417a3ed-blank", "pro", 1.0, True),
    ("old_day", "budget-review-7417a3ed-old-day", "pro", 1.0, True),
    ("historical", "budget-review-7417a3ed-historical", "pro", 1.0, True),
    ("free", "budget-review-7417a3ed-free", "pro", 1.0, True),
    ("undefined", "budget-review-7417a3ed-undefined", "pro", 1.0, True),
    ("unlimited", "budget-review-7417a3ed-unlimited", "business", 0.0, True),
    ("quantum", "budget-review-7417a3ed-quantum", "pro", 1.0, True),
    ("rollback_receipt", "budget-review-7417a3ed-rollback-receipt", "pro", 1.0, True),
    ("rollback_balance", "budget-review-7417a3ed-rollback-balance", "pro", 1.0, True),
    ("ack", "budget-review-7417a3ed-ack", "pro", 1.0, True),
    ("inactive", "budget-review-7417a3ed-inactive", "pro", 1.0, False),
)
TENANT_ID_BASE = 900001
FIXTURE_TENANT_IDS = {label: TENANT_ID_BASE + index for index, (label, *_rest) in enumerate(FIXTURES)}
DEFINED_RELATIONS = (
    "llm_budget_days",
    "llm_budget_reservations",
    "llm_dispatch_receipts",
    "llm_outcome_events",
    "tenants",
)
SEQUENCE_TABLES = DEFINED_RELATIONS
GUARD_FUNCTIONS = (
    "check_llm_budget_reservation_receipt",
    "check_llm_outcome_receipt",
    "reject_llm_budget_reservation_mutation",
    "reject_llm_outcome_mutation",
    "reject_llm_receipt_mutation",
)
GUARD_TRIGGERS = (
    "llm_budget_reservations.llm_budget_reservations_immutable",
    "llm_budget_reservations.llm_budget_reservations_no_truncate",
    "llm_budget_reservations.llm_budget_reservations_receipt_binding",
    "llm_dispatch_receipts.llm_dispatch_receipts_immutable",
    "llm_dispatch_receipts.llm_dispatch_receipts_no_truncate",
    "llm_outcome_events.llm_outcome_events_immutable",
    "llm_outcome_events.llm_outcome_events_no_truncate",
    "llm_outcome_events.llm_outcome_events_receipt_binding",
)
RUNNER_PRIVILEGES = (
    ("llm_budget_reservations", "SELECT"),
    ("llm_budget_reservations", "INSERT"),
    ("llm_budget_reservations", "UPDATE"),
    ("llm_budget_reservations", "DELETE"),
    ("llm_budget_reservations", "TRUNCATE"),
    ("llm_budget_days", "SELECT"),
    ("llm_budget_days", "INSERT"),
    ("llm_budget_days", "UPDATE"),
    ("llm_dispatch_receipts", "SELECT"),
    ("llm_dispatch_receipts", "INSERT"),
    ("llm_outcome_events", "SELECT"),
    ("tenants", "SELECT"),
    ("tenants", "UPDATE"),
)
BUDGET_TABLES = ("llm_dispatch_receipts", "llm_budget_days", "llm_budget_reservations")
PREPARATION_OPERATIONS = (
    "create-dedicated-schema",
    "create-reviewed-tenant-table-and-indexes",
    "apply-reviewed-0093-0094-0095-definitions",
    "grant-exact-runner-privileges",
    "insert-18-frozen-tenants",
    "retain-definitions-and-fixtures",
)
APPROVALS = {
    "preflight": "explicit-read-only-approved",
    "rehearsal": "explicit-definition-rehearsal-approved",
    "apply": "explicit-definition-commit-approved",
}


class VerificationFailure(AssertionError):
    """Fixed labels only; never database exception text."""


def require(value, label):
    if not value:
        raise VerificationFailure(label)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def git_bytes(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.DEVNULL)


def git(*args):
    return git_bytes(*args).decode().strip()


def content_hash(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def source_manifest():
    return {path: content_hash(path) for path in (SCRIPT_PATH, RUNNER_PATH, *DEFINITION_PATHS)}


def verify_script_pin(pin):
    require(type(pin) is str and re.fullmatch(r"[0-9a-f]{40}", pin), "preparation-commit-pin")
    require(git("cat-file", "-t", pin) == "commit", "preparation-pin-not-commit")
    require(git_bytes("show", f"{pin}:{SCRIPT_PATH}") == (ROOT / SCRIPT_PATH).read_bytes(), "preparation-content-drift")
    for path in DEFINITION_PATHS:
        require(git_bytes("show", f"{CODE_PIN}:{path}") == (ROOT / path).read_bytes(), "definition-content-drift")


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}, default=str), flush=True)


def check_window(plan):
    now = datetime.now(timezone.utc)
    start, end = (datetime.fromisoformat(plan[key]) for key in ("window_start", "window_end"))
    require(start.tzinfo is not None and end.tzinfo is not None, "window-timezone")
    require(start <= now < end and (end - now).total_seconds() >= 900, "outside-preparation-window")
    require(now.hour < 23 and now.date().isoformat() == plan["utc_day"], "utc-day-window")


def idle_processes():
    mine = str(os.getpid())
    for line in subprocess.check_output(["ps", "-Ao", "pid=,command="], text=True).splitlines():
        fields = line.strip().split(" ", 1)
        if len(fields) == 2 and fields[0] != mine:
            require(not any(x in fields[1] for x in ("pytest", "alembic", "prepare_llm_budget")), "competing-process")


def ensure_private_dir():
    require(not WORKDIR.is_symlink(), "artifact-directory-symlink")
    for directory in (ROOT / ".agent", WORKDIR.parent, WORKDIR):
        require(not directory.is_symlink(), "artifact-parent-symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
    require(WORKDIR.resolve().is_relative_to(ROOT.resolve()), "artifact-directory-boundary")
    require(
        WORKDIR.stat().st_mode & 0o077 == 0 and WORKDIR.parent.stat().st_mode & 0o077 == 0, "artifact-directory-mode"
    )


def read_private(path):
    path = Path(path)
    require(path.parent == WORKDIR and not path.is_symlink(), "artifact-path")
    ensure_private_dir()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_mode & 0o077 == 0, "artifact-mode")
        require(info.st_uid == os.getuid() and info.st_size <= 2_000_000, "artifact-owner-or-size")
        raw = handle.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def write_artifact(phase, value):
    require(re.fullmatch(r"[a-z-]+", phase), "artifact-phase")
    ensure_private_dir()
    path = WORKDIR / (phase + ".json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(json.dumps(value, sort_keys=True, default=str).encode())
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(WORKDIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_plan(plan, phase):
    require(plan["kind"] == "isolated-budget-preparation-approval", "preparation-approval-kind")
    require(
        plan["run_id"] == RUN_ID and plan["target"] == [TARGET_HOST, TARGET_PORT, TARGET_DATABASE, DDL_OWNER],
        "preparation-target",
    )
    require(plan["schema"] == TARGET_SCHEMA and plan["runner_role"] == RUNNER_ROLE, "preparation-schema-or-role")
    require(plan["operations"] == list(PREPARATION_OPERATIONS), "preparation-operations")
    require(plan["nonproduction"] is True and plan["exclusive_window"] is True, "preparation-owner-attestation")
    require(plan["owner_approval"].get(phase) == APPROVALS[phase], "phase-approval")
    require(
        plan["execution_sha"] == git("rev-parse", "HEAD") and not git("status", "--porcelain"), "expected-clean-head"
    )
    require(not git("diff", CODE_PIN, "HEAD", "--", "app", "migrations"), "application-drift")
    if phase != "preflight":
        require(type(plan["endpoint"]) is list and len(plan["endpoint"]) == 3, "write-endpoint-unmeasured")
    verify_script_pin(plan["preparation_sha"])
    require(plan["sources"] == source_manifest(), "preparation-sources")
    check_window(plan)


@contextmanager
def application_environment(url):
    """Install real Settings without dotenv or inherited provider/private config."""
    require(not any(x == "app" or x.startswith("app.") for x in sys.modules), "app-already-imported")
    from pydantic_settings.sources import DotEnvSettingsSource

    env = {
        "POSTGRES_URL": url,
        "DB_SCHEMA": TARGET_SCHEMA,
        "SECRET_KEY": "synthetic-preparation-not-for-auth",
        "DEBUG": "false",
        "ENVIRONMENT": "test",
        "AGENT_DAILY_COST_LIMIT": "0",
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch("dotenv.load_dotenv", return_value=False),
        patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}),
    ):
        yield


@contextmanager
def connect(role, *, readonly, url_input=None):
    """No user SQL before the owned transaction; read-only from startup options."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = make_url(url_input if url_input is not None else os.environ["BUDGET_PREP_URL"])
    require(url.drivername == "postgresql" and not url.query, "preparation-url-driver")
    require(
        [url.host, url.port or 5432, url.database, url.username] == [TARGET_HOST, TARGET_PORT, TARGET_DATABASE, role],
        "preparation-url",
    )
    options = "-c statement_timeout=10000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=15000"
    if readonly:
        options += " -c default_transaction_read_only=on"
    engine = create_engine(
        url,
        echo=False,
        pool_pre_ping=False,
        connect_args={
            "connect_timeout": 5,
            "options": options,
            "application_name": "budget-preparation-" + RUN_ID,
        },
    )
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def sql(connection, statement, parameters=None):
    from sqlalchemy import text

    return connection.execute(text(statement), parameters or {})


def qualified(connection, schema, name):
    require(
        re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name), "qualified-name"
    )
    quote = connection.dialect.identifier_preparer.quote
    return quote(schema) + "." + quote(name)


def sequence_binding(connection, table):
    row = sql(
        connection,
        "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.oid=pg_get_serial_sequence(:table,'id')::regclass",
        {"table": TARGET_SCHEMA + "." + table},
    ).one()
    require(tuple(row) == (TARGET_SCHEMA, table + "_id_seq"), "sequence-binding")
    return qualified(connection, row[0], row[1])


def role_floor(connection):
    for name in (DDL_OWNER, RUNNER_ROLE):
        row = sql(
            connection,
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit "
            "FROM pg_roles WHERE rolname=:name",
            {"name": name},
        ).one()
        require(row[0] is True and not any(row[1:6]), "privileged-or-nonlogin-role")
        if name == RUNNER_ROLE:
            require(row[6] is False, "runner-inherits")
            require(
                sql(
                    connection,
                    "SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=:name)",
                    {"name": name},
                ).scalar_one()
                == 0,
                "runner-membership",
            )


def loopback_server_address(value):
    """PostgreSQL inet::text may include a /32 or /128 host mask."""
    try:
        address = ipaddress.ip_interface(value)
    except ValueError:
        return False
    return str(address.ip) in ("127.0.0.1", "::1") and address.network.prefixlen == address.max_prefixlen


def identity_and_exclusivity(connection, *, readonly, role):
    row = sql(
        connection,
        "SELECT current_database(),current_user,inet_server_addr()::text,inet_server_port(),"
        "current_setting('transaction_read_only'),current_setting('session_replication_role')",
    ).one()
    require(row[0] == TARGET_DATABASE and row[1] == role and row[3] == TARGET_PORT, "server-target")
    require(loopback_server_address(row[2]) and row[5] == "origin", "server-address-or-replication")
    require(row[4] == ("on" if readonly else "off"), "transaction-mode")
    require(
        sql(
            connection,
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
            "AND backend_type='client backend' AND pid<>pg_backend_pid()",
        ).scalar_one()
        == 0,
        "other-database-clients",
    )
    role_floor(connection)
    require(
        sql(connection, "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").scalar_one()
        == DDL_OWNER,
        "database-owner",
    )
    return [row[0], row[2], row[3]]


def user_objects(connection):
    return [
        list(row)
        for row in sql(
            connection,
            "SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2",
        ).all()
    ]


def no_alembic(connection):
    require(
        sql(connection, "SELECT count(*) FROM pg_class WHERE relname='alembic_version'").scalar_one() == 0,
        "alembic-history-present",
    )


def empty_preflight(connection):
    sql(connection, "SET TRANSACTION READ ONLY")
    endpoint = identity_and_exclusivity(connection, readonly=True, role=DDL_OWNER)
    no_alembic(connection)
    require(user_objects(connection) == [], "database-not-empty")
    schemas = (
        sql(
            connection,
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
            "AND nspname NOT IN ('information_schema','public')",
        )
        .scalars()
        .all()
    )
    require(schemas == [], "unexpected-existing-schema")
    return {"endpoint": endpoint, "objects": [], "schema": "absent", "alembic": "absent"}


def catalog_signature(connection):
    # Extract a constant only; never import/execute the acceptance runner.
    tree = ast.parse((ROOT / RUNNER_PATH).read_text())
    value = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CATALOG_SQL" for t in node.targets)
    )
    rows = sorted([list(row) for row in sql(connection, value, {"schema": TARGET_SCHEMA}).all()])
    return {"sha256": digest(rows), "rows": rows}


def measure_acl(connection):
    measured, expected = {}, {}
    required = set(RUNNER_PRIVILEGES)
    for table in DEFINED_RELATIONS:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            key = "table:" + table + ":" + privilege
            measured[key] = sql(
                connection,
                "SELECT has_table_privilege(:role,:table,:privilege)",
                {
                    "role": RUNNER_ROLE,
                    "table": TARGET_SCHEMA + "." + table,
                    "privilege": privilege,
                },
            ).scalar_one()
            expected[key] = (table, privilege) in required
    for table in SEQUENCE_TABLES:
        for privilege in ("SELECT", "USAGE", "UPDATE"):
            key = "sequence:" + table + "_id_seq:" + privilege
            measured[key] = sql(
                connection,
                "SELECT has_sequence_privilege(:role,:name,:privilege)",
                {
                    "role": RUNNER_ROLE,
                    "name": TARGET_SCHEMA + "." + table + "_id_seq",
                    "privilege": privilege,
                },
            ).scalar_one()
            expected[key] = privilege in ("SELECT", "USAGE")
    for privilege in ("CONNECT", "CREATE", "TEMP"):
        key = "database:" + privilege
        measured[key] = sql(
            connection,
            "SELECT has_database_privilege(:role,current_database(),:privilege)",
            {
                "role": RUNNER_ROLE,
                "privilege": privilege,
            },
        ).scalar_one()
        expected[key] = privilege == "CONNECT"
    for privilege in ("USAGE", "CREATE"):
        key = "schema:" + privilege
        measured[key] = sql(
            connection,
            "SELECT has_schema_privilege(:role,:schema,:privilege)",
            {
                "role": RUNNER_ROLE,
                "schema": TARGET_SCHEMA,
                "privilege": privilege,
            },
        ).scalar_one()
        expected[key] = privilege == "USAGE"
    require(measured == expected, "runner-effective-acl")
    return measured


def inspect_prepared(connection):
    """Facts consumed by BOTH preparation and isolated acceptance, never by normal mode."""
    no_alembic(connection)
    schemas = (
        sql(
            connection,
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
            "AND nspname NOT IN ('information_schema','public') ORDER BY nspname",
        )
        .scalars()
        .all()
    )
    require(schemas == [TARGET_SCHEMA], "schema-scope")
    types = [
        list(row)
        for row in sql(
            connection,
            "SELECT n.nspname,t.typname,t.typtype,pg_get_userbyid(t.typowner) FROM pg_type t "
            "JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname NOT LIKE 'pg_%' "
            "AND n.nspname<>'information_schema' ORDER BY 1,2",
        ).all()
    ]
    expected_types = sorted(
        [
            [TARGET_SCHEMA, name, kind, DDL_OWNER]
            for table in DEFINED_RELATIONS
            for name, kind in ((table, "c"), ("_" + table, "b"))
        ]
    )
    require(types == expected_types, "type-scope-or-owner")
    objects = user_objects(connection)
    require(all(row[0] == TARGET_SCHEMA and row[3] == DDL_OWNER for row in objects), "object-schema-or-owner")
    require(sorted(row[1] for row in objects if row[2] == "r") == sorted(DEFINED_RELATIONS), "relation-scope")
    require(
        sorted(row[1] for row in objects if row[2] == "S") == sorted(t + "_id_seq" for t in SEQUENCE_TABLES),
        "sequence-scope",
    )
    require(all(row[2] in ("r", "S", "i") for row in objects), "extra-object-kind")
    require(
        sql(
            connection,
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=:schema",
            {"schema": TARGET_SCHEMA},
        ).scalar_one()
        == DDL_OWNER,
        "schema-owner",
    )
    require(
        sql(
            connection,
            "SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND a.attacl IS NOT NULL",
            {"schema": TARGET_SCHEMA},
        ).scalar_one()
        == 0,
        "extra-column-acl",
    )
    functions = [
        list(row)
        for row in sql(
            connection,
            "SELECT p.proname,pg_get_function_identity_arguments(p.oid),"
            "pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2",
        ).all()
    ]
    require([row[0] for row in functions] == sorted(GUARD_FUNCTIONS), "function-scope")
    require(
        all(row[1] == "" and row[2] == DDL_OWNER and row[3] is False and row[4] is None for row in functions),
        "function-owner-or-security",
    )
    triggers = [
        list(row)
        for row in sql(
            connection,
            "SELECT c.relname||'.'||t.tgname,t.tgenabled FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:schema AND NOT t.tgisinternal ORDER BY 1",
            {"schema": TARGET_SCHEMA},
        ).all()
    ]
    require(
        [row[0] for row in triggers] == sorted(GUARD_TRIGGERS) and all(row[1] == "O" for row in triggers),
        "enabled-guard-scope",
    )
    sequences = {}
    for table in SEQUENCE_TABLES:
        binding = sequence_binding(connection, table)
        shape = sql(
            connection,
            "SELECT seqincrement,seqcache,seqcycle FROM pg_sequence WHERE seqrelid=CAST(:name AS regclass)",
            {"name": TARGET_SCHEMA + "." + table + "_id_seq"},
        ).one()
        require(tuple(shape) == (1, 1, False), "sequence-configuration")
        sequences[table] = binding
    acl = measure_acl(connection)
    catalog = catalog_signature(connection)
    return {
        "types": types,
        "objects": objects,
        "functions": functions,
        "triggers": triggers,
        "sequences": sequences,
        "acl": acl,
        "acl_manifest_sha256": digest(acl),
        "catalog_sha256": catalog["sha256"],
        "catalog_rows": catalog["rows"],
    }


def load_revision(name):
    path = ROOT / "migrations/versions" / name
    spec = importlib.util.spec_from_file_location("_isolated_" + name[:4], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_definitions(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app.models.tenancy.tenant import Tenant

    require(Tenant.__table__.schema == TARGET_SCHEMA, "model-schema")
    sql(connection, 'CREATE SCHEMA "' + TARGET_SCHEMA + '" AUTHORIZATION "' + DDL_OWNER + '"')
    connection.execute(CreateTable(Tenant.__table__))
    for index in sorted(Tenant.__table__.indexes, key=lambda item: item.name):
        connection.execute(CreateIndex(index))
    for number, predecessor, name in (
        ("0093", "0092", "0093_add_llm_dispatch_receipts.py"),
        ("0094", "0093", "0094_add_llm_outcome_events.py"),
        ("0095", "0094", "0095_add_llm_budget_reservations.py"),
    ):
        module = load_revision(name)
        require(
            module.revision == number and module.down_revision == predecessor and module.schema == TARGET_SCHEMA,
            "definition-identity",
        )
        previous = module.op
        try:
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
        finally:
            module.op = previous


def apply_grants(connection):
    sql(connection, 'GRANT USAGE ON SCHEMA "' + TARGET_SCHEMA + '" TO "' + RUNNER_ROLE + '"')
    for table, privilege in RUNNER_PRIVILEGES:
        connection.exec_driver_sql(
            f'GRANT {privilege} ON {qualified(connection, TARGET_SCHEMA, table)} TO "{RUNNER_ROLE}"'
        )
    for table in SEQUENCE_TABLES:
        connection.exec_driver_sql(
            f'GRANT USAGE, SELECT ON SEQUENCE {sequence_binding(connection, table)} TO "{RUNNER_ROLE}"'
        )


def insert_fixtures(connection):
    for label, slug, plan, cap, active in FIXTURES:
        sql(
            connection,
            f'INSERT INTO {qualified(connection, TARGET_SCHEMA, "tenants")} '
            "(id,name,slug,plan,daily_cost_limit,is_active) VALUES (:id,:slug,:slug,:plan,:cap,:active)",
            {"id": FIXTURE_TENANT_IDS[label], "slug": slug, "plan": plan, "cap": cap, "active": active},
        )


def verify_fixture_rows(connection):
    rows = [
        list(row)
        for row in sql(
            connection,
            f"SELECT id,slug,plan,daily_cost_limit,is_active FROM "
            f'{qualified(connection, TARGET_SCHEMA, "tenants")} ORDER BY id',
        ).all()
    ]
    expected = [[FIXTURE_TENANT_IDS[label], slug, plan, cap, active] for label, slug, plan, cap, active in FIXTURES]
    require(rows == expected, "fixture-row-shape")
    for table in DEFINED_RELATIONS:
        if table != "tenants":
            require(
                sql(connection, f"SELECT count(*) FROM {qualified(connection, TARGET_SCHEMA, table)}").scalar_one()
                == 0,
                "unexpected-prepared-evidence",
            )
    return rows


def expect_sqlstate(connection, statement, parameters, expected):
    from sqlalchemy.exc import DBAPIError

    with connection.begin_nested() as savepoint:
        try:
            sql(connection, statement, parameters)
        except DBAPIError as error:
            require(
                getattr(error.orig, "sqlstate", None) == expected or getattr(error.orig, "pgcode", None) == expected,
                "guard-sqlstate",
            )
        else:
            raise VerificationFailure("guard-not-enforced")
        finally:
            savepoint.rollback()


def run_guard_probes(connection):
    """Rehearsal only: probe seeds AND each expected failure roll back; no commit."""
    receipt, days, reservations = (qualified(connection, TARGET_SCHEMA, t) for t in BUDGET_TABLES)
    params = {"tenant": FIXTURE_TENANT_IDS["fresh"], "day": datetime.now(timezone.utc).date(), "dispatch": "0" * 32}
    with connection.begin_nested() as seeds:
        try:
            sql(
                connection,
                f"INSERT INTO {receipt} (dispatch_id,tenant_id,prepared_at,utc_day,purpose,stage,tariff_state,"
                "llm_model_row_id,llm_provider_row_id,model_id,provider_name,pricing_policy_version,input_cost_per_1k,output_cost_per_1k) "
                "VALUES (:dispatch,:tenant,CAST(:day AS date)::timestamp AT TIME ZONE 'UTC',:day,'budget_review','probe','known',"
                "18001,18002,'budget-review-model','budget-review-provider','structured-provider-v1',0.001,0.002)",
                params,
            )
            sql(
                connection,
                f"INSERT INTO {days} (tenant_id,utc_day,settled_usd,reserved_usd,undefined_count,unbounded_count,frozen,baseline_version) "
                "VALUES (:tenant,:day,0,0,0,0,false,'reviewed-baseline-v1')",
                params,
            )
            insert = f"INSERT INTO {reservations} (tenant_id,utc_day,dispatch_id,ceiling_state,ceiling_usd,bound_version,undefined_policy_version,cap_usd) "
            values = "VALUES (:tenant,:day,:dispatch,'bounded',0.4,'reviewed-bound-v1','owner-undefined-allow-v1',1.0)"
            sql(connection, insert + values, params)
            for statement in (
                f"UPDATE {reservations} SET cap_usd=0.9",
                f"DELETE FROM {reservations}",
                f"TRUNCATE {reservations}",
            ):
                expect_sqlstate(connection, statement, params, "55000")
            expect_sqlstate(connection, insert + values, {**params, "dispatch": "1" * 32}, "23503")
        finally:
            seeds.rollback()
    return {
        "scope": "rollback-only-rehearsal",
        "probe_count": 4,
        "seed_rows_retained": 0,
        "sequence_note": "probe sequences newly created in rehearsal; enclosing rehearsal rolls them back",
    }


def verify_phase_record(record, plan, phase):
    require(record["phase"] == phase and record["run_id"] == RUN_ID, "phase-artifact-kind")
    require(
        record["execution_sha"] == plan["execution_sha"] and record["preparation_sha"] == plan["preparation_sha"],
        "phase-source-pin",
    )
    require(record["sources"] == source_manifest() and record["target"] == plan["target"], "phase-content-or-target")
    require(record["endpoint"] == plan["endpoint"], "phase-endpoint")


def base_record(plan, phase, endpoint):
    return {
        "phase": phase,
        "run_id": RUN_ID,
        "execution_sha": plan["execution_sha"],
        "preparation_sha": plan["preparation_sha"],
        "sources": source_manifest(),
        "target": plan["target"],
        "endpoint": endpoint,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def prepare(args, status):
    require(Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(), "project-venv-only")
    require(Path(sys.executable).parent == ROOT / ".venv/bin", "worktree-venv-entrypoint")
    plan, plan_hash = read_private(args.approval_file)
    validate_plan(plan, args.command)
    idle_processes()
    if args.command != "preflight":
        require(args.allow_definition_application, "definition-application-approval-flag")
    if args.command == "apply":
        require(args.allow_commit, "commit-approval")
    preflight = rehearsal = None
    if args.command in ("rehearsal", "apply"):
        preflight, preflight_hash = read_private(WORKDIR / "preflight.json")
        require(preflight_hash == plan["preflight_sha256"], "preflight-artifact-hash")
        verify_phase_record(preflight, plan, "preflight")
    if args.command == "apply":
        rehearsal, rehearsal_hash = read_private(WORKDIR / "rehearsal.json")
        require(rehearsal_hash == plan["rehearsal_sha256"], "rehearsal-artifact-hash")
        verify_phase_record(rehearsal, plan, "rehearsal")
        require(rehearsal["rolled_back"] is True and rehearsal["absence_verified"] is True, "rehearsal-incomplete")
        require(
            plan["catalog_parity_reviewed"] is True
            and rehearsal["prepared"]["catalog_sha256"] == plan["reviewed_catalog_sha256"],
            "rehearsal-catalog-review",
        )
    with connect(DDL_OWNER, readonly=True) as connection, connection.begin():
        before = empty_preflight(connection)
        if args.command != "preflight" or plan["endpoint"] is not None:
            require(before["endpoint"] == plan["endpoint"], "approved-server-endpoint")
    if args.command == "preflight":
        value = {**base_record(plan, "preflight", before["endpoint"]), "empty": before, "approval_sha256": plan_hash}
        write_artifact("preflight", value)
        return "PREFLIGHT_PASS"
    intent = {
        **base_record(plan, args.command + "-intent", before["endpoint"]),
        "approval_sha256": plan_hash,
        "preflight_sha256": plan["preflight_sha256"],
        "operations": list(PREPARATION_OPERATIONS),
    }
    write_artifact(args.command + "-intent", intent)
    url = os.environ["BUDGET_PREP_URL"]
    with application_environment(url), connect(DDL_OWNER, readonly=False, url_input=url) as connection:
        transaction = connection.begin()
        try:
            check_window(plan)
            require(
                identity_and_exclusivity(connection, readonly=False, role=DDL_OWNER) == before["endpoint"],
                "write-server-endpoint",
            )
            no_alembic(connection)
            require(user_objects(connection) == [], "write-target-not-empty")
            require(
                sql(
                    connection, "SELECT count(*) FROM pg_namespace WHERE nspname=:schema", {"schema": TARGET_SCHEMA}
                ).scalar_one()
                == 0,
                "write-schema-present",
            )
            apply_definitions(connection)
            apply_grants(connection)
            insert_fixtures(connection)
            state = inspect_prepared(connection)
            rows = verify_fixture_rows(connection)
            if args.command == "rehearsal":
                probes = run_guard_probes(connection)
                verify_fixture_rows(connection)
                transaction.rollback()
            else:
                require(state == rehearsal["prepared"], "catalog-or-acl-drift-from-rehearsal")
                write_artifact(
                    "apply-before-commit",
                    {**intent, "prepared": state, "fixtures": rows, "rehearsal_sha256": plan["rehearsal_sha256"]},
                )
                status["commit_attempted"] = True
                transaction.commit()
                status["commit_acknowledged"] = True
        finally:
            if transaction.is_active:
                transaction.rollback()
    if args.command == "rehearsal":
        with connect(DDL_OWNER, readonly=True) as connection, connection.begin():
            require(empty_preflight(connection) == before, "rehearsal-preservation")
        record = {
            **base_record(plan, "rehearsal", before["endpoint"]),
            "prepared": state,
            "fixtures": rows,
            "probes": probes,
            "rolled_back": True,
            "absence_verified": True,
            "approval_sha256": plan_hash,
        }
        write_artifact("rehearsal", record)
        return "REHEARSAL_PASS"
    write_artifact("apply-acknowledged", {**intent, "commit_acknowledged": True})
    with connect(DDL_OWNER, readonly=True) as connection, connection.begin():
        sql(connection, "SET TRANSACTION READ ONLY")
        require(
            identity_and_exclusivity(connection, readonly=True, role=DDL_OWNER) == before["endpoint"],
            "post-commit-endpoint",
        )
        after = inspect_prepared(connection)
        after_rows = verify_fixture_rows(connection)
        require(after == state and after_rows == rows, "post-commit-readback")
    record = {
        **base_record(plan, "apply", before["endpoint"]),
        "prepared": after,
        "fixtures": after_rows,
        "approval_sha256": plan_hash,
        "rehearsal_sha256": plan["rehearsal_sha256"],
        "commit_acknowledged": True,
        "catalog_parity_reviewed": plan["catalog_parity_reviewed"],
        "alembic": "absent",
    }
    write_artifact("apply", record)
    return "PREPARED"


def verify_runner_manifest(provenance, runner_approval):
    """Read real bytes and bind all immutable evidence, not just hash syntax."""
    verify_script_pin(provenance["preparation_script"])
    require(provenance["manifest_file"] == "apply.json", "preparation-manifest-path")
    manifest, actual_hash = read_private(WORKDIR / "apply.json")
    require(actual_hash == provenance["manifest_sha256"], "preparation-manifest-hash")
    require(
        manifest["phase"] == "apply" and manifest["commit_acknowledged"] is True and manifest["alembic"] == "absent",
        "preparation-not-committed",
    )
    require(
        manifest["sources"] == source_manifest() and manifest["execution_sha"] == runner_approval["runner_sha"],
        "manifest-source-drift",
    )
    require(manifest["preparation_sha"] == provenance["preparation_script"], "manifest-preparation-pin")
    require(manifest["target"] == [TARGET_HOST, TARGET_PORT, TARGET_DATABASE, DDL_OWNER], "manifest-target")
    require(
        runner_approval["target"] == [TARGET_HOST, TARGET_PORT, TARGET_DATABASE, RUNNER_ROLE]
        and runner_approval["schema"] == TARGET_SCHEMA,
        "runner-exact-target",
    )
    require(
        manifest["endpoint"] == [runner_approval["server_identity"][i] for i in (0, 2, 3)], "manifest-server-identity"
    )
    require(manifest["catalog_parity_reviewed"] is True, "manifest-catalog-unreviewed")
    require(manifest["run_id"] == RUN_ID and runner_approval["run_id"] == RUN_ID, "manifest-run-id")
    require(runner_approval["fixtures"] == FIXTURE_TENANT_IDS, "manifest-fixture-allocation")
    require(
        manifest["fixtures"]
        == [[FIXTURE_TENANT_IDS[label], slug, plan, cap, active] for label, slug, plan, cap, active in FIXTURES],
        "manifest-fixtures",
    )
    require(
        manifest["prepared"]["catalog_sha256"] == runner_approval["catalog_sha256"]
        and manifest["prepared"]["acl_manifest_sha256"] == runner_approval["acl_manifest_sha256"],
        "manifest-catalog-or-acl",
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=tuple(APPROVALS))
    parser.add_argument("--approval-file", required=True)
    parser.add_argument("--allow-definition-application", action="store_true")
    parser.add_argument("--allow-commit", action="store_true")
    args = parser.parse_args()
    status = {"commit_attempted": False, "commit_acknowledged": False}
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        emit("result", status=prepare(args, status))
        return 0
    except BaseException as error:
        emit(
            "stop",
            error_type=type(error).__name__,
            **status,
            reconciliation_required=args.command != "preflight",
            complete=False,
        )
        return 1
    finally:
        logging.disable(previous)


if __name__ == "__main__":
    raise SystemExit(main())
