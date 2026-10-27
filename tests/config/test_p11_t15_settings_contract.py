"""Real BaseSettings compatibility in a synthetic process; no DB/root fixtures.

Run with the profile Python as a standalone unittest script, not project pytest.
Only config/views are imported; dotenv/env fixtures are temporary and synthetic.
"""

from __future__ import annotations

import importlib.abc
import json
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

# This real-Settings harness must not inherit project DB autouse fixtures.
# Standalone unittest is the owned execution route; pytest does not collect it.
__test__ = False

ROOT = Path(__file__).resolve().parents[2]


class NoDatabaseImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.main", "app.runtime", "app.worker", "app.core.database"} or fullname.startswith(
            "app.models"
        ):
            raise AssertionError(f"Settings test forbids runtime/DB imports: {fullname}")
        return None


class SettingsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_env = os.environ.copy()
        cls.temporary = tempfile.TemporaryDirectory(prefix="settings-contract-")
        cls.secret = secrets.token_urlsafe(32)
        os.environ.clear()
        os.environ.update(
            SECRET_KEY=cls.secret,
            POSTGRES_URL="postgresql://synthetic.invalid/unused",
            PYTHON_DOTENV_DISABLED="1",
            HOME=cls.temporary.name,
        )
        sys.path.insert(0, str(ROOT))
        sys.meta_path.insert(0, NoDatabaseImports())
        from app.core.config import Settings

        cls.Settings = Settings

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        os.environ.clear()
        os.environ.update(cls.old_env)

    def tearDown(self):
        os.environ.pop("PORT", None)

    def test_flat_fields_defaults_types_and_requiredness_are_unchanged(self):
        from pydantic import SecretStr
        from pydantic_core import PydanticUndefined

        expected = json.loads((ROOT / "tests/shared/fixtures/p11-t15-settings-contract.json").read_text())
        actual = {}
        for name, field in self.Settings.model_fields.items():
            default = field.default
            actual[name] = {
                "annotation": str(field.annotation),
                "required": field.is_required(),
                "factory": field.default_factory is not None,
                "default": (
                    "<required>"
                    if default is PydanticUndefined
                    else "<secret>" if isinstance(default, SecretStr) else default
                ),
            }
        self.assertEqual(actual, expected)

    def test_env_dotenv_init_and_default_precedence(self):
        env_file = Path(self.temporary.name) / "synthetic.env"
        env_file.write_text("\n".join(["PORT=8123", "DEBUG=true", "UNKNOWN_OPTION=ignored"]) + "\n")
        self.assertEqual(self.Settings(_env_file=None).PORT, 8000)
        self.assertEqual(self.Settings(_env_file=env_file).PORT, 8123)
        self.assertTrue(self.Settings(_env_file=env_file).DEBUG)
        os.environ["PORT"] = "9234"
        self.assertEqual(self.Settings(_env_file=env_file).PORT, 9234)
        self.assertEqual(self.Settings(_env_file=env_file, PORT=9123).PORT, 9123)

    def test_env_keys_are_still_flat_case_insensitive(self):
        os.environ["port"] = "9123"
        try:
            self.assertEqual(self.Settings(_env_file=None).PORT, 9123)
        finally:
            os.environ.pop("port")

    def test_required_keys_and_existing_validators_stay_fail_closed(self):
        from pydantic import ValidationError

        for kwargs in (
            {"ALGORITHM": "RS256"},
            {"MAX_WORKSPACE_MEMBERS": 0},
            {"RUNTIME_STATE_DIR": "relative"},
            {"DATA_RETENTION_MONTHS": 0},
            {"SUBSCRIPTION_PAYMENT_MODE": "live"},
        ):
            with self.subTest(keys=list(kwargs)), self.assertRaises(ValidationError):
                self.Settings(_env_file=None, **kwargs)
        saved = os.environ.pop("SECRET_KEY")
        try:
            with self.assertRaises(ValidationError):
                self.Settings(_env_file=None)
        finally:
            os.environ["SECRET_KEY"] = saved

    def test_views_are_narrow_immutable_and_secrets_are_masked(self):
        from pydantic import ValidationError

        settings = self.Settings(_env_file=None)
        self.assertEqual(settings.database.POSTGRES_URL.get_secret_value(), settings.POSTGRES_URL)
        self.assertNotIn(self.secret, repr(settings))
        self.assertNotIn(self.secret, json.dumps(settings.safe_dict()))
        self.assertNotIn(self.secret, settings.security.model_dump_json())
        self.assertNotIn(settings.POSTGRES_URL, settings.database.model_dump_json())
        self.assertEqual(settings.security.SECRET_KEY.get_secret_value(), self.secret)
        for view in (settings.database, settings.security, settings.collection, settings.llm):
            self.assertFalse(hasattr(view, "MAX_WORKSPACE_MEMBERS"))
            with self.assertRaises(ValidationError):
                setattr(view, next(iter(type(view).model_fields)), "replacement")

    def test_view_refresh_preserves_legacy_mutation_compatibility(self):
        settings = self.Settings(_env_file=None)
        before = settings.collection
        settings.SOCIAL_COLLECT_LIMIT = 17
        self.assertEqual(settings.collection.SOCIAL_COLLECT_LIMIT, 17)
        self.assertEqual(before.SOCIAL_COLLECT_LIMIT, 1000)
        self.assertEqual(settings.MAX_WORKSPACE_MEMBERS, 2)
        self.assertFalse(settings.PERSONAL_BILLING_ENABLED)
        self.assertFalse(settings.SUBSCRIPTION_CHECKOUT_ENABLED)

    def test_legacy_dump_and_secret_types_are_not_silently_changed(self):
        settings = self.Settings(_env_file=None)
        self.assertIsInstance(settings.SECRET_KEY, str)
        self.assertEqual(settings.model_dump()["SECRET_KEY"], self.secret)
        self.assertNotIn("YOOKASSA_SECRET_KEY", settings.model_dump())
        self.assertEqual(settings.get_token_expire_minutes(), 43200)
        settings.ENVIRONMENT = "production"
        self.assertEqual(settings.get_token_expire_minutes(), 60)

    def test_no_database_or_runtime_was_imported(self):
        self.assertTrue(
            {"app.core.database", "app.models", "app.main", "app.worker", "app.runtime"}.isdisjoint(sys.modules)
        )


if __name__ == "__main__":

    def no_network(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.sendto"}:
            raise AssertionError("Network forbidden in Settings contract")

    sys.addaudithook(no_network)
    unittest.main()
