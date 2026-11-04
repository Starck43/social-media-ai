"""P14-T09 trusted outbound seam: DB-exempt, fake resolver + fake registry, no network."""

from __future__ import annotations

import asyncio
import re
import runpy
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import outbound
from app.core.outbound import (
    ADDRESS_NOT_AUTHORIZED,
    HOST_NOT_TRUSTED,
    PRIVATE_ADDRESS_FORBIDDEN,
    OutboundRefusal,
    StaticPolicy,
    TrustedHost,
    acquire_outbound_target,
    registry_from_entries,
    registry_scope,
)

__source_only__ = True

ROOT = Path(__file__).resolve().parents[2]
CLOUD = StaticPolicy(("oauth.bitrix.info", "*.bitrix24.ru"))
ONPREM = "crm.client.example"


def answers(*addresses):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]


class Resolver:
    """Fake DNS: counts calls, answer can change between attempts."""

    def __init__(self, *addresses):
        self.addresses = list(addresses)
        self.calls = 0

    def __call__(self, host, port):
        self.calls += 1
        return answers(*self.addresses)


def registry(*entries):
    return registry_from_entries(entries)


def run(coro):
    return asyncio.run(coro)


# --- the seam ---------------------------------------------------------------------


def test_empty_registry_cloud_works_onprem_refused_with_typed_error():
    target = acquire_outbound_target(
        "Acme.Bitrix24.RU.", 443, policy=CLOUD, registry=registry(), resolver=Resolver("8.8.8.8")
    )
    assert (target.host, target.addresses) == ("acme.bitrix24.ru", ("8.8.8.8",))
    resolver = Resolver("8.8.8.8")
    with pytest.raises(OutboundRefusal) as refused:
        acquire_outbound_target(ONPREM, 443, policy=CLOUD, registry=registry(), resolver=resolver)
    assert refused.value.code == HOST_NOT_TRUSTED
    assert refused.value.host == ONPREM and ONPREM in str(refused.value)
    assert refused.value.owner == "installation_administrator"
    assert resolver.calls == 0, "an untrusted host is refused before any DNS lookup"


@pytest.mark.parametrize("address", ["10.0.0.1", "127.0.0.1", "169.254.169.254", "::1", "192.168.1.2"])
def test_static_host_with_private_answer_is_private_address_forbidden(address):
    with pytest.raises(OutboundRefusal) as refused:
        acquire_outbound_target("a.bitrix24.ru", 443, policy=CLOUD, registry=registry(), resolver=Resolver(address))
    assert refused.value.code == PRIVATE_ADDRESS_FORBIDDEN


def test_private_entry_authorizes_exactly_its_addresses_and_dns_change_fails_closed():
    reader = registry(TrustedHost(ONPREM, "private", frozenset({"10.0.0.5", "10.0.0.6"})))
    resolver = Resolver("10.0.0.5", "10.0.0.6")
    assert acquire_outbound_target(
        ONPREM, 443, policy=StaticPolicy((ONPREM,)), registry=reader, resolver=resolver
    ).addresses == (
        "10.0.0.5",
        "10.0.0.6",
    )
    resolver.addresses = ["10.0.0.5", "10.0.0.7"]  # DNS answer changed between attempts
    with pytest.raises(OutboundRefusal) as refused:
        acquire_outbound_target(ONPREM, 443, policy=StaticPolicy((ONPREM,)), registry=reader, resolver=resolver)
    assert refused.value.code == ADDRESS_NOT_AUTHORIZED
    resolver.addresses = ["8.8.8.8"]  # even a public answer is not on the private list
    with pytest.raises(OutboundRefusal):
        acquire_outbound_target(ONPREM, 443, policy=StaticPolicy((ONPREM,)), registry=reader, resolver=resolver)
    assert resolver.calls == 3, "DNS is resolved on every attempt, never pinned"


