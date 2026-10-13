"""Actual source, isolated doubles/DDL capture only; no app startup or database.

Run via Kilo/test-runner/pare-test with --noconftest and explicit positional path.
These checks are NOT PostgreSQL concurrency, bootstrap or migration evidence.
"""

import copy
import os
import subprocess
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source
from sqlalchemy import Boolean, Column, Integer, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase

ROOT = Path(__file__).resolve().parents[1]
SCOPE = {"id": 7}
D = Decimal


class TenantError(RuntimeError):
    pass


TENANT_CONTEXT = SimpleNamespace(current_tenant_id=lambda: SCOPE["id"], TenantContextError=TenantError)
JOURNAL = load_isolated_source("_budget_tariffs", ROOT / "app/services/ai/attempt_journal.py")
RECEIPTS = load_isolated_source(
    "app.services.ai._budget_receipt_helpers",
    ROOT / "app/services/ai/dispatch_receipts.py",
    imports={"app.services.ai.attempt_journal": JOURNAL},
)


POLICY = load_isolated_source(
    "app.services.ai._budget_policy",
    ROOT / "app/services/ai/budget_admission.py",
    imports={"app.core.tenant_context": TENANT_CONTEXT, "app.services.ai.dispatch_receipts": RECEIPTS},
)
CAP_SETTINGS = SimpleNamespace(AGENT_DAILY_COST_LIMIT=5.0)
RESOLVER = load_isolated_source(
    "_budget_cap_resolver",
    ROOT / "app/services/tenancy/resolver.py",
    imports={
        "app.core.config": SimpleNamespace(settings=CAP_SETTINGS),
        "app.core.tenant_context": SimpleNamespace(tenant_scope=None),
        "app.models.managers.tenant_manager": SimpleNamespace(
            tenant_channels=None, tenant_invites=None, tenant_users=None, tenants=None
        ),
    },
)


class TestBase(DeclarativeBase):
    __allow_unmapped__ = True
    metadata = MetaData(schema="budget_review")


class Tenant(TestBase):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    is_active = Column(Boolean)


MODEL_IMPORTS = {
    "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="budget_review")),
    "app.core.decorators": SimpleNamespace(app_label=lambda name: lambda cls: cls),
    "app.models.base": SimpleNamespace(Base=TestBase, TenantScopedMixin=type("Scoped", (), {})),
}
RECEIPT_MODEL = load_isolated_source(
    "app.models.analysis._budget_receipt_model",
    ROOT / "app/models/analysis/llm_dispatch_receipt.py",
    imports=MODEL_IMPORTS,
)
MODELS = load_isolated_source(
    "app.models.analysis._budget_models",
    ROOT / "app/models/analysis/llm_budget.py",
    imports=MODEL_IMPORTS,
)


def receipt(**changes):
    now = datetime.now(timezone.utc)
    model = SimpleNamespace(
        id=4,
        model_id="model",
        provider=SimpleNamespace(id=2, name="provider"),
        input_cost_per_1k=D("0.001"),
        output_cost_per_1k=D("0.002"),
    )
    result = RECEIPTS.build_dispatch_receipt(
        model, tenant_id=7, purpose="analysis", stage="text", dispatch_id="a" * 32, prepared_at=now
    )
    result.update(changes)
    return result


