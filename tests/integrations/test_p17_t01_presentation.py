"""1C presentation reflects the dedicated flow, not the generic mail form."""

from app.integrations.catalog import PROVIDERS, WORKSPACE_PROVIDER_IDS
from tests.shared.settings_layout_fixtures import context, environment


def test_onec_catalog_and_card_point_to_dedicated_flow():
    provider = PROVIDERS["onec"]
    assert provider.availability == "workspace_health"
    assert provider.capabilities == ("health_check",)
    assert "onec" not in WORKSPACE_PROVIDER_IDS
    ctx = context("connections")
    ctx["perms"].can_open_chat = False
    ctx["connections"] = [
        dict(
            platform="onec",
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
    assert 'href="/app/settings/onec"' in html
    assert "/app/settings/workspace-connections" not in html
