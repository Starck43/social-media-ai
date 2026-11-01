"""Shared catalog access for tests.

`Platform` is a global catalog with one row per `platform_type` (Q-16): a
per-test platform row is no longer possible, and creating one only worked while
the catalog allowed duplicate types. Tests that need a platform now reuse the
seeded shared row, which is what production does too.

Seeded rows come from `scripts/setup_test_db.py`; a missing row is a broken test
schema, not something a test should paper over by inserting a duplicate.
"""

from __future__ import annotations

from typing import Any

VK_BASE_URL = "https://vk.com"
TELEGRAM_BASE_URL = "https://t.me"


async def shared_platform(platform_type: Any = "vk"):
    """Return the single shared catalog row for `platform_type`.

    No creation, no deletion: the row is reference data owned by the test schema
    bootstrap, so a test cannot leave a stray duplicate behind.
    """
    from app.models import Platform

    kind = getattr(platform_type, "db_value", platform_type)
    rows = await Platform.objects.filter(platform_type=kind).all()
    if len(rows) != 1:
        raise AssertionError(
            f"test schema must hold exactly one {kind!r} platform row, found {len(rows)}; "
            "run `python -m scripts.setup_test_db`"
        )
    return rows[0]
