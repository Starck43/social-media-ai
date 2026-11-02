"""Pure catalog tests; no DB, application import, provider call or network."""

import importlib.util
import secrets
import sys
import unittest
from pathlib import Path

SAMPLE_SECRET = secrets.token_hex(18)

_PATH = Path(__file__).resolve().parents[2] / "app/integrations/catalog/__init__.py"
_SPEC = importlib.util.spec_from_file_location("p12_t08_catalog_under_test", _PATH)
_CATALOG = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _CATALOG
_SPEC.loader.exec_module(_CATALOG)


class ConnectorCatalogTests(unittest.TestCase):
    def test_mail_profiles_have_distinct_fixed_tls_defaults_and_no_send(self):
        for provider, host in (("mailru_mail", "imap.mail.ru"), ("yandex_mail", "imap.yandex.ru")):
            config, secret = _CATALOG.validate_input(
                provider, "app_password", {"account_login": "user@example.com", "app_password": SAMPLE_SECRET}
            )
            self.assertEqual(config["imap_host"], host)
            self.assertEqual(config["imap_port"], 993)
            self.assertEqual(config["smtp_port"], 465)
            self.assertTrue(config["imap_tls"] and config["smtp_tls"])
            self.assertFalse(config["send_enabled"])
            self.assertNotIn(SAMPLE_SECRET, repr(config))
            self.assertEqual(secret, {"app_password": SAMPLE_SECRET})

    def test_wrong_method_and_extra_fields_are_rejected_without_echoing_values(self):
        with self.assertRaisesRegex(_CATALOG.InvalidConnectorInput, "unknown_auth_method"):
            _CATALOG.validate_input("yandex_disk", "app_password", {})
        with self.assertRaises(_CATALOG.InvalidConnectorInput) as caught:
            _CATALOG.validate_input(
                "mailru_mail",
                "app_password",
                {"account_login": "x@example.com", "app_password": SAMPLE_SECRET, "imap_host": "evil"},
            )
        self.assertNotIn(SAMPLE_SECRET, str(caught.exception))
        self.assertNotIn("evil", str(caught.exception))

    def test_missing_and_malformed_fields_fail_closed(self):
        for values in (
            {},
            {"account_login": "wrong", "app_password": SAMPLE_SECRET},
            {"account_login": "a@example.com", "app_password": ""},
        ):
            with self.assertRaises(_CATALOG.InvalidConnectorInput):
                _CATALOG.validate_input("mailru_mail", "app_password", values)

    def test_unknown_provider_and_non_string_values(self):
        with self.assertRaisesRegex(_CATALOG.InvalidConnectorInput, "unknown_provider"):
            _CATALOG.validate_input("other", "oauth", {})
        with self.assertRaisesRegex(_CATALOG.InvalidConnectorInput, "invalid_app_password"):
            _CATALOG.validate_input(
                "mailru_mail", "app_password", {"account_login": "a@example.com", "app_password": 42}
            )

    def test_manual_vk_and_oauth_are_distinct(self):
        self.assertEqual(_CATALOG.validate_input("vk", "oauth", {}), ({}, {}))
        self.assertEqual(
            _CATALOG.validate_input("vk", "user_token", {"user_token": "abc"}), ({}, {"user_token": "abc"})
        )

    def test_provider_registry_and_defaults_are_not_mutated(self):
        with self.assertRaises(TypeError):
            _CATALOG.PROVIDERS["other"] = _CATALOG.VK
        first, _ = _CATALOG.validate_input(
            "mailru_mail", "app_password", {"account_login": "a@example.com", "app_password": "p"}
        )
        first["imap_host"] = "other"
        second, _ = _CATALOG.validate_input(
            "mailru_mail", "app_password", {"account_login": "a@example.com", "app_password": "p"}
        )
        self.assertEqual(second["imap_host"], "imap.mail.ru")


class ExistingStatusTests(unittest.TestCase):
    """Run status helpers with local import doubles, without application imports."""

    @classmethod
    def setUpClass(cls):
        from types import ModuleType, SimpleNamespace

        from tests.source_import_isolation import load_isolated_source

        root = Path(__file__).resolve().parents[2]
        config = ModuleType("app.core.config")
        config.settings = SimpleNamespace(VK_APP_ID="test")
        platform = load_isolated_source("_p12_t08_platform", root / "app/services/social/platform_config.py")
        cls.connections = load_isolated_source(
            "_p12_t08_connections",
            root / "app/services/social/connections.py",
            {"app.core.config": config, "app.services.social.platform_config": platform},
        )

    def test_saved_business_credential_is_not_claimed_verified(self):
        from types import SimpleNamespace

        row = SimpleNamespace(platform="email", is_active=True, meta={"tenant_id": 7})
        card = next(
            c
            for c in self.connections.build_workspace_connectors(credentials=[row], tenant_id=7)
            if c["platform_type"] == "email"
        )
        self.assertIn("не проверено", card["health_diagnostic"])
        self.assertEqual(card["mounted_folders"], [])
        self.assertEqual(card["watermark"], "Нет данных о синхронизации")

    def test_status_labels_saved_not_verified_for_business(self):
        import asyncio
        from types import SimpleNamespace

        async def lookup(*args):
            return SimpleNamespace(expires_at=None, meta={})

        old = self.connections._vault_row
        self.connections._vault_row = lookup
        try:
            status = asyncio.run(self.connections.connection_status("email", 101))
        finally:
            self.connections._vault_row = old
        self.assertEqual(status.label, "Сохранено")
        self.assertIn("не проверено", status.detail)

    def test_settings_warns_legacy_form_is_not_workspace_connection(self):
        from tests.shared.settings_layout_fixtures import context, environment

        ctx = context("connections")
        ctx["connections"] = [
            {
                "platform": "email",
                "title": "Почта",
                "purpose": "Почта",
                "is_oauth": False,
                "status": None,
                "manual_kinds": [],
                "manual_hint": "",
                "rows": [],
                "details": None,
            }
        ]
        html = environment().get_template("web/settings.html").render(**ctx)
        self.assertIn("не настраивает подключение", html)
        self.assertIn("не проверяет доступ", html)


if __name__ == "__main__":
    unittest.main()
