"""Real workspace/binding checks with HTTP mocked; never contact Telegram."""

import logging
from inspect import unwrap
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.admin.views import NotificationAdmin
from app.core.config import settings
from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models import Notification, Tenant, TenantChannel
from app.models.managers.tenant_manager import tenant_channels
from app.services.monitoring import collector
from app.services.notifications import messenger
from app.services.notifications.service import notify
from app.types import NotificationType, SourceType

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def workspaces():
    rows = [await Tenant.objects.create(name="Notification test", slug=f"notify-{uuid4().hex}") for _ in range(2)]

    async def bind(tenant, *, active=True, channel="telegram", chat_id=None):
        return await TenantChannel.objects.create(
            tenant_id=tenant.id,
            channel=channel,
            chat_id=chat_id or f"notify-{uuid4().hex}",
            is_active=active,
            is_digest_target=False,
        )

    try:
        yield *rows, bind
    finally:
        with tenant_scope(bypass=True):
            for row in rows:
                await Tenant.objects.delete_by_id(row.id)


@pytest.fixture
async def destination(workspaces):
    first, _, bind = workspaces
    return await bind(first)


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(calls=[], status=200, payload={"ok": True}, error=None, invalid_json=False)

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json, timeout):
            state.calls.append({"url": url, "payload": json, "timeout": timeout})
            if state.error:
                raise state.error

            def body():
                if state.invalid_json:
                    raise ValueError("raw-response-secret")
                return state.payload

            return SimpleNamespace(status_code=state.status, json=body)

    monkeypatch.setattr(messenger.httpx, "AsyncClient", StubClient)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "fake-notification-token")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "operator-only-chat")
    return state


async def send(recipient=None, **kwargs):
    return await messenger.messenger_service.send_notification(
        "Workspace title",
        "Customer-only content",
        NotificationType.API_ERROR,
        messenger="telegram",
        recipient_id=recipient,
        **kwargs,
    )


async def test_owned_active_non_digest_binding_receives_message(workspaces, destination, transport):
    first, _, _ = workspaces
    with tenant_scope(first.id):
        result = await send(destination.chat_id)
    assert result["telegram"]["success"] is True
    assert transport.calls[0]["payload"]["chat_id"] == destination.chat_id
    assert transport.calls[0]["payload"]["chat_id"] != settings.TELEGRAM_ADMIN_CHAT_ID
    assert destination.is_digest_target is False


@pytest.mark.parametrize("kind", ["notification", "report", "trend"])
async def test_missing_recipient_never_uses_admin_fallback(workspaces, transport, kind):
    first, _, _ = workspaces
    with tenant_scope(first.id):
        if kind == "report":
            result = await messenger.messenger_service.send_report_ready("Customer report", "https://private.invalid")
        elif kind == "trend":
            result = await messenger.messenger_service.send_trend_alert("Customer source", "Customer trend")
        else:
            result = await send()
    assert result["telegram"]["success"] is False
    assert transport.calls == []


async def test_unscoped_caller_cannot_select_workspace(workspaces, destination, transport):
    first, _, _ = workspaces
    with tenant_scope():
        result = await send(destination.chat_id, tenant_id=first.id)
    assert result["telegram"]["success"] is False
    assert transport.calls == []


async def test_foreign_override_rejected_before_binding_lookup(workspaces, destination, transport, monkeypatch):
    first, second, _ = workspaces

    async def unexpected(**kwargs):
        pytest.fail("Foreign override must not query a recipient")

    monkeypatch.setattr(tenant_channels, "get_binding", unexpected)
    with tenant_scope(first.id):
        result = await send(destination.chat_id, tenant_id=second.id)
    assert result["telegram"]["success"] is False
    assert transport.calls == []


async def test_foreign_binding_rejected(workspaces, transport):
    first, second, bind = workspaces
    foreign = await bind(second)
    with tenant_scope(first.id):
        result = await send(foreign.chat_id)
    assert result["telegram"]["success"] is False
    assert transport.calls == []


async def test_explicit_same_workspace_allowed(workspaces, destination, transport):
    first, _, _ = workspaces
    with tenant_scope(first.id):
        assert (await send(destination.chat_id, tenant_id=first.id))["telegram"]["success"] is True


