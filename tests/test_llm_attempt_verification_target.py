"""Configuration-only target guards; no app import, DB, private env or DDL."""

import unittest
from pathlib import Path

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
MODULE = load_isolated_source("_journal_target_guard", ROOT / "scripts/verify_llm_attempt_persistence.py")


class VerificationTargetTests(unittest.TestCase):
    def guard(self, **changes):
        values = {
            "working_url": "postgresql://test:dummy@localhost/social_manager",
            "working_schema": "working",
            "test_url": "postgresql://test:dummy@localhost/social_manager",
            "test_schema": "test_schema",
            "expected_database": "social_manager",
            "expected_schema": "test_schema",
        }
        values.update(changes)
        return MODULE.validate_target(**values)

    def test_separate_schema_in_shared_database_is_allowed(self):
        self.assertIsNone(self.guard())

    def test_separate_database_is_allowed(self):
        self.assertIsNone(
            self.guard(test_url="postgresql://test:dummy@localhost/isolated", expected_database="isolated")
        )

    def test_asyncpg_scheme_is_allowed(self):
        self.assertIsNone(self.guard(test_url="postgresql+asyncpg://test:dummy@localhost/social_manager"))

    def test_same_working_database_and_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            self.guard(test_schema="working", expected_schema="working")

    def test_different_host_does_not_override_conservative_name_guard(self):
        with self.assertRaises(ValueError):
            self.guard(
                test_url="postgresql://test:dummy@alias/social_manager",
                test_schema="working",
                expected_schema="working",
            )

    def test_database_or_schema_approval_mismatch_is_rejected(self):
        for change in ({"expected_database": "wrong"}, {"expected_schema": "wrong"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.guard(**change)

    def test_missing_working_isolation_is_rejected(self):
        for change in ({"working_url": ""}, {"working_schema": ""}, {"test_schema": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.guard(**change)

    def test_missing_or_non_postgresql_test_target_is_rejected(self):
        for url in ("", "sqlite:///social_manager", "postgresql:///social_manager", "postgresql://localhost"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.guard(test_url=url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