class PolicyTests(unittest.TestCase):
    def test_representability_never_rounds_or_accepts_projection_float(self):
        for value in (0.1, True, D("NaN"), D("Infinity"), D("-1"), D("0.0000000001"), D("1000000000")):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                POLICY.exact_usd(value)
        self.assertEqual(POLICY.exact_usd(D("0.000000001")), D("0.000000001"))

    def test_known_paid_requires_positive_versioned_bound(self):
        for amount, version in ((D(0), "reviewed-v1"), (D(1), None), (D(1), "provider prose"), (None, "v1")):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                POLICY.reservation_plan(receipt(), amount, version)

    def test_undefined_zero_is_explicit_policy_not_free(self):
        plan = POLICY.reservation_plan({"tariff_state": "undefined"}, D(0), None)
        self.assertEqual(plan["ceiling_state"], "undefined")
        self.assertEqual(plan["undefined_policy_version"], POLICY.UNDEFINED_POLICY_VERSION)
        POLICY.require_capacity(D(1), D(2), D(5), 1, plan)
        with self.assertRaises(ValueError):
            POLICY.reservation_plan({"tariff_state": "undefined"}, None, None)

    def test_free_basis_is_explicit_zero(self):
        plan = POLICY.reservation_plan({"tariff_state": "free"}, D(0), None)
        self.assertEqual(plan["ceiling_state"], "bounded")
        with self.assertRaises(ValueError):
            POLICY.reservation_plan({"tariff_state": "free"}, D(1), None)

    def test_exact_cap_boundary_and_no_overshoot(self):
        plan = POLICY.reservation_plan(receipt(), D("0.4"), "reviewed-v1")
        POLICY.require_capacity(D(1), D("0.3"), D("0.3"), 0, plan)
        with self.assertRaises(POLICY.BudgetAdmissionDenied):
            POLICY.require_capacity(D(1), D("0.300000001"), D("0.3"), 0, plan)

    def test_known_missing_bound_is_not_zero_even_when_unlimited(self):
        plan = POLICY.reservation_plan(receipt(), None, None)
        self.assertIsNone(plan["ceiling_usd"])
        self.assertEqual(plan["ceiling_state"], "unbounded")
        POLICY.require_capacity(None, D(0), D(0), 0, plan)
        with self.assertRaises(POLICY.BudgetAdmissionDenied):
            POLICY.require_capacity(D(1), D(0), D(0), 0, plan)

    def test_unlimited_still_refuses_storage_overflow(self):
        plan = POLICY.reservation_plan(receipt(), D(1), "reviewed-v1")
        with self.assertRaises(POLICY.BudgetAdmissionUnavailable):
            POLICY.require_capacity(None, D(0), POLICY.MAX_USD, 0, plan)


class CapPolicyTests(unittest.TestCase):
    def test_existing_resolver_semantics_are_preserved(self):
        for effective, column, global_cap, expected in (
            (2.0, 20.0, 5.0, 2.0),
            (1.0, 1.0, 5.0, 1.0),
            (None, 3.0, 5.0, 3.0),
            (None, 0.0, 5.0, 5.0),
            (None, 0.0, 0.0, 0.0),
            (0.0, 0.0, 5.0, 5.0),
        ):
            with self.subTest(effective=effective, column=column, global_cap=global_cap):
                CAP_SETTINGS.AGENT_DAILY_COST_LIMIT = global_cap
                tenant = SimpleNamespace(
                    daily_cost_limit=column, effective_limits=lambda: {"daily_cost_limit": effective}
                )
                self.assertEqual(RESOLVER.resolved_daily_cost_limit(tenant), expected)


class FreshBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCOPE["id"] = 7
        self.model = SimpleNamespace(
            id=4,
            model_id="model",
            provider=SimpleNamespace(id=2, name="provider"),
            input_cost_per_1k=D("0.001"),
            output_cost_per_1k=D("0.002"),
        )

    async def test_fresh_id_and_immutable_receipt_after_owned_ack(self):
        budget = SimpleNamespace(reserve_receipt=AsyncMock(return_value=True))
        args = dict(purpose="analysis", stage="text", ceiling_usd=D(1), bound_version="reviewed-v1", budget=budget)
        first = await POLICY.reserve_dispatch_receipt(self.model, **args)
        second = await POLICY.reserve_dispatch_receipt(self.model, **args)
        self.assertNotEqual(first["dispatch_id"], second["dispatch_id"])
        self.assertEqual(first["tenant_id"], 7)
        with self.assertRaises(TypeError):
            first["tenant_id"] = 8

    async def test_collision_or_commit_uncertainty_never_returns_receipt(self):
        for response in (False, RuntimeError("ack lost")):
            method = (
                AsyncMock(side_effect=response) if isinstance(response, Exception) else AsyncMock(return_value=response)
            )
            with self.assertRaises(RuntimeError):
                await POLICY.reserve_dispatch_receipt(
                    self.model,
                    purpose="analysis",
                    stage="text",
                    ceiling_usd=D(1),
                    bound_version="reviewed-v1",
                    budget=SimpleNamespace(reserve_receipt=method),
                )

    async def test_missing_payer_never_calls_storage(self):
        SCOPE["id"] = None
        method = AsyncMock()
        with self.assertRaises(TenantError):
            await POLICY.reserve_dispatch_receipt(
                self.model,
                purpose="analysis",
                stage="text",
                ceiling_usd=D(1),
                bound_version="reviewed-v1",
                budget=SimpleNamespace(reserve_receipt=method),
            )
        method.assert_not_awaited()


