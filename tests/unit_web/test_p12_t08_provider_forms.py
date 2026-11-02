"""Strict Jinja contract for provider fields, no saved secret projection."""

import secrets

from app.integrations.catalog import WORKSPACE_PROVIDERS
from tests.shared.settings_layout_fixtures import context, environment

__source_only__ = True
MARKER = secrets.token_hex(18)


def test_mail_forms_are_catalog_driven_and_do_not_render_secrets():
    ctx = context("connections")
    ctx.update(
        workspace_connection_write=True,
        workspace_providers=WORKSPACE_PROVIDERS,
        workspace_connection_rows=[
            dict(
                id=41,
                label="Purchasing",
                provider_id="mailru_mail",
                provider_label="Mail.ru",
                auth_method="app_password",
                account_login="mock@mail.ru",
                state="unverified",
                state_label="Сохранено · не проверено",
                checked_at=None,
                is_active=True,
                check_detail="",
                secret_encrypted=MARKER,
                app_password=MARKER,
            )
        ],
    )
    ctx["connections"] = [
        dict(
            platform="email",
            title="Почта",
            purpose="Почта",
            is_oauth=False,
            status=None,
            manual_kinds=[],
            rows=[],
            details=None,
            provider_forms=WORKSPACE_PROVIDERS,
        )
    ]
    html = environment().get_template("web/settings.html").render(**ctx)
    for provider in WORKSPACE_PROVIDERS:
        assert f'data-provider="{provider.id}"' in html
        for field in provider.methods[0].fields:
            assert f'id="mail-{provider.id}-{field.name}"' in html
    assert html.count('data-connector-platform="email"') == 1
    assert 'type="password"' in html and 'name="account_login"' in html
    assert MARKER not in html
    assert "Проверить вход по IMAP" in html and "Отправка писем выключена" in html
    assert 'name="imap_host"' not in html and 'name="secret"' not in html
    assert 'name="tenant_id"' not in html


def test_read_only_member_has_no_create_edit_or_network_buttons():
    ctx = context("connections")
    ctx.update(workspace_connection_write=False, workspace_connection_rows=[])
    ctx["connections"] = [
        dict(
            platform="email",
            title="Почта",
            purpose="Почта",
            is_oauth=False,
            status=None,
            manual_kinds=[],
            rows=[],
            details=None,
            provider_forms=WORKSPACE_PROVIDERS,
        )
    ]
    html = environment().get_template("web/settings.html").render(**ctx)
    assert 'action="/app/settings/workspace-connections' not in html
    assert "владелец, соадминистратор и менеджер" in html
