"""Actual source with explicit import/transaction doubles; no DB/app/provider.

Only the new baseline package is exercised. Existing accepted reservation tests
are not rerun or relabelled. DDL capture is NOT migration execution or PG proof.
"""

import ast
import asyncio
import copy
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from source_import_isolation import load_isolated_source
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKeyConstraint, Integer, MetaData, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[1]
D = Decimal
SCOPE = {"id": 7}


class TenantError(RuntimeError):
    pass


CONTEXT = SimpleNamespace(current_tenant_id=lambda: SCOPE["id"], TenantContextError=TenantError)
POLICY = load_isolated_source("_baseline_policy", ROOT / "app/services/ai/budget_admission.py")
PACKET = load_isolated_source(
    "app.services.ai._reviewed_baseline",
    ROOT / "app/services/ai/budget_baselines.py",
    imports={"app.services.ai.budget_admission": POLICY},
)


class BaselineModelBase(DeclarativeBase):
    __allow_unmapped__ = True
    metadata = MetaData(schema="baseline_review")


class Tenant(BaselineModelBase):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    is_active = Column(Boolean)


MODEL_IMPORTS = {
    "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="baseline_review")),
    "app.core.decorators": SimpleNamespace(app_label=lambda name: lambda cls: cls),
    "app.models.base": SimpleNamespace(Base=BaselineModelBase, TenantScopedMixin=type("Scoped", (), {})),
}
DAYS = load_isolated_source(
    "app.models.llm._baseline_days",
    ROOT / "app/models/llm/llm_budget.py",
    imports=MODEL_IMPORTS,
)
MODELS = load_isolated_source(
    "app.models.llm._reviewed_baseline_model",
    ROOT / "app/models/llm/llm_budget_baseline.py",
    imports=MODEL_IMPORTS,
)
GUARDS = {
    ("llm_budget_baselines_immutable", 27, "reject_llm_budget_baseline_mutation"),
    ("llm_budget_baselines_no_truncate", 34, "reject_llm_budget_baseline_mutation"),
    ("llm_budget_baselines_day_binding", 7, "check_llm_budget_baseline_day"),
}


def packet(**changes):
    # Historical selected day: validation must not infer today's snapshot.
    result = dict(
        tenant_id=7,
        utc_day=date(2026, 10, 1),
        settled_usd=D("0.120000001"),
        reserved_usd=D("0.5"),
        undefined_count=2,
        unbounded_count=1,
        frozen=True,
        baseline_version="review-v1",
        reviewed_by="operator-1",
        review_ref="review:001",
        evidence_sha256="a" * 64,
        cutoff_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
        reviewed_at=datetime(2026, 10, 1, 13, tzinfo=timezone.utc),
        fence_ref="fence:001",
        fence_version=PACKET.FENCE_VERSION,
    )
    result.update(changes)
    return result


