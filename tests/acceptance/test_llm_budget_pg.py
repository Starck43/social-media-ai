"""Opt-in bootstrap-free acceptance; collection imports stdlib only.

Never run without separately approved target, catalog, fixtures and write plan.
No schema/reference bootstrap, migrations, cleanup, HTTP or provider calls.
"""

import asyncio
import hashlib
import importlib
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CODE_PIN = "9dd748cef465e09df74cc98c07ecc65748b41a8c"
D = Decimal
CASES = (
    "catalog",
    "fresh",
    "distinct_race",
    "identical_race_replay",
    "payer_isolation",
    "cap_serialization",
    "baseline_refusals",
    "historical_liability",
    "tariff_states",
    "rollback_after_receipt",
    "rollback_before_commit",
    "post_commit_ack_loss",
    "guards",
    "preservation",
)
FIXTURES = (
    "fresh",
    "race",
    "replay",
    "other",
    "cap",
    "missing",
    "frozen",
    "blank",
    "old_day",
    "historical",
    "free",
    "undefined",
    "unlimited",
    "quantum",
    "rollback_receipt",
    "rollback_balance",
    "ack",
    "inactive",
)
# Every possible inserted identity, including rollback/probe identities, is pinned.
IDENTITIES = (
    "fresh",
    "race_a",
    "race_b",
    "replay",
    "standalone",
    "other",
    "cap",
    "missing",
    "frozen",
    "blank",
    "old_day",
    "historical_seed",
    "historical_denied",
    "free",
    "undefined",
    "undefined_missing",
    "undefined_frozen",
    "undefined_inactive",
    "unlimited",
    "unbounded_denied",
    "quantum",
    "quantum_bad",
    "no_bound",
    "rollback_receipt",
    "rollback_balance",
    "ack",
    "invalid_missing",
    "invalid_payer",
    "invalid_day",
    "invalid_tariff",
)
OPERATIONS = (
    "insert-18-day-baselines",
    "owned-manager-admissions",
    "standalone-negative-fixture-receipt",
    "historical-liability-fixture-pair",
    "cap-fixture-update-to-0.5",
    "unlimited-fixture-update-to-pro-cap1",
    "rollback-only-reservation-update-delete-truncate",
    "rollback-only-invalid-binding-inserts",
)
RETAINED = (
    "fresh",
    "replay",
    "standalone",
    "other",
    "historical_seed",
    "free",
    "undefined",
    "unlimited",
    "quantum",
    "ack",
)
CATALOG_SQL = """
SELECT 'schema', n.nspname, jsonb_build_array(n.nspowner::regrole::text,n.nspacl)::text
FROM pg_catalog.pg_namespace n WHERE n.nspname=:schema
UNION ALL SELECT 'class', c.relname, jsonb_build_array(c.relkind,c.relowner::regrole::text,c.relacl)::text
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname=:schema
UNION ALL SELECT 'column', c.relname||'.'||a.attname,
jsonb_build_array(a.attnum,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated)::text
FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
WHERE n.nspname=:schema AND a.attnum>0 AND NOT a.attisdropped
UNION ALL SELECT 'constraint', c.relname||'.'||k.conname,
jsonb_build_array(pg_catalog.pg_get_constraintdef(k.oid,true),k.convalidated,k.condeferrable,k.condeferred)::text
FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema
UNION ALL SELECT 'index', c.relname, jsonb_build_array(pg_catalog.pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready,i.indislive)::text
FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema
UNION ALL SELECT 'trigger', c.relname||'.'||t.tgname,
jsonb_build_array(t.tgenabled,pg_catalog.pg_get_triggerdef(t.oid,true))::text
FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND NOT t.tgisinternal
UNION ALL SELECT 'function', p.proname||'('||pg_catalog.pg_get_function_identity_arguments(p.oid)||')',
jsonb_build_array(pg_catalog.pg_get_functiondef(p.oid),p.proowner::regrole::text,p.proacl)::text
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname=:schema AND p.prokind='f'
UNION ALL SELECT 'sequence', c.relname,
jsonb_build_array(s.seqtypid::regtype::text,s.seqstart,s.seqincrement,s.seqmin,s.seqmax,s.seqcache,s.seqcycle)::text
FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema
"""


class Blocked(RuntimeError):
    """Safe label only; database exception text must never reach pytest."""


class Injected(RuntimeError):
    pass


def demand(value, label):
    if not value:
        raise Blocked(label)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.DEVNULL).decode().strip()