async def test_unscoped_operator_requires_explicit_workspace(destination, transport):
    with tenant_scope(bypass=True):
        result = await send(destination.chat_id)
    assert result["telegram"]["success"] is False
    assert transport.calls == []


async def test_operator_explicit_workspace_checked_and_context_unchanged(workspaces, destination, transport):
    first, _, _ = workspaces
    with tenant_scope(bypass=True):
        assert (await send(destination.chat_id, tenant_id=first.id))["telegram"]["success"] is True
        assert current_tenant_id() is None and is_bypass() is True


@pytest.mark.parametrize("kind", ["missing", "inactive_workspace", "inactive_binding", "unbound"])
async def test_unavailable_workspace_or_binding_denied(workspaces, destination, transport, kind):
    first, _, _ = workspaces
    if kind == "inactive_workspace":
        await Tenant.objects.update_by_id(first.id, is_active=False)
    if kind == "inactive_binding":
        await TenantChannel.objects.update_by_id(destination.id, is_active=False)
    with tenant_scope(bypass=True):
        result = await send(
            "not-bound" if kind == "unbound" else destination.chat_id,
            tenant_id=-1 if kind == "missing" else first.id,
        )
    assert result["telegram"]["success"] is False
    assert transport.calls == []


@pytest.mark.parametrize("corruption", ["foreign", "inactive", "channel", "chat"])
async def test_defensive_binding_backstop(workspaces, destination, transport, monkeypatch, corruption):
    first, second, _ = workspaces

    async def corrupt_lookup(**kwargs):
        return SimpleNamespace(
            tenant_id=second.id if corruption == "foreign" else first.id,
            is_active=corruption != "inactive",
            channel="vk" if corruption == "channel" else "telegram",
            chat_id="wrong-chat" if corruption == "chat" else destination.chat_id,
        )

    monkeypatch.setattr(tenant_channels, "get_binding", corrupt_lookup)
    with tenant_scope(first.id):
        assert (await send(destination.chat_id))["telegram"]["success"] is False
    assert transport.calls == []


async def test_html_is_literal_customer_text(workspaces, destination, transport):
    first, _, _ = workspaces
    with tenant_scope(first.id):
        await messenger.messenger_service.send_notification(
            '<a href="https://evil.invalid">title</a>',
            "<b>text & body</b>",
            NotificationType.REPORT_READY,
            messenger="telegram",
            recipient_id=destination.chat_id,
        )
    text = transport.calls[0]["payload"]["text"]
    assert "&lt;a href=" in text and "&lt;b&gt;text &amp; body&lt;/b&gt;" in text
    assert '<a href="https://evil.invalid">' not in text


async def test_missing_token_fails_without_http(workspaces, destination, transport, monkeypatch):
    first, _, _ = workspaces
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)
    with tenant_scope(first.id):
        assert (await send(destination.chat_id))["telegram"]["success"] is False
    assert transport.calls == []


@pytest.mark.parametrize("failure", ["status", "ok_false", "exception", "json"])
async def test_transport_failures_do_not_echo_secrets(workspaces, destination, transport, caplog, failure):
    first, _, _ = workspaces
    secret = "raw-response-secret"
    if failure == "status":
        transport.status = 400
        transport.payload = {"ok": False, "description": secret}
    elif failure == "ok_false":
        transport.payload = {"ok": False, "description": secret}
    elif failure == "exception":
        transport.error = httpx.ConnectError(
            f"{secret}: https://api.telegram.org/botfake-notification-token/sendMessage"
        )
    else:
        transport.invalid_json = True
    with caplog.at_level(logging.WARNING, logger=messenger.__name__), tenant_scope(first.id):
        result = await send(destination.chat_id)
    assert result["telegram"]["success"] is False
    assert secret not in str(result) + caplog.text
    assert "fake-notification-token" not in str(result) + caplog.text


async def test_oversized_notification_fails_without_http(workspaces, destination, transport):
    first, _, _ = workspaces
    with tenant_scope(first.id):
        result = await messenger.messenger_service.send_notification(
            "Title",
            "x" * 4096,
            NotificationType.REPORT_READY,
            messenger="telegram",
            recipient_id=destination.chat_id,
        )
    assert result["telegram"]["success"] is False
    assert transport.calls == []