def test_public_entry_keeps_global_rule():
    reader = registry(TrustedHost(ONPREM, "public"))
    assert acquire_outbound_target(
        ONPREM, 443, policy=StaticPolicy((ONPREM,)), registry=reader, resolver=Resolver("8.8.4.4")
    )
    with pytest.raises(OutboundRefusal) as refused:
        acquire_outbound_target(
            ONPREM, 443, policy=StaticPolicy((ONPREM,)), registry=reader, resolver=Resolver("10.1.1.1")
        )
    assert refused.value.code == ADDRESS_NOT_AUTHORIZED


def test_unreadable_registry_is_cloud_only_without_error():
    def broken(host):
        raise RuntimeError("db down")

    assert acquire_outbound_target("a.bitrix24.ru", 443, policy=CLOUD, registry=broken, resolver=Resolver("8.8.8.8"))
    with pytest.raises(OutboundRefusal) as refused:
        acquire_outbound_target(ONPREM, 443, policy=CLOUD, registry=broken, resolver=Resolver("8.8.8.8"))
    assert refused.value.code == HOST_NOT_TRUSTED


def test_no_environment_fallback(monkeypatch):
    for name in ("TRUSTED_OUTBOUND_HOSTS", "BITRIX24_ONPREM_DOMAINS", "ALLOWED_HOSTS"):
        monkeypatch.setenv(name, ONPREM)
    with pytest.raises(OutboundRefusal):
        acquire_outbound_target(ONPREM, 443, policy=CLOUD, resolver=Resolver("8.8.8.8"))


def test_registry_scope_is_per_operation_and_loader_failure_is_empty():
    loads = []
    table = {}

    async def loader():
        loads.append(1)
        return registry(*table.values())

    async def attempt():
        async with registry_scope(loader):
            async with registry_scope(loader):  # nested calls share one snapshot
                if outbound.current_registry()(ONPREM) is None:
                    raise OutboundRefusal(HOST_NOT_TRUSTED, ONPREM)
                return acquire_outbound_target(
                    ONPREM, 443, policy=StaticPolicy((ONPREM,)), resolver=Resolver("8.8.8.8")
                )

    with pytest.raises(OutboundRefusal):
        run(attempt())
    table[ONPREM] = TrustedHost(ONPREM, "public")  # add without restart
    assert run(attempt()).host == ONPREM
    del table[ONPREM]  # remove -> next attempt fails closed
    with pytest.raises(OutboundRefusal):
        run(attempt())
    assert len(loads) == 3
    assert outbound.current_registry() is outbound.empty_registry, "nothing outlives a scope"

    async def broken_loader():
        raise RuntimeError("table missing")

    async def broken_attempt():
        async with registry_scope(broken_loader) as reader:
            return reader

    assert run(broken_attempt()) is outbound.empty_registry


# --- OAuth guard migrated onto the seam ----------------------------------------------


def test_oauth_guard_keeps_legacy_codes_with_empty_registry():
    from app.integrations.oauth.network import OAuthNetworkError, validate_oauth_url

    hosts = ("oauth.bitrix.info", "*.bitrix24.ru")
    assert validate_oauth_url(
        "https://a.bitrix24.ru/rest/x.json", allowed_hosts=hosts, resolver=Resolver("8.8.8.8")
    ) == (
        "a.bitrix24.ru",
        443,
    )
    for url, resolver, code in [
        (f"https://{ONPREM}/oauth/", Resolver("8.8.8.8"), "untrusted_oauth_host"),
        ("https://a.bitrix24.ru/x", Resolver("10.0.0.1"), "unsafe_address"),
        ("https://a.bitrix24.ru/x", Resolver(), "dns_resolution_failed"),
        ("http://a.bitrix24.ru/x", Resolver("8.8.8.8"), "unsafe_oauth_url"),
    ]:
        with pytest.raises(OAuthNetworkError) as err:
            validate_oauth_url(url, allowed_hosts=hosts, resolver=resolver)
        assert err.value.code == code


