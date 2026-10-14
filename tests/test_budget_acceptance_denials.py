"""Isolated stdlib checks of the actual runner helper; no app/DB imports.

Execute only through the project's Kilo/test-runner/pare-test route.
"""

import ast
import unittest
from decimal import Decimal
from pathlib import Path

RUNNER = Path(__file__).parent / "acceptance/test_llm_budget_pg.py"
TREE = ast.parse(RUNNER.read_text(encoding="utf-8"), filename=str(RUNNER))
BLOCKED_NODE = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "Blocked")
PACKAGE_NODE = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "BudgetPackage")
DENIAL_NODE = next(
    node for node in PACKAGE_NODE.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "denied"
)
# Extract the real helper instead of importing the PG runner or duplicating it.
NAMESPACE = {"D": Decimal}
exec(compile(ast.Module(body=[BLOCKED_NODE, DENIAL_NODE], type_ignores=[]), str(RUNNER), "exec"), NAMESPACE)
DENIED = NAMESPACE["denied"]
Blocked = NAMESPACE["Blocked"]
RECORD = {"dispatch_id": "a" * 32}


class ExpectedDenial(RuntimeError):
    pass


class UnexpectedUnavailable(RuntimeError):
    pass


class Probe:
    def __init__(self, outcome):
        self.outcome = outcome
        self.events = []

    async def admit(self, *args):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def emit(self, kind, **values):
        self.events.append((kind, values))


class DenialAssertionTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_refusal_records_safe_type_only(self):
        probe = Probe(ExpectedDenial("must-not-be-logged"))
        await DENIED(probe, RECORD, expected=ExpectedDenial)
        self.assertEqual(
            probe.events,
            [("denial", {"dispatch_id": RECORD["dispatch_id"], "error_type": "ExpectedDenial"})],
        )

    async def test_unrelated_unavailability_is_not_a_pass(self):
        error = UnexpectedUnavailable("different failure")
        probe = Probe(error)
        with self.assertRaises(UnexpectedUnavailable) as caught:
            await DENIED(probe, RECORD, expected=ExpectedDenial)
        self.assertIs(caught.exception, error)
        self.assertEqual(probe.events, [])

    async def test_unrelated_validation_error_is_not_a_pass(self):
        probe = Probe(ValueError("different failure"))
        with self.assertRaises(ValueError):
            await DENIED(probe, RECORD, expected=ExpectedDenial)
        self.assertEqual(probe.events, [])

    async def test_fresh_admission_is_not_a_refusal(self):
        probe = Probe(True)
        with self.assertRaises(Blocked):
            await DENIED(probe, RECORD, expected=ExpectedDenial)
        self.assertEqual(probe.events, [])

    async def test_replay_is_not_a_refusal(self):
        probe = Probe(False)
        with self.assertRaises(Blocked):
            await DENIED(probe, RECORD, expected=ExpectedDenial)
        self.assertEqual(probe.events, [])

    async def test_expected_class_is_required(self):
        probe = Probe(ExpectedDenial("no implicit broad catch"))
        with self.assertRaises(TypeError):
            await DENIED(probe, RECORD)
        self.assertEqual(probe.events, [])
