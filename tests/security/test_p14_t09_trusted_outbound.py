"""P14-T09 trusted outbound seam: DB-exempt, fake resolver + fake registry, no network."""

from __future__ import annotations

import asyncio
import re
import runpy
import socket
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
    assert acquire_outbound_target(ONPREM, 443, policy=CLOUD, registry=reader, resolver=resolver).addresses == (
        "10.0.0.5",
        "10.0.0.6",
    )
    resolver.addresses = ["10.0.0.5", "10.0.0.7"]  # DNS answer changed between attempts
    with pytest.raises(OutboundRefusal) as refused:
        acquire_outbound_target(ONPREM, 443, policy=CLOUD, registry=reader, resolver=resolver)
    assert refused.value.code == ADDRESS_NOT_AUTHORIZED
    resolver.addresses = ["8.8.8.8"]  # even a public answer is not on the private list
    with pytest.raises(OutboundRefusal):
        acquire_outbound_target(ONPREM, 443, policy=CLOUD, registry=reader, resolver=resolver)
    assert resolver.calls == 3, "DNS is resolved on every attempt, never pinned"


def test_public_entry_keeps_global_rule():
    reader = registry(TrustedHost(ONPREM, "public"))
    assert acquire_outbound_target(ONPREM, 443, policy=CLOUD, registry=reader, resolver=Resolver("8.8.4.4"))
    with pytest.raises(OutboundRefusal) as refused:
        acquire_outbound_target(ONPREM, 443, policy=CLOUD, registry=reader, resolver=Resolver("10.1.1.1"))
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
                return acquire_outbound_target(ONPREM, 443, policy=CLOUD, resolver=Resolver("8.8.8.8"))

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
        f"https://{ONPREM}/oauth/", allowed_hosts=("*.bitrix24.ru",), resolver=Resolver("10.0.0.5"), registry=reader
    ) == (ONPREM, 443)
    with pytest.raises(OAuthNetworkError) as err:
        validate_oauth_url(
            f"https://{ONPREM}/oauth/", allowed_hosts=("*.bitrix24.ru",), resolver=Resolver("10.0.0.9"), registry=reader
        )
    assert err.value.code == "address_not_authorized" and err.value.refusal.host == ONPREM


# --- Bitrix24 pilot consumer ----------------------------------------------------------


class FakeOAuth:
    """Stands in for WorkspaceOAuthService; validates the authorize URL like the real one."""

    def __init__(self, resolver):
        self.resolver = resolver
        self.started = []

    async def start_authorization(self, **kwargs):
        from app.integrations.catalog import BITRIX24_OAUTH_METHOD
        from app.integrations.oauth.network import validate_oauth_url

        validate_oauth_url(
            kwargs["authorize_url_override"], allowed_hosts=BITRIX24_OAUTH_METHOD.allowed_hosts, resolver=self.resolver
        )
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
    assert getattr(changed.value, "code", None) == "address_not_authorized"
    del table[ONPREM]
    with pytest.raises(Bitrix24Error) as removed:
        connect()
    assert removed.value.code == HOST_NOT_TRUSTED
    assert oauth.started == [ONPREM]


def test_bitrix_cloud_unchanged_with_empty_registry():
    service, oauth = bitrix({}, Resolver("8.8.8.8"))
    run(service.start_oauth_connect(tenant_id=1, actor_id=1, portal_domain="acme.bitrix24.ru"))
    assert oauth.started == ["acme.bitrix24.ru"]
    assert "acme.bitrix24.ru" not in service._allowed_hosts_for("acme.bitrix24.ru")


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


class FakeSession:
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
        for index, row in enumerate(self.added, start=41):
            row.id = getattr(row, "id", None) or index