def test_oauth_guard_uses_registry_entry():
    from app.integrations.oauth.network import OAuthNetworkError, validate_oauth_url

    reader = registry(TrustedHost(ONPREM, "private", frozenset({"10.0.0.5"})))
    assert validate_oauth_url(
        f"https://{ONPREM}/oauth/", allowed_hosts=(ONPREM,), resolver=Resolver("10.0.0.5"), registry=reader
    ) == (ONPREM, 443)
    with pytest.raises(OAuthNetworkError) as err:
        validate_oauth_url(
            f"https://{ONPREM}/oauth/", allowed_hosts=(ONPREM,), resolver=Resolver("10.0.0.9"), registry=reader
        )
    assert err.value.code == "address_not_authorized" and err.value.refusal.host == ONPREM


# --- Bitrix24 pilot consumer ----------------------------------------------------------


class FakeOAuth:
    """Stands in for WorkspaceOAuthService; validates the authorize URL like the real one."""

    def __init__(self, resolver):
        self.resolver = resolver
        self.started = []

    async def start_authorization(self, **kwargs):
        from app.integrations.oauth.network import validate_oauth_url

        validate_oauth_url(kwargs["authorize_url_override"], allowed_hosts=(), resolver=self.resolver)
        self.started.append(kwargs["account_key"])
        return {"authorize_url": kwargs["authorize_url_override"]}


def bitrix(table, resolver):
    from app.integrations.bitrix24.client import Bitrix24Service

    async def loader():
        return registry(*table.values())

    oauth = FakeOAuth(resolver)
    return Bitrix24Service(oauth_service=oauth, resolver=resolver, registry_loader=loader), oauth


def test_bitrix_onprem_add_remove_without_restart():
    from app.integrations.bitrix24.client import Bitrix24Error

    table = {}
    resolver = Resolver("10.0.0.5")
    service, oauth = bitrix(table, resolver)

    def connect():
        return run(service.start_oauth_connect(tenant_id=1, actor_id=1, portal_domain=f"https://{ONPREM}/"))

    with pytest.raises(Bitrix24Error) as refused:
        connect()
    assert refused.value.code == HOST_NOT_TRUSTED and ONPREM in str(refused.value)
    table[ONPREM] = TrustedHost(ONPREM, "private", frozenset({"10.0.0.5"}))
    assert connect()["authorize_url"] == f"https://{ONPREM}/oauth/authorize/"
    resolver.addresses = ["10.0.0.6"]
    with pytest.raises(Exception) as changed:
        connect()
    assert changed.value.code == "address_not_authorized" and changed.value.status_code == 422
    assert ONPREM in str(changed.value) and "администратор" in str(changed.value).lower()
    del table[ONPREM]
    with pytest.raises(Bitrix24Error) as removed:
        connect()
    assert removed.value.code == HOST_NOT_TRUSTED
    assert oauth.started == [ONPREM]