class ValidationTests(unittest.TestCase):
    def test_exact_complete_snapshot_is_preserved(self):
        value = packet()
        self.assertEqual(PACKET.validate_baseline(value), value)
        self.assertIsNot(PACKET.validate_baseline(value), value)

    def test_missing_fields_never_supply_zero_or_defaults(self):
        for field in PACKET.BASELINE_FIELDS:
            value = packet()
            del value[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                PACKET.validate_baseline(value)

    def test_unknown_fields_and_non_mapping_rejected(self):
        for value in (packet(projection_usd=0), [], None):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                PACKET.validate_baseline(value)

    def test_money_rejects_float_bool_nonfinite_negative_overflow_rounding(self):
        for field in ("settled_usd", "reserved_usd"):
            for amount in (0.0, True, None, D("NaN"), D("Infinity"), D("-1"), D("1e9"), D("1e-10")):
                with self.subTest(field=field, amount=str(amount)), self.assertRaises(ValueError):
                    PACKET.validate_baseline(packet(**{field: amount}))

    def test_money_exact_boundaries_and_explicit_zero_retained(self):
        for amount in (D(0), D("1e-9"), POLICY.MAX_USD):
            self.assertEqual(PACKET.validate_baseline(packet(settled_usd=amount))["settled_usd"], amount)

    def test_liability_counts_are_explicit_int32_not_bools(self):
        for field in ("undefined_count", "unbounded_count"):
            for count in (True, -1, 1.0, None, 2147483648):
                with self.subTest(field=field, count=count), self.assertRaises(ValueError):
                    PACKET.validate_baseline(packet(**{field: count}))
            self.assertEqual(PACKET.validate_baseline(packet(**{field: 2147483647}))[field], 2147483647)

    def test_initial_freeze_is_mandatory_no_activation(self):
        for value in (False, 1, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PACKET.validate_baseline(packet(frozen=value))

    def test_payer_is_explicit_positive_int32(self):
        for value in (True, None, 0, -1, 2147483648, "7"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PACKET.validate_baseline(packet(tenant_id=value))

    def test_version_alone_is_not_review_provenance(self):
        for field in ("baseline_version", "reviewed_by", "review_ref", "fence_ref"):
            for value in ("", "provider prose", "https://private.invalid/secret", "a" * 65, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    PACKET.validate_baseline(packet(**{field: value}))

    def test_canonical_digest_and_exact_fence_attestation_required(self):
        for field, values in (
            ("evidence_sha256", (None, "a" * 63, "A" * 64, "z" * 64)),
            ("fence_version", (None, "v1", "quiesced-only-v1")),
        ):
            for value in values:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    PACKET.validate_baseline(packet(**{field: value}))

    def test_day_is_date_not_datetime_or_future_rollover(self):
        for value in (datetime(2026, 10, 1), "2026-10-01", date.max, date(2099, 1, 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PACKET.validate_baseline(packet(utc_day=value))

    def test_time_requires_aware_utc_and_ordered_day_prefix(self):
        for field in ("cutoff_at", "reviewed_at"):
            for value in (
                datetime(2026, 10, 1, 12),
                "2026-10-01",
                None,
                datetime(2026, 10, 1, 12, tzinfo=timezone(timedelta(hours=3))),
            ):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    PACKET.validate_baseline(packet(**{field: value}))
        for changes in (
            dict(cutoff_at=datetime(2026, 9, 30, 23, tzinfo=timezone.utc)),
            dict(cutoff_at=datetime(2026, 10, 2, 0, 0, 1, tzinfo=timezone.utc)),
            dict(reviewed_at=datetime(2026, 10, 1, 11, tzinfo=timezone.utc)),
            dict(reviewed_at=datetime.now(timezone.utc) + timedelta(days=1)),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PACKET.validate_baseline(packet(**changes))

    def test_exact_day_end_cutoff_allowed_without_rollover(self):
        stamp = datetime(2026, 10, 2, tzinfo=timezone.utc)
        value = PACKET.validate_baseline(packet(cutoff_at=stamp, reviewed_at=stamp))
        self.assertEqual(value["utc_day"], date(2026, 10, 1))


class OwnedDouble:
    def __init__(self, model):
        self.model = model
        self.tenant = SimpleNamespace(is_active=True)
        self.day = None
        self.baseline = None
        self.other_day = packet(utc_day=date(2026, 9, 30))
        self.guards = set(GUARDS)
        self.statements = []
        self.pending = []
        self.durable = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_at = None
        self.collision_at = None
        self.commit_error = None
        self.rollback_error = False

    async def execute(self, statement, params=None):
        self.statements.append((statement, params))
        if getattr(statement, "is_insert", False):
            name = statement.table.name
            if self.fail_at == name:
                raise IntegrityError("insert", {}, RuntimeError("unrelated constraint"))
            if self.fail_at == "cancel":
                raise asyncio.CancelledError()
            if self.collision_at == name:
                return SimpleNamespace(scalar_one_or_none=lambda: None)
            data = statement.compile().params
            self.pending.append((name, data))
            return SimpleNamespace(scalar_one_or_none=lambda: 1)
        if getattr(statement, "is_select", False):
            entity = statement.column_descriptions[0]["entity"]
            value = self.tenant if entity is Tenant else self.day if entity is DAYS.LLMBudgetDay else self.baseline
            return SimpleNamespace(scalar_one_or_none=lambda: value)
        return SimpleNamespace(all=lambda: list(self.guards))

    @asynccontextmanager
    async def _get_owned_session(self):
        try:
            yield self
            if self.commit_error == "before":
                raise RuntimeError("synthetic failure before commit")
            for name, value in self.pending:
                if name == "llm_budget_days":
                    self.day = SimpleNamespace(**value)
                else:
                    self.baseline = SimpleNamespace(**value)
            self.durable.extend(self.pending)
            self.pending.clear()
            self.commits += 1
            if self.commit_error == "after":
                raise RuntimeError("synthetic lost acknowledgement")
        except BaseException:
            self.pending.clear()
            self.rollbacks += 1
            if self.rollback_error:
                raise RuntimeError("synthetic rollback failure")
            raise


MANAGER = load_isolated_source(
    "app.models.managers._reviewed_baseline_manager",
    ROOT / "app/models/managers/llm_budget_manager.py",
    imports={
        "app.core.tenant_context": CONTEXT,
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=OwnedDouble),
        "app.models.managers.llm_dispatch_receipt_manager": SimpleNamespace(LLMDispatchReceiptManager=None),
        "app.services.ai.budget_baselines": PACKET,
        # Existing manager initializer still uses dev's supported analysis alias.
        "app.models.analysis.llm_budget": DAYS,
        "app.models.llm.llm_budget": DAYS,
        "app.models.llm.llm_budget_baseline": MODELS,
        "app.models.tenancy.tenant": SimpleNamespace(Tenant=Tenant),
    },
)


class ProvisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCOPE["id"] = 7
        self.manager = MANAGER.LLMBudgetManager()
        self.value = packet()

    async def provision(self):
        return await self.manager.provision_reviewed_day(self.value)

    async def test_one_owned_commit_binds_snapshot_and_provenance(self):
        self.assertTrue(await self.provision())
        self.assertEqual(self.manager.commits, 1)
        self.assertEqual([name for name, _ in self.manager.durable], ["llm_budget_days", "llm_budget_baselines"])
        self.assertEqual(vars(self.manager.day), {f: self.value[f] for f in PACKET.DAY_FIELDS})
        self.assertEqual(vars(self.manager.baseline), self.value)

    async def test_payer_then_day_locks_match_admission_and_scope_each_read(self):
        await self.provision()
        locks = [s for s, _ in self.manager.statements if getattr(s, "_for_update_arg", None) is not None]
        self.assertEqual([s.column_descriptions[0]["entity"] for s in locks], [Tenant, DAYS.LLMBudgetDay])
        reads = [s for s, _ in self.manager.statements if getattr(s, "is_select", False)]
        for query in reads:
            sql = str(query.compile(dialect=postgresql.dialect()))
            self.assertIn("WHERE", sql)
            self.assertIn(7, query.compile().params.values())
        for query in reads[1:]:
            self.assertIn(self.value["utc_day"], query.compile().params.values())

    async def test_inserts_suppress_only_named_identity_never_update(self):
        await self.provision()
        inserts = [s for s, _ in self.manager.statements if getattr(s, "is_insert", False)]
        for statement, constraint in zip(
            inserts, ("uq_llm_budget_days_payer_day", "uq_llm_budget_baselines_payer_day")
        ):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            self.assertIn("ON CONFLICT ON CONSTRAINT " + constraint + " DO NOTHING", sql)
            self.assertNotIn("DO UPDATE", sql)

    async def test_exact_replay_does_not_restore_counters_or_unfreeze(self):
        await self.provision()
        self.manager.day.settled_usd = D("7")
        self.manager.day.reserved_usd = D("9")
        self.manager.day.undefined_count = 11
        self.manager.day.unbounded_count = 12
        self.manager.day.frozen = False  # synthetic later authorized state, NOT an API in this slice
        day_before = copy.deepcopy(vars(self.manager.day))
        durable_before = copy.deepcopy(self.manager.durable)
        self.assertFalse(await self.provision())
        self.assertEqual(vars(self.manager.day), day_before)
        self.assertEqual(self.manager.durable, durable_before)

    async def test_every_initial_packet_field_conflicts_without_any_rewrite(self):
        await self.provision()
        original = copy.deepcopy(self.value)
        day_before = copy.deepcopy(vars(self.manager.day))
        for field in PACKET.BASELINE_FIELDS:
            if field in ("tenant_id", "utc_day", "frozen", "fence_version"):
                continue  # separately tested scope/day/freeze/attestation validation
            value = original[field]
            changed = (
                value + timedelta(seconds=1)
                if isinstance(value, datetime)
                else (
                    value + 1
                    if isinstance(value, (int, Decimal))
                    else "b" * 64 if field == "evidence_sha256" else value + "-2"
                )
            )
            self.value = {**original, field: changed}
            with self.subTest(field=field), self.assertRaises(PACKET.BudgetBaselineConflict):
                await self.provision()
            self.assertEqual(vars(self.manager.day), day_before)
            self.assertEqual(vars(self.manager.baseline), original)
        self.assertEqual(len(self.manager.durable), 2)

    async def test_legacy_day_never_adopted_even_matching_snapshot(self):
        self.manager.day = SimpleNamespace(**{f: self.value[f] for f in PACKET.DAY_FIELDS})
        before = copy.deepcopy(vars(self.manager.day))
        with self.assertRaises(PACKET.BudgetBaselineConflict):
            await self.provision()
        self.assertEqual(vars(self.manager.day), before)
        self.assertEqual(self.manager.durable, [])

    async def test_orphan_provenance_fails_closed(self):
        self.manager.baseline = SimpleNamespace(**self.value)
        with self.assertRaises(PACKET.BudgetBaselineConflict):
            await self.provision()
        self.assertEqual(self.manager.durable, [])

    async def test_unrelated_insert_errors_rollback_both_rows(self):
        for table in ("llm_budget_days", "llm_budget_baselines"):
            self.manager = MANAGER.LLMBudgetManager()
            self.manager.fail_at = table
            with self.subTest(table=table), self.assertRaises(IntegrityError):
                await self.provision()
            self.assertIsNone(self.manager.day)
            self.assertIsNone(self.manager.baseline)
            self.assertEqual(self.manager.durable, [])
            self.assertEqual(self.manager.rollbacks, 1)

    async def test_named_insert_collision_never_adopted_or_reported_success(self):
        for table in ("llm_budget_days", "llm_budget_baselines"):
            self.manager = MANAGER.LLMBudgetManager()
            self.manager.collision_at = table
            with self.subTest(table=table), self.assertRaises(PACKET.BudgetBaselineConflict):
                await self.provision()
            self.assertEqual(self.manager.durable, [])
            self.assertIsNone(self.manager.day)

    async def test_commit_uncertainty_never_reports_success_then_exact_replay_only(self):
        for phase in ("before", "after"):
            self.manager = MANAGER.LLMBudgetManager()
            self.manager.commit_error = phase
            with self.subTest(phase=phase), self.assertRaises(RuntimeError):
                await self.provision()
            self.assertEqual(len(self.manager.durable), 2 if phase == "after" else 0)
            self.manager.commit_error = None
            if phase == "after":
                self.assertFalse(await self.provision())
                self.assertEqual(len(self.manager.durable), 2)

    async def test_replay_commit_error_is_not_false_success(self):
        await self.provision()
        self.manager.commit_error = "before"
        with self.assertRaises(RuntimeError):
            await self.provision()
        self.assertEqual(len(self.manager.durable), 2)

    async def test_rollback_failure_and_cancellation_propagate(self):
        self.manager.fail_at = "llm_budget_baselines"
        self.manager.rollback_error = True
        with self.assertRaisesRegex(RuntimeError, "rollback failure"):
            await self.provision()
        self.manager = MANAGER.LLMBudgetManager()
        self.manager.fail_at = "cancel"
        with self.assertRaises(asyncio.CancelledError):
            await self.provision()
        self.assertEqual(self.manager.durable, [])

    async def test_missing_payer_or_scope_mismatch_rejected_before_sql(self):
        for scope in (None, True, 0, 2147483648, 8):
            SCOPE["id"] = scope
            with self.subTest(scope=scope), self.assertRaises(TenantError):
                await self.provision()
            self.assertEqual(self.manager.statements, [])

    async def test_inactive_or_absent_payer_never_inserts(self):
        for tenant in (None, SimpleNamespace(is_active=False)):
            self.manager.tenant = tenant
            with self.assertRaises(TenantError):
                await self.provision()
            self.assertEqual(self.manager.durable, [])

    async def test_missing_each_guard_fails_closed_including_replay(self):
        for guard in GUARDS:
            self.manager = MANAGER.LLMBudgetManager()
            self.manager.guards.remove(guard)
            with self.subTest(guard=guard), self.assertRaises(PACKET.BudgetBaselineUnavailable):
                await self.provision()
            self.assertEqual(self.manager.durable, [])
        self.manager = MANAGER.LLMBudgetManager()
        await self.provision()
        self.manager.guards.clear()
        with self.assertRaises(PACKET.BudgetBaselineUnavailable):
            await self.provision()

    async def test_no_external_session_or_auto_rollover_liability_reset(self):
        before = copy.deepcopy(self.manager.other_day)
        with self.assertRaises(TypeError):
            await self.manager.provision_reviewed_day(self.value, session=self.manager)
        await self.provision()
        self.assertEqual(self.manager.other_day, before)
        self.assertEqual(self.manager.day.utc_day, self.value["utc_day"])
        self.assertTrue(self.manager.day.frozen)


class DefinitionTests(unittest.TestCase):
    def setUp(self):
        self.tables = []
        self.ddl = []
        dialect = postgresql.dialect()
        op = SimpleNamespace(
            create_table=lambda name, *args, **kw: self.tables.append((name, args, kw)),
            execute=self.ddl.append,
            get_bind=lambda: SimpleNamespace(dialect=dialect),
        )
        self.migration = load_isolated_source(
            "_baseline_migration_capture",
            ROOT / "migrations/versions/0097_add_reviewed_budget_baselines.py",
            imports={
                "alembic": SimpleNamespace(op=op),
                "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="baseline_review")),
            },
        )
        self.migration.upgrade()  # capture double only; no connection, no Alembic runner

    def test_one_additive_table_parent_and_no_baseline_dml(self):
        self.assertEqual(self.migration.down_revision, "0096")
        self.assertEqual([t[0] for t in self.tables], ["llm_budget_baselines"])
        self.assertEqual(self.tables[0][2], {"schema": "baseline_review"})
        self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE", "DROP", "ALTER")) for sql in self.ddl))

    def test_model_definition_column_check_fk_and_unique_parity(self):
        table = MODELS.LLMBudgetBaseline.__table__
        columns = [c for c in self.tables[0][1] if isinstance(c, Column)]
        self.assertEqual(
            [(c.name, str(c.type), c.nullable) for c in columns],
            [(c.name, str(c.type), c.nullable) for c in table.columns],
        )
        checks = {c.name: str(c.sqltext) for c in self.tables[0][1] if isinstance(c, CheckConstraint)}
        self.assertEqual(checks, {c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)})
        for kind in (ForeignKeyConstraint, UniqueConstraint):
            expected = [c for c in table.constraints if isinstance(c, kind)]
            actual = [c for c in self.tables[0][1] if isinstance(c, kind)]
            self.assertEqual([c.name for c in actual], [c.name for c in expected])
        self.assertIn("ON DELETE RESTRICT", str(CreateTable(table).compile(dialect=postgresql.dialect())))
        self.assertIn("AT TIME ZONE 'UTC'", checks["ck_llm_budget_baselines_times"])

    def test_immutable_triggers_initial_snapshot_binding_and_schema_qualification(self):
        ddl = "\n".join(self.ddl)
        self.assertEqual(len(self.ddl), 5)
        self.assertIn("BEFORE UPDATE OR DELETE ON baseline_review.llm_budget_baselines", ddl)
        self.assertIn("BEFORE TRUNCATE ON baseline_review.llm_budget_baselines", ddl)
        self.assertIn("BEFORE INSERT ON baseline_review.llm_budget_baselines", ddl)
        self.assertIn("FROM baseline_review.llm_budget_days", ddl)
        self.assertIn("FOR UPDATE", ddl)
        for field in PACKET.DAY_FIELDS[2:]:
            self.assertIn("day_row." + field + " IS DISTINCT FROM NEW." + field, ddl)
        self.assertNotIn("public.", ddl)

    def test_guard_probe_uses_bound_schema_and_enabled_exact_trigger_metadata(self):
        source = (ROOT / "app/models/managers/llm_budget_manager.py").read_text()
        start = source.index("async def provision_reviewed_day")
        source = source[start : source.index("async def reserve_receipt", start)]
        for token in (
            "pn.nspname=:schema",
            "t.tgenabled IN ('O', 'A')",
            "t.tgqual IS NULL",
            "t.tgnargs=0",
            "t.tgattr::text=''",
            "session_replication_role",
            "LLMBudgetBaseline.__table__.schema",
        ):
            self.assertIn(token, source)

    def test_downgrade_refuses_even_empty_no_deletion(self):
        with self.assertRaisesRegex(RuntimeError, "preservation plan"):
            self.migration.downgrade()

    def test_clients_and_http_remain_unwired(self):
        for folder in ("app/api", "app/web", "app/agent", "app/services/ai", "cli"):
            for path in (ROOT / folder).rglob("*.py"):
                if path.name in ("budget_admission.py", "budget_baselines.py"):
                    continue
                source = path.read_text()
                for name in ("provision_reviewed_day", "reserve_dispatch_receipt", "reserve_receipt"):
                    self.assertNotIn(name, source, str(path))

    def test_no_baseline_activation_or_mutable_table_edits_in_manager(self):
        tree = ast.parse((ROOT / "app/models/managers/llm_budget_manager.py").read_text())
        method = next(
            n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "provision_reviewed_day"
        )
        for node in ast.walk(method):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("update", "delete", "create_all", "reserve_receipt"))
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                self.assertFalse(any(isinstance(t, ast.Attribute) for t in targets))
