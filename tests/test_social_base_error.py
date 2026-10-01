"""Tests for surfacing logical API errors during social collection.

The base client used to swallow every exception inside `collect_data` and
return `[]`, so a VK business error (e.g. error 15 — hidden wall) degraded
into a silent "0 items" with no reason. `_raise_api_error` already detected
the error but `collect_data` ate it; now it re-raises so callers see the real
cause.
"""

import pytest

from app.services.social.base import BaseClient
from app.types import SourceType


class _FakePlatform:
    platform_type = "vk"
    params = {}


class _FakeSource:
    source_type = SourceType.USER
    external_id = "owner"
    name = "fake"
    params = {"incremental_mode": False}


class _FakeClient(BaseClient):
    """Subclass that stubs the network boundary to return a canned response."""

    def __init__(self, response):
        super().__init__(_FakePlatform())
        self._response = response

    async def _make_request(self, method, params):
        return self._response

    def _extract_items_from_response(self, response):
        return []

    def _should_stop_pagination(self, response, items, page_size, total_collected):
        return True

    def _build_paginated_response(self, all_items):
        return {"response": {"items": all_items}}

    def _normalize_response(self, response, source_type):
        return response

    def _get_api_method(self, source_type, content_type):
        return "wall.get"

    def _build_params(self, source, method):
        return {}


def test_raise_api_error_raises_on_vk_error_body():
    client = _FakeClient({})
    with pytest.raises(RuntimeError, match="Ошибка API"):
        client._raise_api_error({"error": {"error_code": 15, "error_msg": "Access denied"}})


async def test_collect_data_propagates_api_error_instead_of_empty_list():
    """A business API error must reach the caller, not become a silent empty result."""
    client = _FakeClient({"error": {"error_code": 15, "error_msg": "Access denied"}})
    with pytest.raises(RuntimeError, match="Ошибка API"):
        await client.collect_data(_FakeSource(), "posts")
