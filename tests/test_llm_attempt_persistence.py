"""Strict journal replay using actual SQLAlchemy statements and fake sessions.

Run: .venv/bin/python tests/test_llm_attempt_persistence.py
No engine, database, schema action, private environment or provider request.
PostgreSQL race/commit behavior requires separate owner-local evidence.
"""

import unittest
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
TABLE = Table(
    "llm_attempts",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("tenant_id", Integer, nullable=False),
    Column("attempt_key", String(64), nullable=False),
    Column("purpose", String(32), nullable=False),
    Column("stage", String(32), nullable=False),
    Column("source_id", Integer),
    Column("analysis_date", Date),
    Column("job_id", Integer),
    Column("agent_task_id", Integer),
    Column("llm_model_row_id", Integer),
    Column("model_id", String(200)),
    Column("provider_name", String(200)),
    Column("input_cost_per_1k", Numeric(18, 8)),
    Column("output_cost_per_1k", Numeric(18, 8)),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("cost_usd", Numeric(18, 9)),
    Column("cost_certainty", String(32), nullable=False),
    Column("outcome", String(32), nullable=False),
    UniqueConstraint("attempt_key", name="uq_llm_attempts_key"),
    schema="isolated_test",
)
MODEL = SimpleNamespace(__table__=TABLE, **{column.name: column for column in TABLE.columns})


def evidence(**changes):
    value = {
        "attempt_key": "a" * 64,
        "purpose": "analysis",
        "stage": "text_analysis",
        "source_id": 3,
        "analysis_date": date(2026, 10, 12),
        "job_id": 9,
        "agent_task_id": 2,
        "llm_model_row_id": 4,
        "model_id": "model",
        "provider_name": "provider",
        "input_cost_per_1k": Decimal("1"),
        "output_cost_per_1k": Decimal("2"),
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_usd": Decimal("0.02"),
        "cost_certainty": "priced",
        "outcome": "succeeded",
    }
    value.update(changes)
    return value


class BaseManagerDouble:
    def __init__(self, model):
        self.model = model
        self.tenant_id = 7
        self.bypass = False
        self.inserted_id = 1
        self.stored = dict(evidence(), tenant_id=7)
        self.commit_error = None
        self.commits = 0
        self.rollbacks = 0
        self.execute = AsyncMock(side_effect=self._execute)

    async def _apply_tenant(self, session, values):
        explicit = values.pop("tenant_id", None)
        if self.bypass:
            values["tenant_id"] = explicit if explicit is not None else 1
        else:
            if self.tenant_id is None or (explicit is not None and explicit != self.tenant_id):
                raise RuntimeError("Tenant context rejected")
            values["tenant_id"] = self.tenant_id
        return values

    async def _execute(self, statement):
        if statement.is_insert:
            return SimpleNamespace(scalar_one_or_none=lambda: self.inserted_id)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: self.stored))

    @asynccontextmanager
    async def _get_owned_session(self):
        try:
            yield SimpleNamespace(execute=self.execute)
            if self.commit_error is not None:
                raise self.commit_error
            self.commits += 1
        except BaseException:
            self.rollbacks += 1
            raise


MODULE = load_isolated_source(
    "app.models.managers._strict_attempt_persistence",
    ROOT / "app/models/managers/llm_attempt_manager.py",
    imports={
        "app.models.managers.base_manager": SimpleNamespace(BaseManager=BaseManagerDouble),
        "app.models.analysis.llm_attempt": SimpleNamespace(LLMAttempt=MODEL),
    },
)


class AttemptPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_insert_acknowledged_only_after_owned_commit(self):
        manager = MODULE.LLMAttemptManager()
        self.assertTrue(await manager.record_attempt(evidence()))
        self.assertEqual(manager.commits, 1)
        self.assertEqual(manager.execute.await_count, 1)

    async def test_insert_suppresses_only_named_attempt_key_constraint(self):
        manager = MODULE.LLMAttemptManager()
        await manager.record_attempt(evidence())
        sql = str(manager.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_llm_attempts_key DO NOTHING", sql)
        self.assertIn("RETURNING isolated_test.llm_attempts.id", sql)
        self.assertNotIn("DO UPDATE", sql)

    async def test_exact_replay_is_false_with_no_rewrite(self):
        manager = MODULE.LLMAttemptManager()
        manager.inserted_id = None
        self.assertFalse(await manager.record_attempt(evidence()))
        self.assertEqual(manager.execute.await_count, 2)
        self.assertEqual(manager.commits, 1)

    async def test_none_is_not_zero_evidence(self):
        manager = MODULE.LLMAttemptManager()
        manager.inserted_id = None
        manager.stored["cost_usd"] = None
        with self.assertRaises(MODULE.AttemptEvidenceConflict):
            await manager.record_attempt(evidence(cost_usd=Decimal(0)))
        self.assertEqual(manager.commits, 0)

    async def test_any_changed_evidence_field_is_conflict(self):
        replacements = {
            "purpose": "agent_chat",
            "stage": "unified_summary",
            "source_id": 99,
            "analysis_date": date(2026, 10, 13),
            "job_id": 10,
            "agent_task_id": 3,
            "llm_model_row_id": 5,
            "model_id": "other",
            "provider_name": "other",
            "input_cost_per_1k": Decimal("3"),
            "output_cost_per_1k": Decimal("4"),
            "prompt_tokens": None,
            "completion_tokens": 6,
            "cost_usd": Decimal("0.03"),
            "cost_certainty": "undefined",
            "outcome": "unknown",
        }
        for field, value in replacements.items():
            with self.subTest(field=field):
                manager = MODULE.LLMAttemptManager()
                manager.inserted_id = None
                with self.assertRaises(MODULE.AttemptEvidenceConflict):
                    await manager.record_attempt(evidence(**{field: value}))
                self.assertEqual(manager.rollbacks, 1)

    async def test_collision_read_is_bound_to_effective_payer_even_in_bypass(self):
        manager = MODULE.LLMAttemptManager()
        manager.bypass = True
        manager.inserted_id = None
        manager.stored["tenant_id"] = 42
        self.assertFalse(await manager.record_attempt(evidence(tenant_id=42)))
        query = manager.execute.call_args.args[0]
        self.assertEqual(query.compile().params["tenant_id_1"], 42)
        self.assertIn("llm_attempts.attempt_key =", str(query))

    async def test_other_tenant_or_missing_collision_row_is_not_success(self):
        manager = MODULE.LLMAttemptManager()
        manager.inserted_id = None
        manager.stored = None
        with self.assertRaisesRegex(MODULE.AttemptEvidenceConflict, "conflicts"):
            await manager.record_attempt(evidence())
        self.assertEqual(manager.commits, 0)

    async def test_wrong_payer_value_is_detected_even_if_read_returns_it(self):
        manager = MODULE.LLMAttemptManager()
        manager.inserted_id = None
        manager.stored["tenant_id"] = 8
        with self.assertRaises(MODULE.AttemptEvidenceConflict):
            await manager.record_attempt(evidence())

    async def test_explicit_tenant_mismatch_fails_before_insert(self):
        manager = MODULE.LLMAttemptManager()
        with self.assertRaisesRegex(RuntimeError, "Tenant context"):
            await manager.record_attempt(evidence(tenant_id=8))
        manager.execute.assert_not_awaited()

    async def test_missing_tenant_fails_before_insert(self):
        manager = MODULE.LLMAttemptManager()
        manager.tenant_id = None
        with self.assertRaisesRegex(RuntimeError, "Tenant context"):
            await manager.record_attempt(evidence())
        manager.execute.assert_not_awaited()

    async def test_other_integrity_error_is_not_swallowed(self):
        manager = MODULE.LLMAttemptManager()
        failure = IntegrityError("insert", {}, RuntimeError("different constraint"))
        manager.execute.side_effect = failure
        with self.assertRaises(IntegrityError) as caught:
            await manager.record_attempt(evidence())
        self.assertIs(caught.exception, failure)
        self.assertEqual(manager.rollbacks, 1)

    async def test_commit_ack_loss_is_not_an_insert_ack(self):
        manager = MODULE.LLMAttemptManager()
        manager.commit_error = RuntimeError("commit acknowledgement lost")
        with self.assertRaisesRegex(RuntimeError, "acknowledgement"):
            await manager.record_attempt(evidence())
        self.assertEqual(manager.commits, 0)

    async def test_read_failure_is_not_duplicate_ack(self):
        manager = MODULE.LLMAttemptManager()
        manager.execute.side_effect = [
            SimpleNamespace(scalar_one_or_none=lambda: None),
            RuntimeError("read failed"),
        ]
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            await manager.record_attempt(evidence())

    async def test_input_is_not_mutated(self):
        manager = MODULE.LLMAttemptManager()
        value = evidence(tenant_id=7)
        original = dict(value)
        await manager.record_attempt(value)
        self.assertEqual(value, original)

    async def test_omitted_nullable_fields_match_stored_nulls(self):
        manager = MODULE.LLMAttemptManager()
        manager.inserted_id = None
        value = evidence()
        del value["job_id"]
        manager.stored["job_id"] = None
        self.assertFalse(await manager.record_attempt(value))

    async def test_unknown_and_server_managed_fields_are_rejected_before_io(self):
        for field in ("id", "created_at", "_reported_cost_usd", "prompt"):
            with self.subTest(field=field):
                manager = MODULE.LLMAttemptManager()
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    await manager.record_attempt(evidence(**{field: "private"}))
                manager.execute.assert_not_awaited()

    async def test_conflict_message_does_not_expose_evidence(self):
        manager = MODULE.LLMAttemptManager()
        manager.inserted_id = None
        with self.assertRaises(MODULE.AttemptEvidenceConflict) as caught:
            await manager.record_attempt(evidence(model_id="PRIVATE-MODEL"))
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertNotIn("tenant", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