async def test_vk_placeholder_is_not_a_delivery(workspaces, transport):
    first, _, bind = workspaces
    target = await bind(first, channel="vk")
    with tenant_scope(first.id):
        result = await messenger.messenger_service.send_notification(
            "Title",
            "Message",
            NotificationType.REPORT_READY,
            messenger="vk",
            recipient_id=target.chat_id,
        )
    assert result["vk"]["success"] is False
    assert transport.calls == []


async def test_unsupported_transport_rejected(workspaces, destination, transport):
    first, _, _ = workspaces
    with tenant_scope(first.id), pytest.raises(ValueError, match="Unsupported"):
        await messenger.messenger_service.send_notification(
            "Title",
            "Message",
            NotificationType.REPORT_READY,
            messenger="unknown",
            recipient_id=destination.chat_id,
        )
    assert transport.calls == []


@pytest.mark.parametrize("event", list(messenger.OPERATOR_ALERTS))
async def test_operator_catalog_is_fixed_and_uses_only_operator_chat(workspaces, transport, event):
    first, _, _ = workspaces
    with tenant_scope(first.id):
        result = await messenger.messenger_service.send_operator_alert(event)
    assert result["success"] is True
    assert transport.calls[0]["payload"]["chat_id"] == "operator-only-chat"
    title, text = messenger.OPERATOR_ALERTS[event]
    assert title in transport.calls[0]["payload"]["text"] and text in transport.calls[0]["payload"]["text"]


async def test_unknown_operator_event_cannot_forward_text(transport):
    result = await messenger.messenger_service.send_operator_alert("secret-source-and-raw-error")
    assert result["success"] is False
    assert "secret-source" not in str(result)
    assert transport.calls == []


async def test_operator_alert_does_not_accept_free_form_details(transport):
    with pytest.raises(TypeError):
        await messenger.messenger_service.send_operator_alert("collection_failed", message="private")
    assert transport.calls == []


async def test_operator_missing_configuration_fails_closed(transport, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None)
    assert (await messenger.messenger_service.send_operator_alert("api_error"))["success"] is False
    assert transport.calls == []


async def test_legacy_critical_shim_discards_all_customer_content(transport, caplog):
    secret = "private-source-token-stacktrace"
    with caplog.at_level(logging.INFO, logger=messenger.__name__):
        result = await messenger.messenger_service.send_critical_alert(secret, secret, error_details=secret)
    assert result["telegram"]["success"] is True
    assert secret not in str(transport.calls) + caplog.text + str(result)


@pytest.mark.parametrize("kind", ["report", "trend"])
async def test_customer_helpers_use_explicit_owned_recipient(workspaces, destination, transport, kind):
    first, _, _ = workspaces
    with tenant_scope(first.id):
        if kind == "report":
            result = await messenger.messenger_service.send_report_ready(
                "Private report", recipient_id=destination.chat_id
            )
        else:
            result = await messenger.messenger_service.send_trend_alert(
                "Private source", "Private trend", recipient_id=destination.chat_id
            )
    assert result["telegram"]["success"] is True
    assert transport.calls[0]["payload"]["chat_id"] == destination.chat_id


@pytest.mark.parametrize("recipient", ["owned", "foreign", "absent", "db_only"])
async def test_db_service_pins_delivery_to_created_row(workspaces, destination, transport, recipient, caplog):
    first, second, bind = workspaces
    foreign = await bind(second)
    chat_id = destination.chat_id if recipient == "owned" else foreign.chat_id if recipient == "foreign" else None
    with caplog.at_level(logging.INFO, logger="app.services.notifications.service"), tenant_scope(first.id):
        row = await notify.create(
            "Customer notification",
            "Customer text",
            NotificationType.REPORT_READY,
            send_to_messenger=recipient != "db_only",
            recipient_id=chat_id,
        )
        assert row.tenant_id == first.id
        assert await Notification.objects.get(id=row.id) is not None
    assert len(transport.calls) == (1 if recipient == "owned" else 0)
    if recipient in {"absent", "foreign"}:
        assert f"Notification {row.id} delivered to messenger" not in [record.getMessage() for record in caplog.records]