def test_bitrix_api_returns_value_safe_422_message(monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.endpoints import bitrix as api
    from app.integrations.bitrix24.client import Bitrix24Error

    async def refused(**kwargs):
        refusal = OutboundRefusal(ADDRESS_NOT_AUTHORIZED, ONPREM)
        raise Bitrix24Error(refusal.code, str(refusal), status_code=422)

    monkeypatch.setattr(api.bitrix24_service, "start_oauth_connect", refused)
    body = api.BitrixConnectRequest(portal_domain=ONPREM)
    with pytest.raises(HTTPException) as err:
        run(api.connect_bitrix_portal(body, SimpleNamespace(state=SimpleNamespace(tenant_id=1)), SimpleNamespace(id=7)))
    assert err.value.status_code == 422 and err.value.detail["code"] == ADDRESS_NOT_AUTHORIZED
    assert ONPREM in err.value.detail["message"] and "администратор" in err.value.detail["message"].lower()
    assert api._bitrix_http_error(Bitrix24Error("other", "secret", status_code=502)).detail != "secret"


def test_bitrix_cloud_unchanged_with_empty_registry():
    service, oauth = bitrix({}, Resolver("8.8.8.8"))
    run(service.start_oauth_connect(tenant_id=1, actor_id=1, portal_domain="acme.bitrix24.ru"))
    assert oauth.started == ["acme.bitrix24.ru"]


def test_bitrix_service_has_no_constructor_allowlist():
    import inspect

    from app.integrations.bitrix24.client import Bitrix24Service

    params = inspect.signature(Bitrix24Service.__init__).parameters
    assert "allowed_onprem_domains" not in params
    assert {"resolver", "registry_loader"} <= set(params)


# --- second consumer: IMAP check --------------------------------------------------------


def test_mail_check_goes_through_the_seam(monkeypatch):
    from app.integrations import mail_check

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answers("10.0.0.1"))
    monkeypatch.setattr(mail_check, "_PinnedIMAP", lambda *_: pytest.fail("must not connect"))
    seen = []
    real = mail_check.acquire_outbound_target

    def spy(*args, **kwargs):
        seen.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(mail_check, "acquire_outbound_target", spy)
    assert mail_check.check_mail("mailru_mail", "a@example.com", "s").code == "unsafe_address"
    assert seen == [("imap.mail.ru", 993)]


# --- one resolution path ---------------------------------------------------------------

CONSUMERS = [
    "app/integrations/oauth/network.py",
    "app/integrations/oauth/service.py",
    "app/integrations/bitrix24/client.py",
    "app/integrations/bitrix24/portal.py",
    "app/integrations/mail_check.py",
]
OWN_RESOLUTION = re.compile(r"getaddrinfo|gethostbyname|\.is_global|\.is_private|dns\.resolver")


@pytest.mark.parametrize("path", CONSUMERS)
def test_no_connector_has_its_own_resolution_path(path):
    source = (ROOT / path).read_text()
    assert OWN_RESOLUTION.search(source) is None, f"{path} resolves or classifies addresses itself"


def test_both_consumers_bind_the_same_seam():
    from app.integrations import mail_check
    from app.integrations.oauth import network

    assert network.acquire_outbound_target is outbound.acquire_outbound_target
    assert mail_check.acquire_outbound_target is outbound.acquire_outbound_target
    assert network.default_dns_resolver is outbound.default_dns_resolver
    seam = (ROOT / "app/core/outbound.py").read_text()
    for name in ("oauth", "imap", "odata", "bitrix", "httpx", "imaplib"):
        assert f"import {name}" not in seam.lower()


# --- registry manager: admin only, audited -------------------------------------------


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class FakeSession:
    next_id = 40

    def __init__(self, *results):
        self.results = list(results)
        self.added, self.deleted = [], []

    async def execute(self, stmt):
        return FakeResult(self.results.pop(0))

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                FakeSession.next_id += 1
                row.id = FakeSession.next_id
            if hasattr(row, "created_at") and row.created_at is None:
                row.created_at = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def installation_context():
    from app.core.installation_context import installation_scope

    with installation_scope(7):
        yield


def manager():
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost

    return TrustedOutboundHost.objects


@pytest.mark.parametrize("codename", ["user", "superuser", None])
def test_non_admin_and_tenant_actor_cannot_add(codename):
    from app.models.managers.trusted_outbound_host_manager import TrustedHostError

    session = FakeSession(codename)
    with pytest.raises(TrustedHostError) as err:
        run(manager().add_host(actor_id=7, host=ONPREM, kind="public", session=session))
    assert err.value.code == "admin_required"
    assert session.added == []


