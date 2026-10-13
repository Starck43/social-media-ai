"""Small source/helper tests; no DB, pytest, provider or private settings."""

import json
import tempfile
import unittest
from datetime import date, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
M = load_isolated_source("_outcome_pg_verifier", ROOT / "scripts/verify_llm_outcome_events.py")
HEAD = "f8cbe69a7d32465523fa7540e68b8894101705ed"


class Conflict(RuntimeError):
    pass


def report():
    return [
        {"phase": "intake", "head": HEAD},
        {"phase": "rehearsal", "status": "PASS", "rolled_back": True},
        {"phase": "application", "status": "COMMITTED", "definition_only": True},
        {
            "phase": "result",
            "status": "APPLICATION_PASS",
            "public_revision": "0092",
            "outcome_rows": 0,
            "baseline_preserved_since_preflight": True,
            "whole_gate_accepted": False,
            "synthetic_and_mutation_probes_run": False,
            "catalog": dict(relations=[], constraints=[], indexes=[], functions=[], triggers=[]),
        },
    ]


def load(records, head=HEAD):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records))
        return M.application_evidence(path, head)


class ApplicationEvidenceTests(unittest.TestCase):
    def test_exact_pinned_application_report_accepts(self):
        self.assertEqual(set(load(report())), {"relations", "constraints", "indexes", "functions", "triggers"})

    def test_wrong_application_head_rejects(self):
        with self.assertRaises(M.VerificationFailure):
            load(report(), "a" * 40)

    def test_missing_or_duplicate_phase_rejects(self):
        for records in (report()[1:], report() + [report()[-1]]):
            with self.subTest(records=records), self.assertRaises(M.VerificationFailure):
                load(records)

    def test_failed_rehearsal_or_missing_rollback_rejects(self):
        for field, value in [("status", "FAILED"), ("rolled_back", False)]:
            records = report()
            records[1][field] = value
            with self.assertRaises(M.VerificationFailure):
                load(records)

    def test_uncertain_application_never_grants_write_gate(self):
        records = report()
        records[2]["status"] = "APPLICATION_OUTCOME_UNCERTAIN"
        with self.assertRaises(M.VerificationFailure):
            load(records)

    def test_wrong_revision_event_count_or_scope_rejects(self):
        for field, value in [
            ("public_revision", "0094"),
            ("outcome_rows", False),
            ("outcome_rows", 1),
            ("baseline_preserved_since_preflight", False),
            ("synthetic_and_mutation_probes_run", True),
            ("whole_gate_accepted", True),
        ]:
            records = report()
            records[-1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(M.VerificationFailure):
                load(records)

    def test_incomplete_catalog_rejects(self):
        records = report()
        records[-1]["catalog"].pop("functions")
        with self.assertRaises(M.VerificationFailure):
            load(records)


class Helpers(unittest.TestCase):
    def test_wrapped_sqlstate_is_retrieved_without_exception_text(self):
        self.assertEqual(M.sqlstate(SimpleNamespace(orig=SimpleNamespace(sqlstate="55000"))), "55000")

    def test_cyclic_exception_chain_is_bounded(self):
        error = SimpleNamespace()
        error.orig = error
        self.assertIsNone(M.sqlstate(error))

    def test_timezone_rollover_uses_parent_utc_day(self):
        value = M.observation_time({"utc_day": date(2026, 10, 12)})
        self.assertEqual(value.date(), date(2026, 10, 13))
        self.assertEqual(value.tzinfo, timezone.utc)

    def test_distinct_backend_exact_race_accepts(self):
        M.race_outcomes([True, False], [31, 32], False, Conflict)

    def test_distinct_backend_conflicting_race_accepts(self):
        M.race_outcomes([Conflict(), True], [31, 32], True, Conflict)

    def test_one_backend_or_double_insert_race_rejects(self):
        for results, pids in (([True, False], [31, 31]), ([True, True], [31, 32]), ([False, False], [31, 32])):
            with self.assertRaises(M.VerificationFailure):
                M.race_outcomes(results, pids, False, Conflict)

    def test_unrelated_named_exception_does_not_fake_conflict(self):
        lookalike = type("OutcomeEvidenceConflict", (RuntimeError,), {})()
        with self.assertRaises(M.VerificationFailure):
            M.race_outcomes([True, lookalike], [31, 32], True, Conflict)

    def test_missing_approval_flags_denies_before_git_or_database(self):
        import sys

        args = [
            "verifier",
            "--expected-head",
            HEAD,
            "--expected-application-head",
            HEAD,
            "--application-report",
            "synthetic.jsonl",
            "--expected-database",
            "social_manager",
            "--expected-schema",
            "test_schema",
            "--expected-role",
            "starck",
            "--receipt-digest",
            "a" * 64,
            "--journal-digest",
            "b" * 64,
            "--expected-receipts",
            "49",
            "--expected-journal",
            "152",
            "--expected-tenants",
            "289",
        ]
        with (
            patch.object(sys, "argv", args),
            patch.object(M.subprocess, "check_output") as git,
            patch.object(M, "emit"),
        ):
            self.assertEqual(M.main(), 1)
            git.assert_not_called()


class SourceContracts(unittest.TestCase):
    def test_all_required_functional_phases_survive_checkpoint_restore(self):
        import inspect

        source = inspect.getsource(M.verify)
        for label in (
            "accepted_application_definition_model_catalog_parity",
            "exact_replay_collision_tenant_isolation_immutable_reconciliation_and_utc_day",
            "event_mutation_refusal_and_same_transaction_disabled_guard_rollback",
            "direct_sql_payer_policy_time_binding_refusal",
            "direct_sql_provider_amount_consistency_refusal",
            "before_commit_rollback_and_after_successful_commit_lost_ack_replay",
            "barrier_synchronized_identical_and_conflicting_events",
            "preflight_to_final_retained_content_and_catalog_preservation",
        ):
            with self.subTest(phase=label):
                self.assertIn(label, source)
        self.assertIn("LLMOutcomeEvent.tenant_id.in_([1, 94])", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
