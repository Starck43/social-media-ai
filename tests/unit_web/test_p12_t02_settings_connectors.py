"""Isolated unit tests for P12-T02 workspace connectors, PlatformType & cloud drive mounting."""

from __future__ import annotations

import runpy
import secrets
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tests.source_import_isolation import load_isolated_source

__source_only__ = True
SAMPLE_SECRET = secrets.token_hex(18)
ROOT = Path(__file__).resolve().parents[2]


def _load_platform_types():
    fake_config = types.ModuleType("app.core.config")
    fake_config.settings = NS(DB_SCHEMA="test_schema")
    db_enums = load_isolated_source(
        "_p12_t02_db_enums",
        ROOT / "app/utils/db_enums.py",
        {"app.core.config": fake_config},
    )
    return load_isolated_source(
        "_p12_t02_platform_types",
        ROOT / "app/types/enums/platform_types.py",
        {"app.utils.db_enums": db_enums},
    )


def _load_platform_config():
    return load_isolated_source(
        "_p12_t02_platform_config",
        ROOT / "app/services/social/platform_config.py",
    )


def _load_connections():
    platform_types = _load_platform_types()
    platform_config = _load_platform_config()
    fake_config = types.ModuleType("app.core.config")
    fake_config.settings = NS(VK_APP_ID="vk-app-test")
    return load_isolated_source(
        "_p12_t02_connections",
        ROOT / "app/services/social/connections.py",
        {
            "app.core.config": fake_config,
            "app.services.social.platform_config": platform_config,
            "app.types.enums.platform_types": platform_types,
        },
    )


def test_platform_type_and_source_type_drive_folder_expansion():
    mod = _load_platform_types()
    PlatformType = mod.PlatformType
    SourceType = mod.SourceType

    expected_platforms = {
        "vk": "ВКонтакте",
        "telegram": "Telegram",
        "email": "Почта IMAP/SMTP",
        "bitrix24": "Битрикс24 CRM",
        "onec": "1С:Предприятие OData",
        "yandex_disk": "Яндекс.Диск",
        "mailru_cloud": "Облако Mail.ru",
        "local_gateway": "Локальный шлюз",
    }
    assert {p.db_value: p.display_name for p in PlatformType} == expected_platforms
    for db_val in expected_platforms:
        member = PlatformType.get_by_value(db_val)
        assert member is not None
        assert member.emoji
        assert member.display_name in member.label

    drive_folder = SourceType.get_by_value("drive_folder")
    assert drive_folder is SourceType.DRIVE_FOLDER
    assert drive_folder.display_name == "Папка облачного диска"
    assert drive_folder.emoji == "📁"


def test_platform_config_bootstrap_covers_all_eight_platforms():
    platform_types = _load_platform_types()
    platform_config = _load_platform_config()

    for member in platform_types.PlatformType:
        params = platform_config.initial_platform_params(member)
        api_base = platform_config.require_config_param(params, "api_base_url")
        assert api_base.startswith("https://")
        assert params.get("auth_type")
        assert params.get("category") in {"social", "mail", "crm", "erp", "cloud_drive", "gateway"}
        assert params.get("data_boundary") in {"cloud", "company", "local_only"}
        assert isinstance(params.get("capabilities"), list) and len(params["capabilities"]) > 0
        assert isinstance(params.get("write_capabilities"), list)

    # Read vs write separation: cloud drives and local gateway are strictly read-only
    for read_only_kind in ("vk", "yandex_disk", "mailru_cloud", "local_gateway"):
        params = platform_config.initial_platform_params(read_only_kind)
        assert params["write_capabilities"] == []


def test_drive_folder_mounting_and_path_traversal_guards():
    connections = _load_connections()

    descriptor = connections.build_drive_folder_source_params(
        platform_type="yandex_disk",
        folder_path="disk:/Договоры 2026/Поставщики/",
        credential_id=42,
        tenant_id=7,
        allowed_extensions=["pdf", ".docx"],
    )
    assert descriptor == {
        "source_type": "drive_folder",
        "platform_type": "yandex_disk",
        "external_id": "disk:/Договоры 2026/Поставщики",
        "params": {
            "credential_id": 42,
            "tenant_id": 7,
            "sync_mode": "watch_folder",
            "allowed_extensions": [".pdf", ".docx"],
        },
    }

    for unsafe_path in ("", "   ", "disk:/../etc/passwd", "/srv/contracts/../../root", "disk:/a\b"):
        with pytest.raises(ValueError):
            connections.normalize_drive_folder_path(unsafe_path)

    with pytest.raises(ValueError, match="drive_folder_platform_unsupported"):
        connections.build_drive_folder_source_params(
            platform_type="vk",
            folder_path="disk:/Docs",
            credential_id=42,
            tenant_id=7,
        )


