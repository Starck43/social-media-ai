"""The VK identity resolvers behind the community/person import.

Two invariants are pinned here, because both silently break the import's
deduplication when they regress:

* `resolve_user` must ask VK for `screen_name`. Without it the answer carries
  only the numeric id, and a source row that stores the nick (a legacy row, or
  the row the operator created by hand) can no longer be recognised as the same
  person — the import would create a second lane for one wall.
* `resolve_group` must answer a community id signed negative, because a
  community wall is `owner_id=-<id>` in our model and a positive id would
  collide with a person.
"""

from __future__ import annotations

import pytest

from app.services.social.vk_client import VKClient


def _client() -> VKClient:
    """A client without a platform row: the resolvers only need the request path."""
    from types import SimpleNamespace

    client = VKClient.__new__(VKClient)
    client.platform = SimpleNamespace(params={})
    return client


async def test_resolve_user_asks_vk_for_the_screen_name(monkeypatch) -> None:
    captured: dict = {}

    async def fake_identity_response(self, method, params):
        captured["method"] = method
        captured["params"] = params
        return [{"id": 43109341, "first_name": "Sasha", "last_name": "Kabanov", "screen_name": "kaban828"}]

    monkeypatch.setattr(VKClient, "_identity_response", fake_identity_response)

    resolved = await _client().resolve_user("kaban828")

    assert captured["method"] == "users.get"
    assert captured["params"]["fields"] == "screen_name", "the nick is the dedupe key for a legacy row"
    assert resolved == {"id": 43109341, "screen_name": "kaban828", "name": "Sasha Kabanov"}


async def test_resolve_user_reads_an_empty_answer_as_not_found(monkeypatch) -> None:
    async def fake_identity_response(self, method, params):
        return []

    monkeypatch.setattr(VKClient, "_identity_response", fake_identity_response)

    assert await _client().resolve_user("ghost") is None


async def test_resolve_group_answers_a_negative_community_id(monkeypatch) -> None:
    captured: dict = {}

    async def fake_identity_response(self, method, params):
        captured["method"] = method
        captured["params"] = params
        return {"groups": [{"id": 113075242, "screen_name": "dreviz2000", "name": "Древиз", "is_closed": 0}]}

    monkeypatch.setattr(VKClient, "_identity_response", fake_identity_response)

    resolved = await _client().resolve_group("dreviz2000")

    assert captured["method"] == "groups.getById"
    assert resolved == {
        "id": -113075242,
        "screen_name": "dreviz2000",
        "name": "Древиз",
        "is_closed": False,
    }


async def test_resolve_group_reads_an_empty_answer_as_not_found(monkeypatch) -> None:
    async def fake_identity_response(self, method, params):
        return {"groups": []}

    monkeypatch.setattr(VKClient, "_identity_response", fake_identity_response)

    assert await _client().resolve_group("ghost") is None


@pytest.mark.parametrize("code", [100, 770])
async def test_a_missing_object_is_not_found_and_other_errors_propagate(monkeypatch, code) -> None:
    from app.services.social import vk_client as vk_module

    assert code in VKClient.RESOLVE_NOT_FOUND_CODES

    async def fake_resolve_token(platform, *, kinds=None, required=False, owner_user_id=None):
        return "token"

    monkeypatch.setattr(vk_module, "resolve_token", fake_resolve_token)

    async def fake_make_request(self, method, payload):
        return {"error": {"error_code": code, "error_msg": "not found"}}

    monkeypatch.setattr(VKClient, "_make_request", fake_make_request)
    assert await _client()._identity_response("groups.getById", {"group_id": "ghost"}) == {}

    async def fake_auth_error(self, method, payload):
        return {"error": {"error_code": 5, "error_msg": "auth failed"}}

    monkeypatch.setattr(VKClient, "_make_request", fake_auth_error)
    with pytest.raises(RuntimeError):
        await _client()._identity_response("groups.getById", {"group_id": "ghost"})
