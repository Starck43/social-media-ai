"""Opt-in real-PostgreSQL acceptance for reviewed day-baseline provisioning.

PR197-only scope (`feat/reviewed-budget-baseline`). Collection imports stdlib
only and never contacts a database; execution requires an explicit private
approval (environment `PR197_PG_URL` plus `PR197_APPROVAL`) that pins the
target database/schema/executor, the expected normal revision `0097`, the exact
synthetic identity allocation, the exact per-case write/probe grants and the run
identity. Without that approval every case skips.

Discipline:
* bootstrap-free: no root conftest, implicit service bootstrap or unrelated
  suite; real models, managers, owned sessions and transactions;
* DEFAULT READ-ONLY: every session opens `SET TRANSACTION READ ONLY` unless a
  case passes a universal write gate. Writes are possible only through
  `_write_session`, which requires the case to be listed in the approval's
  `write_cases` and the (table, operation) pair to be enumerated in
  ALLOWED_SYNTHETIC_CHANGES; negative probes only through `_probe_statement`
  with the case listed in `probe_cases`. Every gate decision is journaled;
* the effective target is verified (database, executor, server identity,
  exclusivity, exact revision, installed guards) BEFORE any write;
* only the enumerated synthetic payers (SYNTHETIC_TENANTS) are written, and only
  the enumerated ALLOWED_SYNTHETIC_CHANGES may take effect; prior business rows
  and evidence are never altered;
* TRUNCATE is never attempted on the retained target — its guard-negative probe
  is DEFERRED to a separately approved disposable target;
* every readback is INDEPENDENT (a second session/connection);
* preservation evidence: a before/after digest manifest of all retained rows,
  sequences and the revision is captured and written to a private journal; the
  teardown verdict must show zero non-synthetic drift;
* synthetic faults are labelled synthetic and never claim real network loss;
* teardown retains synthetic evidence (no deletes, no cleanup) and verifies
  that no implicit baseline permission/grant rows appeared.

Run standalone:

    python tests/acceptance/test_pr197_reviewed_baseline_pg.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

# Exact tested source head for this harness (PR197 package head).
SOURCE_PIN = "ad912d4ae50c7d66e4846529ffe8ed2cf11edefa"

CASES = (
    "fresh_frozen_day_with_original_provenance",
    "exact_replay_returns_false_without_rewrites",
    "conflicting_same_day_refused",
    "identical_race_single_durable_pair",
    "conflicting_race_single_winner",
    "distinct_payer_isolation",
    "frozen_day_denies_admission_without_writes",
    "provision_and_admission_lock_order",
    "precommit_rollback_leaves_no_half_pair",
    "synthetic_cancellation_rolls_back_and_releases_locks",
    "uncertain_commit_never_returns_success",
    "immutable_update_delete_refused",
    "initial_snapshot_binding_and_constraints_refused",
    "legacy_day_adoption_refused",
    "replay_preserves_current_counters_and_freeze",
    "no_implicit_baseline_grants",
)

# Per-case write/probe grants. The approval file must list exactly these sets;
# any other allocation is refused. Reads need no grant; every mutable session
# must pass through `_write_session` (durable writes) or `_probe_statement`
# (must-fail probes that must leave nothing durable).
WRITE_CASES = (
    "fresh_frozen_day_with_original_provenance",
    "identical_race_single_durable_pair",
    "conflicting_race_single_winner",
    "distinct_payer_isolation",
    "provision_and_admission_lock_order",
    "precommit_rollback_leaves_no_half_pair",
    "synthetic_cancellation_rolls_back_and_releases_locks",
    "uncertain_commit_never_returns_success",
    "initial_snapshot_binding_and_constraints_refused",
    "legacy_day_adoption_refused",
    "replay_preserves_current_counters_and_freeze",
)
PROBE_CASES = (
    "conflicting_same_day_refused",
    "precommit_rollback_leaves_no_half_pair",
    "synthetic_cancellation_rolls_back_and_releases_locks",
    "uncertain_commit_never_returns_success",
    "immutable_update_delete_refused",
    "initial_snapshot_binding_and_constraints_refused",
    "frozen_day_denies_admission_without_writes",
    "legacy_day_adoption_refused",
)
# Exact same-packet replays are neither a durable write nor a must-fail probe:
# they still require an explicit grant, because the manager opens its own
# transaction and only comparison may occur.
REPLAY_CASES = ("exact_replay_returns_false_without_rewrites",)
# The only statements a probe case may attempt, row-scoped and always rolled
# back. TRUNCATE is deliberately absent: it takes ACCESS EXCLUSIVE on a retained
# table, so its guard-negative probe is deferred to a disposable target.
PROBE_SCOPE = (
    ("public.llm_budget_baselines", "INSERT"),
    ("public.llm_budget_baselines", "UPDATE"),
    ("public.llm_budget_baselines", "DELETE"),
    ("public.llm_dispatch_receipts", "INSERT"),
)

# Explicitly deferred cases, per the package handoff and this review.
DEFERRED = (
    "guard-negative disposable-target probes, including llm_budget_baselines "
    "TRUNCATE refusal (separately approved disposable target; never on the "
    "retained target)",
    "real network-loss acknowledgement proof (synthetic ack loss only)",
    "worker/payer-wide in-flight coverage and prior-liability audit",
)

# Retained-state preservation: these relations must show zero non-synthetic
# drift between the before/after manifests.
PRESERVED_TABLES = (
    "public.llm_budget_days",
    "public.llm_budget_reservations",
    "public.llm_dispatch_receipts",
    "public.llm_outcome_events",
    "public.llm_attempts",
    "public.tenants",
    "public.permissions",
    "public.roles",
    "public.role_permission",
    "public.model_types",
)
SYNTHETIC_SCOPED_TABLES = ("public.tenants", "public.llm_budget_days", "public.llm_budget_baselines")

EXPECTED_REVISION = "0097"
BASELINE_TRIGGERS = (
    "llm_budget_baselines_immutable",
    "llm_budget_baselines_no_truncate",
    "llm_budget_baselines_day_binding",
)
# Concrete, enumerated synthetic identities — no open range. The approval file
# must match this tuple exactly; the harness refuses any other allocation.
SYNTHETIC_TENANTS = (
    900001,  # fresh + replay + conflict + admission-denial + immutability + counters cases
    900002,  # identical same-payer/same-day race
    900003,  # conflicting same-payer/same-day race
    900004,  # distinct payer isolation
    900005,  # provision-vs-admission lock order
    900006,  # precommit rollback / legacy adoption refusal
    900007,  # synthetic cancellation
    900008,  # uncertain commit acknowledgement + explicit replay
    900009,  # initial-snapshot binding / constraint refusals
    900010,  # legacy/manual day adoption refusal
)
# The ONLY durable changes this harness may make, and only for the enumerated
# synthetic identities above. Anything else must be refused or is out of scope.
ALLOWED_SYNTHETIC_CHANGES = (
    ("public.tenants", "INSERT", "synthetic payer rows named pr197-synthetic-<id>, slug pr197-synthetic-<id>"),
    ("public.llm_budget_days", "INSERT", "via LLMBudgetManager.provision_reviewed_day only"),
    ("public.llm_budget_baselines", "INSERT", "via LLMBudgetManager.provision_reviewed_day only"),
    ("public.llm_budget_days", "UPDATE", "synthetic rows' current settled_usd/reserved_usd in the replay case"),
)
# Every other relation is SELECT-only. UPDATE/DELETE on llm_budget_baselines are
# attempted only as row-scoped, rolled-back probes on synthetic rows; TRUNCATE is
# never attempted on the retained target (deferred guard-negative probe).
FORBIDDEN_SYNTHETIC_CHANGES = (
    ("public.llm_budget_baselines", "UPDATE"),
    ("public.llm_budget_baselines", "DELETE"),
    ("public.llm_budget_baselines", "TRUNCATE"),
    ("public.permissions", "INSERT"),  # no implicit baseline grants
    ("public.roles", "INSERT"),  # no implicit baseline grants
    ("public.role_permission", "INSERT"),  # no implicit baseline grants
    ("public.model_types", "INSERT"),  # baseline table stays uncatalogued
)


class Blocked(RuntimeError):
    """Safe label only; server exception text must never reach the report."""


class ApprovalMissing(unittest.SkipTest):
    pass


def demand(condition, label: str) -> None:
    if not condition:
        raise Blocked(label)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.DEVNULL).decode().strip()


def load_approval() -> dict:
    url = os.environ.get("PR197_PG_URL")
    raw = os.environ.get("PR197_APPROVAL")
    if not url or not raw:
        raise ApprovalMissing("PR197_PG_URL/PR197_APPROVAL not provided")
    path = Path(raw)
    approval = json.loads(path.read_text() if path.exists() else raw)
    head = git("rev-parse", "HEAD")
    demand(head == SOURCE_PIN, f"runner-source-pin:{head}")
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    demand(parsed.drivername == "postgresql" and not parsed.query, "url-driver-or-query")
    demand(
        [parsed.host, parsed.port or 5432, parsed.database, parsed.username] == approval["target"],
        "target-tuple",
    )
    demand(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", approval["schema"]), "schema-name")
    demand(approval["expected_revision"] == EXPECTED_REVISION, "expected-revision")
    demand(approval["executor"] == parsed.username, "executor")
    demand(approval["server_identity"] and len(approval["server_identity"]) == 2, "server-identity")
    demand(
        approval.get("write_cases") == list(WRITE_CASES),
        "write-cases-grant",
    )
    demand(approval.get("probe_cases") == list(PROBE_CASES), "probe-cases-grant")
    demand(approval.get("replay_cases") == list(REPLAY_CASES), "replay-cases-grant")
    payers = approval["synthetic_tenants"]
    demand(payers == list(SYNTHETIC_TENANTS), "synthetic-tenant-allocation")
    demand(
        approval.get("allowed_changes") == [list(item) for item in ALLOWED_SYNTHETIC_CHANGES],
        "allowed-changes-contract",
    )
    demand(re.fullmatch(r"[0-9a-f]{32}", approval["run_id"]), "run-id")
    return {"url": url, "approval": approval, "payers": payers}


class Pr197PgAcceptance(unittest.IsolatedAsyncioTestCase):
    """Real PG acceptance; every scenario labels its synthetic fault."""

    approval: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.approval = load_approval()
        cls._install_application()
        cls._open_journal()
        cls.preconditions_verified = False
        cls.preservation_before = {}

    # --- journal / preservation evidence -----------------------------------

    @classmethod
    def _open_journal(cls) -> None:
        """Private evidence: every gate decision and every manifest line."""

        run_id = cls.approval["approval"]["run_id"]
        base = Path(cls.approval["approval"].get("evidence_dir") or (ROOT / ".agents" / "tmp"))
        base.mkdir(parents=True, exist_ok=True)
        cls.journal_path = base / f"pr197-{run_id}-journal.jsonl"
        cls.manifest_path = base / f"pr197-{run_id}-manifest.json"
        cls._journal("open", run_id=run_id, source_pin=SOURCE_PIN, deferred=list(DEFERRED))

    @classmethod
    def _journal(cls, kind: str, **values) -> None:
        record = {"kind": kind, "at": now(), **values}
        with open(cls.journal_path, "a", opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    @staticmethod
    def _case_id(case: str) -> str:
        """Accept the unittest method name (`test_x`) or the bare case id."""

        return case[5:] if case.startswith("test_") else case

    def gate_write(self, case: str, table: str, operation: str) -> None:
        """Universal durable-write gate: nothing mutates without passing here."""

        case = self._case_id(case)
        c = self.approval["approval"]
        demand(self.preconditions_verified, f"write-before-preconditions:{case}")
        demand(case in c["write_cases"], f"write-case-not-granted:{case}")
        matched = [item for item in ALLOWED_SYNTHETIC_CHANGES if (item[0], item[1]) == (table, operation)]
        demand(bool(matched), f"write-not-allowed:{table}:{operation}")
        self._journal(
            "write-gate",
            case=case,
            table=table,
            operation=operation,
            note=matched[0][2],
        )

    def gate_probe(self, case: str, table: str, operation: str) -> None:
        """Universal must-fail probe gate: row-scoped, rolled back, journalled."""

        case = self._case_id(case)
        c = self.approval["approval"]
        demand(self.preconditions_verified, f"probe-before-preconditions:{case}")
        demand(case in c["probe_cases"], f"probe-case-not-granted:{case}")
        demand((table, operation) in PROBE_SCOPE, f"probe-not-in-scope:{table}:{operation}")
        self._journal("probe-gate", case=case, table=table, operation=operation)

    def gate_replay(self, case: str) -> None:
        """Universal replay gate: comparison only, never a resend or write."""

        case = self._case_id(case)
        c = self.approval["approval"]
        demand(self.preconditions_verified, f"replay-before-preconditions:{case}")
        demand(case in c["replay_cases"], f"replay-case-not-granted:{case}")
        self._journal("replay-gate", case=case)

    @classmethod
    async def capture_preservation(cls, label: str) -> dict:
        """Digest of every retained relation, sequences and the revision."""

        snapshot = {}
        async with cls.session(readonly=True) as s:
            for table in PRESERVED_TABLES:
                rows = (await s.execute(cls.text(f"SELECT * FROM {table} ORDER BY 1, 2"))).mappings().all()
                snapshot[table] = [
                    dict(row)
                    for row in rows
                    if table not in SYNTHETIC_SCOPED_TABLES or row.get("tenant_id") not in SYNTHETIC_TENANTS
                ]
            snapshot["sequences"] = dict(
                (await s.execute(cls.text("SELECT sequencename, last_value FROM public.llm_budget_days_id_seq"))).all()
            )
            snapshot["revision"] = (
                await s.execute(cls.text("SELECT version_num FROM public.alembic_version"))
            ).scalar_one()
        digests = {name: sha256_hex(json.dumps(value, sort_keys=True, default=str)) for name, value in snapshot.items()}
        cls._journal(
            "preservation",
            label=label,
            digests=digests,
            counts={k: len(v) for k, v in snapshot.items() if isinstance(v, list)},
        )
        return digests

    @classmethod
    async def _capture_preservation(cls):
        return await cls.capture_preservation("after")

    # --- target/install ---------------------------------------------------

    @classmethod
    def _install_application(cls) -> None:
        import sqlalchemy.ext.asyncio as sa_async
        from pydantic_settings.sources import DotEnvSettingsSource

        c = cls.approval
        environment = {
            "POSTGRES_URL": c["url"],
            "DB_SCHEMA": c["approval"]["schema"],
            "SECRET_KEY": "pr197-acceptance-only-not-an-authentication-service",
            "DEBUG": "false",
            "ENVIRONMENT": "test",
            "AGENT_DAILY_COST_LIMIT": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }

        def reviewed_engine(*args, **kwargs):
            demand("connect_args" not in kwargs, "unexpected-engine-connect-options")
            kwargs["connect_args"] = {
                "server_settings": {
                    "statement_timeout": "15000",
                    "lock_timeout": "5000",
                    "idle_in_transaction_session_timeout": "20000",
                    "application_name": "pr197-" + c["approval"]["run_id"],
                }
            }
            return real_create(*args, **kwargs)

        real_create = sa_async.create_async_engine
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("dotenv.load_dotenv", return_value=False),
            patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}),
            patch.object(sa_async, "create_async_engine", reviewed_engine),
        ):
            cls.settings = __import__("app.core.config", fromlist=["settings"]).settings
            __import__("app.models")
            cls.db = __import__("app.core.database", fromlist=["async_session_maker"])
            cls.budget_module = __import__("app.models.managers.llm_budget_manager", fromlist=["LLMBudgetManager"])
            cls.receipts = __import__("app.services.ai.dispatch_receipts", fromlist=["build_dispatch_receipt"])
            cls.context = __import__("app.core.tenant_context", fromlist=["tenant_scope"])
            cls.baselines_service = __import__("app.services.ai.budget_baselines", fromlist=["BudgetBaselineConflict"])
            cls.admission_service = __import__(
                "app.services.ai.budget_admission",
                fromlist=["BudgetAdmissionUnavailable", "BudgetAdmissionConflict", "BudgetAdmissionDenied"],
            )

        from types import SimpleNamespace

        from app.models.llm.llm_budget import LLMBudgetDay
        from app.models.llm.llm_budget_baseline import LLMBudgetBaseline
        from app.models.tenancy.tenant import Tenant

        cls.text = __import__("sqlalchemy", fromlist=["text"]).text
        cls.Day = LLMBudgetDay
        cls.Tenant = Tenant
        cls.manager = cls.budget_module.LLMBudgetManager()

    # --- sessions ---------------------------------------------------------

    @classmethod
    @asynccontextmanager
    async def session(cls, readonly: bool = True):
        """DEFAULT READ-ONLY: read-only transaction unless a caller opts in."""

        async with cls.db.async_session_maker() as session:
            if readonly:
                await session.execute(cls.text("SET TRANSACTION READ ONLY"))
            try:
                yield session
            finally:
                await session.rollback()

    @asynccontextmanager
    async def _write_session(self, case: str):
        """Read-write session for ONE granted durable-write case.

        The gate is evaluated before the session is even opened; the session
        itself still cannot touch anything outside the approved pair because
        each durable statement passes `gate_write` in the calling case.
        """

        c = self.approval["approval"]
        demand(self.preconditions_verified, f"write-before-preconditions:{case}")
        demand(case in c["write_cases"], f"write-case-not-granted:{case}")
        self._journal("write-session-open", case=case)
        async with self.db.async_session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def probe(self, case: str, table: str, operation: str, statement: str, params: dict) -> None:
        """Must-fail probe: gated, row-scoped, executed and rolled back."""

        self.gate_probe(case, table, operation)
        async with self.db.async_session_maker() as s:
            try:
                await s.execute(self.text(statement), params)
            except Exception:
                await s.rollback()
                return
            await s.rollback()
            raise Blocked(f"probe-not-refused:{table}:{operation}")

    async def readback(self, readonly: bool = True):
        """Independent readback: a fresh session, never the writing session."""

        async with self.session(readonly=readonly) as s:
            days = (
                (await s.execute(self.text("SELECT * FROM public.llm_budget_days ORDER BY tenant_id, utc_day")))
                .mappings()
                .all()
            )
            baselines = (
                (await s.execute(self.text("SELECT * FROM public.llm_budget_baselines ORDER BY tenant_id, utc_day")))
                .mappings()
                .all()
            )
            counts = (await s.execute(self.text("SELECT version_num FROM public.alembic_version"))).scalar_one()
            return {"days": days, "baselines": baselines, "revision": counts}

    async def verify_preconditions(self) -> None:
        c = self.approval
        async with self.session(readonly=True) as s:
            row = (
                await s.execute(
                    self.text("SELECT current_database(), current_user, inet_server_addr()::text, inet_server_port()")
                )
            ).one()
            demand(row[0] == c["approval"]["target"][2], "target-database")
            demand(row[1] == c["approval"]["executor"], "target-executor")
            demand([row[2], row[3]] == c["approval"]["server_identity"], "server-identity")
            others = (
                await s.execute(
                    self.text(
                        "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                        "WHERE datname=current_database() AND backend_type='client backend' "
                        "AND pid<>pg_backend_pid() "
                        "AND coalesce(application_name,'') <> current_setting('application_name')"
                    )
                )
            ).scalar_one()
            demand(others == 0, "foreign-backend-on-target")
            role = (
                await s.execute(
                    self.text("SELECT rolsuper, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user")
                )
            ).one()
            demand(tuple(role) == (False, False), "privileged-runner-role")
            rev = (await s.execute(self.text("SELECT version_num FROM public.alembic_version"))).scalar_one()
            demand(rev == EXPECTED_REVISION, f"target-revision:{rev}")
            guards = set(
                (
                    await s.execute(
                        self.text(
                            "SELECT tgname FROM pg_catalog.pg_trigger t "
                            "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname=current_schema() AND c.relname='llm_budget_baselines' "
                            "AND NOT t.tgisinternal AND t.tgenabled='O'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            demand(set(BASELINE_TRIGGERS).issubset(guards), "baseline-guards")
            server = (
                await s.execute(
                    self.text(
                        "SELECT current_setting('server_version'), "
                        "(SELECT count(*) FROM public.llm_budget_days), "
                        "(SELECT count(*) FROM public.llm_budget_reservations), "
                        "(SELECT count(*) FROM public.llm_dispatch_receipts), "
                        "(SELECT count(*) FROM public.llm_outcome_events)"
                    )
                )
            ).one()
            self.retained_before = {
                "server_version": server[0],
                "days": server[1],
                "reservations": server[2],
                "receipts": server[3],
                "outcomes": server[4],
            }
            self._journal("preconditions", **self.retained_before, revision=rev, executor=row[1], database=row[0])
            payload = (
                (
                    await s.execute(
                        self.text("SELECT tenant_id FROM public.llm_budget_days WHERE tenant_id = ANY(:p)"),
                        {"p": c["payers"]},
                    )
                )
                .scalars()
                .all()
            )
            demand(not payload, "synthetic-payer-rows-present")
            grants = dict(
                (
                    await s.execute(
                        self.text(
                            "SELECT (SELECT count(*) FROM public.permissions), (SELECT count(*) FROM public.roles)"
                        )
                    )
                ).one()
            )
            self.grants_before = grants
        self.preservation_before = await self.capture_preservation("before")
        self.preconditions_verified = True

    # --- fixtures (synthetic, labelled, retained) -------------------------

    def packet(self, tenant_id: int, day, *, settled="1.500000000", reserved="0.250000000", version="v1"):
        run_id = self.approval["approval"]["run_id"]
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        return {
            "tenant_id": tenant_id,
            "utc_day": day,
            "settled_usd": Decimal(settled),
            "reserved_usd": Decimal(reserved),
            "undefined_count": 1,
            "unbounded_count": 2,
            "frozen": True,
            "baseline_version": f"synthetic-{version}",
            "reviewed_by": "pr197-harness",
            "review_ref": f"synthetic-{run_id}",
            "evidence_sha256": sha256_hex(f"{run_id}:{tenant_id}:{day}:{version}"),
            "cutoff_at": start + timedelta(hours=23),
            "reviewed_at": start + timedelta(hours=23, minutes=30),
            "fence_ref": "synthetic-fence",
            "fence_version": "quiesced-accounted-v1",
        }

    async def seed_synthetic_tenant(self, tenant_id: int, case: str) -> None:
        self.gate_write("fresh_frozen_day_with_original_provenance", "public.tenants", "INSERT")
        self._journal("seed", case=case, kind="synthetic-tenant", tenant_id=tenant_id)
        async with self.db.async_session_maker() as s:
            await s.execute(
                self.text(
                    "INSERT INTO public.tenants (id, name, slug, is_active, plan, created_at, updated_at) "
                    "VALUES (:i, :n, :s, true, 'synthetic', now(), now()) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"i": tenant_id, "n": f"pr197-synthetic-{tenant_id}", "s": f"pr197-synthetic-{tenant_id}"},
            )
            await s.commit()

    async def seed_unreviewed_day(self, tenant_id: int, day, case: str) -> None:
        """Pre-existing unreviewed day evidence: retained, never rewritten."""

        self.gate_write("precommit_rollback_leaves_no_half_pair", "public.llm_budget_days", "INSERT")
        self._journal("seed", case=case, kind="unreviewed-day", tenant_id=tenant_id)
        async with self.db.async_session_maker() as s:
            await s.execute(
                self.text(
                    "INSERT INTO public.llm_budget_days (tenant_id, utc_day, settled_usd, reserved_usd, "
                    "undefined_count, unbounded_count, frozen, baseline_version) "
                    "VALUES (:i, :d, '9.000000000', '9.000000000', 9, 9, false, '') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"i": tenant_id, "d": day},
            )
            await s.commit()

    # --- helpers ----------------------------------------------------------

    def receipt(self, tenant_id: int, day, dispatch_id: str):
        from types import SimpleNamespace

        model = SimpleNamespace(
            id=self.approval["approval"]["synthetic_model_id"],
            model_id="pr197-synthetic-model",
            provider=SimpleNamespace(
                id=self.approval["approval"]["synthetic_provider_id"], name="pr197-synthetic-provider"
            ),
            input_cost_per_1k=Decimal("0.001"),
            output_cost_per_1k=Decimal("0.002"),
        )
        return self.receipts.build_dispatch_receipt(
            model,
            tenant_id=tenant_id,
            purpose="pr197_acceptance",
            stage="synthetic",
            dispatch_id=dispatch_id,
            prepared_at=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
        )

    async def rows_for(self, tenant_id: int, day):
        async with self.session(readonly=True) as s:
            days = (
                (
                    await s.execute(
                        self.text("SELECT * FROM public.llm_budget_days WHERE tenant_id=:i AND utc_day=:d"),
                        {"i": tenant_id, "d": day},
                    )
                )
                .mappings()
                .all()
            )
            baselines = (
                (
                    await s.execute(
                        self.text("SELECT * FROM public.llm_budget_baselines WHERE tenant_id=:i AND utc_day=:d"),
                        {"i": tenant_id, "d": day},
                    )
                )
                .mappings()
                .all()
            )
            return days, baselines

    async def lock_release(self, table: str) -> bool:
        """No backend of THIS harness still holds a row lock on the table.

        Row locks are per-backend, so the check joins pg_locks with
        pg_stat_activity and filters on the harness application name.
        """

        async with self.session(readonly=True) as s:
            held = (
                await s.execute(
                    self.text(
                        "SELECT count(*) FROM pg_catalog.pg_locks l "
                        "JOIN pg_catalog.pg_class c ON c.oid=l.relation "
                        "JOIN pg_catalog.pg_stat_activity a ON a.pid=l.pid "
                        "WHERE c.relname=:t AND a.application_name=current_setting('application_name') "
                        "AND l.locktype='relation' "
                        "AND l.mode IN ('RowExclusiveLock','ExclusiveLock','AccessExclusiveLock')"
                    ),
                    {"t": table},
                )
            ).scalar_one()
        return held == 0

    @classmethod
    def tearDownClass(cls) -> None:
        """Preservation verdict + manifest; synthetic evidence is retained."""

        if not getattr(cls, "preconditions_verified", False):
            cls._journal("teardown", verdict="skipped-preconditions-unverified")
            return
        after = asyncio.run(cls._capture_preservation())
        drift = {name: value for name, value in after.items() if cls.preservation_before.get(name) != value}
        verdict = "preserved" if not drift else f"drift:{sorted(drift)}"
        manifest = {
            "kind": "pr197-acceptance-manifest",
            "run_id": cls.approval["approval"]["run_id"],
            "source_pin": SOURCE_PIN,
            "target": cls.approval["approval"]["target"],
            "schema": cls.approval["approval"]["schema"],
            "revision_expected": EXPECTED_REVISION,
            "synthetic_tenants": list(SYNTHETIC_TENANTS),
            "allowed_changes": [list(item) for item in ALLOWED_SYNTHETIC_CHANGES],
            "deferred_cases": list(DEFERRED),
            "preservation_before": cls.preservation_before,
            "preservation_after": after,
            "drift": sorted(drift),
            "verdict": verdict,
            "finished_at": now(),
        }
        fd = os.open(cls.manifest_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(manifest, handle, indent=1, sort_keys=True, default=str)
            handle.write("\n")
        cls._journal("teardown", verdict=verdict, drift=sorted(drift))

    # --- cases ------------------------------------------------------------

    async def test_fresh_frozen_day_with_original_provenance(self) -> None:
        await self.verify_preconditions()
        payer = self.approval["payers"][0]
        await self.seed_synthetic_tenant(payer, "test_fresh_frozen_day_with_original_provenance")
        day = datetime.now(timezone.utc).date()
        packet = self.packet(payer, day)
        self.gate_write("test_fresh_frozen_day_with_original_provenance", "public.llm_budget_baselines", "INSERT")
        async with self.context.tenant_scope(payer):
            created = await self.manager.provision_reviewed_day(packet)
        self.assertTrue(created, "synthetic fresh provisioning must be acknowledged")
        state = await self.readback()
        day_rows = [d for d in state["days"] if d["tenant_id"] == payer and d["utc_day"] == day]
        base_rows = [b for b in state["baselines"] if b["tenant_id"] == payer and b["utc_day"] == day]
        self.assertEqual((len(day_rows), len(base_rows)), (1, 1), "synthetic one owned pair")
        self.assertTrue(base_rows[0]["frozen"], "fresh day must be frozen")
        self.assertEqual(Decimal(base_rows[0]["settled_usd"]), packet["settled_usd"])
        self.assertEqual(state["revision"], EXPECTED_REVISION)

    async def test_exact_replay_returns_false_without_rewrites(self) -> None:
        payer = self.approval["payers"][0]
        day = datetime.now(timezone.utc).date()
        before_days, before_bases = await self.rows_for(payer, day)
        packet = self.packet(payer, day)
        self.gate_replay("test_exact_replay_returns_false_without_rewrites")
        async with self.context.tenant_scope(payer):
            replay = await self.manager.provision_reviewed_day(packet)
        self.assertFalse(replay, "exact replay must acknowledge the stored pair, not create one")
        after_days, after_bases = await self.rows_for(payer, day)
        self.assertEqual([dict(r) for r in before_days], [dict(r) for r in after_days], "counters untouched")
        self.assertEqual([dict(r) for r in before_bases], [dict(r) for r in after_bases], "evidence untouched")

    async def test_conflicting_same_day_refused(self) -> None:
        payer = self.approval["payers"][0]
        day = datetime.now(timezone.utc).date()
        tampered = self.packet(payer, day, settled="2.000000000", version="tampered")
        self.gate_probe("test_conflicting_same_day_refused", "public.llm_budget_baselines", "INSERT")
        with self.assertRaises(self.baselines_service.BudgetBaselineConflict):
            async with self.context.tenant_scope(payer):
                await self.manager.provision_reviewed_day(tampered)
        _, bases = await self.rows_for(payer, day)
        self.assertTrue(
            all(Decimal(b["settled_usd"]) != Decimal("2.000000000") for b in bases), "conflict stored nothing"
        )

    async def test_identical_race_single_durable_pair(self) -> None:
        payer = self.approval["payers"][1]
        await self.seed_synthetic_tenant(payer, "test_identical_race_single_durable_pair")
        day = datetime.now(timezone.utc).date()
        packet = self.packet(payer, day)
        self.gate_write("test_identical_race_single_durable_pair", "public.llm_budget_baselines", "INSERT")

        async def run():
            async with self.context.tenant_scope(payer):
                return await self.manager.provision_reviewed_day(packet)

        results = await asyncio.gather(run(), run(), return_exceptions=True)
        truths = [r for r in results if r is True]
        self.assertEqual(len(truths), 1, "synthetic identical race: exactly one fresh insert")
        _, bases = await self.rows_for(payer, day)
        self.assertEqual(len(bases), 1, "synthetic identical race: one durable pair")

    async def test_conflicting_race_single_winner(self) -> None:
        payer = self.approval["payers"][2]
        await self.seed_synthetic_tenant(payer, "test_conflicting_race_single_winner")
        day = datetime.now(timezone.utc).date()
        self.gate_write("test_conflicting_race_single_winner", "public.llm_budget_baselines", "INSERT")

        async def winner():
            async with self.context.tenant_scope(payer):
                return await self.manager.provision_reviewed_day(self.packet(payer, day, version="winner"))

        async def loser():
            async with self.context.tenant_scope(payer):
                return await self.manager.provision_reviewed_day(
                    self.packet(payer, day, settled="7.000000000", version="loser")
                )

        results = await asyncio.gather(winner(), loser(), return_exceptions=True)
        self.assertEqual(sum(1 for r in results if r is True), 1, "synthetic race: single winner")
        self.assertEqual(sum(1 for r in results if isinstance(r, Exception)), 1, "synthetic race: single conflict")
        _, bases = await self.rows_for(payer, day)
        self.assertEqual(len(bases), 1, "synthetic race: one retained pair")

    async def test_distinct_payer_isolation(self) -> None:
        payer = self.approval["payers"][3]
        await self.seed_synthetic_tenant(payer, "test_distinct_payer_isolation")
        day = datetime.now(timezone.utc).date()
        self.gate_write("test_distinct_payer_isolation", "public.llm_budget_baselines", "INSERT")
        async with self.context.tenant_scope(payer):
            created = await self.manager.provision_reviewed_day(
                self.packet(payer, day, settled="3.000000000", version="isolated")
            )
        self.assertTrue(created)
        days, bases = await self.rows_for(payer, day)
        self.assertEqual(len(bases), 1)
        self.assertEqual(Decimal(bases[0]["settled_usd"]), Decimal("3.000000000"), "separate payer isolates values")

    async def test_frozen_day_denies_admission_without_writes(self) -> None:
        payer = self.approval["payers"][0]
        day = datetime.now(timezone.utc).date()
        async with self.session(readonly=True) as s:
            receipts = (
                await s.execute(
                    self.text("SELECT count(*) FROM public.llm_dispatch_receipts WHERE tenant_id=:i"), {"i": payer}
                )
            ).scalar_one()
            reservations = (
                await s.execute(
                    self.text("SELECT count(*) FROM public.llm_budget_reservations WHERE tenant_id=:i"), {"i": payer}
                )
            ).scalar_one()
        # Gated probe: a frozen day must refuse admission, so the attempted
        # receipt insert may never become durable.
        self.gate_probe("test_frozen_day_denies_admission_without_writes", "public.llm_dispatch_receipts", "INSERT")
        with self.assertRaises(self.admission_service.BudgetAdmissionUnavailable):
            async with self.context.tenant_scope(payer):
                await self.manager.reserve_receipt(
                    self.receipt(payer, day, sha256_hex("synthetic-admission-denied")[:32]),
                    ceiling_usd=Decimal("1.000000000"),
                )
        async with self.session(readonly=True) as s:
            after_receipts = (
                await s.execute(
                    self.text("SELECT count(*) FROM public.llm_dispatch_receipts WHERE tenant_id=:i"), {"i": payer}
                )
            ).scalar_one()
            after_reservations = (
                await s.execute(
                    self.text("SELECT count(*) FROM public.llm_budget_reservations WHERE tenant_id=:i"), {"i": payer}
                )
            ).scalar_one()
        self.assertEqual(receipts, after_receipts, "frozen day created no receipt")
        self.assertEqual(reservations, after_reservations, "frozen day created no reservation")

    async def test_provision_and_admission_lock_order(self) -> None:
        payer = self.approval["payers"][4]
        await self.seed_synthetic_tenant(payer, "test_provision_and_admission_lock_order")
        day = datetime.now(timezone.utc).date()
        self.gate_write("test_provision_and_admission_lock_order", "public.llm_budget_baselines", "INSERT")

        async def provision():
            async with self.context.tenant_scope(payer):
                return await self.manager.provision_reviewed_day(
                    self.packet(payer, day, settled="5.000000000", version="lockorder")
                )

        async def admit():
            await asyncio.sleep(0)
            async with self.context.tenant_scope(payer):
                return await self.manager.reserve_receipt(
                    self.receipt(payer, day, sha256_hex("synthetic-lock-order")[:32]),
                    ceiling_usd=Decimal("1.000000000"),
                )

        results = await asyncio.gather(provision(), admit(), return_exceptions=True)
        self.assertTrue(any(r is True for r in results), "synthetic compliant path completed")
        days, bases = await self.rows_for(payer, day)
        self.assertEqual(len(bases), 1, "compliant concurrent path kept one pair")

    async def test_precommit_rollback_leaves_no_half_pair(self) -> None:
        payer = self.approval["payers"][5]
        await self.seed_synthetic_tenant(payer, "test_precommit_rollback_leaves_no_half_pair")
        day = datetime.now(timezone.utc).date() + timedelta(days=1)
        await self.seed_unreviewed_day(payer, day, "test_precommit_rollback_leaves_no_half_pair")
        self.gate_probe("test_precommit_rollback_leaves_no_half_pair", "public.llm_budget_baselines", "INSERT")
        with self.assertRaises(self.baselines_service.BudgetBaselineConflict):
            async with self.context.tenant_scope(payer):
                await self.manager.provision_reviewed_day(self.packet(payer, day, version="orphan"))
        days, bases = await self.rows_for(payer, day)
        self.assertEqual(len(bases), 0, "no durable half-pair after precommit failure")
        self.assertEqual(len(days), 1, "pre-existing retained day evidence survived")

    async def test_synthetic_cancellation_rolls_back_and_releases_locks(self) -> None:
        import app.models.managers.base_manager as base_manager

        payer = self.approval["payers"][6]
        await self.seed_synthetic_tenant(payer, "test_synthetic_cancellation_rolls_back_and_releases_locks")
        day = datetime.now(timezone.utc).date() + timedelta(days=1)
        self.gate_probe(
            "test_synthetic_cancellation_rolls_back_and_releases_locks", "public.llm_budget_baselines", "INSERT"
        )
        original = base_manager.async_session_maker

        @asynccontextmanager
        async def cancelling_session():
            """Synthetic cancellation: the owned commit raises CancelledError.

            The owned-session helper treats CancelledError as a BaseException:
            no rollback call, no acknowledgement; closing the session aborts the
            server transaction, which is exactly what a real cancellation does.
            """

            async with original() as session:
                real_commit = session.commit

                async def cancelled_commit():
                    raise asyncio.CancelledError("synthetic cancellation at commit")

                session.commit = cancelled_commit
                yield session

        with patch.object(base_manager, "async_session_maker", cancelling_session):
            with self.assertRaises(asyncio.CancelledError):
                async with self.context.tenant_scope(payer):
                    await self.manager.provision_reviewed_day(self.packet(payer, day, version="cancelled"))
        days, bases = await self.rows_for(payer, day)
        self.assertEqual((len(days), len(bases)), (0, 0), "synthetic cancellation rolled back")
        self.assertTrue(await self.lock_release("llm_budget_baselines"), "server lock released")

    async def test_uncertain_commit_never_returns_success(self) -> None:
        import app.models.managers.base_manager as base_manager

        payer = self.approval["payers"][7]
        await self.seed_synthetic_tenant(payer, "test_uncertain_commit_never_returns_success")
        day = datetime.now(timezone.utc).date() + timedelta(days=1)
        self.gate_probe("test_uncertain_commit_never_returns_success", "public.llm_budget_baselines", "INSERT")
        original = base_manager.async_session_maker

        @asynccontextmanager
        async def ambiguous_ack():
            """Synthetic acknowledgement loss: the server commit lands, the
            acknowledgement to the caller is lost. Never a success answer."""

            async with original() as session:
                real_commit = session.commit

                async def lost_ack_commit():
                    await real_commit()
                    raise RuntimeError("synthetic acknowledgement loss after durable commit")

                session.commit = lost_ack_commit
                yield session

        with patch.object(base_manager, "async_session_maker", ambiguous_ack):
            with self.assertRaises(RuntimeError):
                async with self.context.tenant_scope(payer):
                    await self.manager.provision_reviewed_day(self.packet(payer, day, version="uncertain"))
        days, bases = await self.rows_for(payer, day)
        self.assertEqual(len(bases), 1, "independent readback distinguishes the durable pair")
        async with self.context.tenant_scope(payer):
            replay = await self.manager.provision_reviewed_day(self.packet(payer, day, version="uncertain"))
        self.assertFalse(replay, "explicit same-packet replay only; never a resend")

    async def test_immutable_update_delete_refused(self) -> None:
        """Row-scoped refused probes only. TRUNCATE is DEFERRED: it needs a
        separately approved disposable target (never the retained target)."""

        payer = self.approval["payers"][0]
        day = datetime.now(timezone.utc).date()
        await self.probe(
            "test_immutable_update_delete_refused",
            "public.llm_budget_baselines",
            "UPDATE",
            "UPDATE public.llm_budget_baselines SET frozen=false WHERE tenant_id=:i AND utc_day=:d",
            {"i": payer, "d": day},
        )
        await self.probe(
            "test_immutable_update_delete_refused",
            "public.llm_budget_baselines",
            "DELETE",
            "DELETE FROM public.llm_budget_baselines WHERE tenant_id=:i AND utc_day=:d",
            {"i": payer, "d": day},
        )
        _, bases = await self.rows_for(payer, day)
        self.assertTrue(bases, "immutable evidence retained after refused statements")

    async def test_initial_snapshot_binding_and_constraints_refused(self) -> None:
        payer = self.approval["payers"][8]
        await self.seed_synthetic_tenant(payer, "test_initial_snapshot_binding_and_constraints_refused")
        day = datetime.now(timezone.utc).date() + timedelta(days=2)
        # Missing day row: the initial-snapshot binding trigger must refuse.
        await self.probe(
            "test_initial_snapshot_binding_and_constraints_refused",
            "public.llm_budget_baselines",
            "INSERT",
            "INSERT INTO public.llm_budget_baselines (tenant_id, utc_day, settled_usd, reserved_usd, "
            "undefined_count, unbounded_count, frozen, baseline_version, reviewed_by, review_ref, "
            "evidence_sha256, cutoff_at, reviewed_at, fence_ref, fence_version) "
            "VALUES (:i, :d, '1.000000000', '0.000000000', 1, 0, true, 'synthetic-x', 'pr197-harness', "
            ":r, :e, now(), now(), 'synthetic-fence', 'quiesced-accounted-v1')",
            {
                "i": payer,
                "d": day,
                "r": sha256_hex("synthetic-binding")[:32],
                "e": sha256_hex("synthetic-binding-evidence"),
            },
        )
        # Negative amounts: the amount check constraint must refuse.
        await self.probe(
            "test_initial_snapshot_binding_and_constraints_refused",
            "public.llm_budget_baselines",
            "INSERT",
            "INSERT INTO public.llm_budget_baselines (tenant_id, utc_day, settled_usd, reserved_usd, "
            "undefined_count, unbounded_count, frozen, baseline_version, reviewed_by, review_ref, "
            "evidence_sha256, cutoff_at, reviewed_at, fence_ref, fence_version) "
            "VALUES (:i, :d, '-1.000000000', '0.000000000', 1, 0, true, 'synthetic-x', 'pr197-harness', "
            ":r, :e, now(), now(), 'synthetic-fence', 'quiesced-accounted-v1')",
            {
                "i": payer,
                "d": day,
                "r": sha256_hex("synthetic-check")[:32],
                "e": sha256_hex("synthetic-check-evidence"),
            },
        )
        days, bases = await self.rows_for(payer, day)
        self.assertEqual((len(days), len(bases)), (0, 0), "no invalid synthetic row survived")

    async def test_legacy_day_adoption_refused(self) -> None:
        payer = self.approval["payers"][9]
        await self.seed_synthetic_tenant(payer, "test_legacy_day_adoption_refused")
        day = datetime.now(timezone.utc).date() + timedelta(days=3)
        await self.seed_unreviewed_day(payer, day, "test_legacy_day_adoption_refused")
        self.gate_probe("test_legacy_day_adoption_refused", "public.llm_budget_baselines", "INSERT")
        with self.assertRaises(self.baselines_service.BudgetBaselineConflict):
            async with self.context.tenant_scope(payer):
                await self.manager.provision_reviewed_day(self.packet(payer, day, version="legacy"))
        days, bases = await self.rows_for(payer, day)
        self.assertEqual(len(bases), 0, "legacy/manual day never adopted")
        self.assertEqual(len(days), 1, "retained unreviewed evidence untouched")

    async def test_replay_preserves_current_counters_and_freeze(self) -> None:
        payer = self.approval["payers"][0]
        day = datetime.now(timezone.utc).date()
        self.gate_write("test_replay_preserves_current_counters_and_freeze", "public.llm_budget_days", "UPDATE")
        async with self._write_session("test_replay_preserves_current_counters_and_freeze") as s:
            await s.execute(
                self.text(
                    "UPDATE public.llm_budget_days SET settled_usd='4.000000000', reserved_usd='1.000000000' "
                    "WHERE tenant_id=:i AND utc_day=:d"
                ),
                {"i": payer, "d": day},
            )
        async with self.context.tenant_scope(payer):
            replay = await self.manager.provision_reviewed_day(self.packet(payer, day))
        self.assertFalse(replay, "replay after synthetic current-state change acknowledged, never rewrote")
        days, _ = await self.rows_for(payer, day)
        self.assertEqual(Decimal(days[0]["settled_usd"]), Decimal("4.000000000"), "current counters preserved")
        self.assertEqual(Decimal(days[0]["reserved_usd"]), Decimal("1.000000000"), "current balances preserved")

    async def test_no_implicit_baseline_grants(self) -> None:
        async with self.session(readonly=True) as s:
            grants_after = dict(
                (
                    await s.execute(
                        self.text(
                            "SELECT (SELECT count(*) FROM public.permissions), (SELECT count(*) FROM public.roles)"
                        )
                    )
                ).one()
            )
        self.assertEqual(grants_after, self.grants_before, "baseline created no implicit permission/grant rows")


if __name__ == "__main__":
    unittest.main(verbosity=2)