@pytest.mark.parametrize("codename", ["operator", "admin"])
def test_admin_add_and_remove_capture_identical_snapshots(codename, monkeypatch):
    from app.models.audit.action_audit_event import ActionAuditEvent

    monkeypatch.setattr(
        ActionAuditEvent.objects, "record_success", lambda **kw: pytest.fail("workspace ledger touched")
    )
    session = FakeSession(codename, None)
    row = run(
        manager().add_host(
            actor_id=7, host=f" {ONPREM.upper()}. ", kind="private", addresses=["10.0.0.5", "10.0.0.5"], session=session
        )
    )
    assert len(session.added) == 2
    event = session.added[1]
    assert (row.host, row.kind, row.authorized_addresses, row.created_by_actor_id) == (
        ONPREM,
        "private",
        ["10.0.0.5"],
        7,
    )
    assert (event.action, event.actor_id, event.host, event.host_entry_id) == (
        "admin.trusted_host_add",
        7,
        ONPREM,
        row.id,
    )
    removal = FakeSession(codename, row)
    assert run(manager().remove_host(actor_id=7, host_id=row.id, confirm_host=ONPREM, session=removal)) == ONPREM
    assert len(removal.added) == 1
    removed_event = removal.added[0]
    assert removal.deleted == [row]
    assert (removed_event.action, removed_event.host, removed_event.snapshot) == (
        "admin.trusted_host_remove",
        ONPREM,
        event.snapshot,
    )
    assert set(event.snapshot) == {
        "id",
        "host",
        "kind",
        "authorized_addresses",
        "note",
        "created_by_actor_id",
        "created_at",
    }


def test_remove_requires_matching_confirmation():
    from app.models.managers.trusted_outbound_host_manager import TrustedHostError

    row = SimpleNamespace(id=3, host=ONPREM)
    session = FakeSession("admin", row)
    with pytest.raises(TrustedHostError) as err:
        run(manager().remove_host(actor_id=7, host_id=3, confirm_host="other.example", session=session))
    assert err.value.code == "confirmation_required" and session.deleted == [] and session.added == []


@pytest.mark.parametrize(
    "host,kind,addresses,code",
    [
        ("10.0.0.5", "private", ["10.0.0.5"], "invalid_host"),
        ("https://crm.example/", "public", [], "invalid_host"),
        ("intranet", "public", [], "invalid_host"),
        (ONPREM, "public", ["10.0.0.5"], "public_host_takes_no_addresses"),
        (ONPREM, "private", [], "private_host_requires_addresses"),
        (ONPREM, "private", ["127.0.0.1"], "address_forbidden"),
        (ONPREM, "private", ["169.254.169.254"], "address_forbidden"),
        (ONPREM, "private", ["not-an-ip"], "invalid_address"),
        (ONPREM, "other", [], "invalid_kind"),
    ],
)
def test_invalid_entries_are_refused_before_db(host, kind, addresses, code):
    from app.models.managers.trusted_outbound_host_manager import TrustedHostError

    with pytest.raises(TrustedHostError) as err:
        run(manager().add_host(actor_id=7, host=host, kind=kind, addresses=addresses, session=FakeSession()))
    assert err.value.code == code


def test_registry_manager_exposes_no_generic_unaudited_mutation_or_queries():
    for name in ("create", "update", "update_by_id", "delete", "delete_by_id", "filter", "all", "get_queryset"):
        assert not hasattr(manager(), name)


def test_installation_admin_roles_match_web_gate():
    from app.models.managers.trusted_outbound_host_manager import INSTALLATION_ADMIN_ROLES
    from app.web.perms import WebPerms

    for codename in ("user", "operator", "admin", "superuser"):
        user = SimpleNamespace(is_active=True, role=SimpleNamespace(codename=codename))
        assert WebPerms(user=user).is_superuser_role == (codename in INSTALLATION_ADMIN_ROLES)


def test_load_trusted_registry_fails_closed(monkeypatch):
    from app.models.managers import trusted_outbound_host_manager as module

    async def broken(*a, **k):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(manager(), "snapshot", broken)
    assert run(module.load_trusted_registry()) is outbound.empty_registry