class BaseDouble:
    def __init__(self, model):
        self.model = model
        self.tenant = SimpleNamespace(is_active=True)
        self.budget = SimpleNamespace(
            settled_usd=D(0),
            reserved_usd=D(0),
            undefined_count=0,
            unbounded_count=0,
            frozen=False,
            baseline_version="reviewed-baseline-v1",
        )
        self.existing = None
        self.older = None
        self.stored_receipt = None
        self.receipt_id = 1
        self.reservation_id = 2
        self.fail_at = None
        self.commit_error = None
        self.commits = 0
        self.rollbacks = 0
        self.statements = []
        self.staged = []
        self.durable = []
        self.guards = {
            ("llm_budget_reservations_immutable", 27, "reject_llm_budget_reservation_mutation"),
            ("llm_budget_reservations_no_truncate", 34, "reject_llm_budget_reservation_mutation"),
            ("llm_budget_reservations_receipt_binding", 7, "check_llm_budget_reservation_receipt"),
        }
        self.receipt_guards = [("llm_dispatch_receipts_immutable", 27), ("llm_dispatch_receipts_no_truncate", 34)]

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        if not getattr(statement, "is_select", False) and not getattr(statement, "is_insert", False):
            guards = self.guards if "c.relname='llm_budget_reservations'" in str(statement) else self.receipt_guards
            return SimpleNamespace(all=lambda: list(guards))
        if getattr(statement, "is_insert", False):
            name = statement.table.name
            if self.fail_at == name:
                raise IntegrityError("insert", {}, RuntimeError("unrelated constraint"))
            self.staged.append(name)
            row_id = self.receipt_id if name == "llm_dispatch_receipts" else self.reservation_id
            return SimpleNamespace(scalar_one_or_none=lambda: row_id)
        description = statement.column_descriptions[0]
        entity = description.get("entity")
        if entity is Tenant:
            result = self.tenant
        elif entity is MODELS.LLMBudgetDay:
            result = self.budget
        elif entity is MODELS.LLMBudgetReservation:
            result = self.older if description["name"] == "id" else self.existing
        else:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: self.stored_receipt))
        return SimpleNamespace(scalar_one_or_none=lambda: result)

    @asynccontextmanager
    async def _get_owned_session(self):
        snapshot = copy.deepcopy(self.budget)
        try:
            yield self
            if self.commit_error == "before":
                raise RuntimeError("ack lost before durable commit")
            self.durable.extend(self.staged)
            self.staged = []
            self.commits += 1
            if self.commit_error == "after":
                raise RuntimeError("ack lost after durable commit")
        except BaseException:
            self.rollbacks += 1
            if self.commit_error != "after":
                self.budget = snapshot
                self.staged = []
            raise


RECEIPT_MANAGER = load_isolated_source(
    "app.models.managers._budget_receipt_storage",
    ROOT / "app/models/managers/llm_dispatch_receipt_manager.py",
    imports={
        "app.core.tenant_context": TENANT_CONTEXT,
        "app.services.ai.dispatch_receipts": RECEIPTS,
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseDouble),
        "app.models.analysis.llm_dispatch_receipt": RECEIPT_MODEL,
    },
)
CAP = {"value": 1.0}
MANAGER = load_isolated_source(
    "app.models.managers._budget_storage",
    ROOT / "app/models/managers/llm_budget_manager.py",
    imports={
        "app.core.tenant_context": TENANT_CONTEXT,
        "app.services.ai.budget_admission": POLICY,
        "app.services.ai.dispatch_receipts": RECEIPTS,
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseDouble),
        "app.models.managers.llm_dispatch_receipt_manager": RECEIPT_MANAGER,
        "app.models.analysis.llm_budget": MODELS,
        "app.models.tenancy.tenant": SimpleNamespace(Tenant=Tenant),
        "app.services.tenancy.resolver": SimpleNamespace(resolved_daily_cost_limit=lambda tenant: CAP["value"]),
    },
)


class AtomicStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCOPE["id"] = 7
        CAP["value"] = 1.0
        self.manager = MANAGER.LLMBudgetManager()
        self.value = receipt()

    async def reserve(self, amount=D("0.4"), version="reviewed-v1"):
        return await self.manager.reserve_receipt(self.value, ceiling_usd=amount, bound_version=version)

    async def test_one_owned_commit_contains_both_inserts_and_balance(self):
        self.assertTrue(await self.reserve())
        self.assertEqual(self.manager.commits, 1)
        self.assertEqual(self.manager.durable, ["llm_dispatch_receipts", "llm_budget_reservations"])
        self.assertEqual(self.manager.budget.reserved_usd, D("0.4"))
        locks = [
            str(s.compile(dialect=postgresql.dialect()))
            for s in self.manager.statements
            if getattr(s, "_for_update_arg", None) is not None
        ]
        self.assertEqual(len(locks), 2)
        self.assertIn("tenants", locks[0])
        self.assertIn("llm_budget_days", locks[1])
        inserts = [
            str(s.compile(dialect=postgresql.dialect()))
            for s in self.manager.statements
            if getattr(s, "is_insert", False)
        ]
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_llm_budget_reservations_identity", inserts[-1])
        self.assertNotIn("DO UPDATE", inserts[-1])

    async def test_current_cap_not_projection_controls_admission(self):
        self.manager.budget.settled_usd = D("0.3")
        self.manager.budget.reserved_usd = D("0.4")
        with self.assertRaises(POLICY.BudgetAdmissionDenied):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])

    async def test_cap_change_is_read_again_from_locked_payer(self):
        CAP["value"] = 0.3
        with self.assertRaises(POLICY.BudgetAdmissionDenied):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])

    async def test_absent_or_frozen_baseline_never_provisions(self):
        for budget in (None, SimpleNamespace(frozen=True)):
            self.manager.budget = budget
            with self.assertRaises(POLICY.BudgetAdmissionUnavailable):
                await self.reserve()
            self.assertEqual(self.manager.durable, [])

    async def test_invalid_scope_is_denied_before_sql(self):
        for scope in (None, True, 8):
            SCOPE["id"] = scope
            with self.assertRaises(TenantError):
                await self.reserve()
            self.assertEqual(self.manager.statements, [])

    async def test_inactive_payer_is_denied(self):
        self.manager.tenant.is_active = False
        with self.assertRaises(TenantError):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])

    async def test_undefined_is_counted_separately_without_priced_reservation(self):
        self.value = receipt(input_cost_per_1k=None, tariff_state="undefined")
        self.manager.budget.settled_usd = D(2)
        self.assertTrue(await self.reserve(D(0), None))
        self.assertEqual(self.manager.budget.undefined_count, 1)
        self.assertEqual(self.manager.budget.reserved_usd, D(0))

    async def test_free_zero_and_unlimited_unbounded_are_distinct(self):
        self.value = receipt(input_cost_per_1k=D(0), output_cost_per_1k=D(0), tariff_state="free")
        self.assertTrue(await self.reserve(D(0), None))
        self.assertEqual(self.manager.budget.undefined_count, 0)
        self.manager = MANAGER.LLMBudgetManager()
        self.value = receipt()
        CAP["value"] = 0.0
        self.assertTrue(await self.reserve(None, None))
        self.assertEqual(self.manager.budget.unbounded_count, 1)

    async def test_capped_unbounded_is_denied(self):
        with self.assertRaises(POLICY.BudgetAdmissionDenied):
            await self.reserve(None, None)
        self.assertEqual(self.manager.durable, [])

    async def test_old_day_liability_does_not_expire(self):
        self.manager.older = 9
        with self.assertRaises(POLICY.BudgetAdmissionDenied):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])

    async def test_fresh_backdated_receipt_is_denied(self):
        old = self.value["prepared_at"] - timedelta(days=1)
        self.value.update(prepared_at=old, utc_day=old.date())
        with self.assertRaises(POLICY.BudgetAdmissionDenied):
            await self.reserve()

    async def test_exact_replay_is_false_without_increment_or_dispatch_permission(self):
        self.manager.existing = SimpleNamespace(
            utc_day=self.value["utc_day"], cap_usd=D(1), **POLICY.reservation_plan(self.value, D("0.4"), "reviewed-v1")
        )
        self.manager.receipt_id = None
        self.manager.stored_receipt = self.value
        self.manager.budget.frozen = True
        CAP["value"] = 0.1
        self.assertFalse(await self.reserve())
        self.assertEqual(self.manager.budget.reserved_usd, D(0))
        self.assertNotIn("llm_budget_reservations", self.manager.durable)

    async def test_changed_replay_bound_is_conflict(self):
        self.manager.existing = SimpleNamespace(
            utc_day=self.value["utc_day"], **POLICY.reservation_plan(self.value, D("0.2"), "reviewed-v1")
        )
        with self.assertRaises(POLICY.BudgetAdmissionConflict):
            await self.reserve()
        self.assertEqual(self.manager.commits, 0)

    async def test_standalone_receipt_cannot_be_upgraded(self):
        self.manager.receipt_id = None
        self.manager.stored_receipt = self.value
        with self.assertRaises(POLICY.BudgetAdmissionConflict):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])
        self.assertEqual(self.manager.budget.reserved_usd, D(0))

    async def test_cross_tenant_identity_collision_never_exposes_or_admits(self):
        self.manager.receipt_id = None
        self.manager.stored_receipt = None
        with self.assertRaises(RECEIPT_MANAGER.ReceiptEvidenceConflict):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])
        query = self.manager.statements[-1].compile().params
        self.assertEqual(query["tenant_id_1"], 7)

    async def test_unrelated_integrity_failure_rolls_back_both_inserts(self):
        self.manager.fail_at = "llm_budget_reservations"
        with self.assertRaises(IntegrityError):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])
        self.assertEqual(self.manager.budget.reserved_usd, D(0))
        self.assertEqual(self.manager.rollbacks, 1)

    async def test_no_success_on_pre_or_post_durable_commit_ack_loss(self):
        for phase in ("before", "after"):
            self.manager = MANAGER.LLMBudgetManager()
            self.manager.commit_error = phase
            with self.assertRaisesRegex(RuntimeError, "ack lost"):
                await self.reserve()
            self.assertEqual(bool(self.manager.durable), phase == "after")

    async def test_disabled_or_incomplete_guards_refuse_before_inserts(self):
        self.manager.guards = set()
        with self.assertRaises(POLICY.BudgetAdmissionUnavailable):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])
        self.manager = MANAGER.LLMBudgetManager()
        self.manager.receipt_guards = []
        with self.assertRaises(RECEIPT_MANAGER.ReceiptStorageUnavailable):
            await self.reserve()
        self.assertEqual(self.manager.durable, [])


