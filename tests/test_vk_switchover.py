"""VK L1/L2 switchover tests: service->user token fallback on layer-1 errors.

`VKClient.collect_data` picks a layer per `Source.params["mode"]`:
- `api` (L1): service token only; a layer-1 error (15/5) is re-raised.
- `user` (L2): user token only.
- `auto` (default): try L1 (service token); on a layer-1 error retry with the
  L2 (user token) when one is configured. If neither layer succeeds, the L1
  error is re-raised rather than silently returning an empty result.

The token resolution and the shared collector are stubbed, so these tests never
touch the VK API or the real credentials vault.
"""

from types import SimpleNamespace

import pytest

import app.services.social.base as base_module
from app.services.social.credentials import AuthorizationRequired
from app.services.social.vk_client import VKClient


class _Platform:
    params = {}
    platform_type = None


def _source(**overrides):
    defaults = dict(id=1, name="Test", params={}, external_id="russkikh_natalia")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _Creds:
    """Scripted resolve_token: fixed service/user values, records call kinds."""

    def __init__(self, service=None, user=None):
        self.service = service
        self.user = user
        self.calls = []

    async def resolve(self, platform, *, kinds=(), tenant_id=None, required=False, **kw):
        self.calls.append(tuple(kinds))
        if "user_token" in kinds:
            return self.user
        if "service_token" in kinds:
            return self.service
        return self.service or self.user


@pytest.fixture
def creds(monkeypatch):
    c = _Creds()
    # vk_client imports `resolve_token` into its own namespace, so patch there.
    import app.services.social.vk_client as vk_client_module

    monkeypatch.setattr(vk_client_module, "resolve_token", c.resolve)
    return c


@pytest.fixture
def stub_collector(monkeypatch):
    """Replace the shared BaseClient.collect_data with a scripted stub.

    The stub behaves by the token currently in `client._token()`: a token in
    `fail_tokens` raises a RuntimeError, otherwise it returns `return_items`.
    `seen` records the order of attempted tokens.
    """

    def install(*, fail_tokens=(), return_items=None, error_msg="Ошибка API (15): Access denied"):
        seen = []

        async def stub(self, source, content_type="posts"):
            tok = self._token()
            seen.append(tok)
            if tok in fail_tokens:
                raise RuntimeError(error_msg)
            return return_items if return_items is not None else []

        monkeypatch.setattr(base_module.BaseClient, "collect_data", stub)
        return seen

    return install


@pytest.fixture(autouse=True)
def _stub_resolve_external_id(monkeypatch):
    """Screen-name resolution performs a real HTTP call; stub it for these tests."""

    async def fake_resolve(self, external_id):
        return external_id

    monkeypatch.setattr(VKClient, "_resolve_external_id", fake_resolve)


# --------------------------------------------------------------------------- #
# mode=auto: fallback on layer-1 error
# --------------------------------------------------------------------------- #


async def test_auto_falls_back_to_user_token_on_error_15(creds, stub_collector):
    creds.service, creds.user = "SVC", "USR"
    seen = stub_collector(fail_tokens=("SVC",), return_items=[{"id": "1"}])

    client = VKClient(_Platform())
    items = await client.collect_data(_source())

    assert [i["id"] for i in items] == ["1"]
    assert seen == ["SVC", "USR"]  # L1 failed -> retried with L2


async def test_auto_re_raises_when_both_layers_fail(creds, stub_collector):
    creds.service, creds.user = "SVC", "USR"
    seen = stub_collector(fail_tokens=("SVC", "USR"))

    client = VKClient(_Platform())
    with pytest.raises(RuntimeError) as ei:
        await client.collect_data(_source())

    assert "15" in str(ei.value)
    assert seen == ["SVC", "USR"]


async def test_auto_re_raises_layer1_error_when_no_user_token(creds, stub_collector):
    creds.service, creds.user = "SVC", None
    seen = stub_collector(fail_tokens=("SVC",))

    client = VKClient(_Platform())
    with pytest.raises(RuntimeError) as ei:
        await client.collect_data(_source())

    assert "15" in str(ei.value)
    assert seen == ["SVC"]  # no L2 to fall back to -> L1 error surfaces


async def test_auto_uses_l1_without_error(creds, stub_collector):
    creds.service, creds.user = "SVC", "USR"
    seen = stub_collector(return_items=[{"id": "x"}])

    client = VKClient(_Platform())
    items = await client.collect_data(_source())

    assert items == [{"id": "x"}]
    assert seen == ["SVC"]  # L1 succeeded, never touched L2


# --------------------------------------------------------------------------- #
# mode=api: L1 only, no fallback
# --------------------------------------------------------------------------- #


async def test_api_mode_does_not_fallback_on_error(creds, stub_collector):
    creds.service, creds.user = "SVC", "USR"
    seen = stub_collector(fail_tokens=("SVC",))

    client = VKClient(_Platform())
    with pytest.raises(RuntimeError):
        await client.collect_data(_source(params={"mode": "api"}))

    assert seen == ["SVC"]  # single attempt, no L2


async def test_api_mode_without_service_token_returns_empty(creds, stub_collector):
    creds.service, creds.user = None, "USR"
    seen = stub_collector()

    client = VKClient(_Platform())
    result = await client.collect_data(_source(params={"mode": "api"}))

    assert result == []
    assert seen == []  # no token -> never reached the collector


# --------------------------------------------------------------------------- #
# mode=user: L2 only
# --------------------------------------------------------------------------- #