@pytest.fixture
def audit(monkeypatch):
    from app.models.audit.action_audit_event import ActionAuditEvent

    events = []

    async def record_success(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", record_success)
    return events


def manager():
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost

    return TrustedOutboundHost.objects


@pytest.mark.parametrize("codename", ["user", "superuser", None])
def test_non_admin_and_tenant_actor_cannot_add(codename, audit):
    from app.models.managers.trusted_outbound_host_manager import TrustedHostError

    session = FakeSession(codename)
    with pytest.raises(TrustedHostError) as err:
        run(manager().add_host(actor_id=7, host=ONPREM, kind="public", session=session))
    assert err.value.code == "admin_required"
    assert session.added == [] and audit == []


@pytest.mark.parametrize("codename", ["operator", "admin"])
def test_admin_add_and_remove_are_audited_with_actor(codename, audit):
    session = FakeSession(codename, None)
    row = run(
        manager().add_host(
            actor_id=7, host=f" {ONPREM.upper()}. ", kind="private", addresses=["10.0.0.5", "10.0.0.5"], session=session
        )
    )
    assert (row.host, row.kind, row.authorized_addresses, row.created_by_actor_id) == (
        ONPREM,
        "private",
        ["10.0.0.5"],
        7,
    )
    assert audit[-1] == {
        "session": session,
        "actor_kind": "user",
        "actor_id": 7,
        "action": "admin.trusted_host_add",
        "resource_kind": "trusted_host",
        "resource_id": row.id,
    }
    removal = FakeSession(codename, row)
    assert run(manager().remove_host(actor_id=7, host_id=row.id, confirm_host=ONPREM, session=removal)) == ONPREM
    assert removal.deleted == [row]
    assert audit[-1]["action"] == "admin.trusted_host_remove" and audit[-1]["actor_id"] == 7


def test_remove_requires_matching_confirmation(audit):
    from app.models.managers.trusted_outbound_host_manager import TrustedHostError

    row = SimpleNamespace(id=3, host=ONPREM)
    session = FakeSession("admin", row)
    with pytest.raises(TrustedHostError) as err:
        run(manager().remove_host(actor_id=7, host_id=3, confirm_host="other.example", session=session))
    assert err.value.code == "confirmation_required" and session.deleted == [] and audit == []


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
        from app.core.tenant_context import current_tenant_id

        calls.append((kwargs, current_tenant_id()))
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


def test_superuser_role_add_runs_in_tenant_scope_for_audit(monkeypatch):
    client, calls = web_client(monkeypatch, "admin")
    response = client.post(
        "/app/admin/trusted-hosts/add",
        data={"_csrf": "ok", "host": ONPREM, "kind": "private", "addresses": "10.0.0.5, 10.0.0.6", "note": "pilot"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    kwargs, tenant = calls[0]
    assert tenant == 5 and kwargs["actor_id"] == 7 and kwargs["addresses"] == ["10.0.0.5", "10.0.0.6"]
    refused = client.post("/app/admin/trusted-hosts/add", data={"_csrf": "bad", "host": ONPREM}, follow_redirects=False)
    assert refused.status_code == 302 and len(calls) == 1


def test_admin_page_shows_live_resolution_through_the_seam(monkeypatch):
    from app.web import trusted_hosts

    monkeypatch.setattr(trusted_hosts, "RESOLVER", Resolver("10.0.0.9"))
    entry = TrustedHost(ONPREM, "private", frozenset({"10.0.0.5"}))
    assert run(trusted_hosts._resolve(entry)) == {"ok": False, "code": ADDRESS_NOT_AUTHORIZED, "addresses": ()}
    monkeypatch.setattr(trusted_hosts, "RESOLVER", Resolver("10.0.0.5"))
    assert run(trusted_hosts._resolve(entry))["addresses"] == ("10.0.0.5",)


# --- schema: audit codes in four places, ORM == migration ------------------------------


def test_audit_codes_in_sync_across_registry_manager_model_and_migration():
    from app.models.audit.action_audit_event import ActionAuditEvent
    from app.models.managers import action_audit_event_manager
    from app.services.audit_codes import AUDIT_CODES, audit_check_sql

    added = {"admin.trusted_host_add", "admin.trusted_host_remove"}
    assert added <= set(AUDIT_CODES) and {AUDIT_CODES[c].resource_kind for c in added} == {"trusted_host"}
    assert added <= action_audit_event_manager._SAFE_ACTIONS
    checks = {c.name: str(c.sqltext) for c in ActionAuditEvent.__table__.constraints if hasattr(c, "sqltext")}
    assert all(f"'{code}'" in checks["ck_action_audit_events_action"] for code in added)
    migration = runpy.run_path(str(ROOT / "migrations/versions/0138_trusted_outbound_hosts.py"))
    assert migration["NEW_CHECKS"] == audit_check_sql()
    assert {code for code, _, _ in migration["ADDED_CODES"]} == added
    assert migration["revision"] == "0138"


def test_migration_table_matches_orm_and_downgrade_refuses_lossy(monkeypatch):
    import sqlalchemy as sa

    from app.models.integrations.trusted_outbound_host import HOST_PATTERN, TrustedOutboundHost

    migration = runpy.run_path(str(ROOT / "migrations/versions/0138_trusted_outbound_hosts.py"))
    assert migration["HOST_PATTERN"] == HOST_PATTERN
    op = migration["op"]
    created, calls = {}, []
    monkeypatch.setattr(op, "create_table", lambda name, *items, **kw: created.update(name=name, items=items, kw=kw))
    monkeypatch.setattr(op, "drop_constraint", lambda *a, **kw: calls.append(("drop", a)))
    monkeypatch.setattr(op, "create_check_constraint", lambda *a, **kw: calls.append(("create", a)))
    monkeypatch.setattr(op, "drop_table", lambda *a, **kw: calls.append(("drop_table", a)))
    migration["upgrade"]()
    assert created["name"] == TrustedOutboundHost.__tablename__ and created["kw"]["schema"]
    columns = {c.name: c for c in created["items"] if isinstance(c, sa.Column)}
    orm = TrustedOutboundHost.__table__
    assert set(columns) == set(orm.columns.keys())
    for name, column in columns.items():
        assert column.nullable == orm.columns[name].nullable, name

    def named(items):
        return {
            c.name: str(getattr(c, "sqltext", ""))
            for c in items
            if isinstance(c, (sa.CheckConstraint, sa.UniqueConstraint))
        }

    assert named(created["items"]) == named(orm.constraints)
    assert len(calls) == 6

    monkeypatch.setattr(op, "get_bind", lambda: SimpleNamespace(execute=lambda q: SimpleNamespace(first=lambda: (1,))))
    calls.clear()
    with pytest.raises(ValueError, match="expired_events"):
        migration["downgrade"]()
    assert calls == []
    monkeypatch.setattr(op, "get_bind", lambda: SimpleNamespace(execute=lambda q: SimpleNamespace(first=lambda: None)))
    migration["downgrade"]()
    assert calls[-1][0] == "drop_table" and len(calls) == 7


def test_model_has_no_tenant_and_cites_the_exception():
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost

    assert "tenant_id" not in TrustedOutboundHost.__table__.columns
    assert "2026-11-04" in TrustedOutboundHost.__doc__
