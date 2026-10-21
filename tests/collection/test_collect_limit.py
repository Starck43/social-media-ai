"""No-DB tests for the collection ceiling in `BaseClient._collect_full`.

Owner command: `.venv/bin/python -m pytest tests/test_collect_limit.py -q --noconftest -o addopts=`

The run is capped at `settings.SOCIAL_COLLECT_LIMIT` per source, a lower
`params.collection.limit` wins, and nothing may raise it above
`SOCIAL_COLLECT_HARD_LIMIT` — a date typo or an unbounded first run must not
park an archive in `collected_items`.
"""

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.social.vk_client import VKClient
from app.types import SourceType


class _Platform:
    params = {}
    platform_type = "vk"


def _source(**overrides):
    defaults = dict(id=1, name="wall", external_id="-123", params={}, last_checked=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _install_fetcher(monkeypatch, *, total=None):
    """A VK-style fetcher: `count` items per page, `count`/`total_count` in the answer."""

    async def fake_fetch(self, method, params):
        count = params.get("count", 50)
        offset = params.get("offset", 0)
        items = [
            {"id": offset + i, "date": 1700000000 + i, "text": f"post-{offset + i}"}
            for i in range(count)
        ]
        data = {"response": {"items": items, "count": total or count}}
        if total is not None:
            data["response"]["total_count"] = total
        return data

    monkeypatch.setattr("app.services.social.vk_client.VKClient._fetch_page", fake_fetch)


@pytest.mark.parametrize(
    ("collection_params", "expected"),
    [
        # Default: the deployment ceiling, not "everything the platform has".
        ({}, settings.SOCIAL_COLLECT_LIMIT),
        # A per-source limit below the ceiling is honoured.
        ({"collection": {"limit": 30}}, 30),
        # Raising above the hard ceiling is clamped, never honoured.
        ({"collection": {"limit": settings.SOCIAL_COLLECT_HARD_LIMIT + 500}}, settings.SOCIAL_COLLECT_HARD_LIMIT),
        # A malformed limit degrades to the default, still bounded.
        ({"collection": {"limit": "abc"}}, settings.SOCIAL_COLLECT_LIMIT),
        # Legacy flat params.limit is read too.
        ({"limit": 7}, 7),
    ],
    ids=["default-ceiling", "per-source-lower", "above-hard-clamped", "malformed-default", "flat-limit"],
)
@pytest.mark.asyncio
async def test_collect_full_never_exceeds_the_ceiling(monkeypatch, collection_params, expected):
    params = dict(collection_params)
    params["mode"] = "api"  # keep the params builder on the wall branch path
    source = _source(params=params, source_type=SourceType.GROUP)
    _install_fetcher(monkeypatch, total=9000)

    client = VKClient(_Platform())
    client._access_token = "TEST"
    items = await client._collect_full(source, "wall.get", "posts")

    assert len(items) == expected
    # Newest-first prefix: only the first `expected` posts survive.
    assert items[0]["id"] == "0"
    assert items[-1]["id"] == str(expected - 1)