async def test_user_mode_uses_only_user_token(creds, stub_collector):
    creds.service, creds.user = "SVC", "USR"
    seen = stub_collector(return_items=[{"id": "u"}])

    client = VKClient(_Platform())
    items = await client.collect_data(_source(params={"mode": "user"}))

    assert items == [{"id": "u"}]
    assert seen == ["USR"]


async def test_user_mode_without_user_token_raises_authorization_required(creds, stub_collector):
    """L2 requested, nobody authorized → say so instead of faking an empty run.

    Returning `[]` here made a misconfigured owner indistinguishable from a
    quiet wall: the job reported "collected, 0 items" and the operator only
    noticed days later, in a digest. The typed error is what the sources page
    and the job result turn into a one-click fix.
    """
    creds.service, creds.user = "SVC", None
    seen = stub_collector()

    client = VKClient(_Platform())
    with pytest.raises(AuthorizationRequired) as ei:
        await client.collect_data(_source(params={"mode": "user"}))

    assert "личный токен" in str(ei.value)
    assert ei.value.hint, "the error must tell the user what to do next"
    assert seen == []


# --------------------------------------------------------------------------- #
# L2 IP-binding refresh-once
# --------------------------------------------------------------------------- #

_IP_ERROR = "Ошибка API (5): User authorization failed: access_token was given to another ip address"


def _stub_refresh(monkeypatch, *, fresh_token="USR_FRESH", fail=False):
    """Stub `refresh_user_token` (lives in vk_oauth, imported lazily in the client).

    Records calls and returns `fresh_token`, or None when `fail` is set (refresh
    itself could not renew the token).
    """
    calls = []

    async def fake_refresh(user_id):
        calls.append(user_id)
        return None if fail else fresh_token

    import app.services.social.vk_oauth as vk_oauth_module

    monkeypatch.setattr(vk_oauth_module, "refresh_user_token", fake_refresh)
    return calls


def _stub_owner(monkeypatch, owner=1):
    """`resolve_source_owner` resolves a real `users.id`; stub it so L2 refresh
    has a concrete owner to renew (the client re-resolves it on every run, so
    setting `client._token_owner` by hand is not enough)."""
    import app.services.social.vk_client as vk_client_module

    async def fake(source):
        return owner

    monkeypatch.setattr(vk_client_module, "resolve_source_owner", fake)


async def test_auto_refreshes_stale_l2_token_and_retries(creds, stub_collector, monkeypatch):
    """L1 fails, L2 token is IP-bound to a foreign address → refresh and retry once."""
    creds.service, creds.user = "SVC", "USR"
    _stub_owner(monkeypatch, owner=1)
    refresh_calls = _stub_refresh(monkeypatch, fresh_token="USR_FRESH")
    # L1 fails, the stale L2 token fails with the IP error, the refreshed one succeeds.
    seen = stub_collector(fail_tokens=("SVC", "USR"), return_items=[{"id": "1"}], error_msg=_IP_ERROR)

    client = VKClient(_Platform())
    items = await client.collect_data(_source())

    assert [i["id"] for i in items] == ["1"]
    assert seen == ["SVC", "USR", "USR_FRESH"]
    assert refresh_calls == [1]  # forced once


async def test_auto_surfaces_error_when_refresh_fails(creds, stub_collector, monkeypatch):
    """Refresh-once must not loop forever: if renewal fails, the IP error surfaces."""
    creds.service, creds.user = "SVC", "USR"
    _stub_owner(monkeypatch, owner=1)
    refresh_calls = _stub_refresh(monkeypatch, fail=True)
    seen = stub_collector(fail_tokens=("SVC", "USR"), error_msg=_IP_ERROR)

    client = VKClient(_Platform())
    with pytest.raises(RuntimeError) as ei:
        await client.collect_data(_source())

    assert "5" in str(ei.value)
    assert seen == ["SVC", "USR"]  # no retry without a fresh token
    assert refresh_calls == [1]


async def test_auto_no_refresh_on_non_ip_error(creds, stub_collector, monkeypatch):
    """Only the IP-binding wording triggers a refresh; other L2 errors don't."""
    creds.service, creds.user = "SVC", "USR"
    _stub_owner(monkeypatch, owner=1)
    refresh_calls = _stub_refresh(monkeypatch)
    seen = stub_collector(fail_tokens=("SVC", "USR"), error_msg="Ошибка API (5): auth failed")

    client = VKClient(_Platform())
    with pytest.raises(RuntimeError):
        await client.collect_data(_source())

    assert seen == ["SVC", "USR"]
    assert refresh_calls == []  # code 5, but not the IP-binding wording


# --------------------------------------------------------------------------- #
# error classification
# --------------------------------------------------------------------------- #


def test_layer1_unavailable_matches_codes_15_and_5():
    assert VKClient._is_layer1_unavailable(RuntimeError("Ошибка API (15): access denied"))
    assert VKClient._is_layer1_unavailable(RuntimeError("Ошибка API (5): auth failed"))
    assert not VKClient._is_layer1_unavailable(RuntimeError("Ошибка API (6): too many requests"))
    assert not VKClient._is_layer1_unavailable(RuntimeError("network timeout"))


def test_is_ip_binding_error_matches_foreign_ip_wording():
    assert VKClient._is_ip_binding_error(RuntimeError(_IP_ERROR))
    assert not VKClient._is_ip_binding_error(RuntimeError("Ошибка API (5): auth failed"))
    assert not VKClient._is_ip_binding_error(RuntimeError("Ошибка API (15): access denied"))