# --- admin route gate -----------------------------------------------------------------


def web_client(monkeypatch, codename, tenant_id=5):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    from app.web import trusted_hosts
    from app.web.perms import WebPerms

    user = SimpleNamespace(id=7, is_active=True, role=SimpleNamespace(codename=codename))
    app = FastAPI()
    app.state.csrf_manager = SimpleNamespace(verify_token=lambda token: token == "ok", generate_token=lambda: "ok")

    @app.middleware("http")
    async def state(request: Request, call_next):
        request.state.web_user = user
        request.state.web_perms = WebPerms(user=user)
        request.state.tenant_id = tenant_id
        return await call_next(request)

    app.add_middleware(SessionMiddleware, secret_key="test")
    app.include_router(trusted_hosts.router, prefix="/app")
    calls = []

    async def add_host(**kwargs):
        from app.core.installation_context import installation_actor

        calls.append((kwargs, installation_actor()))
        return SimpleNamespace(host=kwargs["host"])

    monkeypatch.setattr(manager(), "add_host", add_host)
    monkeypatch.setattr(manager(), "remove_host", lambda **kw: pytest.fail("not reached"))
    return TestClient(app), calls


@pytest.mark.parametrize("codename", ["user", "superuser"])
def test_non_superuser_role_refused_on_admin_routes(monkeypatch, codename):
    client, calls = web_client(monkeypatch, codename)
    responses = [
        client.get("/app/admin/trusted-hosts", follow_redirects=False),
        client.post("/app/admin/trusted-hosts/add", data={"_csrf": "ok", "host": ONPREM}, follow_redirects=False),
        client.post("/app/admin/trusted-hosts/3/remove", data={"_csrf": "ok"}, follow_redirects=False),
    ]
    for response in responses:
        assert response.status_code == 302 and response.headers["location"] == "/app"
    assert calls == []