@pytest.mark.parametrize("recipient", ["owned", "foreign", "absent"])
async def test_admin_action_uses_notification_row_workspace(workspaces, destination, transport, recipient):
    first, second, bind = workspaces
    foreign = await bind(second)
    with tenant_scope(first.id):
        row = await notify.create("Private row", "Private text", NotificationType.REPORT_READY)
    params = {"pks": str(row.id)}
    if recipient != "absent":
        params["recipient_id"] = destination.chat_id if recipient == "owned" else foreign.chat_id
    request = SimpleNamespace(
        query_params=params, url_for=lambda *args, **kwargs: "http://test/admin/notification/list"
    )
    with tenant_scope(bypass=True):
        # Pin the action body; ASGI permission gates are covered separately.
        response = await unwrap(NotificationAdmin.send_to_messenger_action)(NotificationAdmin(), request)
    assert response.status_code == 303
    assert len(transport.calls) == (1 if recipient == "owned" else 0)


@pytest.mark.parametrize("db_failure", [False, True])
@pytest.mark.parametrize("scope_kind", ["workspace", "operator", "foreign", "unscoped", "missing_owner"])
async def test_collection_failure_keeps_workspace_db_and_scrubs_operator_payload(
    workspaces, transport, monkeypatch, db_failure, scope_kind
):
    first, second, _ = workspaces
    secret = "collection-sensitive-exception-token"
    source = SimpleNamespace(
        id=101,
        tenant_id=first.id,
        name="private-customer-source",
        platform_id=1,
        platform=SimpleNamespace(params={}),
        source_type=SourceType.USER,
        last_checked=None,
    )

    class BrokenClient:
        async def collect_data(self, *args):
            raise RuntimeError(secret)

    monkeypatch.setattr(collector, "get_social_client", lambda platform: BrokenClient())
    if db_failure:

        async def broken_db(**kwargs):
            raise RuntimeError("private-db-error")

        monkeypatch.setattr(collector.notify, "create", broken_db)
    ambient = (
        first.id if scope_kind in {"workspace", "missing_owner"} else second.id if scope_kind == "foreign" else None
    )
    bypass = scope_kind == "operator"
    if scope_kind == "missing_owner":
        source.tenant_id = None
    with tenant_scope(ambient, bypass=bypass):
        with pytest.raises(RuntimeError, match=secret):
            await collector.ContentCollector().collect_from_source(source)
        assert current_tenant_id() == ambient and is_bypass() is bypass
    assert len(transport.calls) == 1
    outgoing = transport.calls[0]["payload"]
    assert outgoing["chat_id"] == "operator-only-chat"
    assert secret not in outgoing["text"] and source.name not in outgoing["text"]
    with tenant_scope(first.id):
        rows = await Notification.objects.filter(related_entity_type="source", related_entity_id=source.id)
    expected_row = not db_failure and scope_kind in {"workspace", "operator"}
    assert len(rows) == (1 if expected_row else 0)
    if rows:
        assert rows[0].tenant_id == first.id and secret not in rows[0].message


@pytest.mark.parametrize("caller", ["db_service", "admin_action"])
async def test_ownerless_row_cannot_borrow_ambient_workspace(workspaces, destination, transport, monkeypatch, caller):
    first, _, _ = workspaces
    row = SimpleNamespace(
        id=999,
        tenant_id=None,
        title="Private row",
        message="Private text",
        notification_type=NotificationType.REPORT_READY,
    )
    if caller == "db_service":

        async def create(**kwargs):
            return row

        monkeypatch.setattr(Notification.objects, "create_notification", create)
        with tenant_scope(first.id):
            assert (
                await notify.create(
                    "Title",
                    "Text",
                    NotificationType.REPORT_READY,
                    send_to_messenger=True,
                    recipient_id=destination.chat_id,
                )
                is row
            )
    else:

        async def get(**kwargs):
            return row

        monkeypatch.setattr(Notification.objects, "get", get)
        request = SimpleNamespace(
            query_params={"pks": "999", "recipient_id": destination.chat_id},
            url_for=lambda *args, **kwargs: "http://test/admin/notification/list",
        )
        with tenant_scope(first.id, bypass=True):
            await unwrap(NotificationAdmin.send_to_messenger_action)(NotificationAdmin(), request)
    assert transport.calls == []
