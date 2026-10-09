"""Pure outcome tests; also runnable with stdlib unittest, without app imports."""

import importlib.util
import unittest
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "app/services/digest/delivery_outcomes.py"
_spec = importlib.util.spec_from_file_location("delivery_outcomes", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
summarize_delivery = _module.summarize_delivery
DeliveryFailure = _module.DeliveryFailure


def ledger(*targets):
    return {"run_id": 12, "targets": [{"parts": [{"status": s} for s in statuses]} for statuses in targets]}


class DeliveryOutcomeTests(unittest.TestCase):
    def test_all_sent_only_is_success(self):
        result = summarize_delivery(ledger(["sent", "sent"], ["sent"]))
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["total_parts"], 3)
        self.assertFalse(result["retryable"])

    def test_partial_known_rejection_is_not_success(self):
        result = summarize_delivery(ledger(["sent", "rejected", "pending"], ["sent"]))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sent_parts"], 2)
        self.assertTrue(result["retryable"])

    def test_pending_is_not_delivered(self):
        result = summarize_delivery(ledger(["pending"]))
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retryable"])

    def test_uncertain_blocks_automatic_retry(self):
        result = summarize_delivery(ledger(["sent"], ["uncertain", "pending"]))
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["retryable"])

    def test_in_flight_is_uncertain_even_with_other_success(self):
        result = summarize_delivery(ledger(["in_flight"], ["sent"]))
        self.assertEqual(result["uncertain_parts"], 1)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["retryable"])

    def test_revocation_is_blocked_not_retryable(self):
        result = summarize_delivery(ledger(["blocked"], ["rejected"]))
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["retryable"])

    def test_empty_ledger_cannot_claim_success(self):
        with self.assertRaises(ValueError):
            summarize_delivery(ledger([]))

    def test_failure_defaults_to_terminal_safe_code(self):
        failure = DeliveryFailure("summary_build_requires_reconciliation")
        self.assertFalse(failure.retryable)
        self.assertEqual(failure.result["status"], "blocked")
        self.assertEqual(str(failure), "summary_build_requires_reconciliation")


if __name__ == "__main__":
    unittest.main()
