import asyncio
import socket
from types import SimpleNamespace

import httpx
import pytest

from tests.shared.business_tenancy import assert_cross_tenant_not_found
from tests.shared.provider_http import MailDouble, deny_network, mock_transport, synthetic_provider_routes

pytest_plugins = ("tests.shared.business_tenancy",)


def test_synthetic_provider_snapshots_are_offline_and_closed_by_default():
    routes, mail = synthetic_provider_routes()
    with deny_network(), httpx.Client(transport=mock_transport(routes)) as client:
        for (method, url), spec in routes.items():
            assert client.request(method, url).status_code == spec.status
        with pytest.raises(AssertionError, match="Unexpected synthetic provider"):
            client.get("https://other.example.invalid/")
        with pytest.raises(RuntimeError, match="Network is disabled"):
            socket.getaddrinfo("example.invalid", 443)
        with socket.socket() as sock, pytest.raises(RuntimeError, match="Network is disabled"):
            sock.connect(("127.0.0.1", 80))
    for protocol in ("imap", "smtp"):
        double = MailDouble(mail[protocol])
        assert double.command("noop") == mail[protocol]["NOOP"]
        with pytest.raises(AssertionError, match="Unexpected synthetic mail command"):
            double.command("send")
        assert double.calls == [("NOOP", ())]


def test_async_http_client_uses_the_same_mock_transport():
    async def run():
        routes, _ = synthetic_provider_routes()
        method, url = next(iter(routes))
        async with httpx.AsyncClient(transport=mock_transport(routes)) as client:
            assert (await client.request(method, url)).status_code == 200

    with deny_network():
        asyncio.run(run())


def test_unit_tenancy_fixture_and_non_disclosing_assertion(business_world):
    assert business_world.first.tenant_id != business_world.second.tenant_id
    assert business_world.first.user_id != business_world.second.user_id
    assert_cross_tenant_not_found(SimpleNamespace(status_code=404))
    with pytest.raises(AssertionError):
        assert_cross_tenant_not_found(SimpleNamespace(status_code=403))