class BudgetPackage:
    async def run(self):
        """One sequential package; scenario results live in the private manifest."""
        self.journal = None
        self.previous_logging_disable = logging.root.manager.disable
        self.db = None
        self.tasks = set()
        try:
            self.load_approval()
            self.install_application()
            await self.verify_target()
            self.before = await self.protected()
            self.sequences_before = await self.sequences()
            self.emit(
                "baseline",
                protected=self.before,
                sequences=self.sequences_before,
                fixtures=self.cfg["fixtures"],
                ids=self.ids,
            )
            await self.verify_empty_fixtures()
            self.emit("scenario", name="catalog", result="PASS")
            await self.provision_days()
            for name in CASES[1:-1]:
                await asyncio.wait_for(getattr(self, name)(), timeout=45)
                self.emit("scenario", name=name, result="PASS")
            await self.preservation()
            self.emit("scenario", name="preservation", result="PASS")
            self.emit("completed", result="PASS", limitation="synthetic acknowledgement loss; no HTTP/settlement")
        except BaseException as error:
            if self.journal is not None:
                self.emit("stop", error_type=type(error).__name__, reconciliation_required=True)
            # No exception repr, SQL, URL, parameters or credential-bearing chain.
            raise Blocked("STOP: private manifest and separately approved read-only reconciliation required") from None
        finally:
            for task in self.tasks:
                task.cancel()
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            if self.db is not None:
                await self.db.async_engine.dispose()
                self.db.engine.dispose()
            if self.journal is not None:
                self.journal.close()
            logging.disable(self.previous_logging_disable)

    def load_approval(self):
        demand(Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(), "project-venv-only")
        demand(Path(sys.executable).parent == ROOT / ".venv/bin", "worktree-venv-entrypoint")
        demand(os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1", "plugin-autoload-not-disabled")
        demand(not os.environ.get("PYTEST_PLUGINS"), "external-plugins")
        demand(not any(name == "app" or name.startswith("app.") for name in sys.modules), "app-already-imported")
        demand(not git("status", "--porcelain"), "dirty-worktree")
        path = Path(os.environ["BUDGET_PG_APPROVAL_FILE"]).resolve()
        demand(path.is_relative_to(ROOT / ".agent/private-budget-review"), "approval-path")
        demand(path.stat().st_mode & 0o077 == 0, "approval-not-private")
        demand(path.parent.stat().st_mode & 0o077 == 0, "artifact-directory-not-private")
        self.cfg = json.loads(path.read_text())
        c = self.cfg
        demand(c["write_approval"] == "explicit-owner-approved" and c["nonproduction"] is True, "no-write-approval")
        demand(c["exclusive_window"] is True and c["gate_a_complete"] is True, "window-or-gate-a")
        demand(c["runner_sha"] == git("rev-parse", "HEAD"), "runner-sha")
        demand(not git("diff", CODE_PIN, "HEAD", "--", "app", "migrations"), "application-drift")
        demand(c["scenario_ids"] == list(CASES), "scenario-list")
        demand(
            type(c["target_label"]) is str and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", c["target_label"]),
            "safe-target-label",
        )
        demand(c["synthetic_model_id"] > 0 and c["synthetic_provider_id"] > 0, "synthetic-catalog-identities")
        demand(set(c["fixtures"]) == set(FIXTURES), "fixture-list")
        demand(len(set(c["fixtures"].values())) == len(FIXTURES), "shared-fixture")
        demand(all(type(i) is int and 0 < i <= 2147483647 for i in c["fixtures"].values()), "fixture-id")
        demand(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", c["schema"]), "schema")
        demand(c["schema"] not in ("public", "test_schema"), "historical-or-working-schema")
        demand(re.fullmatch(r"[0-9a-f]{32}", c["run_id"]), "run-id")
        self.ids = {key: hashlib.sha256((c["run_id"] + ":" + key).encode()).hexdigest()[:32] for key in IDENTITIES}
        demand(c["dispatch_ids"] == self.ids, "dispatch-identities")
        demand(
            c["retained_receipts"] == 11 and c["retained_reservations"] == 10 and c["retained_days"] == 18,
            "retained-ceiling",
        )
        demand(c["ddl_operations"] == [], "ddl-not-supported")
        demand(c["operations"] == list(OPERATIONS), "operation-list")
        demand(c["gate_b_accepted"] is True and c["report_isolation_verified"] is True, "prior-gates")
        demand(not os.environ.get("PYTEST_ADDOPTS"), "environment-addopts")
        self.day = datetime.fromisoformat(c["utc_day"]).date()
        self.check_day()
        demand(datetime.now(timezone.utc).hour < 23, "day-transition-window")
        start = datetime.fromisoformat(c["window_start"])
        self.deadline = datetime.fromisoformat(c["window_end"])
        now = datetime.now(timezone.utc)
        demand(start.tzinfo and self.deadline.tzinfo and start <= now < self.deadline, "outside-window")
        demand((self.deadline - now).total_seconds() >= 900, "window-too-short")
        self.hash_key = os.environ["BUDGET_PG_MANIFEST_KEY"]
        demand(len(self.hash_key) >= 32, "manifest-key")
        out = path.parent / (c["run_id"] + ".jsonl")
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.journal = os.fdopen(fd, "w")
        logging.disable(logging.CRITICAL)
        self.emit(
            "approval",
            approval_digest=digest(c),
            runner_sha=c["runner_sha"],
            code_sha=CODE_PIN,
            scenarios=CASES,
            catalog=c["catalog_sha256"],
        )

    def install_application(self):
        from sqlalchemy.engine import make_url

        url = make_url(os.environ["BUDGET_PG_URL"])
        c = self.cfg
        demand(url.drivername == "postgresql" and not url.query, "url-driver-or-query")
        demand([url.host, url.port or 5432, url.database, url.username] == c["target"], "target-tuple")
        demand(c["target"] not in c["forbidden_targets"], "forbidden-target")
        demand(c["forbidden_targets"] and c["target_owner_attestation"], "owner-target-attestation")
        demand(c["runner_role"] == url.username and c["ddl_role"] != url.username, "role-separation")
        # Suppress BOTH dotenv entry points while loading the real Settings class.
        # No test doubles for models/managers/transactions. No private .env reads.
        environment = {
            "POSTGRES_URL": url.render_as_string(hide_password=False),
            "DB_SCHEMA": c["schema"],
            "SECRET_KEY": "acceptance-only-not-an-authentication-service",
            "DEBUG": "false",
            "ENVIRONMENT": "test",
            "AGENT_DAILY_COST_LIMIT": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        import sqlalchemy.ext.asyncio as sa_async
        from pydantic_settings.sources import DotEnvSettingsSource

        real_create = sa_async.create_async_engine

        def create_with_review_options(*args, **kwargs):
            demand("connect_args" not in kwargs, "unexpected-engine-connect-options")
            kwargs["connect_args"] = {
                "server_settings": {
                    "statement_timeout": "10000",
                    "lock_timeout": "5000",
                    "idle_in_transaction_session_timeout": "15000",
                    "application_name": "budget-review-" + c["run_id"],
                }
            }
            return real_create(*args, **kwargs)

        with (
            patch.dict(os.environ, environment, clear=True),
            patch("dotenv.load_dotenv", return_value=False),
            patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}),
            patch.object(sa_async, "create_async_engine", create_with_review_options),
        ):
            self.settings = importlib.import_module("app.core.config").settings
            importlib.import_module("app.models")
            self.db = importlib.import_module("app.core.database")
            self.budget_module = importlib.import_module("app.models.managers.llm_budget_manager")
            self.receipt_module = importlib.import_module("app.models.managers.llm_dispatch_receipt_manager")
            self.policy = importlib.import_module("app.services.ai.budget_admission")
            self.receipts = importlib.import_module("app.services.ai.dispatch_receipts")
            self.context = importlib.import_module("app.core.tenant_context")
        from sqlalchemy import text

        from app.models.analysis.llm_budget import LLMBudgetDay, LLMBudgetReservation
        from app.models.analysis.llm_dispatch_receipt import LLMDispatchReceipt
        from app.models.tenancy.tenant import Tenant

        self.text, self.Day, self.Reservation, self.Receipt, self.Tenant = (
            text,
            LLMBudgetDay,
            LLMBudgetReservation,
            LLMDispatchReceipt,
            Tenant,
        )
        self.manager = self.budget_module.LLMBudgetManager()
        self.emit("imports", real_models=True, real_owned_session=True, process_global_cap="0", debug=False)

    def check_day(self):
        demand(datetime.now(timezone.utc).date() == self.day, "utc-day-changed")
        if hasattr(self, "deadline"):
            demand(datetime.now(timezone.utc) < self.deadline, "window-ended")

    def emit(self, kind, **values):
        self.journal.write(
            json.dumps({"kind": kind, "at": datetime.now(timezone.utc).isoformat(), **values}, default=str) + "\n"
        )
        self.journal.flush()
        os.fsync(self.journal.fileno())

    @asynccontextmanager
    async def session(self, readonly=True):
        self.check_day()
        async with self.db.async_session_maker() as session:
            try:
                # Transaction-scoped read-only before ANY SELECT.
                if readonly:
                    await session.execute(self.text("SET TRANSACTION READ ONLY"))
                yield session
            finally:
                await session.rollback()

    async def verify_target(self):
        async with self.session() as s:
            row = (
                await s.execute(
                    self.text(
                        "SELECT current_database(),current_user,inet_server_addr()::text,"
                        "inet_server_port(),current_setting('transaction_read_only'),"
                        "current_setting('session_replication_role')"
                    )
                )
            ).one()
            demand(list(row[:4]) == self.cfg["server_identity"], "server-identity")
            demand(tuple(row[4:]) == ("on", "origin"), "readonly-or-replication-role")
            role = (
                await s.execute(
                    self.text(
                        "SELECT rolsuper,rolbypassrls,rolcreaterole FROM pg_catalog.pg_roles WHERE rolname=current_user"
                    )
                )
            ).one()
            demand(tuple(role) == (False, False, False), "privileged-runner-role")
            others = (
                await s.execute(
                    self.text(
                        "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                        "WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid() "
                        "AND application_name IS DISTINCT FROM :name"
                    ),
                    {"name": "budget-review-" + self.cfg["run_id"]},
                )
            ).scalar_one()
            demand(others == 0, "other-database-clients")
            signature = digest(
                sorted(
                    [
                        list(row)
                        for row in (await s.execute(self.text(CATALOG_SQL), {"schema": self.cfg["schema"]})).all()
                    ]
                )
            )
            demand(signature == self.cfg["catalog_sha256"], "catalog-mismatch")
            # Gate B must supply EXACT ORM/0095 parity reviewed catalog digest,
            # not merely an arbitrary current-catalog hash.
            demand(self.cfg["catalog_parity_reviewed"] is True, "catalog-parity-unreviewed")
            q = 'SELECT version_num FROM "' + self.cfg["revision_schema"] + '".alembic_version ORDER BY version_num'
            demand(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.cfg["revision_schema"]), "revision-schema")
            demand((await s.execute(self.text(q))).scalars().all() == ["0095"], "revision-mismatch")
            demand(self.cfg["revision_rows"] == ["0095"], "approved-revision-mismatch")
        self.emit("target", safe_label=self.cfg["target_label"], catalog=signature, revision=["0095"])

    async def protected(self):
        result = {}
        async with self.session() as s:
            names = (
                (
                    await s.execute(
                        self.text(
                            "SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
                            "ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relkind IN ('r','p') ORDER BY c.relname"
                        ),
                        {"schema": self.cfg["schema"]},
                    )
                )
                .scalars()
                .all()
            )
            quote = self.db.async_engine.dialect.identifier_preparer.quote
            for name in names:
                qualified = quote(self.cfg["schema"]) + "." + quote(name)
                exclude, params = "", {"key": self.hash_key}
                if name in ("llm_dispatch_receipts", "llm_budget_reservations"):
                    exclude = " WHERE NOT (r.dispatch_id = ANY(:ids))"
                    params["ids"] = list(self.ids.values())
                elif name == "llm_budget_days":
                    exclude = " WHERE NOT (r.tenant_id = ANY(:tenants))"
                    params["tenants"] = list(self.cfg["fixtures"].values())
                elif name == "tenants":
                    # Two explicit synthetic mutable fixtures, checked separately.
                    exclude = " WHERE NOT (r.id = ANY(:mutable))"
                    params["mutable"] = [self.cfg["fixtures"][k] for k in ("cap", "unlimited")]
                query = (
                    "SELECT encode(sha256(convert_to(:key || row_to_json(r)::text,'UTF8')),'hex') FROM "
                    + qualified
                    + " r"
                    + exclude
                )
                hashes = sorted((await s.execute(self.text(query), params)).scalars().all())
                result[name] = {"rows": len(hashes), "sha256": digest(hashes)}
        return result

    async def sequences(self):
        result = {}
        quote = self.db.async_engine.dialect.identifier_preparer.quote
        async with self.session() as s:
            names = (
                await s.execute(
                    self.text(
                        "SELECT c.relname,q.seqincrement,q.seqcache FROM pg_catalog.pg_class c "
                        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_sequence q ON q.seqrelid=c.oid "
                        "WHERE n.nspname=:schema ORDER BY c.relname"
                    ),
                    {"schema": self.cfg["schema"]},
                )
            ).all()
            for name, increment, cache in names:
                values = (
                    await s.execute(
                        self.text("SELECT last_value,is_called FROM " + quote(self.cfg["schema"]) + "." + quote(name))
                    )
                ).one()
                result[name] = {"last_value": values[0], "is_called": values[1], "increment": increment, "cache": cache}
        return result

    async def verify_empty_fixtures(self):
        from sqlalchemy import select

        async with self.session() as s:
            for table, expected_name in self.cfg["sequence_names"].items():
                demand(
                    table in ("llm_dispatch_receipts", "llm_budget_reservations", "llm_budget_days"), "sequence-table"
                )
                sequence = (
                    await s.execute(
                        self.text("SELECT pg_get_serial_sequence(:name,'id')"),
                        {"name": self.cfg["schema"] + "." + table},
                    )
                ).scalar_one()
                demand(sequence == self.cfg["schema"] + "." + expected_name, "sequence-binding")
                demand(
                    self.sequences_before[expected_name]["increment"] == 1
                    and self.sequences_before[expected_name]["cache"] == 1,
                    "sequence-cache-or-increment",
                )
            demand(len(self.cfg["sequence_names"]) == 3, "sequence-list")
            for label, tid in self.cfg["fixtures"].items():
                tenant = (await s.execute(select(self.Tenant).where(self.Tenant.id == tid))).scalar_one()
                demand(
                    tenant.slug == self.cfg["fixture_slugs"][label] and tenant.slug.startswith("budget-review-"),
                    "fixture-ownership",
                )
                demand(
                    tenant.is_active == (label != "inactive")
                    and tenant.daily_cost_limit == (0 if label == "unlimited" else 1),
                    "fixture-cap-active",
                )
                demand(tenant.plan == ("business" if label == "unlimited" else "pro"), "fixture-plan")
                for model in (self.Day, self.Reservation, self.Receipt):
                    demand(
                        (await s.execute(select(model.id).where(model.tenant_id == tid).limit(1))).first() is None,
                        "older-fixture-evidence",
                    )
                if label in ("cap", "unlimited"):
                    if not hasattr(self, "mutable_before"):
                        self.mutable_before = {}
                    self.mutable_before[label] = {
                        col.name: getattr(tenant, col.name) for col in self.Tenant.__table__.columns
                    }
            for model in (self.Reservation, self.Receipt):
                demand(
                    (await s.execute(select(model.id).where(model.dispatch_id.in_(self.ids.values())).limit(1))).first()
                    is None,
                    "run-identities-exist",
                )

    async def provision_days(self):
        self.emit("write_intent", operation="insert-approved-day-baselines", count=18)
        async with self.session(False) as s:
            for label, tid in self.cfg["fixtures"].items():
                if label == "missing":
                    continue
                s.add(
                    self.Day(
                        tenant_id=tid,
                        utc_day=self.day - timedelta(days=int(label == "old_day")),
                        settled_usd=D("1") if label == "undefined" else D("0.1") if label == "fresh" else D(0),
                        reserved_usd=D(0),
                        undefined_count=0,
                        unbounded_count=0,
                        frozen=label == "frozen",
                        baseline_version="" if label == "blank" else "reviewed-baseline-v1",
                    )
                )
            s.add(
                self.Day(
                    tenant_id=self.cfg["fixtures"]["historical"],
                    utc_day=self.day - timedelta(days=1),
                    settled_usd=D(0),
                    reserved_usd=D("0.1"),
                    undefined_count=0,
                    unbounded_count=0,
                    frozen=False,
                    baseline_version="reviewed-baseline-v1",
                )
            )
            await s.commit()
        await self.snapshot("days-provisioned")

    def receipt(self, identity, fixture, state="known", historical=False):
        model = SimpleNamespace(
            id=self.cfg["synthetic_model_id"],
            model_id="budget-review-model",
            provider=SimpleNamespace(id=self.cfg["synthetic_provider_id"], name="budget-review-provider"),
            input_cost_per_1k=None if state == "undefined" else D(0) if state == "free" else D("0.001"),
            output_cost_per_1k=None if state == "undefined" else D(0) if state == "free" else D("0.002"),
        )
        prepared = datetime.combine(self.day - timedelta(days=int(historical)), datetime.min.time(), timezone.utc)
        return self.receipts.build_dispatch_receipt(
            model,
            tenant_id=self.cfg["fixtures"][fixture],
            purpose="budget_review",
            stage="acceptance",
            dispatch_id=self.ids[identity],
            prepared_at=prepared,
        )

    async def admit(self, record, amount=D("0.4"), scope=None, manager=None):
        self.check_day()
        self.emit("admission_intent", dispatch_id=record["dispatch_id"], ceiling=amount)
        with self.context.tenant_scope(record["tenant_id"] if scope is None else scope):
            return await (manager or self.manager).reserve_receipt(
                record,
                ceiling_usd=amount,
                bound_version="reviewed-bound-v1" if record["tariff_state"] == "known" and amount is not None else None,
            )

    async def denied(self, record, amount=D("0.4"), scope=None, *, expected):
        """Require the scenario's refusal class, not an unrelated setup failure."""
        try:
            await self.admit(record, amount, scope)
        except expected as error:
            self.emit("denial", dispatch_id=record["dispatch_id"], error_type=type(error).__name__)
            return
        raise Blocked("expected-denial")

    async def snapshot(self, label):
        from sqlalchemy import select

        async with self.session() as s:
            result = {}
            for name, model in (("receipts", self.Receipt), ("reservations", self.Reservation), ("days", self.Day)):
                rows = (
                    (await s.execute(select(model.__table__).where(model.tenant_id.in_(self.cfg["fixtures"].values()))))
                    .mappings()
                    .all()
                )
                # Synthetic rows only. Includes actual serial IDs for retained evidence.
                result[name] = [dict(row) for row in rows]
        self.emit("retained_snapshot", label=label, **result)
        return result

    async def balances(self, fixture, reserved, undefined=0, unbounded=0):
        from sqlalchemy import select

        async with self.session() as s:
            row = (
                await s.execute(
                    select(self.Day).where(
                        self.Day.tenant_id == self.cfg["fixtures"][fixture], self.Day.utc_day == self.day
                    )
                )
            ).scalar_one()
            demand(
                (row.reserved_usd, row.undefined_count, row.unbounded_count) == (D(reserved), undefined, unbounded),
                "balance-mismatch",
            )

    async def fresh(self):
        demand(await self.admit(self.receipt("fresh", "fresh")) is True, "fresh-ack")
        rows = await self.snapshot("fresh")
        reservation = next(r for r in rows["reservations"] if r["dispatch_id"] == self.ids["fresh"])
        demand(
            reservation["cap_usd"] == D(1)
            and reservation["ceiling_usd"] == D("0.4")
            and reservation["bound_version"] == "reviewed-bound-v1",
            "fresh-snapshot",
        )
        await self.balances("fresh", "0.4")
        day = next(r for r in rows["days"] if r["tenant_id"] == self.cfg["fixtures"]["fresh"])
        demand(
            day["settled_usd"] == D("0.1") and day["baseline_version"] == "reviewed-baseline-v1", "baseline-snapshot"
        )

    async def race(self, records):
        # Two real owned transactions each finish guard queries, then pause just
        # before the payer lock. Neither is an externally supplied admission session.
        base = self.budget_module.LLMBudgetManager
        ready = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        class Racing(base):
            def __init__(self, index):
                super().__init__()
                self.index = index

            @asynccontextmanager
            async def _get_owned_session(self):
                async with super()._get_owned_session() as session:
                    original = session.execute

                    async def execute(statement, *args, **kwargs):
                        if getattr(statement, "_for_update_arg", None) is not None and not ready[self.index].is_set():
                            ready[self.index].set()
                            await release.wait()
                        return await original(statement, *args, **kwargs)

                    with patch.object(session, "execute", execute):
                        yield session

        async def one(index):
            try:
                return await self.admit(records[index], D("0.6"), manager=Racing(index))
            except self.policy.BudgetAdmissionDenied:
                return "denied"

        tasks = [asyncio.create_task(one(i)) for i in range(2)]
        self.tasks.update(tasks)
        try:
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in ready)), 10)
            release.set()
            values = await asyncio.wait_for(asyncio.gather(*tasks), 20)
            self.emit("race", identities=[r["dispatch_id"] for r in records], outcomes=values)
            return values
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.tasks.difference_update(tasks)

    async def distinct_race(self):
        values = await self.race([self.receipt("race_a", "race"), self.receipt("race_b", "race")])
        demand(values.count(True) == 1 and values.count("denied") == 1, "distinct-race")
        await self.balances("race", "0.6")
        await self.snapshot("distinct-race")

    async def identical_race_replay(self):
        record = self.receipt("replay", "replay")
        values = await self.race([record, record])
        demand(values.count(True) == 1 and values.count(False) == 1, "identical-race")
        demand(await self.admit(record, D("0.6")) is False, "replay-is-not-permission")
        await self.denied(record, D("0.5"), expected=self.policy.BudgetAdmissionConflict)
        await self.denied(
            {**record, "stage": "changed"}, D("0.6"), expected=self.receipt_module.ReceiptEvidenceConflict
        )
        await self.denied(
            {**record, "tenant_id": self.cfg["fixtures"]["other"]},
            D("0.6"),
            expected=self.receipt_module.ReceiptEvidenceConflict,
        )
        with self.context.tenant_scope(record["tenant_id"]):
            try:
                await self.manager.reserve_receipt(record, ceiling_usd=D("0.6"), bound_version="changed-v1")
            except self.policy.BudgetAdmissionConflict:
                pass
            else:
                raise Blocked("bound-version-replay")
        standalone = self.receipt("standalone", "replay")
        self.emit("write_intent", operation="standalone-negative-fixture", dispatch_id=standalone["dispatch_id"])
        with self.context.tenant_scope(standalone["tenant_id"]):
            demand(
                await self.receipt_module.LLMDispatchReceiptManager().record_receipt(standalone), "standalone-fixture"
            )
        await self.denied(standalone, D("0.1"), expected=self.policy.BudgetAdmissionConflict)
        await self.balances("replay", "0.6")

    async def payer_isolation(self):
        record = self.receipt("other", "other")
        await self.denied(
            record, scope=self.cfg["fixtures"]["fresh"], expected=self.context.TenantContextError
        )
        demand(await self.admit(record, D("0.6")) is True, "payer-isolation")
        await self.balances("other", "0.6")

    async def cap_serialization(self):
        from sqlalchemy import select

        # Observe the admission actually blocked on this writer's tenant lock;
        # then commit the new cap so the locking reader must resolve it fresh.
        ready, release = asyncio.Event(), asyncio.Event()
        waiter_pid = {}
        outer = self
        base = self.budget_module.LLMBudgetManager

        class Queued(base):
            @asynccontextmanager
            async def _get_owned_session(self):
                async with super()._get_owned_session() as s:
                    original = s.execute

                    async def execute(statement, *args, **kwargs):
                        if getattr(statement, "_for_update_arg", None) is not None and not ready.is_set():
                            waiter_pid["value"] = (await original(outer.text("SELECT pg_backend_pid()"))).scalar_one()
                            ready.set()
                            await release.wait()
                        return await original(statement, *args, **kwargs)

                    with patch.object(s, "execute", execute):
                        yield s

        record = self.receipt("cap", "cap")
        async with self.session(False) as writer:
            tenant = (
                await writer.execute(select(self.Tenant).where(self.Tenant.id == record["tenant_id"]).with_for_update())
            ).scalar_one()
            writer_pid = (await writer.execute(self.text("SELECT pg_backend_pid()"))).scalar_one()
            task = asyncio.create_task(self.admit(record, D("0.6"), manager=Queued()))
            self.tasks.add(task)
            try:
                await asyncio.wait_for(ready.wait(), 10)
                release.set()

                async def observe_blocking():
                    async with self.session() as observer:
                        while True:
                            blockers = (
                                await observer.execute(
                                    self.text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter_pid["value"]}
                                )
                            ).scalar_one()
                            if writer_pid in blockers:
                                return
                            await asyncio.sleep(0.05)

                await asyncio.wait_for(observe_blocking(), 3)
                self.emit("serialization", queued_payer_lock_observed=True)
                tenant.daily_cost_limit = 0.5
                self.emit("write_intent", operation="cap-fixture-only", cap="0.5")
                await writer.commit()
                try:
                    await asyncio.wait_for(task, 15)
                except self.policy.BudgetAdmissionDenied:
                    pass
                else:
                    raise Blocked("stale-cap")
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.tasks.discard(task)
        await self.balances("cap", "0")

    async def baseline_refusals(self):
        for key in ("missing", "frozen", "blank"):
            await self.denied(self.receipt(key, key), expected=self.policy.BudgetAdmissionUnavailable)
        await self.denied(
            self.receipt("old_day", "old_day", historical=True), expected=self.policy.BudgetAdmissionDenied
        )

    async def historical_liability(self):
        # Explicit old-day immutable pair fixture, NOT fresh admission evidence.
        record = self.receipt("historical_seed", "historical", historical=True)
        self.emit(
            "write_intent", operation="historical-liability-fixture-not-admission", dispatch_id=record["dispatch_id"]
        )
        async with self.session(False) as s:
            s.add(self.Receipt(**record))
            await s.flush()
            s.add(
                self.Reservation(
                    dispatch_id=record["dispatch_id"],
                    tenant_id=record["tenant_id"],
                    utc_day=record["utc_day"],
                    ceiling_state="bounded",
                    ceiling_usd=D("0.1"),
                    bound_version="reviewed-bound-v1",
                    cap_usd=D(1),
                    undefined_policy_version="owner-undefined-allow-v1",
                )
            )
            await s.commit()
        await self.denied(
            self.receipt("historical_denied", "historical"), expected=self.policy.BudgetAdmissionDenied
        )
        await self.balances("historical", "0")

    async def tariff_states(self):
        from sqlalchemy import select

        demand(await self.admit(self.receipt("free", "free", "free"), D(0)) is True, "free")
        demand(await self.admit(self.receipt("undefined", "undefined", "undefined"), D(0)) is True, "undefined")
        await self.balances("undefined", "0", undefined=1)
        for fixture in ("missing", "frozen"):
            await self.denied(
                self.receipt("undefined_" + fixture, fixture, "undefined"),
                D(0),
                expected=self.policy.BudgetAdmissionUnavailable,
            )
        await self.denied(
            self.receipt("undefined_inactive", "inactive", "undefined"), D(0), expected=self.context.TenantContextError
        )
        await self.denied(
            self.receipt("no_bound", "inactive"), D("0.1"), expected=self.context.TenantContextError
        )
        demand(await self.admit(self.receipt("unlimited", "unlimited"), None) is True, "unlimited")
        await self.balances("unlimited", "0", unbounded=1)
        await self.denied(
            self.receipt("no_bound", "fresh"), None, expected=self.policy.BudgetAdmissionDenied
        )
        demand(await self.admit(self.receipt("quantum", "quantum"), D("0.000000001")) is True, "quantum")
        await self.denied(self.receipt("quantum_bad", "quantum"), D("0.0000000001"), expected=ValueError)
        await self.balances("quantum", "0.000000001")
        # Explicit fixture-only cap/plan change; current unbounded liability
        # remains immutable and must deny known capped admission.
        async with self.session(False) as s:
            tenant = (
                await s.execute(
                    select(self.Tenant).where(self.Tenant.id == self.cfg["fixtures"]["unlimited"]).with_for_update()
                )
            ).scalar_one()
            tenant.plan = "pro"
            tenant.daily_cost_limit = 1.0
            self.emit("write_intent", operation="unlimited-fixture-to-capped", plan="pro", cap="1.0")
            await s.commit()
        await self.denied(
            self.receipt("unbounded_denied", "unlimited"), D("0.1"), expected=self.policy.BudgetAdmissionDenied
        )
        await self.balances("unlimited", "0", unbounded=1)

    async def rollback_after_receipt(self):
        original = self.receipt_module.LLMDispatchReceiptManager._record_in_session

        async def fail(manager, record, session):
            await original(manager, record, session)
            raise Injected("after-receipt")

        with patch.object(self.receipt_module.LLMDispatchReceiptManager, "_record_in_session", fail):
            try:
                await self.admit(self.receipt("rollback_receipt", "rollback_receipt"))
            except Injected:
                pass
            else:
                raise Blocked("receipt-fault-not-seen")
        await self.balances("rollback_receipt", "0")
        await self.absent("rollback_receipt")

    async def rollback_before_commit(self):
        # Existing Session commit is patched only for the admission-owned session.
        # Flush real balance mutation, then raise before real commit.
        base = self.budget_module.LLMBudgetManager

        class Fault(base):
            @asynccontextmanager
            async def _get_owned_session(self):
                original = None
                try:
                    async with super()._get_owned_session() as s:
                        original = s.commit

                        async def fail():
                            await s.flush()
                            raise Injected("after-balance-before-commit")

                        s.commit = fail
                        yield s
                        # Keep replacement installed until real base context exits.
                finally:
                    if original is not None:
                        s.commit = original

        try:
            await self.admit(self.receipt("rollback_balance", "rollback_balance"), manager=Fault())
        except Injected:
            pass
        else:
            raise Blocked("balance-fault-not-seen")
        await self.balances("rollback_balance", "0")
        await self.absent("rollback_balance")

    async def absent(self, identity):
        from sqlalchemy import select

        async with self.session() as s:
            for model in (self.Receipt, self.Reservation):
                demand(
                    (await s.execute(select(model.id).where(model.dispatch_id == self.ids[identity]))).first() is None,
                    "rollback-persisted",
                )

    async def post_commit_ack_loss(self):
        base = self.budget_module.LLMBudgetManager

        class Loss(base):
            @asynccontextmanager
            async def _get_owned_session(self):
                async with super()._get_owned_session() as s:
                    yield s
                # The real manager's commit has completed. No server disconnect.
                raise Injected("synthetic-post-commit-ack-loss")

        record = self.receipt("ack", "ack")
        try:
            await self.admit(record, manager=Loss())
        except Injected:
            pass
        else:
            raise Blocked("ack-fault-not-seen")
        await self.balances("ack", "0.4")
        rows = await self.snapshot("synthetic-post-commit-ack-loss")
        for name in ("receipts", "reservations"):
            demand(sum(r["dispatch_id"] == self.ids["ack"] for r in rows[name]) == 1, "ack-not-durable")
        demand(await self.admit(record) is False, "ack-retry-permission")

    async def guards(self):
        from sqlalchemy import delete, insert, update
        from sqlalchemy.exc import DBAPIError

        table = self.Reservation.__table__
        statements = [
            (update(table).where(table.c.dispatch_id == self.ids["fresh"]).values(cap_usd=D("0.9")), "55000"),
            (delete(table).where(table.c.dispatch_id == self.ids["fresh"]), "55000"),
        ]
        # TRUNCATE targets the entire table even when rolled back: retained old
        # receipts are not touched, but Gate C MUST approve this exact probe.
        quote = self.db.async_engine.dialect.identifier_preparer.quote
        statements.append((self.text("TRUNCATE " + quote(self.cfg["schema"]) + ".llm_budget_reservations"), "55000"))
        record = self.receipt("fresh", "fresh")
        template = dict(
            dispatch_id=record["dispatch_id"],
            tenant_id=record["tenant_id"],
            utc_day=self.day,
            ceiling_state="bounded",
            ceiling_usd=D("0.4"),
            bound_version="reviewed-bound-v1",
            cap_usd=D(1),
            undefined_policy_version="owner-undefined-allow-v1",
        )
        for key, changes, code in (
            ("invalid_missing", {"dispatch_id": self.ids["invalid_missing"]}, "23503"),
            ("invalid_payer", {"tenant_id": self.cfg["fixtures"]["other"]}, "23503"),
            ("invalid_day", {"utc_day": self.day - timedelta(days=1)}, "23503"),
            ("invalid_tariff", {"ceiling_state": "undefined", "ceiling_usd": D(0), "bound_version": None}, "23514"),
        ):
            statements.append((insert(table).values(**{**template, **changes}), code))
        for statement, expected in statements:
            async with self.session(False) as s:
                try:
                    await s.execute(statement)
                except DBAPIError as error:
                    demand(getattr(error.orig, "sqlstate", None) == expected, "guard-wrong-sqlstate")
                else:
                    raise Blocked("guard-not-enforced")
        self.emit("variant", name="missing-guard-postgresql", result="NOT_RUN", reason="no-trigger-disable")

    async def preservation(self):
        await self.verify_target()
        after = await self.protected()
        demand(after == self.before, "protected-records-changed")
        rows = await self.snapshot("final")
        receipt_ids = {r["dispatch_id"] for r in rows["receipts"]}
        for row in rows["receipts"]:
            identity = next(k for k, v in self.ids.items() if v == row["dispatch_id"])
            fixture = (
                "race"
                if identity in ("race_a", "race_b")
                else (
                    "replay"
                    if identity == "standalone"
                    else "historical" if identity == "historical_seed" else identity
                )
            )
            state = identity if identity in ("free", "undefined") else "known"
            expected_receipt = self.receipt(identity, fixture, state, historical=identity == "historical_seed")
            demand(all(row[k] == v for k, v in expected_receipt.items()), "retained-receipt-content")
        reservation_ids = {r["dispatch_id"] for r in rows["reservations"]}
        expected = {self.ids[k] for k in RETAINED}
        winners = receipt_ids & {self.ids["race_a"], self.ids["race_b"]}
        demand(len(winners) == 1, "race-retained")
        expected |= winners
        expected_amounts = {
            "fresh": "0.4",
            "replay": "0.6",
            "other": "0.6",
            "historical_seed": "0.1",
            "free": "0",
            "undefined": "0",
            "quantum": "0.000000001",
            "ack": "0.4",
            "race_a": "0.6",
            "race_b": "0.6",
        }
        for row in rows["reservations"]:
            identity = next(k for k, v in self.ids.items() if v == row["dispatch_id"])
            receipt_row = next(r for r in rows["receipts"] if r["dispatch_id"] == row["dispatch_id"])
            demand(
                row["tenant_id"] == receipt_row["tenant_id"] and row["utc_day"] == receipt_row["utc_day"],
                "retained-binding",
            )
            demand(row["undefined_policy_version"] == "owner-undefined-allow-v1", "retained-policy")
            expected_state = (
                "undefined" if identity == "undefined" else "unbounded" if identity == "unlimited" else "bounded"
            )
            demand(row["ceiling_state"] == expected_state, "retained-ceiling-state")
            demand(row["cap_usd"] == (None if identity == "unlimited" else D(1)), "retained-cap")
            demand(
                row["bound_version"]
                == (None if identity in ("free", "undefined", "unlimited") else "reviewed-bound-v1"),
                "retained-bound-version",
            )
            demand(
                row["ceiling_usd"] == (None if identity == "unlimited" else D(expected_amounts[identity])),
                "retained-ceiling",
            )
        demand(
            receipt_ids == expected and reservation_ids == expected - {self.ids["standalone"]}, "retained-identities"
        )
        demand(
            len(rows["receipts"]) == 11 and len(rows["reservations"]) == 10 and len(rows["days"]) == 18,
            "retained-count",
        )
        from sqlalchemy import select

        async with self.session() as s:
            for label, changes in (
                ("cap", {"daily_cost_limit": 0.5}),
                ("unlimited", {"plan": "pro", "daily_cost_limit": 1.0}),
            ):
                tenant = (
                    await s.execute(select(self.Tenant).where(self.Tenant.id == self.cfg["fixtures"][label]))
                ).scalar_one()
                actual = {col.name: getattr(tenant, col.name) for col in self.Tenant.__table__.columns}
                before = self.mutable_before[label]
                demand(actual["updated_at"] >= before["updated_at"], "fixture-timestamp")
                expected_row = {**before, **changes, "updated_at": actual["updated_at"]}
                demand(actual == expected_row, "mutable-fixture-unexpected-change")
        amounts = {
            "fresh": "0.4",
            "race": "0.6",
            "replay": "0.6",
            "other": "0.6",
            "quantum": "0.000000001",
            "ack": "0.4",
        }
        for label in FIXTURES:
            if label not in ("missing", "old_day"):
                await self.balances(
                    label,
                    amounts.get(label, "0"),
                    undefined=int(label == "undefined"),
                    unbounded=int(label == "unlimited"),
                )
        for row in rows["days"]:
            label = next(k for k, v in self.cfg["fixtures"].items() if v == row["tenant_id"])
            older = row["utc_day"] != self.day
            demand(row["utc_day"] in (self.day, self.day - timedelta(days=1)), "unexpected-day")
            demand(not older or label in ("old_day", "historical"), "unexpected-older-baseline")
            demand(
                row["settled_usd"] == (D(1) if label == "undefined" else D("0.1") if label == "fresh" else D(0)),
                "settled-changed",
            )
            demand(row["frozen"] == (label == "frozen"), "freeze-changed")
            demand(
                row["baseline_version"] == ("" if label == "blank" else "reviewed-baseline-v1"),
                "baseline-version-changed",
            )
            if older:
                demand(
                    row["reserved_usd"] == (D("0.1") if label == "historical" else D(0))
                    and row["unbounded_count"] == row["undefined_count"] == 0,
                    "older-balance-changed",
                )
        sequences_after = await self.sequences()
        self.emit("sequence_manifest", before=self.sequences_before, after=sequences_after)
        expected_steps = {
            self.cfg["sequence_names"][k]: v
            for k, v in (("llm_dispatch_receipts", 19), ("llm_budget_reservations", 15), ("llm_budget_days", 18))
        }
        demand(set(sequences_after) == set(self.sequences_before), "sequence-set-changed")
        for name, before in self.sequences_before.items():
            actual = sequences_after[name]
            if name not in expected_steps:
                demand(actual == before, "unapproved-sequence-advancement")
                continue
            effective = before["last_value"] - int(not before["is_called"])
            demand(
                actual["last_value"] == effective + expected_steps[name] and actual["is_called"],
                "sequence-delta-mismatch",
            )
        self.emit("preservation", protected=after, result="PASS", sequence_advancement="accounted-not-reset")


def test_reviewed_package(request):
    """Single positional pytest node; disable autoload BEFORE starting pytest."""
    # Built-in pytest plugins only, including its standard unittest/asyncio
    # support (no third-party pytest-asyncio is needed for asyncio.run).
    demand(not request.config.pluginmanager.list_plugin_distinfo(), "third-party-plugin")
    for name, plugin in request.config.pluginmanager.list_name_plugin():
        if plugin is None:
            continue
        module = getattr(plugin, "__name__", type(plugin).__module__)
        demand(module.startswith("_pytest.") or module == "_pytest", "nonbuiltin-plugin")
    args = list(request.config.invocation_params.args)
    demand(
        "--noconftest" in args and "--no-header" in args and "--tb=short" in args and "-q" in args,
        "pytest-routing-flags",
    )
    demand(
        "addopts=" in args and not any(arg == "-p" or arg.startswith("-p") for arg in args), "pytest-plugin-arguments"
    )
    asyncio.run(BudgetPackage().run())
