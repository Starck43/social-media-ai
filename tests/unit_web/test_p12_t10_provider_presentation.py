"""Provider presentation is local, truthful and cannot expose saved secrets."""

from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.integrations.catalog import CATALOG_VERSION, PROVIDERS, WORKSPACE_PROVIDERS
from tests.shared.settings_layout_fixtures import context, environment

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def test_registry_covers_all_providers_and_keeps_mail_version():
    assert CATALOG_VERSION == 1
    assert set(PROVIDERS) == {
        "vk",
        "telegram",
        "yandex_mail",
        "mailru_mail",
        "yandex_disk",
        "mailru_cloud",
        "bitrix24",
        "onec",
        "local_gateway",
    }
    assert {p.id for p in WORKSPACE_PROVIDERS} == {"mailru_mail", "yandex_mail"}
    assert {p.id for p in PROVIDERS.values() if p.availability == "unavailable"} == {
        "yandex_disk",
        "mailru_cloud",
        "bitrix24",
        "onec",
        "local_gateway",
    }
    assert all(not p.capabilities for p in PROVIDERS.values() if p.availability == "unavailable")
    for provider in PROVIDERS.values():
        assert provider.description and provider.category and provider.setup_hint and provider.auth_protocol
        assert provider.methods and provider.icon_asset.startswith("icons/providers/")
        icon = ROOT / "app/static" / provider.icon_asset
        tree = ElementTree.parse(icon)
        assert tree.getroot().tag == "{http://www.w3.org/2000/svg}svg"
        assert "<script" not in icon.read_text().lower()
        assert "href=" not in icon.read_text().lower()


def test_shared_macros_escape_values_and_never_fill_secrets():
    template = environment().from_string(
        """
    {% from "web/components/provider.html" import provider_icon, provider_fields %}
    {{ provider_icon(provider) }}
    {{ provider_fields(provider.methods[0], "test", login) }}"""
    )
    html = template.render(provider=PROVIDERS["mailru_mail"], login='x"><script>bad</script>')
    assert 'src="http://fixture.test/static/icons/providers/mailru-mail.svg"' in html
    assert 'aria-hidden="true"' in html and 'alt=""' in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'type="password"' in html
    password = html.split('name="app_password"', 1)[1].split(">", 1)[0]
    assert "value=" not in password


@pytest.mark.parametrize("provider_id", ["bitrix24", "onec", "yandex_disk", "mailru_cloud", "local_gateway"])
def test_unavailable_provider_has_no_save_or_connect_form(provider_id):
    provider = PROVIDERS[provider_id]
    ctx = context("connections")
    ctx["connections"] = [
        dict(
            platform=provider.platform,
            provider=provider,
            title=provider.label,
            purpose=provider.description,
            is_oauth=False,
            status=None,
            manual_kinds=[],
            rows=[],
            details=None,
        )
    ]
    html = environment().get_template("web/settings.html").render(**ctx)
    assert provider.icon_asset in html
    assert "пока недоступны" in html
    for method in provider.methods:
        for field in method.fields:
            assert f'name="{field.name}"' not in html
    assert "/app/settings/workspace-connections" not in html


@pytest.mark.parametrize(
    "provider_id", ["bitrix24", "onec", "yandex_disk", "mailru_cloud", "local_gateway", "vk", "telegram"]
)
def test_workspace_manager_rejects_descriptors_outside_allowlist(provider_id):
    from app.integrations.catalog import InvalidConnectorInput
    from app.models.managers.workspace_connection_manager import _configuration

    with pytest.raises(InvalidConnectorInput, match="unsupported_provider"):
        _configuration(provider_id, "any", {}, "")
