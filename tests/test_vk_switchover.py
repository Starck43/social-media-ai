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


async def test_user_mode_without_user_token_returns_empty(creds, stub_collector):
    creds.service, creds.user = "SVC", None
    seen = stub_collector()

    client = VKClient(_Platform())
    result = await client.collect_data(_source(params={"mode": "user"}))

    assert result == []
    assert seen == []


# --------------------------------------------------------------------------- #
# error classification
# --------------------------------------------------------------------------- #


def test_layer1_unavailable_matches_codes_15_and_5():
    assert VKClient._is_layer1_unavailable(RuntimeError("Ошибка API (15): access denied"))
    assert VKClient._is_layer1_unavailable(RuntimeError("Ошибка API (5): auth failed"))
    assert not VKClient._is_layer1_unavailable(RuntimeError("Ошибка API (6): too many requests"))
    assert not VKClient._is_layer1_unavailable(RuntimeError("network timeout"))