class DefinitionTests(unittest.TestCase):
    def test_new_models_register_once_and_bind_without_network(self):
        code = """
import socket
def deny(*args, **kwargs):
    raise AssertionError('Unexpected network connection')
socket.socket.connect = deny
from sqlalchemy.orm import configure_mappers
from app.models import Base, LLMBudgetDay, LLMBudgetReservation
from app.models.analysis import LLMBudgetDay as domain_day, LLMBudgetReservation as domain_reservation
from app.models.llm_budget import LLMBudgetDay as public_day, LLMBudgetReservation as public_reservation
assert LLMBudgetDay is domain_day is public_day
assert LLMBudgetReservation is domain_reservation is public_reservation
configure_mappers()
assert LLMBudgetReservation.objects.model is LLMBudgetReservation
for model in (LLMBudgetDay, LLMBudgetReservation):
    assert sum(m.class_ is model for m in Base.registry.mappers) == 1
    assert model.__table__.schema == 'budget_review'
    assert next(iter(model.__table__.c.tenant_id.foreign_keys)).ondelete == 'RESTRICT'
"""
        env = dict(
            os.environ,
            SECRET_KEY="synthetic-import-check",
            POSTGRES_URL="postgresql://dummy:dummy@localhost/dummy",
            DB_SCHEMA="budget_review",
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "New model registration failed; inspect stderr locally, redact secrets")

    def test_schema_qualified_definition_and_model_constraints_match_without_application(self):
        captured = {}
        statements = []
        schema = 'review"schema'

        def capture(name, *items, **kwargs):
            captured[name] = (items, kwargs)

        operations = SimpleNamespace(
            create_table=capture,
            execute=statements.append,
            get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()),
        )
        migration = load_isolated_source(
            "_budget_migration",
            ROOT / "migrations/versions/0095_add_llm_budget_reservations.py",
            imports={
                "alembic": SimpleNamespace(op=operations),
                "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA=schema)),
            },
        )
        migration.upgrade()
        self.assertEqual(migration.down_revision, "0094")
        self.assertEqual(set(captured), {"llm_budget_days", "llm_budget_reservations"})
        for model in (MODELS.LLMBudgetDay, MODELS.LLMBudgetReservation):
            items, kwargs = captured[model.__tablename__]
            self.assertEqual(kwargs["schema"], schema)
            definition = Table(model.__tablename__, MetaData(schema=schema), *items, **kwargs)
            self.assertEqual(set(definition.columns.keys()), set(model.__table__.columns.keys()))
            self.assertEqual(
                {c.name: str(c.sqltext) for c in definition.constraints if hasattr(c, "sqltext")},
                {c.name: str(c.sqltext) for c in model.__table__.constraints if hasattr(c, "sqltext")},
            )
            for column in definition.columns:
                self.assertEqual(column.nullable, model.__table__.columns[column.name].nullable)
                self.assertEqual(str(column.type), str(model.__table__.columns[column.name].type))
        sql = "\n".join(statements)
        self.assertIn('"review""schema".llm_budget_reservations', sql)
        self.assertIn("Reservation receipt binding differs", sql)
        self.assertNotIn("INSERT INTO", sql)
        self.assertNotIn("ALTER TABLE", sql)
        with self.assertRaises(RuntimeError):
            migration.downgrade()


if __name__ == "__main__":
    unittest.main()