def test_superuser_role_add_runs_in_installation_scope_without_tenant(monkeypatch):
    client, calls = web_client(monkeypatch, "admin", tenant_id=None)
    response = client.post(
        "/app/admin/trusted-hosts/add",
        data={"_csrf": "ok", "host": ONPREM, "kind": "private", "addresses": "10.0.0.5, 10.0.0.6", "note": "pilot"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    kwargs, actor = calls[0]
    assert actor == 7 and kwargs["actor_id"] == 7 and kwargs["addresses"] == ["10.0.0.5", "10.0.0.6"]
    refused = client.post("/app/admin/trusted-hosts/add", data={"_csrf": "bad", "host": ONPREM}, follow_redirects=False)
    assert refused.status_code == 302 and len(calls) == 1


def test_admin_page_shows_live_resolution_through_the_seam(monkeypatch):
    from app.web import trusted_hosts

    monkeypatch.setattr(trusted_hosts, "RESOLVER", Resolver("10.0.0.9"))
    entry = TrustedHost(ONPREM, "private", frozenset({"10.0.0.5"}))
    assert run(trusted_hosts._resolve(entry)) == {"ok": False, "code": ADDRESS_NOT_AUTHORIZED, "addresses": ()}
    monkeypatch.setattr(trusted_hosts, "RESOLVER", Resolver("10.0.0.5"))
    assert run(trusted_hosts._resolve(entry))["addresses"] == ("10.0.0.5",)


# --- installation ledger, isolation and migration -----------------------------------


def test_provider_allowlist_cannot_be_widened_by_registry():
    from app.integrations.oauth.network import OAuthNetworkError, validate_oauth_url

    reader = registry(TrustedHost(ONPREM, "public"))
    resolver = Resolver("8.8.8.8")
    with pytest.raises(OAuthNetworkError) as err:
        validate_oauth_url(
            f"https://{ONPREM}/oauth/", allowed_hosts=("*.yandex.ru",), resolver=resolver, registry=reader
        )
    assert err.value.code == "untrusted_oauth_host" and resolver.calls == 0


def test_real_bitrix_authorize_validates_only_the_selected_portal(monkeypatch):
    from app.integrations.oauth import service as oauth_service

    monkeypatch.setattr(oauth_service, "resolve_client_credentials", lambda *args: ("client", "secret"))

    async def stop_after_validation(*args, **kwargs):
        raise RuntimeError("reached authorization boundary")

    monkeypatch.setattr(oauth_service, "_authorize_member", stop_after_validation)
    service = oauth_service.WorkspaceOAuthService(resolver=Resolver("10.0.0.5"))

    async def invoke(provider, account, url):
        return await service.start_authorization(
            tenant_id=1,
            actor_id=1,
            provider_id=provider,
            account_key=account,
            authorize_url_override=url,
            session=object(),
        )

    async def loader():
        return registry(TrustedHost(ONPREM, "private", frozenset({"10.0.0.5"})))

    async def exercise():
        async with registry_scope(loader):
            with pytest.raises(RuntimeError, match="reached authorization boundary"):
                await invoke("bitrix24", ONPREM, f"https://{ONPREM}/oauth/authorize/")
            with pytest.raises(oauth_service.OAuthFlowError) as mismatch:
                await invoke("bitrix24", ONPREM, "https://elsewhere.example/oauth/authorize/")
            assert mismatch.value.code == "untrusted_oauth_host"

    run(exercise())


def test_workspace_member_cannot_read_installation_ledger():
    from app.admin.table_registry import build_table_registry
    from app.models import Base
    from app.models.managers.trusted_outbound_host_event_manager import writer
    from app.models.managers.trusted_outbound_host_manager import TrustedHostError
    from app.services.audit_codes import AUDIT_CODES, audit_check_sql

    session = FakeSession("user")
    with pytest.raises(TrustedHostError, match="admin_required"):
        run(writer.list_events(actor_id=7, session=session))
    assert session.results == [] and session.added == []
    assert not {"admin.trusted_host_add", "admin.trusted_host_remove"} & set(AUDIT_CODES)
    assert "admin.trusted_host_" not in audit_check_sql()["action"]
    assert "trusted_outbound_host_events" not in {item["table"] for item in build_table_registry(Base.metadata)}


def test_installation_administrator_can_read_ledger_after_role_recheck():
    from app.models.managers.trusted_outbound_host_event_manager import writer

    event = SimpleNamespace(host=ONPREM)
    session = FakeSession("admin", [event])
    assert run(writer.list_events(actor_id=7, session=session)) == [event]
    assert not session.results


def test_installation_event_writer_is_append_only():
    from app.models.managers.trusted_outbound_host_event_manager import writer

    for operation in (writer.update, writer.update_by_id, writer.delete, writer.delete_by_id):
        with pytest.raises(PermissionError, match="append-only"):
            run(operation())


def test_dormant_yandex_resolver_is_a_named_exception():
    source = (ROOT / "app/integrations/file_workspace/yandex_network.py").read_text()
    assert "future Yandex metadata transport" in source and OWN_RESOLUTION.search(source)
    # This isolated primitive has no production caller; migration to the seam is mandatory before activation.
    import ast

    importers = set()
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("yandex_network"):
                importers.add(str(path.relative_to(ROOT)))
            if isinstance(node, ast.Import) and any(alias.name.endswith("yandex_network") for alias in node.names):
                importers.add(str(path.relative_to(ROOT)))
    assert importers == {
        "app/integrations/file_workspace/yandex_tls.py",
        "app/integrations/file_workspace/yandex_http.py",
        "app/integrations/file_workspace/yandex_content.py",
    }


def test_readd_is_a_new_row_with_new_creator_and_date():
    from app.core.installation_context import installation_scope

    first_session = FakeSession("admin", None)
    first = run(manager().add_host(actor_id=7, host=ONPREM, kind="public", session=first_session))
    removed = FakeSession("admin", first)
    run(manager().remove_host(actor_id=7, host_id=first.id, confirm_host=ONPREM, session=removed))
    with installation_scope(8):
        later = FakeSession("admin", None)
        second = run(manager().add_host(actor_id=8, host=ONPREM, kind="public", session=later))
    assert first is not second and second.id != first.id
    assert first.created_by_actor_id == 7 and second.created_by_actor_id == 8
    assert first.created_at is not None and second.created_at is not None
    assert removed.deleted == [first]


def test_missing_or_mismatched_installation_scope_refuses_before_db():
    from app.core.installation_context import installation_scope
    from app.models.managers.trusted_outbound_host_manager import TrustedHostError

    for context in (None, 8):
        session = FakeSession()
        with installation_scope(context), pytest.raises(TrustedHostError, match="installation_context_required"):
            run(manager().add_host(actor_id=7, host=ONPREM, kind="public", session=session))
        assert session.added == [] and session.results == []
    for operation in (
        manager().list_hosts(actor_id=7, session=FakeSession()),
        manager().remove_host(actor_id=7, host_id=1, confirm_host=ONPREM, session=FakeSession()),
    ):
        with installation_scope(None), pytest.raises(TrustedHostError, match="installation_context_required"):
            run(operation)


def test_installation_tables_have_no_tenant_fk_or_workspace_audit_dependency():
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost
    from app.models.integrations.trusted_outbound_host_event import TrustedOutboundHostEvent

    for model in (TrustedOutboundHost, TrustedOutboundHostEvent):
        assert "tenant_id" not in model.__table__.columns
        assert not model.__table__.foreign_keys
        assert "2026-11-04" in model.__doc__
    manager_source = (ROOT / "app/models/managers/trusted_outbound_host_manager.py").read_text()
    assert "ActionAuditEvent" not in manager_source
    offboarding = (ROOT / "app/models/managers/workspace_offboarding_manager.py").read_text()
    assert "trusted_outbound_host" not in offboarding


def test_migration_tables_match_orm_and_downgrade_uses_current_registry(monkeypatch):
    import sqlalchemy as sa

    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost
    from app.models.integrations.trusted_outbound_host_event import TrustedOutboundHostEvent

    migration = runpy.run_path(str(ROOT / "migrations/versions/0138_trusted_outbound_hosts.py"))
    op = migration["op"]
    created, calls, statements = {}, [], []
    monkeypatch.setattr(op, "create_table", lambda name, *items, **kw: created.update({name: (items, kw)}))
    monkeypatch.setattr(op, "drop_table", lambda name, **kw: calls.append(name))
    migration["upgrade"]()
    for model in (TrustedOutboundHost, TrustedOutboundHostEvent):
        items, kw = created[model.__tablename__]
        columns = {c.name: c for c in items if isinstance(c, sa.Column)}
        assert kw["schema"] and set(columns) == set(model.__table__.columns.keys())
        assert all(column.nullable == model.__table__.columns[name].nullable for name, column in columns.items())

        def named(constraints):
            return {
                c.name: str(getattr(c, "sqltext", ""))
                for c in constraints
                if isinstance(c, (sa.CheckConstraint, sa.UniqueConstraint))
            }

        assert named(items) == named(model.__table__.constraints)

    def bind_with(result):
        def execute(q):
            statements.append(str(q))
            return SimpleNamespace(first=lambda: result)

        return SimpleNamespace(execute=execute)

    monkeypatch.setattr(op, "get_bind", lambda: bind_with((1,)))
    with pytest.raises(ValueError, match="empty_registry"):
        migration["downgrade"]()
    assert not calls and "trusted_outbound_hosts" in statements[-1] and "action_audit_events" not in statements[-1]
    monkeypatch.setattr(op, "get_bind", lambda: bind_with(None))
    migration["downgrade"]()
    assert calls == ["trusted_outbound_host_events", "trusted_outbound_hosts"]