def test_workspace_connectors_tenant_scoping_and_secret_redaction():
    connections = _load_connections()

    credentials = [
        NS(
            id=11,
            platform="yandex_disk",
            kind="oauth_token",
            is_active=True,
            meta={
                "tenant_id": 7,
                "mounted_folders": ["disk:/Договоры 2026/Спецификации"],
                "watermark": "disk_rev=2026-11-01T12:00Z",
                "oauth_token": "secret-token-must-not-leak",
                "webdav_password": SAMPLE_SECRET,
            },
        ),
        NS(
            id=12,
            platform="bitrix24",
            kind="webhook_secret",
            is_active=True,
            meta={
                "tenant_id": 999,  # Belongs to another workspace
                "watermark": "foreign-workspace-watermark",
            },
        ),
    ]

    cards = connections.build_workspace_connectors(
        credentials=credentials,
        statuses={},
        tenant_id=7,
        username="sergey",
    )
    by_platform = {c["platform_type"]: c for c in cards}
    assert set(by_platform) == {
        "vk",
        "telegram",
        "email",
        "bitrix24",
        "onec",
        "yandex_disk",
        "mailru_cloud",
        "local_gateway",
    }

    yd = by_platform["yandex_disk"]
    assert yd["health_status"] == "connected"
    assert yd["credential_owner"] == "sergey (текущее пространство)"
    assert yd["mounted_folders"] == ["disk:/Договоры 2026/Спецификации"]
    assert yd["watermark"] == "disk_rev=2026-11-01T12:00Z"
    assert "secret-token-must-not-leak" not in str(yd)
    assert SAMPLE_SECRET not in str(yd)

    # Bitrix24 credential for tenant 999 must not activate tenant 7 card
    bx = by_platform["bitrix24"]
    assert bx["health_status"] == "unconfigured"
    assert bx["watermark"] != "foreign-workspace-watermark"


def test_settings_template_renders_workspace_connectors_catalog():
    connections = _load_connections()
    fixtures = runpy.run_path(str(ROOT / "tests/shared/settings_layout_fixtures.py"))
    ctx = fixtures["context"]("connections")
    # One card per connector: the catalog facts ride along with the operator card
    # instead of rendering a second, read-only list. This is the shape
    # `settings_page` builds, so the template is exercised as it ships.
    details = {
        card["platform_type"]: card
        for card in connections.build_workspace_connectors(
            credentials=[
                NS(
                    id=21,
                    platform="yandex_disk",
                    kind="oauth_token",
                    is_active=True,
                    meta={"tenant_id": 7, "mounted_folders": ["disk:/Договоры 2026"]},
                )
            ],
            statuses={},
            tenant_id=7,
            username="sergey",
        )
    }
    ctx["connections"] = [
        dict(
            platform=spec.platform,
            title=spec.title,
            purpose=spec.purpose,
            is_oauth=spec.is_oauth,
            manual_hint=spec.manual_hint,
            status=(
                NS(
                    platform=spec.platform,
                    tone="ok",
                    state="connected",
                    label="Подключено",
                    detail="действует до 01.12.2026",
                    is_configured=True,
                )
                if spec.platform == "vk"
                else None
            ),
            manual_kinds=[("user_token", spec.title)] if spec.platform == "vk" else [],
            rows=[],
            details=details.get(spec.platform),
        )
        for spec in connections.CONNECTIONS
    ]

    html = fixtures["environment"]().get_template("web/settings.html").render(**ctx)
    assert 'data-p12-connectors="workspace-connectors"' in html
    assert "Битрикс24 CRM" in html
    assert "1С:Предприятие OData" in html
    assert "Яндекс.Диск" in html
    assert "Облако Mail.ru" in html
    assert "Локальный шлюз" in html
    assert "disk:/Договоры 2026" in html
    # The P12 catalog is the renderer, but the technical vocabulary stays inside
    # the collapsed details block rather than in the visible card.
    assert "PlatformType" not in html
    assert "UserCredential.meta.tenant_id" not in html
    assert "SourceType.DRIVE_FOLDER" not in html
    assert "Технические детали" in html
    # One grid, and a card may not repeat: each connector appears once.
    assert html.count('data-p12-connectors="workspace-connectors"') == 1
    assert html.count('id="conn-ws-vk"') == 1


