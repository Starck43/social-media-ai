"""Pure frozen-contract checks. No application import, DB, historical suite rerun."""

import ast
import hashlib
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("tests/acceptance/test_llm_budget_pg.py", "_coherence_runner")
preparation = load("scripts/prepare_llm_budget_fixture.py", "_coherence_preparation")
template = json.loads((ROOT / "tests/shared/fixtures/llm-budget-pg-approval.isolated.example.json").read_text())
frozen = json.loads(
    subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "show",
            "8869263046368af840bbe5a9dc8c17c3e2c99eea:docs/validation/llm-budget-pg-approval.example.json",
        ]
    )
)


class ContractCoherence(unittest.TestCase):
    def test_frozen_fields(self):
        for field in (
            "dispatch_ids",
            "fixture_slugs",
            "scenario_ids",
            "operations",
            "run_id",
            "synthetic_model_id",
            "synthetic_provider_id",
            "retained_receipts",
            "retained_reservations",
            "retained_days",
            "sequence_names",
        ):
            with self.subTest(field=field):
                self.assertEqual(template[field], frozen[field])

    def test_derived_dispatch_ids(self):
        self.assertEqual(
            template["dispatch_ids"],
            {
                key: hashlib.sha256((template["run_id"] + ":" + key).encode()).hexdigest()[:32]
                for key in runner.IDENTITIES
            },
        )

    def test_fixture_slugs_and_allocations(self):
        self.assertEqual(template["fixture_slugs"], {label: slug for label, slug, *_ in preparation.FIXTURES})
        self.assertEqual(len(set(preparation.FIXTURE_TENANT_IDS.values())), 18)
        self.assertEqual(template["fixture_slugs"]["inactive"], "budget-review-7417a3ed-inactive")

    def test_gate_template_not_executable(self):
        for key in (
            "write_approval",
            "nonproduction",
            "exclusive_window",
            "gate_a_complete",
            "gate_b_accepted",
            "report_isolation_verified",
            "catalog_parity_reviewed",
            "alembic_absence_verified",
            "target_owner_attestation",
        ):
            self.assertFalse(template[key], key)
        for key in ("runner_sha", "catalog_sha256", "acl_manifest_sha256", "revision_schema", "revision_rows"):
            self.assertIsNone(template[key])
        self.assertTrue(all(value is None for value in template["fixtures"].values()))

    def test_preparation_pin_matches_committed_bytes(self):
        self.assertEqual(template["provenance"]["preparation_script"], runner.PREPARATION_PIN)
        self.assertIsNotNone(__import__("re").fullmatch(r"[0-9a-f]{40}", runner.PREPARATION_PIN))
        preparation.verify_script_pin(runner.PREPARATION_PIN)
        self.assertEqual(template["provenance"]["manifest_file"], "apply.json")

    def test_scope_and_guards(self):
        for field, constant, expected in (
            ("isolated_scope", "ISOLATED_SCOPE", preparation.DEFINED_RELATIONS),
            ("isolated_functions", "ISOLATED_FUNCTIONS", preparation.GUARD_FUNCTIONS),
            ("isolated_triggers", "ISOLATED_TRIGGERS", preparation.GUARD_TRIGGERS),
        ):
            self.assertEqual(template[field], list(getattr(runner, constant)))
            self.assertEqual(getattr(runner, constant), expected)

    def test_actual_admission_privilege_serialization(self):
        self.assertEqual(template["isolated_privileges"], [f"{t}:{p}" for t, p in runner.ISOLATED_PRIVILEGES])
        self.assertEqual(runner.ISOLATED_PRIVILEGES, preparation.RUNNER_PRIVILEGES)
        self.assertIn(("llm_budget_reservations", "DELETE"), preparation.RUNNER_PRIVILEGES)
        self.assertIn(("llm_budget_reservations", "TRUNCATE"), preparation.RUNNER_PRIVILEGES)
        self.assertNotIn(("llm_dispatch_receipts", "UPDATE"), preparation.RUNNER_PRIVILEGES)

    def test_full_sequence_scope(self):
        self.assertEqual(preparation.SEQUENCE_TABLES, preparation.DEFINED_RELATIONS)
        self.assertIn("tenants", preparation.SEQUENCE_TABLES)
        self.assertIn("llm_outcome_events", preparation.SEQUENCE_TABLES)

    def test_phase_authorizations_are_distinct(self):
        self.assertEqual(set(preparation.APPROVALS), {"preflight", "rehearsal", "apply"})
        self.assertEqual(len(set(preparation.APPROVALS.values())), 3)

    def test_target_is_dedicated(self):
        self.assertEqual(
            template["target"],
            [preparation.TARGET_HOST, preparation.TARGET_PORT, preparation.TARGET_DATABASE, preparation.RUNNER_ROLE],
        )
        self.assertNotEqual(preparation.TARGET_DATABASE, "social_manager")
        self.assertNotIn(preparation.TARGET_SCHEMA, preparation.FORBIDDEN_SCHEMAS)
        self.assertEqual(template["ddl_role"], preparation.DDL_OWNER)

    def test_normal_revision_method_unchanged_after_extraction(self):
        source = (ROOT / "tests/acceptance/test_llm_budget_pg.py").read_text()
        self.assertIn(
            'demand((await s.execute(self.text(q))).scalars().all() == ["0095"], "revision-mismatch")', source
        )
        self.assertIn('demand(self.cfg["revision_rows"] == ["0095"], "approved-revision-mismatch")', source)
        self.assertEqual(runner.MODES, ("revision-0095", "isolated-synthetic-fixture"))
        tree = ast.parse(source)
        package = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BudgetPackage")
        run = next(n for n in package.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
        self.assertTrue(
            any(
                isinstance(n, ast.Await)
                and isinstance(n.value, ast.Call)
                and getattr(n.value.func, "attr", None) == "verify_configured_target"
                for n in ast.walk(run)
            )
        )
        self.assertFalse(
            any(
                isinstance(n, ast.IfExp) and (isinstance(n.body, ast.Await) or isinstance(n.orelse, ast.Await))
                for n in ast.walk(run)
            )
        )


if __name__ == "__main__":
    unittest.main()
