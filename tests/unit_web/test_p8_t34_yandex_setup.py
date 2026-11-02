"""Settings template contract; no DB, credentials or provider calls."""

from app.integrations.catalog import WORKSPACE_PROVIDERS
from tests.shared.settings_layout_fixtures import context, environment

__source_only__ = True


def test_yandex_setup_is_separate_from_mail_and_never_displays_secret():
    ctx = context("connections")
    ctx.update(
        workspace_connection_write=True,
        workspace_providers=WORKSPACE_PROVIDERS,
        workspace_connection_rows=[
            dict(
                id=91, label="Disk & safe", provider_id="yandex_disk", is_active=True, secret_encrypted="DO-NOT-RENDER"
            ),
            dict(
                id=92,
                label="Mail",
                provider_id="yandex_mail",
                provider_label="Mail",
                auth_method="app_password",
                account_login="mock@example.test",
                state_label="Saved",
                checked_at=None,
                is_active=True,
                check_detail="",
            ),
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
        ),
        dict(
            platform="yandex_disk",
            title="Яндекс.Диск",
            purpose="Файлы",
            is_oauth=False,
            status=None,
            manual_kinds=[],
            rows=[],
            details=None,
            provider_forms=(),
        ),
    ]
    html = environment().get_template("web/settings.html").render(**ctx)
    assert 'action="/app/settings/yandex-connections"' in html
    assert 'name="oauth_token" type="password"' in html
    assert 'name="scope_ack"' in html and "может охватывать весь диск" in html
    assert 'data-yandex-connection="91"' in html and 'data-workspace-connection="91"' not in html
    assert 'data-workspace-connection="92"' in html and 'data-yandex-connection="92"' not in html
    assert "Disk &amp; safe" in html and "DO-NOT-RENDER" not in html
    assert "доступ не проверен" in html and "ещё в разработке" in html


def test_viewer_does_not_get_connection_form():
    ctx = context("connections")
    ctx.update(workspace_connection_write=False, workspace_connection_rows=[])
    ctx["connections"] = [
        dict(
            platform="yandex_disk",
            title="Яндекс.Диск",
            purpose="Файлы",
            is_oauth=False,
            status=None,
            manual_kinds=[],
            rows=[],
            details=None,
            provider_forms=(),
        )
    ]
    html = environment().get_template("web/settings.html").render(**ctx)
    assert 'action="/app/settings/yandex-connections"' not in html
