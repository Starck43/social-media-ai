"""Owner-local outcome proof on an already applied definition; never migrate.
Synthetic events and rollback-only NEW-table probes need separate approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import apply_llm_outcome_definition as application
from scripts.verify_llm_dispatch_receipts import (
    InjectedBoundaryFailure,
    VerificationFailure,
    after_commit_fault,
    before_commit_fault,
    drain_pair,
    emit,
    require,
    rollback_only,
    timeouts,
)


def application_evidence(path, expected_head, *, isolated=False):
    path = Path(path)
    require(path.stat().st_size <= 2_000_000, "Application report size")
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def phase(name):
        rows = [r for r in records if r.get("phase") == name]
        require(len(rows) == 1, "Application report phase count")
        return rows[0]

    require(phase("intake").get("head") == expected_head, "Application report head")
    require(
        phase("rehearsal").get("status") == "PASS" and phase("rehearsal").get("rolled_back") is True,
        "Application rollback evidence",
    )
    require(
        phase("application").get("status") == "COMMITTED" and phase("application").get("definition_only") is True,
        "Application commit evidence",
    )
    result = phase("result")
    if isolated:
        from scripts.isolated_outcome_fixture import TARGET

        require(
            result.get("target") == list(TARGET)
            and result.get("provenance") == "isolated_synthetic_fixture"
            and phase("intake").get("target") == list(TARGET)
            and phase("preflight").get("fixture_schema") == "absent"
            and phase("preflight").get("public_revision") is None
            and phase("preflight").get("target") == list(TARGET),
            "Isolated preparation provenance",
        )
    require(
        result.get("status") == ("FIXTURE_PASS" if isolated else "APPLICATION_PASS")
        and "public_revision" in result
        and result.get("public_revision") == (None if isolated else "0092")
        and type(result.get("outcome_rows")) is int
        and result["outcome_rows"] == 0,
        "Application final verdict",
    )
    require(
        result.get("fixture_matches_rehearsal" if isolated else "baseline_preserved_since_preflight") is True
        and result.get("whole_gate_accepted") is False
        and result.get("synthetic_and_mutation_probes_run") is False,
        "Application scope",
    )
    require(
        set(result.get("catalog", {})) == {"relations", "constraints", "indexes", "functions", "triggers"},
        "Application catalog shape",
    )
    return result["catalog"]


def sqlstate(error):
    seen = set()
    for _ in range(4):
        if error is None or id(error) in seen:
            return None
        seen.add(id(error))
        code = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
        if code:
            return code
        error = getattr(error, "orig", None) or getattr(error, "__cause__", None)
    return None


def race_outcomes(results, backends, conflicting, conflict_type):
    require(len(backends) == 2 and len(set(backends)) == 2, "Race must use two backends")
    require(len(results) == 2 and sum(x is True for x in results) == 1, "Race insert winner")
    if conflicting:
        require(sum(isinstance(x, conflict_type) for x in results) == 1, "Race evidence conflict")
    else:
        require(sum(x is False for x in results) == 1, "Race exact replay")


def observation_time(parent):
    return datetime.combine(
        parent["utc_day"] + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(seconds=1)


async def verify(args, expected_catalog):
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.exc import DBAPIError

    from app.core.config import settings
    from app.core.database import async_engine, async_session_maker
    from app.core.tenant_context import TenantContextError, tenant_scope
    from app.models import LLMDispatchReceipt, LLMOutcomeEvent
    from app.models.managers.llm_outcome_event_manager import (
        LLMOutcomeEventManager,
        OutcomeEvidenceConflict,
        OutcomeStorageUnavailable,
    )
    from app.services.ai.dispatch_receipts import RECEIPT_FIELDS
    from app.services.ai.outcome_events import EVENT_FIELDS, build_outcome_event

    schema = settings.DB_SCHEMA
    require(schema == args.expected_schema and not settings.DEBUG, "Resolved settings")
    table = f'"{schema}".llm_outcome_events'
    checks = []
    manifest = []

    async def inspect():
        if getattr(args, "isolated_fixture", False):
            from scripts.isolated_outcome_fixture import inspect_state

            return await inspect_state(async_engine, args)
        return await application.inspect_state(async_engine, args, expect_absent=False)

    async def event_rows():
        async with rollback_only(async_session_maker) as session:
            await timeouts(session)
            return {
                r["event_id"]: dict(r)
                for r in (
                    await session.execute(
                        select(LLMOutcomeEvent.id, *(getattr(LLMOutcomeEvent, f) for f in EVENT_FIELDS)).where(
                            LLMOutcomeEvent.tenant_id.in_([1, 94])
                        )
                    )
                ).mappings()
            }

    async def keep(value):
        row = (await event_rows()).get(value["event_id"])
        require(row is not None and all(row[f] == value[f] for f in EVENT_FIELDS), "Retained event differs")
        item = {k: row[k] for k in ("id", "event_id", "dispatch_id", "tenant_id")}
        manifest.append(item)
        emit("retained_event", **item, synthetic=True)

    async def store(value):
        with tenant_scope(value["tenant_id"]):
            return await LLMOutcomeEventManager().record_event(value)

    try:
        baseline, current = await inspect()
        require(current == expected_catalog, "Applied catalog differs from accepted application")
        definition = application.capture(application.load_revision(), schema)
        async with async_engine.connect() as conn:
            await conn.run_sync(lambda c: application.reflected_shape(c, LLMOutcomeEvent.__table__, definition.table))
            application.validate_guards(
                current, application.function_bodies(definition.sql), schema, args.expected_role
            )
            require(
                (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0,
                "Existing outcomes: stop, no automatic resume",
            )
            parents = {}
            for tenant in (1, 94):
                row = (
                    (
                        await conn.execute(
                            select(*(getattr(LLMDispatchReceipt, f) for f in RECEIPT_FIELDS))
                            .where(
                                LLMDispatchReceipt.tenant_id == tenant,
                                LLMDispatchReceipt.purpose == "evidence",
                                LLMDispatchReceipt.stage == "evidence_review",
                                LLMDispatchReceipt.model_id == "synthetic-evidence-model",
                                LLMDispatchReceipt.provider_name == "synthetic-evidence-provider",
                            )
                            .order_by(LLMDispatchReceipt.id)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                require(row is not None, "Approved synthetic parent missing")
                parents[tenant] = dict(row)
        emit(
            "preflight",
            baseline=baseline["tables"],
            parent_dispatches={str(k): v["dispatch_id"] for k, v in parents.items()},
            outcome_rows=0,
        )
        checks.append("accepted_application_definition_model_catalog_parity")

        def record(tenant=1, amount="0.01", **changes):
            values = dict(
                event_id=uuid4().hex,
                observed_at=observation_time(parents[tenant]),
                dispatch_state="response_received",
                outcome="succeeded",
                accounting_envelope={"accounting_cost_reported": True, "accounting_cost_usd": amount},
            )
            values.update(changes)
            return build_outcome_event(parents[tenant], **values)

        first = record(accounting_envelope=None, dispatch_state="possible", outcome="timeout")
        require(await store(first) is True, "First synthetic event")
        await keep(first)
        require(await store(first) is False, "Exact event replay")

        async def refusal(awaitable, error_type, label):
            try:
                await awaitable
            except error_type:
                return
            raise VerificationFailure(label)

        await refusal(
            store(record(94, event_id=first["event_id"])),
            OutcomeEvidenceConflict,
            "Stored-key payer collision accepted",
        )
        await refusal(
            store(dict(first, tenant_id=94, event_id=uuid4().hex)), TenantContextError, "Cross-payer receipt accepted"
        )
        second = record(94)
        require(await store(second) is True, "Second payer insert")
        await keep(second)
        recon = record(event_kind="reconciliation", amount=0)
        require(await store(recon) is True, "Reconciliation insert")
        await keep(recon)
        unresolved = record(amount="0.0000000001")
        require(
            unresolved["cost_usd"] is None and unresolved["cost_certainty"] == "undefined",
            "Unrepresentable price mapping",
        )
        require(await store(unresolved) is True, "Unresolved price insert")
        await keep(unresolved)
        require((await event_rows())[first["event_id"]]["outcome"] == "timeout", "Reconciliation mutated history")
        require(first["observed_at"].date() == parents[1]["utc_day"] + timedelta(days=1), "UTC observation day")
        checks.append("exact_replay_collision_tenant_isolation_immutable_reconciliation_and_utc_day")
        for operation, sql in [
            ("update", f"UPDATE {table} SET prompt_tokens=0 WHERE event_id=:key"),
            ("delete", f"DELETE FROM {table} WHERE event_id=:key"),
            ("truncate", f"TRUNCATE {table}"),
        ]:
            before = await event_rows()
            async with rollback_only(async_session_maker) as session:
                await timeouts(session)
                try:
                    async with session.begin_nested():
                        await session.execute(text(sql), {"key": first["event_id"]})
                except DBAPIError as error:
                    require(sqlstate(error) == "55000", "Wrong immutability SQLSTATE")
                else:
                    raise VerificationFailure("Mutation guard allowed write")
            require(await event_rows() == before, "Guard probe changed events")
            emit("mutation_rollback", operation=operation, refused=True, rolled_back=True)
        for trigger in application.TRIGGERS:
            candidate = record()
            probe = LLMOutcomeEventManager()
            async with rollback_only(async_session_maker) as session:
                await timeouts(session)
                await session.execute(text(f'ALTER TABLE {table} DISABLE TRIGGER "{trigger}"'))

                @asynccontextmanager
                async def same_transaction():
                    yield session

                probe._get_owned_session = same_transaction
                with tenant_scope(1):
                    await refusal(
                        probe.record_event(candidate), OutcomeStorageUnavailable, "Disabled event guard accepted"
                    )
                require(
                    (
                        await session.execute(
                            text(f"SELECT count(*) FROM {table} WHERE event_id=:key"), {"key": candidate["event_id"]}
                        )
                    ).scalar_one()
                    == 0,
                    "Disabled guard inserted event",
                )
            _, restored = await inspect()
            require(restored == expected_catalog, "Rollback failed to restore catalog")
            emit("guard_rollback", trigger=trigger, refused=True, restored=True, committed_disable=False)
        checks.append("event_mutation_refusal_and_same_transaction_disabled_guard_rollback")
        for changes in (
            {"tenant_id": 94},
            {"pricing_policy_version": "different_policy"},
            {"observed_at": parents[1]["prepared_at"] - timedelta(seconds=1)},
        ):
            candidate = dict(first, event_id=uuid4().hex, **changes)
            async with rollback_only(async_session_maker) as session:
                await timeouts(session)
                try:
                    async with session.begin_nested():
                        await session.execute(insert(LLMOutcomeEvent.__table__).values(**candidate))
                except DBAPIError as error:
                    require(sqlstate(error) == "23503", "Wrong receipt binding SQLSTATE")
                else:
                    raise VerificationFailure("Direct SQL receipt binding accepted")
        checks.append("direct_sql_payer_policy_time_binding_refusal")
        candidate = dict(
            first,
            event_id=uuid4().hex,
            provider_price_state="resolved",
            provider_cost_raw="0.2",
            cost_usd=Decimal("0.1"),
            cost_certainty="priced",
            price_source="provider",
        )
        async with rollback_only(async_session_maker) as session:
            await timeouts(session)
            try:
                async with session.begin_nested():
                    await session.execute(insert(LLMOutcomeEvent.__table__).values(**candidate))
            except DBAPIError as error:
                require(sqlstate(error) == "23514", "Wrong provider-price binding SQLSTATE")
            else:
                raise VerificationFailure("Inconsistent provider amount accepted")
        checks.append("direct_sql_provider_amount_consistency_refusal")
        for hook, persist in ((before_commit_fault, False), (after_commit_fault, True)):
            candidate = record()
            probe = LLMOutcomeEventManager()
            owned = probe._get_owned_session
            probe._get_owned_session = lambda: hook(owned)
            with tenant_scope(1):
                await refusal(probe.record_event(candidate), InjectedBoundaryFailure, "Injected failure not propagated")
            require((candidate["event_id"] in await event_rows()) == persist, "Fault persistence differs")
            if persist:
                require(await store(candidate) is False, "Lost-ack replay inserted")
                await keep(candidate)
            emit("injected_boundary", after_successful_commit=persist, persisted=persist, real_network_loss=False)
        checks.append("before_commit_rollback_and_after_successful_commit_lost_ack_replay")
        for conflicting in (False, True):
            first_race = record()
            second_race = record(event_id=first_race["event_id"], amount="0.02") if conflicting else dict(first_race)
            arrived = 0
            ready = asyncio.Event()
            backends = []

            async def write(value):
                nonlocal arrived
                probe = LLMOutcomeEventManager()
                owned = probe._get_owned_session

                @asynccontextmanager
                async def barrier():
                    nonlocal arrived
                    async with owned() as session:
                        await timeouts(session)
                        backends.append((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                        arrived += 1
                        if arrived == 2:
                            ready.set()
                        await asyncio.wait_for(ready.wait(), 10)
                        yield session

                probe._get_owned_session = barrier
                with tenant_scope(1):
                    return await probe.record_event(value)

            results = await drain_pair(write(first_race), write(second_race))
            race_outcomes(results, backends, conflicting, OutcomeEvidenceConflict)
            await keep(first_race if results[0] is True else second_race)
            emit(
                "barrier_race",
                conflicting=conflicting,
                outcomes=[r if isinstance(r, bool) else type(r).__name__ for r in results],
                separate_sessions=True,
                multi_process=False,
            )
        checks.append("barrier_synchronized_identical_and_conflicting_events")
        final, final_catalog = await inspect()
        require(final == baseline, "Receipt/journal/tenant baseline changed")
        require(final_catalog == expected_catalog, "Guard/definition catalog changed")
        rows = await event_rows()
        require(set(rows) == {r["event_id"] for r in manifest}, "Unexplained or missing outcome rows")
        async with async_engine.connect() as conn:
            require(
                (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == len(manifest),
                "Unexpected events outside approved payers",
            )
        checks.append("preflight_to_final_retained_content_and_catalog_preservation")
        emit(
            "result",
            status="PASS",
            checks=checks,
            retained=manifest,
            total_events=len(rows),
            public_revision=None if getattr(args, "isolated_fixture", False) else "0092",
            catalog=final_catalog,
            baseline=final["tables"],
            baseline_integrity_since_preflight_only=True,
            whole_gate_accepted=False,
        )
    finally:
        await async_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "expected-head",
        "expected-application-head",
        "application-report",
        "expected-database",
        "expected-schema",
        "expected-role",
        "receipt-digest",
        "journal-digest",
    ):
        parser.add_argument("--" + name, required=True)
    for name in ("expected-receipts", "expected-journal", "expected-tenants"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--allow-test-event-writes", action="store_true")
    parser.add_argument("--allow-rollback-guard-probes", action="store_true")
    parser.add_argument("--isolated-fixture", action="store_true")
    parser.add_argument("--tenant-digest")
    args = parser.parse_args()
    try:
        require(
            args.allow_test_event_writes and args.allow_rollback_guard_probes, "Separate event/probe approval flags"
        )
        if args.isolated_fixture:
            from scripts.isolated_outcome_fixture import TARGET

            require(
                (args.expected_database, args.expected_schema, args.expected_role) == TARGET,
                "Approved isolated target",
            )
            require(
                (args.expected_receipts, args.expected_journal, args.expected_tenants) == (2, 0, 2)
                and re.fullmatch(r"[0-9a-f]{64}", args.tenant_digest or ""),
                "Explicit isolated fixture baseline",
            )
        else:
            require(
                (args.expected_database, args.expected_schema, args.expected_role)
                == ("social_manager", "test_schema", "starck"),
                "Approved target",
            )
        require(
            all(re.fullmatch(r"[0-9a-f]{40}", v) for v in (args.expected_head, args.expected_application_head)),
            "Head syntax",
        )
        require(
            all(re.fullmatch(r"[0-9a-f]{64}", v) for v in (args.receipt_digest, args.journal_digest)),
            "Baseline digest syntax",
        )
        require(
            all(v >= 0 for v in (args.expected_receipts, args.expected_journal, args.expected_tenants)),
            "Baseline counts",
        )
        require(
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == args.expected_head,
            "Exact head",
        )
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip(), "Dirty checkout"
        )
        application.idle_processes()
        frozen = [
            "migrations/versions/0094_add_llm_outcome_events.py",
            "app/models/analysis/llm_outcome_event.py",
            "app/models/managers/llm_outcome_event_manager.py",
            "app/services/ai/outcome_events.py",
            "scripts/apply_llm_outcome_definition.py",
        ]
        if args.isolated_fixture:
            frozen.extend(
                [
                    "scripts/isolated_outcome_fixture.py",
                    "migrations/versions/0093_add_llm_dispatch_receipts.py",
                    "app/models/tenancy/tenant.py",
                    "app/models/analysis/llm_attempt.py",
                    "app/models/analysis/llm_dispatch_receipt.py",
                    "app/models/base.py",
                    "app/services/ai/dispatch_receipts.py",
                    "scripts/verify_llm_dispatch_receipts.py",
                    "app/core/config.py",
                    "app/core/database.py",
                ]
            )
        require(
            not subprocess.check_output(
                ["git", "diff", args.expected_application_head, "HEAD", "--", *frozen], cwd=ROOT, text=True
            ).strip(),
            "Applied definition/runtime contract changed",
        )
        expected = application_evidence(
            args.application_report, args.expected_application_head, isolated=args.isolated_fixture
        )
        from scripts.setup_test_db import resolve_test_schema, resolve_test_url, resolve_working_schema
        from scripts.verify_llm_attempt_persistence import validate_target

        if args.isolated_fixture:
            from scripts.isolated_outcome_fixture import resolve_target

            url, schema = resolve_target()
        else:
            url, schema = resolve_test_url(), resolve_test_schema()
            validate_target(
                os.environ.get("POSTGRES_URL", "").strip(),
                resolve_working_schema(),
                url,
                schema,
                args.expected_database,
                args.expected_schema,
            )
        os.environ.update(POSTGRES_URL=url, DB_SCHEMA=schema, DEBUG="false")
        emit("intake", head=args.expected_head, application_head=args.expected_application_head, schema=schema)
        asyncio.run(verify(args, expected))
        return 0
    except BaseException as error:
        emit(
            "result",
            status="BLOCKED_OR_FAILED",
            error_type=type(error).__name__,
            check=str(error) if isinstance(error, VerificationFailure) else None,
            complete=False,
            do_not_rerun_automatically=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