@pytest.mark.asyncio
async def test_settings_page_connections_tab_populates_workspace_connectors(monkeypatch):
    import ast
    import sys

    from starlette.responses import HTMLResponse, RedirectResponse

    connections = _load_connections()
    source_path = ROOT / "app/web/settings.py"
    tree = ast.parse(source_path.read_text())

    names = {"_personal_access", "_workspace_access", "_visible_tabs", "_visible_tab", "_tab_of", "settings_page"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []

    self_service_kinds = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "SELF_SERVICE_KINDS":
            self_service_kinds = ast.literal_eval(node.value)

    # Business descriptors must not enable generic secret collection. Telegram
    # personal accounts now connect through their own page (P12-T14), so only the
    # VK user token remains a self-service kind here.
    assert set(self_service_kinds) == {"vk"}
    allowed_kinds = {kind for kinds in self_service_kinds.values() for kind, _ in kinds}
    for forbidden in ("bot_token", "app_id", "client_secret"):
        assert forbidden not in allowed_kinds

    class FakeVault:
        async def web_metadata_for_user(self, *, user_id):
            return [
                NS(
                    id=501,
                    platform="yandex_disk",
                    kind="oauth_token",
                    label="Yandex Docs",
                    is_active=True,
                    meta={"tenant_id": 7, "mounted_folders": ["disk:/Договоры 2026"]},
                )
            ]

    async def fake_statuses(uid):
        return [NS(platform="vk", state="connected", detail="VK OK", is_configured=True, tone="ok", label="Подключено")]

    vault_mod = types.ModuleType("app.models.managers.user_credential_manager")
    vault_mod.user_credentials = FakeVault()
    conn_mod = types.ModuleType("app.services.social.connections")
    conn_mod.CONNECTIONS = connections.CONNECTIONS
    conn_mod.connection_statuses = fake_statuses
    conn_mod.build_workspace_connectors = connections.build_workspace_connectors
    conn_mod.credential_matches_workspace = connections.credential_matches_workspace

    monkeypatch.setitem(sys.modules, "app.models.managers.user_credential_manager", vault_mod)
    monkeypatch.setitem(sys.modules, "app.services.social.connections", conn_mod)

    workspace_mod = types.ModuleType("app.models.managers.workspace_connection_manager")

    class Denied(PermissionError):
        pass

    class FakeConnections:
        async def list_for_actor(self, **kwargs):
            assert kwargs == {"tenant_id": 7, "actor_id": 101}
            return [{"id": 900, "state": "unverified"}], True

    workspace_mod.ConnectionDenied = Denied
    workspace_mod.workspace_connections = FakeConnections()
    monkeypatch.setitem(sys.modules, "app.models.managers.workspace_connection_manager", workspace_mod)

    ns = {
        "TABS": ("account", "connections", "models", "workspace", "team", "agent", "channels"),
        "MANAGER_TABS": frozenset({"agent", "channels"}),
        "BACK": "/app/settings",
        "HTMLResponse": HTMLResponse,
        "RedirectResponse": RedirectResponse,
        "SELF_SERVICE_KINDS": self_service_kinds,
        "render": lambda request, template, **ctx: NS(headers={}, context=ctx),
    }
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
                    type_ignores=[],
                )
            ),
            str(source_path),
            "exec",
        ),
        ns,
    )

    req = NS(
        query_params={"tab": "connections"},
        state=NS(
            tenant_id=7,
            tenant=NS(id=7, is_active=True, name="WS", timezone="UTC"),
            web_user=NS(id=101, username="sergey", is_active=True),
            memberships=[NS(tenant_id=7, user_id=101, channel="web", is_active=True)],
            web_perms=NS(can_manage_workspace=lambda tid: tid == 7),
        ),
    )
    resp = await ns["settings_page"](req)
    assert resp.context["tab"] == "connections"
    assert resp.context["workspace_connection_rows"] == [{"id": 900, "state": "unverified"}]
    email_card = next(c for c in resp.context["connections"] if c["platform"] == "email")
    assert {p.id for p in email_card["provider_forms"]} == {"yandex_mail", "mailru_mail"}
    assert email_card["manual_kinds"] == ()
    assert "workspace_connectors" not in resp.context
    assert len(resp.context["connections"]) == 8
    yd_card = next(c for c in resp.context["connections"] if c["platform"] == "yandex_disk")
    assert yd_card["details"]["health_status"] == "connected"
    assert yd_card["details"]["mounted_folders"] == ["disk:/Договоры 2026"]
    # The personal rows follow the active workspace only.
    assert [row.id for row in yd_card["rows"]] == [501]
