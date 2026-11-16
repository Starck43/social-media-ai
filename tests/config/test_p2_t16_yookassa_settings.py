"""Source-extracted field contracts, not application/BaseSettings integration.

Run with stdlib unittest, not pytest: no app import, environment loading, ORM,
network or conftest fixtures. Only the seven payment fields and their two exact
validators are compiled on Pydantic BaseModel. This deliberately cannot certify
the application settings singleton, checkout functionality or future migrations.
"""

import ast
import sys
import unittest
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[2]
FIELDS = {
    "SUBSCRIPTION_PAYMENT_PROVIDER", "SUBSCRIPTION_PAYMENT_MODE", "SUBSCRIPTION_CHECKOUT_ENABLED",
    "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY", "YOOKASSA_RETURN_URL", "YOOKASSA_HTTP_TIMEOUT",
}
VALIDATORS = {"_yookassa_shop_id", "_yookassa_return_url"}


def settings_contract():
    tree = ast.parse((ROOT / "app/core/config.py").read_text())
    settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    nodes = [
        node for node in settings.body
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in FIELDS)
        or (isinstance(node, ast.FunctionDef) and node.name in VALIDATORS)
    ]
    if len(nodes) != len(FIELDS) + len(VALIDATORS):
        raise AssertionError("Payment field/validator inventory changed; review the isolated contract")
    namespace = {
        "__name__": "p2_t16_extracted_settings", "BaseModel": BaseModel,
        "Field": Field, "SecretStr": SecretStr, "Literal": Literal, "field_validator": field_validator,
    }
    isolated = ast.Module(body=[ast.ClassDef(
        name="PaymentSettingsContract", bases=[ast.Name(id="BaseModel", ctx=ast.Load())],
        keywords=[], body=nodes, decorator_list=[],
    )], type_ignores=[])
    exec(compile(ast.fix_missing_locations(isolated), "<p2-t16-field-contract>", "exec"), namespace)
    return namespace["PaymentSettingsContract"]


class YooKassaSettingsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Settings = settings_contract()

    def tearDown(self):
        self.assertFalse(any(name == "app" or name.startswith("app.") for name in sys.modules))

    def test_safe_defaults(self):
        value = self.Settings()
        self.assertEqual(value.SUBSCRIPTION_PAYMENT_PROVIDER, "yookassa")
        self.assertEqual(value.SUBSCRIPTION_PAYMENT_MODE, "test")
        self.assertIs(value.SUBSCRIPTION_CHECKOUT_ENABLED, False)
        self.assertEqual(value.YOOKASSA_SHOP_ID, "")
        self.assertEqual(value.YOOKASSA_RETURN_URL, "")
        self.assertEqual(value.YOOKASSA_SECRET_KEY.get_secret_value(), "")
        self.assertEqual(value.YOOKASSA_HTTP_TIMEOUT, 15)

    def test_unknown_provider_rejected(self):
        for value in ("other", "", "YooKassa"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.Settings(SUBSCRIPTION_PAYMENT_PROVIDER=value)

    def test_live_and_unknown_modes_rejected(self):
        for value in ("live", "production", "sandbox", ""):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.Settings(SUBSCRIPTION_PAYMENT_MODE=value)

    def test_shop_id_is_trimmed_and_ascii_numeric(self):
        self.assertEqual(self.Settings(YOOKASSA_SHOP_ID=" 123456 ").YOOKASSA_SHOP_ID, "123456")
        for value in ("merchant", "12/34", "１２３", "١٢٣", "123\n456", "-123"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.Settings(YOOKASSA_SHOP_ID=value)

    def test_allowed_return_urls(self):
        for value in ("https://example.com/app/settings", "https://example.com:443/return",
                      "https://example.com:8443/return?status=pending"):
            with self.subTest(value=value):
                self.assertEqual(self.Settings(YOOKASSA_RETURN_URL=value).YOOKASSA_RETURN_URL, value)

    def test_unsafe_return_urls_rejected(self):
        for value in ("http://example.com", "//example.com", "https:///missing", "https://user:pass@example.com",
                      "https://example.com/#token", "https://example.com:99999/return", "https://example.com:80",
                      "https://example.com/a b", "https://example.com/a\nb", "javascript:alert(1)"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.Settings(YOOKASSA_RETURN_URL=value)

    def test_empty_credentials_are_backward_compatible(self):
        value = self.Settings(YOOKASSA_SHOP_ID="", YOOKASSA_SECRET_KEY="", YOOKASSA_RETURN_URL="")
        self.assertFalse(value.SUBSCRIPTION_CHECKOUT_ENABLED)

    def test_secret_is_excluded_from_dump_and_repr(self):
        sentinel = "synthetic-not-a-real-provider-key"
        value = self.Settings(YOOKASSA_SECRET_KEY=sentinel)
        self.assertEqual(value.YOOKASSA_SECRET_KEY.get_secret_value(), sentinel)
        self.assertNotIn("YOOKASSA_SECRET_KEY", value.model_dump())
        self.assertNotIn(sentinel, value.model_dump_json())
        self.assertNotIn(sentinel, repr(value))
        self.assertNotIn("YOOKASSA_SECRET_KEY", repr(value))

    def test_timeout_bounds(self):
        for value in (0, -1, 31, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.Settings(YOOKASSA_HTTP_TIMEOUT=value)
        self.assertEqual(self.Settings(YOOKASSA_HTTP_TIMEOUT=1).YOOKASSA_HTTP_TIMEOUT, 1)
        self.assertEqual(self.Settings(YOOKASSA_HTTP_TIMEOUT=30).YOOKASSA_HTTP_TIMEOUT, 30)

    def test_env_example_matches_the_seven_fields(self):
        entries = {}
        for line in (ROOT / ".env.example").read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in FIELDS:
                self.assertNotIn(key, entries, f"Duplicate payment setting {key}")
                entries[key] = value
        self.assertEqual(set(entries), FIELDS)
        value = self.Settings(**entries)
        self.assertFalse(value.SUBSCRIPTION_CHECKOUT_ENABLED)
        self.assertEqual(value.SUBSCRIPTION_PAYMENT_PROVIDER, "yookassa")
        self.assertEqual(value.SUBSCRIPTION_PAYMENT_MODE, "test")
        self.assertEqual(value.YOOKASSA_SECRET_KEY.get_secret_value(), "")


if __name__ == "__main__":
    unittest.main()
